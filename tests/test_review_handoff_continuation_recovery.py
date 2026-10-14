"""Recover exact continuation publication across real persistence boundaries.

Only temporary core/operational databases and ignored physical Packets are used.
No host connection, timer, reviewer dispatch, or finalization execution occurs.
"""

from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_review_handoff_continuation as manual
from tests import test_review_finalization_continuation as integrated
from tests.test_review_continuation import A, B
from tests.test_review_wait_direct import ORIGINAL
from tests.test_review_wait_service import TURN
from task_governance_tool import review_handoff_continuation as continuation, storage
from task_governance_tool.review_finalization_repository import FinalizationRepository
from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository
from task_governance_tool.review_wait_runtime.request_repository import RequestRecord, RequestRepository


class RecoveryAssertions:
    """Compare stored facts, not the number or ordering of Python helper calls."""

    def originals(self):
        return {path.relative_to(self.repo).as_posix(): path.read_bytes()
                for path in (self.repo / "reviews").glob("*") if path.is_file()}

    def state(self):
        return continuation.core.read_state(self.target, self.task_id, self.caller)

    def test_known_cleanup_recovery_returns_first_selected_requests_once(self):
        self.ready()
        with mock.patch.object(continuation, "observe_cleanup", return_value=SimpleNamespace(
                state="needs_cleanup", reason="review_continuation_cleanup_required")):
            pending = self.call()
        self.assert_not_ready(pending)
        self.assertEqual(pending["handoff"]["next_action"], "stop_known_wait_then_reconcile")
        fence = TransitionRepository(self.root / "transition.sqlite").read()[-1]
        self.assertEqual(fence.phase, "fenced")
        self.assertIsNone(fence.packet_digest)
        self.assertFalse((self.repo / self.args.directory).exists())
        self.assertEqual(RequestRepository(self.root / "request.sqlite").read_rounds(), ())

        # Known cleanup is now settled; this is the first executable response,
        # not a repeated reviewer dispatch. Both manual and integrated fixtures
        # exercise the same boundary without any real host effect.
        handoff = self.assert_ready(self.call())
        self.assertFalse(handoff["replayed"])
        self.assertEqual(handoff["continuation_id"], fence.continuation_id)
        self.assertEqual(len(handoff["review_requests"]), 1)
        selected = handoff["review_requests"][0]
        self.assertEqual(selected["session_id"], A)
        self.assertEqual(selected["result_path"], self.args.directory + "/review-1.json")
        self.assertIn(handoff["packet_path"], selected["request"])
        before = (self.state(), self.originals(),
                  TransitionRepository(self.root / "transition.sqlite").read(),
                  RequestRepository(self.root / "request.sqlite").read_rounds())
        replay = self.assert_ready(self.call())
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["review_requests"], [])
        self.assertEqual((self.state(), self.originals(),
                          TransitionRepository(self.root / "transition.sqlite").read(),
                          RequestRepository(self.root / "request.sqlite").read_rounds()), before)

    def test_settled_cleanup_before_packet_intent_returns_first_requests(self):
        self.ready()
        self.interrupted(TransitionRepository, "advance", after=True)
        settled = TransitionRepository(self.root / "transition.sqlite").read()[-1]
        self.assertEqual(settled.phase, "settled")
        self.assertIsNone(settled.packet_digest)
        self.assertFalse((self.repo / self.args.directory).exists())
        handoff = self.assert_ready(self.call())
        self.assertFalse(handoff["replayed"])
        self.assertEqual(handoff["continuation_id"], settled.continuation_id)
        self.assertEqual([row["session_id"] for row in handoff["review_requests"]], [A])

    def assert_not_ready(self, result):
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["errors"], result)
        self.assertTrue(result["handoff"].get("next_action"), result)
        self.assertNotIn("submit_command", result["handoff"])
        self.assertNotIn("finalization_command", result["handoff"])
        self.assertFalse(result["handoff"].get("review_requests"))

    def assert_ready(self, result, *, count=1):
        self.assertTrue(result["ok"], result)
        handoff = result["handoff"]
        core_rows = self.state().records
        self.assertEqual(len(core_rows), count)
        current = core_rows[-1]
        self.assertEqual(current.state, "prepared")
        self.assertEqual(current.continuation_id, handoff["continuation_id"])
        transitions = TransitionRepository(self.root / "transition.sqlite").read()
        rounds = RequestRepository(self.root / "request.sqlite").read_rounds()
        self.assertEqual(len(transitions), count)
        self.assertEqual(len(rounds), count)
        transition, request = transitions[-1], rounds[-1]
        for row in (transition, request):
            self.assertEqual(row.continuation_id, current.continuation_id)
            self.assertEqual(row.continuation_sequence, current.sequence)
            self.assertEqual(row.preparation_id, current.preparation_id)
            self.assertIn(row.phase, ("prepared", "published"))
        self.assertEqual(request, transition)
        packet = (self.repo / transition.packet_path).read_bytes()
        self.assertEqual(sha256(packet).hexdigest(), transition.packet_digest)
        context = json.loads(packet)["review_session_context"]
        self.assertEqual(context["continuation_id"], current.continuation_id)
        self.assertEqual(context["continuation_sequence"], current.sequence)
        self.assertEqual(RequestRepository(self.root / "request.sqlite").read(), (0, None))
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 0)
        if hasattr(self, "journal"):
            finalization_rounds = self.journal.read_rounds()
            self.assertEqual(len(finalization_rounds), count + 1)
            latest = self.journal.read()
            self.assertEqual(latest.continuation_id, current.continuation_id)
            self.assertEqual(latest.preparation_id, current.preparation_id)
            self.assertEqual(latest.packet_path, transition.packet_path)
            self.assertEqual(latest.packet_digest, transition.packet_digest)
            self.assertEqual(latest.result_paths, transition.result_paths)
            self.assertEqual(latest.active_sessions, tuple(row.session_id for row in current.selected))
            self.assertEqual((latest.registration, latest.commit, latest.completion),
                             ("not_started", "not_started", "not_started"))
        return handoff

    def interrupted(self, owner, name, *, after):
        original = getattr(owner, name)
        reached_boundary = False

        def interrupted_write(*args, **kwargs):
            nonlocal reached_boundary
            if after:
                original(*args, **kwargs)
            reached_boundary = True
            raise RuntimeError("Injected local acknowledgement loss")

        before = self.originals()
        with mock.patch.object(owner, name, autospec=True, side_effect=interrupted_write):
            result = self.call()
        self.assertTrue(reached_boundary, "The intended persistence boundary was not reached")
        self.assert_not_ready(result)
        self.assertEqual(self.originals(), before)
        return result

    def recover(self, owner, name, *, after):
        first = self.interrupted(owner, name, after=after)
        saved_transition = TransitionRepository(self.root / "transition.sqlite").read()[-1]
        first_publication = (saved_transition.phase in ("fenced", "settled")
                             and saved_transition.packet_digest is None)
        before = self.originals()
        result = self.call()
        handoff = self.assert_ready(result)
        self.assertEqual(handoff["replayed"], not first_publication)
        self.assertEqual(len(handoff["review_requests"]), 1 if first_publication else 0)
        if first["handoff"].get("continuation_id") is not None:
            self.assertEqual(handoff["continuation_id"], first["handoff"]["continuation_id"])
        self.assertEqual(self.originals(), before)
        # A settled retry neither appends another round nor returns dispatch.
        settled = (self.state(), TransitionRepository(self.root / "transition.sqlite").read(),
                   RequestRepository(self.root / "request.sqlite").read_rounds())
        again = self.call()
        self.assert_ready(again)
        self.assertTrue(again["handoff"]["replayed"])
        self.assertEqual(again["handoff"]["review_requests"], [])
        self.assertEqual((self.state(), TransitionRepository(self.root / "transition.sqlite").read(),
                          RequestRepository(self.root / "request.sqlite").read_rounds()), settled)
        self.assertEqual(self.originals(), before)


