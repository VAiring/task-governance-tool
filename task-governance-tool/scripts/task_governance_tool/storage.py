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
SCHEMA_VERSION = 5


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
    warnings: list[dict[str, str]]


@dataclass(frozen=True)
class StatusResult:
    target: DatabaseTarget
    exists: bool
    needs_init: bool
    needs_migration: bool
    schema_version: int | None
    counts: dict[str, int]
    error_code: str | None = None
    error_message: str | None = None


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


def default_viewer_output_path(
    skill_root: str | os.PathLike[str],
    project_id: str,
) -> Path:
    return (
        Path(skill_root).resolve()
        / "state"
        / "projects"
        / project_id
        / "viewer"
        / "task-viewer.html"
    )


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


def connect_existing(db_path: Path) -> sqlite3.Connection:
    """Open an existing database read/write without allowing SQLite to create it."""
    uri = db_path.resolve(strict=False).as_uri() + "?mode=rw"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve(strict=False).as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def connect_snapshot_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a point-in-time read transaction without immutable SQLite mode."""
    validate_snapshot_journal_state(db_path)
    uri = db_path.resolve(strict=False).as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
    except Exception:
        connection.close()
        raise
    return connection


def sqlite_sidecar_paths(db_path: Path) -> list[Path]:
    return [Path(str(db_path) + suffix) for suffix in ("-wal", "-shm")]


def existing_sqlite_sidecars(db_path: Path) -> list[Path]:
    return [path for path in sqlite_sidecar_paths(db_path) if path.exists()]


def sqlite_header_uses_wal(db_path: Path) -> bool:
    """Inspect SQLite's stable file-header journal bytes without opening SQLite."""
    try:
        with db_path.open("rb") as stream:
            header = stream.read(20)
    except OSError as exc:
        raise StorageError("internal_error", "could not inspect database journal mode") from exc
    return (
        len(header) >= 20
        and header[:16] == b"SQLite format 3\x00"
        and (header[18] == 2 or header[19] == 2)
    )


def validate_snapshot_journal_state(db_path: Path) -> None:
    """Reject SQLite states that can make a read create WAL/SHM sidecars."""
    if existing_sqlite_sidecars(db_path):
        raise StorageError(
            "internal_error",
            "database has active WAL sidecar files; close writers or checkpoint before export",
        )
    if sqlite_header_uses_wal(db_path):
        raise StorageError(
            "internal_error",
            "database uses WAL journal mode; switch to a rollback journal before export",
        )


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return column_name in {str(row["name"]) for row in rows}


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


def completion_commit_schema_sql() -> str:
    return """
CREATE INDEX IF NOT EXISTS idx_tasks_project_completion_commit
  ON tasks(project_id, completion_commit_hash)
  WHERE completion_commit_hash != '';
"""


def paused_tasks_schema_sql() -> str:
    return """
CREATE TABLE tasks_v3 (
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
    'paused',
    'blocked',
    'review_pending',
    'done',
    'cancelled'
  )),
  blocked_reason TEXT NOT NULL DEFAULT '',
  pause_reason TEXT NOT NULL DEFAULT '',
  review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
  verification TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  completion_commit_required INTEGER NOT NULL DEFAULT 1
    CHECK (completion_commit_required IN (0, 1)),
  completion_commit_hash TEXT NOT NULL DEFAULT '',
  CHECK (status != 'blocked' OR blocked_reason != ''),
  CHECK (
    (status = 'paused' AND pause_reason != '') OR
    (status != 'paused' AND pause_reason = '')
  ),
  CHECK (kind != 'sequential' OR lane != ''),
  CHECK (kind != 'sequential' OR lane_order IS NOT NULL)
);
"""


