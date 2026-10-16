from __future__ import annotations
import copy
import unittest
from tests.test_review_answer_pure_values import metadata_fixture, v, ident, TURN2
from task_governance_tool import review_answer_provenance as p
from task_governance_tool.review_answer import AnswerError

def context(metadata):
    e=metadata["basis"]["execution_basis"]
    return dict(project_id=e["project_id"],task_id=e["task_id"],review_receipt_id=ident("tg_review_receipt_"),receipt_kind="independent",
        target=dict(kind=e["target_kind"],value=e["target_value"],base_revision=e["target_base_revision"],generation=e["target_generation"],capture_version=1))

class ProvenanceTests(unittest.TestCase):
    def test_raw_payload_path_free_and_optional_unknown(self):
        m,a=metadata_fixture(); ctx=context(m)
        result=p.build_provenance_v2(metadata=m,answer=a,**ctx)
        self.assertEqual(result["answer_attestation"]["payload"],a.payload)
        self.assertNotIn("path",result["answer_attestation"]["original"])
        self.assertEqual(result["host_observation"]["model"],dict(state="unknown",value=None,source=None))
        self.assertEqual(p.validate_provenance_v2(result,**ctx),result)
        old=result["digest"]; result["review_provenance_id"]=ident("tg_review_provenance_",2)
        self.assertEqual(p.provenance_digest(result,**ctx),old)
        changed=copy.deepcopy(result); changed["answer_attestation"]["payload"]["summary"]="okay"
        self.assertNotEqual(p.provenance_digest(changed,**ctx),old)

    def test_recomputed_digest_does_not_broaden_sources_or_runtime_facts(self):
        m,a=metadata_fixture();ctx=context(m)
        for field, replacement in (("model",dict(state="declared",value="gpt-6.1-sol",source=None)),
            ("binding_source",dict(adapter="private-api",version=1,operation="dispatch_binding"))):
            result=p.build_provenance_v2(metadata=m,answer=a,**ctx)
            result["host_observation"][field]=replacement
            result["digest"]=p.provenance_digest(result,**ctx)
            with self.subTest(field=field),self.assertRaises(AnswerError):p.validate_provenance_v2(result,**ctx)

    def test_corrected_answer_provenance_and_receipt_binding(self):
        m,a=metadata_fixture(1);ctx=context(m)
        result=p.build_provenance_v2(metadata=m,answer=a,**ctx)
        self.assertEqual(result["host_observation"]["turn_id"],TURN2)
        ctx["review_receipt_id"]=ident("tg_review_receipt_",2)
        with self.assertRaises(AnswerError):p.validate_provenance_v2(result,**ctx)

if __name__ == "__main__":unittest.main()
