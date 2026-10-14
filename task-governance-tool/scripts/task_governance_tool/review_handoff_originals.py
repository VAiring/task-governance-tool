"""Exact physical originals with their own Packet and machine sidecar.

Bytes stay in caller-owned ignored files. Returned references are not permission
to register, rewrite originals, resolve Findings, or infer review PASS.
"""

from dataclasses import dataclass, field
import hashlib
import json

from . import review_continuation as values
from . import review_handoff as files, review_session_transport as transport
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


def read_original(repo, packet_path, result_path, *, approvals=()):
    packet_file = files._path(repo, packet_path)
    packet_raw = files._read(packet_file, files.PACKET_LIMIT)
    packet = files._packet(packet_raw)
    if "review_session_context" not in packet:
        files._fail("review_continuation_unavailable")
    result_file = files._path(repo, result_path)
    raw = files._read(result_file, files.REVIEW_RESULTS_INPUT_LIMIT)
    normalized = files._validate(raw, packet, approvals, original=True)
    sidecar_file = files._path(repo, result_path + ".session.json")
    sidecar_raw = files._read(sidecar_file, files.PACKET_LIMIT)
    metadata = json.loads(sidecar_raw.decode("utf-8"), object_pairs_hook=files._unique)
    bound = transport.validate_packet_metadata(packet, metadata, raw)
    if (files._read(packet_file, files.PACKET_LIMIT) != packet_raw
        or files._read(result_file, files.REVIEW_RESULTS_INPUT_LIMIT) != raw
        or files._read(sidecar_file, files.PACKET_LIMIT) != sidecar_raw):
        files._fail("handoff_file_changed")
    return SavedOriginal(packet_path, result_path, raw, metadata, sidecar_raw, bound, normalized["receipts"][0]["verdict"])


def same_original(first, second):
    return (first.packet_path, first.result_path, hashlib.sha256(first.raw).digest(), first.sidecar_raw) == (
        second.packet_path, second.result_path, hashlib.sha256(second.raw).digest(), second.sidecar_raw)


def read_sources(repo, root, transitions):
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
        path = files._relative_path(repo, result)
        if path_lexically_exists(path):
            sources.append(read_original(repo, packet, result))
        elif path_lexically_exists(files._relative_path(repo, result + ".session.json")):
            files._fail("handoff_file_changed")
    return sources


def compose_inventory(repo, root, record, state, sources, packet_path, originals, approvals=()):
    """Frame complete selected/captured/retained originals for either route.

    Callers supply the same validated native snapshot and own their distinct
    admission/renewal boundaries. This function takes no lease or SQLite
    connection, grants no effect authority, and uses no completion judgment.
    Exact original bytes and sidecars remain attached to their own Packet.
    """
    selected = [read_original(repo, packet_path, path, approvals=approvals) for path in originals]
    if tuple(source.bound.binding.session_id for source in selected) != tuple(choice.session_id for choice in record.selected):
        files._fail("review_continuation_conflict")
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
                files._fail("review_continuation_conflict")
            retained.append(source)
    if needed:
        files._fail("review_continuation_original_registration_required")
    supplied = [*retained, *selected]
    actors = {source.bound.binding.session_id for source in supplied}
    ids = tuple(sorted(node.receipt_id for node in state.heads if node.session_id not in selected_actors and node.session_id not in actors))
    if len(supplied) + len(ids) < len(root.result_paths):
        files._fail("review_continuation_original_registration_required")
    identity = transport.ContinuationIdentity(record.preparation_id, record.continuation_id, record.sequence)
    framed = transport.frame_submission([source.raw for source in supplied], [source.metadata for source in supplied],
        continuation=identity, retained_receipt_ids=ids, retained_originals=captures)
    return framed, tuple(supplied), ids
