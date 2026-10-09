"""Bounded operational intent and reconciliation reads; no new core schema.

The lease spans independent effects, never a core or operational transaction.
Only structural references, hashes, IDs and phases enter this journal.
"""

from contextlib import contextmanager, closing
from dataclasses import asdict, dataclass, replace
import json
import os
import re

from .review_wait_runtime.review_wait_repository import ReviewWaitRepository, RepositoryError, _missing, _identity
from .review_wait_runtime.review_wait_basis import parse_basis
from .session_identity import is_session_id
from .completion import FULL_GIT_OBJECT_ID
from .storage import connect_initialized_readonly
from . import review_session_repository


_SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 1), migration_name TEXT NOT NULL CHECK(migration_name = 'review-finalization-v1'))",
    "intent": "CREATE TABLE intent (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), payload TEXT NOT NULL CHECK(length(payload) <= 32768))",
}
_PHASES = {"not_started": 0, "dispatching": 1, "succeeded": 2}
_REVIEWER_BLOCKERS = {None, "finalization_reviewer_failed", "finalization_reviewer_unknown",
                     "finalization_reviewer_mismatch"}


@dataclass(frozen=True)
class FinalizationRecord:
    basis: dict
    packet_path: str
    packet_digest: str
    result_paths: tuple[str, ...]
    branch: str
    registration: str = "not_started"
    commit: str = "not_started"
    completion: str = "not_started"
    originals: tuple[tuple[str, str], ...] = ()
    receipt_ids: tuple[str, ...] = ()
    candidate: str | None = None
    reviewer_observations: tuple[tuple[str, str, str], ...] = ()
    reviewer_blocker: str | None = None

    def __post_init__(self):
        try:
            parse_basis(json.dumps({"ok": True, "status": "review_wait_basis", "basis": self.basis}).encode(),
                        wait_id="finalization", task_id=self.basis["task_id"], parent_thread_id=self.basis["parent_thread_id"])
            if self.basis["target_kind"] != "git_snapshot":
                raise ValueError
            if not re.fullmatch(r"[0-9a-f]{64}", self.packet_digest):
                raise ValueError
            if (type(self.result_paths) is not tuple or not 1 <= len(self.result_paths) <= 8
                    or len(set(self.result_paths)) != len(self.result_paths) or self.packet_path in self.result_paths):
                raise ValueError
            for path in (self.packet_path, *self.result_paths):
                if (type(path) is not str or not path or len(path) > 1024 or "\\" in path
                        or ":" in path or any(p in ("", ".", "..") for p in path.split("/"))
                        or any(ord(c) < 32 for c in path)):
                    raise ValueError
            if (type(self.branch) is not str or not self.branch.startswith("refs/heads/")
                    or len(self.branch) > 4096 or any(ord(c) < 32 for c in self.branch)):
                raise ValueError
            if any(value not in _PHASES for value in (self.registration, self.commit, self.completion)):
                raise ValueError
            if type(self.originals) is not tuple or type(self.receipt_ids) is not tuple:
                raise ValueError
            for pair in self.originals:
                if (type(pair) is not tuple or len(pair) != 2 or not is_session_id(pair[0])
                        or not re.fullmatch(r"[a-f0-9]{64}", pair[1])):
                    raise ValueError
            if self.registration != "not_started" and len(self.originals) != len(self.result_paths):
                raise ValueError
            if self.registration == "succeeded" and len(self.receipt_ids) != len(self.result_paths):
                raise ValueError
            if any(not re.fullmatch(r"tg_review_receipt_[a-f0-9]{16}", value) for value in self.receipt_ids):
                raise ValueError
            if self.candidate is not None and not FULL_GIT_OBJECT_ID.fullmatch(self.candidate):
                raise ValueError
            if self.commit != "not_started" and (self.registration != "succeeded" or self.candidate is None):
                raise ValueError
            if self.completion != "not_started" and self.commit != "succeeded":
                raise ValueError
            validate_reviewer_observations(self.reviewer_observations, allow_empty=True)
            if self.reviewer_blocker not in _REVIEWER_BLOCKERS:
                raise ValueError
            if any(item[2] != "completed" for item in self.reviewer_observations) and self.reviewer_blocker is None:
                raise ValueError
        except Exception:
            raise RepositoryError("invalid_snapshot") from None


def validate_reviewer_observations(observations, *, allow_empty=False):
    if type(observations) is not tuple or not (0 if allow_empty else 1) <= len(observations) <= 8:
        raise RepositoryError("invalid_snapshot")
    for item in observations:
        if (type(item) is not tuple or len(item) != 3 or not is_session_id(item[0])
                or type(item[1]) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+\-]{0,255}", item[1])
                or item[2] not in ("completed", "failed", "interrupted", "unknown")):
            raise RepositoryError("invalid_snapshot")
    if len({item[0] for item in observations}) != len(observations):
        raise RepositoryError("invalid_snapshot")


