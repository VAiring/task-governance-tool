"""Explicit usage initialization is independent of core setup and reads."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.m14_test_support import make_physical_install, file_snapshot
from task_governance_tool import setup
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.usage_repository import UsageRepository
from task_governance_tool.usage_values import UsageError


class UsageSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = make_physical_install(Path(self.tmp.name).resolve())

    def run_setup(self, read_only=False):
        return setup.run_setup(repo=str(self.install.project_root), repo_explicit=True,
                               script_path=self.install.entrypoint, read_only=read_only,
                               backup_interval_minutes=None, backup_generations=None)

    def resolution(self):
        return resolve_project_state(skill_root=self.install.skill_root, repo=self.install.project_root)

    def test_preview_writes_nothing_and_setup_initializes_once(self):
        before = file_snapshot(self.install.project_root)
        preview = self.run_setup(True)
        self.assertTrue(preview.ok, preview)
        self.assertEqual(preview.data["usage"]["status"], "pending_core_setup")
        self.assertEqual(preview.data["usage"]["completed_writes"], [])
        self.assertEqual(file_snapshot(self.install.project_root), before)
        actual = self.run_setup()
        self.assertTrue(actual.ok, actual)
        self.assertEqual(actual.data["usage"]["status"], "initialized")
        path = self.resolution().paths.usage_database
        original = path.read_bytes()
        self.assertEqual(self.run_setup().data["usage"]["status"], "current")
        self.assertEqual(path.read_bytes(), original)

    def test_usage_failure_does_not_rollback_core_and_retry_is_usage_only(self):
        with mock.patch.object(UsageRepository, "initialize", side_effect=UsageError):
            result = self.run_setup()
        self.assertTrue(result.ok, result)
        self.assertEqual(result.data["usage"]["status"], "unavailable")
        resolution = self.resolution()
        self.assertIsNone(resolution.error_code)
        self.assertIsNotNone(resolution.target)
        self.assertFalse(resolution.paths.usage_database.exists())
        core = resolution.paths.database.read_bytes()
        retried = self.run_setup()
        self.assertTrue(retried.ok)
        self.assertEqual(retried.data["completed_writes"], [])
        self.assertEqual(retried.data["usage"]["completed_writes"], ["usage_initialize"])
        self.assertEqual(resolution.paths.database.read_bytes(), core)

    def test_corrupt_numerical_store_does_not_block_core_admission(self):
        self.assertTrue(self.run_setup().ok)
        resolution = self.resolution()
        resolution.paths.usage_database.write_bytes(b"invalid-numerical-store")
        before = file_snapshot(self.install.project_root)
        self.assertIsNone(self.resolution().error_code)
        preview = self.run_setup(True)
        self.assertTrue(preview.ok)
        self.assertEqual(preview.data["usage"]["status"], "unavailable")
        self.assertEqual(file_snapshot(self.install.project_root), before)
        again = self.run_setup()
        self.assertTrue(again.ok)
        self.assertEqual(again.data["usage"]["status"], "unavailable")
        self.assertEqual(resolution.paths.usage_database.read_bytes(), b"invalid-numerical-store")

    def test_ordinary_resolution_does_not_initialize_missing_store(self):
        self.assertTrue(self.run_setup().ok)
        resolution = self.resolution()
        resolution.paths.usage_database.unlink()
        before = file_snapshot(self.install.project_root)
        self.assertIsNone(self.resolution().error_code)
        self.assertEqual(file_snapshot(self.install.project_root), before)
