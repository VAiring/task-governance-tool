"""Transaction-bound continuation records and expected-head publication.

The caller owns admission, transaction and original/source evidence validation.
These primitives never open/commit SQLite, perform host I/O or choose transport
paths. Reads reject malformed history rather than selecting a timestamp winner.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass

from . import review_continuation as values
from .evidence_ledger import canonical_json_bytes, domain_digest
from .session_identity import CallerIdentity
from .task_values import TaskValidationError, validate_text, validation_error


@dataclass(frozen=True)
class Continuation:
    continuation_id: str
    preparation_id: str
    sequence: int
    predecessor_id: str | None
    basis: dict
    route: str
    state: str
    selected: tuple[values.SelectedReviewer, ...]
    results: tuple[tuple[str, str, str], ...]
    created_at: str
    registered_at: str | None
    retired_at: str | None = None
    retirement_digest: str | None = None


@dataclass(frozen=True)
class JudgmentPublication:
    record: Continuation
    selected: values.SelectedReviewer
    revision: int
    consumer_id: str | None = None
    retained_original: values.RetainedOriginal | None = None


@dataclass(frozen=True)
class CollectedJudgmentPublication:
    record: Continuation
    selected: values.SelectedReviewer
    revision: int
    consumer_id: str
    capture: dict
    judgment_continuation_id: str | None


@dataclass(frozen=True)
class OriginalSources:
    """Immutable origins within one consumer; never registration authority."""

    continuations: dict[str, str | None]
    legacy_captures: tuple[dict, ...]
    collected_captures: tuple[dict, ...]


def read_original_sources(connection, continuation_id, active_sessions) -> OriginalSources:
    """Share source interpretation without invoking publication/writer admission.

    Admission owns the validated continuation graph. Uncaptured initial answers
    have no continuation; retained answers keep their source, not the consumer.
    """
    from .storage import current_schema_version
    from .review_answer_repository import read_collected_captures
    legacy = read_retained_originals(connection, continuation_id)
    collected = (read_collected_captures(connection, continuation_id)
                 if current_schema_version(connection) >= 30 else ())
    origins = {actor: continuation_id for actor in active_sessions}
    retained = [(row["session_id"], row["prior_continuation_id"]) for row in legacy]
    retained.extend((row["capture_identity"]["session_id"],
                     row["capture_identity"]["source_continuation_id"]) for row in collected)
    for actor, source_id in retained:
        if actor in origins:
            raise _inconsistent()
        origins[actor] = source_id
    return OriginalSources(origins, legacy, collected)


def packet_context(connection, *, task, preparing_id=None):
    """Prepared pending context for the existing read-only Packet procedure."""
    from .storage import current_schema_version
    if current_schema_version(connection) < 29:
        return None
    records = read_continuations(connection, project_id=task["project_id"], task_id=task["task_id"])
    pending = [row for row in records if row.basis["target_generation"] == task["review_target_generation"] and row.state in ("preparing", "prepared")]
    if not pending:
        if preparing_id is not None:
            raise validation_error("review_continuation_conflict", "review continuation is no longer pending")
        return None
    if preparing_id is not None and (len(pending) != 1 or pending[0].continuation_id != preparing_id):
        raise validation_error("review_continuation_conflict", "review continuation context changed")
    if len(pending) != 1 or (pending[0].state != "prepared" and pending[0].continuation_id != preparing_id):
        raise validation_error("review_continuation_pending", "review continuation preparation is incomplete")
    record = pending[0]
    from .review_wait_basis_repository import read_review_basis
    if read_review_basis(connection, project_id=task["project_id"], task_id=task["task_id"]) != record.basis:
        raise validation_error("review_continuation_basis_changed", "review continuation basis changed")
    return dict(version=2, project_id=record.basis["project_id"], execution_id=record.basis["execution_id"],
        preparation_id=record.preparation_id, continuation_id=record.continuation_id, continuation_sequence=record.sequence,
        selected_reviewers=[dict(session_id=row.session_id, action=row.action, prior_receipt_id=row.prior_receipt_id) for row in record.selected])


def prepare_publications_locked(connection: sqlite3.Connection, record: Continuation,
                                caller: CallerIdentity) -> dict[str, JudgmentPublication]:
    """Prepare every expected head before any item of the atomic batch is written."""
    require_current_basis(connection, record.basis, caller)
    fresh = next((row for row in read_continuations(connection, project_id=record.basis["project_id"],
        task_id=record.basis["task_id"]) if row.continuation_id == record.continuation_id), None)
    if fresh is None or fresh != record or fresh.state != "prepared":
        raise validation_error("review_continuation_conflict", "review continuation is not prepared")
    nodes = read_judgments(connection, basis=record.basis)
    by_record = {row.continuation_id: row for row in read_continuations(connection, project_id=record.basis["project_id"], task_id=record.basis["task_id"])}
    captured = []
    sources = read_original_sources(connection, record.continuation_id,
                                    (choice.session_id for choice in record.selected))
    for row in sources.legacy_captures:
        old = by_record[sources.continuations[row["session_id"]]]
        choice = next(value for value in old.selected if value.session_id == row["session_id"])
        captured.append((old, choice, values.RetainedOriginal(**{key: row[key] for key in values.RetainedOriginal.__dataclass_fields__})))
    effective = values.select_effective(nodes, quality_basis=values.quality_identity(record.basis), pending=(*record.selected, *(row[1] for row in captured)))
    by_id = {node.receipt_id: node for node in effective.heads}
    publications = {choice.session_id: JudgmentPublication(record, choice,
        by_id[choice.prior_receipt_id].revision + 1 if choice.prior_receipt_id is not None else 1)
        for choice in record.selected}
    publications.update({choice.session_id: JudgmentPublication(old, choice,
        by_id[choice.prior_receipt_id].revision + 1 if choice.prior_receipt_id is not None else 1, record.continuation_id, original)
        for old, choice, original in captured})
    return publications


def require_publication_locked(connection: sqlite3.Connection, publication: JudgmentPublication,
                               *, session_id: str, reviewer_key: str, caller: CallerIdentity,
                               original_digest: str | None = None) -> None:
    """Recheck the captured batch admission without reading a transient partial batch."""
    if type(publication) is CollectedJudgmentPublication:
        from .review_answer_repository import read_collected_captures
        require_current_basis(connection,publication.record.basis,caller)
        rows = read_collected_captures(connection,publication.consumer_id)
        current = next((r for r in rows if r["capture_identity"]["session_id"] == session_id),None)
        consumer = connection.execute("SELECT state FROM review_continuations WHERE continuation_id=?",(publication.consumer_id,)).fetchone()
        if current != publication.capture or current is None or current["result_receipt_id"] is not None or consumer is None or consumer["state"] != "prepared" or current["capture_identity"]["original_digest"] != "sha256:"+original_digest:
            raise values._invalid()
        if publication.selected.prior_receipt_id is not None:
            prior = _session_proof(connection,publication.selected.prior_receipt_id,basis=publication.record.basis,session_id=session_id)
            if prior["reviewer_key"] != reviewer_key or prior["judgment_revision"]+1 != publication.revision:
                raise values._invalid()
            if connection.execute("SELECT 1 FROM review_receipt_judgments WHERE prior_receipt_id=?",(publication.selected.prior_receipt_id,)).fetchone():
                raise validation_error("review_continuation_conflict","review continuation expected head changed")
        elif publication.revision != 1:
            raise values._invalid()
        return
    if type(publication) is not JudgmentPublication or publication.selected.session_id != session_id:
        raise values._invalid()
    record, choice = publication.record, publication.selected
    require_current_basis(connection, record.basis, caller)
    row = connection.execute("""SELECT continuation.*,selected.result_receipt_id,
