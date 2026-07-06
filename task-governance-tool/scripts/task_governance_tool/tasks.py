"""Task-domain validation helpers."""

from __future__ import annotations

import re
import base64
import binascii
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.storage import ProjectIdentity, utc_now


KINDS = ("sequential", "optional")
PRIORITIES = ("low", "normal", "high", "urgent")
STATUSES = ("ready", "in_progress", "blocked", "review_pending", "done", "cancelled")
REVIEW_TIERS = (0, 1, 2)

TEXT_LIMITS = {
    "title": 200,
    "description": 4000,
    "verification": 500,
    "tags": 500,
    "add_note": 2000,
    "event_summary": 1000,
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
    "add_note",
    "event_summary",
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
    return dict(row)


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
        "review_tier": normalized["review_tier"],
        "verification": normalized["verification"],
        "tags": normalized["tags"],
        "created_at": now,
        "updated_at": now,
        "completed_at": completed_at,
    }
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
    except sqlite3.IntegrityError as exc:
        raise TaskRepositoryError(
            "invalid_argument",
            "task violates database constraints, such as duplicate sequential lane order",
        ) from exc

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
    elif isinstance(value, str) and value.strip().isdigit():
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
