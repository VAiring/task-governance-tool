"""Atomic native checks for the late-initial transport admission."""
import copy
from contextlib import closing
from hashlib import sha256
import json
import unittest
from unittest import mock

from tests.review_answer_fixture_support import review_fixture
from tests.verification_receipt_test_support import set_target
from task_governance_tool import storage, review_continuation as cv
from task_governance_tool import review_continuation_repository as cr
from task_governance_tool import review_answer_values as av
from task_governance_tool import review_collected_results as results
from task_governance_tool import review_session_transport as legacy
from task_governance_tool.review_answer_repository import read_continuation_dispatch
from task_governance_tool.review_answer_transport import (
    CollectedItem, LegacyItem, InitialOriginal, frame_collected_submission,
    decode_collected_submission,
)
from task_governance_tool.task_values import TaskValidationError
from task_governance_tool.reviews import ReviewEvidenceError


class LateInitialCoreTests(unittest.TestCase):
    def setUp(self):
        self.fx = self.enterContext(review_fixture(schema_version=31))

    def prepare(self, actor, *, previous=None):
        metadata, _ = self.fx.metadata()
        with closing(storage.connect_initialized(self.fx.target)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            record = cr.admit_locked(connection, basis=self.fx.basis,
                preparation_id=metadata["basis"]["preparation_id"], route="manual",
                selected=(cv.SelectedReviewer(actor, "supplement", None, "Complete assigned review"),),
                caller=self.fx.caller,
                retirement=cv.RetirementProof(previous.continuation_id,
                    cv.basis_digest(self.fx.basis), None, None) if previous else None)
            return cr.advance_prepared_locked(connection, record, self.fx.caller)

    def batch(self, *, previous=None, legacy_initial=False):
        metadata, answer = self.fx.metadata()
        record = self.prepare(metadata["source_identity"]["reviewer_session_id"], previous=previous)
        context = dict(continuation_id=record.continuation_id, sequence=record.sequence,
                       selection_digest=cv.selection_digest(record.selected))
        metadata["basis"]["continuation"] = context
        metadata["binding"]["basis_digest"] = av.basis_digest(metadata["basis"])
        selected = CollectedItem(1, answer.raw, metadata, answer)
        late_meta, late_answer = self.fx.metadata(2)
        if legacy_initial:
            actor = late_meta["source_identity"]["reviewer_session_id"]
            raw, sidecar = self.fx.original(actor)
            late = LegacyItem(2, raw, "sha256:" + sha256(
                json.dumps(self.fx.packet).encode()).hexdigest(), sidecar,
                legacy.validate_metadata(sidecar, raw))
            reference = InitialOriginal(actor, "sha256:" + late.session_binding.original_result_digest,
                                        "sha256:" + legacy.metadata_digest(sidecar))
        else:
            late = CollectedItem(2, late_answer.raw, late_meta, late_answer)
            reference = InitialOriginal(late.session_binding.session_id,
                late_meta["original"]["digest"], av.metadata_digest(late_meta))
        raw = frame_collected_submission(record.preparation_id, (selected, late),
                                        continuation=context, retained_originals=(reference,))
        return record, decode_collected_submission(raw)

    def register(self, batch):
        with closing(storage.connect_initialized(self.fx.target)) as connection, connection:
            return results.add_collected_review_results(connection, self.fx.target.project,
                self.fx.task_id, batch, caller=self.fx.caller, database_target=self.fx.target)

    def snapshot(self):
        with closing(storage.connect_initialized(self.fx.target)) as connection:
            tables = ("review_receipts", "review_findings", "review_collection_sources",
                      "review_collection_dispatches", "review_collection_dispatch_entries")
            return {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                    for table in tables}

    def test_dispatch_failure_rolls_back_all_receipts_sources_and_consumer_state(self):
        record, batch = self.batch()
        before = self.snapshot()
        with mock.patch.object(results, "record_dispatch_locked", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.register(batch)
        self.assertEqual(self.snapshot(), before)
        with closing(storage.connect_initialized(self.fx.target)) as connection:
            current = cr.read_continuations(connection, project_id=self.fx.basis["project_id"],
                                           task_id=self.fx.task_id)
            self.assertEqual(current, (record,))
        registered = self.register(batch)
        self.assertIsNone(registered["review_gate"]["blocking_code"])
        again = self.register(batch)
        self.assertEqual([r["receipt"]["review_receipt_id"] for r in again["receipts"]],
                         [r["receipt"]["review_receipt_id"] for r in registered["receipts"]])

    def test_initial_legacy_source_registers_without_synthetic_capture(self):
        record, batch = self.batch(legacy_initial=True)
        registered = self.register(batch)
        self.assertIsNone(registered["review_gate"]["blocking_code"])
        with closing(storage.connect_initialized(self.fx.target)) as connection:
            current = cr.read_continuations(connection, project_id=self.fx.basis["project_id"],
                                           task_id=self.fx.task_id)[-1]
            dispatch = read_continuation_dispatch(connection, current)
            self.assertEqual([r["protocol"] for r in dispatch["entries"]],
                             ["review-answer-v1", "legacy-review-result-v1"])
            self.assertEqual(cr.read_retained_originals(connection, record.continuation_id), ())
            nodes = cr.read_judgments(connection, basis=self.fx.basis)
            initial = next(row for row in nodes if row.session_id == batch.items[1].session_binding.session_id)
            self.assertIsNone(initial.continuation_id)

    def test_previously_selected_actor_cannot_be_reintroduced_as_initial(self):
        old_meta, _ = self.fx.metadata(2)
        old = self.prepare(old_meta["source_identity"]["reviewer_session_id"])
        _, batch = self.batch(previous=old)
        before = self.snapshot()
        with self.assertRaises(TaskValidationError):
            self.register(batch)
        self.assertEqual(self.snapshot(), before)

    def test_new_continuation_invalidates_prepared_late_batch(self):
        record, batch = self.batch()
        self.prepare(batch.items[0].session_binding.session_id, previous=record)
        before = self.snapshot()
        with self.assertRaises(TaskValidationError):
            self.register(batch)
        self.assertEqual(self.snapshot(), before)

    def test_recomputed_selection_and_changed_target_fail_without_effects(self):
        _, batch = self.batch()
        value = json.loads(batch.raw)
        context = value["continuation"]
        context["selection_digest"] = "f" * 64
        metadata = value["items"][0]["metadata"]
        metadata["basis"]["continuation"] = copy.deepcopy(context)
        metadata["binding"]["basis_digest"] = av.basis_digest(metadata["basis"])
        forged = decode_collected_submission(json.dumps(value, separators=(",", ":")).encode())
        before = self.snapshot()
        with self.assertRaises(TaskValidationError):
            self.register(forged)
        self.assertEqual(self.snapshot(), before)
        set_target(self.fx.db, self.fx.repo, self.fx.task_id, fingerprint="sha256:" + "e" * 64)
        with self.assertRaises(ReviewEvidenceError) as rejected:
            self.register(batch)
        self.assertEqual(rejected.exception.code, "review_target_mismatch")
        self.assertEqual(self.snapshot(), before)
