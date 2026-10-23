"""Real current-schema reopen boundaries on disposable, in-memory state."""

import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from task_governance_tool import storage, tasks
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.sqlite_connection import configure_connection
from tests.review_test_helpers import seed_review_evidence_connection


class LegacyReopenScopeTests(unittest.TestCase):
    def setUp(self):
        self.caller = CallerIdentity("00000000-0000-4000-8000-000000000001")
        self.enterContext(mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.caller.session_id}))
        self.db = configure_connection(sqlite3.connect(":memory:"))
        self.addCleanup(self.db.close)
        storage.apply_migrations(self.db)
        self.project = storage.ProjectIdentity(
            project_id="scope-000000000000",
            canonical_repo=Path("C:/legacy-scope-fixture-never-opened"),
            canonical_path_hash="0" * 64, display_name="scope",
        )
        storage.ensure_project_meta(self.db, self.project)
        storage.ensure_evidence_projection_state_row(self.db, project_id=self.project.project_id)
        storage.ensure_project_maintenance_row(self.db, self.project.project_id)
        storage.ensure_viewer_maintenance_row(self.db, self.project.project_id)
        self.db.commit()

    def add(self, *, active=False):
        task = tasks.add_task(
            self.db, self.project, title="Isolated legacy reopen", kind="optional",
            review_tier="0", status="in_progress" if active else "ready",
            verification_not_required_reason="Isolated storage fixture",
            caller=self.caller,
        ).task
        self.db.commit()
        return task["task_id"]

    def legacy(self):
        task_id = self.add()
        # Only seed the disposable fixture. Restore exact DDL before admission.
        name = "trg_tasks_completion_history_coverage_immutable"
        ddl = self.db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        self.db.execute(f"DROP TRIGGER {name}")
        self.db.execute(
            "UPDATE tasks SET status='done', completed_at='2026-10-23T00:00:00Z', "
            "updated_at='2026-10-23T00:00:00Z', completion_history_coverage='legacy_unknown' "
            "WHERE task_id=?", (task_id,),
        )
        self.db.execute(ddl)
        self.db.commit()
        return task_id

    def native(self):
        task_id = self.add(active=True)
        seed_review_evidence_connection(self.db, task_id, repo_path=self.project.canonical_repo)
        self.db.commit()
        tasks.edit_task(
            self.db, self.project, task_id, status="done", commit_not_required=True,
            verification_complete=True, review_complete=True,
            operation_local=True, caller=self.caller,
        )
        self.db.commit()
        return task_id

    def reopen(self, task_id):
        return tasks.edit_task(
            self.db, self.project, task_id, status="in_progress",
            reopen_reason="Continue isolated work", operation_local=True, caller=self.caller,
        )

    def dump(self):
        return tuple(self.db.iterdump())

    def generation(self):
        return self.db.execute("SELECT source_generation FROM evidence_projection_state").fetchone()[0]

    def assert_physical(self):
        self.assertEqual(self.db.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def corrupt_bundle(self, task_id):
        name = "trg_completion_evidence_bundles_no_update"
        ddl = self.db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        self.db.execute(f"DROP TRIGGER {name}")
        # Closed-range, FK-valid data with an intentionally wrong semantic mask.
        self.db.execute("UPDATE completion_evidence_bundles SET omission_mask=15-omission_mask WHERE task_id=?", (task_id,))
        self.db.execute(ddl)
        self.db.commit()

    def test_bridge_uses_no_global_bundle_audit_and_preserves_partial_basis(self):
        task_id = self.legacy()
        storage.validate_current_database_structure(self.db, self.project.project_id)
        before = self.generation()
        with mock.patch.object(storage, "_validate_completion_evidence_bundle_rows",
                               wraps=storage._validate_completion_evidence_bundle_rows) as audit:
            result = self.reopen(task_id)
            self.db.commit()
        self.assertEqual(audit.call_count, 0)
        self.assertEqual(result.task["status"], "in_progress")
        self.assertEqual(self.db.execute("SELECT review_target_generation FROM tasks WHERE task_id=?", (task_id,)).fetchone()[0], 1)
        self.assertEqual(self.generation(), before + 1)
        cycle = self.db.execute("SELECT * FROM task_completion_cycles WHERE task_id=?", (task_id,)).fetchone()
        self.assertEqual((cycle["origin"], cycle["completeness"], cycle["saved_cycle_ordinal"]),
                         ("legacy_current_done", "partial", 1))
        self.assertEqual((cycle["gate_basis_version"], cycle["verification_basis_version"],
                          cycle["evidence_basis_version"], cycle["verification_attestation"],
                          cycle["verification_receipt_id"], cycle["completion_evidence_bundle_id"]),
                         (0, 0, 0, None, None, None))
        event = self.db.execute("SELECT * FROM task_events WHERE event_type='task_reopened'").fetchone()
        self.assertEqual((event["task_id"], event["completion_cycle_id"]), (task_id, cycle["completion_cycle_id"]))
        owner = self.db.execute("SELECT * FROM task_ownership WHERE task_id=?", (task_id,)).fetchone()
        self.assertEqual((owner["state"], owner["owner_session_id"]), ("owned", self.caller.session_id))
        self.assertIsNotNone(owner["execution_id"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM task_execution_cycles").fetchone()[0], 0)
        self.assertEqual(storage.read_completion_history(self.db, project_id=self.project.project_id,
                         task_id=task_id).legacy_history_incomplete, True)
        from task_governance_tool.reviews import ReviewEvidenceError, enforce_review_gate
        with self.assertRaises(ReviewEvidenceError):
            enforce_review_gate(self.db, project_id=self.project.project_id, task_id=task_id, review_tier=0)
        storage.validate_current_database_structure(self.db, self.project.project_id)
        self.assert_physical()

    def test_unrelated_bundle_corruption_is_isolated_but_global_consumer_rejects(self):
        other = self.native()
        task_id = self.legacy()
        storage.validate_current_database_structure(self.db, self.project.project_id)
        storage.validate_completion_evidence_bundle_storage(self.db)
        self.corrupt_bundle(other)
        self.assert_physical()
        with self.assertRaises(storage.StorageError):
            storage.validate_completion_evidence_bundle_storage(self.db)
        with mock.patch.object(storage, "_validate_completion_evidence_bundle_rows",
                               wraps=storage._validate_completion_evidence_bundle_rows) as audit:
            self.assertEqual(self.reopen(task_id).task["status"], "in_progress")
            self.db.commit()
        self.assertEqual(audit.call_count, 0)
        with self.assertRaises(storage.StorageError):
            storage.validate_completion_evidence_bundle_storage(self.db)
        self.assert_physical()

    def test_own_bundle_corruption_still_rejects_reopen_without_writes(self):
        task_id = self.native()
        self.corrupt_bundle(task_id)
        self.assert_physical()
        before = self.dump()
        with self.assertRaises(storage.StorageError):
            self.reopen(task_id)
        self.assertEqual(self.dump(), before)
        self.db.commit()

    def test_default_legacy_insertion_retains_global_audit(self):
        task_id = self.legacy()
        self.db.execute("BEGIN IMMEDIATE")
        with mock.patch.object(storage, "_validate_completion_evidence_bundle_rows",
                               wraps=storage._validate_completion_evidence_bundle_rows) as audit:
            storage.insert_completion_cycle_locked(self.db, project_id=self.project.project_id,
                task_id=task_id, recorded_at="2026-10-23T01:00:00Z")
        self.assertEqual(audit.call_count, 1)
        self.db.rollback()

    def test_existing_native_and_legacy_cycles_are_reused(self):
        for origin in ("native", "legacy"):
            with self.subTest(origin=origin):
                task_id = self.native() if origin == "native" else self.legacy()
                if origin == "legacy":
                    self.db.execute("BEGIN IMMEDIATE")
                    storage.insert_completion_cycle_locked(self.db, project_id=self.project.project_id,
                        task_id=task_id, recorded_at="2026-10-23T01:00:00Z")
                    self.db.commit()
                before = self.generation()
                cycle_ids = tuple(self.db.execute("SELECT completion_cycle_id FROM task_completion_cycles").fetchall())
                self.reopen(task_id)
                self.db.commit()
                self.assertEqual(self.generation(), before)
                self.assertEqual(tuple(self.db.execute("SELECT completion_cycle_id FROM task_completion_cycles").fetchall()), cycle_ids)
                tasks.edit_task(self.db, self.project, task_id, status="ready",
                                operation_local=True, caller=self.caller)
                self.db.commit()
        storage.validate_current_database_structure(self.db, self.project.project_id)

    def test_missing_cycle_with_complete_coverage_and_projection_mismatch_reject(self):
        task_id = self.legacy()
        self.db.execute("BEGIN IMMEDIATE")
        storage.insert_completion_cycle_locked(self.db, project_id=self.project.project_id,
            task_id=task_id, recorded_at="2026-10-23T01:00:00Z")
        self.db.execute("UPDATE tasks SET completed_at='2026-10-23T00:01:00Z' WHERE task_id=?", (task_id,))
        self.db.commit()
        before = self.dump()
        with self.assertRaises(storage.StorageError) as rejected:
            self.reopen(task_id)
        self.assertEqual(rejected.exception.code, "completion_history_inconsistent")
        self.db.rollback()
        self.assertEqual(self.dump(), before)

        missing = self.add()
        self.db.execute("UPDATE tasks SET status='done', completed_at='2026-10-23T00:00:00Z' WHERE task_id=?", (missing,))
        self.db.commit()
        before = self.dump()
        with self.assertRaises(storage.StorageError) as rejected:
            self.reopen(missing)
        self.assertEqual(rejected.exception.code, "completion_history_inconsistent")
        self.db.rollback()
        self.assertEqual(self.dump(), before)

    def test_late_failures_roll_back_cycle_generation_event_and_ownership(self):
        task_id = self.legacy()
        before = self.dump()
        for target in ("task_governance_tool.tasks.update_task_row",
                       "task_governance_tool.effort.record_task_transition"):
            with self.subTest(target=target), mock.patch(target, side_effect=RuntimeError("late failure")) as failed:
                with self.assertRaisesRegex(RuntimeError, "late failure"):
                    self.reopen(task_id)
                failed.assert_called_once()
                # Savepoint rollback must suffice; do not hide a partial write
                # behind a test-owned rollback of the whole transaction.
                self.assertEqual(self.dump(), before)
                self.db.commit()
        storage.validate_current_database_structure(self.db, self.project.project_id)
        self.assert_physical()

    def test_source_generation_overflow_rolls_back_bridge(self):
        task_id = self.legacy()
        self.db.execute("UPDATE evidence_projection_state SET source_generation=?", (storage.SQLITE_INT64_MAX,))
        self.db.commit()
        before = self.dump()
        with self.assertRaises(storage.StorageError) as rejected:
            self.reopen(task_id)
        self.assertEqual(rejected.exception.code, "evidence_ledger_inconsistent")
        self.assertEqual(self.dump(), before)
        self.db.commit()
        self.assert_physical()


if __name__ == "__main__":
    unittest.main()
