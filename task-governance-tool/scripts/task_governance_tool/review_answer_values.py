"""Closed collection values. This module performs no host, file or database I/O."""
from __future__ import annotations

import json
import re

from . import review_continuation as continuation
from .evidence_ledger import canonical_json_bytes as canonical_bytes, domain_digest
from .review_answer import AnswerError, scan_strings, privacy_scan, decode_answer
from .review_provenance import REVIEW_PROFILES, REVIEW_LENSES, CONTEXT_RELATIONS
from .session_identity import is_session_id
from .task_values import TaskValidationError, reject_private_or_raw_content

def fail(reason="shape", classification="format_invalid"):
    raise AnswerError(classification, reason=reason)

def nullable(schema):
    return (schema,)

SOURCE = dict(adapter=str, version=int, operation=str)
IDENTITY = dict(reviewer_session_id=str, turn_id=str, message_id=str)
SKILL = dict(id=str, version=str)
ASSIGNMENT = dict(kind=str, review_profiles=[str], review_lenses=[str], requested_model=nullable(str),
                  requested_effort=nullable(str), requested_skill=nullable(SKILL), context_relation=nullable(str))
CONTINUATION = dict(continuation_id=str, sequence=int, selection_digest=str)
EXECUTION = {key: str for key in continuation.BASIS_FIELDS}
for _key in ("version", "contract_revision", "review_tier", "completion_commit_required", "target_generation",
             "project_binding_generation", "ownership_generation"):
    EXECUTION[_key] = int
for _key in ("acceptance_criterion_id", "verification_criterion_id"):
    EXECUTION[_key] = nullable(str)
BASIS = dict(version=int, preparation_id=str, packet_digest=str, request_digest=str, protocol=str,
             attestation=str, route=str, continuation=nullable(CONTINUATION), execution_basis=EXECUTION)
BINDING = dict(basis_digest=str, slot=int, reviewer_key=str, dispatch_id=str, parent_session_id=str,
               reviewer_session_id=str, initial_turn_id=str, binding_source=SOURCE, assignment=ASSIGNMENT)
TRACKING = dict(kind=str, boundary=str, boundary_turn_id=str, dispatch_id=nullable(str))
TRACKING_BINDING = {key: schema for key, schema in BINDING.items()
                    if key not in ("dispatch_id", "initial_turn_id")}
TRACKING_BINDING["tracking"] = TRACKING
ORIGINAL = dict(path=str, byte_length=int, digest=str, payload_start=int, payload_length=int, payload_digest=str)
CORRECTION_BINDING = dict(intent_id=str, initial_turn_id=str, correction_turn_id=str, source=SOURCE)
METADATA = dict(basis=BASIS, binding=BINDING, attempt=int, source_identity=IDENTITY, original=ORIGINAL,
                answer_source=SOURCE, terminal=str, terminal_source=SOURCE, correction_binding=nullable(CORRECTION_BINDING))
FAILURE = dict(**{"class":str}, field=nullable(str), reason=str)
PUBLICATION = dict(state=str, original=ORIGINAL)
ATTEMPT = dict(ordinal=int, turn_id=nullable(str), source_identity=nullable(IDENTITY), observation=str,
               observed_digest=nullable(str), terminal=str, terminal_source=nullable(SOURCE),
               publication=nullable(PUBLICATION), failure=nullable(FAILURE))
CORRECTION = dict(intent_id=str, initial_source_identity=IDENTITY, initial_digest=str, effect=str,
                  correction_turn_id=nullable(str), binding_source=nullable(SOURCE), failure=nullable(FAILURE))
REGISTRATION = dict(state=str, submission_digest=nullable(str), receipt_id=nullable(str))
RECORD = dict(version=int, basis=BASIS, binding=nullable(BINDING), attempts=[ATTEMPT],
              correction=nullable(CORRECTION), registration=REGISTRATION)
TRACKING_RECORD = {**RECORD, "binding": nullable(TRACKING_BINDING), "tracking_state": str}
RETIRED_REGISTRATION = dict(continuation_id=str, registration=REGISTRATION)
CAPTURE = dict(kind=str, consumer_continuation_id=str, consumer_slot=int, source_preparation_id=str,
               source_continuation_id=nullable(str), source_slot=int, session_id=str, source_basis_digest=str,
               original_digest=str, metadata_digest=str)

