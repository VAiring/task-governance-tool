"""Candidate schema-29 preparation with native core authority/ownership reads."""

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import unittest
import subprocess
import threading
from unittest import mock

from tests import test_review_continuation_registration as registration_fixture
from tests.test_review_handoff import packet_for
from tests.test_review_results import document, encode
from task_governance_tool import review_preparation as preparation, storage
from task_governance_tool.review_preparation_repository import PreparationRepository
from task_governance_tool.review_wait_basis_repository import read_preparation_basis
from task_governance_tool.review_handoff import HandoffError
from task_governance_tool.session_identity import CallerIdentity
from tests.test_review_continuation import A
from tests.test_review_continuation import B, ident
from task_governance_tool import review_handoff as files, review_continuation as values
from task_governance_tool import review_continuation_repository as continuations
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError


class PreparationPublicationTests(unittest.TestCase):
    setUp = registration_fixture.ContinuationRegistrationTests.setUp

    def physical(self):
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True, capture_output=True)
        with (self.repo / ".gitignore").open("a", encoding="utf-8") as target:
            target.write("\n/reviews/\n")
        (self.repo / "reviews").mkdir()
        self.path = self.repo / "reviews/packet.json"
        self.packet = packet_for(document(self.task_id, revision=self.basis["contract_revision"],
            generation=self.basis["target_generation"]))
        self.packet["review_target"] = {key: self.basis["target_" + key] for key in ("kind", "value", "base_revision", "generation")}
        from task_governance_tool.review_results import review_result_template
        self.packet["result_template"] = review_result_template(self.task_id, self.basis["contract_revision"], self.packet["review_target"])
        self.packet["task"].update(verification="Fixture verification", verification_not_required_reason="")
        self.packet["review_session_context"] = dict(version=1, project_id=self.basis["project_id"], execution_id=self.basis["execution_id"])
        self.path.write_bytes(encode(self.packet))
        self.root = self.repo / "operational"
        self.root.mkdir()
        paths = SimpleNamespace(review_wait_root=self.root, fixed_root=self.repo,
            review_preparation_store=lambda *_: self.root / "preparation.sqlite",
            review_finalization_store=lambda *_: self.root / "finalization.sqlite")
        self.resolution = SimpleNamespace(target=self.target, paths=paths)
        self.enterContext(mock.patch.object(preparation, "resolve", return_value=self.resolution))

    def publish(self, *, route="manual", caller=None):
        return preparation.publish_ready(self.repo, "reviews/packet.json", ["reviews/a.json", "reviews/b.json"], caller or self.caller, route=route)

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
        raw = registration_fixture.ContinuationRegistrationTests.original(self, A)[0]
        with mock.patch.object(files, "capture_caller_identity", return_value=CallerIdentity(A)):
            with self.assertRaises(HandoffError) as caught:
                files.save(self.repo, "reviews/packet.json", "reviews/a.json", raw)
        self.assertEqual(caught.exception.code, "review_packet_stale")
        self.assertFalse((self.repo / "reviews/a.json").exists())
        self.assertFalse((self.repo / "reviews/a.json.session.json").exists())

    def test_unselected_initial_original_can_finish_under_unchanged_basis(self):
        self.physical()
        self.publish()
        self.pending(B)
        raw = registration_fixture.ContinuationRegistrationTests.original(self, A)[0]
        with mock.patch.object(files, "capture_caller_identity", return_value=CallerIdentity(A)), \
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


if __name__ == "__main__":
    unittest.main()
