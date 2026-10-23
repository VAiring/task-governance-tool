"""Actual SELECT cost/identity checks on current DDL, isolated in memory.

Synthetic rows qualify query selection only; the migration/completion suites
separately exercise admitted graphs, triggers, writers and sealed evidence.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'task-governance-tool/scripts'))
from task_governance_tool import storage, review_answer_repository as answers
from task_governance_tool import review_continuation_repository as continuations
from task_governance_tool.sqlite_connection import configure_connection
from task_governance_tool.schema_own_review_indexes import INDEX_SQL
from tests.test_lane_query_scaling import RecordingConnection


class OwnReviewQueryScalingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        connection = configure_connection(sqlite3.connect(':memory:'))
        try:
            storage.apply_migrations(connection)
            cls.ddl = [row[0] for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL "
                "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END,name")]
        finally:
            connection.close()

    def setUp(self):
        self.connection = configure_connection(sqlite3.connect(':memory:'))
        self.addCleanup(self.connection.close)
        self.connection.execute('PRAGMA foreign_keys=OFF')
        self.connection.execute('PRAGMA ignore_check_constraints=ON')
        for sql in self.ddl:
            self.connection.execute(sql)
        recorder = RecordingConnection(self.connection)
        answers.validate_storage_rows(recorder, review_task=('project', 'own'))
        self.collision = next(query for query in recorder.statements if 'WHERE p.rowid IN' in query[0])
        self.dispatch = next(query for query in recorder.statements if 'SELECT dispatch.*' in query[0])
        # Former issued SQL, retained solely as an independent cost/row baseline.
        self.old_collision = ("SELECT 1 FROM review_receipt_provenance_v2 AS p "
            "JOIN review_receipt_provenance AS old ON old.review_provenance_id=p.review_provenance_id "
            "WHERE EXISTS (SELECT 1 FROM review_receipts AS r WHERE r.task_id=? AND "
            "(r.review_receipt_id=p.review_receipt_id OR r.review_provenance_id=p.review_provenance_id)) LIMIT 1", ('own',))
        self.old_dispatch = ("""SELECT dispatch.* FROM review_collection_dispatches AS dispatch
