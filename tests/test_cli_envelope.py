import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
try:
    from task_governance_tool import cli as cli_service
    from task_governance_tool.cli import build_parser, make_context, success_result
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
    def test_success_result_json_object_contains_only_m14_envelope_keys(self):
        payload = success_result("doctor", "ok", {"example": True}).to_json_object()

        self.assertEqual(set(payload), ENVELOPE_KEYS)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "doctor")
        self.assertEqual(payload["data"], {"example": True})
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["warnings"], [])
        self.assertNotIn("db_path", payload)

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
