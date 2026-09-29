from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from unittest import mock

from tests.verification_receipt_test_support import (
    initialize, add_task, run_taskgov, payload, seed_current_review_evidence, completion,
)
from tests.test_m242_r3b_schema20_activation import _SCHEMA20_RUNTIME_PATCH_TARGETS
from tests.test_m23s_schema22_migration import _logical_snapshot, _source_copy, _add_unrelated_objects
from tests.test_m23s_schema22_validation import _bundle_artifacts
from task_governance_tool import storage
from task_governance_tool import schema_task_ownership as ownership
from tests.test_task_ownership import X, Y


@contextmanager
def schema23_runtime():
    with ExitStack() as stack:
        for target in _SCHEMA20_RUNTIME_PATCH_TARGETS:
            stack.enter_context(mock.patch(target, 23))
        yield


class FailingOwnershipCopy(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO completion_evidence_bundles ("):
            raise sqlite3.OperationalError("test-only ownership migration interruption")
        return super().execute(sql, parameters)


class TaskOwnershipMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with schema23_runtime():
            self.repo, self.db = initialize(Path(temporary.name))
            self.done = self.add("Done before ownership", verification_not_required_reason="Test-only no executable change")
            seed_current_review_evidence(self.db, self.repo, self.done)
            result = completion(self.db, self.repo, self.done)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.first = self.add("Old active one")
            self.second = self.add("Old active two")
            self.pending = self.add("Old pending", status="review_pending")
            self.ready = self.add("Old ready", status="ready")
        # Activate the candidate version only for this isolated migration fixture.
        candidate = ExitStack()
        self.addCleanup(candidate.close)
        for target in _SCHEMA20_RUNTIME_PATCH_TARGETS:
            candidate.enter_context(mock.patch(target, 24))

    def add(self, title, **kwargs):
        return add_task(self.db, self.repo, title=title, verification="", **kwargs)["task_id"]

    def test_migration_retains_business_rows_and_bundles_and_unknown_owners(self):
        with closing(storage.connect(self.db)) as connection:
            _add_unrelated_objects(connection)
            project = connection.execute("SELECT project_id FROM tasks LIMIT 1").fetchone()[0]
            tables = tuple(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' ORDER BY name"))
            before = storage._selected_table_projection_snapshot(connection, tables)
            with schema23_runtime():
                _, bundles_before = _bundle_artifacts(connection, project)
            self.assertTrue(ownership.migrate(connection))
            self.assertEqual(storage.current_schema_version(connection), 24)
            self.assertEqual(before, storage._selected_table_projection_snapshot(connection, tables))
            ownership.validate_storage(connection)
            _, bundles_after = _bundle_artifacts(connection, project)
            self.assertEqual(bundles_before, bundles_after)
            self.assertEqual({item.payload["source_schema_version"] for item in bundles_after.values()}, {23})
            rows = {row["task_id"]: dict(row) for row in connection.execute("SELECT * FROM task_ownership")}
            self.assertEqual({task_id for task_id, row in rows.items() if row["state"] == "unknown"},
                             {self.first, self.second, self.pending})
            self.assertTrue(all(row["generation"] == 0 and row["execution_id"] is None
                                and row["owner_session_id"] is None and row["completion_session_id"] is None
                                for row in rows.values()))
            for table in ("task_executions", "task_owner_transitions", "task_execution_cycles"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
            migrated = _logical_snapshot(connection)
            connection.execute("PRAGMA query_only = ON")
            self.assertFalse(ownership.migrate(connection))
            self.assertEqual(_logical_snapshot(connection), migrated)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def test_copy_failure_and_marker_mutation_roll_back(self):
        with _source_copy(self.db, factory=FailingOwnershipCopy) as connection:
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                ownership.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with _source_copy(self.db) as connection:
            connection.execute("CREATE TRIGGER unrelated_marker_change AFTER INSERT ON schema_migrations WHEN NEW.version=24 BEGIN UPDATE tasks SET title='changed by unrelated trigger'; END")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                ownership.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)

    def test_old_marker_with_new_owned_object_and_owned_ddl_drift_fail_closed(self):
        with _source_copy(self.db) as connection:
            connection.execute("CREATE TABLE task_ownership(unrelated TEXT)")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                ownership.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
        with _source_copy(self.db) as connection:
            ownership.migrate(connection)
            connection.execute("DROP INDEX idx_task_ownership_active_session")
            connection.execute("CREATE INDEX idx_task_ownership_active_session ON task_ownership(project_id, owner_session_id)")
            connection.commit()
            with self.assertRaises(storage.StorageError):
                ownership.migrate(connection)

    def test_version23_reader_rejects_migrated_database(self):
        with closing(storage.connect(self.db)) as connection:
            ownership.migrate(connection)
        with schema23_runtime():
            result = payload(run_taskgov("task", "show", self.first, "--repo", str(self.repo), "--db", str(self.db), "--json"))
            self.assertFalse(result["ok"])

    def test_migrated_unknown_requires_explicit_recovery_and_preserves_history(self):
        with closing(storage.connect(self.db)) as connection:
            ownership.migrate(connection)
            old_cycles = tuple(tuple(row) for row in connection.execute("SELECT * FROM task_completion_cycles"))
        def invoke(*args, caller=X):
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": caller.session_id}):
                return payload(run_taskgov(*args, "--repo", str(self.repo), "--db", str(self.db), "--json"))
        for task_id in (self.first, self.pending):
            shown = invoke("task", "show", task_id)
            self.assertTrue(shown["ok"], shown)
            self.assertEqual(shown["data"]["task"]["ownership"]["state"], "unknown")
            self.assertIsNone(shown["data"]["task"]["ownership"]["is_owner"])
            rejected = invoke("task", "edit", task_id, "--add-note", "Cannot infer an owner")
            self.assertEqual(rejected["errors"][0]["code"], "task_not_owned")
            recovered = invoke("task", "edit", task_id, "--status", "paused", "--pause-reason", "Explicit migration recovery")
            self.assertTrue(recovered["ok"], recovered)
            resumed = invoke("task", "edit", task_id, "--status", "in_progress")
            self.assertTrue(resumed["ok"], resumed)
            self.assertTrue(resumed["data"]["task"]["ownership"]["is_owner"])
            self.assertTrue(invoke("task", "edit", task_id, "--status", "paused", "--pause-reason", "Release test slot")["ok"])
        reopened = invoke("task", "edit", self.done, "--status", "in_progress", "--reopen-reason", "Explicit follow-up", caller=Y)
        self.assertTrue(reopened["ok"], reopened)
        self.assertIsNotNone(reopened["data"]["task"]["ownership"]["execution_id"])
        with closing(storage.connect(self.db)) as connection:
            self.assertEqual(tuple(tuple(row) for row in connection.execute("SELECT * FROM task_completion_cycles")), old_cycles)
            ownership.validate_storage(connection)


if __name__ == "__main__":
    unittest.main()
