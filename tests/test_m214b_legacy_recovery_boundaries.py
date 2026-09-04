from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    create_v10_target,
    create_v14_target,
    make_physical_install,
    remove_v18_evidence_ledger_for_test,
    tree_snapshot,
)
from tests.m214b_test_support import (
    inject_primary_candidate_metadata_conflict,
    publish_generations,
    replace_maintenance_pointer,
    replace_verification,
)
from tests.test_db_init import insert_task

from task_governance_tool import backup as backup_service
from task_governance_tool import setup as setup_service
from task_governance_tool.state_resolver import resolve_setup_project_state
from task_governance_tool.storage import DatabaseTarget


class M214BLegacyRecoveryBoundaryTests(unittest.TestCase):
    def test_legacy_primary_setup_rejects_structural_backup_task_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = install.legacy_target
            create_v14_target(target)
            with closing(sqlite3.connect(target.db_path)) as connection:
                insert_task(
                    connection,
                    task_id="tg_task_legacy_structural",
                    project_id=target.project.project_id,
                    title="Legacy structural backup task",
                )
                connection.commit()
            artifact = publish_generations(
                target,
                "2099-03-02T00:00:00Z",
            )[-1]
            replace_verification(
                artifact.path,
                "Legacy structural backup task",
                sqlite3.Binary(b"malformed"),
            )
            state_root = install.skill_root / "state"
            before_managed_state = tree_snapshot(state_root)

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
            self.assertEqual(result.data["planned_writes"], [])
            self.assertEqual(result.data["completed_writes"], [])
            self.assertEqual(tree_snapshot(state_root), before_managed_state)
            self.assertFalse(install.fixed_root.exists())

    def test_legacy_primary_rejects_candidate_metadata_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = install.legacy_target
            create_v14_target(target)
            artifact = publish_generations(
                target,
                "2099-02-01T00:00:00Z",
            )[-1]
            inject_primary_candidate_metadata_conflict(target, artifact)
            state_root = install.skill_root / "state"
            before_managed_state = tree_snapshot(state_root)

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
            self.assertEqual(result.data["planned_writes"], [])
            self.assertEqual(result.data["completed_writes"], [])
            self.assertEqual(tree_snapshot(state_root), before_managed_state)
            self.assertFalse(install.fixed_root.exists())

    def test_v10_legacy_primary_rejects_candidate_pointer_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = install.legacy_target
            create_v10_target(target)
            artifacts = publish_generations(
                target,
                "2098-03-01T00:00:00Z",
                "2099-03-01T00:00:00Z",
                retention=3,
            )
            generation_id = "tg_backup_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            replace_maintenance_pointer(
                artifacts[0].path,
                project_id=target.project.project_id,
                generation_id=generation_id,
                published_at="2097-03-01T00:00:00Z",
                retention=3,
            )
            replace_maintenance_pointer(
                target.db_path,
                project_id=target.project.project_id,
                generation_id=generation_id,
                published_at="2096-03-01T00:00:00Z",
                retention=3,
            )
            state_root = install.skill_root / "state"
            before_managed_state = tree_snapshot(state_root)

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
            self.assertEqual(result.data["planned_writes"], [])
            self.assertEqual(result.data["completed_writes"], [])
            self.assertEqual(tree_snapshot(state_root), before_managed_state)
            self.assertFalse(install.fixed_root.exists())

    def test_v10_older_fallback_points_to_mechanical_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = DatabaseTarget(
                project=install.legacy_target.project,
                db_path=install.db_path,
                explicit_db=True,
            )
            create_v10_target(target)
            with closing(sqlite3.connect(target.db_path)) as connection:
                insert_task(
                    connection,
                    task_id="tg_task_v10_older",
                    project_id=target.project.project_id,
                    title="V10 older task",
                )
                connection.commit()
            publish_generations(target, "2098-09-01T00:00:00Z", retention=2)
            with closing(sqlite3.connect(target.db_path)) as connection:
                insert_task(
                    connection,
                    task_id="tg_task_v10_newer",
                    project_id=target.project.project_id,
                    title="V10 newer task",
                )
                connection.commit()
            artifacts = publish_generations(
                target,
                "2099-09-01T00:00:00Z",
                retention=2,
            )
            head = artifacts[-1]
            replace_verification(head.path, "V10 newer task", "x" * 501)
            target.db_path.unlink()
            resolution = resolve_setup_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            candidate = backup_service.select_managed_backup_for_recovery(
                resolution.target
            )
            self.assertIsNotNone(candidate)
            self.assertIsNotNone(resolution.fixed_recovery)
            real_record = backup_service.record_setup_backup

            with mock.patch.object(
                backup_service,
                "record_setup_backup",
                wraps=real_record,
            ) as recorded:
                restored_version = backup_service.restore_managed_backup(
                    resolution.target,
                    candidate,
                    expected_recovery=resolution.fixed_recovery,
                )

            self.assertEqual(restored_version, 10)
            recorded.assert_called_once()
            self.assertEqual(recorded.call_args.args[1], head.metadata)
            with closing(sqlite3.connect(target.db_path)) as connection:
                pointer = connection.execute(
                    """
                    SELECT latest_backup_generation_id,
                           backup_last_success_at,
                           applied_backup_generations
                      FROM project_maintenance
                    """
                ).fetchone()
            self.assertEqual(
                tuple(pointer),
                (
                    head.metadata.generation_id,
                    head.metadata.published_at,
                    head.metadata.publication_retention,
                ),
            )

    def test_v10_phantom_predecessor_is_set_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = DatabaseTarget(
                project=install.legacy_target.project,
                db_path=install.db_path,
                explicit_db=True,
            )
            create_v10_target(target)
            artifacts = publish_generations(
                target,
                "2098-01-01T00:00:00Z",
                "2099-01-01T00:00:00Z",
                retention=3,
            )
            replace_maintenance_pointer(
                artifacts[-1].path,
                project_id=target.project.project_id,
                generation_id="tg_backup_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                published_at="2098-06-01T00:00:00Z",
                retention=3,
            )
            target.db_path.unlink()
            state_root = install.skill_root / "state"
            before_managed_state = tree_snapshot(state_root)

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
            self.assertEqual(result.data["planned_writes"], [])
            self.assertEqual(result.data["completed_writes"], [])
            self.assertEqual(tree_snapshot(state_root), before_managed_state)
            self.assertFalse(target.db_path.exists())

    def test_v10_absent_predecessor_id_cannot_collide_with_physical_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = DatabaseTarget(
                project=install.legacy_target.project,
                db_path=install.db_path,
                explicit_db=True,
            )
            create_v10_target(target)
            artifacts = publish_generations(
                target,
                "2098-02-01T00:00:00Z",
                "2099-02-01T00:00:00Z",
                retention=3,
            )
            first, later = artifacts
            replace_maintenance_pointer(
                first.path,
                project_id=target.project.project_id,
                generation_id=later.metadata.generation_id,
                published_at="2097-02-01T00:00:00Z",
                retention=3,
            )
            target.db_path.unlink()
            state_root = install.skill_root / "state"
            before_managed_state = tree_snapshot(state_root)

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
            self.assertEqual(result.data["planned_writes"], [])
            self.assertEqual(result.data["completed_writes"], [])
            self.assertEqual(tree_snapshot(state_root), before_managed_state)
            self.assertFalse(target.db_path.exists())

    def test_mixed_schema_older_fallback_can_complete_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = DatabaseTarget(
                project=install.legacy_target.project,
                db_path=install.db_path,
                explicit_db=True,
            )
            create_v10_target(
                target,
                enabled=True,
                generations=5,
            )
            publish_generations(
                target,
                "2026-01-01T00:00:00Z",
                retention=5,
            )

            migrated = install.run("setup", "--json")
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            added = install.run(
                "task",
                "add",
                "--title",
                "Mixed-schema rejected head",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            current_target = install.target
            head = publish_generations(
                current_target,
                "2099-10-01T00:00:00Z",
                retention=5,
            )[-1]
            with closing(sqlite3.connect(head.path)) as connection:
                remove_v18_evidence_ledger_for_test(connection)
            replace_verification(
                head.path,
                "Mixed-schema rejected head",
                "x" * 501,
            )
            install.db_path.unlink()

            recovered = install.run("setup", "--json")

            self.assertEqual(
                recovered.returncode,
                0,
                f"{recovered.stderr}\n{recovered.stdout}",
            )
            with closing(sqlite3.connect(install.db_path)) as connection:
                schema_version = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
                rejected_title_count = connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE title = ?",
                    ("Mixed-schema rejected head",),
                ).fetchone()[0]
            self.assertEqual(schema_version, 22)
            self.assertEqual(rejected_title_count, 0)