selected.action,selected.prior_receipt_id,selected.reason FROM review_continuations AS continuation
JOIN review_continuation_reviewers AS selected ON selected.continuation_id=continuation.continuation_id
WHERE continuation.continuation_id=? AND selected.session_id=?""", (record.continuation_id, session_id)).fetchone()
    expected_state = "superseded" if publication.consumer_id is not None else "prepared"
    if (row is None or row["state"] != expected_state or row["result_receipt_id"] is not None
        or row["basis_json"] != canonical_json_bytes(record.basis).decode()
        or row["preparation_id"] != record.preparation_id or row["sequence"] != record.sequence
        or (row["action"], row["prior_receipt_id"], row["reason"]) != (choice.action, choice.prior_receipt_id, choice.reason)):
        raise validation_error("review_continuation_conflict", "review continuation changed before registration")
    if publication.consumer_id is not None:
        capture = publication.retained_original
        retained = connection.execute("""SELECT retained.*,consumer.state,consumer.basis_json FROM review_continuation_retained_originals AS retained
JOIN review_continuations AS consumer ON consumer.continuation_id=retained.continuation_id
WHERE retained.continuation_id=? AND retained.session_id=?""", (publication.consumer_id,session_id)).fetchone()
        if (type(capture) is not values.RetainedOriginal or retained is None or retained["state"] != "prepared"
            or retained["basis_json"] != canonical_json_bytes(record.basis).decode() or retained["result_receipt_id"] is not None
            or any(retained[key] != value for key,value in capture.as_dict().items())
            or capture.original_result_digest != original_digest):
            raise validation_error("review_continuation_conflict", "retained original changed before registration")
    if choice.prior_receipt_id is not None:
        prior = _session_proof(connection, choice.prior_receipt_id, basis=record.basis, session_id=session_id)
        if prior["reviewer_key"] != reviewer_key or prior["judgment_revision"] + 1 != publication.revision:
            raise values._invalid()
        if connection.execute("SELECT 1 FROM review_receipt_judgments WHERE prior_receipt_id=?", (choice.prior_receipt_id,)).fetchone():
            raise validation_error("review_continuation_conflict", "review continuation expected head changed")
    elif publication.revision != 1:
        raise values._invalid()


def _inconsistent():
    from .storage import evidence_ledger_inconsistent
    return evidence_ledger_inconsistent()


def _writer(connection):
    from .storage import _require_evidence_writer
    _require_evidence_writer(connection)


def _decode_basis(raw):
    try:
        if type(raw) is not str or len(raw.encode("utf-8")) > values.BASIS_LIMIT:
            raise _inconsistent()
        value = values.validate_basis(json.loads(raw))
        if canonical_json_bytes(value).decode("utf-8") != raw:
            raise _inconsistent()
        return value
    except (TaskValidationError, ValueError, UnicodeError, RecursionError) as exc:
        raise _inconsistent() from exc


def _session_proof(connection, receipt_id, *, basis, session_id):
    rows = connection.execute("""SELECT receipt.*,binding.session_id,binding.execution_id,
