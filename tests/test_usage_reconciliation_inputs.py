"""Exact-turn staging is compared with the unchanged full classifier."""

from collections import defaultdict
import json
import unittest
from unittest import mock

from tests import test_usage_response_repository as support
from tests.test_usage_response_repository import A, B
from tests.test_usage_collection import THREAD, event, usage
from tests.test_usage_attribution import turn
from task_governance_tool.usage_attribution_repository import core_transitions
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool import usage_reconciliation_events as events
from task_governance_tool import usage_reconciliation_inputs as inputs
from task_governance_tool.usage_reconciliation_turns import replay_one
from task_governance_tool.usage_values import UsageError


class ReconciliationInputTests(unittest.TestCase):
    records = support.ResponseRepositoryTests.records
    write = support.ResponseRepositoryTests.write
    append = support.ResponseRepositoryTests.append
    reader = support.ResponseRepositoryTests.reader
    acknowledgement = support.ResponseRepositoryTests.acknowledgement
    operation = support.ResponseRepositoryTests.operation
    write_turn = support.ResponseRepositoryTests.write_turn
    ingest = support.ResponseRepositoryTests.ingest

    def setUp(self):
        support.ResponseRepositoryTests.setUp(self)
        self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
        self.repository.initialize()

    def resolve(self, tasks=(A, B)):
        with self.reader() as core, self.repository.connection(write=True) as connection:
            transitions = core_transitions(core, self.repository.basis[0])
            ordinals = {row["transition_id"]: row["source_order"] for row in core.execute(
                "SELECT rowid AS source_order,transition_id FROM task_owner_transitions")}
            return {task: events.reconcile_task(self.repository, core, connection, task,
                [(ordinals[change.transition_id], change) for change in transitions if change.task_id == task]) for task in tasks}

    def stage(self, number):
        with self.reader() as core, self.repository.connection(write=True) as connection:
            return inputs.stage_input_turn(self.repository, core, connection, THREAD, turn(number))

    def replay(self):
        results = []
        for _ in range(1000):
            with self.repository.connection(write=True) as connection:
                def task(execution):
                    row = connection.execute("SELECT DISTINCT task_id FROM usage_reconcile_events WHERE execution_id=?", (execution,)).fetchall()
                    self.assertEqual(len(row), 1)
                    return row[0][0]
                item = replay_one(connection, execution_task=task)
            if item is None:
                return results
            results.append(item)
        self.fail("replay did not converge")

    def owners(self):
        owners = defaultdict(set)
        with self.repository.connection() as connection:
            for row in connection.execute("SELECT allocation.document FROM usage_reconcile_turn_allocations allocation "
                "JOIN usage_reconcile_turn_inputs input USING(thread_id,turn_id)"):
                for key, members in json.loads(row[0])["owners"]:
                    if members:
                        owners[tuple(key)].update(members)
        return dict(owners)

    def assert_full_owners(self):
        with self.reader() as core:
            expected = self.repository.attribution(core, geometry_only=True)
        owners = defaultdict(set)
        for component in expected["components"]:
            for key in component["response_keys"]:
                owners[tuple(key)].update(component["executions"])
        self.assertEqual(self.owners(), dict(owners))

    def test_three_periods_same_turn_preserve_core_order_and_do_not_restage_noop(self):
        rows = []
        for task, number in ((A, 1), (B, 2), (A, 3)):
            rows += self.operation(f"start{number}", self.acknowledgement(task, number, "in_progress"), f"r{number}a", direct=True)
            rows += self.operation(f"done{number}", self.acknowledgement(task, number, "done"), f"r{number}b", direct=True)
        self.write_turn(rows)
        self.ingest()
        self.resolve()
        self.assertTrue(self.stage(1)["changed"])
        self.assertEqual(len(self.replay()), 1)
        self.assert_full_owners()
        self.resolve()
        self.assertFalse(self.stage(1)["changed"])
        self.assertEqual(self.replay(), [])

    def test_late_operation_changes_the_same_original_turn_without_new_numbers(self):
        start = self.acknowledgement(A, 1, "in_progress")
        self.write_turn(self.operation("start", start, "r1", direct=True))
        self.ingest()
        # The core acknowledgement may become visible after the source record.
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(self.core_path)) as core:
            saved = core.execute("SELECT * FROM task_events").fetchone()
            core.execute("DELETE FROM task_events")
            core.commit()
        self.resolve()
        self.stage(1)
        self.replay()
        self.assertEqual(self.owners(), {})
        with closing(sqlite3.connect(self.core_path)) as core:
            core.execute("INSERT INTO task_events VALUES (?,?,?,?)", saved)
            core.commit()
        self.resolve()
        self.stage(1)
        self.replay()
        self.assert_full_owners()
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_responses").fetchone()[0], 1)
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_unresolved").fetchone())

    def test_equal_turn_replay_does_not_republish_serialized_owner_actions(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1", direct=True))
        self.ingest()
        self.resolve()
        self.stage(1)
        self.replay()
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_reconcile_turn_effects")
            connection.execute("INSERT INTO usage_reconcile_turn_queue(thread_id,turn_id,started_at) VALUES (?,?,?)", (THREAD, turn(1), 1))
        self.assertFalse(self.replay()[0]["changed"])
        with self.repository.connection() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_turn_effects").fetchone())

    def test_removed_turn_replays_old_successor_and_retains_old_effects(self):
        start = self.acknowledgement(A, 1, "in_progress")
        self.write_turn(self.operation("start", start, "r1", direct=True) + [
            event("event_msg", type="task_started", turn_id=turn(2), started_at=2),
            event("turn_context", turn_id=turn(2), model="fixture-model"), usage("r2", turn=turn(2))])
        self.ingest()
        self.resolve()
        self.stage(1)
        self.stage(2)
        self.replay()
        self.assertIn(("openai", "r2"), self.owners())
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_turns WHERE turn_id=?", (turn(1),))
        self.assertFalse(self.stage(1)["ordered"])
        self.assertEqual([item["turn_id"] for item in self.replay()], [turn(2)])
        self.assertEqual(self.owners(), {})
        with self.repository.connection() as connection:
            self.assertIsNotNone(connection.execute("SELECT 1 FROM usage_reconcile_turn_effects WHERE turn_id=?", (turn(1),)).fetchone())

    def test_same_clock_peer_removal_reestablishes_previously_unorderable_turn(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1", direct=True))
        self.ingest()
        self.resolve()
        with self.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_turns VALUES (?,?,?)", (THREAD, turn(2), 1))
        self.assertFalse(self.stage(1)["ordered"])
        self.stage(2)
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_turns WHERE turn_id=?", (turn(2),))
        self.stage(2)
        with self.repository.connection() as connection:
            self.assertIsNotNone(connection.execute("SELECT 1 FROM usage_reconcile_input_turn_work WHERE turn_id=?", (turn(1),)).fetchone())
        self.assertTrue(self.stage(1)["ordered"])
        self.replay()
        self.assert_full_owners()

    def test_failed_staging_does_not_acknowledge_work_or_change_committed_allocation(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1", direct=True))
        self.ingest()
        self.resolve()
        with self.repository.connection() as connection:
            before = list(map(tuple, connection.execute("SELECT * FROM usage_reconcile_input_turn_work")))
        with mock.patch.object(inputs, "stage_turn", side_effect=UsageError("usage_pending")):
            with self.assertRaises(UsageError):
                self.stage(1)
        with self.repository.connection() as connection:
            self.assertEqual(list(map(tuple, connection.execute("SELECT * FROM usage_reconcile_input_turn_work"))), before)
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_ordered_inputs").fetchone())


class ReconciliationChangeTests(ReconciliationInputTests):
    def consume(self):
        from task_governance_tool.usage_reconciliation_changes import consume_page
        total = 0
        for _ in range(100):
            with self.repository.connection(write=True) as connection:
                count = consume_page(self.repository, connection)["selected"]
            total += count
            if count == 0:
                return total
        self.fail("change queue did not drain")

    def initial(self):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1", direct=True))
        self.ingest()
        self.consume()

    def queues(self):
        with self.repository.connection() as connection:
            return {name: list(map(tuple, connection.execute("SELECT * FROM " + name))) for name in (
                "usage_reconcile_task_work", "usage_reconcile_participant_work", "usage_reconcile_diagnostic_work",
                "usage_reconcile_source_work", "usage_reconcile_pending_work", "usage_reconcile_input_turn_work")}

    def clear(self):
        with self.repository.connection(write=True) as connection:
            for name in self.queues():
                connection.execute("DELETE FROM " + name)

    def test_offset_only_change_does_not_invalidate_source_history(self):
        self.initial()
        self.clear()
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET offset=offset+1")
        self.assertGreater(self.consume(), 0)
        queues = self.queues()
        # There is no interval, Task, source, uncertainty or diagnostic work
        # merely because the byte cursor moved.
        self.assertTrue(all(not rows for rows in queues.values()), queues)
        self.assertEqual(self.consume(), 0)

    def test_removed_historical_origin_rechecks_current_consumers(self):
        from dataclasses import asdict, replace
        from task_governance_tool.usage_evidence import digest, encode
        self.initial()
        self.clear()
        with self.repository.connection(write=True) as connection:
            source, incarnation, item = self.repository._relations(connection)[1][0]
            old = incarnation + 10
            connection.execute("INSERT INTO usage_incarnations SELECT source_id,?,offset,prefix,file_id FROM usage_sources", (old,))
            value = asdict(replace(item, response_id="conflicting-origin", method="explicit"))
            identity = digest(value)
            connection.execute("INSERT INTO usage_response_boundaries VALUES (?,?,?,?)", (source, old, identity, encode(value).decode()))
        self.consume()
        self.assertIn((A,), self.queues()["usage_reconcile_task_work"])
        self.clear()
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_boundaries WHERE observation_id=?", (identity,))
        self.consume()
        self.assertIn((A,), self.queues()["usage_reconcile_task_work"])
        self.assertIn((THREAD, turn(1)), self.queues()["usage_reconcile_input_turn_work"])

    def test_gap_removal_queues_diagnostics_without_replaying_base_geometry(self):
        self.initial()
        self.repository.record_gap(self.source.source_id, THREAD, "source_unreadable")
        self.consume()
        self.clear()
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_diagnostics WHERE code='source_unreadable'")
        self.consume()
        queues = self.queues()
        self.assertEqual(queues["usage_reconcile_diagnostic_work"], [(THREAD,)])
        self.assertEqual(queues["usage_reconcile_input_turn_work"], [])
        self.assertEqual(queues["usage_reconcile_task_work"], [])

    def test_failed_change_page_keeps_marks_and_does_not_adopt_source(self):
        from task_governance_tool import usage_reconciliation_changes as changes
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r1", direct=True))
        self.ingest()
        with self.repository.connection() as connection:
            original = list(map(tuple, connection.execute("SELECT * FROM usage_reconcile_changes")))
        with mock.patch.object(changes, "_turn_dependencies", side_effect=UsageError("usage_pending")):
            with self.assertRaises(UsageError):
                self.consume()
        with self.repository.connection() as connection:
            self.assertEqual(list(map(tuple, connection.execute("SELECT * FROM usage_reconcile_changes"))), original)
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_sources").fetchone())

    def test_late_original_with_different_turn_invalidates_earlier_explicit_boundary(self):
        start = self.acknowledgement(A, 1, "in_progress")
        rows = self.operation("start", start, "r1", direct=True)
        self.write_turn([rows[0], rows[2]])  # Explicit origin precedes counters.
        self.ingest()
        self.consume()
        self.resolve()
        self.stage(1)
        self.replay()
        with self.repository.connection() as connection:
            self.assertIsNotNone(json.loads(connection.execute("SELECT document FROM usage_reconcile_events WHERE task_id=?", (A,)).fetchone()[0])["boundary"])
        self.clear()
        self.append(event("event_msg", type="task_started", turn_id=turn(2), started_at=2),
                    event("turn_context", turn_id=turn(2), model="fixture-model"), usage("r1", turn=turn(2)))
        self.ingest()
        self.consume()
        self.assertIn((A,), self.queues()["usage_reconcile_task_work"])
        self.assertIn((THREAD, turn(1)), self.queues()["usage_reconcile_input_turn_work"])
        self.resolve()
        with self.repository.connection() as connection:
            self.assertIsNone(json.loads(connection.execute("SELECT document FROM usage_reconcile_events WHERE task_id=?", (A,)).fetchone()[0])["boundary"])

    def test_counter_and_conflict_changes_always_reach_response_work(self):
        self.initial()
        for change in ("UPDATE usage_responses SET input_tokens=input_tokens+1,total_tokens=total_tokens+1",
                       "INSERT INTO usage_conflicts VALUES ('openai','r1')", "DELETE FROM usage_conflicts"):
            with self.repository.connection(write=True) as connection:
                connection.execute("DELETE FROM usage_reconcile_response_work")
                connection.execute(change)
            self.consume()
            with self.repository.connection() as connection:
                self.assertEqual(list(map(tuple, connection.execute("SELECT * FROM usage_reconcile_response_work"))), [("openai", "r1")])

    def test_dirty_response_does_not_scan_unrelated_operation_history(self):
        from task_governance_tool.usage_reconciliation_changes import consume_page
        self.initial()
        def measured():
            with self.repository.connection(write=True) as connection:
                connection.execute("DELETE FROM usage_reconcile_changes")
                connection.execute("UPDATE usage_responses SET input_tokens=input_tokens+1,total_tokens=total_tokens+1")
                steps = [0]
                def progress():
                    steps[0] += 1
                    return 0
                connection.set_progress_handler(progress, 1)
                try:
                    consume_page(self.repository, connection)
                finally:
                    connection.set_progress_handler(None, 0)
                return steps[0]
        small = measured()
        with self.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_operation_turns VALUES (?,?,?,?,?,?,?)", [
                (f"tg_event_{number + 1000:016x}", THREAD, turn(number + 100), self.repository.basis[0], f"tg_task_{number + 1000:016x}", 1, "in_progress")
                for number in range(3000)])
        large = measured()
        self.assertLessEqual(large, small + 1000)


if __name__ == "__main__":
    unittest.main()
