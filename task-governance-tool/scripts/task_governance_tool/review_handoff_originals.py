"""Exact physical originals with their own Packet and machine sidecar.

Bytes stay in caller-owned ignored files. Returned references are not permission
to register, rewrite originals, resolve Findings, or infer review PASS.
"""

from dataclasses import dataclass, field
import hashlib
import json
import os

from . import review_continuation as values
from . import review_handoff_transport as files, review_session_transport as transport
from .review_results import REVIEW_RESULTS_INPUT_LIMIT, REVIEW_RESULTS_RECEIPT_LIMIT
from .state_paths import path_lexically_exists


@dataclass(frozen=True)
class SavedOriginal:
    packet_path: str
    result_path: str
    raw: bytes = field(repr=False)
    metadata: dict
    sidecar_raw: bytes = field(repr=False)
    bound: transport.BoundResult
    verdict: str
    collected_metadata: dict | None = None


def read_original(repo, packet_path, result_path, *, approvals=(), preparation=None, current=True):
    packet_file = files.checked_path(repo, packet_path)
    packet_raw = files.read_bounded(packet_file, files.PACKET_LIMIT)
    packet = files.decode_packet(packet_raw)
    if "review_session_context" not in packet:
        files.fail("review_continuation_unavailable")
    collection = collection_location(repo, packet_path, packet_raw, packet, preparation=preparation, current=current)
    if collection is not None:
        if approvals:
            files.fail()
        resolution, root, bases = collection
        from .review_answer_handoff import service_for
        service = service_for(repo, resolution, root, bases[0], current=current, retained=True)
        if result_path not in service.original_paths:
            files.fail("review_continuation_conflict")
        slot = service.original_paths.index(result_path) + 1
        raw, metadata = service.readback(slot)
        from .review_answer import decode_answer
        from .review_answer_transport import CollectedItem
        item = CollectedItem(slot, raw, metadata, decode_answer(raw))
        sealed = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return SavedOriginal(packet_path, result_path, raw, metadata, sealed,
            transport.BoundResult(item.target, item.session_binding), item.answer.payload["verdict"], metadata)
    result_file = files.checked_path(repo, result_path)
    raw = files.read_bounded(result_file, REVIEW_RESULTS_INPUT_LIMIT)
    normalized = files.validate_results(raw, packet, approvals, original=True)
    sidecar_file = files.checked_path(repo, result_path + ".session.json")
    sidecar_raw = files.read_bounded(sidecar_file, files.PACKET_LIMIT)
    metadata = json.loads(sidecar_raw.decode("utf-8"), object_pairs_hook=files.unique_object)
    bound = transport.validate_packet_metadata(packet, metadata, raw)
    if (files.read_bounded(packet_file, files.PACKET_LIMIT) != packet_raw
        or files.read_bounded(result_file, REVIEW_RESULTS_INPUT_LIMIT) != raw
        or files.read_bounded(sidecar_file, files.PACKET_LIMIT) != sidecar_raw):
        files.fail("handoff_file_changed")
    return SavedOriginal(packet_path, result_path, raw, metadata, sidecar_raw, bound, normalized["receipts"][0]["verdict"])


def collection_location(repo, packet_path, packet_raw, packet, *, preparation=None, current=True):
    if packet.get("review_session_context", {}).get("version") == 2:
        from .review_answer_continuation_preparation import locate_continuation
        return locate_continuation(repo, packet_path, packet_raw, packet, admitted=preparation, current=current)
    if preparation is not None:
        resolution, root = preparation
        if root.packet_path != packet_path or root.packet_digest != hashlib.sha256(packet_raw).hexdigest():
            files.fail("review_packet_stale")
        return (resolution, root, root.collection_bases) if root.version == 2 else None
    from .review_answer_preparation import locate
    found = locate(repo, packet_path, packet_raw, packet)
    if found is None:
        return None
    resolution, root = found
    return resolution, root, root.collection_bases


def same_original(first, second):
    return (first.packet_path, first.result_path, hashlib.sha256(first.raw).digest(), first.sidecar_raw) == (
        second.packet_path, second.result_path, hashlib.sha256(second.raw).digest(), second.sidecar_raw)


def read_sources(repo, root, transitions, *, resolution=None):
    """Read saved originals from admitted locations, without discovering files.

    The caller supplies validated structural records under its own admission.
    A sidecar without its original is interrupted-save residue, never absence.
    No lease, repository query, state change or review judgment is performed.
    """
    sources = []
    locations = [(root.packet_path, path) for path in root.result_paths]
    for row in transitions:
        if row.preparation_id == root.preparation_id and row.phase in ("prepared", "published"):
            locations.extend((row.packet_path, path) for path in row.result_paths)
    for packet, result in locations:
        path = files.relative_path(repo, result)
        if path_lexically_exists(path):
            sources.append(read_original(repo, packet, result,
                preparation=(resolution, root) if resolution is not None else None))
        elif path_lexically_exists(files.relative_path(repo, result + ".session.json")):
            files.fail("handoff_file_changed")
    return sources


