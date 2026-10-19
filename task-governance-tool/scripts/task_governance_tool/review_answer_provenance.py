"""Pure v2 collected-review provenance construction and validation."""
from __future__ import annotations

from . import review_answer_values as v
from .review_answer import validate_payload
from .review_provenance import generate_review_provenance_id

UNKNOWN = dict(state=str, value=(str,), source=(v.SOURCE,))
HOST = dict(basis_digest=str, preparation_id=str, slot=int, attempt=int, parent_session_id=str,
            reviewer_session_id=str, turn_id=str, message_id=str, binding_source=v.SOURCE,
            answer_source=v.SOURCE, terminal_source=v.SOURCE, model=UNKNOWN, skill=UNKNOWN, methods=UNKNOWN)
ORIGINAL = {k:s for k,s in v.ORIGINAL.items() if k != "path"}
PAYLOAD = dict(verdict=str, summary=str, findings=[dict(severity=str, summary=str)], checks=[str], limitations=[str])
ATTESTATION = dict(protocol=str, attestation=str, request_digest=str, original=ORIGINAL, payload=PAYLOAD)
SCHEMA = dict(review_provenance_id=str, provenance_version=int, answer_attestation=ATTESTATION,
              assignment=v.ASSIGNMENT, host_observation=HOST, assurance_class=str,
              producer_class=str, producer_version=int, digest=str)
TARGET = dict(kind=str, value=str, base_revision=str, generation=int, capture_version=int)

def _context(project_id, task_id, review_receipt_id, receipt_kind, target):
    value = v.prepare(dict(project_id=project_id, task_id=task_id, review_receipt_id=review_receipt_id,
                           receipt_kind=receipt_kind, target=target),
                      dict(project_id=str, task_id=str, review_receipt_id=str, receipt_kind=str, target=TARGET))
    v.require(bool(project_id.strip()) and len(project_id) <= 500)
    v.identifier(task_id, "tg_task_"); v.identifier(review_receipt_id, "tg_review_receipt_")
    v.require(receipt_kind == "independent")
    v.require(target["kind"] in ("git_commit", "git_snapshot", "diff_fingerprint", "external_revision"))
    v.require(bool(target["value"]) and len(target["value"]) <= 500)
    v.integer(target["generation"], 1); v.integer(target["capture_version"], 1, 1)
    v.require((target["kind"] == "git_snapshot") == bool(target["base_revision"]))
    if target["base_revision"] is not None:
        v.require(len(target["base_revision"]) <= 500)
    return value

def provenance_digest(provenance, *, project_id, task_id, review_receipt_id, receipt_kind, target):
    context = _context(project_id, task_id, review_receipt_id, receipt_kind, target)
    context["provenance"] = {k:item for k,item in provenance.items() if k not in ("review_provenance_id", "digest")}
    return v.domain_digest(b"taskgov-review-provenance-v2\0", context)

def validate_provenance_v2(provenance, *, project_id, task_id, review_receipt_id, receipt_kind, target):
    result = v.prepare(provenance, SCHEMA, 262144)
    _context(project_id, task_id, review_receipt_id, receipt_kind, target)
    v.identifier(result["review_provenance_id"], "tg_review_provenance_")
    v.require(result["provenance_version"] == 2 and result["producer_version"] == 1)
    v.require(result["assurance_class"] == "bound_attestation" and result["producer_class"] == "taskgov_answer_collector")
    attestation = result["answer_attestation"]
    v.require(attestation["protocol"] == "review-answer-v1" and attestation["attestation"] == "assigned-independent-review-v1")
    v.digest(attestation["request_digest"])
    v._original(attestation["original"], include_path=False)
    validate_payload(attestation["payload"])
    v._assignment(result["assignment"])
    host = result["host_observation"]
    v.digest(host["basis_digest"]); v.identifier(host["preparation_id"], "tg_review_preparation_")
    v.integer(host["slot"], 1, 8); v.integer(host["attempt"], 0, 1)
    v.session(host["parent_session_id"]); v.session(host["reviewer_session_id"])
    v.require(host["parent_session_id"] != host["reviewer_session_id"], "identity")
    v.session(host["turn_id"]); v.host_id(host["message_id"])
    adapter_version = host["binding_source"]["version"]
    if adapter_version == 2:
        v.require(host["attempt"] == 0)
    for field, operation in (("binding_source", "dispatch_binding" if adapter_version == 1 else "reviewer_tracking"), ("answer_source", "answer_observation"), ("terminal_source", "terminal_observation")):
        v._source(host[field], operation)
        v.require(host[field]["version"] == adapter_version, "identity")
    for field in ("model", "skill", "methods"):
        v.require(host[field] == dict(state="unknown", value=None, source=None))
    v.digest(result["digest"])
    v.require(result["digest"] == provenance_digest(result, project_id=project_id, task_id=task_id,
        review_receipt_id=review_receipt_id, receipt_kind=receipt_kind, target=target), "identity")
    return result

def build_provenance_v2(*, project_id, task_id, review_receipt_id, target, metadata, answer,
                        receipt_kind="independent", review_provenance_id=None):
    metadata = v.validate_metadata(metadata)
    payload = validate_payload(answer.payload if hasattr(answer, "payload") else answer)
    if hasattr(answer, "raw"):
        v.validate_metadata(metadata, answer.raw)
    basis, binding, identity = metadata["basis"], metadata["binding"], metadata["source_identity"]
    execution = basis["execution_basis"]
    v.require(execution["project_id"] == project_id and execution["task_id"] == task_id, "identity")
    v.require(target == dict(kind=execution["target_kind"], value=execution["target_value"],
                           base_revision=execution["target_base_revision"], generation=execution["target_generation"], capture_version=1), "identity")
    unknown = dict(state="unknown", value=None, source=None)
    result = dict(review_provenance_id=review_provenance_id or generate_review_provenance_id(), provenance_version=2,
        answer_attestation=dict(protocol=basis["protocol"], attestation=basis["attestation"], request_digest=basis["request_digest"],
            original={k:item for k,item in metadata["original"].items() if k != "path"}, payload=payload),
        assignment=binding["assignment"],
        host_observation=dict(basis_digest=binding["basis_digest"], preparation_id=basis["preparation_id"], slot=binding["slot"],
            attempt=metadata["attempt"], parent_session_id=binding["parent_session_id"], reviewer_session_id=identity["reviewer_session_id"],
            turn_id=identity["turn_id"], message_id=identity["message_id"], binding_source=binding["binding_source"],
            answer_source=metadata["answer_source"], terminal_source=metadata["terminal_source"], model=unknown.copy(), skill=unknown.copy(), methods=unknown.copy()),
        assurance_class="bound_attestation", producer_class="taskgov_answer_collector", producer_version=1, digest="")
    result["digest"] = provenance_digest(result, project_id=project_id, task_id=task_id, review_receipt_id=review_receipt_id,
                                         receipt_kind=receipt_kind, target=target)
    return validate_provenance_v2(result, project_id=project_id, task_id=task_id, review_receipt_id=review_receipt_id,
                                  receipt_kind=receipt_kind, target=target)
