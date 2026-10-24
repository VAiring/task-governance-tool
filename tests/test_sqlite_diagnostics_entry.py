"""Scratch-only public entry coverage for opt-in SQLite diagnostics."""

from __future__ import annotations

from contextlib import closing
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.m14_test_support import (
    ROOT, PhysicalInstall, _copy_skill, tree_snapshot,
)
from tests.test_m17_recovery_hardening import _setup_current
from task_governance_tool import sqlite_diagnostics, usage_lifecycle
from task_governance_tool.sqlite_connection import connect_readonly


_MARKER = b"taskgov-sqlite-diagnostics-v1 transactions\n"
_SCRATCH_PARENT = ROOT / ".tmp-sqlite-diagnostics-tests"


def _diagnostics(install: PhysicalInstall) -> Path:
    return install.project_root / ".taskgov" / "diagnostics" / "sqlite"


def _enable(install: PhysicalInstall) -> Path:
    directory = _diagnostics(install)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "enabled").write_bytes(_MARKER)
    return directory


def _run(install: PhysicalInstall, *arguments: str, explicit_repo: bool = True):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    command = [sys.executable, "-B", "-I", "-S", str(install.entrypoint), *arguments]
    if explicit_repo:
        command.extend(["--repo", str(install.project_root)])
    command.append("--json")
    return subprocess.run(
        command, cwd=install.project_root, env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", check=False, timeout=60,
    )


class SQLiteDiagnosticEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # All databases/packages are disposable copies beneath this ignored root.
        _SCRATCH_PARENT.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="entry-", dir=_SCRATCH_PARENT)
        cls.root = Path(cls.temporary.name).resolve()
        if not cls.root.is_relative_to(_SCRATCH_PARENT.resolve()):
            raise AssertionError("scratch fixture escaped its configured parent")
        try:
            cls.install, _, _ = _setup_current(cls.root / "installed")
        except BaseException:
            cls.temporary.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        if not cls.root.is_relative_to(_SCRATCH_PARENT.resolve()):
            raise AssertionError("scratch cleanup escaped its configured parent")
        cls.temporary.cleanup()

    def setUp(self):
        # Reset only this fixture's known logger artifacts, never project data.
        directory = _diagnostics(self.install)
        if directory.exists():
            for artifact in [directory / "enabled", *directory.glob("trace-*.json")]:
                if artifact.exists():
                    artifact.unlink()

    def _source_install(self) -> PhysicalInstall:
        project = self.root / self._testMethodName / "project"
        project.mkdir(parents=True)
        skill_root = _copy_skill(project)
        for relative in ("AGENTS.md", "docs/specification.md", "docs/design.md", "plan.md"):
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Scratch source-layout fixture\n", encoding="utf-8")
        return PhysicalInstall(project_root=project, skill_root=skill_root)

    def _assert_same_output(self, before, after):
        self.assertEqual(
            (before.returncode, before.stdout, before.stderr),
            (after.returncode, after.stdout, after.stderr),
        )

    def test_installed_read_commands_log_readers_without_changing_output(self):
        commands = (("task", "list"), ("task", "context"))
        baselines = [_run(self.install, *command) for command in commands]
        for baseline in baselines:
            self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
            self.assertTrue(json.loads(baseline.stdout)["ok"])
        self.assertFalse(list(_diagnostics(self.install).glob("trace-*.json")))
        before_database = self.install.db_path.read_bytes()
        directory = _enable(self.install)

        for command, baseline in zip(commands, baselines):
            with self.subTest(command=command):
                previous = set(directory.glob("trace-*.json"))
                observed = _run(self.install, *command)
                self._assert_same_output(baseline, observed)
                added = set(directory.glob("trace-*.json")) - previous
                self.assertEqual(len(added), 1)
                document = json.loads(added.pop().read_bytes())
                self.assertEqual(document["operation"], ".".join(command))
                readers = [record for record in document["records"]
                           if record["role"] == "core" and record["access"] == "readonly"]
                self.assertTrue(any(record["phase"] == "begin" for record in readers))
                self.assertTrue(any(record["phase"] == "close" for record in readers))
                self.assertTrue(any("transaction_elapsed_ns" in record for record in readers))
        self.assertEqual(before_database, self.install.db_path.read_bytes())

    def test_doctor_and_explicit_read_only_remain_write_free_with_marker(self):
        _enable(self.install)
        for command in (("doctor",), ("task", "list", "--read-only"),
                        ("task", "context", "--read-only"), ("setup", "--read-only")):
            with self.subTest(command=command):
                before = self.install.state_snapshot()
                result = _run(self.install, *command)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(before, self.install.state_snapshot())
                self.assertFalse(list(_diagnostics(self.install).glob("trace-*.json")))

    def test_source_missing_database_and_implicit_repo_keep_public_errors(self):
        install = self._source_install()
        baseline = _run(install, "task", "list")
        self.assertNotEqual(baseline.returncode, 0)
        self.assertEqual(json.loads(baseline.stdout)["errors"][0]["code"], "db_not_initialized")
        _enable(install)
        before = tree_snapshot(install.project_root / ".taskgov")
        self._assert_same_output(baseline, _run(install, "task", "list"))
        self.assertEqual(before, tree_snapshot(install.project_root / ".taskgov"))

        # An enabled marker does not authorize source self-hosting without --repo.
        rejected = _run(install, "task", "list", explicit_repo=False)
        self.assertEqual(json.loads(rejected.stdout)["errors"][0]["code"], "project_scope_required")
        self.assertEqual(before, tree_snapshot(install.project_root / ".taskgov"))
        self.assertFalse(install.db_path.exists())

    def test_source_usage_scope_preserves_budget_and_flushes_after_reader_close(self):
        install = self._source_install()
        directory = _enable(install)
        install.db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(install.db_path)) as connection:
            connection.execute("CREATE TABLE fixture (value INTEGER)")
            connection.commit()
        clock = {"now": 100.0}
        admit = sqlite_diagnostics._admit
        result = {"status": "unknown", "collected_sources": 0, "diagnostics": ["usage_unavailable"]}
        connections = []

        def delayed_admission(*args, **kwargs):
            collector = admit(*args, **kwargs)
            clock["now"] = 200.0
            return collector

        def body(payload, **kwargs):
            # Admission consumes the existing event budget; it never resets it.
            self.assertEqual(kwargs["started"], 100.0)
            self.assertTrue(kwargs["repo_explicit"])
            self.assertIsNotNone(sqlite_diagnostics.current_collector())
            # This isolated body stands in for successful existing admission.
            sqlite_diagnostics.allow_output()
            with closing(connect_readonly(install.db_path)) as reader:
                connections.append(reader)
                self.assertEqual(reader.execute("SELECT COUNT(*) FROM fixture").fetchone()[0], 0)
                self.assertFalse(list(directory.glob("trace-*.json")))
            self.assertFalse(list(directory.glob("trace-*.json")))
            return result

        with mock.patch.object(usage_lifecycle, "monotonic", side_effect=lambda: clock["now"]), \
                mock.patch.object(sqlite_diagnostics, "_admit", side_effect=delayed_admission), \
                mock.patch.object(usage_lifecycle, "_collect_event", side_effect=body):
            observed = usage_lifecycle.collect_event({}, skill_root=install.skill_root,
                repo=install.project_root, environment={}, repo_explicit=True)
        self.assertIs(observed, result)
        self.assertIsNone(sqlite_diagnostics.current_collector())
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")
        logs = list(directory.glob("trace-*.json"))
        self.assertEqual(len(logs), 1)
        document = json.loads(logs[0].read_bytes())
        self.assertEqual(document["operation"], "usage_hook")
        self.assertTrue(any(record["phase"] == "close" for record in document["records"]))

    def test_source_usage_hook_failure_keeps_neutral_protocol(self):
        install = self._source_install()
        directory = _enable(install)
        install.db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(install.db_path)) as connection:
            connection.execute("CREATE TABLE fixture (value INTEGER)")
            connection.commit()

        def failed_body(payload, **kwargs):
            sqlite_diagnostics.allow_output()
            with closing(connect_readonly(install.db_path)) as reader:
                reader.execute("SELECT COUNT(*) FROM fixture").fetchone()
                self.assertFalse(list(directory.glob("trace-*.json")))
            raise RuntimeError("PRIVATE hook payload must never reach the diagnostic sink")

        output = io.StringIO()
        with mock.patch.object(usage_lifecycle, "_collect_event", side_effect=failed_body):
            code = usage_lifecycle.main(stdin=io.BytesIO(b"{}"), stdout=output,
                skill_root=install.skill_root, repo=install.project_root, environment={},
                argv=("--repo", str(install.project_root)))
        self.assertEqual((code, output.getvalue()), (0, "{}\n"))
        logs = list(directory.glob("trace-*.json"))
        self.assertEqual(len(logs), 1)
        self.assertNotIn(b"PRIVATE", logs[0].read_bytes())
        self.assertIsNone(sqlite_diagnostics.current_collector())

    def test_rejected_relocation_keeps_cli_and_lifecycle_write_free(self):
        for common in (False, True):
            with self.subTest(common=common):
                destination = self.root / ('relocated-common' if common else 'relocated-sqlite')
                shutil.copytree(self.install.project_root, destination)
                install = PhysicalInstall(destination, destination / self.install.skill_root.relative_to(self.install.project_root))
                _enable(install)
                if common:
                    directory = destination / '.taskgov/diagnostics/logger'
                    directory.mkdir(parents=True)
                    (directory / 'enabled').write_bytes(b'taskgov-logger-v1 debug\n')
                before = install.state_snapshot()
                result = _run(install, 'task', 'list')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)['errors'][0]['code'], 'project_relocation_required')
                self.assertEqual(before, install.state_snapshot())

                payload = {'hook_event_name': 'Stop', 'cwd': str(destination),
                           'session_id': '12345678-1234-1234-1234-123456789abc'}
                with mock.patch.object(usage_lifecycle, 'collection_allowed', return_value=True):
                    observed = usage_lifecycle.collect_event(payload, skill_root=install.skill_root,
                        repo=destination, environment={}, repo_explicit=True)
                self.assertEqual(observed, {'status': 'unknown', 'collected_sources': 0,
                                            'diagnostics': ['usage_unavailable']})
                self.assertEqual(before, install.state_snapshot())


if __name__ == "__main__":
    unittest.main()
