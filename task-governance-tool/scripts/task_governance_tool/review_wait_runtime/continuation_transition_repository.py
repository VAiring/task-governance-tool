"""Durable fence before old-worker cleanup; no host effects or inferred exit.

Request/finalization v1 conversion must wait for settled cleanup. This separate
resolver-owned transition intent bridges that boundary without altering either
old journal while its worker may still use it.
"""

from contextlib import contextmanager
from dataclasses import replace
import os

from .request_continuation import RequestRound, PHASES, SCHEMA, encode, load_rounds
from .review_wait_repository import ReviewWaitRepository, RepositoryError, _missing, _identity


_SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 1), migration_name TEXT NOT NULL CHECK(migration_name = 'review-continuation-transition-v1'))",
    "rounds": SCHEMA["rounds"],
    "current_round": SCHEMA["current_round"],
}


class TransitionRepository(ReviewWaitRepository):
    @contextmanager
    def serial(self, *, create=False):
        fresh = _missing(self.path)
        if fresh and (not create or not _missing(self.lock_path)):
            raise RepositoryError("state_unreadable")
        with self._lease(create_lock=create) as lease:
            if _missing(self.path):
                if not create or not fresh:
                    raise RepositoryError("state_unreadable")
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(descriptor)
                with self._connection(write=True) as connection:
                    for statement in _SCHEMA.values():
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version=1")
                    connection.execute("INSERT INTO schema_history VALUES (1,'review-continuation-transition-v1')")
            lease._database_identity = _identity(self._inspect())
            lease._check()
            yield lease
            lease._check()

    @staticmethod
    def _load(connection):
        objects = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        if (objects != [("table", name, name, sql) for name, sql in sorted(_SCHEMA.items())]
            or connection.execute("PRAGMA user_version").fetchone() != (1,)
            or connection.execute("SELECT * FROM schema_history").fetchall() != [(1, "review-continuation-transition-v1")]):
            raise RepositoryError("state_unreadable")
        return load_rounds(connection)

    def read(self):
        with self._connection() as connection:
            return self._load(connection)

    def fence(self, lease, record):
        lease._check()
        payload = encode(record)
        if record.phase != "fenced" or record.worker_stop_identity is not None:
            raise RepositoryError("state_transition_invalid")
        with self._connection(write=True) as connection:
            rounds = self._load(connection)
            prior = next((row for row in rounds if row.continuation_id == record.continuation_id), None)
            if prior is not None:
                # Later phases reconcile an identical immutable admission.
                if replace(prior, phase="fenced", worker_stop_identity=None, packet_digest=None) != record:
                    raise RepositoryError("binding_mismatch")
                return prior
            group = [row for row in rounds if row.preparation_id == record.preparation_id]
            if (len(group) >= 64 or record.continuation_sequence != len(group) + 1
                or rounds and rounds[-1].phase != "published"):
                raise RepositoryError("state_transition_invalid")
            ordinal = len(rounds) + 1
            connection.execute("INSERT INTO rounds VALUES (?,?,?,?,?)", (ordinal,record.preparation_id,record.continuation_sequence,record.continuation_id,payload))
            connection.execute("INSERT INTO current_round VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET ordinal=excluded.ordinal", (ordinal,))
            self._load(connection)
        return record

    def bind_packet(self, lease, record, digest):
        """Persist exact byte intent before exclusive file creation; never change it."""
        lease._check()
        with self._connection(write=True) as connection:
            rounds = self._load(connection)
            if not rounds or rounds[-1] != record or record.phase != "settled":
                raise RepositoryError("state_transition_invalid")
            if record.packet_digest is not None:
                if record.packet_digest != digest:
                    raise RepositoryError("binding_mismatch")
                return record
            after = replace(record, packet_digest=digest)
            connection.execute("UPDATE rounds SET payload=? WHERE continuation_id=?", (encode(after), record.continuation_id))
            self._load(connection)
        return after

    def advance(self, lease, record, *, phase, worker_stop_identity=None, packet_path=None, packet_digest=None, result_paths=()):
        lease._check()
        if type(record) is not RequestRound or phase not in PHASES:
            raise RepositoryError("state_transition_invalid")
        with self._connection(write=True) as connection:
            rounds = self._load(connection)
            if not rounds or rounds[-1] != record or PHASES.index(phase) != PHASES.index(record.phase) + 1:
                raise RepositoryError("state_transition_invalid")
            changes = {"phase": phase}
            if phase == "settled":
                changes["worker_stop_identity"] = worker_stop_identity
                if packet_path is not None or packet_digest is not None or result_paths:
                    raise RepositoryError("state_transition_invalid")
            elif phase == "prepared":
                if packet_path != record.packet_path or result_paths != record.result_paths:
                    raise RepositoryError("state_transition_invalid")
                if record.packet_digest is not None and record.packet_digest != packet_digest:
                    raise RepositoryError("binding_mismatch")
                changes.update(packet_path=packet_path, packet_digest=packet_digest, result_paths=result_paths)
                if worker_stop_identity is not None:
                    raise RepositoryError("state_transition_invalid")
            elif any(value is not None for value in (worker_stop_identity, packet_path, packet_digest)) or result_paths:
                raise RepositoryError("state_transition_invalid")
            after = replace(record, **changes)
            connection.execute("UPDATE rounds SET payload=? WHERE continuation_id=?", (encode(after), record.continuation_id))
            self._load(connection)
        return after
