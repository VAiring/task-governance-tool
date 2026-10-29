"""Response-key attribution and exact owner-set partitions; no I/O or gates.

Record order is local to an admitted source incarnation. Sources are combined
only when their response-key ordering constraints have one consistent order.
An uncertain endpoint never becomes a guessed interval or measured zero.
"""

from collections import defaultdict

from task_governance_tool.usage_attribution import _models, _coverage
from task_governance_tool.usage_response_ownership import allocate_turn, apply_actions


ALGORITHM = "response-partitions-v6"


def response_order(positions, turns, conflicting_turns=frozenset()):
    starts, conflicts, by_turn, edges = {}, set(conflicting_turns), defaultdict(set), defaultdict(set)
    orders = {}
    for turn in turns:
        key, clock = (turn.thread_id, turn.turn_id), (turn.thread_id, turn.started_at)
        if key in starts and starts[key] != turn.started_at:
            conflicts.add(key)
        if clock in orders and orders[clock] != key:
            conflicts.update((key, orders[clock]))
        starts[key], orders[clock] = turn.started_at, key
    lanes = defaultdict(list)
    for source, incarnation, item in positions:
        key = item.provider, item.response_id
        turn = item.thread_id, item.turn_id
        by_turn[turn].add(key)
        lanes[source, incarnation, *turn].append((item.record_order, key))
    for lane, values in lanes.items():
        at = defaultdict(set)
        for order, key in values:
            at[order].add(key)
        if any(len(keys) != 1 for keys in at.values()):
            conflicts.add(lane[2:])
            continue
        chain = [next(iter(at[order])) for order in sorted(at)]
        # Identical numerical replay is not another response/order boundary.
        chain = list(dict.fromkeys(chain))
        for left, right in zip(chain, chain[1:]):
            edges[lane[2:]].add((left, right))
    ordered, unknown = {}, set()
    for turn, keys in by_turn.items():
        remaining, result = set(keys), []
        if turn not in starts or turn in conflicts:
            unknown.update(keys)
            continue
        incoming, successors = dict.fromkeys(keys, 0), defaultdict(set)
        for left, right in edges[turn]:
            successors[left].add(right)
            incoming[right] += 1
        roots = {key for key, count in incoming.items() if count == 0}
        while remaining:
            if len(roots) != 1:
                unknown.update(keys)
                break
            current = roots.pop()
            result.append(current)
            remaining.remove(current)
            for next_key in successors[current]:
                incoming[next_key] -= 1
                if incoming[next_key] == 0:
                    roots.add(next_key)
        else:
            ordered[turn] = result
    return starts, ordered, unknown


