"""A newer operational journal cannot replace restored core review evidence."""

from contextlib import ExitStack
import json
import subprocess
import sys
from unittest import mock

from tests import test_review_finalization as original
from tests import test_review_handoff_preparation as fixtures
from tests import test_review_answer_installed_finalization as collected
from tests.test_review_results import encode, receipt
from task_governance_tool import backup
from task_governance_tool import review_finalization as finalization


class RestoredFinalizationTests(fixtures.PreparationFixture):
    git = fixtures.ReviewerMaterialTests.git
    committed_fixture = fixtures.ReviewerMaterialTests.committed_fixture
    prepared = original.InstalledFinalizationTests.prepared
    prepare_task = original.InstalledFinalizationTests.prepare_task
    save = original.InstalledFinalizationTests.save
    service = original.InstalledFinalizationTests.service

    def prepare_restore(self, *, alternatives=True, finding=True):
        task, context = self.prepared()
        if alternatives:
            payload = json.loads((self.root / context["packet_path"]).read_bytes())["result_template"]
            payload["receipts"] = [receipt("alternative-a"), receipt("alternative-b")]
            added = subprocess.run([sys.executable, "-I", "-S", str(self.install.entrypoint),
                "review", "result", "add", task, "--json"], input=encode(payload),
                cwd=self.root, capture_output=True, check=False)
            self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
        service = self.service(task)
        with backup.managed_backup_lock(service.resolution.target):
            backup.publish_setup_backup(service.resolution.target, 3)
        self.save(context, 0, finding=finding)
        self.save(context, 1)
        return task, context, service

    def restore_core(self):
        # Only this test's physically installed canonical database is removed.
        self.assertTrue(self.install.db_path.is_relative_to(self.root))
        self.install.db_path.unlink()
        restored = self.cli("setup")
        self.assertIn("database_restore", restored["completed_writes"])

    def assert_no_later_effects(self, service, *, check=False):
        with ExitStack() as stack:
            calls = [stack.enter_context(mock.patch.object(owner, name,
                side_effect=AssertionError("restored evidence reached a later effect")))
                for owner, name in ((service.core, "register"), (service.core, "complete"),
                    (finalization.git, "create_candidate"), (finalization.git, "publish_candidate"))]
            report = service.execute(check=check)
        for call in calls:
            call.assert_not_called()
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["status"], "attention_required", report)
        self.assertEqual(report["blocking_code"], "finalization_registration_incomplete", report)
        self.assertIsNone(report["observed_registered_receipt_ids"])
        return report

    def test_managed_restore_stops_exact_receipt_loss_even_with_other_passes(self):
        for alternatives in (False, True):
            with self.subTest(alternatives=alternatives):
                # Each subcase has its own physical install and journal.
                case = RestoredFinalizationTests(methodName="runTest")
                case.setUp()
                try:
                    task, _context, service = case.prepare_restore(alternatives=alternatives)
                    before = service.execute()
                    case.assertEqual(before["blocking_code"], "finalization_findings_require_judgment", before)
                    case.assertEqual(before["stages"]["registration"], "succeeded")
                    case.assertEqual(len(before["registered_findings"]), 1)
                    journal = service.path.read_bytes()
                    head = case.git("rev-parse", "HEAD")
                    case.restore_core()
                    state = case.cli("task", "show", task)
                    case.assertEqual(state["review_evidence"]["gate"]["satisfied"], alternatives)
                    for check in (True, False):
                        case.assert_no_later_effects(service, check=check)
                    case.assertEqual(service.path.read_bytes(), journal)
                    case.assertEqual(case.git("rev-parse", "HEAD"), head)
                    case.assertNotEqual(case.cli("task", "show", task)["task"]["status"], "done")
                finally:
                    case.doCleanups()

    def test_restored_registration_cannot_publish_saved_candidate_or_complete_published_one(self):
        for published in (False, True):
            with self.subTest(published=published):
                case = RestoredFinalizationTests(methodName="runTest")
                case.setUp()
                try:
                    task, _context, service = case.prepare_restore(finding=False)
                    complete = service.core.complete
                    def stop_completion(*args, **kwargs):
                        if kwargs.get("check"):
                            return complete(*args, **kwargs)
                        raise finalization.FinalizationError("completion_failed")
                    owner, name, action = ((service.core, "complete", stop_completion) if published else
                        (finalization.git, "publish_candidate", finalization.FinalizationError("publication_failed")))
                    with mock.patch.object(owner, name, side_effect=action):
                        first = service.execute()
                    case.assertIsNotNone(first["candidate_commit_id"], first)
                    case.assertEqual(first["commit_observation"], "published" if published else "not_published")
                    journal = service.path.read_bytes()
                    head = case.git("rev-parse", "HEAD")
                    case.restore_core()
                    report = case.assert_no_later_effects(service)
                    case.assertEqual(report["candidate_commit_id"], first["candidate_commit_id"])
                    case.assertEqual(report["commit_observation"], first["commit_observation"])
                    case.assertEqual(service.path.read_bytes(), journal)
                    case.assertEqual(case.git("rev-parse", "HEAD"), head)
                finally:
                    case.doCleanups()

    def test_missing_original_after_registration_stops_recovery(self):
        _task, context, service = self.prepare_restore()
        first = service.execute()
        self.assertEqual(first["stages"]["registration"], "succeeded")
        (self.root / context["review_requests"][0]["result_path"]).unlink()
        with mock.patch.object(finalization.git, "create_candidate") as create:
            report = service.execute()
        create.assert_not_called()
        self.assertEqual(report["blocking_code"], "finalization_originals_unavailable", report)
        self.assertEqual(report["stages"]["registration"], "succeeded")

    def test_restore_after_candidate_creation_is_rechecked_before_publication(self):
        _task, _context, service = self.prepare_restore(finding=False)
        create = finalization.git.create_candidate
        def restore_after_create(*args, **kwargs):
            candidate = create(*args, **kwargs)
            self.restore_core()
            return candidate
        with mock.patch.object(finalization.git, "create_candidate", side_effect=restore_after_create), \
             mock.patch.object(finalization.git, "publish_candidate") as publish:
            report = service.execute()
        publish.assert_not_called()
        self.assertEqual(report["blocking_code"], "finalization_registration_incomplete", report)
        self.assertIsNotNone(report["candidate_commit_id"])
        self.assertEqual(report["commit_observation"], "not_published")

    def test_restore_after_completion_preflight_is_rechecked_before_dispatch(self):
        _task, _context, service = self.prepare_restore(finding=False)
        restored = False
        def guard(_observe):
            nonlocal restored
            if service.journal.read().completion == "dispatching" and not restored:
                self.restore_core()
                restored = True
        complete = service.core.complete
        def complete_checked(*args, **kwargs):
            self.assertTrue(kwargs.get("check"), "native completion was dispatched")
            return complete(*args, **kwargs)
        with mock.patch.object(service.core, "complete", side_effect=complete_checked) as calls:
            report = service.execute(guard=guard)
        self.assertTrue(restored)
        self.assertTrue(all(call.kwargs.get("check") for call in calls.call_args_list))
        self.assertEqual(report["blocking_code"], "finalization_registration_incomplete", report)
        self.assertEqual(report["commit_observation"], "published")
        self.assertNotEqual(report["task_status"], "done")

    def test_completed_check_cannot_hide_unavailable_exact_registration(self):
        _task, _context, service = self.prepare_restore(finding=False)
        completed = service.execute()
        self.assertEqual(completed["status"], "completed", completed)
        with mock.patch.object(finalization, "registered_originals", return_value=()):
            report = self.assert_no_later_effects(service, check=True)
        self.assertEqual(report["task_status"], "done")
        self.assertEqual(report["stages"], completed["stages"])
        self.assertEqual(report["commit_id"], completed["commit_id"])


