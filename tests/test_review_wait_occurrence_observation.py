"""Initial admission races and real operational contention in bounded polling."""

from contextlib import closing
from dataclasses import replace
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests import test_review_wait_control_observation as controls
from tests import test_review_wait_polling as polling
from tests.test_review_wait_continuation_transition import pending
from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository
from task_governance_tool.review_wait_runtime.managed_wait import basis_digest
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.request_repository import RequestRecord, RequestRepository
from task_governance_tool.review_wait_runtime.review_wait_basis import BasisError
from task_governance_tool.review_wait_runtime.review_wait_repository import ObservationBusy, RepositoryError


class OccurrenceObservationTests(unittest.TestCase):
    def make_polling(self):
        flow = polling.ManagedPollingTests(methodName="runTest")
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        control = controls.ControlObservationTests(methodName="runTest")
        control.setUp()
        self.addCleanup(control.doCleanups)
        root = flow.path.parent
        paths = SimpleNamespace(database=control.path,
            review_continuation_store=lambda *args: root / "transition.sqlite",
            review_wait_request_store=lambda *args: root / "request.sqlite")
        session = ProjectReviewWaitSession(ProjectConfig(root, root / "server.mjs", root, "UTC"))
        session._location = Mock(return_value=paths)
        session._policy_enabled = Mock(return_value=True)
        session._tracking_observer = Mock(return_value=flow.tracking_reader)
        session._control_observer = Mock(return_value=control.observe)
        automation = flow.controller.reservation.timer_id
        flow.polling_factory.side_effect = lambda controller, **options: session._polling_sources(
            controller.binding, automation, tuple(row.reviewer_id for row in controller.reviewers), **options)

        def binding():
            with closing(sqlite3.connect(control.path)) as connection:
                status = connection.execute("SELECT status FROM tasks WHERE task_id='a'").fetchone()[0]
                owner = connection.execute("SELECT owner_session_id FROM task_ownership WHERE task_id='a'").fetchone()[0]
            if status != "in_progress" or owner != "parent":
                raise BasisError()
            return polling.BINDING

        flow.current_binding.side_effect = binding
        return flow, control, session, paths

    def test_initial_baseline_is_followed_by_admission_without_periodic_readmission(self):
        flow, _, _, _ = self.make_polling()
        stop = flow.run_polls()
        self.assertEqual([10] * 100, stop.waits)
        flow.assert_no_repeated_admission(expected_binding=2, expected_finalizer=2)
        flow.failure.assert_not_called()

    def test_pause_or_owner_change_before_initial_baseline_is_not_consumed(self):
        for sql in ("UPDATE tasks SET status='paused' WHERE task_id='a'",
                    "UPDATE task_ownership SET owner_session_id='new-parent' WHERE task_id='a'"):
            with self.subTest(sql=sql):
                flow, control, session, _ = self.make_polling()

                def create_observer(*args):
                    self.assertEqual(1, flow.current_binding.call_count)
                    control.write(sql)
                    return control.observe

                session._control_observer.side_effect = create_observer
                stop = flow.run_polls(count=3)
                self.assertEqual([], stop.waits)
                self.assertEqual(2, flow.current_binding.call_count)
                flow.failure.assert_called_once()
                flow.dispatch.assert_not_called()
                flow.host.read_parent.assert_not_called()
                self.assertEqual("suppressed", flow.worker.repository.read().status)

    def test_change_during_initial_control_snapshot_is_not_consumed(self):
        flow, control, _, _ = self.make_polling()
        read = control.observe._read_control

        def changed_snapshot():
            result = read()
            control.write("UPDATE tasks SET status='paused' WHERE task_id='a'")
            return result

        control.observe._read_control = changed_snapshot
        flow.run_polls(count=3)
        self.assertEqual(2, flow.current_binding.call_count)
        flow.failure.assert_called_once()
        flow.dispatch.assert_not_called()
        self.assertIsNone(control.observe._previous_hint)

    def test_change_after_baseline_during_fresh_admission_remains_pending(self):
        flow, control, _, _ = self.make_polling()
        read_binding = flow.current_binding.side_effect

        def admission_then_change():
            result = read_binding()
            if flow.current_binding.call_count == 2:
                control.write("UPDATE tasks SET status='paused' WHERE task_id='a'")
            return result

        flow.current_binding.side_effect = admission_then_change
        stop = flow.run_polls(count=3)
        self.assertEqual([10], stop.waits)
        self.assertEqual(3, flow.current_binding.call_count)
        flow.failure.assert_called_once()
        flow.dispatch.assert_not_called()
        self.assertEqual("suppressed", flow.worker.repository.read().status)

    def continued_polling(self):
        flow, _, session, paths = self.make_polling()
        round_record = replace(pending(), wait_basis_digest=basis_digest(polling.BINDING))
        transition = TransitionRepository(paths.review_continuation_store())
        with transition.serial(create=True) as lease:
            fenced = transition.fence(lease, round_record)
            settled = transition.advance(lease, fenced, phase="settled")
            prepared = transition.advance(lease, settled, phase="prepared", packet_path=settled.packet_path,
                packet_digest="c" * 64, result_paths=settled.result_paths)
        request = RequestRepository(paths.review_wait_request_store())
        with request.serial(create=True) as lease:
            request.install_round(lease, prepared)
            request.append(lease, RequestRecord(basis_digest(polling.BINDING), polling.PARENT_TURN,
                tuple((row.reviewer_id, row.turn_id) for row in flow.reviewers),
                continuation_id=prepared.continuation_id, continuation_sequence=prepared.continuation_sequence))
            request.advance(lease, "created", automation_id=flow.controller.reservation.timer_id)
            request.advance(lease, "prepared")
            request.advance(lease, "started")
        flow.worker.occurrence_guard = lambda controller: session._wait_occurrence_current(
            controller.binding, controller.reservation.timer_id)
        # The lock overlaps an already-admitted worker's ordinary next poll,
        # not its initial or effect-time admission.
        flow.worker._prepare_observation(flow.controller)
        return flow, session, transition, request

    def test_transition_and_request_busy_defer_then_resume_without_suppression_or_cleanup(self):
        for store in ("transition", "request"):
            with self.subTest(store=store):
                flow, _, transition, request = self.continued_polling()
                repository = transition if store == "transition" else request
                with closing(sqlite3.connect(repository.path, timeout=0)) as writer:
                    writer.execute("BEGIN EXCLUSIVE")

                    def released(tick):
                        self.assertEqual(1, tick)
                        flow.failure.assert_not_called()
                        flow.cleanup.assert_not_called()
                        flow.dispatch.assert_not_called()
                        flow.host.read_parent.assert_not_called()
                        self.assertEqual("waiting", flow.worker.repository.read().status)
                        writer.rollback()
                        for reviewer in flow.reviewers:
                            flow.freeze(reviewer.reviewer_id)

                    stop = flow.run_polls(count=3, after_wait=released)
                self.assertEqual([10], stop.waits)
                flow.failure.assert_not_called()
                flow.dispatch.assert_called_once()
                self.assertEqual("waiting", flow.worker.repository.read().status)
                self.assertEqual(1, flow.host.read_parent.call_count)

    def test_operational_observation_preserves_validation_and_ordinary_busy_errors(self):
        _, _, transition, request = self.continued_polling()
        for repository, observe, ordinary in (
                (transition, transition.read_observation, transition.read),
                (request, request.read_admission_observation, request.read_admission)):
            with self.subTest(store=repository.path.name):
                before = repository.path.read_bytes()
                self.assertEqual(ordinary(), observe())
                self.assertEqual(before, repository.path.read_bytes())
                with closing(sqlite3.connect(repository.path, timeout=0)) as writer:
                    writer.execute("BEGIN EXCLUSIVE")
                    with self.assertRaises(ObservationBusy):
                        observe()
                    with self.assertRaises(RepositoryError) as caught:
                        ordinary()
                    self.assertNotIsInstance(caught.exception, ObservationBusy)
                    writer.rollback()
                with closing(sqlite3.connect(repository.path)) as writer, writer:
                    writer.execute("PRAGMA user_version=99")
                with self.assertRaises(RepositoryError) as caught:
                    observe()
                self.assertNotIsInstance(caught.exception, ObservationBusy)
                self.assertEqual("state_unreadable", caught.exception.code)

    def test_observed_occurrence_identity_mismatch_still_stops_the_worker(self):
        flow, _, _, request = self.continued_polling()
        with closing(sqlite3.connect(request.path)) as connection, connection:
            connection.execute("UPDATE attempts SET payload=replace(payload, ?, ?)",
                ('"automation_id":"' + flow.controller.reservation.timer_id + '"',
                 '"automation_id":"other-timer"'))
        flow.run_polls(count=3)
        flow.failure.assert_called_once()
        self.assertEqual("review_continuation_conflict", flow.failure.call_args.args[2].reason)
        flow.dispatch.assert_not_called()
        self.assertEqual("suppressed", flow.worker.repository.read().status)


if __name__ == "__main__":
    unittest.main()
