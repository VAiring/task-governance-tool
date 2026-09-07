"""Viewer publication metadata; rendering and artifact policy stay with callers."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_governance_tool.sqlite_connection import StorageError

if TYPE_CHECKING:
    from task_governance_tool.storage import DatabaseTarget


@dataclass(frozen=True)
class ViewerMaintenanceState:
    project_id: str
    source_generation: int
    rendered_generation: int | None
    last_success_at: str | None
    last_outcome_code: str | None
    last_outcome_at: str | None

    @property
    def due(self) -> bool:
        return (
            self.rendered_generation is None
            or self.rendered_generation < self.source_generation
            or self.last_outcome_code in {"deferred", "failed"}
        )


def read_viewer_maintenance(
    connection: sqlite3.Connection,
    project_id: str,
) -> ViewerMaintenanceState | None:
    from task_governance_tool.storage import (
        validate_utc_timestamp,
        validate_viewer_generation,
    )

    row = connection.execute(
        """
        SELECT project_id, source_generation, rendered_generation,
               last_success_at, last_outcome_code, last_outcome_at
          FROM viewer_maintenance_state
         WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    source_generation = validate_viewer_generation(
        row["source_generation"],
        field="Viewer source generation",
    )
    rendered_generation = (
        validate_viewer_generation(
            row["rendered_generation"],
            field="Viewer rendered generation",
        )
        if row["rendered_generation"] is not None
        else None
    )
    if (
        source_generation < 0
        or (
            rendered_generation is not None
            and (
                rendered_generation < 0
                or rendered_generation > source_generation
            )
        )
    ):
        raise StorageError(
            "internal_error",
            "Viewer generation state is invalid",
        )
    last_success_at = (
        validate_utc_timestamp(
            str(row["last_success_at"]),
            field="Viewer success time",
        )
        if row["last_success_at"] is not None
        else None
    )
    last_outcome_code = (
        str(row["last_outcome_code"])
        if row["last_outcome_code"] is not None
        else None
    )
    if last_outcome_code not in {
        None,
        "succeeded",
        "deferred",
        "failed",
    }:
        raise StorageError("internal_error", "Viewer outcome is invalid")
    last_outcome_at = (
        validate_utc_timestamp(
            str(row["last_outcome_at"]),
            field="Viewer outcome time",
        )
        if row["last_outcome_at"] is not None
        else None
    )
    if (last_outcome_code is None) != (last_outcome_at is None):
        raise StorageError("internal_error", "Viewer outcome is incomplete")
    return ViewerMaintenanceState(
        project_id=str(row["project_id"]),
        source_generation=source_generation,
        rendered_generation=rendered_generation,
        last_success_at=last_success_at,
        last_outcome_code=last_outcome_code,
        last_outcome_at=last_outcome_at,
    )


def ensure_viewer_maintenance_row(
    connection: sqlite3.Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO viewer_maintenance_state(
          project_id, source_generation, rendered_generation
        ) VALUES (?, 0, NULL)
        """,
        (project_id,),
    )


def record_viewer_publication(
    target: DatabaseTarget,
    *,
    source_generation: int,
    published_at: str,
) -> None:
    from task_governance_tool.storage import (
        begin_initialized_write,
        connect_initialized,
        validate_utc_timestamp,
    )

    if (
        isinstance(source_generation, bool)
        or not isinstance(source_generation, int)
        or source_generation < 0
    ):
        raise StorageError(
            "internal_error",
            "Viewer publication generation is invalid",
        )
    timestamp = validate_utc_timestamp(
        published_at,
        field="Viewer publication time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            cursor = connection.execute(
                """
                UPDATE viewer_maintenance_state
                   SET rendered_generation = ?,
                       last_success_at = CASE
                         WHEN last_success_at IS NULL OR last_success_at <= ?
                         THEN ?
                         ELSE last_success_at
                       END,
                       last_outcome_code = CASE
                         WHEN last_outcome_at IS NULL OR last_outcome_at <= ?
                         THEN 'succeeded'
                         ELSE last_outcome_code
                       END,
                       last_outcome_at = CASE
                         WHEN last_outcome_at IS NULL OR last_outcome_at <= ?
                         THEN ?
                         ELSE last_outcome_at
                       END
                 WHERE project_id = ?
                   AND (
                     rendered_generation IS NULL
                     OR rendered_generation <= ?
                   )
                   AND source_generation >= ?
                """,
                (
                    source_generation,
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                    target.project.project_id,
                    source_generation,
                    source_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "internal_error",
                    "Viewer publication generation changed unexpectedly",
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def record_viewer_attempt_outcome(
    target: DatabaseTarget,
    *,
    code: str,
    occurred_at: str,
) -> None:
    from task_governance_tool.storage import (
        begin_initialized_write,
        connect_initialized,
        validate_utc_timestamp,
    )

    if code not in {"deferred", "failed"}:
        raise StorageError("internal_error", "Viewer outcome is invalid")
    timestamp = validate_utc_timestamp(
        occurred_at,
        field="Viewer outcome time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            cursor = connection.execute(
                """
                UPDATE viewer_maintenance_state
                   SET last_outcome_code = CASE
                         WHEN last_outcome_at IS NULL
                           OR last_outcome_at < ?
                           OR (
                             last_outcome_at = ?
                             AND source_generation
                               > COALESCE(rendered_generation, -1)
                           )
                         THEN ?
                         ELSE last_outcome_code
                       END,
                       last_outcome_at = CASE
                         WHEN last_outcome_at IS NULL
                           OR last_outcome_at < ?
                           OR (
                             last_outcome_at = ?
                             AND source_generation
                               > COALESCE(rendered_generation, -1)
                           )
                         THEN ?
                         ELSE last_outcome_at
                       END
                 WHERE project_id = ?
                """,
                (
                    timestamp,
                    timestamp,
                    code,
                    timestamp,
                    timestamp,
                    timestamp,
                    target.project.project_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "internal_error",
                    "Viewer maintenance state is missing",
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
