"""Portable, isolated integration checks for setup's state-root cutover."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import shutil
import hashlib
import os
import tempfile
import unittest
from unittest import mock

from tests.m14_test_support import (
    make_physical_install, file_snapshot, _copy_skill, PhysicalInstall, create_v12_target,
)
from task_governance_tool import setup, setup_state_separation as separation
from task_governance_tool.artifact_lock import zero_wait_artifact_lock
from task_governance_tool.state_paths import StatePathError
from task_governance_tool.state_resolver import observe_current_root, resolve_setup_project_state
from task_governance_tool.state_separation import read_record
from task_governance_tool.storage import (
    DatabaseTarget, UnboundDatabaseTarget, connect, connect_snapshot_readonly,
    initialize_uuid_database, project_identity, ensure_project_meta,
    apply_initial_schema_migration, apply_completion_commit_migration,
)
from task_governance_tool.backup import publish_setup_backup


class SetupStateSeparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temporary_root = Path(self.temporary.name).resolve(strict=True)
        self.install = make_physical_install(self.temporary_root)
        self.new_root = self.install.project_root / ".taskgov"
        self.old_root = self.install.skill_root / "state"

    def run_setup(self, **overrides):
        arguments = dict(
            repo=str(self.install.project_root), repo_explicit=True,
            script_path=self.install.entrypoint, read_only=False,
            backup_interval_minutes=None, backup_generations=None,
        )
        arguments.update(overrides)
        return setup.run_setup(**arguments)

    def old_current(self, *, repo=None):
        current = observe_current_root(repo or self.install.project_root)
        return initialize_uuid_database(UnboundDatabaseTarget(
            canonical_repo=current.canonical_repo,
            canonical_path_hash=current.canonical_path_hash,
            display_name=current.display_name,
            db_path=self.old_root / "current" / "taskgov.sqlite", explicit_db=True,
            skill_root=self.install.skill_root,
        )).target

    def old_legacy(self):
        project = project_identity(self.install.project_root)
        target = DatabaseTarget(
            project=project,
            db_path=self.old_root / "projects" / project.project_id / "taskgov.sqlite",
            explicit_db=True, skill_root=self.install.skill_root,
        )
        create_v12_target(target)
        return target

    def reset_install(self, root):
        self.install = make_physical_install(Path(root).resolve(strict=True))
        self.new_root = self.install.project_root / ".taskgov"
        self.old_root = self.install.skill_root / "state"

    def prepare_barrier_repair(self, origin):
        target = (self.old_current() if origin == "fixed"
                  else self.old_legacy() if origin == "legacy" else None)
        if target is not None:
            publish_setup_backup(target, 3)
            viewer = target.db_path.parent / "viewer"
            viewer.mkdir()
            (viewer / "task-viewer.html").write_bytes(b"retained old projection")
            (target.db_path.parent / "opaque-user-file.txt").write_bytes(b"preserve opaque source")
        resolved = self.assert_success(self.run_setup())
        (self.old_root / "current" / "taskgov.sqlite").unlink()
        return resolved, target

    def assert_success(self, result):
        self.assertTrue(result.ok, (result.error_code, result.data))
        self.assertEqual(result.data["status"], "setup_complete")
        self.assertEqual(result.data["completed_writes"], list(separation.LAYOUT_WRITES))
        self.assertEqual(read_record(self.new_root).phase, "activated")
        resolved = resolve_setup_project_state(
            skill_root=self.install.skill_root, repo=self.install.project_root,
        )
        self.assertIsNone(resolved.error_code)
        self.assertIsNotNone(resolved.target)
        self.assertEqual(resolved.target.db_path, self.new_root / "current" / "taskgov.sqlite")
        return resolved

    def test_fresh_preview_is_write_free_then_activation_is_idempotent(self):
        before = file_snapshot(self.install.project_root)
        preview = self.run_setup(read_only=True)
        self.assertTrue(preview.ok, preview.error_code)
        self.assertEqual(preview.data["status"], "setup_preview")
        self.assertEqual(preview.data["evidence_status"], "not_present")
        self.assertEqual(preview.data["viewer_status"], "not_present")
        self.assertEqual(preview.data["planned_writes"], list(separation.LAYOUT_WRITES))
        self.assertEqual(file_snapshot(self.install.project_root), before)
        self.assertFalse(self.new_root.exists())
        result = self.run_setup()
        resolved = self.assert_success(result)
        self.assertEqual(result.data["evidence_status"], "published")
        self.assertEqual(result.data["viewer_status"], "published")
        self.assertEqual(result.project_id, resolved.project_id)
        self.assertFalse((self.new_root / ".state-separation" / "source").exists())
        self.assertFalse((self.old_root / "current" / "taskgov.sqlite").read_bytes().startswith(b"SQLite"))
        again = self.run_setup()
        self.assertTrue(again.ok, again.error_code)
        self.assertEqual(again.project_id, result.project_id)
        self.assertEqual(again.data["completed_writes"], [])

    def test_existing_identity_source_and_config_are_preserved(self):
        old_target = self.old_current()
        old_database_bytes = old_target.db_path.read_bytes()
        config = self.install.skill_root / "config"
        config.mkdir()
        sentinel = config / "local.txt"
        sentinel.write_bytes(b"not a generated artifact")
        result = self.run_setup()
        resolved = self.assert_success(result)
        self.assertEqual(resolved.project_id, old_target.project.project_id)
        self.assertEqual(resolved.stored_project.binding_generation, 1)
        self.assertEqual(sentinel.read_bytes(), b"not a generated artifact")
        retained = self.new_root / ".state-separation" / "source" / "taskgov.sqlite"
        self.assertTrue(retained.read_bytes().startswith(b"SQLite format 3"))
        with closing(connect_snapshot_readonly(retained)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
        self.assertNotEqual(old_target.db_path.read_bytes(), old_database_bytes)

    def test_retired_prefix_resumes_without_old_database_open(self):
        self.old_current()
        actual = separation.publish_record
        def fail_fenced(state_root, record, *, expected):
            if record.phase == "fenced":
                raise StatePathError()
            return actual(state_root, record, expected=expected)
        with mock.patch.object(separation, "publish_record", side_effect=fail_fenced):
            stopped = self.run_setup()
        self.assertFalse(stopped.ok)
        self.assertEqual(stopped.data["completed_writes"], ["state_layout_retire"])
        self.assertEqual(read_record(self.new_root).phase, "private")
        with mock.patch.object(separation, "resolve_package_project_state", side_effect=AssertionError("old state opened")):
            self.assert_success(self.run_setup())

    def test_published_prefix_resumes_without_overwriting_current(self):
        actual = separation.publish_record
        def fail_activation(state_root, record, *, expected):
            if record.phase == "activated":
                raise StatePathError()
            return actual(state_root, record, expected=expected)
        with mock.patch.object(separation, "publish_record", side_effect=fail_activation):
            stopped = self.run_setup()
        self.assertFalse(stopped.ok)
        self.assertEqual(stopped.data["completed_writes"], list(separation.LAYOUT_WRITES[:2]))
        database = self.new_root / "current" / "taskgov.sqlite"
        before = database.read_bytes()
        with mock.patch.object(separation, "rename_no_replace", side_effect=AssertionError("republished")):
            self.assert_success(self.run_setup())
        self.assertEqual(database.read_bytes(), before)

    def test_source_lock_contention_does_not_retire(self):
        target = self.old_current()
        runner = target.db_path.parent / "verification-runner"
        runner.mkdir()
        (runner / "attempts").mkdir()
        (runner / "quarantine").mkdir()
        lock = runner / "taskgov-verification-runner.lock"
        with zero_wait_artifact_lock(lock):
            result = self.run_setup()
        self.assertFalse(result.ok)
        self.assertEqual(result.data["completed_writes"], [])
        self.assertTrue(target.db_path.read_bytes().startswith(b"SQLite"))

    def test_unexplained_runner_tree_is_preserved_and_refused(self):
        target = self.old_current()
        runner = target.db_path.parent / "verification-runner"
        (runner / "attempts" / ("tg_verification_runner_attempt_" + "a" * 16)).mkdir(parents=True)
        (runner / "quarantine").mkdir()
        before = file_snapshot(self.install.project_root)
        result = self.run_setup()
        self.assertFalse(result.ok)
        self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_empty_runner_layout_is_preserved(self):
        target = self.old_current()
        runner = target.db_path.parent / "verification-runner"
        (runner / "attempts").mkdir(parents=True)
        (runner / "quarantine").mkdir()
        (runner / "taskgov-verification-runner.lock").write_bytes(b"\0")
        self.assert_success(self.run_setup())
        for root in (self.new_root / "current", self.new_root / ".state-separation" / "source"):
            self.assertTrue((root / "verification-runner" / "attempts").is_dir())
            self.assertTrue((root / "verification-runner" / "quarantine").is_dir())
            self.assertEqual((root / "verification-runner" / "taskgov-verification-runner.lock").read_bytes(), b"\0")

    def test_schema_two_legacy_source_migrates_only_in_candidate(self):
        project = project_identity(self.install.project_root)
        original = self.old_root / "projects" / project.project_id / "taskgov.sqlite"
        original.parent.mkdir(parents=True)
        with closing(connect(original)) as connection:
            apply_initial_schema_migration(connection)
            apply_completion_commit_migration(connection)
            ensure_project_meta(connection, project)
            connection.commit()
        before = original.read_bytes()
        resolved = self.assert_success(self.run_setup())
        self.assertEqual(resolved.project_id, project.project_id)
        self.assertEqual(resolved.source_schema_version, 22)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(read_record(self.new_root).source_schema_version, 2)

    def test_fixed_backup_only_source_recovers_in_private_candidate(self):
        target = self.old_current()
        publish_setup_backup(target, 3)
        target.db_path.unlink()
        resolved = self.assert_success(self.run_setup())
        self.assertEqual(resolved.project_id, target.project.project_id)
        self.assertTrue((self.new_root / ".state-separation" / "source" / "taskgov.sqlite").exists())

    def test_legacy_backup_only_source_recovers_without_deleting_generations(self):
        project = project_identity(self.install.project_root)
        database = self.old_root / "projects" / project.project_id / "taskgov.sqlite"
        target = DatabaseTarget(project=project, db_path=database, explicit_db=True,
                                skill_root=self.install.skill_root)
        create_v12_target(target)
        publish_setup_backup(target, 3)
        database.unlink()
        backups_before = file_snapshot(database.parent / "backups")
        resolved = self.assert_success(self.run_setup())
        self.assertEqual(resolved.project_id, project.project_id)
        after = file_snapshot(database.parent / "backups")
        self.assertEqual({name: after[name] for name in backups_before}, backups_before)
        self.assertLessEqual(set(after) - set(backups_before), {"taskgov-backup.lock"})

    def older_selected_backup_source(self, layout):
        from tests.test_m17_legacy_recovery_matrix import _build_local_invalid_newer_source

        target, _, _, rejected = _build_local_invalid_newer_source(self.install)
        if layout == "fixed":
            target.db_path.parent.rename(self.old_root / "current")
            target = replace(target, db_path=self.old_root / "current" / "taskgov.sqlite")
            rejected = target.db_path.parent / "backups" / rejected.name
        return target, rejected

    def test_backup_only_retained_snapshot_and_fenced_retry_keep_rejected_newer_artifact(self):
        for layout in ("fixed", "legacy"):
            for fenced in (False, True):
                with self.subTest(layout=layout, fenced=fenced), tempfile.TemporaryDirectory() as tmp:
                    self.reset_install(tmp)
                    target, rejected = self.older_selected_backup_source(layout)
                    source_before = file_snapshot(target.db_path.parent)
                    rejected_before = rejected.read_bytes()
                    if fenced:
                        actual = separation.publish_record

                        def stop_after_fencing(state_root, record, *, expected):
                            actual(state_root, record, expected=expected)
                            if record.phase == "fenced":
                                raise StatePathError()

                        with mock.patch.object(separation, "publish_record", side_effect=stop_after_fencing):
                            stopped = self.run_setup()
                        self.assertFalse(stopped.ok)
                        self.assertEqual(read_record(self.new_root).phase, "fenced")
                        retained_before = file_snapshot(self.new_root / ".state-separation" / "source")
                        before = file_snapshot(self.install.project_root)
                        preview = self.run_setup(read_only=True)
                        self.assertTrue(preview.ok, (preview.error_code, preview.data))
                        self.assertEqual(file_snapshot(self.install.project_root), before)
                        with mock.patch.object(separation, "resolve_package_project_state",
                                               side_effect=AssertionError("retired source reopened")):
                            result = self.run_setup()
                        self.assertEqual(file_snapshot(self.new_root / ".state-separation" / "source"),
                                         retained_before)
                    else:
                        result = self.run_setup()
                    resolved = self.assert_success(result)
                    self.assertEqual(resolved.project_id, target.project.project_id)
                    with closing(connect_snapshot_readonly(resolved.target.db_path)) as connection:
                        titles = {row[0] for row in connection.execute("SELECT title FROM tasks")}
                    self.assertEqual(titles, {"Legacy older task", "Legacy selected task"})
                    after = file_snapshot(target.db_path.parent)
                    self.assertEqual({name: after[name] for name in source_before}, source_before)
                    self.assertEqual(rejected.read_bytes(), rejected_before)
                    retained = self.new_root / ".state-separation" / "source"
                    self.assertEqual((retained / "backups" / rejected.name).read_bytes(), rejected_before)
                    # The retained source intentionally is not a normalized
                    # active primary; candidate admission remains stricter.
                    source_as_active = separation.resolve_staged_project_state(
                        stage_root=retained, repo=self.install.project_root,
                        skill_root=self.install.skill_root,
                    )
                    self.assertEqual(source_as_active.error_code, "project_state_unreadable")

    def test_fenced_retry_rejects_changed_retained_snapshot_before_database_observation(self):
        for layout in ("fixed", "legacy"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as tmp:
                self.reset_install(tmp)
                self.older_selected_backup_source(layout)
                actual = separation.publish_record

                def stop_after_fencing(state_root, record, *, expected):
                    actual(state_root, record, expected=expected)
                    if record.phase == "fenced":
                        raise StatePathError()

                with mock.patch.object(separation, "publish_record", side_effect=stop_after_fencing):
                    self.assertFalse(self.run_setup().ok)
                self.assertEqual(read_record(self.new_root).phase, "fenced")
                database = self.new_root / ".state-separation" / "source" / "taskgov.sqlite"
                with database.open("ab") as stream:
                    stream.write(b"changed retained snapshot")
                before = file_snapshot(self.install.project_root)
                for read_only in (True, False):
                    with mock.patch.object(separation, "resolve_retained_source_snapshot") as reader:
                        result = self.run_setup(read_only=read_only)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.error_code, "setup_incomplete")
                    reader.assert_not_called()
                    self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_backup_only_source_observation_is_not_reselected_under_lock(self):
        for layout in ("fixed", "legacy"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as tmp:
                self.install = make_physical_install(Path(tmp).resolve(strict=True))
                self.new_root = self.install.project_root / ".taskgov"
                self.old_root = self.install.skill_root / "state"
                if layout == "fixed":
                    target = self.old_current()
                else:
                    project = project_identity(self.install.project_root)
                    target = DatabaseTarget(
                        project=project,
                        db_path=self.old_root / "projects" / project.project_id / "taskgov.sqlite",
                        explicit_db=True, skill_root=self.install.skill_root,
                    )
                    create_v12_target(target)
                publish_setup_backup(target, 3)
                target.db_path.unlink()
                backup = next((target.db_path.parent / "backups").glob("*.sqlite"))
                original = backup.read_bytes()
                actual = setup._revalidate_scope

                def change_observation(**kwargs):
                    scope = actual(**kwargs)
                    observed = backup.stat()
                    os.utime(backup, ns=(observed.st_atime_ns, observed.st_mtime_ns + 2_000_000_000))
                    return scope

                with mock.patch.object(setup, "_revalidate_scope", side_effect=change_observation):
                    result = self.run_setup()
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, "setup_restore_failed")
                self.assertEqual(result.data["completed_writes"], [])
                self.assertFalse((self.new_root / ".state-separation").exists())
                self.assertFalse((self.old_root / "current" / "taskgov.sqlite").exists())
                self.assertEqual(backup.read_bytes(), original)
                self.assert_success(self.run_setup())

    def test_partial_candidate_is_preserved_and_not_automatically_rebuilt(self):
        self.old_current()
        with mock.patch.object(setup, "_publish_evidence", side_effect=StatePathError()):
            stopped = self.run_setup()
        self.assertFalse(stopped.ok)
        record = read_record(self.new_root)
        self.assertIsNotNone(record.retained_digest)
        self.assertIsNone(record.candidate_digest)
        before = file_snapshot(self.install.project_root)
        preview = self.run_setup(read_only=True)
        self.assertFalse(preview.ok)
        self.assertEqual(preview.error_code, "setup_incomplete")
        self.assertEqual(file_snapshot(self.install.project_root), before)
        retried = self.run_setup()
        self.assertFalse(retried.ok)
        self.assertEqual(retried.error_code, "setup_incomplete")
        self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_partial_source_is_preserved_and_not_automatically_rebuilt(self):
        self.old_current()
        actual = separation.copy_database_snapshot
        def interrupted(**kwargs):
            actual(**kwargs)
            raise StatePathError()
        with mock.patch.object(separation, "copy_database_snapshot", side_effect=interrupted):
            stopped = self.run_setup()
        self.assertFalse(stopped.ok)
        self.assertIsNone(read_record(self.new_root).retained_digest)
        before = file_snapshot(self.install.project_root)
        retried = self.run_setup()
        self.assertFalse(retried.ok)
        self.assertEqual(retried.error_code, "setup_incomplete")
        self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_missing_barrier_repair_reports_current_state_without_historical_hash_check(self):
        result = self.run_setup()
        self.assert_success(result)
        (self.old_root / "current" / "taskgov.sqlite").unlink()
        before = file_snapshot(self.install.project_root)
        preview = self.run_setup(read_only=True)
        self.assertTrue(preview.ok, preview.error_code)
        self.assertEqual(preview.data["status"], "setup_preview")
        self.assertTrue(preview.data["maintenance_enabled"])
        self.assertEqual(preview.data["schema_from"], 22)
        self.assertEqual(file_snapshot(self.install.project_root), before)
        repaired = self.run_setup()
        self.assertTrue(repaired.ok, repaired.error_code)
        self.assertEqual(repaired.data["completed_writes"], ["state_layout_retire"])
        self.assertEqual(repaired.data["schema_from"], 22)

    def test_barrier_repair_preserves_recorded_source_and_new_root_recovery(self):
        for origin in ("fresh", "fixed", "legacy"):
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as tmp:
                self.reset_install(tmp)
                resolved, _ = self.prepare_barrier_repair(origin)
                old_before = file_snapshot(self.old_root)
                retained_before = file_snapshot(self.new_root / ".state-separation")
                before = file_snapshot(self.install.project_root)
                preview = self.run_setup(read_only=True)
                self.assertTrue(preview.ok, (preview.error_code, preview.data))
                self.assertEqual(file_snapshot(self.install.project_root), before)
                repaired = self.run_setup()
                self.assertTrue(repaired.ok, (repaired.error_code, repaired.data))
                self.assertEqual(repaired.data["completed_writes"], ["state_layout_retire"])
                after = file_snapshot(self.old_root)
                self.assertEqual({name: after[name] for name in old_before}, old_before)
                self.assertEqual(set(after) - set(old_before), {"current/taskgov.sqlite"})
                self.assertEqual(file_snapshot(self.new_root / ".state-separation"), retained_before)

                publish_setup_backup(resolved.target, 3)
                resolved.target.db_path.unlink()
                (self.old_root / "current" / "taskgov.sqlite").unlink()
                before = file_snapshot(self.install.project_root)
                preview = self.run_setup(read_only=True)
                self.assertTrue(preview.ok, (preview.error_code, preview.data))
                self.assertIn("database_restore", preview.data["planned_writes"])
                self.assertNotIn("database_initialize", preview.data["planned_writes"])
                self.assertEqual(file_snapshot(self.install.project_root), before)
                repaired = self.run_setup()
                self.assertTrue(repaired.ok, (repaired.error_code, repaired.data))
                self.assertIn("database_restore", repaired.data["completed_writes"])
                self.assertEqual(repaired.project_id, resolved.project_id)
                self.assertEqual(file_snapshot(self.new_root / ".state-separation"), retained_before)

    def test_barrier_repair_rejects_competing_legacy_state_without_writes(self):
        for origin in ("fresh", "fixed", "legacy"):
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as tmp:
                self.reset_install(tmp)
                _, target = self.prepare_barrier_repair(origin)
                if origin == "legacy":
                    competing = self.old_root / "projects" / "unexpected-project" / "taskgov.sqlite"
                    competing.parent.mkdir()
                    shutil.copyfile(target.db_path, competing)
                else:
                    self.old_legacy()
                before = file_snapshot(self.install.project_root)
                for read_only in (True, False):
                    result = self.run_setup(read_only=read_only)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.error_code, "project_state_unreadable")
                    self.assertEqual(result.data, {
                        "status": None, "planned_writes": [], "completed_writes": [],
                        "schema_from": None, "schema_to": 22,
                        "maintenance_enabled": None, "backup_interval_minutes": None,
                        "backup_generations": None, "evidence_status": None,
                        "viewer_status": None,
                        "relocation": {
                            "required": False, "source_layout": None, "identity_scheme": None,
                            "binding_generation": None, "confirmation_token": None,
                            "expires_at": None,
                        },
                    })
                    self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_barrier_repair_rejects_changed_or_unexplained_old_artifacts(self):
        for origin in ("fresh", "fixed", "legacy"):
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as tmp:
                self.reset_install(tmp)
                self.prepare_barrier_repair(origin)
                viewer = self.old_root / "current" / "viewer"
                viewer.mkdir(exist_ok=True)
                (viewer / "task-viewer.html").write_bytes(b"new unexplained projection")
                before = file_snapshot(self.install.project_root)
                for read_only in (True, False):
                    result = self.run_setup(read_only=read_only)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.error_code, "project_state_unreadable")
                    self.assertEqual(result.data["completed_writes"], [])
                    self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_barrier_repair_revalidates_old_source_under_transition_locks(self):
        for origin in ("fresh", "fixed", "legacy"):
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as tmp:
                self.reset_install(tmp)
                _, target = self.prepare_barrier_repair(origin)
                actual = setup._revalidate_scope
                before = file_snapshot(self.install.project_root)
                after_drift = None

                def add_competing_source(**kwargs):
                    nonlocal after_drift
                    scope = actual(**kwargs)
                    if origin == "legacy":
                        competing = self.old_root / "projects" / "unexpected-project" / "taskgov.sqlite"
                        competing.parent.mkdir()
                        shutil.copyfile(target.db_path, competing)
                    else:
                        self.old_legacy()
                    # Windows byte locks forbid rereading the two held lock
                    # files. Their prior bytes plus the injected subtree give
                    # the exact expected post-interference file snapshot.
                    after_drift = dict(before)
                    projects = self.old_root / "projects"
                    prefix = projects.relative_to(self.install.project_root).as_posix()
                    after_drift.update({f"{prefix}/{name}": digest
                                        for name, digest in file_snapshot(projects).items()})
                    return scope

                with mock.patch.object(setup, "_revalidate_scope", side_effect=add_competing_source):
                    result = self.run_setup()
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, "project_state_unreadable")
                self.assertEqual(result.data["completed_writes"], [])
                self.assertEqual(result.data["planned_writes"], ["state_layout_retire"])
                self.assertEqual(result.data["schema_from"], 22)
                self.assertIsNotNone(after_drift)
                self.assertEqual(file_snapshot(self.install.project_root), after_drift)

    def test_barrier_repair_rejects_changed_original_legacy_source(self):
        _, target = self.prepare_barrier_repair("legacy")
        with target.db_path.open("ab") as source:
            source.write(b"changed old source")
        before = file_snapshot(self.install.project_root)
        for read_only in (True, False):
            result = self.run_setup(read_only=read_only)
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "project_state_unreadable")
            self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_barrier_repair_preserves_unexplained_projects_or_stage_residue(self):
        for residue in ("projects", "stage"):
            with self.subTest(residue=residue), tempfile.TemporaryDirectory() as tmp:
                self.reset_install(tmp)
                self.prepare_barrier_repair("fresh")
                directory = self.old_root / ("projects" if residue == "projects"
                                             else ".current-stage-" + "f" * 32)
                directory.mkdir()
                (directory / "unexplained.txt").write_bytes(b"preserve uncertain material")
                before = file_snapshot(self.install.project_root)
                for read_only in (True, False):
                    result = self.run_setup(read_only=read_only)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.error_code, "project_state_unreadable"
                                     if residue == "projects" else "setup_incomplete")
                    self.assertEqual(result.data["completed_writes"], [])
                    self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_confirmed_relocation_sealed_and_fenced_retries_need_no_new_confirmation(self):
        for phase in ("sealed", "fenced"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                temporary_root = Path(temporary).resolve(strict=True)
                install = make_physical_install(temporary_root)
                original_install, original_new, original_old = self.install, self.new_root, self.old_root
                self.install = install
                self.new_root = install.project_root / ".taskgov"
                self.old_root = install.skill_root / "state"
                try:
                    previous = temporary_root / "previous"
                    previous.mkdir()
                    target = self.old_current(repo=previous)
                    preview = self.run_setup(read_only=True)
                    self.assertTrue(preview.ok, preview.error_code)
                    token = preview.data["relocation"]["confirmation_token"]
                    if phase == "sealed":
                        patcher = mock.patch.object(separation, "publish_retirement_marker", side_effect=StatePathError())
                    else:
                        actual = separation.publish_record
                        def interrupt(state_root, record, *, expected):
                            if record.phase == "fenced":
                                raise StatePathError()
                            actual(state_root, record, expected=expected)
                        patcher = mock.patch.object(separation, "publish_record", side_effect=interrupt)
                    with patcher:
                        stopped = self.run_setup(confirmation_token=token)
                    self.assertFalse(stopped.ok)
                    self.assertIsNotNone(read_record(self.new_root).candidate_digest)
                    resolved = self.assert_success(self.run_setup())
                    self.assertEqual(resolved.project_id, target.project.project_id)
                    self.assertEqual(resolved.stored_project.binding_generation, 2)
                finally:
                    self.install, self.new_root, self.old_root = original_install, original_new, original_old

    def runner_source(self, *, terminal):
        from tests.test_m242_runner_service import RunnerServiceFixture, service

        root = self.temporary_root / "runner-fixture"
        root.mkdir()
        fixture = RunnerServiceFixture(root)
        skill_parent = fixture.repo / ".agents" / "skills"
        skill_parent.mkdir(parents=True)
        skill = _copy_skill(skill_parent)
        (fixture.repo / ".gitignore").write_text("/.taskgov/\n", encoding="utf-8")
        self.install = PhysicalInstall(project_root=fixture.repo, skill_root=skill)
        self.new_root = fixture.repo / ".taskgov"
        self.old_root = skill / "state"
        database = self.old_root / "current" / "taskgov.sqlite"
        database.parent.mkdir(parents=True)
        shutil.copyfile(fixture.db, database)
        fixture.db = database
        fixture.target = replace(
            fixture.target, db_path=database, skill_root=skill,
            backups_path=None, viewer_path=None, evidence_root=None,
            evidence_index=None, evidence_bundles=None, evidence_lock=None,
            verification_runner_root=None,
        )
        prepared = fixture.prepared()
        paths = service._runner_paths(fixture.target)
        with service.zero_wait_runner_lock(paths):
            intent = service._persist_launch_intent(fixture.target, prepared)
            if terminal:
                with mock.patch.object(service, "_physical_basis_matches", return_value=True):
                    service._complete_prelaunch(fixture.target, paths, prepared, intent,
                                               reason="runtime_unavailable")
        return fixture, paths, intent

    def test_pending_runner_intent_without_tree_is_refused_without_writes(self):
        fixture, _, _ = self.runner_source(terminal=False)
        before = file_snapshot(self.install.project_root)
        result = self.run_setup()
        self.assertFalse(result.ok)
        self.assertEqual(file_snapshot(self.install.project_root), before)
        self.assertEqual(fixture.generation(1)["state"], "pending")

    def test_terminal_runner_rows_and_empty_layout_survive(self):
        fixture, _, _ = self.runner_source(terminal=True)
        before = fixture.generation(1)
        self.assert_success(self.run_setup())
        fixture.db = self.new_root / "current" / "taskgov.sqlite"
        fixture.target = replace(fixture.target, db_path=fixture.db)
        self.assertEqual(fixture.generation(1), before)

    def test_terminal_runner_with_leftover_tree_is_preserved_and_refused(self):
        fixture, paths, intent = self.runner_source(terminal=True)
        from task_governance_tool.verification_runner_lifecycle import create_attempt_directories

        create_attempt_directories(paths, intent.attempt.verification_runner_attempt_id)
        before = file_snapshot(self.install.project_root)
        result = self.run_setup()
        self.assertFalse(result.ok)
        self.assertEqual(file_snapshot(self.install.project_root), before)
        self.assertEqual(fixture.generation(1)["state"], "terminal")

    def test_barrier_repair_reuses_one_ignore_preflight_and_preserves_options(self):
        from task_governance_tool import project_scope

        self.assert_success(self.run_setup())
        (self.old_root / "current" / "taskgov.sqlite").unlink()
        with mock.patch.object(project_scope, "_state_is_ignored", wraps=project_scope._state_is_ignored) as check:
            result = self.run_setup(backup_interval_minutes=45, backup_generations=4)
        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(check.call_count, 1)
        self.assertEqual(result.data["status"], "setup_complete")
        self.assertEqual(result.data["backup_interval_minutes"], 45)
        self.assertEqual(result.data["backup_generations"], 4)
        self.assertEqual(result.data["completed_writes"], ["state_layout_retire", "maintenance_configure"])

    def test_invalid_token_cannot_repair_barrier(self):
        self.assert_success(self.run_setup())
        (self.old_root / "current" / "taskgov.sqlite").unlink()
        before = file_snapshot(self.install.project_root)
        result = self.run_setup(confirmation_token="invalid")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "relocation_token_invalid")
        self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_invalid_token_cannot_activate_a_sealed_fresh_candidate(self):
        with mock.patch.object(separation, "publish_retirement_marker", side_effect=StatePathError()):
            stopped = self.run_setup()
        self.assertFalse(stopped.ok)
        self.assertIsNotNone(read_record(self.new_root).candidate_digest)
        before = file_snapshot(self.install.project_root)
        result = self.run_setup(confirmation_token="invalid")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "relocation_token_invalid")
        self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_malformed_old_stage_precedes_relocation_and_preserves_bytes(self):
        previous = self.temporary_root / "previous"
        previous.mkdir()
        self.old_current(repo=previous)
        residue = self.old_root / (".current-stage-" + "e" * 32)
        residue.mkdir()
        (residue / "sentinel.txt").write_bytes(b"not owned")
        before = file_snapshot(self.install.project_root)
        for read_only in (True, False):
            with self.subTest(read_only=read_only):
                result = self.run_setup(read_only=read_only)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, "setup_incomplete")
                self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_barrier_repair_with_new_root_backup_only_never_plans_initialization(self):
        resolved = self.assert_success(self.run_setup())
        publish_setup_backup(resolved.target, 3)
        resolved.target.db_path.unlink()
        (self.old_root / "current" / "taskgov.sqlite").unlink()
        before = file_snapshot(self.install.project_root)
        preview = self.run_setup(read_only=True)
        self.assertTrue(preview.ok, (preview.error_code, preview.data))
        self.assertIn("database_restore", preview.data["planned_writes"])
        self.assertNotIn("database_initialize", preview.data["planned_writes"])
        self.assertEqual(file_snapshot(self.install.project_root), before)
        repaired = self.run_setup()
        self.assertTrue(repaired.ok, (repaired.error_code, repaired.data))
        self.assertEqual(repaired.project_id, resolved.project_id)
        self.assertIn("state_layout_retire", repaired.data["completed_writes"])
        self.assertIn("database_restore", repaired.data["completed_writes"])
        self.assertNotIn("database_initialize", repaired.data["completed_writes"])

    def test_moved_active_state_cannot_repair_barrier_without_confirmation(self):
        self.assert_success(self.run_setup())
        moved = self.temporary_root / "moved"
        shutil.copytree(self.install.project_root, moved)
        self.install = PhysicalInstall(project_root=moved, skill_root=moved / ".agents" / "skills" / "task-governance-tool")
        self.new_root = moved / ".taskgov"
        self.old_root = self.install.skill_root / "state"
        (self.old_root / "current" / "taskgov.sqlite").unlink()
        before = file_snapshot(moved)
        result = self.run_setup()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "project_relocation_required")
        self.assertEqual(file_snapshot(moved), before)

    def test_lock_held_source_policy_is_replanned(self):
        from task_governance_tool.backup_metadata_repository import configure_project_maintenance

        target = self.old_current()
        actual = setup._revalidate_scope
        def drift(**kwargs):
            configure_project_maintenance(target, requested_interval_minutes=60, requested_generations=5)
            return actual(**kwargs)
        with mock.patch.object(setup, "_revalidate_scope", side_effect=drift):
            result = self.run_setup()
        self.assert_success(result)
        self.assertEqual(result.data["backup_interval_minutes"], 60)
        self.assertEqual(result.data["backup_generations"], 5)

    def test_pending_legacy_cleanup_is_preserved_and_refused(self):
        from task_governance_tool.project_binding_repository import set_legacy_cleanup_pending
        from task_governance_tool.state_transition import build_cleanup_inventory, CleanupInventoryEntry

        target = self.old_current()
        content = target.db_path.read_bytes()
        inventory = build_cleanup_inventory((CleanupInventoryEntry(
            name="taskgov.sqlite", size=len(content), sha256=hashlib.sha256(content).hexdigest(),
        ),))
        set_legacy_cleanup_pending(
            target, project_id=target.project.project_id, expected_identity_scheme="uuid_v1",
            expected_generation=1, expected_path_hash=target.project.canonical_path_hash,
            inventory=inventory.text, fingerprint=inventory.fingerprint,
        )
        before = file_snapshot(self.install.project_root)
        for read_only in (True, False):
            result = self.run_setup(read_only=read_only)
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_incomplete")
            self.assertEqual(result.data["completed_writes"], [])
            self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_owned_predecessor_stage_is_preserved_for_compatible_old_setup(self):
        from task_governance_tool.state_transition import (
            CleanupInventoryEntry, build_cleanup_inventory, create_owned_stage,
        )

        for owner_only in (True, False):
            with self.subTest(owner_only=owner_only), tempfile.TemporaryDirectory() as tmp:
                self.reset_install(tmp)
                target = self.old_legacy()
                content = target.db_path.read_bytes()
                inventory = build_cleanup_inventory((CleanupInventoryEntry(
                    name="taskgov.sqlite", size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                ),))
                stage = create_owned_stage(
                    self.old_root, project_id=target.project.project_id,
                    inventory_fingerprint=inventory.fingerprint,
                )
                if owner_only:
                    stage.stage_directory.path.rmdir()
                else:
                    shutil.copyfile(target.db_path, stage.stage_directory.path / "taskgov.sqlite")
                before = file_snapshot(self.install.project_root)
                for read_only in (True, False):
                    result = self.run_setup(read_only=read_only)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.error_code, "setup_incomplete")
                    self.assertEqual(result.data["completed_writes"], [])
                    self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_retired_fixed_artifact_limit_comes_from_sealed_inventory(self):
        from task_governance_tool.setup_state_inventory import inspect_inventory, inspect_retired_source
        from task_governance_tool.state_separation import SeparationRecord

        retained = self.temporary_root / "retained"
        old = self.temporary_root / "old"
        (retained / "viewer").mkdir(parents=True)
        (old / "current" / "viewer").mkdir(parents=True)
        (retained / "taskgov.sqlite").write_bytes(b"physical inventory only")
        artifact = b"x" * (16_777_216 + 1)
        (retained / "viewer" / "task-viewer.html").write_bytes(artifact)
        (old / "current" / "viewer" / "task-viewer.html").write_bytes(artifact)
        inventory = inspect_inventory(retained, strict=True)
        record = SeparationRecord(
            1, "a" * 32, "activated", "tg_project_" + "b" * 32,
            "fixed_current_v1", 22, 1, "c" * 64, "d" * 64,
            inventory.digest, "e" * 64,
        )
        self.assertEqual(inspect_retired_source(old, retained, record), old / "current")

    def test_domain_inventory_does_not_use_the_legacy_32_file_stage_cap(self):
        from task_governance_tool.setup_state_inventory import inspect_inventory, copy_inventory

        root = self.temporary_root / "inventory"
        root.mkdir()
        (root / "taskgov.sqlite").write_bytes(b"inventory is physical, not a SQLite validator")
        bundles = root / "evidence" / "bundles"
        bundles.mkdir(parents=True)
        for number in range(40):
            (bundles / f"tg_completion_evidence_bundle_{number:016x}.json").write_bytes(b"{}")
        observed = inspect_inventory(root, strict=True)
        self.assertEqual(len(observed.files), 41)
        destination = self.temporary_root / "inventory-copy"
        destination.mkdir()
        copy_inventory(observed, destination, skip_database=False)
        self.assertEqual(inspect_inventory(destination, strict=True).digest, observed.digest)


if __name__ == "__main__":
    unittest.main()
