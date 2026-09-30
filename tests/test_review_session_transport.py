"""Closed machine binding; original judgments and legacy input stay separate."""

import base64
import copy
import json
import unittest

from tests.test_review_results import document, encode, receipt
from tests.test_review_handoff import packet_for
from tests.test_review_session_repository import REVIEWER, SECOND, EXECUTION
from task_governance_tool import review_session_transport as transport
from task_governance_tool.reviews import ReviewEvidenceError
from task_governance_tool.task_values import TaskValidationError


class ReviewSessionTransportTests(unittest.TestCase):
    def setUp(self):
        self.document = document()
        self.raw = b"\n " + encode(self.document) + b"\n"
        self.packet = packet_for(self.document)
        self.packet["review_session_context"] = {
            "version": 1, "project_id": "test-project", "execution_id": EXECUTION,
        }
        self.metadata = transport.metadata_for(self.packet, self.raw, REVIEWER)

    def test_round_trip_preserves_exact_bytes_and_reviewer_not_parent(self):
        other = encode(document(receipts=[receipt("second")]))
        second = transport.metadata_for(self.packet, other, SECOND)
        framed = transport.frame_submission([self.raw, other], [self.metadata, second])
        envelope = json.loads(framed)
        self.assertEqual([base64.b64decode(item["original_base64"]) for item in envelope["items"]], [self.raw, other])
        decoded = transport.decode_submission(framed)
        self.assertEqual([item.binding.session_id for item in decoded.bindings], [REVIEWER.session_id, SECOND.session_id])
        self.assertEqual(decoded.document["receipts"], [*self.document["receipts"], *document(receipts=[receipt("second")])["receipts"]])

    def test_legacy_shapes_are_unbound_without_changing_judgments(self):
        for raw in (self.raw, b"[" + self.raw + b"]"):
            decoded = transport.decode_submission(raw)
            self.assertIsNone(decoded.bindings)
            self.assertEqual(decoded.document, self.document)

    def test_every_missing_binding_field_is_rejected(self):
        for field in self.metadata:
            with self.subTest(field=field):
                changed = dict(self.metadata)
                del changed[field]
                with self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                    transport.frame_submission([self.raw], [changed])

    def test_mismatched_basis_and_reformatted_original_are_not_repaired(self):
        changed_basis = copy.deepcopy(self.metadata)
        changed_basis["review_target"]["generation"] += 1
        for raw, binding in ((self.raw + b" ", self.metadata), (self.raw, changed_basis)):
            with self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                transport.frame_submission([raw], [binding])

    def test_invalid_envelope_never_downgrades_to_legacy(self):
        good = json.loads(transport.frame_submission([self.raw], [self.metadata]))
        cases = [{**good, "format": "future"}, {**good, "items": []}, {**good, "extra": None}]
        item = good["items"][0]
        cases.append({**good, "items": [{**item, "original_base64": "e30=\n"}]})
        cases.append({**good, "items": [{"original_base64": item["original_base64"]}]})
        for value in cases:
            with self.subTest(value=list(value)):
                with self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                    transport.decode_submission(encode(value))

    def test_batch_binding_count_and_whole_transport_limit_are_enforced(self):
        with self.assertRaises(ReviewEvidenceError):
            transport.frame_submission([self.raw], [])
        with self.assertRaises(ReviewEvidenceError):
            transport.decode_submission(b" " * 262145)


if __name__ == "__main__":
    unittest.main()
