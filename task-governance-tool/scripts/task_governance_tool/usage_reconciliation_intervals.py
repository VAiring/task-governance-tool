"""Selected owner/reviewer/wait ranges with old/new interval retention.

Only explicit endpoint identities and their equal-clock peers are needed to
establish a range. Reading the range's original responses is a later phase;
unrelated turns from the same session are not an admission prerequisite.
"""

from dataclasses import asdict

from .usage_attribution import Transition, owner_segments
from .usage_evidence import digest, encode
from .usage_reconciliation_core import _decode
from .usage_review_attribution import ReviewBoundary, ReviewReceiptTurn, reviewer_intervals
from .usage_wait_attribution import WaitObservation, wait_intervals
from .usage_wait_repository import wait_anchor
from .usage_turn_adapter import TurnObservation
from .usage_values import UsageError
from .usage_reconciliation_ranges import coverage, replace_range, enqueue_range


TABLES = (
    """CREATE TABLE usage_reconcile_intervals (
        kind TEXT NOT NULL,origin TEXT NOT NULL,task_id TEXT NOT NULL,execution_id TEXT NOT NULL,thread_id TEXT NOT NULL,
        document TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(kind,origin))""",
    "CREATE INDEX usage_reconcile_intervals_task ON usage_reconcile_intervals(task_id,kind,origin)",
    "CREATE INDEX usage_reconcile_intervals_thread ON usage_reconcile_intervals(thread_id,kind,origin)",
    """CREATE TABLE usage_reconcile_interval_work (
        kind TEXT NOT NULL,origin TEXT NOT NULL,before_document TEXT,after_document TEXT,PRIMARY KEY(kind,origin))""",
    """CREATE TABLE usage_reconcile_interval_inputs (
        task_id TEXT NOT NULL,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,kind TEXT NOT NULL,
        PRIMARY KEY(task_id,thread_id,turn_id,kind))""",
    "CREATE INDEX usage_reconcile_interval_input_thread ON usage_reconcile_interval_inputs(thread_id,turn_id,kind,task_id)",
    """CREATE TABLE usage_reconcile_interval_turn_work (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id))""",
    "CREATE TABLE usage_reconcile_relation_task_work (task_id TEXT PRIMARY KEY)",
    "CREATE INDEX usage_reconcile_wait_task ON usage_wait_turns(project_id,task_id)",
    "CREATE INDEX usage_reconcile_review_task ON usage_review_boundaries(project_id,task_id)",
    "CREATE INDEX usage_reconcile_receipt_turn ON usage_review_receipt_turns(receipt_id)",
    """CREATE TABLE usage_reconcile_receipt_events (
        transition_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL)""",
    "CREATE INDEX usage_reconcile_receipt_event_turn ON usage_reconcile_receipt_events(thread_id,turn_id)",
    "CREATE INDEX usage_reconcile_receipt_event_task ON usage_reconcile_receipt_events(task_id,transition_id)",
)


def endpoint_turns(connection, keys, reviewer_sessions=()):
    """Complete endpoint-order proof, including nonlocal session conflicts."""
    keys, reviewer_sessions = set(map(tuple, keys)), set(reviewer_sessions)
    rows = {}
    if keys:
        for row in connection.execute("SELECT turn.* FROM usage_turns turn JOIN json_each(?) wanted "
            "ON turn.thread_id=json_extract(wanted.value,'$[0]') AND turn.turn_id=json_extract(wanted.value,'$[1]')",
            (encode(sorted(keys)).decode(),)):
            rows[row["thread_id"], row["turn_id"]] = row
    clocks = sorted({(row["thread_id"], row["started_at"]) for row in rows.values()})
    if clocks:
        for row in connection.execute("SELECT turn.* FROM usage_turns turn JOIN json_each(?) wanted "
            "ON turn.thread_id=json_extract(wanted.value,'$[0]') AND turn.started_at=json_extract(wanted.value,'$[1]')",
            (encode(clocks).decode(),)):
            rows[row["thread_id"], row["turn_id"]] = row
    # A single retained conflict proves that range inference is unavailable in
    # this reviewer session. Other conflict rows cannot change that boolean.
    # Endpoint/peer conflicts themselves are still all included below.
    if reviewer_sessions:
        for row in connection.execute("SELECT turn.* FROM json_each(?) wanted JOIN usage_turns turn ON turn.thread_id=wanted.value "
            "AND turn.turn_id=(SELECT MIN(conflict.turn_id) FROM usage_turn_conflicts conflict WHERE conflict.thread_id=wanted.value)",
            (encode(sorted(reviewer_sessions)).decode(),)):
            rows[row["thread_id"], row["turn_id"]] = row
    conflicts = frozenset(tuple(row) for row in connection.execute("SELECT conflict.thread_id,conflict.turn_id FROM usage_turn_conflicts conflict "
        "JOIN json_each(?) wanted ON conflict.thread_id=json_extract(wanted.value,'$[0]') AND conflict.turn_id=json_extract(wanted.value,'$[1]')",
        (encode(sorted(rows)).decode(),))) if rows else frozenset()
    return tuple(TurnObservation(**dict(row)) for _, row in sorted(rows.items())), conflicts


