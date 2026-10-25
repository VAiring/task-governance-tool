"""Managed progress polling is bounded independently of answer persistence."""

from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.test_review_wait_service import BINDING, CHILD, TURN, PARENT, NOW, FakeHost
from tools.review_wait_controller import ReviewWaitController, Reviewer
from tools.review_wait_direct import DirectError, DirectProbe, binding_digest
from tools.review_wait_direct_repository import DirectRecord, instant
from tools.review_wait_host import ChildTurn
from tools.review_wait_repository import ObservationBusy, ReviewWaitRepository
from tools.review_wait_runtime import reservation
from tools import review_wait_repository as repository_module
from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe


SECOND_CHILD = "77777777-7777-4777-8777-777777777777"
SECOND_TURN = "88888888-8888-4888-8888-888888888888"
PARENT_TURN = "55555555-5555-4555-8555-555555555555"
PROBE = "66666666-6666-4666-8666-666666666666"


class PollClock:
    def __init__(self):
        self.elapsed = 0
        self.begin = NOW.replace(microsecond=0)

    def now(self):
        return self.begin + timedelta(seconds=self.elapsed)


class PollStop:
    """Deterministic wait boundaries without threads or wall-clock sleeps."""

    def __init__(self, clock, limit, before_wait, after_wait=None):
        self.clock, self.limit = clock, limit
        self.before_wait, self.after_wait = before_wait, after_wait
        self.waits = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.before_wait()
        self.waits.append(seconds)
        self.clock.elapsed += seconds
        if self.after_wait is not None:
            self.after_wait(len(self.waits))
        if len(self.waits) >= self.limit:
            self.stopped = True
        return self.stopped


class ManagedPollingTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="taskgov-polling-")))
        self.clock = PollClock()
        self.reviewers = (Reviewer(CHILD, TURN), Reviewer(SECOND_CHILD, SECOND_TURN))
        self.controller = ReviewWaitController(BINDING, self.reviewers, reservation(FakeHost().value))
        self.path = root / "wait.sqlite"
        ReviewWaitRepository.create(self.path, self.controller)
        self.current_binding = Mock(return_value=BINDING)
        self.finalization_factory = Mock(return_value=None)
        self.full_tracking = Mock(side_effect=AssertionError("full tracking during progress polling"))
        self.guard = Mock(return_value=False)
        self.tracking = {
            reviewer.reviewer_id: dict(boundary_turn_id=reviewer.turn_id, frozen=False,
                source_identity=None, terminal="unknown")
            for reviewer in self.reviewers
        }
        self.tracking_reader = Mock(side_effect=lambda: deepcopy(self.tracking))
        self.collect_times = []
        self.collect_action = None

        def collect(host, controller, reviewer_id, **options):
            self.collect_times.append((reviewer_id, self.clock.elapsed))
            if self.collect_action is not None:
                self.collect_action(reviewer_id)

        self.collector = Mock(side_effect=collect)
        self.polling_factory = Mock(return_value=(self.guard, self.tracking_reader, None))
        self.worker = ManagedDirectProbe(self.path, self.current_binding, Mock(), self.clock.now,
            finalization_factory=self.finalization_factory,
            answer_tracking=self.full_tracking, tracked_answer_collector=self.collector,
            polling_factory=self.polling_factory)
        self.record = DirectRecord(PROBE, binding_digest(self.controller), PARENT_TURN,
            "waiting", instant(self.clock.now()), instant(self.clock.now() + timedelta(minutes=20)),
            version=2, timer_phase="arming",
            timer_rule="FREQ=DAILY;BYHOUR=0;BYMINUTE=10;BYSECOND=0;COUNT=1",
            timer_updated_at=1, timer_due_at=instant(self.clock.now() + timedelta(minutes=10)))
        self.worker.repository.create_record(self.record)
        self.record = self.worker.repository.update(PROBE, timer_phase="active", timer_updated_at=2)
        self.children = {reviewer.reviewer_id: ChildTurn(reviewer.reviewer_id, reviewer.turn_id,
            "inProgress", "active") for reviewer in self.reviewers}
        self.parent = ChildTurn(PARENT, PARENT_TURN, "completed", "idle")
        self.host = Mock()
        self.parent_action = None

        def parent():
            self.assert_observation_connections_closed()
            if self.parent_action is not None:
                self.parent_action()
            return self.parent

        def child(reviewer_id):
            self.assert_observation_connections_closed()
            return self.children[reviewer_id]

        self.host.read_parent.side_effect = parent
        self.host.read_child.side_effect = child
        self.dispatch = self.enterContext(patch.object(self.worker, "_dispatch", return_value=None))
        self.cleanup = self.enterContext(patch.object(self.worker, "_cleanup"))
        self.failure = self.enterContext(patch.object(self.worker, "_on_failure", return_value=False))
        self.connections = []
        self.connection_calls = []
        connect = repository_module.connect_traced

        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            self.connections.append(connection)
            self.connection_calls.append((args[0], kwargs))
            return connection

        self.enterContext(patch.object(repository_module, "connect_traced", side_effect=tracked_connect))
        self.direct_observation = self.enterContext(patch.object(self.worker.repository,
            "read_observation", wraps=self.worker.repository.read_observation))

    def assert_observation_connections_closed(self):
        for connection in self.connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        self.connections.clear()
        # Also prove these reads cannot hold up another Task/operational writer.
        for path in (self.path, self.worker.repository.path):
            with closing(sqlite3.connect(path, timeout=0)) as connection:
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()

    def run_polls(self, count=100, after_wait=None):
        stop = PollStop(self.clock, count, self.assert_observation_connections_closed, after_wait)
        self.worker._stop = stop
        with patch("task_governance_tool.review_wait_runtime.review_wait_direct.time.monotonic",
                   side_effect=lambda: self.clock.elapsed):
            # This exercises the shared wait loop and actual managed progress
            # hooks. Receipt observation after sending has its separate tests.
            DirectProbe._run(self.worker, self.host, self.record, 1200)
        self.assert_observation_connections_closed()
        return stop

    def ended(self, reviewer_id, *, status="completed"):
        current = self.children[reviewer_id]
        self.children[reviewer_id] = ChildTurn(reviewer_id, current.turn_id, status, "idle")

    def freeze(self, reviewer_id):
        self.tracking[reviewer_id].update(frozen=True, terminal="completed", source_identity={
            "reviewer_session_id": reviewer_id,
            "turn_id": self.children[reviewer_id].turn_id,
            "message_id": "answer-final",
        })

    def assert_no_repeated_admission(self, *, expected_binding=1, expected_finalizer=1):
        self.assertEqual(expected_binding, self.current_binding.call_count)
        self.assertEqual(expected_finalizer, self.finalization_factory.call_count)
        self.polling_factory.assert_called_once()
        self.full_tracking.assert_not_called()

    def observation_journal(self):
        from task_governance_tool.review_finalization import Finalizer
        from task_governance_tool.review_finalization_repository import FinalizationRecord, FinalizationRepository
        root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=self.path.parent)))
        binding = replace(BINDING, task_id="tg_task_" + "1" * 16, execution_id="tg_execution_" + "2" * 16,
                          artifact_manifest_id="tg_artifact_manifest_" + "3" * 16,
                          target_kind="git_snapshot", target_base_revision="a" * 40)
        basis = asdict(binding)
        basis.pop("wait_id")
        basis.update(version=1, task_status="review_pending")
        journal = FinalizationRepository(root / "intent.sqlite")
        with journal.serial(initial=FinalizationRecord(basis, "reviews/packet.json", "c" * 64,
                ("reviews/one.json", "reviews/two.json"), "refs/heads/main")):
            pass

        class ObservationOnly:
            observe_reviewers = Finalizer.observe_reviewers

            def saved_results_ready(self):
                return False

        observer = ObservationOnly()
        observer.journal = journal
        observer.observe_reviewers = Mock(wraps=observer.observe_reviewers)
        return observer

    def observe_progress(self):
        self.worker._polling_sources = self.polling_factory.return_value
        with patch("task_governance_tool.review_wait_runtime.review_wait_direct.time.monotonic",
                   side_effect=lambda: self.clock.elapsed):
            return self.worker._observe_progress(self.host, self.controller, self.parent, self.record, 1200)

    def test_each_observation_is_durable_before_later_child_read_failure(self):
        original_tracking = deepcopy(self.tracking)
        cases = [(False, "completed", "idle")]
        cases += [(tracked, status, thread) for tracked in (False, True)
                  for status in ("failed", "interrupted") for thread in ("idle", "active")]
        for tracked, status, thread in cases:
            with self.subTest(tracked=tracked, status=status, thread=thread):
                self.tracking.clear()
                self.tracking.update(deepcopy(original_tracking) if tracked else {})
                self.worker._progress_seen.clear()
                observer = self.observation_journal()
                error = ValueError("private later child read failure")

                def child(reviewer_id):
                    self.assert_observation_connections_closed()
                    if reviewer_id == CHILD:
                        return ChildTurn(CHILD, TURN, status, thread)
                    retained = observer.journal.read()
                    self.assertEqual(((CHILD, TURN, status),), retained.reviewer_observations)
                    self.assertEqual(None if status == "completed" else "finalization_reviewer_failed",
                                     retained.reviewer_blocker)
                    raise error

                self.host.read_child.side_effect = child
                with patch.object(self.worker, "_finalizer", return_value=observer):
                    with self.assertRaises(ValueError) as raised:
                        self.observe_progress()
                self.assertIs(raised.exception, error)
                retained = observer.journal.read()
                self.assertEqual({(CHILD, TURN, status), (SECOND_CHILD, SECOND_TURN, "unknown")},
                                 set(retained.reviewer_observations))
                self.assertEqual("finalization_reviewer_" + ("unknown" if status == "completed" else "failed"),
                                 retained.reviewer_blocker)
                observer.observe_reviewers(((CHILD, TURN, "completed"), (SECOND_CHILD, SECOND_TURN, "completed")))
                self.assertEqual(retained, observer.journal.read())
                self.assertNotIn(b"private later child read failure", observer.journal.path.read_bytes())
                self.collector.assert_not_called()
                self.dispatch.assert_not_called()

    def test_untracked_changed_identity_retains_unknown_for_original_pair(self):
        self.tracking.clear()
        for child in (ChildTurn(CHILD, SECOND_TURN, "completed", "idle"),
                      ChildTurn(SECOND_CHILD, TURN, "completed", "idle")):
            with self.subTest(child=child):
                observer = self.observation_journal()
                self.host.read_child.reset_mock()
                self.host.read_child.side_effect = lambda reviewer_id: child
                with patch.object(self.worker, "_finalizer", return_value=observer):
                    with self.assertRaises(DirectError) as raised:
                        self.observe_progress()
                self.assertEqual("reviewer_turn_changed", raised.exception.code)
                self.assertEqual(((CHILD, TURN, "unknown"),), observer.journal.read().reviewer_observations)
                self.assertEqual("finalization_reviewer_unknown", observer.journal.read().reviewer_blocker)
                self.host.read_child.assert_called_once_with(CHILD)

    def test_tracked_later_completed_turn_is_not_journaled_as_boundary_completion(self):
        observer = self.observation_journal()
        self.children[CHILD] = ChildTurn(CHILD, SECOND_TURN, "completed", "idle")
        self.collect_action = self.freeze
        with patch.object(self.worker, "_finalizer", return_value=observer):
            self.assertFalse(self.observe_progress())
            self.assertEqual((), observer.journal.read().reviewer_observations)
            self.full_tracking.side_effect = lambda controller: deepcopy(self.tracking)
            self.worker._read_reviewer(self.host, self.controller, self.reviewers[0])
        self.assertEqual(((CHILD, SECOND_TURN, "completed"),), observer.journal.read().reviewer_observations)
        self.assertIsNone(observer.journal.read().reviewer_blocker)

    def test_unchanged_retained_failure_does_not_repeat_finalizer_work(self):
        observer = self.observation_journal()
        self.children[CHILD] = ChildTurn(CHILD, TURN, "failed", "active")
        with patch.object(self.worker, "_finalizer", return_value=observer) as finalizer:
            self.run_polls()
        self.assertEqual(2, finalizer.call_count)  # Initial readiness and one changed failure.
        observer.observe_reviewers.assert_called_once_with(((CHILD, TURN, "failed"),))
        self.assertEqual(1, self.current_binding.call_count)
        self.assertEqual(100, self.tracking_reader.call_count)
        self.full_tracking.assert_not_called()
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_cancellation_during_unavailable_read_prevents_unknown_journal_write(self):
        self.assert_unavailable_read_guard(expires=False)

    def test_expiry_during_unavailable_read_prevents_unknown_journal_write(self):
        self.assert_unavailable_read_guard(expires=True)

    def assert_unavailable_read_guard(self, *, expires):
        observer = self.observation_journal()
        error = ValueError("private unavailable child")

        def unavailable(reviewer_id):
            if expires:
                self.clock.elapsed = 1200
            else:
                self.worker.repository.update(PROBE, status="cancelled")
            raise error

        self.host.read_child.side_effect = unavailable
        with patch.object(self.worker, "_finalizer", return_value=observer):
            with self.assertRaises(ValueError) as raised:
                self.observe_progress()
        self.assertIs(error, raised.exception)
        self.assertEqual({}, self.worker._progress_seen)
        self.assertEqual((), observer.journal.read().reviewer_observations)
        self.assertIsNone(observer.journal.read().reviewer_blocker)
        observer.observe_reviewers.assert_not_called()
        self.host.read_child.assert_called_once_with(CHILD)
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()

    def test_active_reviewers_have_only_one_initial_admission_across_100_polls(self):
        stop = self.run_polls()
        self.assertEqual([10] * 100, stop.waits)
        self.assertEqual(100, self.host.read_parent.call_count)
        self.assertEqual(200, self.host.read_child.call_count)
        self.assertEqual(100, self.tracking_reader.call_count)
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()
        self.assert_no_repeated_admission()
        self.assertEqual(100, self.direct_observation.call_count)
        for path in (self.path, self.worker.repository.path):
            connections = [options for uri, options in self.connection_calls
                           if uri == path.as_uri() + "?mode=ro"]
            self.assertEqual(100, len(connections))
            self.assertTrue(all(options["timeout"] == 0 for options in connections))
        self.assertEqual(200, len(self.connection_calls))

    def test_compatibility_active_reviewers_share_one_direct_observation_per_tick(self):
        self.worker.polling_factory = None
        self.full_tracking.side_effect = lambda controller: {}
        self.run_polls()
        self.assertEqual(100, self.direct_observation.call_count)
        self.assertEqual(200, len(self.connection_calls))
        self.assertEqual(200, self.host.read_child.call_count)
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_legacy_ended_and_running_reviewers_keep_one_direct_observation_per_tick(self):
        self.worker = DirectProbe(self.path, self.current_binding, Mock(), self.clock.now)
        self.dispatch = self.enterContext(patch.object(self.worker, "_dispatch"))
        self.enterContext(patch.object(self.worker, "_cleanup"))
        self.failure = self.enterContext(patch.object(self.worker, "_on_failure", return_value=False))
        observe = self.enterContext(patch.object(self.worker.repository, "read_observation",
                                                wraps=self.worker.repository.read_observation))
        self.ended(CHILD)
        self.run_polls()
        self.assertEqual(100, observe.call_count)
        self.assertEqual(200, len(self.connection_calls))
        self.assertEqual(200, self.host.read_child.call_count)
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_one_completed_answer_does_not_start_permanent_db_monitoring(self):
        self.collect_action = self.freeze

        def transition(tick):
            if tick == 1:
                self.ended(CHILD)

        self.run_polls(after_wait=transition)
        self.assertEqual([(CHILD, 10)], self.collect_times)
        self.assertEqual(2, sum(call.args == (CHILD,) for call in self.host.read_child.call_args_list))
        self.assertEqual(100, sum(call.args == (SECOND_CHILD,) for call in self.host.read_child.call_args_list))
        self.assertEqual(101, self.tracking_reader.call_count)
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()
        self.assert_no_repeated_admission(expected_binding=2)

    def test_control_change_refreshes_admission_once_without_starting_answer_polling(self):
        self.guard.side_effect = [False, True] + [False] * 98
        self.run_polls()
        self.assert_no_repeated_admission(expected_binding=2, expected_finalizer=2)
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_untracked_progress_keeps_exact_inspect_observations_without_finalizer_polling(self):
        self.tracking.clear()
        self.run_polls(count=3)
        observed = self.worker._reviewer_observations
        self.assertEqual(set(self.children), set(observed))
        for child_id, value in observed.items():
            self.assertEqual(self.children[child_id].turn_id, value["turn_id"])
            self.assertEqual("inProgress", value["status"])
            self.assertFalse(value["ended"])
        self.assert_no_repeated_admission()

    def test_parent_busy_does_not_repeat_pending_answer_collection(self):
        for reviewer in self.reviewers:
            self.ended(reviewer.reviewer_id)
        self.parent = ChildTurn(PARENT, PARENT_TURN, "inProgress", "active")
        self.run_polls()
        self.assertEqual([(CHILD, 0), (SECOND_CHILD, 0)], self.collect_times)
        self.assertEqual(101, self.tracking_reader.call_count)
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()
        self.assert_no_repeated_admission(expected_binding=3)

    def test_idle_parent_rechecks_only_missing_answer_every_ten_seconds_then_dispatches_once(self):
        for reviewer in self.reviewers:
            self.ended(reviewer.reviewer_id)
        self.freeze(CHILD)

        def collect(reviewer_id):
            if self.clock.elapsed == 20:
                self.freeze(reviewer_id)

        self.collect_action = collect
        stop = self.run_polls(count=10)
        self.assertEqual([(SECOND_CHILD, 0), (SECOND_CHILD, 10), (SECOND_CHILD, 20)], self.collect_times)
        self.assertEqual([10, 10], stop.waits)
        self.dispatch.assert_called_once()
        self.failure.assert_not_called()
        self.full_tracking.assert_not_called()

    def test_unchanged_pending_candidate_does_not_repeat_collection_or_admission(self):
        for reviewer in self.reviewers:
            self.ended(reviewer.reviewer_id)
        seen = set()
        def candidate(host, reviewer):
            changed = reviewer not in seen
            seen.add(reviewer)
            return changed
        probe = Mock(side_effect=candidate)
        self.polling_factory.return_value = self.guard, self.tracking_reader, probe
        self.run_polls()
        self.assertEqual([(CHILD, 0), (SECOND_CHILD, 0)], self.collect_times)
        self.assertEqual(200, probe.call_count)
        self.assert_no_repeated_admission(expected_binding=3)
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_changed_pending_candidate_runs_fresh_collection_then_finishes(self):
        for reviewer in self.reviewers:
            self.ended(reviewer.reviewer_id)
        self.freeze(CHILD)
        probe = Mock(side_effect=lambda host, reviewer: self.clock.elapsed in {0, 20})
        self.polling_factory.return_value = self.guard, self.tracking_reader, probe
        self.collect_action = lambda reviewer: self.freeze(reviewer) if self.clock.elapsed == 20 else None
        stop = self.run_polls(count=10)
        self.assertEqual([(SECOND_CHILD, 0), (SECOND_CHILD, 20)], self.collect_times)
        self.assertEqual([10, 10], stop.waits)
        self.assert_no_repeated_admission(expected_binding=3)
        self.dispatch.assert_called_once()

    def test_cancel_during_candidate_read_prevents_collection(self):
        self.ended(CHILD)
        def candidate(host, reviewer):
            self.worker.repository.update(PROBE, status="cancelled")
            return True
        self.polling_factory.return_value = self.guard, self.tracking_reader, candidate
        self.run_polls(count=1)
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()
        self.assertEqual("cancelled", self.worker.repository.read().status)

    def test_cancel_during_active_child_read_is_seen_at_next_tick_without_effects(self):
        def child(reviewer_id):
            self.assert_observation_connections_closed()
            if reviewer_id == CHILD:
                self.worker.repository.update(PROBE, status="cancelled")
            return self.children[reviewer_id]

        self.host.read_child.side_effect = child
        stop = self.run_polls(count=5)
        self.assertEqual([10], stop.waits)
        self.assertEqual(2, self.direct_observation.call_count)
        self.assertEqual(2, self.host.read_child.call_count)
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()
        self.assertEqual("cancelled", self.worker.repository.read().status)

    def test_stop_during_child_read_prevents_next_read_without_database_recheck(self):
        def child(reviewer_id):
            self.assert_observation_connections_closed()
            self.worker._stop.set()
            return self.children[reviewer_id]

        self.host.read_child.side_effect = child
        self.run_polls(count=5)
        self.assertEqual(1, self.direct_observation.call_count)
        self.host.read_child.assert_called_once_with(CHILD)
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_cancel_during_child_read_prevents_terminal_journal_write(self):
        self.tracking.clear()
        self.ended(CHILD)

        def child(reviewer_id):
            self.assert_observation_connections_closed()
            if reviewer_id == CHILD:
                self.worker.repository.update(PROBE, status="cancelled")
            return self.children[reviewer_id]

        self.host.read_child.side_effect = child
        finalizer = Mock()
        finalizer.saved_results_ready.return_value = False
        with patch.object(self.worker, "_finalizer", return_value=finalizer):
            self.run_polls(count=1)
        finalizer.observe_reviewers.assert_not_called()
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_cancel_during_failed_child_read_prevents_failure_journal_write(self):
        self.ended(CHILD, status="failed")

        def child(reviewer_id):
            self.assert_observation_connections_closed()
            if reviewer_id == CHILD:
                self.worker.repository.update(PROBE, status="cancelled")
            return self.children[reviewer_id]

        self.host.read_child.side_effect = child
        finalizer = Mock()
        finalizer.saved_results_ready.return_value = False
        with patch.object(self.worker, "_finalizer", return_value=finalizer):
            self.run_polls(count=1)
        finalizer.observe_reviewers.assert_not_called()
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_compatibility_cancel_between_reviewers_prevents_next_collection(self):
        self.worker.polling_factory = None
        self.full_tracking.side_effect = lambda controller: deepcopy(self.tracking)
        def collect(reviewer):
            self.freeze(reviewer)
            self.worker.repository.update(PROBE, status="cancelled")
        self.collect_action = collect
        finalizer = Mock()
        finalizer.saved_results_ready.return_value = False
        with patch.object(self.worker, "_finalizer", return_value=finalizer):
            self.run_polls(count=1)
        self.assertEqual([(CHILD, 0)], self.collect_times)
        finalizer.observe_reviewers.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()
        self.assertEqual("cancelled", self.worker.repository.read().status)

    def test_compatibility_cancel_during_child_read_prevents_terminal_journal_write(self):
        self.worker.polling_factory = None
        self.full_tracking.side_effect = lambda controller: {}
        self.ended(CHILD)

        def child(reviewer_id):
            self.assert_observation_connections_closed()
            if reviewer_id == CHILD:
                self.worker.repository.update(PROBE, status="cancelled")
            return self.children[reviewer_id]

        self.host.read_child.side_effect = child
        finalizer = Mock()
        finalizer.saved_results_ready.return_value = False
        with patch.object(self.worker, "_finalizer", return_value=finalizer):
            self.run_polls(count=1)
        finalizer.observe_reviewers.assert_not_called()
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_deadline_during_candidate_read_prevents_collection(self):
        self.ended(CHILD)
        def candidate(host, reviewer):
            self.clock.elapsed = 1200
            return True
        self.polling_factory.return_value = self.guard, self.tracking_reader, candidate
        self.run_polls(count=1)
        self.collector.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()
        self.assertEqual("expired", self.worker.repository.read().status)

    def test_busy_observation_defers_without_failure_cleanup_or_effect(self):
        for reviewer in self.reviewers:
            self.freeze(reviewer.reviewer_id)
        read = self.worker.repository.read_observation
        attempts = []

        def observe():
            attempts.append(self.clock.elapsed)
            if len(attempts) == 1:
                raise ObservationBusy()
            return read()

        def after_wait(tick):
            self.assertEqual(1, tick)
            self.cleanup.assert_not_called()
            self.failure.assert_not_called()
            self.dispatch.assert_not_called()
            self.assertEqual("waiting", self.worker.repository.read().status)

        with patch.object(self.worker.repository, "read_observation", side_effect=observe):
            stop = self.run_polls(count=5, after_wait=after_wait)
        self.assertEqual([10], stop.waits)
        self.dispatch.assert_called_once()
        self.failure.assert_not_called()

    def test_cancel_during_host_read_prevents_dispatch(self):
        for reviewer in self.reviewers:
            self.freeze(reviewer.reviewer_id)
        self.parent_action = lambda: self.worker.repository.update(PROBE, status="cancelled")
        self.run_polls(count=1)
        self.assertEqual("cancelled", self.worker.repository.read().status)
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_busy_observations_do_not_extend_the_original_deadline(self):
        self.clock.elapsed = 1190
        with patch.object(self.worker.repository, "read_observation", side_effect=ObservationBusy()):
            stop = self.run_polls(count=5)
        self.assertEqual([10], stop.waits)
        self.assertEqual("expired", self.worker.repository.read().status)
        self.host.read_parent.assert_not_called()
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_deadline_during_host_read_prevents_dispatch(self):
        for reviewer in self.reviewers:
            self.freeze(reviewer.reviewer_id)
        self.parent_action = lambda: setattr(self.clock, "elapsed", 1200)
        self.run_polls(count=1)
        self.assertEqual("expired", self.worker.repository.read().status)
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_cancelled_record_at_deadline_does_not_attempt_an_expiry_transition(self):
        self.worker.repository.update(PROBE, status="cancelled")
        self.clock.elapsed = 1200
        self.run_polls(count=1)
        self.assertEqual("cancelled", self.worker.repository.read().status)
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()

    def test_failed_reviewer_is_not_a_forever_pending_answer(self):
        self.ended(CHILD, status="failed")
        self.freeze(SECOND_CHILD)
        self.run_polls(count=1)
        self.collector.assert_not_called()
        self.failure.assert_called_once()
        self.assertEqual("child_status_unavailable", self.failure.call_args.args[2].code)
        self.assertEqual("suppressed", self.worker.repository.read().status)
        self.dispatch.assert_not_called()

    def test_failed_reviewer_waits_for_remaining_review_and_idle_parent(self):
        self.ended(CHILD, status="interrupted")
        self.parent = ChildTurn(PARENT, PARENT_TURN, "inProgress", "active")
        self.collect_action = self.freeze
        def transition(tick):
            self.failure.assert_not_called()
            self.cleanup.assert_not_called()
            if tick == 1:
                self.ended(SECOND_CHILD)
            if tick == 3:
                self.parent = ChildTurn(PARENT, PARENT_TURN, "completed", "idle")
        stop = self.run_polls(count=10, after_wait=transition)
        self.assertEqual([10, 10, 10], stop.waits)
        self.assertEqual([(SECOND_CHILD, 10)], self.collect_times)
        self.failure.assert_called_once()
        self.assertEqual("child_status_unavailable", self.failure.call_args.args[2].code)
        self.dispatch.assert_not_called()

    def test_completed_saved_turn_with_active_chat_is_not_an_ended_reviewer(self):
        self.children[CHILD] = ChildTurn(CHILD, TURN, "completed", "active")
        self.ended(SECOND_CHILD)
        self.run_polls()
        self.assertEqual([(SECOND_CHILD, 0)], self.collect_times)
        self.dispatch.assert_not_called()
        self.failure.assert_not_called()
        self.assert_no_repeated_admission(expected_binding=2)


if __name__ == "__main__":
    unittest.main()
