"""Pure closed validation for the immutable continuation graph in Bundle three."""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import review_continuation as values
from .evidence_ledger import (EvidenceLedgerError, EvidenceSource, TargetCaptureBinding,
                             build_evidence_reference, canonical_json_bytes, domain_digest)
from .review_session_repository import ReviewSessionBinding
from .task_values import TaskValidationError, validate_text
from .review_answer import AnswerError


GRAPH_KEYS = {"version", "continuations", "reviewers", "retained_originals", "judgments", "resolutions",
              "receipts", "session_bindings", "references", "findings", "effective_receipt_ids"}
CONTINUATION_KEYS = {"continuation_id", "project_id", "task_id", "target_generation", "preparation_id",
    "sequence", "predecessor_id", "basis_json", "basis_digest", "selection_digest", "route", "state", "created_at", "registered_at", "retired_at", "retirement_digest"}
RETAINED_KEYS = {"continuation_id", "prior_continuation_id", "session_id", "original_result_digest", "metadata_digest", "result_receipt_id"}
REVIEWER_KEYS = {"continuation_id", "session_id", "action", "prior_receipt_id", "reason", "result_receipt_id", "original_result_digest"}
JUDGMENT_KEYS = {"review_receipt_id", "judgment_revision", "prior_receipt_id", "continuation_id"}
RESOLUTION_KEYS = {"review_finding_id", "successor_receipt_id", "continuation_id", "resolution_summary", "resolution_digest", "created_at"}
ASSERTION_KEYS = {"review_receipt_id", "reviewer_key", "receipt_kind", "verdict", "summary", "user_approved", "created_at", "review_provenance"}
RECEIPT_KEYS = ASSERTION_KEYS | {"judgment_revision"}
SESSION_KEYS = {"review_receipt_id", "session_id", "execution_id", "binding_source", "original_result_digest"}
FINDING_KEYS = {"review_finding_id", "review_receipt_id", "severity", "summary", "status", "resolution_summary", "created_at", "resolved_at"}
REFERENCE_KEYS = {"evidence_reference_id", "source_kind", "source_state", "source_id", "assurance_class",
    "producer_class", "producer_version", "contract_revision", "authority_snapshot_id", "acceptance_criterion_id",
    "verification_criterion_id", "target_kind", "target_value", "target_base_revision", "target_generation", "completion_cycle_id", "digest"}


def _bad():
    return EvidenceLedgerError("evidence_ledger_inconsistent", "stored continuation evidence is inconsistent")


def _rows(graph, name, keys, identity):
    rows = graph[name]
    if type(rows) is not list:
        raise _bad()
    result = {}
    for row in rows:
        if type(row) is not dict or set(row) != keys:
            raise _bad()
        key = tuple(row[field] for field in identity)
        if any(type(item) is not str or not item for item in key) or key in result:
            raise _bad()
        result[key] = row
    expected = sorted(rows, key=lambda row: tuple(row[field].encode("utf-8") for field in identity))
    if rows != expected:
        raise _bad()
    return result


def _time(value):
    from .storage import validate_utc_timestamp, StorageError
    try:
        validate_utc_timestamp(value, field="continuation evidence time")
    except StorageError as exc:
        raise _bad() from exc


