"""Admit changed complete turns and preserve old/new ordering effects.

This phase runs only after operation resolution for the captured basis. It
uses indexed exact-turn reads; a shared session is not a history selection.
The independent effect queues still have to finish before publication.
"""

from .usage_attribution import Transition
from .usage_attribution_repository import resolve_operations
from .usage_evidence import digest, encode
from .usage_reconciliation_core import _decode
from .usage_reconciliation_relations import read_selected_relations
from .usage_reconciliation_turns import stage_turn, remove_turn
from .usage_response_attribution import response_order
from .usage_response_binding import qualify_responses
from .usage_turn_adapter import OperationObservation, TurnObservation
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_ordered_inputs (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,started_at INTEGER,
        document TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id))""",
    "CREATE INDEX usage_reconcile_ordered_clock ON usage_reconcile_ordered_inputs(thread_id,started_at,turn_id)",
    "CREATE INDEX usage_reconcile_response_turn ON usage_responses(thread_id,turn_id)",
    """CREATE TABLE usage_reconcile_positions (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,started_at INTEGER NOT NULL,ordinal INTEGER NOT NULL,
        provider TEXT NOT NULL,response_id TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id,provider,response_id))""",
    "CREATE INDEX usage_reconcile_positions_order ON usage_reconcile_positions(thread_id,started_at,turn_id,ordinal)",
    "CREATE INDEX usage_reconcile_position_key ON usage_reconcile_positions(provider,response_id,thread_id,started_at,turn_id,ordinal)",
    """CREATE TABLE usage_reconcile_last_positions (
        thread_id TEXT NOT NULL,provider TEXT NOT NULL,response_id TEXT NOT NULL,turn_id TEXT NOT NULL,clock INTEGER NOT NULL,ordinal INTEGER NOT NULL,
        PRIMARY KEY(thread_id,provider,response_id))""",
    "CREATE INDEX usage_reconcile_last_position_turn ON usage_reconcile_last_positions(thread_id,turn_id)",
    """CREATE TABLE usage_reconcile_receipt_keys (
        transition_id TEXT PRIMARY KEY,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,provider TEXT NOT NULL,response_id TEXT NOT NULL)""",
    "CREATE INDEX usage_reconcile_receipt_keys_turn ON usage_reconcile_receipt_keys(thread_id,turn_id)",
    "CREATE INDEX usage_reconcile_receipt_keys_response ON usage_reconcile_receipt_keys(provider,response_id)",
)


def _enqueue(connection, thread, turn):
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_input_turn_work VALUES (?,?)", (thread, turn))


def _clock_peers(connection, thread, turn, before, after):
    """Both sides matter: a departed peer can make the old clock valid again."""
    if before == after:
        return
    for clock in {before, after} - {None}:
        rows = connection.execute("SELECT turn_id FROM usage_turns WHERE thread_id=? AND started_at=? "
            "UNION SELECT turn_id FROM usage_reconcile_ordered_inputs WHERE thread_id=? AND started_at=?",
            (thread, clock, thread, clock))
        for row in rows:
            if row[0] != turn:
                _enqueue(connection, thread, row[0])


def _last_positions(connection, thread, keys):
    parameter = encode(sorted(keys)).decode()
    before = {row["turn_id"] for row in connection.execute("SELECT position.turn_id FROM json_each(?) key "
        "CROSS JOIN usage_reconcile_last_positions position ON position.thread_id=? "
        "AND position.provider=json_extract(key.value,'$[0]') AND position.response_id=json_extract(key.value,'$[1]')", (parameter, thread))}
    connection.execute("DELETE FROM usage_reconcile_last_positions WHERE thread_id=? AND (provider,response_id) IN "
        "(SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]') FROM json_each(?))", (thread, parameter))
    connection.execute("INSERT INTO usage_reconcile_last_positions SELECT position.thread_id,position.provider,position.response_id,"
        "position.turn_id,position.started_at,position.ordinal FROM json_each(?) key CROSS JOIN usage_reconcile_positions position "
        "ON position.provider=json_extract(key.value,'$[0]') AND position.response_id=json_extract(key.value,'$[1]') AND position.thread_id=? "
        "WHERE NOT EXISTS (SELECT 1 FROM usage_reconcile_positions later WHERE later.provider=position.provider AND later.response_id=position.response_id "
        "AND later.thread_id=position.thread_id AND (later.started_at,later.turn_id,later.ordinal)>(position.started_at,position.turn_id,position.ordinal))",
        (parameter, thread))
    after = {row[0] for row in connection.execute("SELECT position.turn_id FROM json_each(?) key CROSS JOIN usage_reconcile_last_positions position "
        "ON position.thread_id=? AND position.provider=json_extract(key.value,'$[0]') AND position.response_id=json_extract(key.value,'$[1]')", (parameter, thread))}
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_overlay_turn_work VALUES (?,?)", [(thread, turn) for turn in before | after])
    # Fallback is stored at the numerical original's turn, which can be
    # neither the old nor the new last occurrence. Its cutoff uses this
    # thread's last position even when the uncertainty set itself is equal.
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_overlay_turn_work "
        "SELECT response.thread_id,response.turn_id FROM json_each(?) key CROSS JOIN usage_responses response "
        "ON response.provider=json_extract(key.value,'$[0]') AND response.response_id=json_extract(key.value,'$[1]') "
        "WHERE response.thread_id=?", (parameter, thread))
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_base_key_work VALUES (?,?)", sorted(keys))


def _receipt_keys(connection, thread, turn, sequence):
    old = {row["transition_id"]: (row["provider"], row["response_id"]) for row in connection.execute(
        "SELECT * FROM usage_reconcile_receipt_keys WHERE thread_id=? AND turn_id=?", (thread, turn))}
    new = {row[0]: tuple(sequence[0]) for row in connection.execute(
        "SELECT transition_id FROM usage_reconcile_receipt_events WHERE thread_id=? AND turn_id=?", (thread, turn))} if sequence else {}
    for identity in old.keys() | new.keys():
        if old.get(identity) == new.get(identity):
            continue
        connection.execute("DELETE FROM usage_reconcile_receipt_keys WHERE transition_id=?", (identity,))
        if identity in new:
            connection.execute("INSERT INTO usage_reconcile_receipt_keys VALUES (?,?,?,?,?)", (identity, thread, turn, *new[identity]))
        from .usage_reconciliation_events import _response_work
        for key in {old.get(identity), new.get(identity)} - {None}:
            _response_work(connection, {"provider": key[0], "response_id": key[1]})


def stage_input_turn(repository, core, connection, thread, turn):
    """Requalify exact original relations, including historical-origin closure."""
    if not core.in_transaction or not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    repository.check_budget()
    occurrence_keys = tuple(map(tuple, connection.execute("SELECT DISTINCT position.provider,position.response_id FROM usage_response_positions position "
        "JOIN usage_sources source ON source.source_id=position.source_id AND source.incarnation=position.incarnation "
        "WHERE position.thread_id=? AND position.turn_id=?", (thread, turn))))
    relations = read_selected_relations(connection, turns={(thread, turn)}, response_keys=occurrence_keys)
    operations = tuple(OperationObservation(item.thread_id, item.turn_id, item.project_id, item.task_id,
                                            item.event_id, item.generation, item.status)
                       for _, _, item in relations.current if item.event_id is not None)
    resolved = resolve_operations(core, operations, check_budget=repository.check_budget)
    keys = sorted({(item.provider, item.response_id) for _, _, item in relations.current if item.response_id is not None})
    numeric = {(row["provider"], row["response_id"]): (row["thread_id"], row["turn_id"]) for row in connection.execute(
        "SELECT response.provider,response.response_id,response.thread_id,response.turn_id FROM usage_responses response "
        "JOIN json_each(?) key ON response.provider=json_extract(key.value,'$[0]') AND response.response_id=json_extract(key.value,'$[1]')",
        (encode(keys).decode(),))}
    qualified = qualify_responses(repository.basis[0], relations.positions, relations.current, relations.history, numeric, resolved)
    clock_row = connection.execute("SELECT started_at FROM usage_turns WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
    clock = clock_row[0] if clock_row is not None else None
    conflict = connection.execute("SELECT 1 FROM usage_turn_conflicts WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone() is not None
    if clock is not None:
        conflict |= connection.execute("SELECT 1 FROM usage_turns WHERE thread_id=? AND started_at=? AND turn_id<>? LIMIT 1",
                                       (thread, clock, turn)).fetchone() is not None
    turns = (TurnObservation(thread, turn, clock),) if clock is not None else ()
    local_positions = tuple(entry for entry in qualified.positions if (entry[2].thread_id, entry[2].turn_id) == (thread, turn))
    _, ordered, unknown = response_order(local_positions, turns, {(thread, turn)} if conflict else ())
    sequence = ordered.get((thread, turn))
    from .usage_reconciliation_owner_effects import replace_encounter
    replace_encounter(connection, thread, turn, qualified, relations, sequence is not None)
    actual_keys = {tuple(row) for row in connection.execute("SELECT provider,response_id FROM usage_responses "
                                                         "WHERE thread_id=? AND turn_id=?", (thread, turn))}
    # Missing-order uncertainty is reduced per original key across all
    # threads after input staging; an unorderable occurrence remains local.
    events = {}
    local_keys = sorted({(item.provider, item.response_id) for _, _, item in local_positions})
    for row in connection.execute("SELECT event.* FROM json_each(?) key CROSS JOIN usage_reconcile_events event "
        "ON json_extract(event.document,'$.boundary.provider')=json_extract(key.value,'$[0]') "
        "AND json_extract(event.document,'$.boundary.response_id')=json_extract(key.value,'$[1]') ORDER BY event.source_order",
        (encode(local_keys).decode(),)):
        from .usage_reconciliation_events import _old_value
        value = _old_value(row)
        change = Transition(**value["transition"])
        boundary = value["boundary"]
        key = boundary["provider"], boundary["response_id"]
        if change.actor_session_id != boundary["thread_id"] or change.policy_version != 1:
            unknown.add(key)
            continue
        # Qualification may have changed after a historical origin changed.
        # A stale resolved-event phase must never stage an obsolete anchor.
        selected = qualified.bindings.get(change.transition_id)
        if selected is None or (selected.thread_id, selected.turn_id, selected.provider, selected.response_id) != (
                boundary["thread_id"], boundary["turn_id"], boundary["provider"], boundary["response_id"]):
            raise UsageError("usage_pending")
        events.setdefault(key, []).append((value["order"], change))
    if sequence:
        from .usage_reconciliation_events import _old_value
        for row in connection.execute("SELECT event.* FROM usage_reconcile_receipt_events receipt "
            "JOIN usage_reconcile_events event USING(transition_id) WHERE receipt.thread_id=? AND receipt.turn_id=? ORDER BY event.source_order", (thread, turn)):
            value = _old_value(row)
            pair = value["order"], Transition(**value["transition"])
            if pair not in events.setdefault(sequence[0], []):
                events[sequence[0]].append(pair)
        for row in connection.execute("SELECT event.*,receipt.provider AS receipt_provider,receipt.response_id AS receipt_response "
            "FROM json_each(?) key CROSS JOIN usage_reconcile_receipt_keys receipt ON receipt.provider=json_extract(key.value,'$[0]') "
            "AND receipt.response_id=json_extract(key.value,'$[1]') JOIN usage_reconcile_events event USING(transition_id) "
            "WHERE NOT (receipt.thread_id=? AND receipt.turn_id=?) ORDER BY event.source_order", (encode(local_keys).decode(), thread, turn)):
            value = _old_value(row)
            pair = value["order"], Transition(**value["transition"])
            key = row["receipt_provider"], row["receipt_response"]
            if pair not in events.setdefault(key, []):
                events[key].append(pair)
    old = connection.execute("SELECT * FROM usage_reconcile_ordered_inputs WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
    previous = _decode(old)
    if previous is not None and (previous["thread_id"], previous["turn_id"], previous["started_at"]) != (
            old["thread_id"], old["turn_id"], old["started_at"]):
        raise UsageError("usage_schema_invalid")
    value = {"thread_id": thread, "turn_id": turn, "started_at": clock,
             "ordered": None if sequence is None else list(map(list, sequence)),
             "unknown": sorted(map(list, unknown)), "keys": sorted(map(list, actual_keys))}
    _clock_peers(connection, thread, turn, old["started_at"] if old is not None else None, clock)
    from .usage_reconciliation_pending import invalidate_turn, stage_positions
    invalidate_turn(connection, thread, turn, old["started_at"] if old is not None else None, clock)
    stage_positions(connection, thread, turn, clock, sequence, local_positions, events)
    if previous is None or previous["ordered"] != value["ordered"] or previous["started_at"] != clock:
        affected_keys = set(map(tuple, previous["ordered"] or ())) if previous else set()
        affected_keys.update(sequence or ())
        had_order = connection.execute("SELECT 1 FROM usage_reconcile_positions WHERE thread_id=? LIMIT 1", (thread,)).fetchone() is not None
        connection.execute("DELETE FROM usage_reconcile_positions WHERE thread_id=? AND turn_id=?", (thread, turn))
        connection.executemany("INSERT INTO usage_reconcile_positions VALUES (?,?,?,?,?,?)",
                               [(thread, turn, clock, ordinal, *key) for ordinal, key in enumerate(sequence or ())])
        _last_positions(connection, thread, affected_keys)
        has_order = connection.execute("SELECT 1 FROM usage_reconcile_positions WHERE thread_id=? LIMIT 1", (thread,)).fetchone() is not None
        if had_order != has_order:
            connection.execute("INSERT OR IGNORE INTO usage_reconcile_rule_work SELECT 'missing',transition_id "
                "FROM usage_reconcile_events WHERE thread_id=?", (thread,))
        connection.execute("INSERT INTO usage_reconcile_event_work SELECT transition_id,task_id,thread_id,source_order FROM usage_reconcile_events "
            "WHERE json_extract(document,'$.boundary.thread_id')=? AND json_extract(document,'$.boundary.turn_id')=? "
            "ON CONFLICT(transition_id) DO NOTHING", (thread, turn))
        connection.execute("INSERT INTO usage_reconcile_event_work SELECT event.transition_id,event.task_id,event.thread_id,event.source_order "
            "FROM usage_reconcile_receipt_events receipt JOIN usage_reconcile_events event USING(transition_id) "
            "WHERE receipt.thread_id=? AND receipt.turn_id=? ON CONFLICT(transition_id) DO NOTHING", (thread, turn))
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_event_work SELECT event.transition_id,event.task_id,event.thread_id,event.source_order "
            "FROM json_each(?) key CROSS JOIN usage_reconcile_events event "
            "ON json_extract(event.document,'$.boundary.provider')=json_extract(key.value,'$[0]') "
            "AND json_extract(event.document,'$.boundary.response_id')=json_extract(key.value,'$[1]')", (encode(sorted(affected_keys)).decode(),))
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_event_work SELECT event.transition_id,event.task_id,event.thread_id,event.source_order "
            "FROM json_each(?) key CROSS JOIN usage_reconcile_receipt_keys receipt ON receipt.provider=json_extract(key.value,'$[0]') "
            "AND receipt.response_id=json_extract(key.value,'$[1]') JOIN usage_reconcile_events event USING(transition_id)", (encode(sorted(affected_keys)).decode(),))
    _receipt_keys(connection, thread, turn, sequence)
    connection.execute("INSERT INTO usage_reconcile_ordered_inputs VALUES (?,?,?,?,?) ON CONFLICT(thread_id,turn_id) DO UPDATE SET "
        "started_at=excluded.started_at,document=excluded.document,digest=excluded.digest",
        (thread, turn, clock, encode(value).decode(), digest(value)))
    if sequence is None:
        changed = remove_turn(connection, thread, turn)
    else:
        changed = stage_turn(connection, thread_id=thread, turn_id=turn, started_at=clock, keys=sequence,
                             events=events, checks={(item.provider, item.response_id) for item in qualified.checks if item.response_id is not None})
    if previous != value:
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_turn_effects VALUES (?,?)", (thread, turn))
        connection.executemany("INSERT OR IGNORE INTO usage_reconcile_base_key_work VALUES (?,?)",
            sorted(actual_keys | (set(map(tuple, previous["keys"])) if previous else set())))
    repository.check_budget()
    connection.execute("DELETE FROM usage_reconcile_input_turn_work WHERE thread_id=? AND turn_id=?", (thread, turn))
    return {"thread_id": thread, "turn_id": turn, "changed": changed or previous != value,
            "ordered": sequence is not None, "responses": len(actual_keys)}


def stage_one(repository, core, connection):
    row = connection.execute("SELECT thread_id,turn_id FROM usage_reconcile_input_turn_work ORDER BY rowid LIMIT 1").fetchone()
    return stage_input_turn(repository, core, connection, *row) if row is not None else None
