"""Bounded operational intent and reconciliation reads; no new core schema.

The lease spans independent effects, never a core or operational transaction.
Only structural references, hashes, IDs and phases enter this journal.
"""

from contextlib import contextmanager, closing
from dataclasses import asdict, dataclass, replace
import json
import os
import re
import sys
import time

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
    continuation_id: str | None = None
    continuation_sequence: int = 0
    preparation_id: str | None = None
    active_sessions: tuple[str, ...] = ()
    retained_receipt_ids: tuple[str, ...] = ()
    original_locations: tuple[tuple[str, str], ...] = ()
    replaced_registration_round: int | None = None

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
            from .review_continuation import identifier
            continued = self.continuation_id is not None
            if self.replaced_registration_round is not None and (
                not continued or type(self.replaced_registration_round) is not int
                or self.replaced_registration_round != self.continuation_sequence - 1):
                raise ValueError
            if continued:
                if (not identifier(self.continuation_id, "tg_review_continuation_")
                    or not identifier(self.preparation_id, "tg_review_preparation_")
                    or type(self.continuation_sequence) is not int or not 1 <= self.continuation_sequence <= 64
                    or type(self.active_sessions) is not tuple or not 1 <= len(self.active_sessions) <= 8
                    or len(set(self.active_sessions)) != len(self.active_sessions)
                    or any(not is_session_id(value) for value in self.active_sessions)
                    or len(self.active_sessions) != len(self.result_paths)):
                    raise ValueError
            elif (self.preparation_id is not None or self.continuation_sequence != 0 or self.active_sessions
                  or self.retained_receipt_ids or self.original_locations):
                raise ValueError
            if self.registration != "not_started" and (len(self.originals) != len(self.result_paths) if not continued else
                not len(self.result_paths) <= len(self.originals) <= 8):
                raise ValueError
            if self.registration == "succeeded" and len(self.receipt_ids) != len(self.originals):
                raise ValueError
            if any(not re.fullmatch(r"tg_review_receipt_[a-f0-9]{16}", value) for value in self.receipt_ids):
                raise ValueError
            if self.candidate is not None and not FULL_GIT_OBJECT_ID.fullmatch(self.candidate):
                raise ValueError
            if self.commit != "not_started" and ((not continued and self.registration != "succeeded") or self.candidate is None):
                raise ValueError
            if self.completion != "not_started" and self.commit != "succeeded":
                raise ValueError
            validate_reviewer_observations(self.reviewer_observations, allow_empty=True)
            if self.reviewer_blocker not in _REVIEWER_BLOCKERS:
                raise ValueError
            if any(item[2] != "completed" for item in self.reviewer_observations) and self.reviewer_blocker is None:
                raise ValueError
            if type(self.retained_receipt_ids) is not tuple or len(set(self.retained_receipt_ids)) != len(self.retained_receipt_ids) or any(not identifier(value, "tg_review_receipt_") for value in self.retained_receipt_ids):
                raise ValueError
            if type(self.original_locations) is not tuple or len(self.original_locations) > 8:
                raise ValueError
            from .review_handoff_transport import relative_path
            from pathlib import Path
            for location in self.original_locations:
                if type(location) is not tuple or len(location) != 2:
                    raise ValueError
                for value in location:
                    relative_path(Path("structural-root"), value)
            if continued and self.registration != "not_started" and len(self.original_locations) != len(self.originals):
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
    value = asdict(record)
    if record.replaced_registration_round is None:
        value.pop("replaced_registration_round")
    if record.continuation_id is None:
        for key in ("continuation_id", "continuation_sequence", "preparation_id", "active_sessions", "retained_receipt_ids", "original_locations"):
            value.pop(key)
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(raw) > 32768:
        raise RepositoryError("invalid_snapshot")
    return raw


