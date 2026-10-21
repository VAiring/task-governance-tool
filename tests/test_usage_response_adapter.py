"""Synthetic response/call and late-result fixtures; no host or private logs."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.usage_response_adapter import ResponseTracker, ResponseBoundary, objects
from task_governance_tool.usage_values import ResponseUsage, UsageError
from tests.test_usage_turn_adapter import acknowledgement, THREAD, turn


def call(identity, *, name="functions.exec", arguments='text(await tools.exec_command({cmd:"fixture"}));', number=2, response_id=None):
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


def batch_input(*calls, store=False):
    saved = 'store("PRIVATE_LITERAL_"+i,results[i]);' if store else ""
    return ("const results = await Promise.allSettled([" + ",".join(calls)
            + "]);for(let i=0;i<results.length;i++){" + saved + "text({i,result:results[i]});}")


def settled(index, value):
    return {"type": "input_text", "text": json.dumps({"i": index, "result": {"status": "fulfilled", "value": value}})}


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

    def test_observed_runtime_short_name_cannot_bind_indexed_results_without_input_proof(self):
        record = call("a", name="exec")
        record["payload"]["type"] = "custom_tool_call"
        self.feed(record, 10)
        self.feed({}, 20, response("r1"))
        value = [{"type": "input_text", "text": "Script completed\nWall time 1.2 seconds\nOutput:\n"},
                 {"type": "input_text", "text": json.dumps({"i": 0, "result": {"status": "fulfilled",
                    "value": {"exit_code": 0, "output": json.dumps(acknowledgement())}}})}]
        found = self.feed(output("a", value), 30)
        self.assertIsNone(found[0].response_id)

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
        for handle in ("07", str(2**63), "9" * 100, str(2**53 + 1), str(2**63 - 1)):
            self.assertEqual(_handle_input("exec", "text(await tools.write_stdin({session_id:" + handle + "}));"),
                             None)
        for handle in (2**53, 2**54):
            self.assertEqual(_handle_input("exec", "text(await tools.write_stdin({session_id:" + str(handle) + "}));"),
                             ("session", str(handle)))
        self.assertEqual(_handle_input("write_stdin", {"session_id": 2**53 + 1}), ("session", str(2**53 + 1)))

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

    def test_batch_mixed_poll_and_exec_preserve_separate_origins(self):
        self.feed(call("original", response_id="origin"), 10)
        self.feed(output("original", {"session_id": 7}), 20)
        arguments = batch_input('tools.write_stdin({session_id:7,chars:""})',
                                'tools.exec_command({cmd:"PRIVATE_COMMAND",max_output_tokens:3000})', store=True)
        self.feed(call("batch", arguments=arguments, response_id="batch-response", number=3), 30)
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
        found = self.feed(output("batch", [settled(1, acknowledgement()), settled(0, acknowledgement())], number=3), 40)
        self.assertEqual([(item.call_id, item.response_id, item.turn_id, item.record_order) for item in found],
                         [("batch", "batch-response", turn(3), 30), ("original", "origin", turn(2), 10)])
        self.assertFalse(self.tracker.state["batches"])
        self.assertNotIn("PRIVATE", json.dumps(self.tracker.state))

    def test_observed_batch_running_poll_preserves_later_sole_poll_origin(self):
        self.feed(call("original", response_id="origin"), 10)
        self.feed(output("original", {"session_id": 7}), 20)
        arguments = batch_input('tools.write_stdin({session_id:7,chars:"",yield_time_ms:25000,max_output_tokens:6500})',
                                'tools.exec_command({cmd:"PRIVATE_COMMAND","max_output_tokens":3000})', store=True)
        self.feed(call("batch", arguments=arguments, response_id="batch-response", number=3), 30)
        self.feed(output("batch", [settled(0, {"session_id": 7, "output": ""}),
                                   settled(1, {"exit_code": 0, "output": ""})], number=3), 40)
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
        self.feed(call("final", arguments='text(await tools.write_stdin({session_id:7,chars:""}));', number=4), 50)
        found = self.feed(output("final", acknowledgement(), number=4), 60)
        self.assertEqual((found[0].call_id, found[0].response_id, found[0].turn_id), ("original", "origin", turn(2)))

    def test_batch_multiple_polls_use_index_not_arrival_order(self):
        for index in (0, 1):
            self.feed(call(f"origin-{index}", response_id=f"r{index}"), 10 + index)
            self.feed(output(f"origin-{index}", {"session_id": index + 7}), 20 + index)
        self.feed(call("batch", arguments=batch_input('tools.write_stdin({session_id:7})',
                       'tools.write_stdin({session_id:8})'), number=3), 30)
        found = self.feed(output("batch", [settled(1, acknowledgement()), settled(0, acknowledgement())], number=3), 40)
        self.assertEqual([(item.call_id, item.response_id) for item in found], [("origin-1", "r1"), ("origin-0", "r0")])

    def test_batch_unreadable_or_missing_sibling_stays_pending_after_restart(self):
        for sibling in (None, {"type": "input_text", "text": json.dumps({"i": 1, "result":
                         {"status": "rejected", "reason": "PRIVATE_REASON"}})},
                        settled(1, {"private": "PRIVATE_BODY"})):
            with self.subTest(sibling=sibling is None):
                self.setUp()
                self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"first"})',
                               'tools.exec_command({cmd:"second"})'), response_id="origin"), 10)
                values = [settled(0, acknowledgement())] + ([] if sibling is None else [sibling])
                found = self.feed(output("batch", values), 20)
                self.assertEqual(len(found), 1)
                self.assertEqual(self.tracker.state["batches"]["batch"][0], None)
                self.assertIsNotNone(self.tracker.state["batches"]["batch"][1])
                self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
                self.assertNotIn("PRIVATE", json.dumps(self.tracker.state))
                self.feed(output("batch", [settled(1, {"exit_code": 1})]), 30)
                self.assertFalse(self.tracker.state["batches"])

    def test_batch_same_call_handle_collision_is_preflighted_before_any_boundary(self):
        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"first"})',
                       'tools.exec_command({cmd:"second"})'), response_id="origin"), 10)
        result = {"session_id": 7, "output": json.dumps(acknowledgement())}
        found = self.feed(output("batch", [settled(0, result), settled(1, result)]), 20)
        self.assertEqual(len(found), 2)
        self.assertTrue(all(item.response_id is None for item in found))
        self.assertTrue(self.tracker.state["handles"]["session:7"]["ambiguous"])
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
        self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}), 30)
        found = self.feed(output("poll", acknowledgement()), 40)
        self.assertIsNone(found[0].response_id)

    def test_batch_duplicate_index_or_unknown_wrapper_never_confirms_origin(self):
        for invalid in ([settled(0, acknowledgement()), settled(0, acknowledgement())],
                        [settled(2, acknowledgement())], {"private": [settled(0, acknowledgement())]}):
            self.setUp()
            self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"first"})',
                           'tools.exec_command({cmd:"second"})'), response_id="origin"), 10)
            self.assertEqual(self.feed(output("batch", invalid), 20), ())
            self.assertEqual(len(list(self.tracker.pending_frames())), 2)

    def test_unsupported_batch_is_not_a_fresh_origin(self):
        self.feed(call("unknown", arguments="const results=await Promise.allSettled(variable);text(results);",
                       response_id="unproven"), 10)
        found = self.feed(output("unknown", [settled(0, acknowledgement())]), 20)
        self.assertIsNone(found[0].response_id)

    def test_outer_yielded_batch_stays_pending_without_cell_origin_alias(self):
        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"first"})'), response_id="origin"), 10)
        self.feed(output("batch", "Script running with cell ID batch-cell"), 20)
        self.assertIn("batch", self.tracker.state["batches"])
        self.assertNotIn("cell:batch-cell", self.tracker.state["handles"])

    def test_batch_slots_share_pending_capacity_with_single_calls(self):
        from task_governance_tool.usage_response_adapter import MAX_CONTEXT
        for index in range(MAX_CONTEXT - 1):
            self.feed(call(f"pending-{index}", response_id="prior"), index)
        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"first"})',
                       'tools.exec_command({cmd:"second"})'), response_id="lost"), 200)
        self.assertNotIn("batch", self.tracker.state["batches"])
        self.assertEqual(self.tracker.state["overflow"]["response"], "lost")
        self.assertTrue(self.tracker.state["limited"])
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))

    def test_sparse_batch_positions_remain_bounded_until_the_batch_drains(self):
        from task_governance_tool.usage_response_adapter import MAX_CONTEXT
        self.feed(call("batch", arguments=batch_input(*['tools.exec_command({cmd:"command"})'] * MAX_CONTEXT),
                       response_id="origin"), 10)
        self.feed(output("batch", [settled(index, {"exit_code": 0}) for index in range(MAX_CONTEXT - 1)]), 20)
        self.feed(call("extra", response_id="lost"), 30)
        self.assertNotIn("extra", self.tracker.state["calls"])
        self.assertEqual(self.tracker.state["overflow"]["response"], "lost")
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))

    def test_simultaneous_terminal_and_running_polls_retain_unknown_lifetime(self):
        for reverse in (False, True):
            self.setUp()
            self.feed(call("original", response_id="origin"), 10)
            self.feed(output("original", {"session_id": 7}), 20)
            self.feed(call("batch", arguments=batch_input('tools.write_stdin({session_id:7})',
                           'tools.write_stdin({session_id:7})')), 30)
            values = [settled(0, acknowledgement()), settled(1, {"session_id": 7})]
            found = self.feed(output("batch", list(reversed(values)) if reverse else values), 40)
            self.assertEqual(len(found), 1)
            self.assertIsNone(found[0].response_id)
            self.assertTrue(self.tracker.state["handles"]["session:7"]["ambiguous"])
            self.assertNotIn("session:7", self.tracker.state["retired"])
            self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))

    def test_later_slot_handle_collision_invalidates_previously_issued_poll(self):
        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"first"})',
                       'tools.exec_command({cmd:"second"})'), response_id="origin"), 10)
        self.feed(output("batch", [settled(0, {"session_id": 7})]), 20)
        self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}), 30)
        self.feed(output("batch", [settled(1, {"session_id": 7})]), 40)
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
        found = self.feed(output("poll", acknowledgement()), 50)
        self.assertIsNone(found[0].response_id)

    def test_exact_previous_continuation_is_decoded_without_corruption_repair(self):
        self.feed(call("pending", response_id="origin"), 10)
        legacy = json.loads(json.dumps(self.tracker.state))
        legacy["version"] = 1
        del legacy["batches"]
        del legacy["calls"]["pending"]["slot"]
        del legacy["calls"]["pending"]["bracket_closed"]
        restored = ResponseTracker(THREAD, "fixture", legacy)
        self.assertEqual(restored.state["version"], 4)
        self.assertIsNone(restored.state["calls"]["pending"]["slot"])
        self.assertTrue(restored.state["calls"]["pending"]["bracket_closed"])
        self.assertNotIn("slot", legacy["calls"]["pending"])
        for change in (lambda value: value["calls"]["pending"].pop("method"),
                       lambda value: value.update(batches={}),
                       lambda value: value["calls"]["pending"].update(slot=None)):
            altered = json.loads(json.dumps(legacy))
            change(altered)
            with self.assertRaises(UsageError):
                ResponseTracker(THREAD, "fixture", altered)

    def test_ordinary_first_opaque_result_freezes_bracket_across_restart(self):
        for initial in ((), ("origin",), ("origin", "conflict")):
            for restart in (False, True):
                with self.subTest(initial=initial, restart=restart):
                    self.setUp()
                    self.feed(call("pending"), 10)
                    for index, identity in enumerate(initial):
                        self.feed({}, 11 + index, response(identity))
                    self.feed(output("pending", {"private": "opaque"}), 20)
                    frozen = json.loads(json.dumps(self.tracker.state))
                    self.assertTrue(frozen["calls"]["pending"]["bracket_closed"])
                    if restart:
                        self.tracker = ResponseTracker(THREAD, "fixture", frozen)
                    self.feed({}, 30, response("later"))
                    found = self.feed(output("pending", acknowledgement()), 40)
                    self.assertEqual(len(found), 1)
                    self.assertEqual(found[0].response_id, "origin" if len(initial) == 1 else None)
                    self.assertNotIn("pending", self.tracker.state["calls"])

    def test_batch_first_host_result_closes_all_slots_even_without_routed_values(self):
        cases = (
            [settled(0, {"private": "opaque"})],
            [{"type": "text", "text": json.dumps({"i": 0, "result": {"status": "rejected", "reason": "opaque"}})}],
            [settled(0, acknowledgement()), settled(0, acknowledgement())],
            [settled(7, acknowledgement())],
            [settled(0, {"exit_code": 0})],
            [],
            "Script running with cell ID outer-cell",
            '{"i":0,"i":1,"result":{"status":"fulfilled","value":{}}}',
        )
        for case_index, first in enumerate(cases):
            for initial in ((), ("origin",), ("origin", "conflict")):
                for restart in (False, True):
                    with self.subTest(case=case_index, initial=initial, restart=restart):
                        self.setUp()
                        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"one"})',
                                       'tools.exec_command({cmd:"two"})')), 10)
                        for index, identity in enumerate(initial):
                            self.feed({}, 11 + index, response(identity))
                        self.feed(output("batch", first), 20)
                        self.assertTrue(all(frame is None or frame["bracket_closed"]
                                            for frame in self.tracker.state["batches"]["batch"]))
                        if restart:
                            self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
                        self.feed({}, 30, response("later"))
                        found = self.feed(output("batch", [settled(1, acknowledgement())]), 40)
                        self.assertEqual(len(found), 1)
                        self.assertEqual(found[0].response_id, "origin" if len(initial) == 1 else None)

    def test_first_result_closure_preserves_explicit_and_unrelated_pending_origins(self):
        for use_batch in (False, True):
            with self.subTest(batch=use_batch):
                self.setUp()
                args = batch_input('tools.exec_command({cmd:"one"})') if use_batch else 'text(await tools.exec_command({cmd:"one"}));'
                self.feed(call("explicit", arguments=args, response_id="explicit-origin"), 10)
                self.feed(call("unrelated"), 11)
                first = [settled(0, {"private": "opaque"})] if use_batch else {"private": "opaque"}
                self.feed(output("explicit", first), 20)
                self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
                self.feed({}, 30, response("later"))
                final = [settled(0, acknowledgement())] if use_batch else acknowledgement()
                found = self.feed(output("explicit", final), 40)
                self.assertEqual(found[0].response_id, "explicit-origin")
                other = self.feed(output("unrelated", acknowledgement()), 50)
                self.assertEqual(other[0].response_id, "later")

    def test_missing_or_invalid_first_output_turn_cannot_leave_a_bracket_open(self):
        for use_batch in (False, True):
            for metadata in (None, {}, {"turn_id": "invalid"}):
                with self.subTest(batch=use_batch, metadata=metadata):
                    self.setUp()
                    args = batch_input('tools.exec_command({cmd:"one"})') if use_batch else 'text(await tools.exec_command({cmd:"one"}));'
                    self.feed(call("pending", arguments=args), 10)
                    self.feed({}, 15, response("origin"))
                    first = output("pending", acknowledgement())
                    first["payload"]["internal_chat_message_metadata_passthrough"] = metadata
                    self.assertEqual(self.feed(first, 20), ())
                    self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
                    self.feed({}, 30, response("later"))
                    final = [settled(0, acknowledgement())] if use_batch else acknowledgement()
                    found = self.feed(output("pending", final), 40)
                    self.assertEqual(len(found), 1)
                    self.assertIsNone(found[0].response_id)

    def test_closed_missing_origin_is_preserved_through_handle_and_pending_poll_alias(self):
        for use_batch in (False, True):
            for initial in (None, "origin"):
                with self.subTest(batch=use_batch, initial=initial):
                    self.setUp()
                    args = batch_input('tools.exec_command({cmd:"one"})') if use_batch else 'text(await tools.exec_command({cmd:"one"}));'
                    self.feed(call("original", arguments=args), 10)
                    if initial:
                        self.feed({}, 15, response(initial))
                    first = [settled(0, {"private": "opaque"})] if use_batch else {"private": "opaque"}
                    self.feed(output("original", first), 20)
                    self.feed({}, 30, response("late-before-handle"))
                    running = [settled(0, {"session_id": 7})] if use_batch else {"session_id": 7}
                    self.feed(output("original", running), 40)
                    self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}), 50)
                    self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
                    self.assertTrue(self.tracker.state["handles"]["session:7"]["bracket_closed"])
                    self.assertTrue(self.tracker.state["calls"]["poll"]["bracket_closed"])
                    self.feed({}, 60, response("late-before-poll-result"))
                    found = self.feed(output("poll", acknowledgement()), 70)
                    self.assertEqual((found[0].call_id, found[0].response_id), ("original", initial))

    def test_current_open_bracket_survives_restart_before_first_result(self):
        self.feed(call("pending"), 10)
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))
        self.assertFalse(self.tracker.state["calls"]["pending"]["bracket_closed"])
        self.feed({}, 20, response("origin"))
        found = self.feed(output("pending", acknowledgement()), 30)
        self.assertEqual(found[0].response_id, "origin")

    def test_legacy_contexts_never_reuse_unproven_brackets_or_attach_late_candidates(self):
        for version in (1, 2):
            with self.subTest(version=version):
                def old_frame(identity, *, method="single_bracket", handle=None):
                    frame = {"call": identity, "turn": turn(2), "order": 10, "response": "old-origin",
                             "method": method, "ambiguous": False, "handle": handle}
                    if method == "unavailable":
                        frame["response"] = None
                    if version == 2:
                        frame["slot"] = None
                    return frame
                legacy = {"version": version, "calls": {
                    "pending": old_frame("pending"), "unknown": old_frame("unknown", method="unavailable"),
                    "poll": old_frame("original", handle="session:7")},
                    "handles": {"session:7": old_frame("original"),
                                "session:8": old_frame("explicit", method="explicit")},
                    "retired": ["session:9"], "limited": True, "overflow": old_frame("lost")}
                if version == 2:
                    legacy["batches"] = {"batch": [None, {**old_frame("batch"), "slot": 1},
                                                  {**old_frame("batch", method="explicit"), "slot": 2}]}
                original = json.loads(json.dumps(legacy))
                self.tracker = ResponseTracker(THREAD, "fixture", legacy)
                self.assertEqual(legacy, original)
                self.assertEqual(self.tracker.state["version"], 4)
                for frame in self.tracker.pending_frames():
                    self.assertTrue(frame["bracket_closed"])
                    self.assertIsNone(frame["response"])
                    self.assertNotEqual(frame["method"], "single_bracket")
                self.feed({}, 20, response("late"))
                for identity in ("pending", "unknown", "poll"):
                    found = self.feed(output(identity, acknowledgement()), 30)
                    self.assertIsNone(found[0].response_id)
                self.assertIsNone(self.tracker.state["handles"]["session:8"]["response"])

    def test_continuation_bracket_marker_is_closed_and_validated_in_every_frame_group(self):
        self.feed(call("origin", response_id="explicit"), 10)
        self.feed(output("origin", {"session_id": 7}), 20)
        self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}), 30)
        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"one"})')), 40)
        self.tracker.state["limited"] = True
        self.tracker.state["overflow"] = {**self.tracker.state["handles"]["session:7"], "call": "lost"}
        def selected(state, location):
            if location == "call":
                return state["calls"]["poll"]
            if location == "handle":
                return state["handles"]["session:7"]
            if location == "batch":
                return state["batches"]["batch"][0]
            return state["overflow"]
        for location in ("call", "handle", "batch", "overflow"):
            for invalid in (None, 0, 1, "true"):
                with self.subTest(location=location, invalid=invalid):
                    altered = json.loads(json.dumps(self.tracker.state))
                    frame = selected(altered, location)
                    if invalid is None:
                        frame.pop("bracket_closed")
                    else:
                        frame["bracket_closed"] = invalid
                    with self.assertRaises(UsageError):
                        ResponseTracker(THREAD, "fixture", altered)
        for location in ("call", "handle", "overflow"):
            altered = json.loads(json.dumps(self.tracker.state))
            frame = selected(altered, location)
            frame["bracket_closed"] = False
            with self.assertRaises(UsageError):
                ResponseTracker(THREAD, "fixture", altered)
        for version in (1, 2):
            altered = json.loads(json.dumps(self.tracker.state))
            altered["version"] = version
            with self.assertRaises(UsageError):
                ResponseTracker(THREAD, "fixture", altered)

    def test_batch_depth_budget_spans_outer_and_inner_containers(self):
        def nested(value, count):
            for _ in range(count):
                value = {"output": value}
            return value
        for outer in range(10):
            for inner in range(10):
                with self.subTest(outer=outer, inner=inner):
                    self.setUp()
                    self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"command"})'),
                                   response_id="origin"), 10)
                    body = {"i": 0, "result": {"status": "fulfilled", "value": nested(acknowledgement(), inner)}}
                    found = self.feed(output("batch", nested(body, outer)), 20)
                    self.assertEqual(len(found), int(outer + 1 + inner <= 8))

    def test_sole_literal_poll_allsettled_argument_preserves_original_origin(self):
        self.feed(call("original", response_id="origin"), 10)
        self.feed(output("original", {"session_id": 7}), 20)
        self.feed(call("poll", arguments='text(await tools.write_stdin({session_id:7,chars:"allSettled"}));',
                       response_id="later", number=3), 30)
        found = self.feed(output("poll", acknowledgement(), number=3), 40)
        self.assertEqual((found[0].call_id, found[0].response_id), ("original", "origin"))

    def test_literal_batch_words_preserve_fresh_exec_but_extra_statements_are_unknown(self):
        for arguments, expected in (('text(await tools.exec_command({cmd:"Promise.allSettled([])"}));', "origin"),
                          ('store("Promise.allSettled([])",0);text(await tools.exec_command({cmd:"command"}));', None),
                          ('// Promise.allSettled([])\ntext(await tools.exec_command({cmd:"command"}));', None),
                          ('const pattern=/Promise.allSettled\\(/;text(await tools.exec_command({cmd:"command"}));', None)):
            with self.subTest(arguments=arguments):
                self.setUp()
                self.feed(call("original", arguments=arguments, response_id="origin"), 10)
                found = self.feed(output("original", acknowledgement()), 20)
                self.assertEqual(found[0].response_id, expected)

    def test_overdepth_or_unreadable_cell_headers_do_not_create_handles(self):
        from task_governance_tool.usage_response_adapter import _output_handles, MAX_TEXT
        header = "Script running with cell ID fixture-cell"
        self.assertEqual(_output_handles(header, [], depth=9), set())
        self.assertEqual(_output_handles([{"type": "text", "text": header}], [], depth=8), set())
        for value in (header + "\nPRIVATE", header + "\n" + "x" * MAX_TEXT,
                      header + '\nOutput:\n{"output":{"private":"body"}}'):
            with self.subTest(length=len(value)):
                self.assertEqual(_output_handles(value, []), set())
        self.assertEqual(_output_handles(header, [], depth=8), {("cell", "fixture-cell")})

    def test_unsupported_executable_batch_forms_cannot_bind_indexed_results_as_fresh(self):
        for source in ('Promise["all"+"Settled"]([tools["write_stdin"]({session_id:7})])',
                       '(Promise.allSettled)([tools["write_stdin"]({session_id:7})])',
                       'Promise?.allSettled([tools["write_stdin"]({session_id:7})])',
                       'Promise.all\\u0053ettled([tools["write_stdin"]({session_id:7})])'):
            with self.subTest(source=source):
                self.setUp()
                self.feed(call("original", response_id="origin"), 10)
                self.feed(output("original", {"session_id": 7}), 20)
                self.feed(call("unsupported", arguments=source, response_id="later"), 30)
                found = self.feed(output("unsupported", [settled(0, acknowledgement())]), 40)
                self.assertEqual(len(found), 1)
                self.assertIsNone(found[0].response_id)
                self.assertIn("session:7", self.tracker.state["handles"])

    def test_conflicting_cell_headers_never_choose_the_first_declaration(self):
        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"command"})'),
                       response_id="origin"), 10)
        body = "Script running with cell ID cell-a\nScript running with cell ID cell-b\nOutput:\n{}"
        self.feed(output("batch", [settled(0, body)]), 20)
        self.assertEqual(set(self.tracker.state["handles"]), {"cell:cell-a", "cell:cell-b"})
        self.assertTrue(all(frame["ambiguous"] for frame in self.tracker.state["handles"].values()))
        self.feed(call("poll", name="wait", arguments={"cell_id": "cell-a"}), 30)
        found = self.feed(output("poll", acknowledgement()), 40)
        self.assertIsNone(found[0].response_id)


if __name__ == "__main__":
    unittest.main()
