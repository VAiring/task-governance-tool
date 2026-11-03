"""Serialized initial phases of the numerical reconciliation state machine.

Each step opens its own numerical writer before a fresh core read. Persisted
queues carry later phases; a completed initial phase is never publication
readiness. The production factory selects this implementation only together
with its completed effect/publication integration.
"""

from time import monotonic

from .usage_attribution import Transition
from .usage_reconciliation_core import cursor, revision, sync_page, _decode
from .usage_reconciliation_changes import consume_page, expand_source_page
from .usage_reconciliation_baseline import establish_page
from .usage_reconciliation_events import reconcile_task
from .usage_reconciliation_intervals import reconcile_intervals
from .usage_reconciliation_inputs import stage_one
from .usage_reconciliation_turns import replay_one
from .usage_reconciliation_membership import apply_base_one
from .usage_reconciliation_basis import admit_identity, capture_inputs, require_inputs, global_page
from .usage_values import UsageError


_RESPONSE_PAGE_SIZE = 8
_RESPONSE_PAGE_SECONDS = 0.050
_TURN_PAGE_SIZE = 128
_TURN_PAGE_SECONDS = 0.250


def _turn_page(repository, phase, first, step, started, committed_phases):
    """Finish only this phase in the caller's admitted writer/read transaction.

    The caller records the phase only after this transaction commits. A new
    reconciliation run therefore always makes its first feasible unit durable
    before optional siblings can fail. No connection or admission escapes here.
    """
    if phase in committed_phases:
        stop = min(started + _TURN_PAGE_SECONDS, repository.deadline - 0.050)
        for _ in range(1, _TURN_PAGE_SIZE):
            if monotonic() >= stop:
                break
            repository.check_budget()
            if step() is None:
                break
    return {"phase": phase, **first}


def _exists(connection, table):
    return connection.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone() is not None


def _profile(repository, core, connection, task):
    value = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_core_inputs WHERE task_id=?", (task,)).fetchone())
    if value is None:
        if core.execute("SELECT 1 FROM tasks WHERE project_id=? AND task_id=?", (repository.basis[0], task)).fetchone():
            raise UsageError("usage_schema_invalid")
        return {"transitions": [], "periods": [], "reviews": [], "endpoints": [], "participants": []}
    return value


def _resolve_one(repository, core, connection):
    work = connection.execute("SELECT task_id FROM usage_reconcile_core_work ORDER BY generation,task_id LIMIT 1").fetchone()
    if work is None:
        work = connection.execute("SELECT task_id FROM usage_reconcile_task_work ORDER BY rowid LIMIT 1").fetchone()
    if work is None:
        return None
    task = work[0]
    profile = _profile(repository, core, connection, task)
    reconcile_task(repository, core, connection, task,
                   [(value["order"], Transition(**value["value"])) for value in profile["transitions"]])
    connection.execute("INSERT OR IGNORE INTO usage_reconcile_relation_task_work VALUES (?)", (task,))
    connection.execute("DELETE FROM usage_reconcile_core_work WHERE task_id=?", (task,))
    connection.execute("DELETE FROM usage_reconcile_task_work WHERE task_id=?", (task,))
    return {"task_id": task}


def _participant_one(connection):
    work = connection.execute("SELECT thread_id FROM usage_reconcile_participant_work ORDER BY rowid LIMIT 1").fetchone()
    if work is not None:
        for row in connection.execute("SELECT DISTINCT task_id FROM usage_reconcile_interval_inputs WHERE thread_id=? AND kind='review_session'", (work[0],)):
            connection.execute("INSERT OR IGNORE INTO usage_reconcile_relation_task_work VALUES (?)", (row[0],))
        connection.execute("DELETE FROM usage_reconcile_participant_work WHERE thread_id=?", (work[0],))
        return {"thread_id": work[0]}
    work = connection.execute("SELECT thread_id,turn_id FROM usage_reconcile_interval_turn_work ORDER BY rowid LIMIT 1").fetchone()
    if work is None:
        return None
    for row in connection.execute("SELECT DISTINCT task_id FROM usage_reconcile_interval_inputs WHERE thread_id=? AND turn_id=?", tuple(work)):
        connection.execute("INSERT OR IGNORE INTO usage_reconcile_relation_task_work VALUES (?)", (row[0],))
    connection.execute("DELETE FROM usage_reconcile_interval_turn_work WHERE thread_id=? AND turn_id=?", tuple(work))
    return dict(work)


