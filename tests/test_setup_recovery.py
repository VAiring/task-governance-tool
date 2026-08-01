import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock


try:
    from m14_test_support import (
        canonical_managed_sqlite_files,
        canonical_test_path,
        create_v10_target,
        create_v11_target,
        create_v12_target,
        create_v9_target,
        json_payload,
        make_physical_install,
    )
except ModuleNotFoundError:
    from tests.m14_test_support import (
        canonical_managed_sqlite_files,
        canonical_test_path,
        create_v10_target,
        create_v11_target,
        create_v12_target,
        create_v9_target,
        json_payload,
        make_physical_install,
    )

from task_governance_tool import backup as backup_service
from task_governance_tool import setup as setup_service
from task_governance_tool.storage import DatabaseTarget, StorageError


RECOVERY_WRITES = [
    "database_restore",
    "viewer_publish",
]
RECOVERY_MIGRATION_WRITES = [
    "database_restore",
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "viewer_publish",
]


def fixed_fixture_target(install) -> DatabaseTarget:
    """Create an explicit fixed-current target for pre-v14 fixtures."""

    return DatabaseTarget(
        project=install.legacy_target.project,
        db_path=install.db_path,
        explicit_db=True,
    )


class SetupManagedBackupRecoveryTests(unittest.TestCase):
    def _temporary_restore_paths(self, install):
        return sorted(install.db_path.parent.glob(".taskgov-restore-*"))

    def _initialize_with_backed_up_task(self, root: Path):
        install = make_physical_install(root)
        initialized = install.run("setup", "--json")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        added = install.run(
            "task",
            "add",
            "--title",
            "Retained recovery task",
            "--json",
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        backups = canonical_managed_sqlite_files(
            install,
            exclude=(install.db_path,),
        )
        self.assertEqual(len(backups), 1)
        return install, backups[0]

    def test_setup_preview_and_write_restore_current_managed_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            selected = backup_service.select_managed_backup_for_recovery(
                install.target
            )
            self.assertIsNotNone(selected)
            self.assertEqual(
                canonical_test_path(selected.path),
                canonical_test_path(backup_path),
            )
            before_backup = backup_path.read_bytes()
            install.db_path.unlink()

            before_preview = {
                path.relative_to(install.skill_root).as_posix(): path.read_bytes()
                for path in install.skill_root.rglob("*")
                if path.is_file()
            }
            preview = install.run("setup", "--read-only", "--json")

            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_data = json_payload(preview)["data"]
            self.assertEqual(preview_data["status"], "setup_preview")
            self.assertEqual(
                preview_data["planned_writes"],
                RECOVERY_WRITES,
            )
            self.assertEqual(preview_data["completed_writes"], [])
            self.assertEqual(preview_data["schema_from"], 17)
            self.assertFalse(preview_data["maintenance_enabled"])
            self.assertEqual(
                {
                    path.relative_to(install.skill_root).as_posix():
                    path.read_bytes()
                    for path in install.skill_root.rglob("*")
                    if path.is_file()
                },
                before_preview,
            )

            restored = install.run("setup", "--json")

            self.assertEqual(restored.returncode, 0, restored.stderr)
            restored_data = json_payload(restored)["data"]
            self.assertEqual(restored_data["status"], "setup_complete")
            self.assertEqual(
                restored_data["planned_writes"],
                RECOVERY_WRITES,
            )
            self.assertEqual(
                restored_data["completed_writes"],
                RECOVERY_WRITES,
            )
            self.assertTrue(restored_data["maintenance_enabled"])
            self.assertEqual(backup_path.read_bytes(), before_backup)
            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT title FROM tasks ORDER BY created_at, task_id"
                    ).fetchall(),
                    [("Retained recovery task",)],
                )
                generation = connection.execute(
                    """
                    SELECT generation_id, published_at,
                           publication_retention
                      FROM managed_backup_generations
                     ORDER BY published_at, generation_id
                    """
                ).fetchall()
                maintenance = connection.execute(
                    """
                    SELECT latest_backup_generation_id,
                           backup_last_success_at,
                           applied_backup_generations
                      FROM project_maintenance
                    """
                ).fetchone()
            self.assertEqual(
                generation[-1],
                (
                    selected.metadata.generation_id,
                    selected.metadata.published_at,
                    selected.metadata.publication_retention,
                ),
            )
            self.assertEqual(
                maintenance,
                (
                    selected.metadata.generation_id,
                    selected.metadata.published_at,
                    selected.metadata.publication_retention,
                ),
            )
            self.assertEqual(self._temporary_restore_paths(install), [])

    def test_existing_schema_zero_primary_never_triggers_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            before_backup = backup_path.read_bytes()
            before_viewer = install.viewer_path.read_bytes()
            install.db_path.unlink()
            with closing(sqlite3.connect(install.db_path)):
                pass
            before_primary = install.db_path.read_bytes()

            with mock.patch.object(
                setup_service,
                "select_managed_backup_for_recovery",
                side_effect=AssertionError(
                    "existing primary must not trigger recovery discovery"
                ),
            ) as selector:
                preview = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=True,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertFalse(preview.ok)
            self.assertEqual(
                preview.error_code,
                "project_state_unreadable",
            )
            self.assertEqual(
                preview.data,
                {
                    "status": None,
                    "planned_writes": [],
                    "completed_writes": [],
                    "schema_from": None,
                    "schema_to": 17,
                    "maintenance_enabled": None,
                    "backup_interval_minutes": None,
                    "backup_generations": None,
                    "viewer_status": None,
                    "relocation": {
                        "required": False,
                        "source_layout": None,
                        "identity_scheme": None,
                        "binding_generation": None,
                        "confirmation_token": None,
                        "expires_at": None,
                    },
                },
            )
            self.assertEqual(install.db_path.read_bytes(), before_primary)
            self.assertEqual(backup_path.read_bytes(), before_backup)
            self.assertEqual(install.viewer_path.read_bytes(), before_viewer)
            selector.assert_not_called()

            with mock.patch.object(
                setup_service,
                "select_managed_backup_for_recovery",
                side_effect=AssertionError(
                    "existing primary must not trigger recovery discovery"
                ),
            ) as selector:
                initialized = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertFalse(initialized.ok)
            self.assertEqual(
                initialized.error_code,
                "project_state_unreadable",
            )
            self.assertEqual(initialized.data, preview.data)
            self.assertEqual(install.db_path.read_bytes(), before_primary)
            self.assertEqual(backup_path.read_bytes(), before_backup)
            self.assertEqual(install.viewer_path.read_bytes(), before_viewer)
            selector.assert_not_called()

    def test_setup_restores_supported_old_schema_then_uses_normal_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v9_target(fixed_fixture_target(install))
            with backup_service.managed_backup_lock(install.target):
                candidate_metadata = backup_service.publish_setup_backup(
                    install.target,
                    3,
                )
            with closing(sqlite3.connect(install.db_path)) as connection:
                original_counts = (
                    connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events"
                    ).fetchone()[0],
                )
            install.db_path.unlink()

            preview = install.run("setup", "--read-only", "--json")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(
                json_payload(preview)["data"]["planned_writes"],
                RECOVERY_MIGRATION_WRITES,
            )
            self.assertFalse(install.db_path.exists())

            restored = install.run("setup", "--json")

            self.assertEqual(restored.returncode, 0, restored.stderr)
            data = json_payload(restored)["data"]
            self.assertEqual(data["schema_from"], 9)
            self.assertEqual(
                data["planned_writes"],
                RECOVERY_MIGRATION_WRITES,
            )
            self.assertEqual(
                data["completed_writes"],
                RECOVERY_MIGRATION_WRITES,
            )
            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    17,
                )
                self.assertEqual(
                    (
                        connection.execute(
                            "SELECT COUNT(*) FROM tasks"
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM task_events"
                        ).fetchone()[0],
                    ),
                    original_counts,
                )
                self.assertIsNotNone(
                    connection.execute(
                        """
                        SELECT 1 FROM managed_backup_generations
                         WHERE generation_id = ?
                        """,
                        (candidate_metadata.generation_id,),
                    ).fetchone()
                )

    def test_newer_invalid_artifact_makes_the_fixed_backup_set_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, older_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            second = install.run(
                "task",
                "add",
                "--title",
                "Not present in the older generation",
                "--json",
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            with backup_service.managed_backup_lock(install.target):
                newer = backup_service.publish_setup_backup(
                    install.target,
                    3,
                )
            newer_path = next(
                path
                for path in canonical_managed_sqlite_files(
                    install,
                    exclude=(install.db_path,),
                )
                if newer.generation_id[10:] in path.name
            )
            newer_bytes = b"not a sqlite database"
            newer_path.write_bytes(newer_bytes)
            install.db_path.unlink()

            restored = install.run("setup", "--json")

            self.assertEqual(restored.returncode, 2)
            self.assertEqual(
                json_payload(restored)["errors"],
                [{
                    "code": "project_state_unreadable",
                    "message": "project state could not be read safely",
                }],
            )
            self.assertFalse(install.db_path.exists())
            self.assertEqual(newer_path.read_bytes(), newer_bytes)
            self.assertTrue(older_path.is_file())

    def test_configured_v10_through_v12_recovery_preserves_policy(self):
        fixture_factories = {
            10: create_v10_target,
            11: create_v11_target,
            12: create_v12_target,
        }
        expected_writes = [
            "database_restore",
            "migration_backup",
            "database_migrate",
            "viewer_publish",
        ]
        for version, create_fixture in fixture_factories.items():
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_fixture(
                    fixed_fixture_target(install),
                    enabled=True,
                    interval_minutes=45,
                    generations=2,
                )
                with backup_service.managed_backup_lock(install.target):
                    backup_service.publish_setup_backup(
                        install.target,
                        2,
                    )
                install.db_path.unlink()

                restored = install.run("setup", "--json")

                self.assertEqual(restored.returncode, 0, restored.stderr)
                data = json_payload(restored)["data"]
                self.assertEqual(data["schema_from"], version)
                self.assertEqual(data["planned_writes"], expected_writes)
                self.assertEqual(data["completed_writes"], expected_writes)
                self.assertTrue(data["maintenance_enabled"])
                self.assertEqual(data["backup_interval_minutes"], 45)
                self.assertEqual(data["backup_generations"], 2)
                with closing(sqlite3.connect(install.db_path)) as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        17,
                    )
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT backup_interval_minutes, backup_generations
                              FROM project_maintenance
                            """
                        ).fetchone(),
                        (45, 2),
                    )

    def test_only_invalid_or_foreign_managed_material_fails_closed(self):
        with self.subTest(kind="invalid"), tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            invalid_bytes = b"not a sqlite database"
            backup_path.write_bytes(invalid_bytes)
            install.db_path.unlink()

            failed = install.run("setup", "--json")

            self.assertEqual(failed.returncode, 2)
            payload = json_payload(failed)
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "project_state_unreadable",
                    "message": "project state could not be read safely",
                }],
            )
            self.assertEqual(payload["data"]["planned_writes"], [])
            self.assertFalse(install.db_path.exists())
            self.assertEqual(backup_path.read_bytes(), invalid_bytes)

        with self.subTest(kind="foreign"), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            foreign, foreign_backup = self._initialize_with_backed_up_task(
                root / "foreign"
            )
            local = make_physical_install(root / "local")
            local_backup_dir = local.db_path.parent / "backups"
            local_backup_dir.mkdir(parents=True)
            copied = local_backup_dir / foreign_backup.name
            shutil.copy2(foreign_backup, copied)
            copied_bytes = copied.read_bytes()

            failed = local.run("setup", "--json")

            self.assertEqual(failed.returncode, 2)
            self.assertEqual(
                json_payload(failed)["errors"][0]["code"],
                "project_state_unreadable",
            )
            self.assertFalse(local.db_path.exists())
            self.assertEqual(copied.read_bytes(), copied_bytes)
            self.assertTrue(foreign.db_path.is_file())

    def test_atomic_publish_does_not_replace_a_racing_canonical_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, _ = self._initialize_with_backed_up_task(Path(tmp))
            target = install.target
            candidate = backup_service.select_managed_backup_for_recovery(
                target
            )
            self.assertIsNotNone(candidate)
            install.db_path.unlink()
            real_link = backup_service.os.link
            competing = b"competing canonical state"

            def create_competing_file_then_link(source, destination):
                Path(destination).write_bytes(competing)
                return real_link(source, destination)

            with mock.patch.object(
                backup_service.os,
                "link",
                side_effect=create_competing_file_then_link,
            ):
                with self.assertRaises(StorageError) as raised:
                    backup_service.restore_managed_backup(
                        target,
                        candidate,
                    )

            self.assertEqual(raised.exception.code, "setup_restore_failed")
            self.assertEqual(install.db_path.read_bytes(), competing)
            self.assertEqual(self._temporary_restore_paths(install), [])

    def test_candidate_identity_change_under_lock_fails_before_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            install.db_path.unlink()
            real_lock = setup_service.managed_backup_lock

            @contextmanager
            def change_candidate_identity(target):
                with real_lock(target):
                    details = backup_path.stat()
                    os.utime(
                        backup_path,
                        ns=(
                            details.st_atime_ns,
                            details.st_mtime_ns + 2_000_000_000,
                        ),
                    )
                    yield

            with mock.patch.object(
                setup_service,
                "managed_backup_lock",
                change_candidate_identity,
            ):
                failed = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(failed.error_code, "setup_restore_failed")
            self.assertEqual(
                failed.error_message,
                "managed backup could not be restored",
            )
            self.assertFalse(install.db_path.exists())
            self.assertTrue(backup_path.is_file())
            self.assertEqual(self._temporary_restore_paths(install), [])

    def test_recovery_lock_contention_fails_without_creating_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            target = install.target
            before_backup = backup_path.read_bytes()
            install.db_path.unlink()

            with backup_service.managed_backup_lock(target):
                failed = install.run("setup", "--json")

            self.assertEqual(failed.returncode, 2)
            self.assertEqual(
                json_payload(failed)["errors"],
                [{
                    "code": "setup_restore_failed",
                    "message": "managed backup could not be restored",
                }],
            )
            self.assertFalse(install.db_path.exists())
            self.assertEqual(backup_path.read_bytes(), before_backup)

    def test_orphan_canonical_rollback_journal_blocks_recovery_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            before_backup = backup_path.read_bytes()
            install.db_path.unlink()
            journal = Path(str(install.db_path) + "-journal")
            journal_bytes = b"preserve orphan rollback journal"
            journal.write_bytes(journal_bytes)

            failed = install.run("setup", "--json")

            self.assertEqual(failed.returncode, 2)
            self.assertEqual(
                json_payload(failed)["errors"],
                [{
                    "code": "project_state_unreadable",
                    "message": "project state could not be read safely",
                }],
            )
            self.assertFalse(install.db_path.exists())
            self.assertEqual(journal.read_bytes(), journal_bytes)
            self.assertEqual(backup_path.read_bytes(), before_backup)
            self.assertEqual(self._temporary_restore_paths(install), [])

    def test_orphan_rollback_journal_blocks_fresh_setup_without_any_valid_candidate(self):
        for material in ("none", "invalid", "unrecognized"):
            with (
                self.subTest(material=material),
                tempfile.TemporaryDirectory() as tmp,
            ):
                install = make_physical_install(Path(tmp))
                backup_files: dict[Path, bytes] = {}
                backup_dir = install.db_path.parent / "backups"
                if material == "invalid":
                    backup_dir.mkdir(parents=True)
                    path = backup_dir / (
                        "taskgov-backup-v1_20260727T000000Z_"
                        + ("a" * 32)
                        + "_r3.sqlite"
                    )
                    path.write_bytes(b"invalid managed SQLite material")
                    backup_files[path] = path.read_bytes()
                elif material == "unrecognized":
                    backup_dir.mkdir(parents=True)
                    path = backup_dir / "unrecognized-backup.txt"
                    path.write_bytes(b"unrecognized material")
                    backup_files[path] = path.read_bytes()
                journal = Path(str(install.db_path) + "-journal")
                journal_bytes = b"preserve fresh orphan rollback journal"
                journal.parent.mkdir(parents=True, exist_ok=True)
                journal.write_bytes(journal_bytes)

                failed = install.run("setup", "--json")

                self.assertEqual(failed.returncode, 2)
                self.assertEqual(
                    json_payload(failed)["errors"],
                    [{
                        "code": "project_state_unreadable",
                        "message": "project state could not be read safely",
                    }],
                )
                self.assertFalse(install.db_path.exists())
                self.assertEqual(journal.read_bytes(), journal_bytes)
                for path, content in backup_files.items():
                    self.assertEqual(path.read_bytes(), content)
                self.assertFalse(install.viewer_path.exists())
                self.assertEqual(self._temporary_restore_paths(install), [])

    def test_rollback_journal_appearing_during_fsync_blocks_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            target = install.target
            candidate = backup_service.select_managed_backup_for_recovery(
                target
            )
            self.assertIsNotNone(candidate)
            before_backup = backup_path.read_bytes()
            install.db_path.unlink()
            journal = Path(str(install.db_path) + "-journal")
            journal_bytes = b"late orphan rollback journal"
            real_fsync = backup_service.os.fsync

            def fsync_then_publish_journal(descriptor):
                real_fsync(descriptor)
                journal.write_bytes(journal_bytes)

            with mock.patch.object(
                backup_service.os,
                "fsync",
                side_effect=fsync_then_publish_journal,
            ):
                with self.assertRaises(StorageError) as raised:
                    backup_service.restore_managed_backup(
                        target,
                        candidate,
                    )

            self.assertEqual(raised.exception.code, "setup_restore_failed")
            self.assertFalse(install.db_path.exists())
            self.assertEqual(journal.read_bytes(), journal_bytes)
            self.assertEqual(backup_path.read_bytes(), before_backup)
            self.assertEqual(self._temporary_restore_paths(install), [])

    def test_candidate_appearing_before_fresh_initialize_blocks_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install, backup_path = self._initialize_with_backed_up_task(root)
            hidden = root / backup_path.name
            backup_bytes = backup_path.read_bytes()
            shutil.move(backup_path, hidden)
            install.db_path.unlink()
            shutil.rmtree(install.fixed_root)
            real_revalidate = setup_service._revalidate_scope
            restored_candidate = False

            def revalidate_then_restore_candidate(**kwargs):
                nonlocal restored_candidate
                scope = real_revalidate(**kwargs)
                if not restored_candidate:
                    backup_path.parent.mkdir(parents=True)
                    shutil.move(hidden, backup_path)
                    restored_candidate = True
                return scope

            with mock.patch.object(
                setup_service,
                "_revalidate_scope",
                side_effect=revalidate_then_restore_candidate,
            ):
                failed = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(failed.error_code, "setup_restore_failed")
            self.assertFalse(install.db_path.exists())
            self.assertEqual(backup_path.read_bytes(), backup_bytes)

            recovered = install.run("setup", "--json")

            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT title FROM tasks ORDER BY created_at, task_id"
                    ).fetchall(),
                    [("Retained recovery task",)],
                )

    def test_unrecognized_fixed_backup_content_blocks_fresh_setup_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            backup_dir = install.db_path.parent / "backups"
            backup_dir.mkdir(parents=True)
            unrelated = backup_dir / "notes.txt"
            unrelated.write_text("not managed by taskgov", encoding="utf-8")

            initialized = install.run("setup", "--json")

            self.assertEqual(initialized.returncode, 2)
            self.assertEqual(
                json_payload(initialized)["errors"],
                [{
                    "code": "project_state_unreadable",
                    "message": "project state could not be read safely",
                }],
            )
            self.assertFalse(install.db_path.exists())
            self.assertEqual(
                unrelated.read_text(encoding="utf-8"),
                "not managed by taskgov",
            )

    def test_linklike_only_managed_material_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            backup_bytes = backup_path.read_bytes()
            install.db_path.unlink()
            real_identity = backup_service._file_identity

            def reject_linklike_candidate(path):
                if canonical_test_path(Path(path)) == canonical_test_path(
                    backup_path
                ):
                    return None
                return real_identity(path)

            with mock.patch.object(
                backup_service,
                "_file_identity",
                side_effect=reject_linklike_candidate,
            ):
                failed = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(
                (failed.error_code, failed.error_message),
                (
                    "setup_restore_failed",
                    "managed backup could not be restored",
                ),
            )
            self.assertFalse(install.db_path.exists())
            self.assertEqual(backup_path.read_bytes(), backup_bytes)

    def test_existing_unreadable_primary_is_never_replaced_from_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, backup_path = self._initialize_with_backed_up_task(
                Path(tmp)
            )
            primary_bytes = b"existing unreadable canonical state"
            backup_bytes = backup_path.read_bytes()
            install.db_path.write_bytes(primary_bytes)

            failed = install.run("setup", "--json")

            self.assertEqual(failed.returncode, 2)
            self.assertEqual(
                json_payload(failed)["errors"][0]["code"],
                "project_state_unreadable",
            )
            self.assertEqual(install.db_path.read_bytes(), primary_bytes)
            self.assertEqual(backup_path.read_bytes(), backup_bytes)


if __name__ == "__main__":
    unittest.main()
