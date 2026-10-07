"""Isolated installed helper reads; no live Task database or host operations."""

from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys
from unittest import mock

from tests.m14_test_support import file_snapshot
from tests.test_review_handoff_preparation import PreparationFixture
from tests.test_review_results import FINGERPRINT
from task_governance_tool import review_wait_basis as service
from task_governance_tool import review_wait_basis_repository as repository
from task_governance_tool.state_resolver import resolve_project_state
from tools.review_wait_basis import PublicTaskBasisReader


class WaitBasisHandoffTests(PreparationFixture):
    def setUp(self):
        super().setUp()
        self.task_id = self.task()
        result, _ = self.prepare(self.task_id)
        self.assertEqual(result.returncode, 0, result.stdout)

    def read(self, *, task_id=None, extra=(), environment=None):
        result = subprocess.run([sys.executable, "-I", "-B", str(self.helper), "wait-basis",
            "--repo=" + str(self.root), "--task-id=" + (task_id or self.task_id), *extra],
            stdin=subprocess.DEVNULL, capture_output=True, check=False,
            env=environment, cwd=self.root)
        return result, json.loads(result.stdout)

    def test_closed_structural_basis_without_identity_or_any_file_write(self):
        env = dict(os.environ)
        env.pop("CODEX_THREAD_ID", None)
        before = file_snapshot(self.root)
        result, value = self.read(environment=env)
        self.assertEqual(result.returncode, 0, value)
        self.assertEqual(set(value), {"ok", "status", "basis"})
        self.assertEqual(value["status"], "review_wait_basis")
        basis = value["basis"]
        self.assertEqual(basis["task_id"], self.task_id)
        self.assertEqual(basis["task_status"], "review_pending")
        self.assertEqual(basis["contract_revision"], 0)
        self.assertEqual(basis["target_value"], FINGERPRINT)
        self.assertGreater(basis["ownership_generation"], 0)
        self.assertEqual(len(basis["project_path_hash"]), 64)
        self.assertTrue(basis["artifact_manifest_id"].startswith("tg_artifact_manifest_"))
        self.assertEqual(before, file_snapshot(self.root))

    def test_provider_uses_real_public_helper_and_actual_owner_comparison(self):
        _, value = self.read()
        reader = PublicTaskBasisReader(self.root, self.task_id, value["basis"]["parent_thread_id"],
                                      "wait-1", helper=self.helper)
        observed = reader()
        self.assertEqual(observed.artifact_manifest_id, value["basis"]["artifact_manifest_id"])
        self.assertEqual(observed.ownership_generation, value["basis"]["ownership_generation"])

    def test_reads_original_task_even_after_another_task_added_and_new_generation(self):
        _, old = self.read()
        other = self.task()
        self.assertNotEqual(other, self.task_id)
        self.cli("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        result, new = self.read()
        self.assertEqual(result.returncode, 0, new)
        self.assertEqual(new["basis"]["task_id"], self.task_id)
        self.assertEqual(new["basis"]["target_generation"], old["basis"]["target_generation"] + 1)
        self.assertNotEqual(new["basis"]["artifact_manifest_id"], old["basis"]["artifact_manifest_id"])

    def test_inactive_missing_and_unprepared_tasks_fail_without_any_write(self):
        no_target = self.task()
        self.cli("task", "edit", self.task_id, "--status", "blocked", "--blocked-reason", "Fixture paused work")
        before = file_snapshot(self.root)
        for task, code in [(self.task_id, "wait_basis_inactive"),
                           (no_target, "wait_basis_target_required"), ("absent-task", "wait_basis_unavailable")]:
            result, value = self.read(task_id=task)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(value["code"], code)
            self.assertNotIn("basis", value)
        self.assertEqual(before, file_snapshot(self.root))

    def test_no_caller_identity_packet_db_or_timer_options(self):
        before = file_snapshot(self.root)
        for extra in [("--session-id=untrusted",), ("--db=other.sqlite",),
                      ("--packet=packet.json",), ("--timer-id=timer",), ("--from-stdin",)]:
            result, value = self.read(extra=extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(value["code"], "handoff_invalid_arguments")
        self.assertEqual(before, file_snapshot(self.root))

    def test_numerical_absent_corrupt_and_disabled_do_not_affect_basis(self):
        target = resolve_project_state(skill_root=self.install.skill_root, repo=self.root).target
        path = target.resolved_usage_database
        if path.exists():
            path.unlink()
        choices = self.install.skill_root / "config/setup-features.json"
        choices.parent.mkdir(exist_ok=True)
        for content, enabled in [(None, False), (b"not a numerical database", False),
                                 (b"not a numerical database", True)]:
            choices.write_text(json.dumps({"schema_version": 1, "choices": {"usage_collection": enabled}}),
                               encoding="utf-8")
            if content is not None:
                path.write_bytes(content)
            before = file_snapshot(self.root)
            result, value = self.read()
            self.assertEqual(result.returncode, 0, value)
            self.assertEqual(before, file_snapshot(self.root))

    def test_one_retained_snapshot_validates_manifest_reference_and_closes_on_failure(self):
        resolution = resolve_project_state(skill_root=self.install.skill_root, repo=self.root,
                                           retain_read_connection=True)
        core = resolution.read_connection
        self.assertTrue(core.in_transaction)
        before = file_snapshot(self.root)
        with mock.patch.object(service, "inspect_project_scope") as inspect, \
             mock.patch.object(service, "resolve_project_state", return_value=resolution), \
             mock.patch.object(repository, "validate_manifest_evidence_references", side_effect=ValueError("PRIVATE")) as validator:
            inspect.return_value.first_issue.return_value = None
            inspect.return_value.scope.skill_root = self.install.skill_root
            inspect.return_value.scope.canonical_repo = self.root
            value = service.wait_basis(self.root, task_id=self.task_id)
        self.assertFalse(value["ok"])
        self.assertEqual(value["code"], "wait_basis_unavailable")
        self.assertNotIn("PRIVATE", json.dumps(value))
        validator.assert_called_once()
        with self.assertRaises(sqlite3.ProgrammingError):
            core.execute("SELECT 1")
        self.assertEqual(before, file_snapshot(self.root))

    def test_repository_refuses_non_query_only_or_no_transaction_connection(self):
        with closing(sqlite3.connect(":memory:")) as core:
            with self.assertRaises(repository.WaitBasisError):
                repository.read_wait_basis(core, project_id="x", task_id="x")
            core.execute("BEGIN")
            with self.assertRaises(repository.WaitBasisError):
                repository.read_wait_basis(core, project_id="x", task_id="x")

    def test_corrupt_manifest_or_reference_is_rejected_without_repair_or_disclosure(self):
        target = resolve_project_state(skill_root=self.install.skill_root, repo=self.root).target
        original = target.db_path.read_bytes()
        for table in ("artifact_manifests", "evidence_references"):
            target.db_path.write_bytes(original)
            # Test-only corruption with schema restored: exercise content checks,
            # rather than merely removing the immutable-row protection trigger.
            with closing(sqlite3.connect(target.db_path)) as core:
                triggers = core.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                                        (table,)).fetchall()
                for name, _ in triggers:
                    core.execute('DROP TRIGGER "' + name + '"')
                core.execute("UPDATE " + table + " SET digest=?", ("sha256:" + "f" * 64,))
                for _, sql in triggers:
                    core.execute(sql)
                core.commit()
            before = file_snapshot(self.root)
            result, value = self.read()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(value["code"], "wait_basis_unavailable")
            self.assertNotIn("basis", value)
            self.assertEqual(before, file_snapshot(self.root))

    def test_connection_close_failure_cannot_be_reported_as_success(self):
        resolution = resolve_project_state(skill_root=self.install.skill_root, repo=self.root,
                                           retain_read_connection=True)
        actual_close = resolution.read_connection.close
        core = mock.Mock(wraps=resolution.read_connection)
        def close_then_fail():
            actual_close()
            raise OSError("PRIVATE_CLOSE")
        core.close.side_effect = close_then_fail
        from dataclasses import replace
        broken = replace(resolution, read_connection=core)
        with mock.patch.object(service, "inspect_project_scope") as inspect, \
             mock.patch.object(service, "resolve_project_state", return_value=broken), \
             mock.patch.object(service, "read_wait_basis", return_value={}):
            inspect.return_value.first_issue.return_value = None
            inspect.return_value.scope.skill_root = self.install.skill_root
            inspect.return_value.scope.canonical_repo = self.root
            value = service.wait_basis(self.root, task_id=self.task_id)
        self.assertEqual(value["code"], "wait_basis_unavailable")
        self.assertNotIn("PRIVATE_CLOSE", json.dumps(value))
