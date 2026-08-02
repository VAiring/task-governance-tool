from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

from tests.m14_test_support import PhysicalInstall, make_physical_install

from task_governance_tool import backup as backup_service
from task_governance_tool import setup as setup_service
from task_governance_tool.state_resolver import (
    resolve_project_state,
    resolve_setup_project_state,
)
from task_governance_tool.storage import (
    DatabaseTarget,
    MigrationBackupMetadata,
    ProjectIdentity,
    compare_and_swap_project_binding,
    project_identity,
    read_managed_backup_repository,
)


FIRST_BACKUP_TIME = "2026-07-30T00:00:00Z"
SECOND_BACKUP_TIME = "2026-07-31T00:00:00Z"
DIVERGENT_BINDING_TIME = "2026-08-01T00:00:00Z"
RELOCATION_ISSUED_AT = "2026-07-29T01:02:03Z"
RELOCATION_CONFIRMED_AT = "2026-07-29T01:02:04Z"


def _setup_current(root: Path):
    install = make_physical_install(root)
    result = setup_service.run_setup(
        repo=str(install.project_root),
        repo_explicit=True,
        script_path=install.entrypoint,
        read_only=False,
        backup_interval_minutes=None,
        backup_generations=None,
    )
    if not result.ok:
        raise AssertionError(result)
    resolution = resolve_project_state(
        skill_root=install.skill_root,
        repo=install.project_root,
    )
    if resolution.target is None or resolution.stored_project is None:
        raise AssertionError(resolution)
    return (
        install,
        resolution.target,
        resolution.stored_project.identity_scheme,
    )


def _backup_snapshot(directory: Path) -> dict[str, bytes]:
    if not directory.exists():
        return {}
    return {
        path.name: path.read_bytes()
        for path in sorted(
            directory.glob("taskgov-backup-v1_*.sqlite"),
            key=lambda item: item.name,
        )
        if path.is_file()
    }


def _relocate_install(
    install: PhysicalInstall,
    destination: Path,
) -> PhysicalInstall:
    install.project_root.rename(destination)
    return PhysicalInstall(
        project_root=destination,
        skill_root=(
            destination / ".agents" / "skills" / "task-governance-tool"
        ),
    )


def _run_setup(
    install: PhysicalInstall,
    *,
    read_only: bool,
    now: str,
    confirmation_token: str | None = None,
):
    with mock.patch.object(setup_service, "utc_now", return_value=now):
        return setup_service.run_setup(
            repo=str(install.project_root),
            repo_explicit=True,
            script_path=install.entrypoint,
            read_only=read_only,
            backup_interval_minutes=None,
            backup_generations=None,
            confirmation_token=confirmation_token,
        )


def _rebind_artifact(
    source: DatabaseTarget,
    artifact: Path,
    *,
    destination: Path,
    identity_scheme: str,
    token_digit: str,
) -> None:
    observed = project_identity(destination)
    artifact_target = DatabaseTarget(
        project=ProjectIdentity(
            project_id=source.project.project_id,
            canonical_repo=observed.canonical_repo,
            canonical_path_hash=observed.canonical_path_hash,
            display_name=observed.display_name,
        ),
        db_path=artifact,
        explicit_db=True,
        binding_path_hash=source.binding_path_hash,
        binding_generation=source.binding_generation,
    )
    compare_and_swap_project_binding(
        artifact_target,
        project_id=source.project.project_id,
        identity_scheme=identity_scheme,
        expected_generation=source.binding_generation,
        expected_old_hash=source.binding_path_hash,
        new_hash=artifact_target.project.canonical_path_hash,
        new_display_name=artifact_target.project.display_name,
        reason="confirmed_relocation",
        confirmation_token_digest=token_digit * 64,
        bound_at=DIVERGENT_BINDING_TIME,
    )


