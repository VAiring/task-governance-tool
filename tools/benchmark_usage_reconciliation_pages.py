"""Offline, disposable-fixture comparison of actual source revisions.

Run from candidate root: python -B tools/benchmark_usage_reconciliation_pages.py
  --baseline <physical baseline checkout> --output <ignored result.json>
Timing and SQL counting use separate restored runs; no production monkeypatch
changes batching, clocks, budgets or attribution. Page probes only observe calls.
"""

import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def writer_wait(source):
    """Measure a real Core commit blocked by one selected overlay page."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    import inspect
    from threading import Event
    sys.path[:0] = [str(source / "task-governance-tool/scripts"), str(source)]
    from tests.test_usage_reconciliation_runtime import RuntimeTests
    from tests.test_usage_collection import THREAD
    from tests.test_usage_attribution import turn
    from task_governance_tool import storage, tasks
    from task_governance_tool import usage_reconciliation_runtime as runtime
    from task_governance_tool import usage_reconciliation_overlays as overlays
    from task_governance_tool.session_identity import CallerIdentity
    from task_governance_tool.usage_values import UsageError
    fixture = RuntimeTests()
    try:
        fixture.setUp()
        fixture.drain_snapshots()
        with fixture.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_reconcile_overlay_turn_work VALUES (?,?)",
                                   [(THREAD, turn(n)) for n in range(1, 130)])
        entered, committing, measured = Event(), Event(), {}
        def edit():
            assert entered.wait(10)
            started = time.monotonic()
            with closing(storage.connect_initialized(fixture.target)) as core:
                def trace(sql):
                    if sql.strip().upper() == "COMMIT":
                        measured["commit_started"] = time.monotonic()
                        committing.set()
                core.set_trace_callback(trace)
                tasks.edit_task(core, fixture.target.project, fixture.task_id, database_target=fixture.target,
                                caller=CallerIdentity(THREAD), title="Page contention benchmark")
                core.commit()
            measured["commit_wait_ms"] = (time.monotonic() - measured.pop("commit_started")) * 1000
            measured["update_ms"] = (time.monotonic() - started) * 1000
        original = overlays.apply_turn
        def apply(connection, *key):
            if not entered.is_set():
                entered.set()
                assert committing.wait(10)
                assert not editor.done()
            return original(connection, *key)
        options = ({"committed_phases": {"turn_overlays"}}
                   if "committed_phases" in inspect.signature(runtime.advance_effects).parameters else {})
        with ThreadPoolExecutor(max_workers=1) as pool:
            editor = pool.submit(edit)
            with mock.patch.object(overlays, "apply_turn", apply):
                started = time.monotonic()
                runtime.advance_effects(fixture.repository, fixture.reader, **options)
                measured["page_ms"] = (time.monotonic() - started) * 1000
            editor.result(timeout=10)
        with fixture.repository.connection() as connection:
            measured["units"] = 129 - connection.execute("SELECT COUNT(*) FROM usage_reconcile_overlay_turn_work").fetchone()[0]
        try:
            runtime.advance_effects(fixture.repository, fixture.reader, **options)
        except UsageError as error:
            assert error.code == "usage_pending"
        else:
            raise AssertionError("stale basis accepted")
        measured["next_page_rejected_stale_core"] = True
        return measured
    finally:
        fixture.doCleanups()


def worker(source, root, turns, instrument):
    sys.path[:0] = [str(source / "task-governance-tool/scripts"), str(source)]
    from task_governance_tool import usage_lifecycle as lifecycle, storage
    from task_governance_tool import usage_reconciliation_runtime as runtime
    from tests.test_usage_collection import THREAD
    from tests.test_usage_attribution import turn
    counts, pages, publications = Counter(), [], []
    with ExitStack() as stack:
        if instrument:
            connect = sqlite3.connect
            def traced(*args, **kwargs):
                connection = connect(*args, **kwargs)
                counts["connections"] += 1
                def trace(sql):
                    counts["sql"] += 1
                    counts[sql.lstrip().split(None, 1)[0].upper()] += 1
                connection.set_trace_callback(trace)
                return connection
            validate = storage._validate_current_schema_contract
            def admitted(*args, **kwargs):
                counts["core_schema_checks"] += 1
                return validate(*args, **kwargs)
            stack.enter_context(mock.patch.object(sqlite3, "connect", traced))
            stack.enter_context(mock.patch.object(storage, "_validate_current_schema_contract", admitted))
        else:
            for name in ("advance_initial", "advance_effects"):
                original = getattr(runtime, name)
                def observed(*args, _original=original, **kwargs):
                    started = time.monotonic()
                    result = _original(*args, **kwargs)
                    pages.append({"phase": result["phase"], "ms": (time.monotonic() - started) * 1000})
                    return result
                stack.enter_context(mock.patch.object(runtime, name, observed))
        refresh = lifecycle.refresh_usage
        def published(*args, **kwargs):
            result = refresh(*args, **kwargs)
            publications.append(result)
            return result
        stack.enter_context(mock.patch.object(lifecycle, "refresh_usage", published))
        host = root.parent / "codex-host"
        transcript = host / "sessions/2026/10/01" / f"rollout-fixture-{THREAD}.jsonl"
        events = []
        for attempt in range(10):
            payload = {"hook_event_name": "SessionStart" if attempt == 0 else "Stop", "session_id": THREAD,
                       "turn_id": turn(turns), "cwd": str(root), "transcript_path": str(transcript)}
            output = io.StringIO()
            started = time.monotonic()
            code = lifecycle.main(stdin=io.StringIO(json.dumps(payload)), stdout=output,
                skill_root=root / ".agents/skills/task-governance-tool", repo=root,
                environment={**os.environ, "CODEX_HOME": str(host)})
            events.append((time.monotonic() - started) * 1000)
            assert (code, output.getvalue()) == (0, "{}\n")
            if publications and publications[-1]["publication"] == "current":
                break
        else:
            raise AssertionError(("no current publication", publications))
    from task_governance_tool.state_resolver import resolve_project_state
    target = resolve_project_state(skill_root=root / ".agents/skills/task-governance-tool", repo=root).target
    evidence = {str(path.relative_to(target.resolved_usage_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in target.resolved_usage_root.rglob("*.json")}
    return {"events_ms": events, "elapsed_ms": sum(events), "counts": dict(counts), "pages": pages,
            "projection": evidence, "current": True}


def compare(baseline, repeats, output):
    sys.path[:0] = [str(ROOT), str(ROOT / "task-governance-tool/scripts")]
    from tests.test_usage_lifecycle_install import UsageLifecycleInstallTests
    from tests.test_usage_collection import THREAD, event, usage
    from tests.test_usage_attribution import turn
    from tests.test_usage_turn_adapter import tool_record
    results = []
    for turns, responses in ((25, 1), (64, 8)):
        fixture = UsageLifecycleInstallTests()
        try:
            fixture.setUp()
            fixture.cli("setup", "--usage-collection", "on")
            start = fixture.add()
            fixture.log({1: [start]}, count=turns)
            rows = [event("session_meta", id=THREAD, cwd=str(fixture.root), model_provider="openai")]
            for number in range(1, turns + 1):
                rows.extend((event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                             event("turn_context", turn_id=turn(number), model="fixture-model")))
                if number == 1:
                    rows.append(tool_record(start, number))
                rows.extend(usage(f"r{number}-{index}", turn=turn(number)) for index in range(responses))
            fixture.transcript.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
            state = fixture.root / ".taskgov"
            saved = {path.relative_to(state).as_posix(): path.read_bytes() for path in state.rglob("*") if path.is_file()}
            core_before = fixture.target.db_path.read_bytes()
            expected = None
            for iteration in range(repeats + 1):
                instrument = iteration == repeats
                sources = [("baseline", baseline), ("candidate", ROOT)]
                if iteration % 2:
                    sources.reverse()
                for label, source in sources:
                    # Only this test-created physical fixture is restored.
                    for path in state.rglob("*"):
                        if path.is_file() and path.relative_to(state).as_posix() not in saved:
                            path.unlink()
                    for name, data in saved.items():
                        path = state / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(source),
                        str(fixture.root), str(turns), str(int(instrument))], capture_output=True, text=True, timeout=360)
                    if result.returncode:
                        raise RuntimeError(result.stderr)
                    value = json.loads(result.stdout)
                    actual = value.pop("projection")
                    if expected is None:
                        expected = actual
                    assert actual == expected, "published JSON differs"
                    assert fixture.target.db_path.read_bytes() == core_before, "core was mutated"
                    record = {"source": label, "turns": turns, "responses": turns * responses, "iteration": iteration,
                              "instrumented": instrument, "identical_projection": True, "unchanged_core": True, **value}
                    results.append(record)
                    output.write_text(json.dumps({"python": sys.version, "baseline": str(baseline), "candidate": str(ROOT),
                        "results": results}, indent=2) + "\n", encoding="utf-8")
                    print(json.dumps({key: record[key] for key in ("source", "turns", "iteration", "instrumented", "elapsed_ms", "counts")}), flush=True)
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--wait-worker":
        print(json.dumps(writer_wait(Path(sys.argv[2]))))
    elif len(sys.argv) > 1 and sys.argv[1] == "--worker":
        print(json.dumps(worker(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), bool(int(sys.argv[5])))))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--baseline", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--repeats", type=int, default=3)
        args = parser.parse_args()
        if args.repeats < 2:
            parser.error("at least two timing repetitions are required")
        compare(args.baseline.resolve(), args.repeats, args.output.resolve())
