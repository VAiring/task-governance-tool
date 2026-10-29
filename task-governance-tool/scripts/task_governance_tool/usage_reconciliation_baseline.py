"""Finite reconstruction pages after Setup, restore or tracking invalidation."""

from .usage_reconciliation_changes import _task, _turn, queue_sources
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_baseline (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),phase INTEGER NOT NULL CHECK(phase BETWEEN 0 AND 8),
        position INTEGER NOT NULL,complete INTEGER NOT NULL CHECK(complete IN (0,1)))""",
)
PHASES = ("usage_sources", "usage_responses", "usage_turns", "usage_operation_turns",
          "usage_review_boundaries", "usage_wait_turns", "usage_review_receipt_turns", "usage_response_boundaries")


def establish_page(repository, connection, *, limit=128):
    if not connection.in_transaction or type(limit) is not int or not 1 <= limit <= 128:
        raise UsageError("usage_schema_invalid")
    state = connection.execute("SELECT * FROM usage_reconcile_baseline WHERE singleton=1").fetchone()
    if state is None:
        connection.execute("INSERT INTO usage_reconcile_baseline VALUES (1,0,0,0)")
        phase, position = 0, 0
    elif state["complete"]:
        if state["phase"] != len(PHASES):
            raise UsageError("usage_schema_invalid")
        return {"complete": True, "selected": 0}
    else:
        phase, position = state["phase"], state["position"]
    if phase >= len(PHASES):
        raise UsageError("usage_schema_invalid")
    table = PHASES[phase]
    if table == "usage_response_boundaries":
        rows = tuple(connection.execute("SELECT boundary.rowid AS ordinal,boundary.*,source.thread_id AS source_thread "
            "FROM usage_response_boundaries boundary LEFT JOIN usage_sources source USING(source_id) "
            "WHERE boundary.rowid>? ORDER BY boundary.rowid LIMIT ?", (position, limit)))
    else:
        rows = tuple(connection.execute("SELECT rowid AS ordinal,* FROM " + table + " WHERE rowid>? ORDER BY rowid LIMIT ?", (position, limit)))
    if table == "usage_sources":
        queue_sources(repository, connection, {row["source_id"] for row in rows})
    for row in rows:
        repository.check_budget()
        if table == "usage_sources":
            continue
        elif table == "usage_response_boundaries":
            from .usage_response_repository import _decode_boundary_relation
            if row["source_thread"] is None:
                raise UsageError("usage_schema_invalid")
            _, _, item = _decode_boundary_relation(row)
            _turn(connection, item.thread_id, item.turn_id)
            if item.project_id == repository.basis[0]:
                _task(connection, item.task_id)
        else:
            _turn(connection, row["thread_id"], row["turn_id"])
            if table == "usage_responses":
                connection.execute("INSERT OR IGNORE INTO usage_reconcile_response_work VALUES (?,?)", (row["provider"], row["response_id"]))
            if table in {"usage_operation_turns", "usage_review_boundaries", "usage_wait_turns"} and row["project_id"] == repository.basis[0]:
                _task(connection, row["task_id"])
    if len(rows) < limit:
        phase, position = phase + 1, 0
    else:
        position = rows[-1]["ordinal"]
    connection.execute("UPDATE usage_reconcile_baseline SET phase=?,position=?,complete=? WHERE singleton=1",
                       (phase, position, int(phase == len(PHASES))))
    return {"complete": phase == len(PHASES), "selected": len(rows)}
