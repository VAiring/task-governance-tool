"""Wait diagnostics: isolated workers, safe checkpoints and primary invariance."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import io
import json
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
