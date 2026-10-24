"""Failure notification must refresh admission before irreversible deletion."""

from dataclasses import replace
from datetime import timedelta
import unittest
from unittest import mock

from tests import test_review_wait_failure_notification as failures
from tests import test_review_wait_managed as fixtures
from task_governance_tool.review_wait_runtime.review_wait_direct import DirectError
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
from task_governance_tool.review_wait_runtime.request_repository import ParentDispatchRepository


class FailureBoundaryTests(unittest.TestCase):
    setUp = failures.FailureNotificationTests.setUp
    close_all = failures.FailureNotificationTests.close_all
    make = failures.FailureNotificationTests.make
    factory = failures.FailureNotificationTests.factory
    call = failures.FailureNotificationTests.call
    until = failures.FailureNotificationTests.until
    start = failures.FailureNotificationTests.start
    changed = failures.FailureNotificationTests.changed

    def worker(self):
        return next(iter(self.session.sessions.values())).direct

    def change(self, worker, condition):
        if condition == "due":
            self.now += timedelta(minutes=11)
        elif condition == "outer":
            self.now += timedelta(minutes=21)
        elif condition == "stop":
            worker._stop.set()
        elif condition == "busy":
            self.current_parent = replace(self.current_parent, status="inProgress", thread_status="active")
        elif condition == "owner":
            self.basis = replace(self.basis, ownership_generation=self.basis.ownership_generation + 1)
        elif condition == "off":
            self.enabled = False
        elif condition == "clock":
            self.now -= timedelta(seconds=1)
        else:
            self.fail("Unknown fixture condition")

    def test_heartbeat_read_changes_stop_deletion_for_failure_and_normal_routes(self):
        for failure in (True, False):
            for condition in ("due", "outer", "stop", "busy", "owner", "off", "clock"):
                with self.subTest(failure=failure, condition=condition):
                    case = FailureBoundaryTests(methodName="runTest")
                    case.setUp()
                    try:
                        case.start()
                        worker = case.worker()
                        original = worker._heartbeat
                        injected = []
                        def observed(*args, **kwargs):
                            value = original(*args, **kwargs)
                            if not kwargs.get("cleanup") and not injected:
                                injected.append(True)
                                case.change(worker, condition)
                            return value
                        with mock.patch.object(worker, "_heartbeat", side_effect=observed):
                            if failure:
                                case.changed()
                            else:
                                case.current_parent = ChildTurn(fixtures.PARENT, fixtures.ORIGINAL, "completed", "idle")
                                case.child = ChildTurn(fixtures.CHILD, fixtures.TURN, "completed", "idle")
                            case.until(lambda: bool(injected))
                            if not failure and condition in {"due", "busy"}:
                                # Normal notification retains its existing live fallback.
                                worker._worker.join(0.05)
                                case.assertTrue(worker._worker.is_alive())
                                case.assertEqual(case.direct.read().timer_phase, "active")
                            else:
                                case.until(lambda: not worker._worker.is_alive())
                                case.assertEqual(case.direct.read().timer_phase, "paused")
                            case.assertNotIn("delete", case.effects)
                            case.assertNotIn("send", case.effects)
                            case.assertEqual(case.notices, [])
                    finally:
                        case.doCleanups()

    def test_due_before_failure_detection_never_begins_delete(self):
        self.start()
        worker = self.worker()
        self.now += timedelta(minutes=11)
        self.changed()
        self.until(lambda: not worker._worker.is_alive())
        self.assertEqual(self.effects, ["create", "arm", "pause"])
        self.assertEqual(self.direct.read().timer_phase, "paused")
        self.assertEqual(self.notices, [])

    def test_delete_started_before_cutoff_can_settle_and_send_once_after_due(self):
        host = self.start()
        original = host.delete_heartbeat
        def crossing(*args, **kwargs):
            self.assertEqual(self.direct.read().timer_phase, "deleting")
            original(*args, **kwargs)
            self.now += timedelta(minutes=11)
        with mock.patch.object(host, "delete_heartbeat", side_effect=crossing):
            self.changed()
            self.until(lambda: self.direct.read().status == "accepted")
        self.assertEqual(self.effects, ["create", "arm", "delete", "send"])
        self.assertEqual(len(self.notices), 1)

    def test_already_deleted_timer_can_deliver_failure_after_due_without_redeletion(self):
        self.start()
        worker = self.worker()
        def fail_after_deletion(*_):
            self.assertEqual(self.direct.read().timer_phase, "deleted")
            self.now += timedelta(minutes=11)
            raise DirectError("reviewer_turn_changed")
        with mock.patch.object(worker, "_before_send", side_effect=fail_after_deletion):
            self.current_parent = ChildTurn(fixtures.PARENT, fixtures.ORIGINAL, "completed", "idle")
            self.child = ChildTurn(fixtures.CHILD, fixtures.TURN, "completed", "idle")
            self.until(lambda: self.direct.read().status == "accepted")
        self.assertEqual(self.effects, ["create", "arm", "delete", "send"])
        self.assertEqual(len(self.notices), 1)

    def test_notification_observation_changes_preserve_deletion_without_sending(self):
        for condition in ("outer", "stop", "busy", "owner", "off", "clock"):
            with self.subTest(condition=condition):
                case = FailureBoundaryTests(methodName="runTest")
                case.setUp()
                try:
                    case.start()
                    worker = case.worker()
                    original = worker._notification_observation
                    def observe(*args):
                        original(*args)
                        case.change(worker, condition)
                    with mock.patch.object(worker, "_notification_observation", side_effect=observe):
                        case.changed()
                        case.until(lambda: not worker._worker.is_alive())
                    case.assertEqual(case.effects, ["create", "arm", "delete"])
                    case.assertEqual(case.direct.read().timer_phase, "deleted")
                finally:
                    case.doCleanups()

    def test_failure_dispatch_lease_contention_never_starts_delete_or_send(self):
        self.start()
        worker = self.worker()
        with ParentDispatchRepository(worker.dispatch_path(fixtures.PARENT)).serial():
            self.changed()
            self.until(lambda: not worker._worker.is_alive())
        self.assertEqual(self.effects, ["create", "arm", "pause"])
        self.assertEqual(self.notices, [])
