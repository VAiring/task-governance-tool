import hashlib
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


try:
    from m14_test_support import (
        OldFixedPhysicalInstall,
        create_v12_database,
        create_v14_target,
        json_payload,
        make_physical_install,
    )
except ModuleNotFoundError:
    from tests.m14_test_support import (
        OldFixedPhysicalInstall,
        create_v12_database,
        create_v14_target,
        json_payload,
        make_physical_install,
    )

from task_governance_tool import backup as backup_service
from task_governance_tool import setup as setup_service
from task_governance_tool import setup_state_separation as separation_service
from task_governance_tool.state_resolver import (
    resolve_package_project_state,
    resolve_project_state,
)
from task_governance_tool.state_transition import (
    CleanupInventoryEntry,
    build_cleanup_inventory,
    cleanup_roots,
)
from task_governance_tool.project_binding_repository import (
    read_project_binding_state,
    set_legacy_cleanup_pending,
)
from task_governance_tool.storage import (
    DatabaseTarget,
    connect,
    current_schema_version,
    initialize_database,
)


LAYOUT_WRITES = [
    "state_layout_retire",
    "state_layout_publish",
    "state_layout_activate",
]
MAX_EXTRA_ARTIFACT_BYTES = 16_777_216


def tree_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    """Capture names, kinds, sizes, and contents without following links."""

    snapshot: dict[str, tuple[object, ...]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode):
            snapshot[relative] = ("link", os.readlink(path))
        elif stat.S_ISDIR(details.st_mode):
            snapshot[relative] = ("directory",)
        elif stat.S_ISREG(details.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            snapshot[relative] = (
                "file",
                int(details.st_size),
                digest.hexdigest(),
            )
        else:
            snapshot[relative] = (
                "other",
                int(details.st_mode),
                int(details.st_size),
            )
    return snapshot


class M17SetupRegressionTests(unittest.TestCase):
    def assert_unreadable_preplan(
        self,
        install,
        *,
        include_write: bool,
    ) -> None:
        before = tree_snapshot(install.project_root)
        resolution = resolve_project_state(
            skill_root=install.skill_root,
            repo=install.project_root,
        )
        self.assertEqual(
            resolution.error_code,
            "project_state_unreadable",
        )
        self.assertEqual(tree_snapshot(install.project_root), before)

        modes = (True, False) if include_write else (True,)
        for read_only in modes:
            with self.subTest(read_only=read_only):
                arguments = ["setup"]
                if read_only:
                    arguments.append("--read-only")
                arguments.append("--json")
                result = install.run(*arguments)

                self.assertEqual(result.returncode, 2)
                payload = json_payload(result)
                self.assertEqual(
                    payload["errors"],
                    [{
                        "code": "project_state_unreadable",
                        "message": "project state could not be read safely",
                    }],
                )
                self.assertEqual(payload["data"]["planned_writes"], [])
                self.assertEqual(payload["data"]["completed_writes"], [])
                self.assertEqual(tree_snapshot(install.project_root), before)

        state_root = install.skill_root / "state"
        self.assertFalse((state_root / "taskgov-state.lock").exists())
        self.assertEqual(
            list(state_root.glob(".current-stage-*")),
            [],
        )
        self.assertEqual(
            list(state_root.glob(".legacy-cleanup-*")),
            [],
        )
        self.assertFalse(install.fixed_root.exists())

    def make_pending_cleanup_install(self, root: Path):
        # Explicit predecessor-format fixture, not a new-runtime migration.
        # Keep its valid cleanup metadata and original source independently
        # observable; current setup must not execute this deletion intent.
        physical = make_physical_install(root)
        install = OldFixedPhysicalInstall(physical.project_root, physical.skill_root)
        create_v14_target(install.legacy_target)
        target = DatabaseTarget(
            project=install.legacy_target.project,
            db_path=install.db_path,
            explicit_db=True,
        )
        initialize_database(target)
        original = install.legacy_db_path.read_bytes()
        inventory = build_cleanup_inventory((CleanupInventoryEntry(
            name="taskgov.sqlite", size=len(original),
            sha256=hashlib.sha256(original).hexdigest(),
        ),))
        with closing(connect(install.db_path)) as connection:
            binding = read_project_binding_state(connection)
        set_legacy_cleanup_pending(
            target, project_id=binding.project_id,
            expected_identity_scheme=binding.identity_scheme,
            expected_generation=binding.binding_generation,
            expected_path_hash=binding.canonical_path_hash,
            inventory=inventory.text, fingerprint=inventory.fingerprint,
        )
        install.viewer_path.parent.mkdir(parents=True)
        install.viewer_path.write_bytes(b"predecessor last-good Viewer fixture")
        self.assertTrue(install.db_path.is_file())
        self.assertTrue(install.legacy_db_path.is_file())
        with closing(sqlite3.connect(install.db_path)) as connection:
            pending = connection.execute(
                """
                SELECT legacy_cleanup_pending,
                       legacy_cleanup_inventory,
                       legacy_cleanup_fingerprint
                  FROM project_meta
                """
            ).fetchone()
        self.assertEqual(pending[0], 1)
        self.assertIsNotNone(pending[1])
        self.assertIsNotNone(pending[2])
        resolved = resolve_package_project_state(
            skill_root=install.skill_root, repo=install.project_root,
        )
        self.assertIsNone(resolved.error_code, resolved)
        self.assertTrue(resolved.stored_project.legacy_cleanup_pending)
        return install

    def test_legacy_backup_only_journal_is_unreadable_before_any_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v14_target(install.legacy_target)
            with backup_service.managed_backup_lock(
                install.legacy_target
            ):
                backup_service.publish_setup_backup(
                    install.legacy_target,
                    3,
                )
            install.legacy_db_path.unlink()
            journal = Path(str(install.legacy_db_path) + "-journal")
            journal.write_bytes(b"preserve legacy rollback journal")

            self.assert_unreadable_preplan(
                install,
                include_write=True,
            )

    def test_oversized_legacy_viewer_is_unreadable_without_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v14_target(install.legacy_target)
            viewer = (
                install.legacy_root
                / "viewer"
                / "task-viewer.html"
            )
            viewer.parent.mkdir(parents=True)
            with viewer.open("wb") as stream:
                stream.truncate(
                    install.legacy_db_path.stat().st_size
                    + MAX_EXTRA_ARTIFACT_BYTES
                    + 1
                )

            self.assert_unreadable_preplan(
                install,
                include_write=False,
            )

    def test_two_byte_legacy_backup_lock_is_unreadable_without_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v14_target(install.legacy_target)
            backup_lock = (
                install.legacy_root
                / "backups"
                / "taskgov-backup.lock"
            )
            backup_lock.parent.mkdir(parents=True)
            backup_lock.write_bytes(b"XX")

            self.assert_unreadable_preplan(
                install,
                include_write=False,
            )

    def test_two_byte_transition_lock_is_setup_incomplete_in_both_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            state_root = install.skill_root / "state"
            state_root.mkdir()
            transition_lock = state_root / "taskgov-state.lock"
            transition_lock.write_bytes(b"XX")
            before = tree_snapshot(install.skill_root)

            for read_only in (True, False):
                with self.subTest(read_only=read_only):
                    arguments = ["setup"]
                    if read_only:
                        arguments.append("--read-only")
                    arguments.append("--json")
                    result = install.run(*arguments)

                    self.assertEqual(result.returncode, 2)
                    payload = json_payload(result)
                    self.assertEqual(
                        payload["errors"],
                        [{
                            "code": "setup_incomplete",
                            "message": (
                                "setup completed only partially; rerun setup"
                            ),
                        }],
                    )
                    self.assertEqual(
                        payload["data"]["planned_writes"],
                        [],
                    )
                    self.assertEqual(
                        payload["data"]["completed_writes"],
                        [],
                    )
                    self.assertEqual(
                        tree_snapshot(install.skill_root),
                        before,
                    )

    def test_missing_primary_uses_canonical_legacy_backup_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v14_target(install.legacy_target)
            with backup_service.managed_backup_lock(
                install.legacy_target
            ):
                backup_service.publish_setup_backup(
                    install.legacy_target,
                    3,
                )
            install.legacy_db_path.unlink()
            state_root = install.skill_root / "state"
            transition_lock = state_root / "taskgov-state.lock"
            transition_lock.write_bytes(b"\0")
            before = tree_snapshot(install.skill_root)
            source_before = tree_snapshot(install.legacy_root)

            with backup_service.managed_backup_lock(
                install.legacy_target
            ):
                failed = install.run("setup", "--json")

                self.assertEqual(failed.returncode, 2)
                payload = json_payload(failed)
                # Windows refuses even the initial read of the locked byte;
                # POSIX reaches the later zero-wait acquisition instead.
                code = "project_state_unreadable" if os.name == "nt" else "database_busy"
                self.assertEqual(
                    payload["errors"],
                    [{
                        "code": code,
                        "message": setup_service.PROJECT_STATE_MESSAGES[code],
                    }],
                )
                self.assertEqual(
                    payload["data"]["planned_writes"],
                    LAYOUT_WRITES,
                )
                self.assertEqual(
                    payload["data"]["completed_writes"],
                    [],
                )
                self.assertFalse(install.fixed_root.exists())
                self.assertFalse((install.skill_root / "state" / "current").exists())
                if os.name == "nt":
                    self.assertFalse((install.project_root / ".taskgov").exists())

            self.assertEqual(
                tree_snapshot(install.skill_root),
                before,
            )
            retried = install.run("setup", "--json")

            self.assertEqual(retried.returncode, 0, retried.stderr)
            self.assertTrue(install.db_path.is_file())
            self.assertTrue(install.legacy_root.is_dir())
            self.assertEqual(tree_snapshot(install.legacy_root), source_before)

    def test_changed_pending_cleanup_source_fails_read_only_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = self.make_pending_cleanup_install(Path(tmp))
            with install.legacy_db_path.open("ab") as stream:
                stream.write(b"changed after pending cleanup was recorded")
            before = tree_snapshot(install.project_root)

            result = install.run("setup", "--read-only", "--json")

            self.assertEqual(result.returncode, 2)
            payload = json_payload(result)
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "setup_incomplete",
                    "message": "setup completed only partially; rerun setup",
                }],
            )
            self.assertEqual(
                payload["data"]["planned_writes"],
                LAYOUT_WRITES,
            )
            self.assertEqual(payload["data"]["completed_writes"], [])
            self.assertEqual(tree_snapshot(install.project_root), before)

    def test_unrecorded_retirement_entry_fails_read_only_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = self.make_pending_cleanup_install(Path(tmp))
            _, retirement_root = cleanup_roots(
                install.skill_root / "state",
                install.project_id,
            )
            retirement_root.mkdir()
            unrecorded = retirement_root / "unrecorded.txt"
            unrecorded.write_bytes(b"must not be removed")
            before = tree_snapshot(install.project_root)

            result = install.run("setup", "--read-only", "--json")

            self.assertEqual(result.returncode, 2)
            payload = json_payload(result)
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "setup_incomplete",
                    "message": "setup completed only partially; rerun setup",
                }],
            )
            self.assertEqual(
                payload["data"]["planned_writes"],
                LAYOUT_WRITES,
            )
            self.assertEqual(payload["data"]["completed_writes"], [])
            self.assertEqual(tree_snapshot(install.project_root), before)

    def test_pending_cleanup_prerequisite_never_repairs_due_viewer_in_both_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = self.make_pending_cleanup_install(Path(tmp))
            viewer = (
                install.fixed_root
                / "viewer"
                / "task-viewer.html"
            )
            viewer.unlink()
            _, retirement_root = cleanup_roots(
                install.skill_root / "state",
                install.project_id,
            )
            retirement_root.mkdir()
            (retirement_root / "unrecorded.txt").write_bytes(
                b"must not be removed"
            )
            before = tree_snapshot(install.project_root)

            for read_only in (True, False):
                with self.subTest(read_only=read_only):
                    arguments = ["setup"]
                    if read_only:
                        arguments.append("--read-only")
                    arguments.append("--json")
                    result = install.run(*arguments)

                    self.assertEqual(result.returncode, 2)
                    payload = json_payload(result)
                    self.assertEqual(
                        payload["errors"],
                        [{
                            "code": "setup_incomplete",
                            "message": (
                                "setup completed only partially; rerun setup"
                            ),
                        }],
                    )
                    self.assertEqual(
                        payload["data"]["planned_writes"],
                        LAYOUT_WRITES,
                    )
                    self.assertEqual(
                        payload["data"]["completed_writes"],
                        [],
                    )
                    self.assertEqual(
                        tree_snapshot(install.project_root),
                        before,
                    )

    def test_same_binding_legacy_missing_primary_restores_only_in_fixed_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v12_database(install, enabled=True)
            legacy_project_id = install.legacy_project_id
            with backup_service.managed_backup_lock(install.legacy_target):
                backup_service.publish_setup_backup(
                    install.legacy_target,
                    3,
                )
            source_backups = tuple(
                (install.legacy_root / "backups").glob(
                    "taskgov-backup-v1_*.sqlite"
                )
            )
            self.assertEqual(len(source_backups), 1)
            source_bytes = source_backups[0].read_bytes()
            install.legacy_db_path.unlink()

            preview = install.run("setup", "--read-only", "--json")

            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_data = json_payload(preview)["data"]
            self.assertEqual(
                preview_data["planned_writes"],
                LAYOUT_WRITES,
            )
            self.assertFalse(install.db_path.exists())
            self.assertEqual(source_backups[0].read_bytes(), source_bytes)

            completed = install.run("setup", "--json")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json_payload(completed)
            self.assertEqual(payload["project_id"], legacy_project_id)
            self.assertEqual(
                payload["data"]["completed_writes"],
                LAYOUT_WRITES,
            )
            self.assertTrue(install.db_path.is_file())
            self.assertTrue(install.legacy_root.is_dir())
            self.assertFalse(install.legacy_db_path.exists())
            self.assertEqual(source_backups[0].read_bytes(), source_bytes)
            with closing(connect(install.db_path)) as connection:
                self.assertEqual(current_schema_version(connection), 22)
                maintenance = connection.execute(
                    """
                    SELECT enabled_at, backup_interval_minutes,
                           backup_generations
                      FROM project_maintenance
                    """
                ).fetchone()
            self.assertIsNotNone(maintenance[0])
            self.assertEqual(tuple(maintenance[1:]), (30, 3))
            self.assertGreaterEqual(
                len(
                    tuple(
                        (install.fixed_root / "backups").glob(
                            "taskgov-backup-v1_*.sqlite"
                        )
                    )
                ),
                2,
            )

    def test_corrupt_private_backup_never_publishes_and_retry_preserves_residue(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            legacy_target = install.legacy_target
            create_v14_target(legacy_target)
            with backup_service.managed_backup_lock(legacy_target):
                backup_service.publish_setup_backup(legacy_target, 3)

            source_backup = next(
                (install.legacy_root / "backups").glob(
                    "taskgov-backup-v1_*.sqlite"
                )
            )
            source_database_bytes = install.legacy_db_path.read_bytes()
            source_backup_bytes = source_backup.read_bytes()
            real_copy = separation_service.copy_inventory
            corrupted = []

            def copy_then_corrupt(inventory, destination, *, skip_database):
                real_copy(inventory, destination, skip_database=skip_database)
                if destination.name != "candidate":
                    return
                staged_backup = next(
                    (destination / "backups").glob(
                        "taskgov-backup-v1_*.sqlite"
                    )
                )
                staged_backup.write_bytes(b"corrupt private-stage backup")
                corrupted.append(staged_backup)

            with mock.patch.object(
                separation_service,
                "copy_inventory",
                side_effect=copy_then_corrupt,
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
            self.assertEqual(len(corrupted), 1)
            self.assertEqual(failed.error_code, "project_state_unreadable")
            self.assertEqual(
                failed.data["planned_writes"],
                LAYOUT_WRITES,
            )
            self.assertEqual(failed.data["completed_writes"], [])
            self.assertFalse(install.db_path.exists())
            self.assertEqual(
                install.legacy_db_path.read_bytes(),
                source_database_bytes,
            )
            self.assertEqual(source_backup.read_bytes(), source_backup_bytes)
            self.assertEqual(
                list(
                    (install.skill_root / "state").glob(
                        ".current-stage-*"
                    )
                ),
                [],
            )

            # An unsealed nonempty preparation is preserved, never erased or
            # implicitly reconstructed merely because setup is invoked again.
            private_before = tree_snapshot(install.project_root)
            retried = install.run("setup", "--json")

            self.assertEqual(retried.returncode, 2, retried.stderr)
            retried_payload = json_payload(retried)
            self.assertEqual(retried_payload["errors"][0]["code"], "setup_incomplete")
            self.assertEqual(retried_payload["data"]["completed_writes"], [])
            self.assertEqual(tree_snapshot(install.project_root), private_before)
            self.assertFalse(install.db_path.exists())
            self.assertEqual(corrupted[0].read_bytes(), b"corrupt private-stage backup")

    def test_non_nul_backup_lock_is_inventoried_and_retained_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v14_target(install.legacy_target)
            backup_lock = (
                install.legacy_root
                / "backups"
                / "taskgov-backup.lock"
            )
            backup_lock.parent.mkdir(parents=True)
            backup_lock.write_bytes(b"X")

            completed = install.run("setup", "--json")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json_payload(completed)
            self.assertEqual(
                payload["data"]["planned_writes"],
                LAYOUT_WRITES,
            )
            self.assertEqual(
                payload["data"]["completed_writes"],
                LAYOUT_WRITES,
            )
            self.assertTrue(install.db_path.is_file())
            self.assertEqual(backup_lock.read_bytes(), b"X")
            with closing(sqlite3.connect(install.db_path)) as connection:
                cleanup = connection.execute(
                    """
                    SELECT legacy_cleanup_pending,
                           legacy_cleanup_inventory,
                           legacy_cleanup_fingerprint
                      FROM project_meta
                    """
                ).fetchone()
            self.assertEqual(cleanup, (0, None, None))
            source_before = tree_snapshot(install.legacy_root)

            replay = install.run("setup", "--json")

            self.assertEqual(replay.returncode, 0, replay.stderr)
            replay_data = json_payload(replay)["data"]
            self.assertEqual(replay_data["status"], "already_setup")
            self.assertEqual(replay_data["planned_writes"], [])
            self.assertEqual(replay_data["completed_writes"], [])
            self.assertEqual(tree_snapshot(install.legacy_root), source_before)

    def test_pending_cleanup_prerequisite_preserves_binding_before_any_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = self.make_pending_cleanup_install(root)
            initial = resolve_package_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            self.assertEqual(initial.binding, "matching")
            self.assertIsNotNone(initial.target)
            self.assertIsNotNone(initial.stored_project)
            stored = initial.stored_project
            assert stored is not None

            before = tree_snapshot(install.project_root)
            real_cleanup = setup_service._complete_pending_cleanup
            with (
                mock.patch.object(
                    separation_service, "zero_wait_artifact_lock",
                    wraps=separation_service.zero_wait_artifact_lock,
                ) as lock,
                mock.patch.object(
                    setup_service,
                    "_complete_pending_cleanup",
                    wraps=real_cleanup,
                ) as cleanup,
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
            self.assertEqual(failed.error_code, "setup_incomplete")
            self.assertEqual(failed.data["completed_writes"], [])
            lock.assert_not_called()
            cleanup.assert_not_called()
            self.assertEqual(tree_snapshot(install.project_root), before)
            with closing(sqlite3.connect(install.db_path)) as connection:
                durable = connection.execute(
                    """
                    SELECT canonical_path_hash, binding_generation,
                           legacy_cleanup_pending
                      FROM project_meta
                    """
                ).fetchone()
            self.assertEqual(
                durable,
                (stored.canonical_path_hash, stored.binding_generation, 1),
            )

    def test_owned_old_stage_prerequisite_preserves_residue_before_any_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = make_physical_install(root)
            create_v14_target(install.legacy_target)
            initial = resolve_package_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            self.assertEqual(initial.binding, "matching")
            self.assertIsNotNone(initial.project_id)
            state_root = initial.paths.state_root
            owned = setup_service.create_owned_stage(
                state_root,
                project_id=initial.project_id,
                inventory_fingerprint="8" * 64,
                stage_id="9" * 32,
            )
            self.assertIsNotNone(setup_service._inspect_setup_residue(initial))
            residue_before = tree_snapshot(install.project_root)
            with (
                mock.patch.object(
                    separation_service, "zero_wait_artifact_lock",
                    wraps=separation_service.zero_wait_artifact_lock,
                ) as lock,
                mock.patch.object(
                    setup_service,
                    "remove_stage_residue",
                    wraps=setup_service.remove_stage_residue,
                ) as remove,
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
            self.assertEqual(failed.error_code, "setup_incomplete")
            self.assertEqual(failed.data["completed_writes"], [])
            lock.assert_not_called()
            remove.assert_not_called()
            self.assertTrue(owned.owner_file.path.is_file())
            self.assertTrue(owned.stage_directory.path.is_dir())
            self.assertEqual(tree_snapshot(install.project_root), residue_before)


if __name__ == "__main__":
    unittest.main()
