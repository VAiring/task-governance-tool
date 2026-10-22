"""Admitted native basis and result inventory for the exceptional helper.

File/host composition owns no SQL. This layer opens a validated snapshot or
short writer, and delegates all continuation writes to the core repository.
"""

from contextlib import closing
from dataclasses import dataclass, field

from . import review_continuation as values, review_continuation_repository as records
from .review_wait_basis_repository import read_preparation_basis
from .review_session_repository import read_bindings
from .storage import begin_initialized_write, connect_initialized, connect_initialized_readonly, current_schema_version
from .task_values import validation_error


@dataclass(frozen=True)
class HandoffState:
    basis: dict
    records: tuple
    heads: tuple
    bindings: dict
    captures: dict
    collected_captures: dict = field(default_factory=dict)
    registered_batches: dict = field(default_factory=dict)


def read_state(target, task_id, caller):
    with closing(connect_initialized_readonly(target, operation_local=True, review_task_id=task_id)) as connection:
        if current_schema_version(connection) < 29:
            raise validation_error("review_continuation_unavailable", "review continuation requires the current schema")
        basis = read_preparation_basis(connection, project_id=target.project.project_id, task_id=task_id, caller=caller)
        history = records.read_continuations(connection, project_id=basis["project_id"], task_id=task_id)
        nodes = records.read_judgments(connection, basis=basis, _validated_continuations=history)
        effective = values.select_effective(nodes, quality_basis=values.quality_identity(basis))
        bindings = read_bindings(connection, receipt_ids={row.receipt_id for row in effective.heads})
        captures = {record.continuation_id: records.read_retained_originals(connection, record.continuation_id)
            for record in history if record.basis["target_generation"] == basis["target_generation"]}
        collected = {}
        if current_schema_version(connection) >= 30:
            from .review_answer_repository import read_native_collected_captures
            collected = {record.continuation_id: read_native_collected_captures(connection, record.continuation_id)
                for record in history if record.basis["target_generation"] == basis["target_generation"]}
        registered_batches = {}
        current = tuple(row for row in history if row.basis == basis)
        if current_schema_version(connection) >= 30 and current:
            from .review_answer_repository import read_continuation_dispatch
            found = read_continuation_dispatch(connection, current[-1])
            if found is not None:
                registered_batches[current[-1].continuation_id] = found
        return HandoffState(basis, history, effective.heads, bindings, captures, collected, registered_batches)


def admit(target, basis, preparation_id, route, selected, caller, *, retirement=None, retained_originals=(), retained_collected=(), continuation_id=None, rejection_audit=None):
    with closing(connect_initialized(target, operation_local=True, review_task_id=basis["task_id"])) as connection, connection:
        begin_initialized_write(connection, target, operation_local=True, review_task_id=basis["task_id"])
        return records.admit_locked(connection, basis=basis, preparation_id=preparation_id,
            route=route, selected=selected, caller=caller, retirement=retirement, retained_originals=retained_originals,
            retained_collected=retained_collected, continuation_id=continuation_id, rejection_audit=rejection_audit)


def prepared(target, record, caller):
    with closing(connect_initialized(target, operation_local=True, review_task_id=record.basis["task_id"])) as connection, connection:
        begin_initialized_write(connection, target, operation_local=True, review_task_id=record.basis["task_id"])
        return records.advance_prepared_locked(connection, record, caller)
