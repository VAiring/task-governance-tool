"""Synthetic source corruption at the real full/incremental input boundary."""
from dataclasses import asdict
import json
import unittest
from unittest import mock

from tests.test_usage_collection import usage, THREAD
from tests.test_usage_attribution import turn
from tests.test_usage_response_adapter import call, output, batch_input, settled
from tests.test_usage_terminal_transport import check
from tests.test_usage_turn_adapter import acknowledgement, tool_record
from tests import test_usage_turn_adapter as turn_fixtures
from tests import test_usage_terminal_repository as fixtures
from tests.test_usage_wait_attribution import marker
from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_incremental_adapter import ScanState, scan
from task_governance_tool.usage_response_adapter import ResponseTracker, ResponseBoundary
from task_governance_tool.usage_values import UsageError
from task_governance_tool.usage_turn_adapter import OperationObservation, project_body, project_record
from task_governance_tool.usage_review_attribution import ReviewReceiptTurn


class _SourceFixture:
    setUp = fixtures.TerminalRepositoryTests.setUp
    records = fixtures.TerminalRepositoryTests.records
    write = fixtures.TerminalRepositoryTests.write
    write_turn = fixtures.TerminalRepositoryTests.write_turn

    def damaged(self, mode, kind):
        record = output("one", "PRIVATE_BODY", number=1)
        if kind == "duplicate_payload":
            return json.dumps(record).replace('"output": "PRIVATE_BODY"',
                '"output": "PRIVATE_BODY", "output": "PRIVATE_OTHER"')
        if kind == "duplicate_record":
            return json.dumps(record).replace('"type": "response_item"',
                '"type": "response_item", "type": "response_item"')
        if kind == "duplicate_metadata":
            return json.dumps(record).replace('"turn_id": ', '"turn_id": "conflict", "turn_id": ')
        if kind == "syntax":
            return '{"type":"response_item","payload":'
        if kind == "missing_call":
            record["payload"].pop("call_id")
        elif kind == "invalid_call":
            record["payload"]["call_id"] = []
        elif kind == "missing_turn":
            record["payload"].pop("internal_chat_message_metadata_passthrough")
        elif kind == "missing_kind":
            record["payload"].pop("type")
        elif kind == "empty_kind":
            record["payload"]["type"] = ""
        elif kind == "invalid_kind":
            record["payload"]["type"] = []
        elif kind == "missing_payload":
            record.pop("payload")
        elif kind == "missing_record_kind":
            record.pop("type")
        elif kind == "opaque":
            record["payload"]["output"] = "ordinary stdout"
        elif kind == "unrelated":
            record = {"type": "unrelated", "payload": None}
        elif kind == "message":
            record = {"type": "response_item", "payload": {"type": "message", "content": "PRIVATE_BODY"}}
        else:
            raise AssertionError(kind)
        return json.dumps(record)

    def source_case(self, mode, kind):
        arguments = batch_input('tools.exec_command({cmd:"one"})') if mode == "batch" else {"cmd": "one"}
        entry = call("one", name="exec" if mode == "batch" else "exec_command",
                     arguments=arguments, number=1)
        result = [settled(0, check())] if mode == "batch" else check()
        rows = [call("past", response_id="past-origin", number=1),
                usage("past-origin", turn=turn(1)), output("past", check(), number=1)]
        if mode == "poll":
            rows += [call("origin", response_id="poll-origin", number=1), usage("poll-origin", turn=turn(1)),
                     output("origin", {"session_id": 7}, number=1)]
            entry = call("one", name="write_stdin", arguments={"session_id": 7}, number=1)
        rows += [entry, {"RAW_LINE": True}, usage("later", turn=turn(1)), output("one", result, number=1),
                 call("fresh", number=1), usage("fresh-origin", turn=turn(1)), output("fresh", check(), number=1)]
        self.write_turn(rows)
        self.source.path.write_bytes(self.source.path.read_bytes().replace(
            b'{"RAW_LINE": true}', self.damaged(mode, kind).encode()))

    def full_and_resumed(self, *, pending=None, live_handle=None, response_relations=True):
        full = read_batch(self.source, include_attribution=True,
                          attribution_project_id="fixture-project", include_response_relations=response_relations)
        previous_context = None
        for limit in (1, 512):
            state, context, models, found, numbers, diagnostics = ScanState(), ResponseTracker.empty_state(), {}, [], [], set()
            if not response_relations:
                context = None
            while state.offset < self.source.path.stat().st_size:
                result = scan(self.source, state, models, project_id="fixture-project",
                              max_records=limit, response_context=context)
                # Actual serializable restart state, not a retained tracker object.
                state = ScanState(**json.loads(json.dumps(asdict(result.state))))
                context = json.loads(json.dumps(result.response_context))
                models.update(result.models)
                found.extend(result.attribution)
                numbers.extend(result.responses)
                diagnostics.update(result.diagnostics)
            self.assertEqual(tuple(found), full.attribution)
            self.assertEqual(tuple(numbers), full.responses)
            self.assertEqual(diagnostics, set(full.diagnostics))
            self.assertNotIn("PRIVATE", json.dumps(context))
            if previous_context is not None:
                self.assertEqual(context, previous_context)
            previous_context = context
            if pending is not None:
                self.assertEqual(bool(context["calls"] or context["batches"] or context["handles"]), pending)
            if live_handle is not None:
                self.assertIn(live_handle, context["handles"])
                self.assertNotIn(live_handle, context["retired"])
        return full

    def body_source(self, mode, body, *, process=False):
        entry = call("one", response_id="original", number=1)
        rows = []
        if process:
            body = {"exit_code": 0, "output": json.dumps(body)}
        if mode == "batch":
            entry = call("one", name="exec", response_id="original", number=1,
                         arguments=batch_input('tools.exec_command({cmd:"one"})'))
            body = [settled(0, body)]
        elif mode == "poll":
            rows = [entry, output("one", {"session_id": 7}, number=1)]
            entry = call("poll", name="write_stdin", arguments={"session_id": 7}, number=1)
        if mode != "historical":
            rows.append(entry)
        self.write_turn([*rows, usage("original", turn=turn(1)),
            output("poll" if mode == "poll" else "one", body, number=1),
            call("fresh", number=1), usage("later", turn=turn(1)),
            output("fresh", check(), number=1)])


