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
import hashlib
from uuid import UUID, uuid4

from task_governance_tool.no_replace import rename_no_replace
from task_governance_tool.state_paths import (
    StatePathError, create_exclusive_durable_file, path_lexically_exists, unlink_validated_file,
)

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
    continuation_id: str | None = None
    continuation_sequence: int = 0

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
            from task_governance_tool.review_continuation import identifier, _integer
            if self.continuation_id is None:
                if type(self.continuation_sequence) is not int or self.continuation_sequence != 0:
                    raise ValueError
            elif not identifier(self.continuation_id, "tg_review_continuation_") or not _integer(self.continuation_sequence, 1, 64) or len(self.reviewers) > 8:
                raise ValueError
        except (ValueError, TypeError, IndexError, AttributeError):
            raise RepositoryError("invalid_snapshot") from None


def _encode(record):
    if type(record) is not RequestRecord:
        raise RepositoryError("invalid_snapshot")
    value = asdict(RequestRecord(**asdict(record)))
    if record.continuation_id is None:
        value.pop("continuation_id")
        value.pop("continuation_sequence")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(raw.encode()) > 16384:
        raise RepositoryError("invalid_snapshot")
    return raw


def request_identity_digest(record):
    """Immutable attempt identity, excluding its monotone cleanup phase."""
    value = json.loads(_encode(record))
    value.pop("phase")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class HistoricalRequestProof:
    """Invocation-local proof of a native historical binding and joined cleanup.

    The composer obtains this only from the exact saved controller, native
    history and cleanup reader under request admission. It is never serialized
    or accepted from the public caller, and grants no host operation.
    """
    expected_attempt: int
    expected_request_digest: str
    old_automation_id: str
    old_basis_digest: str
    wait_basis_digest: str
    worker_stop_identity: str

    def matches(self, sequence, request, record):
        return (request is not None and request.phase == "closed"
            and sequence == self.expected_attempt == record.expected_attempt
            and request_identity_digest(request) == self.expected_request_digest == record.expected_request_digest
            and request.automation_id == self.old_automation_id == record.old_automation_id
            and request.basis_digest == self.old_basis_digest
            and self.wait_basis_digest == record.wait_basis_digest
            and self.old_basis_digest != self.wait_basis_digest
            and type(self.worker_stop_identity) is str
            and re.fullmatch(r"[a-f0-9]{64}", self.worker_stop_identity) is not None)


def _decode_record(payload):
    try:
        if type(payload) is not str or len(payload.encode()) > 16384:
            raise ValueError
        value = json.loads(payload)
        if type(value) is not dict or type(value.get("reviewers")) is not list:
            raise ValueError
        value["reviewers"] = tuple(tuple(pair) for pair in value["reviewers"])
        record = RequestRecord(**value)
        if _encode(record) != payload:
            raise ValueError
        return record
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, RepositoryError):
        raise RepositoryError("state_unreadable") from None


class ParentDispatchRepository(ReviewWaitRepository):
    def _prepare_lock(self):
        if not _missing(self.lock_path):
            return
        root = self.path.parent
        temporary = None
        try:
            temporary = create_exclusive_durable_file(
                self.lock_path.with_name(".dispatch-" + uuid4().hex + ".tmp"),
                b"\x00", root=root, max_bytes=1)
            try:
                rename_no_replace(temporary, self.lock_path, root=root)
            except StatePathError:
                # A concurrent creator may publish first. Admission below
                # still validates that complete physical lock; never replace it.
                if _missing(self.lock_path):
                    raise
        except StatePathError:
            raise RepositoryError("state_unreadable") from None
        finally:
            if temporary is not None and path_lexically_exists(temporary.path):
                unlink_validated_file(temporary, root=root)

    @contextmanager
    def serial(self):
        """Nonblocking physical OS lease only; no database or send replay."""
        # Publish the complete byte atomically; direct O_EXCL creation would
        # expose an empty lock to another same-parent worker before os.write.
        self._prepare_lock()
        with self._lease():
            yield


