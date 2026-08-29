"""Completion check/write orchestration across short SQLite and Git phases."""

from __future__ import annotations

import sqlite3
from contextlib import closing, nullcontext
from dataclasses import dataclass, field, replace

from task_governance_tool.completion import CompletionRequest
from task_governance_tool.effort import EffortProfile
from task_governance_tool.storage import (
    DatabaseTarget,
    connect_initialized,
    connect_initialized_readonly,
)
from task_governance_tool.tasks import (
    CompletionBasis,
    CompletionPlan,
    EditTaskResult,
    RunnerSelectionProvider,
    TaskRepositoryError,
    TaskValidationError,
    capture_completion_basis,
    complete_task,
    prepare_completion_plan,
    reject_concurrent_edit_base_change,
    validate_completion_basic_prerequisites,
    validate_completion_plan_basis,
    validate_completion_selector_prerequisites,
    validate_completion_state_basis,
)
from task_governance_tool.verification_runner import VerificationRunnerGateSelection


COMPLETION_BLOCKING_CODES = (
    "invalid_status_transition",
    "sequential_predecessor_incomplete",
    "verification_required",
    "review_required",
    "completion_evidence_conflict",
    "external_revision_approval_required",
    "commit_required",
    "git_commit_not_found_or_ambiguous",
    "invalid_review_evidence",
    "review_target_required",
    "evidence_basis_stale",
    "verification_receipt_required",
    "verification_receipt_blocking",
    "review_target_mismatch",
    "review_finding_unresolved",
    "review_changes_requested",
    "review_receipts_insufficient",
    "completion_check_stale",
)


@dataclass(frozen=True)
class CompletionCheckOutcome:
    basis: CompletionBasis = field(repr=False)
    plan: CompletionPlan | None = field(repr=False)
    blocking_code: str | None


def _capture_preselector_basis(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    request: CompletionRequest,
    *,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
    selector_position: bool,
) -> tuple[CompletionBasis, TaskValidationError | TaskRepositoryError | None]:
    preliminary = capture_completion_basis(
        connection,
        target.project,
        request.task_id,
    )
    try:
        if selector_position:
            validate_completion_selector_prerequisites(
                preliminary,
                request,
                input_error=input_error,
            )
        else:
            validate_completion_basic_prerequisites(
                preliminary,
                request,
                input_error=input_error,
            )
    except (TaskValidationError, TaskRepositoryError) as exc:
        return preliminary, exc
    return preliminary, None


def _capture_selected_completion_basis(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    request: CompletionRequest,
    selection: VerificationRunnerGateSelection | None,
) -> CompletionBasis:
    return capture_completion_basis(
        connection,
        target.project,
        request.task_id,
        runner_selection=selection,
    )


def prepare_request_against_basis(
    basis: CompletionBasis,
    target: DatabaseTarget,
    request: CompletionRequest,
    *,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
    defer_gate_validation: bool = False,
) -> CompletionPlan:
    """Run the same state-first completion preflight for check and write."""
    if input_error is not None:
        validate_completion_state_basis(basis)
        raise input_error
    return prepare_completion_plan(
        basis,
        target.project,
        request,
        defer_gate_validation=defer_gate_validation,
    )


