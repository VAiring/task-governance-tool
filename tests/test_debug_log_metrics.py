"""Bounded general measurements and their real SQLite/native boundaries."""
from contextlib import closing
from pathlib import Path
from types import FunctionType
import unittest
from unittest import mock
import weakref

from tests import test_debug_log as fixtures
from tests.test_sqlite_diagnostics import SECRET
from task_governance_tool import debug_log as log, debug_log_metrics as metrics
from task_governance_tool import debug_log_values as values, sqlite_sql_trace as sql
from task_governance_tool.sqlite_transaction_trace import connect_traced


def source_function(line=10):
    """Give fixture functions distinct lexical locations without source I/O."""
    def process(task_id=None):
        pass
    code = process.__code__.replace(
        co_filename=str(Path(values.__file__).with_name('metrics_fixture.py')),
        co_firstlineno=line)
    return FunctionType(code, {})


class DebugLogMetricsTests(unittest.TestCase):
    setUp = fixtures.DebugLogTests.setUp
    enable = fixtures.DebugLogTests.enable
    configure = fixtures.DebugLogTests.configure
    scope = fixtures.DebugLogTests.scope
    records = fixtures.DebugLogTests.records

    def events(self, kind):
        return [row for row in self.records() if row['event'] == kind]

    def test_repeated_bodies_have_exact_totals_and_no_per_call_events(self):
        self.configure()
        function = source_function()
        function_reference = weakref.ref(function)
        with self.scope() as session:
            with mock.patch.object(log.time, 'perf_counter_ns', side_effect=[100, 110, 200, 230, 300, 300]), \
                    mock.patch.object(metrics, 'code_location', wraps=metrics.code_location) as project:
                for counts in ((0,), (2, 3), ()):
                    with log.aggregate(function) as work:
                        for count in counts:
                            work.count(count)
                project.assert_called_once()
            self.assertEqual(len(session.performance.methods), 1)
            del function
            self.assertIsNone(function_reference())
            self.assertEqual(session.events, [])
        self.assertEqual(session.performance.methods, {})
        groups = self.events('performance.aggregate')
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual((group['calls'], group['measured_calls'], group['total_ns'], group['max_ns']),
                         (3, 3, 40, 30))
        self.assertEqual((group['item_count'], group['counted_calls'], group['unknown_count_calls']), (5, 2, 1))
        self.assertEqual(group['timing_scope'], 'inclusive_body')
        self.assertFalse(self.events('performance.summary')[0]['partial'])

    def test_span_count_does_not_restart_body_clock_or_retain_arguments(self):
        self.configure()
        captured = []
        with self.scope() as session:
            with mock.patch.object(session, 'emit', side_effect=lambda event, data, **kw: captured.append((event, data))), \
                    mock.patch.object(log.time, 'perf_counter_ns', side_effect=[100, 140]):
                with log.span(source_function(), task_id=SECRET) as work:
                    self.assertIsNone(work.function)
                    self.assertIsNone(work.arguments)
                    work.count(2)
                    work.count(3)
        end = next(data for event, data in captured if event == 'method.end')
        self.assertEqual(end['duration_ns'], 40)
        self.assertEqual(end['item_count'], 5)
        self.assertTrue(end['item_count_known'])
        self.assertNotIn(SECRET, repr(captured))

    def test_counts_distinguish_missing_zero_invalid_values_and_saturation(self):
        class Hostile:
            def __int__(self):
                raise AssertionError('converted count')
            def __repr__(self):
                raise AssertionError('rendered count')
            def __lt__(self, other):
                raise AssertionError('compared count')

        cases = (((), None, False, False), ((0,), 0, True, False),
                 ((True,), None, False, False), ((-1,), None, False, False),
                 ((Hostile(),), None, False, False), ((0, False), 0, False, False),
                 ((metrics.MAX_COUNT, 1), metrics.MAX_COUNT, False, True))
        self.configure()
        with self.scope():
            for additions, _, _, _ in cases:
                with log.span(source_function()) as work:
                    for value in additions:
                        work.count(value)
            with log.aggregate(source_function()) as work:
                work.count(0)
                work.count(False)
        ends = self.events('method.end')
        self.assertEqual(len(ends), len(cases))
        for end, (_, count, known, saturated) in zip(ends, cases):
            self.assertEqual((end['item_count'], end['item_count_known'], end['count_saturated']),
                             (count, known, saturated))
        group = self.events('performance.aggregate')[0]
        self.assertEqual((group['item_count'], group['counted_calls'], group['unknown_count_calls']), (0, 0, 1))

    def test_group_limit_keeps_existing_group_counts_after_300_calls(self):
        self.configure()
        functions = [source_function(10 + index) for index in range(metrics.MAX_GROUPS + 2)]
        with self.scope() as session:
            with mock.patch.object(metrics, 'code_location', wraps=metrics.code_location) as project:
                for function in functions:
                    with log.aggregate(function) as work:
                        work.count(1)
                for _ in range(300):
                    with log.aggregate(functions[0]) as work:
                        work.count(2)
                self.assertEqual(project.call_count, metrics.MAX_GROUPS + 2)
            self.assertEqual(len(session.performance.groups), metrics.MAX_GROUPS)
            self.assertEqual(len(session.performance.methods), metrics.MAX_GROUPS)
            self.assertEqual(session.events, [])
        groups = self.events('performance.aggregate')
        self.assertEqual(len(groups), metrics.MAX_GROUPS)
        retained = next(row for row in groups if row['method']['line'] == 10)
        self.assertEqual((retained['calls'], retained['item_count']), (301, 601))
        summary = self.events('performance.summary')[0]
        self.assertEqual((summary['started_calls'], summary['finished_calls'], summary['omitted_calls']),
                         (metrics.MAX_GROUPS + 302, metrics.MAX_GROUPS + 302, 2))
        self.assertTrue(summary['partial'])
        self.assertLessEqual(len(self.records()), log.MAX_EVENTS)

    def test_sql_captures_start_context_and_only_mixes_different_measurements(self):
        self.configure()
        first, second = source_function(10), source_function(20)
        native_calls = []
        with self.scope() as session, closing(connect_traced(':memory:')) as connection, \
                mock.patch.object(sql, '_source', return_value='metrics_fixture.query:1:1:execute'):
            connection.set_trace_callback(lambda statement: native_calls.append(1))
            with log.aggregate(first) as work, log.span(first) as span:
                measurement_id = work.group['measurement_id']
                span_id = span.details['span_id']
                cursor = connection.execute('SELECT 7')
            with log.aggregate(first):
                connection.execute('SELECT 7').fetchall()
            only_group = next(iter(session.collector.sql_metrics.groups.values()))
            self.assertEqual((only_group['measurement_id'], only_group['measurement_binding']),
                             (measurement_id, 'exact'))
            with log.aggregate(second) as work:
                second_id = work.group['measurement_id']
                self.assertEqual(cursor.fetchall(), [(7,)])
                connection.execute('SELECT 7').fetchall()
            self.assertEqual(session.sql_context(), (None, None))
            connection.execute('SELECT 7').fetchall()
        executions = self.events('db.sql.execution')
        first_execution = next(row for row in executions if row['execution_id'] == 1)
        self.assertEqual((first_execution['span_id'], first_execution['measurement_id'],
                          first_execution['measurement_binding']), (span_id, measurement_id, 'exact'))
        third_execution = next(row for row in executions if row['execution_id'] == 3)
        self.assertEqual(third_execution['measurement_id'], second_id)
        group = self.events('db.sql.aggregate')[0]
        self.assertEqual((group['executions'], group['fetch_calls'], group['measurement_id'],
                          group['measurement_binding']), (4, 4, None, 'mixed'))
        self.assertEqual(len(native_calls), 4)

    def test_off_and_nested_read_only_handles_do_not_project_or_read_clock(self):
        self.marker.unlink()
        def body():
            with log.span(source_function(), task_id=SECRET) as span, log.aggregate(source_function()) as work:
                span.count(0)
                work.count(300)
                return 42
        with mock.patch.object(log.time, 'perf_counter_ns', side_effect=AssertionError('OFF clock')) as clock, \
                mock.patch.object(values, 'arguments', side_effect=AssertionError('OFF projection')) as project:
            with self.scope():
                self.assertEqual(body(), 42)
            clock.assert_not_called()
            project.assert_not_called()
        self.assertFalse((self.root / '.taskgov/diagnostics/logger').exists())
        self.configure()
        with self.scope() as session:
            with mock.patch.object(log.time, 'perf_counter_ns', side_effect=AssertionError('read-only clock')) as clock:
                with self.scope(read_only=True):
                    self.assertEqual(body(), 42)
                clock.assert_not_called()
            self.assertEqual(session.performance.started, 0)
        self.assertFalse(self.events('method.start'))
        self.assertFalse(self.events('performance.aggregate'))

    def test_unknown_clock_is_not_a_zero_measurement_and_original_errors_escape(self):
        self.configure()
        original = ValueError(SECRET)
        cancellation = KeyboardInterrupt(SECRET)
        with self.scope():
            with mock.patch.object(log.time, 'perf_counter_ns', side_effect=OSError(SECRET)):
                with self.assertRaises(ValueError) as caught:
                    with log.aggregate(source_function()) as work:
                        work.count(0)
                        raise original
                self.assertIs(caught.exception, original)
            with self.assertRaises(KeyboardInterrupt) as caught:
                with log.aggregate(source_function(20)):
                    raise cancellation
            self.assertIs(caught.exception, cancellation)
        groups = self.events('performance.aggregate')
        unknown = next(row for row in groups if row['method']['line'] == 10)
        self.assertEqual((unknown['calls'], unknown['measured_calls'], unknown['failed_calls']), (1, 0, 1))
        self.assertIsNone(unknown['total_ns'])
        self.assertIsNone(unknown['max_ns'])
        self.assertEqual(next(row for row in groups if row['method']['line'] == 20)['cancelled_calls'], 1)
        self.assertTrue(self.events('performance.summary')[0]['partial'])
        self.assertNotIn(SECRET, repr(self.records()))

    def test_errors_mode_keeps_only_failed_aggregate_and_marks_saturation(self):
        self.configure('errors')
        function = source_function()
        with self.scope():
            with log.aggregate(function) as work:
                work.count(9)
            with self.assertRaises(ValueError):
                with log.aggregate(function) as work:
                    work.count(metrics.MAX_COUNT + 1)
                    raise ValueError(SECRET)
            with log.aggregate(source_function(20)) as work:
                work.count(3)
        groups = self.events('performance.aggregate')
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual((group['calls'], group['failed_calls'], group['item_count'],
                          group['counted_calls'], group['unknown_count_calls']),
                         (1, 1, metrics.MAX_COUNT, 0, 1))
        summary = self.events('performance.summary')[0]
        self.assertEqual((summary['started_calls'], summary['finished_calls']), (3, 3))
        self.assertTrue(summary['counter_saturated'])
        self.assertTrue(summary['partial'])
        self.assertNotIn(SECRET, repr(self.records()))

    def test_observation_failures_preserve_single_input_pass_and_database_outcome(self):
        self.configure()
        visits, native_calls = [], []
        original = ValueError(SECRET)
        def inputs():
            for number in range(300):
                visits.append(number)
                yield (number,)
        with self.scope() as session, closing(connect_traced(':memory:')) as connection:
            connection.execute('CREATE TABLE items(value INTEGER)')
            connection.set_trace_callback(lambda statement: native_calls.append(1))
            with mock.patch.object(session.performance, 'end', side_effect=OSError(SECRET)), \
                    mock.patch.object(session.collector.sql_metrics, 'context', side_effect=OSError(SECRET)):
                with connection, log.aggregate(source_function()) as work:
                    connection.executemany('INSERT INTO items VALUES (?)', inputs())
                    work.count(len(visits))
                with self.assertRaises(ValueError) as caught:
                    with connection, log.aggregate(source_function(20)):
                        connection.execute('INSERT INTO items VALUES (999)')
                        raise original
                self.assertIs(caught.exception, original)
                self.assertEqual(connection.execute('SELECT COUNT(*), SUM(value) FROM items').fetchone(),
                                 (300, sum(range(300))))
            self.assertEqual(visits, list(range(300)))
            # One BEGIN/COMMIT around the input, then BEGIN/INSERT/ROLLBACK and one SELECT.
            self.assertEqual(len(native_calls), 306)
        self.assertTrue(self.events('performance.summary')[0]['partial'])
        executions = self.events('db.sql.execution')
        self.assertTrue(any(row['measurement_binding'] == 'unknown' for row in executions))
        self.assertNotIn(SECRET, repr(self.records()))


if __name__ == '__main__':
    unittest.main()