def shape(value, schema):
    if type(schema) is tuple:
        if value is not None:
            shape(value, schema[0])
    elif type(schema) is dict:
        if type(value) is not dict or set(value) != set(schema):
            fail()
        for key, sub in schema.items():
            shape(value[key], sub)
    elif type(schema) is list:
        if type(value) is not list:
            fail("type")
        for item in value:
            shape(item, schema[0])
    elif type(value) is not schema:
        fail("type")

def prepare(value, schema, limit=32768):
    scan_strings(value)
    shape(value, schema)
    if len(canonical_bytes(value)) > limit:
        fail("bound", "capacity_exceeded")
    privacy_scan(value)
    return json.loads(canonical_bytes(value))

def require(condition, reason="choice"):
    if not condition:
        fail(reason)

def integer(value, low=0, high=(1 << 63)-1):
    require(type(value) is int and low <= value <= high, "bound")

def digest(value):
    require(type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None)

def identifier(value, prefix):
    require(type(value) is str and re.fullmatch(re.escape(prefix)+r"[0-9a-f]{16}", value) is not None)

def host_id(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,255}", value, re.ASCII) is not None)

def session(value):
    require(is_session_id(value), "identity")

def _source(value, operation=None):
    require(value["adapter"] == "codex-app-tools-review-answer" and value["version"] in (1, 2))
    operations = (("dispatch_binding", "answer_observation", "terminal_observation", "correction_binding")
                  if value["version"] == 1 else ("reviewer_tracking", "answer_observation", "terminal_observation"))
    require(value["operation"] in operations)
    require(operation is None or value["operation"] == operation)

def validate_source(value, operation=None):
    result = prepare(value, SOURCE)
    _source(result, operation)
    return result

def _basis(value):
    require(value["version"] in (1, 2) and value["protocol"] == "review-answer-v1" and value["attestation"] == "assigned-independent-review-v1")
    identifier(value["preparation_id"], "tg_review_preparation_")
    digest(value["packet_digest"]); digest(value["request_digest"])
    require(value["route"] in ("manual", "integrated"))
    try:
        continuation.validate_basis(value["execution_basis"])
    except TaskValidationError:
        fail()
    if value["continuation"] is not None:
        item = value["continuation"]
        identifier(item["continuation_id"], "tg_review_continuation_")
        integer(item["sequence"], 1, 64)
        # Selection is an unchanged legacy digest, not a new prefixed digest.
        require(re.fullmatch(r"[0-9a-f]{64}", item["selection_digest"]) is not None)

def validate_basis(value):
    result = prepare(value, BASIS)
    _basis(result)
    return result

def basis_digest(value):
    return domain_digest(b"taskgov-review-answer-basis-v1\0", validate_basis(value))

def _ordered(values, vocabulary, maximum):
    require(len(values) <= maximum and len(values) == len(set(values)), "bound")
    require(all(item in vocabulary for item in values))
    require(values == [item for item in vocabulary if item in values])

def _assignment(value):
    require(value["kind"] == "independent")
    _ordered(value["review_profiles"], REVIEW_PROFILES, 4)
    _ordered(value["review_lenses"], REVIEW_LENSES, 8)
    if value["requested_model"] is not None:
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}", value["requested_model"], re.ASCII) is not None)
    require(value["requested_effort"] is None or value["requested_effort"] in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"))
    if value["requested_skill"] is not None:
        skill = value["requested_skill"]
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}", skill["id"], re.ASCII) is not None)
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}", skill["version"], re.ASCII) is not None)
    require(value["context_relation"] is None or value["context_relation"] in CONTEXT_RELATIONS)

