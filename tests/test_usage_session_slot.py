"""New pending intervals preserve legacy gaps and never synthesize waiting usage."""

from dataclasses import replace
import unittest

from tests.test_usage_attribution import THREAD, turn, response
from task_governance_tool.usage_attribution import Transition, owner_intervals, project
from task_governance_tool.usage_turn_adapter import TurnObservation


class UsageSessionSlotTests(unittest.TestCase):
    def changes(self, policy):
        return tuple(Transition(name, "A", "A1", n, before, after, THREAD, policy)
                     for n, (name, before, after) in enumerate((
                         ("s", "ready", "in_progress"), ("r", "in_progress", "review_pending"),
                         ("w", "review_pending", "in_progress"), ("r2", "in_progress", "review_pending"),
                         ("d", "review_pending", "done")), 1))

    def projection(self, changes, responses=None):
        bindings = {row.transition_id: (THREAD, turn(n)) for row, n in zip(changes, (2, 4, 7, 9, 12))}
        intervals = owner_intervals(changes, bindings)
        turns = tuple(TurnObservation(THREAD, turn(n), n) for n in range(1, 15))
        values = tuple(response(n) for n in range(1, 15)) if responses is None else responses
        return intervals, project(intervals, turns, values)

    def test_new_mutual_transitions_keep_one_whole_interval(self):
        intervals, result = self.projection(self.changes(1))
        self.assertEqual(len(intervals), 1)
        self.assertEqual((intervals[0].start_turn, intervals[0].end_turn), (turn(2), turn(12)))
        self.assertEqual(result["components"][0]["models"][0]["response_count"], 11)
        self.assertIn(("openai", "r13"), result["unassigned_response_keys"])

    def test_legacy_pending_gap_stays_closed_after_policy_activation(self):
        changes = tuple(replace(row, policy_version=int(n >= 2)) for n, row in enumerate(self.changes(0)))
        intervals, result = self.projection(changes)
        self.assertEqual(len(intervals), 2)
        self.assertEqual(result["components"][0]["models"][0]["response_count"], 9)
        self.assertIn(("openai", "r5"), result["unassigned_response_keys"])
        self.assertIn(("openai", "r6"), result["unassigned_response_keys"])

    def test_pending_without_responses_adds_no_synthetic_zero_or_usage(self):
        _, result = self.projection(self.changes(1), ())
        self.assertEqual(result["components"][0]["models"], [])
        _, late = self.projection(self.changes(1), (response(12), response(12), response(12, identity="late")))
        self.assertEqual(late["components"][0]["models"][0]["response_count"], 2)

    def test_missing_exit_never_extends_into_future_session_turns(self):
        changes = self.changes(1)
        intervals = owner_intervals(changes, {"s": (THREAD, turn(2)), "r": (THREAD, turn(4))})
        result = project(intervals, tuple(TurnObservation(THREAD, turn(n), n) for n in range(1, 20)),
            tuple(response(n) for n in range(1, 20)))
        self.assertEqual(result["components"][0]["response_keys"], [("openai", "r2")])
        self.assertIn("boundary_unknown", result["components"][0]["diagnostics"])
