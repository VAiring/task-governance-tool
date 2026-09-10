import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
try:
    from task_governance_tool import cli as cli_service
    from task_governance_tool.cli import make_context
    from task_governance_tool.cli_output import (
        CommandResult,
        emit_result,
        serialized_json_size,
        success_result,
    )
    from task_governance_tool.cli_parser import build_parser
    from task_governance_tool.maintenance import MutationOutcome
finally:
    sys.path.pop(0)

try:
    from m14_test_support import file_snapshot, make_physical_install
except ModuleNotFoundError:
    from tests.m14_test_support import file_snapshot, make_physical_install


ENVELOPE_KEYS = {
    "ok",
    "command",
    "project_id",
    "data",
    "warnings",
    "errors",
}


class CliEnvelopeTests(unittest.TestCase):
    def assert_typed_ordered_json_equal(self, actual, expected):
        self.assertIs(type(actual), type(expected))
        if isinstance(expected, dict):
            self.assertEqual(list(actual), list(expected))
            for key in expected:
                self.assert_typed_ordered_json_equal(actual[key], expected[key])
        elif isinstance(expected, list):
            self.assertEqual(len(actual), len(expected))
            for actual_item, expected_item in zip(actual, expected):
                self.assert_typed_ordered_json_equal(actual_item, expected_item)
        else:
            self.assertEqual(actual, expected)

    def test_compact_json_preserves_legacy_values_types_and_recursive_key_order(self):
        data = {
            "z_rows": [
                {"z_title": "日本語 😀", "a_value": None},
                [True, 1, False, 0, 1.5, "line\nnext\tvalue"],
            ],
            "a_contract": {"z_scope": "表示の確認", "a_revision": 1},
        }
        success = CommandResult(
            ok=True,
            command="task.show",
            project_id="tg_project_test",
            data=data,
        )
        cases = (
            success,
            replace(
                success,
                warnings=[
                    {"message": "first warning", "code": "warning_one"},
                    {"message": "second warning", "code": "warning_two"},
                ],
            ),
            CommandResult(
                ok=False,
                command="task.show",
                data={"task": None},
                errors=[{"message": "task was not found", "code": "task_not_found"}],
                exit_code=2,
            ),
            CommandResult(
                ok=False,
                command="parse",
                errors=[{"message": "arguments are invalid", "code": "invalid_argument"}],
                exit_code=1,
            ),
            replace(success, command="review.prepare"),
        )
        for result in cases:
            with self.subTest(command=result.command, warnings=bool(result.warnings)):
                # The old emitter is an independent oracle for payload and order;
                # presentation changes must preserve every decoded JSON value.
                legacy = json.dumps(
                    result.to_json_object(),
                    indent=2,
                    sort_keys=result.command != "review.prepare",
                ) + "\n"
                stdout = io.StringIO()
                stderr = io.StringIO()
                with mock.patch.object(sys, "stdout", stdout), mock.patch.object(
                    sys, "stderr", stderr
                ):
                    status = emit_result(result, json_output=True)
                rendered = stdout.getvalue()

                self.assertEqual(status, result.exit_code)
                self.assertEqual(stderr.getvalue(), "")
                self.assert_typed_ordered_json_equal(
                    json.loads(rendered), json.loads(legacy)
                )
                self.assertEqual(rendered.count("\n"), 1)
                self.assertTrue(rendered.endswith("}\n"))
                self.assertLess(len(rendered.encode("utf-8")), len(legacy.encode("utf-8")))
                if result.data == data:
                    self.assertIn("日本語 😀", rendered)
                    self.assertNotIn("\\u65e5", rendered)

    def test_compact_json_keeps_legacy_identity_and_diagnostic_omission_boundaries(self):
        identity = CommandResult(
            ok=True,
            command="task.current",
            project_id="project-" + "日" * 100,
            data={"tasks": [], "returned_count": 0},
        )
        diagnostic = CommandResult(
            ok=False,
            command="parse",
            errors=[{"code": "invalid_argument", "message": "確認" * 100}],
            exit_code=1,
        )
        for result in (identity, diagnostic):
            with self.subTest(command=result.command):
                # Freeze the pre-compaction admission model, including ASCII
                # escaping, indentation, and portable CRLF for every newline.
                legacy = (
                    json.dumps(result.to_json_object(), indent=2, sort_keys=True)
                    + "\n"
                ).replace("\n", "\r\n").encode("utf-8")
                self.assertEqual(serialized_json_size(result, result.data), len(legacy))
                for limit in (len(legacy), len(legacy) - 1):
                    with self.subTest(limit=limit):
                        stdout = io.StringIO()
                        with mock.patch.object(sys, "stdout", stdout):
                            status = emit_result(
                                result, json_output=True, max_json_bytes=limit
                            )
                        payload = json.loads(stdout.getvalue())
                        expected = result.to_json_object()
                        if limit < len(legacy):
                            if result.project_id is not None:
                                expected["project_id"] = None
                            else:
                                expected["errors"] = [{
                                    "code": "invalid_argument",
                                    "message": (
                                        "diagnostic details omitted to satisfy "
                                        "the bounded output limit"
                                    ),
                                }]
                        self.assertEqual(payload, expected)
                        self.assertEqual(status, result.exit_code)
                        self.assertLessEqual(len(stdout.getvalue().encode("utf-8")), limit)
                # The smaller wire representation must not admit material that
                # the old size model omitted just one byte below the boundary.
                unrestricted = io.StringIO()
                with mock.patch.object(sys, "stdout", unrestricted):
                    emit_result(result, json_output=True)
                self.assertLess(
                    len(unrestricted.getvalue().encode("utf-8")), len(legacy) - 1
                )

    def test_text_emission_preserves_text_precedence_streams_and_newlines(self):
        cases = (
            (success_result("task.show", "Task: 日本語 😀\nStatus: todo"),
             "Task: 日本語 😀\nStatus: todo\n", ""),
            (success_result("review.prepare", "Task: 日本語 😀\nScope: local"),
             "Task: 日本語 😀\nScope: local\n", ""),
            (CommandResult(
                ok=False, command="parse", exit_code=1,
                errors=[{"code": "invalid_argument", "message": "arguments are invalid"}],
            ), "", "arguments are invalid\n"),
        )
        for result, expected_stdout, expected_stderr in cases:
            with self.subTest(command=result.command):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with mock.patch.object(sys, "stdout", stdout), mock.patch.object(
                    sys, "stderr", stderr
                ):
                    status = emit_result(result, json_output=False)
                self.assertEqual(stdout.getvalue(), expected_stdout)
                self.assertEqual(stderr.getvalue(), expected_stderr)
                self.assertEqual(status, result.exit_code)

    def test_installed_json_is_utf8_with_restrictive_python_stdout_encoding(self):
        title = "日本語の出力確認 😀"
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            def run_raw(*args, encoding="ascii"):
                environment = os.environ.copy()
                environment.pop("PYTHONPATH", None)
                environment["PYTHONIOENCODING"] = encoding + ":strict"
                environment["PYTHONUTF8"] = "0"
                # Do not use -I: it would ignore PYTHONIOENCODING and mask the
                # Windows redirected-stdout regression this test exercises.
                return subprocess.run(
                    [sys.executable, "-B", "-S", str(install.entrypoint), *args],
                    cwd=install.project_root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=30,
                )

            setup = run_raw("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stdout or setup.stderr)
            added = run_raw("task", "add", "--title", title, "--json")
            self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
            task_id = json.loads(added.stdout.decode("utf-8"))["data"]["task"]["task_id"]
            before = file_snapshot(install.project_root)
            for encoding in ("cp932", "ascii"):
                with self.subTest(encoding=encoding):
                    shown = run_raw("task", "show", task_id, "--json", encoding=encoding)
                    self.assertEqual(shown.returncode, 0, shown.stdout or shown.stderr)
                    self.assertEqual(shown.stderr, b"")
                    self.assertEqual(shown.stdout.count(b"\n"), 1)
                    self.assertTrue(shown.stdout.endswith(b"}\n"))
                    self.assertNotIn(b"\r", shown.stdout)
                    self.assertIn(title.encode("utf-8"), shown.stdout)
                    self.assertEqual(
                        json.loads(shown.stdout.decode("utf-8"))["data"]["task"]["title"],
                        title,
                    )
                    rejected = run_raw("--json", "--unknown-option", encoding=encoding)
                    self.assertEqual(rejected.returncode, 1)
                    self.assertEqual(rejected.stderr, b"")
                    self.assertEqual(rejected.stdout.count(b"\n"), 1)
                    self.assertNotIn(b"\r", rejected.stdout)
                    self.assertEqual(json.loads(rejected.stdout.decode("utf-8")), {
                        "ok": False,
                        "command": "parse",
                        "project_id": None,
                        "data": {},
                        "warnings": [],
                        "errors": [{"code": "invalid_argument", "message": "arguments are invalid"}],
                    })
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_success_result_json_object_contains_only_m14_envelope_keys(self):
        payload = success_result("doctor", "ok", {"example": True}).to_json_object()

        self.assertEqual(set(payload), ENVELOPE_KEYS)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "doctor")
        self.assertEqual(payload["data"], {"example": True})
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["warnings"], [])
        self.assertNotIn("db_path", payload)

    def test_result_keeps_maintenance_metadata_out_of_public_envelope(self):
        outcome = MutationOutcome(state_changed=True, viewer_relevant=False)
        target = object()
        result = CommandResult(
            ok=True,
            command="task.edit",
            data={"task": None},
            mutation_outcome=outcome,
            maintenance_target=target,
        )

        self.assertIs(result.mutation_outcome, outcome)
        self.assertIs(result.maintenance_target, target)
        payload = result.to_json_object()
        self.assertEqual(set(payload), ENVELOPE_KEYS)
        self.assertEqual(json.loads(json.dumps(payload)), payload)
        self.assertNotIn("mutation_outcome", repr(result))
        self.assertNotIn("maintenance_target", repr(result))

    def test_argparse_validation_error_uses_path_free_json_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            result = install.run("--json", "--unknown-option")

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload), ENVELOPE_KEYS)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["command"], "parse")
            self.assertEqual(
                payload["errors"],
                [{"code": "invalid_argument", "message": "arguments are invalid"}],
            )
            self.assertNotIn("--unknown-option", result.stdout)
            self.assertNotIn("db_path", result.stdout)

    def test_argparse_type_errors_never_echo_rejected_values(self):
        secret_value = "TOPSECRET-DO-NOT-ECHO"
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            structured = install.run(
                "setup",
                "--backup-interval-minutes",
                secret_value,
                "--json",
            )
            text = install.run(
                "setup",
                "--backup-interval-minutes",
                secret_value,
            )

            self.assertEqual(structured.returncode, 1)
            self.assertEqual(structured.stderr, "")
            self.assertNotIn(secret_value, structured.stdout)
            self.assertEqual(
                json.loads(structured.stdout)["errors"],
                [{"code": "invalid_argument", "message": "arguments are invalid"}],
            )
            self.assertEqual(text.returncode, 1)
            self.assertEqual(text.stdout, "")
            self.assertEqual(text.stderr, "arguments are invalid\n")
            self.assertNotIn(secret_value, text.stderr)

    def test_removed_command_json_is_fixed_and_pre_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = file_snapshot(install.project_root)
            for command in ("self", "db", "unknown-root"):
                with self.subTest(command=command):
                    result = install.run("--json", command, "ignored", "--repo", "missing")
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stderr, "")
                    payload = json.loads(result.stdout)
                    self.assertEqual(set(payload), ENVELOPE_KEYS)
                    self.assertEqual(
                        payload,
                        {
                            "ok": False,
                            "command": "parse",
                            "project_id": None,
                            "data": {},
                            "warnings": [],
                            "errors": [
                                {
                                    "code": "invalid_command",
                                    "message": "command is not available",
                                }
                            ],
                        },
                    )
            self.assertEqual(file_snapshot(install.project_root), before)
            self.assertFalse((install.skill_root / "state").exists())

    def test_removed_command_text_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            for command in ("self", "db", "unknown-root"):
                with self.subTest(command=command):
                    result = install.run(command)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "taskgov: command is not available\n")

    def test_removed_db_option_has_precedence_and_never_echoes_its_value(self):
        secret_value = "C:/private/DO_NOT_ECHO.sqlite"
        cases = (
            ("self", "status", "--db", secret_value, "--json"),
            ("unknown-root", "--db", secret_value, "--json"),
            ("--db", secret_value, "task", "next", "--json"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = file_snapshot(install.project_root)
            for args in cases:
                with self.subTest(args=args):
                    result = install.run(*args)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stderr, "")
                    self.assertNotIn(secret_value, result.stdout)
                    payload = json.loads(result.stdout)
                    self.assertEqual(set(payload), ENVELOPE_KEYS)
                    self.assertEqual(
                        payload["errors"],
                        [{"code": "invalid_option", "message": "option is not available"}],
                    )
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_removed_db_option_is_rejected_even_after_end_of_options(self):
        secret_value = "C:/private/AFTER_END_OF_OPTIONS.sqlite"
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            structured = install.run(
                "--json",
                "task",
                "list",
                "--",
                f"--db={secret_value}",
            )
            self.assertEqual(structured.returncode, 2)
            self.assertEqual(structured.stderr, "")
            self.assertNotIn(secret_value, structured.stdout)
            self.assertEqual(
                json.loads(structured.stdout)["errors"],
                [{"code": "invalid_option", "message": "option is not available"}],
            )

            text = install.run(
                "task",
                "list",
                "--",
                f"--db={secret_value}",
            )
            self.assertEqual(text.returncode, 2)
            self.assertEqual(text.stdout, "")
            self.assertEqual(text.stderr, "taskgov: option is not available\n")
            self.assertNotIn(secret_value, text.stderr)

    def test_common_options_work_before_or_after_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            for args in (
                ("doctor", "--json", "--read-only"),
                ("--json", "--read-only", "doctor"),
            ):
                with self.subTest(args=args):
                    result = install.run(*args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(set(payload), ENVELOPE_KEYS)
                    self.assertEqual(payload["command"], "doctor")
                    self.assertEqual(
                        payload["data"]["components"]["project_state"]["code"],
                        "setup_required",
                    )
            self.assertFalse((install.skill_root / "state").exists())

    def test_missing_task_subcommand_remains_a_validation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = make_physical_install(Path(tmp)).run("--json", "task")

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "parse")
        self.assertEqual(payload["errors"][0]["code"], "invalid_argument")

    def test_read_only_reaches_setup_command_context_without_db_option(self):
        parser = build_parser()
        args = parser.parse_args(["setup", "--read-only"])
        context = make_context(args)

        self.assertEqual(context.command, "setup")
        self.assertTrue(context.read_only)
        self.assertFalse(hasattr(context, "db") and context.db is not None)

    def test_setup_confirmation_token_reaches_service_without_echo(self):
        token = "tgr1.payload.checksum"
        parser = build_parser()
        context = make_context(
            parser.parse_args(["setup", "--confirm-relocation", token])
        )
        service_result = SimpleNamespace(
            ok=True,
            project_id="tg_project_test",
            data={"status": "setup_complete"},
            error_code=None,
            error_message=None,
            text="Setup complete",
        )

        with mock.patch.object(
            cli_service,
            "run_setup",
            return_value=service_result,
        ) as run_setup:
            result = cli_service.handle_setup(context)

        self.assertTrue(result.ok)
        self.assertNotIn(token, result.text)
        run_setup.assert_called_once_with(
            repo=".",
            repo_explicit=False,
            script_path=cli_service.cli_script_path(),
            read_only=False,
            backup_interval_minutes=None,
            backup_generations=None,
            confirmation_token=token,
        )

    def test_read_only_relocation_confirmation_is_pre_resolution_usage_error(self):
        secret_token = "tgr1.PRIVATE_PAYLOAD.PRIVATE_CHECKSUM"
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            missing_repo = install.project_root / "missing-project"
            before = file_snapshot(install.project_root)
            cases = (
                (
                    "setup",
                    "--repo",
                    str(missing_repo),
                    "--read-only",
                    "--confirm-relocation",
                    secret_token,
                    "--json",
                ),
                (
                    "--repo",
                    str(missing_repo),
                    "--read-only",
                    "--json",
                    "setup",
                    "--confirm-relocation",
                    secret_token,
                ),
            )

            for arguments in cases:
                with self.subTest(arguments=arguments):
                    result = install.run(*arguments)

                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stderr, "")
                    self.assertNotIn(secret_token, result.stdout)
                    self.assertEqual(
                        json.loads(result.stdout)["errors"],
                        [{
                            "code": "invalid_option_combination",
                            "message": (
                                "--confirm-relocation cannot be used with "
                                "--read-only"
                            ),
                        }],
                    )
            self.assertEqual(file_snapshot(install.project_root), before)
            self.assertFalse(missing_repo.exists())


if __name__ == "__main__":
    unittest.main()
