"""Positive call-origin admission, unknown success, and versioned replay."""

from dataclasses import asdict
import json
import unittest
from unittest import mock

from tests import test_usage_response_repository as fixtures
from tests.test_usage_response_adapter import call, output, response, batch_input, settled
from tests.test_usage_turn_adapter import acknowledgement, THREAD
from task_governance_tool import usage_response_adapter as adapter
from task_governance_tool.usage_turn_adapter import OperationObservation
from task_governance_tool.usage_values import UsageError


UNSUPPORTED = (
    'text(await tools . write_stdin({session_id:7}));',
    'text(await tools["write_stdin"]({session_id:7}));',
    'text(await globalThis["tools"]["write_stdin"]({session_id:7}));',
    'const poll=tools.write_stdin;text(await poll({session_id:7}));',
    'text(await tools.write_stdin({session_id:handle}));',
    'text(await tools.exec_command(args));',
    'text(await tools.exec_command({cmd:"one"}));text(await tools.write_stdin({session_id:7}));',
    '',
)


class OriginAdmissionTests(unittest.TestCase):
    def pragma_result(self, prefix, source, batch):
        tracker = adapter.ResponseTracker(THREAD, "fixture")
        tracker.consume(call("origin", response_id="original"), 10, None, "fixture-project")
        tracker.consume(output("origin", {"session_id": 7}), 20, None, "fixture-project")
        tracker.consume(call("candidate", arguments=prefix + source, number=3,
                             response_id="receiver"), 30, None, "fixture-project")
        tracker = adapter.ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
        result = [settled(0, acknowledgement())] if batch else acknowledgement()
        found = tracker.consume(output("candidate", result, number=3), 40, None, "fixture-project")
        operations = [item for item in found if isinstance(item, OperationObservation)]
        boundaries = [item for item in found if isinstance(item, adapter.ResponseBoundary)]
        self.assertEqual(len(operations), 1)
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0].event_id, operations[0].event_id)
        return boundaries[0], tracker.state

    def test_pragma_cannot_hide_executable_code_after_any_js_line_terminator(self):
        for separator in ("\n", "\r", "\u2028", "\u2029"):
            for source, batch in (
                    ('text(await tools.exec_command({cmd:"fixture"}));', False),
                    ('text(await tools.write_stdin({session_id:7}));', False),
                    (batch_input('tools.exec_command({cmd:"fixture"})'), True),
                    (batch_input('tools.write_stdin({session_id:7})'), True)):
                with self.subTest(separator=repr(separator), source=source):
                    prefix = '// @exec: {' + separator + 'text(await tools["write_stdin"]({session_id:7})); // }\n'
                    boundary, state = self.pragma_result(prefix, source, batch)
                    self.assertEqual((boundary.response_id, boundary.method), (None, "unavailable"))
                    self.assertIn("session:7", state["handles"])
                    self.assertNotIn("session:7", state["retired"])

    def test_supported_pragma_preserves_single_and_batch_origins(self):
        for ending in ("\n", "\r\n"):
            for source, batch, expected in (
                    ('text(await tools.exec_command({cmd:"fixture"}));', False, "receiver"),
                    ('text(await tools.write_stdin({session_id:7}));', False, "original"),
                    (batch_input('tools.exec_command({cmd:"fixture"})'), True, "receiver"),
                    (batch_input('tools.write_stdin({session_id:7})'), True, "original")):
                with self.subTest(ending=repr(ending), source=source):
                    boundary, _ = self.pragma_result('// @exec: {"yield_time_ms": 10000}' + ending, source, batch)
                    self.assertEqual((boundary.response_id, boundary.method), (expected, "explicit"))

    def test_unrecognized_call_preserves_success_without_borrowing_receiver_response(self):
        for source in UNSUPPORTED:
            for explicit in (False, True):
                for restart in (False, True):
                    with self.subTest(source=source, explicit=explicit, restart=restart):
                        tracker = adapter.ResponseTracker(THREAD, "fixture")
                        feed = lambda record, order, numerical=None: tracker.consume(record, order, numerical, "fixture-project")
                        feed(call("origin", response_id="original"), 10)
                        feed(output("origin", {"session_id": 7}), 20)
                        feed(call("unsupported", arguments=source, number=3,
                                  response_id="receiver" if explicit else None), 30)
                        feed({}, 40, response("receiver", number=3))
                        if restart:
                            tracker = adapter.ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                        found = feed(output("unsupported", acknowledgement(), number=3), 50)
                        operations = [item for item in found if isinstance(item, OperationObservation)]
                        boundaries = [item for item in found if isinstance(item, adapter.ResponseBoundary)]
                        self.assertEqual(len(operations), 1)
                        self.assertEqual(len(boundaries), 1)
                        self.assertEqual((boundaries[0].response_id, boundaries[0].method), (None, "unavailable"))
                        self.assertEqual(boundaries[0].event_id, operations[0].event_id)
                        self.assertIn("session:7", tracker.state["handles"])

    def test_positive_single_and_batch_controls_and_unknown_handles(self):
        for name, arguments in (
                ("exec_command", {"cmd": "fixture"}),
                ("functions.exec_command", '{"cmd":"fixture"}'),
                ("exec", 'text(await tools.exec_command({cmd:"fixture"}));'),
                ("functions.exec", '// @exec: {"yield_time_ms": 10000}\ntext(await tools.exec_command({cmd:"fixture"}));')):
            with self.subTest(name=name, arguments=arguments):
                self.assertEqual(adapter._call_input(name, arguments).kind, "execution")
        for source in UNSUPPORTED:
            self.assertEqual(adapter._call_input("exec", source).kind, "unknown")
        for args in ('{"cmd":"first","cmd":"second"}', {}, {"cmd": "fixture", "unknown": 1}):
            self.assertEqual(adapter._call_input("exec_command", args).kind, "unknown")
        for source in ('text(await tools.exec_command({cmd:"fixture",cmd:"second"}));',
                       'text(await tools.exec_command({cmd:`fixture`}));'):
            self.assertEqual(adapter._call_input("exec", source).kind, "unknown")
        tracker = adapter.ResponseTracker(THREAD, "fixture")
        tracker.consume(call("batch", arguments=batch_input('tools.exec_command({cmd:"fixture"})'),
                             response_id="original"), 10, None, "fixture-project")
        # A missing handle never borrows even explicit receiver metadata.
        tracker.consume(call("poll", name="write_stdin", arguments={"session_id": 99}, response_id="receiver"),
                        20, None, "fixture-project")
        found = tracker.consume(output("poll", acknowledgement()), 30, None, "fixture-project")
        self.assertIsNone(next(item for item in found if isinstance(item, adapter.ResponseBoundary)).response_id)

    def test_v3_continuation_cannot_grant_origin_proof_including_explicit_handles(self):
        tracker = adapter.ResponseTracker(THREAD, "fixture")
        tracker.consume(call("origin", response_id="old"), 10, None, "fixture-project")
        tracker.consume(output("origin", {"session_id": 7}), 20, None, "fixture-project")
        tracker.consume(call("pending", response_id="old-pending"), 30, None, "fixture-project")
        old = json.loads(json.dumps(tracker.state))
        old["version"] = 3
        before = json.dumps(old, sort_keys=True)
        restored = adapter.ResponseTracker(THREAD, "fixture", old)
        self.assertEqual(json.dumps(old, sort_keys=True), before)
        self.assertEqual(restored.state["version"], 4)
        self.assertTrue(all(frame["ambiguous"] and frame["response"] is None for frame in restored.pending_frames()))
        restored.consume(call("poll", name="write_stdin", arguments={"session_id": 7}, response_id="receiver"),
                         40, None, "fixture-project")
        found = restored.consume(output("poll", acknowledgement()), 50, None, "fixture-project")
        self.assertIsNone(next(item for item in found if isinstance(item, adapter.ResponseBoundary)).response_id)
        for invalid in (True, 0, 3, "2"):
            with self.assertRaises(UsageError):
                adapter.ResponseBoundary(**{**asdict(next(item for item in found if isinstance(item, adapter.ResponseBoundary))),
                                            "origin_version": invalid})