def _encode(record):
    if type(record) is not FinalizationRecord:
        raise RepositoryError("invalid_snapshot")
    # Revalidate mutable nested mappings before every durable write.
    FinalizationRecord(**asdict(record))
    raw = json.dumps(asdict(record), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(raw) > 32768:
        raise RepositoryError("invalid_snapshot")
    return raw


class FinalizationRepository(ReviewWaitRepository):
    @contextmanager
    def serial(self, *, initial=None):
        fresh = _missing(self.path)
        if fresh and (initial is None or not _missing(self.lock_path)):
            raise RepositoryError("state_unreadable")
        with self._lease(create_lock=initial is not None) as lease:
            if _missing(self.path):
                if initial is None or not fresh:
                    raise RepositoryError("state_unreadable")
                payload = _encode(initial)
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(descriptor)
                with self._connection(write=True) as connection:
                    for statement in _SCHEMA.values():
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version = 1")
                    connection.execute("INSERT INTO schema_history VALUES (1, 'review-finalization-v1')")
                    connection.execute("INSERT INTO intent VALUES (1, ?)", (payload,))
            elif initial is not None:
                raise RepositoryError("state_exists")
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
                    or connection.execute("SELECT * FROM schema_history").fetchall() != [(1, "review-finalization-v1")]):
                raise ValueError
            rows = connection.execute("SELECT singleton, payload FROM intent").fetchall()
            if len(rows) != 1 or rows[0][0] != 1:
                raise ValueError
            value = json.loads(rows[0][1])
            value["result_paths"] = tuple(value["result_paths"])
            value["receipt_ids"] = tuple(value["receipt_ids"])
            value["originals"] = tuple(tuple(pair) for pair in value["originals"])
            value["reviewer_observations"] = tuple(tuple(item) for item in value["reviewer_observations"])
            record = FinalizationRecord(**value)
            if _encode(record) != rows[0][1]:
                raise ValueError
            return record
        except (ValueError, TypeError, KeyError, RecursionError):
            raise RepositoryError("state_unreadable") from None

    def read(self):
        with self._connection() as connection:
            return self._load(connection)

    def advance(self, lease, **changes):
        lease._check()
        if not set(changes) <= {"registration", "commit", "completion", "originals", "receipt_ids", "candidate",
                               "reviewer_observations", "reviewer_blocker"}:
            raise RepositoryError("state_transition_invalid")
        with self._connection(write=True) as connection:
            before = self._load(connection)
            after = replace(before, **changes)
            for name in ("registration", "commit", "completion"):
                if not 0 <= _PHASES[getattr(after, name)] - _PHASES[getattr(before, name)] <= 1:
                    raise RepositoryError("state_transition_invalid")
            for name in ("originals", "receipt_ids", "candidate"):
                if getattr(before, name) and getattr(before, name) != getattr(after, name):
                    raise RepositoryError("state_transition_invalid")
            if before.reviewer_blocker is not None and after.reviewer_blocker != before.reviewer_blocker:
                raise RepositoryError("state_transition_invalid")
            if before.reviewer_observations:
                retained = {item[0]: item for item in after.reviewer_observations}
                if any(old[0] not in retained or old[:2] != retained[old[0]][:2]
                       or old[2] != "completed" and old != retained[old[0]]
                       for old in before.reviewer_observations):
                    raise RepositoryError("state_transition_invalid")
            connection.execute("UPDATE intent SET payload = ? WHERE singleton = 1", (_encode(after),))
            return after

    def observe_reviewers(self, lease, observations):
        """Persist exact terminal observations; failed/unknown turns never become PASS on retry."""
        validate_reviewer_observations(observations)
        before = self.read()
        blocker = before.reviewer_blocker
        retained = {item[0]: item for item in before.reviewer_observations}
        original_ids = {item[0] for item in before.originals}
        for item in observations:
            old = retained.get(item[0])
            if ((old is not None and old[:2] != item[:2])
                    or original_ids and item[0] not in original_ids):
                return self.advance(lease, reviewer_blocker=blocker or "finalization_reviewer_mismatch")
            retained[item[0]] = old if old is not None and old[2] != "completed" else item
        observations = tuple(sorted(retained.values()))
        if any(item[2] in ("failed", "interrupted") for item in observations):
            blocker = blocker or "finalization_reviewer_failed"
        elif any(item[2] == "unknown" for item in observations):
            blocker = blocker or "finalization_reviewer_unknown"
        if (observations, blocker) == (before.reviewer_observations, before.reviewer_blocker):
            return before
        return self.advance(lease, reviewer_observations=observations, reviewer_blocker=blocker)


def registered_originals(target, record, receipt_ids):
    """Match actual immutable core bindings after a lost registration response."""
    with closing(connect_initialized_readonly(target)) as connection:
        bindings = review_session_repository.read_bindings(connection, receipt_ids=set(receipt_ids))
        found = []
        for session, digest in record.originals:
            matches = [key for key, item in bindings.items() if
                       (item.session_id, item.original_result_digest, item.execution_id, item.binding_source)
                       == (session, digest, record.basis["execution_id"], "handoff")]
            if len(matches) > 1:
                raise RepositoryError("state_unreadable")
            if matches:
                found.append(matches[0])
        if found and len(found) != len(record.originals):
            raise RepositoryError("finalization_registration_incomplete")
        return tuple(found)


def registered_findings(target, record, receipt_ids):
    """Project every registered Finding, including resolved low-severity entries."""
    from .evidence_validation_repository import validate_selected_task_receipt_evidence
    from .review_repository import validate_stored_review_finding_projection
    if not receipt_ids:
        return []
    with closing(connect_initialized_readonly(target)) as connection:
        rows = connection.execute("""SELECT finding.*, receipt.target_generation, receipt.reviewer_key
            FROM review_findings AS finding
            JOIN review_receipts AS receipt ON receipt.review_receipt_id=finding.review_receipt_id
            WHERE receipt.project_id=? AND receipt.task_id=? AND receipt.review_receipt_id IN
              (SELECT value FROM json_each(?)) ORDER BY finding.created_at, finding.review_finding_id""",
            (record.basis["project_id"], record.basis["task_id"], json.dumps(receipt_ids))).fetchall()
        validate_selected_task_receipt_evidence(connection, project_id=record.basis["project_id"],
            task_id=record.basis["task_id"], review_receipt_ids=set(receipt_ids),
            review_finding_ids={row["review_finding_id"] for row in rows}, verification_receipt_ids=set())
        for row in rows:
            validate_stored_review_finding_projection(row)
        return [dict(row) for row in rows]
