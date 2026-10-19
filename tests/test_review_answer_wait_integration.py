"""Existing wait/notification routes own answer lifecycle timing and recovery."""
import unittest
from unittest.mock import Mock, patch

from tests import test_review_wait_managed as fixture_support
from tests.test_review_wait_managed import CHILD, TURN, PARENT, ORIGINAL
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
from task_governance_tool.review_answer import AnswerError


class AnswerWaitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_support.ManagedFlowTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.session._bind_answers = Mock(return_value={CHILD: TURN})
        self.fixture.session._collect_answers = Mock(return_value={"collected": [1], "unavailable": []})

    def test_first_wait_is_dispatch_context_then_repeated_wait_uses_saved_binding(self):
        fixture = self.fixture
        first = fixture.call()
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["status"], "waiting")
        self.assertTrue(fixture.session._bind_answers.call_args.kwargs["fresh_context"])
        second = fixture.call()
        self.assertTrue(second["replayed"])
        self.assertFalse(fixture.session._bind_answers.call_args.kwargs["fresh_context"])
        self.assertEqual(fixture.effects.count("create"), 1)

    def test_missing_collection_keeps_existing_timer_and_reports_limitation(self):
        fixture = self.fixture
        fixture.session._bind_answers.side_effect = AnswerError("collection_unavailable", reason="missing")
        result = fixture.call()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["answer_collection"]["unavailable"][0]["failure"]["class"], "collection_unavailable")
        self.assertEqual(fixture.effects.count("create"), 1)

    def test_saved_turn_mismatch_stops_before_timer_creation(self):
        fixture = self.fixture
        fixture.session._bind_answers.return_value = {CHILD: "40000000-0000-4000-8000-000000000001"}
        result = fixture.call()
        self.assertFalse(result["ok"])
        self.assertEqual(fixture.effects, [])

    def test_ended_without_worker_collects_once_before_reviews_ended_response(self):
        fixture = self.fixture
        fixture.child = ChildTurn(CHILD, TURN, "completed", "idle")
        result = fixture.call()
        self.assertEqual(result["status"], "reviews_ended")
        self.assertEqual(result["answer_collection"], {"collected": [1], "unavailable": []})
        fixture.session._collect_answers.assert_called_once()
        self.assertEqual(fixture.session._collect_answers.call_args.kwargs,
            {"diagnostic": fixture.session._bind_answers.call_args.kwargs["diagnostic"]})
        self.assertEqual(fixture.effects, [])

    def test_owned_worker_collects_before_existing_single_notification(self):
        fixture = self.fixture
        fixture.session._collect_answers.side_effect = lambda *_args: fixture.effects.append("collect")
        self.assertTrue(fixture.call()["ok"])
        fixture.child = ChildTurn(CHILD, TURN, "completed", "idle")
        fixture.current_parent = ChildTurn(PARENT, ORIGINAL, "completed", "idle")
        fixture.until(lambda: "send" in fixture.effects)
        self.assertEqual(fixture.effects.count("collect"), 1)
        self.assertEqual(fixture.effects.count("send"), 1)
        self.assertLess(fixture.effects.index("collect"), fixture.effects.index("send"))
        self.assertEqual(fixture.session._collect_answers.call_args.kwargs, {})

    def test_parent_collection_with_wait_off_creates_no_timer_or_worker(self):
        fixture = self.fixture
        fixture.enabled = False
        with patch("task_governance_tool.setup_feature_config.answer_collection_allowed", return_value=True):
            result = fixture.session.handle("answer_collect", {"task_id": fixture_support.TASK,
                "reviewer_ids": [CHILD]}, fixture_support.META)
        self.assertEqual(result, {"ok": True, "status": "collected", "task_id": fixture_support.TASK,
            "bound": True, "collected": [1], "unavailable": []})
        self.assertTrue(fixture.session._bind_answers.call_args.kwargs["fresh_context"])
        fixture.session._collect_answers.assert_called_once()
        self.assertEqual(fixture.session._collect_answers.call_args.kwargs,
            {"explicit_recovery": True, "diagnostic": fixture.session._bind_answers.call_args.kwargs["diagnostic"]})
        self.assertEqual(fixture.effects, [])
        self.assertEqual(fixture.session.sessions, {})

    def test_parent_unknown_collection_returns_failure_without_poll_or_timer(self):
        fixture = self.fixture
        fixture.enabled = False
        fixture.session._collect_answers.return_value = {"collected": [], "unavailable": [{"slot": 1,
            "failure": {"class": "collection_unavailable", "field": None, "reason": "missing"}}]}
        with patch("task_governance_tool.setup_feature_config.answer_collection_allowed", return_value=True):
            result = fixture.session.handle("answer_collect", {"task_id": fixture_support.TASK,
                "reviewer_ids": [CHILD]}, fixture_support.META)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "collection_unavailable")
        fixture.session._collect_answers.assert_called_once()
        self.assertEqual(fixture.effects, [])

    def test_parent_recovery_with_prior_wait_cannot_reconstruct_missing_binding(self):
        fixture = self.fixture
        self.assertTrue(fixture.call()["ok"])
        fixture.enabled = False
        fixture.session._bind_answers.side_effect = AnswerError("binding_mismatch", reason="identity")
        before = list(fixture.effects)
        with patch("task_governance_tool.setup_feature_config.answer_collection_allowed", return_value=True):
            result = fixture.session.handle("answer_collect", {"task_id": fixture_support.TASK,
                "reviewer_ids": [CHILD]}, fixture_support.META)
        self.assertFalse(result["ok"])
        self.assertFalse(fixture.session._bind_answers.call_args.kwargs["fresh_context"])
        self.assertEqual(fixture.effects, before)

    def test_tracking_wait_keeps_invalid_answer_pending_and_accepts_later_turn(self):
        fixture = self.fixture
        later = "40000000-0000-4000-8000-000000000001"
        state = dict(boundary_turn_id=TURN, frozen=False, source_identity=None, terminal="unknown")
        fixture.session._answer_tracking = Mock(side_effect=lambda *_: {CHILD: dict(state)})
        ready = [False]
        def collect(*_args, **_kwargs):
            if ready[0]:
                state.update(frozen=True, terminal="completed", source_identity=dict(
                    reviewer_session_id=CHILD, turn_id=later, message_id="new-final"))
                return {"collected": [1], "unavailable": []}
            return {"collected": [], "unavailable": [dict(slot=1,
                failure=dict(**{"class": "format_invalid"}, field=None, reason="shape"))]}
        fixture.session._collect_answers.side_effect = collect
        fixture.child = ChildTurn(CHILD, TURN, "completed", "idle")
        result = fixture.call()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "waiting")
        fixture.current_parent = ChildTurn(PARENT, ORIGINAL, "completed", "idle")
        fixture.until(lambda: fixture.session._collect_answers.call_count >= 3)
        self.assertNotIn("send", fixture.effects)
        fixture.child = ChildTurn(CHILD, later, "completed", "idle")
        ready[0] = True
        fixture.until(lambda: "send" in fixture.effects)
        self.assertEqual(fixture.effects.count("create"), 1)
        self.assertEqual(fixture.effects.count("send"), 1)

    def test_frozen_tracking_result_stops_host_answer_observation(self):
        fixture = self.fixture
        later = "40000000-0000-4000-8000-000000000001"
        fixture.session._answer_tracking = Mock(return_value={CHILD: dict(boundary_turn_id=TURN,
            frozen=True, source_identity=dict(reviewer_session_id=CHILD, turn_id=later,
                message_id="accepted-final"), terminal="completed")})
        fixture.child = ChildTurn(CHILD, "50000000-0000-4000-8000-000000000001", "inProgress", "active")
        result = fixture.call()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "reviews_ended")
        # The normal whole-batch readback may run, but no worker follows the
        # reviewer's incidental newer activity after an accepted answer.
        fixture.session._collect_answers.assert_called_once()
        self.assertEqual(fixture.effects, [])


if __name__ == "__main__":
    unittest.main()