def compose_inventory(repo, root, record, state, sources, packet_path, originals, approvals=(), *, resolution=None):
    """Frame complete selected/captured/retained originals for either route.

    Callers supply the same validated native snapshot and own their distinct
    admission/renewal boundaries. This function takes no lease or SQLite
    connection, grants no effect authority, and uses no completion judgment.
    Exact original bytes and sidecars remain attached to their own Packet.
    """
    selected = [read_original(repo, packet_path, path, approvals=approvals,
        preparation=(resolution, root) if resolution is not None else None) for path in originals]
    if (any(source.collected_metadata is not None for source in (*selected, *sources))
        or getattr(state, "collected_captures", {}).get(record.continuation_id)):
        from .review_answer_originals import compose_collected_inventory
        return compose_collected_inventory(repo, root, record, state, sources, selected)
    if tuple(source.bound.binding.session_id for source in selected) != tuple(choice.session_id for choice in record.selected):
        files.fail("review_continuation_conflict")
    captures = tuple(values.RetainedOriginal(**{key: row[key] for key in values.RetainedOriginal.__dataclass_fields__})
        for row in state.captures[record.continuation_id])
    needed = {capture.session_id: capture for capture in captures}
    selected_actors = {choice.session_id for choice in record.selected}
    registered_actors = {node.session_id for node in state.heads}
    retained = []
    for source in sources:
        actor = source.bound.binding.session_id
        if actor in selected_actors:
            continue
        capture = needed.get(actor)
        if capture is not None:
            if (source.bound.continuation is not None and source.bound.continuation.identity.continuation_id == capture.prior_continuation_id
                and source.bound.binding.original_result_digest == capture.original_result_digest
                and transport.metadata_digest(source.metadata) == capture.metadata_digest):
                retained.append(source)
                del needed[actor]
        elif actor not in registered_actors:
            if source.bound.continuation is not None or actor in {row.bound.binding.session_id for row in retained}:
                files.fail("review_continuation_conflict")
            retained.append(source)
    if needed:
        files.fail("review_continuation_original_registration_required")
    supplied = [*retained, *selected]
    actors = {source.bound.binding.session_id for source in supplied}
    ids = tuple(sorted(node.receipt_id for node in state.heads if node.session_id not in selected_actors and node.session_id not in actors))
    if len(supplied) + len(ids) < len(root.result_paths):
        files.fail("review_continuation_original_registration_required")
    identity = transport.ContinuationIdentity(record.preparation_id, record.continuation_id, record.sequence)
    framed = transport.frame_submission([source.raw for source in supplied], [source.metadata for source in supplied],
        continuation=identity, retained_receipt_ids=ids, retained_originals=captures)
    return framed, tuple(supplied), ids


def submission(repo, packet_path, originals, approvals=()):
    """Confirm the complete saved handoff before any registration dispatch.

    The caller need not separately retrieve save acknowledgements or originals.
    This observes the files now; it does not infer a past save/child outcome.
    """
    if not 1 <= len(originals) <= REVIEW_RESULTS_RECEIPT_LIMIT:
        files.fail()
    packet_file = files.checked_path(repo, packet_path)
    packet_raw = files.read_bounded(packet_file, files.PACKET_LIMIT)
    packet = files.decode_packet(packet_raw)
    if packet.get("review_session_context", {}).get("version") == 2:
        from task_governance_tool.review_handoff_continuation import assemble_submission
        return assemble_submission(repo, packet_path, originals, approvals)
    from .review_answer_preparation import locate
    collection = locate(repo, packet_path, packet_raw, packet)
    if collection is not None:
        if approvals:
            files.fail()
        from .review_answer_handoff import initial_submission
        resolution, root = collection
        return initial_submission(repo, root, resolution, originals)
    paths = [files.checked_path(repo, item) for item in originals]
    if len(set(os.path.normcase(str(path)) for path in paths)) != len(paths):
        files.fail()
    documents = [files.read_bounded(path, REVIEW_RESULTS_INPUT_LIMIT) for path in paths]
    if any(not raw.lstrip().startswith(b"{") for raw in documents):
        files.fail()
    framed = b"[" + b",".join(documents) + b"]"
    payload = files.validate_results(framed, packet, approvals)
    binding_files = [(files.checked_path if "review_session_context" in packet else files.relative_path)(
        repo, item + ".session.json") for item in originals]
    binding_documents = []
    if "review_session_context" in packet:
        for path, raw in zip(binding_files, documents):
            binding_raw = files.read_bounded(path, files.PACKET_LIMIT)
            metadata = json.loads(binding_raw.decode("utf-8"), object_pairs_hook=files.unique_object)
            transport.validate_metadata(metadata, raw)
            context = {key: metadata[key] for key in ("version", "project_id", "execution_id")}
            if context != packet["review_session_context"]:
                files.fail("review_target_mismatch")
            binding_documents.append((binding_raw, metadata))
        framed = transport.frame_submission(documents, [metadata for _, metadata in binding_documents])
    elif any(os.path.lexists(path) for path in binding_files):
        files.fail("handoff_invalid_input")
    if files.read_bounded(packet_file, files.PACKET_LIMIT) != packet_raw:
        files.fail("handoff_file_changed")
    for path, raw in zip(paths, documents):
        if files.read_bounded(path, REVIEW_RESULTS_INPUT_LIMIT) != raw:
            files.fail("handoff_file_changed")
    for path, (raw, _) in zip(binding_files, binding_documents):
        if files.read_bounded(path, files.PACKET_LIMIT) != raw:
            files.fail("handoff_file_changed")
    return payload["task_id"], framed