def _binding(value, basis=None):
    digest(value["basis_digest"]); integer(value["slot"], 1, 8)
    key = value["reviewer_key"]
    try:
        reject_private_or_raw_content("reviewer_key", key)
    except TaskValidationError:
        raise AnswerError("privacy_rejected", reason="privacy") from None
    require(bool(key) and key == key.strip() and len(key) <= 500, "bound")
    session(value["parent_session_id"]); session(value["reviewer_session_id"])
    require(value["parent_session_id"] != value["reviewer_session_id"], "identity")
    tracking = value.get("tracking")
    if tracking is None:
        host_id(value["dispatch_id"]); session(value["initial_turn_id"])
        _source(value["binding_source"], "dispatch_binding")
        require(value["binding_source"]["version"] == 1)
    else:
        _source(value["binding_source"], "reviewer_tracking")
        require(value["binding_source"]["version"] == 2)
        require(tracking["kind"] in ("initial", "continuation"))
        session(tracking["boundary_turn_id"])
        if tracking["kind"] == "initial":
            require(tracking["boundary"] == "inclusive" and tracking["dispatch_id"] is not None)
            host_id(tracking["dispatch_id"])
        else:
            require(tracking["boundary"] == "exclusive" and tracking["dispatch_id"] is None)
    _assignment(value["assignment"])
    if basis is not None:
        require((basis["version"] == 2) == (tracking is not None), "identity")
        if tracking is not None:
            require((basis["continuation"] is None) == (tracking["kind"] == "initial"), "identity")
        require(value["basis_digest"] == basis_digest(basis), "identity")
        require(value["parent_session_id"] == basis["execution_basis"]["parent_session_id"], "identity")

def validate_binding(value, basis=None):
    result = prepare(value, TRACKING_BINDING if type(value) is dict and "tracking" in value else BINDING)
    _binding(result, validate_basis(basis) if basis is not None else None)
    return result

def _identity(value):
    session(value["reviewer_session_id"])
    session(value["turn_id"]); host_id(value["message_id"])

def _original(value, raw=None, include_path=True):
    if include_path:
        path = value["path"]
        require(bool(path) and len(path.encode("utf-8")) <= 4096 and "\\" not in path and not any(ord(c) < 32 for c in path), "bound")
        parts = path.split("/")
        reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
        require(not any(not p or p in (".", "..") or p.endswith((".", " ")) or any(c in p for c in ':*?"<>|') or p.split(".")[0].lower() in reserved for p in parts))
        require(parts[0].lower() not in (".git", ".agents", ".codex", ".taskgov", "task-governance-tool"))
    integer(value["byte_length"], 1, 262144); integer(value["payload_start"])
    integer(value["payload_length"], 1, 262144)
    require(value["payload_start"] + value["payload_length"] <= value["byte_length"], "bound")
    digest(value["digest"]); digest(value["payload_digest"])
    if raw is not None:
        decoded = decode_answer(raw)
        require(value["byte_length"] == len(raw) and value["digest"] == decoded.original_digest and
                value["payload_start"] == decoded.payload_start and value["payload_length"] == decoded.payload_length and
                value["payload_digest"] == decoded.payload_digest, "identity")

def validate_original(value, raw=None, include_path=True):
    result = prepare(value, ORIGINAL if include_path else {k:v for k,v in ORIGINAL.items() if k != "path"})
    _original(result, raw, include_path)
    return result

def _metadata(value, raw=None, include_path=True):
    _basis(value["basis"]); _binding(value["binding"], value["basis"])
    _identity(value["source_identity"]); _original(value["original"], raw, include_path)
    binding, identity = value["binding"], value["source_identity"]
    integer(value["attempt"], 0, 1)
    require(identity["reviewer_session_id"] == binding["reviewer_session_id"], "identity")
    require(value["terminal"] == "completed")
    _source(value["answer_source"], "answer_observation"); _source(value["terminal_source"], "terminal_observation")
    require(value["answer_source"]["version"] == value["terminal_source"]["version"] == value["basis"]["version"], "identity")
    correction = value["correction_binding"]
    if value["basis"]["version"] == 2:
        require(value["attempt"] == 0 and correction is None, "identity")
        require(value["answer_source"]["version"] == value["terminal_source"]["version"] == 2, "identity")
        tracking = binding["tracking"]
        require(tracking["boundary"] != "exclusive" or identity["turn_id"] != tracking["boundary_turn_id"], "identity")
    elif value["attempt"] == 0:
        require(correction is None and identity["turn_id"] == binding["initial_turn_id"], "identity")
    else:
        require(correction is not None, "identity")
        identifier(correction["intent_id"], "tg_review_correction_"); session(correction["correction_turn_id"])
        require(correction["initial_turn_id"] == binding["initial_turn_id"] and correction["correction_turn_id"] == identity["turn_id"] and correction["correction_turn_id"] != correction["initial_turn_id"], "identity")
        _source(correction["source"], "correction_binding")

