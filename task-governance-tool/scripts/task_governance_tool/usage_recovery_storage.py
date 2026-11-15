"""Existing-only SQLite crash rollback; no initialization or semantic repair."""

import sqlite3
from pathlib import Path

from .artifact_lock import ArtifactLockError
from .sqlite_connection import connect_existing, StorageError
from .state_paths import StatePathError, inspect_physical_file, path_lexically_exists
from .usage_values import UsageError, UsageRecoveryRequired


def failure(error):
    """Preserve fixed native classifications without inspecting error text."""
    if isinstance(error, UsageError):
        if error.reason != "unavailable":
            return error.reason
        return {"usage_pending": "budget", "usage_schema_invalid": "schema",
                "usage_binding_mismatch": "binding", "usage_not_initialized": "missing"}.get(error.code, error.reason)
    if isinstance(error, ArtifactLockError) and error.contended:
        return "busy"
    native = error.__cause__ if isinstance(error, (StorageError, StatePathError, ArtifactLockError)) else error
    code = getattr(native, "sqlite_errorcode", None) if isinstance(native, sqlite3.Error) else None
    if code == sqlite3.SQLITE_READONLY_ROLLBACK:
        return "readonly_rollback"
    if type(code) is int:
        if code & 255 in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return "busy"
        if code & 255 in {sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM, sqlite3.SQLITE_AUTH}:
            return "permission"
        if code & 255 == sqlite3.SQLITE_INTERRUPT:
            return "budget"
    if isinstance(native, PermissionError):
        return "permission"
    if isinstance(error, StorageError) and error.code == "unsupported_journal_mode":
        return "unsupported_journal"
    return "unavailable"


def _identity(repository, expected, *, unchanged):
    observed = repository._physical()
    if (observed is None or expected is None or
            (observed != expected if unchanged else
             (observed.device, observed.inode) != (expected.device, expected.inode))):
        raise UsageError(reason="physical_changed")
    if repository.path.lstat().st_nlink != 1:
        raise UsageError(reason="physical_changed")
    journal = Path(str(repository.path) + "-journal")
    if path_lexically_exists(journal):
        inspect_physical_file(journal, root=repository.path.parent)
        if journal.lstat().st_nlink != 1:
            raise UsageError(reason="physical_changed")


def recover(repository, error, *, setup=False):
    """One attempt after a failed read of this exact physical store.

    The caller holds the numerical execution lease. SQLite itself owns the
    rollback lock/protocol. Query-only SQL cannot initialize, migrate or edit
    business rows. Validation after native rollback is fresh on this connection.
    """
    if type(error) is not UsageRecoveryRequired or error.repository is not repository:
        raise UsageError()
    connection = None
    try:
        _identity(repository, error.identity, unchanged=True)
        connection = connect_existing(repository.path, timeout=repository.connection_timeout)
        repository._configure_work(connection)
        _identity(repository, error.identity, unchanged=True)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        try:
            repository._validate(connection)
            status = "current"
        except UsageError as invalid:
            if not setup or invalid.code != "usage_schema_invalid":
                raise
            for previous in getattr(repository, "_previous_repositories", ()):
                try:
                    previous(repository.path, *repository.basis)._validate(connection)
                    status = "migration_required"
                    break
                except UsageError as older:
                    if older.code != "usage_schema_invalid":
                        raise
            else:
                raise invalid
        _identity(repository, error.identity, unchanged=False)
        return status
    except (sqlite3.Error, OSError, StatePathError, StorageError) as caught:
        raise UsageError(reason=failure(caught)) from None
    finally:
        if connection is not None:
            connection.close()