class SourceBoundaryTests(_SourceFixture, unittest.TestCase):
    def test_review_registration_keeps_each_valid_receipt_entry(self):
        receipt = "tg_review_receipt_" + "a" * 16
        valid = {"receipt": {"review_receipt_id": receipt}}
        invalid = [None, {}, {"receipt": None}, {"receipt": {}},
                   *({"receipt": {"review_receipt_id": value}} for value in (None, [], "", "wrong"))]
        for command in ("review.receipt.add", "review.result.add"):
            for bad in invalid:
                for reverse in (False, True):
                    entries = [valid, bad][::-1] if reverse else [valid, bad]
                    ack = {"ok": True, "command": command, "project_id": "fixture-project",
                           "data": {"receipts": entries}}
                    for mode in ("direct", "batch", "poll", "historical"):
                        with self.subTest(command=command, bad=bad, reverse=reverse, mode=mode):
                            projected = project_body(ack, THREAD, turn(1), "fixture-project")
                            self.assertEqual(projected.observations, (ReviewReceiptTurn(THREAD, turn(1), receipt),))
                            self.assertTrue(projected.unreadable)
                            # A separate state ack cannot hide incomplete registration evidence.
                            self.body_source(mode, [{"type": "text", "text": json.dumps(ack)},
                                {"type": "text", "text": json.dumps(acknowledgement())}])
                            full = self.full_and_resumed(pending=mode != "historical")
                            self.assertEqual([x.receipt_id for x in full.attribution if isinstance(x, ReviewReceiptTurn)], [receipt])
                            self.assertEqual(sum(isinstance(x, OperationObservation) for x in full.attribution), 1)
                            self.assertEqual(len(full.responses), 2)

    def test_lifetime_and_origin_are_independent_of_json_representation(self):
        valid = json.dumps(check())
        duplicate = '{"PRIVATE":1,"PRIVATE":2}'
        cases = {
            "unused": valid[:-1] + ',"unused":' + duplicate + '}',
            "required": valid.replace('"events": []', '"events":' + duplicate),
            "transport": valid[:-1] + ',"output":' + duplicate + '}',
            "outer": valid.replace('"ok": true', '"ok":false,"ok":true'),
        }
        for damage, raw in cases.items():
            for mode in ("direct", "poll", "batch"):
                observations = []
                for form in ("object", "string"):
                    with self.subTest(damage=damage, mode=mode, form=form):
                        self.body_source(mode, "RAW_ACK" if form == "object" else raw)
                        if form == "object":
                            self.source.path.write_bytes(self.source.path.read_bytes().replace(b'"RAW_ACK"', raw.encode()))
                        full = self.full_and_resumed(pending=damage != "unused" or mode == "batch")
                        boundaries = [x for x in full.attribution if isinstance(x, ResponseBoundary)]
                        expected = ["original", "later"] if damage in ("unused", "transport") and mode != "batch" else ["later"]
                        self.assertEqual([x.response_id for x in boundaries], expected)
                        self.assertEqual(len(full.responses), 2)
                        # Byte offsets legitimately differ for escaped JSON;
                        # identities, proof and pending state must not.
                        observations.append(tuple({key: value for key, value in asdict(item).items()
                            if key != "record_order"} for item in full.attribution))
                self.assertEqual(*observations)

    def test_duplicate_call_invalidates_retained_origins_and_pending_poll_aliases(self):
        for batch_origin in (False, True):
            for repeated in ("origin", "origin_with_poll", "poll", "batch_poll"):
                for name in ("unrelated-tool", "exec_command"):
                    with self.subTest(batch_origin=batch_origin, repeated=repeated, name=name):
                        origin = call("origin", name="exec" if batch_origin else "exec_command",
                            response_id="original", number=1,
                            arguments=batch_input('tools.exec_command({cmd:"one"})') if batch_origin else {"cmd": "one"})
                        running = [settled(0, {"session_id": 7})] if batch_origin else {"session_id": 7}
                        poll = call("poll", name="write_stdin", arguments={"session_id": 7}, number=1)
                        if repeated == "batch_poll":
                            poll = call("poll", name="exec", number=1,
                                        arguments=batch_input('tools.write_stdin({session_id:7})'))
                        duplicate = call("poll" if repeated in ("poll", "batch_poll") else "origin",
                                         name=name, arguments={"cmd": "conflicting"}, number=1)
                        sequence = [duplicate, poll] if repeated == "origin" else [poll, duplicate]
                        result = [settled(0, check())] if repeated == "batch_poll" else check()
                        self.write_turn([call("past", response_id="past", number=1), usage("past", turn=turn(1)),
                            output("past", check(), number=1), origin, usage("original", turn=turn(1)),
                            output("origin", running, number=1),
                            call("independent", response_id="independent", number=1),
                            usage("independent", turn=turn(1)), output("independent", {"session_id": 8}, number=1),
                            *sequence, usage("later", turn=turn(1)), output("poll", result, number=1),
                            call("other_poll", name="write_stdin", arguments={"session_id": 8}, number=1),
                            output("other_poll", check(), number=1),
                            call("fresh", number=1), usage("fresh", turn=turn(1)), output("fresh", check(), number=1)])
                        full = self.full_and_resumed(pending=True, live_handle="session:7")
                        self.assertEqual([x.response_id for x in full.attribution if isinstance(x, ResponseBoundary)],
                                         ["past", None, "independent", "fresh"])
                        self.assertEqual(len(full.responses), 5)

    def test_independent_foreign_identity_precedes_other_helper_requirements(self):
        for mode in ("direct", "batch", "poll"):
            for kind, field, identity in (("read", "review_session_context", "project_id"),
                    ("saved", "review_session", "project_id"), ("saved", "review_session", "session_id"),
                    ("review_wait_ended", "review_wait", "project_id"),
                    ("review_wait_ended", "review_wait", "session_id"), ("command", None, "project_id")):
                foreign = "foreign-project" if identity == "project_id" else turn(99)
                local = "fixture-project" if identity == "project_id" else THREAD
                for value in (foreign, local, None, [], "", "x" * 201, True):
                    for reverse in (False, True):
                        with self.subTest(mode=mode, kind=kind, identity=identity, value=value, reverse=reverse):
                            metadata = {identity: value}
                            if kind == "read":
                                helper = {"context_check": "matched_at_read", field: metadata}
                            elif kind == "command":
                                helper = {"command": [], **metadata}
                            else:
                                helper = {"ok": True, "status": kind, field: metadata}
                            projected = project_body(helper, THREAD, turn(1), "fixture-project")
                            self.assertEqual(projected.unreadable, value != foreign)
                            self.assertFalse(projected.observations)
                            self.assertFalse(projected.terminal)
                            blocks = [{"type": "text", "text": json.dumps(item)} for item in (helper, acknowledgement())]
                            self.body_source(mode, blocks[::-1] if reverse else blocks)
                            full = self.full_and_resumed(pending=value != foreign,
                                live_handle="session:7" if mode == "poll" and value != foreign else None)
                            self.assertEqual(sum(isinstance(x, OperationObservation) for x in full.attribution), 1)
                            self.assertEqual(len(full.responses), 2)

    def test_lost_call_turn_or_name_cannot_leave_an_old_bracket_open(self):
        for mode in ("direct", "batch"):
            for kind in ("function_call", "custom_tool_call"):
                for damage in ("metadata", "turn_missing", "turn_invalid", "name_missing", "name_invalid", "name_other"):
                    with self.subTest(mode=mode, kind=kind, damage=damage):
                        arguments = batch_input('tools.exec_command({cmd:"one"})') if mode == "batch" else {"cmd": "one"}
                        entry = call("one", name="exec" if mode == "batch" else "exec_command",
                                     arguments=arguments, number=1)
                        bad = json.loads(json.dumps(entry))
                        bad["payload"]["type"] = kind
                        if damage == "metadata":
                            bad["payload"].pop("internal_chat_message_metadata_passthrough")
                        elif damage == "turn_missing":
                            bad["payload"]["internal_chat_message_metadata_passthrough"].pop("turn_id")
                        elif damage == "turn_invalid":
                            bad["payload"]["internal_chat_message_metadata_passthrough"]["turn_id"] = []
                        elif damage == "name_missing":
                            bad["payload"].pop("name")
                        else:
                            bad["payload"]["name"] = [] if damage == "name_invalid" else "unrelated-tool"
                        body = [settled(0, check())] if mode == "batch" else check()
                        self.write_turn([call("past", response_id="past", number=1), usage("past", turn=turn(1)),
                            output("past", check(), number=1), entry, bad, usage("later", turn=turn(1)),
                            output("one", body, number=1), call("fresh", number=1), usage("fresh", turn=turn(1)),
                            output("fresh", check(), number=1)])
                        full = self.full_and_resumed()
                        self.assertEqual([x.response_id for x in full.attribution if isinstance(x, ResponseBoundary)],
                                         ["past", None, "fresh"])
                        self.assertEqual(len(full.responses), 3)

    def test_complete_transport_admission_is_shared_with_retained_turn_reads(self):
        valid = acknowledgement()
        raw = json.dumps(valid).replace('"ok": true', '"ok": false, "ok": true')
        cases = ("RAW_ACK", raw, json.dumps(valid) + ' {"unfinished":',
                 [{"type": "text", "text": json.dumps(valid), "extra": True}],
                 {"content": [{"type": "text", "text": raw}]})
        for response_relations in (False, True):
            for value in cases:
                for sibling in (False, True):
                    with self.subTest(response_relations=response_relations, value=value, sibling=sibling):
                        body = {"output": value}
                        if sibling:
                            body["content"] = [{"type": "text", "text": json.dumps(valid)}]
                        self.write_turn([usage("observed", turn=turn(1)), output("one", body, number=1)])
                        self.source.path.write_bytes(self.source.path.read_bytes().replace(b'"RAW_ACK"', raw.encode()))
                        full = self.full_and_resumed(response_relations=response_relations)
                        self.assertEqual(sum(isinstance(x, OperationObservation) for x in full.attribution), int(sibling))
                        self.assertEqual(len(full.responses), 1)

    def test_malformed_helper_leaves_preserve_siblings_without_terminal_proof(self):
        wait = marker()
        metadata = {**wait["review_wait"], "original_result_digest": "a" * 64}
        metadata.pop("artifact_manifest_id")
        saved = {"ok": True, "status": "saved", "review_session": metadata}
        read = {"context_check": "matched_at_read", "review_session_context": metadata,
                "task": {"task_id": metadata["task_id"]}, "contract": {"revision": 1},
                "review_target": metadata["review_target"]}
        cases = [{"command": []}, {"command": ""}, {"command": "unknown"}, {"status": "saved"},
                 {"ok": True, "status": "review_wait_ended"}, {"context_check": "matched_at_read"}]
        for original, field in ((wait, "review_wait"), (saved, "review_session"), (read, "review_session_context")):
            for key in ("project_id", "execution_id", "version"):
                bad = json.loads(json.dumps(original))
                bad[field].pop(key)
                cases.append(bad)
        for mode in ("direct", "batch", "poll"):
            for bad in cases:
                for reverse in (False, True):
                    with self.subTest(mode=mode, bad=bad, reverse=reverse):
                        blocks = [{"type": "text", "text": json.dumps(value)}
                                  for value in (bad, acknowledgement())]
                        self.body_source(mode, blocks[::-1] if reverse else blocks)
                        full = self.full_and_resumed(pending=True,
                            live_handle="session:7" if mode == "poll" else None)
                        self.assertEqual(sum(isinstance(x, OperationObservation) for x in full.attribution), 1)
                        self.assertEqual(len(full.responses), 2)
        for helper in (wait, saved, read):
            for mode in ("direct", "batch", "poll"):
                with self.subTest(valid_helper=helper, mode=mode):
                    self.body_source(mode, helper)
                    self.full_and_resumed(pending=False)
            # An explicitly identified foreign project is irrelevant; unlike a
            # malformed helper it cannot veto a separate validated success.
            foreign = json.loads(json.dumps(helper))
            field = next(key for key in ("review_wait", "review_session", "review_session_context") if key in foreign)
            foreign[field]["project_id"] = "foreign-project"
            blocks = [{"type": "text", "text": json.dumps(value)} for value in (foreign, acknowledgement())]
            self.body_source("poll", blocks)
            self.full_and_resumed(pending=False)

    def test_first_result_cannot_be_skipped_by_source_damage(self):
        for mode in ("direct", "batch", "poll"):
            for kind in ("duplicate_payload", "duplicate_record", "duplicate_metadata", "missing_call", "invalid_call", "missing_kind", "empty_kind", "invalid_kind", "missing_payload",
                         "missing_turn", "missing_record_kind", "syntax", "opaque", "unrelated", "message"):
                with self.subTest(mode=mode, kind=kind):
                    self.source_case(mode, kind)
                    full = self.full_and_resumed()
                    boundaries = [x for x in full.attribution if isinstance(x, ResponseBoundary)]
                    self.assertEqual(len(boundaries), 3)
                    self.assertEqual(boundaries[0].response_id, "past-origin")
                    self.assertEqual(boundaries[-1].response_id, "fresh-origin")
                    ignored = kind in {"unrelated", "message"}
                    expected = "poll-origin" if mode == "poll" and kind in {"opaque", "unrelated", "message"} else (
                        "later" if ignored else None)
                    self.assertEqual(boundaries[1].response_id, expected)
                    self.assertEqual(len(full.responses), 4 if mode == "poll" else 3)
                    self.assertEqual("invalid_record" in full.diagnostics,
                                     kind not in {"opaque", "unrelated", "message", "missing_turn", "duplicate_metadata", "missing_call", "invalid_call"})

    def test_body_ambiguity_preserves_independent_siblings_and_numbers(self):
        duplicate = '{"command":"task.show","command":"task.show","ok":true,"project_id":"fixture-project","data":{}}'
        for bad in ({"type": [], "text": "PRIVATE"},
                    {"type": "text", "text": duplicate},
                    {"type": "text", "text": '{"output":'},
                    "RAW_OBJECT"):
            for container in ("direct", "output", "content"):
                for mode in ("direct", "poll"):
                    with self.subTest(bad=bad, container=container, mode=mode):
                        blocks = [bad, {"type": "text", "text": json.dumps(check())}]
                        body = blocks if container == "direct" else {container: blocks}
                        self.body_source(mode, body)
                        self.source.path.write_bytes(self.source.path.read_bytes().replace(
                            b'"RAW_OBJECT"', b'{"type":"text","type":"input_text","text":"PRIVATE"}'))
                        full = self.full_and_resumed(pending=True)
                        boundaries = [x for x in full.attribution if isinstance(x, ResponseBoundary)]
                        self.assertEqual([x.response_id for x in boundaries], ["original", "later"])
                        self.assertEqual(len(full.responses), 2)

    def test_duplicate_ack_is_not_last_key_wins_for_any_transport(self):
        raw = json.dumps(check()).replace('"ok": true', '"ok": false, "ok": true')
        for form in ("object", "string", "nested"):
            for mode in ("direct", "batch", "poll"):
                with self.subTest(form=form, mode=mode):
                    value = "RAW_ACK" if form == "object" else raw
                    if form == "nested":
                        value = {"content": [{"type": "text", "text": raw}]}
                    rows = []
                    entry = call("one", number=1)
                    if mode == "batch":
                        entry = call("one", arguments=batch_input('tools.exec_command({cmd:"one"})'), number=1)
                        value = {"i": 0, "result": {"status": "fulfilled", "value": value}}
                    elif mode == "poll":
                        rows = [call("origin", response_id="original", number=1),
                                output("origin", {"session_id": 7}, number=1)]
                        entry = call("one", name="write_stdin", arguments={"session_id": 7}, number=1)
                    self.write_turn([*rows, entry, usage("later", turn=turn(1)), output("one", value, number=1)])
                    self.source.path.write_bytes(self.source.path.read_bytes().replace(b'"RAW_ACK"', raw.encode()))
                    full = self.full_and_resumed()
                    self.assertFalse([x for x in full.attribution if isinstance(x, ResponseBoundary)])
                    self.assertEqual(len(full.responses), 1)

    def test_poll_input_ambiguity_cannot_borrow_a_live_origin(self):
        for arguments in ('{"session_id":8,"session_id":7}',
                          '{"session_id":7,"chars":{"x":1,"x":2}}',
                          {"session_id": "RAW_HANDLE"}):
            with self.subTest(arguments=arguments):
                self.write_turn([call("origin", response_id="original", number=1),
                    output("origin", {"session_id": 7}, number=1),
                    call("one", name="write_stdin", arguments=arguments, number=1),
                    usage("later", turn=turn(1)), output("one", check(), number=1)])
                self.source.path.write_bytes(self.source.path.read_bytes().replace(
                    b'"RAW_HANDLE"', b'{"value":8,"value":7}'))
                full = self.full_and_resumed()
                boundaries = [x for x in full.attribution if isinstance(x, ResponseBoundary)]
                self.assertEqual(len(boundaries), 1)
                self.assertIsNone(boundaries[0].response_id)

    def test_invalid_ack_identity_cannot_discard_a_valid_sibling(self):
        for bad in (check() | {"command": []}, check() | {"status": []},
                    check() | {"command": "task.edit", "data": {"changed_fields": ["status"],
                        "task": {"task_id": "invalid", "project_id": "fixture-project",
                                 "status": "in_progress", "ownership": {"generation": 1}},
                        "event": {"task_id": "invalid", "project_id": "fixture-project",
                                  "task_event_id": "invalid"}}}):
            with self.subTest(bad=bad):
                self.write_turn([call("one", number=1), usage("original", turn=turn(1)),
                    output("one", [{"type": "text", "text": json.dumps(bad)},
                                   {"type": "text", "text": json.dumps(check())}], number=1)])
                full = self.full_and_resumed()
                self.assertTrue(any(isinstance(x, ResponseBoundary) and x.command == "task.show"
                                    for x in full.attribution))

    def test_incomplete_source_tail_stays_unread_until_record_finishes(self):
        self.write_turn([call("one", number=1), usage("original", turn=turn(1))])
        prefix_size = self.source.path.stat().st_size
        record = json.dumps(output("one", check(), number=1)).encode()
        with self.source.path.open("ab") as stream:
            stream.write(record[:-4])
        full = read_batch(self.source, include_attribution=True, attribution_project_id="fixture-project",
                          include_response_relations=True)
        part = scan(self.source, ScanState(), {}, project_id="fixture-project", response_context=ResponseTracker.empty_state())
        self.assertEqual(full.pending, "partial_tail")
        self.assertEqual(part.pending, "partial_tail")
        self.assertEqual(full.successor.offset, prefix_size)
        self.assertEqual(part.state.offset, prefix_size)
        self.assertFalse(part.response_context["calls"]["one"]["bracket_closed"])
        with self.source.path.open("ab") as stream:
            stream.write(record[-4:] + b"\n")
        resumed = scan(self.source, part.state, part.models, project_id="fixture-project",
                       response_context=json.loads(json.dumps(part.response_context)))
        self.assertEqual(resumed.pending, "none")
        self.assertEqual([x.response_id for x in resumed.attribution if isinstance(x, ResponseBoundary)], ["original"])
        self.full_and_resumed()

    def test_missing_operation_identity_preserves_siblings_numbers_and_pending_lifetime(self):
        for command in ("task.edit", "task.complete", "task.add", "batch_registration"):
            for missing in ("task_id", "task_event_id", "generation", "status"):
                for mode in ("direct", "batch", "poll", "historical"):
                    with self.subTest(command=command, missing=missing, mode=mode):
                        bad = acknowledgement()
                        bad["command"] = "task.add" if command == "batch_registration" else command
                        data = bad["data"]
                        if missing == "task_id":
                            data["task"].pop("task_id")
                            data["event"].pop("task_id")
                        elif missing == "task_event_id":
                            data["event"].pop(missing)
                        elif missing == "generation":
                            data["task"]["ownership"].pop(missing)
                        else:
                            data["task"].pop(missing)
                        if command == "batch_registration":
                            bad["data"] = {"tasks": [data]}
                        body = [{"type": "text", "text": json.dumps(bad)},
                                {"type": "text", "text": json.dumps(acknowledgement())}]
                        entry = call("one", response_id="original", number=1)
                        rows = []
                        if mode == "batch":
                            entry = call("one", name="exec", response_id="original", number=1,
                                         arguments=batch_input('tools.exec_command({cmd:"one"})'))
                            body = [settled(0, body)]
                        elif mode == "poll":
                            rows = [entry, output("one", {"session_id": 7}, number=1)]
                            entry = call("poll", name="write_stdin", arguments={"session_id": 7}, number=1)
                        if mode != "historical":
                            rows.append(entry)
                        self.write_turn([*rows, usage("original", turn=turn(1)),
                            output("poll" if mode == "poll" else "one", body, number=1),
                            call("fresh", number=1), usage("later", turn=turn(1)),
                            output("fresh", check(), number=1)])
                        full = self.full_and_resumed(pending=mode != "historical")
                        self.assertEqual(sum(isinstance(x, OperationObservation) for x in full.attribution), 1)
                        boundaries = [x for x in full.attribution if isinstance(x, ResponseBoundary)]
                        self.assertEqual([(x.command, x.response_id) for x in boundaries],
                            ([] if mode == "historical" else [("task.edit", "original")]) + [("task.show", "later")])
                        self.assertEqual(len(full.responses), 2)

    def test_omitted_identities_use_validation_at_the_direct_projector_boundary(self):
        for missing in ("task_id", "project_id"):
            with self.subTest(missing=missing):
                bad = acknowledgement()
                bad["data"]["task"].pop(missing)
                bad["data"]["event"].pop(missing)
                if missing == "project_id":
                    bad.pop(missing)
                projected = project_body(bad, THREAD, turn(2))
                self.assertTrue(projected.unreadable)
                self.assertEqual(projected.observations, ())
                self.assertEqual(project_record(tool_record(bad), THREAD), ())

    def test_asymmetric_and_invalid_identities_cannot_borrow_sibling_termination(self):
        missing = object()
        cases = [(path, missing) for path in (
            ("project_id",), ("data", "task", "task_id"), ("data", "event", "task_id"),
            ("data", "task", "project_id"), ("data", "event", "project_id"),
            ("data", "task"), ("data", "event"), ("data", "task", "ownership"))]
        cases += [(path, value) for path in (("project_id",), ("data", "task", "task_id"))
                  for value in (None, [], "", "x" * 201)]
        cases += [(("data", "event", "task_id"), "tg_task_" + "f" * 16),
                  (("data", "task", "project_id"), "other-project"),
                  (("data", "event", "project_id"), "other-project")]
        for command in ("task.add", "task.edit", "task.complete"):
            for path, value in cases:
                for mode in ("direct", "batch", "poll", "historical"):
                    with self.subTest(command=command, path=path, value_type=type(value).__name__, mode=mode):
                        bad = acknowledgement()
                        bad["command"] = command
                        destination = bad
                        for key in path[:-1]:
                            destination = destination[key]
                        if value is missing:
                            destination.pop(path[-1])
                        else:
                            destination[path[-1]] = value
                        body = [{"type": "text", "text": json.dumps(bad)},
                                {"type": "text", "text": json.dumps(acknowledgement())}]
                        self.body_source(mode, body)
                        full = self.full_and_resumed(pending=mode != "historical",
                            live_handle="session:7" if mode == "poll" else None)
                        self.assertEqual(sum(isinstance(x, OperationObservation) for x in full.attribution), 1)
                        self.assertEqual(len(full.responses), 2)

    def test_same_registration_ack_preserves_valid_entries_and_incomplete_evidence(self):
        for referenced in (False, True):
            for bad_first in (False, True):
                for damage in ("missing_both_ids", "missing_one_id", "missing_project", "bad_reference", "not_an_entry"):
                    for mode in ("direct", "batch", "poll", "historical"):
                        with self.subTest(referenced=referenced, bad_first=bad_first, damage=damage, mode=mode):
                            ack, original = turn_fixtures.UsageTurnAdapterTests().registration(batch=True)
                            valid = ack["data"]["tasks"][1] if referenced else original
                            bad = acknowledgement()["data"]
                            if damage == "missing_both_ids":
                                bad["task"].pop("task_id")
                                bad["event"].pop("task_id")
                            elif damage == "missing_one_id":
                                bad["event"].pop("task_id")
                            elif damage == "missing_project":
                                bad["task"].pop("project_id")
                            elif damage == "bad_reference":
                                bad["task"] = {"task_id": bad["task"]["task_id"], "task_ref": "/unrelated"}
                            else:
                                bad = None
                            ack["data"]["tasks"] = [bad, valid] if bad_first else [valid, bad]
                            projected = project_body(ack, THREAD, turn(1), "fixture-project")
                            self.assertTrue(projected.unreadable)
                            self.assertEqual(len(projected.observations), 1)
                            self.assertEqual(len(project_record(tool_record(ack), THREAD)), 1)
                            self.body_source(mode, ack)
                            full = self.full_and_resumed(pending=mode != "historical",
                                live_handle="session:7" if mode == "poll" else None)
                            self.assertEqual(sum(isinstance(x, OperationObservation) for x in full.attribution), 1)
                            self.assertEqual(len(full.responses), 2)

    def test_irrelevant_acks_and_independent_process_exit_do_not_keep_lifetimes_pending(self):
        foreign, metadata, ready, failed, malformed = (acknowledgement() for _ in range(5))
        foreign["project_id"] = "foreign-project"
        metadata["data"]["changed_fields"] = ["verification"]
        ready["command"], ready["data"]["task"]["status"] = "task.add", "ready"
        failed["ok"] = False
        malformed["data"]["event"].pop("task_id")
        for bad, process in ((foreign, False), (metadata, False), (ready, False), (failed, False), (malformed, True)):
            for mode in ("direct", "batch", "poll"):
                with self.subTest(mode=mode, process=process, command=bad["command"]):
                    body = [{"type": "text", "text": json.dumps(bad)},
                            {"type": "text", "text": json.dumps(acknowledgement())}]
                    self.body_source(mode, body, process=process)
                    full = self.full_and_resumed(pending=False)
                    self.assertEqual(sum(isinstance(x, OperationObservation) for x in full.attribution), 1)
                    self.assertEqual(len(full.responses), 2)