class ManualContinuationRecoveryTests(RecoveryAssertions, unittest.TestCase):
    setUp = manual.HandoffContinuationTests.setUp
    physical = manual.HandoffContinuationTests.physical
    publish = manual.HandoffContinuationTests.publish
    ready = manual.HandoffContinuationTests.ready
    call = manual.HandoffContinuationTests.call
    save_actor = manual.HandoffContinuationTests.save_actor

    def test_normal_retry_is_ready_only_with_all_native_ledgers_and_packet(self):
        self.ready()
        old = self.save_actor(B, "reviews/packet.json", "reviews/b.json")
        first = self.assert_ready(self.call())
        replay = self.assert_ready(self.call())
        self.assertEqual(replay["continuation_id"], first["continuation_id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["review_requests"], [])
        self.assertEqual((self.repo / "reviews/b.json").read_bytes(), old)
        self.assertFalse((self.root / "finalization.sqlite").exists())

    def test_core_prepared_entry_failure_completes_only_missing_stages(self):
        self.ready()
        self.recover(continuation.core, "prepared", after=False)

    def test_core_prepared_lost_acknowledgement_reconciles_saved_state(self):
        self.ready()
        self.recover(continuation.core, "prepared", after=True)

    def test_request_install_entry_failure_completes_only_missing_stages(self):
        self.ready()
        self.recover(RequestRepository, "install_round", after=False)

    def test_request_install_lost_acknowledgement_does_not_append_twice(self):
        self.ready()
        self.recover(RequestRepository, "install_round", after=True)

    def test_packet_written_before_interruption_is_reconnected_without_rewrite(self):
        self.ready()
        self.interrupted(continuation, "_new_packet", after=True)
        packet = self.repo / self.args.directory / "packet.json"
        before = packet.read_bytes()
        modified = packet.stat().st_mtime_ns
        handoff = self.assert_ready(self.call())
        self.assertEqual(packet.read_bytes(), before)
        self.assertEqual(packet.stat().st_mtime_ns, modified)
        self.assertEqual(handoff["packet_path"], packet.relative_to(self.repo).as_posix())

    def test_core_admit_entry_failure_resumes_persisted_fence(self):
        self.ready()
        self.recover(continuation.core, "admit", after=False)

    def test_packet_intent_without_saved_bytes_stays_unknown_at_same_destination(self):
        self.ready()
        self.interrupted(TransitionRepository, "bind_packet", after=True)
        before = (self.state(), TransitionRepository(self.root / "transition.sqlite").read())
        self.assert_not_ready(self.call())
        self.assertFalse((self.repo / self.args.directory).exists())
        self.assertEqual((self.state(), TransitionRepository(self.root / "transition.sqlite").read()), before)
        self.assertEqual(RequestRepository(self.root / "request.sqlite").read_rounds(), ())

    def test_core_admit_lost_acknowledgement_preserves_one_operation_identity(self):
        self.ready()
        self.recover(continuation.core, "admit", after=True)

    def predecessor_for_recovery(self):
        self.ready()
        self.save_actor(B, "reviews/packet.json", "reviews/b.json")
        self.assert_ready(self.call())
        original = self.state().records[0]
        saved = {path.relative_to(self.repo).as_posix(): path.read_bytes()
                 for path in (self.repo / "reviews").rglob("*") if path.is_file()}
        self.args.directory = "reviews/continue-2"
        return original, saved

    def assert_second_operation_recovered(self, original, saved):
        handoff = self.assert_ready(self.call(), count=2)
        self.assertNotEqual(handoff["continuation_id"], original.continuation_id)
        retired, successor = self.state().records
        self.assertEqual(successor.predecessor_id, original.continuation_id)
        self.assertEqual(successor.sequence, original.sequence + 1)
        self.assertRegex(retired.retired_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertLessEqual(original.created_at, retired.retired_at)
        self.assertLessEqual(retired.retired_at, successor.created_at)
        canonical_basis = json.dumps(original.basis, ensure_ascii=False,
                                     sort_keys=True, separators=(",", ":")).encode("utf-8")
        basis_digest = sha256(b"taskgov-review-continuation-basis-v1\0" + canonical_basis).hexdigest()
        retirement = dict(continuation_id=original.continuation_id,
                          basis_digest=basis_digest,
                          request_identity_digest=None, worker_stop_identity=None)
        canonical_retirement = json.dumps(retirement, ensure_ascii=False,
                                          sort_keys=True, separators=(",", ":")).encode("utf-8")
        retirement_digest = sha256(b"taskgov-review-continuation-retirement-v1\0"
                                   + canonical_retirement).hexdigest()
        self.assertEqual(retired, replace(original, state="superseded",
                                         retired_at=retired.retired_at,
                                         retirement_digest=retirement_digest))
        settled = self.state()
        replay = self.assert_ready(self.call(), count=2)
        self.assertEqual(replay["continuation_id"], handoff["continuation_id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["review_requests"], [])
        self.assertEqual(self.state(), settled)
        for path, raw in saved.items():
            self.assertEqual((self.repo / path).read_bytes(), raw)
        self.assertFalse((self.root / "finalization.sqlite").exists())

    def test_second_fence_before_core_admission_recovers_without_losing_predecessor(self):
        original, saved = self.predecessor_for_recovery()
        self.interrupted(continuation.core, "admit", after=False)
        self.assertEqual(self.state().records, (original,))
        transitions = TransitionRepository(self.root / "transition.sqlite").read()
        self.assertEqual([row.phase for row in transitions], ["published", "fenced"])
        self.assertEqual(len(RequestRepository(self.root / "request.sqlite").read_rounds()), 1)
        reserved = transitions[-1].continuation_id
        self.assert_second_operation_recovered(original, saved)
        self.assertEqual(self.state().records[-1].continuation_id, reserved)

    def test_previous_request_publication_lost_acknowledgement_recovers_second_operation(self):
        original, saved = self.predecessor_for_recovery()
        self.interrupted(RequestRepository, "publish_without_wait", after=True)
        self.assertEqual(self.state().records, (original,))
        self.assertEqual([row.phase for row in TransitionRepository(self.root / "transition.sqlite").read()],
                         ["prepared"])
        self.assertEqual([row.phase for row in RequestRepository(self.root / "request.sqlite").read_rounds()],
                         ["published"])
        self.assert_second_operation_recovered(original, saved)

    def test_previous_transition_publication_lost_acknowledgement_recovers_second_operation(self):
        original, saved = self.predecessor_for_recovery()
        self.interrupted(TransitionRepository, "advance", after=True)
        self.assertEqual(self.state().records, (original,))
        self.assertEqual([row.phase for row in TransitionRepository(self.root / "transition.sqlite").read()],
                         ["published"])
        self.assertEqual([row.phase for row in RequestRepository(self.root / "request.sqlite").read_rounds()],
                         ["published"])
        self.assert_second_operation_recovered(original, saved)

    def test_superseded_operation_cannot_return_old_executable_ready_result(self):
        self.ready()
        first = self.assert_ready(self.call())
        old_packet = (self.repo / first["packet_path"]).read_bytes()
        self.args.directory = "reviews/continue-2"
        second = self.assert_ready(self.call(), count=2)
        self.assertNotEqual(first["continuation_id"], second["continuation_id"])
        settled = self.state()
        self.assertEqual(settled.records[0].state, "superseded")
        self.args.directory = "reviews/continue-1"
        self.assert_not_ready(self.call())
        self.assertEqual(self.state(), settled)
        self.assertEqual((self.repo / first["packet_path"]).read_bytes(), old_packet)

    def test_missing_ready_packet_is_not_recreated_or_reported_ready(self):
        self.ready()
        handoff = self.assert_ready(self.call())
        path = self.repo / handoff["packet_path"]
        original = path.read_bytes()
        path.rename(path.with_suffix(".retained"))
        self.assert_not_ready(self.call())
        self.assertFalse(path.exists())
        self.assertEqual(path.with_suffix(".retained").read_bytes(), original)

    def test_changed_ready_packet_is_not_overwritten_or_reported_ready(self):
        self.ready()
        handoff = self.assert_ready(self.call())
        path = self.repo / handoff["packet_path"]
        changed = path.read_bytes() + b"\n"
        path.write_bytes(changed)
        self.assert_not_ready(self.call())
        self.assertEqual(path.read_bytes(), changed)

    def test_changed_interrupted_packet_stays_unknown_without_rewrite(self):
        self.ready()
        self.interrupted(continuation, "_new_packet", after=True)
        path = self.repo / self.args.directory / "packet.json"
        changed = path.read_bytes() + b"\n"
        path.write_bytes(changed)
        self.assert_not_ready(self.call())
        self.assertEqual(path.read_bytes(), changed)
        self.assertEqual(len(self.state().records), 1)
        self.assertEqual(RequestRepository(self.root / "request.sqlite").read_rounds(), ())

    def test_conflicting_selection_cannot_take_over_existing_destination(self):
        self.ready()
        self.interrupted(continuation.core, "prepared", after=False)
        before = self.state()
        self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=B, action="supplement",
            prior_receipt_id=None, reason="Different selected reviewer")])).encode()
        self.assert_not_ready(self.call())
        self.assertEqual(self.state(), before)

    def test_incomplete_operation_cannot_escape_to_another_destination(self):
        self.ready()
        self.interrupted(continuation.core, "admit", after=True)
        before = self.state()
        self.args.directory = "reviews/different-destination"
        self.assert_not_ready(self.call())
        self.assertEqual(self.state(), before)
        self.assertFalse((self.repo / self.args.directory).exists())

    def old_request(self, *, phase):
        request = RequestRepository(self.root / "request.sqlite")
        with request.serial(create=True) as lease:
            request.append(lease, RequestRecord(continuation._digest(self.basis), ORIGINAL, ((A, TURN),)))
            if phase == "unknown":
                request.advance(lease, "unknown")
            else:
                request.advance(lease, "created", automation_id="timer-1")
                for stage in ("prepared", "started", "closing", "closed"):
                    request.advance(lease, stage)
        self.resolution.paths.review_wait_store = lambda *_: self.root / "wait.sqlite"
        return request

    def test_unknown_old_request_never_becomes_ready_on_repeated_continuation(self):
        self.ready()
        request = self.old_request(phase="unknown")
        before = request.read()
        self.assert_not_ready(self.call())
        self.assert_not_ready(self.call())
        self.assertEqual(request.read(), before)
        self.assertEqual(request.read_rounds(), ())
        self.assertFalse((self.repo / self.args.directory).exists())

    def test_closed_request_without_worker_exit_proof_is_not_ready(self):
        self.ready()
        request = self.old_request(phase="closed")
        before = request.read()
        self.assert_not_ready(self.call())
        self.assert_not_ready(self.call())
        self.assertEqual(request.read(), before)
        self.assertEqual(request.read_rounds(), ())
        self.assertFalse((self.repo / self.args.directory).exists())

    def current_occurrence(self, *, send_unknown=False, timer_unknown=False):
        from datetime import timedelta
        from uuid import uuid4
        from tests.test_review_wait_service import NOW
        from task_governance_tool.review_wait_runtime.review_wait_controller import (
            Reservation, Reviewer, ReviewWaitController, WaitBinding,
        )
        from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import (
            DirectRecord, DirectRepository, instant,
        )
        from task_governance_tool.review_wait_runtime.review_wait_direct import binding_digest

        self.ready()
        handoff = self.assert_ready(self.call())
        request = RequestRepository(self.root / "request.sqlite")
        with request.serial() as lease:
            request.append(lease, RequestRecord(continuation._digest(self.basis), ORIGINAL, ((A, TURN),),
                continuation_id=handoff["continuation_id"], continuation_sequence=handoff["continuation_sequence"]))
            request.advance(lease, "created", automation_id="timer-1")
            request.advance(lease, "prepared")
            request.advance(lease, "started")
        self.resolution.paths.review_wait_store = lambda *_: self.root / "wait.sqlite"
        binding = WaitBinding(wait_id="current-wait", parent_thread_id=self.caller.require(),
            **{key: self.basis[key] for key in WaitBinding.__dataclass_fields__
               if key not in ("wait_id", "parent_thread_id")})
        controller = ReviewWaitController(binding, (Reviewer(A, TURN),),
            Reservation("timer-1", self.caller.require(), "FREQ=MINUTELY;INTERVAL=10", "UTC", "PAUSED", "d" * 64))
        ReviewWaitRepository.create(self.root / "wait.sqlite", controller)
        direct = DirectRepository.for_wait(self.root / "wait.sqlite")
        now = NOW.replace(microsecond=0)
        record = DirectRecord(str(uuid4()), binding_digest(controller), ORIGINAL, "waiting", instant(now),
            instant(now + timedelta(minutes=20)), version=2, timer_phase="arming",
            timer_rule="FREQ=DAILY;BYHOUR=0;BYMINUTE=10;BYSECOND=0;COUNT=1", timer_updated_at=1,
            timer_due_at=instant(now + timedelta(minutes=10)), worker_id=str(uuid4()))
        direct.create_record(record)
        direct.update(record.probe_id, timer_phase="active", timer_updated_at=2)
        if send_unknown or timer_unknown:
            direct.update(record.probe_id, timer_phase="deleting")
            direct.update(record.probe_id, timer_phase="unknown" if timer_unknown else "deleted")
        if send_unknown:
            direct.update(record.probe_id, status="dispatching")
            direct.update(record.probe_id, status="unknown")
        return request, direct

    def assert_current_occurrence_unchanged(self, request, direct, *, ready):
        before = (request.read(), request.read_rounds(), direct.read(), self.state(), self.originals())
        result = self.call()
        if ready:
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["handoff"]["replayed"])
            self.assertEqual(result["handoff"]["review_requests"], [])
        else:
            self.assert_not_ready(result)
            self.assertEqual(result["errors"][0]["code"], "review_continuation_effect_unknown")
        self.assertEqual((request.read(), request.read_rounds(), direct.read(), self.state(), self.originals()), before)

    def test_current_started_occurrence_with_unknown_send_cannot_report_ready(self):
        self.assert_current_occurrence_unchanged(*self.current_occurrence(send_unknown=True), ready=False)

    def test_current_started_occurrence_with_unknown_timer_cannot_report_ready(self):
        self.assert_current_occurrence_unchanged(*self.current_occurrence(timer_unknown=True), ready=False)

    def test_current_healthy_started_occurrence_replays_without_worker_exit_requirement(self):
        self.assert_current_occurrence_unchanged(*self.current_occurrence(), ready=True)

    def test_unknown_old_send_blocks_even_after_timer_deleted_and_worker_joined(self):
        from datetime import timedelta
        from threading import Thread
        from uuid import uuid4
        from tests.test_review_wait_service import NOW
        from task_governance_tool.review_wait_runtime.review_wait_controller import (
            Reservation, Reviewer, ReviewWaitController, WaitBinding,
        )
        from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import (
            DirectRecord, DirectRepository, instant,
        )
        from task_governance_tool.review_wait_runtime.review_wait_direct import binding_digest

        self.ready()
        request = self.old_request(phase="closed")
        binding = WaitBinding(wait_id="old-wait", parent_thread_id=self.caller.require(),
            **{key: self.basis[key] for key in WaitBinding.__dataclass_fields__
               if key not in ("wait_id", "parent_thread_id")})
        reservation = Reservation("timer-1", self.caller.require(), "FREQ=MINUTELY;INTERVAL=10",
                                  "UTC", "PAUSED", "d" * 64)
        controller = ReviewWaitController(binding, (Reviewer(A, TURN),), reservation)
        wait_path = self.root / "wait.sqlite"
        ReviewWaitRepository.create(wait_path, controller)
        direct = DirectRepository.for_wait(wait_path)
        now = NOW.replace(microsecond=0)
        marker = DirectRecord(str(uuid4()), binding_digest(controller), ORIGINAL, "waiting", instant(now),
            instant(now + timedelta(minutes=20)), version=2, timer_phase="arming",
            timer_rule="FREQ=DAILY;BYHOUR=0;BYMINUTE=10;BYSECOND=0;COUNT=1", timer_updated_at=1,
            timer_due_at=instant(now + timedelta(minutes=10)), worker_id=str(uuid4()))
        direct.create_record(marker)
        direct.update(marker.probe_id, timer_phase="active", timer_updated_at=2)
        direct.update(marker.probe_id, timer_phase="deleting")
        direct.update(marker.probe_id, timer_phase="deleted")
        direct.update(marker.probe_id, status="dispatching")
        direct.update(marker.probe_id, status="unknown")
        # This inert fixture thread performs no wait/host operation. Its genuine
        # join proof isolates unknown send as the remaining blocking condition.
        worker = Thread(target=lambda: None)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        direct.confirm_worker_stopped(marker.probe_id, marker.worker_id, marker.binding_digest,
                                      instant(now + timedelta(seconds=1)))
        before = (request.read(), direct.read())
        for _ in range(2):
            response = self.call()
            self.assert_not_ready(response)
            self.assertEqual(response["errors"][0]["code"], "review_continuation_effect_unknown")
        self.assertEqual((request.read(), direct.read()), before)
        self.assertEqual(request.read_rounds(), ())
        self.assertFalse((self.repo / self.args.directory).exists())


