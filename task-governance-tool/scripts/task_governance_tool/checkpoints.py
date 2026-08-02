"""Bounded typed continuation checkpoint repository."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from task_governance_tool.contracts import read_current_contract
from task_governance_tool.storage import (
    DatabaseTarget,
    ProjectIdentity,
    StorageError,
    utc_now,
    validate_utc_timestamp,
)
from task_governance_tool.tasks import (
    TaskRepositoryError,
    TaskValidationError,
    begin_task_write,
    create_task_event,
    generate_id,
    read_internal_task,
    reject_done_task_write,
    validate_legacy_m19_7_stored_text,
    validate_task_id,
    validate_text,
)


SUMMARY_BYTES = 1_024
NEXT_ACTION_BYTES = 1_024
RISK_BYTES = 512
RISKS_BYTES = 4_096
TOTAL_CONTENT_BYTES = 6_144
MAX_RISKS = 8

PUBLIC_CHECKPOINT_FIELDS = (
    "checkpoint_id",
    "task_id",
    "contract_revision",
    "summary",
    "next_action",
    "unresolved_risks",
    "created_at",
)
PUBLIC_CHECKPOINT_EVENT_FIELDS = (
    "task_event_id",
    "event_type",
    "created_at",
)


@dataclass(frozen=True)
class RecordCheckpointResult:
    checkpoint: dict[str, Any]
    created: bool
    replayed: bool
    event: dict[str, Any] | None


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _validate_byte_limit(field: str, value: str, limit: int) -> None:
    if _utf8_size(value) > limit:
        raise TaskValidationError(
            code="invalid_argument",
            message=f"{field} must be {limit} UTF-8 bytes or fewer",
            field=field,
        )


def _canonical_content(
    *,
    summary: Any,
    next_action: Any,
    unresolved_risks: Sequence[Any] | None,
    legacy_m19_7_stored_summary: bool = False,
) -> tuple[str, str, tuple[str, ...]]:
    summary_validator = (
        validate_legacy_m19_7_stored_text
        if legacy_m19_7_stored_summary
        else validate_text
    )
    normalized_summary = summary_validator(
        "summary",
        summary,
        required=True,
    )
    normalized_next_action = validate_text(
        "next_action",
        next_action,
        required=True,
    )
    _validate_byte_limit("summary", normalized_summary, SUMMARY_BYTES)
    _validate_byte_limit("next_action", normalized_next_action, NEXT_ACTION_BYTES)

    if unresolved_risks is None:
        raw_risks: Sequence[Any] = ()
    elif isinstance(unresolved_risks, (str, bytes)) or not isinstance(
        unresolved_risks,
        Sequence,
    ):
        raise TaskValidationError(
            code="invalid_argument",
            message="unresolved_risks must be a sequence",
            field="unresolved_risks",
        )
    else:
        raw_risks = unresolved_risks
    if len(raw_risks) > MAX_RISKS:
        raise TaskValidationError(
            code="invalid_argument",
            message=f"unresolved_risks may contain at most {MAX_RISKS} values",
            field="unresolved_risks",
        )

    risks: list[str] = []
    for value in raw_risks:
        risk = validate_text(
            "unresolved_risk",
            value,
            required=True,
        )
        _validate_byte_limit("unresolved_risk", risk, RISK_BYTES)
        risks.append(risk)

    risks_size = sum(_utf8_size(risk) for risk in risks)
    if risks_size > RISKS_BYTES:
        raise TaskValidationError(
            code="invalid_argument",
            message=f"unresolved_risks must be {RISKS_BYTES} UTF-8 bytes or fewer in total",
            field="unresolved_risks",
        )
    total_size = (
        _utf8_size(normalized_summary)
        + _utf8_size(normalized_next_action)
        + risks_size
    )
    if total_size > TOTAL_CONTENT_BYTES:
        raise TaskValidationError(
            code="invalid_argument",
            message=f"checkpoint content must be {TOTAL_CONTENT_BYTES} UTF-8 bytes or fewer",
            field="checkpoint",
        )
    return normalized_summary, normalized_next_action, tuple(risks)


def _decode_risks(value: Any) -> list[str]:
    if not isinstance(value, str):
        raise TaskRepositoryError(
            "internal_error",
            "stored checkpoint unresolved risks are invalid",
        )
    try:
        risks = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise TaskRepositoryError(
            "internal_error",
            "stored checkpoint unresolved risks are invalid",
        ) from exc
    if (
        not isinstance(risks, list)
        or len(risks) > MAX_RISKS
        or any(not isinstance(risk, str) for risk in risks)
    ):
        raise TaskRepositoryError(
            "internal_error",
            "stored checkpoint unresolved risks are invalid",
        )
    return risks


def _row_to_checkpoint(row: sqlite3.Row) -> dict[str, Any]:
    risks = _decode_risks(row["unresolved_risks_json"])
    try:
        summary, next_action, normalized_risks = _canonical_content(
            summary=row["summary"],
            next_action=row["next_action"],
            unresolved_risks=risks,
            legacy_m19_7_stored_summary=True,
        )
        checkpoint_id = validate_text(
            "checkpoint_id",
            row["checkpoint_id"],
            required=True,
            limit=128,
        )
        task_id = validate_task_id(row["task_id"])
        contract_revision = int(row["contract_revision"])
        if contract_revision < 0 or isinstance(row["contract_revision"], bool):
            raise ValueError
        created_at = validate_utc_timestamp(
            str(row["created_at"]),
            field="checkpoint created_at",
        )
    except (TaskValidationError, StorageError, TypeError, ValueError) as exc:
        raise TaskRepositoryError(
            "internal_error",
            "stored checkpoint is invalid",
        ) from exc
    if (
        summary != row["summary"]
        or next_action != row["next_action"]
        or list(normalized_risks) != risks
    ):
        raise TaskRepositoryError(
            "internal_error",
            "stored checkpoint is invalid",
        )
    checkpoint = {
        "checkpoint_id": checkpoint_id,
        "task_id": task_id,
        "contract_revision": contract_revision,
        "summary": summary,
        "next_action": next_action,
        "unresolved_risks": risks,
        "created_at": created_at,
    }
    return {field: checkpoint[field] for field in PUBLIC_CHECKPOINT_FIELDS}


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {field: event[field] for field in PUBLIC_CHECKPOINT_EVENT_FIELDS}


def record_checkpoint(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    summary: Any,
    next_action: Any,
    unresolved_risks: Sequence[Any] | None = None,
    database_target: DatabaseTarget | None = None,
) -> RecordCheckpointResult:
    """Record or exactly replay one checkpoint inside the caller transaction."""
    normalized_task_id = validate_task_id(task_id)
    normalized_summary, normalized_next_action, normalized_risks = _canonical_content(
        summary=summary,
        next_action=next_action,
        unresolved_risks=unresolved_risks,
    )

    begin_task_write(connection, database_target)
    task = read_internal_task(
        connection,
        project.project_id,
        normalized_task_id,
    )
    if task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(task)

    contract = read_current_contract(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        current_revision=task["current_contract_revision"],
    )
    contract_revision = int(contract["revision"])
    latest = connection.execute(
        """
        SELECT checkpoint_id, task_id, project_id, contract_revision,
               summary, next_action, unresolved_risks_json, created_at
          FROM task_checkpoints
         WHERE project_id = ?
           AND task_id = ?
           AND contract_revision = ?
         ORDER BY created_at DESC, rowid DESC
         LIMIT 1
        """,
        (project.project_id, normalized_task_id, contract_revision),
    ).fetchone()
    if latest is not None:
        checkpoint = _row_to_checkpoint(latest)
        if (
            checkpoint["summary"] == normalized_summary
            and checkpoint["next_action"] == normalized_next_action
            and checkpoint["unresolved_risks"] == list(normalized_risks)
        ):
            return RecordCheckpointResult(
                checkpoint=checkpoint,
                created=False,
                replayed=True,
                event=None,
            )

    now = utc_now()
    row = {
        "checkpoint_id": generate_id("tg_checkpoint"),
        "task_id": normalized_task_id,
        "project_id": project.project_id,
        "contract_revision": contract_revision,
        "summary": normalized_summary,
        "next_action": normalized_next_action,
        "unresolved_risks_json": json.dumps(
            list(normalized_risks),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "created_at": now,
    }
    connection.execute(
        """
        INSERT INTO task_checkpoints(
          checkpoint_id, task_id, project_id, contract_revision,
          summary, next_action, unresolved_risks_json, created_at
        ) VALUES (
          :checkpoint_id, :task_id, :project_id, :contract_revision,
          :summary, :next_action, :unresolved_risks_json, :created_at
        )
        """,
        row,
    )
    event = create_task_event(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        event_type="checkpoint_recorded",
        summary="Checkpoint recorded",
        created_at=now,
    )
    stored = connection.execute(
        """
        SELECT checkpoint_id, task_id, project_id, contract_revision,
               summary, next_action, unresolved_risks_json, created_at
          FROM task_checkpoints
         WHERE checkpoint_id = ?
        """,
        (row["checkpoint_id"],),
    ).fetchone()
    if stored is None:
        raise TaskRepositoryError(
            "internal_error",
            "checkpoint was not readable after insert",
        )
    return RecordCheckpointResult(
        checkpoint=_row_to_checkpoint(stored),
        created=True,
        replayed=False,
        event=_public_event(event),
    )


def read_latest_checkpoint(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    """Return the latest checkpoint projection for current/show."""
    row = connection.execute(
        """
        SELECT checkpoint_id, task_id, project_id, contract_revision,
               summary, next_action, unresolved_risks_json, created_at
          FROM task_checkpoints
         WHERE project_id = ?
           AND task_id = ?
         ORDER BY created_at DESC, rowid DESC
         LIMIT 1
        """,
        (project_id, task_id),
    ).fetchone()
    return None if row is None else _row_to_checkpoint(row)
