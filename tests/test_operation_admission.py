"""Ordinary Task admission consumes selected data rather than global histories."""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests import test_completion_review_scope as scope_fixture
from tests.m14_test_support import make_physical_install
from tests.registration_output_support import registration_task
from tests.test_task_complete import complete_args, database_counts, run_taskgov
from task_governance_tool import cli, storage


class OperationAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = scope_fixture.CompletionReviewScopeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task_id = self.fixture.task["task_id"]

    def run_task(self, *args):
        return run_taskgov(*args, "--repo", str(self.fixture.repo),
            "--db", str(self.fixture.db), "--json")

    def success(self, *args):
        result = self.run_task(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_start_metadata_and_checkpoint_ignore_unrelated_review_history(self):
        self.fixture.corrupt_other_binding()
        self.success("task", "edit", self.task_id, "--status", "ready")
        started = self.success("task", "edit", self.task_id, "--status", "in_progress")
        self.assertEqual(started["data"]["task"]["status"], "in_progress")
        self.success("task", "edit", self.task_id, "--title", "Selected task edited")
        self.success("task", "checkpoint", self.task_id,
            "--summary", "Selected checkpoint", "--next-action", "Continue selected task")
        with self.assertRaises(storage.StorageError):
            with closing(storage.connect_initialized(self.fixture.target)):
                pass

    def test_normal_read_composition_ignores_unrelated_review_history(self):
        self.fixture.corrupt_other_binding()
        before = database_counts(self.fixture.db)
        for operation in ("context", "show", "current", "next", "list"):
            with self.subTest(operation=operation):
                args = ("task", operation, self.task_id) if operation == "show" else ("task", operation)
                self.success(*args)
                self.assertEqual(database_counts(self.fixture.db), before)

    def test_review_core_reads_validate_only_selected_history(self):
        from types import SimpleNamespace
        from task_governance_tool.review_finalization import Core, FinalizationError
        from task_governance_tool.session_identity import capture_caller_identity
        self.fixture.corrupt_other_binding()
        core = Core(self.fixture.repo, self.fixture.target, capture_caller_identity())
        submission = SimpleNamespace(preparation_id="tg_review_preparation_0123456789abcdef",
            submission_digest="sha256:" + "0" * 64)
        from task_governance_tool import usage_evidence_service
        with mock.patch.object(usage_evidence_service, "read_task_usage", wraps=usage_evidence_service.read_task_usage) as usage:
            state = core.read(self.task_id)
            self.assertEqual(state["task"]["task_id"], self.task_id)
            self.assertNotIn("usage", state)
            with mock.patch.object(cli, "show_task", wraps=cli.show_task) as detail:
                self.assertEqual(core.collection_registration(self.task_id, submission)["status"], "absent")
                self.assertEqual(detail.call_count, 0)
            self.assertEqual(usage.call_count, 0)
            for operation in ("show", "context"):
                args = ("task", operation, self.task_id) if operation == "show" else ("task", operation)
                self.success(*args)
            self.assertEqual(usage.call_count, 2)
        self.success("task", "show", self.task_id)
        self.fixture.extra_v2_provenance(self.task_id)
        with self.assertRaises(FinalizationError) as rejected:
            core.read(self.task_id)
        self.assertEqual(rejected.exception.code, "evidence_ledger_inconsistent")
        with self.assertRaises(storage.StorageError) as inventory_rejected:
            core.collection_registration(self.task_id, submission)
        self.assertEqual(inventory_rejected.exception.code, "evidence_ledger_inconsistent")

    def test_review_target_packet_result_and_finding_writes_ignore_unrelated_history(self):
        from tests.test_review_results import BinaryInput, encode, receipt
        self.fixture.corrupt_other_binding()
        self.success("review", "target", "set", self.task_id,
            "--kind", "diff_fingerprint", "--revision", "sha256:" + "d" * 64)
        packet = self.success("review", "prepare", self.task_id)["data"]
        document = packet["result_template"]
        document["receipts"] = [receipt("local-a", findings=(
            {"severity": "low", "summary": "Scoped follow-up"},)), receipt("local-b")]
        with mock.patch.object(cli.sys, "stdin", BinaryInput(encode(document))):
            result = self.success("review", "result", "add", self.task_id)["data"]
        self.assertTrue(result["review_gate"]["satisfied"])
        finding = result["receipts"][0]["findings"][0]["finding"]["review_finding_id"]
        resolved = self.success("review", "finding", "resolve", finding,
            "--resolution", "Confirmed isolated correction")
        self.assertEqual(resolved["data"]["finding"]["status"], "resolved")
        with self.assertRaises(storage.StorageError):
            with closing(storage.connect_initialized(self.fixture.target)):
                pass

    def test_verification_receipt_and_packet_ignore_unrelated_history(self):
        self.fixture.corrupt_other_binding()
        self.success("task", "edit", self.task_id, "--verification", "Focused isolated checks")
        target = self.success("review", "target", "set", self.task_id,
            "--kind", "diff_fingerprint", "--revision", "sha256:" + "e" * 64)["data"]["task"]
        recorded = self.success("verification", "receipt", "add", self.task_id,
            "--result", "pass", "--duration-ms", "1", "--scope-coverage", "full",
            "--expected-target-generation", str(target["review_target_generation"]))
        self.assertTrue(recorded["ok"])
        packet = self.success("review", "prepare", self.task_id)["data"]
        self.assertTrue(packet["verification_evidence"]["gate"]["satisfied"])

    def test_review_writer_rechecks_own_history_after_preflight(self):
        from tests.test_review_results import BinaryInput, encode, receipt
        from task_governance_tool import reviews
        packet = self.success("review", "prepare", self.task_id)["data"]
        document = packet["result_template"]
        document["receipts"] = [receipt("late-scoped-review")]
        with closing(sqlite3.connect(self.fixture.db)) as connection:
            before = connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0]
        original = reviews.begin_initialized_write
        def race(connection, target, **options):
            self.fixture.extra_v2_provenance(self.task_id)
            return original(connection, target, **options)
        with mock.patch.object(cli.sys, "stdin", BinaryInput(encode(document))), \
             mock.patch.object(reviews, "begin_initialized_write", side_effect=race):
            result = self.run_task("review", "result", "add", self.task_id)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence_ledger_inconsistent", result.stdout)
        with closing(sqlite3.connect(self.fixture.db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0], before)

    def test_own_detail_corruption_still_rejects(self):
        self.fixture.extra_v2_provenance(self.task_id)
        before = database_counts(self.fixture.db)
        for operation in ("show", "context"):
            args = ("task", operation, self.task_id) if operation == "show" else ("task", operation)
            result = self.run_task(*args)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("evidence_ledger_inconsistent", result.stdout)
            self.assertEqual(database_counts(self.fixture.db), before)

    def test_unrelated_note_history_does_not_block_completion_or_detail(self):
        with closing(sqlite3.connect(self.fixture.db)) as connection:
            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_events_note_body_immutable'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_task_events_note_body_immutable")
            connection.execute("UPDATE task_events SET note_body='Invalid retained note' WHERE task_id=? AND event_type='task_added'",
                (self.fixture.other["task_id"],))
            connection.execute(trigger)
            connection.commit()
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        # The note's event type is invalid, although its physical relations are valid.
        self.success("task", "show", self.task_id)
        result = run_taskgov(*complete_args(self.fixture.db, self.fixture.repo, self.task_id, "--json"))
        self.assertEqual(result.returncode, 0, result.stdout)
        with self.assertRaises(storage.StorageError):
            with closing(storage.connect_initialized(self.fixture.target)):
                pass


