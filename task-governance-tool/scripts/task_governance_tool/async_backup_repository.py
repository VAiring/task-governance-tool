"""Short live-database boundaries for asynchronous routine backup only.

These observations certify control metadata, not the database's business rows.
The private snapshot receives the complete publication validation. Setup,
migration and recovery keep their existing admission and publication paths.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from task_governance_tool import backup_metadata_repository as metadata
from task_governance_tool.project_binding_repository import (
    ProjectBindingState, _validate_target_binding, read_project_binding_state,
)
from task_governance_tool.sqlite_connection import (
    DATABASE_BUSY_MESSAGE, StorageError, configure_connection, connect_existing, connect_readonly,
    operational_sqlite_error, validate_operational_journal_state,
)
from task_governance_tool.sqlite_transaction_trace import connect_traced
from task_governance_tool.state_paths import inspect_physical_file
from task_governance_tool.storage import (
    DatabaseTarget, _validate_current_schema_contract, utc_now,
    validate_utc_timestamp,
)


MAX_GENERATIONS_WITH_RESIDUE = metadata.MAX_BACKUP_GENERATIONS + 1


def failure() -> StorageError:
    return StorageError("async_backup_failed", "asynchronous backup could not be completed")


def _database_identity(target: DatabaseTarget) -> tuple[int, int]:
    root = target.project.canonical_repo if target.canonical_fixed else target.db_path.parent
    _, identity = inspect_physical_file(target.db_path, root=root)
    if target.db_path.lstat().st_nlink != 1:
        raise failure()
    return identity.device, identity.inode


@dataclass(frozen=True)
class BackupControl:
    maintenance: metadata.ProjectMaintenanceState
    generations: tuple[metadata.MigrationBackupMetadata, ...]
    database_identity: tuple[int, int]
    schema_version: int
    schema_cookie: int
    binding: ProjectBindingState


def _validate_maintenance(state: metadata.ProjectMaintenanceState | None) -> None:
    if state is None:
        raise failure()
    policy = (state.backup_interval_minutes, state.backup_generations)
    if state.enabled:
        validate_utc_timestamp(state.enabled_at, field="backup opt-in time")
        metadata.validate_backup_policy(interval_minutes=policy[0], generations=policy[1])
    elif policy != (None, None):
        raise failure()
    outcome = (state.backup_last_outcome_code, state.backup_last_outcome_at)
    if outcome != (None, None):
        if outcome[0] not in {"succeeded", "failed", "deferred"}:
            raise failure()
        validate_utc_timestamp(outcome[1], field="backup outcome time")
    success = (state.latest_backup_generation_id, state.backup_last_success_at,
               state.applied_backup_generations)
    if success != (None, None, None):
        metadata.validate_migration_backup_metadata(metadata.MigrationBackupMetadata(*success))


def _read_control(connection: sqlite3.Connection, target: DatabaseTarget,
                  identity: tuple[int, int], *, admit: bool = True) -> BackupControl:
    version = _validate_current_schema_contract(connection) if admit else None
    binding = read_project_binding_state(connection)
    _validate_target_binding(binding, target)
    state = metadata.read_project_maintenance(connection, target.project.project_id)
    _validate_maintenance(state)
    rows = connection.execute(
        "SELECT generation_id, published_at, publication_retention "
        "FROM managed_backup_generations WHERE project_id = ? "
        "ORDER BY published_at, generation_id LIMIT ?",
        (target.project.project_id, MAX_GENERATIONS_WITH_RESIDUE + 1),
    ).fetchall()
    if len(rows) > MAX_GENERATIONS_WITH_RESIDUE:
        raise failure()
    generations = tuple(metadata._metadata_from_generation_row(row) for row in rows)
    metadata.validate_managed_backup_metadata_set(generations)
    cookie = connection.execute("PRAGMA schema_version").fetchone()[0]
    if type(cookie) is not int or cookie < 0:
        raise failure()
    if _database_identity(target) != identity:
        raise failure()
    if version is None:
        from task_governance_tool.storage import SCHEMA_VERSION
        version = SCHEMA_VERSION
    return BackupControl(state, generations, identity, version, cookie, binding)


def _basis(value: BackupControl) -> tuple:
    state = value.maintenance
    return (
        value.database_identity, value.schema_version, value.schema_cookie,
        value.binding, value.generations, state.project_id, state.enabled_at,
        state.backup_interval_minutes, state.backup_generations,
        state.applied_backup_generations, state.backup_last_success_at,
        state.backup_last_outcome_code, state.backup_last_outcome_at,
        state.latest_backup_generation_id,
    )


def require_same_control(actual: BackupControl, expected: BackupControl) -> None:
    """Task/Viewer mutations are allowed; backup/binding/schema drift is not."""
    if _basis(actual) != _basis(expected):
        raise failure()


@contextmanager
def control_snapshot(target: DatabaseTarget, *, expected: BackupControl | None = None,
                     timeout: float = 0.1) -> Iterator[tuple[sqlite3.Connection, BackupControl]]:
    identity = _database_identity(target)
    if expected is not None and identity != expected.database_identity:
        raise failure()
    try:
        with closing(connect_readonly(target.db_path, timeout=timeout)) as connection:
            current = _read_control(connection, target, identity)
            if expected is not None:
                require_same_control(current, expected)
            yield connection, current
            if _database_identity(target) != identity:
                raise failure()
    except sqlite3.Error as exc:
        raise operational_sqlite_error(exc, fallback_message="backup control could not be read") from exc


def read_control(target: DatabaseTarget, *, timeout: float = 0.1) -> BackupControl:
    with control_snapshot(target, timeout=timeout) as (_, current):
        return current


def read_request_policy(target: DatabaseTarget) -> metadata.ProjectMaintenanceState:
    """Observe eligibility only; this never admits a copy or publication.

    The business command has already admitted its target. Reopening this short
    snapshot checks only the current binding and consumed policy. The service
    and child independently perform full control admission before any backup.
    """
    from task_governance_tool.storage import validate_lower_hex_64
    from task_governance_tool.project_binding_repository import (
        validate_binding_generation, validate_identity_project_id,
    )
    identity = _database_identity(target)
    try:
        with closing(connect_readonly(target.db_path, timeout=0.1)) as connection:
            rows = connection.execute(
                "SELECT project_id, identity_scheme, canonical_path_hash, binding_generation "
                "FROM project_meta LIMIT 2").fetchall()
            if len(rows) != 1:
                raise failure()
            row = rows[0]
            project_id = validate_identity_project_id(row["project_id"], row["identity_scheme"])
            path_hash = validate_lower_hex_64(row["canonical_path_hash"], field="backup binding")
            generation = validate_binding_generation(row["binding_generation"])
            if (project_id, path_hash, generation) != (
                    target.project.project_id, target.binding_path_hash, target.binding_generation):
                raise failure()
            policy = metadata.read_project_maintenance(connection, project_id)
            _validate_maintenance(policy)
            if _database_identity(target) != identity:
                raise failure()
            return policy
    except sqlite3.Error as exc:
        raise operational_sqlite_error(exc, fallback_message="backup policy could not be read") from exc


def read_service_target(skill_root: Path, *, repo: Path) -> DatabaseTarget | None:
    """One MCP startup admission; no discovery, migration, Task audit or retry.

    Callers must first admit the physical project/package and effective ignore.
    Absence of the existing maintenance opt-in leaves the service inert.
    """
    from task_governance_tool.state_resolver import backup_runtime_target, canonical_state_paths
    paths = canonical_state_paths(skill_root, repo=repo)
    _, before = inspect_physical_file(paths.database, root=repo)
    if paths.database.lstat().st_nlink != 1:
        raise failure()
    try:
        with closing(connect_readonly(paths.database, timeout=0.1)) as connection:
            _validate_current_schema_contract(connection)
            binding = read_project_binding_state(connection)
            target = backup_runtime_target(skill_root, repo=repo, project_id=binding.project_id,
                binding_path_hash=binding.canonical_path_hash, binding_generation=binding.binding_generation)
            policy = metadata.read_project_maintenance(connection, binding.project_id)
            _validate_maintenance(policy)
            if _database_identity(target) != (before.device, before.inode):
                raise failure()
            return target if policy.enabled else None
    except sqlite3.Error as exc:
        raise operational_sqlite_error(exc, fallback_message="backup service could not be admitted") from exc


def copy_snapshot(target: DatabaseTarget, destination: Path,
                  expected: BackupControl) -> tuple[BackupControl, str]:
    """Pin one source snapshot only for control reads and the SQLite copy."""
    def progress(status: int, remaining: int, total: int) -> None:
        # Connection.backup otherwise retries BUSY/LOCKED indefinitely. A
        # moving copy has no arbitrary time/size limit; lock contention yields
        # to a later ordinary-update opportunity instead of sleeping in a loop.
        if status in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise StorageError("database_busy", DATABASE_BUSY_MESSAGE)

    with control_snapshot(target, expected=expected) as (source, current):
        captured_at = utc_now()
        with closing(configure_connection(connect_traced(destination, access="create", role="backup", file_guard_path=destination))) as copied:
            source.backup(copied, pages=256, progress=progress, sleep=0.01)
    return current, captured_at


def validate_snapshot(path: Path, target: DatabaseTarget, schema_version: int) -> int:
    """The expensive whole-database audit runs with no live DB connection."""
    from task_governance_tool.backup import _validate_publication_source
    with closing(connect_readonly(path)) as connection:
        return _validate_publication_source(connection, target, schema_version)


def commit_inventory(target: DatabaseTarget, expected: BackupControl,
                     generations: tuple[metadata.MigrationBackupMetadata, ...], *,
                     success: bool = False, failure_at: str | None = None) -> BackupControl:
    """CAS the complete bounded inventory in one metadata-only writer.

