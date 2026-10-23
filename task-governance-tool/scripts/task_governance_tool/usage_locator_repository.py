"""Schema 8: retained source candidates and independently fenced fair attempts.

Locators are untrusted hints, separate from admitted numerical sources. Queue
updates never advance a file cursor. No filesystem reads happen in this layer;
the lifecycle caller freshly validates authorization and the physical source.
"""

from dataclasses import dataclass, field, replace
import json

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.usage_publication_repository import UsagePublicationRepository
from task_governance_tool.usage_sources import SourceLocator, validate_locator
from task_governance_tool.usage_values import UsageError


LOCATOR_PAGE_LIMIT = 64
PENDING_LOCATOR_LIMIT = 1024
_MAX_INTEGER = 2**63 - 1
_MIGRATION = """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version IN (1,2,3,4,5,6,7,8)),
        name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
          (version=2 AND name='turn_attribution') OR (version=3 AND name='immutable_usage_evidence') OR
          (version=4 AND name='review_wait_attribution') OR (version=5 AND name='incremental_collection') OR
          (version=6 AND name='response_attribution') OR (version=7 AND name='progressive_publication') OR
          (version=8 AND name='source_locators')))"""
_LOCATOR_DDL = (
    """CREATE TABLE usage_locator_sequence (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        value INTEGER NOT NULL CHECK(value>=0))""",
    """CREATE TABLE usage_locators (
        source_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES usage_sessions(thread_id),
        root_kind TEXT NOT NULL CHECK(root_kind IN ('sessions','archive')),
        root_fingerprint TEXT NOT NULL, relative_path TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0), queue_order INTEGER NOT NULL CHECK(queue_order>0))""",
    """CREATE TABLE usage_pending_locators (
        source_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
        root_kind TEXT NOT NULL CHECK(root_kind IN ('sessions','archive')),
        root_fingerprint TEXT NOT NULL, relative_path TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0), queue_order INTEGER NOT NULL CHECK(queue_order>0),
        pending_review TEXT NOT NULL)""",
    """CREATE INDEX usage_locators_by_queue ON usage_locators
        (root_kind,root_fingerprint,queue_order,source_id)""",
    """CREATE INDEX usage_pending_locators_by_queue ON usage_pending_locators
        (root_kind,root_fingerprint,queue_order,source_id)""",
)


@dataclass(frozen=True)
class LocatorEntry:
    locator: SourceLocator
    revision: int
    queue_order: int
    pending_review: dict | None = field(default=None, repr=False)


