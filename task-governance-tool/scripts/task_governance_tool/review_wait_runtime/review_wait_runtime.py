"""Source-only composition of admitted host facts and durable wait decisions.

This does not install, start a background process, or accept arbitrary MCP
requests. Its caller owns executor admission; production integration must still
establish parent-session lifetime, Setup and canonical paths. Tests inject a
store and host. Every external write follows a durably saved intent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from task_governance_tool.review_wait_runtime.review_wait_controller import (
    Appointment, Reservation, WaitBinding,
)
from task_governance_tool.review_wait_runtime.review_wait_host import HostAdapterError, PublicMcpHost
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository, StoredWait


class RuntimeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def reservation(snapshot) -> Reservation:
    """Retain only structural state; no raw name, prompt or executor metadata."""
    return Reservation(snapshot.id, snapshot.parent_thread_id, snapshot.rule,
                       snapshot.timezone, snapshot.status, snapshot.identity_digest)


class ReviewWaitRuntime:
    """One admitted parent and existing timer, with explicit caller operations.

    ``current_binding`` must read the original Task's current basis, not whichever
    Task is selected now. The public basis provider supplies this admission.
    This class cannot authenticate a caller or prove those supplied facts.
    It never marks a Task done, reads originals, emits usage or creates a timer.
    """

    def __init__(self, repository: ReviewWaitRepository, host: PublicMcpHost,
                 current_binding: Callable[[], WaitBinding], clock: Callable[[], datetime]):
        self.repository, self.host = repository, host
        self.current_binding, self.clock = current_binding, clock

    def _admit(self, controller, *, fresh: bool = True) -> None:
        if (self.host.parent_thread_id != controller.binding.parent_thread_id
                or self.host.automation_id != controller.reservation.timer_id
                or set(self.host.reviewer_ids) != {r.reviewer_id for r in controller.reviewers}):
            raise RuntimeError("host_binding_mismatch")
        if fresh:
            try:
                current = self.current_binding()
            except Exception:
                raise RuntimeError("task_binding_unavailable") from None
            if current != controller.binding:
                raise RuntimeError("task_binding_changed")

    def _timer_selected(self) -> None:
        # The caller already owns the prepared wait's writer lease. A direct
        # experiment creates its permanent one-shot marker under this same lease.
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import marker_exists
        if marker_exists(self.repository.path):
            raise RuntimeError("direct_probe_selected")

    def _timer_cleanup_selected(self) -> None:
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import owns_timer
        if owns_timer(self.repository.path):
            raise RuntimeError("direct_probe_selected")

    @staticmethod
    def _check_cancelled(cancelled) -> None:
        if cancelled is not None and cancelled():
            raise RuntimeError("observation_cancelled")

    def _observe(self, controller, cancelled=None) -> None:
        """A completed turn in a still-active thread is not an ended reviewer."""
        recorded = {t["reviewer_id"] for t in controller.to_snapshot()["terminal"]}
        for expected in controller.reviewers:
            self._check_cancelled(cancelled)
            if expected.reviewer_id in recorded:
                continue
            turn = self.host.read_child(expected.reviewer_id)
            if turn.child_id != expected.reviewer_id or turn.turn_id != expected.turn_id:
                raise RuntimeError("reviewer_turn_changed")
            if (turn.thread_status in {"idle", "notLoaded", "systemError"}
                    and turn.status in {"completed", "failed", "interrupted"}):
                controller.observe_terminal(binding=controller.binding, reviewer=expected, status=turn.status)

    def _save(self, lease, stored: StoredWait) -> StoredWait:
        return lease.save(stored.controller, expected_revision=stored.revision)

    def _execute(self, lease, stored: StoredWait, intent, observed, cancelled=None) -> StoredWait:
        # This method receives only an intent created by this invocation.
        # A pending intent loaded from disk never enters this execution path.
        if intent is not None and intent.kind != "pause":
            self._timer_selected()
            self._admit(stored.controller)
            self._check_cancelled(cancelled)
        stored = self._save(lease, stored)
        controller = stored.controller
        if intent is None:
            return stored
        if intent.kind != "pause":
            try:
                self._admit(controller)
                self._check_cancelled(cancelled)
            except RuntimeError:
                # This invocation knows dispatch has not started. Resolve the
                # saved claim as not applied only with matching fresh readback.
                try:
                    current = reservation(self.host.view_heartbeat())
                except HostAdapterError:
                    current = None
                controller.settle(intent.operation_id, readback=current, now=self.clock(),
                                  operation_finished=current == intent.before)
                self._save(lease, stored)
                raise
        try:
            after = self.host.update_heartbeat(
                observed, "arm" if intent.kind == "rearm" else intent.kind,
                **({"rrule": intent.after.rule} if intent.kind in {"arm", "rearm"} else {}))
        except HostAdapterError:
            # A lost receipt/readback may follow a successful external write.
            # Do not retry, clear pending, or infer quiescence from an old value.
            controller.settle(intent.operation_id, readback=None, now=self.clock())
        else:
            controller.settle(intent.operation_id, readback=reservation(after), now=self.clock(),
                              operation_finished=True)
        return self._save(lease, stored)

    def _after_activation(self, lease, stored: StoredWait) -> StoredWait:
        controller = stored.controller
        if controller.pending is not None:
            return stored
        # The last review may end before activation; no later event is needed.
        if controller.ready and controller.all_ended:
            observed = self.host.view_heartbeat()
            shorten = controller.maybe_shorten(binding=controller.binding,
                expected_arm=controller.arm_number, readback=reservation(observed), now=self.clock())
            return self._execute(lease, stored, shorten, observed)
        if not controller.ready and not controller.stop_confirmed:
            # A missed first occurrence must not become tomorrow's appointment.
            observed = self.host.view_heartbeat(for_pause=True)
            pause = controller.cancel(parent_thread_id=self.host.parent_thread_id,
                binding=controller.binding, expected_arm=controller.arm_number,
                readback=reservation(observed))
            return self._execute(lease, stored, pause, observed)
        return stored

    def arm(self, appointment: Appointment) -> StoredWait:
        with self.repository.writer() as lease:
            self._timer_selected()
            stored = lease.read()
            controller = stored.controller
            self._admit(controller)
            if controller.pending is not None:
                return stored
            self._observe(controller)
            observed = self.host.view_heartbeat()
            intent = controller.arm(parent_thread_id=self.host.parent_thread_id,
                                    binding=controller.binding, appointment=appointment,
                                    readback=reservation(observed), now=self.clock())
            stored = self._execute(lease, stored, intent, observed)
            return self._after_activation(lease, stored) if intent is not None else stored

    def observe(self, *, cancelled: Callable[[], bool] | None = None) -> StoredWait:
        """One bounded observation/decision; no LLM, polling loop or scheduler."""
        with self.repository.writer() as lease:
            self._timer_cleanup_selected()
            self._check_cancelled(cancelled)
            stored = lease.read()
            controller = stored.controller
            self._admit(controller)
            if controller.pending is not None or controller.phase == "closed":
                return stored
            self._observe(controller, cancelled)
            self._check_cancelled(cancelled)
            observed = self.host.view_heartbeat()
            self._check_cancelled(cancelled)
            intent = controller.maybe_shorten(binding=controller.binding,
                expected_arm=controller.arm_number, readback=reservation(observed), now=self.clock())
            return self._execute(lease, stored, intent, observed, cancelled)

    def check(self, *, expected_arm: int, wake_id: str, wake_time: datetime) -> StoredWait:
        with self.repository.writer() as lease:
            self._timer_cleanup_selected()
            stored = lease.read()
            controller = stored.controller
            self._admit(controller, fresh=False)
            if controller.pending is not None:
                return stored
            # Stop/readback is required even when child status retrieval is
            # unavailable. Fresh child observations are mandatory before rearm,
            # not a precondition for stopping the matching scheduled check.
            observed = self.host.view_heartbeat(for_pause=True)
            intent = controller.check(parent_thread_id=self.host.parent_thread_id,
                binding=controller.binding, expected_arm=expected_arm, wake_id=wake_id,
                wake_time=wake_time, readback=reservation(observed))
            return self._execute(lease, stored, intent, observed)

    def rearm(self, *, expected_arm: int, appointment: Appointment, healthy: bool) -> StoredWait:
        with self.repository.writer() as lease:
            self._timer_selected()
            stored = lease.read()
            controller = stored.controller
            self._admit(controller)
            if controller.pending is not None:
                return stored
            self._observe(controller)
            observed = self.host.view_heartbeat()
            intent = controller.rearm(parent_thread_id=self.host.parent_thread_id,
                binding=controller.binding, expected_arm=expected_arm, appointment=appointment,
                readback=reservation(observed), now=self.clock(), healthy=healthy)
            stored = self._execute(lease, stored, intent, observed)
            return self._after_activation(lease, stored) if intent is not None else stored

    def cancel(self, *, expected_arm: int) -> StoredWait:
        with self.repository.writer() as lease:
            self._timer_cleanup_selected()
            stored = lease.read()
            controller = stored.controller
            # Explicit cleanup still addresses the original wait if its Task
            # changed; it cannot create an ACTIVE update or act as another parent.
            self._admit(controller, fresh=False)
            if controller.pending is not None:
                controller.cancel(parent_thread_id=self.host.parent_thread_id,
                    binding=controller.binding, expected_arm=expected_arm, readback=None)
                return self._save(lease, stored)
            observed = self.host.view_heartbeat(for_pause=True)
            intent = controller.cancel(parent_thread_id=self.host.parent_thread_id,
                binding=controller.binding, expected_arm=expected_arm, readback=reservation(observed))
            return self._execute(lease, stored, intent, observed)

    def reconcile(self, *, operation_finished: bool = False) -> StoredWait:
        """Readback only. Quiescence must be established separately, never guessed.

        A new process cannot prove that a previous timed-out host call finished.
        Default reconciliation therefore preserves pending state. Even explicit
        quiescence never retries a write; closed waits use a later cancel call.
        """
        if type(operation_finished) is not bool:
            raise RuntimeError("invalid_reconciliation")
        with self.repository.writer() as lease:
            self._timer_cleanup_selected()
            stored = lease.read()
            controller = stored.controller
            self._admit(controller, fresh=False)
            if controller.pending is None:
                return stored
            # Unknown timing cannot authorize ACTIVE readiness. A closed wait or
            # pending stop may still inspect identity/status for stop-only cleanup.
            cleanup_only = controller.phase == "closed" or controller.pending.kind == "pause"
            observed = self.host.view_heartbeat(for_pause=cleanup_only)
            controller.settle(controller.pending.operation_id, readback=reservation(observed),
                              now=self.clock(), operation_finished=operation_finished)
            return self._save(lease, stored)