def create_task_indexes(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE INDEX idx_tasks_project_status ON tasks(project_id, status)")
    connection.execute("CREATE INDEX idx_tasks_project_kind ON tasks(project_id, kind)")
    connection.execute(
        "CREATE INDEX idx_tasks_project_lane_order ON tasks(project_id, lane, lane_order)"
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX idx_tasks_project_lane_order_unique
          ON tasks(project_id, lane, lane_order)
          WHERE kind = 'sequential'
        """
    )
    connection.execute(
        """
        CREATE INDEX idx_tasks_project_completion_commit
          ON tasks(project_id, completion_commit_hash)
          WHERE completion_commit_hash != ''
        """
    )


def empty_counts() -> dict[str, int]:
    return {
        "active": 0,
        "paused": 0,
        "blocked": 0,
        "review_pending": 0,
        "done": 0,
        "next_actionable": 0,
    }


def required_schema_objects_missing(connection: sqlite3.Connection) -> list[str]:
    required_tables = {
        "schema_migrations",
        "project_meta",
        "tasks",
        "task_events",
        "tool_events",
        "review_receipts",
        "review_findings",
    }
    required_indexes = {
        "idx_tasks_project_status",
        "idx_tasks_project_kind",
        "idx_tasks_project_lane_order",
        "idx_tasks_project_lane_order_unique",
        "idx_task_events_task_created",
        "idx_tasks_project_completion_commit",
        "idx_review_receipts_task_target_verdict",
        "idx_review_receipts_task_reviewer_generation",
        "idx_review_findings_status_severity_receipt",
    }
    table_rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    index_rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    tables = {str(row["name"]) for row in table_rows}
    indexes = {str(row["name"]) for row in index_rows}
    missing = [f"table:{name}" for name in sorted(required_tables - tables)]
    missing.extend(f"index:{name}" for name in sorted(required_indexes - indexes))
    if "tasks" in tables:
        task_column_rows = connection.execute("PRAGMA table_info(tasks)").fetchall()
        task_columns = {str(row["name"]) for row in task_column_rows}
        required_task_columns = {
            "task_id",
            "project_id",
            "title",
            "description",
            "kind",
            "lane",
            "lane_order",
            "priority",
            "status",
            "blocked_reason",
            "pause_reason",
            "review_tier",
            "verification",
            "tags",
            "created_at",
            "updated_at",
            "completed_at",
            "completion_commit_required",
            "completion_commit_hash",
            "completion_evidence_kind",
            "completion_evidence_revision",
            "completion_evidence_reason",
            "external_revision_approved",
            "review_target_kind",
            "review_target_value",
            "review_target_generation",
        }
        missing.extend(
            f"column:tasks.{name}" for name in sorted(required_task_columns - task_columns)
        )
    if "task_events" in tables:
        event_column_rows = connection.execute("PRAGMA table_info(task_events)").fetchall()
        event_columns = {str(row["name"]) for row in event_column_rows}
        required_event_columns = {
            "task_event_id",
            "task_id",
            "project_id",
            "event_type",
            "summary",
            "created_at",
        }
        missing.extend(
            f"column:task_events.{name}"
            for name in sorted(required_event_columns - event_columns)
        )
    required_review_columns = {
        "review_receipts": {
            "review_receipt_id",
            "task_id",
            "project_id",
            "reviewer_key",
            "receipt_kind",
            "verdict",
            "target_kind",
            "target_value",
            "target_generation",
            "summary",
            "user_approved",
            "created_at",
        },
        "review_findings": {
            "review_finding_id",
            "review_receipt_id",
            "severity",
            "status",
            "summary",
            "resolution_summary",
            "created_at",
            "resolved_at",
        },
    }
    for table_name, required_columns in required_review_columns.items():
        if table_name not in tables:
            continue
        column_rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        columns = {str(row["name"]) for row in column_rows}
        missing.extend(
            f"column:{table_name}.{name}"
            for name in sorted(required_columns - columns)
        )
    return missing


def apply_initial_schema_migration(connection: sqlite3.Connection) -> None:
    applied_at = utc_now()
    connection.executescript(initial_schema_sql())
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (1, "initial_schema", applied_at),
    )


def apply_completion_commit_migration(connection: sqlite3.Connection) -> None:
    applied_at = utc_now()
    if not column_exists(connection, "tasks", "completion_commit_required"):
        connection.execute(
            """
            ALTER TABLE tasks
              ADD COLUMN completion_commit_required INTEGER NOT NULL DEFAULT 1
              CHECK (completion_commit_required IN (0, 1))
            """
        )
    if not column_exists(connection, "tasks", "completion_commit_hash"):
        connection.execute(
            """
            ALTER TABLE tasks
              ADD COLUMN completion_commit_hash TEXT NOT NULL DEFAULT ''
            """
        )
    connection.executescript(completion_commit_schema_sql())
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (2, "completion_commit_columns", applied_at),
    )


def apply_paused_state_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Rebuild the task parent table safely while preserving task-event links."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "paused-state migration requires no active transaction",
        )
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
            raise StorageError("internal_error", "could not disable foreign key enforcement")
        connection.execute("BEGIN IMMEDIATE")
        task_ids_before = [
            str(row["task_id"])
            for row in connection.execute("SELECT task_id FROM tasks ORDER BY task_id").fetchall()
        ]
        event_links_before = [
            (str(row["task_event_id"]), str(row["task_id"]))
            for row in connection.execute(
                "SELECT task_event_id, task_id FROM task_events ORDER BY task_event_id"
            ).fetchall()
        ]
        connection.execute("DROP TABLE IF EXISTS tasks_v3")
        connection.execute(paused_tasks_schema_sql())
        connection.execute(
            """
            INSERT INTO tasks_v3(
              task_id, project_id, title, description, kind, lane, lane_order,
              priority, status, blocked_reason, pause_reason, review_tier,
              verification, tags, created_at, updated_at, completed_at,
              completion_commit_required, completion_commit_hash
            )
            SELECT
              task_id, project_id, title, description, kind, lane, lane_order,
              priority, status, blocked_reason, '', review_tier,
              verification, tags, created_at, updated_at, completed_at,
              completion_commit_required, completion_commit_hash
              FROM tasks
            """
        )
        if fail_stage == "after_copy":
            raise StorageError("internal_error", "injected paused-state migration failure")
        connection.execute("DROP TABLE tasks")
        connection.execute("ALTER TABLE tasks_v3 RENAME TO tasks")
        create_task_indexes(connection)
        task_ids_after = [
            str(row["task_id"])
            for row in connection.execute("SELECT task_id FROM tasks ORDER BY task_id").fetchall()
        ]
        event_links_after = [
            (str(row["task_event_id"]), str(row["task_id"]))
            for row in connection.execute(
                "SELECT task_event_id, task_id FROM task_events ORDER BY task_event_id"
            ).fetchall()
        ]
        if task_ids_after != task_ids_before or event_links_after != event_links_before:
            raise StorageError("internal_error", "paused-state migration changed task or event identities")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise StorageError("internal_error", "paused-state migration produced foreign key violations")
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (3, "paused_state", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected paused-state migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise StorageError("internal_error", "could not restore foreign key enforcement")


def apply_completion_evidence_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> int:
    """Add typed completion evidence while preserving every legacy projection."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "completion-evidence migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        if not column_exists(connection, "tasks", "completion_evidence_kind"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN completion_evidence_kind TEXT NOT NULL DEFAULT 'none'
                  CHECK (completion_evidence_kind IN (
                    'none', 'git_commit', 'external_revision',
                    'commit_not_required', 'legacy_unverified'
                  ))
                """
            )
        if not column_exists(connection, "tasks", "completion_evidence_revision"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN completion_evidence_revision TEXT NOT NULL DEFAULT ''
                """
            )
        if not column_exists(connection, "tasks", "completion_evidence_reason"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN completion_evidence_reason TEXT NOT NULL DEFAULT ''
                """
            )
        if not column_exists(connection, "tasks", "external_revision_approved"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN external_revision_approved INTEGER NOT NULL DEFAULT 0
                  CHECK (external_revision_approved IN (0, 1))
                """
            )

        inconsistent_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM tasks
                 WHERE completion_commit_required = 0
                   AND completion_commit_hash != ''
                """
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE tasks
               SET completion_evidence_kind = CASE
                     WHEN completion_commit_required = 1 AND completion_commit_hash = ''
                       THEN 'none'
                     WHEN completion_commit_required = 0 AND completion_commit_hash = ''
                       THEN 'commit_not_required'
                     ELSE 'legacy_unverified'
                   END,
                   completion_evidence_revision = CASE
                     WHEN completion_commit_hash != '' THEN completion_commit_hash
                     ELSE ''
                   END,
                   completion_evidence_reason = '',
                   external_revision_approved = 0
            """
        )
        if fail_stage == "after_mapping":
            raise StorageError("internal_error", "injected completion-evidence migration failure")
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (4, "typed_completion_evidence", utc_now()),
        )
        connection.commit()
        return inconsistent_count
    except Exception:
        connection.rollback()
        raise


