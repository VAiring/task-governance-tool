"""Source-only request admission and controls for one existing review wait.

No server is installed and no store, timer or executor identity is created.
Each request must carry genuine executor metadata supplied by the MCP entry
point. Stored identity is used for comparison only. The observer may retain a
request's admitted host context in memory until stopped; nothing persists it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from threading import RLock
from typing import Callable
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from task_governance_tool.review_wait_runtime.review_wait_controller import Appointment, ControllerError, WaitBinding
from task_governance_tool.review_wait_runtime.review_wait_host import PublicMcpHost, admit_executor_metadata
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository, StoredWait
from task_governance_tool.review_wait_runtime.review_wait_runtime import ReviewWaitRuntime


MAX_METADATA_BYTES = 16_384
MAX_ARGUMENT_BYTES = 1_024
_OPERATIONS = {
    "view": frozenset(), "arm": frozenset(),
    "check": frozenset({"expected_arm", "wake_id", "wake_time"}),
    "rearm": frozenset({"expected_arm", "healthy"}),
    "cancel": frozenset({"expected_arm"}),
}
_SAFE_ERRORS = frozenset({
    "invalid_request", "executor_context_required", "caller_mismatch", "invalid_configuration",
    "state_binding_changed", "service_closed", "observer_cleanup_unknown", "observer_unavailable",
    "appointment_unavailable", "wait_not_ready", "operation_failed", "writer_busy", "revision_conflict",
    "state_unreadable", "state_path_invalid", "unsupported_journal_mode", "binding_mismatch",
    "state_transition_invalid", "invalid_snapshot", "task_binding_unavailable", "task_binding_changed",
    "host_binding_mismatch", "reviewer_turn_changed", "host_transport_unavailable", "host_call_failed",
    "invalid_host_response", "heartbeat_changed", "heartbeat_update_unknown", "child_status_unavailable",
    "automation_config_unavailable", "automation_config_invalid", "automation_config_unsafe",
    "automation_config_changed", "timezone_not_admitted", "timezone_changed", "timezone_unavailable",
    "direct_probe_selected",
})


class ServiceError(ValueError):
    def __init__(self, code: str):
        self.code = code if code in _SAFE_ERRORS else "operation_failed"
        super().__init__(self.code)


def _require(condition: bool, code: str = "invalid_request") -> None:
    if not condition:
        raise ServiceError(code)


def _uuid(value: object, code: str) -> str:
    try:
        _require(type(value) is str and str(UUID(value)) == value, code)
    except (ValueError, AttributeError, TypeError):
        raise ServiceError(code) from None
    return value


def _bounded_json(value: object, maximum: int, code: str) -> None:
    """Bound nesting and values before encoding; never echo rejected content."""
    count = 0

    def visit(item: object, depth: int) -> None:
        nonlocal count
        count += 1
        _require(depth <= 16 and count <= 512, code)
        kind = type(item)
        if item is None or kind is bool:
            return
        if kind is str:
            _require(len(item) <= maximum, code)
        elif kind is int:
            _require(abs(item) <= 2**53 - 1, code)
        elif kind is float:
            _require(math.isfinite(item), code)
        elif kind in (dict, list):
            _require(len(item) <= 128, code)
            if kind is dict:
                for key, nested in item.items():
                    _require(type(key) is str and len(key) <= 128, code)
                    visit(key, depth + 1)
                    visit(nested, depth + 1)
            else:
                for nested in item:
                    visit(nested, depth + 1)
        else:
            raise ServiceError(code)

    try:
        visit(value, 0)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        _require(len(encoded) <= maximum, code)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ServiceError(code) from None


def _now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
        _require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
        return value.astimezone(timezone.utc)
    except Exception:
        raise ServiceError("appointment_unavailable") from None


def _zone(name: str):
    try:
        if name in ("UTC", "Etc/UTC", "Etc/GMT", "GMT"):
            return timezone.utc
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ServiceError("appointment_unavailable") from None


def ten_minute_appointment(clock, timezone_name: str) -> Appointment:
    try:
        chosen = _now(clock).replace(microsecond=0)
        due = chosen + timedelta(minutes=10)
        local = due.astimezone(_zone(timezone_name))
        rule = f"FREQ=DAILY;BYHOUR={local.hour};BYMINUTE={local.minute};BYSECOND={local.second};COUNT=1"
        return Appointment(chosen, due, rule, timezone_name)
    except (ControllerError, ValueError, OverflowError):
        raise ServiceError("appointment_unavailable") from None


@dataclass(frozen=True)
class ServiceConfig:
    repository_path: Path
    server_path: Path
    codex_home: Path
    confirmed_timezone: str
    timezone_source: str

    def __post_init__(self) -> None:
        try:
            for name in ("repository_path", "server_path", "codex_home"):
                value = Path(getattr(self, name))
                _require(value.is_absolute() and ".." not in value.parts and "\x00" not in str(value),
                         "invalid_configuration")
                object.__setattr__(self, name, value)
            _require(self.server_path.name == "server.mjs", "invalid_configuration")
            _require(type(self.confirmed_timezone) is str and
                     bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/+.-]{0,127}", self.confirmed_timezone)),
                     "invalid_configuration")
            _require(type(self.timezone_source) is str and self.timezone_source in ("host_node_intl", "host_os"),
                     "invalid_configuration")
        except (TypeError, ValueError):
            raise ServiceError("invalid_configuration") from None


class ReviewWaitService:
    """Closed parent controls; injection seams exist only for offline tests.

    The caller must authenticate the origin of ``metadata``. Its thread ID is
    checked against the pinned parent; no dictionary alone proves authenticity.
    ``current_binding`` is a read-only provider for the original Task. This
    service cannot assert host timezone equivalence or post-turn admission.
    """

    def __init__(self, config: ServiceConfig, current_binding: Callable[[], WaitBinding],
                 clock: Callable[[], datetime], *, host_factory=None, observer_factory=None):
        try:
            _require(type(config) is ServiceConfig and callable(current_binding) and callable(clock),
                     "invalid_configuration")
            self._config, self._binding_provider, self._clock = config, current_binding, clock
            self._repository = ReviewWaitRepository.open_existing(config.repository_path)
            initial = self._repository.read().controller
            self._binding, self._reviewers = initial.binding, initial.reviewers
            self._identity = self._reservation_identity(initial.reservation)
            _uuid(self._binding.parent_thread_id, "invalid_configuration")
            for reviewer in self._reviewers:
                _uuid(reviewer.reviewer_id, "invalid_configuration")
                _uuid(reviewer.turn_id, "invalid_configuration")
            _require(config.confirmed_timezone == initial.reservation.timezone, "invalid_configuration")
            self._host_factory = host_factory or PublicMcpHost.from_environment
            if observer_factory is None:
                from task_governance_tool.review_wait_runtime.review_wait_observer import ReviewWaitObserver
                observer_factory = ReviewWaitObserver
            _require(callable(self._host_factory) and callable(observer_factory), "invalid_configuration")
            self._observer_factory, self._observer = observer_factory, None
            self._lock, self._closed = RLock(), False
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("invalid_configuration") from None

    @staticmethod
    def _reservation_identity(value) -> tuple:
        return value.timer_id, value.parent_thread_id, value.identity_digest

    def _read(self) -> StoredWait:
        stored = self._repository.read()
        controller = stored.controller
        _require(controller.binding == self._binding and controller.reviewers == self._reviewers and
                 self._reservation_identity(controller.reservation) == self._identity,
                 "state_binding_changed")
        return stored

    def _metadata(self, metadata: object) -> dict:
        _require(type(metadata) is dict, "executor_context_required")
        _bounded_json(metadata, MAX_METADATA_BYTES, "executor_context_required")
        admitted = admit_executor_metadata(metadata)
        _require(admitted["threadId"] == self._binding.parent_thread_id, "caller_mismatch")
        return admitted  # original genuine object; no inserted or rewritten field

    def _arguments(self, operation: object, arguments: object) -> dict:
        _require(type(operation) is str and operation in _OPERATIONS and type(arguments) is dict)
        _bounded_json(arguments, MAX_ARGUMENT_BYTES, "invalid_request")
        _require(set(arguments) == _OPERATIONS[operation])
        if "expected_arm" in arguments:
            value = arguments["expected_arm"]
            _require(type(value) is int and 0 <= value <= 2**53 - 1)
        if operation == "rearm":
            _require(type(arguments["healthy"]) is bool)
        if operation == "check":
            wake = arguments["wake_id"]
            _require(type(wake) is str and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}", wake)))
            value = arguments["wake_time"]
            _require(type(value) is str and len(value) <= 40)
            try:
                parsed = datetime.fromisoformat(value)
                _require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0))
                _require(parsed.isoformat() == value and parsed <= _now(self._clock))
            except (ValueError, TypeError, OverflowError):
                raise ServiceError("invalid_request") from None
            return {**arguments, "wake_time": parsed}
        return dict(arguments)

    def _appointment(self) -> Appointment:
        return ten_minute_appointment(self._clock, self._config.confirmed_timezone)

    def _runtime(self, metadata: dict) -> ReviewWaitRuntime:
        config = self._config
        host = self._host_factory(metadata=metadata, server_path=config.server_path,
            automation_id=self._identity[0], codex_home=config.codex_home,
            confirmed_timezone=config.confirmed_timezone, timezone_source=config.timezone_source,
            reviewer_ids=tuple(item.reviewer_id for item in self._reviewers))
        return ReviewWaitRuntime(self._repository, host, self._binding_provider, self._clock)

    def _stop_observer(self) -> None:
        if self._observer is None:
            return
        try:
            snapshot = self._observer.stop()
            _require(type(snapshot.worker_alive) is bool and not snapshot.worker_alive, "observer_cleanup_unknown")
        except Exception:
            raise ServiceError("observer_cleanup_unknown") from None
        self._observer = None

    def _start_observer(self, runtime: ReviewWaitRuntime) -> None:
        try:
            self._observer = self._observer_factory(runtime)
            self._observer.start()
            snapshot = self._observer.snapshot()
            _require(type(snapshot.worker_alive) is bool, "observer_unavailable")
            # A completed all-ended decision can stop a fast worker immediately.
            _require((snapshot.worker_alive and snapshot.state == "running") or
                     (not snapshot.worker_alive and snapshot.state in ("stopped", "ended", "closed", "checking", "pending")),
                     "observer_unavailable")
        except Exception:
            raise ServiceError("observer_unavailable") from None

    def _response(self, stored: StoredWait) -> dict:
        controller = stored.controller
        value = controller.to_snapshot()
        pending = controller.pending
        observer = {"state": "not_started", "worker_alive": False, "reason": None}
        if self._observer is not None:
            snapshot = self._observer.snapshot()
            _require(type(snapshot.worker_alive) is bool, "observer_unavailable")
            states = {"new", "running", "stopping", "stopped", "ended", "closed", "checking",
                      "pending", "error", "cleanup_unknown"}
            reasons = {"worker_start_failed", "worker_join_timeout", "operation_pending", "reconciliation_required",
                       "invalid_wait_state", "invalid_wait_result", "observation_failed", "observer_failed"}
            state = snapshot.state if type(snapshot.state) is str and snapshot.state in states else "error"
            reason = snapshot.reason
            if reason is not None and (type(reason) is not str or reason not in reasons):
                state, reason = "error", "observer_failed"
            observer = {"state": state, "worker_alive": snapshot.worker_alive, "reason": reason}
        running = observer["worker_alive"] and observer["state"] == "running"
        wait_ready = controller.ready and (controller.all_ended or running)
        return {"ok": True, "state": {
            "wait_id": controller.binding.wait_id, "revision": stored.revision,
            "arm": controller.arm_number, "phase": controller.phase, "ready": controller.ready,
            "wait_ready": wait_ready,
            "all_ended": controller.all_ended, "stop_confirmed": controller.stop_confirmed,
            "needs_reconciliation": controller.needs_reconciliation,
            "pending_operation": None if pending is None else {"operation_id": pending.operation_id, "kind": pending.kind},
            "terminal_count": len(value["terminal"]), "reviewer_count": len(controller.reviewers),
            "appointment_due_at": None if value["appointment"] is None else value["appointment"]["due_at"],
        }, "observer_running": running, "observer": observer}

    def handle(self, operation: object, arguments: object, metadata: object) -> dict:
        try:
            with self._lock:
                _require(not self._closed, "service_closed")
                admitted = self._metadata(metadata)
                arguments = self._arguments(operation, arguments)
                stored = self._read()
                if operation == "view":
                    return self._response(stored)
                # A later request never rewrites a running worker's context.
                # Join it first; an uncertain stop cannot claim control success.
                self._stop_observer()
                runtime = self._runtime(admitted)
                if operation == "arm":
                    stored = runtime.arm(self._appointment())
                elif operation == "rearm":
                    stored = runtime.rearm(appointment=self._appointment(), **arguments)
                elif operation == "check":
                    stored = runtime.check(**arguments)
                else:
                    stored = runtime.cancel(**arguments)
                # An early/stale check or cancel can leave the current arm
                # untouched. Keep its observer running after joining the old
                # context, using this newly admitted request's context.
                if stored.controller.ready:
                    self._start_observer(runtime)
                response = self._response(self._read())
                if response["state"]["ready"] and not response["state"]["wait_ready"]:
                    raise ServiceError("observer_unavailable")
                if operation in ("arm", "rearm") and not response["state"]["wait_ready"]:
                    raise ServiceError("wait_not_ready")
                return response
        except Exception as exc:
            code = getattr(exc, "code", "operation_failed")
            return {"ok": False, "error": code if type(code) is str and code in _SAFE_ERRORS else "operation_failed"}

    def close(self) -> dict:
        """Bounded observer shutdown; EOF alone never changes a host timer."""
        try:
            with self._lock:
                self._closed = True
                self._stop_observer()
                return {"ok": True}
        except Exception:
            return {"ok": False, "error": "observer_cleanup_unknown"}