def check_completion_request(
    target: DatabaseTarget,
    request: CompletionRequest,
    *,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
    initial_connection: sqlite3.Connection | None = None,
    runner_selector: RunnerSelectionProvider | None = None,
) -> CompletionCheckOutcome:
    """Check one request with read/close/Git/read and no stored authority."""
    manager = (
        nullcontext(initial_connection)
        if initial_connection is not None
        else closing(connect_initialized_readonly(target))
    )
    with manager as connection:
        first_preselector_basis, preflight_error = (
            _capture_preselector_basis(
                connection,
                target,
                request,
                input_error=input_error,
                selector_position=False,
            )
        )
    if initial_connection is not None:
        initial_connection.close()

    plan: CompletionPlan | None = None
    if preflight_error is None:
        try:
            plan = prepare_request_against_basis(
                first_preselector_basis,
                target,
                request,
                defer_gate_validation=(
                    first_preselector_basis.task.get(
                        "review_target_runner_basis_version", 0
                    )
                    == 2
                ),
            )
        except (TaskValidationError, TaskRepositoryError) as exc:
            preflight_error = exc

    with closing(connect_initialized_readonly(target)) as connection:
        second_preselector_basis, second_preflight_error = (
            _capture_preselector_basis(
                connection,
                target,
                request,
                input_error=input_error,
                selector_position=True,
            )
        )
    second_basis = second_preselector_basis
    if preflight_error is None and second_preflight_error is not None:
        preflight_error = second_preflight_error

    if (
        second_preselector_basis.semantic_token
        != first_preselector_basis.semantic_token
    ):
        return CompletionCheckOutcome(
            basis=second_basis,
            plan=plan,
            blocking_code="completion_check_stale",
        )
    if preflight_error is not None:
        if preflight_error.code not in COMPLETION_BLOCKING_CODES:
            raise preflight_error
        return CompletionCheckOutcome(
            basis=second_basis,
            plan=plan,
            blocking_code=preflight_error.code,
        )
    if plan is None:
        raise TaskRepositoryError(
            "internal_error",
            "completion preflight did not produce a result",
        )

    final_basis = second_basis
    if second_preselector_basis.task.get(
        "review_target_runner_basis_version", 0
    ) == 2:
        runner_selection = (
            runner_selector(
                second_preselector_basis.task,
                plan.resolution.completion_evidence_revision,
            )
            if runner_selector is not None
            else None
        )
        with closing(connect_initialized_readonly(target)) as connection:
            final_preselector_basis, final_preflight_error = (
                _capture_preselector_basis(
                    connection,
                    target,
                    request,
                    input_error=input_error,
                    selector_position=True,
                )
            )
            if (
                final_preflight_error is None
                and final_preselector_basis.semantic_token
                == second_preselector_basis.semantic_token
            ):
                final_basis = _capture_selected_completion_basis(
                    connection,
                    target,
                    request,
                    runner_selection,
                )
            else:
                final_basis = final_preselector_basis
        if (
            final_preflight_error is not None
            or final_preselector_basis.semantic_token
            != second_preselector_basis.semantic_token
        ):
            return CompletionCheckOutcome(
                basis=final_basis,
                plan=plan,
                blocking_code="completion_check_stale",
            )

    plan = replace(plan, basis=final_basis)
    try:
        validate_completion_plan_basis(
            plan,
            final_basis,
            stale_code="completion_check_stale",
        )
    except (TaskValidationError, TaskRepositoryError) as exc:
        if exc.code not in COMPLETION_BLOCKING_CODES:
            raise
        return CompletionCheckOutcome(
            basis=final_basis,
            plan=plan,
            blocking_code=exc.code,
        )
    return CompletionCheckOutcome(
        basis=final_basis,
        plan=plan,
        blocking_code=None,
    )


def execute_completion_request(
    target: DatabaseTarget,
    request: CompletionRequest,
    *,
    effort_profile: EffortProfile | None = None,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
    runner_selector: RunnerSelectionProvider | None = None,
) -> EditTaskResult:
    """Observe outside the lock, then delegate one locked existing transition."""
    with closing(connect_initialized_readonly(target)) as connection:
        preselector_basis, preflight_error = (
            _capture_preselector_basis(
                connection,
                target,
                request,
                input_error=input_error,
                selector_position=False,
            )
        )
    if preflight_error is not None:
        raise preflight_error
    plan = prepare_request_against_basis(
        preselector_basis,
        target,
        request,
        defer_gate_validation=(
            preselector_basis.task.get(
                "review_target_runner_basis_version", 0
            )
            == 2
        ),
    )
    if preselector_basis.task.get("review_target_runner_basis_version", 0) == 2:
        with closing(connect_initialized_readonly(target)) as connection:
            current_preselector_basis, current_preflight_error = (
                _capture_preselector_basis(
                    connection,
                    target,
                    request,
                    selector_position=True,
                )
            )
        if current_preflight_error is not None:
            raise current_preflight_error
        if (
            current_preselector_basis.semantic_token
            != preselector_basis.semantic_token
        ):
            reject_concurrent_edit_base_change(
                preselector_basis.task,
                current_preselector_basis.task,
                completing=True,
            )

        runner_selection = (
            runner_selector(
                current_preselector_basis.task,
                plan.resolution.completion_evidence_revision,
            )
            if runner_selector is not None
            else None
        )
        with closing(connect_initialized_readonly(target)) as connection:
            final_preselector_basis, final_preflight_error = (
                _capture_preselector_basis(
                    connection,
                    target,
                    request,
                    selector_position=True,
                )
            )
            if final_preflight_error is not None:
                raise final_preflight_error
            if (
                final_preselector_basis.semantic_token
                != current_preselector_basis.semantic_token
            ):
                reject_concurrent_edit_base_change(
                    current_preselector_basis.task,
                    final_preselector_basis.task,
                    completing=True,
                )
            current_basis = _capture_selected_completion_basis(
                connection,
                target,
                request,
                runner_selection,
            )
        plan = replace(plan, basis=current_basis)
        validate_completion_plan_basis(plan, current_basis)
    with closing(connect_initialized(target)) as connection:
        with connection:
            return complete_task(
                connection,
                target.project,
                plan,
                effort_profile=effort_profile,
                database_target=target,
            )
