"""Closed Codex tool-result/turn projection, without transcript retention.

This adapter does not assign a Task by time. It extracts a success acknowledgement
and its host-provided turn identity; the core repository must still verify the
acknowledgement against the committed event and ownership transition.
"""

from __future__ import annotations

from dataclasses import dataclass

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.task_ownership import _identifier
from task_governance_tool.task_values import validate_task_id, TaskValidationError
from task_governance_tool.usage_values import UsageError


ADAPTER_VERSION = "codex-task-turn-v3"
STATUSES = frozenset({"ready", "in_progress", "review_pending", "paused", "blocked", "done", "cancelled"})
CHECKS = frozenset({"task.context", "task.current", "task.next", "task.show", "task.list"})
_WRITES = frozenset({"task.add", "task.edit", "task.complete"})


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
                or not isinstance(self.status, str) or self.status not in STATUSES):
            raise UsageError("boundary_unknown")


def _registration_body(data, entry, field):
    """Resolve only the closed registration references inside this acknowledgement."""
    body = entry.get(field)
    if not isinstance(body, dict) or f"{field}_ref" not in body:
        return body
    identity = "task_id" if field == "task" else "task_event_id"
    if set(body) != {identity, f"{field}_ref"}:
        return None
    preparation = data.get("context_preparation")
    if not isinstance(preparation, dict) or preparation.get("status") != "ready":
        return None
    context = preparation.get("context")
    selected = context.get("selected") if isinstance(context, dict) else None
    if not isinstance(selected, dict):
        return None
    reference = body[f"{field}_ref"]
    if field == "task":
        if reference != "/data/context_preparation/context/selected/task":
            return None
        destination = selected.get("task")
    else:
        prefix = "/data/context_preparation/context/selected/events/"
        if not isinstance(reference, str) or not reference.startswith(prefix):
            return None
        suffix = reference[len(prefix):]
        events = selected.get("events")
        if (not isinstance(events, list) or not suffix.isascii() or not suffix.isdecimal()
                or len(suffix) > len(str(len(events)))):
            return None
        index = int(suffix)
        if str(index) != suffix or index >= len(events):
            return None
        destination = events[index]
    if not isinstance(destination, dict) or destination.get(identity) != body.get(identity):
        return None
    return destination


@dataclass(frozen=True)
class BodyProjection:
    """Validated metadata and incomplete evidence are independent outcomes."""

    observations: tuple = ()
    unreadable: bool = False
    terminal: bool = False


def is_foreign_identity(metadata, project_id, *, thread=None):
    """One independently valid exclusion precedes unrelated field validation."""
    if not isinstance(metadata, dict):
        return False
    project, sender = metadata.get("project_id"), metadata.get("session_id")
    return ((project_id is not None and isinstance(project, str)
             and 0 < len(project) <= 200 and project != project_id)
            or (thread is not None and is_session_id(sender) and sender != thread))


def _operation(entry, data, command, project, thread, turn):
    """Validate one Task entry, including closed same-ack references."""
    if not isinstance(entry, dict):
        raise UsageError("boundary_unknown")
    task, event = entry.get("task"), entry.get("event")
    if command == "task.add":
        task, event = (_registration_body(data, entry, field) for field in ("task", "event"))
    if not isinstance(task, dict) or not isinstance(event, dict):
        raise UsageError("boundary_unknown")
    task_id = validate_task_id(task.get("task_id"))
    status = task.get("status")
    if (task_id != task.get("task_id") or event.get("task_id") != task_id
            or event.get("project_id") != project or task.get("project_id") != project
            or not _identifier(event.get("task_event_id"), "tg_event_")
            or not isinstance(status, str) or status not in STATUSES):
        raise UsageError("boundary_unknown")
    # A valid ready registration is not an execution boundary. Missing or
    # contradictory identities above are not this legitimate irrelevant case.
    if command == "task.add" and status != "in_progress":
        return None
    ownership = task.get("ownership")
    if not isinstance(ownership, dict):
        raise UsageError("boundary_unknown")
    return OperationObservation(thread, turn, project, task_id, event["task_event_id"],
                                ownership.get("generation"), status)


def project_body(item, thread, turn, project_id=None):
    """Project one admitted semantic leaf without losing valid sibling entries.

    No transport decoding or source body leaves this boundary. Callers use the
    unreadable bit for lifetime proof, independently of retained observations.
    """
    from task_governance_tool.usage_review_attribution import ReviewBoundary, project_review
    from task_governance_tool.usage_wait_attribution import WaitObservation, project_wait
    result, unreadable = [], False
    try:
        reviewed = project_review(item, thread, turn, project_id)
        result.extend(reviewed.observations)
        unreadable |= reviewed.unreadable
    except (UsageError, ValueError, TypeError):
        unreadable = True
    try:
        result.extend(project_wait(item, thread, turn, project_id))
    except (UsageError, ValueError, TypeError):
        unreadable = True
    terminal = any(isinstance(value, (ReviewBoundary, WaitObservation)) for value in result)
    command = item.get("command")
    if "command" not in item:
        return BodyProjection(tuple(result), unreadable, terminal)
    if is_foreign_identity(item, project_id):
        return BodyProjection(tuple(result), unreadable, terminal)
    if not isinstance(command, str) or not command:
        return BodyProjection(tuple(result), True, terminal)
    project = item.get("project_id")
    if not isinstance(project, str) or not 0 < len(project) <= 200:
        return BodyProjection(tuple(result), True)
    data = item.get("data")
    if type(item.get("ok")) is not bool or not isinstance(data, dict):
        return BodyProjection(tuple(result), True)
    if command not in _WRITES | CHECKS:
        return BodyProjection(tuple(result), unreadable, terminal)
    if not item["ok"] or command in CHECKS:
        return BodyProjection(tuple(result), unreadable, terminal or not item["ok"])
    if command != "task.add":
        changed = data.get("changed_fields")
        if not isinstance(changed, list) or not all(isinstance(field, str) for field in changed):
            return BodyProjection(tuple(result), True)
        if "status" not in changed:
            return BodyProjection(tuple(result), unreadable)
    entries = data.get("tasks", [data]) if command == "task.add" else [data]
    if not isinstance(entries, list) or not entries:
        return BodyProjection(tuple(result), True)
    for entry in entries:
        try:
            observation = _operation(entry, data, command, project, thread, turn)
        except (UsageError, TaskValidationError, ValueError, TypeError):
            unreadable = True
        else:
            if observation is not None:
                result.append(observation)
    return BodyProjection(tuple(result), unreadable)


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
    # Import at the call boundary: the response tracker uses the semantic
    # projector above, while both source modes share its complete transport
    # admission. No second decoder or renewed child budget is permitted here.
    from task_governance_tool.usage_response_adapter import objects
    try:
        leaves = objects(payload.get("output"))
    except (UsageError, ValueError, TypeError, RecursionError, UnicodeError):
        return ()
    result = []
    for item in leaves:
        result.extend(project_body(item, thread_id, turn, project_id).observations)
    return tuple(result)
