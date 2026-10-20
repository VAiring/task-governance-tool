"""Transaction-bound collected evidence; no transport/host I/O or commits."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .review_session_repository import ReviewSessionBinding, ReviewSessionTarget


@dataclass(frozen=True)
class CollectedReceiptInput:
    metadata: dict
    answer: object
    submission_digest: str


@dataclass(frozen=True)
class CollectedReviewEndpoint:
    """Registered answer identity for read-only consumers, without its body."""

    receipt_id: str
    turn_id: str
    target: ReviewSessionTarget
    binding: ReviewSessionBinding


@dataclass(frozen=True)
class _UsageReviewInputs:
    """Body-free inputs owned by one usage capture's admitted core read.

    These are local values, not a cache or an admission token. Callers must not
    retain them on a repository/connection or reuse them in a later read.
    """

    reviews: tuple[tuple[str, ReviewSessionTarget, ReviewSessionBinding], ...]
    collected_endpoints: tuple[CollectedReviewEndpoint, ...]


def _bad():
    from .storage import evidence_ledger_inconsistent
    return evidence_ledger_inconsistent()


def _canonical(value, limit):
    from .evidence_ledger import canonical_json_bytes
    raw = canonical_json_bytes(value)
    if not 1 <= len(raw) <= limit:
        raise _bad()
    return raw.decode("utf-8")


def _decode(raw, limit):
    # Requiring byte equality with the canonical codec rejects duplicate keys,
    # noncanonical encodings, floats and non-finite values.
    value = json.loads(raw)
    if _canonical(value, limit) != raw:
        raise _bad()
    return value


def _target(receipt):
    return {"kind":receipt["target_kind"], "value":receipt["target_value"],
            "base_revision":receipt["target_base_revision"],
            "generation":receipt["target_generation"], "capture_version":1}


def validate_provenance_relation(connection, receipt, *, row=None):
    from .review_answer_provenance import validate_provenance_v2
    from .review_answer import AnswerError
    if row is None:
        row = connection.execute("SELECT * FROM review_receipt_provenance_v2 WHERE review_receipt_id=?", (receipt["review_receipt_id"],)).fetchone()
    if row is None or receipt["receipt_kind"] != "independent" or receipt["review_provenance_basis_version"] != 2 or row["review_provenance_id"] != receipt["review_provenance_id"]:
        raise _bad()
    try:
        value = _decode(row["provenance_json"],262144)
        value = validate_provenance_v2(value, project_id=receipt["project_id"], task_id=receipt["task_id"],
            review_receipt_id=receipt["review_receipt_id"],receipt_kind=receipt["receipt_kind"],target=_target(receipt))
    except (ValueError,TypeError,KeyError,AnswerError) as exc:
        raise _bad() from exc
    # The contextual digest excludes the random ID; bind its embedded value
    # to the native relation as well as the Receipt before projecting it.
    if value["review_provenance_id"] != row["review_provenance_id"]:
        raise _bad()
    payload = value["answer_attestation"]["payload"]
    if value["digest"] != row["digest"] or payload["verdict"] != receipt["verdict"] or payload["summary"].strip() != receipt["summary"]:
        raise _bad()
    source = connection.execute("SELECT * FROM review_collection_sources WHERE review_receipt_id=?", (receipt["review_receipt_id"],)).fetchone()
    from .review_provenance import DIGEST_PATTERN
    observed = value["host_observation"]
    original = value["answer_attestation"]["original"]
    if source is None or type(source["submission_digest"]) is not str or not DIGEST_PATTERN.fullmatch(source["submission_digest"]) or any(type(source[key]) is not type(observed[key]) or source[key] != observed[key] for key in ("preparation_id","slot","attempt","reviewer_session_id","turn_id","message_id","basis_digest")) or source["original_digest"] != original["digest"] or source["payload_digest"] != original["payload_digest"]:
        raise _bad()
    from .review_answer_values import validate_basis, basis_digest
    basis=validate_basis(_decode(source["basis_json"],32768))
    if (basis_digest(basis)!=source["basis_digest"] or basis["preparation_id"]!=source["preparation_id"]
        or basis["version"]!=observed["binding_source"]["version"]
        or any(basis[key]!=value["answer_attestation"][key] for key in ("protocol","attestation","request_digest"))):
        raise _bad()
    execution=basis["execution_basis"]
    if (execution["project_id"]!=receipt["project_id"] or execution["task_id"]!=receipt["task_id"]
        or execution["parent_session_id"]!=observed["parent_session_id"]
        or any(execution["target_"+key]!=receipt["target_"+key] for key in ("kind","value","base_revision","generation"))):
        raise _bad()
    reference=connection.execute("SELECT * FROM evidence_references WHERE source_kind='review_receipt' AND source_id=?",(receipt["review_receipt_id"],)).fetchone()
    if reference is not None and any(execution[key]!=reference[key] for key in ("contract_revision","authority_snapshot_id","acceptance_criterion_id","verification_criterion_id")):
        raise _bad()
    manifest=connection.execute("SELECT * FROM artifact_manifests WHERE artifact_manifest_id=?",(execution["artifact_manifest_id"],)).fetchone()
    if manifest is None or any(manifest[key]!=execution[key] for key in ("project_id","task_id","authority_snapshot_id","acceptance_criterion_id","verification_criterion_id","target_kind","target_value","target_base_revision","target_generation")):
        raise _bad()
    session=connection.execute("SELECT * FROM review_receipt_sessions WHERE review_receipt_id=?",(receipt["review_receipt_id"],)).fetchone()
    if session is not None and (session["session_id"]!=observed["reviewer_session_id"] or session["execution_id"]!=execution["execution_id"] or session["binding_source"]!="handoff" or "sha256:"+session["original_result_digest"]!=source["original_digest"]):
        raise _bad()
    return value


def read_collected_review_endpoints(connection, *, receipt_ids):
    """Project only registered schema-30+ answers from an admitted core read.

    Operational captures and parent registration turns are not endpoints.
    Validate the saved Receipt/source/provenance/session relation before
    discarding the answer body; no numerical write or host access occurs.
    """
    rows = connection.execute(
        "SELECT * FROM review_receipts WHERE review_provenance_basis_version=2 "
        "AND review_receipt_id IN (SELECT value FROM json_each(?)) ORDER BY review_receipt_id",
        (json.dumps(sorted(receipt_ids)),),
    ).fetchall()
    reviews = _read_usage_reviews(connection, {row["review_receipt_id"] for row in rows})
    return _collected_usage_endpoints(connection, rows, reviews)


def _read_usage_reviews(connection, receipt_ids):
    from .review_session_repository import read_bindings, _receipt_target

    # Preserve the complete binding/target/alias validation performed by both
    # existing readers. Only repeated calls from one capture are eliminated.
    bindings = read_bindings(connection, receipt_ids=receipt_ids)
    return tuple((receipt_id, _receipt_target(connection, receipt_id)[0], binding)
                 for receipt_id, binding in bindings.items())


def _collected_usage_endpoints(connection, rows, reviews):
    """Private projection; inputs come only from this module's fresh readers."""
    validated = {receipt_id: (target, binding) for receipt_id, target, binding in reviews}
    result = []
    for row in rows:
        receipt_id = row["review_receipt_id"]
        provenance = validate_provenance_relation(connection, row)
        target, binding = validated.get(receipt_id, (None, None))
        host = provenance["host_observation"]
        original = provenance["answer_attestation"]["original"]["digest"]
        if (binding is None or binding.binding_source != "handoff"
                or binding.session_id != host["reviewer_session_id"]
                or "sha256:" + binding.original_result_digest != original):
            raise _bad()
        result.append(CollectedReviewEndpoint(receipt_id, host["turn_id"], target, binding))
    return tuple(result)


