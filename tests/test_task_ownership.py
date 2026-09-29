from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from task_governance_tool import task_ownership as ownership
from task_governance_tool.schema_task_ownership import ownership_statements
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.task_values import TaskValidationError


X = CallerIdentity("00000000-0000-4000-8000-000000000001")
Y = CallerIdentity("00000000-0000-4000-8000-000000000002")
UNKNOWN = CallerIdentity(None)
PROJECT = "test-project"
NOW = "2026-09-29T05:00:00Z"


class TaskOwnershipTests(unittest.TestCase):
    """Repository/state-machine tests; public CLI and migration tests are separate."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "state.sqlite3"
        self.connection = self.connect()
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE tasks(project_id TEXT, task_id TEXT PRIMARY KEY, status TEXT, UNIQUE(project_id, task_id))")
        self.connection.execute("CREATE TABLE task_completion_cycles(completion_cycle_id TEXT PRIMARY KEY, project_id TEXT, task_id TEXT)")
        for statement in ownership_statements():
            self.connection.execute(statement)
        self.connection.commit()
        self.serial = 0

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=0.1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def test_existing_task_identifier_contract_is_not_narrowed(self):
        with self.writer():
            self.connection.execute("INSERT INTO tasks VALUES (?, ?, ?)", (PROJECT, "legacy-task-id", "ready"))
            ownership.initialize_task(self.connection, project_id=PROJECT, task_id="legacy-task-id",
                                      status="ready", caller=X, now=NOW)
        basis = ownership.read_bases(self.connection, project_id=PROJECT, task_ids=["legacy-task-id"])["legacy-task-id"]
        self.assertEqual((basis.task_id, basis.state, basis.generation), ("legacy-task-id", "none", 0))

    @contextmanager
    def writer(self, connection=None):
        connection = connection or self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def add(self, status="ready", caller=X, *, connection=None):
        connection = connection or self.connection
        self.serial += 1
        task_id = f"tg_task_{self.serial:016x}"
        connection.execute("INSERT INTO tasks VALUES (?, ?, ?)", (PROJECT, task_id, status))
        ownership.initialize_task(connection, project_id=PROJECT, task_id=task_id,
                                  status=status, caller=caller, now=NOW)
        return task_id

    def basis(self, task_id, connection=None):
        return ownership.read_basis(connection or self.connection, project_id=PROJECT, task_id=task_id)

    def move(self, task_id, status, caller=X, *, observed=None, recovery_reason=None, connection=None):
        connection = connection or self.connection
        result = ownership.transition(connection, observed or self.basis(task_id, connection), caller,
                                      status=status, now=NOW, recovery_reason=recovery_reason)
        connection.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))
        self.assertEqual(self.basis(task_id, connection), result)
        return result

    def assert_error(self, code, operation):
        with self.assertRaises(TaskValidationError) as rejected:
            operation()
        self.assertEqual(rejected.exception.code, code)

    def test_acquire_release_resume_and_reopen_have_distinct_execution_rules(self):
        with self.writer():
            a = self.add()
            started = self.move(a, "in_progress")
            paused = self.move(a, "paused")
            resumed = self.move(a, "in_progress", Y)
            self.assertEqual(resumed.execution_id, started.execution_id)
            self.assertGreater(resumed.generation, paused.generation)
            self.move(a, "blocked", Y)
            self.move(a, "in_progress", X)
            self.move(a, "ready")
            self.assertIsNone(self.basis(a).execution_id)
            self.move(a, "blocked")
            restarted = self.move(a, "in_progress")
            self.assertNotEqual(restarted.execution_id, started.execution_id)
            self.move(a, "cancelled")
            self.assertIsNone(self.basis(a).execution_id)
            self.move(a, "blocked")
            second = self.move(a, "in_progress", Y)
            self.assertNotEqual(second.execution_id, restarted.execution_id)
            self.move(a, "done", Y)
            reopened = self.move(a, "in_progress")
            self.assertNotEqual(reopened.execution_id, second.execution_id)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM task_executions").fetchone()[0], 4)

    def test_review_pending_releases_slot_but_keeps_completion_owner(self):
        with self.writer():
            a = self.add("in_progress")
            initial = self.basis(a)
            pending = self.move(a, "review_pending")
            self.assertEqual(pending.generation, initial.generation)
            self.assertEqual(pending.completion_session_id, X.session_id)
            b = self.add("in_progress")
            self.assertEqual(self.move(a, "review_pending"), pending)
            self.assert_error("task_not_owned", lambda: self.move(a, "done", Y))
            self.assert_error("session_task_in_progress", lambda: self.move(a, "in_progress"))
            self.assertEqual(self.basis(a), pending)
            self.move(a, "done")
            self.assertEqual(self.basis(b).state, "owned")

    def test_recovery_is_explicit_and_invalidates_the_delayed_owner_basis(self):
        with self.writer():
            a = self.add("in_progress")
            stale = self.basis(a)
            self.assert_error("task_not_owned", lambda: ownership.require_mutation(self.connection, stale, Y))
            self.assert_error("task_not_owned", lambda: self.move(a, "paused", Y))
            recovered = self.move(a, "paused", Y, recovery_reason="Explicit handover")
            self.assertGreater(recovered.generation, stale.generation)
            self.assert_error("task_not_owned", lambda: ownership.require_mutation(self.connection, recovered, X))
            self.move(a, "in_progress", Y)
            self.assert_error("task_ownership_changed", lambda: self.move(a, "done", X, observed=stale))
        row = self.connection.execute("SELECT actor_session_id, recovery_reason FROM task_owner_transitions WHERE recovery_reason != ''").fetchone()
        self.assertEqual(tuple(row), (Y.session_id, "Explicit handover"))

    def test_initial_review_pending_and_batch_second_active_roll_back_everything(self):
        self.assert_error("invalid_status_transition", lambda: self._failed_registration("review_pending"))
        self.assert_error("session_task_in_progress", lambda: self._failed_registration("in_progress", twice=True))
        for table in ("tasks", "task_ownership", "task_executions", "task_owner_transitions"):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_blocked_organization_needs_no_slot_but_stale_acquisition_is_rejected(self):
        with self.writer():
            a = self.add("in_progress")
            held = self.move(a, "blocked")
            b = self.add("in_progress")
            for caller in (X, Y, UNKNOWN):
                self.assertEqual(ownership.require_mutation(self.connection, held, caller), held)
            self.assert_error("session_task_in_progress", lambda: self.move(a, "in_progress"))
            acquired = self.move(a, "in_progress", Y)
            self.assertEqual(acquired.execution_id, held.execution_id)
            self.assert_error("task_ownership_changed", lambda: ownership.require_mutation(self.connection, held, X))
            self.assert_error("task_not_owned", lambda: ownership.require_mutation(self.connection, acquired, X))
            self.assertEqual(self.basis(b).owner_session_id, X.session_id)

    def _failed_registration(self, status, twice=False):
        with self.writer():
            self.add(status)
            if twice:
                self.add(status)

    def test_direct_ownerless_review_pending_rejected_with_free_or_occupied_slot(self):
        for source in ("ready", "blocked", "cancelled", "paused"):
            for occupied in (False, True):
                with self.subTest(source=source, occupied=occupied), self.writer():
                    a = self.add()
                    if source != "ready":
                        self.move(a, "in_progress")
                        self.move(a, source)
                    b = self.add("in_progress") if occupied else None
                    before = self.basis(a)
                    self.assert_error("invalid_status_transition", lambda: self.move(a, "review_pending"))
                    self.assertEqual(self.basis(a), before)
                    if b:
                        self.assertEqual(self.basis(b).state, "owned")
                        self.move(b, "cancelled")

    def test_missing_identity_and_migrated_unknown_never_guess_an_owner(self):
        with self.writer():
            ready = self.add(caller=UNKNOWN)
            self.assert_error("session_identity_required", lambda: self.move(ready, "in_progress", UNKNOWN))
            self.assert_error("session_identity_required", lambda: self.move(ready, "done", UNKNOWN))
            self.assert_error("task_not_owned", lambda: self.move(ready, "done"))
            # This is the migration's intentionally ownerless shape, not an initial API status.
            self.connection.execute("UPDATE tasks SET status='review_pending' WHERE task_id=?", (ready,))
            self.connection.execute("UPDATE task_ownership SET state='unknown' WHERE task_id=?", (ready,))
            unknown = self.basis(ready)
            self.assertIsNone(unknown.projection(X)["is_owner"])
            self.assert_error("task_not_owned", lambda: self.move(ready, "review_pending"))
            self.assert_error("session_identity_required", lambda: self.move(ready, "paused", UNKNOWN, recovery_reason="Recover"))
            self.move(ready, "paused", Y, recovery_reason="Recover migrated work")
            self.assertIsNone(self.basis(ready).execution_id)
            self.move(ready, "in_progress", Y)
            self.assertEqual(self.basis(ready).owner_session_id, Y.session_id)

    def test_read_projection_is_caller_relative_and_read_only(self):
        with self.writer():
            a = self.add("in_progress")
            b = self.add()
        self.connection.execute("PRAGMA query_only=ON")
        before = self.path.read_bytes()
        owned = self.basis(a)
        self.assertTrue(owned.projection(X)["is_owner"])
        self.assertFalse(owned.projection(Y)["is_owner"])
        self.assertIsNone(owned.projection(UNKNOWN)["is_owner"])
        self.assertFalse(self.basis(b).projection(X)["is_owner"])
        self.assertFalse(self.basis(b).projection(X)["is_completion_owner"])
        self.assertEqual(before, self.path.read_bytes())

    def test_independent_connections_reject_stale_same_task_and_second_task_start(self):
        with self.writer():
            a, b = self.add(), self.add()
        second = self.connect()
        self.addCleanup(second.close)
        old_a, old_b = self.basis(a, second), self.basis(b, second)
        with self.writer():
            self.move(a, "in_progress")
        with self.writer(second):
            self.assert_error("task_ownership_changed", lambda: self.move(a, "in_progress", Y, observed=old_a, connection=second))
            self.assert_error("session_task_in_progress", lambda: self.move(b, "in_progress", X, observed=old_b, connection=second))
            self.move(b, "in_progress", Y, observed=old_b, connection=second)

    def test_simultaneous_start_serializes_task_and_session_slot(self):
        for same_task in (True, False):
            with self.subTest(same_task=same_task):
                with self.writer():
                    a, b = self.add(), self.add()
                barrier = threading.Barrier(2)
                def start(task_id, caller):
                    connection = self.connect()
                    connection.execute("PRAGMA busy_timeout=5000")
                    try:
                        observed = self.basis(task_id, connection)
                        barrier.wait(timeout=5)
                        try:
                            with self.writer(connection):
                                self.move(task_id, "in_progress", caller, observed=observed, connection=connection)
                            return "acquired"
                        except TaskValidationError as error:
                            return error.code
                    finally:
                        connection.close()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(start, a, X)
                    second = pool.submit(start, a if same_task else b, Y if same_task else X)
                    outcomes = [first.result(timeout=10), second.result(timeout=10)]
                self.assertCountEqual(outcomes, ["acquired", "task_ownership_changed" if same_task else "session_task_in_progress"])
                with self.writer():
                    for task_id in (a, b):
                        basis = self.basis(task_id)
                        if basis.state == "owned":
                            self.move(task_id, "paused", X, recovery_reason="Release race-test slot")

    def test_partial_unique_index_and_immutable_history_enforce_repository_boundary(self):
        with self.writer():
            a, b = self.add("in_progress", X), self.add("in_progress", Y)
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute("UPDATE task_ownership SET owner_session_id=? WHERE task_id=?", (X.session_id, b))
            for table in ("task_executions", "task_owner_transitions"):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(f"DELETE FROM {table} WHERE task_id=?", (a,))
            self.connection.execute("INSERT INTO task_completion_cycles VALUES ('cycle-a', ?, ?)", (PROJECT, a))
            ownership.link_completion_cycle(self.connection, self.basis(a), completion_cycle_id="cycle-a")
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute("UPDATE task_execution_cycles SET task_id=?", (b,))
            self.connection.execute("INSERT INTO task_completion_cycles VALUES ('cycle-b', ?, ?)", (PROJECT, b))
            with self.assertRaises(sqlite3.IntegrityError):
                ownership.link_completion_cycle(self.connection, self.basis(a), completion_cycle_id="cycle-b")

    def test_error_after_ownership_transition_rolls_back_slot_and_history(self):
        with self.writer():
            a = self.add("in_progress")
        before = self.basis(a)
        with self.assertRaisesRegex(RuntimeError, "business write failed"):
            with self.writer():
                self.move(a, "paused", Y, recovery_reason="Recover")
                raise RuntimeError("business write failed")
        self.assertEqual(self.basis(a), before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM task_owner_transitions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