class CollectedRestoreTests(fixtures.PreparationFixture):
    invoke = collected.InstalledAnswerFinalizationTests.invoke
    git = RestoredFinalizationTests.git
    committed_fixture = RestoredFinalizationTests.committed_fixture
    prepared = collected.InstalledAnswerFinalizationTests.prepared
    ready = collected.InstalledAnswerFinalizationTests.ready
    collect = collected.InstalledAnswerFinalizationTests.collect
    service = RestoredFinalizationTests.service
    restore_core = RestoredFinalizationTests.restore_core
    assert_no_later_effects = RestoredFinalizationTests.assert_no_later_effects

    def test_collected_originals_require_their_exact_restored_receipts(self):
        self.cli("setup", "--review-answer-collection", "on", "--review-wait", "off")
        self.ready()
        service = self.service(self.task_id)
        with backup.managed_backup_lock(service.resolution.target):
            backup.publish_setup_backup(service.resolution.target, 3)
        with mock.patch.object(finalization.git, "publish_candidate",
                side_effect=finalization.FinalizationError("publication_failed")):
            registered = service.execute()
        self.assertEqual(registered["stages"]["registration"], "succeeded", registered)
        self.assertEqual(len(registered["registered_receipt_ids"]), 2)
        self.restore_core()
        self.assert_no_later_effects(service)
