"""Whole-turn owner allocation shared by full and incremental attribution.

Only the active execution crosses a distinct turn boundary. Completion tails
and preparation checks are turn-local. Uncertainty/fallback/reviewer overlays
remain separate later phases; convergence here cannot terminate those phases.
"""

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class TurnAllocation:
    active: str | None
    owners: dict
    gaps: dict
    state_before: dict
    unknown: set
    actions: dict


def apply_actions(owners, actions):
    """Apply ordered effects to an original key, including prior occurrences."""
    unknown = False
    for action, execution in actions:
        if action == "add":
            owners.add(execution)
        elif action == "remove":
            owners.discard(execution)
        elif action == "check":
            unknown |= len(owners) > 1
        else:
            raise ValueError("invalid owner action")
    return unknown


def allocate_turn(active, keys, events, check_keys, execution_tasks, thread_gaps=()):
    """Consume one complete ordered turn, retaining global core event order."""
    owners, gaps, before = defaultdict(set), defaultdict(set), {}
    actions = defaultdict(list)
    def apply(key, action, execution):
        actions[key].append((action, execution))
        if apply_actions(owners[key], ((action, execution),)):
            unknown.add(key)
    completed, pending_checks, unknown = None, [], set()
    for key in keys:
        before[key] = {owner for owner in (active, completed) if owner is not None}
        row_events = sorted(events.get(key, ()))
        beginning = []
        for _, change in row_events:
            covered = change.current_status == "in_progress" or (
                change.policy_version == 1 and change.current_status == "review_pending")
            previous_covered = change.previous_status == "in_progress" or (
                change.policy_version == 1 and change.previous_status == "review_pending")
            if covered and not previous_covered:
                beginning.append(change.execution_id)
        beginning = list(dict.fromkeys(beginning))
        if beginning:
            for earlier in pending_checks:
                if completed is not None:
                    apply(earlier, "remove", completed)
                apply(earlier, "add", beginning[0])
            pending_checks = []
            for execution in beginning:
                apply(key, "add", execution)
        elif active is not None:
            apply(key, "add", active)
        elif completed is not None:
            apply(key, "add", completed)
        if not beginning and key in check_keys and active is None:
            pending_checks.append(key)
        elif not beginning and pending_checks and active is None:
            pending_checks.append(key)
        for _, change in row_events:
            if change.current_status in {"in_progress", "review_pending"}:
                active, completed = change.execution_id, None
            elif change.current_status == "done":
                if not beginning and (active is None or active == change.execution_id):
                    apply(key, "add", change.execution_id)
                if active == change.execution_id or active is None:
                    active, completed = None, change.execution_id
            elif change.current_status in {"paused", "blocked", "ready", "cancelled"}:
                active_match = active is not None and (active == change.execution_id or (
                    change.execution_id is None and execution_tasks[active] == change.task_id))
                completed_match = completed is not None and (completed == change.execution_id or (
                    change.execution_id is None and execution_tasks[completed] == change.task_id))
                if not beginning and change.current_status in {"paused", "blocked"} and change.execution_id is not None:
                    apply(key, "add", change.execution_id)
                    apply(key, "check", None)
                    pending_checks = []
                if active_match:
                    active = None
                if completed_match:
                    completed = None
                if active_match or completed_match:
                    pending_checks = []
        if active is not None:
            gaps[active].update(thread_gaps)
    return TurnAllocation(active, dict(owners), dict(gaps), before, unknown, dict(actions))
