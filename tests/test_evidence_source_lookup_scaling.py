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
from tests.test_post_commit_maintenance import invoke
from tests.verification_receipt_test_support import add_task, seed_current_review_evidence, add_receipt
from task_governance_tool import storage


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
    def test_selected_completion_work_is_bounded_and_both_queries_equal_old_lookup(self):
        measured = []
        for history in (2, 14):
            with tempfile.TemporaryDirectory(prefix='taskgov-source-lookup-') as directory:
                target = make_fixture(Path(directory), count=history)
                task = add_task(target.db_path, target.project.canonical_repo,
                                title='Fixed selected source closure', review_tier=2)
                generation = seed_current_review_evidence(target.db_path,
                    target.project.canonical_repo, task['task_id'])
                verified = add_receipt(target.db_path, target.project.canonical_repo,
                                       task['task_id'], generation)
                self.assertEqual(verified.returncode, 0, verified.stdout)
                before = target.db_path.read_bytes()
                with measured_connections() as costs:
                    code, out, err = invoke(target, 'task', 'complete', task['task_id'],
                        '--check', '--read-only', '--verification-complete',
                        '--review-complete', '--commit-not-required', '--json')
                self.assertEqual(code, 0, out + err)
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
                        kinds.add('whole' if 'SELECT reference.*' in sql else 'identity')
                        plan = '\n'.join(row[3] for row in connection.execute('EXPLAIN QUERY PLAN ' + sql))
                        self.assertIn('SEARCH reference USING INDEX idx_evidence_references_source_identity', plan)
                        self.assertIn('SEARCH owner', plan)
                        self.assertNotIn('SCAN owner', plan)
                        self.assertNotIn('SCAN reference', plan)
                        self.assertNotIn('USE TEMP B-TREE', plan)
                        # Independent retained query: enumerate all project owners, then source.
                        old, count = re.subn(
                            r'CROSS JOIN evidence_references AS reference\s+INDEXED BY idx_evidence_references_source_identity\s+'
                            r'CROSS JOIN tasks AS owner\s+INDEXED BY idx_tasks_project_task_identity\s+WHERE reference.project_id =',
                            'CROSS JOIN tasks AS owner INDEXED BY idx_tasks_project_task_identity '
                            'CROSS JOIN evidence_references AS reference INDEXED BY idx_evidence_references_source '
                            'WHERE owner.project_id =', sql)
                        self.assertEqual(count, 1)
                        actual = {tuple(row) for row in connection.execute(sql)}
                        expected = {tuple(row) for row in connection.execute(old)}
                        self.assertEqual(actual, expected)
                self.assertEqual(kinds, {'whole', 'identity'})
                measured.append(dict(history=history, **costs))
        print('source-lookup-cost ' + json.dumps(measured, sort_keys=True))
        small, large = measured
        self.assertEqual(large['connections'], small['connections'])
        self.assertEqual(large['sql'], small['sql'])
        self.assertLessEqual(large['vm'], small['vm'] + 200)


if __name__ == '__main__':
    unittest.main()
