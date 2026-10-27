"""Transactional Viewer change tracking, independent of rendering and file I/O."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import re
import secrets

from .sqlite_connection import StorageError

MAX_GENERATION = 9223372036854775807
MAX_BYTES = 64 * 1024 * 1024
CACHE_COLUMNS = ("cache_generation", "cache_format_version", "projection_version", "cache_sha256", "cache_bytes")
HTML_COLUMNS = ("html_generation", "html_sha256", "html_bytes")


@dataclass(frozen=True)
class ProjectionState:
    project_id: str
    database_epoch: str
    source_generation: int
    invalidated_generation: int
    cache_generation: int | None
    cache_format_version: int | None
    projection_version: int | None
    cache_sha256: str | None
    cache_bytes: int | None
    html_generation: int | None
    html_sha256: str | None
    html_bytes: int | None


def _invalid():
    from .storage import _unreadable_project_state
    return _unreadable_project_state()


def _integer(value, maximum=MAX_GENERATION, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid()
    return value


def _writer(connection):
    if not connection.in_transaction or connection.execute("PRAGMA query_only").fetchone()[0]:
        raise StorageError("internal_error", "Viewer tracking requires an owned writer")


def available(connection):
    from .storage import current_schema_version
    return current_schema_version(connection) >= 34


def read_state(connection, project_id) -> ProjectionState:
    row = connection.execute("""SELECT p.*,m.source_generation
        FROM viewer_projection_state AS p JOIN viewer_maintenance_state AS m
          ON m.project_id=p.project_id WHERE p.project_id=?""", (project_id,)).fetchone()
    if row is None or type(row["project_id"]) is not str or row["project_id"] != project_id:
        raise _invalid()
    epoch = row["database_epoch"]
    if type(epoch) is not str or re.fullmatch("[0-9a-f]{32}", epoch) is None:
        raise _invalid()
    source = _integer(row["source_generation"])
    _integer(row["invalidated_generation"], source)
    for columns in (CACHE_COLUMNS, HTML_COLUMNS):
        present = [row[name] is not None for name in columns]
        if any(present) != all(present):
            raise _invalid()
        if all(present):
            _integer(row[columns[0]], source)
            _integer(row[columns[-1]], MAX_BYTES, 1)
            digest = row[columns[-2]]
            if type(digest) is not str or re.fullmatch("[0-9a-f]{64}", digest) is None:
                raise _invalid()
    if row["cache_generation"] is not None:
        _integer(row["cache_format_version"], minimum=1)
        _integer(row["projection_version"], minimum=1)
    if row["html_generation"] is not None and (
            row["cache_generation"] is None or row["html_generation"] > row["cache_generation"]):
        raise _invalid()
    return ProjectionState(**dict(row))


def initialize_locked(connection, project_id):
    """Setup only, after the project's ordinary maintenance parent exists."""
    _writer(connection)
    source = connection.execute("SELECT source_generation FROM viewer_maintenance_state WHERE project_id=?",
        (project_id,)).fetchone()
    if source is None:
        raise _invalid()
    source = _integer(source[0])
    connection.execute("""INSERT INTO viewer_projection_state(project_id,database_epoch,invalidated_generation)
        VALUES (?,?,?)""", (project_id, secrets.token_hex(16), source))
    connection.execute("""INSERT INTO viewer_task_revisions(project_id,task_id,source_generation)
        SELECT project_id,task_id,? FROM tasks WHERE project_id=?""", (source, project_id))


