"""Bounded phase pages retain atomic progress and fresh admission fences."""

from collections import Counter
from contextlib import closing, ExitStack
import json
import subprocess
import sys
import unittest
from unittest import mock

from tests import test_usage_reconciliation_changes_runtime as changed
from tests.test_usage_attribution import turn
from tests.test_usage_collection import THREAD, event, usage
from task_governance_tool import storage, tasks
from task_governance_tool import usage_reconciliation_runtime as runtime
from task_governance_tool import usage_reconciliation_overlays as overlays
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_values import UsageError


PHASES = ("turn_inputs", "allocation", "base_effects", "turn_overlays")


class PhasePageTests(unittest.TestCase):
    def setUp(self):
        self.support = changed.ChangedRuntimeTests()
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)
        self.fixture = self.support.fixture
        self.repository, self.reader = self.support.repository, self.support.reader

    def queue_overlays(self, count):
        self.support.assert_same()
        with self.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_reconcile_overlay_turn_work VALUES (?,?)",
                                   [(THREAD, turn(n)) for n in range(1, count + 1)])

    def remaining(self):
        with self.repository.connection() as connection:
            return connection.execute("SELECT COUNT(*) FROM usage_reconcile_overlay_turn_work").fetchone()[0]

    def real_allocations_after_first_commit(self, count):
        rows = []
        for number in range(2, count + 1):
            rows.extend((event("event_msg", type="task_started", turn_id=turn(number), started_at=number),
                         event("turn_context", turn_id=turn(number), model="fixture-model"),
                         usage(f"r{number}", turn=turn(number))))
        self.support.append(*rows)
        committed = set()
        for _ in range(1000):
            result = runtime.advance_initial(self.repository, self.reader, committed_phases=committed)
            if result["phase"] == "allocation":
                break
            committed.add(result["phase"])
        else:
            self.fail("real allocation work did not become ready")
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_reconcile_turn_queue").fetchone()[0], count - 1)
            documents = [json.loads(row[0]) for row in connection.execute("SELECT document FROM usage_reconcile_turn_allocations")]
            self.assertEqual(len(documents), 1)
            self.assertTrue(any(members for _, members in documents[0]["owners"]))

    def reconciliation_state(self):
        # Snapshot every derived row and queue in this isolated fixture, so a
        # surviving allocation, invalidation or queue removal cannot be hidden.
        with self.repository.connection() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'usage_reconcile_*'")]
            return {table: sorted(map(tuple, connection.execute('SELECT * FROM "' + table + '"')), key=repr)
                    for table in tables}

    def test_zero_one_127_128_129_and_multiple_pages(self):
        for count in (0, 1, 127, 128, 129, 258):
            with self.subTest(count=count):
                self.queue_overlays(count)
                visited, pages = [], []
                original = overlays.apply_turn
                def apply(connection, *key):
                    visited.append(key)
                    return original(connection, *key)
                committed = set()
                with mock.patch.object(overlays, "apply_turn", apply), mock.patch.object(runtime, "monotonic", return_value=0):
                    while self.remaining():
                        before = len(visited)
                        result = runtime.advance_effects(self.repository, self.reader, committed_phases=committed)
                        committed.add(result["phase"])
                        if result["phase"] == "turn_overlays":
                            pages.append(len(visited) - before)
                expected = [] if not count else [1] + [128] * ((count - 1) // 128)
                if count > 1 and (count - 1) % 128:
                    expected.append((count - 1) % 128)
                self.assertEqual(pages, expected)
                self.assertEqual(visited, [(THREAD, turn(n)) for n in range(1, count + 1)])
                self.support.assert_same()

    def test_real_129_turns_all_four_phases_and_independent_full_replay(self):
        rows = []
        for number in range(2, 130):
            rows.extend((event("event_msg", type="task_started", turn_id=turn(number), started_at=number),
                         event("turn_context", turn_id=turn(number), model="fixture-model"),
                         usage(f"r{number}", turn=turn(number))))
        self.support.append(*rows)
        units, pages = Counter(), {phase: [] for phase in PHASES}
        with ExitStack() as stack:
            for owner, name, phase in ((runtime, "stage_one", "turn_inputs"), (runtime, "replay_one", "allocation"),
                                        (runtime, "apply_base_one", "base_effects"), (overlays, "apply_turn", "turn_overlays")):
                original = getattr(owner, name)
                def observed(*args, _original=original, _phase=phase, **kwargs):
                    value = _original(*args, **kwargs)
                    if value is not None:
                        units[_phase] += 1
                    return value
                stack.enter_context(mock.patch.object(owner, name, observed))
            for name in ("advance_initial", "advance_effects"):
                original = getattr(runtime, name)
                def page(*args, _original=original, **kwargs):
                    before = units.copy()
                    result = _original(*args, **kwargs)
                    phase = result["phase"]
                    if phase in pages:
                        pages[phase].append(units[phase] - before[phase])
                        self.assertEqual({key for key in units if units[key] != before[key]}, {phase})
                    return result
                stack.enter_context(mock.patch.object(runtime, name, page))
            stack.enter_context(mock.patch.object(runtime, "monotonic", return_value=0))
            self.support.assert_same()
        for phase in PHASES:
            self.assertEqual(pages[phase][0], 1, phase)
            self.assertIn(128, pages[phase], phase)
            self.assertTrue(all(1 <= count <= 128 for count in pages[phase]), pages)
        # Late numerical input and changed attribution still converge to the
        # independent full classifier; replay itself does not change output.
        expected = self.support.assert_same()
        self.support.append(usage("late", turn=turn(64)))
        self.assertNotEqual(self.support.assert_same(), expected)
        self.support.append(usage("late", turn=turn(64)))
        replay = self.support.assert_same()
        self.assertEqual(self.support.assert_same(), replay)

    def test_page_time_and_deadline_margin_stop_optional_unit_start(self):
        from task_governance_tool import usage_incremental_repository as incremental
        for deadline, ticks in ((float("inf"), (10, 10.249, 10.250)), (10.075, (10, 10.025)),
                                (10.020, (10, 10))):
            with self.subTest(deadline=deadline):
                self.queue_overlays(4)
                self.repository.deadline = deadline
                try:
                    with mock.patch.object(incremental, "monotonic", return_value=10), \
                            mock.patch.object(runtime, "monotonic", side_effect=ticks):
                        runtime.advance_effects(self.repository, self.reader, committed_phases=PHASES)
                finally:
                    self.repository.deadline = float("inf")
                self.assertEqual(self.remaining(), 2 if len(ticks) == 3 else 3)
                self.support.assert_same()

    def test_failure_rolls_back_entire_page_but_retains_first_commit(self):
        self.real_allocations_after_first_commit(130)
        before = self.reconciliation_state()
        apply, visited = runtime.replay_one, []
        def interrupted(connection, **options):
            value = apply(connection, **options)
            visited.append(value)
            if len(visited) == 100:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_reconcile_turn_allocations").fetchone()[0], 101)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_reconcile_turn_queue").fetchone()[0], 29)
                self.assertTrue(all(row["changed"] for row in visited))
                self.assertTrue(all(any(members for _, members in json.loads(row[0])["owners"])
                    for row in connection.execute("SELECT document FROM usage_reconcile_turn_allocations")))
                raise UsageError("usage_pending")
            return value
        with mock.patch.object(runtime, "replay_one", interrupted), mock.patch.object(runtime, "monotonic", return_value=0):
            with self.assertRaisesRegex(UsageError, "usage_pending"):
                runtime.advance_initial(self.repository, self.reader, committed_phases=PHASES)
        self.assertEqual(len(visited), 100)
        self.assertEqual(self.reconciliation_state(), before)
        self.support.repository = type(self.repository)(self.repository.path, *self.repository.basis)
        self.support.assert_same()
        self.assertEqual(self.remaining(), 0)

    def test_process_exit_keeps_uncommitted_page_pending(self):
        self.real_allocations_after_first_commit(4)
        before = self.reconciliation_state()
        # The child mutates real allocations and invalidations before exiting
        # without unwinding the same page writer. SQLite must recover all rows.
        code = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_reconciliation_turns import replay_one
