"""Whole-batch collected byte transport, capacity and source binding cases."""
import base64
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.review_answer import decode_answer
from task_governance_tool.review_answer_values import basis_digest
from task_governance_tool.review_answer_transport import (
    CollectedItem, CollectedSubmission, decode_collected_submission, frame_collected_submission)
from task_governance_tool.review_session_transport import decode_submission
from task_governance_tool.reviews import ReviewEvidenceError
from tests.test_review_answer_pure_values import metadata_fixture


def item(slot=1, *, findings=0, fenced=False):
    metadata, original = metadata_fixture()
    payload = copy.deepcopy(original.payload)
    payload["findings"] = [dict(severity="low", summary=f"Finding {index}") for index in range(findings)]
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if fenced:
        raw = b" \r\n```json\r\n" + raw + b"\r\n```\r\n"
    answer = decode_answer(raw)
    child = f"20000000-0000-4000-8000-{slot:012d}"
    metadata["binding"].update(slot=slot, reviewer_session_id=child, reviewer_key=f"reviewer-{slot}")
    metadata["source_identity"]["reviewer_session_id"] = child
    metadata["original"].update(path=f".tmp-review/review-{slot}.json", byte_length=len(raw),
        digest=answer.original_digest, payload_start=answer.payload_start,
        payload_length=answer.payload_length, payload_digest=answer.payload_digest)
    return CollectedItem(slot, raw, metadata, answer)


def frame(*items):
    return frame_collected_submission(items[0].metadata["basis"]["preparation_id"], items)


class CollectedTransportTests(unittest.TestCase):
    def test_fenced_original_survives_exactly_and_has_own_typed_arm(self):
        source = item(fenced=True)
        raw = frame(source)
        parsed = decode_submission(raw)
        self.assertIs(type(parsed), CollectedSubmission)
        self.assertEqual(parsed.items[0].raw, source.raw)
        self.assertEqual(parsed.items[0].answer.payload["summary"], "  okay  ")
        self.assertEqual(parsed.bindings[0].binding.session_id, source.session_binding.session_id)
        self.assertFalse(hasattr(parsed, "document"))

    def test_atomic_aggregate_finding_bound_and_duplicate_sessions(self):
        self.assertEqual(len(decode_collected_submission(frame(item(1, findings=32), item(2, findings=32))).items), 2)
        with self.assertRaises(ReviewEvidenceError):
            frame(item(1, findings=33), item(2, findings=32))
        first, second = item(), item(2)
        second.metadata["binding"]["reviewer_session_id"] = first.session_binding.session_id
        second.metadata["source_identity"]["reviewer_session_id"] = first.session_binding.session_id
        with self.assertRaises(ReviewEvidenceError):
            frame(first, second)

    def test_partial_shape_or_changed_bytes_never_reaches_writer(self):
        baseline = json.loads(frame(item()))
        changes = (
            lambda x: x["items"][0].update(slot=True),
            lambda x: x["items"][0].update(original_base64=base64.b64encode(b"{}").decode()),
            lambda x: x["items"][0]["metadata"]["source_identity"].update(message_id=""),
            lambda x: x["items"][0]["metadata"].update(terminal="in_progress"),
            lambda x: x.update(extra="not retained"),
            lambda x: x.update(retained_receipt_ids=["tg_review_receipt_"+"1"*16]),
        )
        for change in changes:
            value = copy.deepcopy(baseline)
            change(value)
            with self.subTest(change=change), self.assertRaises(ReviewEvidenceError):
                decode_submission(json.dumps(value).encode())

    def test_cross_basis_recomputed_digest_is_rejected(self):
        first, second = item(), item(2)
        second.metadata["basis"]["execution_basis"]["ownership_generation"] += 1
        second.metadata["binding"]["basis_digest"] = basis_digest(second.metadata["basis"])
        with self.assertRaises(ReviewEvidenceError):
            frame(first, second)

    def test_whole_encoded_input_limit_not_individual_original_limit(self):
        raw = frame(item())
        with self.assertRaises(ReviewEvidenceError):
            decode_submission(raw + b" " * (262145-len(raw)))


if __name__ == "__main__":
    unittest.main()
