"""Installed decision markers are structural, caller-owned and never core writers."""

import copy
import json
import os
import subprocess
import sys
from unittest import mock

from tests.m14_test_support import file_snapshot
from tests.test_review_handoff_preparation import PreparationFixture
from tests.test_review_results import FINGERPRINT
from task_governance_tool import usage_lifecycle
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.usage_evidence_service import repository_for
from task_governance_tool.usage_repository import UsageRepository
from task_governance_tool.usage_values import UsageError


class ReviewWaitHandoffTests(PreparationFixture):
    def setUp(self):
        super().setUp()
        self.task_id = self.task()
        completed, prepared = self.prepare(self.task_id)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.handoff = prepared["handoff"]
        self.packet_path = self.handoff["packet_path"]
        self.packet_bytes = (self.root / self.packet_path).read_bytes()
        self.packet = json.loads(self.packet_bytes)
        self.target = resolve_project_state(skill_root=self.install.skill_root, repo=self.root).target
        self.assertIsNotNone(self.target)

    def decision(self, *, raw=None, environment=None, extra=()):
        source = ["--packet=" + self.packet_path] if raw is None else ["--from-stdin"]
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(self.helper), "wait-ended",
             "--repo=" + str(self.root), *source, *extra],
            input=raw, capture_output=True, cwd=self.root, check=False,
            env=self.review_environment(9) if environment is None else environment,
        )
        return result, json.loads(result.stdout)

    def assert_success(self, result, value):
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.assertEqual(set(value), {"ok", "status", "review_wait"})
        self.assertTrue(value["ok"])
        self.assertEqual(value["status"], "review_wait_ended")
        metadata = value["review_wait"]
        self.assertEqual(set(metadata), {"version", "session_id", "project_id", "task_id",
                         "execution_id", "contract_revision", "review_target", "artifact_manifest_id"})
        self.assertEqual(metadata["version"], 1)
        self.assertEqual(metadata["session_id"], self.review_environment(9)["CODEX_THREAD_ID"])
        self.assertEqual(metadata["task_id"], self.task_id)
        self.assertEqual(metadata["project_id"], self.packet["review_session_context"]["project_id"])
        self.assertEqual(metadata["execution_id"], self.packet["review_session_context"]["execution_id"])
        self.assertEqual(metadata["review_target"], self.packet["review_target"])
        self.assertEqual(metadata["contract_revision"], self.packet["contract"]["revision"])
        self.assertTrue(metadata["artifact_manifest_id"].startswith("tg_artifact_manifest_"))

    def test_file_and_complete_stdin_emit_same_metadata_without_core_write(self):
        before = file_snapshot(self.root)
        first, marker = self.decision()
        self.assert_success(first, marker)
        second, direct = self.decision(raw=self.packet_bytes)
        self.assert_success(second, direct)
        self.assertEqual(marker, direct)
        after = file_snapshot(self.root)
        changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
        self.assertLessEqual(changed, {".taskgov/current/taskgov-usage.sqlite"})
        repository = repository_for(self.target)
        self.assertIn(marker["review_wait"]["session_id"], repository.registered_sources()[0])

    def test_legacy_marker_accepts_quoted_paths_without_normal_handoff_duty(self):
        self.packet_path = "reviews/quoted's 日本語/packet.json"
        packet_file = self.root / self.packet_path
        packet_file.parent.mkdir()
        packet_file.write_bytes(self.packet_bytes)
        invoked, marker = self.decision()
        self.assert_success(invoked, marker)
        for request in self.handoff["review_requests"]:
            self.assertNotIn("wait-ended", request["request"])
            self.assertEqual(set(request), {"result_path", "read_command", "save_command", "request"})
        self.assertNotIn("wait_ended_command", self.handoff)

    def test_missing_or_malformed_actual_caller_cannot_emit_marker(self):
        for identity in (None, "untrusted session text"):
            with self.subTest(identity=identity):
                environment = dict(os.environ)
                environment.pop("CODEX_THREAD_ID", None)
                if identity is not None:
                    environment["CODEX_THREAD_ID"] = identity
                result, value = self.decision(environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(value["code"], "session_identity_required")
                self.assertNotIn("review_wait", value)

    def test_no_user_supplied_identity_verdict_or_schedule_inputs(self):
        for args in (("--turn-id", "anything"), ("--session-id", "anything"),
                     ("--verdict", "pass"), ("--seconds-remaining", "90"), ("--from-stdin",)):
            with self.subTest(args=args):
                result, value = self.decision(extra=args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(value["code"], "handoff_invalid_arguments")

    def test_stale_structural_basis_rejected_without_registration(self):
        changes = (
            lambda p: p["review_session_context"].update(project_id="another-project"),
            lambda p: p["review_session_context"].update(execution_id="tg_execution_0123456789abcdef"),
            lambda p: p["contract"].update(scope="not the current Contract"),
            lambda p: p["task"].update(title="not the current Task"),
            lambda p: p.pop("review_session_context"),
        )
        before = file_snapshot(self.root)
        for change in changes:
            with self.subTest(change=change):
                packet = copy.deepcopy(self.packet)
                change(packet)
                result, value = self.decision(raw=json.dumps(packet).encode())
                self.assertNotEqual(result.returncode, 0, value)
                self.assertNotIn("review_wait", value)
        self.assertEqual(before, file_snapshot(self.root))

    def test_old_target_packet_cannot_be_rebound_to_new_generation(self):
        self.cli("review", "target", "set", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        before = file_snapshot(self.root)
        result, value = self.decision()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("review_wait", value)
        self.assertEqual(before, file_snapshot(self.root))

    def test_incomplete_or_oversized_stdin_rejected_without_state_change(self):
        before = file_snapshot(self.root)
        for raw in (b"{}", self.packet_bytes[:-2], b" " * 32769):
            result, value = self.decision(raw=raw)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("review_wait", value)
        self.assertEqual(before, file_snapshot(self.root))

    def test_nonignored_saved_packet_rejected_while_direct_transport_remains_available(self):
        unignored = self.root / "unignored.json"
        unignored.write_bytes(self.packet_bytes)
        self.packet_path = "unignored.json"
        result, value = self.decision()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(value["code"], "handoff_ignore_required")
        result, value = self.decision(raw=self.packet_bytes)
        self.assert_success(result, value)

    def test_absent_corrupt_or_old_numerical_store_does_not_reject_declaration_or_repair_store(self):
        target = self.target
        path = target.resolved_usage_database
        original = path.read_bytes()
        for state in ("absent", "corrupt", "old"):
            with self.subTest(state=state):
                if path.exists():
                    path.unlink()
                if state == "corrupt":
                    path.write_bytes(b"not a numerical database")
                elif state == "old":
                    UsageRepository(path, target.project.project_id, target.binding_path_hash,
                                    target.binding_generation).initialize()
                before = file_snapshot(self.root)
                result, value = self.decision()
                self.assert_success(result, value)
                self.assertEqual(before, file_snapshot(self.root))
        path.write_bytes(original)

    def test_registry_failure_is_best_effort_without_log_collection(self):
        from task_governance_tool.session_identity import CallerIdentity
        caller = CallerIdentity(self.review_environment(9)["CODEX_THREAD_ID"])
        with mock.patch.object(usage_lifecycle, "_target", return_value=self.install.target), \
             mock.patch.object(usage_lifecycle, "repository_for") as factory, \
             mock.patch.object(usage_lifecycle, "read_batch") as read:
            factory.return_value.register_session.side_effect = UsageError()
            usage_lifecycle.register_wait_supervisor(self.root, caller)
            factory.return_value.register_session.assert_called_once_with(caller)
            read.assert_not_called()
