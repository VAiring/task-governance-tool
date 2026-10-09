"""Operational intent validates closed bindings and preserves successful stages."""

from dataclasses import asdict, replace
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tests.test_review_wait_service import BINDING, CHILD, TURN, OTHER
from task_governance_tool.review_finalization_repository import FinalizationRecord, FinalizationRepository, RepositoryError


class FinalizationRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "intent.sqlite"
        binding = replace(BINDING, task_id="tg_task_" + "1" * 16, execution_id="tg_execution_" + "2" * 16,
                          artifact_manifest_id="tg_artifact_manifest_" + "3" * 16,
                          target_kind="git_snapshot", target_base_revision="a" * 40)
        basis = asdict(binding)
        basis.pop("wait_id")
        basis.update(version=1, task_status="review_pending")
        self.record = FinalizationRecord(basis, "reviews/packet.json", "c" * 64, ("reviews/one.json",), "refs/heads/main")
        self.repo = FinalizationRepository(self.path)

    def test_closed_intent_read_only_observation_and_monotone_transitions(self):
        with self.repo.serial(initial=self.record):
            pass
        before = self.path.read_bytes()
        self.assertEqual(self.repo.read(), self.record)
        self.assertEqual(self.path.read_bytes(), before)
        with self.repo.serial() as lease:
            first = self.repo.advance(lease, registration="dispatching", originals=((CHILD, "d" * 64),))
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, registration="not_started")
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, originals=((CHILD, "e" * 64),))
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, command="PRIVATE_COMMAND")
            self.assertEqual(self.repo.read(), first)
        self.assertNotIn(b"PRIVATE_COMMAND", self.path.read_bytes())
        with self.assertRaises(RepositoryError):
            with self.repo.serial(initial=self.record):
                pass

    def test_missing_corrupt_schema_and_invalid_order_never_repair(self):
        with self.assertRaises(RepositoryError):
            with self.repo.serial():
                pass
        self.assertFalse(self.path.exists())
        with self.assertRaises(RepositoryError):
            replace(self.record, commit="dispatching", candidate="a" * 40)
        with self.repo.serial(initial=self.record):
            pass
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE unexpected(secret)")
            connection.commit()
        before = self.path.read_bytes()
        with self.assertRaises(RepositoryError):
            self.repo.read()
        self.assertEqual(self.path.read_bytes(), before)

    def test_reviewer_failure_unknown_and_changed_turn_are_durable(self):
        with self.repo.serial(initial=self.record):
            pass
        with self.repo.serial() as lease:
            self.repo.observe_reviewers(lease, ((CHILD, TURN, "completed"),))
            failed = self.repo.observe_reviewers(lease, ((CHILD, TURN, "interrupted"),))
            self.assertEqual(failed.reviewer_blocker, "finalization_reviewer_failed")
            self.assertEqual(self.repo.observe_reviewers(lease, ((CHILD, TURN, "completed"),)), failed)
            self.assertEqual(self.repo.observe_reviewers(lease, ((CHILD, OTHER, "completed"),)), failed)
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, reviewer_blocker=None)
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, reviewer_observations=())
        self.assertEqual(self.repo.read(), failed)

    def test_partial_observations_grow_without_erasing_failure_or_turn(self):
        with self.repo.serial(initial=replace(self.record, result_paths=("reviews/one.json", "reviews/two.json"))):
            pass
        with self.repo.serial() as lease:
            self.repo.observe_reviewers(lease, ((CHILD, TURN, "failed"),))
            observed = self.repo.observe_reviewers(lease, ((OTHER, TURN, "completed"),))
            self.assertEqual(set(observed.reviewer_observations), {(CHILD, TURN, "failed"), (OTHER, TURN, "completed")})
            self.assertEqual(observed.reviewer_blocker, "finalization_reviewer_failed")
            self.assertEqual(self.repo.observe_reviewers(lease, ((CHILD, TURN, "completed"),)), observed)
            with self.assertRaises(RepositoryError):
                self.repo.advance(lease, reviewer_observations=((OTHER, TURN, "completed"),))

    def test_changed_turn_and_nonoriginal_identity_latch_mismatch(self):
        with self.repo.serial(initial=self.record):
            pass
        with self.repo.serial() as lease:
            first = self.repo.observe_reviewers(lease, ((CHILD, TURN, "completed"),))
            changed = self.repo.observe_reviewers(lease, ((CHILD, OTHER, "completed"),))
            self.assertEqual(changed.reviewer_observations, first.reviewer_observations)
            self.assertEqual(changed.reviewer_blocker, "finalization_reviewer_mismatch")
            self.repo.advance(lease, registration="dispatching", originals=((CHILD, "d" * 64),))
            wrong = self.repo.observe_reviewers(lease, ((OTHER, TURN, "completed"),))
            self.assertEqual(wrong.reviewer_observations, first.reviewer_observations)
