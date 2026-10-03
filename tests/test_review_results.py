from __future__ import annotations

import copy
import io
import json
import os
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
from tests.review_test_helpers import REVIEW_PROVENANCE_V1_CASES

from task_governance_tool import cli as cli_service
from task_governance_tool import review_results as result_service
from task_governance_tool import reviews as review_service
from task_governance_tool import tasks as task_service
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.storage import connect_initialized, validate_evidence_ledger_storage
from task_governance_tool.session_identity import capture_caller_identity


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
    def test_known_input_diagnostics_use_schema_paths_and_fixed_reasons(self):
        cases = [
            (lambda p: p.pop("contract_revision"), "contract_revision", "missing"),
            (lambda p: p.update(contract_revision=True), "contract_revision", "integer"),
            (lambda p: p["review_target"].update(kind="invalid-target-value"), "review_target.kind", "choice"),
            (lambda p: p["receipts"][0].pop("findings"), "receipts[].findings", "missing"),
            (lambda p: p["receipts"][0].update(summary=12), "receipts[].summary", "string"),
            (lambda p: p["receipts"][0].update(summary="x" * 1001), "receipts[].summary", "value"),
            (lambda p: p["receipts"][0].update(provenance=None), "receipts[].provenance", "provenance"),
            (lambda p: p["receipts"][0]["provenance"].pop("declared_model_id"), "receipts[].provenance.declared_model_id", "missing"),
            (lambda p: p["receipts"][0]["provenance"].update(model_state="invalid-model-value"), "receipts[].provenance.model_state", "choice"),
            (lambda p: p["receipts"][0]["provenance"].update(model_state="declared"), "receipts[].provenance", "matrix"),
            (lambda p: p["receipts"][0]["provenance"].update(declared_model_id="bad identifier"), "receipts[].provenance.declared_model_id", "identifier"),
            (lambda p: p["receipts"][0]["provenance"].update(method_codes=["diff_inspection"] * 2), "receipts[].provenance.method_codes", "duplicate"),
            (lambda p: p["receipts"][0].update(findings=[{"severity": "low", "summary": "Same"}] * 2), "receipts[].findings", "duplicate"),
            (lambda p: p["receipts"][0].update(findings=[{"severity": "urgent", "summary": "Fix"}]), "receipts[].findings[].severity", "choice"),
        ]
        for key in ("review_profiles", "review_lenses", "method_codes"):
            for value, reason in (([False], "string"), (["invalid-code"], "choice")):
                cases.append((lambda p, key=key, value=value: p["receipts"][0]["provenance"].update({key: value}),
                              "receipts[].provenance." + key + "[]", reason))
        for mutate, field, reason in cases:
            with self.subTest(field=field, reason=reason):
                candidate = document()
                mutate(candidate)
                first = {**candidate, "receipts": [receipt("first")]}
                for originals in (candidate, [first, candidate]):
                    with self.assertRaises(result_service.ReviewResultInputError) as caught:
                        decoded = result_service.decode_review_results(encode(originals))
                        result_service.normalize_review_results(decoded, review_tier=2)
                    self.assertEqual(caught.exception.field, field)
                    self.assertEqual(caught.exception.message, field + ": " + result_service._DIAGNOSTIC_REASONS[reason])
                    self.assertNotIn("invalid-model-value", caught.exception.message)
                    self.assertNotIn("bad identifier", caught.exception.message)

    def test_ambiguous_structure_retains_general_error_without_unknown_keys(self):
        unknown = document()
        unknown["private-unknown-key"] = "private-unknown-value"
        raw = encode(document())
        for candidate in (b"{", raw.replace(b'"version":1', b'"version":1,"version":1'),
                          encode(unknown), raw + b" " * 262145):
            with self.assertRaises(review_service.ReviewEvidenceError) as caught:
                result_service.decode_review_results(candidate)
            self.assertNotIsInstance(caught.exception, result_service.ReviewResultInputError)
            self.assertIsNone(caught.exception.field)
            self.assertEqual(caught.exception.message, "review result input is invalid")

    def test_approval_flag_error_does_not_point_to_a_result_reviewer(self):
        with self.assertRaises(result_service.ReviewResultInputError) as caught:
            result_service.normalize_review_results(document(), review_tier=2, user_approved_reviewers=[""])
        self.assertEqual(caught.exception.field, "--user-approved-reviewer")

    def test_ineligible_approval_keeps_valid_receipt_kind_and_existing_validation_order(self):
        for tier, kind, verdict in ((2, " independent ", "pass"), (0, "not_required", "not_required")):
            candidate = document(receipts=[receipt("reviewer-a", kind=kind, verdict=verdict)])
            for originals in (candidate, [candidate]):
                with self.subTest(tier=tier, array=isinstance(originals, list)):
                    decoded = result_service.decode_review_results(encode(originals))
                    self.assertEqual(result_service.normalize_review_results(decoded, review_tier=tier)
                                     ["receipts"][0]["receipt_kind"], kind.strip())
                    with self.assertRaises(result_service.ReviewResultInputError) as caught:
                        result_service.normalize_review_results(decoded, review_tier=tier,
                                                                user_approved_reviewers=[" reviewer-a "])
                    self.assertEqual(caught.exception.field, "--user-approved-reviewer")
                    self.assertIn("approval flags", caught.exception.message)
            candidate["receipts"][0]["verdict"] = "invalid-verdict"
            with self.assertRaises(result_service.ReviewResultInputError) as caught:
                result_service.normalize_review_results(candidate, review_tier=tier,
                                                        user_approved_reviewers=["reviewer-a"])
            self.assertEqual(caught.exception.field, "receipts[].verdict")

    def test_receipt_correlation_names_group_not_an_unproven_scalar_cause(self):
        for tier, kind, verdict, summary in (
            (0, "not_required", "not_required", ""),
            (2, "independent", "not_required", "Assessment"),
            (0, "self_review_fallback", "pass", "Assessment"),
        ):
            candidate = document(receipts=[receipt(kind=kind, verdict=verdict)])
            candidate["receipts"][0]["summary"] = summary
            for originals in (candidate, [candidate]):
                with self.assertRaises(result_service.ReviewResultInputError) as caught:
                    decoded = result_service.decode_review_results(encode(originals))
                    result_service.normalize_review_results(decoded, review_tier=tier)
                self.assertEqual(caught.exception.field, "receipts[]")
                self.assertIn("relationship", caught.exception.message)

    def assert_invalid(self, payload):
        with self.assertRaises(review_service.ReviewEvidenceError) as raised:
            result_service.decode_review_results(payload)
        self.assertEqual(raised.exception.code, "invalid_review_evidence")

    def test_completed_template_uses_existing_provenance_and_receipt_matrix(self):
        for case in REVIEW_PROVENANCE_V1_CASES:
            with self.subTest(case=case.name):
                template = result_service.review_result_template(TASK_ID, 1, document()["review_target"])
                entry = template["receipts"][0]
                entry.update(receipt())
                entry["provenance"] = case.expected_normalized()
                result = result_service.normalize_review_results(template, review_tier=2)
                self.assertEqual(result["receipts"][0]["provenance"], case.expected_normalized())
        for tier, kind, verdict, approved in (
            (0, "not_required", "not_required", ()),
            (1, "self_review_fallback", "pass", ()),
            (2, "self_review_fallback", "pass", ("reviewer-a",)),
            (2, "self_review_fallback", "changes_requested", ()),
            (2, "independent", "changes_requested", ()),
        ):
            with self.subTest(tier=tier, kind=kind, verdict=verdict):
                template = result_service.review_result_template(TASK_ID, 0, document()["review_target"])
                template["receipts"][0].update(receipt(kind=kind, verdict=verdict))
                result = result_service.normalize_review_results(template, review_tier=tier, user_approved_reviewers=approved)
                self.assertEqual(result["receipts"][0]["verdict"], verdict)

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
    def test_documented_direct_stdin_pipeline_preserves_utf8_review_results(self):
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("Windows PowerShell 5.1 is unavailable")
        version = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            capture_output=True, check=True, timeout=30,
        )
        if not version.stdout.strip().startswith(b"5.1."):
            self.skipTest("This regression requires Windows PowerShell 5.1")

        readme = (Path(__file__).resolve().parents[1] / "task-governance-tool/references/cli_contracts.md").read_text(encoding="utf-8")
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
        encoding_lines = [
            index for index, line in enumerate(lines)
            if line.lstrip().startswith("$OutputEncoding =")
        ]
        self.assertEqual((len(result_lines), len(encoding_lines)), (1, 1))
        self.assertLess(encoding_lines[0], result_lines[0])
        # Execute the documented transport unchanged, including its encoding
        # setup. Substitute only the native consumer so no Task or Git command
        # runs; ASCII hex reveals the exact stdin bytes without stdout recoding.
        python_path = sys.executable.replace("'", "''")
        consumer = f"& '{python_path}' -I -S -c 'import sys; print(sys.stdin.buffer.read().hex())'"
        pipeline = "\n".join(lines[encoding_lines[0]:result_lines[0]] + [consumer])
        payload = document(receipts=[receipt(findings=[{"severity": "low", "summary": "所見も保持する 🔍"}])])
        raw = encode(payload)
        with tempfile.TemporaryDirectory() as temporary:
            second = document(receipts=[receipt("reviewer-b")])
            raw_second = encode(second)
            (Path(temporary) / "review-a.json").write_bytes(raw)
            (Path(temporary) / "review-b.json").write_bytes(raw_second)
            framed = b"[" + raw + b"," + raw_second + b"]"
            # PowerShell 5.1 can reuse Console.InputEncoding for native stdin
            # when its code page matches OutputEncoding, including its BOM.
            for encoding_name, input_encoding in (
                ("cp437", "[System.Text.Encoding]::GetEncoding(437)"),
                ("utf8_with_bom", "[System.Text.Encoding]::UTF8"),
            ):
                with self.subTest(input_encoding=encoding_name):
                    controlled_pipeline = "\n".join([
                        "$originalInputEncoding = [Console]::InputEncoding",
                        "try {",
                        f"[Console]::InputEncoding = {input_encoding}",
                        pipeline,
                        "} finally {",
                        "[Console]::InputEncoding = $originalInputEncoding",
                        "}",
                    ])
                    transported = subprocess.run(
                        [powershell, "-NoProfile", "-NonInteractive", "-Command", controlled_pipeline],
                        cwd=temporary, stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=30,
                    )
                    self.assertEqual(transported.returncode, 0, transported.stderr)
                    self.assertEqual(transported.stderr, b"")
                    received = bytes.fromhex(transported.stdout.decode("ascii").strip())
                    self.assertEqual(received.rstrip(b"\r\n"), framed)
                    self.assertEqual(json.loads(received.decode("utf-8")), [payload, second])

            # Every named original must exist and contain data before the native
            # consumer starts. In particular, Get-Content silently emits no
            # object for an empty file; joining its multi-path output can drop it.
            for filename in ("review-a.json", "review-b.json"):
                for invalid_original in (b"", b" \t\r\n", None):
                    with self.subTest(filename=filename, original=invalid_original):
                        (Path(temporary) / "review-a.json").write_bytes(raw)
                        (Path(temporary) / "review-b.json").write_bytes(raw_second)
                        selected = Path(temporary) / filename
                        if invalid_original is None:
                            selected.unlink()
                        else:
                            selected.write_bytes(invalid_original)
                        rejected = subprocess.run(
                            [powershell, "-NoProfile", "-NonInteractive", "-Command", pipeline],
                            cwd=temporary, stdin=subprocess.DEVNULL, capture_output=True,
                            check=False, timeout=30,
                        )
                        self.assertNotEqual(rejected.returncode, 0)
                        self.assertNotEqual(rejected.stderr, b"")
                        self.assertEqual(rejected.stdout, b"")

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
    document_array = False

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
            "--verification-not-required", "Review-result fixture",
        )["task"]["task_id"]
        self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)

    def success(self, *arguments):
        result = run_taskgov_internal(*arguments, "--repo", str(self.repo), "--db", str(self.db), "--json", maintenance_enabled=False)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        return json.loads(result.stdout)["data"]

    def payload(self, *, receipts=None):
        return document(self.task_id, receipts=receipts)

    def add_review_task(self, *arguments):
        self.success("task", "edit", self.task_id, "--status", "review_pending")
        return self.success("task", "add", *arguments, "--status", "in_progress")["task"]["task_id"]

    def wire_payload(self, payload):
        if self.document_array and type(payload) is dict and payload.get("receipts"):
            return [{**payload, "receipts": [entry]} for entry in payload["receipts"]]
        return payload

    def invoke(self, payload=None, *arguments, stdin=None, json_output=True, maintenance_enabled=False):
        supplied = stdin if stdin is not None else BinaryInput(encode(self.wire_payload(self.payload() if payload is None else payload)))
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

    def assert_gate(self, data, blocking_code, *, generation=1):
        gate = data["review_gate"]
        self.assertEqual(gate["blocking_code"], blocking_code)
        self.assertEqual(gate["satisfied"], blocking_code is None)
        self.assertEqual(gate["basis"], {
            "task_id": self.task_id, "contract_revision": 1,
            "review_target": self.payload()["review_target"] | {"generation": generation},
        })
        with closing(connect_initialized(self.target)) as connection:
            evidence = review_service.read_review_evidence(connection, self.target.project.project_id, self.task_id)
            self.assertEqual({key: gate[key] for key in evidence["gate"]}, evidence["gate"])
            blocker = review_service.first_review_gate_error(evidence)
            self.assertEqual(blocker.code if blocker else None, blocking_code)
        shown = self.success("task", "show", self.task_id)["review_evidence"]["gate"]
        self.assertEqual({key: gate[key] for key in shown}, shown)
        checked = self.success("task", "complete", self.task_id, "--verification-complete", "--review-complete", "--commit-not-required", "--check", "--read-only")
        self.assertEqual(checked["blocking_codes"], [] if blocking_code is None else [blocking_code])

    def test_gate_is_observed_once_inside_writer_without_git(self):
        reader = result_service.read_review_evidence
        observations = []
        def observed(connection, *args, **kwargs):
            observations.append(connection.in_transaction)
            return reader(connection, *args, **kwargs)
        with mock.patch.object(result_service, "read_review_evidence", side_effect=observed) as read, mock.patch("subprocess.run", side_effect=AssertionError("no process under writer")):
            code, stdout, _ = self.invoke()
        self.assertEqual(code, 0, stdout)
        read.assert_called_once()
        self.assertEqual(observations, [True])
        self.assert_gate(json.loads(stdout)["data"], "review_receipts_insufficient")

    def test_old_unresolved_finding_blocks_new_generation_passes(self):
        code, stdout, _ = self.invoke(self.payload(receipts=[receipt(findings=[{"severity": "medium", "summary": "Correct the boundary"}])]))
        self.assertEqual(code, 0, stdout)
        finding_id = json.loads(stdout)["data"]["receipts"][0]["findings"][0]["finding"]["review_finding_id"]
        self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        code, stdout, _ = self.invoke(document(self.task_id, generation=2, receipts=[receipt("new-a"), receipt("new-b")]))
        self.assertEqual(code, 0, stdout)
        self.assert_gate(json.loads(stdout)["data"], "review_finding_unresolved", generation=2)
        self.success("review", "finding", "resolve", finding_id, "--resolution", "Boundary corrected")
        code, stdout, _ = self.invoke(document(self.task_id, generation=2, receipts=[receipt("new-c")]))
        self.assertEqual(code, 0, stdout)
        self.assert_gate(json.loads(stdout)["data"], None, generation=2)

    def test_resolved_current_finding_requires_new_generation_and_fresh_passes(self):
        code, stdout, _ = self.invoke(self.payload(receipts=[receipt(findings=[{"severity": "high", "summary": "Correct current boundary"}])]))
        self.assertEqual(code, 0, stdout)
        finding_id = json.loads(stdout)["data"]["receipts"][0]["findings"][0]["finding"]["review_finding_id"]
        self.success("review", "finding", "resolve", finding_id, "--resolution", "Boundary corrected")
        code, stdout, _ = self.invoke(self.payload(receipts=[receipt("new-a"), receipt("new-b")]))
        self.assertEqual(code, 0, stdout)
        self.assert_gate(json.loads(stdout)["data"], "review_finding_unresolved")
        self.assertEqual(self.success("task", "show", self.task_id)["review_evidence"]["current_findings"][0]["blocking_reason"], "fresh_review_required")
        self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        code, stdout, _ = self.invoke(document(self.task_id, generation=2, receipts=[receipt("fresh-a")]))
        self.assertEqual(code, 0, stdout)
        self.assert_gate(json.loads(stdout)["data"], "review_receipts_insufficient", generation=2)
        code, stdout, _ = self.invoke(document(self.task_id, generation=2, receipts=[receipt("fresh-b")]))
        self.assertEqual(code, 0, stdout)
        self.assert_gate(json.loads(stdout)["data"], None, generation=2)

    def test_changes_requested_without_findings_blocks_even_with_two_passes(self):
        code, stdout, _ = self.invoke(self.payload(receipts=[receipt("requester", verdict="changes_requested"), receipt("pass-a"), receipt("pass-b")]))
        self.assertEqual(code, 0, stdout)
        self.assert_gate(json.loads(stdout)["data"], "review_changes_requested")

    def test_gate_read_failure_before_commit_rolls_back_all_rows(self):
        before = file_snapshot(self.root)
        with mock.patch.object(result_service, "read_review_evidence", side_effect=sqlite3.OperationalError("token=private-fault")), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            result = self.invoke(maintenance_enabled=True)
        self.assert_failure(result, "internal_error", exit_code=2)
        self.assertNotIn("private-fault", result[1] + result[2])
        self.assertEqual(file_snapshot(self.root), before)
        maintenance.assert_not_called()

    def test_post_commit_text_failure_keeps_saved_json_and_warns_without_replay(self):
        with mock.patch.object(cli_service, "review_text", side_effect=ValueError("token=private-format")):
            code, stdout, stderr = self.invoke()
        self.assertEqual((code, stderr), (0, ""), stdout)
        envelope = json.loads(stdout)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["warnings"][0]["code"], "review_result_display_failed")
        self.assertIn("Do not resubmit", envelope["warnings"][0]["message"])
        self.assertNotIn("private-format", stdout)
        self.assert_gate(envelope["data"], "review_receipts_insufficient")

    def test_post_commit_emit_failure_reports_saved_outcome_not_empty_failure(self):
        for json_output in (False, True):
            with self.subTest(json_output=json_output), mock.patch.object(cli_service, "emit_result", side_effect=OSError("token=private-output")) as emit:
                code, stdout, stderr = self.invoke(self.payload(receipts=[receipt(str(json_output))]), json_output=json_output)
            self.assertEqual((code, stdout), (2, ""))
            self.assertIn("Review results recorded, but output failed", stderr)
            self.assertIn("Do not resubmit", stderr)
            self.assertNotIn("private-output", stderr)
            emit.assert_called_once()
        self.assertEqual(self.success("task", "show", self.task_id)["review_evidence"]["counts"]["receipts_current_generation"], 2)

    def test_compact_response_preserves_all_64_findings_and_reduces_wire_bytes(self):
        legacy = []
        add_receipt, add_finding = result_service.add_review_receipt, result_service.add_review_finding
        def capture_receipt(*args, **kwargs):
            saved = add_receipt(*args, **kwargs)
            legacy.append({"receipt": saved.receipt, "event": saved.event, "findings": []})
            return saved
        def capture_finding(*args, **kwargs):
            saved = add_finding(*args, **kwargs)
            legacy[-1]["findings"].append({"finding": saved.finding, "event": saved.event})
            return saved
        payload = self.payload(receipts=[receipt(str(i), findings=[{"severity": "low", "summary": f"src/a.py:{j} 日本語 detail {i}"} for j in range(8)]) for i in range(8)])
        with mock.patch.object(result_service, "add_review_receipt", side_effect=capture_receipt), mock.patch.object(result_service, "add_review_finding", side_effect=capture_finding):
            code, stdout, _ = self.invoke(payload)
        self.assertEqual(code, 0, stdout)
        envelope = json.loads(stdout)
        rows = envelope["data"]["receipts"]
        self.assertEqual(len(rows), 8)
        for original, saved, old in zip(payload["receipts"], rows, legacy):
            self.assertEqual(saved["receipt"]["review_receipt_id"], old["receipt"]["review_receipt_id"])
            self.assertEqual([row["finding"]["summary"] for row in saved["findings"]], [row["summary"] for row in original["findings"]])
            self.assertEqual([row["finding"]["review_finding_id"] for row in saved["findings"]], [row["finding"]["review_finding_id"] for row in old["findings"]])
        old_envelope = {**envelope, "data": {"receipts": legacy}}
        old_bytes, new_bytes = len(encode(old_envelope)), len(encode(envelope))
        self.assertLess(new_bytes, old_bytes)
        print(f"RG3_RESPONSE_BYTES old={old_bytes} new={new_bytes} receipts=8 findings=64")
        self.assertEqual(envelope["data"]["omitted_details"], ["provenance", "repeated_binding", "events", "timestamps", "resolution_metadata"])

    def test_buffered_pipe_flush_failure_retains_rows_and_quiets_later_finalization(self):
        for json_output in (False, True):
            reader, writer = os.pipe()
            os.close(reader)
            # Real OS pipe and real buffering: write initially succeeds in
            # memory; only flush observes the already closed reader.
            with io.TextIOWrapper(io.BufferedWriter(io.FileIO(writer, "wb")), encoding="utf-8") as stdout:
                stderr = io.StringIO()
                candidate = self.payload(receipts=[receipt(str(json_output))])
                argv = ["--repo", str(self.repo), "review", "result", "add", self.task_id]
                if json_output:
                    argv.append("--json")
                with mock.patch.object(sys, "stdin", BinaryInput(encode(self.wire_payload(candidate)))), redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli_service.main(argv, _target_override=self.target, _maintenance_enabled=False)
                self.assertEqual(code, 2)
                self.assertIn("Review results recorded, but output failed", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
                stdout.flush()  # Interpreter-style later flush no longer retries the pipe.
        self.assertEqual(self.success("task", "show", self.task_id)["review_evidence"]["counts"]["receipts_current_generation"], 2)

    def test_two_pass_response_size_and_gate_do_not_complete_task(self):
        legacy = []
        add = result_service.add_review_receipt
        def capture(*args, **kwargs):
            saved = add(*args, **kwargs)
            legacy.append({"receipt": saved.receipt, "event": saved.event, "findings": []})
            return saved
        with mock.patch.object(result_service, "add_review_receipt", side_effect=capture):
            code, stdout, _ = self.invoke(self.payload(receipts=[receipt("a"), receipt("b")]))
        self.assertEqual(code, 0, stdout)
        envelope = json.loads(stdout)
        self.assert_gate(envelope["data"], None)
        self.assertEqual(self.success("task", "show", self.task_id)["task"]["status"], "in_progress")
        old_bytes = len(encode({**envelope, "data": {"receipts": legacy}}))
        new_bytes = len(encode(envelope))
        self.assertLess(new_bytes, old_bytes)
        print(f"RG3_RESPONSE_BYTES old={old_bytes} new={new_bytes} receipts=2 findings=0")

    def test_packet_template_and_each_unfinished_claim_are_rejected_without_writes(self):
        template = self.success("review", "prepare", self.task_id)["result_template"]
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(template), "invalid_review_evidence")
        for field in template["receipts"][0]:
            candidate = copy.deepcopy(template)
            candidate["receipts"][0].update(receipt())
            candidate["receipts"][0][field] = template["receipts"][0][field]
            with self.subTest(unfinished=field):
                self.assert_failure(self.invoke(candidate), "invalid_review_evidence")
        self.assertEqual(file_snapshot(self.root), before)

    def test_completed_packet_template_registers_findings_through_existing_writer(self):
        template = self.success("review", "prepare", self.task_id)["result_template"]
        finding = {"severity": "low", "summary": "src/example.py:12 Clarify the contract"}
        template["receipts"][0].update(receipt(findings=[finding]))
        code, stdout, _ = self.invoke(template)
        self.assertEqual(code, 0, stdout)
        saved = json.loads(stdout)["data"]["receipts"][0]
        self.assertEqual(saved["receipt"]["verdict"], "pass")
        self.assertEqual(saved["findings"][0]["finding"]["summary"], finding["summary"])

    def test_tier_zero_packet_template_accepts_explicit_not_required_declaration(self):
        self.task_id = self.add_review_task("--title", "Mechanical change", "--review-tier", "0", "--verification-not-required", "Mechanical review fixture")
        targeted = self.success("review", "target", "set", self.task_id, "--kind", "external_revision", "--revision", "reviewed-mechanical-change")
        template = targeted["review_preparation"]["packet"]["result_template"]
        self.assertIsNone(template["receipts"][0]["verdict"])
        template["receipts"][0].update(receipt(kind="not_required", verdict="not_required"))
        code, stdout, _ = self.invoke(template)
        self.assertEqual(code, 0, stdout)
        saved = json.loads(stdout)["data"]["receipts"][0]["receipt"]
        self.assertEqual(saved["verdict"], "not_required")
        self.assertNotIn("review_provenance", saved)
        self.assertIsNone(self.success("task", "show", self.task_id, "--audit")["review_evidence"]["recent_receipts"][0]["review_provenance"])

    def test_completed_packet_template_retains_privacy_and_stale_target_rejection(self):
        template = self.success("review", "prepare", self.task_id)["result_template"]
        template["receipts"][0].update(receipt())
        private = copy.deepcopy(template)
        private["receipts"][0]["summary"] = "token=private-sentinel"
        before = file_snapshot(self.root)
        result = self.invoke(private)
        self.assert_failure(result, "privacy_rejected")
        self.assertNotIn("private-sentinel", result[1] + result[2])
        self.assertEqual(file_snapshot(self.root), before)
        self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(template), "review_target_mismatch")
        self.assertEqual(file_snapshot(self.root), before)

    def test_multiple_receipts_findings_keep_native_provenance_references_events_and_gates(self):
        findings = [{"severity": "low", "summary": "Shared observation 日本語"}]
        payload = self.payload(receipts=[receipt("reviewer-b", findings=findings), receipt("reviewer-a", findings=findings)])
        code, stdout, stderr = self.invoke(payload)
        self.assertEqual((code, stderr), (0, ""), stdout)
        result = json.loads(stdout)
        self.assertEqual(set(result), {"ok", "command", "project_id", "data", "warnings", "errors"})
        self.assertEqual(set(result["data"]), {"receipts", "review_gate", "omitted_details"})
        rows = result["data"]["receipts"]
        self.assertEqual([row["receipt"]["reviewer_key"] for row in rows], ["reviewer-b", "reviewer-a"])
        receipt_ids, finding_ids = [], []
        audit = self.success("task", "show", self.task_id, "--audit")["review_evidence"]
        audit_receipts = {item["review_receipt_id"]: item for item in audit["recent_receipts"]}
        for row in rows:
            self.assertEqual(set(row), {"receipt", "findings"})
            public = row["receipt"]
            self.assertEqual(set(public), {"review_receipt_id", "reviewer_key", "receipt_kind", "verdict", "summary", "user_approved"})
            saved = audit_receipts[public["review_receipt_id"]]
            self.assertEqual(saved["task_id"], self.task_id)
            self.assertEqual(saved["target_generation"], 1)
            self.assertEqual(public["summary"], payload["receipts"][0]["summary"])
            self.assertEqual(public["user_approved"], 0)
            provenance = saved["review_provenance"]
            for key, value in PROVENANCE.items():
                self.assertEqual(provenance[key], value)
            self.assertEqual((provenance["provenance_version"], provenance["assurance_class"], provenance["producer_class"], provenance["producer_version"]), (1, "bound_attestation", "trusted_caller", 1))
            receipt_ids.append(public["review_receipt_id"])
            nested = row["findings"][0]
            self.assertEqual(set(nested), {"finding"})
            self.assertEqual(nested["finding"]["review_receipt_id"], public["review_receipt_id"])
            self.assertEqual(nested["finding"]["summary"], findings[0]["summary"])
            finding_ids.append(nested["finding"]["review_finding_id"])
        self.assertEqual(len(set(receipt_ids + finding_ids)), 4)
        with closing(connect_initialized(self.target)) as connection:
            validate_evidence_ledger_storage(connection)
            references = [dict(row) for row in connection.execute("SELECT * FROM evidence_references WHERE source_kind IN ('review_receipt','review_finding')")]
            self.assertEqual({row["source_id"] for row in references}, set(receipt_ids + finding_ids))
            for reference in references:
                self.assertEqual((reference["task_id"], reference["contract_revision"], reference["target_generation"], reference["target_value"]), (self.task_id, 1, 1, FINGERPRINT))
                self.assertEqual((reference["assurance_class"], reference["producer_class"], reference["producer_version"]), ("bound_attestation", "trusted_caller", 1))
                self.assertIsNotNone(reference["acceptance_criterion_id"])
            stored_events = connection.execute("SELECT task_event_id, event_type FROM task_events WHERE event_type IN ('review_receipt_added','review_finding_added') ORDER BY rowid").fetchall()
            self.assertEqual(len({row[0] for row in stored_events}), 4)
            self.assertEqual([row[1] for row in stored_events], ["review_receipt_added", "review_finding_added"] * 2)
        evidence = self.success("task", "show", self.task_id)["review_evidence"]
        self.assertTrue(evidence["gate"]["satisfied"])
        self.assertEqual(evidence["gate"]["qualifying_independent_passes"], 2)
        self.assertEqual(evidence["counts"]["open_low"], 2)
        self.assertEqual({row["review_finding_id"] for row in evidence["current_findings"]}, set(finding_ids))
        self.assert_gate(result["data"], None)

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
                review_service.set_review_target(writer, project, task_id, kind="diff_fingerprint", revision=FINGERPRINT, database_target=self.target, caller=capture_caller_identity())
            after_concurrent_change.append(file_snapshot(self.root))
            return lock(connection, project, task_id, **kwargs)

        with mock.patch.object(result_service, "lock_and_reread_target_owner", side_effect=advance_then_lock):
            self.assert_failure(self.invoke(), "review_target_mismatch")
        self.assertEqual(len(after_concurrent_change), 1)
        self.assertEqual(file_snapshot(self.root), after_concurrent_change[0])
        self.assertEqual(self.success("task", "show", self.task_id)["task"]["review_target_generation"], 2)

    def test_concurrent_tier_change_cannot_reuse_prelock_fallback_normalization(self):
        self.task_id = self.add_review_task("--title", "Tier-sensitive fallback", "--review-tier", "1")
        self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        candidate = document(self.task_id, revision=0, receipts=[receipt(kind="self_review_fallback")])
        lock = result_service.lock_and_reread_target_owner
        concurrent_snapshot = []

        def raise_tier_then_lock(connection, project, task_id, **kwargs):
            self.assertFalse(connection.in_transaction)
            with closing(connect_initialized(self.target)) as writer, writer:
                task_service.edit_task(writer, project, task_id, review_tier=2, database_target=self.target, caller=capture_caller_identity())
            concurrent_snapshot.append(file_snapshot(self.root))
            return lock(connection, project, task_id, **kwargs)

        with mock.patch.object(result_service, "lock_and_reread_target_owner", side_effect=raise_tier_then_lock):
            self.assert_failure(self.invoke(candidate), "review_target_mismatch")
        self.assertEqual(len(concurrent_snapshot), 1)
        self.assertEqual(file_snapshot(self.root), concurrent_snapshot[0])
        self.assertEqual(self.success("task", "show", self.task_id)["task"]["review_tier"], 2)

    def test_missing_target_and_done_task_retain_single_receipt_errors(self):
        original_task_id = self.task_id
        self.task_id = self.add_review_task("--title", "No target", "--review-tier", "2")
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
                        caller=capture_caller_identity(),
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
        self.assert_gate(json.loads(stdout)["data"], None)
        self.assertTrue(self.success("task", "show", self.task_id)["review_evidence"]["gate"]["satisfied"])

    def test_tier_zero_null_provenance_and_tier_one_fallback_keep_existing_meanings(self):
        for tier in (0, 1):
            with self.subTest(tier=tier):
                self.task_id = self.add_review_task("--title", f"Tier {tier} result", "--review-tier", str(tier))
                self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
                kind = "not_required" if tier == 0 else "self_review_fallback"
                verdict = "not_required" if tier == 0 else "pass"
                candidate = document(self.task_id, revision=0, receipts=[receipt(kind=kind, verdict=verdict)])
                code, stdout, _ = self.invoke(candidate)
                self.assertEqual(code, 0, stdout)
                public = json.loads(stdout)["data"]["receipts"][0]["receipt"]
                self.assertEqual(public["user_approved"], 0)
                self.assertNotIn("review_provenance", public)
                audit = self.success("task", "show", self.task_id, "--audit")["review_evidence"]
                self.assertEqual(audit["recent_receipts"][0]["review_provenance"] is None, tier == 0)
                self.assertTrue(self.success("task", "show", self.task_id)["review_evidence"]["gate"]["satisfied"])

    def test_privacy_precedes_semantic_error_and_never_echoes_or_persists_input(self):
        candidate = self.payload(receipts=[{**receipt("reviewer-a"), "kind": "unsupported"}, receipt("reviewer-b")])
        candidate["receipts"][1]["findings"] = [{"severity": "low", "summary": "token=private-result"}]
        before = file_snapshot(self.root)
        result = self.invoke(candidate)
        self.assert_failure(result, "privacy_rejected")
        self.assertNotIn("private-result", result[1] + result[2])
        self.assertEqual(file_snapshot(self.root), before)

    def test_safe_diagnostic_reaches_json_and_text_without_writes(self):
        candidate = self.payload(receipts=[receipt("first"), receipt("second")])
        candidate["receipts"][1]["provenance"]["method_codes"] = ["diff_inspection"] * 2
        before = file_snapshot(self.root)
        result = self.invoke(candidate)
        error = self.assert_failure(result, "invalid_review_evidence")["errors"][0]
        self.assertEqual(error["field"], "receipts[].provenance.method_codes")
        self.assertEqual(set(error), {"code", "message", "field"})
        self.assertNotIn("second", error["message"])
        code, stdout, stderr = self.invoke(candidate, json_output=False)
        self.assertEqual((code, stdout), (1, ""))
        self.assertEqual(stderr, error["message"] + "\n")
        self.assertEqual(file_snapshot(self.root), before)

    def test_ineligible_approval_diagnostic_reaches_cli_without_writes(self):
        before = file_snapshot(self.root)
        error = self.assert_failure(self.invoke(self.payload(), "--user-approved-reviewer", "reviewer-a"),
                                    "invalid_review_evidence")["errors"][0]
        self.assertEqual(error["field"], "--user-approved-reviewer")
        self.assertEqual(file_snapshot(self.root), before)

    def test_element_and_receipt_group_diagnostics_reach_cli_without_writes(self):
        before = file_snapshot(self.root)
        for key in ("review_profiles", "review_lenses", "method_codes"):
            candidate = self.payload()
            candidate["receipts"][0]["provenance"][key] = [None]
            error = self.assert_failure(self.invoke(candidate), "invalid_review_evidence")["errors"][0]
            self.assertEqual(error["field"], "receipts[].provenance." + key + "[]")
        candidate = self.payload(receipts=[receipt(verdict="not_required")])
        error = self.assert_failure(self.invoke(candidate), "invalid_review_evidence")["errors"][0]
        self.assertEqual(error["field"], "receipts[]")
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

    def test_text_retains_ids_findings_and_gate_and_error_is_sanitized(self):
        candidate = self.payload(receipts=[receipt("reviewer-a", findings=[{"severity": "low", "summary": "Private work description"}]), receipt("reviewer-b")])
        code, stdout, stderr = self.invoke(candidate, json_output=False)
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Review results recorded: 2 receipts, 1 findings\n", stdout)
        for text in ("tg_review_receipt_", "tg_review_finding_", "reviewer-a", "Private work description", "satisfied=True", "not Task completion", "Omitted details:"):
            self.assertIn(text, stdout)
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
            added = install.run("task", "add", "--title", "Physical structured results", "--review-tier", "2", "--status", "in_progress", "--json")
            self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            target = install.run("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT, "--json")
            self.assertEqual(target.returncode, 0, target.stdout or target.stderr)
            before = file_snapshot(install.project_root, exclude_state=True)
            payload = document(task_id, revision=0, receipts=[receipt("reviewer-a"), receipt("reviewer-b")])
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(install.entrypoint), "review", "result", "add", task_id,
                 "--repo", str(install.project_root), "--json"],
                cwd=install.project_root, input=encode(self.wire_payload(payload)), capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8") or result.stderr.decode("utf-8"))
            self.assertEqual(result.stderr, b"")
            result_data = json.loads(result.stdout)["data"]
            self.assertEqual([row["receipt"]["summary"] for row in result_data["receipts"]], [item["summary"] for item in payload["receipts"]])
            self.assertEqual(file_snapshot(install.project_root, exclude_state=True), before)
            self.assertFalse((install.skill_root / "config" / "verification-runner.json").exists())


