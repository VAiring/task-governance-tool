"""Delta work, deferred integrity, atomic recovery and full-baseline equivalence."""

from contextlib import closing
from dataclasses import replace
import json
import os
import sqlite3
from time import monotonic
import unittest
from unittest import mock

from tests.test_usage_collection import UsageCollectionTests, THREAD, TURN, TURN2, event, usage, COUNTS
from tests.test_usage_evidence_repository import UsageEvidenceRepositoryTests, execution
from tests.test_usage_attribution import turn
from task_governance_tool.usage_incremental_repository import UsageIncrementalRepository, _PREVIOUS
from task_governance_tool.usage_incremental_adapter import scan, Models, ScanState
from task_governance_tool.usage_work_order_repository import UsageWorkOrderRepository
from task_governance_tool.usage_adapter import MAX_LINE, SourceInput
from task_governance_tool.usage_collection import register_source
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_values import UsageError


class IncrementalCollectionTests(unittest.TestCase):
    records = UsageCollectionTests.records
    write = UsageCollectionTests.write
    append = UsageCollectionTests.append
    collect = UsageCollectionTests.collect
    test_truncate_replace = UsageCollectionTests.test_truncate_and_same_length_prefix_replacement_replay
    test_replace_physical = UsageCollectionTests.test_replaced_physical_file_replays_without_double_count
    test_split_segments = UsageCollectionTests.test_split_segment_is_deduplicated
    test_foreign_owner = UsageCollectionTests.test_conflicting_owner_across_registered_sources
    test_parent_history = UsageCollectionTests.test_parent_history_is_excluded

    def setUp(self):
        UsageCollectionTests.setUp(self)
        self.repository = UsageIncrementalRepository(self.repository.path, *self.repository.basis)
        self.assertEqual(self.repository.initialize(), "migrated")

    def ingest(self, **limits):
        return self.repository.collect_source(self.source, **limits)

    def audit(self, **limits):
        return self.repository.collect_source(self.source, lane="audit", **limits)

    def test_saved_turn_model_lookup_is_once_per_scan_and_not_resaved(self):
        for count in (1, 16, 128):
            with self.subTest(responses=count):
                self.setUp()
                self.write(self.records())
                self.ingest()
                self.append(*(usage(f"later-{n}") for n in range(count)))

                def model_rows(lane):
                    with self.repository.connection() as connection:
                        return tuple(map(tuple, connection.execute(
                            "SELECT turn_id,model,effort FROM usage_scan_models "
                            "WHERE source_id=? AND lane=? ORDER BY turn_id",
                            (self.source.source_id, lane))))

                before_ingest = model_rows("ingest")
                with mock.patch.object(self.repository, "_model", wraps=self.repository._model) as lookup:
                    self.ingest(max_records=count)
                    self.assertEqual(lookup.call_count, 1)
                self.assertEqual(model_rows("ingest"), before_ingest)
                self.assertEqual(self.repository.summary()["models"][0]["response_count"], count)

                self.audit(max_records=1)  # Save turn_context in the audit lane.
                before_audit = model_rows("audit")
                with mock.patch.object(self.repository, "_model", wraps=self.repository._model) as lookup:
                    self.audit(max_records=count)
                    self.assertEqual(lookup.call_count, 1)
                self.assertEqual(model_rows("audit"), before_audit)

    def test_missing_model_cache_is_overridden_by_new_context_in_same_slice(self):
        self.write(self.records())
        self.ingest()
        self.append(usage("unknown-1", turn=TURN2), usage("unknown-2", turn=TURN2),
                    event("turn_context", turn_id=TURN2, model="second"), usage("known", turn=TURN2))
        with mock.patch.object(self.repository, "_model", wraps=self.repository._model) as lookup:
            self.ingest()
            self.assertEqual(lookup.call_count, 1)
        with self.repository.connection() as connection:
            rows = tuple(map(tuple, connection.execute(
                "SELECT turn_id,model FROM usage_scan_models WHERE source_id=? AND lane='ingest' ORDER BY turn_id",
                (self.source.source_id,))))
            response_models = tuple(map(tuple, connection.execute(
                "SELECT response_id,model FROM usage_responses WHERE response_id IN "
                "('unknown-1','unknown-2','known') ORDER BY response_id")))
        self.assertEqual(rows, ((TURN, "fixture-model"), (TURN2, "second")))
        self.assertEqual(response_models, (("known", "second"), ("unknown-1", None), ("unknown-2", None)))

    def test_model_read_cache_does_not_enter_changed_context(self):
        reads = []
        models = Models(lambda key: reads.append(key) or (("saved", "high") if key == TURN else None))
        self.assertEqual(models.get(TURN), ("saved", "high"))
        self.assertEqual(models.get(TURN), ("saved", "high"))
        self.assertEqual(models.get(TURN2, (None, None)), (None, None))
        self.assertEqual(models.get(TURN2, "missing"), "missing")
        self.assertEqual(reads, [TURN, TURN2])
        self.assertEqual(dict(models), {})
        models[TURN2] = ("observed", "medium")
        self.assertEqual(models.get(TURN2), ("observed", "medium"))
        self.assertEqual(dict(models), {TURN2: ("observed", "medium")})

    def test_small_append_and_unchanged_do_not_reparse_history(self):
        self.write(self.records(*(usage(f"r{i}") for i in range(2000))))
        work = []
        while self.repository.cursor(self.source.source_id, THREAD).offset < self.source.path.stat().st_size:
            work.append(self.ingest())
        self.assertGreater(len(work), 1)
        unchanged = self.ingest()
        self.assertEqual(unchanged["records"], 0)
        self.assertLess(unchanged["bytes_read"], 1000)
        self.append(usage("late"))
        delta = self.ingest()
        self.assertEqual(delta["records"], 1)
        self.assertLess(delta["bytes_read"], 2000)
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 2001)
        self.assertIn("prefix_verification_deferred", self.repository.summary()["diagnostics"])

    def test_committed_progress_distinguishes_backlog_eof_and_partial_tail(self):
        self.write(self.records(usage("first"), usage("second")))
        first = self.ingest(max_records=1)
        self.assertEqual((first["pending"], first["progressed"]), ("more_records", True))
        while self.repository.cursor(self.source.source_id, THREAD).offset < self.source.path.stat().st_size:
            self.ingest(max_records=1)
        unchanged = self.ingest()
        self.assertGreater(unchanged["bytes_read"], 0)  # Header bytes are not progress.
        self.assertEqual((unchanged["pending"], unchanged["progressed"]), ("none", False))
        with self.source.path.open("ab") as stream:
            stream.write(b'{"unfinished":')
        partial = self.ingest()
        self.assertEqual((partial["pending"], partial["progressed"]), ("partial_tail", False))

    def test_oversized_drain_progress_does_not_require_complete_record(self):
        from task_governance_tool import usage_incremental_adapter
        self.write(self.records())
        self.ingest()
        with self.source.path.open("ab") as stream:
            stream.write(b"x" * 2048 + b"\n")
        boundary = self.repository.cursor(self.source.source_id, THREAD)
        with mock.patch.object(usage_incremental_adapter, "MAX_LINE", 512):
            result = self.ingest(max_bytes=256)
        self.assertEqual((result["pending"], result["progressed"], result["records"]),
                         ("more_records", True, 0))
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), boundary)

    def _real_limit_unfinished_tail(self, length, *, oversized):
        self.repository = UsageWorkOrderRepository(self.repository.path, *self.repository.basis)
        self.assertEqual(self.repository.initialize(), "migrated")
        self.write(self.records(usage()))
        self.ingest()
        cursor = self.repository.cursor(self.source.source_id, THREAD)
        with self.source.path.open("ab") as stream:
            stream.write(b"x" * length)
        first = self.ingest()
        if oversized and first["pending"] == "more_records":
            self.assertEqual(first["pending"], "more_records")
            self.assertTrue(first["progressed"])
            first = self.ingest()
        self.assertEqual(first["pending"], "partial_tail")
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), cursor)
        self.assertIn("partial_tail", self.repository.summary()["diagnostics"])
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT pending FROM usage_sources WHERE source_id=?", (self.source.source_id,)
            ).fetchone()[0], "partial_tail")
        for limits in (({}, {"max_records": 0}) if oversized else ({},)):
            idle = self.ingest(**limits)
            self.assertEqual((idle["pending"], idle["progressed"], idle["records"]),
                             ("partial_tail", False, 0))
            self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), cursor)
            self.assertIn("partial_tail", self.repository.summary()["diagnostics"])
        if not oversized:
            return

        self.repository = UsageWorkOrderRepository(self.repository.path, *self.repository.basis)
        self.assertEqual(self.ingest()["pending"], "partial_tail")
        with self.repository.connection() as connection:
            before_rollback = (
                tuple(connection.execute("SELECT incarnation,offset,prefix,file_id,pending FROM usage_sources WHERE source_id=?",
                                         (self.source.source_id,)).fetchone()),
                tuple(connection.execute("SELECT revision,document FROM usage_scan_state WHERE source_id=? AND lane='ingest'",
                                         (self.source.source_id,)).fetchone()),
            )
        with mock.patch.object(self.repository, "_before_scan_commit", side_effect=UsageError("usage_pending")):
            with self.assertRaises(UsageError):
                self.ingest()
        with self.repository.connection() as connection:
            after_rollback = (
                tuple(connection.execute("SELECT incarnation,offset,prefix,file_id,pending FROM usage_sources WHERE source_id=?",
                                         (self.source.source_id,)).fetchone()),
                tuple(connection.execute("SELECT revision,document FROM usage_scan_state WHERE source_id=? AND lane='ingest'",
                                         (self.source.source_id,)).fetchone()),
            )
        self.assertEqual(before_rollback, after_rollback)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), cursor)
        self.assertIn("partial_tail", self.repository.summary()["diagnostics"])
        self.audit()
        self.assertIn("partial_tail", self.repository.summary()["diagnostics"])

        other = SourceInput(THREAD, self.logs / "other.jsonl", self.logs, self.root)
        register_source(self.repository, other, CallerIdentity(THREAD))
        self.write(self.records(usage("other")), path=other.path)
        self.repository.collect_source(other)
        self.assertIn("partial_tail", self.repository.summary()["diagnostics"])

        with self.source.path.open("ab") as stream:
            stream.write(b"x")
        self.assertEqual(self.ingest()["pending"], "partial_tail")
        self.assertEqual(self.ingest()["pending"], "partial_tail")
        with self.source.path.open("ab") as stream:
            stream.write(b"\n")
        complete = self.ingest()
        self.assertEqual(complete["pending"], "none")
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).offset,
                         self.source.path.stat().st_size)
        self.assertIn("record_too_large", self.repository.summary()["diagnostics"])
        self.assertNotIn("partial_tail", self.repository.summary()["diagnostics"])
        self.append(usage("later"))
        self.ingest()
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 3)

    def test_real_line_limit_minus_one_retains_partial_tail(self):
        self._real_limit_unfinished_tail(MAX_LINE - 1, oversized=False)

    def test_real_line_limit_exact_retains_partial_tail(self):
        self._real_limit_unfinished_tail(MAX_LINE, oversized=False)

    def test_real_line_limit_plus_one_retains_partial_tail_through_reentry(self):
        self._real_limit_unfinished_tail(MAX_LINE + 1, oversized=True)

    def test_real_line_limit_drain_to_eof_retains_partial_tail(self):
        self._real_limit_unfinished_tail(MAX_LINE + 16, oversized=True)

    def test_page_attempt_and_ingest_progress_commit_or_rollback_together(self):
        self.write(self.records(usage("first")))
        self.ingest(record_attempt=True)
        cursor = self.repository.cursor(self.source.source_id, THREAD)
        with self.repository.connection() as connection:
            previous = connection.execute("SELECT last_attempt FROM usage_work_order WHERE source_id=?",
                                          (self.source.source_id,)).fetchone()[0]
        self.append(usage("second"))
        with mock.patch.object(self.repository, "_before_scan_commit", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.ingest(record_attempt=True)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), cursor)
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT last_attempt FROM usage_work_order WHERE source_id=?",
                                                (self.source.source_id,)).fetchone()[0], previous)
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_responses WHERE response_id='second'").fetchone()[0], 0)
        self.ingest(record_attempt=True)
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT last_attempt FROM usage_work_order WHERE source_id=?",
                                                (self.source.source_id,)).fetchone()[0], previous + 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_responses WHERE response_id='second'").fetchone()[0], 1)

    def test_no_progress_backlog_does_not_advance_attempt_watermark(self):
        self.write(self.records(usage("first")))
        self.ingest(record_attempt=True)
        self.append(usage("second"))
        with self.repository.connection() as connection:
            previous = connection.execute("SELECT last_attempt FROM usage_work_order WHERE source_id=?",
                                          (self.source.source_id,)).fetchone()[0]
        result = self.ingest(record_attempt=True, max_records=0)
        self.assertEqual((result["pending"], result["progressed"]), ("more_records", False))
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT last_attempt FROM usage_work_order WHERE source_id=?",
                                                (self.source.source_id,)).fetchone()[0], previous)

    def test_fixed_audit_goal_finishes_under_constant_append_and_detects_preserved_mtime(self):
        self.write(self.records(*(usage(f"r{i}") for i in range(40))))
        self.ingest()
        initial = self.repository.cursor(self.source.source_id, THREAD)
        for n in range(30):
            self.audit(max_records=2)
            self.append(usage(f"a{n}"))
            self.ingest()
            with self.repository.connection() as connection:
                saved = json.loads(connection.execute("SELECT document FROM usage_scan_state WHERE lane='audit'").fetchone()[0])
            self.assertEqual(saved["goal"], initial.offset)
            if saved["state"]["offset"] == initial.offset:
                break
        else:
            self.fail("a finite audit goal was starved by append")
        before = self.source.path.stat()
        self.source.path.write_bytes(self.source.path.read_bytes().replace(b'"r0"', b'"x0"'))
        os.utime(self.source.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertNotIn("replay", self.ingest())  # The deferred detection window is explicit.
        result = self.audit(max_records=200)
        self.assertTrue(result["replay"])
        self.ingest()
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).incarnation, initial.incarnation + 1)
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 40 + n + 2)

    def test_models_legacy_and_partial_tail_survive_slices_and_late_response(self):
        self.write(self.records(event("event_msg", type="token_count"),
                                event("turn_context", turn_id=TURN2, model="second"), usage("later", turn=TURN2)))
        while self.repository.cursor(self.source.source_id, THREAD).offset < self.source.path.stat().st_size:
            self.ingest(max_records=1)
        self.assertIn("legacy_usage", self.repository.summary()["diagnostics"])
        self.append(usage("late", turn=TURN), event("event_msg", type="token_count"))
        self.ingest()
        self.assertNotIn("legacy_usage", self.repository.summary()["diagnostics"])
        models = self.repository.summary()["models"]
        self.assertEqual({x["model"] for x in models}, {"fixture-model", "second"})
        boundary = self.repository.cursor(self.source.source_id, THREAD)
        fragment = json.dumps(usage("partial")).encode()
        with self.source.path.open("ab") as stream:
            stream.write(fragment[:25])
        self.ingest()
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), boundary)
        with self.source.path.open("ab") as stream:
            stream.write(fragment[25:] + b"\n")
        self.ingest()
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 2)

    def test_scan_failure_rolls_back_context_cursor_observations_and_dirty_marks(self):
        self.write(self.records(usage()))
        before = self.repository.path.read_bytes()
        with mock.patch.object(self.repository, "_before_scan_commit", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.ingest()
        self.assertEqual(before, self.repository.path.read_bytes())
        self.ingest()
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 1)

    def test_old_audit_goal_that_is_no_longer_a_record_boundary_requests_replay(self):
        self.write(self.records(usage()))
        self.ingest()
        original = self.source.path.read_bytes()
        # Preserve size while moving the formerly complete final boundary.
        self.source.path.write_bytes(original[:-1] + b" ")
        result = self.audit()
        self.assertTrue(result["replay"])
        self.ingest()
        self.assertIn("partial_tail", self.repository.summary()["diagnostics"])

    def test_oversized_record_drains_without_advancing_unread_complete_cursor(self):
        self.write(self.records(usage()))
        self.ingest()
        before = self.repository.cursor(self.source.source_id, THREAD)
        with self.source.path.open("ab") as stream:
            stream.write(b"x" * 1200)
        from task_governance_tool import usage_incremental_adapter as adapter
        with mock.patch.object(adapter, "MAX_LINE", 512):
            self.ingest(max_bytes=512)
            self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), before)
            with self.source.path.open("ab") as stream:
                stream.write(b"\n")
            for _ in range(4):
                self.ingest(max_bytes=512)
            self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).offset, self.source.path.stat().st_size)
        self.assertIn("record_too_large", self.repository.summary()["diagnostics"])

    def test_all_prior_schema_migrations_are_explicit_atomic_and_idempotent(self):
        for previous in _PREVIOUS:
            with self.subTest(schema=previous.migrations[-1][0]):
                path = self.root / f"schema-{previous.migrations[-1][0]}.sqlite"
                old = previous(path, *self.repository.basis)
                old.initialize()
                before = path.read_bytes()
                current = UsageIncrementalRepository(path, *old.basis)
                self.assertEqual(current.inspect(), "migration_required")
                with self.assertRaises(UsageError):
                    current.registered_sources()
                self.assertEqual(before, path.read_bytes())
                with mock.patch.object(current, "_before_incremental_marker", side_effect=UsageError()):
                    with self.assertRaises(UsageError):
                        current.initialize()
                self.assertEqual(before, path.read_bytes())
                self.assertEqual(current.initialize(), "migrated")
                self.assertEqual(current.initialize(), "current")

    def test_budget_and_busy_store_preserve_cursor_for_later_event(self):
        self.write(self.records(usage()))
        before = self.repository.path.read_bytes()
        self.repository.deadline = monotonic() - 1
        with self.assertRaises(UsageError):
            self.ingest()
        self.assertEqual(before, self.repository.path.read_bytes())
        self.repository.deadline = float("inf")
        with closing(sqlite3.connect(self.repository.path)) as other:
            other.execute("BEGIN EXCLUSIVE")
            start = monotonic()
            with self.assertRaises(UsageError):
                self.ingest()
            self.assertLess(monotonic() - start, 1)
        self.ingest()

    def test_stale_scan_never_commits_context_from_losing_reader(self):
        self.write(self.records(usage()))
        self.ingest()
        self.append(event("turn_context", turn_id=TURN2, model="second"), usage("second", turn=TURN2))
        raced = False
        def concurrent():
            nonlocal raced
            if not raced:
                raced = True
                self.ingest()
        with mock.patch.object(self.repository, "_before_scan_write", side_effect=concurrent):
            with self.assertRaisesRegex(UsageError, "cursor_stale"):
                self.ingest()
        self.ingest()
        self.assertEqual({row["model"] for row in self.repository.summary()["models"]}, {"fixture-model", "second"})


