"""Task detail projection on the caller-owned read snapshot."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
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
from task_governance_tool.task_values import validate_task_id
from task_governance_tool.verification_runner import (
    VerificationRunnerGateSelection,
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
    event_rows = connection.execute(
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
    from task_governance_tool.contracts import read_current_contract
    from task_governance_tool.checkpoints import read_latest_checkpoint
    from task_governance_tool.handoffs import handoff_summary_for_task
    from task_governance_tool.reviews import read_review_evidence

    review_evidence = read_review_evidence(
        connection,
        project.project_id,
        normalized_task_id,
        validated_task=task_row,
        source_schema_version=source_schema_version,
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
        read_verification_evidence,
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
    )
    return TaskShowResult(
        task=task,
        events=[row_to_event(row) for row in event_rows],
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
    )
