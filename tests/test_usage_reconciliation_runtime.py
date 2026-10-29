"""Restartable phase composition uses real admitted core and numerical stores."""

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.test_viewer_maintenance import make_target
from tests import test_usage_response_repository as response_fixture
from tests.test_usage_collection import THREAD
from task_governance_tool import storage, tasks
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_collection import register_source
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_reconciliation_runtime import advance_initial, advance_effects
from task_governance_tool import usage_reconciliation_core as core_inputs
from task_governance_tool import usage_reconciliation_inputs as turn_inputs
from task_governance_tool.usage_reconciliation_baseline import establish_page


class RuntimeTests(unittest.TestCase):
    operation = response_fixture.ResponseRepositoryTests.operation
    write_turn = response_fixture.ResponseRepositoryTests.write_turn
    records = response_fixture.ResponseRepositoryTests.records
    write = response_fixture.ResponseRepositoryTests.write
    ingest = response_fixture.ResponseRepositoryTests.ingest

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = make_target(self.root, enabled=False)
        self.root = self.root / "project"
        from task_governance_tool.state_resolver import observe_current_root
        self.repository = UsageReconciliationRepository(self.root / "usage.sqlite", self.target.project.project_id,
                                                       observe_current_root(self.root).canonical_path_hash, 1)
        self.repository.initialize()
        self.source = SourceInput(THREAD, self.root / "log.jsonl", self.root, self.root)
        register_source(self.repository, self.source, CallerIdentity(THREAD))
        with closing(storage.connect_initialized(self.target)) as core:
            result = tasks.add_task(core, self.target.project, database_target=self.target,
                                    title="Runtime reconciliation fixture", status="in_progress", caller=CallerIdentity(THREAD))
            core.commit()
        self.task_id = result.task["task_id"]
        with self.reader() as core:
            row = core.execute("SELECT * FROM task_owner_transitions WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (self.task_id,)).fetchone()
            self.execution = row["execution_id"]
            acknowledgement = {"ok": True, "command": "task.add", "project_id": self.repository.basis[0],
                "data": {"task": {**result.task, "ownership": {"generation": row["generation"]}}, "event": result.event}}
        self.write_turn(self.operation("start", acknowledgement, "r1", direct=True))
        self.ingest()

    def reader(self):
        return closing(storage.connect_initialized_readonly(self.target, operation_local=True))

    def drain_initial(self):
        phases = []
        for _ in range(300):
            result = advance_initial(self.repository, self.reader)
            phases.append(result["phase"])
            if result["phase"] == "effects_pending":
                return phases
        self.fail("initial phase did not converge")

    def test_actual_task_acknowledgement_reaches_base_membership_without_claiming_publication(self):
        phases = self.drain_initial()
        self.assertTrue({"core", "baseline", "operations", "intervals", "turn_inputs", "allocation", "base_effects"} <= set(phases))
        with self.repository.connection() as connection:
            owners = [row[0] for row in connection.execute("SELECT DISTINCT execution_id FROM usage_reconcile_membership")]
            self.assertEqual(owners, [self.execution])
            self.assertIsNotNone(connection.execute("SELECT 1 FROM usage_reconcile_overlay_turn_work").fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_capture").fetchone())

    def test_unchanged_initial_phase_reentry_never_reloads_history(self):
        self.drain_initial()
        self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
        with mock.patch.object(core_inputs, "read_task_inputs", side_effect=AssertionError("unrelated core history")), \
                mock.patch.object(turn_inputs, "read_selected_relations", side_effect=AssertionError("unchanged original relations")):
            self.assertEqual(advance_initial(self.repository, self.reader), {"phase": "effects_pending"})

    def test_interrupted_base_key_reduction_cannot_certify_current_inputs(self):
        from task_governance_tool.usage_reconciliation_basis import require_inputs
        from task_governance_tool.usage_values import UsageError
        self.drain_initial()
        with self.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_reconcile_base_key_work VALUES ('openai','r1')")
        self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
        with self.repository.connection() as connection, self.reader() as core:
            with self.assertRaisesRegex(UsageError, "usage_pending"):
                require_inputs(self.repository, core, connection)
        self.assertEqual(advance_initial(self.repository, self.reader),
                         {"phase": "base_effects", "provider": "openai", "response_id": "r1"})
        self.assertEqual(advance_initial(self.repository, self.reader), {"phase": "effects_pending"})

    def test_owner_effects_keep_ambiguity_seen_before_removal_and_later_readdition(self):
        from task_governance_tool.usage_response_ownership import apply_actions
        owners = {"old"}
        self.assertTrue(apply_actions(owners, (("add", "new"), ("check", None), ("remove", "old"))))
        self.assertEqual(owners, {"new"})
        self.assertFalse(apply_actions(owners, (("add", "old"),)))
        self.assertEqual(owners, {"old", "new"})

    def test_restore_epoch_discards_derived_work_but_not_original_responses(self):
        from task_governance_tool.viewer_projection_repository import reset_restored_database
        self.drain_initial()
        reset_restored_database(self.target)
        self.assertTrue(advance_initial(self.repository, self.reader)["reset"])
        with self.repository.connection() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_membership").fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_responses").fetchone()[0], 1)
        self.drain_initial()
        with self.repository.connection() as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT DISTINCT execution_id FROM usage_reconcile_membership")], [self.execution])

    def test_baseline_does_not_manufacture_input_generations(self):
        # A migrated numerical store can have existing inputs and generation 0.
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_reconcile_changes")
            connection.execute("UPDATE usage_reconcile_state SET generation=0")
            self.repository._reset_reconciliation(connection)
        for _ in range(20):
            with self.repository.connection(write=True) as connection:
                result = establish_page(self.repository, connection)
            if result["complete"]:
                break
        else:
            self.fail("baseline did not finish")
        with self.repository.connection() as connection:
            self.assertEqual(self.repository.reconciliation_revision(connection).generation, 0)
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_changes").fetchone())
            self.assertIsNotNone(connection.execute("SELECT 1 FROM usage_reconcile_source_epochs").fetchone())
            self.assertIsNotNone(connection.execute("SELECT 1 FROM usage_reconcile_input_turn_work").fetchone())

    def drain_effects(self):
        self.drain_initial()
        for _ in range(300):
            result = advance_effects(self.repository, self.reader)
            if result["phase"] == "classification_pending":
                return
        self.fail("effects did not converge")

    def test_effects_finish_with_fresh_input_guard_and_full_geometry(self):
        from task_governance_tool.usage_reconciliation_basis import require_inputs
        from task_governance_tool.usage_reconciliation_membership import component_geometry
        self.drain_effects()
        with self.repository.connection() as connection, self.reader() as core:
            basis = require_inputs(self.repository, core, connection, classified=True)
            expected = self.repository.attribution(core, geometry_only=True)
            actual = [component_geometry(connection, row[0]) for row in connection.execute(
                "SELECT DISTINCT component_id FROM usage_reconcile_response_result WHERE component_id IS NOT NULL")]
            self.assertEqual(actual, expected["components"])
            self.assertEqual(basis["numerical"][1], self.repository.reconciliation_revision(connection).generation)

    def test_fresh_guard_rejects_new_numerical_change_before_effects(self):
        from task_governance_tool.usage_values import UsageError
        self.drain_initial()
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET pending='partial_tail'")
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            advance_effects(self.repository, self.reader)
        self.drain_effects()

    def test_uncertainty_dependency_survives_restart_and_fences_publication(self):
        from task_governance_tool.usage_reconciliation_membership import replace_contribution
        from task_governance_tool.usage_reconciliation_basis import require_inputs
        from task_governance_tool.usage_values import UsageError
        self.drain_effects()
        for uncertain in (True, False):
            with self.repository.connection(write=True) as connection:
                replace_contribution(connection, "uncertainty", "recovery-fixture",
                    unknown=[("openai", "r1")] if uncertain else ())
            self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
            with self.repository.connection() as connection, self.reader() as core:
                with self.assertRaisesRegex(UsageError, "usage_pending"):
                    require_inputs(self.repository, core, connection, classified=True)
            # A retained dependency executes before response classification,
            # for both newly unknown and newly known original response keys.
            self.assertEqual(advance_effects(self.repository, self.reader)["phase"], "turn_overlays")
            self.drain_effects()
            with self.repository.connection() as connection, self.reader() as core:
                require_inputs(self.repository, core, connection, classified=True)
                row = connection.execute("SELECT unknown FROM usage_reconcile_response_result "
                    "WHERE provider='openai' AND response_id='r1'").fetchone()
                self.assertEqual(row[0], int(uncertain))

    def test_fresh_guard_rejects_new_core_change_before_effects(self):
        from task_governance_tool.usage_values import UsageError
        self.drain_initial()
        with closing(storage.connect_initialized(self.target)) as core:
            tasks.add_task(core, self.target.project, database_target=self.target, title="Later unrelated Task")
            core.commit()
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            advance_effects(self.repository, self.reader)
        self.drain_effects()

    def test_base_owner_dependency_fences_publication_with_equal_uncertainty(self):
        from task_governance_tool.usage_reconciliation_membership import replace_contribution
        from task_governance_tool.usage_reconciliation_basis import require_inputs
        from task_governance_tool.usage_values import UsageError
        self.drain_effects()
        for assigned in (True, False):
            with self.repository.connection(write=True) as connection:
                owners = [("openai", "r1", self.execution)] if assigned else []
                replace_contribution(connection, "base", "owner-recovery-fixture", owners=owners)
            self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
            with self.repository.connection() as connection, self.reader() as core:
                with self.assertRaisesRegex(UsageError, "usage_pending"):
                    require_inputs(self.repository, core, connection, classified=True)
            self.assertEqual(advance_effects(self.repository, self.reader)["phase"], "turn_overlays")
            self.drain_effects()
            with self.repository.connection(write=True) as connection:
                self.assertFalse(replace_contribution(connection, "base", "owner-recovery-fixture", owners=owners))
                self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_overlay_turn_work").fetchone())

    def test_numerical_physical_replacement_requires_reestablishment(self):
        import os
        import shutil
        self.drain_effects()
        with self.repository.connection() as connection:
            original = tuple(connection.execute("SELECT * FROM usage_responses").fetchone())
        replacement = self.repository.path.with_suffix(".replacement")
        shutil.copyfile(self.repository.path, replacement)
        os.replace(replacement, self.repository.path)
        self.assertEqual(advance_initial(self.repository, self.reader)["phase"], "core")
        with self.repository.connection() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_membership").fetchone())
            self.assertEqual(tuple(connection.execute("SELECT * FROM usage_responses").fetchone()), original)
        self.drain_effects()

    def drain_snapshots(self):
        from task_governance_tool.usage_reconciliation_snapshots import advance
        self.drain_effects()
        for _ in range(300):
            result = advance(self.repository, self.reader)
            if result["phase"] == "captured":
                return result
        self.fail("snapshots did not converge")

    def test_selected_snapshot_capture_and_unchanged_reentry(self):
        from task_governance_tool.usage_reconciliation_snapshots import advance
        from task_governance_tool.usage_evidence import snapshot
        from task_governance_tool.usage_response_repository import component_identity
        self.drain_snapshots()
        with self.repository.connection() as connection, self.reader() as core:
            expected = [snapshot(self.repository.basis[0], item) for item in self.repository.attribution(core)["components"]]
            actual = [self.repository._snapshot(connection, row) for row in connection.execute("SELECT * FROM usage_snapshots")]
            self.assertEqual(sorted(actual, key=component_identity), sorted(expected, key=component_identity))
        with mock.patch.object(self.repository, "_responses", side_effect=AssertionError("unchanged originals")), \
                mock.patch.object(self.repository, "attribution", side_effect=AssertionError("full classification")):
            self.assertEqual(advance(self.repository, self.reader)["phase"], "captured")

    def test_event_budget_stop_keeps_committed_frontier_for_new_repository(self):
        from task_governance_tool import usage_reconciliation_runtime as runtime
        from task_governance_tool.usage_values import UsageError
        phases = []
        original = runtime.advance_initial
        def bounded(repository, reader):
            if len(phases) == 5:
                raise UsageError("usage_pending")
            result = original(repository, reader)
            phases.append(result["phase"])
            return result
        with mock.patch.object(runtime, "advance_initial", side_effect=bounded):
            with self.assertRaisesRegex(UsageError, "usage_pending"):
                self.repository.prepare_publication(self.reader)
        self.assertEqual(len(phases), 5)
        with self.repository.connection() as connection:
            before = connection.execute("SELECT COUNT(*) FROM usage_reconcile_core_inputs").fetchone()[0]
            self.assertGreater(before, 0)
        # Another event/process resumes the stored frontier. Repeating the
        # already committed core validation would fail this assertion.
        self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
        with mock.patch.object(core_inputs, "read_task_inputs", side_effect=AssertionError("replayed committed core page")):
            index, _ = self.repository.prepare_publication(self.reader)
        self.assertEqual(len(index["current_snapshot_ids"]), 1)

    def test_review_endpoint_policy_change_requires_reestablishment(self):
        from task_governance_tool import usage_response_repository as response
        self.drain_snapshots()
        with mock.patch.object(response, "_COLLECTED_ENDPOINT_BASIS", "changed-test-policy"):
            self.assertTrue(advance_initial(self.repository, self.reader)["reset"])
            self.drain_snapshots()


if __name__ == "__main__":
    unittest.main()
