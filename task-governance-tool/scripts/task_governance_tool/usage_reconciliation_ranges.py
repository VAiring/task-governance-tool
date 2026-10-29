"""Exact integer-clock interval selection without scanning session history.

A closed range has at most 126 dyadic bins over the nonnegative 63-bit clock
domain. A point probes its 64 ancestors. No float clock, guessed timestamp or
elapsed-time ownership inference is introduced: only admitted endpoints create
these disposable reverse dependencies.
"""

from .usage_evidence import encode
from .usage_values import UsageError


MAX_CLOCK = 2**63 - 1
TABLES = (
    """CREATE TABLE usage_reconcile_range_bins (
        thread_id TEXT NOT NULL,level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 63),prefix INTEGER NOT NULL,
        kind TEXT NOT NULL,origin TEXT NOT NULL,PRIMARY KEY(thread_id,level,prefix,kind,origin))""",
    "CREATE INDEX usage_reconcile_range_origins ON usage_reconcile_range_bins(kind,origin)",
    """CREATE TABLE usage_reconcile_exact_turns (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,kind TEXT NOT NULL,origin TEXT NOT NULL,
        PRIMARY KEY(thread_id,turn_id,kind,origin))""",
    "CREATE INDEX usage_reconcile_exact_origins ON usage_reconcile_exact_turns(kind,origin)",
    """CREATE TABLE usage_reconcile_range_work (
        identity TEXT PRIMARY KEY,document TEXT NOT NULL,digest TEXT NOT NULL,position TEXT NOT NULL)""",
)


def range_bins(first, last):
    if type(first) is not int or type(last) is not int or not 0 <= first <= last <= MAX_CLOCK:
        raise UsageError("usage_schema_invalid")
    while first <= last:
        aligned = (first & -first).bit_length() - 1 if first else 63
        level = min(aligned, (last - first + 1).bit_length() - 1)
        yield level, first >> level
        first += 1 << level


def coverage(connection, interval):
    def clock(turn):
        if turn is None:
            return None
        row = connection.execute("SELECT started_at FROM usage_turns WHERE thread_id=? AND turn_id=?", (interval.thread_id, turn)).fetchone()
        return row[0] if row is not None else None
    first, last, exact, codes = None, None, None, set(interval.diagnostics)
    if interval.identity_only:
        exact = interval.start_turn
        codes.add("boundary_unknown")
    else:
        start = clock(interval.start_turn)
        end = clock(interval.end_turn) if interval.closed else None
        if start is None:
            exact = interval.end_turn if interval.closed and end is not None else None
            codes.add("boundary_unknown")
        elif interval.closed and (end is None or end < start):
            exact = interval.start_turn
            codes.add("boundary_unknown")
        else:
            first, last = start, end if interval.closed else MAX_CLOCK
            if not interval.closed:
                codes.add("interval_open")
    return {"thread_id": interval.thread_id, "first": first, "last": last, "exact": exact, "diagnostics": sorted(codes)}


def replace_range(connection, kind, origin, value):
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    connection.execute("DELETE FROM usage_reconcile_range_bins WHERE kind=? AND origin=?", (kind, origin))
    connection.execute("DELETE FROM usage_reconcile_exact_turns WHERE kind=? AND origin=?", (kind, origin))
    if value is None:
        return
    if value["exact"] is not None:
        connection.execute("INSERT INTO usage_reconcile_exact_turns VALUES (?,?,?,?)", (value["thread_id"], value["exact"], kind, origin))
    elif value["first"] is not None:
        connection.executemany("INSERT INTO usage_reconcile_range_bins VALUES (?,?,?,?,?)",
            [(value["thread_id"], level, prefix, kind, origin) for level, prefix in range_bins(value["first"], value["last"])])


def consumers(connection, thread, turn, clock):
    found = {tuple(row) for row in connection.execute("SELECT kind,origin FROM usage_reconcile_exact_turns WHERE thread_id=? AND turn_id=?", (thread, turn))}
    if clock is not None:
        if type(clock) is not int or not 0 <= clock <= MAX_CLOCK:
            raise UsageError("usage_schema_invalid")
        ancestors = [[level, clock >> level] for level in range(64)]
        # Keep the finite ancestor table outermost. Reordering this join can
        # scan every bin in the session despite the complete equality key.
        found.update(tuple(row) for row in connection.execute("SELECT bins.kind,bins.origin FROM json_each(?) ancestor "
            "CROSS JOIN usage_reconcile_range_bins bins ON bins.thread_id=? AND bins.level=json_extract(ancestor.value,'$[0]') "
            "AND bins.prefix=json_extract(ancestor.value,'$[1]')", (encode(ancestors).decode(), thread)))
    return found


def enqueue_range(connection, value):
    from .usage_evidence import digest
    if value is not None:
        document = encode(value).decode()
        identity = digest(value)
        # Requeue a partly visited range from its beginning: an intervening
        # edit can invalidate turns already visited in this same range.
        connection.execute("INSERT INTO usage_reconcile_range_work VALUES (?,?,?,'') ON CONFLICT(identity) DO UPDATE SET position=''",
                           (identity, document, identity))


def expand_range_page(connection, *, limit=128):
    import json
    from .usage_reconciliation_core import _decode
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    work = connection.execute("SELECT * FROM usage_reconcile_range_work ORDER BY rowid LIMIT 1").fetchone()
    if work is None:
        return None
    value = _decode(work)
    if value["exact"] is not None:
        turns = [(value["thread_id"], value["exact"])]
        position, done = "", True
    elif value["first"] is None:
        turns, position, done = [], "", True
    else:
        cursor = json.loads(work["position"]) if work["position"] else [value["first"], ""]
        if type(cursor) is not list or len(cursor) != 2 or type(cursor[0]) is not int or type(cursor[1]) is not str:
            raise UsageError("usage_schema_invalid")
        rows = tuple(connection.execute("SELECT thread_id,turn_id,started_at FROM usage_turns WHERE thread_id=? AND started_at<=? "
            "AND (started_at,turn_id)>(?,?) ORDER BY started_at,turn_id LIMIT ?",
            (value["thread_id"], value["last"], *cursor, limit)))
        turns = [(row["thread_id"], row["turn_id"]) for row in rows]
        position = encode([rows[-1]["started_at"], rows[-1]["turn_id"]]).decode() if rows else work["position"]
        done = len(rows) < limit
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_overlay_turn_work VALUES (?,?)", turns)
    if done:
        connection.execute("DELETE FROM usage_reconcile_range_work WHERE identity=?", (work["identity"],))
    else:
        connection.execute("UPDATE usage_reconcile_range_work SET position=? WHERE identity=?", (position, work["identity"]))
    return {"selected": len(turns), "complete": done}
