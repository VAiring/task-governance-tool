"""Real isolated copies, concurrent writes, retained generations and crashes."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import os
import json
import sqlite3
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from tests.test_routine_backup import make_target, metadata, timestamp
from tests.test_m17_recovery_hardening import _setup_current
from task_governance_tool import async_backup_capture as capture
from task_governance_tool import async_backup_repository as repository
from task_governance_tool import backup, storage, tasks


class AsyncBackupCaptureTests(unittest.TestCase):
    def fixture(self, *, canonical=False):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return _setup_current(root)[1] if canonical else make_target(root)

    def run_backup(self, target, minute=0, **kwargs):
        with backup.managed_backup_lock(target):
            if not target.canonical_fixed:
                kwargs["_scratch_root"] = target.resolved_backups_path / "runtime"
            return capture.run_backup(target, observed_at=timestamp(minute), **kwargs)

    def files(self, target):
        return {path.name: path.read_bytes() for path in target.resolved_backups_path.glob("*.sqlite")}

    def test_first_interval_retention_and_canonical_layout(self):
        for canonical in (False, True):
            with self.subTest(canonical=canonical):
                target = self.fixture(canonical=canonical)
                calls = []
                first = self.run_backup(target, captured_callback=lambda *value: calls.append(value))
                self.assertEqual(first.code, "succeeded")
                self.assertEqual(calls, [(first.captured_at, first.generation_id)])
                self.assertEqual(self.run_backup(target, 1).code, "current")
                for minute in (30, 60, 90):
                    self.assertEqual(self.run_backup(target, minute).code, "succeeded")
                control = repository.read_control(target)
                self.assertEqual(len(control.generations), 3)
                self.assertEqual(len(self.files(target)), 3)

    def test_real_writer_completes_during_private_validation_and_is_not_in_copy(self):
        target = self.fixture()
        original = repository.validate_snapshot
        failures, results = [], []

        def validate(path, *args):
            def mutate():
                try:
                    with closing(storage.connect_existing(target.db_path, timeout=0.1)) as connection:
                        tasks.add_task(connection, target.project, database_target=target,
                                       title="After captured point", kind="optional")
                        connection.commit()
                except Exception as exc:
                    failures.append(type(exc).__name__)
            worker = threading.Thread(target=mutate)
            worker.start()
            worker.join(3)
            self.assertFalse(worker.is_alive(), "source reader retained during private validation")
            self.assertEqual(failures, [])
            with closing(storage.connect_readonly(path)) as connection:
                results.append(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            return original(path, *args)

        with mock.patch.object(repository, "validate_snapshot", side_effect=validate):
            result = self.run_backup(target)
        self.assertEqual(result.code, "succeeded")
        self.assertEqual(results, [0])
        with closing(storage.connect_readonly(target.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_source_replacement_during_validation_does_not_publish(self):
        target = self.fixture()
        real = repository.validate_snapshot

        def validate(*args):
            real(*args)
            replacement = target.db_path.with_name("replacement.sqlite")
            shutil.copyfile(target.db_path, replacement)
            os.replace(replacement, target.db_path)

        with mock.patch.object(repository, "validate_snapshot", side_effect=validate):
            self.assertEqual(self.run_backup(target).code, "failed")
        self.assertEqual(self.files(target), {})

    def test_replacement_after_caller_admission_has_no_capture_or_metadata_effect(self):
        target = self.fixture()
        expected = repository.read_control(target)
        replacement = target.db_path.with_name("replacement.sqlite")
        shutil.copyfile(target.db_path, replacement)
        os.replace(replacement, target.db_path)
        before = repository.read_control(target)
        with mock.patch.object(repository, "copy_snapshot") as copied:
            result = self.run_backup(target, expected_control=expected)
        self.assertEqual(result.code, "failed")
        copied.assert_not_called()
        self.assertEqual(repository.read_control(target), before)
        self.assertEqual(self.files(target), {})
        self.assertFalse((target.resolved_backups_path / "runtime").exists())

    def test_matching_caller_admission_survives_ordinary_task_update(self):
        target = self.fixture()
        expected = repository.read_control(target)
        with closing(storage.connect_existing(target.db_path)) as connection:
            tasks.add_task(connection, target.project, database_target=target,
                           title="Updated after launcher observation", kind="optional")
            connection.commit()
        self.assertEqual(self.run_backup(target, expected_control=expected).code, "succeeded")

    def test_changed_private_copy_after_validation_is_not_published(self):
        target = self.fixture()
        self.assertEqual(self.run_backup(target).code, "succeeded")
        original_files = self.files(target)
        real = repository.validate_snapshot

        def validate(path, *args):
            real(path, *args)
            path.write_bytes(b"replaced after validation")

        with mock.patch.object(repository, "validate_snapshot", side_effect=validate):
            self.assertEqual(self.run_backup(target, 30).code, "failed")
        self.assertEqual(self.files(target), original_files)

    def test_copy_validation_callback_and_publish_failures_preserve_last_good(self):
        target = self.fixture()
        self.assertEqual(self.run_backup(target).code, "succeeded")
        files = self.files(target)
        before = repository.read_control(target)
        for phase in ("copy_snapshot", "validate_snapshot", "replace", "callback"):
            with self.subTest(phase=phase):
                if phase in {"copy_snapshot", "validate_snapshot"}:
                    patch = mock.patch.object(repository, phase, side_effect=OSError("isolated failure"))
                else:
                    patch = mock.patch.object(capture.os, "replace", side_effect=OSError("isolated failure")) if phase == "replace" else mock.patch.object(capture.os, "fsync", wraps=capture.os.fsync)
                def fail_callback(*args):
                    raise ValueError("isolated callback failure")
                with patch:
                    result = self.run_backup(target, 30, captured_callback=fail_callback if phase == "callback" else None)
                self.assertEqual(result.code, "failed")
                self.assertEqual(self.files(target), files)
                self.assertEqual(repository.read_control(target).generations, before.generations)
                self.assertFalse(list(target.resolved_backups_path.glob(".taskgov-backup-*")))

    def test_file_publication_then_metadata_failure_recovers_next_attempt(self):
        target = self.fixture()
        original = repository.commit_inventory
        def commit(*args, **kwargs):
            if kwargs.get("success"):
                raise OSError("isolated metadata failure")
            return original(*args, **kwargs)
        with mock.patch.object(repository, "commit_inventory", side_effect=commit):
            result = self.run_backup(target)
        self.assertEqual(result.code, "failed")
        self.assertEqual(len(self.files(target)), 1)
        self.assertEqual(repository.read_control(target).generations, ())
        self.assertEqual(self.run_backup(target, 1).code, "current")
        self.assertEqual(len(repository.read_control(target).generations), 1)
        self.assertEqual(len(self.files(target)), 1)

    def test_duplicate_generation_ids_preserve_last_good_before_any_reconciliation(self):
        for generation_count in (1, 2):
            for conflict in ("earlier_time", "later_time", "lower_retention", "higher_retention"):
                with self.subTest(generation_count=generation_count, conflict=conflict):
                    target = self.fixture()
                    for minute in range(0, 30 * generation_count, 30):
                        self.assertEqual(self.run_backup(target, minute).code, "succeeded")
                    before = repository.read_control(target)
                    latest = before.generations[-1]
                    if conflict.endswith("time"):
                        offset = -1 if conflict == "earlier_time" else 1
                        duplicate = replace(latest, published_at=timestamp(minute + offset))
                    else:
                        offset = -1 if conflict == "lower_retention" else 1
                        duplicate = replace(latest, publication_retention=latest.publication_retention + offset)
                    shutil.copyfile(
                        target.resolved_backups_path / backup._filename(latest),
                        target.resolved_backups_path / backup._filename(duplicate),
                    )
                    original_files = self.files(target)
                    with (
                        mock.patch.object(capture, "_prune", wraps=capture._prune) as pruned,
                        mock.patch.object(repository, "copy_snapshot") as copied,
                        mock.patch.object(repository, "commit_inventory", wraps=repository.commit_inventory) as committed,
                    ):
                        result = self.run_backup(target, minute + 30)
                    self.assertEqual(result.code, "failed")
                    after = repository.read_control(target)
                    self.assertEqual(after.generations, before.generations)
                    for field in ("latest_backup_generation_id", "backup_last_success_at", "applied_backup_generations"):
                        self.assertEqual(getattr(after.maintenance, field), getattr(before.maintenance, field))
                    self.assertEqual(self.files(target), original_files)
                    pruned.assert_not_called()
                    copied.assert_not_called()
                    # Recording this failed attempt must not change the inventory.
                    self.assertEqual(len(committed.call_args_list), 1)
                    self.assertEqual(committed.call_args.args[2], before.generations)
                    self.assertEqual(committed.call_args.kwargs, {"failure_at": timestamp(minute + 30)})

    def test_prune_file_before_row_crash_is_reconciled_without_unbounded_copies(self):
        target = self.fixture()
        for minute in (0, 30, 60):
            self.assertEqual(self.run_backup(target, minute).code, "succeeded")
        original = repository.commit_inventory
        def commit(target, expected, generations, **kwargs):
            if len(generations) < len(expected.generations):
                raise OSError("isolated prune metadata failure")
            return original(target, expected, generations, **kwargs)
        with mock.patch.object(repository, "commit_inventory", side_effect=commit):
            self.assertEqual(self.run_backup(target, 90).code, "failed")
        self.assertEqual(len(self.files(target)), 3)
        self.assertEqual(len(repository.read_control(target).generations), 4)
        self.assertEqual(self.run_backup(target, 91).code, "failed")
        self.assertEqual(len(repository.read_control(target).generations), 3)
        self.assertEqual(self.run_backup(target, 92).code, "succeeded")
        self.assertEqual(len(self.files(target)), 3)

    def test_incomplete_fixed_scratch_is_reclaimed_after_interrupted_worker(self):
        target = self.fixture()
        root = target.resolved_backups_path / "runtime"
        with backup.managed_backup_lock(target):
            _, path = capture._scratch(target, "1" * 32, root)
            path.write_bytes(b"interrupted private copy")
        self.assertEqual(self.run_backup(target, attempt_id="2" * 32).code, "succeeded")
        self.assertFalse((root / "snapshot.sqlite").exists())
        self.assertFalse((root / "snapshot-owner.json").exists())
        self.assertEqual(len(self.files(target)), 1)

    def test_not_due_recovery_reclaims_owned_marker_without_new_capture(self):
        target = self.fixture()
        self.assertEqual(self.run_backup(target).code, "succeeded")
        root = target.resolved_backups_path / "runtime"
        with backup.managed_backup_lock(target):
            capture._scratch(target, "1" * 32, root)
        self.assertEqual(self.run_backup(target, 1).code, "current")
        self.assertFalse((root / "snapshot-owner.json").exists())
        self.assertFalse((root / "snapshot.sqlite").exists())
        self.assertEqual(len(self.files(target)), 1)

    def test_foreign_project_marker_and_hardlinked_private_file_are_preserved(self):
        target = self.fixture()
        root = target.resolved_backups_path / "runtime"
        root.mkdir(parents=True)
        owner = root / "snapshot-owner.json"
        owner.write_bytes(capture._marker_bytes("1" * 32, "foreign-project"))
        self.assertEqual(self.run_backup(target).code, "failed")
        self.assertTrue(owner.exists())
        owner.write_bytes(capture._marker_bytes("1" * 32, target.project.project_id))
        original = root / "unmanaged-original"
        original.write_bytes(b"unmanaged payload")
        try:
            os.link(original, root / "snapshot.sqlite")
        except OSError:
            self.skipTest("filesystem hard links unavailable")
        self.assertEqual(self.run_backup(target).code, "failed")
        self.assertTrue((root / "snapshot.sqlite").exists())
        self.assertEqual(original.read_bytes(), b"unmanaged payload")

    def test_unowned_or_malformed_scratch_is_preserved(self):
        for marker in (None, b"{}", json.dumps({"v": True, "purpose": "async-backup-private-copy", "attempt_id": "1" * 32}).encode()):
            with self.subTest(marker=marker):
                target = self.fixture()
                root = target.resolved_backups_path / "runtime"
                root.mkdir(parents=True)
                scratch = root / "snapshot.sqlite"
                scratch.write_bytes(b"do not remove unowned data")
                if marker is not None:
                    (root / "snapshot-owner.json").write_bytes(marker)
                self.assertEqual(self.run_backup(target).code, "failed")
                self.assertEqual(scratch.read_bytes(), b"do not remove unowned data")
                self.assertEqual(self.files(target), {})

    def test_private_snapshot_foreign_key_defect_is_rejected(self):
        target = self.fixture()
        with closing(storage.connect_existing(target.db_path)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "INSERT INTO task_events(task_event_id, task_id, project_id, event_type, summary, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("tg_event_" + "1" * 16, "tg_task_" + "2" * 16, target.project.project_id,
                 "note_added", "Isolated invalid relation", timestamp(0)),
            )
            connection.commit()
        # Lightweight control admission deliberately does not certify Task rows.
        repository.read_control(target)
        self.assertEqual(self.run_backup(target).code, "failed")
        self.assertEqual(self.files(target), {})

    def test_sqlite_copy_busy_yields_once_and_closes_live_source(self):
        target = self.fixture()
        original = repository.connect_readonly
        copied = []
        readers = []
        class BusyCopy:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def backup(self, destination, **kwargs):
                copied.append(True)
                kwargs["progress"](sqlite3.SQLITE_BUSY, 1, 1)
                raise AssertionError("BUSY should leave before the retry loop")
        def opened(*args, **kwargs):
            connection = original(*args, **kwargs)
            readers.append(connection)
            return BusyCopy(connection)
        with mock.patch.object(repository, "connect_readonly", side_effect=opened):
            self.assertEqual(self.run_backup(target).code, "failed")
        self.assertEqual(copied, [True])
        for connection in readers:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        self.assertEqual(self.files(target), {})

    def test_excess_named_residue_is_rejected_before_artifact_audits(self):
        target = self.fixture()
        target.resolved_backups_path.mkdir(parents=True)
        for index in range(repository.MAX_GENERATIONS_WITH_RESIDUE + 1):
            (target.resolved_backups_path / backup._filename(metadata(index, index, 3))).write_bytes(b"not inspected")
        before = self.files(target)
        with mock.patch.object(backup, "_valid_artifact", side_effect=AssertionError("unbounded artifact audit")) as audit:
            self.assertEqual(self.run_backup(target).code, "failed")
        audit.assert_not_called()
        self.assertEqual(self.files(target), before)
