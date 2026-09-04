from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests import test_m243b_schema21_compatibility as manual_fixture
from tests import test_m23s_schema22_validation as history_fixture
from tests import test_m243c_runner_gate as runner_fixture
from tests.m14_test_support import _copy_skill, tree_snapshot
from tests.m214b_test_support import publish_generations, replace_verification
from tests.test_m17_recovery_hardening import _setup_current
from tests.test_m19_legacy_upgrade_rehearsal import (
    seed_supported_local_configs,
    supported_local_config_snapshot,
)
from tests.test_m242_runner_service import RunnerServiceFixture
from tests.test_m23s_schema22_migration import _logical_snapshot
from task_governance_tool import backup, doctor, state_resolver, tasks, viewer
from task_governance_tool import verification_runner_service as runner_service


storage = history_fixture.storage
OBSERVED_AT = "2026-09-04T00:00:00Z"


def _completed22(testcase, root):
    install, target, task_id = manual_fixture._seed_completed_m21_fixture(testcase, root)
    with closing(storage.connect(target.db_path)) as connection:
        testcase.assertTrue(storage._migrate_schema22_connection(connection))
    return install, target, task_id


def _ready_recovery_task(testcase, install):
    title = "Schema22 local recovery candidate"
    added = install.run("task", "add", "--title", title, "--json")
    testcase.assertEqual(added.returncode, 0, added.stderr)
    return title


