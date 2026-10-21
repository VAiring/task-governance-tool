"""Cleanup decisions from exact physical saved effects, with offline lifecycle proof."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import tempfile
import threading
import unittest

from tests.test_review_wait_service import BINDING, CHILD, TURN, PARENT, NOW, FakeHost
from tests.test_review_wait_direct import ORIGINAL
from tests.test_review_wait_continuation_transition import pending
from task_governance_tool.review_wait_runtime.review_wait_controller import ReviewWaitController, Reviewer
from task_governance_tool.review_wait_runtime.review_wait_runtime import reservation
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
from task_governance_tool.review_wait_runtime.review_wait_direct_repository import DirectRepository, DirectRecord, instant
from task_governance_tool.review_wait_runtime.review_wait_direct import binding_digest
from task_governance_tool.review_wait_runtime.request_repository import RequestRepository, RequestRecord, HistoricalRequestProof, request_identity_digest
from task_governance_tool.review_wait_runtime.managed_wait import basis_digest
from task_governance_tool.review_wait_runtime.continuation_cleanup_repository import observe_cleanup, observe_latest_cleanup, observe_generation_cleanup


class ContinuationCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-cleanup-proof-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.request = RequestRepository(self.root / "request.sqlite")
        self.wait_path = self.root / "wait.sqlite"
        self.paths = SimpleNamespace(review_wait_request_store=lambda *_: self.request.path,
            review_wait_store=lambda *_: self.wait_path)

    def old_request(self, phase="closed", *, admitted_phase="started"):
        with self.request.serial(create=True) as lease:
            self.request.append(lease, RequestRecord(basis_digest(BINDING), ORIGINAL, ((CHILD, TURN),)))
            if phase != "creating":
                self.request.advance(lease, "created", automation_id="timer-1")
                if phase in ("started", "closed"):
                    self.request.advance(lease, "prepared")
                    self.request.advance(lease, "started")
                if phase == "closed":
                    self.request.advance(lease, "closing")
                    self.request.advance(lease, "closed")
                elif phase == "unknown":
                    self.request.advance(lease, "closing")
                    self.request.advance(lease, "unknown")
        sequence, current = self.request.read()
        return replace(pending(), wait_basis_digest=basis_digest(BINDING), expected_attempt=sequence,
            expected_request_digest=request_identity_digest(current), old_automation_id=current.automation_id,
            old_request_phase=admitted_phase)

    def direct(self, *, joined=False, status="cancelled", timer="deleted", binding=BINDING):
        host = FakeHost()
        controller = ReviewWaitController(binding, (Reviewer(CHILD, TURN),), reservation(host.value))
        ReviewWaitRepository.create(self.wait_path, controller)
        repository = DirectRepository.for_wait(self.wait_path)
        now = NOW.replace(microsecond=0)
        record = DirectRecord(str(uuid4()), binding_digest(controller), ORIGINAL, "waiting", instant(now),
            instant(now + timedelta(minutes=20)), version=2, timer_phase="arming",
            timer_rule="FREQ=DAILY;BYHOUR=0;BYMINUTE=10;BYSECOND=0;COUNT=1", timer_updated_at=1,
            timer_due_at=instant(now + timedelta(minutes=10)), worker_id=str(uuid4()))
        repository.create_record(record)
        repository.update(record.probe_id, timer_phase="active", timer_updated_at=2)
        if timer == "deleted":
            repository.update(record.probe_id, timer_phase="deleting")
            repository.update(record.probe_id, timer_phase="deleted")
        if status in ("accepted", "rejected", "unknown"):
            repository.update(record.probe_id, status="dispatching")
        repository.update(record.probe_id, status=status)
        if joined:
            # Exercise an actual started/joined isolated thread; it performs no
            # timer/send/work. Only the external observer saves its confirmation.
            worker = threading.Thread(target=lambda: None)
            worker.start()
            worker.join(1)
            self.assertIsNotNone(worker.ident)
            self.assertFalse(worker.is_alive())
            repository.confirm_worker_stopped(record.probe_id, record.worker_id, record.binding_digest, instant(now + timedelta(seconds=1)))
        return repository.read()

    def observe(self, record):
        before = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        result = observe_cleanup(self.paths, PARENT, BINDING.task_id, record)
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}, before)
        return result

    def test_no_admitted_request_settles_without_inventing_worker_proof(self):
        self.assertEqual(self.observe(pending()).state, "settled")

    def test_known_started_request_requires_cleanup_before_any_replacement(self):
        value = self.old_request("started")
        result = self.observe(value)
        self.assertEqual((result.state, result.reason), ("pending", "review_continuation_cleanup_required"))

    def test_unknown_creation_or_cleanup_never_recommends_replay(self):
        value = self.old_request("unknown")
        result = self.observe(value)
        self.assertEqual((result.state, result.reason), ("unknown", "review_continuation_effect_unknown"))

    def test_missing_marker_after_started_is_unknown_even_for_closed_request(self):
        value = self.old_request()
        self.assertEqual(self.observe(value).reason, "review_continuation_worker_exit_unknown")

    def test_known_pre_start_creation_without_marker_can_settle(self):
        value = self.old_request(admitted_phase="created")
        self.assertEqual(self.observe(value).state, "settled")

    def test_deleted_timer_without_joined_worker_remains_unknown(self):
        value = self.old_request()
        self.direct()
        self.assertEqual(self.observe(value).reason, "review_continuation_worker_exit_unknown")

    def test_saved_matching_thread_join_and_known_cleanup_settle(self):
        value = self.old_request()
        direct = self.direct(joined=True)
        result = self.observe(value)
        self.assertEqual((result.state, result.reason, result.worker_stop_identity), ("settled", None, direct.worker_stop_identity))

    def test_unknown_send_blocks_even_with_deleted_timer_and_joined_worker(self):
        value = self.old_request()
        self.direct(joined=True, status="unknown")
        self.assertEqual(self.observe(value).reason, "review_continuation_effect_unknown")

    def test_closed_request_with_active_timer_is_unresolved_not_ready(self):
        value = self.old_request()
        self.direct(joined=True, timer="active")
        self.assertEqual(self.observe(value).reason, "review_continuation_effect_unknown")

    def test_latest_snapshot_proof_requires_join_and_preserves_every_stored_byte(self):
        self.old_request()
        sequence, request = self.request.read()
        self.direct()
        missing = observe_latest_cleanup(self.paths, PARENT, BINDING.task_id, sequence, request)
        self.assertEqual(missing.reason, "review_continuation_worker_exit_unknown")
        direct = DirectRepository.for_wait(self.wait_path)
        marker = direct.read()
        worker = threading.Thread(target=lambda: None)
        worker.start()
        worker.join(1)
        direct.confirm_worker_stopped(marker.probe_id, marker.worker_id, marker.binding_digest, instant(NOW))
        before = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        proof = observe_latest_cleanup(self.paths, PARENT, BINDING.task_id, sequence, request)
        self.assertEqual(proof.state, "settled")
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}, before)

    def test_historical_cleanup_requires_exact_proof_and_matching_actual_worker(self):
        original = self.old_request()
        direct = self.direct(joined=True)
        changed = replace(original, wait_basis_digest="f" * 64)
        proof = HistoricalRequestProof(original.expected_attempt, original.expected_request_digest,
            original.old_automation_id, original.wait_basis_digest, changed.wait_basis_digest, direct.worker_stop_identity)
        before = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        for invalid in (None, replace(proof, expected_request_digest="c" * 64),
                replace(proof, worker_stop_identity="d" * 64), replace(proof, wait_basis_digest="c" * 64)):
            self.assertEqual(observe_cleanup(self.paths, PARENT, BINDING.task_id, changed, historical=invalid).reason,
                "review_continuation_conflict")
        self.assertEqual(observe_cleanup(self.paths, PARENT, BINDING.task_id, changed, historical=proof).state, "settled")
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}, before)

    def test_controller_cannot_substitute_another_target_for_the_exact_old_request(self):
        record = self.old_request()
        self.direct(joined=True, binding=replace(BINDING, target_generation=BINDING.target_generation + 1))
        self.assertEqual(self.observe(record).reason, "review_continuation_conflict")

    def test_generation_switch_checks_actual_request_task_parent_and_sequence(self):
        self.old_request("started")
        self.direct(joined=True)
        sequence, request = self.request.read()
        current = replace(BINDING, target_generation=BINDING.target_generation + 1)
        before = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        for parent, task, attempt, original in (
                (str(uuid4()), BINDING.task_id, sequence, request),
                (PARENT, "tg_task_2222222222222222", sequence, request),
                (PARENT, BINDING.task_id, sequence + 1, request),
                (PARENT, BINDING.task_id, sequence, replace(request, reviewers=((CHILD, str(uuid4())),)))):
            with self.subTest(parent=parent, task=task, attempt=attempt):
                proof = observe_generation_cleanup(self.paths, parent, task, attempt, original, current)
                self.assertEqual(proof.state, "unknown")
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()})


if __name__ == "__main__":
    unittest.main()
