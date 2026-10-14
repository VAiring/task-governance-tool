"""Explicit settled v1-to-v2 journals with same-turn continuation and no effects."""

from dataclasses import replace
from contextlib import closing
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4
from unittest import mock

from tests.test_review_wait_continuation_transition import pending
from tests.test_review_continuation import ident
from task_governance_tool.review_wait_runtime.request_repository import RequestRecord, RequestRepository, HistoricalRequestProof, request_identity_digest
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError


class RequestContinuationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-request-continuation-")
        self.addCleanup(temporary.cleanup)
        self.repo = RequestRepository(Path(temporary.name).resolve() / "request.sqlite")
        self.turn = str(uuid4())
        self.pairs = ((str(uuid4()), str(uuid4())),)
        with self.repo.serial(create=True) as lease:
            self.repo.append(lease, RequestRecord("d" * 64, self.turn, self.pairs))
            self.repo.advance(lease, "created", automation_id="known-timer")
            self.repo.advance(lease, "closing")
            self.repo.advance(lease, "closed")
        _, before = self.repo.read()
        self.record = replace(pending(), expected_attempt=1, expected_request_digest=request_identity_digest(before),
            old_automation_id=before.automation_id, old_request_phase="created", phase="prepared",
            packet_path="reviews/new/packet.json", packet_digest="c" * 64, result_paths=("reviews/new/a.json",))

    def payloads(self):
        with closing(sqlite3.connect(self.repo.path)) as connection:
            return connection.execute("SELECT sequence,payload FROM attempts ORDER BY sequence").fetchall()

    def install(self):
        with self.repo.serial() as lease:
            return self.repo.install_round(lease, self.record)

    def test_read_stays_v1_until_explicit_settled_conversion_and_preserves_original_bytes(self):
        before = self.repo.path.read_bytes()
        originals = self.payloads()
        self.assertEqual(self.repo.read_rounds(), ())
        self.assertEqual(self.repo.path.read_bytes(), before)
        self.install()
        self.assertEqual(self.payloads(), originals)
        self.assertEqual(self.repo.read_rounds(), (self.record,))
        self.assertEqual(self.install(), self.record)

    def test_fenced_or_unsettled_record_cannot_convert(self):
        before = self.repo.path.read_bytes()
        with self.repo.serial() as lease:
            with self.assertRaises(RepositoryError):
                self.repo.install_round(lease, replace(self.record, phase="fenced", packet_digest=None))
        self.assertEqual(self.repo.path.read_bytes(), before)

    def test_same_parent_turn_requires_exact_prepared_continuation(self):
        self.install()
        with self.repo.serial() as lease:
            for value in (RequestRecord("d" * 64, self.turn, self.pairs),
                          RequestRecord("d" * 64, self.turn, self.pairs, continuation_id=ident("tg_review_continuation_", 99), continuation_sequence=1)):
                with self.assertRaises(RepositoryError):
                    self.repo.append(lease, value)
            value = RequestRecord("d" * 64, self.turn, self.pairs, continuation_id=self.record.continuation_id, continuation_sequence=1)
            self.repo.append(lease, value)
        self.assertEqual(self.repo.read()[1], value)
        self.assertEqual(self.repo.read_rounds()[0].phase, "published")
        self.assertEqual(self.install().phase, "published")

    def test_already_ended_round_has_no_new_attempt_or_automation(self):
        self.install()
        before = self.payloads()
        with self.repo.serial() as lease:
            after = self.repo.publish_without_wait(lease, self.record)
        self.assertEqual(after.phase, "published")
        self.assertEqual(self.payloads(), before)

    def first_closed_occurrence(self):
        self.install()
        value = RequestRecord("d" * 64, self.turn, self.pairs,
            continuation_id=self.record.continuation_id, continuation_sequence=1)
        with self.repo.serial() as lease:
            self.repo.append(lease, value)
            self.repo.advance(lease, "created", automation_id="continued-timer")
            self.repo.advance(lease, "closing")
            self.repo.advance(lease, "closed")
        return value

    def test_later_parent_turn_can_append_same_round_occurrence_preserving_history(self):
        value = self.first_closed_occurrence()
        before = self.payloads()
        rounds = self.repo.read_rounds()
        later = replace(value, parent_turn=str(uuid4()))
        with self.repo.serial() as lease:
            self.repo.append(lease, later)
        self.assertEqual(self.repo.read(), (3, later))
        self.assertEqual(self.payloads()[:2], before)
        self.assertEqual(self.repo.read_rounds(), rounds)

    def test_helper_readiness_recognizes_current_renewed_occurrence_without_redispatch(self):
        value = self.first_closed_occurrence()
        with self.repo.serial() as lease:
            self.repo.append(lease, replace(value, parent_turn=str(uuid4())))
            self.repo.advance(lease, "created", automation_id="renewed-timer")
            self.repo.advance(lease, "prepared")
            self.repo.advance(lease, "started")
        from types import SimpleNamespace
        from task_governance_tool.review_handoff_continuation import _request_round
        before = self.repo.path.read_bytes()
        round_record, occurrence = _request_round(self.repo, self.record,
            (SimpleNamespace(session_id=self.pairs[0][0]),))
        self.assertEqual(round_record.phase, "published")
        self.assertEqual(occurrence, self.repo.read()[1])
        self.assertEqual(self.repo.path.read_bytes(), before)

    def test_published_round_cannot_replace_same_turn_or_changed_reviewer_pairs(self):
        value = self.first_closed_occurrence()
        before = self.payloads()
        with self.repo.serial() as lease:
            for request in (value, replace(value, parent_turn=str(uuid4()),
                    reviewers=((self.pairs[0][0], str(uuid4())),))):
                with self.assertRaises(RepositoryError):
                    self.repo.append(lease, request)
        self.assertEqual(self.payloads(), before)

    def test_published_round_without_prior_occurrence_cannot_start_later_activity(self):
        self.install()
        with self.repo.serial() as lease:
            self.repo.publish_without_wait(lease, self.record)
            with self.assertRaises(RepositoryError):
                self.repo.append(lease, RequestRecord("d" * 64, str(uuid4()), self.pairs,
                    continuation_id=self.record.continuation_id, continuation_sequence=1))

    def test_stored_repeated_round_with_changed_pairs_is_rejected_without_repair(self):
        value = self.first_closed_occurrence()
        with self.repo.serial() as lease:
            self.repo.append(lease, replace(value, parent_turn=str(uuid4())))
        from task_governance_tool.review_wait_runtime.request_repository import _encode
        changed = replace(self.repo.read()[1], reviewers=((self.pairs[0][0], str(uuid4())),))
        with closing(sqlite3.connect(self.repo.path)) as connection, connection:
            connection.execute("UPDATE attempts SET payload=? WHERE sequence=3", (_encode(changed),))
        before = self.repo.path.read_bytes()
        with self.assertRaises(RepositoryError):
            self.repo.read()
        self.assertEqual(self.repo.path.read_bytes(), before)

    def test_started_phase_settles_after_short_worker_read_without_replaying_write(self):
        value = self.first_closed_occurrence()
        with self.repo.serial() as lease:
            self.repo.append(lease, replace(value, parent_turn=str(uuid4())))
            self.repo.advance(lease, "created", automation_id="renewed-timer")
            self.repo.advance(lease, "prepared")
        reading, release = threading.Event(), threading.Event()
        errors = []
        def worker_snapshot():
            try:
                with closing(sqlite3.connect(self.repo.path)) as connection:
                    connection.execute("BEGIN")
                    connection.execute("SELECT payload FROM attempts").fetchall()
                    reading.set()
                    release.wait(2)
                    connection.rollback()
            except Exception as error:
                errors.append(error)
                reading.set()
        worker = threading.Thread(target=worker_snapshot)
        worker.start()
        self.assertTrue(reading.wait(2))
        timer = threading.Timer(0.05, release.set)
        try:
            timer.start()
            with self.repo.serial() as lease:
                after = self.repo.advance(lease, "started")
            self.assertEqual(after.phase, "started")
            self.assertEqual(self.repo.read()[0], 3)
        finally:
            release.set()
            timer.join(2)
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_failed_conversion_rolls_back_schema_and_every_original_row(self):
        before = self.payloads()
        load = self.repo._load
        def reject_v2(connection):
            value = load(connection)
            if connection.execute("PRAGMA user_version").fetchone() == (2,):
                raise RepositoryError("state_unreadable")
            return value
        with self.repo.serial() as lease:
            with mock.patch.object(self.repo, "_load", side_effect=reject_v2):
                with self.assertRaises(RepositoryError):
                    self.repo.install_round(lease, self.record)
        self.assertEqual(self.repo.read_rounds(), ())
        self.assertEqual(self.payloads(), before)

    def test_pointer_corruption_rejects_all_reads_without_repair(self):
        self.install()
        with closing(sqlite3.connect(self.repo.path)) as connection, connection:
            connection.execute("UPDATE current_round SET ordinal=99")
        before = self.repo.path.read_bytes()
        with self.assertRaises(RepositoryError):
            self.repo.read()
        self.assertEqual(self.repo.path.read_bytes(), before)

    def test_distinct_preparation_root_gets_its_own_sequence_without_erasing_prior_root(self):
        self.install()
        with self.repo.serial() as lease:
            self.repo.publish_without_wait(lease, self.record)
            second = replace(self.record, preparation_id=ident("tg_review_preparation_", 2), continuation_id=ident("tg_review_continuation_", 2))
            self.repo.install_round(lease, second)
        self.assertEqual(len(self.repo.read_rounds()), 2)
        self.assertEqual([row.continuation_sequence for row in self.repo.read_rounds()], [1, 1])

    def test_different_basis_requires_exact_invocation_proof_and_preserves_v1_v2_history(self):
        for version in (1, 2):
            with self.subTest(version=version):
                if version == 2:
                    with self.repo.serial() as lease:
                        self.repo.publish_without_wait(lease, changed)
                old = self.repo.read()[1]
                changed = replace(self.record, preparation_id=ident("tg_review_preparation_", 10 + version),
                    continuation_id=ident("tg_review_continuation_", 10 + version),
                    wait_basis_digest=str(version) * 64, worker_stop_identity="e" * 64)
                proof = HistoricalRequestProof(1, request_identity_digest(old), old.automation_id,
                    old.basis_digest, changed.wait_basis_digest, changed.worker_stop_identity)
                before = self.payloads()
                with self.repo.serial() as lease:
                    for invalid in (None, replace(proof, expected_attempt=2), replace(proof, old_basis_digest="f" * 64),
                            replace(proof, wait_basis_digest="a" * 64), replace(proof, expected_request_digest="a" * 64),
                            replace(proof, old_automation_id="other"), replace(proof, worker_stop_identity="a" * 64)):
                        with self.assertRaises(RepositoryError):
                            self.repo.install_round(lease, changed, historical=invalid)
                    self.assertEqual(self.repo.install_round(lease, changed, historical=proof), changed)
                    self.assertEqual(self.repo.install_round(lease, changed, historical=proof), changed)
                self.assertEqual(self.payloads(), before)


if __name__ == "__main__":
    unittest.main()
