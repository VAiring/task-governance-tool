"""Exact-content reuse keeps fresh canonical snapshots and fails closed.

All files and deliberate corruption belong to disposable resolver fixtures.
These tests use the real resolver, SQLite admission and physical hashing.
"""

from contextlib import closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.test_state_resolver import ResolverFixture, tree_snapshot
from task_governance_tool import state_resolver as resolver, storage, tasks
from task_governance_tool.state_paths import StatePathError


class DatabaseValidationReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = ResolverFixture(Path(temporary.name))
        self.target = self.fixture.initialize_fixed_uuid()
        self.reuse = resolver.DatabaseValidationReuse()
        self.validated = self.enterContext(mock.patch.object(
            resolver, "validate_current_database_structure",
            wraps=resolver.validate_current_database_structure))

    def resolve(self, *, reuse=None, **options):
        result = resolver.resolve_project_state(
            skill_root=self.fixture.skill_root, repo=self.fixture.repo,
            retain_read_connection=options.pop("retain_read_connection", True),
            validation_reuse=self.reuse if reuse is None else reuse, **options)
        if result.read_connection is not None:
            self.addCleanup(result.read_connection.close)
        return result

    def admitted(self, *, reuse=None, **options):
        result = self.resolve(reuse=reuse, **options)
        self.assertIsNone(result.error_code)
        self.assertEqual(result.binding, "matching")
        self.assertIsNotNone(result.read_connection)
        with closing(result.read_connection) as connection:
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            return result.target, connection.execute("PRAGMA user_version").fetchone()[0]

    def test_repetition_has_fresh_snapshots_and_platform_safe_validation(self):
        before = tree_snapshot(self.fixture.root)
        opened, statements = [], []
        real_open = resolver.connect_readonly
        def tracked(path):
            connection = real_open(path)
            opened.append(connection)
            connection.set_trace_callback(statements.append)
            return connection
        with mock.patch.object(resolver, "connect_readonly", side_effect=tracked), \
                mock.patch.object(resolver, "validate_current_database_binding",
                                  wraps=resolver.validate_current_database_binding) as binding, \
                mock.patch.object(resolver, "current_schema_version",
                                  wraps=resolver.current_schema_version) as schema:
            self.assertEqual(self.admitted(), self.admitted())
        expected = 1 if resolver._PHYSICAL_HASH_REUSE_SUPPORTED else 2
        self.assertEqual(self.validated.call_count, expected)
        self.assertEqual(binding.call_count, 2)
        self.assertEqual(schema.call_count, 2)
        self.assertEqual(statements.count("PRAGMA quick_check"), expected)
        self.assertEqual(statements.count("PRAGMA foreign_key_check"), expected)
        self.assertEqual(len(opened), 2)
        self.assertIsNot(opened[0], opened[1])
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        self.assertEqual(tree_snapshot(self.fixture.root), before)

    def test_separate_operation_and_explicit_clear_never_share_success(self):
        self.admitted()
        self.admitted(reuse=resolver.DatabaseValidationReuse())
        self.reuse.clear()
        self.admitted()
        self.assertEqual(self.validated.call_count, 3)

    def test_default_resolver_keeps_full_admission_without_hashing(self):
        with mock.patch.object(resolver, "hash_physical_file") as hashing:
            for _ in range(2):
                result = resolver.resolve_project_state(
                    skill_root=self.fixture.skill_root, repo=self.fixture.repo,
                    retain_read_connection=True)
                self.assertIsNone(result.error_code)
                result.read_connection.close()
        self.assertEqual(self.validated.call_count, 2)
        hashing.assert_not_called()

    def test_expired_usage_deadline_opens_no_database_and_later_read_is_fresh(self):
        with mock.patch("time.monotonic", return_value=100.0), \
                mock.patch.object(resolver, "connect_readonly", wraps=resolver.connect_readonly) as opened:
            result = self.resolve(operation_local=True, deadline=99.0)
        self.assertEqual(result.error_code, "project_state_unreadable")
        self.assertIsNone(result.read_connection)
        opened.assert_not_called()
        self.admitted(operation_local=True)

    def test_usage_deadline_reaches_real_admission_and_retained_sql_progress(self):
        before = tree_snapshot(self.fixture.root)
        clock = [100.0]
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(resolver, "connect_readonly", wraps=resolver.connect_readonly) as opened:
            result = self.resolve(operation_local=True, deadline=101.0)
            self.assertIsNone(result.error_code)
            self.assertEqual(opened.call_args.kwargs["timeout"], 0.05)
            self.assertTrue(self.validated.call_args.kwargs["operation_local"])
            clock[0] = 102.0
            with self.assertRaises(sqlite3.OperationalError):
                result.read_connection.execute("WITH RECURSIVE work(n) AS "
                    "(VALUES(1) UNION ALL SELECT n+1 FROM work WHERE n<100000) SELECT sum(n) FROM work").fetchone()
            result.read_connection.close()
        self.assertEqual(tree_snapshot(self.fixture.root), before)

    def test_operation_local_admission_never_uses_or_publishes_global_proof(self):
        self.admitted()
        statements = []
        original_open = resolver.connect_readonly
        def tracked(path):
            connection = original_open(path)
            connection.set_trace_callback(statements.append)
            return connection
        with mock.patch.object(resolver, "hash_physical_file") as hashing, \
                mock.patch.object(resolver, "connect_readonly", side_effect=tracked):
            self.admitted(operation_local=True)
        hashing.assert_not_called()
        self.assertEqual(statements.count("PRAGMA quick_check"), 1)
        self.assertEqual(statements.count("PRAGMA foreign_key_check"), 1)
        self.assertTrue(self.validated.call_args.kwargs["operation_local"])
        # Returning to global admission must validate again, even unchanged bytes.
        self.admitted()
        self.assertEqual(self.validated.call_count, 3)

    def test_operation_local_admission_still_rejects_owned_schema_damage(self):
        with closing(sqlite3.connect(self.target.db_path)) as connection:
            connection.execute("DROP TABLE review_receipt_sessions")
            connection.commit()
        before = tree_snapshot(self.fixture.root)
        result = self.resolve(operation_local=True)
        self.assertEqual(result.error_code, "project_state_unreadable")
        self.assertIsNone(result.read_connection)
        self.assertEqual(tree_snapshot(self.fixture.root), before)

    def test_operation_local_keeps_physical_continuity_without_a_global_proof(self):
        with mock.patch.object(resolver, "hash_physical_file") as hashing:
            self.admitted(operation_local=True)
            self.admitted(operation_local=True)
            self.assertIsNone(self.reuse._validated)
            replacement = self.target.db_path.with_suffix(".replacement")
            replacement.write_bytes(self.target.db_path.read_bytes())
            original_identity = self.target.db_path.stat().st_ino
            os.replace(replacement, self.target.db_path)
            self.assertNotEqual(self.target.db_path.stat().st_ino, original_identity)
            result = self.resolve(operation_local=True)
        hashing.assert_not_called()
        self.assertEqual(self.validated.call_count, 2)
        self.assertEqual(result.error_code, "project_state_unreadable")
        self.assertIsNone(result.read_connection)
        self.assertIsNone(self.reuse._identity)

    def test_nonretained_and_doctor_reads_clear_reuse_and_validate_normally(self):
        for options in (dict(retain_read_connection=False), dict(include_doctor_state=True)):
            with self.subTest(options=options):
                self.admitted()
                self.validated.reset_mock()
                with mock.patch.object(resolver, "hash_physical_file") as hashing:
                    result = self.resolve(**options)
                    self.assertIsNone(result.error_code)
                    if result.read_connection is not None:
                        result.read_connection.close()
                hashing.assert_not_called()
                self.admitted()
                self.assertEqual(self.validated.call_count, 2)

    def test_valid_database_change_revalidates_then_reads_new_snapshot(self):
        self.admitted()
        with closing(sqlite3.connect(self.target.db_path)) as connection:
            connection.execute("PRAGMA user_version=42")
        self.assertEqual(self.admitted()[1], 42)
        self.assertEqual(self.admitted()[1], 42)
        self.assertEqual(self.validated.call_count,
                         2 if resolver._PHYSICAL_HASH_REUSE_SUPPORTED else 3)

    def test_unsupported_hash_reuse_validates_fresh_state_and_ignores_prior_proof(self):
        self.admitted()
        # Construct a genuine earlier byte proof with no SQLite reader open.
        # It must never be consulted once physical hashing is unsupported.
        self.reuse._validated = resolver.hash_physical_file(
            self.target.db_path, root=self.target.db_path.parent)
        with closing(sqlite3.connect(self.target.db_path)) as writer:
            writer.execute("PRAGMA user_version=42")
        self.validated.reset_mock()
        with mock.patch.object(resolver, "_PHYSICAL_HASH_REUSE_SUPPORTED", False), \
                mock.patch.object(resolver, "hash_physical_file") as hashing, \
                mock.patch.object(self.reuse, "_matches", return_value=True) as matches:
            self.assertEqual(self.admitted()[1], 42)
            self.assertEqual(self.admitted()[1], 42)
        hashing.assert_not_called()
        matches.assert_not_called()
        self.assertEqual(self.validated.call_count, 2)

    def test_same_size_and_restored_mtime_semantic_corruption_is_not_reused(self):
        with closing(storage.connect(self.target.db_path)) as connection, connection:
            task = tasks.add_task(connection, self.target.project,
                database_target=self.target, title="Unselected history task",
                status="ready", review_tier=0, verification="Run focused tests").task
        note = "UniqueReusableValidationNote"
        with closing(sqlite3.connect(self.target.db_path)) as connection, connection:
            connection.execute("""INSERT INTO task_events(
task_event_id,project_id,task_id,event_type,summary,created_at,note_body)
VALUES (?,?,?,?,?,?,?)""", ("tg_event_ffffffffffffffff", self.target.project.project_id,
                task["task_id"], "note_added", "Added note", "2026-07-29T01:02:03Z", note))
        self.admitted()
        original = self.target.db_path.read_bytes()
        stamp = self.target.db_path.stat()
        self.assertEqual(original.count(note.encode()), 1)
        corrupt = original.replace(note.encode(), b" " * len(note))
        self.target.db_path.write_bytes(corrupt)
        os.utime(self.target.db_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(self.target.db_path.stat().st_size, stamp.st_size)
        self.assertEqual(self.target.db_path.stat().st_mtime_ns, stamp.st_mtime_ns)
        with closing(sqlite3.connect(self.target.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchall(), [("ok",)])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        rejected = self.resolve()
        self.assertEqual(rejected.error_code, "project_state_unreadable")
        self.assertIsNone(rejected.read_connection)
        self.assertEqual(self.validated.call_count, 2)
        self.target.db_path.write_bytes(original)
        os.utime(self.target.db_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.admitted()
        self.assertEqual(self.validated.call_count, 3)

    def test_identical_bytes_in_replaced_physical_file_reject_inflight_reuse(self):
        self.admitted()
        replacement = self.target.db_path.with_suffix(".replacement")
        replacement.write_bytes(self.target.db_path.read_bytes())
        original_identity = self.target.db_path.stat().st_ino
        os.replace(replacement, self.target.db_path)
        self.assertNotEqual(self.target.db_path.stat().st_ino, original_identity)
        result = self.resolve()
        self.assertEqual(result.error_code, "project_state_unreadable")
        self.assertIsNone(result.read_connection)
        self.admitted()
        self.assertEqual(self.validated.call_count, 2)

    def test_retained_snapshot_blocks_process_writer_after_admission_returns(self):
        writer_code = """
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], timeout=0)
try:
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("PRAGMA user_version=52")
    try:
        connection.commit()
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode != sqlite3.SQLITE_BUSY:
            raise
        connection.rollback()
        print("busy")
    else:
        print("committed")
finally:
    connection.close()
"""
        def attempt_write():
            completed = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", writer_code,
                 str(self.target.db_path)],
                capture_output=True, text=True, timeout=15, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            return completed.stdout.strip()

        # Failed commits roll back without changing the primary, so the second
        # admission exercises actual reuse on supported platforms. Nothing in
        # this process opens/closes a separate DB descriptor while readers live.
        for boundary in ("initial", "subsequent"):
            with self.subTest(boundary=boundary):
                result = self.resolve()
                self.assertIsNone(result.error_code)
                self.assertIsNotNone(result.read_connection)
                with closing(result.read_connection) as reader:
                    self.assertTrue(reader.in_transaction)
                    self.assertEqual(attempt_write(), "busy")
                    self.assertEqual(reader.execute("PRAGMA user_version").fetchone()[0], 0)
        self.assertEqual(self.validated.call_count,
                         1 if resolver._PHYSICAL_HASH_REUSE_SUPPORTED else 2)
        self.assertEqual(attempt_write(), "committed")
        self.assertEqual(self.admitted()[1], 52)

    def test_snapshot_read_and_hash_failures_close_and_invalidate_success(self):
        actual_open = resolver.connect_readonly
        failures = [
                ("current_schema_version", sqlite3.OperationalError("sentinel read failure")),
                ("validate_current_database_binding", RuntimeError("sentinel binding failure"))]
        if resolver._PHYSICAL_HASH_REUSE_SUPPORTED:
            failures.extend([
                ("hash_physical_file", StatePathError()),
                ("hash_physical_file", OSError("sentinel file failure"))])
        for name, failure in failures:
            with self.subTest(boundary=name, failure=type(failure).__name__):
                self.admitted()
                opened = []
                def tracked(path):
                    connection = actual_open(path)
                    opened.append(connection)
                    return connection
                with mock.patch.object(resolver, "connect_readonly", side_effect=tracked), \
                        mock.patch.object(resolver, name, side_effect=failure):
                    result = self.resolve()
                self.assertEqual(result.error_code, "project_state_unreadable")
                self.assertIsNone(result.read_connection)
                for connection in opened:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")
                calls = self.validated.call_count
                self.admitted()
                self.assertEqual(self.validated.call_count, calls + 1)

    def test_connect_busy_failure_invalidates_proof(self):
        self.admitted()
        with mock.patch.object(resolver, "connect_readonly",
                               side_effect=sqlite3.OperationalError("database is locked")):
            rejected = self.resolve()
        self.assertEqual(rejected.error_code, "database_busy")
        self.admitted()
        self.assertEqual(self.validated.call_count, 2)

    def test_wal_residue_and_activation_barrier_are_not_hidden_by_reuse(self):
        self.admitted()
        sidecar = Path(str(self.target.db_path) + "-wal")
        sidecar.write_bytes(b"")
        result = self.resolve()
        self.assertEqual(result.error_code, "unsupported_journal_mode")
        sidecar.unlink()
        self.admitted()
        marker = self.fixture.old_paths.database.read_bytes()
        self.fixture.old_paths.database.unlink()
        result = self.resolve()
        self.assertEqual(resolver.consumer_error_code(result), "migration_required")
        self.assertIsNone(result.read_connection)
        self.fixture.old_paths.database.write_bytes(marker)
        self.admitted()
        self.assertEqual(self.validated.call_count, 4)

    def test_changed_activation_record_forces_full_admission(self):
        self.admitted()
        record = self.fixture.write_record()
        self.fixture.write_record(replace(record, transition_id="f" * 32))
        self.admitted()
        self.admitted()
        self.assertEqual(self.validated.call_count,
                         2 if resolver._PHYSICAL_HASH_REUSE_SUPPORTED else 3)


if __name__ == "__main__":
    unittest.main()
