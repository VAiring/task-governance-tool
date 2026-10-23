"""Independent Reader conformance; producer validators are disabled during reads."""
from __future__ import annotations
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"task-governance-tool"/"scripts"))
sys.path.insert(0,str(ROOT/"tests"))
from tests import evidence_reader_oracle as reader
from tests.review_continuation_evidence_oracle import _CollectedReader
from tests.test_evidence_reader_oracle import _source21_not_required_payload
from tests.evidence_test_support import index_entries, reference_json_bytes
from tests.test_review_answer_pure_values import metadata_fixture
from tests.test_review_answer_pure_provenance import context
from task_governance_tool import review_answer_values as values, review_answer_provenance as producer
from tests.review_answer_fixture_support import review_fixture
from tests.review_continuation_evidence_oracle import validate_graph
import json

def receipt_and_provenance(attempt=0):
    m,a=metadata_fixture(attempt);ctx=context(m)
    p=producer.build_provenance_v2(metadata=m,answer=a,**ctx)
    r=dict(review_receipt_id=ctx["review_receipt_id"],receipt_kind="independent",verdict="pass",summary=a.payload["summary"].strip())
    return m,p,r,ctx

def reseal(p,ctx):
    body={**ctx,"provenance":{key:value for key,value in p.items() if key not in ("review_provenance_id","digest")}}
    p["digest"]=reader._domain_digest(b"taskgov-review-provenance-v2\0",body)

class CollectedReaderTests(unittest.TestCase):
    def test_v2_is_independent_and_explicitly_version_gated(self):
        m,p,r,ctx=receipt_and_provenance(1)
        with patch.object(producer,"validate_provenance_v2",side_effect=AssertionError("product validator called")),patch.object(values,"validate_metadata",side_effect=AssertionError("product validator called")):
            result=reader._validate_review_provenance(p,project_id=ctx["project_id"],task_id=ctx["task_id"],target=ctx["target"],receipt=r,allow_v2=True)
            self.assertEqual(result,p)
            with self.assertRaises(reader.EvidenceConsumerError):
                reader._validate_review_provenance(p,project_id=ctx["project_id"],task_id=ctx["task_id"],target=ctx["target"],receipt=r)

    def test_recomputed_provenance_seals_cannot_hide_nested_corruption(self):
        mutations=[lambda p:p["answer_attestation"]["original"].update(path="private/path"),
            lambda p:p["host_observation"]["model"].update(state="observed",value="claimed-model"),
            lambda p:p["host_observation"]["answer_source"].update(operation="terminal_observation"),
            lambda p:p["host_observation"].update(slot=True),
            lambda p:p["host_observation"].update(turn_id="not-a-turn"),
            lambda p:p["assignment"].update(review_lenses=["privacy","correctness"]),
            lambda p:p["answer_attestation"]["payload"].update(findings=[dict(severity="high",summary="x"),dict(severity="high",summary=" x ")])]
        for mutate in mutations:
            m,p,r,ctx=receipt_and_provenance();mutate(p);reseal(p,ctx)
            with self.subTest(mutate=mutate),self.assertRaises(reader.EvidenceConsumerError):
                reader._validate_review_provenance(p,project_id=ctx["project_id"],task_id=ctx["task_id"],target=ctx["target"],receipt=r,allow_v2=True)

    def test_public_metadata_correction_and_basis_are_cross_checked(self):
        m,_=metadata_fixture(1);public=values.source_metadata_projection(m)
        with patch.object(values,"validate_public_metadata",side_effect=AssertionError("product validator called")):
            self.assertEqual(_CollectedReader(reader).metadata(public),public)
        changed=copy.deepcopy(public)
        changed["correction_binding"]["correction_turn_id"]=changed["binding"]["initial_turn_id"]
        with self.assertRaises(reader.EvidenceConsumerError):_CollectedReader(reader).metadata(changed)
        changed=copy.deepcopy(public);changed["basis"]["execution_basis"]["parent_session_id"]=changed["binding"]["reviewer_session_id"]
        changed["binding"]["basis_digest"]=reader._domain_digest(b"taskgov-review-answer-basis-v1\0",changed["basis"])
        with self.assertRaises(reader.EvidenceConsumerError):_CollectedReader(reader).metadata(changed)

    def test_bundle_four_and_index_three_are_separate_closed_versions(self):
        payload=_source21_not_required_payload()
        payload.update(source_schema_version=30,bundle_version=4,review_continuation_basis=None)
        bundle=dict(format_version=4,payload=payload,bundle_digest=reader._domain_digest(reader.BUNDLE_V4_DOMAIN,payload))
        document=reference_json_bytes(bundle)+b"\n"
        entries=index_entries(document,bundle["bundle_digest"])
        for entry in entries:entry["bundle_format_version"]=4 if entry["bundle_state"]=="native" else None
        index_payload=dict(source_schema_version=30,project_id=payload["project_id"],projection_generation=8,bundle_count=1,legacy_count=1,entries=entries)
        index=dict(format_version=3,payload=index_payload,index_digest=reader._domain_digest(reader.INDEX_V3_DOMAIN,index_payload))
        self.assertEqual(reader._validate_index_envelope(index),index)
        native=next(row for row in entries if row["bundle_state"]=="native")
        self.assertEqual(reader._validate_bundle_payload(payload,entry=native,project_id=payload["project_id"],bundle_format_version=4,maximum_source_schema_version=30),payload)
        old_index=copy.deepcopy(index);old_index["format_version"]=2;old_index["payload"]["source_schema_version"]=29
        old_index["index_digest"]=reader._domain_digest(reader.INDEX_V2_DOMAIN,old_index["payload"])
        with self.assertRaises(reader.EvidenceConsumerError):reader._validate_index_envelope(old_index)
        with self.assertRaises(reader.EvidenceConsumerError):reader._validate_bundle_payload(payload,entry=native,project_id=payload["project_id"],bundle_format_version=3)

    def test_source31_keeps_bundle_four_index_three_and_closed_version_dispatch(self):
        payload = _source21_not_required_payload()
        payload.update(source_schema_version=31, bundle_version=4, review_continuation_basis=None)
        bundle = dict(format_version=4, payload=payload,
            bundle_digest=reader._domain_digest(reader.BUNDLE_V4_DOMAIN, payload))
        entries = index_entries(reference_json_bytes(bundle) + b"\n", bundle["bundle_digest"])
        for entry in entries:
            entry["bundle_format_version"] = 4 if entry["bundle_state"] == "native" else None
        index_payload = dict(source_schema_version=31, project_id=payload["project_id"],
            projection_generation=8, bundle_count=1, legacy_count=1, entries=entries)
        index = dict(format_version=3, payload=index_payload,
            index_digest=reader._domain_digest(reader.INDEX_V3_DOMAIN, index_payload))
        self.assertEqual(reader._validate_index_envelope(index), index)
        native = next(entry for entry in entries if entry["bundle_state"] == "native")
        self.assertEqual(reader._validate_bundle_payload(payload, entry=native,
            project_id=payload["project_id"], bundle_format_version=4,
            maximum_source_schema_version=31), payload)
        for format_version, maximum in ((3, 31), (4, 30)):
            with self.subTest(format=format_version, maximum=maximum), self.assertRaises(reader.EvidenceConsumerError):
                reader._validate_bundle_payload(payload, entry=native, project_id=payload["project_id"],
                    bundle_format_version=format_version, maximum_source_schema_version=maximum)
        for source, version in ((31, 2), (34, 3)):
            changed = copy.deepcopy(index)
            changed["format_version"] = version
            changed["payload"]["source_schema_version"] = source
            domain = reader.INDEX_V2_DOMAIN if version == 2 else reader.INDEX_V3_DOMAIN
            changed["index_digest"] = reader._domain_digest(domain, changed["payload"])
            with self.subTest(source=source, version=version), self.assertRaises(reader.EvidenceConsumerError):
                reader._validate_index_envelope(changed)


