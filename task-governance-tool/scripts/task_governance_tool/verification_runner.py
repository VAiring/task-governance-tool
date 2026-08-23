"""Pure, privacy-bounded verification Runner identities and storage seals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


RUNNER_CONTRACT_VERSION = 1
RUNNER_IMPLEMENTATION_VERSION = "taskgov-verification-runner/1"
RUNNER_TRIGGER = "review_target_set_v1"
RUNNER_EXECUTABLE_ID = "taskgov_python"
RUNNER_MAX_OUTPUT_BYTES = 1_048_576

RESOLUTION_DIGEST_DOMAIN = b"taskgov-verification-runner-resolution-v1\0"
ATTEMPT_DIGEST_DOMAIN = b"taskgov-verification-runner-attempt-v1\0"
SANDBOX_EVENT_DIGEST_DOMAIN = b"taskgov-verification-runner-sandbox-event-v1\0"
OBSERVATION_DIGEST_DOMAIN = b"taskgov-verification-runner-observation-v1\0"

_LOWER_HEX = frozenset("0123456789abcdef")

_RESOLUTION_DIGEST_KEYS = frozenset(
    {
        "project_id",
        "task_id",
        "contract_revision",
        "authority_snapshot_id",
        "verification_criterion_id",
        "verification_expectation_digest",
        "verification_criterion_digest",
        "target_kind",
        "target_value",
        "target_base_revision",
        "target_generation",
        "target_capture_version",
        "artifact_manifest_id",
        "target_material_digest",
        "plan_state",
        "plan_blob_object_id",
        "plan_raw_digest",
        "plan_id",
        "plan_version",
        "plan_semantic_digest",
        "selected_entry_digest",
        "coverage",
        "step_count",
        "runner_contract_version",
        "runner_implementation_version",
        "runner_implementation_digest",
        "runner_policy_digest",
        "sandbox_provider",
        "sandbox_policy_digest",
        "runtime_digest",
        "gate_eligibility_version",
        "trigger",
        "route",
        "reason",
    }
)
_ATTEMPT_DIGEST_KEYS = frozenset(
    {
        "gate_eligibility_version",
        "target_material_digest",
        "project_id",
        "resolution_id",
        "runner_implementation_digest",
        "sandbox_instance_digest",
        "target_generation",
        "task_id",
    }
)
_SANDBOX_EVENT_DIGEST_KEYS = frozenset(
    {
        "attempt_id",
        "event_kind",
        "project_id",
        "target_generation",
        "task_id",
        "terminal_observation_id",
    }
)
_OBSERVATION_DIGEST_KEYS = frozenset(
    {
        "attempt_id",
        "completed_step_count",
        "complete_plan",
        "cpu_time_ms",
        "duration_ms",
        "failed_step_ordinal",
        "finished_at",
        "gate_eligibility_version",
        "launch_state",
        "outcome",
        "peak_job_memory_bytes",
        "project_id",
        "reason",
        "resolution_id",
        "runner_implementation_digest",
        "started_at",
        "target_generation",
        "task_id",
        "route",
        "total_process_count",
        "total_step_count",
    }
)
_RUNNER_OBSERVATION_SOURCE_KEYS = (
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


class VerificationRunnerModelError(ValueError):
    code = "verification_runner_inconsistent"

    def __init__(self) -> None:
        super().__init__("verification runner state is inconsistent")


def _validate_json_value(value: Any) -> None:
    if value is None or type(value) in {bool, int}:
        return
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError
            _validate_json_value(key)
            _validate_json_value(item)
        return
    raise TypeError


def _canonical_json_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _domain_digest(domain: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(domain + _canonical_json_bytes(value)).hexdigest()


def _is_labeled_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in _LOWER_HEX for character in value[7:])
    )


def _exact_mapping(values: Mapping[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise VerificationRunnerModelError()
    try:
        copied = dict(values)
        if frozenset(copied) != keys:
            raise VerificationRunnerModelError()
        _canonical_json_bytes(copied)
    except VerificationRunnerModelError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise VerificationRunnerModelError() from exc
    return copied


def resolution_idempotency_digest(values: Mapping[str, Any]) -> str:
    copied = _exact_mapping(values, _RESOLUTION_DIGEST_KEYS)
    if (
        copied["sandbox_provider"] is not None
        or copied["sandbox_policy_digest"] is not None
        or (
            copied["runtime_digest"] is not None
            and not _is_labeled_digest(copied["runtime_digest"])
        )
    ):
        raise VerificationRunnerModelError()
    return _domain_digest(RESOLUTION_DIGEST_DOMAIN, copied)


def verification_runner_attempt_digest(values: Mapping[str, Any]) -> str:
    copied = _exact_mapping(values, _ATTEMPT_DIGEST_KEYS)
    if copied["sandbox_instance_digest"] is not None:
        raise VerificationRunnerModelError()
    return _domain_digest(ATTEMPT_DIGEST_DOMAIN, copied)


def verification_runner_sandbox_event_digest(values: Mapping[str, Any]) -> str:
    copied = _exact_mapping(values, _SANDBOX_EVENT_DIGEST_KEYS)
    if copied["event_kind"] != "attempt_cleanup_succeeded":
        raise VerificationRunnerModelError()
    return _domain_digest(SANDBOX_EVENT_DIGEST_DOMAIN, copied)


def verification_runner_observation_digest(values: Mapping[str, Any]) -> str:
    return _domain_digest(
        OBSERVATION_DIGEST_DOMAIN,
        _exact_mapping(values, _OBSERVATION_DIGEST_KEYS),
    )


def runner_observation_source_projection(
    *,
    observation: Mapping[str, Any],
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact privacy-bounded Reference source projection."""

    if not isinstance(observation, Mapping) or not isinstance(resolution, Mapping):
        raise VerificationRunnerModelError()
    try:
        source = {
            "observation_id": observation["verification_runner_observation_id"],
            "gate_eligibility_version": observation["gate_eligibility_version"],
            "route": observation["route"],
            "reason": observation["reason"],
            "outcome": observation["outcome"],
            "launch_state": observation["launch_state"],
            "complete_plan": observation["complete_plan"],
            "total_step_count": observation["total_step_count"],
            "completed_step_count": observation["completed_step_count"],
            "failed_step_ordinal": observation["failed_step_ordinal"],
            "started_at": observation["started_at"],
            "finished_at": observation["finished_at"],
            "duration_ms": observation["duration_ms"],
            "cpu_time_ms": observation["cpu_time_ms"],
            "peak_job_memory_bytes": observation["peak_job_memory_bytes"],
            "total_process_count": observation["total_process_count"],
            "plan_blob_object_id": resolution["plan_blob_object_id"],
            "plan_raw_digest": resolution["plan_raw_digest"],
            "plan_id": resolution["plan_id"],
            "plan_version": resolution["plan_version"],
            "plan_semantic_digest": resolution["plan_semantic_digest"],
            "runner_implementation_version": resolution[
                "runner_implementation_version"
            ],
            "runner_implementation_digest": observation[
                "runner_implementation_digest"
            ],
            "runner_policy_digest": resolution["runner_policy_digest"],
            "runtime_digest": resolution["runtime_digest"],
            "sanitized_result_digest": observation["sanitized_result_digest"],
        }
        if (
            tuple(source) != _RUNNER_OBSERVATION_SOURCE_KEYS
            or (
                source["runtime_digest"] is not None
                and not _is_labeled_digest(source["runtime_digest"])
            )
        ):
            raise VerificationRunnerModelError()
        _canonical_json_bytes(source)
    except VerificationRunnerModelError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise VerificationRunnerModelError() from exc
    return source


def generate_runner_id(kind: str, caller_token: object = None) -> str:
    prefixes = {
        "resolution": "tg_verification_runner_resolution",
        "attempt": "tg_verification_runner_attempt",
        "sandbox_event": "tg_verification_runner_sandbox_event",
        "observation": "tg_verification_runner_observation",
    }
    if (
        type(kind) is not str
        or kind not in prefixes
        or type(caller_token) is not str
        or len(caller_token) != 16
        or any(character not in _LOWER_HEX for character in caller_token)
    ):
        raise VerificationRunnerModelError()
    return f"{prefixes[kind]}_{caller_token}"
