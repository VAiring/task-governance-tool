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
from task_governance_tool import schema_evidence_reference_index as schema
from task_governance_tool.project_binding_repository import read_project_binding_state
from task_governance_tool.sqlite_admission import (
    AdmissionConnection, admission_scope, observe_retained_task_connection, read_epoch,
    retained_task_read, same_read_fact, structural_read,
)
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

    def test_retained_version_expires_on_commit_replacement_and_local_schema_change(self):
        with closing(s.connect_readonly(self.target.db_path)) as connection:
            trace = []
            connection.set_trace_callback(trace.append)
            def versions():
                return sum("COUNT(*) AS marker_count FROM schema_migrations" in sql for sql in trace)
            with retained_task_read():
                observe_retained_task_connection(connection)
                self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
                connection.execute("SAVEPOINT nested")
                connection.execute("WITH r AS (SELECT 1) SELECT * FROM r").fetchall()
                connection.execute("ROLLBACK TO nested")
                connection.execute("RELEASE nested")
                self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
                self.assertEqual(versions(), 1)
                connection.commit()
                connection.execute("BEGIN")
                self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
                self.assertEqual(versions(), 2)
            self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
            self.assertEqual(versions(), 3)
            with retained_task_read(), closing(s.connect_readonly(self.target.db_path)) as other:
                observe_retained_task_connection(connection)
                self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
                connection.execute("PRAGMA query_only=OFF")
                connection.execute("CREATE TEMP TABLE schema_migrations(version)")
                connection.execute("INSERT INTO temp.schema_migrations VALUES (2)")
                self.assertEqual(s.current_schema_version(connection), 2)
                self.assertEqual(s.current_schema_version(other), s.SCHEMA_VERSION)
                connection.execute("UPDATE temp.schema_migrations SET version=-1")
                with self.assertRaises(s.StorageError):
                    s.current_schema_version(connection)

    def test_retained_version_falls_back_on_writable_connection_and_native_control(self):
        for opener in (s.connect_existing, s.connect_readonly):
            with closing(opener(self.target.db_path)) as connection, retained_task_read():
                if not connection.in_transaction:
                    connection.execute("BEGIN")
                trace = []
                connection.set_trace_callback(trace.append)
                observe_retained_task_connection(connection)
                s.current_schema_version(connection)
                s.current_schema_version(connection)
                if opener is s.connect_readonly:
                    connection.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_FKEY, True)
                    s.current_schema_version(connection)
                self.assertEqual(sum("COUNT(*) AS marker_count" in sql for sql in trace), 2)

    def test_retained_version_expires_on_cte_writes_even_when_query_only_rejects_them(self):
        for statement in ("UPDATE schema_migrations SET name=name",
                          "DELETE FROM schema_migrations",
                          "INSERT INTO schema_migrations SELECT * FROM schema_migrations",
                          "REPLACE INTO schema_migrations SELECT * FROM schema_migrations",
                          "/*" + "x" * 4100 + "*/ uPdAtE schema_migrations SET name=name"):
            with self.subTest(statement=statement), closing(s.connect_readonly(self.target.db_path)) as connection:
                trace = []
                connection.set_trace_callback(trace.append)
                with retained_task_read():
                    observe_retained_task_connection(connection)
                    self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
                    with self.assertRaises(sqlite3.OperationalError) as denied:
                        connection.execute("WITH r AS (SELECT 1) " + statement)
                    self.assertEqual(denied.exception.sqlite_errorcode, sqlite3.SQLITE_READONLY)
                    self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
                self.assertEqual(sum("COUNT(*) AS marker_count" in sql for sql in trace), 2)

    def test_authorizer_replacement_cannot_reuse_either_version_fact(self):
        def deny_markers(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table == "schema_migrations":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        with closing(s.connect_readonly(self.target.db_path)) as connection, structural_read(), retained_task_read():
            observe_retained_task_connection(connection)
            self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
            connection.set_authorizer(deny_markers)
            with self.assertRaises(sqlite3.DatabaseError) as denied:
                s.current_schema_version(connection)
            self.assertEqual(denied.exception.sqlite_errorcode, sqlite3.SQLITE_AUTH)
            connection.set_authorizer(None)
            self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)

    def test_transient_factory_change_during_version_acquisition_is_never_reused(self):
        with closing(s.connect_readonly(self.target.db_path)) as connection, structural_read(), retained_task_read():
            observe_retained_task_connection(connection)
            def callback(sql):
                if "SELECT 1 FROM sqlite_master" in sql:
                    connection.row_factory = lambda cursor, row: {"marker_count": 1, "version": 1}
                elif "COUNT(*) AS marker_count" in sql:
                    # The executing cursor already inherited the transient factory.
                    connection.row_factory = sqlite3.Row
            connection.set_trace_callback(callback)
            self.assertEqual(s.current_schema_version(connection), 1)
            self.assertIs(connection.row_factory, sqlite3.Row)
            connection.set_trace_callback(None)
            self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)

            @same_read_fact
            def read_text(reader):
                return reader.execute("SELECT 'original'").fetchone()[0]
            def transient_text(raw):
                connection.text_factory = str
                return "transient"
            def replace_text(sql):
                if sql == "SELECT 'original'":
                    connection.text_factory = transient_text
            connection.set_trace_callback(replace_text)
            self.assertEqual(read_text(connection), "transient")
            self.assertIs(connection.text_factory, str)
            connection.set_trace_callback(None)
            self.assertEqual(read_text(connection), "original")

    def test_reinitialized_connection_replaces_the_observer_and_old_owners_cannot_release_it(self):
        def prepare(reader, version):
            reader.row_factory = sqlite3.Row
            reader.execute("CREATE TABLE schema_migrations(version)")
            reader.execute("INSERT INTO schema_migrations VALUES (?)", (version,))
            reader.commit()
            reader.execute("PRAGMA query_only=ON")
            reader.execute("BEGIN")

        with closing(sqlite3.connect(":memory:", factory=AdmissionConnection)) as connection:
            prepare(connection, 1)
            with structural_read():
                with retained_task_read():
                    observe_retained_task_connection(connection)
                    self.assertEqual(s.current_schema_version(connection), 1)
                    old_epoch = read_epoch(connection)
                    connection.__init__(":memory:")
                    prepare(connection, 2)
                    self.assertIsNone(read_epoch(connection))
                    self.assertEqual(s.current_schema_version(connection), 2)
                    self.assertNotEqual(read_epoch(connection), old_epoch)
                self.assertIsNotNone(read_epoch(connection))
                connection.execute("PRAGMA query_only=OFF")
                connection.execute("UPDATE schema_migrations SET version=3")
                self.assertEqual(s.current_schema_version(connection), 3)
            self.assertIsNone(read_epoch(connection))
            connection.rollback()
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            with retained_task_read(), structural_read():
                observe_retained_task_connection(connection)
                self.assertEqual(s.current_schema_version(connection), 2)
                connection.__init__(":memory:")
            self.assertFalse(connection.in_transaction)

    def test_replacing_aggregate_used_by_version_query_requires_a_fresh_read(self):
        class ReplacementMax:
            def step(self, value):
                pass

            def finalize(self):
                return 1

        with closing(s.connect_readonly(self.target.db_path)) as connection, structural_read(), retained_task_read():
            observe_retained_task_connection(connection)
            self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)
            connection.create_aggregate("max", 1, ReplacementMax)
            self.assertEqual(s.current_schema_version(connection), 1)

    def test_control_probes_are_once_per_observed_epoch_including_nested_reads(self):
        controls = {"PRAGMA schema_version", "PRAGMA temp.schema_version",
                    "PRAGMA query_only", "PRAGMA foreign_keys"}
        with closing(s.connect_readonly(self.target.db_path)) as connection:
            trace = []
            connection.set_trace_callback(trace.append)
            with structural_read():
                s._validate_current_schema_contract(connection)
                first = [sql for sql in trace if sql in controls]
                for _ in range(4):
                    with structural_read():
                        s._validate_current_schema_contract(connection)
                self.assertEqual([sql for sql in trace if sql in controls], first)
                # The owned-schema helper retains its independent cookie checks;
                # the reuse observer itself reads each control only once.
                self.assertEqual(first.count("PRAGMA temp.schema_version"), 1)
                self.assertEqual(first.count("PRAGMA query_only"), 1)
                self.assertEqual(first.count("PRAGMA foreign_keys"), 1)
                connection.cursor().execute("SAVEPOINT boundary")
                s._validate_current_schema_contract(connection)
                self.assertEqual(trace.count("PRAGMA temp.schema_version"), 2)

    def test_native_configuration_and_automatic_rollback_expire_observations(self):
        with closing(s.connect_existing(self.target.db_path)) as connection:
            connection.execute("CREATE TABLE rollback_probe(value PRIMARY KEY)")
            connection.execute("INSERT INTO rollback_probe VALUES (1)")
            connection.commit()
            connection.execute("BEGIN")
            with structural_read(), mock.patch.object(schema, "validate_owned_contract",
                    wraps=schema.validate_owned_contract) as owned:
                s._validate_current_schema_contract(connection)
                connection.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_FKEY, False)
                s._validate_current_schema_contract(connection)
                self.assertEqual(owned.call_count, 2)
                connection.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_FKEY, True)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.cursor().execute("INSERT OR ROLLBACK INTO rollback_probe VALUES (1)")
                self.assertFalse(connection.in_transaction)
                connection.execute("BEGIN")
                s._validate_current_schema_contract(connection)
                self.assertEqual(owned.call_count, 3)

    def test_control_probe_callback_change_never_publishes_a_stale_fact(self):
        with closing(s.connect_readonly(self.target.db_path)) as connection:
            changed, calls = [], []
            def callback(sql):
                if sql == "PRAGMA schema_version" and not changed:
                    changed.append(True)
                    connection.execute("PRAGMA query_only=OFF")
            @same_read_fact
            def fact(current):
                calls.append(True)
                return current.execute("SELECT 1").fetchone()[0]
            connection.set_trace_callback(callback)
            with structural_read():
                self.assertEqual(fact(connection), 1)
                self.assertEqual(fact(connection), 1)
                self.assertEqual(fact(connection), 1)
            self.assertEqual(len(calls), 2)

    def test_failed_native_copy_and_deserialize_expire_destination_facts(self):
        with closing(s.connect_readonly(self.target.db_path)) as destination, \
             closing(s.connect_readonly(self.target.db_path)) as source:
            with structural_read(), mock.patch.object(schema, "validate_owned_contract",
                    wraps=schema.validate_owned_contract) as owned:
                s._validate_current_schema_contract(destination)
                with self.assertRaises(TypeError):
                    destination.deserialize(object())
                s._validate_current_schema_contract(destination)
                self.assertEqual(owned.call_count, 2)
                # A query-only destination refuses native copy; no copy result
                # is treated as known or reusable after the failed attempt.
                with self.assertRaises(sqlite3.Error):
                    source.backup(destination)
                s._validate_current_schema_contract(destination)
                self.assertEqual(owned.call_count, 3)

    def test_native_configuration_conversion_cannot_publish_surviving_facts(self):
        for fail in (False, True):
            with self.subTest(fail=fail), closing(s.connect_existing(self.target.db_path)) as connection:
                connection.execute("BEGIN")
                calls, during = [], []
                @same_read_fact
                def fact(current):
                    calls.append(True)
                    return current.execute("PRAGMA foreign_keys").fetchone()[0]
                class Enable:
                    def __bool__(self):
                        during.append(fact(connection))
                        if fail:
                            raise ValueError("conversion interrupted")
                        return False
                with structural_read():
                    self.assertEqual(fact(connection), 1)
                    if fail:
                        with self.assertRaises(ValueError):
                            connection.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_FKEY, Enable())
                    else:
                        connection.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_FKEY, Enable())
                    previous_calls = len(calls)
                    self.assertEqual(during, [1])
                    self.assertEqual(fact(connection), int(fail))
                    self.assertEqual(len(calls), previous_calls + 1)
                    self.assertEqual(fact(connection), int(fail))
                    self.assertEqual(len(calls), previous_calls + 1)

    def test_trace_callback_cannot_publish_pre_statement_facts(self):
        cases = (
            ("PRAGMA query_only=ON", "PRAGMA query_only", 0, 1),
            ("CREATE TEMP TABLE callback_probe(value)",
             "SELECT count(*) FROM sqlite_temp_master WHERE name='callback_probe'", 0, 1),
        )
        for statement, query, before, after in cases:
            for fail in (False, True):
                with self.subTest(statement=statement, fail=fail), \
                     closing(s.connect_existing(self.target.db_path)) as connection:
                    connection.execute("BEGIN")
                    during = []
                    @same_read_fact
                    def fact(current):
                        return current.execute(query).fetchone()[0]
                    def callback(sql):
                        if sql == statement:
                            during.append((fact(connection), connection.execute(query).fetchone()[0]))
                            if fail:
                                raise ValueError("trace interrupted")
                    connection.set_trace_callback(callback)
                    with structural_read():
                        self.assertEqual(fact(connection), before)
                        connection.execute(statement)
                        # PRAGMA controls can change during preparation, while
                        # DDL takes effect after its trace callback. Preserve
                        # actual native timing rather than assuming either.
                        self.assertEqual(len(during), 1)
                        self.assertEqual(during[0][0], during[0][1])
                        self.assertEqual(fact(connection), after)
                        self.assertEqual(fact(connection), after)

    def test_native_copy_conversion_first_observation_expires_after_failure(self):
        with closing(s.connect_readonly(self.target.db_path)) as destination, \
             closing(s.connect_readonly(self.target.db_path)) as source:
            with structural_read(), mock.patch.object(schema, "validate_owned_contract",
                    wraps=schema.validate_owned_contract) as owned:
                class Pages:
                    def __index__(self):
                        s._validate_current_schema_contract(destination)
                        return 1
                # Watching begins only during native argument conversion.
                with self.assertRaises(sqlite3.Error):
                    source.backup(destination, pages=Pages())
                self.assertEqual(owned.call_count, 1)
                s._validate_current_schema_contract(destination)
                self.assertEqual(owned.call_count, 2)

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
