"""Full Task note retention, atomic edits and schema-28 preservation."""

from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from tests.verification_receipt_test_support import (
    initialize, add_task, run_taskgov, payload, target_for,
    seed_current_review_evidence, completion,
)
from tests.test_review_session_migration import candidate_runtime
from tests.test_m23s_schema22_migration import _logical_snapshot, _source_copy, _add_unrelated_objects
from tests.m14_test_support import make_physical_install
from tests.test_m17_cli_consumer_hardening import _setup, _run_cli
from tests.m214b_test_support import publish_generations
from task_governance_tool import storage, tasks, viewer, backup
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool import schema_task_event_notes as schema
from task_governance_tool import schema_session_slot_reacquisition as predecessor


class FailingCopy(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO completion_evidence_bundles ("):
            raise sqlite3.OperationalError("test-only note migration interruption")
        return super().execute(sql, parameters)


class TaskNoteBodyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo, self.db = initialize(Path(temporary.name))
        self.task = add_task(self.db, self.repo)
        self.task_id = self.task["task_id"]

    def run_cli(self, *arguments):
        return run_taskgov(*arguments, "--repo", str(self.repo), "--db", str(self.db), "--json")

    def success(self, *arguments):
        result = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return payload(result)["data"]

    def snapshot(self):
        with closing(storage.connect(self.db)) as connection:
            return _logical_snapshot(connection)

    def test_length_boundaries_keep_exact_body_after_reopen_and_in_context(self):
        for length in (988, 989, 1500, 2000):
            priority = "low" if length in (988, 1500) else "high"
            tier = "1" if length in (988, 1500) else "2"
            for edits in ((), ("--priority", priority),
                          ("--review-tier", tier, "--review-tier-change-reason", "Fixture has no review target")):
                with self.subTest(length=length, edits=edits):
                    note = "日本語abc\n" * (length // 7) + "末" * (length % 7)
                    self.assertEqual(len(note), length)
                    event = self.success("task", "edit", self.task_id, *edits, "--add-note", note)["event"]
                    self.assertEqual(event["note_body"], note)
                    self.assertLessEqual(len(event["summary"]), 1000)
                    for audit in (False, True):
                        shown = self.success("task", "show", self.task_id, *(("--audit",) if audit else ()), "--read-only")
                        retained = next(row for row in shown["events"] if row["task_event_id"] == event["task_event_id"])
                        self.assertEqual(retained["note_body"], note)
                    selected = self.success("task", "context", "--read-only")["selected"]
                    self.assertEqual(next(row for row in selected["events"] if row["task_event_id"] == event["task_event_id"])["note_body"], note)
        with closing(storage.connect(self.db)) as connection:
            schema.validate_storage(connection)

    def test_accepted_normalization_is_retained_without_reprocessing(self):
        raw = "  日本語\r\nsecond line\t "
        event = self.success("task", "edit", self.task_id, "--add-note", raw)["event"]
        self.assertEqual(event["note_body"], raw.strip())
        with closing(storage.connect_readonly(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT note_body FROM task_events WHERE task_event_id=?",
                (event["task_event_id"],)).fetchone()[0], raw.strip())

    def test_note_and_status_transition_are_one_event_with_full_body(self):
        note = "続き" * 999 + "末尾"
        event = self.success("task", "edit", self.task_id, "--status", "paused",
            "--pause-reason", "Await user input", "--add-note", note)["event"]
        self.assertEqual(event["note_body"], note)
        self.assertEqual(event["event_type"], "task_updated")
        self.assertLessEqual(len(event["summary"]), 1000)
        shown = self.success("task", "show", self.task_id, "--read-only")
        self.assertEqual(shown["task"]["status"], "paused")
        self.assertEqual(shown["events"][0]["note_body"], note)

    def test_normal_history_retains_old_body_without_widening_audit_window(self):
        event = self.success("task", "edit", self.task_id, "--add-note", "a" * 1999 + "末")["event"]
        with closing(storage.connect(self.db)) as connection:
            connection.execute("UPDATE task_events SET created_at='2000-01-01T00:00:00Z' WHERE task_id=?", (self.task_id,))
            for index in range(20):
                tasks.create_task_event(connection, project_id=self.task["project_id"], task_id=self.task_id,
                    event_type="checkpoint_recorded", summary="Checkpoint recorded",
                    created_at=f"2026-01-01T00:00:{index:02d}Z")
            connection.commit()
        before = self.snapshot()
        normal = self.success("task", "show", self.task_id, "--read-only")["events"]
        audit = self.success("task", "show", self.task_id, "--audit", "--read-only")["events"]
        self.assertEqual(next(row for row in normal if row["task_event_id"] == event["task_event_id"])["note_body"], event["note_body"])
        self.assertEqual(len(audit), 10)
        self.assertTrue(all(row["note_body"] is None for row in audit))
        self.assertEqual(before, self.snapshot())

    def test_over_limit_and_private_tail_are_rejected_before_any_edit(self):
        for note, code in (("a" * 2001, "invalid_argument"),
                           ("a" * 1500 + " token=private-value", "privacy_rejected")):
            with self.subTest(code=code):
                before = self.snapshot()
                result = self.run_cli("task", "edit", self.task_id, "--priority", "urgent", "--add-note", note)
                self.assertFalse(payload(result)["ok"])
                self.assertIn(code, [error["code"] for error in payload(result)["errors"]])
                self.assertNotIn("private-value", result.stdout + result.stderr)
                self.assertEqual(before, self.snapshot())

    def test_body_storage_failure_rolls_back_own_savepoint_even_if_caller_commits(self):
        ready = add_task(self.db, self.repo, status="ready")
        target = target_for(self.db, self.repo)
        with closing(storage.connect(self.db)) as connection:
            connection.execute("CREATE TRIGGER fixture_note_fail BEFORE INSERT ON task_events "
                "WHEN NEW.note_body IS NOT NULL BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
            connection.commit()
            before = _logical_snapshot(connection)
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(tasks.TaskRepositoryError):
                tasks.edit_task(connection, target.project, ready["task_id"],
                    caller=CallerIdentity("22222222-2222-4222-8222-222222222222"),
                    status="in_progress", priority="urgent", add_note="Keep entire note")
            connection.commit()
            self.assertEqual(before, _logical_snapshot(connection))

    def test_body_and_event_binding_are_immutable(self):
        event = self.success("task", "edit", self.task_id, "--add-note", "retained")["event"]
        with closing(storage.connect(self.db)) as connection:
            for statement, args in (
                ("UPDATE task_events SET note_body=? WHERE task_event_id=?", ("replacement", event["task_event_id"])),
                ("UPDATE task_events SET task_event_id=? WHERE task_event_id=?", ("other", event["task_event_id"])),
                ("UPDATE task_events SET event_type=? WHERE task_event_id=?", ("checkpoint_recorded", event["task_event_id"])),
                ("DELETE FROM task_events WHERE task_event_id=?", (event["task_event_id"],)),
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement, args)
                connection.rollback()
            schema.validate_storage(connection)

    def corrupt_body(self, event_id, value, *, column="note_body", raw_sql=None):
        with closing(storage.connect(self.db)) as connection:
            connection.execute("DROP TRIGGER trg_task_events_note_body_immutable")
            connection.execute("PRAGMA ignore_check_constraints=ON")
            if raw_sql is None:
                connection.execute(f"UPDATE task_events SET {column}=? WHERE task_event_id=?", (value, event_id))
            else:
                connection.execute(f"UPDATE task_events SET note_body={raw_sql} WHERE task_event_id=?", (event_id,))
            connection.execute(schema.note_statements()[1])
            connection.commit()

    def test_corrupt_stored_body_fails_sanitized_on_both_detail_modes_and_context(self):
        event = self.success("task", "edit", self.task_id, "--add-note", "safe retained note")["event"]
        for value in ("a" * 1500 + " token=private-value", "a" * 2001, b"binary note", "   "):
            with self.subTest(type=type(value), length=len(value)):
                self.corrupt_body(event["task_event_id"], value)
                before = self.snapshot()
                for args in (("task", "show", self.task_id), ("task", "show", self.task_id, "--audit"), ("task", "context")):
                    result = self.run_cli(*args, "--read-only")
                    self.assertEqual(payload(result)["errors"], [{"code": "project_state_unreadable", "message": "project state could not be read safely"}])
                    self.assertNotIn("private-value", result.stdout + result.stderr)
                with closing(storage.connect(self.db)) as connection, self.assertRaises(storage.StorageError):
                    schema.validate_storage(connection)
                self.assertEqual(before, self.snapshot())
        self.corrupt_body(event["task_event_id"], None, raw_sql="CAST(X'80' AS TEXT)")
        result = self.run_cli("task", "show", self.task_id, "--read-only")
        self.assertEqual(payload(result)["errors"], [{"code": "project_state_unreadable", "message": "project state could not be read safely"}])

    def test_stored_body_wrong_event_type_is_not_hidden_by_normal_filter(self):
        event = self.success("task", "edit", self.task_id, "--add-note", "retained")["event"]
        self.corrupt_body(event["task_event_id"], "checkpoint_recorded", column="event_type")
        result = self.run_cli("task", "show", self.task_id, "--read-only")
        self.assertEqual(payload(result)["errors"][0]["code"], "project_state_unreadable")

    def test_viewer_validates_full_body_but_retains_summary_only(self):
        note = "a" * 1999 + "末"
        event = self.success("task", "edit", self.task_id, "--add-note", note)["event"]
        target = target_for(self.db, self.repo)
        with closing(storage.connect_snapshot_readonly(self.db)) as connection:
            snapshot = viewer.build_viewer_snapshot(connection, target, generated_at="2026-10-12T00:00:00Z").snapshot
        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn('"note_body"', encoded)
        self.assertNotIn(note, encoded)
        self.assertIn(event["summary"], encoded)
        self.corrupt_body(event["task_event_id"], "a" * 1500 + " token=private-value")
        with closing(storage.connect_snapshot_readonly(self.db)) as connection, self.assertRaises(storage.StorageError):
            viewer.build_viewer_snapshot(connection, target, generated_at="2026-10-12T00:00:00Z")

    def test_invalid_note_is_recovery_set_fatal_even_with_verification_rejection(self):
        event = self.success("task", "edit", self.task_id, "--add-note", "safe retained note")["event"]
        target = target_for(self.db, self.repo)
        artifacts = publish_generations(target, "2098-10-12T00:00:00Z", "2099-10-12T00:00:00Z")
        with closing(sqlite3.connect(artifacts[-1].path)) as connection:
            connection.execute("DROP TRIGGER trg_task_events_note_body_immutable")
            connection.execute("UPDATE task_events SET note_body='token=private-value' WHERE task_event_id=?", (event["task_event_id"],))
            connection.execute(schema.note_statements()[1])
            connection.execute("UPDATE tasks SET verification=? WHERE task_id=?", ("x" * 1001, self.task_id))
            connection.commit()
        before = {artifact.path: artifact.path.read_bytes() for artifact in artifacts}
        with self.assertRaises(storage.StorageError) as rejected:
            backup.select_managed_backup_for_recovery(target)
        self.assertEqual(rejected.exception.code, "setup_restore_failed")
        self.assertEqual(before, {artifact.path: artifact.path.read_bytes() for artifact in artifacts})

    def test_physical_public_cli_uses_canonical_state_and_preserves_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            _setup(install)
            created = _run_cli(install, "task", "add", "--title", "Physical note", "--status", "ready", "--review-tier", "0")
            self.assertEqual(created.returncode, 0, created.stdout)
            task_id = payload(created)["data"]["task"]["task_id"]
            note = "日本語" * 666 + "末尾"
            edited = _run_cli(install, "task", "edit", task_id, "--add-note", note)
            self.assertEqual(edited.returncode, 0, edited.stdout)
            shown = _run_cli(install, "task", "show", task_id, "--read-only")
            self.assertEqual(shown.returncode, 0, shown.stdout)
            self.assertEqual(payload(shown)["data"]["events"][0]["note_body"], note)
            rejected = _run_cli(install, "task", "edit", task_id, "--add-note", note + "x")
            self.assertFalse(payload(rejected)["ok"])


class TaskNoteMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with candidate_runtime(27):
            self.repo, self.db = initialize(Path(temporary.name))
            self.task = add_task(self.db, self.repo, verification="", verification_not_required_reason="Fixture requires no execution")
            # A predecessor fixture has only its old summary; the current writer
            # cannot be used to supply a new schema-28 body to an old schema.
            with closing(storage.connect(self.db)) as connection:
                tasks.create_task_event(connection, project_id=self.task["project_id"],
                    task_id=self.task["task_id"], event_type="note_added",
                    summary=tasks.note_event_summary("a" * 1999 + "末"), created_at=storage.utc_now())
                connection.commit()
            seed_current_review_evidence(self.db, self.repo, self.task["task_id"])
            result = completion(self.db, self.repo, self.task["task_id"])
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_migration_preserves_predecessor_columns_objects_and_bundle_bytes(self):
        with closing(storage.connect(self.db)) as connection:
            _add_unrelated_objects(connection)
            before = storage._selected_table_projection_snapshot(connection, tuple(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations'")))
            self.assertTrue(schema.migrate(connection))
            after = storage._selected_table_projection_snapshot(connection, tuple(before), column_basis={name: values[0] for name, values in before.items()})
            self.assertEqual(before, after)
            self.assertIsNone(connection.execute("SELECT note_body FROM task_events LIMIT 1").fetchone()[0])
            self.assertEqual(connection.execute("SELECT source_schema_version FROM completion_evidence_bundles").fetchone()[0], 27)
            schema.validate_storage(connection)
            snapshot = _logical_snapshot(connection)
            connection.execute("PRAGMA query_only=ON")
            self.assertFalse(schema.migrate(connection))
            self.assertEqual(snapshot, _logical_snapshot(connection))
        shown = payload(run_taskgov("task", "show", self.task["task_id"], "--repo", str(self.repo), "--db", str(self.db), "--json"))
        self.assertTrue(shown["ok"], shown)
        self.assertTrue(all(row["note_body"] is None for row in shown["data"]["events"]))

    def test_copy_and_marker_failures_restore_exact_schema27(self):
        with _source_copy(self.db, factory=FailingCopy) as connection:
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(before, _logical_snapshot(connection))
            predecessor.validate_storage(connection)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)
        with _source_copy(self.db) as connection:
            connection.execute("CREATE TRIGGER fixture_marker_failure AFTER INSERT ON schema_migrations WHEN NEW.version=28 "
                "BEGIN UPDATE tasks SET title='changed by fixture'; END")
            connection.commit()
            before = _logical_snapshot(connection)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(before, _logical_snapshot(connection))
            predecessor.validate_storage(connection)

    def test_known_later_column_and_guard_are_rejected_without_write(self):
        for statement in (schema.NOTE_COLUMN_SQL, schema.note_statements()[0]):
            with self.subTest(statement=statement), _source_copy(self.db) as connection:
                connection.execute(statement)
                connection.commit()
                before = _logical_snapshot(connection)
                with self.assertRaises(storage.StorageError):
                    schema.migrate(connection)
                self.assertEqual(before, _logical_snapshot(connection))

    def test_native28_bundle_remains_format2_and_predecessor_writer_refuses_it(self):
        with closing(storage.connect(self.db)) as connection:
            schema.migrate(connection)
        active = add_task(self.db, self.repo, verification="", verification_not_required_reason="Fixture requires no execution")
        seed_current_review_evidence(self.db, self.repo, active["task_id"])
        result = completion(self.db, self.repo, active["task_id"])
        self.assertEqual(result.returncode, 0, result.stdout)
        with closing(storage.connect(self.db)) as connection:
            row = connection.execute("SELECT source_schema_version,bundle_version FROM completion_evidence_bundles ORDER BY rowid DESC LIMIT 1").fetchone()
            self.assertEqual(tuple(row), (28, 2))
            before = _logical_snapshot(connection)
        with candidate_runtime(27):
            result = run_taskgov("task", "show", active["task_id"], "--repo", str(self.repo), "--db", str(self.db), "--json")
        self.assertFalse(payload(result)["ok"])
        with closing(storage.connect(self.db)) as connection:
            self.assertEqual(before, _logical_snapshot(connection))
            schema.validate_storage(connection)
