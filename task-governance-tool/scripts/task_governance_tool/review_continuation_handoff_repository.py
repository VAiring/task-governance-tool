"""Admitted native basis and result inventory for the exceptional helper.

File/host composition owns no SQL. This layer opens a validated snapshot or
short writer, and delegates all continuation writes to the core repository.
"""

from contextlib import closing
from dataclasses import dataclass

from . import review_continuation as values, review_continuation_repository as records
from .review_wait_basis_repository import read_preparation_basis
from .review_session_repository import read_bindings
from .storage import connect_initialized, connect_initialized_readonly, current_schema_version
from .task_values import validation_error


@dataclass(frozen=True)
class HandoffState:
    basis: dict
    records: tuple
    heads: tuple
    bindings: dict
    captures: dict


def read_state(target, task_id, caller):
    with closing(connect_initialized_readonly(target)) as connection:
        if current_schema_version(connection) < 29:
            raise validation_error("review_continuation_unavailable", "review continuation requires the current schema")
        basis = read_preparation_basis(connection, project_id=target.project.project_id, task_id=task_id, caller=caller)
        history = records.read_continuations(connection, project_id=basis["project_id"], task_id=task_id)
        nodes = records.read_judgments(connection, basis=basis, _validated_continuations=history)
        effective = values.select_effective(nodes, quality_basis=values.quality_identity(basis))
        bindings = read_bindings(connection, receipt_ids={row.receipt_id for row in effective.heads})
        captures = {record.continuation_id: records.read_retained_originals(connection, record.continuation_id)
            for record in history if record.basis["target_generation"] == basis["target_generation"]}
        return HandoffState(basis, history, effective.heads, bindings, captures)


def admit(target, basis, preparation_id, route, selected, caller, *, retirement=None, retained_originals=(), continuation_id=None):
    with closing(connect_initialized(target)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        return records.admit_locked(connection, basis=basis, preparation_id=preparation_id,
            route=route, selected=selected, caller=caller, retirement=retirement, retained_originals=retained_originals,
            continuation_id=continuation_id)


def prepared(target, record, caller):
    with closing(connect_initialized(target)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        return records.advance_prepared_locked(connection, record, caller)
