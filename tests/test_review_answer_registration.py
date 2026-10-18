"""Atomic collected writer, public selectors and uncertain finalizer outcomes."""

from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import replace
import sqlite3
from types import SimpleNamespace
from unittest import TestCase, mock

from tests.review_answer_fixture_support import review_fixture
from task_governance_tool import storage, review_answer_audit, review_answer_registration
from task_governance_tool.review_answer_transport import CollectedItem, frame_collected_submission, decode_collected_submission
from task_governance_tool.review_collected_results import add_collected_review_results, dispatch_entry
from task_governance_tool.review_answer_finalization import registered_ids
from task_governance_tool.review_handoff_transport import HandoffError
from task_governance_tool.review_results import ReviewResultInputError
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError


class RegistrationTests(TestCase):
    def setUp(self):
        self.fixture = self.enterContext(review_fixture(schema_version=30, contract=None))

    def submission(self, verdicts=("pass", "pass")):
        items = []
        for slot, verdict in enumerate(verdicts, 1):
            metadata, answer = self.fixture.metadata(slot, verdict)
            metadata["original"]["path"] = f"reviews/result-{slot}.json"
            items.append(CollectedItem(slot, answer.raw, metadata, answer))
        raw = frame_collected_submission(items[0].metadata["basis"]["preparation_id"], items)
        return decode_collected_submission(raw)

    def register(self, submission):
        with closing(storage.connect_initialized(self.fixture.target)) as connection, connection:
            return add_collected_review_results(connection, self.fixture.target.project,
                self.fixture.basis["task_id"], submission, caller=self.fixture.caller,
                database_target=self.fixture.target)

    def test_two_passes_use_shared_gate_and_exact_inventory(self):
        submitted = self.submission()
        result = self.register(submitted)
        self.assertTrue(result["review_gate"]["satisfied"])
        with closing(storage.connect_initialized_readonly(self.fixture.target)) as connection:
            observed = review_answer_audit.read(connection, self.fixture.basis["project_id"],
                self.fixture.basis["task_id"], (submitted.preparation_id, submitted.submission_digest))
        self.assertEqual(observed["status"], "recorded")
        self.assertEqual(observed["entries"], [dispatch_entry(item, receipt["receipt"]["review_receipt_id"], submitted.submission_digest)
            for item, receipt in zip(submitted.items, result["receipts"])])

    def test_required_collected_summaries_normalize_without_changing_original_payload(self):
        import json
        from task_governance_tool.review_answer import decode_answer
        from task_governance_tool.review_repository import read_review_receipt_with_provenance
        from task_governance_tool import schema_review_answers
        items=[]
        for slot,verdict in enumerate(("pass","changes_requested","unable_to_review"),1):
            metadata,answer=self.fixture.metadata(slot,verdict)
            declared=deepcopy(answer.payload)
            declared.update(summary=" \t\u3000Summary "+str(slot)+"\u3000\r\n ",
                findings=[dict(severity="low",summary=" \t\u3000Finding "+str(slot)+"\u3000 ")],
                checks=["  Source inspected  "],limitations=["\tDeclared limitation\u3000"])
            raw=b"```json\r\n"+json.dumps(declared,ensure_ascii=False).encode("utf-8")+b"\r\n```"
            answer=decode_answer(raw)
            metadata["original"].update(byte_length=len(raw),digest=answer.original_digest,
                payload_start=answer.payload_start,payload_length=answer.payload_length,payload_digest=answer.payload_digest)
            items.append(CollectedItem(slot,raw,metadata,answer))
        submitted=decode_collected_submission(frame_collected_submission(items[0].metadata["basis"]["preparation_id"],items))
        result=self.register(submitted)
        self.assertFalse(result["review_gate"]["satisfied"])
        with closing(storage.connect_initialized_readonly(self.fixture.target)) as connection:
            schema_review_answers.validate_storage(connection)
            for item,saved in zip(items,result["receipts"]):
                declared=item.answer.payload
                self.assertEqual(saved["receipt"]["summary"],declared["summary"].strip())
                self.assertEqual(saved["findings"][0]["finding"]["summary"],declared["findings"][0]["summary"].strip())
                stored=read_review_receipt_with_provenance(connection,review_receipt_id=saved["receipt"]["review_receipt_id"])
                attestation=stored["provenance"]["answer_attestation"]
                self.assertEqual(attestation["payload"],declared)
                self.assertEqual(attestation["original"],{key:value for key,value in item.metadata["original"].items() if key!="path"})
                self.assertEqual(submitted.items[item.slot-1].raw,item.raw)

    def test_second_receipt_storage_fault_rolls_back_entire_batch(self):
        import sqlite3
        from task_governance_tool import review_collected_results
        submitted = self.submission()
        actual = review_collected_results.add_review_receipt
        calls = []
        def write(*args, **kwargs):
            if calls:
                raise sqlite3.OperationalError("injected write failure")
            calls.append(True)
            return actual(*args, **kwargs)
        with mock.patch.object(review_collected_results, "add_review_receipt", side_effect=write):
            with self.assertRaises(sqlite3.OperationalError):
                self.register(submitted)
        with closing(storage.connect_initialized_readonly(self.fixture.target)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0], 0)

    def test_normal_show_exposes_unable_that_blocks_two_passes(self):
        from task_governance_tool.task_show_projection import show_task, build_task_show_data
        self.register(self.submission(("pass", "pass", "unable_to_review")))
        with closing(storage.connect_initialized_readonly(self.fixture.target)) as connection:
            result = show_task(connection, self.fixture.target.project,
                self.fixture.basis["task_id"], caller=self.fixture.caller, include_current_context=True)
        data = build_task_show_data(result, audit=False)["review_evidence"]
        self.assertEqual(data["counts"]["unable_current_generation"], 1)
        self.assertEqual(data["gate"]["qualifying_independent_passes"], 2)
        self.assertFalse(data["gate"]["satisfied"])
        self.assertEqual(len(data["current_receipts"]), 3)


