"""Source-first reverse admission keeps foreign owners visible at bounded cost."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.test_evidence_projection_capture import make_fixture
from tests.integrity_scale_support import fixed_completion_histories, public_command
from tests.test_post_commit_maintenance import invoke
from tests.test_review_session_migration import candidate_runtime
from tests.verification_receipt_test_support import (
    add_task, seed_current_review_evidence, add_receipt, initialize, run_taskgov, set_target,
)
from task_governance_tool import evidence_repository, storage
from task_governance_tool.stored_task_validation import validate_current_stored_task_rows


@contextmanager
def measured_connections():
    counters = dict(vm=0, sql=0, connections=0, reverse=[], inventory=[])
    connect = sqlite3.connect
    def trace(sql):
        counters['sql'] += 1
        if 'WITH selected_sources(value)' in sql:
            counters['reverse'].append(sql)
        if 'INDEXED BY idx_evidence_references_task_kind' in sql:
            counters['inventory'].append(sql)
    def progress():
        counters['vm'] += 1
        return 0
    def observed(*args, **kwargs):
        connection = connect(*args, **kwargs)
        counters['connections'] += 1
        connection.set_trace_callback(trace)
        connection.set_progress_handler(progress, 1)
        return connection
    with mock.patch.object(sqlite3, 'connect', new=observed):
        yield counters


class EvidenceSourceLookupScalingTests(unittest.TestCase):
    def _verified_task(self, db, repo):
        task = add_task(db, repo, review_tier=2)['task_id']
        generation = seed_current_review_evidence(db, repo, task)
        verified = add_receipt(db, repo, task, generation)
        self.assertEqual(verified.returncode, 0, verified.stdout)
        return task

    def test_predecessor_uncaptured_task_cost_is_independent_of_reference_history(self):
        with candidate_runtime(36), tempfile.TemporaryDirectory() as directory:
            repo, db = initialize(Path(directory))
            selected = add_task(db, repo, title='Uncaptured', status='ready')
            measurements = []
            read_manifests = storage.validate_manifest_evidence_references

            def measure_manifests(connection, *, manifests, selected_project_id):
                self.assertEqual(manifests, {})
                start = cost['vm']
                read_manifests(connection, manifests=manifests, selected_project_id=selected_project_id)
                manifest_steps.append(cost['vm'] - start)

            for added in (1, 3):
                for _ in range(added):
                    other = add_task(db, repo, title='Unrelated', status='ready')
                    set_target(db, repo, other['task_id'])
                with closing(storage.connect_readonly(db)) as connection:
                    self.assertEqual(storage.current_schema_version(connection), 36)
                    count = connection.execute('SELECT COUNT(*) FROM evidence_references').fetchone()[0]
                    row = connection.execute('SELECT * FROM tasks WHERE task_id=?', (selected['task_id'],)).fetchone()
                    self.assertIsNone(row['review_target_artifact_manifest_id'])
                manifest_steps = []
                with measured_connections() as cost, closing(storage.connect_readonly(db)) as connection:
                    with mock.patch.object(storage, 'validate_manifest_evidence_references', measure_manifests):
                        validate_current_stored_task_rows(connection, [row], expected_project_id=selected['project_id'])
                self.assertEqual(len(manifest_steps), 1)
                measurements.append((count, manifest_steps[0], len(cost['reverse'])))
            (small_count, small_vm, small_queries), (large_count, large_vm, large_queries) = measurements
            self.assertGreater(large_count, small_count)
            self.assertEqual(small_vm, large_vm)
            self.assertEqual((small_queries, large_queries), (0, 0))

    def test_predecessor_captured_task_skips_history_under_an_unrelated_owner(self):
        with candidate_runtime(36), tempfile.TemporaryDirectory() as directory:
            repo, db = initialize(Path(directory))
            selected = add_task(db, repo, title='Captured', status='ready')
            other = add_task(db, repo, title='Unrelated', status='ready')
            set_target(db, repo, selected['task_id'])
            read_sources = evidence_repository._read_reference_source_rows
            measurements = []

            def measure_sources(connection, **kwargs):
                start = cost['vm']
                rows = read_sources(connection, **kwargs)
                reads.append((kwargs['identity_only'], len(rows), cost['vm'] - start))
                return rows

            for generation in range(1, 34):
                set_target(db, repo, other['task_id'], fingerprint=f'sha256:{generation:064x}')
                if generation not in (1, 33):
                    continue
                with closing(storage.connect_readonly(db)) as connection:
                    self.assertEqual(storage.current_schema_version(connection), 36)
                    counts = connection.execute(
                        'SELECT COUNT(DISTINCT task_id), COUNT(*) FROM evidence_references'
                    ).fetchone()
                    self.assertEqual(tuple(counts), (2, generation + 1))
                    row = connection.execute('SELECT * FROM tasks WHERE task_id=?', (selected['task_id'],)).fetchone()
                reads = []
                with measured_connections() as cost, closing(storage.connect_readonly(db)) as connection:
                    with mock.patch.object(evidence_repository, '_read_reference_source_rows', measure_sources):
                        validate_current_stored_task_rows(connection, [row], expected_project_id=selected['project_id'])
                self.assertEqual({(identity_only, count) for identity_only, count, _ in reads},
                                 {(False, 1), (True, 1)})
                measurements.append(reads)
            self.assertEqual(measurements[0], measurements[1])

    def test_reverse_lookup_keeps_orphans_visible_to_both_readers(self):
        for version in (36, 37):
            with self.subTest(version=version), candidate_runtime(version), tempfile.TemporaryDirectory() as directory:
                repo, db = initialize(Path(directory))
                task = self._verified_task(db, repo)
                with closing(storage.connect(db)) as connection:
                    self.assertEqual(storage.current_schema_version(connection), version)
                    connection.execute('PRAGMA foreign_keys = OFF')  # corrupt this fixture only
                    for kind in ('artifact_manifest', 'verification_receipt', 'review_receipt'):
                        with self.subTest(kind=kind):
                            reference = dict(connection.execute(
                                'SELECT * FROM evidence_references WHERE task_id=? AND source_kind=? LIMIT 1',
                                (task, kind)).fetchone())
                            project, source = reference['project_id'], reference['source_id']
                            for missing in ('tg_task_000000000000019a', task.encode('utf-8')):
                                with self.subTest(missing=type(missing).__name__):
                                    connection.execute('SAVEPOINT isolated_fault')
                                    try:
                                        self._insert_extra_reference(connection, reference, missing)
                                        with self.assertRaises(storage.StorageError) as failed:
                                            storage._validate_selected_reference_source_chunk(connection,
                                                expected_project_id=project, selected_task_ids={task},
                                                source_owners={(kind, source): task})
                                        self.assertEqual(failed.exception.code, 'evidence_ledger_inconsistent')
                                        if kind == 'artifact_manifest':
                                            authority = storage._validated_authority_context(connection,
                                                snapshot_ids={reference['authority_snapshot_id']})
                                            manifests, _ = storage._validate_artifact_manifest_storage(connection,
                                                snapshots=authority.snapshots, links=authority.links, manifest_ids={source})
                                            with self.assertRaises(storage.StorageError) as failed:
                                                storage.validate_manifest_evidence_references(connection,
                                                    manifests=manifests, selected_project_id=project)
                                            self.assertEqual(failed.exception.code, 'evidence_ledger_inconsistent')
                                    finally:
                                        connection.execute('ROLLBACK TO isolated_fault')
                                        connection.execute('RELEASE isolated_fault')

    @staticmethod
    def _insert_extra_reference(connection, original, missing):
        row = dict(original, task_id=missing, evidence_reference_id='tg_evidence_reference_000000000000019a')
        connection.execute(f"INSERT INTO evidence_references({','.join(row)}) "
                           f"VALUES ({','.join('?' for _ in row)})", tuple(row.values()))

    def test_orphan_selected_source_blocks_check_and_completion_without_writes(self):
        for kind in ('artifact_manifest', 'verification_receipt', 'review_receipt'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                repo, db = initialize(Path(directory))
                task = self._verified_task(db, repo)
                with closing(storage.connect(db)) as connection:
                    connection.execute('PRAGMA foreign_keys = OFF')
                    r = dict(connection.execute(
                        'SELECT * FROM evidence_references WHERE task_id=? AND source_kind=? LIMIT 1',
                        (task, kind)).fetchone())
                    self._insert_extra_reference(connection, r, 'tg_task_000000000000019a')
                    connection.commit()
                before = db.read_bytes()
                for flags in (('--read-only', '--check'), ()):
                    result = run_taskgov('task', 'complete', task, '--repo', str(repo), '--db', str(db),
                        *flags, '--verification-complete', '--review-complete', '--commit-not-required', '--json')
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(json.loads(result.stdout)['errors'], [{
                        'code': 'project_state_unreadable',
                        'message': 'project state could not be read safely',
                    }])
                    self.assertEqual(db.read_bytes(), before)

    def test_unrelated_orphan_does_not_block_selected_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            target = make_fixture(Path(directory), count=1)
            repo, db = target.project.canonical_repo, target.db_path
            task = self._verified_task(db, repo)
            with closing(storage.connect(db)) as connection:
                reference = dict(connection.execute(
                    "SELECT * FROM evidence_references WHERE source_kind='completion_evidence' AND task_id!=?",
                    (task,)).fetchone())
                closure = dict(expected_project_id=reference['project_id'], selected_task_ids={reference['task_id']},
                               source_owners={('completion_evidence', reference['source_id']): reference['task_id']})
                storage._validate_selected_reference_source_chunk(connection, **closure)
                connection.execute('PRAGMA foreign_keys = OFF')
                self._insert_extra_reference(connection, reference, 'tg_task_000000000000019a')
                connection.commit()
                # The same corrupt source is rejected if it actually enters the closure.
                with self.assertRaises(storage.StorageError) as failed:
                    storage._validate_selected_reference_source_chunk(connection, **closure)
                self.assertEqual(failed.exception.code, 'evidence_ledger_inconsistent')
            code, out, err = invoke(target, 'task', 'complete', task, '--verification-complete',
                '--review-complete', '--commit-not-required', '--json', maintenance_enabled=False)
            self.assertEqual(code, 0, out + err)
            with closing(storage.connect_readonly(db)) as connection:
                self.assertEqual(connection.execute('SELECT status FROM tasks WHERE task_id=?', (task,)).fetchone()[0], 'done')

    def test_selected_completion_work_is_bounded_and_both_queries_equal_old_lookup(self):
        measured = []
        with fixed_completion_histories() as (task_id, histories):
            for history, target in histories:
                before = target.db_path.read_bytes()
                with measured_connections() as costs:
                    gate = public_command(target, 'task', 'complete', task_id,
                        '--check', '--read-only', '--verification-complete',
                        '--review-complete', '--commit-not-required')
                self.assertIs(gate['data']['ready'], True)
                self.assertEqual(gate['data']['blocking_codes'], [])
                self.assertEqual(target.db_path.read_bytes(), before)
                self.assertTrue(costs['reverse'])
                kinds = set()
                with closing(storage.connect_readonly(target.db_path)) as connection:
                    inventory = costs.pop('inventory')
                    self.assertTrue(inventory)
                    for sql in inventory:
                        plan = '\n'.join(row[3] for row in connection.execute('EXPLAIN QUERY PLAN ' + sql))
                        self.assertIn('SEARCH evidence_references USING INDEX idx_evidence_references_task_kind', plan)
                        self.assertNotIn('SCAN evidence_references', plan)
                        old = sql.replace('INDEXED BY idx_evidence_references_task_kind', '')
                        self.assertEqual(sorted(tuple(row) for row in connection.execute(sql)),
                                         sorted(tuple(row) for row in connection.execute(old)))
                    for sql in costs.pop('reverse'):
                        whole_row = 'SELECT reference.*' in sql
                        kinds.add('whole' if whole_row else 'identity')
                        plan = '\n'.join(row[3] for row in connection.execute('EXPLAIN QUERY PLAN ' + sql))
                        self.assertIn('SEARCH reference USING INDEX idx_evidence_references_source_identity', plan)
                        self.assertIn('SEARCH owner', plan)
                        self.assertNotIn('SCAN owner', plan)
                        self.assertNotIn('SCAN reference', plan)
                        self.assertNotIn('USE TEMP B-TREE', plan)
                        # On valid input the result must match an independent owner-first
                        # inventory. Do not transform the implementation's joins into an oracle.
                        keys_arg = re.search(r"json_each\('([^']*)'\)", sql)
                        self.assertIsNotNone(keys_arg)
                        keys = {tuple(key) for key in json.loads(keys_arg[1])}
                        fields = ('evidence_reference_id', 'project_id', 'task_id', 'source_kind', 'source_id')
                        expected = []
                        for row in connection.execute(
                            'SELECT r.* FROM tasks t JOIN evidence_references r '
                            'ON r.project_id=t.project_id AND r.task_id=t.task_id WHERE t.project_id=?',
                            (target.project.project_id,)):
                            if (row['source_kind'], row['source_id']) in keys:
                                values = tuple(row) if whole_row else tuple(row[k] for k in fields)
                                expected.append(values + (row['task_id'],))
                        self.assertCountEqual((tuple(row) for row in connection.execute(sql)), expected)
                self.assertEqual(kinds, {'whole', 'identity'})
                measured.append(dict(history=history, **costs))
        print('source-lookup-cost ' + json.dumps(measured, sort_keys=True))
        small, large = measured
        self.assertEqual(large['connections'], small['connections'])
        self.assertEqual(large['sql'], small['sql'])
        self.assertEqual(large['vm'], small['vm'])


if __name__ == '__main__':
    unittest.main()
