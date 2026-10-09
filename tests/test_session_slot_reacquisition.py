"""Forward repair preserves applied26 and rejects complete legacy reacquisition."""

from contextlib import closing
from dataclasses import replace
import sqlite3
import unittest

from tests import test_session_slot_migration as fixtures
from tests.test_review_session_migration import candidate_runtime
from tests.test_m23s_schema22_migration import _logical_snapshot, _source_copy
from tests.verification_receipt_test_support import completion, target_for, payload, run_taskgov
from task_governance_tool import storage, task_ownership as ownership
from task_governance_tool import schema_session_slot as predecessor
from task_governance_tool import schema_session_slot_reacquisition as schema


class SessionSlotReacquisitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SessionSlotMigrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.db = self.fixture.repo, self.fixture.db
        with closing(storage.connect(self.db)) as connection, candidate_runtime(26):
            predecessor.migrate(connection)
        self.enterContext(candidate_runtime(27))

    def test_forward_migration_keeps_history_and_blocks_complete_reacquisition(self):
        with closing(storage.connect(self.db)) as connection:
            before = _logical_snapshot(connection)
            self.assertTrue(schema.migrate(connection))
            after = _logical_snapshot(connection)
            # The migration owns DDL and one marker, never Task/business rows.
            for table in before[1]:
                if table != "schema_migrations":
                    self.assertEqual(before[1][table], after[1][table])
            schema.validate_storage(connection)
            saved = _logical_snapshot(connection)
            connection.execute("PRAGMA query_only=ON")
            self.assertFalse(schema.migrate(connection))
            self.assertEqual(saved, _logical_snapshot(connection))
        self.fixture.edit(self.fixture.active, "cancelled")
        pending = self.fixture.pending
        self.fixture.edit(pending[0], "in_progress", code="session_task_in_progress")
        with closing(storage.connect(self.db)) as connection:
            target = target_for(self.db, self.repo)
            basis = ownership.read_bases(connection, project_id=target.project.project_id,
                                         task_ids=[pending[0]])[pending[0]]
            restored = replace(basis, status="in_progress", state="owned",
                generation=basis.generation + 1, owner_session_id=basis.completion_session_id,
                completion_session_id=None)
            snapshot = _logical_snapshot(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "session_task_in_progress"):
                    ownership._record(connection, basis, restored, basis.completion_session_id, storage.utc_now())
                    connection.execute("UPDATE tasks SET status='in_progress' WHERE task_id=?", (pending[0],))
                    storage.validate_current_database(connection, target)
            finally:
                connection.rollback()
            self.assertEqual(snapshot, _logical_snapshot(connection))
        # Completion releases one legacy holding; the remaining one can resume.
        finished = completion(self.db, self.repo, pending[0])
        self.assertEqual(finished.returncode, 0, finished.stdout)
        self.fixture.edit(pending[1], "in_progress")
        self.fixture.edit(pending[1], "review_pending")
        finished = completion(self.db, self.repo, pending[1])
        self.assertEqual(finished.returncode, 0, finished.stdout)
        with closing(storage.connect(self.db)) as connection:
            schema.validate_storage(connection)
            self.assertEqual(connection.execute("SELECT source_schema_version FROM completion_evidence_bundles ORDER BY rowid DESC LIMIT 1").fetchone()[0], 27)

    def test_native26_bundle_survives_and_old_writer_rejects27_without_write(self):
        with candidate_runtime(26):
            finished = completion(self.db, self.repo, self.fixture.pending[0])
            self.assertEqual(finished.returncode, 0, finished.stdout)
        with closing(storage.connect(self.db)) as connection:
            bundles = tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM completion_evidence_bundles ORDER BY rowid"))
            self.assertEqual(connection.execute("SELECT source_schema_version FROM completion_evidence_bundles ORDER BY rowid DESC LIMIT 1").fetchone()[0], 26)
            schema.migrate(connection)
            self.assertEqual(bundles, tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM completion_evidence_bundles ORDER BY rowid")))
            before = _logical_snapshot(connection)
        with candidate_runtime(26):
            result = payload(run_taskgov("task", "edit", self.fixture.active,
                "--status", "cancelled", "--repo", str(self.repo), "--db", str(self.db), "--json"))
        self.assertFalse(result["ok"], result)
        # This injected repository fixture bypasses the public resolver's
        # schema_too_new projection; its storage admission uses migration_required.
        self.assertIn("migration_required", [error["code"] for error in result["errors"]])
        with closing(storage.connect(self.db)) as connection:
            self.assertEqual(before, _logical_snapshot(connection))
            schema.validate_storage(connection)

    def test_copy_failure_and_marker_interference_restore_applied26(self):
        with _source_copy(self.db, factory=fixtures.FailingCopy) as connection:
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(before, _logical_snapshot(connection))
            predecessor.validate_storage(connection)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)
        with _source_copy(self.db) as connection:
            connection.execute("CREATE TRIGGER unrelated_marker_change AFTER INSERT ON schema_migrations "
                "WHEN NEW.version=27 BEGIN UPDATE tasks SET title='changed by fixture trigger'; END")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(before, _logical_snapshot(connection))

    def test_old_guard_or_wrong_fingerprint_never_passes_reentry(self):
        with closing(storage.connect(self.db)) as connection:
            schema.migrate(connection)
            connection.execute("DROP TRIGGER trg_task_ownership_slot_update")
            connection.execute(predecessor.policy_statements()[-1])
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(before, _logical_snapshot(connection))
