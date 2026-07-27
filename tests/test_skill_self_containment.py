import os
import json
import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.cli import build_parser  # noqa: E402
try:  # noqa: E402
    from m14_test_support import canonical_test_path, make_physical_install
except ModuleNotFoundError:  # noqa: E402
    from tests.m14_test_support import (
        canonical_test_path,
        make_physical_install,
    )


def copy_skill_to(destination: Path, *, source: Path = SKILL_ROOT) -> Path:
    copied = destination / "task-governance-tool"
    shutil.copytree(
        source,
        copied,
        ignore=shutil.ignore_patterns(
            "state",
            "__pycache__",
            "*.pyc",
            ".pytest_cache",
            "*.sqlite",
            "*.sqlite3",
            "*.db",
            "*-wal",
            "*-shm",
            "*-journal",
            "task-viewer.html",
            "*.log",
            "*.tmp",
        ),
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


def parser_leaf_commands(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
) -> set[str]:
    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    if not subparsers:
        return {" ".join(prefix)}
    leaves: set[str] = set()
    for subparser_action in subparsers:
        for name, child in subparser_action.choices.items():
            leaves.update(parser_leaf_commands(child, (*prefix, name)))
    return leaves


class SkillSelfContainmentTests(unittest.TestCase):
    def test_core_task_and_review_guidance_is_synchronized(self):
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
        self.assertIn(
            "only command that initializes or migrates",
            " ".join(skill_md.lower().split()),
        )
        self.assertIn("snapshot v3", contracts.lower())
        self.assertIn("schema v13", release_note.lower())
        self.assertIn("0.8.0", release_note)
        self.assertIn("verification and review gates", skill_md.lower())
        self.assertIn("current governed task", openai_yaml.lower())

    def test_tg_m11_snapshot_reopen_and_release_metadata_are_synchronized(self):
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
        runtime_init = (
            SKILL_ROOT / "scripts" / "task_governance_tool" / "__init__.py"
        ).read_text(encoding="utf-8")
        storage = (
            SKILL_ROOT / "scripts" / "task_governance_tool" / "storage.py"
        ).read_text(encoding="utf-8")
        viewer = (
            SKILL_ROOT / "scripts" / "task_governance_tool" / "viewer.py"
        ).read_text(encoding="utf-8")
        forward_note = (
            ROOT
            / "docs"
            / "forward-tests"
            / "tg-m11-git-snapshot-completion.md"
        ).read_text(encoding="utf-8")

        for text in (skill_md, workflow, contracts, readme, release_note):
            self.assertIn("git_snapshot", text)
        for text in (skill_md, workflow, contracts, readme):
            self.assertIn("reopen", text.lower())
        for text in (workflow, contracts, readme):
            normalized = " ".join(text.lower().split())
            self.assertIn("--kind git_snapshot", text)
            self.assertIn("--reopen-reason", text)
            self.assertIn("unstaged", normalized)
            self.assertIn("untracked", normalized)
            self.assertTrue(
                "single-parent" in normalized
                or "exactly one parent" in normalized
            )
            self.assertIn("fresh", normalized)
        release_normalized = " ".join(release_note.lower().split())
        self.assertIn("--kind git_snapshot", release_note)
        self.assertIn("unstaged", release_normalized)
        self.assertIn("untracked", release_normalized)
        self.assertIn('__version__ = "0.8.0"', runtime_init)
        self.assertIn("SCHEMA_VERSION = 13", storage)
        self.assertIn("SNAPSHOT_VERSION = 3", viewer)
        self.assertIn("omits", release_note)
        self.assertIn("## Result\n\nPASS", forward_note)
        self.assertIn("review_target_mismatch", forward_note)
        self.assertIn("no extra LLM review", forward_note)

        version = subprocess.run(
            [sys.executable, "scripts/taskgov.py", "--version"],
            cwd=SKILL_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn("0.8.0", version.stdout)

    def test_tg_m12_local_handoff_guidance_and_isolated_flow_are_synchronized(self):
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
        forward_note = (
            ROOT / "docs" / "forward-tests" / "tg-m12-task-contract.md"
        ).read_text(encoding="utf-8")
        for text in (skill_md, workflow, contracts, readme, release_note):
            self.assertIn("handoff record", text)
        for text in (skill_md, workflow, contracts, readme):
            self.assertIn("pending_handoff", text)
        for text in (skill_md, workflow, contracts):
            self.assertIn("Task Contract", text)
            self.assertIn("at most one", text.lower())
            self.assertIn("rejected raw", text.lower())
        for text in (workflow, contracts):
            self.assertIn("handoff_not_persisted", text)
        self.assertIn("schema v13", release_note.lower())
        self.assertIn("0.8.0", release_note)
        for text in (workflow, contracts, readme, release_note):
            self.assertIn("Effort Advisory", text)
        self.assertIn("effort_advisory_enabled", skill_md)
        for text in (skill_md, workflow, contracts, readme, release_note):
            self.assertNotIn("taskgov.py self status", text)
        self.assertIn("task effort", skill_md)
        self.assertIn("task effort", workflow)
        self.assertIn("task effort", contracts)
        self.assertIn("Result: PASS", forward_note)
        self.assertIn("Additional Task Contract judgments: 0", forward_note)
        self.assertIn("Additional Task Contract user questions: 0", forward_note)

        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            def run(*args):
                return install.run(*args, "--json")

            initialized = run("setup")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertEqual(json.loads(initialized.stdout)["data"]["schema_to"], 13)
            added = run(
                "task",
                "add",
                "--title",
                "Isolated source task",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            recorded = run(
                "handoff",
                "record",
                task_id,
                "--summary",
                "Isolated discovery",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            record_payload = json.loads(recorded.stdout)
            handoff_id = record_payload["data"]["handoff"]["handoff_id"]
            self.assertTrue(record_payload["data"]["local_record"]["durable"])
            listed = run(
                "handoff",
                "list",
            )
            self.assertEqual(json.loads(listed.stdout)["data"]["total_matching"], 1)
            shown = run(
                "handoff",
                "show",
                handoff_id,
            )
            self.assertEqual(shown.returncode, 0, shown.stderr)
            withdrawn = run(
                "handoff",
                "withdraw",
                handoff_id,
                "--reason",
                "Explicit isolated test direction",
            )
            self.assertEqual(withdrawn.returncode, 0, withdrawn.stderr)
            self.assertEqual(
                json.loads(withdrawn.stdout)["data"]["handoff"]["state"],
                "handoff_withdrawn_by_user",
            )
            contract_added = run(
                "task",
                "add",
                "--title",
                "Isolated Contract task",
                "--contract-scope",
                "Isolated bounded scope",
                "--contract-acceptance",
                "Isolated acceptance passes",
            )
            self.assertEqual(contract_added.returncode, 0, contract_added.stderr)
            contract_payload = json.loads(contract_added.stdout)
            self.assertEqual(
                contract_payload["data"]["contract_write"],
                {"recorded": True, "revision": 1},
            )
            contract_shown = run(
                "task",
                "show",
                contract_payload["data"]["task"]["task_id"],
            )
            self.assertEqual(contract_shown.returncode, 0, contract_shown.stderr)
            self.assertEqual(
                json.loads(contract_shown.stdout)["data"]["contract"]["revision"],
                1,
            )

            help_result = install.run("handoff", "--help")
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertNotIn("sync", help_result.stdout)

    def test_tg_m9_paused_visibility_guidance_is_synchronized(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = (SKILL_ROOT / "references" / "task_workflow.md").read_text(
            encoding="utf-8"
        )
        contracts = (SKILL_ROOT / "references" / "cli_contracts.md").read_text(
            encoding="utf-8"
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for text in (skill_md, workflow, contracts):
            self.assertIn("task current", text)
            self.assertIn("--status paused", text)
            self.assertIn("paused_tasks_present", text)
            self.assertIn("bounded", text.lower())
        self.assertIn("task current", readme)
        self.assertIn("paused", readme.lower())
        self.assertIn("advisory", skill_md.lower())
        self.assertIn("advisory", workflow.lower())
        self.assertIn("advisory", contracts.lower())

    def test_skill_guidance_mentions_completion_commit_gate(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = (SKILL_ROOT / "references" / "task_workflow.md").read_text(encoding="utf-8")
        contracts = (SKILL_ROOT / "references" / "cli_contracts.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        forward_note = (ROOT / "docs" / "forward-tests" / "completion-commit-flow.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("deterministic review and evidence gates", skill_md)
        self.assertIn("task current", skill_md)
        self.assertIn("two distinct", skill_md)
        for text in (workflow, contracts):
            self.assertIn("--verification-complete", text)
            self.assertIn("--review-complete", text)
            self.assertIn("--completion-evidence-kind", text)
            self.assertIn("--completion-revision", text)
            self.assertIn("--commit-not-required", text)
        self.assertIn("--verification-complete", readme)
        self.assertIn("--review-complete", readme)
        self.assertIn("--completion-evidence-kind git_commit", readme)
        self.assertIn("--completion-revision", readme)
        for text in (skill_md, workflow, contracts, readme):
            normalized = " ".join(text.split())
            self.assertRegex(
                normalized,
                r"(?i)(?:does not|never)[^.]{0,80}(?:stage|create commits|commits)",
            )
        self.assertIn("create the completion commit through the", workflow)
        self.assertIn("--completion-evidence-kind external_revision", workflow)
        self.assertIn("--external-revision-approved", workflow)
        self.assertIn("done transition without commit evidence failed with `commit_required`", forward_note)
        self.assertIn("the synthetic target project path was not created", forward_note)

    def test_skill_guidance_exposes_only_bounded_automatic_viewer_maintenance(self):
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

        self.assertIn("canonical offline projection", skill_md)
        self.assertIn("bounded same-process", skill_md)
        self.assertIn("no LLM command choice or background", skill_md)
        for text in (skill_md, workflow, contracts, readme):
            self.assertNotIn("taskgov.py web export", text)
            self.assertNotIn("taskgov web export", text)
            self.assertNotIn("explicit `--output`", text)

        for text in (workflow, readme):
            lowered = text.lower()
            self.assertIn("same-process", lowered)
            self.assertTrue(
                "background" in lowered or "schedule" in lowered,
            )

        self.assertIn("bundled Viewer template", release_note)
        self.assertIn("Viewer snapshot v3", release_note)
        self.assertNotIn("explicit `--output`", release_note)
        self.assertNotIn("export", openai_yaml.lower())
        self.assertIn("## Final Result\n\nPASS", forward_note)
        self.assertIn("python scripts/taskgov.py web export --repo", forward_note)
        self.assertIn("created no artifacts", forward_note)

        short_line = next(
            line for line in openai_yaml.splitlines() if "short_description:" in line
        )
        short_description = short_line.split(":", 1)[1].strip().strip('"')
        self.assertGreaterEqual(len(short_description), 25)
        self.assertLessEqual(len(short_description), 64)

    def test_guidance_requires_physical_project_scoped_install(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = (SKILL_ROOT / "references" / "task_workflow.md").read_text(
            encoding="utf-8"
        )
        contracts = (SKILL_ROOT / "references" / "cli_contracts.md").read_text(
            encoding="utf-8"
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_note = (ROOT / "docs" / "release-install.md").read_text(encoding="utf-8")
        specification = (ROOT / "docs" / "specification.md").read_text(encoding="utf-8")

        for text in (skill_md, workflow, contracts, readme, release_note, specification):
            self.assertIn(".agents", text)
            self.assertIn("project-scoped", text)
            self.assertIn("physical", text.lower())

        active_guidance = (skill_md, workflow, contracts, readme, release_note)
        for text in active_guidance:
            lowered = text.lower()
            self.assertNotIn("codex_home", lowered)
            self.assertNotIn(".codex\\skills", lowered)
            self.assertNotIn(".codex/skills", lowered)
        for text in (skill_md, workflow, contracts):
            lowered = text.lower()
            self.assertIn("user-wide", lowered)
            self.assertIn("unsupported", lowered)
        for text in (readme, release_note):
            self.assertRegex(text.lower(), r"\b(?:only|no other)\b")

    def test_target_ignore_guidance_is_only_the_root_anchored_skill_state(self):
        expected = "/.agents/skills/task-governance-tool/state/"
        for relative in ("README.md", "docs/release-install.md"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                matching_blocks = [
                    block.strip()
                    for block in re.findall(r"```(?:text|gitignore)\n(.*?)```", text, re.DOTALL)
                    if expected in block.splitlines()
                ]
                self.assertEqual(matching_blocks, [expected])

        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(expected, gitignore)
        self.assertNotIn("*.sqlite", gitignore)
        self.assertNotIn("*.sqlite3", gitignore)
        self.assertNotIn("*.db", gitignore)

    def test_runtime_source_parses_with_python_3_12_grammar(self):
        runtime_files = sorted(
            (SKILL_ROOT / "scripts").rglob("*.py"),
            key=lambda path: path.as_posix(),
        )
        self.assertTrue(runtime_files)
        for path in runtime_files:
            with self.subTest(path=path.relative_to(SKILL_ROOT).as_posix()):
                ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                    feature_version=(3, 12),
                )

    def test_m14_2_target_root_invocation_uses_physical_install_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            missing = install.run("doctor", "--read-only", "--json")
            self.assertEqual(missing.returncode, 0, missing.stderr)
            self.assertEqual(
                json.loads(missing.stdout)["data"]["components"]["package"]["status"],
                "clean",
            )
            self.assertEqual(
                json.loads(missing.stdout)["data"]["components"]["project_state"]["code"],
                "setup_required",
            )
            self.assertFalse((install.skill_root / "state").exists())

            initialized = install.run("setup", "--json")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            payload = json.loads(initialized.stdout)
            self.assertNotIn("db_path", payload)
            self.assertTrue(install.db_path.is_file())
            self.assertTrue(
                canonical_test_path(install.db_path).is_relative_to(
                    canonical_test_path(install.skill_root / "state")
                )
            )
            self.assertEqual(payload["data"]["schema_to"], 13)

            from_skill_root = install.run(
                "doctor",
                "--repo",
                str(install.project_root),
                "--json",
                cwd=install.skill_root,
            )
            self.assertEqual(from_skill_root.returncode, 0, from_skill_root.stderr)
            from_skill_payload = json.loads(from_skill_root.stdout)
            self.assertEqual(from_skill_payload["project_id"], payload["project_id"])
            self.assertNotIn("db_path", from_skill_payload)
            self.assertEqual(
                from_skill_payload["data"]["components"]["project_state"]["code"],
                "ready",
            )

    def test_m14_7_parser_and_readme_publish_the_same_twenty_leaves(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        command_section = readme.split("## Public Commands", 1)[1].split(
            "## Privacy And Scope",
            1,
        )[0]
        documented = set(
            re.findall(
                r"^\d+\. `taskgov ([^`]+)`$",
                command_section,
                re.MULTILINE,
            )
        )

        self.assertEqual(
            parser_leaf_commands(build_parser()),
            {
                "setup",
                "doctor",
                "task add",
                "task list",
                "task next",
                "task current",
                "task effort",
                "task show",
                "task checkpoint",
                "task edit",
                "task complete",
                "handoff record",
                "handoff list",
                "handoff show",
                "handoff withdraw",
                "review prepare",
                "review target set",
                "review receipt add",
                "review finding add",
                "review finding resolve",
            },
        )
        self.assertEqual(documented, parser_leaf_commands(build_parser()))
        self.assertEqual(len(documented), 20)

    def test_m14_spec_routing_contract_has_fixed_nine_or_ten_calls(self):
        specification = (ROOT / "docs" / "specification.md").read_text(
            encoding="utf-8"
        )
        graph_start = "The deterministic Skill call graph is:"
        graph_end = "### Doctor Contract"
        self.assertIn(graph_start, specification)
        self.assertIn(graph_end, specification)
        graph = specification.split(graph_start, 1)[1].split(graph_end, 1)[0]
        route_counts = (
            ("one compact `task current` call", 1, 1),
            ("one compact `task next` call", 1, 1),
            ("one `task show` call", 1, 1),
            ("one task edit", 1, 1),
            ("one existing\n  `task effort` observation", 0, 1),
            ("one review target set call", 1, 1),
            ("one `review prepare` call", 1, 1),
            ("one receipt write per actual receipt", 2, 2),
            ("one thin complete call", 1, 1),
        )

        positions = []
        for phrase, _, _ in route_counts:
            self.assertEqual(graph.count(phrase), 1)
            positions.append(graph.index(phrase))
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(sum(item[1] for item in route_counts), 9)
        self.assertEqual(sum(item[2] for item in route_counts), 10)
        self.assertIn(
            "at most nine governance\nsubprocess calls",
            graph,
        )
        self.assertIn(
            "profile-enabled path has at most ten",
            graph,
        )
        self.assertIn(
            "instead of separate task, Contract, target, and Git",
            graph,
        )
        for excluded in (
            "`task complete --check`",
            "`doctor`",
            "`task checkpoint`",
        ):
            self.assertIn(excluded, graph)
        self.assertIn("are absent from the\ndefault success path", graph)

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
            self.assertIn("Local project task-state helper for Codex.", result.stdout)
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
            for name in (
                "scratch.sqlite",
                "scratch.sqlite3",
                "scratch.db",
                "scratch.sqlite-wal",
                "scratch.db-journal",
                "task-viewer.html",
                "scratch.log",
                "scratch.tmp",
            ):
                (source / name).write_text("generated", encoding="utf-8")
            copied = copy_skill_to(workspace / "release", source=source)

            self.assertFalse((copied / "state").exists())
            for name in (
                "scratch.sqlite",
                "scratch.sqlite3",
                "scratch.db",
                "scratch.sqlite-wal",
                "scratch.db-journal",
                "task-viewer.html",
                "scratch.log",
                "scratch.tmp",
            ):
                self.assertFalse((copied / name).exists())
            self.assertTrue((copied / "assets" / "task-viewer.template.html").is_file())
            self.assertTrue(
                (copied / "scripts" / "task_governance_tool" / "viewer.py").is_file()
            )
            self.assertTrue(
                (
                    copied
                    / "scripts"
                    / "task_governance_tool"
                    / "git_snapshot.py"
                ).is_file()
            )
            self.assertTrue(
                (
                    copied
                    / "scripts"
                    / "task_governance_tool"
                    / "self_status.py"
                ).is_file()
            )
            self.assertTrue((copied / "release-manifest.json").is_file())
            self.assertTrue(
                (
                    copied
                    / "scripts"
                    / "task_governance_tool"
                    / "handoffs.py"
                ).is_file()
            )
            self.assertTrue(
                (
                    copied
                    / "scripts"
                    / "task_governance_tool"
                    / "contracts.py"
                ).is_file()
            )
            self.assertTrue(
                (
                    copied
                    / "scripts"
                    / "task_governance_tool"
                    / "effort.py"
                ).is_file()
            )

    def test_ci_requires_viewer_runtime_and_rejects_generated_viewer(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("$skillRoot/assets/task-viewer.template.html", workflow)
        self.assertIn("$skillRoot/scripts/task_governance_tool/viewer.py", workflow)
        self.assertIn("$skillRoot/scripts/task_governance_tool/git_snapshot.py", workflow)
        self.assertIn("$skillRoot/scripts/task_governance_tool/handoffs.py", workflow)
        self.assertIn("$skillRoot/scripts/task_governance_tool/contracts.py", workflow)
        self.assertIn("$skillRoot/scripts/task_governance_tool/effort.py", workflow)
        self.assertIn("$skillRoot/scripts/task_governance_tool/self_status.py", workflow)
        self.assertIn("$skillRoot/release-manifest.json", workflow)
        self.assertIn("$doctorJson | ConvertFrom-Json", workflow)
        self.assertIn("$doctor.data.components.package.status -ne 'clean'", workflow)
        self.assertIn("@('self', 'status')", workflow)
        self.assertIn("invalid_command", workflow)
        self.assertIn("SCHEMA_VERSION", workflow)
        self.assertIn("0\\.8\\.0", workflow)
        self.assertIn("task-viewer\\.html$", workflow)
        self.assertIn('Windows skill checks (Python ${{ matrix.python-version }})', workflow)
        matrix_block = workflow.split("matrix:", 1)[1].split("\n\n    steps:", 1)[0]
        self.assertEqual(
            set(re.findall(r'- "([0-9]+\.[0-9]+)"', matrix_block)),
            {"3.12", "3.14"},
        )
        self.assertIn("runs-on: windows-latest", workflow)
        self.assertNotIn("ubuntu-", workflow)
        self.assertNotIn("macos-", workflow)

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
        self.assertIn(
            "task-governance-tool/scripts/task_governance_tool/git_snapshot.py",
            tracked,
        )
        self.assertIn(
            "task-governance-tool/scripts/task_governance_tool/handoffs.py",
            tracked,
        )
        self.assertIn(
            "task-governance-tool/scripts/task_governance_tool/contracts.py",
            tracked,
        )
        self.assertIn(
            "task-governance-tool/scripts/task_governance_tool/effort.py",
            tracked,
        )
        self.assertIn(
            "task-governance-tool/scripts/task_governance_tool/self_status.py",
            tracked,
        )
        self.assertIn("task-governance-tool/release-manifest.json", tracked)
        self.assertFalse(any(path.startswith("task-governance-tool/state/") for path in tracked))
        self.assertFalse(any(path.endswith("/task-viewer.html") for path in tracked))

    def test_git_ignores_only_project_state_not_repository_database_globs(self):
        ignored_paths = [
            "references/copied-reference.md",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite-wal",
            "task-governance-tool/state/projects/example-123456789abc/taskgov.sqlite-shm",
            "task-governance-tool/state/projects/example-123456789abc/viewer/task-viewer.html",
            ".agents/skills/task-governance-tool/state/.keep",
            ".agents/skills/task-governance-tool/state/projects/example/viewer/task-viewer.html",
        ]
        visible_database_paths = [
            ".agents/skills/other-skill/state/taskgov.sqlite",
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
        for path in visible_database_paths:
            with self.subTest(path=path):
                self.assertFalse(git_is_ignored(path))

    def test_repo_scoped_skill_folder_is_not_wholesale_ignored(self):
        self.assertFalse(git_is_ignored(".agents/skills/task-governance-tool/SKILL.md"))


if __name__ == "__main__":
    unittest.main()
