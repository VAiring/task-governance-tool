from __future__ import annotations

import unittest
import sys
import tempfile
import json
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"))

from task_governance_tool.task_values import TaskValidationError
from task_governance_tool.verification_declaration import (
    merge_declaration_fields, normalize_not_required_reason,
    verification_requirement,
)
from tests.verification_receipt_test_support import (
    initialize, run_taskgov, payload, set_target, seed_current_review_evidence,
    completion, add_receipt,
)


class VerificationDeclarationTests(unittest.TestCase):
    def test_three_states_do_not_interpret_prose(self):
        for empty in ("", " ", "\t\n"):
            self.assertEqual(verification_requirement(empty), "unspecified")
            self.assertEqual(verification_requirement(empty, "no executable change"), "not_required")
        for text in ("run tests", "not required", "検証不要", '{"not_required":true}'):
            self.assertEqual(verification_requirement(text), "required")
        with self.assertRaises(TaskValidationError):
            verification_requirement("run tests", "no executable change")

    def test_explicit_reason_is_bounded_nonempty_and_private_data_is_rejected(self):
        self.assertEqual(normalize_not_required_reason(" short reason "), "short reason")
        self.assertEqual(len(normalize_not_required_reason("a" * 1000)), 1000)
        for value in (None, "", " \t", "a" * 1001, "Authorization: Bearer private-token"):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(TaskValidationError):
                    normalize_not_required_reason(value)

    def test_pre_bundle_native_history_cannot_acquire_a_declaration(self):
        from dataclasses import replace
        from tests.test_completion_history_projection import native_cycle
        from task_governance_tool import storage
        cycle = native_cycle()
        self.assertEqual(cycle.evidence_basis_version, 0)
        storage._validate_completion_cycle(cycle)
        for reason in ("", "Retrospective waiver"):
            with self.subTest(reason=reason), self.assertRaises(storage.StorageError):
                storage._validate_completion_cycle(replace(cycle, verification_not_required_reason=reason))

    def test_item_override_replaces_the_declaration_as_one_pair(self):
        required = {"verification": "run checks", "verification_not_required_reason": ""}
        waived = {"verification": "", "verification_not_required_reason": "no change"}
        self.assertEqual(merge_declaration_fields(required, {}), required)
        self.assertEqual(merge_declaration_fields(required, {"verification_not_required_reason": "no change"}), waived)
        self.assertEqual(merge_declaration_fields(waived, {"verification": "run checks"}), required)
        self.assertEqual(merge_declaration_fields(waived, {"verification": " "}), {
            "verification": " ", "verification_not_required_reason": "",
        })
        self.assertEqual(required["verification"], "run checks")


class VerificationDeclarationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.repo, self.db = initialize(Path(self.root.name))

    def call(self, *args):
        return payload(run_taskgov(*args, "--repo", str(self.repo), "--db", str(self.db), "--json"))

    def add(self, *args):
        result = self.call("task", "add", "--title", "declaration test", "--status", "in_progress", "--review-tier", "0", *args)
        self.assertTrue(result["ok"], result)
        return result["data"]["task"]

    def test_omission_blocks_before_and_after_target(self):
        task = self.add()
        self.assertEqual(task["verification_requirement"], "unspecified")
        result = self.call("task", "show", task["task_id"])
        self.assertEqual(result["data"]["verification_evidence"]["gate"]["blocking_code"], "verification_requirement_unspecified")
        target = self.call("review", "target", "set", task["task_id"], "--kind", "diff_fingerprint",
                           "--revision", "sha256:" + "a" * 64)
        self.assertTrue(target["ok"], target)
        self.assertEqual(target["data"]["verification_route"], "blocked")
        self.assertEqual(target["data"]["blocking_code"], "verification_requirement_unspecified")
        self.assertEqual(target["data"]["review_preparation"]["status"], "not_applicable")
        generation = target["data"]["task"]["review_target_generation"]
        result = self.call("task", "show", task["task_id"])
        self.assertEqual(result["data"]["task"]["review_target_generation"], generation)
        self.assertFalse(result["data"]["verification_evidence"]["gate"]["satisfied"])
        for check in (True, False):
            result = self.call("task", "complete", task["task_id"], "--commit-not-required",
                               "--verification-complete", "--review-complete", *(('--check',) if check else ()))
            code = result["data"]["blocking_codes"][0] if check else result["errors"][0]["code"]
            self.assertEqual(code, "verification_requirement_unspecified", result)

    def test_explicit_waiver_completes_and_reason_change_invalidates_target(self):
        task = self.add("--verification-not-required", "no executable change")
        self.assertEqual(task["verification_requirement"], "not_required")
        generation = seed_current_review_evidence(self.db, self.repo, task["task_id"])
        result = self.call("task", "edit", task["task_id"], "--verification-not-required", "only an explicit declaration")
        self.assertTrue(result["ok"], result)
        result = self.call("task", "show", task["task_id"])
        self.assertGreater(result["data"]["task"]["review_target_generation"], generation)
        self.assertEqual(result["data"]["task"]["review_target_kind"], "")
        seed_current_review_evidence(self.db, self.repo, task["task_id"])
        result = completion(self.db, self.repo, task["task_id"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.call("task", "show", task["task_id"])["ok"])

    def test_required_verification_needs_a_fresh_full_passing_receipt(self):
        task_id = self.add("--verification", "Run focused checks")["task_id"]
        for result, coverage in (("fail", "full"), ("pass", "partial"), ("pass", "full")):
            generation = seed_current_review_evidence(self.db, self.repo, task_id)
            self.assertEqual(add_receipt(self.db, self.repo, task_id, generation,
                                        result=result, scope_coverage=coverage).returncode, 0)
            completed = completion(self.db, self.repo, task_id)
            self.assertEqual(completed.returncode == 0, (result, coverage) == ("pass", "full"), completed.stdout)

    def test_acceptance_and_assertions_never_infer_a_declaration(self):
        task_id = self.add("--verification", " \t", "--contract-scope", "One bounded edit",
                           "--contract-acceptance", "Tests and both reviews pass")["task_id"]
        seed_current_review_evidence(self.db, self.repo, task_id)
        result = payload(completion(self.db, self.repo, task_id))
        self.assertEqual(result["errors"][0]["code"], "verification_requirement_unspecified")

    def test_required_waiver_and_unspecified_edits_replace_the_pair(self):
        task_id = self.add("--verification", "checks")["task_id"]
        for arguments, expected in ((('--verification-not-required', 'No executable change'), 'not_required'),
                                    (('--verification', 'checks again'), 'required'),
                                    (('--verification', ' '), 'unspecified')):
            self.assertTrue(self.call("task", "edit", task_id, *arguments)["ok"])
            task = self.call("task", "show", task_id)["data"]["task"]
            self.assertEqual(task["verification_requirement"], expected)
        invalid = self.call("task", "edit", task_id, "--verification", "checks",
                            "--verification-not-required", "No change")
        self.assertFalse(invalid["ok"])

    def test_batch_inherits_or_replaces_the_complete_pair_and_rejects_atomically(self):
        from tests.test_task_batch import BinaryInput, document, encode
        from tests.m14_test_support import file_snapshot
        tasks = [{"title": title, "contract": None, **fields} for title, fields in (
            ("Inherited", {}), ("Required", {"verification": "checks"}),
            ("Unspecified", {"verification": " "}),
            ("Waived", {"verification_not_required_reason": "local reason"}),
        )]
        candidate = document(common={"review_tier": 0, "verification_not_required_reason": "common reason"}, tasks=tasks)
        with mock.patch.object(sys, "stdin", BinaryInput(encode(candidate))):
            result = self.call("task", "add", "--from-stdin")
        self.assertTrue(result["ok"], result)
        rows = self.call("task", "list")["data"]["tasks"]
        self.assertEqual({row["title"]: row["verification_requirement"] for row in rows}, {
            "Inherited": "not_required", "Required": "required", "Unspecified": "unspecified", "Waived": "not_required",
        })
        for reason in ("", " ", None, "Authorization: Bearer private-token"):
            candidate["tasks"][-1]["verification_not_required_reason"] = reason
            before = file_snapshot(Path(self.root.name))
            with mock.patch.object(sys, "stdin", BinaryInput(encode(candidate))):
                rejected = self.call("task", "add", "--from-stdin")
            self.assertFalse(rejected["ok"])
            self.assertNotIn("private-token", json.dumps(rejected))
            self.assertEqual(file_snapshot(Path(self.root.name)), before)