def _hex(value):
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _pending_document(value):
    # The authorization helper owns the closed actual-assignment representation.
    from task_governance_tool.usage_locator_admission import validate_pending_review
    validate_pending_review(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _entry(row, *, pending=False):
    if row is None:
        return None
    try:
        locator = SourceLocator(**{key: row[key] for key in SourceLocator.__dataclass_fields__})
        validate_locator(locator)
        if any(type(row[key]) is not int or not 0 < row[key] <= _MAX_INTEGER
               for key in ("revision", "queue_order")):
            raise ValueError
        review = json.loads(row["pending_review"]) if pending else None
        if pending:
            _pending_document(review)
        return LocatorEntry(locator, row["revision"], row["queue_order"], review)
    except (UsageError, ValueError, TypeError, KeyError):
        raise UsageError("usage_schema_invalid") from None


def _table(pending):
    if type(pending) is not bool:
        raise UsageError("source_unreadable")
    return "usage_pending_locators" if pending else "usage_locators"


def _root_fingerprints(value):
    if (type(value) is not dict or len(value) > 2
            or any(kind not in {"sessions", "archive"} or not _hex(fingerprint)
                   for kind, fingerprint in value.items())):
        raise UsageError("source_unreadable")
    return tuple(value.items())


class UsageLocatorRepository(UsagePublicationRepository):
    schema_statements = (_MIGRATION, *UsagePublicationRepository.schema_statements[1:], *_LOCATOR_DDL)
    migrations = (*UsagePublicationRepository.migrations, (8, "source_locators"))
    _previous_repositories = (UsagePublicationRepository, *UsagePublicationRepository._previous_repositories)

    def _after_numerical_upgrade(self, connection, previous):
        if previous is not UsagePublicationRepository:
            super()._after_numerical_upgrade(connection, previous)
        self._before_locator_marker(connection)

    def _before_locator_marker(self, connection):
        """Migration rollback seam; existing numerical history is unchanged."""

    def _before_locator_commit(self, connection):
        """Candidate/queue rollback seam, independent of the numerical cursor."""

    def _record_discovery_loss(self, connection, cycle, *, locator_root_fingerprints=None):
        # A retained candidate is checked directly. Inventory omission says
        # nothing about it, but only within this event's current source roots.
        # This exclusion never clears a previously observed gap.
        roots = _root_fingerprints({} if locator_root_fingerprints is None else locator_root_fingerprints)
        matches = " OR ".join("(locator.root_kind=? AND locator.root_fingerprint=?)" for _ in roots) or "0"
        connection.execute("INSERT OR IGNORE INTO usage_diagnostics SELECT source_id,'source_unreadable' FROM usage_sources source "
                           "WHERE NOT EXISTS (SELECT 1 FROM usage_locators locator WHERE locator.source_id=source.source_id AND ("
                           + matches + ")) AND source.source_id NOT IN (SELECT source_id FROM usage_discovered WHERE cycle=?)",
                           (*[value for pair in roots for value in pair], cycle))

    def missing_sessions(self, sessions, cycle):
        # This legacy fallback reports only never-admitted participants. Direct
        # attempts diagnose missing/invalid known sources through existing gaps.
        with self.connection() as connection:
            located = {row[0] for row in connection.execute("SELECT DISTINCT thread_id FROM usage_sources")}
        return bool(set(sessions) - located)

    def _next_order(self, connection):
        row = connection.execute("SELECT value FROM usage_locator_sequence WHERE singleton=1").fetchone()
        if row is None:
            # Lazy initialization belongs to the first candidate transaction.
            # Losing a sequence after any candidate exists is not a reset hint.
            if (connection.execute("SELECT 1 FROM usage_locators LIMIT 1").fetchone()
                    or connection.execute("SELECT 1 FROM usage_pending_locators LIMIT 1").fetchone()):
                raise UsageError("usage_schema_invalid")
            connection.execute("INSERT INTO usage_locator_sequence VALUES (1,0)")
            value = 0
        else:
            value = row[0]
        if type(value) is not int or not 0 <= value < _MAX_INTEGER:
            raise UsageError("usage_schema_invalid")
        connection.execute("UPDATE usage_locator_sequence SET value=? WHERE singleton=1", (value + 1,))
        return value + 1

    def _get_locator(self, connection, source_id, *, pending=False):
        return self._read_locator(connection.execute("SELECT * FROM " + _table(pending) + " WHERE source_id=?",
                                                     (source_id,)).fetchone(), pending=pending)

    def _read_locator(self, row, *, pending=False):
        entry = _entry(row, pending=pending)
        if entry is not None and pending:
            self._validate_pending_binding(entry.locator, entry.pending_review, code="usage_schema_invalid")
        return entry

    def _validate_pending_binding(self, locator, review, *, code="source_unreadable"):
        if ((review["project_id"], review["project_path_hash"], review["project_binding_generation"]) != self.basis
                or review["reviewer_session_id"] != locator.thread_id):
            raise UsageError(code)

    def locator_by_id(self, source_id, *, pending=False):
        if not _hex(source_id):
            raise UsageError("source_unreadable")
        with self.connection() as connection:
            return self._get_locator(connection, source_id, pending=pending)

    def remember_locator(self, locator, *, pending_review=None):
        """Commit a candidate before attempting its possibly missing source."""
        validate_locator(locator)
        document = None if pending_review is None else _pending_document(pending_review)
        if pending_review is not None:
            self._validate_pending_binding(locator, pending_review)
        with self.connection(write=True) as connection:
            known = self._get_locator(connection, locator.source_id)
            pending = self._get_locator(connection, locator.source_id, pending=True)
            for previous in (known, pending):
                if previous is not None and previous.locator != locator:
                    raise UsageError("source_unreadable")
            if known is not None:
                return known
            if pending_review is None:
                if connection.execute("SELECT 1 FROM usage_sessions WHERE thread_id=?",
                                      (locator.thread_id,)).fetchone() is None:
                    raise UsageError("source_not_registered")
                if pending is not None:
                    return self._promote_locator(connection, pending)
            elif pending is not None:
                if pending.pending_review == pending_review:
                    return pending
                if pending.revision >= _MAX_INTEGER:
                    raise UsageError("usage_schema_invalid")
                connection.execute("UPDATE usage_pending_locators SET pending_review=?,revision=? WHERE source_id=?",
                                   (document, pending.revision + 1, locator.source_id))
                self._before_locator_commit(connection)
                return replace(pending, revision=pending.revision + 1,
                               pending_review=json.loads(document))
            else:
                # This table alone is capped; known locations are never evicted.
                occupied = connection.execute("SELECT 1 FROM usage_pending_locators LIMIT ?",
                                              (PENDING_LOCATOR_LIMIT,)).fetchall()
                if len(occupied) >= PENDING_LOCATOR_LIMIT:
                    raise UsageError("usage_locator_capacity")
            order = self._next_order(connection)
            values = (locator.source_id, locator.thread_id, locator.root_kind,
                      locator.root_fingerprint, locator.relative_path, 1, order)
            if document is None:
                connection.execute("INSERT INTO usage_locators VALUES (?,?,?,?,?,?,?)", values)
            else:
                connection.execute("INSERT INTO usage_pending_locators VALUES (?,?,?,?,?,?,?,?)", (*values, document))
            self._before_locator_commit(connection)
            return LocatorEntry(locator, 1, order, None if document is None else json.loads(document))

    def locator_page(self, root_fingerprints, *, limit=16, pending=False):
        """Read indexed finite pages; unrelated roots never consume the page."""
        table = _table(pending)
        if type(limit) is not int or not 1 <= limit <= LOCATOR_PAGE_LIMIT:
            raise UsageError("source_unreadable")
        roots = _root_fingerprints(root_fingerprints)
        selected = []
        with self.connection() as connection:
            for kind, fingerprint in roots:
                selected.extend(self._read_locator(row, pending=pending) for row in connection.execute(
                    "SELECT * FROM " + table + " WHERE root_kind=? AND root_fingerprint=? "
                    "ORDER BY queue_order,source_id LIMIT ?", (kind, fingerprint, limit)))
        return tuple(sorted(selected, key=lambda item: (item.queue_order, item.locator.source_id))[:limit])

    def begin_locator_attempt(self, entry):
        """Rotate one exact queued entry before I/O; a crash leaves it retryable."""
        if type(entry) is not LocatorEntry:
            raise UsageError("source_unreadable")
        pending = entry.pending_review is not None
        with self.connection(write=True) as connection:
            current = self._get_locator(connection, entry.locator.source_id, pending=pending)
            if current != entry:
                return None
            if current.revision >= _MAX_INTEGER:
                raise UsageError("usage_schema_invalid")
            order = self._next_order(connection)
            connection.execute("UPDATE " + _table(pending) + " SET revision=?,queue_order=? WHERE source_id=?",
                               (current.revision + 1, order, entry.locator.source_id))
            self._before_locator_commit(connection)
            return replace(current, revision=current.revision + 1, queue_order=order)

    def _promote_locator(self, connection, pending):
        locator = pending.locator
        if connection.execute("SELECT 1 FROM usage_sessions WHERE thread_id=?",
                              (locator.thread_id,)).fetchone() is None:
            raise UsageError("source_not_registered")
        known = self._get_locator(connection, locator.source_id)
        if known is not None and known.locator != locator:
            raise UsageError("source_unreadable")
        if known is None:
            if pending.revision >= _MAX_INTEGER:
                raise UsageError("usage_schema_invalid")
            known = replace(pending, revision=pending.revision + 1, pending_review=None)
            connection.execute("INSERT INTO usage_locators VALUES (?,?,?,?,?,?,?)",
                               (locator.source_id, locator.thread_id, locator.root_kind,
                                locator.root_fingerprint, locator.relative_path, known.revision, known.queue_order))
        connection.execute("DELETE FROM usage_pending_locators WHERE source_id=?", (locator.source_id,))
        self._before_locator_commit(connection)
        return known

    def promote_locator(self, entry):
        """Caller freshly authorized the retained binding; session must exist."""
        if type(entry) is not LocatorEntry or entry.pending_review is None:
            raise UsageError("source_unreadable")
        with self.connection(write=True) as connection:
            current = self._get_locator(connection, entry.locator.source_id, pending=True)
            if current != entry:
                return None
            return self._promote_locator(connection, current)

    def discard_pending_locator(self, entry):
        """Retire only the exact pending authorization, never numerical history."""
        if type(entry) is not LocatorEntry or entry.pending_review is None:
            raise UsageError("source_unreadable")
        with self.connection(write=True) as connection:
            current = self._get_locator(connection, entry.locator.source_id, pending=True)
            if current != entry:
                return False
            connection.execute("DELETE FROM usage_pending_locators WHERE source_id=?", (entry.locator.source_id,))
            self._before_locator_commit(connection)
            return True

    def known_sources(self, source_ids):
        selected = tuple(dict.fromkeys(source_ids))
        if len(selected) > LOCATOR_PAGE_LIMIT or any(not _hex(item) for item in selected):
            raise UsageError("source_unreadable")
        if not selected:
            return {}
        with self.connection() as connection:
            result = dict(connection.execute("SELECT source_id,thread_id FROM usage_sources WHERE source_id IN ("
                                             + ",".join("?" for _ in selected) + ")", selected))
        if any(not is_session_id(thread) for thread in result.values()):
            raise UsageError("usage_schema_invalid")
        return result

    def registered_sessions(self, thread_ids):
        selected = tuple(dict.fromkeys(thread_ids))
        if len(selected) > LOCATOR_PAGE_LIMIT or any(not is_session_id(item) for item in selected):
            raise UsageError("source_unreadable")
        if not selected:
            return frozenset()
        with self.connection() as connection:
            return frozenset(row[0] for row in connection.execute(
                "SELECT thread_id FROM usage_sessions WHERE thread_id IN ("
                + ",".join("?" for _ in selected) + ")", selected))

    def session_ids(self):
        """Participant IDs only, for the existing best-effort unknown discovery."""
        with self.connection() as connection:
            sessions = frozenset(row[0] for row in connection.execute("SELECT thread_id FROM usage_sessions"))
        if any(not is_session_id(thread) for thread in sessions):
            raise UsageError("usage_schema_invalid")
        return sessions