class RetainedSourceAdmissionTests(TestCase):
    def setUp(self):
        self.fixture = self.enterContext(review_fixture(schema_version=30, contract=None))
        from task_governance_tool import review_continuation as values
        from task_governance_tool.review_answer_repository import read_native_collected_captures
        from task_governance_tool.review_answer_values import basis_digest
        predecessor, metadata, answer = self.fixture.collected_source_continuation()
        consumer = self.fixture.retain_collected_source(predecessor, metadata)
        current, current_answer = self.fixture.metadata(2)
        context = dict(continuation_id=consumer.continuation_id, sequence=consumer.sequence,
            selection_digest=values.selection_digest(consumer.selected))
        current["basis"]["continuation"] = context
        current["binding"].update(slot=1, basis_digest=basis_digest(current["basis"]))
        with closing(storage.connect_initialized_readonly(self.fixture.target)) as connection:
            captures = tuple(capture for capture, _ in read_native_collected_captures(connection, consumer.continuation_id))
        self.submitted = decode_collected_submission(frame_collected_submission(metadata["basis"]["preparation_id"],
            (CollectedItem(1, current_answer.raw, current, current_answer), CollectedItem(2, answer.raw, metadata, answer)),
            continuation=context, retained_originals=captures))

    def test_registration_uses_fresh_selected_and_exact_native_retained_sources(self):
        from task_governance_tool import review_answer_handoff as handoff
        resolution = SimpleNamespace(target=self.fixture.target)
        admitted = []
        def service_for(_repo, _resolution, _root, basis, *, retained=False):
            items = tuple(item for item in self.submitted.items if item.metadata["basis"] == basis)
            admitted.append((basis["continuation"], retained))
            service = mock.Mock()
            context = mock.MagicMock()
            context.__enter__.return_value = (mock.Mock(), tuple((item.raw, item.metadata) for item in items))
            service.admission.return_value = context
            return service
        with mock.patch.object(handoff, "root_for", return_value=(resolution, mock.Mock())), \
             mock.patch.object(handoff, "service_for", side_effect=service_for):
            with handoff.registration_admission(self.fixture.repo, self.submitted, self.fixture.caller):
                pass
        self.assertCountEqual(admitted, [(self.submitted.continuation, False),
            (self.submitted.items[1].metadata["basis"]["continuation"], True)])

    def test_old_context_requires_exact_native_capture_and_explicit_consumer_inventory(self):
        from task_governance_tool import review_answer_handoff as handoff
        from task_governance_tool.review_answer import AnswerError
        resolution = SimpleNamespace(target=self.fixture.target)
        item = self.submitted.items[1]
        handoff._admit_retained_items(resolution, self.submitted, (item,))
        changed = deepcopy(item.metadata)
        changed["binding"]["reviewer_key"] = "another-reviewer"
        bad_items = (replace(item, metadata=changed), replace(item, slot=1))
        for value in bad_items:
            with self.subTest(value=value.slot), self.assertRaises(AnswerError):
                handoff._admit_retained_items(resolution, self.submitted, (value,))
        for submitted in (replace(self.submitted, retained_originals=()), replace(self.submitted, continuation=None),
                replace(self.submitted, continuation={**self.submitted.continuation,
                    "continuation_id": "tg_review_continuation_" + "f" * 16})):
            with self.subTest(context=submitted.continuation), self.assertRaises(AnswerError):
                handoff._admit_retained_items(resolution, submitted, (item,))


class AdmissionSelectionTests(TestCase):
    def test_paired_selectors_and_audit_required(self):
        valid = dict(audit=True, collection_preparation="tg_review_preparation_" + "a"*16,
            collection_submission="sha256:" + "b"*64)
        self.assertEqual(review_answer_audit.selection(SimpleNamespace(**valid)), tuple(valid[k] for k in ("collection_preparation", "collection_submission")))
        for override in ({"audit": False}, {"collection_preparation": None}, {"collection_submission": "b"*64}):
            with self.assertRaises(Exception):
                review_answer_audit.selection(SimpleNamespace(**{**valid, **override}))
        self.assertIsNone(review_answer_audit.selection(SimpleNamespace(audit=True)))

    def test_unknown_registration_never_replays_on_absent_snapshot(self):
        from tests.test_review_answer_pure_values import metadata_fixture
        from task_governance_tool.review_answer import decode_answer
        meta, answer = metadata_fixture()
        submission = decode_collected_submission(frame_collected_submission(meta["basis"]["preparation_id"],
            (CollectedItem(1, answer.raw, meta, answer),)))
        core = mock.Mock()
        core.collection_registration.return_value = dict(status="absent", current_basis=None, entries=[])
        with self.assertRaises(HandoffError):
            registered_ids(core, submission.task_id, submission, basis=None, dispatched=True)
        core.register.assert_not_called()


