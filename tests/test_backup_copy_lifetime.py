"""Copy/validation connection lifetimes on isolated canonical state."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current
from tests.test_routine_backup import timestamp
from task_governance_tool import backup, storage, setup, tasks, state_resolver
from task_governance_tool import backup_metadata_repository as metadata


class BackupCopyLifetimeTests(unittest.TestCase):
    def fixture(self, root):
        _, target, _ = _setup_current(root)
        self.assertTrue(target.canonical_fixed)
        return target

    def assert_closed(self, connection):
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def files(self, target):
        return {path.name: path.read_bytes() for path in target.resolved_backups_path.glob("*.sqlite")}

    def assert_no_temporary(self, target):
        self.assertEqual(list(target.resolved_backups_path.glob(".taskgov-backup-*")), [])

    def copy(self, target):
        with backup.managed_backup_lock(target):
            return backup._copy(target, backup._new_metadata(3, published_at=timestamp(0)))

    def test_writer_commits_during_copied_validation_and_three_snapshots_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.fixture(Path(directory))
            real = backup._validate_publication_source
            phases, writers = [], []
            start, finished = threading.Event(), threading.Event()
            failures = []

            def writer():
                if not start.wait(5):
                    failures.append("barrier not reached")
                    finished.set()
                    return
                try:
                    with closing(storage.connect_existing(target.db_path)) as connection:
                        connection.execute("PRAGMA busy_timeout=200")
                        tasks.add_task(connection, target.project, database_target=target,
                                       title="Concurrent isolated Task", kind="optional")
                        connection.commit()
                except Exception as exc:
                    failures.append(type(exc).__name__)
                finally:
                    finished.set()

            thread = threading.Thread(target=writer)
            thread.start()
            try:
                def validate(connection, *args, **kwargs):
                    phases.append(connection)
                    self.assertTrue(connection.in_transaction)
                    if len(phases) == 2:
                        self.assert_closed(phases[0])
                        start.set()
                        self.assertTrue(finished.wait(5), "independent writer did not finish")
                        self.assertEqual(failures, [])
                        writers.append(True)
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
                    if len(phases) == 3:
                        self.assert_closed(phases[1])
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
                    return real(connection, *args, **kwargs)

                with mock.patch.object(backup, "_validate_publication_source", side_effect=validate):
                    self.assertEqual(self.copy(target), storage.SCHEMA_VERSION)
            finally:
                start.set()
                thread.join(6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(writers, [True])
            self.assertEqual(len(phases), 3)
            self.assertEqual(len({id(connection) for connection in phases}), 3)
            for connection in phases:
                self.assert_closed(connection)
            self.assertEqual(len(self.files(target)), 1)

    def test_source_file_replacement_is_freshly_validated(self):
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = self.fixture(root)
                original = backup._validate_publication_source
                real_replace = backup.os.replace
                phases = []

                def validate(connection, *args, **kwargs):
                    phases.append(connection)
                    if len(phases) == 2:
                        self.assert_closed(phases[0])
                        replacement = root / "replacement.sqlite"
                        shutil.copyfile(target.db_path, replacement)
                        with closing(storage.connect_existing(replacement)) as changed:
                            if valid:
                                tasks.add_task(changed, target.project,
                                               title="Replacement isolated Task", kind="optional")
                            else:
                                changed.execute("DELETE FROM schema_migrations WHERE version=1")
                            changed.commit()
                        real_replace(replacement, target.db_path)
                    return original(connection, *args, **kwargs)

                with mock.patch.object(backup, "_validate_publication_source", side_effect=validate):
                    if valid:
                        self.copy(target)
                    else:
                        with self.assertRaises(storage.StorageError):
                            self.copy(target)
                self.assertEqual(len(phases), 3)
                self.assertEqual(len(self.files(target)), 1 if valid else 0)
                for connection in phases:
                    self.assert_closed(connection)
                self.assert_no_temporary(target)

    def test_real_copied_database_corruption_is_rejected_before_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.fixture(Path(directory))
            from task_governance_tool import backup_validation
            original = backup_validation.prepare_copy
            def corrupt_copy(path, *args, **kwargs):
                with closing(storage.connect_existing(path)) as changed:
                    changed.execute("DELETE FROM schema_migrations WHERE version=1")
                    changed.commit()
                return original(path, *args, **kwargs)
            with mock.patch.object(backup_validation, "prepare_copy", side_effect=corrupt_copy):
                with self.assertRaises(storage.StorageError):
                    self.copy(target)
            self.assertEqual(self.files(target), {})
            self.assert_no_temporary(target)

    def test_failures_close_readers_and_preserve_last_good_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.fixture(Path(directory))
            self.assertEqual(backup.run_routine_backup(target, observed_at=timestamp(0)).code, "succeeded")
            before_files = self.files(target)
            before = metadata.read_managed_backup_repository(target)
            real_open, real_validate = backup.connect_readonly, backup._validate_publication_source

            class CopyFailure:
                def __init__(self, connection):
                    self.connection = connection
                def __getattr__(self, name):
                    return getattr(self.connection, name)
                def backup(self, destination):
                    raise sqlite3.OperationalError("isolated copy failure")

            for phase in ("copy", "validation", "fresh_source", "fsync", "replace"):
                with self.subTest(phase=phase):
                    opened, validated = [], []
                    def open_source(*args, **kwargs):
                        connection = real_open(*args, **kwargs)
                        opened.append(connection)
                        return CopyFailure(connection) if phase == "copy" else connection
                    def validate(connection, *args, **kwargs):
                        validated.append(connection)
                        result = real_validate(connection, *args, **kwargs)
                        if len(validated) == ({"validation": 2, "fresh_source": 3}.get(phase, 0)):
                            raise backup._failure()
                        return result
                    with mock.patch.object(backup, "connect_readonly", side_effect=open_source), \
                         mock.patch.object(backup, "_validate_publication_source", side_effect=validate):
                        if phase in {"fsync", "replace"}:
                            with mock.patch.object(backup.os, phase, side_effect=OSError("isolated publication failure")):
                                result = backup.run_routine_backup(target, observed_at=timestamp(30))
                        else:
                            result = backup.run_routine_backup(target, observed_at=timestamp(30))
                    self.assertEqual((result.code, result.attempted), ("failed", True))
                    self.assertEqual(self.files(target), before_files)
                    after = metadata.read_managed_backup_repository(target)
                    self.assertEqual(after.generations, before.generations)
                    self.assertEqual(after.maintenance.latest_backup_generation_id,
                                     before.maintenance.latest_backup_generation_id)
                    self.assertEqual(after.maintenance.backup_last_success_at,
                                     before.maintenance.backup_last_success_at)
                    self.assertEqual(after.maintenance.backup_last_outcome_code, "failed")
                    self.assertTrue(opened)
                    for connection in opened:
                        self.assert_closed(connection)
                    self.assert_no_temporary(target)

    def test_private_stage_copy_closes_source_before_validating_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = self.fixture(root)
            stage = replace(target, db_path=root / "stage.sqlite", canonical_fixed=False)
            original = backup._validate_database
            phases = []
            def validate(connection, *args, **kwargs):
                phases.append(connection)
                if len(phases) == 2:
                    self.assert_closed(phases[0])
                return original(connection, *args, **kwargs)
            with mock.patch.object(backup, "_validate_database", side_effect=validate):
                self.assertEqual(backup.copy_database_snapshot(source_path=target.db_path,
                    source_target=target, destination_target=stage), storage.SCHEMA_VERSION)
            self.assertEqual(len(phases), 2)
            for connection in phases:
                self.assert_closed(connection)

    def test_restore_releases_generation_reader_before_private_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.fixture(Path(directory))
            self.assertEqual(backup.run_routine_backup(target, observed_at=timestamp(0)).code, "succeeded")
            target.db_path.unlink()  # Only the isolated fixture's canonical DB.
            real_open, real_validate = state_resolver.connect_readonly, state_resolver.recovery_database_matches_observation
            readers, validations = [], []
            def open_source(path, *args, **kwargs):
                connection = real_open(path, *args, **kwargs)
                if Path(path).name.startswith("taskgov-backup-v1_"):
                    readers.append(connection)
                return connection
            def validate(path, *args, **kwargs):
                if Path(path).name.startswith(".taskgov-restore-"):
                    self.assertTrue(readers)
                    for reader in readers:
                        self.assert_closed(reader)
                    validations.append(True)
                return real_validate(path, *args, **kwargs)
            with mock.patch.object(state_resolver, "connect_readonly", side_effect=open_source), \
                 mock.patch.object(state_resolver, "recovery_database_matches_observation", side_effect=validate):
                result = setup.run_setup(repo=str(target.project.canonical_repo), repo_explicit=True,
                    script_path=target.skill_root / "scripts/taskgov.py", read_only=False,
                    backup_interval_minutes=None, backup_generations=None)
            self.assertTrue(result.ok, result)
            self.assertTrue(validations)
            self.assertTrue(target.db_path.is_file())
            for reader in readers:
                self.assert_closed(reader)


if __name__ == "__main__":
    unittest.main()
