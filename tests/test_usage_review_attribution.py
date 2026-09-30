"""Reviewer costs require committed identity/target evidence, not parenthood."""

from dataclasses import replace
import unittest

from tests.test_usage_attribution import THREAD, OTHER, interval, turn, response
from tests.test_usage_turn_adapter import TASK
from tests.test_review_results import FINGERPRINT
from task_governance_tool.review_session_repository import ReviewSessionBinding, ReviewSessionTarget
from task_governance_tool.usage_attribution import project
from task_governance_tool.usage_review_attribution import ReviewBoundary, ReviewReceiptTurn, reviewer_intervals, project_review
from task_governance_tool.usage_turn_adapter import TurnObservation


EXECUTION = "tg_execution_0123456789abcdef"
RECEIPT = "tg_review_receipt_0123456789abcdef"
DIGEST = "a" * 64


class UsageReviewAttributionTests(unittest.TestCase):
    def setUp(self):
        self.owners = (interval(TASK, EXECUTION, 1, 1, thread=OTHER),)
        self.turns = tuple(TurnObservation(THREAD, turn(n), n * 1000) for n in range(1, 6))
        self.target = ReviewSessionTarget("fixture-project", TASK, 1, "diff_fingerprint", FINGERPRINT, "", 1)
        self.binding = ReviewSessionBinding(THREAD, EXECUTION, "handoff", DIGEST)
        self.read = ReviewBoundary(THREAD, turn(2), "fixture-project", TASK, EXECUTION, 1,
                                   "diff_fingerprint", FINGERPRINT, "", 1, "read", "")
        self.save = replace(self.read, turn_id=turn(4), phase="save", original_result_digest=DIGEST)

    def intervals(self, *, boundaries=None, reviews=None, receipts=()):
        return reviewer_intervals(self.owners, self.turns, (self.read, self.save) if boundaries is None else boundaries,
                                  receipts, [(RECEIPT, self.target, self.binding)] if reviews is None else reviews)

    def test_committed_child_or_root_is_counted_not_submitting_parent(self):
        for binding_source in ("handoff", "direct"):
            binding = self.binding if binding_source == "handoff" else replace(self.binding, binding_source="direct", original_result_digest=None)
            intervals = self.intervals(reviews=[(RECEIPT, self.target, binding)],
                receipts=(ReviewReceiptTurn(OTHER, turn(5), RECEIPT), ReviewReceiptTurn(THREAD, turn(4), RECEIPT)))
            result = project(intervals, self.turns, tuple(response(n) for n in range(1, 6)))
            self.assertEqual(result["tasks"][0]["models"][0]["total_tokens"], 360)
            self.assertEqual(intervals[0].thread_id, THREAD)

    def test_uncommitted_or_legacy_unbound_review_adds_no_participation(self):
        self.assertEqual(self.intervals(reviews=[]), ())

    def test_contract_generation_execution_or_digest_mismatch_is_not_rebound(self):
        for changed in (replace(self.save, contract_revision=2), replace(self.save, target_generation=2),
                        replace(self.save, execution_id="tg_execution_1123456789abcdef"),
                        replace(self.save, original_result_digest="b" * 64)):
            with self.subTest(changed=changed):
                intervals = self.intervals(boundaries=(self.read, changed))
                self.assertIsNone(intervals[0].end_turn)
                self.assertIn("review_boundary_unknown", intervals[0].diagnostics)

    def test_direct_without_read_counts_only_known_submission_turn_with_gap(self):
        binding = replace(self.binding, binding_source="direct", original_result_digest=None)
        intervals = self.intervals(boundaries=(), reviews=[(RECEIPT, self.target, binding)],
                                   receipts=(ReviewReceiptTurn(THREAD, turn(4), RECEIPT),))
        self.assertEqual(intervals[0].start_turn, turn(4))
        self.assertIn("review_boundary_unknown", intervals[0].diagnostics)

    def test_reviewer_own_task_and_reviewed_task_share_actual_turns(self):
        intervals = self.intervals() + (interval("B", "B1", 2, 4),)
        result = project(intervals, self.turns, tuple(response(n) for n in range(1, 6)))
        self.assertEqual(len(result["components"]), 1)
        self.assertEqual(set(result["components"][0]["executions"]), {EXECUTION, "B1"})
        self.assertEqual(result["components"][0]["models"][0]["total_tokens"], 360)

    def test_metadata_only_projection_and_parent_save_echo_exclusion(self):
        metadata = {"version": 1, "session_id": THREAD, "project_id": "fixture-project", "task_id": TASK,
                    "execution_id": EXECUTION, "contract_revision": 1, "original_result_digest": DIGEST,
                    "review_target": {"kind": "diff_fingerprint", "value": FINGERPRINT, "base_revision": "", "generation": 1}}
        saved = {"ok": True, "status": "saved", "review_session": metadata, "private": "not retained"}
        self.assertEqual(project_review(saved, THREAD, turn(4), "fixture-project"), (self.save,))
        self.assertEqual(project_review(saved, OTHER, turn(4), "fixture-project"), ())

    def test_duplicate_read_save_is_idempotent(self):
        self.assertEqual(self.intervals(), self.intervals(boundaries=(self.read, self.read, self.save, self.save)))

    def test_missing_save_retains_definite_read_turn_not_later_chat(self):
        intervals = self.intervals(boundaries=(self.read,))
        result = project(intervals, self.turns, tuple(response(n) for n in range(1, 6)))
        self.assertEqual(result["tasks"][0]["response_keys"], [("openai", "r2")])
        self.assertIn("boundary_unknown", result["components"][0]["diagnostics"])
