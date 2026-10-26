"""Bounded API-level SQL observations; never retain SQL, parameters or rows.

Transaction ownership stays in sqlite_transaction_trace. Native shortcuts use
the same default-cursor construction and one native execution, so their returned
cursors can expose fetch observations as well. User factories remain native.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
import sqlite3
import sys
import threading
from time import perf_counter_ns

from .debug_log_values import code_location

MAX_GROUPS = 32
MAX_ACTIVE = 128
MAX_DETAILS = 64
RESERVED_EVENTS = MAX_GROUPS + MAX_DETAILS + 1
MAX_COUNT = 2**63 - 1
_active = ContextVar('sql_execution', default=None)
_INFRASTRUCTURE = {'sqlite_sql_trace', 'sqlite_transaction_trace'}


def _source(api):
    frame = None
    try:
        frame = sys._getframe(1)
        for _ in range(32):
            if frame is None:
                break
            location = code_location(frame.f_code)
            if location and location['module'] not in _INFRASTRUCTURE:
                label = f"{location['module']}.{location['function']}:{frame.f_lineno}:{frame.f_lasti}:{api}"
                if len(label) <= 384:
                    return label
            frame = frame.f_back
    except BaseException:
        pass
    finally:
        del frame
    return 'other:' + api


def _moment():
    now = datetime.now().astimezone()
    return {'observed_at': now.astimezone(timezone.utc).isoformat(),
            'log_date': now.date().isoformat(), 'utc_offset': now.strftime('%z')}


def _outcome(error):
    return 'succeeded' if error is None else 'failed' if issubclass(type(error), Exception) else 'cancelled'


def safe(function, *args):
    """Post-call diagnostics must never mask a native return or exception."""
    try:
        if function is not None:
            return function(*args)
    except BaseException:
        pass
    return None


def observer_collector(connection):
    """Reuse existing scope/thread suppression for already-open connections."""
    collector = None
    try:
        from .sqlite_diagnostics import suppressed
        collector = getattr(connection, '_diagnostic_collector', None)
        if collector is None or collector.sealed:
            return None
        if suppressed() or threading.get_ident() != collector.thread_id:
            # A skipped observation must not later masquerade as a complete
            # operation when its original owner closes the borrowed cursor.
            collector.mark_untracked()
            return None
        return None if collector._untracked else collector
    except BaseException:
        if collector is not None:
            safe(collector.mark_untracked)
        return None


def _remember(state, outcome, consumption=None, *, rows_known=None):
    """Set native facts before fallible counters, projection or metadata work."""
    if state is None:
        return
    if (not state['outcome_observed'] or outcome in {'failed', 'cancelled'}
            or outcome == 'unknown' and state['outcome'] == 'succeeded'):
        state['outcome'] = outcome
    state['outcome_observed'] = True
    if consumption is not None:
        state['consumption'] = consumption
    if rows_known is False:
        state['rows_known'] = False


def _plain_conversion(cursor):
    """Only native built-in row/text conversion without registered converters.

