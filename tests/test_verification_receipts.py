from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.verification_receipt_test_support import (
    DEFAULT_VERIFICATION,
    FINGERPRINT_B,
    add_receipt,
    add_task,
    completion,
    initialize,
    payload,
    run_taskgov,
    seed_current_review_evidence,
    set_target,
    show_task,
    table_count,
)

from task_governance_tool import cli as cli_module
from task_governance_tool import review_packet as packet_module
from task_governance_tool import verification_receipts as receipt_service
from task_governance_tool.storage import verification_expectation_digest
from task_governance_tool.verification_receipts import (
    VerificationReceiptError,
    normalize_verification_receipt_input,
)


class VerificationReceiptValidationTests(unittest.TestCase):
    def test_normalization_accepts_closed_values_and_signed_int64_boundaries(self):
        for result in ("pass", "fail", "timeout"):
            for coverage in ("full", "partial"):
                with self.subTest(result=result, coverage=coverage):
                    values = normalize_verification_receipt_input(
                        result=result,
                        duration_ms=(1 << 63) - 1,
                        scope_coverage=coverage,
                        expected_target_generation=(1 << 63) - 1,
                    )
                    self.assertEqual(values.result, result)
                    self.assertEqual(values.duration_ms, (1 << 63) - 1)
                    self.assertEqual(values.scope_coverage, coverage)
                    self.assertEqual(
                        values.expected_target_generation,
                        (1 << 63) - 1,
                    )

        zero_duration = normalize_verification_receipt_input(
            result="pass",
            duration_ms=0,
            scope_coverage="full",
            expected_target_generation=1,
        )
        self.assertEqual(zero_duration.duration_ms, 0)

    def test_normalization_rejects_each_field_with_the_fixed_contract(self):
        cases = (
            (
                {"result": "success"},
                "invalid_verification_evidence",
                "result must be one of pass, fail, or timeout",
            ),
            (
                {"duration_ms": -1},
                "invalid_verification_evidence",
                "duration_ms must be a nonnegative signed-64-bit integer",
            ),
            (
                {"duration_ms": 1 << 63},
                "invalid_verification_evidence",
                "duration_ms must be a nonnegative signed-64-bit integer",
            ),
            (
                {"scope_coverage": "complete"},
                "invalid_verification_evidence",
                "scope_coverage must be full or partial",
            ),
            (
                {"expected_target_generation": 0},
                "invalid_verification_evidence",
                "expected_target_generation must be a positive signed-64-bit integer",
            ),
            (
                {"expected_target_generation": 1 << 63},
                "invalid_verification_evidence",
                "expected_target_generation must be a positive signed-64-bit integer",
            ),
        )
        baseline = {
            "result": "pass",
            "duration_ms": 1,
            "scope_coverage": "full",
            "expected_target_generation": 1,
        }
        for override, code, message in cases:
            with self.subTest(override=override):
                with self.assertRaises(VerificationReceiptError) as raised:
                    normalize_verification_receipt_input(
                        **{**baseline, **override}
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.message, message)

        with self.assertRaises(VerificationReceiptError) as fail_fast:
            normalize_verification_receipt_input(
                result="success",
                duration_ms=-1,
                scope_coverage="complete",
                expected_target_generation=0,
            )
        self.assertEqual(fail_fast.exception.field, "result")


