"""Borrowed connections, ambiguous EOF and failed observation must stay honest."""
from contextlib import closing
import sqlite3
import threading
import unittest
from unittest import mock

from tests import test_sqlite_sql_trace as support
from task_governance_tool import sqlite_diagnostics
from task_governance_tool import sqlite_sql_trace as trace
from task_governance_tool import sqlite_transaction_trace as transactions


class SQLBoundaryTests(unittest.TestCase):
    setUp = support.SQLTraceTests.setUp
    configure = support.SQLTraceTests.configure
    enable = support.SQLTraceTests.enable
    scope = support.SQLTraceTests.scope
    records = support.SQLTraceTests.records
    executions = support.SQLTraceTests.executions

    def test_foreign_thread_fetch_and_close_suppress_publication_even_on_native_failure(self):
        self.configure()
        for guard in (True, False):
            for method in ('fetchone', 'fetchmany', 'fetchall', '__next__', 'close'):
                with self.subTest(guard=guard, method=method):
                    observed = []
                    with self.scope() as session, closing(transactions.connect_traced(
                            ':memory:', check_same_thread=guard)) as c:
                        cursor = c.execute('SELECT 1')
                        def worker():
                            try:
                                observed.append(('return', getattr(cursor, method)()))
                            except BaseException as error:
                                observed.append(('error', type(error)))
                        thread = threading.Thread(target=worker)
                        thread.start()
                        thread.join(timeout=5)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(observed[0][0], 'error' if guard else 'return')
                        if guard:
                            self.assertIs(observed[0][1], sqlite3.ProgrammingError)
                        cursor.close()
                        self.assertTrue(session.collector._untracked)
                    self.assertEqual(self.records(), [])

    def test_suppressed_existing_connections_never_publish_fetch_or_sibling_apis(self):
        self.configure()
        actions = {
            'fetchone': lambda c, r: r.fetchone(),
            'fetchmany': lambda c, r: r.fetchmany(),
            'fetchall': lambda c, r: r.fetchall(),
            'next': lambda c, r: next(r),
            'cursor_execute': lambda c, r: r.execute('SELECT 2'),
            'shortcut': lambda c, r: c.execute('SELECT 2'),
            'script': lambda c, r: r.executescript('SELECT 2;'),
            'many': lambda c, r: c.executemany('INSERT INTO t VALUES (?)', [(2,)]),
            'cursor_close': lambda c, r: r.close(),
            'cursor_create': lambda c, r: c.cursor(),
            'commit': lambda c, r: c.commit(),
            'rollback': lambda c, r: c.rollback(),
            'connection_close': lambda c, r: c.close(),
        }
        for name, action in actions.items():
            with self.subTest(name=name):
                with self.scope() as session, closing(transactions.connect_traced(':memory:')) as c:
                    c.execute('CREATE TABLE t(v)')
                    cursor = c.execute('SELECT 1')
                    with self.scope(read_only=True):
                        action(c, cursor)
                    self.assertTrue(session.collector._untracked)
                self.assertEqual(self.records(), [])

    def test_dedicated_transaction_observer_uses_the_same_suppression_guard(self):
        with sqlite_diagnostics.diagnostic_scope(self.root, entrypoint=self.entrypoint,
                operation='task', repo_explicit=True, validated=True) as collector:
            with closing(transactions.connect_traced(':memory:')) as c:
                with sqlite_diagnostics.suppress_diagnostics():
                    c.execute('BEGIN')
                c.rollback()
            self.assertTrue(collector._untracked)
        self.assertEqual(list(self.directory.glob('trace-*.json')), [])

    def test_converter_and_text_factory_stop_iteration_are_unknown_in_debug_and_errors(self):
        original = StopIteration('private-conversion')
        calls = []
        def conversion(value):
            calls.append(1)
            raise original
        with mock.patch.dict(sqlite3.converters, {'TRACE_STOP': conversion}):
            for mode in ('debug', 'errors'):
                self.configure(mode)
                for kind in ('converter', 'text_factory', 'row_factory'):
                    with self.subTest(mode=mode, kind=kind):
                        before = len(self.records())
                        calls.clear()
                        flags = sqlite3.PARSE_COLNAMES if kind == 'converter' else 0
                        with self.scope(), mock.patch.object(trace, '_source', return_value='owned.call:1:1:execute'), closing(transactions.connect_traced(':memory:', detect_types=flags)) as c:
                            if kind == 'text_factory':
                                def text_factory(value):
                                    c.text_factory = str
                                    return conversion(value)
                                c.text_factory = text_factory
                            elif kind == 'row_factory':
                                # Changing factory just before the exception
                                # cannot turn it into known ordinary EOF.
                                def row_factory(cursor, row):
                                    cursor.row_factory = None
                                    return conversion(row)
                                c.row_factory = row_factory
                            cursor = c.execute('SELECT \'row\' AS "v [TRACE_STOP]"')
                            with self.assertRaises(StopIteration) as caught:
                                next(cursor)
                            self.assertIs(caught.exception, original)
                            self.assertEqual(len(calls), 1)
                            cursor.close()
                        records = self.records()[before:]
                        event = next(r for r in records if r['event'] ==
                                     ('db.sql.execution' if mode == 'debug' else 'db.sql.failure'))
                        self.assertEqual(event['outcome'], 'unknown')
                        if mode == 'debug':
                            self.assertEqual(event['consumption'], 'unknown')
                            self.assertFalse(event['rows_known'])
                            summary = next(r for r in records if r['event'] == 'db.sql.summary')
                            self.assertEqual(summary['unknown_source_executions'], 0)
                            self.assertTrue(summary['partial'])
                        self.assertNotIn('private-conversion', str(records))

    def test_ordinary_iterator_eof_is_complete_and_not_an_errors_event(self):
        for mode in ('debug', 'errors'):
            self.configure(mode)
            before = len(self.records())
            with self.scope(), closing(transactions.connect_traced(':memory:')) as c:
                self.assertEqual(list(c.execute('SELECT 1')), [(1,)])
            records = self.records()[before:]
            if mode == 'debug':
                event = next(r for r in records if r['event'] == 'db.sql.execution')
                self.assertEqual((event['outcome'], event['consumption']), ('succeeded', 'complete'))
            else:
                self.assertEqual(records, [])

    def test_self_changing_row_factory_none_is_not_reported_as_eof(self):
        self.configure()
        with self.scope(), closing(transactions.connect_traced(':memory:')) as c:
            calls = []
            def factory(cursor, row):
                calls.append(1)
                cursor.row_factory = None
                return None
            c.row_factory = factory
            cursor = c.execute('SELECT 1 UNION ALL SELECT 2')
            self.assertIsNone(cursor.fetchone())
            self.assertEqual(calls, [1])
            cursor.close()
        event = self.executions()[0]
        self.assertEqual(event['consumption'], 'unknown')
        self.assertFalse(event['rows_known'])
        self.assertEqual(event['rows_lower_bound'], 0)
        # Native semantics really leave another row after the ambiguous None.
        with closing(sqlite3.connect(':memory:')) as c:
            c.row_factory = factory
            cursor = c.execute('SELECT 1 UNION ALL SELECT 2')
            self.assertIsNone(cursor.fetchone())
            self.assertEqual(cursor.fetchone(), (2,))

    def test_execute_and_fetch_errors_remain_failed_when_aggregation_raises(self):
        self.configure()
        for api in ('execute', 'script', 'many', 'keyword', 'fetch'):
            with self.subTest(api=api):
                before = len(self.records())
                with self.scope(), closing(transactions.connect_traced(':memory:')) as c:
                    c.execute('CREATE TABLE t(v)')
                    cursor = c.execute('SELECT 1')
                    def broken(cursor, row):
                        raise ValueError('private-row')
                    cursor.row_factory = broken
                    def bad_values():
                        yield (1,)
                        raise KeyboardInterrupt('private-generator')
                    with mock.patch.object(trace.Metrics, 'observed', side_effect=RuntimeError('observer')):
                        expected_type = {'execute': sqlite3.OperationalError, 'script': sqlite3.OperationalError,
                                         'many': KeyboardInterrupt, 'keyword': TypeError, 'fetch': ValueError}[api]
                        with self.assertRaises(expected_type):
                            if api == 'execute':
                                c.execute('bad sql')
                            elif api == 'script':
                                c.executescript('bad sql')
                            elif api == 'many':
                                c.executemany('INSERT INTO t VALUES (?)', bad_values())
                            elif api == 'keyword':
                                c.execute(sql='SELECT 1')
                            else:
                                cursor.fetchall()
                    cursor.close()
                rows = [r for r in self.records()[before:] if r['event'] == 'db.sql.execution']
                expected = 'cancelled' if api == 'many' else 'failed'
                self.assertTrue(any(r['outcome'] == expected for r in rows), rows)
                self.assertTrue(all(r['outcome'] != 'succeeded' for r in rows if r['binding'] == 'unknown_after_execute_failure'))

    def test_missing_transaction_observation_is_unknown_not_success(self):
        self.configure()
        with self.scope(), closing(transactions.connect_traced(':memory:')) as c:
            with mock.patch.object(transactions._ObservedConnection, '_after', side_effect=RuntimeError('observer')):
                with self.assertRaises(sqlite3.OperationalError):
                    c.execute('bad sql')
        event = self.executions()[0]
        self.assertEqual((event['outcome'], event['consumption'], event['execute_ns']),
                         ('unknown', 'unknown', None))

    def test_publication_itself_rejects_foreign_thread_and_suppressed_context(self):
        self.configure()
        for kind in ('thread', 'suppression'):
            with self.subTest(kind=kind), self.scope() as session:
                with closing(transactions.connect_traced(':memory:')) as c:
                    c.execute('SELECT 1').fetchall()
                if kind == 'thread':
                    worker = threading.Thread(target=session.finish)
                    worker.start()
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())
                else:
                    with sqlite_diagnostics.suppress_diagnostics():
                        session.finish()
            self.assertEqual(self.records(), [])


if __name__ == '__main__':
    unittest.main()
