"""Project worker that records receipt without another LLM control call."""

import time
from dataclasses import asdict

from .review_wait_direct import DirectProbe, DirectError


class ManagedDirectProbe(DirectProbe):
    def __init__(self, *args, finalization_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.finalization_factory = finalization_factory

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
        finalizer = self._finalizer(controller)
        if finalizer is None:
            return
        if record.version != 2 or self.repository.read().timer_phase != "deleted":
            raise DirectError("heartbeat_changed")
        def guard(observe):
            if not self._idle_and_ended(host, controller, record, monotonic_deadline,
                                        reviewer_observer=observe):
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

    def _run(self, host, record, monotonic_deadline):
        super()._run(host, record, monotonic_deadline)
        # Sending remains one-shot. Only observation follows its settlement;
        # unknown transport outcome can coexist with an independently seen event.
        deadline = min(monotonic_deadline, time.monotonic() + 120)
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                current = self.repository.read()
                if current.status not in {"accepted", "unknown"} or current.acknowledged_turn is not None:
                    return
                turn = host.observe_receipt(record.probe_id, record.original_parent_turn)
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