def _read_usage_review_inputs(connection, *, project_id):
    """Read once for one capture; never expose an unvalidated-input option.

    The owning usage capture passes the returned typed values explicitly to
    its basis and partition calculations before leaving this core transaction.
    Standalone readers and later publication guards make fresh reads instead.
    """
    from .storage import current_schema_version

    if not connection.in_transaction:
        raise RuntimeError("usage review inputs require an existing core read")
    version = current_schema_version(connection)
    if version < 25:
        return _UsageReviewInputs((), ())
    rows = connection.execute(
        "SELECT * FROM review_receipts WHERE project_id=? ORDER BY review_receipt_id",
        (project_id,),
    ).fetchall()
    reviews = _read_usage_reviews(connection, {row["review_receipt_id"] for row in rows})
    collected = [row for row in rows if row["review_provenance_basis_version"] == 2] if version >= 30 else []
    return _UsageReviewInputs(reviews, _collected_usage_endpoints(connection, collected, reviews))


def insert_provenance_locked(connection, *, receipt, provenance, collected):
    from .storage import _require_evidence_writer
    from .review_answer_values import validate_metadata
    from .review_answer_provenance import validate_provenance_v2
    from .review_provenance import DIGEST_PATTERN
    _require_evidence_writer(connection)
    if type(collected) is not CollectedReceiptInput or not isinstance(collected.submission_digest,str) or not DIGEST_PATTERN.fullmatch(collected.submission_digest):
        raise _bad()
    metadata = validate_metadata(collected.metadata,raw=collected.answer.raw)
    validated = validate_provenance_v2(provenance, project_id=receipt["project_id"],task_id=receipt["task_id"],
        review_receipt_id=receipt["review_receipt_id"],receipt_kind=receipt["receipt_kind"],target=_target(receipt))
    basis = metadata["basis"]["execution_basis"]
    # The caller has already acquired the owner writer. This comparison uses
    # the exact current native basis and never trusts operational metadata alone.
    task = connection.execute("SELECT * FROM tasks WHERE task_id=? AND project_id=?", (receipt["task_id"],receipt["project_id"])).fetchone()
    if task is None or any(basis[key] != expected for key,expected in (
        ("project_id",receipt["project_id"]),("task_id",receipt["task_id"]),
        ("contract_revision",task["current_contract_revision"]),
        ("target_kind",receipt["target_kind"]),("target_value",receipt["target_value"]),
        ("target_base_revision",receipt["target_base_revision"]),("target_generation",receipt["target_generation"]),
        ("artifact_manifest_id",task["review_target_artifact_manifest_id"]),
        ("authority_snapshot_id",task["review_target_authority_snapshot_id"]),
        ("acceptance_criterion_id",task["review_target_acceptance_criterion_id"]),
        ("verification_criterion_id",task["review_target_verification_criterion_id"]),
    )):
        raise _bad()
    connection.execute("INSERT INTO review_receipt_provenance_v2(review_receipt_id,review_provenance_id,provenance_json,digest) VALUES (?,?,?,?)", (receipt["review_receipt_id"],validated["review_provenance_id"],_canonical(validated,262144),validated["digest"]))
    host = validated["host_observation"]
    original = validated["answer_attestation"]["original"]
    connection.execute("""INSERT INTO review_collection_sources(review_receipt_id,preparation_id,slot,attempt,reviewer_session_id,turn_id,message_id,basis_digest,basis_json,original_digest,payload_digest,submission_digest)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (receipt["review_receipt_id"],*(host[k] for k in ("preparation_id","slot","attempt","reviewer_session_id","turn_id","message_id","basis_digest")),_canonical(metadata["basis"],32768),original["digest"],original["payload_digest"],collected.submission_digest))


def validate_storage_rows(connection, *, review_task=None):
    # Completion admits its own review graph; global consumers also reject orphans.
    selected = " AND task_id=?" if review_task is not None else ""
    rows = connection.execute("SELECT receipt.* FROM review_receipts AS receipt WHERE review_provenance_basis_version=2" + selected + " ORDER BY review_receipt_id",
                              (review_task[1],) if review_task is not None else ())
    for row in rows:
        if review_task is not None and (row["project_id"], row["task_id"]) != review_task:
            raise _bad()
        provenance=validate_provenance_relation(connection,row)
        validate_payload_findings(connection,row["review_receipt_id"],provenance)
        if connection.execute("SELECT 1 FROM review_receipt_sessions WHERE review_receipt_id=?",(row["review_receipt_id"],)).fetchone() is None:
            raise _bad()
    own_receipt = "r.task_id=?" if review_task is not None else "1"
    own_parameters = (review_task[1], review_task[1]) if review_task is not None else ()
    if connection.execute("""SELECT 1 FROM review_receipt_provenance_v2 AS p
LEFT JOIN review_receipts AS r ON r.review_receipt_id=p.review_receipt_id
WHERE (r.review_receipt_id IS NULL OR r.review_provenance_basis_version!=2
       OR r.review_provenance_id!=p.review_provenance_id) AND """ + own_receipt + """
UNION ALL SELECT 1 FROM review_collection_sources AS s
LEFT JOIN review_receipt_provenance_v2 AS p ON p.review_receipt_id=s.review_receipt_id
LEFT JOIN review_receipts AS r ON r.review_receipt_id=s.review_receipt_id
WHERE p.review_receipt_id IS NULL AND """ + own_receipt + " LIMIT 1", own_parameters).fetchone():
        raise _bad()
    if connection.execute("SELECT 1 FROM review_receipt_provenance_v2 AS p JOIN review_receipt_provenance AS old ON old.review_provenance_id=p.review_provenance_id" +
                          (" WHERE EXISTS (SELECT 1 FROM review_receipts AS r WHERE r.task_id=? AND "
                           "(r.review_receipt_id=p.review_receipt_id OR r.review_provenance_id=p.review_provenance_id))" if review_task is not None else "") + " LIMIT 1",
                          (review_task[1],) if review_task is not None else ()).fetchone():
        raise _bad()
    # Include parents reached through own sources/entries even if their declared
    # owner was changed. Inspect the relation, never the foreign Task's graph.
    dispatch_scope = "" if review_task is None else """ WHERE dispatch.task_id=?
OR (dispatch.preparation_id,dispatch.submission_digest) IN (
  SELECT source.preparation_id,source.submission_digest
  FROM review_collection_sources AS source JOIN review_receipts AS receipt
    ON receipt.review_receipt_id=source.review_receipt_id WHERE receipt.task_id=?)
OR (dispatch.preparation_id,dispatch.submission_digest) IN (
  SELECT entry.preparation_id,entry.submission_digest
  FROM review_collection_dispatch_entries AS entry JOIN review_receipts AS receipt
    ON receipt.review_receipt_id=entry.review_receipt_id WHERE receipt.task_id=?)"""
    for dispatch in connection.execute("SELECT dispatch.* FROM review_collection_dispatches AS dispatch" +
            dispatch_scope + " ORDER BY preparation_id,submission_digest",
            (review_task[1],) * 3 if review_task is not None else ()):
        if review_task is not None and (dispatch["project_id"], dispatch["task_id"]) != review_task:
            raise _bad()
        registration = read_registration(connection,project_id=dispatch["project_id"],task_id=dispatch["task_id"],preparation_id=dispatch["preparation_id"],submission_digest=dispatch["submission_digest"])
        if (registration["status"] != "recorded" or any(
            (entry["project_id"], entry["task_id"]) != (dispatch["project_id"], dispatch["task_id"])
            for entry in registration["entries"]
        )):
            raise _bad()


def validate_payload_findings(connection, receipt_id, provenance):
    expected=sorted((f["severity"],f["summary"].strip()) for f in provenance["answer_attestation"]["payload"]["findings"])
    actual=sorted((r["severity"],r["summary"]) for r in connection.execute("SELECT severity,summary FROM review_findings WHERE review_receipt_id=?",(receipt_id,)))
    if expected!=actual:
        raise _bad()


DISPATCH_ENTRY_KEYS = {"slot","protocol","receipt_id","project_id","task_id","contract_revision","target",
    "source_preparation_id","source_continuation_id","reviewer_session_id","turn_id","message_id","basis_digest",
    "original_digest","payload_digest","submission_digest"}


def require_absent_registration_locked(connection, submission):
    """Settle current native effects under the existing admitted writer lock.

    The wrapper holds every exact source lease. Full native writer admission
    rejects orphan Receipt/provenance relations; these bounded reverse checks
    also reject partial dispatch/source residue before any original is adopted.
    This says nothing about effects before an unrecognized database restoration.
    """
    from .storage import _require_evidence_writer
    from .review_answer_transport import CollectedSubmission, CollectedItem, input_error
    _require_evidence_writer(connection)
    def unknown():
        raise input_error("registration_unknown", reason="effect_unknown")
    if (type(submission) is not CollectedSubmission or not submission.items
            or any(type(item) is not CollectedItem for item in submission.items)):
        unknown()
    selectors = (submission.preparation_id, submission.submission_digest)
    if connection.execute("""SELECT 1 FROM review_collection_dispatches WHERE preparation_id=? AND submission_digest=?
UNION ALL SELECT 1 FROM review_collection_dispatch_entries WHERE preparation_id=? AND submission_digest=?
UNION ALL SELECT 1 FROM review_collection_sources WHERE preparation_id=? AND submission_digest=? LIMIT 1""",
            selectors*3).fetchone() is not None:
        unknown()
    for item in submission.items:
        source = item.metadata["source_identity"]
        if connection.execute("SELECT 1 FROM review_collection_sources WHERE reviewer_session_id=? AND turn_id=? AND message_id=?",
                (source["reviewer_session_id"], source["turn_id"], source["message_id"])).fetchone() is not None:
            unknown()
    if submission.continuation is not None:
        continuation_id = submission.continuation["continuation_id"]
        row = connection.execute("SELECT state FROM review_continuations WHERE continuation_id=?", (continuation_id,)).fetchone()
        if row is None or row["state"] != "prepared" or connection.execute(
                """SELECT 1 FROM review_collected_captures WHERE continuation_id=? AND result_receipt_id IS NOT NULL
UNION ALL SELECT 1 FROM review_continuation_reviewers WHERE continuation_id=? AND result_receipt_id IS NOT NULL LIMIT 1""",
                (continuation_id, continuation_id)).fetchone() is not None:
            unknown()


def record_dispatch_locked(connection, *, preparation_id, submission_digest, project_id, task_id, entries):
    from .storage import _require_evidence_writer
    _require_evidence_writer(connection)
    if type(entries) is not list or not 1 <= len(entries) <= 8 or any(type(e) is not dict or type(e.get("slot")) is not int or not 1 <= e["slot"] <= 8 for e in entries) or [e["slot"] for e in entries] != sorted({e["slot"] for e in entries}):
        raise _bad()
    if any(set(entry) != DISPATCH_ENTRY_KEYS or entry["submission_digest"] != submission_digest or entry["project_id"] != project_id or entry["task_id"] != task_id for entry in entries):
        raise _bad()
    raw = _canonical(entries,32768)
    existing = connection.execute("SELECT * FROM review_collection_dispatches WHERE preparation_id=? AND submission_digest=?", (preparation_id,submission_digest)).fetchone()
    if existing is not None:
        if existing["inventory_json"] != raw or existing["project_id"] != project_id or existing["task_id"] != task_id:
            raise _bad()
        if read_registration(connection,project_id=project_id,task_id=task_id,preparation_id=preparation_id,submission_digest=submission_digest)["status"] != "recorded":
            raise _bad()
        return
    _validate_dispatch_entries(connection,entries)
    connection.execute("INSERT INTO review_collection_dispatches VALUES (?,?,?,?,?)", (preparation_id,submission_digest,project_id,task_id,raw))
    for entry in entries:
        connection.execute("INSERT INTO review_collection_dispatch_entries VALUES (?,?,?,?,?)",(preparation_id,submission_digest,entry["slot"],entry["receipt_id"],_canonical(entry,32768)))


def _validate_dispatch_entries(connection, entries):
    from .review_continuation import identifier
    from .review_provenance import DIGEST_PATTERN
    for entry in entries:
        if (type(entry) is not dict or set(entry) != DISPATCH_ENTRY_KEYS or type(entry["slot"]) is not int or not 1<=entry["slot"]<=8
            or type(entry["contract_revision"]) is not int or entry["contract_revision"]<0
            or not identifier(entry["receipt_id"],"tg_review_receipt_")
            or type(entry["submission_digest"]) is not str or not DIGEST_PATTERN.fullmatch(entry["submission_digest"])
            or type(entry["original_digest"]) is not str or not DIGEST_PATTERN.fullmatch(entry["original_digest"])
            or entry["source_preparation_id"] is not None and not identifier(entry["source_preparation_id"],"tg_review_preparation_")
            or entry["source_continuation_id"] is not None and not identifier(entry["source_continuation_id"],"tg_review_continuation_")
            or type(entry["target"]) is not dict or set(entry["target"])!={"kind","value","base_revision","generation","capture_version"}
            or type(entry["target"]["generation"]) is not int or type(entry["target"]["capture_version"]) is not int):
            raise _bad()
        receipt = connection.execute("SELECT r.*,e.contract_revision FROM review_receipts AS r JOIN evidence_references AS e ON e.source_id=r.review_receipt_id AND e.source_kind='review_receipt' WHERE r.review_receipt_id=?", (entry["receipt_id"],)).fetchone()
        session = connection.execute("SELECT * FROM review_receipt_sessions WHERE review_receipt_id=?", (entry["receipt_id"],)).fetchone()
        if receipt is None or session is None or any(entry[k] != receipt[k] for k in ("project_id","task_id","contract_revision")) or entry["target"] != _target(receipt) or entry["reviewer_session_id"] != session["session_id"] or entry["original_digest"] != "sha256:"+session["original_result_digest"]:
            raise _bad()
        if entry["protocol"] == "review-answer-v1":
            validate_payload_findings(connection,entry["receipt_id"],validate_provenance_relation(connection,receipt))
            source = connection.execute("SELECT * FROM review_collection_sources WHERE review_receipt_id=?", (entry["receipt_id"],)).fetchone()
            if source is None or entry["source_preparation_id"] != source["preparation_id"] or any(entry[k] != source[k] for k in ("reviewer_session_id","turn_id","message_id","basis_digest","original_digest","payload_digest","submission_digest")):
                raise _bad()
        elif entry["protocol"] != "legacy-review-result-v1" or receipt["review_provenance_basis_version"] == 2 or any(entry[k] is not None for k in ("turn_id","message_id","basis_digest","payload_digest")):
            raise _bad()
        judgment=connection.execute("SELECT continuation_id FROM review_receipt_judgments WHERE review_receipt_id=?",(entry["receipt_id"],)).fetchone()
        if judgment is None or entry["source_continuation_id"] != judgment["continuation_id"]:
            raise _bad()
        if judgment["continuation_id"] is not None:
            continuation=connection.execute("SELECT preparation_id FROM review_continuations WHERE continuation_id=?",(judgment["continuation_id"],)).fetchone()
            if continuation is None or entry["source_preparation_id"]!=continuation["preparation_id"]:
                raise _bad()
        elif entry["protocol"]=="legacy-review-result-v1" and entry["source_preparation_id"] is not None:
            raise _bad()


def read_registration(connection, *, project_id, task_id, preparation_id, submission_digest):
    result = dict(status="absent",preparation_id=preparation_id,submission_digest=submission_digest,current_basis=None,entries=[],reason=None)
    import sqlite3
    try:
        row = connection.execute("SELECT * FROM review_collection_dispatches WHERE preparation_id=? AND submission_digest=?", (preparation_id,submission_digest)).fetchone()
        native_entries=connection.execute("SELECT * FROM review_collection_dispatch_entries WHERE preparation_id=? AND submission_digest=? ORDER BY slot LIMIT 9",(preparation_id,submission_digest)).fetchall()
    except sqlite3.Error:
        result.update(status="unknown",reason="inventory_unavailable")
        return result
    if row is None:
        if native_entries:
            result.update(status="conflict",reason="partial_inventory")
        return result
    if type(row["inventory_json"]) is str and len(row["inventory_json"].encode("utf-8"))>32768:
        result.update(status="unknown",reason="capacity")
        return result
    try:
        if row["project_id"] != project_id or row["task_id"] != task_id:
            raise _bad()
        entries = _decode(row["inventory_json"],32768)
        if type(entries) is not list or not 1 <= len(entries) <= 8 or any(type(e) is not dict or set(e) != DISPATCH_ENTRY_KEYS for e in entries):
            raise _bad()
        if len(entries)!=len(native_entries) or any(_canonical(entry,32768)!=native["entry_json"] or entry["slot"]!=native["slot"] or entry["receipt_id"]!=native["review_receipt_id"] for entry,native in zip(entries,native_entries)):
            result.update(status="conflict",reason="partial_inventory")
            return result
        collected_ids={e["receipt_id"] for e in entries if e["protocol"]=="review-answer-v1"}
        native_sources={r["review_receipt_id"] for r in connection.execute("SELECT review_receipt_id FROM review_collection_sources WHERE preparation_id=? AND submission_digest=?",(preparation_id,submission_digest))}
        if collected_ids!=native_sources:
            result.update(status="conflict",reason="partial_inventory")
            return result
        _validate_dispatch_entries(connection,entries)
    except sqlite3.Error:
        result.update(status="unknown",reason="inventory_unavailable")
        return result
    except Exception:
        result.update(status="conflict",reason="source_mismatch")
        return result
    try:
        task = connection.execute("SELECT * FROM tasks WHERE project_id=? AND task_id=?", (project_id,task_id)).fetchone()
        current = task is not None and all(entry["contract_revision"] == task["current_contract_revision"] and entry["target"] == {"kind":task["review_target_kind"],"value":task["review_target_value"],"base_revision":task["review_target_base_revision"],"generation":task["review_target_generation"],"capture_version":task["review_target_capture_version"]} for entry in entries)
        if current:
            current=_current_effect_basis(connection,task,entries)
    except sqlite3.Error:
        result.update(status="unknown",reason="inventory_unavailable")
        return result
    result.update(status="recorded",current_basis=current,entries=entries)
    try:
        _canonical(result,32768)
    except Exception:
        result.update(status="unknown",current_basis=None,entries=[],reason="capacity")
    return result


def _current_effect_basis(connection, task, entries):
    from .task_ownership import read_basis
    from .verification_declaration import verification_requirement
    owner=read_basis(connection,project_id=task["project_id"],task_id=task["task_id"])
    project=connection.execute("SELECT canonical_path_hash,binding_generation FROM project_meta WHERE project_id=?",(task["project_id"],)).fetchone()
    if project is None or owner.state not in ("owned","completion_only"):
        return False
    expected=dict(project_id=task["project_id"],task_id=task["task_id"],contract_revision=task["current_contract_revision"],
        authority_snapshot_id=task["review_target_authority_snapshot_id"],acceptance_criterion_id=task["review_target_acceptance_criterion_id"],verification_criterion_id=task["review_target_verification_criterion_id"],
        artifact_manifest_id=task["review_target_artifact_manifest_id"],review_tier=task["review_tier"],verification_requirement=verification_requirement(task["verification"],task["verification_not_required_reason"]),verification_not_required_reason=task["verification_not_required_reason"],completion_commit_required=task["completion_commit_required"],
        project_path_hash=project["canonical_path_hash"],project_binding_generation=project["binding_generation"],parent_session_id=owner.owner_session_id or owner.completion_session_id,execution_id=owner.execution_id,ownership_generation=owner.generation,
        **{"target_"+key:task["review_target_"+key] for key in ("kind","value","base_revision","generation")})
    for entry in entries:
        if entry["protocol"]=="review-answer-v1":
            source=connection.execute("SELECT basis_json FROM review_collection_sources WHERE review_receipt_id=?",(entry["receipt_id"],)).fetchone()
            basis=_decode(source["basis_json"],32768)["execution_basis"]
            if any(basis[key]!=value for key,value in expected.items()):return False
        else:
            binding=connection.execute("SELECT execution_id FROM review_receipt_sessions WHERE review_receipt_id=?",(entry["receipt_id"],)).fetchone()
            if binding is None or binding["execution_id"]!=owner.execution_id:return False
    return True


def read_collected_captures(connection, continuation_id=None):
    from .review_answer_values import metadata_digest, source_metadata_projection, validate_capture_evidence, validate_metadata
    predicate = " WHERE continuation_id=?" if continuation_id is not None else ""
    rows = connection.execute("SELECT * FROM review_collected_captures"+predicate+" ORDER BY continuation_id,session_id", (continuation_id,) if continuation_id is not None else ()).fetchall()
    result = []
    for row in rows:
        metadata = validate_metadata(_decode(row["metadata_json"],32768))
        projection = validate_capture_evidence(_decode(row["projection_json"],32768))
        if (metadata_digest(metadata) != row["metadata_digest"] or source_metadata_projection(metadata) != projection["source_metadata"]
            or projection["capture_identity"]["consumer_continuation_id"] != row["continuation_id"]
            or projection["capture_identity"]["session_id"] != row["session_id"]
            or projection["capture_identity"]["source_continuation_id"] != row["source_continuation_id"]):
            raise _bad()
        # The frozen projection's nullable link is published separately, so its
        # deterministic immutable bytes never change after initial capture.
        if projection["result_receipt_id"] is not None:
            raise _bad()
        projection["result_receipt_id"] = row["result_receipt_id"]
        result.append(projection)
    return tuple(result)


def read_continuation_dispatch(connection, record):
    """Recover the exact committed batch through its selected judgments."""
    if record.state != "registered" or not record.results:
        return None
    receipt_ids = tuple(row[1] for row in record.results)
    rows = connection.execute(
        """SELECT DISTINCT preparation_id,submission_digest
FROM review_collection_dispatch_entries
WHERE preparation_id=? AND review_receipt_id IN (SELECT value FROM json_each(?))""",
        (record.preparation_id, json.dumps(receipt_ids))).fetchall()
    if not rows:
        return None  # A legacy-only registration has no collected dispatch.
    if len(rows) != 1 or rows[0]["preparation_id"] != record.preparation_id:
        raise _bad()
    found = read_registration(connection, project_id=record.basis["project_id"],
        task_id=record.basis["task_id"], preparation_id=record.preparation_id,
        submission_digest=rows[0]["submission_digest"])
    if (found["status"] != "recorded"
        or not set(receipt_ids) <= {entry["receipt_id"] for entry in found["entries"]}):
        raise _bad()
    return found


def read_native_collected_captures(connection, continuation_id):
    """Private preparation input: unchanged source metadata and native seal."""
    public=read_collected_captures(connection,continuation_id)
    rows=connection.execute("SELECT * FROM review_collected_captures WHERE continuation_id=? ORDER BY session_id",(continuation_id,)).fetchall()
    result=[]
    for projection,row in zip(public,rows):
        capture={**projection["capture_identity"],"metadata_digest":row["metadata_digest"]}
        result.append((capture,_decode(row["metadata_json"],32768)))
    return tuple(result)


def capture_collected_locked(connection, *, capture, metadata):
    from .storage import _require_evidence_writer
    from .review_answer_values import validate_collected_capture, validate_metadata, metadata_digest, source_metadata_projection, capture_evidence_digest
    _require_evidence_writer(connection)
    capture = validate_collected_capture(capture)
    metadata = validate_metadata(metadata)
    identity = {k:v for k,v in capture.items() if k != "metadata_digest"}
    source_metadata = source_metadata_projection(metadata)
    projection = dict(capture_identity=identity,source_metadata=source_metadata,
        evidence_digest=capture_evidence_digest(identity,source_metadata),result_receipt_id=None)
    from .review_answer_values import validate_capture_evidence
    validate_capture_evidence(projection)
    if capture["metadata_digest"] != metadata_digest(metadata):
        raise _bad()
    consumer = connection.execute("SELECT * FROM review_continuations WHERE continuation_id=?",(capture["consumer_continuation_id"],)).fetchone()
    if consumer is None or consumer["state"] != "preparing" or consumer["preparation_id"] != capture["source_preparation_id"] or consumer["basis_json"] != _canonical(metadata["basis"]["execution_basis"],16384) or consumer["route"] != metadata["basis"]["route"]:
        raise _bad()
    if connection.execute("SELECT 1 FROM review_continuation_reviewers WHERE continuation_id=? AND session_id=?",(consumer["continuation_id"],capture["session_id"])).fetchone():
        raise _bad()
    existing_captures=read_collected_captures(connection,consumer["continuation_id"])
    selected_count=connection.execute("SELECT COUNT(*) FROM review_continuation_reviewers WHERE continuation_id=?",(consumer["continuation_id"],)).fetchone()[0]
    legacy_sessions={row[0] for row in connection.execute("SELECT session_id FROM review_continuation_retained_originals WHERE continuation_id=?",(consumer["continuation_id"],))}
    retained_order=sorted(legacy_sessions | {row["capture_identity"]["session_id"] for row in existing_captures} | {capture["session_id"]})
    if capture["session_id"] in legacy_sessions or any(identity["consumer_slot"] != selected_count+retained_order.index(identity["session_id"])+1
        for identity in [capture,*(row["capture_identity"] for row in existing_captures)]):
        raise _bad()
    if len(read_collected_captures(connection,consumer["continuation_id"])) + connection.execute("SELECT COUNT(*) FROM review_continuation_reviewers WHERE continuation_id=?",(consumer["continuation_id"],)).fetchone()[0] + connection.execute("SELECT COUNT(*) FROM review_continuation_retained_originals WHERE continuation_id=?",(consumer["continuation_id"],)).fetchone()[0] >= 8:
        raise _bad()
    source_id = capture["source_continuation_id"]
    if source_id is not None:
        seen = set()
        ancestor = consumer["predecessor_id"]
        while ancestor is not None and ancestor not in seen:
            seen.add(ancestor)
            old = connection.execute("SELECT * FROM review_continuations WHERE continuation_id=?",(ancestor,)).fetchone()
            if old is None or old["basis_json"] != consumer["basis_json"] or old["preparation_id"] != consumer["preparation_id"]:
                raise _bad()
            if ancestor == source_id:
                if (old["state"] != "superseded" or metadata["basis"]["continuation"] !=
                    {key:old[key] for key in ("continuation_id","sequence","selection_digest")}):
                    raise _bad()
                break
            ancestor = old["predecessor_id"]
        else:
            raise _bad()
        selected = connection.execute("SELECT * FROM review_continuation_reviewers WHERE continuation_id=? AND session_id=?",(source_id,capture["session_id"])).fetchone()
        if selected is None or selected["result_receipt_id"] is not None:
            raise _bad()
    if connection.execute("SELECT 1 FROM review_receipt_sessions AS s JOIN review_receipts AS r ON r.review_receipt_id=s.review_receipt_id WHERE s.session_id=? AND r.task_id=? AND r.target_generation=?",(capture["session_id"],consumer["task_id"],consumer["target_generation"])).fetchone():
        raise _bad()
    old = connection.execute("SELECT * FROM review_collected_captures WHERE continuation_id=? AND session_id=?",(consumer["continuation_id"],capture["session_id"])).fetchone()
    parameters = (consumer["continuation_id"],capture["session_id"],source_id,_canonical(metadata,32768),capture["metadata_digest"],_canonical(projection,32768))
    if old is not None:
        if tuple(old[k] for k in ("continuation_id","session_id","source_continuation_id","metadata_json","metadata_digest","projection_json")) != parameters:
            raise _bad()
        return
    connection.execute("INSERT INTO review_collected_captures VALUES (?,?,?,?,?,?,NULL)",parameters)


def match_capture_provenance(capture, provenance):
    """Compare every public overlapping source fact, never a private seal."""
    from .review_answer_values import basis_digest
    metadata = capture["source_metadata"]
    basis,binding,identity = metadata["basis"],metadata["binding"],metadata["source_identity"]
    attestation,host = provenance["answer_attestation"],provenance["host_observation"]
    if (attestation["protocol"] != basis["protocol"] or attestation["attestation"] != basis["attestation"]
        or attestation["request_digest"] != basis["request_digest"] or attestation["original"] != metadata["original"]
        or provenance["assignment"] != binding["assignment"]
        or any(host[k] != v for k,v in (("basis_digest",basis_digest(basis)),("preparation_id",basis["preparation_id"]),
            ("slot",binding["slot"]),("attempt",metadata["attempt"]),("parent_session_id",binding["parent_session_id"]),
            ("reviewer_session_id",identity["reviewer_session_id"]),("turn_id",identity["turn_id"]),("message_id",identity["message_id"]),
            ("binding_source",binding["binding_source"]),("answer_source",metadata["answer_source"]),("terminal_source",metadata["terminal_source"])))):
        raise _bad()


def validate_capture_relations(connection, by_id):
    from .review_continuation import selection_digest
    consumed = set()
    sources={}
    for consumer_id,consumer in by_id.items():
        captures = read_collected_captures(connection,consumer_id)
        sessions = set()
        legacy = connection.execute("SELECT session_id FROM review_continuation_retained_originals WHERE continuation_id=?",(consumer_id,)).fetchall()
        if len(captures)+len(legacy)+len(consumer.selected) > 8:
            raise _bad()
        retained_order = sorted({row["session_id"] for row in legacy} | {row["capture_identity"]["session_id"] for row in captures})
        for capture in captures:
            identity = capture["capture_identity"]
            sid = identity["session_id"]
            if identity["consumer_slot"] != len(consumer.selected) + retained_order.index(sid) + 1:
                raise _bad()
            metadata = capture["source_metadata"]
            source_key=(identity["source_preparation_id"],identity["source_continuation_id"],sid)
            if source_key in sources and sources[source_key] != metadata:
                raise _bad()
            sources[source_key]=metadata
            if sid in sessions or sid in {s.session_id for s in consumer.selected} or sid in {r["session_id"] for r in legacy} or identity["source_preparation_id"] != consumer.preparation_id or metadata["basis"]["execution_basis"] != consumer.basis or metadata["basis"]["route"] != consumer.route:
                raise _bad()
            sessions.add(sid)
            source_id = identity["source_continuation_id"]
            source = None
            if source_id is not None:
                ancestry=set();parent=consumer.predecessor_id
                while parent is not None:
                    if parent in ancestry or parent not in by_id:
                        raise _bad()
                    ancestry.add(parent);parent=by_id[parent].predecessor_id
                source=by_id.get(source_id)
                if source is None or source_id not in ancestry or source.state != "superseded" or source.basis != consumer.basis or source.preparation_id != consumer.preparation_id or sid not in {s.session_id for s in source.selected}:
                    raise _bad()
                if metadata["basis"]["continuation"] != dict(continuation_id=source.continuation_id,
                    sequence=source.sequence,selection_digest=selection_digest(source.selected)):
                    raise _bad()
            rid=capture["result_receipt_id"]
            if rid is None:
                if consumer.state == "registered":
                    raise _bad()
                continue
            if consumer.state != "registered" or rid in consumed:
                raise _bad()
            receipt=connection.execute("SELECT * FROM review_receipts WHERE review_receipt_id=?",(rid,)).fetchone()
            if receipt is None or receipt["reviewer_key"] != metadata["binding"]["reviewer_key"]:
                raise _bad()
            provenance=validate_provenance_relation(connection,receipt)
            match_capture_provenance(capture,provenance)
            from .review_continuation_repository import _session_proof
            proof=_session_proof(connection,rid,basis=consumer.basis,session_id=sid)
            if proof["original_result_digest"] != identity["original_digest"].removeprefix("sha256:") or proof["continuation_id"] != source_id or source is not None and (sid,rid,proof["original_result_digest"]) not in source.results:
                raise _bad()
            consumed.add(rid)
    return consumed