def _reference(reference, source, basis):
    # JSON booleans compare equal to Python integers, and false-y values are
    # not interchangeable with a nullable string in the sealed Reference.
    for field, minimum in (("producer_version", 1), ("contract_revision", 0), ("target_generation", 1)):
        if type(reference[field]) is not int or not minimum <= reference[field] <= (1 << 63) - 1:
            raise _bad()
    base_revision = reference["target_base_revision"]
    if base_revision is not None and type(base_revision) is not str:
        raise _bad()
    binding = TargetCaptureBinding(**{key: basis[key] for key in (
        "target_kind", "target_value", "target_base_revision", "target_generation",
        "authority_snapshot_id", "acceptance_criterion_id", "verification_criterion_id")})
    rebuilt = build_evidence_reference(source=source, project_id=basis["project_id"], task_id=basis["task_id"],
        contract_revision=basis["contract_revision"], binding=binding)
    if (reference["source_kind"], reference["source_state"], reference["source_id"], reference["completion_cycle_id"],
        reference["assurance_class"], reference["producer_class"], reference["producer_version"], reference["digest"]) != (
        source.source_kind, source.source_state, source.source_id, None, rebuilt.attribution.assurance_class,
        rebuilt.attribution.producer_class, rebuilt.attribution.producer_version, rebuilt.digest):
        raise _bad()
    if any(("" if key == "target_base_revision" and reference[key] is None else reference[key]) != basis[key]
        for key in ("contract_revision", "authority_snapshot_id", "acceptance_criterion_id",
        "verification_criterion_id", "target_kind", "target_value", "target_base_revision", "target_generation")):
        raise _bad()
    if not values.identifier(reference["evidence_reference_id"], "tg_evidence_reference_"):
        raise _bad()


@dataclass(frozen=True)
class _GraphRows:
    """Closed, ordered row indexes borrowed only for this validation call."""

    continuations: dict[tuple[str, ...], dict]
    reviewers: dict[tuple[str, ...], dict]
    retained: dict[tuple[str, ...], dict]
    judgments: dict[tuple[str, ...], dict]
    resolutions: dict[tuple[str, ...], dict]
    receipts: dict[tuple[str, ...], dict]
    sessions: dict[tuple[str, ...], dict]
    references: dict[tuple[str, ...], dict]
    findings: dict[tuple[str, ...], dict]
    collected: tuple[dict, ...] = ()


@dataclass(frozen=True)
class _History:
    bases: dict[tuple[str, ...], dict]
    basis: dict
    quality: tuple


def _index_graph(graph) -> _GraphRows:
    if type(graph) is not dict or type(graph.get("version")) is not int or graph["version"] not in (1,2) or set(graph) != GRAPH_KEYS | ({"collected_captures"} if graph["version"] == 2 else set()):
        raise _bad()
    captures = ()
    if graph["version"] == 2:
        from .review_answer_values import validate_capture_evidence
        if type(graph["collected_captures"]) is not list:
            raise _bad()
        captures = tuple(validate_capture_evidence(row) for row in graph["collected_captures"])
        keys = [(r["capture_identity"]["consumer_continuation_id"],r["capture_identity"]["session_id"]) for r in captures]
        if len(set(keys)) != len(keys) or keys != sorted(keys,key=lambda pair:tuple(s.encode("utf-8") for s in pair)):
            raise _bad()
    rows = _GraphRows(
        continuations=_rows(graph, "continuations", CONTINUATION_KEYS, ("continuation_id",)),
        reviewers=_rows(graph, "reviewers", REVIEWER_KEYS, ("continuation_id", "session_id")),
        retained=_rows(graph, "retained_originals", RETAINED_KEYS, ("continuation_id", "session_id")),
        judgments=_rows(graph, "judgments", JUDGMENT_KEYS, ("review_receipt_id",)),
        resolutions=_rows(graph, "resolutions", RESOLUTION_KEYS, ("review_finding_id", "successor_receipt_id")),
        receipts=_rows(graph, "receipts", RECEIPT_KEYS, ("review_receipt_id",)),
        sessions=_rows(graph, "session_bindings", SESSION_KEYS, ("review_receipt_id",)),
        references=_rows(graph, "references", REFERENCE_KEYS, ("evidence_reference_id",)),
        findings=_rows(graph, "findings", FINDING_KEYS, ("review_finding_id",)),
        collected=captures,
    )
    if not rows.continuations or set(rows.judgments) != set(rows.receipts) or not set(rows.sessions) <= set(rows.receipts):
        raise _bad()
    return rows


