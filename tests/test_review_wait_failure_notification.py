"""Safe one-shot failure delivery over the existing managed probe decision."""

import unittest
from dataclasses import replace
from unittest import mock
from tests import test_review_wait_managed as fixtures
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
from task_governance_tool.review_wait_runtime.review_wait_direct_repository import DirectRepository
from task_governance_tool.review_wait_runtime.request_repository import RequestRepository
from tests.test_review_continuation import ident


class FailureNoticeHostTests(unittest.TestCase):
    host = fixtures.ManagedHostTests.host

    def test_fixed_failure_header_binds_the_actual_round_and_is_not_completion(self):
        host = self.host()
        binding = replace(fixtures.BINDING, task_id=fixtures.TASK)
        host.set_wait_failure(fixtures.OTHER, binding, "reviewer_turn_changed",
            continuation_id=ident("tg_review_continuation_"), continuation_sequence=1)
        prompt = host._direct_prompt(fixtures.OTHER)
        self.assertIn('"status":"attention_required"', prompt)
        self.assertIn('"continuation_sequence":1', prompt)
        self.assertIn('"continuation_id":"' + ident("tg_review_continuation_") + '"', prompt)
        self.assertNotIn('"status":"completed"', prompt)
        with mock.patch.object(host, "_call", return_value=("accepted",)):
            try:
                host.send_direct_probe(fixtures.OTHER)
            except Exception:
                pass  # The synthetic transport body is intentionally not a receipt.
        with self.assertRaises(Exception):
            host.set_wait_failure(fixtures.OTHER, binding, "reviewer_turn_changed")

    def test_wrong_parent_or_unbound_round_is_rejected(self):
        host = self.host()
        binding = replace(fixtures.BINDING, task_id=fixtures.TASK)
        for changes in ({"continuation_sequence": 1}, {"continuation_id": ident("tg_review_continuation_"), "continuation_sequence": 0}):
            with self.subTest(changes=changes), self.assertRaises(Exception):
                host.set_wait_failure(fixtures.OTHER, binding, "reviewer_turn_changed", **changes)
        with self.assertRaises(Exception):
            host.set_wait_failure(fixtures.OTHER, replace(binding, parent_thread_id=fixtures.CHILD), "reviewer_turn_changed")


class FailureNotificationTests(unittest.TestCase):
    setUp = fixtures.ManagedFlowTests.setUp
    close_all = fixtures.ManagedFlowTests.close_all
    make = fixtures.ManagedFlowTests.make
    call = fixtures.ManagedFlowTests.call
    until = fixtures.ManagedFlowTests.until

    def factory(self, **kwargs):
        host = fixtures.ManagedFlowTests.factory(self, **kwargs)
        def failed(probe, binding, reason, **identity):
            self.notices.append((probe, binding.task_id, reason, identity))
        host.set_wait_failure = failed
        return host

    def start(self):
        self.notices = []
        result = self.call()
        self.assertTrue(result["parent_may_end"], result)
        _, request = RequestRepository(self.paths.review_wait_request_store(fixtures.PARENT, fixtures.TASK)).read()
        self.direct = DirectRepository.for_wait(self.paths.review_wait_store(request.automation_id))
        return self.hosts[request.automation_id]

    def changed(self, *, idle=True):
        if idle:
            self.current_parent = ChildTurn(fixtures.PARENT, fixtures.ORIGINAL, "completed", "idle")
        self.child = ChildTurn(fixtures.CHILD, fixtures.OTHER, "completed", "idle")

    def test_changed_turn_sends_one_failure_after_known_timer_deletion(self):
        self.start()
        self.changed()
        self.until(lambda: self.direct.read().status == "accepted")
        self.assertEqual(self.effects, ["create", "arm", "delete", "send"])
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.notices[0][2], "reviewer_turn_changed")
        self.assertEqual(self.direct.read().reason, "reviewer_turn_changed")
        self.assertEqual(self.direct.read().timer_phase, "deleted")

    def test_unknown_send_keeps_one_decision_and_never_resends(self):
        host = self.start()
        self.auto_receive = False
        host.outcome = "unknown"
        self.changed()
        self.until(lambda: self.direct.read().status == "unknown")
        self.assertEqual(self.effects.count("send"), 1)
        self.call()
        self.assertEqual(self.effects.count("send"), 1)
        self.assertEqual(len(self.notices), 1)

    def test_busy_parent_retains_failure_without_sending(self):
        self.start()
        self.changed(idle=False)
        self.until(lambda: self.direct.read().status == "suppressed")
        self.assertEqual(self.notices, [])
        self.assertNotIn("send", self.effects)

    def test_unknown_timer_deletion_prevents_failure_send(self):
        self.start()
        self.fail_delete = True
        self.changed()
        self.until(lambda: self.direct.read().timer_phase == "unknown")
        self.assertEqual(self.notices, [])
        self.assertNotIn("send", self.effects)


if __name__ == "__main__":
    unittest.main()
