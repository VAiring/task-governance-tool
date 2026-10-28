"""Command publication and MCP lifecycle keep backup work off the caller."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import shutil
import unittest
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current
from tests.test_routine_backup import timestamp, metadata
from task_governance_tool import async_backup as runtime
from task_governance_tool import async_backup_repository as repository
from task_governance_tool import async_backup_state as state
from task_governance_tool import maintenance, storage
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession


class AsyncBackupIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.install, self.target, _ = _setup_current(self.directory)

    def test_setup_fixture_canonicalizes_root_before_service_admission(self):
        fixture_root = self.directory / "aliased-fixture"
        component = fixture_root / "component"
        component.mkdir(parents=True)
        install, target, _ = _setup_current(component / ".." / "new-fixture")
        self.assertEqual(install.project_root, fixture_root.resolve() / "new-fixture" / "project")
        admitted = repository.read_service_target(install.skill_root, repo=install.project_root)
        self.assertEqual(admitted.db_path, target.db_path)

    def test_policy_request_does_not_repeat_schema_or_history_validation(self):
        statements = []
        real = repository.connect_readonly
        def connect(*args, **kwargs):
            connection = real(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with mock.patch.object(repository, "connect_readonly", side_effect=connect) as opened, \
             mock.patch.object(repository, "_validate_current_schema_contract", side_effect=AssertionError("schema audit")), \
             mock.patch.object(repository, "read_project_binding_state", side_effect=AssertionError("binding history")):
            policy = repository.read_request_policy(self.target)
        self.assertTrue(policy.enabled)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(len(statements), 2)
        self.assertIn("LIMIT 2", statements[0])
        self.assertIn("project_maintenance", statements[1])

    def test_policy_rejects_wrong_binding_without_creating_runtime(self):
        with self.assertRaises(storage.StorageError):
            repository.read_request_policy(replace(self.target, binding_generation=99))
        self.assertFalse(state.runtime_root(self.target).exists())

    def test_due_command_publishes_only_and_current_command_is_silent(self):
        with mock.patch.object(maintenance, "request_backup", return_value=SimpleNamespace(code="requested")) as publish, \
             mock.patch.object(runtime, "launch_backup", side_effect=AssertionError("CLI launched")):
            self.assertEqual(maintenance._request_due_backup(self.target, timestamp(1)), "requested")
            publish.assert_called_once_with(self.target)
            current = repository.read_control(self.target)
            repository.commit_inventory(self.target, current, (metadata(1, 0, 3),), success=True)
            publish.reset_mock()
            self.assertEqual(maintenance._request_due_backup(self.target, timestamp(1)), "current")
            publish.assert_not_called()

    def test_missing_service_warns_but_does_not_launch(self):
        with mock.patch.object(maintenance, "run_routine_evidence_projection", return_value=SimpleNamespace(code="current")), \
             mock.patch.object(runtime, "_spawn", side_effect=AssertionError("CLI spawned")):
            warnings = maintenance.run_post_commit_maintenance(self.target,
                maintenance.MutationOutcome(True, False), observed_at=timestamp(1))
        self.assertEqual([item["code"] for item in warnings], ["backup_request_deferred"])

    def test_incompatible_service_cannot_spawn_and_current_does_not_hash(self):
        admit = mock.Mock(return_value=False)
        with mock.patch.object(runtime, "_spawn", side_effect=AssertionError("incompatible spawn")):
            self.assertEqual(runtime.launch_backup(self.target, _admit_launch=admit).code, "deferred")
            admit.assert_called_once_with()
            current = repository.read_control(self.target)
            repository.commit_inventory(self.target, current, (metadata(1, 0, 3),), success=True)
            admit.reset_mock()
            self.assertEqual(runtime.launch_backup(self.target, observed_at=timestamp(1), _admit_launch=admit).code, "current")
            admit.assert_not_called()

    def test_startup_has_no_task_audit_and_rejects_foreign_binding(self):
        with mock.patch.object(storage, "validate_current_schema21_admitted_rows", side_effect=AssertionError("Task audit")):
            target = repository.read_service_target(self.install.skill_root, repo=self.install.project_root)
        self.assertEqual(target.project.project_id, self.target.project.project_id)
        foreign = self.directory / "other-project"
        copied = foreign / ".taskgov/current/taskgov.sqlite"
        copied.parent.mkdir(parents=True)
        shutil.copyfile(self.target.db_path, copied)
        with self.assertRaises(storage.StorageError):
            repository.read_service_target(self.install.skill_root, repo=foreign)

    def test_service_initialization_is_explicit_and_close_does_not_join_backup(self):
        from task_governance_tool import async_backup_service
        from task_governance_tool.review_wait_runtime import project_server
        config = ProjectConfig(self.install.project_root, self.directory / "server.exe", self.directory / "codex", "Asia/Tokyo")
        identity = mock.Mock()
        session = ProjectReviewWaitSession(config, runtime_identity=identity)
        session.skill = self.install.skill_root
        self.addCleanup(session.close)
        identity.inspect.assert_not_called()
        scope = SimpleNamespace(skill_root=self.install.skill_root, canonical_repo=self.install.project_root)
        inspection = SimpleNamespace(scope=scope, first_issue=lambda **_: None)
        with mock.patch.object(project_server, "inspect_project_scope", return_value=inspection), \
             mock.patch.object(async_backup_service, "BackupServiceObserver") as factory:
            self.assertTrue(session.start_backup_observer())
            self.assertFalse(session.start_backup_observer())
            factory.assert_called_once()
            identity.inspect.assert_not_called()
            factory.return_value.close.return_value = False
            self.assertEqual(session.close(), {"ok": True})
            factory.return_value.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
