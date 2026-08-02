"""Optional immutable Task Contract services."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.ordering import first_out_of_order_advanced_task
from task_governance_tool.storage import ProjectIdentity, utc_now
from task_governance_tool.tasks import (
    SQLITE_INT64_MAX,
    TaskRepositoryError,
    TaskValidationError,
    bounded_transition_summary,
    create_task_event,
    read_internal_task as read_validated_internal_task,
    row_to_task,
    validate_legacy_m19_7_stored_text,
    validate_sqlite_int64,
    validate_text,
    validation_error,
)


CONTRACT_INPUT_FIELDS = (
    "contract_scope",
    "contract_acceptance",
    "contract_constraints",
    "contract_authority_ref",
    "contract_change_reason",
)
CONTRACT_ACTIVE_STATUSES = {
    "ready",
    "in_progress",
    "paused",
    "blocked",
    "review_pending",
}
CONTRACT_ADD_STATUSES = {
    "ready",
    "in_progress",
    "blocked",
    "review_pending",
}
CONTRACT_LIMITS = {
    "contract_scope": 4000,
    "contract_acceptance": 4000,
    "contract_constraints": 2000,
    "contract_authority_ref": 500,
    "contract_change_reason": 1000,
}


@dataclass(frozen=True)
class ContractWriteResult:
    recorded: bool
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded": self.recorded,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ContractEditResult:
    task: dict[str, Any]
    changed_fields: list[str]
    event: dict[str, Any] | None
    contract_write: ContractWriteResult


def split_contract_input(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    remaining = dict(values)
    contract_input = {
        field: remaining.pop(field)
        for field in CONTRACT_INPUT_FIELDS
        if field in remaining
    }
    return remaining, contract_input


def _canonical_text(
    field: str,
    value: Any,
    *,
    required: bool = False,
) -> str:
    text = validate_text(
        field,
        value,
        required=required,
        limit=CONTRACT_LIMITS[field],
    )
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _canonical_legacy_m19_7_stored_constraints(value: Any) -> str:
    text = validate_legacy_m19_7_stored_text(
        "contract_constraints",
        value,
        limit=CONTRACT_LIMITS["contract_constraints"],
    )
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _validate_authority_ref(
    value: Any,
    *,
    task_id: str,
    allowed_revisions: set[int] | None,
    required: bool,
) -> str:
    authority_ref = _canonical_text(
        "contract_authority_ref",
        value,
        required=required,
    )
    if "\n" in authority_ref:
        raise validation_error(
            "contract_authority_required",
            "contract_authority_ref must be one stable identifier, not instruction text",
            "contract_authority_ref",
        )
    if authority_ref.startswith("user_instruction:"):
        prefix = f"user_instruction:{task_id}:"
        raw_revision = authority_ref[len(prefix):] if authority_ref.startswith(prefix) else ""
        try:
            authority_revision = validate_sqlite_int64(
                raw_revision,
                field="contract_authority_ref",
            )
        except TaskValidationError as exc:
            raise validation_error(
                "contract_authority_required",
                "user-instruction authority must identify this task and a positive revision",
                "contract_authority_ref",
            ) from exc
        if (
            authority_revision <= 0
            or (
                allowed_revisions is not None
                and authority_revision not in allowed_revisions
            )
        ):
            raise validation_error(
                "contract_authority_required",
                (
                    "user-instruction authority must identify an allowed "
                    "Contract revision for this task"
                ),
                "contract_authority_ref",
            )
    return authority_ref


def normalize_contract_input(
    contract_input: dict[str, Any],
    *,
    task_id: str,
    revision: int,
    initial: bool,
    current_constraints: str | None = None,
    require_later_metadata: bool = True,
    allowed_authority_revisions: set[int] | None = None,
) -> dict[str, str]:
    if not contract_input:
        raise validation_error(
            "invalid_argument",
            "Contract input was not supplied",
        )
    if "contract_scope" not in contract_input or "contract_acceptance" not in contract_input:
        raise validation_error(
            "invalid_argument",
            "Contract input requires both --contract-scope and --contract-acceptance",
        )
    scope = _canonical_text(
        "contract_scope",
        contract_input["contract_scope"],
        required=True,
    )
    acceptance = _canonical_text(
        "contract_acceptance",
        contract_input["contract_acceptance"],
        required=True,
    )
    if "contract_constraints" in contract_input:
        constraints_text = _canonical_text(
            "contract_constraints",
            contract_input["contract_constraints"],
        )
    else:
        constraints_text = current_constraints or ""
    authority_revisions = (
        {revision}
        if initial and allowed_authority_revisions is None
        else allowed_authority_revisions
    )
    authority_ref = _validate_authority_ref(
        contract_input.get("contract_authority_ref", ""),
        task_id=task_id,
        allowed_revisions=authority_revisions,
        required=not initial and require_later_metadata,
    )
    if initial and "contract_change_reason" in contract_input:
        raise validation_error(
            "invalid_argument",
            "an initial Contract cannot include --contract-change-reason",
            "contract_change_reason",
        )
    change_reason = _canonical_text(
        "contract_change_reason",
        contract_input.get("contract_change_reason", ""),
        required=False,
    )
    if not initial and require_later_metadata and not change_reason:
        raise validation_error(
            "invalid_argument",
            "a semantic Contract revision requires --contract-change-reason",
            "contract_change_reason",
        )
    return {
        "scope": scope,
        "acceptance": acceptance,
        "constraints_text": constraints_text,
        "authority_ref": authority_ref,
        "change_reason": change_reason,
    }


def _contract_projection(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "revision": int(row["revision"]),
        "scope": str(row["scope"]),
        "acceptance": str(row["acceptance"]),
        "constraints": str(row["constraints_text"]),
        "authority_ref": str(row["authority_ref"]),
        "change_reason": str(row["change_reason"]),
        "created_at": str(row["created_at"]),
    }


def _validate_stored_contract(
    row: sqlite3.Row,
    *,
    project_id: str,
    task_id: str,
    revision: int,
) -> dict[str, Any]:
    try:
        stored_revision = validate_sqlite_int64(row["revision"], field="contract_revision")
        if stored_revision <= 0:
            raise ValueError("revision must be positive")
        values = {
            "scope": _canonical_text("contract_scope", row["scope"], required=True),
            "acceptance": _canonical_text(
                "contract_acceptance",
                row["acceptance"],
                required=True,
            ),
            "constraints_text": _canonical_legacy_m19_7_stored_constraints(
                row["constraints_text"],
            ),
            "authority_ref": _validate_authority_ref(
                row["authority_ref"],
                task_id=task_id,
                allowed_revisions={stored_revision},
                required=stored_revision > 1,
            ),
            "change_reason": _canonical_text(
                "contract_change_reason",
                row["change_reason"],
                required=stored_revision > 1,
            ),
        }
    except (TaskValidationError, ValueError) as exc:
        raise TaskRepositoryError(
            "internal_error",
            "stored Task Contract is invalid",
        ) from exc
    if (
        str(row["project_id"]) != project_id
        or str(row["task_id"]) != task_id
        or stored_revision != revision
        or any(str(row[field]) != value for field, value in values.items())
    ):
        raise TaskRepositoryError(
            "internal_error",
            "stored Task Contract does not match its task pointer",
        )
    return _contract_projection(row)


def read_current_contract(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    current_revision: Any,
) -> dict[str, Any]:
    try:
        revision = validate_sqlite_int64(
            current_revision,
            field="current_contract_revision",
        )
    except TaskValidationError as exc:
        raise TaskRepositoryError(
            "internal_error",
            "stored Task Contract pointer is invalid",
        ) from exc
    if revision < 0:
        raise TaskRepositoryError(
            "internal_error",
            "stored Task Contract pointer is invalid",
        )
    if revision == 0:
        return {
            "revision": 0,
            "scope": "",
            "acceptance": "",
            "constraints": "",
            "authority_ref": "",
            "change_reason": "",
            "created_at": None,
        }
    row = connection.execute(
        """
        SELECT *
          FROM task_contract_revisions
         WHERE project_id = ?
           AND task_id = ?
           AND revision = ?
        """,
        (project_id, task_id, revision),
    ).fetchone()
    if row is None:
        raise TaskRepositoryError(
            "internal_error",
            "stored Task Contract pointer has no matching revision",
        )
    max_row = connection.execute(
        """
        SELECT MAX(revision) AS max_revision
          FROM task_contract_revisions
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project_id, task_id),
    ).fetchone()
    if (
        max_row is None
        or max_row["max_revision"] is None
        or int(max_row["max_revision"]) != revision
    ):
        raise TaskRepositoryError(
            "internal_error",
            "stored Task Contract pointer is not the latest revision",
        )
    return _validate_stored_contract(
        row,
        project_id=project_id,
        task_id=task_id,
        revision=revision,
    )


