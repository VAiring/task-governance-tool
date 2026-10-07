"""Source-only review-wait persistence for explicitly injected development paths.

This is not an installed runtime, Setup operation, or public CLI. A writer
lease can span a host operation, but each SQLite transaction is short. Callers
must save the controller's pending intent before an external effect and must
reconcile that intent after interruption; reopening never authorizes replay.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from task_governance_tool.review_wait_runtime.review_wait_controller import ControllerError, ReviewWaitController


MAX_SNAPSHOT_BYTES = 131_072
MAX_DATABASE_BYTES = 2_097_152
_SCHEMA_VERSION = 1
_MIGRATION = "review-wait-source-v1"
_SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 1), migration_name TEXT NOT NULL CHECK(migration_name = 'review-wait-source-v1'))",
    "wait_snapshot": "CREATE TABLE wait_snapshot (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), revision INTEGER NOT NULL CHECK(revision >= 1), payload TEXT NOT NULL CHECK(length(payload) <= 131072))",
}


class RepositoryError(Exception):
    """A fixed development diagnostic; never contains rejected data or paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StoredWait:
    revision: int
    controller: ReviewWaitController


def _identity(details: os.stat_result) -> tuple[int, int]:
    return details.st_dev, details.st_ino


def _physical(details: os.stat_result, *, directory: bool = False) -> bool:
    reparse = getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return not reparse and not stat.S_ISLNK(details.st_mode) and (
        stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode) and details.st_nlink == 1
    )


def _path(value: Path) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or not path.name:
            raise RepositoryError("state_path_invalid")
        for parent in reversed(path.parents):
            if not _physical(parent.lstat(), directory=True):
                raise RepositoryError("state_path_invalid")
        return path
    except (OSError, TypeError, ValueError):
        raise RepositoryError("state_path_invalid") from None


def _file(path: Path, *, maximum: int = MAX_DATABASE_BYTES) -> os.stat_result:
    try:
        details = path.lstat()
        if not _physical(details) or not 0 <= details.st_size <= maximum:
            raise RepositoryError("state_unreadable")
        return details
    except OSError:
        raise RepositoryError("state_unreadable") from None


def _missing(path: Path) -> bool:
    try:
        path.lstat()
        return False
    except FileNotFoundError:
        return True
    except OSError:
        raise RepositoryError("state_unreadable") from None