class IntegratedContinuationRecoveryTests(RecoveryAssertions, unittest.TestCase):
    setUp = integrated.FinalizationContinuationTests.setUp
    physical = integrated.FinalizationContinuationTests.physical
    publish = integrated.FinalizationContinuationTests.publish
    git = integrated.FinalizationContinuationTests.git
    ready = integrated.FinalizationContinuationTests.ready
    call = integrated.FinalizationContinuationTests.call
    save_actor = integrated.FinalizationContinuationTests.save_actor

    def test_fence_before_core_admission_blocks_old_finalizer_until_same_operation_recovers(self):
        from task_governance_tool.review_finalization import Finalizer

        self.ready()
        old = self.save_actor(B, "reviews/packet.json", "reviews/b.json")
        root = self.journal.read_rounds()[0]
        head = self.git("rev-parse", "HEAD")
        self.interrupted(continuation.core, "admit", after=False)
        self.assertEqual(self.state().records, ())
        fence = TransitionRepository(self.root / "transition.sqlite").read()[-1]
        self.assertEqual(fence.phase, "fenced")
        service = Finalizer(self.repo, self.task_id, self.caller,
                            generation=self.basis["target_generation"])
        blocked = service.execute()
        self.assertEqual(blocked["blocking_code"], "review_continuation_conflict", blocked)
        self.assertEqual(blocked["stages"], dict(registration="not_started", commit="not_started",
                                                completion="not_started"))
        self.assertEqual(self.journal.read_rounds(), (root,))
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        handoff = self.assert_ready(self.call())
        self.assertEqual(handoff["continuation_id"], fence.continuation_id)
        prepared = TransitionRepository(self.root / "transition.sqlite").read()[-1]
        self.save_actor(A, handoff["packet_path"], prepared.result_paths[0])
        completed_registration = service.execute()
        self.assertEqual(completed_registration["stages"]["registration"], "succeeded", completed_registration)
        self.assertNotEqual(completed_registration["blocking_code"], "review_continuation_conflict")
        self.assertEqual(completed_registration["stages"]["commit"], "not_started")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual((self.repo / "reviews/b.json").read_bytes(), old)

    def test_finalization_append_entry_failure_reconnects_same_packet_and_root(self):
        self.ready()
        original = self.journal.read_rounds()[0]
        head = self.git("rev-parse", "HEAD")
        self.recover(FinalizationRepository, "append_round", after=False)
        self.assertEqual(self.journal.read_rounds()[0], original)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_finalization_append_lost_acknowledgement_does_not_append_twice(self):
        self.ready()
        original = self.journal.read_rounds()[0]
        head = self.git("rev-parse", "HEAD")
        self.recover(FinalizationRepository, "append_round", after=True)
        self.assertEqual(self.journal.read_rounds()[0], original)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_unknown_integrated_effect_blocks_ready_retry_without_repeating_commit(self):
        self.ready()
        old = self.save_actor(B, "reviews/packet.json", "reviews/b.json")
        handoff = self.assert_ready(self.call())
        result_path = handoff["review_requests"][0]["result_path"]
        new = self.save_actor(A, handoff["packet_path"], result_path)
        with self.journal.serial() as lease:
            self.journal.advance(lease, registration="dispatching",
                originals=((B, sha256(old).hexdigest()), (A, sha256(new).hexdigest())),
                original_locations=(("reviews/packet.json", "reviews/b.json"),
                                    (handoff["packet_path"], result_path)))
        before = self.journal.read()
        head = self.git("rev-parse", "HEAD")
        self.assert_not_ready(self.call())
        self.assertEqual(self.journal.read(), before)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)


