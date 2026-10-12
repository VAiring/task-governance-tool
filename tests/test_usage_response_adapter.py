"""Synthetic response/call and late-result fixtures; no host or private logs."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.usage_response_adapter import ResponseTracker, ResponseBoundary, objects
from task_governance_tool.usage_values import ResponseUsage, UsageError
from tests.test_usage_turn_adapter import acknowledgement, THREAD, turn


def call(identity, *, name="functions.exec", arguments="", number=2, response_id=None):
    metadata = {"turn_id": turn(number)}
    if response_id is not None:
        metadata["response_id"] = response_id
    return {"type": "response_item", "payload": {"type": "function_call", "name": name,
            "call_id": identity, "arguments": arguments, "internal_chat_message_metadata_passthrough": metadata}}


def output(identity, value, *, number=2):
    return {"type": "response_item", "payload": {"type": "function_call_output", "call_id": identity,
            "output": value, "internal_chat_message_metadata_passthrough": {"turn_id": turn(number)}}}


def response(identity, *, number=2):
    return ResponseUsage("fixture", identity, THREAD, turn(number), "fixture-model", "medium", (10, 2, 12, None, None, None))


class ResponseTransportTests(unittest.TestCase):
    def setUp(self):
        self.tracker = ResponseTracker(THREAD, "fixture")

    def feed(self, record, order, numerical=None):
        from task_governance_tool.usage_turn_adapter import OperationObservation
        return tuple(item for item in self.tracker.consume(record, order, numerical, "fixture-project")
                     if not isinstance(item, OperationObservation))

    def test_unique_bracket_and_warning_bind_original_command_response(self):
        self.feed(call("call-1"), 10)
        self.feed({}, 20, response("r1"))
        warning = "Failed to find real location of C:\\runtime\\python.exe\n"
        found = self.feed(output("call-1", warning + json.dumps(acknowledgement())), 30)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].response_id, found[0].record_order, found[0].method), ("r1", 10, "single_bracket"))
        self.assertNotIn("runtime", json.dumps(self.tracker.state))

    def test_multiple_candidates_remain_unknown_not_nearest(self):
        self.feed(call("call-1"), 10)
        self.feed({}, 20, response("r1"))
        self.feed({}, 30, response("r2"))
        found = self.feed(output("call-1", json.dumps(acknowledgement())), 40)
        self.assertIsNone(found[0].response_id)
        self.assertEqual(found[0].method, "unavailable")

    def test_explicit_link_survives_delayed_numerical_arrival(self):
        self.feed(call("call-1", response_id="r1"), 10)
        found = self.feed(output("call-1", json.dumps(acknowledgement())), 20)
        self.assertEqual((found[0].response_id, found[0].method), ("r1", "explicit"))
        self.feed({}, 30, response("r1"))

    def test_session_polls_follow_the_first_call_across_turns(self):
        self.feed(call("original"), 10)
        self.feed({}, 20, response("origin"))
        self.feed(output("original", {"session_id": 7, "output": ""}), 30)
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
        self.feed(call("poll-1", name="functions.write_stdin", arguments={"session_id": 7}, number=3), 40)
        self.feed({}, 50, response("poll", number=3))
        found = self.feed(output("poll-1", {"exit_code": 0, "output": json.dumps(acknowledgement())}, number=3), 60)
        self.assertEqual((found[0].response_id, found[0].turn_id, found[0].record_order), ("origin", turn(2), 10))
        self.assertIn("session:7", self.tracker.state["retired"])

    def test_sole_literal_exec_poll_is_supported_but_variable_is_not(self):
        for arguments, expected in (("text(await tools.write_stdin({session_id:7,chars:\"\",yield_time_ms:30000}));", "origin"),
                                    ("text(await tools.write_stdin({session_id:session}));", None)):
            with self.subTest(arguments=arguments):
                self.setUp()
                self.feed(call("original"), 10)
                self.feed({}, 20, response("origin"))
                self.feed(output("original", {"session_id": 7, "output": ""}), 30)
                self.feed(call("poll", arguments=arguments), 40)
                self.feed({}, 50, response("poll-response"))
                found = self.feed(output("poll", {"exit_code": 0, "output": json.dumps(acknowledgement())}), 60)
                self.assertEqual(found[0].response_id, expected)

    def test_partial_json_unknown_warning_and_prose_never_yield_early_success(self):
        body = json.dumps(acknowledgement())
        for text in (body + '{"partial":', "Some warning\n" + body, "Example: " + body,
                     body + " trailing words"):
            with self.subTest(text_length=len(text)), self.assertRaises((UsageError, ValueError)):
                objects(text)

    def test_unknown_wrappers_are_not_recursively_searched(self):
        self.assertEqual(objects({"private": acknowledgement()}), [{"_transport_unreadable": True}])

    def test_two_results_in_one_known_transport_remain_complete(self):
        body = json.dumps(acknowledgement())
        self.assertEqual(len(objects(body + "\n" + body)), 2)

    def test_observed_runtime_short_name_and_separate_header_blocks(self):
        record = call("a", name="exec")
        record["payload"]["type"] = "custom_tool_call"
        self.feed(record, 10)
        self.feed({}, 20, response("r1"))
        value = [{"type": "input_text", "text": "Script completed\nWall time 1.2 seconds\nOutput:\n"},
                 {"type": "input_text", "text": json.dumps({"i": 0, "result": {"status": "fulfilled",
                    "value": {"exit_code": 0, "output": json.dumps(acknowledgement())}}})}]
        found = self.feed(output("a", value), 30)
        self.assertEqual(found[0].response_id, "r1")

    def test_missing_call_and_wrong_turn_supply_no_invented_origin(self):
        self.assertEqual(self.feed(output("absent", acknowledgement()), 10), ())
        self.feed(call("a", number=3), 20)
        self.feed({}, 30, response("r1"))
        found = self.feed(output("a", acknowledgement(), number=3), 40)
        self.assertIsNone(found[0].response_id)

    def test_no_arguments_or_private_output_in_continuation(self):
        self.feed(call("a", arguments="PRIVATE_CANARY"), 10)
        self.feed(output("a", "PRIVATE_OUTPUT"), 20)
        self.assertNotIn("PRIVATE", json.dumps(self.tracker.state))

    def test_check_ack_requires_the_public_shape(self):
        for data, expected in (({}, 0), ({"task": {"task_id": "made-up"}, "events": []}, 0),
                ({"tasks": [], "count": 0, "limit": 50}, 1)):
            self.setUp()
            self.feed(call("a", response_id="r1"), 10)
            ack = {"ok": True, "command": "task.list", "project_id": "fixture-project", "data": data}
            found = self.feed(output("a", ack), 20)
            self.assertEqual(len(found), expected)

    def test_context_and_compact_checks_use_public_returned_counts_and_closed_refs(self):
        task_id = "tg_task_" + "a" * 16
        selected = {"task": {"task_id": task_id, "project_id": "fixture-project"}}
        current = {"tasks": [{"task_id": task_id, "task_ref": "/data/selected/task"}],
                   "total_matching": 1, "returned_count": 1, "limit": 20, "truncated": False,
                   "statuses": ["in_progress", "review_pending", "paused", "blocked"]}
        data = {"selection": "current", "current": current, "next": None, "selected": selected}
        for altered, expected in ((data, 1),
                ({**data, "current": {**current, "returned_count": 0}}, 0),
                ({**data, "current": {**current, "tasks": [{"task_id": task_id, "task_ref": "/private"}]}}, 0),
                ({**data, "current": {**current, "tasks": [{"task_id": "tg_task_" + "b" * 16,
                    "task_ref": "/data/selected/task"}]}}, 0)):
            self.setUp()
            self.feed(call("check", response_id="r1"), 10)
            found = self.feed(output("check", {"ok": True, "command": "task.context", "project_id": "fixture-project", "data": altered}), 20)
            self.assertEqual(len(found), expected)
        self.setUp()
        self.feed(call("check", response_id="r1"), 10)
        compact = {"tasks": [], "total_matching": 0, "returned_count": 0, "limit": 5, "truncated": False}
        found = self.feed(output("check", {"ok": True, "command": "task.next", "project_id": "fixture-project", "data": compact}), 20)
        self.assertEqual(len(found), 1)

    def test_outer_cell_multiple_polls_and_terminal_handle_reuse(self):
        self.feed(call("origin", response_id="r1"), 10)
        self.feed(output("origin", "Script running with cell ID cell-a"), 20)
        for index in (1, 2):
            self.feed(call(f"poll-{index}", name="wait", arguments={"cell_id": "cell-a"}, number=3), 30 + index)
            self.feed(output(f"poll-{index}", "Script running with cell ID cell-a", number=3), 40 + index)
        self.feed(call("terminal", name="wait", arguments={"cell_id": "cell-a"}, number=3), 50)
        found = self.feed(output("terminal", acknowledgement(), number=3), 60)
        self.assertEqual((found[0].response_id, found[0].turn_id), ("r1", turn(2)))
        self.feed(call("reused", name="wait", arguments={"cell_id": "cell-a"}), 70)
        self.feed({}, 80, response("new"))
        found = self.feed(output("reused", acknowledgement()), 90)
        self.assertIsNone(found[0].response_id)

    def test_unsupported_version_and_interruption_do_not_create_a_bracket(self):
        for supported in (False, True):
            self.tracker = ResponseTracker(THREAD, "fixture", allow_bracket=supported)
            self.feed(call("a"), 10)
            self.feed({}, 20, response("r1"))
            if supported:
                self.tracker.interrupt_relation()
            found = self.feed(output("a", acknowledgement()), 30)
            self.assertIsNone(found[0].response_id)

    def test_poll_literal_integer_range_is_closed(self):
        from task_governance_tool.usage_response_adapter import _handle_input
        for handle in ("07", str(2**63), "9" * 100):
            self.assertEqual(_handle_input("exec", "text(await tools.write_stdin({session_id:" + handle + "}));"),
                             ("unresolved", ""))

    def test_live_handle_collision_invalidates_origin_and_already_issued_poll(self):
        for kind in ("session", "cell"):
            for issued in (False, True):
                with self.subTest(kind=kind, issued=issued):
                    self.setUp()
                    running = {"session_id": 7} if kind == "session" else "Script running with cell ID cell-7"
                    tool = "write_stdin" if kind == "session" else "wait"
                    args = {"session_id": 7} if kind == "session" else {"cell_id": "cell-7"}
                    self.feed(call("first", response_id="r1"), 10)
                    self.feed(output("first", running), 20)
                    if issued:
                        self.feed(call("poll", name=tool, arguments=args), 25)
                    self.feed(call("second", response_id="r2"), 30)
                    self.feed(output("second", running), 40)
                    self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
                    if not issued:
                        self.feed(call("poll", name=tool, arguments=args), 50)
                    found = self.feed(output("poll", acknowledgement()), 60)
                    self.assertIsNone(found[0].response_id)
                    self.assertEqual(found[0].method, "unavailable")

    def test_opaque_result_retains_origin_until_valid_terminal_evidence(self):
        for value in ({"private": {"session_id": 7}}, '{"partial":',
                      {"exit_code": 0, "output": {"private": {"session_id": 7}}},
                      {"exit_code": 0, "output": {"i": 0, "result": {"status": "rejected", "reason": "PRIVATE"}}},
                      [{"type": "text", "text": json.dumps(acknowledgement())}, {"unknown": "PRIVATE"}]):
            with self.subTest(value_type=type(value).__name__):
                self.setUp()
                self.feed(call("pending", response_id="r1"), 10)
                self.feed(output("pending", value), 20)
                self.assertIn("pending", self.tracker.state["calls"])
                self.assertNotIn("PRIVATE", json.dumps(self.tracker.state))
                self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
                self.feed(output("pending", {"exit_code": 1, "output": ""}), 30)
                self.assertNotIn("pending", self.tracker.state["calls"])

    def test_capacity_loss_retains_only_bounded_earliest_origin_after_drain(self):
        from task_governance_tool.usage_response_adapter import MAX_CONTEXT
        for number in range(MAX_CONTEXT + 2):
            self.feed(call("call-" + str(number), response_id="r" + str(number)), 10 + number)
        self.assertEqual(len(self.tracker.state["calls"]), MAX_CONTEXT)
        self.assertEqual(self.tracker.state["overflow"]["response"], "r128")
        for number in range(MAX_CONTEXT):
            self.feed(output("call-" + str(number), {"exit_code": 1}), 200 + number)
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
        self.assertFalse(self.tracker.state["calls"])
        self.assertTrue(self.tracker.state["limited"])
        self.assertEqual(self.tracker.state["overflow"]["order"], 138)


if __name__ == "__main__":
    unittest.main()