class UndispatchedRecoveryTests(TestCase):
    """An outer reservation is recoverable only with complete source proof."""

    def setUp(self):
        from tests.test_review_answer_pure_values import metadata_fixture
        from task_governance_tool.review_answer_handoff import RegistrationAdmission
        items = []
        for slot in (1, 2):
            meta, answer = metadata_fixture()
            reviewer = f"10000000-0000-4000-8000-{slot:012x}"
            meta["binding"].update(slot=slot, reviewer_key="reviewer-"+str(slot),
                reviewer_session_id=reviewer)
            meta["source_identity"].update(reviewer_session_id=reviewer, message_id="message-"+str(slot))
            meta["original"]["path"] = f"reviews/result-{slot}.json"
            items.append(CollectedItem(slot, answer.raw, meta, answer))
        self.submission = decode_collected_submission(frame_collected_submission(
            items[0].metadata["basis"]["preparation_id"], items))
        self.records = [dict(registration=dict(state="not_started", submission_digest=None,
            receipt_id=None)) for _ in items]
        self.service = mock.Mock()
        self.service.repository.read.side_effect = lambda: deepcopy(self.records)
        self.admitted = RegistrationAdmission(self.submission,
            [(self.service, object(), self.submission.items)])
        self.core = mock.Mock()
        self.core.collection_registration.return_value = dict(status="absent", current_basis=None,
            preparation_id=self.submission.preparation_id,
            submission_digest=self.submission.submission_digest, entries=[], reason=None)
        self.active = False

    @contextmanager
    def admission(self, *unused):
        self.assertFalse(self.active, "collection admission was acquired recursively")
        self.active = True
        try:
            yield self.admitted
        finally:
            self.active = False

    def recover(self):
        from task_governance_tool import review_answer_handoff
        with mock.patch.object(review_answer_handoff, "registration_admission", self.admission):
            return registered_ids(self.core, self.submission.task_id, self.submission,
                basis=object(), dispatched=True)

    def test_complete_positive_proof_reenters_only_normal_writer_outside_leases(self):
        def register(*args, **kwargs):
            self.assertFalse(self.active)
            self.admitted.dispatch()  # The real writer repeats this under new leases.
            return dict(receipts=[dict(receipt=dict(review_receipt_id=value)) for value in ("one", "two")])
        self.core.register.side_effect = register
        before = deepcopy(self.records)
        self.assertEqual(self.recover(), ("one", "two"))
        self.core.register.assert_called_once()
        self.assertEqual(self.records, before)

    def test_any_dispatched_unknown_or_registered_source_blocks_entire_batch(self):
        for slot in (0, 1):
            for state in ("dispatched", "unknown", "registered"):
                self.records[slot]["registration"] = dict(state=state,
                    submission_digest=self.submission.submission_digest,
                    receipt_id="receipt" if state == "registered" else None)
                with self.subTest(slot=slot, state=state), self.assertRaises(HandoffError):
                    self.recover()
                self.core.register.assert_not_called()
                self.service.mark_registration_locked.assert_not_called()
            self.records[slot]["registration"] = dict(state="not_started", submission_digest=None, receipt_id=None)

    def test_empty_partial_duplicate_or_noncollected_proof_never_qualifies(self):
        from task_governance_tool.review_answer_handoff import RegistrationAdmission
        for items in ((), self.submission.items[:1], (self.submission.items[0],)*2):
            self.admitted = RegistrationAdmission(self.submission,
                [(self.service, object(), items)])
            with self.subTest(items=len(items)), self.assertRaises(HandoffError):
                self.recover()
        self.admitted = RegistrationAdmission(replace(self.submission,
            items=(SimpleNamespace(slot=1), self.submission.items[1])),
            [(self.service, object(), self.submission.items)])
        self.assertFalse(self.admitted.prove_not_dispatched())
        self.core.register.assert_not_called()

    def test_uncertain_conflicting_or_different_audit_never_enters_source_proof(self):
        initial = deepcopy(self.core.collection_registration.return_value)
        for changed in (dict(status="unknown"), dict(status="conflict"),
                dict(preparation_id="different"), dict(submission_digest="different"),
                dict(reason="partial_inventory"), dict(reason="source_mismatch"), dict(extra=None)):
            self.core.collection_registration.return_value = {**initial, **changed}
            with self.subTest(changed=changed), self.assertRaises(HandoffError):
                self.recover()
        self.core.collection_registration.return_value = {key: value for key, value in initial.items() if key != "reason"}
        with self.assertRaises(HandoffError):
            self.recover()
        self.service.repository.read.assert_not_called()
        self.core.register.assert_not_called()

    def test_losing_writer_race_is_rejected_before_any_dispatch(self):
        from task_governance_tool.review_answer import AnswerError
        def register(*args, **kwargs):
            self.assertFalse(self.active)
            self.records[1]["registration"].update(state="dispatched",
                submission_digest=self.submission.submission_digest)
            self.admitted.dispatch()
            self.fail("a raced source was dispatched twice")
        self.core.register.side_effect = register
        with self.assertRaises(AnswerError):
            self.recover()
        self.service.mark_registration_locked.assert_not_called()

    def test_changed_original_metadata_fails_full_admission_before_source_proof(self):
        from task_governance_tool import review_answer_handoff as handoff
        root = SimpleNamespace(version=2, result_paths=("reviews/result-1.json", "reviews/result-2.json"))
        sources = [(item.raw, deepcopy(item.metadata)) for item in self.submission.items]
        sources[1][1]["source_identity"]["message_id"] = "changed-message"
        entered = mock.MagicMock()
        entered.__enter__.return_value = (object(), tuple(sources))
        self.service.admission.return_value = entered
        caller = mock.Mock()
        caller.require.return_value = self.submission.items[0].metadata["basis"]["execution_basis"]["parent_session_id"]
        with mock.patch.object(handoff, "root_for", return_value=(object(), root)), \
             mock.patch.object(handoff, "service_for", return_value=self.service):
            with self.assertRaises(ReviewResultInputError):
                with handoff.registration_admission("unused", self.submission, caller):
                    self.fail("changed source metadata was admitted")
        self.service.repository.read.assert_not_called()