class EffortOperationAdmissionTests(unittest.TestCase):
    def test_enabled_existing_basis_keeps_fresh_generation_read_local(self):
        from tests.test_effort_advisory import initialize_db, initialize_git_repo, write_profile
        from task_governance_tool.effort import build_effort_advisory
        from task_governance_tool.session_identity import capture_caller_identity
        from task_governance_tool.tasks import add_task

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            initialize_git_repo(repo)
            profile = write_profile(root / "skill")
            target = initialize_db(root / "taskgov.sqlite", repo)
            with closing(storage.connect_initialized(target)) as connection:
                with connection:
                    added = add_task(connection, target.project, title="Enabled effort",
                        status="in_progress", effort_profile=profile,
                        caller=capture_caller_identity())
            task_id = added.task["task_id"]
            before = target.db_path.read_bytes()
            with mock.patch.object(storage, "validate_current_schema21_admitted_rows",
                    side_effect=AssertionError("Effort must not audit unrelated histories")):
                with closing(storage.connect_initialized_readonly(target, operation_local=True)) as connection:
                    result = build_effort_advisory(connection, target.project, task_id,
                        profile, database_target=target)
            self.assertEqual(result.data["basis"]["status"], "captured")
            self.assertEqual(result.data["attribution"], "exclusive_task_window")
            self.assertEqual(result.data["unknown_reasons"], [])
            self.assertEqual(target.db_path.read_bytes(), before)


