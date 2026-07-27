"""Deterministic compact task projections for JSON command envelopes."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from task_governance_tool.tasks import suggested_next_action


COMPACT_CURRENT_MAX_BYTES = 24_576
COMPACT_NEXT_MAX_BYTES = 16_384
COMPACT_EVENT_SUMMARY_MAX_BYTES = 256

COMPACT_CURRENT_DATA_FIELDS = (
    "tasks",
    "total_matching",
    "returned_count",
    "limit",
    "statuses",
    "truncated",
)
COMPACT_CURRENT_TASK_FIELDS = (
    "task_id",
    "title",
    "status",
    "kind",
    "lane",
    "lane_order",
    "priority",
    "review_tier",
    "blocked_reason",
    "pause_reason",
    "latest_event",
    "suggested_next_action",
)
COMPACT_LATEST_EVENT_FIELDS = (
    "event_type",
    "summary",
    "created_at",
    "summary_truncated",
)
COMPACT_NEXT_DATA_FIELDS = (
    "tasks",
    "total_matching",
    "returned_count",
    "limit",
    "truncated",
)
COMPACT_NEXT_TASK_FIELDS = (
    "task_id",
    "title",
    "kind",
    "lane",
    "lane_order",
    "priority",
    "review_tier",
    "tags",
    "suggested_next_action",
)

SerializedSize = Callable[[dict[str, Any]], int]


class CompactProjectionError(ValueError):
    """An internal compact projection cannot satisfy its fixed contract."""


def truncate_utf8(
    value: str,
    *,
    max_bytes: int = COMPACT_EVENT_SUMMARY_MAX_BYTES,
) -> tuple[str, bool]:
    """Return the longest UTF-8 code-point prefix within ``max_bytes``."""
    if not isinstance(value, str):
        raise CompactProjectionError("compact text must be a string")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise CompactProjectionError("compact text byte limit is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CompactProjectionError("compact text is not valid Unicode") from exc
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _project_fields(
    source: Mapping[str, Any],
    fields: Sequence[str],
    *,
    object_name: str,
) -> dict[str, Any]:
    try:
        return {field: source[field] for field in fields}
    except KeyError as exc:
        raise CompactProjectionError(
            f"{object_name} is missing required field {exc.args[0]}"
        ) from None


def project_compact_current_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project one current-task row through the exact compact allow-list."""
    projected = _project_fields(
        task,
        tuple(
            field
            for field in COMPACT_CURRENT_TASK_FIELDS
            if field != "latest_event"
        ),
        object_name="current task",
    )
    latest_event = task.get("latest_event")
    if latest_event is None or latest_event == {}:
        projected["latest_event"] = {}
    else:
        if not isinstance(latest_event, Mapping):
            raise CompactProjectionError("latest event must be an object")
        event = _project_fields(
            latest_event,
            ("event_type", "summary", "created_at"),
            object_name="latest event",
        )
        event["summary"], event["summary_truncated"] = truncate_utf8(
            event["summary"]
        )
        projected["latest_event"] = event
    return {
        field: projected[field]
        for field in COMPACT_CURRENT_TASK_FIELDS
    }


def project_compact_next_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project one next-task row through the exact compact allow-list."""
    projected = _project_fields(
        task,
        tuple(
            field
            for field in COMPACT_NEXT_TASK_FIELDS
            if field != "suggested_next_action"
        ),
        object_name="next task",
    )
    projected["suggested_next_action"] = suggested_next_action(dict(task))
    return {
        field: projected[field]
        for field in COMPACT_NEXT_TASK_FIELDS
    }


def compact_current_empty_data(
    statuses: Sequence[str],
    *,
    limit: int = 0,
    total_matching: int = 0,
    truncated: bool = False,
) -> dict[str, Any]:
    """Return the exact compact-current empty/error data shape."""
    return {
        "tasks": [],
        "total_matching": total_matching,
        "returned_count": 0,
        "limit": limit,
        "statuses": list(statuses),
        "truncated": truncated,
    }


def compact_next_empty_data(
    *,
    limit: int = 0,
    total_matching: int = 0,
    truncated: bool = False,
) -> dict[str, Any]:
    """Return the exact compact-next empty/error data shape."""
    return {
        "tasks": [],
        "total_matching": total_matching,
        "returned_count": 0,
        "limit": limit,
        "truncated": truncated,
    }


def _validate_non_negative_integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CompactProjectionError(f"{name} must be a non-negative integer")
    return value


def _measure(
    data: dict[str, Any],
    *,
    serialized_size: SerializedSize,
) -> int:
    size = serialized_size(data)
    return _validate_non_negative_integer(size, name="serialized size")


def _bound_complete_rows(
    rows: Sequence[dict[str, Any]],
    *,
    byte_limit: int,
    serialized_size: SerializedSize,
    data_factory: Callable[[list[dict[str, Any]], bool], dict[str, Any]],
) -> dict[str, Any]:
    retained: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        candidate_rows = [*retained, row]
        candidate = data_factory(
            candidate_rows,
            index + 1 < len(rows),
        )
        if _measure(candidate, serialized_size=serialized_size) > byte_limit:
            break
        retained.append(row)

    final = data_factory(retained, len(retained) < len(rows))
    if _measure(final, serialized_size=serialized_size) > byte_limit:
        raise CompactProjectionError(
            "compact envelope exceeds its byte limit without task rows"
        )
    return final


def build_compact_current_data(
    tasks: Sequence[Mapping[str, Any]],
    *,
    total_matching: int,
    limit: int,
    statuses: Sequence[str],
    serialized_size: SerializedSize,
) -> dict[str, Any]:
    """Build capped current data using the caller's actual envelope serializer."""
    exact_total = _validate_non_negative_integer(
        total_matching,
        name="total_matching",
    )
    exact_limit = _validate_non_negative_integer(limit, name="limit")
    projected = [project_compact_current_task(task) for task in tasks]
    if exact_total < len(projected):
        raise CompactProjectionError(
            "total_matching cannot be smaller than selected task rows"
        )

    def data_factory(
        retained: list[dict[str, Any]],
        truncated: bool,
    ) -> dict[str, Any]:
        return {
            "tasks": retained,
            "total_matching": exact_total,
            "returned_count": len(retained),
            "limit": exact_limit,
            "statuses": list(statuses),
            "truncated": truncated,
        }

    return _bound_complete_rows(
        projected,
        byte_limit=COMPACT_CURRENT_MAX_BYTES,
        serialized_size=serialized_size,
        data_factory=data_factory,
    )


def build_compact_next_data(
    tasks: Sequence[Mapping[str, Any]],
    *,
    total_matching: int,
    limit: int,
    serialized_size: SerializedSize,
) -> dict[str, Any]:
    """Build capped next data using the caller's actual envelope serializer."""
    exact_total = _validate_non_negative_integer(
        total_matching,
        name="total_matching",
    )
    exact_limit = _validate_non_negative_integer(limit, name="limit")
    projected = [project_compact_next_task(task) for task in tasks]
    if exact_total < len(projected):
        raise CompactProjectionError(
            "total_matching cannot be smaller than selected task rows"
        )

    def data_factory(
        retained: list[dict[str, Any]],
        truncated: bool,
    ) -> dict[str, Any]:
        return {
            "tasks": retained,
            "total_matching": exact_total,
            "returned_count": len(retained),
            "limit": exact_limit,
            "truncated": truncated,
        }

    return _bound_complete_rows(
        projected,
        byte_limit=COMPACT_NEXT_MAX_BYTES,
        serialized_size=serialized_size,
        data_factory=data_factory,
    )
