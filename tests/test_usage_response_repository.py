"""Schema-6 preservation, atomic relation cursors and execution partitions."""

from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest import mock

from tests import test_usage_collection as collection_tests
from tests.test_usage_collection import THREAD, event, usage
from tests.test_usage_attribution import turn
from task_governance_tool.usage_adapter import read_batch, Cursor
from task_governance_tool.usage_incremental_repository import UsageIncrementalRepository, _PREVIOUS
from task_governance_tool.usage_response_repository import UsageResponseRepository
from task_governance_tool.usage_values import UsageError


A, B = ("tg_task_" + digit * 16 for digit in "ab")


class ResponseRepositoryTests(unittest.TestCase):
    records = collection_tests.UsageCollectionTests.records
    write = collection_tests.UsageCollectionTests.write
    append = collection_tests.UsageCollectionTests.append

    def setUp(self):
        collection_tests.UsageCollectionTests.setUp(self)
        self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
        self.repository.initialize()
        self.core_path = self.root / "core.sqlite"
        self.previous, self.generation = {}, 0
        with closing(sqlite3.connect(self.core_path)) as core:
            core.executescript("""
                CREATE TABLE task_executions(execution_id PRIMARY KEY,project_id,task_id);
                CREATE TABLE task_owner_transitions(transition_id,project_id,task_id,execution_id,generation,
                    previous_status,current_status,actor_session_id,created_at,policy_version);
                CREATE TABLE task_events(task_event_id,project_id,task_id,created_at);
                CREATE TABLE task_execution_cycles(completion_cycle_id,project_id,task_id,execution_id);
                CREATE TABLE review_receipt_sessions(review_receipt_id,session_id,execution_id,original_result_digest);
            """)

    def reader(self):
        connection = sqlite3.connect(f"file:{self.core_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return closing(connection)

    def acknowledgement(self, task, number, status, *, cycle=None):
        self.generation += 1
        g = self.generation
        execution = f"tg_execution_{number:016x}"
        event_id = f"tg_event_{g:016x}"
        with closing(sqlite3.connect(self.core_path)) as core:
            core.execute("INSERT OR IGNORE INTO task_executions VALUES (?,?,?)", (execution, self.repository.basis[0], task))
            core.execute("INSERT INTO task_owner_transitions VALUES (?,?,?,?,?,?,?,?,?,1)",
                         (f"transition-{g}", self.repository.basis[0], task, execution, g,
                          self.previous.get(task), status, THREAD, str(g)))
            core.execute("INSERT INTO task_events VALUES (?,?,?,?)", (event_id, self.repository.basis[0], task, str(g)))
            if cycle:
                core.execute("INSERT INTO task_execution_cycles VALUES (?,?,?,?)", (cycle, self.repository.basis[0], task, execution))
            core.commit()
        self.previous[task] = status
        return {"ok": True, "command": "task.edit", "project_id": self.repository.basis[0],
                "data": {"changed_fields": ["status"], "task": {"task_id": task, "project_id": self.repository.basis[0],
                         "status": status, "ownership": {"generation": g}},
                         "event": {"task_event_id": event_id, "task_id": task, "project_id": self.repository.basis[0]}}}

    def operation(self, identity, acknowledgement, response_id, *, number=1, direct=False):
        metadata = {"turn_id": turn(number)}
        if direct:
            metadata["response_id"] = response_id
        return [event("response_item", type="custom_tool_call", name="exec", call_id=identity, input="",
                      internal_chat_message_metadata_passthrough=metadata),
                usage(response_id, turn=turn(number)),
                event("response_item", type="custom_tool_call_output", call_id=identity,
                      internal_chat_message_metadata_passthrough={"turn_id": turn(number)}, output=json.dumps(acknowledgement))]

    def write_turn(self, rows):
        data = self.records(event("event_msg", type="task_started", turn_id=turn(1), started_at=1),
                            event("turn_context", turn_id=turn(1), model="fixture-model"), *rows)
        data[0]["payload"]["cli_version"] = "0.162.0-alpha.17.2"
        self.write(data)

    def ingest(self, **limits):
        while self.repository.cursor(self.source.source_id, THREAD).offset < self.source.path.stat().st_size:
            self.repository.collect_source(self.source, **limits)

    def read(self, task=None, audit=False):
        with self.reader() as core:
            return self.repository.read(core, task_id=task, audit=audit)

    def test_same_turn_three_completion_periods_are_exclusive_and_total_is_a_union(self):
        rows = []
        for task, number, prefix in ((A, 1, "a1"), (B, 2, "b1"), (A, 3, "a2")):
            rows += self.operation(prefix + "start", self.acknowledgement(task, number, "in_progress"), prefix + "r0")
            rows += self.operation(prefix + "done", self.acknowledgement(task, number, "done", cycle=prefix), prefix + "r1")
        self.write_turn(rows)
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        a = self.read(A, audit=True)
        self.assertEqual(len(a["periods"]), 2)
        self.assertEqual([p["models"][0]["total_tokens"] for p in a["periods"]], [240, 240])
        self.assertEqual(a["total"]["models"][0]["total_tokens"], 480)
        self.assertEqual(self.read(B)["total"]["models"][0]["total_tokens"], 240)
        self.assertTrue(all(p["work_units"][0]["shared"] == [] for p in a["periods"]))
        identities = set(self.read()["current_snapshot_ids"])
        self.repository.refresh(self.reader)
        self.assertEqual(set(self.read()["current_snapshot_ids"]), identities)
        self.assertEqual(self.repository.last_projection_work["components_aggregated"], 0)

    def test_full_and_incremental_relation_metadata_match_and_no_private_body_survives(self):
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1")
        rows[0]["payload"]["input"] = "PRIVATE_COMMAND_CANARY"
        rows[-1]["payload"]["output"] = "Failed to find real location of C:\\private\\python.exe\n" + rows[-1]["payload"]["output"]
        self.write_turn(rows)
        full = read_batch(self.source, include_attribution=True, attribution_project_id=self.repository.basis[0],
                          include_response_relations=True)
        self.ingest(max_records=1)
        with self.repository.connection() as connection:
            positions, boundaries = self.repository._relations(connection)
            from task_governance_tool.usage_response_adapter import ResponseBoundary, ResponsePosition
            self.assertEqual({item for _, _, item in positions}, {item for item in full.attribution if isinstance(item, ResponsePosition)})
            self.assertEqual({item for _, _, item in boundaries}, {item for item in full.attribution if isinstance(item, ResponseBoundary)})
            metadata = list(connection.execute("SELECT document FROM usage_response_context"))
            metadata += list(connection.execute("SELECT document FROM usage_response_boundaries"))
            self.assertNotIn("PRIVATE", str(metadata))
            self.assertNotIn("private", str(metadata))

    def test_scan_rollback_includes_relation_context_and_next_process_resumes(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1"))
        before = self.repository.path.read_bytes()
        with mock.patch.object(self.repository, "_before_scan_commit", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.repository.collect_source(self.source, max_records=1)
        self.assertEqual(self.repository.path.read_bytes(), before)
        self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["total"]["response_count"], 1)

    def test_all_old_schemas_need_explicit_atomic_migration(self):
        for previous in (UsageIncrementalRepository, *_PREVIOUS):
            with self.subTest(schema=previous.migrations[-1][0]):
                path = self.root / f"old-{previous.migrations[-1][0]}.sqlite"
                old = previous(path, *self.repository.basis)
                old.initialize()
                before = path.read_bytes()
                new = UsageResponseRepository(path, *self.repository.basis)
                self.assertEqual(new.inspect(), "migration_required")
                with self.assertRaises(UsageError):
                    new.registered_sources()
                self.assertEqual(path.read_bytes(), before)
                with mock.patch.object(new, "_before_incremental_marker", side_effect=UsageError()):
                    with self.assertRaises(UsageError):
                        new.initialize()
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(new.initialize(), "migrated")
                self.assertEqual(new.initialize(), "current")

    def test_source_replacement_never_rebinds_a_success_to_a_new_response(self):
        ack = self.acknowledgement(A, 1, "in_progress")
        self.write_turn(self.operation("start", ack, "original"))
        self.ingest()
        self.repository.refresh(self.reader)
        original = self.read()["current_snapshot_ids"][0]
        # A physical replacement is detectable at ingest. Same-inode prefix
        # editing follows the separate deferred audit contract.
        self.source.path.replace(self.logs / "retained-old.jsonl")
        self.write_turn(self.operation("start", ack, "replacement-response"))
        self.repository.collect_source(self.source)
        self.ingest()
        self.repository.refresh(self.reader)
        result = self.read(A)
        self.assertEqual(result["periods"][0]["work_units"][0]["exclusive"]["response_count"], 0)
        self.assertIn(original, {item["snapshot_id"] for item in self.read(audit=True)["snapshots"]})
        self.assertTrue(self.read()["unattributed"]["unknown"]["response_count"])

    def test_core_restore_missing_event_is_unknown_and_read_is_write_free(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1"))
        self.ingest()
        self.repository.refresh(self.reader)
        before = self.repository.path.read_bytes()
        self.read(A)
        self.assertEqual(before, self.repository.path.read_bytes())
        with closing(sqlite3.connect(self.core_path)) as core:
            core.execute("DELETE FROM task_events")
            core.commit()
        self.repository.refresh(self.reader)
        unit = self.read(A)["periods"][0]["work_units"][0]
        self.assertEqual(unit["exclusive"]["response_count"], 0)
        self.assertIn("operation_unbound", self.read(A)["periods"][0]["gaps"])

    def test_unconfirmed_call_is_partial_then_late_result_resolves_original_key(self):
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1")
        self.write_turn(rows[:-1])
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 1)
        self.append(rows[-1])
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 1)

    def test_migration_preserves_populated_schema5_snapshot_and_cycle_bytes(self):
        from task_governance_tool.usage_collection import register_source
        from task_governance_tool.session_identity import CallerIdentity
        old = UsageIncrementalRepository(self.root / "populated-old.sqlite", *self.repository.basis)
        old.initialize()
        register_source(old, self.source, CallerIdentity(THREAD))
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0")
        rows += self.operation("done", self.acknowledgement(A, 1, "done", cycle="cycle"), "r1")
        self.write_turn(rows)
        old.collect_source(self.source)
        old.refresh(self.reader)
        with old.connection() as connection:
            preserved = {table: list(map(tuple, connection.execute("SELECT * FROM " + table))) for table in
                         ("usage_snapshots", "usage_snapshot_members", "usage_cycle_links", "usage_responses")}
        new = UsageResponseRepository(old.path, *old.basis)
        new.initialize()
        with new.connection() as connection:
            for table, rows in preserved.items():
                self.assertEqual(list(map(tuple, connection.execute("SELECT * FROM " + table))), rows)
        new.collect_source(self.source)
        new.refresh(self.reader)
        with new.connection() as connection:
            for row in preserved["usage_snapshots"]:
                self.assertEqual(tuple(connection.execute("SELECT * FROM usage_snapshots WHERE snapshot_id=?", (row[0],)).fetchone()), row)
        with self.reader() as core:
            self.assertEqual(new.read(core, task_id=A)["total"]["response_count"], 2)

    def test_shared_start_response_keeps_exclusive_parts_and_task_union(self):
        a_start = self.acknowledgement(A, 1, "in_progress")
        a_done = self.acknowledgement(A, 1, "done", cycle="A")
        b_start = self.acknowledgement(B, 2, "in_progress")
        rows = self.operation("all", a_start, "shared", direct=True)
        rows[-1]["payload"]["output"] = json.dumps(a_start) + "\n" + json.dumps(a_done) + "\n" + json.dumps(b_start)
        rows += [usage("exclusive", turn=turn(1))]
        self.write_turn(rows)
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        a, b = self.read(A), self.read(B)
        self.assertEqual(a["total"]["response_count"], 1)
        self.assertEqual(b["total"]["response_count"], 2)
        unit = b["periods"][0]["work_units"][0]
        self.assertEqual(unit["exclusive"]["response_count"], 1)
        self.assertEqual(unit["shared"][0]["response_count"], 1)
        self.assertEqual({item["task_id"] for item in unit["shared"][0]["members"]}, {A, B})

    def test_corrupt_relation_context_does_not_silently_reset_or_advance(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1"))
        self.ingest(max_records=1)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_response_context SET document='{}' WHERE lane='ingest'")
        before = self.repository.cursor(self.source.source_id, THREAD)
        with self.assertRaises(UsageError) as raised:
            self.repository.collect_source(self.source)
        self.assertEqual(raised.exception.code, "usage_schema_invalid")
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), before)

    def test_concurrent_scan_cas_preserves_winning_counters_and_call_context(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1"))
        winner = UsageResponseRepository(self.repository.path, *self.repository.basis)
        retained = []
        def competing_write():
            winner.collect_source(self.source)
            retained.append(winner.path.read_bytes())
        with mock.patch.object(self.repository, "_before_scan_write", side_effect=competing_write):
            with self.assertRaises(UsageError) as raised:
                self.repository.collect_source(self.source)
        self.assertEqual(raised.exception.code, "cursor_stale")
        self.assertEqual(self.repository.path.read_bytes(), retained[0])
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["total"]["response_count"], 1)

    def test_reused_call_id_does_not_choose_the_later_origin(self):
        rows = self.operation("reused", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        rows += self.operation("reused", self.acknowledgement(B, 2, "in_progress"), "r1", direct=True)
        self.write_turn(rows)
        self.ingest()
        self.repository.refresh(self.reader)
        for task in (A, B):
            self.assertTrue(all(unit["exclusive"]["response_count"] == 0
                                for period in self.read(task)["periods"] for unit in period["work_units"]))

    def test_context_only_progress_invalidates_old_projection_and_explicit_failure_resolves(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1", direct=True))
        self.ingest()
        self.repository.refresh(self.reader)
        pending = event("response_item", type="function_call", name="functions.exec", call_id="unknown",
                        arguments="", internal_chat_message_metadata_passthrough={"turn_id": turn(1), "response_id": "r1"})
        self.append(pending)
        self.ingest()
        with self.assertRaises(UsageError) as raised:
            self.read(A)
        self.assertEqual(raised.exception.code, "usage_pending")
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 0)
        self.append(event("response_item", type="function_call_output", call_id="unknown",
            internal_chat_message_metadata_passthrough={"turn_id": turn(1)},
            output={"ok": False, "command": "task.edit", "project_id": self.repository.basis[0], "data": {}}))
        self.ingest()
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 1)

    def test_running_handle_keeps_uncertainty_between_polls_across_restart(self):
        for kind in ("session", "cell"):
            with self.subTest(kind=kind):
                self.setUp()
                rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
                running = {"session_id": 7, "output": ""} if kind == "session" else "Script running with cell ID cell-7"
                rows += self.operation("unknown", {"unused": True}, "r1", direct=True)
                rows[-1]["payload"]["output"] = running
                rows += [usage("r2", turn=turn(1))]
                self.write_turn(rows)
                self.ingest(max_records=1)
                self.repository.refresh(self.reader)
                unit = self.read(A)["periods"][0]["work_units"][0]
                self.assertEqual(unit["exclusive"]["response_count"], 1)
                self.assertEqual(sum(part["response_count"] for part in unit["partial"]), 2)
                self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
                tool = "functions.write_stdin" if kind == "session" else "functions.wait"
                arguments = {"session_id": 7} if kind == "session" else {"cell_id": "cell-7"}
                def poll(identity, value):
                    return [event("response_item", type="function_call", name=tool, call_id=identity,
                        arguments=json.dumps(arguments), internal_chat_message_metadata_passthrough={"turn_id": turn(2)}),
                        event("response_item", type="function_call_output", call_id=identity, output=value,
                        internal_chat_message_metadata_passthrough={"turn_id": turn(2)})]
                self.append(event("event_msg", type="task_started", turn_id=turn(2), started_at=2000),
                            event("turn_context", turn_id=turn(2), model="fixture-model"),
                            *poll("poll-running", running), usage("r3", turn=turn(2)))
                self.ingest(max_records=1)
                self.repository.refresh(self.reader)
                self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 3)
                self.append(*poll("poll-terminal", {"ok": False, "command": "task.edit",
                            "project_id": self.repository.basis[0], "data": {}}))
                self.ingest(max_records=1)
                self.repository.refresh(self.reader)
                self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 4)

    def test_missing_origin_counter_never_fences_previous_turn_or_preceding_responses(self):
        for later_turn in (False, True):
            with self.subTest(later_turn=later_turn):
                self.setUp()
                rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
                rows += self.operation("done", self.acknowledgement(A, 1, "done", cycle="A"), "r1", direct=True)
                self.write_turn(rows)
                self.ingest()
                number = 2 if later_turn else 1
                if later_turn:
                    self.append(event("event_msg", type="task_started", turn_id=turn(2), started_at=2000),
                                event("turn_context", turn_id=turn(2), model="fixture-model"))
                self.append(event("response_item", type="function_call", name="functions.exec", call_id="pending",
                            arguments="", internal_chat_message_metadata_passthrough={"turn_id": turn(number)}))
                self.ingest(max_records=1)
                self.repository.refresh(self.reader)
                unit = self.read(A)["periods"][0]["work_units"][0]
                self.assertEqual(unit["exclusive"]["response_count"], 2)
                self.assertFalse(unit["partial"])
                self.append(usage("late", turn=turn(number)))
                self.ingest(max_records=1)
                self.repository.refresh(self.reader)
                self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 2)
                self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 1)

    def test_foreign_counter_breaks_bracket_in_full_and_sliced_collection(self):
        from tests.test_usage_collection import PARENT
        from task_governance_tool.usage_response_adapter import ResponseBoundary
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "own")
        rows.insert(1, usage("foreign", thread=PARENT, turn=turn(1)))
        self.write_turn(rows)
        full = read_batch(self.source, include_attribution=True, attribution_project_id=self.repository.basis[0],
                          include_response_relations=True)
        found = [item for item in full.attribution if isinstance(item, ResponseBoundary)]
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0].response_id)
        self.ingest(max_records=1)
        with self.repository.connection() as connection:
            _, boundaries = self.repository._relations(connection)
        self.assertEqual({item for _, _, item in boundaries}, set(found))
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["total"]["response_count"], 1)
        self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 0)

    def test_opaque_results_preserve_unknown_through_slices_and_restart(self):
        for limit in (1, 10000):
            for opaque in ({"unknown": {"session_id": 7}}, '{"partial":',
                           {"exit_code": 0, "output": {"unknown": {"session_id": 7}}},
                           [{"type": "text", "text": "PRIVATE_OUTPUT"}]):
                with self.subTest(limit=limit, shape=type(opaque).__name__):
                    self.setUp()
                    rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
                    rows += self.operation("pending", opaque, "r1", direct=True)
                    rows += [usage("r2", turn=turn(1))]
                    self.write_turn(rows)
                    self.ingest(max_records=limit)
                    self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
                    self.repository.refresh(self.reader)
                    unit = self.read(A)["periods"][0]["work_units"][0]
                    self.assertEqual(unit["exclusive"]["response_count"], 1)
                    self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 2)
                    self.assertEqual(self.read(A)["total"]["response_count"], 3)
                    with self.repository.connection() as connection:
                        self.assertNotIn("PRIVATE", str(list(connection.execute("SELECT document FROM usage_response_context"))))
                    self.append(event("response_item", type="function_call_output", call_id="pending",
                        internal_chat_message_metadata_passthrough={"turn_id": turn(1)}, output={"exit_code": 1}))
                    self.ingest(max_records=limit)
                    self.repository.refresh(self.reader)
                    self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 3)

    def test_capacity_loss_preserves_unknown_after_drain_and_restart(self):
        from task_governance_tool.usage_response_adapter import MAX_CONTEXT
        for limit in (32, 10000):
            with self.subTest(limit=limit):
                self.setUp()
                rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
                for number in range(MAX_CONTEXT + 1):
                    rows.append(event("response_item", type="function_call", name="exec", call_id="pending-" + str(number),
                        arguments="", internal_chat_message_metadata_passthrough={"turn_id": turn(1), "response_id": "r1"}))
                rows += [usage("r1", turn=turn(1))]
                for number in range(MAX_CONTEXT):
                    rows.append(event("response_item", type="function_call_output", call_id="pending-" + str(number),
                        internal_chat_message_metadata_passthrough={"turn_id": turn(1)}, output={"exit_code": 1}))
                rows += [usage("r2", turn=turn(1))]
                self.write_turn(rows)
                self.ingest(max_records=limit)
                self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
                self.repository.refresh(self.reader)
                unit = self.read(A)["periods"][0]["work_units"][0]
                self.assertEqual(unit["exclusive"]["response_count"], 1)
                self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 2)
                self.assertEqual(self.read(A)["total"]["response_count"], 3)
                with self.repository.connection() as connection:
                    context = json.loads(connection.execute("SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                    self.assertFalse(context["calls"])
                    self.assertEqual(context["overflow"]["call"], "pending-128")

    def test_live_handle_collision_never_confirms_first_origin_after_restart(self):
        from task_governance_tool.usage_response_adapter import ResponseBoundary
        for kind in ("session", "cell"):
            with self.subTest(kind=kind):
                self.setUp()
                running = {"session_id": 7} if kind == "session" else "Script running with cell ID cell-7"
                tool = "write_stdin" if kind == "session" else "wait"
                args = {"session_id": 7} if kind == "session" else {"cell_id": "cell-7"}
                rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
                rows += self.operation("first", running, "r1", direct=True)
                rows.append(event("response_item", type="function_call", name=tool, call_id="issued-poll",
                    arguments=json.dumps(args), internal_chat_message_metadata_passthrough={"turn_id": turn(1)}))
                rows += self.operation("second", running, "r2", direct=True)
                self.write_turn(rows)
                self.ingest(max_records=1)
                self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
                done = self.acknowledgement(A, 1, "done", cycle="A")
                self.append(event("response_item", type="function_call_output", call_id="issued-poll", output=done,
                    internal_chat_message_metadata_passthrough={"turn_id": turn(1)}), usage("r3", turn=turn(1)))
                self.ingest(max_records=1)
                full = read_batch(self.source, include_attribution=True, attribution_project_id=self.repository.basis[0],
                                  include_response_relations=True)
                expected = {item for item in full.attribution if isinstance(item, ResponseBoundary)}
                with self.repository.connection() as connection:
                    _, stored = self.repository._relations(connection)
                self.assertEqual({item for _, _, item in stored}, expected)
                self.assertIsNone(next(item for item in expected if item.status == "done").response_id)
                self.repository.refresh(self.reader)
                self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 1)
                self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 3)

    def test_repeated_pause_block_resume_keeps_one_execution_and_exact_union(self):
        rows = []
        actions = (("in_progress", 0), ("paused", 2), ("in_progress", 5),
                   ("blocked", 7), ("in_progress", 9), ("done", 10))
        for status, number in actions:
            rows += self.operation("action-" + str(number), self.acknowledgement(A, 1, status,
                cycle="A" if status == "done" else None), "r" + str(number), direct=True)
            if number in (0, 2, 5, 7, 10):
                rows.append(usage("r" + str(number + 1), turn=turn(1)))
            if number == 2:
                rows.append(usage("r4", turn=turn(1)))
        self.write_turn(rows)
        self.ingest(max_records=1)
        self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
        self.repository.refresh(self.reader)
        a = self.read(A)
        self.assertEqual(len(a["periods"]), 1)
        self.assertEqual(len(a["periods"][0]["work_units"]), 1)
        self.assertEqual(a["total"]["response_count"], 9)
        self.assertEqual(a["total"]["models"][0]["total_tokens"], 1080)
        self.assertEqual(self.read()["unattributed"]["unassigned"]["response_count"], 3)
        original = set(self.read()["current_snapshot_ids"])
        self.repository.refresh(self.reader)
        self.assertEqual(set(self.read()["current_snapshot_ids"]), original)

    def test_stop_without_counter_still_closes_and_late_zero_is_observed(self):
        from task_governance_tool.usage_values import METRICS
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        stop = self.operation("stop", self.acknowledgement(A, 1, "paused"), "r1", direct=True)
        rows += [stop[0], stop[2], usage("r2", turn=turn(1), counts=dict.fromkeys(METRICS, 0))]
        self.write_turn(rows)
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["total"]["response_count"], 1)
        unassigned = self.read()["unattributed"]["unassigned"]
        self.assertEqual(unassigned["response_count"], 1)
        self.assertEqual(unassigned["models"][0]["total_tokens"], 0)
        self.assertFalse(self.read()["unattributed"]["unknown"]["response_count"])
        originals = {item["snapshot_id"]: item for item in self.read(audit=True)["snapshots"]}
        self.append(stop[1])
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["total"]["response_count"], 2)
        self.assertEqual(self.read()["unattributed"]["unassigned"]["response_count"], 1)
        self.assertTrue(originals.items() <= {item["snapshot_id"]: item for item in self.read(audit=True)["snapshots"]}.items())

    def test_failed_stop_resume_and_host_interruption_do_not_cut_intervals(self):
        failure = {"ok": False, "command": "task.edit", "project_id": self.repository.basis[0], "data": {}}
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        rows += self.operation("failed-stop", failure, "r1", direct=True)
        rows += [event("event_msg", type="turn_aborted", reason="interrupted"), usage("r2", turn=turn(1))]
        rows += self.operation("stop", self.acknowledgement(A, 1, "paused"), "r3", direct=True)
        rows += [event("event_msg", type="turn_aborted", reason="interrupted")]
        rows += self.operation("failed-resume", failure, "r4", direct=True)
        rows += [usage("r5", turn=turn(1))]
        self.write_turn(rows)
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["total"]["response_count"], 4)
        self.assertEqual(self.read()["unattributed"]["unassigned"]["response_count"], 2)
        self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 0)

    def test_prior_response_algorithm_snapshots_are_preserved_and_superseded(self):
        import task_governance_tool.usage_response_attribution as policy
        import task_governance_tool.usage_response_repository as repository_module
        from task_governance_tool.usage_evidence import validate, digest
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        rows += self.operation("done", self.acknowledgement(A, 1, "done", cycle="A"), "r1", direct=True)
        self.write_turn(rows)
        self.ingest()
        # Start/completion-only input has the same meaning under both policies;
        # seed an exact prior-algorithm fixture, not guessed stop partitions.
        with mock.patch.object(policy, "ALGORITHM", "response-partitions-v1"), \
                mock.patch.object(repository_module, "ALGORITHM", "response-partitions-v1"):
            self.repository.refresh(self.reader)
            old = self.read(audit=True)
        original = old["snapshots"][0]
        with self.assertRaises(UsageError):
            self.read(A)
        self.repository.refresh(self.reader)
        current = self.read(audit=True)
        documents = {item["snapshot_id"]: item for item in current["snapshots"]}
        self.assertEqual(documents[original["snapshot_id"]], original)
        self.assertTrue(all(documents[key]["algorithm"] == "response-partitions-v2" for key in current["current_snapshot_ids"]))
        self.assertTrue(any(original["snapshot_id"] in documents[key]["predecessors"] for key in current["current_snapshot_ids"]))
        self.assertTrue(set(tuple(sorted(row.items())) for row in old["cycle_links"]) <=
                        set(tuple(sorted(row.items())) for row in current["cycle_links"]))
        self.assertEqual(self.read(A)["total"]["response_count"], 2)
        for invalid in ([], {}, "response-partitions-v3"):
            changed = {**original, "algorithm": invalid}
            changed["snapshot_id"] = digest({key: value for key, value in changed.items() if key != "snapshot_id"})
            with self.assertRaises(UsageError):
                validate(changed, self.repository.basis[0])

    def test_delayed_stop_resume_polls_bind_original_turns_after_restart(self):
        from task_governance_tool.usage_response_adapter import ResponseBoundary
        for kind in ("session", "cell"):
            with self.subTest(kind=kind):
                self.setUp()
                tool = "write_stdin" if kind == "session" else "wait"
                def running(number):
                    return {"session_id": number} if kind == "session" else "Script running with cell ID cell-" + str(number)
                def asynchronous(identity, handle, response_id, number=1):
                    records = self.operation(identity, running(handle), response_id, number=number, direct=True)
                    # A cell header is plain transport text, not a JSON-quoted
                    # string in the synthetic Task acknowledgement helper.
                    records[-1]["payload"]["output"] = running(handle)
                    return records
                def poll(identity, number, value, receiving_turn):
                    args = {"session_id": number} if kind == "session" else {"cell_id": "cell-" + str(number)}
                    return [event("response_item", type="function_call", name=tool, call_id=identity, arguments=json.dumps(args),
                                internal_chat_message_metadata_passthrough={"turn_id": turn(receiving_turn)}),
                            event("response_item", type="function_call_output", call_id=identity, output=value,
                                internal_chat_message_metadata_passthrough={"turn_id": turn(receiving_turn)})]
                rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
                pause = self.acknowledgement(A, 1, "paused")
                rows += asynchronous("stop", 7, "r1")
                rows += [usage("r2", turn=turn(1))]
                self.write_turn(rows)
                self.ingest(max_records=1)
                self.repository.refresh(self.reader)
                self.assertGreater(self.read()["unattributed"]["unknown"]["response_count"], 0)
                self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
                self.append(event("event_msg", type="task_started", turn_id=turn(2), started_at=2000),
                    event("turn_context", turn_id=turn(2), model="fixture-model"),
                    *poll("stop-running", 7, running(7), 2), usage("r3", turn=turn(2)),
                    *poll("stop-terminal", 7, json.dumps(pause), 2))
                self.ingest(max_records=1)
                self.repository.refresh(self.reader)
                self.assertEqual(self.read(A)["total"]["response_count"], 2)
                self.assertEqual(self.read()["unattributed"]["unassigned"]["response_count"], 2)
                resume = self.acknowledgement(A, 1, "in_progress")
                self.append(*asynchronous("resume", 8, "r4", number=2), usage("r5", turn=turn(2)))
                self.ingest(max_records=1)
                self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
                self.append(event("event_msg", type="task_started", turn_id=turn(3), started_at=3000),
                    event("turn_context", turn_id=turn(3), model="fixture-model"), usage("r6", turn=turn(3)),
                    *poll("resume-running", 8, running(8), 3),
                    *poll("resume-terminal", 8, "Failed to find real location of C:\\private\\python.exe\n" + json.dumps(resume), 3))
                self.ingest(max_records=1)
                self.repository.refresh(self.reader)
                self.assertEqual(self.read(A)["total"]["response_count"], 5)
                self.assertEqual(self.read()["unattributed"]["unassigned"]["response_count"], 2)
                self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 0)
                full = read_batch(self.source, include_attribution=True, attribution_project_id=self.repository.basis[0],
                                  include_response_relations=True)
                expected = {item for item in full.attribution if isinstance(item, ResponseBoundary)}
                with self.repository.connection() as connection:
                    _, stored = self.repository._relations(connection)
                    self.assertNotIn("private", str(list(connection.execute("SELECT document FROM usage_response_context"))))
                self.assertEqual({item for _, _, item in stored}, expected)
                self.assertEqual({(item.status, item.response_id, item.turn_id) for item in expected},
                                 {("in_progress", "r0", turn(1)), ("paused", "r1", turn(1)), ("in_progress", "r4", turn(2))})

    def test_prior_codec_trace_replays_compact_checks_without_counter_or_history_loss(self):
        import task_governance_tool.usage_response_adapter as adapter
        compact = {"tasks": [], "total_matching": 0, "returned_count": 0, "limit": 20, "truncated": False}
        check = {"ok": True, "command": "task.context", "project_id": self.repository.basis[0],
                 "data": {"selection": "none", "current": compact, "next": compact, "selected": None}}
        rows = self.operation("check", check, "r0", direct=True) + [usage("r1", turn=turn(1))]
        rows += self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r2", direct=True)
        self.write_turn(rows)
        current_shape = adapter._check_shape
        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v1"), \
                mock.patch.object(adapter, "_check_shape", side_effect=lambda command, data, project:
                                  False if command == "task.context" else current_shape(command, data, project)):
            self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        old = {item["snapshot_id"]: item for item in self.read(audit=True)["snapshots"]}
        incarnation = self.repository.cursor(self.source.source_id, THREAD).incarnation
        for _ in range(len(self.source.path.read_bytes().splitlines()) + 1):
            if self.repository.collect_source(self.source, lane="audit", max_records=1).get("replay"):
                break
        else:
            self.fail("changed codec did not require replay")
        self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)
        self.repository.collect_source(self.source, max_records=1)
        self.ingest(max_records=1)
        self.repository.refresh(self.reader)
        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, incarnation)
        self.assertEqual(self.read(A)["total"]["response_count"], 3)
        self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 3)
        self.assertFalse(self.read()["unattributed"]["unknown"]["response_count"])
        self.assertTrue(old.items() <= {item["snapshot_id"]: item for item in self.read(audit=True)["snapshots"]}.items())


if __name__ == "__main__":
    unittest.main()
