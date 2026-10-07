"""Source-only request/observer composition with temporary state and fake hosts."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.review_wait_controller import Reservation, Reviewer, ReviewWaitController, WaitBinding
from tools.review_wait_host import ChildTurn, HeartbeatSnapshot, HostAdapterError
from tools.review_wait_repository import ReviewWaitRepository
from tools.review_wait_service import MAX_METADATA_BYTES, ReviewWaitService, ServiceConfig, ServiceError


PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
TURN = "33333333-3333-4333-8333-333333333333"
OTHER = "44444444-4444-4444-8444-444444444444"
NOW = datetime(2026, 10, 6, 0, 0, 0, 123456, tzinfo=timezone.utc)
SECRET = "PRIVATE_EXECUTOR_VALUE_MUST_NOT_ESCAPE"
BINDING = WaitBinding("wait-1", "project-1", "a" * 64, 1, "task-1", "execution-1", 1,
                      "diff_fingerprint", "sha256:" + "b" * 64, "", 1, "manifest-1", PARENT, 1)
REVIEWERS = (Reviewer(CHILD, TURN),)
META = {"threadId": PARENT, "turnId": TURN, "nested": {"preserve": [True, SECRET]}}


@dataclass(frozen=True)
class ObserverSnapshot:
    state: str
    worker_alive: bool
    reason: str | None = None


class FakeObserver:
    def __init__(self, runtime):
        self.runtime = runtime
        self.alive, self.started, self.stop_calls = False, False, 0
        self.block_stop = False

    def start(self):
        self.started, self.alive = True, True
        return self.snapshot()

    def stop(self):
        self.stop_calls += 1
        if not self.block_stop:
            self.alive = False
        return self.snapshot()

    def snapshot(self):
        return ObserverSnapshot("running" if self.alive else "stopped", self.alive)


class FakeHost:
    parent_thread_id, automation_id, reviewer_ids = PARENT, "timer-1", (CHILD,)

    def __init__(self):
        self.value = HeartbeatSnapshot("timer-1", "heartbeat", PARENT,
            "FREQ=MINUTELY;INTERVAL=10", "UTC", "host_os", "PAUSED", 1, 1, "d" * 64)
        self.turn = ChildTurn(CHILD, TURN, "inProgress", "active")
        self.effects, self.calls = [], []
        self.fail = False

    def read_child(self, child):
        self.calls.append("read")
        return self.turn

    def view_heartbeat(self, *, for_pause=False):
        self.calls.append("view")
        return self.value

    def update_heartbeat(self, before, action, *, rrule=None):
        self.effects.append(action)
        self.value = replace(self.value, updated_at=self.value.updated_at + 1,
            status="PAUSED" if action == "pause" else "ACTIVE",
            rule="FREQ=MINUTELY;INTERVAL=1" if action == "shorten" else rrule or self.value.rule)
        if self.fail:
            raise HostAdapterError(SECRET)
        return self.value


class ReviewWaitServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-review-wait-service-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "wait.sqlite"
        self.config = ServiceConfig(self.path, self.directory / "server.mjs", self.directory, "UTC", "host_os")
        initial = Reservation("timer-1", PARENT, "FREQ=MINUTELY;INTERVAL=10", "UTC", "PAUSED", "d" * 64)
        self.repository = ReviewWaitRepository.create(self.path, ReviewWaitController(BINDING, REVIEWERS, initial))
        self.host, self.factory_calls, self.observers = FakeHost(), [], []
        self.now, self.current = NOW, BINDING
        self.service = self.make_service()
        self.addCleanup(self.service.close)

    def make_service(self, **changes):
        arguments = dict(host_factory=self.host_factory, observer_factory=self.observer_factory)
        arguments.update(changes)
        return ReviewWaitService(self.config, lambda: self.current, lambda: self.now, **arguments)

    def host_factory(self, **kwargs):
        self.factory_calls.append(kwargs)
        return self.host

    def observer_factory(self, runtime):
        observer = FakeObserver(runtime)
        self.observers.append(observer)
        return observer

    def call(self, operation, arguments=None, metadata=None):
        return self.service.handle(operation, {} if arguments is None else arguments,
                                   deepcopy(META) if metadata is None else metadata)

    def test_view_only_reads_existing_store_without_host_or_observer(self):
        before = self.path.read_bytes()
        response = self.call("view")
        self.assertTrue(response["ok"])
        self.assertEqual((response["state"]["arm"], response["state"]["phase"]), (0, "preparing"))
        self.assertFalse(response["observer_running"])
        self.assertEqual([], self.factory_calls)
        self.assertEqual([], self.observers)
        self.assertEqual(before, self.path.read_bytes())
        self.assertNotIn(SECRET, json.dumps(response))

    def test_missing_wrong_and_argument_supplied_identity_never_launch(self):
        for metadata in (None, {}, {"threadId": OTHER}, {"threadId": ""}, {"threadId": "INVALID_UUID"}):
            response = self.service.handle("arm", {}, metadata)
            self.assertFalse(response["ok"])
        self.assertFalse(self.call("arm", {"threadId": PARENT})["ok"])
        self.assertEqual([], self.factory_calls)
        self.assertEqual([], self.host.effects)

    def test_metadata_is_forwarded_unchanged_and_never_serialized(self):
        metadata = deepcopy(META)
        response = self.call("arm", metadata=metadata)
        self.assertTrue(response["ok"])
        self.assertIs(self.factory_calls[0]["metadata"], metadata)
        self.assertEqual(metadata, META)
        self.assertEqual(self.factory_calls[0]["reviewer_ids"], (CHILD,))
        self.assertEqual(self.factory_calls[0]["automation_id"], "timer-1")
        self.assertNotIn(SECRET, json.dumps(self.repository.read().controller.to_snapshot()))
        self.assertNotIn(SECRET.encode(), self.path.read_bytes())
        self.assertNotIn(SECRET, json.dumps(response))

    def test_conflicting_recognized_executor_aliases_are_rejected(self):
        for key in ("openai/threadId", "openai/thread_id", "codexThreadId", "codex_thread_id", "thread_id"):
            with self.subTest(key=key):
                self.assertEqual(self.call("arm", metadata={**META, key: OTHER}),
                                 {"ok": False, "error": "caller_mismatch"})
        for embedded in ({"thread_id": OTHER}, {"thread": {"id": OTHER}}):
            self.assertFalse(self.call("arm", metadata={**META, "x-codex-turn-metadata": json.dumps(embedded)})["ok"])
        self.assertEqual([], self.factory_calls)
        good = {**META, "openai/threadId": PARENT,
                "x-codex-turn-metadata": json.dumps({"thread_id": PARENT})}
        self.assertTrue(self.call("arm", metadata=good)["ok"])
        self.assertIs(self.factory_calls[0]["metadata"], good)

    def test_oversized_nonfinite_deep_and_nonjson_metadata_are_rejected(self):
        deep = "leaf"
        for _ in range(20):
            deep = [deep]
        for value in ("x" * (MAX_METADATA_BYTES + 1), float("nan"), deep, {1: "key"}, b"bytes"):
            with self.subTest(value_type=type(value).__name__):
                response = self.call("arm", metadata={**META, "extra": value})
                self.assertEqual(response, {"ok": False, "error": "executor_context_required"})
        self.assertEqual([], self.factory_calls)

    def test_closed_operation_arguments_reject_modes_ids_paths_and_rules(self):
        operations = (("delete", {}), ("observe", {}), ("reconcile", {"operation_finished": True}),
                      ("arm", {"rrule": "FREQ=MINUTELY;INTERVAL=1"}), ("arm", {"id": "other"}),
                      ("view", {"path": SECRET}), ("cancel", {"expected_arm": True}),
                      ("rearm", {"expected_arm": 1, "healthy": 1}),
                      ("check", {"expected_arm": 1, "wake_id": "x" * 129, "wake_time": NOW.isoformat()}))
        for operation, arguments in operations:
            with self.subTest(operation=operation, arguments=arguments):
                self.assertEqual(self.call(operation, arguments), {"ok": False, "error": "invalid_request"})
        self.assertEqual([], self.factory_calls)

    def test_arm_generates_exact_ten_minute_whole_second_rule(self):
        response = self.call("arm")
        self.assertTrue(response["state"]["ready"])
        self.assertTrue(response["state"]["wait_ready"])
        self.assertEqual(response["state"]["appointment_due_at"], "2026-10-06T00:10:00+00:00")
        self.assertEqual(self.host.value.rule, "FREQ=DAILY;BYHOUR=0;BYMINUTE=10;BYSECOND=0;COUNT=1")
        self.assertEqual(self.host.effects, ["arm"])
        self.assertTrue(self.observers[0].started)
        self.assertTrue(response["observer_running"])

    def test_check_stops_worker_before_pause_and_rearm_uses_new_context(self):
        self.call("arm")
        first = self.observers[0]
        self.now += timedelta(minutes=10)
        metadata = {**META, "turnId": OTHER}
        checked = self.call("check", {"expected_arm": 1, "wake_id": "wake-1", "wake_time": self.now.isoformat()}, metadata)
        self.assertTrue(checked["state"]["stop_confirmed"])
        self.assertEqual(first.stop_calls, 1)
        self.assertFalse(first.alive)
        response = self.call("rearm", {"expected_arm": 1, "healthy": True}, metadata)
        self.assertEqual(response["state"]["arm"], 2)
        self.assertEqual(len(self.observers), 2)
        self.assertEqual(self.factory_calls[0]["metadata"]["turnId"], TURN)
        self.assertEqual(self.factory_calls[-1]["metadata"]["turnId"], OTHER)
        self.assertEqual(self.host.effects, ["arm", "pause", "arm"])

    def test_unknown_observer_stop_blocks_control_without_false_success(self):
        self.call("arm")
        self.observers[0].block_stop = True
        result = self.call("cancel", {"expected_arm": 1})
        self.assertEqual(result, {"ok": False, "error": "observer_cleanup_unknown"})
        self.assertEqual(self.host.effects, ["arm"])
        self.assertEqual(len(self.factory_calls), 1)
        self.observers[0].block_stop = False
        self.assertTrue(self.call("cancel", {"expected_arm": 1})["state"]["stop_confirmed"])

    def test_unauthenticated_request_cannot_stop_running_observer(self):
        self.call("arm")
        self.assertFalse(self.call("cancel", {"expected_arm": 1}, {"threadId": OTHER})["ok"])
        self.assertTrue(self.observers[0].alive)
        self.assertEqual(self.observers[0].stop_calls, 0)

    def test_view_does_not_rebind_or_stop_observer_on_later_metadata(self):
        self.call("arm")
        self.assertTrue(self.call("view", metadata={**META, "turnId": OTHER})["observer_running"])
        self.assertEqual(len(self.factory_calls), 1)
        self.assertEqual(self.observers[0].stop_calls, 0)
        self.assertEqual(self.factory_calls[0]["metadata"]["turnId"], TURN)

    def test_stale_check_cancel_and_early_check_keep_current_observer(self):
        self.call("arm")
        requests = (("check", {"expected_arm": 0, "wake_id": "old", "wake_time": self.now.isoformat()}),
                    ("cancel", {"expected_arm": 0}),
                    ("check", {"expected_arm": 1, "wake_id": "early", "wake_time": self.now.isoformat()}))
        for operation, arguments in requests:
            old = self.observers[-1]
            with self.subTest(operation=operation, arguments=arguments):
                response = self.call(operation, arguments)
                self.assertTrue(response["ok"])
                self.assertTrue(response["state"]["ready"])
                self.assertTrue(response["observer_running"])
                self.assertFalse(old.alive)
                self.assertIsNot(old, self.observers[-1])
                self.assertEqual(self.host.effects, ["arm"])

    def test_unknown_host_result_stays_pending_and_is_never_replayed(self):
        self.host.fail = True
        response = self.call("arm")
        self.assertEqual(response, {"ok": False, "error": "wait_not_ready"})
        response = self.call("view")
        self.assertFalse(response["state"]["ready"])
        self.assertFalse(response["state"]["wait_ready"])
        self.assertTrue(response["state"]["needs_reconciliation"])
        self.assertIsNotNone(response["state"]["pending_operation"])
        self.assertEqual(self.observers, [])
        self.call("arm")
        self.call("cancel", {"expected_arm": 0})
        self.assertEqual(self.host.effects, ["arm"])
        self.assertEqual(self.repository.read().controller.phase, "closed")

    def test_later_observer_failure_is_visible_without_timer_ready_ack(self):
        self.assertTrue(self.call("arm")["state"]["wait_ready"])
        self.observers[0].snapshot = lambda: ObserverSnapshot("error", False, "observation_failed")
        before = self.path.read_bytes()
        result = self.call("view")
        self.assertTrue(result["ok"])
        self.assertTrue(result["state"]["ready"])  # timer still exists
        self.assertFalse(result["state"]["wait_ready"])
        self.assertEqual(result["observer"], {"state": "error", "worker_alive": False, "reason": "observation_failed"})
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.host.effects, ["arm"])

    def test_observer_failure_between_start_and_response_cannot_ack_ready(self):
        class FailsOnSecondSnapshot(FakeObserver):
            def snapshot(self):
                self.snapshots = getattr(self, "snapshots", 0) + 1
                # start itself reads once, service start validates the second.
                if self.snapshots >= 3:
                    self.alive = False
                    return ObserverSnapshot("error", False, "observation_failed")
                return super().snapshot()
        service = self.make_service(observer_factory=FailsOnSecondSnapshot)
        self.addCleanup(service.close)
        result = service.handle("arm", {}, META)
        self.assertEqual(result, {"ok": False, "error": "observer_unavailable"})
        state = service.handle("view", {}, META)
        self.assertTrue(state["state"]["ready"])
        self.assertFalse(state["state"]["wait_ready"])
        self.assertEqual(self.host.effects, ["arm"])

    def test_reopened_active_timer_does_not_claim_observer_ready(self):
        self.call("arm")
        self.service.close()
        reopened = self.make_service()
        self.addCleanup(reopened.close)
        result = reopened.handle("view", {}, META)
        self.assertTrue(result["state"]["ready"])
        self.assertFalse(result["state"]["wait_ready"])
        self.assertEqual(result["observer"], {"state": "not_started", "worker_alive": False, "reason": None})

    def test_changed_task_blocks_activation_but_same_parent_can_cancel(self):
        self.call("arm")
        self.current = replace(BINDING, target_generation=2)
        self.assertEqual(self.call("arm"), {"ok": False, "error": "task_binding_changed"})
        response = self.call("cancel", {"expected_arm": 1})
        self.assertTrue(response["state"]["stop_confirmed"])

    def test_invalid_future_naive_noncanonical_wake_never_launches(self):
        for value in ("x" * 2000, "2026-10-06T00:00:00", "2026-10-06T09:00:00+09:00",
                      (NOW + timedelta(seconds=1)).isoformat(), "2026-10-06T00:00:00Z"):
            with self.subTest(value=value):
                result = self.call("check", {"expected_arm": 1, "wake_id": "wake-1", "wake_time": value})
                self.assertFalse(result["ok"])
        self.assertEqual([], self.factory_calls)

    def test_missing_store_is_not_created(self):
        path = self.directory / "missing.sqlite"
        config = replace(self.config, repository_path=path)
        with self.assertRaises(ServiceError):
            ReviewWaitService(config, lambda: BINDING, lambda: NOW, host_factory=self.host_factory,
                              observer_factory=self.observer_factory)
        self.assertFalse(path.exists())
        self.assertFalse(path.with_name(path.name + ".lock").exists())

    def test_store_binding_replacement_is_rejected_before_host_creation(self):
        controller = self.repository.read().controller
        value = controller.to_snapshot()
        value["binding"]["wait_id"] = "other-wait"
        changed = ReviewWaitController.from_snapshot(value)
        with patch.object(self.service._repository, "read", return_value=replace(self.repository.read(), controller=changed)):
            self.assertEqual(self.call("arm"), {"ok": False, "error": "state_binding_changed"})
        self.assertEqual([], self.factory_calls)

    def test_writer_busy_fails_without_timer_effect(self):
        with self.repository.writer():
            result = self.call("arm")
        self.assertEqual(result, {"ok": False, "error": "writer_busy"})
        self.assertEqual([], self.host.effects)

    def test_unexpected_private_errors_are_sanitized(self):
        def unavailable(**_kwargs):
            raise ValueError(SECRET)
        service = self.make_service(host_factory=unavailable)
        self.addCleanup(service.close)
        response = service.handle("arm", {}, META)
        self.assertEqual(response, {"ok": False, "error": "operation_failed"})

    def test_close_is_idempotent_bounded_and_does_not_pause_timer(self):
        self.call("arm")
        self.assertEqual(self.service.close(), {"ok": True})
        self.assertEqual(self.service.close(), {"ok": True})
        self.assertEqual(self.host.effects, ["arm"])
        self.assertFalse(self.observers[0].alive)
        self.assertEqual(self.call("view"), {"ok": False, "error": "service_closed"})

    def test_close_reports_live_worker_without_claiming_cleanup(self):
        self.call("arm")
        self.observers[0].block_stop = True
        self.assertEqual(self.service.close(), {"ok": False, "error": "observer_cleanup_unknown"})
        self.assertEqual(self.host.effects, ["arm"])
        self.observers[0].block_stop = False
        self.assertEqual(self.service.close(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
