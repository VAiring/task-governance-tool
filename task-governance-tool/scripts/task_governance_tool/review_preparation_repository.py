"""Immutable ready handoff locations in the resolver-owned operational store.

This repository stores structural references, never Packets or result bodies.
It neither reads reviewed material nor admits host/Git effects. Callers validate
the physical publication and current core basis before acquiring this writer.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os

from . import review_continuation as values
from .review_wait_runtime.review_wait_repository import ReviewWaitRepository, RepositoryError, _missing, _identity
from .storage import validate_utc_timestamp


RECORD_LIMIT = 32768
_SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 1), migration_name TEXT NOT NULL CHECK(migration_name = 'review-preparation-v1'))",
    "preparations": "CREATE TABLE preparations (preparation_id TEXT PRIMARY KEY NOT NULL, record_json TEXT NOT NULL CHECK(length(CAST(record_json AS BLOB)) <= 32768))",
    "current": "CREATE TABLE current (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), preparation_id TEXT NOT NULL REFERENCES preparations(preparation_id))",
    "preparations_no_update": "CREATE TRIGGER preparations_no_update BEFORE UPDATE ON preparations BEGIN SELECT RAISE(ABORT, 'immutable preparation'); END",
    "preparations_no_delete": "CREATE TRIGGER preparations_no_delete BEFORE DELETE ON preparations BEGIN SELECT RAISE(ABORT, 'immutable preparation'); END",
}


@dataclass(frozen=True)
class PreparationRecord:
    version: int
    preparation_id: str
    basis: dict
    route: str
    packet_path: str
    packet_digest: str
    result_paths: tuple[str, ...]
    finalization_store: str | None
    created_at: str

    def __post_init__(self):
        try:
            if type(self.version) is not int or self.version != 1 or not values.identifier(self.preparation_id, "tg_review_preparation_"):
                raise ValueError
            object.__setattr__(self, "basis", values.validate_basis(self.basis))
            if self.route not in ("manual", "integrated") or not values._digest(self.packet_digest):
                raise ValueError
            if type(self.result_paths) is not tuple or not 1 <= len(self.result_paths) <= 8:
                raise ValueError
            paths = (self.packet_path, *self.result_paths)
            if len(set(paths)) != len(paths):
                raise ValueError
            # Reuse the existing portable structural rules without filesystem
            # inspection. The composition owns ignore and physical-file checks.
            from pathlib import Path
            from .review_handoff import _relative_path
            for path in paths:
                if type(path) is not str or not path.endswith(".json"):
                    raise ValueError
                _relative_path(Path("structural-root"), path)
            validate_utc_timestamp(self.created_at, field="review preparation creation time")
            digest = sha256((self.basis["parent_session_id"] + ":" + self.basis["task_id"]).encode("ascii")).hexdigest()
            expected = f"finalize-{digest}-{self.basis['target_generation']}.sqlite"
            if ((self.route == "manual" and self.finalization_store is not None)
                or (self.route == "integrated" and (self.finalization_store != expected or self.basis["target_kind"] != "git_snapshot"))):
                raise ValueError
        except Exception:
            raise RepositoryError("invalid_snapshot") from None


def _encode(record):
    if type(record) is not PreparationRecord:
        raise RepositoryError("invalid_snapshot")
    record = PreparationRecord(**asdict(record))
    raw = json.dumps(asdict(record), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > RECORD_LIMIT:
        raise RepositoryError("invalid_snapshot")
    return raw


def _decode(raw):
    try:
        value = json.loads(raw)
        if type(value) is not dict or set(value) != set(PreparationRecord.__dataclass_fields__):
            raise ValueError
        value["result_paths"] = tuple(value["result_paths"])
        record = PreparationRecord(**value)
        if _encode(record) != raw:
            raise ValueError
        return record
    except (ValueError, KeyError, TypeError, RecursionError, RepositoryError):
        raise RepositoryError("state_unreadable") from None


class PreparationRepository(ReviewWaitRepository):
    @staticmethod
    def _load(connection):
        expected = sorted(("trigger" if sql.startswith("CREATE TRIGGER") else "table", name,
                           "preparations" if sql.startswith("CREATE TRIGGER") else name, sql)
                          for name, sql in _SCHEMA.items())
        actual = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        if (actual != expected or connection.execute("PRAGMA user_version").fetchone() != (1,)
            or connection.execute("SELECT * FROM schema_history").fetchall() != [(1, "review-preparation-v1")]
            or connection.execute("PRAGMA foreign_key_check").fetchall()):
            raise RepositoryError("state_unreadable")
        rows = connection.execute("SELECT preparation_id,record_json FROM preparations ORDER BY preparation_id").fetchall()
        records = tuple(_decode(row[1]) for row in rows)
        if not records or any(row[0] != record.preparation_id for row, record in zip(rows, records)):
            raise RepositoryError("state_unreadable")
        roots = {(row.basis["project_id"], row.basis["task_id"], row.basis["parent_session_id"], row.basis["target_generation"]) for row in records}
        current = connection.execute("SELECT singleton,preparation_id FROM current").fetchall()
        if len(roots) != 1 or len(current) != 1 or current[0][0] != 1 or current[0][1] not in {row.preparation_id for row in records}:
            raise RepositoryError("state_unreadable")
        return records, next(row for row in records if row.preparation_id == current[0][1])

    def read(self):
        with self._connection() as connection:
            return self._load(connection)

    @contextmanager
    def serial(self, *, initial=None, admit=lambda: None):
        fresh = _missing(self.path)
        if fresh and (initial is None or not _missing(self.lock_path)):
            raise RepositoryError("state_unreadable")
        payload = _encode(initial) if initial is not None else None
        with self._lease(create_lock=initial is not None) as lease:
            # Core/file revalidation precedes first publication as well as
            # later pointer updates. No SQLite transaction spans the callback.
            admit()
            if _missing(self.path):
                if not fresh or initial is None:
                    raise RepositoryError("state_unreadable")
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(descriptor)
                with self._connection(write=True) as connection:
                    connection.execute("PRAGMA foreign_keys=ON")
                    for statement in _SCHEMA.values():
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version=1")
                    connection.execute("INSERT INTO schema_history VALUES (1,'review-preparation-v1')")
                    connection.execute("INSERT INTO preparations VALUES (?,?)", (initial.preparation_id, payload))
                    connection.execute("INSERT INTO current VALUES (1,?)", (initial.preparation_id,))
                    self._load(connection)
            elif initial is not None:
                raise RepositoryError("state_exists")
            lease._database_identity = _identity(self._inspect())
            lease._check()
            self.read()
            yield lease
            lease._check()

    def publish(self, lease, record):
        payload = _encode(record)
        lease._check()
        with self._connection(write=True) as connection:
            records, current = self._load(connection)
            # This store's path binds the root. A new explicit preparation may
            # change conditions, but cannot adopt a different root identity.
            keys = ("project_id", "task_id", "parent_session_id", "target_generation")
            if any(record.basis[key] != current.basis[key] for key in keys):
                raise RepositoryError("binding_mismatch")
            old = next((row for row in records if row.preparation_id == record.preparation_id), None)
            if old is not None:
                if _encode(old) != payload or current.preparation_id != old.preparation_id:
                    raise RepositoryError("state_transition_invalid")
                return old  # Known publication, never move a pointer backwards.
            connection.execute("INSERT INTO preparations VALUES (?,?)", (record.preparation_id, payload))
            connection.execute("UPDATE current SET preparation_id=? WHERE singleton=1", (record.preparation_id,))
            self._load(connection)
        return record
