"""Read-only snapshot assembly for the static Task Viewer."""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
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
TEMPLATE_PLACEHOLDER = "__TASKGOV_SNAPSHOT_BASE64__"
TEMPLATE_RELATIVE_PATH = Path("assets") / "task-viewer.template.html"


@dataclass(frozen=True)
class ViewerError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


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


def viewer_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / TEMPLATE_RELATIVE_PATH


def encode_snapshot(snapshot: dict[str, Any]) -> str:
    if snapshot.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ViewerError("internal_error", "viewer snapshot version is unsupported")
    try:
        serialized = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ViewerError("internal_error", "viewer snapshot could not be serialized") from exc
    return base64.b64encode(serialized).decode("ascii")


def render_viewer_html(
    snapshot: dict[str, Any],
    *,
    template_path: Path | None = None,
) -> str:
    source_path = template_path or viewer_template_path()
    try:
        template = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ViewerError("internal_error", "viewer template could not be read") from exc

    if template.count(TEMPLATE_PLACEHOLDER) != 1:
        raise ViewerError(
            "internal_error",
            "viewer template must contain exactly one snapshot placeholder",
        )
    return template.replace(TEMPLATE_PLACEHOLDER, encode_snapshot(snapshot))
