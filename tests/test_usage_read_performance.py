"""Physical package, real completed Tasks and unmocked lifecycle work.

Fixture creation alone disables unrelated post-commit artifact maintenance.
Measurements retain resolver, core admission, collection, capture, publication
and normal Task display. Wrappers observe calls; they do not replace work.
"""
from collections import Counter
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter
import unittest
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current
from tests import test_usage_evidence_install as install_support
from tests.test_usage_collection import THREAD, usage
from tests.test_usage_attribution import turn
from task_governance_tool import cli, storage
from task_governance_tool import state_resolver
from task_governance_tool.usage_incremental_repository import UsageIncrementalRepository
from task_governance_tool import usage_lifecycle as lifecycle
from task_governance_tool import usage_evidence_service as service


class UsageReadPerformanceTests(unittest.TestCase):
    add = install_support.UsageEvidenceInstallTests.add
    complete = install_support.UsageEvidenceInstallTests.complete
    response_header = install_support.UsageEvidenceInstallTests.response_header
    response_rows = install_support.UsageEvidenceInstallTests.response_rows

    def cli(self, *arguments, ok=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": THREAD}), \
                mock.patch.object(cli, "cli_script_path", return_value=self.install.entrypoint), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main([*arguments, "--repo", str(self.root), "--json"], _maintenance_enabled=False)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["ok"], ok, result)
        self.assertEqual(code == 0, ok, result)
        return result

    def fixture(self, count=32):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.install, self.target, _ = _setup_current(directory)
        self.root = self.install.project_root.resolve()
        self.host = directory / "host"
        logs = self.host / "sessions/2026/10/01"
        logs.mkdir(parents=True)
        self.path = logs / f"rollout-usage-{THREAD}.jsonl"
        rows, tasks = [], []
        for i in range(count):
            start = self.add(f"Independent measured work {i}")
            task = start["data"]["task"]["task_id"]
            end = self.complete(task)
            tasks.append(task)
            rows += self.response_header(THREAD, i + 1, session=i == 0)
            rows += self.response_rows(f"start{i}", i + 1, start)
            rows += self.response_rows(f"work{i}", i + 1)
            rows += self.response_rows(f"end{i}", i + 1, end)
        self.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        initial = self.event("SessionStart")
        self.assertIn("evidence_verification_deferred", initial["diagnostics"], initial)
        index = json.loads(self.target.resolved_usage_index.read_bytes())
        self.assertEqual(len(index["current_snapshot_ids"]), count)
        self.assertEqual(len(index["cycle_links"]), count)
        return tasks

    def event(self, kind="Stop"):
        return lifecycle.collect_event({"hook_event_name": kind, "session_id": THREAD,
            "cwd": str(self.root), "transcript_path": str(self.path)},
            skill_root=self.install.skill_root, repo=self.root, environment={"CODEX_HOME": str(self.host)})

    def measure(self, operation):
        counts, seconds = Counter(), Counter()
        factory = service.repository_for
        instances = []

        def trace(key):
            def count(statement):
                # Count only; never retain expanded SQL or its bound values.
                counts[key] += 1
                if "FROM usage_snapshot_members" in statement:
                    counts["member_sql"] += 1
            return count

        def core_opener(owner):
            original = owner.connect_readonly
            def opened(*args, **kwargs):
                connection = original(*args, **kwargs)
                connection.set_trace_callback(trace("core_sql_after_open"))
                return connection
            return mock.patch.object(owner, "connect_readonly", side_effect=opened)

        configure = UsageIncrementalRepository._configure_work
        def configured(repository, connection):
            configure(repository, connection)
            connection.set_trace_callback(trace("numerical_sql_after_open"))

        def wrap(owner, name, key):
            original = getattr(owner, name)
            def observed(*args, **kwargs):
                started = perf_counter()
                counts[key] += 1
                try:
                    return original(*args, **kwargs)
                finally:
                    seconds[key] += perf_counter() - started
            return mock.patch.object(owner, name, side_effect=observed)

        def repository(target):
            result = factory(target)
            instances.append(result)
            for name, metric in (("members", "member_reads"), ("_snapshot", "snapshot_original_checks"),
                                 ("_responses", "original_reads"), ("prepare_publication", "capture")):
                original = getattr(result, name)
                def observed(*args, _method=original, _metric=metric, **kwargs):
                    started = perf_counter()
                    counts[_metric] += 1
                    if _metric == "original_reads":
                        counts["original_rows"] += len(args[1])
                    try:
                        return _method(*args, **kwargs)
                    finally:
                        seconds[_metric] += perf_counter() - started
                setattr(result, name, observed)
            return result

        with ExitStack() as stack:
            stack.enter_context(core_opener(storage))
            stack.enter_context(core_opener(state_resolver))
            stack.enter_context(mock.patch.object(UsageIncrementalRepository, "_configure_work", configured))
            for owner, name, key in ((lifecycle, "_target", "entry"),
                (lifecycle, "connect_initialized_readonly", "participant_admission"),
                (lifecycle, "registered_participants", "participants"),
                (lifecycle, "_collect_incremental", "collection"),
                (service, "connect_initialized_readonly", "publication_core_admission"),
                (service, "refresh_usage", "publication"),
                (service, "_publish", "file_publication"),
                (storage, "_validate_review_storage_rows", "global_review_audits")):
                stack.enter_context(wrap(owner, name, key))
            # The lifecycle imported the service function; retain its measured
            # real implementation at that existing alias as well.
            stack.enter_context(mock.patch.object(lifecycle, "refresh_usage", side_effect=service.refresh_usage))
            stack.enter_context(mock.patch.object(lifecycle, "repository_for", side_effect=repository))
            stack.enter_context(mock.patch.object(service, "repository_for", side_effect=repository))
            started = perf_counter()
            result = operation()
            seconds["total"] = perf_counter() - started
        counts["components_aggregated"] = sum(getattr(item, "last_projection_work", {}).get("components_aggregated", 0)
                                              for item in instances)
        return result, {"counts": dict(counts), "seconds": dict(seconds)}

    def test_canonical_many_completed_tasks_no_append_single_append_and_display(self):
        tasks = self.fixture()
        before = self.target.db_path.read_bytes()
        no_append, no_work = self.measure(self.event)
        self.assertIn("evidence_verification_deferred", no_append["diagnostics"])
        self.assertEqual(no_work["counts"].get("global_review_audits", 0), 0)
        self.assertEqual(no_work["counts"].get("components_aggregated", 0), 0)
        self.assertLessEqual(no_work["counts"].get("member_reads", 0), len(tasks))
        with self.path.open("ab") as stream:
            stream.write(json.dumps(usage("single-late", turn=turn(len(tasks)), thread=THREAD)).encode() + b"\n")
        appended, append_work = self.measure(self.event)
        self.assertIn("evidence_verification_deferred", appended["diagnostics"])
        normal, normal_work = self.measure(lambda: self.cli("task", "show", tasks[0]))
        self.assertNotIn("usage", normal["data"])
        self.assertEqual(normal_work["counts"].get("numerical_sql_after_open", 0), 0)
        shown, display_work = self.measure(lambda: self.cli("task", "show", tasks[0], "--audit"))
        self.assertEqual(shown["data"]["usage"]["total"]["response_count"], 3)
        self.assertEqual(display_work["counts"].get("snapshot_original_checks", 0), 1)
        self.assertEqual(display_work["counts"].get("original_rows", 0), 3)
        self.assertEqual(self.target.db_path.read_bytes(), before)
        print(json.dumps({"tasks": len(tasks), "responses": len(tasks) * 3 + 1,
                          "no_append": no_work, "single_append": append_work,
                          "task_display": normal_work, "audit_display": display_work}))
