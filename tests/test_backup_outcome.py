"""Short outcome writes retain primary results and reject stale publications."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tests.test_routine_backup import make_target, metadata, timestamp
from task_governance_tool import backup, backup_metadata_repository as repository, storage


class BackupOutcomeTests(unittest.TestCase):
    def fixture(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        return make_target(Path(directory))

    def state(self, target):
        return self.observe(target).maintenance

    def observe(self, target):
        return repository.read_managed_backup_repository(target, operation_local=True)

    def record(self, target, observed, code="failed"):
        repository.record_backup_attempt_outcome(
            target, code=code, occurred_at=timestamp(0), observed=observed,
        )

    def test_delayed_outcome_cannot_overwrite_new_generation_at_same_timestamp(self):
        for code in ("failed", "deferred"):
            with self.subTest(code=code):
                target = self.fixture()
                repository.record_managed_backup(target, metadata(1, 0, 3))
                old = self.observe(target)
                repository.record_managed_backup(target, metadata(2, 0, 3))
                current = self.observe(target)
                self.record(target, old, code)
                self.assertEqual(self.state(target), current.maintenance)
                self.assertFalse(backup.managed_backup_due(current.maintenance, observed_at=timestamp(1)))
                self.record(target, current, code)
                self.assertEqual(self.state(target).backup_last_outcome_code, code)
                self.assertTrue(backup.managed_backup_due(self.state(target), observed_at=timestamp(1)))

    def test_writer_rechecks_basis_after_open_and_same_generation_recovery(self):
        target = self.fixture()
        observed = self.observe(target)
        real_begin = storage.begin_initialized_write
        def race(connection, *args, **kwargs):
            with closing(storage.connect_existing(target.db_path)) as writer:
                repository._insert_managed_backup_generation(writer, target.project.project_id,
                    metadata(1, 0, 3), allow_existing=False)
                repository._write_backup_success(writer, target.project.project_id, metadata(1, 0, 3))
                writer.commit()
            return real_begin(connection, *args, **kwargs)
        with mock.patch.object(storage, "begin_initialized_write", side_effect=race):
            self.record(target, observed)
        self.assertEqual(self.state(target).backup_last_outcome_code, "succeeded")
        self.record(target, self.observe(target))
        failed = self.observe(target)
        repository.import_managed_backup_generations(target, (metadata(1, 0, 3),))
        self.record(target, failed, "deferred")
        self.assertEqual(self.state(target).backup_last_outcome_code, "succeeded")

    def test_delayed_outcomes_cannot_dirty_prune_only_crash_recovery(self):
        from tests.test_m17_recovery_hardening import _setup_current

        for canonical in (False, True):
            for code in ("failed", "deferred"):
                with self.subTest(canonical=canonical, code=code):
                    root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                    target = _setup_current(root)[1] if canonical else make_target(root)
                    # Real published files and rows, as after a row-commit crash
                    # before retention pruning. Recovery needs no new copy.
                    for index in range(1, 5):
                        item = metadata(index, index, 3)
                        backup._copy(target, item)
                        repository.record_managed_backup(target, item)
                    observed = self.observe(target)
                    result = backup.run_routine_backup(target, observed_at=timestamp(5))
                    current = self.observe(target)
                    self.assertEqual(result.code, "current")
                    self.assertEqual(observed.maintenance, current.maintenance)
                    self.assertEqual((len(observed.generations), len(current.generations)), (4, 3))
                    self.assertEqual(len(backup.discover_managed_backup_metadata(target)), 3)
                    self.record(target, observed, code)
                    self.assertEqual(self.state(target), current.maintenance)
                    self.assertFalse(backup.managed_backup_due(self.state(target), observed_at=timestamp(5)))
                    # A genuine failure observed after recovery must still stick.
                    self.record(target, current, code)
                    self.assertEqual(self.state(target).backup_last_outcome_code, code)

    def test_writer_rechecks_same_count_inventory_and_metadata_changes(self):
        for code in ("failed", "deferred"):
            for replacement in (metadata(2, 0, 3), metadata(1, 0, 2)):
                with self.subTest(code=code, replacement=replacement):
                    target = self.fixture()
                    repository.record_managed_backup(target, metadata(1, 0, 3))
                    repository.record_managed_backup(target, metadata(3, 1, 3))
                    observed = self.observe(target)
                    real_begin = storage.begin_initialized_write

                    def replace_before_writer(connection, *args, **kwargs):
                        # Only the outcome writer is patched, not these real
                        # repository transactions. The latest tuple stays equal.
                        with mock.patch.object(storage, "begin_initialized_write", real_begin):
                            repository.delete_managed_backup_generation(target, metadata(1, 0, 3).generation_id)
                            repository.import_managed_backup_generations(target, (replacement,))
                        return real_begin(connection, *args, **kwargs)

                    with mock.patch.object(storage, "begin_initialized_write", side_effect=replace_before_writer):
                        self.record(target, observed, code)
                    current = self.observe(target)
                    self.assertEqual(current.maintenance, observed.maintenance)
                    self.assertEqual(len(current.generations), len(observed.generations))
                    self.assertNotEqual(current.generations, observed.generations)

    def test_outcome_does_not_walk_history_but_keeps_locked_structural_admission(self):
        target = self.fixture()
        observed = self.observe(target)
        with mock.patch.object(storage, "_validate_review_storage_rows",
                               side_effect=AssertionError("outcome walked reviews")), \
             mock.patch.object(storage, "validate_current_schema21_admitted_rows",
                               side_effect=AssertionError("outcome walked history")), \
             mock.patch.object(storage, "_validate_current_schema_contract",
                               wraps=storage._validate_current_schema_contract) as structure:
            self.record(target, observed)
        self.assertEqual(structure.call_count, 2)
        self.assertEqual(self.state(target).backup_last_outcome_code, "failed")
        wrong = replace(target, binding_generation=2)
        with self.assertRaises(storage.StorageError):
            self.record(wrong, self.observe(target))

    def test_post_publish_reconcile_failure_is_not_lost_and_record_failure_is_bounded(self):
        target = self.fixture()
        with mock.patch.object(backup, "_reconcile_v11", side_effect=(True, False)):
            result = backup.run_routine_backup(target, observed_at=timestamp(0))
        self.assertEqual(result.code, "failed")
        failed = self.state(target)
        self.assertIsNotNone(failed.latest_backup_generation_id)
        self.assertEqual(failed.backup_last_outcome_code, "failed")
        artifacts = backup.discover_managed_backup_metadata(target)
        with mock.patch.object(backup, "_copy", side_effect=OSError("isolated copy failure")), \
             mock.patch.object(backup, "record_backup_attempt_outcome", side_effect=OSError("isolated record failure")):
            result = backup.run_routine_backup(target, observed_at=timestamp(30))
        self.assertEqual(result.code, "failed")
        self.assertEqual(self.state(target), failed)
        self.assertEqual(backup.discover_managed_backup_metadata(target), artifacts)

    def test_deferred_result_cannot_dirty_publication_after_lock_release(self):
        target = self.fixture()
        original = backup._record_attempt_outcome
        def publish_first(*args, **kwargs):
            repository.record_managed_backup(target, metadata(1, 0, 3))
            original(*args, **kwargs)
        with mock.patch.object(backup, "managed_backup_lock", side_effect=storage.StorageError(
                "backup_lock_contended", "managed backup was deferred")), \
             mock.patch.object(backup, "_record_attempt_outcome", side_effect=publish_first):
            result = backup.run_routine_backup(target, observed_at=timestamp(0))
        self.assertEqual(result.code, "deferred")
        self.assertEqual(self.state(target).backup_last_outcome_code, "succeeded")

    def test_sqlite_writer_contention_preserves_last_good_and_can_retry(self):
        target = self.fixture()
        repository.record_managed_backup(target, metadata(1, 0, 3))
        observed = self.observe(target)
        real_connect = storage.connect_existing
        with closing(real_connect(target.db_path)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            # A real competing SQLite writer, with only the test wait shortened.
            with mock.patch.object(storage, "connect_existing",
                    side_effect=lambda path: real_connect(path, timeout=0.05)):
                with self.assertRaises(storage.StorageError) as caught:
                    self.record(target, observed)
            self.assertEqual(caught.exception.code, "database_busy")
            writer.rollback()
        self.assertEqual(self.state(target), observed.maintenance)
        self.record(target, observed)
        self.assertEqual(self.state(target).backup_last_outcome_code, "failed")

    def test_restored_database_with_identical_success_tuple_rejects_old_result(self):
        target = self.fixture()
        repository.record_managed_backup(target, metadata(1, 0, 3))
        observed = self.observe(target)
        restored = target.db_path.with_suffix(".restored")
        shutil.copy2(target.db_path, restored)
        restored.replace(target.db_path)
        current = self.observe(target)
        self.assertEqual(observed.maintenance, current.maintenance)
        self.assertNotEqual(observed.database_identity, current.database_identity)
        self.record(target, observed, "deferred")
        self.assertEqual(self.state(target), observed.maintenance)
        self.record(target, current)
        self.assertEqual(self.state(target).backup_last_outcome_code, "failed")


if __name__ == "__main__":
    unittest.main()
