"""Real POSIX public Runner-to-completion acceptance in temporary installs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import make_physical_install, repository_git_environment
from tests.evidence_reader_oracle import read_evidence_index, validate_evidence_source

from task_governance_tool import storage


POSIX_POLICY_DIGEST = (
    "sha256:37872a1957f5428b3b18014dbb447722731f91aec86e88c324354a72d7fbffa4"
)
RAW_OUTPUT = "TG_OS_R8_RAW_OUTPUT_MUST_NOT_PERSIST_83be91"
PRIVATE_ARGUMENT = "TG_OS_R8_ARGUMENT_MUST_NOT_PERSIST_23aeb1"
PARENT_ENVIRONMENT = "TG_OS_R8_PARENT_ENVIRONMENT_96cad2"


@unittest.skipUnless(sys.platform in {"linux", "darwin"}, "requires the actual public POSIX Runner")
class PosixRunnerPublicGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # The public resolver accepts only a physical canonical install root.
        self.install = make_physical_install(Path(temporary.name).resolve())
        self.repo = self.install.project_root
        self.public_outputs = []

    def _git(self, *arguments):
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            env=repository_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _run(self, *arguments, draft=None, error=None):
        environment = repository_git_environment()
        environment.pop("PYTHONPATH", None)
        environment[PARENT_ENVIRONMENT] = PARENT_ENVIRONMENT
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(self.install.entrypoint), *arguments],
            cwd=self.repo,
            env=environment,
            input=None if draft is None else json.dumps(draft).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=90,
        )
        self.public_outputs.extend((result.stdout, result.stderr))
        body = json.loads(result.stdout.decode("utf-8"))
        if error is None:
            self.assertEqual(result.returncode, 0, body)
            self.assertIs(body["ok"], True, body)
        else:
            self.assertNotEqual(result.returncode, 0, body)
            self.assertIs(body["ok"], False, body)
            self.assertEqual(body["errors"][0]["code"], error, body)
        return body["data"]

    def _prepare(self, behavior="pass"):
        self._git("init", "--quiet")
        self._git("config", "user.name", "TaskGov Fixture")
        self._git("config", "user.email", "taskgov@example.invalid")
        self._git("config", "commit.gpgsign", "false")
        self._git("config", "core.hooksPath", str(self.repo / ".git" / "disabled-hooks"))
        (self.repo / ".gitignore").write_text(
            "/.agents/skills/task-governance-tool/state/\n"
            "/.agents/skills/task-governance-tool/config/verification-runner.json\n",
            encoding="utf-8",
        )
        checks = self.repo / "checks"
        checks.mkdir()
        (self.repo / "payload.txt").write_text("committed payload\n", encoding="utf-8")
        first = (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "root = Path.cwd()\n"
            "assert (root / 'payload.txt').read_text() == 'committed payload\\n'\n"
            "assert not (root / 'ambient-only.txt').exists()\n"
            "assert not (root / '.agents').exists()\n"
            f"assert os.environ.get({PARENT_ENVIRONMENT!r}) is None\n"
            f"assert sys.argv[1:] == [{PRIVATE_ARGUMENT!r}]\n"
            f"print({RAW_OUTPUT!r}, flush=True)\n"
            "(root / 'first-step-only.txt').write_text('first step complete')\n"
        )
        if behavior == "nonzero":
            first += "raise SystemExit(7)\n"
        elif behavior == "timeout":
            first += "while True:\n    time.sleep(60)\n"
        elif behavior != "pass":
            raise AssertionError("unsupported fixture behavior")
        (checks / "first.py").write_text(first, encoding="utf-8")
        (checks / "second.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "assert Path('first-step-only.txt').read_text() == 'first step complete'\n"
            f"print({RAW_OUTPUT!r}, file=sys.stderr, flush=True)\n"
            "Path('second-step-only.txt').write_text('second step complete')\n",
            encoding="utf-8",
        )
        self._git("add", ".gitignore", "payload.txt", "checks")
        self._git("commit", "--quiet", "-m", "temporary Runner gate fixture")
        self.revision = self._git("rev-parse", "HEAD").decode("ascii").strip()
        setup = self._run("setup", "--json")
        self.assertIs(setup["maintenance_enabled"], True)
        task = self._run(
            "task", "add", "--title", "Temporary POSIX Runner gate fixture",
            "--status", "in_progress", "--review-tier", "1",
            "--verification", "python checks/first.py and python checks/second.py",
            "--contract-scope", "Exercise the committed temporary fixture only",
            "--contract-acceptance", "Both committed checks pass in order",
            "--json",
        )["task"]
        self.task_id = task["task_id"]
        self.draft = {
            "version": 2,
            "steps": [
                {
                    "step_id": name,
                    "mode": "script",
                    "entrypoint": f"checks/{name}.py",
                    "argv": [PRIVATE_ARGUMENT] if name == "first" else [],
                    "cwd": ".",
                    "timeout_seconds": 1 if behavior == "timeout" and name == "first" else 20,
                    "cpu_seconds": 10,
                    "windows_limits": None,
                    "output_byte_limit": 1_048_576,
                }
                for name in ("first", "second")
            ],
        }
        self._publish_plan()
        # Ambient tracked and untracked bytes must not reach exact materialization.
        (self.repo / "payload.txt").write_text("ambient payload\n", encoding="utf-8")
        (self.repo / "ambient-only.txt").write_text("ambient only\n", encoding="utf-8")

    def _publish_plan(self):
        edited = self._run(
            "task", "edit", self.task_id, "--runner-plan-action", "replace", "--json",
            draft=self.draft,
        )
        self.assertEqual(edited["runner_plan_update"], {"action": "replace", "status": "updated"})
        self.assertEqual(edited["changed_fields"], [])
        self.assertIsNone(edited["event"])
        plan_path = self.install.skill_root / "config" / "verification-runner.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["version"], 2)
        self.assertIs(plan["trusted_local"], True)
        self.assertEqual(plan["entries"][0]["task_id"], self.task_id)
        self.assertEqual(plan["entries"][0]["steps"], self.draft["steps"])
        relative = plan_path.relative_to(self.repo).as_posix()
        self._git("check-ignore", "--quiet", "--no-index", "--", relative)
        self.assertEqual(self._git("ls-files", "--", relative), b"")

    def _target(self, route):
        routed = self._run(
            "review", "target", "set", self.task_id, "--kind", "git_commit",
            "--revision", self.revision, "--json",
        )
        self.assertEqual(routed["verification_route"], route)
        self.assertEqual(
            routed["blocking_code"], "verification_receipt_blocking" if route == "blocked" else None,
        )
        self.assertEqual(routed["task"]["review_target_generation"], 1)
        return routed

    def _graph(self):
        # Read-only established repository seam; all state writes use the public CLI.
        with closing(storage.connect_initialized_readonly(self.install.target)) as connection:
            return storage.read_verification_runner_generation_locked(
                connection, project_id=self.install.project_id,
                task_id=self.task_id, target_generation=1,
            )

    def _assert_gate(self, blocking_code=None):
        shown = self._run("task", "show", self.task_id, "--json")
        self.assertEqual(shown["task"]["status"], "in_progress")
        self.assertEqual(shown["task"]["review_target_generation"], 1)
        evidence = shown["verification_evidence"]
        self.assertEqual(evidence["gate"], {
            "required": True,
            "satisfied": blocking_code is None,
            "blocking_code": blocking_code,
            "qualifying_receipt_id": None,
        })
        self.assertEqual(evidence["counts"], {
            "blocking_exact_current": 0, "qualifying_exact_current": 0,
            "receipts_exact_current": 0,
        })
        self.assertIsNone(evidence["current_receipt"])

    def _review(self):
        # Explicit test-only human provenance, not a claimed independent agent review.
        self._run(
            "review", "receipt", "add", self.task_id,
            "--reviewer", "fixture-human-reviewer", "--kind", "independent",
            "--verdict", "pass", "--summary", "Test-only human Review Receipt fixture",
            "--reviewer-class", "human", "--model-state", "not_applicable",
            "--skill-state", "not_applicable", "--context-relation", "external_context",
            "--json",
        )

    def _complete(self, error=None):
        return self._run(
            "task", "complete", self.task_id, "--verification-complete", "--review-complete",
            "--completion-evidence-kind", "git_commit", "--completion-revision", self.revision,
            "--json", error=error,
        )

    def _assert_receipt_rejected(self):
        self._run(
            "verification", "receipt", "add", self.task_id, "--result", "pass",
            "--duration-ms", "25", "--scope-coverage", "full",
            "--expected-target-generation", "1", "--json", error="evidence_basis_stale",
        )

    def _target_snapshot(self):
        # Include every nongenerated project/package file, as well as Git index/refs.
        state = self.install.skill_root / "state"
        files = {
            path.relative_to(self.repo).as_posix(): path.read_bytes()
            for path in self.repo.rglob("*")
            if path.is_file() and not path.is_relative_to(state)
            and not path.is_relative_to(self.repo / ".git")
        }
        return (
            files, self._git("status", "--porcelain=v1", "--untracked-files=all"),
            (self.repo / ".git" / "index").read_bytes(), self._git("show-ref"),
        )

    def _assert_cleanup_and_privacy(self):
        runner_root = self.install.fixed_root / "verification-runner"
        self.assertTrue(runner_root.is_dir())
        for name in ("attempts", "quarantine"):
            self.assertEqual(list((runner_root / name).iterdir()), [])
        self.assertFalse((self.repo / "first-step-only.txt").exists())
        self.assertFalse((self.repo / "second-step-only.txt").exists())
        retained = self.public_outputs + [
            path.read_bytes() for path in self.install.fixed_root.rglob("*") if path.is_file()
        ]
        for value in (RAW_OUTPUT, PRIVATE_ARGUMENT, PARENT_ENVIRONMENT):
            for payload in retained:
                self.assertNotIn(value.encode("utf-8"), payload)

    def test_two_step_public_runner_completes_and_publishes_independently_readable_evidence(self):
        self._prepare()
        before = self._target_snapshot()
        self._target("runner_pass")
        graph = self._graph()
        self.assertEqual(graph["state"], "terminal")
        self.assertEqual(graph["resolution"].plan_version, 2)
        self.assertEqual(graph["resolution"].runner_policy_digest, POSIX_POLICY_DIGEST)
        observation = graph["observation"]
        self.assertEqual(
            (observation.route, observation.launch_state, observation.outcome, observation.reason,
             observation.complete_plan, observation.total_step_count, observation.completed_step_count),
            ("runner", "launched", "pass", None, 1, 2, 2),
        )
        self.assertIsNone(observation.failed_step_ordinal)
        self.assertIs(type(observation.cpu_time_ms), int)
        self.assertGreaterEqual(observation.cpu_time_ms, 0)
        self.assertIsNone(observation.peak_job_memory_bytes)
        self.assertIsNone(observation.total_process_count)
        self._assert_gate()
        self._assert_cleanup_and_privacy()
        self._review()
        completed = self._complete()
        self.assertEqual(completed["task"]["status"], "done")

        index = read_evidence_index(
            self.install.fixed_root / "evidence", expected_project_id=self.install.project_id,
        )
        self.assertEqual((index.format_version, index.source_schema_version), (2, 22))
        entries = [entry for entry in index.entries if entry["task_id"] == self.task_id]
        self.assertEqual(len(entries), 1)
        source = validate_evidence_source(index, entries[0])
        self.assertEqual(source.source_kind, "native_bundle")
        payload = source.source["payload"]
        runner = payload["runner_observation"]
        self.assertEqual(payload["verification_basis"], {
            "basis_version": 1, "kind": "runner_observation", "verification_receipt_id": None,
            "runner_observation_id": observation.verification_runner_observation_id,
        })
        self.assertIsNone(payload["verification_receipt"])
        self.assertEqual(runner["observation_id"], observation.verification_runner_observation_id)
        self.assertEqual(runner["runner_policy_digest"], POSIX_POLICY_DIGEST)
        self.assertEqual((runner["complete_plan"], runner["total_step_count"], runner["completed_step_count"]), (1, 2, 2))
        self.assertEqual(runner["cpu_time_ms"], observation.cpu_time_ms)
        self.assertIsNone(runner["peak_job_memory_bytes"])
        self.assertIsNone(runner["total_process_count"])
        references = [row for row in payload["evidence_references"] if row["source_kind"] == "runner_observation"]
        self.assertEqual(len(references), 1)
        reference = references[0]
        self.assertEqual(reference["source_id"], runner["observation_id"])
        self.assertEqual((reference["assurance_class"], reference["producer_class"]), ("machine_observed", "verification_runner"))
        links = [row for row in payload["criterion_links"] if row["relation"] == "runner_observation"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["evidence_reference_id"], reference["evidence_reference_id"])
        self._assert_cleanup_and_privacy()
        self.assertEqual(self._target_snapshot(), before)

    def _assert_launched_failure(self, behavior, outcome, reason):
        self._prepare(behavior)
        before = self._target_snapshot()
        self._target("blocked")
        graph = self._graph()
        self.assertEqual(graph["state"], "terminal")
        observation = graph["observation"]
        self.assertEqual(
            (observation.route, observation.launch_state, observation.outcome, observation.reason,
             observation.complete_plan, observation.total_step_count,
             observation.completed_step_count, observation.failed_step_ordinal),
            ("runner", "launched", outcome, reason, 0, 2, 1, 1),
        )
        self._assert_gate("verification_receipt_blocking")
        self._assert_cleanup_and_privacy()
        self._assert_receipt_rejected()
        self._review()
        self._complete(error="verification_receipt_blocking")
        self._assert_gate("verification_receipt_blocking")
        index = read_evidence_index(self.install.fixed_root / "evidence")
        self.assertEqual(index.entries, ())
        self._assert_cleanup_and_privacy()
        self.assertEqual(self._target_snapshot(), before)

    def test_real_nonzero_attempt_cannot_be_overridden_by_manual_receipt(self):
        self._assert_launched_failure("nonzero", "fail", "step_nonzero")

    def test_real_timeout_attempt_cannot_be_overridden_by_manual_receipt(self):
        self._assert_launched_failure("timeout", "timeout", "timeout")

    def test_changed_plan_makes_real_runner_pass_stale_without_relaunch(self):
        self._prepare()
        self._target("runner_pass")
        observation = self._graph()["observation"]
        self._assert_gate()
        self._review()
        self.draft["steps"][1]["timeout_seconds"] = 21
        self._publish_plan()
        before = self._target_snapshot()
        self._assert_gate("evidence_basis_stale")
        self._assert_receipt_rejected()
        self._complete(error="evidence_basis_stale")
        self.assertEqual(self._graph()["observation"], observation)
        self._assert_cleanup_and_privacy()
        self.assertEqual(self._target_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
