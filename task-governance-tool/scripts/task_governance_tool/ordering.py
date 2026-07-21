"""Shared sequential-lane ordering predicates."""

from __future__ import annotations

import sqlite3


ADVANCED_STATUSES = ("in_progress", "review_pending", "done")


def incomplete_predecessor_sql(task_alias: str = "task") -> str:
    """Return the shared predicate for an incomplete earlier lane task."""
    return f"""
    EXISTS (
      SELECT 1
        FROM tasks AS earlier
       WHERE earlier.project_id = {task_alias}.project_id
         AND earlier.kind = 'sequential'
         AND earlier.lane = {task_alias}.lane
         AND earlier.lane_order < {task_alias}.lane_order
         AND earlier.status NOT IN ('done', 'cancelled')
    )
    """


def first_out_of_order_advanced_task(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    lanes: set[str],
) -> str | None:
    """Find an advanced sequential row whose earlier work is incomplete."""
    if not lanes:
        return None
    placeholders = ", ".join("?" for _ in lanes)
    ordered_lanes = sorted(lanes)
    row = connection.execute(
        f"""
        SELECT task.task_id
          FROM tasks AS task
         WHERE task.project_id = ?
           AND task.kind = 'sequential'
           AND task.lane IN ({placeholders})
           AND task.status IN ('in_progress', 'review_pending', 'done')
           AND {incomplete_predecessor_sql('task')}
         ORDER BY task.lane, task.lane_order, task.task_id
         LIMIT 1
        """,
        [project_id, *ordered_lanes],
    ).fetchone()
    if row is None:
        return None
    return str(row["task_id"] if isinstance(row, sqlite3.Row) else row[0])
