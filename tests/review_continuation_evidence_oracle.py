"""Independent Bundle-three graph interpretation, with no producing imports.

The existing independent reader supplies its local codec, privacy, ID, time
and provenance predicates. This module independently walks judgment ancestry
and recomputes selection and explicit Finding exceptions from closed rows.
"""

import json
import re


class _CollectedReader:
    """Independent closed-value checks using only the Reader's local primitives."""
    def __init__(self, api):
        self.api = api

    def need(self, condition):
        if not condition:
            self.api._invalid()

    def obj(self, value, fields):
        return self.api._mapping(value, tuple(fields.split()))

    def number(self, value, low=0, high=(1 << 63)-1):
        self.need(low <= self.api._integer(value, minimum=low) <= high)

    def text(self, value, maximum=1000, required=True):
        value = self.api._text(value)
        self.need(len(value) <= maximum and (not required or bool(value.strip())))
        self.api._privacy_guard("collected_review", value)
        return value

    def pattern(self, value, pattern):
        self.need(type(value) is str and re.fullmatch(pattern, value, re.ASCII) is not None)
        self.api._privacy_guard("collected_review", value)

    def sid(self, value):
        self.pattern(value, r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")

    def tool(self, value, prefix):
        self.pattern(value, re.escape(prefix)+r"[0-9a-f]{16}")

    def hostid(self, value):
        self.pattern(value, r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,255}")

    def source(self, value, operation, version=1):
        value=self.obj(value,"adapter version operation")
        self.number(value["version"],version,version)
        self.need(value["adapter"] == "codex-app-tools-review-answer" and value["operation"] == operation)

    def assignment(self, value):
        value=self.obj(value,"kind review_profiles review_lenses requested_model requested_effort requested_skill context_relation")
        self.need(value["kind"] == "independent")
        for field, vocabulary, maximum in (("review_profiles",self.api._REVIEW_PROFILES,4),("review_lenses",self.api._REVIEW_LENSES,8)):
            entries=self.api._array(value[field])
            self.need(all(type(item) is str for item in entries))
            self.need(len(entries)<=maximum and len(entries)==len(set(entries)) and entries==[item for item in vocabulary if item in entries])
        if value["requested_model"] is not None:
            self.pattern(value["requested_model"],r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}")
        self.need(value["requested_effort"] is None or value["requested_effort"] in ("none","minimal","low","medium","high","xhigh","max","ultra"))
        if value["requested_skill"] is not None:
            skill=self.obj(value["requested_skill"],"id version")
            self.pattern(skill["id"],r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}")
            self.pattern(skill["version"],r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}")
        self.need(value["context_relation"] is None or value["context_relation"] in self.api._CONTEXT_RELATIONS)

    def original(self, value):
        value=self.obj(value,"byte_length digest payload_start payload_length payload_digest")
        self.number(value["byte_length"],1,262144);self.number(value["payload_start"]);self.number(value["payload_length"],1,262144)
        self.need(value["payload_start"]+value["payload_length"]<=value["byte_length"])
        self.api._digest(value["digest"]);self.api._digest(value["payload_digest"])

    def answer(self,value):
        value=self.obj(value,"verdict summary findings checks limitations")
        self.need(value["verdict"] in ("pass","changes_requested","unable_to_review"))
        self.text(value["summary"])
        findings=self.api._array(value["findings"])
        self.need(len(findings)<=64)
        pairs=[]
        for finding in findings:
            finding=self.obj(finding,"severity summary")
            self.need(finding["severity"] in ("high","medium","low"));self.text(finding["summary"])
            pairs.append((finding["severity"],finding["summary"].strip()))
        self.need(len(set(pairs))==len(pairs))
        for key in ("checks","limitations"):
            entries=self.api._array(value[key]);self.need(len(entries)<=32)
            for entry in entries:self.text(entry)

    def execution(self,value):
        value=self.obj(value,"version project_id task_id contract_revision authority_snapshot_id acceptance_criterion_id verification_criterion_id artifact_manifest_id review_tier verification_requirement verification_not_required_reason completion_commit_required target_kind target_value target_base_revision target_generation project_path_hash project_binding_generation parent_session_id execution_id ownership_generation")
        self.need(len(self.api.canonical_json_bytes(value))<=16384)
        self.number(value["version"],1,1);self.number(value["contract_revision"])
        self.number(value["review_tier"],0,2);self.number(value["completion_commit_required"],0,1)
        for key in ("target_generation","project_binding_generation","ownership_generation"):self.number(value[key],1)
        for key,prefix in (("task_id","tg_task_"),("authority_snapshot_id","tg_authority_snapshot_"),("artifact_manifest_id","tg_artifact_manifest_"),("execution_id","tg_execution_")):
            self.tool(value[key],prefix)
        for key in ("acceptance_criterion_id","verification_criterion_id"):
            if value[key] is not None:self.tool(value[key],"tg_contract_criterion_")
        self.text(value["project_id"],500);self.text(value["target_value"],500);self.text(value["target_base_revision"],500,False)
        self.text(value["verification_not_required_reason"],1000,False)
        self.pattern(value["project_path_hash"],r"[0-9a-f]{64}");self.sid(value["parent_session_id"])
        self.need(value["target_kind"] in ("git_commit","git_snapshot","diff_fingerprint","external_revision"))
        self.need((value["target_kind"]=="git_snapshot")==bool(value["target_base_revision"]))
        self.need(value["verification_requirement"] in ("required","not_required","unspecified") and (value["verification_requirement"]=="not_required")==bool(value["verification_not_required_reason"]))
        return value

    def metadata(self,value):
        value=self.obj(value,"basis binding attempt source_identity original answer_source terminal terminal_source correction_binding")
        self.need(len(self.api.canonical_json_bytes(value))<=32768)
        basis=self.obj(value["basis"],"version preparation_id packet_digest request_digest protocol attestation route continuation execution_basis")
        self.number(basis["version"],1,2);self.tool(basis["preparation_id"],"tg_review_preparation_")
        self.api._digest(basis["packet_digest"]);self.api._digest(basis["request_digest"])
        self.need(basis["protocol"]=="review-answer-v1" and basis["attestation"]=="assigned-independent-review-v1" and basis["route"] in ("manual","integrated"))
        execution=self.execution(basis["execution_basis"])
        if basis["continuation"] is not None:
            cont=self.obj(basis["continuation"],"continuation_id sequence selection_digest")
            self.tool(cont["continuation_id"],"tg_review_continuation_");self.number(cont["sequence"],1,64);self.pattern(cont["selection_digest"],r"[0-9a-f]{64}")
        tracking=basis["version"]==2
        fields="basis_digest slot reviewer_key parent_session_id reviewer_session_id binding_source assignment "
        binding=self.obj(value["binding"],fields+("tracking" if tracking else "dispatch_id initial_turn_id"))
        self.need(binding["basis_digest"]==self.api._domain_digest(b"taskgov-review-answer-basis-v1\0",basis))
        self.number(binding["slot"],1,8);self.text(binding["reviewer_key"],500)
        self.need(binding["reviewer_key"]==binding["reviewer_key"].strip())
        self.sid(binding["parent_session_id"]);self.sid(binding["reviewer_session_id"])
        if tracking:
            window=self.obj(binding["tracking"],"kind boundary boundary_turn_id dispatch_id")
            self.need(window["kind"] in ("initial","continuation"))
            self.sid(window["boundary_turn_id"])
            self.need((basis["continuation"] is None)==(window["kind"]=="initial"))
            if window["kind"]=="initial":
                self.need(window["boundary"]=="inclusive");self.hostid(window["dispatch_id"])
            else:
                self.need(window["boundary"]=="exclusive" and window["dispatch_id"] is None)
        else:
            self.hostid(binding["dispatch_id"]);self.sid(binding["initial_turn_id"])
        self.need(binding["parent_session_id"]==execution["parent_session_id"] and binding["parent_session_id"]!=binding["reviewer_session_id"])
        self.source(binding["binding_source"],"reviewer_tracking" if tracking else "dispatch_binding",basis["version"]);self.assignment(binding["assignment"])
        identity=self.obj(value["source_identity"],"reviewer_session_id turn_id message_id")
        self.sid(identity["turn_id"]);self.hostid(identity["message_id"])
        self.need(identity["reviewer_session_id"]==binding["reviewer_session_id"])
        self.original(value["original"]);self.number(value["attempt"],0,1)
        self.need(value["terminal"]=="completed")
        self.source(value["answer_source"],"answer_observation",basis["version"]);self.source(value["terminal_source"],"terminal_observation",basis["version"])
        correction=value["correction_binding"]
        if tracking:
            self.need(value["attempt"]==0 and correction is None)
            self.need(window["boundary"]!="exclusive" or identity["turn_id"]!=window["boundary_turn_id"])
        elif value["attempt"]==0:
            self.need(correction is None and identity["turn_id"]==binding["initial_turn_id"])
        else:
            correction=self.obj(correction,"intent_id initial_turn_id correction_turn_id source")
            self.tool(correction["intent_id"],"tg_review_correction_")
            self.need(correction["initial_turn_id"]==binding["initial_turn_id"] and correction["correction_turn_id"]==identity["turn_id"] and identity["turn_id"]!=binding["initial_turn_id"])
            self.source(correction["source"],"correction_binding")
        return value


def validate_collected_provenance(value, project_id, task_id, target, receipt, api):
    c=_CollectedReader(api)
    value=c.obj(value,"review_provenance_id provenance_version answer_attestation assignment host_observation assurance_class producer_class producer_version digest")
    c.need(len(api.canonical_json_bytes(value))<=262144)
    c.tool(value["review_provenance_id"],"tg_review_provenance_");c.number(value["provenance_version"],2,2);c.number(value["producer_version"],1,1)
    c.need(receipt["receipt_kind"]=="independent" and value["assurance_class"]=="bound_attestation" and value["producer_class"]=="taskgov_answer_collector")
    a=c.obj(value["answer_attestation"],"protocol attestation request_digest original payload")
    c.need(a["protocol"]=="review-answer-v1" and a["attestation"]=="assigned-independent-review-v1")
    api._digest(a["request_digest"]);c.original(a["original"]);c.answer(a["payload"]);c.assignment(value["assignment"])
    c.need(a["payload"]["verdict"]==receipt["verdict"] and a["payload"]["summary"].strip()==receipt["summary"])
    h=c.obj(value["host_observation"],"basis_digest preparation_id slot attempt parent_session_id reviewer_session_id turn_id message_id binding_source answer_source terminal_source model skill methods")
    api._digest(h["basis_digest"]);c.tool(h["preparation_id"],"tg_review_preparation_");c.number(h["slot"],1,8);c.number(h["attempt"],0,1)
    for field in ("parent_session_id","reviewer_session_id","turn_id"):c.sid(h[field])
    c.need(h["parent_session_id"]!=h["reviewer_session_id"]);c.hostid(h["message_id"])
    source=c.obj(h["binding_source"],"adapter version operation")
    version=source["version"];c.number(version,1,2)
    if version==2:c.need(h["attempt"]==0)
    for field,operation in (("binding_source","reviewer_tracking" if version==2 else "dispatch_binding"),("answer_source","answer_observation"),("terminal_source","terminal_observation")):c.source(h[field],operation,version)
    for field in ("model","skill","methods"):
        fact=c.obj(h[field],"state value source")
        c.need(fact==dict(state="unknown",value=None,source=None))
    sealed_target=dict(target);sealed_target["base_revision"]=sealed_target["base_revision"] or ""
    digest_input=dict(project_id=project_id,task_id=task_id,review_receipt_id=receipt["review_receipt_id"],receipt_kind=receipt["receipt_kind"],target=sealed_target,
        provenance={k:v for k,v in value.items() if k not in ("review_provenance_id","digest")})
    c.need(value["digest"]==api._domain_digest(b"taskgov-review-provenance-v2\0",digest_input))
    return value


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

    graph_fields = (
        "version", "continuations", "reviewers", "retained_originals", "judgments", "resolutions", "receipts",
        "session_bindings", "references", "findings", "effective_receipt_ids")
    new_graph = payload["bundle_version"] == 4
    graph = api._mapping(payload["review_continuation_basis"], (*graph_fields, "collected_captures") if new_graph else graph_fields)
    if integer(graph["version"]) != (2 if new_graph else 1):
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
    # Validate relation keys before regex, hashing or dereferencing. Null is
    # admitted only for the historical/initial relations named here; the
    # semantic checks below still decide whether it is legal in that row.
    continuation_id = re.compile(r"tg_review_continuation_[0-9a-f]{16}\Z")
    preparation_id = re.compile(r"tg_review_preparation_[0-9a-f]{16}\Z")
    relation_groups = (
        (continuations, (("preparation_id", preparation_id, False, None),
                         ("predecessor_id", continuation_id, True, continuations))),
        (reviewers, (("continuation_id", continuation_id, False, continuations),
                     ("prior_receipt_id", api._REVIEW_ID, True, receipts),
                     ("result_receipt_id", api._REVIEW_ID, True, receipts))),
        (retained, (("continuation_id", continuation_id, False, continuations),
                    ("prior_continuation_id", continuation_id, False, continuations),
                    ("result_receipt_id", api._REVIEW_ID, True, receipts))),
        (judgments, (("prior_receipt_id", api._REVIEW_ID, True, receipts),
                     ("continuation_id", continuation_id, True, continuations))),
        (resolutions, (("review_finding_id", api._FINDING_ID, False, findings),
                       ("successor_receipt_id", api._REVIEW_ID, False, judgments),
                       ("continuation_id", continuation_id, False, continuations))),
        (findings, (("review_receipt_id", api._REVIEW_ID, False, receipts),)),
    )
    for collection, relations in relation_groups:
        for row in collection.values():
            for field, pattern, nullable, destination in relations:
                value = row[field]
                if value is None and nullable:
                    continue
                api._identifier(value, pattern)
                if destination is not None and (value,) not in destination:
                    bad()
    for ref in references.values():
        kind = ref["source_kind"]
        if kind not in ("review_receipt", "review_finding"):
            bad()
        destination, pattern = ((receipts, api._REVIEW_ID) if kind == "review_receipt"
                                else (findings, api._FINDING_ID))
        api._identifier(ref["source_id"], pattern)
        if (ref["source_id"],) not in destination:
            bad()
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
            if old is None or prior in predecessors or integer(old["sequence"], minimum=1) + 1 != sequence or any(old[key] != row[key] for key in ("preparation_id", "basis_json", "route")):
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
    collected_consumers = {}
    capture_rows = api._array(graph["collected_captures"]) if new_graph else []
    capture_keys=[]
    occupied_slots=set()
    frozen_sources={}
    c=_CollectedReader(api)
    for frozen in capture_rows:
        frozen=c.obj(frozen,"capture_identity source_metadata evidence_digest result_receipt_id")
        c.need(len(api.canonical_json_bytes(frozen))<=32768)
        identity=c.obj(frozen["capture_identity"],"kind consumer_continuation_id consumer_slot source_preparation_id source_continuation_id source_slot session_id source_basis_digest original_digest")
        c.need(identity["kind"]=="collected")
        cid,sid=identity["consumer_continuation_id"],identity["session_id"]
        c.tool(cid,"tg_review_continuation_");c.sid(sid);c.number(identity["consumer_slot"],1,8);c.number(identity["source_slot"],1,8)
        c.tool(identity["source_preparation_id"],"tg_review_preparation_")
        c.need((cid,sid) not in collected_consumers and (cid,sid) not in retained and (cid,sid) not in reviewers)
        c.need((cid,identity["consumer_slot"]) not in occupied_slots)
        occupied_slots.add((cid,identity["consumer_slot"]))
        capture_keys.append((cid,sid));collected_consumers[(cid,sid)]=frozen
        metadata=c.metadata(frozen["source_metadata"])
        source_basis,binding=metadata["basis"],metadata["binding"]
        source_cont=source_basis["continuation"]
        source_cid=None if source_cont is None else source_cont["continuation_id"]
        source_key=(source_basis["preparation_id"],source_cid,sid)
        if source_key in frozen_sources:
            c.need(frozen_sources[source_key]==metadata)
        else:
            frozen_sources[source_key]=metadata
        c.need(identity["source_continuation_id"]==source_cid and identity["source_preparation_id"]==source_basis["preparation_id"] and
            identity["source_slot"]==binding["slot"] and identity["session_id"]==binding["reviewer_session_id"] and
            identity["source_basis_digest"]==binding["basis_digest"] and identity["original_digest"]==metadata["original"]["digest"])
        c.need(frozen["evidence_digest"]==api._domain_digest(b"taskgov-collected-review-capture-evidence-v1\0",dict(capture_identity=identity,source_metadata=metadata)))
        consumer=continuations.get((cid,))
        c.need(consumer is not None and source_basis["execution_basis"]==bases[cid] and source_basis["route"]==consumer["route"] and source_basis["preparation_id"]==consumer["preparation_id"])
        ancestors=set();ancestor=consumer["predecessor_id"]
        while ancestor is not None:
            c.need(ancestor not in ancestors and (ancestor,) in continuations)
            ancestors.add(ancestor);ancestor=continuations[(ancestor,)]["predecessor_id"]
        if source_cid is not None:
            source=continuations.get((source_cid,))
            c.need(source_cid in ancestors and source["state"]=="superseded" and source_cont["sequence"]==source["sequence"] and source_cont["selection_digest"]==source["selection_digest"])
            c.need((source_cid,sid) in reviewers)
        rid=frozen["result_receipt_id"]
        if rid is None:
            c.need(consumer["state"]=="superseded")
        else:
            c.tool(rid,"tg_review_receipt_")
            receipt=receipts.get((rid,));session=sessions.get((rid,));judgment=judgments.get((rid,))
            c.need(consumer["state"]=="registered" and rid not in consumed and receipt is not None and session is not None and judgment is not None)
            c.need(judgment["continuation_id"]==source_cid and session["session_id"]==sid and session["binding_source"]=="handoff" and session["execution_id"]==bases[cid]["execution_id"] and
                session["original_result_digest"]==identity["original_digest"][7:] and receipt["reviewer_key"]==binding["reviewer_key"])
            provenance=validate_collected_provenance(receipt["review_provenance"],payload["project_id"],payload["task"]["task_id"],payload["target"],receipt,api)
            attestation,host=provenance["answer_attestation"],provenance["host_observation"]
            c.need(all(attestation[key]==source_basis[key] for key in ("protocol","attestation","request_digest")) and attestation["original"]==metadata["original"] and provenance["assignment"]==binding["assignment"])
            overlap=dict(basis_digest=binding["basis_digest"],preparation_id=source_basis["preparation_id"],slot=binding["slot"],attempt=metadata["attempt"],
                parent_session_id=binding["parent_session_id"],reviewer_session_id=sid,turn_id=metadata["source_identity"]["turn_id"],message_id=metadata["source_identity"]["message_id"],
                binding_source=binding["binding_source"],answer_source=metadata["answer_source"],terminal_source=metadata["terminal_source"])
            c.need(all(host[key]==value for key,value in overlap.items()))
            consumed.add(rid)
    c.need(capture_keys==sorted(capture_keys,key=lambda key:tuple(value.encode() for value in key)))
    for (cid,sid),frozen in collected_consumers.items():
        retained_order=sorted({actor for root,actor in retained if root==cid} |
            {actor for root,actor in collected_consumers if root==cid})
        selected_count=sum(root==cid for root,_ in reviewers)
        c.need(frozen["capture_identity"]["consumer_slot"]==selected_count+retained_order.index(sid)+1)
    for (cid,),consumer in continuations.items():
        c.need(sum(root==cid for root,_ in reviewers)+sum(root==cid for root,_ in retained)+sum(root==cid for root,_ in collected_consumers)<=8)
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
        source_receipt = receipts.get((choice["result_receipt_id"],))
        if source_receipt is not None and type(source_receipt["review_provenance"]) is dict and source_receipt["review_provenance"].get("provenance_version") == 2:
            bad()
        if rid is None:
            if consumer["state"] != "superseded":
                bad()
        else:
            session = sessions.get((rid,))
            if (rid in consumed or consumer["state"] != "registered" or choice["result_receipt_id"] != rid
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
        if (kind == "independent" and (verdict not in (("pass", "changes_requested", "unable_to_review") if new_graph else ("pass", "changes_requested")) or approval != 0)
            or kind == "self_review_fallback" and (verdict not in ("pass", "changes_requested") or approval not in (0, 1))
            or kind == "not_required" and (verdict != "not_required" or approval != 0 or not row["summary"])
            or kind not in ("independent", "self_review_fallback", "not_required")):
            bad()
        api._validate_review_provenance(row["review_provenance"], project_id=payload["project_id"], task_id=payload["task"]["task_id"],
            target=payload["target"], receipt=row, allow_v2=new_graph)
        if verdict=="unable_to_review" and row["review_provenance"].get("provenance_version")!=2:
            bad()
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
            if (old is None or prior in successors or integer(old["judgment_revision"], minimum=1) + 1 != revision or old["reviewer_key"] != row["reviewer_key"]
                or cid is None or session is None or (prior,) not in sessions or sessions[(prior,)]["session_id"] != session["session_id"]):
                bad()
            successors[prior] = rid
    for (rid,),row in receipts.items():
        provenance=row["review_provenance"]
        if type(provenance) is dict and provenance.get("provenance_version")==2:
            host=provenance["host_observation"]
            session=sessions.get((rid,))
            if session is None or session["session_id"]!=host["reviewer_session_id"] or session["binding_source"]!="handoff" or session["original_result_digest"]!=provenance["answer_attestation"]["original"]["digest"][7:]:bad()
            cid=judgments[(rid,)]["continuation_id"]
            if cid is not None:
                if host["preparation_id"]!=continuations[(cid,)]["preparation_id"]:bad()
                if continuations[(cid,)]["state"]=="superseded" and rid not in consumed:bad()
            actual=[(f["severity"],f["summary"]) for f in findings.values() if f["review_receipt_id"]==rid]
            expected_findings=[(f["severity"],f["summary"].strip()) for f in provenance["answer_attestation"]["payload"]["findings"]]
            if sorted(actual)!=sorted(expected_findings):bad()
    if {row["result_receipt_id"] for row in reviewers.values() if row["result_receipt_id"] is not None} != {rid for (rid,), row in judgments.items() if row["continuation_id"] is not None}:
        bad()
    heads = sorted((row for (rid,), row in receipts.items() if rid not in successors), key=lambda row: (row["reviewer_key"].encode(), row["review_receipt_id"].encode()))
    if graph["effective_receipt_ids"] != [row["review_receipt_id"] for row in heads] or any(row["verdict"] in ("changes_requested","unable_to_review") for row in heads):
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
        collected_reference = kind == "review_receipt" and type(source["review_provenance"]) is dict and source["review_provenance"].get("provenance_version") == 2
        if (ref["source_state"] != "recorded" or ref["completion_cycle_id"] is not None or
            (ref["assurance_class"], ref["producer_class"], ref["producer_version"]) != ("bound_attestation", "taskgov_answer_collector" if collected_reference else "trusted_caller", 1)):
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