binding.binding_source,binding.original_result_digest,reference.contract_revision,
reference.authority_snapshot_id,reference.acceptance_criterion_id,reference.verification_criterion_id,
judgment.continuation_id,judgment.prior_receipt_id,judgment.judgment_revision AS metadata_revision
FROM review_receipts AS receipt
JOIN review_receipt_sessions AS binding ON binding.review_receipt_id=receipt.review_receipt_id
JOIN evidence_references AS reference ON reference.source_id=receipt.review_receipt_id
AND reference.source_kind='review_receipt' AND reference.source_state='recorded'
JOIN review_receipt_judgments AS judgment ON judgment.review_receipt_id=receipt.review_receipt_id
WHERE receipt.review_receipt_id=?""", (receipt_id,)).fetchall()
    if len(rows) != 1:
        raise _inconsistent()
    row = rows[0]
    fields = ("project_id", "task_id", "contract_revision", "authority_snapshot_id",
              "acceptance_criterion_id", "verification_criterion_id",
              "target_kind", "target_value", "target_base_revision", "target_generation")
    if (row["session_id"] != session_id or row["receipt_kind"] != "independent"
        or row["judgment_revision"] != row["metadata_revision"]
        or any(row[field] != basis[field] for field in fields)):
        raise _inconsistent()
    return row


def read_continuations(connection: sqlite3.Connection, *, project_id: str,
                       task_id: str) -> tuple[Continuation, ...]:
    rows = connection.execute("SELECT * FROM review_continuations WHERE project_id=? AND task_id=? ORDER BY preparation_id,sequence",
                              (project_id, task_id)).fetchall()
    return _validate_continuations(connection, rows, project_id=project_id, task_id=task_id)


def _validate_continuations(connection, rows, *, project_id, task_id):
    """Validate one Task's complete history from the caller's current snapshot."""
    from .storage import validate_utc_timestamp
    result = []
    by_id = {}
    for row in rows:
        try:
            basis = _decode_basis(row["basis_json"])
            if (not values.identifier(row["continuation_id"], "tg_review_continuation_")
                or not values.identifier(row["preparation_id"], "tg_review_preparation_")
                or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= values.CONTINUATION_LIMIT
                or row["route"] not in ("manual", "integrated")
                or row["state"] not in ("preparing", "prepared", "registered", "superseded")
                or (basis["project_id"], basis["task_id"], basis["target_generation"])
                    != (project_id, task_id, row["target_generation"])
                or values.basis_digest(basis) != row["basis_digest"]):
                raise _inconsistent()
            choices = connection.execute("SELECT * FROM review_continuation_reviewers WHERE continuation_id=? ORDER BY session_id",
                                         (row["continuation_id"],)).fetchall()
            selected = values.validate_selection({"version": 1, "reviewers": [
                {key: choice[key] for key in ("session_id", "action", "prior_receipt_id", "reason")} for choice in choices]})
            if values.selection_digest(selected) != row["selection_digest"]:
                raise _inconsistent()
            results = []
            for choice in choices:
                if choice["reason"] != next(item.reason for item in selected if item.session_id == choice["session_id"]):
                    raise _inconsistent()
                if choice["prior_receipt_id"] is not None:
                    _session_proof(connection, choice["prior_receipt_id"], basis=basis, session_id=choice["session_id"])
                receipt, digest = choice["result_receipt_id"], choice["original_result_digest"]
                if (receipt is None) != (digest is None):
                    raise _inconsistent()
                if receipt is not None:
                    if not values.identifier(receipt, "tg_review_receipt_") or not values._digest(digest):
                        raise _inconsistent()
                    proof = _session_proof(connection, receipt, basis=basis, session_id=choice["session_id"])
                    if (proof["continuation_id"] != row["continuation_id"]
                        or proof["prior_receipt_id"] != choice["prior_receipt_id"]
                        or proof["execution_id"] != basis["execution_id"] or proof["binding_source"] != "handoff"
                        or proof["original_result_digest"] != digest):
                        raise _inconsistent()
                    results.append((choice["session_id"], receipt, digest))
            if ((row["state"] == "registered") != (row["registered_at"] is not None)
                or row["state"] == "registered" and len(results) != len(selected)
                or row["state"] in ("preparing", "prepared") and results
                or (row["state"] == "superseded") != (row["retired_at"] is not None and values._digest(row["retirement_digest"]))
                or row["state"] != "superseded" and (row["retired_at"] is not None or row["retirement_digest"] is not None)):
                raise _inconsistent()
            validate_utc_timestamp(row["created_at"], field="review continuation creation time")
            if row["registered_at"] is not None:
                validate_utc_timestamp(row["registered_at"], field="review continuation registration time")
            if row["retired_at"] is not None:
                validate_utc_timestamp(row["retired_at"], field="review continuation retirement time")
            predecessor = by_id.get(row["predecessor_id"])
            if (row["sequence"] == 1 and row["predecessor_id"] is not None
                or row["sequence"] > 1 and (predecessor is None or predecessor.sequence + 1 != row["sequence"]
                    or predecessor.preparation_id != row["preparation_id"] or predecessor.basis != basis
                    or predecessor.route != row["route"] or predecessor.state not in ("registered", "superseded"))):
                raise _inconsistent()
            record = Continuation(row["continuation_id"], row["preparation_id"], row["sequence"],
                row["predecessor_id"], basis, row["route"], row["state"], selected, tuple(results),
                row["created_at"], row["registered_at"], row["retired_at"], row["retirement_digest"])
            by_id[record.continuation_id] = record
            result.append(record)
        except TaskValidationError as exc:
            raise _inconsistent() from exc
    if len({row.predecessor_id for row in result if row.predecessor_id is not None}) != sum(row.predecessor_id is not None for row in result):
        raise _inconsistent()
    children = {row.predecessor_id for row in result if row.predecessor_id is not None}
    if any(row.state == "superseded" and row.continuation_id not in children for row in result):
        raise _inconsistent()
    validate_retained_storage(connection, by_id)
    pending = [row for row in result if row.state in ("preparing", "prepared")]
    if len({row.basis["target_generation"] for row in pending}) != len(pending):
        raise _inconsistent()
    return tuple(result)


def read_retained_originals(connection, continuation_id):
    return tuple(dict(row) for row in connection.execute("SELECT * FROM review_continuation_retained_originals WHERE continuation_id=? ORDER BY session_id", (continuation_id,)))


def validate_retained_storage(connection, by_id):
    from .review_session_transport import continued_metadata_digest
    # The existing composite primary key bounds each read to a known consumer.
    # Keep every historical consumer; neither current generation nor page size
    # may narrow this inventory. Reuse it for slot counts instead of rereading.
    retained = {identifier: read_retained_originals(connection, identifier)
                for identifier in sorted(by_id)}
    consumed = set()
    from .storage import current_schema_version
    if current_schema_version(connection) >= 30:
        from .review_answer_repository import validate_capture_relations
        consumed.update(validate_capture_relations(connection,by_id))
    for row in (row for rows in retained.values() for row in rows):
        capture = values.RetainedOriginal(**{key: row[key] for key in values.RetainedOriginal.__dataclass_fields__})
        consumer, old = by_id[row["continuation_id"]], by_id.get(capture.prior_continuation_id)
        chain = set()
        parent = consumer.predecessor_id
        while parent is not None:
            if parent in chain or parent not in by_id:
                raise _inconsistent()
            chain.add(parent)
            parent = by_id[parent].predecessor_id
        choice = next((value for value in old.selected if value.session_id == capture.session_id), None) if old else None
        if (old is None or old.state != "superseded" or old.continuation_id not in chain or old.basis != consumer.basis
            or choice is None or capture.session_id in {value.session_id for value in consumer.selected}
            or len(consumer.selected) + len(retained[consumer.continuation_id]) > 8
            or continued_metadata_digest(old, choice, capture.original_result_digest) != capture.metadata_digest):
            raise _inconsistent()
        result = row["result_receipt_id"]
        if result is not None:
            proof = _session_proof(connection, result, basis=old.basis, session_id=capture.session_id)
            if (consumer.state != "registered" or proof["continuation_id"] != old.continuation_id
                or proof["original_result_digest"] != capture.original_result_digest
                or (capture.session_id, result, capture.original_result_digest) not in old.results):
                raise _inconsistent()
            consumed.add(result)
        elif consumer.state == "registered":
            raise _inconsistent()
    for record in by_id.values():
        if record.state == "superseded" and any(receipt not in consumed for _, receipt, _ in record.results):
            raise _inconsistent()


def read_judgments(connection: sqlite3.Connection, *, basis: dict, _validated_continuations=None) -> tuple[values.Judgment, ...]:
    """Read the complete exact-generation graph from validated native sources.

    An advanced continuation requires the retained native preparation basis;
    this is not a legacy-evidence upgrader or an audit-page selector.
    """
    from .evidence_validation_repository import validate_selected_task_receipt_evidence
    from .review_session_repository import read_bindings
    from .storage import COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
    basis = values.validate_basis(basis)
    rows = connection.execute("""SELECT receipt.*, judgment.judgment_revision AS metadata_revision,
