"""Settled older waits switch generation once without parent recovery calls."""

from dataclasses import replace
import threading
import unittest
from unittest import mock
from uuid import uuid4

from tests import test_review_wait_managed as fixtures
from tests import test_review_wait_managed_continuation as continuation_fixtures
from task_governance_tool.review_wait_runtime.request_repository import RequestRepository
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn


class GenerationSwitchTests(unittest.TestCase):
    setUp = fixtures.ManagedFlowTests.setUp
    close_all = fixtures.ManagedFlowTests.close_all
    factory = fixtures.ManagedFlowTests.factory
    make = fixtures.ManagedFlowTests.make
    call = fixtures.ManagedFlowTests.call
    ended = fixtures.ManagedFlowTests.ended
    worker = fixtures.WorkerObservationTests.worker
    settled = fixtures.WorkerObservationTests.settled
    rewrite = fixtures.WorkerObservationTests.rewrite
    prepare_round = continuation_fixtures.ManagedContinuationTests.prepared

    def previous(self, *, receipt=True, save=True):
        self.fail_receipt = not receipt
        self.assertEqual(self.call()["status"], "waiting")
        worker = self.worker()
        context = (mock.patch.object(worker.repository, "confirm_worker_stopped", side_effect=OSError())
                   if not save else mock.patch.object(worker, "_worker_stop_persistence", "not_recorded"))
        with context:
            self.ended()
            self.settled()
        self.journal = RequestRepository(self.paths.review_wait_request_store(fixtures.PARENT, fixtures.TASK))
        sequence, request = self.journal.read()
        self.assertEqual((sequence, request.phase), (1, "started"))
        self.old = request
        direct = worker.repository.read()
        self.assertEqual((direct.status, direct.timer_phase), ("accepted", "deleted"))
        self.assertEqual(direct.acknowledged_turn is not None, receipt)
        self.metadata = {"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN}
        self.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
        self.child = ChildTurn(fixtures.CHILD, fixtures.OTHER, "inProgress", "active")
        self.basis = replace(self.basis, target_generation=self.basis.target_generation + 1)
        self.effects.clear()
        return direct

    def fresh(self, **kwargs):
        return self.call(metadata=self.metadata, **kwargs)

    def test_same_process_and_restart_use_actual_exit_and_preserve_old_history(self):
        for restarted in (False, True):
            for receipt in (False, True):
                with self.subTest(restarted=restarted, receipt=receipt):
                    self.setUp()
                    self.previous(receipt=receipt)
                    session = self.make() if restarted else self.session
                    result = self.fresh(session=session)
                    self.assertEqual(result["status"], "waiting", result)
                    self.assertEqual(self.effects, ["create", "arm"])
                    sequence, request = self.journal.read()
                    self.assertEqual(sequence, 2)
                    self.assertNotEqual(request.automation_id, self.old.automation_id)
                    with self.journal._connection() as connection:
                        from task_governance_tool.review_wait_runtime.request_repository import _decode_record
                        old = _decode_record(connection.execute("SELECT payload FROM attempts WHERE sequence=1").fetchone()[0])
                    self.assertEqual(old, replace(self.old, phase="closed"))
                    self.assertTrue(self.fresh(session=session)["replayed"])
                    self.assertEqual(self.effects, ["create", "arm"])
                    self.close_all()

    def test_direct_join_without_saved_proof_is_usable_only_with_exact_owned_thread(self):
        self.previous(save=False)
        restarted = self.make()
        self.assertFalse(self.fresh(session=restarted)["ok"])
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read()[1], self.old)
        self.assertEqual(self.fresh()["status"], "waiting")
        self.assertEqual(self.effects, ["create", "arm"])

    def test_historical_round_keeps_direct_exit_proof_when_saving_continues_to_fail(self):
        for ended in (False, True):
            for closed in (False, True):
                with self.subTest(ended=ended, closed=closed):
                    self.setUp()
                    self.prepare_round()
                    self.previous(receipt=False, save=False)
                    rounds = self.journal.read_rounds()
                    self.assertEqual(rounds[-1].phase, "published")
                    worker = self.worker()
                    with mock.patch.object(worker.repository, "confirm_worker_stopped", side_effect=OSError()):
                        if closed:
                            self.assertTrue(self.call("stop", metadata=self.metadata)["ok"])
                        if ended:
                            self.child = replace(self.child, status="completed", thread_status="idle")
                        result = self.fresh()
                    self.assertEqual(result.get("status"), "reviews_ended" if ended else "waiting", result)
                    self.assertEqual(self.effects, [] if ended else ["create", "arm"])
                    self.assertEqual(self.journal.read()[0], 1 if ended else 2)
                    self.assertEqual(self.journal.read_rounds(), rounds)
                    self.assertIsNone(worker.repository.read().worker_stopped_at)
                    self.close_all()

    def test_historical_round_restart_still_requires_saved_exit_proof(self):
        for saved in (False, True):
            with self.subTest(saved=saved):
                self.setUp()
                self.prepare_round()
                self.previous(save=saved)
                restarted = self.make()
                with mock.patch.object(restarted, "_continuation", return_value=self.validate.return_value):
                    result = self.fresh(session=restarted)
                self.assertEqual(result["ok"], saved, result)
                self.assertEqual(self.effects, ["create", "arm"] if saved else [])
                self.assertEqual(self.journal.read()[0], 2 if saved else 1)
                self.close_all()

    def test_historical_round_rechecks_worker_identity_after_cleanup(self):
        self.prepare_round()
        self.previous(save=False)
        worker = self.worker()
        original = self.session.managed._cleanup
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            worker._owned_worker_id = str(uuid4())
            return result
        with mock.patch.object(worker.repository, "confirm_worker_stopped", side_effect=OSError()), \
                mock.patch.object(self.session.managed, "_cleanup", side_effect=changed):
            result = self.fresh()
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read(), (1, replace(self.old, phase="closed")))

    def test_unknown_effects_missing_or_mismatched_exit_proof_never_replace(self):
        for change in (
                {"status": "dispatching"}, {"status": "unknown"},
                {"timer_phase": "active"}, {"timer_phase": "arming"},
                {"timer_phase": "deleting"}, {"timer_phase": "pausing"}, {"timer_phase": "unknown"},
                {"worker_stopped_at": None, "worker_stop_basis": None, "worker_stop_identity": None},
                {"original_parent_turn": str(uuid4())}):
            with self.subTest(change=tuple(change)):
                self.setUp()
                direct = self.previous()
                if "timer_phase" in change:
                    change = {"status": "suppressed", "acknowledged_turn": None, **change}
                self.rewrite(replace(direct, **change))
                before = self.journal.path.read_bytes()
                self.assertFalse(self.fresh(session=self.make())["ok"])
                self.assertEqual(self.effects, [])
                self.assertEqual(self.journal.path.read_bytes(), before)
                self.close_all()

    def test_same_generation_or_changed_project_binding_never_auto_close(self):
        for change in ({"target_generation": fixtures.BINDING.target_generation, "target_value": "changed"},
                       {"project_id": "other-project"}, {"project_path_hash": "f" * 64},
                       {"project_binding_generation": fixtures.BINDING.project_binding_generation + 1},
                       {"parent_thread_id": str(uuid4())}):
            with self.subTest(change=tuple(change)):
                self.setUp()
                self.previous()
                self.basis = replace(self.basis, **change)
                self.assertFalse(self.fresh()["ok"])
                self.assertEqual(self.effects, [])
                self.assertEqual(self.journal.read()[1], self.old)
                self.close_all()

    def test_already_ended_new_review_closes_old_request_without_new_wait(self):
        self.previous()
        self.child = replace(self.child, status="completed", thread_status="idle")
        result = self.fresh()
        self.assertEqual(result["status"], "reviews_ended", result)
        self.assertFalse(result["parent_may_end"])
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read(), (1, replace(self.old, phase="closed")))

    def test_confirmed_paused_timer_uses_existing_cleanup_once(self):
        direct = self.previous()
        self.rewrite(replace(direct, status="suppressed", timer_phase="paused", acknowledged_turn=None))
        host = self.hosts[self.old.automation_id]
        host.value = replace(host.value, status="PAUSED")
        host.deleted = False
        result = self.fresh(session=self.make())
        self.assertEqual(result["status"], "waiting", result)
        self.assertEqual(self.effects, ["delete", "create", "arm"])
        self.assertEqual(self.journal.read()[0], 2)

    def test_closed_phase_does_not_replace_missing_worker_exit_proof(self):
        direct = self.previous()
        self.assertTrue(self.call("stop", metadata=self.metadata)["ok"])
        self.rewrite(replace(direct, worker_stopped_at=None, worker_stop_basis=None, worker_stop_identity=None))
        self.assertFalse(self.fresh(session=self.make())["ok"])
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read()[0], 1)

    def test_local_worker_identity_conflict_rejects_saved_proof(self):
        self.previous()
        worker = self.worker()
        with mock.patch.object(worker, "_owned_worker_id", str(uuid4())):
            self.assertFalse(self.fresh()["ok"])
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read()[1], self.old)

    def test_parallel_call_cannot_duplicate_transition_or_create(self):
        self.previous()
        entered, released = threading.Event(), threading.Event()
        original = self.session.managed._cleanup
        result = []
        def gated(*args, **kwargs):
            entered.set()
            if not released.wait(5):
                raise AssertionError("test release missing")
            return original(*args, **kwargs)
        with mock.patch.object(self.session.managed, "_cleanup", side_effect=gated):
            first = threading.Thread(target=lambda: result.append(self.fresh()))
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                second = self.fresh(session=self.make())
                self.assertFalse(second["ok"], second)
                self.assertEqual(self.effects, [])
            finally:
                released.set()
                first.join(5)
        self.assertFalse(first.is_alive())
        self.assertEqual(result[0]["status"], "waiting", result)
        self.assertEqual(self.effects, ["create", "arm"])
        self.assertEqual(self.journal.read()[0], 2)

    def test_restart_after_closed_before_append_safely_creates_one_new_attempt(self):
        self.previous()
        with mock.patch.object(RequestRepository, "append", side_effect=RepositoryError("state_unreadable")):
            self.assertFalse(self.fresh()["ok"])
        self.assertEqual(self.journal.read(), (1, replace(self.old, phase="closed")))
        self.assertEqual(self.effects, [])
        self.assertEqual(self.fresh(session=self.make())["status"], "waiting")
        self.assertEqual(self.effects, ["create", "arm"])
        self.assertEqual(self.journal.read()[0], 2)

    def test_restart_after_unknown_append_outcome_never_replays_creation(self):
        self.previous()
        original = RequestRepository.append
        def interrupted(repository, *args):
            original(repository, *args)
            raise RepositoryError("state_unreadable")
        with mock.patch.object(RequestRepository, "append", new=interrupted):
            self.assertFalse(self.fresh()["ok"])
        self.assertEqual(self.journal.read()[0], 2)
        self.assertEqual(self.journal.read()[1].phase, "creating")
        self.assertFalse(self.fresh(session=self.make())["ok"])
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read()[0], 2)

    def test_interrupted_closing_is_not_replayed_as_confirmed_cleanup(self):
        self.previous()
        original = RequestRepository.advance
        def interrupted(repository, lease, phase, **kwargs):
            result = original(repository, lease, phase, **kwargs)
            if phase == "closing":
                raise RepositoryError("state_unreadable")
            return result
        with mock.patch.object(RequestRepository, "advance", new=interrupted):
            self.assertFalse(self.fresh()["ok"])
        self.assertEqual(self.journal.read(), (1, replace(self.old, phase="closing")))
        self.assertFalse(self.fresh(session=self.make())["ok"])
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read()[0], 1)

    def test_basis_drift_after_cleanup_never_creates_a_new_attempt(self):
        for ended in (False, True):
            with self.subTest(ended=ended):
                self.setUp()
                self.previous()
                if ended:
                    self.child = replace(self.child, status="completed", thread_status="idle")
                original = self.session.managed._cleanup
                def changed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    self.basis = replace(self.basis, target_generation=self.basis.target_generation + 1)
                    return result
                with mock.patch.object(self.session.managed, "_cleanup", side_effect=changed):
                    self.assertFalse(self.fresh()["ok"])
                self.assertEqual(self.effects, [])
                self.assertEqual(self.journal.read(), (1, replace(self.old, phase="closed")))
                self.close_all()


if __name__ == "__main__":
    unittest.main()