def mark_tasks_locked(connection, project_id, task_ids):
    """Advance once for a complete distinct affected set, never commit the caller."""
    _writer(connection)
    if not available(connection):
        return
    identifiers = tuple(sorted(set(task_ids)))
    if not identifiers:
        return
    state = read_state(connection, project_id)
    if state.source_generation == MAX_GENERATION:
        raise _invalid()
    # Indexed parent checks precede the single source change. Never discover
    # affected owners by scanning unrelated business rows.
    for task_id in identifiers:
        if type(task_id) is not str or connection.execute(
                "SELECT 1 FROM tasks WHERE task_id=? AND project_id=?", (task_id, project_id)).fetchone() is None:
            raise _invalid()
    connection.execute("UPDATE viewer_maintenance_state SET source_generation=source_generation+1 WHERE project_id=?",
        (project_id,))
    for task_id in identifiers:
        connection.execute("""INSERT INTO viewer_task_revisions(project_id,task_id,source_generation)
            VALUES (?,?,?) ON CONFLICT(project_id,task_id) DO UPDATE
            SET source_generation=excluded.source_generation""", (project_id, task_id, state.source_generation + 1))


def invalidate_locked(connection, project_id, *, rotate_epoch=False):
    """Explicit Setup/rebind/restore's global invalidation, with no Task sweep."""
    _writer(connection)
    state = read_state(connection, project_id)
    if state.source_generation == MAX_GENERATION:
        raise _invalid()
    connection.execute("UPDATE viewer_maintenance_state SET source_generation=source_generation+1 WHERE project_id=?",
        (project_id,))
    connection.execute("""UPDATE viewer_projection_state SET database_epoch=?, invalidated_generation=?,
        cache_generation=NULL,cache_format_version=NULL,projection_version=NULL,cache_sha256=NULL,cache_bytes=NULL,
        html_generation=NULL,html_sha256=NULL,html_bytes=NULL WHERE project_id=?""",
        (secrets.token_hex(16) if rotate_epoch else state.database_epoch, state.source_generation + 1, project_id))


def changed_tasks(connection, project_id, *, after, through):
    """Read revisions in this capture's snapshot; nothing is consumed or deleted."""
    if not connection.in_transaction:
        raise StorageError("internal_error", "Viewer changes require a snapshot")
    state = read_state(connection, project_id)
    _integer(through, state.source_generation)
    _integer(after, through)
    rows = connection.execute("""SELECT r.task_id,r.source_generation,t.project_id AS parent_project
        FROM viewer_task_revisions AS r LEFT JOIN tasks AS t ON t.task_id=r.task_id
        WHERE r.project_id=? AND r.source_generation>? AND r.source_generation<=?
        ORDER BY r.source_generation,r.task_id""", (project_id, after, through))
    result = []
    for row in rows:
        if type(row["task_id"]) is not str or row["parent_project"] != project_id:
            raise _invalid()
        result.append((row["task_id"], _integer(row["source_generation"], through, after + 1)))
    return tuple(result)


def reset_restored_database(target):
    """Setup-only private candidate normalization after source comparison."""
    from .storage import connect_initialized, begin_initialized_write
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            invalidate_locked(connection, target.project.project_id, rotate_epoch=True)
            state = read_state(connection, target.project.project_id)
            connection.commit()
            return state
        except Exception:
            connection.rollback()
            raise


def validate_all_rows(connection):
    """Global admission/migration only; ordinary delta readers use selected rows."""
    projects = tuple(row[0] for row in connection.execute("SELECT project_id FROM project_meta ORDER BY project_id"))
    if tuple(row[0] for row in connection.execute("SELECT project_id FROM viewer_projection_state ORDER BY project_id")) != projects:
        raise _invalid()
    for project_id in projects:
        read_state(connection, project_id)
    if connection.execute("""SELECT 1 FROM tasks AS t LEFT JOIN viewer_task_revisions AS r
        ON r.project_id=t.project_id AND r.task_id=t.task_id WHERE r.task_id IS NULL LIMIT 1""").fetchone():
        raise _invalid()
    for row in connection.execute("""SELECT r.*,t.project_id AS parent_project,m.source_generation AS maximum
        FROM viewer_task_revisions AS r LEFT JOIN tasks AS t ON t.task_id=r.task_id
        LEFT JOIN viewer_maintenance_state AS m ON m.project_id=r.project_id"""):
        if type(row["task_id"]) is not str or row["parent_project"] != row["project_id"] or row["maximum"] is None:
            raise _invalid()
        _integer(row["source_generation"], _integer(row["maximum"]))
