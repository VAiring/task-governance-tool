"""Origin-preserving numerical membership and global owner-set partitions.

An affected interval replaces only its own contribution. Equal diagnostics
from other intervals remain, and disjoint windows with the same exact owner
set still form one partition. No immutable evidence is modified here.
"""

from .usage_evidence import digest, encode
from .usage_values import UsageError
from .usage_response_repository import component_identity
from .usage_reconciliation_owner_effects import TABLES as _OWNER_EFFECT_TABLES


TABLES = (*_OWNER_EFFECT_TABLES,
    """CREATE TABLE usage_reconcile_membership (
        kind TEXT NOT NULL,origin TEXT NOT NULL,provider TEXT NOT NULL,response_id TEXT NOT NULL,execution_id TEXT NOT NULL,
        PRIMARY KEY(kind,origin,provider,response_id,execution_id))""",
    "CREATE INDEX usage_reconcile_membership_key ON usage_reconcile_membership(provider,response_id,execution_id)",
    "CREATE INDEX usage_reconcile_membership_execution ON usage_reconcile_membership(execution_id,provider,response_id)",
    """CREATE TABLE usage_reconcile_unknown (
        kind TEXT NOT NULL,origin TEXT NOT NULL,provider TEXT NOT NULL,response_id TEXT NOT NULL,
        PRIMARY KEY(kind,origin,provider,response_id))""",
    "CREATE INDEX usage_reconcile_unknown_key ON usage_reconcile_unknown(provider,response_id)",
    """CREATE TABLE usage_reconcile_gaps (
        kind TEXT NOT NULL,origin TEXT NOT NULL,execution_id TEXT NOT NULL,code TEXT NOT NULL,
        PRIMARY KEY(kind,origin,execution_id,code))""",
    "CREATE INDEX usage_reconcile_gaps_execution ON usage_reconcile_gaps(execution_id,code)",
    "CREATE TABLE usage_reconcile_response_work (provider TEXT NOT NULL,response_id TEXT NOT NULL,PRIMARY KEY(provider,response_id))",
    "CREATE TABLE usage_reconcile_execution_work (execution_id TEXT PRIMARY KEY)",
    """CREATE TABLE usage_reconcile_response_result (
        provider TEXT NOT NULL,response_id TEXT NOT NULL,component_id TEXT,
        unknown INTEGER NOT NULL CHECK(unknown IN (0,1)),PRIMARY KEY(provider,response_id))""",
    "CREATE INDEX usage_reconcile_response_component ON usage_reconcile_response_result(component_id,provider,response_id)",
    """CREATE TABLE usage_reconcile_components (
        component_id TEXT PRIMARY KEY,assignment TEXT NOT NULL CHECK(assignment IN ('exclusive','shared','partial')),
        members TEXT NOT NULL)""",
    """CREATE TABLE usage_reconcile_component_executions (
        component_id TEXT NOT NULL,execution_id TEXT NOT NULL,PRIMARY KEY(component_id,execution_id))""",
    "CREATE INDEX usage_reconcile_components_execution ON usage_reconcile_component_executions(execution_id,component_id)",
    "CREATE TABLE usage_reconcile_component_work (component_id TEXT PRIMARY KEY)",
    """CREATE TABLE usage_reconcile_overlay_turn_work (
        thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,PRIMARY KEY(thread_id,turn_id))""",
    """CREATE TABLE usage_reconcile_source_admissions (
        kind TEXT NOT NULL,origin TEXT NOT NULL,thread_id TEXT NOT NULL,execution_id TEXT NOT NULL,
        PRIMARY KEY(kind,origin,thread_id,execution_id))""",
    "CREATE INDEX usage_reconcile_admissions_thread ON usage_reconcile_source_admissions(thread_id,execution_id)",
    "CREATE TABLE usage_reconcile_empty_components (execution_id TEXT PRIMARY KEY,component_id TEXT NOT NULL)",
)
KINDS = {"base", "order", "uncertainty", "fallback", "review", "wait", "source", "boundary", "missing"}


def _mark_keys(connection, keys):
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_response_work VALUES (?,?)", sorted(keys))


def replace_admissions(connection, kind, origin, admissions):
    """Source diagnostics propagate only through an actually admitted relation."""
    if not connection.in_transaction or kind not in KINDS:
        raise UsageError("usage_schema_invalid")
    previous = set(map(tuple, connection.execute("SELECT thread_id,execution_id FROM usage_reconcile_source_admissions "
                                                "WHERE kind=? AND origin=?", (kind, origin))))
    admissions = set(map(tuple, admissions))
    if previous == admissions:
        return False
    connection.execute("DELETE FROM usage_reconcile_source_admissions WHERE kind=? AND origin=?", (kind, origin))
    connection.executemany("INSERT INTO usage_reconcile_source_admissions VALUES (?,?,?,?)",
                           [(kind, origin, *value) for value in sorted(admissions)])
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_diagnostic_work VALUES (?)",
                           [(thread,) for thread in sorted({value[0] for value in previous | admissions})])
    return True