def _insert_contract_revision(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    revision: int,
    values: dict[str, str],
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO task_contract_revisions(
          contract_revision_id, task_id, project_id, revision,
          scope, acceptance, constraints_text, authority_ref,
          change_reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"tg_contract_{secrets.token_hex(8)}",
            task_id,
            project_id,
            revision,
            values["scope"],
            values["acceptance"],
            values["constraints_text"],
            values["authority_ref"],
            values["change_reason"],
            created_at,
        ),
    )


def add_initial_contract(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    status: str,
    contract_input: dict[str, Any],
    created_at: str,
) -> ContractWriteResult:
    if status not in CONTRACT_ADD_STATUSES:
        raise validation_error(
            "contract_activation_forbidden",
            "an initial Contract is not allowed for this task status",
            "status",
        )
    values = normalize_contract_input(
        contract_input,
        task_id=task_id,
        revision=1,
        initial=True,
    )
    try:
        _insert_contract_revision(
            connection,
            project_id=project_id,
            task_id=task_id,
            revision=1,
            values=values,
            created_at=created_at,
        )
        cursor = connection.execute(
            """
            UPDATE tasks
               SET current_contract_revision = 1
             WHERE project_id = ?
               AND task_id = ?
               AND current_contract_revision = 0
            """,
            (project_id, task_id),
        )
    except sqlite3.IntegrityError as exc:
        raise TaskRepositoryError(
            "contract_write_conflict",
            "Task Contract revision conflicted with current state",
        ) from exc
    if cursor.rowcount != 1:
        raise TaskRepositoryError(
            "contract_write_conflict",
            "Task Contract activation conflicted with current state",
        )
    return ContractWriteResult(recorded=True, revision=1)


