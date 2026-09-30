"""Candidate numerical schema 2 and selected core attribution reads.

Explicit initialize() alone migrates schema 1. Ordinary connection requires
the exact numerical schema; core state/quality never consults this store.
No host discovery or setup activation is performed by this module.
"""

from __future__ import annotations

from contextlib import nullcontext

from task_governance_tool.usage_repository import UsageRepository, _DDL
from task_governance_tool.usage_turn_adapter import TurnObservation, OperationObservation
from task_governance_tool.usage_attribution import Transition, owner_intervals, project
from task_governance_tool.usage_values import UsageError, METRICS, ResponseUsage
from task_governance_tool.usage_review_attribution import ReviewBoundary, ReviewReceiptTurn, reviewer_intervals


_MIGRATION_DDL = """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version IN (1,2)),
        name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
                                 (version=2 AND name='turn_attribution')))"""
_ATTRIBUTION_DDL = (
    """CREATE TABLE usage_turns (
        thread_id TEXT NOT NULL REFERENCES usage_sessions(thread_id), turn_id TEXT NOT NULL,
        started_at INTEGER NOT NULL CHECK(started_at >= 0), PRIMARY KEY(thread_id, turn_id))""",
    """CREATE TABLE usage_turn_conflicts (
        thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, PRIMARY KEY(thread_id, turn_id),
        FOREIGN KEY(thread_id, turn_id) REFERENCES usage_turns(thread_id, turn_id))""",
    """CREATE TABLE usage_operation_turns (
        event_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES usage_sessions(thread_id),
        turn_id TEXT NOT NULL, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation > 0), status TEXT NOT NULL
        CHECK(status IN ('ready','in_progress','review_pending','paused','blocked','done','cancelled')))""",
    """CREATE TABLE usage_operation_conflicts (
        event_id TEXT PRIMARY KEY REFERENCES usage_operation_turns(event_id))""",
    """CREATE TABLE usage_review_boundaries (
        thread_id TEXT NOT NULL REFERENCES usage_sessions(thread_id), turn_id TEXT NOT NULL,
        project_id TEXT NOT NULL, task_id TEXT NOT NULL, execution_id TEXT NOT NULL,
        contract_revision INTEGER NOT NULL CHECK(contract_revision >= 0),
        target_kind TEXT NOT NULL, target_value TEXT NOT NULL, target_base_revision TEXT NOT NULL,
        target_generation INTEGER NOT NULL CHECK(target_generation > 0),
        phase TEXT NOT NULL CHECK(phase IN ('read','save')), original_result_digest TEXT NOT NULL,
        PRIMARY KEY(thread_id,turn_id,project_id,task_id,execution_id,contract_revision,
                    target_kind,target_value,target_base_revision,target_generation,phase,original_result_digest))""",
    """CREATE TABLE usage_review_receipt_turns (
        thread_id TEXT NOT NULL REFERENCES usage_sessions(thread_id), turn_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL, PRIMARY KEY(thread_id,turn_id,receipt_id))""",
)


def core_transitions(connection, project_id: str) -> tuple[Transition, ...]:
    """Use an already admitted core read transaction, not a numerical writer."""
    return tuple(Transition(**{name: row[name] for name in Transition.__dataclass_fields__})
                 for row in connection.execute(
                     "SELECT * FROM task_owner_transitions WHERE project_id=? ORDER BY rowid", (project_id,)))


def resolve_operation(connection, operation: OperationObservation) -> str | None:
    """The acknowledgement's exact event/Task/generation/status identifies a transition.

    No timestamp proximity search or interpretation of the event summary is used.
    Equal event/transition creation time is an extra consistency check, not the
    selector. A missing restored row stays unbound, never a guessed current Task.
    """
    event = connection.execute(
        "SELECT created_at FROM task_events WHERE task_event_id=? AND project_id=? AND task_id=?",
        (operation.event_id, operation.project_id, operation.task_id),
    ).fetchone()
    if event is None:
        return None
    matches = connection.execute(
        "SELECT transition_id, created_at FROM task_owner_transitions WHERE project_id=? AND task_id=? "
        "AND generation=? AND current_status=? AND actor_session_id=?",
        (operation.project_id, operation.task_id, operation.generation, operation.status, operation.thread_id),
    ).fetchall()
    return matches[0]["transition_id"] if len(matches) == 1 and matches[0]["created_at"] == event["created_at"] else None


