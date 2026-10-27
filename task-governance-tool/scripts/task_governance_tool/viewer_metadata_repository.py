"""Viewer publication metadata; rendering and artifact policy stay with callers."""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_governance_tool.sqlite_connection import StorageError

if TYPE_CHECKING:
    from task_governance_tool.storage import DatabaseTarget
    from task_governance_tool.viewer_projection_repository import ProjectionState
    from task_governance_tool.project_binding_repository import ProjectBindingState


class ViewerBasisChanged(RuntimeError):
    """A known, pre-effect capture mismatch; never an unknown commit outcome."""


@dataclass(frozen=True)
class ProjectionBasis:
    identity: tuple[int, int]
    binding: ProjectBindingState
    state: ProjectionState
    viewer: ViewerMaintenanceState
    enabled_at: str | None

    def header(self):
        from .viewer_projection_cache import CACHE_FORMAT_VERSION, PROJECTION_VERSION
        return dict(cache_format_version=CACHE_FORMAT_VERSION, projection_version=PROJECTION_VERSION,
                    project_id=self.binding.project_id, binding_generation=self.binding.binding_generation,
                    binding_path_hash=self.binding.canonical_path_hash, database_epoch=self.state.database_epoch,
                    database_file_identity=dict(device=self.identity[0], inode=self.identity[1]),
                    source_schema_version=34, applied_generation=self.state.source_generation)


def _database_identity(target):
    from .viewer_projection_cache import physical_identity
    return physical_identity(target.db_path, root=target.db_path.parent)


