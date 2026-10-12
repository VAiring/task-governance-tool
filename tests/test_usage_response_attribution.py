"""Response boundaries, whole-response sharing and per-execution unions."""

from pathlib import Path
from dataclasses import replace
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

    def test_pause_and_block_include_stop_response_but_not_stopped_tail(self):
        for status in ("paused", "blocked"):
            with self.subTest(status=status):
                result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", status, 2)), checks=(5,))
                self.assertEqual(self.keys(result, X, "exclusive"), {"r0", "r1", "r2"})
                self.assertEqual(set(result["unassigned_response_keys"]), {("fixture", "r" + str(i)) for i in range(3, 8)})
                self.assertFalse(result["unknown_response_keys"])

    def test_resume_adopts_only_new_eligible_same_turn_preparation(self):
        for status in ("paused", "blocked"):
            with self.subTest(status=status):
                result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", status, 2),
                                     (A, X, status, "in_progress", 6)), checks=(1, 4, 5))
                self.assertEqual(self.keys(result, X, "exclusive"), {"r0", "r1", "r2", "r4", "r5", "r6", "r7"})
                self.assertEqual(result["unassigned_response_keys"], [("fixture", "r3")])
                self.assertEqual({tuple(part["executions"]) for part in result["components"]}, {(X,)})

    def test_next_task_check_while_active_does_not_cross_stop(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "paused", 2),
                             (B, Y, "ready", "in_progress", 5)), checks=(1,))
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2"})
        self.assertEqual(self.keys(result, Y), {"r5", "r6", "r7"})
        self.assertEqual(set(result["unassigned_response_keys"]), {("fixture", "r3"), ("fixture", "r4")})

    def test_new_check_after_stop_can_prepare_a_different_candidate(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "blocked", 2),
                             (B, Y, "ready", "in_progress", 6)), checks=(1, 4, 5))
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2"})
        self.assertEqual(self.keys(result, Y), {"r4", "r5", "r6", "r7"})
        self.assertEqual(result["unassigned_response_keys"], [("fixture", "r3")])

    def test_stop_and_single_next_resume_in_one_response_prefers_next_execution(self):
        result = self.build(((B, Y, "ready", "in_progress", 0), (B, Y, "in_progress", "blocked", 1),
                             (A, X, "ready", "in_progress", 2), (A, X, "in_progress", "paused", 4),
                             (B, Y, "blocked", "in_progress", 4)))
        self.assertEqual(self.keys(result, X), {"r2", "r3"})
        self.assertEqual(self.keys(result, Y, "exclusive"), {"r0", "r1", "r4", "r5", "r6", "r7"})
        self.assertFalse(self.keys(result, Y, "shared"))

    def test_same_execution_stop_resume_uses_final_core_state_in_same_response(self):
        for final_stop in (False, True):
            with self.subTest(final_stop=final_stop):
                actions = [(A, X, "ready", "in_progress", 0), (A, X, "in_progress", "paused", 3),
                           (A, X, "paused", "in_progress", 3)]
                if final_stop:
                    actions.append((A, X, "in_progress", "blocked", 3))
                result = self.build(actions)
                self.assertEqual(self.keys(result, X, "exclusive"), {"r" + str(i) for i in range(4 if final_stop else 8)})
                self.assertFalse(self.keys(result, X, "shared"))

    def test_multiple_resumes_share_only_their_one_response(self):
        result = self.build(((B, Y, "ready", "in_progress", 0), (B, Y, "in_progress", "blocked", 1),
                             (A, X, "ready", "in_progress", 2), (A, X, "in_progress", "paused", 3),
                             (B, Y, "blocked", "in_progress", 4), (B, Y, "in_progress", "paused", 4),
                             (A, X, "paused", "in_progress", 4), (A, X, "in_progress", "done", 6)))
        self.assertEqual(self.keys(result, X, "shared"), {"r4"})
        self.assertEqual(self.keys(result, Y, "shared"), {"r4"})
        self.assertEqual(self.keys(result, X, "exclusive"), {"r2", "r3", "r5", "r6", "r7"})
        self.assertEqual(self.keys(result, Y, "exclusive"), {"r0", "r1"})

    def test_resume_never_adopts_previous_turn_check_or_stopped_gap(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "paused", 2),
                             (A, X, "paused", "in_progress", 6)), checks=(3,), second_turn=4)
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2", "r6", "r7"})
        self.assertEqual(set(result["unassigned_response_keys"]), {("fixture", "r" + str(i)) for i in (3, 4, 5)})

    def test_unbound_stop_and_resume_never_prove_unassigned_usage(self):
        for missing in (1, 2):
            with self.subTest(missing=missing):
                result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "paused", 2),
                                     (A, X, "paused", "in_progress", 5), (A, X, "in_progress", "done", 6)), unbound=(missing,))
                self.assertTrue(result["unknown_response_keys"])
                self.assertFalse(set(result["unknown_response_keys"]) & set(result["unassigned_response_keys"]))
                self.assertIn("r0", self.keys(result, X, "exclusive"))
                self.assertTrue({"r6", "r7"} <= self.keys(result, X, "exclusive"))

    def test_independent_reviewer_and_receipt_bindings_survive_owner_stop(self):
        reviewer = Interval(A, X, None, THREAD, turn(3), turn(3), True, ())
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "paused", 2)),
                            second_turn=4, additional=(reviewer,))
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2", "r4", "r5", "r6", "r7"})
        self.assertEqual(result["unassigned_response_keys"], [("fixture", "r3")])

    def test_unrelated_blocked_exit_preserves_completion_tail_and_preparation(self):
        for status in ("ready", "cancelled"):
            for preparation in (False, True):
                with self.subTest(status=status, preparation=preparation):
                    actions = [(B, Y, "ready", "in_progress", 0), (B, Y, "in_progress", "blocked", 1),
                               (A, X, "ready", "in_progress", 2), (A, X, "in_progress", "done", 3),
                               (B, None, "blocked", status, 5)]
                    if preparation:
                        actions.append((C, Z, "ready", "in_progress", 7))
                    result = self.build(actions, checks=(4,) if preparation else ())
                    self.assertEqual(self.keys(result, X), {"r2", "r3"} if preparation else {"r" + str(i) for i in range(2, 8)})
                    if preparation:
                        self.assertEqual(self.keys(result, Z, "exclusive"), {"r4", "r5", "r6", "r7"})
                    self.assertFalse(result["unknown_response_keys"] or result["unassigned_response_keys"])

    def recovery(self, *, clock=2400, return_actor=False, other_active=False, reviewer=False):
        other = "11234567-89ab-7cde-8fab-0123456789ab"
        third = "31234567-89ab-7cde-8fab-0123456789ab"
        actions = [Transition("start", A, X, 1, "ready", "in_progress", THREAD, 1)]
        metadata = [("start", THREAD, 2, "original", 10)]
        if other_active:
            actions.append(Transition("other", B, Y, 1, "ready", "in_progress", other, 1))
            metadata.append(("other", other, 4, "other-start", 5))
        actions += [Transition("stop", A, X, 2, "in_progress", "paused", other, 1),
                    Transition("resume", A, X, 3, "paused", "in_progress", THREAD if return_actor else other, 1)]
        metadata += [("stop", other, 4, "recovery-stop", 10),
                     ("resume", THREAD if return_actor else other, 3 if return_actor else 4, "resume", 30)]
        bindings = {}
        for identity, actor, number, key, order in metadata:
            change = next(item for item in actions if item.transition_id == identity)
            bindings[identity] = ResponseBoundary(actor, turn(number), "fixture-project", "task.edit", identity,
                "fixture", key, order, "tg_event_" + f"{len(bindings):016x}", change.task_id, change.generation,
                change.current_status, "explicit")
        records = [(THREAD, 2, "original", 10), (THREAD, 3, "former-late", 10),
                   (other, 4, "recovery-stop", 10), (other, 4, "recovery-gap", 20),
                   (THREAD if return_actor else other, 3 if return_actor else 4, "resume", 30),
                   (THREAD if return_actor else other, 3 if return_actor else 4, "after", 40)]
        if other_active:
            # Actor 2 cannot resume A while its own B holds the slot. Retain
            # only the supported recovery operation and subsequent B work.
            actions.pop()
            bindings.pop("resume")
            records = records[:4] + [(other, 4, "other-start", 5), (other, 4, "other-late", 40)]
        values = tuple(replace(response(key, number=number), thread_id=actor) for actor, number, key, _ in records)
        positions = [(SOURCE if actor == THREAD else "b" * 64, 1,
                      ResponsePosition(actor, turn(number), "fixture", key, order)) for actor, number, key, order in records]
        starts = (TurnObservation(THREAD, turn(2), 2000), TurnObservation(THREAD, turn(3), 3000),
                  TurnObservation(other, turn(4), clock))
        additional = ()
        if reviewer:
            actions.append(Transition("reviewed", C, Z, 1, "ready", "in_progress", third, 1))
            additional = (Interval(C, Z, None, THREAD, turn(3), turn(3), True, ()),)
        return project_responses(tuple(actions), bindings, (), positions, starts, values, additional=additional)

    def test_cross_session_recovery_counts_stop_and_fences_old_actor_without_clocks(self):
        for clock in (1000, 4000):
            for return_actor in (False, True):
                with self.subTest(clock=clock, return_actor=return_actor):
                    result = self.recovery(clock=clock, return_actor=return_actor)
                    self.assertEqual(self.keys(result, X, "exclusive"), {"original", "recovery-stop", "resume", "after"})
                    self.assertEqual(self.keys(result, X, "partial"), {"former-late"})
                    self.assertEqual(result["unknown_response_keys"], [("fixture", "former-late")])
                    self.assertEqual(result["unassigned_response_keys"], [("fixture", "recovery-gap")])

    def test_cross_session_stop_does_not_close_the_recovering_actors_other_task(self):
        result = self.recovery(other_active=True)
        self.assertEqual(self.keys(result, Y, "exclusive"), {"other-start", "recovery-gap", "other-late"})
        self.assertEqual(self.keys(result, X, "exclusive"), {"original"})
        self.assertEqual(self.keys(result, X, "partial"), {"former-late", "recovery-stop"})
        self.assertEqual(self.keys(result, Y, "partial"), {"recovery-stop"})

    def test_independent_reviewer_membership_remains_observed_after_cross_session_stop(self):
        result = self.recovery(return_actor=True, reviewer=True)
        self.assertEqual(self.keys(result, Z), {"former-late", "resume", "after"})
        self.assertTrue(any("former-late" in {key[1] for key in part["response_keys"]} and Z in part["executions"]
                            and part["models"][0]["response_count"] > 0 for part in result["components"]))


if __name__ == "__main__":
    unittest.main()