def _validate_history(rows: _GraphRows) -> _History:
    """Validate every round, predecessor and selection before consuming results."""
    continuations, reviewers = rows.continuations, rows.reviewers
    bases = {}
    rounds = set()
    predecessor_ids = set()
    for key, row in continuations.items():
        basis = values.validate_basis(json.loads(row["basis_json"]))
        if (not values._integer(row["target_generation"], 1)
            or canonical_json_bytes(basis).decode() != row["basis_json"]
            or values.basis_digest(basis) != row["basis_digest"]
            or (row["project_id"], row["task_id"], row["target_generation"])
                != (basis["project_id"], basis["task_id"], basis["target_generation"])
            or not values.identifier(row["continuation_id"], "tg_review_continuation_")
            or not values.identifier(row["preparation_id"], "tg_review_preparation_")
            or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= 64
            or row["route"] not in ("manual", "integrated") or row["state"] not in ("registered", "superseded")):
            raise _bad()
        _time(row["created_at"])
        if row["state"] == "registered":
            _time(row["registered_at"])
            if row["retired_at"] is not None or row["retirement_digest"] is not None:
                raise _bad()
        else:
            _time(row["retired_at"])
            if row["registered_at"] is not None or not values._digest(row["retirement_digest"]):
                raise _bad()
        round_key = (row["preparation_id"], row["sequence"])
        if round_key in rounds:
            raise _bad()
        rounds.add(round_key)
        prior = row["predecessor_id"]
        if row["sequence"] == 1:
            if prior is not None:
                raise _bad()
        else:
            old = continuations.get((prior,))
            if (old is None or prior in predecessor_ids or old["sequence"] + 1 != row["sequence"]
                or old["preparation_id"] != row["preparation_id"] or old["basis_json"] != row["basis_json"] or old["route"] != row["route"]):
                raise _bad()
            predecessor_ids.add(prior)
        choices = [choice for (cid, sid), choice in reviewers.items() if cid == row["continuation_id"]]
        selected = values.validate_selection(dict(version=1, reviewers=[{field: choice[field]
            for field in ("session_id", "action", "prior_receipt_id", "reason")} for choice in choices]))
        if values.selection_digest(selected) != row["selection_digest"]:
            raise _bad()
        bases[key] = basis
    if any(row["state"] == "superseded" and row["continuation_id"] not in predecessor_ids for row in continuations.values()):
        raise _bad()
    basis = next(iter(bases.values()))
    quality = values.quality_identity(basis)
    if any(values.quality_identity(value) != quality for value in bases.values()):
        raise _bad()
    return _History(bases, basis, quality)


def _validate_reviewer_results(rows: _GraphRows, history: _History) -> None:
    result_ids = set()
    for (cid, sid), choice in rows.reviewers.items():
        if (cid,) not in history.bases or sid == history.bases[(cid,)]["parent_session_id"]:
            raise _bad()
        result_id = choice["result_receipt_id"]
        if result_id is None:
            if rows.continuations[(cid,)]["state"] != "superseded" or choice["original_result_digest"] is not None:
                raise _bad()
        elif result_id in result_ids:
            raise _bad()
        else:
            result_ids.add(result_id)


