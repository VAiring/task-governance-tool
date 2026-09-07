"""Shared Task scalar, text, and privacy value validation.

Caller values and the existing stored-legacy mode produce validated values or
the shared TaskValidationError; operation and database ownership stay outside.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any

from task_governance_tool.ordering import canonical_lane


KINDS = ("sequential", "optional")
PRIORITIES = ("low", "normal", "high", "urgent")
STATUSES = ("ready", "in_progress", "paused", "blocked", "review_pending", "done", "cancelled")
REVIEW_TIERS = (0, 1, 2)
SQLITE_INT64_MIN = -(1 << 63)
SQLITE_INT64_MAX = (1 << 63) - 1

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
    re.compile(
        r"(?!(?<![\w.-])Authorization[ \t]*:[ \t]*Bearer[ \t]+(?-i:<redacted>)(?=$|[\s`;,)}\]]))"
        r"Authorization\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
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
_MANUAL_DIAGNOSTIC_FIELDS = frozenset(("title", "description"))
_MANUAL_DIAGNOSTIC_DELIMITERS = (": stderr: ", ": stdout: ")


def _manual_diagnostic_quotation(
    field: str,
    value: str,
) -> tuple[str, int] | None:
    """Return the quoted body and outer heading offset for the exact form."""

    if field not in _MANUAL_DIAGNOSTIC_FIELDS or value.splitlines() != [value]:
        return None
    delimiter_counts = tuple(
        (delimiter, value.count(delimiter))
        for delimiter in _MANUAL_DIAGNOSTIC_DELIMITERS
    )
    if sum(count for _, count in delimiter_counts) != 1:
        return None
    delimiter = next(
        delimiter for delimiter, count in delimiter_counts if count == 1
    )
    context, quotation = value.split(delimiter, 1)
    if not context.strip() or not quotation.strip():
        return None
    return quotation, value.index(delimiter) + 2


def _has_single_manual_diagnostic_delimiter(field: str, value: str) -> bool:
    return field in _MANUAL_DIAGNOSTIC_FIELDS and sum(
        value.count(delimiter) for delimiter in _MANUAL_DIAGNOSTIC_DELIMITERS
    ) == 1


@dataclass
class TaskValidationError(Exception):
    code: str
    message: str
    field: str | None = None

    def __str__(self) -> str:
        return self.message

def validation_error(code: str, message: str, field: str | None = None) -> TaskValidationError:
    return TaskValidationError(code=code, message=message, field=field)


def ensure_string(field: str, value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise validation_error("invalid_argument", f"{field} must be a string", field)
    return value


def _reject_private_or_raw_content_value(
    field: str,
    guard_value: str,
    *,
    allow_manual_diagnostic_quotation: bool = True,
    strict_raw_output: bool = False,
) -> None:
    quotation = (
        _manual_diagnostic_quotation(field, guard_value)
        if allow_manual_diagnostic_quotation
        else None
    )
    if COMBINED_PRIVACY_PATTERN.search(guard_value):
        raise validation_error(
            "privacy_rejected",
            f"{field} appears to contain a secret, raw log, or dump content",
            field,
        )
    if (
        contains_basic_auth_value(guard_value)
        or contains_bearer_token_value(field, guard_value)
        or _contains_raw_output_value(
            field,
            guard_value,
            allow_manual_diagnostic_quotation=allow_manual_diagnostic_quotation,
            strict_raw_output=strict_raw_output,
        )
    ):
        raise validation_error(
            "privacy_rejected",
            f"{field} appears to contain a secret, raw log, or dump content",
            field,
        )
    if quotation is not None:
        _reject_private_or_raw_content_value(
            field,
            quotation[0],
            allow_manual_diagnostic_quotation=False,
            strict_raw_output=True,
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


def _contains_raw_output_value(
    field: str,
    value: str,
    *,
    allow_manual_diagnostic_quotation: bool,
    strict_raw_output: bool,
) -> bool:
    quotation = (
        _manual_diagnostic_quotation(field, value)
        if allow_manual_diagnostic_quotation
        else None
    )
    matches = tuple(RAW_OUTPUT_VALUE_PATTERN.finditer(value))
    if quotation is not None:
        _, expected_heading_offset = quotation
        return (
            len(matches) != 1
            or matches[0].start(1) != expected_heading_offset
        )
    if (
        allow_manual_diagnostic_quotation
        and _has_single_manual_diagnostic_delimiter(field, value)
    ):
        return True
    if strict_raw_output or field in STRICT_RAW_OUTPUT_FIELDS:
        return bool(matches)
    if field == "title":
        for match in matches:
            payload = match.group(2).strip().lower()
            if not payload.startswith(BENIGN_TITLE_RAW_OUTPUT_PREFIXES):
                return True
    return False


def contains_raw_output_value(field: str, value: str) -> bool:
    return _contains_raw_output_value(
        field,
        value,
        allow_manual_diagnostic_quotation=True,
        strict_raw_output=False,
    )


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
    if required and _has_single_manual_diagnostic_delimiter(field, text):
        _reject_private_or_raw_content_value(field, text)
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

def validate_task_id(value: Any) -> str:
    return validate_text("task_id", value, required=True, limit=128)
