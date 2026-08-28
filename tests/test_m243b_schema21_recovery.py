from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests.m214b_test_support import (  # noqa: E402
    publish_generations,
    replace_verification,
)
from tests.m214c_test_support import PRIVATE_SENTINEL  # noqa: E402
from tests.m223_test_support import (  # noqa: E402
    remove_v19_bundle_storage_for_test,
    remove_v20_runner_shadow_for_test,
)
from tests.test_m17_recovery_hardening import _setup_current  # noqa: E402
from tests.test_m242_r3b_schema20_activation import (  # noqa: E402
    _add_task,
    _complete_task,
    _fixed_current20,
    _seed_completion_gates,
    _start_schema20_runtime_oracle,
    _stop_schema20_runtime_oracle,
)
from tests import test_m242_runner_storage as _runner_storage  # noqa: E402

from task_governance_tool import backup as backup_service  # noqa: E402
from task_governance_tool import setup as setup_service  # noqa: E402
from task_governance_tool.state_resolver import (  # noqa: E402
    resolve_setup_project_state,
)
from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    connect_readonly,
    current_schema_version,
)


class M243BSchema21RecoveryTests(unittest.TestCase):
    def _corrupt_source18_or_19_evidence(
        self,
        path: Path,
        *,
        version: int,
    ) -> None:
        with closing(sqlite3.connect(path)) as connection:
            if version == 18:
                trigger_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' "
                    "AND name='trg_authority_snapshots_no_update'"
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_authority_snapshots_no_update"
                )
                cursor = connection.execute(
                    "UPDATE authority_snapshots SET basis_digest = ?",
                    ("sha256:" + "0" * 64,),
                )
                connection.execute(trigger_sql)
            else:
                cursor = connection.execute(
                    "UPDATE evidence_projection_state "
                    "SET source_generation = CAST(0.5 AS REAL)"
                )
            self.assertEqual(cursor.rowcount, 1)
            connection.commit()

    def _seed_source18_or_19_generations(
        self,
        root: Path,
        *,
        version: int,
        title: str,
    ):
        install, target, _ = _setup_current(root)
        added = install.run("task", "add", "--title", title, "--json")
        self.assertEqual(added.returncode, 0, added.stderr)
        with closing(sqlite3.connect(target.db_path)) as connection:
            if version == 19:
                remove_v20_runner_shadow_for_test(connection)
            elif version == 18:
                remove_v19_bundle_storage_for_test(connection)
            else:
                self.fail(f"unsupported source fixture version: {version}")
        with closing(connect_readonly(target.db_path)) as connection:
            self.assertEqual(current_schema_version(connection), version)
        artifacts = publish_generations(
            target,
            "2097-08-25T00:00:00Z",
            "2098-08-25T00:00:00Z",
            "2099-08-25T00:00:00Z",
        )
        self.assertGreaterEqual(len(artifacts), 3)
        for artifact in artifacts:
            with closing(sqlite3.connect(artifact.path)) as connection:
                artifact_version = int(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0]
                )
                if artifact_version != version:
                    if version == 19:
                        remove_v20_runner_shadow_for_test(connection)
                    else:
                        remove_v19_bundle_storage_for_test(connection)
            with closing(connect_readonly(artifact.path)) as connection:
                self.assertEqual(current_schema_version(connection), version)
        return install, target, artifacts

    def _seed_source20_generations(
        self,
        root: Path,
        *,
        title: str,
        completed: bool = False,
    ):
        _start_schema20_runtime_oracle()
        try:
            install, target = _fixed_current20(root, identity_seed=title)
            task_id = _add_task(
                self,
                target,
                title=title,
                verification="" if completed else "recovery check",
                with_contract=True,
            )
            if completed:
                _seed_completion_gates(
                    self,
                    target,
                    task_id,
                    fingerprint="sha256:" + "c" * 64,
                    verification_required=False,
                )
                _complete_task(self, target, task_id)
            artifacts = publish_generations(
                target,
                "2097-08-27T00:00:00Z",
                "2098-08-27T00:00:00Z",
                "2099-08-27T00:00:00Z",
            )
            self.assertGreaterEqual(len(artifacts), 3)
            with closing(connect_readonly(target.db_path)) as connection:
                self.assertEqual(current_schema_version(connection), 20)
            return install, target, task_id, artifacts
        finally:
            _stop_schema20_runtime_oracle()

    def _seed_source20_runner_generations(self, root: Path):
        _start_schema20_runtime_oracle()
        try:
            helper = _runner_storage.RunnerStorageTests()
            target, _basis, resolution, _attempt = helper._pending_graph(
                root,
                seed="source20-recovery-runner",
                commit_character="d",
                token="abcdef0123456789",
            )
            artifacts = publish_generations(
                target,
                "2097-08-26T00:00:00Z",
                "2098-08-26T00:00:00Z",
                "2099-08-26T00:00:00Z",
            )
            self.assertGreaterEqual(len(artifacts), 3)
            with closing(connect_readonly(target.db_path)) as connection:
                self.assertEqual(current_schema_version(connection), 20)
            return target, resolution.verification_runner_resolution_id, artifacts
        finally:
            _stop_schema20_runtime_oracle()

    def _assert_recovery_set_fatal(self, target) -> None:
        self.assertIsNotNone(target.skill_root)
        resolved = resolve_setup_project_state(
            skill_root=target.skill_root,
            repo=target.project.canonical_repo,
        )
        self.assertEqual(resolved.error_code, "project_state_unreadable")
        with self.assertRaises(StorageError) as caught:
            backup_service.select_managed_backup_for_recovery(target)
        self.assertEqual(caught.exception.code, "setup_restore_failed")
        self.assertFalse(target.db_path.exists())

    def _seed_generations(
        self,
        root: Path,
        *,
        title: str,
        verification: str | None = None,
    ):
        install, target, _ = _setup_current(root)
        with closing(connect_readonly(target.db_path)) as connection:
            self.assertEqual(current_schema_version(connection), 21)
        arguments = [
            "task",
            "add",
            "--title",
            title,
        ]
        if verification is not None:
            arguments.extend(("--verification", verification))
        arguments.append("--json")
        added = install.run(*arguments)
        self.assertEqual(added.returncode, 0, added.stderr)
        artifacts = publish_generations(
            target,
            "2097-08-28T00:00:00Z",
            "2098-08-28T00:00:00Z",
            "2099-08-28T00:00:00Z",
        )
        self.assertGreaterEqual(len(artifacts), 3)
        return install, target, artifacts

    def _seed_completed_generations(self, root: Path, *, title: str):
        _install, target, _ = _setup_current(root)
        task_id = _add_task(
            self,
            target,
            title=title,
            verification="",
            with_contract=True,
        )
        _seed_completion_gates(
            self,
            target,
            task_id,
            fingerprint="sha256:" + "a" * 64,
            verification_required=False,
        )
        _complete_task(self, target, task_id)
        artifacts = publish_generations(
            target,
            "2097-08-28T00:00:00Z",
            "2098-08-28T00:00:00Z",
            "2099-08-28T00:00:00Z",
        )
        self.assertGreaterEqual(len(artifacts), 3)
        return target, task_id, artifacts

    def test_source21_recovery_rejection_is_candidate_local_and_private(
        self,
    ) -> None:
        cases = (
            ("x" * 1001, -2),
            (PRIVATE_SENTINEL, -2),
        )
        for index, (value, expected_index) in enumerate(cases):
            with self.subTest(value_length=len(value)):
                with tempfile.TemporaryDirectory(
                    prefix=f".tmp-m243b-recovery-{index}-",
                ) as temporary:
                    title = f"Schema21 recovery candidate {index}"
                    install, target, artifacts = self._seed_generations(
                        Path(temporary),
                        title=title,
                    )
                    replace_verification(artifacts[-1].path, title, value)
                    target.db_path.unlink()

                    candidate = (
                        backup_service.select_managed_backup_for_recovery(
                            target
                        )
                    )

                    self.assertIsNotNone(candidate)
                    self.assertEqual(candidate.path, artifacts[expected_index].path)
                    self.assertEqual(candidate.schema_version, 21)
                    self.assertTrue(
                        all(
                            item.schema_version == 21
                            for item in candidate.inventory
                        )
                    )
                    self.assertIn(
                        artifacts[-1].path,
                        {item.path for item in backup_service._discover(target)},
                    )
                    self.assertNotIn(PRIVATE_SENTINEL, repr(candidate))

                    result = setup_service.run_setup(
                        repo=str(install.project_root),
                        repo_explicit=True,
                        script_path=install.entrypoint,
                        read_only=False,
                        backup_interval_minutes=None,
                        backup_generations=None,
                    )
                    self.assertTrue(result.ok, result)
                    routine = backup_service.run_routine_backup(
                        target,
                        observed_at="2100-08-28T00:00:00Z",
                    )
                    self.assertNotEqual(routine.code, "failed")

    def test_source21_capacity_boundary_1000_remains_recoverable(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-recovery-boundary-",
        ) as temporary:
            _install, target, artifacts = self._seed_generations(
                Path(temporary),
                title="Schema21 exact recovery boundary",
                verification="x" * 1000,
            )
            target.db_path.unlink()

            candidate = backup_service.select_managed_backup_for_recovery(
                target
            )

            self.assertIsNotNone(candidate)
            self.assertEqual(candidate.path, artifacts[-1].path)
            self.assertEqual(candidate.schema_version, 21)

    def test_source21_other_task_defect_is_set_fatal(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-recovery-set-fatal-",
        ) as temporary:
            title = "Schema21 combined recovery defect"
            _install, target, artifacts = self._seed_generations(
                Path(temporary),
                title=title,
            )
            newest = artifacts[-1].path
            replace_verification(newest, title, "x" * 1001)
            with closing(sqlite3.connect(newest)) as connection:
                connection.execute("PRAGMA ignore_check_constraints = ON")
                cursor = connection.execute(
                    "UPDATE tasks SET description = ? WHERE title = ?",
                    (sqlite3.Binary(b"not-text"), title),
                )
                self.assertEqual(cursor.rowcount, 1)
                connection.commit()
            target.db_path.unlink()

            with self.assertRaises(StorageError) as caught:
                backup_service.select_managed_backup_for_recovery(target)

            self.assertEqual(caught.exception.code, "setup_restore_failed")
            self.assertFalse(target.db_path.exists())

    def test_source21_runner_and_bundle_graph_faults_are_set_fatal(self) -> None:
        for fault in ("runner", "bundle"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-recovery-{fault}-",
            ) as temporary:
                title = f"Schema21 {fault} recovery fault"
                target, task_id, artifacts = self._seed_completed_generations(
                    Path(temporary),
                    title=title,
                )
                newest = artifacts[-1].path
                with closing(sqlite3.connect(newest)) as connection:
                    if fault == "runner":
                        cursor = connection.execute(
                            "UPDATE tasks "
                            "SET review_target_runner_basis_version = 2 "
                            "WHERE task_id = ?",
                            (task_id,),
                        )
                        self.assertEqual(cursor.rowcount, 1)
                    else:
                        trigger_sql = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE type='trigger' "
                            "AND name='trg_completion_evidence_bundles_no_update'"
                        ).fetchone()[0]
                        connection.execute(
                            "DROP TRIGGER trg_completion_evidence_bundles_no_update"
                        )
                        cursor = connection.execute(
                            "UPDATE completion_evidence_bundles "
                            "SET bundle_digest = ? WHERE task_id = ?",
                            ("sha256:" + "f" * 64, task_id),
                        )
                        self.assertEqual(cursor.rowcount, 1)
                        connection.execute(trigger_sql)
                    connection.commit()
                target.db_path.unlink()

                with self.assertRaises(StorageError) as caught:
                    backup_service.select_managed_backup_for_recovery(target)

                self.assertEqual(caught.exception.code, "setup_restore_failed")
                self.assertFalse(target.db_path.exists())

    def test_source20_recovery_rejection_remains_candidate_local(self) -> None:
        for index, value in enumerate(("x" * 1001, PRIVATE_SENTINEL)):
            with self.subTest(value_length=len(value)), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-v20-local-{index}-",
            ) as temporary:
                title = f"Schema20 local recovery {index}"
                install, target, _task_id, artifacts = (
                    self._seed_source20_generations(
                        Path(temporary),
                        title=title,
                    )
                )
                replace_verification(artifacts[-1].path, title, value)
                target.db_path.unlink()

                candidate = backup_service.select_managed_backup_for_recovery(target)

                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.path, artifacts[-2].path)
                self.assertEqual(candidate.schema_version, 20)
                self.assertIn(
                    artifacts[-1].path,
                    {item.path for item in backup_service._discover(target)},
                )
                self.assertNotIn(PRIVATE_SENTINEL, repr(candidate))

                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )
                self.assertTrue(result.ok, result)
                with closing(connect_readonly(target.db_path)) as connection:
                    self.assertEqual(current_schema_version(connection), 21)

    def test_source18_and_19_recovery_rejection_remains_candidate_local(
        self,
    ) -> None:
        for version in (18, 19):
            with self.subTest(version=version), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-v{version}-local-",
            ) as temporary:
                title = f"Schema{version} local recovery"
                install, target, artifacts = (
                    self._seed_source18_or_19_generations(
                        Path(temporary),
                        version=version,
                        title=title,
                    )
                )
                replace_verification(artifacts[-1].path, title, "x" * 1001)
                target.db_path.unlink()

                candidate = backup_service.select_managed_backup_for_recovery(
                    target
                )

                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.path, artifacts[-2].path)
                self.assertEqual(candidate.schema_version, version)
                self.assertIn(
                    artifacts[-1].path,
                    {item.path for item in backup_service._discover(target)},
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
                with closing(connect_readonly(target.db_path)) as connection:
                    self.assertEqual(current_schema_version(connection), 21)

    def test_source18_and_19_evidence_faults_are_prepublication_fatal(
        self,
    ) -> None:
        for version in (18, 19):
            with self.subTest(version=version), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-v{version}-evidence-",
            ) as temporary:
                title = f"Schema{version} evidence recovery fault"
                install, target, artifacts = (
                    self._seed_source18_or_19_generations(
                        Path(temporary),
                        version=version,
                        title=title,
                    )
                )
                newest = artifacts[-1].path
                replace_verification(newest, title, "x" * 1001)
                self._corrupt_source18_or_19_evidence(
                    newest,
                    version=version,
                )
                target.db_path.unlink()

                self._assert_recovery_set_fatal(target)

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
                self.assertFalse(target.db_path.exists())

    def test_source18_and_19_publication_and_discovery_reject_evidence_faults(
        self,
    ) -> None:
        for version in (18, 19):
            with self.subTest(version=version), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-v{version}-publication-",
            ) as temporary:
                _install, target, artifacts = (
                    self._seed_source18_or_19_generations(
                        Path(temporary),
                        version=version,
                        title=f"Schema{version} publication fault",
                    )
                )
                before_names = tuple(
                    sorted(path.name for path in target.backups_path.iterdir())
                )
                self._corrupt_source18_or_19_evidence(
                    target.db_path,
                    version=version,
                )

                with backup_service.managed_backup_lock(target):
                    with self.assertRaises(StorageError):
                        backup_service.publish_setup_backup(target, 4)

                self.assertEqual(
                    tuple(
                        sorted(path.name for path in target.backups_path.iterdir())
                    ),
                    before_names,
                )
                newest = artifacts[-1]
                self._corrupt_source18_or_19_evidence(
                    newest.path,
                    version=version,
                )
                discovered = backup_service._discover(target)
                self.assertNotIn(newest.path, {item.path for item in discovered})

    def test_source20_runner_bundle_and_owned_sql_faults_are_prepublication_fatal(
        self,
    ) -> None:
        for fault in (
            "runner",
            "bundle",
            "owned_sql",
            "bundle_attachment",
            "temp_collision",
        ):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-v20-{fault}-",
            ) as temporary:
                root = Path(temporary)
                install = None
                title = f"Schema20 {fault} recovery fault"
                if fault == "runner":
                    target, record_id, artifacts = (
                        self._seed_source20_runner_generations(root)
                    )
                else:
                    install, target, _task_id, artifacts = (
                        self._seed_source20_generations(
                            root,
                            title=title,
                            completed=fault == "bundle",
                        )
                    )
                    record_id = None
                newest = artifacts[-1].path
                with closing(sqlite3.connect(newest)) as connection:
                    if fault == "owned_sql":
                        connection.execute("DROP INDEX idx_tasks_project_status")
                        connection.execute(
                            "CREATE INDEX idx_tasks_project_status "
                            "ON tasks(project_id, status) WHERE status = 'active'"
                        )
                    elif fault == "bundle_attachment":
                        connection.execute(
                            "CREATE INDEX forbidden_recovery_bundle_attachment "
                            "ON completion_evidence_bundles(sealed_at)"
                        )
                    elif fault == "temp_collision":
                        connection.execute(
                            "CREATE TABLE Verification_Runner_Resolutions_v20("
                            "marker INTEGER PRIMARY KEY)"
                        )
                    else:
                        table_name = (
                            "verification_runner_resolutions"
                            if fault == "runner"
                            else "completion_evidence_bundles"
                        )
                        trigger_name = f"trg_{table_name}_no_update"
                        trigger_sql = connection.execute(
                            "SELECT sql FROM sqlite_master "
                            "WHERE type='trigger' AND name=?",
                            (trigger_name,),
                        ).fetchone()[0]
                        connection.execute(f'DROP TRIGGER "{trigger_name}"')
                        if fault == "runner":
                            cursor = connection.execute(
                                "UPDATE verification_runner_resolutions "
                                "SET idempotency_digest = ? "
                                "WHERE verification_runner_resolution_id = ?",
                                ("sha256:" + "f" * 64, record_id),
                            )
                        else:
                            cursor = connection.execute(
                                "UPDATE completion_evidence_bundles "
                                "SET bundle_digest = ?",
                                ("sha256:" + "f" * 64,),
                            )
                        self.assertEqual(cursor.rowcount, 1)
                        connection.execute(trigger_sql)
                    connection.commit()
                if fault in {"bundle", "bundle_attachment", "temp_collision"}:
                    replace_verification(newest, title, "x" * 1001)
                target.db_path.unlink()

                self._assert_recovery_set_fatal(target)

                if fault in {"bundle", "bundle_attachment", "temp_collision"}:
                    self.assertIsNotNone(install)
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
                    self.assertEqual(result.data["completed_writes"], [])
                    self.assertFalse(target.db_path.exists())

    def test_source20_primary_residue_rejects_before_backup_publication(
        self,
    ) -> None:
        for fault in ("bundle_attachment", "temp_collision"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-v20-primary-{fault}-",
            ) as temporary:
                _install, target, _task_id, _artifacts = (
                    self._seed_source20_generations(
                        Path(temporary),
                        title=f"Schema20 primary {fault}",
                    )
                )
                before_names = tuple(
                    sorted(path.name for path in target.backups_path.iterdir())
                )
                with closing(sqlite3.connect(target.db_path)) as connection:
                    if fault == "bundle_attachment":
                        connection.execute(
                            "CREATE INDEX forbidden_primary_bundle_attachment "
                            "ON completion_evidence_bundles(sealed_at)"
                        )
                    else:
                        connection.execute(
                            "CREATE TABLE Verification_Runner_Resolutions_v20("
                            "marker INTEGER PRIMARY KEY)"
                        )
                    connection.commit()

                with backup_service.managed_backup_lock(target):
                    with self.assertRaises(StorageError):
                        backup_service.publish_setup_backup(target, 4)

                self.assertEqual(
                    tuple(
                        sorted(path.name for path in target.backups_path.iterdir())
                    ),
                    before_names,
                )

    def test_source21_cycle_fault_rejects_before_backup_publication(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-v21-primary-cycle-",
        ) as temporary:
            target, _task_id, _artifacts = self._seed_completed_generations(
                Path(temporary),
                title="Schema21 primary completion-cycle fault",
            )
            before_names = tuple(
                sorted(path.name for path in target.backups_path.iterdir())
            )
            with closing(sqlite3.connect(target.db_path)) as connection:
                trigger_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' "
                    "AND name='trg_task_events_completion_cycle_link_immutable'"
                ).fetchone()[0]
                cycle_id = connection.execute(
                    "SELECT completion_cycle_id FROM task_completion_cycles"
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_task_events_completion_cycle_link_immutable"
                )
                cursor = connection.execute(
                    "UPDATE task_events SET completion_cycle_id = NULL "
                    "WHERE completion_cycle_id = ?",
                    (cycle_id,),
                )
                self.assertEqual(cursor.rowcount, 1)
                connection.execute(trigger_sql)
                connection.commit()

            with backup_service.managed_backup_lock(target):
                with self.assertRaises(StorageError):
                    backup_service.publish_setup_backup(target, 4)

            self.assertEqual(
                tuple(sorted(path.name for path in target.backups_path.iterdir())),
                before_names,
            )


if __name__ == "__main__":
    unittest.main()
