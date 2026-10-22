"""Candidate schema-29 preparation with native core authority/ownership reads."""

from contextlib import closing
from contextlib import contextmanager, redirect_stdout
import io
import json
import sys
import unittest
import threading
from unittest import mock

from tests.review_answer_fixture_support import ReviewFixture, review_fixture
from tests.review_handoff_fixture_support import physical_handoff, publish_handoff
from task_governance_tool import review_preparation as preparation, storage
from task_governance_tool.review_preparation_repository import PreparationRepository
from task_governance_tool.review_wait_basis_repository import read_preparation_basis
from task_governance_tool.review_handoff_transport import HandoffError
from task_governance_tool.session_identity import CallerIdentity
from tests.test_review_continuation import A
from tests.test_review_continuation import B, ident
from task_governance_tool import review_handoff as files, review_continuation as values
from task_governance_tool import review_continuation_repository as continuations
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from tests.test_review_results import BinaryInput


class PreparationPublicationTests(unittest.TestCase):
    def setUp(self):
        fixture = self.enterContext(review_fixture(schema_version=29, contract=None))
        self.repo, self.db, self.task_id = fixture.repo, fixture.db, fixture.task_id
        self.target, self.basis = fixture.target, fixture.basis
        self.caller, self.packet = fixture.caller, fixture.packet

    def physical(self):
        physical_handoff(self, enter_context=self.enterContext)

    publish = publish_handoff

    def test_native_full_basis_is_read_only_and_actual_owner_required(self):
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            before = connection.total_changes
            self.assertEqual(read_preparation_basis(connection, project_id=self.basis["project_id"], task_id=self.task_id, caller=self.caller), self.basis)
            self.assertEqual(connection.total_changes, before)
            with self.assertRaises(Exception) as caught:
                read_preparation_basis(connection, project_id=self.basis["project_id"], task_id=self.task_id, caller=CallerIdentity(A))
            self.assertEqual(caught.exception.code, "task_ownership_changed")

    def test_ready_manual_publication_does_not_create_or_adopt_finalization_intent(self):
        self.physical()
        # An unrelated or unreadable finalization path cannot promote the route.
        (self.root / "finalization.sqlite").write_bytes(b"unrelated")
        result = self.publish()
        rows, current = PreparationRepository(self.root / "preparation.sqlite").read()
        self.assertEqual(result, dict(preparation_id=current.preparation_id, route="manual"))
        self.assertEqual(current.basis, self.basis)
        self.assertIsNone(current.finalization_store)
        self.assertEqual((self.root / "finalization.sqlite").read_bytes(), b"unrelated")

    def test_existing_result_or_changed_packet_basis_does_not_publish(self):
        self.physical()
        (self.repo / "reviews/a.json").write_bytes(b"original")
        with self.assertRaises(HandoffError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, "handoff_destination_exists")
        self.assertFalse((self.root / "preparation.sqlite").exists())

    def test_core_change_under_admission_does_not_replace_ready_pointer(self):
        self.physical()
        first = self.publish()
        journal = PreparationRepository(self.root / "preparation.sqlite")
        before = journal.read()
        with mock.patch.object(preparation, "_basis", return_value={**self.basis, "contract_revision": 999}):
            with self.assertRaises(HandoffError) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, "review_continuation_basis_changed")
        self.assertEqual(journal.read(), before)
        self.assertEqual(journal.read()[1].preparation_id, first["preparation_id"])

    def pending(self, actor):
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            return continuations.admit_locked(connection, basis=self.basis,
                preparation_id=PreparationRepository(self.root / "preparation.sqlite").read()[1].preparation_id,
                route="manual", selected=(values.SelectedReviewer(actor, "supplement", None, "Missing material"),), caller=self.caller)

    def test_obsolete_selected_initial_save_fails_before_exclusive_creation(self):
        self.physical()
        self.publish()
        self.pending(A)
        raw = ReviewFixture.original(self, A)[0]
        with mock.patch("task_governance_tool.session_identity.capture_caller_identity", return_value=CallerIdentity(A)):
            with self.assertRaises(HandoffError) as caught:
                files.save(self.repo, "reviews/packet.json", "reviews/a.json", raw)
        self.assertEqual(caught.exception.code, "review_packet_stale")
        self.assertFalse((self.repo / "reviews/a.json").exists())
        self.assertFalse((self.repo / "reviews/a.json.session.json").exists())

    def test_unselected_initial_original_can_finish_under_unchanged_basis(self):
        self.physical()
        self.publish()
        self.pending(B)
        raw = ReviewFixture.original(self, A)[0]
        with mock.patch("task_governance_tool.session_identity.capture_caller_identity", return_value=CallerIdentity(A)), \
             mock.patch("task_governance_tool.usage_lifecycle.register_reviewer"):
            result = files.save(self.repo, "reviews/packet.json", "reviews/a.json", raw)
        self.assertEqual(result["status"], "saved")
        self.assertEqual((self.repo / "reviews/a.json").read_bytes(), raw)

    def test_save_admission_retains_lock_through_complete_file_publication(self):
        self.physical()
        self.publish()
        entered = threading.Event()
        release = threading.Event()
        errors = []
        def save_holder():
            try:
                with preparation.save_admission(self.repo, "reviews/packet.json", self.path.read_bytes(), self.packet, "reviews/a.json", CallerIdentity(A)):
                    entered.set()
                    release.wait(5)
            except Exception as exc:
                errors.append(exc)
                entered.set()
        worker = threading.Thread(target=save_holder)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(errors, [])
            with self.assertRaises(RepositoryError) as caught:
                with PreparationRepository(self.root / "preparation.sqlite").serial():
                    self.fail("a continuation cannot enter while Save owns admission")
            self.assertEqual(caught.exception.code, "writer_busy")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def save_public(self, actor, output):
        raw = ReviewFixture.original(self, actor)[0]
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdin", BinaryInput(raw)), redirect_stdout(stdout), \
             mock.patch("task_governance_tool.session_identity.capture_caller_identity", return_value=CallerIdentity(actor)), \
             mock.patch("task_governance_tool.usage_lifecycle.register_reviewer"):
            code = files.main(["save", "--repo", str(self.repo), "--packet", "reviews/packet.json", "--output", output])
        return code, json.loads(stdout.getvalue()), raw

    def test_public_save_real_lease_conflict_preserves_sibling_and_recovers_missing_only(self):
        self.physical()
        self.publish()
        code, saved, first = self.save_public(A, "reviews/a.json")
        self.assertEqual((code, saved["status"]), (0, "saved"))
        sidecar = self.repo / "reviews/a.json.session.json"
        original_sidecar = sidecar.read_bytes()
        journal = PreparationRepository(self.root / "preparation.sqlite")
        with journal.serial():
            code, failed, _ = self.save_public(B, "reviews/b.json")
        self.assertEqual(code, 1)
        self.assertEqual(failed, {"ok": False, "code": "handoff_writer_busy",
            "message": "Review handoff failed; preserve originals and inspect the outcome before retry.",
            "save_status": "not_started"})
        self.assertFalse((self.repo / "reviews/b.json").exists())
        self.assertFalse((self.repo / "reviews/b.json.session.json").exists())
        self.assertEqual((self.repo / "reviews/a.json").read_bytes(), first)
        self.assertEqual(sidecar.read_bytes(), original_sidecar)
        code, saved, second = self.save_public(B, "reviews/b.json")
        self.assertEqual((code, saved["status"]), (0, "saved"))
        # Parent confirmation and native registration consume the saved bytes;
        # the successful sibling is never saved or replaced again.
        from task_governance_tool import review_handoff_originals, review_session_transport, review_results
        with mock.patch("task_governance_tool.review_answer_preparation.resolve", return_value=self.resolution):
            task_id, framed = review_handoff_originals.submission(self.repo, "reviews/packet.json",
                ["reviews/a.json", "reviews/b.json"])
        decoded = review_session_transport.decode_submission(framed)
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            registered = review_results.add_review_results(connection, self.target.project, task_id,
                decoded.document, database_target=self.target, caller=self.caller,
                session_bindings=decoded.bindings)
        self.assertTrue(registered["review_gate"]["satisfied"])
        self.assertEqual((self.repo / "reviews/a.json").read_bytes(), first)
        self.assertEqual(sidecar.read_bytes(), original_sidecar)
        self.assertEqual((self.repo / "reviews/b.json").read_bytes(), second)

    def test_repository_failures_before_and_after_save_keep_fixed_safe_outcomes(self):
        self.physical()
        self.publish()
        for diagnostic in ("state_unreadable", "untrusted diagnostic detail"):
            with self.subTest(diagnostic=diagnostic), \
                 mock.patch.object(PreparationRepository, "read", side_effect=RepositoryError(diagnostic)):
                code, result, _ = self.save_public(A, "reviews/a.json")
            self.assertEqual((code, result["code"], result["save_status"]),
                             (1, "handoff_state_unavailable", "not_started"))
            self.assertNotIn(diagnostic, json.dumps(result))
            self.assertFalse((self.repo / "reviews/a.json").exists())
        original_admission = preparation.save_admission

        @contextmanager
        def late_failure(*args, **kwargs):
            with original_admission(*args, **kwargs):
                yield
            raise RepositoryError("writer_busy")

        with mock.patch.object(preparation, "save_admission", late_failure):
            code, result, raw = self.save_public(A, "reviews/a.json")
        self.assertEqual((code, result["code"], result["save_status"]),
                         (1, "handoff_outcome_unknown", "unknown"))
        self.assertEqual((self.repo / "reviews/a.json").read_bytes(), raw)
        self.assertTrue((self.repo / "reviews/a.json.session.json").is_file())

    def test_repository_failure_between_original_and_sidecar_preserves_residue(self):
        self.physical()
        self.publish()
        real_write = files.files.write_exclusive

        def write(path, raw):
            if path.name.endswith(".session.json"):
                raise RepositoryError("state_unreadable")
            return real_write(path, raw)

        with mock.patch.object(files.files, "write_exclusive", side_effect=write):
            code, result, raw = self.save_public(A, "reviews/a.json")
        self.assertEqual((code, result["code"], result["save_status"]),
                         (1, "handoff_outcome_unknown", "unknown"))
        self.assertEqual((self.repo / "reviews/a.json").read_bytes(), raw)
        self.assertFalse((self.repo / "reviews/a.json.session.json").exists())


if __name__ == "__main__":
    unittest.main()