def _empty_completion_and_target(task: dict[str, Any]) -> bool:
    return (
        str(task["completion_evidence_kind"]) == "none"
        and str(task["completion_evidence_revision"]) == ""
        and str(task["completion_evidence_reason"]) == ""
        and int(task["external_revision_approved"]) == 0
        and int(task["completion_commit_required"]) == 1
        and str(task["completion_commit_hash"]) == ""
        and str(task["review_target_kind"]) == ""
        and str(task["review_target_value"]) == ""
        and str(task["review_target_base_revision"]) == ""
        and int(task["review_target_generation"]) == 0
    )


def _read_internal_task(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    task = read_validated_internal_task(connection, project_id, task_id)
    if task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    return task


def _rollback_savepoint(connection: sqlite3.Connection, savepoint: str) -> None:
    connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
    connection.execute(f"RELEASE SAVEPOINT {savepoint}")


def _activate_revision_zero(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    existing: dict[str, Any],
    *,
    caller_edit_input: dict[str, Any],
    contract_input: dict[str, Any],
) -> ContractEditResult:
    raw_status = caller_edit_input.get("status")
    if (
        set(caller_edit_input) != {"status"}
        or not isinstance(raw_status, str)
        or raw_status.strip() != "in_progress"
        or existing["status"] not in {"ready", "blocked"}
        or not _empty_completion_and_target(existing)
    ):
        raise validation_error(
            "contract_activation_forbidden",
            (
                "revision-zero Contract activation requires an exact ready or blocked "
                "to in_progress transition with empty completion and review evidence"
            ),
        )
    values = normalize_contract_input(
        contract_input,
        task_id=str(existing["task_id"]),
        revision=1,
        initial=True,
    )
    now = utc_now()
    savepoint = f"taskgov_contract_{secrets.token_hex(4)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        locked = _read_internal_task(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
        )
        if locked != existing:
            raise TaskRepositoryError(
                "contract_write_conflict",
                "task changed concurrently; retry the Contract write",
            )
        _insert_contract_revision(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
            revision=1,
            values=values,
            created_at=now,
        )
        cursor = connection.execute(
            """
            UPDATE tasks
               SET status = 'in_progress',
                   blocked_reason = '',
                   current_contract_revision = 1,
                   updated_at = ?
             WHERE project_id = ?
               AND task_id = ?
               AND current_contract_revision = 0
            """,
            (now, project.project_id, existing["task_id"]),
        )
        if cursor.rowcount != 1:
            raise TaskRepositoryError(
                "contract_write_conflict",
                "Task Contract activation conflicted with current state",
            )
        lanes = {str(existing["lane"])} if existing["kind"] == "sequential" else set()
        invalid_task_id = first_out_of_order_advanced_task(
            connection,
            project_id=project.project_id,
            lanes=lanes,
        )
        if invalid_task_id is not None:
            raise TaskRepositoryError(
                "sequential_predecessor_incomplete",
                (
                    "sequential lane contains active, review-pending, or done work "
                    "with an incomplete predecessor"
                ),
            )
        authority = values["authority_ref"] or "(not supplied)"
        event = create_task_event(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
            event_type="contract_recorded",
            summary=bounded_transition_summary(
                "Contract recorded; Authority: ",
                authority,
                recorded_markers=["revision 1"],
                note=None,
            ),
            created_at=now,
        )
        stored = _read_internal_task(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
        )
        if stored is None:
            raise TaskRepositoryError(
                "internal_error",
                "task was not readable after Contract activation",
            )
    except sqlite3.IntegrityError as exc:
        _rollback_savepoint(connection, savepoint)
        raise TaskRepositoryError(
            "contract_write_conflict",
            "Task Contract activation conflicted with current state",
        ) from exc
    except Exception:
        _rollback_savepoint(connection, savepoint)
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
    changed_fields = ["status"]
    if existing["blocked_reason"]:
        changed_fields.append("blocked_reason")
    return ContractEditResult(
        task=row_to_task(stored),
        changed_fields=changed_fields,
        event=event,
        contract_write=ContractWriteResult(recorded=True, revision=1),
    )


