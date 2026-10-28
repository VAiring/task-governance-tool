"""Closed wait events through the admitted common logger, never recovery proof."""
from enum import Enum
import re
import threading

from task_governance_tool import debug_log as log
from task_governance_tool import debug_log_values as projection
from .review_wait_direct_repository import DIRECT_REASONS


class Stage(Enum):
    WORKER = "worker"
    OBSERVATION = "observation"
    CONTROL_END = "control_end"
    TIMER_ARM = "timer_arm"
    TIMER_DELETE = "timer_delete"
    TIMER_PAUSE = "timer_pause"
    TIMER_SAVE = "timer_save"
    NOTIFICATION = "notification"
    RECEIPT = "receipt"
    RECEIPT_END = "receipt_end"
    JOIN = "join"
    EXIT_SAVE = "exit_save"
    CLEANUP = "cleanup"
    SERVICE_EXIT = "service_exit"
    TRANSPORT = "transport"
    FAILURE = "failure"
    COLLECTION = "collection"


class Outcome(Enum):
    STARTED = "started"
    ATTEMPT = "attempt"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    DEADLINE = "deadline"
    ENDED = "ended"
    WAITING = "waiting"
    BUSY = "busy"
    SKIPPED = "skipped"
    NOT_STARTED = "not_started"


_REASONS = DIRECT_REASONS | {
    "cancel_requested", "state_changed", "deadline", "dispatched", "returned",
    "receipt_seen", "receipt_already_seen", "receipt_not_eligible", "read_failed",
    "eof", "transport_error", "protocol_error", "requested", "cancelled", "unknown",
    "thread_join", "join_timeout", "not_started", "save_failed", "observer_start_failed",
    "observation_busy", "all_reviewers_ended", "failure_notification", "cleanup_failed",
    "collection_failed", "unhandled_exception",
    "join_failed", "receipt_save_failed", "receipt_record_failed",
    "arm_save_failed", "delete_save_failed", "pause_save_failed", "request_error",
}
_FAILED = {Outcome.UNKNOWN, Outcome.REJECTED, Outcome.FAILED, Outcome.CANCELLED, Outcome.DEADLINE}


def _identity(value, pattern):
    return value if type(value) is str and len(value) <= 64 and re.fullmatch(pattern, value) else None


def _session():
    session = log._current.get()
    if session is not None and threading.get_ident() != session.collector.thread_id:
        session.collector.mark_untracked()
        return None
    return None if log.sqlite.suppressed() else session


def bind(task_id=None, wait_id=None, probe_id=None, worker_id=None):
    """Only already-known identity; no state reads or cross-thread collector."""
    try:
        session = _session()
        if session is None:
            return
        uuid = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
        session.bind_wait({
            "task_id": _identity(task_id, r"tg_task_[a-f0-9]{16}"),
            "wait_id": _identity(wait_id, r"wait-[a-f0-9]{32}"),
            "probe_id": _identity(probe_id, uuid), "worker_id": _identity(worker_id, uuid),
        })
    except BaseException:
        pass


def event(stage, outcome, *, reason=None, error=None):
    try:
        session = _session()
        if (session is None or session.wait_identity is None
                or type(stage) is not Stage or type(outcome) is not Outcome):
            return
        reason = reason if type(reason) is str and reason in _REASONS else "unknown"
        key = (tuple(session.wait_identity.values()), stage, outcome, reason)
        if key in session.wait_seen:
            return  # Unchanged observations never produce per-poll lines.
        if len(session.wait_seen) >= 128:
            session.drop("transition_limit")
            return
        session.wait_seen.add(key)
        details = {**session.wait_identity, "stage": stage.value,
                   "outcome": outcome.value, "reason": reason}
        if error is not None:
            details["exception"] = projection.exception(error)
        session.emit("wait.transition", details,
                     failed=outcome in _FAILED or error is not None, important=True)
    except BaseException:
        pass


def checkpoint(*, urgent=False, poll=False):
    try:
        session = _session()
        if session is not None:
            if poll:
                session.wait_polls = min(session.wait_polls + 1, 2**63 - 1)
            session.checkpoint(urgent=urgent)
    except BaseException:
        pass