def apply_review_evidence_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add append-only structured review evidence without synthesizing history."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "review-evidence migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        if not column_exists(connection, "tasks", "review_target_kind"):
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN review_target_kind TEXT NOT NULL DEFAULT ''"
            )
        if not column_exists(connection, "tasks", "review_target_value"):
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN review_target_value TEXT NOT NULL DEFAULT ''"
            )
        if not column_exists(connection, "tasks", "review_target_generation"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN review_target_generation INTEGER NOT NULL DEFAULT 0
                  CHECK (review_target_generation >= 0)
                """
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_receipts (
              review_receipt_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              reviewer_key TEXT NOT NULL,
              receipt_kind TEXT NOT NULL CHECK (receipt_kind IN (
                'independent', 'self_review_fallback', 'not_required'
              )),
              verdict TEXT NOT NULL CHECK (verdict IN (
                'pass', 'changes_requested', 'not_required'
              )),
              target_kind TEXT NOT NULL CHECK (target_kind IN (
                'git_commit', 'diff_fingerprint', 'external_revision'
              )),
              target_value TEXT NOT NULL,
              target_generation INTEGER NOT NULL CHECK (target_generation > 0),
              summary TEXT NOT NULL DEFAULT '',
              user_approved INTEGER NOT NULL DEFAULT 0 CHECK (user_approved IN (0, 1)),
              created_at TEXT NOT NULL,
              UNIQUE (task_id, target_generation, reviewer_key),
              CHECK (reviewer_key != ''),
              CHECK (target_value != ''),
              CHECK (
                (receipt_kind = 'independent'
                  AND verdict IN ('pass', 'changes_requested')
                  AND user_approved = 0) OR
                (receipt_kind = 'self_review_fallback'
                  AND verdict IN ('pass', 'changes_requested')) OR
                (receipt_kind = 'not_required'
                  AND verdict = 'not_required'
                  AND user_approved = 0
                  AND summary != '')
              ),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_findings (
              review_finding_id TEXT PRIMARY KEY,
              review_receipt_id TEXT NOT NULL,
              severity TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
              status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
              summary TEXT NOT NULL,
              resolution_summary TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              resolved_at TEXT,
              FOREIGN KEY (review_receipt_id) REFERENCES review_receipts(review_receipt_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_receipts_task_target_verdict
              ON review_receipts(
                task_id, target_generation, target_kind, target_value, verdict
              )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_receipts_task_reviewer_generation
              ON review_receipts(task_id, target_generation, reviewer_key)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_findings_status_severity_receipt
              ON review_findings(status, severity, review_receipt_id)
            """
        )
        if fail_stage == "after_schema":
            raise StorageError("internal_error", "injected review-evidence migration failure")
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (5, "structured_review_evidence", utc_now()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_migrations(connection: sqlite3.Connection) -> tuple[list[int], list[dict[str, str]]]:
    version = current_schema_version(connection)
    if version > SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            f"database schema version {version} is newer than supported version {SCHEMA_VERSION}",
        )

    applied: list[int] = []
    warnings: list[dict[str, str]] = []
    if version < 1:
        apply_initial_schema_migration(connection)
        applied.append(1)
        version = 1
    if version < 2:
        apply_completion_commit_migration(connection)
        applied.append(2)
        version = 2
    if version < 3:
        if connection.in_transaction:
            connection.commit()
        apply_paused_state_migration(connection)
        applied.append(3)
        version = 3
    if version < 4:
        inconsistent_count = apply_completion_evidence_migration(connection)
        applied.append(4)
        if inconsistent_count:
            warnings.append(
                {
                    "code": "legacy_completion_evidence_preserved",
                    "message": (
                        f"preserved {inconsistent_count} inconsistent legacy completion "
                        "record(s) as legacy_unverified"
                    ),
                }
            )
        version = 4
    if version < 5:
        apply_review_evidence_migration(connection)
        applied.append(5)
    return applied, warnings


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


