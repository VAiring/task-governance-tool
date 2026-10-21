"""Explicit legacy dedication through public installed handoff and collection."""
from __future__ import annotations

import json
import os

from tests import test_review_answer_tracking_installed as tracking
from tests import test_review_handoff_preparation as fixtures
from tests.test_review_results import receipt, encode


class LegacyMigrationFixture(fixtures.PreparationFixture):
    preparation_command = "prepare-finalization"
    git = tracking.InstalledAnswerTrackingTests.git
    committed_fixture = tracking.InstalledAnswerTrackingTests.committed_fixture
    finalize = tracking.InstalledAnswerTrackingTests.finalize
    collect_public = tracking.InstalledAnswerTrackingTests.collect_public
    assert_collected = tracking.InstalledAnswerTrackingTests.assert_collected
    current_receipts = tracking.InstalledAnswerTrackingTests.current_receipts

    def setUp(self):
        super().setUp()
        self.cli("setup", "--review-answer-collection", "off", "--review-wait", "off")
        self.committed_fixture()
        self.task_id = self.task("Focused legacy migration verification")
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "source.py")
        targeted = self.invoke(self.preparation_command, "--repo", str(self.root),
            "--directory", "reviews/g1", "target", self.task_id, "--kind", "git_snapshot")
        self.assertEqual(targeted.returncode, 0, targeted.stdout or targeted.stderr)
        prepared = self.invoke(self.preparation_command, "--repo", str(self.root),
            "--directory", "reviews/g1", "receipt", self.task_id, "--result", "pass",
            "--duration-ms", "1", "--scope-coverage", "full", "--expected-target-generation", "1")
        self.assertEqual(prepared.returncode, 0, prepared.stdout or prepared.stderr)
        self.handoff = json.loads(prepared.stdout)["handoff"]
        self.initial = self.handoff
        self.initial_packet = (self.root / self.initial["packet_path"]).read_bytes()
        self.assertIn("Save command:", self.initial["review_requests"][0]["request"])
        self.cli("setup", "--review-answer-collection", "on")

    @staticmethod
    def review_environment(index=0):
        return {**os.environ, "CODEX_THREAD_ID": tracking.reviewer(index)}

    def save_legacy(self, index, verdict="pass"):
        document = json.loads(self.initial_packet)["result_template"]
        document["receipts"] = [receipt("legacy-reviewer-" + str(index), verdict=verdict)]
        path = self.initial["review_requests"][index - 1]["result_path"]
        result = self.invoke("save", "--repo", str(self.root),
            "--packet", self.initial["packet_path"], "--output", path,
            raw=encode(document), reviewer=index)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        return {name: (self.root / name).read_bytes() for name in (path, path + ".session.json")}

    def continuation(self, selected, directory, *, migrate=False, expect=True):
        result = self.invoke("continue", "--repo", str(self.root), "--task-id", self.task_id,
            "--target-generation", "1", "--directory", directory,
            *(["--migrate-legacy-reviewers"] if migrate else []),
            raw=json.dumps(dict(version=1, reviewers=selected)).encode("utf-8"))
        if not expect:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertFalse((self.root / directory / "packet.json").exists())
            return json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.handoff = json.loads(result.stdout)["handoff"]
        return self.handoff

    def supplement(self, directory="reviews/migrated", *, migrate=False, reviewers=(1, 2)):
        selection = dict(version=1, reviewers=[dict(
            session_id=tracking.reviewer(index), action="supplement", prior_receipt_id=None,
            reason="Explicitly dedicate the same legacy reviewer to the new answer protocol")
            for index in reviewers])
        return self.continuation(selection["reviewers"], directory, migrate=migrate)


