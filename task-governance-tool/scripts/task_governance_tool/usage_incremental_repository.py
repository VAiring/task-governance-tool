"""Numerical schema 5: atomic scan continuations, fair work and dirty turns.

Only explicit Setup migrates. A read snapshot feeds the file adapter, then a
writer checks both the complete cursor and continuation revision. No core write,
private record, raw-body digest, or automatic schema repair is involved.
"""

from dataclasses import asdict
import json
from time import monotonic

from task_governance_tool.usage_adapter import CollectionBatch, Cursor
from task_governance_tool.usage_repository import UsageRepository, _cursor
from task_governance_tool.usage_attribution_repository import UsageAttributionRepository
from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository
from task_governance_tool.usage_wait_repository import UsageWaitRepository
from task_governance_tool.usage_incremental_adapter import ScanState, Models, scan
from task_governance_tool.usage_values import UsageError, label
from task_governance_tool.session_identity import CallerIdentity, is_session_id


_MIGRATION = """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version IN (1,2,3,4,5)),
        name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
          (version=2 AND name='turn_attribution') OR (version=3 AND name='immutable_usage_evidence') OR
          (version=4 AND name='review_wait_attribution') OR (version=5 AND name='incremental_collection')))"""
_INCREMENTAL_DDL = (
    """CREATE TABLE usage_discovery (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        revision INTEGER NOT NULL CHECK(revision>0), document TEXT NOT NULL)""",
    """CREATE TABLE usage_discovered (source_id TEXT PRIMARY KEY, cycle INTEGER NOT NULL CHECK(cycle>=0))""",
    """CREATE TABLE usage_scan_state (
        source_id TEXT NOT NULL REFERENCES usage_sources(source_id), lane TEXT NOT NULL CHECK(lane IN ('ingest','audit')),
        revision INTEGER NOT NULL CHECK(revision>0), document TEXT NOT NULL,
        PRIMARY KEY(source_id,lane))""",
    """CREATE TABLE usage_scan_models (
        source_id TEXT NOT NULL REFERENCES usage_sources(source_id), lane TEXT NOT NULL CHECK(lane IN ('ingest','audit')),
        turn_id TEXT NOT NULL, model TEXT, effort TEXT, PRIMARY KEY(source_id,lane,turn_id))""",
    """CREATE TABLE usage_scan_coverage (
        source_id TEXT NOT NULL REFERENCES usage_sources(source_id), turn_id TEXT NOT NULL,
        modern INTEGER NOT NULL CHECK(modern IN (0,1)), legacy INTEGER NOT NULL CHECK(legacy IN (0,1)),
        PRIMARY KEY(source_id,turn_id))""",
    """CREATE TABLE usage_work_order (source_id TEXT PRIMARY KEY,
        last_attempt INTEGER NOT NULL CHECK(last_attempt>=0))""",
    """CREATE TABLE usage_dirty_turns (thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
        PRIMARY KEY(thread_id,turn_id))""",
    """CREATE TABLE usage_component_basis (component_id TEXT PRIMARY KEY, basis TEXT NOT NULL,
        snapshot_id TEXT NOT NULL REFERENCES usage_snapshots(snapshot_id))""",
    "CREATE INDEX usage_responses_by_turn ON usage_responses(thread_id,turn_id)",
    """CREATE TRIGGER usage_dirty_response AFTER INSERT ON usage_responses BEGIN
        INSERT OR IGNORE INTO usage_dirty_turns VALUES (new.thread_id,new.turn_id); END""",
    """CREATE TRIGGER usage_dirty_conflict AFTER INSERT ON usage_conflicts BEGIN
        INSERT OR IGNORE INTO usage_dirty_turns SELECT thread_id,turn_id FROM usage_responses
        WHERE provider=new.provider AND response_id=new.response_id; END""",
)
_PREVIOUS = (UsageWaitRepository, UsageEvidenceRepository, UsageAttributionRepository, UsageRepository)


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _document(row):
    if row is None:
        return None
    try:
        value = json.loads(row["document"])
        if (set(value) != {"state", "incarnation", "goal", "goal_prefix", "reset"}
                or type(value["incarnation"]) is not int or value["incarnation"] < 1
                or type(value["goal"]) is not int or value["goal"] < 0
                or not _hex(value["goal_prefix"]) or type(value["reset"]) is not bool):
            raise ValueError
        state = ScanState(**value["state"])
        if (type(state.offset) is not int or state.offset < 1 or not _hex(state.prefix)
                or type(state.drain) is not int or state.drain < 0
                or (state.drain and state.drain <= state.offset)
                or label(state.provider) != state.provider or not state.provider
                or not is_session_id(state.owner) or (state.turn is not None and not is_session_id(state.turn))):
            raise ValueError
        value["state"] = state
        return value
    except (ValueError, TypeError, KeyError):
        raise UsageError("usage_schema_invalid") from None


