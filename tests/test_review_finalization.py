"""Installed helper through real originals, existing gates, Git and Task done."""

import json
import shlex
import subprocess
import threading
import time
import unittest
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
from dataclasses import replace
from datetime import timedelta
from unittest import mock

from tests import test_review_handoff_preparation as fixtures
from tests.test_review_results import encode, receipt
from task_governance_tool import review_finalization as finalization, review_wait_basis
from task_governance_tool.session_identity import capture_caller_identity


def _wait_for_stage(condition, stage, *, timeout=30):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {stage}")
        time.sleep(0.02)


class FinalizationStageWaitTests(unittest.TestCase):
    def test_later_stage_gets_its_own_budget_after_slow_completion(self):
        now = [0]
        def advance(_seconds):
            now[0] += 1
        with mock.patch.object(time, "monotonic", side_effect=lambda: now[0]), \
             mock.patch.object(time, "sleep", side_effect=advance):
            _wait_for_stage(lambda: now[0] >= 29, "completion report")
            self.assertEqual(now[0], 29)
            _wait_for_stage(lambda: now[0] >= 58, "notification acceptance after reopen")
        self.assertEqual(now[0], 58)

    def test_each_unreached_stage_has_a_finite_named_failure(self):
        for stage in ("completion report", "notification acceptance after reopen"):
            with self.subTest(stage=stage):
                now = [0]
                def advance(_seconds):
                    now[0] += 1
                with mock.patch.object(time, "monotonic", side_effect=lambda: now[0]), \
                     mock.patch.object(time, "sleep", side_effect=advance), \
                     self.assertRaisesRegex(AssertionError, "Timed out waiting for " + stage):
                    _wait_for_stage(lambda: False, stage)
                self.assertEqual(now[0], 30)


