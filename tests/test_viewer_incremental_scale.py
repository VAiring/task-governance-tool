"""Operation-local work stays fixed as unchanged display/history grows.

Timings are inclusive stage observations, never an additive wall-time estimate.
Logger OFF and ON are measured independently using its real disposable sink.
"""
from collections import Counter
from contextlib import closing, contextmanager
import json
import sqlite3
import sys
import time
import unittest
from unittest import mock

from tests import test_sqlite_diagnostics as diagnostics_fixture
from tests import test_debug_log as logger_fixture
from tests.test_viewer_maintenance import make_target, FIXED_TIME
from tests.test_viewer_review_inventory import seed_reviews
from task_governance_tool import debug_log, storage, tasks, viewer, reviews
from task_governance_tool import viewer_maintenance as service
from task_governance_tool import viewer_metadata_repository as metadata
from task_governance_tool import viewer_projection_cache as cache
from task_governance_tool.viewer_display_validation import _bounded_text_predicate
from task_governance_tool.session_identity import CallerIdentity

PROFILES = []


def extend_native_history(target, task_id, caller, count):
    """Real completion/reopen writers, including current immutable Bundles."""
    with closing(storage.connect_initialized(target, operation_local=True)) as c:
        current = c.execute("SELECT count(*) FROM task_completion_cycles WHERE task_id=?", (task_id,)).fetchone()[0]
        for ordinal in range(current + 1, count + 1):
            with c:
                if current or ordinal > 1:
                    tasks.edit_task(c, target.project, task_id, caller=caller, database_target=target,
                                   status="in_progress", reopen_reason="Next isolated history cycle")
                reviews.set_review_target(c, target.project, task_id, caller=caller,
                    kind="diff_fingerprint", revision="sha256:" + f"{ordinal:064x}")
                reviews.add_review_receipt(c, target.project, task_id, caller=caller,
                    reviewer="Mechanical fixture", kind="not_required", verdict="not_required",
                    summary="Isolated history fixture has no review requirement")
            request = tasks.build_completion_request(task_id, verification_complete=True,
                review_complete=True, completion_evidence_kind="commit_not_required")
            basis = tasks.capture_completion_basis(c, target.project, task_id)
            plan = tasks.prepare_completion_plan(basis, target.project, request)
            with c:
                tasks.complete_task(c, target.project, plan, caller=caller, database_target=target)


