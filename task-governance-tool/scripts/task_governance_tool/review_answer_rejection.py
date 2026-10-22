"""Explicit rejected-answer retirement: files and leases, never core SQL.

Rejected bytes remain call-local and are never returned as SavedOriginals.
The continuation writer separately proves absence while these leases are held.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

from . import review_handoff_transport as files
from .review_answer import AnswerError, inspect_retirement_rejection
from .review_answer_collection import source as observation_source
from .review_answer_handoff import service_for
from .review_answer_values import validate_metadata, basis_digest, metadata_digest
from .review_answer_rejection_values import validate, matches
from .review_handoff_originals import read_original


def _unknown():
    files.fail("review_continuation_effect_unknown")


def _origin(root, history, continuation_id):
    if continuation_id is None:
        if root.version != 2 or any(b["version"] != 2 for b in root.collection_bases):
            _unknown()
        return root.packet_path, root.result_paths, root.collection_bases[0]
    rows = [row for row in history if row.preparation_id == root.preparation_id
            and row.continuation_id == continuation_id and row.phase in ("prepared", "published")]
    if len(rows) != 1 or rows[0].answer_protocol != "review-answer-v1":
        _unknown()
    row = rows[0]
    return row.packet_path, row.result_paths, None


def _native_replacement(root, row, state):
    from .review_continuation import selection_digest, basis_digest as native_digest
    records = [record for record in state.records if record.continuation_id == row.continuation_id]
    audit = validate(row.rejected_answer_replacement)
    if len(records) != 1:
        _unknown()
    record = records[0]
    if (record.basis != root.basis or record.preparation_id != root.preparation_id
        or record.sequence != row.continuation_sequence or record.predecessor_id != audit["source_continuation_id"]
        or row.basis_digest != native_digest(record.basis) or row.selection_digest != selection_digest(record.selected)
        or {s.session_id for s in record.selected} != {s["reviewer_session_id"] for s in audit["sources"] if s["rejection"] is not None}
        or any(s.action != "supplement" or s.prior_receipt_id is not None for s in record.selected)):
        _unknown()
    return audit


def retired_locations(root, history, state, *, repo, resolution):
    """Omit only native-admitted rejected sources; retain their physical files."""
    result = set()
    for row in history:
        if row.preparation_id != root.preparation_id or row.rejected_answer_replacement is None:
            continue
        for item in _native_replacement(root, row, state)["sources"]:
            if item["rejection"] is not None:
                packet, paths, basis = _origin(root, history, item["source_continuation_id"])
                if item["slot"] > len(paths):
                    _unknown()
                if basis is None:
                    from .review_answer_collection_repository import CollectionRepository
                    from .review_answer_continuation_preparation import collection_path, issued_bases
                    records = CollectionRepository(collection_path(resolution.paths, root, item["source_continuation_id"])).read()
                    origin = next(r for r in history if r.continuation_id == item["source_continuation_id"])
                    basis = issued_bases(root, origin, records)[0]
                service = service_for(repo, resolution, root, basis, retained=True)
                record = service.repository.read()[item["slot"]-1]
                service.check_basis(record["basis"], item["slot"], persist_invalidation=False)
                meta = _saved_metadata(service, item["slot"], record)
                raw = files.read_bounded(files.checked_path(repo, meta["original"]["path"]), 262144)
                if (not matches(item, meta) or record["registration"] != item["registration"]
                    or inspect_retirement_rejection(raw, meta["original"]) != item["rejection"]
                    or service.repository.read()[item["slot"]-1] != record):
                    _unknown()
                result.add((packet, paths[item["slot"] - 1]))
    return result


@dataclass(frozen=True)
class RejectionInventory:
    audit: dict
    healthy: tuple
    originals: tuple

    def captures(self, continuation_id, selected):
        result = []
        for slot, original in enumerate(self.healthy, len(selected) + 1):
            meta = original.collected_metadata
            context = meta["basis"]["continuation"]
            capture = dict(kind="collected", consumer_continuation_id=continuation_id,
                consumer_slot=slot, source_preparation_id=meta["basis"]["preparation_id"],
                source_continuation_id=context["continuation_id"] if context else None,
                source_slot=meta["binding"]["slot"], session_id=meta["binding"]["reviewer_session_id"],
                source_basis_digest=basis_digest(meta["basis"]), original_digest=meta["original"]["digest"],
                metadata_digest=metadata_digest(meta))
            result.append((capture, meta))
        return tuple(result)


def _saved_metadata(service, slot, record):
    if (record["version"] != 2 or record["tracking_state"] != "frozen"
            or record["binding"] is None or record["registration"]["receipt_id"] is not None):
        _unknown()
    attempt = record["attempts"][0]
    publication = attempt["publication"]
    if (publication is None or publication["state"] != "saved"
            or attempt["terminal"] != "completed" or attempt["failure"] is not None
            or publication["original"]["path"] != service.original_paths[slot - 1]):
        _unknown()
    return validate_metadata(dict(basis=record["basis"], binding=record["binding"], attempt=0,
        source_identity=attempt["source_identity"], original=publication["original"],
        answer_source=observation_source("answer_observation", 2), terminal="completed",
        terminal_source=attempt["terminal_source"], correction_binding=None))


@contextmanager
def admission(scope, history, state, selected, *, expected=None):
    """Read complete saved inventory under sorted source leases.

    No absence conclusion is made here. The caller must retain this context
    through the fresh core writer, commit and connection close.
    """
    from .review_answer_continuation_preparation import collection_path, issued_bases
    from .review_answer_collection_repository import CollectionRepository
    root, resolution = scope.root, SimpleNamespace(paths=scope.paths, target=scope.target)
    if root.version != 2 or any(b["version"] != 2 for b in root.collection_bases):
        _unknown()
    retired = retired_locations(root, history, state, repo=scope.repo, resolution=resolution)
    locations = [(root.packet_path, root.result_paths, root.collection_bases[0])]
    for row in history:
        if row.preparation_id != root.preparation_id or row.phase not in ("prepared", "published"):
            continue
        if row.answer_protocol != "review-answer-v1":
            _unknown()
        records = CollectionRepository(collection_path(scope.paths, root, row.continuation_id)).read()
        bases = issued_bases(root, row, records)
        if any(b["version"] != 2 for b in bases):
            _unknown()
        locations.append((row.packet_path, row.result_paths, bases[0]))
    groups = []
    for packet, paths, basis in locations:
        service = service_for(scope.repo, resolution, root, basis, retained=True)
        groups.append((str(service.repository.path), packet, paths, service))
    registered = {node.session_id for node in state.heads}
    current = [row for row in state.records if row.preparation_id == root.preparation_id]
    pending = current[-1] if current else None
    if pending is not None and pending.state != "prepared":
        _unknown()
    pending_actors = ({choice.session_id for choice in pending.selected}
        | {capture["session_id"] for capture, _ in state.collected_captures.get(pending.continuation_id, ())}
        if pending is not None else None)
    selected_ids = {choice.session_id for choice in selected}
    if selected_ids & registered or any(choice.action != "supplement" or choice.prior_receipt_id is not None for choice in selected):
        _unknown()
    descriptors, healthy, originals = [], [], []
    with ExitStack() as stack:
        for _, packet, paths, service in sorted(groups):
            lease = stack.enter_context(service.repository.serial())
            for slot, record in enumerate(service.repository.read(), 1):
                if (packet, paths[slot-1]) in retired:
                    continue
                binding = record["binding"]
                if binding is None:
                    _unknown()
                actor = binding["reviewer_session_id"]
                if actor in registered:
                    # Only an already registered judgment outside this pending
                    # batch is history. A partially registered batch cannot be
                    # shrunk to an apparently absent inventory.
                    if pending_actors is None or actor in pending_actors:
                        _unknown()
                    continue
                service.check_basis(record["basis"], slot, lease=lease, persist_invalidation=False)
                meta = _saved_metadata(service, slot, record)
                originals.append((actor, meta["original"]["digest"][7:]))
                path = files.checked_path(scope.repo, meta["original"]["path"])
                raw = files.read_bounded(path, 262144)
                try:
                    saved = read_original(scope.repo, packet, paths[slot-1], preparation=(resolution, root))
                except AnswerError as error:
                    if error.failure["class"] != "privacy_rejected":
                        raise
                    rejection = inspect_retirement_rejection(raw, meta["original"])
                else:
                    rejection = None
                    healthy.append(saved)
                if files.read_bounded(path, 262144) != raw or service.repository.read()[slot-1] != record:
                    _unknown()
                service.check_basis(record["basis"], slot, lease=lease, persist_invalidation=False)
                context = meta["basis"]["continuation"]
                descriptors.append(dict(source_continuation_id=context["continuation_id"] if context else None,
                    slot=slot, **meta["source_identity"], registration=record["registration"], rejection=rejection))
        descriptors.sort(key=lambda row: row["reviewer_session_id"])
        healthy.sort(key=lambda row: row.bound.binding.session_id)
        audit = validate(dict(version=1, source_continuation_id=current[-1].continuation_id if current else None,
                              sources=descriptors))
        if (len(descriptors) + len(registered) != len(root.result_paths)
                or {s["reviewer_session_id"] for s in descriptors if s["rejection"] is not None} != selected_ids
                or expected is not None and audit != expected):
            _unknown()
        yield RejectionInventory(audit, tuple(healthy), tuple(sorted(originals)))


@contextmanager
def continuation_admission(scope, leases, snapshot, selected, args):
    """Only the explicit option may enter the exceptional reader/proof path."""
    enabled = getattr(args, "replace_rejected_answers", False)
    transition = next((row for row in snapshot.history if row.preparation_id == scope.root.preparation_id
                       and row.packet_path == args.directory + "/packet.json"), None)
    if transition is not None and bool(transition.rejected_answer_replacement) != enabled:
        _unknown()
    if not enabled:
        yield None
        return
    if getattr(args, "migrate_legacy_reviewers", False):
        _unknown()
    from .review_wait_runtime.continuation_cleanup_repository import observe_latest_cleanup
    cleanup = observe_latest_cleanup(scope.paths, scope.caller.require(), scope.root.basis["task_id"],
        snapshot.previous.attempt, snapshot.previous.record)
    if cleanup.state != "settled":
        files.fail(cleanup.reason)
    if transition is not None and any(row.continuation_id == transition.continuation_id for row in snapshot.state.records):
        yield None  # Existing native admission is reconciled, never repeated.
        return
    history = snapshot.history[:-1] if transition is not None else snapshot.history
    expected = transition.rejected_answer_replacement if transition is not None else None
    with admission(scope, history, snapshot.state, selected, expected=expected) as inventory:
        if leases.integrated is not None:
            current = leases.integrated.journal.read()
            if (current.commit != "not_started" or current.completion != "not_started"
                or current.registration == "succeeded" or current.receipt_ids
                or current.continuation_id != inventory.audit["source_continuation_id"]
                or current.registration == "dispatching" and tuple(sorted(current.originals)) != inventory.originals):
                _unknown()
        yield inventory


def replacement_for(root, history, metadata, consumer_id, registration, state):
    """Effect guard for both old writers and retained healthy-source dispatch."""
    candidates = []
    for row in history:
        if row.preparation_id != root.preparation_id or row.rejected_answer_replacement is None:
            continue
        for item in _native_replacement(root, row, state)["sources"]:
            if not matches(item, metadata):
                continue
            if item["rejection"] is not None:
                _unknown()
            if item["registration"] == registration:
                candidates.append(row.continuation_id)
    # Several successive rejected rounds may carry the same untouched sibling
    # and old dispatch tuple. Only the latest admitted replacement owns it;
    # predecessor writers remain fenced even though their audit is preserved.
    if candidates and candidates[-1] != consumer_id:
        _unknown()
    return candidates[-1] if candidates else None
