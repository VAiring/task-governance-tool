"""Existing finalizer consumes the same exact inventory as manual continuation.

All native records, originals and Git objects belong to isolated fixtures.
"""

from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_review_handoff_continuation as fixtures
from tests.review_answer_fixture_support import review_fixture
from tests.test_review_continuation import A, B
from tests.verification_receipt_test_support import run_taskgov
from task_governance_tool import storage, review_finalization as finalization
from task_governance_tool.review_wait_basis_repository import read_preparation_basis, read_wait_basis
from task_governance_tool.review_finalization_repository import FinalizationRecord, FinalizationRepository
from task_governance_tool.review_preparation_repository import PreparationRepository
from task_governance_tool.review_results import review_result_template


class FinalizationContinuationTests(unittest.TestCase):
    def setUp(self):
        fixture = self.enterContext(review_fixture(schema_version=29, contract=None))
        self.repo, self.db, self.task_id = fixture.repo, fixture.db, fixture.task_id
        self.target, self.basis = fixture.target, fixture.basis
        self.caller, self.packet = fixture.caller, fixture.packet

    physical = fixtures.HandoffContinuationTests.physical
    publish = fixtures.HandoffContinuationTests.publish
    call = fixtures.HandoffContinuationTests.call

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, check=True)
        return result.stdout

    def save_actor(self, actor, packet_path, output, *, verdict="pass", findings=()):
        from tests.test_review_results import receipt, encode
        from task_governance_tool import review_handoff as files
        from task_governance_tool.session_identity import CallerIdentity
        payload = json.loads((self.repo / packet_path).read_bytes())["result_template"]
        payload["receipts"] = [receipt("a" if actor == A else "b", verdict=verdict, findings=findings)]
        raw = encode(payload)
        with mock.patch("task_governance_tool.session_identity.capture_caller_identity", return_value=CallerIdentity(actor)), \
             mock.patch("task_governance_tool.usage_lifecycle.register_reviewer"):
            self.assertEqual(files.save(self.repo, packet_path, output, raw)["status"], "saved")
        return raw

    def ready(self):
        self.physical()
        self.git("config", "user.name", "Isolated fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "source.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", ".gitignore", "source.py")
        self.git("commit", "--quiet", "-m", "Fixture base")
        (self.repo / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "source.py")
        outcome = run_taskgov("review", "target", "set", "--repo", str(self.repo), "--db", str(self.db),
            self.task_id, "--kind", "git_snapshot", "--json")
        self.assertEqual(outcome.returncode, 0, outcome.stdout or outcome.stderr)
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            self.basis = read_preparation_basis(connection, project_id=self.target.project.project_id,
                task_id=self.task_id, caller=self.caller)
            wait = read_wait_basis(connection, project_id=self.target.project.project_id, task_id=self.task_id)
        self.packet["review_target"] = {key: self.basis["target_" + key] for key in ("kind", "value", "base_revision", "generation")}
        self.packet["result_template"] = review_result_template(self.task_id, self.basis["contract_revision"], self.packet["review_target"])
        self.path.write_text(json.dumps(self.packet), encoding="utf-8")
        intent = FinalizationRecord(wait, "reviews/packet.json", sha256(self.path.read_bytes()).hexdigest(),
            ("reviews/a.json", "reviews/b.json"), self.git("symbolic-ref", "HEAD").decode().strip())
        identity = sha256((self.caller.require() + ":" + self.task_id).encode("ascii")).hexdigest()
        journal_path = self.root / f"finalize-{identity}-{self.basis['target_generation']}.sqlite"
        self.resolution.paths.review_finalization_store = lambda *_: journal_path
        self.journal = FinalizationRepository(journal_path)
        with self.journal.serial(initial=intent):
            pass
        self.publish(route="integrated")
        paths = self.resolution.paths
        paths.review_wait_request_store = lambda *_: self.root / "request.sqlite"
        paths.review_wait_dispatch_store = lambda *_: self.root / "dispatch.sqlite"
        paths.review_continuation_store = lambda *_: self.root / "transition.sqlite"
        self.enterContext(mock.patch.object(fixtures.continuation, "resolve", return_value=self.resolution))
        self.enterContext(mock.patch.object(finalization, "resolve", return_value=self.resolution))
        # Initial-round submission discovers the saved protocol independently.
        # Give that boundary the same real internal-path DB; keep locate and
        # its schema/preparation checks intact.
        from task_governance_tool import review_answer_preparation
        self.enterContext(mock.patch.object(review_answer_preparation, "resolve", return_value=self.resolution))
        self.resolution.stored_project = SimpleNamespace(canonical_path_hash=self.basis["project_path_hash"],
            binding_generation=self.basis["project_binding_generation"])
        # Internal-path fixture: the subprocess provider has no public alternate
        # DB. Read the same genuine query-only native basis at each boundary.
        def current_basis(*_, **__):
            with closing(storage.connect_initialized_readonly(self.target)) as connection:
                return {"ok": True, "status": "review_wait_basis", "basis": read_wait_basis(connection,
                    project_id=self.target.project.project_id, task_id=self.task_id)}
        self.enterContext(mock.patch.object(finalization, "wait_basis", side_effect=current_basis))
        self.args = SimpleNamespace(task_id=self.task_id, target_generation=self.basis["target_generation"],
            directory="reviews/continue-1", records_only=False)
        self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=A, action="supplement",
            prior_receipt_id=None, reason="Continue the missing original")])).encode()

    def prepared_sources(self, *, failed_actor=None, incidental_actor=None, unknown_actor=None):
        self.ready()
        if incidental_actor is not None or unknown_actor is not None:
            actor = incidental_actor or unknown_actor
            with self.journal.serial() as lease:
                self.journal.observe_reviewers(lease, ((actor, "completed-review-turn", "completed"),))
                if incidental_actor is not None:
                    self.journal.observe_reviewers(lease, ((actor, "later-incidental-turn", "completed"),))
                else:
                    self.journal.advance(lease, reviewer_blocker="finalization_reviewer_unknown")
        if failed_actor is not None:
            with self.journal.serial() as lease:
                self.journal.observe_reviewers(lease, ((failed_actor, "old-review-turn", "failed"),))
        old = self.save_actor(B, "reviews/packet.json", "reviews/b.json")
        result = self.call()
        self.assertTrue(result["ok"], result)
        self.assertNotIn("submit_command", result["handoff"])
        handoff = result["handoff"]
        self.save_actor(A, handoff["packet_path"], handoff["review_requests"][0]["result_path"])
        service = finalization.Finalizer(self.repo, self.task_id, self.caller,
            generation=self.basis["target_generation"])
        return service, old

    def test_full_inventory_uses_unchanged_old_original_and_new_selected_original(self):
        service, old = self.prepared_sources()
        record = self.journal.read()
        with self.journal.serial():
            submission, locations, retained = service._continuation_submission(record)
        self.assertEqual(tuple(row.binding.session_id for row in submission.bindings), (B, A))
        self.assertEqual(locations[0], ("reviews/packet.json", "reviews/b.json"))
        self.assertEqual(retained, ())
        self.assertEqual((self.repo / "reviews/b.json").read_bytes(), old)
        from task_governance_tool.review_finalization_basis import FinalizationBasis
        service.core.register(self.task_id, submission, basis=FinalizationBasis.from_record(record))
        with self.journal.serial() as lease:
            record = self.journal.advance(lease, registration="dispatching",
                originals=tuple((row.binding.session_id, row.binding.original_result_digest) for row in submission.bindings),
                original_locations=locations, retained_receipt_ids=retained)
        available = service._originals(record)
        self.assertEqual([row["status"] for row in available], ["available", "available"], available)
        self.assertTrue(service.core.read(self.task_id)["review_evidence"]["gate"]["satisfied"])

    def test_complete_batch_registers_once_then_verification_gate_blocks_git(self):
        service, old = self.prepared_sources()
        before = self.git("rev-parse", "HEAD")
        report = service.execute()
        self.assertEqual(report["stages"]["registration"], "succeeded", report)
        self.assertEqual(len(report["registered_receipt_ids"]), 2)
        self.assertEqual(len(report["originals"]), 2)
        self.assertEqual(report["stages"]["commit"], "not_started")
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        with mock.patch.object(service.core, "register", side_effect=AssertionError("do not register twice")):
            repeated = service.execute()
        self.assertEqual(repeated["registered_receipt_ids"], report["registered_receipt_ids"])
        self.assertEqual((self.repo / "reviews/b.json").read_bytes(), old)

    def test_changed_original_never_dispatches_registration(self):
        service, _ = self.prepared_sources()
        (self.repo / "reviews/b.json").write_bytes(b"changed")
        with mock.patch.object(service.core, "register", side_effect=AssertionError("invalid original")):
            report = service.execute()
        self.assertFalse(report["ok"])
        self.assertEqual(report["stages"]["registration"], "not_started")

    def test_unselected_failed_actor_is_not_erased_by_another_reviewers_pass(self):
        service, _ = self.prepared_sources(failed_actor=B)
        report = service.execute()
        self.assertEqual(report["stages"]["registration"], "succeeded", report)
        self.assertEqual(report["blocking_code"], "finalization_reviewer_failed")
        self.assertEqual(report["stages"]["commit"], "not_started")
        self.assertEqual(self.journal.read_rounds()[0].reviewer_observations, ((B, "old-review-turn", "failed"),))

    def test_selected_successor_can_retire_its_own_execution_failure_without_editing_history(self):
        service, _ = self.prepared_sources(failed_actor=A)
        report = service.execute()
        self.assertEqual(report["stages"]["registration"], "succeeded", report)
        self.assertNotEqual(report["blocking_code"], "finalization_reviewer_failed")
        self.assertTrue(service.saved_results_ready())
        self.assertEqual(self.journal.read_rounds()[0].reviewer_blocker, "finalization_reviewer_failed")

    def test_completed_retained_actor_mismatch_uses_bound_judgment_and_preserves_history(self):
        service, _ = self.prepared_sources(incidental_actor=B)
        old = self.journal.read_rounds()[0]
        result = service.execute()
        self.assertEqual(result["stages"]["registration"], "succeeded", result)
        self.assertNotEqual(result["blocking_code"], "finalization_reviewer_mismatch")
        self.assertTrue(service.saved_results_ready())
        self.assertEqual(self.journal.read_rounds()[0], old)

    def test_completed_selected_actor_mismatch_uses_registered_successor(self):
        service, _ = self.prepared_sources(incidental_actor=A)
        old = self.journal.read_rounds()[0]
        with self.assertRaises(finalization.FinalizationError):
            service._require_reviewer_history(self.journal.read())
        result = service.execute()
        self.assertNotEqual(result["blocking_code"], "finalization_reviewer_mismatch")
        self.assertTrue(service.saved_results_ready())
        self.assertEqual(self.journal.read_rounds()[0], old)

    def test_unknown_execution_is_not_inferred_incidental_from_completed_observation(self):
        service, _ = self.prepared_sources(unknown_actor=B)
        result = service.execute()
        self.assertEqual(result["blocking_code"], "finalization_reviewer_unknown", result)
        self.assertEqual(result["stages"]["commit"], "not_started")

    def test_current_completed_mismatch_uses_exact_registered_set_without_new_round(self):
        self.ready()
        self.save_actor(A, "reviews/packet.json", "reviews/a.json")
        self.save_actor(B, "reviews/packet.json", "reviews/b.json")
        with self.journal.serial() as lease:
            self.journal.observe_reviewers(lease, ((A, "completed-review-turn", "completed"),))
            self.journal.observe_reviewers(lease, ((A, "later-turn", "completed"),))
        service = finalization.Finalizer(self.repo, self.task_id, self.caller,
                                        generation=self.basis["target_generation"])
        self.assertFalse(service.saved_results_ready())
        result = service.execute()
        self.assertEqual(result["stages"]["registration"], "succeeded", result)
        self.assertNotEqual(result["blocking_code"], "finalization_reviewer_mismatch")
        self.assertTrue(service.saved_results_ready())
        self.assertEqual(len(self.journal.read_rounds()), 1)
        self.assertEqual(self.journal.read().reviewer_blocker, "finalization_reviewer_mismatch")
        recorded = run_taskgov("verification", "receipt", "add", self.task_id,
            "--result", "pass", "--duration-ms", "1", "--scope-coverage", "full",
            "--expected-target-generation", str(self.basis["target_generation"]),
            "--repo", str(self.repo), "--db", str(self.db), "--json")
        self.assertEqual(recorded.returncode, 0, recorded.stdout or recorded.stderr)
        completed = service.execute()
        self.assertEqual(completed["status"], "completed", completed)
        self.assertTrue(service.saved_results_ready())
        repeated = service.execute(check=True)
        self.assertEqual(repeated["status"], "completed", repeated)
        self.assertEqual(self.journal.read().reviewer_blocker, "finalization_reviewer_mismatch")

    def test_reviewer_failure_report_preserves_same_basis_and_changed_basis_choices(self):
        service, _ = self.prepared_sources(failed_actor=B)
        result = service.execute()
        self.assertIn("same-basis continuation", result["next_action"])
        self.assertIn("only needed reviewers", result["next_action"])
        self.assertIn("Changed material or conditions require a fresh target", result["next_action"])
        self.assertIn("unknown effects", result["next_action"])

    def test_records_only_cannot_convert_an_integrated_preparation(self):
        self.ready()
        self.args.records_only = True
        before = self.journal.read()
        result = self.call()
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "review_continuation_unavailable")
        self.assertEqual(self.journal.read(), before)
        self.assertFalse((self.repo / self.args.directory).exists())

    def test_unknown_root_effect_prevents_core_successor_admission(self):
        self.ready()
        with self.journal.serial() as lease:
            self.journal.advance(lease, registration="dispatching", originals=((A, "a" * 64), (B, "b" * 64)))
        result = self.call()
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "review_continuation_effect_unknown")
        from task_governance_tool.review_continuation_handoff_repository import read_state
        self.assertEqual(read_state(self.target, self.task_id, self.caller).records, ())
        self.assertFalse((self.repo / self.args.directory).exists())

    def test_native_commit_completion_and_report_reentry_preserve_exact_root_and_originals(self):
        service, old = self.prepared_sources()
        recorded = run_taskgov("verification", "receipt", "add", self.task_id,
            "--result", "pass", "--duration-ms", "1", "--scope-coverage", "full",
            "--expected-target-generation", str(self.basis["target_generation"]),
            "--repo", str(self.repo), "--db", str(self.db), "--json")
        self.assertEqual(recorded.returncode, 0, recorded.stdout or recorded.stderr)
        report = service.execute()
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(report["stages"], dict(registration="succeeded", commit="succeeded", completion="succeeded"))
        commit = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("show", "HEAD:source.py"), b"value = 2\n")
        self.assertEqual(self.git("rev-parse", "HEAD^" ).decode().strip(), self.basis["target_base_revision"])
        with mock.patch.object(service.core, "register", side_effect=AssertionError("registration replay")), \
             mock.patch.object(service.core, "complete", side_effect=AssertionError("completion replay")):
            repeated = service.execute()
        self.assertEqual(repeated["status"], "completed", repeated)
        self.assertEqual(self.git("rev-parse", "HEAD"), commit)
        self.assertEqual(repeated["registered_receipt_ids"], report["registered_receipt_ids"])
        self.assertEqual((self.repo / "reviews/b.json").read_bytes(), old)

    def test_identical_reassessment_body_cannot_reconcile_to_its_predecessor_receipt(self):
        service, _ = self.prepared_sources()
        first = service.execute()
        self.assertEqual(first["stages"]["registration"], "succeeded", first)
        old_record = self.journal.read()
        prior = next(row for row in service.core.read(self.task_id)["review_evidence"]["current_receipts"]
            if row["reviewer_key"] == "a")["review_receipt_id"]
        self.args.directory = "reviews/reassess-2"
        self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=A, action="reassess",
            prior_receipt_id=prior, reason="Reconsider the explanation under the unchanged conditions")])).encode()
        continued = self.call()
        self.assertTrue(continued["ok"], continued)
        handoff = continued["handoff"]
        raw = self.save_actor(A, handoff["packet_path"], handoff["review_requests"][0]["result_path"])
        self.assertEqual(raw, (self.repo / old_record.result_paths[0]).read_bytes())
        from task_governance_tool.review_finalization_repository import registered_originals
        round_record = self.journal.read()
        candidate = replace(round_record, registration="dispatching", originals=((A, sha256(raw).hexdigest()),),
            original_locations=((handoff["packet_path"], handoff["review_requests"][0]["result_path"]),))
        self.assertEqual(registered_originals(self.target, candidate, [prior]), ())
        result = service.execute()
        self.assertEqual(result["stages"]["registration"], "succeeded", result)
        self.assertNotEqual(result["registered_receipt_ids"], [prior])

    def test_report_keeps_retained_and_reassessed_findings_through_completion_and_delivery(self):
        self.ready()
        original_a = self.save_actor(A, "reviews/packet.json", "reviews/a.json",
            verdict="changes_requested", findings=[dict(severity="medium", summary="Check the unchanged condition")])
        original_b = self.save_actor(B, "reviews/packet.json", "reviews/b.json",
            findings=[dict(severity="low", summary="Document the accepted choice")])
        service = finalization.Finalizer(self.repo, self.task_id, self.caller,
                                        generation=self.basis["target_generation"])
        first = service.execute()
        self.assertEqual(first["stages"]["registration"], "succeeded", first)
        by_reviewer = {row["reviewer_key"]: row for row in first["registered_findings"]}
        prior, retained = (by_reviewer[key]["review_receipt_id"] for key in ("a", "b"))
        expected_findings = {row["review_finding_id"] for row in by_reviewer.values()}

        def command(*args):
            result = run_taskgov(*args, "--repo", str(self.repo), "--db", str(self.db), "--json")
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
            return json.loads(result.stdout)["data"]

        command("review", "finding", "resolve", by_reviewer["b"]["review_finding_id"],
                "--resolution", "Accepted documented choice")
        chain = [prior]
        for number in (1, 2):
            self.args.directory = f"reviews/reassess-{number}"
            self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=A, action="reassess",
                prior_receipt_id=chain[-1], reason="Reconsider the unchanged condition")])).encode()
            continued = self.call()
            self.assertTrue(continued["ok"], continued)
            pending = service.execute(check=True)
            # A selected but unregistered successor is not applied history.
            self.assertEqual(pending["registered_findings"], [])
            handoff = continued["handoff"]
            self.save_actor(A, handoff["packet_path"], handoff["review_requests"][0]["result_path"])
            registered = service.execute()
            self.assertEqual(registered["stages"]["registration"], "succeeded", registered)
            self.assertEqual(len(registered["observed_registered_receipt_ids"]), 1)
            chain.append(registered["observed_registered_receipt_ids"][0])
            self.assertEqual({row["review_finding_id"] for row in registered["registered_findings"]}, expected_findings)
            if number == 1:
                command("review", "finding", "resolve", by_reviewer["a"]["review_finding_id"],
                    "--resolution", "Actual reviewer confirmed the unchanged condition",
                    "--reassessment-receipt", chain[-1])

        command("verification", "receipt", "add", self.task_id, "--result", "pass", "--duration-ms", "1",
                "--scope-coverage", "full", "--expected-target-generation", str(self.basis["target_generation"]))
        completed = service.execute()
        self.assertEqual(completed["status"], "completed", completed)
        shown = command("task", "show", self.task_id)
        self.assertEqual(shown["review_evidence"]["current_findings"], [])
        with mock.patch.object(service.core, "register", side_effect=AssertionError("no replay")), \
             mock.patch.object(service.core, "complete", side_effect=AssertionError("no replay")):
            repeated = service.execute(check=True)
        self.assertEqual(repeated["registered_findings"], completed["registered_findings"])
        self.assertEqual(repeated["reported_receipts"], completed["reported_receipts"])
        self.assertEqual(len(completed["registered_findings"]), 2)
        self.assertTrue(all(row["status"] == "resolved" for row in completed["registered_findings"]))
        sources = {row["review_receipt_id"]: row for row in completed["reported_receipts"]}
        self.assertEqual(set(sources), {*chain, retained})
        self.assertEqual(sources[retained]["roles"], ["retained"])
        self.assertEqual(sources[chain[-1]]["roles"], ["registered"])
        self.assertTrue(all(row["review_details"] == dict(status="not_recorded", provenance_version=1,
            checks=None, limitations=None) for row in sources.values()))
        for predecessor, successor in zip(chain, chain[1:]):
            self.assertEqual(sources[predecessor]["roles"], ["reassessment_source"])
            self.assertEqual(sources[predecessor]["successor_receipt_ids"], [successor])
        self.assertEqual(completed["observed_registered_receipt_ids"], [chain[-1]])
        self.assertEqual((self.repo / "reviews/a.json").read_bytes(), original_a)
        self.assertEqual((self.repo / "reviews/b.json").read_bytes(), original_b)
        self.assertIn("summary_ref", completed["originals"][0]["review"])
        from tests.test_review_wait_managed import ManagedHostTests, OTHER
        host = ManagedHostTests().host()
        host.task_id = self.task_id
        host.set_finalization_result(OTHER, completed)
        delivered = json.loads(host._direct_prompt(OTHER).splitlines()[2])
        self.assertEqual({key: value for key, value in delivered.items() if key != "delivery"}, completed)

        with mock.patch.object(finalization, "reported_review_evidence", side_effect=ValueError("unreadable")):
            unavailable = service.execute(check=True)
        self.assertEqual(unavailable["observed_registered_receipt_ids"], [chain[-1]])
        self.assertIsNone(unavailable["registered_findings"])
        self.assertIsNone(unavailable["reported_receipts"])
        self.assertIn("reported_reviews_and_findings", unavailable["unavailable"])
        with mock.patch.object(service.core, "read", side_effect=ValueError("unreadable")):
            unavailable = service.execute(check=True)
        self.assertIsNone(unavailable["observed_registered_receipt_ids"])
        self.assertIsNone(unavailable["registered_findings"])
        self.assertIsNone(unavailable["recorded_work"])
        with mock.patch.object(finalization, "registered_originals", return_value=()):
            unavailable = service.execute(check=True)
        self.assertIsNone(unavailable["observed_registered_receipt_ids"])
        self.assertIsNone(unavailable["registered_findings"])
        self.assertIn("registered_reviews_and_findings", unavailable["unavailable"])

        from task_governance_tool.review_finalization_repository import reported_review_evidence, RepositoryError
        current = self.journal.read()
        for changed in (
            replace(current, basis={**current.basis, "task_id": "tg_task_ffffffffffffffff"}),
            replace(current, basis={**current.basis, "target_generation": self.basis["target_generation"] + 1}),
            replace(current, retained_receipt_ids=("tg_review_receipt_ffffffffffffffff",)),
        ):
            with self.subTest(basis=changed.basis, retained=changed.retained_receipt_ids), self.assertRaises(RepositoryError):
                reported_review_evidence(self.target, changed, [chain[-1]])

    def test_report_validates_complete_history_across_finding_batch_limit(self):
        self.ready()
        findings = [dict(severity="low", summary=f"Recorded condition {index}") for index in range(64)]
        self.save_actor(A, "reviews/packet.json", "reviews/a.json", findings=findings)
        self.save_actor(B, "reviews/packet.json", "reviews/b.json")
        service = finalization.Finalizer(self.repo, self.task_id, self.caller,
                                        generation=self.basis["target_generation"])
        report = service.execute()
        prior = next(row["review_receipt_id"] for row in report["reviews"]["current_receipts"]
                     if row["reviewer_key"] == "a")
        for number in range(1, 7):
            self.args.directory = f"reviews/many-findings-{number}"
            self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=A, action="reassess",
                prior_receipt_id=prior, reason="Reconsider the unchanged recorded conditions")])).encode()
            continued = self.call()
            self.assertTrue(continued["ok"], continued)
            handoff = continued["handoff"]
            self.save_actor(A, handoff["packet_path"], handoff["review_requests"][0]["result_path"], findings=findings)
            report = service.execute()
            self.assertEqual(report["stages"]["registration"], "succeeded", report)
            self.assertIsNotNone(report["registered_findings"], report["unavailable"])
            self.assertEqual(len(report["registered_findings"]), 64 * (number + 1))
            prior = report["observed_registered_receipt_ids"][0]
        expected = {row["review_finding_id"] for row in service.core.read(self.task_id)["review_evidence"]["current_findings"]}
        self.assertEqual(len(expected), 448)
        with mock.patch.object(service.core, "register", side_effect=AssertionError("no replay")):
            checked = service.execute(check=True)
        self.assertEqual({row["review_finding_id"] for row in checked["registered_findings"]}, expected)
        self.assertEqual(len(checked["reported_receipts"]), 8)
        self.assertEqual(checked["registered_findings"], report["registered_findings"])

        from task_governance_tool import evidence_validation_repository as validation
        validate = validation.validate_selected_task_receipt_evidence
        finding_batches = []
        def fail_later_batch(*args, **kwargs):
            if kwargs["review_finding_ids"]:
                finding_batches.append(kwargs["review_finding_ids"])
                if len(finding_batches) == 2:
                    raise ValueError("Unavailable later batch")
            return validate(*args, **kwargs)
        from task_governance_tool.review_finalization_repository import reported_review_evidence
        with mock.patch.object(validation, "validate_selected_task_receipt_evidence", side_effect=fail_later_batch):
            with self.assertRaisesRegex(ValueError, "Unavailable later batch"):
                reported_review_evidence(self.target, self.journal.read(), [prior])
        self.assertEqual([len(batch) for batch in finding_batches], [400, 48])


if __name__ == "__main__":
    unittest.main()
