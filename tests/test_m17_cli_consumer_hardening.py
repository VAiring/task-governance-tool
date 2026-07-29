from __future__ import annotations

import ast
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.m14_test_support import make_physical_install

from task_governance_tool import cli as cli_service
from task_governance_tool import setup as setup_service
from task_governance_tool.storage import StorageError, connect


TASK_ID = "tg_task_aaaaaaaaaaaaaaaa"
RAW_TASK_MARKER = "SENSITIVE_MARKER_123"

# setup and doctor have their own service entry paths. These are the 18
# stateful public leaves that must share the normal command resolver.
STATEFUL_LEAVES: tuple[tuple[str, str], ...] = (
    ("task.add", "task add --title Task"),
    ("task.list", "task list"),
    ("task.next", "task next"),
    ("task.current", "task current"),
    ("task.effort", f"task effort {TASK_ID}"),
    ("task.show", f"task show {TASK_ID}"),
    (
        "task.checkpoint",
        f"task checkpoint {TASK_ID} --summary checkpoint --next-action continue",
    ),
    ("task.edit", f"task edit {TASK_ID} --title Task"),
    ("task.complete", f"task complete {TASK_ID} --check"),
    (
        "handoff.record",
        f"handoff record {TASK_ID} --summary discovery",
    ),
    ("handoff.list", "handoff list"),
    ("handoff.show", "handoff show tg_handoff_missing"),
    (
        "handoff.withdraw",
        "handoff withdraw tg_handoff_missing --reason withdraw",
    ),
    ("review.prepare", f"review prepare {TASK_ID}"),
    (
        "review.target.set",
        f"review target set {TASK_ID} --kind external_revision --revision revision-1",
    ),
    (
        "review.receipt.add",
        f"review receipt add {TASK_ID} --reviewer reviewer "
        "--kind independent --verdict pass",
    ),
    (
        "review.finding.add",
        f"review finding add {TASK_ID} --receipt-id tg_review_receipt_missing "
        "--severity low --summary finding",
    ),
    (
        "review.finding.resolve",
        "review finding resolve tg_review_finding_missing --resolution resolved",
    ),
)


def _setup(install) -> None:
    result = setup_service.run_setup(
        repo=str(install.project_root),
        repo_explicit=True,
        script_path=install.entrypoint,
        read_only=False,
        backup_interval_minutes=None,
        backup_generations=None,
    )
    if not result.ok:
        raise AssertionError(result)


