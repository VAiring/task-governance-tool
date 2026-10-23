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


def lane_ordering_cte(*, lane_filter: str = "") -> str:
    """Compute project ordering once, within the caller's SQL snapshot.

    The optional SQL fragment is repository-owned, never user input. The
    caller binds project_id followed by any affected-lane parameters. Keep
    null orders non-colliding, as in the scalar equality predicate above.
    Count only non-null IDs: SQL ``duplicate.task_id != task.task_id``
    excludes null operands. The rowid is private to this one statement.
    """
    return f"""
    lane_ordering AS MATERIALIZED (
      SELECT rowid AS task_rowid, task_id, status, lane_order,
             {canonical_lane_sql('lane')} AS canonical_lane,
             MIN(CASE WHEN status NOT IN ('done', 'cancelled')
                      THEN lane_order END)
               OVER (PARTITION BY {canonical_lane_sql('lane')}) AS first_incomplete,
             COUNT(task_id) OVER (
               PARTITION BY {canonical_lane_sql('lane')}, lane_order
             ) AS same_order_count
        FROM tasks
       WHERE project_id = ? AND kind = 'sequential' {lane_filter}
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
    lane_filter = f"AND {canonical_lane_sql('lane')} IN ({placeholders})"
    row = connection.execute(
        f"""
        WITH {lane_ordering_cte(lane_filter=lane_filter)}
        SELECT task_id
          FROM lane_ordering
         WHERE status IN ('in_progress', 'review_pending', 'done')
           AND first_incomplete < lane_order
         ORDER BY canonical_lane, lane_order, task_id
         LIMIT 1
        """,
        [project_id, *ordered_lanes],
    ).fetchone()
    if row is None:
        return None
    return str(row["task_id"] if isinstance(row, sqlite3.Row) else row[0])
