"""Real isolated package: usage never enters core completion or sealed Evidence."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.m14_test_support import make_physical_install, file_snapshot
from tests.test_usage_attribution import THREAD, turn
from tests.test_usage_collection import event, usage
from tests.test_usage_turn_adapter import tool_record
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.storage import connect_initialized_readonly
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_collection import collect_registered, register_source
from task_governance_tool import usage_evidence_service as service
from task_governance_tool.usage_values import UsageError


class UsageEvidenceInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name), git_managed=True)
        self.root = self.install.project_root
        self.cli("setup")
        resolution = resolve_project_state(skill_root=self.install.skill_root, repo=self.root)
        self.assertIsNone(resolution.error_code)
        self.target = resolution.target
        self.repository = service.repository_for(self.target)

    def cli(self, *arguments, ok=True):
        environment = {**os.environ, "CODEX_THREAD_ID": THREAD}
        environment.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(self.install.entrypoint),
                                 *arguments, "--json"], cwd=self.root, env=environment,
                                capture_output=True, timeout=30, check=False)
        value = json.loads(result.stdout)
        self.assertEqual(value["ok"], ok, value)
        self.assertEqual(result.returncode == 0, ok, value)
        return value

    def add(self, title="Usage fixture"):
        return self.cli("task", "add", "--title", title, "--status", "in_progress", "--review-tier", "0",
                        "--verification-not-required", "Isolated test fixture")

    def complete(self, task, *, compatibility=False):
        self.cli("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", "sha256:" + "a" * 64)
        self.cli("review", "receipt", "add", task, "--reviewer", "isolated-fixture", "--kind", "not_required",
                 "--verdict", "not_required", "--summary", "Mechanical fixture only; no product change")
        operation = ("task", "edit", task, "--status", "done") if compatibility else ("task", "complete", task)
        return self.cli(*operation, "--verification-complete", "--review-complete", "--commit-not-required")

    def collect(self, acknowledgements, *, count=8):
        logs = self.root / "logs"
        logs.mkdir(exist_ok=True)
        source = SourceInput(THREAD, logs / "fixture.jsonl", logs, self.root)
        register_source(self.repository, source, CallerIdentity(THREAD))
        rows = [event("session_meta", id=THREAD, cwd=str(self.root), model_provider="openai")]
        for number in range(1, count + 1):
            rows.extend([event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                         event("turn_context", turn_id=turn(number), model="fixture-model")])
            for ack in acknowledgements.get(number, ()):
                rows.append(tool_record(ack, number))
            rows.append(usage(f"r{number}", turn=turn(number)))
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        self.assertNotEqual(collect_registered(self.repository, source)["status"], "unknown")
        return source

    def test_public_completion_late_append_and_index_publication_leave_core_unchanged(self):
        start = self.add()
        task = start["data"]["task"]["task_id"]
        ready = self.cli("task", "edit", task, "--status", "ready")
        resumed = self.cli("task", "edit", task, "--status", "in_progress")
        end = self.cli("task", "edit", task, "--status", "review_pending")
        done = self.complete(task)
        self.assertEqual(done["data"]["usage"]["status"], "pending")
        other = self.add("Still active shared participant")
        self.collect({2: [start], 4: [ready, resumed], 6: [end, other], 7: [done]})
        core = self.target.db_path.read_bytes()
        evidence = file_snapshot(self.target.resolved_evidence_root) if self.target.resolved_evidence_root.exists() else {}
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
        shown = self.cli("task", "show", task)["data"]["usage"]
        self.assertEqual(shown["periods"][0]["models"][0]["total_tokens"], 7 * 120)
        self.assertIn("interval_open", shown["periods"][0]["gaps"])
        self.assertEqual(len(shown["cycle_links"]), 1)
        self.assertIsNotNone(shown["periods"][0]["completion_cycle_id"])
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root) if self.target.resolved_evidence_root.exists() else {}, evidence)
        index = self.target.resolved_usage_index.read_bytes()
        source = SourceInput(THREAD, self.root / "logs/fixture.jsonl", self.root / "logs", self.root)
        with source.path.open("ab") as stream:
            stream.write(json.dumps(usage("late", turn=turn(6))).encode() + b"\n")
        collect_registered(self.repository, source)
        original_publish = service._publish

        def fail_index(path, *args, **kwargs):
            if path == self.target.resolved_usage_index:
                raise UsageError()
            return original_publish(path, *args, **kwargs)

        with mock.patch.object(service, "_publish", side_effect=fail_index):
            self.assertEqual(service.refresh_usage(self.target)["publication"], "pending")
        self.assertEqual(self.target.resolved_usage_index.read_bytes(), index)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
        final = self.cli("task", "show", task, "--audit")["data"]["usage"]
        self.assertEqual(final["periods"][0]["models"][0]["total_tokens"], 8 * 120)
        self.assertEqual(final["periods"][0]["completion_cycle_id"], shown["periods"][0]["completion_cycle_id"])
        index_data = json.loads(self.target.resolved_usage_index.read_bytes())
        for identity in index_data["snapshots"]:
            self.assertTrue((self.target.resolved_usage_snapshots / (identity + ".json")).is_file())
        before = file_snapshot(self.root)
        self.cli("task", "show", task)
        self.cli("task", "show", task, "--audit")
        self.cli("task", "context")
        self.assertEqual(file_snapshot(self.root), before)

    def test_missing_corrupt_and_busy_usage_never_prevent_both_completion_routes(self):
        path = self.target.resolved_usage_database
        initial = path.read_bytes()
        for mode in ("missing", "corrupt", "busy"):
            with self.subTest(mode=mode):
                path.write_bytes(initial)
                started = self.add(mode)
                task = started["data"]["task"]["task_id"]
                if mode == "missing":
                    path.unlink()
                elif mode == "corrupt":
                    path.write_bytes(b"private-invalid-store")
                connection = sqlite3.connect(path) if mode == "busy" else None
                try:
                    if connection:
                        connection.execute("BEGIN EXCLUSIVE")
                    done = self.complete(task, compatibility=mode == "corrupt")
                    self.assertEqual(done["data"]["task"]["status"], "done")
                    self.assertEqual(done["data"]["usage"], {"status": "pending", "coverage": "registered_only"})
                    if mode != "busy":
                        self.assertEqual(self.cli("task", "show", task)["data"]["usage"]["status"], "unknown")
                finally:
                    if connection:
                        connection.close()
                if mode == "missing":
                    self.assertFalse(path.exists())
                elif mode == "corrupt":
                    self.assertEqual(path.read_bytes(), b"private-invalid-store")

    def test_worker_uses_core_readonly_and_numerical_failure_is_sanitized(self):
        with mock.patch.object(service, "repository_for", side_effect=RuntimeError("PRIVATE")):
            result = service.refresh_usage(self.target)
        self.assertEqual(result, {"status": "unknown", "publication": "pending", "diagnostics": ["usage_unavailable"]})
        with closing(connect_initialized_readonly(self.target)) as core:
            with self.assertRaises(sqlite3.OperationalError):
                core.execute("DELETE FROM tasks")
        self.target.db_path.write_bytes(b"bad-core")
        rejected = self.cli("task", "context", ok=False)
        self.assertFalse(rejected["ok"])
