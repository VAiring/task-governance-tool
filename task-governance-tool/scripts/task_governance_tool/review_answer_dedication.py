"""Typed future reviewer dedication; no host, file, or database operations."""
from dataclasses import asdict, dataclass

from . import review_continuation as continuation
from .review_answer_values import prepare, require, session, identifier
from .task_values import validate_text, validation_error


@dataclass(frozen=True)
class LegacyDedication:
    session_id: str
    reviewer_key: str
    origin_continuation_id: str


def decode(value):
    schema = dict(version=int, assignments=[
        dict(session_id=str, reviewer_key=str, origin_continuation_id=str)])
    value = prepare(value, schema)
    require(value["version"] == 1 and 1 <= len(value["assignments"]) <= 8)
    rows = []
    for item in value["assignments"]:
        session(item["session_id"])
        identifier(item["origin_continuation_id"], "tg_review_continuation_")
        key = validate_text("reviewer_key", item["reviewer_key"], required=True, limit=500)
        require(key == item["reviewer_key"])
        rows.append(LegacyDedication(**item))
    require(len({row.session_id for row in rows}) == len(rows))
    require(len({row.reviewer_key for row in rows}) == len(rows))
    require(rows == sorted(rows, key=lambda row: row.session_id.encode("utf-8")))
    return tuple(rows)


def encode(rows):
    value = dict(version=1, assignments=[asdict(row) for row in rows])
    decode(value)
    return value


def _fail(*, unavailable=False):
    raise validation_error(
        "review_continuation_unavailable" if unavailable else "review_continuation_conflict",
        "reviewer dedication is unavailable" if unavailable else "reviewer dedication does not match")


def assignment():
    """No launch-request facts are inferred from historical review provenance."""
    return dict(kind="independent", review_profiles=[], review_lenses=[],
                requested_model=None, requested_effort=None, requested_skill=None,
                context_relation=None)


def saved(root, history, state):
    """Validate bounded same-preparation origins against native selections."""
    native = {row.continuation_id: row for row in state.records}
    known, keys = {}, {}
    for round_record in history:
        if round_record.preparation_id != root.preparation_id:
            continue
        declaration = round_record.legacy_migration
        if declaration is None:
            continue
        record = native.get(round_record.continuation_id)
        if (root.version != 1 or round_record.answer_protocol != "review-answer-v1"
            or record is None or record.preparation_id != root.preparation_id
            or record.basis != root.basis
            or round_record.basis_digest != continuation.basis_digest(root.basis)
            or round_record.selection_digest != continuation.selection_digest(record.selected)):
            _fail()
        rows = decode(declaration)
        if tuple(row.session_id for row in rows) != tuple(row.session_id for row in record.selected):
            _fail()
        for row in rows:
            if row.session_id == root.basis["parent_session_id"]:
                _fail()
            prior = known.get(row.session_id)
            if prior is None:
                if row.origin_continuation_id != round_record.continuation_id:
                    _fail()
                known[row.session_id] = row
            elif prior != row:
                _fail()
            if keys.setdefault(row.reviewer_key, row.session_id) != row.session_id:
                _fail()
    for head in state.heads:
        if head.session_id in known and known[head.session_id].reviewer_key != head.reviewer_key:
            _fail()
        if head.reviewer_key in keys and keys[head.reviewer_key] != head.session_id:
            _fail()
    return known


def issue(root, history, state, selected, continuation_id, *, explicit=False, sources=()):
    """Freeze a new declaration; never called to reinterpret an issued round."""
    if root.version != 1:
        if explicit:
            _fail(unavailable=True)
        return None
    known = saved(root, history, state)
    heads = {row.session_id: row for row in state.heads if row.session_id is not None}
    keys = {row.reviewer_key: row.session_id for row in state.heads}
    for source in sources:
        actor, key = source.bound.binding.session_id, source.reviewer_key
        if key is None or actor in heads and heads[actor].reviewer_key != key:
            _fail()
        if keys.setdefault(key, actor) != actor:
            _fail()
    rows = []
    for choice in selected:
        head = heads.get(choice.session_id)
        if ((choice.action == "reassess" and (
                head is None or head.receipt_id != choice.prior_receipt_id))
            or choice.action == "supplement" and head is not None):
            _fail()
        row = known.get(choice.session_id)
        if row is None:
            if not explicit:
                _fail(unavailable=True)
            row = LegacyDedication(choice.session_id,
                head.reviewer_key if head else choice.session_id, continuation_id)
        if head is not None and row.reviewer_key != head.reviewer_key:
            _fail()
        if keys.setdefault(row.reviewer_key, row.session_id) != row.session_id:
            _fail()
        rows.append(row)
    return encode(tuple(rows))


def current(root, history, state, transition):
    """Shared ready/arm eligibility, separate from the initial native proof."""
    if root.version != 1:
        if transition.legacy_migration is not None:
            _fail()
        return None
    known = saved(root, history, state)
    if transition.legacy_migration is None:
        _fail(unavailable=True)
    rows = decode(transition.legacy_migration)
    if any(known.get(row.session_id) != row for row in rows):
        _fail()
    return rows
