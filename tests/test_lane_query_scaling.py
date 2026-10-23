"""Current-DDL SQL cost and independent lane semantics, in memory only.

Synthetic rows here qualify SELECTs, not domain writes. Lifecycle/CLI suites
separately exercise real triggers, stored-row validation and atomic mutation.
"""
import random
import sqlite3
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'task-governance-tool/scripts'))
from task_governance_tool import ordering, selection, storage, tasks
from task_governance_tool.sqlite_connection import configure_connection


PROJECT = 'query-test'


class RecordingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.statements = []

    def execute(self, sql, args=()):
        self.statements.append((sql, tuple(args)))
        return self.connection.execute(sql, args)

    def __getattr__(self, name):
        return getattr(self.connection, name)


class LaneQueryScalingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = configure_connection(sqlite3.connect(':memory:'))
        try:
            storage.apply_migrations(source)
            cls.ddl = [r[0] for r in source.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('table','index') "
                "AND sql IS NOT NULL ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END,name")]
            cls.markers = [tuple(r) for r in source.execute('SELECT * FROM schema_migrations')]
        finally:
            source.close()

    def setUp(self):
        self.connection = configure_connection(sqlite3.connect(':memory:'))
        self.addCleanup(self.connection.close)
        self.connection.execute('PRAGMA foreign_keys=OFF')
        self.connection.execute('PRAGMA ignore_check_constraints=ON')
        for ddl in self.ddl:
            self.connection.execute(ddl)
        self.connection.executemany('INSERT INTO schema_migrations VALUES (?,?,?)', self.markers)
        recorder = RecordingConnection(self.connection)
        selection.select_next_tasks(recorder, SimpleNamespace(project_id=PROJECT))
        self.next_query = next((sql, args) for sql, args in recorder.statements if 'SELECT task.*' in sql)
        recorder.statements.clear()
        ordering.first_out_of_order_advanced_task(recorder, project_id=PROJECT, lanes={'L'})
        self.guard_query = recorder.statements[0]

    def seed(self, rows):
        info = self.connection.execute('PRAGMA table_info(tasks)').fetchall()
        defaults = {r['name']: (0 if r['type'] == 'INTEGER' else '')
                    for r in info if r['notnull'] and r['dflt_value'] is None}
        for number, row in enumerate(rows):
            value = {**defaults, 'task_id': f'tg_task_{number:016x}',
                     'project_id': PROJECT, 'title': 'Query fixture',
                     'kind': 'sequential', 'lane': 'L', 'lane_order': number + 1,
                     'status': 'done', 'priority': 'normal', 'review_tier': 0,
                     'created_at': '2026-01-01T00:00:00Z',
                     'updated_at': '2026-01-01T00:00:00Z', **row}
            columns = ','.join(value)
            self.connection.execute(f"INSERT INTO tasks({columns}) VALUES ({','.join('?' for _ in value)})", tuple(value.values()))

    def measure(self, query):
        ticks = 0
        def tick():
            nonlocal ticks
            ticks += 1
            return 0
        self.connection.set_progress_handler(tick, 1)
        try:
            rows = [tuple(r) for r in self.connection.execute(*query)]
        finally:
            self.connection.set_progress_handler(None, 0)
        return ticks, rows

    def test_actual_sql_growth_is_linear_for_same_and_unrelated_lanes(self):
        # Former correlated access remains a test baseline, evaluated against
        # the identical current DDL and rows, not historical timing numbers.
        old_guard = f"""SELECT task.task_id FROM tasks task
            WHERE task.project_id=? AND task.kind='sequential'
            AND taskgov_canonical_lane(task.lane) IN (?)
            AND task.status IN ('in_progress','review_pending','done')
            AND {ordering.incomplete_predecessor_sql()}
            ORDER BY taskgov_canonical_lane(task.lane),task.lane_order,task.task_id LIMIT 1"""
        old_next = f"""SELECT task.*,COUNT(*) OVER() AS total_matching FROM tasks task
            WHERE task.project_id=? AND task.status='ready'
            AND (task.kind='optional' OR
                (NOT {ordering.incomplete_predecessor_sql()} AND NOT {ordering.duplicate_lane_order_sql()}))
            ORDER BY CASE task.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                     WHEN 'normal' THEN 2 ELSE 3 END,
            taskgov_canonical_lane(task.lane),task.lane_order IS NULL,task.lane_order,
            task.created_at,task.task_id LIMIT ?"""
        observations = {}
        for scenario in ('same_done', 'other_done', 'other_ready'):
            for size in (100, 1000):
                self.connection.execute('DELETE FROM tasks')
                self.seed([{}] + [{'lane': 'L' if scenario == 'same_done' else f'L{i}',
                                  'status': 'ready' if scenario == 'other_ready' else 'done'}
                                 for i in range(size)])
                new_query = self.next_query if scenario == 'other_ready' else self.guard_query
                old_query = (old_next, (PROJECT, 5)) if scenario == 'other_ready' else (old_guard, (PROJECT, 'L'))
                new, actual = self.measure(new_query)
                old, expected = self.measure(old_query)
                self.assertEqual(actual, expected)
                observations[scenario, size] = (new, old)
                plan = [r[3] for r in self.connection.execute('EXPLAIN QUERY PLAN ' + new_query[0], new_query[1])]
                self.assertFalse(any('CORRELATED' in step for step in plan), plan)
                if scenario == 'other_ready':
                    self.assertTrue(any('LIST SUBQUERY' in step for step in plan), plan)
            small, large = observations[scenario, 100], observations[scenario, 1000]
            self.assertLess(large[0], small[0] * 13, observations)
            if scenario != 'other_done':
                self.assertLess(large[0] * 10, large[1], observations)
        print('lane VM steps (new, baseline):', observations)

    def test_mixed_states_canonical_lanes_and_collisions_match_independent_oracle(self):
        rng = random.Random(2302)
        statuses = ['ready', 'in_progress', 'paused', 'blocked', 'review_pending', 'done', 'cancelled']
        raw_lanes = ['L', ' L ', '\tL', '\u3000L\u3000', 'M', ' M ']
        seeded = []
        seen = set()
        for number in range(90):
            lane, order = rng.choice(raw_lanes), rng.randrange(1, 40)
            if (lane, order) in seen:
                continue
            seen.add((lane, order))
            seeded.append({'lane': lane, 'lane_order': order, 'status': rng.choice(statuses),
                           'kind': 'optional' if number % 9 == 0 else 'sequential'})
        seeded += [{'kind': 'optional', 'lane_order': None, 'status': 'ready'},
                   {'project_id': 'foreign', 'lane': 'L', 'lane_order': 0, 'status': 'blocked'}]
        self.seed(seeded)
        rows = [dict(r) for r in self.connection.execute('SELECT * FROM tasks WHERE project_id=?', (PROJECT,))]
        def earlier(task):
            return any(r['kind'] == 'sequential' and r['lane'].strip() == task['lane'].strip()
                       and r['lane_order'] < task['lane_order'] and r['status'] not in ('done','cancelled')
                       for r in rows)
        def collision(task):
            return any(r['task_id'] != task['task_id'] and r['kind'] == 'sequential'
                       and r['lane'].strip() == task['lane'].strip()
                       and r['lane_order'] == task['lane_order'] for r in rows)
        ready = [r for r in rows if r['status'] == 'ready' and
                 (r['kind'] == 'optional' or (not earlier(r) and not collision(r)))]
        ready.sort(key=lambda r: (['urgent','high','normal','low'].index(r['priority']),
                                 r['lane'].strip(), r['lane_order'] is None,
                                 r['lane_order'] or 0, r['created_at'], r['task_id']))
        result = self.connection.execute(*self.next_query).fetchall()
        self.assertEqual([r['task_id'] for r in result], [r['task_id'] for r in ready[:5]])
        self.assertEqual(selection.count_next_tasks(self.connection, PROJECT), len(ready))
        if result:
            self.assertEqual(result[0]['total_matching'], len(ready))
        for lanes in ({'L'}, {'M'}, {' L ', 'M'}, set()):
            advanced = sorted((r for r in rows if r['kind'] == 'sequential'
                               and r['lane'].strip() in {s.strip() for s in lanes}
                               and r['status'] in ordering.ADVANCED_STATUSES and earlier(r)),
                              key=lambda r: (r['lane'].strip(), r['lane_order'], r['task_id']))
            self.assertEqual(ordering.first_out_of_order_advanced_task(
                self.connection, project_id=PROJECT, lanes=lanes), advanced[0]['task_id'] if advanced else None)

    def test_single_task_max_and_conflict_preserve_canonical_and_overflow_rules(self):
        self.seed([{'lane': ' L ', 'lane_order': 7}, {'lane': '\u3000L', 'lane_order': 13},
                   {'lane': 'L', 'kind': 'optional', 'lane_order': 99}])
        self.assertEqual(tasks.next_lane_order(self.connection, PROJECT, 'L'), 14)
        self.assertTrue(tasks.canonical_lane_order_conflict(self.connection, project_id=PROJECT,
                        task_id='other', lane=' L ', lane_order=7))
        self.assertFalse(tasks.canonical_lane_order_conflict(self.connection, project_id=PROJECT,
                         task_id='tg_task_0000000000000000', lane='L', lane_order=7))
        self.connection.execute("UPDATE tasks SET lane_order=? WHERE task_id=?", (2**63-1, 'tg_task_0000000000000001'))
        with self.assertRaisesRegex(Exception, 'signed 64-bit maximum'):
            tasks.next_lane_order(self.connection, PROJECT, 'L')

    def test_optional_only_selection_does_not_scan_sequential_history(self):
        costs = []
        for size in (100, 1000):
            self.connection.execute('DELETE FROM tasks')
            self.seed([{'kind': 'optional', 'status': 'ready', 'lane_order': None}]
                      + [{'lane': f'old{i}'} for i in range(size)])
            cost, result = self.measure(self.next_query)
            self.assertEqual(len(result), 1)
            costs.append(cost)
        self.assertLessEqual(costs[1], costs[0] + 50, costs)


if __name__ == '__main__':
    unittest.main()