class OriginReplayTests(unittest.TestCase):
    setUp = fixtures.ResponseRepositoryTests.setUp
    records = fixtures.ResponseRepositoryTests.records
    write = fixtures.ResponseRepositoryTests.write
    append = fixtures.ResponseRepositoryTests.append
    reader = fixtures.ResponseRepositoryTests.reader
    acknowledgement = fixtures.ResponseRepositoryTests.acknowledgement
    operation = fixtures.ResponseRepositoryTests.operation
    write_turn = fixtures.ResponseRepositoryTests.write_turn
    ingest = fixtures.ResponseRepositoryTests.ingest
    read = fixtures.ResponseRepositoryTests.read

    def sample(self, source):
        rows = self.operation("running", {"session_id": 7}, "original", direct=True)
        rows += self.operation("unsupported", self.acknowledgement(fixtures.A, 1, "in_progress"), "receiver", direct=True)
        rows[-3]["payload"]["input"] = source
        self.write_turn(rows)

    def test_full_sliced_restarted_and_duplicate_ingest_preserve_unknown_numbers_and_success(self):
        for source in UNSUPPORTED[:4]:
            results = []
            for sliced in (False, True):
                with self.subTest(source=source, sliced=sliced):
                    self.setUp()
                    self.sample(source)
                    while self.repository.cursor(self.source.source_id, THREAD).offset < self.source.path.stat().st_size:
                        self.repository.collect_source(self.source, max_records=1 if sliced else 512)
                        self.repository = fixtures.UsageResponseRepository(self.repository.path, *self.repository.basis)
                    self.repository.refresh(self.reader)
                    actual = self.read()
                    self.assertEqual(actual["unattributed"]["unknown"]["response_count"], 2)
                    self.assertEqual(actual["unattributed"]["unassigned"]["response_count"], 0)
                    self.assertEqual(self.repository.summary()["models"][0]["response_count"], 2)
                    self.assertEqual(self.repository.summary()["models"][0]["total_tokens"], 240)
                    with self.repository.connection() as connection:
                        _, boundaries = self.repository._relations(connection)
                        operation = connection.execute("SELECT event_id,status FROM usage_operation_turns").fetchall()
                    full = fixtures.read_batch(self.source, include_attribution=True,
                                               attribution_project_id=self.repository.basis[0], include_response_relations=True)
                    self.assertEqual({item for _, _, item in boundaries},
                                     {item for item in full.attribution if isinstance(item, adapter.ResponseBoundary)})
                    self.assertEqual(len(operation), 1)
                    self.assertEqual(operation[0]["status"], "in_progress")
                    self.assertEqual([(item.response_id, item.method) for _, _, item in boundaries], [(None, "unavailable")])
                    self.repository.collect_source(self.source, max_records=1)
                    self.repository.refresh(self.reader)
                    self.assertEqual(self.read(), actual)
                    results.append((self.read(fixtures.A), actual["unattributed"], self.repository.summary()["models"]))
            self.assertEqual(results[0], results[1])

    def test_old_boundary_bytes_are_untrusted_until_replay_and_snapshots_get_successors(self):
        from task_governance_tool import usage_response_attribution as policy
        from task_governance_tool import usage_response_repository as repository_module
        from task_governance_tool.usage_evidence import encode, digest
        source = UNSUPPORTED[0]
        self.sample(source)
        current = adapter._call_input
        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v7"), \
                mock.patch.object(adapter, "_call_input", side_effect=lambda name, args:
                                  adapter.CallInput("execution") if args == source else current(name, args)), \
                mock.patch.object(policy, "ALGORITHM", "response-partitions-v4"), \
                mock.patch.object(repository_module, "ALGORITHM", "response-partitions-v4"):
            self.ingest(max_records=1)
            self.repository.refresh(self.reader)
            old = self.read(audit=True)
            self.assertEqual(self.read(fixtures.A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 1)
        # Exact legacy relation shape, in this isolated fixture only. No live
        # store migration or relabelling is part of collection or normal reads.
        with self.repository.connection(write=True) as connection:
            for row in connection.execute("SELECT * FROM usage_response_boundaries").fetchall():
                body = json.loads(row["document"])
                body.pop("origin_version")
                connection.execute("UPDATE usage_response_boundaries SET observation_id=?,document=? WHERE observation_id=?",
                                   (digest(body), encode(body).decode(), row["observation_id"]))
            for row in connection.execute("SELECT * FROM usage_response_context").fetchall():
                body = json.loads(row["document"])
                body["version"] = 3
                connection.execute("UPDATE usage_response_context SET document=? WHERE source_id=? AND lane=?",
                                   (encode(body).decode(), row["source_id"], row["lane"]))
        numerical = self.repository.summary()["models"]
        before = self.repository.path.read_bytes()
        with self.assertRaises(UsageError):
            self.read(fixtures.A)
        self.assertEqual(self.repository.path.read_bytes(), before)
        # An allowed refresh before replay must not promote old origin evidence.
        self.repository.refresh(self.reader)
        self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 2)
        incarnation = self.repository.cursor(self.source.source_id, THREAD).incarnation
        for _ in range(len(self.source.path.read_bytes().splitlines()) * 3):
            result = self.repository.collect_source(self.source, lane="audit", max_records=1)
            self.assertLessEqual(result["records"], 1)
            self.repository = fixtures.UsageResponseRepository(self.repository.path, *self.repository.basis)
            if result.get("replay"):
                break
        else:
            self.fail("old origin policy did not schedule bounded replay")
        self.repository.collect_source(self.source, max_records=1)
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, incarnation)
        self.assertEqual(self.repository.summary()["models"], numerical)
        self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 2)
        after = self.read(audit=True)
        documents = {item["snapshot_id"]: item for item in after["snapshots"]}
        self.assertTrue(all(documents[item["snapshot_id"]] == item for item in old["snapshots"]))
        self.assertTrue(all(documents[key]["algorithm"] == "response-partitions-v6" for key in after["current_snapshot_ids"]))
        self.assertTrue(any(item["predecessors"] for item in after["snapshots"] if item["algorithm"] == "response-partitions-v6"))
        with self.repository.connection() as connection:
            _, boundaries = self.repository._relations(connection)
        self.assertEqual([(item.response_id, item.origin_version) for _, _, item in boundaries], [(None, 2)])


if __name__ == "__main__":
    unittest.main()