def _validate_retained_originals(rows: _GraphRows, history: _History) -> None:
    """Rebuild original metadata and require every published old slot's consumer."""
    from types import SimpleNamespace
    from .review_session_transport import continued_metadata_digest
    continuations, reviewers, retained = rows.continuations, rows.reviewers, rows.retained
    consumed = set()
    for (cid, sid), capture in retained.items():
        original = values.RetainedOriginal(**{key: capture[key] for key in values.RetainedOriginal.__dataclass_fields__})
        consumer, old = continuations.get((cid,)), continuations.get((original.prior_continuation_id,))
        if consumer is None or old is None:
            raise _bad()
        choice = reviewers.get((old["continuation_id"], sid))
        ancestry = set()
        prior = consumer["predecessor_id"]
        while prior is not None:
            if prior in ancestry or (prior,) not in continuations:
                raise _bad()
            ancestry.add(prior)
            prior = continuations[(prior,)]["predecessor_id"]
        if (old["state"] != "superseded" or old["continuation_id"] not in ancestry or old["basis_json"] != consumer["basis_json"]
            or choice is None or (cid,sid) in reviewers
            or sum(root == cid for root,_ in reviewers) + sum(root == cid for root,_ in retained) > 8):
            raise _bad()
        declared = SimpleNamespace(basis=history.bases[(old["continuation_id"],)], preparation_id=old["preparation_id"], continuation_id=old["continuation_id"], sequence=old["sequence"])
        selected_choice = values.SelectedReviewer(**{key: choice[key] for key in ("session_id", "action", "prior_receipt_id", "reason")})
        if continued_metadata_digest(declared, selected_choice, original.original_result_digest) != original.metadata_digest:
            raise _bad()
        rid = capture["result_receipt_id"]
        if rid is None:
            if consumer["state"] != "superseded":
                raise _bad()
        else:
            session = rows.sessions.get((rid,))
            if (consumer["state"] != "registered" or choice["result_receipt_id"] != rid
                or choice["original_result_digest"] != original.original_result_digest or session is None
                or session["session_id"] != sid or session["original_result_digest"] != original.original_result_digest):
                raise _bad()
            consumed.add(rid)
    for (cid,sid), choice in reviewers.items():
        if continuations[(cid,)]["state"] == "superseded" and choice["result_receipt_id"] is not None and choice["result_receipt_id"] not in consumed and not any(c["result_receipt_id"] == choice["result_receipt_id"] for c in rows.collected):
            raise _bad()


def _validate_collected_captures(rows, history):
    from .review_answer_repository import match_capture_provenance
    consumed=set()
    sources={}
    for capture in rows.collected:
        identity=capture["capture_identity"]
        cid,sid,source_id=identity["consumer_continuation_id"],identity["session_id"],identity["source_continuation_id"]
        consumer=rows.continuations.get((cid,))
        retained_order=sorted({actor for root,actor in rows.retained if root==cid} |
            {row["capture_identity"]["session_id"] for row in rows.collected if row["capture_identity"]["consumer_continuation_id"]==cid})
        selected_count=sum(root==cid for root,_ in rows.reviewers)
        if identity["consumer_slot"] != selected_count + retained_order.index(sid) + 1:
            raise _bad()
        metadata=capture["source_metadata"]
        source_key=(identity["source_preparation_id"],source_id,sid)
        if source_key in sources and sources[source_key] != metadata:
            raise _bad()
        sources[source_key]=metadata
        if consumer is None or (cid,sid) in rows.reviewers or (cid,sid) in rows.retained or consumer["preparation_id"] != identity["source_preparation_id"] or metadata["basis"]["execution_basis"] != history.bases[(cid,)] or metadata["basis"]["route"] != consumer["route"]:
            raise _bad()
        count=sum(root==cid for root,_ in rows.reviewers)+sum(root==cid for root,_ in rows.retained)+sum(c["capture_identity"]["consumer_continuation_id"]==cid for c in rows.collected)
        if count>8:
            raise _bad()
        if source_id is not None:
            chain=set();parent=consumer["predecessor_id"]
            while parent is not None:
                if parent in chain or (parent,) not in rows.continuations:
                    raise _bad()
                chain.add(parent);parent=rows.continuations[(parent,)]["predecessor_id"]
            source=rows.continuations.get((source_id,))
            if source is None or source_id not in chain or source["state"]!="superseded" or source["basis_json"] != consumer["basis_json"] or source["preparation_id"] != consumer["preparation_id"] or (source_id,sid) not in rows.reviewers:
                raise _bad()
            if metadata["basis"]["continuation"] != {key:source[key] for key in ("continuation_id","sequence","selection_digest")}:
                raise _bad()
        rid=capture["result_receipt_id"]
        if rid is None:
            if consumer["state"] != "superseded":
                raise _bad()
            continue
        row=rows.receipts.get((rid,));session=rows.sessions.get((rid,));judgment=rows.judgments.get((rid,))
        if (consumer["state"] != "registered" or rid in consumed or row is None or session is None or judgment is None
            or row["reviewer_key"] != metadata["binding"]["reviewer_key"]
            or session["session_id"] != sid or session["original_result_digest"] != identity["original_digest"].removeprefix("sha256:")
            or session["binding_source"] != "handoff" or session["execution_id"] != history.bases[(cid,)]["execution_id"]
            or judgment["continuation_id"] != source_id or row["review_provenance"] is None or row["review_provenance"]["provenance_version"] != 2):
            raise _bad()
        try:
            match_capture_provenance(capture,row["review_provenance"])
        except Exception as exc:
            raise _bad() from exc
        if source_id is not None and rows.reviewers[(source_id,sid)]["result_receipt_id"] != rid:
            raise _bad()
        consumed.add(rid)
    for row in rows.receipts.values():
        provenance=row["review_provenance"]
        if provenance is None or provenance["provenance_version"] != 2:
            continue
        rid=row["review_receipt_id"]
        cid=rows.judgments[(rid,)]["continuation_id"]
        if cid is not None and rows.continuations[(cid,)]["state"] == "superseded" and rid not in consumed:
            raise _bad()