class CollectedGraphReaderTests(unittest.TestCase):
    def setUp(self):
        with review_fixture(schema_version=29, contract=None) as fixture:
            artifact,_=fixture.native_continuation_artifact()
        self.payload=copy.deepcopy(artifact.payload)
        self.payload.update(bundle_version=4,source_schema_version=30)
        graph=self.payload["review_continuation_basis"]
        graph.update(version=2,collected_captures=[])
        consumer=next(row for row in graph["continuations"] if row["state"]=="registered")
        chosen={row["session_id"] for row in graph["reviewers"] if row["continuation_id"]==consumer["continuation_id"]}
        session=next(row for row in graph["session_bindings"] if row["session_id"] not in chosen)
        receipt=next(row for row in graph["receipts"] if row["review_receipt_id"]==session["review_receipt_id"])
        metadata,answer=metadata_fixture()
        metadata["basis"].update(execution_basis=json.loads(consumer["basis_json"]),preparation_id=consumer["preparation_id"],route=consumer["route"])
        metadata["binding"].update(basis_digest=values.basis_digest(metadata["basis"]),slot=2,reviewer_key=receipt["reviewer_key"],
            parent_session_id=metadata["basis"]["execution_basis"]["parent_session_id"],reviewer_session_id=session["session_id"])
        metadata["source_identity"]["reviewer_session_id"]=session["session_id"]
        metadata["original"]["digest"]="sha256:"+session["original_result_digest"]
        declared=dict(verdict=receipt["verdict"],summary=receipt["summary"],findings=[],checks=[],limitations=[])
        ctx=dict(project_id=self.payload["project_id"],task_id=self.payload["task"]["task_id"],review_receipt_id=receipt["review_receipt_id"],
            receipt_kind="independent",target={**self.payload["target"],"base_revision":self.payload["target"]["base_revision"] or ""})
        provenance=producer.build_provenance_v2(metadata=metadata,answer=declared,**ctx)
        receipt["review_provenance"]=provenance
        for selected in self.payload["review_receipts"]:
            if selected["review_receipt_id"]==receipt["review_receipt_id"]:selected["review_provenance"]=provenance
        identity=dict(kind="collected",consumer_continuation_id=consumer["continuation_id"],consumer_slot=2,
            source_preparation_id=metadata["basis"]["preparation_id"],source_continuation_id=None,source_slot=2,
            session_id=session["session_id"],source_basis_digest=metadata["binding"]["basis_digest"],original_digest=metadata["original"]["digest"])
        public=values.source_metadata_projection(metadata)
        graph["collected_captures"]=[dict(capture_identity=identity,source_metadata=public,evidence_digest=values.capture_evidence_digest(identity,public),result_receipt_id=receipt["review_receipt_id"])]
        for ref in graph["references"]:
            if ref["source_kind"]=="review_receipt" and ref["source_id"]==receipt["review_receipt_id"]:
                ref["producer_class"]="taskgov_answer_collector"
                body={key:ref[key] for key in reader._REFERENCE_KEYS if key not in ("evidence_reference_id","digest")}
                body.update(project_id=self.payload["project_id"],task_id=self.payload["task"]["task_id"],target_base_revision=ref["target_base_revision"] or "",
                    source_projection={key:receipt[key] for key in reader._REVIEW_KEYS})
                ref["digest"]=reader._domain_digest(reader.EVIDENCE_REFERENCE_DOMAIN,body)

    def test_collected_capture_graph_accepts_declared_consistent_links_without_product_validator(self):
        from task_governance_tool import review_continuation_evidence as product_graph
        product_graph.validate_graph(self.payload["review_continuation_basis"],bundle=self.payload)
        with patch.object(values,"validate_capture_evidence",side_effect=AssertionError("product validator called")):
            self.assertEqual(validate_graph(self.payload,reader),self.payload["review_continuation_basis"])

    def test_initial_capture_inventory_is_not_inferred_from_public_provenance(self):
        from task_governance_tool import review_continuation_evidence as product_graph
        changed=copy.deepcopy(self.payload)
        graph=changed["review_continuation_basis"]
        graph["collected_captures"]=[]
        # The same public initial Receipt may have been registered before the
        # continuation. Only native capture inventory can establish its history;
        # the Reader must not invent a required capture from null source context.
        self.assertEqual(product_graph.validate_graph(graph,bundle=changed),graph)
        with patch.object(product_graph,"validate_graph",side_effect=AssertionError("product validator called")), \
             patch.object(values,"validate_capture_evidence",side_effect=AssertionError("product validator called")):
            self.assertEqual(validate_graph(changed,reader),graph)

    def test_recomputed_capture_seals_do_not_excuse_cross_graph_corruption(self):
        mutations=[lambda row:row["capture_identity"].update(source_slot=1),
            lambda row:row["capture_identity"].update(consumer_continuation_id="tg_review_continuation_ffffffffffffffff"),
            lambda row:row["source_metadata"]["basis"].update(request_digest="sha256:"+"f"*64),
            lambda row:row["source_metadata"]["original"].update(path="private/path"),
            lambda row:row.update(result_receipt_id=None)]
        for mutate in mutations:
            changed=copy.deepcopy(self.payload);row=changed["review_continuation_basis"]["collected_captures"][0];mutate(row)
            row["evidence_digest"]=reader._domain_digest(b"taskgov-collected-review-capture-evidence-v1\0",{key:row[key] for key in ("capture_identity","source_metadata")})
            with self.subTest(mutate=mutate),self.assertRaises(reader.EvidenceConsumerError):validate_graph(changed,reader)

    def test_resealed_capture_reviewer_key_matches_linked_receipt_for_both_source_kinds(self):
        from task_governance_tool import review_continuation_evidence as product_graph
        from task_governance_tool.evidence_ledger import EvidenceLedgerError
        with review_fixture(schema_version=30, contract=None) as fixture:
            continued=fixture.collected_source_artifact().payload
        for source_kind,payload in (("initial",self.payload),("continuation",continued)):
            with self.subTest(source_kind=source_kind):
                graph=payload["review_continuation_basis"]
                self.assertEqual(product_graph.validate_graph(graph,bundle=payload),graph)
                with patch.object(values,"validate_capture_evidence",side_effect=AssertionError("product validator called")):
                    self.assertEqual(validate_graph(payload,reader),graph)
                changed=copy.deepcopy(payload)
                changed_graph=changed["review_continuation_basis"]
                for row in changed_graph["collected_captures"]:
                    row["source_metadata"]["binding"]["reviewer_key"]="different-reviewer"
                    row["evidence_digest"]=reader._domain_digest(b"taskgov-collected-review-capture-evidence-v1\0",
                        {key:row[key] for key in ("capture_identity","source_metadata")})
                    self.assertEqual(values.validate_capture_evidence(row),row)
                with self.assertRaises(EvidenceLedgerError):
                    product_graph.validate_graph(changed_graph)
                with self.assertRaises(EvidenceLedgerError):
                    product_graph.validate_graph(changed_graph,bundle=changed)
                with patch.object(values,"validate_capture_evidence",side_effect=AssertionError("product validator called")),self.assertRaises(reader.EvidenceConsumerError):
                    validate_graph(changed,reader)


