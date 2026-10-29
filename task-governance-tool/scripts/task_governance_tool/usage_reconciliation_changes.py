"""Translate captured dirty identities into durable phase-specific work.

This transaction only acknowledges a mark after all its old/new effects have
been queued. Phase queues are independent: finishing base allocation cannot
acknowledge participation, pending-call fences or source diagnostics.
"""

from .usage_evidence import encode
from .usage_reconciliation_sources import source_deltas, adopt_sources
from .usage_values import UsageError


TABLES = (
    "CREATE TABLE usage_reconcile_task_work (task_id TEXT PRIMARY KEY)",
    "CREATE TABLE usage_reconcile_participant_work (thread_id TEXT PRIMARY KEY)",
    "CREATE TABLE usage_reconcile_diagnostic_work (thread_id TEXT PRIMARY KEY)",
    """CREATE TABLE usage_reconcile_source_work (
        source_id TEXT PRIMARY KEY,before_document TEXT,after_document TEXT)""",
    """CREATE TABLE usage_reconcile_pending_work (
        source_id TEXT PRIMARY KEY,before_document TEXT,after_document TEXT)""",
    """CREATE TABLE usage_reconcile_source_epochs (
        source_id TEXT NOT NULL,incarnation INTEGER NOT NULL,thread_id TEXT NOT NULL,
        phase INTEGER NOT NULL CHECK(phase IN (0,1)),position TEXT NOT NULL,
        PRIMARY KEY(source_id,incarnation))""",
)


def _task(connection, identity):
    if identity is not None:
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_task_work VALUES (?)", (identity,))


def _turn(connection, thread, turn):
    if thread is not None and turn is not None:
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_input_turn_work VALUES (?,?)", (thread, turn))


def _origins(repository, connection, predicate, parameters):
    # Historical rows determine qualification, but only current-incarnation
    # references are affected consumers. Trigger OLD identities cover deletion.
    for row in connection.execute("SELECT json_extract(boundary.document,'$.thread_id') AS thread_id,"
        "json_extract(boundary.document,'$.turn_id') AS turn_id,json_extract(boundary.document,'$.project_id') AS project_id,"
        "json_extract(boundary.document,'$.task_id') AS task_id FROM usage_response_boundaries boundary "
        "JOIN usage_sources source ON source.source_id=boundary.source_id AND source.incarnation=boundary.incarnation WHERE " + predicate,
        parameters):
        if row["project_id"] == repository.basis[0]:
            _task(connection, row["task_id"])
            _turn(connection, row["thread_id"], row["turn_id"])


def _turn_dependencies(repository, connection, thread, turn):
    _turn(connection, thread, turn)
    _origins(repository, connection, "json_extract(boundary.document,'$.thread_id')=? AND json_extract(boundary.document,'$.turn_id')=?",
             (thread, turn))
    for row in connection.execute("SELECT task_id FROM usage_operation_turns WHERE project_id=? AND thread_id=? AND turn_id=? "
        "UNION SELECT task_id FROM usage_review_boundaries WHERE project_id=? AND thread_id=? AND turn_id=? "
        "UNION SELECT task_id FROM usage_wait_turns WHERE project_id=? AND thread_id=? AND turn_id=?",
        (repository.basis[0], thread, turn) * 3):
        _task(connection, row[0])
    # Endpoint changes select their interval consumers. A session-wide conflict
    # has its separate thread mark; an ordinary new turn does not create it.
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_interval_turn_work VALUES (?,?)", (thread, turn))


def _coalesce_source(connection, table, item):
    # Retain the oldest unconsumed before-side across any number of Hook events.
    # Intermediate windows are never published and need not replace that side.
    if table not in {"usage_reconcile_source_work", "usage_reconcile_pending_work"}:
        raise UsageError("usage_schema_invalid")
    before = encode(item.before).decode() if item.before is not None else None
    after = encode(item.after).decode() if item.after is not None else None
    connection.execute("INSERT INTO " + table + " VALUES (?,?,?) ON CONFLICT(source_id) DO UPDATE SET after_document=excluded.after_document",
                       (item.source_id, before, after))


def queue_sources(repository, connection, source_ids):
    """Shared baseline/delta semantic admission, without inventing generations."""
    deltas = source_deltas(connection, source_ids)
    for item in deltas:
        repository.check_budget()
        if not item.changed:
            continue
        before, after = item.before or {}, item.after or {}
        if item.replaced:
            _coalesce_source(connection, "usage_reconcile_source_work", item)
            for value in (item.before, item.after):
                if value is not None:
                    connection.execute("INSERT INTO usage_reconcile_source_epochs VALUES (?,?,?,0,'') "
                        "ON CONFLICT(source_id,incarnation) DO UPDATE SET phase=0,position=''",
                        (item.source_id, value["incarnation"], value["thread"]))
        if item.replaced or before.get("pending") != after.get("pending"):
            _coalesce_source(connection, "usage_reconcile_pending_work", item)
        if item.replaced or before.get("diagnostics") != after.get("diagnostics"):
            for thread in {before.get("thread"), after.get("thread")} - {None}:
                connection.execute("INSERT OR IGNORE INTO usage_reconcile_diagnostic_work VALUES (?)", (thread,))
    # The phase queues now retain the old/new sides, so semantic source state
    # and dirty acknowledgements can advance in the same transaction.
    adopt_sources(connection, deltas)


