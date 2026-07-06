import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"


def run_taskgov(*args):
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class CliEnvelopeTests(unittest.TestCase):
    def test_success_result_json_object_contains_stable_keys(self):
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        try:
            from task_governance_tool.cli import success_result
        finally:
            sys.path.pop(0)

        payload = success_result("db.status", "ok", {"example": True}).to_json_object()
        self.assertEqual(
            sorted(payload.keys()),
            ["command", "data", "db_path", "errors", "ok", "project_id", "warnings"],
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "db.status")
        self.assertEqual(payload["data"], {"example": True})
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["warnings"], [])

    def test_validation_error_uses_json_envelope_when_requested(self):
        result = run_taskgov("--json", "--unknown-option")

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "parse")
        self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
        self.assertIn("--unknown-option", payload["errors"][0]["message"])

    def test_common_options_work_after_leaf_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            result = run_taskgov("db", "status", "--json", "--read-only", "--repo", ".", "--db", str(db))

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "db.status")
        self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")

    def test_common_options_work_before_command_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            result = run_taskgov("--json", "--read-only", "--db", str(db), "db", "status")

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "db.status")
        self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")

    def test_missing_db_subcommand_is_validation_error(self):
        result = run_taskgov("--json", "db")

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "parse")
        self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
        self.assertIn("db requires a subcommand", payload["errors"][0]["message"])

    def test_missing_task_subcommand_is_validation_error(self):
        result = run_taskgov("--json", "task")

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "parse")
        self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
        self.assertIn("task requires a subcommand", payload["errors"][0]["message"])

    def test_text_output_is_concise(self):
        result = run_taskgov("task", "next")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "task.next: handler not implemented yet")

    def test_not_implemented_json_command_uses_tool_error_exit(self):
        result = run_taskgov("--json", "task", "next")

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "task.next")
        self.assertEqual(payload["errors"][0]["code"], "internal_error")

    def test_read_only_reaches_command_context(self):
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        try:
            from task_governance_tool.cli import build_parser, make_context
        finally:
            sys.path.pop(0)

        parser = build_parser()
        args = parser.parse_args(["db", "init", "--read-only"])
        context = make_context(args)

        self.assertEqual(context.command, "db.init")
        self.assertTrue(context.read_only)


if __name__ == "__main__":
    unittest.main()
