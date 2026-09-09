"""Stored Runner graph validation and atomic record operations.

The caller supplies the connection and owns transactions, process/lifecycle
work, and cleanup acceptance. Shared admission and Evidence/Bundle assembly
remain storage-owned; the focused Runner storage and gate tests cover this
repository boundary.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.verification_runner import (
    RUNNER_PLAN_VERSIONS,
    RUNNER_POLICY_DIGESTS,
    runner_accounting_valid,
)

VERIFICATION_RUNNER_RESOLUTION_ID_PATTERN = re.compile(
    r"^tg_verification_runner_resolution_[0-9a-f]{16}$"
)


VERIFICATION_RUNNER_ATTEMPT_ID_PATTERN = re.compile(
    r"^tg_verification_runner_attempt_[0-9a-f]{16}$"
)


VERIFICATION_RUNNER_SANDBOX_EVENT_ID_PATTERN = re.compile(
    r"^tg_verification_runner_sandbox_event_[0-9a-f]{16}$"
)


VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN = re.compile(
    r"^tg_verification_runner_observation_[0-9a-f]{16}$"
)


VERIFICATION_RUNNER_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class VerificationRunnerResolution:
    verification_runner_resolution_id: str
    project_id: str
    task_id: str
    contract_revision: int
    authority_snapshot_id: str
    verification_criterion_id: str
    verification_expectation_digest: str
    verification_criterion_digest: str
    target_kind: str
    target_value: str
    target_base_revision: str | None
    target_generation: int
    target_capture_version: int
    artifact_manifest_id: str
    target_material_digest: str | None
    plan_state: str
    plan_blob_object_id: str | None
    plan_raw_digest: str | None
    plan_id: str | None
    plan_version: int | None
    plan_semantic_digest: str | None
    selected_entry_digest: str | None
    coverage: str
    step_count: int
    runner_contract_version: int
    runner_implementation_version: str
    runner_implementation_digest: str
    runner_policy_digest: str
    runtime_digest: str | None
    gate_eligibility_version: int
    trigger: str
    route: str
    reason: str | None
    idempotency_digest: str
    created_at: str


@dataclass(frozen=True)
class VerificationRunnerAttempt:
    verification_runner_attempt_id: str
    project_id: str
    task_id: str
    target_generation: int
    gate_eligibility_version: int
    verification_runner_resolution_id: str
    target_material_digest: str
    runner_implementation_digest: str
    attempt_digest: str
    intent_recorded_at: str


@dataclass(frozen=True)
class VerificationRunnerObservation:
    verification_runner_observation_id: str
    project_id: str
    task_id: str
    target_generation: int
    gate_eligibility_version: int
    verification_runner_resolution_id: str
    verification_runner_attempt_id: str | None
    runner_implementation_digest: str
    route: str
    launch_state: str
    outcome: str
    reason: str | None
    complete_plan: int
    total_step_count: int
    completed_step_count: int
    failed_step_ordinal: int | None
    started_at: str
    finished_at: str
    duration_ms: int
    cpu_time_ms: int | None
    peak_job_memory_bytes: int | None
    total_process_count: int | None
    sanitized_result_digest: str
    created_at: str


@dataclass(frozen=True)
class VerificationRunnerSandboxEvent:
    verification_runner_sandbox_event_id: str
    project_id: str
    task_id: str
    target_generation: int
    verification_runner_attempt_id: str
    event_kind: str
    event_digest: str
    terminal_observation_id: str | None
    created_at: str


def _verification_runner_resolution_digest_projection(row: Any) -> dict[str, Any]:
    from task_governance_tool.storage import (
        _unreadable_project_state,
    )

    fields = (
        "project_id", "task_id", "contract_revision",
        "authority_snapshot_id", "verification_criterion_id",
        "verification_expectation_digest", "verification_criterion_digest",
        "target_kind", "target_value", "target_base_revision",
        "target_generation", "target_capture_version", "artifact_manifest_id",
        "target_material_digest", "plan_state", "plan_blob_object_id",
        "plan_raw_digest", "plan_id", "plan_version",
        "plan_semantic_digest", "selected_entry_digest", "coverage",
        "step_count", "runner_contract_version",
        "runner_implementation_version", "runner_implementation_digest",
        "runner_policy_digest", "runtime_digest", "gate_eligibility_version",
        "trigger", "route", "reason",
    )
    try:
        projection = {field: row[field] for field in fields}
    except (KeyError, IndexError, TypeError) as exc:
        raise _unreadable_project_state() from exc
    projection["sandbox_provider"] = None
    projection["sandbox_policy_digest"] = None
    return projection


def _verification_runner_attempt_digest_projection(row: Any) -> dict[str, Any]:
    from task_governance_tool.storage import (
        _unreadable_project_state,
    )

    try:
        return {
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "target_generation": row["target_generation"],
            "gate_eligibility_version": row["gate_eligibility_version"],
            "target_material_digest": row["target_material_digest"],
            "resolution_id": row["verification_runner_resolution_id"],
            "runner_implementation_digest": row[
                "runner_implementation_digest"
            ],
            "sandbox_instance_digest": None,
        }
    except (KeyError, IndexError, TypeError) as exc:
        raise _unreadable_project_state() from exc


_RUNNER_RESULT_PAIRINGS = frozenset(
    {
        ("blocked_prelaunch", "runtime_unavailable", "no_launch"),
        ("blocked_prelaunch", "process_setup_failed", "no_launch"),
        ("blocked_prelaunch", "process_boundary_unproved", "no_launch"),
        ("blocked_prelaunch", "process_create_failed", "no_launch"),
        ("blocked_prelaunch", "cancelled", "no_launch"),
        ("blocked_prelaunch", "controller_interrupted", "no_launch"),
        ("pass", None, "launched"),
        ("fail", "step_nonzero", "launched"),
        ("timeout", "timeout", "launched"),
        ("cancelled", "cancelled", "launched"),
        ("resource_exceeded", "cpu_limit", "launched"),
        ("resource_exceeded", "memory_limit", "launched"),
        ("output_rejected", "output_limit", "launched"),
        ("process_error", "runtime_unavailable", "launched"),
        ("process_error", "process_setup_failed", "launched"),
        ("process_error", "process_boundary_unproved", "launched"),
        ("process_error", "process_create_failed", "launched"),
        ("process_error", "process_resume_failed", "launched"),
        ("process_error", "process_wait_failed", "launched"),
        ("process_error", "pipe_drain_failed", "launched"),
        ("process_error", "process_tree_unproved", "launched"),
        ("controller_interrupted", "controller_interrupted", "no_launch"),
        ("controller_interrupted", "controller_interrupted", "launched"),
    }
)


def _runner_row_value(row: Any, value_type: type[Any]) -> Any:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    fields = tuple(value_type.__dataclass_fields__)
    try:
        if set(row.keys()) != set(fields):
            raise evidence_ledger_inconsistent()
        return value_type(**{field: row[field] for field in fields})
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise evidence_ledger_inconsistent() from exc


def _runner_resolution_value(row: Any) -> VerificationRunnerResolution:
    return _runner_row_value(row, VerificationRunnerResolution)


def _runner_attempt_value(row: Any) -> VerificationRunnerAttempt:
    return _runner_row_value(row, VerificationRunnerAttempt)


def _runner_observation_value(row: Any) -> VerificationRunnerObservation:
    return _runner_row_value(row, VerificationRunnerObservation)


def _runner_sandbox_event_value(row: Any) -> VerificationRunnerSandboxEvent:
    return _runner_row_value(row, VerificationRunnerSandboxEvent)


def _validate_runner_timestamp(value: object, *, field_name: str) -> str:
    from task_governance_tool.storage import (
        StorageError,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    if type(value) is not str:
        raise evidence_ledger_inconsistent()
    try:
        return validate_utc_timestamp(value, field=field_name)
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc


def _validated_verification_runner_graph(
    connection: sqlite3.Connection,
    *,
    selected_generation: tuple[str, str, int] | None = None,
    selected_task: sqlite3.Row | None = None,
    selected_history_cycle: _storage.CompletionCycle | None = None,
) -> tuple[
    tuple[_storage._ExpectedEvidenceReference, ...],
    dict[tuple[str, str, int], dict[str, Any]],
]:
    """Validate all Runner rows or one exact selected generation."""

    from task_governance_tool.evidence_repository import (
        _validate_artifact_manifest_storage,
        _validate_stored_evidence_reference_row,
        _validated_authority_context,
        validate_manifest_evidence_references,
    )
    from task_governance_tool.storage import (
        CRITERION_EVIDENCE_LINK_ID_PATTERN,
        EVIDENCE_REFERENCE_ID_PATTERN,
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        SHA256_DIGEST_PATTERN,
        StorageError,
        _ExpectedEvidenceReference,
        current_schema_version,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
    )

    from task_governance_tool.evidence_ledger import (
        EvidenceLedgerError,
        EvidenceSource,
        TargetCaptureBinding,
    )
    from task_governance_tool.verification_runner import (
        RUNNER_CONTRACT_VERSION,
        RUNNER_IMPLEMENTATION_VERSION,
        RUNNER_POLICY_DIGEST,
        RUNNER_TRIGGER,
        VerificationRunnerModelError,
        resolution_idempotency_digest,
        runner_observation_source_projection,
        verification_runner_attempt_digest,
        verification_runner_observation_digest,
        verification_runner_sandbox_event_digest,
    )

    physical_schema_version = current_schema_version(connection)
    if physical_schema_version not in {
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
    }:
        return (), {}
    allowed_eligibility_versions = (
        {0, 1}
        if physical_schema_version in {
            PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
        }
        else {0}
    )
    if selected_generation is not None:
        project_id, task_id, target_generation = selected_generation
        if (
            physical_schema_version not in {
                PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
            }
            or type(project_id) is not str
            or not project_id
            or type(task_id) is not str
            or not task_id
            or type(target_generation) is not int
            or target_generation < 1
            or (selected_task is None) == (selected_history_cycle is None)
        ):
            raise evidence_ledger_inconsistent()
        if selected_task is not None:
            if (
                selected_task["project_id"] != project_id
                or selected_task["task_id"] != task_id
                or selected_task["review_target_generation"] != target_generation
            ):
                raise evidence_ledger_inconsistent()
        else:
            assert selected_history_cycle is not None
            if (
                selected_history_cycle.project_id != project_id
                or selected_history_cycle.task_id != task_id
                or selected_history_cycle.review_target_generation
                != target_generation
                or selected_history_cycle.origin != "native_done"
                or selected_history_cycle.evidence_basis_version != 1
                or selected_history_cycle.verification_basis_kind
                not in {"caller_attestation", "runner_observation"}
            ):
                raise evidence_ledger_inconsistent()
        generation_predicate = (
            " WHERE project_id = ? AND task_id = ? AND target_generation = ? "
        )
        generation_parameters: tuple[object, ...] = selected_generation
        generation_limit = " LIMIT 2"
    else:
        if selected_task is not None or selected_history_cycle is not None:
            raise evidence_ledger_inconsistent()
        generation_predicate = " "
        generation_parameters = ()
        generation_limit = ""
    try:
        resolution_rows = connection.execute(
            "SELECT * FROM verification_runner_resolutions "
            + generation_predicate
            + "ORDER BY project_id, task_id, target_generation, "
            "verification_runner_resolution_id"
            + generation_limit,
            generation_parameters,
        ).fetchall()
        attempt_rows = connection.execute(
            "SELECT * FROM verification_runner_attempts "
            + generation_predicate
            + "ORDER BY project_id, task_id, target_generation, "
            "verification_runner_attempt_id"
            + generation_limit,
            generation_parameters,
        ).fetchall()
        observation_rows = connection.execute(
            "SELECT * FROM verification_runner_observations "
            + generation_predicate
            + "ORDER BY project_id, task_id, target_generation, "
            "verification_runner_observation_id"
            + generation_limit,
            generation_parameters,
        ).fetchall()
        event_rows = connection.execute(
            "SELECT * FROM verification_runner_sandbox_events "
            + generation_predicate
            + "ORDER BY project_id, task_id, target_generation, "
            "verification_runner_sandbox_event_id"
            + generation_limit,
            generation_parameters,
        ).fetchall()
        if selected_generation is None:
            snapshots = {
                str(row["authority_snapshot_id"]): row
                for row in connection.execute(
                    "SELECT * FROM authority_snapshots "
                    "ORDER BY authority_snapshot_id"
                ).fetchall()
            }
            criteria = {
                str(row["criterion_id"]): row
                for row in connection.execute(
                    "SELECT * FROM contract_criteria ORDER BY criterion_id"
                ).fetchall()
            }
            manifests = {
                str(row["artifact_manifest_id"]): row
                for row in connection.execute(
                    "SELECT * FROM artifact_manifests "
                    "ORDER BY artifact_manifest_id"
                ).fetchall()
            }
            tasks = {
                (str(row["project_id"]), str(row["task_id"])): row
                for row in connection.execute(
                    "SELECT project_id, task_id, review_target_generation, "
                    "review_target_runner_basis_version FROM tasks "
                    "ORDER BY project_id, task_id"
                ).fetchall()
            }
        else:
            snapshot_ids: set[str] = set()
            manifest_ids: set[str] = set()
            for row in resolution_rows:
                snapshot_id = row["authority_snapshot_id"]
                manifest_id = row["artifact_manifest_id"]
                if (
                    type(snapshot_id) is not str
                    or not snapshot_id
                    or type(manifest_id) is not str
                    or not manifest_id
                ):
                    raise evidence_ledger_inconsistent()
                snapshot_ids.add(snapshot_id)
                manifest_ids.add(manifest_id)
            authority = _validated_authority_context(
                connection,
                snapshot_ids=snapshot_ids,
            )
            manifest_records, manifests_by_target = (
                _validate_artifact_manifest_storage(
                    connection,
                    snapshots=authority.snapshots,
                    links=authority.links,
                    manifest_ids=manifest_ids,
                )
            )
            validate_manifest_evidence_references(
                connection,
                manifests=manifest_records,
                selected_project_id=selected_generation[0],
            )
            snapshots = authority.snapshots
            criteria = authority.criteria
            manifests = {
                manifest_id: record.row
                for manifest_id, record in manifest_records.items()
            }
            if selected_task is not None:
                selected_task_basis = selected_task
                tasks = {
                    (selected_generation[0], selected_generation[1]): (
                        selected_task_basis
                    )
                }
            else:
                assert selected_history_cycle is not None
                tasks = {}

        resolutions: dict[
            tuple[str, str, int], tuple[VerificationRunnerResolution, sqlite3.Row]
        ] = {}
        for row in resolution_rows:
            value = _runner_resolution_value(row)
            key = (value.project_id, value.task_id, value.target_generation)
            snapshot = snapshots.get(value.authority_snapshot_id)
            criterion = criteria.get(value.verification_criterion_id)
            manifest = manifests.get(value.artifact_manifest_id)
            target_base = value.target_base_revision or ""
            if (
                key in resolutions
                or VERIFICATION_RUNNER_RESOLUTION_ID_PATTERN.fullmatch(
                    value.verification_runner_resolution_id
                )
                is None
                or type(value.contract_revision) is not int
                or value.contract_revision < 1
                or type(value.target_generation) is not int
                or value.target_generation < 1
                or value.target_capture_version != 1
                or value.target_kind not in {"git_snapshot", "git_commit"}
                or type(value.target_value) is not str
                or not value.target_value
                or (
                    value.target_base_revision is not None
                    and (
                        type(value.target_base_revision) is not str
                        or not value.target_base_revision
                    )
                )
                or value.target_material_digest is None
                or SHA256_DIGEST_PATTERN.fullmatch(value.target_material_digest)
                is None
                or value.plan_state != "runner"
                or value.plan_blob_object_id is not None
                or type(value.plan_raw_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(value.plan_raw_digest) is None
                or type(value.plan_id) is not str
                or VERIFICATION_RUNNER_IDENTIFIER_PATTERN.fullmatch(value.plan_id)
                is None
                or type(value.plan_version) is not int
                or value.plan_version not in RUNNER_PLAN_VERSIONS
                or type(value.plan_semantic_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(value.plan_semantic_digest)
                is None
                or type(value.selected_entry_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(value.selected_entry_digest)
                is None
                or value.coverage != "full"
                or type(value.step_count) is not int
                or not 1 <= value.step_count <= 16
                or value.runner_contract_version != RUNNER_CONTRACT_VERSION
                or value.runner_implementation_version
                != RUNNER_IMPLEMENTATION_VERSION
                or type(value.runner_implementation_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(
                    value.runner_implementation_digest
                )
                is None
                or value.runner_policy_digest not in RUNNER_POLICY_DIGESTS
                or (
                    value.gate_eligibility_version == 0
                    and value.runner_policy_digest != RUNNER_POLICY_DIGEST
                )
                or value.runtime_digest is not None
                or value.gate_eligibility_version
                not in allowed_eligibility_versions
                or value.trigger != RUNNER_TRIGGER
                or value.route != "runner"
                or value.reason is not None
                or type(value.idempotency_digest) is not str
                or value.idempotency_digest
                != resolution_idempotency_digest(
                    _verification_runner_resolution_digest_projection(row)
                )
                or snapshot is None
                or snapshot["project_id"] != value.project_id
                or snapshot["task_id"] != value.task_id
                or snapshot["contract_revision"] != value.contract_revision
                or snapshot["verification_digest"]
                != value.verification_expectation_digest
                or criterion is None
                or criterion["project_id"] != value.project_id
                or criterion["task_id"] != value.task_id
                or criterion["criterion_kind"] != "verification"
                or criterion["digest"] != value.verification_criterion_digest
                or manifest is None
                or manifest["project_id"] != value.project_id
                or manifest["task_id"] != value.task_id
                or manifest["state"] != "complete_git"
                or manifest["authority_snapshot_id"] != value.authority_snapshot_id
                or manifest["verification_criterion_id"]
                != value.verification_criterion_id
                or manifest["target_kind"] != value.target_kind
                or manifest["target_value"] != value.target_value
                or manifest["target_base_revision"] != target_base
                or manifest["target_generation"] != value.target_generation
            ):
                raise evidence_ledger_inconsistent()
            _validate_runner_timestamp(
                value.created_at,
                field_name="verification Runner resolution creation time",
            )
            resolutions[key] = (value, row)

        attempts: dict[
            tuple[str, str, int], tuple[VerificationRunnerAttempt, sqlite3.Row]
        ] = {}
        for row in attempt_rows:
            value = _runner_attempt_value(row)
            key = (value.project_id, value.task_id, value.target_generation)
            parent = resolutions.get(key)
            resolution = parent[0] if parent is not None else None
            if (
                key in attempts
                or VERIFICATION_RUNNER_ATTEMPT_ID_PATTERN.fullmatch(
                    value.verification_runner_attempt_id
                )
                is None
                or resolution is None
                or value.gate_eligibility_version
                != resolution.gate_eligibility_version
                or value.verification_runner_resolution_id
                != resolution.verification_runner_resolution_id
                or value.target_material_digest
                != resolution.target_material_digest
                or value.runner_implementation_digest
                != resolution.runner_implementation_digest
                or value.attempt_digest
                != verification_runner_attempt_digest(
                    _verification_runner_attempt_digest_projection(row)
                )
            ):
                raise evidence_ledger_inconsistent()
            recorded_at = _validate_runner_timestamp(
                value.intent_recorded_at,
                field_name="verification Runner attempt intent time",
            )
            if recorded_at < resolution.created_at:
                raise evidence_ledger_inconsistent()
            attempts[key] = (value, row)
        if set(attempts) != set(resolutions):
            raise evidence_ledger_inconsistent()

        observations: dict[
            tuple[str, str, int], tuple[VerificationRunnerObservation, sqlite3.Row]
        ] = {}
        for row in observation_rows:
            value = _runner_observation_value(row)
            key = (value.project_id, value.task_id, value.target_generation)
            parent_resolution = resolutions.get(key)
            parent_attempt = attempts.get(key)
            resolution = (
                parent_resolution[0] if parent_resolution is not None else None
            )
            attempt = parent_attempt[0] if parent_attempt is not None else None
            pairing = (value.outcome, value.reason, value.launch_state)
            expected_route = (
                "runner" if value.launch_state == "launched" else "m21_fallback"
            )
            accounting = (
                value.cpu_time_ms,
                value.peak_job_memory_bytes,
                value.total_process_count,
            )
            expected_complete = int(
                value.outcome == "pass"
                and value.reason is None
                and value.launch_state == "launched"
                and resolution is not None
                and value.completed_step_count
                == value.total_step_count
                == resolution.step_count
            )
            if (
                key in observations
                or VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN.fullmatch(
                    value.verification_runner_observation_id
                )
                is None
                or resolution is None
                or attempt is None
                or value.gate_eligibility_version
                != resolution.gate_eligibility_version
                or value.verification_runner_resolution_id
                != resolution.verification_runner_resolution_id
                or value.verification_runner_attempt_id
                != attempt.verification_runner_attempt_id
                or value.runner_implementation_digest
                != resolution.runner_implementation_digest
                or pairing not in _RUNNER_RESULT_PAIRINGS
                or value.route != expected_route
                or value.complete_plan != expected_complete
                or value.total_step_count != resolution.step_count
                or type(value.completed_step_count) is not int
                or not 0 <= value.completed_step_count <= value.total_step_count
                or (
                    value.failed_step_ordinal is not None
                    and (
                        type(value.failed_step_ordinal) is not int
                        or not 1
                        <= value.failed_step_ordinal
                        <= value.total_step_count
                    )
                )
                or type(value.duration_ms) is not int
                or value.duration_ms < 0
                or not runner_accounting_valid(
                    resolution.runner_policy_digest,
                    outcome=value.outcome,
                    launch_state=value.launch_state,
                    cpu_time_ms=value.cpu_time_ms,
                    peak_job_memory_bytes=value.peak_job_memory_bytes,
                    total_process_count=value.total_process_count,
                )
                or (
                    value.launch_state == "no_launch"
                    and (
                        value.completed_step_count != 0
                        or value.failed_step_ordinal is not None
                        or any(item is not None for item in accounting)
                    )
                )
                or (
                    value.outcome == "pass"
                    and value.failed_step_ordinal is not None
                )
                or value.sanitized_result_digest
                != verification_runner_observation_digest(
                    {
                        "attempt_id": value.verification_runner_attempt_id,
                        "completed_step_count": value.completed_step_count,
                        "complete_plan": value.complete_plan,
                        "cpu_time_ms": value.cpu_time_ms,
                        "duration_ms": value.duration_ms,
                        "failed_step_ordinal": value.failed_step_ordinal,
                        "finished_at": value.finished_at,
                        "gate_eligibility_version": value.gate_eligibility_version,
                        "launch_state": value.launch_state,
                        "outcome": value.outcome,
                        "peak_job_memory_bytes": value.peak_job_memory_bytes,
                        "project_id": value.project_id,
                        "reason": value.reason,
                        "resolution_id": value.verification_runner_resolution_id,
                        "runner_implementation_digest": (
                            value.runner_implementation_digest
                        ),
                        "started_at": value.started_at,
                        "target_generation": value.target_generation,
                        "task_id": value.task_id,
                        "route": value.route,
                        "total_process_count": value.total_process_count,
                        "total_step_count": value.total_step_count,
                    }
                )
            ):
                raise evidence_ledger_inconsistent()
            started = _validate_runner_timestamp(
                value.started_at,
                field_name="verification Runner observation start time",
            )
            finished = _validate_runner_timestamp(
                value.finished_at,
                field_name="verification Runner observation finish time",
            )
            created = _validate_runner_timestamp(
                value.created_at,
                field_name="verification Runner observation creation time",
            )
            if (
                started < attempt.intent_recorded_at
                or started > finished
                or created != finished
            ):
                raise evidence_ledger_inconsistent()
            observations[key] = (value, row)

        events: dict[
            tuple[str, str, int], tuple[VerificationRunnerSandboxEvent, sqlite3.Row]
        ] = {}
        for row in event_rows:
            value = _runner_sandbox_event_value(row)
            key = (value.project_id, value.task_id, value.target_generation)
            attempt_record = attempts.get(key)
            observation_record = observations.get(key)
            attempt = attempt_record[0] if attempt_record is not None else None
            observation = (
                observation_record[0] if observation_record is not None else None
            )
            expected_terminal = (
                observation.verification_runner_observation_id
                if observation is not None
                else None
            )
            if (
                key in events
                or VERIFICATION_RUNNER_SANDBOX_EVENT_ID_PATTERN.fullmatch(
                    value.verification_runner_sandbox_event_id
                )
                is None
                or attempt is None
                or value.verification_runner_attempt_id
                != attempt.verification_runner_attempt_id
                or value.event_kind != "attempt_cleanup_succeeded"
                or value.terminal_observation_id != expected_terminal
                or value.event_digest
                != verification_runner_sandbox_event_digest(
                    {
                        "attempt_id": value.verification_runner_attempt_id,
                        "event_kind": value.event_kind,
                        "project_id": value.project_id,
                        "target_generation": value.target_generation,
                        "task_id": value.task_id,
                        "terminal_observation_id": value.terminal_observation_id,
                    }
                )
            ):
                raise evidence_ledger_inconsistent()
            event_time = _validate_runner_timestamp(
                value.created_at,
                field_name="verification Runner cleanup event time",
            )
            event_floor = (
                observation.finished_at
                if observation is not None
                else attempt.intent_recorded_at
            )
            if event_time < event_floor:
                raise evidence_ledger_inconsistent()
            events[key] = (value, row)
        if not set(observations).issubset(events):
            raise evidence_ledger_inconsistent()

        pending_by_project: dict[str, int] = {}
        for key, (attempt, _) in attempts.items():
            if key not in events:
                pending_by_project[attempt.project_id] = (
                    pending_by_project.get(attempt.project_id, 0) + 1
                )
        if any(count > 1 for count in pending_by_project.values()):
            raise evidence_ledger_inconsistent()

        for (project_id, task_id), task in tasks.items():
            generation = task["review_target_generation"]
            marker = task["review_target_runner_basis_version"]
            if (
                type(generation) is not int
                or generation < 0
                or type(marker) is not int
                or marker not in {0, 2}
            ):
                raise evidence_ledger_inconsistent()
            current = resolutions.get((project_id, task_id, generation))
            if marker == 2:
                if (
                    physical_schema_version not in {
                        PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
                    }
                    or current is None
                    or current[0].gate_eligibility_version != 1
                ):
                    raise evidence_ledger_inconsistent()
            elif current is not None and current[0].gate_eligibility_version != 0:
                raise evidence_ledger_inconsistent()

            if marker == 2 and current is not None:
                resolution = current[0]
                exact_receipt = connection.execute(
                    "SELECT 1 FROM verification_receipts "
                    "WHERE project_id = ? AND task_id = ? "
                    "AND contract_revision = ? "
                    "AND verification_expectation_digest = ? "
                    "AND verification_subject_basis_version = 1 "
                    "AND subject_authority_snapshot_id = ? "
                    "AND subject_verification_criterion_id = ? "
                    "AND target_kind = ? AND target_value = ? "
                    "AND target_base_revision = ? AND target_generation = ? "
                    "LIMIT 1",
                    (
                        project_id,
                        task_id,
                        resolution.contract_revision,
                        resolution.verification_expectation_digest,
                        resolution.authority_snapshot_id,
                        resolution.verification_criterion_id,
                        resolution.target_kind,
                        resolution.target_value,
                        resolution.target_base_revision or "",
                        resolution.target_generation,
                    ),
                ).fetchone()
                if exact_receipt is not None:
                    attempt = attempts.get((project_id, task_id, generation))
                    observation = observations.get((project_id, task_id, generation))
                    cleanup = events.get((project_id, task_id, generation))
                    if (
                        attempt is None
                        or observation is None
                        or cleanup is None
                        or cleanup[0].terminal_observation_id
                        != observation[0].verification_runner_observation_id
                        or observation[0].route != "m21_fallback"
                        or observation[0].launch_state != "no_launch"
                        or observation[0].outcome != "blocked_prelaunch"
                        or observation[0].reason
                        not in {"runtime_unavailable", "process_setup_failed"}
                        or observation[0].complete_plan != 0
                    ):
                        raise evidence_ledger_inconsistent()

        if selected_generation is None:
            runner_references = connection.execute(
                "SELECT * FROM evidence_references "
                "WHERE source_kind = 'runner_observation' "
                "ORDER BY source_id, evidence_reference_id"
            ).fetchall()
        else:
            runner_references = connection.execute(
                "SELECT * FROM evidence_references "
                "WHERE project_id = ? AND task_id = ? "
                "AND target_generation = ? "
                "AND source_kind = 'runner_observation' "
                "ORDER BY source_id, evidence_reference_id LIMIT 2",
                selected_generation,
            ).fetchall()
        references_by_source: dict[str, sqlite3.Row] = {}
        runner_reference_keys: list[list[str]] = []
        for row in runner_references:
            source_id = row["source_id"]
            reference_id = row["evidence_reference_id"]
            project_id = row["project_id"]
            task_id = row["task_id"]
            if (
                type(source_id) is not str
                or source_id in references_by_source
                or type(reference_id) is not str
                or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(reference_id) is None
                or type(project_id) is not str
                or not project_id
                or type(task_id) is not str
                or not task_id
            ):
                raise evidence_ledger_inconsistent()
            references_by_source[source_id] = row
            runner_reference_keys.append([project_id, task_id, reference_id])

        runner_links: tuple[sqlite3.Row, ...] | list[sqlite3.Row]
        if runner_reference_keys:
            selected_references_json = json.dumps(
                runner_reference_keys,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            runner_links = connection.execute(
                """
                WITH selected_references(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT link.*
                  FROM selected_references AS selected
                 CROSS JOIN criterion_evidence_links AS link
                       INDEXED BY idx_criterion_evidence_links_reference
                 WHERE link.project_id = json_extract(selected.value, '$[0]')
                   AND link.task_id = json_extract(selected.value, '$[1]')
                   AND link.evidence_reference_id =
                         json_extract(selected.value, '$[2]')
                 ORDER BY link.evidence_reference_id,
                          link.criterion_evidence_link_id
                 LIMIT ?
                """,
                (selected_references_json, len(runner_reference_keys) + 1),
            ).fetchall()
        else:
            runner_links = ()
        links_by_reference: dict[str, list[sqlite3.Row]] = {}
        for row in runner_links:
            reference_id = row["evidence_reference_id"]
            if type(reference_id) is not str:
                raise evidence_ledger_inconsistent()
            links_by_reference.setdefault(reference_id, []).append(row)

        expected_references: list[_ExpectedEvidenceReference] = []
        observed_reference_ids: set[str] = set()
        observed_link_ids: set[str] = set()
        for key, (observation, observation_row) in observations.items():
            resolution, resolution_row = resolutions[key]
            manifest = manifests[resolution.artifact_manifest_id]
            reference = references_by_source.get(
                observation.verification_runner_observation_id
            )
            if reference is None:
                raise evidence_ledger_inconsistent()
            reference_id = reference["evidence_reference_id"]
            links = links_by_reference.get(str(reference_id), [])
            link = links[0] if len(links) == 1 else None
            target_base = resolution.target_base_revision or ""
            source = EvidenceSource(
                source_kind="runner_observation",
                source_state="recorded",
                source_id=observation.verification_runner_observation_id,
                source_projection=runner_observation_source_projection(
                    observation=dict(observation_row),
                    resolution=dict(resolution_row),
                ),
                _validated_runner_eligibility_version=(
                    observation.gate_eligibility_version
                    if physical_schema_version in {
                        PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
                    }
                    else 0
                ),
            )
            binding = TargetCaptureBinding(
                target_kind=resolution.target_kind,
                target_value=resolution.target_value,
                target_base_revision=target_base,
                target_generation=resolution.target_generation,
                authority_snapshot_id=resolution.authority_snapshot_id,
                acceptance_criterion_id=manifest["acceptance_criterion_id"],
                verification_criterion_id=resolution.verification_criterion_id,
            )
            expected_reference = _ExpectedEvidenceReference(
                source=source,
                project_id=resolution.project_id,
                task_id=resolution.task_id,
                contract_revision=resolution.contract_revision,
                binding=binding,
            )
            reference_seen: set[tuple[str, str]] = set()
            _validate_stored_evidence_reference_row(
                reference,
                expected={
                    (source.source_kind, source.source_id): expected_reference
                },
                seen=reference_seen,
            )
            link_id = (
                link["criterion_evidence_link_id"] if link is not None else None
            )
            if (
                type(reference_id) is not str
                or reference_id in observed_reference_ids
                or reference["created_at"] != observation.created_at
                or link is None
                or type(link_id) is not str
                or CRITERION_EVIDENCE_LINK_ID_PATTERN.fullmatch(link_id) is None
                or link_id in observed_link_ids
                or link["project_id"] != resolution.project_id
                or link["task_id"] != resolution.task_id
                or link["criterion_id"] != resolution.verification_criterion_id
                or link["evidence_reference_id"] != reference_id
                or link["relation"] != "runner_observation"
                or link["assurance_class"] != "machine_observed"
                or link["producer_class"] != "verification_runner"
                or link["producer_version"] != 1
                or link["created_at"] != observation.created_at
            ):
                raise evidence_ledger_inconsistent()
            expected_references.append(expected_reference)
            observed_reference_ids.add(reference_id)
            observed_link_ids.add(link_id)
        if (
            set(references_by_source)
            != {
                item.source.source_id for item in expected_references
            }
            or set(links_by_reference) != observed_reference_ids
        ):
            raise evidence_ledger_inconsistent()
        if (
            observed_reference_ids
            and physical_schema_version == PRIVATE_SCHEMA20_VERSION
        ):
            placeholders = ", ".join("?" for _ in observed_reference_ids)
            ordered_reference_ids = tuple(sorted(observed_reference_ids))
            if connection.execute(
                "SELECT 1 FROM completion_bundle_members "
                f"WHERE evidence_reference_id IN ({placeholders}) LIMIT 1",
                ordered_reference_ids,
            ).fetchone() is not None:
                raise evidence_ledger_inconsistent()
            ordered_link_ids = tuple(
                sorted(observed_link_ids)
            )
            link_placeholders = ", ".join("?" for _ in ordered_link_ids)
            if connection.execute(
                "SELECT 1 FROM completion_bundle_members "
                f"WHERE criterion_evidence_link_id IN ({link_placeholders}) "
                "LIMIT 1",
                ordered_link_ids,
            ).fetchone() is not None:
                raise evidence_ledger_inconsistent()
        generations: dict[tuple[str, str, int], dict[str, Any]] = {}
        for key, (resolution, _) in resolutions.items():
            attempt = attempts[key][0]
            observation_record = observations.get(key)
            cleanup_record = events.get(key)
            observation = (
                observation_record[0] if observation_record is not None else None
            )
            cleanup = cleanup_record[0] if cleanup_record is not None else None
            state = (
                "terminal"
                if observation is not None
                else "restart_cleaned"
                if cleanup is not None
                else "pending"
            )
            generations[key] = {
                "state": state,
                "resolution": resolution,
                "attempt": attempt,
                "observation": observation,
                "cleanup_event": cleanup,
            }
        return tuple(expected_references), generations
    except (
        EvidenceLedgerError,
        VerificationRunnerModelError,
        StorageError,
        sqlite3.Error,
    ) as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def _validated_verification_runner_references(
    connection: sqlite3.Connection,
) -> tuple[_storage._ExpectedEvidenceReference, ...]:
    references, _ = _validated_verification_runner_graph(connection)
    return references


def _verification_runner_value_dict(
    value: object,
    value_type: type[Any],
) -> dict[str, Any]:
    from task_governance_tool.storage import (
        _prepared_row,
        evidence_ledger_inconsistent,
    )

    if not isinstance(value, value_type):
        raise evidence_ledger_inconsistent()
    fields = set(value_type.__dataclass_fields__)
    return _prepared_row(value, fields)


def read_current_verification_runner_target_basis(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Read the exact current target/authority basis used by Runner T1."""

    from task_governance_tool.storage import (
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        SHA256_DIGEST_PATTERN,
        _read_validated_current_task_row,
        _verification_expectation_digest,
        current_schema_version,
        evidence_ledger_inconsistent,
    )


    schema_version = current_schema_version(connection)
    if (
        schema_version not in {
            PRIVATE_SCHEMA20_VERSION,
            PRIVATE_SCHEMA21_VERSION,
            PRIVATE_SCHEMA22_VERSION,
        }
        or type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
    ):
        raise evidence_ledger_inconsistent()
    task = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    snapshot_id = task["review_target_authority_snapshot_id"]
    acceptance_id = task["review_target_acceptance_criterion_id"]
    verification_id = task["review_target_verification_criterion_id"]
    manifest_id = task["review_target_artifact_manifest_id"]
    snapshot = connection.execute(
        "SELECT * FROM authority_snapshots "
        "WHERE project_id = ? AND task_id = ? AND authority_snapshot_id = ?",
        (project_id, task_id, snapshot_id),
    ).fetchone()
    criterion = connection.execute(
        "SELECT * FROM contract_criteria "
        "WHERE project_id = ? AND task_id = ? AND criterion_id = ?",
        (project_id, task_id, verification_id),
    ).fetchone()
    manifest = connection.execute(
        "SELECT * FROM artifact_manifests "
        "WHERE project_id = ? AND task_id = ? AND artifact_manifest_id = ?",
        (project_id, task_id, manifest_id),
    ).fetchone()
    contract_revision = task["current_contract_revision"]
    target_kind = task["review_target_kind"]
    target_value = task["review_target_value"]
    target_base_revision = task["review_target_base_revision"]
    target_generation = task["review_target_generation"]
    target_capture_version = task["review_target_capture_version"]
    if (
        type(contract_revision) is not int
        or contract_revision < 1
        or type(snapshot_id) is not str
        or snapshot is None
        or snapshot["contract_revision"] != contract_revision
        or snapshot["verification_digest"]
        != _verification_expectation_digest(str(task["verification"]))
        or (
            acceptance_id is not None
            and type(acceptance_id) is not str
        )
        or type(verification_id) is not str
        or criterion is None
        or criterion["criterion_kind"] != "verification"
        or type(criterion["digest"]) is not str
        or SHA256_DIGEST_PATTERN.fullmatch(criterion["digest"]) is None
        or type(manifest_id) is not str
        or manifest is None
        or manifest["state"] != "complete_git"
        or manifest["authority_snapshot_id"] != snapshot_id
        or manifest["acceptance_criterion_id"] != acceptance_id
        or manifest["verification_criterion_id"] != verification_id
        or target_kind not in {"git_snapshot", "git_commit"}
        or type(target_value) is not str
        or not target_value
        or type(target_base_revision) is not str
        or type(target_generation) is not int
        or target_generation < 1
        or target_capture_version != 1
        or type(task["review_target_runner_basis_version"]) is not int
        or task["review_target_runner_basis_version"] not in {0, 2}
        or (
            task["review_target_runner_basis_version"] == 2
            and schema_version not in {
                PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
            }
        )
        or manifest["target_kind"] != target_kind
        or manifest["target_value"] != target_value
        or manifest["target_base_revision"] != target_base_revision
        or manifest["target_generation"] != target_generation
    ):
        raise evidence_ledger_inconsistent()
    return {
        "contract_revision": contract_revision,
        "authority_snapshot_id": snapshot_id,
        "acceptance_criterion_id": acceptance_id,
        "verification_criterion_id": verification_id,
        "verification_expectation_digest": snapshot["verification_digest"],
        "verification_criterion_digest": criterion["digest"],
        "target_kind": target_kind,
        "target_value": target_value,
        "target_base_revision": target_base_revision,
        "target_generation": target_generation,
        "target_capture_version": target_capture_version,
        "artifact_manifest_id": manifest_id,
        "gate_eligibility_version": (
            1 if task["review_target_runner_basis_version"] == 2 else 0
        ),
    }


def _read_verification_runner_generation_unvalidated(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    target_generation: int,
) -> dict[str, Any] | None:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    specs = (
        (
            "resolution",
            "verification_runner_resolutions",
            VerificationRunnerResolution,
        ),
        (
            "attempt",
            "verification_runner_attempts",
            VerificationRunnerAttempt,
        ),
        (
            "observation",
            "verification_runner_observations",
            VerificationRunnerObservation,
        ),
        (
            "cleanup_event",
            "verification_runner_sandbox_events",
            VerificationRunnerSandboxEvent,
        ),
    )
    values: dict[str, Any] = {}
    for name, table_name, value_type in specs:
        rows = connection.execute(
            f"SELECT * FROM {table_name} "
            "WHERE project_id = ? AND task_id = ? AND target_generation = ? "
            "ORDER BY rowid LIMIT 2",
            (project_id, task_id, target_generation),
        ).fetchall()
        if len(rows) > 1:
            raise evidence_ledger_inconsistent()
        values[name] = (
            _runner_row_value(rows[0], value_type) if rows else None
        )
    if all(value is None for value in values.values()):
        return None
    if values["resolution"] is None or values["attempt"] is None:
        raise evidence_ledger_inconsistent()
    if values["observation"] is not None:
        state = "terminal"
        if values["cleanup_event"] is None:
            raise evidence_ledger_inconsistent()
    elif values["cleanup_event"] is not None:
        state = "restart_cleaned"
    else:
        state = "pending"
    return {"state": state, **values}


def read_verification_runner_generation_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    target_generation: int,
) -> dict[str, Any] | None:
    """Read one fully validated generation; the name denotes DB serialization."""

    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )


    if (
        type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(target_generation) is not int
        or target_generation < 1
    ):
        raise evidence_ledger_inconsistent()
    _validated_verification_runner_references(connection)
    return _read_verification_runner_generation_unvalidated(
        connection,
        project_id=project_id,
        task_id=task_id,
        target_generation=target_generation,
    )


