"""SQLite boundaries for explicit protection; never write a live repository.

Live readers exist only during copying. Full validation and recovery work use
private databases. The ordinary managed-backup ledger is never updated here.
"""

from contextlib import closing, contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3

from . import async_backup_repository as capture
from . import backup
from .backup_metadata_repository import MigrationBackupMetadata, validate_migration_backup_metadata
from .sqlite_connection import StorageError, connect_readonly, configure_connection
from .sqlite_transaction_trace import connect_traced
from .state_paths import inspect_physical_file
from .storage import DatabaseTarget, utc_now
from .usage_evidence_service import repository_for
from .viewer_projection_repository import read_state


def failure():
    return StorageError("protection_failed", "database protection could not be completed")


def source_identity(path: Path):
    _, identity = inspect_physical_file(path, root=path.parent)
    if path.lstat().st_nlink != 1:
        raise failure()
    return identity.device, identity.inode


def copy_core(target, destination):
    control = capture.read_control(target)
    _, started = capture.copy_snapshot(target, destination, control)
    finished = utc_now()
    capture.validate_snapshot(destination, target, control.schema_version)
    private = replace(target, db_path=destination, canonical_fixed=False)
    observed = capture.read_control(private)
    if (observed.binding != control.binding or observed.schema_version != control.schema_version):
        raise failure()
    # Unlike routine publication, protection cannot invent a later clock value.
    published_at = utc_now()
    metadata = validate_migration_backup_metadata(MigrationBackupMetadata(
        "tg_backup_" + secrets.token_hex(16), published_at,
        observed.maintenance.backup_generations or 1))
    if (metadata.generation_id in {item.generation_id for item in observed.generations}
            or any((item.published_at, item.generation_id) >= (metadata.published_at, metadata.generation_id)
                   for item in observed.generations)):
        raise failure()
    return metadata, control.schema_version, started, finished, control.database_identity


def copy_usage(target, destination):
    repository = repository_for(target)
    identity = source_identity(repository.path)
    def progress(status, remaining, total):
        if status in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise StorageError("database_busy", "database is busy")
    started = utc_now()
    with repository.connection() as source:
        with closing(configure_connection(connect_traced(
                destination, access="create", role="backup", file_guard_path=destination))) as copied:
            source.backup(copied, pages=256, progress=progress, sleep=0.01)
    finished = utc_now()
    if source_identity(repository.path) != identity:
        raise failure()
    private = repository_for(replace(target, usage_database=destination))
    with private.connection() as connection:
        if ([row[0] for row in connection.execute("PRAGMA quick_check")] != ["ok"]
                or connection.execute("PRAGMA foreign_key_check").fetchone()):
            raise failure()
    return private.migrations[-1][0], started, finished, identity


def fsync_copy(path):
    # Every SQLite owner has closed before this raw descriptor opens (POSIX).
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def business_digest(path):
    """Compare all rows except the exact repository-owned recovery delta.

Raw values and SQL never leave this function. Sorted row hashes preserve
duplicates and avoid depending on insertion order or storing business text.
"""
    omitted = {
        "project_maintenance": {"applied_backup_generations", "backup_last_success_at",
            "backup_last_outcome_code", "backup_last_outcome_at", "latest_backup_generation_id"},
        "viewer_maintenance_state": {"source_generation"},
    }
    result = hashlib.sha256()
    with closing(connect_readonly(path)) as connection:
        for table, in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            if table in {"managed_backup_generations", "viewer_projection_state"}:
                continue
            columns = [row[1] for row in connection.execute("PRAGMA table_info(" + _quote(table) + ")")
                       if row[1] not in omitted.get(table, ())]
            query = "SELECT " + ",".join(map(_quote, columns)) + " FROM " + _quote(table)
            rows = []
            for row in connection.execute(query):
                typed = [(type(value).__name__, value.hex() if isinstance(value, bytes) else value) for value in row]
                rows.append(hashlib.sha256(json.dumps(typed, ensure_ascii=True, allow_nan=False).encode()).digest())
            result.update(json.dumps([table, columns, len(rows)]).encode())
            for digest in sorted(rows):
                result.update(digest)
    return result.hexdigest()


def rehearse(target, inbox, usage_path=None):
    """Use the actual managed recovery selector/normalizer in an isolated inbox."""
    restored = DatabaseTarget(project=target.project, db_path=inbox / "taskgov.sqlite",
        explicit_db=True, binding_path_hash=target.binding_path_hash,
        binding_generation=target.binding_generation, skill_root=target.skill_root,
        backups_path=inbox / "backups", usage_database=inbox / "taskgov-usage.sqlite")
    candidate = backup.select_managed_backup_for_recovery(restored)
    if candidate is None:
        raise failure()
    before = business_digest(candidate.path)
    with closing(connect_readonly(candidate.path)) as connection:
        original = read_state(connection, target.project.project_id)
    backup.restore_managed_backup(restored, candidate)
    if business_digest(restored.db_path) != before:
        raise failure()
    control = capture.read_control(restored)
    state = control.maintenance
    if (control.generations != (candidate.metadata,)
            or (state.latest_backup_generation_id, state.backup_last_success_at,
                state.applied_backup_generations, state.backup_last_outcome_code, state.backup_last_outcome_at)
            != (candidate.metadata.generation_id, candidate.metadata.published_at,
                candidate.metadata.publication_retention, "succeeded", candidate.metadata.published_at)):
        raise failure()
    with closing(connect_readonly(restored.db_path)) as connection:
        projection = read_state(connection, target.project.project_id)
    expected = replace(original, database_epoch=projection.database_epoch,
        source_generation=original.source_generation + 1,
        invalidated_generation=original.source_generation + 1, cache_generation=None,
        cache_format_version=None, projection_version=None, cache_sha256=None, cache_bytes=None,
        html_generation=None, html_sha256=None, html_bytes=None)
    if projection != expected or projection.database_epoch == original.database_epoch:
        raise failure()
    if usage_path is not None:
        from .state_paths import copy_physical_file_exclusive, hash_physical_file
        sealed = hash_physical_file(usage_path, root=usage_path.parent)
        copy_physical_file_exclusive(sealed, restored.resolved_usage_database,
            source_root=inbox.parent, destination_root=inbox.parent, max_bytes=sealed.identity.size)
        repository = repository_for(restored)
        @contextmanager
        def core_reader():
            from .storage import connect_initialized_readonly
            with closing(connect_initialized_readonly(restored)) as connection:
                yield connection
        # Real reconciliation uses exact anchors. Missing restored events or
        # executions remain unknown, never assigned to a different execution.
        repository.refresh(core_reader)
    return {"core": "passed", "business_records": "equal", "normalization": "managed_repository_and_viewer",
            "usage": "reconciled" if usage_path is not None else "absent", "host_effects": "excluded"}
