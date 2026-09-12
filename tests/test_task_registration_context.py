from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import file_snapshot, initialize_taskgov_internal, run_taskgov_internal
from tests.test_task_context import run
from tests.test_task_batch import BinaryInput, document, encode
from task_governance_tool import cli
from task_governance_tool.cli_output import CommandResult
from task_governance_tool.storage import StorageError


class RegistrationContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.db = self.root / "state.sqlite"
        initialize_taskgov_internal(repo=self.repo, db=self.db)

    def invoke(self, *args):
        result = run(self.db, self.repo, *args)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        return json.loads(result.stdout)

    def add(self, title="New work", *args):
        return self.invoke("task", "add", "--title", title, *args)

    def batch(self):
        with mock.patch.object(sys, "stdin", BinaryInput(encode(document(tasks=[
            {"title": "Batch A", "contract": None},
            {"title": "Batch B", "contract": None},
        ])))):
            return self.invoke("task", "add", "--from-stdin")

    def assert_context(self, payload, selected_id=None):
        before = file_snapshot(self.root)
        standalone = self.invoke("task", "context")
        self.assertEqual(file_snapshot(self.root), before)
        prepared = payload["data"]["context_preparation"]
        self.assertEqual(prepared, {
            "status": "ready", "context": standalone["data"], "errors": [],
        })
        self.assertEqual(payload["warnings"], standalone["warnings"])
        if selected_id:
            self.assertEqual(prepared["context"]["selected"]["task"]["task_id"], selected_id)
        return prepared["context"]

    def test_single_complete_contract_and_gates_equal_standalone_without_start(self):
        added = self.add("Scoped work", "--contract-scope", "One outcome",
                         "--contract-acceptance", "Focused checks pass",
                         "--contract-authority-ref", "conversation:approved",
                         "--review-tier", "2", "--verification", "Focused checks")
        context = self.assert_context(added, added["data"]["task"]["task_id"])
        self.assertEqual(context["selected"]["contract"]["revision"], 1)
        self.assertEqual(context["selected"]["task"]["status"], "ready")
        self.assertFalse(context["selected"]["review_evidence"]["gate"]["satisfied"])
        self.assertFalse(context["selected"]["verification_evidence"]["gate"]["satisfied"])

    def test_single_and_batch_keep_existing_active_selection(self):
        active = self.add("Existing active", "--status", "in_progress")["data"]["task"]
        for payload in (self.add(), self.batch()):
            self.assert_context(payload, active["task_id"])

    def test_batch_prepares_once_and_does_not_duplicate_context_per_item(self):
        with mock.patch.object(cli, "handle_task_context", wraps=cli.handle_task_context) as prepare:
            payload = self.batch()
        prepare.assert_called_once()
        self.assertEqual(set(payload["data"]), {"tasks", "context_preparation"})
        for item in payload["data"]["tasks"]:
            self.assertNotIn("context_preparation", item)
        self.assert_context(payload)

    def test_earlier_ready_and_held_work_selection_and_warnings_preserved(self):
        held = self.add("Held", "--status", "in_progress")["data"]["task"]["task_id"]
        self.invoke("task", "edit", held, "--status", "paused", "--pause-reason", "Waiting")
        urgent = self.add("Earlier candidate", "--priority", "urgent")["data"]["task"]["task_id"]
        payload = self.add()
        context = self.assert_context(payload, urgent)
        self.assertEqual(context["selection"], "next")
        self.assertTrue(any(w["code"] == "paused_tasks_present" for w in payload["warnings"]))

    def test_successful_absence_is_ready_not_failed(self):
        added = self.add("Blocked", "--status", "blocked", "--blocked-reason", "Waiting")
        context = self.assert_context(added)
        self.assertEqual(context["selection"], "none")
        self.assertIsNone(context["selected"])

    def test_context_read_starts_after_writer_commit_and_close(self):
        original = cli.handle_task_context

        def inspect(context):
            self.assertTrue(context.read_only)
            self.assertIsNotNone(context.read_connection_override)
            self.assertEqual(context.read_connection_override.execute("PRAGMA query_only").fetchone()[0], 1)
            with closing(sqlite3.connect(self.db, timeout=0)) as other:
                other.execute("BEGIN IMMEDIATE")
                self.assertEqual(other.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)
                other.rollback()
            return original(context)

        with mock.patch.object(cli, "handle_task_context", side_effect=inspect):
            payload = self.add()
        self.assertEqual(payload["data"]["context_preparation"]["status"], "ready")

    def test_global_admission_failure_keeps_committed_registration(self):
        with mock.patch.object(cli, "connect_initialized_readonly", side_effect=StorageError(
            "project_state_unreadable", "stored state is invalid",
        )) as admission, mock.patch.object(cli, "handle_task_context") as composition:
            payload = self.add()
        admission.assert_called_once()
        composition.assert_not_called()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["data"]["context_preparation"], {
            "status": "failed", "context": None,
            "errors": [{"code": "project_state_unreadable", "message": "stored state is invalid"}],
        })
        recovered = self.invoke("task", "context")
        self.assertEqual(recovered["data"]["selected"]["task"]["task_id"], payload["data"]["task"]["task_id"])
        self.assertEqual(self.invoke("task", "list")["data"]["count"], 1)

    def test_component_failure_has_no_partial_context_or_warnings_and_batch_is_committed(self):
        failure = CommandResult(ok=False, command="task.context",
            data={"selected": {"partial": True}}, warnings=[{"code": "partial"}],
            errors=[{"code": "project_state_unreadable", "message": "stored state is invalid"}], exit_code=2)
        with mock.patch.object(cli, "handle_task_context", return_value=failure):
            payload = self.batch()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["data"]["context_preparation"]["status"], "failed")
        self.assertIsNone(payload["data"]["context_preparation"]["context"])
        self.assertEqual(self.invoke("task", "list")["data"]["count"], 2)

    def test_unexpected_read_failure_does_not_leak_exception_or_invite_retry(self):
        with mock.patch.object(cli, "handle_task_context", side_effect=RuntimeError("private input bytes")):
            payload = self.add()
        self.assertNotIn("private input bytes", json.dumps(payload))
        self.assertEqual(payload["data"]["context_preparation"]["errors"], [
            {"code": "internal_error", "message": "could not prepare task context"},
        ])
        self.assertEqual(self.invoke("task", "list")["data"]["count"], 1)

    def test_failed_registration_does_not_read_context_or_write(self):
        before = file_snapshot(self.root)
        with mock.patch.object(cli, "prepare_registered_task_context") as prepare:
            result = run(self.db, self.repo, "task", "add", "--title", "Invalid", "--status", "done")
        self.assertNotEqual(result.returncode, 0)
        prepare.assert_not_called()
        self.assertNotIn("context_preparation", json.loads(result.stdout)["data"])
        self.assertEqual(file_snapshot(self.root), before)

    def test_text_failure_distinguishes_committed_registration(self):
        with mock.patch.object(cli, "handle_task_context", side_effect=RuntimeError("private failure")):
            result = run_taskgov_internal(
                "task", "add", "--title", "Text registration", "--repo", str(self.repo),
                "--db", str(self.db), maintenance_enabled=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.assertIn("registration committed", result.stdout)
        self.assertIn("Retry task context, not task add", result.stdout)
        self.assertNotIn("private failure", result.stdout)

    def test_lost_response_recovery_inspects_ids_without_replaying_add(self):
        with mock.patch.object(cli, "add_task", wraps=cli.add_task) as write:
            self.add("Unique accepted outcome")  # Deliberately discard response.
            rows = self.invoke("task", "list")["data"]["tasks"]
            context = self.invoke("task", "context")["data"]
        write.assert_called_once()
        self.assertEqual(len(rows), 1)
        self.assertEqual(context["selected"]["task"]["task_id"], rows[0]["task_id"])

    def test_combined_output_is_smaller_than_replaced_add_and_context_envelopes(self):
        for payload in (self.add(), self.batch()):
            with self.subTest(batch="tasks" in payload["data"]):
                separate = self.invoke("task", "context")
                old_add = {**payload, "data": dict(payload["data"]), "warnings": []}
                prepared = old_add["data"].pop("context_preparation")
                # First iteration's selection predates the second registration;
                # use the exact read returned at that registration boundary.
                separate = {**separate, "data": prepared["context"], "warnings": payload["warnings"]}
                size = lambda obj: len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()) + 1
                self.assertLess(size(payload), size(old_add) + size(separate))
                # Remaining identical target/verify/review/complete outputs
                # cancel in the whole-loop comparison; no token claim follows.


