"""Prior-continuation originals through real installed registration and recovery."""
import json

from tests.test_review_answer_late_arrival import LateAnswerFixture
from tests.test_review_answer_tracking_installed import arrival, reviewer


class RetainedOriginInstalledTests(LateAnswerFixture):
    def continue_actor(self, actor, directory):
        selection = dict(version=1, reviewers=[dict(
            session_id=reviewer(actor), action="supplement", prior_receipt_id=None,
            reason="Finish the unchanged assigned review")])
        continued = self.invoke("continue", "--repo", str(self.root),
            "--task-id", self.task_id, "--target-generation", "1",
            "--directory", directory, raw=json.dumps(selection).encode())
        self.assertEqual(continued.returncode, 0, continued.stdout or continued.stderr)
        self.handoff = json.loads(continued.stdout)["handoff"]
        armed = self.collect_public([arrival(actor, text="Review interrupted")], action="arm")
        self.assertTrue(armed["response"]["ok"], armed)

    def ready_retained(self):
        self.bind_pending()
        self.continue_actor(2, "reviews/first")
        self.assert_collected(self.collect_public([arrival(2, revision=2)]), [1])
        old_path = self.handoff["review_requests"][0]["result_path"]
        self.continue_actor(1, "reviews/second")
        self.assert_collected(self.collect_public([arrival(1, revision=2)]), [1])
        paths = (old_path, self.handoff["review_requests"][0]["result_path"])
        return {path: (self.root / path).read_bytes() for path in paths}

    def assert_completed_once(self, originals, report):
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(report["stages"], dict(registration="succeeded", commit="succeeded", completion="succeeded"))
        self.assertEqual(len(report["registered_receipt_ids"]), 2)
        self.assertEqual({row["review_receipt_id"] for row in report["reported_receipts"]},
                         set(report["registered_receipt_ids"]))
        self.assertEqual(self.cli("task", "show", self.task_id)["task"]["status"], "done")
        commit = self.git("rev-parse", "HEAD")
        repeated = self.instrumented("recover")
        self.assertEqual(repeated["registrations"], 0, repeated)
        self.assertEqual(repeated["report"]["status"], "completed", repeated)
        self.assertEqual(repeated["report"]["registered_receipt_ids"], report["registered_receipt_ids"])
        self.assertEqual(self.git("rev-parse", "HEAD"), commit)
        self.assertEqual(len(self.current_receipts()), 2)
        self.assert_preserved(originals)

    def test_prior_continuation_capture_registers_completes_and_reports_after_restart(self):
        originals = self.ready_retained()
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assert_completed_once(originals, report)

    def test_lost_registration_acknowledgement_recovers_without_another_receipt(self):
        originals = self.ready_retained()
        lost = self.instrumented("lose-registration-output")
        self.assertEqual(lost["registrations"], 1, lost)
        self.assertEqual(lost["report"]["stages"]["commit"], "not_started", lost)
        receipts = {row["review_receipt_id"] for row in self.current_receipts()}
        recovered = self.instrumented("recover")
        self.assertEqual(recovered["registrations"], 0, recovered)
        self.assertEqual(set(recovered["report"]["registered_receipt_ids"]), receipts)
        self.assert_completed_once(originals, recovered["report"])
