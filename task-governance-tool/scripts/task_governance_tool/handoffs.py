"""Durable local handoff outbox services."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from task_governance_tool.storage import ProjectIdentity, utc_now
from task_governance_tool.tasks import (
    TaskValidationError,
    validate_limit,
    validate_task_id,
    validate_text,
)


HANDOFF_STATES = (
    "pending_handoff",
    "handed_off",
    "handoff_withdrawn_by_user",
)
PUBLIC_HANDOFF_FIELDS = (
    "handoff_id",
    "project_id",
    "source_task_id",
    "source_contract_revision",
    "idempotency_key",
    "occurrence_id",
    "summary",
    "rationale",
    "state",
    "adapter_key",
    "adapter_version",
    "delivery_attempts",
    "last_delivery_code",
    "next_attempt_at",
    "claim_expires_at",
    "receiver_receipt",
    "withdraw_reason",
    "created_at",
    "updated_at",
    "handed_off_at",
    "withdrawn_at",
)
COMPACT_HANDOFF_FIELDS = (
    "handoff_id",
    "source_task_id",
    "source_contract_revision",
    "summary",
    "state",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True)
class HandoffError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class RecordHandoffResult:
    handoff: dict[str, Any]
    created: bool
    replayed: bool


@dataclass(frozen=True)
class ListHandoffsResult:
    handoffs: list[dict[str, Any]]
    count: int
    total_matching: int
    limit: int
    states: tuple[str, ...]


@dataclass(frozen=True)
class WithdrawHandoffResult:
    handoff: dict[str, Any]
    changed_fields: list[str]


def _generate_handoff_id() -> str:
    return f"tg_handoff_{secrets.token_hex(8)}"


def _canonical_text(
    field: str,
    value: Any,
    *,
    required: bool = False,
    limit: int,
) -> str:
    text = validate_text(field, value, required=required, limit=limit)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _validate_handoff_id(value: Any) -> str:
    return validate_text("handoff_id", value, required=True, limit=128)


def _validate_state(value: Any) -> str:
    state = validate_text("handoff_state", value, required=True, limit=64).strip()
    if state not in HANDOFF_STATES:
        raise TaskValidationError(
            code="invalid_argument",
            message=f"handoff_state must be one of: {', '.join(HANDOFF_STATES)}",
            field="handoff_state",
        )
    return state


def _validate_states(values: Sequence[Any] | None) -> tuple[str, ...]:
    if not values:
        return ("pending_handoff",)
    states: list[str] = []
    for value in values:
        state = _validate_state(value)
        if state not in states:
            states.append(state)
    return tuple(states)


def _stored_text(
    row: sqlite3.Row,
    column: str,
    *,
    validation_field: str | None = None,
    required: bool = False,
    limit: int,
) -> str:
    field = validation_field or column
    try:
        return validate_text(field, row[column], required=required, limit=limit)
    except TaskValidationError as exc:
        raise HandoffError(
            "internal_error",
            f"stored handoff {field} is invalid",
        ) from exc


def _stored_nonnegative_int(row: sqlite3.Row, column: str) -> int:
    value = row[column]
    if type(value) is not int or value < 0:
        raise HandoffError("internal_error", "stored handoff counter is invalid")
    return value


def _stored_timestamp(
    row: sqlite3.Row,
    column: str,
    *,
    required: bool = False,
) -> None:
    value = row[column]
    if value is None:
        if required:
            raise HandoffError("internal_error", "stored handoff timestamp is invalid")
        return
    if type(value) is not str or len(value) != 20:
        raise HandoffError("internal_error", "stored handoff timestamp is invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise HandoffError(
            "internal_error",
            "stored handoff timestamp is invalid",
        ) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise HandoffError("internal_error", "stored handoff timestamp is invalid")


def _validate_stored_handoff(row: sqlite3.Row) -> None:
    _stored_text(row, "handoff_id", required=True, limit=128)
    _stored_text(row, "project_id", required=True, limit=256)
    try:
        validate_task_id(row["source_task_id"])
    except TaskValidationError as exc:
        raise HandoffError(
            "internal_error",
            "stored handoff source task identity is invalid",
        ) from exc
    state = str(row["state"])
    if state not in HANDOFF_STATES:
        raise HandoffError("internal_error", "stored handoff state is invalid")
    _stored_nonnegative_int(row, "source_contract_revision")
    delivery_attempts = _stored_nonnegative_int(row, "delivery_attempts")
    idempotency_key = str(row["idempotency_key"])
    if len(idempotency_key) != 64 or any(
        char not in "0123456789abcdef" for char in idempotency_key
    ):
        raise HandoffError("internal_error", "stored handoff identity is invalid")
    _stored_text(
        row,
        "summary",
        validation_field="handoff_summary",
        required=True,
        limit=1000,
    )
    _stored_text(row, "rationale", validation_field="handoff_rationale", limit=1000)
    _stored_text(
        row,
        "occurrence_id",
        validation_field="handoff_occurrence_id",
        limit=200,
    )
    _stored_text(row, "adapter_key", validation_field="handoff_adapter_key", limit=500)
    _stored_text(
        row,
        "adapter_version",
        validation_field="handoff_adapter_version",
        limit=500,
    )
    _stored_text(
        row,
        "last_delivery_code",
        validation_field="handoff_last_delivery_code",
        limit=500,
    )
    _stored_text(
        row,
        "receiver_receipt",
        validation_field="handoff_receiver_receipt",
        limit=500,
    )
    _stored_text(
        row,
        "withdraw_reason",
        validation_field="handoff_withdraw_reason",
        limit=1000,
    )
    _stored_timestamp(row, "created_at", required=True)
    _stored_timestamp(row, "updated_at", required=True)
    _stored_timestamp(row, "next_attempt_at")
    _stored_timestamp(row, "claim_expires_at")
    _stored_timestamp(row, "handed_off_at")
    _stored_timestamp(row, "withdrawn_at")
    if state == "pending_handoff":
        valid_state = (
            row["handed_off_at"] is None
            and row["withdrawn_at"] is None
            and str(row["withdraw_reason"]) == ""
            and str(row["receiver_receipt"]) == ""
        )
    elif state == "handed_off":
        valid_state = (
            row["handed_off_at"] is not None
            and row["withdrawn_at"] is None
            and str(row["withdraw_reason"]) == ""
            and str(row["claim_token"]) == ""
            and row["claim_expires_at"] is None
        )
    else:
        valid_state = (
            row["withdrawn_at"] is not None
            and row["handed_off_at"] is None
            and str(row["withdraw_reason"]) != ""
            and str(row["receiver_receipt"]) == ""
            and delivery_attempts == 0
            and str(row["claim_token"]) == ""
            and row["claim_expires_at"] is None
        )
    if not valid_state:
        raise HandoffError(
            "internal_error",
            "stored handoff state fields are inconsistent",
        )


def _row_to_handoff(
    row: sqlite3.Row,
    *,
    compact: bool = False,
) -> dict[str, Any]:
    _validate_stored_handoff(row)
    fields = COMPACT_HANDOFF_FIELDS if compact else PUBLIC_HANDOFF_FIELDS
    return {field: row[field] for field in fields}


def _idempotency_key(
    *,
    project_id: str,
    source_task_id: str,
    source_contract_revision: int,
    summary: str,
    rationale: str,
    occurrence_id: str,
) -> str:
    canonical = json.dumps(
        {
            "version": 1,
            "project_id": project_id,
            "source_task_id": source_task_id,
            "source_contract_revision": source_contract_revision,
            "summary": summary,
            "rationale": rationale,
            "occurrence_id": occurrence_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_handoff(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    source_task_id: Any,
    *,
    summary: Any,
    rationale: Any = "",
    occurrence_id: Any = None,
) -> RecordHandoffResult:
    """Insert or replay one sanitized local handoff inside the caller transaction."""
    normalized_task_id = validate_task_id(source_task_id)
    normalized_summary = _canonical_text(
        "handoff_summary",
        summary,
        required=True,
        limit=1000,
    )
    normalized_rationale = _canonical_text(
        "handoff_rationale",
        rationale,
        limit=1000,
    )
    if occurrence_id is None:
        normalized_occurrence = ""
    else:
        try:
            normalized_occurrence = _canonical_text(
                "handoff_occurrence_id",
                occurrence_id,
                required=True,
                limit=200,
            )
        except TaskValidationError as exc:
            if exc.code == "invalid_argument":
                raise HandoffError(
                    "handoff_occurrence_invalid",
                    "an explicitly supplied occurrence_id must be non-empty and at most 200 characters",
                ) from exc
            raise

    task_row = connection.execute(
        """
        SELECT task_id
          FROM tasks
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project.project_id, normalized_task_id),
    ).fetchone()
    if task_row is None:
        raise HandoffError("not_found", "source task was not found")

    source_contract_revision = 0
    idempotency_key = _idempotency_key(
        project_id=project.project_id,
        source_task_id=normalized_task_id,
        source_contract_revision=source_contract_revision,
        summary=normalized_summary,
        rationale=normalized_rationale,
        occurrence_id=normalized_occurrence,
    )
    existing = connection.execute(
        """
        SELECT *
          FROM handoff_records
         WHERE project_id = ?
           AND idempotency_key = ?
        """,
        (project.project_id, idempotency_key),
    ).fetchone()
    if existing is not None:
        handoff = _row_to_handoff(existing)
        expected = (
            normalized_task_id,
            source_contract_revision,
            normalized_summary,
            normalized_rationale,
            normalized_occurrence,
        )
        actual = (
            str(existing["source_task_id"]),
            _stored_nonnegative_int(existing, "source_contract_revision"),
            str(existing["summary"]),
            str(existing["rationale"]),
            str(existing["occurrence_id"]),
        )
        if actual != expected:
            raise HandoffError(
                "internal_error",
                "handoff idempotency identity collided with different canonical content",
            )
        return RecordHandoffResult(
            handoff=handoff,
            created=False,
            replayed=True,
        )

    now = utc_now()
    row = {
        "handoff_id": _generate_handoff_id(),
        "project_id": project.project_id,
        "source_task_id": normalized_task_id,
        "source_contract_revision": source_contract_revision,
        "idempotency_key": idempotency_key,
        "occurrence_id": normalized_occurrence,
        "summary": normalized_summary,
        "rationale": normalized_rationale,
        "state": "pending_handoff",
        "created_at": now,
        "updated_at": now,
    }
    connection.execute(
        """
        INSERT INTO handoff_records(
          handoff_id, project_id, source_task_id, source_contract_revision,
          idempotency_key, occurrence_id, summary, rationale, state,
          created_at, updated_at
        ) VALUES (
          :handoff_id, :project_id, :source_task_id, :source_contract_revision,
          :idempotency_key, :occurrence_id, :summary, :rationale, :state,
          :created_at, :updated_at
        )
        """,
        row,
    )
    stored = connection.execute(
        "SELECT * FROM handoff_records WHERE handoff_id = ?",
        (row["handoff_id"],),
    ).fetchone()
    if stored is None:
        raise HandoffError("handoff_not_persisted", "local handoff was not readable after insert")
    handoff = _row_to_handoff(stored)
    return RecordHandoffResult(
        handoff=handoff,
        created=True,
        replayed=False,
    )


