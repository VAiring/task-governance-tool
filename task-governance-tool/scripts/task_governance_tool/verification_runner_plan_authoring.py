"""Pure closed Runner Plan draft decoding and PlanV1 action transforms.

This module performs no filesystem, SQLite, Git, CLI, process, Evidence,
Viewer, or logging I/O.  Physical publication remains a later boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from task_governance_tool.tasks import reject_private_or_raw_content
from task_governance_tool.verification_runner_plan import (
    PLAN_BLOB_UTF8_BYTE_LIMIT,
    PLAN_STEP_LIMIT,
    PLAN_TOTAL_TIMEOUT_SECONDS,
    PLAN_VERSION,
    VerificationRunnerPlan,
    VerificationRunnerPlanBasis,
    VerificationRunnerPlanEntry,
    VerificationRunnerPlanError,
    VerificationRunnerPlanStep,
    decode_verification_runner_json,
    decode_verification_runner_plan_steps,
    encode_verification_runner_plan,
    validate_verification_runner_plan_task_id,
    verification_runner_plan_step_string_leaves,
)


DRAFT_KEYS = frozenset({"version", "steps"})
INITIAL_PLAN_ID = "taskgov-local-plan"
PRIVACY_FIELD = "Runner Plan draft"
INVALID_ARGUMENT_MESSAGE = "arguments are invalid"
ENTRY_REQUIRED_MESSAGE = "Runner Plan entry is required for rebind"
_DRAFT_ADMISSION = object()


def _authoring_error(code: str, message: str) -> VerificationRunnerPlanError:
    return VerificationRunnerPlanError(code=code, message=message)


def _invalid_argument() -> VerificationRunnerPlanError:
    return _authoring_error("invalid_argument", INVALID_ARGUMENT_MESSAGE)


def _entry_required() -> VerificationRunnerPlanError:
    return _authoring_error("runner_plan_entry_required", ENTRY_REQUIRED_MESSAGE)


@dataclass(frozen=True, init=False)
class RunnerPlanDraftV1:
    version: int
    steps: tuple[VerificationRunnerPlanStep, ...]
    _admission: object = field(init=False, repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise _invalid_argument()

    def __post_init__(self) -> None:
        if (
            getattr(self, "_admission", None) is not _DRAFT_ADMISSION
            or type(self.version) is not int
            or self.version != PLAN_VERSION
            or type(self.steps) is not tuple
            or not 1 <= len(self.steps) <= PLAN_STEP_LIMIT
            or any(
                type(step) is not VerificationRunnerPlanStep
                or step.ordinal != ordinal
                for ordinal, step in enumerate(self.steps, start=1)
            )
            or len({step.step_id for step in self.steps}) != len(self.steps)
            or sum(step.timeout_seconds for step in self.steps)
            > PLAN_TOTAL_TIMEOUT_SECONDS
        ):
            raise _invalid_argument()


@dataclass(frozen=True)
class RunnerPlanActionResult:
    plan: VerificationRunnerPlan | None
    candidate_bytes: bytes | None

    def __post_init__(self) -> None:
        if self.plan is not None and type(self.plan) is not VerificationRunnerPlan:
            raise _invalid_argument()
        if self.candidate_bytes is not None:
            if (
                self.plan is None
                or type(self.candidate_bytes) is not bytes
                or encode_verification_runner_plan(self.plan)
                != self.candidate_bytes
            ):
                raise _invalid_argument()

    @property
    def changed(self) -> bool:
        return self.candidate_bytes is not None


def _exact_draft(value: Any) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != DRAFT_KEYS:
        raise _invalid_argument()
    if type(value["version"]) is not int:
        raise _invalid_argument()
    if type(value["steps"]) is not list:
        raise _invalid_argument()
    return value


def decode_runner_plan_draft(raw_blob: bytes) -> RunnerPlanDraftV1:
    """Decode one bounded draft with privacy before leaf grammar and bounds."""

    if type(raw_blob) is not bytes or len(raw_blob) > PLAN_BLOB_UTF8_BYTE_LIMIT:
        raise _invalid_argument()
    try:
        value = _exact_draft(decode_verification_runner_json(raw_blob))
        string_leaves = tuple(
            leaf
            for step in value["steps"]
            for leaf in verification_runner_plan_step_string_leaves(step)
        )
    except VerificationRunnerPlanError as exc:
        raise _invalid_argument() from exc

    for leaf in string_leaves:
        reject_private_or_raw_content(PRIVACY_FIELD, leaf)

    if value["version"] != PLAN_VERSION:
        raise _invalid_argument()
    try:
        steps = decode_verification_runner_plan_steps(value["steps"])
    except VerificationRunnerPlanError as exc:
        raise _invalid_argument() from exc
    draft = object.__new__(RunnerPlanDraftV1)
    object.__setattr__(draft, "version", PLAN_VERSION)
    object.__setattr__(draft, "steps", steps)
    object.__setattr__(draft, "_admission", _DRAFT_ADMISSION)
    draft.__post_init__()
    return draft


def _changed_result(plan: VerificationRunnerPlan) -> RunnerPlanActionResult:
    try:
        candidate = encode_verification_runner_plan(plan)
    except VerificationRunnerPlanError as exc:
        raise _invalid_argument() from exc
    return RunnerPlanActionResult(plan=plan, candidate_bytes=candidate)


def _build_candidate_plan(
    *,
    plan_id: str,
    trusted_local: bool,
    entries: tuple[VerificationRunnerPlanEntry, ...],
) -> VerificationRunnerPlan:
    try:
        return VerificationRunnerPlan(
            version=PLAN_VERSION,
            plan_id=plan_id,
            trusted_local=trusted_local,
            entries=entries,
        )
    except VerificationRunnerPlanError as exc:
        raise _invalid_argument() from exc


def _entry_from_basis(
    basis: VerificationRunnerPlanBasis,
    steps: tuple[VerificationRunnerPlanStep, ...],
) -> VerificationRunnerPlanEntry:
    if type(basis) is not VerificationRunnerPlanBasis:
        raise _invalid_argument()
    try:
        return VerificationRunnerPlanEntry(
            task_id=basis.task_id,
            contract_revision=basis.contract_revision,
            verification_expectation_digest=basis.verification_expectation_digest,
            verification_criterion_digest=basis.verification_criterion_digest,
            coverage="full",
            steps=steps,
        )
    except VerificationRunnerPlanError as exc:
        raise _invalid_argument() from exc


def replace_verification_runner_plan(
    plan: VerificationRunnerPlan | None,
    *,
    basis: VerificationRunnerPlanBasis,
    draft: RunnerPlanDraftV1,
) -> RunnerPlanActionResult:
    if plan is not None and type(plan) is not VerificationRunnerPlan:
        raise _invalid_argument()
    if (
        type(draft) is not RunnerPlanDraftV1
        or getattr(draft, "_admission", None) is not _DRAFT_ADMISSION
    ):
        raise _invalid_argument()
    replacement = _entry_from_basis(basis, draft.steps)
    if plan is None:
        return _changed_result(
            _build_candidate_plan(
                plan_id=INITIAL_PLAN_ID,
                trusted_local=True,
                entries=(replacement,),
            )
        )

    matches = tuple(
        index
        for index, entry in enumerate(plan.entries)
        if entry.task_id == basis.task_id
    )
    insertion = matches[0] if matches else len(plan.entries)
    entries = [entry for entry in plan.entries if entry.task_id != basis.task_id]
    entries.insert(insertion, replacement)
    candidate_plan = _build_candidate_plan(
        plan_id=plan.plan_id,
        trusted_local=plan.trusted_local,
        entries=tuple(entries),
    )
    if candidate_plan == plan:
        return RunnerPlanActionResult(plan=plan, candidate_bytes=None)
    return _changed_result(candidate_plan)


def rebind_verification_runner_plan(
    plan: VerificationRunnerPlan | None,
    *,
    basis: VerificationRunnerPlanBasis,
) -> RunnerPlanActionResult:
    if type(basis) is not VerificationRunnerPlanBasis:
        raise _invalid_argument()
    if plan is None:
        raise _entry_required()
    if type(plan) is not VerificationRunnerPlan:
        raise _invalid_argument()
    matches = tuple(
        index
        for index, entry in enumerate(plan.entries)
        if entry.task_id == basis.task_id
    )
    if not matches:
        raise _entry_required()
    if len(matches) != 1:
        raise VerificationRunnerPlanError(code="plan_ambiguous")
    index = matches[0]
    rebound = _entry_from_basis(basis, plan.entries[index].steps)
    if rebound == plan.entries[index]:
        return RunnerPlanActionResult(plan=plan, candidate_bytes=None)
    entries = list(plan.entries)
    entries[index] = rebound
    return _changed_result(
        _build_candidate_plan(
            plan_id=plan.plan_id,
            trusted_local=plan.trusted_local,
            entries=tuple(entries),
        )
    )


def detach_verification_runner_plan(
    plan: VerificationRunnerPlan | None,
    *,
    task_id: str,
) -> RunnerPlanActionResult:
    try:
        normalized_task_id = validate_verification_runner_plan_task_id(task_id)
    except VerificationRunnerPlanError as exc:
        raise _invalid_argument() from exc
    if plan is None:
        return RunnerPlanActionResult(plan=None, candidate_bytes=None)
    if type(plan) is not VerificationRunnerPlan:
        raise _invalid_argument()
    entries = tuple(
        entry for entry in plan.entries if entry.task_id != normalized_task_id
    )
    if entries == plan.entries:
        return RunnerPlanActionResult(plan=plan, candidate_bytes=None)
    return _changed_result(
        _build_candidate_plan(
            plan_id=plan.plan_id,
            trusted_local=plan.trusted_local,
            entries=entries,
        )
    )


def disable_verification_runner_plan(
    plan: VerificationRunnerPlan | None,
) -> RunnerPlanActionResult:
    if plan is None:
        return RunnerPlanActionResult(plan=None, candidate_bytes=None)
    if type(plan) is not VerificationRunnerPlan:
        raise _invalid_argument()
    if plan.trusted_local is False:
        return RunnerPlanActionResult(plan=plan, candidate_bytes=None)
    return _changed_result(
        _build_candidate_plan(
            plan_id=plan.plan_id,
            trusted_local=False,
            entries=plan.entries,
        )
    )
