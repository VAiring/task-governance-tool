"""Host approval fixtures are not proof of a live Guardian reduction."""
from __future__ import annotations

import copy
import importlib.machinery
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.m14_test_support import make_physical_install, file_snapshot, refresh_test_manifest
from tests.test_review_handoff_preparation import PreparationFixture
from tests.test_review_results import FINGERPRINT, encode, receipt
from task_governance_tool import task_preapproval as policy
from task_governance_tool.task_record_policy import RecordPolicyError, validate_record_arguments


class ArgumentBoundaryTests(unittest.TestCase):
    def test_normal_records_and_initial_contracts_are_admitted(self):
        for arguments in (
            ["task", "context"],
            ["task", "add", "--title", "Initial", "--contract-scope", "scope", "--contract-acceptance", "acceptance", "--review-tier", "2"],
            ["task", "add", "--from-stdin"],
            ["task", "edit", "task", "--status", "in_progress", "--add-note", "Progress"],
            ["task", "edit", "task", "--status", "paused", "--pause-reason", "User request"],
            ["task", "checkpoint", "task", "--summary", "Ready", "--next-action", "Review"],
            ["handoff", "record", "task", "--summary", "Follow-up"],
            ["review", "target", "set", "task", "--kind", "git_snapshot"],
            ["verification", "receipt", "add", "task", "--from-stdin"],
            ["review", "result", "add", "task"],
            ["task", "complete", "task", "--commit-not-required", "--verification-complete", "--review-complete"],
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(validate_record_arguments("taskgov.py", ["--records-only", *arguments, "--repo", "/repo", "--json"]), "/repo")

    def test_excluded_arguments_cannot_hide_by_position_combination_or_duplicate(self):
        edits = ("--title", "--description", "--contract-scope", "--contract-acceptance",
                 "--review-tier", "--verification", "--verification-not-required",
                 "--runner-plan-action", "--reopen-reason", "--review-tier-change-reason")
        for option in edits:
            for suffix in ([option, "change"], [option + "=change"]):
                for args in (["task", "edit", "id", "--status", "in_progress", *suffix],
                             [*suffix, "task", "edit", "id", "--status", "in_progress"]):
                    with self.subTest(args=args), self.assertRaises(RecordPolicyError):
                        validate_record_arguments("taskgov.py", ["--records-only", "--repo", "/repo", *args])
        for args in (
            ["setup"], ["task", "edit", "id", "--status", "done"],
            ["task", "edit", "id", "--status", "cancelled"],
            ["task", "edit", "id", "--stat", "in_progress"],
            ["task", "context", "--repo", "/other"],
            ["task", "context", "--repo=/repo"],
            ["task", "context", "--records-only"],
            ["review", "result", "add", "id", "--user-approved-reviewer", "self"],
            ["task", "complete", "id", "--external-revision-approved"],
            ["task", "edit", "id", "--status", "blocked", "--status", "ready"],
        ):
            with self.subTest(args=args), self.assertRaises(RecordPolicyError):
                validate_record_arguments("taskgov.py", ["--records-only", "--repo", "/repo", *args])
        for operation in ("prepare-finalization", "finalize", "wait-ended", "wait-basis"):
            with self.assertRaises(RecordPolicyError):
                validate_record_arguments("review_handoff.py", ["--records-only", operation, "--repo", "/repo"])

    def test_literal_shell_subset_rejects_compound_expansion_and_injected_stdin(self):
        words = ["/python", "-I", "-S", "-B", "/entry.py", "--records-only", "task", "add", "--title", "日本語 ' quoted $value\nsecond line"]
        for shell in ("powershell", "posix"):
            command = policy.shell_command(words, shell)
            self.assertEqual(policy.literal_command(command, shell), words)
            for tail in ("; echo extra", " && echo extra", " | evil", " > file", "\necho extra"):
                with self.subTest(shell=shell, tail=tail), self.assertRaises(ValueError):
                    policy.literal_command(command + tail, shell)
        for command in ("& $python -I", "& 'python' $(echo x)", "& 'python' `x", "& ‘python’", "cmd /c echo x & evil"):
            with self.assertRaises(ValueError):
                policy.literal_command(command, "powershell")
        command = policy.shell_command(words, "powershell")
        self.assertEqual(policy.literal_command(policy.UTF8_POWERSHELL + "@'\n{}\n'@ | " + command, "powershell"), words)
        with self.assertRaises(ValueError):
            policy.literal_command("@'\n’@\nevil\n'@ | " + command, "powershell")

    def test_posix_heredoc_rejects_first_and_intermediate_terminators_in_native_shell(self):
        shell = shutil.which("sh")
        if not shell and os.name == "nt" and shutil.which("git"):
            candidate = Path(shutil.which("git")).resolve().parents[1] / "bin/sh.exe"
            shell = str(candidate) if candidate.is_file() else None
        if not shell:
            self.skipTest("native POSIX shell unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            for index, prefix in enumerate(("", "payload\n")):
                marker = Path(temporary) / f"marker-{index}"
                compound = f"cat <<'END'\n{prefix}END\nprintf probe > marker-{index}\nEND"
                with self.assertRaises(ValueError):
                    policy.literal_command(compound, "posix")
                # Prove that this rejected syntax really does execute the tail.
                subprocess.run([shell, "-c", compound], cwd=temporary,
                               capture_output=True, check=False, timeout=10)
                self.assertEqual(marker.read_text(), "probe")
            self.assertEqual(policy.literal_command("cat <<'END'\n\nEND", "posix"), ["cat"])
            self.assertEqual(policy.literal_command("cat <<'END'\nliteral\nEND", "posix"), ["cat"])


class HookFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name).resolve())
        self.repo = self.install.project_root
        self.package = self.install.skill_root
        self.python = Path(sys.executable).resolve()
        self.digest = policy.runtime_digest(self.package)

    def event(self, arguments=None):
        args = arguments or ["task", "context"]
        return {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "cwd": str(self.repo),
                "tool_input": {"command": policy.shell_command([str(self.python), "-I", "-S", "-B",
                    str(self.install.entrypoint), "--records-only", *args, "--repo", str(self.repo), "--json"], "powershell")}}

    def decision(self, event):
        return policy.decide(event, repo=self.repo, python=self.python, package=self.package,
                             digest=self.digest, shell="powershell")

    def test_exact_scope_pin_and_default_defer_preserve_the_existing_boundary(self):
        event = self.event()
        before = file_snapshot(self.repo)
        self.assertEqual(self.decision(event)["hookSpecificOutput"]["decision"], {"behavior": "allow"})
        for mutate in (
            lambda e: e.update(tool_name="mcp__codex_app__send_message_to_thread"),
            lambda e: e.update(hook_event_name="PreToolUse"),
            lambda e: e.update(cwd=str(self.repo.parent)),
            lambda e: e.update(cwd=str(self.repo).swapcase()),
            lambda e: e["tool_input"].update(command=e["tool_input"]["command"].replace("'-I' ", "")),
            lambda e: e["tool_input"].update(command=e["tool_input"]["command"] + " ; echo other"),
            lambda e: e["tool_input"].update(command=e["tool_input"]["command"].replace("'--records-only' ", "")),
            lambda e: e["tool_input"].update(command=e["tool_input"]["command"].replace(str(self.install.entrypoint), str(self.install.entrypoint).swapcase())),
            lambda e: e["tool_input"].update(command=e["tool_input"]["command"].replace("'--repo'", "'--repo' '" + str(self.repo.parent) + "' '--repo'")),
        ):
            changed = copy.deepcopy(event)
            mutate(changed)
            self.assertEqual(self.decision(changed), {})
        self.assertEqual(file_snapshot(self.repo), before)
        code = self.package / "scripts/task_governance_tool/cli.py"
        code.write_bytes(code.read_bytes() + b"\n# changed\n")
        self.assertEqual(self.decision(event), {})
        refresh_test_manifest(self.package)
        self.assertEqual(self.decision(event), {})

    def test_configuration_proposal_preserves_all_existing_sources_and_writes_nothing(self):
        existing = {"description": "Keep", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing"}]}],
                    "PermissionRequest": [{"matcher": "MCP", "hooks": []}]}}
        before = file_snapshot(self.repo)
        kwargs = dict(repo=self.repo, python=self.python, package=self.package, shell="powershell")
        merged = policy.propose(existing, **kwargs)
        self.assertEqual(existing["hooks"]["Stop"], merged["hooks"]["Stop"])
        self.assertEqual(existing["hooks"]["PermissionRequest"], merged["hooks"]["PermissionRequest"][:-1])
        self.assertEqual(merged, policy.propose(merged, **kwargs))
        self.assertEqual(len(existing["hooks"]["PermissionRequest"]), 1)
        self.assertEqual(before, file_snapshot(self.repo))

    def test_generated_trusted_bootstrap_runs_and_declines_changed_policy_before_import(self):
        shell = "powershell" if os.name == "nt" else "posix"
        candidate = policy.propose({}, repo=self.repo, python=self.python, package=self.package, shell=shell)
        command = candidate["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
        event = self.event()
        event["tool_input"]["command"] = policy.shell_command([str(self.python), "-I", "-S", "-B", str(self.install.entrypoint),
            "--records-only", "task", "context", "--repo", str(self.repo), "--json"], shell)
        if shell == "powershell":
            host = shutil.which("powershell.exe")
            if not host:
                self.skipTest("native PowerShell unavailable")
            invocation = ["cmd.exe", "/d", "/s", "/c", command]
        else:
            invocation = ["/bin/sh", "-c", command]
        def run():
            result = subprocess.run(invocation, input=json.dumps(event).encode("ascii"), cwd=self.repo,
                                    capture_output=True, check=False, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(run()["hookSpecificOutput"]["decision"]["behavior"], "allow")
        # Neither package extensions nor a scripts-root stdlib substitute may
        # execute before the policy's full inventory check can abstain.
        for relative in ("task_governance_tool/task_preapproval" + importlib.machinery.EXTENSION_SUFFIXES[0], "json.py"):
            shadow = self.package / "scripts" / relative
            shadow.write_text("raise RuntimeError('unverified shadow must not load')\n", encoding="utf-8")
            self.assertEqual(run(), {})
            shadow.unlink()
        initializer = self.package / "scripts/task_governance_tool/__init__.py"
        initializer.write_text("raise RuntimeError('must never import changed policy')\n", encoding="utf-8")
        self.assertEqual(run(), {})

    def test_pinned_bootstrap_and_records_ignore_stale_bytecode(self):
        setup = self.install.run("setup", "--repo", str(self.repo), "--json")
        self.assertEqual(setup.returncode, 0, setup.stdout)
        shell = "powershell" if os.name == "nt" else "posix"
        candidate = policy.propose({}, repo=self.repo, python=self.python, package=self.package, shell=shell)
        command = candidate["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
        invocation = ["cmd.exe", "/d", "/s", "/c", command] if os.name == "nt" else ["/bin/sh", "-c", command]
        modules = ("source_imports.py", "task_governance_tool/__init__.py",
                   "task_governance_tool/task_preapproval.py", "task_governance_tool/task_record_policy.py",
                   "task_governance_tool/cli.py", "task_governance_tool/review_handoff.py")
        for index, relative in enumerate(modules):
            source = self.package / "scripts" / relative
            original, stamp = source.read_bytes(), source.stat()
            # Both unchecked-hash and timestamp-valid stale caches must be inert.
            stale = b"raise RuntimeError('unverified cache executed')\n"
            source.write_bytes(stale.ljust(len(original), b" "))
            os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            py_compile.compile(str(source), doraise=True, invalidation_mode=(
                py_compile.PycInvalidationMode.TIMESTAMP if index % 2 else py_compile.PycInvalidationMode.UNCHECKED_HASH))
            source.write_bytes(original)
            os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(policy.runtime_digest(self.package), self.digest)
        event = self.event()
        words = [str(self.python), "-I", "-S", "-B", str(self.install.entrypoint), "--records-only",
                 "task", "context", "--repo", str(self.repo), "--json"]
        event["tool_input"]["command"] = policy.shell_command(words, shell)
        for event_name, expected in (("PermissionRequest", "allow"), ("PermissionRequesX", None)):
            event["hook_event_name"] = event_name
            output = subprocess.run(invocation, input=json.dumps(event).encode("ascii"), cwd=self.repo,
                                    capture_output=True, check=False, timeout=30)
            self.assertEqual(output.returncode, 0, output.stdout or output.stderr)
            decision = json.loads(output.stdout).get("hookSpecificOutput", {}).get("decision", {}).get("behavior")
            self.assertEqual(decision, expected)
        output = subprocess.run(words, cwd=self.repo, capture_output=True, check=False, timeout=30)
        self.assertEqual(output.returncode, 0, output.stdout or output.stderr)
        self.assertTrue(json.loads(output.stdout)["ok"])
        helper = subprocess.run([str(self.python), "-I", "-S", "-B", str(self.package / "scripts/review_handoff.py"),
                                 "--records-only", "read", "--repo", str(self.repo), "--packet", "missing.json"],
                                cwd=self.repo, capture_output=True, check=False, timeout=30)
        self.assertNotEqual(helper.returncode, 0)
        self.assertFalse(json.loads(helper.stdout)["ok"])

    def test_unlisted_native_and_legacy_import_artifacts_decline(self):
        event = self.event()
        for relative in ("json.pyc", "task_governance_tool.pyd", "task_governance_tool.so",
                         "task_governance_tool/task_preapproval" + importlib.machinery.EXTENSION_SUFFIXES[0]):
            with self.subTest(relative=relative):
                extra = self.package / "scripts" / relative
                extra.write_bytes(b"unlisted import artifact")
                self.assertEqual(self.decision(event), {})
                extra.unlink()

    def test_same_named_directories_cannot_replace_pinned_sources(self):
        shell = "powershell" if os.name == "nt" else "posix"
        candidate = policy.propose({}, repo=self.repo, python=self.python, package=self.package, shell=shell)
        command = candidate["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
        invocation = ["cmd.exe", "/d", "/s", "/c", command] if os.name == "nt" else ["/bin/sh", "-c", command]
        words = [str(self.python), "-I", "-S", "-B", str(self.install.entrypoint), "--records-only",
                 "task", "context", "--repo", str(self.repo), "--json"]
        event = self.event()
        event["tool_input"]["command"] = policy.shell_command(words, shell)
        marker = self.repo / "unexpected-import"
        for module in ("task_preapproval", "task_record_policy", "cli", "review_handoff"):
            shadow = self.package / "scripts/task_governance_tool" / module
            shadow.mkdir()
            (shadow / "__init__.py").write_text(
                "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unexpected')\n"
                "raise RuntimeError('alternative source must not execute')\n", encoding="utf-8")
        # The actual trusted hook must abstain before any new initializer runs,
        # including on an invalid event which would not reach runtime_digest.
        for payload in (event, {}):
            output = subprocess.run(invocation, input=json.dumps(payload).encode("ascii"), cwd=self.repo,
                                    capture_output=True, check=False, timeout=30)
            self.assertEqual(output.returncode, 0, output.stdout or output.stderr)
            self.assertEqual(json.loads(output.stdout), {})
            self.assertFalse(marker.exists())
        # Direct restricted invocations must retain the original policy and
        # helper origins too; the intentionally excluded operation stays rejected.
        core = subprocess.run(words[:5] + ["--records-only", "setup", "--repo", str(self.repo), "--json"],
                              cwd=self.repo, capture_output=True, check=False, timeout=30)
        self.assertEqual(json.loads(core.stdout)["errors"][0]["code"], "record_operation_not_allowed")
        helper = subprocess.run([str(self.python), "-I", "-S", "-B", str(self.package / "scripts/review_handoff.py"),
                                 "--records-only", "finalize", "--repo", str(self.repo)],
                                cwd=self.repo, capture_output=True, check=False, timeout=30)
        self.assertNotEqual(helper.returncode, 0)
        self.assertFalse(json.loads(helper.stdout)["ok"])
        self.assertFalse(marker.exists())

    def test_posix_compound_heredoc_never_receives_allow(self):
        words = [str(self.python), "-I", "-S", "-B", str(self.install.entrypoint), "--records-only",
                 "task", "context", "--repo", str(self.repo), "--json"]
        event = self.event()
        for prefix in ("", "payload\n"):
            event["tool_input"]["command"] = policy.shell_command(words, "posix") + f" <<'END'\n{prefix}END\necho extra\nEND"
            self.assertEqual(policy.decide(event, repo=self.repo, python=self.python, package=self.package,
                                          digest=self.digest, shell="posix"), {})


class RecordFlowTests(PreparationFixture):
    def records(self, *args, raw=None):
        arguments = ["--records-only", *args, "--repo", str(self.root)]
        python = Path(sys.executable).resolve()
        command = policy.shell_command([str(python), "-I", "-S", "-B", str(self.install.entrypoint), *arguments, "--json"], "powershell")
        event = {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "cwd": str(self.root), "tool_input": {"command": command}}
        result = policy.decide(event, repo=self.root, python=python, package=self.install.skill_root,
                               digest=policy.runtime_digest(self.install.skill_root), shell="powershell")
        self.assertEqual(result["hookSpecificOutput"]["decision"]["behavior"], "allow")
        if raw is None:
            return self.cli(*arguments)
        completed = subprocess.run([str(python), "-I", "-S", "-B", str(self.install.entrypoint), *arguments, "--json"],
                                   input=raw, cwd=self.root, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        return json.loads(completed.stdout)["data"]

    def test_two_tasks_initial_contract_progress_evidence_and_completion(self):
        registered = self.records("task", "add", "--from-stdin", raw=encode({"version": 1,
            "common": {"review_tier": 0, "verification": "Fixture assertions", "kind": "sequential", "lane": "records"},
            "tasks": [{"title": f"Mechanical fixture {index}", "contract": {"scope": "Fixture only", "acceptance": "Evidence and completion"}}
                      for index in range(2)]}))
        tasks = [item["task"]["task_id"] for item in registered["tasks"]]
        rejected = self.install.run("--records-only", "task", "edit", tasks[1], "--status", "in_progress", "--repo", str(self.root), "--json")
        self.assertEqual(json.loads(rejected.stdout)["errors"][0]["code"], "sequential_predecessor_incomplete")
        for task in tasks:
            self.records("task", "edit", task, "--status", "in_progress")
            self.records("task", "edit", task, "--add-note", "Progress")
            self.records("task", "checkpoint", task, "--summary", "Progress", "--next-action", "Verify")
            self.records("handoff", "record", task, "--summary", "Out of scope example")
            target = self.records("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
            generation = target["task"]["review_target_generation"]
            self.records("verification", "receipt", "add", task, "--result", "pass", "--duration-ms", "1",
                         "--scope-coverage", "full", "--expected-target-generation", str(generation))
            self.records("review", "receipt", "add", task, "--reviewer", "mechanical", "--kind", "not_required",
                         "--verdict", "not_required", "--summary", "Mechanical fixture without product changes")
            completed = self.records("task", "complete", task, "--commit-not-required", "--verification-complete", "--review-complete")
            self.assertEqual(completed["task"]["status"], "done")
        self.assertTrue((self.root / ".taskgov").is_dir())

    def test_runtime_rejects_excluded_changes_before_state_or_files_are_changed(self):
        task = self.task()
        before = file_snapshot(self.root)
        for args in (("task", "edit", task, "--review-tier", "0"), ("setup",),
                     ("task", "edit", task, "--runner-plan-action", "disable"),
                     ("task", "edit", task, "--contract-scope", "expanded")):
            result = self.install.run("--records-only", *args, "--repo", str(self.root), "--json")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["errors"][0]["code"], "record_operation_not_allowed")
            self.assertEqual(before, file_snapshot(self.root))

    def test_shared_handoff_propagates_restriction_through_save_and_submit(self):
        task = self.task()
        completed = self.invoke("--records-only", "prepare", "--repo", str(self.root), "--directory", "reviews/records",
            "target", task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        handoff = json.loads(completed.stdout)["handoff"]
        self.assertIn("--records-only", handoff["submit_command"])
        packet = json.loads((self.root / handoff["packet_path"]).read_bytes())
        paths = []
        for index in range(2):
            path = f"reviews/records/review-{index + 1}.json"
            payload = copy.deepcopy(packet["result_template"])
            payload["receipts"] = [receipt(f"independent-{index}")]
            saved = self.invoke("--records-only", "save", "--repo", str(self.root), "--packet", handoff["packet_path"],
                               "--output", path, raw=encode(payload), reviewer=index)
            self.assertEqual(saved.returncode, 0, saved.stdout)
            paths.append(path)
        submitted = self.invoke("--records-only", "submit", "--repo", str(self.root), "--packet", handoff["packet_path"], *paths)
        self.assertEqual(submitted.returncode, 0, submitted.stdout)
        self.assertTrue(json.loads(submitted.stdout)["data"]["review_gate"]["satisfied"])


class RunnerSeparationTests(unittest.TestCase):
    def test_runner_eligible_target_stops_before_any_write_lock_cleanup_or_launch(self):
        from tests.test_m242_runner_service import RunnerServiceFixture, service
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            before = file_snapshot(Path(temporary))
            with mock.patch.object(service, "_prepare_runner", return_value=prepared), \
                 mock.patch.object(service, "zero_wait_runner_lock") as lock, \
                 mock.patch.object(service, "_persist_ordinary_target") as fallback, \
                 mock.patch.object(service, "_run_intent_under_lock") as launch:
                with self.assertRaises(service.VerificationRunnerServiceError) as rejected:
                    service.set_review_target_with_optional_runner(fixture.target, fixture.task_id,
                        kind="git_snapshot", allow_runner=False)
                self.assertEqual(rejected.exception.code, "runner_execution_requires_authorization")
                lock.assert_not_called()
                fallback.assert_not_called()
                launch.assert_not_called()
            self.assertEqual(before, file_snapshot(Path(temporary)))


if __name__ == "__main__":
    unittest.main()
