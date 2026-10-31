"""Actual selected Runner SELECTs on exact current DDL, isolated in memory.

Synthetic rows qualify query cost/selection only. Runner lifecycle tests retain
real triggers, semantic graph validation, launch and cleanup acceptance.
"""
import sqlite3
import unittest

from task_governance_tool import storage, verification_runner_repository as runner
from task_governance_tool.schema_runner_lookup_indexes import INDEX_SQL
from task_governance_tool.sqlite_connection import configure_connection
from tests.test_lane_query_scaling import RecordingConnection
from tests import test_own_review_query_scaling as query_fixture


class RunnerQueryScalingTests(unittest.TestCase):
    seed = query_fixture.OwnReviewQueryScalingTests.seed
    measure = query_fixture.OwnReviewQueryScalingTests.measure

    @classmethod
    def setUpClass(cls):
        c = configure_connection(sqlite3.connect(':memory:'))
        try:
            storage.apply_migrations(c)
            cls.ddl = [r[0] for r in c.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL "
                "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END,name")]
        finally:
            c.close()

    def setUp(self):
        self.connection = configure_connection(sqlite3.connect(':memory:'))
        self.addCleanup(self.connection.close)
        self.connection.execute('PRAGMA foreign_keys=OFF')
        self.connection.execute('PRAGMA ignore_check_constraints=ON')
        for sql in self.ddl:
            self.connection.execute(sql)
        self.key = ('project', 'own', 1)
        self.graph('own', 'own', 1)
        record = RecordingConnection(self.connection)
        runner._selected_runner_rows(record, self.key)
        runner._runner_reference_rows(record, self.key, [{'verification_runner_observation_id': 'o-own'}])
        runner._runner_reference_links(record, [['project', 'own', 'ref-own']])
        self.queries = record.statements
        self.assertEqual(len(self.queries), 6)

    def graph(self, token, task, generation):
        fields = dict(project_id='project', task_id=task, target_generation=generation)
        self.seed('verification_runner_resolutions', **fields, verification_runner_resolution_id='r-'+token)
        self.seed('verification_runner_attempts', **fields, verification_runner_resolution_id='r-'+token,
                  verification_runner_attempt_id='a-'+token)
        self.seed('verification_runner_observations', **fields, verification_runner_resolution_id='r-'+token,
                  verification_runner_attempt_id='a-'+token, verification_runner_observation_id='o-'+token)
        self.seed('verification_runner_sandbox_events', **fields, verification_runner_attempt_id='a-'+token,
                  verification_runner_sandbox_event_id='e-'+token, terminal_observation_id='o-'+token)
        self.seed('evidence_references', **fields, evidence_reference_id='ref-'+token,
                  source_id='o-'+token, source_kind='runner_observation')
        self.seed('criterion_evidence_links', project_id='project', task_id=task,
                  criterion_evidence_link_id='link-'+token, evidence_reference_id='ref-'+token)

    def assert_bounded_growth(self, task):
        costs, expected = [], None
        previous = 1
        for size in (1, 101, 10001):
            for i in range(previous, size):
                self.graph(str(i), task, i + 1)
            previous = size
            measured = [self.measure(query) for query in self.queries]
            rows = [result for _, result in measured]
            self.assertEqual([len(result) for result in rows], [1] * 6)
            if expected is None:
                expected = rows
            self.assertEqual(rows, expected)
            costs.append([steps for steps, _ in measured])
        for small, large in zip(costs[0], costs[-1], strict=True):
            self.assertLessEqual(large, small + 80, costs)
        # Prove the assertion detects the missing reverse/generation indexes,
        # without changing any selected result or production index definition.
        for name in INDEX_SQL:
            self.connection.execute('DROP INDEX ' + name)
        baseline = [self.measure(query) for query in self.queries]
        self.assertEqual([rows for _, rows in baseline], expected)
        for i in range(1, 6):
            self.assertGreater(baseline[i][0], costs[-1][i] * 20, (costs, baseline[i][0]))
        print('Runner selected VM steps:', task, costs, 'without indexes:', [n for n, _ in baseline])

    def test_other_task_terminal_history_does_not_expand_selected_work(self):
        self.assert_bounded_growth('unrelated')

    def test_own_old_generations_do_not_expand_selected_work(self):
        self.assert_bounded_growth('own')

    def test_reverse_reference_and_link_arms_retain_foreign_owners(self):
        for table, query in (('evidence_references', self.queries[4]),
                             ('criterion_evidence_links', self.queries[5])):
            self.connection.execute('UPDATE ' + table + " SET project_id='foreign',task_id='foreign'")
            rows = self.connection.execute(*query).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]['project_id'], rows[0]['task_id']), ('foreign', 'foreign'))

    def test_selected_owner_arm_retains_wrong_source_and_duplicate_conflicts(self):
        self.connection.execute("UPDATE evidence_references SET source_id='wrong-source'")
        self.assertEqual(len(self.connection.execute(*self.queries[4]).fetchall()), 1)
        self.seed('evidence_references', project_id='foreign', task_id='foreign', target_generation=9,
                  evidence_reference_id='reverse-only', source_id='o-own', source_kind='review_receipt')
        self.assertEqual(len(self.connection.execute(*self.queries[4]).fetchall()), 2)


if __name__ == '__main__':
    unittest.main()
