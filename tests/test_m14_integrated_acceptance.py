from __future__ import annotations

import argparse
import builtins
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from tests.m14_test_support import file_snapshot, make_physical_install


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import __version__  # noqa: E402
from task_governance_tool.cli import build_parser  # noqa: E402
from task_governance_tool.setup import run_setup  # noqa: E402
from task_governance_tool.storage import SCHEMA_VERSION  # noqa: E402
from task_governance_tool.viewer import SNAPSHOT_VERSION  # noqa: E402


ENVELOPE_KEYS = {
    "ok",
    "command",
    "project_id",
    "data",
    "warnings",
    "errors",
}
PUBLIC_LEAVES = {
    "setup",
    "doctor",
    "task add",
    "task list",
    "task next",
    "task current",
    "task effort",
    "task show",
    "task edit",
    "task complete",
    "task checkpoint",
    "handoff record",
    "handoff list",
    "handoff show",
    "handoff withdraw",
    "review prepare",
    "review target set",
    "review receipt add",
    "review finding add",
    "review finding resolve",
}
HELP_CHOICES = {
    (): ("setup", "doctor", "task", "handoff", "review"),
    ("task",): (
        "add",
        "list",
        "next",
        "current",
        "effort",
        "show",
        "checkpoint",
        "edit",
        "complete",
    ),
    ("handoff",): ("record", "list", "show", "withdraw"),
    ("review",): ("prepare", "target", "receipt", "finding"),
    ("review", "target"): ("set",),
    ("review", "receipt"): ("add",),
    ("review", "finding"): ("add", "resolve"),
}
REMOVED_OR_REPLACEMENT_ROOTS = (
    "self",
    "db",
    "web",
    "database",
    "viewer",
    "backup",
    "export",
    "repair",
    "restore",
    "maintenance",
    "admin",
)
FORBIDDEN_PUBLIC_PATH_KEYS = {
    "db_path",
    "database_path",
    "backup_path",
    "viewer_path",
    "state_path",
    "storage_path",
}
GUIDANCE_FILES = (
    ROOT / "README.md",
    ROOT / "docs" / "release-install.md",
    SKILL_ROOT / "SKILL.md",
    SKILL_ROOT / "references" / "cli_contracts.md",
    SKILL_ROOT / "references" / "reconciliation.md",
    SKILL_ROOT / "references" / "task_workflow.md",
)


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
    for action in subparsers:
        for name, child in action.choices.items():
            leaves.update(parser_leaf_commands(child, (*prefix, name)))
    return leaves


def help_choices(stdout: str) -> tuple[str, ...]:
    match = re.search(r"\{([^{}]+)\}", stdout)
    if match is None:
        raise AssertionError(f"help has no command choice list: {stdout!r}")
    return tuple(match.group(1).split(","))


def nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *(nested_keys(item) for item in value.values()),
        )
    if isinstance(value, list):
        return set().union(*(nested_keys(item) for item in value))
    return set()


def nested_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            string
            for item in value.values()
            for string in nested_strings(item)
        ]
    if isinstance(value, list):
        return [string for item in value for string in nested_strings(item)]
    return [value] if isinstance(value, str) else []


def python_taskgov_examples(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("python ") and "taskgov.py" in line
    ]


def routed_governance_graph(*, effort_advisory_enabled: bool) -> list[str]:
    graph = [
        "task.current",
        "task.next",
        "task.show",
        "task.edit",
    ]
    if effort_advisory_enabled:
        graph.append("task.effort")
    graph.extend(
        [
            "review.target.set",
            "review.prepare",
            "review.receipt.add",
            "review.receipt.add",
            "task.complete",
        ]
    )
    return graph


