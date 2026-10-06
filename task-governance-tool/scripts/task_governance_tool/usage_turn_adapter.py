"""Closed Codex tool-result/turn projection, without transcript retention.

This adapter does not assign a Task by time. It extracts a success acknowledgement
and its host-provided turn identity; the core repository must still verify the
acknowledgement against the committed event and ownership transition.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.task_ownership import _identifier
from task_governance_tool.task_values import validate_task_id, TaskValidationError
from task_governance_tool.usage_values import UsageError


ADAPTER_VERSION = "codex-task-turn-v2"
STATUSES = frozenset({"ready", "in_progress", "review_pending", "paused", "blocked", "done", "cancelled"})


@dataclass(frozen=True)
class TurnObservation:
    thread_id: str
    turn_id: str
    started_at: int

    def __post_init__(self):
        if (not is_session_id(self.thread_id) or not is_session_id(self.turn_id)
                or type(self.started_at) is not int or not 0 <= self.started_at < 2**63):
            raise UsageError("boundary_unknown")


@dataclass(frozen=True)
class OperationObservation:
    thread_id: str
    turn_id: str
    project_id: str
    task_id: str
    event_id: str
    generation: int
    status: str

    def __post_init__(self):
        try:
            valid_task = validate_task_id(self.task_id) == self.task_id
        except TaskValidationError:
            valid_task = False
        if (not valid_task or not is_session_id(self.thread_id) or not is_session_id(self.turn_id)
                or not isinstance(self.project_id, str) or not 0 < len(self.project_id) <= 200
                or not _identifier(self.event_id, "tg_event_")
                or type(self.generation) is not int or not 0 < self.generation < 2**63
                or self.status not in STATUSES):
            raise UsageError("boundary_unknown")


def _objects(value, depth=0):
    """Unwrap only known text/exec containers, never arbitrary dictionary fields.

The shell command/arguments are not read. A textual exec header is transport,
not Task prose. Unknown wrappers yield no binding instead of heuristic parsing.
"""
    if depth > 8:
        return
    if isinstance(value, list):
        for block in value:
            if isinstance(block, dict) and block.get("type") in ("input_text", "text"):
                yield from _objects(block.get("text"), depth + 1)
    elif isinstance(value, str):
        if value.startswith("Chunk ID:") and "\nOutput:\n" in value:
            value = value.split("\nOutput:\n", 1)[1]
        value = value.strip()
        try:
            while value:
                parsed, end = json.JSONDecoder().raw_decode(value)
                yield from _objects(parsed, depth + 1)
                value = value[end:].lstrip()
        except (ValueError, RecursionError):
            return
    elif isinstance(value, dict):
        yield value
        for key in ("output", "content"):
            if key in value:
                yield from _objects(value[key], depth + 1)


def project_record(record: dict, thread_id: str, *, project_id: str | None = None):
    """Return numerical metadata only from one already admitted source record."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ()
    if record.get("type") == "event_msg" and payload.get("type") == "task_started":
        return (TurnObservation(thread_id, payload.get("turn_id"), payload.get("started_at")),)
    if (record.get("type") != "response_item"
            or payload.get("type") not in ("function_call_output", "custom_tool_call_output")):
        return ()
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    turn = metadata.get("turn_id") if isinstance(metadata, dict) else None
    if not is_session_id(turn):
        return ()
    result = []
    for item in _objects(payload.get("output")):
        from task_governance_tool.usage_review_attribution import project_review
        from task_governance_tool.usage_wait_attribution import project_wait
        result.extend(project_review(item, thread_id, turn, project_id))
        result.extend(project_wait(item, thread_id, turn, project_id))
        if project_id is not None and item.get("project_id") != project_id:
            continue
        command = item.get("command")
        if command not in ("task.add", "task.edit", "task.complete") or item.get("ok") is not True:
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        if command == "task.add" and isinstance(data.get("tasks"), list):
            for entry in data["tasks"]:
                if isinstance(entry, dict) and "task" in entry:
                    single = {"task": entry.get("task"), "event": entry.get("event")}
                    nested = {"type": "response_item", "payload": {**payload, "output": {**item, "data": single}}}
                    result.extend(project_record(nested, thread_id, project_id=project_id))
            continue
        task, event = data.get("task"), data.get("event")
        if not isinstance(task, dict) or not isinstance(event, dict):
            continue
        if command != "task.add":
            changed = data.get("changed_fields")
            if not isinstance(changed, list) or "status" not in changed:
                continue
        ownership = task.get("ownership")
        if not isinstance(ownership, dict) or (command == "task.add" and task.get("status") != "in_progress"):
            continue
        if (event.get("task_id") != task.get("task_id")
                or event.get("project_id") != item.get("project_id")
                or task.get("project_id") != item.get("project_id")):
            continue
        result.append(OperationObservation(thread_id, turn, item["project_id"], task["task_id"],
                                           event.get("task_event_id"), ownership.get("generation"),
                                           task.get("status")))
    return tuple(result)
