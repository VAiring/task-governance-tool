"""Collected arm of the existing atomic public review-result registration.

The CLI owns admission and its one transaction. This composition uses the same
Receipt/Finding writers and shared gate as legacy input; it never opens a
connection, reads originals, commits, invokes a host or finalizes a Task.
"""

from . import task_ownership
from .review_answer_transport import (CollectedSubmission, CollectedItem, LegacyItem,
    SettledRegistrationRecovery, ManualRegistrationRecovery, decode_collected_submission, input_error)
from .review_answer_repository import CollectedReceiptInput, record_dispatch_locked
from .review_results import _invalid_input, _basis_mismatch, normalize_review_results, decode_review_results
from .reviews import (normalize_receipt, add_review_receipt, add_review_finding,
    lock_and_reread_target_owner, reject_concurrent_review_basis_change,
    validate_stored_review_target, require_current_capture, read_review_evidence,
    first_review_gate_error)
from .tasks import read_mutation_basis, reject_done_task_write, TaskRepositoryError
from .task_values import validate_task_id, validate_text
from .storage import current_schema_version


def _normalize(item, tier):
    if type(item) is LegacyItem:
        return normalize_review_results(decode_review_results(item.raw), review_tier=tier)["receipts"][0]
    payload = item.answer.payload
    from .review_answer import storage_summaries
    summary, finding_summaries = storage_summaries(payload)
    value = normalize_receipt(review_tier=tier, reviewer=item.metadata["binding"]["reviewer_key"],
        kind="independent", verdict=payload["verdict"], summary=summary,
        user_approved=False, collected=True)
    value["findings"] = [dict(severity=finding["severity"], summary=summary)
        for finding, summary in zip(payload["findings"], finding_summaries)]
    return value


def dispatch_entry(item, receipt_id, submission_digest):
    target, binding = item.target, item.session_binding
    result = dict(slot=item.slot, protocol="legacy-review-result-v1", receipt_id=receipt_id,
        project_id=target.project_id, task_id=target.task_id, contract_revision=target.contract_revision,
        target={"kind": target.target_kind, "value": target.target_value,
            "base_revision": target.target_base_revision, "generation": target.target_generation, "capture_version": 1},
        source_preparation_id=None, source_continuation_id=None,
        reviewer_session_id=binding.session_id, turn_id=None, message_id=None, basis_digest=None,
        original_digest="sha256:" + binding.original_result_digest, payload_digest=None,
        submission_digest=submission_digest)
    if type(item) is CollectedItem:
        meta = item.metadata
        result.update(protocol="review-answer-v1", source_preparation_id=meta["basis"]["preparation_id"],
            source_continuation_id=meta["basis"]["continuation"]["continuation_id"] if meta["basis"]["continuation"] else None,
            turn_id=meta["source_identity"]["turn_id"], message_id=meta["source_identity"]["message_id"],
            basis_digest=meta["binding"]["basis_digest"], payload_digest=meta["original"]["payload_digest"])
    elif item.bound.continuation is not None:
        result.update(source_preparation_id=item.bound.continuation.identity.preparation_id,
            source_continuation_id=item.bound.continuation.identity.continuation_id)
    return result


