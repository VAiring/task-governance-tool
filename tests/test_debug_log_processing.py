"""Real processing counts, bounded output and OFF/ON semantic equivalence."""
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import errno
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from tests import test_debug_log as fixture
from tests.test_debug_log_metrics import source_function
from tests import test_viewer_maintenance as viewer_fixture
from tests.test_usage_collection import THREAD, event, usage
from task_governance_tool import debug_log as log, debug_log_metrics as metrics
from task_governance_tool import artifact_lock, evidence_publication, viewer_maintenance
from task_governance_tool import state_paths, usage_sources, usage_incremental_adapter as adapter
from task_governance_tool.evidence_projection import canonical_json_bytes
from task_governance_tool.storage import connect_initialized, connect_initialized_readonly
from task_governance_tool.tasks import add_tasks, list_tasks
from task_governance_tool.viewer import build_viewer_snapshot
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_ingest_work import IngestWork


class ProcessingLogTests(unittest.TestCase):
    setUp = fixture.DebugLogTests.setUp
    enable = fixture.DebugLogTests.enable
    configure = fixture.DebugLogTests.configure
    scope = fixture.DebugLogTests.scope
    records = fixture.DebugLogTests.records

    def groups(self):
        return [row for row in self.records() if row['event'] == 'performance.aggregate']

    def test_units_and_closed_states_are_bounded_without_value_projection(self):
        class Hostile:
            def __str__(self):
                raise AssertionError('converted diagnostic value')
            __repr__ = __str__
            def __hash__(self):
                raise AssertionError('hashed diagnostic value')

        function = source_function()
        self.configure()
        with self.scope():
            for unit in ('items', 'bytes', Hostile()):
                with log.aggregate(function, unit=unit) as work:
                    work.count(3)
                    for state in ('reused', 'reused', 'private-label', Hostile()):
                        work.mark(state)
        groups = self.groups()
        self.assertEqual({row['count_unit'] for row in groups}, {'items', 'bytes', 'unknown'})
        self.assertTrue(all(row['states'] == {'reused': 1} for row in groups))
        self.assertNotIn('private-label', json.dumps(self.records()))
        with log.aggregate(function, unit=Hostile()) as off:
            off.mark(Hostile())
            self.assertIsNone(off.states)

    def test_repeated_files_and_json_count_actual_bytes_without_log_flood(self):
        self.configure()
        path = self.root / 'private-source.bin'
        data = b'private-file-body' * 31
        path.write_bytes(data)
        with self.scope():
            for _ in range(300):
                self.assertEqual(state_paths.read_physical_file_bounded(
                    path, root=self.root, max_bytes=4096)[0], data)
                state_paths.hash_physical_file(path, root=self.root)
                self.assertEqual(canonical_json_bytes({'private-key': None}), b'{"private-key":null}')
            output = self.root / 'output.bin'
            descriptor = os.open(output, os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                state_paths._write_all(descriptor, data)
            finally:
                os.close(descriptor)
        groups = {row['method']['function']: row for row in self.groups()}
        for name in ('read_physical_file_bounded', 'hash_physical_file'):
            self.assertEqual((groups[name]['calls'], groups[name]['item_count']), (300, 300 * len(data)))
        self.assertEqual(groups['_write_all']['item_count'], len(data))
        self.assertEqual(groups['canonical_json_bytes']['calls'], 300)
        self.assertLessEqual(len(self.records()), 8)
        self.assertNotIn('private-', json.dumps(self.records()))

    def test_task_rows_and_sql_are_identical_at_multiple_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = viewer_fixture.make_target(Path(directory), enabled=False)
            populated = 0
            for size in (1, 30, 300):
                with closing(connect_initialized(target)) as connection:
                    while populated < size:
                        batch = min(size - populated, 64)
                        add_tasks(connection, target.project, database_target=target,
                                  task_inputs=[{'title': 'Private task title'} for _ in range(batch)])
                        connection.commit()
                        populated += batch
                results, queries = [], []
                for enabled in (False, True):
                    marker = self.configure() / 'enabled'
                    if not enabled:
                        marker.unlink()
                        self.marker.unlink(missing_ok=True)
                    statements = []
                    with self.scope(), closing(connect_initialized_readonly(target)) as connection:
                        connection.set_trace_callback(statements.append)
                        results.append((list_tasks(connection, target.project, limit=size),
                                        build_viewer_snapshot(connection, target,
                                            generated_at=viewer_fixture.FIXED_TIME)))
                    # Task and Viewer reads own random savepoint names; compare every SQL call
                    # including its order while normalizing only that nonce.
                    queries.append([re.sub(r'\b(taskgov_(?:viewer_batch|review_material|selected)_)[0-9a-f]+\b',
                                           r'\1NONCE', sql) for sql in statements])
                self.assertEqual(results[0], results[1])
                self.assertEqual(queries[0], queries[1])
                self.assertEqual(results[1][1].task_count, size)
                latest = [row for row in self.groups()
                          if row['method']['function'] == 'validate_stored_task_rows'][-1]
                self.assertGreaterEqual(latest['item_count'], size)
        self.assertNotIn('Private task title', json.dumps(self.records()))

    def test_usage_slice_counts_and_deadline_checks_preserve_result(self):
        source = SourceInput(THREAD, self.root / 'private-transcript.jsonl', self.root, self.root)
        for size in (1, 30, 300):
            payload = [event('session_meta', id=THREAD, cwd=str(self.root), model_provider='openai')]
            payload.extend(usage(f'response-{index}') for index in range(size))
            source.path.write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in payload))
            results, clocks = [], []
            for enabled in (False, True):
                marker = self.configure() / 'enabled'
                if not enabled:
                    marker.unlink()
                    self.marker.unlink(missing_ok=True)
                with self.scope(), mock.patch.object(adapter, 'monotonic', return_value=0) as clock:
                    results.append(adapter.scan(source, adapter.ScanState(), {}, deadline=1))
                    clocks.append(clock.call_count)
            self.assertEqual(results[0], results[1])
            self.assertEqual(clocks[0], clocks[1])
            observed = [row for row in self.groups() if row['method']['function'] == 'scan'][-1]
            self.assertEqual(observed['item_count'], results[1].bytes_read)
        with self.scope():
            adapter.scan(source, adapter.ScanState(), {}, deadline=-1)
            adapter.scan(source, adapter.ScanState(), {}, max_records=0)
        observed = [row for row in self.groups() if row['method']['function'] == 'scan'][-1]
        self.assertEqual(observed['states'], {'deadline': 1, 'limit': 1})
        text = json.dumps(self.records())
        self.assertNotIn('private-transcript', text)
        self.assertNotIn('response-299', text)

    def test_discovery_counts_resume_skips_without_second_enumeration(self):
        directory = self.root / 'inventory'
        directory.mkdir()
        for index in range(30):
            (directory / f'{index}.txt').touch()
        position = {'root': 0, 'stack': [[[], 20]]}
        self.configure()
        with self.scope(), mock.patch.object(usage_sources.os, 'scandir', wraps=os.scandir) as scan:
            _, _, complete = usage_sources.locate_slice(set(), [directory], self.root,
                                                        position, deadline=float('inf'))
            self.assertEqual(scan.call_count, 1)
        self.assertTrue(complete)
        group = next(row for row in self.groups() if row['method']['function'] == 'locate_slice')
        self.assertEqual(group['item_count'], 30)

    def test_ingest_reuse_and_limit_keep_original_attempt_budget(self):
        repository = mock.Mock()
        repository.collect_source.return_value = {'replay': True}
        sources = {'one': SimpleNamespace(thread_id=THREAD), 'two': SimpleNamespace(thread_id=THREAD)}
        self.configure()
        clock = mock.Mock(return_value=0)
        work = IngestWork(repository, sources, THREAD, 1, limit=1, current_limit=1, clock=clock)
        with self.scope():
            work.run(sources, list(sources), None)
        self.assertEqual(repository.collect_source.call_count, 2)  # one audit and one ingest
        self.assertEqual(clock.call_count, 2)
        group = next(row for row in self.groups() if row['method']['function'] == 'run')
        self.assertEqual(group['item_count'], 1)
        self.assertEqual(group['states'], {'reused': 1, 'revalidated': 1, 'limit': 1})

    def test_publication_current_and_lock_contention_keep_primary_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = viewer_fixture.make_target(Path(directory), enabled=True)
            self.configure()
            with self.scope():
                first = viewer_maintenance.publish_setup_viewer(target)
                self.assertEqual(first.code, 'succeeded')
                self.assertEqual(viewer_maintenance.run_routine_viewer_refresh(target).code, 'current')
                self.assertEqual(evidence_publication.publish_setup_evidence_projection(target).code, 'succeeded')
                self.assertEqual(evidence_publication.run_routine_evidence_projection(target).code, 'current')
                with mock.patch.object(artifact_lock, '_acquire', side_effect=OSError(errno.EACCES, 'secret')):
                    self.assertEqual(viewer_maintenance.publish_setup_viewer(target).code, 'deferred')
                self.assertEqual(self.records(), [])  # writes remain deferred beyond every DB close
            groups = {row['method']['function']: row for row in self.groups()}
            self.assertEqual(groups['_refresh']['states'], {'published': 1, 'unchanged': 1, 'contended': 1})
            self.assertEqual(groups['_publish_evidence_projection']['states'], {'published': 1, 'unchanged': 1})
            self.assertLessEqual(len(self.records()), log.MAX_EVENTS)
            self.assertLessEqual(len(self.groups()), metrics.MAX_GROUPS)
            self.assertNotIn('secret', json.dumps(self.records()))
