from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.m14_test_support import SOURCE_SKILL_ROOT

from task_governance_tool import cli as cli_service
from task_governance_tool import maintenance as maintenance_service
from task_governance_tool.maintenance import (
    BACKUP_WARNING_MESSAGES,
    MutationOutcome,
    run_post_commit_maintenance,
)
from task_governance_tool.storage import initialize_database, resolve_database_target


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
    def test_false_outcome_skips_backup_without_an_opt_in_or_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with mock.patch.object(
                maintenance_service,
                "run_routine_backup",
            ) as routine:
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=False,
                        viewer_relevant=True,
                    ),
                )

            self.assertEqual(warnings, [])
            routine.assert_not_called()

    def test_json_and_text_warnings_are_fixed_and_primary_success_is_unchanged(self):
        cases = (
            (
                "backup_deferred",
                BACKUP_WARNING_MESSAGES["deferred"],
                True,
            ),
            (
                "backup_failed",
                BACKUP_WARNING_MESSAGES["failed"],
                False,
            ),
        )
        for index, (warning_code, warning_message, json_output) in enumerate(cases):
            with self.subTest(code=warning_code), tempfile.TemporaryDirectory() as tmp:
                target = make_target(Path(tmp))
                warning = [{"code": warning_code, "message": warning_message}]
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
                    return_value=warning,
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
                    self.assertEqual(payload["warnings"], warning)
                    self.assertEqual(
                        payload["data"]["task"]["title"],
                        f"Warning task {index}",
                    )
                else:
                    self.assertIn("Task added:", stdout)
                    self.assertIn(warning_message, stdout)
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
