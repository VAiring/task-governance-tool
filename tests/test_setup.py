import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
try:  # noqa: E402
    from m14_test_support import (
        canonical_managed_sqlite_files,
        create_v10_database,
        create_v9_database,
        file_snapshot,
        json_payload,
        make_physical_install,
        make_source_self_host,
    )
except ModuleNotFoundError:  # noqa: E402
    from tests.m14_test_support import (
        canonical_managed_sqlite_files,
        create_v10_database,
        create_v9_database,
        file_snapshot,
        json_payload,
        make_physical_install,
        make_source_self_host,
    )

from task_governance_tool import setup as setup_service
from task_governance_tool import project_scope as project_scope_service
from task_governance_tool.storage import MigrationBackupMetadata


SETUP_DATA_KEYS = {
    "status",
    "planned_writes",
    "completed_writes",
    "schema_from",
    "schema_to",
    "maintenance_enabled",
    "backup_interval_minutes",
    "backup_generations",
    "viewer_status",
}
FRESH_WRITES = [
    "database_initialize",
    "maintenance_configure",
    "viewer_publish",
]
MIGRATION_WRITES = [
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "viewer_publish",
]
class SetupCommandTests(unittest.TestCase):
    def assert_setup_shape(self, payload: dict) -> dict:
        self.assertEqual(payload["command"], "setup")
        self.assertEqual(set(payload["data"]), SETUP_DATA_KEYS)
        self.assertNotIn("db_path", payload)
        serialized = json.dumps(payload)
        self.assertNotIn("viewer_path", serialized)
        self.assertNotIn("backup_path", serialized)
        return payload["data"]

    def test_fresh_preview_success_and_idempotent_replay_follow_exact_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = file_snapshot(install.project_root)

            preview = install.run("setup", "--read-only", "--json")

            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_data = self.assert_setup_shape(json_payload(preview))
            self.assertEqual(
                preview_data,
                {
                    "status": "setup_preview",
                    "planned_writes": FRESH_WRITES,
                    "completed_writes": [],
                    "schema_from": None,
                    "schema_to": 11,
                    "maintenance_enabled": False,
                    "backup_interval_minutes": 30,
                    "backup_generations": 3,
                    "viewer_status": "not_present",
                },
            )
            self.assertEqual(file_snapshot(install.project_root), before)
            self.assertFalse((install.skill_root / "state").exists())

            completed = install.run("setup", "--json")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            completed_data = self.assert_setup_shape(json_payload(completed))
            self.assertEqual(completed_data["status"], "setup_complete")
            self.assertEqual(completed_data["planned_writes"], FRESH_WRITES)
            self.assertEqual(completed_data["completed_writes"], FRESH_WRITES)
            self.assertEqual(completed_data["schema_from"], None)
            self.assertEqual(completed_data["schema_to"], 11)
            self.assertTrue(completed_data["maintenance_enabled"])
            self.assertEqual(completed_data["backup_interval_minutes"], 30)
            self.assertEqual(completed_data["backup_generations"], 3)
            self.assertEqual(completed_data["viewer_status"], "published")
            self.assertTrue(install.db_path.is_file())
            self.assertTrue(install.viewer_path.is_file())
            with closing(sqlite3.connect(install.db_path)) as connection:
                row = connection.execute(
                    """
                    SELECT enabled_at, backup_interval_minutes,
                           backup_generations, applied_backup_generations
                      FROM project_maintenance
                    """
                ).fetchone()
                self.assertIsNotNone(row[0])
                self.assertEqual(tuple(row[1:]), (30, 3, None))

            replay = install.run("setup", "--json")

            self.assertEqual(replay.returncode, 0, replay.stderr)
            replay_data = self.assert_setup_shape(json_payload(replay))
            self.assertEqual(replay_data["status"], "already_setup")
            self.assertEqual(replay_data["planned_writes"], [])
            self.assertEqual(replay_data["completed_writes"], [])
            self.assertEqual(replay_data["viewer_status"], "current")

    def test_policy_validation_change_and_equal_replay_are_bounded(self):
        invalid_cases = (
            ("--backup-interval-minutes", "0"),
            ("--backup-interval-minutes", "1441"),
            ("--backup-generations", "0"),
            ("--backup-generations", "21"),
        )
        for option, value in invalid_cases:
            with self.subTest(option=option, value=value), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                result = install.run("setup", option, value, "--json")
                self.assertEqual(result.returncode, 2)
                payload = json_payload(result)
                data = self.assert_setup_shape(payload)
                self.assertEqual(payload["errors"], [{
                    "code": "invalid_backup_policy",
                    "message": "backup policy is outside the supported range",
                }])
                self.assertEqual(
                    data,
                    {
                        "status": None,
                        "planned_writes": [],
                        "completed_writes": [],
                        "schema_from": None,
                        "schema_to": 11,
                        "maintenance_enabled": None,
                        "backup_interval_minutes": None,
                        "backup_generations": None,
                        "viewer_status": None,
                    },
                )
                self.assertFalse((install.skill_root / "state").exists())

        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            self.assertEqual(install.run("setup", "--json").returncode, 0)
            changed = install.run(
                "setup",
                "--backup-interval-minutes",
                "45",
                "--backup-generations",
                "5",
                "--json",
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)
            changed_data = self.assert_setup_shape(json_payload(changed))
            self.assertEqual(changed_data["planned_writes"], ["maintenance_configure"])
            self.assertEqual(changed_data["completed_writes"], ["maintenance_configure"])
            self.assertEqual(changed_data["backup_interval_minutes"], 45)
            self.assertEqual(changed_data["backup_generations"], 5)
            with closing(sqlite3.connect(install.db_path)) as connection:
                before = connection.execute(
                    """
                    SELECT enabled_at, backup_interval_minutes,
                           backup_generations, applied_backup_generations
                      FROM project_maintenance
                    """
                ).fetchone()
            self.assertEqual(tuple(before[1:]), (45, 5, None))

            equal = install.run(
                "setup",
                "--backup-interval-minutes",
                "45",
                "--backup-generations",
                "5",
                "--read-only",
                "--json",
            )
            equal_data = self.assert_setup_shape(json_payload(equal))
            self.assertEqual(equal_data["status"], "already_setup")
            self.assertEqual(equal_data["planned_writes"], [])
            self.assertEqual(equal_data["completed_writes"], [])

    def test_setup_repairs_only_the_missing_canonical_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            self.assertEqual(install.run("setup", "--json").returncode, 0)
            install.viewer_path.unlink()
            with closing(sqlite3.connect(install.db_path)) as connection:
                before = tuple(
                    connection.execute(
                        """
                        SELECT enabled_at, backup_interval_minutes,
                               backup_generations, applied_backup_generations
                          FROM project_maintenance
                        """
                    ).fetchone()
                )

            preview = install.run("setup", "--read-only", "--json")
            preview_data = self.assert_setup_shape(json_payload(preview))
            self.assertEqual(preview_data["planned_writes"], ["viewer_publish"])
            self.assertEqual(preview_data["completed_writes"], [])
            self.assertEqual(preview_data["viewer_status"], "not_present")
            self.assertFalse(install.viewer_path.exists())

            repaired = install.run("setup", "--json")

            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            repaired_data = self.assert_setup_shape(json_payload(repaired))
            self.assertEqual(repaired_data["planned_writes"], ["viewer_publish"])
            self.assertEqual(repaired_data["completed_writes"], ["viewer_publish"])
            self.assertEqual(repaired_data["viewer_status"], "published")
            self.assertTrue(install.viewer_path.is_file())
            with closing(sqlite3.connect(install.db_path)) as connection:
                after = tuple(
                    connection.execute(
                        """
                        SELECT enabled_at, backup_interval_minutes,
                               backup_generations, applied_backup_generations
                          FROM project_maintenance
                        """
                    ).fetchone()
                )
            self.assertEqual(after, before)

    def test_broken_canonical_viewer_link_requires_repair_and_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialized = install.run("setup", "--json")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            with closing(sqlite3.connect(install.db_path)) as connection:
                before = tuple(
                    connection.execute(
                        """
                        SELECT enabled_at, backup_interval_minutes,
                               backup_generations, applied_backup_generations
                          FROM project_maintenance
                        """
                    ).fetchone()
                )
            sentinel = b"broken-link-target-must-not-be-overwritten"
            install.viewer_path.write_bytes(sentinel)
            with mock.patch(
                "task_governance_tool.viewer.path_is_reparse_point",
                return_value=True,
            ):
                status = setup_service.inspect_canonical_viewer_status(
                    path=install.viewer_path,
                    target=install.target,
                    current_snapshot=None,
                    compare_snapshot=False,
                    verify_template=False,
                )
            self.assertEqual(status, "repair_required")

            with mock.patch.object(
                setup_service,
                "_viewer_status",
                return_value="repair_required",
            ), mock.patch.object(
                setup_service,
                "_publish_viewer",
                side_effect=setup_service.ViewerError(
                    "output_path_invalid",
                    "link-like canonical Viewer is not writable",
                ),
            ):
                attempted = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )
            self.assertFalse(attempted.ok)
            self.assertEqual(attempted.error_code, "setup_incomplete")
            self.assertEqual(attempted.data["viewer_status"], "repair_required")
            self.assertEqual(attempted.data["planned_writes"], ["viewer_publish"])
            self.assertEqual(attempted.data["completed_writes"], [])
            self.assertEqual(install.viewer_path.read_bytes(), sentinel)
            with closing(sqlite3.connect(install.db_path)) as connection:
                after = tuple(
                    connection.execute(
                        """
                        SELECT enabled_at, backup_interval_minutes,
                               backup_generations, applied_backup_generations
                          FROM project_maintenance
                        """
                    ).fetchone()
                )
            self.assertEqual(after, before)

    def test_stage_failures_report_exact_ordered_progress(self):
        cases = (
            (
                "fresh initialization",
                "fresh",
                "initialize_database",
                "setup_initialization_failed",
                FRESH_WRITES,
                [],
                False,
                "not_present",
            ),
            (
                "migration backup",
                "migrated",
                "publish_setup_backup",
                "setup_backup_failed",
                MIGRATION_WRITES,
                [],
                False,
                "not_present",
            ),
            (
                "database migration",
                "migrated",
                "initialize_database",
                "setup_migration_failed",
                MIGRATION_WRITES,
                ["migration_backup"],
                False,
                "not_present",
            ),
            (
                "fresh maintenance",
                "fresh",
                "configure_project_maintenance",
                "setup_incomplete",
                FRESH_WRITES,
                ["database_initialize"],
                False,
                "not_present",
            ),
            (
                "fresh viewer",
                "fresh",
                "_publish_viewer",
                "setup_incomplete",
                FRESH_WRITES,
                ["database_initialize", "maintenance_configure"],
                True,
                "not_present",
            ),
            (
                "migrated maintenance",
                "migrated",
                "configure_project_maintenance",
                "setup_incomplete",
                MIGRATION_WRITES,
                ["migration_backup", "database_migrate"],
                False,
                "not_present",
            ),
            (
                "migrated viewer",
                "migrated",
                "_publish_viewer",
                "setup_incomplete",
                MIGRATION_WRITES,
                [
                    "migration_backup",
                    "database_migrate",
                    "maintenance_configure",
                ],
                True,
                "not_present",
            ),
        )
        for (
            label,
            starting_state,
            patched_stage,
            error_code,
            planned,
            completed,
            maintenance_enabled,
            viewer_status,
        ) in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                schema_from = None
                if starting_state == "migrated":
                    create_v9_database(install)
                    schema_from = 9

                with mock.patch.object(
                    setup_service,
                    patched_stage,
                    side_effect=(
                        setup_service.ViewerError(
                            "output_write_failed",
                            "injected setup stage failure",
                        )
                        if patched_stage == "_publish_viewer"
                        else RuntimeError("injected setup stage failure")
                    ),
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
                self.assertEqual(result.error_code, error_code)
                self.assertEqual(
                    result.data,
                    {
                        "status": None,
                        "planned_writes": planned,
                        "completed_writes": completed,
                        "schema_from": schema_from,
                        "schema_to": 11,
                        "maintenance_enabled": maintenance_enabled,
                        "backup_interval_minutes": 30,
                        "backup_generations": 3,
                        "viewer_status": viewer_status,
                    },
                )

    def test_viewer_observation_busy_is_a_preflight_failure_without_writes(self):
        for patched_stage in ("connect_snapshot_readonly", "build_viewer_snapshot"):
            with (
                self.subTest(stage=patched_stage),
                tempfile.TemporaryDirectory() as tmp,
            ):
                install = make_physical_install(Path(tmp))
                initialized = install.run("setup", "--json")
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                before = file_snapshot(install.project_root)
                with mock.patch.object(
                    setup_service,
                    patched_stage,
                    side_effect=setup_service.StorageError(
                        "database_busy",
                        "task database is busy; run the command again later",
                    ),
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
                self.assertEqual(result.error_code, "database_busy")
                self.assertEqual(
                    result.error_message,
                    "task database is busy; run the command again later",
                )
                self.assertEqual(
                    result.data,
                    {
                        "status": None,
                        "planned_writes": [],
                        "completed_writes": [],
                        "schema_from": None,
                        "schema_to": 11,
                        "maintenance_enabled": None,
                        "backup_interval_minutes": None,
                        "backup_generations": None,
                        "viewer_status": None,
                    },
                )
                self.assertEqual(file_snapshot(install.project_root), before)

    def test_failure_status_fallback_preserves_the_original_stage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            with mock.patch.object(
                setup_service,
                "_viewer_status",
                side_effect=(
                    "not_present",
                    RuntimeError("secondary Viewer observation failure"),
                ),
            ), mock.patch.object(
                setup_service,
                "initialize_database",
                side_effect=RuntimeError("primary initialization failure"),
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
            self.assertEqual(result.error_code, "setup_initialization_failed")
            self.assertEqual(result.data["viewer_status"], "repair_required")
            self.assertEqual(result.data["planned_writes"], FRESH_WRITES)
            self.assertEqual(result.data["completed_writes"], [])

    def test_linklike_canonical_state_candidates_fail_before_writes(self):
        for candidate_kind in ("state_root", "database"):
            with (
                self.subTest(candidate=candidate_kind),
                tempfile.TemporaryDirectory() as tmp,
            ):
                install = make_physical_install(Path(tmp))
                candidate = (
                    install.skill_root / "state"
                    if candidate_kind == "state_root"
                    else install.db_path
                )
                before = file_snapshot(install.project_root)
                with mock.patch.object(
                    project_scope_service,
                    "_is_linklike",
                    side_effect=lambda path, expected=candidate: path == expected,
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
                self.assertEqual(result.error_code, "state_path_invalid")
                self.assertEqual(result.data["planned_writes"], [])
                self.assertEqual(result.data["completed_writes"], [])
                self.assertEqual(result.data["schema_from"], None)
                self.assertEqual(result.data["viewer_status"], None)
                self.assertEqual(file_snapshot(install.project_root), before)
                self.assertFalse((install.skill_root / "state").exists())

    def test_git_project_requires_only_the_canonical_state_ignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp), git_managed=True)
            ignore = install.project_root / ".gitignore"
            ignore.unlink()

            rejected = install.run("setup", "--json")

            self.assertEqual(rejected.returncode, 2)
            payload = json_payload(rejected)
            data = self.assert_setup_shape(payload)
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "state_ignore_required",
                    "message": "project-local state must be ignored before setup",
                }],
            )
            self.assertIsNone(data["status"])
            self.assertEqual(data["planned_writes"], [])
            self.assertEqual(data["completed_writes"], [])
            self.assertFalse((install.skill_root / "state").exists())

            ignore.write_text(
                "/.agents/skills/task-governance-tool/state/\n",
                encoding="utf-8",
            )
            accepted = install.run("setup", "--json")
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertTrue(install.db_path.is_file())

    def test_v9_setup_backup_precedes_migration_and_records_applied_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v9_database(install)
            before_non_state = file_snapshot(install.project_root, exclude_state=True)

            migrated = install.run(
                "setup",
                "--backup-generations",
                "2",
                "--json",
            )

            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            data = self.assert_setup_shape(json_payload(migrated))
            self.assertEqual(data["schema_from"], 9)
            self.assertEqual(data["schema_to"], 11)
            self.assertEqual(data["planned_writes"], MIGRATION_WRITES)
            self.assertEqual(data["completed_writes"], MIGRATION_WRITES)
            self.assertEqual(data["backup_generations"], 2)
            backups = canonical_managed_sqlite_files(
                install,
                exclude=(install.db_path,),
            )
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as backup:
                self.assertEqual(backup.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(backup.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(
                    backup.execute("SELECT project_id FROM project_meta").fetchone()[0],
                    install.project_id,
                )
            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                    11,
                )
                row = connection.execute(
                    """
                    SELECT enabled_at, backup_interval_minutes,
                           backup_generations, applied_backup_generations
                      FROM project_maintenance
                    """
                ).fetchone()
                self.assertIsNotNone(row[0])
                self.assertEqual(tuple(row[1:]), (30, 2, 2))
            self.assertEqual(
                file_snapshot(install.project_root, exclude_state=True),
                before_non_state,
            )

    def test_configured_v10_setup_seeds_generation_without_reconfiguring_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v10_database(
                install,
                enabled=True,
                interval_minutes=45,
                generations=2,
            )

            migrated = install.run("setup", "--json")

            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            data = self.assert_setup_shape(json_payload(migrated))
            expected_writes = [
                "migration_backup",
                "database_migrate",
                "viewer_publish",
            ]
            self.assertEqual(data["schema_from"], 10)
            self.assertEqual(data["schema_to"], 11)
            self.assertEqual(data["planned_writes"], expected_writes)
            self.assertEqual(data["completed_writes"], expected_writes)
            self.assertEqual(data["backup_interval_minutes"], 45)
            self.assertEqual(data["backup_generations"], 2)
            with closing(sqlite3.connect(install.db_path)) as connection:
                connection.row_factory = sqlite3.Row
                maintenance = connection.execute(
                    """
                    SELECT backup_interval_minutes, backup_generations,
                           applied_backup_generations, backup_last_success_at,
                           latest_backup_generation_id
                      FROM project_maintenance
                    """
                ).fetchone()
                generation = connection.execute(
                    """
                    SELECT generation_id, published_at,
                           publication_retention
                      FROM managed_backup_generations
                    """
                ).fetchone()
                self.assertEqual(
                    (
                        maintenance["backup_interval_minutes"],
                        maintenance["backup_generations"],
                        maintenance["applied_backup_generations"],
                    ),
                    (45, 2, 2),
                )
                self.assertEqual(
                    (
                        generation["generation_id"],
                        generation["published_at"],
                        generation["publication_retention"],
                    ),
                    (
                        maintenance["latest_backup_generation_id"],
                        maintenance["backup_last_success_at"],
                        2,
                    ),
                )

    def test_v10_missing_latest_artifact_fails_backup_before_v11_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            missing = MigrationBackupMetadata(
                generation_id=f"tg_backup_{1:032x}",
                published_at="2026-07-27T00:00:01Z",
                publication_retention=3,
            )
            create_v10_database(
                install,
                enabled=True,
                setup_backup=missing,
            )

            failed = install.run("setup", "--json")

            self.assertEqual(failed.returncode, 2)
            payload = json_payload(failed)
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "setup_backup_failed",
                    "message": "setup backup could not be completed",
                }],
            )
            self.assertEqual(payload["data"]["schema_from"], 10)
            self.assertEqual(payload["data"]["completed_writes"], [])
            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    10,
                )
                self.assertIsNone(
                    connection.execute(
                        """
                        SELECT name FROM sqlite_master
                         WHERE type = 'table'
                           AND name = 'managed_backup_generations'
                        """
                    ).fetchone()
                )

    def test_migration_lock_spans_backup_publication_and_migration_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v9_database(install)
            lock_active = False
            real_backup = setup_service.publish_setup_backup
            real_initialize = setup_service.initialize_database

            @contextmanager
            def observed_lock(_target):
                nonlocal lock_active
                self.assertFalse(lock_active)
                lock_active = True
                try:
                    yield
                finally:
                    lock_active = False

            def checked_backup(*args, **kwargs):
                self.assertTrue(lock_active)
                return real_backup(*args, **kwargs)

            def checked_initialize(*args, **kwargs):
                if kwargs.get("setup_backup") is not None:
                    self.assertTrue(lock_active)
                return real_initialize(*args, **kwargs)

            with (
                mock.patch.object(
                    setup_service,
                    "managed_backup_lock",
                    side_effect=observed_lock,
                ),
                mock.patch.object(
                    setup_service,
                    "publish_setup_backup",
                    side_effect=checked_backup,
                ),
                mock.patch.object(
                    setup_service,
                    "initialize_database",
                    side_effect=checked_initialize,
                ),
            ):
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )

            self.assertTrue(result.ok, result.error_message)
            self.assertFalse(lock_active)

    def test_locked_configuration_preserves_concurrently_set_omitted_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            real_configure = setup_service.configure_project_maintenance

            def configure_after_concurrent_explicit_update(target, **kwargs):
                real_configure(
                    target,
                    requested_interval_minutes=60,
                    requested_generations=5,
                )
                return real_configure(target, **kwargs)

            with mock.patch.object(
                setup_service,
                "configure_project_maintenance",
                side_effect=configure_after_concurrent_explicit_update,
            ):
                result = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=45,
                    backup_generations=None,
                )

            self.assertTrue(result.ok, result.error_message)
            self.assertEqual(result.data["backup_interval_minutes"], 45)
            self.assertEqual(result.data["backup_generations"], 5)
            with closing(sqlite3.connect(install.db_path)) as connection:
                row = connection.execute(
                    """
                    SELECT backup_interval_minutes, backup_generations
                      FROM project_maintenance
                    """
                ).fetchone()
            self.assertEqual(row, (45, 5))

    def test_source_self_host_requires_explicit_repo_and_rejects_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_source_self_host(Path(tmp))

            implicit = install.run("setup", "--json")

            self.assertEqual(implicit.returncode, 2)
            implicit_payload = json_payload(implicit)
            self.assertEqual(
                implicit_payload["errors"],
                [{
                    "code": "project_scope_required",
                    "message": "explicit --repo is required from the package directory",
                }],
            )
            self.assertFalse((install.skill_root / "state").exists())

            explicit = install.run(
                "setup",
                "--repo",
                str(install.project_root),
                "--json",
            )
            self.assertEqual(explicit.returncode, 0, explicit.stderr)
            self.assertTrue(install.db_path.is_file())

            competing = (
                install.project_root
                / ".agents"
                / "skills"
                / "task-governance-tool"
            )
            competing.mkdir(parents=True)
            collision = install.run(
                "setup",
                "--repo",
                str(install.project_root),
                "--json",
            )
            self.assertEqual(collision.returncode, 2)
            self.assertEqual(
                json_payload(collision)["errors"],
                [{
                    "code": "unsupported_install_layout",
                    "message": (
                        "stateful use requires one supported physical "
                        "project-scoped package layout"
                    ),
                }],
            )

    def test_ordinary_skill_root_invocation_requires_explicit_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            implicit = install.run("setup", "--json", cwd=install.skill_root)

            self.assertEqual(implicit.returncode, 2)
            self.assertEqual(
                json_payload(implicit)["errors"],
                [{
                    "code": "project_scope_required",
                    "message": "explicit --repo is required from the package directory",
                }],
            )
            self.assertFalse((install.skill_root / "state").exists())

            explicit = install.run(
                "setup",
                "--repo",
                str(install.project_root),
                "--json",
                cwd=install.skill_root,
            )
            self.assertEqual(explicit.returncode, 0, explicit.stderr)
            self.assertTrue(install.db_path.is_file())


if __name__ == "__main__":
    unittest.main()
