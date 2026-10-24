"""Common observations through actual admitted entry/backup/worker paths."""
from contextlib import closing
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import shutil
import threading
import unittest
from unittest import mock

from tests.test_sqlite_diagnostics_entry import _setup_current, _run
from task_governance_tool import backup, debug_log, usage_lifecycle


def enable(root):
    directory = root / '.taskgov/diagnostics/logger'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'enabled').write_bytes(b'taskgov-logger-v1 debug\n')
    return directory


def rows(directory):
    return [json.loads(line) for path in directory.glob('taskgov-debug.log.*')
            for line in path.read_bytes().splitlines()]


class EntryAdoptionTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix='log-adoption-')
        self.addCleanup(temporary.cleanup)
        install, target, _ = _setup_current(Path(temporary.name) / 'project')
        return install, target, enable(install.project_root)

    def test_write_setup_adopts_logger_but_preview_and_doctor_do_not(self):
        install, _, directory = self.fixture()
        observed = _run(install, 'setup')
        self.assertEqual(observed.returncode, 0, observed.stdout)
        records = rows(directory)
        self.assertTrue(any(r.get('phase') == 'open' and r['operation'] == 'setup' for r in records))
        self.assertTrue(any(r.get('phase') == 'close' for r in records))
        for command in [('setup', '--read-only'), ('doctor',), ('task', 'list', '--read-only')]:
            before = install.state_snapshot()
            result = _run(install, *command)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(before, install.state_snapshot())

    def test_fresh_setup_admits_only_after_publication_and_relocation_does_not(self):
        from tests.m14_test_support import make_physical_install
        temporary = tempfile.TemporaryDirectory(prefix='log-fresh-')
        self.addCleanup(temporary.cleanup)
        install = make_physical_install(Path(temporary.name) / 'original', git_managed=True)
        directory = enable(install.project_root)
        result = _run(install, 'setup')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(any(r.get('phase') == 'open' for r in rows(directory)))
        other = Path(temporary.name) / 'moved'
        shutil.copytree(install.project_root, other)
        moved = replace(install, project_root=other,
                        skill_root=other / '.agents/skills/task-governance-tool')
        before = moved.state_snapshot()
        result = _run(moved, 'setup')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(before, moved.state_snapshot())

    def test_usage_real_admission_logs_core_and_numerical_connections(self):
        install, _, directory = self.fixture()
        payload = {'hook_event_name': 'SessionStart', 'cwd': str(install.project_root),
                   'session_id': '11111111-1111-4111-8111-111111111111'}
        result = usage_lifecycle.collect_event(payload, skill_root=install.skill_root,
            repo=install.project_root, environment={}, repo_explicit=True)
        self.assertIn(result['status'], {'pending', 'unknown'})
        opens = [r for r in rows(directory) if r.get('phase') == 'open']
        self.assertTrue(opens)
        self.assertEqual({r['operation'] for r in opens}, {'usage_hook'})
        self.assertTrue({'core', 'numerical'} <= {r['role'] for r in opens})
        previous = list(rows(directory))
        # Payload admission rejection grants no output, including resolver logs.
        usage_lifecycle.collect_event({**payload, 'cwd': str(install.project_root / 'elsewhere')},
            skill_root=install.skill_root, repo=install.project_root, environment={}, repo_explicit=True)
        self.assertEqual(previous, rows(directory))

    def test_private_backup_copy_and_failure_close_before_output(self):
        install, target, directory = self.fixture()
        destination = target.db_path.parent / 'copy.sqlite'
        copied = replace(target, db_path=destination, canonical_fixed=False)
        with debug_log.operation_scope(install.project_root, entrypoint=install.entrypoint,
                operation='setup', repo_explicit=True, validated=True):
            backup.copy_database_snapshot(source_path=target.db_path,
                source_target=target, destination_target=copied)
            self.assertEqual(rows(directory), [])
        records = rows(directory)
        self.assertTrue(any(r.get('role') == 'backup' and r.get('phase') == 'open' for r in records))
        self.assertTrue(any(r.get('phase') == 'backup' for r in records))
        opens = {(r['invocation_id'], r['connection_id']) for r in records if r.get('phase') == 'open'}
        closes = {(r['invocation_id'], r['connection_id']) for r in records if r.get('phase') == 'close'}
        self.assertEqual(opens, closes)
        failed_destination = target.db_path.parent / 'failed-copy.sqlite'
        failed = replace(target, db_path=failed_destination, canonical_fixed=False)
        original = backup._validate_database
        calls = []

        def fail_copy_validation(connection, *args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise ValueError('private copy validation fixture')
            return original(connection, *args, **kwargs)

        with self.assertRaises(ValueError), mock.patch.object(backup, '_validate_database', fail_copy_validation):
            with debug_log.operation_scope(install.project_root, entrypoint=install.entrypoint,
                    operation='setup', repo_explicit=True, validated=True):
                backup.copy_database_snapshot(source_path=target.db_path,
                    source_target=target, destination_target=failed)
        records = rows(directory)
        self.assertTrue(any(r['event'] == 'exception' for r in records))
        self.assertNotIn('private copy validation fixture', json.dumps(records))


class ReviewAdoptionTests(unittest.TestCase):
    def test_compatibility_wait_ended_reuses_admission_and_publishes(self):
        from tests.test_review_wait_handoff import ReviewWaitHandoffTests
        f = ReviewWaitHandoffTests(methodName='runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        directory = enable(f.root)
        completed, result = f.decision()
        self.assertEqual(completed.returncode, 0, result)
        self.assertTrue(result['ok'])
        records = rows(directory)
        opens = [r for r in records if r.get('phase') == 'open']
        self.assertTrue({'core', 'numerical'} <= {r['role'] for r in opens})
        self.assertEqual({r['operation'] for r in opens}, {'review_handoff'})

    def test_eof_and_cancelled_shutdown_have_finite_owned_cleanup_scope(self):
        from tests.test_review_wait_project import ProjectWaitTests
        from task_governance_tool.review_wait_runtime import review_wait_repository as repository
        from task_governance_tool.review_wait_runtime.review_wait_server import serve
        from task_governance_tool.sqlite_diagnostics import current_collector

        class InterruptedInput(io.BytesIO):
            def readline(self, *args):
                raise KeyboardInterrupt()

        for cancellation in (False, True):
            with self.subTest(cancellation=cancellation):
                f = ProjectWaitTests(methodName='runTest')
                f.setUp()
                self.addCleanup(f.doCleanups)
                directory = enable(f.root)
                f.prepare_wait()
                self.assertTrue(f.call('direct_delete_start')['ok'])
                original, main_thread, observed = repository.connect_traced, threading.get_ident(), []

                def connect(*args, **kwargs):
                    if threading.get_ident() == main_thread:
                        observed.append(current_collector() is not None)
                    return original(*args, **kwargs)

                with mock.patch.object(repository, 'connect_traced', connect):
                    status = serve(InterruptedInput() if cancellation else io.BytesIO(), io.BytesIO(), f.session)
                self.assertEqual(status, 1 if cancellation else 0)
                self.assertTrue(observed)
                self.assertTrue(all(observed))
                cleanup = [r for r in rows(directory) if r['operation'] == 'review_wait_cleanup']
                self.assertTrue(any(r.get('phase') == 'open' for r in cleanup))
                self.assertTrue(any(r.get('phase') == 'close' for r in cleanup))

    def test_readonly_view_then_eof_still_does_not_publish(self):
        from tests.test_review_wait_project import ProjectWaitTests
        from task_governance_tool.review_wait_runtime.review_wait_server import serve
        f = ProjectWaitTests(methodName='runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.prepare_wait()
        directory = enable(f.root)
        viewer = f.make()
        before = f.install.state_snapshot()
        self.assertTrue(f.call('view', session=viewer)['ok'])
        self.assertEqual(serve(io.BytesIO(), io.BytesIO(), viewer), 0)
        self.assertEqual(before, f.install.state_snapshot())
        self.assertEqual(rows(directory), [])

    def test_mcp_request_worker_and_readonly_inspect(self):
        from tests.test_review_wait_project import ProjectWaitTests
        from tests.test_review_wait_service import CHILD, TURN
        from tests.test_review_wait_direct import ORIGINAL
        from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
        f = ProjectWaitTests(methodName='runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        directory = enable(f.root)
        f.prepare_wait()
        records = rows(directory)
        self.assertTrue(any(r.get('role') == 'operational' and r.get('phase') == 'begin' for r in records))
        before = f.install.state_snapshot()
        inspected = f.session.handle('inspect', {'task_id': f.task_id}, f.metadata)
        self.assertTrue(inspected['ok'], inspected)
        self.assertEqual(before, f.install.state_snapshot())
        started = f.call('direct_delete_start')
        self.assertTrue(started['ok'], started)
        host = f.hosts['timer-1']
        host.turn = ChildTurn(CHILD, TURN, 'completed', 'idle')
        host.parent = ChildTurn(f.parent, ORIGINAL, 'completed', 'idle')
        f.join_physical_worker('timer-1')
        f.session.close()
        records = rows(directory)
        workers = [r for r in records if r['operation'] == 'review_wait_worker']
        self.assertTrue(any(r.get('phase') == 'open' for r in workers))
        self.assertTrue(any(r.get('phase') == 'close' for r in workers))
        self.assertTrue(all('NEVER_SAVE_THIS' not in json.dumps(r) for r in records))