def reconcile_thread_diagnostics(connection, thread):
    """Changes in any source affect admitted relations in the whole session."""
    codes = set()
    for row in connection.execute("SELECT source.pending,gap.code FROM usage_sources source LEFT JOIN usage_diagnostics gap "
                                  "USING(source_id) WHERE source.thread_id=?", (thread,)):
        if row["code"] is not None:
            codes.add(row["code"])
        if row["pending"] == "partial_tail":
            codes.add("partial_tail")
        elif row["pending"] in {"unread", "more_records"}:
            codes.add("collection_pending")
        elif row["pending"] != "none":
            raise UsageError("usage_schema_invalid")
    owners = [row[0] for row in connection.execute("SELECT DISTINCT execution_id FROM usage_reconcile_source_admissions "
                                                 "WHERE thread_id=?", (thread,))]
    replace_contribution(connection, "source", thread, gaps=((owner, code) for owner in owners for code in codes))
    connection.execute("DELETE FROM usage_reconcile_diagnostic_work WHERE thread_id=?", (thread,))


def replace_contribution(connection, kind, origin, *, owners=(), unknown=(), gaps=()):
    """Replace one admitted origin, preserving other equal contributions.

    owners: (provider,response,execution), unknown: (provider,response),
    gaps: (execution,closed diagnostic code). The caller owns phase order and
    validates origin-specific geometry; this repository owns replacement.
    """
    if not connection.in_transaction or kind not in KINDS or type(origin) is not str or not origin:
        raise UsageError("usage_schema_invalid")
    owners, unknown, gaps = set(map(tuple, owners)), set(map(tuple, unknown)), set(map(tuple, gaps))
    before_owners = set(map(tuple, connection.execute("SELECT provider,response_id,execution_id FROM usage_reconcile_membership "
                                                    "WHERE kind=? AND origin=?", (kind, origin))))
    before_unknown = set(map(tuple, connection.execute("SELECT provider,response_id FROM usage_reconcile_unknown WHERE kind=? AND origin=?",
                                                      (kind, origin))))
    before_gaps = set(map(tuple, connection.execute("SELECT execution_id,code FROM usage_reconcile_gaps WHERE kind=? AND origin=?",
                                                   (kind, origin))))
    if (before_owners, before_unknown, before_gaps) == (owners, unknown, gaps):
        return False
    _mark_keys(connection, {row[:2] for row in before_owners | owners} | before_unknown | unknown)
    fallback_keys = before_unknown ^ unknown if kind in {"base", "order", "uncertainty"} else set()
    if kind == "base":
        fallback_keys.update(row[:2] for row in before_owners ^ owners)
    if fallback_keys:
        # Fallback reads both pre-overlay uncertainty and final base owners.
        # Either can change while the original turn has no ordered occurrence.
        # Invalidate that numerical turn, not only surviving position consumers.
        # Equal contributions return above; overlay owners never feed this edge.
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_overlay_turn_work "
            "SELECT response.thread_id,response.turn_id FROM json_each(?) changed "
            "CROSS JOIN usage_responses response ON response.provider=json_extract(changed.value,'$[0]') "
            "AND response.response_id=json_extract(changed.value,'$[1]')",
            (encode(sorted(fallback_keys)).decode(),))
    # Membership removals/additions also change whether an execution needs the
    # required empty pending component, even when no diagnostic was changed.
    executions = {row[2] for row in before_owners | owners} | {row[0] for row in before_gaps | gaps}
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_execution_work VALUES (?)", [(value,) for value in sorted(executions)])
    for table in ("usage_reconcile_membership", "usage_reconcile_unknown", "usage_reconcile_gaps"):
        connection.execute("DELETE FROM " + table + " WHERE kind=? AND origin=?", (kind, origin))
    connection.executemany("INSERT INTO usage_reconcile_membership VALUES (?,?,?,?,?)", [(kind, origin, *row) for row in sorted(owners)])
    connection.executemany("INSERT INTO usage_reconcile_unknown VALUES (?,?,?,?)", [(kind, origin, *row) for row in sorted(unknown)])
    connection.executemany("INSERT INTO usage_reconcile_gaps VALUES (?,?,?,?)", [(kind, origin, *row) for row in sorted(gaps)])
    return True


