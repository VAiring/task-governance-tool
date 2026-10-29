"""Compose finite reconciliation with the existing immutable-file audit."""

from contextlib import contextmanager

from .usage_evidence import digest
from .usage_publication_repository import INTEGRITY
from .usage_response_attribution import ALGORITHM
from .usage_reconciliation_basis import require_inputs
from .usage_reconciliation_totals import totals
from .usage_values import UsageError


def reconcile(repository, core_reader):
    from .usage_reconciliation_runtime import advance_initial, advance_effects
    from .usage_reconciliation_snapshots import advance
    for function, complete in ((advance_initial, "effects_pending"), (advance_effects, "classification_pending"), (advance, "captured")):
        while True:
            repository.check_budget()
            result = function(repository, core_reader)
            if result["phase"] == complete:
                break
    return result["basis"]


def captured_basis(repository, core, connection):
    basis = digest(require_inputs(repository, core, connection, classified=True))
    capture = connection.execute("SELECT core_digest FROM usage_capture WHERE singleton=1").fetchone()
    guard = connection.execute("SELECT basis,evidence_generation FROM usage_reconcile_capture_guard WHERE singleton=1").fetchone()
    current = connection.execute("SELECT generation FROM usage_reconcile_evidence_revision WHERE singleton=1").fetchone()
    completed = connection.execute("SELECT basis,complete FROM usage_reconcile_snapshot_round WHERE singleton=1").fetchone()
    if (capture is None or capture[0] != basis or guard is None or guard[0] != basis or current is None or guard[1] != current[0]
            or completed is None or tuple(completed) != (basis, 1)
            or connection.execute("SELECT 1 FROM usage_reconcile_snapshot_stage LIMIT 1").fetchone()
            or connection.execute("SELECT 1 FROM usage_reconcile_snapshot_lineage LIMIT 1").fetchone()
            or connection.execute("SELECT 1 FROM usage_reconcile_component_work LIMIT 1").fetchone()):
        raise UsageError("usage_pending")
    return basis


def metadata(connection):
    # Serialization necessarily lists the immutable IDs/cycle links. It does
    # not reclassify history or read old original numerical counters.
    identities = [row[0] for row in connection.execute("SELECT snapshot_id FROM usage_snapshots ORDER BY rowid")]
    current = [row[0] for row in connection.execute("SELECT snapshot.snapshot_id FROM usage_reconcile_snapshot_heads head "
                                                    "JOIN usage_snapshots snapshot USING(snapshot_id) ORDER BY snapshot.rowid")]
    links = [dict(row) for row in connection.execute("SELECT link.* FROM usage_cycle_links link "
        "JOIN usage_reconcile_periods period ON period.task_id=link.task_id AND period.completion_cycle_id=link.completion_cycle_id "
        "WHERE EXISTS (SELECT 1 FROM usage_snapshots snapshot,json_each(snapshot.document,'$.executions') execution "
        "JOIN usage_reconcile_period_executions member ON member.execution_id=execution.value "
        "WHERE snapshot.snapshot_id=link.snapshot_id AND member.task_id=period.task_id AND member.ordinal=period.ordinal) ORDER BY link.rowid")]
    return {"snapshots": identities, "current_snapshot_ids": current, "cycle_links": links}


def prepare(repository, core_reader):
    reconcile(repository, core_reader)
    with repository.connection(write=True) as connection, core_reader() as core:
        basis = captured_basis(repository, core, connection)
        index = {"format": "taskgov-usage-index-v2", "project_id": repository.basis[0], "core_digest": basis,
                 "attribution": ALGORITHM, "integrity": dict(INTEGRITY), **metadata(connection), "unattributed": totals(connection)}
        identity = digest(index)
        previous = repository._publication_state(connection)
        state = {"revision": previous["revision"] + 1 if previous else 1, "basis": identity,
                 "position": previous["position"] if previous and previous["basis"] == identity
                    and previous["total"] == len(index["snapshots"]) and not previous["published"] else 0,
                 "total": len(index["snapshots"]), "published": 0}
        repository._save_publication_state(connection, state)
    return index, state


@contextmanager
def guard(repository, core_reader, index, state):
    index_identity = digest(index)
    repository.check_budget()
    with repository.connection() as connection, core_reader() as core:
        if (repository._publication_state(connection) != state or state["position"] != state["total"]
                or state["basis"] != index_identity):
            raise UsageError("cursor_stale")
        if captured_basis(repository, core, connection) != index["core_digest"]:
            raise UsageError("usage_pending")
        repository.check_budget()
        yield


def periods(connection):
    result = {}
    for row in connection.execute("SELECT period.*,member.execution_id FROM usage_reconcile_periods period "
        "LEFT JOIN usage_reconcile_period_executions member USING(task_id,ordinal) ORDER BY period.task_id,period.ordinal,member.execution_id"):
        item = result.setdefault((row["task_id"], row["ordinal"]), {"task_id": row["task_id"], "preceding_completion": row["preceding_completion"],
                   "completion_cycle_id": row["completion_cycle_id"], "executions": set()})
        if row["execution_id"] is not None:
            item["executions"].add(row["execution_id"])
    return list(result.values())
