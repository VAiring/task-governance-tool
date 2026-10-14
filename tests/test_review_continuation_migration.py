"""Isolated schema delta preservation; public activation is verified separately."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.verification_receipt_test_support import initialize, add_task, seed_current_review_evidence, completion
from tests.test_m23s_schema22_migration import _source_copy, _logical_snapshot, _add_unrelated_objects
from tests.test_review_session_migration import candidate_runtime
from tests.test_m23s_schema22_validation import _bundle_artifacts
from task_governance_tool import storage
from task_governance_tool import schema_review_continuation as schema
from task_governance_tool.schema_task_event_notes import validate_storage as validate_predecessor


class InterruptedCopy(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if sql.startswith('INSERT INTO "task_completion_cycles" (rowid,'):
            raise sqlite3.OperationalError("isolated copy interruption")
        return super().execute(sql, parameters)


class ContinuationMigrationDeltaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-continuation-schema-")
        self.addCleanup(temporary.cleanup)
        self.enterContext(candidate_runtime(28))
        self.repo, self.db = initialize(Path(temporary.name))
        done = add_task(self.db, self.repo, verification="", verification_not_required_reason="Schema fixture has no executable change")
        seed_current_review_evidence(self.db, self.repo, done["task_id"])
        result = completion(self.db, self.repo, done["task_id"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        active = add_task(self.db, self.repo, title="Current before continuation", review_tier=2)
        seed_current_review_evidence(self.db, self.repo, active["task_id"])

    def begin(self, connection):
        validate_predecessor(connection)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")

    def test_complete_candidate_migration_reentry_and_old_bundle_bytes(self):
        with closing(storage.connect(self.db)) as connection:
            tables = tuple(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations' ORDER BY name"))
            before = storage._selected_table_projection_snapshot(connection, tables)
            columns = {name: projection[0] for name, projection in before.items()}
            project = connection.execute("SELECT project_id FROM tasks LIMIT 1").fetchone()[0]
            _, old_bundles = _bundle_artifacts(connection, project)
            with candidate_runtime(29):
                self.assertTrue(schema.migrate(connection))
                self.assertEqual(storage.current_schema_version(connection), 29)
                self.assertEqual(storage._selected_table_projection_snapshot(connection, tables, column_basis=columns), before)
                schema.validate_storage(connection)
                _, new_bundles = _bundle_artifacts(connection, project)
                self.assertEqual(new_bundles, old_bundles)
                snapshot = _logical_snapshot(connection)
                connection.execute("PRAGMA query_only=ON")
                self.assertFalse(schema.migrate(connection))
                self.assertEqual(_logical_snapshot(connection), snapshot)
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def test_exact_old_rows_sealed_evidence_and_unrelated_objects_preserved(self):
        with closing(storage.connect(self.db)) as connection:
            _add_unrelated_objects(connection)
            tables = tuple(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))
            before = storage._selected_table_projection_snapshot(connection, tables)
            columns = {name: projection[0] for name, projection in before.items()}
            rowids = tuple(tuple(row) for row in connection.execute("SELECT rowid,review_receipt_id FROM review_receipts ORDER BY rowid"))
            self.begin(connection)
            schema._apply_delta_locked(connection)
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tables, column_basis=columns), before)
            self.assertEqual(tuple(tuple(row) for row in connection.execute("SELECT rowid,review_receipt_id FROM review_receipts ORDER BY rowid")), rowids)
            self.assertEqual(connection.execute("SELECT count(*) FROM review_receipt_judgments WHERE judgment_revision=1 AND prior_receipt_id IS NULL AND continuation_id IS NULL").fetchone()[0], len(rowids))
            self.assertEqual(connection.execute("SELECT count(*) FROM review_continuations").fetchone()[0], 0)
            schema.validate_owned_contract(connection)
            storage._schema20_integrity_checks(connection)
            connection.commit()
            connection.execute("PRAGMA legacy_alter_table=OFF")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA query_only=ON")
            schema.validate_owned_contract(connection)

    def test_copy_failure_restores_every_old_object_and_row(self):
        with _source_copy(self.db, factory=InterruptedCopy) as connection:
            before = _logical_snapshot(connection)
            self.begin(connection)
            with self.assertRaises(sqlite3.OperationalError):
                schema._apply_delta_locked(connection)
            connection.rollback()
            connection.execute("PRAGMA legacy_alter_table=OFF")
            connection.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(_logical_snapshot(connection), before)
            validate_predecessor(connection)

    def test_unowned_rebuilt_table_attachment_fails_before_delta(self):
        for table in schema.REBUILT_TABLES:
            with self.subTest(table=table), _source_copy(self.db) as connection:
                connection.execute(f"CREATE INDEX unrelated_attachment ON {table}(task_id)")
                connection.commit()
                before = _logical_snapshot(connection)
                with self.assertRaises(storage.StorageError):
                    self.begin(connection)
                    schema._apply_delta_locked(connection)
                connection.rollback()
                self.assertEqual(_logical_snapshot(connection), before)

    def test_temporary_name_collision_retains_predecessor_and_residue(self):
        with _source_copy(self.db) as connection:
            connection.execute("CREATE TABLE review_receipts_v28(unrelated TEXT)")
            connection.commit()
            before = _logical_snapshot(connection)
            self.begin(connection)
            with self.assertRaises(storage.StorageError):
                schema._apply_delta_locked(connection)
            connection.rollback()
            self.assertEqual(_logical_snapshot(connection), before)


if __name__ == "__main__":
    unittest.main()
