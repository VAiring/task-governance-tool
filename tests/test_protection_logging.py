"""Protection diagnostics retain failures without changing public outcomes."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current
from task_governance_tool import debug_log, debug_log_sink, protection, protection_repository
from task_governance_tool import usage_reconciliation_publication
from task_governance_tool.usage_evidence_service import repository_for


SECRET = 'private-protection-credential-sentinel'


class ProtectionLoggingTests(unittest.TestCase):
    def fixture(self, mode=None, *, usage=False):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        install, target, _ = _setup_current(root / 'project')
        if usage:
            repository_for(target).initialize()
        directory = install.project_root / '.taskgov/diagnostics/logger'
        if mode:
            directory.mkdir(parents=True)
            (directory / 'enabled').write_bytes(f'taskgov-logger-v1 {mode}\n'.encode())
        return install, target, directory

    def run_protection(self, install, *, read_only=False):
        context = SimpleNamespace(repo=install.project_root, repo_explicit=True, read_only=read_only)
        with debug_log.operation_scope(install.project_root, entrypoint=install.entrypoint,
                operation='backup.protect', repo_explicit=True, read_only=read_only):
            return protection.run_protection(context, install.entrypoint)

    def records(self, directory):
        return [json.loads(line) for path in directory.glob('taskgov-debug.log.*')
                for line in path.read_bytes().splitlines()]

    def test_success_records_admitted_stages_after_connections_close(self):
        install, target, directory = self.fixture('debug', usage=True)
        before = [path.read_bytes() for path in (target.db_path, target.resolved_usage_database)]
        result = self.run_protection(install)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.data['recovery'], 'passed')
        rows = self.records(directory)
        self.assertTrue(rows)
        self.assertEqual({row['operation'] for row in rows}, {'backup.protect'})
        methods = {row.get('method') for row in rows if row['event'] == 'method.end'}
        self.assertTrue({'copy_core', 'copy_usage'} <= methods)
        # Successful detail may overflow the common finite buffer. Omission
        # must remain visible, rather than promising a complete stage trace.
        expected = {'rehearse', 'restore_managed_backup', 'refresh', '_same_source',
                    'publication_guard', 'rename_no_replace'}
        if not expected <= methods:
            self.assertTrue(any(row.get('dropped', {}).get('buffer_limit', 0) > 0
                                for row in rows if row['event'] == 'diagnostic.summary'))
        self.assertFalse(any(row['event'] == 'exception' for row in rows))
        self.assertEqual(before, [path.read_bytes() for path in (target.db_path, target.resolved_usage_database)])

    def test_late_usage_sql_failure_survives_pressure_and_public_sanitization(self):
        for mode in ('debug', 'errors'):
            with self.subTest(mode=mode):
                install, target, directory = self.fixture(mode, usage=True)
                before = [path.read_bytes() for path in (target.db_path, target.resolved_usage_database)]
                def fail(repository, _reader):
                    with repository.connection(write=True) as connection:
                        for _ in range(300):
                            connection.execute('SELECT 1').fetchone()
                        connection.execute('SELECT * FROM "' + SECRET + '"')
                with mock.patch.object(usage_reconciliation_publication, 'reconcile', new=fail):
                    result = self.run_protection(install)
                self.assertFalse(result.ok)
                self.assertFalse(result.data['completed'])
                self.assertEqual(result.errors[0]['code'], 'protection_failed')
                rows = self.records(directory)
                failures = [row for row in rows if row['event'] == 'exception']
                self.assertEqual(len(failures), 1)
                self.assertTrue(any(frame['module'] == 'usage_repository' for frame in failures[0]['stack']))
                self.assertTrue(any(row.get('sqlite_errorcode') == 1 and row.get('outcome') == 'failed' for row in rows))
                self.assertTrue(any(row.get('method') == 'refresh' and row.get('outcome') == 'failed' for row in rows))
                self.assertNotIn(SECRET, json.dumps(rows))
                self.assertNotIn(SECRET, json.dumps(result.data))
                self.assertEqual(before, [path.read_bytes() for path in (target.db_path, target.resolved_usage_database)])
                self.assertFalse(list((target.db_path.parent.parent / 'protection').glob('tg_protection_*')))

    def test_off_and_on_have_identical_failure_contract(self):
        install, _, directory = self.fixture()
        with mock.patch.object(protection_repository, 'copy_core', side_effect=OSError(SECRET)):
            off = self.run_protection(install)
            self.assertFalse(directory.exists())
            directory.mkdir(parents=True)
            (directory / 'enabled').write_bytes(b'taskgov-logger-v1 errors\n')
            on = self.run_protection(install)
        self.assertEqual((off.ok, off.data, off.errors, off.exit_code),
                         (on.ok, on.data, on.errors, on.exit_code))
        failures = [row for row in self.records(directory) if row['event'] == 'exception']
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['kind'], 'OSError')
        self.assertNotIn(SECRET, json.dumps(self.records(directory)))

    def test_preview_and_rejected_admission_have_no_diagnostic_writes(self):
        from task_governance_tool import state_resolver
        install, _, directory = self.fixture('debug')
        before = install.state_snapshot()
        result = self.run_protection(install, read_only=True)
        self.assertTrue(result.ok)
        self.assertEqual(before, install.state_snapshot())
        with mock.patch.object(state_resolver, 'consumer_error_code', return_value='project_state_unreadable'):
            rejected = self.run_protection(install)
        self.assertFalse(rejected.ok)
        self.assertEqual(before, install.state_snapshot())
        self.assertEqual(self.records(directory), [])

    def test_sink_and_projection_failures_preserve_protection_failure(self):
        install, _, _ = self.fixture('debug')
        with mock.patch.object(protection_repository, 'copy_core', side_effect=OSError(SECRET)):
            baseline = self.run_protection(install)
            with mock.patch.object(debug_log.projection, 'exception', side_effect=RuntimeError(SECRET)):
                projection = self.run_protection(install)
            with mock.patch.object(debug_log_sink.DailySink, 'write', side_effect=OSError(SECRET)):
                sink = self.run_protection(install)
        for result in (projection, sink):
            self.assertEqual((result.ok, result.data, result.errors, result.exit_code),
                             (baseline.ok, baseline.data, baseline.errors, baseline.exit_code))
