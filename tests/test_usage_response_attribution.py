"""Response boundaries, whole-response sharing and per-execution unions."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.usage_attribution import Transition, Interval
from task_governance_tool.usage_response_adapter import ResponsePosition, ResponseBoundary
from task_governance_tool.usage_response_attribution import project_responses
from task_governance_tool.usage_turn_adapter import TurnObservation
from tests.test_usage_response_adapter import THREAD, turn, response


A, B, C = ("tg_task_" + digit * 16 for digit in "abc")
X, Y, Z = ("tg_execution_" + digit * 16 for digit in "abc")
SOURCE = "a" * 64


class ResponsePartitionTests(unittest.TestCase):
    def build(self, actions, *, count=8, checks=(), additional=(), second_turn=None, missing=(), unbound=(), pending=()):
        transitions, bindings = [], {}
        generations = {}
        for index, (task, execution, previous, status, number) in enumerate(actions):
            transition = "t" + str(index)
            generation = generations.get(task, 0) + 1
            generations[task] = generation
            transitions.append(Transition(transition, task, execution, generation, previous, status, THREAD, 1))
            boundary_turn = 3 if second_turn is not None and number >= second_turn else 2
            bindings[transition] = ResponseBoundary(THREAD, turn(boundary_turn), "fixture-project", "task.edit", "call-" + str(index),
                "fixture", "r" + str(number), number * 10, "tg_event_" + f"{index:016x}", task, generation, status, "explicit")
        numerical = [response("r" + str(number), number=3 if second_turn is not None and number >= second_turn else 2)
                     for number in range(count) if number not in missing]
        positions = [(SOURCE, 1, ResponsePosition(THREAD, item.turn_id, item.provider, item.response_id,
                                                int(item.response_id[1:]) * 10)) for item in numerical]
        starts = (TurnObservation(THREAD, turn(2), 2000),)
        if second_turn is not None:
            starts += (TurnObservation(THREAD, turn(3), 3000),)
        confirmations = tuple(ResponseBoundary(THREAD, turn(3 if second_turn is not None and number >= second_turn else 2),
            "fixture-project", "task.context", "check-" + str(number),
            "fixture", "r" + str(number), number * 10, method="explicit") for number in checks)
        for index in unbound:
            bindings.pop("t" + str(index))
        return project_responses(tuple(transitions), bindings, confirmations, positions, starts, tuple(numerical),
                                 additional=additional, pending=pending)

    def keys(self, result, execution, assignment=None):
        return {key[1] for part in result["components"] if execution in part["executions"]
                and (assignment is None or part["assignment"] == assignment) for key in part["response_keys"]}

    def test_same_turn_completion_tail_and_next_preparation_are_separate(self):
        result = self.build(((A, X, "ready", "in_progress", 1), (A, X, "in_progress", "done", 2),
                             (B, Y, "ready", "in_progress", 6), (B, Y, "in_progress", "done", 7)), checks=(0, 4, 5))
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2", "r3"})
        self.assertEqual(self.keys(result, Y), {"r4", "r5", "r6", "r7"})

    def test_active_task_confirmation_is_not_next_task_preparation(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "done", 4),
                             (B, Y, "ready", "in_progress", 6)), checks=(2, 3))
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2", "r3", "r4", "r5"})
        self.assertEqual(self.keys(result, Y), {"r6", "r7"})

    def test_confirmation_without_a_start_keeps_completion_turn_tail(self):
        result = self.build(((A, X, "ready", "in_progress", 1), (A, X, "in_progress", "done", 2)), checks=(5,))
        self.assertEqual(self.keys(result, X), {"r1", "r2", "r3", "r4", "r5", "r6", "r7"})
        self.assertIn(("fixture", "r0"), result["unassigned_response_keys"])

    def test_previous_turn_preparation_is_not_adopted(self):
        result = self.build(((A, X, "ready", "in_progress", 1), (A, X, "in_progress", "done", 2)),
                            checks=(0,), second_turn=1)
        self.assertEqual(self.keys(result, X), {"r1", "r2", "r3", "r4", "r5", "r6", "r7"})
        self.assertIn(("fixture", "r0"), result["unassigned_response_keys"])

    def test_shared_start_response_does_not_merge_exclusive_neighbours(self):
        result = self.build(((A, X, "ready", "in_progress", 1), (A, X, "in_progress", "done", 1),
                             (B, Y, "ready", "in_progress", 1), (B, Y, "in_progress", "done", 3)), checks=(0,))
        self.assertEqual(self.keys(result, X, "shared"), {"r1"})
        self.assertEqual(self.keys(result, X, "exclusive"), {"r0"})
        self.assertEqual(self.keys(result, Y, "exclusive"), {"r2", "r3", "r4", "r5", "r6", "r7"})

    def test_next_start_wins_over_previous_completion_in_one_response(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "done", 3),
                             (B, Y, "ready", "in_progress", 3)))
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2"})
        self.assertEqual(self.keys(result, Y), {"r3", "r4", "r5", "r6", "r7"})

    def test_reopened_execution_has_its_own_response_set(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "done", 1),
                             (B, Y, "ready", "in_progress", 2), (B, Y, "in_progress", "done", 3),
                             (A, Z, "ready", "in_progress", 4), (A, Z, "in_progress", "done", 5)))
        self.assertEqual(self.keys(result, X), {"r0", "r1"})
        self.assertEqual(self.keys(result, Y), {"r2", "r3"})
        self.assertEqual(self.keys(result, Z), {"r4", "r5", "r6", "r7"})

    def test_missing_numerical_response_is_not_zero_and_late_values_extend_same_set(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "done", 2)), missing=(3,))
        self.assertNotIn("r3", self.keys(result, X))
        later = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "done", 2)))
        self.assertIn("r3", self.keys(later, X))

    def test_confirmation_origin_includes_intermediate_responses(self):
        result = self.build(((A, X, "ready", "in_progress", 5),), checks=(1,))
        self.assertEqual(self.keys(result, X), {"r1", "r2", "r3", "r4", "r5", "r6", "r7"})

    def test_actual_parent_receipt_cuts_off_at_next_preparation(self):
        receipt = Interval(A, X, None, THREAD, turn(3), turn(3), True, ())
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "done", 1),
                             (B, Y, "ready", "in_progress", 6)), second_turn=2, checks=(4,),
                            additional=(receipt,), unbound=(1,))
        self.assertEqual(self.keys(result, X, "exclusive"), {"r0", "r1", "r2", "r3"})
        self.assertEqual(self.keys(result, Y, "exclusive"), {"r4", "r5", "r6", "r7"})

    def test_unconfirmed_operation_fences_only_until_next_known_boundary(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (B, Y, "ready", "in_progress", 5)),
                            pending=((SOURCE, 1, THREAD, turn(2), "fixture", "r2", 20),))
        self.assertEqual(self.keys(result, X, "exclusive"), {"r0", "r1"})
        self.assertEqual(self.keys(result, X, "partial"), {"r2", "r3", "r4"})
        self.assertEqual(self.keys(result, Y, "exclusive"), {"r5", "r6", "r7"})

    def test_ready_exit_without_execution_id_stops_the_old_execution(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, None, "in_progress", "ready", 2)))
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2"})
        self.assertEqual(set(result["unassigned_response_keys"]), {("fixture", "r" + str(i)) for i in range(3, 8)})

    def test_response_order_disagreeing_with_committed_operation_order_is_unknown(self):
        result = self.build(((A, X, "ready", "in_progress", 5), (A, X, "in_progress", "done", 2),
                             (B, Y, "ready", "in_progress", 6)))
        self.assertFalse(self.keys(result, X, "exclusive"))
        self.assertEqual(self.keys(result, Y, "exclusive"), {"r6", "r7"})
        self.assertTrue(any("response_order_unknown" in part["diagnostics"]
                            for part in result["components"] if X in part["executions"]))


if __name__ == "__main__":
    unittest.main()
