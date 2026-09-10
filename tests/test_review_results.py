from __future__ import annotations

import copy
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    file_snapshot, initialize_taskgov_internal, make_physical_install, run_taskgov_internal,
)
from tests.test_review_evidence import database_target, receipt_add

from task_governance_tool import cli as cli_service
from task_governance_tool import review_results as result_service
from task_governance_tool import reviews as review_service
from task_governance_tool import tasks as task_service
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.storage import connect_initialized, validate_evidence_ledger_storage


FINGERPRINT = "sha256:" + "a" * 64
TASK_ID = "tg_task_0123456789abcdef"
PROVENANCE = {
    "reviewer_class": "human",
    "model_state": "not_applicable",
    "declared_model_id": None,
    "skill_state": "not_applicable",
    "declared_skill_id": None,
    "declared_skill_version": None,
    "review_profiles": ["general"],
    "review_lenses": ["correctness"],
    "context_relation": "external_context",
    "method_codes": ["review_packet_inspection"],
}


def receipt(reviewer="reviewer-a", *, findings=(), kind="independent", verdict="pass"):
    return {
        "reviewer": reviewer, "kind": kind, "verdict": verdict,
        "summary": "Focused assessment 日本語 🚀",
        "provenance": None if kind == "not_required" else copy.deepcopy(PROVENANCE),
        "findings": [dict(item) for item in findings],
    }


def document(task_id=TASK_ID, *, revision=1, generation=1, receipts=None):
    return {
        "version": 1, "task_id": task_id, "contract_revision": revision,
        "review_target": {
            "kind": "diff_fingerprint", "value": FINGERPRINT,
            "base_revision": "", "generation": generation,
        },
        "receipts": [receipt()] if receipts is None else receipts,
    }