judgment.prior_receipt_id,judgment.continuation_id,reference.contract_revision AS reference_revision,
reference.authority_snapshot_id,reference.acceptance_criterion_id,reference.verification_criterion_id
FROM review_receipts AS receipt LEFT JOIN review_receipt_judgments AS judgment
ON judgment.review_receipt_id=receipt.review_receipt_id
LEFT JOIN evidence_references AS reference ON reference.source_id=receipt.review_receipt_id
AND reference.source_kind='review_receipt' AND reference.source_state='recorded'
WHERE receipt.project_id=? AND receipt.task_id=? AND receipt.target_generation=?
ORDER BY receipt.review_receipt_id""", (basis["project_id"], basis["task_id"], basis["target_generation"])).fetchall()
    ids = [row["review_receipt_id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise _inconsistent()
    from .review_material_repository import review_materials
    if ids:
        with review_materials(connection, project_id=basis["project_id"], task_id=basis["task_id"]) as materials:
            for offset in range(0, len(ids), COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE):
                validate_selected_task_receipt_evidence(connection, project_id=basis["project_id"], task_id=basis["task_id"],
                    review_receipt_ids=set(ids[offset:offset + COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE]),
                    review_finding_ids=set(), verification_receipt_ids=set(), _materials=materials)
    bindings = read_bindings(connection, receipt_ids=set(ids))
    result = []
    for row in rows:
        if row["judgment_revision"] != row["metadata_revision"]:
            raise _inconsistent()
        quality = dict(basis)
        quality.update(contract_revision=row["reference_revision"], authority_snapshot_id=row["authority_snapshot_id"],
            acceptance_criterion_id=row["acceptance_criterion_id"], verification_criterion_id=row["verification_criterion_id"],
            **{"target_" + key: row["target_" + key] for key in ("kind", "value", "base_revision", "generation")})
        try:
            quality = values.validate_basis(quality)
            binding = bindings.get(row["review_receipt_id"])
            result.append(values.Judgment(row["review_receipt_id"], row["reviewer_key"],
                binding.session_id if binding else None, row["judgment_revision"], row["prior_receipt_id"],
                row["continuation_id"], row["verdict"], values.quality_identity(quality)))
        except TaskValidationError as exc:
            raise _inconsistent() from exc
    history = _validated_continuations if _validated_continuations is not None else read_continuations(connection,
        project_id=basis["project_id"], task_id=basis["task_id"])
    records = {row.continuation_id: row for row in history}
    for node in result:
        if node.continuation_id is None:
            continue
        record = records.get(node.continuation_id)
        if (record is None or record.state not in ("registered", "superseded") or values.quality_identity(record.basis) != node.quality_basis
            or not any((session, receipt) == (node.session_id, node.receipt_id) for session, receipt, digest in record.results)):
            raise _inconsistent()
    values.select_effective(tuple(result), quality_basis=values.quality_identity(basis))
    return tuple(result)


def require_current_basis(connection: sqlite3.Connection, basis: dict, caller: CallerIdentity) -> None:
    from .storage import _read_validated_current_task_row
    from .task_ownership import read_basis
    from .verification_declaration import verification_requirement
    _writer(connection)
    basis = values.validate_basis(basis)
    task = _read_validated_current_task_row(connection, project_id=basis["project_id"], task_id=basis["task_id"])
    if task is None or task["status"] == "done":
        raise validation_error("review_continuation_basis_changed", "review continuation basis changed")
    expected = {"contract_revision": task["current_contract_revision"],
        "authority_snapshot_id": task["review_target_authority_snapshot_id"],
        "acceptance_criterion_id": task["review_target_acceptance_criterion_id"],
        "verification_criterion_id": task["review_target_verification_criterion_id"],
        "artifact_manifest_id": task["review_target_artifact_manifest_id"],
        "review_tier": task["review_tier"],
        "verification_requirement": verification_requirement(task["verification"], task["verification_not_required_reason"]),
        "verification_not_required_reason": task["verification_not_required_reason"],
        "completion_commit_required": task["completion_commit_required"],
        **{"target_" + key: task["review_target_" + key] for key in ("kind", "value", "base_revision", "generation")}}
    ownership = read_basis(connection, project_id=basis["project_id"], task_id=basis["task_id"])
    project = connection.execute("SELECT canonical_path_hash,binding_generation FROM project_meta WHERE project_id=?", (basis["project_id"],)).fetchone()
    if project is None or (project["canonical_path_hash"], project["binding_generation"]) != (basis["project_path_hash"], basis["project_binding_generation"]):
        raise validation_error("review_continuation_basis_changed", "review continuation project binding changed")
    if any(basis[key] != value for key, value in expected.items()):
        raise validation_error("review_continuation_basis_changed", "review continuation basis changed")
    if (ownership.state not in ("owned", "completion_only") or ownership.execution_id != basis["execution_id"]
        or ownership.generation != basis["ownership_generation"]
        or (ownership.owner_session_id or ownership.completion_session_id) != basis["parent_session_id"]
        or caller.require() != basis["parent_session_id"]):
        raise validation_error("task_ownership_changed", "review continuation ownership changed")


def admit_locked(connection: sqlite3.Connection, *, basis: dict, preparation_id: str,
                 route: str, selected: tuple[values.SelectedReviewer, ...],
                 caller: CallerIdentity, retirement: values.RetirementProof | None = None,
                 retained_originals: tuple[values.RetainedOriginal, ...] = (),
                 retained_collected: tuple = (),
                 continuation_id: str | None = None, rejection_audit=None) -> Continuation:
    """Admit one explicit expected-head selection; replay reconciles, never republishes."""
    from .storage import utc_now, current_schema_version
    require_current_basis(connection, basis, caller)
    rejected_actors = set()
    if rejection_audit is not None:
        from .review_answer_rejection_repository import require_absent_locked
        require_absent_locked(connection, basis, preparation_id, selected, rejection_audit)
        rejected_actors = {s["reviewer_session_id"] for s in rejection_audit["sources"] if s["rejection"] is not None}
    if type(retained_collected) is not tuple:
        raise values._invalid()
    collected = []
    if retained_collected:
        from .review_answer_values import validate_collected_capture, validate_metadata, metadata_digest
        from .review_answer import AnswerError
        try:
            for pair in retained_collected:
                if type(pair) is not tuple or len(pair) != 2:
                    raise values._invalid()
                capture, metadata = validate_collected_capture(pair[0]), validate_metadata(pair[1])
                if capture["metadata_digest"] != metadata_digest(metadata):
                    raise values._invalid()
                collected.append((capture, metadata))
        except AnswerError:
            raise values._invalid() from None
    collected_actors = {capture["session_id"] for capture, _ in collected}
    if len(collected_actors) != len(collected):
        raise values._invalid()
    if continuation_id is not None and not values.identifier(continuation_id, "tg_review_continuation_"):
        raise values._invalid()
    selected = values.validate_selection({"version": 1, "reviewers": [row.as_dict() for row in selected]})
    if any(row.session_id == basis["parent_session_id"] for row in selected):
        raise values._invalid()
    if not values.identifier(preparation_id, "tg_review_preparation_") or route not in ("manual", "integrated"):
        raise values._invalid()
    records = read_continuations(connection, project_id=basis["project_id"], task_id=basis["task_id"])
    pending = [row for row in records if row.basis["target_generation"] == basis["target_generation"] and row.state in ("preparing", "prepared")]
    if pending:
        row = pending[0]
        if retirement is None and row.preparation_id == preparation_id and row.basis == basis and row.route == route and row.selected == selected:
            if continuation_id is not None and row.continuation_id != continuation_id:
                raise validation_error("review_continuation_conflict", "review continuation identity changed")
            return row
        if (type(retirement) is not values.RetirementProof or retirement.continuation_id != row.continuation_id
            or retirement.basis_digest != values.basis_digest(basis) or row.basis != basis
            or row.preparation_id != preparation_id or row.route != route):
            raise validation_error("review_continuation_conflict", "a different review continuation is pending")
    elif retirement is not None or retained_originals:
        raise validation_error("review_continuation_conflict", "review continuation retirement has no pending predecessor")
    if (type(retained_originals) is not tuple or len(selected) + len(retained_originals) + len(collected) > 8
        or any(type(value) is not values.RetainedOriginal for value in retained_originals)
        or len({value.session_id for value in retained_originals}) != len(retained_originals)
        or {value.session_id for value in selected} & {value.session_id for value in retained_originals}
        or collected_actors & ({value.session_id for value in selected} | {value.session_id for value in retained_originals})):
        raise values._invalid()
    retained_order = sorted({value.session_id for value in retained_originals} | collected_actors)
    if any(capture["consumer_slot"] != len(selected) + retained_order.index(capture["session_id"]) + 1
           for capture, _ in collected):
        raise values._invalid()
    if pending:
        prior_captures = {values.RetainedOriginal(**{key: row[key] for key in values.RetainedOriginal.__dataclass_fields__})
            for row in read_retained_originals(connection, pending[0].continuation_id)}
        from .review_answer_repository import read_native_collected_captures
        prior_collected = read_native_collected_captures(connection, pending[0].continuation_id) if collected or current_schema_version(connection) >= 30 else ()
        transferred = {(capture["session_id"], capture["metadata_digest"]) for capture, _ in collected}
        if any((capture["session_id"], capture["metadata_digest"]) not in transferred
               and capture["session_id"] not in rejected_actors for capture, _ in prior_collected):
            raise validation_error("review_continuation_conflict", "review continuation would lose a collected original")
        actors = {row.session_id for row in selected} | {row.session_id for row in retained_originals} | collected_actors
        if (not prior_captures <= set(retained_originals)
            or len(selected) + len(retained_originals) + len(collected) < len(pending[0].selected) + len(prior_captures) + len(prior_collected)
            or any(row.prior_receipt_id is not None and row.session_id not in actors for row in pending[0].selected)):
            raise validation_error("review_continuation_conflict", "review continuation would lose a required original or reassessment")
    judgments = read_judgments(connection, basis=basis)
    by_id = {node.receipt_id: node for node in judgments}
    for choice in selected:
        if choice.prior_receipt_id in by_id and by_id[choice.prior_receipt_id].session_id is None:
            raise validation_error("review_continuation_unavailable", "the original reviewer session is not bound")
    try:
        values.select_effective(judgments, quality_basis=values.quality_identity(basis), pending=selected)
    except TaskValidationError as exc:
        raise validation_error("review_continuation_conflict", "review continuation selection does not match current heads") from exc
    previous = [row for row in records if row.preparation_id == preparation_id]
    predecessor = previous[-1] if previous else None
    if predecessor is not None and (predecessor.basis != basis or predecessor.route != route):
        raise validation_error("review_continuation_basis_changed", "review continuation basis changed")
    sequence = predecessor.sequence + 1 if predecessor else 1
    if sequence > values.CONTINUATION_LIMIT:
        raise validation_error("review_continuation_capacity", "review continuation capacity reached")
    if pending:
        connection.execute("UPDATE review_continuations SET state='superseded',retired_at=?,retirement_digest=? WHERE continuation_id=?",
            (utc_now(), retirement.digest(), pending[0].continuation_id))
    continuation = continuation_id or "tg_review_continuation_" + secrets.token_hex(8)
    if any(capture["consumer_continuation_id"] != continuation for capture, _ in collected):
        raise values._invalid()
    connection.execute("""INSERT INTO review_continuations