def _match_bundle_basis(bundle, basis: dict) -> None:
    criteria = {row["kind"]: row["criterion_id"] for row in bundle["criteria"]}
    expected = dict(project_id=bundle["project_id"], task_id=bundle["task"]["task_id"],
        contract_revision=bundle["contract"]["revision"], authority_snapshot_id=bundle["authority_snapshot"]["authority_snapshot_id"],
        artifact_manifest_id=bundle["artifact_manifest"]["artifact_manifest_id"], review_tier=bundle["task"]["review_tier"],
        acceptance_criterion_id=criteria.get("acceptance"), verification_criterion_id=criteria.get("verification"),
        **{"target_" + key: (bundle["target"][key] or "" if key == "base_revision" else bundle["target"][key])
           for key in ("kind", "value", "base_revision", "generation")})
    if any(basis[key] != value for key, value in expected.items()):
        raise _bad()
    if bool(bundle["task"]["verification"].strip()) != (basis["verification_requirement"] == "required"):
        raise _bad()


def _index_references(rows: _GraphRows) -> dict[tuple[str, str], dict]:
    reference_by_source = {}
    for row in rows.references.values():
        key = (row["source_kind"], row["source_id"])
        if row["source_state"] != "recorded" or key in reference_by_source or row["source_kind"] not in ("review_receipt", "review_finding"):
            raise _bad()
        reference_by_source[key] = row
    return reference_by_source


