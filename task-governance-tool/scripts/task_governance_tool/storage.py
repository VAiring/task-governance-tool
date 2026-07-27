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

from task_governance_tool.ordering import LANE_SQL_FUNCTION, canonical_lane


PROJECT_ID_HASH_LENGTH = 12
SCHEMA_VERSION = 12
MIN_BACKUP_INTERVAL_MINUTES = 1
MAX_BACKUP_INTERVAL_MINUTES = 1_440
MIN_BACKUP_GENERATIONS = 1
MAX_BACKUP_GENERATIONS = 20
DEFAULT_BACKUP_INTERVAL_MINUTES = 30
DEFAULT_BACKUP_GENERATIONS = 3
MANAGED_BACKUP_GENERATION_PATTERN = re.compile(r"^tg_backup_[0-9a-f]{32}$")
UNSUPPORTED_JOURNAL_MODE_MESSAGE = (
    "task database uses unsupported WAL journal mode"
)
DATABASE_BUSY_MESSAGE = "task database is busy; run the command again later"


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
class MigrationBackupMetadata:
    generation_id: str
    published_at: str
    publication_retention: int


@dataclass(frozen=True)
class ManagedBackupRepositoryState:
    maintenance: ProjectMaintenanceState
    generations: tuple[MigrationBackupMetadata, ...]


@dataclass(frozen=True)
class SetupStorageState:
    schema_version: int | None
    needs_initialize: bool
    needs_migration: bool
    maintenance_enabled: bool
    backup_interval_minutes: int | None
    backup_generations: int | None


@dataclass(frozen=True)
class ProjectMaintenanceState:
    project_id: str
    enabled_at: str | None
    backup_interval_minutes: int | None
    backup_generations: int | None
    applied_backup_generations: int | None
    backup_last_success_at: str | None
    backup_last_outcome_code: str | None
    backup_last_outcome_at: str | None
    latest_backup_generation_id: str | None
    viewer_last_success_at: str | None
    viewer_last_outcome_code: str | None
    viewer_last_outcome_at: str | None

    @property
    def enabled(self) -> bool:
        return self.enabled_at is not None


@dataclass(frozen=True)
class DoctorStorageState:
    schema_version: int
    project_code: str
    task_counts: dict[str, int]
    maintenance: ProjectMaintenanceState


def skill_root_from_script(script_path: str | os.PathLike[str]) -> Path:
    script = Path(script_path).resolve()
    return script.parent.parent


def lexical_skill_root_from_script(script_path: str | os.PathLike[str]) -> Path:
    script = Path(script_path).expanduser()
    if not script.is_absolute():
        script = Path.cwd() / script
    return script.absolute().parent.parent


def _linked_install_path_candidates(script_path: Path) -> tuple[Path, ...]:
    skill_root = script_path.parent.parent
    candidates = [script_path, script_path.parent, skill_root]
    if (
        skill_root.name.casefold() == "task-governance-tool"
        and skill_root.parent.name.casefold() == "skills"
        and skill_root.parent.parent.name.casefold() == ".agents"
    ):
        candidates.extend((skill_root.parent, skill_root.parent.parent))
    return tuple(candidates)


def uses_unsupported_linked_install(script_path: str | os.PathLike[str]) -> bool:
    script = Path(script_path).expanduser()
    if not script.is_absolute():
        script = Path.cwd() / script
    script = script.absolute()
    try:
        for candidate in _linked_install_path_candidates(script):
            if candidate.is_symlink():
                return True
            is_junction = getattr(candidate, "is_junction", None)
            if is_junction is not None and is_junction():
                return True
        return False
    except (OSError, RuntimeError):
        return True


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


def validate_utc_timestamp(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        value,
    ):
        raise StorageError("internal_error", f"{field} is not a canonical UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise StorageError(
            "internal_error",
            f"{field} is not a canonical UTC timestamp",
        ) from exc
    return value


def validate_migration_backup_metadata(
    metadata: MigrationBackupMetadata,
) -> MigrationBackupMetadata:
    if not isinstance(metadata, MigrationBackupMetadata):
        raise StorageError("internal_error", "setup backup metadata is invalid")
    if not MANAGED_BACKUP_GENERATION_PATTERN.fullmatch(metadata.generation_id):
        raise StorageError("internal_error", "setup backup generation identity is invalid")
    validate_utc_timestamp(metadata.published_at, field="setup backup publication time")
    if (
        isinstance(metadata.publication_retention, bool)
        or not isinstance(metadata.publication_retention, int)
        or not MIN_BACKUP_GENERATIONS
        <= metadata.publication_retention
        <= MAX_BACKUP_GENERATIONS
    ):
        raise StorageError("internal_error", "setup backup retention is invalid")
    return metadata


def validate_managed_backup_metadata_set(
    metadata_items: tuple[MigrationBackupMetadata, ...],
) -> tuple[MigrationBackupMetadata, ...]:
    validated = tuple(
        sorted(
            (
                validate_migration_backup_metadata(metadata)
                for metadata in metadata_items
            ),
            key=lambda metadata: (
                metadata.published_at,
                metadata.generation_id,
            ),
        )
    )
    if len({metadata.generation_id for metadata in validated}) != len(validated):
        raise StorageError(
            "internal_error",
            "managed backup metadata contains duplicate identities",
        )
    return validated


