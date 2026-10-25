"""Explicit source-only, one-attempt same-parent direct-wake experiment.

The worker belongs to its MCP session. The explicitly selected deletion variant
owns one fallback timer; no Task, usage or production configuration is changed.
Accepted sending and received acknowledgement are
separate facts. A lost process or response never authorizes another send.
"""

from __future__ import annotations
from contextlib import nullcontext

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from task_governance_tool.review_wait_runtime.review_wait_host import HOST_BOUNDARY_REASONS, HostAdapterError, admit_executor_metadata, executor_turn_id, host_failure, read_failure
from task_governance_tool.review_wait_runtime.review_wait_basis import BasisError
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository, RepositoryError, ObservationBusy
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
                 *, cycle_seconds: float = 10, join_seconds: float = 40, diagnostic_scope=None):
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
        self._owned_worker_id = None
        self._worker_stop_persistence = "not_recorded"
        self._termination_observer = None
        self._reviewer_observations = {}
        self._observations_lock = threading.Lock()
        self._host = None
        self._closed = False
        self._diagnostic_scope = diagnostic_scope or nullcontext

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
        for field in ("worker_id", "worker_stopped_at", "worker_stop_basis", "worker_stop_identity"):
            state.pop(field)
        if record.version == 1:
            for field in ("version", "timer_phase", "timer_rule", "timer_updated_at", "timer_due_at"):
                state.pop(field)
        alive = (self._worker is not None and self._owned_probe_id == record.probe_id
                 and self._owned_worker_id == record.worker_id and self._worker.is_alive())
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
                failure = host_failure(error)
                read = read_failure(error)
                return {"ok": False, "error": "direct_probe_unavailable", "reason": _reason(error),
                        **({"host_failure": failure} if failure is not None else {}),
                        **({"read_failure": read} if read is not None else {})}

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
                                  instant(now), instant(now + timedelta(seconds=duration)),
                                  worker_id=str(uuid4()) if delete_timer else None, **extras)
            self.repository.create_record(record)
            self._owned_probe_id = record.probe_id
            self._owned_worker_id = record.worker_id
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
                    failure = host_failure(error)
                    read = read_failure(error)
                    return {**self._summary(), **({"host_failure": failure} if failure is not None else {}),
                            **({"read_failure": read} if read is not None else {})}
        self._worker = threading.Thread(target=self._run_scoped, args=(host, record, time.monotonic() + duration), name="review-direct-probe", daemon=False)
        try:
            self._worker.start()
        except Exception:
            self.repository.update(record.probe_id, status="abandoned")
            self._cleanup(host)
            raise DirectError("worker_unavailable") from None
        # An external, bounded join observer records actual termination. The
        # worker cannot certify its own exit from its target or finally block.
        if record.worker_id is None:
            return self._summary()
        worker = self._worker
        self._termination_observer = threading.Thread(
            target=self._confirm_termination, args=(worker, record, duration + self._join),
            name="review-worker-termination", daemon=True)
        try:
            self._termination_observer.start()
        except Exception:
            self._worker_stop_persistence = "failed"
        return self._summary()

    def _confirm_termination(self, worker, record, timeout):
        try:
            if worker.ident is None or record.worker_id is None:
                return
            worker.join(timeout)
            if worker.is_alive():
                return
            with self._diagnostic_scope():
                self.repository.confirm_worker_stopped(record.probe_id, record.worker_id,
                                                       record.binding_digest, instant(self._now()))
                # This exact saved worker/binding check is the existing
                # termination operation's authority; never add a Task DB read.
                from task_governance_tool.debug_log import allow_output
                allow_output()
            self._worker_stop_persistence = "confirmed"
        except Exception:
            self._worker_stop_persistence = "failed"

    def _run(self, host, record, monotonic_deadline):
        try:
            while not self._stop.is_set():
                try:
                    if self._halt_observation(record, monotonic_deadline):
                        return
                    # Acquire cancellation state once for this ordinary tick.
                    # Host reads use in-memory guards; effects re-read below.
                    if self.repository.read_observation().status != "waiting":
                        return
                    controller = self.wait.read_observation().controller
                    if binding_digest(controller) != record.binding_digest:
                        raise DirectError("wait_changed")
                    self._prepare_observation(controller)
                    parent = host.read_parent()
                    self._parent_ready(parent, controller, record, require_idle=False)
                    all_ended = self._observe_progress(host, controller, parent, record, monotonic_deadline)
                    if self._halt_observation(record, monotonic_deadline):
                        return
                except ObservationBusy:
                    # Defer only an effect-free read, never a transaction/effect.
                    if self._expired(record, monotonic_deadline):
                        self._halt_wait(record, monotonic_deadline)
                        return
                    if self._stop.wait(self._cycle):
                        return
                    continue
                if all_ended and _terminal(parent) and parent.thread_status == "idle":
                    if self._halt_wait(record, monotonic_deadline):
                        return
                    if self._dispatch(host, record, monotonic_deadline) is not False:
                        return
                if self._stop.wait(self._cycle):
                    return
        except Exception as error:
            self._retain_read_failure(error, record)
            try:
                if self._on_failure(host, record, error, monotonic_deadline):
                    return
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

    def _halt_observation(self, record, monotonic_deadline):
        """Check in-memory controls between reads, never reuse them for effects."""
        if self._stop.is_set():
            return True
        if self._expired(record, monotonic_deadline):
            # Expiry is a state transition, with the original status/lease
            # checks; it must not overwrite concurrent cancellation.
            return self._halt_wait(record, monotonic_deadline)
        return False

    def _prepare_observation(self, controller):
        # Legacy keeps its basis semantics; managed waiting overrides this.
        self._prepared(controller)

    def _observe_progress(self, host, controller, parent, record, monotonic_deadline):
        all_ended = True
        for expected in self._reviewers_to_observe(controller):
            if self._halt_observation(record, monotonic_deadline):
                return False
            child = self._read_reviewer(host, controller, expected,
                effect_guard=lambda: not self._halt_wait(record, monotonic_deadline))
            if self._halt_observation(record, monotonic_deadline):
                return False
            if not self._reviewer_matches(controller, expected, child):
                raise DirectError("reviewer_turn_changed")
            all_ended = self._reviewer_ended(controller, expected, child) and all_ended
        return all_ended

    def _run_scoped(self, host, record, monotonic_deadline):
        # One explicitly deadline-bounded worker, never the resident server.
        # Admission remains its existing fresh binding reads inside _run.
        with self._diagnostic_scope():
            return self._run(host, record, monotonic_deadline)

    def _retain_read_failure(self, error, record):
        """Legacy waits do not retain process-local purpose diagnostics."""
        return None

    def _on_failure(self, host, record, error, monotonic_deadline):
        """Legacy waits retain their existing suppression-only outcome."""
        return False

    def _reviewers_to_observe(self, controller):
        return controller.reviewers

    def _observe_reviewer(self, controller, observation, *, effect_guard=None):
        """Legacy waits have no integrated result journal."""
        return None

    def _reviewer_matches(self, controller, expected, child):
        return child.child_id == expected.reviewer_id and child.turn_id == expected.turn_id

    def _reviewer_ended(self, controller, expected, child):
        return self._reviewer_matches(controller, expected, child) and _terminal(child)

    def _parent_ready(self, parent, controller, record, *, require_idle=True):
        """The explicit legacy experiment remains fixed to its starting turn."""
        if (parent.child_id != controller.binding.parent_thread_id
                or parent.turn_id != record.original_parent_turn
                or require_idle and (not _terminal(parent) or parent.thread_status != "idle")):
            raise DirectError("parent_turn_changed")
        return True

    def _read_reviewer(self, host, controller, expected, *, terminal=False, reviewer_observer=None,
                       effect_guard=None):
        def observe(observation):
            if reviewer_observer is None:
                self._observe_reviewer(controller, observation, effect_guard=effect_guard)
            elif effect_guard is None or effect_guard():
                reviewer_observer(observation)
        try:
            child = host.read_child(expected.reviewer_id)
        except Exception:
            observe((expected.reviewer_id, expected.turn_id, "unknown"))
            raise
        exact = self._record_reviewer_observation(expected, child)
        if exact and (_terminal(child) or child.status in {"failed", "interrupted"}):
            observe((expected.reviewer_id, expected.turn_id, child.status))
        elif not exact or terminal:
            observe((expected.reviewer_id, expected.turn_id, "unknown"))
        return child

    def _record_reviewer_observation(self, expected, child):
        """Retain only the existing exact, transient inspect facts; no DB read."""
        exact = child.child_id == expected.reviewer_id and child.turn_id == expected.turn_id
        with self._observations_lock:
            if exact:
                self._reviewer_observations[expected.reviewer_id] = {
                    "reviewer_id": expected.reviewer_id, "turn_id": expected.turn_id,
                    "observed_at": instant(self._now()), "status": child.status,
                    "thread_status": child.thread_status, "ended": _terminal(child)}
            else:
                self._reviewer_observations.pop(expected.reviewer_id, None)
        return exact

    def _idle_and_ended(self, host, controller, record, monotonic_deadline, *, reviewer_observer=None):
        """Fresh boundary checks; integrated callers retain every reviewer read."""
        for expected in self._reviewers_to_observe(controller):
            if self._halt_wait(record, monotonic_deadline):
                return False
            child = self._read_reviewer(host, controller, expected, terminal=True,
                                       reviewer_observer=reviewer_observer,
                                       effect_guard=lambda: not self._halt_wait(record, monotonic_deadline))
            exact_terminal = self._reviewer_ended(controller, expected, child)
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
            self._notification_observation(host, controller, self.repository.read())
            # Observation can read state/package sources. Renew the original
            # effect guards after that work, before durable send intent.
            if not self._idle_and_ended(host, controller, record, monotonic_deadline):
                return False
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

    def _notification_observation(self, host, controller, record):
        """Legacy sends retain their unchanged protocol."""
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
        if worker is not None and worker.ident is not None and self.repository.exists():
            record = self.repository.read()
            if record.probe_id == self._owned_probe_id and record.worker_id == self._owned_worker_id:
                self._confirm_termination(worker, record, 0)
            observer = self._termination_observer
            if observer is not None and observer.ident is not None:
                observer.join(1.1)
        if (not owned_only or self._owned_probe_id is not None) and self.repository.exists():
            record = self.repository.read()
            if (self._owned_probe_id is not None and record.probe_id == self._owned_probe_id
                    and record.worker_id == self._owned_worker_id):
                # Reuse the cleanup's existing owned-record identity check,
                # including reads preceding it; no diagnostic-only DB lookup.
                from task_governance_tool.debug_log import allow_output
                allow_output()
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
