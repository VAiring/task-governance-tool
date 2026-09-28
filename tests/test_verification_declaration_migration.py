from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from unittest import mock

from tests.verification_receipt_test_support import (
    initialize, run_taskgov, payload, seed_current_review_evidence, completion, add_receipt,
)
from tests.test_m242_r3b_schema20_activation import _SCHEMA20_RUNTIME_PATCH_TARGETS
from tests.test_m23s_schema22_migration import _logical_snapshot, _source_copy, _add_unrelated_objects
from tests.test_m23s_schema22_validation import _bundle_artifacts
from task_governance_tool import storage
from task_governance_tool import schema_verification_declaration as declaration


@contextmanager
def schema22_runtime():
    # Historical runtime oracle only; normal production admission still requires 23.
    with ExitStack() as stack:
        for target in _SCHEMA20_RUNTIME_PATCH_TARGETS:
            stack.enter_context(mock.patch(target, 22))
        yield


class FailingCopy(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO completion_evidence_bundles ("):
            raise sqlite3.OperationalError("test-only migration interruption")
        return super().execute(sql, parameters)


class VerificationDeclarationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        with schema22_runtime():
            self.repo, self.db = initialize(Path(self.temporary.name))
            self.done = self.add("Old blank done")
            seed_current_review_evidence(self.db, self.repo, self.done)
            result = completion(self.db, self.repo, self.done)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.ready = self.add("Old blank unfinished")
            self.manual = self.add("Old manual done", verification="Run checks")
            generation = seed_current_review_evidence(self.db, self.repo, self.manual)
            self.assertEqual(add_receipt(self.db, self.repo, self.manual, generation).returncode, 0)
            result = completion(self.db, self.repo, self.manual)
            self.assertEqual(result.returncode, 0, result.stdout)

    def call(self, *args):
        return payload(run_taskgov(*args, "--repo", str(self.repo), "--db", str(self.db), "--json"))

    def add(self, title, *, verification=""):
        result = self.call("task", "add", "--title", title, "--verification", verification,
                           "--review-tier", "0", "--status", "in_progress")
        self.assertTrue(result["ok"], result)
        return result["data"]["task"]["task_id"]

    def assert_connection_restored(self, connection):
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def test_old_done_bundles_preserved_reentry_and_reopen_requires_declaration(self):
        with closing(storage.connect(self.db)) as connection:
            _add_unrelated_objects(connection)
            project_id = connection.execute("SELECT project_id FROM tasks LIMIT 1").fetchone()[0]
            _, before = _bundle_artifacts(connection, project_id)
            self.assertTrue(declaration.migrate(connection))
            declaration.validate_storage(connection)
            _, after = _bundle_artifacts(connection, project_id)
            self.assertEqual(before, after)
            self.assertEqual({item.payload["source_schema_version"] for item in after.values()}, {22})
            self.assertEqual([row[0] for row in connection.execute("SELECT verification_not_required_reason FROM tasks")], ["", "", ""])
            self.assertTrue(all(row[0] is None for row in connection.execute("SELECT verification_not_required_reason FROM task_completion_cycles")))
            migrated = _logical_snapshot(connection)
            connection.execute("PRAGMA query_only = ON")
            self.assertFalse(declaration.migrate(connection))
            self.assertEqual(_logical_snapshot(connection), migrated)
            self.assert_connection_restored(connection)
        self.assertTrue(self.call("task", "show", self.done)["ok"])
        self.assertTrue(self.call("task", "show", self.manual)["ok"])
        reopened = self.call("task", "edit", self.done, "--status", "in_progress", "--reopen-reason", "Approved follow-up")
        self.assertTrue(reopened["ok"], reopened)
        for task_id in (self.done, self.ready):
            seed_current_review_evidence(self.db, self.repo, task_id)
            rejected = payload(completion(self.db, self.repo, task_id))
            self.assertEqual(rejected["errors"][0]["code"], "verification_requirement_unspecified")
        with schema22_runtime():
            self.assertFalse(self.call("task", "show", self.done)["ok"])
            with closing(storage.connect(self.db)) as connection:
                with self.assertRaises(storage.StorageError) as rejected:
                    storage.read_setup_state(connection, None)  # Version is checked before target access.
                self.assertEqual(rejected.exception.code, "schema_too_new")

    def test_copy_interruption_and_marker_injected_waiver_roll_back(self):
        with _source_copy(self.db, factory=FailingCopy) as connection:
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                declaration.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
            self.assert_connection_restored(connection)
        with _source_copy(self.db) as connection:
            connection.execute("CREATE TRIGGER unrelated_waiver AFTER INSERT ON schema_migrations WHEN NEW.version=23 BEGIN UPDATE tasks SET verification_not_required_reason='injected waiver' WHERE verification=''; END")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                declaration.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
            self.assert_connection_restored(connection)

    def test_current_schema_rejects_null_new_cycle_declaration(self):
        with closing(storage.connect(self.db)) as connection:
            declaration.migrate(connection)
        task_id = self.add("Native waiver")
        self.assertTrue(self.call("task", "edit", task_id, "--verification-not-required", "No executable change")["ok"])
        seed_current_review_evidence(self.db, self.repo, task_id)
        self.assertEqual(completion(self.db, self.repo, task_id).returncode, 0)
        with closing(storage.connect(self.db)) as connection:
            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_completion_cycles_no_update'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_task_completion_cycles_no_update")
            connection.execute("UPDATE task_completion_cycles SET verification_not_required_reason=NULL WHERE task_id=?", (task_id,))
            connection.execute(trigger)
            connection.commit()
            with self.assertRaises(storage.StorageError):
                declaration.validate_storage(connection)

    def test_old_cycle_cannot_acquire_a_post_hoc_declaration(self):
        with closing(storage.connect(self.db)) as connection:
            declaration.migrate(connection)
        for task_id, reason in ((self.done, "Retrospective waiver"), (self.manual, "")):
            with self.subTest(task_id=task_id), _source_copy(self.db) as connection:
                project_id = connection.execute("SELECT project_id FROM tasks LIMIT 1").fetchone()[0]
                trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_completion_cycles_no_update'").fetchone()[0]
                connection.execute("DROP TRIGGER trg_task_completion_cycles_no_update")
                connection.execute("UPDATE task_completion_cycles SET verification_not_required_reason=? WHERE task_id=?", (reason, task_id))
                connection.execute("UPDATE tasks SET verification_not_required_reason=? WHERE task_id=?", (reason, task_id))
                connection.execute(trigger)
                connection.commit()
                for recovery in (False, True):
                    with self.assertRaises(storage.StorageError):
                        declaration.validate_storage(connection, recovery=recovery)
                with self.assertRaises(storage.StorageError):
                    storage.capture_evidence_projection_basis(connection, project_id=project_id)

    def test_invalid_unselected_reason_is_global_and_not_recovery_local(self):
        with closing(storage.connect(self.db)) as connection:
            declaration.migrate(connection)
            connection.execute("UPDATE tasks SET verification_not_required_reason=? WHERE task_id=?",
                               ("Authorization: Bearer private-token", self.ready))
            connection.commit()
            for recovery in (False, True):
                with self.assertRaises(storage.StorageError) as rejected:
                    declaration.validate_storage(connection, recovery=recovery)
                self.assertNotIsInstance(rejected.exception, storage.StoredTaskVerificationError)
                self.assertNotIn("private-token", str(rejected.exception))
        selected = self.call("task", "context")
        self.assertFalse(selected["ok"])
        self.assertNotIn("private-token", str(selected))
