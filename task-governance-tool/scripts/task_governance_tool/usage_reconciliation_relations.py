"""Indexed selected response relations plus complete historical origin closure."""

from dataclasses import dataclass

from .usage_evidence import encode
from .usage_response_adapter import ResponsePosition
from .usage_response_repository import _decode_boundary_relation
from .usage_values import UsageError


@dataclass(frozen=True)
class SelectedRelations:
    positions: tuple
    current: tuple
    history: tuple
    position_order: dict
    boundary_order: dict


_COLUMNS = "SELECT boundary.*,boundary.rowid AS relation_order,source.thread_id AS source_thread,source.incarnation AS current_incarnation "
_BOUNDARIES = (_COLUMNS + "FROM usage_response_boundaries boundary "
               "JOIN usage_sources source ON source.source_id=boundary.source_id WHERE ")
# Composite JSON-expression IN predicates can scan all boundaries despite the
# expression indexes. Drive equality probes from the small selected-key input.
_KEY_BOUNDARIES = (_COLUMNS + "FROM json_each(?) wanted CROSS JOIN usage_response_boundaries boundary "
                   "JOIN usage_sources source ON source.source_id=boundary.source_id WHERE ")


def read_selected_relations(connection, *, task_ids=(), turns=(), source_ids=(), response_keys=()):
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    task_ids, turns, source_ids = set(task_ids), set(map(tuple, turns)), set(source_ids)
    seeds = {}
    selectors = []
    if task_ids:
        selectors.append((_BOUNDARIES, "json_extract(boundary.document,'$.task_id') IN (SELECT value FROM json_each(?))", sorted(task_ids)))
    if turns:
        selectors.append((_KEY_BOUNDARIES, "json_extract(boundary.document,'$.thread_id')=json_extract(wanted.value,'$[0]') "
                          "AND json_extract(boundary.document,'$.turn_id')=json_extract(wanted.value,'$[1]')", sorted(turns)))
    if source_ids:
        selectors.append((_BOUNDARIES, "boundary.source_id IN (SELECT value FROM json_each(?))", sorted(source_ids)))
    if response_keys:
        selectors.append((_KEY_BOUNDARIES, "json_extract(boundary.document,'$.provider')=json_extract(wanted.value,'$[0]') "
                          "AND json_extract(boundary.document,'$.response_id')=json_extract(wanted.value,'$[1]')", sorted(set(response_keys))))
    for query, predicate, values in selectors:
        for row in connection.execute(query + "boundary.incarnation=source.incarnation AND " + predicate,
                                      (encode(values).decode(),)):
            seeds[row["source_id"], row["incarnation"], row["observation_id"]] = row
    decoded = {key: _decode_boundary_relation(row) for key, row in seeds.items()}
    current = tuple(value for _, value in sorted(decoded.items()))
    event_ids = {item.event_id for _, _, item in current if item.event_id is not None}
    calls = {(item.thread_id, item.call_id) for _, _, item in current}
    history = dict(seeds)
    closure = []
    if event_ids:
        closure.append((_BOUNDARIES, "json_extract(boundary.document,'$.event_id') IN (SELECT value FROM json_each(?))", sorted(event_ids)))
    if calls:
        closure.append((_KEY_BOUNDARIES, "json_extract(boundary.document,'$.thread_id')=json_extract(wanted.value,'$[0]') "
                        "AND json_extract(boundary.document,'$.call_id')=json_extract(wanted.value,'$[1]')", sorted(calls)))
    for query, predicate, values in closure:
        for row in connection.execute(query + predicate, (encode(values).decode(),)):
            history[row["source_id"], row["incarnation"], row["observation_id"]] = row
    # The history closure is validation-only. It must not admit old positions
    # or unrelated current calls as numerical owners of the selected region.
    for key, row in history.items():
        if key not in decoded:
            decoded[key] = _decode_boundary_relation(row)
    decoded_history = tuple(value for _, value in sorted(decoded.items()))
    positions, position_order = {}, {}
    position_selectors = []
    if turns:
        position_selectors.append(("(position.thread_id,position.turn_id) IN "
            "(SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]') FROM json_each(?))", sorted(turns)))
    if source_ids:
        position_selectors.append(("position.source_id IN (SELECT value FROM json_each(?))", sorted(source_ids)))
    keys = sorted({(source, incarnation, item.provider, item.response_id) for source, incarnation, item in current if item.response_id is not None})
    if keys:
        position_selectors.append(("(position.source_id,position.incarnation,position.provider,position.response_id) IN "
            "(SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]'),json_extract(value,'$[3]') FROM json_each(?))", keys))
    for predicate, values in position_selectors:
        for row in connection.execute("SELECT position.*,position.rowid AS relation_order,source.thread_id AS source_thread FROM usage_response_positions position "
                "JOIN usage_sources source ON source.source_id=position.source_id AND source.incarnation=position.incarnation WHERE " + predicate,
                (encode(values).decode(),)):
            item = ResponsePosition(**{field: row[field] for field in ResponsePosition.__dataclass_fields__})
            if item.thread_id != row["source_thread"]:
                raise UsageError("usage_schema_invalid")
            positions[row["source_id"], row["incarnation"], item.provider, item.response_id] = (row["source_id"], row["incarnation"], item)
            position_order[row["source_id"], row["incarnation"], item] = row["relation_order"]
    boundary_order = {}
    for key, row in seeds.items():
        source, incarnation, item = decoded[key]
        if item.response_id is None:
            # Keep the unavailable boundary in current/history for unresolved
            # attribution, but it cannot establish a response position.
            continue
        position = ResponsePosition(item.thread_id, item.turn_id, item.provider, item.response_id, item.record_order)
        identity = source, incarnation, position
        boundary_order[identity] = min(boundary_order.get(identity, row["relation_order"]), row["relation_order"])
    return SelectedRelations(tuple(value for _, value in sorted(positions.items())), current, decoded_history,
                             position_order, boundary_order)
