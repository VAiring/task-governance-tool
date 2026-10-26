"""Metadata-only live DB admission and stale-write rejection."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.test_routine_backup import make_target, metadata, timestamp
from task_governance_tool import async_backup_repository as repository
from task_governance_tool import storage, tasks


class AsyncBackupRepositoryTests(unittest.TestCase):
    def fixture(self):
        return make_target(Path(self.enterContext(tempfile.TemporaryDirectory())))

    def test_control_and_writer_never_audit_task_history_or_physical_integrity(self):
        target = self.fixture()
        statements = []
        real_read, real_write = repository.connect_readonly, repository.connect_existing

        def opened(function, *args, **kwargs):
            connection = function(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with mock.patch.object(repository, "connect_readonly", side_effect=lambda *a, **k: opened(real_read, *a, **k)), \
             mock.patch.object(repository, "connect_existing", side_effect=lambda *a, **k: opened(real_write, *a, **k)), \
             mock.patch.object(storage, "validate_current_schema21_admitted_rows", side_effect=AssertionError("history audit")):
            current = repository.read_control(target)
            result = repository.commit_inventory(target, current, (metadata(1, 0, 3),), success=True)
        self.assertEqual(result.maintenance.latest_backup_generation_id, metadata(1, 0, 3).generation_id)
        self.assertFalse(any("quick_check" in query.lower() or "foreign_key_check" in query.lower()
                             or "from tasks" in query.lower() or "from task_events" in query.lower()
                             for query in statements))

    def test_task_and_viewer_updates_do_not_make_control_stale(self):
        target = self.fixture()
        before = repository.read_control(target)
        with closing(storage.connect_existing(target.db_path)) as connection:
            tasks.add_task(connection, target.project, database_target=target,
                           title="Concurrent mutation", kind="optional")
            connection.execute("UPDATE project_maintenance SET viewer_last_success_at=?", (timestamp(1),))
            connection.commit()
        repository.require_same_control(repository.read_control(target), before)
        after = repository.commit_inventory(target, before, (metadata(1, 0, 3),), success=True)
        self.assertEqual(after.maintenance.viewer_last_success_at, timestamp(1))

    def test_replaced_database_cannot_reuse_identical_control(self):
        target = self.fixture()
        before = repository.read_control(target)
        replacement = target.db_path.with_name("replacement.sqlite")
        shutil.copyfile(target.db_path, replacement)
        os.replace(replacement, target.db_path)
        with self.assertRaises(storage.StorageError):
            repository.commit_inventory(target, before, (metadata(1, 0, 3),), success=True)

    def test_equal_count_replacement_prevents_old_outcome(self):
        target = self.fixture()
        current = repository.read_control(target)
        current = repository.commit_inventory(target, current, (metadata(1, 0, 3),), success=True)
        replacement = repository.commit_inventory(target, current, (metadata(2, 0, 3),), success=True)
        with self.assertRaises(storage.StorageError):
            repository.record_failure(target, current, timestamp(1))
        self.assertEqual(repository.read_control(target), replacement)

    def test_changed_schema_and_binding_are_rejected(self):
        target = self.fixture()
        with self.assertRaises(storage.StorageError):
            repository.read_control(replace(target, binding_path_hash="0" * 64, binding_generation=1))
        with closing(storage.connect_existing(target.db_path)) as connection:
            connection.execute("DROP TRIGGER trg_project_maintenance_enabled_at_immutable")
            connection.commit()
        with self.assertRaises(storage.StorageError):
            repository.read_control(target)

    def test_consumed_policy_type_is_not_coerced(self):
        target = self.fixture()
        with closing(storage.connect_existing(target.db_path)) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE project_maintenance SET backup_generations=1.5")
            connection.commit()
        with self.assertRaises(storage.StorageError):
            repository.read_control(target)

    def test_control_query_count_is_independent_of_task_history_size(self):
        from tests.test_backup_performance import large_task_specs, small_task_specs, seed_fixture
        counts = []
        real = repository.connect_readonly
        for specs in (small_task_specs(), large_task_specs()):
            target = self.fixture()
            seed_fixture(target.db_path.parent.parent, task_specs=specs[0], tool_event_specs=specs[1], target=target)
            statements = []
            def opened(*args, **kwargs):
                connection = real(*args, **kwargs)
                connection.set_trace_callback(statements.append)
                return connection
            with mock.patch.object(repository, "connect_readonly", side_effect=opened) as connect:
                repository.read_control(target)
            self.assertEqual(connect.call_count, 1)
            counts.append(len(statements))
        self.assertEqual(counts[0], counts[1])
