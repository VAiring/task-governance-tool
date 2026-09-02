"""Coordinate one Task edit with one canonical Runner Plan action.

This internal service keeps the SQLite and physical Plan commits separate.  It
captures the expected Plan source before a writer is acquired, performs only
pure Plan computation from the locked prospective Task basis, closes SQLite,
and publishes (or confirms) the Plan afterward.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from task_governance_tool.contracts import CONTRACT_INPUT_FIELDS
from task_governance_tool.reviews import (
    ReviewTargetAuthorityBasis,
    read_review_target_authority_basis,
)
from task_governance_tool.storage import (
    DatabaseTarget,
    connect_initialized,
    connect_initialized_task_readonly,
    contract_criterion_digest,
    verification_expectation_digest,
)
from task_governance_tool.tasks import (
    EditTaskResult,
    RunnerSelectionProvider,
    TaskEditBasisPrecommitValidator,
    TaskRepositoryError,
    edit_task,
    read_internal_task,
    read_task,
    reject_concurrent_edit_base_change,
    validate_task_id,
    validation_error,
)
from task_governance_tool.verification_runner_plan import (
    VerificationRunnerPlan,
    VerificationRunnerPlanBasis,
    VerificationRunnerPlanError,
    decode_verification_runner_plan,
)
from task_governance_tool.verification_runner_plan_authoring import (
    RunnerPlanActionResult,
    RunnerPlanDraftV1,
    decode_runner_plan_draft,
    detach_verification_runner_plan,
    disable_verification_runner_plan,
    rebind_verification_runner_plan,
    replace_verification_runner_plan,
)
from task_governance_tool.verification_runner_plan_publisher import (
    CONFIRM_RUNNER_PLAN_SOURCE,
    RunnerPlanAuthoringSource,
    capture_runner_plan_authoring_source,
    publish_verification_runner_plan,
)


RunnerPlanAction = Literal["replace", "rebind", "detach", "disable"]
RunnerPlanUpdateStatus = Literal["updated", "unchanged", "unconfirmed"]
TaskMutation = Literal["none", "committed"]

RUNNER_PLAN_ACTIONS = frozenset({"replace", "rebind", "detach", "disable"})
INVALID_ARGUMENT_MESSAGE = "arguments are invalid"
INVALID_OPTION_COMBINATION_MESSAGE = (
    "Runner Plan action cannot be combined with these task edit options"
)
RUNNER_PLAN_ACTION_REQUIRED_MESSAGE = (
    "Runner Plan action is required for this Task basis change"
)

_INCOMPATIBLE_ACTION_FIELDS = frozenset(
    {
        "commit_not_required",
        "completion_commit_hash",
        "completion_evidence_kind",
        "completion_evidence_reason",
        "completion_revision",
        "external_revision_approved",
        "reopen_reason",
        "review_complete",
        "verification_complete",
    }
)


@dataclass(frozen=True)
class RunnerPlanUpdate:
    """Closed, path-free public projection of one coordinated Plan action."""

    action: RunnerPlanAction
    status: RunnerPlanUpdateStatus

    def __post_init__(self) -> None:
        if self.action not in RUNNER_PLAN_ACTIONS or self.status not in {
            "updated",
            "unchanged",
            "unconfirmed",
        }:
            raise _invalid_argument()


@dataclass(frozen=True)
class TaskRunnerPlanEditResult:
    """Existing Task result plus the bounded Plan and maintenance disposition."""

    edit_result: EditTaskResult = field(repr=False)
    runner_plan_update: RunnerPlanUpdate | None
    task_mutation: TaskMutation

    def __post_init__(self) -> None:
        if type(self.edit_result) is not EditTaskResult:
            raise _invalid_argument()
        if self.runner_plan_update is not None and type(
            self.runner_plan_update
        ) is not RunnerPlanUpdate:
            raise _invalid_argument()
        if self.task_mutation not in {"none", "committed"}:
            raise _invalid_argument()
        if (
            self.runner_plan_update is not None
            and self.runner_plan_update.status == "unconfirmed"
            and self.task_mutation != "committed"
        ):
            raise _invalid_argument()


def _invalid_argument() -> VerificationRunnerPlanError:
    return VerificationRunnerPlanError(
        code="invalid_argument",
        message=INVALID_ARGUMENT_MESSAGE,
    )


def _validate_target(target: DatabaseTarget) -> tuple[Path, Path]:
    if (
        type(target) is not DatabaseTarget
        or target.canonical_fixed is not True
        or not isinstance(target.project.canonical_repo, Path)
        or not target.project.canonical_repo.is_absolute()
        or not isinstance(target.skill_root, Path)
        or not target.skill_root.is_absolute()
    ):
        raise _invalid_argument()
    return target.project.canonical_repo, target.skill_root


def _validate_action_and_draft(
    action: Any,
    draft_blob: Any,
) -> tuple[RunnerPlanAction | None, RunnerPlanDraftV1 | None]:
    if action is None:
        if draft_blob is not None:
            raise _invalid_argument()
        return None, None
    if type(action) is not str or action not in RUNNER_PLAN_ACTIONS:
        raise _invalid_argument()
    if action == "replace":
        if type(draft_blob) is not bytes:
            raise _invalid_argument()
        return action, decode_runner_plan_draft(draft_blob)
    if draft_blob is not None:
        raise _invalid_argument()
    return action, None


def _reject_incompatible_action_options(edit_input: dict[str, Any]) -> None:
    if _INCOMPATIBLE_ACTION_FIELDS.intersection(edit_input) or (
        isinstance(edit_input.get("status"), str)
        and edit_input["status"].strip() == "done"
    ):
        raise validation_error(
            "invalid_option_combination",
            INVALID_OPTION_COMBINATION_MESSAGE,
        )


def _read_task_preflight_from_connection(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    task_id: str,
    *,
    authority_required: bool,
    authority_nonterminal_required: bool = False,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    ReviewTargetAuthorityBasis | None,
]:
    internal = read_internal_task(
        connection,
        target.project.project_id,
        task_id,
    )
    public = read_task(
        connection,
        target.project.project_id,
        task_id,
    )
    if internal is None or public is None:
        raise TaskRepositoryError("not_found", "task was not found")
    # The authority reader preserves the established done-task error.  A
    # cancelled Task needs this additional authoring-only terminal guard.
    if (
        authority_nonterminal_required
        and internal["status"] == "cancelled"
    ):
        raise _invalid_argument()
    authority = (
        read_review_target_authority_basis(
            connection,
            target.project,
            task_id,
        )
        if authority_required
        else None
    )
    return internal, public, authority


def _read_task_preflight(
    target: DatabaseTarget,
    task_id: str,
    *,
    authority_required: bool,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    ReviewTargetAuthorityBasis | None,
]:
    with closing(connect_initialized_task_readonly(target)) as connection:
        return _read_task_preflight_from_connection(
            connection,
            target,
            task_id,
            authority_required=authority_required,
            authority_nonterminal_required=authority_required,
        )


def _read_task_preflight_snapshot(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    task_id: str,
    *,
    authority_required: bool,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    ReviewTargetAuthorityBasis | None,
]:
    if connection.in_transaction:
        raise TaskRepositoryError(
            "internal_error",
            "Task preflight started inside a transaction",
        )
    connection.execute("BEGIN")
    try:
        return _read_task_preflight_from_connection(
            connection,
            target,
            task_id,
            authority_required=authority_required,
        )
    finally:
        if connection.in_transaction:
            connection.rollback()


def _basis_from_authority(
    authority: ReviewTargetAuthorityBasis,
    *,
    required: bool,
) -> VerificationRunnerPlanBasis | None:
    revision = authority.task.get("current_contract_revision")
    criterion_digest = authority.verification_criterion_digest
    if (
        type(revision) is not int
        or revision <= 0
        or type(criterion_digest) is not str
    ):
        if required:
            raise _invalid_argument()
        return None
    try:
        return VerificationRunnerPlanBasis(
            task_id=str(authority.task["task_id"]),
            contract_revision=revision,
            verification_expectation_digest=(
                authority.verification_expectation_digest
            ),
            verification_criterion_digest=criterion_digest,
        )
    except (KeyError, TypeError, ValueError, VerificationRunnerPlanError) as exc:
        if required:
            raise _invalid_argument() from exc
        return None


def _future_basis(
    prospective: dict[str, Any],
) -> VerificationRunnerPlanBasis:
    try:
        task_id = prospective["task_id"]
        revision = prospective["current_contract_revision"]
        verification = prospective["verification"]
        if (
            type(task_id) is not str
            or type(revision) is not int
            or revision <= 0
            or type(verification) is not str
            or not verification.strip()
        ):
            raise _invalid_argument()
        return VerificationRunnerPlanBasis(
            task_id=task_id,
            contract_revision=revision,
            verification_expectation_digest=(
                verification_expectation_digest(verification)
            ),
            verification_criterion_digest=contract_criterion_digest(
                "verification",
                verification,
            ),
        )
    except (KeyError, TypeError, ValueError, VerificationRunnerPlanError) as exc:
        raise _invalid_argument() from exc


def _capture_decoded_plan(
    repo: Path,
    package_root: Path,
) -> tuple[RunnerPlanAuthoringSource, VerificationRunnerPlan | None]:
    source = capture_runner_plan_authoring_source(repo, package_root)
    if source.state != "present":
        return source, None
    if type(source.raw_blob) is not bytes:
        raise _invalid_argument()
    return source, decode_verification_runner_plan(source.raw_blob)


def _apply_action(
    action: RunnerPlanAction,
    plan: VerificationRunnerPlan | None,
    *,
    task_id: str,
    basis: VerificationRunnerPlanBasis | None,
    draft: RunnerPlanDraftV1 | None,
) -> RunnerPlanActionResult:
    if action == "replace":
        if basis is None or draft is None:
            raise _invalid_argument()
        return replace_verification_runner_plan(plan, basis=basis, draft=draft)
    if action == "rebind":
        if basis is None or draft is not None:
            raise _invalid_argument()
        return rebind_verification_runner_plan(plan, basis=basis)
    if action == "detach":
        if draft is not None:
            raise _invalid_argument()
        return detach_verification_runner_plan(plan, task_id=task_id)
    if action == "disable" and draft is None:
        return disable_verification_runner_plan(plan)
    raise _invalid_argument()


def _publish_action(
    repo: Path,
    package_root: Path,
    source: RunnerPlanAuthoringSource,
    action_result: RunnerPlanActionResult,
) -> RunnerPlanUpdateStatus:
    publish_verification_runner_plan(
        repo,
        package_root,
        source,
        (
            action_result.candidate_bytes
            if action_result.changed
            else CONFIRM_RUNNER_PLAN_SOURCE
        ),
    )
    return "updated" if action_result.changed else "unchanged"


def _basis_changed(
    locked_existing: dict[str, Any],
    prospective: dict[str, Any],
) -> bool:
    return (
        prospective.get("current_contract_revision")
        != locked_existing.get("current_contract_revision")
        or prospective.get("verification") != locked_existing.get("verification")
    )


def _matching_enabled_entry(
    plan: VerificationRunnerPlan | None,
    basis: VerificationRunnerPlanBasis | None,
) -> bool:
    if plan is None or basis is None or plan.trusted_local is not True:
        return False
    matches = tuple(
        entry for entry in plan.entries if entry.task_id == basis.task_id
    )
    return len(matches) == 1 and matches[0].basis() == basis


def _mutation(result: EditTaskResult) -> TaskMutation:
    return "committed" if result.event is not None else "none"


def _edit_on_connection(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    task_id: str,
    *,
    effort_profile: Any | None,
    runner_selector: RunnerSelectionProvider | None,
    basis_precommit_validator: TaskEditBasisPrecommitValidator | None = None,
    edit_input: dict[str, Any],
) -> EditTaskResult:
    return edit_task(
        connection,
        target.project,
        task_id,
        effort_profile=effort_profile,
        database_target=target,
        runner_selector=runner_selector,
        basis_precommit_validator=basis_precommit_validator,
        **edit_input,
    )


def _execute_task_edit(
    target: DatabaseTarget,
    task_id: str,
    *,
    effort_profile: Any | None,
    runner_selector: RunnerSelectionProvider | None,
    basis_precommit_validator: TaskEditBasisPrecommitValidator | None = None,
    edit_input: dict[str, Any],
) -> EditTaskResult:
    with closing(connect_initialized(target)) as connection:
        with connection:
            return _edit_on_connection(
                connection,
                target,
                task_id,
                effort_profile=effort_profile,
                runner_selector=runner_selector,
                basis_precommit_validator=basis_precommit_validator,
                edit_input=edit_input,
            )


def _edit_without_action(
    target: DatabaseTarget,
    repo: Path,
    package_root: Path,
    task_id: str,
    *,
    effort_profile: Any | None,
    runner_selector: RunnerSelectionProvider | None,
    edit_input: dict[str, Any],
) -> TaskRunnerPlanEditResult:
    might_change_basis = "verification" in edit_input or bool(
        set(CONTRACT_INPUT_FIELDS).intersection(edit_input)
    )
    if not might_change_basis:
        result = _execute_task_edit(
            target,
            task_id,
            effort_profile=effort_profile,
            runner_selector=runner_selector,
            edit_input=edit_input,
        )
        return TaskRunnerPlanEditResult(
            edit_result=result,
            runner_plan_update=None,
            task_mutation=_mutation(result),
        )

    with closing(connect_initialized(target)) as connection:
        preflight, _public, authority = _read_task_preflight_snapshot(
            connection,
            target,
            task_id,
            authority_required=True,
        )
        current_basis = (
            None
            if authority is None
            else _basis_from_authority(authority, required=False)
        )
        exact_match = False
        if current_basis is not None:
            try:
                _source, plan = _capture_decoded_plan(repo, package_root)
                exact_match = _matching_enabled_entry(plan, current_basis)
            except VerificationRunnerPlanError:
                # An unreadable, malformed, ambiguous, absent, disabled, stale,
                # or no-entry Plan must not hold an ordinary Task edit hostage.
                exact_match = False

        validator = None
        if exact_match:

            def require_explicit_action(
                locked_existing: dict[str, Any],
                prospective: dict[str, Any],
            ) -> None:
                reject_concurrent_edit_base_change(preflight, locked_existing)
                if _basis_changed(locked_existing, prospective):
                    raise validation_error(
                        "runner_plan_action_required",
                        RUNNER_PLAN_ACTION_REQUIRED_MESSAGE,
                    )

            validator = require_explicit_action

        with connection:
            result = _edit_on_connection(
                connection,
                target,
                task_id,
                effort_profile=effort_profile,
                runner_selector=runner_selector,
                basis_precommit_validator=validator,
                edit_input=edit_input,
            )
    return TaskRunnerPlanEditResult(
        edit_result=result,
        runner_plan_update=None,
        task_mutation=_mutation(result),
    )


def _edit_plan_only(
    target: DatabaseTarget,
    repo: Path,
    package_root: Path,
    task_id: str,
    *,
    action: RunnerPlanAction,
    draft: RunnerPlanDraftV1 | None,
) -> TaskRunnerPlanEditResult:
    preflight, public, authority = _read_task_preflight(
        target,
        task_id,
        authority_required=action in {"replace", "rebind"},
    )
    basis = (
        _basis_from_authority(authority, required=True)
        if authority is not None
        else None
    )
    source, plan = _capture_decoded_plan(repo, package_root)
    action_result = _apply_action(
        action,
        plan,
        task_id=str(preflight["task_id"]),
        basis=basis,
        draft=draft,
    )
    status = _publish_action(
        repo,
        package_root,
        source,
        action_result,
    )
    return TaskRunnerPlanEditResult(
        edit_result=EditTaskResult(
            task=public,
            changed_fields=[],
            event=None,
        ),
        runner_plan_update=RunnerPlanUpdate(action=action, status=status),
        task_mutation="none",
    )


def _edit_combined(
    target: DatabaseTarget,
    repo: Path,
    package_root: Path,
    task_id: str,
    *,
    action: RunnerPlanAction,
    draft: RunnerPlanDraftV1 | None,
    effort_profile: Any | None,
    runner_selector: RunnerSelectionProvider | None,
    edit_input: dict[str, Any],
) -> TaskRunnerPlanEditResult:
    computed: list[RunnerPlanActionResult] = []
    with closing(connect_initialized(target)) as connection:
        preflight, _public, _authority = _read_task_preflight_snapshot(
            connection,
            target,
            task_id,
            authority_required=False,
        )
        source, plan = _capture_decoded_plan(repo, package_root)

        def compute_candidate(
            locked_existing: dict[str, Any],
            prospective: dict[str, Any],
        ) -> None:
            reject_concurrent_edit_base_change(preflight, locked_existing)
            if not _basis_changed(locked_existing, prospective):
                raise validation_error(
                    "invalid_option_combination",
                    INVALID_OPTION_COMBINATION_MESSAGE,
                )
            if computed:
                raise TaskRepositoryError(
                    "internal_error",
                    "Runner Plan basis validation ran more than once",
                )
            computed.append(
                _apply_action(
                    action,
                    plan,
                    task_id=task_id,
                    basis=(
                        _future_basis(prospective)
                        if action in {"replace", "rebind"}
                        else None
                    ),
                    draft=draft,
                )
            )

        with connection:
            task_result = _edit_on_connection(
                connection,
                target,
                task_id,
                effort_profile=effort_profile,
                runner_selector=runner_selector,
                basis_precommit_validator=compute_candidate,
                edit_input=edit_input,
            )
    if len(computed) != 1:
        raise TaskRepositoryError(
            "internal_error",
            "Runner Plan basis validation did not complete",
        )

    try:
        status = _publish_action(
            repo,
            package_root,
            source,
            computed[0],
        )
    except VerificationRunnerPlanError:
        status = "unconfirmed"
    return TaskRunnerPlanEditResult(
        edit_result=task_result,
        runner_plan_update=RunnerPlanUpdate(action=action, status=status),
        task_mutation="committed",
    )


def edit_task_with_runner_plan(
    target: DatabaseTarget,
    task_id: Any,
    *,
    runner_plan_action: Any | None = None,
    runner_plan_draft_blob: Any | None = None,
    effort_profile: Any | None = None,
    runner_selector: RunnerSelectionProvider | None = None,
    **edit_input: Any,
) -> TaskRunnerPlanEditResult:
    """Execute one ordinary, Plan-only, or DB-first combined Task edit."""

    repo, package_root = _validate_target(target)
    normalized_task_id = validate_task_id(task_id)
    action, draft = _validate_action_and_draft(
        runner_plan_action,
        runner_plan_draft_blob,
    )
    exact_edit_input = dict(edit_input)

    if action is None:
        return _edit_without_action(
            target,
            repo,
            package_root,
            normalized_task_id,
            effort_profile=effort_profile,
            runner_selector=runner_selector,
            edit_input=exact_edit_input,
        )

    _reject_incompatible_action_options(exact_edit_input)
    if not exact_edit_input:
        return _edit_plan_only(
            target,
            repo,
            package_root,
            normalized_task_id,
            action=action,
            draft=draft,
        )
    return _edit_combined(
        target,
        repo,
        package_root,
        normalized_task_id,
        action=action,
        draft=draft,
        effort_profile=effort_profile,
        runner_selector=runner_selector,
        edit_input=exact_edit_input,
    )
