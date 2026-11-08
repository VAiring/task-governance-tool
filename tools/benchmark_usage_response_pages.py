"""Compare the shipped response page with its prior policy in disposable fixtures.

python -B tools/benchmark_usage_response_pages.py --output .tmp-response-results.json
Only the baseline overrides policy; the candidate runs the shipped implementation.
Timing/counting are separate;
identical synthetic state is restored before alternating baseline/candidate runs.
"""

import argparse
from collections import Counter
from contextlib import contextmanager, ExitStack
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "task-governance-tool/scripts")]
POLICIES = {"baseline": (8, .050), "candidate": (128, .250)}


@contextmanager
def policy(name):
    """Run the actual candidate, or override only the previous policy values."""
    from task_governance_tool import usage_reconciliation_runtime as runtime
    assert (runtime._RESPONSE_PAGE_SIZE, runtime._RESPONSE_PAGE_SECONDS) == POLICIES["candidate"]
    assert runtime._RESPONSE_COMMIT_MARGIN_SECONDS == .050
    if name == "candidate":
        yield
        return
    size, seconds = POLICIES[name]
    with mock.patch.object(runtime, "_RESPONSE_PAGE_SIZE", size), \
            mock.patch.object(runtime, "_RESPONSE_PAGE_SECONDS", seconds):
        yield


@contextmanager
def observe(instrument=False):
    """Record actual units and writer-hold time; SQL tracing is a separate run."""
    from task_governance_tool import storage, usage_reconciliation_runtime as runtime
    from task_governance_tool import usage_reconciliation_membership as membership
    from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
    counts, pages, active = Counter(), [], []
    advance, classify = runtime.advance_effects, membership.reconcile_response
    connection_method = UsageReconciliationRepository.connection
    validate_method = UsageReconciliationRepository._validate
    writers = []

    def validate(repository, connection):
        # _connection calls this immediately after BEGIN IMMEDIATE. Measure
        # the admitted writer through commit/close without SQL trace overhead.
        if writers and connection.in_transaction:
            writers[-1].setdefault("started", time.perf_counter())
        return validate_method(repository, connection)

    @contextmanager
    def connection(repository, *args, **kwargs):
        writer = {} if kwargs.get("write") else None
        context_start = time.perf_counter()
        if writer is not None:
            writers.append(writer)
        try:
            with connection_method(repository, *args, **kwargs) as db:
                started = time.perf_counter()
                try:
                    yield db
                finally:
                    body_end = time.perf_counter()
            if active and writer is not None:
                active[-1]["writer_body_ms"] = (body_end - started) * 1000
                active[-1]["writer_hold_ms"] = (time.perf_counter() - writer["started"]) * 1000
                active[-1]["writer_context_ms"] = (time.perf_counter() - context_start) * 1000
        finally:
            if writer is not None:
                writers.pop()

    def one(*args, **kwargs):
        if active:
            active[-1]["units"] += 1
        return classify(*args, **kwargs)

    def page(*args, **kwargs):
        sample = {"units": 0}
        active.append(sample)
        started = time.perf_counter()
        before = counts.copy()
        try:
            result = advance(*args, **kwargs)
        finally:
            active.pop()
        if result["phase"] == "response":
            sample.update(ms=(time.perf_counter() - started) * 1000,
                          counts=dict(counts - before))
            pages.append(sample)
        return result

    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(runtime, "advance_effects", page))
        stack.enter_context(mock.patch.object(membership, "reconcile_response", one))
        stack.enter_context(mock.patch.object(UsageReconciliationRepository, "connection", connection))
        stack.enter_context(mock.patch.object(UsageReconciliationRepository, "_validate", validate))
        if instrument:
            native, validate = sqlite3.connect, storage._validate_current_schema_contract
            def connect(*args, **kwargs):
                db = native(*args, **kwargs)
                counts["connections"] += 1
                def trace(sql):
                    counts["sql"] += 1
                    counts[sql.lstrip().split(None, 1)[0].upper()] += 1
                db.set_trace_callback(trace)
                return db
            def admission(*args, **kwargs):
                counts["core_schema_checks"] += 1
                return validate(*args, **kwargs)
            stack.enter_context(mock.patch.object(sqlite3, "connect", connect))
            stack.enter_context(mock.patch.object(storage, "_validate_current_schema_contract", admission))
        yield counts, pages


