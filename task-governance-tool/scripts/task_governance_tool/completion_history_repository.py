"""Bounded completion history reads on the caller-owned connection.

Shared cycle types and validation remain storage-owned. Callers retain snapshot,
transaction, and public projection ownership; these readers perform no writes.
"""

from __future__ import annotations

import sqlite3


def read_latest_completion_cycle(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> _storage.CompletionCycle | None:
    from task_governance_tool.evidence_validation_repository import (
        _validate_selected_completion_cycle_evidence,
    )
    from task_governance_tool.storage import (
        _cycle_from_row,
        _validate_cycle_receipts,
    )

    row = connection.execute(
        """
        SELECT *
          FROM task_completion_cycles
         WHERE project_id = ? AND task_id = ?
         ORDER BY saved_cycle_ordinal DESC
         LIMIT 1
        """,
        (project_id, task_id),
    ).fetchone()
    if row is None:
        return None
    cycle = _cycle_from_row(row)
    _validate_cycle_receipts(connection, cycle)
    _validate_selected_completion_cycle_evidence(
        connection,
        project_id=project_id,
        cycles=(cycle,),
    )
    return cycle


def _completion_history_metadata(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_ids: tuple[str, ...],
) -> dict[str, tuple[int, bool]]:
    from task_governance_tool.storage import (
        _completion_int,
        completion_history_inconsistent,
    )

    if not task_ids:
        return {}
    placeholders = ", ".join("?" for _ in task_ids)
    task_rows = connection.execute(
        f"""
        SELECT task_id, completion_history_coverage
          FROM tasks
         WHERE project_id = ?
           AND task_id IN ({placeholders})
        """,
        (project_id, *task_ids),
    ).fetchall()
    if len(task_rows) != len(task_ids):
        raise completion_history_inconsistent()
    metadata = {
        str(row["task_id"]): [
            0,
            str(row["completion_history_coverage"]) != "complete",
        ]
        for row in task_rows
    }
    cycle_rows = connection.execute(
        f"""
        SELECT task_id, COUNT(*) AS total,
               MAX(CASE WHEN completeness = 'partial' THEN 1 ELSE 0 END)
                 AS has_partial
          FROM task_completion_cycles
         WHERE project_id = ?
           AND task_id IN ({placeholders})
         GROUP BY task_id
        """,
        (project_id, *task_ids),
    ).fetchall()
    for row in cycle_rows:
        item = metadata[str(row["task_id"])]
        item[0] = _completion_int(row["total"])
        item[1] = bool(item[1]) or bool(
            _completion_int(row["has_partial"], maximum=1)
        )
    reopen_rows = connection.execute(
        f"""
        SELECT task_id, COUNT(*) AS total
          FROM task_events
         WHERE project_id = ?
           AND task_id IN ({placeholders})
           AND event_type = 'task_reopened'
           AND completion_cycle_id IS NULL
         GROUP BY task_id
        """,
        (project_id, *task_ids),
    ).fetchall()
    for row in reopen_rows:
        if _completion_int(row["total"]):
            metadata[str(row["task_id"])][1] = True
    return {
        task_id: (int(values[0]), bool(values[1]))
        for task_id, values in metadata.items()
    }


def read_completion_history(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    limit: int = 10,
) -> _storage.CompletionHistory:
    from task_governance_tool.evidence_validation_repository import (
        _validate_selected_completion_cycle_evidence,
        _validate_selected_schema21_completion_bundle_history,
    )
    from task_governance_tool.storage import (
        CompletionHistory,
        StorageError,
        _cycle_from_row,
        _validate_cycle_receipts,
    )

    if type(limit) is not int or not 1 <= limit <= 10:
        raise StorageError(
            "internal_error",
            "completion history limit must be between 1 and 10",
        )
    metadata = _completion_history_metadata(
        connection,
        project_id=project_id,
        task_ids=(task_id,),
    )
    total, incomplete = metadata[task_id]
    rows = connection.execute(
        """
        SELECT *
          FROM task_completion_cycles
         WHERE project_id = ? AND task_id = ?
         ORDER BY saved_cycle_ordinal DESC
         LIMIT ?
        """,
        (project_id, task_id, limit),
    ).fetchall()
    cycles = tuple(_cycle_from_row(row) for row in rows)
    for cycle in cycles:
        _validate_cycle_receipts(connection, cycle)
    _validate_selected_completion_cycle_evidence(
        connection,
        project_id=project_id,
        cycles=cycles,
    )
    _validate_selected_schema21_completion_bundle_history(connection, cycles=cycles)
    return CompletionHistory(
        total=total,
        legacy_history_incomplete=incomplete,
        cycles=cycles,
    )


def read_completion_histories_for_tasks(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_ids: tuple[str, ...],
    limit: int = 10,
) -> dict[str, _storage.CompletionHistory]:
    """Read bounded histories for at most the Viewer's existing 500 Tasks."""

    from task_governance_tool.evidence_validation_repository import (
        _validate_selected_completion_cycle_evidence,
    )
    from task_governance_tool.storage import (
        CompletionHistory,
        StorageError,
        _cycle_from_row,
        _validate_cycle_receipts_batch,
    )

    if (
        type(limit) is not int
        or not 1 <= limit <= 10
        or len(task_ids) > 500
        or len(task_ids) != len(set(task_ids))
        or any(not isinstance(task_id, str) or not task_id for task_id in task_ids)
    ):
        raise StorageError(
            "internal_error",
            "completion history batch request is invalid",
        )
    if not task_ids:
        return {}
    metadata = _completion_history_metadata(
        connection,
        project_id=project_id,
        task_ids=task_ids,
    )
    placeholders = ", ".join("?" for _ in task_ids)
    rows = connection.execute(
        f"""
        SELECT *
          FROM (
            SELECT cycle.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY task_id
                     ORDER BY saved_cycle_ordinal DESC
                   ) AS bounded_row_number
              FROM task_completion_cycles AS cycle
             WHERE project_id = ?
               AND task_id IN ({placeholders})
          )
         WHERE bounded_row_number <= ?
         ORDER BY task_id COLLATE BINARY, saved_cycle_ordinal DESC
        """,
        (project_id, *task_ids, limit),
    ).fetchall()
    grouped: dict[str, list[CompletionCycle]] = {
        task_id: [] for task_id in task_ids
    }
    cycles = tuple(_cycle_from_row(row) for row in rows)
    _validate_cycle_receipts_batch(
        connection,
        project_id=project_id,
        cycles=cycles,
    )
    _validate_selected_completion_cycle_evidence(
        connection,
        project_id=project_id,
        cycles=cycles,
    )
    for cycle in cycles:
        grouped[cycle.task_id].append(cycle)
    return {
        task_id: CompletionHistory(
            total=metadata[task_id][0],
            legacy_history_incomplete=metadata[task_id][1],
            cycles=tuple(grouped[task_id]),
        )
        for task_id in task_ids
    }


# Import after the readers so storage can re-export them in either import order.
from task_governance_tool import storage as _storage
