"""One-byte, zero-wait advisory locks for canonical local artifacts."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from errno import EACCES, EAGAIN, EDEADLK
from pathlib import Path
from typing import Iterator


@dataclass
class ArtifactLockError(Exception):
    contended: bool


def _is_reparse(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _identity(details: os.stat_result) -> tuple[int, int]:
    return (int(details.st_dev), int(details.st_ino))


def _same_open_file(path: Path, details: os.stat_result) -> bool:
    try:
        observed = path.lstat()
    except OSError:
        return False
    return (
        not _is_reparse(observed)
        and stat.S_ISREG(observed.st_mode)
        and _identity(observed) == _identity(details)
    )


def _acquire(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.lockf(
        descriptor,
        fcntl.LOCK_EX | fcntl.LOCK_NB,
        1,
        0,
        os.SEEK_SET,
    )


def _release(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.lockf(
        descriptor,
        fcntl.LOCK_UN,
        1,
        0,
        os.SEEK_SET,
    )


def inspect_existing_artifact_lock(path: Path) -> None:
    """Validate an optional lock artifact without creating or acquiring it."""

    try:
        parent_before = path.parent.lstat()
        if _is_reparse(parent_before) or not stat.S_ISDIR(parent_before.st_mode):
            raise ArtifactLockError(contended=False)
        try:
            details = path.lstat()
        except FileNotFoundError:
            parent_after = path.parent.lstat()
            if (
                _identity(parent_before) != _identity(parent_after)
                or _is_reparse(parent_after)
                or not stat.S_ISDIR(parent_after.st_mode)
            ):
                raise ArtifactLockError(contended=False)
            return
        parent_after = path.parent.lstat()
        if (
            _identity(parent_before) != _identity(parent_after)
            or _is_reparse(parent_after)
            or not stat.S_ISDIR(parent_after.st_mode)
            or _is_reparse(details)
            or not stat.S_ISREG(details.st_mode)
            or int(details.st_size) not in {0, 1}
            or not _same_open_file(path, details)
        ):
            raise ArtifactLockError(contended=False)
    except ArtifactLockError:
        raise
    except OSError as exc:
        raise ArtifactLockError(contended=False) from exc


@contextmanager
def zero_wait_artifact_lock(path: Path) -> Iterator[bytes]:
    """Lock one validated regular file without waiting or stale-file cleanup."""

    descriptor: int | None = None
    locked = False
    lock_bytes = b""
    try:
        parent_before = path.parent.lstat()
        if _is_reparse(parent_before) or not stat.S_ISDIR(parent_before.st_mode):
            raise ArtifactLockError(contended=False)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        details = os.fstat(descriptor)
        parent_after = path.parent.lstat()
        if (
            _identity(parent_before) != _identity(parent_after)
            or _is_reparse(parent_after)
            or not stat.S_ISDIR(parent_after.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or int(details.st_size) not in {0, 1}
            or not _same_open_file(path, details)
        ):
            raise ArtifactLockError(contended=False)
        if details.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        try:
            _acquire(descriptor)
        except OSError as exc:
            if exc.errno in {EACCES, EAGAIN, EDEADLK}:
                raise ArtifactLockError(contended=True) from exc
            raise
        locked = True
        os.lseek(descriptor, 0, os.SEEK_SET)
        lock_bytes = os.read(descriptor, 1)
        current = os.fstat(descriptor)
        if (
            lock_bytes.__len__() != 1
            or int(current.st_size) != 1
            or not _same_open_file(path, current)
        ):
            raise ArtifactLockError(contended=False)
    except ArtifactLockError:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        raise
    except (OSError, ImportError) as exc:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        raise ArtifactLockError(contended=False) from exc

    try:
        yield lock_bytes
    finally:
        if descriptor is not None:
            if locked:
                with suppress(OSError):
                    _release(descriptor)
            with suppress(OSError):
                os.close(descriptor)
