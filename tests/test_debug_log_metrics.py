"""Bounded general measurements and their real SQLite/native boundaries."""
from contextlib import ExitStack, closing
from contextvars import copy_context
from datetime import date, timedelta
from pathlib import Path
from types import FunctionType
import unittest
import threading
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

    def thread_call(self, function, *, copied=True):
        context = copy_context() if copied else None
        result, errors = [], []
        def worker():
            try:
                result.append(context.run(function) if context is not None else function())
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        return result[0]

    def test_copied_context_general_entries_invalidate_owner_publication(self):
        function = source_function()
        def measured(factory):
            with factory(function) as work:
                work.count(7)
            return 23
        for mode in ('debug', 'errors'):
            self.configure(mode)
            for operation in ('span', 'aggregate', 'exception', 'emit', 'reuse', 'admission', 'checkpoint'):
                with self.subTest(mode=mode, operation=operation):
                    with self.scope() as session:
                        # Include an owner failure so errors mode also has a
                        # saved candidate, independent of the foreign action.
                        log.record_exception(ValueError(SECRET))
                        def body():
                            if operation in ('span', 'aggregate'):
                                return measured(getattr(log, operation))
                            if operation == 'exception':
                                log.record_exception(ValueError(SECRET))
                            elif operation == 'emit':
                                session.emit('method.end', {'outcome': 'failed'}, failed=True)
                            elif operation == 'reuse':
                                with self.scope() as reused:
                                    self.assertIsNone(reused)
                            elif operation == 'checkpoint':
                                session.checkpoint(urgent=True)
                            else:
                                session.validated = session.collector.validated = False
                                log.allow_output()
                                self.assertFalse(session.validated)
                                self.assertFalse(session.collector.validated)
                            return 23
                        with mock.patch.object(values, 'arguments', side_effect=AssertionError('foreign projection')):
                            self.assertEqual(self.thread_call(body), 23)
                        self.assertTrue(session.collector._untracked)
                    self.assertEqual(self.records(), [])

    def test_borrowed_handle_mutations_and_exit_invalidate_without_context_copy(self):
        self.configure()
        for factory in (log.span, log.aggregate):
            for action in ('count', 'exit', *(['mark'] if factory is log.aggregate else [])):
                with self.subTest(factory=factory.__name__, action=action):
                    with self.scope() as session:
                        with factory(source_function()) as work:
                            work.count(2)
                            def body():
                                if action == 'exit':
                                    self.assertFalse(work.__exit__(None, None, None))
                                elif action == 'mark':
                                    work.mark('published')
                                else:
                                    work.count(19)
                                return 41
                            self.assertEqual(self.thread_call(body, copied=False), 41)
                            self.assertEqual(work.counts.value, 2)
                            self.assertFalse(getattr(work, 'states', None))
                        self.assertIsNone(log._span.get())
                        self.assertIsNone(log._measurement.get())
                        self.assertTrue(session.collector._untracked)
                    self.assertEqual(self.records(), [])

    def test_borrowed_handle_in_read_only_scope_discards_unsaved_parent(self):
        self.configure()
        for factory in (log.span, log.aggregate):
            with self.scope() as session:
                with factory(source_function()) as work:
                    work.count(2)
                    with self.scope(read_only=True):
                        work.count(19)
                    self.assertEqual(work.counts.value, 2)
                self.assertTrue(session.collector._untracked)
            self.assertEqual(self.records(), [])

    def test_nested_and_independent_thread_scopes_keep_legitimate_final_output(self):
        self.configure()
        def body():
            with self.scope(independent=True) as session:
                with log.aggregate(source_function()) as work:
                    work.count(3)
                return session.invocation_id
        with self.scope() as session:
            with self.scope() as nested:
                self.assertIs(nested, session)
                with log.span(source_function()) as work:
                    work.count(2)
            child = self.thread_call(body)
            self.assertFalse(session.collector._untracked)
            self.assertIs(log._current.get(), session)
        records = self.records()
        self.assertEqual({row['invocation_id'] for row in records}, {child, session.invocation_id})
        self.assertEqual(len(self.events('diagnostic.summary')), 2)
        self.assertEqual(self.events('method.end')[0]['item_count'], 2)
        self.assertEqual(self.events('performance.aggregate')[0]['item_count'], 3)

    def test_ended_handles_and_restored_old_context_do_not_restart_collection(self):
        self.configure()
        for factory in (log.span, log.aggregate):
            with self.scope() as session:
                with factory(source_function()) as work:
                    work.count(2)
                work.count(19)
                if factory is log.aggregate:
                    work.mark('published')
                work.__exit__(None, None, None)
                self.assertEqual(work.counts.value, 2)
                self.assertFalse(getattr(work, 'states', None))
                context = copy_context()
            before = self.records()
            def late():
                with factory(source_function()) as late_work:
                    late_work.count(100)
                log.record_exception(ValueError(SECRET))
                log.allow_output()
                with self.scope() as reused:
                    self.assertIsNone(reused)
                session.emit('method.end', {'outcome': 'failed'}, failed=True)
                session.checkpoint(final=True)
                session.finish()
            context.run(late)
            self.assertEqual(self.records(), before)
            self.assertEqual(session.events, [])
            self.assertEqual(session.performance.groups, {})

    def test_checkpoint_survives_later_sharing_violation_but_final_batch_does_not(self):
        from task_governance_tool.review_wait_runtime import worker_diagnostics as wait
        self.configure()
        with self.scope() as session:
            wait.bind(task_id='tg_task_0123456789abcdef')
            with log.span(source_function()):
                pass
            session.checkpoint(urgent=True)
            before = self.records()
            self.assertTrue(before)
            log.record_exception(ValueError(SECRET))
            self.thread_call(lambda: log.record_exception(ValueError(SECRET)))
        self.assertEqual(self.records(), before)
        self.assertEqual(session.events, [])
        self.assertEqual(session.important, [])

    def test_shared_logger_does_not_change_native_calls_rollback_close_or_exception(self):
        import sqlite3
        baselines = []
        for mode in ('off', 'debug', 'errors', 'read_only'):
            with self.subTest(mode=mode):
                if mode != 'off':
                    self.configure('errors' if mode == 'errors' else 'debug')
                calls = []
                original = ValueError(SECRET)
                native_connect = sqlite3.connect
                with mock.patch.object(sqlite3, 'connect', wraps=native_connect) as opened:
                    with self.scope(read_only=mode == 'read_only'):
                        with closing(connect_traced(':memory:')) as connection:
                            connection.set_trace_callback(lambda statement: calls.append(statement))
                            connection.execute('CREATE TABLE items(value)')
                            with self.assertRaises(ValueError) as caught:
                                with connection, log.aggregate(source_function()) as work:
                                    connection.execute('INSERT INTO items VALUES (7)')
                                    self.thread_call(lambda: work.count(1))
                                    raise original
                            self.assertIs(caught.exception, original)
                            self.assertEqual(connection.execute('SELECT * FROM items').fetchall(), [])
                    self.assertEqual(opened.call_count, 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')
                baselines.append(calls)
                self.assertEqual(self.records(), [])
        self.assertTrue(all(calls == baselines[0] for calls in baselines))

    def test_ended_handle_reentry_keeps_original_binding_and_balanced_events(self):
        self.configure()
        for factory in (log.span, log.aggregate):
            with self.subTest(factory=factory.__name__):
                with self.scope() as session:
                    handle = factory(source_function())
                    with handle:
                        handle.count(2)
                    before = (len(session.events), session.performance.started, session.performance.finished)
                    with handle:
                        handle.count(19)
                    self.assertIs(handle.session, session)
                    self.assertEqual(before, (len(session.events), session.performance.started,
                                              session.performance.finished))
                    self.assertEqual(handle.counts.value, 2)
                    self.assertFalse(session.collector._untracked)
                with self.scope() as later:
                    with handle:
                        handle.count(23)
                    self.assertIs(handle.session, session)
                    self.assertEqual(later.performance.started, 0)
                    self.assertEqual(later.events, [])
        self.assertEqual(len(self.events('method.start')), 1)
        self.assertEqual(len(self.events('method.end')), 1)
        summary = self.events('performance.summary')[0]
        self.assertEqual((summary['started_calls'], summary['finished_calls']), (1, 1))

    def test_active_handle_reentry_keeps_owner_with_and_without_copied_context(self):
        self.configure()
        for factory in (log.span, log.aggregate):
            for copied in (False, True):
                with self.subTest(factory=factory.__name__, copied=copied):
                    with self.scope() as session:
                        with factory(source_function()) as handle:
                            handle.count(2)
                            token = handle.token
                            def borrowed():
                                with handle:
                                    handle.count(19)
                                return 41
                            self.assertEqual(self.thread_call(borrowed, copied=copied), 41)
                            self.assertIs(handle.session, session)
                            self.assertIs(handle.token, token)
                            self.assertEqual(handle.counts.value, 2)
                            self.assertTrue(session.collector._untracked)
                        self.assertIsNone(log._span.get())
                        self.assertIsNone(log._measurement.get())
                    self.assertEqual(self.records(), [])

    def test_active_same_thread_handle_reentry_discards_ambiguous_observations(self):
        self.configure()
        for factory in (log.span, log.aggregate):
            with self.scope() as session:
                with factory(source_function()) as handle:
                    with handle:
                        handle.count(1)
                self.assertTrue(session.collector._untracked)
                self.assertIsNone(log._span.get())
                self.assertIsNone(log._measurement.get())
            self.assertEqual(self.records(), [])

    def test_off_handle_cannot_be_reentered_to_start_an_enabled_observation(self):
        handles = [log.span(source_function()), log.aggregate(source_function())]
        for handle in handles:
            with handle:
                handle.count(1)
        self.configure()
        with self.scope() as session:
            for handle in handles:
                with handle:
                    handle.count(19)
                self.assertIsNone(handle.session)
            self.assertEqual(session.events, [])
            self.assertEqual(session.performance.started, 0)

    def test_scope_reentry_and_exit_on_foreign_thread_preserve_owner_cleanup(self):
        self.configure()
        for action in ('enter', 'exit'):
            for nested in (False, True):
                with self.subTest(action=action, nested=nested):
                    with self.scope() as session:
                        scope = self.scope() if nested else None
                        if scope is None:
                            # Use the actual outer object in the non-borrowed arm.
                            scope = self.scope(independent=True)
                        with scope as bound:
                            log.record_exception(ValueError(SECRET))
                            def borrowed():
                                if action == 'enter':
                                    with scope as rejected:
                                        self.assertIsNone(rejected)
                                else:
                                    self.assertFalse(scope.__exit__(None, None, None))
                            self.thread_call(borrowed, copied=False)
                            self.assertIs(log._current.get(), bound)
                            self.assertFalse(scope.closed)
                            self.assertTrue(bound.collector._untracked)
                        self.assertIs(log._current.get(), session)
                    self.assertIsNone(log._current.get())
        self.assertFalse(self.events('exception'))

    def test_scope_object_nesting_preserves_readonly_and_ended_scope_stays_ended(self):
        self.configure()
        for read_only in (False, True):
            scope = self.scope(read_only=read_only)
            with scope as session:
                with scope as nested:
                    self.assertIs(nested, session)
                self.assertIs(log._current.get(), session)
                with log.span(source_function()):
                    pass
            before = self.records()
            with scope as ended:
                self.assertIsNone(ended)
                self.assertIsNone(log._current.get())
            self.assertEqual(self.records(), before)
            with self.scope() as parent:
                with scope as ended:
                    self.assertIsNone(ended)
                    with log.span(source_function()):
                        pass
                self.assertIs(log._current.get(), parent)
                self.assertEqual(parent.events, [])
                self.assertFalse(parent.collector._untracked)
        self.assertEqual(len(self.events('method.end')), 1)

    def test_rejected_foreign_scope_suppresses_worker_and_preserves_both_cleanups(self):
        self.configure()
        for read_only in (False, True):
            for ended in (False, True):
                with self.subTest(read_only=read_only, ended=ended):
                    scope = self.scope(read_only=read_only)
                    owner = scope.__enter__()
                    if ended:
                        scope.__exit__(None, None, None)
                    def worker():
                        with self.scope(independent=True) as session:
                            self.assertIsNotNone(session)
                            with log.span(source_function()):
                                pass
                            with scope as rejected:
                                self.assertIsNone(rejected)
                                self.assertIsNone(log._current.get())
                                self.assertTrue(log.sqlite.suppressed())
                                with scope:
                                    with log.span(source_function()):
                                        pass
                                # Neither a copied context on this thread nor
                                # another thread may consume this entry's stack.
                                copy_context().run(scope.__exit__, None, None, None)
                                self.thread_call(lambda: scope.__exit__(None, None, None))
                                self.assertIsNone(log._current.get())
                                self.assertTrue(log.sqlite.suppressed())
                                error = ValueError(SECRET)
                                with self.assertRaises(ValueError) as caught:
                                    with scope, log.aggregate(source_function()):
                                        raise error
                                self.assertIs(caught.exception, error)
                            self.assertIs(log._current.get(), session)
                            self.assertFalse(log.sqlite.suppressed())
                            self.assertFalse(session.collector._untracked)
                            with log.span(source_function()):
                                pass
                            return session.invocation_id
                    invocation = self.thread_call(worker, copied=False)
                    if not ended:
                        self.assertEqual(scope.depth, 1)
                        self.assertIs(log._current.get(), owner)
                        self.assertEqual(log.sqlite.suppressed(), read_only)
                        scope.__exit__(None, None, None)
                    self.assertIsNone(log._current.get())
                    self.assertFalse(log.sqlite.suppressed())
                    rows = [row for row in self.records() if row['invocation_id'] == invocation]
                    self.assertEqual(sum(row['event'] == 'method.end' for row in rows), 2)
                    self.assertFalse(any(row['event'] in ('exception', 'performance.aggregate') for row in rows))

    def test_invalidation_during_aggregate_finalization_drops_existing_buffer(self):
        for mode in ('debug', 'errors'):
            self.configure(mode)
            for component in ('sql_metrics', 'performance'):
                with self.subTest(mode=mode, component=component), ExitStack() as patches:
                    with self.scope() as session:
                        log.record_exception(ValueError(SECRET))
                        context = copy_context()
                        aggregate = (session.collector.sql_metrics if component == 'sql_metrics'
                                     else session.performance)
                        finish = aggregate.finish
                        def interleave(*, publish):
                            self.thread_call(lambda: context.run(
                                log.record_exception, ValueError(SECRET)), copied=False)
                            finish(publish=publish)
                        patches.enter_context(mock.patch.object(aggregate, 'finish', side_effect=interleave))
                        self.assertTrue(session.events)
                    self.assertTrue(session.closed)
                    self.assertTrue(session.collector._untracked)
                    self.assertEqual(self.records(), [])

    def test_invalidation_between_daily_batches_keeps_only_dispatched_batch(self):
        from task_governance_tool.review_wait_runtime import worker_diagnostics as wait
        self.configure()
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint), ExitStack() as patches:
                with self.scope() as session:
                    if checkpoint:
                        wait.bind(task_id='tg_task_0123456789abcdef')
                    for day in (date.today() - timedelta(days=1), date.today()):
                        moment = {**log._moment(), 'log_date': day.isoformat()}
                        with mock.patch.object(log, '_moment', return_value=moment):
                            log.record_exception(ValueError(SECRET))
                    context = copy_context()
                    write = session.sink.write
                    writes = []
                    def interleave(day, data):
                        writes.append(day)
                        result = write(day, data)
                        self.assertEqual(result, 'written')
                        self.thread_call(lambda: context.run(
                            log.record_exception, ValueError(SECRET)), copied=False)
                        return result
                    patches.enter_context(mock.patch.object(session.sink, 'write', side_effect=interleave))
                    if checkpoint:
                        session.checkpoint(urgent=True)
                        self.assertEqual(session.previous_checkpoint, 'unknown')
                self.assertEqual(len(writes), 1)
                rows = [row for row in self.records() if row['invocation_id'] == session.invocation_id]
                self.assertTrue(rows)
                self.assertEqual({row['log_date'] for row in rows}, {writes[0]})
                self.assertFalse(any(row['event'] == 'diagnostic.summary' for row in rows))
                self.assertEqual(session.events, [])
                self.assertEqual(session.important, [])

    def test_invalidation_after_summary_construction_prevents_its_dispatch(self):
        from task_governance_tool.review_wait_runtime import worker_diagnostics as wait
        self.configure()
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint), ExitStack() as patches:
                with self.scope() as session:
                    if checkpoint:
                        wait.bind(task_id='tg_task_0123456789abcdef')
                    log.record_exception(ValueError(SECRET))
                    context = copy_context()
                    emit = session.emit
                    summary = 'wait.checkpoint' if checkpoint else 'diagnostic.summary'
                    def interleave(kind, details, **options):
                        emit(kind, details, **options)
                        if kind == summary:
                            self.thread_call(lambda: context.run(
                                log.record_exception, ValueError(SECRET)), copied=False)
                    patches.enter_context(mock.patch.object(session, 'emit', side_effect=interleave))
                    if checkpoint:
                        session.checkpoint(urgent=True)
                rows = [row for row in self.records() if row['invocation_id'] == session.invocation_id]
                self.assertFalse(any(row['event'] == summary for row in rows))
                self.assertEqual(bool(rows), not checkpoint)

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