class VerificationReceiptIntegrationTests(unittest.TestCase):

    def test_result_coverage_matrix_drives_only_the_exact_current_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            cases = tuple(
                (result, coverage)
                for result in ("pass", "fail", "timeout")
                for coverage in ("full", "partial")
            )

            for index, (result, coverage) in enumerate(cases, start=1):
                with self.subTest(result=result, coverage=coverage):
                    generation = set_target(db, repo, task_id)
                    before = payload(
                        show_task(
                            db,
                            repo,
                            task_id,
                            json_output=True,
                        )
                    )["data"]["verification_evidence"]
                    self.assertEqual(
                        before["gate"]["blocking_code"],
                        "verification_receipt_required",
                    )

                    recorded = add_receipt(
                        db,
                        repo,
                        task_id,
                        generation,
                        result=result,
                        scope_coverage=coverage,
                    )
                    self.assertEqual(recorded.returncode, 0, recorded.stdout)
                    receipt = payload(recorded)["data"]["receipt"]
                    shown = payload(
                        show_task(
                            db,
                            repo,
                            task_id,
                            json_output=True,
                        )
                    )["data"]["verification_evidence"]

                    qualifies = result == "pass" and coverage == "full"
                    preparation = payload(recorded)["data"]["review_preparation"]
                    self.assertEqual(preparation["status"], "ready" if qualifies else "blocked")
                    self.assertEqual(preparation["packet"] is not None, qualifies)
                    self.assertEqual(preparation["errors"], [] if qualifies else [{
                        "code": "verification_receipt_blocking",
                        "message": "current verification evidence does not satisfy the required result and coverage",
                    }])
                    self.assertEqual(shown["gate"]["satisfied"], qualifies)
                    self.assertEqual(
                        shown["gate"]["blocking_code"],
                        None if qualifies else "verification_receipt_blocking",
                    )
                    self.assertEqual(
                        shown["gate"]["qualifying_receipt_id"],
                        receipt["verification_receipt_id"] if qualifies else None,
                    )
                    self.assertEqual(
                        shown["counts"],
                        {
                            "receipts_exact_current": 1,
                            "qualifying_exact_current": int(qualifies),
                            "blocking_exact_current": int(not qualifies),
                        },
                    )
                    self.assertEqual(table_count(db, "verification_receipts"), index)


    def test_status_expectation_and_target_errors_use_fixed_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))

            ready = add_task(
                db,
                repo,
                title="Ready task",
                status="ready",
            )
            ready_result = add_receipt(db, repo, ready["task_id"], 1)
            self.assertEqual(
                payload(ready_result)["errors"][0]["code"],
                "invalid_status_transition",
            )

            empty = add_task(
                db,
                repo,
                title="Empty expectation",
                verification="",
            )
            empty_generation = set_target(db, repo, empty["task_id"])
            empty_gate = payload(
                show_task(
                    db,
                    repo,
                    empty["task_id"],
                    json_output=True,
                )
            )["data"]["verification_evidence"]["gate"]
            self.assertEqual(
                empty_gate,
                {
                    "required": False,
                    "satisfied": True,
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                },
            )
            empty_result = add_receipt(
                db,
                repo,
                empty["task_id"],
                empty_generation,
            )
            self.assertEqual(
                payload(empty_result)["errors"][0]["code"],
                "verification_expectation_required",
            )

            targetless = add_task(
                db,
                repo,
                title="Missing target",
            )
            targetless_result = add_receipt(
                db,
                repo,
                targetless["task_id"],
                1,
            )
            self.assertEqual(
                payload(targetless_result)["errors"][0]["code"],
                "review_target_required",
            )
            targetless_gate = payload(
                show_task(
                    db,
                    repo,
                    targetless["task_id"],
                    json_output=True,
                )
            )["data"]["verification_evidence"]["gate"]
            self.assertEqual(
                targetless_gate["blocking_code"],
                "review_target_required",
            )

            done = add_task(
                db,
                repo,
                title="Done task",
                verification="",
            )
            seed_current_review_evidence(db, repo, done["task_id"])
            completed = completion(db, repo, done["task_id"])
            self.assertEqual(completed.returncode, 0, completed.stdout)
            done_result = add_receipt(db, repo, done["task_id"], 1)
            self.assertEqual(
                payload(done_result)["errors"][0]["code"],
                "done_task_requires_reopen",
            )

            for result in (
                ready_result,
                empty_result,
                targetless_result,
                done_result,
            ):
                self.assertEqual(
                    payload(result)["data"],
                    {"receipt": None},
                )
            self.assertEqual(table_count(db, "verification_receipts"), 0)



    def test_completion_check_and_write_require_fresh_pass_full_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            first_generation = seed_current_review_evidence(db, repo, task_id)

            required = completion(db, repo, task_id, check=True)
            self.assertEqual(required.returncode, 0, required.stdout)
            self.assertFalse(payload(required)["data"]["ready"])
            self.assertEqual(
                payload(required)["data"]["blocking_codes"],
                ["verification_receipt_required"],
            )
            mismatched_evidence = run_taskgov(
                "task",
                "complete",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--verification-complete",
                "--review-complete",
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-1",
                "--completion-evidence-reason",
                "Approved external material",
                "--external-revision-approved",
                "--json",
            )
            self.assertEqual(
                payload(mismatched_evidence)["errors"][0]["code"],
                "verification_receipt_required",
            )
            rejected_write = completion(db, repo, task_id)
            self.assertEqual(rejected_write.returncode, 1, rejected_write.stdout)
            self.assertEqual(
                payload(rejected_write)["errors"][0]["code"],
                "verification_receipt_required",
            )

            with mock.patch.object(
                cli_module,
                "select_current_verification_runner_basis",
            ) as selector, mock.patch.object(
                receipt_service,
                "read_internal_task",
                wraps=receipt_service.read_internal_task,
            ) as task_reads:
                blocked_receipt = add_receipt(
                    db,
                    repo,
                    task_id,
                    first_generation,
                    result="fail",
                    scope_coverage="full",
                )
            self.assertEqual(blocked_receipt.returncode, 0, blocked_receipt.stdout)
            selector.assert_not_called()
            self.assertEqual(task_reads.call_count, 2)
            blocking = completion(db, repo, task_id, check=True)
            self.assertEqual(
                payload(blocking)["data"]["blocking_codes"],
                ["verification_receipt_blocking"],
            )

            second_generation = seed_current_review_evidence(
                db,
                repo,
                task_id,
                fingerprint=FINGERPRINT_B,
            )
            qualifying = add_receipt(
                db,
                repo,
                task_id,
                second_generation,
            )
            self.assertEqual(qualifying.returncode, 0, qualifying.stdout)
            receipt_id = payload(qualifying)["data"]["receipt"][
                "verification_receipt_id"
            ]
            ready = completion(db, repo, task_id, check=True)
            self.assertTrue(payload(ready)["data"]["ready"])
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                cycle = connection.execute(
                    """
                    SELECT verification_basis_version,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(cycle[0], 1)
            self.assertEqual(
                cycle[1],
                verification_expectation_digest(DEFAULT_VERIFICATION),
            )
            self.assertEqual(cycle[2], receipt_id)

            shown = payload(
                show_task(
                    db,
                    repo,
                    task_id,
                    json_output=True,
                    audit=True,
                )
            )
            public_cycle = shown["data"]["completion_history"]["cycles"][0]
            self.assertNotIn("verification_basis_version", public_cycle)
            self.assertNotIn("verification_expectation_digest", public_cycle)
            self.assertNotIn("verification_receipt_id", public_cycle)
















    def test_missing_receipt_precedes_insufficient_and_blocking_review_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Receipt ordering audit",
                review_tier=2,
            )
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)

            insufficient = completion(db, repo, task_id)
            self.assertEqual(insufficient.returncode, 1, insufficient.stdout)
            self.assertEqual(
                payload(insufficient)["errors"][0]["code"],
                "verification_receipt_required",
            )

            changes_requested = run_taskgov(
                "review",
                "receipt",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--reviewer",
                "ordering-auditor",
                "--kind",
                "independent",
                "--verdict",
                "changes_requested",
                "--summary",
                "Current target needs changes",
                "--reviewer-class",
                "human",
                "--model-state",
                "not_applicable",
                "--skill-state",
                "not_applicable",
                "--context-relation",
                "external_context",
                "--json",
            )
            self.assertEqual(
                changes_requested.returncode,
                0,
                changes_requested.stdout,
            )
            shown = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]
            review_evidence = shown["review_evidence"]
            self.assertEqual(
                review_evidence["counts"][
                    "changes_requested_current_generation"
                ],
                1,
            )
            self.assertEqual(
                shown["task"]["review_target_generation"],
                generation,
            )

            blocking = completion(db, repo, task_id)
            self.assertEqual(blocking.returncode, 1, blocking.stdout)
            self.assertEqual(
                payload(blocking)["errors"][0]["code"],
                "verification_receipt_required",
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)







class ReceiptPacketConnectionTests(unittest.TestCase):
    def prepare(self, db, repo, task_id, receipt_id=None):
        args = ["review", "prepare", task_id, "--db", str(db), "--repo", str(repo),
                "--read-only", "--json"]
        if receipt_id is not None:
            args.extend(("--verification-receipt-id", receipt_id))
        return run_taskgov(*args)

    def test_success_keeps_both_operations_but_removes_the_public_relay(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task_id = add_task(db, repo)["task_id"]
            generation = set_target(db, repo, task_id)
            with mock.patch.object(cli_module, "add_verification_receipt",
                                   wraps=cli_module.add_verification_receipt) as write, \
                 mock.patch.object(cli_module, "prepare_review_packet",
                                   wraps=cli_module.prepare_review_packet) as prepare:
                recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            self.assertEqual((write.call_count, prepare.call_count), (1, 1))
            data = payload(recorded)["data"]
            self.assertEqual(set(data), {"receipt", "review_preparation"})
            packet = data["review_preparation"]["packet"]
            self.assertEqual(packet["task"]["task_id"], data["receipt"]["task_id"])
            self.assertEqual(packet["contract"]["revision"], data["receipt"]["contract_revision"])
            self.assertEqual(packet["review_target"]["generation"], generation)
            for key in ("kind", "value", "generation"):
                self.assertEqual(packet["review_target"][key], data["receipt"]["source_revision"][key])
            self.assertEqual(packet["review_target"]["base_revision"] or None,
                             data["receipt"]["source_revision"]["base_revision"])
            before = db.read_bytes()
            separate = self.prepare(db, repo, task_id)
            self.assertEqual(payload(separate)["data"], packet)
            self.assertEqual(db.read_bytes(), before)
            # A representative segment: both service operations remain, but the
            # caller receives one CLI response instead of two. Byte counts are
            # distinct from LLM tokens and do not claim a total-token saving.
            combined_bytes = len(recorded.stdout.encode("utf-8"))
            old_envelope = payload(recorded)
            old_envelope["data"] = {"receipt": data["receipt"]}
            separate_bytes = len((json.dumps(old_envelope, ensure_ascii=False,
                                             separators=(",", ":")) + "\n").encode("utf-8"))
            separate_bytes += len(separate.stdout.encode("utf-8"))
            self.assertGreater(combined_bytes, 0)
            self.assertGreater(separate_bytes, 0)
            self.assertEqual(table_count(db, "verification_receipts"), 1)

    def test_post_commit_failure_preserves_receipt_and_retry_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task_id = add_task(db, repo)["task_id"]
            generation = set_target(db, repo, task_id)
            for failure in (packet_module.ReviewPacketError(
                    "review_packet_stale", "review context changed while preparing the packet"),
                    RuntimeError("private exception must not escape")):
                with self.subTest(failure=type(failure).__name__):
                    with mock.patch.object(cli_module, "prepare_review_packet", side_effect=failure):
                        recorded = add_receipt(db, repo, task_id, generation)
                    self.assertEqual(recorded.returncode, 0, recorded.stdout)
                    envelope = payload(recorded)
                    self.assertTrue(envelope["ok"])
                    data = envelope["data"]
                    self.assertEqual(data["review_preparation"]["status"], "failed")
                    self.assertIsNone(data["review_preparation"]["packet"])
                    self.assertNotIn("private exception", recorded.stdout)
                    receipt_id = data["receipt"]["verification_receipt_id"]
                    before = db.read_bytes()
                    retried = self.prepare(db, repo, task_id, receipt_id)
                    self.assertEqual(retried.returncode, 0, retried.stdout)
                    self.assertEqual(db.read_bytes(), before)
                    replay = add_receipt(db, repo, task_id, generation)
                    self.assertEqual(payload(replay)["errors"][0]["code"],
                                     "verification_receipt_already_recorded")
                    generation = set_target(db, repo, task_id)

    def test_retry_rejects_other_task_old_generation_and_nonpassing_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task_id = add_task(db, repo)["task_id"]
            other_id = add_task(db, repo, title="Other task")["task_id"]
            set_target(db, repo, other_id)
            generation = set_target(db, repo, task_id)
            receipt = payload(add_receipt(db, repo, task_id, generation))["data"]["receipt"]
            receipt_id = receipt["verification_receipt_id"]
            wrong_task = self.prepare(db, repo, other_id, receipt_id)
            self.assertEqual(payload(wrong_task)["errors"][0]["code"], "verification_basis_stale")
            generation = set_target(db, repo, task_id, fingerprint=FINGERPRINT_B)
            current = payload(add_receipt(db, repo, task_id, generation, result="fail"))["data"]["receipt"]
            for saved_id, expected in ((receipt_id, "verification_basis_stale"),
                    (current["verification_receipt_id"], "verification_receipt_blocking")):
                result = self.prepare(db, repo, task_id, saved_id)
                self.assertEqual(payload(result)["errors"][0]["code"], expected)
            # Standalone preparation is still permitted but is not a gate PASS.
            self.assertEqual(self.prepare(db, repo, task_id).returncode, 0)
            self.assertFalse(payload(show_task(db, repo, task_id, json_output=True))
                             ["data"]["verification_evidence"]["gate"]["satisfied"])

    def test_target_change_during_packet_observation_is_not_rebound(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task_id = add_task(db, repo)["task_id"]
            generation = set_target(db, repo, task_id)
            observe = packet_module._observe_target
            def change_target(*args, **kwargs):
                set_target(db, repo, task_id, fingerprint=FINGERPRINT_B)
                return observe(*args, **kwargs)
            with mock.patch.object(packet_module, "_observe_target", side_effect=change_target):
                recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            data = payload(recorded)["data"]
            self.assertEqual(data["review_preparation"]["status"], "failed")
            self.assertEqual(data["review_preparation"]["errors"][0]["code"], "verification_basis_stale")
            self.assertEqual(data["receipt"]["source_revision"]["generation"], generation)
            self.assertEqual(table_count(db, "verification_receipts"), 1)

    def test_registration_failure_and_nonpassing_result_never_prepare(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task_id = add_task(db, repo)["task_id"]
            generation = set_target(db, repo, task_id)
            with mock.patch.object(cli_module, "prepare_review_packet") as prepare:
                failed = add_receipt(db, repo, task_id, generation + 1)
                blocked = add_receipt(db, repo, task_id, generation, result="timeout")
            prepare.assert_not_called()
            self.assertFalse(payload(failed)["ok"])
            self.assertEqual(payload(failed)["data"], {"receipt": None})
            self.assertTrue(payload(blocked)["ok"])
            self.assertEqual(payload(blocked)["data"]["review_preparation"]["status"], "blocked")

    def test_contract_change_between_commit_and_prepare_keeps_only_the_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task_id = add_task(db, repo)["task_id"]
            generation = set_target(db, repo, task_id)
            prepare = cli_module.prepare_review_packet
            def revise_contract(*args, **kwargs):
                edited = run_taskgov("task", "edit", task_id, "--db", str(db),
                                     "--repo", str(repo), "--scope", "Changed accepted scope", "--json")
                self.assertEqual(edited.returncode, 0, edited.stdout)
                return prepare(*args, **kwargs)
            with mock.patch.object(cli_module, "prepare_review_packet", side_effect=revise_contract):
                recorded = add_receipt(db, repo, task_id, generation)
            self.assertTrue(payload(recorded)["ok"])
            preparation = payload(recorded)["data"]["review_preparation"]
            self.assertEqual(preparation["status"], "failed")
            self.assertIsNone(preparation["packet"])
            self.assertEqual(table_count(db, "verification_receipts"), 1)

    def test_retry_identifier_errors_are_sanitized_and_do_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task_id = add_task(db, repo)["task_id"]
            set_target(db, repo, task_id)
            before = db.read_bytes()
            for value in ("", "bad-receipt", "tg_verification_receipt_" + "a" * 17):
                result = self.prepare(db, repo, task_id, value)
                self.assertEqual(payload(result)["errors"][0]["code"], "invalid_verification_evidence")
                self.assertEqual(payload(result)["data"], {})
                self.assertEqual(db.read_bytes(), before)

    def test_lost_response_recovers_existing_id_without_replaying_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task_id = add_task(db, repo)["task_id"]
            generation = set_target(db, repo, task_id)
            # Discard the response as if the caller lost it after the commit.
            add_receipt(db, repo, task_id, generation)
            shown = payload(show_task(db, repo, task_id, json_output=True))["data"]
            saved = shown["verification_evidence"]["current_receipt"]
            self.assertEqual((saved["result"], saved["scope_coverage"]), ("pass", "full"))
            before = db.read_bytes()
            retried = self.prepare(db, repo, task_id, saved["verification_receipt_id"])
            self.assertEqual(retried.returncode, 0, retried.stdout)
            self.assertEqual(db.read_bytes(), before)
            self.assertEqual(table_count(db, "verification_receipts"), 1)


if __name__ == "__main__":
    unittest.main()