(continuation_id,project_id,task_id,target_generation,preparation_id,sequence,predecessor_id,
basis_json,basis_digest,selection_digest,route,state,created_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,'preparing',?)""", (continuation, basis["project_id"], basis["task_id"],
        basis["target_generation"], preparation_id, sequence, predecessor.continuation_id if predecessor else None,
        canonical_json_bytes(basis).decode(), values.basis_digest(basis), values.selection_digest(selected), route, utc_now()))
    for row in selected:
        connection.execute("""INSERT INTO review_continuation_reviewers
(continuation_id,session_id,action,prior_receipt_id,reason) VALUES (?,?,?,?,?)""",
            (continuation, row.session_id, row.action, row.prior_receipt_id, row.reason))
    for original in retained_originals:
        connection.execute("INSERT INTO review_continuation_retained_originals VALUES (?,?,?,?,?,NULL)",
            (continuation, original.prior_continuation_id, original.session_id, original.original_result_digest, original.metadata_digest))
    if collected:
        from .review_answer_repository import capture_collected_locked
        for capture, metadata in sorted(collected, key=lambda pair: pair[0]["session_id"]):
            capture_collected_locked(connection, capture=capture, metadata=metadata)
    return next(row for row in read_continuations(connection, project_id=basis["project_id"], task_id=basis["task_id"])
                if row.continuation_id == continuation)


def advance_prepared_locked(connection: sqlite3.Connection, record: Continuation,
                            caller: CallerIdentity) -> Continuation:
    require_current_basis(connection, record.basis, caller)
    rows = read_continuations(connection, project_id=record.basis["project_id"], task_id=record.basis["task_id"])
    current = next((row for row in rows if row.continuation_id == record.continuation_id), None)
    if (current is None or current.basis != record.basis or current.selected != record.selected
        or current.state not in ("preparing", "prepared", "registered")):
        raise validation_error("review_continuation_conflict", "review continuation changed")
    if current.state == "preparing":
        connection.execute("UPDATE review_continuations SET state='prepared' WHERE continuation_id=?", (current.continuation_id,))
    return next(row for row in read_continuations(connection, project_id=record.basis["project_id"], task_id=record.basis["task_id"])
                if row.continuation_id == current.continuation_id)


def append_judgment_locked(connection: sqlite3.Connection, *, receipt_id: str, revision: int = 1,
                           continuation_id: str | None = None, prior_receipt_id: str | None = None) -> None:
    _writer(connection)
    # The deferred reciprocal FK prevents committing a Receipt without this row.
    # Source validation and actual session insertion precede this SQL guard.
    connection.execute("INSERT INTO review_receipt_judgments VALUES (?,?,?,?)",
                       (receipt_id, revision, prior_receipt_id, continuation_id))


def publish_result_locked(connection: sqlite3.Connection, *, record: Continuation,
                          session_id: str, receipt_id: str, original_digest: str) -> None:
    _writer(connection)
    if not values._digest(original_digest):
        raise values._invalid()
    changed = connection.execute("""UPDATE review_continuation_reviewers