def _profile_reviews(profile):
    from .review_session_repository import ReviewSessionTarget, ReviewSessionBinding
    from .review_answer_repository import CollectedReviewEndpoint
    reviews = tuple((identity, ReviewSessionTarget(**target), ReviewSessionBinding(**binding))
                    for identity, target, binding in profile["reviews"])
    endpoints = tuple(CollectedReviewEndpoint(item["receipt_id"], item["turn_id"],
        ReviewSessionTarget(**item["target"]), ReviewSessionBinding(**item["binding"])) for item in profile["endpoints"])
    return reviews, endpoints


def reconcile_intervals(repository, core, connection, task_id, profile):
    if not core.in_transaction or not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    repository.check_budget()
    transitions = tuple(Transition(**item["value"]) for item in sorted(profile["transitions"], key=lambda item: item["order"]))
    legacy = {}
    for row in connection.execute("SELECT document,digest FROM usage_reconcile_events WHERE task_id=? ORDER BY source_order", (task_id,)):
        value = _decode(row)
        if value["legacy"] is not None:
            legacy[value["transition"]["transition_id"]] = tuple(value["legacy"])
    segments = owner_segments(transitions, legacy)
    owners = tuple(interval for interval, _, _ in segments)
    reviews, endpoints = _profile_reviews(profile)
    reviews = tuple(item for item in reviews if item[2].execution_id in {owner.execution_id for owner in owners})
    boundaries = tuple(ReviewBoundary(**dict(row)) for row in connection.execute(
        "SELECT * FROM usage_review_boundaries WHERE project_id=? AND task_id=?", (repository.basis[0], task_id)))
    receipts = tuple(ReviewReceiptTurn(**dict(row)) for row in connection.execute(
        "SELECT * FROM usage_review_receipt_turns WHERE receipt_id IN (SELECT value FROM json_each(?))",
        (encode(sorted({identity for identity, _, _ in reviews})).decode(),)))
    waits = tuple(WaitObservation(**dict(row)) for row in connection.execute(
        "SELECT * FROM usage_wait_turns WHERE project_id=? AND task_id=?", (repository.basis[0], task_id)))
    keys = {(value.thread_id, value.turn_id) for value in (*boundaries, *receipts, *waits)}
    keys.update((value.binding.session_id, value.turn_id) for value in endpoints)
    sessions = {binding.session_id for _, _, binding in reviews}
    turns, conflicts = endpoint_turns(connection, keys, sessions)
    review_ranges = reviewer_intervals(owners, turns, boundaries, receipts, reviews, collected_endpoints=endpoints,
                                      conflicting_turns=conflicts, conservative_legacy=True)
    incoming = {}
    orders = {entry["value"]["transition_id"]: entry["order"] for entry in profile["transitions"]}
    for interval, origin, endpoint in segments:
        incoming["fallback", origin] = {"interval": asdict(interval), "origins": [origin, endpoint],
            "coverage": coverage(connection, interval), "rank": [0, orders[endpoint]] if endpoint is not None else [1, orders[origin]]}
    if len(review_ranges) != len(reviews):
        raise UsageError("usage_schema_invalid")
    for (receipt, _, _), interval in zip(reviews, review_ranges):
        incoming["review", receipt] = {"interval": asdict(interval), "origins": None, "coverage": coverage(connection, interval), "rank": [1, receipt]}
    groups = {}
    for item in waits:
        groups.setdefault((item.execution_id, item.artifact_manifest_id), []).append(item)
    for key, observations in sorted(groups.items()):
        repository.check_budget()
        valid = frozenset(item for item in observations if wait_anchor(core, item) is not None)
        ranges = wait_intervals(owners, turns, observations, valid, conflicting_turns=conflicts)
        if ranges:
            if len(ranges) != 1:
                raise UsageError("usage_schema_invalid")
            incoming["wait", encode(key).decode()] = {"interval": asdict(ranges[0]), "origins": None,
                "coverage": coverage(connection, ranges[0]), "rank": [0, list(key)]}
    previous = {(row["kind"], row["origin"]): _decode(row) for row in connection.execute(
        "SELECT * FROM usage_reconcile_intervals WHERE task_id=?", (task_id,))}
    changed = []
    for key in sorted(previous.keys() | incoming.keys()):
        before, after = previous.get(key), incoming.get(key)
        # JSON canonicalization also normalizes tuple diagnostics on the new side.
        before_document = encode(before).decode() if before is not None else None
        after_document = encode(after).decode() if after is not None else None
        if before_document == after_document:
            continue
        changed.append(key)
        for value in (before, after):
            enqueue_range(connection, value["coverage"] if value is not None else None)
        replace_range(connection, *key, after["coverage"] if after is not None else None)
        connection.execute("INSERT INTO usage_reconcile_interval_work VALUES (?,?,?,?) ON CONFLICT(kind,origin) "
                           "DO UPDATE SET after_document=excluded.after_document", (*key, before_document, after_document))
        if after is None:
            connection.execute("DELETE FROM usage_reconcile_intervals WHERE kind=? AND origin=?", key)
        else:
            interval = after["interval"]
            connection.execute("INSERT INTO usage_reconcile_intervals VALUES (?,?,?,?,?,?,?) ON CONFLICT(kind,origin) DO UPDATE SET "
                "task_id=excluded.task_id,execution_id=excluded.execution_id,thread_id=excluded.thread_id,document=excluded.document,digest=excluded.digest",
                (*key, task_id, interval["execution_id"], interval["thread_id"], after_document, digest(after)))
    connection.execute("DELETE FROM usage_reconcile_interval_inputs WHERE task_id=?", (task_id,))
    references = {(thread, turn, "endpoint") for thread, turn in keys}
    references.update((thread, "", "review_session") for thread in sessions)
    connection.executemany("INSERT INTO usage_reconcile_interval_inputs VALUES (?,?,?,?)",
                           [(task_id, *value) for value in sorted(references)])
    _receipt_events(connection, task_id, transitions, incoming)
    repository.check_budget()
    connection.execute("DELETE FROM usage_reconcile_relation_task_work WHERE task_id=?", (task_id,))
    return tuple(changed)


