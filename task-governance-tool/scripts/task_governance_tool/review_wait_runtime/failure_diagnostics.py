"""Transient, closed diagnostics for the normal MCP boundary; no recovery effects."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import json

from .review_wait_server import _BOOTSTRAP_REASONS, _BOOTSTRAP_STAGES, _bootstrap_failure
from .review_wait_host import (HOST_BOUNDARY_REASONS, HOST_OPERATIONS, HOST_TERMINATION,
                              EXECUTOR_TURN_FAILURES, HostAdapterError, host_failure,
                              read_failure, validated_read_failure)
from .review_wait_direct_repository import DIRECT_REASONS


MAX_DIAGNOSTIC_BYTES = 2048
STAGES = _BOOTSTRAP_STAGES | frozenset({
    "policy", "project_admission", "request_read", "parent_read", "finalization_basis",
    "request_write", "create", "verify_created", "prepare", "start", "cleanup",
    "readiness", "inspection", "operation",
})
PROJECT_REASONS = frozenset({
    "unsupported_python", "unsupported_install_layout", "project_scope_required",
    "invalid_project_root", "project_root_uninspectable", "state_path_invalid",
    "state_ignore_required", "project_state_unreadable", "project_mismatch",
    "project_relocation_required", "schema_too_new", "migration_required",
    "setup_required", "database_busy", "unsupported_journal_mode", "project_unavailable",
})
REASONS = _BOOTSTRAP_REASONS | HOST_BOUNDARY_REASONS | DIRECT_REASONS | PROJECT_REASONS | frozenset({
    "unavailable", "policy_unreadable", "review_wait_not_enabled", "service_closed",
    "binding_mismatch", "parent_turn_changed", "reviewer_turn_changed", "binding_changed",
    "session_limit", "heartbeat_create_unknown", "heartbeat_changed", "preparation_failed",
    "start_unavailable", "cleanup_unresolved", "review_wait_unresolved",
    "review_wait_restart_requires_recovery", "review_wait_binding_changed",
    "state_already_exists", "task_binding_changed", "heartbeat_not_admitted",
    "observer_cleanup_unknown", "direct_probe_unavailable", "direct_probe_selected",
    "review_continuation_conflict", "review_continuation_basis_changed",
    "review_continuation_effect_unknown", "review_continuation_worker_exit_unknown",
    "review_continuation_cleanup_required",
})


class DiagnosticError(ValueError):
    """Only internal fixed codes; exception messages never become diagnostics."""

    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in REASONS else "unavailable"
        super().__init__(self.reason)


def reason_for(error):
    if type(error) is DiagnosticError:
        return error.reason
    reason = _bootstrap_failure("request", error)["reason"]
    if reason == "candidate_unavailable" and type(error) is HostAdapterError:
        code = error.code
        if type(code) is str and code in REASONS:
            return code
    return reason if reason in REASONS else "unavailable"


def _fixed(value, allowed, fallback):
    return value if type(value) is str and value in allowed else fallback


@dataclass
class CallDiagnostics:
    """One caller only: workers never share or mutate this observation."""

    stage: str = "request"
    reason: str | None = None
    local_state: str = "not_attempted"
    host_mutation: str = "not_dispatched"
    retained_effects: str = "not_inspected"
    cleanup: str = "not_attempted"
    host_failure: dict | None = None
    read_failure: dict | None = None
    hook_caller_relation: str | None = None
    hook_executor_context: str | None = None
    _collection_effects: dict = field(default_factory=dict, repr=False)
    _record_effects: str | None = field(default=None, repr=False)

    def _with_collection_effects(self, current=None):
        states = [value[0] for value in self._collection_effects.values()]
        if current is not None:
            states.append(current)
        return ("not_inspected" if "not_inspected" in states else
            "present_unresolved" if "present_unresolved" in states else
            "settled" if "settled" in states else "absent")

    @contextmanager
    def collecting(self, repository):
        """Observe only this call's bounded, complete collection inventories."""
        key = repository.path
        previous = repository._observation
        self._collection_effects.setdefault(key, ("not_inspected", False))

        def observed(event, records):
            _state, unresolved = self._collection_effects[key]
            state = "not_inspected"
            if event == "writing":
                self.local_state = "may_have_changed"
            elif event == "observed":
                publications = [attempt["publication"] for row in records for attempt in row["attempts"]
                                if attempt["publication"] is not None]
                corrections = [row["correction"] for row in records if row["correction"] is not None]
                registrations = [row["registration"]["state"] for row in records]
                unresolved = (any(value["state"] != "saved" for value in publications)
                    or any(value["effect"] == "unknown" or value["effect"] == "accepted"
                           and value["correction_turn_id"] is None for value in corrections)
                    or any(value in ("dispatched", "unknown") for value in registrations))
                state = ("present_unresolved" if unresolved else "settled"
                    if publications or corrections or any(value != "not_started" for value in registrations)
                    else "absent")
            self._collection_effects[key] = (state, unresolved)
            self.retained_effects = self._with_collection_effects(self._record_effects)

        repository._observation = observed
        try:
            yield
        finally:
            repository._observation = previous

    def capture(self, *, error=None, result=None):
        read = validated_read_failure(result.get("read_failure")) if type(result) is dict else read_failure(error)
        if read is not None:
            self.read_failure = read
        supplied = result.get("host_failure") if type(result) is dict else host_failure(error)
        if (type(supplied) is dict and set(supplied) == {"operation", "reason", "termination"}
                and type(supplied["operation"]) is str and supplied["operation"] in HOST_OPERATIONS
                and type(supplied["reason"]) is str and supplied["reason"] in HOST_BOUNDARY_REASONS
                and type(supplied["termination"]) is str and supplied["termination"] in HOST_TERMINATION):
            self.host_failure = dict(supplied)
        if type(result) is dict:
            stage, reason = result.get("stage"), result.get("reason", result.get("error"))
            if type(stage) is str and stage in STAGES:
                self.stage = stage
            self.reason = reason if type(reason) is str and reason in REASONS else "unavailable"
        else:
            self.reason = reason_for(error)

    def capture_start(self, result):
        """A direct summary may succeed as a read while its arm failed."""
        self.capture(result=result)
        if self.reason == "unavailable" and type(result) is dict:
            state = result.get("state")
            reason = state.get("reason") if type(state) is dict else None
            self.reason = _fixed(reason, REASONS, "start_unavailable")

    def invalidate_record(self):
        self.retained_effects = "not_inspected"

    @contextmanager
    def reading_retained(self):
        """A supplemental read must succeed before prior evidence is reusable."""
        observed = self.retained_effects
        self.invalidate_record()
        yield
        self.retained_effects = observed

    @contextmanager
    def retained_context(self, context):
        """Invalidate failed lease admission/exit, preserving body diagnostics."""
        body_error = None
        try:
            with context as value:
                try:
                    yield value
                except BaseException as error:
                    body_error = error
                    raise
        except BaseException as error:
            if error is not body_error:
                self.invalidate_record()
            raise

    def observe_record(self, record):
        self._record_effects = ("absent" if record is None else
                               "settled" if record.phase == "closed" else "present_unresolved")
        self.retained_effects = self._with_collection_effects(self._record_effects)

    def observe_operation(self, result, previous):
        self.invalidate_record()
        if result.get("ok") is True:
            self.retained_effects = previous
        elif result.get("error") == "timer_cleanup_unknown":
            # DirectProbe returns this only after reading an unresolved timer
            # or saving its unknown phase. A failed read/write uses another error.
            self.retained_effects = "present_unresolved"

    def failure(self, result, *, error=None):
        if self.retained_effects in ("absent", "settled"):
            # A later wait-only observation cannot settle collection effects.
            # Failed fresh reads keep not_inspected instead of reviving facts.
            self.retained_effects = self._with_collection_effects(self.retained_effects)
        if error is not None and self.reason is None:
            self.capture(error=error)
        reason = self.reason
        if reason is None:
            reason = reason_for(error) if error is not None else result.get("error")
        if type(reason) is not str or reason not in REASONS:
            reason = "unavailable"
        stage = self.stage if type(self.stage) is str and self.stage in STAGES else "request"
        recovery = "report_and_resume"
        if reason == "review_wait_not_enabled":
            recovery = "respect_off"
        elif reason in {"policy_unreadable", "invalid_host_configuration"}:
            recovery = "check_configuration"
        elif stage == "project_admission":
            recovery = "check_project"
        elif stage == "executor_admission" or reason == "invalid_request":
            recovery = "correct_request"
        elif reason in HOST_BOUNDARY_REASONS or reason == "host_transport_unavailable":
            recovery = "check_connection"
        if (self.host_mutation == "may_have_occurred" or self.cleanup == "unresolved"
                or self.retained_effects == "present_unresolved"
                or any(value[1] for value in self._collection_effects.values())
                or (self.local_state == "may_have_changed"
                    and self.retained_effects == "not_inspected")):
            recovery = "inspect_existing"
        diagnostic = {
            "version": 1, "stage": stage, "reason": reason,
            "local_state": _fixed(self.local_state,
                {"not_attempted", "may_have_changed"}, "may_have_changed"),
            "host_mutation": _fixed(self.host_mutation,
                {"not_dispatched", "may_have_occurred", "confirmed"}, "may_have_occurred"),
            "retained_effects": _fixed(self.retained_effects,
                {"not_inspected", "absent", "present_unresolved", "settled"}, "not_inspected"),
            "cleanup": _fixed(self.cleanup,
                {"not_attempted", "unresolved", "confirmed"}, "unresolved"),
            "recovery": recovery, "turn_end": "report_limitation",
        }
        if self.host_failure is not None:
            diagnostic["host_failure"] = self.host_failure
        read = validated_read_failure(self.read_failure)
        if read is not None:
            diagnostic["read_failure"] = read
        if (stage == "operation" and reason == "caller_mismatch"
                and result.get("ok") is False and result.get("error") == "caller_mismatch"
                and type(self.hook_caller_relation) is str
                and self.hook_caller_relation in {"event_reviewer", "other"}):
            diagnostic["hook_caller_relation"] = self.hook_caller_relation
        if (stage == "operation" and reason == "executor_context_required"
                and result.get("ok") is False and result.get("error") == "review_wait_unavailable"
                and type(self.hook_executor_context) is str
                and self.hook_executor_context in EXECUTOR_TURN_FAILURES):
            diagnostic["hook_executor_context"] = self.hook_executor_context
        # All members are fixed vocabulary, independent of rejected input size.
        assert len(json.dumps(diagnostic).encode("utf-8")) <= MAX_DIAGNOSTIC_BYTES
        return {**result, "diagnostic": diagnostic}
