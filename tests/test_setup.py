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
        canonical_test_path,
        create_v10_database,
        create_v10_target,
        create_v12_database,
        create_v12_target,
        create_v9_database,
        file_snapshot,
        json_payload,
        make_physical_install,
        make_source_self_host,
    )
except ModuleNotFoundError:  # noqa: E402
    from tests.m14_test_support import (
        canonical_managed_sqlite_files,
        canonical_test_path,
        create_v10_database,
        create_v10_target,
        create_v12_database,
        create_v12_target,
        create_v9_database,
        file_snapshot,
        json_payload,
        make_physical_install,
        make_source_self_host,
    )

from task_governance_tool import setup as setup_service
from task_governance_tool import backup as backup_service
from task_governance_tool import project_scope as project_scope_service
from task_governance_tool.storage import (
    DatabaseTarget,
    MigrationBackupMetadata,
)


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
LEGACY_MIGRATION_WRITES = [
    "legacy_state_publish",
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "viewer_publish",
    "legacy_state_cleanup",
]


def fixed_fixture_target(install) -> DatabaseTarget:
    """Construct a fixed-current fixture without using production resolution."""

    return DatabaseTarget(
        project=install.legacy_target.project,
        db_path=install.db_path,
        explicit_db=True,
    )


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
            preview_payload = json_payload(preview)
            preview_data = self.assert_setup_shape(preview_payload)
            self.assertIsNone(preview_payload["project_id"])
            self.assertEqual(
                preview_data,
                {
                    "status": "setup_preview",
                    "planned_writes": FRESH_WRITES,
                    "completed_writes": [],
                    "schema_from": None,
                    "schema_to": 14,
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
            completed_payload = json_payload(completed)
            completed_data = self.assert_setup_shape(completed_payload)
            completed_project_id = completed_payload["project_id"]
            self.assertIsInstance(completed_project_id, str)
            self.assertRegex(
                completed_project_id,
                r"\Atg_project_[0-9a-f]{32}\Z",
            )
            self.assertEqual(completed_data["status"], "setup_complete")
            self.assertEqual(completed_data["planned_writes"], FRESH_WRITES)
            self.assertEqual(completed_data["completed_writes"], FRESH_WRITES)
            self.assertEqual(completed_data["schema_from"], None)
            self.assertEqual(completed_data["schema_to"], 14)
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
                identity = connection.execute(
                    """
                    SELECT project_id, identity_scheme, binding_generation
                      FROM project_meta
                    """
                ).fetchone()
                self.assertEqual(
                    identity,
                    (completed_project_id, "uuid_v1", 1),
                )

            replay = install.run("setup", "--json")

            self.assertEqual(replay.returncode, 0, replay.stderr)
            replay_payload = json_payload(replay)
            replay_data = self.assert_setup_shape(replay_payload)
            self.assertEqual(
                replay_payload["project_id"],
                completed_project_id,
            )
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
                        "schema_to": 14,
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

    def test_setup_previews_and_repairs_viewer_interval_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialized = install.run("setup", "--json")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            config = install.skill_root / "config" / "viewer.json"
            config.parent.mkdir()
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile": "visibility-refresh-v1",
                        "refresh_interval_seconds": 5,
                    }
                ),
                encoding="utf-8",
            )

            preview = install.run("setup", "--read-only", "--json")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_data = self.assert_setup_shape(json_payload(preview))
            self.assertEqual(preview_data["planned_writes"], ["viewer_publish"])
            self.assertEqual(preview_data["completed_writes"], [])
            self.assertEqual(preview_data["viewer_status"], "repair_required")

            repaired = install.run("setup", "--json")
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            repaired_data = self.assert_setup_shape(json_payload(repaired))
            self.assertEqual(repaired_data["planned_writes"], ["viewer_publish"])
            self.assertEqual(repaired_data["completed_writes"], ["viewer_publish"])
            self.assertIn(
                'data-taskgov-refresh-interval-seconds="5"',
                install.viewer_path.read_text(encoding="utf-8"),
            )
            replay = install.run("setup", "--json")
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(
                self.assert_setup_shape(json_payload(replay))["planned_writes"],
                [],
            )

            config.unlink()
            disabled_preview = install.run("setup", "--read-only", "--json")
            self.assertEqual(
                self.assert_setup_shape(json_payload(disabled_preview))[
                    "planned_writes"
                ],
                ["viewer_publish"],
            )
            disabled = install.run("setup", "--json")
            self.assertEqual(disabled.returncode, 0, disabled.stderr)
            self.assertIn(
                'data-taskgov-refresh-interval-seconds="0"',
                install.viewer_path.read_text(encoding="utf-8"),
            )

    def test_invalid_viewer_config_preview_is_no_write_and_setup_preserves_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialized = install.run("setup", "--json")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            last_good = install.viewer_path.read_bytes()
            config = install.skill_root / "config" / "viewer.json"
            config.parent.mkdir()
            config.write_text('{"schema_version":1}', encoding="utf-8")

            before = file_snapshot(install.project_root)
            preview = install.run("setup", "--read-only", "--json")
            after_preview = file_snapshot(install.project_root)

            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_data = self.assert_setup_shape(json_payload(preview))
            self.assertEqual(preview_data["planned_writes"], ["viewer_publish"])
            self.assertEqual(preview_data["completed_writes"], [])
            self.assertEqual(preview_data["viewer_status"], "repair_required")
            self.assertEqual(after_preview, before)

            attempted = install.run("setup", "--json")
            payload = json_payload(attempted)

            self.assertEqual(attempted.returncode, 2)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "setup_incomplete")
            attempted_data = self.assert_setup_shape(payload)
            self.assertEqual(attempted_data["planned_writes"], ["viewer_publish"])
            self.assertEqual(attempted_data["completed_writes"], [])
            self.assertEqual(attempted_data["viewer_status"], "repair_required")
            self.assertEqual(install.viewer_path.read_bytes(), last_good)

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
                "initialize_uuid_database",
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
                LEGACY_MIGRATION_WRITES,
                [],
                False,
                "not_present",
            ),
            (
                "database migration",
                "migrated",
                "initialize_database",
                "setup_migration_failed",
                LEGACY_MIGRATION_WRITES,
                [],
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
                LEGACY_MIGRATION_WRITES,
                [],
                False,
                "not_present",
            ),
            (
                "migrated viewer",
                "migrated",
                "_publish_viewer",
                "setup_incomplete",
                LEGACY_MIGRATION_WRITES,
                [],
                False,
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
                        "schema_to": 14,
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
                        "schema_to": 14,
                        "maintenance_enabled": None,
                        "backup_interval_minutes": None,
                        "backup_generations": None,
                        "viewer_status": None,
                    },
                )
                self.assertEqual(file_snapshot(install.project_root), before)

    def test_legacy_failure_cleanup_preserves_a_replacement_owned_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v10_database(install, enabled=True)
            state_root = install.skill_root / "state"
            replacement_stage_id = "f" * 32
            transition_calls = 0
            replacement_created = False
            real_lock = setup_service.state_transition_lock
            real_inspect = setup_service.inspect_stage_residue
            real_remove = setup_service.remove_stage_residue

            @contextmanager
            def interleaving_lock(root):
                nonlocal transition_calls, replacement_created
                transition_calls += 1
                call_number = transition_calls
                try:
                    with real_lock(root):
                        yield
                finally:
                    if call_number == 1:
                        with real_lock(root):
                            residue = real_inspect(
                                root,
                                max_file_bytes=100_000_000,
                            )
                            self.assertIsNotNone(residue)
                            self.assertNotEqual(
                                residue.owner.stage_id,
                                replacement_stage_id,
                            )
                            real_remove(root, residue)
                            setup_service.create_owned_stage(
                                root,
                                project_id=residue.owner.project_id,
                                inventory_fingerprint=(
                                    residue.owner.inventory_fingerprint
                                ),
                                stage_id=replacement_stage_id,
                            )
                            replacement_created = True

            with (
                mock.patch.object(
                    setup_service,
                    "state_transition_lock",
                    side_effect=interleaving_lock,
                ),
                mock.patch.object(
                    setup_service,
                    "_publish_viewer",
                    side_effect=setup_service.ViewerError(
                        "output_write_failed",
                        "injected pre-publish failure",
                    ),
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
            self.assertEqual(result.error_code, "setup_incomplete")
            self.assertTrue(replacement_created)
            self.assertEqual(transition_calls, 2)
            self.assertFalse(install.fixed_root.exists())
            self.assertTrue(install.legacy_db_path.is_file())
            replacement = real_inspect(
                state_root,
                max_file_bytes=100_000_000,
            )
            self.assertIsNotNone(replacement)
            self.assertEqual(
                replacement.owner.stage_id,
                replacement_stage_id,
            )

    def test_malformed_stage_residue_precedes_relocation_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v10_database(install, enabled=True)
            with closing(sqlite3.connect(install.legacy_db_path)) as connection:
                connection.execute(
                    "UPDATE project_meta SET canonical_path_hash = ?",
                    ("0" * 64,),
                )
                connection.commit()
            malformed_stage = (
                install.skill_root
                / "state"
                / f".current-stage-{'e' * 32}"
            )
            malformed_stage.mkdir()
            (malformed_stage / "sentinel.txt").write_text(
                "must remain untouched\n",
                encoding="utf-8",
            )
            before = file_snapshot(install.project_root)

            for options in (("--read-only",), ()):
                with self.subTest(mode=options or ("write",)):
                    attempted = install.run("setup", *options, "--json")

                    self.assertEqual(
                        attempted.returncode,
                        2,
                        attempted.stderr,
                    )
                    payload = json_payload(attempted)
                    self.assertEqual(
                        payload["errors"],
                        [{
                            "code": "setup_incomplete",
                            "message": (
                                "setup completed only partially; rerun setup"
                            ),
                        }],
                    )
                    self.assertIsNone(payload["project_id"])
                    self.assertEqual(
                        payload["data"]["planned_writes"],
                        [],
                    )
                    self.assertEqual(
                        payload["data"]["completed_writes"],
                        [],
                    )
                    self.assertEqual(
                        file_snapshot(install.project_root),
                        before,
                    )
                    self.assertTrue(malformed_stage.is_dir())

    def test_transition_lock_contention_preserves_database_busy(self):
        for starting_state in ("fresh", "legacy"):
            with (
                self.subTest(starting_state=starting_state),
                tempfile.TemporaryDirectory() as tmp,
            ):
                install = make_physical_install(Path(tmp))
                state_root = install.skill_root / "state"
                if starting_state == "legacy":
                    create_v10_database(install, enabled=True)
                else:
                    state_root.mkdir()

                with setup_service.state_transition_lock(state_root):
                    pass
                before = file_snapshot(install.project_root)
                with setup_service.state_transition_lock(state_root):
                    attempted = install.run("setup", "--json")

                    self.assertEqual(
                        attempted.returncode,
                        2,
                        attempted.stderr,
                    )
                    payload = json_payload(attempted)
                    self.assertEqual(
                        payload["errors"],
                        [{
                            "code": "database_busy",
                            "message": (
                                "task database is busy; "
                                "run the command again later"
                            ),
                        }],
                    )
                    self.assertEqual(
                        payload["data"]["completed_writes"],
                        [],
                    )
                self.assertEqual(
                    file_snapshot(install.project_root),
                    before,
                )

    def test_failure_status_fallback_preserves_the_original_stage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            with mock.patch.object(
                setup_service,
                "_viewer_status",
                side_effect=RuntimeError(
                    "secondary Viewer observation failure"
                ),
            ), mock.patch.object(
                setup_service,
                "configure_project_maintenance",
                side_effect=RuntimeError("primary configuration failure"),
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
            self.assertEqual(result.error_code, "setup_incomplete")
            self.assertEqual(result.data["viewer_status"], "repair_required")
            self.assertEqual(result.data["planned_writes"], FRESH_WRITES)
            self.assertEqual(
                result.data["completed_writes"],
                ["database_initialize"],
            )

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
                    side_effect=lambda path, expected=candidate: (
                        canonical_test_path(path)
                        == canonical_test_path(expected)
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
                self.assertEqual(result.error_code, "state_path_invalid")
                self.assertEqual(result.data["planned_writes"], [])
                self.assertEqual(result.data["completed_writes"], [])
                self.assertEqual(result.data["schema_from"], None)
                self.assertEqual(result.data["viewer_status"], None)
                self.assertEqual(file_snapshot(install.project_root), before)
                self.assertFalse((install.skill_root / "state").exists())

    def test_git_project_requires_effective_canonical_state_ignore(self):
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
            self.assertEqual(data["schema_to"], 14)
            self.assertEqual(
                data["planned_writes"],
                LEGACY_MIGRATION_WRITES,
            )
            self.assertEqual(
                data["completed_writes"],
                LEGACY_MIGRATION_WRITES,
            )
            self.assertFalse(install.legacy_db_path.exists())
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
                    14,
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
                "legacy_state_publish",
                "migration_backup",
                "database_migrate",
                "viewer_publish",
                "legacy_state_cleanup",
            ]
            self.assertEqual(data["schema_from"], 10)
            self.assertEqual(data["schema_to"], 14)
            self.assertEqual(data["planned_writes"], expected_writes)
            self.assertEqual(data["completed_writes"], expected_writes)
            self.assertFalse(install.legacy_db_path.exists())
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

    def test_configured_v12_setup_preserves_omitted_or_equal_policy(self):
        cases = {
            "omitted": (),
            "equal": (
                "--backup-interval-minutes",
                "45",
                "--backup-generations",
                "2",
            ),
        }
        for name, options in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_v12_database(
                    install,
                    enabled=True,
                    interval_minutes=45,
                    generations=2,
                )

                migrated = install.run("setup", *options, "--json")

                self.assertEqual(migrated.returncode, 0, migrated.stderr)
                data = self.assert_setup_shape(json_payload(migrated))
                self.assertEqual(data["schema_from"], 12)
                self.assertEqual(data["schema_to"], 14)
                self.assertEqual(
                    data["planned_writes"],
                    [
                        "legacy_state_publish",
                        "migration_backup",
                        "database_migrate",
                        "viewer_publish",
                        "legacy_state_cleanup",
                    ],
                )
                self.assertEqual(data["completed_writes"], data["planned_writes"])
                self.assertFalse(install.legacy_db_path.exists())
                self.assertNotIn("maintenance_configure", data["completed_writes"])
                self.assertEqual(data["backup_interval_minutes"], 45)
                self.assertEqual(data["backup_generations"], 2)
                self.assertTrue(install.viewer_path.is_file())
                with closing(sqlite3.connect(install.db_path)) as connection:
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT backup_interval_minutes, backup_generations
                              FROM project_maintenance
                            """
                        ).fetchone(),
                        (45, 2),
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        14,
                    )
                    self.assertIsNotNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'table' AND name = 'task_checkpoints'
                            """
                        ).fetchone()
                    )
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT source_generation, rendered_generation,
                                   last_outcome_code
                              FROM viewer_maintenance_state
                            """
                        ).fetchone(),
                        (0, 0, "succeeded"),
                    )

    def test_v12_setup_retry_repairs_each_backup_crash_boundary_before_copy(self):
        def run(install):
            return setup_service.run_setup(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.skill_root / "scripts" / "taskgov.py",
                read_only=False,
                backup_interval_minutes=None,
                backup_generations=None,
            )

        def generation_count(install):
            with closing(sqlite3.connect(install.db_path)) as connection:
                return connection.execute(
                    "SELECT COUNT(*) FROM managed_backup_generations"
                ).fetchone()[0]

        with self.subTest(boundary="file_published"), tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v12_target(
                fixed_fixture_target(install),
                enabled=True,
            )
            with mock.patch.object(
                backup_service,
                "record_managed_backup",
                side_effect=RuntimeError("injected file-only stop"),
            ):
                self.assertFalse(run(install).ok)
            self.assertEqual(generation_count(install), 0)
            self.assertEqual(
                len(canonical_managed_sqlite_files(install, exclude=(install.db_path,))),
                1,
            )
            real_copy = backup_service._copy

            def copy_after_file_repair(target, metadata):
                self.assertEqual(generation_count(install), 1)
                self.assertEqual(
                    len(canonical_managed_sqlite_files(
                        install,
                        exclude=(install.db_path,),
                    )),
                    1,
                )
                return real_copy(target, metadata)

            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=copy_after_file_repair,
            ):
                self.assertTrue(run(install).ok)

        with self.subTest(boundary="row_committed"), tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v12_target(
                fixed_fixture_target(install),
                enabled=True,
                generations=1,
            )
            setup_service.publish_setup_backup(install.target, 1)
            real_reconcile = backup_service._reconcile_v11
            reconcile_calls = 0

            def stop_after_row(*args, **kwargs):
                nonlocal reconcile_calls
                reconcile_calls += 1
                if reconcile_calls == 2:
                    raise RuntimeError("injected post-row stop")
                return real_reconcile(*args, **kwargs)

            with mock.patch.object(
                backup_service,
                "_reconcile_v11",
                side_effect=stop_after_row,
            ):
                self.assertFalse(run(install).ok)
            self.assertEqual(generation_count(install), 2)

            real_copy = backup_service._copy

            def copy_after_row_repair(target, metadata):
                self.assertEqual(generation_count(install), 1)
                self.assertEqual(
                    len(canonical_managed_sqlite_files(
                        install,
                        exclude=(install.db_path,),
                    )),
                    1,
                )
                return real_copy(target, metadata)

            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=copy_after_row_repair,
            ):
                self.assertTrue(run(install).ok)

        with self.subTest(boundary="file_before_row_prune"), tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v12_target(
                fixed_fixture_target(install),
                enabled=True,
                generations=1,
            )
            setup_service.publish_setup_backup(install.target, 1)
            with mock.patch.object(
                backup_service,
                "delete_managed_backup_generation",
                side_effect=RuntimeError("injected file-before-row stop"),
            ):
                self.assertFalse(run(install).ok)
            self.assertEqual(generation_count(install), 2)
            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=AssertionError("repair-only retry must not copy"),
            ) as unexpected_copy:
                self.assertFalse(run(install).ok)
            unexpected_copy.assert_not_called()
            self.assertEqual(generation_count(install), 1)
            self.assertTrue(run(install).ok)

    def test_v10_missing_latest_artifact_fails_resolver_before_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            missing = MigrationBackupMetadata(
                generation_id=f"tg_backup_{1:032x}",
                published_at="2026-07-27T00:00:01Z",
                publication_retention=3,
            )
            create_v10_target(
                fixed_fixture_target(install),
                enabled=True,
                setup_backup=missing,
            )

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
            self.assertIsNone(payload["data"]["schema_from"])
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
