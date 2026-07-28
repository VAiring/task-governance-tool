from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    SOURCE_SKILL_ROOT,
    json_payload,
    make_physical_install,
)

from task_governance_tool import cli as cli_service
from task_governance_tool import maintenance as maintenance_service
from task_governance_tool import viewer_maintenance as viewer_maintenance_service
from task_governance_tool.backup import RoutineBackupResult
from task_governance_tool.maintenance import (
    BACKUP_WARNING_MESSAGES,
    MutationOutcome,
    VIEWER_WARNING_MESSAGES,
    run_post_commit_maintenance,
)
from task_governance_tool.storage import (
    connect_initialized_readonly,
    initialize_database,
    resolve_database_target,
)
from task_governance_tool.viewer_maintenance import ViewerRefreshResult


SCRIPT_PATH = SOURCE_SKILL_ROOT / "scripts" / "taskgov.py"


def make_target(root: Path):
    repo = root / "project"
    repo.mkdir()
    target = resolve_database_target(
        repo=repo,
        db=root / "state" / "taskgov.sqlite",
        script_path=SCRIPT_PATH,
    )
    initialize_database(target)
    return target


def invoke(
    target,
    *args: str,
    maintenance_enabled: bool = True,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    argv = ["--repo", str(target.project.canonical_repo), *args]
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli_service.main(
            argv,
            _target_override=target,
            _maintenance_enabled=maintenance_enabled,
        )
    return code, stdout.getvalue(), stderr.getvalue()


def add_contract_task(target, title: str = "Maintenance source") -> dict:
    code, stdout, stderr = invoke(
        target,
        "task",
        "add",
        "--title",
        title,
        "--status",
        "in_progress",
        "--contract-scope",
        "Initial scope",
        "--contract-acceptance",
        "Initial acceptance",
        "--json",
        maintenance_enabled=False,
    )
    if code != 0:
        raise AssertionError(stderr or stdout)
    return json.loads(stdout)["data"]["task"]


class PostCommitMaintenanceTests(unittest.TestCase):
    def test_warning_messages_are_the_fixed_continuing_contract(self):
        self.assertEqual(
            VIEWER_WARNING_MESSAGES,
            {
                "deferred": (
                    "Viewer refresh was deferred; task result is unchanged"
                ),
                "failed": (
                    "Viewer refresh did not complete; task result is unchanged"
                ),
            },
        )
        self.assertEqual(
            BACKUP_WARNING_MESSAGES,
            {
                "deferred": (
                    "managed backup was deferred; task result is unchanged"
                ),
                "failed": (
                    "managed backup did not complete; task result is unchanged"
                ),
            },
        )

    def test_false_outcome_skips_viewer_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                ) as viewer,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                ) as backup,
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=False,
                        viewer_relevant=True,
                    ),
                )

            self.assertEqual(warnings, [])
            viewer.assert_not_called()
            backup.assert_not_called()

    def test_viewer_runs_before_backup_and_fixed_warnings_keep_that_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            calls: list[str] = []

            def viewer_refresh(*args, **kwargs):
                calls.append("viewer")
                return ViewerRefreshResult(code="deferred", renders=0)

            def routine_backup(*args, **kwargs):
                calls.append("backup")
                return RoutineBackupResult(code="failed", attempted=True)

            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                    side_effect=viewer_refresh,
                ),
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    side_effect=routine_backup,
                ),
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                    observed_at="2026-07-27T00:00:00Z",
                )

            self.assertEqual(calls, ["viewer", "backup"])
            self.assertEqual(
                warnings,
                [
                    {
                        "code": "viewer_refresh_deferred",
                        "message": VIEWER_WARNING_MESSAGES["deferred"],
                    },
                    {
                        "code": "backup_failed",
                        "message": BACKUP_WARNING_MESSAGES["failed"],
                    },
                ],
            )

    def test_viewer_failure_does_not_prevent_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            backup_result = RoutineBackupResult(
                code="current",
                attempted=False,
            )
            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                    side_effect=RuntimeError("private Viewer failure"),
                ) as viewer,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    return_value=backup_result,
                ) as backup,
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                )

            viewer.assert_called_once()
            backup.assert_called_once()
            self.assertEqual(
                warnings,
                [
                    {
                        "code": "viewer_refresh_failed",
                        "message": VIEWER_WARNING_MESSAGES["failed"],
                    }
                ],
            )

    def test_physical_install_invalid_viewer_config_preserves_primary_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stderr)
            last_good = install.viewer_path.read_bytes()
            config = install.skill_root / "config" / "viewer.json"
            config.parent.mkdir()
            config.write_text('{"schema_version":1}', encoding="utf-8")

            added = install.run(
                "task",
                "add",
                "--title",
                "Primary mutation survives invalid Viewer config",
                "--json",
            )

            self.assertEqual(added.returncode, 0, added.stderr)
            payload = json_payload(added)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["data"]["task"]["title"],
                "Primary mutation survives invalid Viewer config",
            )
            self.assertEqual(
                payload["warnings"],
                [
                    {
                        "code": "viewer_refresh_failed",
                        "message": VIEWER_WARNING_MESSAGES["failed"],
                    }
                ],
            )
            self.assertEqual(install.viewer_path.read_bytes(), last_good)

            shown = install.run(
                "task",
                "show",
                payload["data"]["task"]["task_id"],
                "--read-only",
                "--json",
            )
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(
                json_payload(shown)["data"]["task"]["title"],
                "Primary mutation survives invalid Viewer config",
            )
            doctor = install.run("doctor", "--read-only", "--json")
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            viewer = json_payload(doctor)["data"]["components"]["maintenance"][
                "viewer"
            ]
            self.assertTrue(viewer["due"])
            self.assertEqual(viewer["last_outcome"]["code"], "failed")

    def test_backup_failure_preserves_the_viewer_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                    return_value=ViewerRefreshResult(
                        code="deferred",
                        renders=0,
                    ),
                ) as viewer,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    side_effect=RuntimeError("private backup failure"),
                ) as backup,
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                )

            viewer.assert_called_once()
            backup.assert_called_once()
            self.assertEqual(
                warnings,
                [
                    {
                        "code": "viewer_refresh_deferred",
                        "message": VIEWER_WARNING_MESSAGES["deferred"],
                    },
                    {
                        "code": "backup_failed",
                        "message": BACKUP_WARNING_MESSAGES["failed"],
                    },
                ],
            )

    def test_handoff_outcome_runs_backup_without_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                ) as viewer,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    return_value=RoutineBackupResult(
                        code="current",
                        attempted=False,
                    ),
                ) as backup,
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=False,
                    ),
                )

            self.assertEqual(warnings, [])
            viewer.assert_not_called()
            backup.assert_called_once()

    def test_pre_opt_in_viewer_path_does_not_lock_render_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            viewer_directory = target.db_path.parent / "viewer"
            with (
                mock.patch.object(
                    viewer_maintenance_service,
                    "zero_wait_artifact_lock",
                ) as artifact_lock,
                mock.patch.object(
                    viewer_maintenance_service,
                    "build_viewer_snapshot",
                ) as snapshot,
                mock.patch.object(
                    viewer_maintenance_service,
                    "render_viewer_html",
                ) as render,
                mock.patch.object(
                    viewer_maintenance_service,
                    "write_viewer_html",
                ) as write,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    return_value=RoutineBackupResult(
                        code="not_opted_in",
                        attempted=False,
                    ),
                ),
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                )

            self.assertEqual(warnings, [])
            artifact_lock.assert_not_called()
            snapshot.assert_not_called()
            render.assert_not_called()
            write.assert_not_called()
            self.assertFalse(viewer_directory.exists())

    def test_cli_commits_and_closes_the_business_connection_before_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            opened_connections: list[sqlite3.Connection] = []
            real_connect = cli_service.connect_initialized

            def tracked_connect(database_target):
                connection = real_connect(database_target)
                opened_connections.append(connection)
                return connection

            def inspect_after_commit(database_target, outcome):
                self.assertEqual(
                    outcome,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                )
                self.assertEqual(len(opened_connections), 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    opened_connections[0].execute("SELECT 1")
                with closing(
                    connect_initialized_readonly(database_target)
                ) as connection:
                    row = connection.execute(
                        """
                        SELECT title
                          FROM tasks
                         WHERE project_id = ?
                        """,
                        (database_target.project.project_id,),
                    ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["title"], "Closed before maintenance")
                return []

            with (
                mock.patch.object(
                    cli_service,
                    "connect_initialized",
                    side_effect=tracked_connect,
                ),
                mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                    side_effect=inspect_after_commit,
                ) as coordinator,
            ):
                code, stdout, stderr = invoke(
                    target,
                    "task",
                    "add",
                    "--title",
                    "Closed before maintenance",
                    "--json",
                )

            self.assertEqual(code, 0, stderr or stdout)
            coordinator.assert_called_once()

    def test_json_and_text_warnings_are_fixed_and_primary_success_is_unchanged(self):
        cases = (
            (
                [
                    {
                        "code": "viewer_refresh_deferred",
                        "message": VIEWER_WARNING_MESSAGES["deferred"],
                    },
                    {
                        "code": "backup_failed",
                        "message": BACKUP_WARNING_MESSAGES["failed"],
                    },
                ],
                True,
            ),
            (
                [
                    {
                        "code": "viewer_refresh_failed",
                        "message": VIEWER_WARNING_MESSAGES["failed"],
                    },
                    {
                        "code": "backup_deferred",
                        "message": BACKUP_WARNING_MESSAGES["deferred"],
                    },
                ],
                False,
            ),
        )
        for index, (warnings, json_output) in enumerate(cases):
            with self.subTest(warnings=warnings), tempfile.TemporaryDirectory() as tmp:
                target = make_target(Path(tmp))
                args = [
                    "task",
                    "add",
                    "--title",
                    f"Warning task {index}",
                ]
                if json_output:
                    args.append("--json")
                with mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                    return_value=warnings,
                ) as coordinator:
                    code, stdout, stderr = invoke(target, *args)

                self.assertEqual(code, 0, stderr)
                self.assertEqual(stderr, "")
                self.assertEqual(coordinator.call_count, 1)
                outcome = coordinator.call_args.args[1]
                self.assertEqual(
                    outcome,
                    MutationOutcome(state_changed=True, viewer_relevant=True),
                )
                if json_output:
                    payload = json.loads(stdout)
                    self.assertTrue(payload["ok"])
                    self.assertEqual(payload["command"], "task.add")
                    self.assertEqual(payload["warnings"], warnings)
                    self.assertEqual(
                        payload["data"]["task"]["title"],
                        f"Warning task {index}",
                    )
                else:
                    self.assertIn("Task added:", stdout)
                    first_position = stdout.find(warnings[0]["message"])
                    second_position = stdout.find(warnings[1]["message"])
                    self.assertGreaterEqual(first_position, 0)
                    self.assertGreater(second_position, first_position)
                    self.assertNotIn("Traceback", stdout)

    def test_read_failure_and_contract_replay_never_call_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            task = add_contract_task(target)

            commands = (
                (
                    "read",
                    (
                        "task",
                        "list",
                        "--read-only",
                        "--json",
                    ),
                    0,
                ),
                (
                    "failure",
                    (
                        "task",
                        "edit",
                        "tg_task_missing",
                        "--add-note",
                        "Must not persist",
                        "--json",
                    ),
                    1,
                ),
                (
                    "contract replay",
                    (
                        "task",
                        "edit",
                        task["task_id"],
                        "--contract-scope",
                        "Initial scope",
                        "--contract-acceptance",
                        "Initial acceptance",
                        "--json",
                    ),
                    0,
                ),
            )
            for label, args, expected_code in commands:
                with self.subTest(case=label), mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                ) as coordinator:
                    code, stdout, stderr = invoke(target, *args)

                self.assertEqual(code, expected_code, stderr)
                coordinator.assert_not_called()
                if label == "contract replay":
                    payload = json.loads(stdout)
                    self.assertEqual(payload["data"]["changed_fields"], [])
                    self.assertIsNone(payload["data"]["event"])
                    self.assertEqual(
                        payload["data"]["contract_write"],
                        {"recorded": False, "revision": 1},
                    )

    def test_handoff_record_withdraw_trigger_but_exact_record_replay_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            task = add_contract_task(target, "Handoff maintenance source")
            record_args = (
                "handoff",
                "record",
                task["task_id"],
                "--summary",
                "Future work outside this task",
                "--json",
            )

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
                return_value=[],
            ) as first_coordinator:
                first_code, first_stdout, first_stderr = invoke(
                    target,
                    *record_args,
                )
            self.assertEqual(first_code, 0, first_stderr)
            first_coordinator.assert_called_once()
            self.assertEqual(
                first_coordinator.call_args.args[1],
                MutationOutcome(state_changed=True, viewer_relevant=False),
            )
            handoff_id = json.loads(first_stdout)["data"]["handoff"]["handoff_id"]

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
            ) as replay_coordinator:
                replay_code, replay_stdout, replay_stderr = invoke(
                    target,
                    *record_args,
                )
            self.assertEqual(replay_code, 0, replay_stderr)
            replay_coordinator.assert_not_called()
            replay = json.loads(replay_stdout)
            self.assertTrue(replay["data"]["local_record"]["replayed"])
            self.assertFalse(replay["data"]["local_record"]["created"])

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
                return_value=[],
            ) as withdraw_coordinator:
                withdraw_code, _, withdraw_stderr = invoke(
                    target,
                    "handoff",
                    "withdraw",
                    handoff_id,
                    "--reason",
                    "User withdrew the future candidate",
                    "--json",
                )
            self.assertEqual(withdraw_code, 0, withdraw_stderr)
            withdraw_coordinator.assert_called_once()
            self.assertEqual(
                withdraw_coordinator.call_args.args[1],
                MutationOutcome(state_changed=True, viewer_relevant=False),
            )


if __name__ == "__main__":
    unittest.main()
