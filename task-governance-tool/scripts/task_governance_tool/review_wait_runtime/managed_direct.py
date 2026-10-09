"""Project worker that records receipt without another LLM control call."""

import time
from dataclasses import asdict

from .review_wait_direct import DirectProbe, DirectError, _terminal
from .review_wait_repository import RepositoryError
from .request_repository import ParentDispatchRepository


class ManagedDirectProbe(DirectProbe):
    def __init__(self, *args, finalization_factory=None, dispatch_path=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.finalization_factory = finalization_factory
        self.dispatch_path = dispatch_path
        self._finalization_prepared = False

    def _parent_ready(self, parent, controller, record, *, require_idle=True):
        if parent.child_id != controller.binding.parent_thread_id:
            raise DirectError("parent_turn_changed")
        # Task/execution/Contract/target and exact reviewer pairs remain bound;
        # another legitimate Task's parent turn does not replace that basis.
        return not require_idle or _terminal(parent) and parent.thread_status == "idle"

    def _dispatch(self, host, record, monotonic_deadline):
        if self.dispatch_path is None:
            raise DirectError("invalid_configuration")
        controller = self.wait.read().controller
        repository = ParentDispatchRepository(self.dispatch_path(controller.binding.parent_thread_id))
        manager = repository.serial()
        try:
            manager.__enter__()
        except RepositoryError as error:
            if error.code == "writer_busy":
                return False  # Retry admission only, within this original deadline.
            raise
        try:
            return super()._dispatch(host, record, monotonic_deadline)
        finally:
            manager.__exit__(None, None, None)

    def _finalizer(self, controller):
        if self.finalization_factory is None:
            return None
        finalizer = self.finalization_factory(controller)
        if finalizer is None or finalizer.journal is None:
            return None
        basis = finalizer.journal.read().basis
        if any(basis.get(key) != value for key, value in asdict(controller.binding).items() if key != "wait_id"):
            raise DirectError("task_binding_changed")
        return finalizer

    def _prepared(self, controller):
        try:
            super()._prepared(controller)
        except Exception:
            # A bound integrated wait can report stale ownership/target or its
            # own legitimate completion to the original parent. This fallback
            # authorizes observation/delivery only; Finalizer still rejects any
            # mismatched live basis before a Task/Git effect. OFF is rejected by
            # the factory. Legacy/manual waits retain their exact old behavior.
            if (controller.arm_number != 0 or controller.phase != "preparing" or controller.pending is not None
                    or controller.reservation.status != "PAUSED" or not controller.stop_confirmed
                    or self._finalizer(controller) is None):
                raise

    def _observe_reviewer(self, controller, observation):
        finalizer = self._finalizer(controller)
        if finalizer is not None:
            finalizer.observe_reviewers((observation,))

    def _before_send(self, host, controller, record, monotonic_deadline):
        if self._finalization_prepared:
            # Parent work during deferral may reopen/change the original Task.
            # Rebuild from the retained intent and current read-only evidence,
            # preserving successful stages without replaying any Task/Git effect.
            result = self._receipt_finalizer.execute(check=True)
            # Check mode cannot rerun business gates/effects that stopped the
            # original attempt. Keep its known reason unless fresh evidence
            # proves completion or supplies a newer exact-basis failure.
            previous = self._finalization_result
            if (result.get("blocking_code") is None and result.get("status") != "completed"
                    and previous.get("blocking_code") is not None):
                result = {**result, "ok": False, "blocking_code": previous["blocking_code"]}
            host.set_finalization_result(record.probe_id, result)
            self._finalization_result = result
            return
        finalizer = self._finalizer(controller)
        if finalizer is None:
            return
        if record.version != 2 or self.repository.read().timer_phase != "deleted":
            raise DirectError("heartbeat_changed")
        def guard(observe):
            while not self._halt_wait(record, monotonic_deadline):
                if self._idle_and_ended(host, controller, record, monotonic_deadline,
                                         reviewer_observer=observe):
                    return
                if self._stop.wait(self._cycle):
                    break
            raise DirectError("parent_turn_changed")
        def reviewers():
            observed = []
            for reviewer in controller.reviewers:
                try:
                    child = host.read_child(reviewer.reviewer_id)
                    status = child.status if (child.child_id == reviewer.reviewer_id and child.turn_id == reviewer.turn_id
                        and child.status in {"completed", "failed", "interrupted"}) else "unknown"
                except Exception:
                    status = "unknown"
                observed.append((reviewer.reviewer_id, reviewer.turn_id, status))
            return tuple(observed)
        result = finalizer.execute(reviewer_observer=reviewers, guard=guard)
        host.set_finalization_result(record.probe_id, result)
        self._receipt_finalizer = finalizer
        self._finalization_result = result
        self._finalization_prepared = True

    def _run(self, host, record, monotonic_deadline):
        super()._run(host, record, monotonic_deadline)
        # Sending remains one-shot. Only observation follows its settlement;
        # unknown transport outcome can coexist with an independently seen event.
        deadline = min(monotonic_deadline, time.monotonic() + 120)
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                if self._expired(record, monotonic_deadline):
                    return
                current = self.repository.read()
                if current.status not in {"accepted", "unknown"} or current.acknowledged_turn is not None:
                    return
                turn = host.observe_receipt(record.probe_id, self._send_parent_turn, deadline=deadline)
                if turn is not None:
                    self.repository.update(record.probe_id, acknowledged_turn=turn)
                    finalizer = getattr(self, "_receipt_finalizer", None)
                    if finalizer is not None:
                        finalizer.record_notification_receipt(turn)
                    return
                if self._stop.wait(self._cycle):
                    return
        except Exception:
            return  # Accepted stays accepted; an unavailable read is not receipt.
