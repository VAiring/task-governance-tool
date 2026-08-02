from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    create_v14_target,
    file_snapshot,
    make_physical_install,
)
from tests.test_state_resolver import (
    create_backup_artifact,
    insert_generation_rows,
)
from tests.test_db_init import insert_task

from task_governance_tool import setup as setup_service
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.storage import (
    MigrationBackupMetadata,
    SCHEMA_VERSION,
    connect_readonly,
    current_schema_version,
)


@dataclass(frozen=True)
class _PositiveShape:
    name: str
    primary_present: bool
    newest_row_present: bool
    row_only_oldest: bool = False
    pruned_old_files: int = 0


POSITIVE_SHAPES = (
    _PositiveShape(
        name="primary_present_pre_row",
        primary_present=True,
        newest_row_present=False,
    ),
    _PositiveShape(
        name="primary_present_post_row",
        primary_present=True,
        newest_row_present=True,
    ),
    _PositiveShape(
        name="primary_present_interrupted_file_before_row_prune",
        primary_present=True,
        newest_row_present=True,
        row_only_oldest=True,
    ),
    _PositiveShape(
        name="missing_primary_fully_pruned",
        primary_present=False,
        newest_row_present=False,
        pruned_old_files=20,
    ),
    _PositiveShape(
        name="missing_primary_partially_pruned",
        primary_present=False,
        newest_row_present=False,
        pruned_old_files=10,
    ),
)


def _metadata(index: int, *, retention: int) -> MigrationBackupMetadata:
    return MigrationBackupMetadata(
        generation_id=f"tg_backup_{index:032x}",
        published_at=f"2026-07-29T01:02:{index:02d}Z",
        publication_retention=retention,
    )


def _record_generation(target, metadata: MigrationBackupMetadata) -> None:
    insert_generation_rows(target, (metadata,))
    with closing(sqlite3.connect(target.db_path)) as connection:
        connection.execute(
            """
            UPDATE project_maintenance
               SET latest_backup_generation_id = ?,
                   backup_last_success_at = ?,
                   applied_backup_generations = ?
             WHERE project_id = ?
            """,
            (
                metadata.generation_id,
                metadata.published_at,
                metadata.publication_retention,
                target.project.project_id,
            ),
        )
        connection.commit()


def _run_setup(install):
    return setup_service.run_setup(
        repo=str(install.project_root),
        repo_explicit=True,
        script_path=install.entrypoint,
        read_only=False,
        backup_interval_minutes=None,
        backup_generations=1,
    )


def _build_positive_source(install, shape: _PositiveShape):
    target = install.legacy_target
    create_v14_target(target)
    old_artifacts: list[Path] = []
    for index in range(1, 21):
        metadata = _metadata(index, retention=20)
        old_artifacts.append(create_backup_artifact(target, metadata))
        _record_generation(target, metadata)

    newest = _metadata(21, retention=1)
    create_backup_artifact(target, newest)
    if shape.newest_row_present:
        _record_generation(target, newest)

    if shape.row_only_oldest:
        old_artifacts[0].unlink()
    if shape.pruned_old_files:
        for artifact in old_artifacts[: shape.pruned_old_files]:
            artifact.unlink()
    if not shape.primary_present:
        target.db_path.unlink()
    return target, newest


def _build_local_invalid_newer_source(install):
    target = install.legacy_target
    create_v14_target(target)
    with closing(sqlite3.connect(target.db_path)) as connection:
        insert_task(
            connection,
            task_id="tg_task_legacy_older",
            project_id=target.project.project_id,
            title="Legacy older task",
        )
        connection.commit()
    older = _metadata(1, retention=2)
    older_path = create_backup_artifact(target, older)
    _record_generation(target, older)
    with closing(sqlite3.connect(target.db_path)) as connection:
        insert_task(
            connection,
            task_id="tg_task_legacy_selected",
            project_id=target.project.project_id,
            title="Legacy selected task",
        )
        connection.commit()
    selected = _metadata(2, retention=2)
    selected_path = create_backup_artifact(target, selected)
    _record_generation(target, selected)
    with closing(sqlite3.connect(target.db_path)) as connection:
        insert_task(
            connection,
            task_id="tg_task_legacy_newer",
            project_id=target.project.project_id,
            title="Legacy newer task",
        )
        connection.commit()
    newer = _metadata(3, retention=2)
    newer_path = create_backup_artifact(target, newer)
    with closing(sqlite3.connect(newer_path)) as connection:
        cursor = connection.execute(
            "UPDATE tasks SET verification = ? WHERE task_id = ?",
            ("x" * 501, "tg_task_legacy_newer"),
        )
        if cursor.rowcount != 1:
            raise AssertionError("newer legacy task was not updated")
        connection.commit()
    target.db_path.unlink()
    return target, older_path, selected_path, newer_path


