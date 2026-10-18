from __future__ import annotations
import hashlib
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from tests.test_review_answer_pure_values import metadata_fixture, ident, A
from task_governance_tool import review_continuation as c
from task_governance_tool import review_answer_continuation_preparation as p
from task_governance_tool.review_wait_runtime.request_continuation import RequestRound, encode, decode
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from task_governance_tool.review_answer_values import basis_digest

def fixture():
    metadata,_=metadata_fixture()
    root=SimpleNamespace(preparation_id=metadata["basis"]["preparation_id"],basis=metadata["basis"]["execution_basis"],route="integrated")
    record=SimpleNamespace(continuation_id=ident("tg_review_continuation_"),sequence=1,basis=root.basis,
        selected=(c.SelectedReviewer(A,"supplement",None,"Review the updated source"),))
    round=RequestRound(root.preparation_id,record.continuation_id,1,c.basis_digest(root.basis),"b"*64,c.selection_digest(record.selected),0,None,None,None,
        packet_path=".tmp-review/round/packet.json",result_paths=(".tmp-review/round/review-1.json",))
    return root,record,round

class ContinuedAnswerPreparationTests(unittest.TestCase):
    def test_initial_handoff_admission_compares_complete_basis_at_exact_slot(self):
        from copy import deepcopy
        from task_governance_tool import review_answer_handoff as handoff
        from task_governance_tool.review_answer import AnswerError
        root,_,_=fixture()
        raw=b"issued packet"
        first=metadata_fixture()[0]["basis"]
        first["packet_digest"]="sha256:"+hashlib.sha256(raw).hexdigest()
        second=deepcopy(first);second["request_digest"]="sha256:"+"f"*64
        root.collection_bases=(first,second)
        root.packet_path=".tmp-review/packet.json";root.packet_digest=hashlib.sha256(raw).hexdigest()
        root.result_paths=(".tmp-review/one.json",".tmp-review/two.json")
        resolution=SimpleNamespace(paths=SimpleNamespace(review_answer_collection_store=lambda *_:Path.cwd()/"unused.sqlite"))
        with patch.object(handoff.files,"checked_path",return_value=Path("packet.json")),\
             patch.object(handoff.files,"read_bounded",return_value=raw):
            service=handoff.service_for(Path.cwd(),resolution,root,first,current=False)
            service.admit_basis(first,1);service.admit_basis(second,2)
            for value,slot in ((first,2),(second,1),({**first,"route":"manual"},1)):
                with self.subTest(slot=slot,value=value),self.assertRaises(AnswerError) as caught:
                    service.admit_basis(value,slot)
                self.assertEqual(caught.exception.failure["class"],"binding_mismatch")

    def test_old_round_bytes_remain_exact_and_new_tag_is_closed(self):
        root,record,round=fixture()
        old=encode(round)
        self.assertNotIn("answer_protocol",json.loads(old))
        self.assertEqual(decode(old),round)
        tagged=replace(round,answer_protocol="review-answer-v1")
        self.assertEqual(json.loads(encode(tagged))["answer_protocol"],"review-answer-v1")
        self.assertEqual(decode(encode(tagged)),tagged)
        explicit=json.loads(old);explicit["answer_protocol"]="legacy"
        with self.assertRaises(RepositoryError):decode(json.dumps(explicit,sort_keys=True,separators=(",",":")))
        with self.assertRaises(RepositoryError):replace(round,answer_protocol="other")

    def test_basis_digest_binds_exact_continuation_request_after_suffix(self):
        root,record,round=fixture();round=replace(round,answer_protocol="review-answer-v1")
        def generated(*args,**kwargs):
            self.assertTrue(kwargs["collected"])
            return dict(review_requests=[dict(request="Fixed read and five-field instructions",result_path=round.result_paths[0])])
        with patch.object(p.preparation,"_requests",side_effect=generated):
            request=p.requests_for(None,root,record,round)["review_requests"][0]["request"]
            bases=p.expected_bases(None,root,record,round,b"packet bytes")
        self.assertIn("Explicit continuation: supplement. Reason: Review the updated source",request)
        self.assertEqual(bases[0]["request_digest"],"sha256:"+hashlib.sha256(request.encode()).hexdigest())
        self.assertEqual(bases[0]["continuation"],dict(continuation_id=record.continuation_id,sequence=1,selection_digest=round.selection_digest))

    def test_prepared_round_uses_saved_basis_without_regenerating_request(self):
        root,record,round=fixture();round=replace(round,answer_protocol="review-answer-v1",phase="prepared",packet_digest="c"*64)
        scope=SimpleNamespace(repo=None,root=root,paths=None,target=None)
        with patch.object(p,"expected_bases",side_effect=AssertionError("regenerated issued request")),patch.object(p,"locate_from_preparation",return_value=(None,root,("saved",))) as locate,patch.object(p.files,"decode_packet",return_value={}):
            self.assertEqual(p.initialize(scope,record,round,b"packet"),("saved",))
        self.assertTrue(locate.call_args.kwargs["current"])

    def test_settled_pinned_round_recovers_saved_basis_without_regenerating_request(self):
        from task_governance_tool import review_continuation_handoff_repository as core
        root,record,round=fixture();raw=b"packet"
        basis=metadata_fixture()[0]["basis"]
        basis.update(continuation=dict(continuation_id=record.continuation_id,sequence=1,
            selection_digest=round.selection_digest),packet_digest="sha256:"+hashlib.sha256(raw).hexdigest())
        round=replace(round,answer_protocol="review-answer-v1",phase="settled",packet_digest=hashlib.sha256(raw).hexdigest(),
            collection_basis_digests=(basis_digest(basis),))
        record.preparation_id=root.preparation_id;record.state="preparing"
        scope=SimpleNamespace(repo=Path.cwd(),root=root,target=None,caller=None,
            paths=SimpleNamespace(review_answer_collection_store=lambda *_:Path.cwd()/"answers.sqlite"))
        def initialize(_expected,*,admit):
            self.assertEqual(_expected,(basis,));admit()
        with patch.object(p,"expected_bases",side_effect=AssertionError("regenerated pinned request")),\
             patch.object(p.CollectionRepository,"read",return_value=({"basis":basis},)),\
             patch.object(p.CollectionRepository,"initialize",side_effect=initialize),\
             patch.object(core,"read_state",return_value=SimpleNamespace(basis=root.basis,records=(record,))),\
             patch.object(p.files,"checked_path",return_value=Path("packet.json")),\
             patch.object(p.files,"read_bounded",return_value=raw):
            self.assertEqual(p.initialize(scope,record,round,raw),(basis,))

    def test_historical_tagged_lookup_never_needs_live_basis_and_missing_store_is_closed(self):
        root,record,round=fixture();raw=b"packet"
        round=replace(round,answer_protocol="review-answer-v1",phase="prepared",packet_digest=hashlib.sha256(raw).hexdigest())
        identity=dict(continuation_id=record.continuation_id,sequence=1,selection_digest=round.selection_digest)
        metadata,_=metadata_fixture();basis=metadata["basis"]
        basis.update(continuation=identity,packet_digest="sha256:"+round.packet_digest)
        round=replace(round,collection_basis_digests=(basis_digest(basis),))
        packet=dict(review_session_context=dict(version=2,project_id=root.basis["project_id"],execution_id=root.basis["execution_id"],
            preparation_id=root.preparation_id,continuation_id=record.continuation_id,continuation_sequence=1))
        paths=SimpleNamespace(review_continuation_store=lambda *_:Path.cwd()/"transitions.sqlite",review_answer_collection_store=lambda *_:Path.cwd()/"answers.sqlite")
        resolution=SimpleNamespace(paths=paths,target=None)
        from task_governance_tool import review_continuation_handoff_repository as core
        with patch.object(p.TransitionRepository,"read",return_value=(round,)),patch.object(p.CollectionRepository,"read",return_value=({"basis":basis},)),\
             patch.object(core,"read_state",side_effect=AssertionError("historical read consulted live Task")):
            result=p.locate_from_preparation(None,resolution,root,round.packet_path,raw,packet)
            self.assertEqual(result[2],(basis,))
        with patch.object(p.TransitionRepository,"read",return_value=(round,)),patch.object(p.CollectionRepository,"read",side_effect=RepositoryError("state_unreadable")):
            with self.assertRaises(RepositoryError):p.locate_from_preparation(None,resolution,root,round.packet_path,raw,packet)

    def test_issued_continuation_requires_complete_slot_pins_without_reconstruction(self):
        from copy import deepcopy
        from task_governance_tool.review_handoff_transport import HandoffError
        root,record,round=fixture()
        metadata,_=metadata_fixture()
        basis=metadata["basis"]
        basis.update(continuation=dict(continuation_id=record.continuation_id,sequence=1,
            selection_digest=round.selection_digest),packet_digest="sha256:"+"c"*64)
        round=replace(round,answer_protocol="review-answer-v1",phase="prepared",packet_digest="c"*64,
            collection_basis_digests=(basis_digest(basis),))
        with patch.object(p,"expected_bases",side_effect=AssertionError("regenerated issued request")):
            self.assertEqual(p.issued_bases(root,round,({"basis":basis},)),(basis,))
            for field,value in (("request_digest","sha256:"+"f"*64),("route","manual"),
                                ("packet_digest","sha256:"+"e"*64)):
                corrupt=deepcopy(basis);corrupt[field]=value
                with self.subTest(field=field),self.assertRaises(HandoffError):
                    p.issued_bases(root,round,({"basis":corrupt},))
            with self.assertRaises(HandoffError):
                p.issued_bases(root,replace(round,collection_basis_digests=()),({"basis":basis},))

    def test_slot_order_is_part_of_issued_continuation_admission(self):
        from copy import deepcopy
        from task_governance_tool.review_handoff_transport import HandoffError
        root,record,round=fixture()
        metadata,_=metadata_fixture();first=metadata["basis"]
        first.update(continuation=dict(continuation_id=record.continuation_id,sequence=1,
            selection_digest=round.selection_digest),packet_digest="sha256:"+"c"*64)
        second=deepcopy(first);second["request_digest"]="sha256:"+"f"*64
        round=replace(round,answer_protocol="review-answer-v1",phase="prepared",packet_digest="c"*64,
            result_paths=(round.result_paths[0],".tmp-review/round/review-2.json"),
            collection_basis_digests=(basis_digest(first),basis_digest(second)))
        self.assertEqual(p.issued_bases(root,round,({"basis":first},{"basis":second})),(first,second))
        with self.assertRaises(HandoffError):p.issued_bases(root,round,({"basis":second},{"basis":first}))

    def test_runtime_current_round_cannot_redirect_collection_to_initial_store(self):
        from copy import deepcopy
        from task_governance_tool import review_answer_runtime as runtime, review_answer_handoff as handoff
        from task_governance_tool import review_handoff_scope, setup_feature_config, state_paths
        from task_governance_tool.review_wait_runtime.review_wait_controller import WaitBinding
        from task_governance_tool.review_handoff_transport import HandoffError
        root,record,round=fixture()
        initial=metadata_fixture()[0]["basis"]
        root.version=2;root.collection_bases=(initial,)
        continued=deepcopy(initial)
        continued.update(continuation=dict(continuation_id=record.continuation_id,sequence=1,
            selection_digest=round.selection_digest),packet_digest="sha256:"+"c"*64)
        round=replace(round,answer_protocol="review-answer-v1",phase="prepared",packet_digest="c"*64,
            collection_basis_digests=(basis_digest(continued),))
        binding=WaitBinding(wait_id="fixture",**{name:(root.basis["parent_session_id"] if name=="parent_thread_id" else root.basis[name])
            for name in WaitBinding.__dataclass_fields__ if name!="wait_id"})
        paths=SimpleNamespace(review_preparation_store=lambda *_:Path.cwd()/"preparation.sqlite",
            review_continuation_store=lambda *_:Path.cwd()/"transition.sqlite",
            review_answer_collection_store=lambda *_:Path.cwd()/"continued.sqlite")
        with patch.object(review_handoff_scope,"resolve",return_value=SimpleNamespace(paths=paths)),\
             patch.object(setup_feature_config,"answer_collection_allowed",return_value=True),\
             patch.object(state_paths,"path_lexically_exists",return_value=True),\
             patch.object(p.PreparationRepository,"read",return_value=((root,),root)),\
             patch.object(p.TransitionRepository,"read",return_value=(round,)),\
             patch.object(p.CollectionRepository,"read",return_value=({"basis":initial},)),\
             patch.object(handoff,"service_for",side_effect=AssertionError("redirected to initial store")):
            with self.assertRaises(HandoffError):runtime.service_for_wait(None,None,binding)


