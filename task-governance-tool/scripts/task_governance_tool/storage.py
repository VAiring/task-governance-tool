"""Storage path helpers for task-governance-tool."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ID_HASH_LENGTH = 12
SCHEMA_VERSION = 1


class StorageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: str
    canonical_repo: Path
    canonical_path_hash: str
    display_name: str


@dataclass(frozen=True)
class DatabaseTarget:
    project: ProjectIdentity
    db_path: Path
    explicit_db: bool


@dataclass(frozen=True)
class InitResult:
    target: DatabaseTarget
    created: bool
    migrations_applied: list[int]
    schema_version: int


def skill_root_from_script(script_path: str | os.PathLike[str]) -> Path:
    script = Path(script_path).resolve()
    return script.parent.parent


def canonicalize_repo(repo: str | os.PathLike[str]) -> Path:
    return Path(repo).expanduser().resolve(strict=False)


def normalized_path_for_hash(path: Path) -> str:
    normalized = str(path)
    if os.name == "nt":
        normalized = normalized.replace("/", "\\")
        normalized = os.path.normcase(normalized)
    return normalized


def sanitize_project_basename(name: str) -> str:
    sanitized = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    return sanitized or "project"


def project_identity(repo: str | os.PathLike[str]) -> ProjectIdentity:
    canonical = canonicalize_repo(repo)
    hash_input = normalized_path_for_hash(canonical)
    digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
    display_name = canonical.name or "project"
    prefix = sanitize_project_basename(display_name)
    project_id = f"{prefix}-{digest[:PROJECT_ID_HASH_LENGTH]}"
    return ProjectIdentity(
        project_id=project_id,
        canonical_repo=canonical,
        canonical_path_hash=digest,
        display_name=display_name,
    )


def default_db_path(skill_root: str | os.PathLike[str], project_id: str) -> Path:
    return Path(skill_root).resolve() / "state" / "projects" / project_id / "taskgov.sqlite"


def resolve_database_target(
    *,
    repo: str | os.PathLike[str],
    db: str | os.PathLike[str] | None,
    script_path: str | os.PathLike[str],
) -> DatabaseTarget:
    project = project_identity(repo)
    if db:
        db_path = Path(db).expanduser().resolve(strict=False)
        explicit_db = True
    else:
        db_path = default_db_path(skill_root_from_script(script_path), project.project_id)
        explicit_db = False
    return DatabaseTarget(project=project, db_path=db_path, explicit_db=explicit_db)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def current_schema_version(connection: sqlite3.Connection) -> int:
    if not table_exists(connection, "schema_migrations"):
        return 0
    row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    if row is None or row["version"] is None:
        return 0
    return int(row["version"])


def initial_schema_sql() -> str:
    return """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_meta (
  project_id TEXT PRIMARY KEY,
  canonical_path_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('sequential', 'optional')),
  lane TEXT NOT NULL DEFAULT '',
  lane_order INTEGER,
  priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  status TEXT NOT NULL CHECK (status IN (
    'ready',
    'in_progress',
    'blocked',
    'review_pending',
    'done',
    'cancelled'
  )),
  blocked_reason TEXT NOT NULL DEFAULT '',
  review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
  verification TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  CHECK (status != 'blocked' OR blocked_reason != ''),
  CHECK (kind != 'sequential' OR lane != ''),
  CHECK (kind != 'sequential' OR lane_order IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS task_events (
  task_event_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS tool_events (
  tool_event_id TEXT PRIMARY KEY,
  project_id TEXT,
  command TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_project_kind ON tasks(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_tasks_project_lane_order ON tasks(project_id, lane, lane_order);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_project_lane_order_unique
  ON tasks(project_id, lane, lane_order)
  WHERE kind = 'sequential';
CREATE INDEX IF NOT EXISTS idx_task_events_task_created ON task_events(task_id, created_at);
"""


def apply_migrations(connection: sqlite3.Connection) -> list[int]:
    version = current_schema_version(connection)
    if version > SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            f"database schema version {version} is newer than supported version {SCHEMA_VERSION}",
        )
    if version == SCHEMA_VERSION:
        return []

    applied_at = utc_now()
    connection.executescript(initial_schema_sql())
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (SCHEMA_VERSION, "initial_schema", applied_at),
    )
    return [SCHEMA_VERSION]


def ensure_project_meta(connection: sqlite3.Connection, project: ProjectIdentity) -> None:
    now = utc_now()
    rows = connection.execute("SELECT project_id FROM project_meta").fetchall()
    if rows:
        existing_project_id = str(rows[0]["project_id"])
        if existing_project_id != project.project_id:
            raise StorageError(
                "project_mismatch",
                f"database belongs to project {existing_project_id}, not {project.project_id}",
            )
        connection.execute(
            """
            UPDATE project_meta
               SET canonical_path_hash = ?, display_name = ?, updated_at = ?
             WHERE project_id = ?
            """,
            (project.canonical_path_hash, project.display_name, now, project.project_id),
        )
        return

    connection.execute(
        """
        INSERT INTO project_meta(project_id, canonical_path_hash, display_name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project.project_id, project.canonical_path_hash, project.display_name, now, now),
    )


def initialize_database(target: DatabaseTarget) -> InitResult:
    created = not target.db_path.exists()
    try:
        target.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(target.db_path)) as connection:
            with connection:
                migrations_applied = apply_migrations(connection)
                ensure_project_meta(connection, target.project)
                version = current_schema_version(connection)
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError("internal_error", "could not prepare database path") from exc
    except sqlite3.Error as exc:
        raise StorageError("internal_error", "could not open or initialize database") from exc
    return InitResult(
        target=target,
        created=created,
        migrations_applied=migrations_applied,
        schema_version=version,
    )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)
