"""Eight additional phase pages preserve durable progress and fresh fences."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest import mock

from tests.usage_phase_page_support import PHASES, fixture, add_tasks_and_sources
from tests.test_usage_collection import THREAD
from task_governance_tool import storage, tasks
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool import usage_reconciliation_runtime as runtime
from task_governance_tool import usage_reconciliation_uncertainty as uncertainty
from task_governance_tool import usage_reconciliation_pending as pending
from task_governance_tool import usage_reconciliation_overlays as overlays
from task_governance_tool import usage_reconciliation_membership as membership
from task_governance_tool.usage_values import UsageError


UNITS = {
    "operations": (runtime, "_resolve_one"), "participants": (runtime, "_participant_one"),
    "event_locations": (uncertainty, "locate_one"), "pending_source": (pending, "source_one"),
    "uncertainty_rule": (uncertainty, "rebuild_one"), "interval_metadata": (overlays, "interval_metadata_one"),
    "diagnostics": (membership, "reconcile_thread_diagnostics"), "execution": (membership, "reconcile_execution"),
}


class AdditionalPhasePageTests(unittest.TestCase):
    def setUp(self):
        self.support = fixture(cycles=3, turns=4)
        self.addCleanup(self.support.doCleanups)
        add_tasks_and_sources(self.support, tasks_count=3, sources=3, distinct_threads=True)
        self.repository, self.reader = self.support.repository, self.support.reader
        self.pristine = self.repository.path.read_bytes()

    def restore(self):
        # All connections are closed; retain the same isolated physical file.
        self.repository.path.write_bytes(self.pristine)
        self.repository = type(self.repository)(self.repository.path, *self.repository.basis)
        self.support.repository = self.support.fixture.repository = self.repository

    def state(self):
        with self.repository.connection() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'usage_reconcile_*'")]
            return {name: sorted(map(tuple, connection.execute('SELECT * FROM "' + name + '"')), key=repr)
                    for name in tables}

    def first_commit(self, phase):
        committed = set()
        for advance, end in ((runtime.advance_initial, "effects_pending"), (runtime.advance_effects, "classification_pending")):
            for _ in range(1000):
                result = advance(self.repository, self.reader, committed_phases=committed)
                committed.add(result["phase"])
                if result["phase"] == phase:
                    return advance, committed
                if result["phase"] == end:
                    break
        self.fail(f"real fixture did not reach {phase}")

    def test_real_units_all_phases_first_commit_rollback_reopen_and_full_replay(self):
        expected = self.support.assert_same()
        for phase, (module, name) in UNITS.items():
            with self.subTest(phase=phase):
                self.restore()
                calls = []
                original = getattr(module, name)
                first_state = None
                def observed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if result is not None or phase in {"diagnostics", "execution"}:
                        calls.append(result)
                        if len(calls) == 3:
                            raise UsageError("usage_pending")
                    return result
                with mock.patch.object(module, name, observed), mock.patch.object(runtime, "monotonic", return_value=0):
                    advance, committed = self.first_commit(phase)
                    self.assertEqual(len(calls), 1)
                    first_state = self.state()
                    with self.assertRaisesRegex(UsageError, "usage_pending"):
                        advance(self.repository, self.reader, committed_phases=committed)
                self.assertEqual(len(calls), 3)
                self.assertEqual(self.state(), first_state)
                # A new run makes its first unit durable again, not just the
                # first unit ever processed by this persisted phase.
                self.repository = type(self.repository)(self.repository.path, *self.repository.basis)
                self.support.repository = self.repository
                with mock.patch.object(module, name, wraps=original) as watched:
                    advance(self.repository, self.reader)
                    self.assertEqual(watched.call_count, 1)
                self.assertEqual(self.support.assert_same(), expected)

    def test_real_empty_phases_no_new_page_and_unchanged_full_replay(self):
        expected = self.support.assert_same()
        before = self.state()
        with mock.patch.object(runtime, "_phase_page", side_effect=AssertionError("empty phase page")):
            self.assertEqual(runtime.advance_initial(self.repository, self.reader)["phase"], "effects_pending")
            self.assertEqual(runtime.advance_effects(self.repository, self.reader)["phase"], "classification_pending")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.support.assert_same(), expected)

    def test_all_phase_pages_are_bounded_and_preserve_selection_order(self):
        # Real single-unit removals of obsolete identities exercise the same
        # selectors; real populated records and mutations are covered above.
        self.support.assert_same()
        ready = self.repository.path.read_bytes()
        for phase in PHASES:
            for count in (0, 1, 127, 128, 129, 258):
                with self.subTest(phase=phase, count=count):
                    self.repository.path.write_bytes(ready)
                    rows = [f"retired-{i:04}" for i in range(count)]
                    table, columns, values = {
                        "operations": ("task_work", "task_id", [(x,) for x in rows]),
                        "participants": ("participant_work", "thread_id", [(x,) for x in rows]),
                        "event_locations": ("event_work", "transition_id,task_id,thread_id,source_order",
                                            [(x, "retired-task", None, i) for i, x in enumerate(rows)]),
                        "pending_source": ("pending_work", "source_id,after_document", [(x, None) for x in rows]),
                        "uncertainty_rule": ("rule_work", "kind,origin", [("missing", x) for x in rows]),
                        "interval_metadata": ("interval_work", "kind,origin,after_document", [("fallback", x, None) for x in rows]),
                        "diagnostics": ("diagnostic_work", "thread_id", [(x,) for x in rows]),
                        "execution": ("execution_work", "execution_id", [(x,) for x in rows]),
                    }[phase]
                    with self.repository.connection(write=True) as connection:
                        marks = ",".join("?" for _ in columns.split(","))
                        connection.executemany(f"INSERT INTO usage_reconcile_{table}({columns}) VALUES ({marks})", values)
                    advance = runtime.advance_initial if phase in PHASES[:2] else runtime.advance_effects
                    committed, pages = set(), []
                    with mock.patch.object(runtime, "monotonic", return_value=0):
                        remaining = list(values)
                        while remaining:
                            result = advance(self.repository, self.reader, committed_phases=committed)
                            self.assertEqual(result["phase"], phase)
                            committed.add(phase)
                            with self.repository.connection() as connection:
                                after = list(map(tuple, connection.execute(f"SELECT {columns} FROM usage_reconcile_{table} ORDER BY rowid")))
                            consumed = len(remaining) - len(after)
                            self.assertGreater(consumed, 0)
                            self.assertEqual(after, remaining[consumed:])
                            pages.append(consumed)
                            remaining = after
                    wanted = [] if not count else [1] + [128] * ((count - 1) // 128)
                    if count > 1 and (count - 1) % 128:
                        wanted.append((count - 1) % 128)
                    self.assertEqual(pages, wanted)

    def test_each_phase_time_allowance_and_deadline_margin(self):
        from task_governance_tool import usage_incremental_repository as incremental
        for phase, (module, name) in UNITS.items():
            for deadline, ticks, wanted in ((float("inf"), (10, 10.249, 10.250), 2),
                                            (10.075, (10, 10.025), 1), (10.020, (10, 10), 1)):
                with self.subTest(phase=phase, deadline=deadline):
                    self.restore()
                    advance, committed = self.first_commit(phase)
                    self.repository.deadline = deadline
                    try:
                        with mock.patch.object(incremental, "monotonic", return_value=10), \
                                mock.patch.object(runtime, "monotonic", side_effect=ticks), \
                                mock.patch.object(module, name, wraps=getattr(module, name)) as watched:
                            self.assertEqual(advance(self.repository, self.reader, committed_phases=committed)["phase"], phase)
                            self.assertEqual(watched.call_count, wanted)
                    finally:
                        self.repository.deadline = float("inf")
                    self.support.assert_same()

    def test_new_upstream_work_preempts_each_selected_phase(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.restore()
                advance, committed = self.first_commit(phase)
                with self.repository.connection(write=True) as connection:
                    connection.execute("INSERT OR IGNORE INTO usage_reconcile_task_work VALUES (?)", (self.support.fixture.task_id,))
                before = self.state()
                if phase in PHASES[:2]:
                    self.assertEqual(advance(self.repository, self.reader, committed_phases=committed)["phase"], "operations")
                else:
                    with self.assertRaisesRegex(UsageError, "usage_pending"):
                        advance(self.repository, self.reader, committed_phases=committed)
                    self.assertEqual(self.state(), before)
                self.support.assert_same()

    def test_core_writer_between_new_phase_pages_and_fresh_publication_guard(self):
        advance, committed = self.first_commit("event_locations")
        entered, committing = Event(), Event()
        def edit():
            self.assertTrue(entered.wait(10))
            with closing(storage.connect_initialized(self.support.fixture.target)) as core:
                core.set_trace_callback(lambda sql: committing.set() if sql.strip().upper() == "COMMIT" else None)
                tasks.edit_task(core, self.support.fixture.target.project, self.support.fixture.task_id,
                    database_target=self.support.fixture.target, caller=CallerIdentity(THREAD), title="Between phase pages")
                core.commit()
        original = uncertainty.locate_one
        def locate(connection):
            if not entered.is_set():
                entered.set()
                self.assertTrue(committing.wait(10))
                self.assertFalse(writer.done())
            return original(connection)
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(edit)
            with mock.patch.object(uncertainty, "locate_one", locate), mock.patch.object(runtime, "monotonic", return_value=0):
                advance(self.repository, self.reader, committed_phases=committed)
            writer.result(timeout=10)
        before = self.state()
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            advance(self.repository, self.reader, committed_phases=committed)
        self.assertEqual(self.state(), before)
        self.support.assert_same()
        index, state = self.repository.prepare_publication(self.reader)
        for identity in index["snapshots"][state["position"]:]:
            self.repository.publication_snapshot(identity)
            state = self.repository.advance_publication(state)
        # A classified snapshot cannot certify later queue changes.
        with self.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_reconcile_diagnostic_work VALUES (?)", (THREAD,))
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            with self.repository.publication_guard(self.reader, index, state):
                self.fail("stale phase result admitted for publication")
        self.support.assert_same()


if __name__ == "__main__":
    unittest.main()
