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
from task_governance_tool.session_identity import CallerIdentity
from tests.test_review_continuation import ident


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


class ReviewContinuationTransportTests(unittest.TestCase):
    def setUp(self):
        self.document = document()
        self.raw = b"\n " + encode(self.document) + b"\n"
        self.packet = packet_for(self.document)
        self.identity = transport.ContinuationIdentity(ident("tg_review_preparation_"), ident("tg_review_continuation_"), 1)
        self.packet["review_session_context"] = {
            "version": 2, "project_id": "test-project", "execution_id": EXECUTION,
            **self.identity.__dict__, "selected_reviewers": [dict(session_id=REVIEWER.session_id,
                action="reassess", prior_receipt_id=ident("tg_review_receipt_"))],
        }
        self.metadata = transport.metadata_for(self.packet, self.raw, REVIEWER)

    def frame(self, metadata=None, **arguments):
        return transport.frame_submission([self.raw], [metadata or self.metadata], continuation=self.identity, **arguments)

    def test_round_trip_keeps_original_bytes_and_actual_context(self):
        framed = self.frame(retained_receipt_ids=(ident("tg_review_receipt_", 2),))
        self.assertEqual(base64.b64decode(json.loads(framed)["items"][0]["original_base64"]), self.raw)
        decoded = transport.decode_submission(framed)
        self.assertEqual(decoded.continuation, self.identity)
        self.assertEqual(decoded.bindings[0].continuation.prior_receipt_id, ident("tg_review_receipt_"))
        self.assertEqual(decoded.retained_receipt_ids, (ident("tg_review_receipt_", 2),))
        self.assertEqual(decoded.document, self.document)

    def test_only_the_selected_actual_caller_can_save(self):
        for caller in (SECOND, CallerIdentity(None)):
            with self.subTest(caller=caller), self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                transport.metadata_for(self.packet, self.raw, caller)

    def test_sidecar_matches_exact_prepared_packet_without_reconstructing_a_caller(self):
        matched = transport.validate_packet_metadata(self.packet, self.metadata, self.raw)
        self.assertEqual(matched.binding.session_id, REVIEWER.session_id)
        for key, value in (("continuation_id", ident("tg_review_continuation_", 2)),
                           ("preparation_id", ident("tg_review_preparation_", 2)),
                           ("execution_id", "tg_execution_" + "b" * 16)):
            changed = copy.deepcopy(self.packet)
            changed["review_session_context"][key] = value
            with self.subTest(key=key), self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                transport.validate_packet_metadata(changed, self.metadata, self.raw)
        changed = copy.deepcopy(self.packet)
        changed["review_session_context"]["selected_reviewers"][0]["session_id"] = SECOND.session_id
        with self.assertRaises((ReviewEvidenceError, TaskValidationError)):
            transport.validate_packet_metadata(changed, self.metadata, self.raw)

    def test_invalid_typed_continuation_cannot_bypass_closed_input(self):
        for identity, action, prior in ((None, "supplement", None), (self.identity, "unknown", None),
            (self.identity, "supplement", ident("tg_review_receipt_")), (self.identity, "reassess", None)):
            with self.subTest(action=action), self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                transport.ContinuedResult(identity, action, prior)

    def test_machine_metadata_cannot_downgrade_to_ordinary_envelope(self):
        with self.assertRaises((ReviewEvidenceError, TaskValidationError)):
            transport.frame_submission([self.raw], [self.metadata])
        good = json.loads(self.frame())
        ordinary = {"format": transport.FORMAT, "items": good["items"]}
        with self.assertRaises((ReviewEvidenceError, TaskValidationError)):
            transport.decode_submission(encode(ordinary))

    def test_missing_fields_and_mixed_rounds_rejected(self):
        for field in self.metadata:
            changed = dict(self.metadata)
            del changed[field]
            with self.subTest(field=field), self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                self.frame(metadata=changed)
        for changes in (dict(continuation_id=ident("tg_review_continuation_", 2)),
                        dict(continuation_sequence=2), dict(continuation_sequence=True),
                        dict(action="supplement"), dict(prior_receipt_id=None)):
            with self.subTest(changes=changes), self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                self.frame(metadata={**self.metadata, **changes})

    def test_retained_original_v1_is_carried_without_rewriting(self):
        raw = encode(document(receipts=[receipt("second")]))
        original_packet = copy.deepcopy(self.packet)
        original_packet["review_session_context"] = dict(version=1, project_id="test-project", execution_id=EXECUTION)
        original_metadata = transport.metadata_for(original_packet, raw, SECOND)
        framed = transport.frame_submission([self.raw, raw], [self.metadata, original_metadata], continuation=self.identity)
        decoded = transport.decode_submission(framed)
        self.assertIsNone(decoded.bindings[1].continuation)
        self.assertEqual(base64.b64decode(json.loads(framed)["items"][1]["original_base64"]), raw)

    def test_context_closed_shape_missing_execution_and_duplicate_sessions_rejected(self):
        context = self.packet["review_session_context"]
        for changed in ({"version": 2}, {**context, "execution_id": None},
                        {**context, "selected_reviewers": context["selected_reviewers"] * 2},
                        {**context, "extra": "caller-choice"}):
            with self.subTest(changed=changed), self.assertRaises((ReviewEvidenceError, TaskValidationError)):
                transport.validate_context(changed, allow_unknown_execution=True)


if __name__ == "__main__":
    unittest.main()