class FinalizationReportProjectionTests(unittest.TestCase):
    def fixture(self):
        originals = [{"path": "reviews/" + name, "status": "available", "review": receipt(name)}
                     for name in ("one", "two")]
        for original in originals:
            original["review"]["summary"] += "確認結果を保持" * 100
        receipts = [{"review_receipt_id": "receipt-" + name, "reviewer_key": name,
                     "receipt_kind": "independent", "verdict": "pass",
                     "summary": original["review"]["summary"], "user_approved": 0, "created_at": "now"}
                    for name, original in zip(("one", "two"), originals)]
        return ({"originals": originals, "reviews": {"current_receipts": receipts[::-1]}},
                SimpleNamespace(result_paths=("reviews/one", "reviews/two")))

    def test_exact_bound_summaries_use_unique_same_response_pointers_without_mutation(self):
        result, record = self.fixture()
        result["originals"][0]["review"]["findings"] = [{"severity": "low", "summary": "Preserve this fact"}]
        before = deepcopy(result)
        originals = finalization.project_original_summaries(result, record, ("receipt-one", "receipt-two"))
        for index, original in enumerate(originals):
            self.assertNotIn("summary", original["review"])
            pointer = original["review"]["summary_ref"]
            receipt_index = int(pointer.split("/")[3])
            matched = result["reviews"]["current_receipts"][receipt_index]
            self.assertEqual(matched["review_receipt_id"], original["registered_receipt_id"])
            self.assertEqual(matched["summary"], before["originals"][index]["review"]["summary"])
            self.assertEqual(pointer, f"/reviews/current_receipts/{receipt_index}/summary")
            self.assertEqual(original["review"]["provenance"], before["originals"][index]["review"]["provenance"])
            self.assertEqual(original["review"]["findings"], before["originals"][index]["review"]["findings"])
        projected = {**result, "originals": originals}
        self.assertLess(len(json.dumps(projected)), len(json.dumps(before)))
        self.assertEqual(result, before)

    def test_unconfirmed_unavailable_ambiguous_and_unequal_results_keep_body(self):
        mutations = (
            lambda result: result.update(reviews=None),
            lambda result: result["originals"][0].update(status="unavailable"),
            lambda result: result["originals"][0].update(path="reviews/elsewhere"),
            lambda result: result["reviews"]["current_receipts"].append(deepcopy(result["reviews"]["current_receipts"][1])),
            *(lambda result, field=field: result["originals"][0]["review"].update({field: "different"})
              for field in ("reviewer", "kind", "verdict", "summary")),
        )
        for mutate in mutations:
            result, record = self.fixture()
            mutate(result)
            original = deepcopy(result["originals"][0])
            with self.subTest(mutate=mutate):
                projected = finalization.project_original_summaries(result, record, ("receipt-one", "receipt-two"))
                self.assertEqual(projected[0], original)
        for actual in ((), ("receipt-one",), ("receipt-one", "receipt-one")):
            result, record = self.fixture()
            self.assertEqual(finalization.project_original_summaries(result, record, actual), result["originals"])

    def test_precompletion_check_is_distinct_from_current_state_and_action(self):
        record = SimpleNamespace(basis={key: 1 for key in ("task_id", "contract_revision", "target_kind",
                "target_value", "target_base_revision", "target_generation")}, completion="succeeded",
                registration="succeeded", commit="succeeded", candidate="candidate", receipt_ids=(),
                reviewer_observations=(), reviewer_blocker=None)
        state = {"task": {"title": "Task", "status": "done"}, "events": [], "latest_checkpoint": None,
                 "verification_evidence": {"fact": "verification"}, "review_evidence": {"fact": "review"}}
        gate = {"status": "in_progress", "ready": True, "blocking_codes": [],
                "suggested_action": "run task complete with the same evidence and confirmations"}
        before = deepcopy(gate)
        complete = finalization.report(record, state, [], None, gate)
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(complete["task_status"], "done")
        self.assertEqual(complete["completion_gate"], {"observation": "before_completion",
                         "status": "in_progress", "ready": True, "blocking_codes": []})
        self.assertNotIn("run task complete", json.dumps(complete))
        self.assertEqual(complete["recorded_work"]["status"], "not_recorded")
        self.assertEqual(gate, before)
        record.completion = "dispatching"
        state["task"]["status"] = "in_progress"
        partial = finalization.report(record, state, [], "completion_ack_unknown", gate)
        self.assertEqual(partial["status"], "attention_required")
        self.assertFalse(partial["ok"])
        self.assertEqual(partial["stages"]["commit"], "succeeded")
        self.assertEqual(partial["stages"]["completion"], "dispatching")
        self.assertEqual(partial["blocking_code"], "completion_ack_unknown")
        self.assertEqual(partial["completion_gate"]["observation"], "before_completion")
        legacy = {"event_type": "note_added", "summary": "Retained legacy note", "note_body": None}
        state["events"] = [legacy, {"event_type": "task_updated", "note_body": None}]
        self.assertEqual(finalization.report(record, state, [], None, None)["recorded_work"]["recent_notes"], [legacy])


