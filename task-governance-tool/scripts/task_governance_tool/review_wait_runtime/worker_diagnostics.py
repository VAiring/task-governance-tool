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
    REQUEST = "request"
    READINESS = "readiness"
    TIMER_CONFIG = "timer_config"


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
    "automation_config_unsafe", "automation_config_invalid", "automation_config_changed",
    "automation_config_unavailable", "timezone_unavailable", "timezone_changed",
    "reviewers_pending", "parent_not_terminal", "parent_not_idle", "dispatch_candidate",
    "answers_pending", "readiness_unknown", "request_save_failed",
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
            **{key: session.wait_identity.get(key) if session.wait_identity
               and session.wait_identity.get("task_id") == task_id else None
               for key in ("parent_id", "request_sequence")},
        })
    except BaseException:
        pass


def bind_request(*, parent_id=None, sequence=None):
    """Enrich an admitted request from observations already made by its owner."""
    try:
        session = _session()
        if session is None or session.wait_identity is None:
            return
        identity = dict(session.wait_identity)
        if parent_id is not None:
            identity["parent_id"] = _identity(parent_id, r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
        identity["request_sequence"] = sequence if type(sequence) is int and 1 <= sequence <= 64 else None
        session.bind_wait(identity)
    except BaseException:
        pass


def _event(stage, outcome, *, reason=None, error=None, fields=None):
    try:
        session = _session()
        if (session is None or session.wait_identity is None
                or type(stage) is not Stage or type(outcome) is not Outcome):
            return
        from .failure_diagnostics import REASONS
        reason = reason if type(reason) is str and reason in _REASONS | REASONS else "unknown"
        fields = fields or {}
        key = (tuple(sorted(session.wait_identity.items())), stage, outcome, reason, tuple(fields.items()))
        if key in session.wait_seen:
            return  # Unchanged observations never produce per-poll lines.
        if len(session.wait_seen) >= 128:
            session.drop("transition_limit")
            return
        session.wait_seen.add(key)
        details = {**session.wait_identity, "stage": stage.value,
                   "outcome": outcome.value, "reason": reason, **fields}
        if error is not None:
            details["exception"] = projection.exception(error)
        session.emit("wait.transition", details,
                     failed=outcome in _FAILED or error is not None, important=True)
    except BaseException:
        pass


def event(stage, outcome, *, reason=None, error=None):
    _event(stage, outcome, reason=reason, error=error)


def request(diagnostic, *, result=None, error=None, outcome=Outcome.FAILED, secondary=False):
    """Protected call-local facts; never invoke failure() or change its state."""
    try:
        from .failure_diagnostics import CallDiagnostics, STAGES, REASONS, reason_for, _fixed
        if type(diagnostic) is not CallDiagnostics:
            return
        reason = reason_for(error) if secondary else diagnostic.reason
        if reason is None:
            reason = reason_for(error) if error is not None else result.get("error") if type(result) is dict else None
        _event(Stage.REQUEST, outcome, reason=_fixed(reason, REASONS, "unavailable"), error=error,
            fields={"boundary": _fixed(diagnostic.stage, STAGES, "request"),
                "cause": "secondary" if secondary else "primary",
                "local_state": _fixed(diagnostic.local_state, {"not_attempted", "may_have_changed"}, "may_have_changed"),
                "host_mutation": _fixed(diagnostic.host_mutation, {"not_dispatched", "may_have_occurred", "confirmed"}, "may_have_occurred"),
                "retained_effects": _fixed(diagnostic.retained_effects, {"not_inspected", "absent", "present_unresolved", "settled"}, "not_inspected"),
                "cleanup": _fixed(diagnostic.cleanup, {"not_attempted", "unresolved", "confirmed"}, "unresolved")})
    except BaseException:
        pass


def begin_observation(parent, *, terminal, parent_matches, turn_matches):
    """Retain this tick's protected parent facts before fallible child work."""
    try:
        session = _session()
        if session is None or session.wait_identity is None:
            return
        from .review_wait_host import ChildTurn, _TURN_STATES, _THREAD_STATES
        if type(parent) is not ChildTurn or any(type(v) is not bool for v in (terminal, parent_matches, turn_matches)):
            return
        session.wait_parent = {**session.wait_identity,
            "parent_turn_state": parent.status if parent.status in _TURN_STATES else "unknown",
            "parent_thread_state": parent.thread_status if parent.thread_status in _THREAD_STATES else "unknown",
            "parent_terminal": terminal, "parent_identity_matches": parent_matches,
            "original_turn_matches": turn_matches, "preparation_returned": True}
        progress(None, None)
    except BaseException:
        pass


def progress(reviewers_ended, ready, *, source="unavailable"):
    """Separate existing host-end observations from collection readiness."""
    try:
        session = _session()
        if session is None or session.wait_identity is None:
            return
        session.wait_progress = {
            "all_reviewers_ended": reviewers_ended if type(reviewers_ended) is bool else None,
            "review_progress_ready": ready if type(ready) is bool else None,
            "reviewer_observation_source": source if type(source) is str and source in
                {"host", "mixed", "saved_results", "readmission", "unavailable"} else "unavailable"}
        _retain_readiness(session)
        if reviewers_ended is True:
            _event(Stage.OBSERVATION, Outcome.ENDED, reason="all_reviewers_ended")
    except BaseException:
        pass


def _retain_readiness(session):
    """Last observed conditions, not permission for a later dispatch."""
    try:
        if session.wait_parent is None or any(
                session.wait_parent.get(key) != value for key, value in session.wait_identity.items()):
            return
        observed = session.wait_progress
        fields = {key: value for key, value in session.wait_parent.items() if key not in session.wait_identity}
        fields.update(observed)
        reason = ("reviewers_pending" if observed["all_reviewers_ended"] is False else
                  "answers_pending" if observed["review_progress_ready"] is False
                  and observed["reviewer_observation_source"] in
                      {"host", "mixed", "saved_results"} else
                  "readiness_unknown" if observed["review_progress_ready"] is not True else
                  "parent_not_terminal" if not fields["parent_terminal"] else
                  "parent_not_idle" if fields["parent_thread_state"] != "idle" else "dispatch_candidate")
        session.wait_observation = {**session.wait_identity, **fields, "reason": reason}
        _event(Stage.READINESS, Outcome.WAITING, reason=reason, fields=fields)
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
