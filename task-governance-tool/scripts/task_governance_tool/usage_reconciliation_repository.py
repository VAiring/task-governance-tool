"""Numerical reconciliation change tracking; no core writer or publication.

The collector's existing transaction records both an input change and its
affected identities. Dirty rows are disposable work, not numerical evidence.
Explicit Setup alone creates this schema; historical observations stay intact.
"""

from dataclasses import dataclass

from .usage_work_order_repository import UsageWorkOrderRepository
from .usage_values import UsageError
from .usage_reconciliation_core import TABLES as _CORE_TABLES
from .usage_reconciliation_turns import TABLES as _TURN_TABLES
from .usage_reconciliation_sources import TABLES as _SOURCE_TABLES
from .usage_reconciliation_events import TABLES as _EVENT_TABLES
from .usage_reconciliation_inputs import TABLES as _INPUT_TABLES
from .usage_reconciliation_changes import TABLES as _CHANGE_TABLES
from .usage_reconciliation_membership import TABLES as _MEMBERSHIP_TABLES
from .usage_reconciliation_intervals import TABLES as _INTERVAL_TABLES
from .usage_reconciliation_baseline import TABLES as _BASELINE_TABLES
from .usage_reconciliation_ranges import TABLES as _RANGE_TABLES
from .usage_reconciliation_overlays import TABLES as _OVERLAY_TABLES
from .usage_reconciliation_uncertainty import TABLES as _UNCERTAINTY_TABLES
from .usage_reconciliation_pending import TABLES as _PENDING_TABLES
from .usage_reconciliation_basis import TABLES as _BASIS_TABLES
from .usage_reconciliation_snapshots import TABLES as _SNAPSHOT_TABLES
from .usage_reconciliation_totals import TABLES as _TOTAL_TABLES


_MIGRATION = """CREATE TABLE usage_migrations (
    version INTEGER PRIMARY KEY CHECK(version IN (1,2,3,4,5,6,7,8,9,10)),
    name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
      (version=2 AND name='turn_attribution') OR (version=3 AND name='immutable_usage_evidence') OR
      (version=4 AND name='review_wait_attribution') OR (version=5 AND name='incremental_collection') OR
      (version=6 AND name='response_attribution') OR (version=7 AND name='progressive_publication') OR
      (version=8 AND name='source_locators') OR (version=9 AND name='indexed_work_order') OR
      (version=10 AND name='reconciliation_changes')))"""
_TABLES = (
    """CREATE TABLE usage_reconcile_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        epoch TEXT NOT NULL CHECK(length(epoch)=32 AND epoch NOT GLOB '*[^0-9a-f]*'),
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>=0))""",
    """CREATE TABLE usage_reconcile_changes (
        kind TEXT NOT NULL CHECK(kind IN ('thread','turn','task','source','response','boundary','context','origin_event','origin_call','clock','receipt')),
        key1 TEXT NOT NULL CHECK(length(key1)>0), key2 TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        PRIMARY KEY(kind,key1,key2))""",
    "CREATE INDEX usage_reconcile_changes_by_generation ON usage_reconcile_changes(generation,kind,key1,key2)",
    "CREATE INDEX usage_reconcile_turn_order ON usage_turns(thread_id,started_at,turn_id)",
    "CREATE INDEX usage_reconcile_operation_task ON usage_operation_turns(project_id,task_id,event_id)",
    "CREATE INDEX usage_reconcile_operation_turn ON usage_operation_turns(project_id,thread_id,turn_id,task_id)",
    "CREATE INDEX usage_reconcile_boundary_event ON usage_response_boundaries(json_extract(document,'$.event_id'))",
    "CREATE INDEX usage_reconcile_boundary_call ON usage_response_boundaries(json_extract(document,'$.thread_id'),json_extract(document,'$.call_id'))",
    "CREATE INDEX usage_reconcile_boundary_task ON usage_response_boundaries(json_extract(document,'$.task_id'))",
    "CREATE INDEX usage_reconcile_boundary_turn ON usage_response_boundaries(json_extract(document,'$.thread_id'),json_extract(document,'$.turn_id'))",
    "CREATE INDEX usage_reconcile_boundary_response ON usage_response_boundaries(json_extract(document,'$.provider'),json_extract(document,'$.response_id'))",
    "CREATE INDEX usage_reconcile_positions_turn ON usage_response_positions(thread_id,turn_id)",
    "CREATE INDEX usage_reconcile_raw_position_key ON usage_response_positions(provider,response_id,source_id,incarnation)",
    """CREATE TRIGGER usage_reconcile_initialize AFTER INSERT ON usage_meta BEGIN
        INSERT INTO usage_reconcile_state VALUES (1,lower(hex(randomblob(16))),0);
        INSERT INTO usage_reconcile_evidence_revision VALUES (1,0); END""",
)


