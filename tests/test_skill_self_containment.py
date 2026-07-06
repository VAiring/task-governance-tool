import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"


def copy_skill_to(destination: Path, *, source: Path = SKILL_ROOT) -> Path:
    copied = destination / "task-governance-tool"
    shutil.copytree(
        source,
        copied,
        ignore=shutil.ignore_patterns("state", "__pycache__", "*.pyc", ".pytest_cache"),
    )
    return copied


def git_check_ignore(path: str) -> str:
    result = subprocess.run(
        ["git", "check-ignore", "-v", path],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or f"{path} was not ignored")
    if not result.stdout.startswith(".gitignore:"):
        raise AssertionError(f"{path} was ignored outside the repository .gitignore: {result.stdout}")
    return result.stdout


class SkillSelfContainmentTests(unittest.TestCase):
    def test_copied_skill_folder_help_runs_without_repo_python_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = copy_skill_to(Path(tmp))
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)

            result = subprocess.run(
                [sys.executable, "-I", "-S", "scripts/taskgov.py", "--help"],
                cwd=copied,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Local SQLite-backed task state helper for Codex.", result.stdout)
            self.assertIn("--repo", result.stdout)
            self.assertIn("--read-only", result.stdout)
            self.assertFalse((copied / "state").exists())

    def test_static_skill_copy_excludes_generated_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = copy_skill_to(workspace / "source")
            generated = source / "state" / "projects" / "example-123456789abc"
            generated.mkdir(parents=True, exist_ok=True)
            generated_db = generated / "taskgov.sqlite"
            generated_db.write_text("", encoding="utf-8")
            copied = copy_skill_to(workspace / "release", source=source)

            self.assertFalse((copied / "state").exists())

    def test_git_ignores_generated_state_and_sqlite_artifacts(self):
        ignored_paths = [
            "references/copied-reference.md",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite-wal",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite-shm",
            "scratch.sqlite",
            "scratch.sqlite3",
            "scratch.sqlite-wal",
            "scratch.sqlite-shm",
            "scratch.sqlite-journal",
            "scratch.sqlite3-wal",
            "scratch.sqlite3-shm",
            "scratch.sqlite3-journal",
            "scratch.db",
            "scratch.db-wal",
            "scratch.db-shm",
            "scratch.db-journal",
        ]

        for path in ignored_paths:
            with self.subTest(path=path):
                self.assertIn(path, git_check_ignore(path))


if __name__ == "__main__":
    unittest.main()
