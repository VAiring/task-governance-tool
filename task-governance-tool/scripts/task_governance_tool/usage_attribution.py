"""Inclusive-turn interval and shared-component projection; no I/O or gates.

Inputs are admitted core transitions, verified operation/turn bindings and
registered numerical observations. Ownership executions remain graph nodes;
a Task's completion period is a separate cumulative view over those nodes.
"""

from __future__ import annotations

from dataclasses import dataclass

from task_governance_tool.usage_turn_adapter import TurnObservation
from task_governance_tool.usage_values import METRICS, ResponseUsage


@dataclass(frozen=True)
class Transition:
    transition_id: str
    task_id: str
    execution_id: str | None
    generation: int
    previous_status: str | None
    current_status: str
    actor_session_id: str


@dataclass(frozen=True)
class Interval:
    task_id: str
    execution_id: str
    preceding_completion: str | None
    thread_id: str
    start_turn: str | None
    end_turn: str | None
    closed: bool
    diagnostics: tuple[str, ...] = ()


def owner_intervals(transitions: tuple[Transition, ...], bindings: dict[str, tuple[str, str]]) -> tuple[Interval, ...]:
    """Core append order, not log arrival order, determines Task state changes."""
    active, preceding, result = {}, {}, []
    for change in transitions:
        key = change.task_id
        boundary = bindings.get(change.transition_id)
        if change.current_status == "in_progress" and change.previous_status != "in_progress":
            start = boundary[1] if boundary and boundary[0] == change.actor_session_id else None
            active[key] = Interval(key, change.execution_id, preceding.get(key),
                                   change.actor_session_id, start, None, False)
        if change.previous_status == "in_progress" and change.current_status != "in_progress":
            opened = active.pop(key, None)
            if opened is not None:
                end = boundary[1] if boundary and boundary[0] == opened.thread_id else None
                result.append(Interval(opened.task_id, opened.execution_id, opened.preceding_completion,
                                       opened.thread_id, opened.start_turn, end, True))
        if change.current_status == "done":
            preceding[key] = change.transition_id
    result.extend(active.values())
    return tuple(result)


def _coverage(interval: Interval, starts: dict[tuple[str, str], int]):
    first = (interval.thread_id, interval.start_turn)
    if interval.start_turn is None or first not in starts:
        last = (interval.thread_id, interval.end_turn)
        known_end = {last} if interval.closed and last in starts else set()
        return known_end, {"boundary_unknown"}
    start = starts[first]
    if interval.closed:
        last = (interval.thread_id, interval.end_turn)
        if interval.end_turn is None or last not in starts or starts[last] < start:
            # The entry turn is definitely included; never guess the lost exit.
            return {first}, {"boundary_unknown"}
        end = starts[last]
        return {key for key, order in starts.items()
                if key[0] == interval.thread_id and start <= order <= end}, set()
    return {key for key, order in starts.items()
            if key[0] == interval.thread_id and start <= order}, {"interval_open"}


def _models(responses):
    result = {}
    for response in sorted(responses, key=lambda row: (row.provider, row.model or "", row.response_id)):
        key = (response.provider, response.model)
        bucket = result.setdefault(key, {"provider": key[0], "model": key[1],
                                         "response_count": 0, **dict.fromkeys(METRICS, 0)})
        bucket["response_count"] += 1
        for name, value in zip(METRICS, response.counts):
            bucket[name] = None if value is None or bucket[name] is None else bucket[name] + value
    return list(result.values())


def project(intervals: tuple[Interval, ...], turns: tuple[TurnObservation, ...],
            responses: tuple[ResponseUsage, ...], *, conflicting_responses=frozenset(),
            conflicting_turns=frozenset(), thread_diagnostics=None) -> dict:
    """Recompute unions from original keys, never add older aggregate totals.

Turn start values order explicitly identified turns in one thread; they never
join a Task to a log record or connect different threads by overlapping time.
This projection reports observed/pending usage, not final coverage or billing.
"""
    starts, invalid = {}, set(conflicting_turns)
    orders = {}
    for turn in turns:
        key = (turn.thread_id, turn.turn_id)
        order = (turn.thread_id, turn.started_at)
        if (key in starts and starts[key] != turn.started_at):
            invalid.add(key)
        if order in orders and orders[order] != key:
            invalid.update((key, orders[order]))
        orders[order] = key
        starts[key] = turn.started_at
    starts = {key: value for key, value in starts.items() if key not in invalid}
    execution_turns, execution_gaps, periods = {}, {}, {}
    for interval in intervals:
        covered, gaps = _coverage(interval, starts)
        gaps.update(interval.diagnostics)
        gaps.update((thread_diagnostics or {}).get(interval.thread_id, ()))
        execution_turns.setdefault(interval.execution_id, set()).update(covered)
        execution_gaps.setdefault(interval.execution_id, set()).update(gaps)
        periods.setdefault((interval.task_id, interval.preceding_completion), set()).add(interval.execution_id)

    parents = {execution: execution for execution in execution_turns}

    def root(execution):
        while parents[execution] != execution:
            parents[execution] = parents[parents[execution]]
            execution = parents[execution]
        return execution

    turn_owners = {}
    for execution, covered in execution_turns.items():
        for turn in covered:
            if turn in turn_owners:
                parents[root(execution)] = root(turn_owners[turn])
            else:
                turn_owners[turn] = execution
    components = {}
    for execution in execution_turns:
        components.setdefault(root(execution), set()).add(execution)
    # Conflicting keys are excluded, including inconsistent duplicate input.
    unique, conflicts = {}, set(conflicting_responses)
    for response in responses:
        key = (response.provider, response.response_id)
        if key in unique and unique[key] != response:
            conflicts.add(key)
        else:
            unique[key] = response
    good = {key: row for key, row in unique.items() if key not in conflicts}
    projected = []
    for members in components.values():
        covered = set().union(*(execution_turns[execution] for execution in members))
        keys = {key for key, row in good.items() if (row.thread_id, row.turn_id) in covered}
        gaps = set().union(*(execution_gaps[execution] for execution in members))
        if any((row.thread_id, row.turn_id) in covered for key, row in unique.items() if key in conflicts):
            gaps.add("response_conflict")
        if covered - {(good[key].thread_id, good[key].turn_id) for key in keys}:
            gaps.add("usage_pending")
        projected.append({"executions": sorted(members), "turns": sorted(covered),
                          "response_keys": sorted(keys), "models": _models(good[key] for key in keys),
                          "diagnostics": sorted(gaps), "status": "incomplete" if gaps else "pending"})
    cumulative = []
    for (task, preceding), executions in periods.items():
        selected = [component for component in projected if executions.intersection(component["executions"])]
        keys = {tuple(key) for component in selected for key in component["response_keys"]}
        cumulative.append({"task_id": task, "preceding_completion": preceding,
                           "own_executions": sorted(executions),
                           "shared_executions": sorted({member for component in selected for member in component["executions"]}),
                           "response_keys": sorted(keys), "models": _models(good[key] for key in keys)})
    assigned = {key for component in projected for key in component["response_keys"]}
    return {"components": projected, "tasks": cumulative,
            "unassigned_response_keys": sorted(set(good) - assigned)}
