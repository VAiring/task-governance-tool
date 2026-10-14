"""Existing Packet read composition binds prepared continuation and detects drift."""

from contextlib import closing
import unittest
from unittest import mock

from tests import test_review_continuation_registration as fixture
from tests.test_review_continuation import A, ident
from task_governance_tool import storage, review_packet
from task_governance_tool import review_continuation as values, review_continuation_repository as repository


class ContinuationPacketTests(unittest.TestCase):
    setUp = fixture.ContinuationRegistrationTests.setUp
    prepare = fixture.ContinuationRegistrationTests.prepare
    original = fixture.ContinuationRegistrationTests.original
    register = fixture.ContinuationRegistrationTests.register

    def context(self):
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (self.task_id,)).fetchone()
            return repository.packet_context(connection, task=task)

    def test_preparing_does_not_expose_ready_packet_or_new_ordinary_context(self):
        self.assertIsNone(self.context())
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            repository.admit_locked(connection, basis=self.basis, preparation_id=ident("tg_review_preparation_"), route="manual",
                selected=(values.SelectedReviewer(A, "supplement", None, "Recover missing material"),), caller=self.caller)
        with self.assertRaises(review_packet.ReviewPacketError) as caught:
            review_packet.prepare_review_packet(self.target, self.task_id)
        self.assertEqual(caught.exception.code, "review_continuation_pending")

    def test_existing_public_packet_carries_exact_prepared_selected_context(self):
        record = self.prepare()
        value = review_packet.prepare_review_packet(self.target, self.task_id)
        self.assertEqual(value["review_session_context"], self.context())
        self.assertEqual(value["review_session_context"]["continuation_id"], record.continuation_id)
        self.assertEqual(value["review_session_context"]["selected_reviewers"], [dict(session_id=A, action="supplement", prior_receipt_id=None)])
        self.assertEqual(value["review_target"]["generation"], self.basis["target_generation"])

    def test_context_drift_between_two_reads_prevents_packet_publication(self):
        record = self.prepare()
        context = self.context()
        real = repository.packet_context
        calls = []
        def drifting(connection, *, task, preparing_id=None):
            value = real(connection, task=task, preparing_id=preparing_id)
            calls.append(value)
            return value if len(calls) == 1 else {**context, "continuation_sequence": 2}
        with mock.patch.object(repository, "packet_context", side_effect=drifting):
            with self.assertRaises(review_packet.ReviewPacketError) as caught:
                review_packet.prepare_review_packet(self.target, self.task_id)
        self.assertEqual(caught.exception.code, "review_packet_stale")
        self.assertGreaterEqual(len(calls), 2)

    def test_registered_continuation_does_not_force_new_activity_context(self):
        record = self.prepare()
        self.register([self.original(A, continued=record)], continued=record)
        self.assertIsNone(self.context())
        packet = review_packet.prepare_review_packet(self.target, self.task_id)
        self.assertEqual(packet["review_session_context"]["version"], 1)


if __name__ == "__main__":
    unittest.main()
