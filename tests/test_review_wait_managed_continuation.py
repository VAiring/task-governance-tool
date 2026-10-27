"""Offline managed-wait association with explicit admitted request rounds.

Native continuation validation and actual cleanup proof have separate focused
tests; this fixture injects the returned native round and makes no host calls.
"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_review_wait_managed as fixtures
from tests.test_review_continuation import ident
from task_governance_tool.review_wait_runtime.request_repository import RequestRepository, request_identity_digest
from task_governance_tool.review_wait_runtime.request_continuation import RequestRound
from task_governance_tool.review_wait_runtime.managed_wait import basis_digest
from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn


class ManagedContinuationTests(unittest.TestCase):
    setUp = fixtures.ManagedFlowTests.setUp
    close_all = fixtures.ManagedFlowTests.close_all
    factory = fixtures.ManagedFlowTests.factory
    make = fixtures.ManagedFlowTests.make
    call = fixtures.ManagedFlowTests.call
    until = fixtures.ManagedFlowTests.until

    def prepared(self):
        self.session._ensure_root(self.paths)
        path = self.paths.review_wait_request_store(fixtures.PARENT, fixtures.TASK)
        self.journal = RequestRepository(path)
        self.round = RequestRound(ident("tg_review_preparation_"), ident("tg_review_continuation_"), 1,
            "a" * 64, basis_digest(replace(self.basis, wait_id="request")), "b" * 64, 0, None, None, None,
            phase="prepared", packet_path="reviews/round/packet.json", packet_digest="c" * 64,
            result_paths=("reviews/round/a.json",))
        with self.journal.serial(create=True) as lease:
            self.journal.install_round(lease, self.round)
        transition = TransitionRepository(self.paths.review_continuation_store(fixtures.PARENT, fixtures.TASK,
            self.basis.target_generation))
        with transition.serial(create=True) as lease:
            fenced = transition.fence(lease, replace(self.round, phase="fenced", packet_digest=None))
            settled = transition.advance(lease, fenced, phase="settled")
            transition.advance(lease, settled, phase="prepared", packet_path=self.round.packet_path,
                packet_digest=self.round.packet_digest, result_paths=self.round.result_paths)
        self.validate = self.enterContext(mock.patch.object(self.session, "_continuation",
            return_value=SimpleNamespace(state="prepared", selected=(SimpleNamespace(session_id=fixtures.CHILD),))))

    def test_selected_new_occurrence_publishes_bound_attempt_and_duplicate_is_readiness_only(self):
        self.prepared()
        result = self.call()
        self.assertEqual(result["status"], "waiting", result)
        attempt, record, rounds = self.journal.read_admission()
        self.assertEqual(attempt, 1)
        self.assertEqual(record.continuation_id, self.round.continuation_id)
        self.assertEqual(record.continuation_sequence, 1)
        self.assertEqual(rounds[-1].phase, "published")
        effects = list(self.effects)
        repeated = self.call()
        self.assertTrue(repeated["replayed"], repeated)
        self.assertEqual(self.effects, effects)

    def test_scheduled_healthy_continuation_renews_occurrence_without_new_review_round(self):
        self.prepared()
        self.assertEqual(self.call()["status"], "waiting")
        published = self.journal.read_rounds()
        for sequence, turn in enumerate((fixtures.NEW_TURN, fixtures.OTHER), start=2):
            self.now += timedelta(minutes=10)
            self.current_parent = ChildTurn(fixtures.PARENT, turn, "inProgress", "active")
            metadata = {"threadId": fixtures.PARENT, "turnId": turn}
            result = self.call(metadata=metadata)
            self.assertTrue(result["parent_may_end"], result)
            attempt, request, rounds = self.journal.read_admission()
            self.assertEqual(attempt, sequence)
            self.assertEqual(request.continuation_id, self.round.continuation_id)
            self.assertEqual(request.parent_turn, turn)
            self.assertEqual(rounds, published)
            effects = list(self.effects)
            self.assertTrue(self.call(metadata=metadata)["replayed"])
            self.assertEqual(self.effects, effects)
        self.assertEqual(self.effects, ["create", "arm", "pause", "delete", "create", "arm",
            "pause", "delete", "create", "arm"])

    def fence_next(self):
        attempt, previous = self.journal.read()
        transitions = TransitionRepository(self.paths.review_continuation_store(fixtures.PARENT,
            fixtures.TASK, self.basis.target_generation))
        with transitions.serial() as lease:
            old = transitions.read()[-1]
            if old.phase == "prepared":
                transitions.advance(lease, old, phase="published")
            transitions.fence(lease, replace(old, continuation_id=ident("tg_review_continuation_", 2),
                continuation_sequence=2, expected_attempt=attempt,
                expected_request_digest=request_identity_digest(previous) if previous else None,
                old_automation_id=previous.automation_id if previous else None,
                old_request_phase=previous.phase if previous else None, phase="fenced", packet_digest=None,
                worker_stop_identity=None, packet_path="reviews/next/packet.json", result_paths=("reviews/next/a.json",)))

    def test_incomplete_successor_fence_rejects_initial_wait_before_creation(self):
        self.prepared()
        self.fence_next()
        self.assertFalse(self.call()["ok"])
        self.assertEqual(self.effects, [])

    def test_incomplete_successor_fence_rejects_renewal_before_creation(self):
        self.prepared()
        self.assertEqual(self.call()["status"], "waiting")
        self.assertTrue(self.call("stop")["ok"])
        self.fence_next()
        effects = list(self.effects)
        self.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
        result = self.call(metadata={"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN})
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.effects, effects)
        self.assertEqual(self.journal.read()[0], 1)

    def test_fence_published_after_preflight_is_rechecked_under_request_admission(self):
        self.prepared()
        original = self.session.managed._preflight
        def fenced(*args):
            value = original(*args)
            self.fence_next()
            return value
        with mock.patch.object(self.session.managed, "_preflight", side_effect=fenced):
            self.assertFalse(self.call()["ok"])
        self.assertEqual(self.effects, [])

    def test_fence_after_creation_cannot_activate_obsolete_occurrence(self):
        self.prepared()
        original = self.factory
        def host_factory(**kwargs):
            host = original(**kwargs)
            if kwargs["automation_id"] == "pending":
                create = host.create_heartbeat
                def create_then_fence(rule):
                    actual = create(rule)
                    self.fence_next()
                    return actual
                host.create_heartbeat = create_then_fence
            return host
        self.session.managed_host_factory = host_factory
        self.assertFalse(self.call()["ok"])
        self.assertEqual(self.effects, ["create", "delete"])

    def test_continuation_renewal_requires_proven_exit_of_the_previous_worker(self):
        self.prepared()
        self.assertEqual(self.call()["status"], "waiting")
        self.now += timedelta(minutes=10)
        self.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
        with mock.patch("task_governance_tool.review_wait_runtime.continuation_cleanup_repository.observe_latest_cleanup",
                return_value=SimpleNamespace(state="unknown", reason="review_continuation_worker_exit_unknown")):
            result = self.call(metadata={"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN})
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.effects, ["create", "arm", "pause", "delete"])
        self.assertEqual(self.journal.read()[0], 1)

    def test_changed_selected_turn_cannot_renew_a_published_continuation(self):
        self.prepared()
        self.assertEqual(self.call()["status"], "waiting")
        self.now += timedelta(minutes=10)
        self.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
        self.child = ChildTurn(fixtures.CHILD, fixtures.OTHER, "inProgress", "active")
        result = self.call(metadata={"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN})
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.effects, ["create", "arm"])
        self.assertEqual(self.journal.read()[0], 1)

    def test_unknown_cleanup_cannot_renew_or_replay_a_continuation_occurrence(self):
        self.prepared()
        self.assertEqual(self.call()["status"], "waiting")
        self.now += timedelta(minutes=10)
        self.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
        self.fail_delete = True
        metadata = {"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN}
        self.assertFalse(self.call(metadata=metadata)["ok"])
        effects = list(self.effects)
        self.assertFalse(self.call(metadata=metadata)["ok"])
        self.assertEqual(self.effects, effects)
        self.assertEqual(effects, ["create", "arm", "pause", "delete"])
        self.assertEqual(self.journal.read()[0], 1)

    def test_older_prepared_round_retires_without_rebinding_and_starts_fresh_initial_wait(self):
        self.prepared()
        old = self.round
        self.basis = replace(self.basis, target_generation=self.basis.target_generation + 1)
        result = self.call()
        self.assertEqual(result["status"], "waiting", result)
        _, request, rounds = self.journal.read_admission()
        self.assertIsNone(request.continuation_id)
        self.assertEqual(rounds, (replace(old, phase="published"),))
        self.assertEqual(request.basis_digest, basis_digest(replace(self.basis, wait_id="request")))
        effects = list(self.effects)
        duplicate = self.call()
        self.assertTrue(duplicate["replayed"], duplicate)
        self.assertEqual(self.effects, effects)

    def test_older_published_round_is_history_for_fresh_ordinary_wait(self):
        self.prepared()
        with self.journal.serial() as lease:
            old = self.journal.publish_without_wait(lease, self.round)
        self.basis = replace(self.basis, target_generation=self.basis.target_generation + 1)
        result = self.call()
        self.assertEqual(result["status"], "waiting", result)
        self.assertIsNone(self.journal.read()[1].continuation_id)
        self.assertEqual(self.journal.read_rounds(), (old,))

    def test_old_round_with_unprovable_cleanup_cannot_retire_or_create_a_timer(self):
        self.prepared()
        self.basis = replace(self.basis, target_generation=self.basis.target_generation + 1)
        before = self.journal.read_admission()
        with mock.patch("task_governance_tool.review_wait_runtime.continuation_cleanup_repository.observe_latest_cleanup",
                return_value=SimpleNamespace(state="unknown", reason="review_continuation_worker_exit_unknown")):
            result = self.call()
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read_admission(), before)

    def test_cleanup_rejection_diagnostics_preserve_each_public_admission_boundary(self):
        cases = ("generation", "renewal", "retirement")
        reasons = ("review_continuation_effect_unknown", "review_continuation_worker_exit_unknown",
                   "review_continuation_cleanup_required", "review_continuation_conflict")
        for case in cases:
            fixture = ManagedContinuationTests()
            fixture.setUp()
            try:
                fixture.prepared()
                if case != "retirement":
                    self.assertEqual(fixture.call()["status"], "waiting")
                    self.assertTrue(fixture.call("stop")["ok"])
                if case == "renewal":
                    fixture.now += timedelta(minutes=10)
                    fixture.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
                    metadata = {"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN}
                else:
                    fixture.basis = replace(fixture.basis, target_generation=fixture.basis.target_generation + 1)
                    metadata = fixtures.META
                before = fixture.journal.read_admission()
                effects = list(fixture.effects)
                method = "observe_generation_cleanup" if case == "generation" else "observe_latest_cleanup"
                for reason in (*reasons, "private unapproved diagnostic"):
                    with self.subTest(case=case, reason=reason), mock.patch(
                            "task_governance_tool.review_wait_runtime.continuation_cleanup_repository." + method,
                            return_value=SimpleNamespace(state="unknown", reason=reason)):
                        result = fixture.call(metadata=metadata)
                    self.assertFalse(result["ok"], result)
                    value = result["diagnostic"]
                    self.assertEqual(value["reason"], reason if reason in reasons else "unavailable")
                    self.assertEqual(value["stage"], "cleanup" if case == "renewal" else "request_read")
                    self.assertEqual(value["retained_effects"], "not_inspected")
                    self.assertEqual(value["recovery"], "inspect_existing")
                    self.assertEqual(value["local_state"], "may_have_changed")
                    self.assertEqual(value["host_mutation"], "not_dispatched")
                    self.assertEqual(value["cleanup"], "confirmed" if case == "renewal" else "not_attempted")
                    self.assertEqual(fixture.effects, effects)
                    self.assertEqual(fixture.journal.read_admission(), before)
                    self.assertNotIn("private unapproved diagnostic", str(result))
            finally:
                fixture.doCleanups()

    def test_latest_cleanup_typed_read_failure_invalidates_prior_settlement(self):
        from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
        for case in ("renewal", "retirement"):
            fixture = ManagedContinuationTests()
            fixture.setUp()
            try:
                fixture.prepared()
                if case == "renewal":
                    self.assertEqual(fixture.call()["status"], "waiting")
                    self.assertTrue(fixture.call("stop")["ok"])
                    fixture.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
                    metadata = {"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN}
                else:
                    fixture.basis = replace(fixture.basis, target_generation=fixture.basis.target_generation + 1)
                    metadata = fixtures.META
                before, effects = fixture.journal.read_admission(), list(fixture.effects)
                for code in ("state_unreadable", "writer_busy"):
                    with self.subTest(case=case, code=code), mock.patch.object(
                            RequestRepository, "read", side_effect=RepositoryError(code)) as read:
                        result = fixture.call(metadata=metadata)
                        self.assertFalse(result["ok"], result)
                        self.assertEqual(read.call_count, 1)
                        value = result["diagnostic"]
                        self.assertEqual(value["reason"], code)
                        self.assertEqual(value["stage"], "cleanup" if case == "renewal" else "request_read")
                        self.assertEqual(value["retained_effects"], "not_inspected")
                        self.assertEqual(value["recovery"], "inspect_existing")
                        self.assertEqual(value["host_mutation"], "not_dispatched")
                        self.assertEqual(value["cleanup"], "confirmed" if case == "renewal" else "not_attempted")
                    self.assertEqual(fixture.effects, effects)
                    self.assertEqual(fixture.journal.read_admission(), before)
            finally:
                fixture.doCleanups()

    def test_unknown_native_historical_binding_blocks_fresh_wait(self):
        self.prepared()
        from task_governance_tool.review_wait_runtime.failure_diagnostics import DiagnosticError
        self.basis = replace(self.basis, target_generation=self.basis.target_generation + 1)
        self.validate.side_effect = DiagnosticError("review_continuation_basis_changed")
        before = self.journal.read_admission()
        result = self.call()
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read_admission(), before)

    def test_already_ended_round_publishes_without_attempt_timer_or_send(self):
        self.prepared()
        self.child = ChildTurn(fixtures.CHILD, fixtures.TURN, "completed", "idle")
        result = self.call()
        self.assertEqual(result["status"], "reviews_ended", result)
        self.assertFalse(result["parent_may_end"])
        self.assertEqual(self.effects, [])
        attempt, record, rounds = self.journal.read_admission()
        self.assertEqual((attempt, record), (0, None))
        self.assertEqual(rounds[-1].phase, "published")

    def test_unselected_reviewer_is_rejected_before_host_effect(self):
        self.prepared()
        self.validate.return_value = SimpleNamespace(state="prepared", selected=(SimpleNamespace(session_id=fixtures.OTHER),))
        result = self.call()
        self.assertFalse(result["ok"])
        self.assertEqual(self.effects, [])
        self.assertEqual(self.journal.read_admission()[:2], (0, None))

    def test_changed_round_at_request_admission_does_not_create_timer(self):
        self.prepared()
        original = self.session.managed._preflight
        def changed(*args):
            value = original(*args)
            with self.journal.serial() as lease:
                self.journal.publish_without_wait(lease, self.round)
            return value
        with mock.patch.object(self.session.managed, "_preflight", side_effect=changed):
            result = self.call()
        self.assertFalse(result["ok"])
        self.assertEqual(self.effects, [])

    def test_prepared_replacement_fences_old_occurrence_before_any_attempt_exists(self):
        self.prepared()
        from task_governance_tool.review_wait_runtime.failure_diagnostics import DiagnosticError
        with self.assertRaises(DiagnosticError) as caught:
            self.session._wait_occurrence_current(self.basis, "obsolete-automation")
        self.assertEqual(caught.exception.reason, "review_continuation_conflict")
        self.assertEqual(self.effects, [])

    def test_registered_manual_judgment_does_not_read_an_incidental_new_child_turn(self):
        self.prepared()
        self.validate.return_value = SimpleNamespace(state="registered", selected=(SimpleNamespace(session_id=fixtures.CHILD),))
        original = self.factory
        def no_child(**kwargs):
            host = original(**kwargs)
            host.read_child = mock.Mock(side_effect=AssertionError("registered result needs no child read"))
            return host
        self.session.managed_host_factory = no_child
        result = self.call(reviewer_ids=[fixtures.CHILD])
        self.assertEqual(result["status"], "reviews_ended", result)
        self.assertEqual(self.effects, [])

    def registered_active_wait(self):
        self.prepared()
        started = self.call()
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(started.get("status"), "waiting", started)
        self.validate.return_value = SimpleNamespace(state="registered", selected=(SimpleNamespace(session_id=fixtures.CHILD),))
        original = self.factory
        self.admission_child_reads = []
        def no_fresh_child(**kwargs):
            host = original(**kwargs)
            if kwargs["automation_id"] == "pending":
                def unexpected_read(child):
                    self.admission_child_reads.append(child)
                    raise AssertionError("registered judgment needs no fresh child activity")
                host.read_child = unexpected_read
            return host
        self.session.managed_host_factory = no_fresh_child
        return self.journal.read_admission()

    def test_registered_active_manual_wait_cleans_up_in_later_parent_turn(self):
        before = self.registered_active_wait()
        self.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
        result = self.call(metadata={"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN})
        self.assertEqual(result["status"], "reviews_ended", result)
        self.assertFalse(result["parent_may_end"])
        attempt, request, rounds = self.journal.read_admission()
        self.assertEqual((attempt, request.reviewers, rounds), (before[0], before[1].reviewers, before[2]))
        self.assertEqual(request.phase, "closed")
        self.assertEqual(self.effects, ["create", "arm", "pause", "delete"])
        self.assertEqual(self.admission_child_reads, [])

    def test_registered_active_manual_wait_same_turn_keeps_readiness_duplicate(self):
        before = self.registered_active_wait()
        result = self.call()
        self.assertTrue(result["replayed"], result)
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(self.journal.read_admission(), before)
        self.assertEqual(self.effects, ["create", "arm"])
        self.assertEqual(self.admission_child_reads, [])

    def test_registered_manual_wait_does_not_hide_unknown_cleanup(self):
        before = self.registered_active_wait()
        self.fail_delete = True
        self.current_parent = ChildTurn(fixtures.PARENT, fixtures.NEW_TURN, "inProgress", "active")
        metadata = {"threadId": fixtures.PARENT, "turnId": fixtures.NEW_TURN}
        result = self.call(metadata=metadata)
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.effects, ["create", "arm", "pause", "delete"])
        self.assertEqual(self.journal.read()[0], before[0])
        self.assertFalse(self.call(metadata=metadata)["ok"])
        self.assertEqual(self.effects, ["create", "arm", "pause", "delete"])
        self.assertEqual(self.admission_child_reads, [])

    def test_manual_locator_never_adopts_an_unrelated_existing_finalization_file(self):
        from dataclasses import asdict
        from task_governance_tool.review_preparation_repository import PreparationRecord, PreparationRepository
        from tests.test_review_continuation import basis
        self.basis = replace(self.basis, execution_id=ident("tg_execution_"), artifact_manifest_id=ident("tg_artifact_manifest_"))
        self.session._ensure_root(self.paths)
        values = asdict(self.basis)
        values.pop("wait_id")
        values["parent_session_id"] = values.pop("parent_thread_id")
        native = basis(**values)
        path = self.paths.review_preparation_store(fixtures.PARENT, fixtures.TASK, self.basis.target_generation)
        ready = PreparationRecord(1, ident("tg_review_preparation_"), native, "manual", "reviews/packet.json",
            "a" * 64, ("reviews/a.json",), None, "2026-10-14T00:00:00Z")
        with PreparationRepository(path).serial(initial=ready):
            pass
        intent = self.paths.review_finalization_store(fixtures.PARENT, fixtures.TASK, self.basis.target_generation)
        intent.write_bytes(b"unrelated intent must remain untouched")
        self.assertIsNone(self.session._finalizer(self.basis))
        self.assertEqual(intent.read_bytes(), b"unrelated intent must remain untouched")
        self.assertEqual(self.effects, [])


class HistoricalRoundAdmissionTests(unittest.TestCase):
    def admit(self, *, fresh_changes=None, row_changes=None, historical=True):
        from tests.test_review_continuation import basis, A
        from task_governance_tool import review_continuation as values
        from task_governance_tool.review_wait_runtime.review_wait_controller import WaitBinding
        from task_governance_tool.review_wait_runtime.project_server import ProjectReviewWaitSession
        old = basis()
        fresh = basis(target_generation=2)
        fresh.update(fresh_changes or {})
        selected = (values.SelectedReviewer(A, "supplement", None, "Missing original"),)
        record = SimpleNamespace(state="prepared", basis=old, continuation_id=ident("tg_review_continuation_"), preparation_id=ident("tg_review_preparation_"),
            sequence=1, selected=selected)
        for key, value in (row_changes or {}).items():
            setattr(record, key, value)
        def binding(native):
            return WaitBinding(wait_id="request", parent_thread_id=native["parent_session_id"],
                **{key: native[key] for key in WaitBinding.__dataclass_fields__ if key not in ("wait_id", "parent_thread_id")})
        round_record = RequestRound(ident("tg_review_preparation_"), ident("tg_review_continuation_"), 1,
            values.basis_digest(old), basis_digest(binding(old)), values.selection_digest(selected), 0, None, None, None,
            phase="prepared", packet_path="reviews/old/packet.json", packet_digest="c" * 64,
            result_paths=("reviews/old/a.json",))
        owner = SimpleNamespace(config=SimpleNamespace(repo="unused"))
        with mock.patch("task_governance_tool.review_handoff_scope.resolve", return_value=SimpleNamespace(target="unused")), \
             mock.patch("task_governance_tool.review_continuation_handoff_repository.read_state",
                        return_value=SimpleNamespace(basis=fresh, records=(record,))):
            return ProjectReviewWaitSession._continuation(owner, binding(fresh), round_record, historical=historical)

    def test_valid_older_native_round_is_history_only(self):
        self.assertEqual(self.admit().basis["target_generation"], 1)

    def test_same_generation_or_changed_project_binding_cannot_be_declared_history(self):
        from task_governance_tool.review_wait_runtime.failure_diagnostics import DiagnosticError
        for changes in ({"target_generation": 1}, {"project_binding_generation": 2}, {"project_path_hash": "d" * 64}):
            with self.subTest(changes=changes), self.assertRaises(DiagnosticError):
                self.admit(fresh_changes=changes)

    def test_unready_or_wrong_native_locator_is_not_usable_history(self):
        from task_governance_tool.review_wait_runtime.failure_diagnostics import DiagnosticError
        for changes in ({"state": "preparing"}, {"preparation_id": ident("tg_review_preparation_", 9)}, {"sequence": 2}):
            with self.subTest(changes=changes), self.assertRaises(DiagnosticError):
                self.admit(row_changes=changes)

    def test_normal_current_validation_still_rejects_old_basis(self):
        from task_governance_tool.review_wait_runtime.failure_diagnostics import DiagnosticError
        with self.assertRaises(DiagnosticError):
            self.admit(historical=False)


if __name__ == "__main__":
    unittest.main()
