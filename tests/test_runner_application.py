"""Real public manual/Runner equivalence for the documented six-test adapter.

All Setup, Plan and business writes use public CLI in owned temporary installs.
The fail-closed cases alter only those test-owned Plan files.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.m14_test_support import make_physical_install, repository_git_environment
from tests.evidence_reader_oracle import read_evidence_index, validate_evidence_source
from tests.test_review_workspace import COUNTER, SIX_TESTS


class RunnerApplicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name).resolve())
        self.repo = self.install.project_root
        self.plan = self.install.skill_root / "config/verification-runner.json"
        # Execute the shipped examples themselves so guide drift is observable.
        guide = (self.install.skill_root / "references/runner_application.md").read_text(encoding="utf-8")
        entries = re.findall(r"^```python\n(.*?)^```", guide, re.MULTILINE | re.DOTALL)
        drafts = re.findall(r"^```json\n(.*?)^```", guide, re.MULTILINE | re.DOTALL)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(drafts), 1)
        self.draft = json.loads(drafts[0])
        self.git("init", "--quiet")
        self.git("config", "user.name", "TaskGov Fixture")
        self.git("config", "user.email", "taskgov@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(self.repo / ".git/disabled-hooks"))
        (self.repo / ".gitignore").write_text(
            "/.taskgov/\n/.agents/\n/__pycache__/\n", encoding="utf-8")
        for path, content in (("counter.py", COUNTER), ("test_counter.py", SIX_TESTS),
                              ("verify.py", entries[0])):
            (self.repo / path).write_text(content, encoding="utf-8")
        self.commit()
        self.cli("setup")
        self.task = self.cli("task", "add", "--title", "Runner application fixture",
            "--status", "in_progress", "--review-tier", "1",
            "--verification", "Six counter tests via unittest discovery",
            "--contract-scope", "Only the fixed six counter tests",
            "--contract-acceptance", "All six checks pass with complete coverage")["task"]["task_id"]

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.repo,
            env=repository_git_environment(), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def commit(self):
        self.git("-c", "core.autocrlf=false", "add", "--", ".gitignore", "counter.py",
                 "test_counter.py", "verify.py")
        self.git("commit", "--quiet", "-m", "Fixed application fixture")
        self.revision = self.git("rev-parse", "HEAD").decode().strip()

    def cli(self, *args, draft=None, error=None):
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(self.install.entrypoint), *args, "--json"],
            cwd=self.repo, env=repository_git_environment(),
            input=None if draft is None else json.dumps(draft).encode(),
            capture_output=True, timeout=120)
        body = json.loads(result.stdout)
        self.assertEqual(result.returncode == 0, error is None, body)
        self.assertEqual(body["ok"], error is None, body)
        if error:
            self.assertEqual(body["errors"][0]["code"], error, body)
        return body["data"]

    def target(self):
        return self.cli("review", "target", "set", self.task, "--kind", "git_commit",
                        "--revision", self.revision)

    def execute(self, *args):
        start = time.monotonic()
        result = subprocess.run([sys.executable, "-B", *args], cwd=self.repo,
            env=repository_git_environment(), capture_output=True, timeout=30)
        return result, int((time.monotonic() - start) * 1000)

    def assert_six_equivalent(self, *, success):
        module, duration = self.execute("-m", "unittest", "-v")
        script, _ = self.execute("verify.py", "-v")
        identities = []
        for result in (module, script):
            self.assertEqual(result.returncode == 0, success, result.stderr)
            self.assertIn(b"Ran 6 tests", result.stderr)
            identities.append(re.findall(rb"^(test_\w+ \([^\r\n]+\)) \.\.\.",
                                         result.stderr, re.MULTILINE))
        self.assertEqual(len(identities[0]), 6)
        self.assertEqual(identities[0], identities[1])
        return duration

    def receipt(self, generation, duration, result="pass", error=None):
        return self.cli("verification", "receipt", "add", self.task,
            "--result", result, "--duration-ms", str(duration), "--scope-coverage", "full",
            "--expected-target-generation", str(generation), error=error)

    def publish(self):
        result = self.cli("task", "edit", self.task, "--runner-plan-action", "replace", draft=self.draft)
        self.assertEqual(result["runner_plan_update"], {"action": "replace", "status": "updated"})
        self.assertEqual(result["changed_fields"], [])

    def test_manual_and_runner_share_fixed_six_checks_and_bound_evidence(self):
        manual = self.target()
        self.assertEqual(manual["verification_route"], "receipt_required")
        generation = manual["task"]["review_target_generation"]
        # The manual worktree is byte-identical to this target; no other tests exist.
        self.assertEqual(self.git("diff", "HEAD", "--"), b"")
        duration = self.assert_six_equivalent(success=True)
        recorded = self.receipt(generation, duration)
        manual_packet = recorded["review_preparation"]["packet"]
        self.assertEqual(manual_packet["review_target"]["generation"], generation)
        manual_gate = self.cli("task", "show", self.task)["verification_evidence"]
        self.assertTrue(manual_gate["gate"]["satisfied"])
        self.assertEqual(manual_gate["current_receipt"]["scope_coverage"], "full")

        self.publish()
        self.assertEqual(json.loads(self.plan.read_text())["entries"][0]["coverage"], "full")
        # Ambient changes would fail if copied instead of exact Git material.
        (self.repo / "counter.py").write_text("raise RuntimeError('ambient')\n", encoding="utf-8")
        (self.repo / "test_untracked.py").write_text("raise RuntimeError('untracked')\n", encoding="utf-8")
        routed = self.target()
        self.assertEqual(routed["verification_route"], "runner_pass", routed)
        packet = routed["review_preparation"]["packet"]
        self.assertEqual(packet["review_target"]["generation"], generation + 1)
        for key in ("kind", "value", "base_revision"):
            self.assertEqual(packet["review_target"][key], manual_packet["review_target"][key])
        self.assertEqual(packet["contract"], manual_packet["contract"])
        evidence = self.cli("task", "show", self.task)["verification_evidence"]
        self.assertTrue(evidence["gate"]["satisfied"])
        self.assertIsNone(evidence["current_receipt"])
        self.assertEqual(evidence["counts"]["receipts_exact_current"], 0)
        self.cli("review", "receipt", "add", self.task, "--reviewer", "test-only-human",
            "--kind", "independent", "--verdict", "pass", "--summary", "Test fixture only",
            "--reviewer-class", "human", "--model-state", "not_applicable",
            "--skill-state", "not_applicable", "--context-relation", "external_context")
        completed = self.cli("task", "complete", self.task, "--verification-complete",
            "--review-complete", "--completion-evidence-kind", "git_commit",
            "--completion-revision", self.revision)
        self.assertEqual(completed["task"]["status"], "done")
        index = read_evidence_index(self.install.fixed_root / "evidence")
        rows = [row for row in index.entries if row["task_id"] == self.task]
        self.assertEqual(len(rows), 1)
        payload = validate_evidence_source(index, rows[0]).source["payload"]
        self.assertEqual(payload["verification_basis"]["kind"], "runner_observation")
        self.assertIsNone(payload["verification_receipt"])
        observation = payload["runner_observation"]
        self.assertEqual(observation["outcome"], "pass")
        self.assertEqual(observation["complete_plan"], 1)
        self.assertEqual(observation["completed_step_count"], 1)
        runner_root = self.install.fixed_root / "verification-runner"
        for name in ("attempts", "quarantine"):
            self.assertEqual(list((runner_root / name).iterdir()), [])
        self.assertEqual((self.repo / "counter.py").read_text(), "raise RuntimeError('ambient')\n")

    def test_zero_test_script_is_not_equivalent_and_real_failure_stays_blocked(self):
        (self.repo / "counter.py").write_text(COUNTER.replace("sum(values)", "0"), encoding="utf-8")
        self.commit()
        direct, _ = self.execute("test_counter.py")
        self.assertEqual(direct.returncode, 0)
        self.assertNotIn(b"Ran 6 tests", direct.stderr)
        manual = self.target()
        duration = self.assert_six_equivalent(success=False)
        # Store the actual failure; a new generation is needed for the Runner attempt.
        self.receipt(manual["task"]["review_target_generation"], duration, "fail")
        self.publish()
        routed = self.target()
        self.assertEqual(routed["verification_route"], "blocked", routed)
        self.assertIsNone(routed["review_preparation"]["packet"])
        self.receipt(routed["task"]["review_target_generation"], 1,
                     error="evidence_basis_stale")
        self.assertFalse(self.cli("task", "show", self.task)["verification_evidence"]["gate"]["satisfied"])

    def test_setup_no_entry_rebind_stale_invalid_and_detach_routes(self):
        selected = self.cli("setup", "--verification-runner", "on")
        self.assertEqual(selected["optional_features"]["features"]["verification_runner"]["effective"], "on")
        self.assertEqual(json.loads(self.plan.read_text())["entries"], [])
        self.assertEqual(self.target()["verification_route"], "receipt_required")
        self.publish()
        before = json.loads(self.plan.read_text())["entries"][0]
        rebound = self.cli("task", "edit", self.task,
            "--verification", "All six counter discovery checks with fixed stdlib runtime",
            "--runner-plan-action", "rebind")
        self.assertEqual(rebound["runner_plan_update"]["status"], "updated")
        after = json.loads(self.plan.read_text())["entries"][0]
        self.assertEqual(after["steps"], before["steps"])
        self.assertNotEqual(after["verification_expectation_digest"], before["verification_expectation_digest"])
        # Known test-owned corruption only: never alter a live Plan as diagnosis.
        valid = self.plan.read_bytes()
        stale = json.loads(valid)
        stale["entries"][0] = before
        self.plan.write_text(json.dumps(stale), encoding="utf-8")
        self.cli("review", "target", "set", self.task, "--kind", "git_commit",
            "--revision", self.revision, error="plan_basis_mismatch")
        self.plan.write_text("{invalid", encoding="utf-8")
        self.cli("review", "target", "set", self.task, "--kind", "git_commit",
            "--revision", self.revision, error="plan_invalid")
        self.plan.write_bytes(valid)
        self.assertEqual(self.target()["verification_route"], "runner_pass")
        self.cli("task", "edit", self.task, "--runner-plan-action", "detach")
        self.assertEqual(self.target()["verification_route"], "receipt_required")


if __name__ == "__main__":
    unittest.main()
