"""Dedicated reviewer tracking freezes a valid result, never a requested verdict."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.review_answer import AnswerError, decode_answer
from task_governance_tool.review_answer_collection import CollectionService, CollectionInvalidated, source
from task_governance_tool.review_answer_collection_repository import CollectionRepository
from task_governance_tool import review_answer_values as values
from task_governance_tool import review_handoff_transport as files
from task_governance_tool.review_answer_provenance import build_provenance_v2, validate_provenance_v2
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from tests.test_review_answer_pure_values import metadata_fixture, TURN1, TURN2, ident
from tests.test_review_answer_pure_provenance import context


def tracking_fixture(*, continuation=False):
    metadata, answer = metadata_fixture()
    metadata["basis"]["version"] = 2
    if continuation:
        metadata["basis"]["continuation"] = dict(continuation_id=ident("tg_review_continuation_"),
            sequence=1, selection_digest="a" * 64)
    binding = metadata["binding"]
    dispatch = binding.pop("dispatch_id")
    boundary = binding.pop("initial_turn_id")
    binding["basis_digest"] = values.basis_digest(metadata["basis"])
    binding["binding_source"] = source("reviewer_tracking", 2)
    binding["tracking"] = dict(kind="continuation" if continuation else "initial",
        boundary="exclusive" if continuation else "inclusive", boundary_turn_id=boundary,
        dispatch_id=None if continuation else dispatch)
    if continuation:
        metadata["source_identity"]["turn_id"] = TURN2
    metadata["answer_source"] = source("answer_observation", 2)
    metadata["terminal_source"] = source("terminal_observation", 2)
    return values.validate_metadata(metadata, answer.raw), answer


class TrackingCollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-tracking-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".tmp-review").mkdir()
        self.metadata, self.answer = tracking_fixture()
        self.repository = CollectionRepository(self.root / "collection.sqlite")
        self.repository.initialize((self.metadata["basis"],))
        self.admit = Mock()
        self.service = CollectionService(self.root, self.repository,
            original_paths=(self.metadata["original"]["path"],), admit_basis=self.admit)
        self.service.bind(1, self.metadata["binding"])
        ignore = patch.object(files, "require_ignored")
        ignore.start()
        self.addCleanup(ignore.stop)

    def observed(self, *, raw=None, turn=TURN1, verdict="pass", terminal="completed"):
        identity = {**self.metadata["source_identity"], "turn_id": turn}
        payload = {**self.answer.payload, "verdict": verdict}
        return dict(source_identity=identity,
            answer_bytes=json.dumps(payload).encode() if raw is None else raw,
            complete=True, terminal=terminal, answer_source=source("answer_observation", 2),
            terminal_source=source("terminal_observation", 2))

    def test_arm_has_no_invented_answer_turn_and_rebinding_cannot_move_frontier(self):
        row = self.repository.read()[0]
        self.assertEqual(row["version"], 2)
        self.assertEqual(row["tracking_state"], "watching")
        self.assertIsNone(row["attempts"][0]["turn_id"])
        self.assertEqual(self.service.bind(1, self.metadata["binding"]), row)
        changed = copy.deepcopy(self.metadata["binding"])
        changed["tracking"]["boundary_turn_id"] = TURN2
        with self.assertRaises(AnswerError):
            self.service.bind(1, changed)

    def test_invalid_answer_then_new_valid_answer_freezes_and_stops_host_reads(self):
        host = Mock()
        host.observe_latest_answer.side_effect = [self.observed(raw=b"not a review"), self.observed(turn=TURN2)]
        with self.assertRaises(AnswerError):
            self.service.recover(1, host)
        rejected = self.repository.read()[0]
        self.assertEqual(rejected["tracking_state"], "watching")
        self.assertIsNone(rejected["attempts"][0]["observed_digest"])
        self.assertIsNone(rejected["attempts"][0]["publication"])
        raw, metadata = self.service.recover(1, host)
        self.assertEqual(metadata["source_identity"]["turn_id"], TURN2)
        saved = self.repository.read()
        host.observe_latest_answer.side_effect = AssertionError("accepted source must not be polled")
        self.assertEqual(self.service.recover(1, host), (raw, metadata))
        self.service.observe(1, self.observed(verdict="changes_requested"))
        self.assertEqual(self.repository.read(), saved)
        self.assertNotIn(b"not a review", self.repository.path.read_bytes())

    def assert_negative_freezes(self, verdict):
        raw = self.observed(verdict=verdict)["answer_bytes"]
        self.service.observe(1, self.observed(raw=raw))
        self.assertEqual(self.repository.read()[0]["tracking_state"], "frozen")
        self.service.observe(1, self.observed(turn=TURN2))
        self.assertEqual(decode_answer(self.service.readback(1)[0]).payload["verdict"], verdict)

    def test_changes_requested_is_a_frozen_result(self):
        self.assert_negative_freezes("changes_requested")

    def test_unable_is_a_frozen_result(self):
        self.assert_negative_freezes("unable_to_review")

    def test_valid_pending_result_does_not_freeze_until_completed(self):
        with self.assertRaises(AnswerError):
            self.service.observe(1, self.observed(terminal="in_progress"))
        self.assertEqual(self.repository.read()[0]["tracking_state"], "watching")
        self.service.observe(1, self.observed())
        self.assertEqual(self.repository.read()[0]["tracking_state"], "frozen")

    def test_source_and_publication_intent_freeze_together_before_file_effect(self):
        def interrupted(*args):
            row = self.repository.read()[0]
            self.assertEqual(row["tracking_state"], "frozen")
            self.assertEqual(row["attempts"][0]["publication"]["state"], "intent")
            raise KeyboardInterrupt
        with patch.object(files, "write_exclusive", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.service.observe(1, self.observed())
        host = Mock()
        with self.assertRaises(AnswerError) as caught:
            self.service.recover(1, host)
        self.assertEqual(caught.exception.failure["class"], "save_unknown")
        host.observe_latest_answer.assert_not_called()
        self.service.observe(1, self.observed(turn=TURN2))
        self.assertEqual(self.repository.read()[0]["attempts"][0]["turn_id"], TURN1)

    def test_concurrent_first_acceptance_wins_without_replacing_original(self):
        host = Mock()
        winning = self.observed(turn=TURN2, verdict="changes_requested")
        def observe(**kwargs):
            self.service.observe(1, winning)
            return self.observed()
        host.observe_latest_answer.side_effect = observe
        raw, metadata = self.service.recover(1, host)
        self.assertEqual(metadata["source_identity"]["turn_id"], TURN2)
        self.assertEqual(decode_answer(raw).payload["verdict"], "changes_requested")

    def test_invalidated_arm_cannot_read_bind_or_reset(self):
        self.service.invalidate(1)
        host = Mock()
        with self.assertRaises(AnswerError):
            self.service.recover(1, host)
        host.observe_latest_answer.assert_not_called()
        with self.assertRaises(AnswerError):
            self.service.bind(1, self.metadata["binding"])
        with self.repository.serial() as lease:
            def reset(row):
                row["tracking_state"] = "watching"
                return row
            with self.assertRaises(RepositoryError):
                self.repository.update(lease, 1, reset)

    def test_transient_transport_error_does_not_invalidate(self):
        host = Mock()
        host.observe_latest_answer.side_effect = RuntimeError("transport")
        with self.assertRaises(AnswerError):
            self.service.recover(1, host)
        self.assertEqual(self.repository.read()[0]["tracking_state"], "watching")
        host.observe_latest_answer.side_effect = None
        host.observe_latest_answer.return_value = self.observed()
        self.service.recover(1, host)
        self.assertEqual(self.repository.read()[0]["tracking_state"], "frozen")

    def test_proven_material_mismatch_closes_arm_even_after_material_is_restored(self):
        self.admit.side_effect = CollectionInvalidated()
        host = Mock()
        with self.assertRaises(CollectionInvalidated):
            self.service.recover(1, host)
        self.assertEqual(self.repository.read()[0]["tracking_state"], "invalidated")
        self.admit.side_effect = None
        with self.assertRaises(CollectionInvalidated):
            self.service.recover(1, host)
        host.observe_latest_answer.assert_not_called()

    def test_proven_material_change_after_publication_latches_under_existing_lease(self):
        write = files.write_exclusive
        def changed_after_write(*args):
            write(*args)
            self.admit.side_effect = CollectionInvalidated()
        with patch.object(files, "write_exclusive", side_effect=changed_after_write):
            with self.assertRaises(CollectionInvalidated):
                self.service.observe(1, self.observed())
        row = self.repository.read()[0]
        self.assertEqual(row["tracking_state"], "invalidated")
        self.assertEqual(row["attempts"][0]["turn_id"], TURN1)
        self.assertEqual(row["attempts"][0]["publication"]["state"], "intent")
        self.admit.side_effect = None
        with self.assertRaises(CollectionInvalidated):
            self.service.readback(1)

    def test_unproven_admission_failure_does_not_close_arm(self):
        self.admit.side_effect = AnswerError("binding_mismatch", reason="identity")
        with self.assertRaises(AnswerError):
            self.service.recover(1, Mock())
        self.assertEqual(self.repository.read()[0]["tracking_state"], "watching")

    def test_read_only_material_rejection_does_not_write_invalidation(self):
        self.service.observe(1, self.observed())
        before = self.repository.read()
        self.admit.side_effect = CollectionInvalidated()
        with self.assertRaises(CollectionInvalidated):
            self.service.readback(1)
        self.assertEqual(self.repository.read(), before)
        with self.assertRaises(CollectionInvalidated):
            with self.service.admission():
                self.fail("obsolete source admitted")
        self.assertEqual(self.repository.read(), before)
        self.admit.side_effect = None
        self.assertEqual(self.service.readback(1)[0], self.answer.raw)

    def test_provenance_explicitly_names_tracking_adapter_without_false_dispatch(self):
        self.service.observe(1, self.observed())
        raw, metadata = self.service.readback(1)
        kwargs = context(metadata)
        provenance = build_provenance_v2(metadata=metadata, answer=decode_answer(raw), **kwargs)
        self.assertEqual(provenance["producer_version"], 1)
        self.assertEqual(provenance["host_observation"]["binding_source"], source("reviewer_tracking", 2))
        self.assertEqual(validate_provenance_v2(provenance, **kwargs), provenance)


class TrackingValueTests(unittest.TestCase):
    def test_continuation_excludes_preserved_terminal_frontier(self):
        metadata, answer = tracking_fixture(continuation=True)
        self.assertIsNone(metadata["binding"]["tracking"]["dispatch_id"])
        metadata["source_identity"]["turn_id"] = TURN1
        with self.assertRaises(AnswerError):
            values.validate_metadata(metadata, answer.raw)

    def test_version_one_cannot_silently_gain_tracking_semantics(self):
        metadata, answer = tracking_fixture()
        metadata["basis"]["version"] = 1
        with self.assertRaises(AnswerError):
            values.validate_metadata(metadata, answer.raw)

    def test_independent_reader_admits_tracking_and_rejects_false_dispatch(self):
        from tests import evidence_reader_oracle as reader
        from tests.review_continuation_evidence_oracle import _CollectedReader
        from task_governance_tool import review_answer_provenance as producer
        metadata, answer = tracking_fixture(continuation=True)
        kwargs = context(metadata)
        provenance = build_provenance_v2(metadata=metadata, answer=answer, **kwargs)
        receipt = dict(review_receipt_id=kwargs["review_receipt_id"], receipt_kind="independent",
            verdict=answer.payload["verdict"], summary=answer.payload["summary"].strip())
        public = values.source_metadata_projection(metadata)
        with patch.object(producer, "validate_provenance_v2", side_effect=AssertionError("product validator")), \
                patch.object(values, "validate_public_metadata", side_effect=AssertionError("product validator")):
            self.assertEqual(_CollectedReader(reader).metadata(public), public)
            self.assertEqual(reader._validate_review_provenance(provenance,
                project_id=kwargs["project_id"], task_id=kwargs["task_id"], target=kwargs["target"],
                receipt=receipt, allow_v2=True), provenance)
            changed = copy.deepcopy(public)
            changed["binding"]["tracking"]["dispatch_id"] = "invented-dispatch"
            with self.assertRaises(reader.EvidenceConsumerError):
                _CollectedReader(reader).metadata(changed)


if __name__ == "__main__":
    unittest.main()