class InstalledFinalizationTests(fixtures.PreparationFixture):
    git = fixtures.ReviewerMaterialTests.git
    committed_fixture = fixtures.ReviewerMaterialTests.committed_fixture

    def prepared(self):
        self.committed_fixture()
        task = self.task("Focused isolated verification")
        return self.prepare_task(task)

    def prepare_task(self, task):
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
                                       "finalization", "finalization_command", "preparation"})
        self.assertEqual(context["preparation"]["route"], "integrated")
        self.assertNotIn("--commit-approved", context["finalization_command"])
        return task, context

    def test_saved_note_bodies_survive_show_completion_and_check(self):
        self.committed_fixture()
        task = self.cli("task", "add", "--title", "Note reporting", "--review-tier", "2",
                        "--status", "in_progress", "--verification", "Focused isolated verification")["task"]["task_id"]
        notes = []
        for number, edits in enumerate(((), ("--priority", "high"),
                ("--review-tier", "1", "--review-tier-change-reason", "Before target capture"))):
            body = f"作業メモ{number}\n" + "確認した境界" * 220
            event = self.cli("task", "edit", task, *edits, "--add-note", body)["event"]
            notes.append(event)
        self.assertEqual([row["event_type"] for row in notes],
                         ["note_added", "task_updated", "review_tier_changed"])
        no_body = self.cli("task", "edit", task, "--review-tier", "2",
                          "--review-tier-change-reason", "Restore required independent reviews")["event"]
        shown = self.cli("task", "show", task)
        expected = {row["task_event_id"]: row for row in shown["events"] if row.get("note_body") is not None}
        self.assertEqual(set(expected), {row["task_event_id"] for row in notes})
        task, context = self.prepare_task(task)
        for index in range(2):
            self.save(context, index)
        service = self.service(task)
        for check in (False, True):
            report = service.execute(check=check)
            self.assertEqual(report["status"], "completed", report)
            work = report["recorded_work"]
            self.assertEqual(work["status"], "recorded")
            self.assertEqual({row["task_event_id"]: row for row in work["recent_notes"]}, expected)
            self.assertNotIn(no_body["task_event_id"], expected)
            self.assertEqual(work["coverage"], "latest_checkpoint_and_recent_task_events")

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
        from task_governance_tool import review_handoff_scope
        for module in (review_handoff_scope, review_wait_basis):
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
        self.assertEqual(report["completion_gate"]["observation"], "before_completion")
        self.assertNotIn("suggested_action", report["completion_gate"])
        for original in report["originals"]:
            matched = report["reviews"]["current_receipts"][int(original["review"]["summary_ref"].split("/")[3])]
            self.assertEqual(original["registered_receipt_id"], matched["review_receipt_id"])
            self.assertEqual(matched["summary"], receipt()["summary"])
            self.assertEqual(original["review"]["provenance"], receipt()["provenance"])
            self.assertNotIn("summary", original["review"])
        self.assertEqual(self.git("show", "HEAD:source.py"), b"value = 2\n")
        commit = self.git("rev-parse", "HEAD")
        self.assertEqual(self.cli("task", "show", task)["task"]["status"], "done")
        second, repeated = self.finalize(task)
        self.assertEqual(second.returncode, 0, repeated)
        self.assertEqual(repeated["commit_id"], report["commit_id"])
        self.assertEqual(self.git("rev-parse", "HEAD"), commit)
        self.assertEqual(self.cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 2)

    def test_delegated_completion_retains_nonreview_admission_through_writer(self):
        from task_governance_tool import storage
        task, service = self.ready()
        observed = []
        original = storage.validate_current_database
        def capture(connection, target, **options):
            if options.get("review_task_id") == task:
                observed.append((connection.execute("PRAGMA query_only").fetchone()[0],
                    connection.in_transaction, options.get("operation_local", False)))
            return original(connection, target, **options)
        with mock.patch.object(storage, "validate_current_database", side_effect=capture):
            report = service.execute()
        self.assertEqual(report["status"], "completed", report)
        self.assertTrue(observed)
        self.assertTrue(all(not local for _readonly, _transaction, local in observed), observed)
        self.assertIn((1, True, False), observed)
        self.assertIn((0, True, False), observed)

    def test_missing_original_keeps_complete_batch_atomic(self):
        task, context = self.prepared()
        self.save(context, 0)
        before = self.git("rev-parse", "HEAD")
        result, report = self.finalize(task)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["blocking_code"], "finalization_originals_unavailable", report)
        self.assertEqual([r["status"] for r in report["originals"]], ["available", "unavailable"])
        self.assertEqual(report["stages"]["registration"], "not_started")
        self.assertEqual(report["originals"][0]["review"]["summary"], receipt()["summary"])
        self.assertNotIn("summary_ref", report["originals"][0]["review"])
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
        self.assertEqual(recovered["originals"][0]["review"]["findings"],
                         [{"severity": "low", "summary": "source.py:1 Confirm the documented choice"}])
        self.assertIn("summary_ref", recovered["originals"][0]["review"])

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
        self.assertTrue(all("summary_ref" in item["review"] for item in first["originals"]))
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
        result = service.execute(guard=lambda observe: [observe(item) for item in observations])
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
            result = service.execute(guard=lambda observe: [observe(item) for item in observations])
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
        from tests.test_review_wait_service import NOW, TURN, OTHER
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe
        class BoundaryProbe(ManagedDirectProbe):
            def _before_send(self, host, *args):
                host.finalizing = True
                return super()._before_send(host, *args)

            def _idle_and_ended(self, host, *args, **kwargs):
                host.in_finalization_guard = getattr(host, "finalizing", False)
                try:
                    return super()._idle_and_ended(host, *args, **kwargs)
                finally:
                    host.in_finalization_guard = False
        task, service = self.ready()
        before = self.git("rev-parse", "HEAD")
        direct, host, parent = self.wait_fixture(task, service, probe_class=BoundaryProbe)
        def child(child_id):
            if getattr(host, "in_finalization_guard", False):
                if change == "partial" and getattr(host, "injected", False):
                    retained = service.journal.read()
                    self.assertEqual(retained.reviewer_blocker, "finalization_reviewer_failed")
                    self.assertIn((host.reviewer_ids[0], TURN, "failed"), retained.reviewer_observations)
                    raise ValueError("private second child read failure")
                # Inject at the first actual finalization guard, independent of
                # any preflight or removed duplicate-observer call count.
                if not getattr(host, "injected", False):
                    host.injected = True
                    if change == "turn":
                        return ChildTurn(child_id, OTHER, "completed", "idle")
                    if change == "unavailable":
                        raise ValueError("private read failure")
                    if change in {"failed", "interrupted"}:
                        return ChildTurn(child_id, TURN, change, "idle")
                    if change == "partial":
                        return ChildTurn(child_id, TURN, "failed", "idle")
                    if change == "active":
                        return ChildTurn(child_id, TURN, "completed", "active")
                    if change == "expired":
                        direct.clock = lambda: NOW + timedelta(hours=1)
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
        if change in {"parent", "parent_unavailable", "cancel", "expired"}:
            self.assertIsNone(report["reviewer_blocking_code"], report)
            self.assertEqual(report["stages"]["registration"], "not_started")
            _, checked = self.finalize(task, check=True)
            self.assertIsNone(checked["reviewer_blocking_code"], checked)
            _, recovered = self.finalize(task)
            self.assertEqual(recovered["status"], "completed", recovered)
        else:
            expected = "finalization_reviewer_" + ("failed" if change in {"failed", "interrupted", "partial"} else "unknown")
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

    def test_worker_guard_expiry_does_not_latch_reviewer_failure(self):
        self.worker_guard_race("expired")

    def test_worker_guard_active_completed_child_does_not_qualify_as_ended(self):
        self.worker_guard_race("active")

    def test_worker_guard_retains_first_failure_before_second_child_read_fails(self):
        self.worker_guard_race("partial")

    def test_worker_reads_each_child_once_until_bound_judgments_qualify(self):
        from tests.test_review_wait_direct import ORIGINAL, NEW_TURN
        from tests.test_review_wait_service import TURN
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe
        boundaries = []
        class BoundaryProbe(ManagedDirectProbe):
            def _before_send(self, host, *args):
                host.finalizing = True
                return super()._before_send(host, *args)

            def _idle_and_ended(self, host, *args, **kwargs):
                if not getattr(host, "finalizing", False):
                    return super()._idle_and_ended(host, *args, **kwargs)
                record = service.journal.read()
                boundary = {"stages": (record.registration, record.commit, record.completion), "reads": []}
                boundaries.append(boundary)
                host.boundary = boundary
                if len(boundaries) == 1:
                    host.parent = ChildTurn(parent, ORIGINAL, "inProgress", "active")
                try:
                    return super()._idle_and_ended(host, *args, **kwargs)
                finally:
                    host.boundary = None
                    if len(boundaries) == 1:
                        host.parent = ChildTurn(parent, ORIGINAL, "completed", "idle")
        task, service = self.ready()
        direct, host, parent = self.wait_fixture(task, service, probe_class=BoundaryProbe)
        def child(child_id):
            if getattr(host, "finalizing", False):
                self.assertIsNotNone(host.boundary, "Child read outside the effect guard")
                host.boundary["reads"].append(child_id)
            return ChildTurn(child_id, TURN, "completed", "idle")
        host.read_child = child
        reports = []
        host.set_finalization_result = lambda probe, result: reports.append(result)
        host.observe_receipt = lambda *args, **kwargs: NEW_TURN
        self.assertTrue(direct.handle("direct_delete_start", {}, {"threadId": parent, "turnId": ORIGINAL})["ok"])
        host.parent = ChildTurn(parent, ORIGINAL, "completed", "idle")
        # set_finalization_result runs before the final notification guards.
        # Closing on that report races receipt observation with cancellation.
        direct._worker.join(30)
        self.assertFalse(direct._worker.is_alive(), direct.repository.read())
        self.assertEqual(direct.repository.read().acknowledged_turn, NEW_TURN)
        direct.close()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["status"], "completed", reports)
        registered = reports[0]["registered_receipt_ids"]
        self.assertEqual(len(registered), 2)
        self.assertEqual(len(set(registered)), 2)
        evidence = reports[0]["reviews"]
        self.assertTrue(evidence["gate"]["satisfied"])
        self.assertEqual(evidence["gate"]["qualifying_independent_passes"], 2)
        self.assertEqual(len(evidence["current_receipts"]), 2)
        self.assertEqual({row["review_receipt_id"]: row["verdict"] for row in evidence["current_receipts"]},
                         {receipt_id: "pass" for receipt_id in registered})
        self.assertTrue(boundaries)
        self.assertEqual(boundaries[0]["stages"], boundaries[1]["stages"])
        for boundary in boundaries:
            # This fixture changes no judgment/basis after registering its two
            # exact PASS results. Later effects reuse them without child reads;
            # the parent/deadline guards and all stage boundaries still run.
            expected_reads = [] if boundary["stages"][0] == "succeeded" else list(host.reviewer_ids)
            self.assertEqual(boundary["reads"], expected_reads, boundary)
        stages = {boundary["stages"] for boundary in boundaries}
        for stage in (("dispatching", "not_started", "not_started"),
                      ("succeeded", "dispatching", "not_started"),
                      ("succeeded", "succeeded", "dispatching")):
            self.assertIn(stage, stages)

    def test_capacity_failure_after_confirmed_commit_and_done_preserves_success_without_replay(self):
        from tests.test_review_wait_direct import ORIGINAL
        from task_governance_tool.review_wait_runtime.review_wait_host import (
            ChildTurn, HostAdapterError, annotate_read_failure)
        task, service = self.ready()
        direct, host, parent = self.wait_fixture(task, service)
        reports, reads = [], []
        host.set_finalization_result = lambda probe, result: reports.append(result)
        def unavailable(*args, **kwargs):
            reads.append(1)
            raise annotate_read_failure(HostAdapterError("host_call_failed",
                boundary_reason="host_response_frame_limit", operation="read_thread",
                termination="confirmed"), "notification_receipt", 1)
        host.observe_receipt = unavailable
        self.assertTrue(direct.handle("direct_delete_start", {}, {"threadId":parent, "turnId":ORIGINAL})["ok"])
        host.parent = ChildTurn(parent, ORIGINAL, "completed", "idle")
        direct._worker.join(30)
        self.assertFalse(direct._worker.is_alive())
        self.assertEqual(len(reads), 1)  # No identical oversized-read loop.
        self.assertEqual(len(host.sends), 1)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["task_status"], "done")
        self.assertEqual(reports[0]["stages"], {
            "registration":"succeeded", "commit":"succeeded", "completion":"succeeded"})
        commit = reports[0]["commit_id"]
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), commit)
        self.assertEqual(self.cli("task", "show", task)["task"]["status"], "done")
        self.assertEqual(direct.repository.read().status, "accepted")
        self.assertIsNone(direct.repository.read().acknowledged_turn)
        self.assertEqual(direct.read_diagnostic["read_failure"]["purpose"], "notification_receipt")
        self.assertEqual(direct.read_diagnostic["host_failure"]["termination"], "confirmed")
        self.assertEqual(service.execute(check=True)["task_status"], "done")
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), commit)
        self.assertEqual(len(host.sends), 1)

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
            _wait_for_stage(lambda: bool(reports), "completion report")
            self.assertEqual(reports[0]["status"], "completed")
            completed = reports[0]["commit_id"]
            self.assertEqual(host.sends, [])
            self.cli("task", "edit", task, "--status", "in_progress", "--reopen-reason", "New authorized work")
            host.parent = ChildTurn(parent, NEW_TURN, "completed", "idle")
            _wait_for_stage(lambda: direct.repository.read().status == "accepted",
                            "notification acceptance after reopen")
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
