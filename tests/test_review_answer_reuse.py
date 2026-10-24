"""Operation-local answer reuse must preserve fresh external admission."""
import base64
from collections import Counter
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_review_answer_pure_values import metadata_fixture
from tests.test_review_answer_pure_provenance import context
from tests.test_review_answer_tracking_collection import tracking_fixture
from task_governance_tool import review_answer as a
from task_governance_tool import review_answer_values as v
from task_governance_tool import review_answer_provenance as p
from task_governance_tool import review_answer_transport as t
from task_governance_tool.review_answer_collection import CollectionService, source
from task_governance_tool import review_handoff_transport as files
from task_governance_tool.reviews import ReviewEvidenceError


def measured(operation):
    counts = Counter()
    def observe(frame, event, _arg):
        if event != "call":
            return
        name = frame.f_code.co_name
        if name in ("_parse_answer", "validate_payload", "validate_metadata"):
            counts[name] += 1
        if name == "privacy_scan" and type(frame.f_locals["value"]) is dict:
            if set(frame.f_locals["value"]) == a.ANSWER_KEYS:
                counts["answer_privacy"] += 1
    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        return operation(), counts
    finally:
        sys.setprofile(previous)


def sample(slot=1, version=1, fence=None):
    metadata, answer = metadata_fixture() if version == 1 else tracking_fixture()
    payload = {**answer.payload, "summary": f'日本語 "引用" {slot}'}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    raw = b" \t" + body + b"\r\n" if fence is None else (
        b" \n```json" + fence + b" \t" + body + fence + b"```" + fence)
    answer = a.decode_answer(raw)
    child = f"20000000-0000-4000-8000-{slot:012d}"
    metadata["binding"].update(slot=slot, reviewer_session_id=child, reviewer_key=f"reviewer-{slot}")
    metadata["source_identity"]["reviewer_session_id"] = child
    metadata["original"].update(path=f".tmp-review/original-{slot}.json", byte_length=len(raw),
        digest=answer.original_digest, payload_start=answer.payload_start,
        payload_length=answer.payload_length, payload_digest=answer.payload_digest)
    return t.CollectedItem(slot, raw, metadata, answer)


def record(item):
    m = item.metadata
    return dict(version=m["basis"]["version"], basis=m["basis"], binding=m["binding"],
        correction=None, attempts=[dict(observation="complete", failure=None,
            source_identity=m["source_identity"], terminal="completed", terminal_source=m["terminal_source"],
            publication=dict(state="saved", original=m["original"]))])


