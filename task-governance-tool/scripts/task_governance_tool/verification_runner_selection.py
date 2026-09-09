"""Exact-current Runner selection without process execution.

Selection closes its read connection before physical basis checks, then rereads
the stored basis. Launch orchestration reuses the same terminal classification.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from task_governance_tool import __version__
from task_governance_tool.project_scope import PREFLIGHT_MESSAGES
from task_governance_tool.self_status import inspect_local_package
from task_governance_tool.storage import (
    DatabaseTarget,
    StorageError,
    connect_initialized_task_readonly,
)
from task_governance_tool.tasks import read_internal_task
from task_governance_tool.verification_runner import VerificationRunnerGateSelection
from task_governance_tool.verification_runner_repository import (
    VerificationRunnerObservation,
    VerificationRunnerResolution,
    read_current_verification_runner_gate_snapshot,
)
from task_governance_tool.verification_runner_git import (
    VerificationRunnerGitError,
    observe_commit_runner_target,
    observe_staged_runner_target,
    preflight_runner_material,
    preflight_runner_snapshot_successor_material_digest,
)
from task_governance_tool.verification_runner_lifecycle import RUNNER_FAILURE_MESSAGE
from task_governance_tool.verification_runner_plan import (
    VerificationRunnerPlanError,
    capture_verification_runner_plan,
    resolve_verification_runner_plan,
)
from task_governance_tool.verification_runner_runtime import (
    VerificationRunnerRuntimeError,
    capture_runner_implementation,
    current_runner_policy_digest,
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
        and current_runner_policy_digest() == resolution.runner_policy_digest
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


__all__ = ["select_current_verification_runner_basis"]