def _basis(connection, target, identity):
    from .backup_metadata_repository import read_project_maintenance
    from .project_binding_repository import read_project_binding_state
    from .viewer_projection_repository import read_state
    if _database_identity(target) != identity:
        raise ViewerBasisChanged()
    # These physical relational checks are intentionally database-wide. They
    # are not the removed historical Evidence/Task semantic audit, and each
    # fresh effect boundary must establish them for its own transaction.
    if ([row[0] for row in connection.execute("PRAGMA quick_check")] != ["ok"]
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
        raise StorageError("project_state_unreadable", "Viewer source integrity is invalid")
    maintenance = read_project_maintenance(connection, target.project.project_id)
    viewer = read_viewer_maintenance(connection, target.project.project_id)
    if maintenance is None or viewer is None:
        raise StorageError("project_state_unreadable", "Viewer maintenance is unavailable")
    return ProjectionBasis(identity, read_project_binding_state(connection),
                           read_state(connection, target.project.project_id), viewer, maintenance.enabled_at)


@contextmanager
def projection_snapshot(target):
    """Own the fresh structural snapshot, with physical continuity on entry/exit."""
    from .storage import connect_initialized_readonly
    identity = _database_identity(target)
    with closing(connect_initialized_readonly(target, operation_local=True)) as connection:
        basis = _basis(connection, target, identity)
        yield connection, basis
        if _database_identity(target) != identity:
            raise ViewerBasisChanged()


def read_projection_basis(target):
    with projection_snapshot(target) as (_, basis):
        return basis


def _compatible(actual, expected, *, exact_source):
    return (actual.identity == expected.identity and actual.binding == expected.binding
            and actual.enabled_at == expected.enabled_at and actual.enabled_at is not None
            and actual.state.database_epoch == expected.state.database_epoch
            and actual.state.invalidated_generation == expected.state.invalidated_generation
            and (actual.state.source_generation == expected.state.source_generation if exact_source
                 else actual.state.source_generation >= expected.state.source_generation))


def _cache_tuple(state):
    from .viewer_projection_repository import CACHE_COLUMNS
    return tuple(getattr(state, name) for name in CACHE_COLUMNS)


def _html_tuple(state):
    from .viewer_projection_repository import HTML_COLUMNS
    return tuple(getattr(state, name) for name in HTML_COLUMNS)


@contextmanager
def projection_replace_guard(target, expected, *, accepted_cache=None):
    entered = False
    try:
        with projection_snapshot(target) as (_, actual):
            if not _compatible(actual, expected, exact_source=True):
                raise ViewerBasisChanged()
            if accepted_cache is None:
                if _cache_tuple(actual.state) != _cache_tuple(expected.state):
                    raise ViewerBasisChanged()
            elif _cache_tuple(actual.state) != accepted_cache:
                raise ViewerBasisChanged()
            if actual.state.html_generation is not None and actual.state.html_generation > expected.state.source_generation:
                raise ViewerBasisChanged()
            entered = True
            yield
    except ViewerBasisChanged as exc:
        if entered:
            # Replacement may already have happened. Only an entry rejection
            # is eligible for the coordinator's one bounded fresh capture.
            raise StorageError("project_state_unreadable", "Viewer publication basis changed after admission") from exc
        raise


@contextmanager
def _projection_writer(target, expected):
    from .storage import connect_initialized, begin_initialized_write
    if _database_identity(target) != expected.identity:
        raise ViewerBasisChanged()
    with closing(connect_initialized(target, operation_local=True)) as connection:
        try:
            begin_initialized_write(connection, target, operation_local=True)
            actual = _basis(connection, target, expected.identity)
            if not _compatible(actual, expected, exact_source=False):
                raise ViewerBasisChanged()
            yield connection, actual
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def force_projection_rebuild(target, expected):
    """One Setup repair invalidation, preserving source, files and last success."""
    with _projection_writer(target, expected) as (connection, _):
        connection.execute("""UPDATE viewer_projection_state SET cache_generation=NULL,
            cache_format_version=NULL,projection_version=NULL,cache_sha256=NULL,cache_bytes=NULL,
            html_generation=NULL,html_sha256=NULL,html_bytes=NULL WHERE project_id=?""",
            (target.project.project_id,))
        connection.execute("UPDATE viewer_maintenance_state SET rendered_generation=NULL WHERE project_id=?",
                           (target.project.project_id,))


def accept_projection_cache(target, expected, cache, observed):
    from .viewer_projection_cache import (
        CACHE_FORMAT_VERSION, PROJECTION_VERSION, cache_output, require_same_artifact,
    )
    generation = expected.state.source_generation
    value = (generation, CACHE_FORMAT_VERSION, PROJECTION_VERSION, cache.sha256, len(cache.data))
    with _projection_writer(target, expected) as (connection, actual):
        if (actual.state.cache_generation is not None and actual.state.cache_generation > generation
                or _cache_tuple(actual.state) != _cache_tuple(expected.state)):
            raise ViewerBasisChanged()
        require_same_artifact(cache_output(target), observed)
        connection.execute("""UPDATE viewer_projection_state SET cache_generation=?,cache_format_version=?,
            projection_version=?,cache_sha256=?,cache_bytes=?,html_generation=NULL,html_sha256=NULL,html_bytes=NULL
            WHERE project_id=?""", (*value, target.project.project_id))
        connection.execute("UPDATE viewer_maintenance_state SET rendered_generation=NULL WHERE project_id=?",
                           (target.project.project_id,))
    return value


def accept_projection_html(target, expected, accepted_cache, output, observed, *, published_at):
    from .storage import validate_utc_timestamp
    from .viewer_projection_cache import require_same_artifact
    timestamp = validate_utc_timestamp(published_at, field="Viewer publication time")
    generation = expected.state.source_generation
    with _projection_writer(target, expected) as (connection, actual):
        if (_cache_tuple(actual.state) != accepted_cache
                or actual.state.html_generation is not None and actual.state.html_generation > generation):
            raise ViewerBasisChanged()
        require_same_artifact(output, observed)
        connection.execute("""UPDATE viewer_projection_state SET html_generation=?,html_sha256=?,html_bytes=?
            WHERE project_id=?""", (generation, observed.sha256, observed.identity.size, target.project.project_id))
        connection.execute("""UPDATE viewer_maintenance_state SET rendered_generation=?,
            last_success_at=MAX(COALESCE(last_success_at,?),?),
            last_outcome_code=CASE WHEN last_outcome_at IS NULL OR last_outcome_at<=? THEN 'succeeded'
                ELSE last_outcome_code END,
            last_outcome_at=MAX(COALESCE(last_outcome_at,?),?) WHERE project_id=?""",
            (generation, timestamp, timestamp, timestamp, timestamp, timestamp, target.project.project_id))


def record_projection_failure(target, expected, *, code, occurred_at):
    """Only the observed generation/publication may receive this stale-able outcome."""
    from .storage import validate_utc_timestamp
    if code not in {"failed", "deferred"}:
        raise StorageError("internal_error", "Viewer outcome is invalid")
    timestamp = validate_utc_timestamp(occurred_at, field="Viewer outcome time")
    with _projection_writer(target, expected) as (connection, actual):
        if (actual.state != expected.state or actual.viewer != expected.viewer):
            return
        connection.execute("""UPDATE viewer_maintenance_state SET last_outcome_code=?,last_outcome_at=?
            WHERE project_id=? AND (last_outcome_at IS NULL OR last_outcome_at<? OR
            (last_outcome_at=? AND source_generation>COALESCE(rendered_generation,-1)))""",
            (code, timestamp, target.project.project_id, timestamp, timestamp))


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
