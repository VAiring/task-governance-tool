"""Read-only snapshot assembly for the static Task Viewer."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.storage import (
    DatabaseTarget,
    StorageError,
    utc_now,
    validate_snapshot_database,
)
from task_governance_tool.tasks import STATUSES, list_tasks_for_viewer


SNAPSHOT_VERSION = 1
VIEWER_EVENT_LIMIT = 10


@dataclass(frozen=True)
class ViewerSnapshotResult:
    snapshot: dict[str, Any]
    task_count: int
    event_count: int


def build_viewer_snapshot(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    *,
    generated_at: str | None = None,
) -> ViewerSnapshotResult:
    """Build snapshot version 1 from one validated SQLite read transaction."""
    try:
        source_schema_version = validate_snapshot_database(connection, target)
        task_result = list_tasks_for_viewer(
            connection,
            target.project,
            event_limit=VIEWER_EVENT_LIMIT,
        )
    except StorageError:
        raise
    except sqlite3.Error as exc:
        raise StorageError("internal_error", "could not read viewer snapshot") from exc
    timestamp = generated_at or utc_now()
    counts = {"total": len(task_result.tasks)}
    counts.update(
        {
            status: sum(1 for task in task_result.tasks if task["status"] == status)
            for status in STATUSES
        }
    )
    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION,
        "generated_at": timestamp,
        "project": {
            "project_id": target.project.project_id,
            "display_name": target.project.display_name,
        },
        "source_schema_version": source_schema_version,
        "counts": counts,
        "tasks": task_result.tasks,
    }
    return ViewerSnapshotResult(
        snapshot=snapshot,
        task_count=len(task_result.tasks),
        event_count=task_result.event_count,
    )
