"""Task-domain validation helpers."""

from __future__ import annotations

import re
import base64
import binascii
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.completion import (
    CompletionEvidenceError,
    WRITABLE_EVIDENCE_KINDS,
    completion_evidence_values,
    validate_evidence_matrix,
)
from task_governance_tool.ordering import first_out_of_order_advanced_task
from task_governance_tool.storage import ProjectIdentity, utc_now


KINDS = ("sequential", "optional")
PRIORITIES = ("low", "normal", "high", "urgent")
STATUSES = ("ready", "in_progress", "paused", "blocked", "review_pending", "done", "cancelled")
REVIEW_TIERS = (0, 1, 2)

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

TASK_SHOW_FIELDS = PUBLIC_TASK_FIELDS + (
    "completion_commit_required",
    "completion_commit_hash",
    "completion_evidence_kind",
    "completion_evidence_revision",
    "completion_evidence_reason",
    "external_revision_approved",
    "review_target_kind",
    "review_target_value",
    "review_target_generation",
)

VIEWER_TASK_FIELDS = TASK_SHOW_FIELDS

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
}

UPPER_ENV_NAME_PATTERN = r"[A-Z_][A-Z0-9_]*"
KNOWN_ENV_NAME_PATTERN = (
    r"(?i:Path|Temp|Tmp|Home|UserProfile|AppData|LocalAppData|"
    r"ProgramFiles|SystemRoot|ComSpec|Username|User|Pwd|Shell|"
    r"Java_Home|PythonPath|Node_Env|Virtual_Env)"
)
ENV_NAME_PATTERN = rf"(?:{UPPER_ENV_NAME_PATTERN}|{KNOWN_ENV_NAME_PATTERN})"

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


@dataclass(frozen=True)
class ViewerTaskListResult:
    tasks: list[dict[str, Any]]
    event_count: int


@dataclass(frozen=True)
class CurrentTaskResult:
    tasks: list[dict[str, Any]]
    count: int
    limit: int
    statuses: tuple[str, ...]


@dataclass(frozen=True)
class EditTaskResult:
    task: dict[str, Any]
    changed_fields: list[str]
    event: dict[str, Any]


def validation_error(code: str, message: str, field: str | None = None) -> TaskValidationError:
    return TaskValidationError(code=code, message=message, field=field)


