"""Resumable, Task-selected core inputs for numerical reconciliation.

The caller owns an admitted numerical writer and a fresh read-only core
snapshot. No core mutation or publication happens here. Cached values contain
only the structural inputs consumed by numerical attribution, not review prose.
"""

from dataclasses import asdict, dataclass
import json
import os

from .usage_evidence import digest, encode
from .usage_values import UsageError


CORE_INPUT_VERSION = 2
PAGE = 128
TABLES = (
    """CREATE TABLE usage_reconcile_core_cursor (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), document TEXT NOT NULL, digest TEXT NOT NULL)""",
    """CREATE TABLE usage_reconcile_core_inputs (
        task_id TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(generation>=0),
        document TEXT NOT NULL, digest TEXT NOT NULL)""",
    """CREATE TABLE usage_reconcile_core_work (
        task_id TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(generation>=0))""",
    "CREATE INDEX usage_reconcile_core_work_order ON usage_reconcile_core_work(generation,task_id)",
    """CREATE TABLE usage_reconcile_core_participants (
        task_id TEXT NOT NULL,session_id TEXT NOT NULL,PRIMARY KEY(task_id,session_id))""",
    "CREATE INDEX usage_reconcile_core_sessions ON usage_reconcile_core_participants(session_id,task_id)",
    """CREATE TABLE usage_reconcile_core_receipts (
        receipt_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,session_id TEXT NOT NULL)""",
    "CREATE INDEX usage_reconcile_core_receipts_task ON usage_reconcile_core_receipts(task_id,receipt_id)",
    """CREATE TABLE usage_reconcile_periods (
        task_id TEXT NOT NULL,ordinal INTEGER NOT NULL,preceding_completion TEXT,completion_cycle_id TEXT,
        PRIMARY KEY(task_id,ordinal))""",
    """CREATE TABLE usage_reconcile_period_executions (
        task_id TEXT NOT NULL,ordinal INTEGER NOT NULL,execution_id TEXT NOT NULL,
        PRIMARY KEY(task_id,ordinal,execution_id))""",
    "CREATE INDEX usage_reconcile_period_execution ON usage_reconcile_period_executions(execution_id,task_id,ordinal)",
)


@dataclass(frozen=True)
class CoreRevision:
    epoch: str
    generation: int
    invalidated: int
    schema: int
    file_identity: tuple

    def identity(self):
        from .usage_response_attribution import ALGORITHM
        from .usage_response_repository import _COLLECTED_ENDPOINT_BASIS, _LEGACY_REVIEW_BASIS
        return [[CORE_INPUT_VERSION, ALGORITHM, _COLLECTED_ENDPOINT_BASIS, _LEGACY_REVIEW_BASIS],
                self.epoch, self.invalidated, self.schema, list(self.file_identity)]


def revision(core, project_id):
    from .storage import current_schema_version
    from .viewer_projection_repository import read_state
    if not core.in_transaction:
        raise UsageError("usage_schema_invalid")
    schema = current_schema_version(core)
    if schema < 34:
        raise UsageError("usage_schema_invalid")
    state = read_state(core, project_id)
    databases = [row for row in core.execute("PRAGMA database_list") if row[1] == "main"]
    if len(databases) != 1 or not databases[0][2]:
        raise UsageError("usage_schema_invalid")
    stat = os.stat(databases[0][2], follow_symlinks=False)
    if not stat.st_ino:
        raise UsageError("usage_schema_invalid")
    return CoreRevision(state.database_epoch, state.source_generation, state.invalidated_generation,
                        schema, (stat.st_dev, stat.st_ino))


def _decode(row):
    if row is None:
        return None
    value = json.loads(row["document"])
    if digest(value) != row["digest"] or encode(value).decode() != row["document"]:
        raise UsageError("usage_schema_invalid")
    return value


def cursor(connection):
    value = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_core_cursor WHERE singleton=1").fetchone())
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {"identity", "through", "after_generation", "after_task", "complete"}
            or type(value["identity"]) is not list or len(value["identity"]) != 5
            or type(value["through"]) is not int or not 0 <= value["through"] < 2**63
            or type(value["after_generation"]) is not int or not -1 <= value["after_generation"] <= value["through"]
            or type(value["after_task"]) is not str or type(value["complete"]) is not bool):
        raise UsageError("usage_schema_invalid")
    return value


