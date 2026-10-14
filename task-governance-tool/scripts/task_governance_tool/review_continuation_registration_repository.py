"""Repository admission for atomic typed batches and immutable originals.

The existing result service owns normalization, source rows, Findings, events
and transaction lifetime. This adapter derives revisions and reconciles known
registered originals; it neither reads transport files nor commits SQLite.
"""

from dataclasses import dataclass

from . import review_continuation as values
from . import review_continuation_repository as repository
from .review_session_transport import ContinuationIdentity, bound_metadata_digest
from .task_values import validation_error


def _conflict():
    return validation_error("review_continuation_conflict", "review continuation originals do not match the admitted batch")


@dataclass(frozen=True)
class ContinuedBatch:
    record: repository.Continuation
    publications: dict
    existing_receipt_ids: tuple[str | None, ...]
    retained_receipt_ids: tuple[str, ...]


def begin_locked(connection, *, project_id, task_id, identity, bindings,
                 retained_receipt_ids, caller, database_target=None, retained_originals=()):
    if type(identity) is not ContinuationIdentity or bindings is None:
        raise values._invalid()
    records = repository.read_continuations(connection, project_id=project_id, task_id=task_id)
    record = next((row for row in records if row.continuation_id == identity.continuation_id), None)
    if (record is None or record.preparation_id != identity.preparation_id
        or record.sequence != identity.continuation_sequence or record.state not in ("prepared", "registered")):
        raise _conflict()
    repository.require_current_basis(connection, record.basis, caller)
    if database_target is not None and (database_target.binding_path_hash != record.basis["project_path_hash"]
        or database_target.binding_generation != record.basis["project_binding_generation"]):
        raise validation_error("review_continuation_basis_changed", "review continuation project binding changed")
    selected = {row.session_id: row for row in record.selected}
    captures = repository.read_retained_originals(connection, record.continuation_id)
    expected_captures = {values.RetainedOriginal(**{key: row[key] for key in values.RetainedOriginal.__dataclass_fields__}) for row in captures}
    if type(retained_originals) is not tuple or any(type(row) is not values.RetainedOriginal for row in retained_originals) or set(retained_originals) != expected_captures or len(retained_originals) != len(expected_captures):
        raise _conflict()
    captured = {row["session_id"]: row for row in captures}
    seen = set()
    for item in bindings:
        actor = item.binding.session_id
        if actor in seen:
            raise _conflict()
        seen.add(actor)
        if actor in selected:
            choice = selected[actor]
            if (item.continuation is None or item.continuation.identity != identity
                or (item.continuation.action, item.continuation.prior_receipt_id) != (choice.action, choice.prior_receipt_id)):
                raise _conflict()
        elif actor in captured:
            capture = captured[actor]
            if (item.continuation is None or item.continuation.identity.continuation_id != capture["prior_continuation_id"]
                or item.binding.original_result_digest != capture["original_result_digest"]
                or bound_metadata_digest(item) != capture["metadata_digest"]):
                raise _conflict()
        elif item.continuation is not None:
            raise _conflict()
    if not (set(selected) | set(captured)) <= seen:
        raise _conflict()
    nodes = repository.read_judgments(connection, basis=record.basis)
    effective = values.select_effective(nodes, quality_basis=values.quality_identity(record.basis))
    bindings_by_id = connection.execute("""SELECT binding.*,receipt.reviewer_key FROM review_receipt_sessions AS binding
JOIN review_receipts AS receipt ON receipt.review_receipt_id=binding.review_receipt_id
WHERE receipt.project_id=? AND receipt.task_id=? AND receipt.target_generation=?""",
        (project_id, task_id, record.basis["target_generation"])).fetchall()
    saved = {row["review_receipt_id"]: row for row in bindings_by_id}
    registered_selected = {session: receipt for session, receipt, digest in record.results}
    existing = []
    for item in bindings:
        actor = item.binding.session_id
        candidates = ([captured[actor]["result_receipt_id"]] if actor in captured and captured[actor]["result_receipt_id"] is not None else
            [registered_selected[actor]] if actor in registered_selected else
            [row.receipt_id for row in effective.heads if row.session_id == actor]) if actor not in selected or record.state == "registered" else []
        matches = [receipt_id for receipt_id in candidates if receipt_id in saved and
            (saved[receipt_id]["session_id"], saved[receipt_id]["execution_id"], saved[receipt_id]["binding_source"],
             saved[receipt_id]["original_result_digest"]) ==
            (actor, item.binding.execution_id, "handoff", item.binding.original_result_digest)]
        if len(matches) > 1 or record.state == "registered" and not matches:
            raise _conflict()
        existing.append(matches[0] if matches else None)
    expected_retained = {row.receipt_id for row in effective.heads if row.session_id not in selected} - set(existing)
    if (type(retained_receipt_ids) is not tuple or len(set(retained_receipt_ids)) != len(retained_receipt_ids)
        or set(retained_receipt_ids) != expected_retained):
        raise _conflict()
    publications = repository.prepare_publications_locked(connection, record, caller) if record.state == "prepared" else {}
    return ContinuedBatch(record, publications, tuple(existing), tuple(sorted(retained_receipt_ids)))


def finish_locked(connection, batch: ContinuedBatch, receipts, bindings):
    if batch.record.state == "prepared":
        for result, bound in zip(receipts, bindings):
            if bound.binding.session_id in batch.publications:
                publication = batch.publications[bound.binding.session_id]
                repository.publish_result_locked(connection, record=publication.record,
                    session_id=bound.binding.session_id, receipt_id=result["receipt"]["review_receipt_id"],
                    original_digest=bound.binding.original_result_digest)
                if publication.consumer_id is not None:
                    connection.execute("UPDATE review_continuation_retained_originals SET result_receipt_id=? WHERE continuation_id=? AND session_id=? AND result_receipt_id IS NULL",
                        (result["receipt"]["review_receipt_id"],publication.consumer_id,bound.binding.session_id))
        repository.advance_registered_locked(connection, batch.record)


def existing_acknowledgement(connection, receipt_id):
    from .review_repository import validate_stored_review_finding_projection
    row = connection.execute("SELECT * FROM review_receipts WHERE review_receipt_id=?", (receipt_id,)).fetchone()
    if row is None:
        raise _conflict()
    findings = connection.execute("SELECT * FROM review_findings WHERE review_receipt_id=? ORDER BY review_finding_id", (receipt_id,)).fetchall()
    for finding in findings:
        validate_stored_review_finding_projection(finding)
    return {"receipt": {key: row[key] for key in ("review_receipt_id", "reviewer_key", "receipt_kind", "verdict",
        "summary", "user_approved", "judgment_revision")}, "findings": [{"finding": {key: finding[key]
        for key in ("review_finding_id", "review_receipt_id", "severity", "summary", "status")}} for finding in findings]}
