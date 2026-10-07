"""Offline state/race tests; fixtures assert no actual host capability."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.review_wait_controller import (
    Appointment, ControllerError, Reservation, Reviewer, ReviewWaitController,
    SHORT_RULE, WaitBinding,
)


NOW = datetime(2026, 10, 6, 1, 0, 0, tzinfo=timezone.utc)
RULE = "FREQ=DAILY;BYHOUR=1;BYMINUTE=10;BYSECOND=0;COUNT=1"
BINDING = WaitBinding("wait-1", "project-1", "a" * 64, 1, "task-1", "execution-1", 1,
                      "working_tree", "sha256:" + "b" * 64, "c" * 40, 1, "manifest-1", "parent-1", 1)
REVIEWERS = (Reviewer("child-1", "turn-1"), Reviewer("child-2", "turn-2"))
RESERVATION = Reservation("timer-1", "parent-1", RULE, "UTC", "PAUSED", "d" * 64)


def appointment(now=NOW):
    due = now + timedelta(minutes=10)
    rule = f"FREQ=DAILY;BYHOUR={due.hour};BYMINUTE={due.minute};BYSECOND={due.second};COUNT=1"
    return Appointment(now, due, rule, "UTC")


def controller():
    return ReviewWaitController(BINDING, REVIEWERS, RESERVATION)


def confirm(wait, intent, now=NOW):
    assert intent is not None
    return wait.settle(intent.operation_id, readback=intent.after, now=now, operation_finished=True)


def arm(wait):
    intent = wait.arm(parent_thread_id="parent-1", binding=BINDING, appointment=appointment(),
                      readback=wait.reservation, now=NOW)
    assert confirm(wait, intent)
    return intent


def end_all(wait):
    for reviewer, status in zip(REVIEWERS, ("completed", "failed")):
        wait.observe_terminal(binding=BINDING, reviewer=reviewer, status=status)


def shorten(wait, now=NOW):
    return wait.maybe_shorten(binding=BINDING, expected_arm=wait.arm_number,
                              readback=wait.reservation, now=now)


def check(wait, *, arm_number=None, at=None, wake_id="wake-1"):
    return wait.check(parent_thread_id="parent-1", binding=BINDING,
                      expected_arm=wait.arm_number if arm_number is None else arm_number,
                      wake_id=wake_id, wake_time=appointment().due_at if at is None else at,
                      readback=wait.reservation)


def cancel(wait):
    return wait.cancel(parent_thread_id="parent-1", binding=BINDING,
                       expected_arm=wait.arm_number, readback=wait.reservation)


def rearm(wait, *, at=None, arm_number=None, healthy=True):
    at = NOW + timedelta(minutes=10) if at is None else at
    return wait.rearm(parent_thread_id="parent-1", binding=BINDING,
                      expected_arm=wait.arm_number if arm_number is None else arm_number,
                      appointment=appointment(at), readback=wait.reservation, now=at, healthy=healthy)


class ReviewWaitControllerTests(unittest.TestCase):
    def assert_roundtrip(self, wait):
        snapshot = wait.to_snapshot()
        encoded = json.dumps(snapshot, allow_nan=False)
        rebuilt = ReviewWaitController.from_snapshot(json.loads(encoded))
        self.assertEqual(snapshot, rebuilt.to_snapshot())
        return rebuilt

    def test_arm_requires_intent_then_exact_confirmation(self):
        wait = controller()
        intent = wait.arm(parent_thread_id="parent-1", binding=BINDING, appointment=appointment(),
                          readback=RESERVATION, now=NOW)
        self.assertEqual((wait.arm_number, wait.ready, intent.kind), (0, False, "arm"))
        self.assertEqual(intent.before.timer_id, intent.after.timer_id)
        self.assertEqual(intent.after.identity_digest, RESERVATION.identity_digest)
        self.assertIsNone(wait.arm(parent_thread_id="parent-1", binding=BINDING, appointment=appointment(),
                                 readback=RESERVATION, now=NOW))
        rebuilt = self.assert_roundtrip(wait)
        self.assertTrue(confirm(rebuilt, intent))
        self.assertEqual((rebuilt.arm_number, rebuilt.ready), (1, True))
        self.assert_roundtrip(rebuilt)

    def test_exact_dispatched_turn_and_only_actual_terminal_statuses(self):
        wait = controller()
        for status in ("running", "waiting", "SubagentStop", "original_saved", "notLoaded"):
            self.assertFalse(wait.observe_terminal(binding=BINDING, reviewer=REVIEWERS[0], status=status))
        self.assertFalse(wait.observe_terminal(binding=BINDING, reviewer=Reviewer("child-1", "followup"), status="completed"))
        self.assertFalse(wait.observe_terminal(binding=BINDING, reviewer=Reviewer("unrelated", "turn-1"), status="completed"))
        self.assertTrue(wait.observe_terminal(binding=BINDING, reviewer=REVIEWERS[0], status="interrupted"))
        self.assertFalse(wait.all_ended)
        self.assertTrue(wait.observe_terminal(binding=BINDING, reviewer=REVIEWERS[1], status="failed"))
        self.assertTrue(wait.all_ended)  # no originals or verdict fields exist
        self.assertFalse(wait.observe_terminal(binding=BINDING, reviewer=REVIEWERS[0], status="completed"))
        self.assert_roundtrip(wait)

    def test_all_ended_before_arm_is_retained(self):
        wait = controller()
        end_all(wait)
        self.assertIsNone(shorten(wait))
        arm(wait)
        intent = shorten(wait)
        self.assertEqual(intent.kind, "shorten")
        self.assertEqual(intent.after.rule, SHORT_RULE)
        confirm(wait, intent)
        self.assertIsNone(shorten(wait))
        self.assert_roundtrip(wait)

    def test_full_precision_ninety_second_boundary(self):
        for remaining, expected in ((timedelta(seconds=90, microseconds=1), True),
                                    (timedelta(seconds=90), True),
                                    (timedelta(seconds=90) - timedelta(microseconds=1), False),
                                    (timedelta(0), False), (timedelta(seconds=-1), False)):
            with self.subTest(remaining=remaining):
                wait = controller()
                arm(wait)
                end_all(wait)
                intent = shorten(wait, appointment().due_at - remaining)
                self.assertEqual(intent is not None, expected)
                self.assert_roundtrip(wait)

    def test_partial_and_late_duplicate_waves_do_not_repeat_shortening(self):
        wait = controller()
        arm(wait)
        wait.observe_terminal(binding=BINDING, reviewer=REVIEWERS[0], status="completed")
        self.assertIsNone(shorten(wait))
        end_all(wait)
        intent = shorten(wait)
        self.assertFalse(wait.settle(intent.operation_id, readback=None, now=NOW))
        for _ in range(3):
            end_all(wait)
            self.assertIsNone(shorten(wait))
        self.assertTrue(wait.needs_reconciliation)
        self.assert_roundtrip(wait)
        confirm(wait, intent)
        self.assertIsNone(shorten(wait))

    def test_unknown_cannot_be_settled_from_old_snapshot_without_quiescence(self):
        wait = controller()
        arm(wait)
        end_all(wait)
        intent = shorten(wait)
        self.assertFalse(wait.settle(intent.operation_id, readback=intent.before, now=NOW))
        restored = self.assert_roundtrip(wait)
        self.assertIsNone(shorten(restored))
        self.assertTrue(restored.settle(intent.operation_id, readback=intent.before, now=NOW, operation_finished=True))
        self.assertIsNone(restored.pending)
        self.assertIsNone(shorten(restored))  # known failure does not reset one-attempt latch

    def test_rule_zone_parent_and_payload_drift_prevent_mutation(self):
        changes = ({"rule": SHORT_RULE}, {"timezone": "Asia/Tokyo"}, {"parent_thread_id": "other"},
                   {"identity_digest": "e" * 64}, {"status": "PAUSED"}, {"timer_id": "other"})
        for changed in changes:
            with self.subTest(changed=changed):
                wait = controller()
                arm(wait)
                end_all(wait)
                intent = wait.maybe_shorten(binding=BINDING, expected_arm=1,
                                            readback=replace(wait.reservation, **changed), now=NOW)
                self.assertIsNone(intent)
                self.assertTrue(wait.needs_reconciliation)
                self.assertIsNone(wait.pending)

    def test_stale_full_basis_and_parent_do_not_issue_or_cancel(self):
        changes = ({"project_id": "old"}, {"project_path_hash": "e" * 64},
                   {"project_binding_generation": 2}, {"task_id": "old"}, {"execution_id": "old"},
                   {"contract_revision": 2}, {"target_kind": "old"}, {"target_value": "old"},
                   {"target_base_revision": "old"}, {"target_generation": 2},
                   {"artifact_manifest_id": "old"}, {"parent_thread_id": "old"}, {"wait_id": "old"})
        for changed in changes:
            with self.subTest(changed=changed):
                wait = controller()
                arm(wait)
                old = replace(BINDING, **changed)
                before = wait.to_snapshot()
                self.assertIsNone(wait.cancel(parent_thread_id="parent-1", binding=old,
                                              expected_arm=1, readback=wait.reservation))
                self.assertFalse(wait.observe_terminal(binding=old, reviewer=REVIEWERS[0], status="completed"))
                self.assertEqual(before, wait.to_snapshot())
        wait = controller()
        arm(wait)
        self.assertIsNone(wait.cancel(parent_thread_id="other", binding=BINDING, expected_arm=1, readback=wait.reservation))
        self.assertEqual(wait.phase, "waiting")

    def test_cancel_racing_unknown_shorten_never_competes_or_reactivates(self):
        wait = controller()
        arm(wait)
        end_all(wait)
        shortening = shorten(wait)
        self.assertIsNone(cancel(wait))
        self.assertEqual(wait.phase, "closed")
        wait.settle(shortening.operation_id, readback=None, now=NOW)
        self.assertIsNone(cancel(wait))
        restored = self.assert_roundtrip(wait)
        confirm(restored, shortening)
        stop = cancel(restored)
        self.assertEqual((stop.kind, stop.after.status, stop.after.rule), ("pause", "PAUSED", SHORT_RULE))
        confirm(restored, stop)
        self.assertTrue(restored.stop_confirmed)
        self.assertIsNone(shorten(restored))
        self.assertIsNone(rearm(restored))
        self.assertIsNone(cancel(restored))
        self.assert_roundtrip(restored)

    def test_cancel_before_initial_arm_settles_requires_pause_after_activation(self):
        wait = controller()
        intent = wait.arm(parent_thread_id="parent-1", binding=BINDING, appointment=appointment(),
                          readback=wait.reservation, now=NOW)
        self.assertIsNone(cancel(wait))
        confirm(wait, intent)
        self.assertEqual((wait.phase, wait.arm_number, wait.ready), ("closed", 1, False))
        pause = cancel(wait)
        confirm(wait, pause)
        self.assert_roundtrip(wait)

    def test_temporary_check_stop_then_explicit_healthy_rearm_same_timer(self):
        wait = controller()
        arm(wait)
        pause = check(wait)
        self.assertEqual(wait.phase, "checking")
        self.assertIsNone(rearm(wait))
        confirm(wait, pause, appointment().due_at)
        self.assertTrue(wait.stop_confirmed)
        self.assertIsNone(rearm(wait, healthy=False))
        intent = rearm(wait)
        self.assertEqual((intent.kind, intent.after.timer_id), ("rearm", "timer-1"))
        self.assertEqual(wait.arm_number, 1)
        confirm(wait, intent, NOW + timedelta(minutes=10))
        self.assertEqual((wait.arm_number, wait.ready), (2, True))
        self.assertIsNone(rearm(wait, arm_number=1))
        self.assertIsNone(check(wait, arm_number=1))
        self.assertIsNone(check(wait, at=NOW + timedelta(minutes=15)))
        self.assert_roundtrip(wait)

    def test_early_wake_ignored_until_all_ended_and_duplicate_consumed(self):
        wait = controller()
        arm(wait)
        self.assertIsNone(check(wait, at=NOW))
        end_all(wait)
        pause = check(wait, at=NOW)
        self.assertEqual(wait.phase, "closed")
        self.assertIsNone(check(wait, at=NOW))
        confirm(wait, pause)
        self.assertIsNone(rearm(wait))
        self.assertIsNone(check(wait, at=NOW))

    def test_all_ended_during_check_blocks_rearm(self):
        wait = controller()
        arm(wait)
        pause = check(wait)
        end_all(wait)
        self.assertEqual(wait.phase, "closed")
        confirm(wait, pause)
        self.assertIsNone(rearm(wait))
        self.assert_roundtrip(wait)

    def test_all_ended_during_rearm_latches_closed_and_requires_pause(self):
        wait = controller()
        arm(wait)
        confirm(wait, check(wait))
        active = rearm(wait)
        end_all(wait)
        self.assertEqual(wait.phase, "closed")
        self.assert_roundtrip(wait)
        confirm(wait, active, NOW + timedelta(minutes=10))
        self.assertFalse(wait.ready)
        self.assertEqual(wait.arm_number, 2)
        pause = cancel(wait)
        self.assertEqual(pause.kind, "pause")
        confirm(wait, pause)
        self.assertIsNone(shorten(wait))
        self.assertIsNone(rearm(wait))
        self.assert_roundtrip(wait)

    def test_late_same_dispatch_terminal_is_valid_across_arms(self):
        wait = controller()
        arm(wait)
        confirm(wait, check(wait))
        confirm(wait, rearm(wait), NOW + timedelta(minutes=10))
        end_all(wait)
        self.assertIsNone(wait.maybe_shorten(binding=BINDING, expected_arm=1, readback=wait.reservation, now=NOW))
        self.assertEqual(shorten(wait, NOW + timedelta(minutes=10)).expected_arm, 2)

    def test_missed_occurrence_never_becomes_tomorrows_ready_arm(self):
        wait = controller()
        intent = wait.arm(parent_thread_id="parent-1", binding=BINDING, appointment=appointment(),
                          readback=wait.reservation, now=NOW)
        confirm(wait, intent, appointment().due_at)
        self.assertFalse(wait.ready)
        self.assertTrue(wait.needs_reconciliation)
        self.assertEqual(wait.phase, "checking")
        self.assertIsNone(rearm(wait))
        self.assertIsNotNone(cancel(wait))
        self.assert_roundtrip(wait)

    def test_expired_arm_with_prearm_all_ended_closes_and_roundtrips(self):
        wait = controller()
        end_all(wait)
        intent = wait.arm(parent_thread_id="parent-1", binding=BINDING, appointment=appointment(),
                          readback=wait.reservation, now=NOW)
        confirm(wait, intent, appointment().due_at)
        self.assertEqual(wait.phase, "closed")
        self.assertFalse(wait.ready)
        self.assert_roundtrip(wait)
        self.assertIsNotNone(cancel(wait))

    def test_existing_paused_ten_minute_interval_can_be_armed(self):
        existing = replace(RESERVATION, rule="FREQ=MINUTELY;INTERVAL=10")
        wait = ReviewWaitController(BINDING, REVIEWERS, existing)
        self.assert_roundtrip(wait)
        arm(wait)
        self.assertEqual(wait.reservation.rule, RULE)
        self.assertTrue(wait.ready)

    def test_zone_change_and_backwards_clock_cannot_authorize_active_write(self):
        wait = controller()
        changed = replace(appointment(), timezone="Etc/UTC")
        self.assertIsNone(wait.arm(parent_thread_id="parent-1", binding=BINDING, appointment=changed,
                                 readback=wait.reservation, now=NOW))
        arm(wait)
        end_all(wait)
        self.assertIsNone(shorten(wait, NOW - timedelta(microseconds=1)))
        self.assertTrue(wait.needs_reconciliation)

    def test_appointment_rejects_wrong_wall_clock_unknown_zone_and_fraction(self):
        due = NOW + timedelta(minutes=10)
        invalid = (
            lambda: Appointment(NOW, due, "FREQ=DAILY;BYHOUR=23;BYMINUTE=59;BYSECOND=59;COUNT=1", "UTC"),
            lambda: Appointment(NOW, due, RULE, "Unknown/Unavailable"),
            lambda: Appointment(NOW + timedelta(microseconds=1), due + timedelta(microseconds=1), RULE, "UTC"),
        )
        for create in invalid:
            with self.subTest(create=create), self.assertRaises(ControllerError):
                create()
        for alias in ("UTC", "Etc/UTC", "Etc/GMT", "GMT"):
            self.assertEqual(Appointment(NOW, due, RULE, alias).due_at, due)

    def test_named_zone_matches_actual_wall_clock(self):
        try:
            ZoneInfo("Asia/Tokyo")
        except ZoneInfoNotFoundError:
            self.skipTest("system IANA timezone database unavailable")
        rule = "FREQ=DAILY;BYHOUR=10;BYMINUTE=10;BYSECOND=0;COUNT=1"
        selected = Appointment(NOW, NOW + timedelta(minutes=10), rule, "Asia/Tokyo")
        self.assertEqual(selected.due_at, NOW + timedelta(minutes=10))
        with self.assertRaises(ControllerError):
            Appointment(NOW, NOW + timedelta(minutes=10), RULE, "Asia/Tokyo")

    def test_named_zone_rejects_both_ambiguous_fall_back_occurrences(self):
        try:
            ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            self.skipTest("system IANA timezone database unavailable")
        rule = "FREQ=DAILY;BYHOUR=1;BYMINUTE=30;BYSECOND=0;COUNT=1"
        for hour in (5, 6):
            due = datetime(2026, 11, 1, hour, 30, tzinfo=timezone.utc)
            with self.subTest(hour=hour), self.assertRaises(ControllerError):
                Appointment(due - timedelta(minutes=10), due, rule, "America/New_York")

    def test_operation_id_budget_and_sequence_exhaustion_are_bounded(self):
        with self.assertRaises(ControllerError):
            replace(BINDING, wait_id="w" * 129)
        wait = controller()
        snapshot = wait.to_snapshot()
        snapshot["operation_sequence"] = 2**53 - 1
        wait = ReviewWaitController.from_snapshot(snapshot)
        before = wait.to_snapshot()
        with self.assertRaises(ControllerError):
            arm(wait)
        self.assertEqual(wait.to_snapshot(), before)

    def test_out_of_order_settlement_and_mismatching_readback_remain_blocked(self):
        wait = controller()
        intent = wait.arm(parent_thread_id="parent-1", binding=BINDING, appointment=appointment(),
                          readback=wait.reservation, now=NOW)
        self.assertFalse(wait.settle("old", readback=intent.after, now=NOW, operation_finished=True))
        self.assertFalse(wait.settle(intent.operation_id, readback=replace(intent.after, timezone="Asia/Tokyo"),
                                     now=NOW, operation_finished=True))
        self.assertEqual(wait.pending, intent)
        self.assertTrue(wait.needs_reconciliation)
        confirm(wait, intent)
        self.assertFalse(wait.settle(intent.operation_id, readback=intent.after, now=NOW, operation_finished=True))

    def test_dataclasses_reject_unbounded_or_ambiguous_values(self):
        operations = (
            lambda: replace(BINDING, contract_revision=True),
            lambda: replace(BINDING, target_value="x" * 501),
            lambda: replace(BINDING, task_id="x" * 129),
            lambda: replace(BINDING, target_value="bad\x00revision"),
            lambda: replace(BINDING, target_generation=float("nan")),
            lambda: replace(RESERVATION, rule="FREQ=DAILY;BYHOUR=24;BYMINUTE=0;BYSECOND=0;COUNT=1"),
            lambda: replace(RESERVATION, identity_digest="raw prompt"),
            lambda: Appointment(NOW.replace(tzinfo=None), NOW, RULE, "UTC"),
            lambda: Appointment(NOW, NOW + timedelta(minutes=9), RULE, "UTC"),
            lambda: ReviewWaitController(BINDING, (), RESERVATION),
            lambda: ReviewWaitController(BINDING, (REVIEWERS[0], REVIEWERS[0]), RESERVATION),
            lambda: ReviewWaitController(BINDING, (Reviewer("parent-1", "turn"),), RESERVATION),
            lambda: ReviewWaitController(BINDING, REVIEWERS, replace(RESERVATION, status="ACTIVE")),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ControllerError):
                    operation()

    def test_admitted_public_text_and_zero_contract_are_preserved(self):
        value = replace(BINDING, task_id="日本語の Task", contract_revision=0,
                        target_kind="external_revision", target_value="公開済みの revision " + "版" * 300)
        wait = ReviewWaitController(value, REVIEWERS, RESERVATION)
        self.assertEqual(value, ReviewWaitController.from_snapshot(wait.to_snapshot()).binding)

    def test_snapshot_is_closed_bounded_and_validates_cross_field_invariants(self):
        wait = controller()
        arm(wait)
        end_all(wait)
        shorten(wait)
        original = wait.to_snapshot()
        corruptions = (
            lambda s: s.update(version=2), lambda s: s.update(version=True),
            lambda s: s.update(extra="raw"), lambda s: s["binding"].update(prompt="raw"),
            lambda s: s.update(arm=float("nan")), lambda s: s.update(arm=True),
            lambda s: s.update(shortening_attempted=False), lambda s: s.update(stop_confirmed=True),
            lambda s: s["pending"].update(expected_arm=2),
            lambda s: s["pending"]["after"].update(timer_id="other"),
            lambda s: s["pending"].update(operation_id="unrelated"),
            lambda s: s["terminal"][0].update(turn_id="followup"),
            lambda s: s["terminal"].append(s["terminal"][0]),
            lambda s: s.update(reviewers=s["reviewers"] * 100),
        )
        for corrupt in corruptions:
            value = deepcopy(original)
            corrupt(value)
            with self.subTest(value=value), self.assertRaises(ControllerError):
                ReviewWaitController.from_snapshot(value)
        self.assert_roundtrip(wait)

    def test_snapshots_are_copies_and_question_reads_do_not_cancel(self):
        wait = controller()
        arm(wait)
        original = wait.to_snapshot()
        copy = wait.to_snapshot()
        copy["reservation"]["status"] = "PAUSED"
        copy["reviewers"].clear()
        for _ in range(3):
            self.assertTrue(wait.ready)
            self.assertEqual(wait.phase, "waiting")
            self.assertEqual(wait.to_snapshot(), original)


if __name__ == "__main__":
    unittest.main()