class RegistrationRunnerContextTests(unittest.TestCase):
    def test_existing_marker_two_uses_same_show_route_after_registration(self):
        from tests.test_m242_runner_service import RunnerServiceFixture
        from tests.test_m243c_runner_gate import _launch, _persist_terminal
        from task_governance_tool import verification_runner_selection as selection

        for branch in ("pass", "blocking", "fallback"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                _prepared, intent = _launch(fixture)
                _persist_terminal(fixture, intent, branch=branch)
                with mock.patch.object(selection, "_stored_runner_physical_basis_matches", return_value=True):
                    with mock.patch.object(cli, "select_current_verification_runner_basis", wraps=cli.select_current_verification_runner_basis) as physical:
                        added = run(fixture.db, fixture.repo, "task", "add", "--title", "Later work", "--priority", "low")
                    expected = run(fixture.db, fixture.repo, "task", "context")
                self.assertEqual(added.returncode, 0, added.stdout)
                preparation = json.loads(added.stdout)["data"]["context_preparation"]
                self.assertEqual(preparation["status"], "ready", preparation)
                self.assertEqual(preparation["context"], json.loads(expected.stdout)["data"])
                physical.assert_called_once()

    def test_unrelated_runner_corruption_after_commit_is_still_globally_rejected(self):
        from tests.test_m242_runner_service import RunnerServiceFixture
        from tests.test_m243c_runner_gate import _launch, _persist_terminal, _corrupt_runner_observation_digest

        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            original = cli.connect_initialized_readonly

            def corrupt_then_read(target):
                _corrupt_runner_observation_digest(fixture.db, task_id=fixture.task_id)
                return original(target)

            with mock.patch.object(cli, "connect_initialized_readonly", side_effect=corrupt_then_read), mock.patch.object(cli, "handle_task_context") as composition:
                added = run(fixture.db, fixture.repo, "task", "add", "--title", "New higher priority work", "--priority", "urgent")
            self.assertEqual(added.returncode, 0, added.stdout)
            payload = json.loads(added.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["context_preparation"]["status"], "failed")
            composition.assert_not_called()
            with closing(sqlite3.connect(fixture.db)) as reader:
                self.assertEqual(reader.execute("SELECT count(*) FROM tasks WHERE task_id = ?", (payload["data"]["task"]["task_id"],)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