class M14IntegratedAcceptanceTests(unittest.TestCase):
    def assert_path_free_envelope(
        self,
        payload: dict[str, Any],
        *,
        project_root: Path,
    ) -> None:
        self.assertEqual(set(payload), ENVELOPE_KEYS)
        self.assertTrue(FORBIDDEN_PUBLIC_PATH_KEYS.isdisjoint(nested_keys(payload)))
        root_spellings = {
            str(project_root),
            project_root.as_posix(),
        }
        for value in nested_strings(payload):
            self.assertTrue(
                all(root not in value for root in root_spellings),
                f"public value exposed the project path: {value!r}",
            )

    def run_json(self, install, *args: str) -> tuple[dict[str, Any], Any]:
        result = install.run(*args, "--json")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assert_path_free_envelope(
            payload,
            project_root=install.project_root,
        )
        return payload, result

    def test_final_help_and_removed_surfaces_share_one_no_write_boundary(self):
        self.assertEqual(parser_leaf_commands(build_parser()), PUBLIC_LEAVES)
        self.assertEqual(len(PUBLIC_LEAVES), 20)

        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = file_snapshot(install.project_root)

            for command, expected in HELP_CHOICES.items():
                with self.subTest(help=command or ("root",)):
                    result = install.run(*command, "--help")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(help_choices(result.stdout), expected)
                    self.assertNotIn("--db", result.stdout)

            fixed_command_error = {
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
            }
            for command in REMOVED_OR_REPLACEMENT_ROOTS:
                with self.subTest(removed=command):
                    result = install.run(command, "ignored", "--json")
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(json.loads(result.stdout), fixed_command_error)

            rejected_value = "C:/private/DO-NOT-ECHO.sqlite"
            removed_option = install.run(
                "viewer",
                "--db",
                rejected_value,
                "--json",
            )
            self.assertEqual(removed_option.returncode, 2)
            self.assertEqual(removed_option.stderr, "")
            self.assertNotIn(rejected_value, removed_option.stdout)
            self.assertEqual(
                json.loads(removed_option.stdout),
                {
                    **fixed_command_error,
                    "errors": [
                        {
                            "code": "invalid_option",
                            "message": "option is not available",
                        }
                    ],
                },
            )

            fixed_argument_error = {
                **fixed_command_error,
                "errors": [
                    {
                        "code": "invalid_argument",
                        "message": "arguments are invalid",
                    }
                ],
            }
            for args in (("task", "bogus"), ("doctor", "--bogus")):
                with self.subTest(invalid_argument=args):
                    result = install.run(*args, "--json")
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(json.loads(result.stdout), fixed_argument_error)

            self.assertEqual(file_snapshot(install.project_root), before)
            self.assertFalse((install.skill_root / "state").exists())

    def test_active_publication_matches_runtime_schema_and_command_surface(self):
        manifest = json.loads(
            (SKILL_ROOT / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["package_version"], __version__)
        self.assertEqual(SCHEMA_VERSION, 14)
        self.assertEqual(SNAPSHOT_VERSION, 3)
        self.assertIn(
            "scripts/task_governance_tool/viewer_maintenance.py",
            manifest["core_files"],
        )
        self.assertIn(
            "scripts/task_governance_tool/artifact_lock.py",
            manifest["core_files"],
        )
        self.assertIn(
            "references/reconciliation.md",
            manifest["core_files"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            version = make_physical_install(Path(tmp)).run("--version")
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(version.stdout, f"taskgov {__version__}\n")

        examples: list[str] = []
        for path in GUIDANCE_FILES:
            text = path.read_text(encoding="utf-8")
            examples.extend(python_taskgov_examples(text))
        self.assertTrue(any(" setup" in f" {line} " for line in examples))
        self.assertTrue(any(" doctor" in f" {line} " for line in examples))
        for line in examples:
            suffix = line.split("taskgov.py", 1)[1]
            padded = f" {suffix} "
            self.assertNotIn(" self ", padded, line)
            self.assertNotIn(" db ", padded, line)
            self.assertNotIn(" web ", padded, line)
            self.assertNotIn("--db", suffix, line)

        for path in (
            ROOT / "README.md",
            ROOT / "docs" / "release-install.md",
            SKILL_ROOT / "references" / "cli_contracts.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn(__version__, text, path)
            self.assertRegex(text, r"(?i)schema(?: version)? v?14")
            self.assertRegex(text, r"(?i)(?:viewer )?snapshot v3")

        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("effort_advisory_enabled", skill)
        self.assertIn("task effort", skill)
        self.assertRegex(skill, r"(?i)\bnine\b")
        self.assertRegex(skill, r"(?i)\bten\b")

        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(metadata, r"(?i)\bexport\b")
        self.assertRegex(metadata, r"(?i)\btask\b")

        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("unittest discover -s tests", workflow)
        self.assertIn("doctor --repo . --read-only --json", workflow)
        self.assertIn(__version__, workflow)
        self.assertRegex(workflow, r"SCHEMA_VERSION[^\r\n]*14")
        self.assertNotIn("web export --help", workflow)

    def test_m16_setup_does_not_seed_tasks_or_adopt_project_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            instructions_path = install.project_root / "AGENTS.md"
            instructions_content = (
                "# Existing project instructions\n\n"
                "This file is a sentinel and must not be inspected or adopted "
                "by setup.\n"
            )
            instructions_path.write_text(instructions_content, encoding="utf-8")
            before = file_snapshot(install.project_root, exclude_state=True)

            instruction_key = os.path.normcase(
                os.path.abspath(os.fspath(instructions_path))
            )

            def reject_instruction_access(candidate):
                if isinstance(candidate, int):
                    return
                try:
                    candidate_key = os.path.normcase(
                        os.path.abspath(os.fspath(candidate))
                    )
                except TypeError:
                    return
                if candidate_key == instruction_key:
                    raise AssertionError(
                        "setup inspected the consuming project's AGENTS.md"
                    )

            original_open = builtins.open
            original_io_open = io.open
            original_stat = os.stat
            original_lstat = os.lstat

            def guarded_open(candidate, *args, **kwargs):
                reject_instruction_access(candidate)
                return original_open(candidate, *args, **kwargs)

            def guarded_io_open(candidate, *args, **kwargs):
                reject_instruction_access(candidate)
                return original_io_open(candidate, *args, **kwargs)

            def guarded_stat(candidate, *args, **kwargs):
                reject_instruction_access(candidate)
                return original_stat(candidate, *args, **kwargs)

            def guarded_lstat(candidate, *args, **kwargs):
                reject_instruction_access(candidate)
                return original_lstat(candidate, *args, **kwargs)

            with (
                mock.patch("builtins.open", guarded_open),
                mock.patch("io.open", guarded_io_open),
                mock.patch("os.stat", guarded_stat),
                mock.patch("os.lstat", guarded_lstat),
            ):
                setup_result = run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )
            self.assertTrue(setup_result.ok, setup_result)

            setup_payload, _ = self.run_json(install, "setup")
            self.assertEqual(
                file_snapshot(install.project_root, exclude_state=True),
                before,
            )
            self.assertEqual(
                instructions_path.read_text(encoding="utf-8"),
                instructions_content,
            )
            self.assertTrue(
                all(
                    not key.startswith("instruction")
                    and key not in {"bootstrap_task", "policy_task"}
                    for key in nested_keys(setup_payload)
                )
            )

            listed, _ = self.run_json(
                install,
                "task",
                "list",
                "--include-done",
            )
            self.assertEqual(listed["data"]["count"], 0)
            self.assertEqual(listed["data"]["tasks"], [])

    def test_all_review_target_kinds_work_through_the_installed_public_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp), git_managed=True)
            governed_file = install.project_root / "governed.txt"
            governed_file.write_text("integrated acceptance\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", ".gitignore", governed_file.name],
                cwd=install.project_root,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Taskgov Tests",
                    "-c",
                    "user.email=taskgov-tests@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "Create governed fixture",
                ],
                cwd=install.project_root,
                check=True,
            )
            canonical_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=install.project_root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            before_git = file_snapshot(
                install.project_root / ".git",
            )

            self.run_json(install, "setup")
            added, _ = self.run_json(
                install,
                "task",
                "add",
                "--title",
                "Review target acceptance",
            )
            task_id = added["data"]["task"]["task_id"]
            fingerprint = "sha256:" + "a" * 64
            target_cases = (
                ("git_snapshot", None),
                ("git_commit", "HEAD"),
                ("external_revision", "release-reviewed"),
                ("diff_fingerprint", fingerprint),
            )
            for generation, (kind, revision) in enumerate(target_cases, start=1):
                target_args = [
                    "review",
                    "target",
                    "set",
                    task_id,
                    "--kind",
                    kind,
                ]
                if revision is not None:
                    target_args.extend(["--revision", revision])
                target, _ = self.run_json(install, *target_args)
                self.assertEqual(
                    target["data"]["task"]["review_target_kind"],
                    kind,
                )
                self.assertEqual(
                    target["data"]["task"]["review_target_generation"],
                    generation,
                )
                if kind == "git_snapshot":
                    self.assertEqual(
                        target["data"]["task"]["review_target_base_revision"],
                        canonical_head,
                    )
                elif kind == "git_commit":
                    self.assertEqual(
                        target["data"]["task"]["review_target_value"],
                        canonical_head,
                    )
                packet, _ = self.run_json(
                    install,
                    "review",
                    "prepare",
                    task_id,
                    "--read-only",
                )
                self.assertEqual(packet["data"]["review_target"]["kind"], kind)

            self.assertEqual(
                file_snapshot(install.project_root / ".git"),
                before_git,
            )

    def test_setup_doctor_and_default_flow_are_integrated_and_target_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            unchanged_target = file_snapshot(
                install.project_root,
                exclude_state=True,
            )

            doctor_before, _ = self.run_json(
                install,
                "doctor",
                "--read-only",
            )
            self.assertEqual(
                doctor_before["data"]["components"]["project_state"]["code"],
                "setup_required",
            )
            self.assertFalse((install.skill_root / "state").exists())

            preview, _ = self.run_json(
                install,
                "setup",
                "--read-only",
            )
            self.assertEqual(preview["data"]["status"], "setup_preview")
            self.assertEqual(preview["data"]["completed_writes"], [])
            self.assertFalse((install.skill_root / "state").exists())

            setup, _ = self.run_json(install, "setup")
            self.assertEqual(setup["data"]["status"], "setup_complete")
            self.assertTrue(setup["data"]["maintenance_enabled"])
            self.assertEqual(setup["data"]["backup_interval_minutes"], 30)
            self.assertEqual(setup["data"]["backup_generations"], 3)

            state_before_doctor = file_snapshot(install.skill_root / "state")
            doctor_after, _ = self.run_json(
                install,
                "doctor",
                "--read-only",
            )
            self.assertEqual(
                doctor_after["data"]["components"]["project_state"]["code"],
                "ready",
            )
            self.assertEqual(
                doctor_after["data"]["suggested_action"],
                "continue",
            )
            self.assertEqual(
                file_snapshot(install.skill_root / "state"),
                state_before_doctor,
            )

            setup_replay, _ = self.run_json(install, "setup")
            self.assertEqual(setup_replay["data"]["status"], "already_setup")
            self.assertEqual(setup_replay["data"]["planned_writes"], [])
            self.assertEqual(setup_replay["data"]["completed_writes"], [])

            held, _ = self.run_json(
                install,
                "task",
                "add",
                "--title",
                "Held unrelated task",
                "--status",
                "blocked",
                "--blocked-reason",
                "Waiting on an unrelated external decision.",
            )
            added, _ = self.run_json(
                install,
                "task",
                "add",
                "--title",
                "Integrated acceptance task",
                "--review-tier",
                "2",
                "--verification",
                "Run the bounded integrated acceptance",
            )
            task_id = added["data"]["task"]["task_id"]

            graph_payloads: list[dict[str, Any]] = []
            current, _ = self.run_json(
                install,
                "task",
                "current",
                "--compact",
                "--read-only",
            )
            graph_payloads.append(current)
            self.assertEqual(
                [task["task_id"] for task in current["data"]["tasks"]],
                [held["data"]["task"]["task_id"]],
            )
            self.assertEqual(current["data"]["tasks"][0]["status"], "blocked")

            next_task, _ = self.run_json(
                install,
                "task",
                "next",
                "--compact",
                "--read-only",
            )
            graph_payloads.append(next_task)
            self.assertEqual(next_task["data"]["tasks"][0]["task_id"], task_id)

            shown, _ = self.run_json(
                install,
                "task",
                "show",
                task_id,
                "--read-only",
            )
            graph_payloads.append(shown)
            self.assertFalse(shown["data"]["effort_advisory_enabled"])

            started, _ = self.run_json(
                install,
                "task",
                "edit",
                task_id,
                "--status",
                "in_progress",
            )
            graph_payloads.append(started)

            checkpoint, _ = self.run_json(
                install,
                "task",
                "checkpoint",
                task_id,
                "--summary",
                "Public surface and setup are synchronized.",
                "--next-action",
                "Complete integrated review evidence.",
            )
            self.assertTrue(checkpoint["data"]["created"])

            fingerprint = "sha256:" + "a" * 64
            target, _ = self.run_json(
                install,
                "review",
                "target",
                "set",
                task_id,
                "--kind",
                "diff_fingerprint",
                "--revision",
                fingerprint,
            )
            graph_payloads.append(target)

            packet, _ = self.run_json(
                install,
                "review",
                "prepare",
                task_id,
                "--read-only",
            )
            graph_payloads.append(packet)
            self.assertEqual(
                packet["data"]["review_target"]["kind"],
                "diff_fingerprint",
            )
            self.assertFalse(packet["data"]["changed_paths_available"])

            for reviewer in ("integrated-review-a", "integrated-review-b"):
                receipt, _ = self.run_json(
                    install,
                    "review",
                    "receipt",
                    "add",
                    task_id,
                    "--reviewer",
                    reviewer,
                    "--kind",
                    "independent",
                    "--verdict",
                    "pass",
                    "--summary",
                    "Integrated acceptance passed.",
                )
                graph_payloads.append(receipt)

            completed, _ = self.run_json(
                install,
                "task",
                "complete",
                task_id,
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            graph_payloads.append(completed)
            self.assertEqual(completed["data"]["task"]["status"], "done")

            self.assertEqual(
                [payload["command"] for payload in graph_payloads],
                routed_governance_graph(effort_advisory_enabled=False),
            )
            self.assertEqual(len(graph_payloads), 9)
            enabled_graph = routed_governance_graph(
                effort_advisory_enabled=True,
            )
            self.assertEqual(len(enabled_graph), 10)
            self.assertEqual(
                [command for command in enabled_graph if command == "task.effort"],
                ["task.effort"],
            )
            self.assertNotIn("doctor", enabled_graph)
            self.assertNotIn("task.checkpoint", enabled_graph)

            self.assertEqual(
                file_snapshot(install.project_root, exclude_state=True),
                unchanged_target,
            )


if __name__ == "__main__":
    unittest.main()
