"""Wait diagnostics: isolated workers, safe checkpoints and primary invariance."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import io
import json
import sqlite3
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from tests import test_debug_log as debug_fixture, test_review_wait_direct as direct_fixture
from tests import test_review_wait_managed as managed_fixture
from tests.test_debug_log import SECRET
from tests.test_review_wait_direct import META, NEW_TURN
from tests.test_review_wait_service import BINDING, CHILD, TURN, PARENT
from task_governance_tool import debug_log as log, sqlite_connection as db
from task_governance_tool.review_wait_runtime import worker_diagnostics as diag
from task_governance_tool.review_wait_runtime.worker_diagnostics import Stage, Outcome
from task_governance_tool.review_wait_runtime.review_wait_direct import DirectProbe
from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn, HostAdapterError
from task_governance_tool.review_wait_runtime.review_wait_server import serve

IDENTITY = ('tg_task_0123456789abcdef', 'wait-' + 'a' * 32,
            '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222')


class CheckpointTests(unittest.TestCase):
    setUp = debug_fixture.DebugLogTests.setUp
    enable = debug_fixture.DebugLogTests.enable
    configure = debug_fixture.DebugLogTests.configure
    scope = debug_fixture.DebugLogTests.scope
    records = debug_fixture.DebugLogTests.records

    def test_sequential_active_transaction_close_releases_context_after_trailing_end(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            for index in range(12):
                with closing(db.connect_existing(self.database)) as connection:
                    connection.execute('BEGIN')
                    if index == 11:
                        with self.assertRaises(sqlite3.OperationalError):
                            connection.execute('SELECT missing FROM items')
                self.assertEqual(session.wait_sql.connections, {})
                self.assertEqual(session.wait_sql.size, 0)
            self.assertNotIn('db_context_limit', session.dropped)
        rows = [r for r in self.records() if r['event'] == 'wait.db_failure']
        self.assertEqual(len(rows), 2)
        context = rows[-1]['context']
        self.assertEqual(context['end']['end_reason'], 'close')
        self.assertEqual(context['failure']['transaction_id'], context['end']['transaction_id'])

    def test_first_failed_transaction_cannot_inherit_a_later_transaction_end(self):
        self.configure()
        with self.scope():
            diag.bind(*IDENTITY)
            with closing(db.connect_existing(self.database)) as connection:
                connection.execute('BEGIN')
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute('SELECT missing FROM items')
                connection.rollback()
                connection.execute('BEGIN')
                connection.execute('SELECT value FROM items').fetchall()
                connection.commit()
        last = [r for r in self.records() if r['event'] == 'wait.db_failure'][-1]['context']
        self.assertEqual([last[key]['transaction_id'] for key in ('begin', 'failure', 'end')], [1, 1, 1])
        self.assertEqual(last['end']['end_reason'], 'rollback')

    def test_failed_open_releases_context_without_inventing_close(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            for _ in range(10):
                with self.assertRaises(sqlite3.OperationalError):
                    db.connect_existing(self.database.parent / 'absent.sqlite')
                self.assertEqual(session.wait_sql.connections, {})
                self.assertEqual(session.wait_sql.size, 0)
            self.assertNotIn('db_context_limit', session.dropped)
        failures = [r for r in self.records() if r['event'] == 'wait.db_failure']
        self.assertEqual(len(failures), 10)
        self.assertTrue(all(r['context']['close'] is None for r in failures))

    def test_failed_nested_begin_keeps_actual_begin_and_failure_identity(self):
        self.configure()
        with self.scope():
            diag.bind(*IDENTITY)
            with closing(db.connect_existing(self.database)) as connection:
                connection.execute('BEGIN')
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute('BEGIN')
                diag.bind('tg_task_1111111111111111')
                connection.rollback()
        last = [r for r in self.records() if r['event'] == 'wait.db_failure'][-1]
        self.assertEqual(last['task_id'], IDENTITY[0])
        self.assertEqual(last['context']['begin']['outcome'], 'succeeded')
        self.assertEqual(last['context']['failure']['outcome'], 'failed')
        self.assertEqual(last['context']['end']['end_reason'], 'rollback')

    def test_script_unknown_start_is_not_reconstructed_from_old_begin(self):
        self.configure()
        with self.scope():
            diag.bind(*IDENTITY)
            with closing(db.connect_existing(self.database)) as connection:
                connection.execute('BEGIN')
                with self.assertRaises(sqlite3.OperationalError):
                    connection.executescript('BEGIN; SELECT missing FROM items;')
                connection.rollback()
        last = [r for r in self.records() if r['event'] == 'wait.db_failure'][-1]['context']
        self.assertIsNone(last['begin'])
        self.assertIsNone(last['end'])

    def test_ended_reviewers_with_pending_answers_are_not_reported_as_running(self):
        from tests.test_review_wait_polling import ManagedPollingTests
        fixture = ManagedPollingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for key, child in fixture.children.items():
            fixture.children[key] = replace(child, status='completed', thread_status='idle')
        self.configure()
        with self.scope():
            diag.bind(*IDENTITY)
            diag.begin_observation(fixture.parent, terminal=True, parent_matches=True, turn_matches=True)
            ready = fixture.observe_progress()
            self.assertFalse(ready)
        current = [r for r in self.records() if r.get('stage') == 'readiness'][-1]
        self.assertEqual(current['reason'], 'answers_pending')
        self.assertTrue(current['all_reviewers_ended'])
        self.assertFalse(current['review_progress_ready'])
        self.assertEqual(fixture.host.read_child.call_count, 2)
        self.assertEqual(fixture.tracking_reader.call_count, 2)

    def test_saved_answer_and_readmission_are_not_fresh_host_end_observations(self):
        from tests.test_review_wait_polling import ManagedPollingTests
        from task_governance_tool.review_answer_runtime import TrackingReadmissionRequired
        self.configure()
        for saved in (True, False):
            with self.subTest(saved=saved):
                fixture = ManagedPollingTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.worker._saved_results_ready = saved
                if not saved:
                    fixture.tracking_reader.side_effect = TrackingReadmissionRequired()
                with self.scope():
                    diag.bind(*IDENTITY)
                    diag.begin_observation(fixture.parent, terminal=True, parent_matches=True, turn_matches=True)
                    ready = fixture.observe_progress()
                    self.assertEqual(ready, saved)
                current = [r for r in self.records() if r.get('stage') == 'readiness'][-1]
                self.assertIsNone(current['all_reviewers_ended'])
                self.assertEqual(current['reviewer_observation_source'],
                                 'saved_results' if saved else 'readmission')
                self.assertEqual(current['reason'], 'dispatch_candidate' if saved else 'readiness_unknown')
                fixture.host.read_child.assert_not_called()

    def test_saved_results_in_both_shared_loops_never_emit_host_end(self):
        from tests.test_review_wait_polling import ManagedPollingTests
        self.configure()
        for polling in (True, False):
            with self.subTest(polling=polling):
                fixture = ManagedPollingTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                if not polling:
                    fixture.worker.polling_factory = None
                finalizer = SimpleNamespace(saved_results_ready=lambda: True)
                before = len(self.records())
                with self.scope(), mock.patch.object(fixture.worker, '_finalizer', return_value=finalizer):
                    diag.bind(*IDENTITY)
                    fixture.run_polls(count=1)
                rows = self.records()[before:]
                latest = [r for r in rows if r['event'] == 'wait.checkpoint'][-1]['last_observation']
                self.assertIsNone(latest['all_reviewers_ended'])
                self.assertTrue(latest['review_progress_ready'])
                self.assertEqual(latest['reviewer_observation_source'], 'saved_results' if polling else 'unavailable')
                self.assertFalse(any(r.get('reason') == 'all_reviewers_ended' for r in rows))
                fixture.host.read_child.assert_not_called()
                fixture.dispatch.assert_called_once()

    def test_shared_loop_retains_host_end_before_collection_outcomes(self):
        from tests.test_review_wait_polling import ManagedPollingTests
        self.configure()
        for outcome in ('success', 'error', 'candidate_error', 'cancel', 'deadline'):
            with self.subTest(outcome=outcome):
                fixture = ManagedPollingTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                for reviewer in fixture.reviewers:
                    fixture.ended(reviewer.reviewer_id)
                def collect(reviewer_id):
                    if outcome == 'error':
                        raise RuntimeError(SECRET)
                    if outcome == 'cancel':
                        fixture.worker._stop.set()
                    elif outcome == 'deadline':
                        fixture.clock.elapsed = 1201
                    else:
                        fixture.freeze(reviewer_id)
                fixture.collect_action = collect
                if outcome == 'candidate_error':
                    fixture.polling_factory.return_value = (fixture.guard, fixture.tracking_reader,
                        mock.Mock(side_effect=RuntimeError(SECRET)))
                before = len(self.records())
                with self.scope():
                    diag.bind(*IDENTITY)
                    fixture.run_polls(count=1)
                rows = self.records()[before:]
                latest = [r for r in rows if r['event'] == 'wait.checkpoint'][-1]['last_observation']
                self.assertTrue(latest['all_reviewers_ended'])
                self.assertEqual(latest['reviewer_observation_source'], 'host')
                self.assertEqual(latest['parent_thread_state'], 'idle')
                self.assertEqual(latest['review_progress_ready'], True if outcome == 'success' else None)
                self.assertEqual(fixture.host.read_child.call_count, 2)
                self.assertEqual(fixture.dispatch.call_count, 1 if outcome == 'success' else 0)
                self.assertNotIn(SECRET, json.dumps(rows))

    def test_shared_loops_keep_final_host_read_before_observation_save_fails(self):
        from tests.test_review_wait_polling import ManagedPollingTests
        self.configure()
        for polling in (True, False):
            with self.subTest(polling=polling):
                fixture = ManagedPollingTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.tracking.clear()
                fixture.worker.answer_tracking = lambda controller: {}
                if not polling:
                    fixture.worker.polling_factory = None
                for reviewer in fixture.reviewers:
                    fixture.ended(reviewer.reviewer_id)
                before = len(self.records())
                with self.scope(), mock.patch.object(fixture.worker, '_observe_reviewer',
                        side_effect=[True, RuntimeError(SECRET)]):
                    diag.bind(*IDENTITY)
                    fixture.run_polls(count=1)
                rows = self.records()[before:]
                latest = [r for r in rows if r['event'] == 'wait.checkpoint'][-1]['last_observation']
                self.assertTrue(latest['all_reviewers_ended'])
                self.assertIsNone(latest['review_progress_ready'])
                self.assertEqual(latest['reviewer_observation_source'], 'host')
                self.assertEqual(fixture.host.read_child.call_count, 2)
                fixture.dispatch.assert_not_called()
                self.assertNotIn(SECRET, json.dumps(rows))

    def test_last_host_read_keeps_observation_before_cancel_or_deadline(self):
        from tests.test_review_wait_polling import ManagedPollingTests
        self.configure()
        for polling in (True, False):
            for outcome in ('cancel', 'deadline'):
                for valid in (True, False):
                    with self.subTest(polling=polling, outcome=outcome, valid=valid):
                        fixture = ManagedPollingTests()
                        fixture.setUp()
                        self.addCleanup(fixture.doCleanups)
                        if not polling:
                            fixture.worker.polling_factory = None
                            fixture.worker.answer_tracking = lambda controller: {}
                        for reviewer in fixture.reviewers:
                            fixture.ended(reviewer.reviewer_id)
                        final_reviewer = fixture.reviewers[-1].reviewer_id
                        original_read = fixture.host.read_child.side_effect
                        def read(reviewer_id):
                            child = original_read(reviewer_id)
                            if reviewer_id == final_reviewer:
                                if outcome == 'cancel':
                                    fixture.worker._stop.set()
                                else:
                                    fixture.clock.elapsed = 1201
                                if not valid:
                                    child = replace(child, child_id=PARENT)
                            return child
                        fixture.host.read_child.side_effect = read
                        before = len(self.records())
                        with self.scope():
                            diag.bind(*IDENTITY)
                            fixture.run_polls(count=1)
                        rows = self.records()[before:]
                        latest = [r for r in rows if r['event'] == 'wait.checkpoint'][-1]['last_observation']
                        self.assertEqual(latest['all_reviewers_ended'], True if valid else None)
                        self.assertIsNone(latest['review_progress_ready'])
                        self.assertEqual(fixture.host.read_child.call_count, 2)
                        fixture.dispatch.assert_not_called()
                        fixture.failure.assert_not_called()
                        fixture.collector.assert_not_called()
                        self.assertTrue(any(r.get('stage') == 'control_end' and
                            r.get('outcome') == ('cancelled' if outcome == 'cancel' else 'deadline') for r in rows))

    def test_failed_connection_context_survives_detail_pressure_and_closes(self):
        self.configure('errors')
        with self.scope() as session:
            diag.bind(*IDENTITY)
            with closing(db.connect_existing(self.database)) as connection:
                connection.execute('PRAGMA foreign_keys=ON')
                connection.executescript('CREATE TABLE p(id PRIMARY KEY); CREATE TABLE c(id REFERENCES p(id) DEFERRABLE INITIALLY DEFERRED);')
                connection.execute('BEGIN')
                connection.execute('INSERT INTO c VALUES (1)')
                for _ in range(400):
                    connection.execute('SELECT value FROM items').fetchall()
                    session.emit('test.detail', {}, failed=True)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.commit()
                connection.rollback()
            self.assertEqual(session.wait_sql.size, 0)
            self.assertLessEqual(session.size + session.important_size + session.wait_sql.size, log.MAX_BUFFER_BYTES)
        contexts = [r for r in self.records() if r['event'] == 'wait.db_failure']
        self.assertEqual(len(contexts), 2)
        current = contexts[-1]
        self.assertEqual(current['failure']['phase'], 'commit')
        self.assertEqual(current['context']['open']['outcome'], 'succeeded')
        self.assertEqual(current['context']['begin']['phase'], 'begin')
        self.assertEqual(current['context']['end']['end_reason'], 'rollback')
        self.assertEqual(current['context']['close']['outcome'], 'succeeded')
        self.assertEqual(current['failure']['transaction_id'], current['context']['begin']['transaction_id'])
        self.assertEqual((current['coverage'], current['contender']), ('partial', 'unknown'))

    def test_database_context_capacity_and_missing_begin_remain_explicit(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            from contextlib import ExitStack
            with ExitStack() as stack:
                connections = [stack.enter_context(closing(db.connect_existing(self.database))) for _ in range(9)]
                with self.assertRaises(sqlite3.OperationalError):
                    connections[-1].execute('SELECT nonexistent FROM items')
                self.assertEqual(len(session.wait_sql.connections), 8)
                self.assertGreater(session.dropped['db_context_limit'], 0)
        unknown = [r for r in self.records() if r['event'] == 'wait.db_failure' and r['context'] is None]
        self.assertTrue(unknown)
        self.assertEqual(unknown[0]['coverage'], 'partial')

    def test_readiness_last_observation_survives_repeated_conditions_and_errors_mode(self):
        self.configure('errors')
        with self.scope() as session:
            diag.bind(*IDENTITY)
            for _ in range(500):
                diag.begin_observation(ChildTurn(PARENT, TURN, 'completed', 'active'),
                    terminal=False, parent_matches=True, turn_matches=False)
                diag.progress(True, True, source='host')
            self.assertEqual(len(session.wait_seen), 3)
            diag.begin_observation(ChildTurn(PARENT, TURN, 'completed', 'systemError'),
                terminal=True, parent_matches=True, turn_matches=False)
            diag.progress(True, True, source='host')
            diag.event(Stage.CONTROL_END, Outcome.DEADLINE, reason='deadline')
        final = [r for r in self.records() if r['event'] == 'wait.checkpoint'][-1]
        self.assertEqual(final['last_observation']['reason'], 'parent_not_idle')
        self.assertFalse(final['last_observation']['original_turn_matches'])
        self.assertEqual(final['last_observation']['task_id'], IDENTITY[0])
        self.assertFalse(any(r.get('stage') == 'readiness' for r in self.records()))

    def test_timer_config_read_reasons_do_not_change_public_error_or_call_count(self):
        from task_governance_tool.review_wait_runtime import review_wait_host as host_module
        host = object.__new__(host_module.PublicMcpHost)
        host._automation_id = 'fixture'
        host._timezone_reader = None
        host._codex_home = self.root
        host._parent_id = PARENT
        host._timezone = 'UTC'
        host._timezone_source = 'host_node_intl'
        self.configure()
        for code in ('automation_config_unsafe', 'automation_config_invalid',
                     'automation_config_changed', 'automation_config_unavailable'):
            error = HostAdapterError(code)
            with self.scope(), mock.patch.object(host, '_call', return_value=()) as call, \
                 mock.patch.object(host_module, '_read_config', side_effect=error) as read:
                diag.bind(*IDENTITY)
                with self.assertRaises(HostAdapterError) as raised:
                    host.view_heartbeat()
                self.assertIs(raised.exception, error)
                self.assertEqual((call.call_count, read.call_count), (1, 1))
        reasons = [r['reason'] for r in self.records() if r.get('stage') == 'timer_config']
        self.assertEqual(len(set(reasons)), 4)

    def test_checkpoints_close_connections_first_and_keep_sql_identity(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            with closing(db.connect_existing(self.database)) as connection:
                connection.execute('BEGIN')
                diag.event(Stage.WORKER, Outcome.STARTED)
                diag.checkpoint(urgent=True)
                self.assertEqual(self.records(), [])
                connection.rollback()
            diag.checkpoint(urgent=True)
            self.assertTrue(self.records())
            self.assertFalse(session.collector.sealed)
            with closing(db.connect_existing(self.database)) as connection:
                connection.execute('SELECT value FROM items').fetchall()
            diag.event(Stage.WORKER, Outcome.RETURNED)
        rows = self.records()
        self.assertEqual(len({r['invocation_id'] for r in rows}), 1)
        self.assertTrue(any(r['event'].startswith('db.sql.') for r in rows))
        checks = [r for r in rows if r['event'] == 'wait.checkpoint']
        self.assertEqual([r['checkpoint'] for r in checks], [1, 2])
        self.assertEqual(checks[-1]['previous'], 'written')
        self.assertEqual(checks[-1]['worker_exit'], 'unknown')

    def test_sql_and_detail_saturation_cannot_evict_first_cause_or_exit_events(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            diag.event(Stage.FAILURE, Outcome.FAILED, reason='parent_turn_changed', error=ValueError(SECRET))
            with closing(db.connect_existing(self.database)) as connection:
                for _ in range(400):
                    connection.execute('SELECT value FROM items').fetchall()
                    session.emit('test.detail', {}, failed=True)
            diag.event(Stage.CLEANUP, Outcome.FAILED, reason='cleanup_failed')
            diag.event(Stage.WORKER, Outcome.RETURNED)
            self.assertLessEqual(session.size + session.important_size, log.MAX_BUFFER_BYTES)
            self.assertLessEqual(len(session.events) + len(session.important), log.MAX_EVENTS)
        rows = self.records()
        transitions = [r for r in rows if r['event'] == 'wait.transition']
        self.assertEqual([r['stage'] for r in transitions], ['failure', 'cleanup', 'worker'])
        self.assertEqual(transitions[0]['reason'], 'parent_turn_changed')
        self.assertNotIn(SECRET, json.dumps(rows))
        self.assertTrue(rows[-1]['dropped'])

    def test_successful_exact_detail_capacity_keeps_reserved_checkpoint(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            for _ in range(61):
                session.emit('test.detail', {})
            for _ in range(97 + 33):
                session.emit('test.reserved', {}, reserved=True)
            self.assertEqual(len(session.events), 191)
            self.assertEqual(session.dropped, {})
            # Test the final checkpoint's exact input budget; scope exit also
            # publishes the independent SQL collector's ordinary summary.
            session.checkpoint(final=True)
            rows = self.records()
        self.assertEqual(len(rows), 192)
        self.assertEqual(rows[-1]['event'], 'wait.checkpoint')
        self.assertEqual(rows[-1]['dropped'], {})
        self.assertTrue(rows[-1]['scope_returned'])

    def test_rebinding_preserves_buffered_identity_and_scope_limits(self):
        self.configure()
        second = (*IDENTITY[:2], '33333333-3333-4333-8333-333333333333', IDENTITY[3])
        with self.scope() as session:
            with closing(db.connect_existing(self.database)):
                for identity in (IDENTITY, second, IDENTITY):
                    diag.bind(*identity)
                    diag.event(Stage.TIMER_ARM, Outcome.ATTEMPT)
                    diag.checkpoint(urgent=True, poll=True)
                self.assertEqual(self.records(), [])
                self.assertEqual(session.wait_polls, 3)
                self.assertEqual(len(session.wait_seen), 2)
            diag.checkpoint(urgent=True)
            self.assertEqual(session.checkpoints, 1)
            for index in range(200):
                diag.bind(IDENTITY[0], 'wait-' + f'{index:032x}', *IDENTITY[2:])
                diag.event(Stage.TIMER_ARM, Outcome.ATTEMPT)
            self.assertEqual(session.checkpoints, 1)
            self.assertEqual(len(session.wait_seen), 128)
            self.assertGreater(session.dropped['transition_limit'], 0)
        events = [r for r in self.records() if r['event'] == 'wait.transition']
        self.assertEqual([r['probe_id'] for r in events[:2]], [IDENTITY[2], second[2]])
        checkpoint = next(r for r in self.records() if r['event'] == 'wait.checkpoint')
        self.assertEqual((checkpoint['aggregate_scope'], checkpoint['polls']), ('invocation', 3))

    def test_unchanged_polls_are_bounded_and_never_synchronously_flush_each_tick(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            diag.event(Stage.WORKER, Outcome.STARTED)
            diag.checkpoint()
            initial = session.checkpoints
            for _ in range(1000):
                diag.event(Stage.OBSERVATION, Outcome.BUSY, reason='observation_busy')
                diag.checkpoint(poll=True)
            self.assertEqual(session.checkpoints, initial)
            self.assertEqual(len(session.important), 1)
        checks = [r for r in self.records() if r['event'] == 'wait.checkpoint']
        self.assertEqual(checks[-1]['polls'], 1000)

    def test_checkpoint_budget_and_sink_failure_are_explicit_without_replay(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            diag.event(Stage.NOTIFICATION, Outcome.ACCEPTED)
            with mock.patch.object(session.sink, 'write', side_effect=OSError(SECRET)) as write:
                diag.checkpoint(urgent=True)
                self.assertEqual(write.call_count, 1)
                self.assertEqual(session.events, [])
                self.assertEqual(session.important, [])
            diag.event(Stage.RECEIPT_END, Outcome.UNKNOWN, reason='read_failed')
            session.checkpoints = log.WAIT_FLUSHES
            diag.checkpoint(urgent=True)
        rows = self.records()
        self.assertFalse(any(r.get('outcome') == 'accepted' for r in rows))
        self.assertEqual(rows[-1]['previous'], 'unknown')
        self.assertEqual(rows[-1]['dropped']['unavailable'], 1)
        self.assertEqual(rows[-1]['dropped']['checkpoint_limit'], 1)

    def test_errors_mode_unknown_fields_and_read_only_do_not_leak(self):
        class Hostile:
            def __str__(self):
                raise AssertionError('expanded')
            __repr__ = __str__
        self.configure('errors')
        with self.scope():
            diag.bind(Hostile(), SECRET, Hostile(), None)
            diag.event(Stage.WORKER, Outcome.STARTED)
            diag.event(Stage.FAILURE, Outcome.FAILED, reason=Hostile(), error=ValueError(SECRET))
            diag.event(Hostile(), Outcome.FAILED)
            diag.checkpoint(urgent=True)
        rows = self.records()
        self.assertTrue(rows)
        self.assertFalse(any(r.get('outcome') == 'started' for r in rows))
        self.assertEqual(rows[0]['reason'], 'unknown')
        self.assertIsNone(rows[0]['task_id'])
        before = rows
        with self.scope(read_only=True):
            diag.bind(*IDENTITY)
            diag.event(Stage.FAILURE, Outcome.FAILED)
            diag.checkpoint(urgent=True)
        self.assertEqual(self.records(), before)
        self.assertNotIn(SECRET, json.dumps(rows))

    def test_periodic_checkpoint_keeps_earlier_events_and_records_later_failure(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            diag.event(Stage.WORKER, Outcome.STARTED)
            diag.checkpoint()
            first = self.records()
            session.last_checkpoint -= 60_000_000_001
            diag.event(Stage.OBSERVATION, Outcome.ENDED, reason='all_reviewers_ended')
            diag.checkpoint(poll=True)
            self.assertEqual(session.checkpoints, 2)
            self.assertEqual(self.records()[:len(first)], first)
            diag.event(Stage.FAILURE, Outcome.FAILED, reason='wait_changed')
        self.assertIn('wait_changed', [r.get('reason') for r in self.records()])

    def test_errors_batch_keeps_checkpoint_metadata_without_success_transitions(self):
        self.configure('errors')
        with self.scope():
            diag.bind(*IDENTITY)
            diag.event(Stage.WORKER, Outcome.STARTED)
            diag.event(Stage.FAILURE, Outcome.FAILED, reason='read_failed')
            diag.checkpoint(urgent=True, poll=True)
        rows = self.records()
        checks = [r for r in rows if r['event'] == 'wait.checkpoint']
        self.assertEqual(len(checks), 1)
        self.assertEqual((checks[0]['checkpoint'], checks[0]['previous'], checks[0]['polls']),
                         (1, 'not_attempted', 1))
        self.assertEqual(checks[0]['coverage'], 'partial')
        self.assertEqual(checks[0]['dropped'], {})
        self.assertGreaterEqual(checks[0]['elapsed_ns'], 0)
        self.assertFalse(any(r.get('outcome') == 'started' for r in rows))
        with self.scope():
            diag.bind(*IDENTITY)
            diag.event(Stage.WORKER, Outcome.STARTED)
        self.assertEqual(self.records(), rows)

    def test_capacity_contention_and_unknown_cleanup_suppress_without_replay(self):
        self.configure()
        for outcome in ('capacity', 'contended', 'disabled', 'unavailable'):
            with self.subTest(outcome=outcome), self.scope() as session:
                diag.bind(*IDENTITY)
                diag.event(Stage.TIMER_ARM, Outcome.ATTEMPT)
                with mock.patch.object(session.sink, 'write', return_value=outcome) as write:
                    diag.checkpoint(urgent=True)
                self.assertEqual(write.call_count, 1)
                self.assertEqual(session.dropped[outcome], 1)
                self.assertEqual(session.events, [])
                self.assertEqual(session.important, [])
                diag.event(Stage.FAILURE, Outcome.FAILED, reason='wait_changed')
        before = self.records()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            diag.event(Stage.WORKER, Outcome.STARTED)
            session.collector.mark_untracked()
            diag.checkpoint(urgent=True)
        self.assertEqual(self.records(), before)

    def test_nested_read_only_and_off_never_publish_intermediate(self):
        self.marker.unlink()
        with mock.patch.object(log.projection, 'exception', side_effect=AssertionError('OFF projection')):
            with self.scope():
                diag.bind(*IDENTITY)
                diag.event(Stage.FAILURE, Outcome.FAILED, error=ValueError(SECRET))
                diag.checkpoint(urgent=True)
        self.assertEqual(self.records(), [])
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            diag.event(Stage.WORKER, Outcome.STARTED)
            with self.scope(read_only=True):
                session.checkpoint(urgent=True)
                diag.event(Stage.FAILURE, Outcome.FAILED)
                self.assertEqual(self.records(), [])
        self.assertFalse(any(r.get('stage') == 'failure' for r in self.records()))

    def test_important_budget_preserves_first_failure_and_marks_omission(self):
        self.configure()
        with self.scope() as session:
            diag.bind(*IDENTITY)
            for stage in Stage:
                for outcome in (Outcome.FAILED, Outcome.UNKNOWN, Outcome.RETURNED):
                    diag.event(stage, outcome, reason='unknown')
            self.assertLessEqual(sum(e[2] for e in session.important), 16)
        self.assertTrue(self.records()[-1]['dropped']['important_limit'])
        self.assertTrue(any(r.get('stage') == 'worker' and r['outcome'] == 'failed' for r in self.records()))

    def test_concurrent_workers_keep_operation_and_identity_separate(self):
        self.configure()
        barrier = threading.Barrier(2)
        def run(index):
            with self.scope(independent=True):
                diag.bind('tg_task_' + str(index) * 16, IDENTITY[1], IDENTITY[2], IDENTITY[3])
                diag.event(Stage.WORKER, Outcome.STARTED)
                barrier.wait(5)
                diag.event(Stage.FAILURE, Outcome.FAILED, reason='wait_changed')
        workers = [threading.Thread(target=run, args=(i,)) for i in (1, 2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertFalse(worker.is_alive())
        groups = {}
        for row in self.records():
            if row['event'] == 'wait.transition':
                groups.setdefault(row['invocation_id'], set()).add(row['task_id'])
        self.assertTrue(groups)  # The existing zero-wait sink may drop one batch.
        self.assertTrue(all(len(ids) == 1 for ids in groups.values()))

    def test_intermediate_file_survives_forced_child_loss_without_exit_claim(self):
        self.configure()
        scripts = Path(__file__).resolve().parents[1] / 'task-governance-tool/scripts'
        code = '''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from task_governance_tool.debug_log import operation_scope
from task_governance_tool.review_wait_runtime.worker_diagnostics import bind, event, checkpoint, Stage, Outcome
with operation_scope(Path(sys.argv[2]), entrypoint=Path(sys.argv[3]), operation='review_wait_worker', repo_explicit=True, validated=True):
    bind('tg_task_0123456789abcdef', 'wait-' + 'a'*32)
    event(Stage.WORKER, Outcome.STARTED)
    checkpoint(urgent=True)
    print('persisted', flush=True)
    time.sleep(60)
'''
        child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', code,
            str(scripts), str(self.root), str(self.entrypoint)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            # Bound readiness without a blocking pipe read if child fails.
            deadline = time.monotonic() + 15
            while not self.records() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.records())
        finally:
            if child.poll() is None:
                child.kill()  # Only this isolated test's child, no live service.
            child.communicate(timeout=10)
        rows = self.records()
        self.assertTrue(any(r.get('outcome') == 'started' for r in rows))
        self.assertFalse(any(r.get('stage') in {'join', 'exit_save'} for r in rows))
        self.assertTrue(all(r.get('worker_exit', 'unknown') == 'unknown' for r in rows))


class WaitLogTests(unittest.TestCase):
    setUp = direct_fixture.DirectProbeTests.setUp
    make = direct_fixture.DirectProbeTests.make
    cleanup = direct_fixture.DirectProbeTests.cleanup
    call = direct_fixture.DirectProbeTests.call
    until = direct_fixture.DirectProbeTests.until
    ended = direct_fixture.DirectProbeTests.ended
    status = direct_fixture.DirectProbeTests.status

    def enable_logs(self, mode='debug', kind=DirectProbe, *, validated=True):
        self.rows = []
        fixture = self
        class Sink:
            directory = fixture.path.parent
            root = fixture.path.parent
            marker = b'fixture'
            identity = None
            def write(self, day, data):
                fixture.rows.extend(json.loads(line) for line in data.splitlines())
                return 'written'
        sink = Sink()
        sink.mode = mode
        self.enterContext(mock.patch.object(log, 'admit', return_value=sink))
        self.scope = lambda: log.operation_scope(self.path.parent, entrypoint=self.path,
            operation='review_wait_worker', validated=validated, independent=True)
        self.direct = kind(self.path, lambda: self.current, lambda metadata: self.host,
            lambda: self.now, cycle_seconds=0.01, join_seconds=1, diagnostic_scope=self.scope)
        self.instances.append(self.direct)

    def start(self, version=2):
        with self.scope():
            result = self.call('direct_delete_start' if version == 2 else 'direct_start')
        self.assertTrue(result['ok'], result)
        return result

    def joined(self):
        self.direct._worker.join(5)
        self.assertFalse(self.direct._worker.is_alive())
        observer = self.direct._termination_observer
        if observer is not None:
            observer.join(5)

    def events(self, stage):
        return [row for row in self.rows if row.get('stage') == stage.value]

    def test_success_is_separate_from_actual_exit_and_saved_exit(self):
        self.enable_logs()
        self.start()
        self.ended()
        self.joined()
        self.assertEqual(len(self.host.sends), 1)
        self.assertEqual(self.host.effects, ['arm', 'delete'])
        for stage, outcome in [(Stage.TIMER_ARM, 'confirmed'), (Stage.TIMER_DELETE, 'confirmed'),
                (Stage.NOTIFICATION, 'accepted'), (Stage.WORKER, 'returned'),
                (Stage.JOIN, 'confirmed'), (Stage.EXIT_SAVE, 'confirmed')]:
            self.assertIn(outcome, [r['outcome'] for r in self.events(stage)])
        ids = {(r['probe_id'], r['worker_id']) for r in self.rows if r.get('probe_id')}
        self.assertEqual(len(ids), 1)
        self.assertGreaterEqual(len({r['invocation_id'] for r in self.rows}), 3)

    def test_legacy_has_no_fabricated_join_proof(self):
        self.enable_logs()
        self.start(version=1)
        self.ended()
        self.joined()
        self.assertEqual(self.status(), 'accepted')
        self.assertEqual(self.events(Stage.EXIT_SAVE), [])
        self.assertTrue(all(r.get('worker_id') is None for r in self.rows))

    def test_caught_cause_survives_cleanup_failure(self):
        self.enable_logs()
        self.start()
        self.host.parent = ChildTurn(PARENT, NEW_TURN, 'inProgress', 'active')
        with mock.patch.object(self.host, 'update_heartbeat', side_effect=ValueError(SECRET)):
            self.joined()
        self.assertIn('parent_turn_changed', [r['reason'] for r in self.events(Stage.FAILURE)])
        self.assertIn('cleanup_failed', [r['reason'] for r in self.events(Stage.CLEANUP)])
        self.assertEqual(self.host.sends, [])
        self.assertNotIn(SECRET, json.dumps(self.rows))

    def test_send_rejection_and_unknown_are_not_receipt_or_replayed(self):
        self.enable_logs()
        self.host.outcome = 'rejected'
        self.start()
        self.ended()
        self.joined()
        self.assertEqual(len(self.host.sends), 1)
        self.assertIn('rejected', [r['outcome'] for r in self.events(Stage.NOTIFICATION)])
        self.assertEqual(self.events(Stage.RECEIPT), [])

    def test_send_exception_preserves_unknown(self):
        self.enable_logs()
        self.host.fail_send = True
        self.start()
        self.ended()
        self.joined()
        self.assertEqual(self.status(), 'unknown')
        self.assertEqual(len(self.host.sends), 1)
        self.assertIn('unknown', [r['outcome'] for r in self.events(Stage.NOTIFICATION)])

    def test_deletion_unknown_prevents_send(self):
        self.enable_logs()
        self.host.fail_delete = True
        self.start()
        self.ended()
        self.joined()
        self.assertEqual(self.host.sends, [])
        self.assertIn('unknown', [r['outcome'] for r in self.events(Stage.TIMER_DELETE)])

    def test_deadline_and_cancel_are_distinct(self):
        self.enable_logs()
        self.start()
        self.now += timedelta(seconds=1201)
        self.joined()
        self.assertIn('deadline', [r['outcome'] for r in self.events(Stage.CONTROL_END)])
        self.assertEqual(self.status(), 'expired')

    def test_reviewers_ended_busy_parent_expires_without_normal_notification(self):
        self.enable_logs()
        observed = threading.Event()
        original = diag.progress
        def ready(*args, **kwargs):
            original(*args, **kwargs)
            if args[0] is True and args[1] is True:
                observed.set()
        with mock.patch.object(diag, 'progress', side_effect=ready):
            self.start()
            self.host.turn = ChildTurn(CHILD, TURN, 'completed', 'idle')
            self.assertTrue(observed.wait(5))
            self.now += timedelta(seconds=1201)
            self.joined()
        checks = [r for r in self.rows if r['event'] == 'wait.checkpoint' and r.get('last_observation')]
        self.assertEqual(checks[-1]['last_observation']['reason'], 'parent_not_terminal')
        self.assertTrue(checks[-1]['last_observation']['all_reviewers_ended'])
        self.assertEqual(self.host.sends, [])
        self.assertEqual(self.status(), 'expired')

    def test_worker_real_commit_failure_keeps_first_context_and_cleanup(self):
        self.enable_logs()
        registration = self.path.parent / 'registration.sqlite'
        with closing(db.connect(registration)) as connection:
            connection.execute('CREATE TABLE p(id PRIMARY KEY)')
        # A real second connection holds a read lock. Its ownership is known
        # only to this fixture and must never be inferred by the diagnostics.
        reader = self.enterContext(closing(db.connect_existing(registration, timeout=0)))
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM p').fetchall()
        def register(*args):
            with closing(db.connect_existing(registration, timeout=0)) as connection:
                connection.execute('BEGIN')
                connection.execute('INSERT INTO p VALUES (1)')
                for _ in range(300):
                    log._current.get().emit('test.detail', {}, failed=True)
                connection.commit()
        with mock.patch.object(self.direct, '_observe_progress', side_effect=register):
            self.start()
            self.joined()
        failures = [r for r in self.rows if r['event'] == 'wait.db_failure']
        self.assertTrue(any(r['failure']['phase'] == 'commit' for r in failures))
        self.assertTrue(any(r['failure'].get('sqlite_errorcode') == sqlite3.SQLITE_BUSY for r in failures))
        self.assertTrue(all(r['contender'] == 'unknown' for r in failures))
        self.assertTrue(any(r['context'] and r['context']['close'] for r in failures))
        self.assertTrue(self.events(Stage.FAILURE))
        self.assertTrue(self.events(Stage.CLEANUP))
        self.assertEqual(self.host.sends, [])

    def test_cancel_logs_end_without_claiming_thread_exit_in_target(self):
        self.enable_logs()
        result = self.start()
        with self.scope():
            self.call('direct_cancel', {'probe_id': result['state']['probe_id']})
        self.joined()
        self.assertIn('cancelled', [r['outcome'] for r in self.events(Stage.CONTROL_END)])
        self.assertTrue(all(r['outcome'] != 'confirmed' for r in self.events(Stage.WORKER)))

    def test_join_save_failure_does_not_erase_actual_exit(self):
        self.enable_logs()
        self.start()
        with mock.patch.object(self.direct.repository, 'confirm_worker_stopped', side_effect=OSError(SECRET)):
            self.ended()
            self.joined()
        self.assertEqual(self.direct._worker_stop_persistence, 'failed')
        self.assertIn('confirmed', [r['outcome'] for r in self.events(Stage.JOIN)])
        self.assertIn('failed', [r['outcome'] for r in self.events(Stage.EXIT_SAVE)])
        self.assertIsNone(self.direct.repository.read().worker_stopped_at)

    def timer_settlement_failure(self, action):
        self.enable_logs()
        update = self.direct.repository.update
        phase = {'arm': 'active', 'delete': 'deleted', 'pause': 'paused'}[action]
        def fail_settlement(*args, **kwargs):
            if kwargs.get('timer_phase') == phase:
                raise OSError(SECRET)
            return update(*args, **kwargs)
        with mock.patch.object(threading.Thread, 'start'):
            if action != 'arm':
                self.start()
                self.ended()
            with self.scope(), mock.patch.object(self.direct.repository, 'update', side_effect=fail_settlement):
                if action == 'arm':
                    result = self.call('direct_delete_start')
                    self.assertEqual(result['state']['status'], 'suppressed')
                else:
                    self.direct._bind_diagnostics()
                    with self.assertRaises(Exception):
                        if action == 'delete':
                            self.direct._dispatch(self.host, self.direct.repository.read(), time.monotonic() + 5)
                        else:
                            self.direct._cleanup(self.host)
        stage = {'arm': Stage.TIMER_ARM, 'delete': Stage.TIMER_DELETE, 'pause': Stage.TIMER_PAUSE}[action]
        self.assertEqual([r['outcome'] for r in self.events(stage)], ['attempt', 'confirmed'])
        self.assertEqual([r['reason'] for r in self.events(Stage.TIMER_SAVE)], [action + '_save_failed'])
        self.assertEqual(self.direct.repository.read().timer_phase, 'deleting' if action == 'delete' else 'unknown')
        self.assertEqual(self.host.sends, [])
        self.assertEqual(self.host.effects.count(action), 1)
        self.assertNotIn(SECRET, json.dumps(self.rows))

    def test_arm_confirmation_precedes_failed_state_settlement(self):
        self.timer_settlement_failure('arm')

    def test_delete_confirmation_precedes_failed_state_settlement(self):
        self.timer_settlement_failure('delete')

    def test_pause_confirmation_precedes_failed_state_settlement(self):
        self.timer_settlement_failure('pause')

    def final_guard(self, *, cancel):
        self.enable_logs(validated=False)
        dispatch = self.direct._dispatch
        def before_send(*args):
            if cancel:
                self.direct._stop.set()
            else:
                self.now += timedelta(seconds=1201)
        def guarded_dispatch(*args):
            # Isolate the last effect guard after the preceding observations.
            with mock.patch.object(self.direct, '_idle_and_ended', return_value=True), \
                 mock.patch.object(self.direct, '_halt_wait', return_value=False), \
                 mock.patch.object(self.direct, '_before_send', side_effect=before_send):
                return dispatch(*args)
        with mock.patch.object(self.direct, '_dispatch', side_effect=guarded_dispatch):
            self.start(version=1)
            self.ended()
            self.joined()
        self.assertEqual(self.host.sends, [])
        self.assertEqual(self.events(Stage.NOTIFICATION), [])
        reasons = [r['reason'] for r in self.events(Stage.CONTROL_END)]
        self.assertNotIn('dispatched', reasons)
        self.assertIn('cancel_requested' if cancel else 'deadline', reasons)
        if not cancel:
            self.assertEqual(self.status(), 'expired')

    def test_final_cancellation_does_not_claim_dispatch(self):
        self.final_guard(cancel=True)

    def test_final_expiry_does_not_claim_dispatch(self):
        self.final_guard(cancel=False)

    def test_owned_worker_early_cancel_is_logged_without_fresh_diagnostic_reads(self):
        self.enable_logs(validated=False)
        self.direct._stop.set()
        self.start()
        self.joined()
        self.assertIn('cancel_requested', [r['reason'] for r in self.events(Stage.CONTROL_END)])
        self.assertIn('returned', [r['outcome'] for r in self.events(Stage.WORKER)])
        self.assertEqual(self.host.sends, [])

    def test_unowned_cleanup_does_not_admit_logging(self):
        self.enable_logs(validated=False)
        with self.scope():
            self.assertTrue(self.direct.close()['ok'])
        self.assertEqual(self.rows, [])

    def test_log_failure_changes_no_effect_or_primary_result(self):
        self.enable_logs()
        with mock.patch.object(log.Session, 'emit', side_effect=OSError(SECRET)), \
             mock.patch.object(log.Session, 'checkpoint', side_effect=OSError(SECRET)):
            self.start()
            self.ended()
            self.joined()
        self.assertEqual(self.status(), 'accepted')
        self.assertEqual(self.host.effects, ['arm', 'delete'])
        self.assertEqual(len(self.host.sends), 1)

    def test_managed_receipt_deadline_cancellation_and_save_failure(self):
        self.enable_logs(kind=ManagedDirectProbe)
        self.direct._send_parent_turn = META['turnId']
        record = SimpleNamespace(probe_id=IDENTITY[2], worker_id=IDENTITY[3])
        current = SimpleNamespace(status='accepted', acknowledged_turn=None)
        self.host.observe_receipt = mock.Mock(return_value=NEW_TURN)
        from task_governance_tool.review_wait_runtime import managed_direct
        cases = [('deadline', 'deadline'), ('cancel', 'cancel_requested'),
                 ('save', 'receipt_save_failed')]
        for case, reason in cases:
            with self.subTest(case=case), self.scope(), \
                 mock.patch.object(DirectProbe, '_run'), \
                 mock.patch.object(self.direct.repository, 'read', return_value=current), \
                 mock.patch.object(self.direct.repository, 'update', side_effect=OSError(SECRET)), \
                 mock.patch.object(self.direct, '_expired', return_value=False):
                diag.bind(*IDENTITY)
                self.direct._stop.clear()
                if case == 'cancel':
                    self.direct._stop.set()
                if case == 'deadline':
                    with mock.patch.object(managed_direct, 'time',
                            SimpleNamespace(monotonic=mock.Mock(side_effect=[0, 121]))):
                        self.direct._run(self.host, record, 200)
                else:
                    self.direct._run(self.host, record, time.monotonic() + 100)
            self.assertIn(reason, [r['reason'] for r in self.events(Stage.RECEIPT_END)])
        self.assertEqual(self.host.sends, [])


class ManagedLogTests(unittest.TestCase):
    setUp = managed_fixture.ManagedFlowTests.setUp
    close_all = managed_fixture.ManagedFlowTests.close_all
    make = managed_fixture.ManagedFlowTests.make
    factory = managed_fixture.ManagedFlowTests.factory
    call = managed_fixture.ManagedFlowTests.call
    until = managed_fixture.ManagedFlowTests.until
    ended = managed_fixture.ManagedFlowTests.ended

    def test_failed_new_creation_cannot_reuse_previous_worker_identity(self):
        rows = self.logging()
        self.assertTrue(self.call()['ok'])
        old = next(iter(self.session.sessions.values())).direct._diagnostic_identity
        self.current_parent = ChildTurn(PARENT, NEW_TURN, 'inProgress', 'active')
        self.fail_create = True
        result = self.call(metadata={**managed_fixture.META, 'turnId': NEW_TURN})
        self.assertFalse(result['ok'])
        failures = [r for r in rows if r.get('stage') == 'request' and r.get('reason') == 'heartbeat_create_unknown']
        self.assertTrue(failures)
        for row in failures:
            self.assertEqual(row['request_sequence'], 2)
            self.assertEqual([row[key] for key in ('wait_id', 'probe_id', 'worker_id')], [None, None, None])
        self.assertTrue(any(r.get('worker_id') == old[3] for r in rows))

    def test_cleanup_delete_confirmation_survives_failed_journal_close(self):
        from task_governance_tool.review_wait_runtime.request_repository import RequestRepository
        rows = self.logging()
        self.assertTrue(self.call()['ok'])
        original = RequestRepository.advance
        def fail_close(repository, lease, phase, **kwargs):
            if phase == 'closed':
                raise OSError(SECRET)
            return original(repository, lease, phase, **kwargs)
        with mock.patch.object(RequestRepository, 'advance', fail_close):
            result = self.call('stop')
        self.assertFalse(result['ok'])
        self.assertEqual(self.effects.count('delete'), 1)
        events = [r for r in rows if r.get('stage') in ('timer_delete', 'timer_save')]
        self.assertEqual([(r['stage'], r['outcome']) for r in events],
            [('timer_delete', 'attempt'), ('timer_delete', 'confirmed'), ('timer_save', 'failed')])
        self.assertEqual(events[-1]['reason'], 'delete_save_failed')

    def test_legacy_prepare_admitted_failure_has_task_but_no_fabricated_worker(self):
        from task_governance_tool.review_wait_runtime.failure_diagnostics import DiagnosticError
        rows = self.logging()
        def location(**kwargs):
            log.allow_output()
            return self.paths
        with mock.patch.object(self.session, '_location', side_effect=location), \
             mock.patch.object(self.session, '_reader', side_effect=DiagnosticError('binding_mismatch')):
            result = self.session.handle('prepare', {'task_id': managed_fixture.TASK,
                'automation_id': 'fixture', 'reviewer_ids': [CHILD]}, managed_fixture.META)
        self.assertFalse(result['ok'])
        failures = [r for r in rows if r.get('stage') == 'request']
        self.assertEqual(failures[-1]['reason'], 'binding_mismatch')
        self.assertEqual(failures[-1]['boundary'], 'basis_before')
        self.assertEqual(failures[-1]['task_id'], managed_fixture.TASK)
        self.assertIsNone(failures[-1]['worker_id'])
        self.assertIsNone(failures[-1]['request_sequence'])
        self.assertEqual(self.effects, [])

    def test_initial_host_failure_survives_journal_settlement_failure(self):
        from task_governance_tool.review_wait_runtime.request_repository import RequestRepository
        rows = self.logging()
        self.fail_create = True
        with mock.patch.object(RequestRepository, 'advance', side_effect=OSError(SECRET)):
            result = self.call()
        self.assertFalse(result['ok'])
        failures = [r for r in rows if r.get('stage') == 'request']
        self.assertEqual(failures[0]['reason'], 'heartbeat_create_unknown')
        self.assertEqual(failures[0]['host_mutation'], 'may_have_occurred')
        self.assertEqual(failures[0]['local_state'], 'may_have_changed')
        self.assertEqual(result['diagnostic']['reason'], 'heartbeat_create_unknown')
        self.assertEqual(self.effects, ['create'])
        self.assertNotIn(SECRET, json.dumps(rows))

    def test_preworker_failure_retains_old_attempt_and_protected_call_state(self):
        from task_governance_tool.review_wait_runtime.request_repository import RequestRepository
        rows = self.logging()
        self.fail_create = True
        original = RequestRepository.advance
        def saturated(repository, *args, **kwargs):
            session = log._current.get()
            if session is not None:
                for _ in range(400):
                    session.emit('test.detail', {}, failed=True)
            return original(repository, *args, **kwargs)
        with mock.patch.object(RequestRepository, 'advance', saturated):
            first = self.call()
        self.assertFalse(first['ok'])
        attempts = [r for r in rows if r.get('stage') == 'request']
        self.assertTrue(attempts)
        self.assertEqual(attempts[0]['boundary'], 'create')
        self.assertEqual(attempts[0]['reason'], 'heartbeat_create_unknown')
        self.assertEqual(attempts[0]['request_sequence'], 1)
        self.assertEqual(attempts[0]['task_id'], managed_fixture.TASK)
        self.assertEqual(attempts[0]['parent_id'], PARENT)
        self.assertIsNone(attempts[0]['worker_id'])
        self.assertEqual(attempts[0]['host_mutation'], 'may_have_occurred')
        self.fail_create = False
        self.current_parent = ChildTurn(PARENT, NEW_TURN, 'inProgress', 'active')
        self.basis = replace(self.basis, target_generation=self.basis.target_generation + 1)
        with mock.patch('task_governance_tool.review_wait_runtime.continuation_cleanup_repository.observe_generation_cleanup',
                return_value=SimpleNamespace(state='unknown', reason='review_continuation_effect_unknown')):
            second = self.call(metadata={**managed_fixture.META, 'turnId': NEW_TURN})
        self.assertFalse(second['ok'])
        retained = [r for r in rows if r.get('reason') == 'review_continuation_effect_unknown']
        self.assertTrue(retained)
        self.assertEqual(retained[-1]['request_sequence'], 1)
        self.assertEqual(retained[-1]['retained_effects'], 'not_inspected')
        self.assertEqual(self.effects, ['create'])

    def test_primary_preparation_cause_survives_secondary_cleanup_failure(self):
        from task_governance_tool.review_wait_runtime.failure_diagnostics import DiagnosticError
        rows = self.logging()
        factory = self.session.managed._host
        def host(*args, **kwargs):
            value = factory(*args, **kwargs)
            value.verify_created = mock.Mock(side_effect=DiagnosticError('preparation_failed'))
            value.delete_heartbeat = mock.Mock(side_effect=OSError(SECRET))
            return value
        with mock.patch.object(self.session.managed, '_host', side_effect=host):
            result = self.call()
        self.assertFalse(result['ok'])
        events = [r for r in rows if r.get('stage') == 'request']
        self.assertEqual(events[0]['reason'], 'preparation_failed')
        self.assertTrue(any(r['cause'] == 'secondary' and r['boundary'] == 'cleanup' for r in events))
        self.assertEqual(result['diagnostic']['reason'], 'preparation_failed')
        self.assertEqual(result['diagnostic']['cleanup'], 'unresolved')
        self.assertNotIn(SECRET, json.dumps(rows))

    def logging(self):
        rows = []
        class Sink:
            directory = self.root
            root = self.root
            marker = b'fixture'
            identity = None
            mode = 'debug'
            def write(self, day, data):
                rows.extend(json.loads(line) for line in data.splitlines())
                return 'written'
        self.enterContext(mock.patch.object(log, 'admit', return_value=Sink()))
        self.session._worker_scope = lambda **kw: log.operation_scope(self.root, entrypoint=self.root,
            operation=kw.get('operation', 'review_wait_worker'), independent=True)
        return rows

    def test_managed_receipt_and_eof_service_are_separate(self):
        rows = self.logging()
        self.assertTrue(self.call()['ok'])
        direct = next(iter(self.session.sessions.values())).direct
        self.ended()
        direct._worker.join(6)
        direct._termination_observer.join(6)
        self.assertFalse(direct._worker.is_alive())
        self.assertTrue(any(r.get('stage') == 'receipt_end' and r['reason'] == 'receipt_seen' for r in rows))
        self.assertEqual(serve(io.BytesIO(), io.BytesIO(), self.session), 0)
        self.assertTrue(any(r.get('stage') == 'service_exit' and r['reason'] == 'eof' for r in rows))
        self.assertEqual(self.effects.count('send'), 1)

    def test_healthy_renewal_keeps_new_timer_identity(self):
        rows = self.logging()
        self.assertTrue(self.call()['ok'])
        first = next(iter(self.session.sessions.values())).direct
        old_identity = first._diagnostic_identity
        self.current_parent = ChildTurn(PARENT, NEW_TURN, 'inProgress', 'active')
        self.assertTrue(self.call(metadata={**managed_fixture.META, 'turnId': NEW_TURN})['ok'])
        direct = next(s.direct for s in self.session.sessions.values()
                      if s.direct._diagnostic_identity != old_identity)
        identity = direct._diagnostic_identity
        self.assertNotEqual(identity[1:3], old_identity[1:3])
        arm = [r for r in rows if r.get('stage') == 'timer_arm']
        self.assertEqual([(r['wait_id'], r['probe_id'], r['worker_id']) for r in arm],
                         [old_identity[1:]] * 2 + [identity[1:]] * 2)
        self.assertEqual(self.effects, ['create', 'arm', 'pause', 'delete', 'create', 'arm'])

    def test_failure_notification_keeps_confirmed_delete_when_save_fails(self):
        rows = self.logging()
        release = threading.Event()
        self.addCleanup(release.set)
        self.enterContext(mock.patch.object(ManagedDirectProbe, '_run', side_effect=lambda *args: release.wait(10)))
        self.assertTrue(self.call()['ok'])
        direct = next(iter(self.session.sessions.values())).direct
        host = direct._host
        self.ended()
        update = direct.repository.update
        def fail_settlement(*args, **kwargs):
            if kwargs.get('timer_phase') == 'deleted':
                raise OSError(SECRET)
            return update(*args, **kwargs)
        with self.session._worker_scope(), \
             mock.patch.object(host, 'set_wait_failure', create=True), \
             mock.patch.object(direct.repository, 'update', side_effect=fail_settlement):
            log.allow_output()
            direct._bind_diagnostics()
            self.assertFalse(direct._on_failure(host, direct.repository.read(),
                HostAdapterError('child_status_unavailable'), time.monotonic() + 5))
        self.assertEqual([r['outcome'] for r in rows if r.get('stage') == 'timer_delete'],
                         ['attempt', 'confirmed'])
        self.assertEqual([r['reason'] for r in rows if r.get('stage') == 'timer_save'], ['delete_save_failed'])
        self.assertEqual(direct.repository.read().timer_phase, 'deleting')
        self.assertEqual(self.effects, ['create', 'arm', 'delete'])
        self.assertNotIn(SECRET, json.dumps(rows))

    def transport_failure(self, *, malformed=False, cleanup_failure=False):
        rows = self.logging()
        self.assertTrue(self.call()['ok'])
        session = next(iter(self.session.sessions.values()))
        class BrokenInput:
            def readline(self, limit):
                raise OSError(SECRET)
        class BrokenOutput:
            def write(self, value):
                raise OSError(SECRET)
        stdin = io.BytesIO(b'not-json\n') if malformed else BrokenInput()
        stdout = BrokenOutput() if malformed else io.BytesIO()
        if cleanup_failure:
            with mock.patch.object(session, 'close', side_effect=ValueError(SECRET)):
                self.assertEqual(serve(stdin, stdout, self.session), 1)
        else:
            self.assertEqual(serve(stdin, stdout, self.session), 1)
        errors = [r for r in rows if r.get('stage') == 'transport']
        self.assertEqual([r['reason'] for r in errors],
                         ['protocol_error', 'transport_error'] if malformed else ['transport_error'])
        self.assertTrue(all('exception' in r for r in errors))
        self.assertEqual([r['exception']['kind'] for r in errors],
                         ['Exception', 'OSError'] if malformed else ['OSError'])
        exits = [r for r in rows if r.get('stage') == 'service_exit']
        self.assertTrue(exits)
        self.assertTrue(all(r['reason'] == ('protocol_error' if malformed else 'transport_error') for r in exits))
        if cleanup_failure:
            cleanup = [r for r in rows if r.get('stage') == 'cleanup' and r['outcome'] == 'failed']
            self.assertTrue(cleanup)
            self.assertEqual(cleanup[0]['reason'], 'cleanup_failed')
            self.assertEqual(cleanup[0]['exception']['kind'], 'ValueError')
            self.assertLess(rows.index(errors[0]), rows.index(cleanup[0]))
        self.assertNotIn(SECRET, json.dumps(rows))

    def test_transport_cause_survives_successful_cleanup(self):
        self.transport_failure()

    def test_transport_cause_survives_cleanup_failure(self):
        self.transport_failure(cleanup_failure=True)

    def test_protocol_cause_survives_failed_error_reply(self):
        self.transport_failure(malformed=True)

    def test_protocol_and_transport_causes_survive_cleanup_failure(self):
        self.transport_failure(malformed=True, cleanup_failure=True)

    def test_request_error_survives_failed_error_reply(self):
        from tests import test_review_wait_server as server_fixture
        rows = self.logging()
        self.assertTrue(self.call()['ok'])
        service = server_fixture.Service()
        service.failure = SECRET
        service.record_transport_failure = self.session.record_transport_failure
        service.close_for_transport = self.session.close_for_transport
        class BrokenReply(io.BytesIO):
            def write(self, value):
                if self.tell():
                    raise OSError(SECRET)
                return super().write(value)
        data = server_fixture.HELLO + server_fixture.READY + server_fixture.request('tools/call',
            {'name': 'review_wait_view', 'arguments': {}, '_meta': {}})
        self.assertEqual(serve(io.BytesIO(data), BrokenReply(), service), 1)
        errors = [r for r in rows if r.get('stage') == 'transport']
        self.assertEqual([r['reason'] for r in errors], ['request_error', 'transport_error'])
        self.assertEqual([r['exception']['kind'] for r in errors], ['ValueError', 'OSError'])
        self.assertTrue(all(r['reason'] == 'request_error' for r in rows if r.get('stage') == 'service_exit'))
        self.assertNotIn(SECRET, json.dumps(rows))

    def test_transport_logger_failure_does_not_change_protocol_exit_or_cleanup(self):
        from tests import test_review_wait_server as server_fixture
        service = server_fixture.Service()
        service.record_transport_failure = mock.Mock(side_effect=AssertionError(SECRET))
        self.assertEqual(serve(io.BytesIO(b'not-json\n'), io.BytesIO(), service), 1)
        self.assertEqual(service.closed, 1)
        service.record_transport_failure.assert_called_once()

    def test_development_entry_cleanup_keeps_direct_module_execution_supported(self):
        import runpy
        from task_governance_tool.review_wait_runtime import review_wait_server as server
        from task_governance_tool.review_wait_runtime.review_wait_service import ServiceConfig
        namespace = runpy.run_path(server.__file__, run_name='isolated_development_entry')
        config = namespace['SessionConfig'](
            ServiceConfig(self.root / 'wait.sqlite', self.root / 'server.mjs', self.root, 'UTC', 'host_node_intl'),
            self.root, managed_fixture.TASK, 'isolated-automation')
        session = namespace['ReviewWaitSession'](config)
        self.assertEqual(session.close(), {'ok': True})

    def test_transport_logging_preserves_off_and_readonly_and_owned_boundary(self):
        rows = self.logging()
        self.session.record_transport_failure('transport_error', OSError(SECRET))
        self.assertEqual(rows, [])
        self.assertTrue(self.call()['ok'])
        before = len(rows)
        with log.sqlite.suppress_diagnostics():
            self.session.record_transport_failure('transport_error', OSError(SECRET))
        self.assertEqual(len(rows), before)
        with mock.patch.object(log, 'admit', return_value=None), \
             mock.patch.object(log.projection, 'exception', side_effect=AssertionError('OFF projection')):
            self.session.record_transport_failure('transport_error', OSError(SECRET))
        self.assertEqual(len(rows), before)

    def test_read_failure_survives_observation_failure_in_both_paths(self):
        rows = self.logging()
        from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
        expected = SimpleNamespace(reviewer_id=CHILD, turn_id=TURN)
        controller = SimpleNamespace(reviewers=[expected])
        for kind in (DirectProbe, ManagedDirectProbe):
            with self.subTest(kind=kind.__name__):
                probe = kind(self.root / 'unused.sqlite', lambda: BINDING,
                             lambda metadata: None, lambda: self.now)
                original = HostAdapterError('child_status_unavailable')
                second = RepositoryError('state_unreadable')
                host = SimpleNamespace(read_child=mock.Mock(side_effect=original))
                with self.session._worker_scope() as session, \
                     mock.patch.object(probe, '_observe_reviewer', side_effect=second) as observe, \
                     mock.patch.object(probe, '_halt_observation', return_value=False):
                    log.allow_output()
                    diag.bind(*IDENTITY)
                    if kind is ManagedDirectProbe:
                        probe.polling_factory = object()
                        probe._polling_sources = (None, lambda: {}, None)
                    with self.assertRaises(RepositoryError) as raised:
                        if kind is DirectProbe:
                            probe._read_reviewer(host, controller, expected)
                        else:
                            probe._observe_progress(host, controller, None, None, time.monotonic() + 5)
                    self.assertIs(raised.exception, second)
                    observe.assert_called_once()
                    host.read_child.assert_called_once_with(CHILD)
                    invocation = session.invocation_id
                failures = [r for r in rows if r['invocation_id'] == invocation
                            and r.get('stage') == 'failure']
                self.assertEqual([r['reason'] for r in failures], ['child_status_unavailable'])
                self.assertNotIn(SECRET, json.dumps(failures))


    def test_managed_receipt_failure_preserves_send_acceptance(self):
        rows = self.logging()
        self.fail_receipt = True
        self.assertTrue(self.call()['ok'])
        direct = next(iter(self.session.sessions.values())).direct
        self.ended()
        direct._worker.join(6)
        direct._termination_observer.join(6)
        self.assertEqual(direct.repository.read().status, 'accepted')
        self.assertIsNone(direct.repository.read().acknowledged_turn)
        self.assertTrue(any(r.get('stage') == 'receipt_end' and r['reason'] == 'read_failed' for r in rows))
        self.assertEqual(self.effects.count('send'), 1)

    def eof_join_failure(self, *, raises):
        rows = self.logging()
        entered, release = threading.Event(), threading.Event()
        def held_worker(*args):
            entered.set()
            release.wait(10)
        with mock.patch.object(ManagedDirectProbe, '_run', side_effect=held_worker):
            self.assertTrue(self.call()['ok'])
            direct = next(iter(self.session.sessions.values())).direct
            try:
                self.assertTrue(entered.wait(2))
                with mock.patch.object(direct._worker, 'join',
                        side_effect=OSError(SECRET) if raises else None) as join, \
                     mock.patch.object(direct.repository, 'read') as read, \
                     mock.patch.object(direct.repository, 'exists') as exists:
                    self.assertEqual(self.session.close_for_transport('eof'),
                                     {'ok': False, 'error': 'observer_cleanup_unknown'})
                    join.assert_called_once_with(direct._join)
                    read.assert_not_called()
                    exists.assert_not_called()
                cleanup = [r for r in rows if r['operation'] == 'review_wait_cleanup']
                self.assertTrue(any(r.get('stage') == 'service_exit' and r['reason'] == 'eof'
                                    and r['outcome'] == 'unknown' for r in cleanup))
                self.assertTrue(any(r.get('stage') == 'join' and
                    r['reason'] == ('join_failed' if raises else 'join_timeout') for r in cleanup))
                self.assertFalse(any(r.get('stage') == 'join' and r['outcome'] == 'confirmed'
                                     for r in cleanup))
                self.assertFalse(any(r.get('stage') == 'exit_save' for r in cleanup))
                self.assertNotIn(SECRET, json.dumps(cleanup))
            finally:
                release.set()
                direct._worker.join(5)
                direct._termination_observer.join(5)

    def test_eof_timeout_logs_in_unvalidated_scope_without_additional_reads(self):
        self.eof_join_failure(raises=False)

    def test_eof_join_exception_logs_in_unvalidated_scope_without_additional_reads(self):
        self.eof_join_failure(raises=True)


class InvarianceTests(unittest.TestCase):
    def test_same_synchronous_wait_off_debug_errors_and_broken_sink(self):
        from task_governance_tool.review_wait_runtime import review_wait_repository as repository
        measurements = []
        expected = None
        for mode in ('off', 'debug', 'errors', 'broken'):
            fixture = direct_fixture.DirectProbeTests(methodName='runTest')
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            emitted = []
            class Sink:
                directory = fixture.path.parent
                root = fixture.path.parent
                marker = b'fixture'
                identity = None
                def write(self, day, data):
                    if mode == 'broken':
                        raise OSError(SECRET)
                    emitted.append(data)
                    return 'written'
            sink = Sink()
            sink.mode = 'errors' if mode == 'errors' else 'debug'
            scope = lambda: log.operation_scope(fixture.path.parent, entrypoint=fixture.path,
                operation='review_wait_worker', validated=True, independent=True)
            direct = fixture.direct
            direct._diagnostic_scope = scope
            sql_calls = [0]
            original_connect = repository.connect_traced
            def count_sql(_statement):
                sql_calls[0] += 1  # Count only; never retain SQL or parameter text.
            def open_connection(*args, **kwargs):
                connection = original_connect(*args, **kwargs)
                connection.set_trace_callback(count_sql)
                return connection
            with mock.patch.object(log, 'admit', return_value=None if mode == 'off' else sink), \
                 mock.patch.object(log.sqlite, '_admit', return_value=None), \
                 mock.patch.object(threading.Thread, 'start'), \
                 mock.patch.object(repository, 'connect_traced', side_effect=open_connection) as connect:
                begin = time.perf_counter_ns()
                with scope():
                    result = direct.handle('direct_delete_start', {}, META)
                self.assertTrue(result['ok'], result)
                fixture.ended()
                record = direct.repository.read()
                direct._run_scoped(fixture.host, record, time.monotonic() + 100)
                elapsed = time.perf_counter_ns() - begin
                state = direct.repository.read()
                observed = (state.status, state.timer_phase, tuple(fixture.host.effects),
                            len(fixture.host.sends), connect.call_count, state.deadline, sql_calls[0])
            if expected is None:
                expected = observed
            self.assertEqual(observed, expected, mode)
            byte_count = sum(map(len, emitted))
            self.assertLess(byte_count, 512 * 1024)
            self.assertLess(elapsed, 5_000_000_000)
            measurements.append({'mode': mode, 'elapsed_ns': elapsed, 'bytes': byte_count,
                                 'connections': observed[4], 'sends': observed[3], 'sql_calls': observed[6]})
        print('WAIT_LOG_MEASUREMENT ' + json.dumps(measurements, separators=(',', ':')))