Physical publication/deletion happens before this call, outside the writer.
The caller holds the backup artifact lock throughout the physical operation.
"""
    generations = metadata.validate_managed_backup_metadata_set(generations)
    if len(generations) > MAX_GENERATIONS_WITH_RESIDUE or (success and not generations):
        raise failure()
    if failure_at is not None:
        validate_utc_timestamp(failure_at, field="backup failure time")
    identity = _database_identity(target)
    if identity != expected.database_identity:
        raise failure()
    validate_operational_journal_state(target.db_path)
    try:
        with closing(connect_existing(target.db_path, timeout=0.1)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = _read_control(connection, target, identity)
                require_same_control(current, expected)
                wanted = {item.generation_id: item for item in generations}
                old = {item.generation_id: item for item in current.generations}
                for key, item in old.items():
                    if key in wanted and wanted[key] != item:
                        raise failure()
                    if key not in wanted:
                        connection.execute(
                            "DELETE FROM managed_backup_generations WHERE generation_id=? AND project_id=?",
                            (key, target.project.project_id),
                        )
                for item in generations:
                    if item.generation_id not in old:
                        metadata._insert_managed_backup_generation(
                            connection, target.project.project_id, item, allow_existing=False,
                        )
                latest = metadata._latest_managed_backup_row(connection, target.project.project_id)
                if success:
                    metadata._write_backup_success(connection, target.project.project_id, generations[-1])
                else:
                    metadata._update_backup_pointer_from_row(
                        connection, target.project.project_id, latest,
                        outcome_code="failed" if failure_at is not None else None,
                        outcome_at=failure_at,
                    )
                result = _read_control(connection, target, identity, admit=False)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
    except sqlite3.Error as exc:
        raise operational_sqlite_error(exc, fallback_message="backup metadata could not be saved") from exc


def record_failure(target: DatabaseTarget, expected: BackupControl, occurred_at: str) -> BackupControl:
    return commit_inventory(target, expected, expected.generations, failure_at=occurred_at)
