"""Core Receipt/session relation; independent of numerical usage storage.

The registering service owns its existing Receipt/Reference/Finding/event
transaction and provenance validation. These primitives neither open a database
nor begin/commit a transaction. Public writers/admission are connected only at
unit 40's schema-25 activation boundary, not by a missing-table fallback.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from task_governance_tool.session_identity import CallerIdentity, is_session_id
from task_governance_tool.task_ownership import OwnershipBasis, read_basis
from task_governance_tool.task_values import TaskValidationError, validation_error


def _identifier(value: object, prefix: str) -> bool:
    return (type(value) is str and value.startswith(prefix)
            and len(value) == len(prefix) + 16
            and all(char in "0123456789abcdef" for char in value[len(prefix):]))


def _invalid():
    return validation_error("invalid_review_evidence", "review session binding is invalid")


def _mismatch():
    return validation_error("review_receipt_mismatch", "review session binding does not match the current receipt basis")


def _inconsistent():
    from task_governance_tool.storage import evidence_ledger_inconsistent
    return evidence_ledger_inconsistent()


@dataclass(frozen=True)
class ReviewSessionBinding:
    session_id: str
    execution_id: str
    binding_source: str
    original_result_digest: str | None = None

    def __post_init__(self) -> None:
        digest = self.original_result_digest
        if (not is_session_id(self.session_id)
            or not _identifier(self.execution_id, "tg_execution_")
            or self.binding_source not in ("direct", "handoff")
            or (self.binding_source == "direct" and digest is not None)
            or (self.binding_source == "handoff" and not (
                type(digest) is str and len(digest) == 64
                and all(char in "0123456789abcdef" for char in digest)))):
            raise _invalid()


@dataclass(frozen=True)
class ReviewSessionTarget:
    """The already-validated Packet/Receipt basis; no duplicated persisted prose."""

    project_id: str
    task_id: str
    contract_revision: int
    target_kind: str
    target_value: str
    target_base_revision: str
    target_generation: int


def _receipt_target(connection: sqlite3.Connection, receipt_id: str):
    rows = connection.execute(
        """
        SELECT receipt.project_id, receipt.task_id, receipt.reviewer_key, receipt.receipt_kind,
               receipt.target_kind, receipt.target_value, receipt.target_base_revision,
               receipt.target_generation, reference.contract_revision,
               reference.project_id AS reference_project, reference.task_id AS reference_task,
               reference.target_kind AS reference_kind, reference.target_value AS reference_value,
               reference.target_base_revision AS reference_base,
               reference.target_generation AS reference_generation
        FROM review_receipts AS receipt LEFT JOIN evidence_references AS reference
          ON reference.source_kind = 'review_receipt' AND reference.source_state = 'recorded'
             AND reference.source_id = receipt.review_receipt_id
        WHERE receipt.review_receipt_id = ? LIMIT 2
        """, (receipt_id,),
    ).fetchall()
    if len(rows) != 1:
        raise _inconsistent()
    row = rows[0]
    target = ReviewSessionTarget(**{field: row[field] for field in ReviewSessionTarget.__dataclass_fields__})
    if (type(target.contract_revision) is not int or target.contract_revision < 0
        or (row["reference_project"], row["reference_task"], row["reference_kind"],
            row["reference_value"], row["reference_base"], row["reference_generation"])
        != (target.project_id, target.task_id, target.target_kind, target.target_value,
            target.target_base_revision, target.target_generation)):
        raise _inconsistent()
    return target, row["receipt_kind"], row["reviewer_key"]


def _has_alias(connection: sqlite3.Connection, target: ReviewSessionTarget,
               session_id: str, reviewer_key: str) -> bool:
    return connection.execute(
        """
        SELECT 1 FROM review_receipt_sessions AS binding
        JOIN review_receipts AS receipt ON receipt.review_receipt_id = binding.review_receipt_id
        JOIN evidence_references AS reference
          ON reference.source_id = receipt.review_receipt_id
             AND reference.source_kind = 'review_receipt' AND reference.source_state = 'recorded'
        WHERE binding.session_id = ? AND receipt.project_id = ? AND receipt.task_id = ?
          AND reference.contract_revision = ? AND receipt.target_kind = ? AND receipt.target_value = ?
          AND receipt.target_base_revision = ? AND receipt.target_generation = ?
          AND receipt.reviewer_key != ? LIMIT 1
        """, (session_id, target.project_id, target.task_id, target.contract_revision,
              target.target_kind, target.target_value, target.target_base_revision,
              target.target_generation, reviewer_key),
    ).fetchone() is not None


def direct_binding(ownership: OwnershipBasis, caller: CallerIdentity) -> ReviewSessionBinding | None:
    """Old owner-forwarded input remains explicitly unbound, never parent cost."""
    actor = caller.require()
    if actor in (ownership.owner_session_id, ownership.completion_session_id):
        return None
    if ownership.state not in ("owned", "completion_only") or ownership.execution_id is None:
        raise _mismatch()
    return ReviewSessionBinding(actor, ownership.execution_id, "direct")


def require_review_writer(connection: sqlite3.Connection, observed: OwnershipBasis,
                          caller: CallerIdentity, binding: ReviewSessionBinding) -> OwnershipBasis:
    """Narrow registration/Finding authority, not an owner-only mutation bypass."""
    if not connection.in_transaction:
        raise RuntimeError("review session binding requires an existing writer")
    current = read_basis(connection, project_id=observed.project_id, task_id=observed.task_id)
    if current != observed:
        raise validation_error("task_ownership_changed", "task ownership changed; inspect current task state")
    if current.state not in ("owned", "completion_only") or current.execution_id != binding.execution_id:
        raise _mismatch()
    actor = caller.require()
    owner = current.owner_session_id or current.completion_session_id
    if ((binding.binding_source == "direct" and actor != binding.session_id)
        or (binding.binding_source == "handoff" and actor not in (owner, binding.session_id))):
        raise _mismatch()
    return current


def insert_binding_locked(connection: sqlite3.Connection, *, receipt_id: str,
                          binding: ReviewSessionBinding, target: ReviewSessionTarget,
                          observed_ownership: OwnershipBasis, caller: CallerIdentity) -> None:
    """Append after the new Receipt and its Reference, in their one writer.

    The helper and registration decoder must validate handoff bytes/digest and
    transport completeness before this call. A typed binding is never inferred
    from a reviewer name, provenance declaration, parent ID or numerical record.
    """
    if not connection.in_transaction:
        raise RuntimeError("review session binding requires an existing writer")
    if not _identifier(receipt_id, "tg_review_receipt_"):
        raise _invalid()
    if (target.project_id, target.task_id) != (observed_ownership.project_id, observed_ownership.task_id):
        raise _mismatch()
    current = require_review_writer(connection, observed_ownership, caller, binding)
    owner = current.owner_session_id or current.completion_session_id
    stored_target, kind, key = _receipt_target(connection, receipt_id)
    row = connection.execute(
        "SELECT current_contract_revision, review_target_kind, review_target_value, "
        "review_target_base_revision, review_target_generation FROM tasks WHERE project_id=? AND task_id=?",
        (target.project_id, target.task_id),
    ).fetchone()
    if (stored_target != target or row is None
        or tuple(row) != (target.contract_revision, target.target_kind, target.target_value,
                         target.target_base_revision, target.target_generation)
        or (kind == "independent" and binding.session_id == owner)):
        raise _mismatch()
    if _has_alias(connection, target, binding.session_id, key):
        raise validation_error("review_receipt_already_recorded", "this session already recorded a review under a different reviewer key")
    connection.execute(
        "INSERT INTO review_receipt_sessions "
        "(review_receipt_id, session_id, execution_id, binding_source, original_result_digest) VALUES (?, ?, ?, ?, ?)",
        (receipt_id, binding.session_id, binding.execution_id, binding.binding_source, binding.original_result_digest),
    )


def read_bindings(connection: sqlite3.Connection, *, receipt_ids: set[str] | None = None
                  ) -> dict[str, ReviewSessionBinding]:
    """Read/validate selected or global immutable bindings, including past targets.

    Unbound legacy Receipts remain absent. Missing tables or corrupt bindings
    are errors, never an invitation to treat a new bound result as legacy.
    Existing Receipt/Reference admission still validates their full contracts.
    """
    parameters = () if receipt_ids is None else (json.dumps(sorted(receipt_ids)),)
    selection = "" if receipt_ids is None else " WHERE binding.review_receipt_id IN (SELECT value FROM json_each(?))"
    rows = connection.execute(
        "SELECT binding.*, execution.project_id AS execution_project, execution.task_id AS execution_task "
        "FROM review_receipt_sessions AS binding LEFT JOIN task_executions AS execution "
        "ON execution.execution_id = binding.execution_id" + selection, parameters,
    )
    result = {}
    for row in rows:
        receipt_id = row["review_receipt_id"]
        try:
            binding = ReviewSessionBinding(**{field: row[field] for field in ReviewSessionBinding.__dataclass_fields__})
        except TaskValidationError as exc:
            raise _inconsistent() from exc
        if not _identifier(receipt_id, "tg_review_receipt_"):
            raise _inconsistent()
        target, _, key = _receipt_target(connection, receipt_id)
        if ((row["execution_project"], row["execution_task"]) != (target.project_id, target.task_id)
            or _has_alias(connection, target, binding.session_id, key)):
            raise _inconsistent()
        result[receipt_id] = binding
    return result
