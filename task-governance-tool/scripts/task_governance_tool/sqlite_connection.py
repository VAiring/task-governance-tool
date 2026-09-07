"""Configured SQLite connections and sanitized low-level operational errors.

Callers own schema/identity admission, writers, and connection lifetime. The
read-only opener starts the existing query-only snapshot and closes on failure.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from task_governance_tool.ordering import LANE_SQL_FUNCTION, canonical_lane


VERIFICATION_SPECIFIED_SQL_FUNCTION = "taskgov_verification_specified"


UNSUPPORTED_JOURNAL_MODE_MESSAGE = (
    "task database uses unsupported WAL journal mode"
)


DATABASE_BUSY_MESSAGE = "task database is busy; run the command again later"


class StorageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def verification_expectation_is_specified(exact_text: object) -> int:
    """Mirror the public Python whitespace classification inside SQLite."""

    return int(isinstance(exact_text, str) and bool(exact_text.strip()))


def configure_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.row_factory = sqlite3.Row
    connection.create_function(
        LANE_SQL_FUNCTION,
        1,
        canonical_lane,
        deterministic=True,
    )
    connection.create_function(
        VERIFICATION_SPECIFIED_SQL_FUNCTION,
        1,
        verification_expectation_is_specified,
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
    return [
        path
        for path in sqlite_sidecar_paths(db_path)
        if os.path.lexists(path)
    ]


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
