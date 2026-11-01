"""Immutable recurrence collision validation, bounded reads and atomic retry."""

import json
import unittest
from unittest import mock

from tests import test_usage_collection as collection
from tests import test_usage_reconciliation_changes_runtime as runtime
from task_governance_tool.usage_attribution import _models
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_values import UsageError


class CandidateTests(unittest.TestCase):
    def setUp(self):
        fixture = collection.UsageCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write(fixture.records(collection.usage("one"), collection.usage("two")))
        fixture.collect()
        self.repository = UsageReconciliationRepository(fixture.repository.path, *fixture.repository.basis)
        self.repository.initialize()
        with self.repository.connection() as connection:
            models = _models(self.repository._responses(connection, [("openai", "one")]))
        self.component = {"executions": ["tg_execution_" + "a" * 16],
                          "response_keys": [("openai", "one")], "models": models, "diagnostics": []}

    def save(self, connection, component=None, previous=()):
        return self.repository._save_snapshot_candidate(connection, component or self.component, previous)

    @staticmethod
    def evidence(connection):
        return {table: list(map(tuple, connection.execute("SELECT * FROM " + table)))
                for table in ("usage_snapshots", "usage_snapshot_members", "usage_supersessions", "usage_cycle_links")}

    def test_absent_active_and_unchanged_preserve_identity_and_exact_rows(self):
        with self.repository.connection(write=True) as connection:
            original = self.save(connection)
            before = self.evidence(connection)
            self.assertEqual(self.save(connection), original)
            self.assertEqual(self.save(connection, previous=[original]), original)
            self.assertEqual(self.evidence(connection), before)

    def test_repeated_collision_keeps_only_last_collision_beside_fixed_predecessors(self):
        with self.repository.connection(write=True) as connection:
            ordinary = self.save(connection, {**self.component, "diagnostics": ["interval_open"]})
            previous = [ordinary]
            first = self.save(connection, previous=previous)
            last = first
            for number in range(1, 8):
                self.save(connection, {**self.component, "diagnostics": ["partial_tail"]}, [last])
                old = self.evidence(connection)
                next_item = self.save(connection, previous=previous)
                self.assertEqual(next_item["predecessors"], sorted({ordinary["snapshot_id"], last["snapshot_id"]}))
                self.assertNotEqual(next_item["snapshot_id"], last["snapshot_id"])
                for table, rows in old.items():
                    self.assertTrue(set(rows) <= set(self.evidence(connection)[table]))
                last = next_item
            edges = list(map(tuple, connection.execute("SELECT * FROM usage_supersessions")))
            ordinal = {row[0]: index for index, row in enumerate(connection.execute("SELECT snapshot_id FROM usage_snapshots ORDER BY rowid"))}
            self.assertTrue(all(ordinal[parent] < ordinal[child] for parent, child in edges))
            self.assertEqual(self.save(connection, previous=previous), last)

    def test_retired_unchanged_overlap_never_reuses_retired_id(self):
        with self.repository.connection(write=True) as connection:
            first = self.save(connection)
            read = self.repository._snapshot_read(connection, current=True)
            self.save(connection, {**self.component, "diagnostics": ["interval_open"]}, [first])
            later = self.repository._save_snapshot_candidate(connection, self.component, [first], read=read)
            self.assertNotEqual(later["snapshot_id"], first["snapshot_id"])
            self.assertEqual(later["predecessors"], [first["snapshot_id"]])

    def test_predecessor_proof_cannot_cross_repository_or_connection(self):
        with self.repository.connection(write=True) as connection:
            first = self.save(connection)
            read = self.repository._snapshot_read(connection, current=True)
            another = type(self.repository)(self.repository.path, *self.repository.basis)
            with self.assertRaises(UsageError):
                another._save_snapshot_candidate(connection, self.component, [first], read=read)
        with self.repository.connection(write=True) as connection:
            with self.assertRaises(UsageError):
                self.repository._save_snapshot_candidate(connection, self.component, [first], read=read)

    def test_collision_rejects_body_member_and_predecessor_damage_without_repair(self):
        with self.repository.connection(write=True) as connection:
            first = self.save(connection, {**self.component, "diagnostics": ["interval_open"]})
            other = self.save(connection, {**self.component, "diagnostics": ["partial_tail"]})
            candidate = self.save(connection, previous=[first])
            identity = candidate["snapshot_id"]
            mutations = {
                "body_missing": [("UPDATE usage_snapshots SET document='{}' WHERE snapshot_id=?", (identity,))],
                "body_extra": [("UPDATE usage_snapshots SET document=json_set(document,'$.extra',1) WHERE snapshot_id=?", (identity,))],
                "body_mismatch": [("UPDATE usage_snapshots SET document=json_set(document,'$.response_count',2) WHERE snapshot_id=?", (identity,))],
                "member_missing": [("DELETE FROM usage_snapshot_members WHERE snapshot_id=?", (identity,))],
                "member_extra": [("INSERT INTO usage_snapshot_members VALUES (?,'openai','two')", (identity,))],
                "member_mismatch": [("UPDATE usage_snapshot_members SET response_id='two' WHERE snapshot_id=?", (identity,))],
                "predecessor_missing": [("DELETE FROM usage_supersessions WHERE successor_id=?", (identity,))],
                "predecessor_extra": [("INSERT INTO usage_supersessions VALUES (?,?)", (other["snapshot_id"], identity))],
                "predecessor_mismatch": [("UPDATE usage_supersessions SET predecessor_id=? WHERE successor_id=?", (other["snapshot_id"], identity))],
            }
            for name, statements in mutations.items():
                with self.subTest(name=name):
                    connection.execute("SAVEPOINT damaged_fixture")
                    for table in ("usage_snapshots", "usage_snapshot_members", "usage_supersessions"):
                        for operation in ("update", "delete"):
                            connection.execute(f"DROP TRIGGER trg_{table}_no_{operation}")
                    for sql, args in statements:
                        connection.execute(sql, args)
                    damaged = self.evidence(connection)
                    with self.assertRaises(UsageError):
                        self.save(connection, previous=[first])
                    self.assertEqual(self.evidence(connection), damaged)
                    connection.execute("ROLLBACK TO damaged_fixture")
                    connection.execute("RELEASE damaged_fixture")

    def test_repeated_id_guard_and_budget_abort_leave_no_rows_to_adopt(self):
        with self.repository.connection(write=True) as connection:
            first = self.save(connection)
            self.save(connection, {**self.component, "diagnostics": ["partial_tail"]}, [first])
        with self.repository.connection() as connection:
            before = self.evidence(connection)
        with mock.patch("task_governance_tool.usage_evidence_repository.snapshot", return_value=first) as derive:
            with self.assertRaises(UsageError), self.repository.connection(write=True) as connection:
                self.save(connection)
            self.assertEqual(derive.call_count, 3)
        original_snapshot = self.repository._snapshot
        def expire_after_collision(*args, **kwargs):
            value = original_snapshot(*args, **kwargs)
            self.repository.deadline = 0
            return value
        try:
            with self.assertRaisesRegex(UsageError, "usage_pending"), self.repository.connection(write=True) as connection:
                with mock.patch.object(self.repository, "_snapshot", side_effect=expire_after_collision) as examined:
                    self.save(connection)
            self.assertEqual(examined.call_count, 1)
        finally:
            self.repository.deadline = float("inf")
        with self.repository.connection() as connection:
            self.assertEqual(self.evidence(connection), before)
        restarted = type(self.repository)(self.repository.path, *self.repository.basis)
        with restarted.connection(write=True) as connection:
            next_item = restarted._save_snapshot_candidate(connection, self.component, ())
            self.assertEqual(next_item["predecessors"], [first["snapshot_id"]])

    def test_schema10_unrelated_history_does_not_expand_candidate_reads(self):
        with self.repository.connection(write=True) as connection:
            first = self.save(connection)
            self.save(connection, {**self.component, "diagnostics": ["partial_tail"]}, [first])
            # Measure actual SQLite VM work in resolution only, not fixture or
            # lifecycle admission. Compare the same fixed collision chain.
            def measure():
                connection.execute("SAVEPOINT measure")
                statements, steps = [], []
                connection.set_trace_callback(statements.append)
                connection.set_progress_handler(lambda: steps.append(1) or 0, 1)
                try:
                    item = self.save(connection)
                finally:
                    connection.set_progress_handler(None, 0)
                    connection.set_trace_callback(None)
                connection.execute("ROLLBACK TO measure")
                connection.execute("RELEASE measure")
                queries = [sql for sql in statements if sql.startswith("SELECT")]
                return item, len(steps), queries
            before, small, reads = measure()
            for number in range(256):
                empty = {"executions": [f"tg_execution_{number:016x}"], "response_keys": [], "models": [], "diagnostics": []}
                base = self.save(connection, empty)
                self.save(connection, {**empty, "diagnostics": ["interval_open"]}, [base])
            after, large, later_reads = measure()
            self.assertEqual(after, before)
            self.assertEqual(later_reads, reads)
            self.assertLessEqual(large, small + 50, (small, large))
            self.assertEqual(sum("FROM usage_snapshots WHERE snapshot_id=" in sql for sql in reads), 2)
            plan = list(connection.execute("EXPLAIN QUERY PLAN SELECT predecessor_id FROM usage_supersessions WHERE successor_id=?", (first["snapshot_id"],)))
            self.assertTrue(any("usage_reconcile_snapshot_successor" in row[3] for row in plan))

    def test_collision_chain_point_lookups_are_linear_and_direct_edges_bounded(self):
        with self.repository.connection(write=True) as connection:
            current = self.save(connection)
            for length in range(1, 9):
                self.save(connection, {**self.component, "diagnostics": ["interval_open"]}, [current])
                statements = []
                connection.set_trace_callback(statements.append)
                try:
                    current = self.save(connection)
                finally:
                    connection.set_trace_callback(None)
                self.assertEqual(sum(sql.startswith("SELECT snapshot_id,document FROM usage_snapshots WHERE snapshot_id=")
                                     for sql in statements), length + 1)
                self.assertEqual(len(current["predecessors"]), 1)


class PublicationRollbackTests(unittest.TestCase):
    def test_capture_failure_keeps_last_good_files_and_retries_atomic_relations(self):
        fixture = runtime.ChangedRuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.publish_same()
        from task_governance_tool import usage_evidence_service as service
        target, repository = fixture.fixture.target, fixture.repository
        before_files = {path: path.read_bytes() for path in target.resolved_usage_root.rglob("*.json")}
        with repository.connection() as connection:
            before = CandidateTests.evidence(connection)
        with repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET pending='partial_tail'")
        with mock.patch.object(service, "repository_for", return_value=repository), \
                mock.patch.object(repository, "_before_snapshot_commit", side_effect=UsageError("usage_pending")):
            self.assertEqual(service.refresh_usage(target)["publication"], "pending")
        with repository.connection() as connection:
            self.assertEqual(CandidateTests.evidence(connection), before)
        for path, data in before_files.items():
            self.assertEqual(path.read_bytes(), data)
        latest = fixture.publish_same(restart=True)
        self.assertGreater(len(latest["snapshots"]), 1)
        self.assertEqual(json.loads(target.resolved_usage_index.read_bytes()), latest)


if __name__ == "__main__":
    unittest.main()
