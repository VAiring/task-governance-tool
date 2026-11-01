"""Isolated cross-revision admission benchmark; no governed state is opened.

Run this same harness against each checkout with --repo and --output. Timings
are descriptive, never pass thresholds. Diagnostic ON measures the existing
transaction/SQL observers in memory, excluding sink publication and disk I/O.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing, contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=7)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    root = args.repo.resolve(strict=True)
    sys.path[:0] = [str(root / 'task-governance-tool/scripts'), str(root)]
    from task_governance_tool import storage, usage_repository as usage
    from task_governance_tool import usage_locator_collection as collection
    from task_governance_tool import sqlite_diagnostics as diagnostics, sqlite_sql_trace
    from task_governance_tool.sqlite_admission import admission_scope
    from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
    from task_governance_tool.usage_sources import source_hint, locator_from_source
    from task_governance_tool.session_identity import CallerIdentity
    from tests.test_routine_backup import make_target

    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = {}
    with tempfile.TemporaryDirectory(prefix='.tmp-admission-measure-', dir=args.output.parent) as temporary:
        scratch = Path(temporary).resolve()
        target = make_target(scratch, enabled=False)
        repository = UsageReconciliationRepository(scratch / 'taskgov-usage.sqlite', 'fixture', 'a' * 64, 1)
        repository.initialize()
        thread = '00000000-0000-4000-8000-000000000001'
        repository.register_session(CallerIdentity(thread))
        roots = (scratch / 'sessions', scratch / 'archive')
        for path in roots:
            path.mkdir()
        hints = []
        for index in range(collection.DIRECT_PAGE + 1):
            hint = source_hint(thread, str(roots[0] / f'rollout-{index}-{thread}.jsonl'), roots, scratch)
            repository.remember_locator(locator_from_source(hint, roots))
            hints.append(hint)

        def core_read(shared=False):
            with closing(storage.connect_readonly(target.db_path)) as connection:
                def read():
                    for _ in range(3 if shared else 1):
                        storage.validate_current_database(connection, target, operation_local=True)
                (admission_scope(read) if shared else read)()
                return connection.in_transaction

        def core_write():
            with closing(storage.connect_existing(target.db_path)) as connection:
                storage.begin_initialized_write(connection, target, operation_local=True)
                connection.execute('UPDATE project_meta SET display_name=display_name')
                connection.rollback()
                return connection.in_transaction

        def direct(hint):
            work = SimpleNamespace(clock=lambda: 0, deadline=10, gaps=set(), known_sources={}, run=mock.Mock())
            with mock.patch.object(collection, 'source_from_locator', return_value=hints[0]):
                collection._direct(repository, roots, scratch, hint, work, 8)
            return len(work.run.call_args.args[0])

        cases = {
            'core_single_read': core_read,
            'core_single_write': core_write,
            'core_shared_read': lambda: core_read(True),
            'usage_single_read': lambda: repository.known_sources([hints[0].source_id]),
            'usage_single_write': lambda: repository.register_session(CallerIdentity(thread)),
            'usage_without_hint': lambda: direct(None),
            'usage_extra_hint': lambda: direct(hints[-1]),
        }

        @contextmanager
        def observed(enabled):
            if not enabled:
                yield None
                return
            collector = diagnostics.Collector(scratch, scratch, 'transactions', operation='other',
                marker=scratch / 'unused', directory_identity=None)
            records = []
            collector.sql_metrics = sqlite_sql_trace.Metrics('debug',
                lambda *a, **k: records.append((a, k)), collector)
            try:
                with diagnostics.bind_collector(collector):
                    yield collector
            finally:
                collector.sql_metrics.finish(publish=True)
                assert not collector._open, 'diagnostic connection not closed'

        for enabled in (False, True):
            mode = 'diagnostics_on' if enabled else 'diagnostics_off'
            for name, action in cases.items():
                statements, opened, steps = [], [], [0]
                def progress():
                    steps[0] += 1
                    return 0
                native_connect, native_configure = sqlite3.connect, repository._configure_work
                def connect(*a, **k):
                    connection = native_connect(*a, **k)
                    opened.append(connection)
                    connection.set_trace_callback(statements.append)
                    connection.set_progress_handler(progress, 1)
                    return connection
                def configure(connection):
                    native_configure(connection)
                    connection.set_progress_handler(progress, 1)
                with observed(enabled), mock.patch.object(sqlite3, 'connect', side_effect=connect), \
                     mock.patch.object(repository, '_configure_work', side_effect=configure):
                    result = action()
                elapsed, overhead = [], []
                for _ in range(args.repeats):
                    with observed(enabled) as collector:
                        started = time.perf_counter_ns()
                        assert action() == result
                        elapsed.append((time.perf_counter_ns() - started) / 1_000_000)
                        if collector is not None:
                            overhead.append({'transaction_ns': collector.instrumentation_ns,
                                             'sql_ns': collector.sql_metrics.instrumentation_ns})
                results.setdefault(mode, {})[name] = {
                    'connections': len(opened), 'sql_total': len(statements),
                    'pragmas': dict(sorted(Counter(sql for sql in statements
                        if sql.lstrip().upper().startswith('PRAGMA')).items())),
                    'structure_selects': sum(sql.startswith('SELECT sql FROM sqlite_master') for sql in statements),
                    'vm_steps_from_connect': steps[0],
                    'median_ms_without_external_counters': round(statistics.median(elapsed), 4),
                    'diagnostic_instrumentation': overhead, 'result': result,
                }
        with closing(storage.connect_readonly(target.db_path)) as connection:
            schema = storage.current_schema_version(connection)

    report = {'revision': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'working_tree_changed_paths': subprocess.check_output(
            ['git', '-C', str(root), 'diff', 'HEAD', '--name-only'], text=True).splitlines(),
        'python': sys.version, 'sqlite': sqlite3.sqlite_version, 'platform': platform.platform(),
        'core_schema': schema, 'usage_schema': repository.migrations[-1][0], 'repeats': args.repeats,
        'fixture_cleanup': 'completed',
        'measurement': 'All traced SQL including PRAGMA; VM starts after native connect and includes configuration. '
            'Elapsed excludes external trace/progress counters. Diagnostic ON includes transaction and SQL '
            'observers in memory, not final serialization or sink I/O. Observer times are separate and not additive.',
        'results': results}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'cases': len(cases) * 2, 'fixture_cleanup': 'completed'}))


if __name__ == '__main__':
    main()
