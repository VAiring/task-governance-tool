"""Response boundaries, whole-response sharing and per-execution unions."""

from pathlib import Path
from dataclasses import replace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.usage_attribution import Transition, Interval, owner_segments
from task_governance_tool.usage_response_adapter import ResponsePosition, ResponseBoundary
from task_governance_tool.usage_response_attribution import project_responses
from task_governance_tool.usage_turn_adapter import TurnObservation
from tests.test_usage_response_adapter import THREAD, turn, response


A, B, C = ("tg_task_" + digit * 16 for digit in "abc")
X, Y, Z = ("tg_execution_" + digit * 16 for digit in "abc")
SOURCE = "a" * 64


class ResponsePartitionTests(unittest.TestCase):
    def build(self, actions, *, count=8, checks=(), additional=(), second_turn=None, missing=(), unbound=(), pending=(),
              fallback_mode=None):
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
        segments = owner_segments(transitions, {identity: (item.thread_id, item.turn_id) for identity, item in bindings.items()})
        return project_responses(tuple(transitions), bindings, confirmations, positions, starts, tuple(numerical),
                                 additional=additional, pending=pending,
                                 fallback=tuple(item[0] for item in segments) if fallback_mode else (),
                                 fallback_origins=tuple(item[1:] for item in segments) if fallback_mode == "segments" else ())

    def test_same_turn_multiple_origins_never_adopt_unknown_responses_before_all_starts(self):
        for mode in ("legacy", "segments"):
            with self.subTest(mode=mode):
                value = self.build(((A, X, "ready", "in_progress", 2), (A, X, "in_progress", "paused", 4),
                                    (A, X, "paused", "in_progress", 6)), fallback_mode=mode,
                                   pending=((SOURCE, 1, THREAD, turn(2), "fixture", "r0", 0),))
                self.assertFalse({"r0", "r1"} & self.keys(value, X))
                self.assertEqual(value["unknown_response_keys"], [("fixture", "r0"), ("fixture", "r1")])
                self.assertEqual(self.keys(value, X, "exclusive"), {"r2", "r3", "r4", "r6", "r7"})

    def test_segment_origins_keep_unknown_stopped_gap_outside_resumed_execution(self):
        value = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "paused", 2),
                            (A, X, "paused", "in_progress", 6)), fallback_mode="segments",
                           pending=((SOURCE, 1, THREAD, turn(2), "fixture", "r4", 40),))
        self.assertFalse({"r4", "r5"} & self.keys(value, X))
        self.assertEqual(value["unknown_response_keys"], [("fixture", "r4"), ("fixture", "r5")])
        self.assertEqual(value["unassigned_response_keys"], [("fixture", "r3")])
        self.assertEqual(self.keys(value, X, "exclusive"), {"r0", "r1", "r2", "r6", "r7"})

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

    def test_unrelated_organization_does_not_resolve_pending_operation(self):
        for recovery in (None, "start", "stop", "resume"):
            with self.subTest(recovery=recovery):
                actions = [(B, Y, "ready", "in_progress", 0),
                           (B, Y, "in_progress", "blocked", 1),
                           (A, X, "ready", "in_progress", 2),
                           (B, None, "blocked", "ready", 5)]
                if recovery == "start":
                    actions.append((C, Z, "ready", "in_progress", 7))
                elif recovery == "stop":
                    actions.append((A, X, "in_progress", "paused", 7))
                elif recovery == "resume":
                    actions += [(A, X, "in_progress", "paused", 3),
                                (A, X, "paused", "in_progress", 7)]
                    actions.sort(key=lambda action: action[4])
                first = 4 if recovery == "resume" else 3
                value = self.build(actions, count=9, pending=((SOURCE, 1, THREAD, turn(2),
                                                              "fixture", "r" + str(first), first * 10),))
                end = 9 if recovery is None else 7
                uncertain = {"r" + str(i) for i in range(first, end)}
                self.assertEqual({key[1] for key in value["unknown_response_keys"]}, uncertain)
                self.assertEqual(self.keys(value, Y, "exclusive"), {"r0", "r1"})
                self.assertFalse(uncertain & self.keys(value, X, "exclusive"))
                self.assertFalse(uncertain & {key[1] for key in value["unassigned_response_keys"]})
                if recovery == "start":
                    self.assertEqual(self.keys(value, Z, "exclusive"), {"r7", "r8"})
                elif recovery == "stop":
                    self.assertEqual(value["unassigned_response_keys"], [("fixture", "r8")])
                elif recovery == "resume":
                    self.assertEqual(self.keys(value, X, "exclusive"), {"r2", "r3", "r7", "r8"})

    def test_ready_exit_without_execution_id_stops_the_old_execution(self):
        result = self.build(((A, X, "ready", "in_progress", 0), (A, None, "in_progress", "ready", 2)))
        self.assertEqual(self.keys(result, X), {"r0", "r1", "r2"})
        self.assertEqual(set(result["unassigned_response_keys"]), {("fixture", "r" + str(i)) for i in range(3, 8)})

    def test_unbound_release_matches_previous_execution_and_recovers_only_at_state_boundary(self):
        for status in ("ready", "cancelled", "paused", "blocked"):
            for recovery in (False, True):
                with self.subTest(status=status, recovery=recovery):
                    actions = [(A, X, "ready", "in_progress", 0),
                               (A, None if status in {"ready", "cancelled"} else X, "in_progress", status, 2)]
                    if recovery:
                        actions.append((B, Y, "ready", "in_progress", 6))
                    value = self.build(actions, unbound=(1,))
                    end = 6 if recovery else 8
                    uncertain = {"r" + str(i) for i in range(1, end)}
                    self.assertEqual(self.keys(value, X, "exclusive"), {"r0"})
                    self.assertEqual(self.keys(value, X, "partial"), uncertain)
                    self.assertEqual({key[1] for key in value["unknown_response_keys"]}, uncertain)
                    self.assertFalse(value["unassigned_response_keys"])
                    if recovery:
                        self.assertEqual(self.keys(value, Y, "exclusive"), {"r6", "r7"})
                    for part in value["components"]:
                        self.assertEqual(sum(row["total_tokens"] for row in part["models"]),
                                         12 * len(part["response_keys"]))

    def test_unrelated_bound_organization_release_does_not_end_uncertain_stop(self):
        value = self.build(((B, Y, "ready", "in_progress", 0), (B, Y, "in_progress", "blocked", 1),
                            (A, X, "ready", "in_progress", 2), (A, X, "in_progress", "paused", 3),
                            (B, None, "blocked", "ready", 5), (C, Z, "ready", "in_progress", 7)), unbound=(3,))
        self.assertEqual(self.keys(value, Y, "exclusive"), {"r0", "r1"})
        self.assertEqual(self.keys(value, X, "exclusive"), {"r2"})
        self.assertEqual(self.keys(value, X, "partial"), {"r3", "r4", "r5", "r6"})
        self.assertEqual(self.keys(value, Z, "exclusive"), {"r7"})

    def test_unbound_unrelated_release_keeps_confirmed_unassigned_later_turn(self):
        value = self.build(((B, Y, "ready", "in_progress", 0), (B, Y, "in_progress", "blocked", 1),
                            (A, X, "ready", "in_progress", 2), (A, X, "in_progress", "done", 3),
                            (B, None, "blocked", "ready", 5)), unbound=(4,), second_turn=4)
        self.assertEqual(self.keys(value, X, "exclusive"), {"r2", "r3"})
        self.assertFalse(value["unknown_response_keys"])
        self.assertEqual(value["unassigned_response_keys"], [("fixture", "r" + str(i)) for i in range(4, 8)])

    def test_stop_in_unordered_turn_fences_later_turn_until_resume_or_next_start(self):
        for conflict in ("positions", "persisted", "observations"):
            for recovery in ("none", "resume", "next"):
                with self.subTest(conflict=conflict, recovery=recovery):
                    changes = [Transition("start", A, X, 1, "ready", "in_progress", THREAD, 1),
                               Transition("stop", A, X, 2, "in_progress", "paused", THREAD, 1)]
                    if recovery != "none":
                        changes.append(Transition("recover", A if recovery == "resume" else B,
                            X if recovery == "resume" else Y, 3, "paused" if recovery == "resume" else "ready",
                            "in_progress", THREAD, 1))
                    numbers = ((0, 2), (2, 3), (6, 4))
                    bindings = {change.transition_id: ResponseBoundary(THREAD, turn(t), "fixture-project", "task.edit",
                        change.transition_id, "fixture", "r" + str(n), n * 10, "tg_event_" + f"{n:016x}",
                        change.task_id, change.generation, change.current_status, "explicit")
                        for change, (n, t) in zip(changes, numbers)}
                    values = tuple(response("r" + str(n), number=2 if n < 2 else 3 if n < 4 else 4) for n in range(8))
                    positions = [(SOURCE, 1, ResponsePosition(THREAD, row.turn_id, row.provider, row.response_id,
                        int(row.response_id[1:]) * 10)) for row in values]
                    starts = tuple(TurnObservation(THREAD, turn(t), t * 1000) for t in (2, 3, 4))
                    conflicts = frozenset()
                    if conflict == "persisted":
                        conflicts = frozenset({(THREAD, turn(3))})
                    elif conflict == "observations":
                        starts += (TurnObservation(THREAD, turn(3), 3100),)
                    else:
                        positions.append(("b" * 64, 1, replace(positions[3][2], record_order=10)))
                        positions.append(("b" * 64, 1, replace(positions[2][2], record_order=20)))
                    value = project_responses(changes, bindings, (), positions, starts, values, conflicting_turns=conflicts)
                    end = 8 if recovery == "none" else 6
                    self.assertFalse({"r" + str(n) for n in range(1, end)} & self.keys(value, X, "exclusive"))
                    self.assertTrue({("fixture", "r" + str(n)) for n in range(1, end)} <= set(value["unknown_response_keys"]))
                    self.assertFalse(value["unassigned_response_keys"])
                    self.assertTrue({"r4", "r5"} <= self.keys(value, X, "partial"))
                    if recovery != "none":
                        self.assertTrue({"r6", "r7"} <= self.keys(value, X if recovery == "resume" else Y, "exclusive"))

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

    def test_identity_only_additional_read_is_partial_without_tainting_other_turns(self):
        read = Interval(A, X, None, THREAD, turn(3), None, True,
                        ("review_boundary_unknown",), identity_only=True)
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "paused", 2)),
                            second_turn=4, additional=(read,))
        self.assertEqual(self.keys(result, X, "exclusive"), {"r0", "r1", "r2"})
        self.assertEqual(self.keys(result, X, "partial"), {"r4", "r5", "r6", "r7"})
        self.assertEqual(set(result["unknown_response_keys"]), {("fixture", "r" + str(i)) for i in range(4, 8)})
        self.assertEqual(result["unassigned_response_keys"], [("fixture", "r3")])
        self.assertTrue(all("review_boundary_unknown" in item["diagnostics"] for item in result["components"]))
        self.assertEqual(sum(model["response_count"] for part in result["components"] for model in part["models"]), 7)

    def test_identity_only_additional_without_read_does_not_mark_endpoint_partial(self):
        missing = Interval(A, X, None, THREAD, None, turn(3), True,
                           ("review_boundary_unknown",), identity_only=True)
        result = self.build(((A, X, "ready", "in_progress", 0), (A, X, "in_progress", "paused", 2)),
                            second_turn=4, additional=(missing,))
        self.assertEqual(self.keys(result, X, "exclusive"), {"r0", "r1", "r2"})
        self.assertFalse(result["unknown_response_keys"])
        self.assertEqual(set(result["unassigned_response_keys"]), {("fixture", "r" + str(i)) for i in range(3, 8)})

    def test_unrelated_blocked_exit_preserves_completion_tail_and_preparation(self):
        for status in ("ready", "cancelled"):
            for preparation in (False, True):
                with self.subTest(status=status, preparation=preparation):
                    actions = [(B, Y, "ready", "in_progress", 0), (B, Y, "in_progress", "blocked", 1),
                               (A, X, "ready", "in_progress", 2), (A, X, "in_progress", "done", 3),
                               (B, None, "blocked", status, 5)]
                    if preparation:
                        actions.append((C, Z, "ready", "in_progress", 7))
                    for unbound in ((), (4,)):
                        result = self.build(actions, checks=(4,) if preparation else (), unbound=unbound)
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