class HistoricalRequestAssertions:
    """Real historical native target and joined offline worker; no host calls."""

    def historical_fixture(self, *, continued=False, send_status="accepted", joined=True):
        from datetime import timedelta
        from threading import Thread
        from uuid import uuid4
        from tests.test_review_wait_service import NOW
        from task_governance_tool.review_wait_runtime.review_wait_controller import Reservation, Reviewer, ReviewWaitController, WaitBinding
        from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import DirectRecord, DirectRepository, instant
        from task_governance_tool.review_wait_runtime.review_wait_direct import binding_digest

        self.ready()
        old_basis = self.basis
        old_handoff = None
        if continued is True:
            result = self.call()
            self.assertTrue(result["ok"], result)
            old_handoff = result["handoff"]
        request = RequestRepository(self.root / "request.sqlite")
        with request.serial(create=not request.path.exists()) as lease:
            request.append(lease, RequestRecord(continuation._digest(old_basis), ORIGINAL, ((A, TURN),),
                continuation_id=old_handoff["continuation_id"] if old_handoff else None,
                continuation_sequence=old_handoff["continuation_sequence"] if old_handoff else 0))
            request.advance(lease, "created", automation_id="historical-timer")
            for phase in ("prepared", "started", "closing", "closed"):
                request.advance(lease, phase)
        binding = WaitBinding(wait_id="historical-wait", parent_thread_id=self.caller.require(),
            **{key: old_basis[key] for key in WaitBinding.__dataclass_fields__ if key not in ("wait_id", "parent_thread_id")})
        self.resolution.paths.review_wait_store = lambda *_: self.root / "historical-wait.sqlite"
        controller = ReviewWaitController(binding, (Reviewer(A, TURN),),
            Reservation("historical-timer", self.caller.require(), "FREQ=MINUTELY;INTERVAL=10", "UTC", "PAUSED", "d" * 64))
        ReviewWaitRepository.create(self.root / "historical-wait.sqlite", controller)
        direct = DirectRepository.for_wait(self.root / "historical-wait.sqlite")
        now = NOW.replace(microsecond=0)
        marker = DirectRecord(str(uuid4()), binding_digest(controller), ORIGINAL, "waiting", instant(now),
            instant(now + timedelta(minutes=20)), version=2, timer_phase="arming",
            timer_rule="FREQ=DAILY;BYHOUR=0;BYMINUTE=10;BYSECOND=0;COUNT=1", timer_updated_at=1,
            timer_due_at=instant(now + timedelta(minutes=10)), worker_id=str(uuid4()))
        direct.create_record(marker)
        for phase in ("active", "deleting", "deleted"):
            direct.update(marker.probe_id, timer_phase=phase, **({"timer_updated_at": 2} if phase == "active" else {}))
        direct.update(marker.probe_id, status="dispatching")
        direct.update(marker.probe_id, status=send_status)
        worker = Thread(target=lambda: None)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        if joined:
            direct.confirm_worker_stopped(marker.probe_id, marker.worker_id, marker.binding_digest, instant(now + timedelta(seconds=1)))
        if continued in ("ordinary", "prepared"):
            result = self.call()
            self.assertTrue(result["ok"], result)
            if continued == "ordinary":
                with request.serial() as lease:
                    request.publish_without_wait(lease, request.read_rounds()[-1])
        self.fresh_target_preparation()
        return request, direct, binding

    def fresh_target_preparation(self):
        from tests.verification_receipt_test_support import set_target, run_taskgov
        from task_governance_tool.review_wait_basis_repository import read_preparation_basis, read_wait_basis
        from task_governance_tool.review_finalization_repository import FinalizationRecord
        from task_governance_tool import review_preparation, review_results
        old_basis = self.basis
        if hasattr(self, "journal"):
            (self.repo / "source.py").write_text("value = 3\n", encoding="utf-8")
            self.git("add", "source.py")
            result = run_taskgov("review", "target", "set", "--repo", str(self.repo), "--db", str(self.db),
                self.task_id, "--kind", "git_snapshot", "--json")
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        else:
            set_target(self.db, self.repo, self.task_id, fingerprint="sha256:" + "b" * 64)
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            self.basis = read_preparation_basis(connection, project_id=self.target.project.project_id,
                task_id=self.task_id, caller=self.caller)
            wait = read_wait_basis(connection, project_id=self.target.project.project_id, task_id=self.task_id)
        self.assertGreater(self.basis["target_generation"], old_basis["target_generation"])
        packet = json.loads(self.path.read_bytes())
        packet["review_target"] = {key: self.basis["target_" + key] for key in ("kind", "value", "base_revision", "generation")}
        packet["result_template"] = review_results.review_result_template(self.task_id, self.basis["contract_revision"], packet["review_target"])
        destination = self.repo / "reviews/new-root"
        destination.mkdir()
        raw = json.dumps(packet).encode()
        (destination / "packet.json").write_bytes(raw)
        self.resolution.paths.review_preparation_store = lambda *_: self.root / "new-preparation.sqlite"
        self.resolution.paths.review_continuation_store = lambda *_: self.root / "new-transition.sqlite"
        route = "manual"
        if hasattr(self, "journal"):
            route = "integrated"
            identity = sha256((self.caller.require() + ":" + self.task_id).encode("ascii")).hexdigest()
            journal_path = self.root / f"finalize-{identity}-{self.basis['target_generation']}.sqlite"
            self.resolution.paths.review_finalization_store = lambda *_: journal_path
            self.journal = FinalizationRepository(journal_path)
            intent = FinalizationRecord(wait, "reviews/new-root/packet.json", sha256(raw).hexdigest(),
                ("reviews/new-root/a.json", "reviews/new-root/b.json"), self.git("symbolic-ref", "HEAD").decode().strip())
            with self.journal.serial(initial=intent):
                pass
        review_preparation.publish_ready(self.repo, "reviews/new-root/packet.json",
            ["reviews/new-root/a.json", "reviews/new-root/b.json"], self.caller, route=route)
        self.args.target_generation = self.basis["target_generation"]
        self.args.directory = "reviews/new-continue"

    def no_attempt_fixture(self):
        self.ready()
        first = self.call()
        self.assertTrue(first["ok"], first)
        request = RequestRepository(self.root / "request.sqlite")
        self.assertEqual(request.read(), (0, None))
        old = request.read_rounds()[-1]
        self.assertEqual(old.phase, "prepared")
        self.fresh_target_preparation()
        return request, old

    def assert_no_attempt_ready(self, request, old):
        originals = {path: path.read_bytes() for path in (self.repo / "reviews").rglob("*.json")}
        result = self.call()
        self.assertTrue(result["ok"], result)
        self.assertEqual(request.read(), (0, None))
        rounds = request.read_rounds()
        self.assertEqual(len(rounds), 2)
        self.assertEqual(rounds[0], replace(old, phase="published"))
        self.assertEqual(rounds[1].wait_basis_digest, continuation._digest(self.basis))
        self.assertIsNone(rounds[1].worker_stop_identity)
        self.assertIsNone(rounds[1].expected_request_digest)
        self.assertIsNone(rounds[1].old_automation_id)
        for path, raw in originals.items():
            self.assertEqual(path.read_bytes(), raw)
        again = self.call()
        self.assertTrue(again["ok"], again)
        self.assertTrue(again["handoff"]["replayed"])
        self.assertEqual(again["handoff"]["review_requests"], [])
        self.assertEqual(request.read_rounds(), rounds)
        self.assertEqual(request.read(), (0, None))
        return rounds[1]

    def test_unused_old_round_without_any_attempt_is_retired_without_worker_evidence(self):
        request, old = self.no_attempt_fixture()
        self.assert_no_attempt_ready(request, old)

    def test_no_attempt_residue_keeps_new_native_fence_and_packet_identity(self):
        request, old = self.no_attempt_fixture()
        # Reproduce the prior binary's early return, leaving a genuine new
        # prepared Packet/native/fence while the old round remains prepared.
        with mock.patch.object(continuation, "_historical_admission", return_value=None):
            result = self.call()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["errors"][0]["code"], "state_transition_invalid")
        transition = TransitionRepository(self.root / "new-transition.sqlite").read()[-1]
        self.assertEqual(transition.phase, "prepared")
        original = (self.repo / transition.packet_path).read_bytes()
        latest = self.assert_no_attempt_ready(request, old)
        self.assertEqual(latest.continuation_id, transition.continuation_id)
        self.assertEqual((self.repo / transition.packet_path).read_bytes(), original)

    def test_no_attempt_retirement_lost_acknowledgement_resumes_without_duplicate_round(self):
        request, old = self.no_attempt_fixture()
        publish = RequestRepository.publish_without_wait
        def interrupted(*args, **kwargs):
            publish(*args, **kwargs)
            raise RuntimeError("retirement acknowledgement lost")
        with mock.patch.object(RequestRepository, "publish_without_wait", autospec=True, side_effect=interrupted):
            result = self.call()
        self.assertFalse(result["ok"], result)
        self.assertEqual(request.read_rounds(), (replace(old, phase="published"),))
        self.assert_no_attempt_ready(request, old)

    def test_no_attempt_same_generation_unused_round_uses_existing_supersession(self):
        self.ready()
        first = self.call()
        self.assertTrue(first["ok"], first)
        request = RequestRepository(self.root / "request.sqlite")
        old = request.read_rounds()[-1]
        self.args.directory = "reviews/next-unused"
        self.assert_no_attempt_ready(request, old)

    def test_no_attempt_history_requires_strict_old_native_identity_selection_and_current_basis(self):
        from task_governance_tool import review_continuation_repository, review_wait_basis_repository
        request, old = self.no_attempt_fixture()
        state = continuation.core.read_state(self.target, self.task_id, self.caller)
        native = next(row for row in state.records if row.continuation_id == old.continuation_id)
        validate = review_wait_basis_repository.validate_historical_wait
        before = request.path.read_bytes()
        for changes in ({"target_generation": self.basis["target_generation"]},
                {"target_generation": self.basis["target_generation"] + 1}, {"project_id": "other-project"},
                {"project_path_hash": "f" * 64}, {"project_binding_generation": self.basis["project_binding_generation"] + 1},
                {"task_id": "tg_task_" + "f" * 16}, {"parent_session_id": B}):
            altered = replace(native, basis={**native.basis, **changes})
            operational = replace(old, basis_digest=continuation.values.basis_digest(altered.basis),
                wait_basis_digest=continuation._digest(altered.basis))
            with self.subTest(changes=changes), mock.patch.object(review_continuation_repository, "read_continuations", return_value=(altered,)):
                with self.assertRaises(Exception) as caught:
                    validate(self.target, self.basis, None, None, (operational,))
                self.assertEqual(caught.exception.code, "review_continuation_basis_changed")
        for rows, operational in (((), old), ((replace(native, state="preparing"),), old),
                ((native,), replace(old, selection_digest="f" * 64)),
                ((native,), replace(old, preparation_id="tg_review_preparation_" + "f" * 16))):
            with mock.patch.object(review_continuation_repository, "read_continuations", return_value=rows):
                with self.assertRaises(Exception) as caught:
                    validate(self.target, self.basis, None, None, (operational,))
                self.assertEqual(caught.exception.code, "review_continuation_basis_changed")
        with self.assertRaises(Exception) as caught:
            validate(self.target, {**self.basis, "ownership_generation": self.basis["ownership_generation"] + 1}, None, None, (old,))
        self.assertEqual(caught.exception.code, "review_continuation_basis_changed")
        self.assertEqual(request.path.read_bytes(), before)

    def assert_historical_ready(self, request, direct, *, previous_rounds=0):
        before = request.read(), direct.read()
        originals = {path: path.read_bytes() for path in (self.repo / "reviews").rglob("*.json")}
        result = self.call()
        self.assertTrue(result["ok"], result)
        self.assertEqual((request.read(), direct.read()), before)
        self.assertEqual(len(request.read_rounds()), previous_rounds + 1)
        latest = request.read_rounds()[-1]
        self.assertEqual(latest.wait_basis_digest, continuation._digest(self.basis))
        self.assertNotEqual(latest.wait_basis_digest, request.read()[1].basis_digest)
        self.assertEqual(latest.expected_request_digest, continuation.request_identity_digest(request.read()[1]))
        self.assertEqual(latest.worker_stop_identity, direct.read().worker_stop_identity)
        for path, raw in originals.items():
            self.assertEqual(path.read_bytes(), raw)
        again = self.call()
        self.assertTrue(again["ok"], again)
        self.assertTrue(again["handoff"]["replayed"])
        self.assertEqual(again["handoff"]["review_requests"], [])
        self.assertEqual(request.read_rounds()[-1], latest)
        return latest

    def test_first_continuation_accepts_joined_closed_older_ordinary_request(self):
        request, direct, _ = self.historical_fixture()
        self.assert_historical_ready(request, direct)

    def test_first_continuation_preserves_published_older_continuation(self):
        request, direct, _ = self.historical_fixture(continued=True)
        old = request.read_rounds()[0]
        self.assert_historical_ready(request, direct, previous_rounds=1)
        self.assertEqual(request.read_rounds()[0], old)

    def test_v2_ordinary_request_keeps_its_published_native_round_history(self):
        request, direct, _ = self.historical_fixture(continued="ordinary")
        old = request.read_rounds()[0]
        self.assertIsNone(request.read()[1].continuation_id)
        self.assert_historical_ready(request, direct, previous_rounds=1)
        self.assertEqual(request.read_rounds()[0], old)

    def test_exact_unused_older_prepared_round_is_retired_only_monotonically(self):
        request, direct, _ = self.historical_fixture(continued="prepared")
        old = request.read_rounds()[0]
        self.assertEqual(old.phase, "prepared")
        self.assert_historical_ready(request, direct, previous_rounds=1)
        self.assertEqual(request.read_rounds()[0], replace(old, phase="published"))

    def test_next_current_round_can_still_settle_the_older_request_without_new_wait(self):
        request, direct, _ = self.historical_fixture()
        first = self.assert_historical_ready(request, direct)
        self.args.directory = "reviews/new-continue-2"
        second = self.assert_historical_ready(request, direct, previous_rounds=1)
        self.assertEqual(second.continuation_sequence, first.continuation_sequence + 1)
        self.assertEqual(request.read_rounds()[0], replace(first, phase="published"))

    def test_historical_request_install_entry_failure_resumes_exact_prepared_packet(self):
        request, direct, _ = self.historical_fixture()
        with mock.patch.object(RequestRepository, "install_round", side_effect=RuntimeError("before install")):
            result = self.call()
        self.assertFalse(result["ok"], result)
        transition = TransitionRepository(self.root / "new-transition.sqlite").read()[-1]
        saved = (self.repo / transition.packet_path).read_bytes()
        latest = self.assert_historical_ready(request, direct)
        self.assertEqual(latest.continuation_id, transition.continuation_id)
        self.assertEqual((self.repo / transition.packet_path).read_bytes(), saved)

    def test_existing_old_basis_fence_and_core_pending_resume_without_replacement(self):
        request, direct, _ = self.historical_fixture()
        with mock.patch.object(continuation, "observe_cleanup", return_value=SimpleNamespace(
                state="unknown", reason="review_continuation_conflict")):
            result = self.call()
        self.assertFalse(result["ok"], result)
        fence = TransitionRepository(self.root / "new-transition.sqlite").read()[-1]
        self.assertEqual(fence.phase, "fenced")
        latest = self.assert_historical_ready(request, direct)
        self.assertEqual(latest.continuation_id, fence.continuation_id)

    def test_historical_request_install_lost_acknowledgement_keeps_exact_round(self):
        request, direct, _ = self.historical_fixture(continued=True)
        original = RequestRepository.install_round
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("lost local acknowledgement")
        with mock.patch.object(RequestRepository, "install_round", side_effect=interrupted, autospec=True):
            result = self.call()
        self.assertFalse(result["ok"], result)
        before = request.read_rounds()
        self.assert_historical_ready(request, direct, previous_rounds=1)
        self.assertEqual(request.read_rounds(), before)

    def test_unknown_historical_send_blocks_before_new_native_admission(self):
        request, direct, _ = self.historical_fixture(continued=True, send_status="unknown")
        before = request.read(), request.read_rounds(), direct.read(), continuation.core.read_state(self.target, self.task_id, self.caller)
        result = self.call()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["errors"][0]["code"], "review_continuation_effect_unknown")
        self.assertEqual(TransitionRepository(self.root / "new-transition.sqlite").read(), ())
        self.assertEqual((request.read(), request.read_rounds(), direct.read(),
            continuation.core.read_state(self.target, self.task_id, self.caller)), before)

    def test_unjoined_historical_worker_blocks_before_new_native_admission(self):
        request, direct, _ = self.historical_fixture(joined=False)
        before = request.read(), direct.read()
        result = self.call()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["errors"][0]["code"], "review_continuation_worker_exit_unknown")
        self.assertEqual(TransitionRepository(self.root / "new-transition.sqlite").read(), ())
        self.assertEqual((request.read(), direct.read()), before)

    def test_pending_native_history_is_not_admitted_by_a_prepared_operational_round(self):
        from task_governance_tool import review_continuation_repository
        request, direct, _ = self.historical_fixture(continued=True)
        state = continuation.core.read_state(self.target, self.task_id, self.caller)
        pending = replace(state.records[0], state="preparing")
        before = request.read_rounds(), direct.read()
        with mock.patch.object(review_continuation_repository, "read_continuations", return_value=(pending,)):
            result = self.call()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["errors"][0]["code"], "review_continuation_basis_changed")
        self.assertEqual(TransitionRepository(self.root / "new-transition.sqlite").read(), ())
        self.assertEqual((request.read_rounds(), direct.read()), before)

    def test_native_history_rejects_same_future_generation_and_identity_substitutions(self):
        from task_governance_tool.review_wait_basis_repository import validate_historical_wait
        request, _, binding = self.historical_fixture(continued=True)
        current = self.basis
        for changes in ({"target_generation": current["target_generation"]},
                {"target_generation": current["target_generation"] + 1}, {"project_id": "another-project"},
                {"project_path_hash": "f" * 64}, {"project_binding_generation": current["project_binding_generation"] + 1},
                {"task_id": "tg_task_" + "f" * 16}, {"parent_thread_id": B},
                {"execution_id": "tg_execution_" + "f" * 16}, {"ownership_generation": current["ownership_generation"] + 1},
                {"artifact_manifest_id": "tg_artifact_manifest_" + "f" * 16}, {"target_value": "sha256:" + "f" * 64}):
            with self.subTest(changes=changes):
                altered = replace(binding, **changes)
                old = replace(request.read()[1], basis_digest=continuation.wait_digest(altered))
                with self.assertRaises(Exception) as caught:
                    validate_historical_wait(self.target, current, altered, old, request.read_rounds())
                self.assertEqual(caught.exception.code, "review_continuation_basis_changed")
        with self.assertRaises(Exception) as caught:
            validate_historical_wait(self.target, current, binding,
                replace(request.read()[1], reviewers=((B, TURN),)), request.read_rounds())
        self.assertEqual(caught.exception.code, "review_continuation_basis_changed")


class ManualHistoricalRequestTests(HistoricalRequestAssertions, unittest.TestCase):
    setUp = manual.HandoffContinuationTests.setUp
    physical = manual.HandoffContinuationTests.physical
    publish = manual.HandoffContinuationTests.publish
    ready = manual.HandoffContinuationTests.ready
    call = manual.HandoffContinuationTests.call


class IntegratedHistoricalRequestTests(HistoricalRequestAssertions, unittest.TestCase):
    setUp = integrated.FinalizationContinuationTests.setUp
    physical = integrated.FinalizationContinuationTests.physical
    publish = integrated.FinalizationContinuationTests.publish
    git = integrated.FinalizationContinuationTests.git
    ready = integrated.FinalizationContinuationTests.ready
    call = integrated.FinalizationContinuationTests.call


if __name__ == "__main__":
    unittest.main()
