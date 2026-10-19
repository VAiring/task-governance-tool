"""Bounded operational collection facts; no answer bodies or core authority.

Resolver-owned paths and the existing physical lease protect short SQLite
transactions. Read/restart never sends, publishes, retries or registers.
"""

from contextlib import contextmanager
from copy import deepcopy
import json
import os

from . import review_answer_values as values
from .review_wait_runtime.review_wait_repository import ReviewWaitRepository, RepositoryError, _identity, _missing


_SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 1), migration_name TEXT NOT NULL CHECK(migration_name = 'review-answer-collection-v1'))",
    "slots": "CREATE TABLE slots (slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND 8), record_json TEXT NOT NULL CHECK(length(CAST(record_json AS BLOB)) <= 32768))",
    "slots_no_delete": "CREATE TRIGGER slots_no_delete BEFORE DELETE ON slots BEGIN SELECT RAISE(ABORT, 'immutable collection slot'); END",
}


def _encode(value):
    value = values.validate_record(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > 32768:
        raise RepositoryError("invalid_snapshot")
    return raw


def _decode(raw):
    try:
        value = json.loads(raw)
        if _encode(value) != raw:
            raise ValueError
        return value
    except Exception:
        raise RepositoryError("state_unreadable") from None


def initial_record(basis):
    basis = values.validate_basis(basis)
    return values.validate_record(dict(version=basis["version"], basis=basis, binding=None,
        attempts=[dict(ordinal=0, turn_id=None, source_identity=None, observation="missing",
            observed_digest=None, terminal="unknown", terminal_source=None, publication=None, failure=None)],
        correction=None, registration=dict(state="not_started", submission_digest=None, receipt_id=None),
        **({"tracking_state": "watching"} if basis["version"] == 2 else {})))


def _inventory(records):
    if not 1 <= len(records) <= 8:
        raise RepositoryError("state_unreadable")
    first = records[0]["basis"]
    sessions, keys = set(), set()
    for slot, record in enumerate(records, 1):
        if any(record["basis"][key] != first[key] for key in first if key != "request_digest"):
            raise RepositoryError("binding_mismatch")
        binding = record["binding"]
        if binding is not None:
            if binding["slot"] != slot or binding["reviewer_session_id"] in sessions or binding["reviewer_key"] in keys:
                raise RepositoryError("binding_mismatch")
            sessions.add(binding["reviewer_session_id"])
            keys.add(binding["reviewer_key"])


def _forward(old, new):
    def require(condition):
        if not condition:
            raise RepositoryError("state_transition_invalid")
    require(old["version"] == new["version"] and old["basis"] == new["basis"])
    require(old["binding"] is None or old["binding"] == new["binding"])
    tracking = old["version"] == 2
    if tracking:
        allowed_states = {"watching": {"watching", "frozen", "invalidated"},
                          "frozen": {"frozen", "invalidated"}, "invalidated": {"invalidated"}}
        require(new["tracking_state"] in allowed_states[old["tracking_state"]])
        if old["tracking_state"] == "invalidated":
            require(old == new)
    require(len(old["attempts"]) <= len(new["attempts"]) <= len(old["attempts"]) + 1)
    for left, right in zip(old["attempts"], new["attempts"]):
        require(left["ordinal"] == right["ordinal"])
        if tracking and old["tracking_state"] == "watching":
            # Rejected candidates are replaceable bounded diagnostics. Only
            # the first complete, valid result becomes an immutable source.
            continue
        if tracking:
            require({k: v for k, v in left.items() if k != "publication"}
                    == {k: v for k, v in right.items() if k != "publication"})
        for key in ("turn_id", "source_identity", "observed_digest"):
            require(left[key] is None or right[key] == left[key])
        require(left["observation"] != "conflict" or right["observation"] == "conflict")
        require(left["observation"] != "complete" or right["observation"] in ("complete", "conflict"))
        if left["terminal"] in ("completed", "failed", "interrupted"):
            require((left["terminal"], left["terminal_source"]) == (right["terminal"], right["terminal_source"]))
        if left["publication"] is not None:
            require(right["publication"] is not None and left["publication"]["original"] == right["publication"]["original"])
            allowed = {"intent": {"intent", "saved", "unknown"}, "unknown": {"unknown", "saved"}, "saved": {"saved"}}
            require(right["publication"]["state"] in allowed[left["publication"]["state"]])
    if old["correction"] is not None:
        require(new["correction"] is not None)
        left, right = old["correction"], new["correction"]
        for key in ("intent_id", "initial_source_identity", "initial_digest"):
            require(left[key] == right[key])
        allowed = {"not_sent": {"not_sent", "accepted", "failed", "unknown"}, "unknown": {"unknown", "accepted", "failed", "not_sent"},
                   "accepted": {"accepted"}, "failed": {"failed"}}
        require(right["effect"] in allowed[left["effect"]])
        if left["correction_turn_id"] is not None:
            require(left["correction_turn_id"] == right["correction_turn_id"] and left["binding_source"] == right["binding_source"])
    left, right = old["registration"], new["registration"]
    allowed = {"not_started": {"not_started", "dispatched"}, "dispatched": {"dispatched", "unknown", "registered"},
               "unknown": {"unknown", "registered"}, "registered": {"registered"}}
    require(right["state"] in allowed[left["state"]])
    for key in ("submission_digest", "receipt_id"):
        require(left[key] is None or left[key] == right[key])


class CollectionRepository(ReviewWaitRepository):
    _connection_timeout = 1
    _observation = None

    def _observe(self, event, records=None):
        if self._observation is not None:
            self._observation(event, records)

    @staticmethod
    def _load(connection):
        expected = sorted(("trigger" if sql.startswith("CREATE TRIGGER") else "table", name,
            "slots" if sql.startswith("CREATE TRIGGER") else name, sql) for name, sql in _SCHEMA.items())
        actual = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        if (actual != expected or connection.execute("PRAGMA user_version").fetchone() != (1,)
                or connection.execute("SELECT * FROM schema_history").fetchall() != [(1, "review-answer-collection-v1")]):
            raise RepositoryError("state_unreadable")
        rows = connection.execute("SELECT slot,record_json FROM slots ORDER BY slot").fetchall()
        if [row[0] for row in rows] != list(range(1, len(rows) + 1)):
            raise RepositoryError("state_unreadable")
        records = tuple(_decode(row[1]) for row in rows)
        _inventory(records)
        return records

    def read(self):
        self._observe("reading")
        with self._connection() as connection:
            records = self._load(connection)
        self._observe("observed", records)
        return records

    def initialize(self, bases, *, admit=lambda: None):
        if type(bases) not in (tuple, list):
            raise RepositoryError("invalid_snapshot")
        records = tuple(initial_record(basis) for basis in bases)
        _inventory(records)
        payloads = tuple(_encode(record) for record in records)
        with self._lease(create_lock=True) as lease:
            admit()
            if not _missing(self.path):
                existing = self.read()
                if tuple(record["basis"] for record in existing) != tuple(bases):
                    raise RepositoryError("binding_mismatch")
                return existing
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(descriptor)
            with self._connection(write=True) as connection:
                for sql in _SCHEMA.values():
                    connection.execute(sql)
                connection.execute("PRAGMA user_version=1")
                connection.execute("INSERT INTO schema_history VALUES (1,'review-answer-collection-v1')")
                connection.executemany("INSERT INTO slots VALUES (?,?)", enumerate(payloads, 1))
                self._load(connection)
            lease._database_identity = _identity(self._inspect())
            lease._check()
            return records

    @contextmanager
    def serial(self):
        self._observe("reading")
        body_error = None
        try:
            with self._lease() as lease:
                lease._database_identity = _identity(self._inspect())
                lease._check()
                self.read()
                try:
                    yield lease
                except BaseException as error:
                    body_error = error
                    raise
                lease._check()
        except BaseException as error:
            if error is not body_error:
                self._observe("reading")
            raise

    def update(self, lease, slot, transform):
        return self.update_many(lease, {slot: transform})[slot - 1]

    def update_many(self, lease, changes):
        if type(changes) is not dict or not changes or len(changes) > 8:
            raise RepositoryError("invalid_snapshot")
        self._observe("reading")
        lease._check()
        with self._connection(write=True) as connection:
            records = list(self._load(connection))
            for slot, transform in changes.items():
                if type(slot) is not int or not 1 <= slot <= len(records):
                    raise RepositoryError("binding_mismatch")
                old = records[slot - 1]
                new = transform(deepcopy(old))
                raw = _encode(new)
                _forward(old, new)
                records[slot - 1] = new
                if new != old:
                    self._observe("writing")
                    connection.execute("UPDATE slots SET record_json=? WHERE slot=?", (raw, slot))
            _inventory(records)
            self._load(connection)
        records = tuple(deepcopy(records))
        self._observe("observed", records)
        return records
