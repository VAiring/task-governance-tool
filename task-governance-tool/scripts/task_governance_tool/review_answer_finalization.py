"""Collected originals enter the existing finalizer, never a second finisher."""

from .review_answer_transport import CollectedSubmission
from .review_collected_results import dispatch_entry
from .review_handoff_transport import fail


def prepared_reports(finalizer, record):
    """Use immutable locators for completed reports without reopening a Task."""
    from .review_preparation_repository import PreparationRepository
    from .review_handoff_originals import read_original
    from .state_paths import path_lexically_exists
    from .review_handoff_scope import error_code
    paths = finalizer.resolution.paths
    path = paths.review_preparation_store(record.basis["parent_thread_id"], record.basis["task_id"],
        record.basis["target_generation"])
    if not path_lexically_exists(path):
        return None
    _, root = PreparationRepository(path).read()
    if root is None:
        return None
    initial = finalizer.journal.read_rounds()[0]
    if (root.route != "integrated" or root.finalization_store != finalizer.path.name
        or (root.packet_path, root.packet_digest, root.result_paths) !=
            (initial.packet_path, initial.packet_digest, initial.result_paths)):
        fail("review_packet_stale")
    locations = record.original_locations or tuple((record.packet_path, path) for path in record.result_paths)
    results = []
    for index, (packet, path) in enumerate(locations):
        try:
            source = read_original(finalizer.repo, packet, path,
                preparation=(finalizer.resolution, root), current=False)
            saved = source.bound.binding
            if record.originals and record.originals[index] != (saved.session_id, saved.original_result_digest):
                fail("handoff_file_changed")
            results.append({"path": path, "status": "available", "review": review_view(source)})
        except Exception as exc:
            results.append({"path": path, "status": "unavailable", "code": error_code(exc)})
    return results


def review_view(source):
    """Preserve every review finding/check/limitation without inventing v1 facts."""
    from .review_answer import decode_answer
    from .review_results import decode_review_results
    if source.collected_metadata is None:
        return decode_review_results(source.raw)["receipts"][0]
    payload = decode_answer(source.raw).payload
    return {"reviewer": source.collected_metadata["binding"]["reviewer_key"],
        "kind": "independent", **payload}


def _checked_native(observed, submission):
    """Keep the existing exact public audit shape, never an arbitrary response."""
    from .review_continuation import identifier
    from .review_answer_values import canonical_bytes
    if (type(observed) is not dict or set(observed) != {
            "status", "preparation_id", "submission_digest", "current_basis", "entries", "reason"}
            or observed["preparation_id"] != submission.preparation_id
            or observed["submission_digest"] != submission.submission_digest
            or type(observed["entries"]) is not list):
        fail("finalization_registration_unknown")
    status, reason, entries = observed["status"], observed["reason"], observed["entries"]
    if status == "recorded":
        if (type(observed["current_basis"]) is not bool or reason is not None
                or len(entries) != len(submission.items) or not 1 <= len(entries) <= 8
                or any(type(row) is not dict or not identifier(row.get("receipt_id"), "tg_review_receipt_")
                       for row in entries)
                or len({row["receipt_id"] for row in entries}) != len(entries)
                or entries != [dispatch_entry(item, row["receipt_id"], submission.submission_digest)
                               for item, row in zip(submission.items, entries)]):
            fail("finalization_registration_unknown")
    elif (observed["current_basis"] is not None or entries
            or not (status == "absent" and reason is None
                or status == "conflict" and reason in ("source_mismatch", "partial_inventory")
                or status == "unknown" and reason in ("inventory_unavailable", "capacity"))):
        fail("finalization_registration_unknown")
    if len(canonical_bytes(observed)) > 32768:
        fail("finalization_registration_unknown")
    return observed


def _classify_registration(native, sources):
    if any(row["protocol"] != "review-answer-v1" for row in sources):
        return "inconsistent", "mixed_protocol"
    if native["status"] in ("conflict", "unknown"):
        return ("inconsistent" if native["status"] == "conflict" else "unavailable"), "native_"+native["status"]
    states = {row["state"] for row in sources}
    matches = {row["submission_digest_match"] for row in sources}
    if native["status"] == "absent":
        if states == {"not_started"} and matches == {"absent"}:
            return "not_dispatched", None
        if states <= {"dispatched", "unknown"} and matches == {"matching"}:
            return "dispatched", None
    elif native["current_basis"] is True and matches == {"matching"}:
        if all(row["state"] in ("dispatched", "unknown", "registered") and
                (row["receipt_id"] is None or row["receipt_id"] == entry["receipt_id"])
                for row, entry in zip(sources, native["entries"])):
            return "recorded", None
    return "inconsistent", "source_native_mismatch"