def project_responses(transitions, bindings, checks, positions, turns, responses, *,
                      additional=(), conflicting_responses=frozenset(), thread_diagnostics=None,
                      conflicting_turns=frozenset(), unresolved=False, geometry_only=False, pending=(), fallback=(),
                      fallback_origins=()):
    """Every original key is exclusive, shared, unassigned, or unresolved.

    Successful stops include their response; resumed intervals retain the same
    execution and adopt only eligible preparation in the original turn.
    A shared response does not merge the owners' other exclusive responses.
    """
    starts, ordered, unknown = response_order(positions, turns, conflicting_turns)
    unique, conflicts = {}, set(conflicting_responses)
    for row in responses:
        key = row.provider, row.response_id
        if key in unique and unique[key] != row:
            conflicts.add(key)
        else:
            unique[key] = row
    good = {key: row for key, row in unique.items() if key not in conflicts}
    unknown.update(set(good) - {key for keys in ordered.values() for key in keys})
    owners, execution_gaps = defaultdict(set), defaultdict(set)
    events = defaultdict(list)
    executions = {}
    valid_boundaries, boundary_turns, affected_executions, previous_tasks = {}, {}, {}, {}
    for index, change in enumerate(transitions):
        previous = previous_tasks.get(change.task_id)
        affected = change.execution_id
        if (affected is None and change.policy_version == 1
                and change.current_status in {"ready", "cancelled"} and previous is not None
                and previous.policy_version == 1 and previous.current_status == change.previous_status):
            # Core deliberately clears the execution link on release. Its exact
            # preceding same-Task transition still identifies the ended execution.
            affected = previous.execution_id
        affected_executions[index] = affected
        previous_tasks[change.task_id] = change
        if change.execution_id is not None:
            executions[change.execution_id] = change
        boundary = bindings.get(change.transition_id)
        if boundary is None or boundary.response_id is None:
            continue
        key = boundary.provider, boundary.response_id
        if boundary.thread_id != change.actor_session_id or (key in unique and
                (unique[key].thread_id, unique[key].turn_id) != (boundary.thread_id, boundary.turn_id)):
            unknown.add(key)
            continue
        if change.policy_version != 1:
            unknown.add(key)
            continue
        valid_boundaries[index] = key
        boundary_turns[index] = boundary.turn_id
        events[key].append((index, change))
    check_keys = {(item.provider, item.response_id) for item in checks if item.response_id is not None}
    per_thread = defaultdict(list)
    for turn, keys in ordered.items():
        per_thread[turn[0]].append((starts[turn], turn[1], keys))
    # A validated actual parent receipt proves its receiving turn, including
    # automatic completion whose owner-command turn is unavailable. It does
    # not borrow a send/accepted turn or displace another active execution.
    for index, change in enumerate(transitions):
        if change.current_status != "done":
            continue
        receipts = {interval.start_turn for interval in additional if
                    interval.execution_id == change.execution_id and interval.thread_id == change.actor_session_id
                    and interval.start_turn is not None and interval.start_turn == interval.end_turn
                    and not interval.diagnostics}
        if len(receipts) == 1:
            receipt_turn = next(iter(receipts))
            sequence = ordered.get((change.actor_session_id, receipt_turn), [])
            if sequence:
                receipt_key = sequence[0]
                if index not in valid_boundaries:
                    valid_boundaries[index] = receipt_key
                    boundary_turns[index] = receipt_turn
                if (index, change) not in events[receipt_key]:
                    events[receipt_key].append((index, change))
    # Core order proves ownership release across actors, but does not order
    # their original numerical responses. Preserve a definite old endpoint,
    # fence its possible continuation, and wait for a local bound transition.
    held, displaced = {}, []
    for index, change in enumerate(transitions):
        if change.policy_version != 1:
            continue
        if change.current_status in {"in_progress", "review_pending"} and change.execution_id is not None:
            held[change.task_id] = (index, change.execution_id, change.actor_session_id)
        elif change.current_status in {"paused", "blocked", "ready", "cancelled", "done"}:
            previous = held.pop(change.task_id, None)
            if previous is not None and previous[2] is not None and previous[2] != change.actor_session_id:
                displaced.append((index, change.task_id, *previous))
    uncertain_stops, thread_indices, pending_origins = [], {}, defaultdict(list)
    def is_beginning(change):
        return change.current_status == "in_progress" and change.previous_status not in {"in_progress", "review_pending"}

    for thread, timelines in per_thread.items():
        keys = [key for _, _, sequence in sorted(timelines) for key in sequence]
        indices = {key: index for index, key in enumerate(keys)}
        thread_indices[thread] = indices
        changes = [(index, change) for index, change in enumerate(transitions) if change.actor_session_id == thread]
        located = [(index, change, indices[valid_boundaries[index]]) for index, change in changes
                   if valid_boundaries.get(index) in indices]
        for _release, task_id, previous_index, execution, former_actor in displaced:
            if former_actor != thread:
                continue
            before = [index for index, _, _ in located if index <= previous_index]
            first = (indices[valid_boundaries[max(before)]] + int(max(before) == previous_index)) if before else 0
            after = [index for index, change, _ in located if index > previous_index and (
                change.task_id == task_id or (change.current_status == "in_progress"
                    and change.previous_status not in {"in_progress", "review_pending"}))]
            last = indices[valid_boundaries[min(after)]] if after else len(keys)
            affected = keys[first:last]
            if affected:
                unknown.update(affected)
                execution_gaps[execution].add("response_binding_unknown")
        for ordinal, (left, right) in enumerate(zip(located, located[1:])):
            if left[2] <= right[2]:
                continue
            first = located[ordinal - 1][2] if ordinal else 0
            later = [position for _, _, position in located[ordinal + 2:] if position > max(left[2], right[2])]
            last = later[0] if later else len(keys)
            unknown.update(keys[first:last])
            for _, change, _ in (left, right):
                if change.execution_id is not None:
                    execution_gaps[change.execution_id].add("response_order_unknown")
        for source, incarnation, origin_thread, origin_turn, provider, response_id, order in pending:
            if origin_thread != thread:
                continue
            origin_key = provider, response_id
            same_turn = origin_key in good and (good[origin_key].thread_id, good[origin_key].turn_id) == (thread, origin_turn)
            first = indices.get(origin_key) if same_turn else None
            if first is None:
                clock = starts.get((thread, origin_turn))
                before = [indices[item.provider, item.response_id] for sid, inc, item in positions
                          if (sid, inc, item.thread_id, item.turn_id) == (source, incarnation, thread, origin_turn)
                          and item.record_order < order and (item.provider, item.response_id) in indices]
                lower = max(before) if before else -1
                candidates = [indices[item.provider, item.response_id] for sid, inc, item in positions
                    if item.thread_id == thread and (item.provider, item.response_id) in indices
                    and indices[item.provider, item.response_id] > lower
                    and (item.turn_id == origin_turn or (clock is not None
                         and starts.get((thread, item.turn_id), -1) >= clock))
                    and ((sid, inc) != (source, incarnation) or item.record_order >= order)]
                if not candidates:
                    continue
                first = min(candidates)
            pending_origins[thread].append(first)
        for index, change in changes:
            execution = affected_executions[index]
            # A qualified operation/response relation need not be placeable in
            # the current ordering graph (for example, a conflicting turn).
            if valid_boundaries.get(index) in indices or execution is None:
                continue
            applicable = [(item, other) for item, other in changes
                          if other.task_id == change.task_id or is_beginning(other)]
            before = [item for item, _ in applicable if item < index and valid_boundaries.get(item) in indices]
            after = [item for item, _ in applicable if item > index and valid_boundaries.get(item) in indices]
            first = indices[valid_boundaries[max(before)]] if before else 0
            last = indices[valid_boundaries[min(after)]] if after else len(keys)
            if before and not is_beginning(change):
                first += 1
            affected = keys[first:last]
            if change.current_status in {"paused", "blocked", "ready", "cancelled"}:
                uncertain_stops.append((execution, affected))
            else:
                unknown.update(affected)
                execution_gaps[execution].add("response_binding_unknown")
    execution_tasks = {identity: change.task_id for identity, change in executions.items()}
    for thread, timelines in per_thread.items():
        active, state_before = None, {}
        for _, turn, keys in sorted(timelines):
            allocated = allocate_turn(active, keys, events, check_keys, execution_tasks,
                                      (thread_diagnostics or {}).get(thread, ()))
            active = allocated.active
            state_before.update(allocated.state_before)
            for key, actions in allocated.actions.items():
                if apply_actions(owners[key], actions):
                    unknown.add(key)
            for execution, gaps in allocated.gaps.items():
                execution_gaps[execution].update(gaps)

        keys = [key for _, _, sequence in sorted(timelines) for key in sequence]
        for first in pending_origins[thread]:
            # A call with no terminal result could change the carried state.
            # An organization release for a different Task does not re-establish
            # that state. Do not infer the pending call's Task from its body.
            affected = state_before[keys[first]] | owners[keys[first]]
            last = len(keys)
            for position in range(first + 1, len(keys)):
                row_events = events[keys[position]]
                reset = {affected_executions[index] for index, change in row_events
                         if change.current_status in {"in_progress", "review_pending"}
                         or change.previous_status in {"in_progress", "review_pending"}}
                if any(is_beginning(change) for _, change in row_events) or (affected and affected <= reset):
                    last = position
                    break
            unknown.update(keys[first:last])

    # An unplaceable stop cannot prove that the old active execution continued.
    # It also cannot invalidate another Task's independently allocated work or
    # completion/preparation tail merely because an organization release is missing.
    for execution, candidates in uncertain_stops:
        affected = {key for key in candidates if execution in owners[key]}
        unknown.update(affected)
        if affected:
            execution_gaps[execution].add("response_binding_unknown")

    # Retained turn-only history preserves observed partial totals. This is
    # deliberately restricted to unknown keys and never proves exclusive cost.
    for ordinal, interval in enumerate(fallback):
        covered, gaps = _coverage(interval, starts)
        candidates = {key for key, row in good.items() if key in unknown
                      and (row.thread_id, row.turn_id) in covered}
        indices = thread_indices.get(interval.thread_id, {})
        origins = fallback_origins[ordinal] if ordinal < len(fallback_origins) else None
        anchors = [(change, valid_boundaries[index], boundary_turns[index]) for index, change in enumerate(transitions)
                   if affected_executions[index] == interval.execution_id
                   and change.task_id == interval.task_id and change.actor_session_id == interval.thread_id
                   and valid_boundaries.get(index) in indices]
        entry = [indices[key] for change, key, boundary_turn in anchors if is_beginning(change)
                 and boundary_turn == interval.start_turn
                 and (origins is None or change.transition_id == origins[0])]
        exits = [indices[key] for change, key, boundary_turn in anchors if change.current_status in
                 {"paused", "blocked", "ready", "cancelled", "done"}
                 and boundary_turn == interval.end_turn
                 and (origins is None or change.transition_id == origins[1])]
        # Turn-only fallback cannot override an independently located origin or
        # exit, or attach another Task to an ordered response already allocated
        # to a known execution. Unordered keys retain potential partial owners.
        candidates = {key for key in candidates if key not in indices or interval.execution_id in owners[key]
                      or (not (entry and owners[key])
                          and not (entry and indices[key] < min(entry))
                          and not (exits and indices[key] > max(exits)))}
        for key in candidates:
            owners[key].add(interval.execution_id)
        if candidates:
            execution_gaps[interval.execution_id].update(gaps | set(interval.diagnostics))
    # Independent reviewer/receipt relations remain valid even if owner-command
    # observations are incomplete. Their response keys are unioned, never added.
    for interval in additional:
        covered, gaps = _coverage(interval, starts)
        execution_gaps[interval.execution_id].update(gaps | set(interval.diagnostics))
        if interval.source_admitted:
            execution_gaps[interval.execution_id].update((thread_diagnostics or {}).get(interval.thread_id, ()))
        owner = executions.get(interval.execution_id)
        parent_receipt = (owner is not None and owner.current_status == "done"
                          and owner.actor_session_id == interval.thread_id and interval.start_turn == interval.end_turn
                          and interval.start_turn is not None)
        trimmed = None
        if parent_receipt:
            sequence = ordered.get((interval.thread_id, interval.start_turn), [])
            beginning_executions = {change.execution_id for key in sequence for _, change in events[key]
                                   if change.current_status == "in_progress"
                                   and change.previous_status not in {"in_progress", "review_pending"}}
            cutoff = next((index for index, key in enumerate(sequence) if owners[key] & beginning_executions), len(sequence))
            trimmed = set(sequence[:cutoff])
        for key, row in good.items():
            if (row.thread_id, row.turn_id) in covered and (trimmed is None or key in trimmed):
                owners[key].add(interval.execution_id)
                if interval.identity_only:
                    # Definite review boundary membership does not prove a
                    # complete ordered interval. Keep its partial
                    # classification independent of the stored first clock.
                    unknown.add(key)
    # Qualified operation/receipt boundaries identify an admitted source even
    # when this response's counters are missing or conflicting. Require final
    # ownership; diagnostic propagation never creates numerical membership.
    for index, key in valid_boundaries.items():
        execution = affected_executions[index]
        if execution in owners.get(key, ()):
            execution_gaps[execution].update((thread_diagnostics or {}).get(transitions[index].actor_session_id, ()))
    groups = defaultdict(set)
    for key, members in owners.items():
        if key in good:
            # Allocation is final only after preparation, fallback and added
            # participation. A same-response stop must not erase source gaps.
            for member in members:
                execution_gaps[member].update((thread_diagnostics or {}).get(good[key].thread_id, ()))
        else:
            for member in members:
                execution_gaps[member].add("response_conflict" if key in conflicts else "usage_pending")
    for key, members in owners.items():
        if key not in good or not members:
            continue
        assignment = "partial" if key in unknown else "exclusive" if len(members) == 1 else "shared"
        groups[assignment, tuple(sorted(members))].add(key)
    known = {execution for (_, members) in groups for execution in members}
    for execution in executions.keys() - known:
        groups["partial", (execution,)] = set()
        execution_gaps[execution].update({"usage_pending", "response_binding_unknown"})
    components = []
    for (assignment, members), keys in sorted(groups.items()):
        gaps = set().union(*(execution_gaps[member] for member in members))
        if assignment == "partial":
            gaps.add("response_binding_unknown")
        if unresolved:
            gaps.add("operation_unbound")
        covered = sorted({(good[key].thread_id, good[key].turn_id) for key in keys})
        components.append({"executions": list(members), "assignment": assignment, "algorithm": ALGORITHM,
                           "turns": covered, "response_keys": sorted(keys), "diagnostics": sorted(gaps),
                           "models": [] if geometry_only else _models(good[key] for key in keys)})
    assigned = {key for component in components for key in component["response_keys"]}
    return {"components": components, "unknown_response_keys": sorted(set(good) & unknown),
            "unassigned_response_keys": sorted(set(good) - assigned - unknown),
            "unresolved_operations": int(unresolved)}
