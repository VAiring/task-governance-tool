"""Version-two operational rounds with one fixed Git/completion effect root."""

from dataclasses import asdict, dataclass, replace
import json

from .review_finalization_repository import FinalizationRecord, _encode, _PHASES, _SCHEMA as OLD_SCHEMA
from .review_wait_runtime.review_wait_repository import RepositoryError


SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 2), migration_name TEXT NOT NULL CHECK(migration_name = 'review-finalization-v2'))",
    "intent": OLD_SCHEMA["intent"],
    "rounds": "CREATE TABLE rounds (sequence INTEGER PRIMARY KEY CHECK(sequence BETWEEN 0 AND 64), continuation_id TEXT UNIQUE, payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) <= 32768))",
    "current_round": "CREATE TABLE current_round (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), sequence INTEGER NOT NULL REFERENCES rounds(sequence))",
}


@dataclass(frozen=True)
class EffectRoot:
    basis: dict
    packet_path: str
    packet_digest: str
    result_paths: tuple[str, ...]
    branch: str
    candidate: str | None = None
    commit: str = "not_started"
    completion: str = "not_started"

    def __post_init__(self):
        FinalizationRecord(self.basis, self.packet_path, self.packet_digest, self.result_paths, self.branch)
        from .completion import FULL_GIT_OBJECT_ID
        if (self.commit not in _PHASES or self.completion not in _PHASES
            or self.candidate is not None and not FULL_GIT_OBJECT_ID.fullmatch(self.candidate)
            or self.commit != "not_started" and self.candidate is None
            or self.completion != "not_started" and self.commit != "succeeded"):
            raise RepositoryError("invalid_snapshot")


def _root_encode(root):
    return json.dumps(asdict(EffectRoot(**asdict(root))), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _record_decode(raw):
    try:
        value = json.loads(raw)
        for name in ("result_paths", "receipt_ids", "active_sessions", "retained_receipt_ids"):
            if name in value:
                value[name] = tuple(value[name])
        for name in ("originals", "reviewer_observations", "original_locations"):
            if name in value:
                value[name] = tuple(tuple(pair) for pair in value[name])
        record = FinalizationRecord(**value)
        if _encode(record) != raw:
            raise ValueError
        return record
    except (ValueError, KeyError, TypeError, RecursionError, RepositoryError):
        raise RepositoryError("state_unreadable") from None


def _parts(connection):
    objects = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    if (objects != [("table",name,name,sql) for name,sql in sorted(SCHEMA.items())]
        or connection.execute("PRAGMA user_version").fetchone() != (2,)
        or connection.execute("SELECT * FROM schema_history").fetchall() != [(2,"review-finalization-v2")]):
        raise RepositoryError("state_unreadable")
    roots = connection.execute("SELECT singleton,payload FROM intent").fetchall()
    try:
        if len(roots) != 1 or roots[0][0] != 1:
            raise ValueError
        value = json.loads(roots[0][1])
        value["result_paths"] = tuple(value["result_paths"])
        root = EffectRoot(**value)
        if _root_encode(root) != roots[0][1]:
            raise ValueError
    except (ValueError, KeyError, TypeError, RecursionError):
        raise RepositoryError("state_unreadable") from None
    rows = connection.execute("SELECT sequence,continuation_id,payload FROM rounds ORDER BY sequence").fetchall()
    current = connection.execute("SELECT singleton,sequence FROM current_round").fetchall()
    if not 1 <= len(rows) <= 65 or [row[0] for row in rows] != list(range(len(rows))) or current != [(1,len(rows)-1)]:
        raise RepositoryError("state_unreadable")
    history = tuple(_record_decode(row[2]) for row in rows)
    for row, record in zip(rows,history):
        if (record.basis != root.basis or record.branch != root.branch or row[1] != record.continuation_id
            or row[0] == 0 and record.continuation_id is not None
            or row[0] > 0 and (record.continuation_sequence != row[0] or record.preparation_id != history[1].preparation_id)):
            raise RepositoryError("state_unreadable")
        if record.replaced_registration_round is not None:
            prior = history[record.replaced_registration_round]
            if prior.registration != "dispatching" or prior.commit != "not_started" or prior.completion != "not_started":
                raise RepositoryError("state_unreadable")
    if (history[0].packet_path, history[0].packet_digest, history[0].result_paths) != (root.packet_path,root.packet_digest,root.result_paths):
        raise RepositoryError("state_unreadable")
    return root, history


def records(connection):
    return _parts(connection)[1]


def load(connection):
    root, history = _parts(connection)
    return replace(history[-1], candidate=root.candidate, commit=root.commit, completion=root.completion)


def append(connection, record):
    from .review_finalization_repository import FinalizationRepository
    if type(record) is not FinalizationRecord or record.continuation_id is None or record.registration != "not_started" or record.originals or record.receipt_ids:
        raise RepositoryError("state_transition_invalid")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 1:
        before = FinalizationRepository._load(connection)
        # Unknown dispatch stages are never converted into a new round.
        if (any(value == "dispatching" for value in (before.commit,before.completion))
            or before.registration == "dispatching" and record.replaced_registration_round != 0):
            raise RepositoryError("review_continuation_effect_unknown")
        original = _encode(before)
        root = EffectRoot(**{name:getattr(before,name) for name in EffectRoot.__dataclass_fields__})
        connection.execute("DROP TABLE schema_history")
        for name,sql in SCHEMA.items():
            if name != "intent":
                connection.execute(sql)
        connection.execute("PRAGMA user_version=2")
        connection.execute("INSERT INTO schema_history VALUES (2,'review-finalization-v2')")
        connection.execute("UPDATE intent SET payload=? WHERE singleton=1", (_root_encode(root),))
        connection.execute("INSERT INTO rounds VALUES (0,NULL,?)", (original,))
        connection.execute("INSERT INTO current_round VALUES (1,0)")
    root, history = _parts(connection)
    existing = next((row for row in history if row.continuation_id == record.continuation_id),None)
    if existing is not None:
        if (existing.basis,existing.packet_path,existing.packet_digest,existing.result_paths,existing.active_sessions,existing.replaced_registration_round) != (record.basis,record.packet_path,record.packet_digest,record.result_paths,record.active_sessions,record.replaced_registration_round):
            raise RepositoryError("binding_mismatch")
        return load(connection)
    if (len(history)>=65 or record.continuation_sequence!=len(history)
        or record.basis!=root.basis or record.branch!=root.branch
        or root.completion != "not_started" or root.commit == "dispatching"):
        raise RepositoryError("state_transition_invalid")
    if history[-1].registration == "dispatching" and record.replaced_registration_round != len(history) - 1:
        raise RepositoryError("review_continuation_effect_unknown")
    connection.execute("INSERT INTO rounds VALUES (?,?,?)", (record.continuation_sequence,record.continuation_id,_encode(record)))
    connection.execute("UPDATE current_round SET sequence=? WHERE singleton=1", (record.continuation_sequence,))
    return load(connection)


def save_view(connection, before, after):
    root, history = _parts(connection)
    if before != load(connection):
        raise RepositoryError("state_transition_invalid")
    updated_root = replace(root,candidate=after.candidate,commit=after.commit,completion=after.completion)
    round_record = replace(after,candidate=history[-1].candidate,commit=history[-1].commit,completion=history[-1].completion)
    connection.execute("UPDATE intent SET payload=? WHERE singleton=1", (_root_encode(updated_root),))
    connection.execute("UPDATE rounds SET payload=? WHERE sequence=?", (_encode(round_record),after.continuation_sequence))
    load(connection)