def encode(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class BinaryInput(io.BytesIO):
    def __init__(self, value=b"", *, readable=True):
        super().__init__(value)
        self.readable_by_contract = readable
        self.read_sizes = []

    @property
    def buffer(self):
        return self

    def read(self, size=-1):
        if not self.readable_by_contract:
            raise AssertionError("stdin must not be consumed")
        self.read_sizes.append(size)
        return super().read(size)


class ReviewResultsInputTests(unittest.TestCase):
    def assert_invalid(self, payload):
        with self.assertRaises(review_service.ReviewEvidenceError) as raised:
            result_service.decode_review_results(payload)
        self.assertEqual(raised.exception.code, "invalid_review_evidence")

    def test_utf8_decode_preserves_exact_values_and_accepts_exact_byte_limit(self):
        payload = document()
        raw = encode(payload)
        self.assertEqual(result_service.decode_review_results(raw), payload)
        self.assertEqual(result_service.decode_review_results(raw.decode("utf-8")), payload)
        boundary = raw + b" " * (262144 - len(raw))
        self.assertEqual(result_service.REVIEW_RESULTS_INPUT_LIMIT, 262144)
        self.assertEqual(result_service.decode_review_results(boundary), payload)
        self.assert_invalid(boundary + b" ")
        self.assert_invalid(boundary.decode("utf-8") + "界")

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell transport contract")
    def test_readme_powershell_pipeline_preserves_utf8_review_results(self):
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("Windows PowerShell 5.1 is unavailable")
        version = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            capture_output=True, check=True, timeout=30,
        )
        if not version.stdout.strip().startswith(b"5.1."):
            self.skipTest("This regression requires Windows PowerShell 5.1")

        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        examples = []
        for block in readme.split("```")[1::2]:
            language, _, code = block.partition("\n")
            if language.strip() == "powershell" and " review result add " in code:
                examples.append(code.splitlines())
        self.assertEqual(len(examples), 1)
        lines = examples[0]
        result_lines = [
            index for index, line in enumerate(lines)
            if line.lstrip().startswith("python ") and " review result add " in line
        ]
        prepare_lines = [
            index for index, line in enumerate(lines)
            if line.lstrip().startswith("python ") and " review prepare " in line
        ]
        self.assertEqual((len(result_lines), len(prepare_lines)), (1, 1))
        self.assertLess(prepare_lines[0], result_lines[0])
        # Execute the documented transport unchanged, including its encoding
        # setup. Substitute only the native consumer so no Task or Git command
        # runs; ASCII hex reveals the exact stdin bytes without stdout recoding.
        python_path = sys.executable.replace("'", "''")
        consumer = f"& '{python_path}' -I -S -c 'import sys; print(sys.stdin.buffer.read().hex())'"
        pipeline = "\n".join(lines[prepare_lines[0] + 1:result_lines[0]] + [consumer])
        payload = document(receipts=[receipt(findings=[{"severity": "low", "summary": "所見も保持する 🔍"}])])
        raw = encode(payload)
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "review-results.json").write_bytes(raw)
            transported = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", pipeline],
                cwd=temporary, stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=30,
            )
        self.assertEqual(transported.returncode, 0, transported.stderr)
        self.assertEqual(transported.stderr, b"")
        received = bytes.fromhex(transported.stdout.decode("ascii").strip())
        self.assertEqual(received.rstrip(b"\r\n"), raw)
        self.assertEqual(json.loads(received.decode("utf-8")), payload)

    def test_decoder_rejects_duplicate_keys_nonfinite_numbers_invalid_unicode_and_non_documents(self):
        raw = encode(document())
        cases = [
            b"", b"[]", b"null", raw + raw, b"\xff", b"\xef\xbb\xbf" + raw,
            raw.replace(b'"version":1', b'"version":1,"version":1'),
            raw.replace(b'"generation":1', b'"generation":1,"generation":1'),
            raw.replace(b'"version":1', b'"version":NaN'),
            raw.replace(b'"version":1', b'"version":Infinity'),
            raw.replace(b'"version":1', b'"version":1.0'),
            raw.replace(b'"reviewer-a"', b'"\\ud800"'),
            b"[" * 1100 + b"]" * 1100, bytearray(raw), None,
        ]
        for candidate in cases:
            with self.subTest(candidate_type=type(candidate).__name__, prefix=str(candidate)[:45]):
                self.assert_invalid(candidate)

    def test_closed_schema_requires_every_field_and_exact_json_types(self):
        paths = [(), ("review_target",), ("receipts", 0), ("receipts", 0, "provenance")]
        for path in paths:
            original = document()
            for item in path:
                original = original[item]
            for key in original:
                candidate = document()
                target = candidate
                for item in path:
                    target = target[item]
                del target[key]
                with self.subTest(path=path, missing=key):
                    self.assert_invalid(encode(candidate))
            candidate = document()
            target = candidate
            for item in path:
                target = target[item]
            target["user_approved"] = True
            with self.subTest(path=path, unknown="user_approved"):
                self.assert_invalid(encode(candidate))
        changes = [
            (("version",), True), (("version",), 2), (("contract_revision",), True),
            (("contract_revision",), -1), (("contract_revision",), 2**63),
            (("review_target", "generation"), 0), (("review_target", "generation"), True),
            (("review_target", "generation"), 2**63), (("task_id",), None),
            (("receipts",), {}), (("receipts", 0, "summary"), 12),
            (("receipts", 0, "findings"), {}),
            (("receipts", 0, "provenance", "review_profiles"), "general"),
            (("receipts", 0, "provenance", "review_lenses"), [None]),
            (("receipts", 0, "provenance", "declared_model_id"), False),
        ]
        for path, value in changes:
            candidate = document()
            target = candidate
            for item in path[:-1]:
                target = target[item]
            target[path[-1]] = value
            with self.subTest(path=path, value=value):
                self.assert_invalid(encode(candidate))
        for bad_finding in ({"severity": "low"}, {"severity": "low", "summary": "x", "status": "resolved"}):
            self.assert_invalid(encode(document(receipts=[receipt(findings=[bad_finding])])))

    def test_receipt_and_aggregate_finding_limits_are_closed(self):
        at_limit = document(receipts=[
            receipt(f"reviewer-{index}", findings=[
                {"severity": "low", "summary": f"Finding {number}"} for number in range(8)
            ]) for index in range(8)
        ])
        self.assertEqual(result_service.decode_review_results(encode(at_limit)), at_limit)
        self.assertEqual(len(result_service.normalize_review_results(at_limit, review_tier=2)["receipts"]), 8)
        too_many_findings = copy.deepcopy(at_limit)
        too_many_findings["receipts"][-1]["findings"].append({"severity": "low", "summary": "Finding 9"})
        for payload in (document(receipts=[]), document(receipts=[receipt(str(i)) for i in range(9)]), too_many_findings):
            self.assert_invalid(encode(payload))

    def test_normalization_reuses_text_provenance_matrix_and_per_receipt_duplicate_rules(self):
        valid = document(receipts=[receipt(" reviewer-a ", findings=[{"severity": "low", "summary": " Finding "}])])
        normalized = result_service.normalize_review_results(valid, review_tier=2)
        self.assertEqual(normalized["receipts"][0]["reviewer_key"], "reviewer-a")
        self.assertEqual(normalized["receipts"][0]["findings"][0]["summary"], "Finding")
        invalid = []
        for provenance in (None, {**PROVENANCE, "model_state": "declared"}, {**PROVENANCE, "review_profiles": ["general", "general"]}):
            candidate = document()
            candidate["receipts"][0]["provenance"] = provenance
            invalid.append(candidate)
        invalid.extend([
            document(receipts=[{**receipt(), "summary": "x" * 1001}]),
            document(receipts=[receipt(findings=[{"severity": "urgent", "summary": "Fix"}])]),
            document(receipts=[receipt(findings=[{"severity": "low", "summary": "Fix"}, {"severity": "low", "summary": " Fix "}])]),
        ])
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaises(review_service.ReviewEvidenceError) as raised:
                    result_service.normalize_review_results(candidate, review_tier=2)
                self.assertEqual(raised.exception.code, "invalid_review_evidence")
        duplicate = document(receipts=[receipt("reviewer-a"), receipt(" reviewer-a ")])
        with self.assertRaises(review_service.ReviewEvidenceError) as raised:
            result_service.normalize_review_results(duplicate, review_tier=2)
        self.assertEqual(raised.exception.code, "review_receipt_already_recorded")


class ReviewResultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.db = self.root / "taskgov.sqlite"
        initialize_taskgov_internal(repo=self.repo, db=self.db)
        self.target = database_target(self.db, self.repo)
        self.task_id = self.success(
            "task", "add", "--title", "Structured review results", "--status", "in_progress",
            "--review-tier", "2", "--contract-scope", "Register exact review evidence",
            "--contract-acceptance", "All records commit together",
        )["task"]["task_id"]
        self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)

    def success(self, *arguments):
        result = run_taskgov_internal(*arguments, "--repo", str(self.repo), "--db", str(self.db), "--json", maintenance_enabled=False)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        return json.loads(result.stdout)["data"]

    def payload(self, *, receipts=None):
        return document(self.task_id, receipts=receipts)

    def invoke(self, payload=None, *arguments, stdin=None, json_output=True, maintenance_enabled=False):
        supplied = stdin if stdin is not None else BinaryInput(encode(self.payload() if payload is None else payload))
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--repo", str(self.repo), "review", "result", "add", self.task_id, *arguments]
        if json_output:
            argv.append("--json")
        with mock.patch.object(sys, "stdin", supplied), redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_service.main(argv, _target_override=self.target, _maintenance_enabled=maintenance_enabled)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_failure(self, result, code, *, exit_code=1):
        actual_exit, stdout, stderr = result
        self.assertEqual(actual_exit, exit_code, stdout or stderr)
        envelope = json.loads(stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["command"], "review.result.add")
        self.assertEqual(envelope["data"], {"receipts": []})
        self.assertEqual(envelope["errors"][0]["code"], code)
        self.assertEqual(envelope["warnings"], [])
        self.assertEqual(stderr, "")
        return envelope

    def test_multiple_receipts_findings_keep_native_provenance_references_events_and_gates(self):
        findings = [{"severity": "low", "summary": "Shared observation 日本語"}]
        payload = self.payload(receipts=[receipt("reviewer-b", findings=findings), receipt("reviewer-a", findings=findings)])
        code, stdout, stderr = self.invoke(payload)
        self.assertEqual((code, stderr), (0, ""), stdout)
        result = json.loads(stdout)
        self.assertEqual(set(result), {"ok", "command", "project_id", "data", "warnings", "errors"})
        self.assertEqual(set(result["data"]), {"receipts"})
        rows = result["data"]["receipts"]
        self.assertEqual([row["receipt"]["reviewer_key"] for row in rows], ["reviewer-b", "reviewer-a"])
        receipt_ids, finding_ids, event_ids = [], [], []
        for row in rows:
            self.assertEqual(set(row), {"receipt", "event", "findings"})
            public = row["receipt"]
            self.assertEqual(set(public), set(review_service.PUBLIC_RECEIPT_FIELDS))
            self.assertEqual(public["task_id"], self.task_id)
            self.assertEqual(public["target_generation"], 1)
            self.assertEqual(public["summary"], payload["receipts"][0]["summary"])
            self.assertEqual(public["user_approved"], 0)
            provenance = public["review_provenance"]
            for key, value in PROVENANCE.items():
                self.assertEqual(provenance[key], value)
            self.assertEqual((provenance["provenance_version"], provenance["assurance_class"], provenance["producer_class"], provenance["producer_version"]), (1, "bound_attestation", "trusted_caller", 1))
            self.assertEqual(row["event"]["event_type"], "review_receipt_added")
            receipt_ids.append(public["review_receipt_id"])
            event_ids.append(row["event"]["task_event_id"])
            nested = row["findings"][0]
            self.assertEqual(set(nested), {"finding", "event"})
            self.assertEqual(nested["finding"]["review_receipt_id"], public["review_receipt_id"])
            self.assertEqual(nested["finding"]["summary"], findings[0]["summary"])
            self.assertEqual(nested["event"]["event_type"], "review_finding_added")
            finding_ids.append(nested["finding"]["review_finding_id"])
            event_ids.append(nested["event"]["task_event_id"])
        self.assertEqual(len(set(receipt_ids + finding_ids + event_ids)), 8)
        with closing(connect_initialized(self.target)) as connection:
            validate_evidence_ledger_storage(connection)
            references = [dict(row) for row in connection.execute("SELECT * FROM evidence_references WHERE source_kind IN ('review_receipt','review_finding')")]
            self.assertEqual({row["source_id"] for row in references}, set(receipt_ids + finding_ids))
            for reference in references:
                self.assertEqual((reference["task_id"], reference["contract_revision"], reference["target_generation"], reference["target_value"]), (self.task_id, 1, 1, FINGERPRINT))
                self.assertEqual((reference["assurance_class"], reference["producer_class"], reference["producer_version"]), ("bound_attestation", "trusted_caller", 1))
                self.assertIsNotNone(reference["acceptance_criterion_id"])
            stored_events = connection.execute("SELECT task_event_id FROM task_events WHERE event_type IN ('review_receipt_added','review_finding_added') ORDER BY rowid").fetchall()
            self.assertEqual([row[0] for row in stored_events], event_ids)
        evidence = self.success("task", "show", self.task_id)["review_evidence"]
        self.assertTrue(evidence["gate"]["satisfied"])
        self.assertEqual(evidence["gate"]["qualifying_independent_passes"], 2)
        self.assertEqual(evidence["counts"]["open_low"], 2)
        self.assertEqual({row["review_finding_id"] for row in evidence["current_findings"]}, set(finding_ids))

    def test_changes_requested_and_blocking_findings_remain_individual_gate_blockers(self):
        payload = self.payload(receipts=[
            receipt("reviewer-a", findings=[{"severity": "high", "summary": "Atomicity needs correction"}], verdict="changes_requested"),
            receipt("reviewer-b"),
        ])
        code, stdout, _ = self.invoke(payload)
        self.assertEqual(code, 0, stdout)
        evidence = self.success("task", "show", self.task_id)["review_evidence"]
        self.assertFalse(evidence["gate"]["satisfied"])
        self.assertEqual(evidence["counts"]["changes_requested_current_generation"], 1)
        self.assertEqual(evidence["counts"]["open_high"], 1)
        self.assertEqual(evidence["current_findings"][0]["blocking_reason"], "unresolved")

    def test_invalid_second_entry_is_rejected_before_writer_and_without_file_change(self):
        candidate = self.payload(receipts=[receipt("reviewer-a"), receipt("reviewer-b")])
        candidate["receipts"][1]["provenance"]["model_state"] = "declared"
        before = file_snapshot(self.root)
        with mock.patch.object(result_service, "lock_and_reread_target_owner", side_effect=AssertionError("must normalize all entries before writer")):
            self.assert_failure(self.invoke(candidate), "invalid_review_evidence")
        self.assertEqual(file_snapshot(self.root), before)

    def test_late_storage_exception_rolls_back_every_row_reference_event_and_timestamp(self):
        payload = self.payload(receipts=[receipt(f"reviewer-{i}", findings=[{"severity": "low", "summary": "Observation"}]) for i in range(2)])
        before = file_snapshot(self.root)
        create_event = review_service.create_task_event
        calls = []
        rows_before_failure = []

        def fail_last_event(connection, **kwargs):
            calls.append(kwargs["event_type"])
            if len(calls) == 4:
                rows_before_failure.append((
                    connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0],
                    connection.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0],
                ))
                raise sqlite3.OperationalError("private storage fault token=never-echo")
            return create_event(connection, **kwargs)

        with mock.patch.object(review_service, "create_task_event", side_effect=fail_last_event), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            result = self.invoke(payload, maintenance_enabled=True)
        self.assert_failure(result, "internal_error", exit_code=2)
        self.assertEqual(calls, ["review_receipt_added", "review_finding_added"] * 2)
        self.assertEqual(rows_before_failure, [(2, 2)])
        self.assertNotIn("private storage fault", result[1] + result[2])
        maintenance.assert_not_called()
        self.assertEqual(file_snapshot(self.root), before)

    def test_each_submitted_task_contract_and_complete_target_component_must_match(self):
        changes = [
            (("task_id",), "tg_task_ffffffffffffffff"), (("contract_revision",), 0),
            (("review_target", "kind"), "external_revision"),
            (("review_target", "value"), "sha256:" + "b" * 64),
            (("review_target", "base_revision"), "b" * 40),
            (("review_target", "generation"), 2),
        ]
        before = file_snapshot(self.root)
        for path, value in changes:
            candidate = self.payload()
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(component=path):
                self.assert_failure(self.invoke(candidate), "review_target_mismatch")
                self.assertEqual(file_snapshot(self.root), before)

    def test_committed_target_and_contract_changes_do_not_rebind_old_results(self):
        original = self.payload()
        self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(original), "review_target_mismatch")
        self.assertEqual(file_snapshot(self.root), before)
        current = copy.deepcopy(original)
        current["review_target"]["generation"] = 2
        self.success(
            "task", "edit", self.task_id, "--contract-scope", "Updated bounded scope",
            "--contract-acceptance", "All records commit together",
            "--contract-authority-ref", f"user_instruction:{self.task_id}:2",
            "--contract-change-reason", "User requested updated scope",
        )
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(current), "review_target_required")
        self.assertEqual(file_snapshot(self.root), before)
        recaptured = self.success(
            "review", "target", "set", self.task_id, "--kind", "diff_fingerprint",
            "--revision", FINGERPRINT,
        )["task"]
        current["review_target"]["generation"] = recaptured["review_target_generation"]
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(current), "review_target_mismatch")
        self.assertEqual(file_snapshot(self.root), before)

    def test_target_change_between_preflight_and_writer_lock_is_rejected(self):
        lock = result_service.lock_and_reread_target_owner
        after_concurrent_change = []

        def advance_then_lock(connection, project, task_id, **kwargs):
            self.assertFalse(connection.in_transaction)
            with closing(connect_initialized(self.target)) as writer, writer:
                review_service.set_review_target(writer, project, task_id, kind="diff_fingerprint", revision=FINGERPRINT, database_target=self.target)
            after_concurrent_change.append(file_snapshot(self.root))
            return lock(connection, project, task_id, **kwargs)

        with mock.patch.object(result_service, "lock_and_reread_target_owner", side_effect=advance_then_lock):
            self.assert_failure(self.invoke(), "review_target_mismatch")
        self.assertEqual(len(after_concurrent_change), 1)
        self.assertEqual(file_snapshot(self.root), after_concurrent_change[0])
        self.assertEqual(self.success("task", "show", self.task_id)["task"]["review_target_generation"], 2)

    def test_concurrent_tier_change_cannot_reuse_prelock_fallback_normalization(self):
        self.task_id = self.success(
            "task", "add", "--title", "Tier-sensitive fallback", "--review-tier", "1",
        )["task"]["task_id"]
        self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        candidate = document(self.task_id, revision=0, receipts=[receipt(kind="self_review_fallback")])
        lock = result_service.lock_and_reread_target_owner
        concurrent_snapshot = []

        def raise_tier_then_lock(connection, project, task_id, **kwargs):
            self.assertFalse(connection.in_transaction)
            with closing(connect_initialized(self.target)) as writer, writer:
                task_service.edit_task(writer, project, task_id, review_tier=2, database_target=self.target)
            concurrent_snapshot.append(file_snapshot(self.root))
            return lock(connection, project, task_id, **kwargs)

        with mock.patch.object(result_service, "lock_and_reread_target_owner", side_effect=raise_tier_then_lock):
            self.assert_failure(self.invoke(candidate), "review_target_mismatch")
        self.assertEqual(len(concurrent_snapshot), 1)
        self.assertEqual(file_snapshot(self.root), concurrent_snapshot[0])
        self.assertEqual(self.success("task", "show", self.task_id)["task"]["review_tier"], 2)

    def test_missing_target_and_done_task_retain_single_receipt_errors(self):
        original_task_id = self.task_id
        self.task_id = self.success("task", "add", "--title", "No target", "--review-tier", "2")["task"]["task_id"]
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(document(self.task_id, revision=0)), "review_target_required")
        self.assertEqual(file_snapshot(self.root), before)
        self.task_id = original_task_id
        code, stdout, _ = self.invoke(self.payload(receipts=[receipt("reviewer-a"), receipt("reviewer-b")]))
        self.assertEqual(code, 0, stdout)
        self.success(
            "task", "edit", self.task_id, "--status", "done", "--verification-complete",
            "--review-complete", "--commit-not-required",
        )
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(self.payload(receipts=[receipt("reviewer-c")])), "done_task_requires_reopen")
        self.assertEqual(file_snapshot(self.root), before)

    def test_capture_zero_basis_rejects_before_any_new_evidence(self):
        # Match the existing single-Receipt legacy-boundary fixture, keeping
        # the synthetic capture-zero row inside a rolled-back transaction.
        before = file_snapshot(self.root)
        with closing(connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "UPDATE tasks SET review_target_capture_version=0, "
                    "review_target_authority_snapshot_id=NULL, "
                    "review_target_acceptance_criterion_id=NULL, "
                    "review_target_verification_criterion_id=NULL, "
                    "review_target_artifact_manifest_id=NULL WHERE task_id=?",
                    (self.task_id,),
                )
                with self.assertRaises(review_service.ReviewEvidenceError) as raised:
                    result_service.add_review_results(
                        connection, self.target.project, self.task_id, self.payload(),
                    )
                self.assertEqual(raised.exception.code, "evidence_basis_stale")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0], 0)
            finally:
                connection.rollback()
        self.assertEqual(file_snapshot(self.root), before)

    def test_duplicate_batch_reviewer_existing_single_receipt_and_replay_are_atomic(self):
        duplicate = self.payload(receipts=[receipt("reviewer-a"), receipt(" reviewer-a ")])
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(duplicate), "review_receipt_already_recorded")
        self.assertEqual(file_snapshot(self.root), before)
        existing = receipt_add(self.db, self.repo, self.task_id, "reviewer-b")
        self.assertEqual(existing.returncode, 0, existing.stdout)
        before = file_snapshot(self.root)
        partly_new = self.payload(receipts=[receipt("reviewer-a"), receipt(" reviewer-b ")])
        self.assert_failure(self.invoke(partly_new), "review_receipt_already_recorded")
        self.assertEqual(file_snapshot(self.root), before)
        accepted = self.payload(receipts=[receipt("reviewer-a"), receipt("reviewer-c")])
        self.assertEqual(self.invoke(accepted)[0], 0)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(accepted), "review_receipt_already_recorded")
        reverse = receipt_add(self.db, self.repo, self.task_id, "reviewer-a")
        self.assertEqual(json.loads(reverse.stdout)["errors"][0]["code"], "review_receipt_already_recorded")
        self.assertEqual(file_snapshot(self.root), before)

    def test_existing_single_finding_writer_accepts_new_batch_receipt(self):
        code, stdout, _ = self.invoke()
        self.assertEqual(code, 0, stdout)
        receipt_id = json.loads(stdout)["data"]["receipts"][0]["receipt"]["review_receipt_id"]
        finding = self.success("review", "finding", "add", self.task_id, "--receipt-id", receipt_id, "--severity", "medium", "--summary", "Follow-up correction")["finding"]
        self.assertEqual(finding["review_receipt_id"], receipt_id)
        self.assertFalse(self.success("task", "show", self.task_id)["review_evidence"]["gate"]["satisfied"])

    def test_external_approval_is_required_matched_distinct_and_fallback_only(self):
        fallback = self.payload(receipts=[receipt("self-review", kind="self_review_fallback")])
        before = file_snapshot(self.root)
        for candidate, flags in (
            (fallback, ()),
            (fallback, ("--user-approved-reviewer", "other")),
            (fallback, ("--user-approved-reviewer", "self-review", "--user-approved-reviewer", " self-review ")),
            (self.payload(), ("--user-approved-reviewer", "reviewer-a")),
            ({**fallback, "user_approved": True}, ()),
        ):
            with self.subTest(flags=flags):
                self.assert_failure(self.invoke(candidate, *flags), "invalid_review_evidence")
                self.assertEqual(file_snapshot(self.root), before)
        code, stdout, _ = self.invoke(fallback, "--user-approved-reviewer", " self-review ")
        self.assertEqual(code, 0, stdout)
        self.assertEqual(json.loads(stdout)["data"]["receipts"][0]["receipt"]["user_approved"], 1)
        self.assertTrue(self.success("task", "show", self.task_id)["review_evidence"]["gate"]["satisfied"])

    def test_tier_zero_null_provenance_and_tier_one_fallback_keep_existing_meanings(self):
        for tier in (0, 1):
            with self.subTest(tier=tier):
                self.task_id = self.success("task", "add", "--title", f"Tier {tier} result", "--review-tier", str(tier))["task"]["task_id"]
                self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
                kind = "not_required" if tier == 0 else "self_review_fallback"
                verdict = "not_required" if tier == 0 else "pass"
                candidate = document(self.task_id, revision=0, receipts=[receipt(kind=kind, verdict=verdict)])
                code, stdout, _ = self.invoke(candidate)
                self.assertEqual(code, 0, stdout)
                public = json.loads(stdout)["data"]["receipts"][0]["receipt"]
                self.assertEqual(public["user_approved"], 0)
                self.assertEqual(public["review_provenance"] is None, tier == 0)
                self.assertTrue(self.success("task", "show", self.task_id)["review_evidence"]["gate"]["satisfied"])

    def test_privacy_precedes_semantic_error_and_never_echoes_or_persists_input(self):
        candidate = self.payload(receipts=[{**receipt("reviewer-a"), "kind": "unsupported"}, receipt("reviewer-b")])
        candidate["receipts"][1]["findings"] = [{"severity": "low", "summary": "token=private-result"}]
        before = file_snapshot(self.root)
        result = self.invoke(candidate)
        self.assert_failure(result, "privacy_rejected")
        self.assertNotIn("private-result", result[1] + result[2])
        self.assertEqual(file_snapshot(self.root), before)

    def test_read_only_and_parse_rejection_never_consume_stdin_or_open_writer(self):
        before = file_snapshot(self.root)
        with mock.patch.object(cli_service, "connect_initialized", side_effect=AssertionError("must not open writer")):
            self.assert_failure(self.invoke(None, "--read-only", stdin=BinaryInput(readable=False)), "invalid_argument")
            code, stdout, stderr = self.invoke(None, "--user-approved", stdin=BinaryInput(readable=False))
            self.assertEqual((code, stderr), (1, ""))
            rejected = json.loads(stdout)
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["command"], "parse")
            self.assertEqual(rejected["errors"][0]["code"], "invalid_argument")
        self.assertEqual(file_snapshot(self.root), before)

    def test_malformed_or_oversized_stdin_is_bounded_and_rejected_before_connection(self):
        before = file_snapshot(self.root)
        for raw in (b"{", b" " * 262145, b"\xff"):
            stream = BinaryInput(raw)
            with self.subTest(raw_length=len(raw)), mock.patch.object(cli_service, "connect_initialized", side_effect=AssertionError("decode must precede connection")):
                self.assert_failure(self.invoke(stdin=stream), "invalid_review_evidence")
                self.assertEqual(stream.read_sizes, [262145])
                self.assertEqual(file_snapshot(self.root), before)
        self.assert_failure(self.invoke(stdin=io.StringIO("{}")), "invalid_review_evidence")

    def test_text_reports_only_counts_and_error_is_sanitized(self):
        candidate = self.payload(receipts=[receipt("reviewer-a", findings=[{"severity": "low", "summary": "Private work description"}]), receipt("reviewer-b")])
        code, stdout, stderr = self.invoke(candidate, json_output=False)
        self.assertEqual((code, stdout, stderr), (0, "Review results recorded: 2 receipts, 1 findings\n", ""))
        code, stdout, stderr = self.invoke(stdin=BinaryInput(b"token=private-result"), json_output=False)
        self.assertEqual(code, 1)
        self.assertNotIn("private-result", stdout + stderr)
        self.assertEqual((stdout, stderr), ("", "review result input is invalid\n"))

    def test_maintenance_runs_once_after_commit_and_close_and_failure_is_warning_only(self):
        opened = []
        maintenance_observations = []
        connect = cli_service.connect_initialized

        def observe_connection(*args, **kwargs):
            connection = connect(*args, **kwargs)
            opened.append(connection)
            return connection

        def maintenance_after_close(target, outcome):
            closed = False
            try:
                opened[0].execute("SELECT 1")
            except sqlite3.ProgrammingError:
                closed = True
            with closing(sqlite3.connect(self.db)) as reader:
                counts = (
                    reader.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0],
                    reader.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0],
                )
            maintenance_observations.append((target, outcome, len(opened), closed, counts))
            raise OSError("private maintenance detail token=never-echo")

        candidate = self.payload(receipts=[receipt(f"reviewer-{i}", findings=[{"severity": "low", "summary": "Observation"}]) for i in range(2)])
        with mock.patch.object(cli_service, "connect_initialized", side_effect=observe_connection), mock.patch.object(cli_service, "run_post_commit_maintenance", side_effect=maintenance_after_close) as maintenance:
            code, stdout, stderr = self.invoke(candidate, maintenance_enabled=True)
        self.assertEqual((code, stderr), (0, ""), stdout)
        maintenance.assert_called_once()
        self.assertEqual(maintenance_observations, [(
            self.target, MutationOutcome(state_changed=True, viewer_relevant=True), 1, True, (2, 2),
        )])
        envelope = json.loads(stdout)
        self.assertTrue(envelope["ok"])
        self.assertEqual(len(envelope["data"]["receipts"]), 2)
        self.assertEqual([item["code"] for item in envelope["warnings"]], ["evidence_projection_failed", "viewer_refresh_failed", "backup_failed"])
        self.assertNotIn("private maintenance detail", stdout)

    def test_physical_public_cli_consumes_utf8_stdin_without_target_or_config_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve())
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stdout or setup.stderr)
            added = install.run("task", "add", "--title", "Physical structured results", "--review-tier", "2", "--json")
            self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            target = install.run("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT, "--json")
            self.assertEqual(target.returncode, 0, target.stdout or target.stderr)
            before = file_snapshot(install.project_root, exclude_state=True)
            payload = document(task_id, revision=0, receipts=[receipt("reviewer-a"), receipt("reviewer-b")])
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(install.entrypoint), "review", "result", "add", task_id,
                 "--repo", str(install.project_root), "--json"],
                cwd=install.project_root, input=encode(payload), capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8") or result.stderr.decode("utf-8"))
            self.assertEqual(result.stderr, b"")
            result_data = json.loads(result.stdout)["data"]
            self.assertEqual([row["receipt"]["summary"] for row in result_data["receipts"]], [item["summary"] for item in payload["receipts"]])
            self.assertEqual(file_snapshot(install.project_root, exclude_state=True), before)
            self.assertFalse((install.skill_root / "config" / "verification-runner.json").exists())


if __name__ == "__main__":
    unittest.main()