WHERE dispatch.task_id=? OR (dispatch.preparation_id,dispatch.submission_digest) IN (
SELECT source.preparation_id,source.submission_digest FROM review_collection_sources AS source
JOIN review_receipts AS receipt ON receipt.review_receipt_id=source.review_receipt_id WHERE receipt.task_id=?)
OR (dispatch.preparation_id,dispatch.submission_digest) IN (
SELECT entry.preparation_id,entry.submission_digest FROM review_collection_dispatch_entries AS entry
JOIN review_receipts AS receipt ON receipt.review_receipt_id=entry.review_receipt_id WHERE receipt.task_id=?)
ORDER BY preparation_id,submission_digest""", ('own',) * 3)
        recorder.statements.clear()
        continuations.validate_judgment_storage(recorder, review_task=('project', 'own'))
        self.history = next(query for query in recorder.statements if 'SELECT * FROM review_continuations' in query[0])

    def seed(self, table, **values):
        defaults = {row['name']: (0 if row['type'] == 'INTEGER' else '')
                    for row in self.connection.execute('PRAGMA table_info(' + table + ')')
                    if row['notnull'] and row['dflt_value'] is None}
        record = {**defaults, **values}
        self.connection.execute('INSERT INTO ' + table + '(' + ','.join(record) + ') VALUES ('
                                + ','.join('?' for _ in record) + ')', tuple(record.values()))

    def measure(self, query):
        ticks = 0
        def tick():
            nonlocal ticks
            ticks += 1
            return 0
        self.connection.set_progress_handler(tick, 1)
        try:
            rows = [tuple(row) for row in self.connection.execute(*query)]
        finally:
            self.connection.set_progress_handler(None, 0)
        return ticks, rows

    def test_fixed_own_graph_does_not_scan_growing_unrelated_history(self):
        self.seed('review_receipts', review_receipt_id='own-receipt', task_id='own')
        self.seed('review_continuations', continuation_id='own-continuation', task_id='own',
                  preparation_id='own-preparation', sequence=1)
        self.seed('review_collection_dispatches', task_id='own', preparation_id='own-preparation')
        costs = []
        baseline = []
        for size in (100, 1000):
            for number in range(0 if size == 100 else 100, size):
                token = str(number)
                self.seed('review_continuations', continuation_id=token, task_id='unrelated',
                          preparation_id=token, sequence=1)
                self.seed('review_receipt_provenance_v2', review_receipt_id=token, review_provenance_id=token)
                self.seed('review_receipt_provenance', review_provenance_id=token, review_receipt_id=token)
                self.seed('review_collection_dispatches', task_id='unrelated', preparation_id=token)
                self.seed('review_collection_dispatch_entries', preparation_id=token, slot=1, review_receipt_id=token)
            measured = [self.measure(query) for query in (self.history, self.collision, self.dispatch)]
            self.assertEqual([len(rows) for _, rows in measured], [1, 0, 1])
            costs.append([cost for cost, _ in measured])
            for query in (self.history, self.collision, self.dispatch):
                plan = [row[3] for row in self.connection.execute('EXPLAIN QUERY PLAN ' + query[0], query[1])]
                self.assertFalse(any('AUTOMATIC' in row or 'CORRELATED' in row for row in plan), plan)
            # Reproduce the predecessor lookup indexes; table/value semantics
            # are unchanged by33. The live application never drops these indexes.
            for name in INDEX_SQL:
                self.connection.execute('DROP INDEX ' + name)
            previous = [self.measure(query) for query in (self.history, self.old_collision, self.old_dispatch)]
            baseline.append([cost for cost, _ in previous])
            self.assertEqual([rows for _, rows in measured], [rows for _, rows in previous])
            for sql in INDEX_SQL.values():
                self.connection.execute(sql)
        for small, large in zip(*costs):
            self.assertLessEqual(large, small + 80, costs)
        for new, old in zip(costs[-1], baseline[-1]):
            self.assertLess(new * 10, old, (costs, baseline))
        print('own-review VM steps (100, 1000 unrelated), new:', costs, 'previous:', baseline)

    def test_history_keeps_all_states_generations_and_foreign_project_owner(self):
        expected = []
        for generation in (1, 2, 9):
            for sequence, state in enumerate(('preparing', 'prepared', 'registered', 'superseded'), 1):
                token = f'{generation}-{sequence}'
                expected.append(token)
                self.seed('review_continuations', continuation_id=token, task_id='own',
                          project_id='foreign', preparation_id=str(generation), target_generation=generation * 10 + sequence,
                          state=state, sequence=sequence)
        actual = self.connection.execute(*self.history).fetchall()
        self.assertEqual([row['continuation_id'] for row in actual], expected)
        with self.assertRaises(storage.StorageError):
            continuations.validate_judgment_storage(self.connection, review_task=('project', 'own'))

    def test_provenance_both_identity_arms_and_null_receipt_id(self):
        self.seed('review_receipts', review_receipt_id='own-receipt', task_id='own', review_provenance_id='alias')
        cases = [('own-receipt', 'different'), ('foreign-receipt', 'alias'), (None, 'alias')]
        for receipt, provenance in cases:
            with self.subTest(receipt=receipt, provenance=provenance):
                self.connection.execute('DELETE FROM review_receipt_provenance_v2')
                self.connection.execute('DELETE FROM review_receipt_provenance')
                self.seed('review_receipt_provenance_v2', review_receipt_id=receipt, review_provenance_id=provenance)
                self.seed('review_receipt_provenance', review_provenance_id=provenance)
                self.assertEqual(self.connection.execute(*self.collision).fetchone()[0], 1)
                self.assertEqual(self.measure(self.collision)[1], self.measure(self.old_collision)[1])
        self.connection.execute('UPDATE review_receipts SET review_provenance_id=NULL')
        self.assertIsNone(self.connection.execute(*self.collision).fetchone())

    def test_dispatch_union_keeps_reverse_only_foreign_owners_and_deduplicates(self):
        self.seed('review_receipts', review_receipt_id='own-receipt', task_id='own')
        for arm in ('source', 'entry'):
            with self.subTest(arm=arm):
                self.connection.execute('DELETE FROM review_collection_sources')
                self.connection.execute('DELETE FROM review_collection_dispatch_entries')
                self.connection.execute('DELETE FROM review_collection_dispatches')
                self.seed('review_collection_dispatches', preparation_id='p', submission_digest='d',
                          project_id='foreign', task_id='foreign')
                table = 'review_collection_sources' if arm == 'source' else 'review_collection_dispatch_entries'
                self.seed(table, review_receipt_id='own-receipt', preparation_id='p', submission_digest='d', slot=1)
                self.assertEqual(len(self.connection.execute(*self.dispatch).fetchall()), 1)
                self.assertEqual(self.measure(self.dispatch)[1], self.measure(self.old_dispatch)[1])
                # For the entry-only arm, the full reader reaches owner rejection
                # before decoding an inventory; no source row is needed.
                if arm == 'entry':
                    with self.assertRaises(storage.StorageError):
                        answers.validate_storage_rows(self.connection, review_task=('project', 'own'))
        self.connection.execute("UPDATE review_collection_dispatches SET task_id='own'")
        self.seed('review_collection_sources', review_receipt_id='own-receipt', preparation_id='p', submission_digest='d', slot=1)
        self.assertEqual(len(self.connection.execute(*self.dispatch).fetchall()), 1)


if __name__ == '__main__':
    unittest.main()
