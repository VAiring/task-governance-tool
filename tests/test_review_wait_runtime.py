"""Offline composition: real controller/store and fake host, not host proof."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from tools.review_wait_controller import Appointment, Reviewer, ReviewWaitController, WaitBinding
from tools.review_wait_host import ChildTurn, HeartbeatSnapshot, HostAdapterError
from tools.review_wait_repository import RepositoryError, ReviewWaitRepository
from tools.review_wait_runtime import ReviewWaitRuntime, RuntimeError, reservation

NOW = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
BINDING = WaitBinding("wait-1", "project-1", "a" * 64, 1, "task-1", "execution-1", 1,
    "diff_fingerprint", "sha256:" + "b" * 64, "", 1, "manifest-1", "parent-1", 1)
REVIEWERS = (Reviewer("child-1", "turn-1"), Reviewer("child-2", "turn-2"))


def appointment(now=NOW):
    due = now + timedelta(minutes=10)
    rule = f"FREQ=DAILY;BYHOUR={due.hour};BYMINUTE={due.minute};BYSECOND={due.second};COUNT=1"
    return Appointment(now, due, rule, "UTC")


class FakeHost:
    parent_thread_id, automation_id = "parent-1", "timer-1"
    reviewer_ids = ("child-1", "child-2")

    def __init__(self):
        self.value = HeartbeatSnapshot("timer-1", "heartbeat", "parent-1",
            "FREQ=MINUTELY;INTERVAL=10", "UTC", "host_os", "PAUSED", 1, 1, "d" * 64)
        self.turns = {r.reviewer_id: ChildTurn(r.reviewer_id, r.turn_id, "inProgress", "active") for r in REVIEWERS}
        self.effects, self.repository, self.fail_after_write = [], None, False

    def read_child(self, child_id):
        return self.turns[child_id]

    def view_heartbeat(self, *, for_pause=False):
        return self.value

    def update_heartbeat(self, before, action, *, rrule=None):
        intent = self.repository.read().controller.pending
        if intent is None or before != self.value:
            raise AssertionError("external effect lacks saved intent or fresh readback")
        # A DB writer can enter: the controller lease is not a SQLite transaction.
        with self.repository._connection(write=True):
            pass
        try:
            with self.repository.writer():
                raise AssertionError("competing writer acquired live lease")
        except RepositoryError as exc:
            if exc.code != "writer_busy":
                raise
        self.effects.append((action, intent.operation_id))
        self.value = replace(self.value, updated_at=self.value.updated_at + 1,
            status="PAUSED" if action == "pause" else "ACTIVE",
            rule="FREQ=MINUTELY;INTERVAL=1" if action == "shorten" else rrule or self.value.rule)
        if self.fail_after_write:
            raise HostAdapterError("host_call_failed")
        return self.value


class ReviewWaitRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="taskgov-wait-runtime-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "wait.sqlite"
        self.host = FakeHost()
        self.repository = ReviewWaitRepository.create(self.path,
            ReviewWaitController(BINDING, REVIEWERS, reservation(self.host.value)))
        self.host.repository = self.repository
        self.now, self.current = NOW, BINDING
        self.runtime = self.reopen()

    def reopen(self):
        return ReviewWaitRuntime(ReviewWaitRepository.open_existing(self.path), self.host,
            lambda: self.current, lambda: self.now)

    def actions(self):
        return [x[0] for x in self.host.effects]

    def end_all(self):
        for reviewer, status in zip(REVIEWERS, ("completed", "failed")):
            self.host.turns[reviewer.reviewer_id] = ChildTurn(reviewer.reviewer_id, reviewer.turn_id, status, "idle")

    def test_all_ended_shortens_once_then_early_check_pauses_same_timer(self):
        self.assertTrue(self.runtime.arm(appointment()).controller.ready)
        self.end_all()
        self.assertTrue(self.runtime.observe().controller.all_ended)
        self.runtime.observe()
        self.now += timedelta(minutes=1)
        checked = self.runtime.check(expected_arm=1, wake_id="wake-1", wake_time=self.now).controller
        self.assertEqual("closed", checked.phase)
        self.assertTrue(checked.stop_confirmed)
        self.runtime.rearm(expected_arm=1, appointment=appointment(self.now), healthy=True)
        self.assertEqual(["arm", "shorten", "pause"], self.actions())
        self.assertEqual("timer-1", self.host.value.id)

    def test_active_thread_does_not_record_any_terminal_turn(self):
        self.runtime.arm(appointment())
        for status in ("completed", "failed", "interrupted"):
            with self.subTest(status=status):
                for reviewer in REVIEWERS:
                    self.host.turns[reviewer.reviewer_id] = ChildTurn(
                        reviewer.reviewer_id, reviewer.turn_id, status, "active")
                state = self.runtime.observe().controller
                self.assertEqual([], state.to_snapshot()["terminal"])
                self.assertFalse(state.all_ended)
        self.assertEqual(["arm"], self.actions())

    def test_failed_system_error_reviews_all_end_and_shorten_once(self):
        self.runtime.arm(appointment())
        for reviewer in REVIEWERS:
            self.host.turns[reviewer.reviewer_id] = ChildTurn(
                reviewer.reviewer_id, reviewer.turn_id, "failed", "systemError")
        state = self.runtime.observe().controller
        self.assertTrue(state.all_ended)
        self.assertEqual(2, len(state.to_snapshot()["terminal"]))
        self.runtime.observe()
        self.assertEqual(["arm", "shorten"], self.actions())

    def test_partial_system_error_end_does_not_shorten(self):
        self.runtime.arm(appointment())
        self.host.turns["child-1"] = ChildTurn("child-1", "turn-1", "failed", "systemError")
        state = self.runtime.observe().controller
        self.assertEqual(1, len(state.to_snapshot()["terminal"]))
        self.assertFalse(state.all_ended)
        self.assertEqual(["arm"], self.actions())

    def test_system_error_without_terminal_turn_is_not_an_end(self):
        self.runtime.arm(appointment())
        for reviewer in REVIEWERS:
            self.host.turns[reviewer.reviewer_id] = ChildTurn(
                reviewer.reviewer_id, reviewer.turn_id, "inProgress", "systemError")
        state = self.runtime.observe().controller
        self.assertEqual([], state.to_snapshot()["terminal"])
        self.assertFalse(state.all_ended)
        self.assertEqual(["arm"], self.actions())

    def test_mismatched_failed_system_error_cannot_complete_dispatch(self):
        self.runtime.arm(appointment())
        for child, turn in (("child-1", "other-turn"), ("other-child", "turn-1")):
            self.host.turns["child-1"] = ChildTurn(child, turn, "failed", "systemError")
            with self.subTest(child=child, turn=turn), self.assertRaisesRegex(RuntimeError, "reviewer_turn_changed"):
                self.runtime.observe()
        self.assertEqual([], self.repository.read().controller.to_snapshot()["terminal"])
        self.assertEqual(["arm"], self.actions())

    def test_followup_turn_cannot_complete_original_dispatch(self):
        self.runtime.arm(appointment())
        self.host.turns["child-1"] = ChildTurn("child-1", "different-turn", "completed", "idle")
        with self.assertRaisesRegex(RuntimeError, "reviewer_turn_changed"):
            self.runtime.observe()
        self.assertFalse(self.repository.read().controller.all_ended)
        self.assertEqual(["arm"], self.actions())

    def test_completed_before_arm_is_retained(self):
        self.end_all()
        self.assertTrue(self.runtime.arm(appointment()).controller.all_ended)
        self.assertEqual(["arm", "shorten"], self.actions())
        self.runtime.observe()
        self.assertEqual(["arm", "shorten"], self.actions())

    def test_direct_marker_excludes_activation_even_if_marker_is_malformed(self):
        self.path.with_name(self.path.name + ".direct.sqlite").write_bytes(b"unknown")
        for operation in (lambda: self.runtime.arm(appointment()),
                          lambda: self.runtime.rearm(expected_arm=0, appointment=appointment(), healthy=True)):
            with self.assertRaisesRegex(RuntimeError, "direct_probe_selected"):
                operation()
        self.assertEqual([], self.actions())
        self.assertEqual(0, self.repository.read().controller.arm_number)

    def test_due_check_stops_even_when_child_read_fails_and_task_has_changed(self):
        self.runtime.arm(appointment())
        self.now = appointment().due_at
        self.current = replace(BINDING, target_generation=2)
        def unavailable(_child):
            raise HostAdapterError("child_status_unavailable")
        self.host.read_child = unavailable
        checked = self.runtime.check(expected_arm=1, wake_id="wake-1", wake_time=self.now).controller
        self.assertTrue(checked.stop_confirmed)
        self.assertEqual(["arm", "pause"], self.actions())

    def test_arm_that_crosses_due_closes_timer_without_ready_claim(self):
        self.end_all()
        update = self.host.update_heartbeat
        def delayed(before, action, **kwargs):
            result = update(before, action, **kwargs)
            self.now = appointment().due_at
            return result
        self.host.update_heartbeat = delayed
        state = self.runtime.arm(appointment()).controller
        self.assertFalse(state.ready)
        self.assertEqual("closed", state.phase)
        self.assertTrue(state.stop_confirmed)
        self.assertEqual(["arm", "pause"], self.actions())

    def test_healthy_check_rearm_and_old_wake_do_not_duplicate(self):
        self.runtime.arm(appointment())
        self.now = appointment().due_at
        self.assertTrue(self.runtime.check(expected_arm=1, wake_id="wake-1", wake_time=self.now).controller.stop_confirmed)
        self.assertEqual(2, self.runtime.rearm(expected_arm=1, appointment=appointment(self.now), healthy=True).controller.arm_number)
        self.runtime.check(expected_arm=1, wake_id="wake-1", wake_time=self.now)
        self.runtime.rearm(expected_arm=1, appointment=appointment(self.now), healthy=True)
        self.assertEqual(["arm", "pause", "arm"], self.actions())

    def test_all_ended_after_check_blocks_stale_rearm(self):
        self.runtime.arm(appointment())
        self.now = appointment().due_at
        self.runtime.check(expected_arm=1, wake_id="wake-1", wake_time=self.now)
        self.end_all()
        state = self.runtime.rearm(expected_arm=1, appointment=appointment(self.now), healthy=True).controller
        self.assertEqual("closed", state.phase)
        self.assertTrue(state.stop_confirmed)
        self.assertEqual(["arm", "pause"], self.actions())

    def test_lost_response_survives_restart_without_replay(self):
        self.runtime.arm(appointment())
        self.end_all()
        self.host.fail_after_write = True
        self.assertTrue(self.runtime.observe().controller.needs_reconciliation)
        restarted = self.reopen()
        restarted.observe()
        restarted.reconcile()
        self.assertIsNotNone(self.repository.read().controller.pending)
        self.assertEqual(["arm", "shorten"], self.actions())
        self.host.fail_after_write = False
        # Fixture fact: the fake host's earlier operation has fully returned.
        self.assertIsNone(restarted.reconcile(operation_finished=True).controller.pending)
        self.assertTrue(restarted.cancel(expected_arm=1).controller.stop_confirmed)
        self.assertEqual(["arm", "shorten", "pause"], self.actions())

    def test_cancel_latches_during_unknown_shorten(self):
        self.runtime.arm(appointment())
        self.end_all()
        self.host.fail_after_write = True
        self.runtime.observe()
        cancelled = self.runtime.cancel(expected_arm=1).controller
        self.assertEqual("closed", cancelled.phase)
        self.assertFalse(cancelled.stop_confirmed)
        self.host.fail_after_write = False
        self.runtime.reconcile(operation_finished=True)
        self.assertTrue(self.runtime.cancel(expected_arm=1).controller.stop_confirmed)

    def test_changed_task_refuses_activation_but_original_parent_can_clean_up(self):
        self.runtime.arm(appointment())
        self.current = replace(BINDING, target_generation=2)
        with self.assertRaisesRegex(RuntimeError, "task_binding_changed"):
            self.runtime.observe()
        self.assertTrue(self.runtime.cancel(expected_arm=1).controller.stop_confirmed)
        self.assertEqual(["arm", "pause"], self.actions())

    def test_other_parent_or_timer_cannot_control_wait(self):
        for attribute, value in (("parent_thread_id", "other-parent"), ("automation_id", "other-timer")):
            original = getattr(self.host, attribute)
            setattr(self.host, attribute, value)
            with self.assertRaisesRegex(RuntimeError, "host_binding_mismatch"):
                self.runtime.arm(appointment())
            setattr(self.host, attribute, original)
        self.assertEqual([], self.actions())

    def test_immutable_identity_drift_is_never_overwritten(self):
        self.runtime.arm(appointment())
        self.end_all()
        self.host.value = replace(self.host.value, identity_digest="e" * 64)
        self.assertTrue(self.runtime.observe().controller.needs_reconciliation)
        self.assertEqual(["arm"], self.actions())

    def test_task_change_during_child_reads_refuses_activation(self):
        read = self.host.read_child
        def changed(child):
            self.current = replace(BINDING, target_generation=2)
            return read(child)
        self.host.read_child = changed
        with self.assertRaisesRegex(RuntimeError, "task_binding_changed"):
            self.runtime.arm(appointment())
        self.assertEqual([], self.actions())
        self.assertIsNone(self.repository.read().controller.pending)

    def test_task_change_after_intent_save_resolves_without_dispatch(self):
        calls = []
        def current():
            calls.append(1)
            return BINDING if len(calls) < 3 else replace(BINDING, target_generation=2)
        self.runtime.current_binding = current
        with self.assertRaisesRegex(RuntimeError, "task_binding_changed"):
            self.runtime.arm(appointment())
        self.assertEqual([], self.actions())
        self.assertIsNone(self.repository.read().controller.pending)

    def test_expired_rearm_is_closed_and_paused(self):
        self.runtime.arm(appointment())
        self.now = appointment().due_at
        self.runtime.check(expected_arm=1, wake_id="wake-1", wake_time=self.now)
        next_appointment = appointment(self.now)
        update = self.host.update_heartbeat
        def delayed(before, action, **kwargs):
            result = update(before, action, **kwargs)
            self.now = next_appointment.due_at
            return result
        self.host.update_heartbeat = delayed
        state = self.runtime.rearm(expected_arm=1, appointment=next_appointment, healthy=True).controller
        self.assertEqual("closed", state.phase)
        self.assertTrue(state.stop_confirmed)
        self.assertEqual(["arm", "pause", "arm", "pause"], self.actions())

    def test_owner_reacquisition_invalidates_same_execution_wait(self):
        self.runtime.arm(appointment())
        self.current = replace(BINDING, ownership_generation=2)
        with self.assertRaisesRegex(RuntimeError, "task_binding_changed"):
            self.runtime.observe()
        self.assertEqual(["arm"], self.actions())

    def test_shutdown_between_child_reads_stops_without_external_write(self):
        self.runtime.arm(appointment())
        stopped, reads = [], []
        read = self.host.read_child
        def stop_after_one(child):
            reads.append(child)
            stopped.append(True)
            return read(child)
        self.host.read_child = stop_after_one
        with self.assertRaisesRegex(RuntimeError, "observation_cancelled"):
            self.runtime.observe(cancelled=lambda: bool(stopped))
        self.assertEqual(["child-1"], reads)
        self.assertEqual(["arm"], self.actions())

    def test_shutdown_after_claim_does_not_dispatch_saved_intent(self):
        self.runtime.arm(appointment())
        self.end_all()
        def stop_when_saved():
            return self.repository.read().controller.pending is not None
        with self.assertRaisesRegex(RuntimeError, "observation_cancelled"):
            self.runtime.observe(cancelled=stop_when_saved)
        self.assertIsNone(self.repository.read().controller.pending)
        self.assertEqual(["arm"], self.actions())

    def test_shutdown_during_dispatch_still_settles_known_result(self):
        self.runtime.arm(appointment())
        self.end_all()
        stopped = []
        update = self.host.update_heartbeat
        def stop_during_update(before, action, **kwargs):
            stopped.append(True)
            return update(before, action, **kwargs)
        self.host.update_heartbeat = stop_during_update
        result = self.runtime.observe(cancelled=lambda: bool(stopped))
        self.assertIsNone(result.controller.pending)
        self.assertEqual(["arm", "shorten"], self.actions())

    def test_timezone_loss_blocks_active_operation_but_not_explicit_stop(self):
        for mode in ("cancel", "check"):
            with self.subTest(mode=mode):
                # Each subcase owns a separate already-active wait.
                if mode == "check":
                    self.setUp()
                self.runtime.arm(appointment())
                self.end_all()
                def timezone_lost(*, for_pause=False):
                    if not for_pause:
                        raise HostAdapterError("timezone_unavailable")
                    return self.host.value
                self.host.view_heartbeat = timezone_lost
                with self.assertRaises(HostAdapterError):
                    self.runtime.observe()
                if mode == "cancel":
                    state = self.runtime.cancel(expected_arm=1).controller
                else:
                    self.now = appointment().due_at
                    state = self.runtime.check(expected_arm=1, wake_id="wake-1", wake_time=self.now).controller
                self.assertTrue(state.stop_confirmed)
                self.assertEqual(["arm", "pause"], self.actions())


if __name__ == "__main__":
    unittest.main()
