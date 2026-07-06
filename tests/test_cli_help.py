import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
