from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

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
                            "receipts_total": index,
                            "receipts_exact_current": 1,
                            "qualifying_exact_current": int(qualifies),
                            "blocking_exact_current": int(not qualifies),
                        },
                    )


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
            rejected_write = completion(db, repo, task_id)
            self.assertEqual(rejected_write.returncode, 1, rejected_write.stdout)
            self.assertEqual(
                payload(rejected_write)["errors"][0]["code"],
                "verification_receipt_required",
            )

            blocked_receipt = add_receipt(
                db,
                repo,
                task_id,
                first_generation,
                result="fail",
                scope_coverage="full",
            )
            self.assertEqual(blocked_receipt.returncode, 0, blocked_receipt.stdout)
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
            review_evidence = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]["review_evidence"]
            self.assertEqual(
                review_evidence["counts"][
                    "changes_requested_current_generation"
                ],
                1,
            )
            self.assertEqual(
                review_evidence["target"]["generation"],
                generation,
            )

            blocking = completion(db, repo, task_id)
            self.assertEqual(blocking.returncode, 1, blocking.stdout)
            self.assertEqual(
                payload(blocking)["errors"][0]["code"],
                "verification_receipt_required",
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)







if __name__ == "__main__":
    unittest.main()