class UsageIncrementalRepository(UsageWaitRepository):
    schema_statements = (_MIGRATION, *UsageWaitRepository.schema_statements[1:], *_INCREMENTAL_DDL)
    migrations = (*UsageWaitRepository.migrations, (5, "incremental_collection"))
    connection_timeout = 0.05
    deadline = float("inf")

    def check_budget(self):
        if monotonic() >= self.deadline:
            raise UsageError("usage_pending")

    def _configure_work(self, connection):
        self.check_budget()
        connection.set_progress_handler(lambda: int(monotonic() >= self.deadline), 1000)

    def inspect(self):
        try:
            return UsageRepository.inspect(self)
        except UsageError as error:
            if error.code != "usage_schema_invalid":
                raise
        for previous in _PREVIOUS:
            try:
                with previous(self.path, *self.basis).connection():
                    return "migration_required"
            except UsageError as error:
                if error.code != "usage_schema_invalid":
                    raise
        raise UsageError("usage_schema_invalid")

    def initialize(self):
        if self.inspect() != "migration_required":
            return UsageRepository.initialize(self)
        for previous in _PREVIOUS:
            old = previous(self.path, *self.basis)
            try:
                with old.connection(write=True) as connection:
                    if (connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                            or connection.execute("PRAGMA foreign_key_check").fetchone()):
                        raise UsageError()
                    connection.execute("ALTER TABLE usage_migrations RENAME TO usage_migrations_old")
                    connection.execute(_MIGRATION)
                    connection.execute("INSERT INTO usage_migrations SELECT * FROM usage_migrations_old")
                    connection.execute("DROP TABLE usage_migrations_old")
                    for statement in self.schema_statements[1:]:
                        if statement not in previous.schema_statements:
                            connection.execute(statement)
                    for migration in self.migrations[len(previous.migrations):]:
                        connection.execute("INSERT INTO usage_migrations VALUES (?,?)", migration)
                    self._before_incremental_marker(connection)
                    self._validate(connection)
                return "migrated"
            except UsageError as error:
                if error.code != "usage_schema_invalid":
                    raise
        raise UsageError("usage_schema_invalid")

    def _before_incremental_marker(self, connection):
        """Migration rollback seam; existing evidence is never rewritten."""

    def _project_for_refresh(self, core, connection, previous):
        from task_governance_tool.usage_incremental_projection import project_changed
        return project_changed(self, core, connection, previous)

    def _adopt_projection(self, connection, projected, adopted):
        from task_governance_tool.usage_incremental_projection import adopt
        adopt(connection, projected, adopted)

    def work_order(self, sources, thread):
        with self.connection() as connection:
            attempts = dict(connection.execute("SELECT source_id,last_attempt FROM usage_work_order"))
        fair = sorted(sources, key=lambda key: (attempts.get(key, -1), key))
        # Reserve the first slot for globally oldest work, then one caller slot.
        # Even tiny budgets therefore advance old sessions/segments under load.
        priority = next((key for key in fair if sources[key].thread_id == thread), None)
        return list(dict.fromkeys([*fair[:1], *([priority] if priority else []), *fair[1:]]))

    def discovery_position(self):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM usage_discovery").fetchone()
        if row is None:
            return 0, {"root": 0, "stack": [], "cycle": 0, "attempt_floor": 0}
        try:
            value = json.loads(row["document"])
            if (set(value) != {"root", "stack", "cycle", "attempt_floor"} or type(value["root"]) is not int
                    or value["root"] not in {0, 1} or type(value["cycle"]) is not int or value["cycle"] < 0
                    or type(value["attempt_floor"]) is not int or value["attempt_floor"] < 0
                    or not isinstance(value["stack"], list) or len(value["stack"]) > 4):
                raise ValueError
            for depth, (parts, ordinal) in enumerate(value["stack"]):
                if (len(parts) != depth or type(ordinal) is not int or ordinal < 0
                        or any(not isinstance(part, str) or len(part) != (4 if i == 0 else 2)
                               or not part.isascii() or not part.isdecimal() for i, part in enumerate(parts))):
                    raise ValueError
            return row["revision"], value
        except (ValueError, TypeError, KeyError):
            raise UsageError("usage_schema_invalid") from None

    def unattempted_sources(self, sources, attempt_floor):
        """Page completion accumulates across events, including failed attempts."""
        with self.connection() as connection:
            attempted = {row[0] for row in connection.execute(
                "SELECT source_id FROM usage_work_order WHERE last_attempt>?", (attempt_floor,))}
        return set(sources) - attempted

    def missing_sessions(self, sessions, cycle):
        """A finished inventory can diagnose participants never admitted as sources."""
        with self.connection() as connection:
            located = {row[0] for row in connection.execute(
                "SELECT DISTINCT source.thread_id FROM usage_sources source JOIN usage_discovered seen "
                "USING(source_id) WHERE seen.cycle=?", (cycle,))}
        return bool(set(sessions) - located)

    def commit_discovery(self, revision, position, seen, complete):
        """CAS advance after this page's attempts, which may span many events."""
        with self.connection(write=True) as connection:
            row = connection.execute("SELECT revision FROM usage_discovery").fetchone()
            if (row[0] if row else 0) != revision:
                raise UsageError("cursor_stale")
            connection.executemany("INSERT INTO usage_discovered VALUES (?,?) ON CONFLICT(source_id) DO UPDATE SET cycle=excluded.cycle",
                                   [(source, position["cycle"]) for source in seen])
            if complete:
                connection.execute("INSERT OR IGNORE INTO usage_diagnostics SELECT source_id,'source_unreadable' FROM usage_sources "
                                   "WHERE source_id NOT IN (SELECT source_id FROM usage_discovered WHERE cycle=?)", (position["cycle"],))
                position = {"root": 0, "stack": [], "cycle": position["cycle"] + 1}
            position = {**position, "attempt_floor": connection.execute(
                "SELECT coalesce(max(last_attempt),0) FROM usage_work_order").fetchone()[0]}
            connection.execute("INSERT INTO usage_discovery VALUES (1,?,?) ON CONFLICT(singleton) "
                               "DO UPDATE SET revision=excluded.revision,document=excluded.document",
                               (revision + 1, json.dumps(position)))

    def attempted(self, source):
        if not _hex(source):
            raise UsageError("source_unreadable")
        with self.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_work_order VALUES (?,(SELECT coalesce(max(last_attempt),0)+1 FROM usage_work_order)) "
                               "ON CONFLICT(source_id) DO UPDATE SET last_attempt=excluded.last_attempt", (source,))

    def collect_source(self, source, *, lane="ingest", **limits):
        """Read without a writer, then CAS continuation+observations atomically."""
        with self.connection() as connection:
            source_row = connection.execute("SELECT * FROM usage_sources WHERE source_id=? AND thread_id=?",
                                            (source.source_id, source.thread_id)).fetchone()
            expected = _cursor(source_row) if source_row else Cursor()
            row = connection.execute("SELECT * FROM usage_scan_state WHERE source_id=? AND lane=?",
                                     (source.source_id, lane)).fetchone()
            document = _document(row)
            revision = row["revision"] if row else 0
            reset = document is None or document["incarnation"] != expected.incarnation or document["reset"]
            if lane == "audit" and not expected.offset:
                return {"bytes_read": 0, "records": 0}
            if lane == "audit" and connection.execute(
                    "SELECT 1 FROM usage_scan_state WHERE source_id=? AND lane='ingest'", (source.source_id,)).fetchone() is None:
                # Old prefix format has no resumable context. Setup preserves it;
                # ingest bootstraps a new incarnation before auditing that format.
                return {"bytes_read": 0, "records": 0}
            if lane == "audit" and document and document["state"].offset == document["goal"]:
                reset = True
            state = ScanState() if reset else document["state"]
            goal = expected.offset if lane == "audit" and reset else (document["goal"] if lane == "audit" else None)
            goal_prefix = expected.prefix if lane == "audit" and reset else (document["goal_prefix"] if lane == "audit" else "")
            lookup = lambda turn: None if reset else self._model(connection, source.source_id, lane, turn)
            try:
                result = scan(source, state, Models(lookup), file_id="" if reset and lane == "ingest" else expected.file_id,
                              goal=goal, project_id=self.basis[0], deadline=self.deadline, **limits)
            except UsageError as error:
                if error.code != "source_replaced":
                    raise
                result = None
        if result is None:
            self._invalidate(source.source_id, expected, revision, lane)
            return {"bytes_read": 0, "records": 0, "replay": True}
        broken_boundary = (lane == "audit" and result.state.offset < goal
                           and (result.pending == "partial_tail" or result.state.drain == goal))
        if lane == "audit" and (broken_boundary or (result.state.offset == goal and result.state.prefix != goal_prefix)):
            self._invalidate(source.source_id, expected, revision, lane)
            return {"bytes_read": result.bytes_read, "records": result.records, "replay": True}
        incarnation = expected.incarnation + int(reset) if lane == "ingest" else expected.incarnation
        incarnation = max(1, incarnation)
        value = {"state": asdict(result.state), "incarnation": incarnation,
                 "goal": goal if lane == "audit" else result.state.offset,
                 "goal_prefix": goal_prefix if lane == "audit" else result.state.prefix, "reset": False}
        # Registration happens only after adapter admission; no source path persists.
        if source_row is None:
            self.register_source(source.source_id, CallerIdentity(source.thread_id))
        self._before_scan_write()
        with self.connection(write=True) as connection:
            self._cas(connection, source.source_id, expected, revision, lane)
            if reset:
                connection.execute("DELETE FROM usage_scan_models WHERE source_id=? AND lane=?", (source.source_id, lane))
            connection.executemany("INSERT INTO usage_scan_models VALUES (?,?,?,?,?) ON CONFLICT(source_id,lane,turn_id) "
                                   "DO UPDATE SET model=excluded.model,effort=excluded.effort",
                                   [(source.source_id, lane, turn, *model) for turn, model in result.models.items()])
            connection.execute("INSERT INTO usage_scan_state VALUES (?,?,?,?) ON CONFLICT(source_id,lane) DO UPDATE SET "
                               "revision=excluded.revision,document=excluded.document",
                               (source.source_id, lane, revision + 1, json.dumps(value)))
            if lane == "ingest":
                if reset:
                    connection.execute("DELETE FROM usage_scan_coverage WHERE source_id=?", (source.source_id,))
                for column, turns in (("modern", result.modern), ("legacy", result.legacy)):
                    connection.executemany(f"INSERT INTO usage_scan_coverage VALUES (?,?,?,?) ON CONFLICT(source_id,turn_id) "
                                           f"DO UPDATE SET {column}=1",
                                           [(source.source_id, turn, int(column == 'modern'), int(column == 'legacy')) for turn in turns])
                gaps = set(result.diagnostics) | {"prefix_verification_deferred"}
                if reset and expected.incarnation and (document is not None or expected.file_id != result.file_id):
                    gaps.add("source_replaced")
                if connection.execute("SELECT 1 FROM usage_scan_coverage WHERE source_id=? AND legacy=1 AND modern=0 LIMIT 1",
                                      (source.source_id,)).fetchone():
                    gaps.add("legacy_usage")
                batch = CollectionBatch(source.source_id, source.thread_id, expected,
                                        Cursor(incarnation, result.state.offset, result.state.prefix, result.file_id),
                                        result.responses, tuple(sorted(gaps)), result.pending, result.attribution)
                self.commit_batch(batch, numerical_connection=connection)
            self._before_scan_commit(connection)
        return {"bytes_read": result.bytes_read, "records": result.records}

    def _model(self, connection, source, lane, turn):
        row = connection.execute("SELECT model,effort FROM usage_scan_models WHERE source_id=? AND lane=? AND turn_id=?",
                                 (source, lane, turn)).fetchone()
        if row is not None and any(value is not None and label(value) != value for value in row):
            raise UsageError("usage_schema_invalid")
        return tuple(row) if row else None

    def _cas(self, connection, source, expected, revision, lane):
        row = connection.execute("SELECT * FROM usage_sources WHERE source_id=?", (source,)).fetchone()
        actual = connection.execute("SELECT revision FROM usage_scan_state WHERE source_id=? AND lane=?", (source, lane)).fetchone()
        if row is None or _cursor(row) != expected or (actual[0] if actual else 0) != revision:
            raise UsageError("cursor_stale")

    def _invalidate(self, source, expected, revision, lane):
        # Defer replay to the next bounded ingest slot; no recursive full read.
        with self.connection(write=True) as connection:
            self._cas(connection, source, expected, revision, lane)
            row = connection.execute("SELECT * FROM usage_scan_state WHERE source_id=? AND lane='ingest'", (source,)).fetchone()
            value = _document(row)
            if value:
                value.update(state=asdict(value["state"]), reset=True)
                connection.execute("UPDATE usage_scan_state SET document=?,revision=revision+1 WHERE source_id=? AND lane='ingest'",
                                   (json.dumps(value), source))
            connection.execute("INSERT OR IGNORE INTO usage_diagnostics VALUES (?,'source_replaced')", (source,))

    def _before_scan_commit(self, connection):
        """Fault seam: continuation, models, cursor, observations and dirty marks roll back."""

    def _before_scan_write(self):
        """Concurrency seam after closing the reader, before opening the CAS writer."""