def consume_page(repository, connection):
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    revision = repository.reconciliation_revision(connection)
    changes = repository.pending_changes(connection, through=revision)
    queue_sources(repository, connection, {first for kind, first, _, _ in changes if kind in {"source", "context"}})
    for kind, first, second, _ in changes:
        repository.check_budget()
        if kind in {"source", "context", "boundary"}:
            continue  # exact OLD/NEW turn and origin marks accompany boundaries
        if kind == "task":
            if first == repository.basis[0]:
                _task(connection, second)
        elif kind == "turn":
            _turn_dependencies(repository, connection, first, second)
        elif kind == "thread":
            connection.execute("INSERT OR IGNORE INTO usage_reconcile_participant_work VALUES (?)", (first,))
        elif kind == "receipt":
            for row in connection.execute("SELECT task_id FROM usage_reconcile_core_receipts WHERE receipt_id=?", (first,)):
                _task(connection, row[0])
        elif kind == "clock":
            if not second.isdecimal() or str(int(second)) != second:
                raise UsageError("usage_schema_invalid")
            for row in connection.execute("SELECT turn_id FROM usage_turns WHERE thread_id=? AND started_at=? "
                "UNION SELECT turn_id FROM usage_reconcile_ordered_inputs WHERE thread_id=? AND started_at=?",
                (first, int(second), first, int(second))):
                _turn_dependencies(repository, connection, first, row[0])
        elif kind == "response":
            connection.execute("INSERT OR IGNORE INTO usage_reconcile_response_work VALUES (?,?)", (first, second))
            connection.execute("INSERT OR IGNORE INTO usage_reconcile_base_key_work VALUES (?,?)", (first, second))
            # An explicit boundary may have arrived before its numerical row.
            # The later original can disprove that boundary's turn identity.
            _origins(repository, connection, "json_extract(boundary.document,'$.provider')=? AND json_extract(boundary.document,'$.response_id')=?",
                     (first, second))
            for row in connection.execute("SELECT thread_id,turn_id FROM usage_responses WHERE provider=? AND response_id=?",
                                          (first, second)):
                _turn_dependencies(repository, connection, *row)
            for row in connection.execute("SELECT DISTINCT position.thread_id,position.turn_id FROM usage_response_positions position "
                "JOIN usage_sources source ON source.source_id=position.source_id AND source.incarnation=position.incarnation "
                "WHERE position.provider=? AND position.response_id=?", (first, second)):
                _turn_dependencies(repository, connection, *row)
        elif kind == "origin_event":
            _origins(repository, connection, "json_extract(boundary.document,'$.event_id')=?", (first,))
        elif kind == "origin_call":
            _origins(repository, connection, "json_extract(boundary.document,'$.thread_id')=? AND json_extract(boundary.document,'$.call_id')=?",
                     (first, second))
        else:
            raise UsageError("usage_schema_invalid")
    repository.acknowledge_changes(connection, changes, through=revision)
    return {"selected": len(changes), "generation": revision.generation}


def expand_source_page(repository, connection, *, limit=128):
    """Retain every intermediate incarnation, including partly processed ones."""
    work = connection.execute("SELECT * FROM usage_reconcile_source_epochs ORDER BY rowid LIMIT 1").fetchone()
    if work is None:
        return None
    source, incarnation, phase, position = work["source_id"], work["incarnation"], work["phase"], work["position"]
    if phase == 0:
        import json
        cursor = json.loads(position) if position else ["", ""]
        if type(cursor) is not list or len(cursor) != 2 or any(type(item) is not str for item in cursor):
            raise UsageError("usage_schema_invalid")
        rows = tuple(connection.execute("SELECT provider,response_id,thread_id,turn_id FROM usage_response_positions "
            "WHERE source_id=? AND incarnation=? AND (provider,response_id)>(?,?) ORDER BY provider,response_id LIMIT ?",
            (source, incarnation, *cursor, limit)))
        for row in rows:
            _turn_dependencies(repository, connection, row["thread_id"], row["turn_id"])
        next_position = encode([rows[-1]["provider"], rows[-1]["response_id"]]).decode() if rows else position
    else:
        rows = tuple(connection.execute("SELECT observation_id,json_extract(document,'$.thread_id') AS thread_id,"
            "json_extract(document,'$.turn_id') AS turn_id,json_extract(document,'$.project_id') AS project_id,"
            "json_extract(document,'$.task_id') AS task_id FROM usage_response_boundaries "
            "WHERE source_id=? AND incarnation=? AND observation_id>? ORDER BY observation_id LIMIT ?", (source, incarnation, position, limit)))
        for row in rows:
            _turn(connection, row["thread_id"], row["turn_id"])
            if row["project_id"] == repository.basis[0]:
                _task(connection, row["task_id"])
        next_position = rows[-1]["observation_id"] if rows else position
    repository.check_budget()
    if len(rows) == limit:
        connection.execute("UPDATE usage_reconcile_source_epochs SET position=? WHERE source_id=? AND incarnation=?",
                           (next_position, source, incarnation))
    elif phase == 0:
        connection.execute("UPDATE usage_reconcile_source_epochs SET phase=1,position='' WHERE source_id=? AND incarnation=?", (source, incarnation))
    else:
        connection.execute("DELETE FROM usage_reconcile_source_epochs WHERE source_id=? AND incarnation=?", (source, incarnation))
        if connection.execute("SELECT 1 FROM usage_reconcile_source_epochs WHERE source_id=? LIMIT 1", (source,)).fetchone() is None:
            connection.execute("DELETE FROM usage_reconcile_source_work WHERE source_id=?", (source,))
    return {"source_id": source, "incarnation": incarnation, "phase": phase, "selected": len(rows)}