class AnswerReuseTests(unittest.TestCase):
    def assert_one_answer(self, counts, size=1):
        self.assertEqual(counts["_parse_answer"], size)
        self.assertEqual(counts["validate_payload"], size)
        self.assertEqual(counts["answer_privacy"], size)

    def test_transport_one_parse_and_privacy_per_raw_per_operation(self):
        for version in (1, 2):
            for fence in (None, b"\n", b"\r\n"):
                for size in (1, 8):
                    with self.subTest(version=version, fence=fence, size=size):
                        items = tuple(sample(slot, version, fence) for slot in range(1, size + 1))
                        raw, counts = measured(lambda: t.frame_collected_submission(
                            items[0].metadata["basis"]["preparation_id"], items))
                        self.assert_one_answer(counts, size)
                        decoded, counts = measured(lambda: t.decode_collected_submission(raw))
                        self.assert_one_answer(counts, size)
                        self.assertEqual(decoded.items, items)
                        self.assertEqual(json.loads(t.frame_collected_submission(decoded.preparation_id, decoded.items)), json.loads(raw))
                        decoded.items[0].metadata["original"]["path"] = "changed.json"
                        self.assertNotEqual(decoded.items[0].metadata, items[0].metadata)

    def test_public_validators_never_reuse_a_prior_call(self):
        item = sample()
        for validate in (lambda: v.validate_original(item.metadata["original"], item.raw),
                         lambda: v.validate_metadata(item.metadata, item.raw)):
            for _ in range(2):
                _value, counts = measured(validate)
                self.assert_one_answer(counts)
        other = sample(2)
        for validate in (v.validate_original, v.validate_metadata):
            value = item.metadata["original"] if validate is v.validate_original else item.metadata
            with self.assertRaises(a.AnswerError):
                validate(value, other.raw)

    def test_provenance_single_payload_validation_and_complete_context_seal(self):
        for version in (1, 2):
            for fence in (None, b"\n", b"\r\n"):
                item = sample(version=version, fence=fence)
                ctx = context(item.metadata)
                result, counts = measured(lambda: p.build_provenance_v2(
                    metadata=item.metadata, answer=item.answer, **ctx))
                self.assert_one_answer(counts)
                self.assertEqual(counts["validate_metadata"], 1)
                self.assertEqual(p.validate_provenance_v2(result, **ctx), result)
                self.assertEqual(result["answer_attestation"]["payload"], item.answer.payload)
                result["answer_attestation"]["payload"]["summary"] = "other"
                self.assertNotEqual(item.answer.payload["summary"], "other")
                with self.assertRaises(a.AnswerError):
                    p.validate_provenance_v2(result, **ctx)
                for key in ("task_id", "review_receipt_id", "project_id"):
                    changed = {**ctx, key: ctx[key] + "0"}
                    original = p.build_provenance_v2(metadata=item.metadata, answer=item.answer, **ctx)
                    with self.assertRaises(a.AnswerError):
                        p.validate_provenance_v2(original, **changed)

    def test_provenance_dict_arm_remains_detached_and_self_validated(self):
        item = sample()
        result, counts = measured(lambda: p.build_provenance_v2(
            metadata=item.metadata, answer=item.answer.payload, **context(item.metadata)))
        self.assertEqual(counts["_parse_answer"], 0)
        self.assertEqual(counts["validate_payload"], 1)
        self.assertEqual(counts["answer_privacy"], 1)
        self.assertEqual(p.validate_provenance_v2(result, **context(item.metadata)), result)

    def test_provenance_rejects_detached_raw_payload_and_wrong_exact_types(self):
        item, other = sample(), sample(2)
        for answer in (replace(item.answer, raw=other.raw),
                       replace(item.answer, payload=other.answer.payload),
                       replace(item.answer, payload_start=True),
                       replace(item.answer, original_digest=other.answer.original_digest),
                       SimpleNamespace(**vars(item.answer))):
            with self.subTest(answer=type(answer).__name__), self.assertRaises(a.AnswerError):
                p.build_provenance_v2(metadata=item.metadata, answer=answer, **context(item.metadata))
        with self.assertRaises(a.AnswerError):
            v._validate_metadata_with_answer(item.metadata, SimpleNamespace(**vars(item.answer)))

    def test_original_all_range_and_digest_fields_remain_bound(self):
        item = sample(fence=b"\r\n")
        for key in ("byte_length", "payload_start", "payload_length", "digest", "payload_digest"):
            metadata = copy.deepcopy(item.metadata)
            old = metadata["original"][key]
            metadata["original"][key] = old + 1 if type(old) is int else "sha256:" + "0" * 64
            with self.subTest(key=key), self.assertRaises(ReviewEvidenceError):
                t.frame_collected_submission(metadata["basis"]["preparation_id"],
                    (replace(item, metadata=metadata),))

    def test_transport_shape_privacy_semantics_and_binding_diagnostic_order(self):
        item = sample()
        frame = json.loads(t.frame_collected_submission(item.metadata["basis"]["preparation_id"], (item,)))
        cases = (({"summary": "Authorization: Bearer fake-test", "verdict": "invalid"}, "privacy_rejected"),
                 ({"verdict": "invalid"}, "format_invalid"),
                 ({"summary": "x" * 1001}, "capacity_exceeded"),
                 ({"findings": [dict(severity="low", summary="x"), dict(severity="low", summary=" x ")]}, "format_invalid"))
        for changes, expected in cases:
            envelope = copy.deepcopy(frame)
            raw = json.dumps({**item.answer.payload, **changes}).encode()
            envelope["items"][0]["original_base64"] = base64.b64encode(raw).decode()
            with self.subTest(changes=tuple(changes)), self.assertRaises(ReviewEvidenceError) as caught:
                t.decode_collected_submission(json.dumps(envelope).encode())
            self.assertEqual(caught.exception.code, expected)
        envelope = copy.deepcopy(frame)
        envelope["items"][0]["metadata"]["attempt"] = True
        envelope["items"][0]["original_base64"] = base64.b64encode(json.dumps({
            **item.answer.payload, "summary": "Authorization: Bearer fake-test"}).encode()).decode()
        with self.assertRaises(ReviewEvidenceError) as caught:
            t.decode_collected_submission(json.dumps(envelope).encode())
        self.assertEqual(caught.exception.code, "format_invalid")

    def test_readback_one_parse_and_new_admission_after_each_physical_read(self):
        for version in (1, 2):
            item = sample(version=version)
            saved = record(item)
            service = object.__new__(CollectionService)
            service.repo = None
            service.original_paths = (item.metadata["original"]["path"],)
            with patch.object(files, "checked_path", return_value="unused"), \
                    patch.object(files, "read_bounded", return_value=item.raw) as read:
                for _ in range(2):
                    result, counts = measured(lambda: service._readback(saved, 1, 0))
                    self.assert_one_answer(counts)
                    self.assertEqual(result, (item.raw, item.metadata))
                read.return_value = sample(2, version).raw
                with self.assertRaises(a.AnswerError):
                    service._readback(saved, 1, 0)

    def test_readback_keeps_second_physical_confirmation_and_live_basis_checks(self):
        item = sample()
        service = object.__new__(CollectionService)
        service.repo = None
        service.original_paths = (item.metadata["original"]["path"],)
        service._record = Mock(return_value=record(item))
        service.check_basis = Mock()
        with patch.object(files, "checked_path", return_value="unused"), \
                patch.object(files, "read_bounded", side_effect=(item.raw, sample(2).raw)):
            with self.assertRaises(a.AnswerError) as caught:
                service.readback(1)
        self.assertEqual(caught.exception.failure["class"], "save_unknown")
        self.assertEqual(service.check_basis.call_count, 2)

    def test_publication_keeps_fresh_prewrite_validation_and_recovery_admission(self):
        from task_governance_tool.review_answer_collection_repository import CollectionRepository
        for version in (1, 2):
            item = sample(version=version)
            with tempfile.TemporaryDirectory() as directory, patch.object(files, "require_ignored"):
                root = Path(directory)
                (root / ".tmp-review").mkdir()
                repository = CollectionRepository(root / "collection.sqlite")
                repository.initialize((item.metadata["basis"],))
                service = CollectionService(root, repository,
                    original_paths=(item.metadata["original"]["path"],), admit_basis=Mock())
                service.bind(1, item.metadata["binding"])
                observation = dict(source_identity=item.metadata["source_identity"], answer_bytes=item.raw,
                    complete=True, terminal="completed", answer_source=source("answer_observation", version),
                    terminal_source=source("terminal_observation", version))
                real_write = files.write_exclusive
                def interrupted(path, raw):
                    real_write(path, raw)
                    raise OSError("simulated lost acknowledgement")
                def observe():
                    with patch.object(files, "write_exclusive", side_effect=interrupted):
                        with self.assertRaises(a.AnswerError) as caught:
                            service.observe(1, observation)
                    self.assertEqual(caught.exception.failure["class"], "save_unknown")
                _value, counts = measured(observe)
                self.assert_one_answer(counts, 2)  # Initial admission plus fresh prewrite admission.
                _value, counts = measured(lambda: service.reconcile_publication(1))
                self.assert_one_answer(counts)
                _value, counts = measured(lambda: service.readback(1))
                self.assert_one_answer(counts)

    def test_native_writer_revalidates_changed_raw_before_sql(self):
        from task_governance_tool import storage
        from task_governance_tool.review_answer_repository import CollectedReceiptInput, insert_provenance_locked
        item = sample()
        provenance = p.build_provenance_v2(metadata=item.metadata, answer=item.answer, **context(item.metadata))
        changed = replace(item.answer, raw=sample(2).raw)
        submitted = CollectedReceiptInput(item.metadata, changed, "sha256:" + "a" * 64)
        connection = Mock()
        with patch.object(storage, "_require_evidence_writer"), self.assertRaises(a.AnswerError):
            insert_provenance_locked(connection, receipt={}, provenance=provenance, collected=submitted)
        connection.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