class ViewerIncrementalScaleTests(unittest.TestCase):
    setUp = diagnostics_fixture.SQLiteDiagnosticsTests.setUp
    enable = diagnostics_fixture.SQLiteDiagnosticsTests.enable

    def test_one_changed_task_100_and_1000_with_unchanged_history_and_logger_modes(self):
        self.marker.unlink()
        target = make_target(self.root, enabled=True)
        selected, historical, _ = seed_reviews(target)
        caller = CallerIdentity("00000000-0000-4000-8000-000000000001")
        history_caller = CallerIdentity("00000000-0000-4000-8000-000000000004")
        with closing(storage.connect_initialized(target)) as c, c:
            cycles_task = tasks.add_task(c, target.project, title="Native completion history",
                status="in_progress", review_tier=0, caller=history_caller,
                verification_not_required_reason="Isolated non-executable history fixture").task["task_id"]
        logger = self.root / ".taskgov/diagnostics/logger"
        observations = []
        for task_count, history_count, cycles_count in ((100, 100, 2), (1000, 100, 2), (1000, 10000, 12)):
            with closing(storage.connect_initialized(target)) as c:
                present = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
                for index in range(present, task_count):
                    tasks.add_task(c, target.project, title=f"Unchanged scale task {index}")
                prior = c.execute("SELECT count(*) FROM task_events WHERE task_id=?", (historical,)).fetchone()[0]
                c.executemany("""INSERT INTO task_events(task_event_id,project_id,task_id,event_type,summary,created_at)
                    VALUES(?,?,?,'task_updated','Unchanged history fixture',?)""",
                    [(f"tg_event_{index:016x}", target.project.project_id, historical, FIXED_TIME)
                     for index in range(prior, history_count)])
                count = c.execute("SELECT count(*) FROM review_receipts WHERE task_id=?", (historical,)).fetchone()[0]
                for ordinal in range(count, cycles_count):
                    reviews.add_review_receipt(c, target.project, historical,
                        caller=CallerIdentity("00000000-0000-4000-8000-000000000002"),
                        reviewer=f"Unchanged history reviewer {ordinal}", kind="independent",
                        verdict="pass", summary="Unchanged history fixture", reviewer_class="human",
                        model_state="not_applicable", skill_state="not_applicable",
                        review_profiles=["general"], review_lenses=["correctness"],
                        context_relation="external_context", review_methods=["review_packet_inspection"])
                c.commit()
            extend_native_history(target, cycles_task, history_caller, cycles_count)
            self.assertEqual(service.publish_setup_viewer(target, observed_at=FIXED_TIME).code, "succeeded")
            for logging in (False, True):
                if logging:
                    logger_fixture.DebugLogTests.configure(self)
                elif (logger / "enabled").exists():
                    (logger / "enabled").unlink()
                with closing(storage.connect_initialized(target, operation_local=True)) as c:
                    tasks.edit_task(c, target.project, selected, caller=caller,
                        database_target=target, add_note="One selected change")
                    c.commit()
                counts, stages, sql, plans = Counter(), {}, [], []
                # Compare identical cold pure-predicate state in both logger
                # modes, independently of earlier samples and full baselines.
                _bounded_text_predicate.cache_clear()
                pending, active_transactions, lifetimes = {}, {}, []
                original_changes = service.changed_tasks
                def changes(c, *args, **kwargs):
                    statements = []
                    c.set_trace_callback(statements.append)
                    try:
                        return original_changes(c, *args, **kwargs)
                    finally:
                        c.set_trace_callback(None)
                        for statement in statements:
                            if "FROM viewer_task_revisions AS r" in statement:
                                plans.extend(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + statement))
                original_reader = service.read_task_projections
                def read(c, **kwargs):
                    self.assertEqual(kwargs["task_ids"], (selected,))
                    started = time.perf_counter_ns()
                    c.set_trace_callback(sql.append)
                    try:
                        return original_reader(c, **kwargs)
                    finally:
                        c.set_trace_callback(None)
                        stages["selected_source_ms"] = (time.perf_counter_ns() - started) / 1e6
                        for statement in sql:
                            normalized = " ".join(statement.lower().split())
                            if ("from aliases cross join review_receipts" in normalized
                                    or "select * from task_completion_cycles" in normalized
                                    or "from task_events where task_id in" in normalized
                                    or "from task_events as event left join" in normalized):
                                plans.extend(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + statement))
                def profile(frame, event, value):
                    if event == "call" and "task_governance_tool" in frame.f_code.co_filename:
                        if frame.f_code.co_name in {"_history", "validate_judgment_storage", "_read_review_evidence_with_materials"}:
                            counts[frame.f_code.co_name] += 1
                    if event not in {"c_call", "c_return", "c_exception"}:
                        return
                    connection = getattr(value, "__self__", None)
                    if isinstance(connection, sqlite3.Cursor):
                        connection = connection.connection
                    if not isinstance(connection, sqlite3.Connection):
                        return
                    key = (id(connection), value.__name__)
                    if event == "c_call":
                        try:
                            pending[key] = (connection.in_transaction, time.perf_counter_ns())
                        except sqlite3.ProgrammingError:
                            pass
                    elif key in pending:
                        before, started = pending.pop(key)
                        try:
                            after = connection.in_transaction
                        except sqlite3.ProgrammingError:
                            after = False  # successful native close ends its transaction
                        if not before and after:
                            ancestor = frame
                            write = False
                            while ancestor is not None:
                                write |= ancestor.f_code.co_name == "begin_initialized_write"
                                ancestor = ancestor.f_back
                            active_transactions[id(connection)] = (time.perf_counter_ns(), "write" if write else "read")
                        elif before and not after and id(connection) in active_transactions:
                            began, access = active_transactions.pop(id(connection))
                            lifetimes.append(dict(access=access, elapsed_ms=(time.perf_counter_ns() - began) / 1e6))
                def timed(name, function):
                    def call(*args, **kwargs):
                        started = time.perf_counter_ns()
                        try:
                            return function(*args, **kwargs)
                        finally:
                            stages[name] = stages.get(name, 0) + (time.perf_counter_ns() - started) / 1e6
                    return call
                def transaction(name, function):
                    @contextmanager
                    def scope(*args, **kwargs):
                        started = time.perf_counter_ns()
                        try:
                            with function(*args, **kwargs) as value:
                                yield value
                        finally:
                            stages[name] = stages.get(name, 0) + (time.perf_counter_ns() - started) / 1e6
                    return scope
                previous = sys.getprofile()
                sys.setprofile(profile)
                started = time.perf_counter_ns()
                try:
                    with mock.patch.object(service, "changed_tasks", side_effect=changes), \
                         mock.patch.object(service, "read_task_projections", side_effect=read), \
                         mock.patch.object(service, "load_candidate", side_effect=timed("cache_validate_ms", service.load_candidate)), \
                         mock.patch.object(service, "render_viewer_html", side_effect=timed("full_html_ms", service.render_viewer_html)), \
                         mock.patch.object(metadata, "_projection_writer", side_effect=transaction("writer_scope_ms", metadata._projection_writer)), \
                         mock.patch.object(service, "projection_snapshot", side_effect=transaction("capture_scope_ms", service.projection_snapshot)), \
                         debug_log.operation_scope(self.root, entrypoint=self.entrypoint,
                             operation="task", repo_explicit=True, validated=True) as log:
                        self.assertEqual(log is not None, logging)
                        result = service.run_routine_viewer_refresh(target, observed_at=FIXED_TIME)
                finally:
                    sys.setprofile(previous)
                stages["wall_ms"] = (time.perf_counter_ns() - started) / 1e6
                self.assertEqual(result.code, "succeeded")
                self.assertEqual(dict(counts), dict(_history=1, validate_judgment_storage=1,
                                                  _read_review_evidence_with_materials=1))
                self.assertTrue(any("SEARCH receipt" in plan for plan in plans), plans)
                self.assertTrue(any("SEARCH finding" in plan for plan in plans), plans)
                self.assertTrue(any("SEARCH task_completion_cycles" in plan for plan in plans), plans)
                self.assertTrue(any("SEARCH task_events" in plan for plan in plans), plans)
                self.assertTrue(any("SEARCH event USING INDEX idx_task_events_task_created" in plan
                                    for plan in plans), plans)
                self.assertTrue(any("SEARCH cycle USING INDEX sqlite_autoindex_task_completion_cycles" in plan
                                    for plan in plans), plans)
                self.assertFalse(any("SCAN event" in plan or "SCAN cycle" in plan for plan in plans), plans)
                self.assertTrue(any("SEARCH r USING" in plan and "source_generation>? AND source_generation<?" in plan
                                    for plan in plans), plans)
                self.assertFalse(any("SCAN finding" in plan or "SCAN receipt" in plan for plan in plans), plans)
                self.assertFalse(active_transactions)
                self.assertTrue(any(item["access"] == "read" for item in lifetimes))
                self.assertEqual(sum(item["access"] == "write" for item in lifetimes), 2)
                # Source queries may join the selected parents, never select the
                # unrelated Task or its enlarged historical collection.
                self.assertFalse(any(historical in statement for statement in sql))
                self.assertFalse(any(cycles_task in statement for statement in sql))
                with closing(storage.connect_snapshot_readonly(target.db_path)) as c:
                    full = viewer.build_viewer_snapshot(c, target, generated_at=FIXED_TIME)
                saved = cache.load_candidate(cache.cache_output(target))
                self.assertEqual(saved.tasks, full.snapshot["tasks"])
                self.assertEqual(target.resolved_viewer_path.read_text(encoding="utf-8"), viewer.render_viewer_html(full.snapshot))
                observations.append(dict(tasks=task_count, unchanged_events=history_count, unchanged_cycles=cycles_count,
                    logger="debug" if logging else "off", text_predicates="cold", validators=dict(counts),
                    selected_sql_count=len(sql), plan=plans, inclusive_timings=stages,
                    native_transaction_lifetimes=lifetimes))
        for mode in ("off", "debug"):
            rows = [item for item in observations if item["logger"] == mode]
            for row in rows[1:]:
                self.assertEqual(rows[0]["selected_sql_count"], row["selected_sql_count"])
                self.assertEqual(rows[0]["validators"], row["validators"])
        records = logger_fixture.DebugLogTests.records(self)
        self.assertTrue(any(row["event"] == "db.transaction" for row in records))
        self.assertTrue(any(row["event"] == "diagnostic.summary" for row in records))
        PROFILES.extend(observations)


if __name__ == "__main__":
    unittest.main()
