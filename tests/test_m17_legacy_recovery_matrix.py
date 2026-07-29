from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from tests.m14_test_support import file_snapshot, make_physical_install
from tests.test_state_resolver import create_backup_artifact

from task_governance_tool import setup as setup_service
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.storage import (
    MigrationBackupMetadata,
    SCHEMA_VERSION,
    connect_readonly,
    current_schema_version,
    initialize_database,
    record_managed_backup,
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
    initialize_database(target)
    old_artifacts: list[Path] = []
    for index in range(1, 21):
        metadata = _metadata(index, retention=20)
        old_artifacts.append(create_backup_artifact(target, metadata))
        record_managed_backup(target, metadata)

    newest = _metadata(21, retention=1)
    create_backup_artifact(target, newest)
    if shape.newest_row_present:
        record_managed_backup(target, newest)

    if shape.row_only_oldest:
        old_artifacts[0].unlink()
    if shape.pruned_old_files:
        for artifact in old_artifacts[: shape.pruned_old_files]:
            artifact.unlink()
    if not shape.primary_present:
        target.db_path.unlink()
    return target, newest


class M17LegacyRecoveryMatrixTests(unittest.TestCase):
    def test_legacy_private_stage_converges_each_retention_one_shape(self):
        for shape in POSITIVE_SHAPES:
            with self.subTest(shape=shape.name), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                legacy_target, newest = _build_positive_source(install, shape)
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
                self.assertEqual(
                    generations,
                    [(newest.generation_id, 1)],
                )
                self.assertEqual(
                    maintenance,
                    (newest.generation_id, 1, 1),
                )
                backup_files = sorted(
                    resolution.target.resolved_backups_path.glob(
                        "taskgov-backup-v1_*.sqlite"
                    ),
                    key=lambda path: path.name,
                )
                self.assertEqual(len(backup_files), 1)
                self.assertIn(
                    newest.generation_id[10:],
                    backup_files[0].name,
                )

    def test_missing_primary_invalid_generation_relations_fail_closed(self):
        for shape in ("two_file_only", "twenty_one_missing_rows"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                target = install.legacy_target
                initialize_database(target)

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
                    for index in range(1, 22):
                        record_managed_backup(
                            target,
                            _metadata(index, retention=20),
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


if __name__ == "__main__":
    unittest.main()
