"""Owned worker tests with a real controller/store and an offline fake host."""

from dataclasses import asdict
from pathlib import Path
import tempfile
import threading
import time
import unittest
from uuid import uuid4

from tests.test_review_wait_host import wait_result
from tests.test_review_wait_runtime import BINDING, NOW, REVIEWERS, FakeHost, appointment
from tools.review_wait_controller import Reviewer, ReviewWaitController
from tools.review_wait_host import ChildTurn, HostAdapterError, WaitSnapshot, parse_wait_threads
from tools.review_wait_observer import ObserverError, ReviewWaitObserver
from tools.review_wait_repository import ReviewWaitRepository
from tools.review_wait_runtime import ReviewWaitRuntime, reservation


class ObserverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-observer-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "wait.sqlite"
        self.host = FakeHost()
        self.repository = ReviewWaitRepository.create(self.path,
            ReviewWaitController(BINDING, REVIEWERS, reservation(self.host.value)))
        self.host.repository = self.repository
        self.runtime = ReviewWaitRuntime(self.repository, self.host, lambda: BINDING, lambda: NOW)
        self.release, self.wait_entered = threading.Event(), threading.Event()
        self.wait_calls, self.observations = [], []
        self.wait_override = None
        self.wait_delay = 0
        self.host.wait_children = self.wait_children
        original = self.runtime.observe

        def observe(**kwargs):
            self.observations.append(1)
            return original(**kwargs)

        self.runtime.observe = observe
        self.observer = None
        self.addCleanup(self.cleanup_worker)

    def cleanup_worker(self):
        self.release.set()
        if self.observer is not None:
            self.observer.stop()
            self.until(lambda: not self.observer.snapshot().worker_alive)

    def until(self, condition, seconds=5):
        end = time.monotonic() + seconds
        while not condition() and time.monotonic() < end:
            time.sleep(0.01)
        self.assertTrue(condition(), "offline worker did not reach expected state")

    def wait_children(self, children, *, timeout_ms, cursors):
        # Waiting is outside the same store's writer lease and DB transaction.
        with self.repository.writer():
            pass
        self.wait_calls.append((tuple(children), timeout_ms, dict(cursors)))
        self.wait_entered.set()
        self.release.wait(self.wait_delay)
        if self.wait_override is not None:
            return self.wait_override(children)
        return WaitSnapshot(tuple(self.host.turns[x] for x in children),
                            tuple((x, "cursor-" + x) for x in children), True)

    def start(self, **kwargs):
        self.observer = ReviewWaitObserver(self.runtime, join_timeout_seconds=0.1, **kwargs)
        self.observer.start()
        return self.observer

    def end_all(self):
        for reviewer, status in zip(REVIEWERS, ("completed", "failed")):
            self.host.turns[reviewer.reviewer_id] = ChildTurn(reviewer.reviewer_id,
                reviewer.turn_id, status, "idle")

    def test_initial_prearm_completion_is_recorded_without_wait_or_activation(self):
        self.end_all()
        observer = self.start()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual("ended", observer.snapshot().state)
        self.assertTrue(self.repository.read().controller.all_ended)
        self.assertEqual([], self.host.effects)
        self.assertEqual([], self.wait_calls)

    def test_candidate_causes_exact_read_and_only_one_shortening(self):
        self.runtime.arm(appointment())
        self.wait_delay = 5
        observer = self.start()
        self.assertTrue(self.wait_entered.wait(3))
        self.end_all()
        self.release.set()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual("ended", observer.snapshot().state)
        self.assertEqual(["arm", "shorten"], [x[0] for x in self.host.effects])
        self.assertEqual(2, len(self.observations))
        observer.start()
        self.assertEqual(["arm", "shorten"], [x[0] for x in self.host.effects])

    def test_unconfirmed_candidate_is_rechecked_until_exact_read_converges(self):
        self.runtime.arm(appointment())
        prior_all_ended = []

        def candidate(children):
            if len(self.wait_calls) >= 2:
                prior_all_ended.append(self.repository.read().controller.all_ended)
                self.end_all()
            return WaitSnapshot(tuple(ChildTurn(x, self.host.turns[x].turn_id, "completed", "idle")
                                      for x in children), tuple((x, "next-cursor") for x in children), False)

        self.wait_override = candidate
        observer = self.start()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual(3, len(self.observations))
        self.assertEqual([False], prior_all_ended)
        self.assertTrue(self.repository.read().controller.all_ended)
        self.assertEqual("ended", observer.snapshot().state)
        self.assertEqual({x: "next-cursor" for x in self.host.reviewer_ids}, self.wait_calls[1][2])
        self.assertEqual(["arm", "shorten"], [x[0] for x in self.host.effects])
        self.assertFalse(observer.stop().worker_alive)

    def test_different_actual_turn_stops_without_shortening(self):
        self.runtime.arm(appointment())
        self.wait_delay = 5
        observer = self.start()
        self.assertTrue(self.wait_entered.wait(3))
        self.host.turns["child-1"] = ChildTurn("child-1", "other-turn", "completed", "idle")
        self.release.set()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual(("error", "observation_failed"),
                         (observer.snapshot().state, observer.snapshot().reason))
        self.assertEqual(["arm"], [x[0] for x in self.host.effects])

    def test_cursor_reset_continues_with_replacement_cursor_until_all_ended(self):
        reviewers = tuple(Reviewer(str(uuid4()), str(uuid4())) for _ in range(2))
        self.repository = ReviewWaitRepository.create(self.path.with_name("reset.sqlite"),
            ReviewWaitController(BINDING, reviewers, reservation(self.host.value)))
        self.host.repository = self.repository
        self.host.reviewer_ids = tuple(r.reviewer_id for r in reviewers)
        self.host.turns = {r.reviewer_id: ChildTurn(r.reviewer_id, r.turn_id, "inProgress", "active")
                           for r in reviewers}
        self.runtime = ReviewWaitRuntime(self.repository, self.host, lambda: BINDING, lambda: NOW)
        self.runtime.arm(appointment())

        def polled(children):
            number = len(self.wait_calls)
            if number == 3:
                for reviewer in reviewers:
                    self.host.turns[reviewer.reviewer_id] = ChildTurn(
                        reviewer.reviewer_id, reviewer.turn_id, "completed", "idle")
            polls = []
            for child in children:
                observed = self.host.turns[child]
                poll = wait_result()["polls"][0]
                poll["thread"].update(id=child, status={"type": observed.thread_status})
                poll["latestTurn"].update(id=observed.turn_id, status=observed.status)
                poll["cursor"] = "old-cursor" if number == 1 else "replacement-cursor"
                if number == 2:
                    poll["cursorReset"] = True
                polls.append(poll)
            return parse_wait_threads({"timedOut": number < 3, "wake": None, "polls": polls}, children)

        self.wait_override = polled
        observer = self.start()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual("ended", observer.snapshot().state)
        self.assertEqual(3, len(self.wait_calls))
        self.assertEqual({}, self.wait_calls[0][2])
        self.assertEqual({r.reviewer_id: "old-cursor" for r in reviewers}, self.wait_calls[1][2])
        self.assertEqual({r.reviewer_id: "replacement-cursor" for r in reviewers}, self.wait_calls[2][2])
        self.assertTrue(self.repository.read().controller.all_ended)
        self.assertEqual(["arm", "shorten"], [x[0] for x in self.host.effects])

    def test_pending_checking_closed_do_not_start_observation(self):
        for phase in ("pending", "checking", "closed"):
            with self.subTest(phase=phase):
                path = self.path.with_name(phase + ".sqlite")
                repo = ReviewWaitRepository.create(path,
                    ReviewWaitController(BINDING, REVIEWERS, reservation(FakeHost().value)))
                fake = FakeHost()
                fake.repository = repo
                runtime = ReviewWaitRuntime(repo, fake, lambda: BINDING, lambda: NOW)
                fake.fail_after_write = phase == "pending"
                runtime.arm(appointment())
                if phase == "checking":
                    runtime.check(expected_arm=1, wake_id="wake", wake_time=appointment().due_at)
                if phase == "closed":
                    runtime.cancel(expected_arm=1)
                runtime.observe = lambda **kwargs: self.fail("blocked phase was observed")
                observer = ReviewWaitObserver(runtime, join_timeout_seconds=0.1)
                observer.start()
                self.until(lambda: not observer.snapshot().worker_alive)
                self.assertEqual(phase, observer.snapshot().state)

    def test_parent_can_cancel_during_wait_without_writer_lock_conflict(self):
        self.runtime.arm(appointment())
        self.wait_delay = 5
        observer = self.start()
        self.assertTrue(self.wait_entered.wait(3))
        self.assertTrue(self.runtime.cancel(expected_arm=1).controller.stop_confirmed)
        self.end_all()
        self.release.set()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual("closed", observer.snapshot().state)
        self.assertEqual(["arm", "pause"], [x[0] for x in self.host.effects])

    def test_stop_reports_unknown_join_then_finishes_without_implicit_pause(self):
        self.runtime.arm(appointment())
        self.wait_delay = 5
        observer = self.start()
        self.assertTrue(self.wait_entered.wait(3))
        first = observer.stop()
        self.assertEqual(("cleanup_unknown", True), (first.state, first.worker_alive))
        self.end_all()
        self.release.set()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual("stopped", observer.stop().state)
        self.assertEqual(["arm"], [x[0] for x in self.host.effects])

    def test_stop_during_initial_observe_cancels_before_second_read(self):
        entered, reads = threading.Event(), []
        original = self.host.read_child

        def read(child):
            reads.append(child)
            entered.set()
            self.release.wait(5)
            return original(child)

        self.host.read_child = read
        observer = self.start()
        self.assertTrue(entered.wait(3))
        self.assertTrue(observer.stop().worker_alive)
        self.release.set()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual(1, len(reads))
        self.assertEqual("stopped", observer.snapshot().state)
        self.assertEqual([], self.host.effects)

    def test_sixty_four_reviewers_use_fair_eight_target_bounded_batches(self):
        reviewers = tuple(Reviewer(f"child-{i}", f"turn-{i}") for i in range(64))
        path = self.path.with_name("many.sqlite")
        self.repository = ReviewWaitRepository.create(path,
            ReviewWaitController(BINDING, reviewers, reservation(self.host.value)))
        self.host.repository = self.repository
        self.host.reviewer_ids = tuple(r.reviewer_id for r in reviewers)
        self.host.turns = {r.reviewer_id: ChildTurn(r.reviewer_id, r.turn_id, "inProgress", "active") for r in reviewers}
        self.runtime = ReviewWaitRuntime(self.repository, self.host, lambda: BINDING, lambda: NOW)
        observer = self.start()
        self.until(lambda: len(self.wait_calls) >= 2)
        self.assertTrue(all(len(children) == 8 and timeout == 3750
                            for children, timeout, _ in self.wait_calls))
        self.assertFalse(set(self.wait_calls[0][0]) & set(self.wait_calls[1][0]))
        self.assertFalse(observer.stop().worker_alive)

    def test_immediate_no_change_waits_are_rate_limited_and_stop_interrupts_delay(self):
        observer = self.start()
        self.assertTrue(self.wait_entered.wait(3))
        time.sleep(0.15)
        self.assertEqual(1, len(self.wait_calls))
        began = time.monotonic()
        self.assertFalse(observer.stop().worker_alive)
        self.assertLess(time.monotonic() - began, 0.5)

    def test_unknown_update_stops_with_pending_intent_without_retry(self):
        self.runtime.arm(appointment())
        self.wait_delay = 5
        observer = self.start()
        self.assertTrue(self.wait_entered.wait(3))
        self.end_all()
        self.host.fail_after_write = True
        self.release.set()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual("pending", observer.snapshot().state)
        self.assertIsNotNone(self.repository.read().controller.pending)
        self.assertEqual(["arm", "shorten"], [x[0] for x in self.host.effects])

    def test_errors_and_snapshots_disclose_no_provider_details(self):
        def failed(_children):
            raise HostAdapterError("PRIVATE_PROVIDER_BODY")
        self.wait_override = failed
        observer = self.start()
        self.until(lambda: not observer.snapshot().worker_alive)
        self.assertEqual("error", observer.snapshot().state)
        self.assertNotIn("PRIVATE", repr(asdict(observer.snapshot())))
        self.assertEqual({"state", "worker_alive", "reason"}, set(asdict(observer.snapshot())))

    def test_stop_before_start_and_invalid_configuration(self):
        observer = ReviewWaitObserver(self.runtime)
        observer.stop()
        self.assertEqual("stopped", observer.start().state)
        self.assertFalse(observer.snapshot().worker_alive)
        for kwargs in ({"wait_timeout_ms": 0}, {"wait_timeout_ms": 30001},
                       {"minimum_cycle_seconds": 0}, {"join_timeout_seconds": float("nan")},
                       {"wait_timeout_ms": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ObserverError):
                ReviewWaitObserver(self.runtime, **kwargs)


if __name__ == "__main__":
    unittest.main()