def _later_revision(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    existing: dict[str, Any],
    *,
    caller_edit_input: dict[str, Any],
    contract_input: dict[str, Any],
) -> ContractEditResult:
    if caller_edit_input:
        raise validation_error(
            "contract_write_conflict",
            "later Contract revisions must be Contract-only task edits",
        )
    if existing["status"] not in CONTRACT_ACTIVE_STATUSES:
        raise validation_error(
            "contract_activation_forbidden",
            "a Contract revision is not allowed for this task status",
            "status",
        )
    current_revision = validate_sqlite_int64(
        existing["current_contract_revision"],
        field="current_contract_revision",
    )
    if current_revision <= 0:
        raise TaskRepositoryError(
            "internal_error",
            "Task Contract pointer is invalid",
        )
    if current_revision >= SQLITE_INT64_MAX:
        raise validation_error(
            "contract_write_conflict",
            "Task Contract revision reached SQLite's signed 64-bit maximum",
        )
    next_revision = current_revision + 1
    current = read_current_contract(
        connection,
        project_id=project.project_id,
        task_id=str(existing["task_id"]),
        current_revision=current_revision,
    )
    values = normalize_contract_input(
        contract_input,
        task_id=str(existing["task_id"]),
        revision=next_revision,
        initial=False,
        current_constraints=str(current["constraints"]),
        require_later_metadata=False,
        allowed_authority_revisions=None,
    )
    if (
        values["scope"] == current["scope"]
        and values["acceptance"] == current["acceptance"]
        and values["constraints_text"] == current["constraints"]
    ):
        row = _read_internal_task(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
        )
        return ContractEditResult(
            task=row_to_task(row),
            changed_fields=[],
            event=None,
            contract_write=ContractWriteResult(
                recorded=False,
                revision=current_revision,
            ),
        )
    if not values["authority_ref"]:
        raise validation_error(
            "contract_authority_required",
            "a semantic Contract revision requires --contract-authority-ref",
            "contract_authority_ref",
        )
    expected_user_authority = (
        f"user_instruction:{existing['task_id']}:{next_revision}"
    )
    if values["authority_ref"].startswith("user_instruction:"):
        current_user_authority = (
            f"user_instruction:{existing['task_id']}:{current_revision}"
        )
        if values["authority_ref"] not in {
            current_user_authority,
            expected_user_authority,
        }:
            raise validation_error(
                "contract_authority_required",
                (
                    "a semantic user-instruction authority must identify the "
                    "current or next Contract revision"
                ),
                "contract_authority_ref",
            )
        # A concurrent writer may have formed the exact current-or-next
        # placeholder before this transaction acquired its write lock. The
        # validator accepts only those two revisions; bind that same explicit
        # instruction deterministically to the revision allocated here.
        values["authority_ref"] = expected_user_authority
    if not values["change_reason"]:
        raise validation_error(
            "invalid_argument",
            "a semantic Contract revision requires --contract-change-reason",
            "contract_change_reason",
        )

    target_was_empty = (
        int(existing["review_target_generation"]) == 0
        and str(existing["review_target_kind"]) == ""
        and str(existing["review_target_value"]) == ""
        and str(existing["review_target_base_revision"]) == ""
    )
    if target_was_empty:
        next_generation = 0
    else:
        if int(existing["review_target_generation"]) >= SQLITE_INT64_MAX:
            raise validation_error(
                "contract_write_conflict",
                "review target generation reached SQLite's signed 64-bit maximum",
            )
        next_generation = int(existing["review_target_generation"]) + 1

    next_status = (
        "in_progress"
        if existing["status"] == "review_pending"
        else str(existing["status"])
    )
    update_values = {
        "current_contract_revision": next_revision,
        "completion_commit_required": 1,
        "completion_commit_hash": "",
        "completion_evidence_kind": "none",
        "completion_evidence_revision": "",
        "completion_evidence_reason": "",
        "external_revision_approved": 0,
        "review_target_kind": "",
        "review_target_value": "",
        "review_target_base_revision": "",
        "review_target_generation": next_generation,
        "status": next_status,
    }
    changed_fields = [
        field
        for field, value in update_values.items()
        if field not in {
            "current_contract_revision",
            "review_target_base_revision",
        }
        and existing[field] != value
    ]
    now = utc_now()
    savepoint = f"taskgov_contract_{secrets.token_hex(4)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        locked = _read_internal_task(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
        )
        if locked != existing:
            raise TaskRepositoryError(
                "contract_write_conflict",
                "task changed concurrently; retry the Contract write",
            )
        _insert_contract_revision(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
            revision=next_revision,
            values=values,
            created_at=now,
        )
        cursor = connection.execute(
            """
            UPDATE tasks
               SET current_contract_revision = :current_contract_revision,
                   completion_commit_required = :completion_commit_required,
                   completion_commit_hash = :completion_commit_hash,
                   completion_evidence_kind = :completion_evidence_kind,
                   completion_evidence_revision = :completion_evidence_revision,
                   completion_evidence_reason = :completion_evidence_reason,
                   external_revision_approved = :external_revision_approved,
                   review_target_kind = :review_target_kind,
                   review_target_value = :review_target_value,
                   review_target_base_revision = :review_target_base_revision,
                   review_target_generation = :review_target_generation,
                   status = :status,
                   updated_at = :updated_at
             WHERE project_id = :project_id
               AND task_id = :task_id
               AND current_contract_revision = :expected_revision
            """,
            {
                **update_values,
                "updated_at": now,
                "project_id": project.project_id,
                "task_id": existing["task_id"],
                "expected_revision": current_revision,
            },
        )
        if cursor.rowcount != 1:
            raise TaskRepositoryError(
                "contract_write_conflict",
                "Task Contract revision conflicted with current state",
            )
        event = create_task_event(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
            event_type="contract_revised",
            summary=bounded_transition_summary(
                "Contract revised; Reason: ",
                values["change_reason"],
                recorded_markers=[
                    f"revision {next_revision}",
                    f"authority {values['authority_ref']}",
                ],
                note=None,
            ),
            created_at=now,
        )
        stored = _read_internal_task(
            connection,
            project_id=project.project_id,
            task_id=str(existing["task_id"]),
        )
        if stored is None:
            raise TaskRepositoryError(
                "internal_error",
                "task was not readable after Contract revision",
            )
    except sqlite3.IntegrityError as exc:
        _rollback_savepoint(connection, savepoint)
        raise TaskRepositoryError(
            "contract_write_conflict",
            "Task Contract revision conflicted with current state",
        ) from exc
    except Exception:
        _rollback_savepoint(connection, savepoint)
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
    return ContractEditResult(
        task=row_to_task(stored),
        changed_fields=changed_fields,
        event=event,
        contract_write=ContractWriteResult(
            recorded=True,
            revision=next_revision,
        ),
    )


def edit_contract(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    existing: dict[str, Any],
    *,
    caller_edit_input: dict[str, Any],
    contract_input: dict[str, Any],
) -> ContractEditResult:
    current_revision = validate_sqlite_int64(
        existing["current_contract_revision"],
        field="current_contract_revision",
    )
    if current_revision == 0:
        return _activate_revision_zero(
            connection,
            project,
            existing,
            caller_edit_input=caller_edit_input,
            contract_input=contract_input,
        )
    return _later_revision(
        connection,
        project,
        existing,
        caller_edit_input=caller_edit_input,
        contract_input=contract_input,
    )
