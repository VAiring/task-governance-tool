"""Reviewer costs require committed identity/target evidence, not parenthood."""

from dataclasses import replace
import unittest

from tests.test_usage_attribution import THREAD, OTHER, interval, turn, response
from tests.test_usage_turn_adapter import TASK
from tests.test_review_results import FINGERPRINT
from task_governance_tool.review_session_repository import ReviewSessionBinding, ReviewSessionTarget
from task_governance_tool.review_answer_repository import CollectedReviewEndpoint
from task_governance_tool.usage_attribution import Transition, project
from task_governance_tool.usage_response_adapter import ResponsePosition
from task_governance_tool.usage_response_attribution import project_responses
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

    def intervals(self, *, boundaries=None, reviews=None, receipts=(), endpoints=(), turns=None,
                  conflicts=frozenset()):
        return reviewer_intervals(self.owners, self.turns if turns is None else turns,
                                  (self.read, self.save) if boundaries is None else boundaries,
                                  receipts, [(RECEIPT, self.target, self.binding)] if reviews is None else reviews,
                                  collected_endpoints=endpoints, conflicting_turns=conflicts)

    def endpoint(self, *, number=4):
        return CollectedReviewEndpoint(RECEIPT, turn(number), self.target, self.binding)

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

    def test_collected_endpoint_closes_review_without_a_legacy_save(self):
        intervals = self.intervals(boundaries=(self.read,), endpoints=(self.endpoint(),))
        result = project(intervals, self.turns, tuple(response(n) for n in range(1, 6)))
        self.assertEqual((intervals[0].start_turn, intervals[0].end_turn), (turn(2), turn(4)))
        self.assertEqual(intervals[0].diagnostics, ())
        self.assertEqual(result["tasks"][0]["response_keys"], [("openai", f"r{n}") for n in (2, 3, 4)])
        self.assertEqual(result["tasks"][0]["models"][0]["total_tokens"], 360)

    def test_native_source_does_not_use_a_parent_registration_or_legacy_save_echo(self):
        intervals = self.intervals(boundaries=(self.read, replace(self.save, turn_id=turn(5))),
            receipts=(ReviewReceiptTurn(OTHER, turn(5), RECEIPT),), endpoints=(self.endpoint(),))
        self.assertEqual(intervals[0].end_turn, turn(4))
        self.assertEqual(intervals[0].thread_id, THREAD)

    def test_collected_without_read_has_only_known_answer_turn_and_partial_coverage(self):
        intervals = self.intervals(boundaries=(), endpoints=(self.endpoint(),))
        result = project(intervals, self.turns, tuple(response(n) for n in range(1, 6)))
        self.assertEqual(result["tasks"][0]["response_keys"], [("openai", "r4")])
        self.assertIn("review_boundary_unknown", intervals[0].diagnostics)
        current = self.response_projection(intervals, self.turns)
        parts = [part for part in current["components"] if EXECUTION in part["executions"]]
        self.assertEqual({tuple(key) for part in parts for key in part["response_keys"]}, {("openai", "r4")})

    def test_collected_endpoint_without_numerical_observations_is_not_zero(self):
        intervals = self.intervals(boundaries=(self.read,), endpoints=(self.endpoint(),))
        result = project(intervals, self.turns, ())
        self.assertEqual(result["tasks"][0]["models"], [])
        self.assertIn("usage_pending", result["components"][0]["diagnostics"])

    def test_mismatched_collected_identity_never_borrows_a_valid_legacy_save(self):
        for changes in (
            {"target": replace(self.target, project_id="another-project")},
            {"target": replace(self.target, task_id="tg_task_" + "b" * 16)},
            {"target": replace(self.target, contract_revision=2)},
            {"target": replace(self.target, target_generation=2)},
            {"target": replace(self.target, target_value="sha256:" + "b" * 64)},
            {"binding": replace(self.binding, session_id=OTHER)},
            {"binding": replace(self.binding, execution_id="tg_execution_" + "b" * 16)},
            {"binding": replace(self.binding, original_result_digest="b" * 64)},
        ):
            with self.subTest(changes=changes):
                intervals = self.intervals(endpoints=(replace(self.endpoint(), **changes),))
                self.assertIsNone(intervals[0].end_turn)
                self.assertIn("review_boundary_unknown", intervals[0].diagnostics)

    def test_unregistered_answer_and_different_receipt_do_not_add_coverage(self):
        self.assertEqual(self.intervals(reviews=[], endpoints=(self.endpoint(),)), ())
        intervals = self.intervals(boundaries=(self.read,), endpoints=(
            replace(self.endpoint(), receipt_id="tg_review_receipt_" + "b" * 16),))
        self.assertIsNone(intervals[0].end_turn)

    def test_collected_duplicate_is_idempotent_but_conflicting_turns_are_unknown(self):
        endpoint = self.endpoint()
        self.assertEqual(self.intervals(endpoints=(endpoint,)), self.intervals(endpoints=(endpoint, endpoint)))
        intervals = self.intervals(endpoints=(endpoint, replace(endpoint, turn_id=turn(5))))
        self.assertIsNone(intervals[0].end_turn)
        self.assertIn("review_boundary_unknown", intervals[0].diagnostics)

    def test_collected_endpoints_do_not_change_direct_receipt_policy(self):
        direct = replace(self.binding, binding_source="direct", original_result_digest=None)
        intervals = self.intervals(reviews=[(RECEIPT, self.target, direct)],
            receipts=(ReviewReceiptTurn(THREAD, turn(4), RECEIPT),),
            endpoints=(replace(self.endpoint(), binding=direct),))
        self.assertIsNone(intervals[0].end_turn)

    def response_projection(self, intervals, turns, *, conflicts=frozenset()):
        numerical = tuple(response(n) for n in range(1, 6))
        positions = tuple(("a" * 64, 1, ResponsePosition(
            row.thread_id, row.turn_id, row.provider, row.response_id, n))
            for n, row in enumerate(numerical))
        transitions = (Transition("owner-start", TASK, EXECUTION, 1, "ready", "in_progress", OTHER, 1),)
        return project_responses(transitions, {}, (), positions, turns, numerical,
                                 additional=intervals, conflicting_turns=conflicts)

    def assert_unordered_collected_endpoint_preserves_read(self, turns, *, conflicts=frozenset()):
        # Keep conflicting observations until native boundary selection, so a
        # collision with another identity is not hidden by prefiltering.
        intervals = self.intervals(boundaries=(self.read,), endpoints=(self.endpoint(),),
                                   turns=turns, conflicts=conflicts)
        self.assertEqual((intervals[0].start_turn, intervals[0].end_turn), (turn(2), None))
        self.assertIn("review_boundary_unknown", intervals[0].diagnostics)
        legacy = project(intervals, turns, tuple(response(n) for n in range(1, 6)), conflicting_turns=conflicts)
        current = self.response_projection(intervals, turns, conflicts=conflicts)
        for projected in (legacy, current):
            with self.subTest(consumer="interval" if projected is legacy else "response"):
                parts = [part for part in projected["components"] if EXECUTION in part["executions"]]
                self.assertEqual({tuple(key) for part in parts for key in part["response_keys"]}, {("openai", "r2")})
                self.assertEqual(sum(model["total_tokens"] for part in parts for model in part["models"]), 120)
                self.assertTrue(all("boundary_unknown" in part["diagnostics"] for part in parts))
        without_endpoint = self.intervals(boundaries=(self.read,), turns=turns, conflicts=conflicts)
        before = project(without_endpoint, turns, tuple(response(n) for n in range(1, 6)), conflicting_turns=conflicts)
        self.assertEqual(before["tasks"][0]["response_keys"], legacy["tasks"][0]["response_keys"])

    def test_missing_native_endpoint_order_preserves_only_definite_read_in_both_projections(self):
        turns = tuple(item for item in self.turns if item.turn_id != turn(4))
        self.assert_unordered_collected_endpoint_preserves_read(turns)

    def test_conflicting_native_endpoint_order_preserves_only_definite_read_in_both_projections(self):
        turns = self.turns + (TurnObservation(THREAD, turn(4), 4500),)
        self.assert_unordered_collected_endpoint_preserves_read(turns, conflicts=frozenset({(THREAD, turn(4))}))

    def assert_unordered_collected_endpoint_without_read_has_no_membership(self, turns, *, conflicts=frozenset()):
        intervals = self.intervals(boundaries=(), endpoints=(self.endpoint(),), turns=turns, conflicts=conflicts)
        legacy = project(intervals, turns, tuple(response(n) for n in range(1, 6)), conflicting_turns=conflicts)
        current = self.response_projection(intervals, turns, conflicts=conflicts)
        for projected in (legacy, current):
            with self.subTest(consumer="interval" if projected is legacy else "response"):
                parts = [part for part in projected["components"] if EXECUTION in part["executions"]]
                self.assertTrue(parts)
                self.assertEqual({tuple(key) for part in parts for key in part["response_keys"]}, set())
                self.assertTrue(all(part["models"] == [] for part in parts))
                self.assertTrue(all("boundary_unknown" in part["diagnostics"] for part in parts))
        self.assertEqual((intervals[0].start_turn, intervals[0].end_turn), (None, None))
        self.assertIn("review_boundary_unknown", intervals[0].diagnostics)

    def test_missing_native_endpoint_order_without_read_has_no_inferred_membership(self):
        turns = tuple(item for item in self.turns if item.turn_id != turn(4))
        self.assert_unordered_collected_endpoint_without_read_has_no_membership(turns)

    def test_conflicting_native_endpoint_order_without_read_has_no_inferred_membership(self):
        turns = self.turns + (TurnObservation(THREAD, turn(4), 4500),)
        self.assert_unordered_collected_endpoint_without_read_has_no_membership(
            turns, conflicts=frozenset({(THREAD, turn(4))}))

    def test_colliding_native_endpoint_orders_keep_only_a_safe_read_in_both_projections(self):
        cases = (
            (tuple(replace(item, started_at=4000) if item.turn_id == turn(5) else item
                   for item in self.turns), frozenset()),
            (tuple(replace(item, started_at=4000) if item.turn_id in (turn(3), turn(5)) else item
                   for item in self.turns), frozenset()),
            # The conflicting partner still invalidates the endpoint's order.
            (tuple(replace(item, started_at=4000) if item.turn_id == turn(5) else item
                   for item in self.turns), frozenset({(THREAD, turn(5))})),
            (self.turns + (TurnObservation(THREAD, turn(4), 4500),), frozenset()),
            # One identity can conflict with a second through either of its
            # inconsistent observations; neither input order may hide that.
            (self.turns + (TurnObservation(THREAD, turn(5), 4000),), frozenset()),
        )
        for turns, conflicts in cases:
            for observed in (turns, tuple(reversed(turns))):
                with self.subTest(turns=observed, conflicts=conflicts):
                    self.assert_unordered_collected_endpoint_preserves_read(observed, conflicts=conflicts)
                    self.assert_unordered_collected_endpoint_without_read_has_no_membership(
                        observed, conflicts=conflicts)

    def assert_native_membership(self, turns, boundaries, expected_turns, expected_keys, *, gap,
                                 conflicts=frozenset()):
        intervals = self.intervals(boundaries=boundaries, endpoints=(self.endpoint(),), turns=turns,
                                   conflicts=conflicts)
        self.assertEqual((intervals[0].start_turn, intervals[0].end_turn), expected_turns)
        self.assertEqual("review_boundary_unknown" in intervals[0].diagnostics, gap)
        for projected in (project(intervals, turns, tuple(response(n) for n in range(1, 6)),
                                  conflicting_turns=conflicts),
                          self.response_projection(intervals, turns, conflicts=conflicts)):
            parts = [part for part in projected["components"] if EXECUTION in part["executions"]]
            self.assertTrue(parts)
            self.assertEqual({tuple(key) for part in parts for key in part["response_keys"]}, expected_keys)
            self.assertEqual(sum(model["total_tokens"] for part in parts for model in part["models"]),
                             len(expected_keys) * 120)
            self.assertTrue(all(("review_boundary_unknown" in part["diagnostics"]) == gap for part in parts))

    def test_visible_native_read_tie_retains_only_the_safe_endpoint(self):
        turns = tuple(replace(item, started_at=2000) if item.turn_id == turn(1) else item for item in self.turns)
        self.assert_native_membership(turns, (self.read,), (turn(4), turn(4)),
                                      {("openai", "r4")}, gap=True)

    def test_same_id_start_disagreement_closes_native_range_for_raw_and_stored_inputs(self):
        # The repository keeps the first start and only the conflicting ID.
        # Neither possible arrival order proves which other turns are affected.
        for alternate in (2000, 4000, 9000):
            observed = self.turns + (TurnObservation(THREAD, turn(5), alternate),)
            projections = []
            for raw in (observed, tuple(reversed(observed))):
                first = {}
                for item in raw:
                    first.setdefault((item.thread_id, item.turn_id), item)
                for turns, conflicts in ((raw, frozenset()),
                        (tuple(first.values()), frozenset({(THREAD, turn(5))}))):
                    with self.subTest(alternate=alternate, turns=turns, conflicts=conflicts):
                        self.assert_native_membership(turns, (self.read,), (turn(2), None),
                                                      {("openai", "r2")}, gap=True, conflicts=conflicts)
                        self.assert_native_membership(turns, (), (None, None), set(),
                                                      gap=True, conflicts=conflicts)
                        selected, = self.intervals(boundaries=(self.read,), endpoints=(self.endpoint(),),
                                                   turns=turns, conflicts=conflicts)
                        self.assertTrue(selected.identity_only)
                        projected = self.response_projection((selected,), turns, conflicts=conflicts)
                        projections.append(projected["components"])
                        self.assertEqual(projected["components"][0]["assignment"], "partial")
                        self.assertIn("response_binding_unknown", projected["components"][0]["diagnostics"])
            self.assertTrue(all(value == projections[0] for value in projections))

    def test_conflicted_native_read_cannot_supply_a_start_or_an_inferred_endpoint(self):
        observed = self.turns + (TurnObservation(THREAD, turn(2), 2500),)
        for turns, conflicts in ((observed, frozenset()), (tuple(reversed(observed)), frozenset()),
                (self.turns, frozenset({(THREAD, turn(2))}))):
            with self.subTest(turns=turns, conflicts=conflicts):
                self.assert_native_membership(turns, (self.read,), (None, None), set(),
                                              gap=True, conflicts=conflicts)

    def test_second_safe_read_is_retained_without_range_after_same_id_conflict(self):
        later_read = replace(self.read, turn_id=turn(3))
        observed = self.turns + (TurnObservation(THREAD, turn(2), 2500),)
        for turns, conflicts in ((observed, frozenset()),
                (self.turns, frozenset({(THREAD, turn(2))}))):
            with self.subTest(turns=turns, conflicts=conflicts):
                self.assert_native_membership(turns, (self.read, later_read), (turn(3), None),
                                              {("openai", "r3")}, gap=True, conflicts=conflicts)

    def test_native_identity_fallback_still_excludes_visible_nonconflicting_read_ties(self):
        tied = tuple(replace(item, started_at=2000) if item.turn_id == turn(1) else item
                     for item in self.turns)
        for turns, conflicts in ((tied + (TurnObservation(THREAD, turn(5), 9000),), frozenset()),
                (tied, frozenset({(THREAD, turn(5))}))):
            with self.subTest(turns=turns, conflicts=conflicts):
                self.assert_native_membership(turns, (self.read,), (None, None), set(),
                                              gap=True, conflicts=conflicts)

    def test_native_identity_fallback_chooses_the_same_eligible_read_independent_of_input_order(self):
        later_read = replace(self.read, turn_id=turn(3))
        observed = self.turns + (TurnObservation(THREAD, turn(5), 2000),)
        for turns, conflicts in ((observed, frozenset()), (tuple(reversed(observed)), frozenset()),
                (self.turns, frozenset({(THREAD, turn(5))}))):
            for boundaries in ((self.read, later_read), (later_read, self.read, self.read)):
                with self.subTest(turns=turns, conflicts=conflicts, boundaries=boundaries):
                    self.assert_native_membership(turns, boundaries, (turn(2), None),
                                                  {("openai", "r2")}, gap=True, conflicts=conflicts)

    def test_other_session_same_id_conflict_does_not_close_native_range(self):
        other = (TurnObservation(OTHER, turn(5), 5000), TurnObservation(OTHER, turn(5), 4000))
        for turns, conflicts in ((self.turns + other, frozenset()),
                (self.turns + other[:1], frozenset({(OTHER, turn(5))}))):
            with self.subTest(turns=turns, conflicts=conflicts):
                self.assert_native_membership(turns, (self.read,), (turn(2), turn(4)),
                    {("openai", "r2"), ("openai", "r3"), ("openai", "r4")}, gap=False, conflicts=conflicts)

    def test_native_same_id_identical_observations_keep_the_full_range(self):
        turns = self.turns + self.turns + (self.turns[-1],)
        self.assert_native_membership(turns, (self.read, self.read), (turn(2), turn(4)),
                                      {("openai", "r2"), ("openai", "r3"), ("openai", "r4")}, gap=False)

    def test_same_session_peer_conflict_does_not_change_legacy_or_direct_selection(self):
        conflicts = frozenset({(THREAD, turn(5))})
        filtered = tuple(item for item in self.turns if item.turn_id != turn(5))
        for source in ("handoff", "direct"):
            binding = self.binding if source == "handoff" else replace(
                self.binding, binding_source="direct", original_result_digest=None)
            reviews = [(RECEIPT, self.target, binding)]
            receipts = (ReviewReceiptTurn(THREAD, turn(4), RECEIPT),)
            with self.subTest(source=source):
                actual = self.intervals(reviews=reviews, receipts=receipts, conflicts=conflicts)
                expected = self.intervals(reviews=reviews, receipts=receipts, turns=filtered)
                self.assertEqual(actual, expected)
                self.assertEqual((actual[0].start_turn, actual[0].end_turn), (turn(2), turn(4)))

    def test_native_read_and_endpoint_collisions_never_create_membership(self):
        turns = tuple(replace(item, started_at={1: 2000, 5: 4000}.get(number, item.started_at))
                      for number, item in enumerate(self.turns, 1))
        self.assert_native_membership(turns, (self.read,), (None, None), set(), gap=True)
        same_turn_read = replace(self.read, turn_id=turn(4))
        self.assert_native_membership(turns, (same_turn_read,), (None, None), set(), gap=True)

    def test_a_second_safe_read_can_start_after_a_conflicting_read(self):
        turns = tuple(replace(item, started_at=2000) if item.turn_id == turn(1) else item for item in self.turns)
        later_read = replace(self.read, turn_id=turn(3))
        self.assert_native_membership(turns, (self.read, later_read), (turn(3), turn(4)),
                                      {("openai", "r3"), ("openai", "r4")}, gap=False)

    def test_native_order_duplicates_and_other_session_same_times_are_not_conflicts(self):
        for turns in (self.turns + self.turns,
                      self.turns + tuple(TurnObservation(OTHER, turn(n), n * 1000) for n in range(1, 6))):
            with self.subTest(turns=turns):
                self.assert_native_membership(turns, (self.read,), (turn(2), turn(4)),
                    {("openai", "r2"), ("openai", "r3"), ("openai", "r4")}, gap=False)

    def test_explicit_turn_conflicts_keep_legacy_selection_policy(self):
        conflicts = frozenset({(THREAD, turn(4))})
        filtered = tuple(item for item in self.turns if (item.thread_id, item.turn_id) not in conflicts)
        self.assertEqual(self.intervals(turns=self.turns, conflicts=conflicts), self.intervals(turns=filtered))
        collided = tuple(replace(item, started_at=4000) if item.turn_id == turn(5) else item for item in self.turns)
        self.assertEqual(self.intervals(turns=collided), self.intervals())

    def test_late_native_endpoint_order_expands_only_the_proven_interval(self):
        intervals = self.intervals(boundaries=(self.read,), endpoints=(self.endpoint(),))
        legacy = project(intervals, self.turns, tuple(response(n) for n in range(1, 6)))
        current = self.response_projection(intervals, self.turns)
        for projected in (legacy, current):
            parts = [part for part in projected["components"] if EXECUTION in part["executions"]]
            self.assertEqual({tuple(key) for part in parts for key in part["response_keys"]},
                             {("openai", "r2"), ("openai", "r3"), ("openai", "r4")})
            self.assertTrue(all("boundary_unknown" not in part["diagnostics"] for part in parts))

    def test_missing_legacy_save_order_keeps_its_existing_boundary_policy(self):
        turns = tuple(item for item in self.turns if item.turn_id != turn(4))
        intervals = self.intervals(turns=turns)
        self.assertEqual((intervals[0].start_turn, intervals[0].end_turn), (turn(4), turn(4)))
        self.assertIn("review_boundary_unknown", intervals[0].diagnostics)
