"""Candidate schema-25 public CLI/helper tests on disposable physical installs."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import make_physical_install, refresh_test_manifest
from tests.test_review_results import encode, receipt, FINGERPRINT
from tests.test_review_session_repository import OWNER, REVIEWER, SECOND
from task_governance_tool.session_identity import CallerIdentity


class ReviewSessionInstallTests(unittest.TestCase):
    def test_candidate_setup_previews_and_migrates_numerical_store_separately(self):
        from task_governance_tool.state_resolver import observe_current_root
        from task_governance_tool.usage_repository import UsageRepository
        from task_governance_tool.usage_wait_repository import UsageWaitRepository

        path = self.install.db_path.parent / "taskgov-usage.sqlite"
        basis = (self.packet["review_session_context"]["project_id"],
                 observe_current_root(self.root).canonical_path_hash, 1)
        self.assertEqual(UsageWaitRepository(path, *basis).inspect(), "current")
        # Only this test's disposable numerical store is replaced with v1.
        path.unlink()
        old = UsageRepository(path, *basis)
        old.initialize()
        original = path.read_bytes()
        preview = self.cli("setup", "--read-only")["data"]["usage"]
        self.assertEqual(preview["schema_to"], 4)
        self.assertEqual(preview["status"], "migration_required")
        self.assertEqual(preview["planned_writes"], ["usage_migrate"])
        self.assertEqual(path.read_bytes(), original)
        migrated = self.cli("setup")["data"]["usage"]
        self.assertEqual(migrated["status"], "migrated")
        self.assertEqual(migrated["completed_writes"], ["usage_migrate"])
        current = self.cli("setup")["data"]["usage"]
        self.assertEqual(current["status"], "current")
        self.assertEqual(current["completed_writes"], [])

    def test_reviewer_read_save_usage_joins_committed_identity_not_parent(self):
        from task_governance_tool.state_resolver import observe_current_root
        from task_governance_tool.usage_adapter import SourceInput
        from task_governance_tool.usage_attribution_repository import UsageAttributionRepository
        from task_governance_tool.usage_collection import collect_registered, register_source
        from tests.test_usage_attribution import turn
        from tests.test_usage_collection import event, usage
        from tests.test_usage_turn_adapter import tool_record

        read_result = self.invoke(self.install.skill_root / "scripts/review_handoff.py",
            ["read", "--repo", str(self.root), "--packet", self.packet_path, "--role", "independent"], caller=REVIEWER)
        self.assertEqual(read_result.returncode, 0, read_result.stdout or read_result.stderr)
        displayed = json.loads(read_result.stdout)
        output = "reviews/g1/numerical-child.json"
        saved = self.helper("save", "--packet", self.packet_path, "--output", output,
                            caller=REVIEWER, raw=self.original("numerical-child"))
        self.assertEqual(saved["review_session"]["session_id"], REVIEWER.session_id)
        self.helper("submit", "--packet", self.packet_path, output)
        repository = UsageAttributionRepository(self.root / "isolated-usage.sqlite",
            self.packet["review_session_context"]["project_id"], observe_current_root(self.root).canonical_path_hash, 1)
        repository.initialize()
        logs = self.root / "logs"
        logs.mkdir()
        source = SourceInput(REVIEWER.session_id, logs / "review.jsonl", logs, self.root)
        register_source(repository, source, REVIEWER)
        rows = [event("session_meta", id=REVIEWER.session_id, cwd=str(self.root), model_provider="openai")]
        for number in range(1, 6):
            rows.extend((event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                         event("turn_context", turn_id=turn(number), model="fixture-model")))
            if number in (2, 4):
                rows.append(tool_record(displayed if number == 2 else saved, number))
            rows.append(usage(f"review-{number}", thread=REVIEWER.session_id, turn=turn(number)))
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        self.assertNotEqual(collect_registered(repository, source)["status"], "unknown")
        with closing(sqlite3.connect(f"file:{self.install.db_path.as_posix()}?mode=ro", uri=True)) as core:
            core.row_factory = sqlite3.Row
            projected = repository.attribution(core)
        self.assertEqual(projected["tasks"][0]["models"][0]["total_tokens"], 360)
        self.assertNotIn(b"Bound review fixture", repository.path.read_bytes())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name), git_managed=True)
        self.root = self.install.project_root
        storage = self.install.skill_root / "scripts/task_governance_tool/storage.py"
        source = storage.read_text(encoding="utf-8")
        self.assertEqual(source.count("SCHEMA_VERSION = 25"), 1)
        ignore = self.root / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") + "/reviews/\n", encoding="utf-8")
        self.cli("setup")
        self.started = self.cli("task", "add", "--title", "Bound review fixture", "--status", "in_progress",
            "--review-tier", "2", "--verification-not-required", "Isolated protocol fixture")
        self.task = self.started["data"]["task"]["task_id"]
        prepared = self.helper("prepare", "--directory", "reviews/g1", "target", self.task,
                               "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        self.handoff = prepared["handoff"]
        self.packet_path = prepared["handoff"]["packet_path"]
        self.packet = json.loads((self.root / self.packet_path).read_bytes())

    def invoke(self, entry, args, *, caller=OWNER, raw=None):
        environment = {**os.environ, "CODEX_THREAD_ID": caller.session_id or "",
                       "CODEX_SESSION_ID": OWNER.session_id}
        environment.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, "-I", "-S", str(entry), *args], cwd=self.root,
            env=environment, input=raw, capture_output=True, check=False, timeout=30)

    def cli(self, *args, caller=OWNER, raw=None, error=None):
        result = self.invoke(self.install.entrypoint, [*args, "--json"], caller=caller, raw=raw)
        value = json.loads(result.stdout)
        self.assertEqual(value["ok"], error is None, value)
        if error is None:
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, value)
            self.assertIn(error, [item["code"] for item in value["errors"]], value)
        return value

    def helper(self, *args, caller=OWNER, raw=None, error=False):
        entry = self.install.skill_root / "scripts/review_handoff.py"
        result = self.invoke(entry, [args[0], "--repo", str(self.root), *args[1:]], caller=caller, raw=raw)
        value = json.loads(result.stdout)
        self.assertEqual(value["ok"], not error, value)
        self.assertEqual(result.returncode == 0, not error, value)
        return value

    def original(self, key="reviewer", findings=()):
        document = copy.deepcopy(self.packet["result_template"])
        document["receipts"] = [receipt(key, findings=findings)]
        return b"\n " + json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"

    def save(self, name, caller, *, findings=()):
        original = self.original(name, findings)
        output = "reviews/g1/" + name + ".json"
        self.helper("save", "--packet", self.packet_path, "--output", output, caller=caller, raw=original)
        self.assertEqual((self.root / output).read_bytes(), original)
        metadata = json.loads((self.root / (output + ".session.json")).read_bytes())
        self.assertEqual(metadata["session_id"], caller.session_id)
        self.assertEqual(metadata["original_result_digest"], hashlib.sha256(original).hexdigest())
        return output

    def counts(self):
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
                "review_receipts", "review_receipt_sessions", "review_findings", "task_events"))

    def test_parent_forwards_two_real_sessions_and_preserves_bytes_and_no_slot(self):
        other = self.cli("task", "add", "--title", "Reviewer own work", "--kind", "optional",
            "--status", "in_progress", "--review-tier", "0", "--verification-not-required", "Fixture", caller=REVIEWER)["data"]["task"]
        first = self.save("child", REVIEWER)
        second = self.save("root", SECOND)
        result = self.helper("submit", "--packet", self.packet_path, first, second)
        self.assertTrue(result["data"]["review_gate"]["satisfied"], result)
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            rows = connection.execute("SELECT session_id, execution_id, binding_source FROM review_receipt_sessions").fetchall()
            self.assertEqual({row[0] for row in rows}, {REVIEWER.session_id, SECOND.session_id})
            self.assertEqual({row[1] for row in rows}, {self.packet["review_session_context"]["execution_id"]})
            self.assertEqual({row[2] for row in rows}, {"handoff"})
            self.assertEqual(connection.execute("SELECT count(*) FROM task_ownership").fetchone()[0], 2)
        shown = self.cli("task", "show", other["task_id"], caller=REVIEWER)["data"]["task"]
        self.assertEqual(shown["ownership"], other["ownership"])
        self.cli("task", "edit", self.task, "--add-note", "No reviewer edit privilege", caller=REVIEWER, error="task_not_owned")

    def resume_with_retained_submission(self):
        command = self.handoff["submit_command"]
        shell = (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
                 if os.name == "nt" else ["/bin/sh", "-c", command])
        return subprocess.run(shell, cwd=self.root, capture_output=True, check=False,
                              env={**os.environ, "CODEX_THREAD_ID": OWNER.session_id}, timeout=30)

    def check_resume_decisions(self, *, requested):
        # Reviewer work finishes before resumption. Discard save responses so
        # the parent has only its prepared handoff and all-ended indication.
        originals = []
        for index, caller in enumerate((REVIEWER, SECOND)):
            document = copy.deepcopy(self.packet["result_template"])
            findings = ([{"severity": severity, "summary": f"fixture.py:{line} {severity} review finding"}
                         for line, severity in enumerate(("low", "medium", "high"), 1)]
                        if requested and index == 1 else [])
            document["receipts"] = [receipt(f"resume-{index}",
                verdict="changes_requested" if requested and index == 1 else "pass", findings=findings)]
            original = encode(document)
            path = self.handoff["review_requests"][index]["result_path"]
            saved = self.invoke(self.install.skill_root / "scripts/review_handoff.py",
                ["save", "--repo", str(self.root), "--packet", self.packet_path, "--output", path],
                caller=caller, raw=original)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            originals.append((path, original))
        # The resumed parent executes the exact retained command, without a
        # list/read/show operation, regenerated paths or original JSON display.
        result = self.resume_with_retained_submission()
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        registered = json.loads(result.stdout)
        self.assertTrue(registered["ok"])
        data = registered["data"]
        self.assertEqual([r["receipt"]["verdict"] for r in data["receipts"]],
                         ["pass", "changes_requested" if requested else "pass"])
        self.assertEqual(data["review_gate"]["satisfied"], not requested)
        self.assertEqual(data["review_gate"]["basis"], {"task_id": self.task,
            "contract_revision": self.packet["contract"]["revision"], "review_target": self.packet["review_target"]})
        actual = [f["finding"] for r in data["receipts"] for f in r["findings"]]
        self.assertEqual([f["severity"] for f in actual], ["low", "medium", "high"] if requested else [])
        for entry in data["receipts"]:
            self.assertTrue(entry["receipt"]["summary"])
            for row in entry["findings"]:
                self.assertTrue(row["finding"]["review_finding_id"])
                self.assertEqual(row["finding"]["review_receipt_id"], entry["receipt"]["review_receipt_id"])
        for path, original in originals:
            self.assertEqual((self.root / path).read_bytes(), original)
        before = self.counts()
        replay = self.resume_with_retained_submission()  # Explicit rejection test, never a normal retry.
        self.assertNotEqual(replay.returncode, 0)
        self.assertEqual(json.loads(replay.stdout)["errors"][0]["code"], "review_receipt_already_recorded")
        self.assertEqual(self.counts(), before)

    def test_all_ended_resume_registers_pass_without_parent_save_confirmation_reads(self):
        self.check_resume_decisions(requested=False)

    def test_all_ended_resume_registers_requested_changes_and_all_severities_for_judgment(self):
        self.check_resume_decisions(requested=True)

    def test_direct_reviewer_binding_alias_rejection_and_parent_legacy_unbound(self):
        first = self.cli("review", "result", "add", self.task, raw=self.original("direct"), caller=REVIEWER)
        receipt_id = first["data"]["receipts"][0]["receipt"]["review_receipt_id"]
        before = self.counts()
        self.cli("review", "result", "add", self.task, raw=self.original("alias"), caller=REVIEWER,
                 error="review_receipt_already_recorded")
        self.assertEqual(self.counts(), before)
        self.cli("review", "finding", "add", self.task, "--receipt-id", receipt_id, "--severity", "low",
                 "--summary", "fixture.py:1 Reviewer-owned finding", caller=REVIEWER)
        self.cli("review", "finding", "add", self.task, "--receipt-id", receipt_id, "--severity", "low",
                 "--summary", "fixture.py:2 Other reviewer", caller=SECOND, error="review_receipt_mismatch")
        self.cli("review", "result", "add", self.task, raw=self.original("legacy-parent"))
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            rows = connection.execute("SELECT session_id, binding_source FROM review_receipt_sessions").fetchall()
            self.assertEqual(rows, [(REVIEWER.session_id, "direct")])

    def test_single_receipt_cli_captures_actual_reviewer(self):
        self.cli("review", "receipt", "add", self.task, "--reviewer", "single",
                 "--kind", "independent", "--verdict", "pass",
                 "--reviewer-class", "human", "--model-state", "not_applicable",
                 "--skill-state", "not_applicable", "--context-relation", "external_context",
                 "--review-profile", "general", "--review-lens", "correctness",
                 "--review-method", "review_packet_inspection", caller=REVIEWER)
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            rows = connection.execute("SELECT session_id, binding_source FROM review_receipt_sessions").fetchall()
            self.assertEqual(rows, [(REVIEWER.session_id, "direct")])

    def test_missing_tampered_and_stale_binding_refused_without_registration(self):
        output = self.save("reviewer", REVIEWER)
        path = self.root / (output + ".session.json")
        original = path.read_bytes()
        metadata = json.loads(original)
        before = self.counts()
        path.unlink()
        missing = self.helper("submit", "--packet", self.packet_path, output, error=True)
        self.assertEqual(missing["registration_status"], "not_started")
        self.assertEqual(missing["code"], "handoff_input_missing")
        for field, value in (("original_result_digest", "0" * 64), ("execution_id", "tg_execution_" + "f" * 16),
                             ("session_id", "invalid"), ("contract_revision", metadata["contract_revision"] + 1)):
            changed = {**metadata, field: value}
            path.write_bytes(encode(changed))
            rejected = self.helper("submit", "--packet", self.packet_path, output, error=True)
            self.assertEqual(rejected["registration_status"], "not_started")
            self.assertEqual(self.counts(), before)
        path.write_bytes(original)
        self.cli("review", "target", "set", self.task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        before = self.counts()
        self.helper("submit", "--packet", self.packet_path, output, error=True)
        self.assertEqual(self.counts(), before)

    def test_late_core_failure_rolls_back_binding_and_retains_originals(self):
        output = self.save("reviewer", REVIEWER, findings=[{"severity": "low", "summary": "fixture.py:1 Finding"}])
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            connection.execute("CREATE TRIGGER test_late_review_failure BEFORE INSERT ON task_events "
                "WHEN NEW.event_type='review_receipt_added' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
            connection.commit()
        before = self.counts()
        self.helper("submit", "--packet", self.packet_path, output, error=True)
        self.assertEqual(self.counts(), before)
        self.assertTrue((self.root / output).is_file())
        self.assertTrue((self.root / (output + ".session.json")).is_file())

    def test_corrupt_usage_does_not_prevent_registration_or_completion(self):
        usage = self.install.fixed_root / "taskgov-usage.sqlite"
        usage.write_bytes(b"intentionally invalid isolated numerical store")
        for key, caller in (("first", REVIEWER), ("second", SECOND)):
            self.cli("review", "result", "add", self.task, raw=self.original(key), caller=caller)
        self.cli("task", "complete", self.task, "--verification-complete", "--review-complete", "--commit-not-required")
        self.assertEqual(usage.read_bytes(), b"intentionally invalid isolated numerical store")
        self.assertEqual(self.cli("task", "show", self.task)["data"]["task"]["status"], "done")

    def test_absent_usage_does_not_prevent_registration_or_completion(self):
        usage = self.install.fixed_root / "taskgov-usage.sqlite"
        # Explicit setup initialized this disposable store; remove only that
        # fixture file to exercise absence during ordinary core operations.
        usage.unlink()
        self.assertFalse(usage.exists())
        for key, caller in (("first", REVIEWER), ("second", SECOND)):
            self.cli("review", "result", "add", self.task, raw=self.original(key), caller=caller)
        self.cli("task", "complete", self.task, "--verification-complete", "--review-complete", "--commit-not-required")
        self.assertFalse(usage.exists())

    def test_exclusively_locked_usage_does_not_prevent_registration_or_completion(self):
        usage = self.install.fixed_root / "taskgov-usage.sqlite"
        with closing(sqlite3.connect(usage)) as connection:
            connection.execute("CREATE TABLE isolated_marker(value TEXT)")
            connection.commit()
            before = usage.read_bytes()
            connection.execute("BEGIN EXCLUSIVE")
            try:
                for key, caller in (("first", REVIEWER), ("second", SECOND)):
                    self.cli("review", "result", "add", self.task, raw=self.original(key), caller=caller)
                self.cli("task", "complete", self.task, "--verification-complete", "--review-complete", "--commit-not-required")
            finally:
                connection.rollback()
            self.assertEqual(usage.read_bytes(), before)

    def test_unknown_reviewer_can_read_but_cannot_save_a_bound_original(self):
        unknown = CallerIdentity(None)
        before = self.counts()
        result = self.invoke(self.install.skill_root / "scripts/review_handoff.py",
            ["read", "--repo", str(self.root), "--packet", self.packet_path, "--role", "independent"], caller=unknown)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.assertEqual(json.loads(result.stdout)["context_check"], "matched_at_read")
        output = "reviews/g1/unknown.json"
        self.helper("save", "--packet", self.packet_path, "--output", output,
                    raw=self.original(), caller=unknown, error=True)
        self.assertFalse((self.root / output).exists())
        self.assertFalse((self.root / (output + ".session.json")).exists())
        self.assertEqual(self.counts(), before)

    def test_public_cli_does_not_downgrade_malformed_machine_binding(self):
        before = self.counts()
        raw = encode({"format": "taskgov-review-session-handoff-v1",
                      "items": [{"original_base64": "e30=", "binding": {}}]})
        self.cli("review", "result", "add", self.task, raw=raw, error="invalid_review_evidence")
        self.assertEqual(self.counts(), before)

    def test_concurrent_public_aliases_cannot_create_two_passes(self):
        def submit(key):
            return self.invoke(self.install.entrypoint,
                ["review", "result", "add", self.task, "--json"], caller=REVIEWER, raw=self.original(key))
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [json.loads(result.stdout) for result in pool.map(submit, ("first-name", "second-name"))]
        self.assertEqual(sum(value["ok"] for value in responses), 1, responses)
        self.assertIn(next(value for value in responses if not value["ok"])["errors"][0]["code"],
                      {"review_receipt_already_recorded", "database_busy"})
        self.assertEqual(self.counts()[:2], (1, 1))


if __name__ == "__main__":
    unittest.main()
