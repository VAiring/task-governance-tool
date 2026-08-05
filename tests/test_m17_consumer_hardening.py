from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    create_v10_database,
    create_v11_database,
    create_v12_database,
    create_v12_target,
    create_v14_target,
    create_v9_database,
    file_snapshot,
    make_physical_install,
)

from task_governance_tool import backup as backup_service
from task_governance_tool import doctor as doctor_service
from task_governance_tool import project_scope as project_scope_service
from task_governance_tool import setup as setup_service
from task_governance_tool import state_resolver as resolver_service
from task_governance_tool import storage as storage_service
from task_governance_tool import viewer as viewer_writer
from task_governance_tool import viewer_maintenance as viewer_service
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.storage import (
    DatabaseTarget,
    ProjectIdentity,
    StorageError,
    begin_initialized_write,
    compare_and_swap_project_binding,
    connect,
    connect_initialized,
    apply_viewer_maintenance_migration,
    project_identity,
)


FIRST_REBIND_TIME = "2026-07-29T00:00:00Z"
SECOND_REBIND_TIME = "2026-07-29T00:00:01Z"


def _setup(root: Path):
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
    return install, resolution.target, resolution.stored_project.identity_scheme


def _destination_target(
    source: DatabaseTarget,
    destination: Path,
    *,
    binding_path_hash: str,
    binding_generation: int,
) -> DatabaseTarget:
    observed = project_identity(destination)
    return DatabaseTarget(
        project=ProjectIdentity(
            project_id=source.project.project_id,
            canonical_repo=observed.canonical_repo,
            canonical_path_hash=observed.canonical_path_hash,
            display_name=observed.display_name,
        ),
        db_path=source.db_path,
        explicit_db=True,
        binding_path_hash=binding_path_hash,
        binding_generation=binding_generation,
    )


def _rebind(
    source: DatabaseTarget,
    destination: DatabaseTarget,
    *,
    identity_scheme: str,
    token_digest: str,
    bound_at: str,
):
    return compare_and_swap_project_binding(
        destination,
        project_id=source.project.project_id,
        identity_scheme=identity_scheme,
        expected_generation=source.binding_generation,
        expected_old_hash=source.binding_path_hash,
        new_hash=destination.project.canonical_path_hash,
        new_display_name=destination.project.display_name,
        reason="confirmed_relocation",
        confirmation_token_digest=token_digest,
        bound_at=bound_at,
    )


