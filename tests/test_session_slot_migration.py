from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.test_review_session_migration import candidate_runtime
from tests.test_m23s_schema22_migration import _logical_snapshot, _source_copy, _add_unrelated_objects
from tests.test_m23s_schema22_validation import _bundle_artifacts
from tests.verification_receipt_test_support import (
    initialize, add_task, seed_current_review_evidence, completion, run_taskgov, payload, target_for,
)
from task_governance_tool import storage, schema_session_slot as schema


class FailingCopy(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO completion_evidence_bundles ("):
            raise sqlite3.OperationalError("test-only session slot copy interruption")
        return super().execute(sql, parameters)


class SessionSlotMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(candidate_runtime(25))
        self.repo, self.db = initialize(Path(temporary.name))
        self.done = self.add("Completed legacy task")
        self.assertEqual(completion(self.db, self.repo, self.done).returncode, 0)
        self.pending = [self.add("Legacy pending one"), self.add("Legacy pending two")]
        self.active = add_task(self.db, self.repo, title="Legacy active")['task_id']

    def add(self, title):
        task_id = add_task(self.db, self.repo, title=title, verification="",
            verification_not_required_reason="No executable change in fixture")["task_id"]
        seed_current_review_evidence(self.db, self.repo, task_id)
        self.edit(task_id, "review_pending")
        return task_id

    def edit(self, task_id, status, *, code=None):
        result = payload(run_taskgov("task", "edit", task_id, "--status", status,
            "--repo", str(self.repo), "--db", str(self.db), "--json"))
        if code is None:
            self.assertTrue(result["ok"], result)
        else:
            self.assertIn(code, [error["code"] for error in result["errors"]], result)
        return result

    def test_preserve_legacy_overlap_history_bundles_and_release_or_complete(self):
        with closing(storage.connect(self.db)) as connection:
            _add_unrelated_objects(connection)
            tables = tuple(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name!='schema_migrations' ORDER BY name"))
            before = storage._selected_table_projection_snapshot(connection, tables)
            columns = {name: value[0] for name, value in before.items()}
            project = connection.execute("SELECT project_id FROM tasks LIMIT 1").fetchone()[0]
            _, bundles = _bundle_artifacts(connection, project)
            with candidate_runtime(26):
                self.assertTrue(schema.migrate(connection))
                self.assertEqual(before, storage._selected_table_projection_snapshot(connection, tables, column_basis=columns))
                self.assertEqual(bundles, _bundle_artifacts(connection, project)[1])
                self.assertEqual({row[0] for row in connection.execute("SELECT policy_version FROM task_owner_transitions")}, {0})
                schema.validate_storage(connection)
                snapshot = _logical_snapshot(connection)
                connection.execute("PRAGMA query_only=ON")
                self.assertFalse(schema.migrate(connection))
                self.assertEqual(snapshot, _logical_snapshot(connection))
        with candidate_runtime(26):
            # All legacy holders remain readable; no implicit winner or demotion.
            for task in (*self.pending, self.active):
                result = payload(run_taskgov("task", "show", task, "--repo", str(self.repo), "--db", str(self.db), "--json"))
                self.assertTrue(result["ok"], result)
            self.edit(self.pending[0], "in_progress", code="session_task_in_progress")
            result = completion(self.db, self.repo, self.pending[0])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.edit(self.active, "cancelled")
            self.edit(self.pending[1], "in_progress")
            self.edit(self.pending[1], "review_pending")
            result = completion(self.db, self.repo, self.pending[1])
            self.assertEqual(result.returncode, 0, result.stdout)
            with closing(storage.connect(self.db)) as connection:
                storage.validate_current_database(connection, target_for(self.db, self.repo))
                self.assertEqual({row[0] for row in connection.execute("SELECT policy_version FROM task_owner_transitions")}, {0, 1})
                self.assertEqual(connection.execute("SELECT source_schema_version FROM completion_evidence_bundles ORDER BY rowid DESC LIMIT 1").fetchone()[0], 26)

    def test_copy_and_marker_failure_roll_back_all_policy_and_history(self):
        with _source_copy(self.db, factory=FailingCopy) as connection, candidate_runtime(26):
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)
        with _source_copy(self.db) as connection, candidate_runtime(26):
            connection.execute("CREATE TRIGGER unrelated_marker_change AFTER INSERT ON schema_migrations "
                "WHEN NEW.version=26 BEGIN UPDATE tasks SET title='changed by fixture trigger'; END")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)

    def test_policy_collision_and_ddl_drift_fail_closed(self):
        with _source_copy(self.db) as connection, candidate_runtime(26):
            connection.execute("ALTER TABLE task_owner_transitions ADD COLUMN policy_version INTEGER")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
        with _source_copy(self.db) as connection, candidate_runtime(26):
            schema.migrate(connection)
            connection.execute("DROP TRIGGER trg_task_ownership_slot_insert")
            connection.commit()
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)

    def test_old_runtime_rejects_new_schema_without_downgrade(self):
        with closing(storage.connect(self.db)) as connection, candidate_runtime(26):
            schema.migrate(connection)
        result = payload(run_taskgov("task", "show", self.active, "--repo", str(self.repo), "--db", str(self.db), "--json"))
        self.assertFalse(result["ok"], result)
        with closing(storage.connect(self.db)) as connection:
            self.assertEqual(storage.current_schema_version(connection), 26)