class ReviewResultOutputProcessTests(unittest.TestCase):
    def test_installed_closed_stdout_exits_two_without_shutdown_traceback_or_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve())
            def cli(*args):
                result = install.run(*args, "--json")
                self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
                return json.loads(result.stdout)["data"]
            cli("setup")
            for json_output in (False, True):
                task = cli("task", "add", "--title", "Broken output", "--review-tier", "2", "--status", "in_progress")["task"]["task_id"]
                cli("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
                reader, writer = os.pipe()
                os.close(reader)
                try:
                    command = [sys.executable, "-I", "-S", str(install.entrypoint), "--repo", str(install.project_root), "review", "result", "add", task]
                    if json_output:
                        command.append("--json")
                    result = subprocess.run(command, input=encode(document(task, revision=0)), stdout=writer, stderr=subprocess.PIPE, cwd=install.project_root, timeout=30, check=False)
                finally:
                    os.close(writer)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stderr.decode().strip(), "Review results recorded, but output failed. Do not resubmit; inspect recorded evidence before retrying.")
                self.assertEqual(cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 1)
                cli("task", "edit", task, "--status", "review_pending")


class ReviewResultsDocumentArrayTests(ReviewResultsTests):
    # Exercise the same storage, gate, race, privacy, approval, replay and
    # physical-CLI oracles with one complete original per Receipt as well.
    document_array = True


    def test_array_and_single_document_have_equal_evidence_and_gate_semantics(self):
        baseline_db = self.db
        payload = self.payload(receipts=[
            receipt("reviewer-a", findings=[{"severity": "medium", "summary": "Same observation"}], verdict="changes_requested"),
            receipt("reviewer-b", findings=[{"severity": "medium", "summary": "Same observation"}], verdict="changes_requested"),
        ])
        observations = []
        for as_array in (False, True):
            self.db = self.root / f"comparison-{as_array}.sqlite"
            shutil.copy2(baseline_db, self.db)
            self.target = database_target(self.db, self.repo)
            with mock.patch.object(self, "document_array", as_array):
                code, stdout, _ = self.invoke(payload)
            self.assertEqual(code, 0, stdout)
            with closing(connect_initialized(self.target)) as connection:
                # Digests include generated IDs: validate them before omitting
                # those derived hashes from the ID/time-normalized comparison.
                validate_evidence_ledger_storage(connection)
                references = [dict(row) for row in connection.execute(
                    "SELECT * FROM evidence_references WHERE source_kind IN ('review_receipt','review_finding') ORDER BY rowid"
                )]
            ids = {}

            def comparable(value):
                if isinstance(value, dict):
                    return {key: comparable(item) for key, item in sorted(value.items())
                            if key not in {"created_at", "updated_at", "digest"}}
                if isinstance(value, list):
                    return [comparable(item) for item in value]
                if isinstance(value, str) and value.startswith("tg_"):
                    return ids.setdefault(value, f"id-{len(ids)}")
                return value

            evidence = self.success("task", "show", self.task_id)["review_evidence"]
            observations.append(comparable([json.loads(stdout)["data"], references, evidence]))
        self.assertEqual(observations[0], observations[1])

    def test_disagreeing_document_identity_rejects_before_connection(self):
        first = self.payload()
        changes = [
            (("task_id",), "tg_task_ffffffffffffffff"), (("contract_revision",), 0),
            (("review_target", "kind"), "external_revision"),
            (("review_target", "value"), "sha256:" + "b" * 64),
            (("review_target", "base_revision"), "b" * 40),
            (("review_target", "generation"), 2),
        ]
        before = file_snapshot(self.root)
        for path, value in changes:
            second = self.payload(receipts=[receipt("reviewer-b")])
            target = second
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(component=path), mock.patch.object(cli_service, "connect_initialized", side_effect=AssertionError("identity before connection")):
                self.assert_failure(self.invoke([first, second]), "review_target_mismatch")
            self.assertEqual(file_snapshot(self.root), before)

    def test_array_second_document_privacy_precedes_identity_comparison(self):
        second = self.payload(receipts=[receipt("reviewer-b")])
        second["contract_revision"] = 0
        second["receipts"][0]["summary"] = "token=private-second-document"
        before = file_snapshot(self.root)
        with mock.patch.object(cli_service, "connect_initialized", side_effect=AssertionError("privacy before connection")):
            result = self.invoke([self.payload(), second])
        self.assert_failure(result, "privacy_rejected")
        self.assertNotIn("private-second-document", result[1] + result[2])
        self.assertEqual(file_snapshot(self.root), before)