class SourceReplayTests(_SourceFixture, unittest.TestCase):
    append = fixtures.TerminalRepositoryTests.append
    reader = fixtures.TerminalRepositoryTests.reader
    acknowledgement = fixtures.TerminalRepositoryTests.acknowledgement
    operation = fixtures.TerminalRepositoryTests.operation
    read = fixtures.TerminalRepositoryTests.read
    ingest = fixtures.TerminalRepositoryTests.ingest
    restart = fixtures.TerminalRepositoryTests.restart
    relations = fixtures.TerminalRepositoryTests.relations
    context = fixtures.TerminalRepositoryTests.context

    def test_v14_replay_preserves_originals_and_history_without_read_mutation(self):
        import task_governance_tool.usage_adapter as source_adapter
        import task_governance_tool.usage_response_adapter as adapter
        from tests.test_usage_response_repository import A
        self.source_case("direct", "duplicate_payload")
        self.append(*self.operation("start", self.acknowledgement(A, 1, "in_progress"), "start-origin"))
        self.append(*self.operation("done", self.acknowledgement(A, 1, "done", cycle="A"), "done-origin"))
        # Reproduce only v14's silent identity loss through the real file/DB
        # pipeline; do not manufacture or patch saved relation/cursor rows.
        def legacy_payload(record):
            payload = record.get("payload")
            return payload if record.get("type") == "response_item" and isinstance(payload, dict) else None
        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v14"), \
                mock.patch.object(source_adapter, "response_payload", side_effect=legacy_payload), \
                mock.patch.object(adapter, "response_payload", side_effect=legacy_payload):
            self.ingest(1)
        self.assertTrue(any(x.call_id == "one" and x.response_id == "later" for x in self.relations()[1]))
        old_models = self.repository.summary()["models"]
        old_cursor = self.repository.cursor(self.source.source_id, THREAD)
        self.repository.refresh(self.reader)
        old_documents = {x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}
        self.assertTrue(old_documents, "history-preservation assertion must cover actual snapshots")
        saved_source = self.source.path.read_bytes()
        source_identity = self.source.path.stat().st_ino
        retained_source = self.source.path.with_name("temporarily-unavailable.jsonl")
        self.source.path.rename(retained_source)
        before = self.repository.path.read_bytes()
        self.read(audit=True)
        self.assertEqual(self.repository.path.read_bytes(), before)
        with self.assertRaises(UsageError):
            self.repository.collect_source(self.source, lane="audit", max_records=1)
        self.assertEqual(self.repository.path.read_bytes(), before)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), old_cursor)
        retained_source.rename(self.source.path)
        self.assertEqual(self.source.path.stat().st_ino, source_identity)
        for _ in range(len(saved_source.splitlines()) + 2):
            result = self.repository.collect_source(self.source, lane="audit", max_records=1)
            self.assertLessEqual(result["records"], 1)
            self.restart()
            if result.get("replay"):
                break
        else:
            self.fail("v14 source interpretation did not schedule bounded replay")
        self.repository.collect_source(self.source, max_records=1)
        self.ingest(1)
        self.assertTrue(any(x.call_id == "one" and x.response_id is None for x in self.relations()[1]))
        self.assertFalse(any(x.call_id == "one" and x.response_id == "later" for x in self.relations()[1]))
        self.assertEqual(self.repository.summary()["models"], old_models)
        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, old_cursor.incarnation)
        self.repository.refresh(self.reader)
        self.assertTrue(old_documents.items() <= {
            x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}.items())
        self.repository.collect_source(self.source)
        self.assertEqual(self.repository.summary()["models"], old_models)


if __name__ == "__main__":
    unittest.main()
