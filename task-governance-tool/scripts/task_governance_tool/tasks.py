"""Task-domain validation helpers."""

from __future__ import annotations

import re
import base64
import binascii
import hashlib
import json
import secrets
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from task_governance_tool.completion import (
    CompletionEvidenceError,
    CompletionRequest,
    CompletionResolution,
    FULL_GIT_OBJECT_ID,
    WRITABLE_EVIDENCE_KINDS,
    completion_evidence_values,
    resolve_completion_request,
    resolve_git_commit,
    validate_evidence_matrix,
)
from task_governance_tool.completion_history_projection import (
    format_completion_history,
)
from task_governance_tool.git_snapshot import (
    GitSnapshotError,
    verify_git_snapshot_commit,
)
from task_governance_tool.evidence_ledger import (
    EvidenceSource,
    TargetCaptureBinding,
    build_evidence_reference,
)
from task_governance_tool.evidence_projection import (
    EvidenceProjectionError,
    build_native_bundle_plan,
    required_native_bundle_link_count,
)
from task_governance_tool.ordering import (
    ADVANCED_STATUSES,
    canonical_lane,
    canonical_lane_sql,
    duplicate_lane_order_sql,
    first_out_of_order_advanced_task,
    incomplete_predecessor_sql,
)
from task_governance_tool.storage import (
    PRIVATE_SCHEMA22_VERSION,
    SCHEMA_VERSION,
    CompletionHistory,
    DatabaseTarget,
    PreparedCompletionBundleMember,
    PreparedCompletionEvidenceBundle,
    PreparedCompletionFindingSnapshot,
    PreparedCriterionEvidenceLink,
    ProjectIdentity,
    StorageError,
    _ValidatedViewerTaskBatch,
    _consume_validated_viewer_task_batch,
    allocate_native_completion_identity_locked,
    begin_initialized_write,
    capture_or_reuse_current_authority_snapshot_locked,
    completion_history_inconsistent,
    current_schema_version,
    insert_completion_cycle_locked,
    insert_native_completion_cycle_locked,
    match_current_done_completion_cycle_locked,
    persist_evidence_reference_locked,
    prepare_native_completion_cycle_locked,
    read_native_completion_bundle_basis_locked,
    read_completion_history,
    stored_task_sqlite_error,
    stored_task_verification_limit,
    utc_now,
    validate_utc_timestamp,
    validate_selected_task_authority_storage,
)
from task_governance_tool.verification_runner import (
    VerificationRunnerGateSelection,
)


KINDS = ("sequential", "optional")
PRIORITIES = ("low", "normal", "high", "urgent")
STATUSES = ("ready", "in_progress", "paused", "blocked", "review_pending", "done", "cancelled")
REVIEW_TIERS = (0, 1, 2)
SQLITE_INT64_MIN = -(1 << 63)
SQLITE_INT64_MAX = (1 << 63) - 1
RunnerSelectionProvider = Callable[
    [Mapping[str, Any], str],
    VerificationRunnerGateSelection | None,
]

PUBLIC_TASK_FIELDS = (
    "task_id",
    "project_id",
    "title",
    "description",
    "kind",
    "lane",
    "lane_order",
    "priority",
    "status",
    "blocked_reason",
    "pause_reason",
    "review_tier",
    "verification",
    "tags",
    "created_at",
    "updated_at",
    "completed_at",
)

PUBLIC_EVENT_FIELDS = (
    "task_event_id",
    "task_id",
    "project_id",
    "event_type",
    "summary",
    "created_at",
)

TASK_SHOW_FIELDS = PUBLIC_TASK_FIELDS + (
    "completion_commit_required",
    "completion_commit_hash",
    "completion_evidence_kind",
    "completion_evidence_revision",
    "completion_evidence_reason",
    "external_revision_approved",
    "review_target_kind",
    "review_target_value",
    "review_target_base_revision",
    "review_target_generation",
)

VIEWER_TASK_FIELDS = tuple(
    field
    for field in TASK_SHOW_FIELDS
    if field != "review_target_base_revision"
)

TASK_VERIFICATION_INPUT_LIMIT = 1_000


TEXT_LIMITS = {
    "title": 200,
    "description": 4000,
    "tags": 500,
    "add_note": 2000,
    "event_summary": 1000,
    "completion_commit_hash": 128,
    "completion_revision": 500,
    "completion_evidence_reason": 1000,
    "pause_reason": 1000,
    "review_target_value": 500,
    "reviewer_key": 500,
    "review_receipt_summary": 1000,
    "review_finding_summary": 1000,
    "review_finding_resolution": 1000,
    "reopen_reason": 1000,
    "review_tier_change_reason": 1000,
    "handoff_summary": 1000,
    "handoff_rationale": 1000,
    "handoff_occurrence_id": 200,
    "handoff_withdraw_reason": 1000,
    "contract_scope": 4000,
    "contract_acceptance": 4000,
    "contract_constraints": 2000,
    "contract_authority_ref": 500,
    "contract_change_reason": 1000,
}

UPPER_ENV_NAME_PATTERN = r"[A-Z_][A-Z0-9_]*"
KNOWN_ENV_NAME_PATTERN = (
    r"(?i:Path|Temp|Tmp|Home|UserProfile|AppData|LocalAppData|"
    r"ProgramFiles|SystemRoot|ComSpec|Username|User|Pwd|Shell|"
    r"Java_Home|PythonPath|Node_Env|Virtual_Env)"
)
ENV_NAME_PATTERN = rf"(?:{UPPER_ENV_NAME_PATTERN}|{KNOWN_ENV_NAME_PATTERN})"

LEGACY_M19_7_DISPATCH_ASSIGNMENT_PATTERN = re.compile(
    r"(?<![^\s`])dispatch_authorization=[1-9][0-9]*(?=$|[\s`;,)\]])"
)
LEGACY_M19_7_DISPATCH_JSON_PATTERN = re.compile(
    r'"dispatch_authorization"\s*:\s*[1-9][0-9]*(?=\s*(?:$|[,}]))'
)
LEGACY_M19_7_DISPATCH_SENTINEL = "taskgov_legacy_operation_sequence"

PRIVACY_PATTERNS = (
    re.compile(r"Authorization\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"Authorization\s+(Basic|Bearer|Token|ApiKey)\s+\S+", re.IGNORECASE),
    re.compile(r"Authorization\s+(Basic|Bearer|Token|ApiKey)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(Set-)?Cookie\s*:", re.IGNORECASE),
    re.compile(
        r"\b(?!(?<![A-Z0-9_.-])(?-i:max_tokens|token_count|password_length)[ \t]*=[ \t]*[0-9]+(?=$|[\s`;,)}\]]))"
        r"[A-Z0-9_.-]*(Password|Passwd|Pwd|Token|Secret|Cookie|Credential|Credentials|Api[-_]?Key|ApiKey|Access[-_]?Key|AccessKey|Private[-_]?Key|PrivateKey)[A-Z0-9_.-]*\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\"'][A-Z0-9_.-]*dispatch_authorization[A-Z0-9_.-]*[\"']\s*:",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\"'][A-Z0-9_.-]*(Password|Passwd|Pwd|Token|Secret|Cookie|Credential|Credentials|Api[-_]?Key|ApiKey|Access[-_]?Key|AccessKey|Private[-_]?Key|PrivateKey|Authorization)[A-Z0-9_.-]*[\"']\s*:\s*[\"'][^\"']+[\"']",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\"'](?:api\s+key|access\s+key|secret\s+key|private\s+key|client\s+secret(?:\s+key)?)[\"']\s*:\s*[\"'][^\"']+[\"']",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:api|access|secret|private|client\s+secret)\s+key\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(Basic|Bearer)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"-----(BEGIN|END)\s+", re.IGNORECASE),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(
        r"(?im)^\s*(?:private|system|developer)\s+prompt\s*[:=-]\s*\S+"
    ),
    re.compile(
        r"(?im)^\s*(?:private\s+reasoning|chain[- ]of[- ]thought)\s*[:=-]\s*\S+"
    ),
    re.compile(r"(?im)^\s*(?:raw\s+)?review\s+transcript\s*[:=-]\s*\S+"),
    re.compile(
        r"(?im)^\s*stack\s+trace\s*[:=-]?\s*\n\s*(?:#\d+|at\s+|Traceback|Exception|Caused by:|panic:|goroutine|\S+\.\S+)"
    ),
    re.compile(r"(?im)^\s*(log\s+output|raw\s+log)\s*[:=-]?\s*\n\s*\S+"),
    re.compile(
        r"(?im)^\s*(log\s+output|raw\s+log)\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(output|log)\b)"
    ),
    re.compile(
        rf"(?m)\b(?i:environment(?:\s+(?:variables|dump))?|env(?:\s+(?:dump|vars))?)\s*[:=-]?\s*\n\s*{ENV_NAME_PATTERN}\s*[:=]"
    ),
    re.compile(
        rf"\b(?i:environment\s+(?:variables|dump)|env\s+(?:dump|vars))\s+{ENV_NAME_PATTERN}\s*[:=]",
    ),
    re.compile(rf"\b(?i:environment)\s+{ENV_NAME_PATTERN}\s*[:=]"),
    re.compile(rf"\b(?i:env)\s+{ENV_NAME_PATTERN}\s*[:=]"),
    re.compile(r"(?m)^\s*[A-Z_][A-Z0-9_]*=.*\n\s*[A-Z_][A-Z0-9_]*="),
    re.compile(
        rf"(?m)^\s*{ENV_NAME_PATTERN}\s*[:=]\s*\S+.*\n\s*{ENV_NAME_PATTERN}\s*[:=]\s*\S+",
    ),
    re.compile(r"(?im)^\s*(raw\s+)?(stdout|stderr)(\s+dump)?\s*\n\s*\S+"),
    re.compile(
        r"\b(raw\s+)?(stdout|stderr)\s+dump\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(output|log)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?im)^\s*(raw\s+)?(stdout|stderr)(\s+dump)?\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(output|log)\b)"
    ),
    re.compile(
        r"\braw\s+(stdout|stderr)\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(output|log)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?im)^\s*command\s+(output|log)\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(output|log)\b)"
    ),
    re.compile(r"(?im)^\s*standard\s+(output|error)\s*\n\s*\S+"),
    re.compile(
        r"(?im)^\s*standard\s+(output|error)\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(output|log)\b)"
    ),
    re.compile(r"(?im)^\s*=+\s*(raw\s+)?(stdout|stderr)\s*=+"),
    re.compile(r"(?m)^\s*diff --git "),
    re.compile(r"(?m)^\s*@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@"),
    re.compile(r"(?m)^\s*---\s+\S+.*\n\s*\+\+\+\s+\S+.*\n\s*@@\s"),
    re.compile(r"(?m)^\s*at\s+(?:async\s+)?(?:[\w.$<>]+\s+)?\(?[^()\s]+:\d+:\d+\)?"),
    re.compile(r"(?m)^\s*at\s+[\w.<>]+\(.*\)\s+in\s+.+:\s*line\s+\d+"),
    re.compile(r"(?m)^\s*at\s+(?:[\w.-]+/)?[\w.$]+\(.*\.java:\d+\)"),
    re.compile(r"(?m)^Exception in thread\s+"),
    re.compile(r"(?m)^\s*Caused by:\s+\S+"),
    re.compile(r"(?m)^panic:\s+"),
    re.compile(r"(?m)^goroutine\s+\d+\s+\[running\]:"),
)

_SCOPABLE_REGEX_FLAGS = (
    (re.ASCII, "a"),
    (re.IGNORECASE, "i"),
    (re.LOCALE, "L"),
    (re.MULTILINE, "m"),
    (re.DOTALL, "s"),
    (re.UNICODE, "u"),
    (re.VERBOSE, "x"),
)
_SCOPABLE_REGEX_FLAG_MASK = sum(
    int(flag) for flag, _ in _SCOPABLE_REGEX_FLAGS
)
_LEADING_GLOBAL_INLINE_FLAGS_PATTERN = re.compile(
    r"^(?:\(\?[aiLmsux]+\))*"
)


def _scoped_regex_branch(pattern: re.Pattern[str]) -> str:
    """Preserve one compiled pattern's semantics inside an alternation."""

    unsupported_flags = int(pattern.flags) & ~_SCOPABLE_REGEX_FLAG_MASK
    if unsupported_flags:
        raise ValueError("privacy pattern uses unsupported regular-expression flags")
    source = _LEADING_GLOBAL_INLINE_FLAGS_PATTERN.sub(
        "",
        pattern.pattern,
        count=1,
    )
    scoped_flags = "".join(
        name for flag, name in _SCOPABLE_REGEX_FLAGS if pattern.flags & flag
    )
    return f"(?{scoped_flags}:{source})"


COMBINED_PRIVACY_PATTERN = re.compile(
    "(?:"
    + "|".join(_scoped_regex_branch(pattern) for pattern in PRIVACY_PATTERNS)
    + ")"
)

BASIC_AUTH_VALUE_PATTERN = re.compile(r"\bBasic\s+([A-Za-z0-9+/]{8,}={0,2})(?=$|[\s,.;:)])", re.IGNORECASE)
BEARER_TOKEN_VALUE_PATTERN = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{3,})(?=$|[\s,.;:)])", re.IGNORECASE)
RAW_OUTPUT_VALUE_PATTERN = re.compile(
    r"(?im)\b((?:raw\s+)?(?:stdout|stderr)(?:\s+dump)?|command\s+(?:output|log)|standard\s+(?:output|error)|log\s+output|raw\s+log)\s*[:=-]\s*(\S.*)$"
)
STRICT_RAW_OUTPUT_FIELDS = {
    "command_label",
    "description",
    "verification",
    "tags",
    "blocked_reason",
    "pause_reason",
    "completion_revision",
    "completion_evidence_reason",
    "add_note",
    "event_summary",
    "review_target_value",
    "reviewer_key",
    "review_receipt_summary",
    "review_finding_summary",
    "review_finding_resolution",
    "handoff_summary",
    "handoff_rationale",
    "handoff_occurrence_id",
    "handoff_withdraw_reason",
    "handoff_adapter_key",
    "handoff_adapter_version",
    "handoff_last_delivery_code",
    "handoff_receiver_receipt",
    "contract_scope",
    "contract_acceptance",
    "contract_constraints",
    "contract_authority_ref",
    "contract_change_reason",
    "summary",
    "next_action",
    "unresolved_risk",
}
BENIGN_TITLE_RAW_OUTPUT_PREFIXES = (
    "add ",
    "adjust ",
    "document ",
    "fix ",
    "format ",
    "improve ",
    "review ",
    "support ",
    "test ",
    "update ",
    "refine ",
    "revise ",
    "clarify ",
    "clean ",
    "normalize ",
    "standardize ",
)


@dataclass(frozen=True)
class TaskValidationError(Exception):
    code: str
    message: str
    field: str | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class TaskRepositoryError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class AddTaskResult:
    task: dict[str, Any]
    event: dict[str, Any]
    contract_write: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskListResult:
    tasks: list[dict[str, Any]]
    count: int
    limit: int


@dataclass(frozen=True)
class TaskShowResult:
    task: dict[str, Any]
    events: list[dict[str, Any]]
    suggested_next_action: str
    review_evidence: dict[str, Any]
    handoff_summary: dict[str, int]
    contract: dict[str, Any]
    latest_checkpoint: dict[str, Any] | None
    completion_history: dict[str, Any]
    completion_history_latest_summary: dict[str, Any] | None
    verification_evidence: dict[str, Any]


@dataclass(frozen=True)
class ViewerTaskListResult:
    tasks: list[dict[str, Any]]
    event_count: int


@dataclass(frozen=True)
class CurrentTaskResult:
    tasks: list[dict[str, Any]]
    count: int
    total_matching: int
    limit: int
    statuses: tuple[str, ...]


@dataclass(frozen=True)
class EditTaskResult:
    task: dict[str, Any]
    changed_fields: list[str]
    event: dict[str, Any] | None
    contract_write: dict[str, Any] | None = None


TaskEditBasisPrecommitValidator = Callable[
    [dict[str, Any], dict[str, Any]],
    None,
]


@dataclass(frozen=True)
class CompletionBasis:
    task: dict[str, Any] = field(repr=False)
    review_evidence: dict[str, Any] | None = field(repr=False)
    verification_gate: Any = field(repr=False)
    runner_selection: VerificationRunnerGateSelection | None = field(repr=False)
    review_error: tuple[str, str, str | None] | None
    predecessor_incomplete: bool
    lane_order_conflict: bool
    semantic_token: str