class M17LegacyRecoveryMatrixTests(unittest.TestCase):
    def test_legacy_private_stage_converges_each_retention_one_shape(self):
        for shape in POSITIVE_SHAPES:
            with self.subTest(shape=shape.name), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                legacy_target, _ = _build_positive_source(install, shape)
                legacy_project_id = legacy_target.project.project_id

                result = _run_setup(install)

                self.assertTrue(result.ok, result)
                self.assertFalse(install.legacy_root.exists())
                resolution = resolve_project_state(
                    skill_root=install.skill_root,
                    repo=install.project_root,
                )
                self.assertIsNone(resolution.error_code)
                self.assertEqual(resolution.layout, "fixed_current_v1")
                self.assertEqual(resolution.binding, "matching")
                self.assertEqual(resolution.project_id, legacy_project_id)
                self.assertIsNotNone(resolution.target)
                self.assertEqual(
                    resolution.target.project.project_id,
                    legacy_project_id,
                )
                self.assertEqual(
                    resolution.stored_project.identity_scheme,
                    "legacy_path_v1",
                )

                with closing(
                    connect_readonly(resolution.target.db_path)
                ) as connection:
                    self.assertEqual(
                        current_schema_version(connection),
                        SCHEMA_VERSION,
                    )
                    project_id = connection.execute(
                        "SELECT project_id FROM project_meta"
                    ).fetchone()[0]
                    generations = [
                        tuple(row)
                        for row in connection.execute(
                            """
                            SELECT generation_id, publication_retention
                              FROM managed_backup_generations
                             ORDER BY published_at, generation_id
                            """
                        ).fetchall()
                    ]
                    maintenance = tuple(
                        connection.execute(
                            """
                            SELECT latest_backup_generation_id,
                                   applied_backup_generations,
                                   backup_generations
                              FROM project_maintenance
                            """
                        ).fetchone()
                    )

                self.assertEqual(project_id, legacy_project_id)
                self.assertEqual(len(generations), 1)
                generation_id, retention = generations[0]
                self.assertRegex(generation_id, r"^tg_backup_[0-9a-f]{32}$")
                self.assertEqual(retention, 1)
                self.assertEqual(
                    maintenance,
                    (generation_id, 1, 1),
                )
                backup_files = sorted(
                    resolution.target.resolved_backups_path.glob(
                        "taskgov-backup-v1_*.sqlite"
                    ),
                    key=lambda path: path.name,
                )
                self.assertEqual(len(backup_files), 1)
                self.assertIn(
                    generation_id[10:],
                    backup_files[0].name,
                )

    def test_missing_primary_invalid_generation_relations_fail_closed(self):
        for shape in ("two_file_only", "twenty_one_missing_rows"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                target = install.legacy_target
                create_v14_target(target)

                if shape == "two_file_only":
                    create_backup_artifact(
                        target,
                        _metadata(1, retention=20),
                    )
                    create_backup_artifact(
                        target,
                        _metadata(2, retention=1),
                    )
                else:
                    insert_generation_rows(
                        target,
                        tuple(
                            _metadata(index, retention=20)
                            for index in range(1, 22)
                        ),
                    )
                    create_backup_artifact(
                        target,
                        _metadata(22, retention=1),
                    )
                target.db_path.unlink()
                before = file_snapshot(install.skill_root)

                result = _run_setup(install)

                self.assertFalse(result.ok)
                self.assertEqual(
                    result.error_code,
                    "project_state_unreadable",
                )
                self.assertEqual(result.data["planned_writes"], [])
                self.assertEqual(result.data["completed_writes"], [])
                self.assertFalse(install.db_path.exists())
                self.assertEqual(
                    file_snapshot(install.skill_root),
                    before,
                )

    def test_backup_only_legacy_skips_newer_local_invalid_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            _build_local_invalid_newer_source(install)

            result = _run_setup(install)

            self.assertTrue(result.ok, result)
            resolution = resolve_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            self.assertIsNotNone(resolution.target)
            with closing(sqlite3.connect(resolution.target.db_path)) as connection:
                titles = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT title FROM tasks"
                    ).fetchall()
                }
            self.assertEqual(
                titles,
                {"Legacy older task", "Legacy selected task"},
            )

    def test_backup_only_legacy_revalidates_selected_source_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            _, _, selected_path, _ = _build_local_invalid_newer_source(
                install
            )
            real_copy = setup_service.copy_database_snapshot
            changed_bytes: bytes | None = None

            def invalidate_selected_before_copy(**kwargs):
                nonlocal changed_bytes
                self.assertEqual(kwargs["source_path"], selected_path)
                before = selected_path.stat()
                with closing(sqlite3.connect(selected_path)) as connection:
                    cursor = connection.execute(
                        "UPDATE tasks SET verification = ? WHERE task_id = ?",
                        ("x" * 501, "tg_task_legacy_selected"),
                    )
                    self.assertEqual(cursor.rowcount, 1)
                    connection.commit()
                changed = selected_path.stat()
                self.assertEqual(changed.st_size, before.st_size)
                os.utime(
                    selected_path,
                    ns=(changed.st_atime_ns, before.st_mtime_ns),
                )
                after = selected_path.stat()
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
                changed_bytes = selected_path.read_bytes()
                return real_copy(**kwargs)

            with mock.patch.object(
                setup_service,
                "copy_database_snapshot",
                side_effect=invalidate_selected_before_copy,
            ) as copied:
                result = _run_setup(install)

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_restore_failed")
            self.assertEqual(result.data["completed_writes"], [])
            copied.assert_called_once()
            self.assertFalse(install.fixed_root.exists())
            self.assertIsNotNone(changed_bytes)
            self.assertEqual(selected_path.read_bytes(), changed_bytes)
            state_root = install.skill_root / "state"
            self.assertEqual(list(state_root.glob(".current-stage-*")), [])

    def test_backup_only_legacy_maps_post_plan_reselection_to_restore_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            _, _, selected_path, _ = _build_local_invalid_newer_source(
                install
            )
            real_lock = setup_service.state_transition_lock
            changed_bytes: bytes | None = None

            @contextmanager
            def invalidate_selected_after_plan(state_root):
                nonlocal changed_bytes
                with real_lock(state_root):
                    before = selected_path.stat()
                    with closing(sqlite3.connect(selected_path)) as connection:
                        cursor = connection.execute(
                            "UPDATE tasks SET verification = ? WHERE task_id = ?",
                            ("x" * 501, "tg_task_legacy_selected"),
                        )
                        self.assertEqual(cursor.rowcount, 1)
                        connection.commit()
                    changed = selected_path.stat()
                    self.assertEqual(changed.st_size, before.st_size)
                    os.utime(
                        selected_path,
                        ns=(changed.st_atime_ns, before.st_mtime_ns),
                    )
                    changed_bytes = selected_path.read_bytes()
                    yield

            with mock.patch.object(
                setup_service,
                "state_transition_lock",
                invalidate_selected_after_plan,
            ):
                result = _run_setup(install)

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_restore_failed")
            self.assertEqual(result.data["completed_writes"], [])
            self.assertFalse(install.fixed_root.exists())
            self.assertIsNotNone(changed_bytes)
            self.assertEqual(selected_path.read_bytes(), changed_bytes)
            state_root = install.skill_root / "state"
            self.assertEqual(list(state_root.glob(".current-stage-*")), [])

    def test_backup_only_legacy_revalidates_inventory_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            _, older_path, _, _ = _build_local_invalid_newer_source(install)
            real_resolve_staged = setup_service.resolve_staged_project_state
            changed_bytes: bytes | None = None

            def invalidate_nonselected_candidate(**kwargs):
                nonlocal changed_bytes
                result = real_resolve_staged(**kwargs)
                before = older_path.stat()
                with closing(sqlite3.connect(older_path)) as connection:
                    cursor = connection.execute(
                        "UPDATE tasks SET verification = ? WHERE task_id = ?",
                        ("x" * 501, "tg_task_legacy_older"),
                    )
                    self.assertEqual(cursor.rowcount, 1)
                    connection.commit()
                changed = older_path.stat()
                self.assertEqual(changed.st_size, before.st_size)
                os.utime(
                    older_path,
                    ns=(changed.st_atime_ns, before.st_mtime_ns),
                )
                after = older_path.stat()
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
                changed_bytes = older_path.read_bytes()
                return result

            with mock.patch.object(
                setup_service,
                "resolve_staged_project_state",
                side_effect=invalidate_nonselected_candidate,
            ) as staged:
                result = _run_setup(install)

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_restore_failed")
            self.assertEqual(result.data["completed_writes"], [])
            staged.assert_called_once()
            self.assertFalse(install.fixed_root.exists())
            self.assertIsNotNone(changed_bytes)
            self.assertEqual(older_path.read_bytes(), changed_bytes)
            state_root = install.skill_root / "state"
            self.assertEqual(list(state_root.glob(".current-stage-*")), [])


if __name__ == "__main__":
    unittest.main()
