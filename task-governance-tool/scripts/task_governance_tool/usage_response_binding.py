"""Qualify response origins from exact selected core/numerical facts; no I/O."""

from dataclasses import dataclass, replace

from .usage_response_adapter import ResponsePosition, CHECKS
from .usage_turn_adapter import OperationObservation


@dataclass(frozen=True)
class QualifiedResponses:
    bindings: dict
    checks: tuple
    positions: tuple
    unresolved: frozenset
    anchors: tuple


def qualify_responses(project_id, positions, boundaries, historic, numerical, resolutions):
    """Historical origin collisions still qualify current-incarnation records.

    Selected callers must supply the complete event/call-origin closure, not
    merely historical rows whose response currently lies in their dirty turn.
    """
    origins, call_origins = {}, {}
    for _, _, item in historic:
        if item.origin_version == 2 and item.response_id is not None:
            origin = item.thread_id, item.turn_id, item.provider, item.response_id
            if item.event_id is not None:
                origins.setdefault(item.event_id, set()).add(origin)
            call_origins.setdefault((item.thread_id, item.call_id), set()).add(origin)
    by_event, checks, unresolved, anchored = {}, [], set(), []
    first_positions = {(source, incarnation, item.provider, item.response_id): item
                       for source, incarnation, item in positions}
    for source, incarnation, item in boundaries:
        if item.project_id != project_id:
            continue
        position = first_positions.get((source, incarnation, item.provider, item.response_id))
        same_numerical = numerical.get((item.provider, item.response_id), (item.thread_id, item.turn_id)) == (
            item.thread_id, item.turn_id)
        qualified = (item.origin_version == 2 and item.response_id is not None and same_numerical
            and len(origins.get(item.event_id, ())) <= 1
            and len(call_origins.get((item.thread_id, item.call_id), ())) <= 1 and (
            (position is None and item.method == "explicit") or (position is not None
             and position.thread_id == item.thread_id and position.turn_id == item.turn_id
             and position.record_order >= item.record_order)))
        if not qualified:
            item = replace(item, response_id=None, method="unavailable")
        if item.command in CHECKS:
            checks.append(item)
        else:
            operation = OperationObservation(item.thread_id, item.turn_id, item.project_id, item.task_id,
                                             item.event_id, item.generation, item.status)
            transition = resolutions[operation]
            if transition is None:
                unresolved.add(item.event_id)
                continue
            by_event.setdefault(transition, []).append(item)
        if item.response_id is not None:
            anchored.append((source, incarnation, ResponsePosition(item.thread_id, item.turn_id, item.provider,
                                                                   item.response_id, item.record_order)))
    bindings = {}
    for transition, items in by_event.items():
        identities = {(item.thread_id, item.turn_id, item.provider, item.response_id) for item in items}
        if len(identities) == 1 and next(iter(identities))[-1] is not None:
            bindings[transition] = items[0]
        else:
            unresolved.update(item.event_id for item in items)
    anchor_keys = {(source, incarnation, item.provider, item.response_id) for source, incarnation, item in anchored}
    current_positions = tuple(entry for entry in positions if (entry[0], entry[1], entry[2].provider, entry[2].response_id)
                              not in anchor_keys) + tuple(anchored)
    return QualifiedResponses(bindings, tuple(checks), current_positions, frozenset(unresolved), tuple(anchored))