def _receipt_events(connection, task_id, transitions, intervals):
    """Actual parent receipts may supply an otherwise absent done event turn."""
    from .usage_reconciliation_changes import _turn
    incoming = {}
    additional = [value["interval"] for (kind, _), value in intervals.items() if kind != "fallback"]
    for change in transitions:
        if change.current_status != "done":
            continue
        turns = {value["start_turn"] for value in additional if value["execution_id"] == change.execution_id
                 and value["thread_id"] == change.actor_session_id and value["start_turn"] is not None
                 and value["start_turn"] == value["end_turn"] and not value["diagnostics"]}
        if len(turns) == 1:
            incoming[change.transition_id] = (change.actor_session_id, next(iter(turns)))
    previous = {row["transition_id"]: (row["thread_id"], row["turn_id"]) for row in connection.execute(
        "SELECT * FROM usage_reconcile_receipt_events WHERE task_id=?", (task_id,))}
    for identity in previous.keys() | incoming.keys():
        old, new = previous.get(identity), incoming.get(identity)
        if old == new:
            continue
        for value in (old, new):
            if value is not None:
                _turn(connection, *value)
        if new is None:
            connection.execute("DELETE FROM usage_reconcile_receipt_events WHERE transition_id=?", (identity,))
        else:
            connection.execute("INSERT INTO usage_reconcile_receipt_events VALUES (?,?,?,?) ON CONFLICT(transition_id) DO UPDATE SET "
                               "task_id=excluded.task_id,thread_id=excluded.thread_id,turn_id=excluded.turn_id", (identity, task_id, *new))
        connection.execute("INSERT INTO usage_reconcile_event_work SELECT transition_id,task_id,thread_id,source_order "
                           "FROM usage_reconcile_events WHERE transition_id=? ON CONFLICT(transition_id) DO NOTHING", (identity,))