def _save_cursor(connection, value):
    connection.execute("INSERT INTO usage_reconcile_core_cursor VALUES (1,?,?) ON CONFLICT(singleton) "
                       "DO UPDATE SET document=excluded.document,digest=excluded.digest", (encode(value).decode(), digest(value)))


def read_task_inputs(core, project_id, task_ids):
    """Fresh admission of selected histories; no persistent verified flags."""
    from .task_ownership import validate_storage_rows
    from .usage_attribution import Transition
    from .usage_evidence_repository import core_basis
    from .review_answer_repository import _read_usage_review_inputs
    validate_storage_rows(core, project_id=project_id, task_ids=task_ids)
    transitions = tuple(core.execute("SELECT rowid AS source_order,* FROM task_owner_transitions "
                                    "WHERE project_id=? AND task_id IN (SELECT value FROM json_each(?)) ORDER BY rowid",
                                    (project_id, json.dumps(sorted(task_ids)))))
    _, periods = core_basis(core, project_id, task_ids=task_ids)
    reviews = _read_usage_review_inputs(core, project_id=project_id, task_ids=task_ids)
    result = {task_id: {"transitions": [], "periods": [], "reviews": [], "endpoints": [], "participants": []} for task_id in task_ids}
    participants = {task_id: set() for task_id in task_ids}
    for row in core.execute("SELECT task_id,actor_session_id FROM task_owner_transitions WHERE project_id=? "
                            "AND task_id IN (SELECT value FROM json_each(?)) AND state='owned'",
                            (project_id, json.dumps(sorted(task_ids)))):
        participants[row["task_id"]].add(row["actor_session_id"])
    for transition in transitions:
        # Core append order across Tasks is required for same-response events,
        # ownership displacement and reversal fences. Task-local generation is
        # not a substitute for that order; the enclosing epoch fences restores.
        item = Transition(**{key: transition[key] for key in Transition.__dataclass_fields__})
        result[item.task_id]["transitions"].append({"order": transition["source_order"], "value": asdict(item)})
    for period in periods:
        result[period["task_id"]]["periods"].append({**period, "executions": sorted(period["executions"])})
    for identity, target, binding in reviews.reviews:
        result[target.task_id]["reviews"].append([identity, asdict(target), asdict(binding)])
        participants[target.task_id].add(binding.session_id)
    for endpoint in reviews.collected_endpoints:
        result[endpoint.target.task_id]["endpoints"].append(asdict(endpoint))
    from .session_identity import is_session_id
    for task_id, sessions in participants.items():
        if any(not is_session_id(session) for session in sessions):
            raise UsageError("usage_schema_invalid")
        result[task_id]["participants"] = sorted(sessions)
    return result