# Each expression yields only structural identities. Raw contexts, boundary
# documents, counters, locations and diagnostic text are never copied here.
# OLD and NEW are both retained for moves/replacements, including deletions.
_INPUTS = {
    "usage_sessions": ("thread_id", (("thread", "r.thread_id", "''"),)),
    "usage_sources": ("source_id,thread_id,incarnation,offset,prefix,file_id,pending", (
        ("source", "r.source_id", "''"),)),
    "usage_incarnations": ("source_id,incarnation,offset,prefix,file_id", (("source", "r.source_id", "''"),)),
    "usage_responses": ("provider,response_id,thread_id,turn_id,model,effort,input_tokens,output_tokens,total_tokens,cached_input_tokens,reasoning_output_tokens,cache_write_input_tokens", (
        ("turn", "r.thread_id", "r.turn_id"), ("response", "r.provider", "r.response_id"))),
    "usage_observations": ("source_id,incarnation,provider,response_id", (
        ("source", "r.source_id", "''"), ("response", "r.provider", "r.response_id"))),
    "usage_conflicts": ("provider,response_id", (("response", "r.provider", "r.response_id"),)),
    "usage_diagnostics": ("source_id,code", (("source", "r.source_id", "''"),)),
    "usage_turns": ("thread_id,turn_id,started_at", (
        ("turn", "r.thread_id", "r.turn_id"), ("clock", "r.thread_id", "CAST(r.started_at AS TEXT)"))),
    "usage_turn_conflicts": ("thread_id,turn_id", (
        ("turn", "r.thread_id", "r.turn_id"), ("thread", "r.thread_id", "''"))),
    "usage_operation_turns": ("event_id,thread_id,turn_id,project_id,task_id,generation,status", (
        ("turn", "r.thread_id", "r.turn_id"), ("task", "r.project_id", "r.task_id"))),
    "usage_operation_conflicts": ("event_id", (
        ("task", "(SELECT project_id FROM usage_operation_turns WHERE event_id=r.event_id)",
         "(SELECT task_id FROM usage_operation_turns WHERE event_id=r.event_id)"),)),
    "usage_review_boundaries": ("thread_id,turn_id,project_id,task_id,execution_id,contract_revision,target_kind,target_value,target_base_revision,target_generation,phase,original_result_digest", (
        ("turn", "r.thread_id", "r.turn_id"), ("task", "r.project_id", "r.task_id"))),
    "usage_review_receipt_turns": ("thread_id,turn_id,receipt_id", (
        ("turn", "r.thread_id", "r.turn_id"), ("receipt", "r.receipt_id", "''"))),
    "usage_wait_turns": ("thread_id,turn_id,project_id,task_id,execution_id,contract_revision,target_kind,target_value,target_base_revision,target_generation,artifact_manifest_id", (
        ("turn", "r.thread_id", "r.turn_id"), ("task", "r.project_id", "r.task_id"))),
    "usage_response_positions": ("source_id,incarnation,thread_id,turn_id,provider,response_id,record_order", (
        ("turn", "r.thread_id", "r.turn_id"), ("response", "r.provider", "r.response_id"))),
    "usage_response_boundaries": ("source_id,incarnation,observation_id,document", (
        ("boundary", "r.source_id", "r.observation_id"),
        ("response", "json_extract(r.document,'$.provider')", "json_extract(r.document,'$.response_id')"),
        ("turn", "json_extract(r.document,'$.thread_id')", "json_extract(r.document,'$.turn_id')"),
        ("task", "json_extract(r.document,'$.project_id')", "json_extract(r.document,'$.task_id')"),
        ("origin_event", "json_extract(r.document,'$.event_id')", "''"),
        ("origin_call", "json_extract(r.document,'$.thread_id')", "json_extract(r.document,'$.call_id')"))),
    "usage_response_context": ("source_id,lane,document", (("context", "r.source_id", "''"),)),
    # The scan CAS revision advances even at unchanged EOF. Attribution reads
    # the closed document, not that attempt counter; treating the counter as
    # a semantic change would restart the publication audit on every event.
    "usage_scan_state": ("source_id,lane,document", (("context", "r.source_id", "''"),)),
}


