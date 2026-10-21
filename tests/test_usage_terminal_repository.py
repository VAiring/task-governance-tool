"""Finished opaque stdout through full, resumed and old-codec collection."""
import json
import unittest
from unittest import mock

from tests import test_usage_response_repository as fixtures
from tests.test_usage_collection import THREAD, usage
from tests.test_usage_attribution import turn
from tests.test_usage_response_adapter import call, output, batch_input, settled
from tests.test_usage_terminal_transport import envelope, wrapped, check
from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_response_adapter import ResponseBoundary, ResponsePosition
from task_governance_tool.usage_response_repository import UsageResponseRepository


class TerminalRepositoryTests(unittest.TestCase):
    setUp = fixtures.ResponseRepositoryTests.setUp
    records = fixtures.ResponseRepositoryTests.records
    write = fixtures.ResponseRepositoryTests.write
    append = fixtures.ResponseRepositoryTests.append
    reader = fixtures.ResponseRepositoryTests.reader
    acknowledgement = fixtures.ResponseRepositoryTests.acknowledgement
    operation = fixtures.ResponseRepositoryTests.operation
    write_turn = fixtures.ResponseRepositoryTests.write_turn
    read = fixtures.ResponseRepositoryTests.read

    def restart(self):
        self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)

    def ingest(self, limit):
        for _ in range(len(self.source.path.read_bytes().splitlines()) + 2):
            if self.repository.cursor(self.source.source_id, THREAD).offset >= self.source.path.stat().st_size:
                return
            self.repository.collect_source(self.source, max_records=limit)
            self.restart()
        self.fail("fixture ingestion did not terminate")

    def prefix(self, count, mode="wrapper", body="PRIVATE_PLAIN_STDOUT"):
        rows = []
        for index in range(count):
            identity = f"plain-{index}"
            value = envelope(body, index % 2)
            entry = call(identity, number=1)
            if mode == "direct":
                entry = call(identity, name="exec_command", arguments={"cmd": "PRIVATE_CMD"}, number=1)
            elif mode == "batch":
                entry = call(identity, arguments=batch_input('tools.exec_command({cmd:"PRIVATE_CMD"})'), number=1)
                value = [settled(0, value)]
            elif mode == "poll":
                # Distinct lifetimes, including retirement at the fixed handle bound.
                rows += [entry, usage(f"r{index}", turn=turn(1)),
                         output(identity, {"session_id": index}, number=1)]
                identity += "-poll"
                entry = call(identity, name="write_stdin", arguments={"session_id": index}, number=1)
            else:
                value = wrapped(value)
            response_id = f"poll-r{index}" if mode == "poll" else f"r{index}"
            rows += [entry, usage(response_id, turn=turn(1)), output(identity, value, number=1)]
        return rows

    def finish(self, rows):
        rows += self.operation("check", check(), "check-origin")
        rows += self.operation("start", self.acknowledgement(fixtures.A, 1, "in_progress"), "start-origin")
        rows += self.operation("done", self.acknowledgement(fixtures.A, 1, "done", cycle="A"), "done-origin")
        self.write_turn(rows)

    def relations(self):
        with self.repository.connection() as connection:
            positions, boundaries = self.repository._relations(connection)
            return {x for _, _, x in positions}, {x for _, _, x in boundaries}

    def context(self):
        with self.repository.connection() as connection:
            return json.loads(connection.execute(
                "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])

    def test_capacity_boundaries_full_resumed_and_reingested_match(self):
        for count in (127, 128, 129):
            for mode in ("direct", "wrapper", "batch", "poll"):
                for limit in (37, 10000):
                    with self.subTest(count=count, mode=mode, limit=limit):
                        self.setUp()
                        self.finish(self.prefix(count, mode))
                        full = read_batch(self.source, include_attribution=True,
                                          attribution_project_id=self.repository.basis[0], include_response_relations=True)
                        expected = ({x for x in full.attribution if isinstance(x, ResponsePosition)},
                                    {x for x in full.attribution if isinstance(x, ResponseBoundary)})
                        self.ingest(limit)
                        self.assertEqual(self.relations(), expected)
                        found = {x.command: x for x in expected[1]}
                        self.assertEqual(found["task.show"].response_id, "check-origin")
                        self.repository.refresh(self.reader)
                        self.assertEqual(self.read(fixtures.A)["total"]["response_count"], 3)
                        self.assertEqual(self.repository.summary()["models"][0]["response_count"],
                                         count * (2 if mode == "poll" else 1) + 3)
                        self.assertFalse(self.read()["unattributed"]["unknown"]["response_count"])
                        state = self.context()
                        self.assertFalse(state["calls"] or state["batches"] or state["handles"])
                        self.assertNotIn("PRIVATE", json.dumps(state))
                        self.assertNotIn("PRIVATE", str(self.relations()))
                        before = self.repository.summary()["models"]
                        self.repository.collect_source(self.source)
                        self.assertEqual(self.repository.summary()["models"], before)

    def test_empty_and_json_controls_across_single_record_restart(self):
        for body in ("", "{}"):
            with self.subTest(body=body):
                self.setUp()
                self.finish(self.prefix(129, body=body))
                self.ingest(1)
                self.assertFalse(self.context()["calls"])
                self.repository.refresh(self.reader)
                self.assertEqual(self.read(fixtures.A)["total"]["response_count"], 3)

    def test_v8_pending_and_lost_origin_recover_only_after_source_replay(self):
        import task_governance_tool.usage_response_adapter as adapter
        self.finish(self.prefix(129))
        # Reproduce v8's missing opaque-stdout termination with its real prefix.
        # It accepts the later valid Taskgov replies, but capacity has already
        # prevented tracking those calls. No saved state is manually repaired.
        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v8"), \
                mock.patch.object(adapter, "_lifetime_evidence", return_value="unresolved"):
            self.ingest(37)
        self.assertEqual(len(self.context()["calls"]), 128)
        self.assertTrue(self.context()["limited"])
        self.assertFalse(self.relations()[1])
        self.repository.refresh(self.reader)
        old_documents = {x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}
        old_models = self.repository.summary()["models"]
        old_cursor = self.repository.cursor(self.source.source_id, THREAD)
        self.restart()
        # Loading the new codec alone neither deletes old pending frames nor fixes lost origins.
        self.assertTrue(self.context()["limited"])
        self.assertFalse(self.relations()[1])
        for _ in range(len(self.source.path.read_bytes().splitlines()) + 1):
            result = self.repository.collect_source(self.source, lane="audit", max_records=7)
            self.assertLessEqual(result["records"], 7)
            self.restart()
            if result.get("replay"):
                break
        else:
            self.fail("v8 did not schedule bounded replay")
        self.repository.collect_source(self.source, max_records=1)
        self.ingest(37)
        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, old_cursor.incarnation)
        self.assertFalse(self.context()["limited"])
        self.assertFalse(self.context()["calls"])
        self.repository.refresh(self.reader)
        self.assertEqual(self.repository.summary()["models"], old_models)
        self.assertEqual(self.read(fixtures.A)["total"]["response_count"], 3)
        self.assertFalse(self.read()["unattributed"]["unknown"]["response_count"])
        self.assertTrue(old_documents.items() <= {
            x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}.items())

    def test_prior_terminal_codec_replays_conflicting_header_as_unresolved(self):
        import task_governance_tool.usage_response_adapter as adapter
        invalid = "Chunk ID: first\nChunk ID: second\nProcess exited with code 0\nOutput:\nplain"
        self.finish([call("conflicting", number=1), usage("r0", turn=turn(1)),
                     output("conflicting", invalid, number=1)])
        # Version 9 admitted this conflicting header as terminal. Its semantic
        # prefix must not certify the corrected lifetime interpretation.
        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v9"), \
                mock.patch.object(adapter, "_header_fields", return_value=(["Process exited with code 0"], [])):
            self.ingest(1)
        self.assertFalse(self.context()["calls"])
        self.repository.refresh(self.reader)
        old_models = self.repository.summary()["models"]
        old_documents = {x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}
        old_cursor = self.repository.cursor(self.source.source_id, THREAD)
        for _ in range(len(self.source.path.read_bytes().splitlines()) + 1):
            result = self.repository.collect_source(self.source, lane="audit", max_records=1)
            self.restart()
            if result.get("replay"):
                break
        else:
            self.fail("prior terminal codec did not schedule replay")
        self.repository.collect_source(self.source, max_records=1)
        self.ingest(1)
        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, old_cursor.incarnation)
        self.assertIn("conflicting", self.context()["calls"])
        self.repository.refresh(self.reader)
        self.assertEqual(self.repository.summary()["models"], old_models)
        self.assertTrue(old_documents.items() <= {
            x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}.items())

    def test_conflicting_ack_full_incremental_and_prior_codec_replay(self):
        import task_governance_tool.usage_response_adapter as adapter
        for prior in (False, True):
            with self.subTest(prior=prior):
                self.setUp()
                conflicting = envelope(json.dumps(check())) | {"session_id": 7}
                self.finish([call("conflicting", number=1), usage("r0", turn=turn(1)),
                             output("conflicting", conflicting, number=1)])
                full = read_batch(self.source, include_attribution=True,
                                  attribution_project_id=self.repository.basis[0], include_response_relations=True)
                expected = ({x for x in full.attribution if isinstance(x, ResponsePosition)},
                            {x for x in full.attribution if isinstance(x, ResponseBoundary)})
                if prior:
                    current = adapter._lifetime_evidence
                    def old_gate(value, *, depth=0):
                        if isinstance(value, dict) and value.get("session_id") == 7 and value.get("exit_code") == 0:
                            return "body"
                        return current(value, depth=depth)
                    with mock.patch.object(adapter, "VERSION", "codex-response-relations-v10"), \
                            mock.patch.object(adapter, "_lifetime_evidence", side_effect=old_gate):
                        self.ingest(1)
                    self.assertNotIn("conflicting", self.context()["calls"])
                    old_models = self.repository.summary()["models"]
                    old_cursor = self.repository.cursor(self.source.source_id, THREAD)
                    for _ in range(len(self.source.path.read_bytes().splitlines()) + 1):
                        result = self.repository.collect_source(self.source, lane="audit", max_records=1)
                        self.restart()
                        if result.get("replay"):
                            break
                    else:
                        self.fail("prior body fallback did not schedule replay")
                    self.repository.collect_source(self.source, max_records=1)
                self.ingest(1)
                self.assertEqual(self.relations(), expected)
                self.assertIn("conflicting", self.context()["calls"])
                self.assertFalse(self.context()["handles"])
                before = self.repository.summary()["models"]
                self.repository.collect_source(self.source)
                self.assertEqual(self.repository.summary()["models"], before)
                if prior:
                    self.assertEqual(before, old_models)
                    self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, old_cursor.incarnation)

    def test_incomplete_and_mixed_transport_full_sliced_and_v11_replay(self):
        import task_governance_tool.usage_response_adapter as adapter
        for mixed in (False, True):
            for batch in (False, True):
                for prior in (False, True):
                    with self.subTest(mixed=mixed, batch=batch, prior=prior):
                        self.setUp()
                        incomplete = {"chunk_id": "fixture", "output": '{"session_id":7}'}
                        value = check() | {"content": json.dumps(incomplete)} if mixed else incomplete
                        arguments = batch_input('tools.exec_command({cmd:"one"})') if batch else 'text(await tools.exec_command({cmd:"one"}));'
                        rows = [call("incomplete", arguments=arguments, number=1), usage("original", turn=turn(1)),
                                output("incomplete", [settled(0, value)] if batch else value, number=1),
                                call("poll", name="write_stdin", arguments={"session_id": 7}, number=1),
                                usage("poll-response", turn=turn(1)), output("poll", check(), number=1)]
                        self.finish(rows)
                        full = read_batch(self.source, include_attribution=True,
                                          attribution_project_id=self.repository.basis[0], include_response_relations=True)
                        expected = ({x for x in full.attribution if isinstance(x, ResponsePosition)},
                                    {x for x in full.attribution if isinstance(x, ResponseBoundary)})
                        if prior:
                            current_kind, current_evidence = adapter._process_kind, adapter._lifetime_evidence
                            # Model the two v11 defects through the real collector;
                            # never repair stored continuation or relation rows directly.
                            def old_kind(item):
                                if "session_id" not in item and "exit_code" not in item:
                                    return None
                                return current_kind(item)
                            def old_evidence(value, *, depth=0):
                                if mixed and isinstance(value, dict) and value.get("command") == "task.show":
                                    return "body"
                                return current_evidence(value, depth=depth)
                            with mock.patch.object(adapter, "VERSION", "codex-response-relations-v11"), \
                                    mock.patch.object(adapter, "_process_kind", side_effect=old_kind), \
                                    mock.patch.object(adapter, "_lifetime_evidence", side_effect=old_evidence):
                                self.ingest(1)
                            self.assertFalse(self.context()["calls"] or self.context()["batches"])
                            old_models = self.repository.summary()["models"]
                            old_cursor = self.repository.cursor(self.source.source_id, THREAD)
                            for _ in range(len(self.source.path.read_bytes().splitlines()) + 1):
                                result = self.repository.collect_source(self.source, lane="audit", max_records=1)
                                self.restart()
                                if result.get("replay"):
                                    break
                            else:
                                self.fail("v11 interpretation did not schedule replay")
                            self.repository.collect_source(self.source, max_records=1)
                        self.ingest(1 if prior else 10000)
                        self.assertEqual(self.relations(), expected)
                        state = self.context()
                        self.assertTrue(state["calls"] or state["batches"])
                        self.assertFalse(state["handles"])
                        poll_boundaries = [x for x in expected[1] if x.call_id == "poll"]
                        self.assertEqual(len(poll_boundaries), 1)
                        self.assertIsNone(poll_boundaries[0].response_id)
                        before = self.repository.summary()["models"]
                        self.repository.collect_source(self.source)
                        self.assertEqual(self.repository.summary()["models"], before)
                        if prior:
                            self.assertEqual(before, old_models)
                            self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, old_cursor.incarnation)


    def test_outer_batch_and_duplicate_metadata_full_sliced_and_v12_replay(self):
        import task_governance_tool.usage_response_adapter as adapter
        import task_governance_tool.usage_batch_adapter as batch_adapter
        for batch in (False, True):
            for prior in (False, True):
                with self.subTest(batch=batch, prior=prior):
                    self.setUp()
                    value = (envelope(settled(0, {"session_id": 7})["text"]) if batch else
                             '{"exit_code":0,"exit_code":null,"session_id":7}')
                    arguments = batch_input('tools.exec_command({cmd:"one"})') if batch else 'text(await tools.exec_command({cmd:"one"}));'
                    self.finish([call("one", arguments=arguments, number=1), usage("original", turn=turn(1)),
                                 output("one", value, number=1),
                                 call("poll", name="write_stdin", arguments={"session_id": 7}, number=1),
                                 usage("poll-response", turn=turn(1)), output("poll", check(), number=1)])
                    full = read_batch(self.source, include_attribution=True,
                                      attribution_project_id=self.repository.basis[0], include_response_relations=True)
                    expected = ({x for x in full.attribution if isinstance(x, ResponsePosition)},
                                {x for x in full.attribution if isinstance(x, ResponseBoundary)})
                    if prior:
                        current_text = adapter._text_json
                        def prior_text(value, **kwargs):
                            # The v12 ordinary handle decoder used last-key-wins;
                            # its batch router also admitted process stdout.
                            return current_text(value, unique_keys=False, allow_header=kwargs.get("allow_header", True))
                        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v12"), \
                                mock.patch.object(adapter, "_text_json", side_effect=prior_text), \
                                mock.patch.object(batch_adapter, "_CONTAINER_FIELDS",
                                                  batch_adapter._CONTAINER_FIELDS | adapter._PROCESS_MARKERS):
                            self.ingest(1)
                        self.assertFalse(self.context()["calls"] or self.context()["batches"])
                        self.assertTrue(any(x.call_id == "one" and x.response_id == "original" for x in self.relations()[1]))
                        old_models = self.repository.summary()["models"]
                        old_cursor = self.repository.cursor(self.source.source_id, THREAD)
                        self.repository.refresh(self.reader)
                        old_documents = {x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}
                        for _ in range(len(self.source.path.read_bytes().splitlines()) + 1):
                            result = self.repository.collect_source(self.source, lane="audit", max_records=1)
                            self.restart()
                            if result.get("replay"):
                                break
                        else:
                            self.fail("v12 interpretation did not schedule replay")
                        self.repository.collect_source(self.source, max_records=1)
                    self.ingest(1 if prior else 10000)
                    self.assertEqual(self.relations(), expected)
                    state = self.context()
                    self.assertTrue(state["calls"] or state["batches"])
                    self.assertFalse(state["handles"])
                    self.assertEqual([x.response_id for x in expected[1] if x.call_id == "poll"], [None])
                    if prior:
                        self.assertEqual(self.repository.summary()["models"], old_models)
                        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, old_cursor.incarnation)
                        self.repository.refresh(self.reader)
                        self.assertTrue(old_documents.items() <= {
                            x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}.items())


    def test_source_shape_fences_match_full_and_single_record_collection(self):
        for kind in ("hidden_index", "unknown_block", "duplicate_source"):
            for limit in (1, 10000):
                with self.subTest(kind=kind, limit=limit):
                    self.setUp()
                    value = (envelope(settled(0, check())["text"]) if kind == "hidden_index" else
                             [{"type": "text", "text": '{"session_id":7}', "exit_code": 0}]
                             if kind == "unknown_block" else "RAW_OBJECT")
                    self.finish([call("one", number=1), usage("original", turn=turn(1)),
                                 output("one", value, number=1),
                                 call("poll", name="write_stdin", arguments={"session_id": 7}, number=1),
                                 usage("poll-response", turn=turn(1)), output("poll", check(), number=1)])
                    if kind == "duplicate_source":
                        self.source.path.write_bytes(self.source.path.read_bytes().replace(
                            b'"RAW_OBJECT"', b'{"exit_code":0,"exit_code":null,"session_id":7}'))
                    full = read_batch(self.source, include_attribution=True,
                                      attribution_project_id=self.repository.basis[0], include_response_relations=True)
                    expected = ({x for x in full.attribution if isinstance(x, ResponsePosition)},
                                {x for x in full.attribution if isinstance(x, ResponseBoundary)})
                    self.ingest(limit)
                    self.assertEqual(self.relations(), expected)
                    self.assertFalse(self.context()["handles"])
                    self.assertEqual(bool(self.context()["calls"]), kind != "hidden_index")
                    self.assertTrue(all(x.response_id is None for x in expected[1] if x.call_id in {"one", "poll"}))
                    self.assertEqual(self.repository.summary()["models"][0]["response_count"], 5)
                    before = self.repository.summary()["models"]
                    self.repository.collect_source(self.source)
                    self.assertEqual(self.repository.summary()["models"], before)

    def test_duplicate_source_v13_replay_preserves_counters_and_history(self):
        import task_governance_tool.usage_adapter as source_adapter
        import task_governance_tool.usage_incremental_adapter as incremental
        import task_governance_tool.usage_response_adapter as adapter
        self.finish([call("one", number=1), usage("original", turn=turn(1)),
                     output("one", "RAW_OBJECT", number=1),
                     call("poll", name="write_stdin", arguments={"session_id": 7}, number=1),
                     usage("poll-response", turn=turn(1)), output("poll", check(), number=1)])
        self.source.path.write_bytes(self.source.path.read_bytes().replace(
            b'"RAW_OBJECT"', b'{"exit_code":0,"exit_code":null,"session_id":7}'))
        full = read_batch(self.source, include_attribution=True,
                          attribution_project_id=self.repository.basis[0], include_response_relations=True)
        expected = ({x for x in full.attribution if isinstance(x, ResponsePosition)},
                    {x for x in full.attribution if isinstance(x, ResponseBoundary)})
        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v13"), \
                mock.patch.object(source_adapter, "_json", side_effect=json.loads), \
                mock.patch.object(incremental, "_json", side_effect=json.loads):
            self.ingest(1)
        self.assertFalse(self.context()["calls"] or self.context()["handles"])
        self.assertTrue(any(x.call_id == "one" and x.response_id == "original" for x in self.relations()[1]))
        old_models = self.repository.summary()["models"]
        old_cursor = self.repository.cursor(self.source.source_id, THREAD)
        self.repository.refresh(self.reader)
        old_documents = {x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}
        for _ in range(len(self.source.path.read_bytes().splitlines()) + 1):
            result = self.repository.collect_source(self.source, lane="audit", max_records=1)
            self.restart()
            if result.get("replay"):
                break
        else:
            self.fail("v13 source interpretation did not schedule replay")
        self.repository.collect_source(self.source, max_records=1)
        self.ingest(1)
        self.assertEqual(self.relations(), expected)
        self.assertTrue(self.context()["calls"])
        self.assertFalse(self.context()["handles"])
        self.assertEqual(self.repository.summary()["models"], old_models)
        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, old_cursor.incarnation)
        self.repository.refresh(self.reader)
        self.assertTrue(old_documents.items() <= {
            x["snapshot_id"]: x for x in self.read(audit=True)["snapshots"]}.items())


if __name__ == "__main__":
    unittest.main()