def configure_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.row_factory = sqlite3.Row
    connection.create_function(
        LANE_SQL_FUNCTION,
        1,
        canonical_lane,
        deterministic=True,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def is_sqlite_busy_or_locked(exc: sqlite3.Error) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int):
        return (error_code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def operational_sqlite_error(
    exc: sqlite3.Error,
    *,
    fallback_message: str,
) -> StorageError:
    if is_sqlite_busy_or_locked(exc):
        return StorageError("database_busy", DATABASE_BUSY_MESSAGE)
    return StorageError("internal_error", fallback_message)


def connect(db_path: Path) -> sqlite3.Connection:
    return configure_connection(sqlite3.connect(db_path))


def connect_existing(db_path: Path) -> sqlite3.Connection:
    """Open an existing database read/write without allowing SQLite to create it."""
    uri = db_path.resolve(strict=False).as_uri() + "?mode=rw"
    return configure_connection(sqlite3.connect(uri, uri=True))


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open one lock-respecting query-only read transaction."""
    validate_operational_journal_state(db_path)
    if not db_path.exists():
        raise StorageError(
            "db_not_initialized",
            "project state is not set up; run setup first",
        )
    uri = db_path.resolve(strict=False).as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open database",
        ) from exc
    try:
        configure_connection(connection)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
    except sqlite3.Error as exc:
        connection.close()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open database",
        ) from exc
    except Exception:
        connection.close()
        raise
    return connection


def connect_snapshot_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a Viewer-compatible point-in-time read transaction."""
    return connect_readonly(db_path)


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
    if len(header) < 20 or header[:16] != b"SQLite format 3\x00":
        return False
    journal_versions = (header[18], header[19])
    if any(version not in {1, 2} for version in journal_versions):
        return False
    return 2 in journal_versions


def validate_operational_journal_state(db_path: Path) -> None:
    """Reject persistent WAL state before any operational SQLite access."""
    if existing_sqlite_sidecars(db_path):
        raise StorageError(
            "unsupported_journal_mode",
            UNSUPPORTED_JOURNAL_MODE_MESSAGE,
        )
    if not db_path.exists():
        return
    if sqlite_header_uses_wal(db_path):
        raise StorageError(
            "unsupported_journal_mode",
            UNSUPPORTED_JOURNAL_MODE_MESSAGE,
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


def missing_migration_versions(
    connection: sqlite3.Connection,
    version: int,
) -> list[int]:
    if version <= 0 or not table_exists(connection, "schema_migrations"):
        return list(range(1, max(version, 0) + 1))
    rows = connection.execute(
        "SELECT version FROM schema_migrations WHERE version BETWEEN 1 AND ?",
        (version,),
    ).fetchall()
    present = {int(row["version"]) for row in rows}
    return [candidate for candidate in range(1, version + 1) if candidate not in present]


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
        "handoff_pending": 0,
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
        "handoff_records",
        "task_contract_revisions",
        "task_effort_activity",
        "task_effort_bases",
        "project_maintenance",
        "managed_backup_generations",
        "task_checkpoints",
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
        "idx_handoff_project_state_created",
        "idx_handoff_project_source",
        "idx_handoff_due_claim",
        "idx_contract_project_task_revision",
        "idx_effort_bases_project",
        "idx_managed_backup_project_published",
        "idx_checkpoints_project_task_created",
    }
    required_triggers = {
        "trg_project_maintenance_enabled_at_immutable",
    }
    table_rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    index_rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    trigger_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    ).fetchall()
    tables = {str(row["name"]) for row in table_rows}
    indexes = {str(row["name"]) for row in index_rows}
    triggers = {str(row["name"]) for row in trigger_rows}
    missing = [f"table:{name}" for name in sorted(required_tables - tables)]
    missing.extend(f"index:{name}" for name in sorted(required_indexes - indexes))
    missing.extend(f"trigger:{name}" for name in sorted(required_triggers - triggers))
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
            "review_target_base_revision",
            "current_contract_revision",
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
    if "project_meta" in tables:
        project_column_rows = connection.execute("PRAGMA table_info(project_meta)").fetchall()
        project_columns = {str(row["name"]) for row in project_column_rows}
        if "effort_activity_generation" not in project_columns:
            missing.append("column:project_meta.effort_activity_generation")
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
            "target_base_revision",
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
    if "handoff_records" in tables:
        handoff_column_rows = connection.execute(
            "PRAGMA table_info(handoff_records)"
        ).fetchall()
        handoff_columns = {str(row["name"]) for row in handoff_column_rows}
        required_handoff_columns = {
            "handoff_id",
            "project_id",
            "source_task_id",
            "source_contract_revision",
            "idempotency_key",
            "occurrence_id",
            "summary",
            "rationale",
            "state",
            "adapter_key",
            "adapter_version",
            "delivery_attempts",
            "last_delivery_code",
            "next_attempt_at",
            "claim_token",
            "claim_expires_at",
            "receiver_receipt",
            "withdraw_reason",
            "created_at",
            "updated_at",
            "handed_off_at",
            "withdrawn_at",
        }
        missing.extend(
            f"column:handoff_records.{name}"
            for name in sorted(required_handoff_columns - handoff_columns)
        )
    if "task_contract_revisions" in tables:
        contract_column_rows = connection.execute(
            "PRAGMA table_info(task_contract_revisions)"
        ).fetchall()
        contract_columns = {str(row["name"]) for row in contract_column_rows}
        required_contract_columns = {
            "contract_revision_id",
            "task_id",
            "project_id",
            "revision",
            "scope",
            "acceptance",
            "constraints_text",
            "authority_ref",
            "change_reason",
            "created_at",
        }
        missing.extend(
            f"column:task_contract_revisions.{name}"
            for name in sorted(required_contract_columns - contract_columns)
        )
    required_effort_columns = {
        "task_effort_activity": {
            "task_id",
            "project_id",
            "generation",
        },
        "task_effort_bases": {
            "task_id",
            "project_id",
            "basis_head",
            "basis_clean",
            "captured_at",
            "project_generation",
            "subject_generation",
            "other_active_at_capture",
        },
    }
    for table_name, required_columns in required_effort_columns.items():
        if table_name not in tables:
            continue
        column_rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        columns = {str(row["name"]) for row in column_rows}
        missing.extend(
            f"column:{table_name}.{name}"
            for name in sorted(required_columns - columns)
        )
    if "project_maintenance" in tables:
        maintenance_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(project_maintenance)"
            ).fetchall()
        }
        required_maintenance_columns = {
            "project_id",
            "enabled_at",
            "backup_interval_minutes",
            "backup_generations",
            "applied_backup_generations",
            "backup_last_success_at",
            "backup_last_outcome_code",
            "backup_last_outcome_at",
            "latest_backup_generation_id",
            "viewer_last_success_at",
            "viewer_last_outcome_code",
            "viewer_last_outcome_at",
        }
        missing.extend(
            f"column:project_maintenance.{name}"
            for name in sorted(required_maintenance_columns - maintenance_columns)
        )
    if "managed_backup_generations" in tables:
        generation_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(managed_backup_generations)"
            ).fetchall()
        }
        required_generation_columns = {
            "generation_id",
            "project_id",
            "published_at",
            "publication_retention",
        }
        missing.extend(
            f"column:managed_backup_generations.{name}"
            for name in sorted(required_generation_columns - generation_columns)
        )
    if "task_checkpoints" in tables:
        checkpoint_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(task_checkpoints)"
            ).fetchall()
        }
        required_checkpoint_columns = {
            "checkpoint_id",
            "task_id",
            "project_id",
            "contract_revision",
            "summary",
            "next_action",
            "unresolved_risks_json",
            "created_at",
        }
        missing.extend(
            f"column:task_checkpoints.{name}"
            for name in sorted(required_checkpoint_columns - checkpoint_columns)
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


def git_snapshot_review_receipts_schema_sql() -> str:
    return """
CREATE TABLE review_receipts_v6 (
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
    'git_commit', 'diff_fingerprint', 'external_revision', 'git_snapshot'
  )),
  target_value TEXT NOT NULL,
  target_base_revision TEXT NOT NULL DEFAULT '',
  target_generation INTEGER NOT NULL CHECK (target_generation > 0),
  summary TEXT NOT NULL DEFAULT '',
  user_approved INTEGER NOT NULL DEFAULT 0 CHECK (user_approved IN (0, 1)),
  created_at TEXT NOT NULL,
  UNIQUE (task_id, target_generation, reviewer_key),
  CHECK (reviewer_key != ''),
  CHECK (target_value != ''),
  CHECK (
    (target_kind = 'git_snapshot' AND target_base_revision != '') OR
    (target_kind != 'git_snapshot' AND target_base_revision = '')
  ),
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
);
"""


def create_review_receipt_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX idx_review_receipts_task_target_verdict
          ON review_receipts(
            task_id, target_generation, target_kind, target_value,
            target_base_revision, verdict
          )
        """
    )
    connection.execute(
        """
        CREATE INDEX idx_review_receipts_task_reviewer_generation
          ON review_receipts(task_id, target_generation, reviewer_key)
        """
    )


def apply_git_snapshot_schema_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add target-base storage while preserving receipt and finding identities."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "git-snapshot migration requires no active transaction",
        )
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
            raise StorageError("internal_error", "could not disable foreign key enforcement")
        connection.execute("BEGIN IMMEDIATE")
        if not column_exists(connection, "tasks", "review_target_base_revision"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN review_target_base_revision TEXT NOT NULL DEFAULT ''
                """
            )

        receipt_rows_before = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT review_receipt_id, task_id, project_id, reviewer_key,
                       receipt_kind, verdict, target_kind, target_value,
                       target_generation, summary, user_approved, created_at
                  FROM review_receipts
                 ORDER BY review_receipt_id
                """
            ).fetchall()
        ]
        finding_links_before = [
            (str(row["review_finding_id"]), str(row["review_receipt_id"]))
            for row in connection.execute(
                """
                SELECT review_finding_id, review_receipt_id
                  FROM review_findings
                 ORDER BY review_finding_id
                """
            ).fetchall()
        ]
        connection.execute("DROP TABLE IF EXISTS review_receipts_v6")
        connection.execute(git_snapshot_review_receipts_schema_sql())
        connection.execute(
            """
            INSERT INTO review_receipts_v6(
              review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
              verdict, target_kind, target_value, target_base_revision,
              target_generation, summary, user_approved, created_at
            )
            SELECT
              review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
              verdict, target_kind, target_value, '', target_generation,
              summary, user_approved, created_at
              FROM review_receipts
            """
        )
        if fail_stage == "after_copy":
            raise StorageError("internal_error", "injected git-snapshot migration failure")
        connection.execute("DROP TABLE review_receipts")
        connection.execute("ALTER TABLE review_receipts_v6 RENAME TO review_receipts")
        create_review_receipt_indexes(connection)

        receipt_rows_after = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT review_receipt_id, task_id, project_id, reviewer_key,
                       receipt_kind, verdict, target_kind, target_value,
                       target_generation, summary, user_approved, created_at
                  FROM review_receipts
                 ORDER BY review_receipt_id
                """
            ).fetchall()
        ]
        finding_links_after = [
            (str(row["review_finding_id"]), str(row["review_receipt_id"]))
            for row in connection.execute(
                """
                SELECT review_finding_id, review_receipt_id
                  FROM review_findings
                 ORDER BY review_finding_id
                """
            ).fetchall()
        ]
        if (
            receipt_rows_after != receipt_rows_before
            or finding_links_after != finding_links_before
        ):
            raise StorageError(
                "internal_error",
                "git-snapshot migration changed review evidence identities",
            )
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise StorageError(
                "internal_error",
                "git-snapshot migration produced foreign key violations",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (6, "git_snapshot_target_base", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected git-snapshot migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise StorageError("internal_error", "could not restore foreign key enforcement")


def apply_handoff_outbox_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add the local handoff outbox without changing existing task data."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "handoff-outbox migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE handoff_records (
              handoff_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              source_task_id TEXT NOT NULL,
              source_contract_revision INTEGER NOT NULL DEFAULT 0
                CHECK (source_contract_revision >= 0),
              idempotency_key TEXT NOT NULL,
              occurrence_id TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL,
              rationale TEXT NOT NULL DEFAULT '',
              state TEXT NOT NULL CHECK (state IN (
                'pending_handoff',
                'handed_off',
                'handoff_withdrawn_by_user'
              )),
              adapter_key TEXT NOT NULL DEFAULT '',
              adapter_version TEXT NOT NULL DEFAULT '',
              delivery_attempts INTEGER NOT NULL DEFAULT 0
                CHECK (delivery_attempts >= 0),
              last_delivery_code TEXT NOT NULL DEFAULT '',
              next_attempt_at TEXT,
              claim_token TEXT NOT NULL DEFAULT '',
              claim_expires_at TEXT,
              receiver_receipt TEXT NOT NULL DEFAULT '',
              withdraw_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              handed_off_at TEXT,
              withdrawn_at TEXT,
              UNIQUE (project_id, idempotency_key),
              FOREIGN KEY (source_task_id) REFERENCES tasks(task_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_handoff_project_state_created
              ON handoff_records(project_id, state, created_at, handoff_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_handoff_project_source
              ON handoff_records(project_id, source_task_id, created_at, handoff_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_handoff_due_claim
              ON handoff_records(project_id, state, claim_expires_at)
            """
        )
        if fail_stage == "after_schema":
            raise StorageError("internal_error", "injected handoff-outbox migration failure")
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (7, "local_handoff_outbox", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected handoff-outbox migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_task_contract_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add immutable Task Contract revisions without rewriting legacy task rows."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "task-contract migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            ALTER TABLE tasks
              ADD COLUMN current_contract_revision INTEGER NOT NULL DEFAULT 0
              CHECK (current_contract_revision >= 0)
            """
        )
        if fail_stage == "after_task_column":
            raise StorageError("internal_error", "injected task-contract migration failure")
        connection.execute(
            """
            CREATE TABLE task_contract_revisions (
              contract_revision_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK (revision > 0),
              scope TEXT NOT NULL,
              acceptance TEXT NOT NULL,
              constraints_text TEXT NOT NULL DEFAULT '',
              authority_ref TEXT NOT NULL DEFAULT '',
              change_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE (task_id, revision),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_contract_project_task_revision
              ON task_contract_revisions(project_id, task_id, revision)
            """
        )
        if fail_stage == "after_schema":
            raise StorageError("internal_error", "injected task-contract migration failure")
        nonzero_pointer_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE current_contract_revision != 0"
            ).fetchone()[0]
        )
        if nonzero_pointer_count:
            raise StorageError(
                "internal_error",
                "task-contract migration did not preserve revision-zero compatibility",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (8, "task_contract_revisions", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected task-contract migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_effort_advisory_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add the minimal basis and activity data required by Effort Advisory."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "effort-advisory migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            ALTER TABLE project_meta
              ADD COLUMN effort_activity_generation INTEGER NOT NULL DEFAULT 0
              CHECK (effort_activity_generation >= 0)
            """
        )
        if fail_stage == "after_project_column":
            raise StorageError("internal_error", "injected effort-advisory migration failure")
        connection.execute(
            """
            CREATE TABLE task_effort_activity (
              task_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE task_effort_bases (
              task_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              basis_head TEXT NOT NULL,
              basis_clean INTEGER NOT NULL CHECK (basis_clean IN (0, 1)),
              captured_at TEXT NOT NULL,
              project_generation INTEGER NOT NULL CHECK (project_generation >= 0),
              subject_generation INTEGER NOT NULL CHECK (subject_generation >= 0),
              other_active_at_capture INTEGER NOT NULL
                CHECK (other_active_at_capture IN (0, 1)),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_effort_bases_project
              ON task_effort_bases(project_id, captured_at, task_id)
            """
        )
        if fail_stage == "after_schema":
            raise StorageError("internal_error", "injected effort-advisory migration failure")
        legacy_counts = (
            int(connection.execute("SELECT COUNT(*) FROM task_effort_activity").fetchone()[0]),
            int(connection.execute("SELECT COUNT(*) FROM task_effort_bases").fetchone()[0]),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM project_meta WHERE effort_activity_generation != 0"
                ).fetchone()[0]
            ),
        )
        if legacy_counts != (0, 0, 0):
            raise StorageError(
                "internal_error",
                "effort-advisory migration did not preserve a strict disabled baseline",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (9, "informational_effort_advisory", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected effort-advisory migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_project_maintenance_migration(
    connection: sqlite3.Connection,
    *,
    setup_backup: MigrationBackupMetadata | None = None,
    fail_stage: str | None = None,
) -> None:
    """Add the partial one-row maintenance state used by setup and doctor."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "project-maintenance migration requires no active transaction",
        )
    existing_version = current_schema_version(connection)
    if existing_version >= 10:
        maintenance_missing = {
            item
            for item in required_schema_objects_missing(connection)
            if item == "table:project_maintenance"
            or item == "trigger:trg_project_maintenance_enabled_at_immutable"
            or item.startswith("column:project_maintenance.")
        }
        if (
            missing_migration_versions(connection, existing_version)
            or maintenance_missing
        ):
            raise StorageError(
                "migration_required",
                "project-maintenance migration is incomplete",
            )
        return
    if setup_backup is not None:
        validate_migration_backup_metadata(setup_backup)

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE project_maintenance (
              project_id TEXT PRIMARY KEY,
              enabled_at TEXT,
              backup_interval_minutes INTEGER
                CHECK (
                  backup_interval_minutes IS NULL
                  OR backup_interval_minutes BETWEEN 1 AND 1440
                ),
              backup_generations INTEGER
                CHECK (
                  backup_generations IS NULL
                  OR backup_generations BETWEEN 1 AND 20
                ),
              applied_backup_generations INTEGER
                CHECK (
                  applied_backup_generations IS NULL
                  OR applied_backup_generations BETWEEN 1 AND 20
                ),
              backup_last_success_at TEXT,
              backup_last_outcome_code TEXT
                CHECK (
                  backup_last_outcome_code IS NULL
                  OR backup_last_outcome_code IN ('succeeded', 'deferred', 'failed')
                ),
              backup_last_outcome_at TEXT,
              latest_backup_generation_id TEXT
                CHECK (
                  latest_backup_generation_id IS NULL
                  OR (
                    length(latest_backup_generation_id) = 42
                    AND latest_backup_generation_id GLOB
                      'tg_backup_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                  )
                ),
              viewer_last_success_at TEXT,
              viewer_last_outcome_code TEXT
                CHECK (
                  viewer_last_outcome_code IS NULL
                  OR viewer_last_outcome_code IN ('succeeded', 'deferred', 'failed')
                ),
              viewer_last_outcome_at TEXT,
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id),
              CHECK (
                (enabled_at IS NULL
                  AND backup_interval_minutes IS NULL
                  AND backup_generations IS NULL)
                OR
                (enabled_at IS NOT NULL
                  AND backup_interval_minutes IS NOT NULL
                  AND backup_generations IS NOT NULL)
              ),
              CHECK (
                (backup_last_outcome_code IS NULL
                  AND backup_last_outcome_at IS NULL)
                OR
                (backup_last_outcome_code IS NOT NULL
                  AND backup_last_outcome_at IS NOT NULL)
              ),
              CHECK (
                (viewer_last_outcome_code IS NULL
                  AND viewer_last_outcome_at IS NULL)
                OR
                (viewer_last_outcome_code IS NOT NULL
                  AND viewer_last_outcome_at IS NOT NULL)
              ),
              CHECK (
                (backup_last_success_at IS NULL
                  AND latest_backup_generation_id IS NULL
                  AND applied_backup_generations IS NULL)
                OR
                (backup_last_success_at IS NOT NULL
                  AND latest_backup_generation_id IS NOT NULL
                  AND applied_backup_generations IS NOT NULL)
              )
            )
            """
        )
        connection.execute(
            """
            CREATE TRIGGER trg_project_maintenance_enabled_at_immutable
            BEFORE UPDATE OF enabled_at ON project_maintenance
            WHEN OLD.enabled_at IS NOT NULL AND NEW.enabled_at IS NOT OLD.enabled_at
            BEGIN
              SELECT RAISE(ABORT, 'project maintenance enabled_at is immutable');
            END
            """
        )
        if fail_stage == "after_schema":
            raise StorageError(
                "internal_error",
                "injected project-maintenance migration failure",
            )

        project_rows = connection.execute(
            "SELECT project_id FROM project_meta ORDER BY project_id"
        ).fetchall()
        if len(project_rows) > 1:
            raise StorageError(
                "internal_error",
                "project-maintenance migration found multiple project identities",
            )
        if setup_backup is not None and not project_rows:
            raise StorageError(
                "internal_error",
                "setup backup metadata requires existing project identity",
            )
        if project_rows:
            project_id = str(project_rows[0]["project_id"])
            if setup_backup is None:
                connection.execute(
                    "INSERT INTO project_maintenance(project_id) VALUES (?)",
                    (project_id,),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO project_maintenance(
                      project_id, applied_backup_generations,
                      backup_last_success_at, backup_last_outcome_code,
                      backup_last_outcome_at, latest_backup_generation_id
                    ) VALUES (?, ?, ?, 'succeeded', ?, ?)
                    """,
                    (
                        project_id,
                        setup_backup.publication_retention,
                        setup_backup.published_at,
                        setup_backup.published_at,
                        setup_backup.generation_id,
                    ),
                )
        if fail_stage == "after_row":
            raise StorageError(
                "internal_error",
                "injected project-maintenance migration failure",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (10, "project_maintenance", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected project-maintenance migration failure",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_managed_backup_generations_migration(
    connection: sqlite3.Connection,
    *,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
    fail_stage: str | None = None,
) -> None:
    """Add the bounded managed-generation set and seed validated setup copies."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "managed-backup migration requires no active transaction",
        )
    existing_version = current_schema_version(connection)
    if existing_version >= 11:
        generation_missing = {
            item
            for item in required_schema_objects_missing(connection)
            if item == "table:managed_backup_generations"
            or item == "index:idx_managed_backup_project_published"
            or item.startswith("column:managed_backup_generations.")
        }
        if (
            missing_migration_versions(connection, existing_version)
            or generation_missing
        ):
            raise StorageError(
                "migration_required",
                "managed-backup migration is incomplete",
            )
        return

    validated = validate_managed_backup_metadata_set(managed_backups)

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE managed_backup_generations (
              generation_id TEXT PRIMARY KEY
                CHECK (
                  length(generation_id) = 42
                  AND generation_id GLOB
                    'tg_backup_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                ),
              project_id TEXT NOT NULL,
              published_at TEXT NOT NULL
                CHECK (
                  length(published_at) = 20
                  AND published_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                ),
              publication_retention INTEGER NOT NULL
                CHECK (publication_retention BETWEEN 1 AND 20),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_managed_backup_project_published
              ON managed_backup_generations(
                project_id, published_at, generation_id
              )
            """
        )
        if fail_stage == "after_schema":
            raise StorageError(
                "internal_error",
                "injected managed-backup migration failure",
            )

        project_rows = connection.execute(
            "SELECT project_id FROM project_meta ORDER BY project_id"
        ).fetchall()
        if len(project_rows) > 1:
            raise StorageError(
                "internal_error",
                "managed-backup migration found multiple project identities",
            )
        if validated and not project_rows:
            raise StorageError(
                "internal_error",
                "managed-backup seed requires existing project identity",
            )
        if project_rows:
            project_id = str(project_rows[0]["project_id"])
            maintenance = connection.execute(
                """
                SELECT applied_backup_generations, backup_last_success_at,
                       latest_backup_generation_id
                  FROM project_maintenance
                 WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if maintenance is None:
                raise StorageError(
                    "internal_error",
                    "managed-backup migration requires maintenance state",
                )
            latest_id = (
                str(maintenance["latest_backup_generation_id"])
                if maintenance["latest_backup_generation_id"] is not None
                else None
            )
            if latest_id is None:
                if validated:
                    raise StorageError(
                        "internal_error",
                        "managed-backup seed has no matching latest generation",
                    )
            else:
                latest = next(
                    (
                        metadata
                        for metadata in validated
                        if metadata.generation_id == latest_id
                    ),
                    None,
                )
                if (
                    latest is None
                    or latest.published_at
                    != str(maintenance["backup_last_success_at"])
                    or latest.publication_retention
                    != int(maintenance["applied_backup_generations"])
                ):
                    raise StorageError(
                        "internal_error",
                        "managed-backup seed does not match latest generation",
                    )
            for metadata in validated:
                _insert_managed_backup_generation(
                    connection,
                    project_id,
                    metadata,
                    allow_existing=False,
                )
        if fail_stage == "after_rows":
            raise StorageError(
                "internal_error",
                "injected managed-backup migration failure",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (11, "managed_backup_generations", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected managed-backup migration failure",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_task_checkpoints_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add the append-only typed continuation checkpoint store."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "task-checkpoint migration requires no active transaction",
        )
    existing_version = current_schema_version(connection)
    if existing_version >= 12:
        checkpoint_missing = {
            item
            for item in required_schema_objects_missing(connection)
            if item == "table:task_checkpoints"
            or item == "index:idx_checkpoints_project_task_created"
            or item.startswith("column:task_checkpoints.")
        }
        if (
            missing_migration_versions(connection, existing_version)
            or checkpoint_missing
        ):
            raise StorageError(
                "migration_required",
                "task-checkpoint migration is incomplete",
            )
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE task_checkpoints (
              checkpoint_id TEXT PRIMARY KEY
                CHECK (
                  length(CAST(checkpoint_id AS BLOB)) BETWEEN 1 AND 128
                ),
              task_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              contract_revision INTEGER NOT NULL
                CHECK (contract_revision >= 0),
              summary TEXT NOT NULL
                CHECK (
                  length(CAST(summary AS BLOB)) BETWEEN 1 AND 1024
                ),
              next_action TEXT NOT NULL
                CHECK (
                  length(CAST(next_action AS BLOB)) BETWEEN 1 AND 1024
                ),
              unresolved_risks_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                  length(CAST(unresolved_risks_json AS BLOB))
                    BETWEEN 2 AND 24601
                ),
              created_at TEXT NOT NULL
                CHECK (
                  length(created_at) = 20
                  AND created_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                ),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_checkpoints_project_task_created
              ON task_checkpoints(
                project_id, task_id, created_at, checkpoint_id
              )
            """
        )
        if fail_stage == "after_schema":
            raise StorageError(
                "internal_error",
                "injected task-checkpoint migration failure",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (12, "task_checkpoints", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected task-checkpoint migration failure",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
) -> tuple[list[int], list[dict[str, str]]]:
    version = current_schema_version(connection)
    if version > SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            f"database schema version {version} is newer than supported version {SCHEMA_VERSION}",
        )
    if version > 0 and missing_migration_versions(connection, version):
        raise StorageError(
            "migration_required",
            (
                "database migration history is incomplete; restore a valid "
                "database backup or inspect the migration history"
            ),
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
        version = 5
    if version < 6:
        apply_git_snapshot_schema_migration(connection)
        applied.append(6)
        version = 6
    if version < 7:
        apply_handoff_outbox_migration(connection)
        applied.append(7)
        version = 7
    if version < 8:
        apply_task_contract_migration(connection)
        applied.append(8)
        version = 8
    if version < 9:
        apply_effort_advisory_migration(connection)
        applied.append(9)
        version = 9
    if version < 10:
        apply_project_maintenance_migration(
            connection,
            setup_backup=setup_backup,
        )
        applied.append(10)
        version = 10
    if version < 11:
        apply_managed_backup_generations_migration(
            connection,
            managed_backups=managed_backups,
        )
        applied.append(11)
        version = 11
    if version < 12:
        apply_task_checkpoints_migration(connection)
        applied.append(12)
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


def read_project_maintenance(
    connection: sqlite3.Connection,
    project_id: str,
) -> ProjectMaintenanceState | None:
    row = connection.execute(
        """
        SELECT project_id, enabled_at, backup_interval_minutes,
               backup_generations, applied_backup_generations,
               backup_last_success_at, backup_last_outcome_code,
               backup_last_outcome_at, latest_backup_generation_id,
               viewer_last_success_at, viewer_last_outcome_code,
               viewer_last_outcome_at
          FROM project_maintenance
         WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return ProjectMaintenanceState(
        project_id=str(row["project_id"]),
        enabled_at=(
            str(row["enabled_at"]) if row["enabled_at"] is not None else None
        ),
        backup_interval_minutes=(
            int(row["backup_interval_minutes"])
            if row["backup_interval_minutes"] is not None
            else None
        ),
        backup_generations=(
            int(row["backup_generations"])
            if row["backup_generations"] is not None
            else None
        ),
        applied_backup_generations=(
            int(row["applied_backup_generations"])
            if row["applied_backup_generations"] is not None
            else None
        ),
        backup_last_success_at=(
            str(row["backup_last_success_at"])
            if row["backup_last_success_at"] is not None
            else None
        ),
        backup_last_outcome_code=(
            str(row["backup_last_outcome_code"])
            if row["backup_last_outcome_code"] is not None
            else None
        ),
        backup_last_outcome_at=(
            str(row["backup_last_outcome_at"])
            if row["backup_last_outcome_at"] is not None
            else None
        ),
        latest_backup_generation_id=(
            str(row["latest_backup_generation_id"])
            if row["latest_backup_generation_id"] is not None
            else None
        ),
        viewer_last_success_at=(
            str(row["viewer_last_success_at"])
            if row["viewer_last_success_at"] is not None
            else None
        ),
        viewer_last_outcome_code=(
            str(row["viewer_last_outcome_code"])
            if row["viewer_last_outcome_code"] is not None
            else None
        ),
        viewer_last_outcome_at=(
            str(row["viewer_last_outcome_at"])
            if row["viewer_last_outcome_at"] is not None
            else None
        ),
    )


def ensure_project_maintenance_row(
    connection: sqlite3.Connection,
    project_id: str,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO project_maintenance(project_id) VALUES (?)",
        (project_id,),
    )


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
                f"version {SCHEMA_VERSION}; run setup to migrate"
            ),
        )

    if missing_migration_versions(connection, version):
        raise StorageError(
            "migration_required",
            (
                "database migration history is incomplete; restore a valid "
                "database backup or inspect the migration history"
            ),
        )

    if required_schema_objects_missing(connection):
        raise StorageError(
            "migration_required",
            "database schema is incomplete; run setup to migrate",
        )

    existing_project_id = read_project_meta_id(connection)
    if existing_project_id is None:
        raise StorageError(
            "migration_required",
            "database project metadata is missing; run setup to repair",
        )
    if existing_project_id != target.project.project_id:
        raise StorageError(
            "project_mismatch",
            (
                f"database belongs to project {existing_project_id}, "
                f"not {target.project.project_id}"
            ),
        )
    if read_project_maintenance(connection, existing_project_id) is None:
        raise StorageError(
            "migration_required",
            "database project maintenance state is missing; run setup to repair",
        )
    return version


def validate_managed_backup_source_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> int:
    """Validate the stable v11 backup repository on a migration source."""
    version = current_schema_version(connection)
    if version == SCHEMA_VERSION:
        return validate_current_database(connection, target)
    if version < 11 or version > SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            "managed backup state requires schema version 11 or newer",
        )
    if missing_migration_versions(connection, version):
        raise StorageError(
            "migration_required",
            "managed backup migration history is incomplete",
        )
    existing_project_id = read_project_meta_id(connection)
    if existing_project_id is None:
        raise StorageError(
            "migration_required",
            "database project metadata is missing; run setup to repair",
        )
    if existing_project_id != target.project.project_id:
        raise StorageError(
            "project_mismatch",
            (
                f"database belongs to project {existing_project_id}, "
                f"not {target.project.project_id}"
            ),
        )
    if read_project_maintenance(connection, existing_project_id) is None:
        raise StorageError(
            "migration_required",
            "database project maintenance state is missing; run setup to repair",
        )
    return version


def connect_initialized(
    target: DatabaseTarget,
    *,
    managed_backup_source: bool = False,
) -> sqlite3.Connection:
    """Open a current, project-matching database without taking a write lock."""
    validate_operational_journal_state(target.db_path)
    if not target.db_path.exists():
        raise StorageError(
            "db_not_initialized",
            "project state is not set up; run setup first",
        )
    try:
        connection = connect_existing(target.db_path)
    except sqlite3.Error as exc:
        if not target.db_path.exists():
            raise StorageError(
                "db_not_initialized",
                "project state is not set up; run setup first",
            ) from exc
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open database",
        ) from exc
    try:
        (
            validate_managed_backup_source_database
            if managed_backup_source
            else validate_current_database
        )(connection, target)
    except sqlite3.Error as exc:
        connection.close()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not inspect database",
        ) from exc
    except Exception:
        connection.close()
        raise
    return connection


def begin_initialized_write(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    *,
    managed_backup_source: bool = False,
) -> None:
    """Acquire the short writer transaction and revalidate its database owner."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "write transaction was acquired before preflight completed",
        )
    try:
        connection.execute("BEGIN IMMEDIATE")
        (
            validate_managed_backup_source_database
            if managed_backup_source
            else validate_current_database
        )(connection, target)
    except sqlite3.Error as exc:
        connection.rollback()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not start database write",
        ) from exc
    except Exception:
        connection.rollback()
        raise


def connect_initialized_readonly(
    target: DatabaseTarget,
    *,
    managed_backup_source: bool = False,
) -> sqlite3.Connection:
    """Open one validated current-schema read transaction."""
    try:
        connection = connect_readonly(target.db_path)
    except StorageError:
        raise
    except sqlite3.Error as exc:
        if not target.db_path.exists():
            raise StorageError(
                "db_not_initialized",
                "project state is not set up; run setup first",
            ) from exc
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open database",
        ) from exc
    try:
        (
            validate_managed_backup_source_database
            if managed_backup_source
            else validate_current_database
        )(connection, target)
    except sqlite3.Error as exc:
        connection.rollback()
        connection.close()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not inspect database",
        ) from exc
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection


def validate_backup_policy(
    *,
    interval_minutes: int,
    generations: int,
) -> tuple[int, int]:
    if (
        isinstance(interval_minutes, bool)
        or not isinstance(interval_minutes, int)
        or not MIN_BACKUP_INTERVAL_MINUTES
        <= interval_minutes
        <= MAX_BACKUP_INTERVAL_MINUTES
        or isinstance(generations, bool)
        or not isinstance(generations, int)
        or not MIN_BACKUP_GENERATIONS <= generations <= MAX_BACKUP_GENERATIONS
    ):
        raise StorageError(
            "invalid_backup_policy",
            "backup policy is outside the supported range",
        )
    return interval_minutes, generations


def configure_project_maintenance(
    target: DatabaseTarget,
    *,
    requested_interval_minutes: int | None,
    requested_generations: int | None,
    enabled_at: str | None = None,
) -> tuple[int, int]:
    activation_time = validate_utc_timestamp(
        enabled_at or utc_now(),
        field="maintenance enablement time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            current = read_project_maintenance(
                connection,
                target.project.project_id,
            )
            if current is None:
                raise StorageError(
                    "migration_required",
                    "database project maintenance state is missing; run setup to repair",
                )
            interval_minutes = (
                requested_interval_minutes
                if requested_interval_minutes is not None
                else (
                    current.backup_interval_minutes
                    if current.enabled
                    else DEFAULT_BACKUP_INTERVAL_MINUTES
                )
            )
            generations = (
                requested_generations
                if requested_generations is not None
                else (
                    current.backup_generations
                    if current.enabled
                    else DEFAULT_BACKUP_GENERATIONS
                )
            )
            interval_minutes, generations = validate_backup_policy(
                interval_minutes=interval_minutes,
                generations=generations,
            )
            if (
                current.enabled
                and current.backup_interval_minutes == interval_minutes
                and current.backup_generations == generations
            ):
                connection.commit()
                return interval_minutes, generations
            connection.execute(
                """
                UPDATE project_maintenance
                   SET enabled_at = COALESCE(enabled_at, ?),
                       backup_interval_minutes = ?,
                       backup_generations = ?
                 WHERE project_id = ?
                """,
                (
                    activation_time,
                    interval_minutes,
                    generations,
                    target.project.project_id,
                ),
            )
            connection.commit()
            return interval_minutes, generations
        except Exception:
            connection.rollback()
            raise


def _begin_backup_metadata_write(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    *,
    allowed_versions: set[int],
) -> int:
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "backup metadata write requires no active transaction",
        )
    try:
        connection.execute("BEGIN IMMEDIATE")
        version = current_schema_version(connection)
        if (
            version not in allowed_versions
            or missing_migration_versions(connection, version)
            or read_project_meta_id(connection) != target.project.project_id
            or read_project_maintenance(
                connection,
                target.project.project_id,
            )
            is None
        ):
            raise StorageError(
                "migration_required",
                "backup metadata requires valid project maintenance state",
            )
        return version
    except sqlite3.Error as exc:
        connection.rollback()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not start backup metadata write",
        ) from exc
    except Exception:
        connection.rollback()
        raise


def _metadata_from_generation_row(
    row: sqlite3.Row,
) -> MigrationBackupMetadata:
    return validate_migration_backup_metadata(
        MigrationBackupMetadata(
            generation_id=str(row["generation_id"]),
            published_at=str(row["published_at"]),
            publication_retention=int(row["publication_retention"]),
        )
    )


def _write_backup_success(
    connection: sqlite3.Connection,
    project_id: str,
    metadata: MigrationBackupMetadata,
) -> None:
    validated = validate_migration_backup_metadata(metadata)
    connection.execute(
        """
        UPDATE project_maintenance
           SET applied_backup_generations = ?,
               backup_last_success_at = ?,
               backup_last_outcome_code = 'succeeded',
               backup_last_outcome_at = ?,
               latest_backup_generation_id = ?
         WHERE project_id = ?
        """,
        (
            validated.publication_retention,
            validated.published_at,
            validated.published_at,
            validated.generation_id,
            project_id,
        ),
    )


def _insert_managed_backup_generation(
    connection: sqlite3.Connection,
    project_id: str,
    metadata: MigrationBackupMetadata,
    *,
    allow_existing: bool,
) -> None:
    validated = validate_migration_backup_metadata(metadata)
    existing = connection.execute(
        """
        SELECT generation_id, published_at, publication_retention
          FROM managed_backup_generations
         WHERE generation_id = ? AND project_id = ?
        """,
        (validated.generation_id, project_id),
    ).fetchone()
    if existing is not None:
        if (
            not allow_existing
            or _metadata_from_generation_row(existing) != validated
        ):
            raise StorageError(
                "internal_error",
                "managed backup generation metadata changed",
            )
        return
    connection.execute(
        """
        INSERT INTO managed_backup_generations(
          generation_id, project_id, published_at, publication_retention
        ) VALUES (?, ?, ?, ?)
        """,
        (
            validated.generation_id,
            project_id,
            validated.published_at,
            validated.publication_retention,
        ),
    )


def record_setup_backup(
    target: DatabaseTarget,
    metadata: MigrationBackupMetadata,
) -> None:
    validated = validate_migration_backup_metadata(metadata)
    validate_operational_journal_state(target.db_path)
    with closing(connect_existing(target.db_path)) as connection:
        try:
            _begin_backup_metadata_write(
                connection,
                target,
                allowed_versions={10},
            )
            _write_backup_success(
                connection,
                target.project.project_id,
                validated,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def list_managed_backup_generations(
    connection: sqlite3.Connection,
    project_id: str,
) -> tuple[MigrationBackupMetadata, ...]:
    rows = connection.execute(
        """
        SELECT generation_id, published_at, publication_retention
          FROM managed_backup_generations
         WHERE project_id = ?
         ORDER BY published_at, generation_id
        """,
        (project_id,),
    ).fetchall()
    return tuple(
        _metadata_from_generation_row(row)
        for row in rows
    )


def _connect_managed_backup_repository(
    target: DatabaseTarget,
    *,
    migration_source: bool,
    read_only: bool,
) -> sqlite3.Connection:
    return (
        connect_initialized_readonly(
            target,
            managed_backup_source=migration_source,
        )
        if read_only
        else connect_initialized(
            target,
            managed_backup_source=migration_source,
        )
    )


def _begin_managed_backup_repository_write(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    *,
    migration_source: bool,
) -> None:
    begin_initialized_write(
        connection,
        target,
        managed_backup_source=migration_source,
    )


def read_managed_backup_repository(
    target: DatabaseTarget,
    *,
    migration_source: bool = False,
) -> ManagedBackupRepositoryState:
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=True,
        )
    ) as connection:
        maintenance = read_project_maintenance(
            connection,
            target.project.project_id,
        )
        if maintenance is None:
            raise StorageError(
                "migration_required",
                "database project maintenance state is missing; run setup to repair",
            )
        generations = list_managed_backup_generations(
            connection,
            target.project.project_id,
        )
    return ManagedBackupRepositoryState(
        maintenance=maintenance,
        generations=generations,
    )