def _triggers():
    statements = []
    for table, (columns, targets) in _INPUTS.items():
        for operation in ("INSERT", "UPDATE", "DELETE"):
            aliases = ("OLD", "NEW") if operation == "UPDATE" else (("OLD",) if operation == "DELETE" else ("NEW",))
            checks = []
            if operation == "UPDATE":
                checks.append("(" + " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in columns.split(",")) + ")")
            if table in {"usage_response_context", "usage_scan_state"}:
                checks.append("(" + " OR ".join(f"{alias}.lane='ingest'" for alias in aliases) + ")")
            when = " WHEN " + " AND ".join(checks) if checks else ""
            body = ["UPDATE usage_reconcile_state SET generation=generation+1 WHERE singleton=1 "
                    "AND generation<9223372036854775807;",
                    "SELECT CASE WHEN changes()!=1 THEN RAISE(ABORT,'usage reconciliation tracking unavailable') END;"]
            for alias in aliases:
                for kind, first, second in targets:
                    first, second = first.replace("r.", alias + "."), second.replace("r.", alias + ".")
                    body.append("INSERT INTO usage_reconcile_changes(kind,key1,key2,generation) "
                        f"SELECT '{kind}',{first},{second},generation FROM usage_reconcile_state WHERE singleton=1 "
                        f"AND {first} IS NOT NULL AND {second} IS NOT NULL "
                        "ON CONFLICT(kind,key1,key2) DO UPDATE SET generation=excluded.generation;")
            statements.append(f"CREATE TRIGGER usage_reconcile_{table}_{operation.lower()} AFTER {operation} ON {table}"
                              + when + " BEGIN " + " ".join(body) + " END")
    return tuple(statements)


@dataclass(frozen=True)
class ReconciliationRevision:
    epoch: str
    generation: int