def list_handoffs(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    states: Sequence[Any] | None = None,
    source_task_id: Any = None,
    limit: Any = None,
) -> ListHandoffsResult:
    selected_states = _validate_states(states)
    filters = ["project_id = ?"]
    values: list[Any] = [project.project_id]
    placeholders = ", ".join("?" for _ in selected_states)
    filters.append(f"state IN ({placeholders})")
    values.extend(selected_states)
    if source_task_id is not None:
        filters.append("source_task_id = ?")
        values.append(validate_task_id(source_task_id))
    row_limit = validate_limit(limit)
    where = " AND ".join(filters)
    rows = connection.execute(
        f"""
        SELECT *, COUNT(*) OVER () AS total_matching
          FROM handoff_records
         WHERE {where}
         ORDER BY created_at ASC, handoff_id ASC
         LIMIT ?
        """,
        [*values, row_limit],
    ).fetchall()
    total_matching = int(rows[0]["total_matching"]) if rows else 0
    handoffs = [_row_to_handoff(row, compact=True) for row in rows]
    return ListHandoffsResult(
        handoffs=handoffs,
        count=len(handoffs),
        total_matching=total_matching,
        limit=row_limit,
        states=selected_states,
    )


def show_handoff(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    handoff_id: Any,
) -> dict[str, Any]:
    normalized_id = _validate_handoff_id(handoff_id)
    row = connection.execute(
        """
        SELECT *
          FROM handoff_records
         WHERE project_id = ?
           AND handoff_id = ?
        """,
        (project.project_id, normalized_id),
    ).fetchone()
    if row is None:
        raise HandoffError("not_found", "handoff was not found")
    return _row_to_handoff(row)


