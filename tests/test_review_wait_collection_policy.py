"""Policy changes retain pending work through the real private poll composition."""

from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_review_wait_polling as polling
from tests.test_review_answer_tracking_collection import tracking_fixture
from task_governance_tool import review_answer_runtime as runtime
from task_governance_tool import review_handoff_transport as files
from task_governance_tool import setup_feature_config as config
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection import CollectionService, source
from task_governance_tool.review_answer_collection_repository import CollectionRepository
from task_governance_tool.review_answer_values import basis_digest
from task_governance_tool.review_finalization import FinalizationError
from task_governance_tool.review_wait_basis_repository import WaitBasisError
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_basis import BasisError
from task_governance_tool.review_wait_runtime.review_wait_repository import ObservationBusy, RepositoryError


class CollectionPolicyPollingTests(unittest.TestCase):
    def setUp(self):
        self.flow = polling.ManagedPollingTests(methodName="runTest")
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.root = self.flow.path.parent
        self.skill = self.root / "skill"
        (self.skill / "config").mkdir(parents=True)
        (self.root / ".tmp-review").mkdir()
        self.set_policy(True)
        self.metadata, self.answer = tracking_fixture()
        self.policy_observer = None
        self.metadata["basis"]["execution_basis"]["parent_session_id"] = polling.PARENT
        bases, bindings = [], []
        self.pages = {}
        for index, reviewer in enumerate(self.flow.reviewers, 1):
            basis = deepcopy(self.metadata["basis"])
            basis["request_digest"] = "sha256:" + str(index) * 64
            binding = deepcopy(self.metadata["binding"])
            binding.update(basis_digest=basis_digest(basis), slot=index, reviewer_key=f"reviewer-{index}",
                parent_session_id=polling.PARENT, reviewer_session_id=reviewer.reviewer_id)
            binding["tracking"]["boundary_turn_id"] = reviewer.turn_id
            bases.append(basis)
            bindings.append(binding)
            self.pages[reviewer.reviewer_id] = dict(schemaVersion=1,
                thread=dict(id=reviewer.reviewer_id, kind="codex", hostId="local"),
                page=dict(order="newest_first", limit=2, hasMore=False, nextCursor=None),
                turns=[dict(id=reviewer.turn_id, status="completed", error=None,
                    startedAt=1, completedAt=2, durationMs=1, items=[dict(type="agentMessage",
                        id="message-1", phase="final_answer", text=self.answer.raw.decode())])])
        self.repository = CollectionRepository(self.root / "collection.sqlite")
        self.repository.initialize(bases)
        self.admit = Mock(side_effect=self.admit_basis)
        self.service = CollectionService(self.root, self.repository,
            original_paths=(".tmp-review/first.json", ".tmp-review/second.json"), admit_basis=self.admit)
        for index, binding in enumerate(bindings, 1):
            self.service.bind(index, binding)
        self.enterContext(patch.object(files, "require_ignored"))
        self.recover = self.enterContext(patch.object(self.service, "recover", wraps=self.service.recover))
        self.session = ProjectReviewWaitSession(ProjectConfig(self.root, self.root / "server.mjs", self.root, "UTC"))
        self.session.skill = self.skill
        self.session._location = Mock(return_value=SimpleNamespace(database=self.root / "unused.sqlite"))
        self.control = Mock(return_value=False)
        self.session._control_observer = Mock(return_value=self.control)
        self.session._wait_occurrence_current = Mock()
        self.sources = self.enterContext(patch.object(runtime, "sources_for_reviewers", side_effect=self.source_groups))
        self.flow.polling_factory.side_effect = lambda controller, **options: self.session._polling_sources(
            controller.binding, controller.reservation.timer_id,
            tuple(row.reviewer_id for row in controller.reviewers), **options)
        self.flow.host._metadata = dict(threadId=polling.PARENT, turnId=polling.PARENT_TURN)
        self.flow.host._json_call.side_effect = lambda operation, arguments: deepcopy(self.pages[arguments["threadId"]])
        self.before_collect = None
        self.after_collect = None
        self.collect_times = []
        self.flow.worker.tracked_answer_collector = Mock(side_effect=self.collect)

    def set_policy(self, enabled, *, waiting=True):
        (self.skill / config.CHOICES_PATH).write_bytes(config.encode(dict(schema_version=1,
            choices=dict(review_wait=waiting, review_answer_collection=enabled))))

    def admit_basis(self, basis, slot):
        enabled = config.answer_collection_allowed(self.skill)
        if self.policy_observer is not None:
            self.policy_observer(enabled)
        if not enabled:
            raise AnswerError("collection_unavailable", reason="incomplete")

    def source_groups(self, repo, skill, binding, reviewers, *, observe_policy=None):
        self.policy_observer = observe_policy
        enabled = config.answer_collection_allowed(skill)
        if observe_policy is not None:
            observe_policy(enabled)
        if not enabled:
            return None
        slots = {reviewer.reviewer_id: index for index, reviewer in enumerate(self.flow.reviewers, 1)}
        return (runtime.SourceGroup(self.service, tuple(reviewers),
            tuple(slots[child] for child in reviewers), tuple(range(1, len(reviewers) + 1))),)

    def collect(self, host, controller, reviewer, **options):
        self.collect_times.append((reviewer, self.flow.clock.elapsed))
        if self.before_collect is not None:
            self.before_collect()
        result = self.session._collect_answers(controller.binding, host, (reviewer,), **options)
        if self.after_collect is not None:
            self.after_collect()
        return result

    def assert_collected_once(self, reviewer=polling.CHILD):
        slot = next(index for index, item in enumerate(self.flow.reviewers, 1) if item.reviewer_id == reviewer)
        row = self.repository.read()[slot - 1]
        self.assertEqual("frozen", row["tracking_state"])
        self.assertEqual("saved", row["attempts"][0]["publication"]["state"])
        self.assertEqual("message-1", row["attempts"][0]["source_identity"]["message_id"])
        self.assertEqual(1, sum(call.args[0] == slot for call in self.recover.call_args_list))

    def saved_result_state(self, *, ready=True, completed=True):
        # Native judgment/legitimate-completion eligibility is covered by the
        # finalizer integration tests. Keep its exact binding and released-owner
        # behavior here while exercising the actual polling composition.
        state = SimpleNamespace(ready=ready, completed=completed, error=None)
        finalizer = Mock()
        finalizer.journal.read.return_value = SimpleNamespace(basis={
            key: value for key, value in asdict(self.flow.controller.binding).items() if key != "wait_id"})
        def readiness():
            if state.error is not None:
                raise state.error
            return state.ready
        finalizer.saved_results_ready.side_effect = readiness
        self.flow.finalization_factory.return_value = finalizer
        def binding():
            if state.completed:
                raise BasisError("wait_basis_inactive")
            return polling.BINDING
        self.flow.current_binding.side_effect = binding
        def sources(*args, **kwargs):
            if state.completed:
                raise WaitBasisError("wait_basis_inactive")
            return self.source_groups(*args, **kwargs)
        self.sources.side_effect = sources
        return state, finalizer

    def test_completed_saved_results_precede_initial_collection_source_admission(self):
        _, finalizer = self.saved_result_state()
        self.control.side_effect = [True]
        self.flow.run_polls(count=3)
        self.sources.assert_not_called()
        self.flow.host.read_child.assert_not_called()
        self.assertEqual(2, finalizer.saved_results_ready.call_count)
        self.assertEqual(2, self.flow.current_binding.call_count)
        self.flow.polling_factory.assert_called_once()
        self.assertEqual(self.flow.controller.binding, self.flow.polling_factory.call_args.args[0].binding)
        self.assertEqual({"collect": False}, self.flow.polling_factory.call_args.kwargs)
        self.session._wait_occurrence_current.assert_called_once()
        self.flow.dispatch.assert_called_once()
        self.flow.failure.assert_not_called()

    def test_observer_reconstruction_after_completion_rechecks_saved_readiness_first(self):
        state, finalizer = self.saved_result_state(ready=False, completed=False)
        def readmission():
            state.ready = state.completed = True
            raise runtime.TrackingReadmissionRequired()
        tracking = Mock(side_effect=readmission)
        with patch.object(self.session, "_tracking_observer", return_value=tracking) as admission:
            stop = self.flow.run_polls(count=3)
        self.assertEqual([10], stop.waits)
        admission.assert_called_once()
        tracking.assert_called_once()
        self.assertEqual([True, False], [call.kwargs["collect"] for call in self.flow.polling_factory.call_args_list])
        self.assertEqual(2, finalizer.saved_results_ready.call_count)
        self.flow.dispatch.assert_called_once()
        self.flow.failure.assert_not_called()

    def test_saved_ready_busy_parent_retains_controls_without_repeated_admission(self):
        _, finalizer = self.saved_result_state()
        self.control.side_effect = [True] + [False] * 99
        self.flow.parent = polling.ChildTurn(polling.PARENT, polling.SECOND_TURN, "inProgress", "active")
        stop = self.flow.run_polls()
        self.assertEqual([10] * 100, stop.waits)
        self.assertEqual(100, self.control.call_count)
        self.assertEqual(100, self.session._wait_occurrence_current.call_count)
        self.assertEqual(2, self.flow.current_binding.call_count)
        self.assertEqual(2, finalizer.saved_results_ready.call_count)
        self.flow.polling_factory.assert_called_once()
        self.assertEqual(self.flow.controller.binding, self.flow.polling_factory.call_args.args[0].binding)
        self.assertEqual({"collect": False}, self.flow.polling_factory.call_args.kwargs)
        self.sources.assert_not_called()
        self.flow.host.read_child.assert_not_called()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_not_called()

    def test_initial_control_baseline_invalidates_saved_hint_before_collection_rebuild(self):
        state, _ = self.saved_result_state()
        def invalidate():
            state.ready = state.completed = False
            self.control.side_effect = None
            return True
        self.control.side_effect = invalidate
        self.flow.run_polls(count=4)
        self.assertEqual([False, True], [call.kwargs["collect"] for call in self.flow.polling_factory.call_args_list])
        self.sources.assert_called_once()
        self.assertEqual(6, self.flow.host.read_child.call_count)
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_not_called()

    def test_busy_saved_route_reopen_discards_readiness_and_rejects_stale_collection(self):
        state, _ = self.saved_result_state()
        self.flow.parent = polling.ChildTurn(polling.PARENT, polling.SECOND_TURN, "inProgress", "active")
        def transition(tick):
            if tick == 2:
                state.ready = state.completed = False
                state.error = FinalizationError("task_ownership_changed")
                self.control.return_value = True
                self.sources.side_effect = WaitBasisError("wait_basis_inactive")
                self.flow.parent = polling.ChildTurn(polling.PARENT, polling.SECOND_TURN, "completed", "idle")
        self.flow.run_polls(count=5, after_wait=transition)
        self.assertFalse(self.flow.worker._saved_results_ready)
        self.sources.assert_called_once()
        self.recover.assert_not_called()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_called_once()

    def test_completion_during_collection_admission_recovers_only_fresh_saved_results(self):
        state, finalizer = self.saved_result_state(ready=False, completed=False)
        def complete(*args, **kwargs):
            state.ready = state.completed = True
            raise WaitBasisError("wait_basis_inactive")
        self.sources.side_effect = complete
        self.control.return_value = True
        self.flow.run_polls(count=3)
        self.sources.assert_called_once()
        self.assertEqual(3, finalizer.saved_results_ready.call_count)
        self.assertEqual([True, False], [call.kwargs["collect"] for call in self.flow.polling_factory.call_args_list])
        self.flow.dispatch.assert_called_once()
        self.flow.failure.assert_not_called()

    def test_inactive_collection_without_saved_results_remains_rejected(self):
        self.saved_result_state(ready=False, completed=False)
        self.sources.side_effect = WaitBasisError("wait_basis_inactive")
        self.flow.run_polls(count=3)
        self.sources.assert_called_once()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_called_once()

    def test_other_collection_admission_errors_do_not_take_completed_fallback(self):
        state, finalizer = self.saved_result_state(ready=False, completed=False)
        def unavailable(*args, **kwargs):
            state.ready = state.completed = True
            raise WaitBasisError("wait_basis_unavailable")
        self.sources.side_effect = unavailable
        self.flow.run_polls(count=3)
        self.sources.assert_called_once()
        finalizer.saved_results_ready.assert_called_once()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_called_once()

    def test_saved_route_still_observes_wait_policy_while_parent_busy(self):
        self.saved_result_state()
        self.flow.parent = polling.ChildTurn(polling.PARENT, polling.SECOND_TURN, "inProgress", "active")
        self.flow.run_polls(count=5, after_wait=lambda tick: self.set_policy(True, waiting=False))
        self.sources.assert_not_called()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_called_once()
        self.assertEqual("wait_basis_unavailable", self.flow.failure.call_args.args[2].code)

    def test_saved_route_cancel_during_parent_read_prevents_dispatch(self):
        self.saved_result_state()
        self.flow.parent_action = lambda: self.flow.worker.repository.update(polling.PROBE, status="cancelled")
        self.flow.run_polls(count=3)
        self.sources.assert_not_called()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_not_called()
        self.assertEqual("cancelled", self.flow.worker.repository.read().status)

    def test_saved_route_deadline_during_parent_read_prevents_dispatch(self):
        self.saved_result_state()
        self.flow.parent_action = lambda: setattr(self.flow.clock, "elapsed", 1200)
        self.flow.run_polls(count=3)
        self.sources.assert_not_called()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_not_called()
        self.assertEqual("expired", self.flow.worker.repository.read().status)

    def test_admitted_off_on_preserves_unchanged_answer_while_peer_runs(self):
        def transition(tick):
            if tick == 1:
                self.set_policy(False)
                self.flow.ended(polling.CHILD)
            if tick == 3:
                self.flow.host._json_call.assert_not_called()
                self.recover.assert_not_called()
                self.set_policy(True)
        self.flow.run_polls(count=8, after_wait=transition)
        self.assert_collected_once()
        self.assertEqual(2, self.flow.host._json_call.call_count)
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_not_called()
        self.assertEqual(2, self.flow.polling_factory.call_count)

    def test_initially_off_reenters_source_admission_when_enabled_while_peer_runs(self):
        self.set_policy(False)
        self.flow.ended(polling.CHILD)
        def transition(tick):
            if tick == 2:
                self.flow.host._json_call.assert_not_called()
                self.set_policy(True)
        self.flow.run_polls(count=7, after_wait=transition)
        self.assert_collected_once()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_not_called()
        self.assertEqual(2, self.flow.polling_factory.call_count)

    def test_off_during_initial_source_admission_is_not_cached_as_enabled_legacy(self):
        self.flow.ended(polling.CHILD)
        def disable_during_admission(*args, **kwargs):
            self.set_policy(False)
            self.sources.side_effect = self.source_groups
            return self.source_groups(*args, **kwargs)
        self.sources.side_effect = disable_during_admission
        def transition(tick):
            if tick == 2:
                self.flow.host._json_call.assert_not_called()
                self.set_policy(True)
        self.flow.run_polls(count=7, after_wait=transition)
        self.assert_collected_once()
        self.flow.failure.assert_not_called()
        self.assertEqual(2, self.flow.polling_factory.call_count)

    def test_all_ended_parent_idle_waits_through_admitted_off_then_collects_once_each(self):
        def transition(tick):
            if tick == 1:
                self.set_policy(False)
                for reviewer in self.flow.reviewers:
                    self.flow.ended(reviewer.reviewer_id)
            if tick == 3:
                self.flow.dispatch.assert_not_called()
                self.flow.host._json_call.assert_not_called()
                self.set_policy(True)
        self.flow.run_polls(count=9, after_wait=transition)
        for reviewer in self.flow.reviewers:
            self.assert_collected_once(reviewer.reviewer_id)
        self.flow.dispatch.assert_called_once()
        self.flow.failure.assert_not_called()

    def test_off_after_probe_before_collection_does_not_consume_candidate(self):
        self.flow.ended(polling.CHILD)
        def disable_once():
            self.before_collect = None
            self.set_policy(False)
        self.before_collect = disable_once
        def transition(tick):
            if tick == 2:
                self.assertEqual(1, self.flow.host._json_call.call_count)
                self.recover.assert_not_called()
                self.set_policy(True)
        self.flow.run_polls(count=7, after_wait=transition)
        self.assert_collected_once()
        self.assertEqual(3, self.flow.host._json_call.call_count)
        self.flow.failure.assert_not_called()

    def test_reenabled_before_post_collection_read_defers_readmission_without_suppression(self):
        self.flow.ended(polling.CHILD)
        def disable_once():
            self.before_collect = None
            self.set_policy(False)
        def enable_once():
            self.after_collect = None
            self.set_policy(True)
        self.before_collect, self.after_collect = disable_once, enable_once
        self.flow.run_polls(count=6)
        self.assert_collected_once()
        self.flow.failure.assert_not_called()
        self.assertEqual(2, self.flow.polling_factory.call_count)

    def test_native_collection_admission_denial_survives_immediate_reenable(self):
        self.flow.ended(polling.CHILD)
        def disable_before_recovery(*args, **kwargs):
            self.recover.side_effect = None
            self.set_policy(False)
            try:
                return self.recover._mock_wraps(*args, **kwargs)
            finally:
                self.set_policy(True)
        self.recover.side_effect = disable_before_recovery
        self.flow.run_polls(count=6)
        self.assertEqual(2, self.recover.call_count)
        self.assertEqual("frozen", self.repository.read()[0]["tracking_state"])
        self.assertEqual(3, self.flow.host._json_call.call_count)
        self.assertEqual(2, self.flow.polling_factory.call_count)
        self.flow.failure.assert_not_called()

    def test_pending_changed_same_identity_body_is_reconsidered_while_peer_runs(self):
        self.flow.ended(polling.CHILD)
        final = self.pages[polling.CHILD]["turns"][0]["items"][0]
        final["text"] = "not a review"
        def transition(tick):
            if tick == 3:
                self.assertEqual(1, self.recover.call_count)
                final["text"] = self.answer.raw.decode()
        self.flow.run_polls(count=8, after_wait=transition)
        self.assertEqual(2, self.recover.call_count)
        self.assertEqual("frozen", self.repository.read()[0]["tracking_state"])
        self.assertEqual([0, 30], [at for _, at in self.collect_times])
        self.flow.failure.assert_not_called()

    def test_pending_incomplete_same_identity_can_become_complete_while_peer_runs(self):
        self.flow.ended(polling.CHILD)
        final = self.pages[polling.CHILD]["turns"][0]["items"][0]
        final["truncated"] = True
        def transition(tick):
            if tick == 3:
                self.assertEqual(1, self.recover.call_count)
                final["truncated"] = False
        self.flow.run_polls(count=8, after_wait=transition)
        self.assertEqual(2, self.recover.call_count)
        self.assertEqual("frozen", self.repository.read()[0]["tracking_state"])
        self.assertEqual([0, 30], [at for _, at in self.collect_times])
        self.flow.failure.assert_not_called()

    def test_writer_busy_retries_only_unprocessed_source_while_peer_runs(self):
        self.flow.ended(polling.CHILD)
        def busy_once(*args, **kwargs):
            self.recover.side_effect = None
            raise RepositoryError("writer_busy")
        self.recover.side_effect = busy_once
        self.flow.run_polls(count=6)
        self.assertEqual(2, self.recover.call_count)
        self.assertEqual("frozen", self.repository.read()[0]["tracking_state"])
        self.assertEqual([0, 10], [at for _, at in self.collect_times])
        self.flow.failure.assert_not_called()

    def test_frozen_unknown_is_preserved_across_policy_changes_without_host_replay(self):
        reviewer = self.flow.reviewers[0]
        observed = dict(source_identity=dict(reviewer_session_id=reviewer.reviewer_id,
            turn_id=reviewer.turn_id, message_id="message-1"), answer_bytes=self.answer.raw,
            complete=True, terminal="completed", answer_source=source("answer_observation", 2),
            terminal_source=source("terminal_observation", 2))
        with patch.object(files, "write_exclusive", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.observe(1, observed)
        saved = self.repository.read()
        self.flow.ended(polling.CHILD)
        def transition(tick):
            self.set_policy(tick != 1)
        self.flow.run_polls(count=6, after_wait=transition)
        self.assertEqual(saved, self.repository.read())
        self.flow.host._json_call.assert_not_called()
        self.recover.assert_not_called()
        self.flow.failure.assert_not_called()

    def test_frozen_valid_negative_is_preserved_while_collection_is_disabled(self):
        reviewer = self.flow.reviewers[0]
        raw = self.answer.raw.replace(b'"pass"', b'"changes_requested"')
        self.service.observe(1, dict(source_identity=dict(reviewer_session_id=reviewer.reviewer_id,
            turn_id=reviewer.turn_id, message_id="message-1"), answer_bytes=raw,
            complete=True, terminal="completed", answer_source=source("answer_observation", 2),
            terminal_source=source("terminal_observation", 2)))
        saved = self.repository.read()
        self.flow.ended(polling.CHILD)
        self.flow.run_polls(count=6, after_wait=lambda tick: self.set_policy(False))
        self.assertEqual(saved, self.repository.read())
        self.flow.host._json_call.assert_not_called()
        self.recover.assert_not_called()
        self.flow.dispatch.assert_not_called()
        self.flow.failure.assert_not_called()

    def test_saved_native_results_skip_unused_busy_or_unavailable_tracking(self):
        for error in (ObservationBusy(), AnswerError("collection_unavailable", reason="incomplete")):
            with self.subTest(error=type(error).__name__):
                flow = polling.ManagedPollingTests(methodName="runTest")
                flow.setUp()
                self.addCleanup(flow.doCleanups)
                flow.worker._reviewers_to_observe = Mock(return_value=())
                flow.tracking_reader.side_effect = error
                flow.run_polls(count=3)
                flow.tracking_reader.assert_not_called()
                flow.dispatch.assert_called_once()
                flow.failure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
