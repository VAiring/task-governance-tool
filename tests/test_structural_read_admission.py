"""Ordinary structural entry, explicit audits and actual read-epoch reuse."""
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
from time import perf_counter
import unittest
from unittest import mock

from task_governance_tool import storage as s, state_resolver as resolver
from task_governance_tool import schema_viewer_finding_index as schema
from task_governance_tool.project_binding_repository import read_project_binding_state
from task_governance_tool.sqlite_admission import admission_scope
from tests.test_routine_backup import make_target


class StructuralReadAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.target = make_target(root, enabled=False)
        cls.saved = root / "saved.sqlite"
        shutil.copyfile(cls.target.db_path, cls.saved)

    def setUp(self):
        shutil.copyfile(self.saved, self.target.db_path)

    def inspect(self, *, local, doctor=False, recovery=False):
        trace = []
        work = [0]
        opener = resolver.connect_readonly
        def connect(*args, **kwargs):
            connection = opener(*args, **kwargs)
            connection.set_trace_callback(trace.append)
            connection.set_progress_handler(lambda: work.__setitem__(0, work[0] + 1) or 0, 1)
            return connection
        started = perf_counter()
        with mock.patch.object(resolver, "connect_readonly", side_effect=connect):
            result = resolver._inspect_database(self.target.db_path, mutable=True,
                operation_local=local, classify_recovery_content=recovery,
                doctor_current_root=resolver.observe_current_root(self.target.project.canonical_repo) if doctor else None)
        return result, trace, work[0], perf_counter() - started

    def test_resolver_modes_keep_full_audit_explicit(self):
        for local, recovery in ((True, False), (False, False), (True, True)):
            with self.subTest(local=local, recovery=recovery):
                _, trace, _, _ = self.inspect(local=local, recovery=recovery)
                full = not local or recovery
                self.assertEqual("PRAGMA quick_check" in trace, full)
                self.assertEqual("PRAGMA foreign_key_check" in trace, full)
                cycles = [sql for sql in trace if "SELECT COUNT(*) FROM task_completion_cycles WHERE project_id" in sql]
                self.assertEqual(bool(cycles), full)
        _, trace, _, _ = self.inspect(local=True, doctor=True)
        self.assertIn("PRAGMA quick_check", trace)
        self.assertIn("PRAGMA foreign_key_check", trace)
        self.assertTrue(any("SELECT COUNT(*) FROM task_completion_cycles WHERE project_id" in sql for sql in trace))

    def test_same_read_shares_all_history_owned_schema_and_binding(self):
        with closing(s.connect_readonly(self.target.db_path)) as connection:
            trace = []
            connection.set_trace_callback(trace.append)
            @admission_scope
            def compose():
                for _ in range(3):
                    s.validate_current_database(connection, self.target, operation_local=True)
                    read_project_binding_state(connection)
            compose()
            self.assertEqual(sum("SELECT version,name,applied_at FROM schema_migrations" in sql for sql in trace), 1)
            self.assertEqual(sum(sql.startswith("SELECT type,name,tbl_name,sql FROM sqlite_master") for sql in trace), 1)
            self.assertEqual(sum("FROM project_path_binding_history" in sql and "ORDER BY" in sql for sql in trace), 1)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            # A standalone call after lexical exit performs a new admission.
            s.validate_current_database(connection, self.target, operation_local=True)
            self.assertEqual(sum("SELECT version,name,applied_at FROM schema_migrations" in sql for sql in trace), 2)

    def test_native_transaction_controls_expire_facts_without_consuming_savepoints(self):
        controls = (
            lambda c: (c.commit(), c.execute("BEGIN")),
            lambda c: (c.rollback(), c.execute("BEGIN")),
            lambda c: (c.cursor().execute("END"), c.cursor().execute("BEGIN")),
            lambda c: c.executescript("/* implicit commit, then restart */ BEGIN; COMMIT; BEGIN;"),
            lambda c: (c.execute("SAVEPOINT caller_owned"), c.execute("RELEASE caller_owned")),
            lambda c: (c.__exit__(None, None, None), c.execute("BEGIN")),
        )
        for boundary in controls:
            with self.subTest(boundary=boundary), closing(s.connect_readonly(self.target.db_path)) as connection:
                with mock.patch.object(schema, "validate_owned_contract", wraps=schema.validate_owned_contract) as owned:
                    @admission_scope
                    def compose():
                        s._validate_current_schema_contract(connection)
                        boundary(connection)
                        s._validate_current_schema_contract(connection)
                        connection.execute("SAVEPOINT nested_caller")
                        s._validate_current_schema_contract(connection)
                        connection.execute("RELEASE nested_caller")
                    compose()
                    self.assertEqual(owned.call_count, 3)

    def test_cursor_write_ddl_and_binding_mutation_invalidate_same_transaction(self):
        mutations = (
            "UPDATE schema_migrations SET name='wrong' WHERE version=1",
            "DROP INDEX idx_review_continuations_task_history",
            "UPDATE project_meta SET canonical_path_hash='" + "a" * 64 + "'",
        )
        for sql in mutations:
            with self.subTest(sql=sql), closing(s.connect_existing(self.target.db_path)) as connection:
                connection.execute("BEGIN")
                @admission_scope
                def compose():
                    s.validate_current_database(connection, self.target, operation_local=True)
                    connection.cursor().execute(sql)
                    with self.assertRaises(s.StorageError):
                        s.validate_current_database(connection, self.target, operation_local=True)
                compose()
                connection.rollback()

    def test_separate_connections_and_failed_validation_never_share_success(self):
        with closing(s.connect_readonly(self.target.db_path)) as first, closing(s.connect_readonly(self.target.db_path)) as second:
            with mock.patch.object(schema, "validate_owned_contract", wraps=schema.validate_owned_contract) as owned:
                @admission_scope
                def compose():
                    s._validate_current_schema_contract(first)
                    s._validate_current_schema_contract(second)
                compose()
                self.assertEqual(owned.call_count, 2)
            @admission_scope
            def failed_then_valid():
                with mock.patch.object(schema, "validate_owned_contract", side_effect=s.StorageError("internal_error", "fixture")):
                    with self.assertRaises(s.StorageError):
                        s._validate_current_schema_contract(first)
                s._validate_current_schema_contract(first)
            failed_then_valid()

    def test_read_to_writer_drift_requires_fresh_admission(self):
        @admission_scope
        def read_then_write():
            with closing(s.connect_readonly(self.target.db_path)) as reader:
                s._validate_current_schema_contract(reader)
            with closing(s.connect_existing(self.target.db_path)) as mutation:
                mutation.execute("DROP INDEX idx_review_continuations_task_history")
                mutation.commit()
            with closing(s.connect_existing(self.target.db_path)) as writer:
                with self.assertRaises(s.StorageError):
                    s.begin_initialized_write(writer, self.target, operation_local=True)
                self.assertFalse(writer.in_transaction)
        read_then_write()

    def test_query_only_and_comment_controls_expire_structural_results(self):
        with closing(s.connect_readonly(self.target.db_path)) as connection:
            with mock.patch.object(schema, "validate_owned_contract", wraps=schema.validate_owned_contract) as owned:
                @admission_scope
                def compose():
                    s._validate_current_schema_contract(connection)
                    connection.execute("PRAGMA query_only=OFF")
                    connection.execute("PRAGMA query_only=ON")
                    s._validate_current_schema_contract(connection)
                    connection.execute("/* explicit */ ROLLBACK")
                    connection.execute("-- explicit\n BEGIN")
                    s._validate_current_schema_contract(connection)
                compose()
                self.assertEqual(owned.call_count, 3)

    def test_trace_callback_replacement_and_native_connection_fallback(self):
        with closing(s.connect_readonly(self.target.db_path)) as connection:
            before, after = [], []
            connection.set_trace_callback(before.append)
            @admission_scope
            def compose():
                s._validate_current_schema_contract(connection)
                connection.set_trace_callback(after.append)
                connection.execute("SELECT 123")
                s._validate_current_schema_contract(connection)
            compose()
            connection.execute("SELECT 456")
            self.assertNotIn("SELECT 123", before)
            self.assertIn("SELECT 123", after)
            self.assertIn("SELECT 456", after)
        with closing(sqlite3.connect(self.target.db_path)) as connection:
            s.configure_connection(connection)
            connection.execute("BEGIN")
            with mock.patch.object(schema, "validate_owned_contract", wraps=schema.validate_owned_contract) as owned:
                @admission_scope
                def compose_native():
                    s._validate_current_schema_contract(connection)
                    s._validate_current_schema_contract(connection)
                compose_native()
                self.assertEqual(owned.call_count, 2)

    def test_ordinary_control_read_does_not_claim_global_cycle_equality(self):
        with closing(s.connect_readonly(self.target.db_path)) as connection:
            from dataclasses import replace
            state = s.read_evidence_projection_state(connection, project_id=self.target.project.project_id)
            with mock.patch.object(s, "read_evidence_projection_state", return_value=replace(state, source_generation=state.source_generation + 1)):
                s.validate_current_database(connection, self.target, operation_local=True)
                with self.assertRaises(s.StorageError):
                    s.validate_current_database(connection, self.target)

    def test_unrelated_rows_add_no_ordinary_scan_work_but_explicit_audit_sees_them(self):
        with closing(s.connect_existing(self.target.db_path)) as connection:
            connection.execute("CREATE TABLE audit_extra(value INTEGER, task_id TEXT REFERENCES tasks(task_id))")
            connection.commit()
        _, _, small_local, small_time = self.inspect(local=True)
        _, _, small_audit, _ = self.inspect(local=False)
        with closing(s.connect_existing(self.target.db_path)) as connection:
            connection.executemany("INSERT INTO audit_extra(value) VALUES (?)", ((i,) for i in range(20000)))
            connection.commit()
        _, _, large_local, large_time = self.inspect(local=True)
        _, _, large_audit, _ = self.inspect(local=False)
        self.assertLessEqual(large_local, small_local + 20)
        self.assertGreater(large_audit, small_audit + 20000)
        print(f"\nstructural admission VM steps ordinary={small_local}/{large_local}; audit={small_audit}/{large_audit}; ordinary seconds={small_time:.4f}/{large_time:.4f}")
        with closing(s.connect_existing(self.target.db_path)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("INSERT INTO audit_extra VALUES (1, 'foreign-task')")
            connection.commit()
        self.inspect(local=True)
        with self.assertRaises(resolver._ResolverFailure):
            self.inspect(local=False)


if __name__ == "__main__":
    unittest.main()