class FinalizationRepository(ReviewWaitRepository):
    # A scheduled preflight can read while its old worker retains an observation.
    # SQLite's bounded busy wait settles that short read/commit overlap; it does
    # not replay a transaction or any externally visible finalization operation.
    _connection_timeout = 1

    @contextmanager
    def serial(self, *, initial=None, wait_for_observation=False):
        fresh = _missing(self.path)
        if fresh and (initial is None or not _missing(self.lock_path)):
            raise RepositoryError("state_unreadable")
        # Wait only for a short observation writer's OS-lock admission, never
        # replay a transaction, finalization stage or host effect.
        deadline = time.monotonic() + (1 if wait_for_observation else 0)
        while True:
            manager = self._lease(create_lock=initial is not None)
            try:
                lease = manager.__enter__()
                break
            except RepositoryError as error:
                if error.code != "writer_busy" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        try:
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
        except BaseException:
            manager.__exit__(*sys.exc_info())
            raise
        else:
            manager.__exit__(None, None, None)

    @staticmethod
    def _load(connection):
        try:
            if connection.execute("PRAGMA user_version").fetchone() == (2,):
                from .review_finalization_continuation_repository import load
                return load(connection)
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
            if "continuation_id" in value:
                raise ValueError
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
                               "reviewer_observations", "reviewer_blocker", "retained_receipt_ids", "original_locations"}:
            raise RepositoryError("state_transition_invalid")
        with self._connection(write=True) as connection:
            before = self._load(connection)
            after = replace(before, **changes)
            for name in ("registration", "commit", "completion"):
                if not 0 <= _PHASES[getattr(after, name)] - _PHASES[getattr(before, name)] <= 1:
                    raise RepositoryError("state_transition_invalid")
            for name in ("originals", "receipt_ids", "candidate", "retained_receipt_ids", "original_locations"):
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
            if connection.execute("PRAGMA user_version").fetchone() == (2,):
                from .review_finalization_continuation_repository import save_view
                save_view(connection, before, after)
            else:
                if after.continuation_id is not None or after.original_locations or after.retained_receipt_ids:
                    raise RepositoryError("state_transition_invalid")
                connection.execute("UPDATE intent SET payload = ? WHERE singleton = 1", (_encode(after),))
            return after

    def append_round(self, lease, record):
        from .review_finalization_continuation_repository import append
        lease._check()
        with self._connection(write=True) as connection:
            return append(connection, record)

    def read_rounds(self):
        from .review_finalization_continuation_repository import records
        with self._connection() as connection:
            self._load(connection)
            return records(connection) if connection.execute("PRAGMA user_version").fetchone() == (2,) else (self._load(connection),)

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
    with closing(connect_initialized_readonly(target, operation_local=True, review_task_id=record.basis["task_id"])) as connection:
        receipts = connection.execute("""SELECT * FROM review_receipts
            WHERE project_id=? AND task_id=? AND review_receipt_id IN (SELECT value FROM json_each(?))""",
            (record.basis["project_id"], record.basis["task_id"], json.dumps(sorted(receipt_ids)))).fetchall()
        matching_ids = {row["review_receipt_id"] for row in receipts if all(
            row["target_" + key] == record.basis["target_" + key]
            for key in ("generation", "kind", "value", "base_revision"))}
        bindings = review_session_repository.read_bindings(connection, receipt_ids=matching_ids)
        expected_continuations = None
        judgments = {}
        if record.continuation_id is not None:
            from .review_continuation_repository import read_original_sources
            expected_continuations = read_original_sources(connection, record.continuation_id,
                                                          record.active_sessions).continuations
            judgments = {row["review_receipt_id"]: row["continuation_id"] for row in connection.execute(
                "SELECT review_receipt_id,continuation_id FROM review_receipt_judgments WHERE review_receipt_id IN (SELECT value FROM json_each(?))",
                (json.dumps(sorted(receipt_ids)),))}
        found = []
        for session, digest in record.originals:
            matches = [key for key, item in bindings.items() if
                       (item.session_id, item.original_result_digest, item.execution_id, item.binding_source)
                       == (session, digest, record.basis["execution_id"], "handoff")
                       and (expected_continuations is None or key in judgments
                            and judgments[key] == expected_continuations.get(session))]
            if len(matches) > 1:
                raise RepositoryError("state_unreadable")
            if matches:
                found.append(matches[0])
        if found and len(found) != len(record.originals):
            raise RepositoryError("finalization_registration_incomplete")
        return tuple(found)