def _component(connection, assignment, members):
    members = tuple(sorted(set(members)))
    identity = component_identity({"assignment": assignment, "executions": list(members)})
    document = encode(members).decode()
    old = connection.execute("SELECT assignment,members FROM usage_reconcile_components WHERE component_id=?", (identity,)).fetchone()
    if old is not None and tuple(old) != (assignment, document):
        raise UsageError("usage_schema_invalid")
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_components VALUES (?,?,?)", (identity, assignment, document))
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_component_executions VALUES (?,?)", [(identity, value) for value in members])
    return identity


def reconcile_response(connection, provider, response):
    """Classify one original key after the owning phases finish their effects."""
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    key = provider, response
    before = connection.execute("SELECT component_id,unknown FROM usage_reconcile_response_result WHERE provider=? AND response_id=?", key).fetchone()
    owners = tuple(row[0] for row in connection.execute("SELECT DISTINCT execution_id FROM usage_reconcile_membership "
                                                       "WHERE provider=? AND response_id=? ORDER BY execution_id", key))
    original = connection.execute("SELECT thread_id FROM usage_responses WHERE provider=? AND response_id=?", key).fetchone()
    exists = original is not None
    conflict = connection.execute("SELECT 1 FROM usage_conflicts WHERE provider=? AND response_id=?", key).fetchone() is not None
    unknown = connection.execute("SELECT 1 FROM usage_reconcile_unknown WHERE provider=? AND response_id=? LIMIT 1", key).fetchone() is not None
    identity = None
    if exists and not conflict and owners:
        identity = _component(connection, "partial" if unknown else "exclusive" if len(owners) == 1 else "shared", owners)
    if exists and not conflict:
        connection.execute("INSERT INTO usage_reconcile_response_result VALUES (?,?,?,?) ON CONFLICT(provider,response_id) DO UPDATE SET "
                           "component_id=excluded.component_id,unknown=excluded.unknown", (*key, identity, int(unknown)))
    else:
        connection.execute("DELETE FROM usage_reconcile_response_result WHERE provider=? AND response_id=?", key)
    # Missing/conflicting original counters contribute diagnostics to their
    # actual owners but never acquire numerical membership themselves.
    replace_contribution(connection, "missing", encode(key).decode(), gaps=(
        (owner, "response_conflict" if conflict else "usage_pending") for owner in owners if not exists or conflict))
    replace_admissions(connection, "source", encode(key).decode(),
                       ((original[0], owner) for owner in owners) if exists and not conflict else ())
    from .usage_reconciliation_events import _old_value
    admitted = set()
    for row in connection.execute("SELECT * FROM usage_reconcile_events WHERE json_extract(document,'$.boundary.provider')=? "
                                  "AND json_extract(document,'$.boundary.response_id')=?", key):
        value = _old_value(row)
        change, boundary = value["transition"], value["boundary"]
        owner = value["affected_execution"]
        if (owner in owners and change["policy_version"] == 1 and change["actor_session_id"] == boundary["thread_id"]):
            admitted.add((change["actor_session_id"], owner))
    replace_admissions(connection, "boundary", encode(key).decode(), admitted)
    for component in {before[0] if before else None, identity} - {None}:
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_component_work VALUES (?)", (component,))
        retire_component(connection, component)
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_execution_work VALUES (?)", [(owner,) for owner in owners])
    connection.execute("DELETE FROM usage_reconcile_response_work WHERE provider=? AND response_id=?", key)
    from .usage_reconciliation_totals import reconcile
    reconcile(connection, *key)
    return identity


def retire_component(connection, identity):
    """Retire only an unused derived partition, retaining its queued adoption.

    Snapshot heads and unfinished immutable lineage have separate indexes.
    They must not keep obsolete owner sets in current diagnostic selection.
    """
    if (connection.execute("SELECT 1 FROM usage_reconcile_response_result WHERE component_id=? LIMIT 1", (identity,)).fetchone()
            or connection.execute("SELECT 1 FROM usage_reconcile_empty_components WHERE component_id=? LIMIT 1", (identity,)).fetchone()):
        return
    connection.execute("DELETE FROM usage_reconcile_component_executions WHERE component_id=?", (identity,))
    connection.execute("DELETE FROM usage_reconcile_components WHERE component_id=?", (identity,))


