"""Execute the shipped caller examples; no new product orchestration layer."""

import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from tests.test_review_handoff_preparation import PreparationFixture
from tests.test_review_results import encode, receipt


WORKFLOW = Path(__file__).resolve().parents[1] / "task-governance-tool/references/task_workflow.md"


def example(heading, language):
    section = WORKFLOW.read_text(encoding="utf-8").split("### " + heading + "\n", 1)[1]
    return section.split("```" + language + "\n", 1)[1].split("```", 1)[0]


class GitWorkflowExampleTests(PreparationFixture):
    def setUp(self):
        super().setUp()
        self.shell = shutil.which("powershell" if os.name == "nt" else "sh")
        if self.shell is None:
            self.skipTest("native documented shell is unavailable")
        self.language = "powershell" if os.name == "nt" else "sh"
        self.git("config", "user.name", "Taskgov fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.file = self.root / "sample.txt"
        self.file.write_text("before\n", encoding="utf-8")
        self.git("add", "sample.txt", ".gitignore")
        self.git("commit", "--quiet", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.file.write_text("review candidate\n", encoding="utf-8")
        self.task_id = self.task()
        self.cli("task", "edit", self.task_id, "--status", "in_progress")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                              text=True, check=True)

    def command(self, heading, *, paths="sample.txt", task=None, directory="reviews/g1"):
        code = example(heading, self.language)
        code = code.replace("<intended-project-paths>", paths)
        code = code.replace("<task-id>", task or self.task_id)
        code = code.replace("<project-approved message>", "reviewed fixture")
        code = code.replace("reviews/g1", directory)
        if os.name == "nt":
            code = code.replace("python ", "& '" + sys.executable.replace("'", "''") + "' ")
        else:
            code = code.replace("python3 ", shlex.quote(sys.executable) + " ")
        return code

    def run_example(self, heading, **values):
        code = self.command(heading, **values)
        if os.name == "nt":
            encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
            args = [self.shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        else:
            args = [self.shell, "-c", code]
        return subprocess.run(args, cwd=self.root, capture_output=True, check=False)

    def target(self, **kwargs):
        result = self.run_example("Set The Review Target", **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        return json.loads(result.stdout)

    def reviews(self, target):
        context = target["handoff"]
        packet = json.loads((self.root / context["packet_path"]).read_bytes())
        result = packet["result_template"]
        paths = []
        for index, reviewer in enumerate(("fixture-a", "fixture-b")):
            result["receipts"] = [receipt(reviewer)]
            path = context["review_requests"][index]["result_path"]
            saved = self.invoke("save", "--repo", str(self.root), "--packet", context["packet_path"],
                                "--output", path, raw=encode(result))
            self.assertEqual(saved.returncode, 0, saved.stdout)
            paths.append(path)
        registered = self.invoke("submit", "--repo", str(self.root), "--packet", context["packet_path"],
                                 *paths)
        self.assertEqual(registered.returncode, 0, registered.stdout)

    def show(self):
        return self.cli("task", "show", self.task_id)

    def complete_tail(self, commit):
        return self.install.run("task", "complete", self.task_id, "--verification-complete",
            "--review-complete", "--completion-evidence-kind", "git_commit",
            "--completion-revision", commit, "--json")

    def test_success_and_lost_success_ack_preserve_one_generation_and_one_commit(self):
        target = self.target()
        self.assertEqual(target["source"]["review_target"]["generation"], 1)
        self.reviews(target)
        completed = self.run_example("Complete Work")
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertIn(commit.encode("ascii"), completed.stdout)
        self.assertEqual(self.git("rev-list", "--count", self.base + "..HEAD").stdout.strip(), "1")
        # Recovery after a discarded response reads state, not replaying either example.
        shown = self.show()
        self.assertEqual(shown["task"]["status"], "done")
        self.assertEqual(shown["task"]["review_target_generation"], 1)

    def test_git_stage_failure_stops_before_target(self):
        result = self.run_example("Set The Review Target", paths="missing-file")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.show()["task"]["review_target_generation"], 0)
        self.assertFalse((self.root / "reviews").exists())

    def test_target_cli_failure_is_not_hidden_by_shell(self):
        result = self.run_example("Set The Review Target", task="tg_task_0000000000000000")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.strip(), "sample.txt")
        self.assertEqual(self.show()["task"]["review_target_generation"], 0)

    def test_lost_target_response_recovers_from_public_state_without_reset(self):
        result = self.run_example("Set The Review Target")
        self.assertEqual(result.returncode, 0)
        # Treat the response body as lost; obtain the actual binding via public recovery.
        shown = self.cli("task", "show", self.task_id, "--audit")
        self.assertEqual(shown["task"]["review_target_generation"], 1)
        recovered = self.invoke("prepare", "--repo", str(self.root), "--directory", "reviews/recovery",
            "recover", self.task_id, "--expected-binding", shown["review_evidence"]["preparation_binding"])
        self.assertEqual(recovered.returncode, 0, recovered.stdout)
        self.assertEqual(self.show()["task"]["review_target_generation"], 1)

    def test_failed_git_commit_does_not_call_completion(self):
        # No staged changes: git commit fails before even an intentionally invalid Task ID is used.
        result = self.run_example("Complete Work", task="tg_task_0000000000000000")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b'"ok"', result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.base)
        self.assertEqual(self.show()["task"]["status"], "in_progress")

    def test_commit_success_and_completion_failure_recover_only_tail(self):
        target = self.target()
        result = self.run_example("Complete Work")  # No reviews yet: existing gate must fail.
        self.assertNotEqual(result.returncode, 0)
        commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(commit, self.base)
        self.assertIn(commit.encode("ascii"), result.stdout)
        self.assertEqual(self.show()["task"]["status"], "in_progress")
        self.reviews(target)
        recovered = self.complete_tail(commit)
        self.assertEqual(recovered.returncode, 0, recovered.stdout)
        self.assertEqual(self.git("rev-list", "--count", self.base + "..HEAD").stdout.strip(), "1")

    def test_changed_tree_still_fails_current_completion_binding(self):
        target = self.target()
        self.reviews(target)
        self.file.write_text("changed after review\n", encoding="utf-8")
        self.git("add", "sample.txt")
        result = self.run_example("Complete Work")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"review_target_mismatch", result.stdout)
        self.assertEqual(self.show()["task"]["status"], "in_progress")