class RegistrationObservationTests(TestCase):
    """The existing check exposes facts without creating a recovery permit."""

    admission = UndispatchedRecoveryTests.admission

    def setUp(self):
        UndispatchedRecoveryTests.setUp(self)
        self.record = SimpleNamespace(continuation_id=None, packet_path="reviews/packet.json",
            result_paths=("reviews/result-1.json", "reviews/result-2.json"),
            originals=tuple((b.binding.session_id, b.binding.original_result_digest)
                for b in self.submission.bindings))
        self.finalizer = mock.Mock(repo="unused", task_id=self.submission.task_id,
            core=self.core)
        self.finalizer.journal.read.return_value = self.record
        self.finalizer.journal.serial.return_value = mock.MagicMock()

    def observe(self):
        from task_governance_tool import review_answer_handoff, review_handoff_originals
        from task_governance_tool.review_answer_finalization import registration_observation
        with mock.patch.object(review_answer_handoff, "registration_admission", self.admission), \
             mock.patch.object(review_handoff_originals, "submission", return_value=(self.submission.task_id, self.submission.raw)):
            return registration_observation(self.finalizer, self.record)

    def assert_readonly(self):
        self.core.register.assert_not_called()
        self.core.complete.assert_not_called()
        self.finalizer.journal.advance.assert_not_called()
        self.finalizer.journal.observe_reviewers.assert_not_called()
        self.finalizer.journal.serial.assert_called_once_with()
        self.service.mark_registration_locked.assert_not_called()
        self.service.confirm_registration_locked.assert_not_called()

    def test_complete_sources_and_exact_public_audit_are_observed_without_mutation(self):
        before = deepcopy(self.records)
        def lookup(*unused):
            self.assertTrue(self.active, "native read must remain under every source lease")
            return dict(status="absent", current_basis=None, entries=[], reason=None,
                preparation_id=self.submission.preparation_id, submission_digest=self.submission.submission_digest)
        self.core.collection_registration.side_effect = lookup
        result = self.observe()
        self.assertEqual(set(result), {"version", "preparation_id", "submission_digest", "native", "sources", "classification", "reason"})
        self.assertEqual((result["classification"], result["reason"]), ("not_dispatched", None))
        self.assertEqual(result["sources"], [dict(slot=slot, source_slot=slot, protocol="review-answer-v1",
            state="not_started", receipt_id=None, submission_digest_match="absent") for slot in (1, 2)])
        self.assertEqual((result["preparation_id"], result["submission_digest"]),
            (self.submission.preparation_id, self.submission.submission_digest))
        self.assertEqual(self.records, before)
        self.assertFalse(self.active)
        self.assert_readonly()

    def test_dispatched_unknown_and_recorded_sources_have_distinct_facts(self):
        for row in self.records:
            row["registration"].update(state="dispatched", submission_digest=self.submission.submission_digest)
        self.records[1]["registration"]["state"] = "unknown"
        result = self.observe()
        self.assertEqual(result["classification"], "dispatched")
        self.assertEqual([row["state"] for row in result["sources"]], ["dispatched", "unknown"])
        receipt_ids = ["tg_review_receipt_"+str(slot)*16 for slot in (1, 2)]
        self.core.collection_registration.return_value.update(status="recorded", current_basis=True,
            entries=[dispatch_entry(item, receipt, self.submission.submission_digest)
                for item, receipt in zip(self.submission.items, receipt_ids)])
        self.records[0]["registration"].update(state="registered", receipt_id=receipt_ids[0])
        result = self.observe()
        self.assertEqual(result["classification"], "recorded")
        self.assertEqual(result["sources"][0]["receipt_id"], receipt_ids[0])
        self.core.register.assert_not_called()
        self.service.confirm_registration_locked.assert_not_called()

    def test_mixed_states_changed_digest_and_native_conflicts_never_look_settled(self):
        self.records[0]["registration"].update(state="dispatched", submission_digest="sha256:"+"f"*64)
        result = self.observe()
        self.assertEqual((result["classification"], result["reason"]), ("inconsistent", "source_native_mismatch"))
        self.assertEqual(result["sources"][0]["submission_digest_match"], "different")
        for status, reason, classification in (("conflict", "partial_inventory", "inconsistent"),
                ("unknown", "inventory_unavailable", "unavailable")):
            self.core.collection_registration.return_value.update(status=status, reason=reason)
            result = self.observe()
            self.assertEqual((result["classification"], result["reason"]), (classification, "native_"+status))
            self.assertEqual(len(result["sources"]), 2)

    def test_mixed_legacy_inventory_has_no_invented_source_registration(self):
        from task_governance_tool.review_answer_transport import LegacyItem
        from task_governance_tool.review_answer_handoff import RegistrationAdmission
        from task_governance_tool.review_answer_finalization import _classify_registration
        legacy = LegacyItem(2, b"", "", {}, None)
        submitted = replace(self.submission, items=(self.submission.items[0], legacy))
        admitted = RegistrationAdmission(submitted, [(self.service, object(), submitted.items[:1])])
        sources = admitted.registration_states()
        self.assertEqual(sources[1], dict(slot=2, protocol="legacy-review-result-v1", source_slot=None,
            state=None, submission_digest_match=None, receipt_id=None))
        self.assertEqual(_classify_registration(self.core.collection_registration.return_value, sources),
            ("inconsistent", "mixed_protocol"))

    def test_invalid_or_partial_sources_and_bad_native_shapes_return_no_prefix(self):
        original_groups = self.admitted.groups
        self.admitted.groups = [(self.service, object(), self.submission.items[:1])]
        result = self.observe()
        self.assertEqual((result["classification"], result["reason"], result["sources"]),
            ("unavailable", "source_unavailable", []))
        self.core.collection_registration.assert_not_called()
        self.admitted.groups = original_groups
        self.records[1]["registration"]["receipt_id"] = "SENTINEL-private"
        self.assertEqual(self.observe()["sources"], [])
        self.records[1]["registration"]["receipt_id"] = None
        initial = deepcopy(self.core.collection_registration.return_value)
        for changed in (dict(reason="partial_inventory"), dict(private="SENTINEL-private"),
                dict(submission_digest="different"), dict(entries=[{"private": "SENTINEL-private"}])):
            self.core.collection_registration.return_value = {**initial, **changed}
            result = self.observe()
            self.assertEqual((result["classification"], result["reason"], result["sources"], result["native"]),
                ("unavailable", "native_unavailable", [], None))
            self.assertNotIn("SENTINEL", str(result))

    def test_busy_and_cleanup_failure_are_sanitized_and_never_create_a_lock(self):
        manager = self.finalizer.journal.serial.return_value
        manager.__enter__.side_effect = RepositoryError("writer_busy")
        result = self.observe()
        self.assertEqual((result["classification"], result["reason"]), ("unavailable", "busy"))
        self.core.collection_registration.assert_not_called()
        self.assert_readonly()
        manager.__enter__.side_effect = None
        manager.__exit__.side_effect = OSError("SENTINEL private cleanup")
        result = self.observe()
        self.assertEqual((result["classification"], result["reason"], result["native"], result["sources"]),
            ("unavailable", "finalization_unavailable", None, []))
        self.assertNotIn("SENTINEL", str(result))

    def test_changed_record_and_original_binding_stay_unavailable(self):
        self.finalizer.journal.read.return_value = object()
        self.assertEqual(self.observe()["reason"], "basis_changed")
        self.finalizer.journal.read.return_value = self.record
        self.record.originals = ()
        self.assertEqual(self.observe()["reason"], "submission_unavailable")
        self.core.collection_registration.assert_not_called()

    def test_source_lease_cleanup_failure_discards_the_complete_projection(self):
        normal = self.admission
        @contextmanager
        def cleanup_failure(*args):
            with normal(*args) as admitted:
                yield admitted
            raise OSError("SENTINEL source cleanup detail")
        self.admission = cleanup_failure
        result = self.observe()
        self.assertEqual((result["classification"], result["reason"], result["sources"], result["native"]),
            ("unavailable", "source_unavailable", [], None))
        self.assertNotIn("SENTINEL", str(result))
        self.assert_readonly()

    def test_valid_legacy_submission_keeps_existing_check_shape(self):
        from task_governance_tool import review_session_transport
        with mock.patch.object(review_session_transport, "decode_submission", return_value=object()):
            self.assertIsNone(self.observe())
        self.core.collection_registration.assert_not_called()
        self.service.repository.read.assert_not_called()


