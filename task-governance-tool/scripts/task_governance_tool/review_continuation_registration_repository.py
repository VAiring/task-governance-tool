"""Repository admission for atomic typed batches and immutable originals.

The existing result service owns normalization, source rows, Findings, events
and transaction lifetime. This adapter derives revisions and reconciles known
registered originals; it neither reads transport files nor commits SQLite.
"""

from dataclasses import dataclass
from contextlib import nullcontext

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
    with repository.validated_continuation_history(connection, project_id=project_id, task_id=task_id) as history:
        return _begin_locked(connection, project_id=project_id, task_id=task_id, identity=identity,
            bindings=bindings, retained_receipt_ids=retained_receipt_ids, caller=caller,
            database_target=database_target, retained_originals=retained_originals, history=history)


def _begin_locked(connection, *, project_id, task_id, identity, bindings,
                  retained_receipt_ids, caller, database_target, retained_originals, history):
    records = history.records(connection, project_id=project_id, task_id=task_id)
    record = next((row for row in records if row.continuation_id == identity.continuation_id), None)
    if (record is None or record.preparation_id != identity.preparation_id
        or record.sequence != identity.continuation_sequence or record.state not in ("prepared", "registered")):
        raise _conflict()
    repository.require_current_basis(connection, record.basis, caller)
    if database_target is not None and (database_target.binding_path_hash != record.basis["project_path_hash"]
        or database_target.binding_generation != record.basis["project_binding_generation"]):
        raise validation_error("review_continuation_basis_changed", "review continuation project binding changed")
    selected = {row.session_id: row for row in record.selected}
    sources = repository.read_original_sources(connection, record.continuation_id, selected)
    captures = sources.legacy_captures
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
            if (item.continuation is None or item.continuation.identity.continuation_id != sources.continuations[actor]
                or item.binding.original_result_digest != capture["original_result_digest"]
                or bound_metadata_digest(item) != capture["metadata_digest"]):
                raise _conflict()
        elif item.continuation is not None:
            raise _conflict()
    if not (set(selected) | set(captured)) <= seen:
        raise _conflict()
    nodes = history.judgments(connection, basis=record.basis)
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
    publications = repository.prepare_publications_locked(connection, record, caller, _history=history) if record.state == "prepared" else {}
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


def begin_collected_locked(connection, *, project_id, task_id, submission, caller, database_target=None):
    """Admit every collected/legacy item against native immutable source facts.

    Operational file/host observations are admitted outside this writer. This
    boundary still compares the full current execution basis and capture graph.
    """
    from .review_answer_transport import CollectedSubmission
    if type(submission) is not CollectedSubmission:
        raise _conflict()
    scope = (repository.validated_continuation_history(connection, project_id=project_id, task_id=task_id)
             if submission.continuation is not None else nullcontext(None))
    with scope as history:
        return _begin_collected_locked(connection, project_id=project_id, task_id=task_id,
            submission=submission, caller=caller, database_target=database_target, history=history)