def sync_page(repository, core, connection):
    """Commit at most one selected page with its exact captured frontier.

    Coalesced marks above the fence remain for the next window. The caller may
    stop after any committed page; a partial baseline is never publication-ready.
    """
    repository.check_budget()
    current = revision(core, repository.basis[0])
    previous = cursor(connection)
    reset = previous is None or previous["identity"] != current.identity() or previous["through"] > current.generation
    if reset:
        repository._reset_reconciliation(connection)
        state = {"identity": current.identity(), "through": current.generation,
                 "after_generation": -1, "after_task": "", "complete": False}
    elif previous["complete"] and previous["through"] == current.generation:
        return {"complete": True, "reset": False, "selected": 0, "changed": (), "revision": current}
    elif previous["complete"]:
        # All old marks were consumed; select only marks strictly above it.
        state = {**previous, "through": current.generation, "after_generation": previous["through"],
                 "after_task": "\U0010ffff", "complete": False}
    else:
        state = dict(previous)
    rows = tuple(core.execute("""SELECT r.task_id,r.source_generation,t.project_id AS parent_project
        FROM viewer_task_revisions r LEFT JOIN tasks t ON t.task_id=r.task_id
        WHERE r.project_id=? AND r.source_generation<=? AND
          (r.source_generation>? OR (r.source_generation=? AND r.task_id>?))
        ORDER BY r.source_generation,r.task_id LIMIT ?""",
        (repository.basis[0], state["through"], state["after_generation"], state["after_generation"], state["after_task"], PAGE)))
    if any(row["parent_project"] != repository.basis[0] or type(row["source_generation"]) is not int
           or not 0 <= row["source_generation"] <= state["through"] for row in rows):
        raise UsageError("usage_schema_invalid")
    selected = {row["task_id"]: row["source_generation"] for row in rows}
    inputs = read_task_inputs(core, repository.basis[0], set(selected)) if selected else {}
    changed = []
    for task_id, value in inputs.items():
        repository.check_budget()
        old = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_core_inputs WHERE task_id=?", (task_id,)).fetchone())
        # Even equal structural projections need selected operation/anchor
        # resolution: a newly committed event/manifest can satisfy a previously
        # unresolved numerical observation without changing a transition.
        connection.execute("INSERT INTO usage_reconcile_core_work VALUES (?,?) ON CONFLICT(task_id) "
                           "DO UPDATE SET generation=excluded.generation", (task_id, selected[task_id]))
        if old != value:
            changed.append(task_id)
        if old is None or old["periods"] != value["periods"]:
            before_executions = {row[0] for row in connection.execute(
                "SELECT execution_id FROM usage_reconcile_period_executions WHERE task_id=?", (task_id,))}
            after_executions = {execution for period in value["periods"] for execution in period["executions"]}
            connection.executemany("INSERT OR IGNORE INTO usage_reconcile_execution_work VALUES (?)",
                                   [(identity,) for identity in sorted(before_executions | after_executions)])
            connection.execute("DELETE FROM usage_reconcile_periods WHERE task_id=?", (task_id,))
            connection.execute("DELETE FROM usage_reconcile_period_executions WHERE task_id=?", (task_id,))
            for ordinal, period in enumerate(value["periods"]):
                connection.execute("INSERT INTO usage_reconcile_periods VALUES (?,?,?,?)",
                    (task_id, ordinal, period["preceding_completion"], period["completion_cycle_id"]))
                connection.executemany("INSERT INTO usage_reconcile_period_executions VALUES (?,?,?)",
                                       [(task_id, ordinal, execution) for execution in period["executions"]])
        connection.execute("DELETE FROM usage_reconcile_core_participants WHERE task_id=?", (task_id,))
        connection.executemany("INSERT INTO usage_reconcile_core_participants VALUES (?,?)",
                               [(task_id, session) for session in value["participants"]])
        connection.execute("DELETE FROM usage_reconcile_core_receipts WHERE task_id=?", (task_id,))
        connection.executemany("INSERT INTO usage_reconcile_core_receipts VALUES (?,?,?)",
                               [(receipt, task_id, binding["session_id"]) for receipt, _, binding in value["reviews"]])
        connection.execute("INSERT INTO usage_reconcile_core_inputs VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                           "generation=excluded.generation,document=excluded.document,digest=excluded.digest",
                           (task_id, selected[task_id], encode(value).decode(), digest(value)))
    if rows:
        state.update(after_generation=rows[-1]["source_generation"], after_task=rows[-1]["task_id"])
    state["complete"] = len(rows) < PAGE
    _save_cursor(connection, state)
    return {"complete": state["complete"] and state["through"] == current.generation,
            "reset": reset, "selected": len(rows), "changed": tuple(changed), "revision": current}


def participants(repository, core_reader):
    """A finite discovery page, not full-history validation on each event.

    A partial baseline commits its progress and returns pending. The collector
    retains its existing registered participants/direct-source progress.
    """
    result = repository.reconcile_core_page(core_reader)
    if not result["complete"]:
        raise UsageError("usage_pending")
    with repository.connection() as connection:
        return frozenset(row[0] for row in connection.execute(
            "SELECT DISTINCT session_id FROM usage_reconcile_core_participants ORDER BY session_id"))
