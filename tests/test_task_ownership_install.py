from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import make_physical_install, refresh_test_manifest
from tests.test_task_ownership import X, Y, UNKNOWN


class TaskOwnershipInstallTests(unittest.TestCase):
    """Real physical install and child CLI; no internal DB or caller override."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name))

    def invoke(self, *args, caller=X, error=None, stdin=None):
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": caller.session_id or ""}):
            if stdin is None:
                result = self.install.run(*args, "--json")
            else:
                result = subprocess.run(
                    [sys.executable, "-I", "-S", str(self.install.entrypoint), *args, "--json"],
                    cwd=self.install.project_root, input=stdin, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, check=False, timeout=30,
                )
        value = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if error is None else 1, value)
        self.assertEqual(value["ok"], error is None, value)
        if error:
            self.assertIn(error, [row["code"] for row in value["errors"]])
        return value

    def test_fresh_setup_maintenance_backup_and_ownerless_reads(self):
        self.invoke("setup", "--read-only", caller=UNKNOWN)
        self.assertFalse(self.install.db_path.exists())
        self.invoke("setup", caller=UNKNOWN)
        self.assertTrue(self.install.viewer_path.is_file())
        task = self.invoke("task", "add", "--title", "Physical owner test",
                           "--status", "in_progress", "--review-tier", "0",
                           "--verification-not-required", "Fixture-only work")["data"]["task"]
        task_id = task["task_id"]
        self.assertEqual(task["ownership"]["owner_session_id"], X.session_id)
        self.assertEqual(task["ownership"]["state"], "owned")
        before = self.install.state_snapshot()
        shown = self.invoke("task", "show", task_id, caller=UNKNOWN)["data"]["task"]
        self.assertIsNone(shown["ownership"]["is_owner"])
        self.invoke("doctor", caller=UNKNOWN)
        self.assertEqual(before, self.install.state_snapshot())
        self.invoke("task", "edit", task_id, "--add-note", "Other caller", caller=Y,
                    error="task_not_owned")
        self.invoke("task", "edit", task_id, "--status", "paused", "--pause-reason", "Explicit handover", caller=Y)
        resumed = self.invoke("task", "edit", task_id, "--status", "in_progress", caller=Y)["data"]["task"]
        self.assertEqual(resumed["ownership"]["execution_id"], task["ownership"]["execution_id"])
        self.assertEqual(resumed["ownership"]["owner_session_id"], Y.session_id)
        self.invoke("setup", caller=UNKNOWN)
        self.assertNotIn('"is_owner"', self.install.viewer_path.read_text(encoding="utf-8"))
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0], 25)
            self.assertEqual(connection.execute("SELECT count(*) FROM task_executions").fetchone()[0], 1)
        backups = tuple((self.install.fixed_root / "backups").glob("taskgov-backup-v1_*.sqlite"))
        self.assertTrue(backups)
        for path in backups:
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0], 25)
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_plan_only_publication_requires_current_owner_without_task_mutation(self):
        from tests.test_m242_runner_plan_edit import draft_blob
        self.install = make_physical_install(self.install.project_root.parent / "plan", git_managed=True)
        ignore = self.install.project_root / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") +
                          "/.agents/skills/task-governance-tool/config/verification-runner.json\n", encoding="utf-8")
        self.invoke("setup")
        task_id = self.invoke("task", "add", "--title", "Plan owner", "--status", "in_progress",
                              "--verification", "Run isolated tests", "--contract-scope", "Plan fixture",
                              "--contract-acceptance", "Only explicit owner publication")["data"]["task"]["task_id"]
        command = ("task", "edit", task_id, "--runner-plan-action", "replace")
        before = self.install.state_snapshot()
        self.invoke(*command, caller=Y, error="task_not_owned", stdin=draft_blob())
        plan = self.install.skill_root / "config" / "verification-runner.json"
        self.assertFalse(plan.exists())
        self.assertEqual(before, self.install.state_snapshot())
        published = self.invoke(*command, stdin=draft_blob())["data"]
        self.assertEqual(published["runner_plan_update"], {"action": "replace", "status": "updated"})
        self.assertTrue(published["task"]["ownership"]["is_owner"])
        self.assertEqual(before, self.install.state_snapshot())
        saved_plan = plan.read_bytes()
        self.invoke("task", "edit", task_id, "--status", "paused", "--pause-reason", "Explicit recovery", caller=Y)
        self.invoke("task", "edit", task_id, "--status", "in_progress", caller=Y)
        self.invoke("task", "edit", task_id, "--runner-plan-action", "disable", error="task_not_owned")
        self.assertEqual(saved_plan, plan.read_bytes())

    def test_public_setup_upgrade_preserves_old_tasks_cycles_and_backup(self):
        # Pin only this physical fixture's predecessor runtime. The candidate
        # upgrade then runs through the unpatched child-process public CLI.
        storage_path = self.install.skill_root / "scripts" / "task_governance_tool" / "storage.py"
        candidate = storage_path.read_text(encoding="utf-8")
        self.assertEqual(candidate.count("SCHEMA_VERSION = 25"), 1)
        storage_path.write_text(candidate.replace("SCHEMA_VERSION = 25", "SCHEMA_VERSION = 23", 1),
                                encoding="utf-8", newline="\n")
        refresh_test_manifest(self.install.skill_root)
        self.assertEqual(self.invoke("setup")["data"]["schema_to"], 23)
        def add(title, status):
            return self.invoke("task", "add", "--title", title, "--status", status,
                               "--review-tier", "0", "--verification-not-required", "Migration fixture")["data"]["task"]["task_id"]
        done = add("Historical complete", "in_progress")
        self.invoke("review", "target", "set", done, "--kind", "diff_fingerprint", "--revision", "sha256:" + "a" * 64)
        self.invoke("review", "receipt", "add", done, "--reviewer", "Mechanical fixture",
                    "--kind", "not_required", "--verdict", "not_required", "--summary", "Fixture-only mechanical review")
        self.invoke("task", "complete", done, "--verification-complete", "--review-complete", "--commit-not-required")
        old_active = [add("Historical active one", "in_progress"), add("Historical active two", "in_progress")]
        pending = add("Historical pending", "review_pending")
        ready = add("Historical ready", "ready")
        preserved = ("tasks", "task_events", "review_receipts", "task_completion_cycles", "completion_evidence_bundles")
        def snapshot(connection):
            return {table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid")) for table in preserved}
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            before = snapshot(connection)
        self.assertTrue(before["completion_evidence_bundles"])
        storage_path.write_text(candidate, encoding="utf-8", newline="\n")
        refresh_test_manifest(self.install.skill_root)
        state_before = self.install.state_snapshot()
        preview = self.invoke("setup", "--read-only", caller=UNKNOWN)["data"]
        self.assertEqual((preview["schema_from"], preview["schema_to"]), (23, 25))
        self.assertEqual(preview["completed_writes"], [])
        self.assertEqual(state_before, self.install.state_snapshot())
        result = self.invoke("setup", caller=UNKNOWN)["data"]
        self.assertEqual((result["schema_from"], result["schema_to"]), (23, 25))
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            self.assertEqual(before, snapshot(connection))
            self.assertEqual(connection.execute("SELECT count(*) FROM task_executions").fetchone()[0], 0)
        for task_id in [*old_active, pending, done, ready]:
            shown = self.invoke("task", "show", task_id)["data"]["task"]
            self.assertEqual(shown["ownership"]["state"], "unknown" if task_id in [*old_active, pending] else "none")
        matching_backup = False
        for path in (self.install.fixed_root / "backups").glob("taskgov-backup-v1_*.sqlite"):
            with closing(sqlite3.connect(path)) as connection:
                if connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 23:
                    matching_backup |= snapshot(connection) == before
        self.assertTrue(matching_backup, "pre-migration managed backup must preserve the exact old business state")
        self.invoke("setup", caller=UNKNOWN)
        with closing(sqlite3.connect(self.install.db_path)) as connection:
            self.assertEqual(before, snapshot(connection))

    def test_plan_preflight_cannot_accept_a_later_reacquisition_by_the_same_caller(self):
        from dataclasses import replace
        from task_governance_tool import verification_runner_plan_edit as plans
        from task_governance_tool.task_values import TaskValidationError
        self.install = make_physical_install(self.install.project_root.parent / "race", git_managed=True)
        ignore = self.install.project_root / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") +
                          "/.agents/skills/task-governance-tool/config/verification-runner.json\n", encoding="utf-8")
        self.invoke("setup")
        task_id = self.invoke("task", "add", "--title", "Plan race", "--status", "in_progress",
                              "--verification", "Run isolated checks", "--contract-scope", "Fixture",
                              "--contract-acceptance", "Exact preflight owner")["data"]["task"]["task_id"]
        target = replace(self.install.target, skill_root=self.install.skill_root, canonical_fixed=True)
        capture = plans._capture_decoded_plan
        def handover(*args, **kwargs):
            self.invoke("task", "edit", task_id, "--status", "paused", "--pause-reason", "Explicit takeover", caller=Y)
            self.invoke("task", "edit", task_id, "--status", "in_progress", caller=X)
            return capture(*args, **kwargs)
        for edit in ({"runner_plan_action": "disable"},
                     {"runner_plan_action": "disable", "verification": "Revised isolated checks"},
                     {"verification": "Revised isolated checks"}):
            with self.subTest(edit=edit):
                with mock.patch.object(plans, "_capture_decoded_plan", side_effect=handover), \
                     mock.patch.object(plans, "_publish_action", side_effect=AssertionError("stale publication")), \
                     self.assertRaises(TaskValidationError) as rejected:
                    plans.edit_task_with_runner_plan(target, task_id, caller=X, **edit)
                self.assertEqual(rejected.exception.code, "task_ownership_changed")
        self.assertFalse((self.install.skill_root / "config" / "verification-runner.json").exists())


if __name__ == "__main__":
    unittest.main()
