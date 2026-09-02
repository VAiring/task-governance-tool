import ast
import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = (
    ROOT
    / "task-governance-tool"
    / "scripts"
    / "task_governance_tool"
    / "verification_runner.py"
)
GIT_TARGET_PATH = MODEL_PATH.with_name("verification_runner_git.py")
PLAN_PATH = MODEL_PATH.with_name("verification_runner_plan.py")
MANIFEST_PATH = ROOT / "task-governance-tool" / "release-manifest.json"
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.verification_runner import (
        ATTEMPT_DIGEST_DOMAIN,
        OBSERVATION_DIGEST_DOMAIN,
        RESOLUTION_DIGEST_DOMAIN,
        SANDBOX_EVENT_DIGEST_DOMAIN,
        VerificationRunnerModelError,
        generate_runner_id,
        resolution_idempotency_digest,
        runner_observation_source_projection,
        verification_runner_attempt_digest,
        verification_runner_observation_digest,
        verification_runner_sandbox_event_digest,
    )
finally:
    sys.path.pop(0)


POLICY_DIGEST = (
    "sha256:8910c1edfd525be0def6a2c3afb65adab11e5a32e9a60ebbf898c175ffd60fa8"
)
PROJECTION_KEYS = (
    "observation_id",
    "gate_eligibility_version",
    "route",
    "reason",
    "outcome",
    "launch_state",
    "complete_plan",
    "total_step_count",
    "completed_step_count",
    "failed_step_ordinal",
    "started_at",
    "finished_at",
    "duration_ms",
    "cpu_time_ms",
    "peak_job_memory_bytes",
    "total_process_count",
    "plan_blob_object_id",
    "plan_raw_digest",
    "plan_id",
    "plan_version",
    "plan_semantic_digest",
    "runner_implementation_version",
    "runner_implementation_digest",
    "runner_policy_digest",
    "runtime_digest",
    "sanitized_result_digest",
)


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
        "runner_policy_digest": POLICY_DIGEST,
        "sandbox_provider": None,
        "sandbox_policy_digest": None,
        "runtime_digest": None,
        "gate_eligibility_version": 0,
        "trigger": "review_target_set_v1",
        "route": "m21_fallback",
        "reason": "plan_absent",
    }


def attempt_values():
    return {
        "gate_eligibility_version": 0,
        "target_material_digest": "sha256:" + "5" * 64,
        "project_id": "project-1",
        "resolution_id": "tg_verification_runner_resolution_5555555555555555",
        "runner_implementation_digest": "sha256:" + "4" * 64,
        "sandbox_instance_digest": None,
        "target_generation": 3,
        "task_id": "tg_task_0123456789abcdef",
    }