def ensure_string(field: str, value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise validation_error("invalid_argument", f"{field} must be a string", field)
    return value


def reject_private_or_raw_content(field: str, value: str) -> None:
    for pattern in PRIVACY_PATTERNS:
        if pattern.search(value):
            raise validation_error(
                "privacy_rejected",
                f"{field} appears to contain a secret, raw log, or dump content",
                field,
            )
    if (
        contains_basic_auth_value(value)
        or contains_bearer_token_value(field, value)
        or contains_raw_output_value(field, value)
    ):
        raise validation_error(
            "privacy_rejected",
            f"{field} appears to contain a secret, raw log, or dump content",
            field,
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


def validate_text(
    field: str,
    value: Any,
    *,
    required: bool = False,
    limit: int | None = None,
    default: str = "",
) -> str:
    text = ensure_string(field, value, default=default)
    if required:
        text = text.strip()
        if not text:
            raise validation_error("invalid_argument", f"{field} is required", field)
    reject_private_or_raw_content(field, text)
    if limit is not None and len(text) > limit:
        raise validation_error(
            "invalid_argument",
            f"{field} must be {limit} characters or fewer",
            field,
        )
    return text


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


def validate_lane_order(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise validation_error("invalid_argument", "lane_order must be an integer", "lane_order")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise validation_error("invalid_argument", "lane_order must be an integer", "lane_order")


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
        "lane": validate_text("lane", lane),
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
        """
        SELECT MAX(lane_order) AS max_order
          FROM tasks
         WHERE project_id = ?
           AND kind = 'sequential'
           AND lane = ?
        """,
        (project_id, lane),
    ).fetchone()
    if row is None or row["max_order"] is None:
        return 1
    return int(row["max_order"]) + 1


def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {field: row[field] for field in PUBLIC_TASK_FIELDS if field in row_keys}


def row_to_show_task(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {field: row[field] for field in TASK_SHOW_FIELDS if field in row_keys}


def row_to_viewer_task(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {field: row[field] for field in VIEWER_TASK_FIELDS if field in row_keys}


def row_to_internal_task(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


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
) -> dict[str, Any]:
    event = {
        "task_event_id": generate_id("tg_event"),
        "task_id": task_id,
        "project_id": project_id,
        "event_type": event_type,
        "summary": validate_event_summary(summary),
        "created_at": created_at,
    }
    connection.execute(
        """
        INSERT INTO task_events(task_event_id, task_id, project_id, event_type, summary, created_at)
        VALUES (:task_event_id, :task_id, :project_id, :event_type, :summary, :created_at)
        """,
        event,
    )
    return event


def add_task(connection: sqlite3.Connection, project: ProjectIdentity, **task_input: Any) -> AddTaskResult:
    normalized = validate_task_input(**task_input)
    lane = normalized["lane"].strip()
    lane_order = normalized["lane_order"]
    if normalized["kind"] == "sequential":
        lane = lane or "default"
        if lane_order is None:
            lane_order = next_lane_order(connection, project.project_id, lane)

    now = utc_now()
    completed_at = now if normalized["status"] == "done" else None
    task_id = generate_id("tg_task")
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
    savepoint = f"taskgov_ordering_{secrets.token_hex(4)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        connection.execute(
            """
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
            )
            """,
            row,
        )
        if row["kind"] == "sequential":
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

    event = create_task_event(
        connection,
        project_id=project.project_id,
        task_id=task_id,
        event_type="task_added",
        summary="Task registered",
        created_at=now,
    )
    task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if task is None:
        raise TaskRepositoryError("internal_error", "task was not readable after insert")
    return AddTaskResult(task=row_to_task(task), event=event)


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
        filters.append("lane = ?")
        values.append(validate_text("lane", lane))
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
           lane,
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
            normalized[field] = validate_text(field, value)
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
    from task_governance_tool.reviews import read_review_evidence

    return TaskShowResult(
        task=task,
        events=[row_to_event(row) for row in event_rows],
        suggested_next_action=suggested_next_action(task),
        review_evidence=read_review_evidence(
            connection,
            project.project_id,
            normalized_task_id,
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
) -> CurrentTaskResult:
    row_limit = validate_limit(limit, default=20)
    rows = connection.execute(
        """
        SELECT
          task.*,
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
           AND task.status IN ('in_progress', 'review_pending', 'paused', 'blocked')
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
        (project.project_id, row_limit),
    ).fetchall()
    tasks = []
    for row in rows:
        task = row_to_task(row)
        if row["latest_event_id"] is None:
            task["latest_event"] = {}
        else:
            task["latest_event"] = {
                "task_event_id": row["latest_event_id"],
                "task_id": task["task_id"],
                "project_id": task["project_id"],
                "event_type": row["latest_event_type"],
                "summary": row["latest_event_summary"],
                "created_at": row["latest_event_created_at"],
            }
        task["suggested_next_action"] = current_suggested_next_action(task)
        tasks.append(task)
    return CurrentTaskResult(
        tasks=tasks,
        count=len(tasks),
        limit=row_limit,
        statuses=CURRENT_STATUSES,
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


def enforce_done_transition_gates(
    connection: sqlite3.Connection,
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


def edit_task(connection: sqlite3.Connection, project: ProjectIdentity, task_id: Any, **edit_input: Any) -> EditTaskResult:
    normalized_task_id = validate_task_id(task_id)
    normalized = validate_task_edit_input(**edit_input)
    add_note = normalized.pop("add_note", None)
    completion_commit_hash = normalized.pop("completion_commit_hash", None)
    commit_not_required = bool(normalized.pop("commit_not_required", False))
    completion_evidence_kind = normalized.pop("completion_evidence_kind", None)
    completion_revision = normalized.pop("completion_revision", None)
    completion_evidence_reason = normalized.pop("completion_evidence_reason", None)
    external_revision_approved = bool(normalized.pop("external_revision_approved", False))
    verification_complete = bool(normalized.pop("verification_complete", False))
    review_complete = bool(normalized.pop("review_complete", False))

    existing = read_internal_task(connection, project.project_id, normalized_task_id)
    if existing is None:
        raise TaskRepositoryError("not_found", "task was not found")

    updated = dict(existing)
    status_was_provided = "status" in normalized
    lane_was_provided = "lane" in normalized
    order_was_provided = "lane_order" in normalized
    for field, value in normalized.items():
        updated[field] = value
    evidence_marker: str | None = None
    requested_evidence_kind = completion_evidence_kind
    requested_revision = completion_revision or ""
    requested_reason = completion_evidence_reason or ""
    if completion_commit_hash is not None:
        requested_evidence_kind = "git_commit"
        requested_revision = completion_commit_hash
    elif commit_not_required:
        requested_evidence_kind = "commit_not_required"
    if requested_evidence_kind is not None:
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

    if updated["kind"] == "sequential":
        if not str(updated["lane"]).strip():
            updated["lane"] = "default"
        if updated["lane_order"] is None:
            updated["lane_order"] = next_lane_order(connection, project.project_id, str(updated["lane"]))
        elif lane_was_provided and not order_was_provided and updated["lane"] != existing["lane"]:
            updated["lane_order"] = next_lane_order(connection, project.project_id, str(updated["lane"]))

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
    changed_fields = [field for field in comparable_fields if updated[field] != existing[field]]
    recorded_markers = []
    if evidence_marker is not None:
        recorded_markers.append(evidence_marker)
    if verification_complete:
        recorded_markers.append("verification complete")
    if review_complete:
        recorded_markers.append("review complete")
    if not changed_fields and add_note is None and not recorded_markers:
        raise validation_error("invalid_argument", "task edit did not change any fields")

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

    savepoint = f"taskgov_ordering_{secrets.token_hex(4)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        update_task_row(
            connection,
            task_id=normalized_task_id,
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
        enforce_done_transition_gates(
            connection,
            updated,
            status_was_provided=status_was_provided,
            verification_complete=verification_complete,
            review_complete=review_complete,
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

    pause_changed = updated["pause_reason"] != existing["pause_reason"]
    if status_was_provided and updated["status"] == "paused":
        event_type = "task_updated"
        summary = bounded_transition_summary(
            "Paused: ",
            str(updated["pause_reason"]),
            recorded_markers=recorded_markers,
            note=add_note,
        )
    elif status_was_provided and existing["status"] == "paused":
        event_type = "task_updated"
        summary = bounded_transition_summary(
            "Resumed from paused; Previous reason: ",
            str(existing["pause_reason"]),
            recorded_markers=recorded_markers,
            note=add_note,
        )
    elif pause_changed:
        event_type = "task_updated"
        summary = bounded_transition_summary(
            "Pause reason updated: ",
            str(updated["pause_reason"]),
            recorded_markers=recorded_markers,
            note=add_note,
        )
    elif add_note is not None:
        event_type = "note_added" if not changed_fields and not recorded_markers else "task_updated"
        summary = note_event_summary(add_note, recorded_markers)
    elif recorded_markers:
        event_type = "task_updated"
        summary = f"Recorded: {', '.join(recorded_markers)}"
    else:
        event_type = "task_updated"
        summary = f"Updated fields: {', '.join(changed_fields)}"
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
        raise TaskRepositoryError("internal_error", "task was not readable after update")
    return EditTaskResult(task=task, changed_fields=changed_fields, event=event)
