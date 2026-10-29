"""Pending-call fences use admitted source positions and base ownership only."""

import json

from .usage_evidence import encode, digest
from .usage_reconciliation_core import _decode
from .usage_reconciliation_ranges import MAX_CLOCK, consumers, replace_range
from .usage_reconciliation_uncertainty import replace_rule, _queue


TABLES = (
    """CREATE TABLE usage_reconcile_source_positions (
        source_id TEXT NOT NULL,incarnation INTEGER NOT NULL,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,
        record_order INTEGER NOT NULL,provider TEXT NOT NULL,response_id TEXT NOT NULL,
        clock INTEGER NOT NULL,ordinal INTEGER NOT NULL,
        PRIMARY KEY(source_id,incarnation,thread_id,turn_id,record_order,provider,response_id))""",
    "CREATE INDEX usage_reconcile_source_position_turn ON usage_reconcile_source_positions(thread_id,turn_id)",
    "CREATE INDEX usage_reconcile_source_position_order ON usage_reconcile_source_positions(thread_id,clock,turn_id,ordinal)",
    """CREATE TABLE usage_reconcile_pending_origins (
        origin TEXT PRIMARY KEY,source_id TEXT NOT NULL,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,
        document TEXT NOT NULL,digest TEXT NOT NULL)""",
    "CREATE INDEX usage_reconcile_pending_source ON usage_reconcile_pending_origins(source_id)",
    "CREATE INDEX usage_reconcile_pending_turn ON usage_reconcile_pending_origins(thread_id,turn_id)",
    """CREATE TABLE usage_reconcile_resets (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,clock INTEGER NOT NULL,ordinal INTEGER NOT NULL,execution_id TEXT NOT NULL,
        PRIMARY KEY(thread_id,turn_id,ordinal,execution_id))""",
    "CREATE INDEX usage_reconcile_reset_order ON usage_reconcile_resets(thread_id,execution_id,clock,turn_id,ordinal)",
)


def invalidate_turn(connection, thread, turn, *clocks):
    selected = {row[0] for row in connection.execute(
        "SELECT origin FROM usage_reconcile_pending_origins WHERE thread_id=? AND turn_id=?", (thread, turn))}
    for clock in set(clocks):
        selected.update(origin for kind, origin in consumers(connection, thread, turn, clock) if kind == "pending_input")
    for origin in selected:
        _queue(connection, "pending", origin)


def stage_positions(connection, thread, turn, clock, sequence, positions, events):
    """Only qualified current source positions enter the pending-origin index."""
    indices = {tuple(key): ordinal for ordinal, key in enumerate(sequence or ())}
    connection.execute("DELETE FROM usage_reconcile_source_positions WHERE thread_id=? AND turn_id=?", (thread, turn))
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_source_positions VALUES (?,?,?,?,?,?,?,?,?)", [
        (source, incarnation, thread, turn, item.record_order, item.provider, item.response_id, clock, indices[item.provider, item.response_id])
        for source, incarnation, item in positions if (item.provider, item.response_id) in indices])
    connection.execute("DELETE FROM usage_reconcile_resets WHERE thread_id=? AND turn_id=?", (thread, turn))
    identities = [change.transition_id for values in events.values() for _, change in values]
    affected = {row[0]: row[1] for row in connection.execute(
        "SELECT transition_id,json_extract(document,'$.affected_execution') FROM usage_reconcile_events "
        "WHERE transition_id IN (SELECT value FROM json_each(?))", (encode(identities).decode(),))}
    resets = set()
    for key, values in events.items():
        if key not in indices:
            continue
        for _, change in values:
            if change.current_status == "in_progress" and change.previous_status not in {"in_progress", "review_pending"}:
                resets.add((thread, turn, clock, indices[key], ""))
            if (change.current_status in {"in_progress", "review_pending"} or change.previous_status in {"in_progress", "review_pending"}):
                execution = affected.get(change.transition_id)
                if execution is not None:
                    resets.add((thread, turn, clock, indices[key], execution))
    connection.executemany("INSERT INTO usage_reconcile_resets VALUES (?,?,?,?,?)", sorted(resets))


def source_one(connection):
    row = connection.execute("SELECT source_id,after_document FROM usage_reconcile_pending_work ORDER BY rowid LIMIT 1").fetchone()
    if row is None:
        return None
    source, document = row
    value = json.loads(document) if document is not None else None
    before = {item["origin"]: _decode(item) for item in connection.execute(
        "SELECT * FROM usage_reconcile_pending_origins WHERE source_id=?", (source,))}
    incoming = {}
    if value is not None:
        for call, slot, turn, order, provider, response in value["pending"]:
            origin = encode([source, value["incarnation"], call, slot, turn, order]).decode()
            incoming[origin] = {"source": source, "incarnation": value["incarnation"], "thread": value["thread"],
                                "turn": turn, "order": order, "provider": provider, "response": response}
    # Compare with actually adopted origins, including a partly consumed earlier
    # incarnation, not merely the oldest coalesced before-side of the source.
    for origin in before.keys() | incoming.keys():
        if before.get(origin) == incoming.get(origin):
            continue
        _queue(connection, "pending", origin)
        connection.execute("DELETE FROM usage_reconcile_pending_origins WHERE origin=?", (origin,))
        if origin in incoming:
            item = incoming[origin]
            connection.execute("INSERT INTO usage_reconcile_pending_origins VALUES (?,?,?,?,?,?)",
                (origin, source, item["thread"], item["turn"], encode(item).decode(), digest(item)))
    connection.execute("DELETE FROM usage_reconcile_pending_work WHERE source_id=?", (source,))
    return source