class UsageAttributionRepository(UsageRepository):
    schema_statements = (_MIGRATION_DDL, *_DDL[1:], *_ATTRIBUTION_DDL)
    migrations = (*UsageRepository.migrations, (2, "turn_attribution"))
    collect_attribution = True

    def _record_attribution(self, connection, batch):
        if any(item.thread_id != batch.thread_id for item in batch.attribution):
            raise UsageError("source_unreadable")
        self._record(connection, batch.attribution)

    def initialize(self) -> str:
        if not self.path.exists():
            return super().initialize()
        try:
            with self.connection() as connection:
                if (connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                        or connection.execute("PRAGMA foreign_key_check").fetchone()):
                    raise UsageError()
                return "current"
        except UsageError as error:
            if error.code != "usage_schema_invalid":
                raise
        old = UsageRepository(self.path, *self.basis)
        with old.connection(write=True) as connection:
            # The old exact schema/binding was admitted under the writer. A
            # changed/newer/corrupt store is never reconstructed or overwritten.
            if (connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                    or connection.execute("PRAGMA foreign_key_check").fetchone()):
                raise UsageError()
            connection.execute("ALTER TABLE usage_migrations RENAME TO usage_migrations_v1")
            connection.execute(_MIGRATION_DDL)
            connection.execute("INSERT INTO usage_migrations SELECT * FROM usage_migrations_v1")
            connection.execute("DROP TABLE usage_migrations_v1")
            for statement in _ATTRIBUTION_DDL:
                connection.execute(statement)
            self._before_attribution_marker(connection)
            connection.execute("INSERT INTO usage_migrations VALUES (2,'turn_attribution')")
            self._validate(connection)
        return "migrated"

    def inspect(self) -> str:
        try:
            return super().inspect()
        except UsageError as error:
            if error.code != "usage_schema_invalid":
                raise
        with UsageRepository(self.path, *self.basis).connection():
            return "migration_required"

    def _before_attribution_marker(self, connection):
        """Fault-injection seam; migration has not committed."""

    def _record(self, connection, observations):
        for observation in observations:
            if isinstance(observation, TurnObservation):
                values = (observation.thread_id, observation.turn_id, observation.started_at)
                existing = connection.execute("SELECT * FROM usage_turns WHERE thread_id=? AND turn_id=?", values[:2]).fetchone()
                if existing is None:
                    connection.execute("INSERT INTO usage_turns VALUES (?,?,?)", values)
                elif tuple(existing) != values:
                    connection.execute("INSERT OR IGNORE INTO usage_turn_conflicts VALUES (?,?)", values[:2])
            elif isinstance(observation, OperationObservation) and observation.project_id == self.basis[0]:
                values = (observation.event_id, observation.thread_id, observation.turn_id, observation.project_id,
                          observation.task_id, observation.generation, observation.status)
                existing = connection.execute("SELECT * FROM usage_operation_turns WHERE event_id=?", values[:1]).fetchone()
                if existing is None:
                    connection.execute("INSERT INTO usage_operation_turns VALUES (?,?,?,?,?,?,?)", values)
                elif tuple(existing) != values:
                    connection.execute("INSERT OR IGNORE INTO usage_operation_conflicts VALUES (?)", values[:1])
            elif isinstance(observation, ReviewBoundary) and observation.project_id == self.basis[0]:
                connection.execute("INSERT OR IGNORE INTO usage_review_boundaries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                   tuple(getattr(observation, name) for name in ReviewBoundary.__dataclass_fields__))
            elif isinstance(observation, ReviewReceiptTurn):
                connection.execute("INSERT OR IGNORE INTO usage_review_receipt_turns VALUES (?,?,?)",
                                   (observation.thread_id, observation.turn_id, observation.receipt_id))
            else:
                raise UsageError("boundary_unknown")

    def record(self, observations) -> None:
        """Internal already-registered source seam; no Task mutation or init."""
        with self.connection(write=True) as connection:
            self._record(connection, observations)

    def attribution(self, core_connection, *, numerical_connection=None) -> dict:
        """Revalidate persisted associations against core, including after restore."""
        transitions = core_transitions(core_connection, self.basis[0])
        bindings, unresolved = {}, set()
        with (self.connection() if numerical_connection is None else nullcontext(numerical_connection)) as connection:
            observations = [OperationObservation(**dict(row)) for row in connection.execute(
                "SELECT thread_id,turn_id,project_id,task_id,event_id,generation,status FROM usage_operation_turns "
                "WHERE event_id NOT IN (SELECT event_id FROM usage_operation_conflicts)")]
            for observation in observations:
                transition = resolve_operation(core_connection, observation)
                if transition is None:
                    unresolved.add(observation.event_id)
                    continue
                boundary = (observation.thread_id, observation.turn_id)
                if transition in bindings and bindings[transition] != boundary:
                    bindings[transition] = None
                else:
                    bindings[transition] = boundary
            turns = tuple(TurnObservation(**dict(row)) for row in connection.execute("SELECT * FROM usage_turns"))
            conflicts = frozenset(tuple(row) for row in connection.execute("SELECT * FROM usage_turn_conflicts"))
            responses = tuple(ResponseUsage(row["provider"], row["response_id"], row["thread_id"], row["turn_id"],
                                            row["model"], row["effort"], tuple(row[name] for name in METRICS))
                              for row in connection.execute("SELECT * FROM usage_responses"))
            response_conflicts = frozenset(tuple(row) for row in connection.execute("SELECT * FROM usage_conflicts"))
            review_boundaries = tuple(ReviewBoundary(**dict(row)) for row in connection.execute("SELECT * FROM usage_review_boundaries"))
            receipt_turns = tuple(ReviewReceiptTurn(**dict(row)) for row in connection.execute("SELECT * FROM usage_review_receipt_turns"))
            diagnostics = {}
            for row in connection.execute("SELECT source.thread_id, source.pending, gap.code FROM usage_sources source "
                                          "LEFT JOIN usage_diagnostics gap ON gap.source_id=source.source_id"):
                codes = diagnostics.setdefault(row["thread_id"], set())
                if row["code"] is not None:
                    codes.add(row["code"])
                if row["pending"] != "none":
                    codes.add("collection_pending")
        owners = owner_intervals(transitions, bindings)
        reviews = []
        from task_governance_tool.storage import current_schema_version
        if current_schema_version(core_connection) >= 25:
            from task_governance_tool.review_session_repository import read_bindings, _receipt_target
            receipt_ids = {row[0] for row in core_connection.execute(
                "SELECT review_receipt_id FROM review_receipts WHERE project_id=?", (self.basis[0],))}
            for receipt_id, binding in read_bindings(core_connection, receipt_ids=receipt_ids).items():
                target, _, _ = _receipt_target(core_connection, receipt_id)
                reviews.append((receipt_id, target, binding))
        # Conflicting turn order cannot select a reviewer read/save boundary.
        safe_turns = tuple(item for item in turns if (item.thread_id, item.turn_id) not in conflicts)
        intervals = owners + reviewer_intervals(owners, safe_turns, review_boundaries, receipt_turns, reviews)
        result = project(intervals, turns, responses,
                         conflicting_turns=conflicts, conflicting_responses=response_conflicts,
                         thread_diagnostics=diagnostics)
        result["unresolved_operations"] = len(unresolved)
        return result
