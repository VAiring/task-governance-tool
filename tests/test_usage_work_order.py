"""Candidate-bound work, indexed watermarks, and setup-only schema-9 upgrades."""

from contextlib import nullcontext
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_usage_collection as collection
from tests import test_usage_incremental as incremental
from tests import test_usage_locator_repository as locators
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_locator_repository import UsageLocatorRepository
from task_governance_tool.usage_work_order_repository import UsageWorkOrderRepository
from task_governance_tool.usage_values import UsageError


PROFILES = []


class _PreviousOrder(UsageLocatorRepository):
    """Frozen pre-IP-02 selection; the remaining methods and schema are v8."""

    def _candidate_attempts(self, connection, sources):
        return dict(connection.execute("SELECT source_id,last_attempt FROM usage_work_order"))

    def unattempted_sources(self, sources, attempt_floor):
        with self.connection() as connection:
            attempted = {row[0] for row in connection.execute(
                "SELECT source_id FROM usage_work_order WHERE last_attempt>?", (attempt_floor,))}
        return set(sources) - attempted


class _Counted:
    def __init__(self, connection):
        self.connection = connection
        self.rows = 0

    def execute(self, statement, arguments=()):
        cursor = self.connection.execute(statement, arguments)
        owner = self

        class Rows:
            def __iter__(self):
                for row in cursor:
                    owner.rows += 1
                    yield row

            def fetchone(self):
                row = cursor.fetchone()
                owner.rows += int(row is not None)
                return row

        return Rows()

    def executemany(self, statement, arguments):
        return self.connection.executemany(statement, arguments)


def _snapshot(connection):
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name!='usage_migrations' ORDER BY name")]
    return {table: sorted(map(tuple, connection.execute('SELECT * FROM ' + table))) for table in tables}


