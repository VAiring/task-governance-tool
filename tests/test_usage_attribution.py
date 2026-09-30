"""Known inclusive-turn arithmetic, cumulative periods and shared components."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"))

from task_governance_tool.usage_attribution import Interval, Transition, owner_intervals, project
from task_governance_tool.usage_turn_adapter import TurnObservation
from task_governance_tool.usage_values import ResponseUsage


THREAD = "01234567-89ab-7cde-8fab-0123456789ab"
OTHER = "11234567-89ab-7cde-8fab-0123456789ab"


def turn(number):
    return f"{number:08x}-89ab-7cde-8fab-0123456789ab"


def response(number, *, thread=THREAD, identity=None):
    return ResponseUsage("openai", identity or f"r{number}", thread, turn(number),
                         "fixture-model", "high", (100, 20, 120, 60, 5, None))


def interval(task, execution, start, end, *, thread=THREAD, preceding=None):
    return Interval(task, execution, preceding, thread, turn(start), turn(end), True)


class UsageAttributionTests(unittest.TestCase):
    def project(self, intervals, *, count=12, extra_responses=(), turns=None):
        turns = turns if turns is not None else tuple(TurnObservation(THREAD, turn(n), n) for n in range(1, count + 1))
        return project(tuple(intervals), turns, tuple(response(n) for n in range(1, count + 1)) + extra_responses)

    def test_resume_keeps_closed_intervals_even_when_execution_changes(self):
        changes = (
            Transition("s1", "A", "A1", 1, "ready", "in_progress", THREAD),
            Transition("e1", "A", None, 2, "in_progress", "ready", THREAD),
            Transition("s2", "A", "A2", 3, "ready", "in_progress", THREAD),
            Transition("e2", "A", "A2", 3, "in_progress", "review_pending", THREAD),
            Transition("d", "A", "A2", 4, "review_pending", "done", THREAD),
        )
        bound = {identity: (THREAD, turn(n)) for identity, n in (("s1", 2), ("e1", 4), ("s2", 7), ("e2", 9), ("d", 12))}
        intervals = owner_intervals(changes, bound)
        self.assertEqual(len(intervals), 2)
        result = self.project(intervals)
        self.assertEqual(result["tasks"][0]["models"][0]["total_tokens"], 6 * 120)
        self.assertEqual(result["tasks"][0]["own_executions"], ["A1", "A2"])
        self.assertIn(("openai", "r12"), result["unassigned_response_keys"])
        self.assertIn(("openai", "r1"), result["unassigned_response_keys"])

    def test_whole_single_turn_including_late_post_done_responses(self):
        for via_review in (False, True):
            with self.subTest(via_review=via_review):
                changes = [Transition("s", "A", "A1", 1, "ready", "in_progress", THREAD)]
                if via_review:
                    changes.append(Transition("r", "A", "A1", 1, "in_progress", "review_pending", THREAD))
                changes.append(Transition("d", "A", "A1", 2, "review_pending" if via_review else "in_progress", "done", THREAD))
                intervals = owner_intervals(tuple(changes), {row.transition_id: (THREAD, turn(2)) for row in changes})
                before = self.project(intervals)
                after = self.project(intervals, extra_responses=(response(2, identity="late"), response(2)))
                self.assertEqual(before["tasks"][0]["models"][0]["total_tokens"], 120)
                self.assertEqual(after["tasks"][0]["models"][0]["total_tokens"], 240)

    def test_same_turn_in_two_resumed_intervals_is_counted_once(self):
        result = self.project([interval("A", "A1", 2, 4), interval("A", "A1", 4, 6)])
        self.assertEqual(result["tasks"][0]["models"][0]["response_count"], 5)

    def test_ab_and_bc_merge_without_duplicate_response(self):
        result = self.project([interval("A", "A1", 2, 3), interval("B", "B1", 3, 4),
                               interval("B", "B1", 7, 8), interval("C", "C1", 8, 9)])
        self.assertEqual(len(result["components"]), 1)
        self.assertEqual(result["components"][0]["executions"], ["A1", "B1", "C1"])
        self.assertEqual(result["components"][0]["models"][0]["response_count"], 6)

    def test_distinct_b_executions_do_not_join_components_by_task_id(self):
        result = self.project([interval("A", "A1", 2, 3), interval("B", "B1", 3, 4),
                               interval("B", "B2", 7, 8), interval("C", "C1", 8, 9)])
        self.assertEqual(len(result["components"]), 2)
        b = next(row for row in result["tasks"] if row["task_id"] == "B")
        self.assertEqual(b["models"][0]["response_count"], 6)

    def test_reopen_preserves_distinct_completion_periods(self):
        changes = (Transition("s", "A", "A1", 1, "ready", "in_progress", THREAD),
                   Transition("d", "A", "A1", 2, "in_progress", "done", THREAD),
                   Transition("s2", "A", "A2", 3, "done", "in_progress", THREAD),
                   Transition("d2", "A", "A2", 4, "in_progress", "done", THREAD))
        intervals = owner_intervals(changes, {name: (THREAD, turn(n)) for name, n in (("s", 2), ("d", 3), ("s2", 7), ("d2", 8))})
        result = self.project(intervals)
        self.assertEqual([row["preceding_completion"] for row in result["tasks"]], [None, "d"])
        self.assertEqual([row["models"][0]["total_tokens"] for row in result["tasks"]], [240, 240])

    def test_time_overlap_of_other_threads_never_creates_sharing(self):
        turns = tuple(TurnObservation(thread, turn(n), n) for thread in (THREAD, OTHER) for n in range(1, 5))
        result = self.project([interval("A", "A1", 2, 3), interval("B", "B1", 2, 3, thread=OTHER)],
                              turns=turns, extra_responses=tuple(response(n, thread=OTHER, identity=f"other{n}") for n in range(1, 5)))
        self.assertEqual(len(result["components"]), 2)

    def test_unknown_exit_retains_definite_entry_not_future_turns(self):
        value = replace(interval("A", "A1", 2, 3), end_turn=None)
        result = self.project([value])
        self.assertEqual(result["tasks"][0]["models"][0]["response_count"], 1)
        self.assertIn("boundary_unknown", result["components"][0]["diagnostics"])

    def test_unknown_entry_retains_only_definite_exit_turn(self):
        value = replace(interval("A", "A1", 2, 4), start_turn=None)
        result = self.project([value])
        self.assertEqual(result["tasks"][0]["response_keys"], [("openai", "r4")])
        self.assertIn("boundary_unknown", result["components"][0]["diagnostics"])

    def test_conflicting_response_excluded_and_optional_metrics_stay_unknown(self):
        conflict = replace(response(3), counts=(101, 20, 121, 60, 5, None))
        result = self.project([interval("A", "A1", 2, 3)], extra_responses=(conflict,))
        model = result["tasks"][0]["models"][0]
        self.assertEqual(model["total_tokens"], 120)
        self.assertIsNone(model["cache_write_input_tokens"])
        self.assertIn("response_conflict", result["components"][0]["diagnostics"])

    def test_open_interval_observed_only_and_never_complete(self):
        result = self.project([replace(interval("A", "A1", 2, 3), end_turn=None, closed=False)])
        self.assertIn("interval_open", result["components"][0]["diagnostics"])
        self.assertNotEqual(result["components"][0]["status"], "complete")

    def test_conflicting_turn_order_does_not_choose_last_writer(self):
        turns = (TurnObservation(THREAD, turn(2), 2), TurnObservation(THREAD, turn(2), 9))
        result = self.project([interval("A", "A1", 2, 2)], turns=turns)
        self.assertEqual(result["tasks"][0]["models"], [])
        self.assertIn("boundary_unknown", result["components"][0]["diagnostics"])


if __name__ == "__main__":
    unittest.main()
