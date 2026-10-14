"""Durable transition admission and crash recovery without any live host effect."""

from dataclasses import replace
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.test_review_continuation import ident
from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository
from task_governance_tool.review_wait_runtime.request_continuation import RequestRound, encode
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError


def pending(index=1):
    return RequestRound(ident("tg_review_preparation_"), ident("tg_review_continuation_", index), index,
        "a" * 64, "d" * 64, "b" * 64, 0, None, None, None,
        packet_path=f"reviews/round-{index}/packet.json", result_paths=(f"reviews/round-{index}/a.json",))


class ContinuationTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-transition-")
        self.addCleanup(temporary.cleanup)
        self.repo = TransitionRepository(Path(temporary.name).resolve() / "transition.sqlite")

    def fence(self, value=None):
        with self.repo.serial(create=not self.repo.path.exists()) as lease:
            return self.repo.fence(lease, value or pending())

    def publish(self, value):
        with self.repo.serial() as lease:
            settled = self.repo.advance(lease, value, phase="settled")
            prepared = self.repo.advance(lease, settled, phase="prepared", packet_path=value.packet_path,
                packet_digest="c" * 64, result_paths=value.result_paths)
            return self.repo.advance(lease, prepared, phase="published")

    def test_fence_is_idempotent_and_read_or_restart_never_advances(self):
        value = self.fence()
        before = self.repo.path.read_bytes()
        self.assertEqual(TransitionRepository(self.repo.path).read(), (value,))
        self.assertEqual(self.repo.path.read_bytes(), before)
        self.assertEqual(self.fence(), value)
        with self.repo.serial() as lease:
            with self.assertRaises(RepositoryError):
                self.repo.fence(lease, replace(pending(), selection_digest="d" * 64))
        self.assertEqual(self.repo.read(), (value,))

    def test_settled_prepared_and_published_are_distinct_durable_boundaries(self):
        value = self.fence()
        with self.repo.serial() as lease:
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, value, phase="prepared", packet_path="reviews/packet.json",
                    packet_digest="c" * 64, result_paths=("reviews/a.json",))
        published = self.publish(value)
        self.assertEqual(self.fence(), published)
        next_round = self.fence(pending(2))
        self.assertEqual(self.repo.read(), (published, next_round))
        with self.repo.serial() as lease:
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, published, phase="settled")
        self.assertEqual(self.repo.read(), (published, next_round))

    def test_unfinished_prior_round_rejects_competing_publication(self):
        value = self.fence()
        with self.repo.serial() as lease:
            with self.assertRaises(RepositoryError):
                self.repo.fence(lease, pending(2))
        self.assertEqual(self.repo.read(), (value,))

    def test_packet_byte_intent_survives_restart_and_cannot_be_replaced(self):
        fenced = self.fence()
        with self.repo.serial() as lease:
            settled = self.repo.advance(lease, fenced, phase="settled")
            intended = self.repo.bind_packet(lease, settled, "c" * 64)
        self.assertEqual(TransitionRepository(self.repo.path).read(), (intended,))
        with self.repo.serial() as lease:
            self.assertEqual(self.repo.bind_packet(lease, intended, "c" * 64), intended)
            with self.assertRaises(RepositoryError):
                self.repo.bind_packet(lease, intended, "d" * 64)
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, intended, phase="prepared", packet_path=intended.packet_path,
                    packet_digest="d" * 64, result_paths=intended.result_paths)
        self.assertEqual(self.repo.read(), (intended,))

    def test_capacity_is_64_and_never_evicts_history(self):
        for index in range(1, 65):
            self.publish(self.fence(pending(index)))
        before = self.repo.read()
        with self.assertRaises(RepositoryError):
            pending(65)
        self.assertEqual(len(before), 64)
        self.assertEqual(self.repo.read(), before)

    def test_transaction_failure_after_round_update_keeps_fence(self):
        value = self.fence()
        load = self.repo._load
        def reject_settled(connection):
            result = load(connection)
            if result[-1].phase == "settled":
                raise RepositoryError("state_unreadable")
            return result
        with self.repo.serial() as lease:
            with mock.patch.object(self.repo, "_load", side_effect=reject_settled):
                with self.assertRaises(RepositoryError):
                    self.repo.advance(lease, value, phase="settled")
        self.assertEqual(self.repo.read(), (value,))

    def test_corrupt_prior_round_and_unknown_schema_cannot_be_hidden_by_last_row(self):
        self.publish(self.fence())
        self.fence(pending(2))
        with closing(sqlite3.connect(self.repo.path)) as connection, connection:
            value = json.loads(connection.execute("SELECT payload FROM rounds WHERE sequence=1").fetchone()[0])
            value["continuation_id"] = ident("tg_review_continuation_", 99)
            connection.execute("UPDATE rounds SET payload=? WHERE sequence=1", (json.dumps(value),))
        before = self.repo.path.read_bytes()
        with self.assertRaises(RepositoryError):
            self.repo.read()
        self.assertEqual(self.repo.path.read_bytes(), before)

    def test_closed_inputs_never_claim_cleanup_from_missing_request_identity(self):
        for changes in (dict(expected_attempt=True), dict(expected_attempt=1), dict(old_automation_id="timer"),
                        dict(phase="prepared"), dict(old_request_phase="unknown")):
            with self.subTest(changes=changes), self.assertRaises(RepositoryError):
                replace(pending(), **changes)
        self.assertEqual(encode(pending()), encode(pending()))


if __name__ == "__main__":
    unittest.main()