def read_project_meta_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute("SELECT project_id FROM project_meta ORDER BY project_id LIMIT 1").fetchone()
    if row is None:
        return None
    return str(row["project_id"])


def validate_current_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> int:
    """Require the supported schema and matching project without migrating."""
    version = current_schema_version(connection)
    if version != SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            (
                f"database schema version {version} does not match supported "
                f"version {SCHEMA_VERSION}; run db init to migrate"
            ),
        )

    if required_schema_objects_missing(connection):
        raise StorageError(
            "migration_required",
            "database schema is incomplete; run db init to migrate",
        )

    existing_project_id = read_project_meta_id(connection)
    if existing_project_id is None:
        raise StorageError(
            "migration_required",
            "database project metadata is missing; run db init to repair",
        )
    if existing_project_id != target.project.project_id:
        raise StorageError(
            "project_mismatch",
            (
                f"database belongs to project {existing_project_id}, "
                f"not {target.project.project_id}"
            ),
        )
    return version


def connect_initialized(target: DatabaseTarget) -> sqlite3.Connection:
    """Open a current, project-matching database without creation or migration."""
    if not target.db_path.exists():
        raise StorageError(
            "db_not_initialized",
            "database is not initialized; run db init first",
        )
    try:
        connection = connect_existing(target.db_path)
    except sqlite3.Error as exc:
        if not target.db_path.exists():
            raise StorageError(
                "db_not_initialized",
                "database is not initialized; run db init first",
            ) from exc
        raise StorageError("internal_error", "could not open database") from exc
    try:
        connection.execute("BEGIN IMMEDIATE")
        validate_current_database(connection, target)
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection


def validate_snapshot_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> int:
    """Revalidate schema and project identity inside a viewer read transaction."""
    query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
    if not connection.in_transaction or query_only != 1:
        raise StorageError(
            "internal_error",
            "viewer snapshot requires an active query-only transaction",
        )

    return validate_current_database(connection, target)


def count_tasks(connection: sqlite3.Connection, project_id: str) -> dict[str, int]:
    from task_governance_tool.selection import count_next_tasks

    counts = empty_counts()
    counts["active"] = int(
        connection.execute(
            """
            SELECT COUNT(*) AS count
              FROM tasks
             WHERE project_id = ?
               AND status IN ('ready', 'in_progress', 'paused', 'blocked', 'review_pending')
            """,
            (project_id,),
        ).fetchone()["count"]
    )
    for status, key in (
        ("paused", "paused"),
        ("blocked", "blocked"),
        ("review_pending", "review_pending"),
        ("done", "done"),
    ):
        counts[key] = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE project_id = ? AND status = ?",
                (project_id, status),
            ).fetchone()["count"]
        )
    counts["next_actionable"] = count_next_tasks(connection, project_id)
    return counts


def status_error(
    target: DatabaseTarget,
    *,
    exists: bool,
    needs_init: bool,
    needs_migration: bool,
    schema_version: int | None,
    code: str,
    message: str,
) -> StatusResult:
    return StatusResult(
        target=target,
        exists=exists,
        needs_init=needs_init,
        needs_migration=needs_migration,
        schema_version=schema_version,
        counts=empty_counts(),
        error_code=code,
        error_message=message,
    )


def inspect_database(target: DatabaseTarget) -> StatusResult:
    if not target.db_path.exists():
        return status_error(
            target,
            exists=False,
            needs_init=True,
            needs_migration=False,
            schema_version=None,
            code="db_not_initialized",
            message="database is not initialized; run db init first",
        )

    if existing_sqlite_sidecars(target.db_path):
        return status_error(
            target,
            exists=True,
            needs_init=False,
            needs_migration=False,
            schema_version=None,
            code="internal_error",
            message="database has active WAL sidecar files; close writers or checkpoint before status",
        )

    try:
        with closing(connect_readonly(target.db_path)) as connection:
            version = current_schema_version(connection)
            if version != SCHEMA_VERSION:
                return status_error(
                    target,
                    exists=True,
                    needs_init=False,
                    needs_migration=True,
                    schema_version=version,
                    code="migration_required",
                    message=(
                        f"database schema version {version} does not match supported "
                        f"version {SCHEMA_VERSION}; run db init to migrate"
                    ),
                )

            missing_schema_objects = required_schema_objects_missing(connection)
            if missing_schema_objects:
                return status_error(
                    target,
                    exists=True,
                    needs_init=False,
                    needs_migration=True,
                    schema_version=version,
                    code="migration_required",
                    message="database schema is incomplete; run db init to migrate",
                )

            existing_project_id = read_project_meta_id(connection)
            if existing_project_id is None:
                return status_error(
                    target,
                    exists=True,
                    needs_init=False,
                    needs_migration=True,
                    schema_version=version,
                    code="migration_required",
                    message="database project metadata is missing; run db init to repair",
                )
            if existing_project_id != target.project.project_id:
                return status_error(
                    target,
                    exists=True,
                    needs_init=False,
                    needs_migration=False,
                    schema_version=version,
                    code="project_mismatch",
                    message=(
                        f"database belongs to project {existing_project_id}, "
                        f"not {target.project.project_id}"
                    ),
                )

            return StatusResult(
                target=target,
                exists=True,
                needs_init=False,
                needs_migration=False,
                schema_version=version,
                counts=count_tasks(connection, target.project.project_id),
            )
    except sqlite3.Error as exc:
        return status_error(
            target,
            exists=True,
            needs_init=False,
            needs_migration=False,
            schema_version=None,
            code="internal_error",
            message="could not inspect database",
        )


def initialize_database(target: DatabaseTarget) -> InitResult:
    created = not target.db_path.exists()
    try:
        target.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(target.db_path)) as connection:
            with connection:
                if table_exists(connection, "project_meta"):
                    existing_project_id = read_project_meta_id(connection)
                    if (
                        existing_project_id is not None
                        and existing_project_id != target.project.project_id
                    ):
                        raise StorageError(
                            "project_mismatch",
                            f"database belongs to project {existing_project_id}, not {target.project.project_id}",
                        )
                migrations_applied, warnings = apply_migrations(connection)
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
        warnings=warnings,
    )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)