r = UsageReconciliationRepository(Path(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5]))
with r.connection(write=True) as connection:
    def task(execution):
        return connection.execute('SELECT DISTINCT task_id FROM usage_reconcile_events WHERE execution_id=?', (execution,)).fetchone()[0]
    for _ in range(2):
        assert replay_one(connection, execution_task=task)['changed']
    documents = [json.loads(row[0]) for row in connection.execute('SELECT document FROM usage_reconcile_turn_allocations')]
    assert len(documents) == 3 and all(any(members for _, members in doc['owners']) for doc in documents)
    assert connection.execute('SELECT COUNT(*) FROM usage_reconcile_turn_queue').fetchone()[0] == 1
    os._exit(23)
"""
        from pathlib import Path
        scripts = Path(runtime.__file__).parents[1]
        result = subprocess.run([sys.executable, "-B", "-c", code, str(scripts), str(self.repository.path),
                                 *map(str, self.repository.basis)], timeout=15, capture_output=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.reconciliation_state(), before)
        self.support.repository = type(self.repository)(self.repository.path, *self.repository.basis)
        self.support.assert_same()

    def test_core_writer_progresses_between_pages_and_next_page_rejects_stale_basis(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        self.queue_overlays(130)
        entered, committing = Event(), Event()
        def edit():
            self.assertTrue(entered.wait(5))
            with closing(storage.connect_initialized(self.fixture.target)) as core:
                core.set_trace_callback(lambda sql: committing.set() if sql.strip().upper() == "COMMIT" else None)
                tasks.edit_task(core, self.fixture.target.project, self.fixture.task_id,
                    database_target=self.fixture.target, caller=CallerIdentity(THREAD), title="Between turn pages")
                core.commit()
        original = overlays.apply_turn
        def apply(connection, *key):
            if not entered.is_set():
                entered.set()
                self.assertTrue(committing.wait(5))
                self.assertFalse(writer.done())
            return original(connection, *key)
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(edit)
            with mock.patch.object(overlays, "apply_turn", apply), mock.patch.object(runtime, "monotonic", return_value=0):
                runtime.advance_effects(self.repository, self.reader, committed_phases=PHASES)
            writer.result(timeout=5)
        self.assertEqual(self.remaining(), 2)
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            runtime.advance_effects(self.repository, self.reader, committed_phases=PHASES)
        self.assertEqual(self.remaining(), 2)
        self.support.assert_same()


if __name__ == "__main__":
    unittest.main()
