"""Shared sequential-lane ordering predicates."""

from __future__ import annotations

import sqlite3


ADVANCED_STATUSES = ("in_progress", "review_pending", "done")
LANE_SQL_FUNCTION = "taskgov_canonical_lane"


def canonical_lane(value: object) -> str:
    """Return the shared in-memory and SQLite lane representation."""
    return "" if value is None else str(value).strip()


def canonical_lane_sql(expression: str) -> str:
    return f"{LANE_SQL_FUNCTION}({expression})"


def incomplete_predecessor_sql(task_alias: str = "task") -> str:
    """Return the shared predicate for an incomplete earlier lane task."""
    return f"""
    EXISTS (
      SELECT 1
        FROM tasks AS earlier
       WHERE earlier.project_id = {task_alias}.project_id
         AND earlier.kind = 'sequential'
         AND {canonical_lane_sql("earlier.lane")} =
             {canonical_lane_sql(f"{task_alias}.lane")}
         AND earlier.lane_order < {task_alias}.lane_order
         AND earlier.status NOT IN ('done', 'cancelled')
    )
    """


def duplicate_lane_order_sql(task_alias: str = "task") -> str:
    """Return the shared predicate for a canonical lane/order collision."""
    return f"""
    EXISTS (
      SELECT 1
        FROM tasks AS duplicate
       WHERE duplicate.project_id = {task_alias}.project_id
         AND duplicate.kind = 'sequential'
         AND duplicate.task_id != {task_alias}.task_id
         AND {canonical_lane_sql("duplicate.lane")} =
             {canonical_lane_sql(f"{task_alias}.lane")}
         AND duplicate.lane_order = {task_alias}.lane_order
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
    ordered_lanes = sorted({canonical_lane(lane) for lane in lanes})
    placeholders = ", ".join("?" for _ in ordered_lanes)
    row = connection.execute(
        f"""
        SELECT task.task_id
          FROM tasks AS task
         WHERE task.project_id = ?
           AND task.kind = 'sequential'
           AND {canonical_lane_sql("task.lane")} IN ({placeholders})
           AND task.status IN ('in_progress', 'review_pending', 'done')
           AND {incomplete_predecessor_sql('task')}
         ORDER BY {canonical_lane_sql("task.lane")}, task.lane_order, task.task_id
         LIMIT 1
        """,
        [project_id, *ordered_lanes],
    ).fetchone()
    if row is None:
        return None
    return str(row["task_id"] if isinstance(row, sqlite3.Row) else row[0])
