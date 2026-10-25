"""Task-local hints use disposable SQLite; never inspect the live project DB."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"))
from task_governance_tool import review_wait_control_observation as observation
from task_governance_tool.sqlite_connection import StorageError
from task_governance_tool.review_wait_runtime.review_wait_repository import ObservationBusy, RepositoryError


class ControlObservationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.sqlite3"
        # Values are intentionally small: these tests exercise observation,
        # not the full admission already required before its construction.
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE project_meta(project_id TEXT PRIMARY KEY, identity_scheme,
                    canonical_path_hash, binding_generation);
                CREATE TABLE tasks(task_id TEXT PRIMARY KEY, project_id, status,
                    current_contract_revision, review_target_kind, review_target_value,
                    review_target_base_revision, review_target_generation,
                    review_target_artifact_manifest_id, review_target_authority_snapshot_id,
                    review_target_acceptance_criterion_id, review_target_verification_criterion_id,
                    review_tier, completion_commit_required, external_revision_approved, updated_at);
                CREATE TABLE task_ownership(task_id TEXT PRIMARY KEY, project_id,
                    execution_id, generation, state, owner_session_id, completion_session_id);
                CREATE TABLE review_receipts(review_receipt_id TEXT PRIMARY KEY,
                    project_id, task_id, target_generation);
                CREATE INDEX idx_review_receipts_task_reviewer_generation
                    ON review_receipts(task_id,target_generation);
                CREATE TABLE review_findings(review_finding_id TEXT PRIMARY KEY,
                    review_receipt_id, status, severity);
                CREATE INDEX idx_review_findings_status_severity_receipt
                    ON review_findings(status,severity,review_receipt_id);
                INSERT INTO project_meta VALUES('p','scheme','hash',1);
                INSERT INTO tasks VALUES('a','p','in_progress',1,'git_snapshot','value','base',
                    1,'manifest','authority','acceptance','verification',2,1,0,'same timestamp');
                INSERT INTO tasks SELECT 'b',project_id,status,current_contract_revision,
                    review_target_kind,review_target_value,review_target_base_revision,
                    review_target_generation,review_target_artifact_manifest_id,
                    review_target_authority_snapshot_id,review_target_acceptance_criterion_id,
                    review_target_verification_criterion_id,review_tier,
                    completion_commit_required,external_revision_approved,updated_at
                    FROM tasks WHERE task_id='a';
                INSERT INTO task_ownership VALUES('a','p','execution',1,'owned','parent',NULL);
                INSERT INTO task_ownership VALUES('b','p','other',1,'owned','other-parent',NULL);
            """)
        self.observe = observation.ReviewWaitControlObservation(self.path, "p", "a")

    def write(self, sql, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(sql, parameters)

    def test_unchanged_100_ticks_open_no_sqlite_connection(self):
        before = self.path.read_bytes()
        self.assertTrue(self.observe())
        with mock.patch.object(observation, "connect_readonly", side_effect=AssertionError("unchanged SQL")):
            for _ in range(100):
                self.assertFalse(self.observe())
        self.assertEqual(self.path.read_bytes(), before)

    def test_other_task_commit_reads_one_task_snapshot_without_full_validation(self):
        self.observe()
        self.write("UPDATE tasks SET status='done' WHERE task_id='b'")
        statements, opened = [], []
        real_open = observation.connect_readonly
        def traced(path, **kwargs):
            connection = real_open(path, **kwargs)
            opened.append(connection)
            connection.set_trace_callback(statements.append)
            return connection
        with mock.patch.object(observation, "connect_readonly", side_effect=traced) as read:
            self.assertFalse(self.observe())
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.kwargs, {"timeout": 0})
        self.assertEqual(len(statements), 1)
        self.assertIn("SELECT task.status", statements[0])
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")

    def test_relevant_control_changes_include_same_timestamp_and_scalar_types(self):
        self.observe()
        changes = [
            "UPDATE tasks SET review_target_generation=2 WHERE task_id='a'",
            "UPDATE tasks SET status='paused' WHERE task_id='a'",
            "UPDATE tasks SET current_contract_revision=2 WHERE task_id='a'",
            "UPDATE task_ownership SET owner_session_id='new-parent' WHERE task_id='a'",
            "UPDATE task_ownership SET generation=2 WHERE task_id='a'",
            "UPDATE project_meta SET binding_generation=2 WHERE project_id='p'",
            "UPDATE project_meta SET canonical_path_hash='new-path' WHERE project_id='p'",
            "UPDATE tasks SET completion_commit_required=1.0 WHERE task_id='a'",
        ]
        for sql in changes:
            with self.subTest(sql=sql):
                self.write(sql)
                self.assertTrue(self.observe())
                self.assertFalse(self.observe())

    def test_receipts_and_blocker_resolution_are_task_local_across_generations(self):
        self.observe()
        self.write("INSERT INTO review_receipts VALUES('other','p','b',1)")
        self.assertFalse(self.observe())
        self.write("INSERT INTO review_receipts VALUES('current','p','a',1)")
        self.assertTrue(self.observe())
        self.write("INSERT INTO review_receipts VALUES('old','p','a',0)")
        self.assertFalse(self.observe())
        self.write("INSERT INTO review_findings VALUES('old-blocker','old','open','medium')")
        self.assertTrue(self.observe())
        self.write("INSERT INTO review_findings VALUES('other-blocker','other','open','high')")
        self.assertFalse(self.observe())
        self.write("UPDATE review_findings SET status='resolved' WHERE review_finding_id='old-blocker'")
        self.assertTrue(self.observe())
        self.write("INSERT INTO review_findings VALUES('non-blocker','current','open','low')")
        self.assertFalse(self.observe())

    def test_commit_same_physical_timestamp_is_seen_by_change_counter(self):
        self.observe()
        previous = self.path.stat()
        self.write("UPDATE task_ownership SET generation=2 WHERE task_id='a'")
        os.utime(self.path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertEqual(self.path.stat().st_size, previous.st_size)
        self.assertTrue(self.observe())

    def test_pending_writer_busy_preserves_previous_observation(self):
        self.observe()
        previous_hint = self.observe._previous_hint
        self.write("UPDATE task_ownership SET generation=2 WHERE task_id='a'")
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute("BEGIN EXCLUSIVE")
            writer.execute("UPDATE tasks SET status='paused' WHERE task_id='a'")
            with self.assertRaises(ObservationBusy):
                self.observe()
            self.assertEqual(self.observe._previous_hint, previous_hint)
            writer.rollback()
        self.assertTrue(self.observe())
        self.assertFalse(self.observe())

    def test_commit_between_snapshot_and_after_hint_is_not_consumed(self):
        self.observe()
        self.write("UPDATE tasks SET status='done' WHERE task_id='b'")
        read = self.observe._read_control
        def racing_read():
            value = read()
            # This write succeeding also demonstrates the read closed first.
            self.write("UPDATE task_ownership SET generation=2 WHERE task_id='a'")
            return value
        with mock.patch.object(self.observe, "_read_control", side_effect=racing_read):
            self.assertTrue(self.observe())
        self.assertTrue(self.observe())
        self.assertFalse(self.observe())

    def test_only_native_busy_is_deferred_and_private_errors_are_sanitized(self):
        for code in (sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_CORRUPT, None):
            error = sqlite3.OperationalError("PRIVATE")
            if code is not None:
                error.sqlite_errorcode = code
            wrapped = StorageError("database_busy", "PRIVATE")
            wrapped.__cause__ = error
            with self.subTest(code=code), mock.patch.object(observation, "connect_readonly", side_effect=wrapped):
                with self.assertRaises(RepositoryError) as caught:
                    self.observe()
                self.assertNotIsInstance(caught.exception, ObservationBusy)
                self.assertEqual(str(caught.exception), "state_unreadable")

    def test_corrupt_schema_fails_and_connection_closes(self):
        self.observe()
        self.write("DROP TABLE review_findings")
        opened = []
        real_open = observation.connect_readonly
        def track(path, **kwargs):
            result = real_open(path, **kwargs)
            opened.append(result)
            return result
        with mock.patch.object(observation, "connect_readonly", side_effect=track):
            with self.assertRaisesRegex(RepositoryError, "^state_unreadable$"):
                self.observe()
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")

    def test_missing_task_never_becomes_unchanged_or_available(self):
        self.observe()
        self.write("DELETE FROM tasks WHERE task_id='a'")
        with self.assertRaisesRegex(RepositoryError, "^state_unreadable$"):
            self.observe()

    def test_schema_change_requests_admission_even_with_same_control_values(self):
        self.observe()
        self.write("CREATE TABLE unexpected_object(value)")
        self.assertTrue(self.observe())

    def test_replacement_missing_and_corrupt_files_never_reuse_previous_hint(self):
        self.observe()
        replacement = self.path.with_suffix(".replacement")
        replacement.write_bytes(self.path.read_bytes())
        os.replace(replacement, self.path)
        with self.assertRaisesRegex(RepositoryError, "^state_unreadable$"):
            self.observe()
        self.path.unlink()
        with self.assertRaisesRegex(RepositoryError, "^state_unreadable$"):
            self.observe()
        self.path.write_bytes(b"PRIVATE invalid DB")
        with self.assertRaisesRegex(RepositoryError, "^state_unreadable$"):
            observation.ReviewWaitControlObservation(self.path, "p", "a")()

    def test_wal_sidecar_change_keeps_existing_journal_guard(self):
        self.observe()
        Path(str(self.path) + "-wal").write_bytes(b"")
        with self.assertRaisesRegex(RepositoryError, "^state_unreadable$"):
            self.observe()

    def test_findings_query_starts_from_own_receipt_index(self):
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute("EXPLAIN QUERY PLAN " + observation._CONTROL_SQL, ("p", "a")).fetchall()
        detail = "\n".join(row[3] for row in rows)
        self.assertNotIn("SCAN ", detail)
        self.assertIn("idx_review_receipts_task_reviewer_generation", detail)
        self.assertIn("idx_review_findings_status_severity_receipt", detail)

    def test_real_schema33_task_and_ownership_control(self):
        from tests.review_answer_fixture_support import review_fixture
        with review_fixture(schema_version=33) as fixture:
            observed = observation.ReviewWaitControlObservation(fixture.db, fixture.basis["project_id"], fixture.task_id)
            self.assertTrue(observed())
            self.assertFalse(observed())
            with closing(sqlite3.connect(fixture.db)) as connection, connection:
                connection.execute("UPDATE task_ownership SET generation=generation+1 WHERE task_id=?", (fixture.task_id,))
            self.assertTrue(observed())


if __name__ == "__main__":
    unittest.main()