class IncrementalProjectionTests(unittest.TestCase):
    setUpFixture = UsageEvidenceRepositoryTests.setUp
    records = UsageCollectionTests.records
    write = UsageCollectionTests.write
    append = UsageCollectionTests.append
    collect = UsageCollectionTests.collect
    core_reader = UsageEvidenceRepositoryTests.core_reader
    change = UsageEvidenceRepositoryTests.change
    observed = UsageEvidenceRepositoryTests.observed
    read = UsageEvidenceRepositoryTests.read
    refresh = UsageEvidenceRepositoryTests.refresh

    def setUp(self):
        # Reuse isolated core fixture without inheriting baseline-only assertions.
        UsageCollectionTests.setUp(self)
        from pathlib import Path
        from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository
        self.repository = UsageIncrementalRepository(self.repository.path, *self.repository.basis)
        self.repository.initialize()
        self.core_path = self.root / "core-fixture.sqlite"
        self.change_number, self.previous = 0, {}
        with closing(sqlite3.connect(self.core_path)) as core:
            core.executescript("""
                CREATE TABLE task_executions(execution_id PRIMARY KEY,project_id,task_id);
                CREATE TABLE task_owner_transitions(transition_id,project_id,task_id,execution_id,generation,
                    previous_status,current_status,actor_session_id,created_at);
                CREATE TABLE task_events(task_event_id,project_id,task_id,created_at);
                CREATE TABLE task_execution_cycles(completion_cycle_id,project_id,task_id,execution_id);
                CREATE TABLE review_receipt_sessions(review_receipt_id,session_id,execution_id,original_result_digest);
            """)

    def assert_baseline(self):
        with self.repository.connection() as connection, self.core_reader() as core:
            full = self.repository.attribution(core, numerical_connection=connection)
            current = self.repository._snapshots(connection, current=True)
            from task_governance_tool.usage_evidence import snapshot
            comparable = lambda item: {k: v for k, v in item.items() if k not in {"snapshot_id", "predecessors"}}
            actual = {tuple(x["executions"]): comparable(x) for x in current}
            expected = {}
            for component in full["components"]:
                if full["unresolved_operations"]:
                    component["diagnostics"] = sorted(set(component["diagnostics"]) | {"operation_unbound"})
                expected[tuple(component["executions"])] = comparable(snapshot(self.repository.basis[0], component))
            self.assertEqual(actual, expected)

    def test_unchanged_reuses_components_late_data_selects_one_and_merge_uses_union(self):
        a, b = ("tg_task_" + letter * 16 for letter in "ab")
        self.observed()
        self.change(a, 1, "in_progress", 2)
        self.change(a, 1, "done", 3, cycle="a")
        self.change(b, 2, "in_progress", 7)
        self.change(b, 2, "done", 8, cycle="b")
        self.refresh()
        self.assert_baseline()
        self.assertEqual(self.repository.last_projection_work["components_aggregated"], 2)
        self.refresh()
        self.assertEqual(self.repository.last_projection_work["components_aggregated"], 0)
        self.append(usage("late", turn=turn(3)))
        self.collect()
        self.refresh()
        self.assertEqual(self.repository.last_projection_work["components_aggregated"], 1)
        self.assert_baseline()
        self.change("tg_task_" + "c" * 16, 3, "in_progress", 3)
        self.change("tg_task_" + "c" * 16, 3, "done", 7, cycle="c")
        self.refresh()
        self.assertEqual(self.repository.last_projection_work["components_aggregated"], 1)
        self.assert_baseline()

    test_capture_rollback = UsageEvidenceRepositoryTests.test_capture_rollback_keeps_last_good_snapshot_and_membership
    test_restore = UsageEvidenceRepositoryTests.test_restored_core_rejects_newer_associations
    test_reopen = UsageEvidenceRepositoryTests.test_reopened_ready_period_does_not_inherit_completed_total
    test_conflict = UsageEvidenceRepositoryTests.test_conflict_creates_successor_and_never_counts_superseded_totals

    def test_cached_components_still_validate_original_counters(self):
        self.observed()
        task = "tg_task_" + "a" * 16
        self.change(task, 1, "in_progress", 2)
        self.change(task, 1, "done", 3, cycle="a")
        self.refresh()
        self.refresh()
        self.assertEqual(self.repository.last_projection_work["components_aggregated"], 0)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_responses SET input_tokens=101,total_tokens=121 WHERE response_id='r2'")
        with self.assertRaises(UsageError):
            self.refresh()
        with self.assertRaises(UsageError):
            self.read(task)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromTestCase(cls)
                              for cls in (IncrementalCollectionTests, IncrementalProjectionTests))


if __name__ == "__main__":
    unittest.main()
