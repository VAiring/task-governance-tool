"""Validated project-local SQLite backup publication."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from task_governance_tool.storage import (
    MAX_BACKUP_GENERATIONS,
    MIN_BACKUP_GENERATIONS,
    SCHEMA_VERSION,
    DatabaseTarget,
    MigrationBackupMetadata,
    StorageError,
    configure_connection,
    connect_readonly,
    current_schema_version,
    missing_migration_versions,
    read_project_maintenance,
    record_setup_backup,
    utc_now,
    validate_migration_backup_metadata,
)


_FILENAME = re.compile(
    r"^taskgov-backup-v1_(?P<time>\d{8}T\d{6}Z)_"
    r"(?P<token>[0-9a-f]{32})_r(?P<retention>[1-9]|1[0-9]|20)\.sqlite$"
)
_FAILURE_MESSAGE = "setup backup could not be completed"
_LOCK_FILENAME = "taskgov-backup.lock"


@dataclass(frozen=True)
class _Artifact:
    path: Path
    metadata: MigrationBackupMetadata
    identity: tuple[int, int, int, int]

    @property
    def key(self) -> tuple[str, str]:
        return (self.metadata.published_at, self.metadata.generation_id)


def _failure() -> StorageError:
    return StorageError("setup_backup_failed", _FAILURE_MESSAGE)


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


def _new_metadata(retention: int) -> MigrationBackupMetadata:
    return validate_migration_backup_metadata(
        MigrationBackupMetadata(
            generation_id=f"tg_backup_{secrets.token_hex(16)}",
            published_at=utc_now(),
            publication_retention=_retention(retention),
        )
    )


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
    path = target.db_path.parent / "backups"
    try:
        if create:
            path.mkdir(exist_ok=True)
        elif not path.exists():
            return path
    except OSError as exc:
        raise _failure() from exc
    _directory_identity(path)
    return path


def _same_open_file(path: Path, details: os.stat_result) -> bool:
    try:
        observed = path.lstat()
    except OSError:
        return False
    return (
        not _reparse(observed)
        and stat.S_ISREG(observed.st_mode)
        and int(observed.st_dev) == int(details.st_dev)
        and int(observed.st_ino) == int(details.st_ino)
    )


def _acquire_os_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def managed_backup_lock(target: DatabaseTarget) -> Iterator[None]:
    """Take the shared zero-wait OS lock without holding a SQLite lock."""

    descriptor: int | None = None
    locked = False
    try:
        directory = _directory(target, create=True)
        directory_identity = _directory_identity(directory)
        path = directory / _LOCK_FILENAME
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        details = os.fstat(descriptor)
        if (
            _directory_identity(directory) != directory_identity
            or not stat.S_ISREG(details.st_mode)
            or int(details.st_size) not in {0, 1}
            or not _same_open_file(path, details)
        ):
            raise _failure()
        if details.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        _acquire_os_lock(descriptor)
        locked = True
    except StorageError:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        raise
    except (OSError, ImportError) as exc:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        raise _failure() from exc

    try:
        yield
    finally:
        if descriptor is not None:
            if locked:
                with suppress(OSError):
                    _release_os_lock(descriptor)
            with suppress(OSError):
                os.close(descriptor)


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


def _source_version(target: DatabaseTarget) -> int:
    with closing(connect_readonly(target.db_path)) as connection:
        return _validate_database(connection, target)


def _valid_artifact(
    path: Path,
    target: DatabaseTarget,
    identity: tuple[int, int, int, int],
) -> bool:
    try:
        with closing(connect_readonly(path)) as connection:
            _validate_database(connection, target)
        return _file_identity(path) == identity
    except (OSError, sqlite3.Error, StorageError):
        return False


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
        _validate_database(connection, target, SCHEMA_VERSION)
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


def _copy(target: DatabaseTarget, metadata: MigrationBackupMetadata) -> int:
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        with closing(connect_readonly(target.db_path)) as source:
            source_version = _validate_database(source, target)
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
                _validate_database(destination, target, source_version)

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


def publish_setup_backup(
    target: DatabaseTarget,
    publication_retention: int,
) -> MigrationBackupMetadata:
    """Publish one validated copy, commit v10 metadata, then prune."""
    metadata = _new_metadata(publication_retention)
    source_version = _source_version(target)
    if source_version == SCHEMA_VERSION:
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

    copied_version = _copy(target, metadata)
    artifacts = _discover(target)
    if not any(item.metadata == metadata for item in artifacts):
        raise _failure()
    if copied_version == SCHEMA_VERSION:
        record_setup_backup(target, metadata)
    _prune(
        artifacts,
        metadata.publication_retention,
        metadata.generation_id,
    )
    return metadata
