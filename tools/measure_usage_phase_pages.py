"""Offline before/after measurement of the actual phase runtime, not a prototype.

Run from the candidate root with --baseline-root pointing to a source-only
checkout. All other package Python files must match; no live state is opened.
Times are collected without observers, counts in a separate observation pass.
"""

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager, nullcontext
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "task-governance-tool/scripts")]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_baseline(root):
    package = Path("task-governance-tool/scripts/task_governance_tool")
    old, current = root / package, ROOT / package
    paths = {p.relative_to(current) for p in current.rglob("*.py")}
    assert paths == {p.relative_to(old) for p in old.rglob("*.py")}
    differences = [str(p) for p in sorted(paths) if (old / p).read_bytes() != (current / p).read_bytes()]
    assert differences == ["usage_reconciliation_runtime.py"], differences
    path = old / "usage_reconciliation_runtime.py"
    spec = importlib.util.spec_from_file_location("task_governance_tool._phase_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {"baseline_runtime_sha256": sha(path.read_bytes()),
                    "candidate_runtime_sha256": sha((current / path.name).read_bytes()),
                    "only_runtime_python_differs": True}


@contextmanager
def selected(module):
    import task_governance_tool
    # Select the complete, unchanged file from the baseline, not a batching
    # wrapper layered on production code. Its imports use identical siblings.
    with mock.patch.dict(sys.modules, {"task_governance_tool.usage_reconciliation_runtime": module}), \
            mock.patch.object(task_governance_tool, "usage_reconciliation_runtime", module):
        yield


class Counts:
    def __init__(self, core_path):
        self.values = Counter()
        self.core = core_path.as_posix().lower()

    def __enter__(self):
        from task_governance_tool import storage
        self.stack = ExitStack()
        native, validator = sqlite3.connect, storage._validate_current_schema_contract
        def connect(*args, **kwargs):
            connection = native(*args, **kwargs)
            self.values["connections"] += 1
            kind = "core" if self.core in str(args[0]).replace("\\", "/").lower() else "usage"
            self.values[kind + "_connections"] += 1
            connection.set_trace_callback(lambda sql: self.values.update([kind + "_sql_trace_callbacks"]))
            return connection
        def schema(*args, **kwargs):
            self.values["schema_entries"] += 1
            return validator(*args, **kwargs)
        self.stack.enter_context(mock.patch.object(sqlite3, "connect", connect))
        self.stack.enter_context(mock.patch.object(storage, "_validate_current_schema_contract", schema))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def compare(test, label, modules, emit):
    original = test.repository.path.read_bytes()
    core = test.fixture.target.db_path.read_bytes()
    expected = None
    for iteration in range(3):
        order = ("before", "after") if iteration % 2 == 0 else ("after", "before")
        for mode in order:
            test.repository.path.write_bytes(original)
            test.repository = type(test.repository)(test.repository.path, *test.repository.basis)
            test.fixture.repository = test.repository
            counts = Counts(test.fixture.target.db_path)
            instrumented = iteration == 2
            with selected(modules[mode]), counts if instrumented else nullcontext():
                started = time.monotonic()
                index, _ = test.repository.prepare_publication(test.reader)
                seconds = time.monotonic() - started
            expected = index if expected is None else expected
            assert expected == index
            assert test.fixture.target.db_path.read_bytes() == core
            emit(kind="measurement", case=label, mode=mode, iteration=iteration,
                 instrumented=instrumented, seconds=seconds, counts=dict(counts.values))
    assert test.assert_same() == expected
    emit(kind="oracle", case=label, exact_index=True, full_replay=True, core_unchanged=True)