class M17RecoveryHardeningTests(unittest.TestCase):
    def test_fixed_rebind_recovery_window_closes_after_current_head_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install, target, _ = _setup_current(root)
            first = backup_service.run_routine_backup(
                target,
                observed_at=FIRST_BACKUP_TIME,
            )
            self.assertEqual((first.code, first.attempted), ("succeeded", True))

            moved = _relocate_install(
                install,
                root / "moved-project",
            )
            preview = _run_setup(
                moved,
                read_only=True,
                now=RELOCATION_ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            confirmed = _run_setup(
                moved,
                read_only=False,
                now=RELOCATION_CONFIRMED_AT,
                confirmation_token=token,
            )
            self.assertTrue(confirmed.ok, confirmed)
            rebound = resolve_project_state(
                skill_root=moved.skill_root,
                repo=moved.project_root,
            )
            self.assertEqual(rebound.binding, "matching")
            self.assertEqual(rebound.target.binding_generation, 2)
            current_target = rebound.target
            current_primary = current_target.db_path.read_bytes()

            current_target.db_path.unlink()
            recovery_gap = resolve_setup_project_state(
                skill_root=moved.skill_root,
                repo=moved.project_root,
            )
            self.assertEqual(
                recovery_gap.error_code,
                "project_state_unreadable",
            )
            self.assertIsNone(recovery_gap.fixed_recovery)

            current_target.db_path.write_bytes(current_primary)
            current = resolve_project_state(
                skill_root=moved.skill_root,
                repo=moved.project_root,
            )
            self.assertEqual(current.binding, "matching")
            second = backup_service.run_routine_backup(
                current.target,
                observed_at=SECOND_BACKUP_TIME,
            )
            self.assertEqual(
                (second.code, second.attempted),
                ("succeeded", True),
            )
            current.target.db_path.unlink()
            recoverable = resolve_setup_project_state(
                skill_root=moved.skill_root,
                repo=moved.project_root,
            )
            self.assertIsNone(recoverable.error_code)
            self.assertIsNotNone(recoverable.fixed_recovery)
            self.assertEqual(
                recoverable.fixed_recovery.selected.stored_project.binding_generation,
                2,
            )

            restored = _run_setup(
                moved,
                read_only=False,
                now=DIVERGENT_BINDING_TIME,
            )
            self.assertTrue(restored.ok, restored)
            final = resolve_project_state(
                skill_root=moved.skill_root,
                repo=moved.project_root,
            )
            self.assertEqual(final.binding, "matching")
            self.assertEqual(final.target.binding_generation, 2)

    def test_routine_backup_rejects_divergent_file_only_lineage_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, identity_scheme = _setup_current(Path(tmp))
            metadata = MigrationBackupMetadata(
                generation_id=f"tg_backup_{1:032x}",
                published_at=FIRST_BACKUP_TIME,
                publication_retention=3,
            )
            backup_service._copy(target, metadata)
            artifacts = backup_service._discover(target)
            self.assertEqual(len(artifacts), 1)
            _rebind_artifact(
                target,
                artifacts[0].path,
                destination=install.project_root.parent / "divergent-routine",
                identity_scheme=identity_scheme,
                token_digit="1",
            )

            backup_root = target.resolved_backups_path
            before_files = _backup_snapshot(backup_root)
            before_repository = read_managed_backup_repository(target)
            real_reconcile = backup_service._reconcile_v11

            with mock.patch.object(
                backup_service,
                "_reconcile_v11",
                wraps=real_reconcile,
            ) as reconcile:
                result = backup_service.run_routine_backup(
                    target,
                    observed_at=SECOND_BACKUP_TIME,
                )

            self.assertEqual((result.code, result.attempted), ("failed", True))
            reconcile.assert_not_called()
            self.assertEqual(_backup_snapshot(backup_root), before_files)
            after_repository = read_managed_backup_repository(target)
            self.assertEqual(
                after_repository.generations,
                before_repository.generations,
            )
            self.assertEqual(
                (
                    after_repository.maintenance.latest_backup_generation_id,
                    after_repository.maintenance.backup_last_success_at,
                    after_repository.maintenance.applied_backup_generations,
                ),
                (
                    before_repository.maintenance.latest_backup_generation_id,
                    before_repository.maintenance.backup_last_success_at,
                    before_repository.maintenance.applied_backup_generations,
                ),
            )
            self.assertEqual(
                list(backup_root.glob(".taskgov-backup-*.tmp")),
                [],
            )

    def test_missing_primary_revalidates_all_backups_under_lock_before_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, identity_scheme = _setup_current(Path(tmp))
            for observed_at in (FIRST_BACKUP_TIME, SECOND_BACKUP_TIME):
                published = backup_service.run_routine_backup(
                    target,
                    observed_at=observed_at,
                )
                self.assertEqual(
                    (published.code, published.attempted),
                    ("succeeded", True),
                )
            artifacts = backup_service._discover(target)
            self.assertEqual(len(artifacts), 2)
            older = artifacts[0].path
            target.db_path.unlink()

            real_lock = setup_service.managed_backup_lock
            real_restore = setup_service.restore_managed_backup
            backup_root = target.resolved_backups_path
            locked_files: dict[str, bytes] | None = None
            changed = False

            @contextmanager
            def change_nonselected_backup_after_lock(lock_target):
                nonlocal changed, locked_files
                with real_lock(lock_target) as lock_bytes:
                    if not changed:
                        _rebind_artifact(
                            target,
                            older,
                            destination=(
                                install.project_root.parent
                                / "divergent-recovery"
                            ),
                            identity_scheme=identity_scheme,
                            token_digit="2",
                        )
                        locked_files = _backup_snapshot(backup_root)
                        changed = True
                    yield lock_bytes

            with (
                mock.patch.object(
                    setup_service,
                    "managed_backup_lock",
                    change_nonselected_backup_after_lock,
                ),
                mock.patch.object(
                    setup_service,
                    "restore_managed_backup",
                    wraps=real_restore,
                ) as restore,
            ):
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertTrue(changed)
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_restore_failed")
            restore.assert_not_called()
            self.assertFalse(target.db_path.exists())
            self.assertIsNotNone(locked_files)
            self.assertEqual(_backup_snapshot(backup_root), locked_files)
            self.assertEqual(
                list(target.db_path.parent.glob(".taskgov-restore-*.tmp")),
                [],
            )

    def test_recovery_content_classification_drift_under_lock_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "Classification race task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            for observed_at in (
                "2098-01-01T00:00:00Z",
                "2099-01-01T00:00:00Z",
            ):
                with (
                    mock.patch.object(
                        backup_service,
                        "utc_now",
                        return_value=observed_at,
                    ),
                    backup_service.managed_backup_lock(target),
                ):
                    backup_service.publish_setup_backup(target, 3)
            artifacts = backup_service._discover(target)
            self.assertGreaterEqual(len(artifacts), 2)
            newest = artifacts[-1].path
            target.db_path.unlink()

            real_lock = setup_service.managed_backup_lock
            real_restore = setup_service.restore_managed_backup
            backup_root = target.resolved_backups_path
            locked_files: dict[str, bytes] | None = None
            changed = False

            @contextmanager
            def invalidate_newest_after_lock(lock_target):
                nonlocal changed, locked_files
                with real_lock(lock_target) as lock_bytes:
                    if not changed:
                        with closing(sqlite3.connect(newest)) as connection:
                            cursor = connection.execute(
                                "UPDATE tasks SET verification = ? WHERE title = ?",
                                ("x" * 501, "Classification race task"),
                            )
                            self.assertEqual(cursor.rowcount, 1)
                            connection.commit()
                        locked_files = _backup_snapshot(backup_root)
                        changed = True
                    yield lock_bytes

            with (
                mock.patch.object(
                    setup_service,
                    "managed_backup_lock",
                    invalidate_newest_after_lock,
                ),
                mock.patch.object(
                    setup_service,
                    "restore_managed_backup",
                    wraps=real_restore,
                ) as restore,
            ):
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertTrue(changed)
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_restore_failed")
            restore.assert_not_called()
            self.assertFalse(target.db_path.exists())
            self.assertIsNotNone(locked_files)
            self.assertEqual(_backup_snapshot(backup_root), locked_files)
            self.assertEqual(
                list(target.db_path.parent.glob(".taskgov-restore-*.tmp")),
                [],
            )

    def test_restore_rejects_nonselected_classification_drift_after_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "Post-selection race task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            for observed_at in (
                "2098-02-01T00:00:00Z",
                "2099-02-01T00:00:00Z",
            ):
                with (
                    mock.patch.object(
                        backup_service,
                        "utc_now",
                        return_value=observed_at,
                    ),
                    backup_service.managed_backup_lock(target),
                ):
                    backup_service.publish_setup_backup(target, 3)
            artifacts = backup_service._discover(target)
            self.assertGreaterEqual(len(artifacts), 2)
            older = artifacts[-2].path
            target.db_path.unlink()

            real_restore = setup_service.restore_managed_backup
            backup_root = target.resolved_backups_path
            changed_files: dict[str, bytes] | None = None

            def drift_after_selection(
                restore_target,
                candidate,
                *,
                expected_recovery,
            ):
                nonlocal changed_files
                before = older.stat()
                with closing(sqlite3.connect(older)) as connection:
                    cursor = connection.execute(
                        "UPDATE tasks SET verification = ? WHERE title = ?",
                        ("x" * 501, "Post-selection race task"),
                    )
                    self.assertEqual(cursor.rowcount, 1)
                    connection.commit()
                changed = older.stat()
                self.assertEqual(changed.st_size, before.st_size)
                os.utime(
                    older,
                    ns=(changed.st_atime_ns, before.st_mtime_ns),
                )
                after = older.stat()
                self.assertEqual(
                    (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ),
                    (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                    ),
                )
                changed_files = _backup_snapshot(backup_root)
                return real_restore(
                    restore_target,
                    candidate,
                    expected_recovery=expected_recovery,
                )

            with mock.patch.object(
                setup_service,
                "restore_managed_backup",
                side_effect=drift_after_selection,
            ) as restore:
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_restore_failed")
            self.assertEqual(result.data["completed_writes"], [])
            restore.assert_called_once()
            self.assertFalse(target.db_path.exists())
            self.assertIsNotNone(changed_files)
            self.assertEqual(_backup_snapshot(backup_root), changed_files)
            self.assertEqual(
                list(target.db_path.parent.glob(".taskgov-restore-*.tmp")),
                [],
            )

    def test_restore_rejects_inventory_removal_after_repository_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            for observed_at in (
                "2098-03-01T00:00:00Z",
                "2099-03-01T00:00:00Z",
            ):
                with (
                    mock.patch.object(
                        backup_service,
                        "utc_now",
                        return_value=observed_at,
                    ),
                    backup_service.managed_backup_lock(target),
                ):
                    backup_service.publish_setup_backup(target, 3)
            artifacts = backup_service._discover(target)
            self.assertGreaterEqual(len(artifacts), 2)
            older = artifacts[-2].path
            target.db_path.unlink()

            real_prepare = backup_service._prepare_recovered_repository
            backup_root = target.resolved_backups_path
            changed_files: dict[str, bytes] | None = None

            def remove_after_prepare(
                temporary_target,
                candidate,
                *,
                expected_snapshot,
            ):
                nonlocal changed_files
                real_prepare(
                    temporary_target,
                    candidate,
                    expected_snapshot=expected_snapshot,
                )
                older.unlink()
                changed_files = _backup_snapshot(backup_root)

            with mock.patch.object(
                backup_service,
                "_prepare_recovered_repository",
                side_effect=remove_after_prepare,
            ) as prepare:
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_restore_failed")
            self.assertEqual(result.data["completed_writes"], [])
            prepare.assert_called_once()
            self.assertFalse(target.db_path.exists())
            self.assertIsNotNone(changed_files)
            self.assertEqual(_backup_snapshot(backup_root), changed_files)
            self.assertEqual(
                list(target.db_path.parent.glob(".taskgov-restore-*.tmp")),
                [],
            )

    def test_local_rejection_never_hides_generation_structure_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "Mixed corruption task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            for observed_at in (
                "2097-01-01T00:00:00Z",
                "2098-01-01T00:00:00Z",
                "2099-01-01T00:00:00Z",
            ):
                with (
                    mock.patch.object(
                        backup_service,
                        "utc_now",
                        return_value=observed_at,
                    ),
                    backup_service.managed_backup_lock(target),
                ):
                    backup_service.publish_setup_backup(target, 4)
            artifacts = backup_service._discover(target)
            self.assertGreaterEqual(len(artifacts), 3)
            rejected = artifacts[-2]
            structural_head = artifacts[-1]
            with closing(sqlite3.connect(rejected.path)) as connection:
                connection.execute(
                    "UPDATE tasks SET verification = ?",
                    ("x" * 501,),
                )
                connection.commit()
            with closing(sqlite3.connect(structural_head.path)) as connection:
                cursor = connection.execute(
                    "DELETE FROM managed_backup_generations WHERE generation_id = ?",
                    (rejected.metadata.generation_id,),
                )
                self.assertEqual(cursor.rowcount, 1)
                connection.commit()
            target.db_path.unlink()

            with mock.patch.object(
                setup_service,
                "restore_managed_backup",
            ) as restore:
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "project_state_unreadable")
            restore.assert_not_called()
            self.assertFalse(target.db_path.exists())
            self.assertEqual(
                list(target.db_path.parent.glob(".taskgov-restore-*.tmp")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
