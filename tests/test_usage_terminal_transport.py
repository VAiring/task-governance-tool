"""Host terminal evidence is independent of stdout and Taskgov success."""
import json
import unittest

from tests.test_usage_response_adapter import (
    THREAD, batch_input, call, output, response, settled,
)
from task_governance_tool.usage_response_adapter import ResponseTracker, ResponseBoundary


def envelope(body="ordinary output", code=0):
    # Shape observed in the two supported Codex log versions. No private body.
    return dict(chunk_id="fixture", wall_time_seconds=0.1, exit_code=code, output=body)


def process_header(body="ordinary output", code=0):
    return f"Chunk ID: fixture\nWall time: 0.1 seconds\nProcess exited with code {code}\nOutput:\n{body}"


def wrapped(value):
    return [{"type": "input_text", "text": "Script completed\nWall time 0.1 seconds\nOutput:\n"},
            {"type": "input_text", "text": json.dumps(value)}]


def check():
    return dict(ok=True, command="task.show", project_id="fixture-project",
                data={"task": {"task_id": "tg_task_" + "a" * 16}, "events": []})


class TerminalTransportTests(unittest.TestCase):
    def fresh(self):
        self.tracker = ResponseTracker(THREAD, "fixture")
        self.order = 0

    def feed(self, record, numerical=None):
        self.order += 1
        return self.tracker.consume(record, self.order, numerical, "fixture-project")

    def restart(self):
        self.tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(self.tracker.state)))

    def test_finished_plain_output_does_not_exhaust_capacity(self):
        for count in (127, 128, 129):
            for transport in (envelope, process_header, lambda body, code: wrapped(envelope(body, code))):
                for body in ("ordinary output", "", "{}", '{"partial":'):
                    with self.subTest(count=count, transport=transport.__name__, body=body):
                        self.fresh()
                        for index in range(count):
                            self.feed(call(f"plain-{index}"))
                            self.feed({}, response(f"r{index}"))
                            self.assertEqual(self.feed(output(f"plain-{index}", transport(body, index % 2))), ())
                            self.restart()
                        self.assertFalse(list(self.tracker.pending_frames()))
                        self.assertFalse(self.tracker.state["limited"])
                        self.feed(call("check"))
                        self.feed({}, response("check-origin"))
                        found = self.feed(output("check", check()))
                        self.assertEqual([(x.command, x.response_id) for x in found if isinstance(x, ResponseBoundary)],
                                         [("task.show", "check-origin")])

    def test_success_never_comes_from_exit_code_or_invalid_stdout(self):
        for code in (0, 1, -9):
            for body in ("success", '{"ok":true', json.dumps({"ok": True, "command": "task.show"})):
                self.fresh()
                self.feed(call("plain", response_id="origin"))
                self.assertEqual(self.feed(output("plain", envelope(body, code))), ())
                self.assertFalse(list(self.tracker.pending_frames()))

    def test_unknown_or_conflicting_terminal_evidence_remains_unresolved(self):
        cases = ["ordinary output", '{"partial":', "Script completed\nOutput:\nordinary output",
                 {"private": envelope()}, {"exit_code": 0, "output": {"private": "opaque"}},
                 {"exit_code": True, "output": "plain"}, {"exit_code": "0", "output": "plain"},
                 {**envelope(), "truncated": True}, {**envelope(), "session_id": 7},
                 envelope("Warning: truncated output (original token count: 9000)"),
                 "Chunk ID: fixture\nProcess exited with code 0\nScript running with cell ID a\nOutput:\nplain",
                 "Chunk ID: fixture\nProcess exited with code 0\nProcess exited with code 1\nOutput:\nplain",
                 "Chunk ID: fixture\nUnknown header\nProcess exited with code 0\nOutput:\nplain",
                 wrapped(envelope()) + [{"type": "text", "text": "opaque sibling"}],
                 wrapped(envelope()) + wrapped(envelope()),
                 '{"exit_code":0,"exit_code":1,"output":"plain"}',
                 {"command": "unrecognized", "exit_code": 0, "output": "plain"}]
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                self.fresh()
                self.feed(call("unresolved", response_id="origin"))
                self.assertEqual(self.feed(output("unresolved", value)), ())
                self.restart()
                self.assertTrue(list(self.tracker.pending_frames()))

    def test_indexed_slots_resolve_independently_and_preserve_sparse_capacity(self):
        self.fresh()
        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"one"})',
                                                    'tools.exec_command({cmd:"two"})'), response_id="origin"))
        self.feed(output("batch", [settled(0, envelope())]))
        self.restart()
        self.assertEqual(self.tracker._pending_count(), 2)
        self.assertIsNone(self.tracker.state["batches"]["batch"][0])
        self.assertIsNotNone(self.tracker.state["batches"]["batch"][1])
        self.feed(output("batch", [settled(1, envelope(code=1))]))
        self.assertFalse(list(self.tracker.pending_frames()))
        for index in range(129):
            self.feed(call(f"b{index}", arguments=batch_input('tools.exec_command({cmd:"one"})')))
            self.feed(output(f"b{index}", [settled(0, envelope())]))
        self.assertFalse(self.tracker.state["limited"])

    def test_terminal_poll_retires_only_its_bound_lifetime(self):
        for tool, arguments in (("write_stdin", {"session_id": 7}),
                                ("exec", 'text(await tools.write_stdin({session_id:7}));')):
            self.fresh()
            self.feed(call("original", response_id="original-response"))
            self.feed(output("original", {"session_id": 7, "output": "running"}))
            self.restart()
            self.feed(call("poll", name=tool, arguments=arguments, number=3))
            self.assertEqual(self.feed(output("poll", envelope(), number=3)), ())
            self.assertFalse(list(self.tracker.pending_frames()))
            self.assertIn("session:7", self.tracker.state["retired"])

    def test_outer_cell_completion_does_not_prove_inner_process_completion(self):
        self.fresh()
        self.feed(call("original", response_id="origin"))
        self.feed(output("original", "Script running with cell ID outer"))
        self.feed(call("poll", name="wait", arguments={"cell_id": "outer"}))
        self.feed(output("poll", "Script completed\nOutput:\nordinary output"))
        self.assertIn("cell:outer", self.tracker.state["handles"])
        self.feed(output("poll", wrapped(envelope())))
        self.assertFalse(list(self.tracker.pending_frames()))
        self.assertIn("cell:outer", self.tracker.state["retired"])

    def test_simultaneous_running_and_terminal_polls_keep_unknown_handle(self):
        self.fresh()
        self.feed(call("original", response_id="origin"))
        self.feed(output("original", {"session_id": 7}))
        self.feed(call("batch", arguments=batch_input('tools.write_stdin({session_id:7})',
                                                     'tools.write_stdin({session_id:7})')))
        self.feed(output("batch", [settled(0, envelope()), settled(1, {"session_id": 7})]))
        self.restart()
        self.assertTrue(self.tracker.state["handles"]["session:7"]["ambiguous"])
        self.assertNotIn("session:7", self.tracker.state["retired"])

    def test_inherited_depth_size_and_index_ambiguity_remain_closed(self):
        for value in ([settled(0, envelope()), settled(0, envelope())],
                      [settled(True, envelope())], envelope("x" * (2_097_152 + 1))):
            self.fresh()
            self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"one"})')))
            self.feed(output("batch", value))
            self.assertTrue(list(self.tracker.pending_frames()))
        for levels in (8, 9):
            self.fresh()
            self.feed(call("one"))
            value = envelope()
            for _ in range(levels):
                value = {"content": [{"type": "text", "text": json.dumps(value)}]}
            self.feed(output("one", value))
            self.assertTrue(list(self.tracker.pending_frames()))

    def test_nested_and_conflicting_headers_cannot_discharge_a_lifetime(self):
        invalid = [
            "Chunk ID: a\nChunk ID: b\nProcess exited with code 0",
            "Chunk ID: a\nChunk ID: a\nProcess exited with code 0",
            "Chunk ID: a\nWall time: 1 seconds\nWall time 2 seconds\nProcess exited with code 0",
            "Chunk ID: a\nWall time: .. seconds\nProcess exited with code 0",
            "Chunk ID: a\nWall time: 999999999999999999999 seconds\nProcess exited with code 0",
            "Chunk ID: a\nOriginal token count: 1\nOriginal token count: 2\nProcess exited with code 0",
            "Chunk ID: a\nOriginal token count: 999999999999999999999\nProcess exited with code 0",
            "Chunk ID: a\nProcess exited with code 0\nProcess exited with code 1",
            "Script completed\nScript running with cell ID live",
            "Script completed\nScript completed",
        ]
        for head in invalid:
            for body in ("plain", json.dumps(check())):
                for wrapper in (lambda value: value, lambda value: "Script completed\nOutput:\n" + value,
                                lambda value: [{"type": "text", "text": value}]):
                    for batch in (False, True):
                        with self.subTest(head=head, body=body, batch=batch, wrapper=wrapper):
                            self.fresh()
                            arguments = batch_input('tools.exec_command({cmd:"one"})') if batch else 'text(await tools.exec_command({cmd:"one"}));'
                            self.feed(call("one", arguments=arguments, response_id="origin"))
                            value = wrapper(head + "\nOutput:\n" + body)
                            self.feed(output("one", [settled(0, value)] if batch else value))
                            self.restart()
                            self.assertTrue(list(self.tracker.pending_frames()))
        self.fresh()
        self.feed(call("nested", response_id="origin"))
        value = "Script completed\nOutput:\nScript running with cell ID inner\nOutput:\n" + json.dumps(envelope())
        self.feed(output("nested", value))
        self.assertTrue(list(self.tracker.pending_frames()))

    def test_colliding_lifetimes_survive_later_terminal_polls(self):
        for kind in ("session", "cell"):
            for issued in (False, True):
                for batch in (False, True):
                    for terminal in (envelope(), process_header(), check()):
                        with self.subTest(kind=kind, issued=issued, batch=batch, terminal=terminal):
                            self.fresh()
                            running = {"session_id": 7} if kind == "session" else "Script running with cell ID cell7"
                            key = "session:7" if kind == "session" else "cell:cell7"
                            tool = "write_stdin" if kind == "session" else "wait"
                            args = {"session_id": 7} if kind == "session" else {"cell_id": "cell7"}
                            source = 'tools.write_stdin({session_id:7})' if kind == "session" else 'tools.wait({cell_id:"cell7"})'
                            poll_call = (call("poll", arguments=batch_input(source)) if batch else
                                         call("poll", name=tool, arguments=args))
                            self.feed(call("first", response_id="first"))
                            self.feed(output("first", running))
                            if issued:
                                self.feed(poll_call)
                            self.feed(call("second", response_id="second"))
                            self.feed(output("second", running))
                            self.restart()
                            if not issued:
                                self.feed(poll_call)
                            found = self.feed(output("poll", [settled(0, terminal)] if batch else terminal))
                            self.restart()
                            self.assertIn(key, self.tracker.state["handles"])
                            self.assertTrue(self.tracker.state["handles"][key]["ambiguous"])
                            self.assertNotIn(key, self.tracker.state["retired"])
                            self.assertTrue(list(self.tracker.pending_frames()))
                            self.assertTrue(all(item.response_id is None for item in found if isinstance(item, ResponseBoundary)))

    def test_process_stdout_cannot_create_or_collide_with_a_live_handle(self):
        bodies = [json.dumps({"session_id": 7}), json.dumps({"output": {"session_id": 7}}),
                  "Script running with cell ID fake", json.dumps(check() | {"session_id": 7})]
        for body in bodies:
            for transport in (envelope, process_header, lambda text: wrapped(envelope(text))):
                for batch in (False, True):
                    with self.subTest(body=body, transport=transport, batch=batch):
                        self.fresh()
                        self.feed(call("live", response_id="live-origin"))
                        self.feed(output("live", {"session_id": 7}))
                        arguments = batch_input('tools.exec_command({cmd:"one"})') if batch else 'text(await tools.exec_command({cmd:"one"}));'
                        self.feed(call("one", arguments=arguments, response_id="other-origin"))
                        value = transport(body)
                        self.feed(output("one", [settled(0, value)] if batch else value))
                        self.restart()
                        self.assertFalse(self.tracker.state["calls"] or self.tracker.state["batches"])
                        self.assertEqual(set(self.tracker.state["handles"]), {"session:7"})
                        self.assertFalse(self.tracker.state["handles"]["session:7"]["ambiguous"])
        self.fresh()
        self.feed(call("running", response_id="origin"))
        self.feed(output("running", {"session_id": 7, "output": json.dumps({"session_id": 9})}))
        self.assertEqual(set(self.tracker.state["handles"]), {"session:7"})

    def test_terminal_stdout_is_opaque_even_beside_an_independent_live_result(self):
        self.fresh()
        self.feed(call("mixed", response_id="origin"))
        self.feed(output("mixed", wrapped(envelope(json.dumps({"session_id": 7}))) +
                         [{"type": "text", "text": json.dumps({"session_id": 9})}]))
        self.assertEqual(set(self.tracker.state["handles"]), {"session:9"})

    def test_invalid_process_envelope_cannot_expose_stdout_handles(self):
        for value in ({"exit_code": 0, "output": {"session_id": 7}},
                      {"exit_code": True, "output": '{"session_id":7}'},
                      {"exit_code": "0", "output": '{"session_id":7}'},
                      {"exit_code": 0, "session_id": 7, "output": "plain"}):
            for batch in (False, True):
                with self.subTest(value=value, batch=batch):
                    self.fresh()
                    arguments = batch_input('tools.exec_command({cmd:"one"})') if batch else 'text(await tools.exec_command({cmd:"one"}));'
                    self.feed(call("one", arguments=arguments, response_id="origin"))
                    self.feed(output("one", [settled(0, value)] if batch else value))
                    self.assertFalse(self.tracker.state["handles"])
                    self.assertTrue(list(self.tracker.pending_frames()))

    def test_acknowledgements_cannot_override_unresolved_process_transport(self):
        acknowledgements = [check(), check() | {"ok": False}, {"status": "saved"},
                            {"status": "review_wait_ended"}, {"context_check": "matched_at_read"}]
        mutations = [{"session_id": 7}, {"exit_code": None}, {"exit_code": True},
                     {"exit_code": "0"}, {"chunk_id": "invalid chunk"},
                     {"wall_time_seconds": -1}, {"original_token_count": False}]
        for ack in acknowledgements:
            for mutation in mutations:
                for wrapper in (lambda x: x, wrapped, lambda x: {"output": json.dumps(x)}):
                    for poll in (False, True):
                        for batch in (False, True):
                            with self.subTest(ack=ack, mutation=mutation, wrapper=wrapper, poll=poll, batch=batch):
                                self.fresh()
                                if poll:
                                    self.feed(call("original", response_id="origin"))
                                    self.feed(output("original", {"session_id": 7}))
                                expression = 'tools.write_stdin({session_id:7})' if poll else 'tools.exec_command({cmd:"one"})'
                                self.feed(call("one", arguments=batch_input(expression) if batch else f'text(await {expression});',
                                               response_id="poll-response" if poll else "origin"))
                                self.restart()
                                value = wrapper(envelope(json.dumps(ack)) | mutation)
                                found = self.feed(output("one", [settled(0, value)] if batch else value))
                                self.restart()
                                self.assertTrue(list(self.tracker.pending_frames()))
                                self.assertNotIn("session:7", self.tracker.state["retired"])
                                self.assertEqual(set(self.tracker.state["handles"]), {"session:7"} if poll else set())
                                if ack == check():
                                    self.assertEqual([x.response_id for x in found if isinstance(x, ResponseBoundary)], ["origin"])

    def test_incomplete_stdout_never_supplies_a_handle_or_poll_origin(self):
        for body in ('{"session_id":7}', '{"output":{"session_id":7}}', "Script running with cell ID fake"):
            for wrapper in (lambda x: x, wrapped, lambda x: {"content": json.dumps(x)}):
                for batch in (False, True):
                    with self.subTest(body=body, wrapper=wrapper, batch=batch):
                        self.fresh()
                        expression = 'tools.exec_command({cmd:"one"})'
                        self.feed(call("one", arguments=batch_input(expression) if batch else f'text(await {expression});',
                                       response_id="origin"))
                        value = wrapper(envelope(body, None))
                        self.feed(output("one", [settled(0, value)] if batch else value))
                        self.restart()
                        self.assertFalse(self.tracker.state["handles"])
                        self.assertTrue(list(self.tracker.pending_frames()))
                        self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}))
                        found = self.feed(output("poll", check()))
                        self.assertTrue(all(x.response_id is None for x in found if isinstance(x, ResponseBoundary)))
        self.fresh()
        self.feed(call("running", response_id="origin"))
        self.feed(output("running", envelope('{"session_id":9}', None) | {"session_id": 7}))
        self.restart()
        self.assertEqual(set(self.tracker.state["handles"]), {"session:7"})

    def test_body_terminal_controls_and_unresolved_siblings(self):
        for ack in (check(), check() | {"ok": False}, {"status": "saved"}):
            for wrapper in (lambda x: x, wrapped, lambda x: {"output": json.dumps(x)}):
                self.fresh()
                self.feed(call("one", response_id="origin"))
                self.feed(output("one", wrapper(ack)))
                self.assertFalse(list(self.tracker.pending_frames()))
            for sibling in (envelope(json.dumps(ack), None), {"chunk_id": "fixture", "output": json.dumps(ack)},
                            {"session_id": 7, "exit_code": 0, "output": json.dumps(ack)}):
                self.fresh()
                self.feed(call("one", response_id="origin"))
                self.feed(output("one", wrapped(ack) + wrapped(sibling)))
                self.restart()
                self.assertTrue(list(self.tracker.pending_frames()))

    def test_all_process_metadata_fences_stdout_and_validates_running_handles(self):
        markers = [{"chunk_id": "fixture"}, {"wall_time_seconds": 0.1},
                   {"original_token_count": 2}, {"exit_code": None}]
        invalid = [{"chunk_id": "bad chunk"}, {"wall_time_seconds": -1},
                   {"wall_time_seconds": True}, {"original_token_count": False},
                   {"original_token_count": 2**63}, {"unknown": True}]
        cases = markers + [dict(session_id=7, **item) for item in invalid]
        for metadata in cases:
            for wrapper in (lambda x: x, wrapped, lambda x: {"output": json.dumps(x)}):
                for batch in (False, True):
                    with self.subTest(metadata=metadata, wrapper=wrapper, batch=batch):
                        self.fresh()
                        expression = 'tools.exec_command({cmd:"one"})'
                        self.feed(call("one", arguments=batch_input(expression) if batch else f'text(await {expression});',
                                       response_id="origin"))
                        value = wrapper(metadata | {"output": '{"session_id":9}'})
                        self.feed(output("one", [settled(0, value)] if batch else value))
                        self.restart()
                        self.assertFalse(self.tracker.state["handles"])
                        self.assertTrue(list(self.tracker.pending_frames()))
                        for handle in (7, 9):
                            self.feed(call(f"poll{handle}", name="write_stdin", arguments={"session_id": handle}))
                            found = self.feed(output(f"poll{handle}", check()))
                            self.assertTrue(all(x.response_id is None for x in found if isinstance(x, ResponseBoundary)))
        for metadata in ({}, *markers):
            self.fresh()
            self.feed(call("one", response_id="origin"))
            self.feed(output("one", metadata | {"session_id": 7, "output": '{"session_id":9}'}))
            self.restart()
            self.assertEqual(set(self.tracker.state["handles"]), {"session:7"})
        # An output-only wrapper is still a supported container, not process stdout.
        self.fresh()
        self.feed(call("one", response_id="origin"))
        self.feed(output("one", {"output": '{"session_id":7}'}))
        self.assertEqual(set(self.tracker.state["handles"]), {"session:7"})

    def test_mixed_acknowledgements_preserve_child_transport_fences(self):
        children = [envelope(json.dumps(check()), None),
                    {"chunk_id": "fixture", "output": json.dumps(check())},
                    envelope(json.dumps(check())) | {"session_id": 7},
                    {"content": json.dumps(envelope("plain", None))}]
        for child in children:
            for field in ("output", "content"):
                for ack in (check(), check() | {"ok": False}, {"status": "saved"},
                            {"status": "review_wait_ended"}, {"context_check": "matched_at_read"}):
                    for poll in (False, True):
                        for batch in (False, True):
                            with self.subTest(child=child, field=field, ack=ack, poll=poll, batch=batch):
                                self.fresh()
                                if poll:
                                    self.feed(call("original", response_id="origin"))
                                    self.feed(output("original", {"session_id": 7}))
                                expression = 'tools.write_stdin({session_id:7})' if poll else 'tools.exec_command({cmd:"one"})'
                                self.feed(call("one", arguments=batch_input(expression) if batch else f'text(await {expression});',
                                               response_id="poll-response" if poll else "origin"))
                                value = ack | {field: json.dumps(child)}
                                found = self.feed(output("one", [settled(0, value)] if batch else value))
                                self.restart()
                                self.assertTrue(list(self.tracker.pending_frames()))
                                self.assertNotIn("session:7", self.tracker.state["retired"])
                                self.assertEqual(set(self.tracker.state["handles"]), {"session:7"} if poll else set())
                                if ack == check():
                                    self.assertTrue(any(x.response_id == "origin" for x in found if isinstance(x, ResponseBoundary)))


    def test_outer_process_stdout_cannot_route_a_batch_slot(self):
        for inner in ({"session_id": 7}, envelope(), check()):
            body = settled(0, inner)["text"]
            values = [envelope(body), envelope(body, None), envelope(body) | {"session_id": 7},
                      {"chunk_id": "fixture", "output": body}, {"wall_time_seconds": 0.1, "output": body},
                      {"original_token_count": 1, "output": body}, {"session_id": 7, "output": body},
                      process_header(body)]
            for value in values:
                for wrapper in (lambda x: x, wrapped, lambda x: {"content": json.dumps(x)}):
                    with self.subTest(inner=inner, value=value, wrapper=wrapper):
                        self.fresh()
                        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"one"})',
                                                                     'tools.exec_command({cmd:"two"})'), response_id="origin"))
                        found = self.feed(output("batch", wrapper(value)))
                        self.restart()
                        self.assertFalse(found)
                        self.assertFalse(self.tracker.state["handles"])
                        self.assertTrue(all(self.tracker.state["batches"]["batch"]))
                        self.feed(output("batch", [settled(1, envelope())]))
                        self.assertIsNone(self.tracker.state["batches"]["batch"][1])
                        self.assertIsNotNone(self.tracker.state["batches"]["batch"][0])
                        self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}))
                        found = self.feed(output("poll", check()))
                        self.assertTrue(all(x.response_id is None for x in found if isinstance(x, ResponseBoundary)))

    def test_duplicate_process_metadata_never_admits_ordinary_handles(self):
        values = ['{"exit_code":0,"exit_code":null,"session_id":7}',
                  '{"session_id":9,"session_id":7}',
                  '{"chunk_id":"a","chunk_id":"b","session_id":7}',
                  '{"wall_time_seconds":-1,"wall_time_seconds":1,"session_id":7}',
                  '{"original_token_count":-1,"original_token_count":1,"session_id":7}']
        for value in values:
            for wrapper in (lambda x: x, lambda x: {"output": x},
                            lambda x: [{"type": "text", "text": x}]):
                for direct in (False, True):
                    with self.subTest(value=value, wrapper=wrapper, direct=direct):
                        self.fresh()
                        self.feed(call("one", name="exec_command" if direct else "exec",
                            arguments={"cmd": "one"} if direct else 'text(await tools.exec_command({cmd:"one"}));',
                            response_id="origin"))
                        self.feed(output("one", wrapper(value)))
                        self.restart()
                        self.assertFalse(self.tracker.state["handles"])
                        self.assertTrue(list(self.tracker.pending_frames()))
                        self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}))
                        found = self.feed(output("poll", check()))
                        self.assertTrue(all(x.response_id is None for x in found if isinstance(x, ResponseBoundary)))
            self.fresh()
            self.feed(call("one", response_id="origin"))
            self.feed(output("one", [{"type": "text", "text": value},
                                     {"type": "text", "text": '{"session_id":9}'}]))
            self.assertEqual(set(self.tracker.state["handles"]), {"session:9"})


    def test_indexed_semantic_bodies_never_supply_unproven_origins_or_handles(self):
        from tests.test_usage_turn_adapter import acknowledgement, OperationObservation
        for value in (check(), acknowledgement(), {"session_id": 7}):
            indexed = settled(0, value)["text"]
            for transport in (lambda x: x, envelope, process_header,
                              lambda x: {"output": x},
                              lambda x: {"content": [{"type": "text", "text": x}]},
                              lambda x: check() | {"output": x}):
                for poll in (False, True):
                    for batch in (False, True):
                        with self.subTest(value=value, transport=transport, poll=poll, batch=batch):
                            self.fresh()
                            if poll:
                                self.feed(call("original", response_id="original-response"))
                                self.feed(output("original", {"session_id": 9}))
                            expression = 'tools.write_stdin({session_id:9})' if poll else 'tools.exec_command({cmd:"one"})'
                            self.feed(call("one", arguments=batch_input(expression) if batch else f'text(await {expression});',
                                           response_id="current-response"))
                            body = transport(indexed)
                            found = self.feed(output("one", [settled(0, body)] if batch else body))
                            self.restart()
                            boundaries = [x for x in found if isinstance(x, ResponseBoundary)]
                            if value.get("ok"):
                                self.assertTrue(boundaries)
                            if value.get("command") == "task.edit":
                                self.assertTrue(any(isinstance(x, OperationObservation) for x in found))
                            self.assertTrue(all(x.response_id is None for x in boundaries))
                            self.assertNotIn("session:7", self.tracker.state["handles"])
                            if poll:
                                self.assertIn("session:9", self.tracker.state["handles"])
                                self.assertNotIn("session:9", self.tracker.state["retired"])

    def test_unknown_text_blocks_cannot_route_slots_or_introduce_handles(self):
        for extra in ({"exit_code": 0}, {"session_id": 7}, {"unknown": True}):
            for kind in ("text", "input_text"):
                for wrap in (lambda x: x, lambda x: {"content": x}):
                    with self.subTest(extra=extra, kind=kind, wrap=wrap):
                        self.fresh()
                        self.feed(call("batch", arguments=batch_input('tools.exec_command({cmd:"one"})',
                                                                     'tools.exec_command({cmd:"two"})'), response_id="origin"))
                        bad = {"type": kind, "text": settled(0, envelope())["text"]} | extra
                        self.feed(output("batch", wrap([bad, settled(1, envelope())])))
                        self.restart()
                        self.assertIsNotNone(self.tracker.state["batches"]["batch"][0])
                        self.assertIsNone(self.tracker.state["batches"]["batch"][1])
                        for inner in ('{"session_id":7}', 'Script running with cell ID fake'):
                            self.fresh()
                            self.feed(call("one", response_id="origin"))
                            bad = {"type": kind, "text": inner} | extra
                            self.feed(output("one", wrap([bad, {"type": kind, "text": '{"session_id":9}'}])))
                            self.restart()
                            self.assertEqual(set(self.tracker.state["handles"]), {"session:9"})
                            self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}))
                            found = self.feed(output("poll", check()))
                            self.assertTrue(all(x.response_id is None for x in found if isinstance(x, ResponseBoundary)))

    def test_raw_source_objects_keep_duplicate_metadata_opaque(self):
        from task_governance_tool.usage_adapter import _json
        cases = ['{"exit_code":0,"exit_code":0,"output":"ordinary"}',
                 '{"exit_code":0,"exit_code":null,"session_id":7}',
                 '{"session_id":9,"session_id":7}',
                 '{"chunk_id":"a","chunk_id":"b","exit_code":0}',
                 '{"wall_time_seconds":-1,"wall_time_seconds":1,"exit_code":0}',
                 '{"original_token_count":-1,"original_token_count":1,"exit_code":0}']
        for raw in cases:
            for batch in (False, True):
                with self.subTest(raw=raw, batch=batch):
                    self.fresh()
                    self.feed(call("one", arguments=batch_input('tools.exec_command({cmd:"one"})',
                                                                  'tools.exec_command({cmd:"two"})') if batch else
                                   'text(await tools.exec_command({cmd:"one"}));', response_id="origin"))
                    value = ({"output": {"i": 0, "result": {"status": "fulfilled", "value": "RAW_OBJECT"}}}
                             if batch else "RAW_OBJECT")
                    wire = json.dumps(output("one", value)).replace('"RAW_OBJECT"', raw).encode()
                    self.feed(_json(wire))
                    self.restart()
                    self.assertTrue(list(self.tracker.pending_frames()))
                    self.assertFalse(self.tracker.state["handles"])
                    self.feed(call("poll", name="write_stdin", arguments={"session_id": 7}))
                    found = self.feed(output("poll", check()))
                    self.assertTrue(all(x.response_id is None for x in found if isinstance(x, ResponseBoundary)))


if __name__ == "__main__":
    unittest.main()
