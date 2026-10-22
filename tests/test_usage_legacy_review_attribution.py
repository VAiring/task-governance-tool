"""Current legacy review intervals cannot claim exclusive uncertain ranges."""

from dataclasses import replace
import unittest

from tests import test_usage_review_attribution as fixtures
from tests.test_usage_review_attribution import EXECUTION, RECEIPT
from tests.test_usage_attribution import THREAD, OTHER, turn
from task_governance_tool.usage_review_attribution import ReviewReceiptTurn, reviewer_intervals
from task_governance_tool.usage_turn_adapter import TurnObservation


class LegacyReviewAttributionTests(unittest.TestCase):
    setUp = fixtures.UsageReviewAttributionTests.setUp
    response_projection = fixtures.UsageReviewAttributionTests.response_projection
    endpoint = fixtures.UsageReviewAttributionTests.endpoint

    def intervals(self, source, *, turns=None, boundaries=None, conflicts=frozenset(),
                  conservative=True, endpoints=()):
        binding = self.binding if source == "handoff" else replace(
            self.binding, binding_source="direct", original_result_digest=None)
        return reviewer_intervals(self.owners, self.turns if turns is None else turns,
            (self.read, self.save) if boundaries is None else boundaries,
            (ReviewReceiptTurn(THREAD, turn(4), RECEIPT),), [(RECEIPT, self.target, binding)],
            collected_endpoints=endpoints, conflicting_turns=conflicts, conservative_legacy=conservative)

    def check(self, source, expected, *, assignment="partial", turns=None,
              boundaries=None, conflicts=frozenset()):
        turns = self.turns if turns is None else turns
        intervals = self.intervals(source, turns=turns, boundaries=boundaries, conflicts=conflicts)
        projected = self.response_projection(intervals, turns, conflicts=conflicts)
        parts = [part for part in projected["components"] if EXECUTION in part["executions"]]
        self.assertEqual({tuple(key) for part in parts for key in part["response_keys"]},
                         {("openai", f"r{number}") for number in expected})
        self.assertTrue(all(part["assignment"] == assignment for part in parts), parts)
        if assignment == "partial":
            self.assertTrue(all("review_boundary_unknown" in part["diagnostics"] for part in parts))
            self.assertTrue(all("response_binding_unknown" in part["diagnostics"] for part in parts))
        return parts

    def test_peer_start_disagreement_matches_raw_reversed_and_first_retained_inputs(self):
        for source in ("handoff", "direct"):
            for alternate in (2000, 4000, 9000):
                observed = self.turns + (TurnObservation(THREAD, turn(5), alternate),)
                projections = []
                for raw in (observed, tuple(reversed(observed))):
                    first = {}
                    for item in raw:
                        first.setdefault((item.thread_id, item.turn_id), item)
                    for turns, conflicts in ((raw, frozenset()),
                            (tuple(first.values()), frozenset({(THREAD, turn(5))}))):
                        with self.subTest(source=source, alternate=alternate, conflicts=conflicts):
                            projections.append(self.check(source, {2}, turns=turns, conflicts=conflicts))
                            self.check(source, set(), turns=turns, boundaries=(self.save,), conflicts=conflicts)
                self.assertTrue(all(value == projections[0] for value in projections))

    def test_endpoint_tie_missing_order_and_reverse_preserve_only_read(self):
        cases = (
            tuple(replace(item, started_at=4000) if item.turn_id == turn(5) else item for item in self.turns),
            tuple(item for item in self.turns if item.turn_id != turn(4)),
            tuple(replace(item, started_at=500) if item.turn_id == turn(4) else item for item in self.turns),
        )
        for source in ("handoff", "direct"):
            for turns in cases:
                with self.subTest(source=source, turns=turns):
                    before = self.check(source, {2}, turns=turns)
                    self.assertEqual(before, self.check(source, {2}, turns=tuple(reversed(turns))))

    def test_conflicted_or_tied_read_is_not_an_identity_fallback(self):
        cases = (
            self.turns + (TurnObservation(THREAD, turn(2), 2500),),
            tuple(replace(item, started_at=2000) if item.turn_id == turn(1) else item for item in self.turns)
                + (TurnObservation(THREAD, turn(5), 9000),),
        )
        for source in ("handoff", "direct"):
            for turns in cases:
                with self.subTest(source=source, turns=turns):
                    self.check(source, set(), turns=turns)
                    later = replace(self.read, turn_id=turn(3))
                    self.check(source, {3}, turns=turns, boundaries=(self.read, later, self.save))

    def test_safe_endpoint_without_read_is_partial_and_unknown_endpoint_adds_nothing(self):
        for source in ("handoff", "direct"):
            with self.subTest(source=source):
                self.check(source, {4}, boundaries=(self.save,))
                missing = tuple(item for item in self.turns if item.turn_id != turn(4))
                self.check(source, set(), turns=missing, boundaries=(self.save,))
        self.check("handoff", {2}, boundaries=(self.read,))

    def test_ordered_duplicates_and_other_sessions_remain_exclusive(self):
        others = tuple(TurnObservation(OTHER, turn(n), n * 1000) for n in range(1, 6))
        for source in ("handoff", "direct"):
            for turns, conflicts in ((self.turns, frozenset()), (self.turns * 2, frozenset()),
                    (self.turns + others, frozenset({(OTHER, turn(5))}))):
                with self.subTest(source=source, conflicts=conflicts):
                    self.check(source, {2, 3, 4}, assignment="exclusive", turns=turns, conflicts=conflicts)
                    self.assertEqual(self.intervals(source, turns=turns, conflicts=conflicts),
                        self.intervals(source, turns=turns, conflicts=conflicts, conservative=False))

    def test_native_route_and_historical_selection_are_not_reinterpreted(self):
        conflicts = frozenset({(THREAD, turn(5))})
        for source in ("handoff", "direct"):
            retained, = self.intervals(source, conflicts=conflicts, conservative=False)
            self.assertEqual((retained.start_turn, retained.end_turn), (turn(2), turn(4)))
            self.assertFalse(retained.identity_only)
        for conflicts in (frozenset(), conflicts):
            for boundaries in ((self.read,), ()):
                self.assertEqual(self.intervals("handoff", conflicts=conflicts, boundaries=boundaries,
                    endpoints=(self.endpoint(),)), self.intervals("handoff", conflicts=conflicts,
                    boundaries=boundaries, endpoints=(self.endpoint(),), conservative=False))
