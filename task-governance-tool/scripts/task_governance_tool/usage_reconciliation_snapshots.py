"""Selected immutable successors, with a frozen predecessor set per capture.

Committed intermediate snapshots are history until every selected component
has been adopted and the fresh input fence still matches. Splits share the
same old predecessors even when their successors commit in different steps.
"""

from .usage_evidence import digest, encode, snapshot, RESPONSE_FORMAT
from .usage_response_repository import component_identity
from .usage_reconciliation_core import _decode
from .usage_reconciliation_basis import require_inputs
from .usage_reconciliation_membership import component_geometry
from .usage_attribution import _models
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_evidence_revision (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>=0))""",
    """CREATE TABLE usage_reconcile_capture_guard (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),basis TEXT NOT NULL,evidence_generation INTEGER NOT NULL)""",
    *("CREATE TRIGGER usage_reconcile_evidence_" + table + " AFTER INSERT ON " + table + " BEGIN "
      "SELECT CASE WHEN (SELECT generation FROM usage_reconcile_evidence_revision WHERE singleton=1) IS NULL "
      "THEN RAISE(ABORT,'missing_usage_tracking') END; "
      "UPDATE usage_reconcile_evidence_revision SET generation=generation+1 WHERE singleton=1; END"
      for table in ("usage_snapshots", "usage_snapshot_members", "usage_cycle_links", "usage_supersessions")),
    """CREATE TABLE usage_reconcile_snapshot_heads (
        snapshot_id TEXT PRIMARY KEY,component_id TEXT NOT NULL,legacy INTEGER NOT NULL,response_count INTEGER NOT NULL)""",
    "CREATE INDEX usage_reconcile_head_partition ON usage_reconcile_snapshot_heads(component_id,snapshot_id)",
    """CREATE TABLE usage_reconcile_snapshot_executions (
        snapshot_id TEXT NOT NULL,execution_id TEXT NOT NULL,PRIMARY KEY(snapshot_id,execution_id))""",
    "CREATE INDEX usage_reconcile_snapshot_execution ON usage_reconcile_snapshot_executions(execution_id,snapshot_id)",
    "CREATE INDEX usage_reconcile_snapshot_member ON usage_snapshot_members(provider,response_id,snapshot_id)",
    "CREATE INDEX usage_reconcile_snapshot_successor ON usage_supersessions(successor_id,predecessor_id)",
    """CREATE TABLE usage_reconcile_snapshot_bootstrap (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),position INTEGER NOT NULL,complete INTEGER NOT NULL)""",
    """CREATE TABLE usage_reconcile_snapshot_round (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),basis TEXT NOT NULL,complete INTEGER NOT NULL)""",
    """CREATE TABLE usage_reconcile_snapshot_stage (
        component_id TEXT PRIMARY KEY,snapshot_id TEXT,geometry_digest TEXT)""",
    """CREATE TABLE usage_reconcile_snapshot_lineage (
        snapshot_id TEXT NOT NULL REFERENCES usage_snapshots(snapshot_id),execution_id TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,execution_id))""",
    "CREATE INDEX usage_reconcile_lineage_execution ON usage_reconcile_snapshot_lineage(execution_id,snapshot_id)",
)


def _head(repository, connection, row):
    document = repository._snapshot_document(row)
    if connection.execute("SELECT 1 FROM json_each(?) member WHERE NOT EXISTS (SELECT 1 FROM usage_reconcile_period_executions "
        "WHERE execution_id=member.value) LIMIT 1", (encode(document["executions"]).decode(),)).fetchone():
        return
    connection.execute("INSERT OR REPLACE INTO usage_reconcile_snapshot_heads VALUES (?,?,?,?)",
        (document["snapshot_id"], component_identity(document), int(document["format"] != RESPONSE_FORMAT), document["response_count"]))
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_snapshot_executions VALUES (?,?)",
        [(document["snapshot_id"], execution) for execution in document["executions"]])


def bootstrap_page(repository, connection, *, limit=128):
    state = connection.execute("SELECT position,complete FROM usage_reconcile_snapshot_bootstrap WHERE singleton=1").fetchone()
    if state is not None and state["complete"]:
        return None
    rows = tuple(connection.execute("SELECT rowid AS ordinal,snapshot_id,document FROM usage_snapshots "
        "WHERE rowid>? ORDER BY rowid LIMIT ?", (state["position"] if state else 0, limit)))
    for row in rows:
        repository.check_budget()
        if not connection.execute("SELECT 1 FROM usage_supersessions WHERE predecessor_id=? LIMIT 1", (row["snapshot_id"],)).fetchone():
            _head(repository, connection, row)
    position = rows[-1]["ordinal"] if rows else (state["position"] if state else 0)
    connection.execute("INSERT INTO usage_reconcile_snapshot_bootstrap VALUES (1,?,?) ON CONFLICT(singleton) "
                       "DO UPDATE SET position=excluded.position,complete=excluded.complete", (position, int(len(rows) < limit)))
    return len(rows)


def _adopt_heads(repository, connection):
    # Only successors made by this round can retire its frozen predecessors.
    predecessors = [row[0] for row in connection.execute("SELECT DISTINCT edge.predecessor_id FROM usage_reconcile_snapshot_stage stage "
        "JOIN usage_supersessions edge ON edge.successor_id=stage.snapshot_id")]
    for identity in predecessors:
        connection.execute("DELETE FROM usage_reconcile_snapshot_heads WHERE snapshot_id=?", (identity,))
        connection.execute("DELETE FROM usage_reconcile_snapshot_executions WHERE snapshot_id=?", (identity,))
    for row in connection.execute("SELECT snapshot.* FROM usage_reconcile_snapshot_stage stage JOIN usage_snapshots snapshot USING(snapshot_id) "
        "WHERE NOT EXISTS (SELECT 1 FROM usage_supersessions WHERE predecessor_id=stage.snapshot_id)"):
        _head(repository, connection, row)


def _overlaps(repository, connection, component):
    selected = {row[0] for row in connection.execute("SELECT snapshot_id FROM usage_reconcile_snapshot_heads WHERE component_id=?",
                                                    (component_identity(component),))}
    same_execution = tuple(connection.execute("SELECT DISTINCT head.* FROM json_each(?) execution "
        "CROSS JOIN usage_reconcile_snapshot_executions member ON member.execution_id=execution.value "
        "JOIN usage_reconcile_snapshot_heads head USING(snapshot_id)", (encode(component["executions"]).decode(),)))
    for row in same_execution:
        if row["legacy"] or not row["response_count"] or not component["response_keys"]:
            selected.add(row["snapshot_id"])
        elif not connection.execute("SELECT 1 FROM usage_snapshot_members member JOIN usage_reconcile_response_result result "
            "USING(provider,response_id) WHERE member.snapshot_id=? AND result.component_id IS NOT NULL LIMIT 1", (row["snapshot_id"],)).fetchone():
            selected.add(row["snapshot_id"])
    for row in connection.execute("SELECT DISTINCT member.snapshot_id FROM json_each(?) key "
        "CROSS JOIN usage_snapshot_members member ON member.provider=json_extract(key.value,'$[0]') "
        "AND member.response_id=json_extract(key.value,'$[1]') JOIN usage_reconcile_snapshot_heads head USING(snapshot_id) WHERE head.legacy=0",
        (encode(component["response_keys"]).decode(),)):
        selected.add(row[0])
    # A partially committed split may already have retired the old head when
    # an input/epoch changes. Retain that round's immutable lineage obligations
    # separately from current heads. Only still-uncovered keys need their old
    # predecessor; an existing successor already carries covered-key lineage.
    for row in connection.execute("SELECT DISTINCT member.snapshot_id FROM json_each(?) key "
        "CROSS JOIN usage_snapshot_members member ON member.provider=json_extract(key.value,'$[0]') "
        "AND member.response_id=json_extract(key.value,'$[1]') "
        "WHERE EXISTS (SELECT 1 FROM usage_reconcile_snapshot_lineage WHERE snapshot_id=member.snapshot_id) "
        "AND NOT EXISTS (SELECT 1 FROM usage_snapshot_members covered JOIN usage_reconcile_snapshot_heads head USING(snapshot_id) "
        "WHERE covered.provider=member.provider AND covered.response_id=member.response_id)",
        (encode(component["response_keys"]).decode(),)):
        selected.add(row[0])
    # Empty, legacy and fully retired partitions also overlap by execution,
    # including when no surviving numerical key can express that relation.
    for row in connection.execute("SELECT DISTINCT snapshot.snapshot_id,snapshot.document FROM json_each(?) execution "
        "CROSS JOIN usage_reconcile_snapshot_lineage lineage ON lineage.execution_id=execution.value "
        "JOIN usage_snapshots snapshot USING(snapshot_id)", (encode(component["executions"]).decode(),)):
        document = repository._snapshot_document(row)
        if (document["format"] != RESPONSE_FORMAT or not document["response_count"] or not component["response_keys"]
                or not connection.execute("SELECT 1 FROM usage_snapshot_members member JOIN usage_reconcile_response_result result "
                    "USING(provider,response_id) WHERE member.snapshot_id=? AND result.component_id IS NOT NULL LIMIT 1", (row[0],)).fetchone()):
            selected.add(row[0])
    return tuple(connection.execute("SELECT snapshot_id,document FROM usage_snapshots WHERE snapshot_id IN "
                                    "(SELECT value FROM json_each(?)) ORDER BY rowid", (encode(sorted(selected)).decode(),)))


def _component(repository, connection, identity):
    active = connection.execute("SELECT 1 FROM usage_reconcile_response_result WHERE component_id=? LIMIT 1", (identity,)).fetchone()
    active = active or connection.execute("SELECT 1 FROM usage_reconcile_empty_components WHERE component_id=? LIMIT 1", (identity,)).fetchone()
    if not active:
        connection.execute("INSERT OR REPLACE INTO usage_reconcile_snapshot_stage VALUES (?,NULL,NULL)", (identity,))
        return
    component = component_geometry(connection, identity)
    geometry = digest(component)
    rows = _overlaps(repository, connection, component)
    from .usage_snapshot_read import SnapshotRead
    read = SnapshotRead(repository, connection, rows, {row["snapshot_id"]: repository._snapshot_document(row) for row in rows})
    component["models"] = _models(read.responses(component["response_keys"]))
    previous = read.documents
    candidate = snapshot(repository.basis[0], component)
    comparable = lambda value: {key: value for key, value in value.items() if key not in {"snapshot_id", "predecessors"}}
    if len(previous) == 1 and comparable(candidate) == comparable(previous[0]):
        candidate = previous[0]
    else:
        candidate = snapshot(repository.basis[0], component, [value["snapshot_id"] for value in previous])
        connection.execute("INSERT INTO usage_snapshots VALUES (?,?)", (candidate["snapshot_id"], encode(candidate).decode()))
        connection.executemany("INSERT INTO usage_snapshot_members VALUES (?,?,?)",
                               [(candidate["snapshot_id"], *key) for key in component["response_keys"]])
        connection.executemany("INSERT INTO usage_supersessions VALUES (?,?)",
                               [(predecessor, candidate["snapshot_id"]) for predecessor in candidate["predecessors"]])
        connection.executemany("INSERT OR IGNORE INTO usage_reconcile_snapshot_lineage VALUES (?,?)",
                               [(item["snapshot_id"], execution) for item in previous for execution in item["executions"]])
    periods = connection.execute("SELECT DISTINCT period.task_id,period.completion_cycle_id FROM json_each(?) execution "
        "CROSS JOIN usage_reconcile_period_executions member ON member.execution_id=execution.value "
        "JOIN usage_reconcile_periods period USING(task_id,ordinal) WHERE period.completion_cycle_id IS NOT NULL",
        (encode(candidate["executions"]).decode(),))
    connection.executemany("INSERT OR IGNORE INTO usage_cycle_links VALUES (?,?,?)",
                           [(candidate["snapshot_id"], row["completion_cycle_id"], row["task_id"]) for row in periods])
    connection.execute("INSERT OR REPLACE INTO usage_reconcile_snapshot_stage VALUES (?,?,?)", (identity, candidate["snapshot_id"], geometry))


def advance(repository, core_reader):
    with repository.connection(write=True) as connection, core_reader() as core:
        repository.check_budget()
        basis = digest(require_inputs(repository, core, connection, classified=True))
        bootstrap = bootstrap_page(repository, connection)
        if bootstrap is not None:
            return {"phase": "snapshot_baseline", "selected": bootstrap}
        previous = connection.execute("SELECT basis,complete FROM usage_reconcile_snapshot_round WHERE singleton=1").fetchone()
        if previous is None or previous["basis"] != basis:
            # A changed basis abandons only derived work. Already committed
            # immutable candidates remain legitimate history and predecessors.
            _adopt_heads(repository, connection)
            connection.execute("INSERT OR IGNORE INTO usage_reconcile_component_work SELECT component_id FROM usage_reconcile_snapshot_stage")
            connection.execute("DELETE FROM usage_reconcile_snapshot_stage")
            connection.execute("INSERT INTO usage_reconcile_snapshot_round VALUES (1,?,0) ON CONFLICT(singleton) DO UPDATE SET basis=excluded.basis,complete=0", (basis,))
        work = connection.execute("SELECT component_id FROM usage_reconcile_component_work ORDER BY rowid LIMIT 1").fetchone()
        if work is not None:
            connection.execute("UPDATE usage_reconcile_snapshot_round SET complete=0 WHERE singleton=1")
            _component(repository, connection, work[0])
            connection.execute("DELETE FROM usage_reconcile_component_work WHERE component_id=?", (work[0],))
            repository._before_snapshot_commit(connection)
            return {"phase": "snapshot", "component_id": work[0]}
        if previous is None or not previous["complete"] or previous["basis"] != basis:
            _adopt_heads(repository, connection)
            for row in connection.execute("SELECT * FROM usage_reconcile_snapshot_stage"):
                if row["snapshot_id"] is None:
                    connection.execute("DELETE FROM usage_component_basis WHERE component_id=?", (row["component_id"],))
                else:
                    connection.execute("INSERT INTO usage_component_basis VALUES (?,?,?) ON CONFLICT(component_id) DO UPDATE SET "
                        "basis=excluded.basis,snapshot_id=excluded.snapshot_id", (row["component_id"], row["geometry_digest"], row["snapshot_id"]))
            connection.execute("DELETE FROM usage_reconcile_snapshot_stage")
            connection.execute("DELETE FROM usage_reconcile_snapshot_lineage")
            connection.execute("UPDATE usage_reconcile_snapshot_round SET complete=1 WHERE singleton=1")
            connection.execute("INSERT INTO usage_capture VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET core_digest=excluded.core_digest", (basis,))
            connection.execute("INSERT INTO usage_reconcile_capture_guard SELECT 1,?,generation FROM usage_reconcile_evidence_revision WHERE singleton=1 "
                "ON CONFLICT(singleton) DO UPDATE SET basis=excluded.basis,evidence_generation=excluded.evidence_generation", (basis,))
            connection.execute("DELETE FROM usage_dirty_turns")
            repository._before_snapshot_commit(connection)
        return {"phase": "captured", "basis": basis}