def restore(directory, saved):
    """Only the caller-created disposable fixture, with no supplied live path."""
    for path in directory.rglob("*"):
        if path.is_file() and path.relative_to(directory).as_posix() not in saved:
            path.unlink()
    for name, data in saved.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def saved_files(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def response_trials(repeats):
    from tests.test_usage_reconciliation_changes_runtime import ChangedRuntimeTests
    from task_governance_tool import usage_reconciliation_runtime as runtime
    results = []
    for count in (9, 129, 513):
        fixture = ChangedRuntimeTests()
        try:
            fixture.prepare_fixture(real_initialization=False)
            keys, expected = fixture.queued_responses(count)
            repository = fixture.repository
            saved = repository.path.read_bytes()
            core_bytes = fixture.fixture.target.db_path.read_bytes()
            for iteration in range(repeats + 1):
                instrument = iteration == repeats
                for name in (("baseline", "candidate") if iteration % 2 == 0 else ("candidate", "baseline")):
                    repository.path.write_bytes(saved)
                    with policy(name), observe(instrument) as (counts, pages):
                        started = time.perf_counter()
                        first = True
                        for _ in range(count + 2):
                            result = runtime.advance_effects(repository, fixture.reader, single_response=first)
                            if result["phase"] != "response":
                                break
                            first = False
                        else:
                            raise AssertionError("response queue did not drain")
                        elapsed = (time.perf_counter() - started) * 1000
                    assert fixture.assert_frozen_same() == expected
                    assert fixture.fixture.target.db_path.read_bytes() == core_bytes
                    results.append(dict(kind="responses", policy=name, responses=count, iteration=iteration,
                        instrumented=instrument, elapsed_ms=elapsed, counts=dict(counts), pages=pages,
                        frozen_oracle_equal=True, unchanged_core=True))
        finally:
            fixture.doCleanups()
    return results


def writer_wait(name):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    from threading import Event
    from tests.test_usage_reconciliation_changes_runtime import ChangedRuntimeTests
    from tests.test_usage_collection import THREAD
    from task_governance_tool import storage, tasks, usage_reconciliation_runtime as runtime
    from task_governance_tool import usage_reconciliation_membership as membership
    from task_governance_tool.session_identity import CallerIdentity
    from task_governance_tool.usage_values import UsageError
    fixture = ChangedRuntimeTests()
    try:
        fixture.prepare_fixture(real_initialization=False)
        fixture.queued_responses(129)
        entered, committing, measured = Event(), Event(), {}
        def edit():
            assert entered.wait(10)
            started = time.perf_counter()
            with closing(storage.connect_initialized(fixture.fixture.target)) as core:
                def trace(sql):
                    if sql.strip().upper() == "COMMIT":
                        measured["commit_started"] = time.perf_counter()
                        committing.set()
                core.set_trace_callback(trace)
                tasks.edit_task(core, fixture.fixture.target.project, fixture.fixture.task_id,
                    database_target=fixture.fixture.target, caller=CallerIdentity(THREAD), title="Response page contention")
                core.commit()
            measured["commit_wait_ms"] = (time.perf_counter() - measured.pop("commit_started")) * 1000
            measured["update_ms"] = (time.perf_counter() - started) * 1000
        def during_page(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                assert committing.wait(10)
                assert not editor.done()
            return original(*args, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool, policy(name), observe() as (_, pages):
            original = membership.reconcile_response
            editor = pool.submit(edit)
            with mock.patch.object(membership, "reconcile_response", during_page):
                runtime.advance_effects(fixture.repository, fixture.reader)
            editor.result(timeout=10)
        with fixture.repository.connection() as connection:
            remaining = connection.execute("SELECT COUNT(*) FROM usage_reconcile_response_work").fetchone()[0]
        assert remaining == 129 - pages[0]["units"]
        try:
            runtime.advance_effects(fixture.repository, fixture.reader)
        except UsageError as error:
            assert error.code == "usage_pending"
        else:
            raise AssertionError("stale Core accepted")
        fixture.assert_frozen_same()
        return {"kind": "contention", "policy": name, "page": pages[0], **measured,
                "stale_core_rejected": True, "oracle_equal_after_resume": True}
    finally:
        fixture.doCleanups()


def hook_worker(root, turns, name, instrument):
    from tools.benchmark_usage_reconciliation_pages import worker
    with policy(name), observe(instrument) as (counts, pages):
        # Inner worker remains timing-only, even when our separate SQL observer
        # is enabled. Clocks, Hook deadline, publication and ingestion are real.
        result = worker(ROOT, root, turns, False)
    result.pop("pages")
    result["counts"] = dict(counts)
    result["pages"] = pages
    # Outside the timing/counting region, independently recompute attribution,
    # numerical totals and diagnostics using the retained frozen classifier.
    from contextlib import closing
    from tests.test_usage_reconciliation_changes_runtime import ChangedRuntimeTests
    from task_governance_tool import storage
    from task_governance_tool.state_resolver import resolve_project_state
    from task_governance_tool.usage_evidence_service import repository_for
    target = resolve_project_state(skill_root=root / ".agents/skills/task-governance-tool", repo=root).target
    checker = ChangedRuntimeTests()
    checker.repository = repository_for(target)
    checker.reader = lambda: closing(storage.connect_initialized_readonly(target, operation_local=True))
    assert checker.assert_frozen_same() == json.loads(target.resolved_usage_index.read_bytes())
    result["frozen_oracle_equal"] = True
    return result


@contextmanager
def hook_fixture(count):
    """One synthetic turn, one admitted Task; never copied application data."""
    from tests.test_usage_lifecycle_install import UsageLifecycleInstallTests
    from tests.test_usage_collection import THREAD, event, usage
    from tests.test_usage_attribution import turn
    from tests.test_usage_turn_adapter import tool_record
    fixture = UsageLifecycleInstallTests()
    try:
        fixture.setUp()
        fixture.cli("setup", "--usage-collection", "on")
        start = fixture.add()
        fixture.log({1: [start]}, count=1)
        rows = [event("session_meta", id=THREAD, cwd=str(fixture.root), model_provider="openai"),
                event("event_msg", type="task_started", turn_id=turn(1), started_at=1000),
                event("turn_context", turn_id=turn(1), model="fixture-model"), tool_record(start, 1)]
        rows += [usage(f"r-{n}", turn=turn(1)) for n in range(count)]
        fixture.transcript.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        yield fixture
    finally:
        fixture.doCleanups()


def hook_trials(repeats, emit):
    for count in (9, 129, 513):
        with hook_fixture(count) as fixture:
            state = fixture.root / ".taskgov"
            saved, core = saved_files(state), fixture.target.db_path.read_bytes()
            expected = None
            for iteration in range(repeats + 1):
                instrument = iteration == repeats
                for name in (("baseline", "candidate") if iteration % 2 == 0 else ("candidate", "baseline")):
                    restore(state, saved)
                    result = subprocess.run([sys.executable, "-B", __file__, "--hook-worker", str(fixture.root),
                        "1", name, str(int(instrument))], capture_output=True, text=True, timeout=360)
                    if result.returncode:
                        raise RuntimeError(result.stderr)
                    value = json.loads(result.stdout)
                    projection = value.pop("projection")
                    if expected is None:
                        expected = projection
                    assert expected == projection, "published JSON differs"
                    assert fixture.target.db_path.read_bytes() == core, "Core mutated"
                    emit(dict(kind="hook", policy=name, responses=count, iteration=iteration,
                              instrumented=instrument, identical_projection=True, unchanged_core=True, **value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--phase", choices=("all", "responses", "contention", "hook"), default="all")
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error("at least three timing repetitions required")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    runtime = ROOT / "task-governance-tool/scripts/task_governance_tool/usage_reconciliation_runtime.py"
    benchmark = Path(__file__)
    benchmark_digest = hashlib.sha256(benchmark.read_bytes()).hexdigest()
    document = dict(python=sys.version, revision=revision, runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
                    benchmark_sha256=benchmark_digest,
                    policies=POLICIES, deadline_margin_seconds=.050, diagnostic_logging="fixture default off", results=[])
    def emit(row):
        assert hashlib.sha256(benchmark.read_bytes()).hexdigest() == benchmark_digest, "benchmark changed during measurement"
        document["results"].append(row)
        args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in row.items() if k not in {"pages", "counts"}}), flush=True)
    if args.phase in {"all", "responses"}:
        for row in response_trials(args.repeats):
            emit(row)
    if args.phase in {"all", "contention"}:
        for iteration in range(args.repeats):
            for name in (("baseline", "candidate") if iteration % 2 == 0 else ("candidate", "baseline")):
                emit({**writer_wait(name), "iteration": iteration})
    if args.phase in {"all", "hook"}:
        hook_trials(args.repeats, emit)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--hook-worker":
        print(json.dumps(hook_worker(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4], bool(int(sys.argv[5])))))
    else:
        main()
