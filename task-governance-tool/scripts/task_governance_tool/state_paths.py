"""Bounded physical-filesystem primitives for canonical taskgov state.

These helpers deliberately expose no recursive deletion and no replacing
rename.  Callers must first reduce an operation to an explicit, validated set
of files and directories.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


STATE_PATH_FAILURE_MESSAGE = "project state could not be changed safely"
_COPY_CHUNK_BYTES = 1024 * 1024


@dataclass
class StatePathError(Exception):
    """One sanitized failure at the generated-state filesystem boundary."""

    code: str = "state_path_invalid"
    message: str = STATE_PATH_FAILURE_MESSAGE

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class DirectoryIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class ValidatedFile:
    path: Path = field(repr=False)
    identity: FileIdentity
    sha256: str


@dataclass(frozen=True)
class ValidatedDirectory:
    path: Path = field(repr=False)
    identity: DirectoryIdentity


def _failure() -> StatePathError:
    return StatePathError()


def _is_reparse(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _file_identity(details: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=int(details.st_dev),
        inode=int(details.st_ino),
        size=int(details.st_size),
        modified_ns=int(details.st_mtime_ns),
    )


def _directory_identity(details: os.stat_result) -> DirectoryIdentity:
    return DirectoryIdentity(
        device=int(details.st_dev),
        inode=int(details.st_ino),
    )


def _lexical_key(path: Path) -> Path:
    if not path.is_absolute():
        raise _failure()
    return Path(os.path.normcase(os.path.normpath(str(path))))


def require_contained(
    path: Path,
    root: Path,
    *,
    allow_root: bool = False,
) -> Path:
    """Return an absolute lexical path only when it is under ``root``."""

    candidate = _lexical_key(Path(path))
    boundary = _lexical_key(Path(root))
    try:
        relative = candidate.relative_to(boundary)
    except ValueError as exc:
        raise _failure() from exc
    if not allow_root and not relative.parts:
        raise _failure()
    return Path(path)


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise _failure() from exc


def _assert_parent_chain(path: Path, root: Path) -> None:
    """Reject a link/reparse or non-directory in the existing parent chain."""

    require_contained(path, root, allow_root=True)
    boundary = Path(root)
    relative = _lexical_key(path).relative_to(_lexical_key(boundary))
    current = boundary
    candidates = [current]
    for component in relative.parts[:-1]:
        current /= component
        candidates.append(current)
    for candidate in candidates:
        details = _lstat(candidate)
        if _is_reparse(details) or not stat.S_ISDIR(details.st_mode):
            raise _failure()


def path_lexically_exists(path: Path) -> bool:
    """Observe the directory entry itself without following a link."""

    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _failure() from exc
    return True


def inspect_physical_directory(
    path: Path,
    *,
    root: Path | None = None,
) -> ValidatedDirectory:
    if root is not None:
        require_contained(path, root, allow_root=True)
        if path != root:
            _assert_parent_chain(path, root)
    details = _lstat(path)
    if _is_reparse(details) or not stat.S_ISDIR(details.st_mode):
        raise _failure()
    return ValidatedDirectory(Path(path), _directory_identity(details))


def inspect_physical_file(
    path: Path,
    *,
    root: Path,
    max_bytes: int | None = None,
) -> tuple[Path, FileIdentity]:
    require_contained(path, root)
    _assert_parent_chain(path, root)
    details = _lstat(path)
    identity = _file_identity(details)
    if (
        _is_reparse(details)
        or not stat.S_ISREG(details.st_mode)
        or identity.size < 0
        or (
            max_bytes is not None
            and (
                isinstance(max_bytes, bool)
                or not isinstance(max_bytes, int)
                or max_bytes < 0
                or identity.size > max_bytes
            )
        )
    ):
        raise _failure()
    return Path(path), identity


def create_physical_directory_exclusive(
    path: Path,
    *,
    root: Path,
) -> ValidatedDirectory:
    require_contained(path, root)
    _assert_parent_chain(path, root)
    try:
        path.mkdir()
    except OSError as exc:
        raise _failure() from exc
    try:
        return inspect_physical_directory(path, root=root)
    except Exception:
        with suppress(OSError):
            path.rmdir()
        raise


def _open_no_follow(path: Path, flags: int, mode: int | None = None) -> int:
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        if mode is None:
            return os.open(path, flags)
        return os.open(path, flags, mode)
    except OSError as exc:
        raise _failure() from exc


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        try:
            written = os.write(descriptor, data[offset:])
        except OSError as exc:
            raise _failure() from exc
        if written <= 0:
            raise _failure()
        offset += written


def _same_file_identity(path: Path, identity: FileIdentity) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    return (
        not _is_reparse(details)
        and stat.S_ISREG(details.st_mode)
        and _file_identity(details) == identity
    )


def _same_file_object(path: Path, identity: FileIdentity) -> bool:
    """Compare only the stable object identity for failed-create cleanup."""

    try:
        details = path.lstat()
    except OSError:
        return False
    observed = _file_identity(details)
    return (
        not _is_reparse(details)
        and stat.S_ISREG(details.st_mode)
        and (observed.device, observed.inode) == (identity.device, identity.inode)
    )


def _same_directory_identity(path: Path, identity: DirectoryIdentity) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    return (
        not _is_reparse(details)
        and stat.S_ISDIR(details.st_mode)
        and _directory_identity(details) == identity
    )


def create_exclusive_durable_file(
    path: Path,
    data: bytes,
    *,
    root: Path,
    max_bytes: int,
) -> ValidatedFile:
    """Create, fsync, and revalidate one absent physical regular file."""

    if (
        not isinstance(data, bytes)
        or not data
        or isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < 1
        or len(data) > max_bytes
    ):
        raise _failure()
    require_contained(path, root)
    _assert_parent_chain(path, root)
    if path_lexically_exists(path):
        raise _failure()

    descriptor: int | None = None
    created_identity: FileIdentity | None = None
    try:
        descriptor = _open_no_follow(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        details = os.fstat(descriptor)
        if _is_reparse(details) or not stat.S_ISREG(details.st_mode):
            raise _failure()
        created_identity = _file_identity(details)
        _write_all(descriptor, data)
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise _failure() from exc
        final_details = os.fstat(descriptor)
        final_identity = _file_identity(final_details)
        if (
            _is_reparse(final_details)
            or not stat.S_ISREG(final_details.st_mode)
            or final_identity.size != len(data)
        ):
            raise _failure()
        os.close(descriptor)
        descriptor = None
        if not _same_file_identity(path, final_identity):
            raise _failure()
        return ValidatedFile(
            path=Path(path),
            identity=final_identity,
            sha256=hashlib.sha256(data).hexdigest(),
        )
    except Exception:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if created_identity is not None and _same_file_object(path, created_identity):
            with suppress(OSError):
                path.unlink()
        raise


def hash_physical_file(
    path: Path,
    *,
    root: Path,
    expected_size: int | None = None,
    max_bytes: int | None = None,
) -> ValidatedFile:
    """Hash one physical file through a no-follow descriptor and revalidate it."""

    _, before = inspect_physical_file(path, root=root, max_bytes=max_bytes)
    if expected_size is not None and (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
        or before.size != expected_size
    ):
        raise _failure()
    descriptor: int | None = None
    digest = hashlib.sha256()
    observed = 0
    try:
        descriptor = _open_no_follow(path, os.O_RDONLY)
        opened = os.fstat(descriptor)
        if _file_identity(opened) != before:
            raise _failure()
        while True:
            try:
                chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            except OSError as exc:
                raise _failure() from exc
            if not chunk:
                break
            observed += len(chunk)
            if observed > before.size:
                raise _failure()
            digest.update(chunk)
        after_open = _file_identity(os.fstat(descriptor))
        if observed != before.size or after_open != before:
            raise _failure()
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    if not _same_file_identity(path, before):
        raise _failure()
    return ValidatedFile(
        path=Path(path),
        identity=before,
        sha256=digest.hexdigest(),
    )


def read_physical_file_bounded(
    path: Path,
    *,
    root: Path,
    max_bytes: int,
) -> tuple[bytes, ValidatedFile]:
    """Read one small physical file exactly and revalidate its directory entry."""

    _, before = inspect_physical_file(path, root=root, max_bytes=max_bytes)
    descriptor: int | None = None
    chunks: list[bytes] = []
    observed = 0
    try:
        descriptor = _open_no_follow(path, os.O_RDONLY)
        if _file_identity(os.fstat(descriptor)) != before:
            raise _failure()
        while True:
            try:
                chunk = os.read(
                    descriptor,
                    min(_COPY_CHUNK_BYTES, max_bytes + 1 - observed),
                )
            except OSError as exc:
                raise _failure() from exc
            if not chunk:
                break
            observed += len(chunk)
            if observed > max_bytes or observed > before.size:
                raise _failure()
            chunks.append(chunk)
        if observed != before.size or _file_identity(os.fstat(descriptor)) != before:
            raise _failure()
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    if not _same_file_identity(path, before):
        raise _failure()
    data = b"".join(chunks)
    return (
        data,
        ValidatedFile(
            path=Path(path),
            identity=before,
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )


def copy_physical_file_exclusive(
    source: ValidatedFile,
    destination: Path,
    *,
    source_root: Path,
    destination_root: Path,
    max_bytes: int,
) -> ValidatedFile:
    """Copy validated bytes to an absent file without preserving path metadata."""

    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < 0
        or source.identity.size > max_bytes
    ):
        raise _failure()
    require_contained(source.path, source_root)
    require_contained(destination, destination_root)
    _assert_parent_chain(source.path, source_root)
    _assert_parent_chain(destination, destination_root)
    if not _same_file_identity(source.path, source.identity):
        raise _failure()
    if path_lexically_exists(destination):
        raise _failure()

    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    destination_identity: FileIdentity | None = None
    digest = hashlib.sha256()
    observed = 0
    try:
        source_descriptor = _open_no_follow(source.path, os.O_RDONLY)
        if _file_identity(os.fstat(source_descriptor)) != source.identity:
            raise _failure()
        destination_descriptor = _open_no_follow(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        created = os.fstat(destination_descriptor)
        if _is_reparse(created) or not stat.S_ISREG(created.st_mode):
            raise _failure()
        destination_identity = _file_identity(created)
        while True:
            try:
                chunk = os.read(source_descriptor, _COPY_CHUNK_BYTES)
            except OSError as exc:
                raise _failure() from exc
            if not chunk:
                break
            observed += len(chunk)
            if observed > max_bytes or observed > source.identity.size:
                raise _failure()
            digest.update(chunk)
            _write_all(destination_descriptor, chunk)
        if (
            observed != source.identity.size
            or digest.hexdigest() != source.sha256
            or _file_identity(os.fstat(source_descriptor)) != source.identity
        ):
            raise _failure()
        try:
            os.fsync(destination_descriptor)
        except OSError as exc:
            raise _failure() from exc
        destination_identity = _file_identity(os.fstat(destination_descriptor))
        if destination_identity.size != observed:
            raise _failure()
        os.close(source_descriptor)
        source_descriptor = None
        os.close(destination_descriptor)
        destination_descriptor = None
        if (
            not _same_file_identity(source.path, source.identity)
            or not _same_file_identity(destination, destination_identity)
        ):
            raise _failure()
        return ValidatedFile(
            path=Path(destination),
            identity=destination_identity,
            sha256=source.sha256,
        )
    except Exception:
        for descriptor in (source_descriptor, destination_descriptor):
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
        if (
            destination_identity is not None
            and _same_file_object(destination, destination_identity)
        ):
            with suppress(OSError):
                destination.unlink()
        raise


def rename_no_replace(
    source: ValidatedFile | ValidatedDirectory,
    destination: Path,
    *,
    root: Path,
) -> ValidatedFile | ValidatedDirectory:
    """Move one validated sibling-tree entry without replacement on Windows."""

    require_contained(source.path, root)
    require_contained(destination, root)
    _assert_parent_chain(source.path, root)
    _assert_parent_chain(destination, root)
    if path_lexically_exists(destination):
        raise _failure()
    if isinstance(source, ValidatedFile):
        if not _same_file_identity(source.path, source.identity):
            raise _failure()
    elif not _same_directory_identity(source.path, source.identity):
        raise _failure()

    # The supported Windows runtime gives os.rename no-replace semantics.
    # POSIX os.rename may replace an existing empty directory or file, so an
    # unverified port must fail closed instead of emulating this with replace.
    if os.name != "nt":
        raise StatePathError(
            code="unsupported_no_replace",
            message=STATE_PATH_FAILURE_MESSAGE,
        )
    try:
        os.rename(source.path, destination)
    except OSError as exc:
        raise _failure() from exc
    if path_lexically_exists(source.path):
        raise _failure()
    if isinstance(source, ValidatedFile):
        if not _same_file_identity(destination, source.identity):
            raise _failure()
        return ValidatedFile(destination, source.identity, source.sha256)
    if not _same_directory_identity(destination, source.identity):
        raise _failure()
    return ValidatedDirectory(destination, source.identity)


def unlink_validated_file(file: ValidatedFile, *, root: Path) -> None:
    require_contained(file.path, root)
    _assert_parent_chain(file.path, root)
    if not _same_file_identity(file.path, file.identity):
        raise _failure()
    try:
        file.path.unlink()
    except OSError as exc:
        raise _failure() from exc


def rmdir_validated_directory(
    directory: ValidatedDirectory,
    *,
    root: Path,
) -> None:
    require_contained(directory.path, root)
    _assert_parent_chain(directory.path, root)
    if not _same_directory_identity(directory.path, directory.identity):
        raise _failure()
    try:
        directory.path.rmdir()
    except OSError as exc:
        raise _failure() from exc


def rmdir_if_empty(path: Path, *, root: Path) -> bool:
    """Remove one physical directory when empty; preserve a non-empty one."""

    if not path_lexically_exists(path):
        return True
    directory = inspect_physical_directory(path, root=root)
    try:
        path.rmdir()
    except OSError as exc:
        if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
            return False
        raise _failure() from exc
    return True


def remove_explicit_files_and_directories(
    *,
    root: Path,
    files: Iterable[ValidatedFile],
    directories_deepest_first: Iterable[ValidatedDirectory],
    max_files: int = 32,
    max_directories: int = 3,
) -> None:
    """Delete exactly the caller-validated entries, never a recursive tree."""

    selected_files = tuple(files)
    selected_directories = tuple(directories_deepest_first)
    if (
        isinstance(max_files, bool)
        or isinstance(max_directories, bool)
        or not isinstance(max_files, int)
        or not isinstance(max_directories, int)
        or max_files < 0
        or max_directories < 0
        or len(selected_files) > max_files
        or len(selected_directories) > max_directories
        or len({str(_lexical_key(item.path)) for item in selected_files})
        != len(selected_files)
        or len({str(_lexical_key(item.path)) for item in selected_directories})
        != len(selected_directories)
    ):
        raise _failure()
    for file in selected_files:
        unlink_validated_file(file, root=root)
    for directory in selected_directories:
        rmdir_validated_directory(directory, root=root)
