"""Independent numerical SQLite store; never joins a core Task transaction.

Only explicit setup calls initialize(). Ordinary readers/writers require an
existing exact schema and binding. A failure is numerical unavailability, not
permission to repair state or reject a core Task operation.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile

from task_governance_tool.session_identity import CallerIdentity, is_session_id
from task_governance_tool.sqlite_connection import (
    connect, connect_existing, connect_readonly, StorageError,
    validate_operational_journal_state,
)
from task_governance_tool.state_paths import (
    inspect_physical_directory, inspect_physical_file, path_lexically_exists,
    StatePathError, hash_physical_file, unlink_validated_file,
)
from task_governance_tool.no_replace import rename_no_replace
from task_governance_tool.usage_adapter import CollectionBatch, Cursor
from task_governance_tool.usage_values import GAP_CODES, METRICS, ResponseUsage, UsageError


USAGE_SCHEMA = 1
_DDL = (
    """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version = 1),
        name TEXT NOT NULL CHECK(name = 'response_collection'))""",
    """CREATE TABLE usage_meta (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1), project_id TEXT NOT NULL,
        binding_hash TEXT NOT NULL, binding_generation INTEGER NOT NULL CHECK(binding_generation > 0))""",
    """CREATE TABLE usage_sessions (thread_id TEXT PRIMARY KEY)""",
    """CREATE TABLE usage_sources (
        source_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES usage_sessions(thread_id),
        incarnation INTEGER NOT NULL DEFAULT 0, offset INTEGER NOT NULL DEFAULT 0,
        prefix TEXT NOT NULL DEFAULT '', file_id TEXT NOT NULL DEFAULT '',
        pending TEXT NOT NULL DEFAULT 'unread' CHECK(pending IN ('unread','none','partial_tail','more_records')),
        CHECK(incarnation >= 0 AND offset >= 0))""",
    """CREATE TABLE usage_incarnations (
        source_id TEXT NOT NULL REFERENCES usage_sources(source_id), incarnation INTEGER NOT NULL,
        offset INTEGER NOT NULL, prefix TEXT NOT NULL, file_id TEXT NOT NULL,
        PRIMARY KEY(source_id, incarnation), CHECK(incarnation > 0 AND offset > 0))""",
    """CREATE TABLE usage_responses (
        provider TEXT NOT NULL, response_id TEXT NOT NULL, thread_id TEXT NOT NULL,
        turn_id TEXT NOT NULL, model TEXT, effort TEXT,
        input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
        total_tokens INTEGER NOT NULL CHECK(total_tokens = input_tokens + output_tokens),
        cached_input_tokens INTEGER CHECK(cached_input_tokens BETWEEN 0 AND input_tokens),
        reasoning_output_tokens INTEGER CHECK(reasoning_output_tokens BETWEEN 0 AND output_tokens),
        cache_write_input_tokens INTEGER CHECK(cache_write_input_tokens BETWEEN 0 AND input_tokens),
        PRIMARY KEY(provider, response_id))""",
    """CREATE TABLE usage_observations (
        source_id TEXT NOT NULL, incarnation INTEGER NOT NULL,
        provider TEXT NOT NULL, response_id TEXT NOT NULL,
        PRIMARY KEY(source_id, incarnation, provider, response_id),
        FOREIGN KEY(source_id, incarnation) REFERENCES usage_incarnations(source_id, incarnation),
        FOREIGN KEY(provider, response_id) REFERENCES usage_responses(provider, response_id))""",
    """CREATE TABLE usage_conflicts (
        provider TEXT NOT NULL, response_id TEXT NOT NULL,
        PRIMARY KEY(provider, response_id),
        FOREIGN KEY(provider, response_id) REFERENCES usage_responses(provider, response_id))""",
    """CREATE TABLE usage_diagnostics (
        source_id TEXT NOT NULL REFERENCES usage_sources(source_id), code TEXT NOT NULL,
        PRIMARY KEY(source_id, code))""",
)


def _schema(connection):
    return tuple(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"))


def _cursor(row) -> Cursor:
    incarnation, offset, prefix, file_id = (row[name] for name in
                                           ("incarnation", "offset", "prefix", "file_id"))
    if (type(incarnation) is not int or type(offset) is not int
            or incarnation < 0 or offset < 0
            or not isinstance(prefix, str) or not isinstance(file_id, str)):
        raise UsageError("usage_schema_invalid")
    if incarnation == 0:
        if offset != 0 or prefix != "" or file_id != "":
            raise UsageError("usage_schema_invalid")
    elif (offset == 0 or len(prefix) != 64
          or any(c not in "0123456789abcdef" for c in prefix)
          or len(file_id.split(":")) != 2
          or any(not part or any(c not in "0123456789" for c in part)
                 for part in file_id.split(":"))):
        raise UsageError("usage_schema_invalid")
    return Cursor(incarnation, offset, prefix, file_id)


def _response(row) -> ResponseUsage:
    return ResponseUsage(row["provider"], row["response_id"], row["thread_id"],
                         row["turn_id"], row["model"], row["effort"],
                         tuple(row[name] for name in METRICS))


class UsageRepository:
    schema_statements = _DDL
    migrations = ((1, "response_collection"),)
    collect_attribution = False

    def __init__(self, path: Path, project_id: str, binding_hash: str, binding_generation: int):
        self.path = Path(path)
        self.basis = (project_id, binding_hash, binding_generation)
        if (not isinstance(project_id, str) or not project_id or len(project_id) > 200
                or not isinstance(binding_hash, str) or len(binding_hash) != 64
                or any(c not in "0123456789abcdef" for c in binding_hash)
                or type(binding_generation) is not int or binding_generation < 1):
            raise UsageError()

    def _physical(self, *, missing=False):
        inspect_physical_directory(self.path.parent)
        if path_lexically_exists(self.path):
            inspect_physical_file(self.path, root=self.path.parent)
        elif path_lexically_exists(Path(str(self.path) + "-journal")):
            raise UsageError()
        elif not missing:
            raise UsageError("usage_not_initialized")
        validate_operational_journal_state(self.path)

    def _validate(self, connection):
        if _schema(connection) != tuple(sorted(self.schema_statements, key=lambda sql: sql.split()[2])):
            raise UsageError("usage_schema_invalid")
        if [tuple(r) for r in connection.execute("SELECT * FROM usage_migrations ORDER BY version")] != list(self.migrations):
            raise UsageError("usage_schema_invalid")
        if [tuple(r) for r in connection.execute("SELECT project_id,binding_hash,binding_generation FROM usage_meta")] != [self.basis]:
            raise UsageError("usage_binding_mismatch")

    @contextmanager
    def connection(self, *, write=False):
        connection = None
        try:
            self._physical()
            connection = connect_existing(self.path) if write else connect_readonly(self.path)
            if write:
                connection.execute("BEGIN IMMEDIATE")
            self._validate(connection)
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError, StatePathError, StorageError):
            raise UsageError() from None
        finally:
            if connection is not None:
                connection.close()

    def initialize(self) -> str:
        """Explicit setup only. Never repair/overwrite an incompatible store."""
        connection = None
        temporary = None
        try:
            self._physical(missing=True)
            if path_lexically_exists(self.path):
                with self.connection() as connection:
                    if (connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                            or connection.execute("PRAGMA foreign_key_check").fetchone()):
                        raise UsageError()
                return "current"
            descriptor, name = tempfile.mkstemp(prefix=".taskgov-usage-", suffix=".tmp", dir=self.path.parent)
            os.close(descriptor)
            temporary = Path(name)
            connection = connect(temporary)
            connection.execute("BEGIN IMMEDIATE")
            for statement in self.schema_statements:
                connection.execute(statement)
            connection.execute("INSERT INTO usage_meta VALUES (1,?,?,?)", self.basis)
            connection.executemany("INSERT INTO usage_migrations VALUES (?,?)", self.migrations)
            self._validate(connection)
            connection.commit()
            connection.close()
            connection = None
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            rename_no_replace(hash_physical_file(temporary, root=self.path.parent),
                              self.path, root=self.path.parent)
            return "initialized"
        except (sqlite3.Error, OSError, StatePathError, StorageError):
            raise UsageError() from None
        finally:
            if connection is not None:
                connection.close()
            if temporary is not None and temporary.exists():
                unlink_validated_file(hash_physical_file(temporary, root=self.path.parent),
                                      root=self.path.parent)

    def inspect(self) -> str:
        if not path_lexically_exists(self.path):
            return "not_present"
        with self.connection():
            return "current"

    def register_source(self, source_id: str, caller: CallerIdentity) -> None:
        """Internal lifecycle seam, not a public participation command."""
        thread = caller.session_id
        if (not is_session_id(thread) or not isinstance(source_id, str)
                or len(source_id) != 64 or any(c not in "0123456789abcdef" for c in source_id)):
            raise UsageError("source_unreadable")
        with self.connection(write=True) as connection:
            previous = connection.execute("SELECT thread_id FROM usage_sources WHERE source_id=?", (source_id,)).fetchone()
            if previous is not None and previous[0] != thread:
                raise UsageError("source_unreadable")
            connection.execute("INSERT OR IGNORE INTO usage_sessions VALUES (?)", (thread,))
            connection.execute("INSERT OR IGNORE INTO usage_sources(source_id,thread_id) VALUES (?,?)", (source_id, thread))

    def cursor(self, source_id: str, thread_id: str) -> Cursor:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM usage_sources WHERE source_id=? AND thread_id=?", (source_id, thread_id)).fetchone()
            if row is None:
                raise UsageError("source_not_registered")
            return _cursor(row)

    def _before_cursor(self, connection):
        """Local fault-injection seam after observations, before cursor commit."""

    def _record_attribution(self, connection, batch):
        if batch.attribution:
            raise UsageError("usage_schema_invalid")

    def record_gap(self, source_id: str, thread_id: str, code: str) -> None:
        # An uncommitted/stale observation has lost no data. Its current call
        # reports unknown; successful replay must not inherit a permanent gap.
        if code not in GAP_CODES or code in {"cursor_stale", "source_changed"}:
            return
        with self.connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM usage_sources WHERE source_id=? AND thread_id=?",
                                  (source_id, thread_id)).fetchone() is None:
                raise UsageError("source_not_registered")
            connection.execute("INSERT OR IGNORE INTO usage_diagnostics VALUES (?,?)", (source_id, code))

    def commit_batch(self, batch: CollectionBatch) -> None:
        if (any(code not in GAP_CODES for code in batch.diagnostics)
                or batch.pending not in {"none", "partial_tail", "more_records"}):
            raise UsageError()
        with self.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM usage_sources WHERE source_id=? AND thread_id=?", (batch.source_id, batch.thread_id)).fetchone()
            if row is None or _cursor(row) != batch.expected:
                raise UsageError("cursor_stale")
            new, old = batch.successor, batch.expected
            if (new.incarnation not in {old.incarnation, old.incarnation + 1}
                    or new.incarnation < 1 or new.offset < 1
                    or (new.incarnation == old.incarnation and new.offset < old.offset)):
                raise UsageError("cursor_stale")
            connection.execute("""INSERT INTO usage_incarnations VALUES (?,?,?,?,?)
                ON CONFLICT(source_id,incarnation) DO UPDATE SET
                offset=excluded.offset,prefix=excluded.prefix,file_id=excluded.file_id""",
                               (batch.source_id, new.incarnation, new.offset, new.prefix, new.file_id))
            for response in batch.responses:
                if response.thread_id != batch.thread_id:
                    raise UsageError("source_unreadable")
                existing = connection.execute("SELECT * FROM usage_responses WHERE provider=? AND response_id=?", (response.provider, response.response_id)).fetchone()
                if existing is None:
                    connection.execute("INSERT INTO usage_responses VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                       (response.provider, response.response_id, response.thread_id,
                                        response.turn_id, response.model, response.effort, *response.counts))
                elif _response(existing) != response:
                    connection.execute("INSERT OR IGNORE INTO usage_conflicts VALUES (?,?)", (response.provider, response.response_id))
                connection.execute("INSERT OR IGNORE INTO usage_observations VALUES (?,?,?,?)",
                                   (batch.source_id, new.incarnation, response.provider, response.response_id))
            # The adapter rescans owned turn coverage through the successor
            # cursor. A later modern row can resolve a previously legacy-only
            # turn; other gaps remain durable, including source replacement.
            connection.execute("DELETE FROM usage_diagnostics WHERE source_id=? AND code='legacy_usage'",
                               (batch.source_id,))
            connection.executemany("INSERT OR IGNORE INTO usage_diagnostics VALUES (?,?)",
                                   ((batch.source_id, code) for code in batch.diagnostics if code != "partial_tail"))
            self._record_attribution(connection, batch)
            self._before_cursor(connection)
            connection.execute("UPDATE usage_sources SET incarnation=?,offset=?,prefix=?,file_id=?,pending=? WHERE source_id=?",
                               (new.incarnation, new.offset, new.prefix, new.file_id,
                                batch.pending, batch.source_id))

    def summary(self) -> dict:
        """Observed totals only; collection alone cannot certify final coverage."""
        with self.connection() as connection:
            models = {}
            conflict_count = connection.execute("SELECT count(*) FROM usage_conflicts").fetchone()[0]
            for row in connection.execute("""SELECT r.* FROM usage_responses r WHERE NOT EXISTS (
                SELECT 1 FROM usage_conflicts c WHERE c.provider=r.provider AND c.response_id=r.response_id)
                ORDER BY provider, model, response_id"""):
                response = _response(row)
                key = (response.provider, response.model)
                bucket = models.setdefault(key, {"provider": key[0], "model": key[1],
                                                "response_count": 0, **dict.fromkeys(METRICS, 0)})
                bucket["response_count"] += 1
                for name, value in zip(METRICS, response.counts):
                    bucket[name] = None if value is None or bucket[name] is None else bucket[name] + value
            gaps = {row[0] for row in connection.execute("SELECT DISTINCT code FROM usage_diagnostics")}
            if not gaps <= GAP_CODES:
                raise UsageError()
            pending = {row[0] for row in connection.execute("SELECT DISTINCT pending FROM usage_sources WHERE pending!='none'")}
            if "partial_tail" in pending:
                gaps.add("partial_tail")
            if pending & {"unread", "more_records"}:
                gaps.add("collection_pending")
            if conflict_count:
                gaps.add("response_conflict")
            return {"status": "conflicting" if conflict_count else "incomplete" if gaps else "pending",
                    "models": list(models.values()), "conflicting_responses": conflict_count,
                    "registered_sources": connection.execute("SELECT count(*) FROM usage_sources").fetchone()[0],
                    "diagnostics": sorted(gaps)}
