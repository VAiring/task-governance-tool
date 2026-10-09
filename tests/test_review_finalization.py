"""Installed helper through real originals, existing gates, Git and Task done."""

import json
import shlex
import subprocess
import threading
import time
from uuid import uuid4
from dataclasses import replace
from unittest import mock

from tests import test_review_handoff_preparation as fixtures
from tests.test_review_results import encode, receipt
from task_governance_tool import review_finalization as finalization, review_wait_basis
from task_governance_tool.session_identity import capture_caller_identity


class InstalledFinalizationTests(fixtures.PreparationFixture):
    git = fixtures.ReviewerMaterialTests.git
    committed_fixture = fixtures.ReviewerMaterialTests.committed_fixture

    def prepared(self):
        self.committed_fixture()
        task = self.task("Focused isolated verification")
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "source.py")
        target_call = self.invoke("prepare-finalization", "--repo", str(self.root), "--directory=reviews/g1",
                                 "target", task, "--kind=git_snapshot")
        self.assertEqual(target_call.returncode, 0, target_call.stdout or target_call.stderr)
        target = json.loads(target_call.stdout)
        self.assertEqual(target["handoff"], {"status": "not_applicable"})
        self.assertEqual(target["source"]["verification_route"], "receipt_required")
        completed = self.invoke("prepare-finalization", "--repo", str(self.root), "--directory=reviews/g1",
            "receipt", task, "--result=pass", "--duration-ms=1", "--scope-coverage=full",
            "--expected-target-generation=" + str(target["source"]["review_target"]["generation"]))
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        context = json.loads(completed.stdout)["handoff"]
        self.assertEqual(context["finalization"]["status"], "prepared")
        self.assertEqual(set(context), {"status", "packet_path", "review_requests", "review_wait",
                                       "finalization", "finalization_command"})
        self.assertNotIn("--commit-approved", context["finalization_command"])
        return task, context

    def save(self, context, index, *, finding=False):
        packet = json.loads((self.root / context["packet_path"]).read_bytes())
        payload = packet["result_template"]
        payload["receipts"] = [receipt("reviewer-" + str(index), findings=(
            [{"severity": "low", "summary": "source.py:1 Confirm the documented choice"}] if finding else []))]
        result = self.invoke("save", "--repo", str(self.root), "--packet", context["packet_path"],
            "--output", context["review_requests"][index]["result_path"], raw=encode(payload), reviewer=index)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)

    def finalize(self, task, *, check=False):
        result = self.invoke("finalize", "--repo", str(self.root), "--task-id", task, *(["--check"] if check else []))
        return result, json.loads(result.stdout)

    def service(self, task):
        for module in (finalization, review_wait_basis):
            self.enterContext(mock.patch.object(module, "__file__", str(self.install.skill_root /
                "scripts/task_governance_tool" / (module.__name__.split(".")[-1] + ".py"))))
        return finalization.Finalizer(self.root, task, capture_caller_identity())

    def ready(self):
        task, context = self.prepared()
        for index in range(2):
            self.save(context, index)
        return task, self.service(task)

    def test_complete_exact_target_and_duplicate_is_read_only(self):
        task, context = self.prepared()
        for index in range(2):
            self.save(context, index)
        (self.root / "source.py").write_text("value = 99  # unstaged\n")
        # Exercise the exact returned workerless continuation, including its
        # retained generation, rather than reconstructing a command from prose.
        result = subprocess.run(shlex.split(context["finalization_command"].removeprefix("& ")),
                                cwd=self.root, capture_output=True, check=False)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(report["stages"], dict(registration="succeeded", commit="succeeded", completion="succeeded"))
        self.assertEqual(len(report["registered_receipt_ids"]), 2)
        self.assertEqual(report["verification"]["counts"]["qualifying_exact_current"], 1)
        self.assertEqual(self.git("show", "HEAD:source.py"), b"value = 2\n")
        commit = self.git("rev-parse", "HEAD")
        self.assertEqual(self.cli("task", "show", task)["task"]["status"], "done")
        second, repeated = self.finalize(task)
        self.assertEqual(second.returncode, 0, repeated)
        self.assertEqual(repeated["commit_id"], report["commit_id"])
        self.assertEqual(self.git("rev-parse", "HEAD"), commit)
        self.assertEqual(self.cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 2)

    def test_missing_original_keeps_complete_batch_atomic(self):
        task, context = self.prepared()
        self.save(context, 0)
        before = self.git("rev-parse", "HEAD")
        result, report = self.finalize(task)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["blocking_code"], "finalization_originals_unavailable", report)
        self.assertEqual([r["status"] for r in report["originals"]], ["available", "unavailable"])
        self.assertEqual(report["stages"]["registration"], "not_started")
        self.assertEqual(self.cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.save(context, 1)
        result, report = self.finalize(task)
        self.assertEqual(result.returncode, 0, report)

    def test_findings_register_but_stop_before_commit(self):
        task, context = self.prepared()
        self.save(context, 0, finding=True)
        self.save(context, 1)
        before = self.git("rev-parse", "HEAD")
        result, report = self.finalize(task)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["blocking_code"], "finalization_findings_require_judgment", report)
        self.assertEqual(report["stages"]["registration"], "succeeded")
        self.assertEqual(report["stages"]["commit"], "not_started")
        self.assertEqual(len(report["reviews"]["current_findings"]), 1)
        self.assertEqual(len(report["registered_findings"]), 1, report)
        self.assertEqual(report["registered_findings"][0]["review_finding_id"],
                         report["reviews"]["current_findings"][0]["review_finding_id"])
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        finding = report["registered_findings"][0]["review_finding_id"]
        self.cli("review", "finding", "resolve", finding, "--resolution", "Documented choice confirmed")
        _, recovered = self.finalize(task)
        self.assertEqual(recovered["status"], "completed", recovered)
        self.assertEqual(recovered["reviews"]["current_findings"], [])
        self.assertEqual(recovered["registered_findings"][0]["review_finding_id"], finding)
        self.assertEqual(recovered["registered_findings"][0]["status"], "resolved")

    def test_lost_registration_ack_recovers_actual_batch_without_replay(self):
        task, service = self.ready()
        original = service.core.register
        def lose(*args, **kwargs):
            original(*args, **kwargs)
            raise finalization.FinalizationError("registration_ack_unknown")
        with mock.patch.object(service.core, "register", side_effect=lose):
            first = service.execute()
        self.assertEqual(first["stages"]["registration"], "dispatching")
        self.assertEqual(len(first["observed_registered_receipt_ids"]), 2, first)
        with mock.patch.object(service.core, "register", side_effect=AssertionError("duplicate")):
            second = service.execute()
        self.assertEqual(second["status"], "completed", second)
        self.assertEqual(len(second["reviews"]["current_receipts"]), 2)

    def test_lost_publish_ack_recovers_same_commit(self):
        task, service = self.ready()
        original = finalization.git.publish_candidate
        def lose(*args, **kwargs):
            original(*args, **kwargs)
            raise finalization.FinalizationError("publication_ack_unknown")
        with mock.patch.object(finalization.git, "publish_candidate", side_effect=lose):
            first = service.execute()
        self.assertEqual(first["commit_observation"], "published", first)
        with mock.patch.object(finalization.git, "publish_candidate", side_effect=AssertionError("duplicate")):
            second = service.execute()
        self.assertEqual(second["status"], "completed", second)
        self.assertEqual(first["commit_id"], second["commit_id"])

    def test_known_unpublished_failure_reuses_candidate_after_observation(self):
        task, service = self.ready()
        with mock.patch.object(finalization.git, "publish_candidate", side_effect=
                               finalization.git.FinalizationGitError("finalization_git_busy")):
            first = service.execute()
        self.assertEqual(first["commit_observation"], "not_published", first)
        with mock.patch.object(finalization.git, "create_candidate", side_effect=AssertionError("new candidate")):
            second = service.execute()
        self.assertEqual(second["status"], "completed", second)
        self.assertEqual(first["candidate_commit_id"], second["commit_id"])

    def test_completion_failure_and_lost_ack_never_repeat_success(self):
        task, service = self.ready()
        original = service.core.complete
        def fail(*args, **kwargs):
            if kwargs.get("check"):
                return original(*args, **kwargs)
            raise finalization.FinalizationError("completion_failed")
        with mock.patch.object(service.core, "complete", side_effect=fail):
            first = service.execute()
        self.assertEqual(first["stages"]["commit"], "succeeded", first)
        self.assertEqual(first["stages"]["completion"], "dispatching", first)
        def lose(*args, **kwargs):
            result = original(*args, **kwargs)
            if not kwargs.get("check"):
                raise finalization.FinalizationError("completion_ack_unknown")
            return result
        with mock.patch.object(service.core, "complete", side_effect=lose):
            second = service.execute()
        self.assertEqual(second["task_status"], "done", second)
        with mock.patch.object(service.core, "complete", side_effect=AssertionError("duplicate")):
            third = service.execute()
        self.assertEqual(third["status"], "completed", third)
        self.assertEqual(third["commit_id"], first["commit_id"])

    def test_changed_index_stops_without_recapture_or_publication(self):
        task, service = self.ready()
        (self.root / "source.py").write_text("value = 3\n")
        self.git("add", "source.py")
        before = self.git("rev-parse", "HEAD")
        result = service.execute()
        self.assertEqual(result["blocking_code"], "review_target_mismatch", result)
        self.assertEqual(result["stages"]["commit"], "not_started")
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_failed_reviewer_registers_results_but_does_not_commit(self):
        task, service = self.ready()
        from tests.test_review_wait_service import TURN
        observations = tuple((self.review_environment(i)["CODEX_THREAD_ID"], TURN,
                              "failed" if i else "completed") for i in range(2))
        before = self.git("rev-parse", "HEAD")
        result = service.execute(reviewer_observer=lambda: observations)
        self.assertEqual(result["blocking_code"], "finalization_reviewer_failed", result)
        self.assertEqual(result["stages"]["registration"], "succeeded")
        self.assertEqual(result["stages"]["commit"], "not_started")
        for check in (True, False):
            _, recovered = self.finalize(task, check=check)
            self.assertEqual(recovered["blocking_code"], "finalization_reviewer_failed", recovered)
            self.assertEqual(recovered["reviewer_observations"], result["reviewer_observations"])
            self.assertEqual(recovered["stages"], result["stages"])
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.cli("task", "show", task)["task"]["status"], "review_pending")

    def pause_resume(self, task):
        self.cli("task", "edit", task, "--status", "paused", "--pause-reason", "Concurrent recovery fixture")
        self.cli("task", "edit", task, "--status", "in_progress")
        self.cli("task", "edit", task, "--status", "review_pending")

    def add_low(self, task):
        receipt_id = self.cli("task", "show", task)["review_evidence"]["current_receipts"][0]["review_receipt_id"]
        return self.cli("review", "finding", "add", task, "--receipt-id", receipt_id,
                        "--severity", "low", "--summary", "source.py:1 Late reviewer observation")["finding"]

    def test_same_parent_reacquisition_before_registration_rejects_retained_basis(self):
        task, service = self.ready()
        before = self.git("rev-parse", "HEAD")
        original = service.core.register
        def race(*args, **kwargs):
            self.pause_resume(task)
            return original(*args, **kwargs)
        with mock.patch.object(service.core, "register", side_effect=race):
            result = service.execute()
        self.assertEqual(result["blocking_code"], "task_ownership_changed", result)
        state = self.cli("task", "show", task)
        self.assertEqual(state["review_evidence"]["counts"]["receipts_current_generation"], 0)
        self.assertEqual(state["task"]["status"], "review_pending")
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_same_parent_reacquisition_before_completion_preserves_commit_but_not_done(self):
        task, service = self.ready()
        original = service.core.complete
        def race(*args, **kwargs):
            if not kwargs.get("check"):
                self.pause_resume(task)
            return original(*args, **kwargs)
        with mock.patch.object(service.core, "complete", side_effect=race):
            result = service.execute()
        self.assertEqual(result["blocking_code"], "task_ownership_changed", result)
        self.assertEqual(result["stages"]["commit"], "succeeded", result)
        self.assertEqual(self.cli("task", "show", task)["task"]["status"], "review_pending")
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), result["commit_id"])

    def test_new_low_after_candidate_blocks_publication(self):
        task, service = self.ready()
        before = self.git("rev-parse", "HEAD")
        original = finalization.git.create_candidate
        def race(*args, **kwargs):
            candidate = original(*args, **kwargs)
            self.add_low(task)
            return candidate
        with mock.patch.object(finalization.git, "create_candidate", side_effect=race):
            result = service.execute()
        self.assertEqual(result["blocking_code"], "finalization_findings_require_judgment", result)
        self.assertEqual(result["stages"]["commit"], "not_started")
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_new_low_after_native_preflight_blocks_locked_completion_and_manual_path_still_allows_it(self):
        from task_governance_tool import tasks
        task, service = self.ready()
        original = tasks.lock_and_reread_edit_owner
        def race(*args, **kwargs):
            self.add_low(task)
            return original(*args, **kwargs)
        with mock.patch.object(tasks, "lock_and_reread_edit_owner", side_effect=race):
            result = service.execute()
        self.assertEqual(result["blocking_code"], "finalization_findings_require_judgment", result)
        self.assertEqual(result["stages"]["commit"], "succeeded")
        self.assertEqual(result["task_status"], "review_pending")
        self.assertEqual(result["reviews"]["counts"]["open_low"], 1)
        manual = self.cli("task", "complete", task, "--verification-complete", "--review-complete",
                          "--completion-evidence-kind", "git_commit", "--completion-revision", result["commit_id"])
        self.assertEqual(manual["task"]["status"], "done")

    def test_reviewer_becomes_unavailable_before_publication_stays_blocked_on_retry(self):
        from tests.test_review_wait_service import TURN
        task, service = self.ready()
        observations = [(self.review_environment(i)["CODEX_THREAD_ID"], TURN, "completed") for i in range(2)]
        before = self.git("rev-parse", "HEAD")
        original = finalization.git.create_candidate
        def race(*args, **kwargs):
            candidate = original(*args, **kwargs)
            observations[1] = (*observations[1][:2], "unknown")
            return candidate
        with mock.patch.object(finalization.git, "create_candidate", side_effect=race):
            result = service.execute(reviewer_observer=lambda: tuple(observations))
        self.assertEqual(result["blocking_code"], "finalization_reviewer_unknown", result)
        _, recovered = self.finalize(task)
        self.assertEqual(recovered["blocking_code"], "finalization_reviewer_unknown", recovered)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def wait_fixture(self, task, service, *, probe_class=None):
        from tests.test_review_wait_direct import DirectHost, ORIGINAL
        from tests.test_review_wait_service import NOW, TURN
        from task_governance_tool.review_wait_runtime.review_wait_basis import parse_basis
        from task_governance_tool.review_wait_runtime.review_wait_controller import ReviewWaitController, Reviewer
        from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
        from task_governance_tool.review_wait_runtime.review_wait_runtime import reservation
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe
        basis = service.journal.read().basis
        parent = basis["parent_thread_id"]
        binding = parse_basis(json.dumps({"ok": True, "status": "review_wait_basis", "basis": basis}).encode(),
                              wait_id="fixture-wait", task_id=task, parent_thread_id=parent)
        reviewers = tuple(Reviewer(self.review_environment(i)["CODEX_THREAD_ID"], TURN) for i in range(2))
        host = DirectHost()
        host.parent_thread_id, host.reviewer_ids = parent, tuple(r.reviewer_id for r in reviewers)
        host.value = replace(host.value, parent_thread_id=parent)
        host.parent = ChildTurn(parent, ORIGINAL, "inProgress", "active")
        host.read_child = lambda child: ChildTurn(child, TURN, "completed", "idle")
        path = self.root / "reviews/wait.sqlite"
        ReviewWaitRepository.create(path, ReviewWaitController(binding, reviewers, reservation(host.value)))
        def current():
            if service.core.read(task)["task"]["status"] == "done":
                raise ValueError("owner released")
            return binding
        direct = (probe_class or ManagedDirectProbe)(path, current, lambda metadata: host, lambda: NOW,
                                    dispatch_path=lambda parent: self.root / "reviews/dispatch.sqlite",
                                    cycle_seconds=0.01, join_seconds=5, finalization_factory=lambda controller: service)
        self.addCleanup(direct.close)
        return direct, host, parent

    def worker_guard_race(self, change):
        from tests.test_review_wait_direct import ORIGINAL
        from tests.test_review_wait_service import TURN, OTHER
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe
        class BoundaryProbe(ManagedDirectProbe):
            def _before_send(self, host, *args):
                host.finalizing_reads = 0
                return super()._before_send(host, *args)
        task, service = self.ready()
        before = self.git("rev-parse", "HEAD")
        direct, host, parent = self.wait_fixture(task, service, probe_class=BoundaryProbe)
        def child(child_id):
            if hasattr(host, "finalizing_reads"):
                host.finalizing_reads += 1
                # The initial observer reads both children as completed; change
                # the next read made by the real effect-boundary guard.
                if host.finalizing_reads == 3:
                    if change == "turn":
                        return ChildTurn(child_id, OTHER, "completed", "idle")
                    if change == "unavailable":
                        raise ValueError("private read failure")
                    if change in {"failed", "interrupted"}:
                        return ChildTurn(child_id, TURN, change, "idle")
                    if change == "parent":
                        host.parent = ChildTurn(parent, OTHER, "completed", "idle")
                    if change == "parent_unavailable":
                        host.read_parent = mock.Mock(side_effect=ValueError("private parent failure"))
                    if change == "cancel":
                        direct._stop.set()
            return ChildTurn(child_id, TURN, "completed", "idle")
        host.read_child = child
        reports = []
        host.set_finalization_result = lambda probe, result: reports.append(result)
        host.observe_receipt = lambda probe, old, **kwargs: None
        result = direct.handle("direct_delete_start", {}, {"threadId": parent, "turnId": ORIGINAL})
        self.assertTrue(result["ok"], result)
        host.parent = ChildTurn(parent, ORIGINAL, "completed", "idle")
        deadline = time.monotonic() + 30
        while not reports and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(len(reports), 1, direct.repository.read())
        direct.close()
        report = reports[0]
        if change == "parent":
            self.assertEqual(report["status"], "completed", report)
            self.assertEqual(service.core.read(task)["task"]["status"], "done")
            return
        self.assertEqual(report["stages"]["commit"], "not_started", report)
        self.assertEqual(report["stages"]["completion"], "not_started", report)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.cli("task", "show", task)["task"]["status"], "review_pending")
        if change in {"parent", "parent_unavailable", "cancel"}:
            self.assertIsNone(report["reviewer_blocking_code"], report)
            self.assertEqual(report["stages"]["registration"], "not_started")
            _, checked = self.finalize(task, check=True)
            self.assertIsNone(checked["reviewer_blocking_code"], checked)
            _, recovered = self.finalize(task)
            self.assertEqual(recovered["status"], "completed", recovered)
        else:
            expected = "finalization_reviewer_" + ("failed" if change in {"failed", "interrupted"} else "unknown")
            self.assertEqual(report["reviewer_blocking_code"], expected, report)
            for check in (True, False):
                _, recovered = self.finalize(task, check=check)
                self.assertEqual(recovered["blocking_code"], expected, recovered)
                self.assertEqual(recovered["reviewer_observations"], report["reviewer_observations"])
                self.assertEqual(recovered["stages"]["commit"], "not_started")
                self.assertEqual(recovered["stages"]["completion"], "not_started")
                # Read-only recovery preserves every stage. An explicit retry
                # may register valid originals, but cannot commit or complete.
                self.assertEqual(recovered["stages"]["registration"],
                                 report["stages"]["registration"] if check else "succeeded")
            self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_worker_guard_changed_turn_is_retained_across_cli_retry(self):
        self.worker_guard_race("turn")

    def test_worker_guard_unavailable_reviewer_is_retained_across_cli_retry(self):
        self.worker_guard_race("unavailable")

    def test_worker_guard_failed_reviewer_is_retained_across_cli_retry(self):
        self.worker_guard_race("failed")

    def test_worker_guard_interrupted_reviewer_is_retained_across_cli_retry(self):
        self.worker_guard_race("interrupted")

    def test_worker_guard_new_idle_parent_turn_allows_bound_finalization(self):
        self.worker_guard_race("parent")

    def test_worker_guard_parent_unavailable_does_not_latch_reviewer_failure(self):
        self.worker_guard_race("parent_unavailable")

    def test_worker_guard_cancellation_does_not_latch_reviewer_failure(self):
        self.worker_guard_race("cancel")

    def scheduled_handoff(self, status):
        from tests.test_review_wait_direct import ORIGINAL, NEW_TURN
        from tests.test_review_wait_service import TURN, OTHER
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        task, service = self.ready()
        direct, host, parent = self.wait_fixture(task, service)
        before = self.git("rev-parse", "HEAD")
        observed = []
        def child(child_id):
            observed.append(child_id)
            if child_id == host.reviewer_ids[0]:
                if status == "unavailable":
                    raise ValueError("private child read failure")
                return ChildTurn(child_id, OTHER if status == "changed" else TURN,
                                 "completed" if status == "changed" else status, "idle")
            return ChildTurn(child_id, TURN, "completed", "idle")
        host.read_child = child
        host.read_parent = lambda: ChildTurn(parent, NEW_TURN if observed else ORIGINAL, "inProgress", "active")
        host.set_finalization_result = lambda *args: self.fail("A busy parent must defer dispatch")
        host.observe_receipt = lambda *args, **kwargs: None
        started = direct.handle("direct_delete_start", {}, {"threadId": parent, "turnId": ORIGINAL})
        self.assertTrue(started["ok"], started)
        if status in {"completed", "failed", "interrupted"}:
            deadline = time.monotonic() + 20
            while len(service.journal.read().reviewer_observations) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(service.journal.read().reviewer_observations), 2)
            self.assertTrue(direct._worker.is_alive())
            self.assertEqual(direct.repository.read().status, "waiting")
            self.assertEqual(direct.repository.read().timer_phase, "active")
            direct.close()  # Explicit EOF, not a later parent turn, stops it.
        else:
            direct._worker.join(20)
        self.assertFalse(direct._worker.is_alive())
        self.assertTrue(observed)
        self.assertEqual(host.sends, [])
        self.assertEqual(direct.repository.read().timer_phase, "paused")
        expected = (None if status == "completed" else "finalization_reviewer_" +
                    ("failed" if status in {"failed", "interrupted"} else "unknown"))
        record = service.journal.read()
        self.assertEqual(record.reviewer_blocker, expected)
        self.assertTrue(record.reviewer_observations)
        for check in (True, False):
            _, result = self.finalize(task, check=check)
            if expected:
                self.assertEqual(result["blocking_code"], expected, result)
                self.assertEqual(result["stages"]["commit"], "not_started")
                self.assertEqual(result["stages"]["completion"], "not_started")
                self.assertEqual(self.git("rev-parse", "HEAD"), before)
            elif not check:
                self.assertEqual(result["status"], "completed", result)

    def test_scheduled_handoff_preserves_failed_reviewer_before_dispatch(self):
        self.scheduled_handoff("failed")

    def test_scheduled_handoff_preserves_interrupted_reviewer_before_dispatch(self):
        self.scheduled_handoff("interrupted")

    def test_wait_read_failure_is_retained_before_dispatch(self):
        self.scheduled_handoff("unavailable")

    def test_wait_changed_turn_is_retained_before_dispatch(self):
        self.scheduled_handoff("changed")

    def test_healthy_scheduled_handoff_does_not_create_reviewer_failure(self):
        self.scheduled_handoff("completed")

    def preparation_failure_recovery(self, status):
        from tests.test_review_wait_direct import DirectHost, ORIGINAL, NEW_TURN
        from tests.test_review_wait_service import NOW, TURN
        from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
        from task_governance_tool.review_wait_runtime.review_wait_basis import parse_basis
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        task, service = self.ready()
        before = self.git("rev-parse", "HEAD")
        basis = service.journal.read().basis
        parent = basis["parent_thread_id"]
        reviewers = tuple(self.review_environment(i)["CODEX_THREAD_ID"] for i in range(2))
        effects, reads = [], []
        class Host(DirectHost):
            def read_child(self, child):
                reads.append(child)
                if len(reads) <= len(reviewers):
                    return ChildTurn(child, TURN, "inProgress", "active")
                self.parent = ChildTurn(parent, NEW_TURN, "inProgress", "active")
                if child == reviewers[0]:
                    if status == "unavailable":
                        raise ValueError("private preparation failure")
                    return ChildTurn(child, TURN, status, "idle")
                return ChildTurn(child, TURN, "completed", "idle")

            def create_heartbeat(self, rule):
                effects.append("create")
                self.automation_id = "preparation-race"
                self.value = replace(self.value, id=self.automation_id, rule=rule,
                                     timezone_source="host_node_intl")
                return self.automation_id

            def verify_created(self, rule):
                return self.value

            def delete_heartbeat(self, before, *, cleanup=False):
                effects.append("delete")
                return super().delete_heartbeat(before)
        host = Host()
        host.parent_thread_id, host.reviewer_ids = parent, reviewers
        host.value = replace(host.value, parent_thread_id=parent)
        host.parent = ChildTurn(parent, ORIGINAL, "inProgress", "active")
        def reader(repo, task_id, parent_id, wait, **kwargs):
            return lambda: parse_basis(json.dumps({"ok": True, "status": "review_wait_basis", "basis": basis}).encode(),
                wait_id=wait, task_id=task_id, parent_thread_id=parent_id)
        session = ProjectReviewWaitSession(ProjectConfig(self.root, self.root / "server.mjs", self.root, "UTC"),
            managed_host_factory=lambda **kwargs: host, basis_factory=reader, clock=lambda: NOW)
        self.addCleanup(session.close)
        session._enabled = lambda: True
        session._location = lambda **kwargs: service.resolution.paths
        session._finalizer = lambda binding: service
        result = session.handle("wait", {"task_id": task, "reviewer_ids": list(reviewers)},
                                {"threadId": parent, "turnId": ORIGINAL})
        self.assertFalse(result["ok"], result)
        self.assertEqual(effects, ["create", "delete"])
        self.assertTrue(all(item.direct is None or item.direct._worker is None for item in session.sessions.values()))
        blocker = "finalization_reviewer_" + ("unknown" if status == "unavailable" else "failed")
        self.assertEqual(service.journal.read().reviewer_blocker, blocker)
        for check in (True, False):
            _, report = self.finalize(task, check=check)
            self.assertEqual(report["blocking_code"], blocker, report)
            self.assertEqual(report["stages"]["commit"], "not_started")
            self.assertEqual(report["stages"]["completion"], "not_started")
        self.assertEqual(report["stages"]["registration"], "succeeded")
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.cli("task", "show", task)["task"]["status"], "review_pending")

    def test_preparation_failed_reviewer_blocks_retained_cli_finalize(self):
        self.preparation_failure_recovery("failed")

    def test_preparation_unavailable_reviewer_blocks_retained_cli_finalize(self):
        self.preparation_failure_recovery("unavailable")

    def test_wait_worker_finishes_before_same_parent_notification_and_receipt(self):
        from tests.test_review_wait_direct import ORIGINAL, NEW_TURN
        from tests.test_review_wait_host import read_result, AUTOMATION, OTHER
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        from task_governance_tool.review_wait_runtime.managed_host import ManagedHost
        task, service = self.ready()
        direct, host, parent = self.wait_fixture(task, service)
        deferred = threading.Event()
        parent_ready = direct._parent_ready
        def observe_parent(*args, **kwargs):
            ready = parent_ready(*args, **kwargs)
            if not ready and direct._finalization_prepared:
                deferred.set()
            return ready
        direct._parent_ready = observe_parent
        wire = ManagedHost(metadata={"threadId": parent, "turnId": ORIGINAL}, task_id=task,
            automation_id=AUTOMATION, codex_home=self.root, confirmed_timezone="UTC", timezone_source="host_os",
            reviewer_ids=list(host.reviewer_ids), command=["offline-peer"])
        reports = []
        def report(probe, result):
            self.assertTrue(host.deleted)
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(service.core.read(task)["task"]["status"], "done")
            reports.append(result)
            wire.set_finalization_result(probe, result)
            # B becomes busy after A completes but before A's single send.
            if len(reports) == 1:
                host.parent = ChildTurn(parent, OTHER, "inProgress", "active")
            payload = json.loads(wire._direct_prompt(probe).splitlines()[2])
            self.assertEqual({key: item for key, item in payload.items() if key != "delivery"}, result)
        host.set_finalization_result = report
        host.on_send = lambda probe: setattr(host, "parent", ChildTurn(parent, NEW_TURN, "inProgress", "active"))
        def observe(probe, old, **kwargs):
            if probe not in host.sends:
                return None
            value = read_result()
            value["thread"].update(id=parent, status={"type": "active"})
            envelope = ("<codex_delegation>\n  <source_thread_id>" + parent + "</source_thread_id>\n  <input>"
                        + wire._direct_prompt(probe) + "</input>\n</codex_delegation>")
            raw = envelope.encode("utf-16-le")
            output = {"text": raw[:40000].decode("utf-16-le", errors="surrogatepass"), "truncated": len(raw) > 40000}
            if output["truncated"]:
                output["originalChars"] = len(raw) // 2
            value["turns"][0].update(id=NEW_TURN, status="inProgress", items=[{
                "type": "functionCallOutput", "namespace": "codex_app", "name": "send_message_to_thread", "output": output}])
            value["page"].update(hasMore=True, nextCursor="previous")
            fence = read_result()
            fence["thread"]["id"] = parent
            fence["turns"][0].update(id=old, items=[])
            newer = read_result()
            newer["thread"]["id"] = parent
            newer["turns"][0].update(id=str(uuid4()), items=[])
            newer["page"].update(hasMore=True, nextCursor="notification")
            with mock.patch.object(wire, "_json_call", side_effect=[newer, value, fence]):
                return wire.observe_receipt(probe, old, **kwargs)
        host.observe_receipt = observe
        with mock.patch.object(service, "record_notification_receipt") as usage:
            result = direct.handle("direct_delete_start", {}, {"threadId": parent, "turnId": ORIGINAL})
            self.assertTrue(result["ok"], result)
            host.parent = ChildTurn(parent, ORIGINAL, "completed", "idle")
            deadline = time.monotonic() + 30
            # Wait for the worker to observe busy, not merely for the report
            # callback to append. Otherwise this test can restore idle before
            # the worker sees busy and legitimately take the non-deferred path.
            self.assertTrue(deferred.wait(max(0, deadline - time.monotonic())))
            self.assertEqual(len(reports), 1, direct.repository.read())
            self.assertEqual(host.sends, [])
            host.parent = ChildTurn(parent, OTHER, "completed", "idle")
            while not usage.called and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(reports), 2, direct.repository.read())
            self.assertEqual(len(host.sends), 1)
            usage.assert_called_once_with(NEW_TURN)
            self.assertEqual(direct.repository.read().acknowledged_turn, NEW_TURN)

    def test_busy_deferred_completed_report_is_rechecked_after_task_reopen(self):
        from tests.test_review_wait_direct import ORIGINAL, NEW_TURN
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        task, service = self.ready()
        direct, host, parent = self.wait_fixture(task, service)
        reports = []
        def report(probe, result):
            reports.append(result)
            if len(reports) == 1:
                host.parent = ChildTurn(parent, NEW_TURN, "inProgress", "active")
        host.set_finalization_result = report
        host.observe_receipt = lambda *args, **kwargs: None
        with mock.patch.object(service, "execute", wraps=service.execute) as execute:
            self.assertTrue(direct.handle("direct_delete_start", {}, {"threadId": parent, "turnId": ORIGINAL})["ok"])
            host.parent = ChildTurn(parent, ORIGINAL, "completed", "idle")
            deadline = time.monotonic() + 30
            while not reports and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(reports[0]["status"], "completed")
            completed = reports[0]["commit_id"]
            self.assertEqual(host.sends, [])
            self.cli("task", "edit", task, "--status", "in_progress", "--reopen-reason", "New authorized work")
            host.parent = ChildTurn(parent, NEW_TURN, "completed", "idle")
            while direct.repository.read().status != "accepted" and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(direct.repository.read().status, "accepted")
            self.assertEqual(len(host.sends), 1)
            self.assertEqual(len(reports), 2)
            self.assertEqual(reports[-1]["status"], "attention_required")
            self.assertEqual(reports[-1]["blocking_code"], "review_target_mismatch")
            self.assertIsNone(reports[-1]["task_status"])
            self.assertEqual(reports[-1]["stages"], reports[0]["stages"])
            self.assertEqual(reports[-1]["commit_id"], completed)
            self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), completed)
            self.assertEqual(service.core.read(task)["task"]["status"], "in_progress")
            self.assertEqual([call.kwargs.get("check", False) for call in execute.call_args_list], [False, True])

    def test_check_does_not_register_or_commit(self):
        task, context = self.prepared()
        for index in range(2):
            self.save(context, index)
        before = self.git("rev-parse", "HEAD")
        result, report = self.finalize(task, check=True)
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(report["stages"], dict(registration="not_started", commit="not_started", completion="not_started"))
        self.assertEqual(self.cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