def add_collected_review_results(connection, project, task_id, submission, *, caller,
                                  database_target=None, finalization_basis=None,
                                  user_approved_reviewers=(), registration_recovery=None):
    if type(submission) is not CollectedSubmission or user_approved_reviewers:
        raise _invalid_input()
    # Internal callers receive the same closed byte validation as CLI stdin.
    submission = decode_collected_submission(submission.raw)
    if registration_recovery is not None:
        if type(registration_recovery) not in (SettledRegistrationRecovery, ManualRegistrationRecovery) or database_target is None:
            raise _invalid_input()
        registration_recovery.require_submission(submission, finalization_basis)
    task_id = validate_task_id(task_id)
    if submission.task_id != task_id or current_schema_version(connection) < 30:
        raise _basis_mismatch()
    observed, ownership = read_mutation_basis(connection, project.project_id, task_id)
    if observed is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(observed)
    task_ownership.require_completion_owner(ownership, caller)
    normalized = tuple(_normalize(item, observed["review_tier"]) for item in submission.items)
    if len({row["reviewer_key"] for row in normalized}) != len(normalized):
        raise _invalid_input()
    locked = lock_and_reread_target_owner(connection, project, task_id,
        database_target=database_target, caller=caller, ownership=ownership,
        review_session=submission.items[0].session_binding)
    if (any(type(item) is CollectedItem and item.metadata["basis"]["version"] == 2 for item in submission.items)
            and locked["status"] not in ("in_progress", "review_pending")):
        raise _basis_mismatch()
    if finalization_basis is not None:
        finalization_basis.require_target(database_target)
        finalization_basis.require_task(locked, task_ownership.read_basis(
            connection, project_id=project.project_id, task_id=task_id))
    reject_concurrent_review_basis_change(observed, locked, code="review_target_mismatch",
        message="review result Task, Contract, or target does not match the current task")
    target = submission.items[0].target
    if (target.project_id != project.project_id or target.contract_revision != locked["current_contract_revision"]
        or any(getattr(target, "target_" + key) != locked["review_target_" + key]
            for key in ("kind", "value", "base_revision", "generation"))):
        raise _basis_mismatch()
    validate_stored_review_target(locked)
    require_current_capture(locked)
    if type(registration_recovery) is ManualRegistrationRecovery:
        from .review_continuation_repository import require_current_basis
        for item in submission.items:
            require_current_basis(connection, item.metadata["basis"]["execution_basis"], caller)
        receipts = _manual_recorded_results(connection, project.project_id, task_id,
            submission, registration_recovery, normalized)
        if receipts is not None:
            return _result(connection, project.project_id, task_id, locked, target, receipts)
    if registration_recovery is not None:
        from .review_answer_repository import require_absent_registration_locked
        require_absent_registration_locked(connection, submission)
    from .review_continuation_registration_repository import begin_collected_locked, finish_collected_locked, existing_acknowledgement
    batch = begin_collected_locked(connection, project_id=project.project_id, task_id=task_id,
        submission=submission, caller=caller, database_target=database_target)
    if registration_recovery is not None and any(value is not None for value in batch.existing_receipt_ids):
        raise input_error("registration_unknown", reason="effect_unknown")
    receipts = []
    for index, (item, entry) in enumerate(zip(submission.items, normalized)):
        if batch.existing_receipt_ids[index] is not None:
            receipts.append(existing_acknowledgement(connection, batch.existing_receipt_ids[index]))
            continue
        kwargs = {}
        if type(item) is CollectedItem:
            kwargs["collected_answer"] = CollectedReceiptInput(item.metadata, item.answer, submission.submission_digest)
        else:
            provenance = dict(entry["provenance"] or {})
            kwargs = {**provenance, "review_methods": provenance.pop("method_codes", None)}
            kwargs.pop("method_codes", None)
        receipt = add_review_receipt(connection, project, task_id,
            reviewer=entry["reviewer_key"], kind=entry["receipt_kind"], verdict=entry["verdict"],
            summary=entry["summary"], user_approved=bool(entry["user_approved"]), **kwargs,
            database_target=database_target, caller=caller, session_binding=item.session_binding,
            _continued_publication=batch.publications.get(item.session_binding.session_id))
        findings = []
        for finding in entry["findings"]:
            saved = add_review_finding(connection, project, task_id,
                receipt_id=receipt.receipt["review_receipt_id"], **finding,
                database_target=database_target, caller=caller)
            findings.append({"finding": {key: saved.finding[key] for key in (
                "review_finding_id", "review_receipt_id", "severity", "summary", "status")}})
        result = {"receipt": {key: receipt.receipt[key] for key in (
            "review_receipt_id", "reviewer_key", "receipt_kind", "verdict", "summary", "user_approved")},
            "findings": findings}
        if "judgment_revision" in receipt.receipt:
            result["receipt"]["judgment_revision"] = receipt.receipt["judgment_revision"]
        receipts.append(result)
    finish_collected_locked(connection, batch, receipts, submission)
    record_dispatch_locked(connection, preparation_id=submission.preparation_id,
        submission_digest=submission.submission_digest, project_id=project.project_id, task_id=task_id,
        entries=[dispatch_entry(item, result["receipt"]["review_receipt_id"], submission.submission_digest)
                 for item, result in zip(submission.items, receipts)])
    return _result(connection, project.project_id, task_id, locked, target, receipts)


def _manual_recorded_results(connection, project_id, task_id, submission, recovery, normalized):
    """Confirm a complete current native effect; absence is checked separately by the writer."""
    from .review_answer_repository import read_registration
    from .review_continuation_registration_repository import existing_acknowledgement
    observed = read_registration(connection, project_id=project_id, task_id=task_id,
        preparation_id=submission.preparation_id, submission_digest=submission.submission_digest)
    if (type(observed) is not dict or set(observed) != {
            "status", "preparation_id", "submission_digest", "current_basis", "entries", "reason"}
            or observed["preparation_id"] != submission.preparation_id
            or observed["submission_digest"] != submission.submission_digest
            or type(observed["entries"]) is not list):
        raise input_error("registration_unknown", reason="effect_unknown")
    if observed["status"] == "absent":
        if (observed["reason"] is not None or observed["current_basis"] is not None
                or observed["entries"] or any(value is not None for value in recovery.receipt_ids)):
            raise input_error("registration_unknown", reason="effect_unknown")
        return None
    entries = observed["entries"]
    if (observed["status"] != "recorded" or observed["current_basis"] is not True or observed["reason"] is not None
            or len(entries) != len(submission.items)
            or entries != [dispatch_entry(item, row["receipt_id"], submission.submission_digest)
                           for item, row in zip(submission.items, entries)]
            or any(saved is not None and saved != row["receipt_id"]
                   for saved, row in zip(recovery.receipt_ids, entries))):
        raise input_error("registration_unknown", reason="effect_unknown")
    receipts = []
    for row, original in zip(entries, normalized):
        saved = existing_acknowledgement(connection, row["receipt_id"])
        # Native IDs are random and cannot reconstruct the original array order.
        # Keep current resolution facts, but order every Finding by its bound payload.
        findings = {(entry["finding"]["severity"], entry["finding"]["summary"]): entry
                    for entry in saved["findings"]}
        order = [(finding["severity"], finding["summary"]) for finding in original["findings"]]
        if len(findings) != len(saved["findings"]) or len(order) != len(findings) or set(order) != set(findings):
            raise input_error("registration_unknown", reason="effect_unknown")
        saved["findings"] = [findings[key] for key in order]
        receipts.append(saved)
    return receipts


def _result(connection, project_id, task_id, locked, target, receipts):
    evidence = read_review_evidence(connection, project_id, task_id, validated_task=locked)
    blocker = first_review_gate_error(evidence)
    return dict(receipts=receipts, review_gate={**evidence["gate"],
        "blocking_code": blocker.code if blocker else None,
        "basis": {"task_id": task_id, "contract_revision": target.contract_revision,
            "review_target": {key: getattr(target, "target_" + key) for key in ("kind", "value", "base_revision", "generation")}}},
        omitted_details=["provenance", "repeated_binding", "events", "timestamps", "resolution_metadata"])
