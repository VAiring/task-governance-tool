"""Installed public operations through the hook entrypoint, not direct collection."""

import json
import os
import shutil
import subprocess
import sys
import unittest

from tests import test_usage_evidence_install as evidence_support
from tests.test_usage_attribution import THREAD, turn
from tests.test_usage_collection import event, usage
from tests.test_usage_turn_adapter import tool_record
from tests.m14_test_support import file_snapshot
from tests import test_review_session_install as review_support
from tests.test_review_session_repository import OWNER, REVIEWER, SECOND
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.usage_evidence_service import repository_for


class UsageLifecycleInstallTests(unittest.TestCase):
    setUp = evidence_support.UsageEvidenceInstallTests.setUp
    cli = evidence_support.UsageEvidenceInstallTests.cli
    add = evidence_support.UsageEvidenceInstallTests.add
    complete = evidence_support.UsageEvidenceInstallTests.complete

    def log(self, acknowledgements, count=5):
        self.host = self.root.parent / "codex-host"
        directory = self.host / "sessions/2026/10/01"
        directory.mkdir(parents=True, exist_ok=True)
        self.transcript = directory / f"rollout-fixture-{THREAD}.jsonl"
        rows = [event("session_meta", id=THREAD, cwd=str(self.root), model_provider="openai")]
        for n in range(1, count + 1):
            rows += [event("event_msg", type="task_started", turn_id=turn(n), started_at=n * 1000),
                     event("turn_context", turn_id=turn(n), model="fixture-model")]
            rows += [tool_record(ack, n) for ack in acknowledgements.get(n, ())]
            rows.append(usage(f"r{n}", turn=turn(n)))
        self.transcript.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))

    def hook(self, kind="Stop", *, entrypoint=None, arguments=(), **fields):
        payload = {"hook_event_name": kind, "session_id": THREAD, "turn_id": turn(5),
                   "cwd": str(self.root), "transcript_path": str(self.transcript), **fields}
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
                    str(entrypoint or self.install.skill_root / "scripts/usage_hook.py"), *arguments],
                    cwd=self.root, input=json.dumps(payload).encode(), capture_output=True,
                    env={**os.environ, "CODEX_HOME": str(self.host)}, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"{}\n", b""))

    def test_unsupported_copied_package_preserves_all_state(self):
        self.log({})
        outside = self.root.parent / "copied-package"
        shutil.copytree(self.install.skill_root, outside)
        before = file_snapshot(self.root / ".taskgov")
        self.hook("SessionStart", entrypoint=outside / "scripts/usage_hook.py")
        self.hook("SessionStart", entrypoint=outside / "scripts/usage_hook.py",
                  arguments=("--repo", str(self.root)))
        self.assertEqual(file_snapshot(self.root / ".taskgov"), before)

    def test_setup_generated_command_collects_fixture_without_core_writes(self):
        selected = self.cli("setup", "--usage-collection", "on")
        self.assertEqual(selected["data"]["usage_hooks"]["status"], "prepared")
        self.log({})
        document = json.loads((self.root / ".codex/hooks.json").read_bytes())
        handler = document["hooks"]["SessionStart"][0]["hooks"][0]
        command = (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", handler["commandWindows"]]
                   if os.name == "nt" else ["/bin/sh", "-c", handler["command"]])
        payload = {"hook_event_name": "SessionStart", "session_id": THREAD,
                   "cwd": str(self.root), "transcript_path": str(self.transcript)}
        core = self.target.db_path.read_bytes()
        evidence = file_snapshot(self.target.resolved_evidence_root)
        result = subprocess.run(command, input=json.dumps(payload).encode(), capture_output=True,
            cwd=self.root, env={**os.environ, "CODEX_HOME": str(self.host)}, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"{}\n", b""))
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 5)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), evidence)

    def test_linked_package_preserves_all_state(self):
        self.log({})
        physical = self.root.parent / "physical-package"
        self.install.skill_root.rename(physical)
        if os.name == "nt":
            from tests.test_self_status import create_windows_junction
            create_windows_junction(self.install.skill_root, physical)
        else:
            self.install.skill_root.symlink_to(physical, target_is_directory=True)
        before = file_snapshot(self.root / ".taskgov")
        self.hook("SessionStart")
        self.hook("SessionStart", arguments=("--repo", str(self.root)))
        self.assertEqual(file_snapshot(self.root / ".taskgov"), before)

    def test_source_package_requires_explicit_repo_and_rejects_competitor(self):
        from task_governance_tool.usage_lifecycle import _target
        from task_governance_tool.usage_values import UsageError
        source = self.root / "task-governance-tool"
        self.install.skill_root.rename(source)
        (self.root / "docs").mkdir(exist_ok=True)
        for relative in ("AGENTS.md", "docs/specification.md", "docs/design.md", "plan.md"):
            (self.root / relative).write_text("Isolated source fixture\n", encoding="utf-8")
        before = file_snapshot(self.root / ".taskgov")
        with self.assertRaises(UsageError):
            _target(source, self.root)
        self.assertEqual(_target(source, self.root, repo_explicit=True).db_path, self.target.db_path)
        self.install.skill_root.mkdir()
        with self.assertRaises(UsageError):
            _target(source, self.root, repo_explicit=True)
        self.assertEqual(file_snapshot(self.root / ".taskgov"), before)

    def test_public_source_hook_requires_explicit_repo_then_collects_same_project(self):
        self.log({})
        source = self.root / "task-governance-tool"
        self.install.skill_root.rename(source)
        (self.root / "docs").mkdir(exist_ok=True)
        for relative in ("AGENTS.md", "docs/specification.md", "docs/design.md", "plan.md"):
            (self.root / relative).write_text("Isolated source fixture\n", encoding="utf-8")
        entry = source / "scripts/usage_hook.py"
        before = file_snapshot(self.root / ".taskgov")
        core = self.target.db_path.read_bytes()
        evidence = file_snapshot(self.target.resolved_evidence_root)
        self.hook("SessionStart", entrypoint=entry)
        self.assertEqual(file_snapshot(self.root / ".taskgov"), before)
        self.hook("SessionStart", entrypoint=entry, arguments=("--repo", str(self.root)))
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 5)
        self.hook(entrypoint=entry, arguments=("--repo", "."))
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 5)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), evidence)
        self.install.skill_root.mkdir()
        before = file_snapshot(self.root / ".taskgov")
        with self.transcript.open("ab") as stream:
            stream.write(json.dumps(usage("after-competitor", turn=turn(5))).encode() + b"\n")
        self.hook(entrypoint=entry, arguments=("--repo", str(self.root)))
        self.assertEqual(file_snapshot(self.root / ".taskgov"), before)

    def test_public_explicit_repo_does_not_redirect_or_accept_invalid_arguments(self):
        self.log({})
        before = file_snapshot(self.root / ".taskgov")
        for args in (("--repo", str(self.root.parent)), ("--repo",), ("--repo", ""),
                     ("--repo", " "), ("--other", str(self.root)),
                     ("--repo", str(self.root), "--repo", str(self.root))):
            with self.subTest(arguments=args):
                self.hook("SessionStart", arguments=args)
                self.assertEqual(file_snapshot(self.root / ".taskgov"), before)
        self.hook("SessionStart", arguments=("--repo", str(self.root)), cwd=str(self.root.parent))
        self.assertEqual(file_snapshot(self.root / ".taskgov"), before)
        self.hook("SessionStart", arguments=("--repo", str(self.root)))
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 5)

    def test_hook_after_completed_task_then_late_append_preserves_core_and_cycle(self):
        start = self.add()
        task = start["data"]["task"]["task_id"]
        paused = self.cli("task", "edit", task, "--status", "paused", "--pause-reason", "Isolated pause")
        resumed = self.cli("task", "edit", task, "--status", "in_progress")
        end = self.cli("task", "edit", task, "--status", "review_pending")
        done = self.complete(task)
        self.log({2: [start], 3: [paused, resumed], 4: [end], 5: [done]})
        core = self.target.db_path.read_bytes()
        evidence = file_snapshot(self.target.resolved_evidence_root)
        self.hook()
        result = self.cli("task", "show", task)["data"]["usage"]
        self.assertEqual(result["periods"][0]["models"][0]["total_tokens"], 3 * 120)
        cycle = result["cycle_links"][0]["completion_cycle_id"]
        with self.transcript.open("ab") as stream:
            stream.write(json.dumps(usage("late-endpoint", turn=turn(4))).encode() + b"\n")
        self.hook("SessionStart", source="resume")
        result = self.cli("task", "show", task, "--audit")["data"]["usage"]
        self.assertEqual(result["periods"][0]["models"][0]["total_tokens"], 4 * 120)
        self.assertEqual(result["cycle_links"][-1]["completion_cycle_id"], cycle)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), evidence)
        self.assertNotEqual(result["periods"][0]["quality"], "complete")

    def test_disabled_and_foreign_scope_never_turn_into_implicit_collection(self):
        start = self.add()
        task = start["data"]["task"]["task_id"]
        self.log({2: [start]})
        # No event means no automatic read from an ordinary Task command.
        before = self.target.resolved_usage_database.read_bytes()
        self.cli("task", "context")
        self.cli("task", "show", task)
        self.assertEqual(self.target.resolved_usage_database.read_bytes(), before)
        self.hook(cwd=str(self.root.parent))
        self.assertEqual(self.target.resolved_usage_database.read_bytes(), before)

    def test_older_numerical_store_requires_explicit_setup_not_a_hook(self):
        from task_governance_tool.usage_repository import UsageRepository
        self.log({})
        path = self.target.resolved_usage_database
        path.unlink()
        UsageRepository(path, *self.repository.basis).initialize()
        before = path.read_bytes()
        self.hook("SessionStart")
        self.assertEqual(path.read_bytes(), before)
        self.cli("setup")
        self.hook("SessionStart")
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 5)


