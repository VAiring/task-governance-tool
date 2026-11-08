"""Repository failures cannot leave a committable partial Task operation."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.verification_receipt_test_support import (
    initialize_current, add_task, target_for, seed_current_review_evidence,
    completion, payload, run_taskgov,
)
from tests.test_m23s_schema22_migration import _logical_snapshot
from task_governance_tool import tasks, contracts, effort, storage
from task_governance_tool.session_identity import CallerIdentity


CALLER = CallerIdentity("22222222-2222-4222-8222-222222222222")
KINDS = ("add", "add_contract", "batch", "edit", "edit_note", "note",
         "complete", "complete_edit", "complete_note", "reopen",
         "activate", "revise")


class TaskWriteAtomicityTests(unittest.TestCase):
    @contextmanager
    def case(self, kind):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            "os.environ", {"CODEX_THREAD_ID": CALLER.session_id},
        ):
            repo, db = initialize_current(Path(temporary))
            active = kind.startswith("complete") or kind in {"reopen", "revise"}
            task = add_task(db, repo, status="in_progress" if active else "ready",
                            verification="", verification_not_required_reason="No managed changes")
            task_id = task["task_id"]
            if kind.startswith("complete") or kind == "reopen":
                seed_current_review_evidence(db, repo, task_id)
            if kind == "reopen":
                result = completion(db, repo, task_id)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            project = target_for(db, repo).project
            with closing(storage.connect(db)) as connection:
                if kind == "revise":
                    # Initial Contract creation is a real ready/start activation.
                    tasks.edit_task(connection, project, task_id, caller=CALLER,
                                    status="paused", pause_reason="Release fixture slot")
                    connection.commit()
                    task_id = tasks.add_task(connection, project, title="Contract fixture",
                        contract_scope="Original scope", contract_acceptance="Original acceptance",
                        caller=CALLER).task["task_id"]
                    connection.commit()
                plan = None
                if kind == "complete":
                    basis = tasks.capture_completion_basis(connection, project, task_id)
                    request = tasks.build_completion_request(task_id, verification_complete=True,
                        review_complete=True, completion_evidence_kind="commit_not_required")
                    plan = tasks.prepare_completion_plan(basis, project, request)

                def invoke():
                    if kind in {"add", "add_contract"}:
                        kwargs = ({"contract_scope": "Scope", "contract_acceptance": "Acceptance"}
                                  if kind == "add_contract" else {})
                        return tasks.add_task(connection, project, title="New Task", caller=CALLER, **kwargs)
                    if kind == "batch":
                        return tasks.add_tasks(connection, project, caller=CALLER,
                            task_inputs=[{"title": "First"}, {"title": "Second"}])
                    if kind == "complete":
                        return tasks.complete_task(connection, project, plan, caller=CALLER)
                    if kind.startswith("complete"):
                        kwargs = dict(status="done", verification_complete=True,
                                      review_complete=True, commit_not_required=True)
                        if kind == "complete_note":
                            kwargs["add_note"] = "Completion note"
                    elif kind == "reopen":
                        kwargs = dict(status="in_progress", reopen_reason="More work")
                    elif kind == "activate":
                        kwargs = dict(status="in_progress", contract_scope="New scope",
                                      contract_acceptance="New acceptance")
                    elif kind == "revise":
                        kwargs = dict(contract_scope="Revised scope", contract_acceptance="New acceptance",
                            contract_authority_ref="conversation:approved", contract_change_reason="Accepted change")
                    elif kind == "note":
                        kwargs = dict(add_note="Note only")
                    else:
                        kwargs = dict(status="in_progress", title="Changed title", priority="urgent")
                        if kind == "edit_note":
                            kwargs["add_note"] = "Retained note"
                    return tasks.edit_task(connection, project, task_id, caller=CALLER, **kwargs)

                connection.execute("CREATE TABLE fixture_prior(value TEXT)")
                connection.commit()
                yield connection, invoke, task_id, project

    def prior_change(self, connection):
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO fixture_prior VALUES ('keep prior change')")

    def test_event_reread_and_transition_failures_preserve_outer_changes(self):
        for kind in KINDS:
            for phase in ("event", "reread", "transition"):
                with self.subTest(kind=kind, phase=phase), self.case(kind) as (connection, invoke, _, project):
                    if phase == "transition":
                        owner, name = effort, "record_task_transition"
                    elif kind in {"activate", "revise"}:
                        owner, name = contracts, "create_task_event" if phase == "event" else "_read_internal_task"
                    else:
                        owner = tasks
                        name = ("create_task_event" if phase == "event" else
                                "fetch_stored_task_row" if kind in {"add", "add_contract", "batch"} else "read_task")
                    actual = getattr(owner, name)
                    calls = []
                    failure = RuntimeError("original operation failure")

                    def fail_after_write(*args, **kwargs):
                        result = actual(*args, **kwargs)
                        calls.append(result)
                        # Contract's first read is its concurrency guard; fail the reread.
                        after = 2 if kind in {"activate", "revise"} and phase == "reread" else 1
                        if kind == "batch":
                            after = 2  # The entire batch must roll back, including item one.
                        if len(calls) == after:
                            raise failure
                        return result

                    # Batch explicitly requires preflight outside a transaction.
                    if kind != "batch":
                        self.prior_change(connection)
                    before = _logical_snapshot(connection)
                    with mock.patch.object(owner, name, side_effect=fail_after_write):
                        with self.assertRaises(RuntimeError) as caught:
                            invoke()
                    self.assertIs(caught.exception, failure)
                    connection.commit()
                    self.assertEqual(_logical_snapshot(connection), before)

    def test_constraint_abort_and_whole_transaction_rollback_keep_original_cause(self):
        for kind in KINDS:
            for action in ("ABORT", "ROLLBACK"):
                with self.subTest(kind=kind, action=action), self.case(kind) as (connection, invoke, _, project):
                    connection.execute("CREATE TRIGGER fixture_failure BEFORE INSERT ON task_events "
                        f"BEGIN SELECT RAISE({action}, 'private original storage failure'); END")
                    connection.commit()
                    baseline = _logical_snapshot(connection)
                    if kind != "batch":
                        self.prior_change(connection)
                    prior = _logical_snapshot(connection)
                    with self.assertRaises(tasks.TaskRepositoryError) as caught:
                        invoke()
                    error = caught.exception
                    self.assertEqual(error.code, "internal_error")
                    self.assertNotIn("private original", str(error))
                    self.assertIsInstance(error.__cause__, sqlite3.IntegrityError)
                    self.assertEqual(error.__cause__.sqlite_errorcode, sqlite3.SQLITE_CONSTRAINT_TRIGGER)
                    self.assertIn("private original storage failure", str(error.__cause__))
                    self.assertEqual(connection.in_transaction, action == "ABORT")
                    connection.commit()
                    self.assertEqual(_logical_snapshot(connection), prior if action == "ABORT" else baseline)

    def test_known_lane_collisions_remain_input_errors_without_parsing_sqlite_text(self):
        for mode in ("add", "edit", "batch"):
            with self.subTest(mode=mode), self.case("edit") as (connection, _, task_id, project):
                tasks.add_task(connection, project, title="Occupied", kind="sequential", lane="lane", lane_order=1)
                connection.commit()
                before = _logical_snapshot(connection)
                with self.assertRaises(tasks.TaskRepositoryError) as caught:
                    if mode == "edit":
                        tasks.edit_task(connection, project, task_id, kind="sequential", lane="lane", lane_order=1)
                    elif mode == "batch":
                        tasks.add_tasks(connection, project, task_inputs=[{"title": "First"},
                            {"title": "Clash", "kind": "sequential", "lane": "lane", "lane_order": 1}])
                    else:
                        tasks.add_task(connection, project, title="Clash", kind="sequential", lane="lane", lane_order=1)
                self.assertEqual(caught.exception.code, "invalid_argument")
                self.assertIsNone(caught.exception.__cause__)
                connection.commit()
                self.assertEqual(before, _logical_snapshot(connection))

    def test_success_retains_task_events_and_prior_outer_changes(self):
        for kind in KINDS:
            with self.subTest(kind=kind), self.case(kind) as (connection, invoke, _, project):
                if kind != "batch":
                    self.prior_change(connection)
                before = connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
                invoke()
                connection.commit()
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0], before)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM fixture_prior").fetchone()[0],
                                 0 if kind == "batch" else 1)

    def test_edit_ownership_rejection_precedes_lane_conflict(self):
        with self.case("edit") as (connection, _, task_id, project):
            tasks.edit_task(connection, project, task_id, caller=CALLER, status="in_progress")
            tasks.add_task(connection, project, title="Occupied", kind="sequential", lane="lane", lane_order=1)
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(tasks.TaskValidationError) as caught:
                tasks.edit_task(connection, project, task_id,
                    caller=CallerIdentity("33333333-3333-4333-8333-333333333333"),
                    kind="sequential", lane="lane", lane_order=1)
            self.assertEqual(caught.exception.code, "task_not_owned")
            connection.commit()
            self.assertEqual(before, _logical_snapshot(connection))

    def test_effort_whole_abort_propagates_but_local_advisory_abort_is_optional(self):
        profile = effort.EffortProfile(True, True, True, effort.PROFILE_ID, 1, "a" * 64, {})
        preflight = effort.EffortTransitionPreflight(0, 0, False, True,
            effort.GitEndpoint(True, "a" * 40, True, b""))
        for table in ("task_effort_activity", "task_effort_bases"):
            for action in ("ABORT", "ROLLBACK"):
                with self.subTest(table=table, action=action), self.case("edit") as (connection, _, task_id, project):
                    connection.execute(f"CREATE TRIGGER fixture_effort_failure BEFORE INSERT ON {table} "
                        f"BEGIN SELECT RAISE({action}, 'original effort failure'); END")
                    connection.commit()
                    baseline = _logical_snapshot(connection)
                    self.prior_change(connection)
                    with mock.patch.object(effort, "prepare_task_transition", return_value=preflight):
                        if action == "ROLLBACK":
                            with self.assertRaises(tasks.TaskRepositoryError) as caught:
                                tasks.edit_task(connection, project, task_id, caller=CALLER,
                                    status="in_progress", effort_profile=profile)
                            self.assertEqual(caught.exception.code, "internal_error")
                            self.assertEqual(str(caught.exception.__cause__), "original effort failure")
                            self.assertFalse(connection.in_transaction)
                        else:
                            result = tasks.edit_task(connection, project, task_id, caller=CALLER,
                                status="in_progress", effort_profile=profile)
                            self.assertEqual(result.task["status"], "in_progress")
                    connection.commit()
                    if action == "ROLLBACK":
                        self.assertEqual(baseline, _logical_snapshot(connection))
                    else:
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM fixture_prior").fetchone()[0], 1)

    def test_failed_savepoint_cleanup_rolls_back_unusable_outer_transaction(self):
        class FailingCleanup(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.startswith("ROLLBACK TO SAVEPOINT"):
                    raise sqlite3.OperationalError("cleanup failed")
                return super().execute(sql, parameters)
        with closing(sqlite3.connect(":memory:", factory=FailingCleanup)) as connection:
            connection.execute("CREATE TABLE fixture(value TEXT)")
            connection.execute("INSERT INTO fixture VALUES ('outer')")
            original = RuntimeError("operation failed")
            with self.assertRaises(RuntimeError) as caught:
                with tasks._task_write_savepoint(connection):
                    connection.execute("INSERT INTO fixture VALUES ('partial')")
                    raise original
            self.assertIs(caught.exception, original)
            self.assertFalse(connection.in_transaction)
            connection.commit()
            self.assertEqual(connection.execute("SELECT * FROM fixture").fetchall(), [])

    def test_failed_fallback_close_preserves_original_operation_error(self):
        for close_error in (OSError("descriptor retirement failed"),
                            sqlite3.OperationalError("close failed")):
            for original in (RuntimeError("operation failed"),
                             sqlite3.IntegrityError("constraint failed")):
                with self.subTest(close_error=type(close_error), original=type(original)):
                    class FailingCleanup(sqlite3.Connection):
                        close_calls = 0

                        def execute(self, sql, parameters=()):
                            if sql.startswith("ROLLBACK TO SAVEPOINT"):
                                raise sqlite3.OperationalError("savepoint cleanup failed")
                            return super().execute(sql, parameters)

                        def rollback(self):
                            raise sqlite3.OperationalError("rollback failed")

                        def close(self):
                            self.close_calls += 1
                            super().close()
                            # GuardedConnection can fail retiring POSIX descriptors
                            # after native close has already invalidated the handle.
                            raise close_error

                    connection = sqlite3.connect(":memory:", factory=FailingCleanup)
                    try:
                        connection.execute("CREATE TABLE fixture(value TEXT)")
                        connection.execute("INSERT INTO fixture VALUES ('outer')")
                        expected = (tasks.TaskRepositoryError if isinstance(original, sqlite3.IntegrityError)
                                    else RuntimeError)
                        with self.assertRaises(expected) as caught:
                            with tasks._task_write_savepoint(connection):
                                connection.execute("INSERT INTO fixture VALUES ('partial')")
                                raise original
                        if isinstance(original, sqlite3.IntegrityError):
                            self.assertEqual(caught.exception.code, "internal_error")
                            self.assertIs(caught.exception.__cause__, original)
                        else:
                            self.assertIs(caught.exception, original)
                        self.assertEqual(connection.close_calls, 1)
                        with self.assertRaises(sqlite3.ProgrammingError):
                            connection.commit()
                    finally:
                        sqlite3.Connection.close(connection)

    def test_public_internal_constraint_is_sanitized_and_uses_tool_error_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo, db = initialize_current(Path(temporary))
            private = sqlite3.IntegrityError("duplicate sequential lane order; token=private-storage-detail")
            with mock.patch.object(tasks, "create_task_event", side_effect=private):
                result = run_taskgov("task", "add", "--title", "Safe task",
                    "--repo", str(repo), "--db", str(db), "--json")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(payload(result)["errors"], [{"code": "internal_error",
                "message": "Task storage constraints could not be satisfied"}])
            self.assertNotIn("private-storage-detail", result.stdout + result.stderr)
            with closing(storage.connect(db)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
