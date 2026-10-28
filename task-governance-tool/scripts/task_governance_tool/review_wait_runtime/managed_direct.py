"""Project worker that records receipt without another LLM control call."""

import time
from dataclasses import asdict
from datetime import datetime

from .review_wait_direct import DirectProbe, DirectError, _terminal, _reason, binding_digest
from .review_wait_repository import RepositoryError
from .request_repository import ParentDispatchRepository
from .review_wait_host import read_failure, host_failure
from . import worker_diagnostics as diagnostics
from .worker_diagnostics import Stage, Outcome


class ManagedDirectProbe(DirectProbe):
    def __init__(self, *args, finalization_factory=None, dispatch_path=None,
                 observation_factory=None, runtime_observer=None, occurrence_guard=None, answer_collector=None,
                 answer_tracking=None, tracked_answer_collector=None, polling_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.finalization_factory = finalization_factory
        self.dispatch_path = dispatch_path
        self.observation_factory = observation_factory
        self.runtime_observer = runtime_observer
        self.occurrence_guard = occurrence_guard
        self.answer_collector = answer_collector
        self.answer_tracking = answer_tracking
        self.tracked_answer_collector = tracked_answer_collector
        self.polling_factory = polling_factory
        self._polling_sources = None
        self._progress_seen = {}
        self._saved_results_ready = False
        self._answers_collected = False
        self._finalization_prepared = False
        self.read_diagnostic = None
        self._read_diagnostic_identity = None

    def _run_scoped(self, host, record, monotonic_deadline):
        try:
            return super()._run_scoped(host, record, monotonic_deadline)
        finally:
            # Ended associations remain inspectable; polling hints must not.
            self._polling_sources = None
            self._progress_seen.clear()
            self._saved_results_ready = False

    def read_diagnostic_for(self, probe_id, worker_id):
        """Return a transient cause only for the exact observed owned worker."""
        with self._observations_lock:
            identity = (probe_id, worker_id)
            if (worker_id is None or self._read_diagnostic_identity != identity
                    or (self._owned_probe_id, self._owned_worker_id) != identity):
                return None
            return self.read_diagnostic

    def _retain_read_failure(self, error, record):
        read = read_failure(error)
        if read is None:
            return
        diagnostic = {"read_failure": read}
        exchange = host_failure(error)
        if exchange is not None:
            diagnostic["host_failure"] = exchange
        with self._observations_lock:
            identity = (record.probe_id, record.worker_id)
            if (identity != (self._owned_probe_id, self._owned_worker_id)
                    or self.read_diagnostic is not None):
                return
            self.read_diagnostic = diagnostic
            self._read_diagnostic_identity = identity

    def _notification_observation(self, host, controller, record):
        # The snapshot precedes this notification's send intent. Its send result,
        # receipt and future exit cannot be confirmed in its own frozen body.
        if self.observation_factory is None or not hasattr(host, "set_wait_observation"):
            return
        try:
            observed = self.observation_factory(controller)
        except Exception as error:
            diagnostics.event(Stage.OBSERVATION, Outcome.UNKNOWN, reason=_reason(error), error=error)
            from .worker_observation import observation
            from .review_wait_direct_repository import instant
            from datetime import datetime, timezone
            observed = observation(controller.binding.task_id, controller.binding.parent_thread_id,
                instant(datetime.now(timezone.utc)), reason="observation_unavailable")
        # A factory may return its caught read-failure projection rather than
        # raise. Preserve the already-bound notification identity in either
        # case, without claiming any unread request/worker/delivery evidence.
        if observed["identity"]["probe_id"] is None:
            observed["identity"]["probe_id"] = record.probe_id
        if observed["identity"]["automation_id"] is None:
            observed["identity"]["automation_id"] = controller.reservation.timer_id
        occurrence = self.occurrence_guard(controller) if self.occurrence_guard is not None else None
        if getattr(occurrence, "continuation_id", None) is not None:
            observed["identity"]["continuation_id"] = occurrence.continuation_id
            observed["identity"]["continuation_sequence"] = occurrence.continuation_sequence
        observed["service"] = {"status": "unknown", "scope": "notification_worker_only"}
        if self.runtime_observer is not None:
            try:
                observed["runtime"] = self.runtime_observer()
            except Exception as error:
                diagnostics.event(Stage.OBSERVATION, Outcome.UNKNOWN, reason=_reason(error), error=error)
                observed["runtime"] = {"comparison": "unknown"}
        host.set_wait_observation(record.probe_id, observed)

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
        # This check precedes the legacy fallback: a newer explicit occurrence
        # cannot inherit this worker's effect/delivery authority.
        if self.occurrence_guard is not None:
            try:
                self.occurrence_guard(controller)
            except Exception as error:
                diagnostics.event(Stage.OBSERVATION, Outcome.UNKNOWN, reason=_reason(error), error=error)
                raise DirectError(getattr(error, "reason", None) or _reason(error)) from None
        try:
            super()._prepared(controller)
        except Exception as error:
            diagnostics.event(Stage.OBSERVATION, Outcome.UNKNOWN, reason=_reason(error), error=error)
            # A bound integrated wait can report stale ownership/target or its
            # own legitimate completion to the original parent. This fallback
            # authorizes observation/delivery only; Finalizer still rejects any
            # mismatched live basis before a Task/Git effect. OFF is rejected by
            # the factory. Legacy/manual waits retain their exact old behavior.
            if (controller.arm_number != 0 or controller.phase != "preparing" or controller.pending is not None
                    or controller.reservation.status != "PAUSED" or not controller.stop_confirmed
                    or self._finalizer(controller) is None):
                raise

    def _observe_reviewer(self, controller, observation, *, effect_guard=None):
        finalizer = self._finalizer(controller)
        if finalizer is not None:
            if effect_guard is not None and not effect_guard():
                return False
            finalizer.observe_reviewers((observation,))
        return True

    def _tracking(self, controller, expected):
        values = self.answer_tracking(controller) if self.answer_tracking is not None else {}
        return values.get(expected.reviewer_id)

    def _prepare_observation(self, controller):
        if self.polling_factory is None:
            return self._prepared(controller)
        if self._polling_sources is None:
            from task_governance_tool.review_wait_basis_repository import WaitBasisError
            self._prepared(controller)
            self._saved_results_ready = not self._reviewers_to_observe(controller)
            try:
                self._polling_sources = self.polling_factory(
                    controller, collect=not self._saved_results_ready)
            except WaitBasisError as error:
                diagnostics.event(Stage.OBSERVATION, Outcome.UNKNOWN, reason=_reason(error), error=error)
                if error.code != "wait_basis_inactive":
                    raise
                # Completion can release ownership after the readiness read.
                # Only fresh qualified native results replace that failed
                # collection admission; other errors keep their failure route.
                self._prepared(controller)
                self._saved_results_ready = not self._reviewers_to_observe(controller)
                if not self._saved_results_ready:
                    raise
                self._polling_sources = self.polling_factory(controller, collect=False)
        guard, tracking, _ = self._polling_sources
        if guard():
            self._prepared(controller)
            self._saved_results_ready = not self._reviewers_to_observe(controller)
        if not self._saved_results_ready and tracking is None:
            # The saved route retained controls, not collection admission.
            # Rebuild on the next ordinary tick after readiness is invalidated.
            self._polling_sources = None
            self._progress_seen.clear()

    @staticmethod
    def _completed_tracking(expected, tracked):
        if tracked["boundary_turn_id"] != expected.turn_id:
            raise DirectError("reviewer_turn_changed")
        if not tracked["frozen"]:
            return False
        if tracked["source_identity"] is None or tracked["terminal"] != "completed":
            raise DirectError("child_status_unavailable")
        return True

    def _observe_progress(self, host, controller, parent, record, monotonic_deadline):
        if self.polling_factory is None:
            return super()._observe_progress(host, controller, parent, record, monotonic_deadline)
        from task_governance_tool.review_answer_runtime import TrackingReadmissionRequired
        if self._saved_results_ready:
            return True  # Dispatch still validates the live native judgments.
        if self._polling_sources is None:
            return False
        _, tracking_reader, candidate_probe = self._polling_sources
        try:
            tracking = tracking_reader()
        except TrackingReadmissionRequired:
            # A newly bound source requires its original admission, not a
            # guessed child/slot mapping. No effect has been attempted here.
            self._polling_sources = None
            self._progress_seen.clear()
            return False
        pending = []
        all_ended = True
        for expected in controller.reviewers:
            if self._halt_observation(record, monotonic_deadline):
                return False
            tracked = tracking.get(expected.reviewer_id)
            if tracked is not None and self._completed_tracking(expected, tracked):
                continue
            try:
                child = host.read_child(expected.reviewer_id)
            except Exception as error:
                diagnostics.event(Stage.FAILURE, Outcome.FAILED, reason=_reason(error), error=error)
                self._observe_reviewer(controller, (expected.reviewer_id, expected.turn_id, "unknown"),
                    effect_guard=lambda: not self._halt_wait(record, monotonic_deadline))
                raise
            if tracked is None:
                self._record_reviewer_observation(expected, child)
            else:
                with self._observations_lock:
                    self._reviewer_observations.pop(expected.reviewer_id, None)
            if self._halt_observation(record, monotonic_deadline):
                return False
            if child.child_id != expected.reviewer_id or (tracked is None and child.turn_id != expected.turn_id):
                if not self._observe_reviewer(controller, (expected.reviewer_id, expected.turn_id, "unknown"),
                        effect_guard=lambda: not self._halt_wait(record, monotonic_deadline)):
                    return False
                raise DirectError("reviewer_turn_changed")
            ended = _terminal(child)
            all_ended = all_ended and ended
            state = (child.turn_id, child.status, child.thread_status)
            changed = self._progress_seen.get(expected.reviewer_id) != state
            # Retain each failure or untracked terminal fact before the next
            # host read can fail. Tracked completion still comes only from its
            # admitted frozen answer, never this latest-turn progress hint.
            if changed and (child.status in {"failed", "interrupted"} or tracked is None and ended):
                if not self._observe_reviewer(controller, (expected.reviewer_id, child.turn_id, child.status),
                        effect_guard=lambda: not self._halt_wait(record, monotonic_deadline)):
                    return False
                self._progress_seen[expected.reviewer_id] = state
            if ended:
                if tracked is not None:
                    pending.append((expected, child, state, changed))
            else:
                self._progress_seen[expected.reviewer_id] = state
        parent_idle = _terminal(parent) and parent.thread_status == "idle"
        collected = False
        for expected, child, state, changed in pending:
            if candidate_probe is None and not changed and not (all_ended and parent_idle):
                continue  # Compatibility callbacks have no bounded candidate hint.
            if child.status in {"failed", "interrupted"}:
                if all_ended and parent_idle:
                    raise DirectError("child_status_unavailable")
                continue
            if self._halt_observation(record, monotonic_deadline):
                return False
            try:
                candidate_changed = candidate_probe is None or candidate_probe(host, expected.reviewer_id)
            except TrackingReadmissionRequired:
                self._polling_sources = None
                self._progress_seen.clear()
                return False
            if self._halt_observation(record, monotonic_deadline):
                return False
            if candidate_changed is None:
                continue
            if not candidate_changed:
                self._progress_seen[expected.reviewer_id] = state
                continue
            if self.tracked_answer_collector is not None:
                self._prepared(controller)
                if self._halt_wait(record, monotonic_deadline):
                    return False
                self.tracked_answer_collector(host, controller, expected.reviewer_id,
                                              observation=tracking_reader)
                collected = True
            self._progress_seen[expected.reviewer_id] = state
        if collected:
            try:
                tracking = tracking_reader()
            except TrackingReadmissionRequired:
                self._polling_sources = None
                self._progress_seen.clear()
                return False
        if pending:
            all_ended = all_ended and all(
                expected.reviewer_id in tracking
                and self._completed_tracking(expected, tracking[expected.reviewer_id])
                for expected, _, _, _ in pending)
        return all_ended

    def _read_reviewer(self, host, controller, expected, *, terminal=False, reviewer_observer=None,
                       effect_guard=None):
        tracked = self._tracking(controller, expected)
        if tracked is None:
            return super()._read_reviewer(host, controller, expected, terminal=terminal,
                                         reviewer_observer=reviewer_observer, effect_guard=effect_guard)
        if tracked["boundary_turn_id"] != expected.turn_id:
            raise DirectError("reviewer_turn_changed")
        if not tracked["frozen"] and self.tracked_answer_collector is not None:
            if effect_guard is not None and not effect_guard():
                return None
            self.tracked_answer_collector(host, controller, expected.reviewer_id)
            tracked = self._tracking(controller, expected)
        if tracked is None or tracked["boundary_turn_id"] != expected.turn_id:
            raise DirectError("reviewer_turn_changed")
        if tracked["frozen"]:
            source = tracked["source_identity"]
            if source is None or tracked["terminal"] != "completed":
                raise DirectError("child_status_unavailable")
            observe = reviewer_observer or (lambda value: self._observe_reviewer(
                controller, value, effect_guard=effect_guard))
            if reviewer_observer is not None and effect_guard is not None and not effect_guard():
                return None
            observe((expected.reviewer_id, source["turn_id"], "completed"))
        # The collector independently observes the source. No synthetic latest
        # turn or invented current thread activity is returned to the wait loop.
        with self._observations_lock:
            self._reviewer_observations.pop(expected.reviewer_id, None)
        return None

    def _reviewer_matches(self, controller, expected, child):
        tracked = self._tracking(controller, expected)
        if tracked is not None:
            return tracked["boundary_turn_id"] == expected.turn_id
        return super()._reviewer_matches(controller, expected, child)

    def _reviewer_ended(self, controller, expected, child):
        tracked = self._tracking(controller, expected)
        if tracked is not None:
            return (tracked["boundary_turn_id"] == expected.turn_id and tracked["frozen"]
                    and tracked["terminal"] == "completed")
        return super()._reviewer_ended(controller, expected, child)

    def _reviewers_to_observe(self, controller):
        finalizer = self._finalizer(controller)
        if finalizer is not None:
            from task_governance_tool.review_finalization import FinalizationError
            try:
                if finalizer.saved_results_ready():
                    return ()  # Saved native judgments precede incidental chat activity.
            except FinalizationError as error:
                diagnostics.event(Stage.OBSERVATION, Outcome.UNKNOWN, reason=_reason(error), error=error)
                if error.code not in {"review_target_mismatch", "task_ownership_changed"}:
                    raise
                # Changed Task state cannot reuse judgments. Observe the bound
                # reviewers so the existing finalizer can report that mismatch;
                # its native effect guards and occurrence fence remain intact.
        return controller.reviewers

    def _failure_parent(self, host, controller, record, monotonic_deadline):
        """Fresh effect guard without requiring a failed reviewer to qualify."""
        if self._halt_wait(record, monotonic_deadline):
            return None
        parent = host.read_parent()
        if not self._parent_ready(parent, controller, record):
            return None
        self._prepared(controller)
        return None if self._halt_wait(record, monotonic_deadline) else parent

    def _on_failure(self, host, record, error, monotonic_deadline):
        """One failure outcome through the existing same-parent send admission.

        No busy-parent retry, unknown-effect replay, finalization or new timer.
        If any safety observation is unavailable, retain normal suppression.
        """
        reason = _reason(error)
        if (self.dispatch_path is None or record.version != 2 or not hasattr(host, "set_wait_failure")
            or reason not in {"reviewer_turn_changed", "child_status_unavailable", "invalid_host_response",
                              "wait_basis_unavailable", "task_binding_changed"}
            or self._halt_wait(record, monotonic_deadline)):
            return False
        controller = self.wait.read().controller
        try:
            with ParentDispatchRepository(self.dispatch_path(controller.binding.parent_thread_id)).serial():
                with self.wait.writer() as lease:
                    current_controller = lease.read().controller
                    if binding_digest(current_controller) != record.binding_digest or binding_digest(controller) != record.binding_digest:
                        return False
                    self._prepared(controller)
                    current = self.repository.read()
                    if current.status != "waiting" or current.timer_phase not in {"active", "deleted"}:
                        return False
                    parent = host.read_parent()
                    if not self._parent_ready(parent, controller, record):
                        return False
                    if current.timer_phase == "active":
                        if self._now() >= datetime.fromisoformat(current.timer_due_at):
                            return False
                        before = self._heartbeat(host, controller, current)
                        if self._failure_parent(host, controller, record, monotonic_deadline) is None:
                            return False
                        if self._now() >= datetime.fromisoformat(current.timer_due_at):
                            return False
                        self.repository.update(record.probe_id, timer_phase="deleting")
                        try:
                            diagnostics.event(Stage.TIMER_DELETE, Outcome.ATTEMPT, reason="failure_notification")
                            diagnostics.checkpoint(urgent=True)
                            host.delete_heartbeat(before)
                        except Exception as error:
                            diagnostics.event(Stage.TIMER_DELETE, Outcome.UNKNOWN, reason=_reason(error), error=error)
                            self.repository.update(record.probe_id, timer_phase="unknown")
                            return False
                        diagnostics.event(Stage.TIMER_DELETE, Outcome.CONFIRMED)
                        try:
                            current = self.repository.update(record.probe_id, timer_phase="deleted")
                        except Exception as error:
                            diagnostics.event(Stage.TIMER_SAVE, Outcome.FAILED, reason="delete_save_failed", error=error)
                            raise
                    occurrence = self.occurrence_guard(controller) if self.occurrence_guard is not None else None
                    host.set_wait_failure(record.probe_id, controller.binding, reason,
                        continuation_id=getattr(occurrence, "continuation_id", None),
                        continuation_sequence=getattr(occurrence, "continuation_sequence", 0))
                    self._notification_observation(host, controller, current)
                    parent = self._failure_parent(host, controller, record, monotonic_deadline)
                    if parent is None:
                        return False
                    self._send_parent_turn = parent.turn_id
                    self.repository.update(record.probe_id, status="dispatching", reason=reason)
                    try:
                        diagnostics.event(Stage.NOTIFICATION, Outcome.ATTEMPT, reason="failure_notification")
                        diagnostics.checkpoint(urgent=True)
                        outcome = host.send_direct_probe(record.probe_id)
                    except Exception as error:
                        diagnostics.event(Stage.NOTIFICATION, Outcome.UNKNOWN, reason=_reason(error), error=error)
                        outcome = "unknown"
                    if outcome not in {"accepted", "rejected", "unknown"}:
                        outcome = "unknown"
                    diagnostics.event(Stage.NOTIFICATION, Outcome(outcome), reason="failure_notification")
                    self.repository.update(record.probe_id, status=outcome, reason=reason)
                    diagnostics.checkpoint(urgent=True)
                    return True
        except Exception as error:
            diagnostics.event(Stage.FAILURE, Outcome.FAILED, reason=_reason(error), error=error)
            return False  # Durable dispatching remains unknown, never replayed.

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
        if not self._answers_collected and self.answer_collector is not None:
            # Exactly one collection attempt at this existing ended-reviewer
            # boundary; no independent worker or changed notification route.
            self._answers_collected = True
            try:
                self.answer_collector(host, controller)
            except Exception as error:
                diagnostics.event(Stage.COLLECTION, Outcome.FAILED, reason="collection_failed", error=error)
                # The existing finalizer reports missing/unknown originals;
                # manual end notification leaves recovery with the parent.
                pass
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
        result = finalizer.execute(guard=guard)
        host.set_finalization_result(record.probe_id, result)
        self._receipt_finalizer = finalizer
        self._finalization_result = result
        self._finalization_prepared = True

    def _run(self, host, record, monotonic_deadline):
        super()._run(host, record, monotonic_deadline)
        # Sending remains one-shot. Only observation follows its settlement;
        # unknown transport outcome can coexist with an independently seen event.
        deadline = min(monotonic_deadline, time.monotonic() + 120)
        failure_reason = "read_failed"
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                if self._expired(record, monotonic_deadline):
                    diagnostics.event(Stage.RECEIPT_END, Outcome.DEADLINE, reason="deadline")
                    return
                current = self.repository.read()
                if current.status not in {"accepted", "unknown"} or current.acknowledged_turn is not None:
                    diagnostics.event(Stage.RECEIPT_END, Outcome.CONFIRMED if current.acknowledged_turn is not None
                        else Outcome.SKIPPED, reason="receipt_already_seen" if current.acknowledged_turn is not None
                        else "receipt_not_eligible")
                    return
                diagnostics.event(Stage.RECEIPT, Outcome.ATTEMPT)
                diagnostics.checkpoint(poll=True)
                turn = host.observe_receipt(record.probe_id, self._send_parent_turn, deadline=deadline)
                if turn is not None:
                    diagnostics.event(Stage.RECEIPT, Outcome.CONFIRMED, reason="receipt_seen")
                    failure_reason = "receipt_save_failed"
                    self.repository.update(record.probe_id, acknowledged_turn=turn)
                    finalizer = getattr(self, "_receipt_finalizer", None)
                    if finalizer is not None:
                        failure_reason = "receipt_record_failed"
                        finalizer.record_notification_receipt(turn)
                    diagnostics.event(Stage.RECEIPT_END, Outcome.CONFIRMED, reason="receipt_seen")
                    return
                if self._stop.wait(self._cycle):
                    diagnostics.event(Stage.RECEIPT_END, Outcome.CANCELLED, reason="cancel_requested")
                    return
            diagnostics.event(Stage.RECEIPT_END, Outcome.CANCELLED if self._stop.is_set() else Outcome.DEADLINE,
                              reason="cancel_requested" if self._stop.is_set() else "deadline")
        except Exception as error:
            diagnostics.event(Stage.RECEIPT_END, Outcome.UNKNOWN, reason=failure_reason, error=error)
            self._retain_read_failure(error, record)
            return  # Accepted stays accepted; an unavailable read is not receipt.