SET result_receipt_id=?,original_result_digest=? WHERE continuation_id=? AND session_id=?
AND result_receipt_id IS NULL""", (receipt_id, original_digest, record.continuation_id, session_id)).rowcount
    if changed != 1:
        row = connection.execute("SELECT result_receipt_id,original_result_digest FROM review_continuation_reviewers WHERE continuation_id=? AND session_id=?",
                                 (record.continuation_id, session_id)).fetchone()
        if row is None or tuple(row) != (receipt_id, original_digest):
            raise validation_error("review_continuation_conflict", "review continuation result changed")


def advance_registered_locked(connection: sqlite3.Connection, record: Continuation) -> None:
    from .storage import utc_now
    _writer(connection)
    # SQL admits only a complete result set; the outer batch owns atomicity.
    connection.execute("UPDATE review_continuations SET state='registered',registered_at=? WHERE continuation_id=? AND state='prepared'",
                       (utc_now(), record.continuation_id))


def resolution_digest(*, finding_id: str, successor_id: str, continuation_id: str,
                      summary: str, original_resolution: str, resolved_at: str) -> str:
    return domain_digest(b"taskgov-review-reassessment-resolution-v1\0", dict(
        review_finding_id=finding_id, successor_receipt_id=successor_id, continuation_id=continuation_id,
        resolution_summary=summary, original_resolution_summary=original_resolution,
        original_resolved_at=resolved_at)).removeprefix("sha256:")


def append_resolution_locked(connection: sqlite3.Connection, *, finding_id: str,
                             successor_id: str, summary: str, _materials=None) -> dict:
    from .storage import utc_now
    _writer(connection)
    summary = validate_text("review_finding_resolution", summary, required=True, limit=1000)
    finding = connection.execute("SELECT * FROM review_findings WHERE review_finding_id=?", (finding_id,)).fetchone()
    successor = connection.execute("SELECT continuation_id FROM review_receipt_judgments WHERE review_receipt_id=?", (successor_id,)).fetchone()
    if finding is None or finding["status"] != "resolved" or successor is None or successor["continuation_id"] is None:
        raise values._invalid()
    from .evidence_validation_repository import validate_selected_task_receipt_evidence
    owner = connection.execute("SELECT project_id,task_id FROM review_receipts WHERE review_receipt_id=?", (finding["review_receipt_id"],)).fetchone()
    if owner is None:
        raise _inconsistent()
    validate_selected_task_receipt_evidence(connection, project_id=owner["project_id"], task_id=owner["task_id"],
        review_receipt_ids={successor_id}, review_finding_ids={finding_id}, verification_receipt_ids=set(),
        _materials=_materials)
    continuation = successor["continuation_id"]
    digest = resolution_digest(finding_id=finding_id, successor_id=successor_id, continuation_id=continuation,
        summary=summary, original_resolution=finding["resolution_summary"], resolved_at=finding["resolved_at"])
    old = connection.execute("SELECT * FROM review_reassessment_resolutions WHERE review_finding_id=? AND successor_receipt_id=?",
                             (finding_id, successor_id)).fetchone()
    if old is not None:
        if old["resolution_digest"] != digest:
            raise validation_error("review_continuation_conflict", "review reassessment resolution changed")
        return dict(old)
    connection.execute("INSERT INTO review_reassessment_resolutions VALUES (?,?,?,?,?,?)",
                       (finding_id, successor_id, continuation, summary, digest, utc_now()))
    return dict(connection.execute("SELECT * FROM review_reassessment_resolutions WHERE review_finding_id=? AND successor_receipt_id=?",
                                   (finding_id, successor_id)).fetchone())


def pending_for_session(connection, *, project_id, task_id, generation, session_id) -> bool:
    return connection.execute("""SELECT 1 FROM review_continuations AS continuation
