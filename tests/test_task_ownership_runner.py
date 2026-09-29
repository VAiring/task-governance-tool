from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from tests.test_task_ownership import X, Y
from tests.test_m242_r3b_schema20_activation import _SCHEMA20_RUNTIME_PATCH_TARGETS
from tests.test_m242_runner_service import RunnerServiceFixture, passing_process_result, row_counts
from tests.m14_test_support import run_taskgov_internal
from task_governance_tool import verification_runner_service as service
from task_governance_tool.task_values import TaskValidationError


class TaskOwnershipRunnerTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target in _SCHEMA20_RUNTIME_PATCH_TARGETS:
            stack.enter_context(mock.patch(target, 24))
        stack.enter_context(mock.patch.dict(os.environ, {"CODEX_THREAD_ID": X.session_id}))
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.fixture = RunnerServiceFixture(root)
        self.edit("in_progress", X)

    def edit(self, status, caller, *extra):
        fixture = self.fixture
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": caller.session_id}):
            result = run_taskgov_internal("task", "edit", fixture.task_id, "--status", status,
                                          *extra, "--repo", str(fixture.repo), "--db", str(fixture.db),
                                          "--json", maintenance_enabled=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        return json.loads(result.stdout)

    def test_owner_check_precedes_runner_restart_side_effects_and_t1(self):
        fixture = self.fixture
        prepared = fixture.prepared()
        with mock.patch.object(service, "_prepare_runner", return_value=prepared), mock.patch.object(service, "zero_wait_runner_lock") as lock:
            with self.assertRaises(TaskValidationError) as error:
                service.set_review_target_with_optional_runner(fixture.target, fixture.task_id, kind="git_snapshot", caller=Y)
            self.assertEqual(error.exception.code, "task_not_owned")
            lock.assert_not_called()
        self.assertEqual(sum(row_counts(fixture.db).values()), 0)
        self.edit("paused", Y, "--pause-reason", "Explicit recovery after preflight")
        self.edit("in_progress", Y)
        with self.assertRaises(TaskValidationError) as stale:
            service._persist_launch_intent(fixture.target, prepared, X)
        self.assertEqual(stale.exception.code, "task_ownership_changed")
        self.assertEqual(sum(row_counts(fixture.db).values()), 0)

    def test_admitted_attempt_finishes_cleanup_and_audit_after_owner_changes(self):
        fixture = self.fixture
        prepared = fixture.prepared()
        intent = service._persist_launch_intent(fixture.target, prepared, X)
        paths = service._runner_paths(fixture.target)
        def process(request):
            self.edit("paused", Y, "--pause-reason", "Explicit takeover during admitted attempt")
            self.edit("in_progress", Y)
            return passing_process_result(request)
        with service.zero_wait_runner_lock(paths), mock.patch.object(service, "_physical_basis_matches", return_value=True), \
                mock.patch.object(service, "observe_fixed_package_runtime", return_value=Path(__file__).resolve()), \
                mock.patch.object(service, "build_clean_environment", return_value=()), \
                mock.patch.object(service, "run_process_request", side_effect=process):
            result = service._run_intent_under_lock(fixture.target, paths, prepared, intent, cancel_requested=lambda: False)
        self.assertEqual(result.verification_route, "runner_pass")
        self.assertFalse((paths.attempts / intent.attempt.verification_runner_attempt_id).exists())
        saved = fixture.generation(1)
        self.assertIsNotNone(saved["observation"])
        self.assertIsNotNone(saved["cleanup_event"])
        with self.assertRaises(TaskValidationError) as old_owner:
            service.set_review_target_with_optional_runner(fixture.target, fixture.task_id, kind="diff_fingerprint",
                                                           revision="sha256:" + "b" * 64, caller=X)
        self.assertEqual(old_owner.exception.code, "task_not_owned")
        self.assertEqual(row_counts(fixture.db)["verification_runner_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
