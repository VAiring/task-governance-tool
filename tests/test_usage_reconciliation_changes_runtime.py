"""Real dirty notifications, source replay and state changes versus full replay."""

from contextlib import closing
from dataclasses import replace
import unittest

from tests import test_usage_reconciliation_runtime as support
from tests.test_usage_collection import THREAD, event, usage, COUNTS
from tests.test_usage_attribution import turn
from task_governance_tool import storage, tasks
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_collection import register_source
from task_governance_tool.usage_evidence import snapshot
from task_governance_tool.usage_response_repository import component_identity


class ChangedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = support.RuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repository, self.reader = self.fixture.repository, self.fixture.reader

    def assert_same(self):
        index, _ = self.repository.prepare_publication(self.reader)
        with self.repository.connection() as connection, self.reader() as core:
            full = self.repository.attribution(core)
            expected = [snapshot(self.repository.basis[0], item) for item in full["components"]]
            actual = [self.repository._snapshot(connection, connection.execute(
                "SELECT * FROM usage_snapshots WHERE snapshot_id=?", (identity,)).fetchone())
                for identity in index["current_snapshot_ids"]]
            def comparable(items):
                return [{k: v for k, v in item.items() if k not in {"snapshot_id", "predecessors"}}
                        for item in sorted(items, key=component_identity)]
            self.assertEqual(comparable(actual), comparable(expected))
            self.assertEqual(index["unattributed"], {kind: self.repository._totals(connection, full[field])
                for kind, field in (("unknown", "unknown_response_keys"), ("unassigned", "unassigned_response_keys"))})
        return index

    def append(self, *rows):
        from tests.test_usage_collection import UsageCollectionTests
        UsageCollectionTests.append(self.fixture, *rows)
        self.fixture.ingest()

    def transition(self, status, response, **options):
        fixture = self.fixture
        with closing(storage.connect_initialized(fixture.target)) as core:
            result = tasks.edit_task(core, fixture.target.project, fixture.task_id,
                database_target=fixture.target, caller=CallerIdentity(THREAD), status=status, **options)
            core.commit()
        with self.reader() as core:
            row = core.execute("SELECT generation FROM task_owner_transitions WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                               (fixture.task_id,)).fetchone()
        acknowledgement = {"ok": True, "command": "task.edit", "project_id": self.repository.basis[0],
            "data": {"changed_fields": ["status"], "task": {**result.task, "ownership": {"generation": row[0]}}, "event": result.event}}
        self.append(*fixture.operation(response, acknowledgement, response, direct=True))

    def test_late_duplicate_key_in_other_turn_and_removal_use_actual_reverse_queue(self):
        self.assert_same()
        second = replace(self.fixture.source, path=self.fixture.root / "second.jsonl")
        register_source(self.repository, second, CallerIdentity(THREAD))
        self.fixture.write(self.fixture.records(event("event_msg", type="task_started", turn_id=turn(2), started_at=2),
            event("turn_context", turn_id=turn(2), model="fixture-model"),
            usage("r1", turn=turn(2)), usage("s2", turn=turn(2))), path=second.path)
        self.repository.collect_source(second)
        self.assert_same()
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_positions WHERE source_id=?", (second.source_id,))
        self.assert_same()

    def test_pause_and_resume_keep_unassigned_gap_and_old_execution(self):
        self.assert_same()
        self.transition("paused", "pause", pause_reason="Fixture interruption")
        self.append(usage("gap", turn=turn(1), counts={k: v for k, v in COUNTS.items() if k in {
            "input_tokens", "output_tokens", "total_tokens"}}))
        paused = self.assert_same()
        self.assertEqual(paused["unattributed"]["unassigned"]["response_count"], 1)
        self.transition("in_progress", "resume")
        self.append(usage("after", turn=turn(1)))
        resumed = self.assert_same()
        self.assertEqual(resumed["unattributed"], paused["unattributed"])

    def assert_frozen_same(self, *, restart=False):
        from unittest import mock
        from tests.test_usage_reconciliation_oracle import previous_classifier
        if restart:
            self.fixture.drain_effects()
            self.repository = type(self.repository)(self.repository.path, *self.repository.basis)
            self.fixture.repository = self.repository
        with mock.patch("task_governance_tool.usage_response_repository.project_responses", previous_classifier()):
            return self.assert_same()

    def assert_frozen_geometry(self):
        from unittest import mock
        from tests.test_usage_reconciliation_oracle import previous_classifier
        from task_governance_tool.usage_reconciliation_membership import component_geometry
        self.fixture.drain_effects()
        with self.reader() as core, self.repository.connection() as connection, mock.patch(
                "task_governance_tool.usage_response_repository.project_responses", previous_classifier()):
            expected = self.repository.attribution(core, geometry_only=True)
            identities = {row[0] for row in connection.execute(
                "SELECT component_id FROM usage_reconcile_response_result WHERE component_id IS NOT NULL "
                "UNION SELECT component_id FROM usage_reconcile_empty_components")}
            actual = [component_geometry(connection, identity) for identity in identities]
            self.assertEqual(sorted(actual, key=component_identity), sorted(expected["components"], key=component_identity))
            for column, predicate in (("unknown_response_keys", "unknown=1"), ("unassigned_response_keys", "unknown=0 AND component_id IS NULL")):
                keys = sorted(tuple(row) for row in connection.execute(
                    "SELECT provider,response_id FROM usage_reconcile_response_result WHERE " + predicate))
                self.assertEqual(keys, sorted(expected[column]))

    def repeat_response(self, response, number=2, thread=THREAD):
        second = replace(self.fixture.source, thread_id=thread, path=self.fixture.root / f"repeat-{number}.jsonl")
        register_source(self.repository, second, CallerIdentity(thread))
        self.fixture.write(self.fixture.records(event("event_msg", type="task_started", turn_id=turn(number), started_at=number),
            event("turn_context", turn_id=turn(number), model="fixture-model"),
            usage(response, turn=turn(number), thread=thread), thread=thread), path=second.path)
        self.repository.collect_source(second)
        # Controlled fixture recovery, not automatic production conflict clearing.
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_conflicts")
        return second

    def remove_and_restore_position(self, source):
        with self.repository.connection(write=True) as connection:
            rows = list(map(tuple, connection.execute("SELECT * FROM usage_response_positions WHERE source_id=?", (source.source_id,))))
            connection.execute("DELETE FROM usage_response_positions WHERE source_id=?", (source.source_id,))
        self.assert_frozen_same(restart=True)
        with self.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_response_positions VALUES (" + ",".join("?" for _ in rows[0]) + ")", rows)
        self.assert_frozen_same(restart=True)

    def test_missing_order_uses_global_occurrences_after_recovery_and_restart(self):
        self.check_global_order(THREAD)

    def test_missing_order_uses_other_thread_occurrences(self):
        self.check_global_order("22222222-2222-4222-8222-222222222222")

    def check_global_order(self, thread):
        self.append(usage("r", turn=turn(1)))
        self.assert_frozen_same()
        second = self.repeat_response("r", thread=thread)
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_positions WHERE source_id=? AND response_id='r'", (self.fixture.source.source_id,))
        self.assert_frozen_same(restart=True)
        self.remove_and_restore_position(second)

    def test_bad_order_occurrence_remains_unknown_despite_an_ordered_occurrence(self):
        self.append(usage("r", turn=turn(1)))
        self.repeat_response("r")
        self.assert_frozen_same()
        with self.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_turn_conflicts VALUES (?,?)", (THREAD, turn(2)))
        result = self.assert_frozen_same(restart=True)
        self.assertEqual(result["unattributed"]["unknown"]["response_count"], 1)
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_turn_conflicts")
        self.assert_frozen_same(restart=True)

    def test_fallback_uses_last_thread_position_after_stop_and_restart(self):
        self.append(usage("r", turn=turn(1)))
        self.transition("paused", "pause", pause_reason="Fixture interruption")
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_boundaries WHERE json_extract(document,'$.call_id')='start'")
        self.assert_frozen_same()
        second = self.repeat_response("r")
        self.assert_frozen_same(restart=True)
        self.remove_and_restore_position(second)

    def test_moving_stop_position_revisits_unchanged_original_turn(self):
        self.append(usage("r", turn=turn(1)))
        self.transition("paused", "pause", pause_reason="Fixture interruption")
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_boundaries WHERE json_extract(document,'$.call_id')='start'")
        self.repeat_response("r")
        before = self.assert_frozen_same()
        later_stop = self.repeat_response("pause", number=3)
        after = self.assert_frozen_same(restart=True)
        self.assertNotEqual(before["current_snapshot_ids"], after["current_snapshot_ids"])
        self.remove_and_restore_position(later_stop)

    def test_base_owner_removal_revisits_original_without_position(self):
        self.append(usage("r", turn=turn(1)))
        self.transition("paused", "pause", pause_reason="Fixture interruption")
        self.repeat_response("r")
        other = "22222222-2222-4222-8222-222222222222"
        second = replace(self.fixture.source, thread_id=other, path=self.fixture.root / "owner-source.jsonl")
        register_source(self.repository, second, CallerIdentity(other))
        self.fixture.write(self.fixture.records(
            event("event_msg", type="task_started", turn_id=turn(3), started_at=3),
            event("turn_context", turn_id=turn(3), model="fixture-model"),
            usage("r1", turn=turn(3), thread=other), usage("r", turn=turn(3), thread=other), thread=other), path=second.path)
        self.repository.collect_source(second)
        third = "33333333-3333-4333-8333-333333333333"
        self.repeat_response("r", number=4, thread=third)
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_positions WHERE source_id=? AND response_id='r'", (self.fixture.source.source_id,))
            connection.execute("INSERT INTO usage_turn_conflicts VALUES (?,?)", (third, turn(4)))
        self.assert_frozen_same()
        with self.repository.connection(write=True) as connection:
            saved = tuple(connection.execute("SELECT * FROM usage_response_positions WHERE source_id=? AND response_id='r1'", (second.source_id,)).fetchone())
            connection.execute("DELETE FROM usage_response_positions WHERE source_id=? AND response_id='r1'", (second.source_id,))
        self.assert_frozen_same(restart=True)
        with self.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_response_positions VALUES (" + ",".join("?" for _ in saved) + ")", saved)
        self.repository = type(self.repository)(self.repository.path, *self.repository.basis)
        self.fixture.repository = self.repository
        # Verify reinserted membership independently of publication: the
        # retained schema-9 full publisher also rejects this exact historical
        # snapshot-ID recurrence. That separate pre-existing failure is handed off.
        self.assert_frozen_geometry()

    def test_source_replacement_and_diagnostic_removal_reconcile_without_rewriting_history(self):
        before = self.assert_same()
        self.repository.record_gap(self.fixture.source.source_id, THREAD, "source_unreadable")
        gap = self.assert_same()
        self.assertNotEqual(before["current_snapshot_ids"], gap["current_snapshot_ids"])
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_diagnostics WHERE source_id=?", (self.fixture.source.source_id,))
        self.assert_same()
        self.fixture.write_turn([usage("replacement", turn=turn(1))])
        self.repository.collect_source(self.fixture.source)
        self.assertGreater(self.repository.cursor(self.fixture.source.source_id, THREAD).incarnation, 0)
        after = self.assert_same()
        self.assertTrue(set(before["snapshots"]) <= set(after["snapshots"]))

    def test_partial_split_retains_predecessors_across_input_change_and_restores(self):
        from task_governance_tool import usage_reconciliation_snapshots as snapshots
        from task_governance_tool.viewer_projection_repository import reset_restored_database
        for interruption in ("input", "core_restore", "numerical_restore"):
            with self.subTest(interruption=interruption):
                if interruption != "input":
                    self.setUp()
                self.append(usage("r2", turn=turn(1)), usage("r3", turn=turn(1)))
                original = self.assert_same()
                rows = self.fixture.operation("pending", {"ok": False}, "r2", direct=True)
                self.append(*rows[:-1])
                self.fixture.drain_effects()
                self.assertEqual(snapshots.advance(self.repository, self.reader)["phase"], "snapshot")
                if interruption == "input":
                    self.append(event("event_msg", type="fixture_noise"))
                elif interruption == "core_restore":
                    reset_restored_database(self.fixture.target)
                else:
                    with self.repository.connection(write=True) as connection:
                        connection.execute("UPDATE usage_reconcile_state SET epoch=lower(hex(randomblob(16)))")
                latest = self.assert_same()
                self.assertEqual(len(latest["current_snapshot_ids"]), 2)
                for identity in latest["current_snapshot_ids"]:
                    self.assertEqual(self.repository.publication_snapshot(identity)["predecessors"], original["current_snapshot_ids"])


if __name__ == "__main__":
    unittest.main()
