from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.m14_test_support import file_snapshot, initialize_taskgov_internal, make_physical_install
from tests.test_m17_cli_consumer_hardening import _run_cli, _setup
from tests.test_task_context import run

from task_governance_tool import storage
from task_governance_tool import verification_receipt_repository as receipt_repository


REVIEW_RECEIPT_FIELDS = {
    "review_receipt_id", "reviewer_key", "receipt_kind", "verdict", "summary",
    "user_approved", "created_at",
}
VERIFICATION_RECEIPT_FIELDS = {
    "verification_receipt_id", "result", "duration_ms", "scope_coverage", "created_at",
}
SHOW_KEYS = {
    "task", "events", "suggested_next_action", "review_evidence", "handoff_summary",
    "contract", "latest_checkpoint", "completion_history", "verification_evidence",
    "effort_advisory_enabled",
}


class TaskShowProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.db = self.root / "taskgov.sqlite"
        initialize_taskgov_internal(repo=self.repo, db=self.db)
        self.event_sequence = 0

    def success(self, *arguments):
        result = run(self.db, self.repo, *arguments)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        return payload

    def add(self, *arguments):
        return self.success(
            "task", "add", "--title", "Working projection", "--status", "in_progress",
            "--review-tier", "2", *arguments,
        )["data"]["task"]

    def show(self, task_id, *, audit=False):
        options = ("--audit",) if audit else ()
        return self.success("task", "show", task_id, "--read-only", *options)

    def target(self, task_id):
        return self.success(
            "review", "target", "set", task_id, "--kind", "diff_fingerprint",
            "--revision", "sha256:" + "a" * 64,
        )["data"]["task"]["review_target_generation"]

    def review(self, task_id, reviewer, *, verdict="pass"):
        return self.success(
            "review", "receipt", "add", task_id, "--reviewer", reviewer,
            "--kind", "independent", "--verdict", verdict, "--summary", "Focused review assessment",
            "--reviewer-class", "human", "--model-state", "not_applicable",
            "--skill-state", "not_applicable", "--context-relation", "external_context",
        )["data"]["receipt"]

    def finding(self, task_id, receipt_id, severity, summary):
        return self.success(
            "review", "finding", "add", task_id, "--receipt-id", receipt_id,
            "--severity", severity, "--summary", summary,
        )["data"]["finding"]

    def resolve(self, finding_id):
        return self.success(
            "review", "finding", "resolve", finding_id, "--resolution", "Corrected after inspection",
        )["data"]["finding"]

    def verification(self, task_id, generation, *, result="pass", coverage="full"):
        return self.success(
            "verification", "receipt", "add", task_id, "--result", result,
            "--duration-ms", "17", "--scope-coverage", coverage,
            "--expected-target-generation", str(generation),
        )["data"]["receipt"]

    def seed_event(self, task, event_type, summary, *, timestamp="2999-01-01T00:00:00Z"):
        self.event_sequence += 1
        event_id = f"tg_event_{self.event_sequence:016x}"
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                "INSERT INTO task_events(task_event_id,task_id,project_id,event_type,summary,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (event_id, task["task_id"], task["project_id"], event_type, summary, timestamp),
            )
            connection.commit()
        return event_id

    def assert_modes_have_same_gates(self, normal, audit):
        self.assertEqual(normal["review_evidence"]["gate"], {
            key: value for key, value in audit["review_evidence"]["gate"].items()
            if key != "review_tier"
        })
        self.assertEqual(normal["review_evidence"]["counts"], {
            key: value for key, value in audit["review_evidence"]["counts"].items()
            if key != "receipts_total"
        })
        self.assertEqual(normal["verification_evidence"]["gate"], audit["verification_evidence"]["gate"])
        self.assertEqual(normal["verification_evidence"]["counts"], {
            key: value for key, value in audit["verification_evidence"]["counts"].items()
            if key != "receipts_total"
        })

    def test_normal_has_fixed_shapes_and_complete_task_contract_checkpoint_and_routes(self):
        scope = "Complete scope 日本語. " * 110
        acceptance = "Retain every acceptance condition. " * 100
        constraints = "Permission boundary remains explicit. " * 35
        task = self.add(
            "--verification", "Focused verification",
            "--contract-scope", scope, "--contract-acceptance", acceptance,
            "--contract-constraints", constraints, "--contract-authority-ref", "docs/specification.md",
        )
        for with_checkpoint in (False, True):
            with self.subTest(checkpoint=with_checkpoint):
                checkpoint = None
                if with_checkpoint:
                    checkpoint = self.success(
                        "task", "checkpoint", task["task_id"], "--summary", "Current progress 日本語",
                        "--next-action", "Inspect the remaining change", "--unresolved-risk", "Review remains pending",
                    )["data"]["checkpoint"]
                before = file_snapshot(self.root)
                normal = self.show(task["task_id"])["data"]
                audit = self.show(task["task_id"], audit=True)["data"]
                context = self.success("task", "context")["data"]["selected"]
                self.assertEqual(set(normal), SHOW_KEYS)
                self.assertEqual(set(audit), SHOW_KEYS)
                self.assertEqual(context, normal)
                for field in ("task", "contract", "latest_checkpoint", "handoff_summary", "effort_advisory_enabled", "suggested_next_action"):
                    self.assertEqual(normal[field], audit[field])
                self.assertEqual(normal["task"], audit["task"])
                self.assertEqual(normal["contract"]["scope"], scope.strip())
                self.assertEqual(normal["contract"]["acceptance"], acceptance.strip())
                self.assertEqual(normal["contract"]["constraints"], constraints.strip())
                self.assertEqual(normal["latest_checkpoint"], checkpoint)
                self.assertEqual(normal["completion_history"], {"total": 0, "legacy_history_incomplete": False})
                self.assertEqual(set(audit["completion_history"]), {"total", "returned_count", "truncated", "legacy_history_incomplete", "cycles"})
                self.assertEqual(set(normal["review_evidence"]), {"gate", "counts", "current_receipts", "current_findings"})
                self.assertEqual(set(audit["review_evidence"]), {"target", "gate", "counts", "blocking_findings", "recent_receipts", "recent_findings", "preparation_binding"})
                self.assertIsNone(audit["review_evidence"]["preparation_binding"])
                self.assertEqual(set(normal["verification_evidence"]), {"current_verification_subject", "gate", "counts", "current_receipt"})
                self.assertEqual(set(audit["verification_evidence"]), {"contract_revision", "expectation", "source_revision", "current_verification_subject", "gate", "counts", "recent_receipts"})
                self.assertIsNone(normal["verification_evidence"]["current_receipt"])
                self.assert_modes_have_same_gates(normal, audit)
                self.assertEqual(file_snapshot(self.root), before)

    def test_all_current_review_receipts_are_retained_beyond_audit_window(self):
        task = self.add()
        self.target(task["task_id"])
        receipts = [self.review(task["task_id"], f"reviewer-{index:02d}") for index in range(12)]
        normal = self.show(task["task_id"])["data"]
        audit = self.show(task["task_id"], audit=True)["data"]
        current = normal["review_evidence"]["current_receipts"]
        self.assertEqual(len(current), 12)
        self.assertEqual(len(audit["review_evidence"]["recent_receipts"]), 10)
        self.assertEqual({row["review_receipt_id"] for row in current}, {row["review_receipt_id"] for row in receipts})
        self.assertEqual([row["review_receipt_id"] for row in current], [row["review_receipt_id"] for row in reversed(receipts)])
        for row in current:
            self.assertEqual(set(row), REVIEW_RECEIPT_FIELDS)
            original = next(receipt for receipt in receipts if receipt["review_receipt_id"] == row["review_receipt_id"])
            self.assertEqual(row, {key: original[key] for key in REVIEW_RECEIPT_FIELDS})
        self.assert_modes_have_same_gates(normal, audit)

    def test_old_open_findings_and_current_resolved_blocker_survive_many_generations(self):
        task = self.add()
        self.target(task["task_id"])
        old_receipt = self.review(task["task_id"], "old-reviewer")
        high = self.finding(task["task_id"], old_receipt["review_receipt_id"], "high", "Old unresolved blocker")
        low = self.finding(task["task_id"], old_receipt["review_receipt_id"], "low", "Old unresolved suggestion")
        for generation in range(2, 14):
            self.assertEqual(self.target(task["task_id"]), generation)
            receipt = self.review(task["task_id"], f"reviewer-{generation}")
            historical = self.finding(task["task_id"], receipt["review_receipt_id"], "low", f"Resolved historical detail {generation}")
            self.resolve(historical["review_finding_id"])
        medium = self.finding(task["task_id"], receipt["review_receipt_id"], "medium", "Fresh review is still required")
        self.resolve(medium["review_finding_id"])

        before = file_snapshot(self.root)
        normal = self.show(task["task_id"])["data"]
        audit = self.show(task["task_id"], audit=True)["data"]
        findings = {row["review_finding_id"]: row for row in normal["review_evidence"]["current_findings"]}
        self.assertEqual(set(findings), {high["review_finding_id"], low["review_finding_id"], medium["review_finding_id"]})
        self.assertEqual(findings[high["review_finding_id"]]["blocking_reason"], "unresolved")
        self.assertIsNone(findings[low["review_finding_id"]]["blocking_reason"])
        self.assertEqual(findings[medium["review_finding_id"]]["blocking_reason"], "fresh_review_required")
        self.assertEqual(findings[high["review_finding_id"]]["target_generation"], 1)
        self.assertEqual(normal["task"]["review_target_generation"], 13)
        self.assertTrue({high["review_finding_id"], low["review_finding_id"]}.isdisjoint(
            row["review_finding_id"] for row in audit["review_evidence"]["recent_findings"]
        ))
        self.assert_modes_have_same_gates(normal, audit)
        self.assertFalse(normal["review_evidence"]["gate"]["satisfied"])
        self.assertEqual(file_snapshot(self.root), before)

        self.target(task["task_id"])
        refreshed = self.show(task["task_id"])["data"]["review_evidence"]["current_findings"]
        self.assertEqual({row["review_finding_id"] for row in refreshed}, {high["review_finding_id"], low["review_finding_id"]})

    def test_notes_and_transition_context_are_not_limited_to_latest_ten_events(self):
        task = self.add()
        standalone = self.success("task", "edit", task["task_id"], "--add-note", "Standalone continuation note 日本語")["data"]["event"]
        combined = self.success("task", "edit", task["task_id"], "--priority", "high", "--add-note", "Combined edit continuation note")["data"]["event"]
        expected_notes = [standalone["task_event_id"], combined["task_event_id"]]
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE task_events SET created_at = '2000-01-01T00:00:00Z' WHERE task_id = ?", (task["task_id"],))
            connection.commit()
        original_bytes = "  Stored continuation 日本語\r\nwith original spacing  "
        for index in range(12):
            event_type = ("note_added", "task_updated", "review_tier_changed", "task_reopened")[index % 4]
            expected_notes.append(self.seed_event(task, event_type, original_bytes if index == 0 else f"Continuation context {index}", timestamp="2000-01-01T00:00:00Z"))
        for _ in range(20):
            latest_mechanical = self.seed_event(task, "checkpoint_recorded", "Checkpoint recorded")
        before = file_snapshot(self.root)
        normal = self.show(task["task_id"])["data"]
        audit = self.show(task["task_id"], audit=True)["data"]
        self.assertEqual([row["task_event_id"] for row in normal["events"]], [latest_mechanical, *reversed(expected_notes)])
        self.assertEqual(len(audit["events"]), 10)
        self.assertTrue(all(row["event_type"] == "checkpoint_recorded" for row in audit["events"]))
        self.assertIn(original_bytes, [row["summary"] for row in normal["events"]])
        self.assertIn(standalone["summary"], [row["summary"] for row in normal["events"]])
        self.assertIn(combined["summary"], [row["summary"] for row in normal["events"]])
        self.assertEqual(file_snapshot(self.root), before)

        newest_note = self.seed_event(task, "note_added", "Newest note is also a note", timestamp="3000-01-01T00:00:00Z")
        updated = self.show(task["task_id"])["data"]["events"]
        self.assertEqual([row["task_event_id"] for row in updated], [newest_note, *reversed(expected_notes)])

    def test_newly_exposed_old_private_note_fails_safely_without_partial_data(self):
        task = self.add()
        note_id = self.seed_event(task, "note_added", "Old continuation note", timestamp="2000-01-01T00:00:00Z")
        for _ in range(12):
            self.seed_event(task, "checkpoint_recorded", "Checkpoint recorded")
        for rejected_summary in ("token=stored-private-value", " " + "x" * 1000 + " "):
            with self.subTest(stored_length=len(rejected_summary)):
                with closing(sqlite3.connect(self.db)) as connection:
                    connection.execute("UPDATE task_events SET summary = ? WHERE task_event_id = ?", (rejected_summary, note_id))
                    connection.commit()
                before = file_snapshot(self.root)
                audit = self.show(task["task_id"], audit=True)
                self.assertNotIn(rejected_summary, json.dumps(audit))
                for arguments in (("task", "show", task["task_id"]), ("task", "context")):
                    with self.subTest(arguments=arguments):
                        result = run(self.db, self.repo, *arguments)
                        payload = json.loads(result.stdout)
                        self.assertEqual(result.returncode, 2)
                        self.assertFalse(payload["ok"])
                        self.assertEqual(payload["warnings"], [])
                        self.assertEqual(payload["errors"], [{"code": "project_state_unreadable", "message": "project state could not be read safely"}])
                        self.assertNotIn(rejected_summary.strip(), result.stdout or result.stderr)
                self.assertEqual(file_snapshot(self.root), before)

    def test_failed_timeout_and_partial_current_receipts_keep_exact_details_and_gate(self):
        task = self.add("--verification", "Focused verification")
        for result, coverage in (("fail", "full"), ("timeout", "full"), ("pass", "partial"), ("pass", "full")):
            with self.subTest(result=result, coverage=coverage):
                generation = self.target(task["task_id"])
                receipt = self.verification(task["task_id"], generation, result=result, coverage=coverage)
                normal = self.show(task["task_id"])["data"]
                audit = self.show(task["task_id"], audit=True)["data"]
                evidence = normal["verification_evidence"]
                self.assertEqual(evidence["current_receipt"], {key: receipt[key] for key in VERIFICATION_RECEIPT_FIELDS})
                qualifying = result == "pass" and coverage == "full"
                self.assertEqual(evidence["gate"]["satisfied"], qualifying)
                self.assertEqual(evidence["gate"]["blocking_code"], None if qualifying else "verification_receipt_blocking")
                self.assert_modes_have_same_gates(normal, audit)
        self.target(task["task_id"])
        missing = self.show(task["task_id"])["data"]["verification_evidence"]
        self.assertIsNone(missing["current_receipt"])
        self.assertEqual(missing["gate"]["blocking_code"], "verification_receipt_required")

    def test_exact_current_receipt_is_not_derived_from_recent_ten_with_timestamp_ties(self):
        task = self.add("--verification", "Focused verification")
        for index in range(12):
            generation = self.target(task["task_id"])
            suffix = f"{0xffffffffffffffff - index:016x}" if index < 11 else "0000000000000000"
            with mock.patch.object(storage, "utc_now", return_value="2026-08-01T00:00:00Z"), mock.patch.object(
                receipt_repository, "secrets", SimpleNamespace(token_hex=lambda count, value=suffix: value)
            ):
                receipt = self.verification(task["task_id"], generation)
        before = file_snapshot(self.root)
        normal = self.show(task["task_id"])["data"]
        audit = self.show(task["task_id"], audit=True)["data"]
        recent = audit["verification_evidence"]["recent_receipts"]
        self.assertEqual(len(recent), 10)
        self.assertEqual({row["created_at"] for row in recent}, {receipt["created_at"]})
        self.assertNotIn(receipt["verification_receipt_id"], {row["verification_receipt_id"] for row in recent})
        self.assertEqual(normal["verification_evidence"]["current_receipt"], {key: receipt[key] for key in VERIFICATION_RECEIPT_FIELDS})
        self.assert_modes_have_same_gates(normal, audit)
        self.assertEqual(file_snapshot(self.root), before)

    def test_hidden_historical_finding_is_still_validated_in_both_modes(self):
        task = self.add()
        self.target(task["task_id"])
        receipt = self.review(task["task_id"], "historical-reviewer")
        finding = self.finding(task["task_id"], receipt["review_receipt_id"], "low", "Historical resolved detail")
        self.resolve(finding["review_finding_id"])
        self.target(task["task_id"])
        self.assertEqual(self.show(task["task_id"])["data"]["review_evidence"]["current_findings"], [])
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE review_findings SET summary = 'Tampered historical summary' WHERE review_finding_id = ?", (finding["review_finding_id"],))
            connection.commit()
        before = file_snapshot(self.root)
        failures = []
        for options in ((), ("--audit",)):
            response = run(self.db, self.repo, "task", "show", task["task_id"], *options)
            payload = json.loads(response.stdout)
            self.assertEqual(response.returncode, 2)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "evidence_ledger_inconsistent")
            self.assertIsNone(payload["data"]["review_evidence"])
            self.assertEqual(payload["warnings"], [])
            failures.append(payload)
        self.assertEqual(failures[0], failures[1])
        self.assertEqual(file_snapshot(self.root), before)

    def test_not_found_has_identical_empty_failure_shape_in_both_modes(self):
        before = file_snapshot(self.root)
        results = [run(self.db, self.repo, "task", "show", "tg_task_missing", *options) for options in ((), ("--audit",))]
        self.assertEqual([result.returncode for result in results], [1, 1])
        self.assertEqual(json.loads(results[0].stdout), json.loads(results[1].stdout))
        self.assertEqual(json.loads(results[0].stdout)["errors"][0]["code"], "not_found")
        self.assertEqual(file_snapshot(self.root), before)

    def test_hidden_completion_bundle_is_still_validated_in_both_modes(self):
        from tests.test_m243c_runner_gate import _corrupt_completion_bundle_digest

        task = self.add("--review-tier", "0")
        self.target(task["task_id"])
        self.success(
            "review", "receipt", "add", task["task_id"], "--reviewer", "mechanical-review",
            "--kind", "not_required", "--verdict", "not_required", "--summary", "Mechanical fixture",
        )
        self.success("task", "complete", task["task_id"], "--verification-complete", "--review-complete", "--commit-not-required")
        _corrupt_completion_bundle_digest(self.db, task_id=task["task_id"])
        before = file_snapshot(self.root)
        failures = []
        for options in ((), ("--audit",)):
            response = run(self.db, self.repo, "task", "show", task["task_id"], *options)
            payload = json.loads(response.stdout)
            self.assertEqual(response.returncode, 2)
            self.assertEqual(payload["errors"][0]["code"], "completion_history_inconsistent")
            self.assertIsNone(payload["data"]["completion_history"])
            failures.append(payload)
        self.assertEqual(failures[0], failures[1])
        self.assertEqual(file_snapshot(self.root), before)

    def test_completed_audit_remains_available_without_changing_saved_evidence(self):
        install = make_physical_install(self.root / "physical")
        _setup(install)

        def physical(*arguments):
            result = _run_cli(install, *arguments)
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
            return json.loads(result.stdout)

        task_id = physical("task", "add", "--title", "Completed evidence fixture", "--status", "in_progress", "--review-tier", "0")["data"]["task"]["task_id"]
        physical("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", "sha256:" + "b" * 64)
        physical("review", "receipt", "add", task_id, "--reviewer", "mechanical-review", "--kind", "not_required", "--verdict", "not_required", "--summary", "Mechanical fixture")
        physical("task", "complete", task_id, "--verification-complete", "--review-complete", "--commit-not-required")
        _setup(install)
        evidence = install.fixed_root / "evidence"
        saved = {path.relative_to(evidence).as_posix(): path.read_bytes() for path in evidence.rglob("*.json")}
        self.assertIn("index.json", saved)
        self.assertEqual(len(saved), 2)
        before = file_snapshot(install.project_root)

        normal = physical("task", "show", task_id)["data"]
        audit = physical("task", "show", task_id, "--audit")["data"]
        physical("task", "context")
        self.assertEqual(normal["completion_history"], {"total": 1, "legacy_history_incomplete": False})
        self.assertEqual(audit["completion_history"]["returned_count"], 1)
        self.assertEqual(len(audit["completion_history"]["cycles"]), 1)
        self.assertEqual(normal["task"], audit["task"])
        self.assert_modes_have_same_gates(normal, audit)
        self.assertLess(len(json.dumps(normal, ensure_ascii=False).encode("utf-8")), len(json.dumps(audit, ensure_ascii=False).encode("utf-8")))
        self.assertEqual({path.relative_to(evidence).as_posix(): path.read_bytes() for path in evidence.rglob("*.json")}, saved)
        self.assertEqual(file_snapshot(install.project_root), before)


if __name__ == "__main__":
    unittest.main()
