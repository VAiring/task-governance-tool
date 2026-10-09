"""Combined held-slot policy with retained legacy overlap and atomic acquisition."""

from tests import test_task_ownership as support
from task_governance_tool.schema_session_slot import policy_statements
from task_governance_tool.schema_session_slot_reacquisition import slot_update_statement

X, Y = support.X, support.Y


class SessionSlotTests(support.TaskOwnershipTests):
    def setUp(self):
        super().setUp()
        for statement in policy_statements():
            self.connection.execute(statement)
        self.connection.execute("DROP TRIGGER trg_task_ownership_slot_update")
        self.connection.execute(slot_update_statement())
        self.connection.execute("CREATE TABLE schema_migrations(version INTEGER)")
        self.connection.execute("INSERT INTO schema_migrations VALUES (27)")
        self.connection.commit()

    def test_review_pending_releases_slot_but_keeps_completion_owner(self):
        # Override the predecessor's intentional old-policy expectation.
        with self.writer():
            a = self.add("in_progress")
            initial = self.basis(a)
            pending = self.move(a, "review_pending")
            b = self.add()
            self.assertEqual(pending.generation, initial.generation)
            self.assertEqual(pending.completion_session_id, X.session_id)
            self.assert_error("session_task_in_progress", lambda: self.move(b, "in_progress"))
            self.assertEqual(self.move(a, "review_pending"), pending)
            self.assert_error("task_not_owned", lambda: self.move(a, "done", Y))
            resumed = self.move(a, "in_progress")
            self.assertEqual(resumed.execution_id, initial.execution_id)
            self.move(a, "review_pending")
            self.move(a, "done")
            self.move(b, "in_progress")

    def test_pending_releases_on_explicit_hold_exit_and_does_not_block_other_owner(self):
        for state in ("paused", "blocked", "ready", "cancelled", "done"):
            with self.subTest(state=state), self.writer():
                a = self.add("in_progress")
                self.move(a, "review_pending")
                b = self.add("in_progress", Y)
                self.move(a, state)
                c = self.add("in_progress")
                self.move(c, "cancelled")
                self.move(b, "cancelled", Y)

    def test_database_guard_rejects_new_acquisition_while_pending(self):
        import sqlite3
        with self.writer():
            a = self.add("in_progress")
            self.move(a, "review_pending")
            b = self.add("in_progress", Y)
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute("UPDATE task_ownership SET owner_session_id=? WHERE task_id=?", (X.session_id, b))
