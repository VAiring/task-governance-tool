import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.verification_runner import (
        OBSERVATION_DIGEST_DOMAIN,
        RESOLUTION_DIGEST_DOMAIN,
        VerificationRunnerModelError,
        resolution_idempotency_digest,
        runner_observation_source_projection,
        verification_runner_observation_digest,
        verification_runner_policy_digest,
    )
finally:
    sys.path.pop(0)


def expected_digest(domain, value):
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(domain + payload).hexdigest()


def resolution_values():
    return {
        "project_id": "project-1",
        "task_id": "tg_task_0123456789abcdef",
        "contract_revision": 2,
        "authority_snapshot_id": "tg_authority_snapshot_1111111111111111",
        "verification_criterion_id": "tg_contract_criterion_2222222222222222",
        "verification_expectation_digest": "a" * 64,
        "verification_criterion_digest": "sha256:" + "b" * 64,
        "target_kind": "git_snapshot",
        "target_value": "sha256:" + "c" * 64,
        "target_base_revision": "d" * 40,
        "target_generation": 3,
        "target_capture_version": 1,
        "artifact_manifest_id": "tg_artifact_manifest_3333333333333333",
        "target_material_digest": None,
        "plan_state": "absent",
        "plan_blob_object_id": None,
        "plan_raw_digest": None,
        "plan_id": None,
        "plan_version": None,
        "plan_semantic_digest": None,
        "selected_entry_digest": None,
        "coverage": "not_applicable",
        "step_count": 0,
        "runner_contract_version": 1,
        "runner_implementation_version": "taskgov-verification-runner/1",
        "runner_implementation_digest": "sha256:" + "4" * 64,
        "runner_policy_digest": verification_runner_policy_digest(),
        "sandbox_provider": None,
        "sandbox_policy_digest": None,
        "runtime_digest": None,
        "gate_eligibility_version": 0,
        "trigger": "review_target_set_v1",
        "route": "m21_fallback",
        "reason": "plan_absent",
    }


def observation_values():
    return {
        "attempt_id": None,
        "completed_step_count": 0,
        "complete_plan": 0,
        "cpu_time_ms": None,
        "duration_ms": 0,
        "failed_step_ordinal": None,
        "finished_at": "2026-01-01T00:00:00Z",
        "gate_eligibility_version": 0,
        "launch_state": "no_launch",
        "outcome": "not_run",
        "peak_job_memory_bytes": None,
        "project_id": "project-1",
        "reason": "plan_absent",
        "resolution_id": "tg_verification_runner_resolution_5555555555555555",
        "runner_implementation_digest": "sha256:" + "4" * 64,
        "started_at": "2026-01-01T00:00:00Z",
        "target_generation": 3,
        "task_id": "tg_task_0123456789abcdef",
        "route": "m21_fallback",
        "total_process_count": None,
        "total_step_count": 0,
    }


class RunnerModelTests(unittest.TestCase):
    def test_resolution_and_observation_seals_use_exact_domains_and_keys(self):
        resolution = resolution_values()
        observation = observation_values()
        self.assertEqual(
            resolution_idempotency_digest(resolution),
            expected_digest(RESOLUTION_DIGEST_DOMAIN, resolution),
        )
        self.assertEqual(
            verification_runner_observation_digest(observation),
            expected_digest(OBSERVATION_DIGEST_DOMAIN, observation),
        )
        with self.assertRaises(VerificationRunnerModelError):
            resolution_idempotency_digest({**resolution, "argv": ["forbidden"]})

    def test_fixed_policy_digests_are_stable_and_distinct(self):
        runner = verification_runner_policy_digest()
        self.assertRegex(runner, r"^sha256:[0-9a-f]{64}$")

    def test_reference_projection_has_no_child_or_command_bytes(self):
        resolution = resolution_values()
        observation = {
            "verification_runner_observation_id": (
                "tg_verification_runner_observation_6666666666666666"
            ),
            "verification_runner_attempt_id": None,
            "verification_runner_resolution_id": (
                "tg_verification_runner_resolution_5555555555555555"
            ),
            "project_id": resolution["project_id"],
            "task_id": resolution["task_id"],
            "target_generation": resolution["target_generation"],
            "gate_eligibility_version": 0,
            "route": "m21_fallback",
            "reason": "plan_absent",
            "outcome": "not_run",
            "launch_state": "no_launch",
            "complete_plan": 0,
            "total_step_count": 0,
            "completed_step_count": 0,
            "failed_step_ordinal": None,
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:00Z",
            "duration_ms": 0,
            "cpu_time_ms": None,
            "peak_job_memory_bytes": None,
            "total_process_count": None,
            "runner_implementation_digest": resolution[
                "runner_implementation_digest"
            ],
            "sanitized_result_digest": verification_runner_observation_digest(
                observation_values()
            ),
        }
        projection = runner_observation_source_projection(
            observation=observation,
            resolution=resolution,
        )
        self.assertEqual(
            projection["observation_id"],
            observation["verification_runner_observation_id"],
        )
        for forbidden in (
            "attempt_id",
            "resolution_id",
            "project_id",
            "task_id",
            "target_generation",
            "argv",
            "stdout",
            "stderr",
            "exit_code",
            "environment",
        ):
            self.assertNotIn(forbidden, projection)


if __name__ == "__main__":
    unittest.main()
