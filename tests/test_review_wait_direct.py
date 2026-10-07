"""Isolated direct-wake experiments; no real host, timer or Task mutation."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_review_wait_service import BINDING, CHILD, TURN, OTHER, PARENT, NOW, FakeHost
from tools.review_wait_controller import ReviewWaitController, Reviewer
from tools.review_wait_direct import DirectProbe, binding_digest
from tools.review_wait_direct_repository import DirectRepository, marker_path, marker_exists
from tools.review_wait_host import HOST_BOUNDARY_REASONS, ChildTurn, HostAdapterError
from tools.review_wait_repository import ReviewWaitRepository, RepositoryError
from tools.review_wait_runtime import reservation


ORIGINAL = "55555555-5555-4555-8555-555555555555"
NEW_TURN = "66666666-6666-4666-8666-666666666666"
META = {"threadId": PARENT, "turnId": ORIGINAL, "private": "DO_NOT_STORE_PRIVATE_META"}


class DirectHost(FakeHost):
    def __init__(self):
        super().__init__()
        self.parent = ChildTurn(PARENT, ORIGINAL, "inProgress", "active")
        self.sends = []
        self.outcome = "accepted"
        self.send_entered = threading.Event()
        self.send_release = threading.Event()
        self.block_send = False
        self.fail_send = False
        self.on_send = None
        self.on_delete = None
        self.delete_entered = threading.Event()
        self.delete_release = threading.Event()
        self.block_delete = False
        self.fail_delete = False
        self.deleted = False

    def delete_heartbeat(self, before):
        if before != self.value or self.deleted:
            raise HostAdapterError("heartbeat_changed")
        self.effects.append("delete")
        self.delete_entered.set()
        if self.on_delete:
            self.on_delete()
        if self.block_delete:
            self.delete_release.wait(5)
        if self.fail_delete:
            raise HostAdapterError("heartbeat_delete_unknown")
        self.deleted = True

    def read_parent(self):
        return self.parent

    def send_direct_probe(self, probe_id):
        self.sends.append(probe_id)
        self.send_entered.set()
        if self.on_send:
            self.on_send(probe_id)
        if self.block_send:
            self.send_release.wait(5)
        if self.fail_send:
            raise ValueError("PRIVATE_PROVIDER_RESPONSE")
        return self.outcome


class DirectProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-direct-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "wait.sqlite"
        self.host = DirectHost()
        self.controller = ReviewWaitController(BINDING, (Reviewer(CHILD, TURN),), reservation(self.host.value))
        self.wait = ReviewWaitRepository.create(self.path, self.controller)
        self.now, self.current = NOW, BINDING
        self.instances = []
        self.direct = self.make()
        self.addCleanup(self.cleanup)

    def make(self):
        instance = DirectProbe(self.path, lambda: self.current, lambda metadata: self.host,
            lambda: self.now, cycle_seconds=0.01, join_seconds=0.1)
        self.instances.append(instance)
        return instance

    def cleanup(self):
        self.host.send_release.set()
        self.host.delete_release.set()
        for instance in self.instances:
            instance.close()
            if instance._worker is not None and instance._worker.is_alive():
                instance._worker.join(5)

    def call(self, op, args=None, metadata=None, instance=None):
        return (instance or self.direct).handle(op, args or {}, META if metadata is None else metadata)

    def until(self, condition):
        end = time.monotonic() + 4
        while not condition() and time.monotonic() < end:
            time.sleep(0.01)
        self.assertTrue(condition())

    def start(self):
        result = self.call("direct_start")
        self.assertTrue(result["ok"], result)
        return result["state"]["probe_id"]

    def ended(self, *, child_status="completed", thread_status="idle"):
        self.host.turn = ChildTurn(CHILD, TURN, child_status, thread_status)
        self.host.parent = ChildTurn(PARENT, ORIGINAL, "completed", "idle")

    def status(self):
        return self.direct.repository.read().status

    def test_constructor_and_empty_status_have_no_host_or_file_effects(self):
        self.assertEqual({"ok": True, "state": None, "worker_alive": False}, self.call("direct_status"))
        self.assertFalse(self.direct.blocks_timer())
        self.assertEqual([], self.host.calls)
        self.assertFalse(marker_path(self.path).exists())

    def test_exact_all_ended_and_original_idle_send_once_without_timer_change(self):
        before = self.path.read_bytes()
        probe = self.start()
        self.ended(child_status="failed", thread_status="systemError")
        self.until(lambda: self.status() == "accepted")
        self.assertEqual([probe], self.host.sends)
        self.assertEqual([], self.host.effects)
        self.assertEqual(before, self.path.read_bytes())
        self.assertTrue(self.direct.blocks_timer())
        self.assertEqual("probe_already_exists", self.call("direct_start")["error"])
        for _ in range(3):
            self.assertTrue(self.call("direct_status")["ok"])
        self.assertEqual([probe], self.host.sends)
        self.assertNotIn(b"DO_NOT_STORE_PRIVATE_META", marker_path(self.path).read_bytes())

    def test_children_end_while_parent_active_do_not_send(self):
        self.start()
        self.host.turn = ChildTurn(CHILD, TURN, "completed", "idle")
        time.sleep(0.05)
        self.assertEqual([], self.host.sends)
        self.assertEqual("waiting", self.status())
        self.host.parent = ChildTurn(PARENT, ORIGINAL, "completed", "idle")
        self.until(lambda: self.status() == "accepted")

    def test_partial_and_active_completed_children_do_not_send(self):
        self.start()
        self.host.parent = ChildTurn(PARENT, ORIGINAL, "completed", "idle")
        self.host.turn = ChildTurn(CHILD, TURN, "completed", "active")
        time.sleep(0.05)
        self.assertEqual([], self.host.sends)
        self.host.turn = ChildTurn(CHILD, TURN, "interrupted", "notLoaded")
        self.until(lambda: self.status() == "accepted")

    def test_new_parent_turn_suppresses_instead_of_steering(self):
        self.start()
        self.ended()
        self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.until(lambda: self.status() == "suppressed")
        self.assertEqual([], self.host.sends)

    def test_changed_actual_child_turn_suppresses(self):
        self.start()
        self.ended()
        self.host.turn = ChildTurn(CHILD, OTHER, "completed", "idle")
        self.until(lambda: self.status() == "suppressed")
        self.assertEqual([], self.host.sends)

    def test_stale_basis_refuses_start_and_later_suppresses(self):
        self.current = replace(BINDING, ownership_generation=2)
        self.assertEqual("task_binding_changed", self.call("direct_start")["error"])
        self.assertFalse(self.direct.blocks_timer())
        self.current = BINDING
        self.start()
        self.current = replace(BINDING, target_generation=2)
        self.until(lambda: self.status() == "suppressed")
        self.assertEqual([], self.host.sends)

    def test_wrong_paused_identity_refuses_and_no_marker(self):
        self.host.value = replace(self.host.value, identity_digest="e" * 64)
        self.assertEqual("heartbeat_changed", self.call("direct_start")["error"])
        self.assertFalse(self.direct.blocks_timer())

    def test_paused_to_active_before_dispatch_suppresses_without_timer_write(self):
        self.start()
        self.host.value = replace(self.host.value, status="ACTIVE")
        self.ended()
        self.until(lambda: self.status() == "suppressed")
        self.assertEqual([], self.host.sends)
        self.assertEqual([], self.host.effects)

    def test_rejected_and_unknown_are_not_retried_after_restart(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                if failure:
                    self.setUp()
                self.host.outcome = "rejected"
                self.host.fail_send = failure
                probe = self.start()
                self.ended()
                self.until(lambda: self.status() == ("unknown" if failure else "rejected"))
                reopened = self.make()
                self.assertTrue(self.call("direct_status", instance=reopened)["ok"])
                self.assertEqual("probe_already_exists", self.call("direct_start", instance=reopened)["error"])
                self.assertEqual([probe], self.host.sends)

    def test_restart_waiting_never_restarts_observation(self):
        probe = self.start()
        self.assertEqual({"ok": True}, self.direct.close())
        reopened = self.make()
        self.ended()
        result = self.call("direct_status", instance=reopened)
        self.assertEqual("abandoned", result["state"]["status"])
        self.assertFalse(result["worker_alive"])
        self.assertEqual([], self.host.sends)
        self.assertEqual(probe, result["state"]["probe_id"])
        self.assertTrue(reopened.blocks_timer())

    def test_readonly_or_rejected_session_close_cannot_abandon_another_worker(self):
        probe = self.start()
        observer = self.make()
        self.assertTrue(self.call("direct_status", instance=observer)["ok"])
        self.assertTrue(observer.close()["ok"])
        rejected = self.make()
        self.assertFalse(self.call("direct_status", metadata={"threadId": OTHER}, instance=rejected)["ok"])
        self.assertTrue(rejected.close()["ok"])
        self.assertEqual("waiting", self.status())
        self.ended()
        self.until(lambda: self.status() == "accepted")
        self.assertEqual([probe], self.host.sends)

    def test_explicit_admitted_other_session_cancel_stops_waiting_owner(self):
        probe = self.start()
        other = self.make()
        result = self.call("direct_cancel", {"probe_id": probe}, instance=other)
        self.assertEqual("cancelled", result["state"]["status"])
        self.until(lambda: not self.direct._worker.is_alive())
        self.ended()
        self.assertEqual([], self.host.sends)

    def test_ack_requires_exact_probe_parent_and_new_genuine_turn(self):
        probe = self.start()
        self.ended()
        self.until(lambda: self.status() == "accepted")
        self.assertFalse(self.call("direct_ack", {"probe_id": OTHER})["ok"])
        self.assertFalse(self.call("direct_ack", {"probe_id": probe})["ok"])
        self.assertFalse(self.call("direct_ack", {"probe_id": probe}, {"threadId": OTHER, "turnId": NEW_TURN})["ok"])
        self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        result = self.call("direct_ack", {"probe_id": probe}, {**META, "turnId": NEW_TURN})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["state"]["received"])
        self.assertEqual(NEW_TURN, result["state"]["acknowledged_turn"])

    def test_ack_during_dispatch_survives_late_unknown_settlement(self):
        self.host.block_send = True
        self.host.fail_send = True
        probe = self.start()
        self.ended()
        self.assertTrue(self.host.send_entered.wait(3))
        self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        response = self.call("direct_ack", {"probe_id": probe}, {**META, "turnId": NEW_TURN})
        self.assertTrue(response["ok"], response)
        self.host.send_release.set()
        self.until(lambda: self.status() == "unknown")
        self.assertEqual(NEW_TURN, self.direct.repository.read().acknowledged_turn)

    def test_rejected_outcome_does_not_accept_ack(self):
        self.host.outcome = "rejected"
        probe = self.start()
        self.ended()
        self.until(lambda: self.status() == "rejected")
        self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertFalse(self.call("direct_ack", {"probe_id": probe}, {**META, "turnId": NEW_TURN})["ok"])

    def test_cancel_waiting_and_eof_leave_permanent_marker_without_send(self):
        probe = self.start()
        result = self.call("direct_cancel", {"probe_id": probe})
        self.assertEqual("cancelled", result["state"]["status"])
        self.assertFalse(result["worker_alive"])
        self.assertTrue(self.direct.blocks_timer())
        self.ended()
        self.assertEqual([], self.host.sends)

    def test_cancel_during_send_does_not_claim_effect_was_cancelled(self):
        self.host.block_send = True
        probe = self.start()
        self.ended()
        self.assertTrue(self.host.send_entered.wait(3))
        result = self.call("direct_cancel", {"probe_id": probe})
        self.assertEqual("observer_cleanup_unknown", result["error"])
        self.assertEqual("dispatching", self.status())
        self.host.send_release.set()
        self.until(lambda: self.status() == "accepted")
        self.assertEqual([probe], self.host.sends)
        self.assertEqual("accepted", self.call("direct_cancel", {"probe_id": probe})["state"]["status"])

    def test_deadline_expiration_never_sends(self):
        self.start()
        self.now += timedelta(minutes=10)
        self.ended()
        self.until(lambda: self.status() == "expired")
        self.assertEqual([], self.host.sends)

    def test_deadline_during_child_read_stops_before_remaining_reads(self):
        path = self.path.with_name("multiple.sqlite")
        reviewers = (Reviewer(CHILD, TURN), Reviewer(OTHER, NEW_TURN))
        ReviewWaitRepository.create(path, ReviewWaitController(BINDING, reviewers, reservation(self.host.value)))
        self.direct = DirectProbe(path, lambda: self.current, lambda meta: self.host,
            lambda: self.now, cycle_seconds=0.01, join_seconds=0.1)
        self.instances.append(self.direct)
        reads = []
        def read(child):
            reads.append(child)
            self.now += timedelta(minutes=10)
            return ChildTurn(child, TURN, "completed", "idle")
        self.host.read_child = read
        self.start()
        self.until(lambda: self.status() == "expired")
        self.assertEqual([CHILD], reads)
        self.assertEqual([], self.host.sends)

    def test_clock_regression_suppresses_without_extending_wait(self):
        self.start()
        self.now -= timedelta(seconds=1)
        self.until(lambda: self.status() == "suppressed")
        self.assertEqual("clock_changed", self.direct.repository.read().reason)
        self.assertEqual([], self.host.sends)

    def test_overlapping_local_reads_do_not_destroy_intent_or_settlement(self):
        self.host.block_send = True
        probe = self.start()
        self.ended()
        self.assertTrue(self.host.send_entered.wait(3))
        entered = threading.Event()
        def short_reader():
            with self.direct.repository._serial():
                with self.direct.repository._connection():
                    entered.set()
                    time.sleep(0.08)
        reader = threading.Thread(target=short_reader)
        reader.start()
        self.assertTrue(entered.wait(2))
        self.host.send_release.set()
        reader.join(3)
        self.until(lambda: self.status() == "accepted")
        self.assertEqual([probe], self.host.sends)

    def test_shared_wait_lease_spans_intent_and_external_send(self):
        def sent(probe):
            self.assertEqual("dispatching", self.status())
            with self.assertRaisesRegex(RepositoryError, "writer_busy"):
                with self.wait.writer():
                    pass
            # The short direct lease and SQLite transaction are both available
            # during send. Follow the repository's read/write serialization so
            # this assertion itself cannot contend with a concurrent status read.
            with self.direct.repository._serial():
                with self.direct.repository._connection(write=True):
                    pass
        self.host.on_send = sent
        self.start()
        self.ended()
        self.until(lambda: self.status() == "accepted")

    def test_unknown_dispatch_marker_is_readable_and_not_replayed(self):
        probe = self.start()
        self.direct._stop.set()
        self.direct._worker.join(3)
        self.direct.repository.update(probe, status="dispatching")
        reopened = self.make()
        result = self.call("direct_status", instance=reopened)
        self.assertEqual("unknown", result["state"]["status"])
        self.assertEqual("dispatching", self.status())
        self.assertFalse(result["worker_alive"])
        self.assertEqual([], self.host.sends)

    def test_two_instances_cannot_create_second_marker(self):
        self.start()
        other = self.make()
        self.assertEqual("probe_already_exists", self.call("direct_start", instance=other)["error"])
        self.assertIsNone(other._worker)

    def test_marker_unsafe_or_uninspectable_still_blocks_timer(self):
        self.assertFalse(marker_exists(self.path))
        marker_path(self.path).write_bytes(b"not sqlite")
        self.assertTrue(marker_exists(self.path))
        self.assertFalse(self.call("direct_status")["ok"])
        with patch.object(Path, "lstat", side_effect=PermissionError("PRIVATE_PATH")):
            self.assertTrue(marker_exists(self.path))

    def test_invalid_inputs_and_errors_never_expose_metadata(self):
        for operation, args, metadata in (("direct_start", {"prompt": "PRIVATE"}, META),
                ("direct_start", {}, {}), ("direct_start", {}, {**META, "thread_id": OTHER})):
            result = self.call(operation, args, metadata)
            self.assertFalse(result["ok"])
            self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertFalse(self.direct.blocks_timer())

    def test_start_host_error_retains_only_known_boundary_reason(self):
        for detail in HOST_BOUNDARY_REASONS | {"PRIVATE_PROVIDER_BODY"}:
            error = HostAdapterError("host_call_failed", boundary_reason=detail)
            error.args = ("PRIVATE_PROVIDER_BODY",)
            with patch.object(self.host, "read_parent", side_effect=error):
                response = self.call("direct_start")
            self.assertEqual("direct_probe_unavailable", response["error"])
            self.assertEqual(detail if detail in HOST_BOUNDARY_REASONS else "host_call_failed", response["reason"])
            self.assertNotIn("PRIVATE", json.dumps(response))
            self.assertFalse(self.direct.blocks_timer())

    def test_observer_saves_fixed_host_boundary_reason_without_provider_body(self):
        self.start()
        error = HostAdapterError("host_call_failed", boundary_reason="host_cleanup_unknown")
        error.args = ("PRIVATE_PROVIDER_BODY",)
        with patch.object(self.host, "read_child", side_effect=error):
            self.until(lambda: self.status() == "suppressed")
        record = self.direct.repository.read()
        self.assertEqual("host_cleanup_unknown", record.reason)
        self.assertEqual([], self.host.sends)
        self.assertNotIn(b"PRIVATE_PROVIDER_BODY", marker_path(self.path).read_bytes())


class DeleteProbeTests(unittest.TestCase):
    setUp = DirectProbeTests.setUp
    make = DirectProbeTests.make
    cleanup = DirectProbeTests.cleanup
    call = DirectProbeTests.call
    until = DirectProbeTests.until
    ended = DirectProbeTests.ended
    status = DirectProbeTests.status

    def start(self):
        result = self.call("direct_delete_start")
        self.assertTrue(result["ok"], result)
        return result["state"]["probe_id"]

    def finished(self):
        self.until(lambda: not self.direct._worker.is_alive())
        return self.direct.repository.read()

    def test_arm_delete_send_order_and_separate_new_turn_ack(self):
        original = self.path.read_bytes()
        probe = self.start()
        record = self.direct.repository.read()
        self.assertEqual((2, "active", "waiting"), (record.version, record.timer_phase, record.status))
        self.assertEqual("FREQ=DAILY;BYHOUR=0;BYMINUTE=10;BYSECOND=0;COUNT=1", record.timer_rule)
        self.assertEqual(["arm"], self.host.effects)
        self.host.on_send = lambda _: self.assertTrue(self.host.deleted)
        self.ended(child_status="failed", thread_status="systemError")
        self.until(lambda: self.status() == "accepted")
        self.assertEqual("deleted", self.finished().timer_phase)
        self.assertEqual(["arm", "delete"], self.host.effects)
        self.assertEqual([probe], self.host.sends)
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(self.call("direct_status")["state"]["received"])
        self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertTrue(self.call("direct_ack", {"probe_id": probe}, {**META, "turnId": NEW_TURN})["state"]["received"])

    def test_partial_review_and_active_parent_keep_ten_minute_check(self):
        self.start()
        self.host.parent = ChildTurn(PARENT, ORIGINAL, "completed", "idle")
        time.sleep(0.05)
        self.assertEqual(["arm"], self.host.effects)
        self.host.parent = ChildTurn(PARENT, ORIGINAL, "inProgress", "active")
        self.host.turn = ChildTurn(CHILD, TURN, "completed", "idle")
        time.sleep(0.05)
        self.assertEqual(["arm"], self.host.effects)
        self.assertEqual([], self.host.sends)
        self.ended(child_status="interrupted")
        self.until(lambda: self.status() == "accepted")

    def test_cancel_before_delete_pauses_once_and_restart_cannot_replay(self):
        probe = self.start()
        self.assertTrue(self.call("direct_cancel", {"probe_id": probe})["ok"])
        self.assertEqual(("cancelled", "paused"), (self.status(), self.finished().timer_phase))
        reopened = self.make()
        self.assertTrue(self.call("direct_cancel", {"probe_id": probe}, instance=reopened)["ok"])
        for operation in ("direct_delete_start", "direct_start"):
            self.assertEqual("probe_already_exists", self.call(operation, instance=reopened)["error"])
        self.assertEqual(["arm", "pause"], self.host.effects)
        self.assertEqual([], self.host.sends)

    def test_unknown_delete_stops_without_pause_or_send_and_cannot_retry(self):
        self.host.fail_delete = True
        probe = self.start()
        self.ended()
        self.until(lambda: self.status() == "suppressed")
        self.assertEqual("unknown", self.finished().timer_phase)
        self.assertEqual("heartbeat_delete_unknown", self.direct.repository.read().reason)
        other = self.make()
        self.assertFalse(self.call("direct_cancel", {"probe_id": probe}, instance=other)["ok"])
        self.assertEqual(["arm", "delete"], self.host.effects)
        self.assertEqual([], self.host.sends)

    def test_unknown_arm_retains_marker_without_worker_or_competing_pause(self):
        self.host.fail = True
        probe = self.start()
        record = self.direct.repository.read()
        self.assertEqual(("suppressed", "unknown"), (record.status, record.timer_phase))
        self.assertIsNone(self.direct._worker)
        self.assertFalse(self.call("direct_cancel", {"probe_id": probe})["ok"])
        self.assertEqual(["arm"], self.host.effects)

    def test_cancel_during_delete_retains_deleted_fact_and_suppresses_send(self):
        self.host.block_delete = True
        probe = self.start()
        self.ended()
        self.assertTrue(self.host.delete_entered.wait(3))
        self.assertEqual("deleting", self.direct.repository.read().timer_phase)
        self.assertFalse(self.call("direct_cancel", {"probe_id": probe})["ok"])
        self.host.delete_release.set()
        self.finished()
        result = self.call("direct_cancel", {"probe_id": probe})
        self.assertTrue(result["ok"], result)
        self.assertEqual(("cancelled", "deleted"), (self.status(), self.direct.repository.read().timer_phase))
        self.assertEqual(["arm", "delete"], self.host.effects)
        self.assertEqual([], self.host.sends)

    def test_basis_change_after_delete_never_sends_or_pauses(self):
        self.start()
        self.host.on_delete = lambda: setattr(self, "current", replace(BINDING, contract_revision=2))
        self.ended()
        self.until(lambda: self.status() == "suppressed")
        self.assertEqual("deleted", self.finished().timer_phase)
        self.assertEqual(["arm", "delete"], self.host.effects)
        self.assertEqual([], self.host.sends)

    def test_parent_change_after_delete_never_steers_new_turn(self):
        self.start()
        self.host.on_delete = lambda: setattr(self.host, "parent", ChildTurn(PARENT, NEW_TURN, "inProgress", "active"))
        self.ended()
        self.until(lambda: self.status() == "suppressed")
        self.assertEqual("deleted", self.finished().timer_phase)
        self.assertEqual([], self.host.sends)

    def test_deadline_and_new_parent_turn_clean_up_known_active(self):
        for case in ("deadline", "new_turn", "basis", "eof"):
            with self.subTest(case=case):
                if case != "deadline":
                    self.setUp()
                self.start()
                if case == "deadline":
                    self.now += timedelta(minutes=20)
                elif case == "new_turn":
                    self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
                elif case == "basis":
                    self.current = replace(BINDING, contract_revision=2)
                else:
                    self.assertTrue(self.direct.close()["ok"])
                self.assertEqual("paused", self.finished().timer_phase)
                self.assertEqual(["arm", "pause"], self.host.effects)
                self.assertEqual([], self.host.sends)

    def test_cleanup_failure_never_retries_pause(self):
        probe = self.start()
        self.host.fail = True
        self.assertFalse(self.call("direct_cancel", {"probe_id": probe})["ok"])
        self.assertEqual("unknown", self.finished().timer_phase)
        self.assertFalse(self.call("direct_cancel", {"probe_id": probe})["ok"])
        self.assertEqual(["arm", "pause"], self.host.effects)

    def test_due_retains_fallback_until_actual_later_parent_turn(self):
        self.start()
        self.host.parent = ChildTurn(PARENT, ORIGINAL, "completed", "idle")
        self.now = NOW + timedelta(minutes=10, seconds=5)
        time.sleep(0.05)
        record = self.direct.repository.read()
        self.assertEqual(("waiting", "active"), (record.status, record.timer_phase))
        self.assertEqual(["arm"], self.host.effects)
        self.assertTrue(self.direct._worker.is_alive())
        # Even all-ended at this point leaves the due fallback eligible.
        self.ended()
        time.sleep(0.05)
        self.assertEqual(["arm"], self.host.effects)
        self.assertEqual([], self.host.sends)
        self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertEqual("paused", self.finished().timer_phase)
        self.assertEqual("suppressed", self.status())
        self.assertEqual(["arm", "pause"], self.host.effects)

    def test_due_crossing_during_final_reads_keeps_worker_and_fallback_active(self):
        self.start()
        original = self.direct._idle_and_ended

        def cross_due(*args):
            result = original(*args)
            self.now = NOW + timedelta(minutes=10)
            return result

        with patch.object(self.direct, "_idle_and_ended", side_effect=cross_due):
            self.ended()
            self.until(lambda: self.now >= NOW + timedelta(minutes=10))
            time.sleep(0.05)
            self.assertEqual(["arm"], self.host.effects)
            self.assertTrue(self.direct._worker.is_alive())
        self.assertEqual("active", self.direct.repository.read().timer_phase)
        self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertEqual("paused", self.finished().timer_phase)

    def test_delete_started_before_due_finishes_send_if_call_crosses_due(self):
        probe = self.start()
        self.host.on_delete = lambda: setattr(self, "now", NOW + timedelta(minutes=10, seconds=5))
        self.ended()
        self.until(lambda: self.status() == "accepted")
        self.assertEqual("deleted", self.finished().timer_phase)
        self.assertEqual(["arm", "delete"], self.host.effects)
        self.assertEqual([probe], self.host.sends)

    def test_inspector_eof_does_not_pause_another_active_worker(self):
        self.start()
        inspector = self.make()
        self.assertTrue(self.call("direct_status", instance=inspector)["ok"])
        self.assertTrue(inspector.close()["ok"])
        self.assertEqual(["arm"], self.host.effects)
        self.assertEqual("active", self.direct.repository.read().timer_phase)

    def test_generic_runtime_cannot_mutate_version_two_timer(self):
        from tools.review_wait_runtime import ReviewWaitRuntime, RuntimeError
        self.start()
        runtime = ReviewWaitRuntime(self.wait, self.host, lambda: self.current, lambda: self.now)
        for operation in (runtime.observe, lambda: runtime.cancel(expected_arm=0),
                lambda: runtime.check(expected_arm=0, wake_id="wake", wake_time=NOW), runtime.reconcile):
            with self.assertRaisesRegex(RuntimeError, "direct_probe_selected"):
                operation()
        self.assertEqual(["arm"], self.host.effects)

    def test_unknown_send_keeps_deletion_and_ack_survives_settlement(self):
        self.host.block_send = self.host.fail_send = True
        probe = self.start()
        self.ended()
        self.assertTrue(self.host.send_entered.wait(3))
        self.host.parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertTrue(self.call("direct_ack", {"probe_id": probe}, {**META, "turnId": NEW_TURN})["ok"])
        self.host.send_release.set()
        self.until(lambda: self.status() == "unknown")
        record = self.finished()
        self.assertEqual(("deleted", NEW_TURN), (record.timer_phase, record.acknowledged_turn))
        self.assertEqual(["arm", "delete"], self.host.effects)

    def test_restart_pending_delete_is_inspection_only_and_cancel_cannot_compete(self):
        with patch("tools.review_wait_direct.threading.Thread.start"):
            probe = self.start()
        self.direct.repository.update(probe, timer_phase="deleting")
        reopened = self.make()
        result = self.call("direct_status", instance=reopened)
        self.assertEqual("deleting", result["state"]["timer_phase"])
        self.assertFalse(result["worker_alive"])
        self.assertFalse(self.call("direct_cancel", {"probe_id": probe}, instance=reopened)["ok"])
        self.assertEqual(["arm"], self.host.effects)
        self.assertEqual([], self.host.sends)

    def test_restart_known_active_can_only_be_explicitly_cleaned_up(self):
        with patch("tools.review_wait_direct.threading.Thread.start"):
            probe = self.start()
        reopened = self.make()
        self.assertTrue(self.call("direct_status", instance=reopened)["ok"])
        self.assertEqual(["arm"], self.host.effects)
        self.assertTrue(self.call("direct_cancel", {"probe_id": probe}, instance=reopened)["ok"])
        self.assertEqual(["arm", "pause"], self.host.effects)
        self.assertEqual("paused", self.direct.repository.read().timer_phase)

    def test_cancel_durable_before_delete_intent_prevents_delete(self):
        probe = self.start()
        update = self.direct.repository.update

        def race(identity, **changes):
            if changes.get("timer_phase") == "deleting":
                update(identity, status="cancelled")
            return update(identity, **changes)

        with patch.object(self.direct.repository, "update", side_effect=race):
            self.ended()
            self.assertEqual("paused", self.finished().timer_phase)
        self.assertEqual("cancelled", self.status())
        self.assertEqual(["arm", "pause"], self.host.effects)
        self.assertEqual([], self.host.sends)

    def test_storage_cannot_dispatch_before_confirmed_deletion_or_resurrect_timer(self):
        with patch("tools.review_wait_direct.threading.Thread.start"):
            probe = self.start()
        for changes in ({"status": "dispatching"}, {"timer_phase": "deleted"},
                        {"timer_phase": "arming"}, {"timer_updated_at": 100}):
            with self.assertRaises(RepositoryError):
                self.direct.repository.update(probe, **changes)
        self.direct.repository.update(probe, timer_phase="deleting")
        self.direct.repository.update(probe, timer_phase="deleted")
        for phase in ("active", "pausing", "deleting"):
            with self.assertRaises(RepositoryError):
                self.direct.repository.update(probe, timer_phase=phase)

    def test_changed_host_revision_cannot_delete_or_blindly_pause(self):
        probe = self.start()
        self.host.value = replace(self.host.value, updated_at=99)
        self.ended()
        self.until(lambda: self.status() == "suppressed")
        self.finished()
        self.assertFalse(self.call("direct_cancel", {"probe_id": probe})["ok"])
        self.assertEqual(["arm"], self.host.effects)
        self.assertEqual([], self.host.sends)

    def test_legacy_payload_remains_untagged_and_canonical(self):
        from tools.review_wait_direct_repository import _encode
        self.call("direct_start")
        record = self.direct.repository.read()
        value = json.loads(_encode(record))
        self.assertEqual(1, record.version)
        self.assertEqual({"probe_id", "binding_digest", "original_parent_turn", "status", "created_at",
                          "deadline", "acknowledged_turn", "reason"}, set(value))
        self.assertNotIn("timer_", _encode(record))


if __name__ == "__main__":
    unittest.main()
