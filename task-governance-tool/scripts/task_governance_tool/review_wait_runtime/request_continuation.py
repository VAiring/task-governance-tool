"""Closed schema-two request rounds; no host call, thread join or core access."""

from dataclasses import asdict, dataclass
import json
import re

from task_governance_tool import review_continuation as values
from .review_wait_repository import RepositoryError


SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 2), migration_name TEXT NOT NULL CHECK(migration_name = 'review-wait-request-v2'))",
    "rounds": "CREATE TABLE rounds (ordinal INTEGER PRIMARY KEY CHECK(ordinal > 0), preparation_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 64), continuation_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) <= 32768), UNIQUE(preparation_id,sequence))",
    "current_round": "CREATE TABLE current_round (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), ordinal INTEGER NOT NULL REFERENCES rounds(ordinal))",
}
PHASES = ("fenced", "settled", "prepared", "published")


@dataclass(frozen=True)
class RequestRound:
    preparation_id: str
    continuation_id: str
    continuation_sequence: int
    basis_digest: str
    wait_basis_digest: str
    selection_digest: str
    expected_attempt: int
    expected_request_digest: str | None
    old_automation_id: str | None
    old_request_phase: str | None
    phase: str = "fenced"
    worker_stop_identity: str | None = None
    packet_path: str | None = None
    packet_digest: str | None = None
    result_paths: tuple[str, ...] = ()
    answer_protocol: str = "legacy"
    collection_basis_digests: tuple[str, ...] = ()
    legacy_migration: dict | None = None
    rejected_answer_replacement: dict | None = None

    def __post_init__(self):
        try:
            if (not values.identifier(self.preparation_id, "tg_review_preparation_")
                or not values.identifier(self.continuation_id, "tg_review_continuation_")
                or not values._integer(self.continuation_sequence, 1, 64)
                or not values._digest(self.basis_digest) or not values._digest(self.wait_basis_digest) or not values._digest(self.selection_digest)
                or not values._integer(self.expected_attempt, 0, 64)
                or self.phase not in PHASES):
                raise ValueError
            if type(self.answer_protocol) is not str or self.answer_protocol not in ("legacy", "review-answer-v1"):
                raise ValueError
            if self.legacy_migration is not None:
                from task_governance_tool.review_answer_dedication import decode
                if self.answer_protocol != "review-answer-v1":
                    raise ValueError
                decode(self.legacy_migration)
            if self.rejected_answer_replacement is not None:
                from task_governance_tool.review_answer_rejection_values import validate
                if self.answer_protocol != "review-answer-v1" or self.legacy_migration is not None:
                    raise ValueError
                validate(self.rejected_answer_replacement)
            if (self.expected_attempt == 0) != (self.expected_request_digest is None):
                raise ValueError
            if (self.expected_attempt == 0) != (self.old_request_phase is None):
                raise ValueError
            if self.old_request_phase is not None and self.old_request_phase not in ("creating", "created", "prepared", "started", "closing", "closed", "failed", "unknown"):
                raise ValueError
            if self.expected_request_digest is not None and not values._digest(self.expected_request_digest):
                raise ValueError
            if self.old_automation_id is not None:
                if type(self.old_automation_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", self.old_automation_id):
                    raise ValueError
            if self.worker_stop_identity is not None and not values._digest(self.worker_stop_identity):
                raise ValueError
            if self.phase == "fenced" and self.worker_stop_identity is not None:
                raise ValueError
            if self.phase != "fenced" and self.old_request_phase not in (None, "created") and self.worker_stop_identity is None:
                raise ValueError
            if self.expected_attempt == 0 and (self.old_automation_id is not None or self.worker_stop_identity is not None):
                raise ValueError
            if type(self.result_paths) is not tuple:
                raise ValueError
            if (self.phase == "fenced" and self.packet_digest is not None
                or self.phase in ("prepared", "published") and self.packet_digest is None):
                raise ValueError
            if self.packet_digest is not None and not values._digest(self.packet_digest):
                raise ValueError
            if not 1 <= len(self.result_paths) <= 8:
                raise ValueError
            if type(self.collection_basis_digests) is not tuple:
                raise ValueError
            if self.collection_basis_digests:
                if (self.answer_protocol != "review-answer-v1" or self.phase == "fenced"
                    or self.packet_digest is None or len(self.collection_basis_digests) != len(self.result_paths)
                    or any(type(digest) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                           for digest in self.collection_basis_digests)):
                    raise ValueError
            from pathlib import Path
            from task_governance_tool.review_handoff_transport import relative_path
            paths = (self.packet_path, *self.result_paths)
            if len(set(paths)) != len(paths):
                raise ValueError
            for path in paths:
                if type(path) is not str or not path.endswith(".json"):
                    raise ValueError
                relative_path(Path("structural-root"), path)
        except Exception:
            raise RepositoryError("invalid_snapshot") from None


def encode(record):
    if type(record) is not RequestRound:
        raise RepositoryError("invalid_snapshot")
    value = asdict(RequestRound(**asdict(record)))
    if record.answer_protocol == "legacy":
        del value["answer_protocol"]
    if not record.collection_basis_digests:
        # Preserve legacy and already-issued unanchored collected round bytes.
        # Operational admission rejects an unanchored collected ready round.
        del value["collection_basis_digests"]
    if record.legacy_migration is None:
        del value["legacy_migration"]
    if record.rejected_answer_replacement is None:
        del value["rejected_answer_replacement"]
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > 32768:
        raise RepositoryError("invalid_snapshot")
    return raw


def decode(raw):
    try:
        value = json.loads(raw)
        optional = {"legacy_migration", "rejected_answer_replacement"}
        fields = set(RequestRound.__dataclass_fields__) - optional
        observed_fields = set(value) - optional if type(value) is dict else set()
        if type(value) is not dict or observed_fields not in (
            fields, fields - {"collection_basis_digests"},
            fields - {"collection_basis_digests", "answer_protocol"}):
            raise ValueError
        value["result_paths"] = tuple(value["result_paths"])
        if "collection_basis_digests" in value:
            if type(value["collection_basis_digests"]) is not list:
                raise ValueError
            value["collection_basis_digests"] = tuple(value["collection_basis_digests"])
        record = RequestRound(**value)
        if encode(record) != raw:
            raise ValueError
        return record
    except (ValueError, KeyError, TypeError, RecursionError, RepositoryError):
        raise RepositoryError("state_unreadable") from None


def load_rounds(connection):
    rows = connection.execute("SELECT ordinal,preparation_id,sequence,continuation_id,payload FROM rounds ORDER BY ordinal").fetchall()
    if [row[0] for row in rows] != list(range(1, len(rows) + 1)):
        raise RepositoryError("state_unreadable")
    rounds = tuple(decode(row[4]) for row in rows)
    groups = {}
    for row, record in zip(rows, rounds):
        if row[1:4] != (record.preparation_id, record.continuation_sequence, record.continuation_id):
            raise RepositoryError("state_unreadable")
        group = groups.setdefault(record.preparation_id, [])
        group.append(record.continuation_sequence)
        if group != list(range(1, len(group) + 1)) or len(group) > 64:
            raise RepositoryError("state_unreadable")
    current = connection.execute("SELECT singleton,ordinal FROM current_round").fetchall()
    if (current != ([(1, len(rows))] if rows else [])
        or any(row.phase != "published" for row in rounds[:-1])):
        raise RepositoryError("state_unreadable")
    return rounds