def _begin_collected_locked(connection, *, project_id, task_id, submission, caller, database_target, history):
    from .review_answer_transport import CollectedItem, LegacyItem
    from .review_answer_values import validate_metadata
    identity = submission.continuation
    record = None
    publications = {}
    captures = {}
    selected = {}
    if identity is not None:
        records = history.records(connection, project_id=project_id, task_id=task_id)
        record = next((r for r in records if r.continuation_id == identity["continuation_id"]),None)
        if record is None or record.preparation_id != submission.preparation_id or record.sequence != identity["sequence"] or record.state not in ("prepared","registered"):
            raise _conflict()
        if identity["selection_digest"] != values.selection_digest(record.selected):
            raise _conflict()
        repository.require_current_basis(connection,record.basis,caller)
        selected = {s.session_id:s for s in record.selected}
        sources = repository.read_original_sources(connection, record.continuation_id, selected)
        captures = {r["capture_identity"]["session_id"]:r for r in sources.collected_captures}
        if record.state == "prepared":
            publications = repository.prepare_publications_locked(connection,record,caller,_history=history)
            nodes = history.judgments(connection,basis=record.basis)
            by_id = {n.receipt_id:n for n in nodes}
            by_record = {r.continuation_id:r for r in records}
            for actor,capture in captures.items():
                source_id = sources.continuations[actor]
                source_record = by_record[source_id] if source_id is not None else record
                choice = next((s for s in source_record.selected if s.session_id == actor),None) if source_id else values.SelectedReviewer(actor,"supplement",None,"Retained initial collected original")
                if choice is None:
                    raise _conflict()
                revision = by_id[choice.prior_receipt_id].revision+1 if choice.prior_receipt_id else 1
                publications[actor] = repository.CollectedJudgmentPublication(source_record,choice,revision,record.continuation_id,capture,source_id)
            values.select_effective(nodes,quality_basis=values.quality_identity(record.basis),pending=tuple(p.selected for p in publications.values()))
    supplied_captures = [r for r in submission.retained_originals if type(r) is dict]
    if len(supplied_captures) != len(captures) or {r["session_id"] for r in supplied_captures} != set(captures):
        raise _conflict()
    seen = set()
    existing = []
    legacy_bindings = []
    initial_sessions = {use.session_id for use in submission.source_uses if use.kind == "initial"}
    if initial_sessions:
        same_preparation = tuple(row for row in records if row.preparation_id == record.preparation_id)
        if (not same_preparation or same_preparation[-1].continuation_id != record.continuation_id
            or initial_sessions & {choice.session_id for row in records
                if row.basis["target_generation"] == record.basis["target_generation"] for choice in row.selected}):
            raise _conflict()
        nodes = history.judgments(connection, basis=record.basis)
        if record.state == "prepared" and initial_sessions & {node.session_id for node in nodes}:
            raise _conflict()
    for item in submission.items:
        session = item.session_binding.session_id
        if session in seen:
            raise _conflict()
        seen.add(session)
        if type(item) is CollectedItem:
            metadata = validate_metadata(item.metadata,raw=item.raw)
            basis = metadata["basis"]
            execution = basis["execution_basis"]
            if execution["project_id"] != project_id or execution["task_id"] != task_id or basis["preparation_id"] != submission.preparation_id:
                raise _conflict()
            repository.require_current_basis(connection,execution,caller)
            if database_target is not None and (execution["project_path_hash"] != database_target.binding_path_hash or execution["project_binding_generation"] != database_target.binding_generation):
                raise _conflict()
            source_context = basis["continuation"]
            if session in captures:
                capture = captures[session]
                from .review_answer_values import metadata_digest, source_metadata_projection
                supplied = next(r for r in supplied_captures if r["session_id"] == session)
                if (supplied["metadata_digest"] != metadata_digest(metadata)
                    or {k:v for k,v in supplied.items() if k != "metadata_digest"} != capture["capture_identity"]
                    or source_metadata_projection(metadata) != capture["source_metadata"]):
                    raise _conflict()
            elif session in initial_sessions:
                if source_context is not None or execution != record.basis or basis["route"] != record.route:
                    raise _conflict()
            elif record is not None:
                if session not in selected or source_context != dict(continuation_id=record.continuation_id,sequence=record.sequence,selection_digest=values.selection_digest(record.selected)):
                    raise _conflict()
            elif source_context is not None:
                raise _conflict()
            source = metadata["source_identity"]
            row = connection.execute("SELECT * FROM review_collection_sources WHERE reviewer_session_id=? AND turn_id=? AND message_id=?", (session,source["turn_id"],source["message_id"])).fetchone()
            if row is not None:
                from .review_answer_values import basis_digest
                if any(row[k] != v for k,v in (("preparation_id",basis["preparation_id"]),("slot",metadata["binding"]["slot"]),("attempt",metadata["attempt"]),("basis_digest",basis_digest(basis)),("original_digest",metadata["original"]["digest"]),("payload_digest",metadata["original"]["payload_digest"]),("submission_digest",submission.submission_digest))):
                    raise _conflict()
            existing.append(row["review_receipt_id"] if row is not None else None)
        elif type(item) is LegacyItem:
            legacy_bindings.append(item.bound)
            saved=connection.execute("SELECT review_receipt_id FROM review_collection_dispatch_entries WHERE preparation_id=? AND submission_digest=? AND slot=?",(submission.preparation_id,submission.submission_digest,item.slot)).fetchone()
            if saved is not None:
                binding=connection.execute("SELECT * FROM review_receipt_sessions WHERE review_receipt_id=?",(saved["review_receipt_id"],)).fetchone()
                if binding is None or (binding["session_id"],binding["execution_id"],binding["binding_source"],binding["original_result_digest"]) != (session,item.session_binding.execution_id,"handoff",item.session_binding.original_result_digest):
                    raise _conflict()
            existing.append(saved["review_receipt_id"] if saved else None)
        else:
            raise _conflict()
    expected = set(selected)|set(captures)
    legacy_captures = sources.legacy_captures if record else ()
    if record is not None and not expected|{r["session_id"] for r in legacy_captures} <= seen:
        raise _conflict()
    if record is None and (submission.retained_receipt_ids or submission.retained_originals or legacy_bindings):
        raise _conflict()
    if record is not None:
        heads = values.select_effective(history.judgments(connection,basis=record.basis),quality_basis=values.quality_identity(record.basis))
        retained = {r.receipt_id for r in heads.heads if r.session_id not in selected} - set(existing)
        if set(submission.retained_receipt_ids) != retained:
            raise _conflict()
    # Legacy retained arms keep their exact existing sidecar validation.
    if legacy_bindings:
        legacy_expected = {values.RetainedOriginal(**{k:r[k] for k in values.RetainedOriginal.__dataclass_fields__}) for r in legacy_captures}
        legacy_supplied = {r for r in submission.retained_originals if type(r) is values.RetainedOriginal}
        if legacy_expected != legacy_supplied:
            raise _conflict()
        for item in legacy_bindings:
            if item.binding.session_id in initial_sessions:
                if item.continuation is not None:
                    raise _conflict()
                continue
            capture = next((r for r in legacy_captures if r["session_id"] == item.binding.session_id),None)
            if capture is None or item.continuation is None or item.continuation.identity.continuation_id != capture["prior_continuation_id"] or item.binding.original_result_digest != capture["original_result_digest"] or bound_metadata_digest(item) != capture["metadata_digest"]:
                raise _conflict()
    return ContinuedBatch(record,publications,tuple(existing),submission.retained_receipt_ids)


def finish_collected_locked(connection, batch, receipts, submission):
    if batch.record is None or batch.record.state != "prepared":
        return
    from .review_answer_transport import CollectedItem
    for result,item in zip(receipts,submission.items):
        actor = item.session_binding.session_id
        receipt_id = result["receipt"]["review_receipt_id"]
        publication = batch.publications.get(actor)
        if publication is not None:
            if not isinstance(publication,repository.CollectedJudgmentPublication) or publication.judgment_continuation_id is not None:
                repository.publish_result_locked(connection,record=publication.record,session_id=actor,
                    receipt_id=receipt_id,original_digest=item.session_binding.original_result_digest)
            if publication.consumer_id is not None and not isinstance(publication,repository.CollectedJudgmentPublication):
                connection.execute("UPDATE review_continuation_retained_originals SET result_receipt_id=? WHERE continuation_id=? AND session_id=? AND result_receipt_id IS NULL",(receipt_id,publication.consumer_id,actor))
        if type(item) is CollectedItem:
            connection.execute("UPDATE review_collected_captures SET result_receipt_id=? WHERE continuation_id=? AND session_id=? AND result_receipt_id IS NULL",(receipt_id,batch.record.continuation_id,actor))
    repository.advance_registered_locked(connection,batch.record)