def cleanup_event_values():
    return {
        "attempt_id": "tg_verification_runner_attempt_7777777777777777",
        "event_kind": "attempt_cleanup_succeeded",
        "project_id": "project-1",
        "target_generation": 3,
        "task_id": "tg_task_0123456789abcdef",
        "terminal_observation_id": (
            "tg_verification_runner_observation_6666666666666666"
        ),
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


def projection_observation(resolution):
    return {
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


class RunnerModelTests(unittest.TestCase):
    def assert_sanitized_model_error(self, function, *args, **kwargs):
        with self.assertRaises(VerificationRunnerModelError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, "verification_runner_inconsistent")
        self.assertEqual(
            str(raised.exception),
            "verification runner state is inconsistent",
        )

    def test_resolution_and_observation_seals_use_exact_domains_and_keys(self):
        resolution = resolution_values()
        observation = observation_values()
        self.assertEqual(len(resolution), 34)
        self.assertEqual(len(observation), 21)
        self.assertEqual(
            resolution_idempotency_digest(resolution),
            "sha256:eea69e4cc1528375d3c2c22074a21a5fc9b0d6566a083af88858ab16e0e21f90",
        )
        self.assertEqual(
            resolution_idempotency_digest(resolution),
            expected_digest(RESOLUTION_DIGEST_DOMAIN, resolution),
        )
        self.assertEqual(
            verification_runner_observation_digest(observation),
            "sha256:bc005854205ceb7f97b1ffcc3ba5a7bbb12065f59e481702e8d29ee671a1bfe4",
        )
        self.assertEqual(
            verification_runner_observation_digest(observation),
            expected_digest(OBSERVATION_DIGEST_DOMAIN, observation),
        )
        self.assert_sanitized_model_error(
            resolution_idempotency_digest,
            {**resolution, "argv": ["forbidden"]},
        )
        missing = dict(observation)
        missing.pop("route")
        self.assert_sanitized_model_error(
            verification_runner_observation_digest,
            missing,
        )

        runtime = {**resolution, "runtime_digest": "sha256:" + "9" * 64}
        self.assertEqual(
            resolution_idempotency_digest(runtime),
            expected_digest(RESOLUTION_DIGEST_DOMAIN, runtime),
        )
        for field in ("sandbox_provider", "sandbox_policy_digest"):
            self.assert_sanitized_model_error(
                resolution_idempotency_digest,
                {**resolution, field: "retired"},
            )
        self.assert_sanitized_model_error(
            resolution_idempotency_digest,
            {**resolution, "runtime_digest": "not-a-digest"},
        )

    def test_reference_projection_has_no_child_or_command_bytes(self):
        resolution = resolution_values()
        observation = {
            **projection_observation(resolution),
            "argv": ["forbidden"],
            "stdout": "forbidden",
            "private_path": "forbidden",
        }
        projection = runner_observation_source_projection(
            observation=observation,
            resolution=resolution,
        )
        self.assertEqual(tuple(projection), PROJECTION_KEYS)
        self.assertEqual(len(projection), 26)
        self.assertEqual(
            projection["observation_id"],
            observation["verification_runner_observation_id"],
        )
        self.assertIsNone(projection["runtime_digest"])

        runtime = {**resolution, "runtime_digest": "sha256:" + "9" * 64}
        self.assertEqual(
            runner_observation_source_projection(
                observation=projection_observation(runtime),
                resolution=runtime,
            )["runtime_digest"],
            runtime["runtime_digest"],
        )
        for forbidden in (
            "attempt_id",
            "resolution_id",
            "project_id",
            "task_id",
            "target_generation",
            "argv",
            "command",
            "stdout",
            "stderr",
            "output",
            "exit_code",
            "environment",
            "credentials",
            "private_path",
            "exception",
            "diagnostics",
        ):
            self.assertNotIn(forbidden, projection)

        self.assert_sanitized_model_error(
            runner_observation_source_projection,
            observation={},
            resolution=resolution,
        )

    def test_attempt_and_cleanup_event_seals_are_legacy_stable_and_closed(self):
        attempt = attempt_values()
        event = cleanup_event_values()
        self.assertEqual(len(attempt), 8)
        self.assertEqual(len(event), 6)
        self.assertEqual(
            verification_runner_attempt_digest(attempt),
            "sha256:eed885a387dadb96dc7bf402b654bd356ebb54349217eacefecf0b9387a65e40",
        )
        self.assertEqual(
            verification_runner_attempt_digest(attempt),
            expected_digest(ATTEMPT_DIGEST_DOMAIN, attempt),
        )
        self.assertEqual(
            verification_runner_sandbox_event_digest(event),
            "sha256:a13a3ed52dc74a676e2f82dfd91daaf42bdec17cb8bafeba44992104dcbaecc7",
        )
        self.assertEqual(
            verification_runner_sandbox_event_digest(event),
            expected_digest(SANDBOX_EVENT_DIGEST_DOMAIN, event),
        )
        self.assert_sanitized_model_error(
            verification_runner_attempt_digest,
            {**attempt, "sandbox_instance_digest": "sha256:" + "8" * 64},
        )
        self.assert_sanitized_model_error(
            verification_runner_attempt_digest,
            {key: value for key, value in attempt.items() if key != "target_generation"},
        )
        self.assert_sanitized_model_error(
            verification_runner_sandbox_event_digest,
            {**event, "event_kind": "profile_created"},
        )
        self.assert_sanitized_model_error(
            verification_runner_sandbox_event_digest,
            {**event, "profile_moniker": "retired"},
        )

    def test_runner_ids_require_exact_caller_tokens(self):
        token = "0123456789abcdef"
        expected = {
            "resolution": "tg_verification_runner_resolution_" + token,
            "attempt": "tg_verification_runner_attempt_" + token,
            "sandbox_event": "tg_verification_runner_sandbox_event_" + token,
            "observation": "tg_verification_runner_observation_" + token,
        }
        for kind, identifier in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(generate_runner_id(kind, token), identifier)

        self.assert_sanitized_model_error(generate_runner_id, "resolution")
        for invalid in (
            "0123456789abcde",
            "0123456789abcdef0",
            "0123456789abcdeF",
            "0123456789abcdeg",
            1234567890123456,
        ):
            self.assert_sanitized_model_error(
                generate_runner_id,
                "resolution",
                invalid,
            )
        self.assert_sanitized_model_error(generate_runner_id, "unknown", token)
        self.assert_sanitized_model_error(generate_runner_id, [], token)

    def test_invalid_values_fail_with_sanitized_model_error(self):
        resolution = resolution_values()
        for invalid in (1.0, object(), "\ud800"):
            self.assert_sanitized_model_error(
                resolution_idempotency_digest,
                {**resolution, "reason": invalid},
            )
        recursive = []
        recursive.append(recursive)
        self.assert_sanitized_model_error(
            resolution_idempotency_digest,
            {**resolution, "reason": recursive},
        )
        self.assert_sanitized_model_error(
            verification_runner_observation_digest,
            {**observation_values(), "reason": 1.5},
        )

    def test_model_source_is_pure_and_manifest_bound(self):
        source = MODEL_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODEL_PATH))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        self.assertEqual(
            imports,
            {
                "__future__",
                "hashlib",
                "json",
                "collections.abc",
                "dataclasses",
                "typing",
            },
        )
        self.assertFalse(
            any(name.startswith("task_governance_tool") for name in imports)
        )

        forbidden_calls = {
            "open",
            "input",
            "print",
            "exec",
            "eval",
            "compile",
            "__import__",
        }
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(forbidden_calls.isdisjoint(calls))
        for retired in (
            "import secrets",
            "import random",
            "import uuid",
            "import sqlite3",
            "import subprocess",
            "import socket",
            "windows_appcontainer_profile_v1",
            "appcontainer_no_capabilities_v1",
            "immutable_target_no_child_write_v1",
            "PROC_THREAD_ATTRIBUTE_JOB_LIST",
            "RUNNER_PROVIDER_ID",
            "_LEGACY_RUNNER_POLICY_DIGEST",
            "verification_runner_sandbox_instance_digest",
            "verification_runner_policy_digest",
            "verification_runner_sandbox_policy_digest",
        ):
            self.assertNotIn(retired, source)

        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        core = manifest["core_files"]
        model_name = "scripts/task_governance_tool/verification_runner.py"
        self.assertEqual(manifest["manifest_version"], 1)
        self.assertEqual(manifest["package_name"], "task-governance-tool")
        self.assertEqual(manifest["package_version"], "0.13.0")
        self.assertEqual(
            manifest["release_origin"],
            "github:VAiring/task-governance-tool",
        )
        self.assertEqual(len(core), 63)
        self.assertEqual(
            core["scripts/task_governance_tool/verification_runner_git.py"],
            "sha256:" + hashlib.sha256(GIT_TARGET_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            core["scripts/task_governance_tool/verification_runner_plan.py"],
            "sha256:" + hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            core[model_name],
            "sha256:" + hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