def _validate_receipts(rows: _GraphRows, history: _History,
                       reference_by_source: dict[tuple[str, str], dict]) -> tuple[values.Judgment, ...]:
    """Bind assertions to References and actual sessions before deriving heads."""
    nodes = []
    for key, row in rows.receipts.items():
        for field, required, limit in (("reviewer_key", True, 500), ("summary", False, 1000)):
            validate_text(field, row[field], required=required, limit=limit)
        _time(row["created_at"])
        source = EvidenceSource("review_receipt", "recorded", row["review_receipt_id"],
            {field: row[field] for field in ASSERTION_KEYS})
        if type(row["review_provenance"]) is dict and row["review_provenance"].get("provenance_version") == 2:
            from .review_answer_provenance import validate_provenance_v2
            validate_provenance_v2(row["review_provenance"],project_id=history.basis["project_id"],task_id=history.basis["task_id"],
                review_receipt_id=row["review_receipt_id"],receipt_kind=row["receipt_kind"],target={"kind":history.basis["target_kind"],"value":history.basis["target_value"],"base_revision":history.basis["target_base_revision"],"generation":history.basis["target_generation"],"capture_version":1})
            payload=row["review_provenance"]["answer_attestation"]["payload"]
            actual=sorted((f["severity"],f["summary"]) for f in rows.findings.values() if f["review_receipt_id"]==row["review_receipt_id"])
            expected=sorted((f["severity"],f["summary"].strip()) for f in payload["findings"])
            if payload["verdict"] != row["verdict"] or payload["summary"].strip() != row["summary"] or actual != expected:
                raise _bad()
        reference = reference_by_source.get(("review_receipt", row["review_receipt_id"]))
        if reference is None:
            raise _bad()
        _reference(reference, source, history.basis)
        metadata = rows.judgments[key]
        if (not values._integer(metadata["judgment_revision"], 1)
            or row["judgment_revision"] != metadata["judgment_revision"]):
            raise _bad()
        session = rows.sessions.get(key)
        if session is not None:
            ReviewSessionBinding(**{field: session[field] for field in ("session_id", "execution_id", "binding_source", "original_result_digest")})
        nodes.append(values.Judgment(row["review_receipt_id"], row["reviewer_key"], session["session_id"] if session else None,
            row["judgment_revision"], metadata["prior_receipt_id"], metadata["continuation_id"], row["verdict"], history.quality))
        if metadata["continuation_id"] is not None:
            record = rows.continuations.get((metadata["continuation_id"],))
            choice = rows.reviewers.get((metadata["continuation_id"], session["session_id"])) if session else None
            if (record is None or choice is None or row["receipt_kind"] != "independent"
                or choice["result_receipt_id"] != row["review_receipt_id"] or choice["prior_receipt_id"] != metadata["prior_receipt_id"]
                or session["binding_source"] != "handoff" or session["original_result_digest"] != choice["original_result_digest"]
                or session["execution_id"] != history.bases[(metadata["continuation_id"],)]["execution_id"]):
                raise _bad()
    selected_ids = {row["result_receipt_id"] for row in rows.reviewers.values() if row["result_receipt_id"] is not None}
    if selected_ids != {row.receipt_id for row in nodes if row.continuation_id is not None}:
        raise _bad()
    return tuple(nodes)


def _validate_effective_heads(nodes: tuple[values.Judgment, ...], quality: tuple,
                              declared) -> values.EffectiveJudgments:
    effective = values.select_effective(nodes, quality_basis=quality)
    if declared != [node.receipt_id for node in effective.heads]:
        raise _bad()
    return effective


def _validate_findings(rows: _GraphRows, basis: dict,
                       reference_by_source: dict[tuple[str, str], dict]) -> None:
    for row in rows.findings.values():
        if (row["review_receipt_id"],) not in rows.receipts:
            raise _bad()
        validate_text("review_finding_summary", row["summary"], required=True, limit=1000)
        validate_text("review_finding_resolution", row["resolution_summary"], required=row["status"] == "resolved", limit=1000)
        _time(row["created_at"])
        if row["status"] not in ("open", "resolved"):
            raise _bad()
        if row["status"] == "open":
            if row["resolution_summary"] != "" or row["resolved_at"] is not None:
                raise _bad()
        else:
            _time(row["resolved_at"])
        source = EvidenceSource("review_finding", "recorded", row["review_finding_id"],
            {field: row[field] for field in ("review_finding_id", "review_receipt_id", "severity", "summary", "created_at")})
        reference = reference_by_source.get(("review_finding", row["review_finding_id"]))
        if reference is None:
            raise _bad()
        _reference(reference, source, basis)
    if len(reference_by_source) != len(rows.receipts) + len(rows.findings):
        raise _bad()


