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

from tests import test_review_evidence as review_fixtures
from tests.m14_test_support import (
    file_snapshot, initialize_taskgov_internal, make_physical_install,
    run_taskgov_internal,
)

from task_governance_tool import cli as cli_service
from task_governance_tool import finding_resolutions as resolution_service
from task_governance_tool import reviews as review_service
from task_governance_tool import storage as storage_service
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.task_values import TaskValidationError


TASK_ID = "tg_task_0123456789abcdef"
FINDING_A = "tg_review_finding_0123456789abcdef"
FINDING_B = "tg_review_finding_fedcba9876543210"
FINGERPRINT = "sha256:" + "a" * 64


def document(task_id=TASK_ID, *, groups=None):
    return {
        "version": 1, "task_id": task_id,
        "resolutions": [{"finding_ids": [FINDING_A], "resolution": "Corrected and verified"}] if groups is None else groups,
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


class FindingResolutionInputTests(unittest.TestCase):
    def assert_invalid(self, value, *, code=None):
        with self.assertRaises((review_service.ReviewEvidenceError, TaskValidationError)) as raised:
            resolution_service.decode_finding_resolutions(value)
        if code is not None:
            self.assertEqual(raised.exception.code, code)

    def test_explicit_grouped_resolutions_expand_in_input_order_and_normalize(self):
        shared = "共通の修正を確認しました"
        raw = encode(document(" " + TASK_ID + " ", groups=[
            {"finding_ids": [" " + FINDING_B + " ", FINDING_A], "resolution": " " + shared + " "},
            {"finding_ids": ["finding-c"], "resolution": "Separate resolution"},
        ]))
        self.assertEqual(resolution_service.decode_finding_resolutions(raw), {
            "task_id": TASK_ID,
            "resolutions": [
                {"finding_id": FINDING_B, "resolution": shared},
                {"finding_id": FINDING_A, "resolution": shared},
                {"finding_id": "finding-c", "resolution": "Separate resolution"},
            ],
        })
        self.assertEqual(raw.count(shared.encode("utf-8")), 1)

    def test_closed_shape_exact_types_and_no_implicit_selection_or_resolution(self):
        baseline = document()
        malformed = [
            [], None, {**baseline, "version": True}, {**baseline, "version": 1.0},
            {**baseline, "version": "1"}, {**baseline, "version": 2},
            {**baseline, "task_id": None}, {**baseline, "task_id": 1},
            {**baseline, "result": "pass"}, {**baseline, "review_target": {}},
            {key: value for key, value in baseline.items() if key != "task_id"},
            {**baseline, "resolutions": {}}, document(groups=[]),
            document(groups=[{"finding_ids": [], "resolution": "Empty selection"}]),
            document(groups=[{"finding_ids": FINDING_A, "resolution": "Wrong ID container"}]),
            document(groups=[{"finding_ids": [FINDING_A]}]),
            document(groups=[{"resolution": "No inferred selection"}]),
            document(groups=[{"finding_ids": [FINDING_A], "resolution": None}]),
            document(groups=[{"finding_ids": [FINDING_A], "resolution": False}]),
            document(groups=[{"finding_ids": [False], "resolution": "Wrong ID type"}]),
            document(groups=[{"finding_ids": [FINDING_A], "resolution": "", "status": "resolved"}]),
        ]
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self.assert_invalid(encode(candidate))

    def test_capacity_unicode_and_duplicate_guards_cover_complete_selection(self):
        raw = encode(document())
        padded = raw + b" " * (262144 - len(raw))
        self.assertEqual(resolution_service.decode_finding_resolutions(padded)["task_id"], TASK_ID)
        self.assert_invalid(padded + b" ")
        maximum = document(groups=[{"finding_ids": [f"finding-{i}" for i in range(64)], "resolution": "解" * 1000}])
        self.assertEqual(len(resolution_service.decode_finding_resolutions(encode(maximum))["resolutions"]), 64)
        maximum["resolutions"][0]["finding_ids"].append("finding-65")
        self.assert_invalid(encode(maximum))
        for ids, text in ((["x" * 129], "Resolution"), ([FINDING_A], "x" * 1001), ([FINDING_A], " ")):
            self.assert_invalid(encode(document(groups=[{"finding_ids": ids, "resolution": text}])))
        self.assertEqual(len(resolution_service.decode_finding_resolutions(encode(document(groups=[{"finding_ids": ["x" * 128], "resolution": "Resolution"}])))["resolutions"]), 1)
        for groups in (
            [{"finding_ids": [FINDING_A, " " + FINDING_A + " "], "resolution": "Same group"}],
            [{"finding_ids": [FINDING_A], "resolution": "First"}, {"finding_ids": [" " + FINDING_A + " "], "resolution": "Second"}],
        ):
            self.assert_invalid(encode(document(groups=groups)))
        for value in (
            b"", b"{", b"\xff", b"\xef\xbb\xbf" + raw, raw + raw,
            raw[:-1] + b',"version":1}',
            raw.replace(b'"version":1', b'"version":NaN'),
            raw.replace(b'"version":1', b'"version":Infinity'),
            raw.replace(b'"Corrected and verified"', b'"\\ud800"'),
        ):
            with self.subTest(value=value[:55]):
                self.assert_invalid(value)

    def test_every_id_and_resolution_uses_privacy_validation(self):
        secret = "token=private-value"
        for candidate in (
            document(secret),
            document(groups=[{"finding_ids": [secret], "resolution": "Safe"}]),
            document(groups=[{"finding_ids": [FINDING_A], "resolution": secret}]),
        ):
            self.assert_invalid(encode(candidate), code="privacy_rejected")


class FindingResolutionCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.db = self.root / "taskgov.sqlite"
        initialize_taskgov_internal(repo=self.repo, db=self.db)
        self.target = review_fixtures.database_target(self.db, self.repo)
        self.task_id = self.success("task", "add", "--title", "Selected Finding resolution", "--status", "in_progress", "--review-tier", "2")["task"]["task_id"]
        self.set_target()
        self.receipt_id = self.review("reviewer-a")["review_receipt_id"]
        self.review("reviewer-b")

    def success(self, *arguments):
        response = run_taskgov_internal(*arguments, "--repo", str(self.repo), "--db", str(self.db), "--json", maintenance_enabled=False)
        self.assertEqual(response.returncode, 0, response.stdout or response.stderr)
        return json.loads(response.stdout)["data"]

    def set_target(self):
        return self.success("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)

    def review(self, reviewer, *, verdict="pass"):
        response = review_fixtures.receipt_add(self.db, self.repo, self.task_id, reviewer, verdict=verdict, summary="Focused review assessment")
        self.assertEqual(response.returncode, 0, response.stdout or response.stderr)
        return json.loads(response.stdout)["data"]["receipt"]

    def finding(self, summary, *, severity="low", task_id=None, receipt_id=None):
        return self.success("review", "finding", "add", task_id or self.task_id, "--receipt-id", receipt_id or self.receipt_id, "--severity", severity, "--summary", summary)["finding"]

    def invoke(self, groups=None, *arguments, stdin=None, task_id=None, maintenance_enabled=False, json_output=True):
        stream = stdin if stdin is not None else BinaryInput(encode(document(task_id or self.task_id, groups=groups)))
        argv = ["--repo", str(self.repo), "review", "finding", "resolve", "--from-stdin", *arguments]
        if json_output:
            argv.append("--json")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", stream), redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_service.main(argv, _target_override=self.target, _maintenance_enabled=maintenance_enabled)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_failure(self, result, error, *, exit_code=1):
        code, stdout, stderr = result
        self.assertEqual((code, stderr), (exit_code, ""), stdout or stderr)
        envelope = json.loads(stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["command"], "review.finding.resolve")
        self.assertEqual(envelope["data"], {"findings": []})
        self.assertEqual(envelope["errors"][0]["code"], error)
        self.assertEqual(envelope["warnings"], [])

    def snapshot_rows(self):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.row_factory = sqlite3.Row
            return {
                table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("review_findings", "review_receipts", "task_events", "evidence_references")
            }

    def test_shared_and_individual_resolutions_preserve_mapping_unselected_findings_and_originals(self):
        first = self.finding("First selected issue")
        second = self.finding("Second selected issue")
        third = self.finding("Third selected issue")
        unselected = self.finding("Unselected blocker", severity="high")
        before = self.snapshot_rows()
        groups = [
            {"finding_ids": [second["review_finding_id"], first["review_finding_id"]], "resolution": "Shared verified correction 日本語"},
            {"finding_ids": [third["review_finding_id"]], "resolution": "Separate verified correction"},
        ]
        code, stdout, stderr = self.invoke(groups)
        self.assertEqual((code, stderr), (0, ""), stdout)
        rows = json.loads(stdout)["data"]["findings"]
        self.assertEqual([row["finding"]["review_finding_id"] for row in rows], [second["review_finding_id"], first["review_finding_id"], third["review_finding_id"]])
        self.assertEqual([row["finding"]["resolution_summary"] for row in rows], [groups[0]["resolution"], groups[0]["resolution"], groups[1]["resolution"]])
        originals = {row["review_finding_id"]: row for row in before["review_findings"]}
        for row in rows:
            self.assertEqual(set(row), {"finding", "event"})
            self.assertEqual(row["finding"]["status"], "resolved")
            for key in ("review_finding_id", "review_receipt_id", "severity", "summary", "created_at"):
                self.assertEqual(row["finding"][key], originals[row["finding"]["review_finding_id"]][key])
            self.assertEqual(row["event"]["event_type"], "review_finding_resolved")
        after = self.snapshot_rows()
        self.assertEqual(next(row for row in after["review_findings"] if row["review_finding_id"] == unselected["review_finding_id"]), unselected)
        self.assertEqual(after["review_receipts"], before["review_receipts"])
        self.assertEqual(after["evidence_references"], before["evidence_references"])
        shown = self.success("task", "show", self.task_id)["review_evidence"]
        self.assertEqual(shown["counts"]["open_high"], 1)
        self.assertFalse(shown["gate"]["satisfied"])

    def test_blocking_resolution_requires_new_target_and_fresh_review_receipts(self):
        high = self.finding("Blocking correction", severity="high")
        code, stdout, _ = self.invoke([{"finding_ids": [high["review_finding_id"]], "resolution": "Corrected and verified"}])
        self.assertEqual(code, 0, stdout)
        evidence = self.success("task", "show", self.task_id)["review_evidence"]
        self.assertFalse(evidence["gate"]["satisfied"])
        self.assertEqual(evidence["current_findings"][0]["blocking_reason"], "fresh_review_required")
        still_blocked = review_fixtures.done(self.db, self.repo, self.task_id)
        self.assertEqual(json.loads(still_blocked.stdout)["errors"][0]["code"], "review_finding_unresolved")
        self.set_target()
        self.assertFalse(self.success("task", "show", self.task_id)["review_evidence"]["gate"]["satisfied"])
        self.review("reviewer-a")
        self.review("reviewer-b")
        finished = review_fixtures.done(self.db, self.repo, self.task_id)
        self.assertEqual(finished.returncode, 0, finished.stdout)

    def test_old_generation_finding_resolution_does_not_reset_current_target_or_receipts(self):
        old = self.finding("Old generation blocker", severity="medium")
        self.set_target()
        self.review("reviewer-a")
        self.review("reviewer-b")
        before = self.success("task", "show", self.task_id)
        code, stdout, _ = self.invoke([{"finding_ids": [old["review_finding_id"]], "resolution": "Original correction now verified"}])
        self.assertEqual(code, 0, stdout)
        after = self.success("task", "show", self.task_id)
        self.assertEqual(after["task"]["review_target_generation"], before["task"]["review_target_generation"])
        self.assertEqual(after["review_evidence"]["current_receipts"], before["review_evidence"]["current_receipts"])
        self.assertTrue(after["review_evidence"]["gate"]["satisfied"])

    def test_resolved_replay_rejects_without_overwrite_or_new_events_and_single_mode_survives(self):
        first = self.finding("Previously resolved")
        second = self.finding("Still open")
        original = self.success("review", "finding", "resolve", first["review_finding_id"], "--resolution", "Original immutable resolution")["finding"]
        before = file_snapshot(self.root)
        result = self.invoke([
            {"finding_ids": [second["review_finding_id"]], "resolution": "Must roll back"},
            {"finding_ids": [first["review_finding_id"]], "resolution": "Forbidden replacement"},
        ])
        self.assert_failure(result, "invalid_review_evidence")
        self.assertEqual(file_snapshot(self.root), before)
        self.assertEqual(next(row for row in self.snapshot_rows()["review_findings"] if row["review_finding_id"] == first["review_finding_id"]), original)
        self.assertEqual(self.invoke([{"finding_ids": [second["review_finding_id"]], "resolution": "Corrected after retry"}])[0], 0)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke([{"finding_ids": [second["review_finding_id"]], "resolution": "Corrected after retry"}]), "invalid_review_evidence")
        self.assertEqual(file_snapshot(self.root), before)

    def test_missing_and_wrong_task_finding_leave_all_selected_rows_unchanged(self):
        selected = self.finding("Selected local issue")
        other_id = self.success("task", "add", "--title", "Another owner", "--review-tier", "2")["task"]["task_id"]
        self.success("review", "target", "set", other_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        receipt = review_fixtures.receipt_add(self.db, self.repo, other_id, "other-reviewer")
        self.assertEqual(receipt.returncode, 0, receipt.stdout)
        foreign = self.finding("Other Task issue", task_id=other_id, receipt_id=json.loads(receipt.stdout)["data"]["receipt"]["review_receipt_id"])
        for finding_id, error in (("finding-missing", "not_found"), (foreign["review_finding_id"], "invalid_review_evidence")):
            before = file_snapshot(self.root)
            groups = [{"finding_ids": [selected["review_finding_id"], finding_id], "resolution": "Checked correction"}]
            self.assert_failure(self.invoke(groups), error)
            self.assertEqual(file_snapshot(self.root), before)

    def test_done_task_is_write_locked_even_for_nonblocking_findings(self):
        finding = self.finding("Low finding on done Task")
        done = review_fixtures.done(self.db, self.repo, self.task_id)
        self.assertEqual(done.returncode, 0, done.stdout)
        before = file_snapshot(self.root)
        self.assert_failure(self.invoke([{"finding_ids": [finding["review_finding_id"]], "resolution": "Not allowed on done"}]), "done_task_requires_reopen")
        self.assertEqual(file_snapshot(self.root), before)

    def test_late_event_fault_rolls_back_all_resolutions_but_preserves_prior_success(self):
        prior = self.finding("Prior successful resolution")
        self.success("review", "finding", "resolve", prior["review_finding_id"], "--resolution", "Already confirmed")
        first, second = self.finding("First new issue"), self.finding("Second new issue")
        before = file_snapshot(self.root)
        actual_event = review_service.create_task_event
        calls = []

        def fail_second(connection, **kwargs):
            if kwargs["event_type"] == "review_finding_resolved":
                calls.append(connection.execute("SELECT COUNT(*) FROM review_findings WHERE status='resolved'").fetchone()[0])
                if len(calls) == 2:
                    raise sqlite3.OperationalError("private fault token=never-echo")
            return actual_event(connection, **kwargs)

        groups = [{"finding_ids": [first["review_finding_id"], second["review_finding_id"]], "resolution": "New verified resolution"}]
        with mock.patch.object(review_service, "create_task_event", side_effect=fail_second), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            result = self.invoke(groups, maintenance_enabled=True)
        self.assert_failure(result, "internal_error", exit_code=2)
        self.assertEqual(calls, [2, 3])
        self.assertNotIn("never-echo", result[1] + result[2])
        maintenance.assert_not_called()
        self.assertEqual(file_snapshot(self.root), before)
        self.assertEqual(self.invoke(groups)[0], 0)

    def test_caught_batch_failure_rolls_back_only_its_savepoint_in_caller_transaction(self):
        prior = self.finding("Earlier caller transaction write")
        selected = self.finding("Selected batch write")
        with closing(storage_service.connect_initialized(self.target)) as connection:
            earlier = review_service.resolve_review_finding(
                connection, self.target.project, prior["review_finding_id"],
                resolution="Preserve earlier caller work", database_target=self.target,
            )
            self.assertTrue(connection.in_transaction)
            with self.assertRaises(review_service.TaskRepositoryError) as raised:
                review_service.resolve_review_findings(
                    connection, self.target.project, self.task_id,
                    resolutions=[
                        {"finding_id": selected["review_finding_id"], "resolution": "Batch prefix must roll back"},
                        {"finding_id": "missing-later-finding", "resolution": "Later failure"},
                    ], database_target=self.target,
                )
            self.assertEqual(raised.exception.code, "not_found")
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.execute(
                "SELECT status FROM review_findings WHERE review_finding_id=?", (selected["review_finding_id"],),
            ).fetchone()[0], "open")
            connection.commit()
        rows = self.snapshot_rows()
        self.assertEqual(next(row for row in rows["review_findings"] if row["review_finding_id"] == prior["review_finding_id"]), earlier.finding)
        self.assertEqual(next(row for row in rows["review_findings"] if row["review_finding_id"] == selected["review_finding_id"]), selected)
        self.assertEqual([row["task_event_id"] for row in rows["task_events"] if row["event_type"] == "review_finding_resolved"], [earlier.event["task_event_id"]])

    def test_writer_reread_rejects_concurrent_resolution_and_done(self):
        for change in ("resolution", "done"):
            with self.subTest(change=change):
                finding = self.finding(f"Concurrent {change} issue")
                actual_lock = review_service.lock_and_reread_target_owner
                snapshots = []
                started = False

                def change_then_lock(connection, project, task_id, **kwargs):
                    nonlocal started
                    if not started:
                        started = True
                        self.assertFalse(connection.in_transaction)
                        if change == "resolution":
                            self.success("review", "finding", "resolve", finding["review_finding_id"], "--resolution", "Concurrent original resolution")
                        else:
                            done = review_fixtures.done(self.db, self.repo, self.task_id)
                            self.assertEqual(done.returncode, 0, done.stdout)
                        snapshots.append(file_snapshot(self.root))
                    return actual_lock(connection, project, task_id, **kwargs)

                with mock.patch.object(review_service, "lock_and_reread_target_owner", side_effect=change_then_lock):
                    result = self.invoke([{"finding_ids": [finding["review_finding_id"]], "resolution": "Stale caller resolution"}])
                self.assert_failure(result, "invalid_review_evidence" if change == "resolution" else "done_task_requires_reopen")
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(file_snapshot(self.root), snapshots[0])

    def test_migrated_capture_zero_finding_remains_resolvable(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = review_fixtures.ReviewEvidenceTests()
            repo, db, target, task_id, _receipt_id, finding_id, _foreign = fixture._create_v17_legacy_finding_fixture(Path(temporary))
            with closing(storage_service.connect_existing(db)) as connection:
                storage_service.apply_evidence_ledger_capture_migration(connection)
                storage_service.apply_completion_evidence_bundle_migration(connection)
                storage_service.apply_migrations(connection)
            self.repo, self.db, self.target, self.task_id = repo, db, target, task_id
            with closing(sqlite3.connect(db)) as connection:
                before_capture = connection.execute("SELECT review_target_capture_version,review_target_generation FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            self.assertEqual(before_capture[0], 0)
            code, stdout, stderr = self.invoke([{"finding_ids": [finding_id], "resolution": "Legacy correction verified"}])
            self.assertEqual((code, stderr), (0, ""), stdout)
            with closing(sqlite3.connect(db)) as connection:
                after_capture = connection.execute("SELECT review_target_capture_version,review_target_generation FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            self.assertEqual(after_capture, before_capture)
            self.assertEqual(json.loads(stdout)["data"]["findings"][0]["finding"]["status"], "resolved")

    def test_read_only_invalid_json_and_parse_conflicts_do_not_consume_or_write(self):
        for arguments in ((FINDING_A,), ("--resolution", "Single value"), (FINDING_A, "--resolution", "Single value")):
            with self.subTest(arguments=arguments), mock.patch.object(cli_service, "resolve_context_target", side_effect=AssertionError("parse precedes state")):
                code, stdout, stderr = self.invoke(None, *arguments, stdin=BinaryInput(readable=False))
                self.assertEqual((code, stderr), (1, ""), stdout)
                envelope = json.loads(stdout)
                self.assertEqual(envelope["command"], "parse")
                self.assertEqual(envelope["errors"][0]["code"], "invalid_option_combination")
        for arguments in ((), (FINDING_A,), ("--resolution", "Missing Finding ID")):
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(cli_service, "resolve_context_target", side_effect=AssertionError("manual parse precedes state")), mock.patch.object(sys, "stdin", BinaryInput(readable=False)), redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli_service.main(["review", "finding", "resolve", *arguments, "--json"])
            self.assertEqual((code, stderr.getvalue()), (1, ""), stdout.getvalue())
            envelope = json.loads(stdout.getvalue())
            self.assertEqual((envelope["command"], envelope["errors"][0]["code"]), ("parse", "invalid_argument"))
        before = file_snapshot(self.root)
        with mock.patch.object(cli_service, "connect_initialized", side_effect=AssertionError("no connection")), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            result = self.invoke(None, "--read-only", stdin=BinaryInput(readable=False), maintenance_enabled=True)
            self.assert_failure(result, "invalid_argument")
            for raw in (b"{", b" " * 262145, b"\xff"):
                stream = BinaryInput(raw)
                self.assert_failure(self.invoke(stdin=stream, maintenance_enabled=True), "invalid_review_evidence")
                self.assertEqual(stream.read_sizes, [262145])
            secret = encode(document(self.task_id, groups=[{"finding_ids": [FINDING_A], "resolution": "token=never-retain"}]))
            result = self.invoke(stdin=BinaryInput(secret), maintenance_enabled=True)
            self.assert_failure(result, "privacy_rejected")
            self.assertNotIn("never-retain", result[1] + result[2])
        maintenance.assert_not_called()
        self.assertEqual(file_snapshot(self.root), before)

    def test_one_maintenance_call_after_all_committed_resolutions_and_close(self):
        first, second = self.finding("First"), self.finding("Second")
        opened, observed = [], []
        actual_connect = cli_service.connect_initialized

        def connect(*args, **kwargs):
            connection = actual_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        def maintain(target, outcome):
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute("SELECT 1")
            with closing(sqlite3.connect(self.db)) as reader:
                observed.append((outcome, reader.execute("SELECT COUNT(*) FROM review_findings WHERE status='resolved'").fetchone()[0]))
            raise OSError("private maintenance fault token=never-echo")

        groups = [{"finding_ids": [first["review_finding_id"], second["review_finding_id"]], "resolution": "Verified shared repair"}]
        with mock.patch.object(cli_service, "connect_initialized", side_effect=connect), mock.patch.object(cli_service, "run_post_commit_maintenance", side_effect=maintain) as maintenance:
            code, stdout, stderr = self.invoke(groups, maintenance_enabled=True)
        self.assertEqual((code, stderr), (0, ""), stdout)
        maintenance.assert_called_once()
        self.assertEqual(observed, [(MutationOutcome(state_changed=True, viewer_relevant=True), 2)])
        self.assertEqual(len(json.loads(stdout)["data"]["findings"]), 2)
        self.assertNotIn("never-echo", stdout)

    def test_physical_install_consumes_utf8_batch_without_source_or_config_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve())
            help_result = install.run("review", "finding", "resolve", "--help")
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            for option in ("finding_id", "--resolution", "--from-stdin", "--read-only", "--json"):
                self.assertIn(option, help_result.stdout)

            def successful(*arguments):
                result = install.run(*arguments, "--json")
                self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
                return json.loads(result.stdout)["data"]

            successful("setup")
            task_id = successful("task", "add", "--title", "Physical Finding batch", "--review-tier", "1")["task"]["task_id"]
            successful("review", "target", "set", task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
            receipt_id = successful("review", "receipt", "add", task_id, "--reviewer", "physical-reviewer", "--kind", "independent", "--verdict", "pass", "--reviewer-class", "human", "--model-state", "not_applicable", "--skill-state", "not_applicable", "--context-relation", "external_context")["receipt"]["review_receipt_id"]
            ids = [successful("review", "finding", "add", task_id, "--receipt-id", receipt_id, "--severity", "low", "--summary", title)["finding"]["review_finding_id"] for title in ("First issue", "Second issue")]
            before = file_snapshot(install.project_root, exclude_state=True)
            raw = encode(document(task_id, groups=[{"finding_ids": ids, "resolution": "両方の修正を確認しました"}]))
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(install.entrypoint), "review", "finding", "resolve", "--from-stdin", "--repo", str(install.project_root), "--json"],
                cwd=install.project_root, input=raw, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual((result.returncode, result.stderr), (0, b""), result.stdout.decode("utf-8"))
            rows = json.loads(result.stdout)["data"]["findings"]
            self.assertEqual([row["finding"]["review_finding_id"] for row in rows], ids)
            self.assertEqual([row["finding"]["resolution_summary"] for row in rows], ["両方の修正を確認しました"] * 2)
            self.assertEqual(file_snapshot(install.project_root, exclude_state=True), before)
            self.assertFalse((install.skill_root / "config" / "verification-runner.json").exists())


if __name__ == "__main__":
    unittest.main()
