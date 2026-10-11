"""Only structured turn identities and known successful CLI acknowledgements."""

from dataclasses import replace
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"))
from task_governance_tool.usage_turn_adapter import OperationObservation, TurnObservation, project_record
from task_governance_tool.cli import write_task_projection, registration_context_projection
from tests.test_usage_attribution import THREAD, turn


TASK = "tg_task_0123456789abcdef"
EVENT = "tg_event_0123456789abcdef"


def acknowledgement(status="in_progress", generation=1, event_id=EVENT):
    return {"command": "task.edit", "ok": True, "project_id": "fixture-project",
            "data": {"task": {"task_id": TASK, "project_id": "fixture-project", "status": status,
                               "ownership": {"generation": generation}},
                     "event": {"task_event_id": event_id, "project_id": "fixture-project", "task_id": TASK},
                     "changed_fields": ["status"]}}


def tool_record(ack, number=2, *, wrapped=True):
    output = json.dumps(ack)
    if wrapped:
        output = [{"type": "input_text", "text": json.dumps({"exit_code": 0, "output": output + "\n"})}]
    return {"type": "response_item", "payload": {"type": "function_call_output",
            "internal_chat_message_metadata_passthrough": {"turn_id": turn(number)}, "output": output}}


class UsageTurnAdapterTests(unittest.TestCase):
    def registration(self, *, batch=False):
        ack = acknowledgement()
        ack["command"] = "task.add"
        entry = ack["data"]
        selected = {"task": copy.deepcopy(entry["task"]), "events": [copy.deepcopy(entry["event"])]}
        projected = registration_context_projection(entry, selected)
        context = {"status": "ready", "context": {"selected": selected}, "errors": []}
        if batch:
            ready = acknowledgement("ready")["data"]
            ack["data"] = {"tasks": [{"input_index": 0, **ready}, {"input_index": 1, **projected}],
                           "context_preparation": context}
        else:
            ack["data"] = {**projected, "context_preparation": context}
        return ack, entry

    def test_registration_references_preserve_single_and_batch_operation_metadata(self):
        for batch in (False, True):
            for referenced_fields in ((), ("task",), ("event",), ("task", "event")):
                with self.subTest(batch=batch, referenced_fields=referenced_fields):
                    ack, original = self.registration(batch=batch)
                    entry = ack["data"]["tasks"][1] if batch else ack["data"]
                    for field in ("task", "event"):
                        if field not in referenced_fields:
                            entry[field] = original[field]
                    expected = OperationObservation(THREAD, turn(2), "fixture-project", TASK, EVENT, 1, "in_progress")
                    self.assertEqual(project_record(tool_record(ack), THREAD), (expected,))

    def test_registration_rejects_unavailable_malformed_and_mismatched_references(self):
        for batch in (False, True):
            changes = (
                ("task", "task_ref", "/data/task"),
                ("task", "task_id", "tg_task_ffffffffffffffff"),
                ("task", "ownership", {"generation": 1}),
                ("event", "task_event_id", "tg_event_ffffffffffffffff"),
                *(("event", "event_ref", "/data/context_preparation/context/selected/events/" + index)
                  for index in ("-1", "1", "00", "true", "0/extra", "９", "9" * 5000)),
                ("event", "event_ref", None),
            )
            for field, key, value in changes:
                with self.subTest(batch=batch, field=field, key=key, value_type=type(value).__name__):
                    ack, _ = self.registration(batch=batch)
                    entry = ack["data"]["tasks"][1] if batch else ack["data"]
                    entry[field][key] = value
                    self.assertEqual(project_record(tool_record(ack), THREAD), ())
            for preparation in (None, {"status": "failed", "context": None},
                                {"status": "ready", "context": {"selected": None}}):
                ack, _ = self.registration(batch=batch)
                ack["data"]["context_preparation"] = preparation
                self.assertEqual(project_record(tool_record(ack), THREAD), ())
            for field, key in (("task", "project_id"), ("event", "project_id"), ("event", "task_id")):
                ack, _ = self.registration(batch=batch)
                selected = ack["data"]["context_preparation"]["context"]["selected"]
                body = selected["task"] if field == "task" else selected["events"][0]
                body[key] = "another-identity"
                self.assertEqual(project_record(tool_record(ack), THREAD), ())

    def test_compact_write_acknowledgements_preserve_actual_turn_and_ownership_binding(self):
        for command, status in (("task.edit", "in_progress"), ("task.complete", "done")):
            ack = acknowledgement(status)
            ack["command"] = command
            ack["data"]["task"].update(kind="optional", lane="", lane_order=None,
                                       priority="normal", tags="", created_at="2026-10-08T00:00:00Z")
            expected = project_record(tool_record(ack), THREAD)
            self.assertEqual(len(expected), 1)
            ack["data"]["task"] = write_task_projection(ack["data"]["task"], ack["data"]["changed_fields"])
            self.assertEqual(project_record(tool_record(ack), THREAD), expected)

    def test_batch_registration_selects_only_actual_initial_in_progress(self):
        active, ready = acknowledgement(), acknowledgement("ready")
        batch = {"command": "task.add", "ok": True, "project_id": "fixture-project",
                 "data": {"tasks": [active["data"], ready["data"]]}}
        result = project_record(tool_record(batch), THREAD)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, "in_progress")

    def test_actual_content_block_and_exec_container_shapes(self):
        expected = OperationObservation(THREAD, turn(2), "fixture-project", TASK, EVENT, 1, "in_progress")
        for wrapped in (False, True):
            self.assertEqual(project_record(tool_record(acknowledgement(), wrapped=wrapped), THREAD), (expected,))

    def test_known_exec_header_and_multiple_acknowledgements(self):
        record = tool_record(acknowledgement())
        record["payload"]["output"] = "Chunk ID: fixture\nWall time: 1\nOutput:\n" + json.dumps(acknowledgement()) + json.dumps(acknowledgement())
        self.assertEqual(len(project_record(record, THREAD)), 2)

    def test_failed_check_read_and_metadata_edit_are_not_state_boundaries(self):
        for change in ({"ok": False}, {"command": "task.show"}):
            self.assertEqual(project_record(tool_record({**acknowledgement(), **change}), THREAD), ())
        ack = acknowledgement()
        ack["data"]["changed_fields"] = ["verification"]
        self.assertEqual(project_record(tool_record(ack), THREAD), ())
        ack["data"]["event"] = None
        self.assertEqual(project_record(tool_record(ack), THREAD), ())

    def test_missing_turn_never_uses_adjacent_or_invented_identity(self):
        record = tool_record(acknowledgement())
        record["payload"].pop("internal_chat_message_metadata_passthrough")
        self.assertEqual(project_record(record, THREAD), ())

    def test_argument_or_message_containing_success_json_is_ignored(self):
        for kind in ("function_call", "message"):
            record = tool_record(acknowledgement())
            record["payload"]["type"] = kind
            self.assertEqual(project_record(record, THREAD), ())

    def test_turn_start_projects_no_prompt_or_other_body(self):
        record = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn(2),
                  "started_at": 2000, "prompt": "private-canary", "other": {"secret": "private-canary"}}}
        self.assertEqual(project_record(record, THREAD), (TurnObservation(THREAD, turn(2), 2000),))

    def test_ack_project_or_task_contradiction_is_not_a_boundary(self):
        ack = acknowledgement()
        ack["data"]["event"]["task_id"] = "another-task"
        self.assertEqual(project_record(tool_record(ack), THREAD), ())


if __name__ == "__main__":
    unittest.main()
