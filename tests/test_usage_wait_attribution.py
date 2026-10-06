"""Whole decision turns, exact sender binding and deterministic repeat handling."""

from dataclasses import replace
import unittest

from tests.test_usage_attribution import THREAD, OTHER, interval, turn, response
from tests.test_usage_turn_adapter import TASK, tool_record
from task_governance_tool.usage_attribution import project
from task_governance_tool.usage_turn_adapter import TurnObservation, project_record
from task_governance_tool.usage_wait_attribution import WaitObservation, project_wait, wait_intervals
from task_governance_tool.usage_values import UsageError


EXECUTION = "tg_execution_0123456789abcdef"
MANIFEST = "tg_artifact_manifest_0123456789abcdef"


def marker(*, thread=THREAD, project_id="fixture-project", task=TASK, execution=EXECUTION,
           manifest=MANIFEST, generation=1, contract=1):
    return {"ok": True, "status": "review_wait_ended", "review_wait": {
        "version": 1, "session_id": thread, "project_id": project_id, "task_id": task,
        "execution_id": execution, "contract_revision": contract,
        "review_target": {"kind": "diff_fingerprint", "value": "sha256:" + "a" * 64,
                          "base_revision": "", "generation": generation},
        "artifact_manifest_id": manifest}}


def fact(number=4, **values):
    value = marker(**values)
    return project_wait(value, value["review_wait"]["session_id"], turn(number), "fixture-project")[0]


class UsageWaitAttributionTests(unittest.TestCase):
    def setUp(self):
        self.owners = (interval(TASK, EXECUTION, 2, 3),)
        self.turns = tuple(TurnObservation(THREAD, turn(n), n) for n in range(1, 9))

    def projected(self, facts, *, valid=None, turns=None, conflicts=frozenset(), extra=()):
        waits = wait_intervals(self.owners, self.turns if turns is None else turns,
                              facts, frozenset(facts if valid is None else valid), conflicting_turns=conflicts)
        return project(self.owners + waits, self.turns if turns is None else turns,
                       tuple(response(n) for n in range(1, 9)) + extra,
                       conflicting_turns=conflicts)

    def test_same_turn_is_union_and_separate_turn_is_whole(self):
        self.assertEqual(self.projected((fact(3),))["tasks"][0]["response_keys"],
                         [("openai", "r2"), ("openai", "r3")])
        result = self.projected((fact(5),), extra=(response(5, identity="before"), response(5, identity="after")))
        self.assertEqual(result["tasks"][0]["models"][0]["response_count"], 5)
        self.assertIn(("openai", "r6"), result["unassigned_response_keys"])

    def test_replay_and_later_renotification_select_earliest_host_turn(self):
        expected = self.projected((fact(4),))
        self.assertEqual(self.projected((fact(6), fact(4), fact(4))), expected)
        initial = self.projected((fact(6),))
        self.assertIn(("openai", "r6"), initial["tasks"][0]["response_keys"])
        self.assertNotIn(("openai", "r6"), expected["tasks"][0]["response_keys"])

    def test_multiple_senders_unknown_order_or_lost_anchor_add_no_turn(self):
        cases = ((fact(4), fact(5, thread=OTHER)), (fact(4), fact(99)))
        for facts in cases:
            result = self.projected(facts)
            self.assertEqual(result["tasks"][0]["models"][0]["response_count"], 2)
            self.assertIn("boundary_unknown", result["components"][0]["diagnostics"])
        result = self.projected((fact(4),), valid=())
        self.assertEqual(result["tasks"][0]["models"][0]["response_count"], 2)
        result = self.projected((fact(4),), conflicts={(THREAD, turn(4))})
        self.assertEqual(result["tasks"][0]["models"][0]["response_count"], 2)
        ambiguous = self.turns + (TurnObservation(THREAD, turn(9), 4),)
        self.assertEqual(self.projected((fact(4),), turns=ambiguous)["tasks"][0]["models"][0]["response_count"], 2)

    def test_structural_envelope_and_sender_exclude_parent_echo_and_plain_prose(self):
        expected = (fact(4),)
        self.assertEqual(project_record(tool_record(marker(), 4), THREAD, project_id="fixture-project"), expected)
        self.assertEqual(project_record(tool_record(marker(), 4), OTHER, project_id="fixture-project"), ())
        self.assertEqual(project_record(tool_record(marker(), 4), THREAD, project_id="other-project"), ())
        for kind in ("function_call", "message"):
            record = tool_record(marker(), 4)
            record["payload"]["type"] = kind
            self.assertEqual(project_record(record, THREAD), ())
        record = tool_record(marker(), 4)
        record["payload"].pop("internal_chat_message_metadata_passthrough")
        self.assertEqual(project_record(record, THREAD), ())

    def test_closed_metadata_rejects_body_boolean_generation_and_extra_fields(self):
        for mutate in (lambda item: item.update(prompt="private"),
                       lambda item: item["review_wait"].update(timer_payload="private"),
                       lambda item: item["review_wait"].update(version=True),
                       lambda item: item["review_wait"]["review_target"].update(generation=True)):
            value = marker()
            mutate(value)
            with self.assertRaises(UsageError):
                project_wait(value, THREAD, turn(4), "fixture-project")

    def test_old_execution_cannot_rebind_to_reopened_execution(self):
        self.owners = (interval(TASK, "tg_execution_1123456789abcdef", 7, 8, preceding="old-done"),)
        result = self.projected((fact(4),))
        self.assertEqual(result["tasks"][0]["response_keys"], [("openai", "r7"), ("openai", "r8")])