@dataclass(frozen=True)
class CompletionPlan:
    request: CompletionRequest
    basis: CompletionBasis = field(repr=False)
    resolution: CompletionResolution


@dataclass(frozen=True)
class StoredTaskSchemaCapabilities:
    """Source-schema facts used by the shared stored Task validator."""

    source_schema_version: int
    verification_limit: int
    has_completion_commit: bool
    has_pause_reason: bool
    has_completion_evidence: bool
    has_review_target: bool
    has_review_target_base: bool
    has_contract_revision: bool
    has_completion_history_coverage: bool


@dataclass(frozen=True)
class StoredTaskValidationResult:
    """Private facts retained from one bounded stored-Task validation."""

    verification_rejection: str | None = None
    verification_rejected_task_ids: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
        compare=False,
    )
    current_contract_rows: dict[str, sqlite3.Row | None] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )


_AUTHORITY_PRIVACY_REUSE_FIELDS = frozenset(
    {"title", "description", "verification"}
)


def _stored_task_privacy_success_cache(
    prevalidated_privacy_successes: frozenset[tuple[str, str]] | None,
) -> set[tuple[str, str]]:
    """Copy one internal same-call authority proof into a local cache."""

    if prevalidated_privacy_successes is None:
        return set()
    if type(prevalidated_privacy_successes) is not frozenset:
        raise _stored_task_unreadable()
    for item in prevalidated_privacy_successes:
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or item[0] not in _AUTHORITY_PRIVACY_REUSE_FIELDS
            or type(item[1]) is not str
        ):
            raise _stored_task_unreadable()
    return set(prevalidated_privacy_successes)


def stored_task_schema_capabilities(
    source_schema_version: object,
) -> StoredTaskSchemaCapabilities:
    """Resolve one immutable source-schema capability without reading SQLite."""

    if (
        type(source_schema_version) is not int
        or (
            not 1 <= source_schema_version <= SCHEMA_VERSION
            and source_schema_version != PRIVATE_SCHEMA22_VERSION
        )
    ):
        raise _stored_task_unreadable()
    try:
        verification_limit = stored_task_verification_limit(
            source_schema_version
        )
    except StorageError as exc:
        raise _stored_task_unreadable() from exc
    return StoredTaskSchemaCapabilities(
        source_schema_version=source_schema_version,
        verification_limit=verification_limit,
        has_completion_commit=source_schema_version >= 2,
        has_pause_reason=source_schema_version >= 3,
        has_completion_evidence=source_schema_version >= 4,
        has_review_target=source_schema_version >= 5,
        has_review_target_base=source_schema_version >= 6,
        has_contract_revision=source_schema_version >= 8,
        has_completion_history_coverage=source_schema_version >= 15,
    )


def _stored_task_unreadable() -> StorageError:
    return StorageError(
        "project_state_unreadable",
        "project state could not be read safely",
    )


def fetch_stored_task_rows(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] | list[Any] = (),
) -> list[sqlite3.Row]:
    """Fetch a Task batch while preserving busy and sanitizing decode faults."""

    try:
        return connection.execute(query, parameters).fetchall()
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc


def fetch_stored_task_row(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] | list[Any] = (),
) -> sqlite3.Row | None:
    """Fetch one Task row with the same storage-fault mapping as batches."""

    try:
        return connection.execute(query, parameters).fetchone()
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc


def _stored_value(row: sqlite3.Row | dict[str, Any], field: str) -> object:
    try:
        if field not in row.keys():
            raise KeyError(field)
        return row[field]
    except (IndexError, KeyError, TypeError, AttributeError) as exc:
        raise _stored_task_unreadable() from exc


def _stored_text(
    row: sqlite3.Row | dict[str, Any],
    field: str,
    *,
    required: bool = False,
    limit: int | None = None,
    verification_limit: int | None = None,
    privacy_field: str | None = None,
    privacy_success_cache: set[tuple[str, str]] | None = None,
) -> tuple[str, str | None]:
    value = _stored_value(row, field)
    if type(value) is not str:
        raise _stored_task_unreadable()
    if required and not value.strip():
        raise _stored_task_unreadable()
    verification_rejection: str | None = None
    resolved_privacy_field = privacy_field or field
    privacy_key = (resolved_privacy_field, value)
    if privacy_success_cache is None or privacy_key not in privacy_success_cache:
        try:
            reject_private_or_raw_content(resolved_privacy_field, value)
        except TaskValidationError as exc:
            if field == "verification" and exc.code == "privacy_rejected":
                verification_rejection = "privacy"
            else:
                raise _stored_task_unreadable() from exc
        else:
            if privacy_success_cache is not None:
                privacy_success_cache.add(privacy_key)
    if limit is not None and len(value) > limit:
        raise _stored_task_unreadable()
    if (
        verification_limit is not None
        and len(value) > verification_limit
        and verification_rejection != "privacy"
    ):
        verification_rejection = "capacity"
    return value, verification_rejection


def _stored_integer(
    row: sqlite3.Row | dict[str, Any],
    field: str,
    *,
    nullable: bool = False,
    minimum: int = SQLITE_INT64_MIN,
    maximum: int = SQLITE_INT64_MAX,
) -> int | None:
    value = _stored_value(row, field)
    if value is None and nullable:
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise _stored_task_unreadable()
    return value


def _stored_timestamp(
    row: sqlite3.Row | dict[str, Any],
    field: str,
    *,
    nullable: bool = False,
) -> str | None:
    value = _stored_value(row, field)
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise _stored_task_unreadable()
    try:
        return validate_utc_timestamp(value, field=field)
    except StorageError as exc:
        raise _stored_task_unreadable() from exc


def _validate_stored_completion_evidence(
    row: sqlite3.Row | dict[str, Any],
    privacy_success_cache: set[tuple[str, str]],
) -> None:
    task = {
        "completion_evidence_kind": _stored_text(
            row,
            "completion_evidence_kind",
            privacy_success_cache=privacy_success_cache,
        )[0],
        "completion_evidence_revision": _stored_text(
            row,
            "completion_evidence_revision",
            limit=TEXT_LIMITS["completion_revision"],
            privacy_field="completion_revision",
            privacy_success_cache=privacy_success_cache,
        )[0],
        "completion_evidence_reason": _stored_text(
            row,
            "completion_evidence_reason",
            limit=TEXT_LIMITS["completion_evidence_reason"],
            privacy_success_cache=privacy_success_cache,
        )[0],
        "external_revision_approved": _stored_integer(
            row,
            "external_revision_approved",
            minimum=0,
            maximum=1,
        ),
        "completion_commit_required": _stored_integer(
            row,
            "completion_commit_required",
            minimum=0,
            maximum=1,
        ),
        "completion_commit_hash": _stored_text(
            row,
            "completion_commit_hash",
            limit=TEXT_LIMITS["completion_revision"],
            privacy_field="completion_revision",
            privacy_success_cache=privacy_success_cache,
        )[0],
    }
    kind = str(task["completion_evidence_kind"])
    revision = str(task["completion_evidence_revision"])
    legacy_hash = str(task["completion_commit_hash"])
    if kind == "git_commit" and set(revision) == {"0"}:
        raise _stored_task_unreadable()
    if kind == "legacy_unverified" and not revision:
        raise _stored_task_unreadable()
    try:
        validate_evidence_matrix(task, allow_legacy=True)
    except (CompletionEvidenceError, TypeError, ValueError, OverflowError) as exc:
        raise _stored_task_unreadable() from exc


def _validate_stored_review_target(
    row: sqlite3.Row | dict[str, Any],
    capabilities: StoredTaskSchemaCapabilities,
    privacy_success_cache: set[tuple[str, str]],
) -> None:
    kind = _stored_text(
        row,
        "review_target_kind",
        privacy_success_cache=privacy_success_cache,
    )[0]
    value = _stored_text(
        row,
        "review_target_value",
        limit=TEXT_LIMITS["review_target_value"],
        privacy_success_cache=privacy_success_cache,
    )[0]
    generation = _stored_integer(
        row,
        "review_target_generation",
        minimum=0,
    )
    base_revision = (
        _stored_text(
            row,
            "review_target_base_revision",
            limit=TEXT_LIMITS["review_target_value"],
            privacy_success_cache=privacy_success_cache,
        )[0]
        if capabilities.has_review_target_base
        else ""
    )
    if not kind:
        if value or base_revision:
            raise _stored_task_unreadable()
        return
    if generation is None or generation <= 0 or not value or value != value.strip():
        raise _stored_task_unreadable()
    if kind == "git_commit":
        if (
            FULL_GIT_OBJECT_ID.fullmatch(value) is None
            or set(value) == {"0"}
            or base_revision
        ):
            raise _stored_task_unreadable()
    elif kind == "diff_fingerprint":
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None or base_revision:
            raise _stored_task_unreadable()
    elif kind == "external_revision":
        if base_revision:
            raise _stored_task_unreadable()
    elif kind == "git_snapshot":
        if (
            not capabilities.has_review_target_base
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
            or FULL_GIT_OBJECT_ID.fullmatch(base_revision) is None
            or set(base_revision) == {"0"}
        ):
            raise _stored_task_unreadable()
    else:
        raise _stored_task_unreadable()


def _validate_stored_task_row(
    row: sqlite3.Row | dict[str, Any],
    *,
    capabilities: StoredTaskSchemaCapabilities,
    expected_project_id: str,
    privacy_success_cache: set[tuple[str, str]],
) -> str | None:
    task_id = _stored_text(
        row,
        "task_id",
        required=True,
        limit=128,
        privacy_success_cache=privacy_success_cache,
    )[0]
    if task_id != task_id.strip():
        raise _stored_task_unreadable()
    project_id = _stored_text(
        row,
        "project_id",
        required=True,
        privacy_success_cache=privacy_success_cache,
    )[0]
    if project_id != expected_project_id:
        raise _stored_task_unreadable()
    _stored_text(
        row,
        "title",
        required=True,
        limit=TEXT_LIMITS["title"],
        privacy_success_cache=privacy_success_cache,
    )
    _stored_text(
        row,
        "description",
        limit=TEXT_LIMITS["description"],
        privacy_success_cache=privacy_success_cache,
    )
    kind = _stored_text(
        row,
        "kind",
        privacy_success_cache=privacy_success_cache,
    )[0]
    lane = _stored_text(
        row,
        "lane",
        privacy_success_cache=privacy_success_cache,
    )[0]
    lane_order = _stored_integer(row, "lane_order", nullable=True)
    priority = _stored_text(
        row,
        "priority",
        privacy_success_cache=privacy_success_cache,
    )[0]
    status = _stored_text(
        row,
        "status",
        privacy_success_cache=privacy_success_cache,
    )[0]
    blocked_reason = _stored_text(
        row,
        "blocked_reason",
        privacy_success_cache=privacy_success_cache,
    )[0]
    pause_reason = (
        _stored_text(
            row,
            "pause_reason",
            limit=TEXT_LIMITS["pause_reason"],
            privacy_success_cache=privacy_success_cache,
        )[0]
        if capabilities.has_pause_reason
        else ""
    )
    review_tier = _stored_integer(
        row,
        "review_tier",
        minimum=0,
        maximum=2,
    )
    _, verification_rejection = _stored_text(
        row,
        "verification",
        verification_limit=capabilities.verification_limit,
        privacy_success_cache=privacy_success_cache,
    )
    _stored_text(
        row,
        "tags",
        limit=TEXT_LIMITS["tags"],
        privacy_success_cache=privacy_success_cache,
    )
    created_at = _stored_timestamp(row, "created_at")
    updated_at = _stored_timestamp(row, "updated_at")
    completed_at = _stored_timestamp(row, "completed_at", nullable=True)

    allowed_statuses = STATUSES if capabilities.has_pause_reason else tuple(
        value for value in STATUSES if value != "paused"
    )
    if (
        kind not in KINDS
        or priority not in PRIORITIES
        or status not in allowed_statuses
        or review_tier not in REVIEW_TIERS
        or lane != canonical_lane(lane)
        or (kind == "sequential" and (not lane or lane_order is None))
        or (status == "blocked" and not blocked_reason.strip())
        or (
            capabilities.has_pause_reason
            and (
                (status == "paused" and not pause_reason.strip())
                or (status != "paused" and pause_reason != "")
            )
        )
        or (completed_at is None) != (status != "done")
        or created_at is None
        or updated_at is None
    ):
        raise _stored_task_unreadable()

    if capabilities.has_completion_commit:
        _stored_integer(
            row,
            "completion_commit_required",
            minimum=0,
            maximum=1,
        )
        _stored_text(
            row,
            "completion_commit_hash",
            limit=TEXT_LIMITS["completion_revision"],
            privacy_field="completion_revision",
            privacy_success_cache=privacy_success_cache,
        )
    if capabilities.has_completion_evidence:
        _validate_stored_completion_evidence(row, privacy_success_cache)
    if capabilities.has_review_target:
        _validate_stored_review_target(
            row,
            capabilities,
            privacy_success_cache,
        )
    if capabilities.has_contract_revision:
        _stored_integer(row, "current_contract_revision", minimum=0)
    if capabilities.has_completion_history_coverage:
        if _stored_text(
            row,
            "completion_history_coverage",
            privacy_success_cache=privacy_success_cache,
        )[0] not in {
            "legacy_unknown",
            "complete",
        }:
            raise _stored_task_unreadable()
    return verification_rejection


