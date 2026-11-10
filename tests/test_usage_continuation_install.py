"""Physical Hook return followed by an actual independent MCP process lifetime."""

import json
import os
import subprocess
import sys
from time import monotonic, sleep
import unittest

from tests import test_usage_lifecycle_install as lifecycle_support
from tests.m14_test_support import file_snapshot
from task_governance_tool import usage_continuation_state as state
from task_governance_tool.artifact_lock import ArtifactLockError


class ContinuationInstallTests(unittest.TestCase):
    setUp = lifecycle_support.UsageLifecycleInstallTests.setUp
    cli = lifecycle_support.UsageLifecycleInstallTests.cli
    log = lifecycle_support.UsageLifecycleInstallTests.log
    hook = lifecycle_support.UsageLifecycleInstallTests.hook
    add = lifecycle_support.UsageLifecycleInstallTests.add

    def launch(self):
        process = subprocess.Popen([sys.executable, "-I", "-S", "-B",
            str(self.install.skill_root / "scripts/review_wait_server.py"), "--repo", str(self.root),
            "--server", str(self.root / "unused-server.mjs"), "--codex-home", str(self.host), "--timezone", "UTC"],
            cwd=self.root, env={**os.environ, "CODEX_HOME": str(self.host)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.addCleanup(self.stop, process, interrupted=True)
        return process

    def stop(self, process, *, interrupted=False):
        if process.stdin is not None:
            process.stdin.close()
            process.stdin = None
        try:
            output, error = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()  # Only the test-owned isolated service, never a host service.
            output, error = process.communicate(timeout=5)
            self.fail("isolated service did not close")
        self.assertEqual((output, error), (b"", b""))
        # Deliberate shutdown may exhaust the bounded join while an admitted
        # thread is still leaving. The server must not falsely confirm cleanup.
        self.assertIn(process.returncode, (0, 1) if interrupted else (0,))

    def status(self):
        try:
            with state.lease(self.target, "state") as root:
                return state.read(root, self.target)
        except ArtifactLockError:
            return None

    def test_real_entries_resume_after_hook_return_and_service_restart(self):
        self.log({})
        with state.lease(self.target, "execution", create=True):
            self.hook("SessionStart")
            accepted = self.status()
            self.assertIsNotNone(accepted)
            self.assertEqual(accepted["status"], "requested")
            self.assertEqual(self.repository.summary()["models"], [])
            first, duplicate = self.launch(), self.launch()
            sleep(0.5)
            self.stop(first, interrupted=True)
            self.stop(duplicate, interrupted=True)
        # A Core revision after intake must be admitted with no additional Hook.
        self.add("Concurrent Core revision")
        core = self.target.db_path.read_bytes()
        sealed = file_snapshot(self.target.resolved_evidence_root)
        process = self.launch()
        until = monotonic() + 75
        while monotonic() < until:
            self.assertIsNone(process.poll(), "service ended before catch-up")
            sleep(min(1.0, max(0, until - monotonic())))
        # Observe once at the unchanged deadline. Polling this zero-wait lease
        # itself can interrupt the worker's checkpoint/acknowledgment. Dedicated
        # unit tests cover contention; this test covers an independent lifetime.
        result = self.status()
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "caught_up", result)
        self.assertEqual(result["request"], accepted["request"])
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 5)
        index = json.loads(self.target.resolved_usage_index.read_bytes())
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), sealed)
        self.stop(process)
        from task_governance_tool.usage_evidence_service import refresh_usage
        self.assertEqual(refresh_usage(self.target)["publication"], "current")
        self.assertEqual(json.loads(self.target.resolved_usage_index.read_bytes()), index)


if __name__ == "__main__":
    unittest.main()
