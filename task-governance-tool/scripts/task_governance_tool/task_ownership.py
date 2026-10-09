"""Task-local ownership repository and policy; callers own the writer transaction.

This module neither reads the environment nor begins/commits a transaction.
Admission and services pass the exact selected Task basis and typed caller.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass, replace

from task_governance_tool.session_identity import CallerIdentity, is_session_id
from task_governance_tool.task_values import (
    SQLITE_INT64_MAX, TaskValidationError, validate_task_id, validation_error,
)


@dataclass(frozen=True)
class OwnershipBasis:
    project_id: str
    task_id: str
    status: str
    execution_id: str | None
    generation: int
    state: str
    owner_session_id: str | None
    completion_session_id: str | None

    def projection(self, caller: CallerIdentity) -> dict[str, object]:
        known = caller.session_id is not None and self.state != "unknown"
        return {
            "state": self.state,
            "owner_session_id": self.owner_session_id,
            "completion_session_id": self.completion_session_id,
            "execution_id": self.execution_id,
            "generation": self.generation,
            "is_owner": caller.session_id == self.owner_session_id if known else None,
            "is_completion_owner": caller.session_id == self.completion_session_id if known else None,
        }


def _unreadable():
    from task_governance_tool.storage import StorageError
    return StorageError("project_state_unreadable", "project state could not be read safely")


def _identifier(value: object, prefix: str) -> bool:
    return (isinstance(value, str) and value.startswith(prefix)
            and len(value) == len(prefix) + 16
            and all(char in "0123456789abcdef" for char in value[len(prefix):]))


def validate_basis(basis: OwnershipBasis) -> None:
    # Task IDs predate ownership and retain their established stored/public
    # contract. Only the new execution/transition IDs use our generated shape.
    try:
        if validate_task_id(basis.task_id) != basis.task_id:
            raise _unreadable()
    except TaskValidationError as exc:
        raise _unreadable() from exc
    if (not isinstance(basis.project_id, str) or not basis.project_id
        or type(basis.generation) is not int or not 0 <= basis.generation <= SQLITE_INT64_MAX
        or basis.status not in ("ready", "in_progress", "review_pending", "paused", "blocked", "done", "cancelled")
        or (basis.execution_id is not None and not _identifier(basis.execution_id, "tg_execution_"))
        or any(value is not None and not is_session_id(value)
               for value in (basis.owner_session_id, basis.completion_session_id))):
        raise _unreadable()
    if basis.state == "owned":
        valid = (basis.status == "in_progress" and basis.execution_id is not None
                 and basis.generation > 0 and basis.owner_session_id is not None
                 and basis.completion_session_id is None)
    elif basis.state == "completion_only":
        valid = (basis.status == "review_pending" and basis.execution_id is not None
                 and basis.generation > 0 and basis.owner_session_id is None
                 and basis.completion_session_id is not None)
    elif basis.state == "unknown":
        valid = (basis.status in ("in_progress", "review_pending") and basis.generation == 0
                 and basis.execution_id is None and basis.owner_session_id is None
                 and basis.completion_session_id is None)
    else:
        valid = (basis.state == "none" and basis.status not in ("in_progress", "review_pending")
                 and basis.owner_session_id is None and basis.completion_session_id is None
                 and (basis.execution_id is None or basis.generation > 0))
    if not valid:
        raise _unreadable()


def read_bases(connection: sqlite3.Connection, *, project_id: str,
               task_ids: list[str]) -> dict[str, OwnershipBasis]:
    """Validate selected relationships in bounded batches, never a global scan."""
    result = {}
    for offset in range(0, len(task_ids), 128):
        chunk = task_ids[offset:offset + 128]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            "SELECT ownership.*, task.status, execution.execution_id AS existing_execution "
            "FROM tasks AS task JOIN task_ownership AS ownership "
            "ON ownership.task_id=task.task_id AND ownership.project_id=task.project_id "
            "LEFT JOIN task_executions AS execution ON execution.execution_id=ownership.execution_id "
            "AND execution.project_id=task.project_id AND execution.task_id=task.task_id "
            f"WHERE task.project_id=? AND task.task_id IN ({placeholders})", (project_id, *chunk),
        ).fetchall()
        for row in rows:
            basis = OwnershipBasis(**{field: row[field] for field in OwnershipBasis.__dataclass_fields__})
            validate_basis(basis)
            if basis.execution_id is not None and row["existing_execution"] is None:
                raise _unreadable()
            result[basis.task_id] = basis
    if set(result) != set(task_ids):
        raise _unreadable()
    return result


def read_basis(connection: sqlite3.Connection, *, project_id: str, task_id: str) -> OwnershipBasis:
    return read_bases(connection, project_id=project_id, task_ids=[task_id])[task_id]


def project_tasks(connection: sqlite3.Connection, tasks: list[dict],
                  caller: CallerIdentity) -> list[dict]:
    """Attach per-invocation display only; never pass it to persisted Evidence."""
    from task_governance_tool.storage import current_schema_version
    if not tasks or current_schema_version(connection) < 24:
        return tasks
    project_id = tasks[0]["project_id"]
    if any(task["project_id"] != project_id for task in tasks):
        raise _unreadable()
    bases = read_bases(connection, project_id=project_id, task_ids=[task["task_id"] for task in tasks])
    return [{**task, "ownership": bases[task["task_id"]].projection(caller)} for task in tasks]


def capture_basis(connection: sqlite3.Connection, *, project_id: str,
                  task_id: str) -> OwnershipBasis | None:
    """Older explicit fixtures retain their historical semantics, never a missing-table fallback."""
    from task_governance_tool.storage import current_schema_version
    if current_schema_version(connection) < 24:
        return None
    return read_basis(connection, project_id=project_id, task_id=task_id)


def _locked(connection: sqlite3.Connection, observed: OwnershipBasis) -> OwnershipBasis:
    if not connection.in_transaction:
        raise RuntimeError("ownership mutation requires an existing writer")
    current = read_basis(connection, project_id=observed.project_id, task_id=observed.task_id)
    if current != observed:
        raise validation_error("task_ownership_changed", "task ownership changed; inspect current task state")
    return current


def require_mutation(connection: sqlite3.Connection, observed: OwnershipBasis,
                     caller: CallerIdentity) -> OwnershipBasis:
    current = _locked(connection, observed)
    if current.state in ("owned", "completion_only"):
        actor = caller.require()
        if actor not in (current.owner_session_id, current.completion_session_id):
            raise validation_error("task_not_owned", "the caller does not own this task")
    elif current.state == "unknown" or current.status == "paused":
        caller.require()
        raise validation_error("task_not_owned", "resume the task before changing it")
    return current


def require_completion_owner(basis: OwnershipBasis, caller: CallerIdentity) -> None:
    """Pure prerequisite for check/write; the actual writer still revalidates."""
    actor = caller.require()
    if basis.state not in ("owned", "completion_only") or actor not in (basis.owner_session_id, basis.completion_session_id):
        raise validation_error("task_not_owned", "the caller does not own this task")


def _next_generation(current: OwnershipBasis) -> int:
    if current.generation == SQLITE_INT64_MAX:
        raise validation_error("task_ownership_changed", "task ownership generation cannot advance")
    return current.generation + 1


def _free_slot(connection: sqlite3.Connection, *, project_id: str, task_id: str, actor: str) -> None:
    from task_governance_tool.storage import current_schema_version
    combined = current_schema_version(connection) >= 26
    subject = "coalesce(owner_session_id, completion_session_id)" if combined else "owner_session_id"
    held = "state IN ('owned', 'completion_only')" if combined else "state = 'owned'"
    if connection.execute(
        f"SELECT 1 FROM task_ownership WHERE project_id = ? AND {subject} = ? "
        f"AND {held} AND task_id != ? LIMIT 1", (project_id, actor, task_id),
    ).fetchone() is not None:
        raise validation_error("session_task_in_progress", "the caller already holds an executing or review-pending task")


def _new_execution(connection: sqlite3.Connection, current: OwnershipBasis, now: str) -> str:
    execution_id = f"tg_execution_{secrets.token_hex(8)}"
    connection.execute(
        "INSERT INTO task_executions(execution_id, project_id, task_id, started_at) VALUES (?, ?, ?, ?)",
        (execution_id, current.project_id, current.task_id, now),
    )
    return execution_id


def _record(connection: sqlite3.Connection, previous: OwnershipBasis, current: OwnershipBasis,
            actor: str, now: str, reason: str = "", *, initial: bool = False) -> None:
    from task_governance_tool.storage import current_schema_version
    modern = current_schema_version(connection) >= 26
    validate_basis(current)
    connection.execute(
        "UPDATE task_ownership SET execution_id = ?, generation = ?, state = ?, "
        "owner_session_id = ?, completion_session_id = ? WHERE project_id = ? AND task_id = ?",
        (current.execution_id, current.generation, current.state, current.owner_session_id,
         current.completion_session_id, current.project_id, current.task_id),
    )
    connection.execute(
        "INSERT INTO task_owner_transitions(transition_id, project_id, task_id, execution_id, generation, "
        "previous_status, current_status, state, actor_session_id, recovery_reason, created_at"
        + (", policy_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)" if modern
           else ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
        (f"tg_owner_transition_{secrets.token_hex(8)}", current.project_id, current.task_id,
         current.execution_id, current.generation, None if initial else previous.status,
         current.status, current.state, actor, reason, now),
    )


def initialize_task(connection: sqlite3.Connection, *, project_id: str, task_id: str,
                    status: str, caller: CallerIdentity, now: str) -> None:
    """Called after insertion, in the same single/batch registration transaction."""
    if not connection.in_transaction:
        raise RuntimeError("ownership registration requires an existing writer")
    if status == "review_pending":
        raise validation_error("invalid_status_transition", "new review-pending work must be started first")
    current = OwnershipBasis(project_id, task_id, status, None, 0, "none", None, None)
    actor = None
    if status == "in_progress":
        actor = caller.require()
        _free_slot(connection, project_id=project_id, task_id=task_id, actor=actor)
        current = replace(current, execution_id=_new_execution(connection, current, now), generation=1,
                          state="owned", owner_session_id=actor)
    validate_basis(current)
    connection.execute(
        "INSERT INTO task_ownership(task_id, project_id, execution_id, generation, state, "
        "owner_session_id, completion_session_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, project_id, current.execution_id, current.generation, current.state,
         current.owner_session_id, current.completion_session_id),
    )
    if actor is not None:
        _record(connection, current, current, actor, now, initial=True)


def transition(connection: sqlite3.Connection, observed: OwnershipBasis, caller: CallerIdentity,
               *, status: str, now: str, recovery_reason: str | None = None) -> OwnershipBasis:
    """Apply ownership before the business status update; the caller's savepoint covers both.

    `recovery_reason` is supplied only after validating an isolated pause edit.
    No ordinary note/Contract/metadata mutation may share that recovery request.
    """
    current = _locked(connection, observed)
    if recovery_reason is not None:
        from task_governance_tool.task_values import validate_text
        reason = validate_text("pause_reason", recovery_reason, required=True, limit=1000)
        actor = caller.require()
        if status != "paused" or current.status not in ("in_progress", "review_pending"):
            raise validation_error("invalid_status_transition", "recovery requires an isolated pause")
        result = replace(current, status=status, state="none", owner_session_id=None,
                         completion_session_id=None, generation=_next_generation(current))
        _record(connection, current, result, actor, now, reason)
        return result
    if status == "review_pending" and current.status not in ("in_progress", "review_pending"):
        raise validation_error("invalid_status_transition", "review-pending work must be started first")
    if status == "done" and current.state not in ("owned", "completion_only"):
        caller.require()
        raise validation_error("task_not_owned", "start or resume the task before completing it")
    acquiring = status == "in_progress" and current.status != "in_progress"
    if acquiring and current.status != "review_pending":
        # Pause/block have deliberately released ownership. Ready/cancelled/reopen start afresh.
        actor = caller.require()
    else:
        require_mutation(connection, current, caller)
        actor = caller.session_id
    if acquiring:
        actor = caller.require()
        _free_slot(connection, project_id=current.project_id, task_id=current.task_id, actor=actor)
        execution = current.execution_id
        if execution is None or current.status in ("ready", "cancelled", "done"):
            execution = _new_execution(connection, current, now)
        result = replace(current, status=status, execution_id=execution,
                         generation=_next_generation(current), state="owned",
                         owner_session_id=actor, completion_session_id=None)
    elif status == "review_pending" and current.status == "in_progress":
        result = replace(current, status=status, state="completion_only", owner_session_id=None,
                         completion_session_id=actor)
    elif status in ("ready", "cancelled") and current.execution_id is not None:
        # Ending an execution is distinct from releasing its slot. Clear the
        # live link so a later ready -> blocked -> start cannot revive it.
        actor = caller.require()
        result = replace(current, status=status, execution_id=None, state="none",
                         owner_session_id=None, completion_session_id=None,
                         generation=_next_generation(current))
    elif current.state in ("owned", "completion_only") and status not in ("in_progress", "review_pending"):
        result = replace(current, status=status, state="none", owner_session_id=None,
                         completion_session_id=None, generation=_next_generation(current))
    else:
        # Non-executing organization changes no ownership history or active slot.
        result = replace(current, status=status)
    validate_basis(result)
    if replace(result, status=current.status) != current:
        assert actor is not None
        _record(connection, current, result, actor, now)
    return result


def link_completion_cycle(connection: sqlite3.Connection, basis: OwnershipBasis,
                          *, completion_cycle_id: str) -> None:
    if not connection.in_transaction or basis.execution_id is None:
        raise RuntimeError("completion linkage requires a locked execution")
    connection.execute(
        "INSERT INTO task_execution_cycles(completion_cycle_id, project_id, task_id, execution_id) "
        "VALUES (?, ?, ?, ?)", (completion_cycle_id, basis.project_id, basis.task_id, basis.execution_id),
    )


def validate_storage_rows(connection: sqlite3.Connection) -> None:
    """Global consumers validate all ownership/history; ordinary reads stay Task-local."""
    from task_governance_tool.storage import StorageError, validate_utc_timestamp
    from task_governance_tool.task_values import TaskValidationError, validate_text

    try:
        tasks = connection.execute("SELECT project_id, task_id FROM tasks").fetchall()
        if connection.execute("SELECT count(*) FROM task_ownership").fetchone()[0] != len(tasks):
            raise _unreadable()
        current = {}
        for project in {task["project_id"] for task in tasks}:
            current.update(read_bases(connection, project_id=project,
                           task_ids=[task["task_id"] for task in tasks if task["project_id"] == project]))
        executions = {}
        for row in connection.execute("SELECT * FROM task_executions"):
            if (not _identifier(row["execution_id"], "tg_execution_")
                or row["task_id"] not in current or current[row["task_id"]].project_id != row["project_id"]):
                raise _unreadable()
            validate_utc_timestamp(row["started_at"], field="execution time")
            executions[row["execution_id"]] = (row["project_id"], row["task_id"])
        latest = {}
        policies = {}
        for row in connection.execute("SELECT * FROM task_owner_transitions ORDER BY rowid"):
            policy = row["policy_version"] if "policy_version" in row.keys() else 0
            if type(policy) is not int or policy not in (0, 1) or policy < policies.get(row["task_id"], 0):
                raise _unreadable()
            policies[row["task_id"]] = policy
            if (not _identifier(row["transition_id"], "tg_owner_transition_")
                or row["task_id"] not in current or current[row["task_id"]].project_id != row["project_id"]
                or not is_session_id(row["actor_session_id"])
                or row["previous_status"] not in (None, "ready", "in_progress", "review_pending", "paused", "blocked", "done", "cancelled")
                or type(row["generation"]) is not int or row["generation"] < 1
                or (row["execution_id"] is not None
                    and executions.get(row["execution_id"]) != (row["project_id"], row["task_id"]))):
                raise _unreadable()
            transition_basis = OwnershipBasis(
                row["project_id"], row["task_id"], row["current_status"], row["execution_id"],
                row["generation"], row["state"],
                row["actor_session_id"] if row["state"] == "owned" else None,
                row["actor_session_id"] if row["state"] == "completion_only" else None,
            )
            validate_basis(transition_basis)
            if row["state"] == "unknown":
                raise _unreadable()
            reason = validate_text("pause_reason", row["recovery_reason"], limit=1000)
            if (reason != row["recovery_reason"] or reason and
                (row["current_status"] != "paused" or row["previous_status"] not in ("in_progress", "review_pending"))):
                raise _unreadable()
            validate_utc_timestamp(row["created_at"], field="ownership time")
            previous = latest.get(row["task_id"])
            if (previous is None and row["generation"] != 1
                or previous is not None and row["generation"] not in (previous.generation, previous.generation + 1)):
                raise _unreadable()
            latest[row["task_id"]] = transition_basis
        for task_id, basis in current.items():
            last = latest.get(task_id)
            if basis.generation == 0:
                if last is not None:
                    raise _unreadable()
            elif last is None or replace(last, status=basis.status) != basis:
                raise _unreadable()
        for row in connection.execute(
            "SELECT link.*, cycle.project_id AS cycle_project, cycle.task_id AS cycle_task "
            "FROM task_execution_cycles AS link LEFT JOIN task_completion_cycles AS cycle "
            "ON cycle.completion_cycle_id=link.completion_cycle_id"
        ):
            if (executions.get(row["execution_id"]) != (row["project_id"], row["task_id"])
                or row["cycle_project"] != row["project_id"] or row["cycle_task"] != row["task_id"]):
                raise _unreadable()
        if connection.execute(
            "SELECT 1 FROM completion_evidence_bundles AS bundle "
            "LEFT JOIN task_execution_cycles AS link ON link.completion_cycle_id=bundle.completion_cycle_id "
            "WHERE bundle.source_schema_version IN (24, 25, 26, 27) AND link.completion_cycle_id IS NULL LIMIT 1"
        ).fetchone():
            raise _unreadable()
    except (TaskValidationError, StorageError, UnicodeError, sqlite3.Error) as exc:
        if isinstance(exc, StorageError) and exc.code == "database_busy":
            raise
        from task_governance_tool.storage import is_sqlite_busy_or_locked, DATABASE_BUSY_MESSAGE
        if isinstance(exc, sqlite3.Error) and is_sqlite_busy_or_locked(exc):
            raise StorageError("database_busy", DATABASE_BUSY_MESSAGE) from exc
        raise _unreadable() from exc
