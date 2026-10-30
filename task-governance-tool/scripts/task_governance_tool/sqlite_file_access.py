"""Keep application-owned raw readers from cancelling POSIX SQLite locks.

All product SQLite openers supply their physical path to ``connect_guarded``.
Open/close registration and raw descriptor ownership share one short mutex;
SQL execution and file reads do not hold it. Entries contain no content or
validation proof and disappear after their last connection and reader close.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path
import sqlite3
import threading
import weakref


_POSIX = os.name == "posix"
_mutex = threading.RLock()


@dataclass
class _File:
    connections: int = 0
    readers: int = 0
    descriptor: int | None = None
    extra_descriptors: list[int] = field(default_factory=list)


_files: dict[tuple[int, int], _File] = {}
_unidentified: list[int] = []


def _identity(details):
    return details.st_dev, details.st_ino


def _retire(key, entry):
    if entry.connections or entry.readers:
        return
    # Remove before close: a failed close must never be retried against a
    # descriptor number the OS might already have reused.
    del _files[key]
    descriptors = entry.extra_descriptors
    if entry.descriptor is not None:
        descriptors.append(entry.descriptor)
    if not any(item.connections for item in _files.values()):
        descriptors.extend(_unidentified)
        _unidentified.clear()
    first_error = None
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError as error:
            first_error = first_error or error
    if first_error is not None:
        raise first_error


class GuardedConnection(sqlite3.Connection):
    """Native transaction semantics, with explicit physical-file close ownership."""

    def _remember_cursor(self, cursor):
        if getattr(self, "_file_guard_key", None) is not None:
            self._file_guard_cursors.add(cursor)
        return cursor

    def cursor(self, *args, **kwargs):
        return self._remember_cursor(super().cursor(*args, **kwargs))

    def execute(self, *args, **kwargs):
        return self._remember_cursor(super().execute(*args, **kwargs))

    def executemany(self, *args, **kwargs):
        return self._remember_cursor(super().executemany(*args, **kwargs))

    def executescript(self, *args, **kwargs):
        return self._remember_cursor(super().executescript(*args, **kwargs))

    def close(self):
        with _mutex:
            key = getattr(self, "_file_guard_key", None)
            if key is not None:
                # Enforce native thread/open checks before touching cursors.
                sqlite3.Connection.cursor(self).close()
                # sqlite3_close_v2 can otherwise leave a zombie native reader
                # behind an unconsumed cursor, even after Connection.close().
                for cursor in list(self._file_guard_cursors):
                    sqlite3.Cursor.close(cursor)
            super().close()  # Failed/wrong-thread close retains protection.
            if key is not None:
                self._file_guard_key = None
                entry = _files[key]
                entry.connections -= 1
                _retire(key, entry)

    def __del__(self):
        # Supported owners explicitly close in finally. Also release ordinary
        # same-thread abandonment. If native close fails, retain descriptors
        # rather than risk releasing another connection's process-wide locks.
        try:
            if getattr(self, "_file_guard_key", None) is not None:
                self.close()
        except BaseException:
            pass


def connect_guarded(path: Path | None, opener):
    """Run the existing opener once, without changing its transaction settings."""
    if not _POSIX or path is None:
        return opener(False)
    with _mutex:
        try:
            before = _identity(path.stat())
        except FileNotFoundError:
            before = None
        connection = opener(True)
        try:
            after = _identity(path.stat())
            if before is not None and after != before:
                raise OSError("database file changed during open")
            entry = _files.setdefault(after, _File())
            connection._file_guard_cursors = weakref.WeakSet()
            connection._file_guard_key = after
            entry.connections += 1
        except BaseException:
            connection.close()
            raise
        return connection


class _Reader:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self._offset = 0

    def read(self, count):
        if _POSIX:
            result = os.pread(self.descriptor, count, self._offset)
        else:
            result = os.read(self.descriptor, count)
        self._offset += len(result)
        return result


@contextmanager
def file_reader(path: Path):
    """Read fresh bytes, sharing one protected descriptor per physical inode.

    A reader has its own offset. Callers still own physical-path, identity,
    size and content validation; this helper grants no admission authority.
    """
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not _POSIX:
        descriptor = os.open(path, flags)
        try:
            yield _Reader(descriptor)
        finally:
            os.close(descriptor)
        return
    with _mutex:
        key = _identity(path.stat())
        entry = _files.setdefault(key, _File())
        if entry.descriptor is None:
            descriptor = None
            try:
                descriptor = os.open(path, flags)
                opened_key = _identity(os.fstat(descriptor))
            except BaseException:
                if descriptor is not None:
                    _unidentified.append(descriptor)
                _retire(key, entry)
                raise
            if opened_key != key:
                # Retire against the actual inode, including a replaced path
                # that now names another locally open database.
                actual = _files.setdefault(opened_key, _File())
                if actual.descriptor is not None:
                    actual.extra_descriptors.append(descriptor)
                else:
                    actual.descriptor = descriptor
                _retire(opened_key, actual)
                _retire(key, entry)
                raise OSError("database file changed during read")
            entry.descriptor = descriptor
        entry.readers += 1
        descriptor = entry.descriptor
    try:
        yield _Reader(descriptor)
    finally:
        with _mutex:
            entry.readers -= 1
            _retire(key, entry)