def _verification_runner_gate_storage_token(
    *,
    marker: int,
    basis_matches: bool,
    generation: dict[str, Any],
    reference: dict[str, Any] | None,
    criterion_link: dict[str, Any] | None,
) -> tuple[object, ...]:
    resolution = generation["resolution"]
    attempt = generation["attempt"]
    observation = generation["observation"]
    cleanup = generation["cleanup_event"]
    return (
        marker,
        generation["state"],
        basis_matches,
        resolution.verification_runner_resolution_id,
        resolution.idempotency_digest,
        attempt.verification_runner_attempt_id,
        attempt.attempt_digest,
        (
            observation.verification_runner_observation_id
            if observation is not None
            else None
        ),
        observation.sanitized_result_digest if observation is not None else None,
        (
            cleanup.verification_runner_sandbox_event_id
            if cleanup is not None
            else None
        ),
        cleanup.event_digest if cleanup is not None else None,
        reference["evidence_reference_id"] if reference is not None else None,
        reference["digest"] if reference is not None else None,
        (
            criterion_link["criterion_evidence_link_id"]
            if criterion_link is not None
            else None
        ),
        criterion_link["relation"] if criterion_link is not None else None,
    )


def _read_current_verification_runner_gate_snapshot(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Return validated current Runner graph facts without selecting an outcome."""

    from task_governance_tool.storage import (
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        _read_validated_current_task_row,
        current_schema_version,
        evidence_ledger_inconsistent,
    )


    if (
        current_schema_version(connection) not in {
            PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
        }
        or type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
    ):
        raise evidence_ledger_inconsistent()
    task = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    marker = task["review_target_runner_basis_version"]
    target_generation = task["review_target_generation"]
    if marker != 2 or type(target_generation) is not int or target_generation < 1:
        raise evidence_ledger_inconsistent()
    generation_key = (project_id, task_id, target_generation)
    _references, generations = _validated_verification_runner_graph(
        connection,
        selected_generation=generation_key,
        selected_task=task,
    )
    generation = generations.get(generation_key)
    if generation is None:
        raise evidence_ledger_inconsistent()
    resolution = generation["resolution"]
    manifest = connection.execute(
        "SELECT acceptance_criterion_id FROM artifact_manifests "
        "WHERE project_id = ? AND task_id = ? AND artifact_manifest_id = ?",
        (project_id, task_id, resolution.artifact_manifest_id),
    ).fetchone()
    if manifest is None:
        raise evidence_ledger_inconsistent()
    current_basis = read_current_verification_runner_target_basis(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    expected_basis = _verification_runner_current_basis_for_resolution(
        resolution,
        acceptance_criterion_id=manifest["acceptance_criterion_id"],
    )
    basis_matches = current_basis == expected_basis

    observation = generation["observation"]
    reference: dict[str, Any] | None = None
    criterion_link: dict[str, Any] | None = None
    if observation is not None:
        reference_rows = connection.execute(
            "SELECT * FROM evidence_references "
            "WHERE project_id = ? AND task_id = ? "
            "AND source_kind = 'runner_observation' AND source_id = ? "
            "ORDER BY evidence_reference_id LIMIT 2",
            (
                project_id,
                task_id,
                observation.verification_runner_observation_id,
            ),
        ).fetchall()
        if len(reference_rows) != 1:
            raise evidence_ledger_inconsistent()
        reference = dict(reference_rows[0])
        link_rows = connection.execute(
            "SELECT * FROM criterion_evidence_links "
            "WHERE project_id = ? AND task_id = ? "
            "AND relation = 'runner_observation' "
            "AND evidence_reference_id = ? "
            "ORDER BY criterion_evidence_link_id LIMIT 2",
            (project_id, task_id, reference["evidence_reference_id"]),
        ).fetchall()
        if len(link_rows) != 1:
            raise evidence_ledger_inconsistent()
        criterion_link = dict(link_rows[0])

    storage_token = _verification_runner_gate_storage_token(
        marker=marker,
        basis_matches=basis_matches,
        generation=generation,
        reference=reference,
        criterion_link=criterion_link,
    )
    return {
        "marker": marker,
        "target_generation": target_generation,
        "basis_matches": basis_matches,
        "state": generation["state"],
        "resolution": resolution,
        "attempt": generation["attempt"],
        "observation": observation,
        "cleanup_event": generation["cleanup_event"],
        "reference": reference,
        "criterion_link": criterion_link,
        "storage_token": storage_token,
    }


def read_current_verification_runner_gate_snapshot(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Return one exact graph or the established unreadable-state failure."""

    from task_governance_tool.storage import (
        StorageError,
        _unreadable_project_state,
    )


    try:
        return _read_current_verification_runner_gate_snapshot(
            connection,
            project_id=project_id,
            task_id=task_id,
        )
    except StorageError as exc:
        if exc.code in {"database_busy", "project_state_unreadable"}:
            raise
        raise _unreadable_project_state() from exc


def require_current_verification_runner_selection(
    connection: sqlite3.Connection,
    *,
    selection: Any,
) -> dict[str, Any]:
    """Revalidate the DB half of one service-owned selection."""

    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
        verification_runner_state_invalid,
    )

    from task_governance_tool.verification_runner import (
        VerificationRunnerGateSelection,
    )

    if not isinstance(selection, VerificationRunnerGateSelection):
        raise evidence_ledger_inconsistent()
    snapshot = read_current_verification_runner_gate_snapshot(
        connection,
        project_id=selection.project_id,
        task_id=selection.task_id,
    )
    observation = snapshot["observation"]
    observation_id = (
        observation.verification_runner_observation_id
        if observation is not None
        else None
    )
    if (
        not snapshot["basis_matches"]
        or snapshot["target_generation"] != selection.target_generation
        or snapshot["storage_token"] != selection.storage_token
        or observation_id != selection.verification_runner_observation_id
    ):
        raise verification_runner_state_invalid()
    return snapshot


