"""Next-actionable task selection helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.ordering import (
    canonical_lane_sql,
    duplicate_lane_order_sql,
    incomplete_predecessor_sql,
)
from task_governance_tool.storage import ProjectIdentity
from task_governance_tool.tasks import (
    KINDS,
    PRIORITIES,
    fetch_stored_task_rows,
    row_to_task,
    validate_choice,
    validate_current_stored_task_rows,
    validate_lane,
    validate_limit,
)


PRIORITY_ORDER = ("urgent", "high", "normal", "low")


@dataclass(frozen=True)
class TaskNextResult:
    tasks: list[dict[str, Any]]
    count: int
    total_matching: int
    limit: int
    selection_rules: dict[str, Any]


def selection_rules() -> dict[str, Any]:
    return {
        "status": "ready",
        "optional": "ready optional tasks are actionable",
        "sequential": "ready sequential tasks require earlier lane tasks to be done or cancelled",
        "blocked_lanes": "blocked or incomplete earlier sequential tasks block only their lane",
        "priority_order": list(PRIORITY_ORDER),
    }


def next_task_readiness_sql(task_alias: str = "task") -> str:
    return (
        f"({task_alias}.kind = 'optional' OR "
        f"(NOT {incomplete_predecessor_sql(task_alias)} "
        f"AND NOT {duplicate_lane_order_sql(task_alias)}))"
    )


def next_task_filters(
    project: ProjectIdentity,
    *,
    kind: Any = None,
    lane: Any = None,
    priority: Any = None,
) -> tuple[list[str], list[Any]]:
    filters = ["task.project_id = ?", "task.status = 'ready'"]
    values: list[Any] = [project.project_id]
    if kind is not None:
        filters.append("task.kind = ?")
        values.append(validate_choice("kind", kind, KINDS, "invalid_kind"))
    if lane is not None:
        filters.append(f"{canonical_lane_sql('task.lane')} = ?")
        values.append(validate_lane(lane))
    if priority is not None:
        filters.append("task.priority = ?")
        values.append(validate_choice("priority", priority, PRIORITIES, "invalid_priority"))
    return filters, values


def count_next_tasks(connection: sqlite3.Connection, project_id: str) -> int:
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS count
          FROM tasks AS task
         WHERE task.project_id = ?
           AND task.status = 'ready'
           AND {next_task_readiness_sql("task")}
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return 0
    if isinstance(row, sqlite3.Row):
        return int(row["count"])
    return int(row[0])


def select_next_tasks(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    kind: Any = None,
    lane: Any = None,
    priority: Any = None,
    limit: Any = None,
) -> TaskNextResult:
    filters, values = next_task_filters(project, kind=kind, lane=lane, priority=priority)
    row_limit = validate_limit(limit, default=5)
    values.append(row_limit)
    rows = fetch_stored_task_rows(
        connection,
        f"""
        SELECT task.*, COUNT(*) OVER() AS total_matching
          FROM tasks AS task
         WHERE {" AND ".join(filters)}
           AND {next_task_readiness_sql("task")}
         ORDER BY
           CASE task.priority
             WHEN 'urgent' THEN 0
             WHEN 'high' THEN 1
             WHEN 'normal' THEN 2
             ELSE 3
           END,
           {canonical_lane_sql("task.lane")},
           task.lane_order IS NULL,
           task.lane_order,
           task.created_at,
           task.task_id
         LIMIT ?
        """,
        values,
    )
    validate_current_stored_task_rows(
        rows,
        expected_project_id=project.project_id,
    )
    tasks = [row_to_task(row) for row in rows]
    return TaskNextResult(
        tasks=tasks,
        count=len(tasks),
        total_matching=(int(rows[0]["total_matching"]) if rows else 0),
        limit=row_limit,
        selection_rules=selection_rules(),
    )
