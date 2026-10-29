"""Selected capture, interruptible successors and fresh publication fencing."""

import unittest
from unittest import mock

from tests import test_usage_reconciliation_runtime as support
from task_governance_tool.usage_values import UsageError
from task_governance_tool.usage_reconciliation_publication import captured_basis
from task_governance_tool import usage_reconciliation_core as core_inputs
from task_governance_tool import usage_reconciliation_snapshots as snapshots


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = support.RuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repository, self.reader = self.fixture.repository, self.fixture.reader

    def audit(self, index, state):
        for identity in index["snapshots"][state["position"]:]:
            self.repository.publication_snapshot(identity)
            state = self.repository.advance_publication(state)
        return state

    def test_capture_audit_and_fresh_guard_without_history_reclassification(self):
        index, state = self.repository.prepare_publication(self.reader)
        state = self.audit(index, state)
        self.assertEqual(len(index["current_snapshot_ids"]), 1)
        with mock.patch.object(self.repository, "attribution", side_effect=AssertionError("whole classifier")), \
                mock.patch.object(core_inputs, "read_task_inputs", side_effect=AssertionError("unchanged core history")), \
                mock.patch.object(self.repository, "_responses", side_effect=AssertionError("unchanged original counters")):
            with self.repository.publication_guard(self.reader, index, state):
                pass
            same, _ = self.repository.prepare_publication(self.reader)
        self.assertEqual(same, index)

    def test_fresh_guard_rejects_input_change_and_keeps_previous_snapshot(self):
        index, state = self.repository.prepare_publication(self.reader)
        state = self.audit(index, state)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET pending='partial_tail'")
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            with self.repository.publication_guard(self.reader, index, state):
                self.fail("stale guard yielded")
        newer, _ = self.repository.prepare_publication(self.reader)
        self.assertEqual(len(newer["snapshots"]), 2)
        self.assertNotEqual(newer["current_snapshot_ids"], index["current_snapshot_ids"])
        item = self.repository.publication_snapshot(newer["current_snapshot_ids"][0])
        self.assertEqual(item["predecessors"], index["current_snapshot_ids"])
        self.assertIn("partial_tail", item["gaps"])

    def test_read_only_display_uses_fresh_capture_without_reloading_all_relations(self):
        index, _ = self.repository.prepare_publication(self.reader)
        with mock.patch.object(self.repository, "_relation_inputs", side_effect=AssertionError("all relations")), \
                mock.patch.object(self.repository, "attribution", side_effect=AssertionError("whole classifier")):
            with self.reader() as core:
                result = self.repository.read(core, task_id=self.fixture.task_id)
                self.assertEqual(result["total"]["response_count"], 1)
                self.assertEqual(self.repository.read(core)["unattributed"], index["unattributed"])
            with self.repository.connection(write=True) as connection:
                connection.execute("UPDATE usage_sources SET pending='partial_tail'")
            with self.reader() as core:
                with self.assertRaisesRegex(UsageError, "usage_pending"):
                    self.repository.read(core, task_id=self.fixture.task_id)

    def test_unchanged_eof_collection_preserves_incomplete_publication_audit(self):
        self.repository.prepare_publication(self.reader)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET pending='partial_tail'")
        self.repository.prepare_publication(self.reader)
        self.repository.collect_source(self.fixture.source)
        index, state = self.repository.prepare_publication(self.reader)
        self.assertGreater(len(index["snapshots"]), 1)
        state = self.repository.advance_publication(state)
        with self.repository.connection() as connection:
            before = self.repository.reconciliation_revision(connection)
            revision = connection.execute("SELECT revision FROM usage_scan_state WHERE lane='ingest'").fetchone()[0]
        self.repository.collect_source(self.fixture.source, lane="audit")
        self.repository.collect_source(self.fixture.source)
        with self.repository.connection() as connection:
            self.assertGreater(connection.execute("SELECT revision FROM usage_scan_state WHERE lane='ingest'").fetchone()[0], revision)
            self.assertEqual(self.repository.reconciliation_revision(connection), before)
        same, later = self.repository.prepare_publication(self.reader)
        self.assertEqual(same, index)
        self.assertEqual(later["position"], state["position"])

    def test_snapshot_failure_rolls_back_candidate_and_keeps_work_for_next_event(self):
        original, _ = self.repository.prepare_publication(self.reader)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET pending='partial_tail'")
        with mock.patch.object(self.repository, "_before_snapshot_commit", side_effect=UsageError("usage_pending")):
            with self.assertRaisesRegex(UsageError, "usage_pending"):
                self.repository.prepare_publication(self.reader)
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0], 1)
            self.assertIsNotNone(connection.execute("SELECT 1 FROM usage_reconcile_component_work").fetchone())
        recovered, _ = self.repository.prepare_publication(self.reader)
        self.assertEqual(len(recovered["snapshots"]), 2)
        self.assertEqual(self.repository.publication_snapshot(recovered["current_snapshot_ids"][0])["predecessors"], original["current_snapshot_ids"])

    def test_intervening_input_keeps_committed_candidate_as_history_and_reconciles_again(self):
        original, _ = self.repository.prepare_publication(self.reader)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET pending='partial_tail'")
        self.fixture.drain_effects()
        self.assertEqual(snapshots.advance(self.repository, self.reader)["phase"], "snapshot")
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET pending='none'")
        with self.repository.connection() as connection, self.reader() as core:
            with self.assertRaisesRegex(UsageError, "usage_pending"):
                captured_basis(self.repository, core, connection)
        recovered, _ = self.repository.prepare_publication(self.reader)
        self.assertEqual(len(recovered["snapshots"]), 3)
        self.assertEqual(len(recovered["current_snapshot_ids"]), 1)
        current = self.repository.publication_snapshot(recovered["current_snapshot_ids"][0])
        self.assertNotIn("partial_tail", current["gaps"])
        self.assertNotEqual(current["snapshot_id"], original["current_snapshot_ids"][0])


if __name__ == "__main__":
    unittest.main()
