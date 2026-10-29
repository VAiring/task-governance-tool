"""Selected Task operation resolution and old/new boundary invalidation."""

from dataclasses import asdict

from .usage_attribution_repository import resolve_operations
from .usage_evidence import encode, digest
from .usage_reconciliation_relations import read_selected_relations
from .usage_response_binding import qualify_responses
from .usage_turn_adapter import OperationObservation
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_events (
        transition_id TEXT PRIMARY KEY,source_order INTEGER NOT NULL UNIQUE,
        task_id TEXT NOT NULL,thread_id TEXT,execution_id TEXT,
        previous_status TEXT,current_status TEXT NOT NULL,
        document TEXT NOT NULL,digest TEXT NOT NULL)""",
    "CREATE INDEX usage_reconcile_events_thread_order ON usage_reconcile_events(thread_id,source_order)",
    "CREATE INDEX usage_reconcile_events_task ON usage_reconcile_events(task_id,source_order)",
    "CREATE INDEX usage_reconcile_events_boundary_turn ON usage_reconcile_events("
    "json_extract(document,'$.boundary.thread_id'),json_extract(document,'$.boundary.turn_id'))",
    "CREATE INDEX usage_reconcile_events_boundary_response ON usage_reconcile_events("
    "json_extract(document,'$.boundary.provider'),json_extract(document,'$.boundary.response_id'))",
    "CREATE INDEX usage_reconcile_events_execution ON usage_reconcile_events(execution_id,source_order,task_id)",
    """CREATE TABLE usage_reconcile_displacements (
        transition_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,thread_id TEXT NOT NULL,
        held_transition TEXT NOT NULL,held_order INTEGER NOT NULL,execution_id TEXT NOT NULL)""",
    "CREATE INDEX usage_reconcile_displacements_task ON usage_reconcile_displacements(task_id)",
    """CREATE TABLE usage_reconcile_event_work (
        transition_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,thread_id TEXT,source_order INTEGER NOT NULL)""",
    """CREATE TABLE usage_reconcile_unresolved (
        task_id TEXT NOT NULL,event_id TEXT NOT NULL,PRIMARY KEY(task_id,event_id))""",
    """CREATE TABLE usage_reconcile_global_work (kind TEXT PRIMARY KEY CHECK(kind='operation_unbound'))""",
    """CREATE TABLE usage_reconcile_input_turn_work (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id))""",
)


def _turn_work(connection, thread, turn):
    if thread is not None and turn is not None:
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_input_turn_work VALUES (?,?)", (thread, turn))


def _response_work(connection, boundary):
    if boundary is None:
        return
    for row in connection.execute("SELECT DISTINCT position.thread_id,position.turn_id FROM usage_response_positions position "
        "JOIN usage_sources source ON source.source_id=position.source_id AND source.incarnation=position.incarnation "
        "WHERE position.provider=? AND position.response_id=?", (boundary["provider"], boundary["response_id"])):
        _turn_work(connection, *row)


def _old_value(row):
    from .usage_reconciliation_core import _decode
    value = _decode(row)
    if value is not None:
        change = value["transition"]
        if any(row[field] != change[field] for field in ("transition_id", "task_id", "execution_id", "previous_status", "current_status")) or (
                row["thread_id"] != change["actor_session_id"] or row["source_order"] != value["order"]):
            raise UsageError("usage_schema_invalid")
    return value


def reconcile_task(repository, core, connection, task_id, transitions):
    """The owning phase supplies admitted (core append ordinal, Transition) pairs.

    Resolve pending event/manifest joins even when a core projection is equal.
    This function handles operations; the parallel participation phase owns the
    exact review/wait-anchor joins. Neither can acknowledge the other's work.
    """
    if not core.in_transaction or not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    repository.check_budget()
    relations = read_selected_relations(connection, task_ids={task_id})
    rows = tuple(connection.execute("SELECT operation.*,conflict.event_id AS conflicting FROM usage_operation_turns operation "
        "LEFT JOIN usage_operation_conflicts conflict USING(event_id) WHERE operation.project_id=? AND operation.task_id=? ORDER BY operation.rowid",
        (repository.basis[0], task_id)))
    old_operations = tuple(OperationObservation(**{field: row[field] for field in OperationObservation.__dataclass_fields__})
                           for row in rows if row["conflicting"] is None)
    operations = tuple(OperationObservation(item.thread_id, item.turn_id, item.project_id, item.task_id,
                                            item.event_id, item.generation, item.status)
                       for _, _, item in relations.current if item.event_id is not None)
    resolved = resolve_operations(core, old_operations + operations, check_budget=repository.check_budget)
    response_keys = sorted({(item.provider, item.response_id) for _, _, item in relations.current if item.response_id is not None})
    numerical = {(row["provider"], row["response_id"]): (row["thread_id"], row["turn_id"]) for row in connection.execute(
        "SELECT response.provider,response.response_id,response.thread_id,response.turn_id FROM usage_responses response "
        "JOIN json_each(?) key ON response.provider=json_extract(key.value,'$[0]') AND response.response_id=json_extract(key.value,'$[1]')",
        (encode(response_keys).decode(),))}
    qualified = qualify_responses(repository.basis[0], relations.positions, relations.current, relations.history, numerical, resolved)
    legacy = {identity: (item.thread_id, item.turn_id) for item in old_operations if (identity := resolved[item]) is not None}
    before = {row["transition_id"]: (row, _old_value(row)) for row in connection.execute(
        "SELECT * FROM usage_reconcile_events WHERE task_id=?", (task_id,))}
    incoming = set()
    previous_transition = None
    held, displacements = None, {}
    for order, transition in transitions:
        repository.check_budget()
        if transition.task_id != task_id or type(order) is not int or order < 1:
            raise UsageError("usage_schema_invalid")
        identity = transition.transition_id
        incoming.add(identity)
        boundary = qualified.bindings.get(identity)
        affected = transition.execution_id
        if (affected is None and transition.policy_version == 1 and transition.current_status in {"ready", "cancelled"}
                and previous_transition is not None and previous_transition.policy_version == 1
                and previous_transition.current_status == transition.previous_status):
            affected = previous_transition.execution_id
        previous_transition = transition
        if transition.policy_version == 1:
            if transition.current_status in {"in_progress", "review_pending"} and transition.execution_id is not None:
                held = (identity, order, transition.execution_id, transition.actor_session_id)
            elif transition.current_status in {"paused", "blocked", "ready", "cancelled", "done"}:
                if held is not None and held[3] is not None and held[3] != transition.actor_session_id:
                    displacements[identity] = (identity, task_id, held[3], held[0], held[1], held[2])
                held = None
        value = {"order": order, "transition": asdict(transition), "affected_execution": affected,
                 "legacy": list(legacy[identity]) if identity in legacy else None,
                 "boundary": asdict(boundary) if boundary is not None else None}
        previous = before.get(identity)
        if previous is not None and previous[1] == value:
            continue
        executions = {transition.execution_id, previous[1]["transition"]["execution_id"] if previous else None} - {None}
        connection.executemany("INSERT OR IGNORE INTO usage_reconcile_execution_work VALUES (?)", [(value,) for value in sorted(executions)])
        if previous is not None and previous[1]["boundary"] is not None:
            old = previous[1]["boundary"]
            _turn_work(connection, old["thread_id"], old["turn_id"])
            _response_work(connection, old)
        if boundary is not None:
            _turn_work(connection, boundary.thread_id, boundary.turn_id)
            _response_work(connection, asdict(boundary))
        connection.execute("INSERT INTO usage_reconcile_events VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(transition_id) DO UPDATE SET "
            "source_order=excluded.source_order,task_id=excluded.task_id,thread_id=excluded.thread_id,execution_id=excluded.execution_id,"
            "previous_status=excluded.previous_status,current_status=excluded.current_status,document=excluded.document,digest=excluded.digest",
            (identity, order, task_id, transition.actor_session_id, transition.execution_id, transition.previous_status,
             transition.current_status, encode(value).decode(), digest(value)))
        connection.execute("INSERT INTO usage_reconcile_event_work VALUES (?,?,?,?) ON CONFLICT(transition_id) DO UPDATE SET "
            "task_id=excluded.task_id,thread_id=excluded.thread_id,source_order=excluded.source_order",
            (identity, task_id, transition.actor_session_id, order))
    for identity in before.keys() - incoming:
        row, value = before[identity]
        if value["transition"]["execution_id"] is not None:
            connection.execute("INSERT OR IGNORE INTO usage_reconcile_execution_work VALUES (?)", (value["transition"]["execution_id"],))
        if value["boundary"] is not None:
            _turn_work(connection, value["boundary"]["thread_id"], value["boundary"]["turn_id"])
            _response_work(connection, value["boundary"])
        connection.execute("INSERT INTO usage_reconcile_event_work VALUES (?,?,?,?) ON CONFLICT(transition_id) DO UPDATE SET "
            "task_id=excluded.task_id,thread_id=excluded.thread_id,source_order=excluded.source_order",
            (identity, task_id, row["thread_id"], row["source_order"]))
        connection.execute("DELETE FROM usage_reconcile_events WHERE transition_id=?", (identity,))
    old_displacements = {row[0]: tuple(row) for row in connection.execute(
        "SELECT * FROM usage_reconcile_displacements WHERE task_id=?", (task_id,))}
    for identity in old_displacements.keys() | displacements.keys():
        if old_displacements.get(identity) == displacements.get(identity):
            continue
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_rule_work VALUES ('displaced',?)", (identity,))
        connection.execute("DELETE FROM usage_reconcile_displacements WHERE transition_id=?", (identity,))
        if identity in displacements:
            connection.execute("INSERT INTO usage_reconcile_displacements VALUES (?,?,?,?,?,?)", displacements[identity])
    was_unresolved = connection.execute("SELECT 1 FROM usage_reconcile_unresolved LIMIT 1").fetchone() is not None
    connection.execute("DELETE FROM usage_reconcile_unresolved WHERE task_id=?", (task_id,))
    connection.executemany("INSERT INTO usage_reconcile_unresolved VALUES (?,?)", [(task_id, identity) for identity in sorted(qualified.unresolved)])
    is_unresolved = connection.execute("SELECT 1 FROM usage_reconcile_unresolved LIMIT 1").fetchone() is not None
    if was_unresolved != is_unresolved:
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_global_work VALUES ('operation_unbound')")
        connection.execute("DELETE FROM usage_reconcile_global_progress WHERE kind='operation_unbound'")
    return qualified
