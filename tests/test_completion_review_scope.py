"""Completion validates its own review history, never another Task's reviews."""

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.test_task_complete import (
    add_task, complete_args, database_counts, init_db, run_taskgov,
    seed_review_evidence,
)
from task_governance_tool import storage
from tests.m14_test_support import make_physical_install
from tests.registration_output_support import registration_task
from tests.review_test_helpers import seed_review_evidence_connection


class CompletionReviewScopeTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.repo = Path(directory) / "repo"
        self.db = Path(directory) / "taskgov.sqlite"
        init_db(self.db, self.repo)
        self.other = add_task(self.db, self.repo, "Earlier independent work", "--review-tier", "2", "--status", "in_progress")
        seed_review_evidence(self.db, self.other["task_id"])
        completed = run_taskgov(*complete_args(self.db, self.repo, self.other["task_id"], "--json"))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.task = add_task(self.db, self.repo, "Current work", "--review-tier", "2", "--status", "in_progress")
        seed_review_evidence(self.db, self.task["task_id"])
        self.target = storage.resolve_database_target(repo=self.repo, db=self.db,
            script_path=Path(__file__).resolve().parents[1] / "task-governance-tool/scripts/taskgov.py")

    def corrupt_judgment(self, task_id, *, prior=None):
        # Restore exact DDL after modifying only disposable fixture rows.
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            receipt = connection.execute(
                "SELECT review_receipt_id FROM review_receipts WHERE task_id=? ORDER BY review_receipt_id LIMIT 1",
                (task_id,),
            ).fetchone()[0]
            trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='trg_review_receipt_judgments_no_update'",
            ).fetchone()[0]
            connection.execute("DROP TRIGGER trg_review_receipt_judgments_no_update")
            if prior is None:
                connection.execute("UPDATE review_receipt_judgments SET judgment_revision=2 WHERE review_receipt_id=?", (receipt,))
            else:
                connection.execute("UPDATE review_receipt_judgments SET prior_receipt_id=? WHERE review_receipt_id=?", (prior, receipt))
            connection.execute(trigger)
            connection.commit()
        return receipt

    def corrupt_other_binding(self):
        with closing(sqlite3.connect(self.db)) as connection:
            receipt = connection.execute("SELECT review_receipt_id FROM review_receipts WHERE task_id=? LIMIT 1",
                (self.other["task_id"],)).fetchone()[0]
            execution = connection.execute("SELECT execution_id FROM task_ownership WHERE task_id=?",
                (self.task["task_id"],)).fetchone()[0]
            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_review_receipt_sessions_owner_insert'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_review_receipt_sessions_owner_insert")
            connection.execute("INSERT INTO review_receipt_sessions VALUES (?, ?, ?, 'direct', NULL)",
                (receipt, "00000000-0000-4000-8000-000000000001", execution))
            connection.execute(trigger)
            connection.commit()
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        return receipt, execution

    def test_other_task_history_does_not_block_check_or_completion(self):
        receipt, execution = self.corrupt_other_binding()
        # Whole-store consumers must still detect this deliberately broken history.
        with self.assertRaises(storage.StorageError):
            with closing(storage.connect_initialized(self.target)):
                pass
        before = database_counts(self.db)
        checked = run_taskgov(*complete_args(self.db, self.repo, self.task["task_id"], "--check", "--json"))
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["data"]["ready"])
        self.assertEqual(database_counts(self.db), before)
        completed = run_taskgov(*complete_args(self.db, self.repo, self.task["task_id"], "--json"))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["data"]["task"]["status"], "done")
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute(
                "SELECT execution_id FROM review_receipt_sessions WHERE review_receipt_id=?", (receipt,),
            ).fetchone()[0], execution)

    def test_compatibility_done_does_not_validate_other_task_history(self):
        self.corrupt_other_binding()
        args = complete_args(self.db, self.repo, self.task["task_id"], "--json")
        completed = run_taskgov("task", "edit", *args[2:], "--status", "done")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["data"]["task"]["status"], "done")

    def test_padded_compatibility_done_keeps_the_same_review_scope(self):
        self.corrupt_other_binding()
        args = complete_args(self.db, self.repo, self.task["task_id"], "--json")
        completed = run_taskgov("task", "edit", *args[2:], "--status", " done ")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["data"]["task"]["status"], "done")

    def extra_v2_provenance(self, task_id, *, provenance_id="tg_review_provenance_abcdef0123456789"):
        with closing(sqlite3.connect(self.db)) as connection:
            receipt = connection.execute("SELECT review_receipt_id FROM review_receipts WHERE task_id=? LIMIT 1", (task_id,)).fetchone()[0]
            connection.execute("INSERT INTO review_receipt_provenance_v2 VALUES (?, ?, '{}', ?)",
                (receipt, provenance_id, "sha256:" + "0" * 64))
            connection.commit()
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_extra_v2_provenance_on_own_legacy_receipt_blocks_completion(self):
        self.extra_v2_provenance(self.task["task_id"])
        before = database_counts(self.db)
        for flags in (("--check", "--json"), ("--json",)):
            completed = run_taskgov(*complete_args(self.db, self.repo, self.task["task_id"], *flags))
            self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("evidence_ledger_inconsistent", completed.stdout)
            self.assertEqual(database_counts(self.db), before)

    def test_extra_v2_provenance_on_other_task_does_not_block_completion(self):
        self.extra_v2_provenance(self.other["task_id"])
        completed = run_taskgov(*complete_args(self.db, self.repo, self.task["task_id"], "--json"))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_foreign_v2_provenance_cannot_alias_own_legacy_provenance_id(self):
        with closing(sqlite3.connect(self.db)) as connection:
            provenance_id = connection.execute("SELECT review_provenance_id FROM review_receipts WHERE task_id=? LIMIT 1",
                (self.task["task_id"],)).fetchone()[0]
        self.extra_v2_provenance(self.other["task_id"], provenance_id=provenance_id)
        before = database_counts(self.db)
        completed = run_taskgov(*complete_args(self.db, self.repo, self.task["task_id"], "--json"))
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("evidence_ledger_inconsistent", completed.stdout)
        self.assertEqual(database_counts(self.db), before)

    def test_own_history_corruption_still_rejects_without_writes(self):
        self.corrupt_judgment(self.task["task_id"])
        before = database_counts(self.db)
        for extra in (("--check", "--json"), ("--json",)):
            result = run_taskgov(*complete_args(self.db, self.repo, self.task["task_id"], *extra))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("evidence_ledger_inconsistent", result.stdout)
            self.assertEqual(database_counts(self.db), before)

    def test_other_task_receipt_cannot_enter_own_judgment_chain(self):
        with closing(sqlite3.connect(self.db)) as connection:
            foreign = connection.execute("SELECT review_receipt_id FROM review_receipts WHERE task_id=? LIMIT 1",
                (self.other["task_id"],)).fetchone()[0]
        self.corrupt_judgment(self.task["task_id"], prior=foreign)
        before = database_counts(self.db)
        result = run_taskgov(*complete_args(self.db, self.repo, self.task["task_id"], "--json"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence_ledger_inconsistent", result.stdout)
        self.assertEqual(database_counts(self.db), before)

    def test_physical_installed_completion_does_not_recheck_predecessor_reviews(self):
        self.physical_completion()

    def test_physical_padded_done_does_not_recheck_predecessor_reviews(self):
        self.physical_completion(padded=True)

    def physical_completion(self, *, padded=False):
        install = make_physical_install(self.repo.parent / "physical")
        actor = "00000000-0000-4000-8000-000000000001"
        def invoke(*args):
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": actor}):
                result = install.run(*args, "--json")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        invoke("setup")
        self.repo, self.db = install.project_root, install.db_path
        for order in (1, 2):
            task = registration_task(invoke("task", "add", "--title", "Sequential review scope",
                "--kind", "sequential", "--lane", "scope", "--order", str(order),
                "--status", "in_progress", "--review-tier", "2",
                "--verification-not-required", "Isolated scope fixture"))
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": actor}), closing(sqlite3.connect(self.db)) as connection:
                seed_review_evidence_connection(connection, task["task_id"], repo_path=self.repo)
                connection.commit()
            if order == 1:
                self.other = task
                invoke("task", "complete", task["task_id"], "--verification-complete",
                    "--review-complete", "--commit-not-required")
            else:
                self.task = task
        self.corrupt_other_binding()
        # Public resolver admission must also be scoped, not just injected targets.
        checked = invoke("task", "complete", self.task["task_id"], "--check",
            "--verification-complete", "--review-complete", "--commit-not-required")
        self.assertTrue(checked["data"]["ready"])
        command = ("task", "edit", self.task["task_id"], "--status", " done ") if padded else ("task", "complete", self.task["task_id"])
        completed = invoke(*command, "--verification-complete", "--review-complete", "--commit-not-required")
        self.assertEqual(completed["data"]["task"]["status"], "done")


class CollectedCompletionScopeTests(unittest.TestCase):
    def test_own_collected_source_rejects_dispatch_labeled_as_other_task(self):
        from tests.review_answer_fixture_support import review_fixture
        from tests.verification_receipt_test_support import add_receipt
        fixture = self.enterContext(review_fixture(schema_version=31, contract=None))
        other = add_task(fixture.db, fixture.repo, "Unrelated dispatch owner")
        with closing(storage.connect_initialized(fixture.target)) as connection:
            for index in (1, 2):
                fixture.add(connection, index)
            connection.commit()
        verified = add_receipt(fixture.db, fixture.repo, fixture.task_id, fixture.basis["target_generation"])
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        with closing(sqlite3.connect(fixture.db)) as connection:
            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_review_collection_dispatches_no_update'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_review_collection_dispatches_no_update")
            connection.execute("UPDATE review_collection_dispatches SET task_id=? WHERE rowid=(SELECT min(rowid) FROM review_collection_dispatches)", (other["task_id"],))
            connection.execute(trigger)
            connection.commit()
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        before = database_counts(fixture.db)
        for flags in (("--check", "--json"), ("--json",)):
            completed = run_taskgov(*complete_args(fixture.db, fixture.repo, fixture.task_id, *flags))
            self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("evidence_ledger_inconsistent", completed.stdout)
            self.assertEqual(database_counts(fixture.db), before)


if __name__ == "__main__":
    unittest.main()