def files(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def restore_files(directory, saved):
    # Only owned, isolated TemporaryDirectory contents; never a target root.
    assert directory.resolve().is_relative_to(Path(tempfile.tempdir).resolve())
    for p in directory.rglob("*"):
        assert p.resolve().is_relative_to(directory.resolve())
        if p.is_file() and p.relative_to(directory).as_posix() not in saved:
            p.unlink()
    for name, data in saved.items():
        p = directory / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or p.read_bytes() != data:
            p.write_bytes(data)


def hook_compare(modules, emit):
    from tests.test_usage_lifecycle_install import UsageLifecycleInstallTests
    from tests.test_usage_collection import THREAD, usage
    from tests.test_usage_attribution import turn
    from task_governance_tool import usage_lifecycle as lifecycle
    from task_governance_tool import usage_evidence_service as evidence_service
    test = UsageLifecycleInstallTests()
    try:
        test.setUp()
        test.cli("setup", "--usage-collection", "on")
        start = test.add()
        identity = start["data"]["task"]["task_id"]
        pause = test.cli("task", "edit", identity, "--status", "paused", "--pause-reason", "Isolated pause")
        resume = test.cli("task", "edit", identity, "--status", "in_progress")
        test.log({1: [start], 3: [pause], 5: [resume]}, count=8)
        core = test.target.db_path.read_bytes()
        evidence = files(test.target.resolved_evidence_root)
        for label in ("initial", "unchanged", "one-append"):
            if label == "one-append":
                with test.transcript.open("ab") as stream:
                    stream.write(json.dumps(usage("phase-late", turn=turn(8))).encode() + b"\n")
            saved, results, intents = files(test.root / ".taskgov"), [], []
            for mode, module in modules.items():
                restore_files(test.root / ".taskgov", saved)
                reports, output = [], io.StringIO()
                refresh = evidence_service.refresh_usage
                def observed(*args, **kwargs):
                    value = refresh(*args, **kwargs)
                    reports.append(value)
                    return value
                payload = {"hook_event_name": "SessionStart", "session_id": THREAD, "turn_id": turn(8),
                           "cwd": str(test.root), "transcript_path": str(test.transcript)}
                with selected(module), mock.patch.object(evidence_service, "refresh_usage", observed):
                    started = time.monotonic()
                    code = lifecycle.main(stdin=io.StringIO(json.dumps(payload)), stdout=output,
                        skill_root=test.install.skill_root, repo=test.root, environment={**os.environ, "CODEX_HOME": str(test.host)})
                    elapsed = time.monotonic() - started
                assert (code, output.getvalue()) == (0, "{}\n")
                assert reports and reports[-1]["publication"] == "current", reports
                observed_json = {"index.json": test.target.resolved_usage_index.read_bytes(),
                                 **{"snapshots/" + n: data for n, data in files(test.target.resolved_usage_snapshots).items()}}
                results.append({n: sha(data) for n, data in observed_json.items()})
                # Continuation request identity is deliberately fresh per Hook,
                # and is operational state, not a published numerical document.
                from task_governance_tool.usage_continuation_state import validate
                intent = json.loads((test.target.resolved_usage_runtime / "continuation.json").read_bytes())
                validate(intent)
                intents.append({k: v for k, v in intent.items() if k != "request"})
                assert test.target.db_path.read_bytes() == core
                assert files(test.target.resolved_evidence_root) == evidence
                emit(kind="hook", case=label, mode=mode, seconds=elapsed, publication="current")
            assert results[0] == results[1]
            assert intents[0] == intents[1]
            emit(kind="hook_equality", case=label, json_equal=True, core_unchanged=True,
                 sealed_evidence_unchanged=True, operational_intent_equal_except_fresh_request=True)
    finally:
        test.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--hook-only", action="store_true")
    args = parser.parse_args()
    args.temp_root.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(args.temp_root.resolve())
    os.environ.update(TEMP=tempfile.tempdir, TMP=tempfile.tempdir)
    from task_governance_tool import usage_reconciliation_runtime as candidate
    from tests.usage_phase_page_support import fixture, add_tasks_and_sources
    from tests.test_usage_collection import usage
    from tests.test_usage_attribution import turn
    baseline, metadata = load_baseline(args.baseline_root.resolve())
    modules = {"before": baseline, "after": candidate}
    with args.output.open("x", encoding="utf-8") as stream:
        def emit(**record):
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            print(json.dumps(record), flush=True)
        emit(kind="provenance", python=sys.version, platform=sys.platform, **metadata,
             count_semantics="SQLite trace callbacks, not SQL API calls; observed run time excluded from time comparison")
        if not args.hook_only:
            for cycles, turns, label in ((16, 32, "pause16-turns32"), (0, 128, "task1-turns128"), (0, 8, "tasks13-sources5")):
                test = fixture(cycles=cycles, turns=turns)
                try:
                    if label == "tasks13-sources5":
                        add_tasks_and_sources(test)
                    compare(test, label, modules, emit)
                    if label == "task1-turns128":
                        compare(test, "unchanged-history128", modules, emit)
                        test.append(usage("phase-late", turn=turn(129)))
                        compare(test, "one-append-history128", modules, emit)
                finally:
                    test.doCleanups()
        hook_compare(modules, emit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
