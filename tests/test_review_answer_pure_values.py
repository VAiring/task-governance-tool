from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"))
from task_governance_tool import review_answer_values as v
from task_governance_tool.review_answer import AnswerError, decode_answer
from tests.test_review_continuation import basis as execution_basis, OWNER, A, ident

TURN1="10000000-0000-4000-8000-000000000001"
TURN2="10000000-0000-4000-8000-000000000002"
INTENT=ident("tg_review_correction_")

def source(operation):
    return dict(adapter="codex-app-tools-review-answer", version=1, operation=operation)

def metadata_fixture(attempt=0):
    basis = dict(version=1, preparation_id=ident("tg_review_preparation_"), packet_digest="sha256:"+"a"*64,
                 request_digest="sha256:"+"b"*64, protocol="review-answer-v1", attestation="assigned-independent-review-v1",
                 route="integrated", continuation=None, execution_basis=execution_basis())
    binding = dict(basis_digest=v.basis_digest(basis), slot=1, reviewer_key="reviewer", dispatch_id="dispatch-1",
                   parent_session_id=OWNER, reviewer_session_id=A, initial_turn_id=TURN1, binding_source=source("dispatch_binding"),
                   assignment=dict(kind="independent", review_profiles=["general"], review_lenses=["correctness"],
                                   requested_model=None, requested_effort=None, requested_skill=None, context_relation=None))
    answer = decode_answer(json.dumps(dict(verdict="pass", summary="  okay  ", findings=[], checks=[], limitations=[])).encode())
    original = dict(path=".tmp-review/original.json", byte_length=len(answer.raw), digest=answer.original_digest,
                    payload_start=answer.payload_start, payload_length=answer.payload_length, payload_digest=answer.payload_digest)
    metadata = dict(basis=basis, binding=binding, attempt=attempt, source_identity=dict(reviewer_session_id=A, turn_id=TURN1 if attempt==0 else TURN2, message_id="message-1"),
                    original=original, answer_source=source("answer_observation"), terminal="completed", terminal_source=source("terminal_observation"),
                    correction_binding=None if attempt==0 else dict(intent_id=INTENT, initial_turn_id=TURN1, correction_turn_id=TURN2, source=source("correction_binding")))
    return metadata, answer

def record_fixture():
    metadata, _ = metadata_fixture()
    return dict(version=1, basis=metadata["basis"], binding=None, attempts=[dict(ordinal=0, turn_id=None, source_identity=None,
        observation="missing", observed_digest=None, terminal="unknown", terminal_source=None, publication=None, failure=None)],
        correction=None, registration=dict(state="not_started", submission_digest=None, receipt_id=None))

class ValueTests(unittest.TestCase):
    def test_metadata_raw_ranges_and_detachment(self):
        metadata, answer = metadata_fixture()
        result = v.validate_metadata(metadata, answer.raw)
        self.assertEqual(result, metadata)
        result["binding"]["slot"] = 2
        self.assertEqual(metadata["binding"]["slot"], 1)
        metadata["original"]["payload_start"] += 1
        with self.assertRaises(AnswerError):
            v.validate_metadata(metadata, answer.raw)

    def test_closed_nested_types_and_identity(self):
        for path, replacement in ((["binding", "slot"], True), (["basis", "version"], True),
            (["binding", "assignment", "review_lenses"], ["privacy", "correctness"]),
            (["source_identity", "turn_id"], "different"), (["original", "path"], "../escape"),
            (["answer_source", "operation"], "dispatch_binding")):
            metadata, _ = metadata_fixture()
            parent=metadata
            for key in path[:-1]: parent=parent[key]
            parent[path[-1]]=replacement
            with self.subTest(path=path), self.assertRaises(AnswerError): v.validate_metadata(metadata)
        metadata, _ = metadata_fixture(); metadata["binding"]["extra"] = "x"
        with self.assertRaises(AnswerError): v.validate_metadata(metadata)

    def test_initial_record_and_corrected_attempt_digest_are_distinct(self):
        record = record_fixture()
        self.assertEqual(v.validate_record(record), record)
        m, _ = metadata_fixture(1)
        record["binding"] = m["binding"]
        initial = record["attempts"][0]
        initial.update(turn_id=TURN1, source_identity=dict(reviewer_session_id=A,turn_id=TURN1,message_id="message-0"),
                       observation="complete", observed_digest="sha256:"+"c"*64)
        record["correction"] = dict(intent_id=INTENT, initial_source_identity=initial["source_identity"].copy(), initial_digest=initial["observed_digest"],
            effect="accepted", correction_turn_id=TURN2, binding_source=source("correction_binding"), failure=None)
        record["attempts"].append(dict(ordinal=1,turn_id=TURN2,source_identity=m["source_identity"],observation="complete",observed_digest=m["original"]["digest"],
            terminal="completed",terminal_source=m["terminal_source"],publication=dict(state="saved",original=m["original"]),failure=None))
        self.assertEqual(v.validate_record(record), record)
        record["attempts"][1]["publication"]["original"]["digest"] = initial["observed_digest"]
        with self.assertRaises(AnswerError): v.validate_record(record)

    def test_capture_and_native_metadata_seal(self):
        metadata, _=metadata_fixture()
        capture=dict(kind="collected",consumer_continuation_id=ident("tg_review_continuation_"),consumer_slot=1,
            source_preparation_id=metadata["basis"]["preparation_id"],source_continuation_id=None,source_slot=1,session_id=A,
            source_basis_digest=v.basis_digest(metadata["basis"]),original_digest=metadata["original"]["digest"],metadata_digest=v.metadata_digest(metadata))
        self.assertEqual(v.validate_collected_capture(capture), capture)
        other=copy.deepcopy(metadata); other["original"]["path"]=".tmp-review/other.json"
        self.assertNotEqual(v.metadata_digest(metadata),v.metadata_digest(other))

    def test_public_capture_projection_and_recomputed_crossbinding_tamper(self):
        metadata, _=metadata_fixture(1)
        identity=dict(kind="collected",consumer_continuation_id=ident("tg_review_continuation_"),consumer_slot=1,
            source_preparation_id=metadata["basis"]["preparation_id"],source_continuation_id=None,source_slot=1,session_id=A,
            source_basis_digest=v.basis_digest(metadata["basis"]),original_digest=metadata["original"]["digest"])
        public=v.source_metadata_projection(metadata)
        row=dict(capture_identity=identity,source_metadata=public,evidence_digest=v.capture_evidence_digest(identity,public),result_receipt_id=None)
        self.assertEqual(v.validate_capture_evidence(row),row)
        self.assertNotIn("path",public["original"])
        row["capture_identity"]["source_slot"]=2
        row["evidence_digest"]=v.capture_evidence_digest(row["capture_identity"],public)
        with self.assertRaises(AnswerError):v.validate_capture_evidence(row)
        row["capture_identity"]["source_slot"]=1
        row["source_metadata"]["original"]["path"]=".tmp-review/leak"
        with self.assertRaises(AnswerError):v.validate_capture_evidence(row)

if __name__ == "__main__": unittest.main()
