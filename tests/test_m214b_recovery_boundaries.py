from __future__ import annotations

import os
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
)
from tests.test_db_init import insert_task
from tests.test_m17_recovery_hardening import (
    _relocate_install,
    _run_setup,
    _setup_current,
)

from task_governance_tool import backup as backup_service
from task_governance_tool import setup as setup_service
from task_governance_tool.state_resolver import (
    resolve_project_state,
    resolve_setup_project_state,
)
from task_governance_tool.storage import DatabaseTarget, StorageError


def _publish_generations(target, *timestamps: str, retention: int = 4):
    for timestamp in timestamps:
        with (
            mock.patch.object(
                backup_service,
                "utc_now",
                return_value=timestamp,
            ),
            backup_service.managed_backup_lock(target),
        ):
            backup_service.publish_setup_backup(target, retention)
    return backup_service._discover(target)


def _replace_verification(path: Path, title: str, value: object) -> None:
    with closing(sqlite3.connect(path)) as connection:
        cursor = connection.execute(
            "UPDATE tasks SET verification = ? WHERE title = ?",
            (value, title),
        )
        if cursor.rowcount != 1:
            raise AssertionError("recovery test task was not updated")
        connection.commit()


def _restore_temporaries(target) -> list[Path]:
    return list(target.db_path.parent.glob(".taskgov-restore-*.tmp"))


