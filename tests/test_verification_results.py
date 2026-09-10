from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.m14_test_support import file_snapshot, make_physical_install, run_taskgov_internal
from tests.test_m243c_runner_gate import RunnerServiceFixture, _launch, _persist_terminal
from tests.verification_receipt_test_support import (
    FINGERPRINT_A, FINGERPRINT_B, add_receipt, add_task, completion,
    initialize, payload, seed_current_review_evidence, set_target,
    table_count, target_for,
)

from task_governance_tool import cli as cli_service
from task_governance_tool import verification_receipts as receipt_service
from task_governance_tool import verification_results as result_service
from task_governance_tool import verification_runner_selection as runner_selection
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.task_values import TaskValidationError
from task_governance_tool.verification_receipts import VerificationReceiptError


TASK_ID = "tg_task_0123456789abcdef"


def document(task_id=TASK_ID, *, generation=1, result="pass", coverage="full"):
    return {
        "version": 1, "task_id": task_id, "result": result, "duration_ms": 17,
        "scope_coverage": coverage, "expected_target_generation": generation,
    }


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


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


class VerificationResultInputTests(unittest.TestCase):
    def assert_invalid(self, raw, *, code="invalid_verification_evidence"):
        with self.assertRaises((VerificationReceiptError, TaskValidationError)) as raised:
            result_service.decode_verification_result(raw, task_id=TASK_ID)
        self.assertEqual(raised.exception.code, code)

    def test_exact_byte_limit_and_native_integer_boundaries(self):
        candidate = document()
        candidate["duration_ms"] = 0
        expected = {key: value for key, value in candidate.items() if key not in {"version", "task_id"}}
        raw = encode(candidate)
        self.assertEqual(result_service.decode_verification_result(raw, task_id=TASK_ID), expected)
        padded = raw + b" " * (4096 - len(raw))
        self.assertEqual(result_service.decode_verification_result(padded, task_id=TASK_ID), expected)
        self.assert_invalid(padded + b" ")
        for key in ("duration_ms", "expected_target_generation"):
            candidate[key] = (1 << 63) - 1
        values = result_service.decode_verification_result(encode(candidate), task_id=TASK_ID)
        self.assertEqual(values["duration_ms"], (1 << 63) - 1)
        self.assertEqual(values["expected_target_generation"], (1 << 63) - 1)

    def test_required_closed_keys_and_exact_json_types(self):
        baseline = document()
        for key in baseline:
            with self.subTest(missing=key):
                self.assert_invalid(encode({name: value for name, value in baseline.items() if name != key}))
        for key in (
            "verification_subject", "verification_receipt_id", "contract_revision",
            "source_revision", "created_at", "command", "stdout", "exit_code",
        ):
            with self.subTest(extra=key):
                self.assert_invalid(encode({**baseline, key: "must-not-be-retained"}))
        invalid_values = {
            "version": (True, "1", 1.0, 0, 2, None),
            "task_id": (None, 7, [], {}),
            "result": (None, 1, True, [], {}),
            "scope_coverage": (None, 1, False, [], {}),
            "duration_ms": (None, True, "1", 1.0, -1, 1 << 63),
            "expected_target_generation": (None, False, "1", 1.0, 0, -1, 1 << 63),
        }
        for key, values in invalid_values.items():
            for value in values:
                with self.subTest(field=key, value=value):
                    self.assert_invalid(encode({**baseline, key: value}))

    def test_duplicate_keys_nonfinite_numbers_bom_surrogates_and_non_documents(self):
        raw = encode(document())
        malformed = (
            b"", b"{", b"[]", b"null", b"true", raw + raw, b"\xff",
            b"\xef\xbb\xbf" + raw, raw[:-1] + b',"version":1}',
            raw.replace(b'"duration_ms":17', b'"duration_ms":NaN'),
            raw.replace(b'"duration_ms":17', b'"duration_ms":Infinity'),
            raw.replace(b'"duration_ms":17', b'"duration_ms":1e0'),
            raw.replace(b'"result":"pass"', b'"result":"\\ud800"'),
            raw.replace(TASK_ID.encode(), b"\\udfff"),
        )
        for value in malformed:
            with self.subTest(value=value[:45]):
                self.assert_invalid(value)
        for value in (raw.decode(), bytearray(raw), None, {}):
            with self.subTest(raw_type=type(value)):
                self.assert_invalid(value)

    def test_normalization_privacy_and_task_identity_are_preserved(self):
        candidate = document(task_id=" " + TASK_ID + " ", result=" pass ", coverage=" partial ")
        values = result_service.decode_verification_result(encode(candidate), task_id=TASK_ID)
        self.assertEqual((values["result"], values["scope_coverage"]), ("pass", "partial"))
        for key, value in (("result", "success"), ("scope_coverage", "complete")):
            self.assert_invalid(encode({**document(), key: value}))
        for key in ("task_id", "result", "scope_coverage"):
            self.assert_invalid(encode({**document(), key: "token=private-value"}), code="privacy_rejected")
        self.assert_invalid(encode(document(task_id="tg_task_ffffffffffffffff")), code="verification_basis_stale")


class VerificationResultCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo, self.db = initialize(self.root)
        self.target = target_for(self.db, self.repo)
        self.task_id = self.success(
            "task", "add", "--title", "Fixed external verification result",
            "--status", "in_progress", "--review-tier", "0",
            "--verification", "Run the fixed local verifier",
            "--contract-scope", "Register one aggregate declaration",
            "--contract-acceptance", "Keep the existing Receipt gates",
        )["task"]["task_id"]
        self.generation = set_target(self.db, self.repo, self.task_id)

    def success(self, *arguments):
        result = run_taskgov_internal(
            *arguments, "--repo", str(self.repo), "--db", str(self.db), "--json",
            maintenance_enabled=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        return json.loads(result.stdout)["data"]

    def document(self, **kwargs):
        return document(self.task_id, generation=self.generation, **kwargs)

    def invoke(self, candidate=None, *arguments, stdin=None, json_output=True, maintenance_enabled=False):
        stream = stdin if stdin is not None else BinaryInput(encode(self.document() if candidate is None else candidate))
        argv = [
            "--repo", str(self.repo), "verification", "receipt", "add",
            self.task_id, "--from-stdin", *arguments,
        ]
        if json_output:
            argv.append("--json")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", stream), redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_service.main(argv, _target_override=self.target, _maintenance_enabled=maintenance_enabled)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_failure(self, result, code, *, exit_code=1):
        actual_exit, stdout, stderr = result
        self.assertEqual((actual_exit, stderr), (exit_code, ""), stdout or stderr)
        envelope = json.loads(stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["command"], "verification.receipt.add")
        self.assertEqual(envelope["data"], {"receipt": None})
        self.assertEqual(envelope["errors"][0]["code"], code)
        self.assertEqual(envelope["warnings"], [])
        return envelope

    def test_fixed_external_producer_stdout_is_consumed_verbatim_with_existing_receipt_shape(self):
        producer = subprocess.run(
            [sys.executable, "-c", (
                "import json,sys; "
                "value={'version':1,'task_id':sys.argv[1],'result':'pass','duration_ms':17,"
                "'scope_coverage':'full','expected_target_generation':int(sys.argv[2])}; "
                "sys.stdout.buffer.write(json.dumps(value,indent=2).encode('utf-8')+b'\\n'); "
                "sys.stderr.write('verifier diagnostics stay outside the Receipt\\n')"
            ), self.task_id, str(self.generation)],
            capture_output=True, timeout=10, check=True,
        )
        before = self.success("task", "show", self.task_id)
        code, stdout, stderr = self.invoke(stdin=BinaryInput(producer.stdout))
        self.assertEqual((code, stderr), (0, ""), stdout)
        envelope = json.loads(stdout)
        self.assertEqual(set(envelope["data"]), {"receipt"})
        receipt = envelope["data"]["receipt"]
        self.assertEqual(set(receipt), set(receipt_service.PUBLIC_VERIFICATION_RECEIPT_FIELDS))
        self.assertEqual((receipt["result"], receipt["duration_ms"], receipt["scope_coverage"]), ("pass", 17, "full"))
        self.assertEqual(receipt["task_id"], self.task_id)
        self.assertEqual(receipt["contract_revision"], 1)
        self.assertEqual(receipt["verification_subject"]["basis_version"], 1)
        self.assertEqual(receipt["verification_subject"]["kind"], "task_verification_criterion")
        self.assertEqual(receipt["source_revision"], {
            "kind": "diff_fingerprint", "value": FINGERPRINT_A,
            "base_revision": None, "generation": self.generation,
        })
        after = self.success("task", "show", self.task_id)
        for key in ("task", "events", "contract", "review_evidence", "handoff_summary", "completion_history"):
            self.assertEqual(after[key], before[key])
        self.assertNotIn(producer.stderr.decode().strip(), stdout)
        self.assertNotIn(producer.stdout, self.db.read_bytes())

    def test_all_result_coverage_pairs_retain_gate_meaning_without_inference(self):
        for result in ("pass", "fail", "timeout"):
            for coverage in ("full", "partial"):
                with self.subTest(result=result, coverage=coverage):
                    self.generation = set_target(self.db, self.repo, self.task_id)
                    code, stdout, stderr = self.invoke(self.document(result=result, coverage=coverage))
                    self.assertEqual((code, stderr), (0, ""), stdout)
                    gate = self.success("task", "show", self.task_id)["verification_evidence"]["gate"]
                    qualifies = result == "pass" and coverage == "full"
                    self.assertEqual(gate["satisfied"], qualifies)
                    self.assertEqual(gate["blocking_code"], None if qualifies else "verification_receipt_blocking")
        self.assertEqual(table_count(self.db, "verification_receipts"), 6)

    def test_physical_install_subprocess_accepts_verbatim_input_without_target_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve())
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stdout or setup.stderr)
            added = install.run(
                "task", "add", "--title", "Physical verifier declaration",
                "--status", "in_progress", "--verification", "Run the fixed local verifier",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            target = install.run(
                "review", "target", "set", task_id, "--kind", "diff_fingerprint",
                "--revision", FINGERPRINT_A, "--json",
            )
            self.assertEqual(target.returncode, 0, target.stdout or target.stderr)
            generation = json.loads(target.stdout)["data"]["task"]["review_target_generation"]
            raw = ("\n" + json.dumps(document(task_id, generation=generation), indent=2) + "\n").encode("utf-8")
            before = file_snapshot(install.project_root, exclude_state=True)
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(install.entrypoint),
                 "verification", "receipt", "add", task_id, "--from-stdin",
                 "--repo", str(install.project_root), "--json"],
                cwd=install.project_root, input=raw, capture_output=True,
                timeout=30, check=False,
            )
            self.assertEqual((result.returncode, result.stderr), (0, b""), result.stdout.decode("utf-8"))
            receipt = json.loads(result.stdout)["data"]["receipt"]
            self.assertEqual(set(receipt), set(receipt_service.PUBLIC_VERIFICATION_RECEIPT_FIELDS))
            self.assertEqual((receipt["task_id"], receipt["result"], receipt["duration_ms"], receipt["scope_coverage"]), (task_id, "pass", 17, "full"))
            self.assertEqual(receipt["source_revision"]["generation"], generation)
            self.assertEqual(file_snapshot(install.project_root, exclude_state=True), before)
            self.assertFalse((install.skill_root / "config" / "verification-runner.json").exists())

    def test_existing_runner_fallback_allows_receipt_but_pass_and_blocking_refuse(self):
        for branch in ("fallback", "pass", "blocking"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                _prepared, intent = _launch(fixture)
                _persist_terminal(fixture, intent, branch=branch)
                self.repo, self.db, self.target = fixture.repo, fixture.db, fixture.target
                self.task_id, self.generation = fixture.task_id, 1
                before = file_snapshot(Path(temporary))
                with mock.patch.object(runner_selection, "_stored_runner_physical_basis_matches", return_value=True), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
                    result = self.invoke()
                maintenance.assert_not_called()
                if branch == "fallback":
                    self.assertEqual((result[0], result[2]), (0, ""), result[1])
                    self.assertEqual(table_count(self.db, "verification_receipts"), 1)
                else:
                    self.assert_failure(result, "evidence_basis_stale")
                    self.assertEqual(table_count(self.db, "verification_receipts"), 0)
                    self.assertEqual(file_snapshot(Path(temporary)), before)

    def test_immutable_replay_and_single_item_interoperation_require_new_target(self):
        self.assertEqual(self.invoke(self.document(result="fail", coverage="partial"))[0], 0)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(), "verification_receipt_already_recorded")
        manual = add_receipt(self.db, self.repo, self.task_id, self.generation)
        self.assertEqual(payload(manual)["errors"][0]["code"], "verification_receipt_already_recorded")
        self.assertEqual(file_snapshot(self.root), before)
        old_generation = self.generation
        self.generation = set_target(self.db, self.repo, self.task_id)
        self.assert_failure(self.invoke({**self.document(), "expected_target_generation": old_generation}), "verification_basis_stale")
        manual = add_receipt(self.db, self.repo, self.task_id, self.generation)
        self.assertEqual(manual.returncode, 0, manual.stdout)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(), "verification_receipt_already_recorded")
        self.assertEqual(file_snapshot(self.root), before)
        with closing(sqlite3.connect(self.db)) as connection:
            rows = connection.execute("SELECT result,scope_coverage FROM verification_receipts ORDER BY target_generation").fetchall()
        self.assertEqual(rows, [("fail", "partial"), ("pass", "full")])

    def test_wrong_task_and_changed_contract_or_verification_never_rebind_old_result(self):
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke({**self.document(), "task_id": "tg_task_ffffffffffffffff"}), "verification_basis_stale")
        self.assertEqual(file_snapshot(self.root), before)
        old_result = self.document()
        self.success(
            "task", "edit", self.task_id, "--contract-scope", "Updated accepted scope",
            "--contract-acceptance", "Keep new accepted verification basis",
            "--contract-authority-ref", f"user_instruction:{self.task_id}:2",
            "--contract-change-reason", "User explicitly revised accepted work",
        )
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(old_result), "review_target_required")
        self.assertEqual(file_snapshot(self.root), before)
        self.generation = set_target(self.db, self.repo, self.task_id)
        self.assert_failure(self.invoke(old_result), "verification_basis_stale")
        self.assertEqual(self.invoke()[0], 0)
        old_result = self.document()
        self.success("task", "edit", self.task_id, "--verification", "Run the revised local verifier")
        self.generation = set_target(self.db, self.repo, self.task_id)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(old_result), "verification_basis_stale")
        self.assertEqual(file_snapshot(self.root), before)

    def test_forbidden_statuses_empty_expectation_and_missing_target_do_not_write(self):
        self.success("task", "edit", self.task_id, "--status", "review_pending")
        self.assertEqual(self.invoke()[0], 0)
        for status, arguments in (
            ("paused", ("--pause-reason", "Awaiting continuation")),
            ("blocked", ("--blocked-reason", "Awaiting dependency")),
            ("cancelled", ()),
        ):
            with self.subTest(status=status):
                task = add_task(self.db, self.repo)
                self.task_id = task["task_id"]
                self.generation = set_target(self.db, self.repo, self.task_id)
                self.success("task", "edit", self.task_id, "--status", status, *arguments)
                before = file_snapshot(self.root)
                self.assert_failure(self.invoke(), "invalid_status_transition")
                self.assertEqual(file_snapshot(self.root), before)
        for options, error in (
            ({"status": "ready"}, "invalid_status_transition"),
            ({"verification": ""}, "verification_expectation_required"),
            ({}, "review_target_required"),
        ):
            with self.subTest(options=options):
                self.task_id = add_task(self.db, self.repo, **options)["task_id"]
                self.generation = 1
                before = file_snapshot(self.root)
                self.assert_failure(self.invoke(), error)
                self.assertEqual(file_snapshot(self.root), before)

    def test_done_requires_reopen_and_historical_receipts_cannot_satisfy_it(self):
        self.generation = seed_current_review_evidence(self.db, self.repo, self.task_id)
        self.assertEqual(self.invoke()[0], 0)
        finished = completion(self.db, self.repo, self.task_id)
        self.assertEqual(finished.returncode, 0, finished.stdout)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke(), "done_task_requires_reopen")
        self.assertEqual(file_snapshot(self.root), before)
        self.success("task", "edit", self.task_id, "--status", "in_progress", "--reopen-reason", "Explicit follow-up correction")
        old_result = self.document()
        self.generation = set_target(self.db, self.repo, self.task_id)
        self.assert_failure(self.invoke(old_result), "verification_basis_stale")
        self.assertEqual(self.success("task", "show", self.task_id)["verification_evidence"]["gate"]["blocking_code"], "verification_receipt_required")

    def test_writer_reread_rejects_concurrent_target_and_status_changes(self):
        actual_begin = receipt_service.begin_initialized_write
        for change, expected in (("target", "verification_basis_stale"), ("status", "invalid_status_transition")):
            with self.subTest(change=change):
                current = self.success("task", "show", self.task_id)["task"]
                self.generation = current["review_target_generation"]
                concurrent_snapshots = []

                def change_then_lock(connection, target):
                    self.assertFalse(connection.in_transaction)
                    if change == "target":
                        set_target(self.db, self.repo, self.task_id, fingerprint=FINGERPRINT_B)
                    else:
                        self.success("task", "edit", self.task_id, "--status", "paused", "--pause-reason", "Concurrent continuation boundary")
                    concurrent_snapshots.append(file_snapshot(self.root))
                    return actual_begin(connection, target)

                with mock.patch.object(receipt_service, "begin_initialized_write", side_effect=change_then_lock), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
                    self.assert_failure(self.invoke(maintenance_enabled=True), expected)
                maintenance.assert_not_called()
                self.assertEqual(len(concurrent_snapshots), 1)
                self.assertEqual(file_snapshot(self.root), concurrent_snapshots[0])
                self.assertEqual(table_count(self.db, "verification_receipts"), 0)

    def test_late_reference_failure_rolls_back_receipt_without_maintenance(self):
        before = file_snapshot(self.root)
        inserted = []

        def fail_reference(connection, **kwargs):
            inserted.append(connection.execute("SELECT COUNT(*) FROM verification_receipts").fetchone()[0])
            raise sqlite3.OperationalError("private fault token=never-echo")

        with mock.patch.object(receipt_service, "persist_evidence_reference_locked", side_effect=fail_reference), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            result = self.invoke(maintenance_enabled=True)
        self.assert_failure(result, "internal_error", exit_code=2)
        self.assertNotIn("never-echo", result[1] + result[2])
        self.assertEqual(inserted, [1])
        self.assertEqual(file_snapshot(self.root), before)
        maintenance.assert_not_called()

    def test_read_only_and_invalid_input_do_not_consume_or_open_writer(self):
        before = file_snapshot(self.root)
        with mock.patch.object(cli_service, "connect_initialized", side_effect=AssertionError("must not open writer")), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            self.assert_failure(self.invoke(None, "--read-only", stdin=BinaryInput(readable=False), maintenance_enabled=True), "invalid_argument")
            for raw in (b"{", b" " * 4097, b"\xff", encode({**self.document(), "stdout": "token=secret"})):
                with self.subTest(raw_size=len(raw)):
                    stream = BinaryInput(raw)
                    result = self.invoke(stdin=stream, maintenance_enabled=True)
                    self.assert_failure(result, "invalid_verification_evidence")
                    self.assertNotIn("token=secret", result[1] + result[2])
                    self.assertEqual(stream.read_sizes, [4097])
            self.assert_failure(self.invoke(stdin=io.StringIO("{}")), "invalid_verification_evidence")
        maintenance.assert_not_called()
        self.assertEqual(file_snapshot(self.root), before)

    def test_input_mode_parse_errors_precede_state_and_stdin(self):
        single_options = (
            ("--result", "pass"), ("--duration-ms", "17"),
            ("--scope-coverage", "full"), ("--expected-target-generation", "1"),
        )
        for option in single_options:
            with self.subTest(option=option), mock.patch.object(cli_service, "resolve_context_target", side_effect=AssertionError("parse must precede state")):
                code, stdout, stderr = self.invoke(None, *option, stdin=BinaryInput(readable=False))
                self.assertEqual((code, stderr), (1, ""), stdout)
                envelope = json.loads(stdout)
                self.assertEqual(envelope["command"], "parse")
                self.assertEqual(envelope["data"], {})
        for omitted in range(len(single_options)):
            arguments = [part for index, pair in enumerate(single_options) if index != omitted for part in pair]
            with self.subTest(omitted=omitted):
                response = run_taskgov_internal(
                    "verification", "receipt", "add", self.task_id, *arguments,
                    "--repo", str(self.repo), "--db", str(self.db), "--json",
                    maintenance_enabled=False,
                )
                self.assertEqual(response.returncode, 1, response.stdout)
                self.assertEqual(payload(response)["command"], "parse")

    def test_success_keeps_text_and_backup_only_maintenance_after_close(self):
        opened, observed = [], []
        original_connect = cli_service.connect_initialized

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        def maintenance(target, outcome):
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute("SELECT 1")
            observed.append((outcome, table_count(self.db, "verification_receipts")))
            raise OSError("private backup detail token=never-echo")

        with mock.patch.object(cli_service, "connect_initialized", side_effect=connect), mock.patch.object(cli_service, "run_post_commit_maintenance", side_effect=maintenance) as coordinator:
            code, stdout, stderr = self.invoke(maintenance_enabled=True)
        self.assertEqual((code, stderr), (0, ""), stdout)
        coordinator.assert_called_once()
        self.assertEqual(observed, [(MutationOutcome(state_changed=True, viewer_relevant=False), 1)])
        self.assertNotIn("never-echo", stdout)
        self.assertTrue(json.loads(stdout)["ok"])
        self.generation = set_target(self.db, self.repo, self.task_id)
        code, stdout, stderr = self.invoke(json_output=False)
        self.assertEqual((code, stderr), (0, ""), stdout)
        self.assertRegex(stdout, r"^Verification receipt recorded: tg_verification_receipt_[0-9a-f]{16}\nResult: pass  Coverage: full\nSource: diff_fingerprint/generation [0-9]+\n$")


if __name__ == "__main__":
    unittest.main()