class Schema22ConsumerTests(unittest.TestCase):
    def test_empty22_setup_helper_retains_exact_owned_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.sqlite3"
            with closing(storage.connect(path)) as connection:
                storage.apply_migrations(connection)
                self.assertTrue(storage._migrate_schema22_connection(connection))
            before = tree_snapshot(Path(temporary))
            self.assertTrue(storage._is_exact_empty_completion_history_database(path))
            self.assertEqual(tree_snapshot(Path(temporary)), before)
            with closing(storage.connect(path)) as connection:
                connection.execute("CREATE TABLE unrelated_fixture (value TEXT)")
                connection.commit()
            before = tree_snapshot(Path(temporary))
            self.assertFalse(storage._is_exact_empty_completion_history_database(path))
            self.assertEqual(tree_snapshot(Path(temporary)), before)

    def test_backup_recovery_preserves_old_bundle_and_supported_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            install, target, _task_id = _completed22(self, Path(temporary))
            configs = seed_supported_local_configs(install.skill_root)
            with closing(storage.connect_readonly(target.db_path)) as connection:
                _basis, original = history_fixture._bundle_artifacts(connection, target.project.project_id)
            # These local constants exercise prepared dispatch, not public
            # activation; every schema, binding, and content validator stays real.
            with mock.patch.object(storage, "SCHEMA_VERSION", 22), \
                 mock.patch.object(backup, "SCHEMA_VERSION", 22), \
                 mock.patch.object(state_resolver, "SCHEMA_VERSION", 22):
                artifacts = publish_generations(target, "2098-09-04T00:00:00Z", "2099-09-04T00:00:00Z")
                target.db_path.unlink()
                before = tree_snapshot(install.skill_root)
                candidate = backup.select_managed_backup_for_recovery(target)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.path, artifacts[-1].path)
                self.assertEqual(candidate.schema_version, 22)
                resolution = state_resolver.resolve_setup_project_state(
                    skill_root=install.skill_root, repo=install.project_root
                )
                self.assertIsNone(resolution.error_code)
                self.assertIsNotNone(resolution.fixed_recovery)
                self.assertEqual(tree_snapshot(install.skill_root), before)
                with backup.managed_backup_lock(target):
                    restored_version = backup.restore_managed_backup(
                        target, candidate, expected_recovery=resolution.fixed_recovery
                    )
                self.assertEqual(restored_version, 22)
                with closing(storage.connect_readonly(target.db_path)) as connection:
                    storage.validate_schema22_storage(connection)
                    _basis, restored = history_fixture._bundle_artifacts(connection, target.project.project_id)
                    self.assertEqual(restored, original)
                self.assertEqual(supported_local_config_snapshot(install.skill_root), configs)
                self.assertEqual(list(target.db_path.parent.glob(".taskgov-restore-*.tmp")), [])
            self.assertEqual(storage.SCHEMA_VERSION, 21)

    def test_verification_only_recovery_rejection_remains_candidate_local(self):
        with tempfile.TemporaryDirectory() as temporary:
            install, target, _identity = _setup_current(Path(temporary))
            title = _ready_recovery_task(self, install)
            with closing(storage.connect(target.db_path)) as connection:
                self.assertTrue(storage._migrate_schema22_connection(connection))
            with mock.patch.object(storage, "SCHEMA_VERSION", 22), \
                 mock.patch.object(backup, "SCHEMA_VERSION", 22), \
                 mock.patch.object(state_resolver, "SCHEMA_VERSION", 22):
                artifacts = publish_generations(target, "2098-09-05T00:00:00Z", "2099-09-05T00:00:00Z")
                replace_verification(artifacts[-1].path, title, "x" * 1001)
                target.db_path.unlink()
                before = tree_snapshot(install.skill_root)
                candidate = backup.select_managed_backup_for_recovery(target)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.path, artifacts[-2].path)
                self.assertEqual(candidate.schema_version, 22)
                newest = next(item for item in candidate.inventory if item.path == artifacts[-1].path)
                self.assertFalse(newest.content_valid)
                self.assertIn(artifacts[-1].path, {item.path for item in backup._discover(target)})
                resolution = state_resolver.resolve_setup_project_state(
                    skill_root=install.skill_root, repo=install.project_root
                )
                self.assertIsNone(resolution.error_code)
                self.assertIsNotNone(resolution.fixed_recovery)
                self.assertEqual(resolution.fixed_recovery.selected.path, artifacts[-2].path)
                resolver_newest = next(
                    item for item in resolution.fixed_recovery.managed_backups
                    if item.path == artifacts[-1].path
                )
                self.assertFalse(resolver_newest.recovery_content_valid)
                self.assertEqual(tree_snapshot(install.skill_root), before)

    def test_bundle_and_runner_structure_remain_recovery_set_fatal(self):
        for fault in ("bundle", "runner"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                install, target, task_id = manual_fixture._seed_completed_m21_fixture(self, Path(temporary))
                local_title = _ready_recovery_task(self, install)
                with closing(storage.connect(target.db_path)) as connection:
                    self.assertTrue(storage._migrate_schema22_connection(connection))
                with mock.patch.object(storage, "SCHEMA_VERSION", 22), \
                     mock.patch.object(backup, "SCHEMA_VERSION", 22), \
                     mock.patch.object(state_resolver, "SCHEMA_VERSION", 22):
                    artifacts = publish_generations(target, "2098-09-06T00:00:00Z", "2099-09-06T00:00:00Z")
                    with closing(sqlite3.connect(artifacts[-1].path)) as connection:
                        # The local verification exception cannot hide a graph fault.
                        connection.execute("UPDATE tasks SET verification=? WHERE title=?", ("x" * 1001, local_title))
                        if fault == "runner":
                            connection.execute("UPDATE tasks SET review_target_runner_basis_version=2 WHERE task_id=?", (task_id,))
                        else:
                            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_completion_evidence_bundles_no_update'").fetchone()[0]
                            connection.execute("DROP TRIGGER trg_completion_evidence_bundles_no_update")
                            connection.execute("UPDATE completion_evidence_bundles SET bundle_digest=? WHERE task_id=?",
                                               ("sha256:" + "f" * 64, task_id))
                            connection.execute(trigger)
                        connection.commit()
                    target.db_path.unlink()
                    before = tree_snapshot(install.skill_root)
                    with self.assertRaises(storage.StorageError) as rejected:
                        backup.select_managed_backup_for_recovery(target)
                    self.assertEqual(rejected.exception.code, "setup_restore_failed")
                    resolution = state_resolver.resolve_setup_project_state(
                        skill_root=install.skill_root, repo=install.project_root
                    )
                    self.assertEqual(resolution.error_code, "project_state_unreadable")
                    self.assertEqual(tree_snapshot(install.skill_root), before)
                    self.assertFalse(target.db_path.exists())

    def test_viewer_and_doctor_helpers_keep_public_shapes_and_history_read_only(self):
        fixture = history_fixture.Schema22StoredValidationTests
        fixture.setUpClass()
        try:
            target = fixture.target
            with closing(storage.connect_snapshot_readonly(target.db_path)) as connection:
                original_view = viewer.build_viewer_snapshot(connection, target, generated_at=OBSERVED_AT).snapshot
                original_doctor = storage.read_doctor_state(connection, target)
                _basis, original = history_fixture._bundle_artifacts(connection, target.project.project_id)
            with closing(storage.connect(target.db_path)) as connection:
                self.assertTrue(storage._migrate_schema22_connection(connection))
            before = tree_snapshot(fixture.install.skill_root)
            with mock.patch.object(storage, "SCHEMA_VERSION", 22), mock.patch.object(
                tasks, "_consume_validated_viewer_task_batch",
                wraps=tasks._consume_validated_viewer_task_batch,
            ) as consume_batch:
                with closing(storage.connect_snapshot_readonly(target.db_path)) as connection:
                    database_before = _logical_snapshot(connection)
                    observed_view = viewer.build_viewer_snapshot(connection, target, generated_at=OBSERVED_AT).snapshot
                    observed_doctor = storage.read_doctor_state(connection, target)
                    _basis, artifacts = history_fixture._bundle_artifacts(connection, target.project.project_id)
                    self.assertEqual(artifacts, original)
                    self.assertEqual(_logical_snapshot(connection), database_before)
                    self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                consume_batch.assert_called_once()
            self.assertEqual(observed_view, {**original_view, "source_schema_version": 22})
            self.assertEqual(observed_view["snapshot_version"], 4)
            self.assertEqual(observed_doctor, replace(original_doctor, schema_version=22))
            serialized = json.dumps(observed_view, sort_keys=True)
            self.assertNotIn("runner_observation", serialized)
            self.assertNotIn("verification_basis", serialized)
            self.assertEqual(tree_snapshot(fixture.install.skill_root), before)
            self.assertEqual(storage.SCHEMA_VERSION, 21)
        finally:
            fixture.doClassCleanups()

    def test_unpatched_public_doctor_and_resolver_still_reject_newer22_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            install, _target, _task_id = _completed22(self, Path(temporary))
            before = tree_snapshot(install.skill_root)
            resolution = state_resolver.resolve_project_state(skill_root=install.skill_root, repo=install.project_root)
            self.assertEqual(resolution.error_code, "schema_too_new")
            result = doctor.run_doctor(repo=str(install.project_root), repo_explicit=True,
                                       script_path=install.entrypoint)
            self.assertFalse(result.ok)
            self.assertEqual(result.errors[0]["code"], "schema_too_new")
            self.assertEqual(result.data["components"]["project_state"]["required_schema_version"], 21)
            self.assertEqual(tree_snapshot(install.skill_root), before)

    def test_restored_pending_runner_is_not_relaunched_and_keeps_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            parent = fixture.repo / ".agents" / "skills"
            parent.mkdir(parents=True)
            package = _copy_skill(parent)
            fixture.target = replace(fixture.target, skill_root=package)
            configs = seed_supported_local_configs(package)
            _prepared, intent = runner_fixture._launch(fixture)
            with closing(storage.connect(fixture.db)) as connection:
                self.assertTrue(storage._migrate_schema22_connection(connection))
            with mock.patch.object(storage, "SCHEMA_VERSION", 22), \
                 mock.patch.object(backup, "SCHEMA_VERSION", 22), \
                 mock.patch.object(runner_service, "run_process_request", side_effect=AssertionError("recovery relaunched Runner")) as process:
                artifacts = publish_generations(fixture.target, "2099-09-07T00:00:00Z")
                with closing(storage.connect_readonly(fixture.db)) as connection:
                    original = storage.read_verification_runner_generation_locked(connection,
                        project_id=fixture.target.project.project_id, task_id=fixture.task_id,
                        target_generation=intent.resolution.target_generation)
                fixture.db.unlink()
                candidate = backup.select_managed_backup_for_recovery(fixture.target)
                self.assertEqual(candidate.path, artifacts[-1].path)
                self.assertEqual(backup.restore_managed_backup(fixture.target, candidate), 22)
                with closing(storage.connect_readonly(fixture.db)) as connection:
                    restored = storage.read_verification_runner_generation_locked(connection,
                        project_id=fixture.target.project.project_id, task_id=fixture.task_id,
                        target_generation=intent.resolution.target_generation)
                self.assertEqual(restored, original)
                self.assertEqual(restored["state"], "pending")
                paths = runner_service._runner_paths(fixture.target)
                with runner_service.zero_wait_runner_lock(paths) as inventory:
                    with self.assertRaises(runner_service.VerificationRunnerServiceError) as rejected:
                        runner_service._deny_unresolved_attempt(fixture.target, paths, inventory)
                self.assertEqual(rejected.exception.code, "runner_state_invalid")
                with closing(storage.connect_readonly(fixture.db)) as connection:
                    cleaned = storage.read_verification_runner_generation_locked(connection,
                        project_id=fixture.target.project.project_id, task_id=fixture.task_id,
                        target_generation=intent.resolution.target_generation)
                    storage.validate_schema22_storage(connection)
                self.assertEqual(cleaned["state"], "restart_cleaned")
                self.assertEqual(cleaned["resolution"], original["resolution"])
                self.assertEqual(cleaned["attempt"], original["attempt"])
                self.assertIsNone(cleaned["observation"])
                self.assertIsNone(cleaned["cleanup_event"].terminal_observation_id)
                process.assert_not_called()
                self.assertEqual(supported_local_config_snapshot(package), configs)


if __name__ == "__main__":
    unittest.main()
