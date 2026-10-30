"""Reusable diagnostics: real SQLite contention, isolation and bounded retention."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "task-governance-tool/scripts"))
from task_governance_tool import sqlite_connection as connections
from task_governance_tool import sqlite_diagnostics as diagnostics
from task_governance_tool import state_paths

SECRET = "Authorization: Bearer private-diagnostic-sentinel"
TEST_ROOT = ROOT / ".tmp-sqlite-diagnostics-tests"


class SQLiteDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.entrypoint = self.root / "task-governance-tool/scripts/taskgov.py"
        for relative in ("AGENTS.md", "docs/specification.md", "docs/design.md", "plan.md",
                         "task-governance-tool/SKILL.md", "task-governance-tool/release-manifest.json",
                         "task-governance-tool/scripts/taskgov.py"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("isolated fixture", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        (self.root / ".gitignore").write_text("/.taskgov/\n", encoding="ascii")
        self.directory = self.root / ".taskgov/diagnostics/sqlite"
        self.directory.mkdir(parents=True)
        self.marker = self.directory / "enabled"
        self.enable()
        self.database = self.root / ".taskgov/taskgov.sqlite"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE items (value TEXT)")
            connection.execute("INSERT INTO items VALUES ('initial')")
        self.assertIsNone(diagnostics.current_collector())

    def enable(self, mode="transactions"):
        self.marker.write_bytes(f"taskgov-sqlite-diagnostics-v1 {mode}\n".encode("ascii"))

    def scope(self):
        return diagnostics.diagnostic_scope(self.root, entrypoint=self.entrypoint,
                                             operation="task", repo_explicit=True, validated=True)

    def documents(self):
        return [json.loads(path.read_bytes()) for path in sorted(self.directory.glob("trace-*.json"))]

    def records(self):
        return [record for document in self.documents() for record in document["records"]]

    def test_disabled_has_base_connection_and_no_output_or_ignore_subprocess(self):
        self.marker.unlink()
        with mock.patch("task_governance_tool.project_scope._state_is_ignored") as ignored:
            with self.scope() as collector, closing(connections.connect_existing(self.database)) as connection:
                self.assertIsNone(collector)
                from task_governance_tool.sqlite_file_access import GuardedConnection
                self.assertIs(type(connection), GuardedConnection if os.name == 'posix' else sqlite3.Connection)
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
        ignored.assert_not_called()
        self.assertEqual(self.documents(), [])

    def test_exception_and_path_properties_do_not_run_in_observer(self):
        from task_governance_tool.sqlite_transaction_trace import connect_traced
        accessed = []

        class AdapterError(Exception):
            @property
            def sqlite_errorcode(self):
                accessed.append('sqlite_errorcode')
                return 5

            @property
            def __dict__(self):
                accessed.append('__dict__')
                return {}

        original = AdapterError(SECRET)

        class Adapter:
            def __conform__(self, protocol):
                raise original

        class DatabasePath(type(self.database)):
            @property
            def name(self):
                accessed.append('name')
                return 'taskgov.sqlite'

        with self.scope(), closing(connect_traced(DatabasePath(self.database))) as connection:
            with self.assertRaises(AdapterError) as caught:
                connection.execute('SELECT ?', (Adapter(),))
            self.assertIs(caught.exception, original)
        self.assertEqual(accessed, [])
        failed = [record for record in self.records() if record['outcome'] == 'failed']
        self.assertEqual(len(failed), 1)
        self.assertNotIn('sqlite_errorcode', failed[0])

    def test_unvalidated_operation_does_not_publish_resolver_observations(self):
        with diagnostics.diagnostic_scope(self.root, entrypoint=self.entrypoint,
                operation='task', repo_explicit=True), closing(connections.connect_readonly(self.database)):
            pass
        self.assertEqual(self.documents(), [])

    def test_path_classification_does_not_execute_metaclass_equality(self):
        from task_governance_tool.sqlite_transaction_trace import connect_traced
        accessed = []
        database = str(self.database)

        class Meta(type):
            def __eq__(cls, other):
                accessed.append('type equality')
                return False

        class Database(metaclass=Meta):
            def __fspath__(self):
                accessed.append('native fspath')
                return database

        with self.scope(), closing(connect_traced(Database())) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM items').fetchone()[0], 1)
        self.assertEqual(accessed, ['native fspath'])
        self.assertTrue(self.documents())

    def test_scope_preserves_exception_without_touching_traceback_property(self):
        accessed = []

        class UntrustedError(Exception):
            @property
            def __traceback__(self):
                accessed.append('traceback')
                return None

        original = UntrustedError(SECRET)
        observed = None
        try:
            with self.scope(), closing(connections.connect_existing(self.database)):
                raise original
        except BaseException as error:
            observed = error
        self.assertIs(observed, original)
        self.assertEqual(accessed, [])
        self.assertTrue(self.documents()[0]['failed'])

    def test_unknown_diagnostic_labels_do_not_execute_hash_or_comparison(self):
        from task_governance_tool.sqlite_transaction_trace import connect_traced
        accessed = []

        class Label:
            def __hash__(self):
                accessed.append('hash')
                return 0

            def __eq__(self, other):
                accessed.append('equality')
                return False

        with self.scope(), closing(connect_traced(self.database, role=Label(), access=Label())) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM items').fetchone()[0], 1)
        self.assertEqual(accessed, [])
        self.assertTrue(self.documents())

    def test_real_begin_busy_is_not_a_started_transaction(self):
        with closing(sqlite3.connect(self.database)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with self.scope(), closing(connections.connect_existing(self.database, timeout=0.02)) as writer:
                with self.assertRaises(sqlite3.OperationalError):
                    writer.execute("BEGIN IMMEDIATE")
                self.assertFalse(writer.in_transaction)
            blocker.rollback()
        failed = [r for r in self.records() if r["outcome"] == "failed"]
        self.assertEqual([r["phase"] for r in failed], ["begin"])
        self.assertEqual(failed[0]["sqlite_errorcode"] & 255, sqlite3.SQLITE_BUSY)
        self.assertNotIn("transaction_elapsed_ns", failed[0])
        self.assertGreater(failed[0]["elapsed_ns"], 0)

    def test_adapter_exception_dictionaries_cannot_change_transaction(self):
        for variant in ('mapping', 'key'):
            with self.subTest(variant=variant), self.scope(), \
                    closing(connections.connect_existing(self.database)) as writer:
                calls = []

                def interfere():
                    calls.append('rollback')
                    writer.rollback()

                class Fields(dict):
                    def get(self, *args):
                        interfere()
                        return 5

                class Key(str):
                    __hash__ = str.__hash__

                    def __eq__(self, other):
                        interfere()
                        return str.__eq__(self, other)

                original = ValueError('adapter failure')
                original.__dict__ = (Fields(sqlite_errorcode=5) if variant == 'mapping'
                                     else {Key('sqlite_errorcode'): 5})

                class Adapter:
                    def __conform__(self, protocol):
                        raise original

                writer.execute('BEGIN')
                with self.assertRaises(ValueError) as caught:
                    writer.execute('INSERT INTO items VALUES (?)', (Adapter(),))
                self.assertIs(caught.exception, original)
                self.assertEqual(calls, [])
                self.assertTrue(writer.in_transaction)
                writer.rollback()

    def test_diagnostic_globals_and_sql_keywords_avoid_foreign_lookup(self):
        from task_governance_tool import sqlite_transaction_trace as trace
        calls = []

        class Key(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                calls.append('key equality')
                return str.__eq__(self, other)

        namespace = {Key('__name__'): 'task_governance_tool.storage', 'origin': trace._origin}
        exec('def probe(): return origin()', namespace)
        probe = namespace['probe']
        keywords = {Key('sql'): 'BEGIN'}
        calls.clear()
        probe()
        self.assertEqual(trace._control((), keywords), ('statement', None))
        self.assertEqual(calls, [])

    def test_foreign_factory_keyword_delegates_without_extra_comparison(self):
        from task_governance_tool import sqlite_transaction_trace as trace
        calls = []

        class Key(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                calls.append('key equality')
                return str.__eq__(self, other)

        keywords = {Key('factory'): sqlite3.Connection}
        counts = []
        for enabled in (False, True):
            if enabled:
                self.enable()
            else:
                self.marker.unlink()
            calls.clear()
            with self.scope(), closing(trace.connect_traced(self.database, **keywords)) as connection:
                self.assertIs(type(connection), sqlite3.Connection)
            counts.append(len(calls))
        self.assertEqual(counts[0], counts[1])
        self.assertEqual(self.documents(), [])

    def test_real_context_commit_busy_rolls_back_and_keeps_primary_exception(self):
        with closing(sqlite3.connect(self.database)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM items").fetchall()
            with self.scope(), closing(connections.connect_existing(self.database, timeout=0.02)) as writer:
                with self.assertRaises(sqlite3.OperationalError):
                    with writer:
                        writer.execute("BEGIN IMMEDIATE")
                        writer.execute("INSERT INTO items VALUES (?)", (SECRET,))
                self.assertFalse(writer.in_transaction)
                self.assertEqual(writer.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
                self.assertEqual(self.documents(), [])  # No logging while connections remain open.
            reader.rollback()
        records = self.records()
        failed = [r for r in records if r["outcome"] == "failed" and r["phase"] == "commit"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["sqlite_errorcode"] & 255, sqlite3.SQLITE_BUSY)
        self.assertTrue(any(r.get("end_reason") == "ended_after_commit_failure" for r in records))
        self.assertNotIn(SECRET, json.dumps(self.documents()))

    def test_explicit_failed_commit_stays_active_until_rollback(self):
        with closing(sqlite3.connect(self.database)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM items").fetchall()
            with self.scope(), closing(connections.connect_existing(self.database, timeout=0.02)) as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("INSERT INTO items VALUES ('pending')")
                with self.assertRaises(sqlite3.OperationalError):
                    writer.commit()
                self.assertTrue(writer.in_transaction)
                writer.rollback()
                self.assertFalse(writer.in_transaction)
            reader.rollback()
        self.assertTrue(any(r["phase"] == "commit" and r["outcome"] == "failed"
                            and r["in_transaction"] for r in self.records()))
        self.assertTrue(any(r["phase"] == "rollback" for r in self.records()))

    def test_successful_read_close_reports_lifetime_and_flushes_after_close(self):
        with self.scope() as collector:
            with closing(connections.connect_readonly(self.database)) as reader:
                reader.execute("SELECT * FROM items").fetchall()
                time.sleep(0.005)
                self.assertEqual(self.documents(), [])
            self.assertEqual(self.documents(), [])
        self.assertEqual(len(self.documents()), 1)
        spans = [r["transaction_elapsed_ns"] for r in self.records() if "transaction_elapsed_ns" in r]
        self.assertGreater(max(spans), 0)
        self.assertTrue(any(r["role"] == "core" and r["access"] == "readonly" for r in self.records()))
        self.assertGreater(collector.instrumentation_ns, 0)
        self.assertGreater(collector.admission_ns, 0)

    def test_implicit_cursor_savepoints_and_body_exception_preserve_driver_behavior(self):
        original = RuntimeError(SECRET)
        with self.scope(), closing(connections.connect_existing(self.database)) as connection:
            cursor = connection.cursor()
            cursor.execute("INSERT INTO items VALUES (?)", (SECRET,))
            self.assertTrue(connection.in_transaction)
            cursor.execute("SAVEPOINT inner_scope")
            cursor.execute("ROLLBACK TO inner_scope")
            cursor.execute("RELEASE inner_scope")
            self.assertTrue(connection.in_transaction)
            connection.commit()
            with self.assertRaises(RuntimeError) as caught:
                with connection:
                    connection.execute("INSERT INTO items VALUES ('rollback')")
                    raise original
            self.assertIs(caught.exception, original)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)
        self.assertTrue(any(r["phase"] == "implicit_begin" for r in self.records()))
        self.assertNotIn(SECRET, json.dumps(self.documents()))

    def test_script_and_statement_failures_never_retain_sql_or_exception_text(self):
        with self.scope(), closing(connections.connect_existing(self.database)) as connection:
            connection.executescript("BEGIN; INSERT INTO items VALUES ('script'); COMMIT;")
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(f'SELECT * FROM "{SECRET}"')
        self.assertTrue(any(r["phase"] == "script" for r in self.records()))
        self.assertTrue(any(r["phase"] == "statement" and r["outcome"] == "failed" for r in self.records()))
        data = json.dumps(self.documents())
        self.assertNotIn(SECRET, data)
        self.assertNotIn(str(self.root), data)
        self.assertNotIn("INSERT INTO", data)

    def test_nested_scope_publishes_once_and_unclosed_connection_suppresses_output(self):
        with self.scope() as first:
            with self.scope() as second, closing(connections.connect_existing(self.database)) as connection:
                self.assertIs(first, second)
                connection.execute("BEGIN")
                connection.rollback()
            self.assertEqual(self.documents(), [])
        self.assertEqual(len(self.documents()), 1)
        with self.scope():
            unclosed = connections.connect_existing(self.database)
            unclosed.execute("BEGIN")
        self.assertEqual(len(self.documents()), 1)
        unclosed.close()
        self.assertIsNone(diagnostics.current_collector())

    def test_logging_and_sink_errors_cannot_undo_commit_or_replace_sqlite_error(self):
        with self.scope(), mock.patch.object(diagnostics.Collector, "event", side_effect=RuntimeError(SECRET)):
            with closing(connections.connect_existing(self.database)) as connection, connection:
                connection.execute("INSERT INTO items VALUES ('saved')")
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("SELECT * FROM missing")
        with mock.patch.object(state_paths, "create_exclusive_durable_file", side_effect=OSError(SECRET)):
            with self.scope(), closing(connections.connect_existing(self.database)) as connection, connection:
                connection.execute("INSERT INTO items VALUES ('also saved')")
        with closing(sqlite3.connect(self.database)) as check:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM items").fetchone()[0], 3)
        self.assertEqual(self.documents(), [])

    def test_marker_modes_exact_opt_in_and_failures_keep_failed_operation_only(self):
        for payload in (b"taskgov-sqlite-diagnostics-v1 transactions\r\n", b"x" * 65, SECRET.encode()):
            self.marker.write_bytes(payload)
            with self.scope() as collector:
                self.assertIsNone(collector)
        self.enable("failures")
        with self.scope(), closing(connections.connect_existing(self.database)) as connection:
            connection.execute("BEGIN")
            connection.rollback()
        self.assertEqual(self.documents(), [])
        with self.scope(), closing(connections.connect_existing(self.database)) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("SELECT * FROM missing")
        self.assertEqual(len(self.documents()), 1)

    def test_changed_ignore_or_removed_marker_blocks_publication(self):
        for change in (lambda: self.marker.unlink(),
                       lambda: (self.root / ".gitignore").write_text("", encoding="ascii")):
            self.enable()
            (self.root / ".gitignore").write_text("/.taskgov/\n", encoding="ascii")
            with self.scope(), closing(connections.connect_existing(self.database)) as connection:
                connection.execute("BEGIN")
                connection.rollback()
                change()
        self.assertEqual(self.documents(), [])

    def test_bounded_buffer_retains_late_failure_and_slots_never_overwrite(self):
        with self.scope() as collector:
            identity = collector.register_connection("core", "readwrite", SECRET)
            for _ in range(400):
                collector.event(identity, "begin", "succeeded", 1, origin=SECRET)
            collector.event(identity, "commit", "failed", 2, sqlite_errorcode=sqlite3.SQLITE_BUSY)
            collector.closed(identity)
        document = self.documents()[0]
        self.assertLessEqual(len(document["records"]), diagnostics.MAX_RECORDS)
        self.assertGreater(document["records_dropped"], 0)
        self.assertTrue(any(r["phase"] == "commit" and r["outcome"] == "failed" for r in document["records"]))
        self.assertNotIn(SECRET, json.dumps(document))
        first = (self.directory / "trace-000.json").read_bytes()
        self.assertLessEqual(len(first), diagnostics.MAX_BYTES)
        for slot in range(1, diagnostics.MAX_FILES):
            (self.directory / f"trace-{slot:03d}.json").write_bytes(b"occupied")
        with self.scope(), closing(connections.connect_existing(self.database)) as connection:
            connection.execute("BEGIN")
            connection.rollback()
        self.assertEqual((self.directory / "trace-000.json").read_bytes(), first)
        self.assertEqual(len(list(self.directory.glob("trace-*.json"))), diagnostics.MAX_FILES)

    def test_symlink_marker_cannot_enable(self):
        target = self.root / "marker-target"
        target.write_bytes(self.marker.read_bytes())
        self.marker.unlink()
        try:
            self.marker.symlink_to(target)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        with self.scope() as collector:
            self.assertIsNone(collector)
        self.assertEqual(self.documents(), [])

    def test_separation_preserves_diagnostics_without_treating_them_as_state(self):
        from task_governance_tool.state_separation import inspect_separation, SeparationError
        state = self.root / "isolated-state"
        diagnostic = state / "diagnostics"
        diagnostic.mkdir(parents=True)
        payload = diagnostic / "not-state.sqlite"
        payload.write_bytes(SECRET.encode())
        observed = inspect_separation(state_root=state, old_database=self.root / "absent")
        self.assertEqual(observed.state, "absent")
        self.assertEqual(payload.read_bytes(), SECRET.encode())
        (state / "unknown").mkdir()
        with self.assertRaises(SeparationError):
            inspect_separation(state_root=state, old_database=self.root / "absent")
        invalid = self.root / "invalid-state"
        invalid.mkdir()
        (invalid / "diagnostics").write_bytes(b"not a directory")
        with self.assertRaises(SeparationError):
            inspect_separation(state_root=invalid, old_database=self.root / "absent")

    def test_script_rebegin_has_no_fabricated_continuous_lifetime(self):
        with self.scope(), closing(connections.connect_existing(self.database)) as connection:
            connection.execute("BEGIN")
            connection.executescript("BEGIN;")
            # Native executescript first commits the active legacy transaction.
            self.assertTrue(connection.in_transaction)
            connection.commit()
        commits = [r for r in self.records() if r["phase"] == "commit"]
        self.assertTrue(commits)
        self.assertNotIn("transaction_elapsed_ns", commits[-1])

    def test_custom_factory_is_preserved_and_suppresses_unprovable_flush(self):
        from task_governance_tool.sqlite_transaction_trace import connect_traced
        with self.scope(), closing(connections.connect_existing(self.database)) as tracked:
            tracked.execute("BEGIN")
            tracked.rollback()
            untracked = connect_traced(self.database, factory=sqlite3.Connection)
            self.assertIs(type(untracked), sqlite3.Connection)
        self.assertEqual(self.documents(), [])
        untracked.close()


if __name__ == "__main__":
    unittest.main()
