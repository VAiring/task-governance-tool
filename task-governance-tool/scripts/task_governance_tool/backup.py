"""Validated project-local SQLite backup publication."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from task_governance_tool.state_resolver import (
        FixedRecoveryObservation,
        ManagedBackupObservation,
    )

from task_governance_tool.artifact_lock import (
    ArtifactLockError,
    zero_wait_artifact_lock,
)
from task_governance_tool.storage import (
    MAX_BACKUP_INTERVAL_MINUTES,
    MAX_BACKUP_GENERATIONS,
    MIN_BACKUP_INTERVAL_MINUTES,
    MIN_BACKUP_GENERATIONS,
    SCHEMA_VERSION,
    DatabaseTarget,
    ManagedBackupRepositoryState,
    MigrationBackupMetadata,
    ProjectMaintenanceState,
    StorageError,
    StoredTaskVerificationError,
    configure_connection,
    connect_readonly,
    current_schema_version,
    delete_managed_backup_generation,
    import_managed_backup_generations,
    missing_migration_versions,
    normalize_managed_backup_pointer,
    read_managed_backup_repository,
    read_project_maintenance,
    record_backup_attempt_outcome,
    record_managed_backup,
    record_setup_backup,
    utc_now,
    validate_current_database,
    validate_migration_backup_metadata,
    validate_stored_task_verification,
    validate_utc_timestamp,
)


_FILENAME = re.compile(
    r"^taskgov-backup-v1_(?P<time>\d{8}T\d{6}Z)_"
    r"(?P<token>[0-9a-f]{32})_r(?P<retention>[1-9]|1[0-9]|20)\.sqlite$"
)
_FILENAME_ALIAS = re.compile(_FILENAME.pattern, re.IGNORECASE)
_FAILURE_MESSAGE = "setup backup could not be completed"
_RESTORE_FAILURE_MESSAGE = "managed backup could not be restored"
_LOCK_FILENAME = "taskgov-backup.lock"


@dataclass(frozen=True)
class _Artifact:
    path: Path
    metadata: MigrationBackupMetadata
    identity: tuple[int, int, int, int]

    @property
    def key(self) -> tuple[str, str]:
        return (self.metadata.published_at, self.metadata.generation_id)


@dataclass(frozen=True)
class ManagedBackupRecoveryArtifact:
    """One fully classified generation bound to a recovery decision."""

    path: Path = field(repr=False)
    metadata: MigrationBackupMetadata
    schema_version: int
    content_valid: bool
    identity: tuple[int, int, int, int] = field(repr=False)

    @property
    def key(self) -> tuple[str, str]:
        return (self.metadata.published_at, self.metadata.generation_id)


@dataclass(frozen=True)
class RoutineBackupResult:
    code: str
    attempted: bool


@dataclass(frozen=True)
class ManagedBackupRecoveryCandidate:
    """One validated same-project generation eligible for setup recovery."""

    path: Path = field(repr=False)
    metadata: MigrationBackupMetadata
    schema_version: int
    identity: tuple[int, int, int, int] = field(repr=False)
    inventory: tuple[ManagedBackupRecoveryArtifact, ...] = field(repr=False)


def _failure() -> StorageError:
    return StorageError("setup_backup_failed", _FAILURE_MESSAGE)


def _restore_failure() -> StorageError:
    return StorageError("setup_restore_failed", _RESTORE_FAILURE_MESSAGE)


def _canonical_rollback_journal_present(target: DatabaseTarget) -> bool:
    """Treat any lexical rollback-journal entry as unsafe recovery residue."""

    path = Path(str(target.db_path) + "-journal")
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _restore_failure() from exc
    return True


def _retention(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_BACKUP_GENERATIONS <= value <= MAX_BACKUP_GENERATIONS
    ):
        raise StorageError(
            "invalid_backup_policy",
            "backup policy is outside the supported range",
        )
    return value


def _new_metadata(
    retention: int,
    *,
    published_at: str | None = None,
    after: MigrationBackupMetadata | None = None,
) -> MigrationBackupMetadata:
    timestamp = validate_utc_timestamp(
        published_at or utc_now(),
        field="setup backup publication time",
    )
    if after is not None and timestamp <= after.published_at:
        try:
            timestamp = (
                datetime.strptime(
                    after.published_at,
                    "%Y-%m-%dT%H:%M:%SZ",
                )
                .replace(tzinfo=UTC)
                + timedelta(seconds=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, ValueError) as exc:
            raise _failure() from exc
    return validate_migration_backup_metadata(
        MigrationBackupMetadata(
            generation_id=f"tg_backup_{secrets.token_hex(16)}",
            published_at=timestamp,
            publication_retention=_retention(retention),
        )
    )


def managed_backup_due(
    maintenance: ProjectMaintenanceState,
    *,
    observed_at: str,
) -> bool:
    """Return the fixed stored-policy due state without writing."""
    timestamp = validate_utc_timestamp(
        observed_at,
        field="backup due observation time",
    )
    if not maintenance.enabled:
        return False
    interval = maintenance.backup_interval_minutes
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int)
        or interval < MIN_BACKUP_INTERVAL_MINUTES
        or interval > MAX_BACKUP_INTERVAL_MINUTES
    ):
        raise StorageError("internal_error", "backup interval is invalid")
    if maintenance.backup_last_outcome_code in {
        "deferred",
        "failed",
    }:
        return True
    last_success = maintenance.backup_last_success_at
    if last_success is None:
        return True
    validate_utc_timestamp(last_success, field="backup success time")
    observed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    )
    succeeded = datetime.strptime(
        last_success,
        "%Y-%m-%dT%H:%M:%SZ",
    ).replace(tzinfo=UTC)
    return (observed - succeeded).total_seconds() >= interval * 60


def _filename(metadata: MigrationBackupMetadata) -> str:
    timestamp = datetime.strptime(metadata.published_at, "%Y-%m-%dT%H:%M:%SZ")
    return (
        f"taskgov-backup-v1_{timestamp.strftime('%Y%m%dT%H%M%SZ')}_"
        f"{metadata.generation_id[10:]}_r{metadata.publication_retention}.sqlite"
    )


def _parse_filename(name: str) -> MigrationBackupMetadata | None:
    match = _FILENAME.fullmatch(name)
    if match is None:
        return None
    try:
        timestamp = datetime.strptime(match["time"], "%Y%m%dT%H%M%SZ")
        if timestamp.strftime("%Y%m%dT%H%M%SZ") != match["time"]:
            return None
        return validate_migration_backup_metadata(
            MigrationBackupMetadata(
                generation_id=f"tg_backup_{match['token']}",
                published_at=timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                publication_retention=int(match["retention"]),
            )
        )
    except (StorageError, ValueError):
        return None


def _reparse(details: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0) & flag
    )


def _file_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        details = path.lstat()
    except OSError:
        return None
    if _reparse(details) or not stat.S_ISREG(details.st_mode):
        return None
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(details.st_size),
        int(details.st_mtime_ns),
    )


def _directory_identity(path: Path) -> tuple[int, int]:
    try:
        details = path.lstat()
    except OSError as exc:
        raise _failure() from exc
    if _reparse(details) or not stat.S_ISDIR(details.st_mode):
        raise _failure()
    return (int(details.st_dev), int(details.st_ino))


def _directory(target: DatabaseTarget, *, create: bool) -> Path:
    path = target.resolved_backups_path
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        elif not path.exists():
            return path
    except OSError as exc:
        raise _failure() from exc
    _directory_identity(path)
    return path


@contextmanager
def managed_backup_lock(target: DatabaseTarget) -> Iterator[bytes]:
    """Take the shared zero-wait OS lock without holding a SQLite lock."""
    try:
        directory = _directory(target, create=True)
        path = directory / _LOCK_FILENAME
        try:
            with zero_wait_artifact_lock(path) as lock_bytes:
                yield lock_bytes
        except ArtifactLockError as exc:
            if exc.contended:
                raise StorageError(
                    "backup_lock_contended",
                    _FAILURE_MESSAGE,
                ) from exc
            raise _failure() from exc
    except StorageError:
        raise
    except OSError as exc:
        raise _failure() from exc


def _validate_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    expected_version: int | None = None,
) -> int:
    try:
        version = current_schema_version(connection)
        if (
            version < 1
            or version > SCHEMA_VERSION
            or (expected_version is not None and version != expected_version)
            or missing_migration_versions(connection, version)
        ):
            raise _failure()
        projects = connection.execute(
            "SELECT project_id FROM project_meta ORDER BY project_id"
        ).fetchall()
        if len(projects) != 1:
            raise _failure()
        if str(projects[0]["project_id"]) != target.project.project_id:
            raise StorageError(
                "project_mismatch",
                "task database belongs to a different project",
            )
        if [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ] != ["ok"]:
            raise _failure()
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise _failure()
        return version
    except sqlite3.Error as exc:
        raise _failure() from exc


def _validate_publication_source(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    expected_version: int | None = None,
) -> int:
    version = _validate_database(connection, target, expected_version)
    if version == SCHEMA_VERSION:
        validate_current_database(connection, target)
    return version


def _source_version(target: DatabaseTarget) -> int:
    with closing(connect_readonly(target.db_path)) as connection:
        return _validate_publication_source(connection, target)


def _valid_artifact(
    path: Path,
    target: DatabaseTarget,
    identity: tuple[int, int, int, int],
) -> bool:
    return _artifact_schema_version(path, target, identity) is not None


def _artifact_schema_version(
    path: Path,
    target: DatabaseTarget,
    identity: tuple[int, int, int, int],
) -> int | None:
    try:
        with closing(connect_readonly(path)) as connection:
            version = _validate_database(connection, target)
        return version if _file_identity(path) == identity else None
    except (OSError, sqlite3.Error, StorageError):
        return None


def _discover(target: DatabaseTarget) -> list[_Artifact]:
    directory = _directory(target, create=False)
    if not directory.exists():
        return []
    try:
        names = sorted(path.name for path in directory.iterdir())
    except OSError as exc:
        raise _failure() from exc
    artifacts: list[_Artifact] = []
    identities: set[str] = set()
    for name in names:
        metadata = _parse_filename(name)
        if metadata is None:
            continue
        path = directory / name
        identity = _file_identity(path)
        if identity is None or not _valid_artifact(path, target, identity):
            continue
        if metadata.generation_id in identities:
            raise _failure()
        identities.add(metadata.generation_id)
        artifacts.append(_Artifact(path, metadata, identity))
    return sorted(artifacts, key=lambda artifact: artifact.key)


def _discover_recovery_artifacts(
    target: DatabaseTarget,
) -> list[ManagedBackupRecoveryArtifact]:
    """Classify recognized recovery artifacts without hiding structural faults."""

    directory = _directory(target, create=False)
    if not directory.exists():
        return []
    try:
        names = sorted(path.name for path in directory.iterdir())
    except OSError as exc:
        raise _restore_failure() from exc
    artifacts: list[ManagedBackupRecoveryArtifact] = []
    generation_ids: set[str] = set()
    recognized_count = 0
    for name in names:
        folded_name = name.casefold()
        if any(
            folded_name.endswith(suffix)
            and _FILENAME_ALIAS.fullmatch(name[: -len(suffix)]) is not None
            for suffix in ("-journal", "-wal", "-shm")
        ):
            raise _restore_failure()
    for name in names:
        metadata = _parse_filename(name)
        if metadata is None:
            continue
        recognized_count += 1
        if recognized_count > MAX_BACKUP_GENERATIONS + 1:
            raise _restore_failure()
        if metadata.generation_id in generation_ids:
            raise _restore_failure()
        generation_ids.add(metadata.generation_id)
        path = directory / name
        identity = _file_identity(path)
        if identity is None:
            raise _restore_failure()
        content_valid = True
        try:
            with closing(connect_readonly(path)) as connection:
                version = _validate_database(connection, target)
                try:
                    validate_stored_task_verification(
                        connection,
                        version,
                        target.project.project_id,
                    )
                except StoredTaskVerificationError:
                    content_valid = False
        except (OSError, sqlite3.Error, StorageError) as exc:
            raise _restore_failure() from exc
        if _file_identity(path) != identity:
            raise _restore_failure()
        artifacts.append(
            ManagedBackupRecoveryArtifact(
                path=path,
                metadata=metadata,
                schema_version=version,
                content_valid=content_valid,
                identity=identity,
            )
        )
    return sorted(artifacts, key=lambda item: item.key)


def discover_managed_backup_metadata(
    target: DatabaseTarget,
) -> tuple[MigrationBackupMetadata, ...]:
    """Return validated canonical artifacts without exposing their paths."""
    return tuple(artifact.metadata for artifact in _discover(target))


def select_managed_backup_for_recovery(
    target: DatabaseTarget,
) -> ManagedBackupRecoveryCandidate | None:
    """Select the newest valid managed generation, or fail closed if none is safe."""

    try:
        if _canonical_rollback_journal_present(target):
            raise _restore_failure()
        artifacts = _discover_recovery_artifacts(target)
        eligible = [item for item in artifacts if item.content_valid]
        if eligible:
            selected = eligible[-1]
            return ManagedBackupRecoveryCandidate(
                path=selected.path,
                metadata=selected.metadata,
                schema_version=selected.schema_version,
                identity=selected.identity,
                inventory=tuple(artifacts),
            )

        directory = _directory(target, create=False)
        if not directory.exists():
            return None
        names = sorted(path.name for path in directory.iterdir())
        if any(_parse_filename(name) is not None for name in names):
            raise _restore_failure()
        return None
    except StorageError as exc:
        if exc.code == "setup_restore_failed":
            raise
        raise _restore_failure() from exc
    except OSError as exc:
        raise _restore_failure() from exc


def _require_recovery_inventory(
    target: DatabaseTarget,
    candidate: ManagedBackupRecoveryCandidate,
) -> tuple[ManagedBackupRecoveryArtifact, ...]:
    """Reclassify and bind the complete generation set before publication."""

    observed = tuple(_discover_recovery_artifacts(target))
    eligible = tuple(item for item in observed if item.content_valid)
    if observed != candidate.inventory or not eligible:
        raise _restore_failure()
    selected = eligible[-1]
    if (
        selected.path != candidate.path
        or selected.metadata != candidate.metadata
        or selected.identity != candidate.identity
        or selected.schema_version != candidate.schema_version
    ):
        raise _restore_failure()
    return observed


def recovery_candidate_matches_observation(
    candidate: ManagedBackupRecoveryCandidate,
    recovery: "FixedRecoveryObservation",
) -> bool:
    """Match a backup-service selection to one deep resolver observation."""

    selected = recovery.selected
    if (
        not selected.recovery_content_valid
        or candidate.path != selected.path
        or candidate.metadata != selected.metadata
        or candidate.schema_version != selected.source_schema_version
        or candidate.identity != selected.identity
        or len(candidate.inventory) != len(recovery.managed_backups)
    ):
        return False
    return all(
        artifact.path == observed.path
        and artifact.metadata == observed.metadata
        and artifact.schema_version == observed.source_schema_version
        and artifact.content_valid == observed.recovery_content_valid
        and artifact.identity == observed.identity
        for artifact, observed in zip(
            candidate.inventory,
            recovery.managed_backups,
            strict=True,
        )
    )


def _require_fixed_recovery_state(
    target: DatabaseTarget,
    candidate: ManagedBackupRecoveryCandidate,
    expected: "FixedRecoveryObservation",
) -> None:
    """Re-run the owning whole-set resolver before fixed publication."""

    if not target.canonical_fixed or target.skill_root is None:
        raise _restore_failure()
    from task_governance_tool.state_resolver import resolve_setup_project_state

    resolution = resolve_setup_project_state(
        skill_root=target.skill_root,
        repo=target.project.canonical_repo,
    )
    resolved = resolution.target
    if (
        resolution.error_code is not None
        or resolution.layout != "fixed_current_v1"
        or resolution.binding != "matching"
        or resolution.project_id != target.project.project_id
        or resolution.fixed_recovery != expected
        or not recovery_candidate_matches_observation(candidate, expected)
        or resolved is None
        or not resolved.canonical_fixed
        or resolved.db_path != target.db_path
        or resolved.skill_root != target.skill_root
        or resolved.backups_path != target.backups_path
        or resolved.viewer_path != target.viewer_path
        or resolved.binding_path_hash != target.binding_path_hash
        or resolved.binding_generation != target.binding_generation
    ):
        raise _restore_failure()


def _prepare_recovered_repository(
    target: DatabaseTarget,
    candidate: ManagedBackupRecoveryCandidate,
    *,
    expected_snapshot: "ManagedBackupObservation | None" = None,
) -> None:
    if expected_snapshot is not None:
        from task_governance_tool.state_resolver import (
            recovery_database_matches_observation,
        )

        if not recovery_database_matches_observation(
            target.db_path,
            expected_snapshot,
        ):
            raise _restore_failure()
    classified = _require_recovery_inventory(target, candidate)
    version = candidate.schema_version
    if version == 10:
        record_setup_backup(target, classified[-1].metadata)
        return
    if version < 11:
        return

    migration_source = version < SCHEMA_VERSION
    artifacts = [
        _Artifact(item.path, item.metadata, item.identity)
        for item in classified
    ]
    artifact_by_id = {
        artifact.metadata.generation_id: artifact for artifact in artifacts
    }
    repository = read_managed_backup_repository(
        target,
        migration_source=migration_source,
    )
    invalid_rows = tuple(
        metadata
        for metadata in repository.generations
        if (
            metadata.generation_id not in artifact_by_id
            or artifact_by_id[metadata.generation_id].metadata != metadata
        )
    )
    observed_at = utc_now()
    for metadata in invalid_rows:
        delete_managed_backup_generation(
            target,
            metadata.generation_id,
            failure_at=observed_at,
            migration_source=migration_source,
        )

    repository = read_managed_backup_repository(
        target,
        migration_source=migration_source,
    )
    row_ids = {
        metadata.generation_id for metadata in repository.generations
    }
    file_only = tuple(
        artifact.metadata
        for artifact in artifacts
        if artifact.metadata.generation_id not in row_ids
    )
    if file_only:
        import_managed_backup_generations(
            target,
            file_only,
            migration_source=migration_source,
        )
    normalize_managed_backup_pointer(
        target,
        migration_source=migration_source,
    )
    repository = read_managed_backup_repository(
        target,
        migration_source=migration_source,
    )
    expected_latest = artifacts[-1].metadata
    if (
        not repository.generations
        or candidate.metadata not in repository.generations
        or repository.generations[-1] != expected_latest
        or repository.maintenance.latest_backup_generation_id
        != expected_latest.generation_id
        or repository.maintenance.backup_last_success_at
        != expected_latest.published_at
        or repository.maintenance.applied_backup_generations
        != expected_latest.publication_retention
    ):
        raise _restore_failure()


def _normalized_recovery_observation(
    candidate: ManagedBackupRecoveryCandidate,
    expected: "ManagedBackupObservation",
) -> "ManagedBackupObservation":
    version = candidate.schema_version
    if version >= 11:
        rows = tuple(item.metadata for item in candidate.inventory)
        pointer = rows[-1]
    elif version == 10:
        rows = ()
        pointer = candidate.inventory[-1].metadata
    else:
        rows = ()
        pointer = None
    return replace(
        expected,
        generation_rows=rows,
        maintenance_pointer=pointer,
    )


def restore_managed_backup(
    target: DatabaseTarget,
    candidate: ManagedBackupRecoveryCandidate,
    *,
    expected_recovery: "FixedRecoveryObservation | None" = None,
) -> int:
    """Atomically recreate a missing canonical DB from one validated generation."""

    temporary: Path | None = None
    descriptor: int | None = None
    try:
        if (
            target.db_path.exists()
            or target.db_path.is_symlink()
            or _canonical_rollback_journal_present(target)
        ):
            raise _restore_failure()
        _require_recovery_inventory(target, candidate)
        if target.canonical_fixed:
            if expected_recovery is None:
                raise _restore_failure()
            _require_fixed_recovery_state(
                target,
                candidate,
                expected_recovery,
            )
        if _parse_filename(candidate.path.name) != candidate.metadata:
            raise _restore_failure()

        parent = target.db_path.parent
        parent_identity = _directory_identity(parent)
        descriptor, name = tempfile.mkstemp(
            prefix=".taskgov-restore-",
            suffix=".tmp",
            dir=parent,
        )
        temporary = Path(name)
        os.close(descriptor)
        descriptor = None

        with closing(connect_readonly(candidate.path)) as source:
            source_version = _validate_database(
                source,
                target,
                candidate.schema_version,
            )
            try:
                validate_stored_task_verification(
                    source,
                    source_version,
                    target.project.project_id,
                )
            except StoredTaskVerificationError as exc:
                raise _restore_failure() from exc
            with closing(
                configure_connection(sqlite3.connect(temporary))
            ) as destination:
                source.backup(destination)
                _validate_database(
                    destination,
                    target,
                    source_version,
                )
                try:
                    validate_stored_task_verification(
                        destination,
                        source_version,
                        target.project.project_id,
                    )
                except StoredTaskVerificationError as exc:
                    raise _restore_failure() from exc

        temporary_target = DatabaseTarget(
            project=target.project,
            db_path=temporary,
            explicit_db=target.explicit_db,
            binding_path_hash=target.binding_path_hash,
            binding_generation=target.binding_generation,
            skill_root=target.skill_root,
            backups_path=target.backups_path,
            viewer_path=target.viewer_path,
            canonical_fixed=False,
        )
        _prepare_recovered_repository(
            temporary_target,
            candidate,
            expected_snapshot=(
                expected_recovery.selected
                if expected_recovery is not None
                else None
            ),
        )
        if expected_recovery is not None:
            from task_governance_tool.state_resolver import (
                recovery_database_matches_observation,
            )

            if not recovery_database_matches_observation(
                temporary,
                _normalized_recovery_observation(
                    candidate,
                    expected_recovery.selected,
                ),
            ):
                raise _restore_failure()
        with closing(connect_readonly(temporary)) as restored:
            _validate_database(
                restored,
                target,
                source_version,
            )
            try:
                validate_stored_task_verification(
                    restored,
                    source_version,
                    target.project.project_id,
                )
            except StoredTaskVerificationError as exc:
                raise _restore_failure() from exc

        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        _require_recovery_inventory(target, candidate)
        if target.canonical_fixed:
            if expected_recovery is None:
                raise _restore_failure()
            _require_fixed_recovery_state(
                target,
                candidate,
                expected_recovery,
            )
        if (
            _directory_identity(parent) != parent_identity
            or _file_identity(temporary) is None
            or target.db_path.exists()
            or target.db_path.is_symlink()
            or _canonical_rollback_journal_present(target)
        ):
            raise _restore_failure()
        os.link(temporary, target.db_path)
        return source_version
    except StorageError as exc:
        if exc.code == "setup_restore_failed":
            raise
        raise _restore_failure() from exc
    except (OSError, sqlite3.Error) as exc:
        raise _restore_failure() from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()
            for suffix in ("-journal", "-wal", "-shm"):
                with suppress(OSError):
                    Path(str(temporary) + suffix).unlink()


def copy_database_snapshot(
    *,
    source_path: Path,
    source_target: DatabaseTarget,
    destination_target: DatabaseTarget,
    expected_source_identity: tuple[int, int, int, int] | None = None,
    require_recovery_content_valid: bool = False,
    expected_recovery_observation: "ManagedBackupObservation | None" = None,
) -> int:
    """Copy one validated SQLite snapshot into an absent private-stage DB."""

    destination = destination_target.db_path
    sidecars = tuple(
        Path(f"{destination}{suffix}") for suffix in ("-journal", "-wal", "-shm")
    )
    if (
        destination.exists()
        or destination.is_symlink()
        or any(os.path.lexists(path) for path in sidecars)
    ):
        raise _failure()
    try:
        source_identity = _file_identity(source_path)
        if source_identity is None or (
            expected_source_identity is not None
            and source_identity != expected_source_identity
        ):
            raise _failure()
        recovery_database_matches_observation = None
        if expected_recovery_observation is not None:
            from task_governance_tool.state_resolver import (
                recovery_database_matches_observation,
            )

            if not recovery_database_matches_observation(
                source_path,
                expected_recovery_observation,
            ):
                raise _failure()
        with closing(connect_readonly(source_path)) as source:
            source_version = _validate_database(source, source_target)
            if require_recovery_content_valid:
                try:
                    validate_stored_task_verification(
                        source,
                        source_version,
                        source_target.project.project_id,
                    )
                except StoredTaskVerificationError as exc:
                    raise _failure() from exc
            with closing(
                configure_connection(sqlite3.connect(destination))
            ) as copied:
                source.backup(copied)
                _validate_database(
                    copied,
                    destination_target,
                    source_version,
                )
                if require_recovery_content_valid:
                    try:
                        validate_stored_task_verification(
                            copied,
                            source_version,
                            source_target.project.project_id,
                        )
                    except StoredTaskVerificationError as exc:
                        raise _failure() from exc
        if (
            expected_recovery_observation is not None
            and recovery_database_matches_observation is not None
            and not recovery_database_matches_observation(
                destination,
                expected_recovery_observation,
            )
        ):
            raise _failure()
        with destination.open("r+b") as stream:
            os.fsync(stream.fileno())
        if (
            _file_identity(source_path) != source_identity
            or _file_identity(destination) is None
            or (
                expected_recovery_observation is not None
                and recovery_database_matches_observation is not None
                and not recovery_database_matches_observation(
                    source_path,
                    expected_recovery_observation,
                )
            )
        ):
            raise _failure()
        return source_version
    except StorageError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise _failure() from exc
    finally:
        if not destination.exists():
            for sidecar in sidecars:
                with suppress(OSError):
                    sidecar.unlink()


def reconcile_private_migration_repository(target: DatabaseTarget) -> None:
    """Converge only a validated private-stage backup repository."""

    version = _source_version(target)
    if version >= 11:
        for _ in range(2):
            if _reconcile_v11(
                target,
                observed_at=utc_now(),
                migration_source=version < SCHEMA_VERSION,
            ):
                return
        raise _failure()
    if version == 10:
        _reconcile_v10(target)
        return
    artifacts = _discover(target)
    if artifacts:
        newest = artifacts[-1]
        _prune(
            artifacts,
            newest.metadata.publication_retention,
            newest.metadata.generation_id,
        )


def _new_publication_metadata(
    target: DatabaseTarget,
    retention: int,
    *,
    published_at: str,
) -> MigrationBackupMetadata:
    artifacts = _discover(target)
    return _new_metadata(
        retention,
        published_at=published_at,
        after=artifacts[-1].metadata if artifacts else None,
    )


def _delete(artifact: _Artifact) -> None:
    if (
        _file_identity(artifact.path) != artifact.identity
        or _parse_filename(artifact.path.name) != artifact.metadata
    ):
        raise _failure()
    try:
        artifact.path.unlink()
    except OSError as exc:
        raise _failure() from exc


def _prune(
    artifacts: list[_Artifact],
    retention: int,
    retain_id: str,
) -> None:
    retention = _retention(retention)
    ordered = sorted(artifacts, key=lambda artifact: artifact.key)
    retained = next(
        (item for item in ordered if item.metadata.generation_id == retain_id),
        None,
    )
    if retained is None:
        raise _failure()
    keep = ordered[-retention:]
    if retained not in keep:
        keep = [retained, *keep[-(retention - 1):]] if retention > 1 else [retained]
    keep_ids = {item.metadata.generation_id for item in keep}
    for artifact in ordered:
        if artifact.metadata.generation_id not in keep_ids:
            _delete(artifact)


def _reconcile_v10(target: DatabaseTarget) -> None:
    artifacts = _discover(target)
    with closing(connect_readonly(target.db_path)) as connection:
        _validate_database(connection, target, 10)
        state = read_project_maintenance(connection, target.project.project_id)
    if state is None:
        raise _failure()

    current: _Artifact | None = None
    if state.latest_backup_generation_id is not None:
        if (
            state.backup_last_success_at is None
            or state.applied_backup_generations is None
        ):
            raise _failure()
        pointer = validate_migration_backup_metadata(
            MigrationBackupMetadata(
                generation_id=state.latest_backup_generation_id,
                published_at=state.backup_last_success_at,
                publication_retention=state.applied_backup_generations,
            )
        )
        current = next(
            (
                artifact
                for artifact in artifacts
                if artifact.metadata.generation_id == pointer.generation_id
            ),
            None,
        )
        if current is not None and current.metadata != pointer:
            raise _failure()
        baseline = (pointer.published_at, pointer.generation_id)
    else:
        baseline = ("", "")

    newer = [artifact for artifact in artifacts if artifact.key > baseline]
    if newer:
        current = newer[-1]
        record_setup_backup(target, current.metadata)
    elif current is None:
        if state.latest_backup_generation_id is not None:
            raise _failure()
        return
    _prune(
        artifacts,
        current.metadata.publication_retention,
        current.metadata.generation_id,
    )


def _reconcile_v11(
    target: DatabaseTarget,
    *,
    observed_at: str,
    migration_source: bool = False,
) -> bool:
    """Repair one bounded v11 generation set before any new publication."""
    timestamp = validate_utc_timestamp(
        observed_at,
        field="backup reconciliation time",
    )
    artifacts = _discover(target)
    artifact_by_id = {
        artifact.metadata.generation_id: artifact for artifact in artifacts
    }
    repository = read_managed_backup_repository(
        target,
        migration_source=migration_source,
    )
    row_ids = {
        metadata.generation_id for metadata in repository.generations
    }
    file_only = tuple(
        artifact.metadata
        for artifact in artifacts
        if artifact.metadata.generation_id not in row_ids
    )
    if file_only:
        import_managed_backup_generations(
            target,
            file_only,
            migration_source=migration_source,
        )
        repository = read_managed_backup_repository(
            target,
            migration_source=migration_source,
        )

    invalid_rows = tuple(
        metadata
        for metadata in repository.generations
        if (
            metadata.generation_id not in artifact_by_id
            or artifact_by_id[metadata.generation_id].metadata != metadata
        )
    )
    if invalid_rows:
        for metadata in invalid_rows:
            delete_managed_backup_generation(
                target,
                metadata.generation_id,
                failure_at=timestamp,
                migration_source=migration_source,
            )
        return False

    if not repository.generations:
        if repository.maintenance.latest_backup_generation_id is not None:
            normalize_managed_backup_pointer(
                target,
                migration_source=migration_source,
            )
        return True
    expected_latest = repository.generations[-1]
    latest_id = repository.maintenance.latest_backup_generation_id
    if (
        latest_id != expected_latest.generation_id
        or repository.maintenance.backup_last_success_at
        != expected_latest.published_at
        or repository.maintenance.applied_backup_generations
        != expected_latest.publication_retention
    ):
        normalize_managed_backup_pointer(
            target,
            migration_source=migration_source,
        )
        repository = read_managed_backup_repository(
            target,
            migration_source=migration_source,
        )
        latest_id = repository.maintenance.latest_backup_generation_id
    retention = repository.maintenance.applied_backup_generations
    if retention is None:
        raise _failure()
    ordered = list(repository.generations)
    keep_ids = {
        metadata.generation_id for metadata in ordered[-_retention(retention):]
    }
    if latest_id is None or latest_id not in keep_ids:
        raise _failure()
    for metadata in ordered:
        if metadata.generation_id in keep_ids:
            continue
        artifact = artifact_by_id.get(metadata.generation_id)
        if artifact is None or artifact.metadata != metadata:
            raise _failure()
        _delete(artifact)
        delete_managed_backup_generation(
            target,
            metadata.generation_id,
            migration_source=migration_source,
        )
    return True


def _reconciliation_needed(
    target: DatabaseTarget,
    repository: ManagedBackupRepositoryState,
) -> bool:
    """Detect crash residue cheaply; the locked reconciler performs validation."""
    directory = _directory(target, create=False)
    observed: list[MigrationBackupMetadata] = []
    if directory.exists():
        try:
            paths = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError:
            return True
        for path in paths:
            metadata = _parse_filename(path.name)
            if metadata is not None and _file_identity(path) is not None:
                observed.append(metadata)
    observed.sort(
        key=lambda metadata: (
            metadata.published_at,
            metadata.generation_id,
        )
    )
    generations = repository.generations
    if tuple(observed) != generations:
        return True
    maintenance = repository.maintenance
    if not generations:
        return maintenance.latest_backup_generation_id is not None
    latest = generations[-1]
    return (
        maintenance.latest_backup_generation_id != latest.generation_id
        or maintenance.backup_last_success_at != latest.published_at
        or maintenance.applied_backup_generations
        != latest.publication_retention
        or len(generations) > latest.publication_retention
    )


def _copy(target: DatabaseTarget, metadata: MigrationBackupMetadata) -> int:
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        with closing(connect_readonly(target.db_path)) as source:
            source_version = _validate_publication_source(source, target)
            directory = _directory(target, create=True)
            directory_identity = _directory_identity(directory)
            final = directory / _filename(metadata)
            if final.exists() or final.is_symlink():
                raise _failure()
            descriptor, name = tempfile.mkstemp(
                prefix=".taskgov-backup-",
                suffix=".tmp",
                dir=directory,
            )
            temporary = Path(name)
            os.close(descriptor)
            descriptor = None
            with closing(
                configure_connection(sqlite3.connect(temporary))
            ) as destination:
                source.backup(destination)
                _validate_publication_source(
                    destination,
                    target,
                    source_version,
                )

        if (
            _directory_identity(directory) != directory_identity
            or temporary is None
            or _file_identity(temporary) is None
            or final.exists()
            or final.is_symlink()
        ):
            raise _failure()
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        with closing(connect_readonly(target.db_path)) as current:
            _validate_publication_source(current, target, source_version)
            os.replace(temporary, final)
        temporary = None
        return source_version
    except (OSError, sqlite3.Error) as exc:
        raise _failure() from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()
            for suffix in ("-journal", "-wal", "-shm"):
                with suppress(OSError):
                    Path(str(temporary) + suffix).unlink()


def _record_attempt_outcome(
    target: DatabaseTarget,
    *,
    code: str,
    occurred_at: str,
) -> None:
    with suppress(Exception):
        record_backup_attempt_outcome(
            target,
            code=code,
            occurred_at=occurred_at,
        )


def _validate_canonical_artifact_set(
    target: DatabaseTarget,
    *,
    allow_relocation: bool = False,
) -> None:
    if not target.canonical_fixed:
        return
    if target.skill_root is None:
        raise _failure()
    from task_governance_tool.state_resolver import (
        resolve_setup_project_state,
    )

    resolution = resolve_setup_project_state(
        skill_root=target.skill_root,
        repo=target.project.canonical_repo,
    )
    resolved = resolution.target
    if (
        resolution.error_code is not None
        or resolution.layout != "fixed_current_v1"
        or resolution.binding
        not in (
            {"matching", "relocation_required"}
            if allow_relocation
            else {"matching"}
        )
        or resolution.fixed_recovery is not None
        or resolved is None
        or not resolved.canonical_fixed
        or resolved.db_path != target.db_path
        or resolved.skill_root != target.skill_root
        or resolved.backups_path != target.backups_path
        or resolved.viewer_path != target.viewer_path
        or resolved.project.project_id != target.project.project_id
        or resolved.binding_path_hash != target.binding_path_hash
        or resolved.binding_generation != target.binding_generation
    ):
        raise _failure()


def run_routine_backup(
    target: DatabaseTarget,
    *,
    observed_at: str | None = None,
) -> RoutineBackupResult:
    """Run at most one due managed publication after a business commit."""
    timestamp = validate_utc_timestamp(
        observed_at or utc_now(),
        field="routine backup observation time",
    )
    repository = read_managed_backup_repository(target)
    if not repository.maintenance.enabled:
        return RoutineBackupResult(code="not_opted_in", attempted=False)
    due = managed_backup_due(
        repository.maintenance,
        observed_at=timestamp,
    )
    if not due and not _reconciliation_needed(target, repository):
        return RoutineBackupResult(code="current", attempted=False)

    try:
        with managed_backup_lock(target):
            _validate_canonical_artifact_set(target)
            if not _reconcile_v11(target, observed_at=timestamp):
                _record_attempt_outcome(
                    target,
                    code="failed",
                    occurred_at=timestamp,
                )
                return RoutineBackupResult(code="failed", attempted=True)
            repository = read_managed_backup_repository(target)
            if not managed_backup_due(
                repository.maintenance,
                observed_at=timestamp,
            ):
                return RoutineBackupResult(code="current", attempted=True)
            retention = repository.maintenance.backup_generations
            if retention is None:
                raise _failure()
            metadata = _new_publication_metadata(
                target,
                retention,
                published_at=timestamp,
            )
            _copy(target, metadata)
            artifacts = _discover(target)
            if not any(artifact.metadata == metadata for artifact in artifacts):
                raise _failure()
            record_managed_backup(target, metadata)
            if not _reconcile_v11(target, observed_at=timestamp):
                raise _failure()
            return RoutineBackupResult(code="succeeded", attempted=True)
    except StorageError as exc:
        code = "deferred" if exc.code == "backup_lock_contended" else "failed"
        _record_attempt_outcome(
            target,
            code=code,
            occurred_at=timestamp,
        )
        return RoutineBackupResult(code=code, attempted=True)
    except Exception:
        _record_attempt_outcome(
            target,
            code="failed",
            occurred_at=timestamp,
        )
        return RoutineBackupResult(code="failed", attempted=True)


def publish_setup_backup(
    target: DatabaseTarget,
    publication_retention: int,
) -> MigrationBackupMetadata:
    """Publish one validated copy, commit v10 metadata, then prune."""
    observed_at = utc_now()
    _validate_canonical_artifact_set(
        target,
        allow_relocation=True,
    )
    source_version = _source_version(target)
    if source_version >= 11:
        if not _reconcile_v11(
            target,
            observed_at=observed_at,
            migration_source=True,
        ):
            raise _failure()
    elif source_version == 10:
        _reconcile_v10(target)
    else:
        existing = _discover(target)
        if (
            existing
            and len(existing)
            > existing[-1].metadata.publication_retention
        ):
            previous = existing[-1]
            _prune(
                existing,
                previous.metadata.publication_retention,
                previous.metadata.generation_id,
            )

    metadata = _new_publication_metadata(
        target,
        publication_retention,
        published_at=observed_at,
    )
    copied_version = _copy(target, metadata)
    artifacts = _discover(target)
    if not any(item.metadata == metadata for item in artifacts):
        raise _failure()
    if copied_version >= 11:
        record_managed_backup(
            target,
            metadata,
            migration_source=True,
        )
        if not _reconcile_v11(
            target,
            observed_at=metadata.published_at,
            migration_source=True,
        ):
            raise _failure()
    else:
        if copied_version == 10:
            record_setup_backup(target, metadata)
        _prune(
            artifacts,
            metadata.publication_retention,
            metadata.generation_id,
        )
    return metadata