def _decode(payload: str) -> ReviewWaitController:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError

    try:
        if type(payload) is not str or len(payload.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise ValueError
        value = json.loads(payload, object_pairs_hook=pairs, parse_constant=reject_constant)
        controller = ReviewWaitController.from_snapshot(value)
        if _encode(controller) != payload:
            raise ValueError
        return controller
    except (ControllerError, ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        raise RepositoryError("state_unreadable") from None


def _encode(controller: ReviewWaitController) -> str:
    try:
        if type(controller) is not ReviewWaitController:
            raise ValueError
        value = controller.to_snapshot()
        validated = ReviewWaitController.from_snapshot(value)
        payload = json.dumps(validated.to_snapshot(), ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(payload.encode("ascii")) > MAX_SNAPSHOT_BYTES:
            raise ValueError
        return payload
    except (ControllerError, ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        raise RepositoryError("invalid_snapshot") from None


def _lock(descriptor: int, *, release: bool = False) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)


def _forward(previous: ReviewWaitController, candidate: ReviewWaitController) -> None:
    """Reject snapshot rollback and effects lacking a previously persisted intent."""
    old, new = previous.to_snapshot(), candidate.to_snapshot()
    identity = ("timer_id", "parent_thread_id", "identity_digest")
    if any(old["reservation"][key] != new["reservation"][key] for key in identity):
        raise RepositoryError("binding_mismatch")
    old_terminal = {item["reviewer_id"]: item for item in old["terminal"]}
    new_terminal = {item["reviewer_id"]: item for item in new["terminal"]}
    valid = (
        (old["phase"] != "closed" or new["phase"] == "closed")
        and all(new_terminal.get(key) == item for key, item in old_terminal.items())
        and old["arm"] <= new["arm"] <= old["arm"] + 1
        and old["operation_sequence"] <= new["operation_sequence"] <= old["operation_sequence"] + 1
    )
    if old["pending"] is not None:
        # Settlement is a separate durable boundary. A valid standalone
        # snapshot cannot replace an unresolved effect with the next intent.
        valid = valid and new["operation_sequence"] == old["operation_sequence"]
        if new["pending"] is not None:
            valid = valid and (
                new["pending"] == old["pending"]
                and new["arm"] == old["arm"]
                and new["reservation"] == old["reservation"]
                and new["appointment"] == old["appointment"]
                and (not old["outcome_unknown"] or new["outcome_unknown"])
            )
    if new["operation_sequence"] != old["operation_sequence"]:
        valid = valid and new["pending"] is not None
    if new["arm"] == old["arm"]:
        valid = valid and new["appointment"] == old["appointment"]
        valid = valid and all(not old[key] or new[key] for key in ("shortening_attempted", "shortened"))
    else:
        valid = valid and old["pending"] is not None and old["pending"]["kind"] in ("arm", "rearm")
        if valid:
            valid = new["appointment"] == old["pending"]["appointment"] and new["reservation"] == old["pending"]["after"]
    if new["reservation"] != old["reservation"]:
        valid = valid and old["pending"] is not None and new["reservation"] == old["pending"]["after"]
    if not valid:
        raise RepositoryError("state_transition_invalid")


class ReviewWaitRepository:
    """One development wait per explicit file; ordinary runtime must use its resolver."""

    def __init__(self, path: Path):
        self.path = _path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @classmethod
    def create(cls, path: Path, controller: ReviewWaitController) -> ReviewWaitRepository:
        payload = _encode(controller)
        repo = cls(path)
        if not _missing(repo.path):
            raise RepositoryError("state_exists")
        with repo._lease(create_lock=True):
            descriptor = None
            try:
                descriptor = os.open(repo.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                raise RepositoryError("state_exists") from None
            except OSError:
                raise RepositoryError("state_unreadable") from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            with repo._connection(write=True) as connection:
                for statement in _SCHEMA.values():
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
                connection.execute("INSERT INTO schema_history VALUES (?, ?)", (_SCHEMA_VERSION, _MIGRATION))
                connection.execute("INSERT INTO wait_snapshot VALUES (1, 1, ?)", (payload,))
        return repo

    @classmethod
    def open_existing(cls, path: Path) -> ReviewWaitRepository:
        repo = cls(path)
        repo.read()
        return repo

    def _inspect(self) -> os.stat_result:
        _path(self.path)
        details = _file(self.path)
        lock = _file(self.lock_path, maximum=1)
        if lock.st_size != 1:
            raise RepositoryError("state_unreadable")
        for suffix in ("-wal", "-shm"):
            if not _missing(Path(str(self.path) + suffix)):
                raise RepositoryError("unsupported_journal_mode")
        journal = Path(str(self.path) + "-journal")
        if not _missing(journal):
            _file(journal)
        try:
            with self.path.open("rb") as stream:
                if _identity(os.fstat(stream.fileno())) != _identity(details):
                    raise RepositoryError("state_unreadable")
                header = stream.read(20)
            if header[:16] == b"SQLite format 3\x00" and len(header) == 20 and (header[18] == 2 or header[19] == 2):
                raise RepositoryError("unsupported_journal_mode")
        except OSError:
            raise RepositoryError("state_unreadable") from None
        return details

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        before = self._inspect()
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=0, isolation_level=None)
            connection.execute("PRAGMA trusted_schema = OFF")
            if not write:
                connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if _identity(self._inspect()) != _identity(before):
                raise RepositoryError("state_unreadable")
            yield connection
            if _identity(self._inspect()) != _identity(before):
                raise RepositoryError("state_unreadable")
            connection.commit()
        except sqlite3.Error:
            raise RepositoryError("state_unreadable") from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _load(connection: sqlite3.Connection) -> StoredWait:
        objects = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        expected = [("table", name, name, sql) for name, sql in sorted(_SCHEMA.items())]
        if objects != expected or connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
            raise RepositoryError("state_unreadable")
        if connection.execute("SELECT version, migration_name FROM schema_history").fetchall() != [(_SCHEMA_VERSION, _MIGRATION)]:
            raise RepositoryError("state_unreadable")
        rows = connection.execute("SELECT singleton, revision, payload FROM wait_snapshot").fetchall()
        if len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int or not 1 <= rows[0][1] < 2**63:
            raise RepositoryError("state_unreadable")
        return StoredWait(rows[0][1], _decode(rows[0][2]))

    def read(self) -> StoredWait:
        with self._connection() as connection:
            return self._load(connection)

    @contextmanager
    def _lease(self, *, create_lock: bool = False) -> Iterator[WriterLease]:
        _path(self.path)
        descriptor = None
        locked = False
        lease = None
        try:
            if create_lock and _missing(self.lock_path):
                try:
                    descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    os.write(descriptor, b"\x00")
                    os.fsync(descriptor)
                except FileExistsError:
                    descriptor = None
            if descriptor is None:
                _file(self.lock_path, maximum=1)
                descriptor = os.open(self.lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            details = os.fstat(descriptor)
            if not _physical(details) or details.st_size != 1 or _identity(_file(self.lock_path, maximum=1)) != _identity(details):
                raise RepositoryError("state_unreadable")
            try:
                _lock(descriptor)
            except OSError:
                raise RepositoryError("writer_busy") from None
            locked = True
            lease = WriterLease(self, descriptor, _identity(details))
            yield lease
        except OSError:
            raise RepositoryError("state_unreadable") from None
        finally:
            if lease is not None:
                lease._active = False
            if descriptor is not None:
                cleanup_failed = False
                try:
                    if locked:
                        _lock(descriptor, release=True)
                except OSError:
                    cleanup_failed = True
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failed = True
                if cleanup_failed:
                    raise RepositoryError("state_unreadable") from None

    @contextmanager
    def writer(self) -> Iterator[WriterLease]:
        self._inspect()
        with self._lease() as lease:
            lease._database_identity = _identity(self._inspect())
            yield lease


class WriterLease:
    """An exclusive writer permit, not a SQLite transaction or host authority."""

    def __init__(self, repo: ReviewWaitRepository, descriptor: int, identity: tuple[int, int]):
        self._repo = repo
        self._descriptor = descriptor
        self._identity = identity
        self._database_identity = None
        self._thread = threading.get_ident()
        self._active = True

    def _check(self) -> None:
        if not self._active or threading.get_ident() != self._thread:
            raise RepositoryError("writer_required")
        try:
            if _identity(_file(self._repo.lock_path, maximum=1)) != self._identity or _identity(os.fstat(self._descriptor)) != self._identity:
                raise RepositoryError("state_unreadable")
        except OSError:
            raise RepositoryError("state_unreadable") from None
        if self._database_identity != _identity(self._repo._inspect()):
            raise RepositoryError("state_unreadable")

    def read(self) -> StoredWait:
        self._check()
        return self._repo.read()

    def save(self, controller: ReviewWaitController, *, expected_revision: int) -> StoredWait:
        self._check()
        payload = _encode(controller)
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**63 - 1:
            raise RepositoryError("revision_conflict")
        with self._repo._connection(write=True) as connection:
            stored = self._repo._load(connection)
            if stored.revision != expected_revision:
                raise RepositoryError("revision_conflict")
            if stored.controller.binding != controller.binding or stored.controller.reviewers != controller.reviewers:
                raise RepositoryError("binding_mismatch")
            _forward(stored.controller, controller)
            connection.execute("UPDATE wait_snapshot SET revision = ?, payload = ? WHERE singleton = 1 AND revision = ?", (expected_revision + 1, payload, expected_revision))
        return StoredWait(expected_revision + 1, _decode(payload))
