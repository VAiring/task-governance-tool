"""Installed helper through lifecycle collection to real core cycle snapshots."""

from contextlib import closing
import json
import os
import subprocess
import sys
import unittest

from tests import test_usage_evidence_install as support
from tests.test_usage_attribution import THREAD, OTHER, turn
from tests.test_usage_collection import event, usage
from tests.test_usage_turn_adapter import tool_record
from task_governance_tool.storage import connect_initialized_readonly
from task_governance_tool.usage_values import UsageError


class UsageWaitInstallTests(unittest.TestCase):
    setUp = support.UsageEvidenceInstallTests.setUp
    cli = support.UsageEvidenceInstallTests.cli
    add = support.UsageEvidenceInstallTests.add
    complete = support.UsageEvidenceInstallTests.complete

    def prepare(self):
        self.cli("setup", "--usage-collection", "on")
        start = self.add()
        task = start["data"]["task"]["task_id"]
        before_target = self.target.db_path.read_bytes()
        targeted = self.cli("review", "target", "set", task, "--kind", "diff_fingerprint",
                            "--revision", "sha256:" + "a" * 64)
        packet = targeted["data"]["review_preparation"]["packet"]
        end = self.cli("task", "edit", task, "--status", "review_pending")
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
            str(self.install.skill_root / "scripts/review_handoff.py"), "wait-ended",
            "--repo", str(self.root), "--from-stdin"], cwd=self.root,
            input=json.dumps(packet).encode(), capture_output=True,
            env={**os.environ, "CODEX_THREAD_ID": OTHER}, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        marker = json.loads(result.stdout)
        self.assertTrue(marker["ok"], marker)
        self.assertEqual(marker["review_wait"]["session_id"], OTHER)
        # A numerical wait does not need a successful original or any Receipt.
        shown = self.cli("task", "show", task)
        self.assertEqual(shown["data"]["review_evidence"]["counts"]["receipts_current_generation"], 0)
        self.host = self.root.parent / "wait-host"
        self.logs = self.host / "sessions/2026/10/06"
        self.logs.mkdir(parents=True)
        return task, start, end, marker, before_target

    def log(self, thread, prefix, acknowledgements, numbers):
        rows = [event("session_meta", id=thread, cwd=str(self.root), model_provider="openai")]
        for number in numbers:
            rows.extend([event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                         event("turn_context", turn_id=turn(number), model="fixture-model")])
            rows.extend(tool_record(value, number) for value in acknowledgements.get(number, ()))
            rows.append(usage(f"{prefix}-{number}", thread=thread, turn=turn(number)))
        path = self.logs / f"rollout-fixture-{thread}.jsonl"
        path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        return path

    def hook(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
            str(self.install.skill_root / "scripts/usage_hook.py")], cwd=self.root,
            input=json.dumps({"hook_event_name": "Stop", "session_id": THREAD, "cwd": str(self.root)}).encode(),
            capture_output=True, env={**os.environ, "CODEX_HOME": str(self.host)}, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"{}\n", b""))

    def test_helper_sender_lifecycle_late_completion_reopen_and_echo_exclusion(self):
        task, start, end, marker, _ = self.prepare()
        done = self.complete(task)
        parent = self.log(THREAD, "parent", {2: [start], 3: [end], 5: [marker], 6: [done]}, range(1, 8))
        supervisor = self.log(OTHER, "supervisor", {4: [marker], 7: [marker]}, range(1, 9))
        core = self.target.db_path.read_bytes()
        self.hook()
        first = self.cli("task", "show", task)["data"]["usage"]["periods"][0]
        self.assertEqual(first["response_count"], 3, first)
        with self.repository.connection() as connection:
            members = {key for identity in first["snapshot_ids"] for key in self.repository.members(connection, identity)}
        self.assertEqual(members, {("openai", "parent-2"), ("openai", "parent-3"), ("openai", "supervisor-4")})
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.hook()
        self.assertEqual(self.cli("task", "show", task)["data"]["usage"]["periods"][0], first)

        with supervisor.open("ab") as stream:
            stream.write(json.dumps(usage("late-decision", thread=OTHER, turn=turn(4))).encode() + b"\n")
        reopened = self.cli("task", "edit", task, "--status", "in_progress", "--reopen-reason", "Approved fixture follow-up")
        closed = self.cli("task", "edit", task, "--status", "review_pending")
        done_again = self.complete(task)
        with parent.open("ab") as stream:
            for number, acknowledgement in ((8, reopened), (9, closed), (10, done_again)):
                for row in (event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                            event("turn_context", turn_id=turn(number), model="fixture-model"),
                            tool_record(acknowledgement, number), usage(f"parent-{number}", turn=turn(number))):
                    stream.write(json.dumps(row).encode() + b"\n")
        core = self.target.db_path.read_bytes()
        self.hook()
        prior, current = self.cli("task", "show", task, "--audit")["data"]["usage"]["periods"]
        self.assertEqual((prior["response_count"], current["response_count"]), (4, 2))
        self.assertEqual(prior["completion_cycle_id"], first["completion_cycle_id"])
        self.assertNotEqual(current["completion_cycle_id"], prior["completion_cycle_id"])
        self.assertEqual(self.target.db_path.read_bytes(), core)

    def test_real_core_restore_without_manifest_rejects_old_snapshot_and_fact(self):
        task, start, end, marker, before_target = self.prepare()
        self.log(THREAD, "parent", {2: [start], 3: [end]}, range(1, 5))
        self.log(OTHER, "supervisor", {4: [marker]}, range(1, 6))
        self.hook()
        self.assertEqual(self.cli("task", "show", task)["data"]["usage"]["periods"][0]["response_count"], 3)
        # Restore a valid real core with the original execution but before the
        # target capture. The numerical store must not supply the missing anchor.
        self.target.db_path.write_bytes(before_target)
        with closing(connect_initialized_readonly(self.target)) as core:
            with self.assertRaises(UsageError):
                self.repository.read(core, task_id=task)
        self.hook()
        shown = self.cli("task", "show", task)["data"]["usage"]
        self.assertEqual(shown["periods"][0]["response_count"], 3)  # Owner is now open through turn 4.
        with self.repository.connection() as connection:
            members = {key for identity in shown["periods"][0]["snapshot_ids"]
                       for key in self.repository.members(connection, identity)}
        self.assertNotIn(("openai", "supervisor-4"), members)
