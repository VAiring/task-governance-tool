"""Independent Bundle-three graph interpretation, with no producing imports.

The existing independent reader supplies its local codec, privacy, ID, time
and provenance predicates. This module independently walks judgment ancestry
and recomputes selection and explicit Finding exceptions from closed rows.
"""

import json
import re


def validate_graph(payload, api):
    bad = api._invalid

    def integer(value, minimum=0):
        if api._integer(value, minimum=minimum) > (1 << 63) - 1:
            bad()
        return value

    def identifier(value, pattern):
        return api._identifier(value, re.compile(pattern))

    def text(value, field, limit, required=False):
        value = api._text(value)
        if len(value) > limit or required and not value.strip():
            bad()
        api._privacy_guard(field, value)
        return value

    def session_id(value):
        return identifier(value, r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")

    def raw_digest(value):
        return identifier(value, r"[0-9a-f]{64}\Z")

    graph = api._mapping(payload["review_continuation_basis"], (
        "version", "continuations", "reviewers", "retained_originals", "judgments", "resolutions", "receipts",
        "session_bindings", "references", "findings", "effective_receipt_ids"))
    if integer(graph["version"]) != 1:
        bad()

    def rows(name, fields, identity):
        values = api._closed_list(graph[name], fields)
        keys = [tuple(row[field] for field in identity) for row in values]
        if any(type(value) is not str or not value for key in keys for value in key):
            bad()
        if len(set(keys)) != len(keys):
            bad()
        if keys != sorted(keys, key=lambda key: tuple(value.encode("utf-8") for value in key)):
            bad()
        return dict(zip(keys, values))

    continuations = rows("continuations", ("continuation_id", "project_id", "task_id", "target_generation", "preparation_id",
        "sequence", "predecessor_id", "basis_json", "basis_digest", "selection_digest", "route", "state", "created_at", "registered_at", "retired_at", "retirement_digest"), ("continuation_id",))
    reviewers = rows("reviewers", ("continuation_id", "session_id", "action", "prior_receipt_id", "reason", "result_receipt_id", "original_result_digest"), ("continuation_id", "session_id"))
    retained = rows("retained_originals", ("continuation_id", "prior_continuation_id", "session_id", "original_result_digest", "metadata_digest", "result_receipt_id"), ("continuation_id", "session_id"))
    judgments = rows("judgments", ("review_receipt_id", "judgment_revision", "prior_receipt_id", "continuation_id"), ("review_receipt_id",))
    resolutions = rows("resolutions", ("review_finding_id", "successor_receipt_id", "continuation_id", "resolution_summary", "resolution_digest", "created_at"), ("review_finding_id", "successor_receipt_id"))
    assertion_fields = ("review_receipt_id", "reviewer_key", "receipt_kind", "verdict", "summary", "user_approved", "created_at", "review_provenance")
    receipts = rows("receipts", (*assertion_fields, "judgment_revision"), ("review_receipt_id",))
    sessions = rows("session_bindings", ("review_receipt_id", "session_id", "execution_id", "binding_source", "original_result_digest"), ("review_receipt_id",))
    references = rows("references", api._REFERENCE_KEYS, ("evidence_reference_id",))
    findings = rows("findings", ("review_finding_id", "review_receipt_id", "severity", "summary", "status", "resolution_summary", "created_at", "resolved_at"), ("review_finding_id",))
    basis_fields = {"version", "project_id", "task_id", "contract_revision", "authority_snapshot_id", "acceptance_criterion_id",
        "verification_criterion_id", "artifact_manifest_id", "review_tier", "verification_requirement", "verification_not_required_reason",
        "completion_commit_required", "target_kind", "target_value", "target_base_revision", "target_generation", "project_path_hash",
        "project_binding_generation", "parent_session_id", "execution_id", "ownership_generation"}
    if not continuations or set(receipts) != set(judgments) or not set(sessions) <= set(receipts):
        bad()
    criteria = {row["kind"]: row["criterion_id"] for row in payload["criteria"]}
    expected = dict(project_id=payload["project_id"], task_id=payload["task"]["task_id"], contract_revision=payload["contract"]["revision"],
        authority_snapshot_id=payload["authority_snapshot"]["authority_snapshot_id"], artifact_manifest_id=payload["artifact_manifest"]["artifact_manifest_id"],
        acceptance_criterion_id=criteria.get("acceptance"), verification_criterion_id=criteria.get("verification"), review_tier=payload["task"]["review_tier"],
        **{"target_" + key: (payload["target"][key] or "" if key == "base_revision" else payload["target"][key])
           for key in ("kind", "value", "base_revision", "generation")})
    bases, rounds, predecessors = {}, set(), set()
    verification_condition = None
    for (cid,), row in continuations.items():
        try:
            basis = json.loads(api._text(row["basis_json"]))
        except (ValueError, RecursionError):
            bad()
        if (type(basis) is not dict or set(basis) != basis_fields or integer(basis["version"]) != 1
            or api.canonical_json_bytes(basis).decode() != row["basis_json"] or len(row["basis_json"].encode()) > 16384
            or any(basis[key] != value for key, value in expected.items())
            or row["basis_digest"] != api._domain_digest(b"taskgov-review-continuation-basis-v1\0", basis)[7:]
            or (row["project_id"], row["task_id"], row["target_generation"]) != (basis["project_id"], basis["task_id"], basis["target_generation"])
            or row["state"] not in ("registered", "superseded") or row["route"] not in ("manual", "integrated")
            or re.fullmatch(r"tg_review_continuation_[0-9a-f]{16}", cid) is None
            or re.fullmatch(r"tg_review_preparation_[0-9a-f]{16}", row["preparation_id"]) is None):
            bad()
        for field in ("ownership_generation", "project_binding_generation"):
            integer(basis[field], minimum=1)
        integer(basis["contract_revision"])
        integer(basis["target_generation"], minimum=1)
        integer(row["target_generation"], minimum=1)
        if integer(basis["review_tier"]) > 2 or integer(basis["completion_commit_required"]) > 1:
            bad()
        raw_digest(basis["project_path_hash"])
        session_id(basis["parent_session_id"])
        identifier(basis["execution_id"], r"tg_execution_[0-9a-f]{16}\Z")
        requirement = basis["verification_requirement"]
        reason = text(basis["verification_not_required_reason"], "verification_not_required_reason", 1000)
        if (requirement not in ("required", "not_required", "unspecified")
            or (requirement == "not_required") != bool(reason)
            or bool(payload["task"]["verification"].strip()) != (requirement == "required")):
            bad()
        condition = (requirement, reason)
        if verification_condition is not None and verification_condition != condition:
            bad()
        verification_condition = condition
        api._utc_second(row["created_at"])
        if row["state"] == "registered":
            api._utc_second(row["registered_at"])
            if row["retired_at"] is not None or row["retirement_digest"] is not None:
                bad()
        else:
            api._utc_second(row["retired_at"])
            if row["registered_at"] is not None or type(row["retirement_digest"]) is not str or re.fullmatch(r"[0-9a-f]{64}", row["retirement_digest"]) is None:
                bad()
        sequence = integer(row["sequence"], minimum=1)
        if sequence > 64 or (row["preparation_id"], sequence) in rounds:
            bad()
        rounds.add((row["preparation_id"], sequence))
        prior = row["predecessor_id"]
        if sequence == 1:
            if prior is not None:
                bad()
        else:
            old = continuations.get((prior,))
            if old is None or prior in predecessors or old["sequence"] + 1 != sequence or any(old[key] != row[key] for key in ("preparation_id", "basis_json", "route")):
                bad()
            predecessors.add(prior)
        choices = [choice for (root, sid), choice in reviewers.items() if root == cid]
        if not 1 <= len(choices) <= 8:
            bad()
        selection = []
        for choice in choices:
            session_id(choice["session_id"])
            if choice["prior_receipt_id"] is not None:
                api._identifier(choice["prior_receipt_id"], api._REVIEW_ID)
            if choice["action"] not in ("supplement", "reassess") or (choice["action"] == "supplement") != (choice["prior_receipt_id"] is None):
                bad()
            if choice["session_id"] == basis["parent_session_id"]:
                bad()
            text(choice["reason"], "review_continuation_reason", 1000, required=True)
            if choice["reason"] != choice["reason"].strip():
                bad()
            selection.append({field: choice[field] for field in ("session_id", "action", "prior_receipt_id", "reason")})
        if row["selection_digest"] != api._domain_digest(b"taskgov-review-continuation-selection-v1\0", selection)[7:]:
            bad()
        bases[cid] = basis
    if any(cid not in bases for cid, sid in reviewers):
        bad()
    if any(row["state"] == "superseded" and row["continuation_id"] not in predecessors for row in continuations.values()):
        bad()
    published = set()
    for (cid,sid), choice in reviewers.items():
        result = choice["result_receipt_id"]
        if result is None:
            if continuations[(cid,)]["state"] != "superseded" or choice["original_result_digest"] is not None:
                bad()
        elif result in published:
            bad()
        else:
            api._identifier(result, api._REVIEW_ID)
            raw_digest(choice["original_result_digest"])
            published.add(result)
    consumed = set()
    for (cid,sid), capture in retained.items():
        consumer = continuations.get((cid,))
        old = continuations.get((capture["prior_continuation_id"],))
        choice = reviewers.get((capture["prior_continuation_id"],sid))
        if consumer is None or old is None or choice is None:
            bad()
        ancestry = set()
        prior = consumer["predecessor_id"]
        while prior is not None:
            if prior in ancestry or (prior,) not in continuations:
                bad()
            ancestry.add(prior)
            prior = continuations[(prior,)]["predecessor_id"]
        if (old["continuation_id"] not in ancestry or old["state"] != "superseded" or old["basis_json"] != consumer["basis_json"]
            or (cid,sid) in reviewers or sum(root == cid for root,_ in reviewers) + sum(root == cid for root,_ in retained) > 8):
            bad()
        for field in ("original_result_digest", "metadata_digest"):
            if type(capture[field]) is not str or re.fullmatch(r"[0-9a-f]{64}", capture[field]) is None:
                bad()
        old_basis = bases[old["continuation_id"]]
        metadata = dict(version=2, project_id=old_basis["project_id"], execution_id=old_basis["execution_id"],
            session_id=sid, task_id=old_basis["task_id"], contract_revision=old_basis["contract_revision"],
            review_target={key: old_basis["target_" + key] for key in ("kind", "value", "base_revision", "generation")},
            original_result_digest=capture["original_result_digest"], preparation_id=old["preparation_id"],
            continuation_id=old["continuation_id"], continuation_sequence=old["sequence"], action=choice["action"], prior_receipt_id=choice["prior_receipt_id"])
        if api._domain_digest(b"taskgov-review-continuation-retained-metadata-v1\0", metadata)[7:] != capture["metadata_digest"]:
            bad()
        rid = capture["result_receipt_id"]
        if rid is None:
            if consumer["state"] != "superseded":
                bad()
        else:
            session = sessions.get((rid,))
            if (consumer["state"] != "registered" or choice["result_receipt_id"] != rid
                or choice["original_result_digest"] != capture["original_result_digest"] or session is None
                or session["session_id"] != sid or session["original_result_digest"] != capture["original_result_digest"]):
                bad()
            consumed.add(rid)
    if any(continuations[(cid,)]["state"] == "superseded" and choice["result_receipt_id"] is not None and choice["result_receipt_id"] not in consumed for (cid,sid),choice in reviewers.items()):
        bad()
    successors, actors, revisions = {}, {}, set()
    for (rid,), row in receipts.items():
        api._identifier(rid, api._REVIEW_ID)
        api._utc_second(row["created_at"])
        text(row["reviewer_key"], "reviewer_key", 500, required=True)
        text(row["summary"], "summary", 1000)
        if row["reviewer_key"] != row["reviewer_key"].strip():
            bad()
        kind, verdict, approval = row["receipt_kind"], row["verdict"], integer(row["user_approved"])
        if (kind == "independent" and (verdict not in ("pass", "changes_requested") or approval != 0)
            or kind == "self_review_fallback" and (verdict not in ("pass", "changes_requested") or approval not in (0, 1))
            or kind == "not_required" and (verdict != "not_required" or approval != 0 or not row["summary"])
            or kind not in ("independent", "self_review_fallback", "not_required")):
            bad()
        api._validate_review_provenance(row["review_provenance"], project_id=payload["project_id"], task_id=payload["task"]["task_id"],
            target=payload["target"], receipt=row)
        metadata = judgments[(rid,)]
        revision = integer(row["judgment_revision"], minimum=1)
        revision_key = (row["reviewer_key"], revision)
        if integer(metadata["judgment_revision"], minimum=1) != revision or revision_key in revisions:
            bad()
        revisions.add(revision_key)
        session = sessions.get((rid,))
        if session is not None:
            sid = session_id(session["session_id"])
            identifier(session["execution_id"], r"tg_execution_[0-9a-f]{16}\Z")
            if session["binding_source"] not in ("direct", "handoff"):
                bad()
            if session["binding_source"] == "handoff":
                raw_digest(session["original_result_digest"])
            elif session["original_result_digest"] is not None:
                bad()
            if actors.setdefault(sid, row["reviewer_key"]) != row["reviewer_key"]:
                bad()
        cid = metadata["continuation_id"]
        if cid is not None:
            choice = reviewers.get((cid, session["session_id"])) if session is not None else None
            if (choice is None or kind != "independent" or choice["result_receipt_id"] != rid
                or choice["prior_receipt_id"] != metadata["prior_receipt_id"] or session["execution_id"] != bases[cid]["execution_id"]
                or session["binding_source"] != "handoff" or session["original_result_digest"] != choice["original_result_digest"]):
                bad()
        prior = metadata["prior_receipt_id"]
        if revision == 1:
            if prior is not None:
                bad()
        else:
            old = receipts.get((prior,))
            if (old is None or prior in successors or old["judgment_revision"] + 1 != revision or old["reviewer_key"] != row["reviewer_key"]
                or cid is None or session is None or (prior,) not in sessions or sessions[(prior,)]["session_id"] != session["session_id"]):
                bad()
            successors[prior] = rid
    if {row["result_receipt_id"] for row in reviewers.values() if row["result_receipt_id"] is not None} != {rid for (rid,), row in judgments.items() if row["continuation_id"] is not None}:
        bad()
    heads = sorted((row for (rid,), row in receipts.items() if rid not in successors), key=lambda row: (row["reviewer_key"].encode(), row["review_receipt_id"].encode()))
    if graph["effective_receipt_ids"] != [row["review_receipt_id"] for row in heads] or any(row["verdict"] == "changes_requested" for row in heads):
        bad()
    passes = [row for row in heads if row["receipt_kind"] == "independent" and row["verdict"] == "pass" and row["user_approved"] == 0]
    required = {0: 0, 1: 1, 2: 2}[payload["task"]["review_tier"]]
    if required and len(passes) >= required:
        selected = passes[:required]
    else:
        kind = "not_required" if required == 0 else "self_review_fallback"
        selected = sorted((row for row in heads if row["receipt_kind"] == kind and row["user_approved"] == int(required == 2)), key=lambda row: row["review_receipt_id"].encode())[:1]
    if payload["review_receipts"] != [{field: row[field] for field in assertion_fields} for row in selected]:
        bad()
    source_ids = set()
    for ref in references.values():
        api._identifier(ref["evidence_reference_id"], api._REFERENCE_ID)
        integer(ref["producer_version"], minimum=1)
        integer(ref["contract_revision"])
        integer(ref["target_generation"], minimum=1)
        reference_base = api._text(ref["target_base_revision"], nullable=True)
        if reference_base is None:
            reference_base = ""
        kind, rid = ref["source_kind"], ref["source_id"]
        if (kind, rid) in source_ids or kind not in ("review_receipt", "review_finding"):
            bad()
        source_ids.add((kind, rid))
        source = ({field: receipts[(rid,)][field] for field in assertion_fields} if kind == "review_receipt" else
            {field: findings[(rid,)][field] for field in ("review_finding_id", "review_receipt_id", "severity", "summary", "created_at")})
        if (ref["source_state"] != "recorded" or ref["completion_cycle_id"] is not None or
            (ref["assurance_class"], ref["producer_class"], ref["producer_version"]) != ("bound_attestation", "trusted_caller", 1)):
            bad()
        digest_value = {key: ref[key] for key in api._REFERENCE_KEYS if key not in ("evidence_reference_id", "digest")}
        digest_value.update(project_id=payload["project_id"], task_id=payload["task"]["task_id"], source_projection=source,
                            target_base_revision=reference_base)
        if ref["digest"] != api._domain_digest(api.EVIDENCE_REFERENCE_DOMAIN, digest_value):
            bad()
        for field in ("contract_revision", "authority_snapshot_id", "acceptance_criterion_id", "verification_criterion_id", "target_kind", "target_value", "target_generation"):
            if ref[field] != expected[field]:
                bad()
        if reference_base != expected["target_base_revision"]:
            bad()
    if source_ids != {("review_receipt", rid) for (rid,) in receipts} | {("review_finding", fid) for (fid,) in findings}:
        bad()
    linked = set()
    for row in resolutions.values():
        finding = findings[(row["review_finding_id"],)]
        if finding["status"] != "resolved" or judgments[(row["successor_receipt_id"],)]["continuation_id"] != row["continuation_id"]:
            bad()
        ancestor = judgments[(row["successor_receipt_id"],)]["prior_receipt_id"]
        seen = set()
        while ancestor is not None:
            if ancestor in seen:
                bad()
            seen.add(ancestor)
            ancestor = judgments[(ancestor,)]["prior_receipt_id"]
        if finding["review_receipt_id"] not in seen:
            bad()
        text(row["resolution_summary"], "review_finding_resolution", 1000, required=True)
        api._utc_second(row["created_at"])
        digest = dict(review_finding_id=row["review_finding_id"], successor_receipt_id=row["successor_receipt_id"], continuation_id=row["continuation_id"],
            resolution_summary=row["resolution_summary"], original_resolution_summary=finding["resolution_summary"], original_resolved_at=finding["resolved_at"])
        if row["resolution_digest"] != api._domain_digest(b"taskgov-review-reassessment-resolution-v1\0", digest)[7:]:
            bad()
        linked.add(row["review_finding_id"])
    for (fid,), row in findings.items():
        api._identifier(fid, api._FINDING_ID)
        text(row["summary"], "review_finding_summary", 1000, required=True)
        text(row["resolution_summary"], "review_finding_resolution", 1000, required=row["status"] == "resolved")
        api._utc_second(row["created_at"])
        if ((row["review_receipt_id"],) not in receipts or row["status"] not in ("open", "resolved")
            or row["severity"] not in ("high", "medium", "low")):
            bad()
        if row["status"] == "resolved":
            api._utc_second(row["resolved_at"])
        elif row["resolution_summary"] != "" or row["resolved_at"] is not None:
            bad()
        if row["severity"] in ("high", "medium") and (row["status"] != "resolved" or fid not in linked):
            bad()
    return graph
