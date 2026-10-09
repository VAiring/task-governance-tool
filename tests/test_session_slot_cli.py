"""Current public CLI policy; predecessor CLI suite remains pinned to schema 24."""
from contextlib import closing

from tests import test_task_ownership_cli as support
from tests.test_review_session_migration import candidate_runtime
from tests.verification_receipt_test_support import target_for
from task_governance_tool import storage


class SessionSlotCliTests(support.TaskOwnershipCliTests):
    def setUp(self):
        super().setUp()
        self.enterContext(candidate_runtime(27))
        with closing(storage.connect(self.db)) as connection:
            storage.apply_migrations(connection)

    def test_task_scoped_write_entries_reject_nonowner_without_rows(self):
        # Schema 25 introduced bound reviewer submission; it is deliberately
        # distinct from ordinary ownership, and schema 27 must preserve it.
        first = self.add(status="in_progress", verification="Isolated check")
        task_id = first["task_id"]
        generation = self.invoke("review", "target", "set", task_id, "--kind", "diff_fingerprint",
            "--revision", "sha256:" + "a" * 64)["data"]["task"]["review_target_generation"]
        self.edit(first, "review_pending")
        self.invoke("review", "receipt", "add", task_id, "--reviewer", "Independent fixture",
            "--kind", "not_required", "--verdict", "not_required", "--summary", "Mechanical fixture", caller=support.Y)
        for args in (
            ("task", "edit", task_id, "--add-note", "Unauthorized note"),
            ("task", "checkpoint", task_id, "--summary", "Checkpoint", "--next-action", "Continue"),
            ("handoff", "record", task_id, "--summary", "Discovery"),
            ("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", "sha256:" + "b" * 64),
            ("verification", "receipt", "add", task_id, "--result", "pass", "--duration-ms", "10",
                "--scope-coverage", "full", "--expected-target-generation", str(generation)),
        ):
            with self.subTest(command=args[:3]):
                before = self.db.read_bytes()
                self.invoke(*args, caller=support.Y, code="task_not_owned")
                self.assertEqual(before, self.db.read_bytes())

    def test_review_pending_can_complete_while_another_task_is_active(self):
        # Current policy blocks a second start, preserving normal completion.
        first = self.add(status="in_progress")
        task_id = first["task_id"]
        self.invoke("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", "sha256:" + "a" * 64)
        self.invoke("review", "receipt", "add", task_id, "--reviewer", "Mechanical fixture", "--kind", "not_required", "--verdict", "not_required", "--summary", "Test-only mechanical fixture")
        self.edit(first, "review_pending")
        other = self.add("Other ready")
        before = self.db.read_bytes()
        self.edit(other, "in_progress", code="session_task_in_progress")
        self.assertEqual(before, self.db.read_bytes())
        self.invoke("task", "complete", task_id, "--verification-complete", "--review-complete", "--commit-not-required", caller=support.Y, code="task_not_owned")
        completed = self.invoke("task", "complete", task_id, "--verification-complete", "--review-complete", "--commit-not-required")["data"]["task"]
        self.assertEqual(completed["status"], "done")
        self.edit(other, "in_progress")
        with closing(storage.connect(self.db)) as connection:
            storage.validate_current_database(connection, target_for(self.db, self.repo))

    def test_contract_revision_reacquires_slot_atomically_and_pending_noop_does_not(self):
        first = self.invoke("task", "add", "--title", "Contract task", "--status", "in_progress",
            "--contract-scope", "Original scope", "--contract-acceptance", "Original acceptance",
            "--contract-authority-ref", "test authority")["data"]["task"]
        self.edit(first, "review_pending")
        self.edit(first, "review_pending", support.Y, code="task_not_owned")
        other = self.add("Other ready")
        self.edit(other, "in_progress", code="session_task_in_progress")
        updated = self.invoke("task", "edit", first["task_id"], "--contract-scope", "New scope",
            "--contract-acceptance", "New acceptance", "--contract-authority-ref", "test authority",
            "--contract-change-reason", "Explicit test revision")["data"]["task"]
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["ownership"]["execution_id"], first["ownership"]["execution_id"])