def _point(row):
    return [row["clock"], row["turn_id"], row["ordinal"]] if row is not None else None


def _origin_position(connection, item, clock):
    explicit = connection.execute("SELECT position.* FROM usage_reconcile_last_positions position "
        "JOIN usage_responses response USING(provider,response_id) LEFT JOIN usage_conflicts conflict USING(provider,response_id) "
        "WHERE position.thread_id=? AND response.thread_id=? AND response.turn_id=? "
        "AND position.provider=? AND position.response_id=? AND conflict.response_id IS NULL",
        (item["thread"], item["thread"], item["turn"], item["provider"], item["response"])).fetchone()
    if explicit is not None:
        return explicit
    before = connection.execute("SELECT position.* FROM usage_reconcile_source_positions source JOIN usage_reconcile_last_positions position "
        "USING(thread_id,provider,response_id) WHERE source.source_id=? AND source.incarnation=? "
        "AND source.thread_id=? AND source.turn_id=? AND source.record_order<? ORDER BY position.clock DESC,position.turn_id DESC,position.ordinal DESC LIMIT 1",
        (item["source"], item["incarnation"], item["thread"], item["turn"], item["order"])).fetchone()
    # All candidates are qualified ordered positions. A missing origin clock
    # permits only its exact turn, never an inferred chronology.
    predicate = "source.turn_id=?" if clock is None else "source.clock>=?"
    parameters = [item["thread"], item["turn"] if clock is None else clock]
    if before is not None:
        predicate += " AND (position.clock,position.turn_id,position.ordinal)>(?,?,?)"
        parameters.extend(_point(before))
    parameters.extend((item["source"], item["incarnation"], item["order"]))
    return connection.execute("SELECT position.* FROM usage_reconcile_source_positions source JOIN usage_reconcile_last_positions position "
        "USING(thread_id,provider,response_id) WHERE source.thread_id=? AND " + predicate +
        " AND (source.source_id<>? OR source.incarnation<>? OR source.record_order>=?) ORDER BY position.clock,position.turn_id,position.ordinal LIMIT 1", parameters).fetchone()


def _reset(connection, thread, first, owners):
    candidates = []
    beginning = connection.execute("SELECT * FROM usage_reconcile_resets WHERE thread_id=? AND execution_id='' "
        "AND (clock,turn_id,ordinal)>(?,?,?) ORDER BY clock,turn_id,ordinal LIMIT 1", (thread, *first)).fetchone()
    if beginning is not None:
        candidates.append(_point(beginning))
    if owners:
        # A reset must cover every owner on the same indivisible response.
        row = connection.execute("SELECT reset.clock,reset.turn_id,reset.ordinal FROM json_each(?) owner "
            "CROSS JOIN usage_reconcile_resets reset ON reset.thread_id=? AND reset.execution_id=owner.value "
            "WHERE (reset.clock,reset.turn_id,reset.ordinal)>(?,?,?) "
            "GROUP BY reset.clock,reset.turn_id,reset.ordinal HAVING COUNT(*)=? ORDER BY reset.clock,reset.turn_id,reset.ordinal LIMIT 1",
            (encode(sorted(owners)).decode(), thread, *first, len(owners))).fetchone()
        if row is not None:
            candidates.append(list(row))
    return min(candidates) if candidates else None


def rebuild(connection, origin):
    item = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_pending_origins WHERE origin=?", (origin,)).fetchone())
    replace_range(connection, "pending_input", origin, None)
    if item is None:
        replace_rule(connection, "pending", origin, None, (), ())
        return
    clock_row = connection.execute("SELECT started_at FROM usage_turns WHERE thread_id=? AND turn_id=?", (item["thread"], item["turn"])).fetchone()
    clock = clock_row[0] if clock_row is not None else None
    position = _origin_position(connection, item, clock)
    value, last = None, None
    if position is not None:
        from .usage_reconciliation_turns import _document
        allocation = _document(connection.execute("SELECT * FROM usage_reconcile_turn_allocations WHERE thread_id=? AND turn_id=?",
                                                  (item["thread"], position["turn_id"])).fetchone())
        key = position["provider"], position["response_id"]
        owners = {member for selected, members in allocation["before"] if tuple(selected) == key for member in members}
        from .usage_reconciliation_owner_effects import base_owners
        owners.update(base_owners(connection, key, through_thread=item["thread"])[0])
        first = _point(position)
        last = _reset(connection, item["thread"], first, owners)
        value = {"thread": item["thread"], "first": first, "last": last, "owner_filter": None, "gaps": [], "conditional_gaps": []}
    replace_rule(connection, "pending", origin, value, (), ())
    # A newly eligible origin, changed base owner or new/reset boundary in this
    # relevant range must recompute the fence. Its exact origin turn is also
    # indexed separately, so clock changes cannot orphan this dependency.
    if clock is None:
        coverage = {"thread_id": item["thread"], "first": None, "last": None, "exact": item["turn"]}
    else:
        coverage = {"thread_id": item["thread"], "first": clock, "last": last[0] if last else MAX_CLOCK, "exact": None}
    replace_range(connection, "pending_input", origin, coverage)