def _latest_managed_backup_row(
    connection: sqlite3.Connection,
    project_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT generation_id, published_at, publication_retention
          FROM managed_backup_generations
         WHERE project_id = ?
         ORDER BY published_at DESC, generation_id DESC
         LIMIT 1
        """,
        (project_id,),
    ).fetchone()


def _update_backup_pointer_from_row(
    connection: sqlite3.Connection,
    project_id: str,
    row: sqlite3.Row | None,
    *,
    outcome_code: str | None = None,
    outcome_at: str | None = None,
) -> None:
    if outcome_code is not None:
        if outcome_code not in {"deferred", "failed"} or outcome_at is None:
            raise StorageError("internal_error", "backup outcome is invalid")
        validate_utc_timestamp(outcome_at, field="backup outcome time")
    if row is None:
        metadata = None
    else:
        metadata = _metadata_from_generation_row(row)
    connection.execute(
        """
        UPDATE project_maintenance
           SET applied_backup_generations = ?,
               backup_last_success_at = ?,
               backup_last_outcome_code =
                 CASE WHEN ? IS NULL THEN backup_last_outcome_code ELSE ? END,
               backup_last_outcome_at =
                 CASE WHEN ? IS NULL THEN backup_last_outcome_at ELSE ? END,
               latest_backup_generation_id = ?
         WHERE project_id = ?
        """,
        (
            metadata.publication_retention if metadata is not None else None,
            metadata.published_at if metadata is not None else None,
            outcome_code,
            outcome_code,
            outcome_code,
            outcome_at,
            metadata.generation_id if metadata is not None else None,
            project_id,
        ),
    )


def record_managed_backup(
    target: DatabaseTarget,
    metadata: MigrationBackupMetadata,
    *,
    migration_source: bool = False,
) -> None:
    validated = validate_migration_backup_metadata(metadata)
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=False,
        )
    ) as connection:
        try:
            _begin_managed_backup_repository_write(
                connection,
                target,
                migration_source=migration_source,
            )
            _insert_managed_backup_generation(
                connection,
                target.project.project_id,
                validated,
                allow_existing=False,
            )
            _write_backup_success(
                connection,
                target.project.project_id,
                validated,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def import_managed_backup_generations(
    target: DatabaseTarget,
    metadata_items: tuple[MigrationBackupMetadata, ...],
    *,
    migration_source: bool = False,
) -> None:
    validated = validate_managed_backup_metadata_set(metadata_items)
    if not validated:
        return
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=False,
        )
    ) as connection:
        try:
            _begin_managed_backup_repository_write(
                connection,
                target,
                migration_source=migration_source,
            )
            for metadata in validated:
                _insert_managed_backup_generation(
                    connection,
                    target.project.project_id,
                    metadata,
                    allow_existing=True,
                )
            latest = _latest_managed_backup_row(
                connection,
                target.project.project_id,
            )
            if latest is None:
                raise StorageError(
                    "internal_error",
                    "managed backup import did not persist a generation",
                )
            _write_backup_success(
                connection,
                target.project.project_id,
                _metadata_from_generation_row(latest),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def delete_managed_backup_generation(
    target: DatabaseTarget,
    generation_id: str,
    *,
    failure_at: str | None = None,
    migration_source: bool = False,
) -> None:
    if not MANAGED_BACKUP_GENERATION_PATTERN.fullmatch(generation_id):
        raise StorageError("internal_error", "managed backup identity is invalid")
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=False,
        )
    ) as connection:
        try:
            _begin_managed_backup_repository_write(
                connection,
                target,
                migration_source=migration_source,
            )
            connection.execute(
                """
                DELETE FROM managed_backup_generations
                 WHERE generation_id = ? AND project_id = ?
                """,
                (generation_id, target.project.project_id),
            )
            latest = _latest_managed_backup_row(
                connection,
                target.project.project_id,
            )
            _update_backup_pointer_from_row(
                connection,
                target.project.project_id,
                latest,
                outcome_code="failed" if failure_at is not None else None,
                outcome_at=failure_at,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def normalize_managed_backup_pointer(
    target: DatabaseTarget,
    *,
    migration_source: bool = False,
) -> None:
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=False,
        )
    ) as connection:
        try:
            _begin_managed_backup_repository_write(
                connection,
                target,
                migration_source=migration_source,
            )
            latest = _latest_managed_backup_row(
                connection,
                target.project.project_id,
            )
            _update_backup_pointer_from_row(
                connection,
                target.project.project_id,
                latest,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def record_backup_attempt_outcome(
    target: DatabaseTarget,
    *,
    code: str,
    occurred_at: str,
) -> None:
    if code not in {"deferred", "failed"}:
        raise StorageError("internal_error", "backup outcome is invalid")
    timestamp = validate_utc_timestamp(
        occurred_at,
        field="backup outcome time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            connection.execute(
                """
                UPDATE project_maintenance
                   SET backup_last_outcome_code = ?,
                       backup_last_outcome_at = ?
                 WHERE project_id = ?
                """,
                (code, timestamp, target.project.project_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def record_setup_viewer(
    target: DatabaseTarget,
    *,
    published_at: str,
) -> None:
    timestamp = validate_utc_timestamp(
        published_at,
        field="Viewer publication time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            connection.execute(
                """
                UPDATE project_maintenance
                   SET viewer_last_success_at = ?,
                       viewer_last_outcome_code = 'succeeded',
                       viewer_last_outcome_at = ?
                 WHERE project_id = ?
                """,
                (timestamp, timestamp, target.project.project_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


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

    version = current_schema_version(connection)
    if version < 5 or version > SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            (
                f"database schema version {version} is not supported by "
                "Viewer snapshot version 3"
            ),
        )
    if missing_migration_versions(connection, version):
        raise StorageError(
            "migration_required",
            "database migration history is incomplete",
        )

    required_tables = {
        "schema_migrations",
        "project_meta",
        "tasks",
        "task_events",
        "review_receipts",
        "review_findings",
    }
    table_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    tables = {str(row["name"]) for row in table_rows}
    if required_tables - tables:
        raise StorageError(
            "migration_required",
            "database schema is incomplete for Viewer snapshot version 3",
        )

    from task_governance_tool.tasks import VIEWER_TASK_FIELDS

    task_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if set(VIEWER_TASK_FIELDS) - task_columns:
        raise StorageError(
            "migration_required",
            "database task schema is incomplete for Viewer snapshot version 3",
        )
    receipt_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(review_receipts)").fetchall()
    }
    required_receipt_columns = {
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
    }
    if version >= 6:
        required_receipt_columns.add("target_base_revision")
    if required_receipt_columns - receipt_columns:
        raise StorageError(
            "migration_required",
            "database review schema is incomplete for Viewer snapshot version 3",
        )

    existing_project_id = read_project_meta_id(connection)
    if existing_project_id is None:
        raise StorageError(
            "migration_required",
            "database project metadata is missing",
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
    counts["handoff_pending"] = int(
        connection.execute(
            """
            SELECT COUNT(*) AS count
              FROM handoff_records
             WHERE project_id = ?
               AND state = 'pending_handoff'
            """,
            (project_id,),
        ).fetchone()["count"]
    )
    return counts


def read_setup_state(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> SetupStorageState:
    try:
        version = current_schema_version(connection)
        if version == 0:
            return SetupStorageState(
                schema_version=None,
                needs_initialize=True,
                needs_migration=False,
                maintenance_enabled=False,
                backup_interval_minutes=None,
                backup_generations=None,
            )
        if version > SCHEMA_VERSION:
            raise StorageError(
                "schema_too_new",
                "task database schema is newer than this taskgov version",
            )
        if missing_migration_versions(connection, version):
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        if not table_exists(connection, "project_meta"):
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        existing_project_id = read_project_meta_id(connection)
        if existing_project_id is None:
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        if existing_project_id != target.project.project_id:
            raise StorageError(
                "project_mismatch",
                "task database belongs to a different project",
            )
        maintenance = None
        if version >= 10:
            maintenance_missing = {
                item
                for item in required_schema_objects_missing(connection)
                if item == "table:project_maintenance"
                or item
                == "trigger:trg_project_maintenance_enabled_at_immutable"
                or item.startswith("column:project_maintenance.")
            }
            if maintenance_missing:
                raise StorageError(
                    "project_state_unreadable",
                    "project state could not be read safely",
                )
            maintenance = read_project_maintenance(
                connection,
                target.project.project_id,
            )
            if maintenance is None:
                raise StorageError(
                    "project_state_unreadable",
                    "project state could not be read safely",
                )
        if version < SCHEMA_VERSION:
            return SetupStorageState(
                schema_version=version,
                needs_initialize=False,
                needs_migration=True,
                maintenance_enabled=bool(
                    maintenance is not None and maintenance.enabled
                ),
                backup_interval_minutes=(
                    maintenance.backup_interval_minutes
                    if maintenance is not None
                    else None
                ),
                backup_generations=(
                    maintenance.backup_generations
                    if maintenance is not None
                    else None
                ),
            )
        if required_schema_objects_missing(connection):
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        if maintenance is None:
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        return SetupStorageState(
            schema_version=version,
            needs_initialize=False,
            needs_migration=False,
            maintenance_enabled=maintenance.enabled,
            backup_interval_minutes=maintenance.backup_interval_minutes,
            backup_generations=maintenance.backup_generations,
        )
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="project state could not be read safely",
        ) from exc


def inspect_setup_state(target: DatabaseTarget) -> SetupStorageState:
    validate_operational_journal_state(target.db_path)
    if not target.db_path.exists():
        return SetupStorageState(
            schema_version=None,
            needs_initialize=True,
            needs_migration=False,
            maintenance_enabled=False,
            backup_interval_minutes=None,
            backup_generations=None,
        )
    try:
        with closing(connect_readonly(target.db_path)) as connection:
            return read_setup_state(connection, target)
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="project state could not be read safely",
        ) from exc


def read_doctor_state(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> DoctorStorageState:
    setup_state = read_setup_state(connection, target)
    if setup_state.needs_initialize:
        raise StorageError("setup_required", "project state is not set up")
    if setup_state.needs_migration:
        raise StorageError(
            "migration_required",
            "task database requires setup migration",
        )
    maintenance = read_project_maintenance(
        connection,
        target.project.project_id,
    )
    if maintenance is None:
        raise StorageError(
            "project_state_unreadable",
            "project state could not be read safely",
        )
    return DoctorStorageState(
        schema_version=SCHEMA_VERSION,
        project_code="ready" if maintenance.enabled else "setup_required",
        task_counts=count_tasks(connection, target.project.project_id),
        maintenance=maintenance,
    )


def initialize_database(
    target: DatabaseTarget,
    *,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
) -> InitResult:
    created = not target.db_path.exists()
    try:
        validate_operational_journal_state(target.db_path)
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
                migrations_applied, warnings = apply_migrations(
                    connection,
                    setup_backup=setup_backup,
                    managed_backups=managed_backups,
                )
                ensure_project_meta(connection, target.project)
                ensure_project_maintenance_row(
                    connection,
                    target.project.project_id,
                )
                version = validate_current_database(connection, target)
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError("internal_error", "could not prepare database path") from exc
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open or initialize database",
        ) from exc
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