class UsageLifecycleReviewInstallTests(unittest.TestCase):
    setUp = review_support.ReviewSessionInstallTests.setUp
    invoke = review_support.ReviewSessionInstallTests.invoke
    cli = review_support.ReviewSessionInstallTests.cli
    helper = review_support.ReviewSessionInstallTests.helper
    original = review_support.ReviewSessionInstallTests.original

    def test_segment_discovery_recovers_parent_and_preserves_review_cycle_snapshots(self):
        target = resolve_project_state(skill_root=self.install.skill_root, repo=self.root).target
        repository = repository_for(target)
        host = self.root.parent / "segmented-host"
        logs = host / "sessions/2026/10/02"
        logs.mkdir(parents=True)

        def rows(thread, prefix, acknowledgements, numbers):
            result = [event("session_meta", id=thread, cwd=str(self.root), model_provider="openai")]
            for number in numbers:
                result += [event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                           event("turn_context", turn_id=turn(number), model="fixture-model")]
                result += [tool_record(ack, number) for ack in acknowledgements.get(number, ())]
                result.append(usage(f"{prefix}-{number}", thread=thread, turn=turn(number)))
            return b"".join(json.dumps(row).encode() + b"\n" for row in result)

        def hook(kind="Stop"):
            # No transcript hint: a later event must discover every registered participant.
            payload = {"hook_event_name": kind, "session_id": OWNER.session_id, "cwd": str(self.root)}
            result = subprocess.run([sys.executable, "-I", "-S", "-B",
                str(self.install.skill_root / "scripts/usage_hook.py")], cwd=self.root,
                input=json.dumps(payload).encode(), capture_output=True,
                env={**os.environ, "CODEX_HOME": str(host)}, timeout=30)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"{}\n", b""))

        outputs = []
        expected = set()
        for number, caller in enumerate((REVIEWER, SECOND)):
            read = self.invoke(self.install.skill_root / "scripts/review_handoff.py",
                ["read", "--repo", str(self.root), "--packet", self.packet_path, "--role", "independent"],
                caller=caller)
            self.assertEqual(read.returncode, 0, read.stdout or read.stderr)
            displayed = json.loads(read.stdout)
            output = f"reviews/g1/segment-review-{number}.json"
            saved = self.helper("save", "--packet", self.packet_path, "--output", output,
                                caller=caller, raw=self.original(f"segment-review-{number}"))
            outputs.append(output)
            prefix = f"review-{number}"
            path = logs / f"rollout-2026-10-02T00-00-00-{caller.session_id}.jsonl"
            path.write_bytes(rows(caller.session_id, prefix, {2: [displayed], 4: [saved]}, range(1, 6)))
            expected.update(("openai", f"{prefix}-{n}") for n in (2, 3, 4))
        self.helper("submit", "--packet", self.packet_path, *outputs)
        end = self.cli("task", "edit", self.task, "--status", "review_pending")
        done = self.cli("task", "complete", self.task, "--verification-complete", "--review-complete",
                        "--commit-not-required")
        acknowledgements = {2: [self.started], 4: [end], 5: [done]}
        legacy = logs / f"rollout-2026-10-02T00-00-00-{OWNER.session_id}.jsonl"
        legacy.write_bytes(rows(OWNER.session_id, "parent", {}, (1,)))
        core = target.db_path.read_bytes()
        evidence = file_snapshot(target.resolved_evidence_root)
        hook()
        initial = self.cli("task", "show", self.task)["data"]["usage"]
        self.assertEqual(initial["periods"][0]["response_count"], 6)
        cycle = initial["cycle_links"][0]["completion_cycle_id"]
        old_snapshots = file_snapshot(target.resolved_usage_snapshots)

        # A previously missed current segment coexists with the older file and
        # repeats its first response. Discovery and ordinary replay recover it.
        segment = logs / f"rollout-2026-10-02T00-00-00-{OWNER.session_id}_{THREAD}.jsonl"
        segment.write_bytes(rows(OWNER.session_id, "parent", acknowledgements, range(1, 6)))
        hook("SessionStart")
        expected.update(("openai", f"parent-{n}") for n in (2, 3, 4))

        def assert_current():
            current = self.cli("task", "show", self.task)["data"]["usage"]
            period = current["periods"][0]
            self.assertEqual(period["response_count"], len(expected))
            self.assertEqual(period["models"][0]["total_tokens"], len(expected) * 120)
            self.assertEqual({link["completion_cycle_id"] for link in current["cycle_links"]}, {cycle})
            with repository.connection() as connection:
                members = {key for identity in period["snapshot_ids"]
                           for key in repository.members(connection, identity)}
            self.assertEqual(members, expected)
            self.assertEqual(target.db_path.read_bytes(), core)
            self.assertEqual(file_snapshot(target.resolved_evidence_root), evidence)
            now = file_snapshot(target.resolved_usage_snapshots)
            self.assertTrue(old_snapshots.items() <= now.items())
            return period

        recovered = assert_current()
        successor = json.loads((target.resolved_usage_snapshots / (recovered["snapshot_ids"][0] + ".json")).read_bytes())
        self.assertEqual(set(successor["predecessors"]), set(initial["periods"][0]["snapshot_ids"]))
        stable = file_snapshot(target.resolved_usage_root)
        hook()
        self.assertEqual(assert_current(), recovered)
        self.assertEqual(file_snapshot(target.resolved_usage_root), stable)
        with segment.open("ab") as stream:
            stream.write(json.dumps(usage("parent-late", thread=OWNER.session_id, turn=turn(4))).encode() + b"\n")
        expected.add(("openai", "parent-late"))
        hook()
        assert_current()
        hook()
        assert_current()

    def test_read_is_readonly_saved_child_collects_but_needs_core_receipt(self):
        target = resolve_project_state(skill_root=self.install.skill_root, repo=self.root).target
        repository = repository_for(target)
        before = target.resolved_usage_database.read_bytes()
        display = self.invoke(self.install.skill_root / "scripts/review_handoff.py",
            ["read", "--repo", str(self.root), "--packet", self.packet_path, "--role", "independent"],
            caller=REVIEWER)
        self.assertEqual(display.returncode, 0)
        self.assertEqual(target.resolved_usage_database.read_bytes(), before)
        displayed = json.loads(display.stdout)
        output = "reviews/g1/hook-child.json"
        saved = self.helper("save", "--packet", self.packet_path, "--output", output,
                            caller=REVIEWER, raw=self.original("hook-child"))
        self.assertIn(REVIEWER.session_id, repository.registered_sources()[0])
        host = self.root.parent / "review-host"
        logs = host / "sessions/2026/10/01"
        logs.mkdir(parents=True)
        path = logs / f"rollout-child-{REVIEWER.session_id}.jsonl"
        rows = [event("session_meta", id=REVIEWER.session_id, cwd=str(self.root), model_provider="openai")]
        for n in range(1, 6):
            rows += [event("event_msg", type="task_started", turn_id=turn(n), started_at=n * 1000),
                     event("turn_context", turn_id=turn(n), model="review-model")]
            if n in (2, 4):
                rows.append(tool_record(displayed if n == 2 else saved, n))
            rows.append(usage(f"review-{n}", thread=REVIEWER.session_id, turn=turn(n)))
        path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        payload = {"hook_event_name": "SubagentStop", "cwd": str(self.root),
                   "session_id": OWNER.session_id, "agent_id": REVIEWER.session_id,
                   "agent_transcript_path": str(path)}
        def hook():
            result = subprocess.run([sys.executable, "-I", "-S", "-B",
                        str(self.install.skill_root / "scripts/usage_hook.py")], cwd=self.root,
                        input=json.dumps(payload).encode(), capture_output=True,
                        env={**os.environ, "CODEX_HOME": str(host)}, timeout=30)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"{}\n", b""))
        hook()
        self.assertEqual(repository.summary()["models"][0]["response_count"], 5)
        initial = self.cli("task", "show", self.task)["data"]["usage"]
        self.assertEqual(initial["periods"][0]["models"], [])
        self.helper("submit", "--packet", self.packet_path, output)
        core = target.db_path.read_bytes()
        hook()
        shown = self.cli("task", "show", self.task)["data"]["usage"]
        self.assertEqual(shown["periods"][0]["models"][0]["total_tokens"], 360)
        self.assertEqual(target.db_path.read_bytes(), core)


if __name__ == "__main__":
    unittest.main()