def validate_metadata(value, raw=None):
    result = prepare(value, metadata_schema(value))
    _metadata(result, raw)
    return result

def metadata_digest(value):
    return domain_digest(b"taskgov-collected-review-metadata-v1\0", validate_metadata(value))

PUBLIC_METADATA = {**METADATA, "original": {k:s for k,s in ORIGINAL.items() if k != "path"}}
CAPTURE_IDENTITY = {k:s for k,s in CAPTURE.items() if k != "metadata_digest"}
CAPTURE_EVIDENCE = dict(capture_identity=CAPTURE_IDENTITY, source_metadata=PUBLIC_METADATA,
                        evidence_digest=str, result_receipt_id=nullable(str))

def metadata_schema(value, *, public=False):
    schema = PUBLIC_METADATA if public else METADATA
    basis = value.get("basis") if type(value) is dict else None
    if type(basis) is dict and basis.get("version") == 2:
        return {**schema, "binding": TRACKING_BINDING}
    return schema

def validate_public_metadata(value):
    result = prepare(value, metadata_schema(value, public=True))
    _metadata(result, include_path=False)
    return result

def source_metadata_projection(metadata):
    result = validate_metadata(metadata)
    del result["original"]["path"]
    return validate_public_metadata(result)

def _capture_identity(value):
    require(value["kind"] == "collected")
    identifier(value["consumer_continuation_id"], "tg_review_continuation_")
    if value["source_continuation_id"] is not None:
        identifier(value["source_continuation_id"], "tg_review_continuation_")
    identifier(value["source_preparation_id"], "tg_review_preparation_")
    integer(value["consumer_slot"], 1, 8); integer(value["source_slot"], 1, 8)
    session(value["session_id"])
    digest(value["source_basis_digest"]); digest(value["original_digest"])

def capture_evidence_digest(capture_identity, source_metadata):
    identity = prepare(capture_identity, CAPTURE_IDENTITY)
    _capture_identity(identity)
    metadata = validate_public_metadata(source_metadata)
    return domain_digest(b"taskgov-collected-review-capture-evidence-v1\0",
                         dict(capture_identity=identity, source_metadata=metadata))

def validate_capture_evidence(value):
    metadata = value.get("source_metadata") if type(value) is dict else None
    result = prepare(value, {**CAPTURE_EVIDENCE, "source_metadata": metadata_schema(metadata, public=True)})
    identity, metadata = result["capture_identity"], result["source_metadata"]
    _capture_identity(identity); _metadata(metadata, include_path=False)
    continuation_value = metadata["basis"]["continuation"]
    require(identity["source_continuation_id"] == (None if continuation_value is None else continuation_value["continuation_id"]), "identity")
    require(identity["source_preparation_id"] == metadata["basis"]["preparation_id"] and
            identity["source_slot"] == metadata["binding"]["slot"] and
            identity["session_id"] == metadata["binding"]["reviewer_session_id"] and
            identity["source_basis_digest"] == basis_digest(metadata["basis"]) and
            identity["original_digest"] == metadata["original"]["digest"], "identity")
    digest(result["evidence_digest"])
    require(result["evidence_digest"] == capture_evidence_digest(identity, metadata), "identity")
    if result["result_receipt_id"] is not None:
        identifier(result["result_receipt_id"], "tg_review_receipt_")
    return result

