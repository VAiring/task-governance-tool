from __future__ import annotations

import os
import json
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest import mock

from tests.test_task_ownership import X, Y, UNKNOWN
from tests.test_m242_r3b_schema20_activation import _SCHEMA20_RUNTIME_PATCH_TARGETS
from tests.verification_receipt_test_support import initialize, run_taskgov, payload, target_for
from task_governance_tool import storage
from tests.test_review_results import BinaryInput, document, receipt


class TaskOwnershipCliTests(unittest.TestCase):
    """Public parser/services against real isolated v24 SQLite, no host setup."""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target in _SCHEMA20_RUNTIME_PATCH_TARGETS:
            stack.enter_context(mock.patch(target, 24))
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.repo, self.db = initialize(root)

    def invoke(self, *args, caller=X, code=None):
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": caller.session_id or ""}):
            result = payload(run_taskgov(*args, "--repo", str(self.repo), "--db", str(self.db), "--json"))
        if code is None:
            self.assertTrue(result["ok"], result)
        else:
            self.assertFalse(result["ok"], result)
            self.assertIn(code, [error["code"] for error in result["errors"]], result)
        return result

    def add(self, title="Ownership task", status="ready", caller=X, verification=None):
        args = ("--verification", verification) if verification is not None else ("--verification-not-required", "Test-only no executable change")
        return self.invoke("task", "add", "--title", title, "--status", status, "--review-tier", "0", *args, caller=caller)["data"]["task"]

    def edit(self, task, status, caller=X, *extra, code=None):
        return self.invoke("task", "edit", task["task_id"], "--status", status, *extra, caller=caller, code=code)

    def test_start_owner_guard_recovery_and_read_projection(self):
        first = self.add(status="in_progress")
        other = self.add("Second task")
        owner = first["ownership"]
        self.assertEqual(owner["owner_session_id"], X.session_id)
        self.assertTrue(owner["is_owner"])
        self.edit(other, "in_progress", code="session_task_in_progress")
        self.invoke("task", "edit", first["task_id"], "--add-note", "Note", caller=Y, code="task_not_owned")
        viewed = self.invoke("task", "show", first["task_id"], caller=Y)["data"]["task"]
        self.assertFalse(viewed["ownership"]["is_owner"])
        self.edit(first, "paused", Y, "--pause-reason", "Explicit test recovery")
        self.invoke("task", "edit", first["task_id"], "--add-note", "Note", code="task_not_owned")
        resumed = self.edit(first, "in_progress", Y)["data"]["task"]
        self.assertEqual(owner["execution_id"], resumed["ownership"]["execution_id"])
        self.assertGreater(resumed["ownership"]["generation"], owner["generation"])
        self.assertTrue(resumed["ownership"]["is_owner"])
        self.edit(other, "in_progress")

    def test_context_eligibility_precedes_limit_and_missing_identity_is_read_only(self):
        for number in range(22):
            caller = type(X)(f"00000000-0000-4000-8000-{number + 100:012x}")
            self.add(f"Other owner {number}", status="in_progress", caller=caller)
        mine = self.add("Mine", status="in_progress")
        context = self.invoke("task", "context")["data"]
        self.assertEqual(context["selected"]["task"]["task_id"], mine["task_id"])
        self.assertEqual(context["current"]["total_matching"], 1)
        ready = self.add("Unstarted")
        before = self.db.read_bytes()
        result = self.invoke("task", "context", caller=UNKNOWN)
        self.assertEqual(result["data"]["selected"]["task"]["task_id"], ready["task_id"])
        self.assertIn("session_identity_required", [warning["code"] for warning in result["warnings"]])
        self.assertIsNone(result["data"]["selected"]["task"]["ownership"]["is_owner"])
        self.assertEqual(before, self.db.read_bytes())
        self.edit(ready, "in_progress", UNKNOWN, code="session_identity_required")

    def test_review_pending_can_complete_while_another_task_is_active(self):
        first = self.add(status="in_progress")
        task_id = first["task_id"]
        self.invoke("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", "sha256:" + "a" * 64)
        self.invoke("review", "receipt", "add", task_id, "--reviewer", "Mechanical fixture", "--kind", "not_required", "--verdict", "not_required", "--summary", "Test-only mechanical fixture")
        pending = self.edit(first, "review_pending")["data"]["task"]
        self.assertTrue(pending["ownership"]["is_completion_owner"])
        second = self.add("Other active", status="in_progress")
        self.invoke("task", "complete", task_id, "--verification-complete", "--review-complete", "--commit-not-required", caller=Y, code="task_not_owned")
        completed = self.invoke("task", "complete", task_id, "--verification-complete", "--review-complete", "--commit-not-required")["data"]["task"]
        self.assertEqual(completed["status"], "done")
        self.assertEqual(completed["ownership"]["state"], "none")
        with closing(storage.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM task_execution_cycles").fetchone()[0], 1)
            storage.validate_current_database(connection, target_for(self.db, self.repo))
        self.assertEqual(self.invoke("task", "context")["data"]["selected"]["task"]["task_id"], second["task_id"])

    def test_ownerless_pending_rejected_without_taking_slot(self):
        ready = self.add()
        self.invoke("task", "add", "--title", "Invalid pending", "--status", "review_pending", code="invalid_status_transition")
        self.edit(ready, "review_pending", code="invalid_status_transition")
        self.edit(ready, "in_progress")

    def test_started_blocked_can_be_organized_while_another_task_is_active(self):
        for destination in ("ready", "cancelled"):
            with self.subTest(destination=destination):
                first = self.add(status="in_progress")
                held = self.edit(first, "blocked", X, "--blocked-reason", "Waiting for input")["data"]["task"]
                other = self.add("Other active", status="in_progress")
                for caller in (X, Y, UNKNOWN):
                    changed = self.invoke(
                        "task", "edit", first["task_id"], "--blocked-reason",
                        f"Clarified by {caller.session_id or 'ordinary caller'}", caller=caller,
                    )["data"]["task"]
                    self.assertEqual(changed["status"], "blocked")
                    self.assertEqual(changed["ownership"]["generation"], held["ownership"]["generation"])
                    self.assertEqual(changed["ownership"]["state"], "none")
                before = self.db.read_bytes()
                self.edit(first, "in_progress", code="session_task_in_progress")
                self.assertEqual(before, self.db.read_bytes())
                self.edit(first, "review_pending", code="invalid_status_transition")
                self.invoke("task", "complete", first["task_id"], "--verification-complete",
                            "--review-complete", "--commit-not-required", code="task_not_owned")
                organized = self.edit(first, destination)["data"]["task"]
                self.assertEqual(organized["status"], destination)
                self.assertEqual(organized["ownership"]["state"], "none")
                self.assertIsNone(organized["ownership"]["execution_id"])
                self.assertEqual(self.invoke("task", "context")["data"]["selected"]["task"]["task_id"], other["task_id"])
                with closing(storage.connect(self.db)) as connection:
                    storage.validate_current_database(connection, target_for(self.db, self.repo))
                self.edit(other, "cancelled")
                self.edit(first, "blocked", X, "--blocked-reason", "Waiting again after ending execution")
                resumed = self.edit(first, "in_progress")["data"]["task"]
                self.assertNotEqual(resumed["ownership"]["execution_id"], first["ownership"]["execution_id"])
                self.edit(first, "cancelled")

    def test_public_batch_cannot_create_two_active_or_ownerless_pending_tasks(self):
        for second_status, code in (("in_progress", "session_task_in_progress"),
                                    ("review_pending", "invalid_status_transition")):
            candidate = {"version": 1, "common": {"review_tier": 0}, "tasks": [
                {"title": "First", "status": "in_progress", "contract": None},
                {"title": "Second", "status": second_status, "contract": None},
            ]}
            before = self.db.read_bytes()
            with self.subTest(second_status=second_status), mock.patch("sys.stdin", BinaryInput(json.dumps(candidate).encode())):
                self.invoke("task", "add", "--from-stdin", code=code)
            self.assertEqual(before, self.db.read_bytes())
        self.add(status="in_progress")

    def test_task_scoped_write_entries_reject_nonowner_without_rows(self):
        task = self.add(status="in_progress", verification="Focused isolated checks")
        task_id = task["task_id"]
        target_args = ("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", "sha256:" + "a" * 64)
        self.invoke(*target_args, caller=Y, code="task_not_owned")
        generation = self.invoke(*target_args)["data"]["task"]["review_target_generation"]
        receipt_args = ("review", "receipt", "add", task_id, "--reviewer", "Mechanical fixture", "--kind", "not_required", "--verdict", "not_required", "--summary", "Mechanical fixture")
        self.invoke(*receipt_args, caller=Y, code="task_not_owned")
        review = self.invoke(*receipt_args)["data"]["receipt"]["review_receipt_id"]
        finding_args = ("review", "finding", "add", task_id, "--receipt-id", review, "--severity", "low", "--summary", "Test finding")
        self.invoke(*finding_args, caller=Y, code="task_not_owned")
        finding = self.invoke(*finding_args)["data"]["finding"]["review_finding_id"]
        handoff = self.invoke("handoff", "record", task_id, "--summary", "Out of scope test")["data"]["handoff"]["handoff_id"]
        cases = [
            ("task", "checkpoint", task_id, "--summary", "Test checkpoint", "--next-action", "Continue"),
            ("handoff", "record", task_id, "--summary", "Another discovery"),
            ("handoff", "withdraw", handoff, "--reason", "User withdrawal"),
            ("review", "finding", "resolve", finding, "--resolution", "Repaired"),
            ("verification", "receipt", "add", task_id, "--result", "pass", "--duration-ms", "10", "--scope-coverage", "full", "--expected-target-generation", str(generation)),
            ("task", "edit", task_id, "--status", "done", "--verification-complete", "--review-complete", "--commit-not-required"),
        ]
        for args in cases:
            with self.subTest(command=args[:3]):
                before = self.db.read_bytes()
                self.invoke(*args, caller=Y, code="task_not_owned")
                self.assertEqual(before, self.db.read_bytes())
        result_document = document(task_id, revision=0, generation=generation,
                                   receipts=[receipt("Another reviewer", kind="not_required", verdict="not_required")])
        with mock.patch("sys.stdin", BinaryInput(json.dumps(result_document).encode())):
            self.invoke("review", "result", "add", task_id, caller=Y, code="task_not_owned")

    def test_contract_revision_reacquires_slot_atomically_and_pending_noop_does_not(self):
        task = self.invoke("task", "add", "--title", "Contract task", "--status", "in_progress",
                           "--contract-scope", "Original scope", "--contract-acceptance", "Original acceptance",
                           "--contract-authority-ref", "test authority")["data"]["task"]
        self.edit(task, "review_pending")
        other = self.add("Other active", status="in_progress")
        before = self.db.read_bytes()
        self.invoke("task", "edit", task["task_id"], "--contract-scope", "New scope", "--contract-acceptance", "New acceptance",
                    "--contract-authority-ref", "test authority", "--contract-change-reason", "Explicit test revision", code="session_task_in_progress")
        self.assertEqual(before, self.db.read_bytes())
        self.edit(task, "review_pending")
        self.edit(task, "review_pending", Y, code="task_not_owned")
        self.edit(other, "paused", X, "--pause-reason", "Release test slot")
        updated = self.invoke("task", "edit", task["task_id"], "--contract-scope", "New scope", "--contract-acceptance", "New acceptance",
                              "--contract-authority-ref", "test authority", "--contract-change-reason", "Explicit test revision")["data"]["task"]
        self.assertEqual(updated["status"], "in_progress")
        self.assertTrue(updated["ownership"]["is_owner"])

    def test_delayed_completion_and_check_detect_takeover_generation(self):
        from task_governance_tool import completion_workflow
        task = self.add(status="in_progress")
        task_id = task["task_id"]
        self.invoke("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", "sha256:" + "a" * 64)
        self.invoke("review", "receipt", "add", task_id, "--reviewer", "Mechanical", "--kind", "not_required", "--verdict", "not_required", "--summary", "Mechanical fixture")
        args = ("task", "complete", task_id, "--verification-complete", "--review-complete", "--commit-not-required")
        unknown = self.invoke(*args, "--check", caller=UNKNOWN)["data"]
        self.assertFalse(unknown["ready"])
        self.assertEqual(unknown["blocking_codes"], ["session_identity_required"])
        original = completion_workflow.prepare_request_against_basis
        def takeover(*values, **kwargs):
            self.edit(task, "paused", Y, "--pause-reason", "Explicit recovery during completion preflight")
            self.edit(task, "in_progress", Y)
            return original(*values, **kwargs)
        with mock.patch.object(completion_workflow, "prepare_request_against_basis", side_effect=takeover):
            self.invoke(*args, code="task_ownership_changed")
        self.assertEqual(self.invoke("task", "show", task_id)["data"]["task"]["status"], "in_progress")
        self.edit(task, "paused", X, "--pause-reason", "Restore test ownership")
        self.edit(task, "in_progress", X)
        with mock.patch.object(completion_workflow, "prepare_request_against_basis", side_effect=takeover):
            checked = self.invoke(*args, "--check")["data"]
        self.assertFalse(checked["ready"])
        self.assertEqual(checked["blocking_codes"], ["completion_check_stale"])


if __name__ == "__main__":
    unittest.main()
