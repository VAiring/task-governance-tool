"""Common logger privacy, native DB boundaries, timing and daily retention."""
from contextlib import closing
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

from tests.test_sqlite_diagnostics import SQLiteDiagnosticsTests, SECRET
from task_governance_tool import debug_log as log, debug_log_sink as sink, debug_log_values as values
from task_governance_tool import sqlite_connection as db


def example(task_id=None, count=None, force=None, title=None, note=None, password=None, payload=None, omitted=None):
    pass


class DebugLogTests(unittest.TestCase):
    setUp = SQLiteDiagnosticsTests.setUp
    enable = SQLiteDiagnosticsTests.enable

    def configure(self, mode="debug"):
        directory = self.root / '.taskgov/diagnostics/logger'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'enabled').write_bytes(f'taskgov-logger-v1 {mode}\n'.encode())
        return directory

    def scope(self, **options):
        return log.operation_scope(self.root, entrypoint=self.entrypoint, operation='task.show',
                                   repo_explicit=True, validated=True, **options)

    def records(self):
        directory = self.root / '.taskgov/diagnostics/logger'
        return [json.loads(line) for path in directory.glob('taskgov-debug.log.*')
                for line in path.read_bytes().splitlines() if line.startswith(b'{')]

    def test_off_does_not_expand_arguments_or_allocate_logger_artifacts(self):
        self.marker.unlink()  # both facilities OFF
        with mock.patch.object(values, 'arguments', side_effect=AssertionError('OFF expansion')):
            with self.scope(), log.span(example, payload=object()):
                pass
        self.assertFalse((self.root / '.taskgov/diagnostics/logger').exists())

    def test_values_and_short_api_mask_before_the_buffer(self):
        self.configure()
        with self.scope() as session:
            with log.span(example, task_id='tg_task_0123456789abcdef', count=0, force=False,
                          title='', note=' \t\n', password=876543, payload={'Authorization': SECRET}):
                pass
            raw = b''.join(event[1] for event in session.events)
            self.assertNotIn(SECRET.encode(), raw)
            self.assertNotIn(b'876543', raw)
        args = self.records()[0]['args']
        self.assertEqual(args['task_id']['value'], 'tg_task_0123456789abcdef')
        self.assertEqual(args['count']['value'], 0)
        self.assertIs(args['force']['value'], False)
        self.assertEqual(args['title']['state'], 'empty_string')
        self.assertEqual(args['note']['state'], 'whitespace')
        self.assertEqual(args['omitted']['state'], 'missing')
        self.assertEqual(values.value(None), {'state': 'null'})
        self.assertEqual(values.value(float('nan'))['state'], 'non_finite')

    def test_unknown_objects_and_dynamic_dictionary_keys_are_not_stringified(self):
        class Hostile:
            def __repr__(self):
                raise AssertionError('repr called')
            __str__ = __repr__
        self.configure()
        with self.scope(), log.span(example, payload={SECRET: [Hostile(), SECRET, {'token': 42}]}):
            pass
        text = json.dumps(self.records())
        self.assertNotIn(SECRET, text)
        self.assertNotIn('"value": 42', text)

    def test_exception_projection_bypasses_subclass_properties(self):
        accessed = []

        class UntrustedError(Exception):
            @property
            def __traceback__(self):
                accessed.append('__traceback__')
                return None

        directory = self.configure()
        original = UntrustedError(SECRET)
        for mode in ('common', 'sqlite', 'off', 'read_only'):
            with self.subTest(mode=mode):
                if mode == 'sqlite':
                    (directory / 'enabled').unlink()
                elif mode == 'off':
                    self.marker.unlink()
                elif mode == 'read_only':
                    self.configure()
                    self.enable()
                observed = None
                try:
                    with self.scope(read_only=mode == 'read_only'), log.span(example):
                        raise original
                except BaseException as error:
                    observed = error
                self.assertIs(observed, original)
        self.assertEqual(accessed, [])
        self.assertEqual(len([r for r in self.records() if r['event'] == 'exception']), 1)

    def test_metadata_projection_during_transaction_does_not_resolve_files(self):
        self.configure()
        with self.scope(), closing(db.connect_existing(self.database)) as connection:
            connection.execute('BEGIN')
            with mock.patch.object(Path, 'resolve', side_effect=AssertionError('filesystem lookup')) as resolve:
                with log.span(values.value, item=1):
                    try:
                        values.value([1], depth=None)
                    except TypeError as error:
                        log.record_exception(error)
                resolve.assert_not_called()
            connection.rollback()
        rows = self.records()
        self.assertTrue(any(r.get('method') == 'value' for r in rows))
        stack = next(r['stack'] for r in rows if r['event'] == 'exception')
        self.assertTrue(any(frame['module'] == 'debug_log_values' for frame in stack))

    def test_bounds_do_not_cut_json_and_preserve_late_failure(self):
        self.configure()
        with self.scope() as session:
            for _ in range(170):
                with log.span(example, payload=[[list(range(100))] * 100] * 100):
                    pass
            log.record_exception(ValueError(SECRET))
            self.assertLessEqual(len(session.events), log.MAX_EVENTS)
            self.assertLessEqual(session.size, log.MAX_BUFFER_BYTES)
            self.assertTrue(all(len(event[1]) <= log.MAX_EVENT_BYTES for event in session.events))
        self.assertTrue(any(row['event'] == 'exception' for row in self.records()))
        self.assertGreater(self.records()[-1]['dropped']['buffer_limit'], 0)
        self.assertLessEqual(len(self.records()), log.MAX_EVENTS)

    def test_numeric_authentication_fields_are_masked_before_buffer_and_file(self):
        def authenticate(pin, otp, loginPin, authCode, shipping, count, payload):
            pass
        self.configure()
        secret_names = ('pin', 'otp', 'userPIN', 'totp', 'hotp', 'CVV', 'mfaCode',
                        'oneTimeCode', 'backup_code', 'security_code', 'verification_code', 'pwd', 'passphrase',
                        'private-key', 'access key')
        payload = {name: 87654321 for name in secret_names}
        payload['shipping'] = 17
        with self.scope() as session:
            with log.span(authenticate, pin=87654321, otp=87654321, loginPin=87654321,
                          authCode=87654321, shipping=17, count=3, payload=payload):
                pass
            protected = [json.loads(data) for _, data, _ in session.events]
            self.assertNotIn('87654321', json.dumps(protected))
        rows = self.records()
        self.assertNotIn('87654321', json.dumps(rows))
        args = rows[0]['args']
        for name in ('pin', 'otp', 'loginPin', 'authCode'):
            self.assertEqual(args[name], {'state': 'present', 'redacted': True})
        self.assertEqual(args['shipping']['value'], 17)
        self.assertEqual(args['count']['value'], 3)
        items = args['payload']['items']
        self.assertTrue(all(item['value'] == {'state': 'present', 'redacted': True}
                            for item in items[:-1]))
        self.assertEqual(items[-1]['value']['value'], 17)

    def test_type_checks_do_not_execute_class_properties(self):
        from types import FunctionType
        accessed = []

        class Callable:
            @property
            def __class__(self):
                accessed.append('callable class')
                return FunctionType

            def __call__(self):
                accessed.append('call')

        class Cancel(BaseException):
            @property
            def __class__(self):
                accessed.append('exception class')
                return Exception

        self.configure()
        original = Cancel()
        observed = None
        try:
            with self.scope(), log.span(Callable()):
                raise original
        except BaseException as error:
            observed = error
        self.assertIs(observed, original)
        self.assertEqual(accessed, [])
        rows = self.records()
        self.assertEqual(rows[0]['args'], {})
        self.assertTrue(any(r.get('outcome') == 'cancelled' for r in rows))

    def test_unknown_dictionary_keys_mask_values_without_callbacks(self):
        from enum import Enum
        accessed = []

        class Auth(str, Enum):
            PIN = 'pin'

        class Key(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                accessed.append('key equality')
                return str.__eq__(self, other)

        payload = {Auth.PIN: 87654321, Key('password'): 87654321, 5: 87654321}
        self.configure()
        accessed.clear()
        with self.scope() as session:
            with log.span(example, payload=payload):
                pass
            self.assertNotIn(b'87654321', b''.join(item[1] for item in session.events))
        self.assertNotIn('87654321', json.dumps(self.records()))
        self.assertEqual(accessed, [])

    def test_span_does_not_lookup_foreign_keyword_keys(self):
        accessed = []

        class Key(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                accessed.append('keyword equality')
                return str.__eq__(self, other)

        handle = log.span(example, **{Key('count'): 9})
        self.configure()
        accessed.clear()
        with self.scope(), handle:
            pass
        self.assertEqual(accessed, [])
        self.assertEqual(self.records()[0]['args'], {})
        self.assertTrue(self.records()[0]['args_truncated'])

    def test_overlarge_keyword_source_is_omitted_with_an_indicator(self):
        self.configure()
        fields = {'extra' + str(i): 1 for i in range(256)}
        fields['count'] = 7
        with self.scope(), log.span(example, **fields):
            pass
        self.assertEqual(self.records()[0]['args'], {})
        self.assertTrue(self.records()[0]['args_truncated'])

    def test_logger_lock_rejects_hardlink_before_external_file_write(self):
        import os
        directory = self.configure()
        external = self.root / 'external-empty-file'
        external.write_bytes(b'')
        try:
            os.link(external, directory / 'logger.lock')
        except OSError:
            self.skipTest('hardlink privilege unavailable')
        with self.scope(), log.span(example, count=1):
            pass
        self.assertEqual(external.read_bytes(), b'')
        self.assertEqual(self.records(), [])

    def test_strict_lock_rejects_descriptor_links_before_first_write(self):
        from types import SimpleNamespace
        from task_governance_tool import artifact_lock
        path = self.configure() / 'logger.lock'
        real_stat = artifact_lock.os.fstat

        def multiple_links(descriptor):
            details = real_stat(descriptor)
            return SimpleNamespace(st_dev=details.st_dev, st_ino=details.st_ino,
                                   st_mode=details.st_mode, st_size=details.st_size, st_nlink=2)

        with mock.patch.object(artifact_lock.os, 'fstat', side_effect=multiple_links), \
                mock.patch.object(artifact_lock.os, 'write') as write:
            with self.assertRaises(artifact_lock.ArtifactLockError):
                with artifact_lock.zero_wait_artifact_lock(path, require_single_link=True):
                    self.fail('multiple links admitted')
        write.assert_not_called()
        self.assertEqual(path.read_bytes(), b'')

    def test_unknown_value_does_not_execute_metaclass_equality(self):
        accessed = []

        class Meta(type):
            def __eq__(cls, other):
                accessed.append('type equality')
                return False

        class Unknown(metaclass=Meta):
            pass

        self.configure()
        with self.scope(), log.span(example, payload=[Unknown()]):
            pass
        self.assertEqual(accessed, [])
        self.assertEqual(self.records()[0]['args']['payload']['items'][0]['kind'], 'object')

    def test_argument_limit_is_explicit_at_the_boundary(self):
        self.configure()
        for count in (32, 33):
            names = ['a' + str(i) for i in range(count)]
            namespace = {}
            exec('def many(' + ','.join(names) + '): pass', namespace)
            with self.scope(), log.span(namespace['many'], **dict.fromkeys(names, 1)):
                pass
        starts = [row for row in self.records() if row['event'] == 'method.start']
        self.assertEqual([len(row['args']) for row in starts], [32, 32])
        self.assertEqual([row['args_truncated'] for row in starts], [False, True])

    def test_nested_spans_restore_context_and_preserve_original_exception(self):
        self.configure()
        original = RuntimeError(SECRET)
        with self.assertRaises(RuntimeError) as caught:
            with self.scope(), log.span(example):
                with log.span(example):
                    raise original
        self.assertIs(caught.exception, original)
        records = self.records()
        starts = [r for r in records if r['event'] == 'method.start']
        ends = [r for r in records if r['event'] == 'method.end']
        self.assertEqual(starts[1]['parent_span_id'], starts[0]['span_id'])
        self.assertTrue(all(r['duration_ns'] >= 0 and r['outcome'] == 'failed' for r in ends))
        self.assertEqual(len([r for r in records if r['event'] == 'exception']), 1)
        self.assertNotIn(SECRET, json.dumps(records))
        self.assertIsNone(log._span.get())
        self.assertIsNone(log._current.get())

    def test_errors_mode_does_not_collect_success_arguments(self):
        self.configure('errors')
        with mock.patch.object(values, 'arguments', side_effect=AssertionError('unneeded expansion')):
            with self.scope(), log.span(example, payload=SECRET):
                pass
        self.assertEqual(self.records(), [])
        with self.assertRaises(ValueError):
            with self.scope(), log.span(example):
                raise ValueError(SECRET)
        self.assertEqual([r['event'] for r in self.records()], ['method.end', 'exception'])

    def test_sqlite_reuses_observer_flushes_after_close_and_no_double_sink(self):
        directory = self.configure()
        with self.scope(), log.span(example), closing(db.connect_existing(self.database)) as connection:
            connection.execute('BEGIN')
            connection.execute('INSERT INTO items VALUES (?)', (SECRET,))
            connection.commit()
            self.assertEqual(list(directory.iterdir()), [directory / 'enabled'])
        records = self.records()
        self.assertTrue(any(r.get('phase') == 'commit' for r in records))
        self.assertFalse(list(self.directory.glob('trace-*.json')))
        self.assertNotIn(SECRET, json.dumps(records))
        self.assertEqual(len({r['invocation_id'] for r in records}), 1)

    def test_unclosed_connection_or_unvalidated_binding_never_flushes(self):
        directory = self.configure()
        with self.scope():
            connection = db.connect_existing(self.database)
            with log.span(example):
                pass
        connection.close()
        self.assertEqual(list(directory.iterdir()), [directory / 'enabled'])
        with log.operation_scope(self.root, entrypoint=self.entrypoint, operation='task.show', repo_explicit=True):
            with log.span(example):
                pass
        self.assertEqual(self.records(), [])

    def test_marker_removal_and_read_only_suppress_all_output(self):
        directory = self.configure()
        with self.scope(read_only=True), log.span(example), closing(db.connect_existing(self.database)):
            pass
        self.assertEqual(self.records(), [])
        self.assertFalse(list(self.directory.glob('trace-*.json')))
        with self.scope(), log.span(example):
            (directory / 'enabled').unlink()
        self.assertEqual(self.records(), [])

    def test_nested_read_only_cannot_reenable_either_logger(self):
        self.configure()
        from task_governance_tool import sqlite_diagnostics
        with self.scope(read_only=True), self.scope(), sqlite_diagnostics.diagnostic_scope(
                self.root, entrypoint=self.entrypoint, operation='task', repo_explicit=True):
            with log.span(example), closing(db.connect_existing(self.database)) as connection:
                connection.execute('SELECT 1')
        self.assertEqual(self.records(), [])
        self.assertFalse(list(self.directory.glob('trace-*.json')))

    def test_instrumentation_failures_and_cancellation_preserve_body(self):
        self.configure()
        with mock.patch.object(log.time, 'perf_counter_ns', side_effect=OSError(SECRET)):
            with self.scope(), log.span(example):
                result = 17
        self.assertEqual(result, 17)
        error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as caught:
            with self.scope(), log.span(example):
                raise error
        self.assertIs(caught.exception, error)
        self.assertTrue(any(row.get('outcome') == 'cancelled' for row in self.records()))

    def test_invalid_marker_and_symlink_destination_are_not_written(self):
        directory = self.configure()
        (directory / 'enabled').write_bytes(b'invalid\n')
        self.marker.unlink()
        with self.scope(), log.span(example):
            pass
        self.assertEqual(self.records(), [])
        self.configure()
        from datetime import datetime
        path = directory / ('taskgov-debug.log.' + datetime.now().astimezone().date().isoformat())
        target = self.root / 'outside-log'
        target.write_bytes(b'preserve')
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest('symlink privilege unavailable')
        with self.scope(), log.span(example):
            pass
        self.assertEqual(target.read_bytes(), b'preserve')

    def test_file_count_and_total_limits_do_not_delete_existing_files(self):
        directory = self.configure()
        (directory / 'taskgov-debug.log.2000-01-01').write_bytes(b'{}\n')
        for limits in ({'MAX_FILES': 1}, {'MAX_TOTAL_BYTES': 3}):
            with mock.patch.multiple(sink, **limits):
                with self.scope(), log.span(example):
                    pass
            self.assertEqual([p.name for p in directory.glob('taskgov-debug.log.*')], ['taskgov-debug.log.2000-01-01'])
    def test_delayed_events_retain_occurrence_dates_across_midnight(self):
        directory = self.configure()
        times = iter([{'observed_at': '2026-12-31T14:59:59+00:00', 'log_date': '2026-12-31', 'utc_offset': '+0900'},
                      {'observed_at': '2026-12-31T15:00:00+00:00', 'log_date': '2027-01-01', 'utc_offset': '+0900'},
                      {'observed_at': '2026-12-31T15:00:01+00:00', 'log_date': '2027-01-01', 'utc_offset': '+0900'}])
        with mock.patch.object(log, '_moment', side_effect=lambda: next(times)):
            with self.scope(), log.span(example):
                self.assertEqual(len(list(directory.iterdir())), 1)
        self.assertEqual(len(list(directory.glob('taskgov-debug.log.*'))), 2)
        first = json.loads((directory / 'taskgov-debug.log.2026-12-31').read_bytes())
        self.assertEqual(first['event'], 'method.start')

    def test_sink_capacity_partial_tail_and_failure_leave_business_result(self):
        directory = self.configure()
        from datetime import datetime
        day = datetime.now().astimezone().date().isoformat()
        path = directory / ('taskgov-debug.log.' + day)
        path.write_bytes(b'partial')
        with self.scope(), log.span(example):
            pass
        self.assertTrue(path.read_bytes().startswith(b'partial\n{'))
        before = path.read_bytes()
        with mock.patch.object(sink, 'MAX_DAY_BYTES', len(before)):
            with self.scope(), closing(db.connect_existing(self.database)) as connection, connection:
                connection.execute("INSERT INTO items VALUES ('saved')")
        self.assertEqual(path.read_bytes(), before)
        with mock.patch.object(sink, '_write_all', side_effect=OSError(SECRET)):
            with self.scope(), log.span(example):
                result = 42
        self.assertEqual(result, 42)
        with closing(db.connect_existing(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM items').fetchone()[0], 2)

    def test_thread_contention_drops_without_waiting_and_json_lines_do_not_mix(self):
        self.configure()
        barrier = threading.Barrier(4)
        def worker():
            with self.scope(), log.span(example):
                barrier.wait(timeout=5)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertTrue(self.records())
        self.assertTrue(all(row['format'] == 'taskgov-debug-v1' for row in self.records()))