def _failure(value):
    if value is None:
        return
    require(value["class"] in ("format_invalid", "privacy_rejected", "capacity_exceeded", "collection_unavailable", "binding_mismatch", "save_unknown", "registration_unknown", "review_unable", "review_changes_requested"))
    require(value["field"] is None or value["field"] in ("verdict", "summary", "findings", "findings[].severity", "findings[].summary", "checks", "checks[]", "limitations", "limitations[]"))
    require(value["reason"] in ("shape", "type", "choice", "unicode", "duplicate", "bound", "privacy", "missing", "incomplete", "identity", "conflict", "effect_unknown", "observer_unavailable", "transport_unavailable"))
    require((value["class"] == "privacy_rejected") == (value["reason"] == "privacy"))

def validate_record(value):
    tracked = type(value) is dict and value.get("version") == 2
    schema = TRACKING_RECORD if tracked else RECORD
    if tracked and "retired_registrations" in value:
        schema = {**schema, "retired_registrations": [RETIRED_REGISTRATION]}
    result = prepare(value, schema)
    require(result["version"] in (1, 2))
    _basis(result["basis"])
    require(result["version"] == result["basis"]["version"], "identity")
    if tracked:
        return _tracking_record(result)
    binding, attempts, correction = result["binding"], result["attempts"], result["correction"]
    if binding is not None:
        _binding(binding, result["basis"])
    require(len(attempts) <= 2 and [a["ordinal"] for a in attempts] == list(range(len(attempts))), "bound")
    if correction is not None:
        require(binding is not None and bool(attempts), "identity")
        identifier(correction["intent_id"], "tg_review_correction_"); _identity(correction["initial_source_identity"]); digest(correction["initial_digest"])
        require(correction["initial_source_identity"] == attempts[0]["source_identity"] and correction["initial_digest"] == attempts[0]["observed_digest"], "identity")
        require(correction["effect"] in ("not_sent", "accepted", "failed", "unknown"))
        if correction["correction_turn_id"] is not None:
            session(correction["correction_turn_id"])
            require(correction["effect"] == "accepted" and correction["binding_source"] is not None and correction["correction_turn_id"] != binding["initial_turn_id"], "identity")
            _source(correction["binding_source"], "correction_binding")
        else:
            require(correction["binding_source"] is None, "identity")
        _failure(correction["failure"])
    for a in attempts:
        integer(a["ordinal"], 0, 1)
        require(a["observation"] in ("missing", "complete", "unavailable", "conflict"))
        if a["observation"] in ("complete", "conflict"):
            require(a["source_identity"] is not None, "identity")
        if a["observation"] == "complete" and a["observed_digest"] is None:
            require(a["failure"] is not None and a["publication"] is None, "missing")
        expected_turn = None if binding is None else binding["initial_turn_id"] if a["ordinal"] == 0 else correction["correction_turn_id"] if correction else None
        require(a["ordinal"] == 0 or correction is not None, "identity")
        require(a["turn_id"] == expected_turn, "identity")
        if a["source_identity"] is not None:
            _identity(a["source_identity"])
            require(binding is not None and a["source_identity"]["turn_id"] == expected_turn and a["source_identity"]["reviewer_session_id"] == binding["reviewer_session_id"], "identity")
        if a["observed_digest"] is not None:
            digest(a["observed_digest"])
            require(a["observation"] in ("complete", "conflict") and a["source_identity"] is not None, "identity")
        require(a["terminal"] in ("in_progress", "completed", "failed", "interrupted", "unknown"))
        require((a["terminal"] == "unknown") == (a["terminal_source"] is None), "identity")
        if a["terminal_source"] is not None:
            require(expected_turn is not None, "identity")
            _source(a["terminal_source"], "terminal_observation")
        if a["publication"] is not None:
            p = a["publication"]
            require(p["state"] in ("intent", "saved", "unknown"))
            _original(p["original"])
            require(a["observation"] in ("complete", "conflict") and a["source_identity"] is not None and p["original"]["digest"] == a["observed_digest"], "identity")
        _failure(a["failure"])
        if a["failure"] is not None and a["failure"]["class"] == "privacy_rejected":
            require(a["observed_digest"] is None and a["publication"] is None, "privacy")
    reg = result["registration"]
    require(reg["state"] in ("not_started", "dispatched", "registered", "unknown"))
    require((reg["state"] == "not_started") == (reg["submission_digest"] is None), "identity")
    require((reg["state"] == "registered") == (reg["receipt_id"] is not None), "identity")
    if reg["submission_digest"] is not None:
        digest(reg["submission_digest"])
    if reg["receipt_id"] is not None:
        identifier(reg["receipt_id"], "tg_review_receipt_")
    return result

