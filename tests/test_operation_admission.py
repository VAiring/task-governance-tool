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

    def test_handoff_operations_ignore_unrelated_review_history(self):
        self.fixture.corrupt_other_binding()
        created = self.success("handoff", "record", self.task_id,
            "--summary", "Separate follow-up")["data"]["handoff"]
        replay = self.success("handoff", "record", self.task_id, "--summary", "Separate follow-up")
        self.assertTrue(replay["data"]["local_record"]["replayed"])
        self.success("handoff", "list")
        self.success("handoff", "show", created["handoff_id"])
        self.success("handoff", "withdraw", created["handoff_id"], "--reason", "User handled separately")
        with self.assertRaises(storage.StorageError):
            with closing(storage.connect_initialized(self.fixture.target)):
                pass

    def test_review_core_reads_validate_only_selected_history(self):
        from types import SimpleNamespace
        from task_governance_tool.review_finalization import Core, FinalizationError
        from task_governance_tool.session_identity import capture_caller_identity
        from task_governance_tool import review_finalization
        from task_governance_tool.tasks import read_internal_task
        self.fixture.corrupt_other_binding()
        core = Core(self.fixture.repo, self.fixture.target, capture_caller_identity())
        submission = SimpleNamespace(preparation_id="tg_review_preparation_0123456789abcdef",
            submission_digest="sha256:" + "0" * 64)
        from task_governance_tool import usage_evidence_service
        with mock.patch.object(usage_evidence_service, "read_task_usage", wraps=usage_evidence_service.read_task_usage) as usage:
            state = core.read(self.task_id)
            self.assertEqual(state["task"]["task_id"], self.task_id)
            self.assertNotIn("usage", state)
            with mock.patch.object(cli, "show_task", wraps=cli.show_task) as detail, \
                    mock.patch.object(review_finalization, "read_internal_task",
                                      wraps=read_internal_task) as inventory_task:
                self.assertEqual(core.collection_registration(self.task_id, submission)["status"], "absent")
                self.assertEqual(detail.call_count, 0)
                inventory_task.assert_called_once()
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


class PublicHandoffAdmissionTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.install = make_physical_install(root)
        result = self.install.run("setup", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.enterContext(mock.patch.object(cli, "cli_script_path", return_value=self.install.entrypoint))
        self.enterContext(mock.patch.dict(os.environ,
            {"CODEX_THREAD_ID": "00000000-0000-4000-8000-000000000001"}))
        self.task = registration_task(self.invoke("task", "add", "--title", "Handoff source",
            "--review-tier", "0", "--verification-not-required", "Isolated admission fixture"))
        self.task_id = self.task["task_id"]
        self.invoke("task", "edit", self.task_id, "--status", "in_progress")

    def invoke(self, *args, succeeds=True):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*args, "--repo", str(self.install.project_root), "--json"],
                _maintenance_enabled=False)
        self.assertEqual(code == 0, succeeds, out.getvalue() + err.getvalue())
        return json.loads(out.getvalue())

    def record(self, summary="Bounded follow-up"):
        return self.invoke("handoff", "record", self.task_id, "--summary", summary)["data"]["handoff"]

    def source_rows(self):
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            return (connection.execute("SELECT * FROM tasks WHERE task_id=?", (self.task_id,)).fetchall(),
                connection.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY task_event_id",
                                   (self.task_id,)).fetchall())

    def test_all_leaves_keep_semantic_work_bounded_as_unrelated_history_grows(self):
        from task_governance_tool import task_ownership, schema_task_event_notes
        from task_governance_tool import review_answer_repository, review_continuation_repository
        other = registration_task(self.invoke("task", "add", "--title", "Unrelated history",
            "--review-tier", "0", "--verification-not-required", "Isolated history"))
        before = self.source_rows()
        for growth in (0, 600):
            if growth:
                with closing(sqlite3.connect(self.install.db_path)) as connection:
                    connection.executemany("INSERT INTO task_events "
                        "(task_event_id,project_id,task_id,event_type,summary,created_at,note_body) "
                        "VALUES (?,?,?,'note_added','Retained note','2026-10-28T00:00:00Z','Unrelated note')",
                        [(f"tg_event_{index:016x}", self.task["project_id"], other["task_id"])
                         for index in range(growth)])
                    connection.commit()
            def forbidden(*args, **kwargs):
                raise AssertionError("Handoff entered an unrelated semantic-history audit")
            with mock.patch.object(storage, "validate_current_schema21_admitted_rows", side_effect=forbidden), \
                 mock.patch.object(task_ownership, "validate_storage_rows", side_effect=forbidden), \
                 mock.patch.object(schema_task_event_notes, "validate_note_rows", side_effect=forbidden), \
                 mock.patch.object(review_answer_repository, "validate_storage_rows", side_effect=forbidden), \
                 mock.patch.object(review_continuation_repository, "validate_judgment_storage", side_effect=forbidden):
                summary = f"Follow-up with {growth} unrelated notes"
                handoff = self.record(summary)
                replay = self.invoke("handoff", "record", self.task_id, "--summary", summary)
                self.assertTrue(replay["data"]["local_record"]["replayed"])
                database_before = self.install.db_path.read_bytes()
                listed = self.invoke("handoff", "list", "--read-only")["data"]
                shown = self.invoke("handoff", "show", handoff["handoff_id"], "--read-only")["data"]
                self.assertEqual((listed["count"], listed["total_matching"]), (1, 1))
                self.assertEqual(shown["handoff"], handoff)
                self.assertEqual(self.install.db_path.read_bytes(), database_before)
                self.invoke("handoff", "withdraw", handoff["handoff_id"], "--reason", "User handled")
                self.assertEqual(self.source_rows(), before)

    def test_consumed_privacy_is_rejected_by_read_replay_and_withdraw(self):
        handoff = self.record()
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            connection.execute("UPDATE handoff_records SET summary=? WHERE handoff_id=?",
                ("Authorization: Bearer secret-value", handoff["handoff_id"]))
            connection.commit()
        before = self.install.db_path.read_bytes()
        for args in (("handoff", "list"), ("handoff", "show", handoff["handoff_id"]),
                     ("handoff", "record", self.task_id, "--summary", "Bounded follow-up"),
                     ("handoff", "withdraw", handoff["handoff_id"], "--reason", "User handled")):
            result = self.invoke(*args, succeeds=False)
            self.assertEqual(result["errors"][0]["code"], "internal_error")
            self.assertNotIn("secret-value", json.dumps(result))
            self.assertEqual(self.install.db_path.read_bytes(), before)

    def test_owner_is_rechecked_for_replay_and_withdraw(self):
        handoff = self.record()
        before = self.install.db_path.read_bytes()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "00000000-0000-4000-8000-000000000002"}):
            for args in (("handoff", "record", self.task_id, "--summary", "Bounded follow-up"),
                         ("handoff", "withdraw", handoff["handoff_id"], "--reason", "User handled")):
                result = self.invoke(*args, succeeds=False)
                self.assertEqual(result["errors"][0]["code"], "task_not_owned")
                self.assertEqual(self.install.db_path.read_bytes(), before)

    def test_structural_faults_remain_fatal_for_all_leaves(self):
        handoff = self.record()
        original = self.install.db_path.read_bytes()
        for fault in ("schema", "history", "foreign_key", "binding"):
            with self.subTest(fault=fault):
                self.install.db_path.write_bytes(original)
                with closing(sqlite3.connect(self.install.db_path)) as connection:
                    if fault == "schema":
                        connection.execute("DROP INDEX idx_handoff_project_state_created")
                    elif fault == "history":
                        connection.execute("UPDATE schema_migrations SET name='invalid' WHERE version=1")
                    elif fault == "foreign_key":
                        connection.execute("UPDATE handoff_records SET source_task_id='tg_task_absent'")
                    else:
                        connection.execute("UPDATE project_meta SET canonical_path_hash=?", ("0" * 64,))
                    connection.commit()
                before = self.install.db_path.read_bytes()
                for args in (("handoff", "list"), ("handoff", "show", handoff["handoff_id"]),
                             ("handoff", "record", self.task_id, "--summary", "New follow-up"),
                             ("handoff", "withdraw", handoff["handoff_id"], "--reason", "User handled")):
                    self.invoke(*args, succeeds=False)
                    self.assertEqual(self.install.db_path.read_bytes(), before)

    def test_record_rejects_its_selected_contract_pointer(self):
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            connection.execute("UPDATE tasks SET current_contract_revision=1 WHERE task_id=?", (self.task_id,))
            connection.commit()
        before = self.install.db_path.read_bytes()
        self.invoke("handoff", "record", self.task_id, "--summary", "Invalid source", succeeds=False)
        self.assertEqual(self.install.db_path.read_bytes(), before)

    def test_writer_uses_fresh_ownership_after_connection_admission(self):
        from task_governance_tool import cli_handoff
        handoff = self.record()
        original = cli_handoff.begin_initialized_write

        def changed_owner(connection, target, **options):
            with closing(sqlite3.connect(target.db_path)) as other:
                other.execute("UPDATE task_ownership SET owner_session_id=? WHERE task_id=?",
                    ("00000000-0000-4000-8000-000000000002", self.task_id))
                other.commit()
            return original(connection, target, **options)

        with mock.patch.object(cli_handoff, "begin_initialized_write", side_effect=changed_owner):
            result = self.invoke("handoff", "withdraw", handoff["handoff_id"],
                "--reason", "User handled", succeeds=False)
        self.assertEqual(result["errors"][0]["code"], "task_not_owned")
        shown = self.invoke("handoff", "show", handoff["handoff_id"])["data"]["handoff"]
        self.assertEqual(shown, handoff)

    def test_selected_operations_read_ownership_once_and_replay_keeps_source(self):
        from task_governance_tool import task_ownership
        original = task_ownership.read_bases
        statements = []
        def observed(connection, **options):
            connection.set_trace_callback(statements.append)
            return original(connection, **options)
        operations = [("task", "list"), ("task", "current"),
            ("task", "checkpoint", self.task_id, "--summary", "Selected summary", "--next-action", "Continue"),
            ("task", "checkpoint", self.task_id, "--summary", "Selected summary", "--next-action", "Continue"),
            ("handoff", "record", self.task_id, "--summary", "Selected follow-up"),
            ("handoff", "record", self.task_id, "--summary", "Selected follow-up")]
        with mock.patch.object(task_ownership, "read_bases", side_effect=observed):
            for args in operations:
                statements.clear()
                self.invoke(*args)
                reads = [sql for sql in statements if "FROM tasks AS task JOIN task_ownership AS ownership" in sql]
                self.assertEqual(len(reads), 1, args)
                if args[1] == "checkpoint":
                    self.assertFalse(any("MAX(revision) AS max_revision" in sql for sql in statements))
        before = self.source_rows()
        self.invoke("handoff", "record", self.task_id, "--summary", "Selected follow-up")
        self.assertEqual(self.source_rows(), before)

    def test_selected_facts_reject_other_task_connection_writes_and_transaction_replacement(self):
        from task_governance_tool.stored_task_validation import _validated_task_facts
        from task_governance_tool import storage
        target = self.install.target
        for fault in ("task", "connection", "write", "transaction", "closed"):
            with self.subTest(fault=fault), closing(storage.connect(target.db_path)) as connection:
                connection.execute("BEGIN")
                row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (self.task_id,)).fetchone()
                with _validated_task_facts(connection, [row], project_id=self.task["project_id"]) as facts:
                    self.assertIsNotNone(facts.ownership(connection, self.task["project_id"], self.task_id))
                    if fault == "task":
                        with self.assertRaises(storage.StorageError):
                            facts.ownership(connection, self.task["project_id"], "tg_task_other")
                    elif fault == "connection":
                        with closing(storage.connect(target.db_path)) as other:
                            with self.assertRaises(storage.StorageError):
                                facts.ownership(other, self.task["project_id"], self.task_id)
                    elif fault == "write":
                        connection.execute("UPDATE tasks SET title='Changed selected Task' WHERE task_id=?", (self.task_id,))
                        with self.assertRaises(storage.StorageError):
                            facts.ownership(connection, self.task["project_id"], self.task_id)
                    elif fault == "transaction":
                        connection.rollback()
                        connection.execute("BEGIN")
                        with self.assertRaises(storage.StorageError):
                            facts.ownership(connection, self.task["project_id"], self.task_id)
                with self.assertRaises(storage.StorageError):
                    facts.ownership(connection, self.task["project_id"], self.task_id)
                connection.rollback()


if __name__ == "__main__":
    unittest.main()