class ContinuedAnswerPublicationTests(unittest.TestCase):
    def test_new_round_under_legacy_root_freezes_collection_before_publication(self):
        from tests.review_handoff_fixture_support import handoff_fixture
        from task_governance_tool import setup_feature_config
        fixture=self.enterContext(handoff_fixture(schema_version=30, contract=None))
        fixture.ready()
        self.assertEqual(p.PreparationRepository(fixture.root/"preparation.sqlite").read()[1].version,1)
        fixture.resolution.paths.review_answer_collection_store=lambda *_:fixture.root/"answers.sqlite"
        with patch.object(setup_feature_config,"answer_collection_allowed",return_value=True):
            result=fixture.call()
        self.assertTrue(result["ok"],result)
        requests=result["handoff"]["review_requests"]
        self.assertEqual(len(requests),1)
        self.assertIn("exactly verdict, summary, findings, checks, limitations",requests[0]["request"])
        self.assertNotIn("Save command",requests[0]["request"])
        from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository
        transition=TransitionRepository(fixture.root/"transition.sqlite").read()[-1]
        self.assertEqual(transition.answer_protocol,"review-answer-v1")
        records=p.CollectionRepository(fixture.root/"answers.sqlite").read()
        self.assertEqual(records[0]["basis"]["request_digest"],"sha256:"+hashlib.sha256(requests[0]["request"].encode()).hexdigest())
        self.assertEqual(records[0]["basis"]["continuation"]["continuation_id"],transition.continuation_id)

if __name__=="__main__":unittest.main()
