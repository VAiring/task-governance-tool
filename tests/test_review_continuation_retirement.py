"""Native settled supersession, exact retained v2 originals and atomic publication."""

from contextlib import closing
from dataclasses import replace
import unittest

from tests import test_review_continuation_registration as fixture
from tests.test_review_continuation import A, B, ident
from task_governance_tool import storage, review_results, review_session_transport as transport
from task_governance_tool import review_continuation as values, review_continuation_repository as repository
from task_governance_tool import schema_review_continuation as schema
from task_governance_tool import review_continuation_evidence as frozen
from tests.verification_receipt_test_support import run_taskgov, completion, payload
from tests.test_m23s_schema22_validation import _bundle_artifacts


class ContinuationRetirementTests(unittest.TestCase):
    setUp = fixture.ContinuationRegistrationTests.setUp
    prepare = fixture.ContinuationRegistrationTests.prepare
    original = fixture.ContinuationRegistrationTests.original
    evidence = fixture.ContinuationRegistrationTests.evidence
    freeze = fixture.ContinuationRegistrationTests.freeze

    def initial(self):
        return self.prepare(selected=(values.SelectedReviewer(A, "supplement", None, "Missing A"),
            values.SelectedReviewer(B, "supplement", None, "Missing B")))

    def retire(self, old, selected, captures=(), *, proof=None):
        proof = proof or values.RetirementProof(old.continuation_id, values.basis_digest(self.basis), None, None)
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            record = repository.admit_locked(connection, basis=self.basis, preparation_id=old.preparation_id, route="manual",
                selected=selected, caller=self.caller, retirement=proof, retained_originals=captures)
            return repository.advance_prepared_locked(connection, record, self.caller)

    @staticmethod
    def capture(record, original):
        raw, metadata = original
        return values.RetainedOriginal(record.continuation_id, metadata["session_id"], metadata["original_result_digest"], transport.metadata_digest(metadata))

    def register(self, originals, record, captures=()):
        identity = transport.ContinuationIdentity(record.preparation_id, record.continuation_id, record.sequence)
        decoded = transport.decode_submission(transport.frame_submission([row[0] for row in originals], [row[1] for row in originals],
            continuation=identity, retained_originals=captures))
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            return review_results.add_review_results(connection, self.target.project, self.task_id, decoded.document,
                database_target=self.target, caller=self.caller, session_bindings=decoded.bindings,
                continuation_identity=decoded.continuation, retained_originals=decoded.retained_originals)

    def test_unjudged_round_can_be_superseded_without_inventing_results(self):
        old = self.initial()
        current = self.retire(old, old.selected)
        self.assertEqual(current.sequence, 2)
        self.assertEqual(current.predecessor_id, old.continuation_id)
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            history = repository.read_continuations(connection, project_id=self.basis["project_id"], task_id=self.task_id)
            self.assertEqual([row.state for row in history], ["superseded", "prepared"])
            self.assertTrue(history[0].retirement_digest)
            self.assertEqual(history[0].results, ())
        self.assertFalse(self.evidence()["gate"]["satisfied"])

    def test_missing_slot_or_wrong_retirement_proof_rolls_back(self):
        old = self.initial()
        before = None
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            before = storage._selected_table_projection_snapshot(connection, ("review_continuations", "review_continuation_reviewers"))
        for selected, proof in ((old.selected[:1], None), (old.selected, values.RetirementProof(old.continuation_id, "f" * 64, None, None))):
            with self.subTest(selected=selected), self.assertRaises(Exception) as caught:
                self.retire(old, selected, proof=proof)
            self.assertEqual(caught.exception.code, "review_continuation_conflict")
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tuple(before)), before)

    def test_retained_original_keeps_old_context_and_registers_with_new_required_result(self):
        old = self.initial()
        saved = self.original(A, continued=old)
        capture = self.capture(old, saved)
        current = self.retire(old, (old.selected[1],), (capture,))
        new = self.original(B, continued=current)
        outcome = self.register([saved, new], current, (capture,))
        self.assertTrue(outcome["review_gate"]["satisfied"])
        self.assertEqual(saved[1]["continuation_id"], old.continuation_id)
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            schema.validate_storage(connection)
            history = repository.read_continuations(connection, project_id=self.basis["project_id"], task_id=self.task_id)
            self.assertEqual([row.state for row in history], ["superseded", "registered"])
            self.assertEqual(history[0].results[0][0], A)
            self.assertEqual(history[1].results[0][0], B)
            self.assertIsNotNone(repository.read_retained_originals(connection, current.continuation_id)[0]["result_receipt_id"])

    def test_captured_ng_is_registered_and_remains_blocking(self):
        old = self.initial()
        saved = self.original(A, continued=old, verdict="changes_requested", findings=[dict(severity="medium", summary="Retained issue")])
        capture = self.capture(old, saved)
        current = self.retire(old, (old.selected[1],), (capture,))
        outcome = self.register([saved, self.original(B, continued=current)], current, (capture,))
        self.assertFalse(outcome["review_gate"]["satisfied"])
        self.assertEqual(self.evidence()["counts"]["changes_requested_current_generation"], 1)
        self.assertEqual(self.evidence()["counts"]["open_medium"], 1)

    def test_retained_actor_cannot_be_selected_again_or_reformatted(self):
        old = self.initial()
        saved = self.original(A, continued=old)
        capture = self.capture(old, saved)
        with self.assertRaises(Exception):
            self.retire(old, old.selected, (capture,))
        current = self.retire(old, (old.selected[1],), (capture,))
        wrong = replace(capture, original_result_digest="d" * 64)
        with self.assertRaises(Exception):
            self.register([saved, self.original(B, continued=current)], current, (wrong,))
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 0)

    def test_captured_unregistered_original_survives_another_failed_round(self):
        old = self.initial()
        saved = self.original(A, continued=old)
        capture = self.capture(old, saved)
        second = self.retire(old, (old.selected[1],), (capture,))
        with self.assertRaises(Exception):
            self.retire(second, second.selected)
        third = self.retire(second, second.selected, (capture,))
        outcome = self.register([saved, self.original(B, continued=third)], third, (capture,))
        self.assertTrue(outcome["review_gate"]["satisfied"])
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            self.assertIsNone(repository.read_retained_originals(connection, second.continuation_id)[0]["result_receipt_id"])
            self.assertIsNotNone(repository.read_retained_originals(connection, third.continuation_id)[0]["result_receipt_id"])

    def test_frozen_retention_graph_rejects_digest_and_consumption_substitution(self):
        import copy
        old = self.initial()
        saved = self.original(A, continued=old)
        capture = self.capture(old, saved)
        current = self.retire(old, (old.selected[1],), (capture,))
        self.register([saved, self.original(B, continued=current)], current, (capture,))
        graph = self.freeze()
        frozen.validate_graph(graph)
        self.assertEqual(len(graph["retained_originals"]), 1)
        for changes in (dict(metadata_digest="d" * 64), dict(result_receipt_id=None), dict(prior_continuation_id=current.continuation_id)):
            wrong = copy.deepcopy(graph)
            wrong["retained_originals"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(Exception):
                frozen.validate_graph(wrong)

    def test_native_seal_and_independent_reader_keep_superseded_and_consumed_original(self):
        old = self.initial()
        saved = self.original(A, continued=old)
        capture = self.capture(old, saved)
        current = self.retire(old, (old.selected[1],), (capture,))
        self.register([saved, self.original(B, continued=current)], current, (capture,))
        recorded = run_taskgov("verification", "receipt", "add", self.task_id,
            "--result", "pass", "--duration-ms", "1", "--scope-coverage", "full",
            "--expected-target-generation", str(self.basis["target_generation"]),
            "--repo", str(self.repo), "--db", str(self.db), "--json")
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        result = completion(self.db, self.repo, self.task_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            schema.validate_storage(connection)
            bundle = connection.execute("SELECT * FROM completion_evidence_bundles WHERE task_id=?", (self.task_id,)).fetchone()
            _, artifacts = _bundle_artifacts(connection, self.basis["project_id"])
            artifact = artifacts[bundle["completion_evidence_bundle_id"]]
            from tests.evidence_reader_oracle import ValidatedEvidenceSource
            entry = dict(task_id=self.task_id, completion_cycle_id=bundle["completion_cycle_id"], cycle_ordinal=bundle["cycle_ordinal"],
                bundle_state="native", bundle_id=bundle["completion_evidence_bundle_id"],
                bundle_file="bundles/" + bundle["completion_evidence_bundle_id"] + ".json", bundle_digest=artifact.bundle_digest,
                file_digest=artifact.file_digest, sealed_at=bundle["sealed_at"], bundle_format_version=3)
            source = ValidatedEvidenceSource("native_bundle", dict(index_format_version=2, source_schema_version=29,
                project_id=self.basis["project_id"], projection_generation=1, index_digest="sha256:" + "0" * 64, entry=entry), artifact.envelope)
            graph = source.source["payload"]["review_continuation_basis"]
            self.assertEqual(graph["retained_originals"][0]["prior_continuation_id"], old.continuation_id)
            self.assertTrue(graph["retained_originals"][0]["result_receipt_id"])


if __name__ == "__main__":
    unittest.main()
