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
