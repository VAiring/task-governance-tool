"""Task-domain validation helpers."""

from __future__ import annotations

import re
import base64
import binascii
import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from task_governance_tool.completion import (
    CompletionEvidenceError,
    CompletionRequest,
    CompletionResolution,
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
from task_governance_tool.ordering import (
    ADVANCED_STATUSES,
    canonical_lane,
    canonical_lane_sql,
    duplicate_lane_order_sql,
    first_out_of_order_advanced_task,
    incomplete_predecessor_sql,
)
from task_governance_tool.storage import (
    CompletionHistory,
    DatabaseTarget,
    ProjectIdentity,
    begin_initialized_write,
    completion_history_inconsistent,
    current_schema_version,
    insert_completion_cycle_locked,
    insert_native_completion_cycle_locked,
    match_current_done_completion_cycle_locked,
    read_completion_history,
    utc_now,
)


KINDS = ("sequential", "optional")
PRIORITIES = ("low", "normal", "high", "urgent")
STATUSES = ("ready", "in_progress", "paused", "blocked", "review_pending", "done", "cancelled")
REVIEW_TIERS = (0, 1, 2)
SQLITE_INT64_MIN = -(1 << 63)
SQLITE_INT64_MAX = (1 << 63) - 1

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

TEXT_LIMITS = {
    "title": 200,
    "description": 4000,
    "verification": 500,
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
        r"\b[A-Z0-9_.-]*(Password|Passwd|Pwd|Token|Secret|Cookie|Credential|Credentials|Api[-_]?Key|ApiKey|Access[-_]?Key|AccessKey|Private[-_]?Key|PrivateKey)[A-Z0-9_.-]*\s*[:=]\s*\S+",
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

BASIC_AUTH_VALUE_PATTERN = re.compile(r"\bBasic\s+([A-Za-z0-9+/]{8,}={0,2})(?=$|[\s,.;:)])", re.IGNORECASE)
BEARER_TOKEN_VALUE_PATTERN = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{3,})(?=$|[\s,.;:)])", re.IGNORECASE)
RAW_OUTPUT_VALUE_PATTERN = re.compile(
    r"(?im)\b((?:raw\s+)?(?:stdout|stderr)(?:\s+dump)?|command\s+(?:output|log)|standard\s+(?:output|error)|log\s+output|raw\s+log)\s*[:=-]\s*(\S.*)$"
)
STRICT_RAW_OUTPUT_FIELDS = {
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


@dataclass(frozen=True)
class CompletionBasis:
    task: dict[str, Any] = field(repr=False)
    review_evidence: dict[str, Any] | None = field(repr=False)
    review_error: tuple[str, str, str | None] | None
    predecessor_incomplete: bool
    lane_order_conflict: bool
    semantic_token: str


@dataclass(frozen=True)
class CompletionPlan:
    request: CompletionRequest
    basis: CompletionBasis = field(repr=False)
    resolution: CompletionResolution


def validation_error(code: str, message: str, field: str | None = None) -> TaskValidationError:
    return TaskValidationError(code=code, message=message, field=field)


def ensure_string(field: str, value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise validation_error("invalid_argument", f"{field} must be a string", field)
    return value


def _reject_private_or_raw_content_value(field: str, guard_value: str) -> None:
    for pattern in PRIVACY_PATTERNS:
        if pattern.search(guard_value):
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
        "verification": validate_text("verification", verification, limit=TEXT_LIMITS["verification"]),
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
        task = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise TaskRepositoryError(
                "internal_error",
                "task was not readable after insert",
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
    rows = [row_to_task(row) for row in connection.execute(query, values).fetchall()]
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
            normalized[field] = validate_text(field, value, limit=TEXT_LIMITS["verification"])
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
) -> TaskShowResult:
    normalized_task_id = validate_task_id(task_id)
    task_row = connection.execute(
        """
        SELECT *
          FROM tasks
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project.project_id, normalized_task_id),
    ).fetchone()
    if task_row is None:
        raise TaskRepositoryError("not_found", "task was not found")

    task = row_to_show_task(task_row)
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
    )


def list_tasks_for_viewer(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    event_limit: int = 10,
) -> ViewerTaskListResult:
    """Return the complete, bounded task projection used by static viewers."""
    if not 1 <= event_limit <= 10:
        raise TaskRepositoryError(
            "internal_error",
            "viewer event limit must be between 1 and 10",
        )

    task_rows = connection.execute(
        """
        SELECT *
          FROM tasks
         WHERE project_id = ?
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
        (project.project_id,),
    ).fetchall()

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
        )
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
    rows = connection.execute(
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
    ).fetchall()
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
    row = connection.execute(
        """
        SELECT *
          FROM tasks
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project_id, task_id),
    ).fetchone()
    if row is None:
        return None
    return row_to_task(row)


def read_internal_task(connection: sqlite3.Connection, project_id: str, task_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT *
          FROM tasks
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project_id, task_id),
    ).fetchone()
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
        )
    except ReviewEvidenceError as exc:
        review_evidence = None
        review_error = (exc.code, exc.message, exc.field)

    semantic_token = _completion_semantic_token(
        {
            "task": task,
            "predecessor_incomplete": predecessor_incomplete,
            "lane_order_conflict": lane_order_conflict,
            "contract": contract,
            "review_evidence": review_evidence,
            "review_error": review_error,
        }
    )
    return CompletionBasis(
        task=task,
        review_evidence=review_evidence,
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


def validate_completion_database_basis(
    basis: CompletionBasis,
    proposed_task: dict[str, Any],
    *,
    verification_complete: bool,
    review_complete: bool,
) -> None:
    """Run the shared fail-fast database-only completion preflight."""
    validate_completion_state_basis(basis)
    validate_done_transition_inputs(
        proposed_task,
        status_was_provided=True,
        verification_complete=verification_complete,
        review_complete=review_complete,
    )
    if basis.review_error is not None:
        code, message, field = basis.review_error
        raise validation_error(code, message, field)
    if basis.review_evidence is None:
        raise TaskRepositoryError(
            "internal_error",
            "completion review evidence was not available",
        )

    from task_governance_tool.reviews import first_review_gate_error

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
    if completion_plan is not None:
        locked_basis = capture_completion_basis(
            connection,
            project,
            normalized_task_id,
        )
        validate_completion_plan_basis(
            completion_plan,
            locked_basis,
        )
    reject_concurrent_edit_base_change(
        existing,
        locked_existing,
        completing=(status_was_provided and updated["status"] == "done"),
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
    if not changed_fields and add_note is None and not recorded_markers:
        raise validation_error("invalid_argument", "task edit did not change any fields")

    completing = status_was_provided and updated["status"] == "done"
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
    lane_metadata_changed = any(
        updated[field] != existing[field]
        for field in ("kind", "lane", "lane_order")
    )
    advanced_transition = (
        status_was_provided
        and updated["status"] in ADVANCED_STATUSES
        and updated["status"] != existing["status"]
    )

    event: dict[str, Any] | None = None
    task: dict[str, Any] | None = None
    savepoint = f"taskgov_ordering_{secrets.token_hex(4)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        completion_cycle_id: str | None = None
        if completing:
            proposed_done = dict(updated)
            proposed_done["updated_at"] = now
            if (
                proposed_done["kind"] == "sequential"
                and (lane_metadata_changed or advanced_transition)
                and canonical_lane_order_conflict(
                    connection,
                    project_id=project.project_id,
                    task_id=normalized_task_id,
                    lane=str(proposed_done["lane"]),
                    lane_order=int(proposed_done["lane_order"]),
                )
            ):
                raise TaskRepositoryError(
                    "invalid_argument",
                    "task conflicts with an existing canonical sequential lane order",
                )
            if proposed_done["kind"] == "sequential":
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
                        project.project_id,
                        normalized_task_id,
                        canonical_lane(proposed_done["lane"]),
                        int(proposed_done["lane_order"]),
                    ),
                ).fetchone()
                if predecessor is not None:
                    raise TaskRepositoryError(
                        "sequential_predecessor_incomplete",
                        "sequential lane contains active, review-pending, or done work with an incomplete predecessor",
                    )
            enforce_done_review_gate(
                connection,
                proposed_done,
                status_was_provided=True,
            )
            cycle = insert_native_completion_cycle_locked(
                connection,
                project_id=project.project_id,
                task_id=normalized_task_id,
                task_projection=proposed_done,
                recorded_at=now,
            )
            completion_cycle_id = cycle.completion_cycle_id

        update_task_row(
            connection,
            task_id=normalized_task_id,
            project_id=project.project_id,
            values=update_values,
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