SQLite does not expose the cause of StopIteration. Unknown configurations are
conservative; no converter, row, exception traceback or callback is inspected.
"""
    connection = cursor.connection
    row_factory = sqlite3.Cursor.row_factory.__get__(cursor)
    text_factory = sqlite3.Connection.text_factory.__get__(connection)
    return (not getattr(connection, '_diagnostic_converters_possible', True)
            and (row_factory is None or row_factory is sqlite3.Row)
            and (text_factory is str or text_factory is bytes))


class Metrics:
    """One admitted operation, independent budgets for grouping and details."""
    def __init__(self, mode, emit, collector, *, context=None):
        self.mode, self.emit, self.collector = mode, emit, collector
        self.groups, self.active, self.details = {}, {}, []
        self.sequence = self.calls = self.omitted_groups = self.untracked = 0
        self.omitted_details = self.unknown_fetches = self.custom_cursors = 0
        self.instrumentation_ns = 0
        self.observation_failures = 0
        self.unknown_sources = 0
        self.saturated = self.closed = False
        self.context = context

    def usable(self):
        if threading.get_ident() != self.collector.thread_id:
            self.collector.mark_untracked()
            return False
        return not self.closed and not self.collector.sealed and not self.collector._untracked

    def add(self, mapping, key, value):
        total = mapping.get(key, 0) + value
        self.saturated |= total > MAX_COUNT
        mapping[key] = min(MAX_COUNT, total)

    def bump(self, name, value=1):
        total = getattr(self, name) + value
        self.saturated |= total > MAX_COUNT
        setattr(self, name, min(MAX_COUNT, total))

    def begin(self, connection, api, *, binding='exact'):
        if not self.usable():
            return None
        if self.sequence == MAX_COUNT:
            self.saturated = True
            return None
        self.bump('sequence')
        identity = _source(api)
        if identity.startswith('other:'):
            self.bump('unknown_sources')
        parent = _active.get()
        connection_id = connection._diagnostic_id
        role, access, _ = self.collector._open.get(connection_id, ('other', 'readwrite', 'other'))
        state = {'execution_id': self.sequence, 'sql_id': identity, 'api': api,
                 'connection_id': connection_id, 'role': role, 'access': access,
                 'transaction_id': connection._diagnostic_transaction,
                 'parent_execution_id': parent[1] if parent and parent[0] is self else None,
                 'binding': binding, 'started_ns': perf_counter_ns(), 'execute_ns': 0,
                 'execute_measured': False, 'fetch_transaction_id': None,
                 'fetch_transaction_binding': 'none',
                 'fetch_ns': 0, 'fetch_max_ns': 0, 'fetch_calls': 0, 'rows_lower_bound': 0,
                 'rows_known': True, 'consumption': 'pending', 'outcome': 'unknown',
                 'outcome_observed': False,
                 'ended': False, 'tracked': len(self.active) < MAX_ACTIVE}
        state.update(span_id=None, measurement_id=None, measurement_binding='none')
        if self.context is not None:
            try:
                state['span_id'], state['measurement_id'] = self.context()
                state['measurement_binding'] = 'exact' if state['measurement_id'] is not None else 'none'
            except BaseException:
                state['measurement_binding'] = 'unknown'
                self.bump('observation_failures')
        if state['tracked']:
            self.active[state['execution_id']] = state
        else:
            self.bump('untracked')
        return state

    def group(self, state):
        key = state['sql_id']
        if key not in self.groups:
            if len(self.groups) >= MAX_GROUPS:
                self.bump('omitted_groups')
                return None
            self.groups[key] = {'sql_id': key, 'api': state['api'], 'executions': 0,
                                'measurement_id': state['measurement_id'],
                                'measurement_binding': state['measurement_binding'],
                                'execute_ns': 0, 'execute_max_ns': 0, 'fetch_calls': 0,
                                'fetch_ns': 0, 'fetch_max_ns': 0, 'rows_lower_bound': 0,
                                'unknown_rows_calls': 0, 'failures': 0, 'cancelled': 0, 'unknown_outcomes': 0,
                                'complete_results': 0, 'partial_results': 0, 'unknown_results': 0}
        group = self.groups[key]
        if (group['measurement_binding'] != state['measurement_binding'] or
                group['measurement_id'] != state['measurement_id']):
            group['measurement_id'], group['measurement_binding'] = None, 'mixed'
        return group

    def observed(self, state, method, elapsed, error, *, rows=None, consumption=None, outcome=None):
        if state is None or not self.usable():
            return
        self.bump('calls')
        outcome = outcome or _outcome(error)
        fetching = method in {'fetchone', 'fetchmany', 'fetchall', 'next'}
        _remember(state, outcome, consumption)
        if fetching:
            self.add(state, 'fetch_calls', 1)
            self.add(state, 'fetch_ns', elapsed)
            state['fetch_max_ns'] = max(state['fetch_max_ns'], elapsed)
            if rows is None:
                state['rows_known'] = False
            else:
                self.add(state, 'rows_lower_bound', rows)
        else:
            self.add(state, 'execute_ns', elapsed)
            state['execute_measured'] = True
        if consumption is not None:
            state['consumption'] = consumption
        if self.mode == 'debug':
            group = self.group(state)
            if group is not None:
                prefix = 'fetch' if fetching else 'execute'
                self.add(group, 'fetch_calls' if fetching else 'executions', 1)
                self.add(group, prefix + '_ns', elapsed)
                group[prefix + '_max_ns'] = max(group[prefix + '_max_ns'], elapsed)
                if fetching:
                    self.add(group, 'unknown_rows_calls' if rows is None else 'rows_lower_bound', 1 if rows is None else rows)
                if outcome != 'succeeded':
                    key = {'cancelled': 'cancelled', 'unknown': 'unknown_outcomes'}.get(outcome, 'failures')
                    self.add(group, key, 1)
        elif outcome != 'succeeded':
            self.detail({'execution_id': state['execution_id'], 'sql_id': state['sql_id'],
                         'connection_id': state['connection_id'], 'binding': state['binding'],
                         'transaction_id': state['transaction_id'],
                         'span_id': state['span_id'], 'measurement_id': state['measurement_id'],
                         'measurement_binding': state['measurement_binding'],
                         'parent_execution_id': state['parent_execution_id'],
                         'method': method, 'outcome': outcome, 'elapsed_ns': elapsed}, failed=True)

    def detail(self, record, *, failed=False):
        if len(self.details) < MAX_DETAILS:
            self.details.append((record, failed, _moment()))
        else:
            self.bump('omitted_details')
            if failed:
                index = next((i for i, item in enumerate(self.details) if not item[1]), 0)
                self.details.pop(index)
                self.details.append((record, failed, _moment()))

    def end(self, state, reason):
        if state is None or state['ended'] or not self.usable():
            return
        state['ended'] = True
        self.active.pop(state['execution_id'], None)
        if not state['outcome_observed']:
            state['consumption'], state['rows_known'] = 'unknown', False
        elif state['consumption'] == 'pending':
            state['consumption'] = 'partial'
        if self.mode != 'debug':
            return
        group = self.groups.get(state['sql_id'])
        if group is not None:
            key = ('complete_results' if state['consumption'] in {'complete', 'no_rows', 'discarded'}
                   else 'unknown_results' if state['consumption'] == 'unknown' else 'partial_results')
            self.add(group, key, 1)
        record = {key: value for key, value in state.items()
                  if key not in {'started_ns', 'ended', 'tracked', 'outcome_observed'}}
        if not state['execute_measured']:
            record['execute_ns'] = None
        record.update(end_reason=reason, wall_lifetime_ns=max(0, perf_counter_ns() - state['started_ns']),
                      internal_statements='unknown', timing_scope='inclusive_native_api')
        self.detail(record, failed=state['outcome'] != 'succeeded')

    def close_connection(self, connection_id):
        for state in list(self.active.values()):
            if state['connection_id'] == connection_id:
                self.end(state, 'connection_closed')

    def finish(self, *, publish):
        if publish:
            for state in list(self.active.values()):
                self.end(state, 'scope_ended')
            for record, failed, moment in self.details:
                self.emit('db.sql.execution' if self.mode == 'debug' else 'db.sql.failure',
                          record, failed=failed, moment=moment, reserved=True)
            if self.mode == 'debug':
                for record in self.groups.values():
                    self.emit('db.sql.aggregate', {**record, 'timing_scope': 'inclusive_native_api'}, reserved=True)
                self.emit('db.sql.summary', {'api_calls': self.calls, 'executions_started': self.sequence,
                    'aggregate_omitted_calls': self.omitted_groups, 'details_omitted': self.omitted_details,
                    'untracked_executions': self.untracked, 'unbound_fetch_calls': self.unknown_fetches,
                    'custom_cursors': self.custom_cursors, 'instrumentation_ns': self.instrumentation_ns,
                    'observation_failures': self.observation_failures,
                    'unknown_source_executions': self.unknown_sources,
                    'counter_saturated': self.saturated,
                    'partial': bool(self.omitted_groups or self.omitted_details or self.untracked
                        or self.unknown_fetches or self.custom_cursors or self.saturated
                        or self.observation_failures or self.unknown_sources
                        or any(g['unknown_outcomes'] or g['unknown_rows_calls'] for g in self.groups.values()))}, reserved=True)
            elif self.omitted_details or self.observation_failures or self.untracked or self.custom_cursors:
                self.emit('db.sql.summary', {'details_omitted': self.omitted_details,
                          'observation_failures': self.observation_failures,
                          'untracked_executions': self.untracked, 'custom_cursors': self.custom_cursors,
                          'partial': True},
                          failed=True, reserved=True)
        self.closed = True
        self.active.clear()
        self.details.clear()
        self.groups.clear()


def _metrics(connection):
    try:
        value = getattr(observer_collector(connection), 'sql_metrics', None)
        return value if value is not None and value.usable() else None
    except BaseException:
        return None


def _record(metrics, function, *args, state=None):
    try:
        return function(*args)
    except BaseException:
        safe(lambda: _remember(state, 'unknown', 'unknown', rows_known=False))
        safe(metrics.bump, 'observation_failures')
        return None


def _leave(metrics, context, overhead, native_ns):
    if context is not None:
        safe(_active.reset, context)
    if overhead is not None and native_ns is not None:
        safe(lambda: metrics.bump('instrumentation_ns', max(0, perf_counter_ns() - overhead - native_ns)))


def custom_cursor(connection):
    metrics = _metrics(connection)
    if metrics is not None:
        safe(metrics.bump, 'custom_cursors')


def connection_closed(connection):
    metrics = _metrics(connection)
    if metrics is not None:
        _record(metrics, metrics.close_connection, connection._diagnostic_id)


class _ObservedCursor(sqlite3.Cursor):
    def _execute(self, api, args, kwargs):
        native = getattr(super(), api)
        connection = self.connection
        metrics = _metrics(connection)
        phase = 'script' if api == 'executescript' else None
        options = {} if phase else {'sql': (args, kwargs)}
        if metrics is None:
            return connection._observe(lambda: native(*args, **kwargs), phase, **options)
        overhead, context, state, native_ns = None, None, None, None
        try:
            overhead = perf_counter_ns()
            previous = getattr(self, '_sql_state', None)
            if api != 'executescript':
                self._sql_state = None
                metrics.end(previous, 'reexecute')
            state = metrics.begin(connection, api)
            context = _active.set((metrics, state['execution_id'])) if state else None
        except BaseException:
            safe(metrics.bump, 'observation_failures')
        def observed(result, error, started, ended):
            nonlocal native_ns
            _remember(state, _outcome(error), 'unknown' if error is not None else None)
            native_ns = ended - started
            if state is None:
                return
            state['transaction_id_after'] = connection._diagnostic_transaction
            if api == 'executescript':
                consumption = 'discarded' if error is None else 'unknown'
            elif error is not None:
                state['binding'] = 'unknown_after_execute_failure'
                consumption = 'unknown'
            else:
                consumption = 'pending' if sqlite3.Cursor.description.__get__(self) is not None else 'no_rows'
            _remember(state, _outcome(error), consumption)
            metrics.observed(state, api, native_ns, error, consumption=consumption)
            if api == 'executescript' or error is not None or not state['tracked']:
                metrics.end(state, 'script_returned' if api == 'executescript' else
                            'execute_failed' if error is not None else 'tracking_limit')
            if api != 'executescript':
                self._sql_state = state if state['tracked'] and error is None else None
        def record(result, error, started, ended):
            _record(metrics, observed, result, error, started, ended, state=state)
        try:
            return connection._observe(lambda: native(*args, **kwargs), phase, observed=record, **options)
        finally:
            if state is not None and native_ns is None:
                safe(metrics.bump, 'observation_failures')
            _leave(metrics, context, overhead, native_ns)

    def execute(self, *args, **kwargs):
        return self._execute('execute', args, kwargs)

    def executemany(self, *args, **kwargs):
        return self._execute('executemany', args, kwargs)

    def executescript(self, *args, **kwargs):
        return self._execute('executescript', args, kwargs)

    def _fetch(self, method, native, args, kwargs):
        metrics = _metrics(self.connection)
        if metrics is None:
            return native(*args, **kwargs)
        overhead, context, state, started = None, None, None, None
        plain_before = False
        builtin_rows_before = False
        orphan = True
        try:
            overhead = perf_counter_ns()
            state = getattr(self, '_sql_state', None)
            orphan = state is None or state['ended']
            if orphan:
                metrics.bump('unknown_fetches')
                state = metrics.begin(self.connection, 'fetch', binding='unbound')
            if state is not None:
                transaction = self.connection._diagnostic_transaction
                if state['fetch_transaction_binding'] == 'none':
                    state['fetch_transaction_id'] = transaction
                    state['fetch_transaction_binding'] = 'exact'
                elif state['fetch_transaction_id'] != transaction:
                    state['fetch_transaction_id'] = None
                    state['fetch_transaction_binding'] = 'mixed'
            context = _active.set((metrics, state['execution_id'])) if state else None
            row_factory = sqlite3.Cursor.row_factory.__get__(self)
            builtin_rows_before = row_factory is None or row_factory is sqlite3.Row
            plain_before = _plain_conversion(self)
            started = perf_counter_ns()
        except BaseException:
            safe(metrics.bump, 'observation_failures')
        native_ns = None
        def record(result, error, ended):
            nonlocal native_ns
            outcome = _outcome(error)
            is_stop = type(error) is StopIteration and method == 'next'
            if is_stop:
                outcome = 'succeeded' if plain_before and safe(_plain_conversion, self) is True else 'unknown'
            _remember(state, outcome, 'unknown' if error is not None else None)
            if started is None or ended is None:
                _remember(state, 'unknown', 'unknown', rows_known=False)
                metrics.bump('observation_failures')
                return
            native_ns = ended - started
            rows, consumption = None, None
            factory = sqlite3.Cursor.row_factory.__get__(self)
            builtin_rows = builtin_rows_before and (factory is None or factory is sqlite3.Row)
            # A user factory can itself raise StopIteration or return None.
            # Never infer exhaustion from those ambiguous native outcomes.
            if error is not None:
                consumption = 'unknown'
                if is_stop and outcome == 'succeeded':
                    error, rows, consumption = None, 0, 'complete'
            else:
                if method in {'fetchmany', 'fetchall'} and type(result) is list:
                    rows = len(result)
                    if method == 'fetchall' or not result:
                        consumption = 'complete'
                elif method == 'next':
                    rows = 1
                elif builtin_rows:
                    rows = int(result is not None)
                    if result is None:
                        consumption = 'complete'
                else:
                    consumption = 'unknown'
            _remember(state, outcome, consumption, rows_known=rows is not None)
            metrics.observed(state, method, native_ns, error, rows=rows, consumption=consumption, outcome=outcome)
            if orphan:
                metrics.end(state, 'unbound_fetch')
        try:
            result = native(*args, **kwargs)
        except BaseException as error:
            _record(metrics, record, None, error, safe(perf_counter_ns), state=state)
            raise
        else:
            _record(metrics, record, result, None, safe(perf_counter_ns), state=state)
            return result
        finally:
            _leave(metrics, context, overhead, native_ns)

    def fetchone(self, *args, **kwargs):
        return self._fetch('fetchone', super().fetchone, args, kwargs)

    def fetchmany(self, *args, **kwargs):
        return self._fetch('fetchmany', super().fetchmany, args, kwargs)

    def fetchall(self, *args, **kwargs):
        return self._fetch('fetchall', super().fetchall, args, kwargs)

    def __next__(self):
        return self._fetch('next', super().__next__, (), {})

    def close(self):
        metrics = _metrics(self.connection)
        result = super().close()
        if metrics is not None:
            _record(metrics, metrics.end, getattr(self, '_sql_state', None), 'cursor_closed')
        return result

    def _end(self, reason):
        metrics = _metrics(self.connection)
        if metrics is not None:
            _record(metrics, metrics.end, getattr(self, '_sql_state', None), reason)

    def __del__(self):
        try:
            self._end('cursor_released')
        except BaseException:
            pass


def shortcut(connection, api, args, kwargs):
    metrics = _metrics(connection)
    if metrics is None:
        native = getattr(sqlite3.Connection, api)
        phase = 'script' if api == 'executescript' else None
        options = {} if phase else {'sql': (args, kwargs)}
        return connection._observe(lambda: native(connection, *args, **kwargs), phase, **options)
    if kwargs:
        # Supported native shortcut signatures are positional-only. Let that
        # original API reject keywords, including its original error message.
        # A future accepting driver stays native and is explicitly untracked.
        state = _record(metrics, metrics.begin, connection, api)
        def record(result, error, started, ended):
            def update():
                _remember(state, _outcome(error), 'unknown')
                metrics.observed(state, api, ended - started, error, consumption='unknown')
                metrics.end(state, 'native_keyword_call')
                if error is None:
                    metrics.bump('untracked')
            _record(metrics, update, state=state)
        return connection._observe(lambda: getattr(sqlite3.Connection, api)(connection, *args, **kwargs),
                                   'script' if api == 'executescript' else None,
                                   sql=None if api == 'executescript' else (args, kwargs), observed=record)
    try:
        cursor = sqlite3.Connection.cursor(connection, factory=_ObservedCursor)
    except BaseException:
        # No SQL API was entered; never fabricate a zero-duration execution.
        safe(metrics.bump, 'untracked')
        raise
    return cursor._execute(api, args, kwargs)