class RegistrationBoundaryTests(TestCase):
    """Keep the real atomic writer while injecting only lifecycle failures."""

    submission = RegistrationTests.submission

    def setUp(self):
        self.fixture = self.enterContext(review_fixture(schema_version=30, contract=None))
        self.context = SimpleNamespace(repo=self.fixture.repo,
            caller=self.fixture.caller,
            args=SimpleNamespace(task_id=self.fixture.basis["task_id"],
                user_approved_reviewers=()))
        self.admitted = mock.Mock()
        self.admission = mock.MagicMock()
        self.admission.__enter__.return_value = self.admitted
        self.admission.__exit__.return_value = False
        self.enterContext(mock.patch.object(review_answer_registration,
            "registration_admission", return_value=self.admission))
        self.writer = self.enterContext(mock.patch.object(review_answer_registration,
            "add_collected_review_results", wraps=add_collected_review_results))
        self.submitted = self.submission()

    def register(self):
        return review_answer_registration.register(self.context, self.fixture.target,
            self.submitted)

    def assert_inventory(self, count, result=None):
        with closing(storage.connect_initialized_readonly(self.fixture.target)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0], count)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_collection_dispatches").fetchone()[0], int(bool(count)))
            observed = review_answer_audit.read(connection, self.fixture.basis["project_id"],
                self.fixture.basis["task_id"],
                (self.submitted.preparation_id, self.submitted.submission_digest))
        self.assertEqual(observed["status"], "recorded" if count else "absent")
        if result is not None:
            self.assertEqual(observed["entries"], [dispatch_entry(item,
                row["receipt"]["review_receipt_id"], self.submitted.submission_digest)
                for item, row in zip(self.submitted.items, result["receipts"])])

    def assert_saved_warning(self, result, warnings):
        self.assertTrue(result["review_gate"]["satisfied"])
        self.assertEqual(len(result["receipts"]), 2)
        self.assertEqual(warnings, [{
            "code": "collection_registration_acknowledgement_unavailable",
            "message": "Review results were recorded. Collection acknowledgement is unresolved; do not resubmit."}])
        self.writer.assert_called_once()
        self.admitted.dispatch.assert_called_once_with()
        self.assert_inventory(2, result)

    def test_success_confirms_the_real_committed_result_once(self):
        result, warnings = self.register()
        self.assertEqual(warnings, [])
        self.writer.assert_called_once()
        self.admitted.dispatch.assert_called_once_with()
        self.admitted.confirm.assert_called_once_with(result)
        self.assert_inventory(2, result)

    def test_confirmation_failure_keeps_committed_result(self):
        self.admitted.confirm.side_effect = OSError("SENTINEL private confirmation detail")
        result, warnings = self.register()
        self.assert_saved_warning(result, warnings)
        self.admitted.confirm.assert_called_once_with(result)

    def test_admission_exit_failure_keeps_confirmed_committed_result(self):
        self.admission.__exit__.side_effect = RepositoryError("SENTINEL private lease detail")
        result, warnings = self.register()
        self.assert_saved_warning(result, warnings)
        self.admitted.confirm.assert_called_once_with(result)

    def test_confirmation_and_admission_exit_failure_have_one_saved_warning(self):
        self.admitted.confirm.side_effect = OSError("SENTINEL private confirmation detail")
        self.admission.__exit__.side_effect = OSError("SENTINEL private cleanup detail")
        result, warnings = self.register()
        self.assert_saved_warning(result, warnings)
        self.admitted.confirm.assert_called_once_with(result)

    def test_connection_close_failure_keeps_committed_result_without_confirmation(self):
        connection = storage.connect_initialized(self.fixture.target)
        wrapped = _ConnectionCleanupFailure(connection, "close")
        with mock.patch.object(review_answer_registration, "connect_initialized", return_value=wrapped):
            result, warnings = self.register()
        self.assert_saved_warning(result, warnings)
        self.admitted.confirm.assert_not_called()

    def test_admission_entry_failure_opens_no_writer(self):
        self.admission.__enter__.side_effect = RepositoryError("SENTINEL private entry detail")
        with mock.patch.object(review_answer_registration, "connect_initialized") as connect:
            with self.assertRaises(ReviewResultInputError) as caught:
                self.register()
        self.assertEqual(caught.exception.code, "collection_unavailable")
        self.assertEqual(caught.exception.message, "collected review answer: incomplete")
        connect.assert_not_called()
        self.writer.assert_not_called()
        self.admitted.dispatch.assert_not_called()
        self.admitted.confirm.assert_not_called()
        self.assert_inventory(0)

    def test_dispatch_failure_opens_no_writer(self):
        self.admitted.dispatch.side_effect = HandoffError()
        with mock.patch.object(review_answer_registration, "connect_initialized") as connect:
            with self.assertRaises(ReviewResultInputError) as caught:
                self.register()
        self.assertEqual(caught.exception.code, "collection_unavailable")
        connect.assert_not_called()
        self.writer.assert_not_called()
        self.admitted.confirm.assert_not_called()
        self.assert_inventory(0)

    def test_partial_core_write_failure_rolls_back_without_confirmation(self):
        from task_governance_tool import review_collected_results
        actual = review_collected_results.add_review_receipt
        calls = []
        def write(*args, **kwargs):
            if calls:
                raise sqlite3.OperationalError("SENTINEL injected write failure")
            result = actual(*args, **kwargs)
            calls.append(result)
            return result
        with mock.patch.object(review_collected_results, "add_review_receipt", side_effect=write):
            with self.assertRaises(sqlite3.OperationalError):
                self.register()
        self.assertEqual(len(calls), 1)
        self.writer.assert_called_once()
        self.admitted.confirm.assert_not_called()
        self.assert_inventory(0)

    def test_sqlite_commit_failure_rolls_back_without_saved_success(self):
        connection = storage.connect_initialized(self.fixture.target)
        commit_attempts = []
        def authorize(action, operation, *unused):
            if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
                commit_attempts.append(operation)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        with mock.patch.object(review_answer_registration, "connect_initialized", return_value=connection):
            with self.assertRaises(sqlite3.DatabaseError):
                self.register()
        self.assertEqual(commit_attempts, ["COMMIT"])
        self.writer.assert_called_once()
        self.admitted.confirm.assert_not_called()
        self.assert_inventory(0)

    def test_uncertain_transaction_exit_does_not_infer_commit_or_replay(self):
        connection = storage.connect_initialized(self.fixture.target)
        wrapped = _ConnectionCleanupFailure(connection, "transaction_exit")
        with mock.patch.object(review_answer_registration, "connect_initialized", return_value=wrapped):
            with self.assertRaises(OSError):
                self.register()
        self.writer.assert_called_once()
        self.admitted.confirm.assert_not_called()
        # The test can inspect the actual effect; the interrupted caller cannot
        # infer it from an unsuccessful transaction exit or return success.
        self.assert_inventory(2)