def read_pending_verification_runner_cleanup(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> tuple[dict[str, Any], ...]:
    """Return only unresolved attempt intents requiring proved cleanup."""

    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )


    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    _validated_verification_runner_references(connection)
    rows = connection.execute(
        "SELECT resolution.task_id, resolution.target_generation "
        "FROM verification_runner_resolutions AS resolution "
        "LEFT JOIN verification_runner_observations AS observation "
        "ON observation.project_id = resolution.project_id "
        "AND observation.task_id = resolution.task_id "
        "AND observation.target_generation = resolution.target_generation "
        "LEFT JOIN verification_runner_sandbox_events AS cleanup "
        "ON cleanup.project_id = resolution.project_id "
        "AND cleanup.task_id = resolution.task_id "
        "AND cleanup.target_generation = resolution.target_generation "
        "WHERE resolution.project_id = ? "
        "AND resolution.gate_eligibility_version IN (0, 1) "
        "AND observation.verification_runner_observation_id IS NULL "
        "AND cleanup.verification_runner_sandbox_event_id IS NULL "
        "ORDER BY resolution.task_id, resolution.target_generation",
        (project_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = _read_verification_runner_generation_unvalidated(
            connection,
            project_id=project_id,
            task_id=str(row["task_id"]),
            target_generation=int(row["target_generation"]),
        )
        if item is None or item["state"] != "pending":
            raise evidence_ledger_inconsistent()
        result.append(item)
    return tuple(result)


def has_pending_verification_runner_cleanup(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> bool:
    return bool(
        read_pending_verification_runner_cleanup(
            connection,
            project_id=project_id,
        )
    )


def _verification_runner_current_basis_for_resolution(
    resolution: VerificationRunnerResolution,
    *,
    acceptance_criterion_id: str | None,
) -> dict[str, Any]:
    return {
        "contract_revision": resolution.contract_revision,
        "authority_snapshot_id": resolution.authority_snapshot_id,
        "acceptance_criterion_id": acceptance_criterion_id,
        "verification_criterion_id": resolution.verification_criterion_id,
        "verification_expectation_digest": (
            resolution.verification_expectation_digest
        ),
        "verification_criterion_digest": resolution.verification_criterion_digest,
        "target_kind": resolution.target_kind,
        "target_value": resolution.target_value,
        "target_base_revision": resolution.target_base_revision or "",
        "target_generation": resolution.target_generation,
        "target_capture_version": resolution.target_capture_version,
        "artifact_manifest_id": resolution.artifact_manifest_id,
        "gate_eligibility_version": resolution.gate_eligibility_version,
    }


def _verification_runner_current_basis_matches(
    connection: sqlite3.Connection,
    *,
    resolution: VerificationRunnerResolution,
    acceptance_criterion_id: str | None,
) -> bool:
    """Return false for a valid reset/drift and validate a matching basis fully."""

    from task_governance_tool.storage import (
        _read_validated_current_task_row,
        evidence_ledger_inconsistent,
    )


    task = _read_validated_current_task_row(
        connection,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    expected = _verification_runner_current_basis_for_resolution(
        resolution,
        acceptance_criterion_id=acceptance_criterion_id,
    )
    task_projection = {
        "contract_revision": task["current_contract_revision"],
        "authority_snapshot_id": task[
            "review_target_authority_snapshot_id"
        ],
        "acceptance_criterion_id": task[
            "review_target_acceptance_criterion_id"
        ],
        "verification_criterion_id": task[
            "review_target_verification_criterion_id"
        ],
        "target_kind": task["review_target_kind"],
        "target_value": task["review_target_value"],
        "target_base_revision": task["review_target_base_revision"],
        "target_generation": task["review_target_generation"],
        "target_capture_version": task["review_target_capture_version"],
        "artifact_manifest_id": task["review_target_artifact_manifest_id"],
    }
    if any(task_projection[key] != expected[key] for key in task_projection):
        return False
    return read_current_verification_runner_target_basis(
        connection,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
    ) == expected


def insert_verification_runner_resolution_locked(
    connection: sqlite3.Connection,
    *,
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
) -> bool:
    """Atomically append the resolution and launch intent inside caller T1."""

    from task_governance_tool.storage import (
        _require_evidence_writer,
        evidence_ledger_inconsistent,
        evidence_ledger_sqlite_error,
    )


    _require_evidence_writer(connection)
    resolution_values = _verification_runner_value_dict(
        resolution,
        VerificationRunnerResolution,
    )
    attempt_values = _verification_runner_value_dict(
        attempt,
        VerificationRunnerAttempt,
    )
    if (
        resolution.project_id != attempt.project_id
        or resolution.task_id != attempt.task_id
        or resolution.target_generation != attempt.target_generation
        or resolution.verification_runner_resolution_id
        != attempt.verification_runner_resolution_id
        or resolution.gate_eligibility_version not in {0, 1}
        or attempt.gate_eligibility_version
        != resolution.gate_eligibility_version
    ):
        raise evidence_ledger_inconsistent()
    existing = _read_verification_runner_generation_unvalidated(
        connection,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
        target_generation=resolution.target_generation,
    )
    if existing is not None:
        if (
            existing["state"] != "pending"
            or _verification_runner_value_dict(
                existing["resolution"], VerificationRunnerResolution
            )
            != resolution_values
            or _verification_runner_value_dict(
                existing["attempt"], VerificationRunnerAttempt
            )
            != attempt_values
        ):
            raise evidence_ledger_inconsistent()
        _validated_verification_runner_references(connection)
        return False
    pending = connection.execute(
        "SELECT 1 FROM verification_runner_resolutions AS existing "
        "LEFT JOIN verification_runner_observations AS observation "
        "ON observation.project_id = existing.project_id "
        "AND observation.task_id = existing.task_id "
        "AND observation.target_generation = existing.target_generation "
        "LEFT JOIN verification_runner_sandbox_events AS cleanup "
        "ON cleanup.project_id = existing.project_id "
        "AND cleanup.task_id = existing.task_id "
        "AND cleanup.target_generation = existing.target_generation "
        "WHERE existing.project_id = ? "
        "AND observation.verification_runner_observation_id IS NULL "
        "AND cleanup.verification_runner_sandbox_event_id IS NULL LIMIT 1",
        (resolution.project_id,),
    ).fetchone()
    if pending is not None:
        raise evidence_ledger_inconsistent()
    current = read_current_verification_runner_target_basis(
        connection,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
    )
    expected_current = _verification_runner_current_basis_for_resolution(
        resolution,
        acceptance_criterion_id=current["acceptance_criterion_id"],
    )
    if current != expected_current:
        raise evidence_ledger_inconsistent()
    try:
        resolution_fields = tuple(sorted(resolution_values))
        connection.execute(
            "INSERT INTO verification_runner_resolutions("
            + ", ".join(resolution_fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in resolution_fields)
            + ")",
            resolution_values,
        )
        attempt_fields = tuple(sorted(attempt_values))
        connection.execute(
            "INSERT INTO verification_runner_attempts("
            + ", ".join(attempt_fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in attempt_fields)
            + ")",
            attempt_values,
        )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    _validated_verification_runner_references(connection)
    return True


def _exact_runner_terminal_replay(
    connection: sqlite3.Connection,
    *,
    generation: dict[str, Any],
    observation_values: dict[str, Any],
    reference: dict[str, Any],
    criterion_link: _storage.PreparedCriterionEvidenceLink,
    cleanup_values: dict[str, Any],
) -> bool:
    from task_governance_tool.storage import (
        PreparedCriterionEvidenceLink,
    )

    stored_observation = generation["observation"]
    stored_cleanup = generation["cleanup_event"]
    if stored_observation is None or stored_cleanup is None:
        return False
    stored_reference = connection.execute(
        "SELECT * FROM evidence_references "
        "WHERE source_kind = 'runner_observation' AND source_id = ?",
        (stored_observation.verification_runner_observation_id,),
    ).fetchone()
    stored_link = connection.execute(
        "SELECT * FROM criterion_evidence_links "
        "WHERE relation = 'runner_observation' AND evidence_reference_id = ?",
        (reference.get("evidence_reference_id"),),
    ).fetchone()
    return (
        _verification_runner_value_dict(
            stored_observation, VerificationRunnerObservation
        )
        == observation_values
        and _verification_runner_value_dict(
            stored_cleanup, VerificationRunnerSandboxEvent
        )
        == cleanup_values
        and stored_reference is not None
        and dict(stored_reference) == reference
        and stored_link is not None
        and dict(stored_link)
        == _verification_runner_value_dict(
            criterion_link,
            PreparedCriterionEvidenceLink,
        )
    )


def persist_verification_runner_terminal_locked(
    connection: sqlite3.Connection,
    *,
    observation: VerificationRunnerObservation,
    evidence_reference: dict[str, Any],
    criterion_link: _storage.PreparedCriterionEvidenceLink,
    cleanup_event: VerificationRunnerSandboxEvent,
) -> bool:
    """Atomically append one closed terminal graph and standalone Evidence."""

    from task_governance_tool.evidence_repository import (
        persist_criterion_evidence_link_locked,
    )
    from task_governance_tool.storage import (
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS,
        _require_evidence_writer,
        evidence_ledger_inconsistent,
        evidence_ledger_sqlite_error,
        verification_runner_state_invalid,
    )


    _require_evidence_writer(connection)
    observation_values = _verification_runner_value_dict(
        observation,
        VerificationRunnerObservation,
    )
    cleanup_values = _verification_runner_value_dict(
        cleanup_event,
        VerificationRunnerSandboxEvent,
    )
    if type(evidence_reference) is not dict:
        raise evidence_ledger_inconsistent()
    reference = dict(evidence_reference)
    reference_fields = _EVIDENCE_LEDGER_REQUIRED_COLUMNS["evidence_references"]
    if set(reference) != reference_fields:
        raise evidence_ledger_inconsistent()
    _validated_verification_runner_references(connection)
    generation = _read_verification_runner_generation_unvalidated(
        connection,
        project_id=observation.project_id,
        task_id=observation.task_id,
        target_generation=observation.target_generation,
    )
    if generation is None:
        raise evidence_ledger_inconsistent()
    if generation["state"] == "terminal":
        if _exact_runner_terminal_replay(
            connection,
            generation=generation,
            observation_values=observation_values,
            reference=reference,
            criterion_link=criterion_link,
            cleanup_values=cleanup_values,
        ):
            return False
        raise evidence_ledger_inconsistent()
    if generation["state"] != "pending":
        raise evidence_ledger_inconsistent()
    attempt = generation["attempt"]
    resolution = generation["resolution"]
    if (
        cleanup_event.project_id != observation.project_id
        or cleanup_event.task_id != observation.task_id
        or cleanup_event.target_generation != observation.target_generation
        or cleanup_event.verification_runner_attempt_id
        != observation.verification_runner_attempt_id
        or cleanup_event.terminal_observation_id
        != observation.verification_runner_observation_id
        or attempt.verification_runner_attempt_id
        != observation.verification_runner_attempt_id
        or observation.gate_eligibility_version
        != resolution.gate_eligibility_version
    ):
        raise evidence_ledger_inconsistent()
    if not _verification_runner_current_basis_matches(
        connection,
        resolution=resolution,
        acceptance_criterion_id=reference["acceptance_criterion_id"],
    ):
        raise verification_runner_state_invalid()
    observation_fields = tuple(sorted(observation_values))
    cleanup_fields = tuple(sorted(cleanup_values))
    reference_order = tuple(sorted(reference))
    try:
        connection.execute(
            "INSERT INTO verification_runner_observations("
            + ", ".join(observation_fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in observation_fields)
            + ")",
            observation_values,
        )
        connection.execute(
            "INSERT INTO evidence_references("
            + ", ".join(reference_order)
            + ") VALUES ("
            + ", ".join(":" + field for field in reference_order)
            + ")",
            reference,
        )
        persist_criterion_evidence_link_locked(
            connection,
            link=criterion_link,
        )
        connection.execute(
            "INSERT INTO verification_runner_sandbox_events("
            + ", ".join(cleanup_fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in cleanup_fields)
            + ")",
            cleanup_values,
        )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    _validated_verification_runner_references(connection)
    return True


def persist_verification_runner_restart_cleanup_locked(
    connection: sqlite3.Connection,
    *,
    cleanup_event: VerificationRunnerSandboxEvent,
) -> bool:
    """Append the one cleanup-only event; this state remains fail closed."""

    from task_governance_tool.storage import (
        _require_evidence_writer,
        evidence_ledger_inconsistent,
        evidence_ledger_sqlite_error,
    )


    _require_evidence_writer(connection)
    cleanup_values = _verification_runner_value_dict(
        cleanup_event,
        VerificationRunnerSandboxEvent,
    )
    _validated_verification_runner_references(connection)
    generation = _read_verification_runner_generation_unvalidated(
        connection,
        project_id=cleanup_event.project_id,
        task_id=cleanup_event.task_id,
        target_generation=cleanup_event.target_generation,
    )
    if generation is None:
        raise evidence_ledger_inconsistent()
    if (
        generation["resolution"].gate_eligibility_version not in {0, 1}
        or generation["attempt"] is None
        or generation["attempt"].gate_eligibility_version
        != generation["resolution"].gate_eligibility_version
    ):
        raise evidence_ledger_inconsistent()
    if generation["state"] == "restart_cleaned":
        if (
            _verification_runner_value_dict(
                generation["cleanup_event"], VerificationRunnerSandboxEvent
            )
            == cleanup_values
        ):
            return False
        raise evidence_ledger_inconsistent()
    if (
        generation["state"] != "pending"
        or cleanup_event.terminal_observation_id is not None
        or cleanup_event.verification_runner_attempt_id
        != generation["attempt"].verification_runner_attempt_id
    ):
        raise evidence_ledger_inconsistent()
    fields = tuple(sorted(cleanup_values))
    try:
        connection.execute(
            "INSERT INTO verification_runner_sandbox_events("
            + ", ".join(fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in fields)
            + ")",
            cleanup_values,
        )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    _validated_verification_runner_references(connection)
    return True


# Shared value types remain storage-owned. Bind the module after definitions
# so either repository import order also resolves runtime annotations.
from task_governance_tool import storage as _storage