def reported_review_evidence(target, record, registered_receipt_ids):
    """Read report-only history; never expand registration/replay identities.

    Roots are this batch's observed originals and explicitly retained Receipts.
    Only registered predecessor edges reachable from those roots add history;
    a pending selection is not a successor judgment. One read transaction owns
    graph validation, receipt selection and complete Finding projection.
    """
    from .evidence_validation_repository import validate_selected_task_receipt_evidence
    from .review_material_repository import review_materials
    from .review_repository import validate_stored_review_finding_projection
    from .storage import COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
    roles = {key: {"registered"} for key in registered_receipt_ids}
    for key in record.retained_receipt_ids:
        roles.setdefault(key, set()).add("retained")
    if not roles:
        return [], []
    with closing(connect_initialized_readonly(target, operation_local=True, review_task_id=record.basis["task_id"])) as connection, review_materials(
            connection, project_id=record.basis["project_id"], task_id=record.basis["task_id"]) as materials:
        successors = {}
        if record.continuation_id is not None:
            from .review_continuation import quality_identity
            from .review_continuation_repository import read_continuations, read_judgments
            history = read_continuations(connection, project_id=record.basis["project_id"],
                                        task_id=record.basis["task_id"])
            current = next((row for row in history if row.continuation_id == record.continuation_id), None)
            if (current is None or current.preparation_id != record.preparation_id
                    or current.sequence != record.continuation_sequence
                    or current.basis["parent_session_id"] != record.basis["parent_thread_id"]
                    or any(current.basis[key] != value for key, value in record.basis.items()
                           if key not in ("task_status", "parent_thread_id"))):
                raise RepositoryError("state_unreadable")
            nodes = {row.receipt_id: row for row in read_judgments(connection,
                basis=current.basis, _validated_continuations=history)}
            quality = quality_identity(current.basis)
            pending = list(roles)
            visited = set()
            while pending:
                receipt_id = pending.pop()
                if receipt_id in visited:
                    continue
                node = nodes.get(receipt_id)
                if node is None or node.quality_basis != quality:
                    raise RepositoryError("state_unreadable")
                visited.add(receipt_id)
                if node.prior_receipt_id is not None:
                    prior = node.prior_receipt_id
                    roles.setdefault(prior, set()).add("reassessment_source")
                    successors.setdefault(prior, set()).add(receipt_id)
                    pending.append(prior)
        receipt_ids = sorted(roles)
        receipts = connection.execute("""SELECT * FROM review_receipts
            WHERE project_id=? AND task_id=? AND review_receipt_id IN (SELECT value FROM json_each(?))""",
            (record.basis["project_id"], record.basis["task_id"], json.dumps(receipt_ids))).fetchall()
        if (len(receipts) != len(receipt_ids) or any(
                row["target_" + key] != record.basis["target_" + key]
                for row in receipts for key in ("generation", "kind", "value", "base_revision"))):
            raise RepositoryError("state_unreadable")
        rows = connection.execute("""SELECT finding.*, receipt.target_generation, receipt.reviewer_key
            FROM review_findings AS finding
            JOIN review_receipts AS receipt ON receipt.review_receipt_id=finding.review_receipt_id
            WHERE receipt.project_id=? AND receipt.task_id=? AND receipt.review_receipt_id IN
              (SELECT value FROM json_each(?)) ORDER BY finding.created_at, finding.review_finding_id""",
            (record.basis["project_id"], record.basis["task_id"], json.dumps(receipt_ids))).fetchall()
        size = COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        provenance = {}
        for offset in range(0, len(receipt_ids), size):
            provenance.update(validate_selected_task_receipt_evidence(connection, project_id=record.basis["project_id"],
                task_id=record.basis["task_id"], review_receipt_ids=set(receipt_ids[offset:offset + size]),
                review_finding_ids=set(), verification_receipt_ids=set(), _materials=materials))
        # Finding validation includes its parents. Do not add an unrelated
        # Receipt batch to that union or it could exceed the same bounded API.
        for offset in range(0, len(rows), size):
            validate_selected_task_receipt_evidence(connection, project_id=record.basis["project_id"],
                task_id=record.basis["task_id"], review_receipt_ids=set(),
                review_finding_ids={row["review_finding_id"] for row in rows[offset:offset + size]},
                verification_receipt_ids=set(), _materials=materials)
        for row in rows:
            validate_stored_review_finding_projection(row)
        versions = {row["review_receipt_id"]: row["review_provenance_basis_version"] for row in receipts}
        return ([dict(review_receipt_id=key, roles=sorted(roles[key]),
                      successor_receipt_ids=sorted(successors.get(key, ())),
                      review_details=_review_details(versions[key], provenance[key])) for key in receipt_ids],
                [dict(row) for row in rows])


def _review_details(version, provenance):
    """Project exact claims, without inferring them from legacy summaries."""
    if version != 2:
        return dict(status="not_recorded", provenance_version=version, checks=None, limitations=None)
    payload = provenance["answer_attestation"]["payload"]
    return dict(status="available", provenance_version=2,
                checks=list(payload["checks"]), limitations=list(payload["limitations"]))