JOIN review_continuation_reviewers AS selected ON selected.continuation_id=continuation.continuation_id
WHERE continuation.project_id=? AND continuation.task_id=? AND continuation.target_generation=?
AND continuation.state IN ('preparing','prepared') AND selected.session_id=?
UNION SELECT 1 FROM review_continuations AS continuation
JOIN review_continuation_retained_originals AS retained ON retained.continuation_id=continuation.continuation_id
WHERE continuation.project_id=? AND continuation.task_id=? AND continuation.target_generation=?
AND continuation.state IN ('preparing','prepared') AND retained.session_id=? LIMIT 1""",
        (project_id, task_id, generation, session_id,project_id,task_id,generation,session_id)).fetchone() is not None


def resolution_link_exists(connection, finding_id, successor_id) -> bool:
    return connection.execute("SELECT 1 FROM review_reassessment_resolutions WHERE review_finding_id=? AND successor_receipt_id=?",
                              (finding_id, successor_id)).fetchone() is not None


def validate_judgment_storage(connection, *, review_task=None) -> None:
    """Validate immutable lineage in the admitted global or completing-Task scope."""
    # Whole-store admission acquires the continuation inventory once, then uses
    # the same per-Task validator as ordinary reads. No persistent cache or
    # cross-transaction validation result is introduced.
    histories = {}
    selection = " WHERE task_id=?" if review_task is not None else ""
    parameters = (review_task[1],) if review_task is not None else ()
    for row in connection.execute("SELECT * FROM review_continuations" + selection + " ORDER BY preparation_id,sequence", parameters):
        if review_task is not None and (row["project_id"], row["task_id"]) != review_task:
            raise _inconsistent()
        histories.setdefault((row["project_id"], row["task_id"]), []).append(row)
    all_records = {}
    for task in connection.execute("SELECT project_id,task_id FROM tasks" + selection + " ORDER BY task_id", parameters):
        all_records.update({record.continuation_id: record for record in _validate_continuations(
            connection, histories.pop((task["project_id"], task["task_id"]), ()),
            project_id=task["project_id"], task_id=task["task_id"])})
    cursor = connection.execute("""SELECT receipt.review_receipt_id,receipt.judgment_revision,
