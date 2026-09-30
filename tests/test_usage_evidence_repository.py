"""Immutable numerical capture, exact completion periods, replay and migration."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from unittest import mock

from tests import test_usage_collection as collection_tests
from tests.test_usage_collection import THREAD, event, usage
from tests.test_usage_attribution import turn
from task_governance_tool.usage_repository import UsageRepository
from task_governance_tool.usage_attribution_repository import UsageAttributionRepository
from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository
from task_governance_tool.usage_turn_adapter import OperationObservation, TurnObservation
from task_governance_tool.usage_values import UsageError


def execution(number):
    return f"tg_execution_{number:016x}"


class UsageEvidenceRepositoryTests(collection_tests.UsageCollectionTests):
    def setUp(self):
        super().setUp()
        self.old = self.repository
        self.repository = UsageEvidenceRepository(self.old.path, *self.old.basis)
        self.assertEqual(self.repository.initialize(), "migrated")
        self.core_path = self.root / "core-fixture.sqlite"
        self.change_number = 0
        self.previous = {}
        with closing(sqlite3.connect(self.core_path)) as core:
            core.executescript("""
                CREATE TABLE task_executions(execution_id PRIMARY KEY,project_id,task_id);
                CREATE TABLE task_owner_transitions(transition_id,project_id,task_id,execution_id,generation,
                    previous_status,current_status,actor_session_id,created_at);
                CREATE TABLE task_events(task_event_id,project_id,task_id,created_at);
                CREATE TABLE task_execution_cycles(completion_cycle_id,project_id,task_id,execution_id);
                CREATE TABLE review_receipt_sessions(review_receipt_id,session_id,execution_id,original_result_digest);
            """)

    def core_reader(self):
        core = sqlite3.connect(f"file:{self.core_path.as_posix()}?mode=ro", uri=True)
        core.row_factory = sqlite3.Row
        core.execute("PRAGMA query_only=ON")
        core.execute("BEGIN")
        return closing(core)

    def change(self, task, number, status, at, *, cycle=None, bind=True):
        self.change_number += 1
        generation = self.change_number
        ex = execution(number) if number else None
        identity = f"transition-{generation}"
        event_id = f"tg_event_{generation:016x}"
        with closing(sqlite3.connect(self.core_path)) as core:
            if ex:
                core.execute("INSERT OR IGNORE INTO task_executions VALUES (?,?,?)", (ex, self.repository.basis[0], task))
            core.execute("INSERT INTO task_owner_transitions VALUES (?,?,?,?,?,?,?,?,?)",
                         (identity, self.repository.basis[0], task, ex, generation, self.previous.get(task), status, THREAD, str(generation)))
            core.execute("INSERT INTO task_events VALUES (?,?,?,?)", (event_id, self.repository.basis[0], task, str(generation)))
            if cycle:
                core.execute("INSERT INTO task_execution_cycles VALUES (?,?,?,?)", (cycle, self.repository.basis[0], task, ex))
            core.commit()
        self.previous[task] = status
        if bind:
            self.repository.record((OperationObservation(THREAD, turn(at), self.repository.basis[0], task,
                                                         event_id, generation, status),))

    def observed(self, first=1, last=12):
        rows = self.records()
        for number in range(first, last + 1):
            rows.extend([event("event_msg", type="task_started", turn_id=turn(number), started_at=number),
                         event("turn_context", turn_id=turn(number), model="fixture-model"),
                         usage(f"r{number}", turn=turn(number))])
        self.write(rows)
        self.collect()

    def read(self, task=None, audit=False):
        with self.core_reader() as core:
            return self.repository.read(core, task_id=task, audit=audit)

    def refresh(self):
        return self.repository.refresh(self.core_reader)

    def test_restart_and_overlapping_turns_keep_all_precompletion_executions(self):
        task = "tg_task_" + "a" * 16
        self.observed()
        self.change(task, 1, "in_progress", 2)
        self.change(task, 0, "ready", 4)
        self.change(task, 2, "in_progress", 4)
        self.change(task, 0, "cancelled", 6)
        self.change(task, 3, "in_progress", 8)
        self.change(task, 3, "done", 9, cycle="cycle-A")
        self.refresh()
        summary = self.read(task)["periods"][0]
        self.assertEqual(summary["models"][0]["total_tokens"], 7 * 120)
        self.assertEqual(summary["own_executions"], [execution(1), execution(2), execution(3)])
        self.assertEqual(summary["completion_cycle_id"], "cycle-A")
        self.assertEqual(len(summary["snapshot_ids"]), 2)
        before = self.repository.path.read_bytes()
        self.refresh()
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_late_response_merge_and_reopen_preserve_original_cycle_and_successors(self):
        a, b, c = ("tg_task_" + letter * 16 for letter in "abc")
        self.observed()
        self.change(a, 1, "in_progress", 2)
        self.change(a, 1, "done", 3, cycle="cycle-A1")
        self.refresh()
        first = self.read(a)["periods"][0]["snapshot_ids"][0]
        self.change(b, 2, "in_progress", 3)
        self.change(b, 2, "paused", 4)
        self.change(b, 2, "in_progress", 7)
        self.change(b, 2, "done", 8, cycle="cycle-B")
        self.change(c, 3, "in_progress", 8)
        self.change(c, 3, "done", 9, cycle="cycle-C")
        self.change(a, 0, "ready", 10)
        self.change(a, 4, "in_progress", 10)
        self.change(a, 4, "done", 11, cycle="cycle-A2")
        self.append(usage("late", turn=turn(3)))
        self.collect()
        self.refresh()
        history = self.read(a, audit=True)
        earlier, later = history["periods"]
        self.assertEqual(earlier["models"][0]["total_tokens"], 7 * 120)
        self.assertEqual(later["models"][0]["total_tokens"], 2 * 120)
        self.assertEqual(earlier["completion_cycle_id"], "cycle-A1")
        self.assertEqual(later["completion_cycle_id"], "cycle-A2")
        self.assertEqual(earlier["shared_executions"], [execution(1), execution(2), execution(3)])
        document = self.read(audit=True)
        self.assertIn(first, [item["snapshot_id"] for item in document["snapshots"]])
        self.assertNotIn(first, document["current_snapshot_ids"])
        new_id = earlier["snapshot_ids"][0]
        self.assertEqual(next(item for item in document["snapshots"] if item["snapshot_id"] == new_id)["predecessors"], [first])
        self.assertFalse(any(link["snapshot_id"] == new_id and link["completion_cycle_id"] == "cycle-A2" for link in document["cycle_links"]))

    def test_unobserved_endpoint_and_open_shared_task_never_claim_complete(self):
        task = "tg_task_" + "a" * 16
        self.observed(last=3)
        self.change(task, 1, "in_progress", 2)
        self.change(task, 1, "done", 4, cycle="cycle-A")
        self.refresh()
        period = self.read(task)["periods"][0]
        self.assertEqual(period["models"][0]["total_tokens"], 120)
        self.assertIn("boundary_unknown", period["gaps"])
        self.observed(last=4)
        self.refresh()
        self.assertEqual(self.read(task)["periods"][0]["models"][0]["total_tokens"], 360)

    def test_capture_rollback_keeps_last_good_snapshot_and_membership(self):
        task = "tg_task_" + "a" * 16
        self.observed()
        self.change(task, 1, "in_progress", 2)
        self.change(task, 1, "done", 3, cycle="cycle-A")
        self.refresh()
        old = self.read(audit=True)
        self.append(usage("late", turn=turn(3)))
        self.collect()
        with mock.patch.object(self.repository, "_before_snapshot_commit", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.refresh()
        self.assertEqual(self.read(audit=True), old)
        self.refresh()
        self.assertNotEqual(self.read(audit=True), old)
        with self.repository.connection(write=True) as connection:
            for table in ("usage_snapshots", "usage_snapshot_members", "usage_cycle_links", "usage_supersessions"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"DELETE FROM {table}")

    def test_restored_core_rejects_newer_associations(self):
        task = "tg_task_" + "a" * 16
        self.observed()
        self.change(task, 1, "in_progress", 2)
        restored = self.core_path.read_bytes()
        self.change(task, 1, "done", 3, cycle="cycle-A")
        self.refresh()
        self.core_path.write_bytes(restored)
        with self.assertRaises(UsageError):
            self.read(task)
        self.refresh()
        result = self.read(task)
        self.assertEqual(result["cycle_links"], [])
        self.assertIsNone(result["periods"][0]["completion_cycle_id"])
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_cycle_links").fetchone()[0], 1)

    def test_reopened_ready_period_does_not_inherit_completed_total(self):
        task = "tg_task_" + "a" * 16
        self.observed()
        self.change(task, 1, "in_progress", 2)
        self.change(task, 1, "done", 3, cycle="cycle-A")
        self.refresh()
        self.change(task, 0, "ready", 7)
        self.refresh()
        current = self.read(task)["periods"][0]
        self.assertEqual(current["models"], [])
        self.assertIsNone(current["completion_cycle_id"])
        self.assertEqual(len(self.read(task, audit=True)["periods"]), 2)

    def test_conflict_creates_successor_and_never_counts_superseded_totals(self):
        task = "tg_task_" + "a" * 16
        self.observed()
        self.change(task, 1, "in_progress", 2)
        self.change(task, 1, "done", 3, cycle="cycle-A")
        self.refresh()
        first = self.read(task)["periods"][0]
        self.append(usage("r3", turn=turn(4)))
        self.collect()
        self.refresh()
        current = self.read(task)["periods"][0]
        self.assertEqual(current["models"][0]["total_tokens"], 120)
        self.assertEqual(current["quality"], "conflicting")
        self.assertNotEqual(current["snapshot_ids"], first["snapshot_ids"])
        self.assertEqual(len(self.read(audit=True)["snapshots"]), 2)

    def test_numerical_migration_one_or_two_rolls_back_and_preserves_rows(self):
        for previous in (UsageRepository, UsageAttributionRepository):
            path = self.root / (previous.__name__ + ".sqlite")
            old = previous(path, *self.repository.basis)
            old.initialize()
            current = UsageEvidenceRepository(path, *old.basis)
            before = path.read_bytes()
            self.assertEqual(current.inspect(), "migration_required")
            self.assertEqual(path.read_bytes(), before)
            with mock.patch.object(current, "_before_evidence_marker", side_effect=UsageError()):
                with self.assertRaises(UsageError):
                    current.initialize()
            self.assertEqual(old.inspect(), "current")
            self.assertEqual(current.initialize(), "migrated")
            self.assertEqual(current.initialize(), "current")
            with self.assertRaises(UsageError):
                old.inspect()

    def test_two_workers_serialize_without_duplicate_snapshot_or_cycle_link(self):
        task = "tg_task_" + "a" * 16
        self.observed()
        self.change(task, 1, "in_progress", 2)
        self.change(task, 1, "done", 3, cycle="cycle-A")
        with ThreadPoolExecutor(max_workers=2) as workers:
            first, second = list(workers.map(lambda _: self.refresh(), range(2)))
        self.assertEqual(first, second)
        result = self.read(audit=True)
        self.assertEqual(len(result["snapshots"]), 1)
        self.assertEqual(len(result["cycle_links"]), 1)
