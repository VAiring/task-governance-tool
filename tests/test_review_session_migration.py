from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from unittest import mock

from tests.verification_receipt_test_support import (
    initialize, add_task, seed_current_review_evidence, completion, run_taskgov, payload,
)
from tests.test_m242_r3b_schema20_activation import _SCHEMA20_RUNTIME_PATCH_TARGETS
from tests.test_m23s_schema22_migration import _logical_snapshot, _source_copy, _add_unrelated_objects
from tests.test_m23s_schema22_validation import _bundle_artifacts
from task_governance_tool import storage
from task_governance_tool import schema_review_sessions as schema


@contextmanager
def candidate_runtime(version=25):
    """Pin the test runtime explicitly for predecessor and current boundaries."""
    with ExitStack() as stack:
        for target in _SCHEMA20_RUNTIME_PATCH_TARGETS:
            stack.enter_context(mock.patch(target, version))
        yield


class FailingCopy(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO completion_evidence_bundles ("):
            raise sqlite3.OperationalError("test-only review session copy interruption")
        return super().execute(sql, parameters)


class ReviewSessionMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(candidate_runtime(24))
        self.repo, self.db = initialize(Path(temporary.name))
        self.done = add_task(self.db, self.repo, verification="",
            verification_not_required_reason="No executable change in fixture")["task_id"]
        seed_current_review_evidence(self.db, self.repo, self.done)
        result = completion(self.db, self.repo, self.done)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.active = add_task(self.db, self.repo, title="Active before binding", review_tier=2)["task_id"]
        seed_current_review_evidence(self.db, self.repo, self.active)

    def test_migration_preserves_rows_bundles_and_unbound_legacy_and_reentry(self):
        with closing(storage.connect(self.db)) as connection:
            _add_unrelated_objects(connection)
            tables = tuple(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name != 'schema_migrations' ORDER BY name"))
            before = storage._selected_table_projection_snapshot(connection, tables)
            project = connection.execute("SELECT project_id FROM tasks LIMIT 1").fetchone()[0]
            _, bundles_before = _bundle_artifacts(connection, project)
            with candidate_runtime():
                self.assertTrue(schema.migrate(connection))
                self.assertEqual(storage.current_schema_version(connection), 25)
                self.assertEqual(before, storage._selected_table_projection_snapshot(connection, tables))
                schema.validate_storage(connection)
                _, bundles_after = _bundle_artifacts(connection, project)
                self.assertEqual(bundles_before, bundles_after)
                self.assertEqual(connection.execute("SELECT count(*) FROM review_receipt_sessions").fetchone()[0], 0)
                migrated = _logical_snapshot(connection)
                connection.execute("PRAGMA query_only = ON")
                self.assertFalse(schema.migrate(connection))
                self.assertEqual(_logical_snapshot(connection), migrated)
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def test_copy_and_marker_failure_roll_back(self):
        with _source_copy(self.db, factory=FailingCopy) as connection, candidate_runtime():
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)
        with _source_copy(self.db) as connection, candidate_runtime():
            connection.execute("CREATE TRIGGER unrelated_marker_change AFTER INSERT ON schema_migrations "
                "WHEN NEW.version=25 BEGIN UPDATE tasks SET title='changed by fixture trigger'; END")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)

    def test_owned_object_collision_and_drift_fail_closed(self):
        with _source_copy(self.db) as connection, candidate_runtime():
            connection.execute("CREATE TABLE review_receipt_sessions(unrelated TEXT)")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
        with _source_copy(self.db) as connection, candidate_runtime():
            schema.migrate(connection)
            connection.execute("DROP TRIGGER trg_review_receipt_sessions_no_delete")
            connection.commit()
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)

    def test_existing_version24_reader_refuses_candidate_without_downgrade(self):
        with closing(storage.connect(self.db)) as connection, candidate_runtime():
            schema.migrate(connection)
        result = payload(run_taskgov("task", "show", self.active, "--repo", str(self.repo), "--db", str(self.db), "--json"))
        self.assertFalse(result["ok"], result)
        with closing(storage.connect(self.db)) as connection:
            self.assertEqual(storage.current_schema_version(connection), 25)


if __name__ == "__main__":
    unittest.main()