class PublicOperationAdmissionTests(unittest.TestCase):
    def test_public_task_paths_do_not_run_global_semantic_validators(self):
        from task_governance_tool import task_ownership, schema_task_event_notes
        from task_governance_tool import review_answer_repository, review_continuation_repository
        with tempfile.TemporaryDirectory() as directory:
            install = make_physical_install(Path(directory))
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            def invoke(*args):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main([*args, "--repo", str(install.project_root), "--json"],
                        _maintenance_enabled=False)
                self.assertEqual(code, 0, out.getvalue() + err.getvalue())
                return json.loads(out.getvalue())
            def forbidden(*args, **kwargs):
                raise AssertionError("Unrelated global semantic traversal")
            original_answers = review_answer_repository.validate_storage_rows
            original_judgments = review_continuation_repository.validate_judgment_storage
            def selected_answers(connection, *, review_task=None):
                self.assertIsNotNone(review_task)
                return original_answers(connection, review_task=review_task)
            def selected_judgments(connection, *, review_task=None):
                self.assertIsNotNone(review_task)
                return original_judgments(connection, review_task=review_task)
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "00000000-0000-4000-8000-000000000001"}), \
                 mock.patch.object(cli, "cli_script_path", return_value=install.entrypoint), \
                 mock.patch.object(storage, "validate_current_schema21_admitted_rows", side_effect=forbidden), \
                 mock.patch.object(task_ownership, "validate_storage_rows", side_effect=forbidden), \
                 mock.patch.object(schema_task_event_notes, "validate_note_rows", side_effect=forbidden), \
                 mock.patch.object(review_answer_repository, "validate_storage_rows", side_effect=selected_answers), \
                 mock.patch.object(review_continuation_repository, "validate_judgment_storage", side_effect=selected_judgments):
                task = registration_task(invoke("task", "add", "--title", "Public selected operation",
                    "--review-tier", "0", "--verification-not-required", "Isolated admission fixture"))
                task_id = task["task_id"]
                invoke("task", "edit", task_id, "--status", "in_progress")
                invoke("task", "edit", task_id, "--title", "Updated public operation")
                invoke("task", "checkpoint", task_id, "--summary", "Selected checkpoint", "--next-action", "Continue")
                for operation in ("context", "show", "current", "next", "list"):
                    invoke("task", operation, *([task_id] if operation == "show" else []))
                from tests.review_test_helpers import seed_review_evidence_connection
                with closing(sqlite3.connect(install.db_path)) as connection:
                    seed_review_evidence_connection(connection, task_id, repo_path=install.project_root)
                    connection.commit()
                invoke("task", "complete", task_id, "--verification-complete", "--review-complete", "--commit-not-required")


if __name__ == "__main__":
    unittest.main()
