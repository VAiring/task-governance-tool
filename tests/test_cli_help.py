import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
try:
    from task_governance_tool.cli import build_parser
finally:
    sys.path.pop(0)

try:
    from m14_test_support import make_physical_install
except ModuleNotFoundError:
    from tests.m14_test_support import make_physical_install


M14_6_STAGE_LEAVES = {
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
}


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


class CliHelpTests(unittest.TestCase):
    def test_m14_6_staged_parser_has_only_its_twenty_leaves(self):
        self.assertEqual(parser_leaf_commands(build_parser()), M14_6_STAGE_LEAVES)

    def test_root_help_is_read_only_and_hides_removed_storage_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = sorted(path.relative_to(install.project_root) for path in install.project_root.rglob("*"))

            result = install.run("--help")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("setup", result.stdout)
            self.assertIn("doctor", result.stdout)
            self.assertIn("--repo", result.stdout)
            self.assertIn("--json", result.stdout)
            self.assertIn("--read-only", result.stdout)
            self.assertNotIn("--db", result.stdout)
            self.assertNotIn("{db,", result.stdout)
            self.assertNotIn("{self,", result.stdout)
            self.assertNotIn("web", result.stdout)
            after = sorted(path.relative_to(install.project_root) for path in install.project_root.rglob("*"))
            self.assertEqual(after, before)

    def test_setup_help_exposes_only_bounded_policy_and_common_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            result = install.run("setup", "--help")

            self.assertEqual(result.returncode, 0, result.stderr)
            for option in (
                "--backup-interval-minutes",
                "--backup-generations",
                "--confirm-relocation",
                "--repo",
                "--json",
                "--read-only",
            ):
                self.assertIn(option, result.stdout)
            normalized = " ".join(result.stdout.split())
            self.assertIn("first use --read-only to preview", normalized)
            self.assertIn("explicit current user approval", normalized)
            self.assertIn("exact token", normalized)
            for error_code in (
                "project_relocation_required",
                "relocation_token_invalid",
                "relocation_token_expired",
                "relocation_token_stale",
                "relocation_token_used",
                "relocation_not_required",
            ):
                self.assertIn(error_code, result.stdout)
            relocation_options = {
                token
                for token in result.stdout.split()
                if token.startswith("--") and "relocation" in token
            }
            self.assertEqual(relocation_options, {"--confirm-relocation"})
            for removed in ("--db", "--output", "--fix", "--disable", "--restore"):
                self.assertNotIn(removed, result.stdout)

    def test_doctor_help_has_no_mutating_or_path_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))

            result = install.run("doctor", "--help")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--repo", result.stdout)
            self.assertIn("--json", result.stdout)
            for removed in ("--db", "--fix", "--repair", "--output", "--backup"):
                self.assertNotIn(removed, result.stdout)

    def test_task_contract_compact_completion_and_checkpoint_help_remain_available(self):
        for command in (("task", "add"), ("task", "edit")):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                result = make_physical_install(Path(tmp)).run(*command, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                for option in (
                    "--contract-scope",
                    "--contract-acceptance",
                    "--contract-constraints",
                    "--contract-authority-ref",
                    "--contract-change-reason",
                ):
                    self.assertIn(option, result.stdout)
                self.assertNotIn("--db", result.stdout)

        for command in (("task", "current"), ("task", "next")):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                result = make_physical_install(Path(tmp)).run(*command, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--compact", result.stdout)
                self.assertNotIn("--db", result.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            complete = make_physical_install(Path(tmp)).run("task", "complete", "--help")
        self.assertEqual(complete.returncode, 0, complete.stderr)
        for option in (
            "--check",
            "--completion-evidence-kind",
            "--completion-revision",
            "--verification-complete",
            "--review-complete",
        ):
            self.assertIn(option, complete.stdout)
        self.assertNotIn("--status", complete.stdout)
        self.assertNotIn("--db", complete.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = make_physical_install(Path(tmp)).run(
                "task",
                "checkpoint",
                "--help",
            )
        self.assertEqual(checkpoint.returncode, 0, checkpoint.stderr)
        for option in (
            "task_id",
            "--summary",
            "--next-action",
            "--unresolved-risk",
            "--repo",
            "--json",
            "--read-only",
        ):
            self.assertIn(option, checkpoint.stdout)
        for unsupported in ("--compact", "--check", "--db"):
            self.assertNotIn(unsupported, checkpoint.stdout)

    def test_review_and_handoff_help_keep_only_implemented_stage_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            handoff = install.run("handoff", "--help")
            review = install.run("review", "--help")

            self.assertEqual(handoff.returncode, 0, handoff.stderr)
            for command in ("record", "list", "show", "withdraw"):
                self.assertIn(command, handoff.stdout)
            self.assertNotIn("sync", handoff.stdout)
            self.assertEqual(review.returncode, 0, review.stderr)
            for command in ("prepare", "target", "receipt", "finding"):
                self.assertIn(command, review.stdout)

            prepare = install.run("review", "prepare", "--help")
            self.assertEqual(prepare.returncode, 0, prepare.stderr)
            for supported in ("task_id", "--repo", "--json", "--read-only"):
                self.assertIn(supported, prepare.stdout)
            for unsupported in (
                "--focus",
                "--reviewer",
                "--import",
                "--output",
                "--file",
                "--db",
            ):
                self.assertNotIn(unsupported, prepare.stdout)

            receipt = install.run("review", "receipt", "add", "--help")
            self.assertEqual(receipt.returncode, 0, receipt.stderr)
            normalized_receipt = " ".join(receipt.stdout.split())
            for phrase in (
                "current-generation stored distinctness key",
                "distinct strings do not prove reviewer identity",
                "independence",
                "authenticated provenance",
            ):
                self.assertIn(phrase, normalized_receipt)

    def test_removed_groups_are_not_compatibility_help_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            for command in ("self", "db", "web"):
                with self.subTest(command=command):
                    result = install.run(command, "--help", "--json")
                    self.assertEqual(result.returncode, 2)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["command"], "parse")
                    self.assertEqual(
                        payload["errors"],
                        [{"code": "invalid_command", "message": "command is not available"}],
                    )
                    self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
