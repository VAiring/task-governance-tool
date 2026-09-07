"""Maintenance policy and atomic managed-backup metadata persistence.

Connection admission and migrations remain storage-owned; physical backup
publication, reconciliation, pruning, and locking remain with their callers.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_governance_tool.sqlite_connection import (
    StorageError,
    connect_existing,
    operational_sqlite_error,
    validate_operational_journal_state,
)

if TYPE_CHECKING:
    from task_governance_tool.storage import DatabaseTarget


MIN_BACKUP_INTERVAL_MINUTES = 1
MAX_BACKUP_INTERVAL_MINUTES = 1_440
MIN_BACKUP_GENERATIONS = 1
MAX_BACKUP_GENERATIONS = 20
DEFAULT_BACKUP_INTERVAL_MINUTES = 30
DEFAULT_BACKUP_GENERATIONS = 3
MANAGED_BACKUP_GENERATION_PATTERN = re.compile(r"^tg_backup_[0-9a-f]{32}$")


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


def validate_migration_backup_metadata(
    metadata: MigrationBackupMetadata,
) -> MigrationBackupMetadata:
    from task_governance_tool.storage import (
        validate_utc_timestamp,
    )

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


def read_project_maintenance(
    connection: sqlite3.Connection,
    project_id: str,
) -> ProjectMaintenanceState | None:
    from task_governance_tool.storage import (
        validate_sqlite_integer_storage_class,
    )

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
            validate_sqlite_integer_storage_class(
                row["backup_interval_minutes"]
            )
            if row["backup_interval_minutes"] is not None
            else None
        ),
        backup_generations=(
            validate_sqlite_integer_storage_class(row["backup_generations"])
            if row["backup_generations"] is not None
            else None
        ),
        applied_backup_generations=(
            validate_sqlite_integer_storage_class(
                row["applied_backup_generations"]
            )
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
    from task_governance_tool.storage import (
        begin_initialized_write,
        connect_initialized,
        utc_now,
        validate_utc_timestamp,
    )

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
    from task_governance_tool.storage import (
        current_schema_version,
        missing_migration_versions,
        read_project_meta_id,
    )

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
    from task_governance_tool.storage import (
        validate_sqlite_integer_storage_class,
    )

    return validate_migration_backup_metadata(
        MigrationBackupMetadata(
            generation_id=str(row["generation_id"]),
            published_at=str(row["published_at"]),
            publication_retention=validate_sqlite_integer_storage_class(
                row["publication_retention"]
            ),
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
    from task_governance_tool.storage import (
        connect_initialized,
        connect_initialized_readonly,
    )

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
    from task_governance_tool.storage import (
        begin_initialized_write,
    )

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
    from task_governance_tool.storage import (
        validate_utc_timestamp,
    )

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
    from task_governance_tool.storage import (
        begin_initialized_write,
        connect_initialized,
        validate_utc_timestamp,
    )

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
