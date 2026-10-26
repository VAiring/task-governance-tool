"""Async child diagnostics reuse admission and never inherit the parent scope."""

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from tests import test_async_backup_runtime as runtime_support
from task_governance_tool import async_backup as runtime
from task_governance_tool import async_backup_repository as repository
from task_governance_tool import debug_log, sqlite_diagnostics


class AsyncBackupLoggingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_support.AsyncBackupRuntimeTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.install, self.target = self.fixture.install, self.fixture.target
        self.root, self.record = self.fixture.prepare()
        self.diagnostics = self.install.project_root / ".taskgov/diagnostics"

    def enable(self, kind):
        directory = self.diagnostics / kind
        directory.mkdir(parents=True, exist_ok=True)
        marker = (b"taskgov-logger-v1 debug\n" if kind == "logger"
                  else b"taskgov-sqlite-diagnostics-v1 transactions\n")
        (directory / "enabled").write_bytes(marker)
        return directory

    def run_worker(self):
        return runtime.worker_main(self.install.project_root, self.record["attempt_id"],
                                   skill_root=self.install.skill_root, go_stream=io.BytesIO(b"G"))

    def records(self):
        return [json.loads(line) for path in (self.diagnostics / "logger").glob("taskgov-debug.log.*")
                for line in path.read_bytes().splitlines()]

    def test_disabled_worker_creates_no_diagnostic_output(self):
        with mock.patch.object(repository, "read_control", wraps=repository.read_control) as read:
            self.assertEqual(self.run_worker(), 0)
        self.assertEqual(read.call_count, 2)
        self.assertFalse(self.diagnostics.exists())

    def test_common_worker_scope_is_independent_and_adds_no_admission_read(self):
        self.enable("logger")
        sqlite_directory = self.enable("sqlite")
        with debug_log.operation_scope(self.install.project_root,
                entrypoint=self.install.entrypoint, operation="task", repo_explicit=True,
                validated=True) as parent:
            self.assertIsNotNone(parent)
            with mock.patch.object(repository, "read_control", wraps=repository.read_control) as read:
                self.assertEqual(self.run_worker(), 0)
            self.assertEqual(read.call_count, 2)
            self.assertIs(sqlite_diagnostics.current_collector(), parent.collector)
            rows = self.records()
            self.assertTrue(rows)
            self.assertEqual({row["operation"] for row in rows}, {"backup_worker"})
            self.assertNotIn(parent.invocation_id, {row["invocation_id"] for row in rows})
            self.assertTrue(any(row.get("phase") == "backup" for row in rows))
            self.assertTrue(any(row["event"] == "db.sql.summary" for row in rows))
            self.assertTrue(any(row.get("origin") == "async_backup_repository.copy_snapshot" for row in rows))
        self.assertFalse(list(sqlite_directory.glob("trace-*.json")))

    def test_sqlite_only_worker_operation_is_admitted(self):
        directory = self.enable("sqlite")
        self.assertEqual(self.run_worker(), 0)
        documents = [json.loads(path.read_bytes()) for path in directory.glob("trace-*.json")]
        self.assertTrue(documents)
        self.assertEqual({document["operation"] for document in documents}, {"backup_worker"})
        self.assertTrue(any(record["phase"] == "backup" for document in documents for record in document["records"]))

    def test_rejected_database_basis_discards_buffered_observations(self):
        self.enable("logger")
        directory = self.enable("sqlite")
        replacement = self.target.db_path.with_name("replacement.sqlite")
        shutil.copyfile(self.target.db_path, replacement)
        os.replace(replacement, self.target.db_path)
        self.assertEqual(self.run_worker(), 2)
        self.assertEqual(self.records(), [])
        self.assertFalse(list(directory.glob("trace-*.json")))

    def test_real_child_entry_records_its_own_process(self):
        self.enable("logger")
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
            str(self.install.skill_root / "scripts/backup_worker.py"),
            str(self.install.project_root), self.record["attempt_id"]],
            input=b"G", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(self.fixture.directory), timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        rows = self.records()
        self.assertTrue(rows)
        self.assertEqual({row["operation"] for row in rows}, {"backup_worker"})
        self.assertEqual(len({row["pid"] for row in rows}), 1)
        self.assertNotIn(os.getpid(), {row["pid"] for row in rows})
