"""One bounded internal analysis worker session; no public launch surface."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, NoReturn

from task_governance_tool import _analysis_win32 as _win32
from task_governance_tool._analysis_windows_process import (
    ProcessQuarantineRequired,
)
from task_governance_tool.analysis_contracts import AnalysisContractError
from task_governance_tool.analysis_outbox import (
    AnalysisOutboxError,
    AnalysisOutboxSession,
    PublicationResult,
)
from task_governance_tool.analysis_packet import (
    FIXED_PROMPT_DIGEST,
    AnalysisPacket,
    AnalysisPacketError,
    build_analysis_packet,
)
from task_governance_tool.analysis_validator import (
    AnalysisValidationError,
    build_analysis_report,
)
from task_governance_tool.codex_analysis_adapter import (
    ClosedMockPlan,
    abort_prepared_mock_attempt,
    bind_closed_mock_attempt,
    execute_prepared_mock_attempt,
    mark_prepared_mock_attempt_recorded,
    preflight_optional,
    prepare_closed_mock_input,
)
from task_governance_tool.evidence_consumer import (
    EvidenceConsumerError,
    ValidatedEvidenceIndex,
    revalidate_descriptor_source,
)
from task_governance_tool.state_paths import AnalysisStatePaths


_RUN_DISPOSITIONS = frozenset(
    {"busy", "idle", "published", "failed", "cancelled", "deferred"}
)
_RETRYABLE_INFERENCE_STATES = frozenset(
    {"unavailable", "launch_failed", "timeout", "invalid_output"}
)


@dataclass(frozen=True)
class AnalysisWorkerError(RuntimeError):
    code: str = "analysis_worker_invalid"
    message: str = "analysis worker could not complete safely"

    def __str__(self) -> str:
        return self.message


def _failure() -> NoReturn:
    raise AnalysisWorkerError()


@dataclass(frozen=True, slots=True, init=False)
class RunOnceResult:
    """Small sanitized outcome from one exclusive worker session."""

    disposition: str
    analysis_job_id: str | None
    status: dict[str, Any] | None
    lease_retained: bool

    def __init__(
        self,
        disposition: str,
        *,
        analysis_job_id: str | None = None,
        status: dict[str, Any] | None = None,
        lease_retained: bool = False,
    ) -> None:
        if (
            disposition not in _RUN_DISPOSITIONS
            or type(lease_retained) is not bool
            or (lease_retained and disposition != "deferred")
            or ((analysis_job_id is None) != (status is None))
            or (disposition in {"busy", "idle"} and status is not None)
        ):
            _failure()
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "analysis_job_id", analysis_job_id)
        object.__setattr__(self, "status", deepcopy(status))
        object.__setattr__(self, "lease_retained", lease_retained)


# The single private binding lets a spawn-only focused test pause the real
# winner after acquisition without adding a callback, event, or public option.
_acquire_session = AnalysisOutboxSession.acquire


def _result(
    disposition: str,
    descriptor: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    *,
    lease_retained: bool = False,
) -> RunOnceResult:
    return RunOnceResult(
        disposition,
        analysis_job_id=(
            None if descriptor is None else str(descriptor["analysis_job_id"])
        ),
        status=status,
        lease_retained=lease_retained,
    )


def _release_result(
    session: AnalysisOutboxSession,
    disposition: str,
    descriptor: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
) -> RunOnceResult:
    session.release_normal()
    return _result(disposition, descriptor, status)


def _publication_result(
    descriptor: dict[str, Any],
    result: PublicationResult,
) -> RunOnceResult:
    if result.status is None:
        if result.disposition != "deferred" or not result.lease_retained:
            _failure()
        return RunOnceResult("deferred", lease_retained=True)
    return _result(
        result.disposition,
        descriptor,
        result.status,
        lease_retained=result.lease_retained,
    )


def _cas_or_defer(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    current: dict[str, Any],
    proposed: dict[str, Any],
) -> tuple[dict[str, Any] | None, RunOnceResult | None]:
    result = session.cas_status(
        descriptor=descriptor,
        expected_status=current,
        status=proposed,
    )
    if not result.applied:
        return None, _release_result(
            session,
            "deferred",
            descriptor,
            result.status,
        )
    return result.status, None


def _terminal_status(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    current: dict[str, Any],
    *,
    state: str,
    fixed_code: str,
    inference_state: str | None = None,
) -> RunOnceResult:
    terminal = deepcopy(current)
    terminal.update(
        {
            "state": state,
            "fixed_code": fixed_code,
            "report_id": None,
            "report_digest": None,
            "render_digest": None,
        }
    )
    if inference_state is not None:
        terminal["inference_state"] = inference_state
    observed, deferred = _cas_or_defer(
        session,
        descriptor,
        current,
        terminal,
    )
    if deferred is not None:
        return deferred
    return _release_result(session, state, descriptor, observed)


def _pending_failure(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    current: dict[str, Any],
    fixed_code: str,
) -> RunOnceResult:
    failed = deepcopy(current)
    failed.update(
        {
            "state": "failed",
            "worker_attempt_count": 1,
            "fixed_code": fixed_code,
        }
    )
    observed, deferred = _cas_or_defer(
        session,
        descriptor,
        current,
        failed,
    )
    if deferred is not None:
        return deferred
    return _release_result(session, "failed", descriptor, observed)


def _report_failure(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    current: dict[str, Any],
) -> RunOnceResult:
    return _terminal_status(
        session,
        descriptor,
        current,
        state="failed",
        fixed_code="report_invalid",
    )


def _publish_no_adapter(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    packet: AnalysisPacket,
    current: dict[str, Any],
    *,
    prompt_digest: str | None,
) -> RunOnceResult:
    try:
        report = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state=current["inference_state"],
            prompt_digest=prompt_digest,
        )
    except AnalysisValidationError:
        return _report_failure(session, descriptor, current)
    proof = session.seal_no_adapter_controller_proof()
    return _publication_result(
        descriptor,
        session.publish_no_adapter(
            descriptor=descriptor,
            expected_status=current,
            packet=packet,
            report=report,
            proof=proof,
            prompt_digest=prompt_digest,
        ),
    )


def _run_offline(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    packet: AnalysisPacket,
    current: dict[str, Any],
) -> RunOnceResult:
    return _publish_no_adapter(
        session,
        descriptor,
        packet,
        current,
        prompt_digest=None,
    )


def _record_no_call(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    packet: AnalysisPacket,
    current: dict[str, Any],
    inference_state: str,
) -> RunOnceResult:
    no_call = deepcopy(current)
    no_call["inference_state"] = inference_state
    observed, deferred = _cas_or_defer(
        session,
        descriptor,
        current,
        no_call,
    )
    if deferred is not None:
        return deferred
    return _publish_no_adapter(
        session,
        descriptor,
        packet,
        observed,
        prompt_digest=FIXED_PROMPT_DIGEST,
    )


def _run_mock(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    packet: AnalysisPacket,
    current: dict[str, Any],
    plan: ClosedMockPlan,
) -> RunOnceResult:
    prior_discard = None
    attempt_number = 1
    while True:
        prepared_input = prepare_closed_mock_input(
            descriptor,
            packet,
            attempt_number,
            plan.scenario_for_attempt(attempt_number),
            prior_discard,
        )
        if not prepared_input.ready:
            if attempt_number != 1 or prepared_input.inference_state != "input_too_large":
                _failure()
            return _record_no_call(
                session,
                descriptor,
                packet,
                current,
                "input_too_large",
            )
        opaque_input = prepared_input.prepared_input
        if opaque_input is None:
            _failure()
        slot = session.create_adapter_tree_slot(
            descriptor,
            packet,
            opaque_input.binding,
            attempt_number,
        )
        prepared = bind_closed_mock_attempt(
            opaque_input,
            slot.root_capability,
        )
        counted = deepcopy(current)
        counted.update(
            {
                "adapter_attempt_count": current["adapter_attempt_count"] + 1,
                "inference_state": "running",
            }
        )
        counted_result = session.cas_status(
            descriptor=descriptor,
            expected_status=current,
            status=counted,
        )
        if not counted_result.applied:
            aborted = abort_prepared_mock_attempt(prepared)
            session.abort_adapter_tree(slot, aborted)
            return _release_result(
                session,
                "deferred",
                descriptor,
                counted_result.status,
            )
        current = counted_result.status
        mark_prepared_mock_attempt_recorded(prepared)
        attempt = execute_prepared_mock_attempt(prepared)
        if attempt.inference_state == "cancelled":
            session.discard_adapter_tree(slot, attempt.tree_proof)
            cancelled = deepcopy(current)
            cancelled.update(
                {
                    "state": "cancelled",
                    "inference_state": "cancelled",
                    "fixed_code": "cancelled",
                    "duration_ms": current["duration_ms"] + attempt.duration_ms,
                }
            )
            observed, deferred = _cas_or_defer(
                session,
                descriptor,
                current,
                cancelled,
            )
            if deferred is not None:
                return deferred
            return _release_result(session, "cancelled", descriptor, observed)

        outcome = deepcopy(current)
        outcome.update(
            {
                "inference_state": attempt.inference_state,
                "duration_ms": current["duration_ms"] + attempt.duration_ms,
                "accepted_output_digest": (
                    attempt.adapter_output.accepted_output_digest
                    if attempt.adapter_output is not None
                    else None
                ),
            }
        )
        outcome_result = session.cas_status(
            descriptor=descriptor,
            expected_status=current,
            status=outcome,
        )
        if not outcome_result.applied:
            session.discard_adapter_tree(slot, attempt.tree_proof)
            return _release_result(
                session,
                "deferred",
                descriptor,
                outcome_result.status,
            )
        current = outcome_result.status
        retry = (
            attempt_number == 1
            and attempt.inference_state in _RETRYABLE_INFERENCE_STATES
            and len(plan.scenarios) == 2
        )
        if retry:
            prior_discard = session.discard_adapter_tree(
                slot,
                attempt.tree_proof,
            )
            attempt_number = 2
            continue

        try:
            report = build_analysis_report(
                descriptor=descriptor,
                packet=packet,
                inference_state=current["inference_state"],
                adapter_output=attempt.adapter_output,
                prompt_digest=attempt.prompt_digest,
            )
        except AnalysisValidationError:
            session.discard_adapter_tree(slot, attempt.tree_proof)
            return _report_failure(session, descriptor, current)
        return _publication_result(
            descriptor,
            session.publish_adapter(
                descriptor=descriptor,
                expected_status=current,
                packet=packet,
                report=report,
                slot=slot,
                tree_proof=attempt.tree_proof,
                adapter_output=attempt.adapter_output,
                prompt_digest=attempt.prompt_digest,
            ),
        )


def _run_optional(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    packet: AnalysisPacket,
    current: dict[str, Any],
    plan: ClosedMockPlan | None,
) -> RunOnceResult:
    if plan is None:
        preflight = preflight_optional()
        if (
            preflight.ready
            or preflight.inference_state != "policy_blocked"
            or preflight.adapter_attempt_count != 0
            or preflight.prompt_digest != FIXED_PROMPT_DIGEST
        ):
            _failure()
        return _record_no_call(
            session,
            descriptor,
            packet,
            current,
            "policy_blocked",
        )
    return _run_mock(session, descriptor, packet, current, plan)


def _run_pending(
    session: AnalysisOutboxSession,
    index: ValidatedEvidenceIndex,
    descriptor: dict[str, Any],
    pending: dict[str, Any],
    mock_plan: ClosedMockPlan | None,
) -> RunOnceResult:
    try:
        source = revalidate_descriptor_source(index, descriptor)
    except EvidenceConsumerError:
        return _pending_failure(
            session,
            descriptor,
            pending,
            "source_invalid",
        )
    try:
        packet = build_analysis_packet(descriptor, source)
    except AnalysisPacketError as failure:
        return _pending_failure(
            session,
            descriptor,
            pending,
            "packet_too_large" if failure.code == "packet_too_large" else "source_invalid",
        )
    running = deepcopy(pending)
    running.update(
        {
            "state": "running",
            "worker_attempt_count": 1,
            "packet_digest": packet.packet_digest,
        }
    )
    current, deferred = _cas_or_defer(
        session,
        descriptor,
        pending,
        running,
    )
    if deferred is not None:
        return deferred
    if descriptor["recipe"]["inference_mode"] == "offline":
        return _run_offline(session, descriptor, packet, current)
    return _run_optional(session, descriptor, packet, current, mock_plan)


def _run_recovery(
    session: AnalysisOutboxSession,
    index: ValidatedEvidenceIndex,
    descriptor: dict[str, Any],
    current: dict[str, Any],
) -> RunOnceResult:
    try:
        source = revalidate_descriptor_source(index, descriptor)
        packet = build_analysis_packet(descriptor, source)
    except (EvidenceConsumerError, AnalysisPacketError):
        return _release_result(session, "deferred", descriptor, current)
    prompt_digest = (
        None
        if descriptor["recipe"]["inference_mode"] == "offline"
        else FIXED_PROMPT_DIGEST
    )
    return _publication_result(
        descriptor,
        session.recover_publication(
            descriptor=descriptor,
            expected_status=current,
            packet=packet,
            expected_prompt_digest=prompt_digest,
        ),
    )


def _run_reclaim(
    session: AnalysisOutboxSession,
    descriptor: dict[str, Any],
    current: dict[str, Any],
) -> RunOnceResult:
    if current["worker_attempt_count"] < 2:
        reclaimed = deepcopy(current)
        reclaimed["worker_attempt_count"] += 1
        current, deferred = _cas_or_defer(
            session,
            descriptor,
            current,
            reclaimed,
        )
        if deferred is not None:
            return deferred
    terminal_inference = current["inference_state"]
    if descriptor["recipe"]["inference_mode"] == "codex_optional":
        if terminal_inference in {"pending", "running"}:
            terminal_inference = "failed"
    return _terminal_status(
        session,
        descriptor,
        current,
        state="failed",
        fixed_code="interrupted",
        inference_state=terminal_inference,
    )


def run_once(
    paths: AnalysisStatePaths,
    evidence_index: ValidatedEvidenceIndex,
    mock_plan: ClosedMockPlan | None = None,
) -> RunOnceResult:
    """Consume at most one selected job under one no-wait exclusive lease."""

    if (
        type(paths) is not AnalysisStatePaths
        or (mock_plan is not None and type(mock_plan) is not ClosedMockPlan)
    ):
        _failure()
    try:
        session = _acquire_session(paths)
    except AnalysisOutboxError as failure:
        if failure.contended and failure.code == "analysis_busy":
            return RunOnceResult("busy")
        raise AnalysisWorkerError() from failure

    selected = None
    try:
        selected = session.select_next_job()
        if selected is None:
            return _release_result(session, "idle")
        descriptor = selected.descriptor
        current = selected.status
        if selected.kind == "pending":
            return _run_pending(
                session,
                evidence_index,
                descriptor,
                current,
                mock_plan,
            )
        if selected.kind == "recover_intent":
            return _run_recovery(
                session,
                evidence_index,
                descriptor,
                current,
            )
        if selected.kind == "reclaim_running":
            return _run_reclaim(session, descriptor, current)
        _failure()
    except (ProcessQuarantineRequired, _win32.Win32QuarantineRequired) as failure:
        if session.state == "active":
            session._retain_publication_resources(failure)
        return RunOnceResult(
            "deferred",
            lease_retained=session.state == "retained",
        )
    except (AnalysisContractError, AnalysisOutboxError, AnalysisValidationError) as failure:
        if session.state == "active":
            session._retain_publication_resources(failure)
        raise AnalysisWorkerError() from failure
    except BaseException as failure:
        if session.state == "active":
            session._retain_publication_resources(failure)
        raise


__all__ = (
    "AnalysisWorkerError",
    "RunOnceResult",
    "run_once",
)
