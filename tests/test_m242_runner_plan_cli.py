from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests.m14_test_support import make_physical_install
from tests.test_m242_runner_plan_edit import (
    FUTURE_VERIFICATION,
    draft_blob,
    runner_plan_edit_fixture,
)

from task_governance_tool import cli as cli_service
from task_governance_tool import verification_runner_plan_edit as edit_service
from task_governance_tool.verification_runner_plan import (
    PLAN_BLOB_UTF8_BYTE_LIMIT,
    VerificationRunnerPlanError,
    decode_verification_runner_plan,
)
from task_governance_tool import tasks as task_service


class BinaryInput(io.BytesIO):
    def __init__(self, payload: bytes = b"", *, readable: bool = True) -> None:
        super().__init__(payload)
        self.readable_by_contract = readable
        self.read_sizes: list[int] = []

    @property
    def buffer(self) -> BinaryInput:
        return self

    def read(self, size: int = -1) -> bytes:
        if not self.readable_by_contract:
            raise AssertionError("stdin must not be read")
        self.read_sizes.append(size)
        return super().read(size)


def invoke(
    fixture,
    *arguments: str,
    stdin: BinaryInput | None = None,
    json_output: bool = True,
    maintenance_enabled: bool = False,
    target=None,
) -> tuple[int, str, str]:
    supplied_stdin = stdin or BinaryInput(b"", readable=False)
    stdout = io.StringIO()
    stderr = io.StringIO()
    argv = [
        "--repo",
        str(fixture.repo),
        "task",
        "edit",
        fixture.task_id,
        *arguments,
    ]
    if json_output:
        argv.append("--json")
    with (
        mock.patch.object(sys, "stdin", supplied_stdin),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        code = cli_service.main(
            argv,
            _target_override=fixture.target if target is None else target,
            _maintenance_enabled=maintenance_enabled,
        )
    return code, stdout.getvalue(), stderr.getvalue()


class RunnerPlanCliTests(unittest.TestCase):
    def test_physical_public_cli_resolves_and_publishes_one_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary), git_managed=True)
            ignore = install.project_root / ".gitignore"
            ignore.write_text(
                ignore.read_text(encoding="utf-8")
                + "/.agents/skills/task-governance-tool/config/verification-runner.json\n",
                encoding="utf-8",
            )
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stdout or setup.stderr)
            added = install.run(
                "task",
                "add",
                "--title",
                "Public Runner Plan activation",
                "--status",
                "in_progress",
                "--review-tier",
                "2",
                "--verification",
                "python -m unittest -q tests.test_focused",
                "--contract-scope",
                "Exercise the public Plan action",
                "--contract-acceptance",
                "Publish exactly one ignored Plan",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]

            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(install.entrypoint),
                    "task",
                    "edit",
                    task_id,
                    "--runner-plan-action",
                    "replace",
                    "--json",
                ],
                cwd=install.project_root,
                input=draft_blob(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(
                payload["data"]["runner_plan_update"],
                {"action": "replace", "status": "updated"},
            )
            self.assertTrue(
                (
                    install.skill_root
                    / "config"
                    / "verification-runner.json"
                ).is_file()
            )

    def test_replace_reads_one_bounded_stdin_document_and_is_plan_only(self):
        with runner_plan_edit_fixture() as fixture:
            before = fixture.database_dump()
            raw_draft = draft_blob()
            supplied = BinaryInput(raw_draft + b" " * (65_536 - len(raw_draft)))
            self.assertEqual(PLAN_BLOB_UTF8_BYTE_LIMIT, 65_536)
            with (
                mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                ) as maintenance,
                mock.patch.object(
                    cli_service,
                    "select_current_verification_runner_basis",
                ) as runner_selection,
                mock.patch.object(
                    cli_service,
                    "set_review_target_with_optional_runner",
                ) as review_target,
            ):
                code, stdout, stderr = invoke(
                    fixture,
                    "--runner-plan-action",
                    "replace",
                    stdin=supplied,
                    maintenance_enabled=True,
                )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(
                payload["data"]["runner_plan_update"],
                {"action": "replace", "status": "updated"},
            )
            self.assertEqual(payload["data"]["changed_fields"], [])
            self.assertIsNone(payload["data"]["event"])
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(
                supplied.read_sizes,
                [65_537],
            )
            self.assertEqual(fixture.database_dump(), before)
            self.assertTrue(fixture.plan_path.is_file())
            maintenance.assert_not_called()
            runner_selection.assert_not_called()
            review_target.assert_not_called()

    def test_non_replace_and_actionless_edits_never_read_stdin(self):
        with runner_plan_edit_fixture() as fixture:
            fixture.write_exact_plan()
            for arguments, expected_update in (
                (
                    ("--runner-plan-action", "rebind"),
                    {"action": "rebind", "status": "unchanged"},
                ),
                (
                    ("--runner-plan-action", "detach"),
                    {"action": "detach", "status": "updated"},
                ),
                (
                    ("--runner-plan-action", "disable"),
                    {"action": "disable", "status": "updated"},
                ),
                (("--title", "Actionless update"), None),
            ):
                with self.subTest(arguments=arguments):
                    supplied = BinaryInput(b"", readable=False)
                    code, stdout, stderr = invoke(
                        fixture,
                        *arguments,
                        stdin=supplied,
                    )
                    self.assertEqual(code, 0, stderr)
                    data = json.loads(stdout)["data"]
                    if expected_update is None:
                        self.assertNotIn("runner_plan_update", data)
                    else:
                        self.assertEqual(data["runner_plan_update"], expected_update)
                    self.assertEqual(supplied.read_sizes, [])

    def test_actionless_output_is_byte_compatible_with_private_legacy_seam(self):
        with runner_plan_edit_fixture() as fixture:
            legacy_db = fixture.target.db_path.with_name("legacy.sqlite")
            with (
                closing(sqlite3.connect(fixture.target.db_path)) as source,
                closing(sqlite3.connect(legacy_db)) as destination,
            ):
                source.backup(destination)
            legacy_target = replace(
                fixture.target,
                db_path=legacy_db,
                canonical_fixed=False,
                skill_root=None,
            )
            for json_output in (True, False):
                with self.subTest(json_output=json_output):
                    suffix = "json" if json_output else "text"
                    with (
                        mock.patch.object(
                            task_service,
                            "utc_now",
                            return_value=f"2026-09-02T00:00:0{int(json_output)}Z",
                        ),
                        mock.patch.object(
                            task_service,
                            "generate_id",
                            return_value=f"tg_event_{suffix * 8}",
                        ),
                    ):
                        canonical = invoke(
                            fixture,
                            "--title",
                            f"Compatible {suffix} output",
                            json_output=json_output,
                        )
                        legacy = invoke(
                            fixture,
                            "--title",
                            f"Compatible {suffix} output",
                            json_output=json_output,
                            target=legacy_target,
                        )
                    self.assertEqual(canonical, legacy)
                    self.assertEqual(canonical[0], 0, canonical[2])
                    self.assertNotIn("runner_plan_update", canonical[1])
                    self.assertNotIn("Runner Plan:", canonical[1])

    def test_invalid_action_and_replace_input_fail_in_task_edit_envelope(self):
        cases = (
            (
                ("--runner-plan-action", "unknown"),
                BinaryInput(readable=False),
                "invalid_argument",
                "arguments are invalid",
            ),
            (
                ("--runner-plan-action", "replace"),
                BinaryInput(b"{"),
                "invalid_argument",
                "arguments are invalid",
            ),
            (
                ("--runner-plan-action", "replace"),
                BinaryInput(b"x" * 65_537),
                "invalid_argument",
                "arguments are invalid",
            ),
            (
                ("--runner-plan-action", "replace"),
                BinaryInput(draft_blob(argv=["token=private-value"])),
                "privacy_rejected",
                "Runner Plan draft appears to contain a secret, raw log, or dump content",
            ),
            (
                ("--runner-plan-action", "rebind"),
                BinaryInput(readable=False),
                "runner_plan_entry_required",
                "Runner Plan entry is required for rebind",
            ),
        )
        for arguments, supplied, error_code, error_message in cases:
            with self.subTest(error_code=error_code), runner_plan_edit_fixture() as fixture:
                before = fixture.database_dump()
                code, stdout, stderr = invoke(
                    fixture,
                    *arguments,
                    stdin=supplied,
                )
                self.assertEqual(code, 1, stderr)
                payload = json.loads(stdout)
                self.assertEqual(payload["command"], "task.edit")
                self.assertEqual(
                    payload["data"],
                    {"task": None, "changed_fields": [], "event": None},
                )
                self.assertEqual(
                    payload["errors"],
                    [{"code": error_code, "message": error_message}],
                )
                self.assertNotIn("private-value", stdout)
                self.assertEqual(fixture.database_dump(), before)
                self.assertFalse(fixture.plan_path.exists())

    def test_read_only_rejects_replace_before_stdin_is_read(self):
        with runner_plan_edit_fixture() as fixture:
            supplied = BinaryInput(b"", readable=False)
            code, stdout, stderr = invoke(
                fixture,
                "--runner-plan-action",
                "replace",
                "--read-only",
                stdin=supplied,
            )
            self.assertEqual(code, 1, stderr)
            self.assertEqual(
                json.loads(stdout)["errors"][0]["code"],
                "invalid_argument",
            )
            self.assertEqual(supplied.read_sizes, [])

    def test_actionless_basis_change_requires_disposition_for_exact_enabled_entry(self):
        with runner_plan_edit_fixture() as fixture:
            fixture.write_exact_plan()
            before = fixture.database_dump()
            supplied = BinaryInput(b"", readable=False)
            code, stdout, stderr = invoke(
                fixture,
                "--verification",
                FUTURE_VERIFICATION,
                stdin=supplied,
            )
            self.assertEqual(code, 1, stderr)
            payload = json.loads(stdout)
            self.assertEqual(
                payload["errors"],
                [
                    {
                        "code": "runner_plan_action_required",
                        "message": (
                            "Runner Plan action is required for this Task basis change"
                        ),
                    }
                ],
            )
            self.assertEqual(
                payload["data"],
                {"task": None, "changed_fields": [], "event": None},
            )
            self.assertEqual(supplied.read_sizes, [])
            self.assertEqual(fixture.database_dump(), before)

    def test_combined_basis_change_commits_task_plan_and_one_maintenance(self):
        with runner_plan_edit_fixture() as fixture:
            fixture.write_exact_plan()
            supplied = BinaryInput(readable=False)
            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
                return_value=[],
            ) as maintenance:
                code, stdout, stderr = invoke(
                    fixture,
                    "--verification",
                    FUTURE_VERIFICATION,
                    "--runner-plan-action",
                    "rebind",
                    stdin=supplied,
                    maintenance_enabled=True,
                )

            self.assertEqual(code, 0, stderr)
            data = json.loads(stdout)["data"]
            self.assertEqual(
                data["runner_plan_update"],
                {"action": "rebind", "status": "updated"},
            )
            self.assertIn("verification", data["changed_fields"])
            self.assertIsNotNone(data["event"])
            self.assertEqual(fixture.task_row()["verification"], FUTURE_VERIFICATION)
            plan = decode_verification_runner_plan(fixture.plan_path.read_bytes())
            matches = [entry for entry in plan.entries if entry.task_id == fixture.task_id]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].basis(), fixture.basis())
            self.assertEqual(supplied.read_sizes, [])
            maintenance.assert_called_once()

    def test_action_text_appends_one_fixed_runner_plan_line(self):
        with runner_plan_edit_fixture() as fixture:
            code, stdout, stderr = invoke(
                fixture,
                "--runner-plan-action",
                "replace",
                stdin=BinaryInput(draft_blob()),
                json_output=False,
            )
            self.assertEqual(code, 0, stderr)
            lines = stdout.rstrip("\n").splitlines()
            self.assertEqual(lines[-1], "Runner Plan: replace updated")
            self.assertEqual(sum(line.startswith("Runner Plan:") for line in lines), 1)

    def test_combined_postcommit_failure_is_success_with_ordered_warnings(self):
        with runner_plan_edit_fixture() as fixture:
            fixture.write_exact_plan()
            maintenance_warning = {
                "code": "viewer_refresh_deferred",
                "message": "Viewer refresh was deferred; task result is unchanged",
            }
            authoring_warning = {
                "code": "task_applied_runner_plan_unconfirmed",
                "message": (
                    "Task update completed but Runner Plan disposition is unconfirmed; "
                    "apply an explicit Plan action before relying on Runner execution"
                ),
            }
            with (
                mock.patch.object(
                    edit_service,
                    "publish_verification_runner_plan",
                    side_effect=VerificationRunnerPlanError(
                        code="runner_plan_changed",
                        message="private publisher detail",
                    ),
                ),
                mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                    return_value=[maintenance_warning],
                ) as maintenance,
            ):
                code, stdout, stderr = invoke(
                    fixture,
                    "--verification",
                    FUTURE_VERIFICATION,
                    "--runner-plan-action",
                    "rebind",
                    maintenance_enabled=True,
                )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["data"]["runner_plan_update"],
                {"action": "rebind", "status": "unconfirmed"},
            )
            self.assertEqual(
                set(payload["data"]),
                {"task", "changed_fields", "event", "runner_plan_update"},
            )
            self.assertEqual(
                payload["warnings"],
                [authoring_warning, maintenance_warning],
            )
            self.assertNotIn("private publisher detail", stdout)
            maintenance.assert_called_once()

        with runner_plan_edit_fixture() as fixture:
            fixture.write_exact_plan()
            maintenance_message = "Viewer refresh was deferred; task result is unchanged"
            with (
                mock.patch.object(
                    edit_service,
                    "publish_verification_runner_plan",
                    side_effect=VerificationRunnerPlanError(
                        code="runner_plan_changed",
                        message="private publisher detail",
                    ),
                ),
                mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                    return_value=[
                        {
                            "code": "viewer_refresh_deferred",
                            "message": maintenance_message,
                        }
                    ],
                ),
            ):
                code, stdout, stderr = invoke(
                    fixture,
                    "--verification",
                    FUTURE_VERIFICATION,
                    "--runner-plan-action",
                    "rebind",
                    json_output=False,
                    maintenance_enabled=True,
                )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(
                stdout.rstrip().splitlines()[-3:],
                [
                    "Runner Plan: rebind unconfirmed",
                    (
                        "Task update completed but Runner Plan disposition is unconfirmed; "
                        "apply an explicit Plan action before relying on Runner execution"
                    ),
                    maintenance_message,
                ],
            )

    def test_plan_only_publication_failure_is_exit_two_without_maintenance(self):
        cases = (
            (
                "runner_plan_changed",
                "Runner Plan changed before update; no Plan change was made",
            ),
            ("runner_plan_update_failed", "Runner Plan update did not complete"),
        )
        for error_code, error_message in cases:
            with self.subTest(error_code=error_code), runner_plan_edit_fixture() as fixture:
                fixture.write_exact_plan()
                before = fixture.database_dump()
                with (
                    mock.patch.object(
                        edit_service,
                        "publish_verification_runner_plan",
                        side_effect=VerificationRunnerPlanError(
                            code=error_code,
                            message=error_message,
                        ),
                    ),
                    mock.patch.object(
                        cli_service,
                        "run_post_commit_maintenance",
                    ) as maintenance,
                ):
                    code, stdout, stderr = invoke(
                        fixture,
                        "--runner-plan-action",
                        "rebind",
                        maintenance_enabled=True,
                    )

                self.assertEqual(code, 2, stderr)
                payload = json.loads(stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(
                    payload["errors"],
                    [{"code": error_code, "message": error_message}],
                )
                self.assertEqual(
                    payload["data"],
                    {"task": None, "changed_fields": [], "event": None},
                )
                self.assertEqual(fixture.database_dump(), before)
                maintenance.assert_not_called()

    def test_action_rejects_completion_options_without_mutation(self):
        cases = (
            ("done", ("--status", "done")),
            (
                "completion_evidence",
                ("--completion-evidence-kind", "commit_not_required"),
            ),
            ("commit_not_required", ("--commit-not-required",)),
            ("completion_commit", ("--completion-commit-hash", "a" * 40)),
            ("completion_revision", ("--completion-revision", "b" * 40)),
            (
                "completion_reason",
                ("--completion-evidence-reason", "Explicit completion basis"),
            ),
            ("external_revision", ("--external-revision-approved",)),
            ("reopen", ("--reopen-reason", "Explicit reopen")),
            ("verification_complete", ("--verification-complete",)),
            ("review_complete", ("--review-complete",)),
        )
        for name, incompatible in cases:
            with self.subTest(name=name), runner_plan_edit_fixture() as fixture:
                before = fixture.database_dump()
                supplied = BinaryInput(readable=False)
                code, stdout, stderr = invoke(
                    fixture,
                    "--runner-plan-action",
                    "replace",
                    *incompatible,
                    stdin=supplied,
                )
                self.assertEqual(code, 1, stderr)
                self.assertEqual(
                    json.loads(stdout)["errors"],
                    [
                        {
                            "code": "invalid_option_combination",
                            "message": (
                                "Runner Plan action cannot be combined with "
                                "these task edit options"
                            ),
                        }
                    ],
                )
                self.assertEqual(fixture.database_dump(), before)
                self.assertFalse(fixture.plan_path.exists())
                self.assertEqual(supplied.read_sizes, [])


if __name__ == "__main__":
    unittest.main()