def advance_initial(repository, core_reader, *, committed_phases=()):
    """Commit one admission unit or one bounded selected-phase page."""
    started = monotonic()
    with repository.connection(write=True) as connection, core_reader() as core:
        repository.check_budget()
        admit_identity(repository, connection)
        current, previous = revision(core, repository.basis[0]), cursor(connection)
        if (previous is None or previous["identity"] != current.identity() or previous["through"] != current.generation
                or not previous["complete"]):
            return {"phase": "core", **sync_page(repository, core, connection)}
        baseline = connection.execute("SELECT complete FROM usage_reconcile_baseline WHERE singleton=1").fetchone()
        if baseline is None or not baseline[0]:
            return {"phase": "baseline", **establish_page(repository, connection)}
        if _exists(connection, "usage_reconcile_changes"):
            return {"phase": "changes", **consume_page(repository, connection)}
        source = expand_source_page(repository, connection)
        if source is not None:
            return {"phase": "source", **source}
        resolved = _resolve_one(repository, core, connection)
        if resolved is not None:
            return {"phase": "operations", **resolved}
        participant = _participant_one(connection)
        if participant is not None:
            return {"phase": "participants", **participant}
        work = connection.execute("SELECT task_id FROM usage_reconcile_relation_task_work ORDER BY rowid LIMIT 1").fetchone()
        if work is not None:
            changed = reconcile_intervals(repository, core, connection, work[0], _profile(repository, core, connection, work[0]))
            return {"phase": "intervals", "task_id": work[0], "changed": changed}
        staged = stage_one(repository, core, connection)
        if staged is not None:
            return _turn_page(repository, "turn_inputs", staged,
                lambda: stage_one(repository, core, connection), started, committed_phases)
        def execution_task(execution):
            rows = tuple(connection.execute("SELECT DISTINCT task_id FROM usage_reconcile_events WHERE execution_id=? LIMIT 2", (execution,)))
            if len(rows) != 1:
                raise UsageError("usage_schema_invalid")
            return rows[0][0]
        replayed = replay_one(connection, execution_task=execution_task)
        if replayed is not None:
            return _turn_page(repository, "allocation", replayed,
                lambda: replay_one(connection, execution_task=execution_task), started, committed_phases)
        base = apply_base_one(connection)
        if base is not None:
            return _turn_page(repository, "base_effects", base,
                lambda: apply_base_one(connection), started, committed_phases)
        capture_inputs(repository, core, connection)
        return {"phase": "effects_pending"}


def advance_effects(repository, core_reader, *, single_response=False, committed_phases=()):
    """Selected effect stages; publication still has its separate fresh guard."""
    from . import usage_reconciliation_uncertainty as uncertainty
    from . import usage_reconciliation_overlays as overlays
    from .usage_reconciliation_ranges import expand_range_page
    from . import usage_reconciliation_membership as membership
    from . import usage_reconciliation_pending as pending
    started = monotonic()
    with repository.connection(write=True) as connection, core_reader() as core:
        repository.check_budget()
        require_inputs(repository, core, connection)
        located = uncertainty.locate_one(connection)
        if located is not None:
            return {"phase": "event_locations", "transition_id": located}
        source_pending = pending.source_one(connection)
        if source_pending is not None:
            return {"phase": "pending_source", "source_id": source_pending}
        rule = uncertainty.rebuild_one(connection)
        if rule is not None:
            return {"phase": "uncertainty_rule", "rule": rule}
        interval = overlays.interval_metadata_one(connection)
        if interval is not None:
            return {"phase": "interval_metadata", "interval": interval}
        expanded = expand_range_page(connection)
        if expanded is not None:
            return {"phase": "affected_turns", **expanded}
        def overlay_one():
            turn = connection.execute("SELECT thread_id,turn_id FROM usage_reconcile_overlay_turn_work ORDER BY rowid LIMIT 1").fetchone()
            if turn is None:
                return None
            uncertainty.apply_turn(connection, *turn)
            return overlays.apply_turn(connection, *turn)
        overlaid = overlay_one()
        if overlaid is not None:
            return _turn_page(repository, "turn_overlays", overlaid, overlay_one, started, committed_phases)
        responses = connection.execute("SELECT provider,response_id FROM usage_reconcile_response_work "
            "ORDER BY rowid LIMIT ?", (1 if single_response else _RESPONSE_PAGE_SIZE,)).fetchall()
        if responses:
            repository.check_budget()
            # The first key may use the remaining hard budget. Start optional
            # siblings only within the lock-time allowance and with commit margin.
            stop = min(started + _RESPONSE_PAGE_SECONDS, repository.deadline - _RESPONSE_PAGE_SECONDS)
            for response in responses:
                membership.reconcile_response(connection, *response)
                if monotonic() >= stop:
                    break
            return {"phase": "response", "key": tuple(responses[0])}
        source = connection.execute("SELECT thread_id FROM usage_reconcile_diagnostic_work ORDER BY rowid LIMIT 1").fetchone()
        if source is not None:
            membership.reconcile_thread_diagnostics(connection, source[0])
            return {"phase": "diagnostics", "thread_id": source[0]}
        execution = connection.execute("SELECT execution_id FROM usage_reconcile_execution_work ORDER BY rowid LIMIT 1").fetchone()
        if execution is not None:
            membership.reconcile_execution(connection, execution[0])
            return {"phase": "execution", "execution_id": execution[0]}
        global_work = global_page(connection)
        if global_work is not None:
            return {"phase": "global_diagnostics", "selected": global_work}
        return {"phase": "classification_pending"}
