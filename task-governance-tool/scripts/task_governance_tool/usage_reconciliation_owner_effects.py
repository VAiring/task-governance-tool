"""Ordered base-owner effects for only the affected original response keys.

Preparation removes an owner globally, including an earlier occurrence of the
same original key. Later additions and pause ambiguity checks retain their
order. Overlay membership is separate and must not be removed here.
"""

from .usage_evidence import digest, encode
from .usage_reconciliation_core import _decode
from .usage_response_ownership import apply_actions
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_turn_encounters (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,phase INTEGER NOT NULL,ordinal INTEGER NOT NULL,
        PRIMARY KEY(thread_id,turn_id))""",
    "CREATE INDEX usage_reconcile_encounter_order ON usage_reconcile_turn_encounters(thread_id,phase,ordinal)",
    """CREATE TABLE usage_reconcile_base_actions (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,started_at INTEGER NOT NULL,
        provider TEXT NOT NULL,response_id TEXT NOT NULL,document TEXT NOT NULL,digest TEXT NOT NULL,
        PRIMARY KEY(thread_id,turn_id,provider,response_id))""",
    "CREATE INDEX usage_reconcile_base_action_key ON usage_reconcile_base_actions(provider,response_id,thread_id,started_at,turn_id)",
    "CREATE INDEX usage_reconcile_base_action_thread ON usage_reconcile_base_actions(thread_id,provider,response_id)",
    "CREATE TABLE usage_reconcile_base_key_work (provider TEXT NOT NULL,response_id TEXT NOT NULL,PRIMARY KEY(provider,response_id))",
)


def _thread_order(connection, thread):
    row = connection.execute("SELECT phase,ordinal FROM usage_reconcile_turn_encounters WHERE thread_id=? "
                             "ORDER BY phase,ordinal LIMIT 1", (thread,)).fetchone()
    return tuple(row) if row is not None else None


def replace_encounter(connection, thread, turn, qualified, relations, ordered):
    """Keep the old classifier's thread encounter order without full replay.

    Raw positions precede qualified anchors. Their original insertion rowids,
    not source hashes or the order of dirty work, decide thread encounter order.
    Only orderable turns participate, as in response_order.
    """
    before = _thread_order(connection, thread)
    anchors = {(source, incarnation, item.provider, item.response_id) for source, incarnation, item in qualified.anchors}
    ranks = [(0, relations.position_order[entry]) for entry in qualified.positions
             if (entry[2].thread_id, entry[2].turn_id) == (thread, turn)
             and (entry[0], entry[1], entry[2].provider, entry[2].response_id) not in anchors]
    ranks += [(1, relations.boundary_order[entry]) for entry in qualified.anchors
              if (entry[2].thread_id, entry[2].turn_id) == (thread, turn)]
    connection.execute("DELETE FROM usage_reconcile_turn_encounters WHERE thread_id=? AND turn_id=?", (thread, turn))
    if ordered and ranks:
        connection.execute("INSERT INTO usage_reconcile_turn_encounters VALUES (?,?,?,?)", (thread, turn, *min(ranks)))
    if before != _thread_order(connection, thread):
        # Only shared original keys can depend on relative thread order.
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_base_key_work SELECT own.provider,own.response_id "
            "FROM usage_reconcile_positions own WHERE own.thread_id=? AND EXISTS "
            "(SELECT 1 FROM usage_reconcile_base_actions peer WHERE peer.provider=own.provider "
            "AND peer.response_id=own.response_id AND peer.thread_id<>own.thread_id)", (thread,))


def replace_actions(connection, thread, turn, started_at, actions):
    old = {tuple(row) for row in connection.execute("SELECT provider,response_id FROM usage_reconcile_base_actions "
                                                   "WHERE thread_id=? AND turn_id=?", (thread, turn))}
    connection.execute("DELETE FROM usage_reconcile_base_actions WHERE thread_id=? AND turn_id=?", (thread, turn))
    connection.executemany("INSERT INTO usage_reconcile_base_actions VALUES (?,?,?,?,?,?,?)",
        [(thread, turn, started_at, *key, encode(value).decode(), digest(value)) for key, value in actions])
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_base_key_work VALUES (?,?)",
                           sorted(old | {tuple(key) for key, _ in actions}))


def base_owners(connection, key, *, through_thread=None):
    """Read the final base result or its exact end-of-thread encounter prefix."""
    rows = tuple(connection.execute("SELECT * FROM usage_reconcile_base_actions WHERE provider=? AND response_id=?", key))
    threads = {row["thread_id"] for row in rows}
    if through_thread is not None:
        threads.add(through_thread)
    order = {thread: _thread_order(connection, thread) for thread in threads} if len(threads) > 1 else {thread: () for thread in threads}
    if any(value is None for value in order.values()):
        raise UsageError("usage_pending")
    owners, unknown = set(), False
    for item in sorted(rows, key=lambda value: (order[value["thread_id"]], value["started_at"], value["turn_id"])):
        if through_thread is not None and order[item["thread_id"]] > order[through_thread]:
            break
        unknown |= apply_actions(owners, _decode(item))
    return owners, unknown


def reconcile_one(connection):
    from .usage_reconciliation_membership import replace_contribution
    from .usage_reconciliation_pending import invalidate_turn
    row = connection.execute("SELECT provider,response_id FROM usage_reconcile_base_key_work ORDER BY rowid LIMIT 1").fetchone()
    if row is None:
        return None
    key = tuple(row)
    owners, unknown = base_owners(connection, key)
    changed = replace_contribution(connection, "base", encode(["response", *key]).decode(),
        owners=((*key, owner) for owner in owners), unknown=(key,) if unknown else ())
    # Full replay distinguishes an explicitly unorderable occurrence from a
    # good numerical original absent from every ordered occurrence. Keep the
    # latter as its own contribution so another thread can resolve it without
    # erasing independent bad-order diagnostics.
    missing = connection.execute("SELECT 1 FROM usage_responses response WHERE provider=? AND response_id=? "
        "AND NOT EXISTS (SELECT 1 FROM usage_conflicts conflict WHERE conflict.provider=response.provider AND conflict.response_id=response.response_id) "
        "AND NOT EXISTS (SELECT 1 FROM usage_reconcile_positions position WHERE position.provider=response.provider AND position.response_id=response.response_id)",
        key).fetchone() is not None
    replace_contribution(connection, "order", encode(["response", *key]).decode(), unknown=(key,) if missing else ())
    for item in connection.execute("SELECT thread_id,turn_id,started_at FROM usage_reconcile_positions "
                                    "WHERE provider=? AND response_id=?", key):
        if changed:
            connection.execute("INSERT OR IGNORE INTO usage_reconcile_overlay_turn_work VALUES (?,?)", tuple(item)[:2])
        # An encounter prefix can change even when a later session restores
        # the same final owner set. Pending fences depend on that prefix.
        invalidate_turn(connection, *item)
    connection.execute("DELETE FROM usage_reconcile_base_key_work WHERE provider=? AND response_id=?", key)
    return {"provider": key[0], "response_id": key[1]}
