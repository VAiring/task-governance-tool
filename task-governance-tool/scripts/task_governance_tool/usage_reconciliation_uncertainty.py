"""Indexed missing/reversed boundary fences, separate from base convergence.

Core append ordinals select boundary dependencies. Admitted response positions
select affected numerical keys. These orders are distinct and never substitute
for one another. Old/new ranges are queued before a rule is replaced.
"""

from .usage_evidence import digest, encode
from .usage_reconciliation_core import _decode
from .usage_reconciliation_events import _old_value
from .usage_reconciliation_overlays import event_position
from .usage_reconciliation_ranges import MAX_CLOCK, consumers, replace_range, enqueue_range
from .usage_reconciliation_membership import replace_contribution
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_event_locations (
        transition_id TEXT PRIMARY KEY,source_order INTEGER NOT NULL,task_id TEXT NOT NULL,thread_id TEXT,
        execution_id TEXT,affected_execution TEXT,is_beginning INTEGER NOT NULL,current_status TEXT NOT NULL,
        clock INTEGER,turn_id TEXT,ordinal INTEGER)""",
    "CREATE INDEX usage_reconcile_location_thread ON usage_reconcile_event_locations(thread_id,source_order) WHERE clock IS NOT NULL",
    "CREATE INDEX usage_reconcile_location_task ON usage_reconcile_event_locations(thread_id,task_id,source_order) WHERE clock IS NOT NULL",
    "CREATE INDEX usage_reconcile_location_beginning ON usage_reconcile_event_locations(thread_id,is_beginning,source_order) WHERE clock IS NOT NULL",
    """CREATE TABLE usage_reconcile_uncertainty (
        kind TEXT NOT NULL,origin TEXT NOT NULL,thread_id TEXT NOT NULL,document TEXT NOT NULL,digest TEXT NOT NULL,
        PRIMARY KEY(kind,origin))""",
    "CREATE TABLE usage_reconcile_rule_work(kind TEXT NOT NULL,origin TEXT NOT NULL,PRIMARY KEY(kind,origin))",
    """CREATE TABLE usage_reconcile_rule_events (
        transition_id TEXT NOT NULL,kind TEXT NOT NULL,origin TEXT NOT NULL,PRIMARY KEY(transition_id,kind,origin))""",
    "CREATE INDEX usage_reconcile_rule_event_origins ON usage_reconcile_rule_events(kind,origin,transition_id)",
    """CREATE TABLE usage_reconcile_rule_namespaces (
        kind TEXT NOT NULL,origin TEXT NOT NULL,namespace TEXT NOT NULL,PRIMARY KEY(kind,origin,namespace))""",
    """CREATE TABLE usage_reconcile_turn_uncertainty (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,origin TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id,origin))""",
)


def _queue(connection, kind, origin):
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_rule_work VALUES (?,?)", (kind, origin))


def _point(row):
    return [row["clock"], row["turn_id"], row["ordinal"]] if row is not None and row["clock"] is not None else None


def _neighbor(connection, row, *, following):
    comparison, order = (">", "ASC") if following else ("<", "DESC")
    return connection.execute("SELECT * FROM usage_reconcile_event_locations WHERE thread_id=? AND clock IS NOT NULL "
        f"AND source_order{comparison}? ORDER BY source_order {order} LIMIT 1", (row["thread_id"], row["source_order"])).fetchone()


def _scope(thread, kind, task=""):
    return encode(["core-boundary", thread, kind, task]).decode()


def _dependents(connection, row):
    if row is None or row["thread_id"] is None:
        return
    for selected in connection.execute("SELECT kind,origin FROM usage_reconcile_rule_events WHERE transition_id=?", (row["transition_id"],)):
        _queue(connection, *selected)
    scopes = [_scope(row["thread_id"], "all"), _scope(row["thread_id"], "task", row["task_id"])]
    if row["is_beginning"]:
        scopes.append(_scope(row["thread_id"], "beginning"))
    import json
    for namespace in scopes:
        for _, identity in consumers(connection, namespace, "", row["source_order"]):
            _queue(connection, *json.loads(identity)[:2])
    if row["clock"] is not None:
        previous = _neighbor(connection, row, following=False)
        if previous is not None:
            _queue(connection, "reversal", previous["transition_id"])
        following = _neighbor(connection, row, following=True)
        if following is not None:
            # A newly located predecessor moves the next reversal's start,
            # even when this event is not itself part of a reversed pair.
            _queue(connection, "reversal", following["transition_id"])
        _queue(connection, "reversal", row["transition_id"])


def locate_one(connection):
    work = connection.execute("SELECT transition_id FROM usage_reconcile_event_work ORDER BY rowid LIMIT 1").fetchone()
    if work is None:
        return None
    identity = work[0]
    old = connection.execute("SELECT * FROM usage_reconcile_event_locations WHERE transition_id=?", (identity,)).fetchone()
    _dependents(connection, old)
    event = connection.execute("SELECT * FROM usage_reconcile_events WHERE transition_id=?", (identity,)).fetchone()
    if event is None:
        connection.execute("DELETE FROM usage_reconcile_event_locations WHERE transition_id=?", (identity,))
        current = None
    else:
        value = _old_value(event)
        change = value["transition"]
        position = event_position(connection, identity)
        point = position[1] if position is not None else (None, None, None)
        current = (identity, value["order"], change["task_id"], change["actor_session_id"], change["execution_id"], value["affected_execution"],
                   int(change["current_status"] == "in_progress" and change["previous_status"] not in {"in_progress", "review_pending"}),
                   change["current_status"], *point)
        connection.execute("INSERT INTO usage_reconcile_event_locations VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(transition_id) DO UPDATE SET "
            "source_order=excluded.source_order,task_id=excluded.task_id,thread_id=excluded.thread_id,execution_id=excluded.execution_id,"
            "affected_execution=excluded.affected_execution,is_beginning=excluded.is_beginning,current_status=excluded.current_status,"
            "clock=excluded.clock,turn_id=excluded.turn_id,ordinal=excluded.ordinal", current)
        current = connection.execute("SELECT * FROM usage_reconcile_event_locations WHERE transition_id=?", (identity,)).fetchone()
    _dependents(connection, current)
    if _point(old) != _point(current):
        # The interval's core endpoints can stay equal while a repeated
        # response moves its located cutoff. Revisit only intervals using
        # this event, including their already processed numerical turns.
        for task in {row["task_id"] for row in (old, current) if row is not None}:
            for row in connection.execute("SELECT document,digest FROM usage_reconcile_intervals WHERE task_id=? AND kind='fallback'", (task,)):
                interval = _decode(row)
                if identity in interval["origins"]:
                    enqueue_range(connection, interval["coverage"])
    _queue(connection, "missing", identity)
    _queue(connection, "reversal", identity)
    connection.execute("DELETE FROM usage_reconcile_event_work WHERE transition_id=?", (identity,))
    return identity


def _applicable(connection, row, *, following):
    comparison, direction = (">", "ASC") if following else ("<", "DESC")
    candidates = []
    for predicate, parameter in (("task_id=?", row["task_id"]), ("is_beginning=?", 1)):
        found = connection.execute("SELECT * FROM usage_reconcile_event_locations WHERE thread_id=? AND clock IS NOT NULL "
            f"AND {predicate} AND source_order{comparison}? ORDER BY source_order {direction} LIMIT 1",
            (row["thread_id"], parameter, row["source_order"])).fetchone()
        if found is not None:
            candidates.append(found)
    return (min if following else max)(candidates, key=lambda item: item["source_order"]) if candidates else None


def _missing(connection, row):
    if row is None or row["clock"] is not None or row["thread_id"] is None or row["affected_execution"] is None:
        return None, (), ()
    if connection.execute("SELECT 1 FROM usage_reconcile_positions WHERE thread_id=? LIMIT 1", (row["thread_id"],)).fetchone() is None:
        # No ordered owner stream exists to fence. Independent reviewer
        # participation does not inherit a fabricated owner-stream gap.
        return None, (), ()
    before, after = _applicable(connection, row, following=False), _applicable(connection, row, following=True)
    first, last = _point(before), _point(after)
    if first is not None and not row["is_beginning"]:
        first[2] += 1
    stop = row["current_status"] in {"paused", "blocked", "ready", "cancelled"}
    value = {"thread": row["thread_id"], "first": first, "last": last, "owner_filter": row["affected_execution"] if stop else None,
             "gaps": [] if stop else [[row["affected_execution"], "response_binding_unknown"]],
             "conditional_gaps": [[row["affected_execution"], "response_binding_unknown"]] if stop else []}
    lower, upper = before["source_order"] if before else 0, after["source_order"] if after else MAX_CLOCK
    dependencies = [(_scope(row["thread_id"], "task", row["task_id"]), lower, upper),
                    (_scope(row["thread_id"], "beginning"), lower, upper)]
    return value, [item["transition_id"] for item in (row, before, after) if item is not None], dependencies


def _reversal(connection, left):
    if left is None or left["clock"] is None:
        return None, (), ()
    right = _neighbor(connection, left, following=True)
    if right is None or _point(left) <= _point(right):
        return None, (), ()
    previous = _neighbor(connection, left, following=False)
    upper = connection.execute("SELECT * FROM usage_reconcile_event_locations WHERE thread_id=? AND clock IS NOT NULL "
        "AND source_order>? AND (clock,turn_id,ordinal)>(?,?,?) ORDER BY source_order LIMIT 1",
        (left["thread_id"], right["source_order"], *_point(left))).fetchone()
    value = {"thread": left["thread_id"], "first": _point(previous), "last": _point(upper), "owner_filter": None,
             "gaps": [[execution, "response_order_unknown"] for execution in sorted({left["execution_id"], right["execution_id"]} - {None})],
             "conditional_gaps": []}
    events = [row["transition_id"] for row in (previous, left, right, upper) if row is not None]
    return value, events, [(_scope(left["thread_id"], "all"), right["source_order"], upper["source_order"] if upper else MAX_CLOCK)]


def _coverage(value):
    if value is None:
        return None
    return {"thread_id": value["thread"], "first": value["first"][0] if value["first"] is not None else 0,
            "last": value["last"][0] if value["last"] is not None else MAX_CLOCK, "exact": None, "diagnostics": []}


def _displaced(connection, origin):
    row = connection.execute("SELECT * FROM usage_reconcile_displacements WHERE transition_id=?", (origin,)).fetchone()
    if row is None:
        return None, (), ()
    before = connection.execute("SELECT * FROM usage_reconcile_event_locations WHERE thread_id=? AND clock IS NOT NULL "
        "AND source_order<=? ORDER BY source_order DESC LIMIT 1", (row["thread_id"], row["held_order"])).fetchone()
    after = _applicable(connection, {"thread_id": row["thread_id"], "task_id": row["task_id"], "source_order": row["held_order"]}, following=True)
    first = _point(before)
    if first is not None and before["source_order"] == row["held_order"]:
        first[2] += 1
    value = {"thread": row["thread_id"], "first": first, "last": _point(after), "owner_filter": None,
             "gaps": [], "conditional_gaps": [[row["execution_id"], "response_binding_unknown"]]}
    dependencies = [(_scope(row["thread_id"], "all"), before["source_order"] if before else 0, row["held_order"]),
                    (_scope(row["thread_id"], "task", row["task_id"]), row["held_order"], after["source_order"] if after else MAX_CLOCK),
                    (_scope(row["thread_id"], "beginning"), row["held_order"], after["source_order"] if after else MAX_CLOCK)]
    return value, [item["transition_id"] for item in (before, after) if item is not None], dependencies


def replace_rule(connection, kind, origin, value, events, dependencies):
    identity = encode([kind, origin]).decode()
    old = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_uncertainty WHERE kind=? AND origin=?", (kind, origin)).fetchone())
    for row in connection.execute("SELECT namespace FROM usage_reconcile_rule_namespaces WHERE kind=? AND origin=?", (kind, origin)):
        # Dependency identity includes the namespace, as one rule has several.
        replace_range(connection, "dependency", encode([kind, origin, row[0]]).decode(), None)
    connection.execute("DELETE FROM usage_reconcile_rule_namespaces WHERE kind=? AND origin=?", (kind, origin))
    connection.execute("DELETE FROM usage_reconcile_rule_events WHERE kind=? AND origin=?", (kind, origin))
    if old != value:
        for changed in (old, value):
            if changed is not None:
                coverage = _coverage(changed)
                if coverage["first"] <= coverage["last"]:
                    enqueue_range(connection, coverage)
        coverage = _coverage(value)
        replace_range(connection, "uncertainty", identity, coverage if coverage is not None and coverage["first"] <= coverage["last"] else None)
        replace_contribution(connection, "uncertainty", identity, gaps=value["gaps"] if value is not None else ())
        if value is None:
            connection.execute("DELETE FROM usage_reconcile_uncertainty WHERE kind=? AND origin=?", (kind, origin))
        else:
            connection.execute("INSERT INTO usage_reconcile_uncertainty VALUES (?,?,?,?,?) ON CONFLICT(kind,origin) DO UPDATE SET "
                "thread_id=excluded.thread_id,document=excluded.document,digest=excluded.digest",
                (kind, origin, value["thread"], encode(value).decode(), digest(value)))
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_rule_events VALUES (?,?,?)", [(event, kind, origin) for event in events])
    for namespace, lower, upper in dependencies:
        scoped = encode([kind, origin, namespace]).decode()
        replace_range(connection, "dependency", scoped, {"thread_id": namespace, "first": lower, "last": upper, "exact": None})
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_rule_namespaces VALUES (?,?,?)", (kind, origin, namespace))


def rebuild_one(connection):
    work = connection.execute("SELECT kind,origin FROM usage_reconcile_rule_work ORDER BY rowid LIMIT 1").fetchone()
    if work is None:
        return None
    kind, origin = work
    if kind == "pending":
        from .usage_reconciliation_pending import rebuild
        rebuild(connection, origin)
        connection.execute("DELETE FROM usage_reconcile_rule_work WHERE kind=? AND origin=?", (kind, origin))
        return kind, origin
    row = connection.execute("SELECT * FROM usage_reconcile_event_locations WHERE transition_id=?", (origin,)).fetchone()
    if kind == "missing":
        value, events, dependencies = _missing(connection, row)
    elif kind == "reversal":
        value, events, dependencies = _reversal(connection, row)
    elif kind == "displaced":
        value, events, dependencies = _displaced(connection, origin)
    else:
        raise UsageError("usage_schema_invalid")
    replace_rule(connection, kind, origin, value, events, dependencies)
    connection.execute("DELETE FROM usage_reconcile_rule_work WHERE kind=? AND origin=?", (kind, origin))
    return kind, origin


def apply_turn(connection, thread, turn):
    """Apply selected fences to pre-fallback base owners of one ordered turn."""
    import json
    clock_row = connection.execute("SELECT started_at FROM usage_turns WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
    selected = [json.loads(origin) for kind, origin in consumers(connection, thread, turn, clock_row[0] if clock_row else None) if kind == "uncertainty"]
    rules = [(row["kind"], row["origin"], _decode(row)) for row in connection.execute(
        "SELECT rule.* FROM json_each(?) selected CROSS JOIN usage_reconcile_uncertainty rule "
        "WHERE rule.kind=json_extract(selected.value,'$[0]') AND rule.origin=json_extract(selected.value,'$[1]')", (encode(selected).decode(),))]
    # Last-position lookup locates rule endpoints. Membership instead tests
    # every occurrence in the range, including an earlier repeated key.
    positions = {(row["provider"], row["response_id"]): [row["started_at"], row["turn_id"], row["ordinal"]]
                 for row in connection.execute("SELECT * FROM usage_reconcile_positions WHERE thread_id=? AND turn_id=?", (thread, turn))}
    owners = {}
    for row in connection.execute("SELECT membership.provider,membership.response_id,membership.execution_id FROM json_each(?) key "
        "CROSS JOIN usage_reconcile_membership membership ON membership.provider=json_extract(key.value,'$[0]') "
        "AND membership.response_id=json_extract(key.value,'$[1]') WHERE membership.kind='base'", (encode(sorted(positions)).decode(),)):
        owners.setdefault((row[0], row[1]), set()).add(row[2])
    previous = {row[0] for row in connection.execute("SELECT origin FROM usage_reconcile_turn_uncertainty WHERE thread_id=? AND turn_id=?", (thread, turn))}
    current = set()
    for kind, identity, rule in rules:
        origin = encode([kind, identity, thread, turn]).decode()
        current.add(origin)
        affected = {key for key, point in positions.items() if (rule["first"] is None or rule["first"] <= point)
                    and (rule["last"] is None or point < rule["last"])
                    and (rule["owner_filter"] is None or rule["owner_filter"] in owners.get(key, ()))}
        replace_contribution(connection, "uncertainty", origin, unknown=affected,
                             gaps=rule["conditional_gaps"] if affected else ())
    for origin in previous - current:
        replace_contribution(connection, "uncertainty", origin)
    connection.execute("DELETE FROM usage_reconcile_turn_uncertainty WHERE thread_id=? AND turn_id=?", (thread, turn))
    connection.executemany("INSERT INTO usage_reconcile_turn_uncertainty VALUES (?,?,?)", [(thread, turn, origin) for origin in sorted(current)])