class M17ConsumerHardeningTests(unittest.TestCase):
    def assert_public_state_is_unreadable_without_write(self, install) -> None:
        before = file_snapshot(install.project_root)
        leaf = install.run("task", "list", "--json")
        self.assertEqual(leaf.returncode, 2, leaf.stderr)
        self.assertEqual(
            json.loads(leaf.stdout)["errors"][0]["code"],
            "project_state_unreadable",
        )

        doctor = doctor_service.run_doctor(
            repo=str(install.project_root),
            repo_explicit=True,
            script_path=install.entrypoint,
        )
        self.assertFalse(doctor.ok)
        self.assertEqual(
            doctor.data["components"]["project_state"]["code"],
            "unreadable",
        )
        self.assertEqual(
            [error["code"] for error in doctor.errors],
            ["project_state_unreadable"],
        )

        setup = setup_service.run_setup(
            repo=str(install.project_root),
            repo_explicit=True,
            script_path=install.entrypoint,
            read_only=True,
            backup_interval_minutes=None,
            backup_generations=None,
        )
        self.assertFalse(setup.ok)
        self.assertEqual(setup.error_code, "project_state_unreadable")
        self.assertEqual(setup.data["planned_writes"], [])
        self.assertEqual(setup.data["completed_writes"], [])
        self.assertEqual(file_snapshot(install.project_root), before)

        setup_write = setup_service.run_setup(
            repo=str(install.project_root),
            repo_explicit=True,
            script_path=install.entrypoint,
            read_only=False,
            backup_interval_minutes=None,
            backup_generations=None,
        )
        self.assertFalse(setup_write.ok)
        self.assertEqual(
            setup_write.error_code,
            "project_state_unreadable",
        )
        self.assertEqual(setup_write.data["planned_writes"], [])
        self.assertEqual(setup_write.data["completed_writes"], [])
        self.assertEqual(file_snapshot(install.project_root), before)

    def assert_missing_primary_sidecar_rejected(
        self,
        *,
        legacy: bool,
        suffix: str,
        with_backup: bool,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            if legacy:
                target = install.legacy_target
                create_v14_target(target)
                database = install.legacy_db_path
                if with_backup:
                    with backup_service.managed_backup_lock(target):
                        backup_service.publish_setup_backup(target, 3)
            else:
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )
                self.assertTrue(result.ok, result)
                resolution = resolve_project_state(
                    skill_root=install.skill_root,
                    repo=install.project_root,
                )
                target = resolution.target
                self.assertIsNotNone(target)
                assert target is not None
                database = install.db_path
                if with_backup:
                    backup = backup_service.run_routine_backup(
                        target,
                        observed_at=FIRST_REBIND_TIME,
                    )
                    self.assertEqual(backup.code, "succeeded")

            database.unlink()
            sidecar = Path(f"{database}{suffix}")
            sidecar.write_bytes(b"preserve orphan WAL state")
            before = file_snapshot(install.project_root)

            leaf = install.run("task", "list", "--json")
            self.assertEqual(leaf.returncode, 2, leaf.stderr)
            self.assertEqual(
                json.loads(leaf.stdout)["errors"][0]["code"],
                "unsupported_journal_mode",
            )

            doctor = doctor_service.run_doctor(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
            )
            self.assertFalse(doctor.ok)
            self.assertEqual(
                doctor.data["components"]["project_state"]["code"],
                "unsupported_journal",
            )
            self.assertEqual(
                [error["code"] for error in doctor.errors],
                ["unsupported_journal_mode"],
            )

            setup = setup_service.run_setup(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
                read_only=True,
                backup_interval_minutes=None,
                backup_generations=None,
            )
            self.assertFalse(setup.ok)
            self.assertEqual(setup.error_code, "unsupported_journal_mode")
            self.assertEqual(setup.data["planned_writes"], [])
            self.assertEqual(setup.data["completed_writes"], [])
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_ready_doctor_uses_one_resolver_read_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, _, _ = _setup(Path(tmp))
            before = file_snapshot(install.project_root)
            real_connect = resolver_service.connect_readonly

            with mock.patch.object(
                resolver_service,
                "connect_readonly",
                wraps=real_connect,
            ) as connect:
                result = doctor_service.run_doctor(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                )

            self.assertTrue(result.ok, result)
            self.assertEqual(connect.call_count, 1)
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_doctor_maps_incomplete_current_schema_to_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, _, _ = _setup(Path(tmp))
            with closing(connect(install.db_path)) as connection:
                connection.execute("DROP TABLE task_checkpoints")
                connection.commit()
            before = file_snapshot(install.project_root)

            result = doctor_service.run_doctor(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
            )

            self.assertFalse(result.ok, result)
            self.assertEqual(
                result.data["components"]["project_state"],
                {
                    "code": "unreadable",
                    "schema_version": None,
                    "required_schema_version": 19,
                },
            )
            self.assertEqual(
                [error["code"] for error in result.errors],
                ["project_state_unreadable"],
            )
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_missing_latest_history_row_cannot_masquerade_as_v13(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v12_database(install, enabled=True)
            migrated = setup_service.run_setup(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
                read_only=False,
                backup_interval_minutes=None,
                backup_generations=None,
            )
            self.assertTrue(migrated.ok, migrated)
            with closing(connect(install.db_path)) as connection:
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = 14"
                )
                connection.execute("DROP TABLE task_checkpoints")
                connection.commit()
            self.assert_public_state_is_unreadable_without_write(install)

    def test_incomplete_declared_older_schema_is_unreadable_without_write(self):
        cases = (
            (
                "v9",
                create_v9_database,
                "DROP TABLE task_effort_activity",
            ),
            (
                "v10",
                create_v10_database,
                "DROP TABLE project_maintenance",
            ),
            (
                "v11",
                create_v11_database,
                "DROP TABLE managed_backup_generations",
            ),
            (
                "v12",
                create_v12_database,
                "DROP TABLE task_checkpoints",
            ),
        )
        for name, create_database, corrupt in cases:
            with self.subTest(schema=name):
                with tempfile.TemporaryDirectory() as tmp:
                    install = make_physical_install(Path(tmp))
                    create_database(install)
                    with closing(connect(install.legacy_db_path)) as connection:
                        connection.execute(corrupt)
                        connection.commit()
                    self.assert_public_state_is_unreadable_without_write(
                        install
                    )

        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v12_database(install)
            with closing(connect(install.legacy_db_path)) as connection:
                apply_viewer_maintenance_migration(connection)
                connection.execute("DROP TABLE viewer_maintenance_state")
                connection.commit()
            self.assert_public_state_is_unreadable_without_write(install)

    def test_v13_missing_required_viewer_row_is_unreadable_without_write(self):
        for layout in ("legacy", "fixed"):
            with self.subTest(layout=layout):
                with tempfile.TemporaryDirectory() as tmp:
                    install = make_physical_install(Path(tmp))
                    target = (
                        install.legacy_target
                        if layout == "legacy"
                        else DatabaseTarget(
                            project=install.legacy_target.project,
                            db_path=install.db_path,
                            explicit_db=True,
                        )
                    )
                    create_v12_target(target)
                    with closing(connect(target.db_path)) as connection:
                        apply_viewer_maintenance_migration(connection)
                        connection.execute(
                            "DELETE FROM viewer_maintenance_state"
                        )
                        connection.commit()
                    self.assert_public_state_is_unreadable_without_write(
                        install
                    )

    def test_all_v14_candidate_sources_require_complete_schema(self):
        for source_kind in ("fixed_backup", "legacy_primary"):
            with self.subTest(source_kind=source_kind):
                with tempfile.TemporaryDirectory() as tmp:
                    if source_kind == "fixed_backup":
                        install, target, _ = _setup(Path(tmp))
                        backup = backup_service.run_routine_backup(
                            target,
                            observed_at=FIRST_REBIND_TIME,
                        )
                        self.assertEqual(backup.code, "succeeded")
                        candidate = next(
                            target.resolved_backups_path.glob(
                                "taskgov-backup-v1_*.sqlite"
                            )
                        )
                        install.db_path.unlink()
                    else:
                        install = make_physical_install(Path(tmp))
                        create_v14_target(install.legacy_target)
                        candidate = install.legacy_db_path
                    with closing(connect(candidate)) as connection:
                        connection.execute("DROP TABLE task_checkpoints")
                        connection.commit()

                    self.assert_public_state_is_unreadable_without_write(
                        install
                    )

    def test_missing_primary_preserves_orphan_wal_sidecars(self):
        for legacy in (False, True):
            for suffix in ("-wal", "-shm"):
                for with_backup in (False, True):
                    with self.subTest(
                        legacy=legacy,
                        suffix=suffix,
                        with_backup=with_backup,
                    ):
                        self.assert_missing_primary_sidecar_rejected(
                            legacy=legacy,
                            suffix=suffix,
                            with_backup=with_backup,
                        )

    def test_sidecar_detection_uses_lexical_existence(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "taskgov.sqlite"
            wal = Path(f"{database}-wal")
            with mock.patch.object(
                storage_service.os.path,
                "lexists",
                side_effect=lambda path: Path(path) == wal,
            ):
                self.assertEqual(
                    storage_service.existing_sqlite_sidecars(database),
                    [wal],
                )
                with self.assertRaises(StorageError) as raised:
                    storage_service.validate_operational_journal_state(
                        database
                    )
            self.assertEqual(
                raised.exception.code,
                "unsupported_journal_mode",
            )

    def test_backup_deep_validation_rejects_noncanonical_artifact_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, original, _ = _setup(Path(tmp))
            wrong_root = install.project_root / "wrong-artifacts"
            wrong_backups = wrong_root / "backups"
            wrong_viewer = wrong_root / "viewer" / "task-viewer.html"
            wrong_backups.mkdir(parents=True)
            wrong_viewer.parent.mkdir(parents=True)
            (wrong_backups / "sentinel.txt").write_text(
                "preserve backup sentinel",
                encoding="utf-8",
            )
            wrong_viewer.write_text(
                "preserve viewer sentinel",
                encoding="utf-8",
            )
            before = file_snapshot(install.project_root)
            mismatched = DatabaseTarget(
                project=original.project,
                db_path=original.db_path,
                explicit_db=original.explicit_db,
                binding_path_hash=original.binding_path_hash,
                binding_generation=original.binding_generation,
                skill_root=original.skill_root,
                backups_path=wrong_backups,
                viewer_path=wrong_viewer,
                canonical_fixed=True,
            )

            with self.assertRaises(StorageError) as raised:
                backup_service._validate_canonical_artifact_set(mismatched)

            self.assertEqual(raised.exception.code, "setup_backup_failed")
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_legacy_backup_lock_uses_the_resolver_owned_artifact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install, original, _ = _setup(root)
            injected_backups = root / "resolver-owned" / "backups"
            reconstructed = root / "legacy-db" / "backups"
            target = DatabaseTarget(
                project=original.project,
                db_path=reconstructed.parent / "taskgov.sqlite",
                explicit_db=True,
                binding_path_hash=original.binding_path_hash,
                binding_generation=original.binding_generation,
                backups_path=injected_backups,
            )

            with setup_service._legacy_managed_backup_lock(target):
                self.assertTrue(
                    (injected_backups / "taskgov-backup.lock").is_file()
                )
                self.assertFalse(reconstructed.exists())

            self.assertFalse(reconstructed.exists())

    def test_setup_target_helpers_preserve_canonical_resolver_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install, _, _ = _setup(root)
            resolution = resolve_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            self.assertIsNotNone(resolution.stored_project)
            stored = resolution.stored_project
            assert stored is not None
            injected_paths = replace(
                resolution.paths,
                backups=root / "resolver-owned" / "backups",
                viewer=root / "resolver-owned" / "viewer.html",
            )
            injected = replace(resolution, paths=injected_paths)

            canonical_targets = (
                setup_service._stored_target_at(
                    injected,
                    injected_paths.database,
                ),
                setup_service._bound_target_at(
                    injected,
                    injected_paths.database,
                    binding_path_hash=stored.canonical_path_hash,
                    binding_generation=stored.binding_generation,
                ),
            )
            for target in canonical_targets:
                self.assertTrue(target.canonical_fixed)
                self.assertEqual(target.backups_path, injected_paths.backups)
                self.assertEqual(target.viewer_path, injected_paths.viewer)

            private_database = root / "private-stage" / "taskgov.sqlite"
            private = setup_service._stored_target_at(
                injected,
                private_database,
            )
            self.assertFalse(private.canonical_fixed)
            self.assertEqual(
                private.backups_path,
                private_database.parent / "backups",
            )
            self.assertEqual(
                private.viewer_path,
                private_database.parent / "viewer" / "task-viewer.html",
            )

    def test_writer_revalidates_hash_and_generation_under_its_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, original, identity_scheme = _setup(Path(tmp))
            self.assertIsNotNone(original.binding_path_hash)
            self.assertEqual(original.binding_generation, 1)
            connection = connect_initialized(original)
            moved = _destination_target(
                original,
                install.project_root.parent / "moved-project",
                binding_path_hash=original.binding_path_hash,
                binding_generation=original.binding_generation,
            )
            _rebind(
                original,
                moved,
                identity_scheme=identity_scheme,
                token_digest="1" * 64,
                bound_at=FIRST_REBIND_TIME,
            )

            try:
                with self.assertRaises(StorageError) as raised:
                    begin_initialized_write(connection, original)
            finally:
                connection.close()

            self.assertEqual(
                (raised.exception.code, raised.exception.message),
                (
                    "project_state_unreadable",
                    "project state could not be read safely",
                ),
            )

    def test_generation_rejects_stale_target_after_binding_returns_to_same_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, original, identity_scheme = _setup(Path(tmp))
            moved = _destination_target(
                original,
                install.project_root.parent / "moved-project",
                binding_path_hash=original.binding_path_hash,
                binding_generation=original.binding_generation,
            )
            moved_binding = _rebind(
                original,
                moved,
                identity_scheme=identity_scheme,
                token_digest="2" * 64,
                bound_at=FIRST_REBIND_TIME,
            )
            current_moved = DatabaseTarget(
                project=moved.project,
                db_path=moved.db_path,
                explicit_db=True,
                binding_path_hash=moved_binding.canonical_path_hash,
                binding_generation=moved_binding.binding_generation,
            )
            returned = _destination_target(
                current_moved,
                original.project.canonical_repo,
                binding_path_hash=current_moved.binding_path_hash,
                binding_generation=current_moved.binding_generation,
            )
            returned = DatabaseTarget(
                project=ProjectIdentity(
                    project_id=original.project.project_id,
                    canonical_repo=original.project.canonical_repo,
                    canonical_path_hash=original.project.canonical_path_hash,
                    display_name=original.project.display_name,
                ),
                db_path=returned.db_path,
                explicit_db=True,
                binding_path_hash=returned.binding_path_hash,
                binding_generation=returned.binding_generation,
            )
            _rebind(
                current_moved,
                returned,
                identity_scheme=identity_scheme,
                token_digest="3" * 64,
                bound_at=SECOND_REBIND_TIME,
            )

            with self.assertRaises(StorageError) as raised:
                connect_initialized(original)

            self.assertEqual(raised.exception.code, "project_state_unreadable")

    def test_viewer_revalidates_immediately_before_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, original, identity_scheme = _setup(Path(tmp))
            before = install.viewer_path.read_bytes()
            with closing(connect_initialized(original)) as connection:
                begin_initialized_write(connection, original)
                connection.execute(
                    """
                    UPDATE viewer_maintenance_state
                       SET source_generation = source_generation + 1
                     WHERE project_id = ?
                    """,
                    (original.project.project_id,),
                )
                connection.commit()
            moved = _destination_target(
                original,
                install.project_root.parent / "moved-project",
                binding_path_hash=original.binding_path_hash,
                binding_generation=original.binding_generation,
            )
            real_write = viewer_service.write_viewer_html

            def write_after_rebind(*args, **kwargs):
                _rebind(
                    original,
                    moved,
                    identity_scheme=identity_scheme,
                    token_digest="4" * 64,
                    bound_at=FIRST_REBIND_TIME,
                )
                return real_write(*args, **kwargs)

            with mock.patch.object(
                viewer_service,
                "write_viewer_html",
                side_effect=write_after_rebind,
            ):
                result = viewer_service.publish_setup_viewer(
                    original,
                    skill_root=install.skill_root,
                )

            self.assertEqual(result.code, "failed")
            self.assertEqual(result.renders, 0)
            self.assertEqual(install.viewer_path.read_bytes(), before)

    def test_viewer_holds_revalidation_transaction_through_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, original, identity_scheme = _setup(Path(tmp))
            with closing(connect_initialized(original)) as connection:
                begin_initialized_write(connection, original)
                connection.execute(
                    """
                    UPDATE viewer_maintenance_state
                       SET source_generation = source_generation + 1
                     WHERE project_id = ?
                    """,
                    (original.project.project_id,),
                )
                connection.commit()
            moved = _destination_target(
                original,
                install.project_root.parent / "moved-project",
                binding_path_hash=original.binding_path_hash,
                binding_generation=original.binding_generation,
            )
            real_replace = viewer_writer.os.replace
            rebind_errors: list[str] = []

            def replace_while_rebinding(source, destination):
                try:
                    _rebind(
                        original,
                        moved,
                        identity_scheme=identity_scheme,
                        token_digest="6" * 64,
                        bound_at=FIRST_REBIND_TIME,
                    )
                except StorageError as exc:
                    rebind_errors.append(exc.code)
                return real_replace(source, destination)

            with mock.patch.object(
                viewer_writer.os,
                "replace",
                side_effect=replace_while_rebinding,
            ):
                result = viewer_service.publish_setup_viewer(
                    original,
                    skill_root=install.skill_root,
                )

            self.assertEqual((result.code, result.renders), ("succeeded", 1))
            self.assertEqual(rebind_errors, ["database_busy"])
            current = resolve_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            self.assertEqual(current.binding, "matching")
            self.assertEqual(current.target.binding_generation, 1)

    def test_backup_revalidates_after_copy_before_atomic_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, original, identity_scheme = _setup(Path(tmp))
            moved = _destination_target(
                original,
                install.project_root.parent / "moved-project",
                binding_path_hash=original.binding_path_hash,
                binding_generation=original.binding_generation,
            )
            metadata = backup_service._new_publication_metadata(
                original,
                3,
                published_at=FIRST_REBIND_TIME,
            )
            real_validate = backup_service._validate_database
            calls = 0

            def validate_with_rebind(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 3:
                    _rebind(
                        original,
                        moved,
                        identity_scheme=identity_scheme,
                        token_digest="5" * 64,
                        bound_at=SECOND_REBIND_TIME,
                    )
                return real_validate(*args, **kwargs)

            with (
                mock.patch.object(
                    backup_service,
                    "_validate_database",
                    side_effect=validate_with_rebind,
                ),
                self.assertRaises(StorageError) as raised,
            ):
                backup_service._copy(original, metadata)

            self.assertEqual(raised.exception.code, "project_state_unreadable")
            backup_root = install.fixed_root / "backups"
            self.assertEqual(
                list(backup_root.glob("taskgov-backup-v1_*.sqlite")),
                [],
            )
            self.assertEqual(
                list(backup_root.glob(".taskgov-backup-*.tmp")),
                [],
            )

    def test_setup_runs_effective_ignore_detection_only_in_initial_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            real_check = project_scope_service._state_is_ignored
            with mock.patch.object(
                project_scope_service,
                "_state_is_ignored",
                wraps=real_check,
            ) as check:
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertTrue(result.ok, result)
            self.assertEqual(check.call_count, 1)


if __name__ == "__main__":
    unittest.main()
