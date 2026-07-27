import subprocess
import sys
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPT = SKILL_ROOT / "scripts" / "taskgov.py"
STATE_DIR = SKILL_ROOT / "state"


def state_snapshot():
    if not STATE_DIR.exists():
        return []
    entries = ["."]
    entries.extend(sorted(str(path.relative_to(STATE_DIR)) for path in STATE_DIR.rglob("*")))
    return entries


class CliHelpTests(unittest.TestCase):
    def test_help_runs_from_skill_folder(self):
        before_state = state_snapshot()

        result = subprocess.run(
            [sys.executable, "scripts/taskgov.py", "--help"],
            cwd=SKILL_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("taskgov", result.stdout)
        self.assertIn("--repo", result.stdout)
        self.assertIn("--db", result.stdout)
        self.assertIn("--json", result.stdout)
        self.assertIn("--read-only", result.stdout)
        self.assertEqual(state_snapshot(), before_state)

    def test_runtime_package_imports_from_scripts_path(self):
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        try:
            from task_governance_tool.cli import build_parser
        finally:
            sys.path.pop(0)

        parser = build_parser()
        self.assertEqual(parser.prog, "taskgov")

    def test_task_contract_options_are_available_on_add_and_edit(self):
        expected_options = (
            "--contract-scope",
            "--contract-acceptance",
            "--contract-constraints",
            "--contract-authority-ref",
            "--contract-change-reason",
        )
        for command in (("task", "add"), ("task", "edit")):
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, "scripts/taskgov.py", *command, "--help"],
                    cwd=SKILL_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                for option in expected_options:
                    self.assertIn(option, result.stdout)

    def test_compact_and_thin_completion_help_are_available(self):
        for command in (("task", "current"), ("task", "next")):
            with self.subTest(command=command):
                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/taskgov.py",
                        *command,
                        "--help",
                    ],
                    cwd=SKILL_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--compact", result.stdout)

        complete = subprocess.run(
            [
                sys.executable,
                "scripts/taskgov.py",
                "task",
                "complete",
                "--help",
            ],
            cwd=SKILL_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(complete.returncode, 0, complete.stderr)
        for option in (
            "--check",
            "--completion-evidence-kind",
            "--completion-revision",
            "--completion-evidence-reason",
            "--external-revision-approved",
            "--commit-not-required",
            "--verification-complete",
            "--review-complete",
            "--repo",
            "--json",
            "--read-only",
        ):
            self.assertIn(option, complete.stdout)
        self.assertNotIn("--status", complete.stdout)
        self.assertNotIn("--title", complete.stdout)
        self.assertNotIn("--contract-scope", complete.stdout)

    def test_structured_review_command_help_is_available(self):
        commands = (
            ("review", "target", "set", "--help"),
            ("review", "receipt", "add", "--help"),
            ("review", "finding", "add", "--help"),
            ("review", "finding", "resolve", "--help"),
        )
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, "scripts/taskgov.py", *command],
                    cwd=SKILL_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--repo", result.stdout)
                self.assertIn("--read-only", result.stdout)

    def test_local_handoff_help_exposes_only_implemented_commands(self):
        group = subprocess.run(
            [sys.executable, "scripts/taskgov.py", "handoff", "--help"],
            cwd=SKILL_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(group.returncode, 0, group.stderr)
        for command in ("record", "list", "show", "withdraw"):
            self.assertIn(command, group.stdout)
        self.assertNotIn("sync", group.stdout)

        for command in ("record", "list", "show", "withdraw"):
            with self.subTest(command=command):
                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/taskgov.py",
                        "handoff",
                        command,
                        "--help",
                    ],
                    cwd=SKILL_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--repo", result.stdout)
                self.assertIn("--read-only", result.stdout)

        bare = subprocess.run(
            [sys.executable, "scripts/taskgov.py", "--json", "handoff"],
            cwd=SKILL_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(bare.returncode, 1)
        payload = json.loads(bare.stdout)
        self.assertEqual(payload["command"], "parse")
        self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
        self.assertIn("record, list, show, or withdraw", payload["errors"][0]["message"])

    def test_self_status_help_and_bare_group_contract(self):
        help_result = subprocess.run(
            [sys.executable, "scripts/taskgov.py", "self", "status", "--help"],
            cwd=SKILL_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--repo", help_result.stdout)
        self.assertIn("--db", help_result.stdout)
        self.assertIn("--json", help_result.stdout)
        self.assertIn("--read-only", help_result.stdout)

        bare = subprocess.run(
            [sys.executable, "scripts/taskgov.py", "--json", "self"],
            cwd=SKILL_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(bare.returncode, 1)
        payload = json.loads(bare.stdout)
        self.assertEqual(payload["command"], "parse")
        self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
        self.assertIn("self requires a subcommand: status", payload["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
