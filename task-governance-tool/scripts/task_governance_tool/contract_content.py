"""Contract content selection, normalization, and stored-row projection.

Inputs are existing values or supplied rows; revision reads, writes, lifecycle,
and transaction ownership remain in contracts.py and storage.py.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from task_governance_tool.tasks import TaskRepositoryError
from task_governance_tool.task_values import (
    TaskValidationError,
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


CONTRACT_LIMITS = {
    "contract_scope": 4000,
    "contract_acceptance": 4000,
    "contract_constraints": 2000,
    "contract_authority_ref": 500,
    "contract_change_reason": 1000,
}


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
