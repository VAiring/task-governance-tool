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


def git_is_ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


class SkillSelfContainmentTests(unittest.TestCase):
    def test_tg_m8_guidance_and_metadata_are_synchronized(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = (SKILL_ROOT / "references" / "task_workflow.md").read_text(
            encoding="utf-8"
        )
        contracts = (SKILL_ROOT / "references" / "cli_contracts.md").read_text(
            encoding="utf-8"
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_note = (ROOT / "docs" / "release-install.md").read_text(
            encoding="utf-8"
        )
        openai_yaml = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )

        for text in (skill_md, workflow, contracts, readme):
            self.assertIn("task current", text)
            self.assertIn("Tier 2", text)
        for text in (workflow, contracts, readme):
            self.assertIn("review target set", text)
            self.assertIn("review receipt add", text)
        self.assertIn("review target", skill_md)
        self.assertIn("task add --status done", skill_md)
        self.assertIn("Only `db init`", skill_md)
        self.assertIn("snapshot_version\": 3", contracts)
        self.assertIn("schema v5", release_note)
        self.assertIn("verification receipts", skill_md.lower())
        self.assertIn("current work", openai_yaml.lower())

    def test_skill_guidance_mentions_completion_commit_gate(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = (SKILL_ROOT / "references" / "task_workflow.md").read_text(encoding="utf-8")
        contracts = (SKILL_ROOT / "references" / "cli_contracts.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        forward_note = (ROOT / "docs" / "forward-tests" / "completion-commit-flow.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("completing tasks with structured review and completion evidence", skill_md)
        self.assertIn("task current", skill_md)
        self.assertIn("two distinct", skill_md)
        for text in (workflow, contracts, readme):
            self.assertIn("--verification-complete", text)
            self.assertIn("--review-complete", text)
            self.assertIn("--completion-commit-hash", text)
            self.assertIn("--commit-not-required", text)
            self.assertIn("does not create commits", " ".join(text.split()))
        self.assertIn("first create the project commit through the", workflow)
        self.assertIn("--completion-evidence-kind external_revision", workflow)
        self.assertIn("--external-revision-approved", workflow)
        self.assertIn("git show --name-only <completion_commit_hash>", workflow)
        self.assertIn("done transition without commit evidence failed with `commit_required`", forward_note)
        self.assertIn("the synthetic target project path was not created", forward_note)

    def test_skill_guidance_exposes_static_viewer_with_explicit_write_gate(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = (SKILL_ROOT / "references" / "task_workflow.md").read_text(
            encoding="utf-8"
        )
        contracts = (SKILL_ROOT / "references" / "cli_contracts.md").read_text(
            encoding="utf-8"
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_note = (ROOT / "docs" / "release-install.md").read_text(encoding="utf-8")
        openai_yaml = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        forward_note = (ROOT / "docs" / "forward-tests" / "static-task-viewer.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("creating or regenerating a user-requested offline static Task Viewer", skill_md)
        self.assertIn("python scripts/taskgov.py web export --repo <target-project>", skill_md)
        self.assertIn("no `viewer` command group", skill_md)
        self.assertIn("`--project-root` option", skill_md)
        for text in (skill_md, workflow, contracts, readme):
            self.assertIn("web export", text)
            self.assertIn("--read-only", text)
            self.assertIn("explicit", text.lower())
            self.assertIn("snapshot", text.lower())

        for text in (skill_md, workflow, readme):
            lowered = text.lower()
            self.assertTrue(
                "no server" in lowered
                or "start a server" in lowered
                or "without a server" in lowered
            )
            self.assertIn("browser", lowered)

        self.assertIn("task-viewer.template.html", release_note)
        self.assertIn("generated `task-viewer.html`", release_note)
        self.assertIn("Snapshot v3", release_note)
        self.assertIn("explicit `--output`", release_note)
        self.assertIn("offline task viewer", openai_yaml.lower())
        self.assertIn("## Final Result\n\nPASS", forward_note)
        self.assertIn("python scripts/taskgov.py web export --repo", forward_note)
        self.assertIn("created no artifacts", forward_note)

        short_line = next(
            line for line in openai_yaml.splitlines() if "short_description:" in line
        )
        short_description = short_line.split(":", 1)[1].strip().strip('"')
        self.assertGreaterEqual(len(short_description), 25)
        self.assertLessEqual(len(short_description), 64)

    def test_guidance_prefers_project_scoped_install(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_note = (ROOT / "docs" / "release-install.md").read_text(encoding="utf-8")
        specification = (ROOT / "docs" / "specification.md").read_text(encoding="utf-8")

        for text in (skill_md, readme, release_note, specification):
            self.assertIn(".agents", text)
            self.assertIn("project-scoped", text)

        self.assertIn("not recommended", release_note)
        self.assertIn("not recommended", readme)
        self.assertIn("user-wide", skill_md)

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
            generated_viewer = generated / "viewer" / "task-viewer.html"
            generated_viewer.parent.mkdir()
            generated_viewer.write_text("generated", encoding="utf-8")
            copied = copy_skill_to(workspace / "release", source=source)

            self.assertFalse((copied / "state").exists())
            self.assertTrue((copied / "assets" / "task-viewer.template.html").is_file())
            self.assertTrue(
                (copied / "scripts" / "task_governance_tool" / "viewer.py").is_file()
            )

    def test_ci_requires_viewer_runtime_and_rejects_generated_viewer(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("$skillRoot/assets/task-viewer.template.html", workflow)
        self.assertIn("$skillRoot/scripts/task_governance_tool/viewer.py", workflow)
        self.assertIn("task-viewer\\.html$", workflow)

    def test_tracked_skill_package_contains_runtime_but_no_generated_state(self):
        result = subprocess.run(
            ["git", "ls-files", "task-governance-tool"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        tracked = set(result.stdout.splitlines())
        self.assertIn("task-governance-tool/assets/task-viewer.template.html", tracked)
        self.assertIn(
            "task-governance-tool/scripts/task_governance_tool/viewer.py",
            tracked,
        )
        self.assertFalse(any(path.startswith("task-governance-tool/state/") for path in tracked))
        self.assertFalse(any(path.endswith("/task-viewer.html") for path in tracked))

    def test_git_ignores_generated_state_and_sqlite_artifacts(self):
        ignored_paths = [
            "references/copied-reference.md",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite-wal",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite-shm",
            "task-governance-tool/state/projects/example-123456789abc/viewer/task-viewer.html",
            ".agents/skills/task-governance-tool/state/.keep",
            ".agents/skills/task-governance-tool/state/projects/example/viewer/task-viewer.html",
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

    def test_repo_scoped_skill_folder_is_not_wholesale_ignored(self):
        self.assertFalse(git_is_ignored(".agents/skills/task-governance-tool/SKILL.md"))


if __name__ == "__main__":
    unittest.main()
