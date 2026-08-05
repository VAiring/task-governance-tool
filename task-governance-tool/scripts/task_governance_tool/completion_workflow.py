"""Completion check/write orchestration across short SQLite and Git phases."""

from __future__ import annotations

import sqlite3
from contextlib import closing, nullcontext
from dataclasses import dataclass, field

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
    TaskRepositoryError,
    TaskValidationError,
    capture_completion_basis,
    complete_task,
    prepare_completion_plan,
    validate_completion_plan_basis,
    validate_completion_state_basis,
)


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


def prepare_request_against_basis(
    basis: CompletionBasis,
    target: DatabaseTarget,
    request: CompletionRequest,
    *,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
) -> CompletionPlan:
    """Run the same state-first completion preflight for check and write."""
    if input_error is not None:
        validate_completion_state_basis(basis)
        raise input_error
    return prepare_completion_plan(
        basis,
        target.project,
        request,
    )


def check_completion_request(
    target: DatabaseTarget,
    request: CompletionRequest,
    *,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
    initial_connection: sqlite3.Connection | None = None,
) -> CompletionCheckOutcome:
    """Check one request with read/close/Git/read and no stored authority."""
    manager = (
        nullcontext(initial_connection)
        if initial_connection is not None
        else closing(connect_initialized_readonly(target))
    )
    with manager as connection:
        first_basis = capture_completion_basis(
            connection,
            target.project,
            request.task_id,
        )
    if initial_connection is not None:
        initial_connection.close()

    plan: CompletionPlan | None = None
    preflight_error: TaskValidationError | TaskRepositoryError | None = None
    try:
        plan = prepare_request_against_basis(
            first_basis,
            target,
            request,
            input_error=input_error,
        )
    except (TaskValidationError, TaskRepositoryError) as exc:
        preflight_error = exc

    with closing(connect_initialized_readonly(target)) as connection:
        second_basis = capture_completion_basis(
            connection,
            target.project,
            request.task_id,
        )

    if second_basis.semantic_token != first_basis.semantic_token:
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
    try:
        validate_completion_plan_basis(
            plan,
            second_basis,
            stale_code="completion_check_stale",
        )
    except (TaskValidationError, TaskRepositoryError) as exc:
        if exc.code not in COMPLETION_BLOCKING_CODES:
            raise
        return CompletionCheckOutcome(
            basis=second_basis,
            plan=plan,
            blocking_code=exc.code,
        )
    return CompletionCheckOutcome(
        basis=second_basis,
        plan=plan,
        blocking_code=None,
    )


def execute_completion_request(
    target: DatabaseTarget,
    request: CompletionRequest,
    *,
    effort_profile: EffortProfile | None = None,
    input_error: TaskValidationError | TaskRepositoryError | None = None,
) -> EditTaskResult:
    """Observe outside the lock, then delegate one locked existing transition."""
    with closing(connect_initialized_readonly(target)) as connection:
        basis = capture_completion_basis(
            connection,
            target.project,
            request.task_id,
        )
    plan = prepare_request_against_basis(
        basis,
        target,
        request,
        input_error=input_error,
    )
    with closing(connect_initialized(target)) as connection:
        with connection:
            return complete_task(
                connection,
                target.project,
                plan,
                effort_profile=effort_profile,
                database_target=target,
            )