class CollectedSourceGraphReaderTests(unittest.TestCase):
    def test_resealed_source_context_requires_product_and_independent_reader_agreement(self):
        from task_governance_tool import review_continuation_evidence as product_graph
        from task_governance_tool.evidence_ledger import EvidenceLedgerError
        with review_fixture(schema_version=30, contract=None) as fixture:
            artifact=fixture.collected_source_artifact()
        payload=artifact.payload
        graph=payload["review_continuation_basis"]
        self.assertEqual(len(graph["collected_captures"]),2)
        self.assertEqual(sum(row["result_receipt_id"] is None for row in graph["collected_captures"]),1)
        self.assertTrue(all(row["capture_identity"]["source_continuation_id"] is not None for row in graph["collected_captures"]))
        self.assertEqual(product_graph.validate_graph(graph,bundle=payload),graph)
        with patch.object(values,"validate_capture_evidence",side_effect=AssertionError("product validator called")):
            self.assertEqual(validate_graph(payload,reader),graph)
        for field,value in (("sequence",2),("selection_digest","f"*64)):
            changed=copy.deepcopy(payload)
            changed_graph=changed["review_continuation_basis"]
            receipt_basis={}
            for row in changed_graph["collected_captures"]:
                metadata=row["source_metadata"]
                metadata["basis"]["continuation"][field]=value
                digest=reader._domain_digest(b"taskgov-review-answer-basis-v1\0",metadata["basis"])
                metadata["binding"]["basis_digest"]=digest
                row["capture_identity"]["source_basis_digest"]=digest
                row["evidence_digest"]=reader._domain_digest(b"taskgov-collected-review-capture-evidence-v1\0",
                    {key:row[key] for key in ("capture_identity","source_metadata")})
                self.assertEqual(values.validate_capture_evidence(row),row)
                if row["result_receipt_id"] is not None:
                    receipt_basis[row["result_receipt_id"]]=digest
            for receipt in changed_graph["receipts"]:
                rid=receipt["review_receipt_id"]
                if rid not in receipt_basis:
                    continue
                provenance=receipt["review_provenance"]
                provenance["host_observation"]["basis_digest"]=receipt_basis[rid]
                reseal(provenance,dict(project_id=changed["project_id"],task_id=changed["task"]["task_id"],
                    review_receipt_id=rid,receipt_kind=receipt["receipt_kind"],
                    target={**changed["target"],"base_revision":changed["target"]["base_revision"] or ""}))
                for selected in changed["review_receipts"]:
                    if selected["review_receipt_id"]==rid:
                        selected["review_provenance"]=copy.deepcopy(provenance)
                for ref in (*changed_graph["references"],*changed["evidence_references"]):
                    if ref["source_kind"]=="review_receipt" and ref["source_id"]==rid:
                        body={key:ref[key] for key in reader._REFERENCE_KEYS if key not in ("evidence_reference_id","digest")}
                        body.update(project_id=changed["project_id"],task_id=changed["task"]["task_id"],
                            target_base_revision=ref["target_base_revision"] or "",
                            source_projection={key:receipt[key] for key in reader._REVIEW_KEYS})
                        ref["digest"]=reader._domain_digest(reader.EVIDENCE_REFERENCE_DOMAIN,body)
            with self.subTest(field=field):
                with self.assertRaises(EvidenceLedgerError):
                    product_graph.validate_graph(changed_graph,bundle=changed)
                with patch.object(values,"validate_capture_evidence",side_effect=AssertionError("product validator called")),self.assertRaises(reader.EvidenceConsumerError):
                    validate_graph(changed,reader)

if __name__=="__main__":unittest.main()