class ReviewResultDocumentDecodingTests(unittest.TestCase):
    def test_original_documents_are_merged_without_rewriting_or_deduplication(self):
        same_finding = {"severity": "low", "summary": " Same finding 日本語 🚀 "}
        originals = [document(receipts=[receipt(" a ", findings=[same_finding])]),
                     document(receipts=[receipt("b", findings=[same_finding]), receipt("c")])]
        before = copy.deepcopy(originals)
        expected = document(receipts=originals[0]["receipts"] + originals[1]["receipts"])
        raw = b"[\n" + encode(originals[0]) + b",\n" + encode(originals[1]) + b"\n]"
        for supplied in (raw, raw.decode("utf-8")):
            self.assertEqual(result_service.decode_review_results(supplied), expected)
        self.assertEqual(originals, before)
        self.assertEqual(result_service.decode_review_results(encode([originals[0]])), originals[0])

    def test_every_original_is_a_closed_document_before_identity_comparison(self):
        first = document()
        invalid = [None, [], [document()], {}, first["receipts"][0]]
        for path, value in (
            (("version",), True), (("version",), 2), (("contract_revision",), True),
            (("review_target", "generation"), True), (("receipts",), []),
            (("receipts", 0, "provenance"), {}), (("receipts", 0, "user_approved"), True),
        ):
            candidate = copy.deepcopy(first)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            invalid.append(candidate)
        raws = [encode([first, second]) for second in invalid]
        raw = encode(first)
        raws.extend([b"[" + raw + b"," + raw[:-20] + b"]",
                     b"[" + raw + b"," + raw.replace(b'"version":1', b'"version":1,"version":1') + b"]"])
        for supplied in raws:
            with self.subTest(size=len(supplied)), self.assertRaises(review_service.ReviewEvidenceError) as raised:
                result_service.decode_review_results(supplied)
            self.assertEqual(raised.exception.code, "invalid_review_evidence")

    def test_array_limits_apply_to_whole_raw_input_and_combined_receipts_and_findings(self):
        originals = [document(receipts=[receipt(f"reviewer-{i}", findings=[
            {"severity": "low", "summary": f"Finding {j}"} for j in range(8)
        ])]) for i in range(8)]
        raw = encode(originals)
        expected = document(receipts=[entry for value in originals for entry in value["receipts"]])
        self.assertEqual(result_service.decode_review_results(raw), expected)
        boundary = raw + b" " * (result_service.REVIEW_RESULTS_INPUT_LIMIT - len(raw))
        self.assertEqual(result_service.decode_review_results(boundary), expected)
        too_many_findings = copy.deepcopy(originals)
        too_many_findings[-1]["receipts"][0]["findings"].append({"severity": "low", "summary": "Extra"})
        too_many_receipts = [document(receipts=[receipt(str(i)) for i in range(8)]), document()]
        for supplied in (boundary + b" ", encode([]), encode(originals + [document()]),
                         encode(too_many_findings), encode(too_many_receipts)):
            with self.subTest(size=len(supplied)), self.assertRaises(review_service.ReviewEvidenceError) as raised:
                result_service.decode_review_results(supplied)
            self.assertEqual(raised.exception.code, "invalid_review_evidence")


if __name__ == "__main__":
    unittest.main()