class WorkOrderTests(unittest.TestCase):
    records = collection.UsageCollectionTests.records
    write = collection.UsageCollectionTests.write
    append = collection.UsageCollectionTests.append
    ingest = incremental.IncrementalCollectionTests.ingest
    test_cursor_attempt_rollback = incremental.IncrementalCollectionTests.test_page_attempt_and_ingest_progress_commit_or_rollback_together
    test_no_progress_keeps_page_pending = incremental.IncrementalCollectionTests.test_no_progress_backlog_does_not_advance_attempt_watermark

    def setUp(self):
        collection.UsageCollectionTests.setUp(self)
        self.repository = UsageWorkOrderRepository(self.repository.path, *self.repository.basis)
        self.assertEqual(self.repository.initialize(), 'migrated')

    def test_same_candidates_and_order_with_100_1000_10000_history(self):
        sources = {f'{number:064x}': SimpleNamespace(thread_id=collection.THREAD if number % 2 else collection.PARENT)
                   for number in range(16)}
        for size in (100, 1000, 10000):
            results = []
            for kind in (_PreviousOrder, UsageWorkOrderRepository):
                repo = kind(self.root / f'{kind.__name__}-{size}.sqlite', *self.repository.basis)
                repo.initialize()
                # One unseen candidate and ties exercise the original ordering.
                with repo.connection(write=True) as connection:
                    connection.executemany('INSERT INTO usage_work_order VALUES (?,?)',
                        [(f'{number:064x}', number // 2) for number in range(1, size)])
                with repo.connection(write=True) as connection:
                    plans = [row[3] for row in connection.execute(
                        'EXPLAIN QUERY PLAN SELECT max(last_attempt) FROM usage_work_order')]
                    counted = _Counted(connection)
                    instructions = [0]
                    def tick():
                        instructions[0] += 1
                        return 0
                    connection.set_progress_handler(tick, 1)
                    with mock.patch.object(repo, 'connection', side_effect=lambda **kw: nullcontext(counted)):
                        order = repo.work_order(sources, collection.THREAD, hint_id=f'{15:064x}')
                        pending = repo.unattempted_sources(sources, 4)
                        for identity in order:
                            repo.attempted(identity)
                        repo.commit_discovery(0, {'root': 0, 'stack': [], 'cycle': 0}, [], False)
                    connection.set_progress_handler(None, 0)
                    attempts = dict(connection.execute('SELECT * FROM usage_work_order WHERE source_id IN ('
                        + ','.join('?' for _ in sources) + ')', tuple(sources)))
                    discovery = json.loads(connection.execute('SELECT document FROM usage_discovery').fetchone()[0])
                value = (order, pending, attempts, discovery)
                results.append(value)
                PROFILES.append(dict(history=size, schema=repo.migrations[-1][0], vm=instructions[0],
                                     rows=counted.rows, plans=plans))
                if kind is UsageWorkOrderRepository:
                    self.assertIn('usage_work_order_by_attempt', ' '.join(plans))
                    self.assertLessEqual(counted.rows, 32)
                    self.assertEqual(order[:2], [f'{0:064x}', f'{15:064x}'])
            self.assertEqual(*results)
        current = [row for row in PROFILES[-6:] if row['schema'] == 9]
        previous = [row for row in PROFILES[-6:] if row['schema'] == 8]
        self.assertLess(max(row['vm'] for row in current) - min(row['vm'] for row in current), 100)
        self.assertLess(current[-1]['vm'] * 10, previous[-1]['vm'])
        self.assertGreater(previous[-1]['rows'], 10000)

    def test_empty_unseen_tied_and_large_candidate_inputs_are_not_truncated(self):
        repo = self.repository
        self.assertEqual(repo.work_order({}, collection.THREAD), [])
        self.assertEqual(repo.unattempted_sources({}, 0), set())
        sources = {f'{number:064x}': SimpleNamespace(thread_id=collection.PARENT) for number in range(300)}
        with repo.connection(write=True) as connection:
            connection.executemany('INSERT INTO usage_work_order VALUES (?,?)',
                                   [(key, 7) for key in sources if int(key, 16) % 2])
        order = repo.work_order(sources, collection.THREAD, hint_id='foreign')
        self.assertEqual(order, sorted(sources, key=lambda key: (7 if int(key, 16) % 2 else -1, key)))
        self.assertEqual(repo.unattempted_sources(sources, 7), set(sources))
        self.assertEqual(repo.unattempted_sources(iter(sources), 7), set(sources))
        self.assertEqual(repo.unattempted_sources(sources, 6), {key for key in sources if not int(key, 16) % 2})

    def test_failed_attempt_and_discovery_cas_keep_atomic_watermark(self):
        repo = self.repository
        key = self.source.source_id
        self.assertEqual(repo.unattempted_sources([key], 0), {key})
        # A failed source still records an attempt, without claiming a cursor.
        repo.attempted(key)
        self.assertEqual(repo.cursor(key, collection.THREAD).offset, 0)
        self.assertEqual(repo.unattempted_sources([key], 0), set())
        repo.commit_discovery(0, {'root': 0, 'stack': [], 'cycle': 0}, [key], False)
        previous = repo.discovery_position()
        with self.assertRaisesRegex(UsageError, 'cursor_stale'):
            repo.commit_discovery(0, {'root': 1, 'stack': [], 'cycle': 0}, ['f' * 64], False)
        self.assertEqual(repo.discovery_position(), previous)
        self.assertEqual(previous[1]['attempt_floor'], 1)
        with repo.connection(write=True) as connection:
            competing = UsageWorkOrderRepository(repo.path, *repo.basis)
            with self.assertRaises(UsageError):
                competing.attempted(key)
        repo.attempted(key)
        self.assertEqual(repo.unattempted_sources([key], 1), set())

    def test_fresh_reentry_missing_index_and_old_reader_rejection(self):
        repo = UsageWorkOrderRepository(self.root / 'fresh.sqlite', *self.repository.basis)
        self.assertEqual(repo.initialize(), 'initialized')
        self.assertEqual(repo.initialize(), 'current')
        with self.assertRaises(UsageError):
            UsageLocatorRepository(repo.path, *repo.basis).inspect()
        with repo.connection(write=True) as connection:
            connection.execute('DROP INDEX usage_work_order_by_attempt')
        before = repo.path.read_bytes()
        for action in (repo.inspect, repo.initialize, lambda: repo.attempted('a' * 64)):
            with self.assertRaises(UsageError):
                action()
            self.assertEqual(repo.path.read_bytes(), before)

    def test_deadline_after_ingest_commit_preserves_attempt_watermark(self):
        self.write(self.records(collection.usage()))
        self.repository.collect_source(self.source, record_attempt=True)
        self.repository.deadline = 0
        with self.assertRaisesRegex(UsageError, 'usage_pending'):
            self.repository.commit_discovery(0, {'root': 0, 'stack': [], 'cycle': 0}, [], False)
        self.repository.deadline = float('inf')
        self.assertEqual(self.repository.unattempted_sources([self.source.source_id], 0), set())
        self.assertEqual(self.repository.discovery_position()[0], 0)
        self.repository.commit_discovery(0, {'root': 0, 'stack': [], 'cycle': 0}, [], False)
        self.assertEqual(self.repository.discovery_position()[1]['attempt_floor'], 1)

    def test_all_eight_predecessors_preserve_observations_and_rollback(self):
        self.write(self.records(collection.usage()))
        for previous in UsageWorkOrderRepository._previous_repositories:
            with self.subTest(schema=previous.migrations[-1][0]):
                old = previous(self.root / f'old-{previous.migrations[-1][0]}.sqlite', *self.repository.basis)
                old.initialize()
                old.register_source(self.source.source_id, CallerIdentity(collection.THREAD))
                batch = read_batch(self.source, old.cursor(self.source.source_id, collection.THREAD),
                                   include_attribution=old.collect_attribution, attribution_project_id=old.basis[0])
                old.commit_batch(batch)
                before = old.summary()
                with old.connection() as connection:
                    original = _snapshot(connection)
                new = UsageWorkOrderRepository(old.path, *old.basis)
                self.assertEqual(new.inspect(), 'migration_required')
                with self.assertRaises(UsageError):
                    new.attempted(self.source.source_id)
                with mock.patch.object(new, '_before_work_order_marker', side_effect=UsageError()):
                    with self.assertRaises(UsageError):
                        new.initialize()
                self.assertEqual(old.inspect(), 'current')
                with old.connection() as connection:
                    self.assertEqual(_snapshot(connection), original)
                self.assertEqual(new.initialize(), 'migrated')
                self.assertEqual(new.initialize(), 'current')
                self.assertEqual(new.summary(), before)
                with new.connection() as connection:
                    self.assertEqual(connection.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                    self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])
                    self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM usage_migrations ORDER BY version')], list(new.migrations))


class WorkOrderContinuationMigrationTests(unittest.TestCase):
    write_source = locators.UsageLocatorRepositoryTests.write_source
    binding = locators.UsageLocatorRepositoryTests.binding

    def setUp(self):
        locators.UsageLocatorRepositoryTests.setUp(self)

    def test_schema8_migration_preserves_every_populated_table_and_cursor(self):
        old = self.repository
        self.write_source()
        old.collect_source(self.source, record_attempt=True)
        old.collect_source(self.source, lane='audit')
        old.remember_locator(self.locator)
        old.commit_discovery(0, {'root': 0, 'stack': [], 'cycle': 0}, [self.source.source_id], False)
        entries = old.locator_page(self.fingerprints)
        with old.connection(write=True) as connection:
            old._save_publication_state(connection, dict(revision=1, basis='a' * 64, position=2, total=5, published=0))
        with old.connection() as connection:
            before = _snapshot(connection)
        new = UsageWorkOrderRepository(old.path, *old.basis)
        self.assertEqual(new.initialize(), 'migrated')
        with new.connection() as connection:
            self.assertEqual(_snapshot(connection), before)
        self.assertEqual(new.locator_page(self.fingerprints), entries)
        with new.connection() as connection:
            self.assertEqual(new._publication_state(connection)['position'], 2)


if __name__ == '__main__':
    unittest.main()