def _restore_original_mtime(path: Path, before: os.stat_result) -> None:
    changed = path.stat()
    if changed.st_size != before.st_size:
        raise AssertionError("test mutation changed the SQLite file size")
    os.utime(path, ns=(changed.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    if (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise AssertionError("test mutation did not preserve file identity")


def _replace_maintenance_pointer(
    path: Path,
    *,
    project_id: str,
    generation_id: str,
    published_at: str,
    retention: int,
) -> None:
    with closing(sqlite3.connect(path)) as connection:
        cursor = connection.execute(
            """
            UPDATE project_maintenance
               SET latest_backup_generation_id = ?,
                   backup_last_success_at = ?,
                   applied_backup_generations = ?
             WHERE project_id = ?
            """,
            (generation_id, published_at, retention, project_id),
        )
        if cursor.rowcount != 1:
            raise AssertionError("maintenance pointer was not updated")
        connection.commit()


def _inject_primary_candidate_metadata_conflict(target, artifact) -> None:
    generation_id = "tg_backup_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with closing(sqlite3.connect(target.db_path)) as connection:
        connection.execute(
            """
            INSERT INTO managed_backup_generations(
              generation_id,
              project_id,
              published_at,
              publication_retention
            ) VALUES (?, ?, ?, ?)
            """,
            (
                generation_id,
                target.project.project_id,
                "2097-02-01T00:00:00Z",
                artifact.metadata.publication_retention,
            ),
        )
        connection.commit()
    conflicting_published_at = "2098-02-01T00:00:00Z"
    with closing(sqlite3.connect(artifact.path)) as connection:
        connection.execute(
            """
            INSERT INTO managed_backup_generations(
              generation_id,
              project_id,
              published_at,
              publication_retention
            ) VALUES (?, ?, ?, ?)
            """,
            (
                generation_id,
                target.project.project_id,
                conflicting_published_at,
                artifact.metadata.publication_retention,
            ),
        )
        connection.commit()
    _replace_maintenance_pointer(
        artifact.path,
        project_id=target.project.project_id,
        generation_id=generation_id,
        published_at=conflicting_published_at,
        retention=artifact.metadata.publication_retention,
    )


class M214BRecoveryBoundaryTests(unittest.TestCase):
    def test_fixed_primary_setup_rejects_structural_backup_task_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "Fixed structural backup task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifact = _publish_generations(
                target,
                "2099-03-01T00:00:00Z",
            )[-1]
            _replace_verification(
                artifact.path,
                "Fixed structural backup task",
                sqlite3.Binary(b"malformed"),
            )
            primary_bytes = target.db_path.read_bytes()
            backup_bytes = artifact.path.read_bytes()

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
            self.assertEqual(target.db_path.read_bytes(), primary_bytes)
            self.assertEqual(artifact.path.read_bytes(), backup_bytes)

    def test_fixed_primary_remains_authoritative_over_local_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "Fixed local rejection task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifact = _publish_generations(
                target,
                "2099-03-01T00:00:01Z",
            )[-1]
            _replace_verification(
                artifact.path,
                "Fixed local rejection task",
                "x" * 501,
            )

            result = setup_service.run_setup(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
                read_only=False,
                backup_interval_minutes=None,
                backup_generations=None,
            )

            self.assertTrue(result.ok, result)
            with closing(sqlite3.connect(target.db_path)) as connection:
                verification = connection.execute(
                    "SELECT verification FROM tasks WHERE title = ?",
                    ("Fixed local rejection task",),
                ).fetchone()[0]
            self.assertEqual(verification, "")

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
            artifact = _publish_generations(
                target,
                "2099-03-02T00:00:00Z",
            )[-1]
            _replace_verification(
                artifact.path,
                "Legacy structural backup task",
                sqlite3.Binary(b"malformed"),
            )
            primary_bytes = target.db_path.read_bytes()
            backup_bytes = artifact.path.read_bytes()

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
            self.assertFalse(install.fixed_root.exists())
            self.assertEqual(target.db_path.read_bytes(), primary_bytes)
            self.assertEqual(artifact.path.read_bytes(), backup_bytes)

    def test_fixed_primary_rejects_candidate_metadata_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            artifact = _publish_generations(
                target,
                "2099-02-01T00:00:00Z",
            )[-1]
            _inject_primary_candidate_metadata_conflict(target, artifact)
            primary_bytes = target.db_path.read_bytes()
            backup_bytes = artifact.path.read_bytes()

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
            self.assertEqual(target.db_path.read_bytes(), primary_bytes)
            self.assertEqual(artifact.path.read_bytes(), backup_bytes)

    def test_legacy_primary_rejects_candidate_metadata_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = install.legacy_target
            create_v14_target(target)
            artifact = _publish_generations(
                target,
                "2099-02-01T00:00:00Z",
            )[-1]
            _inject_primary_candidate_metadata_conflict(target, artifact)
            primary_bytes = target.db_path.read_bytes()
            backup_bytes = artifact.path.read_bytes()

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
            self.assertFalse(install.fixed_root.exists())
            self.assertEqual(target.db_path.read_bytes(), primary_bytes)
            self.assertEqual(artifact.path.read_bytes(), backup_bytes)

    def test_v10_legacy_primary_rejects_candidate_pointer_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = install.legacy_target
            create_v10_target(target)
            artifacts = _publish_generations(
                target,
                "2098-03-01T00:00:00Z",
                "2099-03-01T00:00:00Z",
                retention=3,
            )
            generation_id = "tg_backup_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            _replace_maintenance_pointer(
                artifacts[0].path,
                project_id=target.project.project_id,
                generation_id=generation_id,
                published_at="2097-03-01T00:00:00Z",
                retention=3,
            )
            _replace_maintenance_pointer(
                target.db_path,
                project_id=target.project.project_id,
                generation_id=generation_id,
                published_at="2096-03-01T00:00:00Z",
                retention=3,
            )
            primary_bytes = target.db_path.read_bytes()
            backup_bytes = {
                artifact.path: artifact.path.read_bytes()
                for artifact in artifacts
            }

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
            self.assertFalse(install.fixed_root.exists())
            self.assertEqual(target.db_path.read_bytes(), primary_bytes)
            self.assertEqual(
                {path: path.read_bytes() for path in backup_bytes},
                backup_bytes,
            )

    def test_selected_older_repository_corruption_is_set_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "Candidate repository task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifacts = _publish_generations(
                target,
                "2097-04-01T00:00:00Z",
                "2098-04-01T00:00:00Z",
                "2099-04-01T00:00:00Z",
            )
            selected = artifacts[-2]
            newest = artifacts[-1]
            _replace_verification(
                newest.path,
                "Candidate repository task",
                "x" * 501,
            )
            with closing(sqlite3.connect(selected.path)) as connection:
                row = connection.execute(
                    """
                    SELECT generation_id, publication_retention
                      FROM managed_backup_generations
                     ORDER BY published_at, generation_id
                     LIMIT 1
                    """
                ).fetchone()
                self.assertIsNotNone(row)
                replacement = 3 if int(row[1]) != 3 else 4
                cursor = connection.execute(
                    """
                    UPDATE managed_backup_generations
                       SET publication_retention = ?
                     WHERE generation_id = ?
                    """,
                    (replacement, str(row[0])),
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
            self.assertEqual(result.data["planned_writes"], [])
            restore.assert_not_called()
            self.assertFalse(target.db_path.exists())

    def test_structural_drift_after_selection_fails_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            artifacts = _publish_generations(
                target,
                "2097-05-01T00:00:00Z",
                "2098-05-01T00:00:00Z",
                "2099-05-01T00:00:00Z",
            )
            selected_path = artifacts[-1].path
            target.db_path.unlink()
            real_restore = setup_service.restore_managed_backup

            def corrupt_after_selection(
                restore_target,
                candidate,
                *,
                expected_recovery,
            ):
                before = selected_path.stat()
                with closing(sqlite3.connect(selected_path)) as connection:
                    row = connection.execute(
                        """
                        SELECT generation_id
                          FROM managed_backup_generations
                         ORDER BY published_at, generation_id
                         LIMIT 1
                        """
                    ).fetchone()
                    self.assertIsNotNone(row)
                    cursor = connection.execute(
                        """
                        DELETE FROM managed_backup_generations
                         WHERE generation_id = ?
                        """,
                        (str(row[0]),),
                    )
                    self.assertEqual(cursor.rowcount, 1)
                    connection.commit()
                _restore_original_mtime(selected_path, before)
                return real_restore(
                    restore_target,
                    candidate,
                    expected_recovery=expected_recovery,
                )

            with mock.patch.object(
                setup_service,
                "restore_managed_backup",
                side_effect=corrupt_after_selection,
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
            self.assertEqual(_restore_temporaries(target), [])

    def test_final_deep_validation_follows_weak_inventory_rescan(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            artifacts = _publish_generations(
                target,
                "2097-05-02T00:00:00Z",
                "2098-05-02T00:00:00Z",
                "2099-05-02T00:00:00Z",
            )
            selected_path = artifacts[-1].path
            target.db_path.unlink()
            real_inventory = backup_service._require_recovery_inventory
            calls = 0

            def corrupt_during_final_inventory(restore_target, candidate):
                nonlocal calls
                calls += 1
                if calls == 3:
                    before = selected_path.stat()
                    with closing(sqlite3.connect(selected_path)) as connection:
                        row = connection.execute(
                            """
                            SELECT generation_id
                              FROM managed_backup_generations
                             ORDER BY published_at, generation_id
                             LIMIT 1
                            """
                        ).fetchone()
                        self.assertIsNotNone(row)
                        cursor = connection.execute(
                            """
                            DELETE FROM managed_backup_generations
                             WHERE generation_id = ?
                            """,
                            (str(row[0]),),
                        )
                        self.assertEqual(cursor.rowcount, 1)
                        connection.commit()
                    _restore_original_mtime(selected_path, before)
                return real_inventory(restore_target, candidate)

            with mock.patch.object(
                backup_service,
                "_require_recovery_inventory",
                side_effect=corrupt_during_final_inventory,
            ):
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
            self.assertEqual(calls, 3)
            self.assertFalse(target.db_path.exists())
            self.assertEqual(_restore_temporaries(target), [])

    def test_invalid_to_valid_drift_never_reselects_after_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "Rejected head task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifacts = _publish_generations(
                target,
                "2098-06-01T00:00:00Z",
                "2099-06-01T00:00:00Z",
            )
            rejected = artifacts[-1].path
            _replace_verification(
                rejected,
                "Rejected head task",
                "x" * 501,
            )
            target.db_path.unlink()
            real_restore = setup_service.restore_managed_backup

            def make_head_eligible(
                restore_target,
                candidate,
                *,
                expected_recovery,
            ):
                before = rejected.stat()
                _replace_verification(
                    rejected,
                    "Rejected head task",
                    "x" * 500,
                )
                _restore_original_mtime(rejected, before)
                return real_restore(
                    restore_target,
                    candidate,
                    expected_recovery=expected_recovery,
                )

            with mock.patch.object(
                setup_service,
                "restore_managed_backup",
                side_effect=make_head_eligible,
            ):
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
            self.assertFalse(target.db_path.exists())
            self.assertEqual(_restore_temporaries(target), [])

    def test_candidate_sidecar_case_variants_are_set_fatal(self):
        cases = ("-journal", "-JOURNAL", "-WAL", "-SHM", "whole-name")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                install, target, _ = _setup_current(Path(tmp))
                artifact = _publish_generations(
                    target,
                    "2099-07-01T00:00:00Z",
                )[-1]
                sidecar = (
                    artifact.path.with_name(
                        f"{artifact.path.name.upper()}-JOURNAL"
                    )
                    if case == "whole-name"
                    else Path(f"{artifact.path}{case}")
                )
                sidecar.write_bytes(b"")
                target.db_path.unlink()

                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

                self.assertFalse(result.ok)
                self.assertEqual(
                    result.error_code,
                    "project_state_unreadable",
                )
                self.assertEqual(result.data["planned_writes"], [])
                self.assertFalse(target.db_path.exists())
                self.assertTrue(sidecar.exists())

    def test_fixed_temporary_content_is_revalidated_after_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "Temporary validation task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            _publish_generations(target, "2099-08-01T00:00:00Z")
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
            real_prepare = backup_service._prepare_recovered_repository

            def invalidate_temporary(
                temporary_target,
                selected,
                *,
                expected_snapshot,
            ):
                real_prepare(
                    temporary_target,
                    selected,
                    expected_snapshot=expected_snapshot,
                )
                _replace_verification(
                    temporary_target.db_path,
                    "Temporary validation task",
                    "x" * 501,
                )

            with mock.patch.object(
                backup_service,
                "_prepare_recovered_repository",
                side_effect=invalidate_temporary,
            ):
                with self.assertRaises(StorageError) as caught:
                    backup_service.restore_managed_backup(
                        resolution.target,
                        candidate,
                        expected_recovery=resolution.fixed_recovery,
                    )

            self.assertEqual(caught.exception.code, "setup_restore_failed")
            self.assertFalse(target.db_path.exists())
            self.assertEqual(_restore_temporaries(target), [])

    def test_fixed_temporary_structure_is_validated_before_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            _publish_generations(
                target,
                "2097-08-02T00:00:00Z",
                "2098-08-02T00:00:00Z",
                "2099-08-02T00:00:00Z",
            )
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
            real_prepare = backup_service._prepare_recovered_repository

            def corrupt_before_normalization(
                temporary_target,
                selected,
                *,
                expected_snapshot,
            ):
                with closing(sqlite3.connect(temporary_target.db_path)) as connection:
                    row = connection.execute(
                        """
                        SELECT generation_id
                          FROM managed_backup_generations
                         ORDER BY published_at, generation_id
                         LIMIT 1
                        """
                    ).fetchone()
                    self.assertIsNotNone(row)
                    cursor = connection.execute(
                        """
                        DELETE FROM managed_backup_generations
                         WHERE generation_id = ?
                        """,
                        (str(row[0]),),
                    )
                    self.assertEqual(cursor.rowcount, 1)
                    connection.commit()
                return real_prepare(
                    temporary_target,
                    selected,
                    expected_snapshot=expected_snapshot,
                )

            with mock.patch.object(
                backup_service,
                "_prepare_recovered_repository",
                side_effect=corrupt_before_normalization,
            ):
                with self.assertRaises(StorageError) as caught:
                    backup_service.restore_managed_backup(
                        resolution.target,
                        candidate,
                        expected_recovery=resolution.fixed_recovery,
                    )

            self.assertEqual(caught.exception.code, "setup_restore_failed")
            self.assertFalse(target.db_path.exists())
            self.assertEqual(_restore_temporaries(target), [])

    def test_fixed_temporary_structure_is_revalidated_after_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            _publish_generations(target, "2099-08-03T00:00:00Z")
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
            real_prepare = backup_service._prepare_recovered_repository

            def corrupt_after_normalization(
                temporary_target,
                selected,
                *,
                expected_snapshot,
            ):
                real_prepare(
                    temporary_target,
                    selected,
                    expected_snapshot=expected_snapshot,
                )
                with closing(sqlite3.connect(temporary_target.db_path)) as connection:
                    connection.execute("DROP INDEX idx_tasks_project_status")
                    connection.commit()

            with mock.patch.object(
                backup_service,
                "_prepare_recovered_repository",
                side_effect=corrupt_after_normalization,
            ):
                with self.assertRaises(StorageError) as caught:
                    backup_service.restore_managed_backup(
                        resolution.target,
                        candidate,
                        expected_recovery=resolution.fixed_recovery,
                    )

            self.assertEqual(caught.exception.code, "setup_restore_failed")
            self.assertFalse(target.db_path.exists())
            self.assertEqual(_restore_temporaries(target), [])

    def test_generation_metadata_is_globally_immutable_across_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            artifacts = _publish_generations(
                target,
                "2098-08-04T00:00:00Z",
                "2099-08-04T00:00:00Z",
                retention=3,
            )
            first, later = artifacts
            with closing(sqlite3.connect(first.path)) as connection:
                connection.execute(
                    """
                    INSERT INTO managed_backup_generations(
                      generation_id,
                      project_id,
                      published_at,
                      publication_retention
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        later.metadata.generation_id,
                        target.project.project_id,
                        "2097-08-04T00:00:00Z",
                        3,
                    ),
                )
                cursor = connection.execute(
                    """
                    UPDATE project_maintenance
                       SET latest_backup_generation_id = ?,
                           backup_last_success_at = ?,
                           applied_backup_generations = ?
                     WHERE project_id = ?
                    """,
                    (
                        later.metadata.generation_id,
                        "2097-08-04T00:00:00Z",
                        3,
                        target.project.project_id,
                    ),
                )
                self.assertEqual(cursor.rowcount, 1)
                connection.commit()
            backup_bytes = {
                item.path: item.path.read_bytes()
                for item in artifacts
            }
            target.db_path.unlink()

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
            self.assertFalse(target.db_path.exists())
            self.assertEqual(
                {path: path.read_bytes() for path in backup_bytes},
                backup_bytes,
            )

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
            _publish_generations(target, "2098-09-01T00:00:00Z", retention=2)
            with closing(sqlite3.connect(target.db_path)) as connection:
                insert_task(
                    connection,
                    task_id="tg_task_v10_newer",
                    project_id=target.project.project_id,
                    title="V10 newer task",
                )
                connection.commit()
            artifacts = _publish_generations(
                target,
                "2099-09-01T00:00:00Z",
                retention=2,
            )
            head = artifacts[-1]
            _replace_verification(
                head.path,
                "V10 newer task",
                "x" * 501,
            )
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
            artifacts = _publish_generations(
                target,
                "2098-01-01T00:00:00Z",
                "2099-01-01T00:00:00Z",
                retention=3,
            )
            head = artifacts[-1]
            with closing(sqlite3.connect(head.path)) as connection:
                cursor = connection.execute(
                    """
                    UPDATE project_maintenance
                       SET latest_backup_generation_id = ?,
                           backup_last_success_at = ?,
                           applied_backup_generations = ?
                     WHERE project_id = ?
                    """,
                    (
                        "tg_backup_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "2098-06-01T00:00:00Z",
                        3,
                        target.project.project_id,
                    ),
                )
                self.assertEqual(cursor.rowcount, 1)
                connection.commit()
            backup_bytes = {
                item.path: item.path.read_bytes()
                for item in artifacts
            }
            target.db_path.unlink()

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
            self.assertFalse(target.db_path.exists())
            self.assertEqual(
                {path: path.read_bytes() for path in backup_bytes},
                backup_bytes,
            )

    def test_v10_absent_predecessor_id_cannot_collide_with_physical_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            target = DatabaseTarget(
                project=install.legacy_target.project,
                db_path=install.db_path,
                explicit_db=True,
            )
            create_v10_target(target)
            artifacts = _publish_generations(
                target,
                "2098-02-01T00:00:00Z",
                "2099-02-01T00:00:00Z",
                retention=3,
            )
            first, later = artifacts
            with closing(sqlite3.connect(first.path)) as connection:
                cursor = connection.execute(
                    """
                    UPDATE project_maintenance
                       SET latest_backup_generation_id = ?,
                           backup_last_success_at = ?,
                           applied_backup_generations = ?
                     WHERE project_id = ?
                    """,
                    (
                        later.metadata.generation_id,
                        "2097-02-01T00:00:00Z",
                        3,
                        target.project.project_id,
                    ),
                )
                self.assertEqual(cursor.rowcount, 1)
                connection.commit()
            backup_bytes = {
                item.path: item.path.read_bytes()
                for item in artifacts
            }
            target.db_path.unlink()

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
            self.assertFalse(target.db_path.exists())
            self.assertEqual(
                {path: path.read_bytes() for path in backup_bytes},
                backup_bytes,
            )

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
            _publish_generations(
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
            head = _publish_generations(
                current_target,
                "2099-10-01T00:00:00Z",
                retention=5,
            )[-1]
            _replace_verification(
                head.path,
                "Mixed-schema rejected head",
                "x" * 501,
            )
            install.db_path.unlink()

            recovered = install.run("setup", "--json")

            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            with closing(sqlite3.connect(install.db_path)) as connection:
                schema_version = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
                rejected_title_count = connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE title = ?",
                    ("Mixed-schema rejected head",),
                ).fetchone()[0]
            self.assertEqual(schema_version, 17)
            self.assertEqual(rejected_title_count, 0)

    def test_a_b_a_binding_history_does_not_reuse_old_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install, target, _ = _setup_current(root)
            added = install.run(
                "task",
                "add",
                "--title",
                "Binding history task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            _publish_generations(target, "2026-08-03T00:00:00Z")
            origin = install.project_root

            moved = _relocate_install(install, root / "moved-project")
            preview_b = _run_setup(
                moved,
                read_only=True,
                now="2026-08-04T00:00:00Z",
            )
            confirmed_b = _run_setup(
                moved,
                read_only=False,
                now="2026-08-04T00:00:01Z",
                confirmation_token=(
                    preview_b.data["relocation"]["confirmation_token"]
                ),
            )
            self.assertTrue(confirmed_b.ok, confirmed_b)
            target_b = resolve_project_state(
                skill_root=moved.skill_root,
                repo=moved.project_root,
            ).target
            _publish_generations(target_b, "2026-08-05T00:00:00Z")

            returned = _relocate_install(moved, origin)
            preview_a = _run_setup(
                returned,
                read_only=True,
                now="2026-08-06T00:00:00Z",
            )
            confirmed_a = _run_setup(
                returned,
                read_only=False,
                now="2026-08-06T00:00:01Z",
                confirmation_token=(
                    preview_a.data["relocation"]["confirmation_token"]
                ),
            )
            self.assertTrue(confirmed_a.ok, confirmed_a)
            target_a = resolve_project_state(
                skill_root=returned.skill_root,
                repo=returned.project_root,
            ).target
            head = _publish_generations(
                target_a,
                "2026-08-07T00:00:00Z",
            )[-1]
            _replace_verification(
                head.path,
                "Binding history task",
                "x" * 501,
            )
            target_a.db_path.unlink()

            result = _run_setup(
                returned,
                read_only=False,
                now="2026-08-08T00:00:00Z",
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "setup_restore_failed")
            self.assertEqual(result.data["planned_writes"], [])
            self.assertFalse(target_a.db_path.exists())


if __name__ == "__main__":
    unittest.main()