class _ConnectionCleanupFailure:
    def __init__(self, connection, phase):
        self.connection, self.phase = connection, phase

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *error):
        result = self.connection.__exit__(*error)
        if self.phase == "transaction_exit" and error[0] is None:
            raise OSError("SENTINEL lost transaction acknowledgement")
        return result

    def close(self):
        self.connection.close()
        if self.phase == "close":
            raise OSError("SENTINEL private connection cleanup detail")


class SettledRecoveryTests(TestCase):
    """Actual storage, with only physical source admission replaced by a fixture."""

    def setUp(self):
        self.fixture = self.enterContext(review_fixture(schema_version=30, contract=None))
        from task_governance_tool.review_answer_handoff import RegistrationAdmission
        from task_governance_tool.review_answer_transport import SettledRegistrationRecovery
        from task_governance_tool.review_answer_values import basis_digest
        from task_governance_tool.review_finalization_basis import FinalizationBasis
        items = []
        for item in RegistrationTests.submission(self).items:
            meta = deepcopy(item.metadata)
            meta["basis"]["route"] = "integrated"
            meta["binding"]["basis_digest"] = basis_digest(meta["basis"])
            items.append(replace(item, metadata=meta))
        self.submitted = decode_collected_submission(frame_collected_submission(items[0].metadata["basis"]["preparation_id"], items))
        values = {**self.fixture.basis, "parent_thread_id": self.fixture.basis["parent_session_id"]}
        self.basis = FinalizationBasis(**{key: values[key] for key in FinalizationBasis.__dataclass_fields__})
        self.request = SettledRegistrationRecovery(self.submitted.preparation_id, self.submitted.submission_digest)
        self.context = SimpleNamespace(repo=self.fixture.repo, caller=self.fixture.caller,
            args=SimpleNamespace(task_id=self.fixture.basis["task_id"], user_approved_reviewers=()))
        self.records = [dict(registration=dict(state="dispatched", submission_digest=self.submitted.submission_digest,
            receipt_id=None)) for _ in self.submitted.items]
        self.service = mock.Mock()
        self.service.repository.read.side_effect = lambda: deepcopy(self.records)
        self.service.confirm_registration_locked.side_effect = self.confirm
        self.admitted = RegistrationAdmission(self.submitted, [(self.service, object(), self.submitted.items)])
        self.active = False
        self.enterContext(mock.patch.object(review_answer_registration, "registration_admission", self.admission))
        self.writer = self.enterContext(mock.patch.object(review_answer_registration,
            "add_collected_review_results", wraps=add_collected_review_results))

    @contextmanager
    def admission(self, *unused):
        if self.active:
            raise RepositoryError("writer_busy")
        self.active = True
        try:
            yield self.admitted
        finally:
            self.active = False

    def confirm(self, lease, slot, digest, receipt):
        self.assertTrue(self.active)
        self.assertEqual(digest, self.submitted.submission_digest)
        self.records[slot-1]["registration"].update(state="registered", receipt_id=receipt)

    def register(self, *, request=True, basis=True):
        return review_answer_registration.register(self.context, self.fixture.target, self.submitted,
            finalization_basis=self.basis if basis is True else basis,
            registration_recovery=self.request if request is True else request)

    def count(self):
        with closing(storage.connect_initialized_readonly(self.fixture.target)) as connection:
            return connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0]

    def test_recovery_keeps_leases_through_transaction_close_and_confirm_then_blocks_delayed_writer(self):
        actual_connect = review_answer_registration.connect_initialized
        owner = self
        class ObservedConnection(_ConnectionCleanupFailure):
            def __exit__(self, *error):
                owner.assertTrue(owner.active)
                return super().__exit__(*error)
            def close(self):
                owner.assertTrue(owner.active)
                return super().close()
        def connect(target):
            self.assertTrue(self.active)
            return ObservedConnection(actual_connect(target), "observe")
        with mock.patch.object(review_answer_registration, "connect_initialized", side_effect=connect):
            result, warnings = self.register()
        self.assertEqual(warnings, [])
        self.assertEqual(self.count(), 2)
        self.assertEqual([row["registration"]["state"] for row in self.records], ["registered"]*2)
        self.service.mark_registration_locked.assert_not_called()
        for request in (True, None):
            with self.subTest(request=request), self.assertRaises(ReviewResultInputError):
                self.register(request=request)
        self.writer.assert_called_once()
        self.assertEqual(self.count(), 2)

    def test_busy_and_rolled_back_writer_can_each_be_recovered_once_without_reset(self):
        with mock.patch.object(review_answer_registration, "connect_initialized",
                side_effect=storage.StorageError("database_busy", "task database is busy")):
            with self.assertRaises(storage.StorageError):
                self.register()
        self.assertEqual(self.count(), 0)
        from task_governance_tool import review_collected_results
        actual = review_collected_results.add_review_receipt
        calls = []
        def fail_second(*args, **kwargs):
            if calls:
                raise sqlite3.OperationalError("isolated rollback")
            calls.append(True)
            return actual(*args, **kwargs)
        with mock.patch.object(review_collected_results, "add_review_receipt", side_effect=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.register()
        self.assertEqual(self.count(), 0)
        self.assertEqual([row["registration"]["state"] for row in self.records], ["dispatched"]*2)
        self.records[1]["registration"]["state"] = "unknown"
        result, warnings = self.register()
        self.assertEqual(warnings, [])
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.writer.call_count, 2)
        self.service.mark_registration_locked.assert_not_called()

    def test_existing_or_competing_lease_and_partial_or_mismatched_source_refuse_before_writer(self):
        with self.admission():
            with self.assertRaises(ReviewResultInputError):
                self.register()
        original = deepcopy(self.records)
        for change in (dict(state="not_started", submission_digest=None),
                dict(state="registered", receipt_id="tg_review_receipt_"+"a"*16),
                dict(submission_digest="sha256:"+"f"*64)):
            self.records = deepcopy(original)
            self.records[1]["registration"].update(change)
            with self.subTest(change=change), self.assertRaises(ReviewResultInputError):
                self.register()
        self.records = original
        self.admitted.groups = [(self.service, object(), self.submitted.items[:1])]
        with self.assertRaises(ReviewResultInputError):
            self.register()
        self.writer.assert_not_called()
        self.assertEqual(self.count(), 0)

    def test_request_requires_exact_typed_finalization_basis_selectors_and_collected_protocol(self):
        from task_governance_tool.review_answer_transport import LegacyItem
        for request, basis in ((False, True), (self.request, None),
                (replace(self.request, submission_digest="sha256:"+"f"*64), True),
                (replace(self.request, preparation_id="tg_review_preparation_"+"f"*16), True)):
            with self.subTest(request=request, basis=basis), self.assertRaises(ReviewResultInputError):
                self.register(request=request, basis=basis)
        self.submitted = replace(self.submitted, items=(LegacyItem(1, b"", "", {}, None),))
        with self.assertRaises(ReviewResultInputError):
            self.register()
        self.writer.assert_not_called()

    def test_stale_retained_basis_refuses_without_changing_source_intent(self):
        for basis in (replace(self.basis, contract_revision=self.basis.contract_revision+1),
                replace(self.basis, ownership_generation=self.basis.ownership_generation+1),
                replace(self.basis, project_binding_generation=self.basis.project_binding_generation+1)):
            with self.subTest(basis=basis), self.assertRaises(Exception):
                self.register(basis=basis)
        self.assertEqual(self.count(), 0)
        self.assertEqual([row["registration"]["state"] for row in self.records], ["dispatched"]*2)

    def test_confirmation_failure_keeps_committed_success_and_prevents_another_core_write(self):
        self.service.confirm_registration_locked.side_effect = OSError("SENTINEL lost confirmation")
        result, warnings = self.register()
        self.assertEqual(len(result["receipts"]), 2)
        self.assertEqual([row["code"] for row in warnings], ["collection_registration_acknowledgement_unavailable"])
        self.assertNotIn("SENTINEL", str(warnings))
        self.assertEqual(self.count(), 2)
        with self.assertRaises(ReviewResultInputError):
            self.register()
        self.assertEqual(self.count(), 2)
        self.assertEqual([row["registration"]["state"] for row in self.records], ["dispatched"]*2)

    @staticmethod
    def remove_guards(connection):
        rows = connection.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND tbl_name IN ('review_collection_dispatches','review_collection_dispatch_entries','review_collection_sources')").fetchall()
        for row in rows:
            connection.execute('DROP TRIGGER "'+row["name"].replace('"','""')+'"')
        return [row["sql"] for row in rows]

    def test_exact_and_source_only_residue_never_becomes_existing_receipt_adoption(self):
        self.register()
        from task_governance_tool.review_answer_repository import require_absent_registration_locked
        changes = ((), ("DELETE FROM review_collection_dispatches",),
            ("DELETE FROM review_collection_dispatches", "DELETE FROM review_collection_dispatch_entries"),
            ("DELETE FROM review_collection_dispatches", "DELETE FROM review_collection_dispatch_entries",
             "UPDATE review_collection_sources SET submission_digest='sha256:"+"f"*64+"'"))
        with closing(storage.connect(self.fixture.db)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            for statements in changes:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self.remove_guards(connection)
                    for statement in statements:
                        connection.execute(statement)
                    with self.subTest(statements=statements), self.assertRaises(ReviewResultInputError):
                        require_absent_registration_locked(connection, self.submitted)
                finally:
                    connection.rollback()
        self.assertEqual(self.count(), 2)

    def test_receipt_without_source_is_rejected_by_fresh_inside_writer_native_admission(self):
        self.register()
        with closing(storage.connect_initialized(self.fixture.target)) as prepared:
            with closing(storage.connect(self.fixture.db)) as corrupt:
                corrupt.execute("PRAGMA foreign_keys=OFF")
                with corrupt:
                    statements = self.remove_guards(corrupt)
                    for table in ("review_collection_dispatch_entries", "review_collection_sources", "review_collection_dispatches"):
                        corrupt.execute("DELETE FROM "+table)
                    for statement in statements:
                        corrupt.execute(statement)
            with self.assertRaises(storage.StorageError):
                storage.begin_initialized_write(prepared, self.fixture.target)
            self.assertFalse(prepared.in_transaction)

    def test_native_absence_does_not_reject_same_reviewer_and_answer_from_another_turn(self):
        self.register()
        from task_governance_tool.review_answer_repository import require_absent_registration_locked
        items = []
        for item in self.submitted.items:
            metadata = deepcopy(item.metadata)
            turn = "20000000-0000-4000-8000-000000000002"
            metadata["binding"]["initial_turn_id"] = turn
            metadata["source_identity"].update(turn_id=turn, message_id="later-"+str(item.slot))
            items.append(replace(item, metadata=metadata))
        later = decode_collected_submission(frame_collected_submission(self.submitted.preparation_id, items))
        with closing(storage.connect_initialized(self.fixture.target)) as connection:
            storage.begin_initialized_write(connection, self.fixture.target)
            require_absent_registration_locked(connection, later)
            connection.rollback()
        self.assertEqual(self.count(), 2)

    def test_registered_consumer_and_published_selected_or_captured_links_are_residue(self):
        from task_governance_tool.review_answer_repository import require_absent_registration_locked, read_native_collected_captures
        from task_governance_tool import review_continuation as values
        from task_governance_tool.review_answer_values import basis_digest
        source, metadata, answer = self.fixture.collected_source_continuation()
        consumer = self.fixture.retain_collected_source(source, metadata)
        current, current_answer = self.fixture.metadata(2)
        context = dict(continuation_id=consumer.continuation_id, sequence=consumer.sequence,
            selection_digest=values.selection_digest(consumer.selected))
        current["basis"]["continuation"] = context
        current["binding"].update(slot=1, basis_digest=basis_digest(current["basis"]))
        with closing(storage.connect_initialized_readonly(self.fixture.target)) as connection:
            captures = tuple(capture for capture, _ in read_native_collected_captures(connection, consumer.continuation_id))
        submitted = decode_collected_submission(frame_collected_submission(metadata["basis"]["preparation_id"],
            (CollectedItem(1, current_answer.raw, current, current_answer), CollectedItem(2, answer.raw, metadata, answer)),
            continuation=context, retained_originals=captures))
        changes = (("UPDATE review_continuations SET state='registered',registered_at='2026-10-16T00:00:00Z' WHERE continuation_id=?", (consumer.continuation_id,)),
            ("UPDATE review_collected_captures SET result_receipt_id=? WHERE continuation_id=?", ("tg_review_receipt_"+"a"*16, consumer.continuation_id)),
            ("UPDATE review_continuation_reviewers SET result_receipt_id=?,original_result_digest=? WHERE continuation_id=?",
             ("tg_review_receipt_"+"a"*16, "b"*64, consumer.continuation_id)))
        with closing(storage.connect(self.fixture.db)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            for statement, arguments in changes:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    require_absent_registration_locked(connection, submitted)
                    guards = connection.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name IN ('review_continuations','review_collected_captures','review_continuation_reviewers')").fetchall()
                    for row in guards:
                        connection.execute('DROP TRIGGER "'+row["name"].replace('"','""')+'"')
                    connection.execute(statement, arguments)
                    with self.subTest(statement=statement), self.assertRaises(ReviewResultInputError):
                        require_absent_registration_locked(connection, submitted)
                finally:
                    connection.rollback()
