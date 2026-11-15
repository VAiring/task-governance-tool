"""Operation-local answer reuse must preserve fresh external admission."""
import base64
from collections import Counter
import copy
from dataclasses import replace
from hashlib import sha256
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


class CollectionBatchReadTests(unittest.TestCase):
    """One batch shares observations; every new read re-admits mutable state."""

    def setUp(self):
        from task_governance_tool.review_answer_collection_repository import CollectionRepository
        from task_governance_tool.review_answer_handoff import _CollectionAdmission
        self.repo = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.repo / ".tmp-review").mkdir()
        self.packet = self.repo / ".tmp-review/packet.json"
        self.packet.write_bytes(b'packet fixture')
        items = [sample(slot, version=2) for slot in (1, 2)]
        for item in items:
            basis = item.metadata["basis"]
            basis["packet_digest"] = "sha256:" + sha256(self.packet.read_bytes()).hexdigest()
            item.metadata["binding"]["basis_digest"] = v.basis_digest(basis)
        bases = tuple(item.metadata["basis"] for item in items)
        self.store = CollectionRepository(self.repo / "collection.sqlite")
        self.store.initialize(bases)
        self.paths = tuple(item.metadata["original"]["path"] for item in items)
        self.service = CollectionService(self.repo, self.store, original_paths=self.paths, admit_basis=Mock())
        self.enterContext(patch.object(files, "require_ignored"))
        for slot, item in enumerate(items, 1):
            self.service.bind(slot, item.metadata["binding"])
            self.service.observe(slot, dict(source_identity=item.metadata["source_identity"], answer_bytes=item.raw,
                complete=True, terminal="completed", answer_source=source("answer_observation", 2),
                terminal_source=source("terminal_observation", 2)))
        self.admission = _CollectionAdmission(self.repo, SimpleNamespace(target=object(), paths=object()),
            SimpleNamespace(basis=bases[0]["execution_basis"]), self.store, tuple(v.basis_digest(b) for b in bases),
            ".tmp-review/packet.json", sha256(self.packet.read_bytes()).hexdigest(), self.paths, False, True, None)
        self.service.admit_basis = self.admission

    def test_same_results_share_packet_check_but_next_read_checks_again(self):
        from task_governance_tool.review_answer_handoff import _CollectionAdmission
        expected = tuple(self.service.readback_item(slot) for slot in (1, 2))
        original = _CollectionAdmission._packet
        with patch.object(_CollectionAdmission, "_packet", autospec=True, side_effect=original) as packet:
            self.assertEqual(self.service.readback_items((1, 2)), expected)
            self.assertEqual(packet.call_count, 2)
            self.assertEqual(self.service.readback_items((1, 2)), expected)
            self.assertEqual(packet.call_count, 4)
        (self.repo / self.paths[1]).write_bytes(b'replaced')
        with self.assertRaises(a.AnswerError):
            self.service.readback_items((1, 2))

    def test_packet_original_and_operational_change_during_batch_are_rejected(self):
        before = self.store.read()
        for changed in ("packet", "original", "record"):
            packet_bytes = self.packet.read_bytes()
            original_bytes = (self.repo / self.paths[0]).read_bytes()
            real = self.service._readback_item
            def read(record, slot, ordinal, **kwargs):
                item = real(record, slot, ordinal, **kwargs)
                if slot == 2:
                    if changed == "packet":
                        self.packet.write_bytes(b'changed packet')
                    elif changed == "original":
                        (self.repo / self.paths[0]).write_bytes(b'changed original')
                    else:
                        with self.store.serial() as lease:
                            self.service.mark_registration_locked(lease, (1,), "sha256:" + "d" * 64)
                return item
            with self.subTest(changed=changed), patch.object(self.service, "_readback_item", side_effect=read):
                with self.assertRaises(a.AnswerError):
                    self.service.readback_items((1, 2))
            self.packet.write_bytes(packet_bytes)
            (self.repo / self.paths[0]).write_bytes(original_bytes)
        self.assertNotEqual(before, self.store.read())

    def test_native_state_and_git_observations_do_not_escape_one_batch(self):
        from task_governance_tool import review_answer_handoff as handoff
        self.service.admit_basis = replace(self.admission, current=True)
        actual = copy.deepcopy(self.admission.root.basis)
        with patch.object(handoff._CollectionAdmission, "_preparation") as preparation, \
                patch.object(handoff, "connect_initialized_readonly", side_effect=lambda *a, **k: Mock()) as connect, \
                patch.object(handoff, "read_review_basis", side_effect=lambda *a, **k: copy.deepcopy(actual)), \
                patch.object(handoff._CollectionAdmission, "_material") as material:
            self.service.readback_items((1, 2))
            self.assertEqual((connect.call_count, preparation.call_count, material.call_count), (2, 2, 2))
            actual["target_generation"] += 1
            with self.assertRaises(a.AnswerError):
                self.service.readback_items((1, 2))
            self.assertEqual(connect.call_count, 3)
            self.assertEqual(material.call_count, 2)
        # No shared admission suppresses the policy or separately issued pins.
        self.service.admit_basis = replace(self.admission, enabled=lambda: False)
        with self.assertRaises(a.AnswerError):
            self.service.readback_items((1, 2))
        self.service.admit_basis = replace(self.admission, issued_digests=(self.admission.issued_digests[0], "sha256:" + "f" * 64))
        with self.assertRaises(a.AnswerError):
            self.service.readback_items((1, 2))

    def test_read_only_invalidation_never_mutates_tracking(self):
        from task_governance_tool.review_answer_handoff import _CollectionAdmission
        from task_governance_tool.review_answer_collection import CollectionInvalidated
        before = self.store.read()
        with patch.object(_CollectionAdmission, "_material", side_effect=CollectionInvalidated):
            with self.assertRaises(CollectionInvalidated):
                self.service.readback_items((1, 2))
        self.assertEqual(self.store.read(), before)

    def test_saved_original_batch_preserves_individual_binding_and_packet_confirmation(self):
        from task_governance_tool import review_handoff_originals as originals
        bases = tuple(row["basis"] for row in self.store.read())
        root = SimpleNamespace(version=2, collection_bases=bases)
        with patch.object(files, "decode_packet", return_value={"review_session_context": {}}), \
                patch.object(originals, "collection_location", return_value=(None, root, bases)) as locate, \
                patch("task_governance_tool.review_answer_handoff.service_for", return_value=self.service):
            observed = originals.read_original_batch(self.repo, ".tmp-review/packet.json", self.paths)
            self.assertEqual(locate.call_count, 1)
            self.assertEqual(tuple(row.result_path for row in observed), self.paths)
            self.assertEqual(len({row.bound.binding.session_id for row in observed}), 2)
            original = self.service.readback_items
            def changed(slots):
                result = original(slots)
                self.packet.write_bytes(b'changed after admission')
                return result
            with patch.object(self.service, "readback_items", side_effect=changed):
                with self.assertRaises(files.HandoffError):
                    originals.read_original_batch(self.repo, ".tmp-review/packet.json", self.paths)


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

    def test_original_and_report_share_parse_but_next_physical_read_is_fresh(self):
        from task_governance_tool.review_handoff_originals import read_original
        from task_governance_tool.review_answer_finalization import review_view
        for size in (1, 2, 8):
            items = tuple(sample(slot) for slot in range(1, size + 1))
            for item in items:
                service = object.__new__(CollectionService)
                service.repo = None
                # Each source group uses its original slot, even in a retained batch.
                service.original_paths = tuple(value.metadata["original"]["path"] for value in items)
                service._record = Mock(return_value=record(item))
                service.check_basis = Mock()
                root = SimpleNamespace(collection_bases=(item.metadata["basis"],))
                def physical(path, _limit):
                    return b"packet" if path == "packet.json" else item.raw
                def observe_and_report():
                    source = read_original(None, "packet.json", item.metadata["original"]["path"])
                    return source, review_view(source)
                with patch.object(files, "checked_path", side_effect=lambda _repo, path: path), \
                        patch.object(files, "read_bounded", side_effect=physical) as read, \
                        patch.object(files, "decode_packet", return_value={"review_session_context": {}}), \
                        patch("task_governance_tool.review_handoff_originals.collection_location", return_value=(None, root, root.collection_bases)), \
                        patch("task_governance_tool.review_answer_handoff.service_for", return_value=service):
                    (source, report), counts = measured(observe_and_report)
                    self.assert_one_answer(counts)
                    self.assertEqual(report, {"reviewer": item.metadata["binding"]["reviewer_key"],
                        "kind": "independent", **item.answer.payload})
                    self.assertEqual((read.call_count, service.check_basis.call_count), (3, 2))
                    report["checks"].append("caller modification")
                    self.assertEqual(review_view(source)["checks"], item.answer.payload["checks"])
                    _result, counts = measured(observe_and_report)
                    self.assert_one_answer(counts)
                    self.assertEqual(service.check_basis.call_count, 4)
                    with patch.object(files, "read_bounded", side_effect=lambda path, _limit:
                            b"packet" if path == "packet.json" else sample(size + 1).raw):
                        with self.assertRaises(a.AnswerError):
                            observe_and_report()

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

    def test_initial_submission_uses_admitted_parse_and_preserves_physical_confirmation(self):
        from contextlib import contextmanager
        from task_governance_tool import review_answer_handoff as handoff
        for count in (1, 2, 8):
            items = tuple(sample(slot) for slot in range(1, count + 1))
            @contextmanager
            def admitted():
                yield object(), items
            root = SimpleNamespace(version=2, result_paths=tuple(item.metadata['original']['path'] for item in items),
                collection_bases=(items[0].metadata['basis'],), basis=items[0].metadata['basis']['execution_basis'],
                preparation_id=items[0].metadata['basis']['preparation_id'])
            originals = dict(zip(root.result_paths, (item.raw for item in items)))
            with patch.object(handoff, 'service_for', return_value=SimpleNamespace(admission_items=admitted)), \
                 patch.object(files, 'checked_path', side_effect=lambda _repo, path: path), \
                 patch.object(files, 'read_bounded', side_effect=lambda path, _limit: originals[path]) as physical:
                (task, raw), counts = measured(lambda: handoff.initial_submission(None, root, None, root.result_paths))
                self.assertEqual(task, root.basis['task_id'])
                self.assert_one_answer(counts, count)  # Framing's independent input boundary only.
                self.assertEqual(physical.call_count, count)
                self.assertEqual(t.decode_collected_submission(raw).items, items)
                originals[root.result_paths[-1]] = sample(count + 1).raw
                with self.assertRaises(ReviewEvidenceError):
                    handoff.initial_submission(None, root, None, root.result_paths)


if __name__ == "__main__":
    unittest.main()
