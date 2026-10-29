"""Apply selected fallback/reviewer/wait participation after uncertainty.

The current full classifier's phase order remains observable: fallback can
inspect earlier fallback owners, and parent-receipt trimming sees preceding
additional participation. A turn recomputes only its indexed interval users.
"""

from collections import defaultdict

from .usage_evidence import encode
from .usage_reconciliation_core import _decode
from .usage_reconciliation_events import _old_value
from .usage_reconciliation_membership import replace_contribution, replace_admissions
from .usage_reconciliation_ranges import consumers
from .usage_reconciliation_turns import _document
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_turn_contributions (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,kind TEXT NOT NULL,origin TEXT NOT NULL,
        PRIMARY KEY(thread_id,turn_id,kind,origin))""",
)


def interval_metadata_one(connection):
    """An admitted additional interval carries diagnostics even with no keys."""
    import json
    row = connection.execute("SELECT * FROM usage_reconcile_interval_work ORDER BY rowid LIMIT 1").fetchone()
    if row is None:
        return None
    kind, origin = row["kind"], row["origin"]
    current = json.loads(row["after_document"]) if row["after_document"] is not None else None
    if kind != "fallback":
        interval = current["interval"] if current is not None else None
        replace_contribution(connection, kind, origin, gaps=((interval["execution_id"], code) for code in current["coverage"]["diagnostics"]) if interval else ())
        replace_admissions(connection, kind, origin,
                           ((interval["thread_id"], interval["execution_id"]),) if interval and interval["source_admitted"] else ())
    connection.execute("DELETE FROM usage_reconcile_interval_work WHERE kind=? AND origin=?", (kind, origin))
    return kind, origin


def event_position(connection, identity):
    row = connection.execute("SELECT * FROM usage_reconcile_events WHERE transition_id=?", (identity,)).fetchone()
    if row is None:
        return None
    value = _old_value(row)
    boundary, change = value["boundary"], value["transition"]
    if boundary is not None and change["policy_version"] == 1 and boundary["thread_id"] == change["actor_session_id"]:
        position = connection.execute("SELECT * FROM usage_reconcile_positions WHERE thread_id=? AND provider=? AND response_id=? "
            "ORDER BY started_at DESC,turn_id DESC,ordinal DESC LIMIT 1",
            (boundary["thread_id"], boundary["provider"], boundary["response_id"])).fetchone()
    else:
        position = connection.execute("SELECT position.clock AS started_at,position.*,receipt.turn_id AS receipt_turn FROM usage_reconcile_receipt_keys receipt "
            "JOIN usage_reconcile_last_positions position USING(thread_id,provider,response_id) WHERE receipt.transition_id=?", (identity,)).fetchone()
    if position is None:
        return None
    return value, (position["started_at"], position["turn_id"], position["ordinal"]), boundary["turn_id"] if boundary is not None else position["receipt_turn"]


def apply_turn(connection, thread, turn):
    """Recompute one turn; upstream uncertainty changes requeue its consumers."""
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    allocation = connection.execute("SELECT * FROM usage_reconcile_turn_allocations WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
    inputs = connection.execute("SELECT digest FROM usage_reconcile_turn_inputs WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
    if inputs is not None and (allocation is None or allocation["input_digest"] != inputs["digest"]):
        raise UsageError("usage_pending")
    if inputs is not None:
        _document(allocation)
    owners = defaultdict(set)
    current = connection.execute("SELECT started_at FROM usage_turns WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
    clock = current[0] if current else None
    keys = {tuple(row) for row in connection.execute("SELECT response.provider,response.response_id FROM usage_responses response "
        "LEFT JOIN usage_conflicts conflict USING(provider,response_id) WHERE response.thread_id=? AND response.turn_id=? AND conflict.response_id IS NULL", (thread, turn))}
    unknown = {tuple(row) for row in connection.execute("SELECT DISTINCT u.provider,u.response_id FROM json_each(?) wanted "
        "CROSS JOIN usage_reconcile_unknown u ON u.provider=json_extract(wanted.value,'$[0]') AND u.response_id=json_extract(wanted.value,'$[1]') "
        "WHERE u.kind IN ('base','order','uncertainty')", (encode(sorted(keys)).decode(),))}
    local_keys = {tuple(row) for row in connection.execute("SELECT provider,response_id FROM usage_reconcile_positions WHERE thread_id=? AND turn_id=?", (thread, turn))}
    positions = {(row["provider"], row["response_id"]): (row["clock"], row["turn_id"], row["ordinal"])
        for row in connection.execute("SELECT position.* FROM json_each(?) wanted CROSS JOIN usage_reconcile_last_positions position "
            "ON position.thread_id=? AND position.provider=json_extract(wanted.value,'$[0]') "
            "AND position.response_id=json_extract(wanted.value,'$[1]')", (encode(sorted(keys | local_keys)).decode(), thread))}
    # Fallback and receipt trimming run after all base allocation, unlike the
    # per-session pending-reset phase. They must see global removals/additions.
    for row in connection.execute("SELECT member.provider,member.response_id,member.execution_id FROM json_each(?) wanted "
        "CROSS JOIN usage_reconcile_membership member ON member.provider=json_extract(wanted.value,'$[0]') "
        "AND member.response_id=json_extract(wanted.value,'$[1]') WHERE member.kind='base'", (encode(sorted(keys | positions.keys())).decode(),)):
        owners[row[0], row[1]].add(row[2])
    selected = consumers(connection, thread, turn, clock)
    intervals = [(row["kind"], row["origin"], _decode(row)) for row in connection.execute(
        "SELECT interval.* FROM json_each(?) wanted CROSS JOIN usage_reconcile_intervals interval "
        "WHERE interval.kind=json_extract(wanted.value,'$[0]') AND interval.origin=json_extract(wanted.value,'$[1]')",
        (encode(sorted(selected)).decode(),))] if selected else []
    before = {tuple(row) for row in connection.execute("SELECT kind,origin FROM usage_reconcile_turn_contributions WHERE thread_id=? AND turn_id=?", (thread, turn))}
    adopted = set()
    def save(kind, identity, chosen, execution, *, partial=False, gaps=()):
        origin = encode([identity, thread, turn]).decode()
        replace_contribution(connection, kind, origin, owners=((*key, execution) for key in chosen),
                             unknown=chosen if partial else (), gaps=((execution, code) for code in gaps) if chosen else ())
        adopted.add((kind, origin))
        for key in chosen:
            owners[key].add(execution)
    for kind, identity, value in sorted((item for item in intervals if item[0] == "fallback"), key=lambda item: item[2]["rank"]):
        interval = value["interval"]
        entry = event_position(connection, value["origins"][0])
        exit = event_position(connection, value["origins"][1]) if value["origins"][1] is not None else None
        if entry is not None and (entry[2] != interval["start_turn"] or entry[0]["transition"]["current_status"] != "in_progress"
                or entry[0]["transition"]["previous_status"] in {"in_progress", "review_pending"}):
            entry = None
        if exit is not None and (exit[2] != interval["end_turn"] or exit[0]["transition"]["current_status"] not in {"paused", "blocked", "ready", "cancelled", "done"}):
            exit = None
        execution = interval["execution_id"]
        chosen = {key for key in keys & unknown if key not in positions or execution in owners[key]
                  or (not (entry and owners[key]) and not (entry and positions[key] < entry[1])
                      and not (exit and positions[key] > exit[1]))}
        save(kind, identity, chosen, execution, gaps=value["coverage"]["diagnostics"])
    # Core events are already admitted and ordered in the complete-turn input.
    staged = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_turn_inputs WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone())
    sequence = [tuple(key) for key in staged["keys"]] if staged else []
    beginning = {change["execution_id"] for _, events in staged["events"] for _, change in events
                 if change["current_status"] == "in_progress" and change["previous_status"] not in {"in_progress", "review_pending"}} if staged else set()
    for kind, identity, value in sorted((item for item in intervals if item[0] != "fallback"), key=lambda item: item[2]["rank"]):
        interval = value["interval"]
        execution = interval["execution_id"]
        latest = connection.execute("SELECT current_status,thread_id FROM usage_reconcile_events WHERE execution_id=? ORDER BY source_order DESC LIMIT 1", (execution,)).fetchone()
        parent_receipt = (latest is not None and latest[0] == "done" and latest[1] == thread
                          and interval["start_turn"] == interval["end_turn"] and interval["start_turn"] is not None)
        chosen = set(keys)
        if parent_receipt:
            cutoff = next((ordinal for ordinal, key in enumerate(sequence) if owners[key] & beginning), len(sequence))
            chosen.intersection_update(sequence[:cutoff])
        save(kind, identity, chosen, execution, partial=interval["identity_only"])
    for kind, origin in before - adopted:
        replace_contribution(connection, kind, origin)
    connection.execute("DELETE FROM usage_reconcile_turn_contributions WHERE thread_id=? AND turn_id=?", (thread, turn))
    connection.executemany("INSERT INTO usage_reconcile_turn_contributions VALUES (?,?,?,?)", [(thread, turn, *value) for value in sorted(adopted)])
    connection.execute("DELETE FROM usage_reconcile_overlay_turn_work WHERE thread_id=? AND turn_id=?", (thread, turn))
    return {"thread_id": thread, "turn_id": turn, "intervals": len(intervals)}
