"""Connection inventory and real-driver transaction observation boundaries."""
import ast
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import unittest

from tests import test_sqlite_diagnostics as fixture_support
from task_governance_tool import debug_log, sqlite_diagnostics
from task_governance_tool.sqlite_transaction_trace import connect_traced


def connection_calls(source):
    """Find direct and imported/assigned aliases, retaining only code locations."""
    tree = ast.parse(source)
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                names[item.asname or item.name] = item.name
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                names[item.asname or item.name] = (node.module or '') + '.' + item.name

    def resolve(node):
        if isinstance(node, ast.Name):
            return names.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return resolve(node.value) + '.' + node.attr
        return ''

    # Assignment aliases may point to other aliases, including forward text
    # order in another function. Stop once the finite name mapping stabilizes.
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    for _ in range(len(assignments) + 1):
        changed = False
        for node in assignments:
            value = resolve(node.value)
            if value in {'sqlite3.connect', 'sqlite3.dbapi2.connect'}:
                for target in node.targets:
                    if isinstance(target, ast.Name) and names.get(target.id) != value:
                        names[target.id] = value
                        changed = True
        if not changed:
            break
    return [(node.lineno, resolve(node.func)) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and resolve(node.func) in
            {'sqlite3.connect', 'sqlite3.dbapi2.connect'}]


class ConnectionInventoryTests(unittest.TestCase):
    def test_native_connect_only_in_observer_delegate(self):
        package = fixture_support.ROOT / 'task-governance-tool/scripts'
        actual = {}
        for path in package.rglob('*.py'):
            calls = connection_calls(path.read_text(encoding='utf-8'))
            if calls:
                actual[path.relative_to(package).as_posix()] = len(calls)
        self.assertEqual(actual, {'task_governance_tool/sqlite_transaction_trace.py': 3})

    def test_inventory_detects_import_and_assignment_aliases(self):
        self.assertEqual(len(connection_calls('''
import sqlite3 as db
from sqlite3 import connect as opener
from sqlite3 import dbapi2 as driver
first = db.connect
second = first
db.connect('a'); opener('b'); driver.connect('c'); second('d')
''')), 4)


class TransactionCoverageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_support.SQLiteDiagnosticsTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_savepoints_outer_release_and_partial_rollback(self):
        f = self.fixture
        with f.scope(), closing(connect_traced(f.database)) as connection:
            cursor = connection.cursor()
            cursor.execute('/* private prefix */ SAVEPOINT private_name')
            cursor.execute("INSERT INTO items VALUES ('discard')")
            cursor.execute('ROLLBACK TO private_name')
            self.assertTrue(connection.in_transaction)
            cursor.execute('RELEASE private_name')
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute('SELECT count(*) FROM items').fetchone()[0], 1)
        records = f.records()
        self.assertEqual([r['phase'] for r in records if r['phase'] in
                         {'savepoint', 'rollback_to', 'release'}], ['savepoint', 'rollback_to', 'release'])
        self.assertEqual([r['end_reason'] for r in records if r['phase'] == 'transaction_end'], ['release'])
        self.assertNotIn('private', json.dumps(records))

    def test_script_new_and_restarted_transactions_have_unknown_lifetimes(self):
        f = self.fixture
        with f.scope(), closing(connect_traced(f.database)) as connection:
            connection.executescript('BEGIN; SELECT 1;')
            connection.executescript('BEGIN; SELECT 2;')
            connection.rollback()
        records = [r for r in f.records() if 'transaction_id' in r]
        self.assertTrue(records)
        self.assertTrue(all(r.get('lifetime') == 'unknown' for r in records))
        self.assertTrue(all('transaction_elapsed_ns' not in r for r in records))

    def test_cancelled_call_preserves_identity_and_transaction(self):
        f = self.fixture
        interrupted = KeyboardInterrupt('private payload')

        class Value:
            def __conform__(self, protocol):
                raise interrupted

        with f.scope(), closing(connect_traced(f.database)) as connection:
            connection.execute('BEGIN')
            try:
                connection.execute('SELECT ?', (Value(),))
            except BaseException as error:
                self.assertIs(error, interrupted)
            else:
                self.fail('native cancellation was lost')
            self.assertTrue(connection.in_transaction)
            connection.rollback()
        self.assertEqual([r['outcome'] for r in f.records() if r['phase'] == 'statement'], ['cancelled'])

    def test_real_backup_success_and_failure_are_aggregated(self):
        f = self.fixture
        with f.scope(), closing(connect_traced(f.database)) as source:
            with closing(connect_traced(':memory:', role='backup', access='create')) as destination:
                source.backup(destination)
                self.assertEqual(destination.execute('SELECT count(*) FROM items').fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                source.backup(destination)
        self.assertEqual([r['outcome'] for r in f.records() if r['phase'] == 'backup'], ['succeeded', 'failed'])
        self.assertTrue(any(r['role'] == 'backup' and r['phase'] == 'close' for r in f.records()))

    def test_native_autocommit_false_restarts_do_not_join_lifetimes(self):
        f = self.fixture
        states = []
        for opener in (sqlite3.connect, connect_traced):
            observed = []
            with f.scope(), closing(opener(f.database, autocommit=False)) as connection:
                observed.append(connection.in_transaction)
                connection.execute('SELECT 1')
                connection.commit()
                observed.append(connection.in_transaction)
                connection.rollback()
                observed.append(connection.in_transaction)
            states.append(observed)
        self.assertEqual(states[0], states[1])
        starts = [r for r in f.records() if r['phase'] == 'implicit_begin']
        self.assertEqual(len(starts), 3)
        self.assertEqual(len({r['transaction_id'] for r in starts}), 3)

    def test_autocommit_true_noop_methods_and_contexts_keep_one_transaction(self):
        f = self.fixture
        states = []
        for opener in (sqlite3.connect, connect_traced):
            observed = []
            with f.scope() as collector, closing(opener(':memory:', autocommit=True)) as connection:
                connection.execute('CREATE TABLE values_under_test (value)')
                connection.execute('BEGIN')
                connection.execute('INSERT INTO values_under_test VALUES (1)')
                for action in (connection.commit, connection.rollback):
                    action()
                    observed.append((connection.in_transaction,
                                     connection.execute('SELECT count(*) FROM values_under_test').fetchone()))
                with connection:
                    connection.execute('SELECT 1')
                with self.assertRaises(ValueError):
                    with connection:
                        raise ValueError('fixture cancellation')
                observed.append((connection.in_transaction,
                                 connection.execute('SELECT count(*) FROM values_under_test').fetchone()))
                connection.execute('ROLLBACK')
                observed.append((connection.in_transaction,
                                 connection.execute('SELECT count(*) FROM values_under_test').fetchone()))
            states.append(observed)
        self.assertEqual(states[0], states[1])
        self.assertEqual(states[1], [(True, (1,))] * 3 + [(False, (0,))])
        records = collector.records
        self.assertEqual([r['end_reason'] for r in records if r['phase'] == 'transaction_end'], ['rollback'])
        self.assertEqual(len({r['transaction_id'] for r in records if 'transaction_id' in r}), 1)
        self.assertFalse(any(r['phase'] == 'implicit_begin' for r in records))

    def test_commented_partial_rollbacks_preserve_outer_identity_in_all_modes(self):
        f = self.fixture
        variants = ('ROLLBACK /* bounded comment */ TO s', 'ROLLBACK/*x*/TO s',
                    'ROLLBACK TRANSACTION /*x*/ TO s', 'ROLLBACK --x\n TO s')
        for mode in (True, False, sqlite3.LEGACY_TRANSACTION_CONTROL):
            for explicit_cursor in (False, True):
                with self.subTest(mode=mode, explicit_cursor=explicit_cursor):
                    with f.scope() as collector, closing(connect_traced(':memory:', autocommit=mode)) as connection:
                        target = connection.cursor() if explicit_cursor else connection
                        target.execute('CREATE TABLE values_under_test (value)')
                        if not connection.in_transaction:
                            target.execute('; /*x*/ BEGIN/*x*/IMMEDIATE')
                        for statement in variants:
                            target.execute('SAVEPOINT/*x*/s')
                            target.execute('INSERT INTO values_under_test VALUES (1)')
                            target.execute(statement)
                            self.assertTrue(connection.in_transaction)
                            self.assertEqual(target.execute('SELECT count(*) FROM values_under_test').fetchone(), (0,))
                            target.execute('RELEASE/*x*/s')
                        target.execute('ROLLBACK')
                    self.assertEqual(len([r for r in collector.records if r['phase'] == 'rollback_to']), 4)
                    self.assertEqual([r['end_reason'] for r in collector.records if r['phase'] == 'transaction_end'], ['rollback'])
                    self.assertEqual(len({r['transaction_id'] for r in collector.records if 'transaction_id' in r}), 1)

    def test_control_prefix_budget_does_not_guess_partial_rollback(self):
        from task_governance_tool.sqlite_transaction_trace import _control
        for comment in ('/*' + 'x' * 5000 + '*/', '--' + 'x' * 5000 + '\n'):
            self.assertEqual(_control(('ROLLBACK ' + comment + ' TO s',), {}), ('statement', None))

    def test_cleanup_label_is_admitted_by_sqlite_only_sink(self):
        f = self.fixture
        with sqlite_diagnostics.diagnostic_scope(f.root, entrypoint=f.entrypoint,
                operation='review_wait_cleanup', repo_explicit=True, validated=True) as collector:
            self.assertIsNotNone(collector)
            with closing(connect_traced(f.database)) as connection:
                connection.execute('BEGIN')
                connection.rollback()
        self.assertEqual({document['operation'] for document in f.documents()}, {'review_wait_cleanup'})

    def test_close_failure_and_late_cleanup_never_publish_success(self):
        f = self.fixture
        errors = []
        with f.scope() as collector:
            connection = connect_traced(f.database)
            def close_in_wrong_thread():
                try:
                    connection.close()
                except sqlite3.ProgrammingError:
                    errors.append('native thread guard')
            worker = threading.Thread(target=close_in_wrong_thread)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ['native thread guard'])
        count = len(collector.records)
        connection.close()
        self.assertEqual(len(collector.records), count)
        self.assertEqual(f.documents(), [])

    def test_worker_owns_fresh_scope_and_common_sink_does_not_duplicate(self):
        f = self.fixture
        directory = f.root / '.taskgov/diagnostics/logger'
        directory.mkdir()
        (directory / 'enabled').write_bytes(b'taskgov-logger-v1 debug\n')
        identities = []
        errors = []
        def operation():
            try:
                with debug_log.operation_scope(f.root, entrypoint=f.entrypoint,
                        operation='review_wait_worker', repo_explicit=True, independent=True) as session:
                    debug_log.allow_output()  # Existing binding admission stand-in.
                    identities.append(session.invocation_id)
                    with closing(connect_traced(f.database)) as connection:
                        connection.execute('BEGIN')
                        connection.rollback()
            except BaseException as error:
                errors.append(type(error).__name__)
        with debug_log.operation_scope(f.root, entrypoint=f.entrypoint,
                operation='review_wait', repo_explicit=True, validated=True) as parent:
            identities.append(parent.invocation_id)
            worker = threading.Thread(target=operation)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertIs(sqlite_diagnostics.current_collector(), parent.collector)
        self.assertEqual(errors, [])
        self.assertEqual(len(set(identities)), 2)
        rows = [json.loads(line) for path in directory.glob('taskgov-debug.log.*')
                for line in path.read_bytes().splitlines()]
        opens = [r for r in rows if r.get('phase') == 'open']
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]['operation'], 'review_wait_worker')
        self.assertEqual(f.documents(), [])
