"""Explicit source-only, one-attempt same-parent direct-wake experiment.

The worker belongs to its MCP session. The explicitly selected deletion variant
owns one fallback timer; no Task, usage or production configuration is changed.
Accepted sending and received acknowledgement are
separate facts. A lost process or response never authorizes another send.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from task_governance_tool.review_wait_runtime.review_wait_host import HOST_BOUNDARY_REASONS, HostAdapterError, admit_executor_metadata, executor_turn_id
from task_governance_tool.review_wait_runtime.review_wait_basis import BasisError
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository, RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_direct_repository import DirectRecord, DirectRepository, DIRECT_REASONS, instant, marker_exists
from task_governance_tool.review_wait_runtime.review_wait_runtime import reservation


class DirectError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in DIRECT_REASONS else "direct_probe_unavailable"
        super().__init__(self.code)


def _reason(error):
    if type(error) in (DirectError, HostAdapterError, RepositoryError, BasisError):
        code = getattr(error, "code", None)
        if type(error) is HostAdapterError and code == "host_call_failed":
            detail = getattr(error, "boundary_reason", None)
            if type(detail) is str and detail in HOST_BOUNDARY_REASONS:
                return detail
        if type(code) is str and code in DIRECT_REASONS:
            return code
    return "direct_probe_unavailable"


def binding_digest(controller) -> str:
    value = {"binding": asdict(controller.binding), "reviewers": [asdict(r) for r in controller.reviewers],
             "reservation": asdict(controller.reservation)}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _terminal(turn) -> bool:
    return (turn.status in {"completed", "failed", "interrupted"}
            and turn.thread_status in {"idle", "notLoaded", "systemError"})


class DirectProbe:
    def __init__(self, repository_path: Path, current_binding, host_factory, clock,
                 *, cycle_seconds: float = 1, join_seconds: float = 40):
        if (not callable(current_binding) or not callable(host_factory) or not callable(clock)
                or type(cycle_seconds) not in (int, float) or not 0 < cycle_seconds <= 30
                or type(join_seconds) not in (int, float) or not 0 < join_seconds <= 60):
            raise DirectError("invalid_configuration")
        self.wait = ReviewWaitRepository(repository_path)
        self.repository = DirectRepository.for_wait(repository_path)
        self.current_binding, self.host_factory, self.clock = current_binding, host_factory, clock
        self._cycle, self._join = cycle_seconds, join_seconds
        self._stop = threading.Event()
        self._request = threading.RLock()
        self._worker = None
        self._owned_probe_id = None
        self._host = None
        self._closed = False

    def blocks_timer(self) -> bool:
        return marker_exists(self.wait.path)

    def _now(self):
        value = self.clock()
        instant(value)
        return value.astimezone(timezone.utc)

    def _expired(self, record, monotonic_deadline):
        now = self._now()
        if now < datetime.fromisoformat(record.created_at):
            raise DirectError("clock_changed")
        return now >= datetime.fromisoformat(record.deadline) or time.monotonic() >= monotonic_deadline

    def _halt_wait(self, record, monotonic_deadline):
        if self._stop.is_set() or self.repository.read().status != "waiting":
            return True
        if self._expired(record, monotonic_deadline):
            self.repository.update(record.probe_id, status="expired")
            return True
        return False

    def _admit(self, metadata, controller):
        metadata = admit_executor_metadata(metadata)
        if metadata["threadId"] != controller.binding.parent_thread_id:
            raise DirectError("caller_mismatch")
        return metadata

    def _prepared(self, controller):
        if (controller.arm_number != 0 or controller.phase != "preparing" or controller.pending is not None
                or controller.reservation.status != "PAUSED" or not controller.stop_confirmed):
            raise DirectError("wait_not_prepared")
        if self.current_binding() != controller.binding:
            raise DirectError("task_binding_changed")

    def _heartbeat(self, host, controller, record=None, *, cleanup=False):
        deleting = record is not None and record.version == 2
        observed = host.view_heartbeat(for_pause=cleanup or not deleting)
        expected = controller.reservation
        if deleting:
            expected = replace(expected, status="ACTIVE", rule=record.timer_rule)
        if (reservation(observed) != expected or deleting and
                observed.updated_at != record.timer_updated_at):
            raise DirectError("heartbeat_changed")
        return observed

    def _summary(self):
        if not self.repository.exists():
            return {"ok": True, "state": None, "worker_alive": False}
        record = self.repository.read()
        state = asdict(record)
        state.pop("binding_digest")
        if record.version == 1:
            for field in ("version", "timer_phase", "timer_rule", "timer_updated_at", "timer_due_at"):
                state.pop(field)
        alive = self._worker is not None and self._worker.is_alive()
        if record.status == "dispatching" and not alive:
            state["status"] = "unknown"
        state["received"] = record.acknowledged_turn is not None
        return {"ok": True, "state": state, "worker_alive": alive}

    def handle(self, operation, args, metadata):
        with self._request:
            try:
                if operation not in {"direct_start", "direct_delete_start", "direct_status", "direct_cancel", "direct_ack"}:
                    raise DirectError("invalid_request")
                required = set() if operation in {"direct_start", "direct_delete_start", "direct_status"} else {"probe_id"}
                if type(args) is not dict or set(args) != required:
                    raise DirectError("invalid_request")
                controller = self.wait.read().controller
                metadata = self._admit(metadata, controller)
                if operation == "direct_status":
                    return self._summary()
                if self._closed:
                    raise DirectError("service_closed")
                if operation in {"direct_start", "direct_delete_start"}:
                    return self._start(metadata, delete_timer=operation == "direct_delete_start")
                record = self.repository.read()
                if args["probe_id"] != record.probe_id or binding_digest(controller) != record.binding_digest:
                    raise DirectError("probe_mismatch")
                if operation == "direct_cancel":
                    result = self._stop_worker("cancelled")
                    if not result["ok"]:
                        return result
                    self._cleanup(self.host_factory(metadata))
                    return self._summary()
                turn_id = executor_turn_id(metadata)
                if turn_id == record.original_parent_turn:
                    raise DirectError("ack_turn_mismatch")
                parent = self.host_factory(metadata).read_parent()
                if parent.child_id != controller.binding.parent_thread_id or parent.turn_id != turn_id:
                    raise DirectError("ack_turn_mismatch")
                self.repository.update(record.probe_id, acknowledged_turn=turn_id)
                return self._summary()
            except DirectError as error:
                return {"ok": False, "error": _reason(error)}
            except Exception as error:
                return {"ok": False, "error": "direct_probe_unavailable", "reason": _reason(error)}

    def _start(self, metadata, *, delete_timer=False):
        with self.wait.writer() as lease:
            if self.repository.exists():
                raise DirectError("probe_already_exists")
            controller = lease.read().controller
            self._admit(metadata, controller)
            self._prepared(controller)
            turn_id = executor_turn_id(metadata)
            host = self.host_factory(metadata)
            parent = host.read_parent()
            if (parent.child_id != controller.binding.parent_thread_id or parent.turn_id != turn_id
                    or parent.status != "inProgress" or parent.thread_status != "active"):
                raise DirectError("parent_turn_mismatch")
            self._heartbeat(host, controller)
            self._prepared(controller)
            now = self._now()
            extras = {}
            if delete_timer:
                from task_governance_tool.review_wait_runtime.review_wait_service import ten_minute_appointment
                appointment = ten_minute_appointment(self.clock, controller.reservation.timezone)
                now = appointment.chosen_at
                before = host.view_heartbeat()
                if reservation(before) != controller.reservation:
                    raise DirectError("heartbeat_changed")
                self._prepared(controller)
                extras = dict(version=2, timer_phase="arming", timer_rule=appointment.rule,
                              timer_updated_at=before.updated_at, timer_due_at=instant(appointment.due_at))
            duration = 1200 if delete_timer else 600
            record = DirectRecord(str(uuid4()), binding_digest(controller), turn_id, "waiting",
                                  instant(now), instant(now + timedelta(seconds=duration)), **extras)
            self.repository.create_record(record)
            self._owned_probe_id = record.probe_id
            self._host = host
            if delete_timer:
                try:
                    after = host.update_heartbeat(before, "arm", rrule=record.timer_rule)
                    if (reservation(after) != replace(controller.reservation, status="ACTIVE", rule=record.timer_rule)
                            or after.updated_at < before.updated_at):
                        raise DirectError("heartbeat_update_unknown")
                    record = self.repository.update(record.probe_id, timer_phase="active", timer_updated_at=after.updated_at)
                except Exception as error:
                    self.repository.update(record.probe_id, status="suppressed", timer_phase="unknown", reason=_reason(error))
                    return self._summary()
        self._worker = threading.Thread(target=self._run, args=(host, record, time.monotonic() + duration), name="review-direct-probe", daemon=False)
        try:
            self._worker.start()
        except Exception:
            self.repository.update(record.probe_id, status="abandoned")
            self._cleanup(host)
            raise DirectError("worker_unavailable") from None
        return self._summary()

    def _run(self, host, record, monotonic_deadline):
        try:
            while not self._stop.is_set():
                if self._halt_wait(record, monotonic_deadline):
                    return
                controller = self.wait.read().controller
                if binding_digest(controller) != record.binding_digest:
                    raise DirectError("wait_changed")
                self._prepared(controller)
                all_ended = True
                for expected in controller.reviewers:
                    if self._halt_wait(record, monotonic_deadline):
                        return
                    child = self._read_reviewer(host, controller, expected)
                    if self._halt_wait(record, monotonic_deadline):
                        return
                    if child.child_id != expected.reviewer_id or child.turn_id != expected.turn_id:
                        raise DirectError("reviewer_turn_changed")
                    all_ended = _terminal(child) and all_ended
                if self._halt_wait(record, monotonic_deadline):
                    return
                parent = host.read_parent()
                if self._halt_wait(record, monotonic_deadline):
                    return
                self._parent_ready(parent, controller, record, require_idle=False)
                if all_ended and _terminal(parent) and parent.thread_status == "idle":
                    if self._dispatch(host, record, monotonic_deadline) is not False:
                        return
                if self._stop.wait(self._cycle):
                    return
        except Exception as error:
            try:
                current = self.repository.read()
                if current.status == "waiting":
                    self.repository.update(record.probe_id, status="suppressed", reason=_reason(error))
                elif current.status == "dispatching":
                    self.repository.update(record.probe_id, status="unknown", reason=_reason(error))
            except Exception:
                pass  # Durable dispatching remains unknown; never retry.
        finally:
            try:
                self._cleanup(host)
            except Exception:
                pass  # The retained active/pending phase exposes unresolved cleanup.

    def _observe_reviewer(self, controller, observation):
        """Legacy waits have no integrated result journal."""
        return None

    def _parent_ready(self, parent, controller, record, *, require_idle=True):
        """The explicit legacy experiment remains fixed to its starting turn."""
        if (parent.child_id != controller.binding.parent_thread_id
                or parent.turn_id != record.original_parent_turn
                or require_idle and (not _terminal(parent) or parent.thread_status != "idle")):
            raise DirectError("parent_turn_changed")
        return True

    def _read_reviewer(self, host, controller, expected, *, terminal=False, reviewer_observer=None):
        observe = reviewer_observer or (lambda observation: self._observe_reviewer(controller, observation))
        try:
            child = host.read_child(expected.reviewer_id)
        except Exception:
            observe((expected.reviewer_id, expected.turn_id, "unknown"))
            raise
        exact = child.child_id == expected.reviewer_id and child.turn_id == expected.turn_id
        if exact and (_terminal(child) or child.status in {"failed", "interrupted"}):
            observe((expected.reviewer_id, expected.turn_id, child.status))
        elif not exact or terminal:
            observe((expected.reviewer_id, expected.turn_id, "unknown"))
        return child

    def _idle_and_ended(self, host, controller, record, monotonic_deadline, *, reviewer_observer=None):
        """Fresh boundary checks; integrated callers retain every reviewer read."""
        for expected in controller.reviewers:
            if self._halt_wait(record, monotonic_deadline):
                return False
            child = self._read_reviewer(host, controller, expected, terminal=True,
                                       reviewer_observer=reviewer_observer)
            exact_terminal = (child.child_id == expected.reviewer_id and child.turn_id == expected.turn_id
                              and _terminal(child))
            if not exact_terminal:
                raise DirectError("reviewer_turn_changed")
        if self._halt_wait(record, monotonic_deadline):
            return False
        parent = host.read_parent()
        if not self._parent_ready(parent, controller, record):
            return False
        self._prepared(controller)
        return not self._halt_wait(record, monotonic_deadline)

    def _dispatch(self, host, record, monotonic_deadline):
        with self.wait.writer() as lease:
            controller = lease.read().controller
            if binding_digest(controller) != record.binding_digest:
                raise DirectError("wait_changed")
            self._prepared(controller)
            if self._halt_wait(record, monotonic_deadline):
                return
            current = self.repository.read()
            if (record.version == 2 and current.timer_phase == "active"
                    and self._now() >= datetime.fromisoformat(record.timer_due_at)):
                return False  # Keep observing the scheduled fallback; do not pause it.
            if record.version != 2 or current.timer_phase != "deleted":
                observed = self._heartbeat(host, controller, current)
            if record.version == 2 and current.timer_phase != "deleted":
                if current.timer_phase != "active":
                    raise DirectError("heartbeat_changed")
                if not self._idle_and_ended(host, controller, record, monotonic_deadline):
                    return False
                if self._now() >= datetime.fromisoformat(record.timer_due_at):
                    return False
                self.repository.update(record.probe_id, timer_phase="deleting")
                try:
                    host.delete_heartbeat(observed)
                except Exception as error:
                    self.repository.update(record.probe_id, timer_phase="unknown")
                    raise DirectError(_reason(error)) from None
                self.repository.update(record.probe_id, timer_phase="deleted")
            if not self._idle_and_ended(host, controller, record, monotonic_deadline):
                return False
            self._before_send(host, controller, record, monotonic_deadline)
            if self._halt_wait(record, monotonic_deadline):
                return
            parent = host.read_parent()
            if self._halt_wait(record, monotonic_deadline):
                return
            if not self._parent_ready(parent, controller, record):
                return False
            self._prepared(controller)
            if self._stop.is_set():
                return
            if self._expired(record, monotonic_deadline):
                self.repository.update(record.probe_id, status="expired")
                return
            # Transient receipt fence belongs to this single attempt. Restart
            # never resumes observation or sends, so it needs no new payload.
            self._send_parent_turn = parent.turn_id
            self.repository.update(record.probe_id, status="dispatching")
            # Once dispatching is durable, cancellation cannot claim the effect
            # never ran. Keep the wait lease until the single call settles.
            reason = None
            try:
                outcome = host.send_direct_probe(record.probe_id)
            except Exception as error:
                outcome = "unknown"
                reason = _reason(error)
            if type(outcome) is not str or outcome not in {"accepted", "rejected", "unknown"}:
                outcome = "unknown"
            if reason is None and outcome != "accepted":
                reason = "send_" + outcome
            self.repository.update(record.probe_id, status=outcome, reason=reason)

    def _before_send(self, host, controller, record, monotonic_deadline):
        """Legacy waiting has no Task/result effects at this extension point."""
        return None

    def _cleanup(self, host):
        if not self.repository.exists():
            return
        initial = self.repository.read()
        if initial.version != 2 or initial.timer_phase in {"deleted", "paused"}:
            return
        if initial.timer_phase != "active":
            raise DirectError("timer_cleanup_unknown")
        with self.wait.writer() as lease:
            current = self.repository.read()
            if current.timer_phase in {"deleted", "paused"}:
                return
            if current.timer_phase != "active":
                raise DirectError("timer_cleanup_unknown")
            controller = lease.read().controller
            if binding_digest(controller) != current.binding_digest:
                raise DirectError("wait_changed")
            # Cleanup retains original identity even after the Task basis changes.
            before = self._heartbeat(host, controller, current, cleanup=True)
            self.repository.update(current.probe_id, timer_phase="pausing")
            try:
                after = host.update_heartbeat(before, "pause")
                if (reservation(after) != replace(reservation(before), status="PAUSED")
                        or after.updated_at < before.updated_at):
                    raise DirectError("heartbeat_update_unknown")
                self.repository.update(current.probe_id, timer_phase="paused", timer_updated_at=after.updated_at)
            except Exception:
                self.repository.update(current.probe_id, timer_phase="unknown")
                raise DirectError("timer_cleanup_unknown") from None

    def _stop_worker(self, status, *, owned_only=False):
        self._stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(self._join)
        if worker is not None and worker.is_alive():
            return {"ok": False, "error": "observer_cleanup_unknown"}
        if (not owned_only or self._owned_probe_id is not None) and self.repository.exists():
            record = self.repository.read()
            if record.status == "waiting" and (not owned_only or record.probe_id == self._owned_probe_id):
                self.repository.update(record.probe_id, status=status)
        return {"ok": True}

    def close(self):
        with self._request:
            self._closed = True
            try:
                result = self._stop_worker("abandoned", owned_only=True)
                if result["ok"] and self._owned_probe_id is not None:
                    self._cleanup(self._host)
                return result
            except Exception:
                return {"ok": False, "error": "observer_cleanup_unknown"}
