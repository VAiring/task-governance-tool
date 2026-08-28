from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    remove_v18_evidence_ledger_for_test,
    tree_snapshot,
)
from tests.m214b_test_support import (
    inject_primary_candidate_metadata_conflict as _inject_primary_candidate_metadata_conflict,
    publish_generations as _publish_generations,
    replace_recovery_integer_storage as _replace_recovery_integer_storage,
    replace_verification as _replace_verification,
    restore_original_mtime as _restore_original_mtime,
    restore_temporaries as _restore_temporaries,
)
from tests.m214c_test_support import PRIVATE_SENTINEL
from tests.m223_test_support import remove_v19_bundle_storage_for_test
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
from task_governance_tool.storage import (
    StorageError,
    read_managed_backup_repository,
    validate_sqlite_integer_storage_class,
)


def _downgrade_candidate_to_v17(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        remove_v18_evidence_ledger_for_test(connection)


def _downgrade_candidates_to_v18(artifacts) -> None:
    for artifact in artifacts:
        with closing(sqlite3.connect(artifact.path)) as connection:
            remove_v19_bundle_storage_for_test(connection)
            if int(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
            ) != 18:
                raise AssertionError("recovery fixture is not schema 18")


class M214BRecoveryBoundaryTests(unittest.TestCase):
    def test_recovery_integer_storage_class_fault_matrix_is_set_fatal(self):
        for location in (
            "generation_row",
            "maintenance_pointer",
            "backup_interval",
            "backup_generations",
        ):
            for primary_present in (True, False):
                with (
                    self.subTest(
                        location=location,
                        primary_present=primary_present,
                    ),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    install, target, _ = _setup_current(Path(tmp))
                    artifacts = _publish_generations(
                        target,
                        "2098-02-10T00:00:00Z",
                        "2099-02-10T00:00:00Z",
                    )
                    corrupted_path = (
                        target.db_path if primary_present else artifacts[-1].path
                    )
                    self.assertEqual(
                        _replace_recovery_integer_storage(
                            corrupted_path,
                            location=location,
                            value=4.5,
                        ),
                        "real",
                    )
                    if not primary_present:
                        target.db_path.unlink()

                    state_root = install.skill_root / "state"
                    before_managed_state = tree_snapshot(state_root)
                    if primary_present:
                        with self.assertRaises(StorageError) as caught:
                            read_managed_backup_repository(target)
                        self.assertEqual(
                            caught.exception.code,
                            "project_state_unreadable",
                        )

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
                    self.assertEqual(result.data["completed_writes"], [])
                    self.assertEqual(
                        tree_snapshot(state_root),
                        before_managed_state,
                    )
                    self.assertEqual(
                        target.db_path.exists(),
                        primary_present,
                    )

    def test_recovery_integer_validator_rejects_non_integer_storage_classes(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE values_under_test(value)")
            for label, value in (
                ("real", 4.5),
                ("text", "4"),
                ("blob", sqlite3.Binary(b"4")),
            ):
                with self.subTest(storage_class=label):
                    connection.execute("DELETE FROM values_under_test")
                    connection.execute(
                        "INSERT INTO values_under_test(value) VALUES (?)",
                        (value,),
                    )
                    raw_value, storage_class = connection.execute(
                        "SELECT value, typeof(value) FROM values_under_test"
                    ).fetchone()
                    self.assertEqual(storage_class, label)
                    with self.assertRaises(StorageError) as caught:
                        validate_sqlite_integer_storage_class(raw_value)
                    self.assertEqual(
                        caught.exception.code,
                        "project_state_unreadable",
                    )
        with self.assertRaises(StorageError):
            validate_sqlite_integer_storage_class(True)

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
            state_root = install.skill_root / "state"
            before_managed_state = tree_snapshot(state_root)
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
            self.assertEqual(tree_snapshot(state_root), before_managed_state)
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
            _downgrade_candidate_to_v17(artifact.path)
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

    def test_v18_task_only_local_rejection_falls_back_to_older_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            title = "V18 local rejection task"
            added = install.run(
                "task",
                "add",
                "--title",
                title,
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifacts = _publish_generations(
                target,
                "2097-03-02T00:00:00Z",
                "2098-03-02T00:00:00Z",
                "2099-03-02T00:00:00Z",
            )
            _downgrade_candidates_to_v18(artifacts)
            _replace_verification(artifacts[-1].path, title, "x" * 1001)
            target.db_path.unlink()

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
                    (title,),
                ).fetchone()[0]
            self.assertEqual(verification, "")

    def test_v18_task_only_privacy_rejection_falls_back_to_older_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            title = "V18 local privacy rejection task"
            added = install.run(
                "task",
                "add",
                "--title",
                title,
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifacts = _publish_generations(
                target,
                "2097-03-02T01:00:00Z",
                "2098-03-02T01:00:00Z",
                "2099-03-02T01:00:00Z",
            )
            _downgrade_candidates_to_v18(artifacts)
            _replace_verification(
                artifacts[-1].path,
                title,
                PRIVATE_SENTINEL,
            )
            target.db_path.unlink()

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
                    (title,),
                ).fetchone()[0]
            self.assertEqual(verification, "")

    def test_v18_ledger_defect_is_set_fatal_even_with_local_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            title = "V18 combined recovery defect"
            added = install.run(
                "task",
                "add",
                "--title",
                title,
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifacts = _publish_generations(
                target,
                "2097-03-03T00:00:00Z",
                "2098-03-03T00:00:00Z",
                "2099-03-03T00:00:00Z",
            )
            _downgrade_candidates_to_v18(artifacts)
            newest = artifacts[-1].path
            _replace_verification(
                newest,
                title,
                PRIVATE_SENTINEL + ("x" * 1001),
            )
            with closing(sqlite3.connect(newest)) as connection:
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_authority_snapshots_no_update'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_authority_snapshots_no_update"
                )
                connection.execute(
                    "UPDATE authority_snapshots SET basis_digest = ?",
                    ("sha256:" + "0" * 64,),
                )
                connection.execute(trigger_sql)
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
            self.assertEqual(result.data["completed_writes"], [])
            restore.assert_not_called()
            self.assertFalse(target.db_path.exists())

    def test_backup_revalidation_does_not_hide_v18_ledger_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            title = "V18 backup revalidation defect"
            added = install.run(
                "task",
                "add",
                "--title",
                title,
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifacts = _publish_generations(
                target,
                "2097-03-03T01:00:00Z",
                "2098-03-03T01:00:00Z",
                "2099-03-03T01:00:00Z",
            )
            _downgrade_candidates_to_v18(artifacts)
            newest = artifacts[-1].path
            _replace_verification(
                newest,
                title,
                PRIVATE_SENTINEL + ("x" * 1001),
            )
            with closing(sqlite3.connect(newest)) as connection:
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_authority_snapshots_no_update'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_authority_snapshots_no_update"
                )
                connection.execute(
                    "UPDATE authority_snapshots SET basis_digest = ?",
                    ("sha256:" + "0" * 64,),
                )
                connection.execute(trigger_sql)
                connection.commit()
            target.db_path.unlink()

            with self.assertRaises(StorageError) as caught:
                backup_service.select_managed_backup_for_recovery(target)

            self.assertEqual(caught.exception.code, "setup_restore_failed")
            self.assertFalse(target.db_path.exists())

    def test_v18_ledger_only_defect_is_preselection_set_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            added = install.run(
                "task",
                "add",
                "--title",
                "V18 ledger-only recovery defect",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            artifacts = _publish_generations(
                target,
                "2097-03-04T00:00:00Z",
                "2098-03-04T00:00:00Z",
                "2099-03-04T00:00:00Z",
            )
            _downgrade_candidates_to_v18(artifacts)
            newest = artifacts[-1].path
            with closing(sqlite3.connect(newest)) as connection:
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_authority_snapshots_no_update'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_authority_snapshots_no_update"
                )
                connection.execute(
                    "UPDATE authority_snapshots SET basis_digest = ?",
                    ("sha256:" + "0" * 64,),
                )
                connection.execute(trigger_sql)
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

    def test_fixed_primary_rejects_candidate_metadata_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, target, _ = _setup_current(Path(tmp))
            artifact = _publish_generations(
                target,
                "2099-02-01T00:00:00Z",
            )[-1]
            _inject_primary_candidate_metadata_conflict(target, artifact)
            state_root = install.skill_root / "state"
            before_managed_state = tree_snapshot(state_root)
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
            self.assertEqual(tree_snapshot(state_root), before_managed_state)
            self.assertEqual(target.db_path.read_bytes(), primary_bytes)
            self.assertEqual(artifact.path.read_bytes(), backup_bytes)

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
            _downgrade_candidate_to_v17(newest.path)
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

    def test_publication_inventory_precedes_final_deep_and_blocks_link(self):
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
            real_deep_validation = backup_service._require_fixed_recovery_state
            real_prepare = backup_service._prepare_recovered_repository
            events: list[str] = []
            drift_injected = False

            def observe_inventory(restore_target, candidate):
                observed = real_inventory(restore_target, candidate)
                if (
                    drift_injected
                    and restore_target.db_path == target.db_path
                ):
                    events.append("post_drift_shallow")
                return observed

            def prepare_then_drift(
                restore_target,
                candidate,
                *,
                expected_snapshot=None,
            ):
                nonlocal drift_injected
                result = real_prepare(
                    restore_target,
                    candidate,
                    expected_snapshot=expected_snapshot,
                )
                if not drift_injected:
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
                    drift_injected = True
                    events.append("post_prepare_drift")
                return result

            def observe_deep_validation(
                restore_target,
                candidate,
                expected_recovery,
            ):
                events.append(
                    "post_drift_deep"
                    if drift_injected
                    else "entry_deep"
                )
                return real_deep_validation(
                    restore_target,
                    candidate,
                    expected_recovery,
                )

            with (
                mock.patch.object(
                    backup_service,
                    "_require_recovery_inventory",
                    side_effect=observe_inventory,
                ),
                mock.patch.object(
                    backup_service,
                    "_prepare_recovered_repository",
                    side_effect=prepare_then_drift,
                ),
                mock.patch.object(
                    backup_service,
                    "_require_fixed_recovery_state",
                    side_effect=observe_deep_validation,
                ),
                mock.patch.object(
                    backup_service.os,
                    "link",
                    wraps=backup_service.os.link,
                ) as publish_link,
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
            self.assertTrue(drift_injected)
            self.assertIn("entry_deep", events)
            self.assertIn("post_prepare_drift", events)
            self.assertIn("post_drift_shallow", events)
            self.assertIn("post_drift_deep", events)
            last_shallow = max(
                index
                for index, event in enumerate(events)
                if event == "post_drift_shallow"
            )
            last_deep = max(
                index
                for index, event in enumerate(events)
                if event == "post_drift_deep"
            )
            self.assertLess(
                events.index("post_prepare_drift"),
                last_shallow,
            )
            self.assertLess(last_shallow, last_deep)
            self.assertEqual(last_deep, len(events) - 1)
            publish_link.assert_not_called()
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
            _downgrade_candidate_to_v17(rejected)
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
                    "x" * 1001,
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
            _downgrade_candidate_to_v17(head.path)
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