def registration_observation(finalizer, record):
    """Observe an unresolved reservation without settling or replaying it.

    All leases already exist. Order is finalization, collection, native read;
    no host, state write, confirmation, reset or writer call is permitted.
    Nothing returned here is a reusable proof or permission for another call.
    """
    from .review_answer_handoff import registration_admission
    from .review_session_transport import decode_submission
    from . import review_handoff_originals as originals
    result = dict(version=1, preparation_id=None, submission_digest=None,
        native=None, sources=[], classification="unavailable", reason="finalization_unavailable")
    reason = "finalization_unavailable"
    try:
        with finalizer.journal.serial():
            if finalizer.journal.read() != record:
                result["reason"] = "basis_changed"
                return result
            reason = "submission_unavailable"
            if record.continuation_id is not None:
                submission, _locations, _retained = finalizer._continuation_submission(record)
            else:
                _, framed = originals.submission(finalizer.repo, record.packet_path, record.result_paths)
                submission = decode_submission(framed)
            if type(submission) is not CollectedSubmission:
                return None  # The existing legacy report remains unchanged.
            result.update(preparation_id=submission.preparation_id, submission_digest=submission.submission_digest)
            if (not submission.bindings or tuple((b.binding.session_id, b.binding.original_result_digest)
                    for b in submission.bindings) != record.originals):
                fail("handoff_file_changed")
            reason = "basis_changed"
            finalizer._match(record, finalizer.core.read(finalizer.task_id), allow_done=True)
            reason = "source_unavailable"
            with registration_admission(finalizer.repo, submission, finalizer.caller) as admitted:
                sources = admitted.registration_states()
                reason = "native_unavailable"
                native = _checked_native(finalizer.core.collection_registration(finalizer.task_id, submission), submission)
                classification, diagnostic = _classify_registration(native, sources)
                reason = "source_unavailable"
            # Publish no prefix if any source or finalization lease cleanup fails.
            reason = "finalization_unavailable"
        result.update(native=native, sources=sources, classification=classification, reason=diagnostic)
    except Exception as exc:
        result["reason"] = "busy" if getattr(exc, "code", None) in ("writer_busy", "database_busy") else reason
    return result


def registered_ids(core, task_id, submission, *, basis, dispatched):
    if type(submission) is not CollectedSubmission:
        fail("invalid_review_evidence")
    recovery = None
    if dispatched:
        # Native absence alone never settles a dispatched writer. A finalizer
        # reservation can, however, precede every source's dispatch intent.
        observed = core.collection_registration(task_id, submission)
        from .review_answer_handoff import registration_admission
        if (type(observed) is dict and set(observed) == {
                "status", "preparation_id", "submission_digest", "current_basis", "entries", "reason"}
            and observed["status"] == "absent" and observed["reason"] is None
            and observed.get("preparation_id") == submission.preparation_id
            and observed.get("submission_digest") == submission.submission_digest
            and observed.get("current_basis") is None and observed.get("entries") == []):
            with registration_admission(core.repo, submission, core.caller) as admitted:
                if not admitted.prove_not_dispatched():
                    from .review_answer_transport import SettledRegistrationRecovery
                    recovery = SettledRegistrationRecovery(submission.preparation_id, submission.submission_digest)
                    try:
                        admitted.require_settled_recovery(recovery, basis)
                    except Exception:
                        fail("finalization_registration_unknown")
            # Do not recurse under collection leases. The same writer repeats
            # admission; settled recovery additionally requires fresh native
            # absence inside its writer lock. This request is not a proof.
        else:
            if (observed.get("status") != "recorded" or observed.get("current_basis") is not True
                or observed.get("preparation_id") != submission.preparation_id
                or observed.get("submission_digest") != submission.submission_digest
                or len(observed.get("entries", [])) != len(submission.items)):
                fail("finalization_registration_unknown")
            entries = observed["entries"]
            expected = [dispatch_entry(item, entry["receipt_id"], submission.submission_digest)
                for item, entry in zip(submission.items, entries)]
            if entries != expected:
                fail("finalization_registration_unknown")
            with registration_admission(core.repo, submission, core.caller) as admitted:
                admitted.confirm({"receipts": [{"receipt": {"review_receipt_id": row["receipt_id"]}} for row in entries]})
            return tuple(row["receipt_id"] for row in entries)
    data = core.register(task_id, submission, basis=basis,
        **({"registration_recovery": recovery} if recovery is not None else {}))
    receipts = data.get("receipts")
    if type(receipts) is not list or len(receipts) != len(submission.items):
        fail("finalization_registration_unknown")
    return tuple(row["receipt"]["review_receipt_id"] for row in receipts)
