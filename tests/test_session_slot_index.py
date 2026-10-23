"""Actual ownership service and retained triggers on the schema-32 lookup index."""
import sqlite3

from tests import test_session_slot as predecessor
from tests.test_task_ownership import PROJECT, NOW, X, Y
from task_governance_tool import task_ownership as ownership
from task_governance_tool.schema_session_slot_index import INDEX_NAME, INDEX_SQL
from task_governance_tool.session_identity import CallerIdentity


class IndexedSessionSlotTests(predecessor.SessionSlotTests):
    def setUp(self):
        super().setUp()
        self.connection.execute(INDEX_SQL)
        self.connection.execute("UPDATE schema_migrations SET version=32")
        self.connection.commit()

    def measure(self, operation):
        self.connection.execute("SAVEPOINT measure_slot")
        steps = [0]
        statements = []
        def tick():
            steps[0] += 1
            return 0
        self.connection.set_trace_callback(statements.append)
        self.connection.set_progress_handler(tick, 1)
        try:
            operation()
        finally:
            self.connection.set_progress_handler(None, 0)
            self.connection.set_trace_callback(None)
            self.connection.execute("ROLLBACK TO measure_slot")
            self.connection.execute("RELEASE measure_slot")
        return steps[0], statements

    def seed_noise(self, count):
        with self.writer():
            for number in range(count):
                self.add()  # Inactive rows never belong to the partial index.
                actor = CallerIdentity(f"10000000-0000-4000-8000-{number+100:012x}")
                self.add("in_progress", actor)
                pending = self.add("in_progress", CallerIdentity(f"20000000-0000-4000-8000-{number+100:012x}"))
                self.move(pending, "review_pending", CallerIdentity(f"20000000-0000-4000-8000-{number+100:012x}"))
                task_id = f"foreign-{number}"
                project = f"other-project-{number}"
                self.connection.execute("INSERT INTO tasks VALUES (?,?,?)", (project, task_id, "in_progress"))
                ownership.initialize_task(self.connection, project_id=project, task_id=task_id,
                    status="in_progress", caller=X, now=NOW)

    def probes(self):
        with self.writer():
            update = self.add()
            basis = self.basis(update)
            execution = ownership._new_execution(self.connection, basis, NOW)
            insert = self.add()
            insert_basis = self.basis(insert)
            insert_execution = ownership._new_execution(self.connection, insert_basis, NOW)
            self.connection.execute("DELETE FROM task_ownership WHERE task_id=?", (insert,))
        return {
            "service": lambda: ownership._free_slot(self.connection, project_id=PROJECT, task_id=update, actor=X.session_id),
            "update_trigger": lambda: self.connection.execute(
                "UPDATE task_ownership SET execution_id=?,generation=1,state='owned',owner_session_id=? WHERE task_id=?",
                (execution, X.session_id, update)),
            "insert_trigger": lambda: self.connection.execute(
                "INSERT INTO task_ownership(task_id,project_id,execution_id,generation,state,owner_session_id,completion_session_id) "
                "VALUES (?,?,?,1,'owned',?,NULL)", (insert, PROJECT, insert_execution, X.session_id)),
        }

    def test_actual_service_and_both_triggers_have_bounded_search_with_unrelated_growth(self):
        observations = {}
        for size in (100, 1000):
            # Each size gets the same stock ownership/policy DDL with real rows.
            fixture = IndexedSessionSlotTests()
            fixture.setUp()
            try:
                fixture.seed_noise(size)
                probes = fixture.probes()
                indexed = {name: fixture.measure(operation) for name, operation in probes.items()}
                service_sql = next(sql for sql in indexed["service"][1] if sql.startswith("SELECT 1 FROM task_ownership"))
                plan = tuple(tuple(row) for row in fixture.connection.execute("EXPLAIN QUERY PLAN " + service_sql))
                self.assertTrue(any(INDEX_NAME in row[-1] and "SEARCH" in row[-1] for row in plan), plan)
                fixture.connection.execute("DROP INDEX " + INDEX_NAME)
                fixture.connection.commit()
                baseline = {name: fixture.measure(operation)[0] for name, operation in probes.items()}
                observations[size] = ({name: result[0] for name, result in indexed.items()}, baseline)
            finally:
                fixture.doCleanups()
        for name in observations[100][0]:
            with self.subTest(path=name):
                small, large = observations[100], observations[1000]
                self.assertLessEqual(large[0][name], small[0][name] + 50, observations)
                self.assertGreater(large[1][name], small[1][name] * 5, observations)
                self.assertLess(large[0][name] * 10, large[1][name], observations)

    def test_index_is_nonunique_and_legacy_overlap_still_blocks_reacquisition(self):
        with self.writer():
            a = self.add("in_progress")
            self.move(a, "review_pending")
            b = self.add()
            old = self.basis(b)
            execution = ownership._new_execution(self.connection, old, NOW)
            # A disposable legacy fixture: bypass only the acquisition guard,
            # retaining every owned-column and foreign-key contract.
            guard = self.connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_ownership_slot_update'").fetchone()[0]
            self.connection.execute("DROP TRIGGER trg_task_ownership_slot_update")
            self.connection.execute("UPDATE task_ownership SET execution_id=?,generation=1,state='completion_only',completion_session_id=? WHERE task_id=?",
                (execution, X.session_id, b))
            self.connection.execute("UPDATE tasks SET status='review_pending' WHERE task_id=?", (b,))
            self.connection.execute(guard)
            self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM task_ownership WHERE completion_session_id=?", (X.session_id,)).fetchone()[0], 2)
            self.assert_error("session_task_in_progress", lambda: self.move(a, "in_progress"))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "session_task_in_progress"):
                self.connection.execute("UPDATE task_ownership SET state='owned',owner_session_id=completion_session_id,completion_session_id=NULL WHERE task_id=?", (a,))
            self.move(b, "cancelled")
            self.move(a, "in_progress")
        index = next(row for row in self.connection.execute("PRAGMA index_list(task_ownership)") if row[1] == INDEX_NAME)
        self.assertEqual(index[2], 0)
        self.assertEqual(index[4], 1)
