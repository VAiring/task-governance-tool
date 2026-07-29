from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
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


if __name__ == "__main__":
    unittest.main()
