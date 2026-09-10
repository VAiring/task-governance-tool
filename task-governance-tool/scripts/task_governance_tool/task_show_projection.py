"""Task detail projection on the caller-owned read snapshot."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from task_governance_tool.completion_history_projection import (
    format_completion_history,
)
from task_governance_tool.completion_history_repository import (
    read_completion_history,
)
from task_governance_tool.storage import (
    CompletionHistory,
    ProjectIdentity,
    StorageError,
    current_schema_version,
)
from task_governance_tool.stored_task_validation import (
    fetch_validated_current_task_row,
)
from task_governance_tool.tasks import (
    TaskRepositoryError,
    row_to_event,
    row_to_internal_task,
    row_to_show_task,
    suggested_next_action,
)
from task_governance_tool.task_values import (
    TEXT_LIMITS,
    TaskValidationError,
    validate_event_summary,
    validate_task_id,
)
from task_governance_tool.verification_runner import (
    VerificationRunnerGateSelection,
)


CURRENT_CONTEXT_EVENT_TYPES = (
    "note_added", "task_updated", "review_tier_changed", "task_reopened",
)


@dataclass(frozen=True)
class TaskShowResult:
    task: dict[str, Any]
    events: list[dict[str, Any]]
    suggested_next_action: str
    review_evidence: dict[str, Any]
    handoff_summary: dict[str, int]
    contract: dict[str, Any]
    latest_checkpoint: dict[str, Any] | None
    completion_history: dict[str, Any]
    completion_history_latest_summary: dict[str, Any] | None
    verification_evidence: dict[str, Any]
    current_events: list[dict[str, Any]] = field(default_factory=list)
    current_review_receipts: list[dict[str, Any]] = field(default_factory=list)
    current_review_findings: list[dict[str, Any]] = field(default_factory=list)
    current_verification_receipt: dict[str, Any] | None = None


def build_task_show_data(
    result: TaskShowResult,
    *,
    audit: bool,
) -> dict[str, Any]:
    """Select a fixed presentation after all shared Task/evidence validation."""
    review = result.review_evidence
    verification = result.verification_evidence
    return {
        "task": result.task,
        "events": result.events if audit else result.current_events,
        "suggested_next_action": result.suggested_next_action,
        "review_evidence": review if audit else {
            "gate": {
                key: review["gate"][key]
                for key in (
                    "required_independent_passes", "qualifying_independent_passes",
                    "fallback_kind", "satisfied",
                )
            },
            "counts": {
                key: review["counts"][key]
                for key in (
                    "receipts_current_generation", "changes_requested_current_generation",
                    "open_high", "open_medium", "open_low",
                )
            },
            "current_receipts": result.current_review_receipts,
            "current_findings": result.current_review_findings,
        },
        "verification_evidence": verification if audit else {
            "current_verification_subject": verification["current_verification_subject"],
            "gate": verification["gate"],
            "counts": {
                key: verification["counts"][key]
                for key in (
                    "receipts_exact_current", "qualifying_exact_current",
                    "blocking_exact_current",
                )
            },
            "current_receipt": result.current_verification_receipt,
        },
        "handoff_summary": result.handoff_summary,
        "contract": result.contract,
        "latest_checkpoint": result.latest_checkpoint,
        "completion_history": result.completion_history if audit else {
            key: result.completion_history[key]
            for key in ("total", "legacy_history_incomplete")
        },
    }


def _completion_history_latest_summary(
    history: CompletionHistory,
) -> dict[str, Any] | None:
    if not history.cycles:
        return None
    cycle = history.cycles[0]
    return {
        "saved_cycle_ordinal": cycle.saved_cycle_ordinal,
        "origin": cycle.origin,
        "completeness": cycle.completeness,
        "completed_at": cycle.completed_at,
        "completion_evidence_kind": cycle.completion_evidence_kind,
        "review_target_kind": cycle.review_target_kind,
        "review_target_generation": cycle.review_target_generation,
        "review_basis_kind": cycle.gate_basis.kind,
    }


def show_task(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    event_limit: int = 10,
    runner_selection: VerificationRunnerGateSelection | None = None,
    include_current_context: bool = False,
) -> TaskShowResult:
    normalized_task_id = validate_task_id(task_id)
    source_schema_version = current_schema_version(connection)
    task_row = fetch_validated_current_task_row(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
    )
    if task_row is None:
        raise TaskRepositoryError("not_found", "task was not found")
    task = row_to_show_task(task_row)
    internal_task = row_to_internal_task(task_row)
    if include_current_context:
        # Keep the old audit window and all typed resume context in one read.
        # A single ordered selection cannot duplicate an event in both sets.
        event_rows = connection.execute(
            """
            SELECT *
              FROM task_events
             WHERE project_id = ?
               AND task_id = ?
               AND (
                 event_type IN (?, ?, ?, ?)
                 OR rowid IN (
                   SELECT rowid
                     FROM task_events
                    WHERE project_id = ? AND task_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                 )
               )
             ORDER BY created_at DESC, rowid DESC
            """,
            (
                project.project_id, normalized_task_id,
                *CURRENT_CONTEXT_EVENT_TYPES,
                project.project_id, normalized_task_id,
                max(1, event_limit) if event_limit >= 0 else event_limit,
            ),
        ).fetchall()
        audit_event_rows = event_rows[:event_limit] if event_limit >= 0 else event_rows
        current_events = []
        for index, row in enumerate(event_rows):
            if index != 0 and row["event_type"] not in CURRENT_CONTEXT_EVENT_TYPES:
                continue
            if event_limit >= 0 and index >= event_limit:
                # Only these rows are newly exposed beyond the prior read window.
                # Validate the existing emitted-summary policy without normalizing
                # stored bytes or changing direct-service/audit admission.
                if (
                    type(row["summary"]) is not str
                    or len(row["summary"]) > TEXT_LIMITS["event_summary"]
                ):
                    raise StorageError(
                        "project_state_unreadable",
                        "project state could not be read safely",
                    )
                try:
                    validate_event_summary(row["summary"])
                except TaskValidationError as exc:
                    raise StorageError(
                        "project_state_unreadable",
                        "project state could not be read safely",
                    ) from exc
            current_events.append(row_to_event(row))
    else:
        audit_event_rows = connection.execute(
            """
            SELECT *
              FROM task_events
             WHERE project_id = ?
               AND task_id = ?
             ORDER BY created_at DESC, rowid DESC
             LIMIT ?
            """,
            (project.project_id, normalized_task_id, event_limit),
        ).fetchall()
        current_events = []
    from task_governance_tool.contracts import read_current_contract
    from task_governance_tool.checkpoints import read_latest_checkpoint
    from task_governance_tool.handoffs import handoff_summary_for_task
    from task_governance_tool.reviews import (
        TaskShowReviewDetails,
        read_review_evidence,
    )

    current_review = TaskShowReviewDetails() if include_current_context else None
    review_evidence = read_review_evidence(
        connection,
        project.project_id,
        normalized_task_id,
        validated_task=task_row,
        source_schema_version=source_schema_version,
        task_show_details=current_review,
    )
    handoff_summary = handoff_summary_for_task(
        connection,
        project.project_id,
        normalized_task_id,
    )
    contract = read_current_contract(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        current_revision=task_row["current_contract_revision"],
    )
    latest_checkpoint = read_latest_checkpoint(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
    )
    raw_completion_history = read_completion_history(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
    )
    from task_governance_tool.verification_receipts import (
        TaskShowVerificationDetails,
        read_verification_evidence,
    )

    current_verification = (
        TaskShowVerificationDetails() if include_current_context else None
    )
    verification_evidence = read_verification_evidence(
        connection,
        task=internal_task,
        completion_cycle=(
            raw_completion_history.cycles[0]
            if raw_completion_history.cycles
            else None
        ),
        runner_selection=runner_selection,
        task_show_details=current_verification,
    )
    return TaskShowResult(
        task=task,
        events=[row_to_event(row) for row in audit_event_rows],
        suggested_next_action=suggested_next_action(task),
        review_evidence=review_evidence,
        handoff_summary=handoff_summary,
        contract=contract,
        latest_checkpoint=latest_checkpoint,
        completion_history=format_completion_history(raw_completion_history),
        completion_history_latest_summary=_completion_history_latest_summary(
            raw_completion_history
        ),
        verification_evidence=verification_evidence,
        current_events=current_events,
        current_review_receipts=(
            current_review.receipts if current_review is not None else []
        ),
        current_review_findings=(
            current_review.findings if current_review is not None else []
        ),
        current_verification_receipt=(
            current_verification.receipt if current_verification is not None else None
        ),
    )