def _run_cli(
    install,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    argv = [*args, "--repo", str(install.project_root), "--json"]
    with (
        mock.patch.object(
            cli_service,
            "_CLI_SCRIPT_PATH",
            install.entrypoint,
        ),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        returncode = cli_service.main(
            argv,
            _maintenance_enabled=False,
        )
    return subprocess.CompletedProcess(
        args=argv,
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _assert_connection_closed(
    testcase: unittest.TestCase,
    connection: sqlite3.Connection,
) -> None:
    with testcase.assertRaises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def _capture_resolutions(real_resolve, captured: list[sqlite3.Connection]):
    def capture(**kwargs):
        resolution = real_resolve(**kwargs)
        if resolution.read_connection is not None:
            captured.append(resolution.read_connection)
        return resolution

    return capture


class M17CliConsumerHardeningTests(unittest.TestCase):
    def test_production_consumers_do_not_import_legacy_target_resolvers(self):
        package_root = Path(cli_service.__file__).resolve().parent
        forbidden = {
            "default_db_path",
            "project_identity",
            "resolve_database_target",
        }
        violations: list[str] = []
        for source in sorted(package_root.glob("*.py")):
            if source.name == "storage.py":
                continue
            tree = ast.parse(
                source.read_text(encoding="utf-8"),
                filename=str(source),
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for imported in node.names:
                        if imported.name in forbidden:
                            violations.append(
                                f"{source.name}:{node.lineno}:{imported.name}"
                            )
                elif isinstance(node, ast.Call):
                    called = node.func
                    name = (
                        called.id
                        if isinstance(called, ast.Name)
                        else (
                            called.attr
                            if isinstance(called, ast.Attribute)
                            else None
                        )
                    )
                    if name in forbidden:
                        violations.append(
                            f"{source.name}:{node.lineno}:{name}"
                        )

        self.assertEqual(violations, [])

    def test_all_stateful_leaves_resolve_once_and_fail_closed_before_setup(self):
        self.assertEqual(len(STATEFUL_LEAVES), 18)
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            real_resolve = cli_service.resolve_project_state

            for expected_command, command_line in STATEFUL_LEAVES:
                with self.subTest(command=expected_command):
                    with mock.patch.object(
                        cli_service,
                        "resolve_project_state",
                        wraps=real_resolve,
                    ) as resolve:
                        result = _run_cli(install, *command_line.split())

                    payload = _payload(result)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stderr, "")
                    self.assertNotIn("Traceback", result.stdout)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["command"], expected_command)
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "db_not_initialized",
                    )
                    self.assertEqual(resolve.call_count, 1)

            self.assertFalse(install.db_path.exists())

    def test_resolve_context_target_never_falls_back_to_another_resolver(self):
        args = cli_service.build_parser().parse_args(
            ["task", "list", "--repo", ".", "--json"]
        )
        context = cli_service.make_context(args)

        with (
            mock.patch.object(cli_service, "resolve_project_state") as resolve,
            self.assertRaises(StorageError) as raised,
        ):
            cli_service.resolve_context_target(context)

        self.assertEqual(raised.exception.code, "internal_error")
        resolve.assert_not_called()

    def test_retained_read_connection_closes_on_all_dispatch_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            _setup(install)
            real_resolve = cli_service.resolve_project_state

            def run_and_capture(*args: str):
                captured: list[sqlite3.Connection] = []

                with mock.patch.object(
                    cli_service,
                    "resolve_project_state",
                    side_effect=_capture_resolutions(
                        real_resolve,
                        captured,
                    ),
                ):
                    result = _run_cli(install, *args)
                self.assertEqual(len(captured), 1)
                _assert_connection_closed(self, captured[0])
                return result

            success = run_and_capture("task", "list")
            self.assertEqual(success.returncode, 0)

            domain_error = run_and_capture(
                "task",
                "list",
                "--limit",
                "0",
            )
            self.assertEqual(domain_error.returncode, 1)
            self.assertEqual(
                _payload(domain_error)["errors"][0]["code"],
                "invalid_argument",
            )

            captured: list[sqlite3.Connection] = []

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    cli_service,
                    "_CLI_SCRIPT_PATH",
                    install.entrypoint,
                ),
                mock.patch.object(
                    cli_service,
                    "resolve_project_state",
                    side_effect=_capture_resolutions(
                        real_resolve,
                        captured,
                    ),
                ),
                mock.patch.object(
                    cli_service,
                    "dispatch_stateful_command",
                    side_effect=RuntimeError("injected dispatch failure"),
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
                self.assertRaisesRegex(RuntimeError, "injected dispatch failure"),
            ):
                cli_service.main(
                    [
                        "task",
                        "list",
                        "--repo",
                        str(install.project_root),
                        "--json",
                    ],
                    _maintenance_enabled=False,
                )

            self.assertEqual(len(captured), 1)
            _assert_connection_closed(self, captured[0])

    def test_invalid_and_not_found_inputs_stay_in_json_envelopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            _setup(install)

            cases = (
                (
                    ("task", "list", "--limit", "0"),
                    "invalid_argument",
                ),
                (
                    ("task", "show", TASK_ID),
                    "not_found",
                ),
            )
            for args, expected_code in cases:
                with self.subTest(code=expected_code):
                    result = _run_cli(install, *args)
                    payload = _payload(result)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stderr, "")
                    self.assertNotIn("Traceback", result.stdout)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        expected_code,
                    )

    def test_uninitialized_state_never_echoes_unvalidated_task_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            for args in (
                ("task", "effort", RAW_TASK_MARKER),
                (
                    "task",
                    "complete",
                    RAW_TASK_MARKER,
                    "--check",
                ),
            ):
                with self.subTest(command=" ".join(args[:2])):
                    result = _run_cli(install, *args)
                    payload = _payload(result)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stderr, "")
                    self.assertNotIn(RAW_TASK_MARKER, result.stdout)
                    self.assertIsNone(payload["data"]["task_id"])
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "db_not_initialized",
                    )

    def test_retained_read_rejects_missing_required_schema_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            _setup(install)
            with closing(connect(install.db_path)) as connection:
                connection.execute("DROP TABLE task_checkpoints")
                connection.commit()

            result = _run_cli(install, "task", "list")
            payload = _payload(result)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            self.assertNotIn("Traceback", result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["errors"][0]["code"],
                "migration_required",
            )


if __name__ == "__main__":
    unittest.main()
