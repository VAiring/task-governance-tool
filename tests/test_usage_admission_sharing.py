"""Numerical structural reuse is lexical; source, writer and publication stay fresh."""
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
from time import perf_counter
from types import SimpleNamespace
import unittest
from unittest import mock

from task_governance_tool import usage_repository as base
from task_governance_tool.sqlite_admission import structural_read
from task_governance_tool.usage_attribution_repository import UsageAttributionRepository
from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository
from task_governance_tool.usage_wait_repository import UsageWaitRepository
from task_governance_tool.usage_incremental_repository import UsageIncrementalRepository
from task_governance_tool.usage_response_repository import UsageResponseRepository
from task_governance_tool.usage_publication_repository import UsagePublicationRepository
from task_governance_tool.usage_locator_repository import UsageLocatorRepository
from task_governance_tool.usage_work_order_repository import UsageWorkOrderRepository
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool import usage_locator_collection as collection
from task_governance_tool.usage_evidence_service import read_task_usage
from task_governance_tool.usage_values import UsageError
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_sources import source_hint, locator_from_source
from tests.test_usage_collection import THREAD


REPOSITORIES = (base.UsageRepository, UsageAttributionRepository, UsageEvidenceRepository,
                UsageWaitRepository, UsageIncrementalRepository, UsageResponseRepository,
                UsagePublicationRepository, UsageLocatorRepository, UsageWorkOrderRepository,
                UsageReconciliationRepository)


class UsageAdmissionSharingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def repository(self, kind=UsageReconciliationRepository):
        repository = kind(self.root / (kind.__name__ + '.sqlite'), 'fixture', 'a' * 64, 1)
        repository.initialize()
        return repository

    def test_all_schemas_share_only_inside_one_read_and_return_typed_basis(self):
        for kind in REPOSITORIES:
            with self.subTest(kind=kind.__name__):
                repository = self.repository(kind)
                trace = []
                opener = base.connect_readonly
                def opened(*args, **kwargs):
                    connection = opener(*args, **kwargs)
                    connection.set_trace_callback(trace.append)
                    return connection
                with mock.patch.object(base, 'connect_readonly', side_effect=opened) as opens:
                    with repository.admitted_read() as admission:
                        self.assertEqual(admission.validation,
                            base.UsageAdmission(repository.migrations[-1][0], repository.basis))
                        for _ in range(3):
                            with repository.connection(admission=admission) as connection:
                                repository._validate(connection)
                        self.assertEqual(opens.call_count, 1)
                    with repository.connection():
                        pass
                    self.assertEqual(opens.call_count, 2)
                for fragment in ('SELECT sql FROM sqlite_master', 'SELECT * FROM usage_migrations',
                                 'SELECT project_id,binding_hash,binding_generation FROM usage_meta'):
                    self.assertEqual(sum(sql.startswith(fragment) for sql in trace), 2)
                self.assertNotIn('PRAGMA quick_check', trace)
                self.assertNotIn('PRAGMA foreign_key_check', trace)

    def test_standalone_connections_and_lexical_exit_never_reuse(self):
        repository = self.repository()
        with closing(base.connect_readonly(repository.path)) as first, closing(base.connect_readonly(repository.path)) as second:
            with mock.patch.object(base, '_schema', wraps=base._schema) as schema:
                with structural_read():
                    repository._validate(first)
                    repository._validate(first)
                    repository._validate(second)
                    self.assertEqual(schema.call_count, 2)
                repository._validate(first)
                repository._validate(first)
                self.assertEqual(schema.call_count, 4)

    def test_single_use_has_no_reuse_probes_and_shared_admission_runs_once(self):
        repository = self.repository()
        controls = {"PRAGMA schema_version", "PRAGMA temp.schema_version",
                    "PRAGMA query_only", "PRAGMA foreign_keys"}
        for write in (False, True):
            with self.subTest(write=write):
                trace = []
                opener = base.connect_existing if write else base.connect_readonly
                def opened(*args, **kwargs):
                    connection = opener(*args, **kwargs)
                    connection.set_trace_callback(trace.append)
                    return connection
                with mock.patch.object(base, 'connect_existing' if write else 'connect_readonly',
                        side_effect=opened), repository.connection(write=write):
                    pass
                self.assertFalse([sql for sql in trace if sql in controls])
                self.assertEqual(sum('FROM usage_reconcile_state' in sql for sql in trace), 1)
        with mock.patch.object(repository, '_validate', wraps=repository._validate) as validate:
            with repository.admitted_read() as admission:
                for _ in range(3):
                    with repository.connection(admission=admission):
                        pass
            self.assertEqual(validate.call_count, 1)

    def test_borrowing_refuses_transaction_restart_local_write_and_ddl(self):
        repository = self.repository()
        # Fault injection needs a writable fixture descriptor; production reads
        # also enforce SQLite mode=ro, which prevents these corrupting writes.
        def mutable_fixture(path, **kwargs):
            connection = base.connect_existing(path, **kwargs)
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            return connection
        boundaries = (
            lambda c: (c.commit(), c.execute('BEGIN')),
            lambda c: (c.rollback(), c.execute('BEGIN')),
            lambda c: c.executescript('BEGIN; COMMIT; BEGIN;'),
            lambda c: c.execute('SAVEPOINT caller'),
            lambda c: (c.execute('PRAGMA query_only=OFF'), c.execute('PRAGMA query_only=ON')),
            lambda c: (c.execute('PRAGMA query_only=OFF'), c.execute('CREATE TABLE invalid(x)')),
            lambda c: (c.execute('PRAGMA query_only=OFF'), c.execute('CREATE TEMP TABLE usage_meta(x)')),
            lambda c: (c.execute('PRAGMA query_only=OFF'), c.execute("UPDATE usage_meta SET binding_generation=2")),
            lambda c: c.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_FKEY, False),
        )
        with mock.patch.object(base, 'connect_readonly', side_effect=mutable_fixture):
            for boundary in boundaries:
                with self.subTest(boundary=boundary), repository.admitted_read() as admission:
                    boundary(admission.connection)
                    with self.assertRaises(UsageError):
                        with repository.connection(admission=admission):
                            self.fail('stale numerical read accepted')

    def test_diagnostics_observe_shared_reads_without_reintroducing_control_sql(self):
        from task_governance_tool import sqlite_diagnostics as diagnostics, sqlite_sql_trace
        repository = self.repository()
        collector = diagnostics.Collector(self.root, self.root, 'transactions', operation='other',
            marker=self.root / 'unused', directory_identity=None)
        collector.sql_metrics = sqlite_sql_trace.Metrics('debug', lambda *a, **k: None, collector)
        trace = []
        opener = base.connect_readonly
        def opened(*args, **kwargs):
            connection = opener(*args, **kwargs)
            connection.set_trace_callback(trace.append)
            return connection
        with diagnostics.bind_collector(collector), mock.patch.object(base, 'connect_readonly', side_effect=opened):
            with repository.admitted_read() as admission:
                for _ in range(3):
                    self.assertEqual(repository.known_sources([], admission=admission), {})
        self.assertEqual(trace.count('PRAGMA temp.schema_version'), 1)
        self.assertEqual(sum('FROM usage_reconcile_state' in sql for sql in trace), 1)
        self.assertEqual(collector.connections_registered, 1)
        self.assertFalse(collector._open)
        self.assertGreater(collector.sql_metrics.calls, 0)
        self.assertEqual({record['origin'] for record in collector.records}, {'usage_repository.connection'})
        collector.sql_metrics.finish(publish=False)

    def test_handle_exit_close_different_repository_and_writer_are_rejected(self):
        repository = self.repository()
        other = UsageReconciliationRepository(repository.path, *repository.basis)
        with repository.admitted_read() as admission:
            for target, write in ((other, False), (repository, True)):
                with self.assertRaises(UsageError), target.connection(write=write, admission=admission):
                    self.fail('borrow crossed ownership or writer boundary')
            admission.connection.close()
            with self.assertRaises(UsageError), repository.connection(admission=admission):
                self.fail('closed read accepted')
        with self.assertRaises(UsageError), repository.connection(admission=admission):
            self.fail('expired read accepted')

    def test_binding_path_and_physical_replacement_invalidate_borrow(self):
        repository = self.repository()
        copy = self.root / 'replaced.sqlite'
        shutil.copyfile(repository.path, copy)
        with repository.admitted_read() as admission:
            original = repository.basis
            repository.basis = (*original[:2], 2)
            with self.assertRaises(UsageError), repository.connection(admission=admission):
                self.fail('changed binding accepted')
            repository.basis = original
            original_path = repository.path
            repository.path = copy
            with self.assertRaises(UsageError), repository.connection(admission=admission):
                self.fail('changed source accepted')
            repository.path = original_path
            with mock.patch.object(base, '_file_identity', return_value=(0, 999)):
                with self.assertRaises(UsageError), repository.connection(admission=admission):
                    self.fail('replaced file accepted')
        # Replacement during initial open cannot produce a valid borrowing handle.
        real_identity = base._file_identity(repository.path)
        with mock.patch.object(base, '_file_identity', side_effect=[real_identity, (0, 999)]):
            with self.assertRaises(UsageError), repository.admitted_read():
                self.fail('open race accepted')

    def test_mutated_structure_marker_and_binding_are_revalidated(self):
        repository = self.repository()
        mutations = ('CREATE TABLE unowned(x)', "UPDATE usage_migrations SET name='wrong' WHERE version=1",
                     "UPDATE usage_meta SET project_id='foreign'")
        for sql in mutations:
            with self.subTest(sql=sql), closing(base.connect_existing(repository.path)) as connection:
                connection.execute('BEGIN')
                connection.execute('PRAGMA ignore_check_constraints=ON')
                with structural_read():
                    repository._validate(connection)
                    connection.execute(sql)
                    with self.assertRaises(UsageError):
                        repository._validate(connection)
                connection.rollback()

    def test_setup_keeps_physical_and_fk_checks_and_unknown_usage_is_not_zero(self):
        repository = self.repository()
        trace = []
        opener = base.connect_readonly
        def opened(*args, **kwargs):
            connection = opener(*args, **kwargs)
            connection.set_trace_callback(trace.append)
            return connection
        with mock.patch.object(base, 'connect_readonly', side_effect=opened):
            self.assertEqual(repository.initialize(), 'current')
        self.assertIn('PRAGMA quick_check', trace)
        self.assertIn('PRAGMA foreign_key_check', trace)
        with closing(base.connect_existing(repository.path)) as connection:
            connection.execute('CREATE TABLE invalid(x)')
            connection.commit()
        with mock.patch('task_governance_tool.usage_evidence_service.repository_for', return_value=repository):
            result = read_task_usage(None, None, 'tg_task_0000000000000000')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['periods'], [])
        self.assertEqual(result['diagnostics'], ['usage_unavailable'])

    def test_locator_metadata_shares_one_read_and_closes_before_physical_work(self):
        repository = self.repository()
        roots = (self.root / 'sessions', self.root / 'archive')
        for path in roots:
            path.mkdir()
        repository.register_session(CallerIdentity(THREAD))
        hints = []
        for i in range(collection.DIRECT_PAGE + 1):
            hint = source_hint(THREAD, str(roots[0] / f'rollout-{i}-{THREAD}.jsonl'), roots, self.root)
            repository.remember_locator(locator_from_source(hint, roots))
            hints.append(hint)
        work = SimpleNamespace(clock=lambda: 0, deadline=10, gaps=set(), known_sources={}, run=mock.Mock())
        opened_connections = []
        trace, steps = [], [0]
        opener = base.connect_readonly
        configure = repository._configure_work
        def measured(connection):
            configure(connection)
            # This synthetic metadata fixture has no deadline. Install the work
            # counter after the repository's normal progress-handler setup.
            connection.set_progress_handler(lambda: steps.__setitem__(0, steps[0] + 1) or 0, 1)
        self.enterContext(mock.patch.object(repository, '_configure_work', side_effect=measured))
        def opened(*args, **kwargs):
            connection = opener(*args, **kwargs)
            opened_connections.append(connection)
            connection.set_trace_callback(trace.append)
            return connection
        with mock.patch.object(base, 'connect_readonly', side_effect=opened):
            started = perf_counter()
            baseline = list(repository.locator_page(collection.locator_root_fingerprints(roots),
                                                   limit=collection.DIRECT_PAGE))
            baseline.insert(1, repository.locator_by_id(hints[-1].source_id))
            known = repository.known_sources([entry.locator.source_id for entry in baseline])
            baseline_time, baseline_steps = perf_counter() - started, steps[0]
        self.assertEqual(len(opened_connections), 3)
        self.assertEqual(sum(sql.startswith('SELECT sql FROM sqlite_master') for sql in trace), 3)
        opened_connections.clear()
        trace.clear()
        steps[0] = 0
        def physical(*args):
            self.assertEqual(len(opened_connections), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                opened_connections[0].execute('SELECT 1')
            return hints[0]
        with mock.patch.object(base, 'connect_readonly', side_effect=opened), \
                mock.patch.object(collection, 'source_from_locator', side_effect=physical):
            started = perf_counter()
            collection._direct(repository, roots, self.root, hints[-1], work, 8)
            shared_time = perf_counter() - started
        sources = work.run.call_args.args[0]
        self.assertEqual(list(sources), [entry.locator.source_id for entry in baseline])
        self.assertEqual(work.known_sources, known)
        self.assertIn(hints[-1].source_id, sources)
        self.assertEqual(sum(sql.startswith('SELECT sql FROM sqlite_master') for sql in trace), 1)
        self.assertLess(steps[0], baseline_steps)
        print(f'\nUsage locator metadata: connections=3/1, VM steps={baseline_steps}/{steps[0]}, '
              f'seconds={baseline_time:.4f}/{shared_time:.4f}')


if __name__ == '__main__':
    unittest.main()