def _tracking_record(result):
    binding, attempts = result["binding"], result["attempts"]
    state = result["tracking_state"]
    require(state in ("watching", "frozen", "invalidated"))
    require(result["correction"] is None and len(attempts) == 1, "identity")
    if binding is not None:
        _binding(binding, result["basis"])
    attempt = attempts[0]
    require(attempt["ordinal"] == 0 and attempt["observation"] in ("missing", "complete", "unavailable"))
    identity = attempt["source_identity"]
    if identity is None:
        require(attempt["turn_id"] is None, "identity")
    else:
        _identity(identity)
        require(binding is not None and identity["reviewer_session_id"] == binding["reviewer_session_id"]
                and attempt["turn_id"] == identity["turn_id"], "identity")
        tracking = binding["tracking"]
        require(tracking["boundary"] != "exclusive" or identity["turn_id"] != tracking["boundary_turn_id"], "identity")
    require(attempt["terminal"] in ("unknown", "in_progress", "completed", "failed", "interrupted"))
    require((attempt["terminal"] == "unknown") == (attempt["terminal_source"] is None), "identity")
    if attempt["terminal_source"] is not None:
        require(identity is not None, "identity")
        _source(attempt["terminal_source"], "terminal_observation")
        require(attempt["terminal_source"]["version"] == 2)
    _failure(attempt["failure"])
    frozen = state == "frozen" or state == "invalidated" and attempt["publication"] is not None
    if frozen:
        require(binding is not None and identity is not None and attempt["terminal"] == "completed"
                and attempt["observation"] == "complete" and attempt["failure"] is None
                and attempt["publication"] is not None, "identity")
        digest(attempt["observed_digest"])
        publication = attempt["publication"]
        require(publication["state"] in ("intent", "saved", "unknown"))
        _original(publication["original"])
        require(publication["original"]["digest"] == attempt["observed_digest"], "identity")
    else:
        require(attempt["publication"] is None and attempt["observed_digest"] is None, "identity")
        require(identity is None or attempt["failure"] is not None, "identity")
    reg = result["registration"]
    require(reg["state"] in ("not_started", "dispatched", "registered", "unknown"))
    require((reg["state"] == "not_started") == (reg["submission_digest"] is None), "identity")
    require((reg["state"] == "registered") == (reg["receipt_id"] is not None), "identity")
    require(reg["state"] == "not_started" or frozen, "identity")
    if reg["submission_digest"] is not None:
        digest(reg["submission_digest"])
    if reg["receipt_id"] is not None:
        identifier(reg["receipt_id"], "tg_review_receipt_")
    if "retired_registrations" in result:
        retired = result["retired_registrations"]
        require(frozen and 1 <= len(retired) <= 64 and reg["state"] != "not_started", "identity")
        continuations, digests = set(), {reg["submission_digest"]}
        for entry in retired:
            identifier(entry["continuation_id"], "tg_review_continuation_")
            old = entry["registration"]
            require(entry["continuation_id"] not in continuations and old["state"] in ("dispatched", "unknown")
                and old["receipt_id"] is None and old["submission_digest"] not in digests, "identity")
            digest(old["submission_digest"])
            continuations.add(entry["continuation_id"])
            digests.add(old["submission_digest"])
    return result

def validate_collected_capture(value):
    result = prepare(value, CAPTURE)
    require(result["kind"] == "collected")
    for field in ("consumer_continuation_id", "source_continuation_id"):
        if result[field] is not None:
            identifier(result[field], "tg_review_continuation_")
    identifier(result["source_preparation_id"], "tg_review_preparation_")
    integer(result["consumer_slot"], 1, 8); integer(result["source_slot"], 1, 8)
    session(result["session_id"])
    for field in ("source_basis_digest", "original_digest", "metadata_digest"):
        digest(result[field])
    return result