receipt.project_id,receipt.task_id,
judgment.judgment_revision AS metadata_revision,judgment.prior_receipt_id,judgment.continuation_id
FROM review_receipts AS receipt LEFT JOIN review_receipt_judgments AS judgment
ON judgment.review_receipt_id=receipt.review_receipt_id""" +
        (" WHERE receipt.task_id=?" if review_task is not None else "") + " ORDER BY receipt.review_receipt_id", parameters)
    from .storage import COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
    nodes = [row for chunk in iter(lambda: cursor.fetchmany(COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE), []) for row in chunk]
    expected_results = {receipt: (record, session, digest) for record in all_records.values()
                        for session, receipt, digest in record.results}
    observed_results = set()
    successor_ids = set()
    by_id = {row["review_receipt_id"]: row for row in nodes}
    for row in nodes:
        if review_task is not None and (row["project_id"], row["task_id"]) != review_task:
            raise _inconsistent()
        if (row["metadata_revision"] != row["judgment_revision"] or type(row["judgment_revision"]) is not int
            or row["judgment_revision"] < 1):
            raise _inconsistent()
        prior_id, continuation_id = row["prior_receipt_id"], row["continuation_id"]
        if continuation_id is None:
            if prior_id is not None or row["judgment_revision"] != 1:
                raise _inconsistent()
            continue
        expected = expected_results.get(row["review_receipt_id"])
        if expected is None or expected[0].continuation_id != continuation_id or expected[0].state not in ("registered", "superseded"):
            raise _inconsistent()
        record, session, digest = expected
        actual = _session_proof(connection, row["review_receipt_id"], basis=record.basis, session_id=session)
        if actual["original_result_digest"] != digest:
            raise _inconsistent()
        observed_results.add(row["review_receipt_id"])
        if prior_id is None:
            if row["judgment_revision"] != 1:
                raise _inconsistent()
            continue
        if prior_id in successor_ids or prior_id not in by_id:
            raise _inconsistent()
        successor_ids.add(prior_id)
        prior = _session_proof(connection, prior_id, basis=record.basis, session_id=session)
        if (prior["judgment_revision"] + 1 != row["judgment_revision"] or prior["reviewer_key"] != actual["reviewer_key"]):
            raise _inconsistent()
    if observed_results != set(expected_results):
        raise _inconsistent()