class UsageReconciliationRepository(UsageWorkOrderRepository):
    schema_statements = (_MIGRATION, *UsageWorkOrderRepository.schema_statements[1:], *_TABLES, *_CORE_TABLES,
                         *_TURN_TABLES, *_SOURCE_TABLES, *_EVENT_TABLES, *_INPUT_TABLES, *_CHANGE_TABLES,
                         *_MEMBERSHIP_TABLES, *_INTERVAL_TABLES, *_BASELINE_TABLES, *_RANGE_TABLES, *_OVERLAY_TABLES,
                         *_UNCERTAINTY_TABLES, *_PENDING_TABLES, *_BASIS_TABLES, *_SNAPSHOT_TABLES, *_TOTAL_TABLES, *_triggers())
    migrations = (*UsageWorkOrderRepository.migrations, (10, "reconciliation_changes"))
    _previous_repositories = (UsageWorkOrderRepository, *UsageWorkOrderRepository._previous_repositories)

    def _after_numerical_upgrade(self, connection, previous):
        connection.execute("INSERT INTO usage_reconcile_state VALUES (1,lower(hex(randomblob(16))),0)")
        connection.execute("INSERT INTO usage_reconcile_evidence_revision VALUES (1,0)")
        if previous is not UsageWorkOrderRepository:
            super()._after_numerical_upgrade(connection, previous)
        self._before_reconciliation_marker(connection)

    def _before_reconciliation_marker(self, connection):
        """Migration fault seam; a new epoch requires rebuilding derived inputs."""

    def _validate(self, connection):
        super()._validate(connection)
        self.reconciliation_revision(connection)

    def reconciliation_revision(self, connection):
        row = connection.execute("SELECT epoch,generation FROM usage_reconcile_state WHERE singleton=1").fetchone()
        if (row is None or type(row["epoch"]) is not str or len(row["epoch"]) != 32
                or any(char not in "0123456789abcdef" for char in row["epoch"])
                or type(row["generation"]) is not int or not 0 <= row["generation"] < 2**63):
            raise UsageError("usage_schema_invalid")
        return ReconciliationRevision(row["epoch"], row["generation"])

    def pending_changes(self, connection, *, through, limit=128):
        current = self.reconciliation_revision(connection)
        if current != through or type(limit) is not int or not 1 <= limit <= 128:
            raise UsageError("cursor_stale")
        return tuple(tuple(row) for row in connection.execute(
            "SELECT kind,key1,key2,generation FROM usage_reconcile_changes WHERE generation<=? "
            "ORDER BY generation,kind,key1,key2 LIMIT ?", (through.generation, limit)))

    def acknowledge_changes(self, connection, changes, *, through):
        if not connection.in_transaction or self.reconciliation_revision(connection) != through:
            raise UsageError("cursor_stale")
        connection.executemany("DELETE FROM usage_reconcile_changes WHERE kind=? AND key1=? AND key2=? AND generation=?", changes)

    def reconcile_core_page(self, core_reader):
        from .usage_reconciliation_core import sync_page
        from .usage_reconciliation_basis import admit_identity
        with self.connection(write=True) as connection, core_reader() as core:
            admit_identity(self, connection)
            result = sync_page(self, core, connection)
            self.check_budget()
            return result

    def discover_participants(self, core_reader):
        from .usage_reconciliation_core import participants
        return participants(self, core_reader)

    def refresh(self, core_reader):
        from .usage_reconciliation_publication import reconcile
        reconcile(self, core_reader)
        with self.connection() as connection:
            return [self._snapshot_document(row) for row in connection.execute("SELECT snapshot.* FROM usage_reconcile_snapshot_heads head "
                "JOIN usage_snapshots snapshot USING(snapshot_id) ORDER BY snapshot.rowid")]

    def prepare_publication(self, core_reader):
        from .usage_reconciliation_publication import prepare
        return prepare(self, core_reader)

    def publication_guard(self, core_reader, index, state):
        from .usage_reconciliation_publication import guard
        return guard(self, core_reader, index, state)

    def _core_basis(self, core, connection, **_):
        from .usage_reconciliation_publication import captured_basis, periods
        return captured_basis(self, core, connection), periods(connection)

    def _display_basis(self, core, connection):
        # The fresh structural fence proves that selected histories were
        # reconciled. Read-only display neither drains work nor reloads all
        # historical response relations as an extra classification pass.
        return self._core_basis(core, connection), None

    def _display_unattributed(self, core, connection, relations, read):
        from .usage_reconciliation_totals import totals
        return totals(connection)

    def _reset_reconciliation(self, connection):
        """Discard only derived work; original numbers/evidence remain immutable."""
        if not connection.in_transaction:
            raise UsageError("usage_schema_invalid")
        for statement in self.schema_statements:
            if statement.startswith("CREATE TABLE usage_reconcile_"):
                table = statement.split()[2].split("(", 1)[0]
                if table not in {"usage_reconcile_state", "usage_reconcile_changes", "usage_reconcile_identity",
                                  "usage_reconcile_evidence_revision", "usage_reconcile_snapshot_lineage"}:
                    connection.execute("DELETE FROM " + table)
