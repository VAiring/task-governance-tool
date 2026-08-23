"""Synchronous review-target trigger for the schema-v20 shadow Runner.

The coordinator keeps Git/OS work outside SQLite, uses one no-wait package
Runner lock, and enters short transactions only to persist the target intent or
append terminal evidence.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from task_governance_tool.artifact_manifest import (
    ArtifactManifestError,
    ArtifactObservation,
    observe_git_commit_manifest,
    observe_staged_git_manifest,
    opaque_artifact_observation,
)
from task_governance_tool.evidence_ledger import (
    EvidenceSource,
    TargetCaptureBinding,
    build_evidence_reference,
)
from task_governance_tool.git_snapshot import GitSnapshotError
from task_governance_tool.reviews import (
    REVIEW_TARGET_KINDS,
    ReviewEvidenceError,
    ReviewTargetAuthorityBasis,
    ReviewTargetResult,
    generate_review_id,
    normalize_revision_review_target,
    persist_prepared_review_target_capture,
    read_review_target_authority_basis,
    review_error,
    validate_revision_review_target_input,
)
from task_governance_tool.state_paths import VerificationRunnerStatePaths
from task_governance_tool.storage import (
    DatabaseTarget,
    PreparedCriterionEvidenceLink,
    StorageError,
    VerificationRunnerObservation,
    VerificationRunnerAttempt,
    VerificationRunnerResolution,
    VerificationRunnerSandboxEvent,
    begin_initialized_write,
    connect_initialized,
    connect_initialized_readonly,
    has_pending_verification_runner_cleanup,
    insert_verification_runner_resolution_locked,
    persist_verification_runner_recovery_terminal_locked,
    persist_verification_runner_terminal_locked,
    read_current_verification_runner_target_basis,
    read_pending_verification_runner_cleanup,
    read_verification_runner_resolution_locked,
    read_verification_runner_public_projection,
    utc_now,
)
from task_governance_tool.tasks import (
    TaskRepositoryError,
    TaskValidationError,
    validate_choice,
    validate_task_id,
)
from task_governance_tool.verification_runner import (
    RUNNER_CONTRACT_VERSION,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_TRIGGER,
    generate_runner_id,
    resolution_idempotency_digest,
    runner_observation_source_projection,
    verification_runner_observation_digest,
    verification_runner_sandbox_event_digest,
)
from task_governance_tool.verification_runner_git import (
    RunnerMaterialization,
    VerificationRunnerGitError,
    observe_commit_runner_target,
    observe_staged_runner_target,
    preflight_runner_material,
)
from task_governance_tool.verification_runner_lifecycle import (
    RunnerLayoutInventory,
    VerificationRunnerLifecycleError,
    attempt_paths,
    inspect_runner_layout,
    quarantine_attempt_tree,
    remove_attempt_tree,
    require_known_attempt_inventory,
    zero_wait_runner_lock,
)
from task_governance_tool.verification_runner_plan import (
    VerificationRunnerPlanError,
    VerificationRunnerPlanResolution,
    resolve_verification_runner_plan,
)
from task_governance_tool.verification_runner_runtime import (
    RunnerImplementationIdentity,
    VerificationRunnerRuntimeError,
    capture_runner_implementation,
)
from task_governance_tool.verification_runner_process import (
    ProcessRunResult,
)


_RUNNER_POLICY_DIGEST = (
    "sha256:8910c1edfd525be0def6a2c3afb65adab11e5a32e9a60ebbf898c175ffd60fa8"
)


@dataclass
class VerificationRunnerServiceError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class RunnerReviewTargetResult:
    review: ReviewTargetResult
    verification_runner: dict[str, Any]


@dataclass(frozen=True)
class _CapturedTarget:
    observation: ArtifactObservation
    material: RunnerMaterialization | None
    addressable: bool
    closed_reason: str | None


@dataclass(frozen=True)
class _PreparedResolution:
    plan: VerificationRunnerPlanResolution
    implementation: RunnerImplementationIdentity
    route: str
    reason: str | None


@dataclass(frozen=True)
class _RunnerLaunchIntent:
    review: ReviewTargetResult
    resolution: VerificationRunnerResolution
    attempt: VerificationRunnerAttempt
    verification_runner: dict[str, Any]


def _service_error(code: str, message: str) -> VerificationRunnerServiceError:
    return VerificationRunnerServiceError(code, message)


def _runner_paths(target: DatabaseTarget) -> VerificationRunnerStatePaths:
    return VerificationRunnerStatePaths(
        root=target.resolved_verification_runner_root,
        lock=target.resolved_verification_runner_lock,
        attempts=target.resolved_verification_runner_attempts,
        quarantine=target.resolved_verification_runner_quarantine,
    )


def _plan_relative_path(target: DatabaseTarget) -> str:
    if target.skill_root is None:
        raise _service_error(
            "runner_state_invalid",
            "verification Runner package identity is unavailable",
        )
    repo = Path(target.project.canonical_repo).resolve(strict=False)
    skill = Path(target.skill_root).resolve(strict=False)
    try:
        relative = skill.relative_to(repo)
    except ValueError as exc:
        raise _service_error(
            "runner_state_invalid",
            "verification Runner package identity is unavailable",
        ) from exc
    if not relative.parts:
        raise _service_error(
            "runner_state_invalid",
            "verification Runner package identity is unavailable",
        )
    return (relative / "config" / "verification-runner.json").as_posix()


def _capture_ordinary_target(
    target: DatabaseTarget,
    *,
    kind: str,
    revision: Any,
) -> ArtifactObservation:
    if kind == "git_snapshot":
        if revision is not None:
            raise review_error(
                "invalid_review_evidence",
                "git_snapshot captures the current staged index and does not accept --revision",
                "review_target_value",
            )
        return observe_staged_git_manifest(target.project.canonical_repo)
    if revision is None:
        raise review_error(
            "invalid_review_evidence",
            "--revision is required unless review target kind is git_snapshot",
            "review_target_value",
        )
    target_kind, value = normalize_revision_review_target(
        target.project,
        kind,
        revision,
    )
    if target_kind == "git_commit":
        return observe_git_commit_manifest(target.project.canonical_repo, value)
    return opaque_artifact_observation(
        target_kind=target_kind,
        target_value=value,
    )


def _capture_runner_target(
    target: DatabaseTarget,
    *,
    kind: str,
    revision: Any,
    plan_relative_path: str,
) -> _CapturedTarget:
    if kind not in {"git_snapshot", "git_commit"}:
        return _CapturedTarget(
            observation=_capture_ordinary_target(
                target,
                kind=kind,
                revision=revision,
            ),
            material=None,
            addressable=False,
            closed_reason="unsupported_target",
        )
    try:
        if kind == "git_snapshot":
            if revision is not None:
                raise review_error(
                    "invalid_review_evidence",
                    "git_snapshot captures the current staged index and does not accept --revision",
                    "review_target_value",
                )
            observed = observe_staged_runner_target(
                target.project.canonical_repo,
                plan_relative_path=plan_relative_path,
            )
        else:
            if revision is None:
                raise review_error(
                    "invalid_review_evidence",
                    "--revision is required unless review target kind is git_snapshot",
                    "review_target_value",
                )
            _kind, value = normalize_revision_review_target(
                target.project,
                kind,
                revision,
            )
            observed = observe_commit_runner_target(
                target.project.canonical_repo,
                value,
                plan_relative_path=plan_relative_path,
            )
        try:
            material = preflight_runner_material(
                target.project.canonical_repo,
                observed,
            )
        except VerificationRunnerGitError as exc:
            if exc.code == "unsupported_target":
                return _CapturedTarget(
                    observation=observed.artifact,
                    material=None,
                    addressable=False,
                    closed_reason=exc.code,
                )
            raise _service_error(exc.code, exc.message) from exc
        return _CapturedTarget(
            observation=observed.artifact,
            material=material,
            addressable=True,
            closed_reason=None,
        )
    except VerificationRunnerGitError as exc:
        if exc.code != "unsupported_target":
            raise _service_error(exc.code, exc.message) from exc
        return _CapturedTarget(
            observation=_capture_ordinary_target(
                target,
                kind=kind,
                revision=revision,
            ),
            material=None,
            addressable=False,
            closed_reason=exc.code,
        )
    except (ArtifactManifestError, GitSnapshotError) as exc:
        code = getattr(exc, "code", "invalid_review_evidence")
        message = getattr(exc, "message", "review target could not be captured")
        raise _service_error(code, message) from exc


def _capture_runner_target_for_install(
    target: DatabaseTarget,
    *,
    kind: str,
    revision: Any,
) -> _CapturedTarget:
    """Resolve the production plan path, retaining only the injected test seam."""

    plan_relative_path = ""
    if kind in {"git_snapshot", "git_commit"}:
        try:
            plan_relative_path = _plan_relative_path(target)
        except VerificationRunnerServiceError:
            if not target.explicit_db:
                raise
            return _CapturedTarget(
                observation=_capture_ordinary_target(
                    target,
                    kind=kind,
                    revision=revision,
                ),
                material=None,
                addressable=False,
                closed_reason="unsupported_target",
            )
    return _capture_runner_target(
        target,
        kind=kind,
        revision=revision,
        plan_relative_path=plan_relative_path,
    )


def _invalid_plan_resolution(
    material: RunnerMaterialization,
    *,
    reason: str,
) -> VerificationRunnerPlanResolution:
    return VerificationRunnerPlanResolution(
        plan_state="invalid",
        route="blocked",
        reason=reason,
        plan_blob_object_id=material.plan_blob_object_id,
        plan_raw_digest=material.plan_raw_digest,
        plan_id=None,
        plan_version=None,
        plan_semantic_digest=None,
        selected_entry_digest=None,
        coverage="not_applicable",
        steps=(),
    )


def _not_addressable_plan(reason: str) -> VerificationRunnerPlanResolution:
    return VerificationRunnerPlanResolution(
        plan_state="not_addressable",
        route=("m21_fallback" if reason == "unsupported_target" else "blocked"),
        reason=reason,
        plan_blob_object_id=None,
        plan_raw_digest=None,
        plan_id=None,
        plan_version=None,
        plan_semantic_digest=None,
        selected_entry_digest=None,
        coverage="not_applicable",
        steps=(),
    )


def _prepare_resolution(
    target: DatabaseTarget,
    authority: ReviewTargetAuthorityBasis,
    captured: _CapturedTarget,
) -> _PreparedResolution | None:
    if target.skill_root is None:
        return None
    try:
        implementation = capture_runner_implementation(Path(target.skill_root))
    except VerificationRunnerRuntimeError as exc:
        if exc.code == "policy_mismatch":
            # The Runner is an audit-only consumer of an otherwise valid review
            # target.  An unauthenticated implementation therefore leaves the
            # current generation with no Runner row instead of blocking exact
            # target installation.  Lifecycle and storage uncertainty remain
            # outside this boundary and continue to fail closed.
            return None
        raise _service_error(
            exc.code,
            "verification Runner implementation is unavailable",
        ) from exc

    if not captured.addressable or captured.material is None:
        plan = _not_addressable_plan(captured.closed_reason or "unsupported_target")
    else:
        try:
            plan = resolve_verification_runner_plan(
                captured.material.plan_raw_blob,
                plan_blob_object_id=captured.material.plan_blob_object_id,
                task_id=str(authority.task["task_id"]),
                contract_revision=int(authority.task["current_contract_revision"]),
                verification_expectation_digest=authority.verification_expectation_digest,
                verification_criterion_digest=str(
                    authority.verification_criterion_digest
                ),
            )
        except VerificationRunnerPlanError as exc:
            plan = _invalid_plan_resolution(captured.material, reason=exc.code)

    route = plan.route
    reason = plan.reason
    if plan.plan_state == "runner" and route == "runner":
        # R4A has physically retired the Candidate-only runtime.  Until the
        # later bounded-process unit supplies an authorized fixed executable,
        # a trusted plan remains an inert shadow observation with no attempt.
        route = "m21_fallback"
        reason = "sandbox_unavailable"
    return _PreparedResolution(
        plan=plan,
        implementation=implementation,
        route=route,
        reason=reason,
    )


def _resolution_row(
    *,
    current: dict[str, Any],
    prepared: _PreparedResolution,
    captured: _CapturedTarget,
    resolution_id: str,
    created_at: str,
) -> VerificationRunnerResolution:
    plan = prepared.plan
    target_material_digest = (
        captured.material.target_material_digest
        if plan.plan_state == "runner" and captured.material is not None
        else None
    )
    values = {
        "project_id": current["project_id"],
        "task_id": current["task_id"],
        "contract_revision": current["contract_revision"],
        "authority_snapshot_id": current["authority_snapshot_id"],
        "verification_criterion_id": current["verification_criterion_id"],
        "verification_expectation_digest": current[
            "verification_expectation_digest"
        ],
        "verification_criterion_digest": current[
            "verification_criterion_digest"
        ],
        "target_kind": current["target_kind"],
        "target_value": current["target_value"],
        "target_base_revision": current["target_base_revision"],
        "target_generation": current["target_generation"],
        "target_capture_version": current["target_capture_version"],
        "artifact_manifest_id": current["artifact_manifest_id"],
        "target_material_digest": target_material_digest,
        "plan_state": plan.plan_state,
        "plan_blob_object_id": plan.plan_blob_object_id,
        "plan_raw_digest": plan.plan_raw_digest,
        "plan_id": plan.plan_id,
        "plan_version": plan.plan_version,
        "plan_semantic_digest": plan.plan_semantic_digest,
        "selected_entry_digest": plan.selected_entry_digest,
        "coverage": plan.coverage,
        "step_count": plan.step_count,
        "runner_contract_version": RUNNER_CONTRACT_VERSION,
        "runner_implementation_version": RUNNER_IMPLEMENTATION_VERSION,
        "runner_implementation_digest": prepared.implementation.implementation_digest,
        "runner_policy_digest": _RUNNER_POLICY_DIGEST,
        "sandbox_provider": None,
        "sandbox_policy_digest": None,
        "runtime_digest": None,
        "gate_eligibility_version": 0,
        "trigger": RUNNER_TRIGGER,
        "route": prepared.route,
        "reason": prepared.reason,
    }
    return VerificationRunnerResolution(
        verification_runner_resolution_id=resolution_id,
        **values,
        idempotency_digest=resolution_idempotency_digest(values),
        created_at=created_at,
    )


def _no_launch_observation(
    resolution: VerificationRunnerResolution,
    *,
    observation_id: str,
    observed_at: str,
) -> VerificationRunnerObservation:
    outcome = "not_run" if resolution.route == "m21_fallback" else "blocked_prelaunch"
    total_step_count = 0 if resolution.route == "m21_fallback" else resolution.step_count
    digest_values = {
        "attempt_id": None,
        "completed_step_count": 0,
        "complete_plan": 0,
        "cpu_time_ms": None,
        "duration_ms": 0,
        "failed_step_ordinal": None,
        "finished_at": observed_at,
        "gate_eligibility_version": resolution.gate_eligibility_version,
        "launch_state": "no_launch",
        "outcome": outcome,
        "peak_job_memory_bytes": None,
        "project_id": resolution.project_id,
        "reason": resolution.reason,
        "resolution_id": resolution.verification_runner_resolution_id,
        "runner_implementation_digest": resolution.runner_implementation_digest,
        "started_at": observed_at,
        "target_generation": resolution.target_generation,
        "task_id": resolution.task_id,
        "route": resolution.route,
        "total_process_count": None,
        "total_step_count": total_step_count,
    }
    return VerificationRunnerObservation(
        verification_runner_observation_id=observation_id,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
        target_generation=resolution.target_generation,
        gate_eligibility_version=resolution.gate_eligibility_version,
        verification_runner_resolution_id=(
            resolution.verification_runner_resolution_id
        ),
        verification_runner_attempt_id=None,
        runner_implementation_digest=resolution.runner_implementation_digest,
        route=resolution.route,
        launch_state="no_launch",
        outcome=outcome,
        reason=resolution.reason,
        complete_plan=0,
        total_step_count=total_step_count,
        completed_step_count=0,
        failed_step_ordinal=None,
        started_at=observed_at,
        finished_at=observed_at,
        duration_ms=0,
        cpu_time_ms=None,
        peak_job_memory_bytes=None,
        total_process_count=None,
        sanitized_result_digest=verification_runner_observation_digest(
            digest_values
        ),
        created_at=observed_at,
    )


def _sandbox_event(
    attempt: VerificationRunnerAttempt,
    *,
    event_kind: str,
    created_at: str,
    terminal_observation_id: str | None = None,
) -> VerificationRunnerSandboxEvent:
    values = {
        "attempt_id": attempt.verification_runner_attempt_id,
        "event_kind": event_kind,
        "project_id": attempt.project_id,
        "target_generation": attempt.target_generation,
        "task_id": attempt.task_id,
        "terminal_observation_id": terminal_observation_id,
    }
    return VerificationRunnerSandboxEvent(
        verification_runner_sandbox_event_id=generate_runner_id("sandbox_event"),
        project_id=attempt.project_id,
        task_id=attempt.task_id,
        target_generation=attempt.target_generation,
        verification_runner_attempt_id=attempt.verification_runner_attempt_id,
        event_kind=event_kind,
        event_digest=verification_runner_sandbox_event_digest(values),
        terminal_observation_id=terminal_observation_id,
        created_at=created_at,
    )


def _attempt_terminal_observation(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    *,
    launch_state: str,
    outcome: str,
    reason: str | None,
    route: str,
    total_step_count: int,
    completed_step_count: int,
    failed_step_ordinal: int | None,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    cpu_time_ms: int | None,
    peak_job_memory_bytes: int | None,
    total_process_count: int | None,
    observation_id: str | None = None,
) -> VerificationRunnerObservation:
    complete_plan = int(
        outcome == "pass"
        and reason is None
        and failed_step_ordinal is None
        and total_step_count == resolution.step_count
        and completed_step_count == total_step_count
        and total_step_count > 0
    )
    values = {
        "attempt_id": attempt.verification_runner_attempt_id,
        "completed_step_count": completed_step_count,
        "complete_plan": complete_plan,
        "cpu_time_ms": cpu_time_ms,
        "duration_ms": duration_ms,
        "failed_step_ordinal": failed_step_ordinal,
        "finished_at": finished_at,
        "gate_eligibility_version": resolution.gate_eligibility_version,
        "launch_state": launch_state,
        "outcome": outcome,
        "peak_job_memory_bytes": peak_job_memory_bytes,
        "project_id": resolution.project_id,
        "reason": reason,
        "resolution_id": resolution.verification_runner_resolution_id,
        "runner_implementation_digest": resolution.runner_implementation_digest,
        "started_at": started_at,
        "target_generation": resolution.target_generation,
        "task_id": resolution.task_id,
        "route": route,
        "total_process_count": total_process_count,
        "total_step_count": total_step_count,
    }
    return VerificationRunnerObservation(
        verification_runner_observation_id=(
            observation_id or generate_runner_id("observation")
        ),
        project_id=resolution.project_id,
        task_id=resolution.task_id,
        target_generation=resolution.target_generation,
        gate_eligibility_version=resolution.gate_eligibility_version,
        verification_runner_resolution_id=(
            resolution.verification_runner_resolution_id
        ),
        verification_runner_attempt_id=attempt.verification_runner_attempt_id,
        runner_implementation_digest=resolution.runner_implementation_digest,
        route=route,
        launch_state=launch_state,
        outcome=outcome,
        reason=reason,
        complete_plan=complete_plan,
        total_step_count=total_step_count,
        completed_step_count=completed_step_count,
        failed_step_ordinal=failed_step_ordinal,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        cpu_time_ms=cpu_time_ms,
        peak_job_memory_bytes=peak_job_memory_bytes,
        total_process_count=total_process_count,
        sanitized_result_digest=verification_runner_observation_digest(values),
        created_at=finished_at,
    )


def _prelaunch_attempt_observation(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    *,
    reason: str,
    observed_at: str,
    fallback: bool = False,
) -> VerificationRunnerObservation:
    return _attempt_terminal_observation(
        resolution,
        attempt,
        launch_state="no_launch",
        outcome="not_run" if fallback else "blocked_prelaunch",
        reason=reason,
        route="m21_fallback" if fallback else "blocked",
        total_step_count=0 if fallback else resolution.step_count,
        completed_step_count=0,
        failed_step_ordinal=None,
        started_at=observed_at,
        finished_at=observed_at,
        duration_ms=0,
        cpu_time_ms=None,
        peak_job_memory_bytes=None,
        total_process_count=None,
    )


def _cleanup_failure_observation(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    provisional: VerificationRunnerObservation,
    *,
    finished_at: str,
) -> VerificationRunnerObservation:
    launch_time_proved = provisional.launch_state == "launched"
    return _attempt_terminal_observation(
        resolution,
        attempt,
        launch_state=provisional.launch_state,
        outcome="sandbox_cleanup_failed",
        reason="sandbox_cleanup_failed",
        route="blocked",
        total_step_count=provisional.total_step_count,
        completed_step_count=provisional.completed_step_count,
        failed_step_ordinal=None,
        started_at=provisional.started_at if launch_time_proved else finished_at,
        finished_at=finished_at,
        duration_ms=provisional.duration_ms if launch_time_proved else 0,
        cpu_time_ms=None,
        peak_job_memory_bytes=None,
        total_process_count=None,
    )




def _stored_basis_matches(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    resolution: VerificationRunnerResolution,
) -> bool:
    try:
        authority = read_review_target_authority_basis(
            connection,
            target.project,
            resolution.task_id,
        )
        current = read_current_verification_runner_target_basis(
            connection,
            project_id=resolution.project_id,
            task_id=resolution.task_id,
        )
    except (ReviewEvidenceError, StorageError, TaskRepositoryError, TaskValidationError):
        return False
    return bool(
        int(authority.task["current_contract_revision"])
        == resolution.contract_revision
        and authority.authority_snapshot_id == resolution.authority_snapshot_id
        and authority.verification_criterion_id
        == resolution.verification_criterion_id
        and authority.verification_criterion_digest
        == resolution.verification_criterion_digest
        and authority.verification_expectation_digest
        == resolution.verification_expectation_digest
        and current["contract_revision"] == resolution.contract_revision
        and current["authority_snapshot_id"] == resolution.authority_snapshot_id
        and current["verification_criterion_id"]
        == resolution.verification_criterion_id
        and current["verification_expectation_digest"]
        == resolution.verification_expectation_digest
        and current["verification_criterion_digest"]
        == resolution.verification_criterion_digest
        and current["target_kind"] == resolution.target_kind
        and current["target_value"] == resolution.target_value
        and current["target_base_revision"] == resolution.target_base_revision
        and current["target_generation"] == resolution.target_generation
        and current["target_capture_version"]
        == resolution.target_capture_version
        and current["artifact_manifest_id"] == resolution.artifact_manifest_id
        and current["gate_eligibility_version"]
        == resolution.gate_eligibility_version
    )


def _installed_basis_matches(
    target: DatabaseTarget,
    resolution: VerificationRunnerResolution,
) -> bool:
    try:
        implementation = capture_runner_implementation(Path(target.skill_root))
    except (TypeError, VerificationRunnerRuntimeError, OSError):
        return False
    return bool(
        implementation.implementation_version
        == resolution.runner_implementation_version
        and implementation.implementation_digest
        == resolution.runner_implementation_digest
        and _RUNNER_POLICY_DIGEST == resolution.runner_policy_digest
    )


def _process_terminal_observation(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    result: ProcessRunResult,
    *,
    started_at: str,
    finished_at: str,
    basis_drifted: bool,
) -> VerificationRunnerObservation:
    launched = result.launch_state == "launched"
    launch_state = "launched" if launched else "no_launch"
    if not launched:
        started_at = finished_at
    outcome = result.outcome
    reason = result.reason
    route = "runner" if launched else "blocked"
    failed_ordinal = result.failed_step_ordinal if launched else None
    completed = len(result.steps) if launched else 0
    cpu = result.cpu_time_ms if launched else None
    memory = result.peak_job_memory_bytes if launched else None
    processes = result.total_process_count if launched else None
    if basis_drifted:
        outcome = "post_launch_drift" if launched else "blocked_prelaunch"
        reason = "post_launch_drift" if launched else "prelaunch_drift"
        failed_ordinal = None
    if result.outcome == "controller_interrupted" and launched:
        launch_state = "launch_uncertain"
        outcome = "controller_interrupted"
        reason = "controller_interrupted"
        route = "blocked"
        failed_ordinal = None
        completed = sum(step.outcome == "pass" for step in result.steps)
        started_at = finished_at
        cpu = memory = processes = None
    if outcome == "sandbox_cleanup_failed":
        route = "blocked"
        failed_ordinal = None
        cpu = memory = processes = None
    return _attempt_terminal_observation(
        resolution,
        attempt,
        launch_state=launch_state,
        outcome=outcome,
        reason=reason,
        route=route,
        total_step_count=resolution.step_count,
        completed_step_count=completed,
        failed_step_ordinal=failed_ordinal,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=(
            result.duration_ms if launch_state == "launched" else 0
        ),
        cpu_time_ms=cpu,
        peak_job_memory_bytes=memory,
        total_process_count=processes,
    )


def _terminal_evidence(
    *,
    resolution: VerificationRunnerResolution,
    observation: VerificationRunnerObservation,
    acceptance_criterion_id: str | None,
    created_at: str,
) -> tuple[dict[str, Any], PreparedCriterionEvidenceLink]:
    source = EvidenceSource(
        source_kind="runner_observation",
        source_state="recorded",
        source_id=observation.verification_runner_observation_id,
        source_projection=runner_observation_source_projection(
            observation=asdict(observation),
            resolution=asdict(resolution),
        ),
    )
    binding = TargetCaptureBinding(
        target_kind=resolution.target_kind,
        target_value=resolution.target_value,
        target_base_revision=resolution.target_base_revision or "",
        target_generation=resolution.target_generation,
        authority_snapshot_id=resolution.authority_snapshot_id,
        acceptance_criterion_id=acceptance_criterion_id,
        verification_criterion_id=resolution.verification_criterion_id,
    )
    spec = build_evidence_reference(
        source=source,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
        contract_revision=resolution.contract_revision,
        binding=binding,
    )
    reference_id = generate_review_id("tg_evidence_reference")
    reference = {
        "evidence_reference_id": reference_id,
        "project_id": resolution.project_id,
        "task_id": resolution.task_id,
        "source_kind": source.source_kind,
        "source_state": source.source_state,
        "source_id": source.source_id,
        "assurance_class": spec.attribution.assurance_class,
        "producer_class": spec.attribution.producer_class,
        "producer_version": spec.attribution.producer_version,
        "contract_revision": resolution.contract_revision,
        "authority_snapshot_id": resolution.authority_snapshot_id,
        "acceptance_criterion_id": acceptance_criterion_id,
        "verification_criterion_id": resolution.verification_criterion_id,
        "target_kind": resolution.target_kind,
        "target_value": resolution.target_value,
        "target_base_revision": resolution.target_base_revision or "",
        "target_generation": resolution.target_generation,
        "completion_cycle_id": None,
        "digest": spec.digest,
        "created_at": created_at,
    }
    link = PreparedCriterionEvidenceLink(
        criterion_evidence_link_id=generate_review_id(
            "tg_criterion_evidence_link"
        ),
        project_id=resolution.project_id,
        task_id=resolution.task_id,
        criterion_id=resolution.verification_criterion_id,
        evidence_reference_id=reference_id,
        relation="runner_observation",
        assurance_class=spec.attribution.assurance_class,
        producer_class=spec.attribution.producer_class,
        producer_version=spec.attribution.producer_version,
        created_at=created_at,
    )
    return reference, link


def _basis_drift_observation(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    observation: VerificationRunnerObservation,
) -> VerificationRunnerObservation:
    if (
        observation.outcome == "sandbox_cleanup_failed"
        or observation.launch_state == "launch_uncertain"
    ):
        return observation
    launched = observation.launch_state == "launched"
    return _attempt_terminal_observation(
        resolution,
        attempt,
        launch_state=observation.launch_state,
        outcome="post_launch_drift" if launched else "blocked_prelaunch",
        reason="post_launch_drift" if launched else "prelaunch_drift",
        route="runner" if launched else "blocked",
        total_step_count=resolution.step_count,
        completed_step_count=(
            observation.completed_step_count if launched else 0
        ),
        failed_step_ordinal=None,
        started_at=observation.started_at,
        finished_at=observation.finished_at,
        duration_ms=observation.duration_ms,
        cpu_time_ms=observation.cpu_time_ms if launched else None,
        peak_job_memory_bytes=(
            observation.peak_job_memory_bytes if launched else None
        ),
        total_process_count=(
            observation.total_process_count if launched else None
        ),
    )


def _publish_attempt_terminal(
    target: DatabaseTarget,
    authority: ReviewTargetAuthorityBasis,
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    observation: VerificationRunnerObservation,
    *,
    cleanup_proved: bool,
) -> dict[str, Any]:
    current_observation = observation
    if not _installed_basis_matches(target, resolution):
        current_observation = _basis_drift_observation(
            resolution,
            attempt,
            current_observation,
        )
    with closing(connect_initialized(target)) as connection:
        with connection:
            begin_initialized_write(connection, target)
            if not _stored_basis_matches(connection, target, resolution):
                current_observation = _basis_drift_observation(
                    resolution,
                    attempt,
                    current_observation,
                )
            cleanup_event = (
                _sandbox_event(
                    attempt,
                    event_kind="attempt_cleanup_succeeded",
                    created_at=current_observation.created_at,
                    terminal_observation_id=(
                        current_observation.verification_runner_observation_id
                    ),
                )
                if cleanup_proved
                else None
            )
            reference, link = _terminal_evidence(
                resolution=resolution,
                observation=current_observation,
                acceptance_criterion_id=authority.acceptance_criterion_id,
                created_at=current_observation.created_at,
            )
            persist_verification_runner_terminal_locked(
                connection,
                observation=current_observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup_event,
            )
            return read_verification_runner_public_projection(
                connection,
                project_id=resolution.project_id,
                task_id=resolution.task_id,
            )


def _cleanup_attempt_and_publish(
    target: DatabaseTarget,
    authority: ReviewTargetAuthorityBasis,
    intent: _RunnerLaunchIntent,
    observation: VerificationRunnerObservation,
) -> RunnerReviewTargetResult:
    paths = _runner_paths(target)
    # A process-layer cleanup failure means Job/handle shutdown was not proved.
    # Do not delete the private tree or publish cleanup success on the strength
    # of later filesystem operations alone.
    cleanup_proved = (
        observation.outcome != "sandbox_cleanup_failed"
        and observation.launch_state != "launch_uncertain"
    )
    if cleanup_proved:
        try:
            remove_attempt_tree(
                paths,
                intent.attempt.verification_runner_attempt_id,
            )
        except VerificationRunnerLifecycleError:
            cleanup_proved = False
    if not cleanup_proved:
        try:
            exact = attempt_paths(
                paths,
                intent.attempt.verification_runner_attempt_id,
            )
            if exact.root.exists() and not exact.quarantine.exists():
                quarantine_attempt_tree(
                    paths,
                    intent.attempt.verification_runner_attempt_id,
                )
        except (OSError, VerificationRunnerLifecycleError):
            pass
        observation = _cleanup_failure_observation(
            intent.resolution,
            intent.attempt,
            observation,
            finished_at=utc_now(),
        )
    projection = _publish_attempt_terminal(
        target,
        authority,
        intent.resolution,
        intent.attempt,
        observation,
        cleanup_proved=cleanup_proved,
    )
    return RunnerReviewTargetResult(
        review=intent.review,
        verification_runner=projection,
    )


def _prelaunch_reason(error: BaseException) -> tuple[str, bool]:
    code = str(getattr(error, "code", "sandbox_setup_failed"))
    allowed = {
        "target_drift",
        "object_drift",
        "policy_mismatch",
        "materialization_failed",
        "sandbox_setup_failed",
        "controller_interrupted",
        "state_inconsistent",
    }
    return (code if code in allowed else "sandbox_setup_failed", False)


def _unexpected_attempt_observation(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    *,
    stage: str,
    error: BaseException,
    observed_at: str,
) -> VerificationRunnerObservation:
    if stage == "process":
        # The process boundary was entered but did not return its closed launch
        # classification.  A child may exist, so no-launch is unprovable.
        return _attempt_terminal_observation(
            resolution,
            attempt,
            launch_state="launch_uncertain",
            outcome="controller_interrupted",
            reason="controller_interrupted",
            route="blocked",
            total_step_count=resolution.step_count,
            completed_step_count=0,
            failed_step_ordinal=None,
            started_at=observed_at,
            finished_at=observed_at,
            duration_ms=0,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
        )
    reason, fallback = _prelaunch_reason(error)
    return _prelaunch_attempt_observation(
        resolution,
        attempt,
        reason=reason,
        observed_at=observed_at,
        fallback=fallback,
    )


def _post_process_observation(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    provisional: VerificationRunnerObservation,
    *,
    outcome: str,
    reason: str,
    finished_at: str,
) -> VerificationRunnerObservation:
    if provisional.outcome == "sandbox_cleanup_failed":
        return provisional
    if provisional.launch_state == "launch_uncertain":
        # Once the process boundary reports uncertainty, later controller
        # checks cannot prove that no child was launched.  Keep the original
        # conservative terminal; cleanup/recovery must retain the attempt
        # until ownership and process absence are proved durably.
        return provisional
    if provisional.launch_state != "launched":
        return _prelaunch_attempt_observation(
            resolution,
            attempt,
            reason=reason,
            observed_at=finished_at,
        )
    if outcome == "controller_interrupted":
        return _attempt_terminal_observation(
            resolution,
            attempt,
            launch_state="launch_uncertain",
            outcome="controller_interrupted",
            reason="controller_interrupted",
            route="blocked",
            total_step_count=resolution.step_count,
            completed_step_count=provisional.completed_step_count,
            failed_step_ordinal=None,
            started_at=finished_at,
            finished_at=finished_at,
            duration_ms=0,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
        )
    return _attempt_terminal_observation(
        resolution,
        attempt,
        launch_state="launched",
        outcome=outcome,
        reason=reason,
        route="runner",
        total_step_count=resolution.step_count,
        completed_step_count=provisional.completed_step_count,
        failed_step_ordinal=None,
        started_at=provisional.started_at,
        finished_at=finished_at,
        duration_ms=provisional.duration_ms,
        cpu_time_ms=provisional.cpu_time_ms,
        peak_job_memory_bytes=provisional.peak_job_memory_bytes,
        total_process_count=provisional.total_process_count,
    )


def _read_cancel_requested(callback: Callable[[], bool]) -> bool | None:
    try:
        value = callback()
    except BaseException:
        return None
    return value if type(value) is bool else None






def _recovery_acceptance_criterion_id(
    connection: sqlite3.Connection,
    resolution: VerificationRunnerResolution,
) -> str | None:
    rows = connection.execute(
        """
        SELECT criterion_id
          FROM authority_snapshot_criteria
         WHERE project_id = ? AND task_id = ?
           AND authority_snapshot_id = ?
           AND criterion_kind = 'acceptance'
         ORDER BY criterion_id
        """,
        (
            resolution.project_id,
            resolution.task_id,
            resolution.authority_snapshot_id,
        ),
    ).fetchall()
    if len(rows) > 1:
        raise _service_error(
            "state_inconsistent",
            "verification Runner recovery authority is inconsistent",
        )
    return str(rows[0]["criterion_id"]) if rows else None


def _publish_recovery_terminal(
    target: DatabaseTarget,
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    observation: VerificationRunnerObservation,
    *,
    cleanup_proved: bool,
) -> None:
    with closing(connect_initialized(target)) as connection:
        with connection:
            begin_initialized_write(connection, target)
            # Cleanup belongs to the immutable resolution/attempt, not to the
            # Task's later current target or Contract.  Drift makes the old
            # evidence stale for gates but must never prevent terminal cleanup
            # publication for its durable owner.
            cleanup_event = (
                _sandbox_event(
                    attempt,
                    event_kind="attempt_cleanup_succeeded",
                    created_at=observation.created_at,
                    terminal_observation_id=(
                        observation.verification_runner_observation_id
                    ),
                )
                if cleanup_proved
                else None
            )
            reference, link = _terminal_evidence(
                resolution=resolution,
                observation=observation,
                acceptance_criterion_id=_recovery_acceptance_criterion_id(
                    connection,
                    resolution,
                ),
                created_at=observation.created_at,
            )
            persist_verification_runner_recovery_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup_event,
            )




def reconcile_pending_verification_runner_cleanup_under_lock(
    target: DatabaseTarget,
    inventory: RunnerLayoutInventory,
) -> None:
    """Retain pre-R4A attempt uncertainty without retired profile recovery."""

    paths = _runner_paths(target)
    with closing(connect_initialized_readonly(target)) as connection:
        pending = read_pending_verification_runner_cleanup(
            connection,
            project_id=target.project.project_id,
        )
        require_known_attempt_inventory(
            inventory,
            known_attempt_ids=tuple(
                item["attempt"].verification_runner_attempt_id
                for item in pending
            ),
        )
        if not pending:
            return
        item = pending[0]
        attempt = item["attempt"]
        terminal = item["terminal_observation"]
        resolution = read_verification_runner_resolution_locked(
            connection,
            project_id=attempt.project_id,
            task_id=attempt.task_id,
            target_generation=attempt.target_generation,
        )

    try:
        exact = attempt_paths(paths, attempt.verification_runner_attempt_id)
        if exact.root.exists() and not exact.quarantine.exists():
            quarantine_attempt_tree(paths, attempt.verification_runner_attempt_id)
    except (OSError, VerificationRunnerLifecycleError):
        pass

    if terminal is None:
        observed_at = utc_now()
        uncertain = _attempt_terminal_observation(
            resolution,
            attempt,
            launch_state="launch_uncertain",
            outcome="controller_interrupted",
            reason="controller_interrupted",
            route="blocked",
            total_step_count=resolution.step_count,
            completed_step_count=0,
            failed_step_ordinal=None,
            started_at=observed_at,
            finished_at=observed_at,
            duration_ms=0,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
        )
        _publish_recovery_terminal(
            target,
            resolution,
            attempt,
            _cleanup_failure_observation(
                resolution,
                attempt,
                uncertain,
                finished_at=observed_at,
            ),
            cleanup_proved=False,
        )
    raise _service_error(
        "runner_cleanup_required",
        "verification Runner cleanup could not be proved",
    )




def _persist_without_attempt(
    target: DatabaseTarget,
    authority: ReviewTargetAuthorityBasis,
    captured: _CapturedTarget,
    prepared: _PreparedResolution | None,
) -> RunnerReviewTargetResult:
    observed_at = utc_now()
    with closing(connect_initialized(target)) as connection:
        with connection:
            review = persist_prepared_review_target_capture(
                connection,
                target.project,
                authority.task,
                observation=captured.observation,
                database_target=target,
                now=observed_at,
            )
            if prepared is not None:
                current = read_current_verification_runner_target_basis(
                    connection,
                    project_id=target.project.project_id,
                    task_id=str(authority.task["task_id"]),
                )
                current.update(
                    {
                        "project_id": target.project.project_id,
                        "task_id": str(authority.task["task_id"]),
                    }
                )
                resolution = _resolution_row(
                    current=current,
                    prepared=prepared,
                    captured=captured,
                    resolution_id=generate_runner_id("resolution"),
                    created_at=observed_at,
                )
                if resolution.route == "runner":
                    raise StorageError(
                        "internal_error",
                        "verification Runner launch intent was not persisted",
                    )
                insert_verification_runner_resolution_locked(
                    connection,
                    resolution=resolution,
                )
                observation = _no_launch_observation(
                    resolution,
                    observation_id=generate_runner_id("observation"),
                    observed_at=observed_at,
                )
                reference, link = _terminal_evidence(
                    resolution=resolution,
                    observation=observation,
                    acceptance_criterion_id=authority.acceptance_criterion_id,
                    created_at=observed_at,
                )
                persist_verification_runner_terminal_locked(
                    connection,
                    observation=observation,
                    evidence_reference=reference,
                    criterion_link=link,
                )
            projection = read_verification_runner_public_projection(
                connection,
                project_id=target.project.project_id,
                task_id=str(authority.task["task_id"]),
            )
    return RunnerReviewTargetResult(review=review, verification_runner=projection)


def set_review_target_with_shadow_runner(
    target: DatabaseTarget,
    task_id: Any,
    *,
    kind: Any,
    revision: Any = None,
    _cancel_requested: Callable[[], bool] = lambda: False,
) -> RunnerReviewTargetResult:
    """Run the sole schema-v20 synchronous target-set trigger."""

    normalized_task_id = validate_task_id(task_id)
    if not callable(_cancel_requested):
        raise _service_error(
            "state_inconsistent",
            "verification Runner cancellation boundary is unavailable",
        )
    # Reject an unknown/done Task before creating or locking Runner state.  The
    # later short target transaction rereads this exact observed basis and
    # rejects any concurrent Task/Contract change.
    with closing(connect_initialized_readonly(target)) as connection:
        authority = read_review_target_authority_basis(
            connection,
            target.project,
            normalized_task_id,
        )
    target_kind = validate_choice(
        "review_target_kind",
        kind,
        REVIEW_TARGET_KINDS,
        "invalid_review_evidence",
    )
    if target_kind == "git_snapshot":
        if revision is not None:
            raise review_error(
                "invalid_review_evidence",
                "git_snapshot captures the current staged index and does not accept --revision",
                "review_target_value",
            )
    else:
        # Privacy/shape failures must precede Runner state creation and lock
        # acquisition.  Git resolution/capture remains under the Runner lock.
        validate_revision_review_target_input(target_kind, revision)
    paths = _runner_paths(target)
    try:
        with zero_wait_runner_lock(paths) as inventory:
            with closing(connect_initialized_readonly(target)) as connection:
                pending = read_pending_verification_runner_cleanup(
                    connection,
                    project_id=target.project.project_id,
                )
                require_known_attempt_inventory(
                    inventory,
                    known_attempt_ids=tuple(
                        item["attempt"].verification_runner_attempt_id
                        for item in pending
                    ),
                )
            if pending:
                reconcile_pending_verification_runner_cleanup_under_lock(
                    target,
                    inventory,
                )
                inventory = inspect_runner_layout(paths)
                require_known_attempt_inventory(
                    inventory,
                    known_attempt_ids=(),
                )
            with closing(connect_initialized_readonly(target)) as connection:
                if has_pending_verification_runner_cleanup(
                    connection,
                    project_id=target.project.project_id,
                ):
                    raise _service_error(
                        "runner_cleanup_required",
                        "verification Runner cleanup must complete before setting a target",
                    )
            captured = (
                _CapturedTarget(
                    observation=_capture_ordinary_target(
                        target,
                        kind=target_kind,
                        revision=revision,
                    ),
                    material=None,
                    addressable=False,
                    closed_reason=None,
                )
                if authority.verification_criterion_id is None
                else (
                    _capture_runner_target_for_install(
                        target,
                        kind=target_kind,
                        revision=revision,
                    )
                )
            )
            prepared = (
                None
                if authority.verification_criterion_id is None
                else _prepare_resolution(target, authority, captured)
            )
            return _persist_without_attempt(target, authority, captured, prepared)
    except VerificationRunnerLifecycleError as exc:
        raise _service_error(exc.code, exc.message) from exc
