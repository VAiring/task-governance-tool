"""Compose exact mixed-protocol continuation originals, without effects."""

from hashlib import sha256

from . import review_handoff_transport as files
from . import review_session_transport as legacy
from .review_answer import decode_answer
from .review_answer_values import metadata_digest
from .review_answer_transport import CollectedItem, LegacyItem, frame_collected_submission
from .review_continuation import RetainedOriginal, selection_digest


def compose_collected_inventory(repo, root, record, state, sources, selected):
    if tuple(source.bound.binding.session_id for source in selected) != tuple(row.session_id for row in record.selected):
        files.fail("review_continuation_conflict")
    selected_ids = {row.session_id for row in record.selected}
    registered = {row.session_id for row in state.heads}
    legacy_captures = tuple(RetainedOriginal(**{key: row[key] for key in RetainedOriginal.__dataclass_fields__})
        for row in state.captures[record.continuation_id])
    native = state.collected_captures.get(record.continuation_id, ())
    collected_captures = tuple(capture for capture, _metadata in native)
    needed = {row.session_id: row for row in legacy_captures}
    needed_collected = {row["session_id"]: row for row in collected_captures}
    retained = []
    for source in sources:
        actor = source.bound.binding.session_id
        if actor in selected_ids or actor in registered:
            continue
        meta = source.collected_metadata
        capture = needed_collected.get(actor) if meta is not None else needed.get(actor)
        if capture is None:
            files.fail("review_continuation_original_registration_required")
        if meta is not None:
            if (capture["original_digest"] != meta["original"]["digest"]
                or capture["metadata_digest"] != metadata_digest(meta)
                or capture["source_basis_digest"] != meta["binding"]["basis_digest"]):
                files.fail("review_continuation_conflict")
            del needed_collected[actor]
        else:
            if (source.bound.continuation is None
                or source.bound.continuation.identity.continuation_id != capture.prior_continuation_id
                or source.bound.binding.original_result_digest != capture.original_result_digest
                or legacy.metadata_digest(source.metadata) != capture.metadata_digest):
                files.fail("review_continuation_conflict")
            del needed[actor]
        retained.append(source)
    if needed or needed_collected:
        files.fail("review_continuation_original_registration_required")
    retained.sort(key=lambda row: row.bound.binding.session_id)
    supplied = tuple([*selected, *retained])
    actors = {row.bound.binding.session_id for row in supplied}
    ids = tuple(sorted(row.receipt_id for row in state.heads if row.session_id not in selected_ids | actors))
    if len(supplied) + len(ids) != len(root.result_paths):
        files.fail("review_continuation_original_registration_required")
    items = []
    for slot, source in enumerate(supplied, 1):
        if source.collected_metadata is not None:
            items.append(CollectedItem(slot, source.raw, source.collected_metadata, decode_answer(source.raw)))
        else:
            if source in selected:
                files.fail("review_continuation_conflict")
            packet = files.read_bounded(files.checked_path(repo, source.packet_path), files.PACKET_LIMIT)
            items.append(LegacyItem(slot, source.raw, "sha256:" + sha256(packet).hexdigest(), source.metadata, source.bound))
    captures = sorted((*legacy_captures, *collected_captures),
        key=lambda row: row.session_id if type(row) is RetainedOriginal else row["session_id"])
    continuation = dict(continuation_id=record.continuation_id, sequence=record.sequence,
        selection_digest=selection_digest(record.selected))
    framed = frame_collected_submission(record.preparation_id, items, continuation=continuation,
        retained_receipt_ids=ids, retained_originals=captures)
    return framed, supplied, ids