class RequestRepository(ReviewWaitRepository):
    # A live continuation worker reads this journal while admission records its
    # started phase. Let that short snapshot release its SQLite read lock; never
    # replay the transaction or any host effect after a failed commit.
    _connection_timeout = 1

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
    def _load(connection, *, with_rounds=False):
        try:
            from . import request_continuation as continuation
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            schema = _SCHEMA if version == 1 else {**_SCHEMA, **continuation.SCHEMA} if version == 2 else None
            objects = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            if (schema is None or objects != [("table", name, name, sql) for name, sql in sorted(schema.items())]
                    or connection.execute("SELECT * FROM schema_history").fetchall() != [(version, "review-wait-request-v" + str(version))]):
                raise ValueError
            count = connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            rounds = continuation.load_rounds(connection) if version == 2 else ()
            rows = connection.execute("SELECT sequence, payload FROM attempts ORDER BY sequence DESC LIMIT 1").fetchall()
            if not rows:
                return (0, None, rounds) if with_rounds else (0, None)
            sequence, payload = rows[0]
            if sequence != count or not 1 <= sequence <= 64 or type(payload) is not str or len(payload) > 16384:
                raise ValueError
            record = _decode_record(payload)
            if version == 1 and record.continuation_id is not None:
                raise ValueError
            if version == 2:
                history = connection.execute("SELECT sequence,payload FROM attempts ORDER BY sequence").fetchall()
                if [row[0] for row in history] != list(range(1, count + 1)):
                    raise ValueError
                known = {row.continuation_id: row for row in rounds}
                selected = {}
                for index, raw in history:
                    prior = _decode_record(raw)
                    if index < count and prior.phase != "closed":
                        raise ValueError
                    if prior.continuation_id is not None:
                        bound = known.get(prior.continuation_id)
                        if (bound is None or bound.phase != "published" or prior.continuation_sequence != bound.continuation_sequence
                            or prior.basis_digest != bound.wait_basis_digest):
                            raise ValueError
                        earlier = selected.get(prior.continuation_id)
                        if earlier is None and index != bound.expected_attempt + 1:
                            raise ValueError
                        if earlier is not None and (earlier[0] != index - 1
                            or earlier[1].reviewers != prior.reviewers or earlier[1].parent_turn == prior.parent_turn):
                            raise ValueError
                        selected[prior.continuation_id] = (index, prior)
            return (sequence, record, rounds) if with_rounds else (sequence, record)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise RepositoryError("state_unreadable") from None

    def read(self):
        with self._connection() as connection:
            return self._load(connection)

    def read_admission(self):
        """One validated snapshot for attempt and its explicit round."""
        with self._connection() as connection:
            return self._load(connection, with_rounds=True)

    def append(self, lease, record):
        lease._check()
        if record.phase != "creating":
            raise RepositoryError("state_transition_invalid")
        with self._connection(write=True) as connection:
            sequence, before = self._load(connection)
            from .request_continuation import encode as encode_round, load_rounds
            rounds = load_rounds(connection) if connection.execute("PRAGMA user_version").fetchone()[0] == 2 else ()
            continued = rounds[-1] if rounds else None
            if record.continuation_id is not None:
                if (continued is None or record.continuation_id != continued.continuation_id
                    or record.continuation_sequence != continued.continuation_sequence or record.basis_digest != continued.wait_basis_digest
                    or continued.phase not in ("prepared", "published")):
                    raise RepositoryError("state_transition_invalid")
                if continued.phase == "prepared" and sequence != continued.expected_attempt:
                    raise RepositoryError("state_transition_invalid")
                if continued.phase == "published" and (before is None
                    or before.continuation_id != record.continuation_id
                    or before.continuation_sequence != record.continuation_sequence
                    or before.basis_digest != record.basis_digest or before.reviewers != record.reviewers
                    or before.parent_turn == record.parent_turn):
                    raise RepositoryError("state_transition_invalid")
            elif continued is not None and continued.phase != "published":
                raise RepositoryError("state_transition_invalid")
            if sequence >= 64 or before is not None and (
                    before.phase != "closed" or before.parent_turn == record.parent_turn and record.continuation_id is None):
                raise RepositoryError("state_transition_invalid")
            connection.execute("INSERT INTO attempts VALUES (?, ?)", (sequence + 1, _encode(record)))
            if record.continuation_id is not None and continued.phase == "prepared":
                connection.execute("UPDATE rounds SET payload=? WHERE continuation_id=?", (encode_round(replace(continued, phase="published")), continued.continuation_id))
            self._load(connection)

    def read_rounds(self):
        from .request_continuation import load_rounds
        with self._connection() as connection:
            self._load(connection)
            return load_rounds(connection) if connection.execute("PRAGMA user_version").fetchone()[0] == 2 else ()

    def install_round(self, lease, record, *, historical=None):
        """Explicit settled conversion/publication, preserving every old JSON byte."""
        from . import request_continuation as continuation
        lease._check()
        if type(record) is not continuation.RequestRound or record.phase != "prepared":
            raise RepositoryError("state_transition_invalid")
        with self._connection(write=True) as connection:
            sequence, before = self._load(connection)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 2:
                rounds = continuation.load_rounds(connection)
                previous = next((row for row in rounds if row.continuation_id == record.continuation_id), None)
                if previous is not None:
                    if replace(previous, phase="prepared") != record:
                        raise RepositoryError("binding_mismatch")
                    return previous
            if (sequence != record.expected_attempt or before is not None and (
                before.phase != "closed" or request_identity_digest(before) != record.expected_request_digest
                or before.automation_id != record.old_automation_id
                or before.basis_digest != record.wait_basis_digest and not (
                    type(historical) is HistoricalRequestProof and historical.matches(sequence, before, record)
                    and historical.worker_stop_identity == record.worker_stop_identity))):
                raise RepositoryError("binding_mismatch")
            if version == 1:
                # Validate the whole admitted predecessor, not just its last row.
                history = connection.execute("SELECT sequence,payload FROM attempts ORDER BY sequence").fetchall()
                if ([row[0] for row in history] != list(range(1, sequence + 1))
                    or any(_decode_record(raw).phase != "closed" or _decode_record(raw).continuation_id is not None for _, raw in history)):
                    raise RepositoryError("state_unreadable")
                connection.execute("DROP TABLE schema_history")
                for statement in continuation.SCHEMA.values():
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=2")
                connection.execute("INSERT INTO schema_history VALUES (2,'review-wait-request-v2')")
            rounds = continuation.load_rounds(connection)
            previous = next((row for row in rounds if row.continuation_id == record.continuation_id), None)
            if previous is not None:
                if replace(previous, phase="prepared") != record:
                    raise RepositoryError("binding_mismatch")
                return previous
            group = [row for row in rounds if row.preparation_id == record.preparation_id]
            if (len(group) >= 64 or record.continuation_sequence != len(group) + 1
                or rounds and rounds[-1].phase != "published"):
                raise RepositoryError("state_transition_invalid")
            ordinal = len(rounds) + 1
            connection.execute("INSERT INTO rounds VALUES (?,?,?,?,?)", (ordinal,record.preparation_id,record.continuation_sequence,record.continuation_id,continuation.encode(record)))
            connection.execute("INSERT INTO current_round VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET ordinal=excluded.ordinal", (ordinal,))
            self._load(connection)
        return record

    def publish_without_wait(self, lease, record):
        """Consume a ready round after ended reviews or proven fresh-basis retirement.

        The composer must validate the native historical binding and exact
        settled effects before retiring an older-generation unused round.
        Original rows and identities remain unchanged except monotone phase.
        """
        from .request_continuation import encode as encode_round, load_rounds
        lease._check()
        with self._connection(write=True) as connection:
            sequence, _ = self._load(connection)
            rounds = load_rounds(connection)
            if not rounds or rounds[-1] != record or record.phase != "prepared" or sequence != record.expected_attempt:
                raise RepositoryError("state_transition_invalid")
            after = replace(record, phase="published")
            connection.execute("UPDATE rounds SET payload=? WHERE continuation_id=?", (encode_round(after), record.continuation_id))
            self._load(connection)
        return after

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
