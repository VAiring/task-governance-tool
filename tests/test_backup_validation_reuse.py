"""Content proofs never replace current physical/inventory/recovery authority."""
from contextlib import closing
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.test_routine_backup import make_target, metadata, timestamp
from tests.test_m17_recovery_hardening import _setup_current
from task_governance_tool import backup, backup_validation as validation, state_resolver
from task_governance_tool import backup_metadata_repository as repository
from task_governance_tool import schema_viewer_projection, setup, storage


class BackupValidationReuseTests(unittest.TestCase):
    def fixture(self, *, canonical=False):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        if canonical:
            install, target, _ = _setup_current(root)
        else:
            install, target = None, make_target(root)
        item = metadata(1, 0, 3)
        with backup.managed_backup_lock(target):
            backup._copy(target, item)
            repository.record_managed_backup(target, item)
        path = target.resolved_backups_path / backup._filename(item)
        record_path = validation.record_directory(path.parent) / (item.generation_id + ".json")
        return target, path, item, record_path, install

    def inspect(self, target, path, item):
        return validation.inspect_artifact(path, item, project_id=target.project.project_id)

    def test_record_binds_closed_copy_and_reuse_skips_internal_audit(self):
        target, path, item, record_path, _ = self.fixture()
        before = record_path.read_bytes()
        record = validation.decode(before)
        self.assertEqual(record.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual((record.project_id, record.metadata, record.schema),
                         (target.project.project_id, item, storage.SCHEMA_VERSION))
        with mock.patch.object(schema_viewer_projection, "validate_storage",
                               side_effect=AssertionError("repeated internal audit")), \
             mock.patch.object(validation, "hash_physical_file", wraps=validation.hash_physical_file) as hashes:
            observed = self.inspect(target, path, item)
        self.assertTrue(observed.recovery_content_valid)
        self.assertEqual(hashes.call_count, 1)
        self.assertEqual(record_path.read_bytes(), before)

    def test_missing_obsolete_and_partial_records_require_real_inspection_without_writes(self):
        for mode in ("missing", "obsolete", "partial"):
            with self.subTest(mode=mode):
                target, path, item, record_path, _ = self.fixture()
                record = validation.decode(record_path.read_bytes())
                if mode == "missing":
                    record_path.unlink()
                    before = None
                else:
                    record = replace(record, rules=validation.RULES_VERSION + 1) if mode == "obsolete" else replace(
                        record, mode="structure", scope="structure", classification="structural_only")
                    before = validation.encode(record)
                    record_path.write_bytes(before)
                with mock.patch.object(schema_viewer_projection, "validate_storage",
                                       wraps=schema_viewer_projection.validate_storage) as checked:
                    self.assertTrue(self.inspect(target, path, item).recovery_content_valid)
                self.assertEqual(checked.call_count, 1)
                self.assertEqual(record_path.read_bytes() if record_path.exists() else None, before)

    def test_intervening_content_change_before_sqlite_observation_is_rejected(self):
        target, path, item, record_path, _ = self.fixture()
        record_bytes = record_path.read_bytes()
        inspect = state_resolver._inspect_database
        def changed(candidate, **kwargs):
            stamp = candidate.stat()
            with closing(sqlite3.connect(candidate)) as connection:
                connection.execute('DELETE FROM schema_migrations WHERE version=1')
                connection.commit()
            self.assertEqual(candidate.stat().st_size, stamp.st_size)
            os.utime(candidate, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            return inspect(candidate, **kwargs)
        with mock.patch.object(state_resolver, '_inspect_database', side_effect=changed), \
             self.assertRaises(storage.StorageError):
            self.inspect(target, path, item)
        self.assertEqual(record_path.read_bytes(), record_bytes)

    def test_hash_descriptor_keeps_sqlite_lock_until_reader_closes(self):
        writer = """import sqlite3,sys
c=sqlite3.connect(sys.argv[1], timeout=0.05)
try:
 c.execute('UPDATE project_meta SET display_name=display_name')
 c.commit()
except sqlite3.OperationalError:
 sys.exit(4)
finally:
 c.close()
"""
        original = validation.hash_physical_file
        for fail in (False, True):
            with self.subTest(fail=fail):
                target, path, item, _, _ = self.fixture()
                observed = []
                def hash_locked(*args, **kwargs):
                    value = original(*args, **kwargs)
                    result = subprocess.run([sys.executable, '-I', '-S', '-c', writer, str(path)],
                        capture_output=True, timeout=5)
                    observed.append(result.returncode)
                    if fail:
                        raise validation.StatePathError('isolated failure')
                    return value
                with mock.patch.object(validation, 'hash_physical_file', side_effect=hash_locked):
                    if fail:
                        with self.assertRaises(storage.StorageError):
                            self.inspect(target, path, item)
                    else:
                        self.inspect(target, path, item)
                self.assertEqual(observed, [4])
                # A real independent writer is no longer blocked after either exit.
                self.assertEqual(subprocess.run([sys.executable, '-I', '-S', '-c', writer, str(path)],
                    capture_output=True, timeout=5).returncode, 0)

    def test_certified_snapshot_source_reuses_audit_and_copy_is_independently_checked(self):
        target, path, item, _, _ = self.fixture()
        observed = self.inspect(target, path, item)
        expected = state_resolver.ManagedBackupObservation(item,
            observed.stored_project.source_schema_version, observed.recovery_content_valid,
            backup._file_identity(path), path, observed.stored_project, observed.generation_rows,
            observed.maintenance_pointer, observed.viewer_projection, observed.content_sha256)
        destination = replace(target, db_path=path.parent / 'private-copy.sqlite')
        audited = []
        original = schema_viewer_projection.validate_storage
        def validate(connection, *args, **kwargs):
            audited.append(Path(connection.execute('PRAGMA database_list').fetchone()[2]))
            return original(connection, *args, **kwargs)
        with mock.patch.object(schema_viewer_projection, 'validate_storage', side_effect=validate):
            version = backup.copy_database_snapshot(source_path=path, source_target=target,
                destination_target=destination, expected_source_identity=expected.identity,
                require_recovery_content_valid=True, expected_recovery_observation=expected)
        self.assertEqual(version, storage.SCHEMA_VERSION)
        self.assertNotIn(path, audited)
        self.assertIn(destination.db_path, audited)
        self.assertTrue(state_resolver.recovery_database_matches_observation(destination.db_path, expected))

    def test_public_recovery_planning_reuses_source_proof_and_checks_copied_output(self):
        from tests.m14_test_support import file_snapshot

        for obsolete in (False, True):
            for read_only in (True, False):
                with self.subTest(obsolete=obsolete, read_only=read_only):
                    target, path, item, record_path, install = self.fixture(canonical=True)
                    if obsolete:
                        record_path.write_bytes(validation.encode(replace(
                            validation.decode(record_path.read_bytes()),
                            rules=validation.RULES_VERSION + 1)))
                    original_bytes, record_bytes = path.read_bytes(), record_path.read_bytes()
                    target.db_path.unlink()
                    before = file_snapshot(install.project_root)
                    audited = []
                    original = schema_viewer_projection._validate_storage_with_task_rows

                    def audit(connection, *args, **kwargs):
                        audited.append(Path(connection.execute("PRAGMA database_list").fetchone()[2]))
                        return original(connection, *args, **kwargs)

                    with mock.patch.object(schema_viewer_projection,
                                           "_validate_storage_with_task_rows", side_effect=audit):
                        result = setup.run_setup(repo=str(install.project_root), repo_explicit=True,
                            script_path=install.entrypoint, read_only=read_only,
                            backup_interval_minutes=None, backup_generations=None)
                    self.assertTrue(result.ok, (result.error_code, result.data))
                    self.assertEqual(path in audited, obsolete)
                    self.assertEqual(path.read_bytes(), original_bytes)
                    self.assertEqual(record_path.read_bytes(), record_bytes)
                    if read_only:
                        self.assertEqual(file_snapshot(install.project_root), before)
                    else:
                        self.assertTrue(target.db_path.exists())
                        self.assertTrue(any(candidate != path for candidate in audited))
                        self.assertIn("database_restore", result.data["completed_writes"])

    def test_public_recovery_planning_rejects_source_or_binding_drift(self):
        for mode in ("replacement", "content", "binding"):
            with self.subTest(mode=mode):
                target, path, item, record_path, install = self.fixture(canonical=True)
                target.db_path.unlink()
                original = setup.inspect_backup_setup_state
                calls = []

                def inspect(planning_target, expected):
                    calls.append(mode)
                    if mode == "replacement":
                        changed = path.with_name("replacement.sqlite")
                        shutil.copyfile(path, changed)
                        os.replace(changed, path)
                    elif mode == "content":
                        with closing(sqlite3.connect(path)) as connection:
                            connection.execute("UPDATE project_maintenance SET backup_interval_minutes=31")
                            connection.commit()
                    else:
                        planning_target = replace(planning_target,
                            binding_generation=planning_target.binding_generation + 1)
                    return original(planning_target, expected)

                with mock.patch.object(setup, "inspect_backup_setup_state", side_effect=inspect):
                    result = setup.run_setup(repo=str(install.project_root), repo_explicit=True,
                        script_path=install.entrypoint, read_only=True,
                        backup_interval_minutes=None, backup_generations=None)
                self.assertEqual(calls, [mode])
                self.assertFalse(result.ok)
                self.assertEqual(result.data["completed_writes"], [])
                self.assertFalse(target.db_path.exists())

    def test_same_size_mtime_preserving_byte_change_is_never_reenrolled(self):
        target, path, item, record_path, _ = self.fixture()
        record_bytes, original = record_path.read_bytes(), path.read_bytes()
        stamp = path.stat()
        changed = bytearray(original)
        changed[-1] ^= 1
        path.write_bytes(changed)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with mock.patch.object(schema_viewer_projection, "validate_storage") as checked:
            with self.assertRaises(storage.StorageError):
                self.inspect(target, path, item)
        checked.assert_not_called()
        self.assertEqual(record_path.read_bytes(), record_bytes)

    def test_invalid_record_identity_digest_and_encoding_fail_closed(self):
        for mutation in ("project", "generation", "schema", "digest", "json", "checksum"):
            with self.subTest(mutation=mutation):
                target, path, item, record_path, _ = self.fixture()
                record = validation.decode(record_path.read_bytes())
                changed = {
                    "project": replace(record, project_id="foreign_project"),
                    "generation": replace(record, generation_id="tg_backup_" + "f" * 32),
                    "schema": replace(record, schema=record.schema - 1),
                    "digest": replace(record, sha256="f" * 64),
                }.get(mutation)
                raw = validation.encode(changed) if changed else b"{}\n" if mutation == "json" else record_path.read_bytes().replace(b'"checksum":"', b'"checksum":"0', 1)
                record_path.write_bytes(raw)
                with self.assertRaises(storage.StorageError):
                    self.inspect(target, path, item)
                self.assertEqual(record_path.read_bytes(), raw)

    def test_sidecars_and_hardlinks_are_rejected_even_with_valid_record(self):
        target, path, item, _, _ = self.fixture()
        for suffix in ("-journal", "-wal", "-shm", "-WAL"):
            extra = Path(str(path) + suffix)
            extra.touch()
            with self.subTest(suffix=suffix), self.assertRaises(storage.StorageError):
                self.inspect(target, path, item)
            extra.unlink()
        extra = path.with_name("alias.sqlite")
        os.link(path, extra)
        with self.assertRaises(storage.StorageError):
            self.inspect(target, path, item)
        extra.unlink()

    def test_healthy_primary_setup_does_not_inspect_or_hash_old_backup_contents(self):
        target, path, _, record_path, install = self.fixture(canonical=True)
        # Physical/inventory shape remains valid; the old contents are unnecessary
        # for setup of this already healthy authoritative primary.
        path.write_bytes(b"not a database")
        with mock.patch.object(validation, "inspect_artifact", side_effect=AssertionError("unnecessary audit")), \
             mock.patch.object(validation, "hash_physical_file", side_effect=AssertionError("unnecessary hash")):
            observed = state_resolver.resolve_setup_project_state(skill_root=install.skill_root, repo=install.project_root)
            for read_only in (True, False):
                result = setup.run_setup(repo=str(install.project_root), repo_explicit=True,
                    script_path=install.entrypoint, read_only=read_only,
                    backup_interval_minutes=None, backup_generations=None)
                self.assertTrue(result.ok, result.error_code)
        self.assertIsNone(observed.error_code)
        self.assertTrue(record_path.is_file())
        target.db_path.unlink()
        observed = state_resolver.resolve_setup_project_state(skill_root=install.skill_root, repo=install.project_root)
        self.assertEqual(observed.error_code, "project_state_unreadable")

    def test_orphan_record_cannot_recover_a_database_and_cleanup_never_deletes_artifact(self):
        target, path, item, record_path, _ = self.fixture()
        before = path.read_bytes()
        record = validation.decode(record_path.read_bytes())
        with backup.managed_backup_lock(target):
            validation.cleanup_orphans(path.parent, project_id=target.project.project_id)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(record_path.exists())
        path.unlink()
        with backup.managed_backup_lock(target):
            validation.cleanup_orphans(path.parent, project_id=target.project.project_id)
        self.assertFalse(record_path.exists())
        self.assertEqual(repository.read_managed_backup_repository(target).generations, (item,))
        record_path.write_bytes(validation.encode(replace(record, project_id='foreign_project')))
        with backup.managed_backup_lock(target), self.assertRaises(storage.StorageError):
            validation.cleanup_orphans(path.parent, project_id=target.project.project_id)
        self.assertTrue(record_path.exists())

    def test_publication_failure_leaves_bounded_non_authoritative_record(self):
        target, _, _, _, _ = self.fixture()
        item = metadata(2, 30, 3)
        real = backup.os.replace
        def replace_file(source, destination):
            if str(destination).endswith(".sqlite"):
                raise OSError("isolated publication interruption")
            return real(source, destination)
        with backup.managed_backup_lock(target), mock.patch.object(backup.os, "replace", side_effect=replace_file):
            with self.assertRaises(storage.StorageError):
                backup._copy(target, item)
        record = validation.record_directory(target.resolved_backups_path) / (item.generation_id + ".json")
        self.assertTrue(record.exists())
        self.assertFalse((target.resolved_backups_path / backup._filename(item)).exists())
        with backup.managed_backup_lock(target):
            validation.cleanup_orphans(target.resolved_backups_path, project_id=target.project.project_id)
        self.assertFalse(record.exists())

    def test_explicit_content_audit_does_not_accept_the_cached_inspection(self):
        target, path, item, _, _ = self.fixture()
        with mock.patch.object(schema_viewer_projection, "validate_storage",
                               wraps=schema_viewer_projection.validate_storage) as checked:
            observed = validation.inspect_artifact(path, item, project_id=target.project.project_id, audit=True)
        self.assertTrue(observed.recovery_content_valid)
        self.assertEqual(checked.call_count, 1)

    def test_same_byte_replacement_after_recovery_plan_is_not_continuity(self):
        target, path, _, _, install = self.fixture(canonical=True)
        target.db_path.unlink()
        resolution = state_resolver.resolve_setup_project_state(skill_root=install.skill_root, repo=install.project_root)
        self.assertIsNone(resolution.error_code)
        candidate = backup.select_managed_backup_for_recovery(target)
        replacement = path.with_name("replacement.sqlite")
        shutil.copyfile(path, replacement)
        os.replace(replacement, path)
        with backup.managed_backup_lock(target), self.assertRaises(storage.StorageError):
            backup.restore_managed_backup(target, candidate, expected_recovery=resolution.fixed_recovery)
        self.assertFalse(target.db_path.exists())

    def test_restore_pins_certified_contents_before_starting_copy(self):
        target, path, _, record_path, install = self.fixture(canonical=True)
        target.db_path.unlink()
        resolution = state_resolver.resolve_setup_project_state(skill_root=install.skill_root, repo=install.project_root)
        candidate = backup.select_managed_backup_for_recovery(target)
        record_bytes = record_path.read_bytes()
        original = backup._require_fixed_recovery_state
        def change_after_inventory(*args):
            original(*args)
            stamp = path.stat()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('DELETE FROM schema_migrations WHERE version=1')
                connection.commit()
            self.assertEqual(path.stat().st_size, stamp.st_size)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with backup.managed_backup_lock(target), \
             mock.patch.object(backup, '_require_fixed_recovery_state', side_effect=change_after_inventory), \
             mock.patch.object(backup, 'connect_traced', side_effect=AssertionError('copy before source admission')), \
             self.assertRaises(storage.StorageError):
            backup.restore_managed_backup(target, candidate, expected_recovery=resolution.fixed_recovery)
        self.assertFalse(target.db_path.exists())
        self.assertEqual(record_path.read_bytes(), record_bytes)

    def test_copy_drift_during_fresh_source_admission_is_not_published(self):
        target, path, _, _, _ = self.fixture()
        last_good = path.read_bytes()
        original = backup._validate_publication_source
        calls = 0
        def drift(connection, *args, **kwargs):
            nonlocal calls
            calls += 1
            result = original(connection, *args, **kwargs)
            if calls == 3:
                temporary, = target.resolved_backups_path.glob('.taskgov-backup-*.tmp')
                stamp = temporary.stat()
                changed = bytearray(temporary.read_bytes())
                changed[-1] ^= 1
                temporary.write_bytes(changed)
                os.utime(temporary, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            return result
        item = metadata(2, 30, 3)
        with backup.managed_backup_lock(target), \
             mock.patch.object(backup, '_validate_publication_source', side_effect=drift), \
             self.assertRaises(storage.StorageError):
            backup._copy(target, item)
        self.assertFalse((target.resolved_backups_path / backup._filename(item)).exists())
        self.assertEqual(path.read_bytes(), last_good)

    def test_present_bad_record_never_drops_an_owned_generation(self):
        target, path, item, record_path, _ = self.fixture()
        record_path.write_bytes(b'{}\n')
        with backup.managed_backup_lock(target), self.assertRaises(storage.StorageError):
            backup._reconcile_v11(target, observed_at='2026-07-27T02:00:00Z')
        self.assertTrue(path.is_file())
        self.assertEqual(repository.read_managed_backup_repository(target).generations, (item,))

    def test_retention_keeps_valid_fallback_alongside_locally_rejected_head(self):
        from task_governance_tool import tasks
        from tests.test_m214b_recovery_boundaries import _downgrade_candidate_to_v17
        from tests.m214b_test_support import replace_verification
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = make_target(root)
        title = 'Retained fallback'
        with closing(storage.connect_existing(target.db_path)) as connection:
            tasks.add_task(connection, target.project, title=title, kind='optional')
            connection.commit()
        with backup.managed_backup_lock(target):
            for index in range(1, 4):
                item = metadata(index, index * 30, 3)
                backup._copy(target, item)
                repository.record_managed_backup(target, item)
            artifacts = backup._discover(target)
            for artifact in artifacts[-2:]:
                _downgrade_candidate_to_v17(artifact.path)
                replace_verification(artifact.path, title, 'x' * 501)
            artifacts = backup._discover(target)
            with self.assertRaises(storage.StorageError):
                backup._prune(artifacts, 2, artifacts[-1].metadata.generation_id)
            self.assertTrue(all(item.path.exists() for item in artifacts))
            replace_verification(artifacts[-2].path, title, '')
            artifacts = backup._discover(target)
            backup._prune(artifacts, 2, artifacts[-1].metadata.generation_id)
        self.assertFalse(artifacts[0].path.exists())
        self.assertTrue(all(item.path.exists() for item in artifacts[-2:]))
        self.assertFalse(self.inspect(target, artifacts[-1].path, artifacts[-1].metadata).recovery_content_valid)

    def test_reconcile_survivor_drift_preserves_last_good(self):
        for file_only in (False, True):
            for defect in ("record", "content", "replacement"):
                with self.subTest(file_only=file_only, defect=defect):
                    target, old_path, old_item, _, _ = self.fixture()
                    old_bytes = old_path.read_bytes()
                    latest = metadata(2, 30, 1)
                    with backup.managed_backup_lock(target):
                        backup._copy(target, latest)
                        if not file_only:
                            repository.record_managed_backup(target, latest)
                        latest_path = target.resolved_backups_path / backup._filename(latest)
                        record_path = validation.record_directory(latest_path.parent) / (latest.generation_id + ".json")
                        real_read = backup.read_managed_backup_repository
                        changed = False
                        def read(*args, **kwargs):
                            nonlocal changed
                            result = real_read(*args, **kwargs)
                            if not changed:
                                changed = True
                                if defect == "record":
                                    record_path.write_bytes(b"{}")
                                elif defect == "content":
                                    stamp = latest_path.stat()
                                    with closing(sqlite3.connect(latest_path)) as connection:
                                        connection.execute("DELETE FROM schema_migrations WHERE version=1")
                                        connection.commit()
                                    os.utime(latest_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                                else:
                                    stamp = latest_path.stat()
                                    replacement = latest_path.with_suffix(".replacement")
                                    replacement.write_bytes(latest_path.read_bytes())
                                    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                                    os.replace(replacement, latest_path)
                            return result
                        with mock.patch.object(backup, "read_managed_backup_repository", side_effect=read):
                            with self.assertRaises(storage.StorageError):
                                backup._reconcile_v11(target, observed_at=timestamp(60))
                    self.assertTrue(changed)
                    self.assertEqual(old_path.read_bytes(), old_bytes)
                    self.assertIn(old_item, repository.read_managed_backup_repository(target).generations)

    def test_reconcile_requires_eligible_survivor_and_keeps_local_rejection(self):
        from task_governance_tool import tasks
        from tests.test_m214b_recovery_boundaries import _downgrade_candidate_to_v17
        from tests.m214b_test_support import replace_verification
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = make_target(root)
        title = "Reconciliation fallback"
        with closing(storage.connect_existing(target.db_path)) as connection:
            tasks.add_task(connection, target.project, title=title, kind="optional")
            connection.commit()
        with backup.managed_backup_lock(target):
            for index in range(1, 4):
                item = metadata(index, index * 30, 2 if index == 3 else 3)
                backup._copy(target, item)
                repository.record_managed_backup(target, item)
            artifacts = backup._discover(target)
            for artifact in artifacts[-2:]:
                _downgrade_candidate_to_v17(artifact.path)
                replace_verification(artifact.path, title, "x" * 501)
            with self.assertRaises(storage.StorageError):
                backup._reconcile_v11(target, observed_at=timestamp(120))
            self.assertTrue(all(item.path.exists() for item in artifacts))
            self.assertEqual(len(repository.read_managed_backup_repository(target).generations), 3)
            replace_verification(artifacts[-2].path, title, "")
            self.assertTrue(backup._reconcile_v11(target, observed_at=timestamp(120)))
        self.assertFalse(artifacts[0].path.exists())
        self.assertTrue(all(item.path.exists() for item in artifacts[-2:]))
        self.assertEqual(len(repository.read_managed_backup_repository(target).generations), 2)
        self.assertFalse(self.inspect(target, artifacts[-1].path, artifacts[-1].metadata).recovery_content_valid)

    def test_recordless_survivor_content_continuity_in_all_retention_paths(self):
        from task_governance_tool import async_backup_capture as capture
        from task_governance_tool import async_backup_repository as control_repository
        for route in ("legacy", "reconcile", "async"):
            for change in ("foreign", "same_project"):
                with self.subTest(route=route, change=change):
                    target, old_path, old_item, _, _ = self.fixture()
                    latest = metadata(2, 30, 1)
                    with backup.managed_backup_lock(target):
                        backup._copy(target, latest)
                        repository.record_managed_backup(target, latest)
                        path = target.resolved_backups_path / backup._filename(latest)
                        (validation.record_directory(path.parent) / (latest.generation_id + ".json")).unlink()
                        artifacts = backup._discover(target)
                        old_bytes = old_path.read_bytes()
                        stamp = path.stat()
                        if change == "foreign":
                            foreign, _, _, _, _ = self.fixture()
                            with backup.managed_backup_lock(foreign):
                                backup._copy(foreign, latest)
                            replacement = (foreign.resolved_backups_path / backup._filename(latest)).read_bytes()
                            self.assertEqual(len(replacement), stamp.st_size)
                            path.write_bytes(replacement)
                        else:
                            with closing(sqlite3.connect(path)) as connection:
                                connection.execute("UPDATE project_maintenance SET backup_interval_minutes=31")
                                connection.commit()
                        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                        self.assertEqual(backup._file_identity(path), artifacts[-1].identity)
                        self.assertTrue(validation.inspect_artifact(path, latest).recovery_content_valid)
                        with self.assertRaises(storage.StorageError):
                            if route == "legacy":
                                backup._prune(artifacts, 1, latest.generation_id)
                            elif route == "reconcile":
                                with mock.patch.object(backup, "_discover", return_value=artifacts):
                                    backup._reconcile_v11(target, observed_at=timestamp(60))
                            else:
                                control = control_repository.read_control(target)
                                capture._prune(target, control, artifacts)
                        self.assertEqual(old_path.read_bytes(), old_bytes)
                        self.assertIn(old_item, repository.read_managed_backup_repository(target).generations)

    def test_recordless_repository_relationships_precede_reconciliation_effects(self):
        from task_governance_tool import async_backup_capture as capture
        from task_governance_tool import async_backup_repository as control_repository
        for route in ("sync", "async"):
            for file_only in (False, True):
                for defect in ("conflict", "future", "missing_prefix"):
                    with self.subTest(route=route, file_only=file_only, defect=defect):
                        target, old_path, old_item, _, install = self.fixture(canonical=True)
                        latest = metadata(2, 30, 1)
                        with backup.managed_backup_lock(target):
                            backup._copy(target, latest)
                            if not file_only:
                                repository.record_managed_backup(target, latest)
                            path = target.resolved_backups_path / backup._filename(latest)
                            (validation.record_directory(path.parent) / (latest.generation_id + ".json")).unlink()
                            with closing(sqlite3.connect(path)) as connection:
                                if defect == "missing_prefix":
                                    connection.execute("DELETE FROM managed_backup_generations")
                                    connection.execute("UPDATE project_maintenance SET latest_backup_generation_id=NULL, backup_last_success_at=NULL, applied_backup_generations=NULL")
                                else:
                                    identity = "tg_backup_" + "f" * 32 if defect == "future" else old_item.generation_id
                                    connection.execute("UPDATE managed_backup_generations SET generation_id=?, published_at=?", (identity, timestamp(60)))
                                    connection.execute("UPDATE project_maintenance SET latest_backup_generation_id=?, backup_last_success_at=?", (identity, timestamp(60)))
                                connection.commit()
                            # Internal SQLite/domain admission is successful; only the
                            # relationship with its publication and peers is invalid.
                            self.assertTrue(self.inspect(target, path, latest).recovery_content_valid)
                            before_rows = repository.read_managed_backup_repository(target).generations
                            old_bytes = old_path.read_bytes()
                            with self.assertRaises(storage.StorageError):
                                if route == "sync":
                                    backup._reconcile_v11(target, observed_at=timestamp(60))
                                else:
                                    capture._reconcile(target, control_repository.read_control(target), timestamp(60))
                            self.assertEqual(repository.read_managed_backup_repository(target).generations, before_rows)
                            self.assertEqual(old_path.read_bytes(), old_bytes)
                            self.assertTrue(path.exists())

    def test_recordless_file_only_drift_cannot_be_imported_without_pruning(self):
        from task_governance_tool import async_backup_capture as capture
        from task_governance_tool import async_backup_repository as control_repository
        for route in ("sync", "async"):
            with self.subTest(route=route):
                target, old_path, old_item, _, _ = self.fixture()
                latest = metadata(2, 30, 3)
                with backup.managed_backup_lock(target):
                    backup._copy(target, latest)
                    path = target.resolved_backups_path / backup._filename(latest)
                    (validation.record_directory(path.parent) / (latest.generation_id + ".json")).unlink()
                    original = backup._validate_artifact_relationships
                    def change_after_validation(*args, **kwargs):
                        original(*args, **kwargs)
                        stamp = path.stat()
                        with closing(sqlite3.connect(path)) as connection:
                            connection.execute("UPDATE project_maintenance SET backup_interval_minutes=31")
                            connection.commit()
                        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    with mock.patch.object(backup, "_validate_artifact_relationships", side_effect=change_after_validation):
                        with self.assertRaises(storage.StorageError):
                            if route == "sync":
                                backup._reconcile_v11(target, observed_at=timestamp(60))
                            else:
                                capture._reconcile(target, control_repository.read_control(target), timestamp(60))
                    self.assertEqual(repository.read_managed_backup_repository(target).generations, (old_item,))
                    self.assertTrue(old_path.exists())
                    self.assertTrue(path.exists())

    def test_record_codec_and_inventory_bounds_are_closed(self):
        target, path, item, record_path, _ = self.fixture()
        record = validation.decode(record_path.read_bytes())
        for changed in (replace(record, version=True), replace(record, rules=0),
                        replace(record, size=0), replace(record, schema=True),
                        replace(record, mode='structure'), replace(record, classification='unknown')):
            with self.subTest(changed=changed), self.assertRaises(storage.StorageError):
                validation.decode(validation.encode(changed))
        for name in ('unknown.json', item.generation_id + '-unknown.json'):
            extra = record_path.with_name(name)
            extra.write_bytes(b'{}')
            with self.assertRaises(storage.StorageError):
                validation.inventory(path.parent)
            extra.unlink()
        record_path.write_bytes(b'x' * (validation.MAX_RECORD_BYTES + 1))
        with self.assertRaises((storage.StorageError, validation.StatePathError)):
            validation.inventory(path.parent)
        record_path.write_bytes(validation.encode(record))
        consumed = 0
        def unbounded_entries():
            nonlocal consumed
            while True:
                consumed += 1
                if consumed > validation.MAX_RECORDS + 2:
                    raise AssertionError('unbounded record discovery')
                yield record_path
        with mock.patch.object(Path, 'iterdir', side_effect=unbounded_entries), \
             self.assertRaises(storage.StorageError):
            validation.inventory(path.parent)
        self.assertEqual(consumed, validation.MAX_RECORDS + 2)

    def test_healthy_primary_still_rejects_filename_ledger_disagreement(self):
        target, _, item, _, install = self.fixture(canonical=True)
        with closing(storage.connect_existing(target.db_path)) as connection:
            changed = '2026-07-27T00:01:00Z'
            connection.execute('UPDATE managed_backup_generations SET published_at=? WHERE generation_id=?',
                               (changed, item.generation_id))
            connection.execute('UPDATE project_maintenance SET backup_last_success_at=?', (changed,))
            connection.commit()
        with mock.patch.object(validation, 'inspect_artifact', side_effect=AssertionError('old content read')):
            resolution = state_resolver.resolve_setup_project_state(
                skill_root=install.skill_root, repo=install.project_root)
        self.assertEqual(resolution.error_code, 'project_state_unreadable')

    def test_old_publication_admission_cannot_certify_unchecked_task_content(self):
        from tests.test_setup_backup import make_target as empty_target
        from tests.m14_test_support import create_v10_target
        from tests.test_db_init import insert_task
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = empty_target(root)
        create_v10_target(target)
        with closing(sqlite3.connect(target.db_path)) as connection:
            insert_task(connection, task_id='tg_task_old', project_id=target.project.project_id,
                        title='Legacy validation gate')
            connection.execute('UPDATE tasks SET verification=?', ('x' * 501,))
            connection.commit()
        item = metadata(1, 0, 3)
        with backup.managed_backup_lock(target), self.assertRaises(storage.StorageError):
            backup._copy(target, item)
        self.assertFalse((target.resolved_backups_path / backup._filename(item)).exists())
        self.assertEqual(validation.inventory(target.resolved_backups_path), ())

    def test_operational_inspection_errors_keep_existing_codes(self):
        target, path, item, record_path, _ = self.fixture()
        record_path.unlink()
        for code in ('database_busy', 'unsupported_journal_mode', 'schema_too_new'):
            with self.subTest(code=code), \
                 mock.patch.object(state_resolver, '_inspect_database',
                                   side_effect=state_resolver._ResolverFailure(code)), \
                 self.assertRaises(storage.StorageError) as raised:
                self.inspect(target, path, item)
            self.assertEqual(raised.exception.code, code)

    def test_unsafe_record_inventory_is_a_sanitized_resolution_failure(self):
        _, _, _, record_path, install = self.fixture(canonical=True)
        record_path.write_bytes(b'x' * (validation.MAX_RECORD_BYTES + 1))
        resolution = state_resolver.resolve_setup_project_state(
            skill_root=install.skill_root, repo=install.project_root)
        self.assertEqual(resolution.error_code, 'project_state_unreadable')

    def test_separation_inventory_copies_and_seals_bounded_validation_domain(self):
        from task_governance_tool.setup_state_inventory import inspect_inventory, copy_inventory
        target, _, _, record_path, _ = self.fixture()
        pending = record_path.parent / '.pending.json'
        pending.write_bytes(b'interrupted bounded publication')
        root = target.db_path.parent
        before = inspect_inventory(root, strict=True)
        names = {item.path.relative_to(root).as_posix() for item in before.files}
        self.assertIn(record_path.relative_to(root).as_posix(), names)
        self.assertIn(pending.relative_to(root).as_posix(), names)
        destination = Path(self.enterContext(tempfile.TemporaryDirectory()))
        copy_inventory(before, destination, skip_database=False)
        copied = inspect_inventory(destination, strict=True)
        self.assertEqual(copied.digest, before.digest)
        copied_record = destination / record_path.relative_to(root)
        self.assertEqual(copied_record.read_bytes(), record_path.read_bytes())
        copied_record.write_bytes(b'changed sealed bytes')
        self.assertNotEqual(inspect_inventory(destination, strict=True).digest, before.digest)
        self.assertEqual(inspect_inventory(root, strict=False).digest, before.digest)

    def test_separation_does_not_treat_unknown_validation_entries_as_opaque_source(self):
        from task_governance_tool.setup_state_inventory import inspect_inventory
        target, _, _, record_path, _ = self.fixture()
        unknown = record_path.parent / 'unknown.json'
        unknown.write_bytes(b'preserve unknown record')
        for strict in (False, True):
            with self.subTest(strict=strict), self.assertRaises(storage.StorageError):
                inspect_inventory(target.db_path.parent, strict=strict)
        self.assertEqual(unknown.read_bytes(), b'preserve unknown record')


if __name__ == "__main__":
    unittest.main()