def reconcile_execution(connection, execution):
    """Propagate changed diagnostics and retain required empty pending owners."""
    known = connection.execute("SELECT 1 FROM usage_reconcile_events WHERE execution_id=? LIMIT 1", (execution,)).fetchone() is not None
    measured = connection.execute("SELECT 1 FROM usage_reconcile_component_executions member "
        "JOIN usage_reconcile_response_result response ON response.component_id=member.component_id "
        "WHERE member.execution_id=? LIMIT 1", (execution,)).fetchone() is not None
    old = connection.execute("SELECT component_id FROM usage_reconcile_empty_components WHERE execution_id=?", (execution,)).fetchone()
    if known and not measured:
        identity = _component(connection, "partial", (execution,))
        connection.execute("INSERT INTO usage_reconcile_empty_components VALUES (?,?) ON CONFLICT(execution_id) DO UPDATE SET component_id=excluded.component_id",
                           (execution, identity))
        replace_contribution(connection, "missing", execution, gaps=((execution, "usage_pending"), (execution, "response_binding_unknown")))
    else:
        connection.execute("DELETE FROM usage_reconcile_empty_components WHERE execution_id=?", (execution,))
        replace_contribution(connection, "missing", execution)
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_component_work SELECT component_id FROM usage_reconcile_component_executions WHERE execution_id=?",
                       (execution,))
    if old is not None:
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_component_work VALUES (?)", (old[0],))
        retire_component(connection, old[0])
    connection.execute("DELETE FROM usage_reconcile_execution_work WHERE execution_id=?", (execution,))


def component_geometry(connection, identity):
    """Read only this global partition, including its disjoint outside windows."""
    import json
    from .usage_response_attribution import ALGORITHM
    row = connection.execute("SELECT assignment,members FROM usage_reconcile_components WHERE component_id=?", (identity,)).fetchone()
    if row is None:
        raise UsageError("usage_schema_invalid")
    members = json.loads(row["members"])
    if component_identity({"assignment": row["assignment"], "executions": members}) != identity or encode(members).decode() != row["members"]:
        raise UsageError("usage_schema_invalid")
    keys = list(connection.execute("SELECT result.provider,result.response_id,response.thread_id,response.turn_id "
        "FROM usage_reconcile_response_result result JOIN usage_responses response USING(provider,response_id) "
        "WHERE result.component_id=? ORDER BY result.provider,result.response_id", (identity,)))
    gaps = {row[0] for row in connection.execute("SELECT DISTINCT code FROM usage_reconcile_gaps "
        "WHERE execution_id IN (SELECT value FROM json_each(?))", (encode(members).decode(),))}
    if row["assignment"] == "partial":
        gaps.add("response_binding_unknown")
    if connection.execute("SELECT 1 FROM usage_reconcile_unresolved LIMIT 1").fetchone():
        gaps.add("operation_unbound")
    return {"executions": members, "assignment": row["assignment"], "algorithm": ALGORITHM,
            "turns": sorted({(value[2], value[3]) for value in keys}),
            "response_keys": [(value[0], value[1]) for value in keys], "diagnostics": sorted(gaps), "models": []}


def apply_base_one(connection):
    """Replace one turn's base contribution; retain its later overlay work."""
    from .usage_reconciliation_turns import _document
    from .usage_reconciliation_core import _decode
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    work = connection.execute("SELECT thread_id,turn_id FROM usage_reconcile_turn_effects ORDER BY rowid LIMIT 1").fetchone()
    if work is None:
        from .usage_reconciliation_owner_effects import reconcile_one
        return reconcile_one(connection)
    key = tuple(work)
    origin = encode(key).decode()
    inputs = connection.execute("SELECT digest FROM usage_reconcile_turn_inputs WHERE thread_id=? AND turn_id=?", key).fetchone()
    allocation = connection.execute("SELECT * FROM usage_reconcile_turn_allocations WHERE thread_id=? AND turn_id=?", key).fetchone()
    if inputs is not None and (allocation is None or allocation["input_digest"] != inputs["digest"]):
        raise UsageError("usage_pending")
    value = _document(allocation) if inputs is not None else None
    from .usage_reconciliation_owner_effects import replace_actions
    clock = connection.execute("SELECT started_at FROM usage_reconcile_turn_inputs WHERE thread_id=? AND turn_id=?", key).fetchone()
    replace_actions(connection, *key, clock[0] if clock else 0, value["actions"] if value else ())
    replace_contribution(connection, "base", origin,
        gaps=((owner, code) for owner, codes in value["gaps"] for code in codes) if value else ())
    replace_admissions(connection, "base", origin, ((key[0], owner) for owner, _ in value["gaps"]) if value else ())
    ordered = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_ordered_inputs WHERE thread_id=? AND turn_id=?", key).fetchone())
    replace_contribution(connection, "order", origin, unknown=ordered["unknown"] if ordered else ())
    _mark_keys(connection, map(tuple, ordered["keys"]) if ordered else ())
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_overlay_turn_work VALUES (?,?)", key)
    connection.execute("DELETE FROM usage_reconcile_turn_effects WHERE thread_id=? AND turn_id=?", key)
    if inputs is None:
        connection.execute("DELETE FROM usage_reconcile_turn_allocations WHERE thread_id=? AND turn_id=?", key)
    return {"thread_id": key[0], "turn_id": key[1]}
