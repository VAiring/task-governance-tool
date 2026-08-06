"""Independent read-only consumer for sealed M22 Evidence JSON.

The consumer deliberately imports neither SQLite/storage nor the producing
``evidence_projection`` module.  It validates canonical bytes, the closed
index/Bundle envelopes, source bindings, and representative nested semantics
needed before an analysis descriptor can be created.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from task_governance_tool.analysis_contracts import (
    AnalysisContractError,
    canonical_json_bytes,
    parse_canonical_json_document,
    validate_descriptor,
    validate_source_basis,
)
from task_governance_tool.state_paths import (
    EVIDENCE_BUNDLE_MAX_BYTES,
    EVIDENCE_INDEX_MAX_BYTES,
    StatePathError,
    inspect_physical_directory,
    read_physical_file_bounded,
)


INDEX_DOMAIN = b"taskgov-evidence-index-v1\0"
BUNDLE_DOMAIN = b"taskgov-completion-evidence-bundle-v1\0"
REVIEW_PROVENANCE_DOMAIN = b"taskgov-review-provenance-v1\0"
FINDING_SNAPSHOT_DOMAIN = b"taskgov-completion-bundle-finding-snapshot-v1\0"
CRITERION_DOMAIN = b"taskgov-contract-criterion-v1\0"
ARTIFACT_MANIFEST_DOMAIN = b"taskgov-artifact-manifest-v1\0"
EVIDENCE_REFERENCE_DOMAIN = b"taskgov-evidence-reference-v1\0"
INDEX_MAX_ENTRIES = 100_000

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_PROJECT_ID = re.compile(
    r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{12}|tg_project_[0-9a-f]{32})$"
)
_TASK_ID = re.compile(r"^tg_task_[0-9a-f]{16}$")
_CYCLE_ID = re.compile(r"^tg_completion_cycle_[0-9a-f]{16}$")
_BUNDLE_ID = re.compile(r"^tg_completion_evidence_bundle_[0-9a-f]{16}$")
_AUTHORITY_ID = re.compile(r"^tg_authority_snapshot_[0-9a-f]{16}$")
_CRITERION_ID = re.compile(r"^tg_contract_criterion_[0-9a-f]{16}$")
_LINK_ID = re.compile(r"^tg_criterion_evidence_link_[0-9a-f]{16}$")
_REFERENCE_ID = re.compile(r"^tg_evidence_reference_[0-9a-f]{16}$")
_MANIFEST_ID = re.compile(r"^tg_artifact_manifest_[0-9a-f]{16}$")
_VERIFICATION_ID = re.compile(r"^tg_verification_receipt_[0-9a-f]{16}$")
_REVIEW_ID = re.compile(r"^tg_review_receipt_[0-9a-f]{16}$")
_FINDING_ID = re.compile(r"^tg_review_finding_[0-9a-f]{16}$")
_PROVENANCE_ID = re.compile(r"^tg_review_provenance_[0-9a-f]{16}$")
_DECLARED_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}$")
_DECLARED_SKILL_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")
_UTC_SECOND = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$"
)
_ARTIFACT_MODES = frozenset({"100644", "100755", "120000", "160000"})
_ARTIFACT_KIND_RANK = {"add": 0, "modify": 1, "delete": 2, "rename": 3}
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {
        "AUX",
        "CON",
        "CONIN$",
        "CONOUT$",
        "NUL",
        "PRN",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        "COM\u00b9",
        "COM\u00b2",
        "COM\u00b3",
        "LPT\u00b9",
        "LPT\u00b2",
        "LPT\u00b3",
    }
)

_BUNDLE_ENV_NAME_PATTERN = (
    r"(?:[A-Z_][A-Z0-9_]*|(?i:Path|Temp|Tmp|Home|UserProfile|AppData|"
    r"LocalAppData|ProgramFiles|SystemRoot|ComSpec|Username|User|Pwd|Shell|"
    r"Java_Home|PythonPath|Node_Env|Virtual_Env))"
)
_LEGACY_DISPATCH_ASSIGNMENT = re.compile(
    r"(?<![^\s`])dispatch_authorization=[1-9][0-9]*(?=$|[\s`;,)\]])"
)
_LEGACY_DISPATCH_JSON = re.compile(
    r'"dispatch_authorization"\s*:\s*[1-9][0-9]*(?=\s*(?:$|[,}]))'
)

_BUNDLE_PRIVACY_PATTERNS = (
    re.compile(r"Authorization\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(
        r"Authorization\s+(?:Basic|Bearer|Token|ApiKey)(?:\s*[:=])?\s+\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:Set-)?Cookie\s*:", re.IGNORECASE),
    re.compile(
        r"\b[A-Z0-9_.-]*(?:Password|Passwd|Pwd|Token|Secret|Cookie|Credential|Credentials|Api[-_]?Key|ApiKey|Access[-_]?Key|AccessKey|Private[-_]?Key|PrivateKey)[A-Z0-9_.-]*\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\"'][A-Z0-9_.-]*dispatch_authorization[A-Z0-9_.-]*[\"']\s*:",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\"'][A-Z0-9_.-]*(?:Password|Passwd|Pwd|Token|Secret|Cookie|Credential|Credentials|Api[-_]?Key|ApiKey|Access[-_]?Key|AccessKey|Private[-_]?Key|PrivateKey|Authorization)[A-Z0-9_.-]*[\"']\s*:\s*[\"'][^\"']+[\"']",
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
    re.compile(r"(?<![^\s`])dispatch_authorization\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"\b(?:Basic|Bearer)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"-----(?:BEGIN|END)\s+", re.IGNORECASE),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"(?im)^\s*(?:private|system|developer)\s+prompt\s*[:=-]\s*\S+"),
    re.compile(
        r"(?im)^\s*(?:private\s+reasoning|chain[- ]of[- ]thought)\s*[:=-]\s*\S+"
    ),
    re.compile(r"(?im)^\s*(?:raw\s+)?review\s+transcript\s*[:=-]\s*\S+"),
    re.compile(
        r"(?im)^\s*stack\s+trace\s*[:=-]?\s*\n\s*(?:#\d+|at\s+|Traceback|Exception|Caused by:|panic:|goroutine|\S+\.\S+)"
    ),
    re.compile(r"(?im)^\s*(?:raw\s+)?(?:stdout|stderr)(?:\s+dump)?\s*[:=-]?\s*\n\s*\S+"),
    re.compile(r"(?im)^\s*(?:log\s+output|raw\s+log)\s*[:=-]?\s*\n\s*\S+"),
    re.compile(
        r"(?im)^\s*(?:log\s+output|raw\s+log)\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(?:output|log)\b)"
    ),
    re.compile(
        rf"(?m)\b(?i:environment(?:\s+(?:variables|dump))?|env(?:\s+(?:dump|vars))?)\s*[:=-]?\s*\n\s*{_BUNDLE_ENV_NAME_PATTERN}\s*[:=]"
    ),
    re.compile(
        rf"\b(?i:environment\s+(?:variables|dump)|env\s+(?:dump|vars))\s+{_BUNDLE_ENV_NAME_PATTERN}\s*[:=]"
    ),
    re.compile(rf"\b(?i:environment|env)\s+{_BUNDLE_ENV_NAME_PATTERN}\s*[:=]"),
    re.compile(r"(?m)^\s*[A-Z_][A-Z0-9_]*=.*\n\s*[A-Z_][A-Z0-9_]*="),
    re.compile(
        rf"(?m)^\s*{_BUNDLE_ENV_NAME_PATTERN}\s*[:=]\s*\S+.*\n\s*{_BUNDLE_ENV_NAME_PATTERN}\s*[:=]\s*\S+"
    ),
    re.compile(
        r"(?im)^\s*(?:raw\s+)?(?:stdout|stderr)(?:\s+dump)?\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(?:output|log)\b)"
    ),
    re.compile(
        r"\b(?:raw\s+)?(?:stdout|stderr)\s+dump\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(?:output|log)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\braw\s+(?:stdout|stderr)\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(?:output|log)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?im)^\s*command\s+(?:output|log)\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(?:output|log)\b)"
    ),
    re.compile(r"(?im)^\s*standard\s+(?:output|error)\s*\n\s*\S+"),
    re.compile(
        r"(?im)^\s*standard\s+(?:output|error)\s+(?:secret\b|failure\b|error\b|line\s+\d+|.*\b(?:output|log)\b)"
    ),
    re.compile(r"(?im)^\s*=+\s*(?:raw\s+)?(?:stdout|stderr)\s*=+"),
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
_BUNDLE_BASIC_AUTH = re.compile(
    r"\bBasic\s+([A-Za-z0-9+/]{8,}={0,2})(?=$|[\s,.;:)])",
    re.IGNORECASE,
)
_BUNDLE_BEARER_TOKEN = re.compile(
    r"\bBearer\s+([A-Za-z0-9._~+/=-]{3,})(?=$|[\s,.;:)])",
    re.IGNORECASE,
)
_BUNDLE_RAW_OUTPUT = re.compile(
    r"(?im)\b((?:raw\s+)?(?:stdout|stderr)(?:\s+dump)?|command\s+(?:output|log)|standard\s+(?:output|error)|log\s+output|raw\s+log)\s*[:=-]\s*(\S.*)$"
)
_BUNDLE_BENIGN_TITLE_RAW_OUTPUT_PREFIXES = (
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

_CRITERION_KIND_ORDER = {"acceptance": 0, "verification": 1}
_RELATION_ORDER = {
    "verification_attestation": 0,
    "review_assessment": 1,
    "review_finding": 2,
    "completion_basis": 3,
}
_SOURCE_KIND_ORDER = {
    "artifact_manifest": 0,
    "verification_receipt": 1,
    "review_receipt": 2,
    "review_finding": 3,
    "completion_evidence": 4,
}
_OMISSION_ORDER = (
    "acceptance_criterion_absent",
    "verification_criterion_absent",
    "artifact_content_not_observed",
    "historical_finding_reference_absent",
)

_REVIEWER_CLASSES = (
    "human",
    "llm",
    "deterministic_tool",
    "hybrid",
    "unknown",
)
_MODEL_STATES = ("declared", "not_applicable", "unknown")
_SKILL_STATES = ("declared", "not_applicable", "not_used", "unknown")
_CONTEXT_RELATIONS = (
    "same_context",
    "forked_context",
    "fresh_context",
    "external_context",
    "not_applicable",
    "unknown",
)
_REVIEW_PROFILES = (
    "general",
    "authority_contract",
    "implementation",
    "verification",
    "migration_compatibility",
    "privacy_safety",
    "release_acceptance",
)
_REVIEW_LENSES = (
    "correctness",
    "contract_compliance",
    "state_completion_integrity",
    "privacy",
    "target_safety",
    "verification_regression",
    "migration_compatibility",
    "maintainability",
    "accessibility",
    "performance",
    "release_integrity",
)
_REVIEW_METHODS = (
    "review_packet_inspection",
    "authority_cross_check",
    "diff_inspection",
    "source_inspection",
    "test_inspection",
    "verification_evidence_inspection",
    "artifact_inspection",
    "runtime_observation",
    "deterministic_rule_check",
)

_INDEX_ENVELOPE_KEYS = ("format_version", "index_digest", "payload")
_INDEX_PAYLOAD_KEYS = (
    "source_schema_version",
    "project_id",
    "projection_generation",
    "bundle_count",
    "legacy_count",
    "entries",
)
_INDEX_ENTRY_KEYS = (
    "task_id",
    "completion_cycle_id",
    "cycle_ordinal",
    "bundle_state",
    "bundle_id",
    "bundle_file",
    "bundle_digest",
    "file_digest",
    "sealed_at",
)
_BUNDLE_ENVELOPE_KEYS = ("bundle_digest", "format_version", "payload")
_BUNDLE_PAYLOAD_KEYS = (
    "artifact_manifest",
    "authority_snapshot",
    "bundle_id",
    "bundle_version",
    "completion_cycle_id",
    "cycle_ordinal",
    "sealed_at",
    "completion_evidence",
    "contract",
    "criteria",
    "criterion_links",
    "evidence_references",
    "finding_snapshots",
    "omissions",
    "project_id",
    "review_receipts",
    "source_schema_version",
    "target",
    "task",
    "verification_receipt",
)
_ARTIFACT_KEYS = (
    "artifact_manifest_id",
    "state",
    "object_format",
    "comparison_base",
    "digest",
    "omission_code",
    "entries",
)
_ARTIFACT_ENTRY_KEYS = (
    "ordinal",
    "kind",
    "old_path",
    "new_path",
    "before_mode",
    "before_object_id",
    "after_mode",
    "after_object_id",
)
_AUTHORITY_KEYS = ("authority_snapshot_id", "generation", "digest")
_COMPLETION_KEYS = (
    "kind",
    "revision",
    "reason",
    "external_revision_approved",
    "completion_commit_required",
    "completion_commit_hash",
)
_CONTRACT_KEYS = (
    "revision",
    "specified",
    "scope",
    "acceptance",
    "constraints",
    "authority_ref",
)
_CRITERION_KEYS = ("criterion_id", "kind", "text", "digest")
_LINK_KEYS = (
    "criterion_evidence_link_id",
    "criterion_id",
    "evidence_reference_id",
    "relation",
    "assurance_class",
    "producer_class",
    "producer_version",
)
_REFERENCE_KEYS = (
    "evidence_reference_id",
    "source_kind",
    "source_state",
    "source_id",
    "assurance_class",
    "producer_class",
    "producer_version",
    "contract_revision",
    "authority_snapshot_id",
    "acceptance_criterion_id",
    "verification_criterion_id",
    "target_kind",
    "target_value",
    "target_base_revision",
    "target_generation",
    "completion_cycle_id",
    "digest",
)
_FINDING_KEYS = (
    "review_finding_id",
    "review_receipt_id",
    "target_generation",
    "severity",
    "summary",
    "status",
    "resolution_summary",
    "created_at",
    "resolved_at",
    "evidence_reference_id",
    "assurance_class",
    "producer_class",
    "producer_version",
    "digest",
)
_REVIEW_KEYS = (
    "review_receipt_id",
    "reviewer_key",
    "receipt_kind",
    "verdict",
    "summary",
    "user_approved",
    "created_at",
    "review_provenance",
)
_PROVENANCE_KEYS = (
    "review_provenance_id",
    "provenance_version",
    "reviewer_class",
    "model_state",
    "declared_model_id",
    "skill_state",
    "declared_skill_id",
    "declared_skill_version",
    "review_profiles",
    "review_lenses",
    "context_relation",
    "method_codes",
    "assurance_class",
    "producer_class",
    "producer_version",
    "digest",
)
_TARGET_KEYS = ("kind", "value", "base_revision", "generation", "capture_version")
_TASK_KEYS = ("task_id", "title", "description", "review_tier", "verification")
_VERIFICATION_KEYS = (
    "verification_receipt_id",
    "verification_subject",
    "result",
    "duration_ms",
    "scope_coverage",
    "created_at",
)
_SUBJECT_KEYS = (
    "basis_version",
    "kind",
    "authority_snapshot_id",
    "verification_criterion_id",
)


@dataclass(frozen=True)
class EvidenceConsumerError(ValueError):
    code: str = "source_invalid"
    message: str = "analysis source is invalid"

    def __str__(self) -> str:
        return self.message


def _invalid() -> NoReturn:
    raise EvidenceConsumerError()


def _mapping(value: object, keys: Sequence[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        _invalid()
    return dict(value)


def _array(value: object) -> list[Any]:
    if type(value) is not list:
        _invalid()
    return list(value)


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _invalid()
    return value


def _text(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or "\0" in value:
        _invalid()
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise EvidenceConsumerError() from exc
    return value


def _project_id(value: object) -> str:
    return _identifier(value, _PROJECT_ID)


def _utc_second(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    text = _text(value)
    if text is None:
        _invalid()
    matched = _UTC_SECOND.fullmatch(text)
    if matched is None:
        _invalid()
    try:
        datetime(*(int(part) for part in matched.groups()))
    except ValueError as exc:
        raise EvidenceConsumerError() from exc
    return text


def _privacy_guard(field: str, value: str) -> None:
    """Reject private/raw content without importing the M22 producer stack."""

    guard_value = value
    if field == "contract_constraints":
        guard_value = _LEGACY_DISPATCH_ASSIGNMENT.sub(
            "taskgov_legacy_operation_sequence",
            guard_value,
        )
        guard_value = _LEGACY_DISPATCH_JSON.sub(
            '"taskgov_legacy_operation_sequence":1',
            guard_value,
        )
    if any(
        pattern.search(guard_value) is not None
        for pattern in _BUNDLE_PRIVACY_PATTERNS
    ):
        _invalid()
    for match in _BUNDLE_BASIC_AUTH.finditer(guard_value):
        token = match.group(1)
        padded = token + ("=" * (-len(token) % 4))
        try:
            decoded = base64.b64decode(padded, validate=True)
        except (ValueError, binascii.Error):
            continue
        if b":" in decoded:
            _invalid()
    raw_output = _BUNDLE_RAW_OUTPUT.search(guard_value)
    if raw_output is not None and (
        field != "title"
        or not raw_output.group(2).strip().lower().startswith(
            _BUNDLE_BENIGN_TITLE_RAW_OUTPUT_PREFIXES
        )
    ):
        _invalid()
    for match in _BUNDLE_BEARER_TOKEN.finditer(guard_value):
        token = match.group(1).rstrip(",.;)")
        lowered = token.lower()
        tail = guard_value[match.end() :].strip()
        if lowered in {
            "authentication",
            "auth",
            "token",
            "tokens",
            "header",
            "headers",
            "support",
            "behavior",
        }:
            continue
        if (
            lowered in {"secret", "abc123"}
            or "secret" in lowered
            or lowered.startswith(("sk-", "xox", "ghp_", "gho_", "pat_"))
            or (any(not character.isalpha() for character in token) and len(token) >= 5)
            or (len(token) >= 12 and not tail)
            or (field != "title" and len(token) >= 20)
        ):
            _invalid()


def _validate_bundle_privacy(
    *,
    task: Mapping[str, Any],
    contract: Mapping[str, Any],
    criteria: Sequence[Mapping[str, Any]],
    target: Mapping[str, Any],
    artifact_entries: Sequence[Mapping[str, Any]],
    completion: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
) -> None:
    """Apply one independent pure privacy pass to every Bundle free-text route."""

    values: list[tuple[str, object]] = [
        ("title", task["title"]),
        ("description", task["description"]),
        ("verification", task["verification"]),
        ("contract_scope", contract["scope"]),
        ("contract_acceptance", contract["acceptance"]),
        ("contract_constraints", contract["constraints"]),
        ("contract_authority_ref", contract["authority_ref"]),
        ("review_target_value", target["value"]),
        ("review_target_base_revision", target["base_revision"]),
        ("completion_revision", completion["revision"]),
        ("completion_evidence_reason", completion["reason"]),
        ("completion_commit_hash", completion["completion_commit_hash"]),
    ]
    values.extend(
        (
            "verification" if item["kind"] == "verification" else "contract_acceptance",
            item["text"],
        )
        for item in criteria
    )
    for item in artifact_entries:
        values.extend(
            (
                ("artifact_path", item["old_path"]),
                ("artifact_path", item["new_path"]),
            )
        )
    for item in reviews:
        values.extend(
            (
                ("reviewer_key", item["reviewer_key"]),
                ("review_receipt_summary", item["summary"]),
            )
        )
    for item in findings:
        values.extend(
            (
                ("review_finding_summary", item["summary"]),
                ("review_finding_resolution", item["resolution_summary"]),
            )
        )
    for field, value in values:
        if value is None:
            continue
        if type(value) is not str:
            _invalid()
        _privacy_guard(field, value)


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _invalid()
    return value


def _identifier(value: object, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _invalid()
    return value


def _domain_digest(domain: bytes, value: object) -> str:
    return "sha256:" + hashlib.sha256(domain + canonical_json_bytes(value)).hexdigest()


def _criterion_digest(kind: str, text: str) -> str:
    body = CRITERION_DOMAIN + kind.encode("utf-8") + b"\0" + text.encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _validate_artifact_digest(
    artifact: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    authority: Mapping[str, Any],
    criteria_by_kind: Mapping[str, Mapping[str, Any]],
) -> None:
    value = {
        "acceptance_criterion_id": (
            criteria_by_kind["acceptance"]["criterion_id"]
            if "acceptance" in criteria_by_kind
            else None
        ),
        "authority_snapshot_id": authority["authority_snapshot_id"],
        "comparison_base": artifact["comparison_base"],
        "entries": artifact["entries"],
        "object_format": artifact["object_format"],
        "omission_code": artifact["omission_code"],
        "state": artifact["state"],
        "target_base_revision": target["base_revision"] or "",
        "target_generation": target["generation"],
        "target_kind": target["kind"],
        "target_value": target["value"],
        "verification_criterion_id": (
            criteria_by_kind["verification"]["criterion_id"]
            if "verification" in criteria_by_kind
            else None
        ),
    }
    if artifact["digest"] != _domain_digest(ARTIFACT_MANIFEST_DOMAIN, value):
        _invalid()


def _closed_list(value: object, keys: Sequence[str]) -> list[dict[str, Any]]:
    return [_mapping(item, keys) for item in _array(value)]


def _utf8(value: object) -> bytes:
    text = _text(value)
    if type(text) is not str:
        _invalid()
    return text.encode("utf-8")


def _optional_identifier(
    value: object,
    pattern: re.Pattern[str],
) -> str | None:
    if value is None:
        return None
    if type(value) is not str or pattern.fullmatch(value) is None:
        _invalid()
    return value


def _ordered_codes(
    value: object,
    *,
    allowed: Sequence[str],
    maximum: int,
) -> list[str]:
    codes = _array(value)
    if (
        len(codes) > maximum
        or any(type(code) is not str or code not in allowed for code in codes)
        or len(codes) != len(set(codes))
    ):
        _invalid()
    selected = set(codes)
    if codes != [code for code in allowed if code in selected]:
        _invalid()
    return codes


def _validate_provenance_semantics(provenance: Mapping[str, Any]) -> None:
    reviewer_class = provenance["reviewer_class"]
    model_state = provenance["model_state"]
    skill_state = provenance["skill_state"]
    context_relation = provenance["context_relation"]
    if (
        reviewer_class not in _REVIEWER_CLASSES
        or model_state not in _MODEL_STATES
        or skill_state not in _SKILL_STATES
        or context_relation not in _CONTEXT_RELATIONS
    ):
        _invalid()

    model_id = _optional_identifier(
        provenance["declared_model_id"],
        _DECLARED_IDENTIFIER,
    )
    skill_id = _optional_identifier(
        provenance["declared_skill_id"],
        _DECLARED_IDENTIFIER,
    )
    skill_version = _optional_identifier(
        provenance["declared_skill_version"],
        _DECLARED_SKILL_VERSION,
    )
    for field, declared_value in (
        ("declared_model_id", model_id),
        ("declared_skill_id", skill_id),
        ("declared_skill_version", skill_version),
    ):
        if declared_value is not None:
            _privacy_guard(f"review_provenance_{field}", declared_value)
    _ordered_codes(
        provenance["review_profiles"],
        allowed=_REVIEW_PROFILES,
        maximum=4,
    )
    _ordered_codes(
        provenance["review_lenses"],
        allowed=_REVIEW_LENSES,
        maximum=8,
    )
    _ordered_codes(
        provenance["method_codes"],
        allowed=_REVIEW_METHODS,
        maximum=8,
    )

    model_declared = model_state == "declared" and model_id is not None
    model_unknown = model_state == "unknown" and model_id is None
    skill_declared = (
        skill_state == "declared"
        and skill_id is not None
        and skill_version is not None
    )
    skill_without_declaration = (
        skill_state in {"not_used", "unknown"}
        and skill_id is None
        and skill_version is None
    )
    if reviewer_class in {"human", "deterministic_tool"}:
        valid = (
            model_state == "not_applicable"
            and model_id is None
            and skill_state == "not_applicable"
            and skill_id is None
            and skill_version is None
        )
    elif reviewer_class in {"llm", "hybrid"}:
        valid = (model_declared or model_unknown) and (
            skill_declared or skill_without_declaration
        )
    else:
        valid = (
            model_state == "unknown"
            and model_id is None
            and skill_state == "unknown"
            and skill_id is None
            and skill_version is None
        )
    if not valid:
        _invalid()


def _validate_target(value: object) -> dict[str, Any]:
    target = _mapping(value, _TARGET_KEYS)
    kind = _text(target["kind"])
    target_value = _text(target["value"])
    base_revision = _text(target["base_revision"], nullable=True)
    if (
        not target_value
        or _integer(target["capture_version"]) != 1
        or _integer(target["generation"], minimum=1) < 1
    ):
        _invalid()
    if kind == "git_snapshot":
        if (
            _DIGEST.fullmatch(target_value) is None
            or type(base_revision) is not str
            or _GIT_OBJECT_ID.fullmatch(base_revision) is None
            or set(base_revision) == {"0"}
        ):
            _invalid()
    elif kind == "git_commit":
        if (
            base_revision is not None
            or _GIT_OBJECT_ID.fullmatch(target_value) is None
            or set(target_value) == {"0"}
        ):
            _invalid()
    elif kind == "diff_fingerprint":
        if base_revision is not None or _DIGEST.fullmatch(target_value) is None:
            _invalid()
    elif kind == "external_revision":
        if base_revision is not None or target_value != target_value.strip():
            _invalid()
    else:
        _invalid()
    return target


def _validate_artifact_path(value: object) -> str:
    if type(value) is not str:
        _invalid()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise EvidenceConsumerError() from exc
    if (
        not encoded
        or len(encoded) > 240
        or value.startswith("/")
        or "\\" in value
        or "\0" in value
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in value
        )
    ):
        _invalid()
    for part in value.split("/"):
        if (
            part in {"", ".", ".."}
            or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in part)
            or part.endswith((".", " "))
        ):
            _invalid()
        device_basename = part.split(".", 1)[0].rstrip(" ").upper()
        if device_basename in _WINDOWS_RESERVED_BASENAMES:
            _invalid()
    return value


def _artifact_sort_text(value: str | None) -> tuple[int, bytes]:
    return (0, b"") if value is None else (1, value.encode("utf-8"))


def _artifact_entry_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    primary = item["old_path"] if item["old_path"] is not None else item["new_path"]
    secondary = item["new_path"] if item["new_path"] is not None else item["old_path"]
    return (
        _artifact_sort_text(primary),
        _artifact_sort_text(secondary),
        _ARTIFACT_KIND_RANK[item["kind"]],
        _artifact_sort_text(item["before_mode"]),
        _artifact_sort_text(item["before_object_id"]),
        _artifact_sort_text(item["after_mode"]),
        _artifact_sort_text(item["after_object_id"]),
    )


def _validate_artifact_entry(
    item: Mapping[str, Any],
    *,
    object_id_length: int,
) -> None:
    _integer(item["ordinal"])
    kind = item["kind"]
    if kind not in _ARTIFACT_KIND_RANK:
        _invalid()
    old_path = item["old_path"]
    new_path = item["new_path"]
    if old_path is not None:
        old_path = _validate_artifact_path(old_path)
    if new_path is not None:
        new_path = _validate_artifact_path(new_path)

    before = (item["before_mode"], item["before_object_id"])
    after = (item["after_mode"], item["after_object_id"])
    before_null = before == (None, None)
    after_null = after == (None, None)
    for pair, is_null in ((before, before_null), (after, after_null)):
        if is_null:
            continue
        mode, object_id = pair
        if (
            mode is None
            or object_id is None
            or type(mode) is not str
            or mode not in _ARTIFACT_MODES
            or type(object_id) is not str
            or _GIT_OBJECT_ID.fullmatch(object_id) is None
            or set(object_id) == {"0"}
            or len(object_id) != object_id_length
        ):
            _invalid()
    valid = (
        kind == "add"
        and old_path is None
        and before_null
        and new_path is not None
        and not after_null
    ) or (
        kind == "delete"
        and old_path is not None
        and not before_null
        and new_path is None
        and after_null
    ) or (
        kind == "modify"
        and old_path is not None
        and old_path == new_path
        and not before_null
        and not after_null
        and before != after
    ) or (
        kind == "rename"
        and old_path is not None
        and new_path is not None
        and old_path != new_path
        and not before_null
        and before == after
    )
    if not valid:
        _invalid()


def _validate_artifact(
    value: object,
    *,
    target: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    artifact = _mapping(value, _ARTIFACT_KEYS)
    _identifier(artifact["artifact_manifest_id"], _MANIFEST_ID)
    _digest(artifact["digest"])
    entries = _closed_list(artifact["entries"], _ARTIFACT_ENTRY_KEYS)
    if [item["ordinal"] for item in entries] != list(range(len(entries))):
        _invalid()

    state = artifact["state"]
    if state == "complete_git":
        object_format = artifact["object_format"]
        comparison_base = artifact["comparison_base"]
        if (
            target["kind"] not in {"git_snapshot", "git_commit"}
            or object_format not in {"sha1", "sha256"}
            or type(comparison_base) is not str
            or _GIT_OBJECT_ID.fullmatch(comparison_base) is None
            or set(comparison_base) == {"0"}
            or len(comparison_base) != (40 if object_format == "sha1" else 64)
            or (
                target["kind"] == "git_commit"
                and len(target["value"])
                != (40 if object_format == "sha1" else 64)
            )
            or artifact["omission_code"] is not None
            or (
                target["kind"] == "git_snapshot"
                and comparison_base != target["base_revision"]
            )
        ):
            _invalid()
        object_id_length = 40 if object_format == "sha1" else 64
        if len(entries) > 10_000:
            _invalid()
        for item in entries:
            _validate_artifact_entry(item, object_id_length=object_id_length)
        if entries != sorted(entries, key=_artifact_entry_sort_key):
            _invalid()
    elif state == "opaque_target":
        if (
            target["kind"] not in {"diff_fingerprint", "external_revision"}
            or artifact["object_format"] is not None
            or artifact["comparison_base"] is not None
            or artifact["omission_code"] != "artifact_content_not_observed"
            or entries
        ):
            _invalid()
    else:
        _invalid()
    return artifact, entries


def _validate_completion(value: object) -> dict[str, Any]:
    completion = _mapping(value, _COMPLETION_KEYS)
    kind = completion["kind"]
    revision = _text(completion["revision"])
    reason = _text(completion["reason"])
    completion_hash = _text(completion["completion_commit_hash"])
    for field in ("external_revision_approved", "completion_commit_required"):
        if type(completion[field]) is not int or completion[field] not in {0, 1}:
            _invalid()
    if kind == "git_commit":
        valid = (
            type(revision) is str
            and _GIT_OBJECT_ID.fullmatch(revision) is not None
            and set(revision) != {"0"}
            and not reason
            and completion["external_revision_approved"] == 0
            and completion["completion_commit_required"] == 1
            and completion_hash == revision
        )
    elif kind == "external_revision":
        valid = (
            bool(revision)
            and revision == revision.strip()
            and bool(reason)
            and reason == reason.strip()
            and completion["external_revision_approved"] == 1
            and completion["completion_commit_required"] == 1
            and completion_hash == revision
        )
    elif kind == "commit_not_required":
        valid = (
            not revision
            and not reason
            and completion["external_revision_approved"] == 0
            and completion["completion_commit_required"] == 0
            and not completion_hash
        )
    else:
        valid = False
    if not valid:
        _invalid()
    return completion


def _validate_index_entry(value: object) -> dict[str, Any]:
    entry = _mapping(value, _INDEX_ENTRY_KEYS)
    _identifier(entry["task_id"], _TASK_ID)
    _identifier(entry["completion_cycle_id"], _CYCLE_ID)
    _integer(entry["cycle_ordinal"], minimum=1)
    state = entry["bundle_state"]
    bundle_fields = (
        "bundle_id",
        "bundle_file",
        "bundle_digest",
        "file_digest",
        "sealed_at",
    )
    if state == "legacy_unknown":
        if any(entry[field] is not None for field in bundle_fields):
            _invalid()
    elif state == "native":
        bundle_id = _identifier(entry["bundle_id"], _BUNDLE_ID)
        if entry["bundle_file"] != f"bundles/{bundle_id}.json":
            _invalid()
        _digest(entry["bundle_digest"])
        _digest(entry["file_digest"])
        _utc_second(entry["sealed_at"])
    else:
        _invalid()
    return entry


def _validate_index_envelope(value: object) -> dict[str, Any]:
    """Validate and normalize one complete canonical index authority."""

    envelope = _mapping(value, _INDEX_ENVELOPE_KEYS)
    payload = _mapping(envelope["payload"], _INDEX_PAYLOAD_KEYS)
    _project_id(payload["project_id"])
    if (
        _integer(payload["source_schema_version"]) != 19
        or _integer(envelope["format_version"]) != 1
    ):
        _invalid()
    _integer(payload["projection_generation"])
    bundle_count = _integer(payload["bundle_count"])
    legacy_count_declared = _integer(payload["legacy_count"])
    index_digest = _digest(envelope["index_digest"])
    try:
        expected_digest = _domain_digest(INDEX_DOMAIN, payload)
    except AnalysisContractError as exc:
        raise EvidenceConsumerError() from exc
    if index_digest != expected_digest:
        _invalid()
    entries = [_validate_index_entry(item) for item in _array(payload["entries"])]
    if len(entries) > INDEX_MAX_ENTRIES:
        _invalid()
    expected = sorted(
        entries,
        key=lambda item: (
            item["task_id"].encode("utf-8"),
            item["cycle_ordinal"],
            item["completion_cycle_id"].encode("utf-8"),
        ),
    )
    native_count = sum(item["bundle_state"] == "native" for item in entries)
    legacy_count = len(entries) - native_count
    if (
        entries != expected
        or bundle_count != native_count
        or legacy_count_declared != legacy_count
        or len({item["completion_cycle_id"] for item in entries}) != len(entries)
        or len({(item["task_id"], item["cycle_ordinal"]) for item in entries})
        != len(entries)
    ):
        _invalid()
    payload["entries"] = entries
    envelope["payload"] = payload
    try:
        canonical_json_bytes(envelope)
    except AnalysisContractError as exc:
        raise EvidenceConsumerError() from exc
    return envelope


def _validate_review_provenance(
    value: object,
    *,
    project_id: str,
    task_id: str,
    target: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any] | None:
    kind = receipt["receipt_kind"]
    if value is None:
        if kind != "not_required":
            _invalid()
        return None
    if kind not in {"independent", "self_review_fallback"}:
        _invalid()
    provenance = _mapping(value, _PROVENANCE_KEYS)
    _identifier(provenance["review_provenance_id"], _PROVENANCE_ID)
    if (
        _integer(provenance["provenance_version"]) != 1
        or provenance["assurance_class"] != "bound_attestation"
        or provenance["producer_class"] != "trusted_caller"
        or _integer(provenance["producer_version"]) != 1
    ):
        _invalid()
    _validate_provenance_semantics(provenance)
    sealed_target = dict(target)
    if sealed_target["base_revision"] is None:
        sealed_target["base_revision"] = ""
    digest_payload = {
        "project_id": project_id,
        "task_id": task_id,
        "review_receipt_id": receipt["review_receipt_id"],
        "receipt_kind": kind,
        "target": sealed_target,
        **{field: provenance[field] for field in _PROVENANCE_KEYS[1:-1]},
    }
    if provenance["digest"] != _domain_digest(
        REVIEW_PROVENANCE_DOMAIN,
        digest_payload,
    ):
        _invalid()
    return provenance


def _validate_reviews(
    value: object,
    *,
    project_id: str,
    task: Mapping[str, Any],
    target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    reviews = _closed_list(value, _REVIEW_KEYS)
    gate_basis: list[tuple[str, str, str]] = []
    seen_receipts: set[str] = set()
    review_tier = task["review_tier"]
    for receipt in reviews:
        receipt_id = _identifier(receipt["review_receipt_id"], _REVIEW_ID)
        reviewer_key = _text(receipt["reviewer_key"])
        summary = _text(receipt["summary"])
        _utc_second(receipt["created_at"])
        kind = receipt["receipt_kind"]
        verdict = receipt["verdict"]
        if (
            receipt_id in seen_receipts
            or not reviewer_key
            or reviewer_key != reviewer_key.strip()
            or kind not in {"independent", "self_review_fallback", "not_required"}
            or verdict not in {"pass", "changes_requested", "not_required"}
            or type(receipt["user_approved"]) is not int
            or receipt["user_approved"] not in {0, 1}
        ):
            _invalid()
        seen_receipts.add(receipt_id)
        approval = receipt["user_approved"]
        if kind == "independent":
            valid_relation = verdict == "pass" and approval == 0
        elif kind == "self_review_fallback":
            valid_relation = (
                review_tier in {1, 2}
                and verdict == "pass"
                and bool(summary and summary.strip())
                and approval == int(review_tier == 2)
            )
        else:
            valid_relation = (
                review_tier == 0
                and verdict == "not_required"
                and approval == 0
                and bool(summary and summary.strip())
            )
        if not valid_relation:
            _invalid()
        _validate_review_provenance(
            receipt["review_provenance"],
            project_id=project_id,
            task_id=task["task_id"],
            target=target,
            receipt=receipt,
        )
        gate_basis.append((kind, reviewer_key, receipt_id))

    if review_tier == 0:
        valid_basis = len(gate_basis) == 1 and gate_basis[0][0] == "not_required"
    elif review_tier == 1:
        valid_basis = (
            len(gate_basis) == 1
            and gate_basis[0][0] in {"independent", "self_review_fallback"}
        )
    else:
        fallback_basis = (
            len(gate_basis) == 1
            and gate_basis[0][0] == "self_review_fallback"
        )
        independent_basis = (
            len(gate_basis) == 2
            and all(item[0] == "independent" for item in gate_basis)
            and len({item[1] for item in gate_basis}) == 2
            and gate_basis
            == sorted(
                gate_basis,
                key=lambda item: (item[1].encode("utf-8"), item[2].encode("utf-8")),
            )
        )
        valid_basis = fallback_basis or independent_basis
    if not valid_basis:
        _invalid()
    return reviews


def _validate_findings(
    value: object,
    *,
    target_generation: int,
) -> list[dict[str, Any]]:
    findings = _closed_list(value, _FINDING_KEYS)
    seen_ids: set[str] = set()
    for finding in findings:
        finding_id = _identifier(finding["review_finding_id"], _FINDING_ID)
        _identifier(finding["review_receipt_id"], _REVIEW_ID)
        generation = _integer(finding["target_generation"], minimum=1)
        if (
            finding_id in seen_ids
            or generation > target_generation
            or finding["severity"] not in {"high", "medium", "low"}
            or finding["status"] not in {"open", "resolved"}
            or _integer(finding["producer_version"], minimum=1) != 1
        ):
            _invalid()
        seen_ids.add(finding_id)
        for field in ("summary", "resolution_summary"):
            _text(finding[field])
        _utc_second(finding["created_at"])
        _utc_second(finding["resolved_at"], nullable=True)
        reference_id = _optional_identifier(
            finding["evidence_reference_id"],
            _REFERENCE_ID,
        )
        expected_attribution = (
            ("legacy_unknown", "legacy_migration")
            if reference_id is None
            else ("bound_attestation", "trusted_caller")
        )
        if (
            (finding["assurance_class"], finding["producer_class"])
            != expected_attribution
            or finding["digest"]
            != _domain_digest(
                FINDING_SNAPSHOT_DOMAIN,
                {key: finding[key] for key in _FINDING_KEYS if key != "digest"},
            )
        ):
            _invalid()
    expected = sorted(
        findings,
        key=lambda item: (
            item["target_generation"],
            _utf8(item["created_at"]),
            _utf8(item["review_finding_id"]),
        ),
    )
    if findings != expected:
        _invalid()
    return findings


def _reference_source_projection(
    *,
    payload: Mapping[str, Any],
    reference: Mapping[str, Any],
    artifact: Mapping[str, Any],
    verification: Mapping[str, Any] | None,
    reviews_by_id: Mapping[str, Mapping[str, Any]],
    findings_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    source_kind = reference["source_kind"]
    if source_kind == "artifact_manifest":
        if artifact["state"] == "complete_git":
            return {
                "artifact_manifest_id": artifact["artifact_manifest_id"],
                "state": artifact["state"],
                "object_format": artifact["object_format"],
                "comparison_base": artifact["comparison_base"],
                "entry_count": len(artifact["entries"]),
                "digest": artifact["digest"],
                "omission_code": artifact["omission_code"],
            }
        return {
            "artifact_manifest_id": artifact["artifact_manifest_id"],
            "state": artifact["state"],
            "target_kind": payload["target"]["kind"],
            "digest": artifact["digest"],
            "omission_code": artifact["omission_code"],
        }
    if source_kind == "verification_receipt":
        if verification is None:
            _invalid()
        subject = verification["verification_subject"]
        return {
            "verification_receipt_id": verification["verification_receipt_id"],
            "subject_basis_version": subject["basis_version"],
            "authority_snapshot_id": subject["authority_snapshot_id"],
            "verification_criterion_id": subject["verification_criterion_id"],
            "result": verification["result"],
            "duration_ms": verification["duration_ms"],
            "scope_coverage": verification["scope_coverage"],
            "created_at": verification["created_at"],
        }
    if source_kind == "review_receipt":
        return dict(reviews_by_id[reference["source_id"]])
    if source_kind == "review_finding":
        finding = findings_by_id[reference["source_id"]]
        return {
            key: finding[key]
            for key in (
                "review_finding_id",
                "review_receipt_id",
                "severity",
                "summary",
                "created_at",
            )
        }
    completion = payload["completion_evidence"]
    return {
        "completion_cycle_id": payload["completion_cycle_id"],
        "completed_at": payload["sealed_at"],
        "completion_evidence_kind": completion["kind"],
        "completion_evidence_revision": completion["revision"],
        "completion_evidence_reason": completion["reason"],
        "external_revision_approved": completion["external_revision_approved"],
        "completion_commit_required": completion["completion_commit_required"],
        "completion_commit_hash": completion["completion_commit_hash"],
    }


def _validate_relations(
    *,
    payload: Mapping[str, Any],
    criteria: Sequence[Mapping[str, Any]],
    artifact: Mapping[str, Any],
    authority: Mapping[str, Any],
    contract: Mapping[str, Any],
    completion: Mapping[str, Any],
    target: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
    verification: Mapping[str, Any] | None,
) -> None:
    criteria_by_id = {item["criterion_id"]: item for item in criteria}
    criteria_by_kind = {item["kind"]: item for item in criteria}
    acceptance_id = (
        criteria_by_kind["acceptance"]["criterion_id"]
        if "acceptance" in criteria_by_kind
        else None
    )
    verification_id = (
        criteria_by_kind["verification"]["criterion_id"]
        if "verification" in criteria_by_kind
        else None
    )
    reviews_by_id = {item["review_receipt_id"]: item for item in reviews}
    review_ids = set(reviews_by_id)
    finding_by_id = {item["review_finding_id"]: item for item in findings}

    references = _closed_list(payload["evidence_references"], _REFERENCE_KEYS)
    reference_by_id: dict[str, dict[str, Any]] = {}
    source_keys: set[tuple[str, str]] = set()
    for reference in references:
        reference_id = _identifier(reference["evidence_reference_id"], _REFERENCE_ID)
        source_kind = reference["source_kind"]
        source_state = _text(reference["source_state"])
        source_id = _text(reference["source_id"])
        _digest(reference["digest"])
        _integer(reference["producer_version"], minimum=1)
        _integer(reference["contract_revision"])
        _identifier(reference["authority_snapshot_id"], _AUTHORITY_ID)
        _optional_identifier(reference["acceptance_criterion_id"], _CRITERION_ID)
        _optional_identifier(reference["verification_criterion_id"], _CRITERION_ID)
        _text(reference["target_base_revision"], nullable=True)
        _integer(reference["target_generation"], minimum=1)
        _optional_identifier(reference["completion_cycle_id"], _CYCLE_ID)
        if (
            source_kind not in _SOURCE_KIND_ORDER
            or not source_id
            or reference_id in reference_by_id
            or (source_kind, source_id) in source_keys
            or reference["producer_version"] != 1
            or reference["contract_revision"] != contract["revision"]
            or reference["authority_snapshot_id"]
            != authority["authority_snapshot_id"]
            or reference["acceptance_criterion_id"] != acceptance_id
            or reference["verification_criterion_id"] != verification_id
            or reference["target_kind"] != target["kind"]
            or reference["target_value"] != target["value"]
            or reference["target_base_revision"] != target["base_revision"]
            or reference["target_generation"] != target["generation"]
        ):
            _invalid()

        if source_kind == "artifact_manifest":
            expected = (
                artifact["state"],
                artifact["artifact_manifest_id"],
                (
                    ("machine_observed", "taskgov_git")
                    if artifact["state"] == "complete_git"
                    else (
                        ("bound_attestation", "trusted_caller")
                        if target["kind"] == "diff_fingerprint"
                        else ("external_reference", "external_system")
                    )
                ),
                None,
            )
        elif source_kind == "verification_receipt":
            if verification is None:
                _invalid()
            expected = (
                "recorded",
                verification["verification_receipt_id"],
                ("bound_attestation", "trusted_caller"),
                None,
            )
        elif source_kind == "review_receipt":
            if source_id not in review_ids:
                _invalid()
            expected = (
                "recorded",
                source_id,
                ("bound_attestation", "trusted_caller"),
                None,
            )
        elif source_kind == "review_finding":
            if source_id not in finding_by_id:
                _invalid()
            expected = (
                "recorded",
                source_id,
                ("bound_attestation", "trusted_caller"),
                None,
            )
        else:
            expected_attribution = {
                "git_commit": ("machine_observed", "taskgov_git"),
                "external_revision": ("external_reference", "external_system"),
                "commit_not_required": ("bound_attestation", "trusted_caller"),
            }[completion["kind"]]
            expected = (
                completion["kind"],
                payload["completion_cycle_id"],
                expected_attribution,
                payload["completion_cycle_id"],
            )
        if (
            source_state != expected[0]
            or source_id != expected[1]
            or (reference["assurance_class"], reference["producer_class"])
            != expected[2]
            or reference["completion_cycle_id"] != expected[3]
        ):
            _invalid()
        digest_value = {
            "acceptance_criterion_id": reference["acceptance_criterion_id"],
            "assurance_class": reference["assurance_class"],
            "authority_snapshot_id": reference["authority_snapshot_id"],
            "completion_cycle_id": reference["completion_cycle_id"],
            "contract_revision": reference["contract_revision"],
            "producer_class": reference["producer_class"],
            "producer_version": reference["producer_version"],
            "project_id": payload["project_id"],
            "source_id": source_id,
            "source_kind": source_kind,
            "source_projection": _reference_source_projection(
                payload=payload,
                reference=reference,
                artifact=artifact,
                verification=verification,
                reviews_by_id=reviews_by_id,
                findings_by_id=finding_by_id,
            ),
            "source_state": source_state,
            "target_base_revision": reference["target_base_revision"] or "",
            "target_generation": reference["target_generation"],
            "target_kind": reference["target_kind"],
            "target_value": reference["target_value"],
            "task_id": payload["task"]["task_id"],
            "verification_criterion_id": reference["verification_criterion_id"],
        }
        if reference["digest"] != _domain_digest(
            EVIDENCE_REFERENCE_DOMAIN,
            digest_value,
        ):
            _invalid()
        reference_by_id[reference_id] = reference
        source_keys.add((source_kind, source_id))

    expected_references = sorted(
        references,
        key=lambda item: (
            _SOURCE_KIND_ORDER[item["source_kind"]],
            _utf8(item["source_id"]),
            _utf8(item["evidence_reference_id"]),
        ),
    )
    if references != expected_references:
        _invalid()
    expected_source_ids = {
        "artifact_manifest": {artifact["artifact_manifest_id"]},
        "verification_receipt": (
            {verification["verification_receipt_id"]}
            if verification is not None
            else set()
        ),
        "review_receipt": review_ids,
        "review_finding": {
            item["review_finding_id"]
            for item in findings
            if item["evidence_reference_id"] is not None
        },
        "completion_evidence": {payload["completion_cycle_id"]},
    }
    observed_source_ids = {
        source_kind: {
            item["source_id"]
            for item in references
            if item["source_kind"] == source_kind
        }
        for source_kind in _SOURCE_KIND_ORDER
    }
    if observed_source_ids != expected_source_ids:
        _invalid()

    links = _closed_list(payload["criterion_links"], _LINK_KEYS)
    seen_link_ids: set[str] = set()
    seen_relations: set[tuple[str, str, str]] = set()
    for link in links:
        link_id = _identifier(link["criterion_evidence_link_id"], _LINK_ID)
        reference = reference_by_id.get(link["evidence_reference_id"])
        criterion = criteria_by_id.get(link["criterion_id"])
        relation = link["relation"]
        if (
            reference is None
            or criterion is None
            or relation not in _RELATION_ORDER
            or link_id in seen_link_ids
            or (link["criterion_id"], link["evidence_reference_id"], relation)
            in seen_relations
            or link["assurance_class"] != reference["assurance_class"]
            or link["producer_class"] != reference["producer_class"]
            or link["producer_version"] != reference["producer_version"]
        ):
            _invalid()
        valid_pair = (
            relation == "verification_attestation"
            and criterion["kind"] == "verification"
            and reference["source_kind"] == "verification_receipt"
        ) or (
            relation == "review_assessment"
            and criterion["kind"] == "acceptance"
            and reference["source_kind"] == "review_receipt"
        ) or (
            relation == "review_finding"
            and criterion["kind"] == "acceptance"
            and reference["source_kind"] == "review_finding"
            and finding_by_id[reference["source_id"]]["target_generation"]
            == target["generation"]
        ) or (
            relation == "completion_basis"
            and criterion["kind"] == "acceptance"
            and reference["source_kind"]
            in {"artifact_manifest", "completion_evidence"}
        )
        if not valid_pair:
            _invalid()
        seen_link_ids.add(link_id)
        seen_relations.add(
            (link["criterion_id"], link["evidence_reference_id"], relation)
        )

    expected_links = sorted(
        links,
        key=lambda item: (
            _CRITERION_KIND_ORDER[criteria_by_id[item["criterion_id"]]["kind"]],
            _utf8(item["criterion_id"]),
            _RELATION_ORDER[item["relation"]],
            _utf8(item["evidence_reference_id"]),
            _utf8(item["criterion_evidence_link_id"]),
        ),
    )
    if links != expected_links:
        _invalid()

    for reference in references:
        source_kind = reference["source_kind"]
        if source_kind == "verification_receipt":
            expected_relation = "verification_attestation"
            expected_criterion = verification_id
        elif source_kind == "review_receipt":
            expected_relation = "review_assessment"
            expected_criterion = acceptance_id
        elif source_kind == "review_finding":
            current = (
                finding_by_id[reference["source_id"]]["target_generation"]
                == target["generation"]
            )
            expected_relation = "review_finding" if current else None
            expected_criterion = acceptance_id if current else None
        else:
            expected_relation = "completion_basis"
            expected_criterion = acceptance_id
        matching = [
            link
            for link in links
            if link["evidence_reference_id"] == reference["evidence_reference_id"]
        ]
        expected_count = int(
            expected_relation is not None and expected_criterion is not None
        )
        if len(matching) != expected_count or (
            matching
            and (
                matching[0]["relation"] != expected_relation
                or matching[0]["criterion_id"] != expected_criterion
            )
        ):
            _invalid()

    for finding in findings:
        reference_id = finding["evidence_reference_id"]
        if reference_id is not None:
            reference = reference_by_id.get(reference_id)
            if (
                reference is None
                or reference["source_kind"] != "review_finding"
                or reference["source_id"] != finding["review_finding_id"]
                or reference["assurance_class"] != finding["assurance_class"]
                or reference["producer_class"] != finding["producer_class"]
                or reference["producer_version"] != finding["producer_version"]
            ):
                _invalid()


def _validate_bundle_payload(
    value: object,
    *,
    entry: Mapping[str, Any],
    project_id: str,
) -> dict[str, Any]:
    payload = _mapping(value, _BUNDLE_PAYLOAD_KEYS)
    sealed_at = _utc_second(payload["sealed_at"])
    if (
        _integer(payload["source_schema_version"]) != 19
        or _integer(payload["bundle_version"]) != 1
        or payload["project_id"] != project_id
        or payload["bundle_id"] != entry["bundle_id"]
        or payload["completion_cycle_id"] != entry["completion_cycle_id"]
        or payload["cycle_ordinal"] != entry["cycle_ordinal"]
        or sealed_at != entry["sealed_at"]
    ):
        _invalid()

    task = _mapping(payload["task"], _TASK_KEYS)
    if (
        _identifier(task["task_id"], _TASK_ID) != entry["task_id"]
        or type(task["review_tier"]) is not int
        or task["review_tier"] not in {0, 1, 2}
    ):
        _invalid()
    for field in ("title", "description", "verification"):
        _text(task[field])

    target = _validate_target(payload["target"])
    artifact, artifact_entries = _validate_artifact(
        payload["artifact_manifest"],
        target=target,
    )
    authority = _mapping(payload["authority_snapshot"], _AUTHORITY_KEYS)
    _identifier(authority["authority_snapshot_id"], _AUTHORITY_ID)
    _integer(authority["generation"], minimum=1)
    # The Bundle omits the snapshot's producer and full authority-basis fields;
    # its digest is therefore syntax/binding-attested here, not re-derived.
    _digest(authority["digest"])
    completion = _validate_completion(payload["completion_evidence"])
    contract = _mapping(payload["contract"], _CONTRACT_KEYS)
    revision = _integer(contract["revision"])
    if (
        type(contract["specified"]) is not bool
        or contract["specified"] != (revision > 0)
    ):
        _invalid()
    for field in ("scope", "acceptance", "constraints", "authority_ref"):
        _text(contract[field])

    criteria = _closed_list(payload["criteria"], _CRITERION_KEYS)
    for criterion in criteria:
        _identifier(criterion["criterion_id"], _CRITERION_ID)
        if criterion["kind"] not in _CRITERION_KIND_ORDER:
            _invalid()
        criterion_text = _text(criterion["text"])
        if (
            type(criterion_text) is not str
            or _digest(criterion["digest"])
            != _criterion_digest(criterion["kind"], criterion_text)
        ):
            _invalid()
    if (
        len({item["criterion_id"] for item in criteria}) != len(criteria)
        or len({item["kind"] for item in criteria}) != len(criteria)
        or criteria
        != sorted(
            criteria,
            key=lambda item: (
                _CRITERION_KIND_ORDER[item["kind"]],
                _utf8(item["criterion_id"]),
            ),
        )
    ):
        _invalid()
    criteria_by_kind = {item["kind"]: item for item in criteria}
    acceptance_expected = revision > 0 and bool(contract["acceptance"].strip())
    verification_expected = bool(task["verification"].strip())
    if (
        ("acceptance" in criteria_by_kind) != acceptance_expected
        or ("verification" in criteria_by_kind) != verification_expected
        or (
            acceptance_expected
            and criteria_by_kind["acceptance"]["text"] != contract["acceptance"]
        )
        or (
            verification_expected
            and criteria_by_kind["verification"]["text"] != task["verification"]
        )
    ):
        _invalid()
    _validate_artifact_digest(
        artifact,
        target=target,
        authority=authority,
        criteria_by_kind=criteria_by_kind,
    )

    omissions = _array(payload["omissions"])
    findings = _validate_findings(
        payload["finding_snapshots"],
        target_generation=target["generation"],
    )
    expected_omissions = []
    if not acceptance_expected:
        expected_omissions.append("acceptance_criterion_absent")
    if not verification_expected:
        expected_omissions.append("verification_criterion_absent")
    if artifact["state"] == "opaque_target":
        expected_omissions.append("artifact_content_not_observed")
    if any(item["evidence_reference_id"] is None for item in findings):
        expected_omissions.append("historical_finding_reference_absent")
    if (
        omissions != expected_omissions
        or any(type(item) is not str or item not in _OMISSION_ORDER for item in omissions)
    ):
        _invalid()

    reviews = _validate_reviews(
        payload["review_receipts"],
        project_id=project_id,
        task=task,
        target=target,
    )

    verification = payload["verification_receipt"]
    if verification_expected:
        if verification is None:
            _invalid()
        receipt = _mapping(verification, _VERIFICATION_KEYS)
        _identifier(receipt["verification_receipt_id"], _VERIFICATION_ID)
        subject = _mapping(receipt["verification_subject"], _SUBJECT_KEYS)
        if (
            _integer(subject["basis_version"]) != 1
            or subject["kind"] != "task_verification_criterion"
            or subject["authority_snapshot_id"]
            != authority["authority_snapshot_id"]
            or subject["verification_criterion_id"]
            != criteria_by_kind["verification"]["criterion_id"]
            or receipt["result"] != "pass"
            or receipt["scope_coverage"] != "full"
        ):
            _invalid()
        _integer(receipt["duration_ms"])
        _utc_second(receipt["created_at"])
        verification = receipt
    elif verification is not None:
        _invalid()

    _validate_relations(
        payload=payload,
        criteria=criteria,
        artifact=artifact,
        authority=authority,
        contract=contract,
        completion=completion,
        target=target,
        reviews=reviews,
        findings=findings,
        verification=verification,
    )
    _validate_bundle_privacy(
        task=task,
        contract=contract,
        criteria=criteria,
        target=target,
        artifact_entries=artifact_entries,
        completion=completion,
        reviews=reviews,
        findings=findings,
    )
    canonical_json_bytes(payload)
    return payload


def _fresh_canonical_mapping(document: bytes) -> dict[str, Any]:
    try:
        value = parse_canonical_json_document(
            document + b"\n",
            maximum=len(document) + 1,
        )
    except AnalysisContractError as exc:
        raise EvidenceConsumerError() from exc
    if type(value) is not dict:
        _invalid()
    return value


@dataclass(frozen=True, init=False)
class ValidatedEvidenceIndex:
    """Canonical immutable index bytes with fresh-copy public projections."""

    _evidence_root: Path = field(repr=False)
    _index_bytes: bytes = field(repr=False)

    def __init__(
        self,
        evidence_root: str | Path,
        project_id: str,
        projection_generation: int,
        index_digest: str,
        entries: Sequence[Mapping[str, Any]],
    ) -> None:
        if type(entries) not in {list, tuple}:
            _invalid()
        normalized_entries = [_validate_index_entry(item) for item in entries]
        bundle_count = sum(
            item["bundle_state"] == "native" for item in normalized_entries
        )
        envelope = _validate_index_envelope(
            {
                "format_version": 1,
                "index_digest": index_digest,
                "payload": {
                    "source_schema_version": 19,
                    "project_id": project_id,
                    "projection_generation": projection_generation,
                    "bundle_count": bundle_count,
                    "legacy_count": len(normalized_entries) - bundle_count,
                    "entries": normalized_entries,
                },
            }
        )
        object.__setattr__(self, "_evidence_root", Path(evidence_root))
        object.__setattr__(self, "_index_bytes", canonical_json_bytes(envelope))

    def _fresh_envelope(self) -> dict[str, Any]:
        return _fresh_canonical_mapping(self._index_bytes)

    @property
    def evidence_root(self) -> Path:
        return self._evidence_root

    @property
    def project_id(self) -> str:
        return str(self._fresh_envelope()["payload"]["project_id"])

    @property
    def projection_generation(self) -> int:
        return int(self._fresh_envelope()["payload"]["projection_generation"])

    @property
    def index_digest(self) -> str:
        return str(self._fresh_envelope()["index_digest"])

    @property
    def entries(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._fresh_envelope()["payload"]["entries"])


@dataclass(frozen=True, init=False)
class ValidatedEvidenceSource:
    """Canonical immutable source bytes with fresh-copy public projections."""

    _source_kind: str
    _source_basis_bytes: bytes = field(repr=False)
    _source_bytes: bytes | None = field(repr=False)

    def __init__(
        self,
        source_kind: str,
        source_basis: object,
        source: object | None,
    ) -> None:
        try:
            basis = validate_source_basis(source_basis, source_kind=source_kind)
        except AnalysisContractError as exc:
            raise EvidenceConsumerError() from exc
        _project_id(basis["project_id"])
        entry = _validate_index_entry(basis["entry"])
        basis["entry"] = entry
        source_bytes: bytes | None
        if source_kind == "legacy_index_entry":
            if source is not None:
                _invalid()
            source_bytes = None
        elif source_kind == "native_bundle":
            envelope = _mapping(source, _BUNDLE_ENVELOPE_KEYS)
            if (
                _integer(envelope["format_version"]) != 1
                or envelope["bundle_digest"] != entry["bundle_digest"]
            ):
                _invalid()
            payload = _validate_bundle_payload(
                envelope["payload"],
                entry=entry,
                project_id=basis["project_id"],
            )
            if envelope["bundle_digest"] != _domain_digest(BUNDLE_DOMAIN, payload):
                _invalid()
            source_bytes = canonical_json_bytes(envelope)
            if entry["file_digest"] != (
                "sha256:" + hashlib.sha256(source_bytes + b"\n").hexdigest()
            ):
                _invalid()
        else:
            _invalid()
        object.__setattr__(self, "_source_kind", source_kind)
        object.__setattr__(self, "_source_basis_bytes", canonical_json_bytes(basis))
        object.__setattr__(self, "_source_bytes", source_bytes)

    @property
    def source_kind(self) -> str:
        return self._source_kind

    @property
    def source_basis(self) -> dict[str, Any]:
        return _fresh_canonical_mapping(self._source_basis_bytes)

    @property
    def source(self) -> dict[str, Any] | None:
        if self._source_bytes is None:
            return None
        return _fresh_canonical_mapping(self._source_bytes)


def revalidate_validated_index(value: object) -> ValidatedEvidenceIndex:
    """Purely revalidate one index at every source-selection boundary."""

    if type(value) is not ValidatedEvidenceIndex:
        _invalid()
    envelope = _validate_index_envelope(value._fresh_envelope())
    payload = envelope["payload"]
    return ValidatedEvidenceIndex(
        value.evidence_root,
        payload["project_id"],
        payload["projection_generation"],
        envelope["index_digest"],
        payload["entries"],
    )


def revalidate_validated_source(value: object) -> ValidatedEvidenceSource:
    """Purely revalidate one source at packet/outbox trust boundaries."""

    if type(value) is not ValidatedEvidenceSource:
        _invalid()
    return ValidatedEvidenceSource(
        value.source_kind,
        value.source_basis,
        value.source,
    )


def read_evidence_index(
    evidence_root: str | Path,
    *,
    expected_project_id: str | None = None,
) -> ValidatedEvidenceIndex:
    """Read and validate one exact M22 index without enumerating Bundles."""

    root = Path(evidence_root)
    try:
        inspect_physical_directory(root, root=root.parent)
        document, _ = read_physical_file_bounded(
            root / "index.json",
            root=root,
            max_bytes=EVIDENCE_INDEX_MAX_BYTES,
        )
        envelope = _validate_index_envelope(
            parse_canonical_json_document(document, maximum=EVIDENCE_INDEX_MAX_BYTES)
        )
    except (StatePathError, AnalysisContractError, OSError) as exc:
        raise EvidenceConsumerError() from exc
    payload = envelope["payload"]
    project_id = payload["project_id"]
    if expected_project_id is not None and project_id != expected_project_id:
        _invalid()
    return ValidatedEvidenceIndex(
        root,
        project_id,
        payload["projection_generation"],
        envelope["index_digest"],
        payload["entries"],
    )


def _load_evidence_source(
    index: ValidatedEvidenceIndex,
    *,
    selected: dict[str, Any],
    source_kind: str,
    basis: dict[str, Any],
) -> ValidatedEvidenceSource:
    if source_kind == "legacy_index_entry":
        return ValidatedEvidenceSource(source_kind, basis, None)

    bundle_id = selected["bundle_id"]
    relative = PurePosixPath(selected["bundle_file"])
    if relative.parts != ("bundles", f"{bundle_id}.json"):
        _invalid()
    bundles_root = index.evidence_root / "bundles"
    bundle_path = bundles_root / f"{bundle_id}.json"
    try:
        inspect_physical_directory(bundles_root, root=index.evidence_root)
        document, observed = read_physical_file_bounded(
            bundle_path,
            root=index.evidence_root,
            max_bytes=EVIDENCE_BUNDLE_MAX_BYTES,
        )
        if selected["file_digest"] != "sha256:" + observed.sha256:
            _invalid()
        envelope = _mapping(
            parse_canonical_json_document(
                document,
                maximum=EVIDENCE_BUNDLE_MAX_BYTES,
            ),
            _BUNDLE_ENVELOPE_KEYS,
        )
    except (StatePathError, AnalysisContractError, OSError) as exc:
        raise EvidenceConsumerError() from exc
    if (
        _integer(envelope["format_version"]) != 1
        or envelope["bundle_digest"] != selected["bundle_digest"]
    ):
        _invalid()
    payload = _validate_bundle_payload(
        envelope["payload"],
        entry=selected,
        project_id=index.project_id,
    )
    if envelope["bundle_digest"] != _domain_digest(BUNDLE_DOMAIN, payload):
        _invalid()
    return ValidatedEvidenceSource(source_kind, basis, envelope)


def validate_evidence_source(
    index: ValidatedEvidenceIndex,
    entry: Mapping[str, Any],
) -> ValidatedEvidenceSource:
    """Bind one exact current index entry and its independently checked source."""

    stable_index = revalidate_validated_index(index)
    selected = _validate_index_entry(entry)
    if selected not in stable_index.entries:
        _invalid()
    source_kind = (
        "native_bundle" if selected["bundle_state"] == "native" else "legacy_index_entry"
    )
    try:
        basis = validate_source_basis(
            {
                "project_id": stable_index.project_id,
                "projection_generation": stable_index.projection_generation,
                "index_digest": stable_index.index_digest,
                "entry": selected,
            },
            source_kind=source_kind,
        )
    except AnalysisContractError as exc:
        raise EvidenceConsumerError() from exc
    return _load_evidence_source(
        stable_index,
        selected=selected,
        source_kind=source_kind,
        basis=basis,
    )


def revalidate_descriptor_source(
    index: ValidatedEvidenceIndex,
    descriptor: Mapping[str, Any],
) -> ValidatedEvidenceSource:
    """Recheck a replay source while preserving the descriptor's original basis.

    A newer valid index may change only its generation/digest for this replay.
    The project and all nine entry fields must still match exactly, and native
    Bundle bytes are checked against the descriptor-bound entry before the
    original basis is returned.
    """

    stable_index = revalidate_validated_index(index)
    try:
        bound = validate_descriptor(descriptor)
        source_kind = bound["source_kind"]
        basis = validate_source_basis(
            bound["source_basis"],
            source_kind=source_kind,
        )
    except AnalysisContractError as exc:
        raise EvidenceConsumerError() from exc
    selected = _validate_index_entry(basis["entry"])
    _project_id(basis["project_id"])
    if (
        basis["project_id"] != stable_index.project_id
        or selected not in stable_index.entries
    ):
        _invalid()
    return _load_evidence_source(
        stable_index,
        selected=selected,
        source_kind=source_kind,
        basis=basis,
    )


__all__ = (
    "EvidenceConsumerError",
    "ValidatedEvidenceIndex",
    "ValidatedEvidenceSource",
    "read_evidence_index",
    "revalidate_descriptor_source",
    "revalidate_validated_index",
    "revalidate_validated_source",
    "validate_evidence_source",
)
