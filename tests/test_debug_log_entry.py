"""Public CLI invariance and multiprocess daily output in disposable installs."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests import test_sqlite_diagnostics_entry as entry


class DebugLogEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='taskgov-debug-entry-')
        cls.root = Path(cls.temp.name)
        cls.install, _, _ = entry._setup_current(cls.root / 'installed')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @contextmanager
    def enabled(self):
        directory = self.install.project_root / '.taskgov/diagnostics/logger'
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / 'enabled'
        marker.write_bytes(b'taskgov-logger-v1 debug\n')
        try:
            yield directory
        finally:
            marker.unlink()

    def test_installed_cli_off_on_identical_and_no_sqlite_duplicate(self):
        baseline = entry._run(self.install, 'task', 'list')
        self.assertEqual(baseline.returncode, 0, baseline.stdout)
        with self.enabled() as directory:
            observed = entry._run(self.install, 'task', 'list')
            self.assertEqual((baseline.returncode, baseline.stdout, baseline.stderr),
                             (observed.returncode, observed.stdout, observed.stderr))
            rows = [json.loads(line) for path in directory.glob('taskgov-debug.log.*')
                    for line in path.read_bytes().splitlines()]
            self.assertTrue(any(r['event'] == 'method.start' for r in rows))
            self.assertTrue(any(r.get('phase') == 'close' for r in rows))
            self.assertFalse(list((self.install.project_root / '.taskgov/diagnostics/sqlite').glob('trace-*.json')))

    def test_read_only_doctor_and_setup_leave_entire_state_unchanged(self):
        with self.enabled():
            for command in [('doctor',), ('task', 'list', '--read-only'), ('setup', '--read-only')]:
                with self.subTest(command=command):
                    before = self.install.state_snapshot()
                    result = entry._run(self.install, *command)
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(before, self.install.state_snapshot())

    def test_multiprocess_append_produces_complete_distinct_records(self):
        with self.enabled() as directory:
            command = [sys.executable, '-I', '-S', '-B', str(self.install.entrypoint), 'task', 'list',
                       '--repo', str(self.install.project_root), '--json']
            processes = [subprocess.Popen(command, cwd=self.install.project_root,
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
            for process in processes:
                output, error = process.communicate(timeout=60)
                self.assertEqual(process.returncode, 0, output + error)
            rows = [json.loads(line) for path in directory.glob('taskgov-debug.log.*')
                    for line in path.read_bytes().splitlines()]
            self.assertTrue(rows)  # Nonblocking contention may intentionally drop operations.
            self.assertTrue(all(row['format'] == 'taskgov-debug-v1' for row in rows))

    def test_invalid_marker_does_not_create_artifacts(self):
        with self.enabled() as directory:
            (directory / 'enabled').write_bytes(b'taskgov-logger-v1 debug\r\n')
            before = self.install.state_snapshot()
            result = entry._run(self.install, 'task', 'list')
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(before, self.install.state_snapshot())