class LegacyMigrationInstalledTests(LegacyMigrationFixture):
    def test_unanswered_legacy_reviewers_arm_collect_and_complete(self):
        self.supplement(migrate=True)
        armed = self.collect_public([
            tracking.arrival(index, text="Legacy review ended without a saved answer")
            for index in (1, 2)], action="arm")
        self.assertTrue(armed["response"]["ok"], armed)
        repeated = self.collect_public([
            tracking.arrival(index, revision=2) for index in (1, 2)], action="arm")
        self.assertEqual(repeated["response"], armed["response"])
        self.assertEqual(repeated["reads"], [])
        self.assert_collected(self.collect_public([
            tracking.arrival(index, revision=2) for index in (1, 2)]), [1, 2])
        originals = {row["result_path"]: (self.root / row["result_path"]).read_bytes()
                     for row in self.handoff["review_requests"]}
        self.assert_collected(self.collect_public([
            tracking.arrival(index, "changes_requested", revision=3) for index in (1, 2)]), [1, 2])
        self.assertEqual({path: (self.root / path).read_bytes() for path in originals}, originals)
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(len(self.current_receipts()), 2)
        self.assertEqual((self.root / self.initial["packet_path"]).read_bytes(), self.initial_packet)

    def test_old_registered_key_survives_migration_and_next_continuation(self):
        originals = {**self.save_legacy(1, "changes_requested"), **self.save_legacy(2)}
        _, blocked = self.finalize(self.task_id)
        self.assertEqual(blocked["stages"]["registration"], "succeeded", blocked)
        prior = next(row for row in self.current_receipts() if row["verdict"] == "changes_requested")
        for revision in (2, 3):
            self.continuation([dict(session_id=tracking.reviewer(1), action="reassess",
                prior_receipt_id=prior["review_receipt_id"], reason="Reassess the same unchanged review")],
                "reviews/reassess-" + str(revision), migrate=revision == 2)
            armed = self.collect_public([tracking.arrival(1, revision=revision - 1)], action="arm")
            self.assertTrue(armed["response"]["ok"], armed)
            self.assertEqual(armed["reads"], [[tracking.reviewer(1), 2]])
            verdict = "unable_to_review" if revision == 2 else "pass"
            self.assert_collected(self.collect_public([tracking.arrival(1, verdict, revision)]), [1])
            _, report = self.finalize(self.task_id)
            prior = next(row for row in self.current_receipts() if row["reviewer_key"] == "legacy-reviewer-1")
            self.assertEqual(prior["judgment_revision"], revision)
            if revision == 3:
                self.assertEqual(report["status"], "completed", report)
            else:
                self.assertEqual(report["blocking_code"], "review_unable", report)
                self.assertEqual(report["stages"], dict(
                    registration="succeeded", commit="not_started", completion="not_started"))
        self.assertEqual({path: (self.root / path).read_bytes() for path in originals}, originals)
        self.assertEqual(len(self.current_receipts()), 2)
        details = [row["review_details"] for row in report["reported_receipts"]]
        self.assertEqual(len(details), 4)
        self.assertEqual(details.count(dict(status="not_recorded", provenance_version=1,
                                           checks=None, limitations=None)), 2)
        self.assertEqual(details.count(dict(status="available", provenance_version=2,
            checks=["Complete assigned material inspected"], limitations=["Offline public host fixture"])), 2)
        _, checked = self.finalize(self.task_id, check=True)
        self.assertEqual(checked["reported_receipts"], report["reported_receipts"])

    def test_migrated_selected_answer_keeps_late_unselected_legacy_original(self):
        self.supplement(migrate=True, reviewers=(1,))
        originals = self.save_legacy(2)
        self.assertTrue(self.collect_public([tracking.arrival(1)], action="arm")["response"]["ok"])
        self.assert_collected(self.collect_public([tracking.arrival(1, revision=2)]), [1])
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual({path: (self.root / path).read_bytes() for path in originals}, originals)
        self.assertEqual({row["reviewer_key"] for row in self.current_receipts()},
                         {tracking.reviewer(1), "legacy-reviewer-2"})

    def test_missing_opt_in_or_declaration_cannot_publish_then_partial_arm_recovers(self):
        selected = [dict(session_id=tracking.reviewer(index), action="supplement",
            prior_receipt_id=None, reason="Explicitly migrate the old review") for index in (1, 2)]
        missing = self.continuation(selected, "reviews/no-declaration", expect=False)
        self.assertEqual(missing["errors"][0]["code"], "review_continuation_unavailable")
        self.cli("setup", "--review-answer-collection", "off")
        disabled = self.continuation(selected, "reviews/disabled", migrate=True, expect=False)
        self.assertEqual(disabled["errors"][0]["code"], "review_continuation_unavailable")
        self.cli("setup", "--review-answer-collection", "on")
        self.continuation(selected, "reviews/migrated", migrate=True)
        first = self.collect_public([tracking.arrival(1)], action="arm")
        self.assertTrue(first["response"]["ok"], first)
        resumed = self.collect_public([tracking.arrival(1, revision=2), tracking.arrival(2)], action="arm")
        self.assertTrue(resumed["response"]["ok"], resumed)
        self.assertEqual(resumed["reads"], [[tracking.reviewer(2), 2]])
        self.assert_collected(self.collect_public([
            tracking.arrival(index, revision=2) for index in (1, 2)]), [1, 2])
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)

    def test_saved_unregistered_selected_answer_is_not_discarded_by_migration(self):
        originals = self.save_legacy(1)
        selected = [dict(session_id=tracking.reviewer(1), action="supplement",
            prior_receipt_id=None, reason="Attempt to replace an existing original")]
        refused = self.continuation(selected, "reviews/refused", migrate=True, expect=False)
        self.assertEqual(refused["errors"][0]["code"], "review_continuation_original_registration_required")
        self.assertEqual({path: (self.root / path).read_bytes() for path in originals}, originals)

    def test_issued_round_replay_keeps_protocol_and_explicit_new_round_reuses_origin(self):
        self.cli("setup", "--review-answer-collection", "off")
        old = self.supplement("reviews/old-round")
        packet = (self.root / old["packet_path"]).read_bytes()
        self.cli("setup", "--review-answer-collection", "on")
        repeated = self.supplement("reviews/old-round", migrate=True)
        self.assertEqual(repeated, {**old, "replayed": True, "review_requests": []})
        self.assertEqual((self.root / old["packet_path"]).read_bytes(), packet)
        self.assertIn("Save command:", old["review_requests"][0]["request"])
        self.assertNotIn("answer_collection_arm", repeated)
        migrated = self.supplement("reviews/migrated", migrate=True)
        self.cli("setup", "--review-answer-collection", "off")
        repeated = self.supplement("reviews/migrated")
        self.assertEqual(repeated, {**migrated, "replayed": True, "review_requests": []})
        self.assertNotIn("Save command:", migrated["review_requests"][0]["request"])
        self.cli("setup", "--review-answer-collection", "on")
        # Supersede this unanswered migrated round. Saved dedication is enough;
        # a new round still requires its own pre-send arm.
        self.supplement("reviews/next")
        self.assertTrue(self.collect_public([
            tracking.arrival(index) for index in (1, 2)], action="arm")["response"]["ok"])
        self.assert_collected(self.collect_public([
            tracking.arrival(index, revision=2) for index in (1, 2)]), [1, 2])
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assertEqual((self.root / old["packet_path"]).read_bytes(), packet)