def withdraw_handoff(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    handoff_id: Any,
    *,
    reason: Any,
) -> WithdrawHandoffResult:
    normalized_id = _validate_handoff_id(handoff_id)
    normalized_reason = _canonical_text(
        "handoff_withdraw_reason",
        reason,
        required=True,
        limit=1000,
    )
    existing = connection.execute(
        """
        SELECT *
          FROM handoff_records
         WHERE project_id = ?
           AND handoff_id = ?
        """,
        (project.project_id, normalized_id),
    ).fetchone()
    if existing is None:
        raise HandoffError("not_found", "handoff was not found")
    _validate_stored_handoff(existing)
    if (
        str(existing["state"]) != "pending_handoff"
        or _stored_nonnegative_int(existing, "delivery_attempts") != 0
        or str(existing["claim_token"]) != ""
        or existing["claim_expires_at"] is not None
    ):
        raise HandoffError(
            "handoff_not_withdrawable",
            "handoff can be withdrawn only before any delivery claim or attempt",
        )

    now = utc_now()
    cursor = connection.execute(
        """
        UPDATE handoff_records
           SET state = 'handoff_withdrawn_by_user',
               withdraw_reason = ?,
               updated_at = ?,
               withdrawn_at = ?
         WHERE project_id = ?
           AND handoff_id = ?
           AND state = 'pending_handoff'
           AND delivery_attempts = 0
           AND claim_token = ''
           AND claim_expires_at IS NULL
        """,
        (normalized_reason, now, now, project.project_id, normalized_id),
    )
    if cursor.rowcount != 1:
        raise HandoffError(
            "handoff_not_withdrawable",
            "handoff changed concurrently and is no longer withdrawable",
        )
    stored = connection.execute(
        "SELECT * FROM handoff_records WHERE handoff_id = ?",
        (normalized_id,),
    ).fetchone()
    if stored is None:
        raise HandoffError("internal_error", "handoff was not readable after withdrawal")
    return WithdrawHandoffResult(
        handoff=_row_to_handoff(stored),
        changed_fields=["state", "withdraw_reason", "withdrawn_at"],
    )


def handoff_summary_for_task(
    connection: sqlite3.Connection,
    project_id: str,
    task_id: str,
) -> dict[str, int]:
    summary = {state: 0 for state in HANDOFF_STATES}
    rows = connection.execute(
        """
        SELECT *
          FROM handoff_records
         WHERE project_id = ?
           AND source_task_id = ?
         ORDER BY created_at ASC, handoff_id ASC
        """,
        (project_id, task_id),
    ).fetchall()
    for row in rows:
        _validate_stored_handoff(row)
        state = str(row["state"])
        summary[state] += 1
    return summary
