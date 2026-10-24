"""Real-driver SQL/fetch observations and differential native semantics."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest import mock

from tests import test_debug_log as fixtures
from task_governance_tool import sqlite_sql_trace as trace
from task_governance_tool.sqlite_transaction_trace import connect_traced


class SQLTraceTests(unittest.TestCase):
    setUp = fixtures.DebugLogTests.setUp
    configure = fixtures.DebugLogTests.configure
    enable = fixtures.DebugLogTests.enable
    scope = fixtures.DebugLogTests.scope
    records = fixtures.DebugLogTests.records

    def executions(self):
        return [r for r in self.records() if r['event'] == 'db.sql.execution']

    def test_execute_fetch_and_cursor_reuse_have_exact_ids(self):
        self.configure()
        with self.scope(), closing(connect_traced(':memory:')) as c:
            a = c.execute('SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3')
            b = c.cursor()
            bound_execute = b.execute
            self.assertIs(bound_execute('SELECT 4'), b)
            self.assertEqual(a.fetchone(), (1,))
            self.assertEqual(b.fetchall(), [(4,)])
            self.assertEqual(a.fetchmany(1), [(2,)])
            self.assertEqual(list(a), [(3,)])
            self.assertIs(a.execute('SELECT 5 WHERE 0'), a)
            self.assertEqual(a.fetchall(), [])
        rows = self.executions()
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted(r['rows_lower_bound'] for r in rows), [0, 1, 3])
        self.assertTrue(all(r['consumption'] == 'complete' for r in rows))
        self.assertEqual(len({r['execution_id'] for r in rows}), 3)
        self.assertEqual(len({r['connection_id'] for r in rows}), 1)
        self.assertTrue(all(r['execute_ns'] > 0 and r['fetch_ns'] > 0 for r in rows))

    def test_repeated_queries_aggregate_beyond_detail_limit_without_extra_sql(self):
        self.configure()
        for count in (1, 16, 1000):
            with self.scope() as session, closing(connect_traced(':memory:')) as c:
                actual = []
                c.set_trace_callback(actual.append)
                for _ in range(count):
                    c.execute('SELECT 42').fetchall()
                self.assertEqual(actual, ['SELECT 42'] * count)
            records = [r for r in self.records() if r['invocation_id'] == session.invocation_id]
            group = next(r for r in records if r['event'] == 'db.sql.aggregate')
            self.assertEqual((group['executions'], group['fetch_calls'], group['rows_lower_bound']),
                             (count, count, count))
            self.assertGreaterEqual(group['execute_ns'], group['execute_max_ns'])
            self.assertGreaterEqual(group['fetch_ns'], group['fetch_max_ns'])
            summary = next(r for r in records if r['event'] == 'db.sql.summary')
            self.assertEqual(summary['api_calls'], count * 2)
            self.assertEqual(summary['details_omitted'], max(0, count - trace.MAX_DETAILS))
            self.assertLessEqual(len(records), 256)

    def test_shortcuts_native_contract_and_transaction_modes(self):
        def scenario(c):
            out = []
            c.execute('CREATE TABLE t(v)')
            cursor = c.executemany('INSERT INTO t VALUES (?)', [(1,), (2,)])
            out.append((cursor.rowcount, cursor.lastrowid, cursor.description))
            cursor.execute('INSERT INTO t VALUES (3) RETURNING v')
            out.append((cursor.fetchone(), cursor.fetchall(), cursor.rowcount, cursor.lastrowid))
            c.row_factory = sqlite3.Row
            r = c.execute('SELECT v FROM t').fetchall()
            out.append([tuple(x) for x in r])
            c.commit()
            out.append(c.in_transaction)
            return out
        self.configure()
        for mode in (sqlite3.LEGACY_TRANSACTION_CONTROL, True, False):
            with closing(sqlite3.connect(':memory:', autocommit=mode)) as c:
                expected = scenario(c)
            with self.scope(), closing(connect_traced(':memory:', autocommit=mode)) as c:
                self.assertEqual(scenario(c), expected)

    def test_script_preserves_prior_result_and_is_one_aggregate(self):
        self.configure()
        with self.scope(), closing(connect_traced(':memory:')) as c:
            cursor = c.execute('SELECT 1 UNION ALL SELECT 2')
            self.assertEqual(cursor.fetchone(), (1,))
            cursor.executescript('CREATE TABLE t(v); INSERT INTO t VALUES (9); SELECT * FROM t;')
            self.assertEqual(cursor.fetchall(), [(2,)])
        rows = self.executions()
        self.assertEqual(len(rows), 2)
        script = next(r for r in rows if r['api'] == 'executescript')
        self.assertEqual(script['internal_statements'], 'unknown')
        self.assertEqual(script['consumption'], 'discarded')
        query = next(r for r in rows if r['api'] == 'execute')
        self.assertEqual(query['rows_lower_bound'], 2)

    def test_failed_reexecute_never_attributes_old_rows_to_failed_sql(self):
        self.configure()
        with self.scope(), closing(connect_traced(':memory:')) as c:
            cursor = c.execute('SELECT 1 UNION ALL SELECT 2')
            cursor.fetchone()
            with self.assertRaises(TypeError):
                cursor.execute(3)
            self.assertEqual(cursor.fetchall(), [(2,)])
        failed = next(r for r in self.executions() if r['outcome'] == 'failed')
        self.assertEqual(failed['rows_lower_bound'], 0)
        self.assertEqual(failed['consumption'], 'unknown')
        unbound = next(r for r in self.executions() if r['binding'] == 'unbound')
        self.assertEqual(unbound['rows_lower_bound'], 1)

    def test_partial_reuse_close_and_no_prefetch(self):
        self.configure()
        visited = []
        with self.scope(), closing(connect_traced(':memory:')) as c:
            c.create_function('observe', 1, lambda x: visited.append(x) or x)
            a = c.execute('SELECT observe(1) UNION ALL SELECT observe(2) UNION ALL SELECT observe(3)')
            self.assertEqual(visited, [1])  # native execute steps once; no instrumentation fetch
            a.fetchone()
            a.close()
            c.execute('SELECT 4').close()
        self.assertEqual(visited, [1, 2])
        self.assertTrue(all(r['consumption'] == 'partial' for r in self.executions()))
        self.assertEqual(sorted(r['rows_lower_bound'] for r in self.executions()), [0, 1])

    def test_generator_partial_failure_cancellation_and_trigger(self):
        self.configure()
        cancellation = KeyboardInterrupt('private-secret')
        def parameters():
            yield (1,)
            raise cancellation
        with self.scope(), closing(connect_traced(':memory:')) as c:
            c.executescript('CREATE TABLE t(v); CREATE TABLE u(v); CREATE TRIGGER tr AFTER INSERT ON t BEGIN INSERT INTO u VALUES(new.v); END;')
            with self.assertRaises(KeyboardInterrupt) as caught:
                c.executemany('INSERT INTO t VALUES(?)', parameters())
            self.assertIs(caught.exception, cancellation)
            self.assertEqual(c.execute('SELECT * FROM u').fetchall(), [(1,)])
        row = next(r for r in self.executions() if r['api'] == 'executemany')
        self.assertEqual(row['outcome'], 'cancelled')
        self.assertEqual(row['internal_statements'], 'unknown')
        self.assertEqual(row['consumption'], 'unknown')
        self.assertNotIn('private-secret', json.dumps(self.records()))

    def test_custom_row_factory_none_stop_iteration_and_fetch_failure(self):
        self.configure()
        original = ValueError('private-factory')
        def broken(cursor, row):
            raise original
        def stopped(cursor, row):
            raise StopIteration
        with self.scope(), closing(connect_traced(':memory:')) as c:
            c.row_factory = lambda cursor, row: None
            a = c.execute('SELECT 1 UNION ALL SELECT 2')
            self.assertIsNone(a.fetchone())
            self.assertEqual(a.fetchall(), [None])
            a.close()
            c.row_factory = stopped
            b = c.execute('SELECT 1')
            with self.assertRaises(StopIteration):
                next(b)
            b.close()
            c.row_factory = broken
            d = c.execute('SELECT 1')
            with self.assertRaises(ValueError) as caught:
                d.fetchall()
            self.assertIs(caught.exception, original)
            d.close()
        rows = self.executions()
        self.assertFalse(rows[0]['rows_known'])
        self.assertEqual(rows[0]['rows_lower_bound'], 1)
        self.assertEqual(rows[1]['consumption'], 'unknown')
        self.assertEqual(rows[2]['outcome'], 'failed')

    def test_nested_udf_has_parent_and_inclusive_nonadditive_times(self):
        self.configure()
        with self.scope(), closing(connect_traced(':memory:')) as c:
            def child():
                return c.execute('SELECT 7').fetchone()[0]
            c.create_function('nested', 0, child)
            c.execute('SELECT nested()').fetchall()
        rows = self.executions()
        outer = next(r for r in rows if r['parent_execution_id'] is None)
        inner = next(r for r in rows if r['parent_execution_id'] is not None)
        self.assertEqual(inner['parent_execution_id'], outer['execution_id'])
        self.assertGreaterEqual(outer['execute_ns'], inner['execute_ns'])
        self.assertTrue(all(r['timing_scope'] == 'inclusive_native_api' for r in rows))

    def test_metrics_failure_does_not_repeat_native_call_or_replace_error(self):
        self.configure()
        with self.scope(), closing(connect_traced(':memory:')) as c:
            actual = []
            c.set_trace_callback(actual.append)
            for method in ('begin', 'observed', 'end'):
                with mock.patch.object(trace.Metrics, method, side_effect=RuntimeError('diagnostic')):
                    cursor = c.execute('SELECT 8')
                    self.assertEqual(cursor.fetchall(), [(8,)])
                    cursor.close()
                    with self.assertRaises(sqlite3.OperationalError):
                        c.execute('bad-sql')
            self.assertEqual(actual, ['SELECT 8'] * 3)
        self.assertGreater(next(r for r in self.records() if r['event'] == 'db.sql.summary')['observation_failures'], 0)

    def test_off_errors_readonly_and_factory_boundaries(self):
        self.marker.unlink()
        with self.scope(), closing(connect_traced(':memory:')) as c:
            self.assertIs(type(c.execute('SELECT 1')), sqlite3.Cursor)
        self.assertEqual(self.records(), [])
        self.configure('errors')
        with self.scope(), closing(connect_traced(':memory:')) as c:
            c.execute('SELECT 1').fetchall()
            with self.assertRaises(sqlite3.OperationalError):
                c.execute('private-bad-sql')
        self.assertTrue(any(r['event'] == 'db.sql.failure' for r in self.records()))
        self.assertFalse(any(r['event'] in {'db.sql.execution', 'db.sql.aggregate'} for r in self.records()))
        before = self.records()
        with self.scope(read_only=True), closing(connect_traced(':memory:')) as c:
            c.execute('SELECT 1').fetchall()
        self.assertEqual(before, self.records())
        self.configure()
        class Custom(sqlite3.Cursor):
            pass
        with self.scope(), closing(connect_traced(':memory:')) as c:
            self.assertIs(type(c.cursor(Custom)), Custom)
        self.assertTrue(next(r for r in reversed(self.records()) if r['event'] == 'db.sql.summary')['partial'])

    def test_capacity_keeps_aggregate_and_reports_untracked_unknown_and_late_error(self):
        self.configure()
        with self.scope(), closing(connect_traced(':memory:')) as c:
            with mock.patch.object(trace, 'MAX_GROUPS', 2), mock.patch.object(trace, 'MAX_ACTIVE', 2):
                with mock.patch.object(trace, '_source', side_effect=lambda api: 'site.' + str(counter[0])):
                    counter = [0]
                    cursors = []
                    for n in range(4):
                        counter[0] = n
                        cursors.append(c.execute('SELECT 1'))
                    cursors[-1].fetchall()
                for cursor in cursors:
                    cursor.close()
            for _ in range(100):
                c.execute('SELECT 2').fetchall()
            with self.assertRaises(sqlite3.OperationalError):
                c.execute('bad-sql')
        summary = next(r for r in self.records() if r['event'] == 'db.sql.summary')
        self.assertTrue(summary['partial'])
        self.assertGreater(summary['aggregate_omitted_calls'], 0)
        self.assertGreater(summary['untracked_executions'], 0)
        self.assertGreater(summary['unbound_fetch_calls'], 0)
        self.assertTrue(any(r['outcome'] == 'failed' for r in self.executions()))

    def test_sql_parameters_and_rows_never_enter_buffer_or_log(self):
        secret = 'private-secret-token'
        self.configure()
        with self.scope() as session, closing(connect_traced(':memory:')) as c:
            c.execute('SELECT ? /* private-sql-comment */', (secret,)).fetchall()
            self.assertNotIn(secret, repr(session.collector.sql_metrics.active))
            self.assertNotIn(secret, repr(session.collector.sql_metrics.details))
        raw = json.dumps(self.records())
        self.assertNotIn(secret, raw)
        self.assertNotIn('private-sql-comment', raw)
        self.assertNotIn('SELECT', raw)

    def test_native_invalid_arguments_and_closed_cursor_errors_are_preserved(self):
        def cases(connection):
            out = []
            for method, args, kwargs in (
                ('execute', (), {}), ('execute', (3,), {}),
                ('execute', (), {'sql': 'SELECT 1'}),
                ('execute', ('SELECT ?',), {}),
                ('executemany', ('SELECT 1', []), {}),
                ('executescript', (), {'sql_script': 'SELECT 1;'}),
            ):
                try:
                    getattr(connection, method)(*args, **kwargs)
                except BaseException as exc:
                    out.append((type(exc).__name__, str(exc)))
                else:
                    out.append('success')
            cursor = connection.execute('SELECT 1')
            cursor.close()
            for method in ('fetchone', 'fetchmany', 'fetchall', '__next__'):
                try:
                    getattr(cursor, method)()
                except BaseException as exc:
                    out.append((type(exc).__name__, str(exc)))
            return out
        self.configure()
        with closing(sqlite3.connect(':memory:')) as c:
            expected = cases(c)
        with self.scope(), closing(connect_traced(':memory:')) as c:
            self.assertEqual(cases(c), expected)

    def test_database_contention_and_interruption_keep_native_error_codes(self):
        self.configure()
        with closing(sqlite3.connect(self.database)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            with self.scope(), closing(connect_traced(self.database, timeout=0)) as c:
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    c.execute("INSERT INTO items VALUES('blocked')")
                self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
                c.set_progress_handler(lambda: 1, 1)
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    c.execute('WITH RECURSIVE n(v) AS (SELECT 1 UNION ALL SELECT v+1 FROM n WHERE v<100) SELECT * FROM n').fetchall()
                self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_INTERRUPT)
        self.assertEqual(len([r for r in self.executions() if r['outcome'] == 'failed']), 2)

    def test_source_position_and_sql_day_grouping_are_code_owned(self):
        from task_governance_tool import sqlite_connection
        self.configure()
        moment = {'observed_at': '2026-10-23T14:59:59+00:00',
                  'log_date': '2026-10-23', 'utc_offset': '+0900'}
        with self.scope(), closing(sqlite_connection.connect_existing(self.database)) as c:
            with mock.patch.object(trace, '_moment', return_value=moment):
                c.execute('SELECT 1').fetchall()
        rows = self.executions()
        self.assertTrue(any(r['sql_id'].startswith('sqlite_connection.') for r in rows))
        self.assertTrue(any(r['log_date'] == '2026-10-23' for r in rows))

    def test_sink_failure_and_no_io_during_sql_keep_primary_results(self):
        from pathlib import Path
        from task_governance_tool import debug_log_sink
        self.configure()
        with mock.patch.object(debug_log_sink.DailySink, 'write', return_value='write_failed'):
            with self.scope(), closing(connect_traced(':memory:')) as c:
                with mock.patch.object(Path, 'resolve', side_effect=AssertionError('file probe')):
                    self.assertEqual(c.execute('SELECT 6').fetchall(), [(6,)])
        self.assertEqual(self.records(), [])


if __name__ == '__main__':
    unittest.main()