def _validate_stored_contract_relationships(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    *,
    expected_project_id: str,
    privacy_success_cache: set[tuple[str, str]],
) -> dict[str, sqlite3.Row | None]:
    """Validate current Contract pointers with one selected-batch read."""

    pointers: dict[str, int] = {}
    for row in rows:
        task_id = _stored_text(
            row,
            "task_id",
            required=True,
            limit=128,
            privacy_success_cache=privacy_success_cache,
        )[0]
        pointer = _stored_integer(row, "current_contract_revision", minimum=0)
        if pointer is None or task_id in pointers:
            raise _stored_task_unreadable()
        pointers[task_id] = pointer
    if not pointers:
        return {}

    selected_ids = json.dumps(
        list(pointers),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    relationship_rows = fetch_stored_task_rows(
        connection,
        """
        WITH selected_task_ids(value) AS (
            SELECT value FROM json_each(?)
        ),
        selected_storage_keys(value) AS (
            SELECT value FROM selected_task_ids
            UNION ALL
            SELECT CAST(value AS BLOB) FROM selected_task_ids
        )
        SELECT *
          FROM task_contract_revisions
         WHERE task_id IN (SELECT value FROM selected_storage_keys)
         ORDER BY task_id, revision
        """,
        (selected_ids,),
    )
    revisions: dict[str, dict[int, sqlite3.Row]] = {
        task_id: {} for task_id in pointers
    }
    for relationship in relationship_rows:
        project_id = _stored_text(
            relationship,
            "project_id",
            required=True,
            privacy_success_cache=privacy_success_cache,
        )[0]
        task_id = _stored_text(
            relationship,
            "task_id",
            required=True,
            limit=128,
            privacy_success_cache=privacy_success_cache,
        )[0]
        revision = _stored_integer(
            relationship,
            "revision",
            minimum=1,
        )
        if (
            revision is None
            or project_id != expected_project_id
            or task_id not in pointers
            or pointers[task_id] == 0
            or revision in revisions[task_id]
        ):
            raise _stored_task_unreadable()
        revisions[task_id][revision] = relationship

    for task_id, pointer in pointers.items():
        related = revisions[task_id]
        if pointer == 0:
            if related:
                raise _stored_task_unreadable()
        elif not related or pointer not in related or pointer != max(related):
            raise _stored_task_unreadable()
    return {
        task_id: (revisions[task_id].get(pointer) if pointer > 0 else None)
        for task_id, pointer in pointers.items()
    }


def validate_stored_task_rows(
    rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    *,
    connection: sqlite3.Connection | None = None,
    source_schema_version: object,
    expected_project_id: str,
    verification_rejection_is_local: bool = False,
    _prevalidated_privacy_successes: frozenset[tuple[str, str]] | None = None,
) -> StoredTaskValidationResult:
    """Validate one loaded Task batch before projection or derived use.

    No stored value is coerced, normalized, rewritten, or included in an error.
    The recovery-only flag preserves M21.4B's candidate-local exception for
    verification privacy/capacity while every structural fault stays fatal.
    The private authority seed is copied, shape-checked, and consumed only by
    this call's ordinary field-bound privacy cache.
    """

    if type(expected_project_id) is not str or not expected_project_id:
        raise _stored_task_unreadable()
    capabilities = stored_task_schema_capabilities(source_schema_version)
    privacy_success_cache = _stored_task_privacy_success_cache(
        _prevalidated_privacy_successes
    )
    rejection: str | None = None
    rejected_task_ids: set[str] = set()
    for row in rows:
        row_rejection = _validate_stored_task_row(
            row,
            capabilities=capabilities,
            expected_project_id=expected_project_id,
            privacy_success_cache=privacy_success_cache,
        )
        if row_rejection == "privacy":
            rejection = "privacy"
        elif row_rejection == "capacity" and rejection is None:
            rejection = "capacity"
        if row_rejection is not None:
            rejected_task_ids.add(str(row["task_id"]))
    current_contract_rows: dict[str, sqlite3.Row | None] = {}
    if capabilities.has_contract_revision:
        if connection is None:
            raise _stored_task_unreadable()
        current_contract_rows = _validate_stored_contract_relationships(
            connection,
            rows,
            expected_project_id=expected_project_id,
            privacy_success_cache=privacy_success_cache,
        )
    if rejection is not None and not verification_rejection_is_local:
        raise _stored_task_unreadable()
    return StoredTaskValidationResult(
        verification_rejection=rejection,
        verification_rejected_task_ids=frozenset(rejected_task_ids),
        current_contract_rows=current_contract_rows,
    )


def validate_current_stored_task_rows(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    *,
    expected_project_id: str,
) -> None:
    version = current_schema_version(connection)
    validation = validate_stored_task_rows(
        rows,
        connection=connection,
        source_schema_version=(
            version
            if 1 <= version <= SCHEMA_VERSION or version == PRIVATE_SCHEMA22_VERSION
            else SCHEMA_VERSION
        ),
        expected_project_id=expected_project_id,
    )
    if version >= 18:
        try:
            validate_selected_task_authority_storage(
                connection,
                rows,
                expected_project_id=expected_project_id,
                current_contract_rows=validation.current_contract_rows,
            )
        except StorageError as exc:
            if exc.code == "database_busy":
                raise
            raise _stored_task_unreadable() from exc


def fetch_validated_current_task_row(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> sqlite3.Row | None:
    """Read one current Task and its Contract relation in one snapshot."""

    owns_read_transaction = not connection.in_transaction
    try:
        if owns_read_transaction:
            connection.execute("BEGIN")
        row = fetch_stored_task_row(
            connection,
            "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
            (project_id, task_id),
        )
        if row is not None:
            validate_current_stored_task_rows(
                connection,
                [row],
                expected_project_id=project_id,
            )
        return row
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc
    finally:
        if owns_read_transaction and connection.in_transaction:
            connection.rollback()


def validation_error(code: str, message: str, field: str | None = None) -> TaskValidationError:
    return TaskValidationError(code=code, message=message, field=field)


def ensure_string(field: str, value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise validation_error("invalid_argument", f"{field} must be a string", field)
    return value


def _reject_private_or_raw_content_value(field: str, guard_value: str) -> None:
    if COMBINED_PRIVACY_PATTERN.search(guard_value):
        raise validation_error(
            "privacy_rejected",
            f"{field} appears to contain a secret, raw log, or dump content",
            field,
        )
    if (
        contains_basic_auth_value(guard_value)
        or contains_bearer_token_value(field, guard_value)
        or contains_raw_output_value(field, guard_value)
    ):
        raise validation_error(
            "privacy_rejected",
            f"{field} appears to contain a secret, raw log, or dump content",
            field,
        )


def reject_private_or_raw_content(field: str, value: str) -> None:
    _reject_private_or_raw_content_value(field, value)


def _legacy_m19_7_stored_guard_value(value: str) -> str:
    guard_value = LEGACY_M19_7_DISPATCH_ASSIGNMENT_PATTERN.sub(
        LEGACY_M19_7_DISPATCH_SENTINEL,
        value,
    )
    return LEGACY_M19_7_DISPATCH_JSON_PATTERN.sub(
        f'"{LEGACY_M19_7_DISPATCH_SENTINEL}":1',
        guard_value,
    )


def contains_basic_auth_value(value: str) -> bool:
    for match in BASIC_AUTH_VALUE_PATTERN.finditer(value):
        token = match.group(1)
        padded = token + ("=" * (-len(token) % 4))
        try:
            decoded = base64.b64decode(padded, validate=True)
        except (ValueError, binascii.Error):
            continue
        if b":" in decoded:
            return True
    return False


def contains_bearer_token_value(field: str, value: str) -> bool:
    for match in BEARER_TOKEN_VALUE_PATTERN.finditer(value):
        token = match.group(1).rstrip(",.;)")
        token_lower = token.lower()
        tail = value[match.end() :].strip()
        if token_lower in {"authentication", "auth", "token", "tokens", "header", "headers", "support", "behavior"}:
            continue
        if token_lower in {"secret", "abc123"} or "secret" in token_lower:
            return True
        if token_lower.startswith(("sk-", "xox", "ghp_", "gho_", "pat_")):
            return True
        if any(not char.isalpha() for char in token) and len(token) >= 5:
            return True
        if len(token) >= 12 and not tail:
            return True
        if field != "title" and len(token) >= 20:
            return True
    return False


def contains_raw_output_value(field: str, value: str) -> bool:
    if field in STRICT_RAW_OUTPUT_FIELDS:
        return RAW_OUTPUT_VALUE_PATTERN.search(value) is not None
    if field == "title":
        for match in RAW_OUTPUT_VALUE_PATTERN.finditer(value):
            payload = match.group(2).strip().lower()
            if not payload.startswith(BENIGN_TITLE_RAW_OUTPUT_PREFIXES):
                return True
    return False


def _validate_text(
    field: str,
    value: Any,
    *,
    required: bool = False,
    limit: int | None = None,
    default: str = "",
    legacy_m19_7_stored: bool = False,
) -> str:
    text = ensure_string(field, value, default=default)
    if required:
        text = text.strip()
        if not text:
            raise validation_error("invalid_argument", f"{field} is required", field)
    guard_value = (
        _legacy_m19_7_stored_guard_value(text)
        if legacy_m19_7_stored
        else text
    )
    _reject_private_or_raw_content_value(field, guard_value)
    if limit is not None and len(text) > limit:
        raise validation_error(
            "invalid_argument",
            f"{field} must be {limit} characters or fewer",
            field,
        )
    return text


def validate_text(
    field: str,
    value: Any,
    *,
    required: bool = False,
    limit: int | None = None,
    default: str = "",
) -> str:
    return _validate_text(
        field,
        value,
        required=required,
        limit=limit,
        default=default,
    )


def validate_legacy_m19_7_stored_text(
    field: str,
    value: Any,
    *,
    required: bool = False,
    limit: int | None = None,
    default: str = "",
) -> str:
    """Validate one already-stored M19.7 text field without changing bytes."""

    return _validate_text(
        field,
        value,
        required=required,
        limit=limit,
        default=default,
        legacy_m19_7_stored=True,
    )


def validate_choice(field: str, value: Any, allowed: tuple[str, ...], code: str) -> str:
    text = ensure_string(field, value).strip()
    if text not in allowed:
        raise validation_error(code, f"{field} must be one of: {', '.join(allowed)}", field)
    return text


def validate_review_tier(value: Any) -> int:
    if isinstance(value, bool):
        raise validation_error("invalid_review_tier", "review_tier must be 0, 1, or 2", "review_tier")
    if isinstance(value, int):
        tier = value
    elif isinstance(value, str) and value.strip().isdigit():
        tier = int(value.strip())
    else:
        raise validation_error("invalid_review_tier", "review_tier must be 0, 1, or 2", "review_tier")
    if tier not in REVIEW_TIERS:
        raise validation_error("invalid_review_tier", "review_tier must be 0, 1, or 2", "review_tier")
    return tier


def validate_lane(value: Any) -> str:
    return canonical_lane(validate_text("lane", value))


def validate_sqlite_int64(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise validation_error("invalid_argument", f"{field} must be an integer", field)
    if isinstance(value, int):
        integer = value
    elif (
        isinstance(value, str)
        and re.fullmatch(r"-?[0-9]+", value.strip()) is not None
    ):
        try:
            integer = int(value.strip())
        except (ValueError, OverflowError) as exc:
            raise validation_error(
                "invalid_argument",
                f"{field} must be within SQLite's signed 64-bit integer range",
                field,
            ) from exc
    else:
        raise validation_error("invalid_argument", f"{field} must be an integer", field)
    if integer < SQLITE_INT64_MIN or integer > SQLITE_INT64_MAX:
        raise validation_error(
            "invalid_argument",
            f"{field} must be within SQLite's signed 64-bit integer range",
            field,
        )
    return integer


def validate_lane_order(value: Any) -> int | None:
    if value is None:
        return None
    return validate_sqlite_int64(value, field="lane_order")


def validate_event_summary(summary: Any) -> str:
    return validate_text(
        "event_summary",
        summary,
        required=True,
        limit=TEXT_LIMITS["event_summary"],
    )


def validate_task_input(
    *,
    title: Any,
    description: Any = "",
    kind: Any = "optional",
    lane: Any = "",
    lane_order: Any = None,
    priority: Any = "normal",
    status: Any = "ready",
    blocked_reason: Any = "",
    pause_reason: Any = "",
    review_tier: Any = 1,
    verification: Any = "",
    tags: Any = "",
    add_note: Any = None,
) -> dict[str, Any]:
    normalized = {
        "title": validate_text("title", title, required=True, limit=TEXT_LIMITS["title"]),
        "description": validate_text("description", description, limit=TEXT_LIMITS["description"]),
        "kind": validate_choice("kind", kind, KINDS, "invalid_kind"),
        "lane": validate_lane(lane),
        "lane_order": validate_lane_order(lane_order),
        "priority": validate_choice("priority", priority, PRIORITIES, "invalid_priority"),
        "status": validate_choice("status", status, STATUSES, "invalid_status"),
        "blocked_reason": validate_text("blocked_reason", blocked_reason),
        "pause_reason": validate_text(
            "pause_reason",
            pause_reason,
            limit=TEXT_LIMITS["pause_reason"],
        ),
        "review_tier": validate_review_tier(review_tier),
        "verification": validate_text(
            "verification",
            verification,
            limit=TASK_VERIFICATION_INPUT_LIMIT,
        ),
        "tags": validate_text("tags", tags, limit=TEXT_LIMITS["tags"]),
    }
    if normalized["status"] == "blocked" and not normalized["blocked_reason"].strip():
        raise validation_error(
            "blocked_reason_required",
            "blocked_reason is required when status is blocked",
            "blocked_reason",
        )
    if normalized["status"] == "done":
        raise validation_error(
            "initial_done_forbidden",
            "task add --status done is prohibited; add the task first and complete it with task edit",
            "status",
        )
    if normalized["status"] == "paused":
        raise validation_error(
            "initial_paused_forbidden",
            "task add --status paused is prohibited; pause work with task edit after it starts",
            "status",
        )
    if normalized["pause_reason"]:
        raise validation_error(
            "invalid_argument",
            "pause_reason may be recorded only while a task is paused",
            "pause_reason",
        )
    if add_note is not None:
        normalized["add_note"] = validate_text("add_note", add_note, limit=TEXT_LIMITS["add_note"])
    return normalized


def generate_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def next_lane_order(connection: sqlite3.Connection, project_id: str, lane: str) -> int:
    row = connection.execute(
        f"""
        SELECT MAX(lane_order) AS max_order
          FROM tasks
         WHERE project_id = ?
           AND kind = 'sequential'
           AND {canonical_lane_sql("lane")} = ?
        """,
        (project_id, lane),
    ).fetchone()
    if row is None or row["max_order"] is None:
        return 1
    maximum = int(row["max_order"])
    if maximum >= SQLITE_INT64_MAX:
        raise validation_error(
            "invalid_argument",
            "lane_order cannot be assigned because the lane reached SQLite's signed 64-bit maximum",
            "lane_order",
        )
    return maximum + 1


def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {field: row[field] for field in PUBLIC_TASK_FIELDS if field in row_keys}


def canonical_lane_order_conflict(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    lane: str,
    lane_order: int,
) -> bool:
    row = connection.execute(
        f"""
        SELECT 1
          FROM tasks
         WHERE project_id = ?
           AND kind = 'sequential'
           AND task_id != ?
           AND {canonical_lane_sql("lane")} = ?
           AND lane_order = ?
         LIMIT 1
        """,
        (project_id, task_id, canonical_lane(lane), lane_order),
    ).fetchone()
    return row is not None


def row_to_show_task(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {field: row[field] for field in TASK_SHOW_FIELDS if field in row_keys}


def row_to_viewer_task(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {field: row[field] for field in VIEWER_TASK_FIELDS if field in row_keys}


def row_to_internal_task(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def row_to_event(
    row: dict[str, Any] | sqlite3.Row,
) -> dict[str, Any]:
    return {field: row[field] for field in PUBLIC_EVENT_FIELDS}


def find_task_ids_by_completion_commit_hash(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    completion_commit_hash: Any,
) -> list[str]:
    commit_hash = validate_text(
        "completion_commit_hash",
        completion_commit_hash,
        required=True,
        limit=TEXT_LIMITS["completion_commit_hash"],
    )
    rows = connection.execute(
        """
        SELECT task_id
          FROM tasks
         WHERE project_id = ?
           AND completion_commit_hash = ?
         ORDER BY task_id
        """,
        (project.project_id, commit_hash),
    ).fetchall()
    return [str(row["task_id"]) for row in rows]


def split_tags(tags: str) -> list[str]:
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def task_matches_tag(tags: str, requested: str) -> bool:
    return requested in split_tags(tags)


def create_task_event(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    event_type: str,
    summary: str,
    created_at: str,
    completion_cycle_id: str | None = None,
) -> dict[str, Any]:
    if completion_cycle_id is not None:
        cycle = connection.execute(
            """
            SELECT project_id, task_id, origin
              FROM task_completion_cycles
             WHERE completion_cycle_id = ?
            """,
            (completion_cycle_id,),
        ).fetchone()
        completion_event = event_type in {
            "task_updated",
            "review_tier_changed",
        }
        reopen_event = event_type == "task_reopened"
        if (
            cycle is None
            or str(cycle["project_id"]) != project_id
            or str(cycle["task_id"]) != task_id
            or (
                completion_event
                and str(cycle["origin"]) != "native_done"
            )
            or not (completion_event or reopen_event)
        ):
            raise completion_history_inconsistent()
        linked = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM task_events
                 WHERE completion_cycle_id = ?
                   AND (
                     (? = 1 AND event_type IN (
                       'task_updated', 'review_tier_changed'
                     ))
                     OR (? = 1 AND event_type = 'task_reopened')
                   )
                """,
                (
                    completion_cycle_id,
                    int(completion_event),
                    int(reopen_event),
                ),
            ).fetchone()[0]
        )
        if linked:
            raise completion_history_inconsistent()
    event = {
        "task_event_id": generate_id("tg_event"),
        "task_id": task_id,
        "project_id": project_id,
        "event_type": event_type,
        "summary": validate_event_summary(summary),
        "created_at": created_at,
        "completion_cycle_id": completion_cycle_id,
    }
    link_column = ", completion_cycle_id" if completion_cycle_id else ""
    link_value = ", :completion_cycle_id" if completion_cycle_id else ""
    connection.execute(
        f"""
        INSERT INTO task_events(
          task_event_id, task_id, project_id, event_type, summary, created_at
          {link_column}
        )
        VALUES (
          :task_event_id, :task_id, :project_id, :event_type, :summary,
          :created_at
          {link_value}
        )
        """,
        event,
    )
    return row_to_event(event)


def begin_task_write(
    connection: sqlite3.Connection,
    database_target: DatabaseTarget | None,
) -> None:
    if connection.in_transaction:
        return
    if database_target is not None:
        begin_initialized_write(connection, database_target)
    else:
        connection.execute("BEGIN IMMEDIATE")


def ensure_git_preflight_outside_transaction(
    connection: sqlite3.Connection,
) -> None:
    if connection.in_transaction:
        raise TaskRepositoryError(
            "internal_error",
            "Git preflight cannot run inside an active database transaction",
        )


def add_task(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    effort_profile: Any | None = None,
    database_target: DatabaseTarget | None = None,
    **task_input: Any,
) -> AddTaskResult:
    from task_governance_tool.contracts import (
        CONTRACT_ADD_STATUSES,
        add_initial_contract,
        split_contract_input,
    )

    task_input, contract_input = split_contract_input(task_input)
    raw_status = task_input.get("status", "ready")
    if (
        contract_input
        and isinstance(raw_status, str)
        and raw_status.strip() in STATUSES
        and raw_status.strip() not in CONTRACT_ADD_STATUSES
    ):
        raise validation_error(
            "contract_activation_forbidden",
            "an initial Contract is not allowed for this task status",
            "status",
        )
    normalized = validate_task_input(**task_input)
    task_id = generate_id("tg_task")
    from task_governance_tool.effort import (
        prepare_task_transition,
        record_task_transition,
    )

    effort_preflight = prepare_task_transition(
        connection,
        project,
        task_id=task_id,
        previous_status=None,
        current_status=str(normalized["status"]),
        profile=effort_profile,
    )
    begin_task_write(connection, database_target)

    lane = normalized["lane"]
    lane_order = normalized["lane_order"]
    if normalized["kind"] == "sequential":
        lane = lane or "default"
        if lane_order is None:
            lane_order = next_lane_order(connection, project.project_id, lane)

    now = utc_now()
    completed_at = now if normalized["status"] == "done" else None
    schema_version = current_schema_version(connection)
    row = {
        "task_id": task_id,
        "project_id": project.project_id,
        "title": normalized["title"],
        "description": normalized["description"],
        "kind": normalized["kind"],
        "lane": lane,
        "lane_order": lane_order,
        "priority": normalized["priority"],
        "status": normalized["status"],
        "blocked_reason": normalized["blocked_reason"],
        "pause_reason": normalized["pause_reason"],
        "review_tier": normalized["review_tier"],
        "verification": normalized["verification"],
        "tags": normalized["tags"],
        "created_at": now,
        "updated_at": now,
        "completed_at": completed_at,
    }
    completion_history_column = ""
    completion_history_value = ""
    if schema_version >= 15:
        row["completion_history_coverage"] = (
            "complete" if schema_version >= 16 else "legacy_unknown"
        )
        completion_history_column = ", completion_history_coverage"
        completion_history_value = ", :completion_history_coverage"
    savepoint = f"taskgov_ordering_{secrets.token_hex(4)}"
    contract_write: dict[str, Any] | None = None
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        connection.execute(
            f"""
            INSERT INTO tasks(
              task_id,
              project_id,
              title,
              description,
              kind,
              lane,
              lane_order,
              priority,
              status,
              blocked_reason,
              pause_reason,
              review_tier,
              verification,
              tags,
              created_at,
              updated_at,
              completed_at
              {completion_history_column}
            )
            VALUES (
              :task_id,
              :project_id,
              :title,
              :description,
              :kind,
              :lane,
              :lane_order,
              :priority,
              :status,
              :blocked_reason,
              :pause_reason,
              :review_tier,
              :verification,
              :tags,
              :created_at,
              :updated_at,
              :completed_at
              {completion_history_value}
            )
            """,
            row,
        )
        if row["kind"] == "sequential":
            if canonical_lane_order_conflict(
                connection,
                project_id=project.project_id,
                task_id=task_id,
                lane=str(row["lane"]),
                lane_order=int(row["lane_order"]),
            ):
                raise TaskRepositoryError(
                    "invalid_argument",
                    "task conflicts with an existing canonical sequential lane order",
                )
            invalid_task_id = first_out_of_order_advanced_task(
                connection,
                project_id=project.project_id,
                lanes={str(row["lane"])},
            )
            if invalid_task_id is not None:
                raise TaskRepositoryError(
                    "sequential_predecessor_incomplete",
                    "sequential lane contains active, review-pending, or done work with an incomplete predecessor",
                )
        if contract_input:
            contract_write = add_initial_contract(
                connection,
                project_id=project.project_id,
                task_id=task_id,
                status=str(row["status"]),
                contract_input=contract_input,
                created_at=now,
            ).to_dict()
        if schema_version >= 18:
            capture_or_reuse_current_authority_snapshot_locked(
                connection,
                project_id=project.project_id,
                task_id=task_id,
                created_at=now,
            )
        event = create_task_event(
            connection,
            project_id=project.project_id,
            task_id=task_id,
            event_type="task_added",
            summary="Task registered",
            created_at=now,
        )
        record_task_transition(
            connection,
            project,
            task_id=task_id,
            previous_status=None,
            current_status=str(row["status"]),
            profile=effort_profile,
            occurred_at=now,
            preflight=effort_preflight,
        )
        task = fetch_stored_task_row(
            connection,
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        )
        if task is None:
            raise TaskRepositoryError(
                "internal_error",
                "task was not readable after insert",
            )
        validate_stored_task_rows(
            [task],
            connection=connection,
            source_schema_version=schema_version,
            expected_project_id=project.project_id,
        )
    except sqlite3.IntegrityError as exc:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise TaskRepositoryError(
            "invalid_argument",
            "task violates database constraints, such as duplicate sequential lane order",
        ) from exc
    except Exception:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")

    return AddTaskResult(
        task=row_to_task(task),
        event=event,
        contract_write=contract_write,
    )


def validate_limit(value: Any, *, default: int = 20) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise validation_error("invalid_argument", "limit must be a positive integer", "limit")
    if isinstance(value, int):
        limit = value
    elif (
        isinstance(value, str)
        and value.strip().isascii()
        and value.strip().isdigit()
    ):
        limit = int(value.strip())
    else:
        raise validation_error("invalid_argument", "limit must be a positive integer", "limit")
    if limit < 1:
        raise validation_error("invalid_argument", "limit must be a positive integer", "limit")
    return min(limit, 100)


def list_tasks(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    status: Any = None,
    kind: Any = None,
    lane: Any = None,
    priority: Any = None,
    tag: Any = None,
    limit: Any = None,
    include_done: bool = False,
) -> TaskListResult:
    filters: list[str] = ["project_id = ?"]
    values: list[Any] = [project.project_id]
    if status is not None:
        filters.append("status = ?")
        values.append(validate_choice("status", status, STATUSES, "invalid_status"))
    elif not include_done:
        filters.append("status NOT IN ('done', 'cancelled')")
    if kind is not None:
        filters.append("kind = ?")
        values.append(validate_choice("kind", kind, KINDS, "invalid_kind"))
    if lane is not None:
        filters.append(f"{canonical_lane_sql('lane')} = ?")
        values.append(validate_lane(lane))
    if priority is not None:
        filters.append("priority = ?")
        values.append(validate_choice("priority", priority, PRIORITIES, "invalid_priority"))
    requested_tag = None
    if tag is not None:
        requested_tag = validate_text("tag", tag, required=True, limit=TEXT_LIMITS["tags"])

    row_limit = validate_limit(limit)
    query = f"""
        SELECT *
          FROM tasks
         WHERE {" AND ".join(filters)}
         ORDER BY
           CASE priority
             WHEN 'urgent' THEN 0
             WHEN 'high' THEN 1
             WHEN 'normal' THEN 2
             ELSE 3
           END,
           {canonical_lane_sql("lane")},
           lane_order IS NULL,
           lane_order,
           created_at,
           task_id
    """
    query_values = list(values)
    if requested_tag is None:
        query += " LIMIT ?"
        query_values.append(row_limit)
    stored_rows = fetch_stored_task_rows(connection, query, query_values)
    validate_current_stored_task_rows(
        connection,
        stored_rows,
        expected_project_id=project.project_id,
    )
    rows = [row_to_task(row) for row in stored_rows]
    if requested_tag is not None:
        rows = [row for row in rows if task_matches_tag(str(row["tags"]), requested_tag)]
    tasks = rows[:row_limit]
    return TaskListResult(tasks=tasks, count=len(tasks), limit=row_limit)


def validate_task_id(value: Any) -> str:
    return validate_text("task_id", value, required=True, limit=128)


def validate_completion_commit_hash(value: Any) -> str:
    return validate_text(
        "completion_commit_hash",
        value,
        limit=TEXT_LIMITS["completion_commit_hash"],
    )


def note_event_summary(note: str, recorded_markers: list[str] | None = None) -> str:
    prefix = "Note added: "
    suffix = ""
    if recorded_markers:
        suffix = f"; Recorded: {', '.join(recorded_markers)}"
    limit = TEXT_LIMITS["event_summary"]
    note_limit = limit - len(prefix) - len(suffix)
    if note_limit <= 0:
        return prefix + suffix[: limit - len(prefix)]
    if len(note) <= note_limit:
        return prefix + note + suffix
    if note_limit <= 3:
        return prefix + note[:note_limit] + suffix
    return prefix + note[: note_limit - 3] + "..." + suffix


def bounded_event_summary(prefix: str, value: str, suffix: str = "") -> str:
    limit = TEXT_LIMITS["event_summary"]
    available = limit - len(prefix)
    if available <= 0:
        return prefix[:limit]
    if len(value) > available:
        if available <= 3:
            return prefix + value[:available]
        return prefix + value[: available - 3] + "..."
    base = prefix + value
    remaining = limit - len(base)
    if len(suffix) <= remaining:
        return base + suffix
    if remaining <= 3:
        return base + suffix[:remaining]
    return base + suffix[: remaining - 3] + "..."


def bounded_transition_summary(
    prefix: str,
    value: str,
    *,
    recorded_markers: list[str],
    note: str | None,
) -> str:
    """Keep mandatory audit markers in bounded pause/resume event summaries."""
    if not recorded_markers:
        suffix = f"; Note: {note}" if note is not None else ""
        return bounded_event_summary(prefix, value, suffix)

    marker_suffix = f"; Recorded: {', '.join(recorded_markers)}"
    value_limit = TEXT_LIMITS["event_summary"] - len(prefix) - len(marker_suffix)
    if value_limit <= 0:
        return (prefix + marker_suffix)[: TEXT_LIMITS["event_summary"]]
    if len(value) > value_limit:
        value = value[: max(0, value_limit - 3)] + ("..." if value_limit >= 3 else "")
    summary = prefix + value + marker_suffix
    if note is None:
        return summary
    return bounded_event_summary(summary, "", f"; Note: {note}")


def task_edit_event_details(
    *,
    existing: dict[str, Any],
    updated: dict[str, Any],
    changed_fields: list[str],
    status_was_provided: bool,
    pause_changed: bool,
    review_tier_changed: bool,
    review_tier_change_reason: str | None,
    add_note: str | None,
    recorded_markers: list[str],
) -> tuple[str, str]:
    if review_tier_changed and review_tier_change_reason is not None:
        return (
            "review_tier_changed",
            bounded_transition_summary(
                (
                    f"Review tier changed: {existing['review_tier']} -> "
                    f"{updated['review_tier']}; Reason: "
                ),
                review_tier_change_reason,
                recorded_markers=recorded_markers,
                note=add_note,
            ),
        )
    if status_was_provided and updated["status"] == "paused":
        return (
            "task_updated",
            bounded_transition_summary(
                "Paused: ",
                str(updated["pause_reason"]),
                recorded_markers=recorded_markers,
                note=add_note,
            ),
        )
    if status_was_provided and existing["status"] == "paused":
        return (
            "task_updated",
            bounded_transition_summary(
                "Resumed from paused; Previous reason: ",
                str(existing["pause_reason"]),
                recorded_markers=recorded_markers,
                note=add_note,
            ),
        )
    if pause_changed:
        return (
            "task_updated",
            bounded_transition_summary(
                "Pause reason updated: ",
                str(updated["pause_reason"]),
                recorded_markers=recorded_markers,
                note=add_note,
            ),
        )
    if add_note is not None:
        return (
            (
                "note_added"
                if not changed_fields and not recorded_markers
                else "task_updated"
            ),
            note_event_summary(add_note, recorded_markers),
        )
    if recorded_markers:
        return "task_updated", f"Recorded: {', '.join(recorded_markers)}"
    return "task_updated", f"Updated fields: {', '.join(changed_fields)}"


def validate_task_edit_input(**edit_input: Any) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field, value in edit_input.items():
        if field == "title":
            normalized[field] = validate_text(field, value, required=True, limit=TEXT_LIMITS["title"])
        elif field == "description":
            normalized[field] = validate_text(field, value, limit=TEXT_LIMITS["description"])
        elif field == "kind":
            normalized[field] = validate_choice(field, value, KINDS, "invalid_kind")
        elif field == "lane":
            normalized[field] = validate_lane(value)
        elif field == "lane_order":
            normalized[field] = validate_lane_order(value)
        elif field == "priority":
            normalized[field] = validate_choice(field, value, PRIORITIES, "invalid_priority")
        elif field == "status":
            normalized[field] = validate_choice(field, value, STATUSES, "invalid_status")
        elif field == "blocked_reason":
            normalized[field] = validate_text(field, value)
        elif field == "pause_reason":
            normalized[field] = validate_text(field, value, limit=TEXT_LIMITS["pause_reason"])
        elif field == "review_tier":
            normalized[field] = validate_review_tier(value)
        elif field == "verification":
            normalized[field] = validate_text(
                field,
                value,
                limit=TASK_VERIFICATION_INPUT_LIMIT,
            )
        elif field == "tags":
            normalized[field] = validate_text(field, value, limit=TEXT_LIMITS["tags"])
        elif field == "add_note":
            normalized[field] = validate_text(field, value, required=True, limit=TEXT_LIMITS["add_note"])
        elif field == "reopen_reason":
            normalized[field] = validate_text(
                field,
                value,
                required=True,
                limit=TEXT_LIMITS["reopen_reason"],
            )
        elif field == "review_tier_change_reason":
            normalized[field] = validate_text(
                field,
                value,
                required=True,
                limit=TEXT_LIMITS["review_tier_change_reason"],
            )
        elif field == "completion_commit_hash":
            normalized[field] = validate_completion_commit_hash(value)
        elif field == "completion_evidence_kind":
            normalized[field] = validate_choice(
                field,
                value,
                WRITABLE_EVIDENCE_KINDS,
                "completion_evidence_conflict",
            )
        elif field == "completion_revision":
            normalized[field] = validate_text(
                field,
                value,
                limit=TEXT_LIMITS["completion_revision"],
            )
        elif field == "completion_evidence_reason":
            normalized[field] = validate_text(
                field,
                value,
                limit=TEXT_LIMITS["completion_evidence_reason"],
            )
        elif field in {
            "commit_not_required",
            "external_revision_approved",
            "verification_complete",
            "review_complete",
        }:
            if value is not True:
                raise validation_error("invalid_argument", f"{field} must be true when provided", field)
            normalized[field] = True
        else:
            raise validation_error("invalid_argument", f"{field} is not editable", field)
    explicit_evidence_fields = {
        "completion_evidence_kind",
        "completion_revision",
        "completion_evidence_reason",
        "external_revision_approved",
    }
    if "completion_commit_hash" in normalized and explicit_evidence_fields & normalized.keys():
        raise validation_error(
            "completion_evidence_conflict",
            "completion_commit_hash cannot be combined with explicit completion evidence options",
            "completion_commit_hash",
        )
    if normalized.get("commit_not_required") and (
        "completion_commit_hash" in normalized or explicit_evidence_fields & normalized.keys()
    ):
        raise validation_error(
            "completion_evidence_conflict",
            "commit_not_required cannot be combined with other completion evidence options",
            "commit_not_required",
        )
    if "completion_evidence_kind" not in normalized and (
        {"completion_revision", "completion_evidence_reason", "external_revision_approved"}
        & normalized.keys()
    ):
        raise validation_error(
            "completion_evidence_conflict",
            "completion evidence details require --completion-evidence-kind",
            "completion_evidence_kind",
        )
    if not normalized:
        raise validation_error("invalid_argument", "at least one editable field or add_note is required")
    if normalized.get("status") == "blocked" and not str(normalized.get("blocked_reason", "")).strip():
        raise validation_error(
            "blocked_reason_required",
            "blocked_reason is required when status is blocked",
            "blocked_reason",
        )
    return normalized


def build_completion_request(
    task_id: Any,
    **completion_input: Any,
) -> CompletionRequest:
    """Validate the completion-only caller surface through existing edit rules."""
    normalized_task_id = validate_task_id(task_id)
    normalized = validate_task_edit_input(status="done", **completion_input)
    normalized.pop("status")

    evidence_kind = normalized.pop("completion_evidence_kind", None)
    completion_revision = str(normalized.pop("completion_revision", ""))
    completion_reason = str(
        normalized.pop("completion_evidence_reason", "")
    )
    external_approved = bool(
        normalized.pop("external_revision_approved", False)
    )
    if bool(normalized.pop("commit_not_required", False)):
        evidence_kind = "commit_not_required"
    completion_commit_hash = normalized.pop("completion_commit_hash", None)
    if completion_commit_hash is not None:
        evidence_kind = "git_commit"
        completion_revision = str(completion_commit_hash)

    request = CompletionRequest(
        task_id=normalized_task_id,
        verification_complete=bool(
            normalized.pop("verification_complete", False)
        ),
        review_complete=bool(normalized.pop("review_complete", False)),
        completion_evidence_kind=(
            str(evidence_kind) if evidence_kind is not None else None
        ),
        completion_revision=completion_revision,
        completion_evidence_reason=completion_reason,
        external_revision_approved=external_approved,
    )
    if normalized:
        raise validation_error(
            "invalid_argument",
            "task complete accepts only completion evidence and confirmations",
        )
    return request


def suggested_next_action(task: dict[str, Any]) -> str:
    status = task["status"]
    if status == "ready":
        return "Start work, then update the task state when work begins or the status changes."
    if status == "in_progress":
        return "Continue work, or update the task status when the execution unit changes."
    if status == "blocked":
        return "Resolve the blocker, or choose another ready task."
    if status == "review_pending":
        return "Complete the required review gate, then update the task status."
    if status == "paused":
        return "Review the pause reason, then resume the task to in_progress when safe."
    if status == "done":
        return "No next action; the task is done."
    if status == "cancelled":
        return "No next action; the task is cancelled."
    return "Inspect the task status before choosing the next action."


def _completion_history_latest_summary(
    history: CompletionHistory,
) -> dict[str, Any] | None:
    if not history.cycles:
        return None
    cycle = history.cycles[0]
    return {
        "saved_cycle_ordinal": cycle.saved_cycle_ordinal,
        "origin": cycle.origin,
        "completeness": cycle.completeness,
        "completed_at": cycle.completed_at,
        "completion_evidence_kind": cycle.completion_evidence_kind,
        "review_target_kind": cycle.review_target_kind,
        "review_target_generation": cycle.review_target_generation,
        "review_basis_kind": cycle.gate_basis.kind,
    }


def show_task(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    event_limit: int = 10,
    runner_selection: VerificationRunnerGateSelection | None = None,
) -> TaskShowResult:
    normalized_task_id = validate_task_id(task_id)
    source_schema_version = current_schema_version(connection)
    task_row = fetch_validated_current_task_row(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
    )
    if task_row is None:
        raise TaskRepositoryError("not_found", "task was not found")
    task = row_to_show_task(task_row)
    internal_task = row_to_internal_task(task_row)
    event_rows = connection.execute(
        """
        SELECT *
          FROM task_events
         WHERE project_id = ?
           AND task_id = ?
         ORDER BY created_at DESC, rowid DESC
         LIMIT ?
        """,
        (project.project_id, normalized_task_id, event_limit),
    ).fetchall()
    from task_governance_tool.contracts import read_current_contract
    from task_governance_tool.checkpoints import read_latest_checkpoint
    from task_governance_tool.handoffs import handoff_summary_for_task
    from task_governance_tool.reviews import read_review_evidence

    review_evidence = read_review_evidence(
        connection,
        project.project_id,
        normalized_task_id,
        validated_task=task_row,
        source_schema_version=source_schema_version,
    )
    handoff_summary = handoff_summary_for_task(
        connection,
        project.project_id,
        normalized_task_id,
    )
    contract = read_current_contract(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        current_revision=task_row["current_contract_revision"],
    )
    latest_checkpoint = read_latest_checkpoint(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
    )
    raw_completion_history = read_completion_history(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
    )
    from task_governance_tool.verification_receipts import (
        read_verification_evidence,
    )

    verification_evidence = read_verification_evidence(
        connection,
        task=internal_task,
        completion_cycle=(
            raw_completion_history.cycles[0]
            if raw_completion_history.cycles
            else None
        ),
        runner_selection=runner_selection,
    )
    return TaskShowResult(
        task=task,
        events=[row_to_event(row) for row in event_rows],
        suggested_next_action=suggested_next_action(task),
        review_evidence=review_evidence,
        handoff_summary=handoff_summary,
        contract=contract,
        latest_checkpoint=latest_checkpoint,
        completion_history=format_completion_history(raw_completion_history),
        completion_history_latest_summary=_completion_history_latest_summary(
            raw_completion_history
        ),
        verification_evidence=verification_evidence,
    )


def list_tasks_for_viewer(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    event_limit: int = 10,
    source_schema_version: int = SCHEMA_VERSION,
) -> ViewerTaskListResult:
    """Return a Viewer projection with ordinary complete Task validation."""

    return _list_tasks_for_viewer(
        connection,
        project,
        event_limit=event_limit,
        source_schema_version=source_schema_version,
        validated_task_batch=None,
    )


def _list_tasks_for_validated_viewer_snapshot(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    validated_task_batch: _ValidatedViewerTaskBatch,
    *,
    event_limit: int = 10,
    source_schema_version: int = SCHEMA_VERSION,
) -> ViewerTaskListResult:
    """Return a Viewer projection from one exact same-transaction v18 proof."""

    return _list_tasks_for_viewer(
        connection,
        project,
        event_limit=event_limit,
        source_schema_version=source_schema_version,
        validated_task_batch=validated_task_batch,
    )


def _list_tasks_for_viewer(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    event_limit: int,
    source_schema_version: int,
    validated_task_batch: _ValidatedViewerTaskBatch | None,
) -> ViewerTaskListResult:
    if not 1 <= event_limit <= 10:
        raise TaskRepositoryError(
            "internal_error",
            "viewer event limit must be between 1 and 10",
        )

    task_rows = fetch_stored_task_rows(
        connection,
        """
        SELECT *
          FROM tasks
         ORDER BY
           CASE priority
             WHEN 'urgent' THEN 0
             WHEN 'high' THEN 1
             WHEN 'normal' THEN 2
             ELSE 3
           END,
           lane,
           lane_order IS NULL,
           lane_order,
           created_at,
           task_id
        """,
    )
    if validated_task_batch is None:
        validate_stored_task_rows(
            task_rows,
            connection=connection,
            source_schema_version=source_schema_version,
            expected_project_id=project.project_id,
        )
    else:
        _consume_validated_viewer_task_batch(
            connection,
            validated_task_batch,
            project_id=project.project_id,
            source_schema_version=source_schema_version,
            task_rows=task_rows,
        )

    from task_governance_tool.reviews import read_review_evidence

    tasks: list[dict[str, Any]] = []
    event_count = 0
    for task_row in task_rows:
        task = row_to_viewer_task(task_row)
        event_rows = connection.execute(
            """
            SELECT *
              FROM task_events
             WHERE project_id = ?
               AND task_id = ?
             ORDER BY created_at DESC, rowid DESC
             LIMIT ?
            """,
            (project.project_id, task["task_id"], event_limit),
        ).fetchall()
        events = [row_to_event(row) for row in event_rows]
        task["events"] = events
        task["review_evidence"] = read_review_evidence(
            connection,
            project.project_id,
            str(task["task_id"]),
            validated_task=task_row,
            source_schema_version=source_schema_version,
        )
        for receipt in task["review_evidence"]["recent_receipts"]:
            receipt.pop("review_provenance", None)
        tasks.append(task)
        event_count += len(events)

    return ViewerTaskListResult(tasks=tasks, event_count=event_count)


CURRENT_STATUSES = ("in_progress", "review_pending", "paused", "blocked")


def validate_current_status_filter(value: Any = None) -> str | None:
    if value is None:
        return None
    return validate_choice("status", value, CURRENT_STATUSES, "invalid_status")


def current_suggested_next_action(task: dict[str, Any]) -> str:
    status = task["status"]
    if status == "in_progress":
        return "continue the task and inspect its latest event"
    if status == "review_pending":
        return "complete the required review gate"
    if status == "paused":
        reason = str(task.get("pause_reason", "")).strip()
        suffix = f": {reason}" if reason else ""
        return f"review the pause reason and resume explicitly when safe{suffix}"
    reason = str(task.get("blocked_reason", "")).strip()
    suffix = f": {reason}" if reason else ""
    return f"resolve or reassess the blocker{suffix}"


def list_current_tasks(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    limit: Any = None,
    status: Any = None,
) -> CurrentTaskResult:
    row_limit = validate_limit(limit, default=20)
    status_filter = validate_current_status_filter(status)
    if status_filter is None:
        status_predicate = (
            "task.status IN ('in_progress', 'review_pending', 'paused', 'blocked')"
        )
        parameters: tuple[Any, ...] = (project.project_id, row_limit)
        result_statuses = CURRENT_STATUSES
    else:
        status_predicate = "task.status = ?"
        parameters = (project.project_id, status_filter, row_limit)
        result_statuses = (status_filter,)
    rows = fetch_stored_task_rows(
        connection,
        f"""
        SELECT
          task.*,
          COUNT(*) OVER() AS total_matching,
          latest.task_event_id AS latest_event_id,
          latest.event_type AS latest_event_type,
          latest.summary AS latest_event_summary,
          latest.created_at AS latest_event_created_at
          FROM tasks AS task
          LEFT JOIN task_events AS latest
            ON latest.rowid = (
              SELECT event.rowid
                FROM task_events AS event
               WHERE event.project_id = task.project_id
                 AND event.task_id = task.task_id
               ORDER BY event.created_at DESC, event.rowid DESC
               LIMIT 1
            )
         WHERE task.project_id = ?
           AND {status_predicate}
         ORDER BY
           CASE task.status
             WHEN 'in_progress' THEN 0
             WHEN 'review_pending' THEN 1
             WHEN 'paused' THEN 2
             ELSE 3
           END,
           CASE task.priority
             WHEN 'urgent' THEN 0
             WHEN 'high' THEN 1
             WHEN 'normal' THEN 2
             ELSE 3
           END,
           task.updated_at DESC,
           task.task_id
         LIMIT ?
        """,
        parameters,
    )
    validate_current_stored_task_rows(
        connection,
        rows,
        expected_project_id=project.project_id,
    )
    tasks = []
    from task_governance_tool.checkpoints import read_latest_checkpoint

    for row in rows:
        task = row_to_task(row)
        if row["latest_event_id"] is None:
            task["latest_event"] = {}
        else:
            task["latest_event"] = row_to_event({
                "task_event_id": row["latest_event_id"],
                "task_id": task["task_id"],
                "project_id": task["project_id"],
                "event_type": row["latest_event_type"],
                "summary": row["latest_event_summary"],
                "created_at": row["latest_event_created_at"],
            })
        task["latest_checkpoint"] = read_latest_checkpoint(
            connection,
            project_id=project.project_id,
            task_id=task["task_id"],
        )
        task["suggested_next_action"] = current_suggested_next_action(task)
        tasks.append(task)
    return CurrentTaskResult(
        tasks=tasks,
        count=len(tasks),
        total_matching=(int(rows[0]["total_matching"]) if rows else 0),
        limit=row_limit,
        statuses=result_statuses,
    )


def read_task(connection: sqlite3.Connection, project_id: str, task_id: str) -> dict[str, Any] | None:
    row = fetch_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if row is None:
        return None
    return row_to_task(row)


def read_internal_task(connection: sqlite3.Connection, project_id: str, task_id: str) -> dict[str, Any] | None:
    row = fetch_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if row is None:
        return None
    return row_to_internal_task(row)


def _completion_semantic_token(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(serialized).hexdigest()


def capture_completion_basis(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    runner_selection: VerificationRunnerGateSelection | None = None,
) -> CompletionBasis:
    """Capture only task-local facts that can change completion readiness."""
    normalized_task_id = validate_task_id(task_id)
    task = read_internal_task(
        connection,
        project.project_id,
        normalized_task_id,
    )
    if task is None:
        raise TaskRepositoryError("not_found", "task was not found")

    predecessor_incomplete = False
    lane_order_conflict = False
    if task["kind"] == "sequential":
        predicate_row = connection.execute(
            f"""
            SELECT
              {incomplete_predecessor_sql("task")} AS predecessor_incomplete,
              {duplicate_lane_order_sql("task")} AS lane_order_conflict
              FROM tasks AS task
             WHERE task.project_id = ?
               AND task.task_id = ?
            """,
            (project.project_id, normalized_task_id),
        ).fetchone()
        predecessor_incomplete = bool(
            predicate_row is not None
            and int(predicate_row["predecessor_incomplete"])
        )
        lane_order_conflict = bool(
            predicate_row is not None
            and int(predicate_row["lane_order_conflict"])
        )
    contract_row = connection.execute(
        """
        SELECT *
          FROM task_contract_revisions
         WHERE project_id = ?
           AND task_id = ?
           AND revision = ?
        """,
        (
            project.project_id,
            normalized_task_id,
            int(task["current_contract_revision"]),
        ),
    ).fetchone()
    contract = dict(contract_row) if contract_row is not None else None

    from task_governance_tool.reviews import (
        ReviewEvidenceError,
        read_review_evidence,
    )

    review_evidence: dict[str, Any] | None
    review_error: tuple[str, str, str | None] | None = None
    try:
        review_evidence = read_review_evidence(
            connection,
            project.project_id,
            normalized_task_id,
            review_tier=int(task["review_tier"]),
            validated_task=task,
        )
    except ReviewEvidenceError as exc:
        review_evidence = None
        review_error = (exc.code, exc.message, exc.field)

    from task_governance_tool.verification_receipts import (
        current_verification_gate,
    )

    verification_gate = current_verification_gate(
        connection,
        task=task,
        runner_selection=runner_selection,
    )

    semantic_token = _completion_semantic_token(
        {
            "task": task,
            "predecessor_incomplete": predecessor_incomplete,
            "lane_order_conflict": lane_order_conflict,
            "contract": contract,
            "review_evidence": review_evidence,
            "review_error": review_error,
            "verification_gate": verification_gate.to_public(),
            "runner_selection": (
                runner_selection.semantic_value()
                if runner_selection is not None
                else None
            ),
        }
    )
    return CompletionBasis(
        task=task,
        review_evidence=review_evidence,
        verification_gate=verification_gate,
        runner_selection=runner_selection,
        review_error=review_error,
        predecessor_incomplete=predecessor_incomplete,
        lane_order_conflict=lane_order_conflict,
        semantic_token=semantic_token,
    )


def validate_completion_state_basis(basis: CompletionBasis) -> None:
    """Reject task-state and sequential-lane blockers before Git observation."""
    reject_done_task_write(basis.task)
    if basis.task["status"] == "paused":
        raise validation_error(
            "invalid_status_transition",
            "paused tasks may resume only to in_progress",
            "status",
        )
    if basis.lane_order_conflict:
        raise TaskRepositoryError(
            "invalid_argument",
            "task conflicts with an existing canonical sequential lane order",
        )
    if basis.predecessor_incomplete:
        raise TaskRepositoryError(
            "sequential_predecessor_incomplete",
            "sequential lane contains active, review-pending, or done work with an incomplete predecessor",
        )


def validate_completion_basic_prerequisites(
    basis: CompletionBasis,
    request: CompletionRequest,
    *,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
) -> None:
    """Preserve Task-state and parsed-input precedence before resolution."""

    if request.task_id != str(basis.task["task_id"]):
        raise TaskRepositoryError("not_found", "task was not found")
    validate_completion_state_basis(basis)
    if input_error is not None:
        raise input_error


def validate_completion_selector_prerequisites(
    basis: CompletionBasis,
    request: CompletionRequest,
    *,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
) -> None:
    """Preserve established completion checks before package inspection."""

    validate_completion_basic_prerequisites(
        basis,
        request,
        input_error=input_error,
    )
    if basis.task.get("review_target_runner_basis_version", 0) != 2:
        return
    if not request.verification_complete:
        raise validation_error(
            "verification_required",
            "task edit --status done requires --verification-complete",
            "verification_complete",
        )
    if not request.review_complete:
        raise validation_error(
            "review_required",
            "task edit --status done requires --review-complete",
            "review_complete",
        )
    if (
        int(basis.task["review_target_generation"]) <= 0
        or not str(basis.task["review_target_kind"])
        or not str(basis.task["review_target_value"])
    ):
        raise validation_error(
            "review_target_required",
            "task completion requires a current structured review target",
            "review_target_kind",
        )
    if int(basis.task.get("review_target_capture_version", 0)) == 0:
        raise validation_error(
            "evidence_basis_stale",
            "current evidence basis must be captured again",
        )


def validate_completion_preselector_database_basis(
    basis: CompletionBasis,
    proposed_task: dict[str, Any],
    *,
    verification_complete: bool,
    review_complete: bool,
) -> None:
    """Run existing completion checks that must precede Runner selection."""
    validate_completion_state_basis(basis)
    validate_done_transition_inputs(
        proposed_task,
        status_was_provided=True,
        verification_complete=verification_complete,
        review_complete=review_complete,
    )
    if (
        int(basis.task["review_target_generation"]) <= 0
        or not str(basis.task["review_target_kind"])
        or not str(basis.task["review_target_value"])
    ):
        raise validation_error(
            "review_target_required",
            "task completion requires a current structured review target",
            "review_target_kind",
        )
    if int(basis.task.get("review_target_capture_version", 0)) == 0:
        raise validation_error(
            "evidence_basis_stale",
            "current evidence basis must be captured again",
        )


def validate_completion_database_basis(
    basis: CompletionBasis,
    proposed_task: dict[str, Any],
    *,
    verification_complete: bool,
    review_complete: bool,
) -> None:
    """Run the shared fail-fast database-only completion preflight."""
    validate_completion_preselector_database_basis(
        basis,
        proposed_task,
        verification_complete=verification_complete,
        review_complete=review_complete,
    )
    from task_governance_tool.reviews import first_review_gate_error

    if basis.verification_gate.blocking_code == "evidence_basis_stale":
        raise validation_error(
            "evidence_basis_stale",
            "current evidence basis must be captured again",
        )
    if basis.verification_gate.blocking_code == "verification_receipt_required":
        raise validation_error(
            "verification_receipt_required",
            "current verification evidence is required",
        )
    if basis.verification_gate.blocking_code == "verification_receipt_blocking":
        raise validation_error(
            "verification_receipt_blocking",
            "current verification evidence does not satisfy the required result and coverage",
        )
    if not basis.verification_gate.satisfied:
        raise TaskRepositoryError(
            "invalid_verification_evidence",
            "stored verification evidence is inconsistent",
        )

    if basis.review_error is not None:
        code, message, field = basis.review_error
        raise validation_error(code, message, field)
    if basis.review_evidence is None:
        raise TaskRepositoryError(
            "internal_error",
            "completion review evidence was not available",
        )

    gate_error = first_review_gate_error(basis.review_evidence)
    if gate_error is not None:
        raise validation_error(
            gate_error.code,
            gate_error.message,
            gate_error.field,
        )


def proposed_completion_task(
    task: dict[str, Any],
    resolution: CompletionResolution,
) -> dict[str, Any]:
    proposed = dict(task)
    proposed.update(resolution.to_task_values())
    proposed.update(
        {
            "status": "done",
            "blocked_reason": "",
            "pause_reason": "",
        }
    )
    return proposed


def prepare_completion_plan(
    basis: CompletionBasis,
    project: ProjectIdentity,
    request: CompletionRequest,
    *,
    defer_gate_validation: bool = False,
) -> CompletionPlan:
    """Resolve and validate one completion request with no open DB transaction."""
    if request.task_id != str(basis.task["task_id"]):
        raise TaskRepositoryError(
            "not_found",
            "task was not found",
        )
    validate_completion_state_basis(basis)
    try:
        resolution = resolve_completion_request(
            repo=project.canonical_repo,
            request=request,
            existing_task=basis.task,
        )
    except CompletionEvidenceError as exc:
        raise validation_error(exc.code, exc.message, exc.field) from exc
    proposed = proposed_completion_task(basis.task, resolution)
    if defer_gate_validation:
        validate_completion_preselector_database_basis(
            basis,
            proposed,
            verification_complete=request.verification_complete,
            review_complete=request.review_complete,
        )
    else:
        validate_completion_database_basis(
            basis,
            proposed,
            verification_complete=request.verification_complete,
            review_complete=request.review_complete,
        )
    revalidate_done_git_evidence(
        proposed,
        repo=project.canonical_repo,
        status_was_provided=True,
    )
    return CompletionPlan(
        request=request,
        basis=basis,
        resolution=resolution,
    )


def validate_completion_plan_basis(
    plan: CompletionPlan,
    basis: CompletionBasis,
    *,
    stale_code: str | None = None,
) -> dict[str, Any]:
    """Revalidate a cached outside-Git observation against current DB facts."""
    if basis.semantic_token != plan.basis.semantic_token:
        if stale_code is not None:
            raise validation_error(
                stale_code,
                "completion readiness changed during validation; retry the check",
            )
        reject_concurrent_edit_base_change(
            plan.basis.task,
            basis.task,
            completing=True,
        )
    proposed = proposed_completion_task(basis.task, plan.resolution)
    validate_completion_database_basis(
        basis,
        proposed,
        verification_complete=plan.request.verification_complete,
        review_complete=plan.request.review_complete,
    )
    return proposed


def reject_done_task_write(task: dict[str, Any]) -> None:
    if task["status"] == "done":
        raise TaskRepositoryError(
            "done_task_requires_reopen",
            "done task writes require an explicit reopen",
        )


def update_task_row(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    project_id: str,
    values: dict[str, Any],
) -> None:
    assignments = ", ".join(f"{field} = :{field}" for field in values)
    parameters = dict(values)
    parameters["task_id"] = task_id
    parameters["project_id"] = project_id
    connection.execute(
        f"""
        UPDATE tasks
           SET {assignments}
         WHERE project_id = :project_id
           AND task_id = :task_id
        """,
        parameters,
    )


def lock_and_reread_edit_owner(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: str,
    *,
    database_target: DatabaseTarget | None = None,
) -> dict[str, Any]:
    begin_task_write(connection, database_target)
    task = read_internal_task(connection, project.project_id, task_id)
    if task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    return task


def reject_concurrent_edit_base_change(
    existing: dict[str, Any],
    locked: dict[str, Any],
    *,
    completing: bool = False,
) -> None:
    reject_done_task_write(locked)
    if completing and any(
        locked[field] != existing[field]
        for field in (
            "review_target_kind",
            "review_target_value",
            "review_target_base_revision",
            "review_target_generation",
        )
    ):
        raise validation_error(
            "review_target_mismatch",
            "review target changed after completion preflight",
            "review_target_value",
        )
    ignored_fields = {"updated_at"}
    if any(
        locked[field] != existing[field]
        for field in existing
        if field not in ignored_fields
    ):
        raise TaskRepositoryError(
            "invalid_argument",
            "task changed concurrently; retry the edit against current state",
        )


def validate_proposed_done_ordering(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    proposed_done: Mapping[str, Any],
    lane_metadata_changed: bool,
    advanced_transition: bool,
) -> None:
    """Preserve the established sequential checks before gate selection."""

    if (
        proposed_done["kind"] == "sequential"
        and (lane_metadata_changed or advanced_transition)
        and canonical_lane_order_conflict(
            connection,
            project_id=project_id,
            task_id=task_id,
            lane=str(proposed_done["lane"]),
            lane_order=int(proposed_done["lane_order"]),
        )
    ):
        raise TaskRepositoryError(
            "invalid_argument",
            "task conflicts with an existing canonical sequential lane order",
        )
    if proposed_done["kind"] != "sequential":
        return
    predecessor = connection.execute(
        f"""
        SELECT 1
          FROM tasks AS earlier
         WHERE earlier.project_id = ?
           AND earlier.task_id != ?
           AND earlier.kind = 'sequential'
           AND {canonical_lane_sql("earlier.lane")} = ?
           AND earlier.lane_order < ?
           AND earlier.status NOT IN ('done', 'cancelled')
         LIMIT 1
        """,
        (
            project_id,
            task_id,
            canonical_lane(proposed_done["lane"]),
            int(proposed_done["lane_order"]),
        ),
    ).fetchone()
    if predecessor is not None:
        raise TaskRepositoryError(
            "sequential_predecessor_incomplete",
            "sequential lane contains active, review-pending, or done work with an incomplete predecessor",
        )


def validate_done_transition_inputs(
    task: dict[str, Any],
    *,
    status_was_provided: bool,
    verification_complete: bool,
    review_complete: bool,
) -> None:
    if not status_was_provided or task["status"] != "done":
        return
    if not verification_complete:
        raise validation_error(
            "verification_required",
            "task edit --status done requires --verification-complete",
            "verification_complete",
        )
    if not review_complete:
        raise validation_error(
            "review_required",
            "task edit --status done requires --review-complete",
            "review_complete",
        )
    try:
        validate_evidence_matrix(task, allow_legacy=False)
    except CompletionEvidenceError as exc:
        raise validation_error(exc.code, exc.message, exc.field) from exc
    if task["completion_evidence_kind"] == "none":
        raise validation_error(
            "commit_required",
            "task edit --status done requires explicit completion evidence",
            "completion_evidence_kind",
        )


def revalidate_done_git_evidence(
    task: dict[str, Any],
    *,
    repo: Path,
    status_was_provided: bool,
) -> None:
    if not status_was_provided or task["status"] != "done":
        return
    completion_kind = str(task["completion_evidence_kind"])
    completion_revision = str(task["completion_evidence_revision"])
    target_kind = str(task["review_target_kind"])
    target_value = str(task["review_target_value"])
    target_base_revision = str(task["review_target_base_revision"])

    if task["completion_evidence_kind"] == "git_commit":
        try:
            resolved_completion = resolve_git_commit(
                repo,
                completion_revision,
            )
        except CompletionEvidenceError as exc:
            raise validation_error(exc.code, exc.message, exc.field) from exc
        if resolved_completion != completion_revision:
            raise validation_error(
                "git_commit_not_found_or_ambiguous",
                "stored Git completion revision no longer resolves to the recorded commit",
                "completion_revision",
            )

    if target_kind == "git_commit":
        try:
            resolved_target = resolve_git_commit(repo, target_value)
        except CompletionEvidenceError as exc:
            raise validation_error(exc.code, exc.message, "review_target_value") from exc
        if resolved_target != target_value:
            raise validation_error(
                "git_commit_not_found_or_ambiguous",
                "stored Git review target no longer resolves to the recorded commit",
                "review_target_value",
            )
        if completion_kind != "git_commit" or completion_revision != target_value:
            raise validation_error(
                "review_target_mismatch",
                "Git completion evidence must equal the current Git commit review target",
                "review_target_value",
            )
    elif target_kind == "git_snapshot":
        if completion_kind != "git_commit":
            raise validation_error(
                "review_target_mismatch",
                "a Git snapshot review target requires Git commit completion evidence",
                "completion_evidence_kind",
            )
        try:
            resolved_base = resolve_git_commit(repo, target_base_revision)
        except CompletionEvidenceError as exc:
            raise validation_error(
                exc.code,
                exc.message,
                "review_target_base_revision",
            ) from exc
        if resolved_base != target_base_revision:
            raise validation_error(
                "git_commit_not_found_or_ambiguous",
                "stored Git snapshot base no longer resolves to the recorded commit",
                "review_target_base_revision",
            )
        try:
            verify_git_snapshot_commit(
                repo,
                completion_revision,
                expected_base_revision=target_base_revision,
                expected_fingerprint=target_value,
            )
        except GitSnapshotError as exc:
            raise validation_error(exc.code, exc.message, exc.field) from exc
    elif target_kind == "external_revision":
        if (
            completion_kind != "external_revision"
            or completion_revision != target_value
        ):
            raise validation_error(
                "review_target_mismatch",
                "external completion evidence must equal the current external review target",
                "review_target_value",
            )
    elif target_kind == "diff_fingerprint":
        if completion_kind != "commit_not_required":
            raise validation_error(
                "review_target_mismatch",
                "commit-not-required completion requires a diff_fingerprint review target",
                "review_target_kind",
            )
    elif target_kind:
        raise validation_error(
            "review_target_mismatch",
            "completion evidence does not match the current review target",
            "review_target_kind",
        )


def enforce_done_review_gate(
    connection: sqlite3.Connection,
    task: dict[str, Any],
    *,
    status_was_provided: bool,
) -> None:
    if not status_was_provided or task["status"] != "done":
        return
    from task_governance_tool.reviews import ReviewEvidenceError, enforce_review_gate

    try:
        enforce_review_gate(
            connection,
            project_id=str(task["project_id"]),
            task_id=str(task["task_id"]),
            review_tier=int(task["review_tier"]),
        )
    except ReviewEvidenceError as exc:
        raise validation_error(exc.code, exc.message, exc.field) from exc


def reopen_done_task(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    existing: dict[str, Any],
    *,
    reopen_reason: str,
    effort_profile: Any | None = None,
    effort_preflight: Any | None = None,
    database_target: DatabaseTarget | None = None,
) -> EditTaskResult:
    locked_existing = lock_and_reread_edit_owner(
        connection,
        project,
        str(existing["task_id"]),
        database_target=database_target,
    )
    if locked_existing["status"] != "done":
        raise TaskRepositoryError(
            "invalid_argument",
            "task changed concurrently; retry the reopen against current state",
        )
    existing = locked_existing
    (
        history_coverage,
        latest_cycle,
        projection_matches,
        latest_cycle_has_reopen_link,
    ) = match_current_done_completion_cycle_locked(
        connection,
        project_id=project.project_id,
        task_id=str(existing["task_id"]),
    )
    if latest_cycle is not None and (
        not projection_matches or latest_cycle_has_reopen_link
    ):
        raise completion_history_inconsistent()
    if latest_cycle is None and history_coverage != "legacy_unknown":
        raise completion_history_inconsistent()
    generation = validate_sqlite_int64(
        int(existing["review_target_generation"]) + 1,
        field="review_target_generation",
    )
    now = utc_now()
    reopened = dict(existing)
    reopened.update(
        {
            "status": "in_progress",
            "completed_at": None,
            "blocked_reason": "",
            "pause_reason": "",
            "completion_evidence_kind": "none",
            "completion_evidence_revision": "",
            "completion_evidence_reason": "",
            "external_revision_approved": 0,
            "completion_commit_required": 1,
            "completion_commit_hash": "",
            "review_target_kind": "",
            "review_target_value": "",
            "review_target_generation": generation,
            "review_target_base_revision": "",
            "review_target_capture_version": 0,
            "review_target_authority_snapshot_id": None,
            "review_target_acceptance_criterion_id": None,
            "review_target_verification_criterion_id": None,
            "review_target_artifact_manifest_id": None,
            "review_target_runner_basis_version": 0,
        }
    )
    reset_fields = (
        "status",
        "completed_at",
        "blocked_reason",
        "pause_reason",
        "completion_evidence_kind",
        "completion_evidence_revision",
        "completion_evidence_reason",
        "external_revision_approved",
        "completion_commit_required",
        "completion_commit_hash",
        "review_target_kind",
        "review_target_value",
        "review_target_generation",
        "review_target_base_revision",
    )
    persisted_changed_fields = [
        field for field in reset_fields if reopened[field] != existing[field]
    ]
    update_values = {
        field: reopened[field]
        for field in persisted_changed_fields
    }
    update_values.update(
        {
            "review_target_capture_version": 0,
            "review_target_authority_snapshot_id": None,
            "review_target_acceptance_criterion_id": None,
            "review_target_verification_criterion_id": None,
            "review_target_artifact_manifest_id": None,
            "review_target_runner_basis_version": 0,
        }
    )
    update_values["updated_at"] = now
    affected_lanes = (
        {str(existing["lane"])} if existing["kind"] == "sequential" else set()
    )
    previous_kind = str(existing["completion_evidence_kind"])
    raw_previous_revision = str(existing["completion_evidence_revision"])
    try:
        previous_revision = validate_text(
            "previous_completion_revision",
            raw_previous_revision,
            limit=TEXT_LIMITS["completion_revision"],
        ) or "(empty)"
    except TaskValidationError:
        previous_revision = (
            "sha256:"
            + hashlib.sha256(raw_previous_revision.encode("utf-8")).hexdigest()
            + " (redacted historical revision)"
        )
    summary = bounded_transition_summary(
        "Reopened: ",
        reopen_reason,
        recorded_markers=[
            f"previous completion evidence {previous_kind} {previous_revision}"
        ],
        note=None,
    )

    savepoint = f"taskgov_reopen_{secrets.token_hex(4)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        if latest_cycle is None:
            latest_cycle = insert_completion_cycle_locked(
                connection,
                project_id=project.project_id,
                task_id=str(existing["task_id"]),
                recorded_at=now,
            )
        update_task_row(
            connection,
            task_id=str(existing["task_id"]),
            project_id=project.project_id,
            values=update_values,
        )
        invalid_task_id = first_out_of_order_advanced_task(
            connection,
            project_id=project.project_id,
            lanes=affected_lanes,
        )
        if invalid_task_id is not None:
            raise TaskRepositoryError(
                "sequential_predecessor_incomplete",
                "sequential lane contains active, review-pending, or done work with an incomplete predecessor",
            )
        event = create_task_event(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
            event_type="task_reopened",
            summary=summary,
            created_at=now,
            completion_cycle_id=latest_cycle.completion_cycle_id,
        )
        task = read_task(connection, project.project_id, str(existing["task_id"]))
        if task is None:
            raise TaskRepositoryError(
                "internal_error",
                "task was not readable after reopen",
            )
        from task_governance_tool.effort import record_task_transition

        record_task_transition(
            connection,
            project,
            task_id=str(existing["task_id"]),
            previous_status=str(existing["status"]),
            current_status=str(task["status"]),
            profile=effort_profile,
            occurred_at=now,
            preflight=effort_preflight,
        )
    except sqlite3.IntegrityError as exc:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise TaskRepositoryError(
            "invalid_argument",
            "task violates database constraints while reopening",
        ) from exc
    except Exception:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
    changed_fields = [
        field
        for field in persisted_changed_fields
        if field != "review_target_base_revision"
    ]
    return EditTaskResult(task=task, changed_fields=changed_fields, event=event)


def edit_task(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    effort_profile: Any | None = None,
    database_target: DatabaseTarget | None = None,
    completion_plan: CompletionPlan | None = None,
    runner_selector: RunnerSelectionProvider | None = None,
    basis_precommit_validator: TaskEditBasisPrecommitValidator | None = None,
    **edit_input: Any,
) -> EditTaskResult:
    from task_governance_tool.effort import (
        prepare_task_transition,
        record_task_transition,
    )

    normalized_task_id = validate_task_id(task_id)
    existing = read_internal_task(connection, project.project_id, normalized_task_id)
    if existing is None:
        raise TaskRepositoryError("not_found", "task was not found")
    if existing["status"] == "done":
        raw_status = edit_input.get("status")
        exact_reopen_candidate = (
            set(edit_input) == {"status", "reopen_reason"}
            and isinstance(raw_status, str)
            and raw_status.strip() == "in_progress"
        )
        if exact_reopen_candidate:
            reopen_input = validate_task_edit_input(**edit_input)
            effort_preflight = prepare_task_transition(
                connection,
                project,
                task_id=normalized_task_id,
                previous_status=str(existing["status"]),
                current_status="in_progress",
                profile=effort_profile,
            )
            reopened = reopen_done_task(
                connection,
                project,
                existing,
                reopen_reason=str(reopen_input["reopen_reason"]),
                effort_profile=effort_profile,
                effort_preflight=effort_preflight,
                database_target=database_target,
            )
            return reopened
        reject_done_task_write(existing)

    from task_governance_tool.contracts import edit_contract, split_contract_input

    edit_input, contract_input = split_contract_input(edit_input)
    if completion_plan is not None:
        if contract_input or str(edit_input.get("status", "")).strip() != "done":
            raise TaskRepositoryError(
                "internal_error",
                "completion plan was supplied for a non-completion edit",
            )
        request_input = dict(edit_input)
        request_input.pop("status", None)
        supplied_request = build_completion_request(
            normalized_task_id,
            **request_input,
        )
        if supplied_request != completion_plan.request:
            raise TaskRepositoryError(
                "internal_error",
                "completion plan did not match the completion request",
            )
        reject_concurrent_edit_base_change(
            completion_plan.basis.task,
            existing,
            completing=True,
        )
    if contract_input:
        potential_contract_status = str(existing["status"])
        if (
            int(existing["current_contract_revision"]) == 0
            and existing["status"] in {"ready", "blocked"}
            and str(edit_input.get("status", "")).strip() == "in_progress"
        ) or (
            int(existing["current_contract_revision"]) > 0
            and existing["status"] == "review_pending"
        ):
            potential_contract_status = "in_progress"
        effort_preflight = prepare_task_transition(
            connection,
            project,
            task_id=normalized_task_id,
            previous_status=str(existing["status"]),
            current_status=potential_contract_status,
            profile=effort_profile,
        )
        locked_existing = lock_and_reread_edit_owner(
            connection,
            project,
            normalized_task_id,
            database_target=database_target,
        )
        result = edit_contract(
            connection,
            project,
            locked_existing,
            caller_edit_input=edit_input,
            contract_input=contract_input,
            basis_precommit_validator=basis_precommit_validator,
        )
        if result.event is not None:
            record_task_transition(
                connection,
                project,
                task_id=normalized_task_id,
                previous_status=str(locked_existing["status"]),
                current_status=str(result.task["status"]),
                profile=effort_profile,
                occurred_at=str(result.event["created_at"]),
                preflight=effort_preflight,
            )
        return EditTaskResult(
            task=result.task,
            changed_fields=result.changed_fields,
            event=result.event,
            contract_write=result.contract_write.to_dict(),
        )

    normalized = validate_task_edit_input(**edit_input)
    provided_fields = set(normalized)
    reopen_reason = normalized.pop("reopen_reason", None)
    review_tier_change_reason = normalized.pop("review_tier_change_reason", None)
    add_note = normalized.pop("add_note", None)
    completion_commit_hash = normalized.pop("completion_commit_hash", None)
    commit_not_required = bool(normalized.pop("commit_not_required", False))
    completion_evidence_kind = normalized.pop("completion_evidence_kind", None)
    completion_revision = normalized.pop("completion_revision", None)
    completion_evidence_reason = normalized.pop("completion_evidence_reason", None)
    external_revision_approved = bool(normalized.pop("external_revision_approved", False))
    verification_complete = bool(normalized.pop("verification_complete", False))
    review_complete = bool(normalized.pop("review_complete", False))

    if reopen_reason is not None:
        raise validation_error(
            "invalid_argument",
            "reopen_reason is valid only for an exact done to in_progress reopen",
            "reopen_reason",
        )

    updated = dict(existing)
    status_was_provided = "status" in normalized
    lane_was_provided = "lane" in normalized
    order_was_provided = "lane_order" in normalized
    for field, value in normalized.items():
        updated[field] = value
    authority_changed = any(
        field in normalized and updated[field] != existing[field]
        for field in ("title", "description", "verification", "review_tier")
    )
    authority_changed_after_target = (
        authority_changed and int(existing["review_target_generation"]) > 0
    )
    if authority_changed_after_target:
        if status_was_provided and updated["status"] not in {
            "in_progress",
            "paused",
            "blocked",
            "cancelled",
        }:
            raise validation_error(
                "invalid_status_transition",
                "authority changes require a fresh target before review or completion",
                "status",
            )
        evidence_companions = {
            "completion_commit_hash",
            "completion_evidence_kind",
            "completion_revision",
            "completion_evidence_reason",
            "external_revision_approved",
            "commit_not_required",
        }
        if provided_fields & evidence_companions:
            raise validation_error(
                "completion_evidence_conflict",
                "authority changes cannot be combined with completion evidence options",
                "verification",
            )
        updated.update(
            {
                "completion_evidence_kind": "none",
                "completion_evidence_revision": "",
                "completion_evidence_reason": "",
                "external_revision_approved": 0,
                "completion_commit_required": 1,
                "completion_commit_hash": "",
                "review_target_kind": "",
                "review_target_value": "",
                "review_target_base_revision": "",
                "review_target_generation": validate_sqlite_int64(
                    int(existing["review_target_generation"]) + 1,
                    field="review_target_generation",
                ),
                "review_target_capture_version": 0,
                "review_target_authority_snapshot_id": None,
                "review_target_acceptance_criterion_id": None,
                "review_target_verification_criterion_id": None,
                "review_target_artifact_manifest_id": None,
                "review_target_runner_basis_version": 0,
            }
        )
        if not status_was_provided and existing["status"] == "review_pending":
            updated["status"] = "in_progress"
    review_tier_changed = (
        "review_tier" in normalized
        and int(updated["review_tier"]) != int(existing["review_tier"])
    )
    review_tier_downgrade = (
        review_tier_changed
        and int(updated["review_tier"]) < int(existing["review_tier"])
    )
    if review_tier_change_reason is not None and not review_tier_changed:
        raise validation_error(
            "invalid_argument",
            "review_tier_change_reason requires a review_tier change",
            "review_tier_change_reason",
        )
    if review_tier_downgrade:
        safe_statuses = {"ready", "in_progress", "paused", "blocked"}
        forbidden_companions = {
            "completion_commit_hash",
            "completion_evidence_kind",
            "completion_revision",
            "completion_evidence_reason",
            "external_revision_approved",
            "commit_not_required",
            "verification_complete",
            "review_complete",
        }
        if (
            review_tier_change_reason is None
            or existing["status"] not in safe_statuses
            or updated["status"] not in safe_statuses
            or int(existing["review_target_generation"]) != 0
            or str(existing["review_target_kind"]) != ""
            or str(existing["review_target_value"]) != ""
            or str(existing["review_target_base_revision"]) != ""
            or bool(forbidden_companions & provided_fields)
        ):
            raise TaskRepositoryError(
                "review_tier_downgrade_forbidden",
                "review tier may be lowered only before structured review begins",
            )
    evidence_marker: str | None = None
    requested_evidence_kind = completion_evidence_kind
    requested_revision = completion_revision or ""
    requested_reason = completion_evidence_reason or ""
    if completion_commit_hash is not None:
        requested_evidence_kind = "git_commit"
        requested_revision = completion_commit_hash
    elif commit_not_required:
        requested_evidence_kind = "commit_not_required"
    if completion_plan is not None and status_was_provided and updated["status"] == "done":
        updated.update(completion_plan.resolution.to_task_values())
        evidence_marker = completion_plan.resolution.audit_marker
    elif requested_evidence_kind is not None:
        if requested_evidence_kind == "git_commit":
            ensure_git_preflight_outside_transaction(connection)
        try:
            evidence = completion_evidence_values(
                repo=project.canonical_repo,
                kind=requested_evidence_kind,
                revision=requested_revision,
                reason=requested_reason,
                external_revision_approved=external_revision_approved,
            )
        except CompletionEvidenceError as exc:
            raise validation_error(exc.code, exc.message, exc.field) from exc
        updated.update(evidence.values)
        evidence_marker = evidence.audit_marker

    implicit_lane_order = False
    if updated["kind"] == "sequential":
        if lane_was_provided or existing["kind"] != "sequential":
            updated["lane"] = validate_lane(updated["lane"])
        if not updated["lane"]:
            updated["lane"] = "default"
        if updated["lane_order"] is None:
            implicit_lane_order = True
        elif (
            lane_was_provided
            and not order_was_provided
            and updated["lane"] != existing["lane"]
        ):
            implicit_lane_order = True

    if status_was_provided and updated["status"] == "blocked" and "blocked_reason" not in normalized:
        raise validation_error(
            "blocked_reason_required",
            "blocked_reason is required when status is blocked",
            "blocked_reason",
        )
    if updated["status"] == "blocked" and not str(updated["blocked_reason"]).strip():
        raise validation_error(
            "blocked_reason_required",
            "blocked_reason is required when status is blocked",
            "blocked_reason",
        )
    if status_was_provided and updated["status"] != "blocked" and "blocked_reason" not in normalized:
        updated["blocked_reason"] = ""

    if status_was_provided and updated["status"] == "paused":
        if existing["status"] not in {"in_progress", "review_pending"}:
            raise validation_error(
                "invalid_status_transition",
                "only in_progress or review_pending tasks may transition to paused",
                "status",
            )
        if not str(updated.get("pause_reason", "")).strip():
            raise validation_error(
                "pause_reason_required",
                "pause_reason is required when status is paused",
                "pause_reason",
            )
    elif status_was_provided and existing["status"] == "paused":
        if updated["status"] != "in_progress":
            raise validation_error(
                "invalid_status_transition",
                "paused tasks may resume only to in_progress",
                "status",
            )
        if str(normalized.get("pause_reason", "")).strip():
            raise validation_error(
                "invalid_status_transition",
                "pause_reason cannot be retained when a paused task resumes",
                "pause_reason",
            )
        updated["pause_reason"] = ""
    elif "pause_reason" in normalized:
        if existing["status"] != "paused" or not str(updated["pause_reason"]).strip():
            raise validation_error(
                "pause_reason_required" if existing["status"] == "paused" else "invalid_status_transition",
                "a non-empty pause_reason may be recorded only while status is paused",
                "pause_reason",
            )
    elif updated["status"] != "paused":
        updated["pause_reason"] = ""

    validate_done_transition_inputs(
        updated,
        status_was_provided=status_was_provided,
        verification_complete=verification_complete,
        review_complete=review_complete,
    )
    if (
        status_was_provided
        and updated["status"] == "done"
        and (
            updated["completion_evidence_kind"] == "git_commit"
            or updated["review_target_kind"] in {"git_commit", "git_snapshot"}
        )
    ):
        ensure_git_preflight_outside_transaction(connection)
    if completion_plan is None:
        revalidate_done_git_evidence(
            updated,
            repo=project.canonical_repo,
            status_was_provided=status_was_provided,
        )
    completing = status_was_provided and updated["status"] == "done"
    lane_metadata_changed = any(
        updated[field] != existing[field]
        for field in ("kind", "lane", "lane_order")
    )
    advanced_transition = (
        status_was_provided
        and updated["status"] in ADVANCED_STATUSES
        and updated["status"] != existing["status"]
    )
    compatibility_runner_selection = None
    if (
        completing
        and completion_plan is None
        and runner_selector is not None
        and existing.get("review_target_runner_basis_version", 0) == 2
    ):
        preselector_basis = capture_completion_basis(
            connection,
            project,
            normalized_task_id,
        )
        reject_concurrent_edit_base_change(
            existing,
            preselector_basis.task,
            completing=True,
        )
        preselector_proposed_done = dict(updated)
        if implicit_lane_order:
            preselector_proposed_done["lane_order"] = next_lane_order(
                connection,
                project.project_id,
                str(preselector_proposed_done["lane"]),
            )
        validate_proposed_done_ordering(
            connection,
            project_id=project.project_id,
            task_id=normalized_task_id,
            proposed_done=preselector_proposed_done,
            lane_metadata_changed=lane_metadata_changed,
            advanced_transition=advanced_transition,
        )
        compatibility_request = CompletionRequest(
            task_id=normalized_task_id,
            verification_complete=verification_complete,
            review_complete=review_complete,
        )
        validate_completion_selector_prerequisites(
            preselector_basis,
            compatibility_request,
        )
        compatibility_runner_selection = runner_selector(
            preselector_basis.task,
            str(updated["completion_evidence_revision"]),
        )
    effort_preflight = prepare_task_transition(
        connection,
        project,
        task_id=normalized_task_id,
        previous_status=str(existing["status"]),
        current_status=str(updated["status"]),
        profile=effort_profile,
    )
    locked_existing = lock_and_reread_edit_owner(
        connection,
        project,
        normalized_task_id,
        database_target=database_target,
    )
    reject_concurrent_edit_base_change(
        existing,
        locked_existing,
        completing=(status_was_provided and updated["status"] == "done"),
    )
    if completion_plan is not None:
        locked_basis = capture_completion_basis(
            connection,
            project,
            normalized_task_id,
            runner_selection=completion_plan.basis.runner_selection,
        )
        validate_completion_plan_basis(
            completion_plan,
            locked_basis,
        )
    if implicit_lane_order:
        updated["lane_order"] = next_lane_order(
            connection,
            project.project_id,
            str(updated["lane"]),
        )

    now = utc_now()
    if status_was_provided:
        if updated["status"] == "done":
            updated["completed_at"] = existing["completed_at"] or now
        elif existing["completed_at"] is not None:
            updated["completed_at"] = None
    comparable_fields = (
        "title",
        "description",
        "kind",
        "lane",
        "lane_order",
        "priority",
        "status",
        "blocked_reason",
        "pause_reason",
        "review_tier",
        "verification",
        "tags",
        "completed_at",
        "completion_commit_required",
        "completion_commit_hash",
        "completion_evidence_kind",
        "completion_evidence_revision",
        "completion_evidence_reason",
        "external_revision_approved",
        "review_target_kind",
        "review_target_value",
        "review_target_base_revision",
        "review_target_generation",
    )
    changed_fields = [
        field for field in comparable_fields if updated[field] != existing[field]
    ]
    recorded_markers = []
    if evidence_marker is not None:
        recorded_markers.append(evidence_marker)
    if verification_complete:
        recorded_markers.append("verification complete")
    if review_complete:
        recorded_markers.append("review complete")
    if basis_precommit_validator is not None:
        basis_precommit_validator(locked_existing, updated)
    if not changed_fields and add_note is None and not recorded_markers:
        raise validation_error("invalid_argument", "task edit did not change any fields")

    pause_changed = updated["pause_reason"] != existing["pause_reason"]
    event_type, summary = task_edit_event_details(
        existing=existing,
        updated=updated,
        changed_fields=changed_fields,
        status_was_provided=status_was_provided,
        pause_changed=pause_changed,
        review_tier_changed=review_tier_changed,
        review_tier_change_reason=review_tier_change_reason,
        add_note=add_note,
        recorded_markers=recorded_markers,
    )
    update_values = {field: updated[field] for field in changed_fields}
    if authority_changed_after_target:
        update_values.update(
            {
                "review_target_capture_version": 0,
                "review_target_authority_snapshot_id": None,
                "review_target_acceptance_criterion_id": None,
                "review_target_verification_criterion_id": None,
                "review_target_artifact_manifest_id": None,
                "review_target_runner_basis_version": 0,
            }
        )
    update_values["updated_at"] = now
    ordering_changed = any(
        updated[field] != existing[field]
        for field in ("kind", "lane", "lane_order", "status")
    )
    affected_lanes: set[str] = set()
    if ordering_changed and existing["kind"] == "sequential":
        affected_lanes.add(str(existing["lane"]))
    if ordering_changed and updated["kind"] == "sequential":
        affected_lanes.add(str(updated["lane"]))
    event: dict[str, Any] | None = None
    task: dict[str, Any] | None = None
    savepoint = f"taskgov_ordering_{secrets.token_hex(4)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        completion_cycle_id: str | None = None
        if completing:
            proposed_done = dict(updated)
            proposed_done["updated_at"] = now
            validate_proposed_done_ordering(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
                proposed_done=proposed_done,
                lane_metadata_changed=lane_metadata_changed,
                advanced_transition=advanced_transition,
            )
            if (
                int(proposed_done["review_target_generation"]) > 0
                and str(proposed_done["review_target_kind"])
                and str(proposed_done["review_target_value"])
                and int(proposed_done.get("review_target_capture_version", 0)) != 1
            ):
                raise validation_error(
                    "evidence_basis_stale",
                    "current evidence basis must be captured again",
                )
            from task_governance_tool.verification_receipts import (
                enforce_verification_gate,
                verification_expectation_digest,
            )

            verification_gate = enforce_verification_gate(
                connection,
                task=proposed_done,
                runner_selection=(
                    completion_plan.basis.runner_selection
                    if completion_plan is not None
                    else compatibility_runner_selection
                ),
            )
            enforce_done_review_gate(
                connection,
                proposed_done,
                status_was_provided=True,
            )
            exact_verification_digest = verification_expectation_digest(
                str(proposed_done["verification"])
            )
            qualifying_verification_receipt_id = (
                verification_gate.qualifying_receipt_id
                if str(proposed_done["verification"]).strip()
                else None
            )
            subject_authority_snapshot_id = (
                str(proposed_done["review_target_authority_snapshot_id"])
                if str(proposed_done["verification"]).strip()
                else None
            )
            subject_verification_criterion_id = (
                str(proposed_done["review_target_verification_criterion_id"])
                if str(proposed_done["verification"]).strip()
                else None
            )
            completion_identity = allocate_native_completion_identity_locked(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
            )
            cycle = prepare_native_completion_cycle_locked(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
                task_projection=proposed_done,
                recorded_at=now,
                verification_expectation_digest=exact_verification_digest,
                verification_receipt_id=qualifying_verification_receipt_id,
                verification_subject_basis_version=1,
                subject_authority_snapshot_id=subject_authority_snapshot_id,
                subject_verification_criterion_id=(
                    subject_verification_criterion_id
                ),
                verification_basis_kind=(
                    verification_gate.verification_basis_kind
                ),
                verification_runner_observation_id=(
                    verification_gate.verification_runner_observation_id
                ),
                completion_identity=completion_identity,
            )
            completion_cycle_id = cycle.completion_cycle_id
            completion_binding = TargetCaptureBinding(
                target_kind=str(proposed_done["review_target_kind"]),
                target_value=str(proposed_done["review_target_value"]),
                target_base_revision=str(
                    proposed_done["review_target_base_revision"]
                ),
                target_generation=int(
                    proposed_done["review_target_generation"]
                ),
                authority_snapshot_id=str(
                    proposed_done["review_target_authority_snapshot_id"]
                ),
                acceptance_criterion_id=(
                    str(
                        proposed_done[
                            "review_target_acceptance_criterion_id"
                        ]
                    )
                    if proposed_done[
                        "review_target_acceptance_criterion_id"
                    ]
                    is not None
                    else None
                ),
                verification_criterion_id=(
                    str(
                        proposed_done[
                            "review_target_verification_criterion_id"
                        ]
                    )
                    if proposed_done[
                        "review_target_verification_criterion_id"
                    ]
                    is not None
                    else None
                ),
            )
            completion_source = EvidenceSource(
                source_kind="completion_evidence",
                source_state=str(cycle.completion_evidence_kind),
                source_id=cycle.completion_cycle_id,
                source_projection={
                    "completion_cycle_id": cycle.completion_cycle_id,
                    "completed_at": cycle.completed_at,
                    "completion_evidence_kind": (
                        cycle.completion_evidence_kind
                    ),
                    "completion_evidence_revision": (
                        cycle.completion_evidence_revision
                    ),
                    "completion_evidence_reason": (
                        cycle.completion_evidence_reason
                    ),
                    "external_revision_approved": int(
                        cycle.external_revision_approved
                    ),
                    "completion_commit_required": int(
                        cycle.completion_commit_required
                    ),
                    "completion_commit_hash": cycle.completion_commit_hash,
                },
            )
            completion_reference = build_evidence_reference(
                source=completion_source,
                project_id=project.project_id,
                task_id=normalized_task_id,
                contract_revision=cycle.contract_revision,
                binding=completion_binding,
                completion_cycle_id=cycle.completion_cycle_id,
            )
            completion_reference_row = {
                    "evidence_reference_id": (
                        f"tg_evidence_reference_{secrets.token_hex(8)}"
                    ),
                    "project_id": project.project_id,
                    "task_id": normalized_task_id,
                    "source_kind": completion_source.source_kind,
                    "source_state": completion_source.source_state,
                    "source_id": completion_source.source_id,
                    "assurance_class": (
                        completion_reference.attribution.assurance_class
                    ),
                    "producer_class": (
                        completion_reference.attribution.producer_class
                    ),
                    "producer_version": (
                        completion_reference.attribution.producer_version
                    ),
                    "contract_revision": cycle.contract_revision,
                    "authority_snapshot_id": (
                        completion_binding.authority_snapshot_id
                    ),
                    "acceptance_criterion_id": (
                        completion_binding.acceptance_criterion_id
                    ),
                    "verification_criterion_id": (
                        completion_binding.verification_criterion_id
                    ),
                    "target_kind": completion_binding.target_kind,
                    "target_value": completion_binding.target_value,
                    "target_base_revision": (
                        completion_binding.target_base_revision
                    ),
                    "target_generation": (
                        completion_binding.target_generation
                    ),
                    "completion_cycle_id": cycle.completion_cycle_id,
                    "digest": completion_reference.digest,
                    "created_at": now,
                }
            bundle_basis = read_native_completion_bundle_basis_locked(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
                cycle=cycle,
                completion_reference=completion_reference_row,
            )
            persist_evidence_reference_locked(
                connection,
                reference=completion_reference_row,
            )
            try:
                link_count = required_native_bundle_link_count(
                    basis=bundle_basis,
                    cycle=cycle,
                )
                bundle_plan = build_native_bundle_plan(
                    basis=bundle_basis,
                    cycle=cycle,
                    completion_identity=completion_identity,
                    criterion_link_ids=tuple(
                        "tg_criterion_evidence_link_"
                        + secrets.token_hex(8)
                        for _ in range(link_count)
                    ),
                    sealed_at=now,
                )
            except EvidenceProjectionError as exc:
                if exc.code == "evidence_bundle_too_large":
                    raise TaskValidationError(
                        code=exc.code,
                        message=exc.message,
                    ) from exc
                raise StorageError(exc.code, exc.message) from exc
            prepared_links = tuple(
                PreparedCriterionEvidenceLink(**link)
                for link in bundle_plan.storage_links
            )
            prepared_members = tuple(
                [
                    PreparedCompletionBundleMember(
                        project_id=project.project_id,
                        task_id=normalized_task_id,
                        completion_evidence_bundle_id=(
                            completion_identity.completion_evidence_bundle_id
                        ),
                        member_kind="criterion_link",
                        ordinal=ordinal,
                        criterion_evidence_link_id=(
                            link.criterion_evidence_link_id
                        ),
                        evidence_reference_id=None,
                    )
                    for ordinal, link in enumerate(prepared_links)
                ]
                + [
                    PreparedCompletionBundleMember(
                        project_id=project.project_id,
                        task_id=normalized_task_id,
                        completion_evidence_bundle_id=(
                            completion_identity.completion_evidence_bundle_id
                        ),
                        member_kind="evidence_reference",
                        ordinal=ordinal,
                        criterion_evidence_link_id=None,
                        evidence_reference_id=reference_id,
                    )
                    for ordinal, reference_id in enumerate(
                        bundle_plan.reference_ids
                    )
                ]
            )
            prepared_findings = tuple(
                PreparedCompletionFindingSnapshot(
                    project_id=project.project_id,
                    task_id=normalized_task_id,
                    completion_evidence_bundle_id=(
                        completion_identity.completion_evidence_bundle_id
                    ),
                    ordinal=ordinal,
                    **snapshot,
                )
                for ordinal, snapshot in enumerate(
                    bundle_plan.finding_snapshots
                )
            )
            prepared_bundle = PreparedCompletionEvidenceBundle(
                completion_evidence_bundle_id=(
                    completion_identity.completion_evidence_bundle_id
                ),
                project_id=project.project_id,
                task_id=normalized_task_id,
                completion_cycle_id=completion_identity.completion_cycle_id,
                cycle_ordinal=completion_identity.saved_cycle_ordinal,
                source_schema_version=bundle_basis.source_schema_version,
                bundle_version=2,
                contract_revision=cycle.contract_revision,
                authority_snapshot_id=(
                    completion_binding.authority_snapshot_id
                ),
                acceptance_criterion_id=(
                    completion_binding.acceptance_criterion_id
                ),
                verification_criterion_id=(
                    completion_binding.verification_criterion_id
                ),
                target_kind=completion_binding.target_kind,
                target_value=completion_binding.target_value,
                target_base_revision=(
                    completion_binding.target_base_revision
                ),
                target_generation=completion_binding.target_generation,
                target_capture_version=1,
                artifact_manifest_id=(
                    str(proposed_done["review_target_artifact_manifest_id"])
                ),
                verification_receipt_id=(
                    qualifying_verification_receipt_id
                ),
                verification_basis_kind=cycle.verification_basis_kind,
                verification_runner_observation_id=(
                    cycle.verification_runner_observation_id
                ),
                omission_mask=bundle_plan.omission_mask,
                sealed_at=now,
                bundle_digest=bundle_plan.artifact.bundle_digest,
                payload_size_bytes=len(bundle_plan.artifact.payload_bytes),
                criterion_links=prepared_links,
                members=prepared_members,
                finding_snapshots=prepared_findings,
            )
            persisted_cycle = insert_native_completion_cycle_locked(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
                task_projection=proposed_done,
                recorded_at=now,
                verification_expectation_digest=exact_verification_digest,
                verification_receipt_id=(
                    qualifying_verification_receipt_id
                ),
                verification_subject_basis_version=1,
                subject_authority_snapshot_id=(
                    subject_authority_snapshot_id
                ),
                subject_verification_criterion_id=(
                    subject_verification_criterion_id
                ),
                verification_basis_kind=cycle.verification_basis_kind,
                verification_runner_observation_id=(
                    cycle.verification_runner_observation_id
                ),
                completion_identity=completion_identity,
                prepared_cycle=cycle,
                completion_bundle=prepared_bundle,
            )
            if persisted_cycle != cycle:
                raise completion_history_inconsistent()

        update_task_row(
            connection,
            task_id=normalized_task_id,
            project_id=project.project_id,
            values=update_values,
        )
        if authority_changed:
            capture_or_reuse_current_authority_snapshot_locked(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
                created_at=now,
            )
        if (
            updated["kind"] == "sequential"
            and (lane_metadata_changed or advanced_transition)
            and canonical_lane_order_conflict(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
                lane=str(updated["lane"]),
                lane_order=int(updated["lane_order"]),
            )
        ):
            raise TaskRepositoryError(
                "invalid_argument",
                "task conflicts with an existing canonical sequential lane order",
            )
        invalid_task_id = first_out_of_order_advanced_task(
            connection,
            project_id=project.project_id,
            lanes=affected_lanes,
        )
        if invalid_task_id is not None:
            raise TaskRepositoryError(
                "sequential_predecessor_incomplete",
                "sequential lane contains active, review-pending, or done work with an incomplete predecessor",
            )
        if completing:
            event = create_task_event(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
                event_type=event_type,
                summary=summary,
                created_at=now,
                completion_cycle_id=completion_cycle_id,
            )
            task = read_task(
                connection,
                project.project_id,
                normalized_task_id,
            )
            if task is None:
                raise TaskRepositoryError(
                    "internal_error",
                    "task was not readable after update",
                )
            record_task_transition(
                connection,
                project,
                task_id=normalized_task_id,
                previous_status=str(existing["status"]),
                current_status=str(task["status"]),
                profile=effort_profile,
                occurred_at=now,
                preflight=effort_preflight,
            )
    except sqlite3.IntegrityError as exc:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise TaskRepositoryError(
            "invalid_argument",
            "task violates database constraints, such as duplicate sequential lane order",
        ) from exc
    except Exception:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")

    if not completing:
        event = create_task_event(
            connection,
            project_id=project.project_id,
            task_id=normalized_task_id,
            event_type=event_type,
            summary=summary,
            created_at=now,
        )
        task = read_task(connection, project.project_id, normalized_task_id)
        if task is None:
            raise TaskRepositoryError(
                "internal_error",
                "task was not readable after update",
            )
        record_task_transition(
            connection,
            project,
            task_id=normalized_task_id,
            previous_status=str(existing["status"]),
            current_status=str(task["status"]),
            profile=effort_profile,
            occurred_at=now,
            preflight=effort_preflight,
        )
    if event is None or task is None:
        raise TaskRepositoryError(
            "internal_error",
            "task completion write did not produce an event and task",
        )
    return EditTaskResult(task=task, changed_fields=changed_fields, event=event)


def complete_task(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    plan: CompletionPlan,
    *,
    effort_profile: Any | None = None,
    database_target: DatabaseTarget | None = None,
) -> EditTaskResult:
    """Delegate thin completion to the existing task-edit transition."""
    request = plan.request
    edit_input: dict[str, Any] = {"status": "done"}
    if request.verification_complete:
        edit_input["verification_complete"] = True
    if request.review_complete:
        edit_input["review_complete"] = True
    if request.completion_evidence_kind is not None:
        edit_input["completion_evidence_kind"] = request.completion_evidence_kind
    if request.completion_revision:
        edit_input["completion_revision"] = request.completion_revision
    if request.completion_evidence_reason:
        edit_input["completion_evidence_reason"] = (
            request.completion_evidence_reason
        )
    if request.external_revision_approved:
        edit_input["external_revision_approved"] = True
    return edit_task(
        connection,
        project,
        request.task_id,
        effort_profile=effort_profile,
        database_target=database_target,
        completion_plan=plan,
        **edit_input,
    )
