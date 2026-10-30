"""Real SQLite/process contention; POSIX negative control is never emulated."""
from contextlib import closing, ExitStack
import ast
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'task-governance-tool/scripts'
sys.path.insert(0, str(SCRIPTS))
from task_governance_tool import sqlite_connection as connections
from task_governance_tool import sqlite_file_access as files
from task_governance_tool.review_wait_control_observation import ReviewWaitControlObservation
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
from task_governance_tool.state_paths import hash_physical_file, StatePathError
from task_governance_tool.usage_repository import UsageRepository


CHILD = r'''
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository, RepositoryError
try:
    if sys.argv[3] == 'lease':
        repo = ReviewWaitRepository(Path(sys.argv[1]))
        with repo._lease(), repo._connection(write=True) as c:
            c.execute("UPDATE probe SET child='child'")
    else:
        c = sqlite3.connect(sys.argv[1], timeout=0)
        try:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE probe SET child='child'")
            c.commit()
        finally:
            c.close()
    result = {'committed': True}
except (sqlite3.Error, RepositoryError) as error:
    cause = error.__context__ if isinstance(error, RepositoryError) else error
    result = {'committed': False, 'code': getattr(cause, 'sqlite_errorcode', None),
              'repository_error': error.code if isinstance(error, RepositoryError) else None}
print(json.dumps(result))
'''


class SQLiteFileAccessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.path = self.root / 'state.sqlite'
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executescript("CREATE TABLE probe(parent, child); INSERT INTO probe VALUES('original','original');")
        Path(str(self.path) + '.lock').write_bytes(b'0')

    def contender(self, *, lease=False):
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', CHILD,
            str(self.path), str(SCRIPTS), 'lease' if lease else 'plain'],
            text=True, capture_output=True, timeout=15, check=True)
        return json.loads(result.stdout)

    def assert_blocked(self, *, lease=False):
        result = self.contender(lease=lease)
        self.assertFalse(result['committed'], result)
        self.assertEqual(result['code'] & 255, sqlite3.SQLITE_BUSY)

    def values(self):
        with closing(sqlite3.connect(self.path)) as c:
            return c.execute('SELECT * FROM probe').fetchone()

    def test_common_header_hint_hash_and_sqlite_reader_preserve_writer(self):
        usage = UsageRepository(self.path, 'p', 'a' * 64, 1)
        operations = {
            'journal': lambda: connections.validate_operational_journal_state(self.path),
            'hint': lambda: ReviewWaitControlObservation(self.path, 'p', 't')._hint(),
            'usage_preflight': usage._physical,
            'hash': lambda: hash_physical_file(self.path, root=self.root),
            'sqlite_reader': lambda: connections.connect_readonly(self.path, timeout=0).close(),
        }
        for name, operation in operations.items():
            with self.subTest(name=name), closing(connections.connect_existing(self.path)) as holder:
                holder.execute('BEGIN IMMEDIATE')
                holder.execute("UPDATE probe SET parent='parent'")
                operation()
                self.assert_blocked()
                holder.commit()
            self.assertTrue(self.contender()['committed'])
            self.assertEqual(self.values(), ('parent', 'child'))

    def test_diagnostic_connection_keeps_same_lock_and_close_ownership(self):
        from task_governance_tool.sqlite_diagnostics import Collector, bind_collector
        # In-memory observation only: sink admission is tested independently.
        collector = Collector(self.root, self.root, 'transactions', operation='task',
                              marker=self.root / 'enabled', directory_identity=(0, 0))
        with bind_collector(collector), closing(connections.connect_existing(self.path)) as holder:
            self.assertEqual(type(holder).__name__, '_ObservedConnection')
            holder.execute('BEGIN IMMEDIATE')
            holder.execute("UPDATE probe SET parent='parent'")
            connections.sqlite_header_uses_wal(self.path)
            self.assert_blocked()
            holder.commit()
        self.assertEqual(collector._open, {})
        self.assertTrue(any(record['phase'] == 'close' for record in collector.records))
        self.assertTrue(self.contender()['committed'])
        self.assertEqual(self.values(), ('parent', 'child'))

    def test_another_thread_inspection_preserves_retained_read_and_lease_writer(self):
        repo = ReviewWaitRepository(self.path)
        for mode in ('read', 'write'):
            with self.subTest(mode=mode), repo._connection(write=mode == 'write') as holder:
                holder.execute('SELECT * FROM probe').fetchall()
                before = tuple(holder.execute('SELECT * FROM probe').fetchone())
                failures = []
                def inspect():
                    try:
                        for _ in range(20):
                            repo._inspect()
                            with repo._connection(observation=True) as other:
                                other.execute('SELECT * FROM probe').fetchall()
                    except BaseException as error:
                        failures.append(error)
                thread = threading.Thread(target=inspect)
                thread.start()
                thread.join(10)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])
                self.assert_blocked(lease=True)
                self.assertEqual(tuple(holder.execute('SELECT * FROM probe').fetchone()), before)
            self.assertTrue(self.contender(lease=True)['committed'])

    def test_two_connections_and_deferred_hash_close_order(self):
        for reversed_close in (False, True):
            with self.subTest(reversed_close=reversed_close), ExitStack() as stack:
                first = connections.connect_readonly(self.path)
                second = connections.connect_readonly(self.path)
                stack.callback(first.close)
                stack.callback(second.close)
                for c in (first, second):
                    c.execute('SELECT * FROM probe').fetchall()
                deferred = ExitStack()
                hash_physical_file(self.path, root=self.root, descriptor_scope=deferred)
                (second if reversed_close else first).close()
                deferred.close()
                self.assert_blocked()
            self.assertTrue(self.contender()['committed'])

    def test_runtime_writer_lease_remains_independent_of_sqlite_inspection(self):
        repo = ReviewWaitRepository(self.path)
        with repo.writer(), repo._connection(write=True) as holder:
            holder.execute("UPDATE probe SET parent='parent'")
            repo._inspect()
            result = self.contender(lease=True)
            self.assertFalse(result['committed'])
            self.assertEqual(result['repository_error'], 'writer_busy')
        self.assertTrue(self.contender(lease=True)['committed'])
        self.assertEqual(self.values(), ('parent', 'child'))

    def test_exception_hash_cleanup_keeps_other_reader(self):
        with closing(connections.connect_readonly(self.path)) as holder:
            holder.execute('SELECT * FROM probe').fetchall()
            with mock.patch.object(files._Reader, 'read', side_effect=OSError('private')):
                with self.assertRaises(StatePathError):
                    hash_physical_file(self.path, root=self.root)
            self.assert_blocked()
        self.assertTrue(self.contender()['committed'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-lifetime mechanism')
    def test_unconsumed_cursors_close_before_raw_descriptor(self):
        c = connections.connect_existing(self.path)
        cursors = [c.execute('SELECT 1 UNION ALL SELECT 2'), c.cursor()]
        cursors[1].execute('SELECT * FROM probe')
        connections.sqlite_header_uses_wal(self.path)
        c.close()
        for cursor in cursors:
            with self.assertRaises(sqlite3.ProgrammingError):
                cursor.fetchone()
        self.assertTrue(self.contender()['committed'])

    def test_private_inode_hash_does_not_change_source_lock(self):
        private = self.root / 'private.bin'
        private.write_bytes(b'private')
        with closing(connections.connect_readonly(self.path)) as holder:
            holder.execute('SELECT * FROM probe').fetchall()
            self.assertEqual(hash_physical_file(private, root=self.root).sha256,
                             hashlib.sha256(b'private').hexdigest())
            self.assert_blocked()

    def test_wal_rejected_before_connection_or_sidecar_creation(self):
        with closing(sqlite3.connect(self.path)) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('SELECT * FROM probe').fetchall()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with self.assertRaises(connections.StorageError) as error:
            connections.connect_readonly(self.path)
        self.assertEqual(error.exception.code, 'unsupported_journal_mode')
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    @unittest.skipUnless(os.name == 'posix', 'requires actual POSIX advisory locks')
    def test_raw_close_negative_control_reproduces_lost_update(self):
        # Deliberately unsafe, disposable data only. Confirms this environment
        # exhibits the defect rather than accepting a vacuous positive control.
        with closing(sqlite3.connect(self.path)) as holder:
            holder.execute('BEGIN IMMEDIATE')
            holder.execute("UPDATE probe SET parent='parent'")
            with self.path.open('rb') as raw:
                raw.read(20)
            self.assertTrue(self.contender()['committed'])
            try:
                holder.commit()
            except sqlite3.OperationalError as error:
                # The contender removed the shared journal. SQLite can report
                # this after writing the parent's stale pages; loss still must
                # be demonstrated below, not inferred from an exception.
                self.assertEqual(error.sqlite_errorcode, sqlite3.SQLITE_IOERR_DELETE_NOENT)
        self.assertEqual(self.values(), ('parent', 'original'))

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-lifetime mechanism')
    def test_descriptor_reused_without_cached_bytes_and_retired_at_close(self):
        with closing(connections.connect_existing(self.path)) as holder:
            with files.file_reader(self.path) as reader:
                fd = reader.descriptor
                before = reader.read(44)
            for _ in range(100):
                with files.file_reader(self.path) as reader:
                    self.assertEqual(reader.descriptor, fd)
            holder.execute("UPDATE probe SET parent='changed'")
            holder.commit()
            with files.file_reader(self.path) as reader:
                self.assertNotEqual(reader.read(44)[24:28], before[24:28])
        with self.assertRaises(OSError):
            os.fstat(fd)

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-lifetime mechanism')
    def test_failed_close_retains_protection_then_owner_close_releases(self):
        holder = connections.connect_existing(self.path)
        holder.execute('BEGIN IMMEDIATE')
        connections.sqlite_header_uses_wal(self.path)
        failures = []
        def close():
            try:
                holder.close()
            except sqlite3.ProgrammingError as error:
                failures.append(type(error))
        thread = threading.Thread(target=close)
        thread.start()
        thread.join(10)
        self.assertEqual(failures, [sqlite3.ProgrammingError])
        self.assert_blocked()
        holder.close()
        self.assertTrue(self.contender()['committed'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-lifetime mechanism')
    def test_failed_configuration_and_abandonment_release_registration(self):
        with mock.patch.object(connections, 'configure_connection', side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):
                connections.connect_existing(self.path)
        holder = connections.connect_existing(self.path)
        connections.sqlite_header_uses_wal(self.path)
        del holder
        gc.collect()
        self.assertNotIn((self.path.stat().st_dev, self.path.stat().st_ino), files._files)


class FileOwnershipMechanismTests(unittest.TestCase):
    """Descriptor accounting also runs on Windows; not POSIX contention proof."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name).resolve() / 'unit.sqlite'
        with closing(sqlite3.connect(self.path)) as c:
            c.execute('CREATE TABLE item(value)')
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(files, '_POSIX', True))
        if not hasattr(os, 'pread'):
            def positional_read(fd, size, offset):
                os.lseek(fd, offset, os.SEEK_SET)
                return os.read(fd, size)
            stack.enter_context(mock.patch.object(os, 'pread', positional_read, create=True))

    def test_repeated_readers_and_two_connections_retire_only_after_last_close(self):
        one = connections.connect_existing(self.path)
        two = connections.connect_existing(self.path)
        self.addCleanup(one.close)
        self.addCleanup(two.close)
        for _ in range(100):
            with files.file_reader(self.path) as reader:
                fd = reader.descriptor
                self.assertEqual(reader.read(16), b'SQLite format 3\x00')
        one.close()
        os.fstat(fd)
        two.close()
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_unconsumed_cursor_and_garbage_collection_do_not_leave_registry(self):
        holder = connections.connect_existing(self.path)
        cursor = holder.execute('SELECT 1 UNION ALL SELECT 2')
        with files.file_reader(self.path) as reader:
            fd = reader.descriptor
        holder.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            cursor.fetchone()
        with self.assertRaises(OSError):
            os.fstat(fd)
        holder = connections.connect_existing(self.path)
        connections.sqlite_header_uses_wal(self.path)
        del holder
        gc.collect()
        self.assertNotIn((self.path.stat().st_dev, self.path.stat().st_ino), files._files)

    def test_unidentified_descriptor_waits_for_connections_and_releases_on_failure(self):
        holder = connections.connect_existing(self.path)
        self.addCleanup(holder.close)
        opened = []
        original = os.open
        def capture(*args):
            fd = original(*args)
            opened.append(fd)
            return fd
        with mock.patch.object(files.os, 'open', side_effect=capture), \
             mock.patch.object(files.os, 'fstat', side_effect=OSError('fixture')):
            with self.assertRaises(OSError), files.file_reader(self.path):
                pass
        os.fstat(opened[0])
        holder.close()
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_configuration_failure_closes_registration(self):
        with mock.patch.object(connections, 'configure_connection', side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):
                connections.connect_existing(self.path)
        self.assertNotIn((self.path.stat().st_dev, self.path.stat().st_ino), files._files)

    def test_replaced_open_descriptor_is_retained_against_actual_inode(self):
        other = self.path.with_name('other.sqlite')
        with closing(connections.connect(other)) as holder:
            holder.execute('CREATE TABLE item(value)')
            holder.commit()
            with files.file_reader(other):
                pass
            opened = []
            original = os.open
            def swapped(path, flags):
                fd = original(other, flags)
                opened.append(fd)
                return fd
            with mock.patch.object(files.os, 'open', side_effect=swapped):
                with self.assertRaises(OSError), files.file_reader(self.path):
                    pass
            os.fstat(opened[0])
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_open_failure_does_not_leave_empty_entry(self):
        with mock.patch.object(files.os, 'open', side_effect=OSError('fixture')):
            with self.assertRaises(OSError), files.file_reader(self.path):
                pass
        self.assertNotIn((self.path.stat().st_dev, self.path.stat().st_ino), files._files)

    def test_every_product_native_connection_supplies_physical_guard_path(self):
        calls = []
        for path in (SCRIPTS / 'task_governance_tool').rglob('*.py'):
            if path.name == 'sqlite_transaction_trace.py':
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'connect_traced':
                    self.assertIn('file_guard_path', {k.arg for k in node.keywords}, str(path))
                    calls.append(path.name)
        self.assertEqual(len(calls), 8)


if __name__ == '__main__':
    unittest.main()
