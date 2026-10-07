"""Durable, non-replayable creation intents for the single-call wait entry.

The request writer spans host effects; transactions remain short. Only bounded
structural identities and phases are stored, never handles, prompts or metadata.
Prior attempts remain immutable history once the next attempt is appended.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
import json
import os
import re
from uuid import UUID

from .review_wait_repository import ReviewWaitRepository, RepositoryError, _missing, _identity


_SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 1), migration_name TEXT NOT NULL CHECK(migration_name = 'review-wait-request-v1'))",
    "attempts": "CREATE TABLE attempts (sequence INTEGER PRIMARY KEY CHECK(sequence > 0), payload TEXT NOT NULL CHECK(length(payload) <= 16384))",
}
_TRANSITIONS = {
    "creating": {"created", "unknown"}, "created": {"prepared", "failed", "closing"},
    "prepared": {"started", "failed", "closing"}, "started": {"closing"},
    "closing": {"closed", "unknown"}, "failed": {"closing"},
}


@dataclass(frozen=True)
class RequestRecord:
    basis_digest: str
    parent_turn: str
    reviewers: tuple[tuple[str, str], ...]
    phase: str = "creating"
    automation_id: str | None = None

    def __post_init__(self):
        try:
            if (type(self.basis_digest) is not str or not re.fullmatch(r"[a-f0-9]{64}", self.basis_digest)
                    or type(self.parent_turn) is not str or str(UUID(self.parent_turn)) != self.parent_turn
                    or type(self.reviewers) is not tuple or not 1 <= len(self.reviewers) <= 64
                    or len({r[0] for r in self.reviewers}) != len(self.reviewers)
                    or self.phase not in {*_TRANSITIONS, "closed", "unknown"}):
                raise ValueError
            for pair in self.reviewers:
                if type(pair) is not tuple or len(pair) != 2:
                    raise ValueError
                for value in pair:
                    if type(value) is not str or str(UUID(value)) != value:
                        raise ValueError
            if self.automation_id is not None and (type(self.automation_id) is not str
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", self.automation_id)):
                raise ValueError
            if self.phase not in {"creating", "unknown"} and self.automation_id is None:
                raise ValueError
            if self.phase == "creating" and self.automation_id is not None:
                raise ValueError
        except (ValueError, TypeError, IndexError, AttributeError):
            raise RepositoryError("invalid_snapshot") from None


def _encode(record):
    if type(record) is not RequestRecord:
        raise RepositoryError("invalid_snapshot")
    return json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))


class RequestRepository(ReviewWaitRepository):
    @contextmanager
    def serial(self, *, create=False):
        """Create only on an explicit admitted wait, and never repair residue."""
        fresh = _missing(self.path)
        if fresh and (not create or not _missing(self.lock_path)):
            raise RepositoryError("state_unreadable")
        with self._lease(create_lock=create) as lease:
            if _missing(self.path):
                if not create or not fresh:
                    raise RepositoryError("state_unreadable")
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR
                                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(descriptor)
                with self._connection(write=True) as connection:
                    for statement in _SCHEMA.values():
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version = 1")
                    connection.execute("INSERT INTO schema_history VALUES (1, 'review-wait-request-v1')")
            lease._database_identity = _identity(self._inspect())
            lease._check()
            yield lease
            lease._check()

    @staticmethod
    def _load(connection):
        try:
            objects = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            if (objects != [("table", name, name, sql) for name, sql in sorted(_SCHEMA.items())]
                    or connection.execute("PRAGMA user_version").fetchone() != (1,)
                    or connection.execute("SELECT * FROM schema_history").fetchall() != [(1, "review-wait-request-v1")]):
                raise ValueError
            count = connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            rows = connection.execute("SELECT sequence, payload FROM attempts ORDER BY sequence DESC LIMIT 1").fetchall()
            if not rows:
                return 0, None
            sequence, payload = rows[0]
            if sequence != count or not 1 <= sequence <= 64 or type(payload) is not str or len(payload) > 16384:
                raise ValueError
            data = json.loads(payload)
            if type(data) is not dict or type(data.get("reviewers")) is not list:
                raise ValueError
            data["reviewers"] = tuple(tuple(pair) for pair in data["reviewers"])
            record = RequestRecord(**data)
            if _encode(record) != payload:
                raise ValueError
            return sequence, record
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise RepositoryError("state_unreadable") from None

    def read(self):
        with self._connection() as connection:
            return self._load(connection)

    def append(self, lease, record):
        lease._check()
        if record.phase != "creating":
            raise RepositoryError("state_transition_invalid")
        with self._connection(write=True) as connection:
            sequence, before = self._load(connection)
            if sequence >= 64 or before is not None and (
                    before.phase != "closed" or before.parent_turn == record.parent_turn):
                raise RepositoryError("state_transition_invalid")
            connection.execute("INSERT INTO attempts VALUES (?, ?)", (sequence + 1, _encode(record)))

    def advance(self, lease, phase, *, automation_id=None):
        lease._check()
        with self._connection(write=True) as connection:
            sequence, before = self._load(connection)
            if before is None or phase not in _TRANSITIONS.get(before.phase, set()):
                raise RepositoryError("state_transition_invalid")
            if automation_id is not None and (before.phase != "creating" or phase != "created"):
                raise RepositoryError("state_transition_invalid")
            after = replace(before, phase=phase, automation_id=automation_id or before.automation_id)
            connection.execute("UPDATE attempts SET payload = ? WHERE sequence = ?", (_encode(after), sequence))
            return after