def _validate_resolutions(rows: _GraphRows, nodes: tuple[values.Judgment, ...]) -> None:
    """A resolved blocking Finding needs its explicit, sealed ancestry link."""
    by_id = {node.receipt_id: node for node in nodes}
    linked = set()
    for row in rows.resolutions.values():
        finding, successor = rows.findings.get((row["review_finding_id"],)), by_id.get(row["successor_receipt_id"])
        if finding is None or successor is None or finding["status"] != "resolved" or successor.continuation_id != row["continuation_id"]:
            raise _bad()
        validate_text("review_finding_resolution", row["resolution_summary"], required=True, limit=1000)
        _time(row["created_at"])
        prior = successor.prior_receipt_id
        ancestry = set()
        while prior is not None:
            if prior in ancestry or prior not in by_id:
                raise _bad()
            ancestry.add(prior)
            prior = by_id[prior].prior_receipt_id
        if finding["review_receipt_id"] not in ancestry:
            raise _bad()
        digest = domain_digest(b"taskgov-review-reassessment-resolution-v1\0", dict(
            review_finding_id=row["review_finding_id"], successor_receipt_id=row["successor_receipt_id"],
            continuation_id=row["continuation_id"], resolution_summary=row["resolution_summary"],
            original_resolution_summary=finding["resolution_summary"], original_resolved_at=finding["resolved_at"])).removeprefix("sha256:")
        if digest != row["resolution_digest"]:
            raise _bad()
        linked.add(row["review_finding_id"])
    if any(row["status"] == "open" and row["severity"] in ("high", "medium") or
           row["status"] == "resolved" and row["severity"] in ("high", "medium") and row["review_finding_id"] not in linked
           for row in rows.findings.values()):
        raise _bad()


def _match_bundle_receipts(bundle, rows: _GraphRows, history: _History,
                           effective: values.EffectiveJudgments) -> None:
    receipts = rows.receipts
    tier = history.basis["review_tier"]
    required = {0: 0, 1: 1, 2: 2}[tier]
    independent = [node.receipt_id for node in effective.heads
        if receipts[(node.receipt_id,)]["receipt_kind"] == "independent"
        and node.verdict == "pass" and receipts[(node.receipt_id,)]["user_approved"] == 0]
    if tier in (1, 2) and len(independent) >= required:
        selected = independent[:required]
    else:
        kind, verdict, approval = ("not_required", "not_required", 0) if tier == 0 else ("self_review_fallback", "pass", int(tier == 2))
        candidates = sorted(node.receipt_id for node in effective.heads
            if receipts[(node.receipt_id,)]["receipt_kind"] == kind and node.verdict == verdict
            and receipts[(node.receipt_id,)]["user_approved"] == approval)
        if not candidates:
            raise _bad()
        selected = candidates[:1]
    if [row["review_receipt_id"] for row in bundle["review_receipts"]] != selected:
        raise _bad()
    for row in bundle["review_receipts"]:
        if row != {key: receipts[(row["review_receipt_id"],)][key] for key in ASSERTION_KEYS}:
            raise _bad()


def validate_graph(graph, *, bundle=None):
    """Recompute the complete graph; helpers neither mutate nor retain its rows.

    Native admission separately compares seal-time source rows. This pure check
    proves declared consistency, never current authority. Keep dependency order
    and error normalization here, shared by sealing, projection and reentry.
    """
    try:
        rows = _index_graph(graph)
        history = _validate_history(rows)
        _validate_reviewer_results(rows, history)
        _validate_retained_originals(rows, history)
        _validate_collected_captures(rows,history)
        if graph["version"] == 1 and any(type(r["review_provenance"]) is dict and r["review_provenance"].get("provenance_version")==2 for r in rows.receipts.values()):
            raise _bad()
        if bundle is not None:
            _match_bundle_basis(bundle, history.basis)
        references = _index_references(rows)
        nodes = _validate_receipts(rows, history, references)
        effective = _validate_effective_heads(nodes, history.quality, graph["effective_receipt_ids"])
        _validate_findings(rows, history.basis, references)
        _validate_resolutions(rows, nodes)
        if any(node.verdict in ("changes_requested","unable_to_review") for node in effective.heads):
            raise _bad()
        if bundle is not None:
            _match_bundle_receipts(bundle, rows, history, effective)
        if len(canonical_json_bytes(graph)) > 16_777_216:
            raise _bad()
        return graph
    except (TaskValidationError, AnswerError, KeyError, ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise _bad() from exc


def decode_graph_json(raw):
    try:
        if type(raw) is not str:
            raise _bad()
        graph = validate_graph(json.loads(raw))
        if canonical_json_bytes(graph).decode() != raw:
            raise _bad()
        return graph
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _bad() from exc
