"""Parent orchestration and exact-current selection for the verification Runner.

The existing review-target command is the sole trigger. Ineligible and
definite pre-intent fallback paths persist only the ordinary target. A Runner
route commits the target, resolution, and attempt intent atomically before any
private tree is created, then publishes terminal audit evidence only after all
process and lifecycle cleanup proofs succeed.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from task_governance_tool import __version__

from task_governance_tool.artifact_manifest import (
    ArtifactManifestError,
    ArtifactObservation,
    observe_git_commit_manifest,
    observe_staged_git_manifest,
    opaque_artifact_observation,
)
from task_governance_tool.evidence_ledger import (
    EvidenceLedgerError,
    EvidenceSource,
    TargetCaptureBinding,
    build_evidence_reference,
)
from task_governance_tool.git_snapshot import GitSnapshotError
from task_governance_tool.project_scope import PREFLIGHT_MESSAGES
from task_governance_tool.reviews import (
    REVIEW_TARGET_KINDS,
    ReviewTargetAuthorityBasis,
    ReviewTargetResult,
    generate_review_id,
    normalize_revision_review_target,
    persist_prepared_review_target_capture,
    read_review_target_authority_basis,
    review_error,
    validate_revision_review_target_input,
)
from task_governance_tool.storage import (
    DatabaseTarget,
    PreparedCriterionEvidenceLink,
    StorageError,
    VerificationRunnerAttempt,
    VerificationRunnerObservation,
    VerificationRunnerResolution,
    VerificationRunnerSandboxEvent,
    begin_initialized_write,
    connect_initialized,
    connect_initialized_readonly,
    connect_initialized_task_readonly,
    insert_verification_runner_resolution_locked,
    persist_verification_runner_restart_cleanup_locked,
    persist_verification_runner_terminal_locked,
    read_current_verification_runner_gate_snapshot,
    read_current_verification_runner_target_basis,
    read_pending_verification_runner_cleanup,
    utc_now,
)
from task_governance_tool.tasks import (
    read_internal_task,
    validate_choice,
    validate_task_id,
)
from task_governance_tool.verification_runner import (
    RUNNER_CONTRACT_VERSION,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_POLICY_DIGEST,
    RUNNER_TRIGGER,
    VerificationRunnerGateSelection,
    generate_runner_id,
    resolution_idempotency_digest,
    runner_observation_source_projection,
    verification_runner_attempt_digest,
    verification_runner_observation_digest,
    verification_runner_sandbox_event_digest,
)
from task_governance_tool.self_status import inspect_local_package
from task_governance_tool.verification_runner_git import (
    TARGET_STALE_MESSAGE,
    RunnerMaterialization,
    RunnerTargetObservation,
    VerificationRunnerGitError,
    materialize_runner_target,
    observe_commit_runner_target,
    observe_staged_runner_target,
    preflight_runner_material,
    preflight_runner_snapshot_successor_material_digest,
)
from task_governance_tool.verification_runner_lifecycle import (
    RUNNER_FAILURE_MESSAGE,
    VerificationRunnerLifecycleError,
    VerificationRunnerStatePaths,
    cleanup_attempt_tree,
    create_attempt_directories,
    create_scratch_directories,
    require_known_attempt_inventory,
    verification_runner_state_paths,
    zero_wait_runner_lock,
)
from task_governance_tool.verification_runner_plan import (
    VerificationRunnerPlanError,
    VerificationRunnerPlanResolution,
    capture_verification_runner_plan,
    resolve_verification_runner_plan,
)
from task_governance_tool.verification_runner_process import (
    RunnerCancelSignal,
    RunnerProcessError,
    RunnerProcessRequestV1,
    RunnerProcessResultV1,
    RunnerProcessStepV1,
    build_clean_environment,
    run_process_request,
)
from task_governance_tool.verification_runner_runtime import (
    RunnerFixedExecutableLease,
    RunnerImplementationIdentity,
    VerificationRunnerRuntimeError,
    capture_runner_implementation,
)


@dataclass
class VerificationRunnerServiceError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class _ReviewTargetRouteResult(ReviewTargetResult):
    verification_route: str
    blocking_code: str | None


_POST_INTENT_TYPED_ERRORS = (
    EvidenceLedgerError,
    StorageError,
    VerificationRunnerLifecycleError,
    VerificationRunnerServiceError,
)


@dataclass(frozen=True)
class _PreparedRunner:
    authority: ReviewTargetAuthorityBasis
    target: RunnerTargetObservation
    material: RunnerMaterialization
    plan: VerificationRunnerPlanResolution
    implementation: RunnerImplementationIdentity


@dataclass(frozen=True)
class _LaunchIntent:
    review: ReviewTargetResult
    resolution: VerificationRunnerResolution
    attempt: VerificationRunnerAttempt
    acceptance_criterion_id: str | None


def _service_error(code: str, message: str) -> VerificationRunnerServiceError:
    return VerificationRunnerServiceError(code=code, message=message)


def _state_invalid() -> VerificationRunnerServiceError:
    return _service_error("runner_state_invalid", RUNNER_FAILURE_MESSAGE)


def _routed_review_target(
    review: ReviewTargetResult,
    *,
    verification_route: str,
    blocking_code: str | None = None,
) -> _ReviewTargetRouteResult:
    return _ReviewTargetRouteResult(
        task=review.task,
        changed_fields=review.changed_fields,
        event=review.event,
        verification_route=verification_route,
        blocking_code=blocking_code,
    )


def _runner_paths(target: DatabaseTarget) -> VerificationRunnerStatePaths:
    return verification_runner_state_paths(target.resolved_verification_runner_root)


def _capture_ordinary_target(
    target: DatabaseTarget,
    *,
    kind: str,
    revision: Any,
) -> ArtifactObservation:
    try:
        if kind == "git_snapshot":
            if revision is not None:
                raise review_error(
                    "invalid_review_evidence",
                    "git_snapshot captures the current staged index and does not accept --revision",
                    "review_target_value",
                )
            return observe_staged_git_manifest(target.project.canonical_repo)
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
    except ArtifactManifestError as exc:
        raise review_error(exc.code, exc.message) from exc
    except GitSnapshotError as exc:
        raise review_error(exc.code, exc.message, exc.field) from exc


def _persist_ordinary_target(
    target: DatabaseTarget,
    authority: ReviewTargetAuthorityBasis,
    observation: ArtifactObservation,
) -> _ReviewTargetRouteResult:
    verification = authority.task.get("verification")
    if not isinstance(verification, str):
        raise _state_invalid()
    verification_route = (
        "receipt_required" if verification.strip() else "not_required"
    )
    with closing(connect_initialized(target)) as connection:
        with connection:
            review = persist_prepared_review_target_capture(
                connection,
                target.project,
                authority.task,
                observation=observation,
                database_target=target,
            )
    return _routed_review_target(
        review,
        verification_route=verification_route,
    )


def _prepare_runner(
    target: DatabaseTarget,
    authority: ReviewTargetAuthorityBasis,
    *,
    kind: str,
    revision: Any,
) -> _PreparedRunner | None:
    if (
        authority.verification_criterion_id is None
        or authority.verification_criterion_digest is None
        or int(authority.task["current_contract_revision"]) <= 0
        or target.skill_root is None
        or kind not in {"git_snapshot", "git_commit"}
    ):
        return None
    repo = Path(target.project.canonical_repo)
    package_root = Path(target.skill_root)
    try:
        source = capture_verification_runner_plan(repo, package_root)
        plan = resolve_verification_runner_plan(
            source,
            task_id=str(authority.task["task_id"]),
            contract_revision=int(authority.task["current_contract_revision"]),
            verification_expectation_digest=(
                authority.verification_expectation_digest
            ),
            verification_criterion_digest=authority.verification_criterion_digest,
        )
    except VerificationRunnerPlanError as exc:
        raise _service_error(exc.code, exc.message) from exc
    if plan.route == "m21_fallback":
        return None

    # Package-integrity failure is a definite pre-intent ineligibility. The
    # ordinary exact target remains usable and no Runner row is created.
    try:
        implementation = capture_runner_implementation(package_root)
    except VerificationRunnerRuntimeError:
        return None

    try:
        observed = (
            observe_staged_runner_target(repo)
            if kind == "git_snapshot"
            else observe_commit_runner_target(
                repo,
                normalize_revision_review_target(
                    target.project,
                    kind,
                    revision,
                )[1],
            )
        )
        material = preflight_runner_material(repo, observed)
    except VerificationRunnerGitError as exc:
        if exc.code == "target_unsupported":
            return None
        raise _service_error(exc.code, exc.message) from exc
    return _PreparedRunner(
        authority=authority,
        target=observed,
        material=material,
        plan=plan,
        implementation=implementation,
    )


def _revalidate_prepared_runner(
    target: DatabaseTarget,
    prepared: _PreparedRunner,
    *,
    kind: str,
    revision: Any,
) -> _PreparedRunner | None:
    """Reapply the complete pre-T1 physical admission matrix under the lock."""

    current = _prepare_runner(
        target,
        prepared.authority,
        kind=kind,
        revision=revision,
    )
    if current is None:
        return None
    if current.target != prepared.target or current.material != prepared.material:
        raise _service_error("target_stale", TARGET_STALE_MESSAGE)
    if (
        current.plan != prepared.plan
        or current.implementation != prepared.implementation
    ):
        raise _state_invalid()
    return current


def _new_runner_id(kind: str) -> str:
    return generate_runner_id(kind, secrets.token_hex(8))


def _resolution_row(
    current: dict[str, Any],
    prepared: _PreparedRunner,
    *,
    created_at: str,
) -> VerificationRunnerResolution:
    plan = prepared.plan
    digest_values = {
        "project_id": prepared.authority.task["project_id"],
        "task_id": prepared.authority.task["task_id"],
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
        "target_base_revision": current["target_base_revision"] or None,
        "target_generation": current["target_generation"],
        "target_capture_version": current["target_capture_version"],
        "artifact_manifest_id": current["artifact_manifest_id"],
        "target_material_digest": prepared.material.target_material_digest,
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
        "runner_implementation_digest": (
            prepared.implementation.implementation_digest
        ),
        "runner_policy_digest": RUNNER_POLICY_DIGEST,
        "sandbox_provider": None,
        "sandbox_policy_digest": None,
        "runtime_digest": None,
        "gate_eligibility_version": 1,
        "trigger": RUNNER_TRIGGER,
        "route": "runner",
        "reason": None,
    }
    storage_values = {
        key: value
        for key, value in digest_values.items()
        if key not in {"sandbox_provider", "sandbox_policy_digest"}
    }
    return VerificationRunnerResolution(
        verification_runner_resolution_id=_new_runner_id("resolution"),
        **storage_values,
        idempotency_digest=resolution_idempotency_digest(digest_values),
        created_at=created_at,
    )


def _attempt_row(
    resolution: VerificationRunnerResolution,
    *,
    created_at: str,
) -> VerificationRunnerAttempt:
    digest_values = {
        "project_id": resolution.project_id,
        "task_id": resolution.task_id,
        "target_generation": resolution.target_generation,
        "gate_eligibility_version": resolution.gate_eligibility_version,
        "resolution_id": resolution.verification_runner_resolution_id,
        "target_material_digest": resolution.target_material_digest,
        "runner_implementation_digest": resolution.runner_implementation_digest,
        "sandbox_instance_digest": None,
    }
    return VerificationRunnerAttempt(
        verification_runner_attempt_id=_new_runner_id("attempt"),
        project_id=resolution.project_id,
        task_id=resolution.task_id,
        target_generation=resolution.target_generation,
        gate_eligibility_version=resolution.gate_eligibility_version,
        verification_runner_resolution_id=(
            resolution.verification_runner_resolution_id
        ),
        target_material_digest=str(resolution.target_material_digest),
        runner_implementation_digest=resolution.runner_implementation_digest,
        attempt_digest=verification_runner_attempt_digest(digest_values),
        intent_recorded_at=created_at,
    )


def _persist_launch_intent(
    target: DatabaseTarget,
    prepared: _PreparedRunner,
) -> _LaunchIntent:
    created_at = utc_now()
    with closing(connect_initialized(target)) as connection:
        with connection:
            review = persist_prepared_review_target_capture(
                connection,
                target.project,
                prepared.authority.task,
                observation=prepared.target.artifact,
                database_target=target,
                now=created_at,
                runner_basis_version=2,
            )
            current = read_current_verification_runner_target_basis(
                connection,
                project_id=target.project.project_id,
                task_id=str(prepared.authority.task["task_id"]),
            )
            resolution = _resolution_row(current, prepared, created_at=created_at)
            attempt = _attempt_row(resolution, created_at=created_at)
            insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
                attempt=attempt,
            )
    return _LaunchIntent(
        review=review,
        resolution=resolution,
        attempt=attempt,
        acceptance_criterion_id=prepared.authority.acceptance_criterion_id,
    )


def _cleanup_event(
    attempt: VerificationRunnerAttempt,
    *,
    created_at: str,
    terminal_observation_id: str | None,
) -> VerificationRunnerSandboxEvent:
    values = {
        "attempt_id": attempt.verification_runner_attempt_id,
        "event_kind": "attempt_cleanup_succeeded",
        "project_id": attempt.project_id,
        "target_generation": attempt.target_generation,
        "task_id": attempt.task_id,
        "terminal_observation_id": terminal_observation_id,
    }
    return VerificationRunnerSandboxEvent(
        verification_runner_sandbox_event_id=_new_runner_id("sandbox_event"),
        project_id=attempt.project_id,
        task_id=attempt.task_id,
        target_generation=attempt.target_generation,
        verification_runner_attempt_id=(
            attempt.verification_runner_attempt_id
        ),
        event_kind="attempt_cleanup_succeeded",
        event_digest=verification_runner_sandbox_event_digest(values),
        terminal_observation_id=terminal_observation_id,
        created_at=created_at,
    )


def _observation_row(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    *,
    route: str,
    launch_state: str,
    outcome: str,
    reason: str | None,
    complete_plan: int,
    completed_step_count: int,
    failed_step_ordinal: int | None,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    cpu_time_ms: int | None,
    peak_job_memory_bytes: int | None,
    total_process_count: int | None,
) -> VerificationRunnerObservation:
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
        "total_step_count": resolution.step_count,
    }
    return VerificationRunnerObservation(
        verification_runner_observation_id=_new_runner_id("observation"),
        project_id=resolution.project_id,
        task_id=resolution.task_id,
        target_generation=resolution.target_generation,
        gate_eligibility_version=resolution.gate_eligibility_version,
        verification_runner_resolution_id=(
            resolution.verification_runner_resolution_id
        ),
        verification_runner_attempt_id=(attempt.verification_runner_attempt_id),
        runner_implementation_digest=resolution.runner_implementation_digest,
        route=route,
        launch_state=launch_state,
        outcome=outcome,
        reason=reason,
        complete_plan=complete_plan,
        total_step_count=resolution.step_count,
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


def _terminal_evidence(
    resolution: VerificationRunnerResolution,
    observation: VerificationRunnerObservation,
    *,
    acceptance_criterion_id: str | None,
) -> tuple[dict[str, Any], PreparedCriterionEvidenceLink]:
    source = EvidenceSource(
        source_kind="runner_observation",
        source_state="recorded",
        source_id=observation.verification_runner_observation_id,
        source_projection=runner_observation_source_projection(
            observation=asdict(observation),
            resolution=asdict(resolution),
        ),
        _validated_runner_eligibility_version=(
            observation.gate_eligibility_version
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
        "created_at": observation.created_at,
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
        created_at=observation.created_at,
    )
    return reference, link


def _persist_terminal(
    target: DatabaseTarget,
    intent: _LaunchIntent,
    observation: VerificationRunnerObservation,
) -> None:
    event = _cleanup_event(
        intent.attempt,
        created_at=observation.created_at,
        terminal_observation_id=observation.verification_runner_observation_id,
    )
    reference, link = _terminal_evidence(
        intent.resolution,
        observation,
        acceptance_criterion_id=intent.acceptance_criterion_id,
    )
    try:
        with closing(connect_initialized(target)) as connection:
            with connection:
                begin_initialized_write(connection, target)
                persist_verification_runner_terminal_locked(
                    connection,
                    observation=observation,
                    evidence_reference=reference,
                    criterion_link=link,
                    cleanup_event=event,
                )
    except StorageError as exc:
        if exc.code != "runner_state_invalid":
            raise
        # T2 rejected a definite current-basis drift after the private tree was
        # already proved absent. Record cleanup-only in a new transaction; any
        # uncertainty while doing so deliberately leaves the attempt pending.
        _persist_cleanup_only(target, intent.attempt)
        raise _state_invalid() from exc


def _persist_cleanup_only(
    target: DatabaseTarget,
    attempt: VerificationRunnerAttempt,
) -> None:
    event = _cleanup_event(
        attempt,
        created_at=utc_now(),
        terminal_observation_id=None,
    )
    with closing(connect_initialized(target)) as connection:
        with connection:
            begin_initialized_write(connection, target)
            persist_verification_runner_restart_cleanup_locked(
                connection,
                cleanup_event=event,
            )


def _cleanup_or_fail(
    target: DatabaseTarget,
    paths: VerificationRunnerStatePaths,
    attempt: VerificationRunnerAttempt,
    *,
    persist_only: bool,
) -> None:
    cleanup = cleanup_attempt_tree(
        paths,
        attempt.verification_runner_attempt_id,
    )
    if cleanup.state != "absent":
        raise _state_invalid()
    if persist_only:
        _persist_cleanup_only(target, attempt)


def _complete_prelaunch(
    target: DatabaseTarget,
    paths: VerificationRunnerStatePaths,
    prepared: _PreparedRunner,
    intent: _LaunchIntent,
    *,
    reason: str,
) -> _ReviewTargetRouteResult:
    _cleanup_or_fail(target, paths, intent.attempt, persist_only=False)
    if not _physical_basis_matches(target, prepared):
        _persist_cleanup_only(target, intent.attempt)
        raise _state_invalid()
    observed_at = utc_now()
    observation = _observation_row(
        intent.resolution,
        intent.attempt,
        route="m21_fallback",
        launch_state="no_launch",
        outcome="blocked_prelaunch",
        reason=reason,
        complete_plan=0,
        completed_step_count=0,
        failed_step_ordinal=None,
        started_at=observed_at,
        finished_at=observed_at,
        duration_ms=0,
        cpu_time_ms=None,
        peak_job_memory_bytes=None,
        total_process_count=None,
    )
    _persist_terminal(target, intent, observation)
    return _routed_runner_target(intent.review, intent.resolution, observation)


def _current_basis_matches(
    target: DatabaseTarget,
    prepared: _PreparedRunner,
    resolution: VerificationRunnerResolution,
) -> bool:
    with closing(connect_initialized_readonly(target)) as connection:
        current = read_current_verification_runner_target_basis(
            connection,
            project_id=resolution.project_id,
            task_id=resolution.task_id,
        )
    expected = {
        "contract_revision": resolution.contract_revision,
        "authority_snapshot_id": resolution.authority_snapshot_id,
        "acceptance_criterion_id": prepared.authority.acceptance_criterion_id,
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
    return current == expected


def _physical_basis_matches(
    target: DatabaseTarget,
    prepared: _PreparedRunner,
) -> bool:
    if target.skill_root is None:
        return False
    repo = Path(target.project.canonical_repo)
    package_root = Path(target.skill_root)
    try:
        source = capture_verification_runner_plan(repo, package_root)
        plan = resolve_verification_runner_plan(
            source,
            task_id=str(prepared.authority.task["task_id"]),
            contract_revision=int(
                prepared.authority.task["current_contract_revision"]
            ),
            verification_expectation_digest=(
                prepared.authority.verification_expectation_digest
            ),
            verification_criterion_digest=str(
                prepared.authority.verification_criterion_digest
            ),
        )
        implementation = capture_runner_implementation(package_root)
        observed = (
            observe_staged_runner_target(repo)
            if prepared.target.artifact.target_kind == "git_snapshot"
            else observe_commit_runner_target(
                repo,
                prepared.target.artifact.target_value,
            )
        )
        material = preflight_runner_material(repo, observed)
    except (
        VerificationRunnerGitError,
        VerificationRunnerPlanError,
        VerificationRunnerRuntimeError,
    ):
        return False
    return bool(
        plan == prepared.plan
        and implementation.implementation_digest
        == prepared.implementation.implementation_digest
        and observed == prepared.target
        and material == prepared.material
    )


def _basis_is_current(
    target: DatabaseTarget,
    prepared: _PreparedRunner,
    resolution: VerificationRunnerResolution,
) -> bool:
    return _current_basis_matches(target, prepared, resolution) and (
        _physical_basis_matches(target, prepared)
    )


def _selection_from_snapshot(
    snapshot: dict[str, Any],
    *,
    project_id: str,
    task_id: str,
    mode: str,
) -> VerificationRunnerGateSelection:
    observation = snapshot["observation"]
    return VerificationRunnerGateSelection(
        project_id=project_id,
        task_id=task_id,
        target_generation=int(snapshot["target_generation"]),
        mode=mode,
        verification_runner_observation_id=(
            observation.verification_runner_observation_id
            if observation is not None and mode != "stale"
            else None
        ),
        storage_token=tuple(snapshot["storage_token"]),
    )


def _stored_runner_physical_basis_matches(
    target: DatabaseTarget,
    snapshot: dict[str, Any],
    *,
    completion_revision: str | None = None,
) -> bool:
    if target.skill_root is None:
        raise StorageError(
            "package_status_unknown",
            PREFLIGHT_MESSAGES["package_status_unknown"],
        )
    package_status = inspect_local_package(
        target.skill_root,
        installed_version=__version__,
    )
    if package_status.status == "modified":
        raise StorageError(
            "package_core_modified",
            PREFLIGHT_MESSAGES["package_core_modified"],
        )
    if package_status.status != "clean":
        raise StorageError(
            "package_status_unknown",
            PREFLIGHT_MESSAGES["package_status_unknown"],
        )

    resolution = snapshot["resolution"]
    repo = Path(target.project.canonical_repo)
    package_root = Path(target.skill_root)
    try:
        source = capture_verification_runner_plan(repo, package_root)
        plan = resolve_verification_runner_plan(
            source,
            task_id=resolution.task_id,
            contract_revision=resolution.contract_revision,
            verification_expectation_digest=(
                resolution.verification_expectation_digest
            ),
            verification_criterion_digest=(
                resolution.verification_criterion_digest
            ),
        )
        implementation = capture_runner_implementation(package_root)
        if resolution.target_kind == "git_snapshot":
            if completion_revision is None:
                observed = observe_staged_runner_target(repo)
                material = preflight_runner_material(repo, observed)
                target_matches = bool(
                    observed.artifact.target_kind == resolution.target_kind
                    and observed.artifact.target_value == resolution.target_value
                    and observed.artifact.target_base_revision
                    == (resolution.target_base_revision or "")
                    and material.target_material_digest
                    == resolution.target_material_digest
                )
            elif (
                type(resolution.target_base_revision) is not str
                or not resolution.target_base_revision
            ):
                target_matches = False
            else:
                successor_digest = (
                    preflight_runner_snapshot_successor_material_digest(
                        repo,
                        completion_revision,
                        expected_base_revision=resolution.target_base_revision,
                        expected_fingerprint=resolution.target_value,
                    )
                )
                target_matches = (
                    successor_digest == resolution.target_material_digest
                )
        else:
            observed = observe_commit_runner_target(repo, resolution.target_value)
            material = preflight_runner_material(repo, observed)
            target_matches = bool(
                (completion_revision is None or completion_revision == resolution.target_value)
                and observed.artifact.target_kind == resolution.target_kind
                and observed.artifact.target_value == resolution.target_value
                and observed.artifact.target_base_revision
                == (resolution.target_base_revision or "")
                and material.target_material_digest
                == resolution.target_material_digest
            )
    except VerificationRunnerRuntimeError as exc:
        raise StorageError(
            "package_status_unknown",
            PREFLIGHT_MESSAGES["package_status_unknown"],
        ) from exc
    except (VerificationRunnerPlanError, VerificationRunnerGitError):
        return False
    return bool(
        plan.plan_state == resolution.plan_state
        and plan.route == resolution.route
        and plan.reason == resolution.reason
        and plan.plan_blob_object_id == resolution.plan_blob_object_id
        and plan.plan_raw_digest == resolution.plan_raw_digest
        and plan.plan_id == resolution.plan_id
        and plan.plan_version == resolution.plan_version
        and plan.plan_semantic_digest == resolution.plan_semantic_digest
        and plan.selected_entry_digest == resolution.selected_entry_digest
        and plan.coverage == resolution.coverage
        and plan.step_count == resolution.step_count
        and implementation.implementation_version
        == resolution.runner_implementation_version
        and implementation.implementation_digest
        == resolution.runner_implementation_digest
        and target_matches
    )


def _terminal_runner_mode(
    resolution: VerificationRunnerResolution,
    observation: VerificationRunnerObservation,
) -> str:
    if (
        observation.route == "m21_fallback"
        and observation.launch_state == "no_launch"
        and observation.outcome == "blocked_prelaunch"
        and observation.reason in {"runtime_unavailable", "process_setup_failed"}
        and observation.complete_plan == 0
    ):
        return "m21_fallback"
    if (
        observation.route == "runner"
        and observation.launch_state == "launched"
        and observation.outcome == "pass"
        and observation.reason is None
        and observation.complete_plan == 1
        and observation.total_step_count
        == observation.completed_step_count
        == resolution.step_count
        and observation.failed_step_ordinal is None
    ):
        return "runner_observation"
    return "blocking"


def _routed_runner_target(
    review: ReviewTargetResult,
    resolution: VerificationRunnerResolution,
    observation: VerificationRunnerObservation,
) -> _ReviewTargetRouteResult:
    mode = _terminal_runner_mode(resolution, observation)
    if mode == "m21_fallback":
        return _routed_review_target(
            review,
            verification_route="receipt_required",
        )
    if mode == "runner_observation":
        return _routed_review_target(
            review,
            verification_route="runner_pass",
        )
    return _routed_review_target(
        review,
        verification_route="blocked",
        blocking_code="verification_receipt_blocking",
    )


def select_current_verification_runner_basis(
    target: DatabaseTarget,
    *,
    task: Mapping[str, Any],
    completion_revision: str | None = None,
) -> VerificationRunnerGateSelection | None:
    """Select one live Runner gate without holding SQLite during physical checks."""

    if str(task.get("status", "")) == "done":
        return None
    try:
        marker = task["review_target_runner_basis_version"]
        project_id = str(task["project_id"])
        task_id = str(task["task_id"])
        target_generation = int(task["review_target_generation"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(
            "runner_state_invalid",
            RUNNER_FAILURE_MESSAGE,
        ) from exc
    if marker == 0:
        return None
    if marker != 2 or project_id != target.project.project_id:
        raise StorageError("runner_state_invalid", RUNNER_FAILURE_MESSAGE)

    caller_task = dict(task)
    with closing(connect_initialized_task_readonly(target)) as connection:
        current_task = read_internal_task(connection, project_id, task_id)
        if current_task is None or current_task != caller_task:
            return None
        snapshot = read_current_verification_runner_gate_snapshot(
            connection,
            project_id=project_id,
            task_id=task_id,
        )
    if snapshot["target_generation"] != target_generation:
        raise StorageError("runner_state_invalid", RUNNER_FAILURE_MESSAGE)
    if not snapshot["basis_matches"]:
        return _selection_from_snapshot(
            snapshot,
            project_id=project_id,
            task_id=task_id,
            mode="stale",
        )

    physical_error: StorageError | None = None
    try:
        physical_matches = (
            _stored_runner_physical_basis_matches(target, snapshot)
            if completion_revision is None
            else _stored_runner_physical_basis_matches(
                target,
                snapshot,
                completion_revision=completion_revision,
            )
        )
    except StorageError as exc:
        physical_error = exc
        physical_matches = False

    with closing(connect_initialized_task_readonly(target)) as connection:
        current_task = read_internal_task(connection, project_id, task_id)
        if current_task is None or current_task != caller_task:
            return None
        current = read_current_verification_runner_gate_snapshot(
            connection,
            project_id=project_id,
            task_id=task_id,
        )
    if (
        not current["basis_matches"]
        or current["storage_token"] != snapshot["storage_token"]
    ):
        return _selection_from_snapshot(
            current,
            project_id=project_id,
            task_id=task_id,
            mode="stale",
        )
    if physical_error is not None:
        raise physical_error
    if not physical_matches:
        return _selection_from_snapshot(
            current,
            project_id=project_id,
            task_id=task_id,
            mode="stale",
        )
    if current["state"] != "terminal":
        mode = "stale"
    else:
        mode = _terminal_runner_mode(
            current["resolution"],
            current["observation"],
        )
    return _selection_from_snapshot(
        current,
        project_id=project_id,
        task_id=task_id,
        mode=mode,
    )


def _process_steps(
    plan: VerificationRunnerPlanResolution,
) -> tuple[RunnerProcessStepV1, ...]:
    return tuple(
        RunnerProcessStepV1(
            ordinal=step.ordinal,
            step_id=step.step_id,
            mode=step.mode,
            entrypoint=step.entrypoint,
            argv=step.argv,
            cwd=step.cwd,
            shell=False,
            path_lookup=False,
            timeout_seconds=step.timeout_seconds,
            cpu_seconds=step.cpu_seconds,
            memory_mib=step.memory_mib,
            process_limit=step.process_limit,
            output_byte_limit=step.output_byte_limit,
        )
        for step in plan.steps
    )


def _terminal_from_process(
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
    result: RunnerProcessResultV1,
    *,
    started_at: str,
    finished_at: str,
) -> VerificationRunnerObservation:
    completed = len(result.steps)
    complete_plan = int(
        result.outcome == "pass"
        and result.reason is None
        and result.launch_state == "launched"
        and completed == resolution.step_count
        and tuple(step.ordinal for step in result.steps)
        == tuple(range(1, resolution.step_count + 1))
        and all(
            step.outcome == "pass"
            and step.reason is None
            and step.launch_state == "launched"
            for step in result.steps
        )
        and result.failed_step_ordinal is None
    )
    return _observation_row(
        resolution,
        attempt,
        route=("runner" if result.launch_state == "launched" else "m21_fallback"),
        launch_state=result.launch_state,
        outcome=result.outcome,
        reason=result.reason,
        complete_plan=complete_plan,
        completed_step_count=completed,
        failed_step_ordinal=result.failed_step_ordinal,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=result.duration_ms,
        cpu_time_ms=result.cpu_time_ms,
        peak_job_memory_bytes=result.peak_job_memory_bytes,
        total_process_count=result.total_process_count,
    )


def _process_result_matches_request(
    request: RunnerProcessRequestV1,
    result: object,
) -> bool:
    """Recheck the adapter ownership and ordinal prefix at the service seam."""

    if type(result) is not RunnerProcessResultV1:
        return False
    expected_prefix = request.steps[: len(result.steps)]
    return bool(
        result.version == request.version
        and result.attempt_id == request.attempt_id
        and tuple(step.ordinal for step in result.steps)
        == tuple(step.ordinal for step in expected_prefix)
        and (
            result.failed_step_ordinal is None
            or result.failed_step_ordinal
            in {step.ordinal for step in request.steps}
        )
    )


def _deny_unresolved_attempt(
    target: DatabaseTarget,
    paths: VerificationRunnerStatePaths,
    inventory: Any,
) -> None:
    try:
        with closing(connect_initialized_readonly(target)) as connection:
            pending = read_pending_verification_runner_cleanup(
                connection,
                project_id=target.project.project_id,
            )
        known_ids = tuple(
            item["attempt"].verification_runner_attempt_id for item in pending
        )
        require_known_attempt_inventory(inventory, known_attempt_ids=known_ids)
        if not pending:
            return
        if len(pending) != 1:
            raise _state_invalid()
        item = pending[0]
        if item["state"] == "pending":
            _cleanup_or_fail(
                target,
                paths,
                item["attempt"],
                persist_only=True,
            )
        raise _state_invalid()
    except _POST_INTENT_TYPED_ERRORS:
        raise
    except BaseException as exc:
        # A pending row belongs to an earlier committed T1. Restart cleanup is
        # therefore the same sanitized post-intent failure boundary.
        raise _state_invalid() from exc


def _run_intent_under_lock(
    target: DatabaseTarget,
    paths: VerificationRunnerStatePaths,
    prepared: _PreparedRunner,
    intent: _LaunchIntent,
    *,
    cancel_requested: Callable[[], bool],
) -> _ReviewTargetRouteResult:
    try:
        attempt_paths = create_attempt_directories(
            paths,
            intent.attempt.verification_runner_attempt_id,
        )
        create_scratch_directories(
            paths,
            intent.attempt.verification_runner_attempt_id,
        )
    except VerificationRunnerLifecycleError:
        return _complete_prelaunch(
            target,
            paths,
            prepared,
            intent,
            reason="process_setup_failed",
        )
    except BaseException as exc:
        raise _state_invalid() from exc

    try:
        materialize_runner_target(
            Path(target.project.canonical_repo),
            prepared.material,
            attempt_paths.target,
        )
    except VerificationRunnerGitError as exc:
        if exc.code in {"target_stale", "object_drift"}:
            _cleanup_or_fail(target, paths, intent.attempt, persist_only=True)
            raise _state_invalid() from exc
        return _complete_prelaunch(
            target,
            paths,
            prepared,
            intent,
            reason="process_setup_failed",
        )
    except BaseException as exc:
        raise _state_invalid() from exc

    try:
        basis_current = _basis_is_current(target, prepared, intent.resolution)
    except StorageError:
        raise
    except BaseException as exc:
        raise _state_invalid() from exc
    if not basis_current:
        _cleanup_or_fail(target, paths, intent.attempt, persist_only=True)
        raise _state_invalid()

    try:
        requested = cancel_requested()
    except BaseException as exc:
        # The callback is not a process-boundary owner. Even a look-alike
        # RunnerProcessError from it is an unknown pre-adapter failure.
        raise _state_invalid() from exc
    try:
        if type(requested) is not bool:
            raise RunnerProcessError("process_setup_failed")
        cancel_signal = RunnerCancelSignal(requested)
        system_root_value = os.environ.get("SystemRoot")
        if not system_root_value:
            raise RunnerProcessError("process_setup_failed")
        steps = _process_steps(prepared.plan)
    except RunnerProcessError:
        return _complete_prelaunch(
            target,
            paths,
            prepared,
            intent,
            reason="process_setup_failed",
        )
    except BaseException as exc:
        raise _state_invalid() from exc

    runtime_bound = False
    process_entered = False
    try:
        lease = RunnerFixedExecutableLease(
            attempt_paths.target,
            attempt_paths.scratch,
        )
    except BaseException as exc:
        # Construction is resource-free. An interruption here therefore has
        # nothing to clean up, but it is still an unknown post-intent failure.
        raise _state_invalid() from exc
    prelaunch_reason: str | None = None
    pending_failure: BaseException | None = None
    request: RunnerProcessRequestV1 | None = None
    result: RunnerProcessResultV1 | None = None
    started_at = utc_now()
    try:
        with lease as executable:
            runtime_bound = True
            clean_environment = build_clean_environment(
                Path(system_root_value),
                attempt_paths.scratch,
            )
            request = RunnerProcessRequestV1(
                version=RUNNER_CONTRACT_VERSION,
                attempt_id=intent.attempt.verification_runner_attempt_id,
                executable=executable,
                materialized_root=attempt_paths.target,
                scratch_root=attempt_paths.scratch,
                clean_environment=clean_environment,
                steps=steps,
                cancel_signal=cancel_signal,
            )
            process_entered = True
            result = run_process_request(request)
    except VerificationRunnerRuntimeError as exc:
        if not runtime_bound and exc.handle_cleanup_state != "uncertain":
            prelaunch_reason = "runtime_unavailable"
        else:
            pending_failure = exc
    except RunnerProcessError as exc:
        if not process_entered:
            prelaunch_reason = "process_setup_failed"
        else:
            pending_failure = exc
    except BaseException as exc:
        # Unknown errors and interruptions are never promoted to a definite
        # no-launch observation, even when they happened before adapter entry.
        pending_failure = exc
    finally:
        try:
            cleanup_state = lease.finalize_owner()
        except BaseException as exc:
            raise _state_invalid() from exc
        if cleanup_state != "closed":
            raise _state_invalid()

    if pending_failure is not None:
        raise _state_invalid() from pending_failure
    if prelaunch_reason is not None:
        return _complete_prelaunch(
            target,
            paths,
            prepared,
            intent,
            reason=prelaunch_reason,
        )
    if request is None or result is None:
        raise _state_invalid()
    if not _process_result_matches_request(request, result):
        raise _state_invalid()
    if not (
        result.process_zero
        and result.handles_closed
        and result.raw_output_discarded
    ):
        raise _state_invalid()
    try:
        basis_current = _basis_is_current(target, prepared, intent.resolution)
    except StorageError:
        raise
    except BaseException as exc:
        raise _state_invalid() from exc
    cleanup = cleanup_attempt_tree(
        paths,
        intent.attempt.verification_runner_attempt_id,
    )
    if cleanup.state != "absent":
        raise _state_invalid()
    if not basis_current or not _physical_basis_matches(target, prepared):
        _persist_cleanup_only(target, intent.attempt)
        raise _state_invalid()
    observation = _terminal_from_process(
        intent.resolution,
        intent.attempt,
        result,
        started_at=started_at,
        finished_at=utc_now(),
    )
    _persist_terminal(target, intent, observation)
    return _routed_runner_target(intent.review, intent.resolution, observation)


def set_review_target_with_shadow_runner(
    target: DatabaseTarget,
    task_id: Any,
    *,
    kind: Any,
    revision: Any = None,
    _cancel_requested: Callable[[], bool] = lambda: False,
) -> _ReviewTargetRouteResult:
    """Set one exact review target and optionally consume the local Runner plan."""

    normalized_task_id = validate_task_id(task_id)
    if not callable(_cancel_requested):
        raise _state_invalid()
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
        normalized_revision = None
    else:
        validate_revision_review_target_input(target_kind, revision)
        _normalized_kind, normalized_revision = normalize_revision_review_target(
            target.project,
            target_kind,
            revision,
        )

    prepared = _prepare_runner(
        target,
        authority,
        kind=target_kind,
        revision=normalized_revision,
    )
    if prepared is None:
        return _persist_ordinary_target(
            target,
            authority,
            _capture_ordinary_target(
                target,
                kind=target_kind,
                revision=normalized_revision,
            ),
        )

    paths = _runner_paths(target)
    try:
        with zero_wait_runner_lock(paths) as inventory:
            _deny_unresolved_attempt(target, paths, inventory)
            current_prepared = _revalidate_prepared_runner(
                target,
                prepared,
                kind=target_kind,
                revision=normalized_revision,
            )
            if current_prepared is None:
                return _persist_ordinary_target(
                    target,
                    authority,
                    _capture_ordinary_target(
                        target,
                        kind=target_kind,
                        revision=normalized_revision,
                    ),
                )
            intent = _persist_launch_intent(target, current_prepared)
            try:
                return _run_intent_under_lock(
                    target,
                    paths,
                    current_prepared,
                    intent,
                    cancel_requested=_cancel_requested,
                )
            except _POST_INTENT_TYPED_ERRORS:
                # Preserve the established typed error translations below and
                # every existing storage/service code.
                raise
            except BaseException as exc:
                # T1 is already committed. No unexpected cleanup, basis, or T2
                # fault may escape with raw exception or private-path detail.
                raise _state_invalid() from exc
    except VerificationRunnerLifecycleError as exc:
        raise _service_error(exc.code, exc.message) from exc
    except EvidenceLedgerError as exc:
        raise StorageError(exc.code, exc.message) from exc


__all__ = [
    "VerificationRunnerServiceError",
    "select_current_verification_runner_basis",
    "set_review_target_with_shadow_runner",
]
