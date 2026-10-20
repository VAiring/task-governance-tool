"""Late initial originals and continued answers through installed public paths."""
from __future__ import annotations

import json

from tests import test_review_answer_tracking_installed as tracking
from tests.test_review_answer_tracking_installed import arrival, reviewer
from tests import test_review_handoff_preparation as fixtures
from tests import test_review_answer_installed_finalization as finalization


class LateAnswerFixture(fixtures.PreparationFixture):
    reviewer_count = 2
    git = tracking.InstalledAnswerTrackingTests.git
    committed_fixture = tracking.InstalledAnswerTrackingTests.committed_fixture
    prepared = tracking.InstalledAnswerTrackingTests.prepared
    finalize = tracking.InstalledAnswerTrackingTests.finalize
    collect_public = tracking.InstalledAnswerTrackingTests.collect_public
    assert_collected = tracking.InstalledAnswerTrackingTests.assert_collected
    bind_pending = tracking.InstalledAnswerTrackingTests.bind_pending
    current_receipts = tracking.InstalledAnswerTrackingTests.current_receipts
    instrumented = finalization.InstalledAnswerFinalizationTests.instrumented

    def invoke(self, *args, **kwargs):
        if args[0] == "prepare-finalization":
            args = (args[0], "--reviewers", str(self.reviewer_count), *args[1:])
        return super().invoke(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.cli("setup", "--review-answer-collection", "on", "--review-wait", "off")
        self.task_id, self.handoff = self.prepared(contract=True)
        self.initial = self.handoff
        self.initial_paths = tuple(row["result_path"] for row in self.initial["review_requests"])

    def supplement(self, directory="reviews/continued"):
        selection = dict(version=1, reviewers=[dict(
            session_id=reviewer(1), action="supplement", prior_receipt_id=None,
            reason="Complete the unchanged assigned review after interruption")])
        continued = self.invoke(
            "continue", "--repo", str(self.root), "--task-id", self.task_id,
            "--target-generation", "1", "--directory", directory,
            raw=json.dumps(selection).encode("utf-8"))
        self.assertEqual(continued.returncode, 0, continued.stdout or continued.stderr)
        self.handoff = json.loads(continued.stdout)["handoff"]
        armed = self.collect_public([arrival(1, text="Review interrupted")], action="arm")
        self.assertTrue(armed["response"]["ok"], armed)
        return self.handoff

    def ready_late(self, *, before=False, verdict="pass", text=None):
        self.bind_pending()
        self.supplement()
        if before:
            self.assert_collected(self.collect_public([arrival(2, verdict, text=text)]), [1])
        self.assert_collected(self.collect_public([arrival(1, revision=2)]), [1])
        if not before:
            self.assert_collected(self.collect_public([arrival(2, verdict, text=text)]), [1])
        original_paths = (
            self.handoff["review_requests"][0]["result_path"], self.initial_paths[1])
        return {path: (self.root / path).read_bytes() for path in original_paths}

    def assert_preserved(self, originals):
        self.assertEqual({path: (self.root / path).read_bytes() for path in originals}, originals)


class LateAnswerInstalledTests(LateAnswerFixture):
    def test_continued_answer_and_late_initial_answer_complete_without_recontinue(self):
        originals = self.ready_late()
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(len(self.current_receipts()), 2)
        self.assertEqual(len(report["originals"]), 2)
        self.assert_preserved(originals)

    def test_initial_arrives_first_and_unknown_registration_response_recovers_same_batch(self):
        originals = self.ready_late(before=True)
        lost = self.instrumented("lose-registration-output")
        self.assertEqual(lost["registrations"], 1, lost)
        self.assertEqual(lost["report"]["stages"]["commit"], "not_started", lost)
        receipts = {row["review_receipt_id"] for row in self.current_receipts()}
        recovered = self.instrumented("recover")
        self.assertEqual(recovered["registrations"], 0, recovered)
        self.assertEqual(recovered["report"]["status"], "completed", recovered)
        self.assertEqual({row["review_receipt_id"] for row in self.current_receipts()}, receipts)
        self.assert_preserved(originals)

    def test_initial_unable_registers_complete_batch_and_blocks_completion(self):
        originals = self.ready_late(verdict="unable_to_review")
        completed, report = self.finalize(self.task_id)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(report["stages"]["registration"], "succeeded", report)
        self.assertEqual(report["blocking_code"], "review_unable", report)
        self.assertEqual(len(self.current_receipts()), 2)
        self.assert_preserved(originals)

    def test_initial_ng_keeps_finding_and_blocks_completion(self):
        payload = json.loads(tracking.answer(2, "changes_requested").split("```json\r\n")[1].split("\r\n```")[0])
        payload["findings"] = [dict(severity="medium", summary="Review the retained source condition")]
        originals = self.ready_late(text=json.dumps(payload))
        completed, report = self.finalize(self.task_id)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(report["stages"]["registration"], "succeeded", report)
        self.assertEqual(report["reviews"]["counts"]["open_medium"], 1, report)
        self.assertEqual(len(self.current_receipts()), 2)
        self.assert_preserved(originals)


class FourReviewerLateAnswerTests(LateAnswerFixture):
    reviewer_count = 4

    def test_saved_capture_keeps_slot_with_late_initials_on_both_session_sides(self):
        bound = self.collect_public([arrival(index, status="inProgress") for index in range(1, 5)])
        self.assertTrue(bound["response"].get("bound"), bound)
        self.assert_collected(self.collect_public([arrival(3)]), [1])
        capture_bytes = (self.root / self.initial_paths[2]).read_bytes()
        self.supplement()
        self.supplement(directory="reviews/continued-again")
        self.assert_collected(self.collect_public([arrival(1, revision=2)]), [1])
        self.assert_collected(self.collect_public([arrival(2), arrival(4)]), [1, 2])
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(len(self.current_receipts()), 4)
        self.assertEqual([row["review"]["reviewer"] for row in report["originals"]],
                         [reviewer(1), reviewer(3), reviewer(2), reviewer(4)])
        self.assertEqual((self.root / self.initial_paths[2]).read_bytes(), capture_bytes)


class ManualLateAnswerTests(LateAnswerFixture):
    def prepared(self, *, contract=False):
        self.committed_fixture()
        task_id = self.task("Focused manual late-answer verification")
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "source.py")
        targeted = self.invoke("prepare", "--repo", str(self.root), "--directory", "reviews/g1",
                               "target", task_id, "--kind", "git_snapshot")
        self.assertEqual(targeted.returncode, 0, targeted.stdout or targeted.stderr)
        prepared = self.invoke("prepare", "--repo", str(self.root), "--directory", "reviews/g1",
            "receipt", task_id, "--result", "pass", "--duration-ms", "1",
            "--scope-coverage", "full", "--expected-target-generation", "1")
        self.assertEqual(prepared.returncode, 0, prepared.stdout or prepared.stderr)
        handoff = json.loads(prepared.stdout)["handoff"]
        self.assertEqual(handoff["preparation"]["route"], "manual")
        return task_id, handoff

    def test_manual_submit_registers_both_originals_then_completes_exact_commit(self):
        originals = self.ready_late()
        submitted = self.invoke("submit", "--repo", str(self.root),
            "--packet", self.handoff["packet_path"], "--",
            *(row["result_path"] for row in self.handoff["review_requests"]))
        self.assertEqual(submitted.returncode, 0, submitted.stdout or submitted.stderr)
        self.assertEqual(len(self.current_receipts()), 2)
        self.assert_preserved(originals)
        self.git("commit", "-m", "Complete late-answer fixture")
        revision = self.git("rev-parse", "HEAD").decode().strip()
        self.cli("task", "complete", self.task_id, "--verification-complete", "--review-complete",
                 "--completion-evidence-kind", "git_commit", "--completion-revision", revision)
        self.assertEqual(self.cli("task", "show", self.task_id)["task"]["status"], "done")
