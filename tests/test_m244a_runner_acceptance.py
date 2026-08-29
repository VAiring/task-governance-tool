from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import fields, replace
from pathlib import Path
from unittest import mock

from tests.m14_test_support import _copy_skill, run_taskgov_internal
from tests.test_m242_r3b_schema20_activation import (
    _start_schema20_runtime_oracle,
    _stop_schema20_runtime_oracle,
)
from tests.test_m242_runner_model import PROJECTION_KEYS
from tests.test_m242_runner_service import (
    FakeFixedExecutableLease,
    RunnerServiceFixture,
    git,
    row_counts,
)
from tests import test_m242_runner_storage as _runner_storage

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import storage  # noqa: E402
from task_governance_tool import verification_runner_service as service  # noqa: E402
from task_governance_tool.verification_runner import (  # noqa: E402
    RUNNER_CONTRACT_VERSION, runner_observation_source_projection,
)
from task_governance_tool.verification_runner_lifecycle import (  # noqa: E402
    inspect_runner_layout,
)
from task_governance_tool.verification_runner_process import (  # noqa: E402
    RunnerProcessResultV1,
)


class M244ARunnerAcceptanceTests(unittest.TestCase):
    def test_clean_repository_without_plan_uses_public_m21_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            fixture = RunnerServiceFixture(temporary_root)
            skill_root = _copy_skill(fixture.repo / ".agents" / "skills")
            plan_path = skill_root / "config" / "verification-runner.json"
            self.assertFalse(plan_path.exists())
            git(fixture.repo, "add", ".agents/skills/task-governance-tool")
            git(fixture.repo, "commit", "--quiet", "-m", "clean target")
            self.assertEqual(git(fixture.repo, "status", "--porcelain").stdout, b"")
            installed_target = replace(fixture.target, skill_root=skill_root)
            with mock.patch(
                "tests.m14_test_support.resolve_database_target",
                return_value=installed_target,
            ), mock.patch.object(
                service,
                "capture_verification_runner_plan",
                wraps=service.capture_verification_runner_plan,
            ) as capture_plan:
                result = run_taskgov_internal(
                    "review", "target", "set", "--repo", str(fixture.repo), "--db",
                    str(fixture.db), fixture.task_id, "--kind", "git_snapshot", "--json",
                    maintenance_enabled=False,
                )
            self.assertEqual(result.returncode, 0, result.stdout)
            capture_plan.assert_called_once_with(fixture.repo, skill_root)
            data = json.loads(result.stdout)["data"]
            self.assertEqual((data["verification_route"], data["blocking_code"]),
                             ("receipt_required", None))
            self.assertEqual(sum(row_counts(fixture.db).values()), 0)

    def test_schema20_terminal_pass_remains_gate_ineligible(self):
        with tempfile.TemporaryDirectory() as temporary:
            _start_schema20_runtime_oracle()
            try:
                helper = _runner_storage.RunnerStorageTests()
                target, basis, resolution, attempt = helper._pending_graph(
                    Path(temporary), seed="m244a-shadow", commit_character="a",
                    token="1" * 16,
                )
                observation, event = helper._terminal_values(
                    resolution, attempt, token="2" * 16)
                _source, reference, link = helper._terminal_evidence(
                    resolution, observation,
                    acceptance_criterion_id=basis["acceptance_criterion_id"],
                    token="3" * 16,
                )
                with closing(storage.connect_initialized(target)) as connection:
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        storage.persist_verification_runner_terminal_locked(
                            connection, observation=observation,
                            evidence_reference=reference, criterion_link=link,
                            cleanup_event=event,
                        )
            finally:
                _stop_schema20_runtime_oracle()
            storage.rehearse_schema21_storage(target.db_path)
            with closing(storage.connect_initialized_readonly(target)) as connection:
                task = service.read_internal_task(
                    connection,
                    target.project.project_id,
                    resolution.task_id,
                )
                graph = storage.read_verification_runner_generation_locked(
                    connection, project_id=resolution.project_id,
                    task_id=resolution.task_id,
                    target_generation=resolution.target_generation,
                )
            self.assertEqual(
                (graph["observation"].outcome,
                 graph["resolution"].gate_eligibility_version,
                 graph["attempt"].gate_eligibility_version,
                 graph["observation"].gate_eligibility_version), ("pass", 0, 0, 0))
            self.assertEqual(task["review_target_runner_basis_version"], 0)
            self.assertIsNone(service.select_current_verification_runner_basis(
                target, task=task))

    def test_process_create_failure_is_terminal_and_parent_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            owner = FakeFixedExecutableLease()

            def process_create_failed(request):
                return RunnerProcessResultV1(
                    RUNNER_CONTRACT_VERSION, request.attempt_id,
                    "blocked_prelaunch", "process_create_failed", "no_launch",
                    None, 1, None, None, None, True, True, True, (),
                )

            with mock.patch.object(service, "_prepare_runner", return_value=prepared), \
                 mock.patch.object(service, "_basis_is_current", return_value=True), \
                 mock.patch.object(service, "_physical_basis_matches", return_value=True), \
                 mock.patch.object(service, "RunnerFixedExecutableLease",
                                   side_effect=owner.factory), \
                 mock.patch.object(service, "run_process_request",
                                   side_effect=process_create_failed):
                routed = service.set_review_target_with_shadow_runner(
                    fixture.target, fixture.task_id, kind="git_snapshot")
            observation = fixture.generation(1)["observation"]
            self.assertEqual((observation.outcome, observation.reason,
                              observation.launch_state),
                             ("blocked_prelaunch", "process_create_failed", "no_launch"))
            self.assertEqual((routed.verification_route, routed.blocking_code),
                             ("blocked", "verification_receipt_blocking"))

    def test_real_windows_job_output_cap_cleanup_and_complete_retention_deny(self):
        # This is intentionally non-SKIP: another OS cannot manufacture PASS.
        self.assertEqual(os.name, "nt", "TG-M24.4A requires a real Windows Job")
        raw_out = "M244A_RAW_STDOUT_92a1"
        raw_err = "M244A_RAW_STDERR_51bc"
        argv_secret = "M244A_ARGV_03de"
        credential = "M244A_CREDENTIAL_787f"
        exception_body = "M244A_EXCEPTION_BODY_a641"

        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            source = "\n".join(
                (
                    "import os, subprocess, sys, traceback",
                    "assert 'M244A_PARENT_CREDENTIAL' not in os.environ",
                    f"assert sys.argv[1] == {argv_secret!r}",
                    "child = subprocess.Popen([sys.executable, '-I', '-c', "
                    "'import time; time.sleep(30)'])",
                    f"print({raw_err!r}, file=sys.stderr, flush=True)",
                    f"try: raise RuntimeError({exception_body!r})",
                    "except RuntimeError: traceback.print_exc()",
                    f"sys.stdout.write({raw_out!r} + 'X' * 2000000)",
                    "sys.stdout.flush(); child.wait()",
                )
            ) + "\n"
            (fixture.repo / "focused.py").write_text(source, encoding="utf-8")
            git(fixture.repo, "add", "focused.py")
            prepared = fixture.prepared()
            step = replace(prepared.plan.steps[0], argv=(argv_secret,))
            prepared = replace(prepared, plan=replace(prepared.plan, steps=(step,)))

            captured = []
            real_process = service.run_process_request

            def run_and_capture(request):
                result = real_process(request)
                captured.append((request, result))
                return result

            with mock.patch.dict(
                os.environ, {"M244A_PARENT_CREDENTIAL": credential}
            ), mock.patch.object(
                service, "_prepare_runner", return_value=prepared
            ), mock.patch.object(
                service, "_basis_is_current", return_value=True
            ), mock.patch.object(
                service, "_physical_basis_matches", return_value=True
            ), mock.patch.object(
                service, "run_process_request", side_effect=run_and_capture
            ):
                routed = service.set_review_target_with_shadow_runner(
                    fixture.target, fixture.task_id, kind="git_snapshot"
                )

            self.assertEqual(len(captured), 1)
            request, process = captured[0]
            self.assertEqual(
                (
                    process.outcome,
                    process.reason,
                    process.process_zero,
                    process.handles_closed,
                    process.raw_output_discarded,
                ),
                ("output_rejected", "output_limit", True, True, True),
            )
            self.assertEqual(
                (routed.verification_route, routed.blocking_code),
                ("blocked", "verification_receipt_blocking"),
            )

            graph = fixture.generation(1)
            inventory = inspect_runner_layout(service._runner_paths(fixture.target))
            self.assertEqual(
                (graph["state"], inventory.attempt_ids, inventory.quarantine_ids),
                ("terminal", (), ()),
            )
            self.assertFalse(request.materialized_root.exists())
            self.assertFalse(request.scratch_root.exists())
            projection = runner_observation_source_projection(
                observation=graph["observation"].__dict__,
                resolution=graph["resolution"].__dict__,
            )
            self.assertEqual(tuple(projection), PROJECTION_KEYS)
            self.assertEqual(
                tuple(field.name for field in fields(RunnerProcessResultV1)),
                (
                    "version", "attempt_id", "outcome", "reason", "launch_state",
                    "failed_step_ordinal", "duration_ms", "cpu_time_ms",
                    "peak_job_memory_bytes", "total_process_count", "process_zero",
                    "handles_closed", "raw_output_discarded", "steps",
                ),
            )
            forbidden_keys = {
                "stdout", "stderr", "raw_output", "command", "argv",
                "environment", "credential", "credentials", "absolute_path",
                "private_path", "exit_code", "exception", "exception_body",
                "stack_trace", "raw_plan", "raw_target", "debug",
            }
            self.assertTrue(forbidden_keys.isdisjoint(projection))

            denied = (
                raw_out, raw_err, argv_secret, credential, exception_body,
                str(request.materialized_root),
                str(request.scratch_root),
            )
            public = json.dumps(
                {"task": routed.task, "runner_observation": projection}
            ).encode("utf-8")
            retained = [
                path.read_bytes()
                for path in fixture.db.parent.rglob("*")
                if path.is_file()
            ]
            for value in denied:
                public_forms = {
                    value.encode("utf-8"),
                    json.dumps(value)[1:-1].encode("utf-8"),
                }
                for encoded in public_forms:
                    self.assertNotIn(encoded, public)
                for payload in retained:
                    for encoded in public_forms | {value.encode("utf-16le")}:
                        self.assertNotIn(encoded, payload)