class LegacyMigrationManualTests(LegacyMigrationFixture):
    preparation_command = "prepare"

    def test_manual_migration_keeps_old_original_and_completes(self):
        originals = self.save_legacy(2)
        self.supplement(migrate=True, reviewers=(1,))
        self.assertEqual(self.handoff["route"], "manual")
        self.assertTrue(self.collect_public([tracking.arrival(1)], action="arm")["response"]["ok"])
        self.assert_collected(self.collect_public([tracking.arrival(1, revision=2)]), [1])
        submitted = self.invoke("submit", "--repo", str(self.root),
            "--packet", self.handoff["packet_path"], "--",
            *(row["result_path"] for row in self.handoff["review_requests"]))
        self.assertEqual(submitted.returncode, 0, submitted.stdout or submitted.stderr)
        self.assertEqual(len(self.current_receipts()), 2)
        self.assertEqual({path: (self.root / path).read_bytes() for path in originals}, originals)
        self.git("commit", "-m", "Complete explicit legacy migration fixture")
        revision = self.git("rev-parse", "HEAD").decode().strip()
        self.cli("task", "complete", self.task_id, "--verification-complete", "--review-complete",
            "--completion-evidence-kind", "git_commit", "--completion-revision", revision)
        self.assertEqual(self.cli("task", "show", self.task_id)["task"]["status"], "done")
