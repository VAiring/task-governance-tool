"""Durable base-allocation replay, separate from uncertainty and partition work.

Staging/replay belong to the existing numerical writer. No intermediate output
is publication-ready. The final reconciler must also finish the independent
uncertainty, membership and diagnostic queues against the same input basis.
"""

from dataclasses import asdict
import json

from .usage_attribution import Transition
from .usage_evidence import digest, encode
from .usage_response_ownership import allocate_turn
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_turn_inputs (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,started_at INTEGER NOT NULL CHECK(started_at>=0),
        document TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id))""",
    "CREATE INDEX usage_reconcile_turn_inputs_order ON usage_reconcile_turn_inputs(thread_id,started_at,turn_id)",
    """CREATE TABLE usage_reconcile_turn_queue (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,
        started_at INTEGER NOT NULL CHECK(started_at>=0),UNIQUE(thread_id,turn_id))""",
    "CREATE INDEX usage_reconcile_turn_queue_order ON usage_reconcile_turn_queue(thread_id,started_at,turn_id)",
    """CREATE TABLE usage_reconcile_turn_allocations (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,input_digest TEXT NOT NULL,active_before TEXT,active_after TEXT,
        document TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id))""",
    """CREATE TABLE usage_reconcile_turn_effects (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id))""",
)


def _document(row):
    if row is None:
        return None
    value = json.loads(row["document"])
    if digest(value) != row["digest"] or encode(value).decode() != row["document"]:
        raise UsageError("usage_schema_invalid")
    for field in ("thread_id", "turn_id", "started_at", "input_digest", "active_before", "active_after"):
        if field in row.keys() and (field not in value or row[field] != value[field]):
            raise UsageError("usage_schema_invalid")
    return value


def _enqueue(connection, row):
    if row is not None:
        connection.execute("INSERT INTO usage_reconcile_turn_queue(thread_id,turn_id,started_at) VALUES (?,?,?) "
                           "ON CONFLICT(thread_id,turn_id) DO UPDATE SET started_at=excluded.started_at",
                           (row["thread_id"], row["turn_id"], row["started_at"]))


def _neighbor(connection, row, *, following):
    comparison, order = (">", "ASC") if following else ("<", "DESC")
    return connection.execute("SELECT * FROM usage_reconcile_turn_inputs WHERE thread_id=? "
        f"AND (started_at,turn_id){comparison}(?,?) ORDER BY started_at {order},turn_id {order} LIMIT 1",
        (row["thread_id"], row["started_at"], row["turn_id"])).fetchone()


def stage_turn(connection, *, thread_id, turn_id, started_at, keys, events, checks, thread_gaps=()):
    """Stage an already admitted complete-turn ordering and exact core events."""
    if not connection.in_transaction or type(started_at) is not int or started_at < 0:
        raise UsageError("usage_schema_invalid")
    value = {"thread_id": thread_id, "turn_id": turn_id, "started_at": started_at, "keys": [list(key) for key in keys],
             "events": [[list(key), [[order, asdict(change)] for order, change in sorted(events.get(key, ()))]] for key in keys],
             "checks": sorted(map(list, checks)), "gaps": sorted(set(thread_gaps))}
    old = connection.execute("SELECT * FROM usage_reconcile_turn_inputs WHERE thread_id=? AND turn_id=?",
                             (thread_id, turn_id)).fetchone()
    if old is not None and _document(old) == value and old["started_at"] == started_at:
        return False
    if old is not None and old["started_at"] != started_at:
        _enqueue(connection, _neighbor(connection, old, following=True))
    connection.execute("INSERT INTO usage_reconcile_turn_inputs VALUES (?,?,?,?,?) ON CONFLICT(thread_id,turn_id) DO UPDATE SET "
                       "started_at=excluded.started_at,document=excluded.document,digest=excluded.digest",
                       (thread_id, turn_id, started_at, encode(value).decode(), digest(value)))
    _enqueue(connection, {"thread_id": thread_id, "turn_id": turn_id, "started_at": started_at})
    if old is not None and old["started_at"] != started_at:
        _enqueue(connection, _neighbor(connection, {"thread_id": thread_id, "turn_id": turn_id, "started_at": started_at}, following=True))
    return True


def remove_turn(connection, thread_id, turn_id):
    """Retire a no-longer-orderable turn without losing its old effects.

    Uncertainty membership is owned by the input/effect phases. Removing this
    base allocation changes the incoming state of its former successor even
    when that successor's original numerical inputs did not change.
    """
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    old = connection.execute("SELECT * FROM usage_reconcile_turn_inputs WHERE thread_id=? AND turn_id=?",
                             (thread_id, turn_id)).fetchone()
    if old is None:
        return False
    _document(old)
    _enqueue(connection, _neighbor(connection, old, following=True))
    connection.execute("DELETE FROM usage_reconcile_turn_inputs WHERE thread_id=? AND turn_id=?", (thread_id, turn_id))
    connection.execute("DELETE FROM usage_reconcile_turn_queue WHERE thread_id=? AND turn_id=?", (thread_id, turn_id))
    # Keep the old allocation until the effects phase replaces its contributions.
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_turn_effects VALUES (?,?)", (thread_id, turn_id))
    return True


def replay_one(connection, *, execution_task):
    """Replay the earliest dirty turn of the oldest queued session.

    One committed turn is progress even if the event ends before the next turn.
    Only base-allocation propagation may stop at active-state convergence.
    ``execution_task`` reads an exact admitted core execution when an incoming
    owner is absent from this turn's events.
    """
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    first = connection.execute("SELECT thread_id FROM usage_reconcile_turn_queue ORDER BY sequence LIMIT 1").fetchone()
    if first is None:
        return None
    row = connection.execute("SELECT input.* FROM usage_reconcile_turn_queue work JOIN usage_reconcile_turn_inputs input "
        "ON input.thread_id=work.thread_id AND input.turn_id=work.turn_id WHERE work.thread_id=? "
        "ORDER BY work.started_at,work.turn_id LIMIT 1", (first[0],)).fetchone()
    if row is None:
        raise UsageError("usage_schema_invalid")
    inputs = _document(row)
    if type(inputs) is not dict or set(inputs) != {"thread_id", "turn_id", "started_at", "keys", "events", "checks", "gaps"}:
        raise UsageError("usage_schema_invalid")
    previous = _neighbor(connection, row, following=False)
    incoming = None
    if previous is not None:
        prior = connection.execute("SELECT * FROM usage_reconcile_turn_allocations WHERE thread_id=? AND turn_id=?",
                                   (previous["thread_id"], previous["turn_id"])).fetchone()
        if prior is None or prior["input_digest"] != previous["digest"]:
            _enqueue(connection, previous)
            return {"thread_id": row["thread_id"], "turn_id": row["turn_id"], "deferred": True}
        _document(prior)
        incoming = prior["active_after"]
    keys = tuple(map(tuple, inputs["keys"]))
    events = {tuple(key): tuple((order, Transition(**change)) for order, change in values) for key, values in inputs["events"]}
    tasks = {change.execution_id: change.task_id for values in events.values() for _, change in values
             if change.execution_id is not None}
    if incoming is not None and incoming not in tasks:
        tasks[incoming] = execution_task(incoming)
        if tasks[incoming] is None:
            raise UsageError("usage_pending")
    allocated = allocate_turn(incoming, keys, events, set(map(tuple, inputs["checks"])), tasks, inputs["gaps"])
    value = {"thread_id": row["thread_id"], "turn_id": row["turn_id"], "input_digest": row["digest"],
             "active_before": incoming, "active_after": allocated.active,
             "owners": [[list(key), sorted(members)] for key, members in sorted(allocated.owners.items())],
             "actions": [[list(key), [list(action) for action in actions]] for key, actions in sorted(allocated.actions.items())],
             "gaps": [[key, sorted(gaps)] for key, gaps in sorted(allocated.gaps.items())],
             "before": [[list(key), sorted(members)] for key, members in sorted(allocated.state_before.items())],
             "unknown": sorted(map(list, allocated.unknown))}
    old = connection.execute("SELECT * FROM usage_reconcile_turn_allocations WHERE thread_id=? AND turn_id=?",
                             (row["thread_id"], row["turn_id"])).fetchone()
    old_value = _document(old)
    connection.execute("INSERT INTO usage_reconcile_turn_allocations VALUES (?,?,?,?,?,?,?) ON CONFLICT(thread_id,turn_id) DO UPDATE SET "
        "input_digest=excluded.input_digest,active_before=excluded.active_before,active_after=excluded.active_after,"
        "document=excluded.document,digest=excluded.digest",
        (row["thread_id"], row["turn_id"], row["digest"], incoming, allocated.active, encode(value).decode(), digest(value)))
    if old is None or old["active_after"] != allocated.active:
        _enqueue(connection, _neighbor(connection, row, following=True))
    if old_value != value:
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_turn_effects VALUES (?,?)", (row["thread_id"], row["turn_id"]))
        from .usage_reconciliation_pending import invalidate_turn
        invalidate_turn(connection, row["thread_id"], row["turn_id"], row["started_at"])
    connection.execute("DELETE FROM usage_reconcile_turn_queue WHERE thread_id=? AND turn_id=?", (row["thread_id"], row["turn_id"]))
    return {"thread_id": row["thread_id"], "turn_id": row["turn_id"], "deferred": False,
            "changed": old_value != value, "active": allocated.active}
