"""Changed windows retain outside membership and independent gap provenance."""

from collections import defaultdict
import unittest

from tests import test_usage_reconciliation_inputs as inputs
from tests.test_usage_response_repository import A, B
from tests.test_usage_collection import usage
from tests.test_usage_attribution import turn
from task_governance_tool import usage_reconciliation_membership as membership
from task_governance_tool.usage_response_repository import component_identity
from task_governance_tool.usage_values import UsageError


class MembershipTests(unittest.TestCase):
    def setUp(self):
        self.fixture = inputs.ReconciliationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repository = self.fixture.repository
        self.fixture.write_turn([usage(f"r{number}", turn=turn(1)) for number in range(6)])
        self.fixture.ingest()

    def replace(self, origin, *, owners=(), unknown=(), gaps=(), kind="base"):
        with self.repository.connection(write=True) as connection:
            return membership.replace_contribution(connection, kind, origin, owners=owners, unknown=unknown, gaps=gaps)

    def drain(self):
        with self.repository.connection(write=True) as connection:
            for row in tuple(connection.execute("SELECT * FROM usage_reconcile_response_work")):
                membership.reconcile_response(connection, *row)

    def component(self, assignment, owners):
        with self.repository.connection() as connection:
            return membership.component_geometry(connection, component_identity({"assignment": assignment, "executions": sorted(owners)}))

    def test_disjoint_outside_windows_are_not_replaced_by_changed_window(self):
        self.replace("first", owners=[("openai", "r0", A), ("openai", "r1", A)])
        self.replace("outside", owners=[("openai", "r4", A), ("openai", "r5", A)])
        self.drain()
        self.replace("first", owners=[("openai", "r0", B), ("openai", "r1", B)])
        self.drain()
        self.assertEqual(self.component("exclusive", [A])["response_keys"], [("openai", "r4"), ("openai", "r5")])
        self.assertEqual(self.component("exclusive", [B])["response_keys"], [("openai", "r0"), ("openai", "r1")])

    def test_old_new_shared_partitions_and_unknown_removal_are_exact(self):
        self.replace("a", owners=[("openai", "r0", A), ("openai", "r1", A)])
        self.replace("b", owners=[("openai", "r1", B), ("openai", "r2", B)])
        self.replace("pending", unknown=[("openai", "r1")], kind="uncertainty")
        self.drain()
        self.assertEqual(self.component("partial", [A, B])["response_keys"], [("openai", "r1")])
        self.replace("pending", kind="uncertainty")
        self.drain()
        self.assertEqual(self.component("shared", [A, B])["response_keys"], [("openai", "r1")])
        self.replace("b", owners=[("openai", "r2", B)])
        self.drain()
        self.assertEqual(self.component("exclusive", [A])["response_keys"], [("openai", "r0"), ("openai", "r1")])

    def test_removing_one_gap_origin_cannot_remove_another_equal_gap(self):
        self.replace("numbers", owners=[("openai", "r0", A)])
        self.replace("one", gaps=[(A, "boundary_unknown")], kind="review")
        self.replace("two", gaps=[(A, "boundary_unknown")], kind="review")
        self.drain()
        self.replace("one", kind="review")
        self.assertEqual(self.component("exclusive", [A])["diagnostics"], ["boundary_unknown"])
        self.replace("two", kind="review")
        self.assertEqual(self.component("exclusive", [A])["diagnostics"], [])

    def test_conflicting_counter_is_diagnostic_only_and_recovery_restores_key(self):
        self.replace("a", owners=[("openai", "r0", A)])
        self.drain()
        with self.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_conflicts VALUES (?,?)", ("openai", "r0"))
            membership.reconcile_response(connection, "openai", "r0")
        with self.repository.connection() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_components").fetchone())
            self.assertIsNotNone(connection.execute("SELECT 1 FROM usage_reconcile_gaps WHERE execution_id=? AND code='response_conflict'", (A,)).fetchone())
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_conflicts")
            membership.reconcile_response(connection, "openai", "r0")
        self.assertEqual(self.component("exclusive", [A])["response_keys"], [("openai", "r0")])
        self.assertEqual(self.component("exclusive", [A])["diagnostics"], [])

    def test_retired_partitions_do_not_accumulate_in_execution_or_global_selection(self):
        from task_governance_tool.usage_reconciliation_basis import global_page
        for number in range(32):
            self.replace("a", owners=[("openai", "r0", A), ("openai", "r0", f"previous-{number}")])
            self.drain()
        self.replace("a", owners=[("openai", "r0", A)])
        self.drain()
        with self.repository.connection(write=True) as connection:
            # Old IDs still have a one-time retirement obligation. A later
            # diagnostic or global uncertainty change must not recreate it.
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_reconcile_component_work").fetchone()[0], 33)
            connection.execute("DELETE FROM usage_reconcile_component_work")
            membership.reconcile_execution(connection, A)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_reconcile_component_work").fetchone()[0], 1)
            connection.execute("DELETE FROM usage_reconcile_component_work")
            connection.execute("INSERT INTO usage_reconcile_global_work VALUES ('operation_unbound')")
            self.assertEqual(global_page(connection), 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_reconcile_components").fetchone()[0], 1)

    def test_failed_replace_preserves_membership_and_its_work(self):
        self.replace("a", owners=[("openai", "r0", A)])
        self.drain()
        with self.assertRaises(UsageError):
            with self.repository.connection(write=True) as connection:
                membership.replace_contribution(connection, "base", "a", owners=[("openai", "r1", B)])
                raise UsageError("usage_pending")
        self.assertEqual(self.component("exclusive", [A])["response_keys"], [("openai", "r0")])
        self.assertFalse(self.replace("a", owners=[("openai", "r0", A)]))

    def test_encounter_change_invalidates_unowned_pending_key_without_replaying_its_turn(self):
        from types import SimpleNamespace
        from task_governance_tool import usage_reconciliation_owner_effects as effects
        from task_governance_tool.usage_evidence import digest, encode
        from tests.test_usage_collection import THREAD
        other = "22222222-2222-4222-8222-222222222222"
        key = ("openai", "r1")
        with self.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_reconcile_turn_encounters VALUES (?,?,?,?)",
                [(THREAD, turn(1), 0, 3), (THREAD, turn(2), 0, 1), (other, turn(3), 0, 2)])
            connection.executemany("INSERT INTO usage_reconcile_positions VALUES (?,?,?,?,?,?)",
                [(THREAD, turn(1), 1, 0, *key), (other, turn(3), 3, 0, *key)])
            connection.execute("INSERT INTO usage_reconcile_pending_origins VALUES (?,?,?,?,?,?)",
                ("pending", "source", THREAD, turn(1), encode({}).decode(), digest({})))
            effects.replace_actions(connection, other, turn(3), 3, [(key, [["add", A]])])
            effects.reconcile_one(connection)
            connection.execute("DELETE FROM usage_reconcile_rule_work")
            self.assertEqual(effects.base_owners(connection, key, through_thread=THREAD)[0], set())
            self.assertEqual(effects.base_owners(connection, key)[0], {A})
            # Only the separate encounter-minimum turn disappears. The pending
            # turn has no owner effects of its own and is never staged again.
            effects.replace_encounter(connection, THREAD, turn(2),
                SimpleNamespace(anchors=(), positions=()), SimpleNamespace(), False)
            self.assertEqual(effects.base_owners(connection, key, through_thread=THREAD)[0], {A})
            self.assertEqual(effects.base_owners(connection, key)[0], {A})
            self.assertIsNotNone(effects.reconcile_one(connection))
            self.assertEqual(list(map(tuple, connection.execute(
                "SELECT kind,origin FROM usage_reconcile_rule_work"))), [("pending", "pending")])
            self.assertIsNone(effects.reconcile_one(connection))


class BasePipelineOracleTests(unittest.TestCase):
    def test_actual_resolved_operations_staging_and_membership_match_full_projection(self):
        fixture = inputs.ReconciliationInputTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        rows = []
        for task, number in ((A, 1), (B, 2), (A, 3)):
            rows += fixture.operation(f"start{number}", fixture.acknowledgement(task, number, "in_progress"), f"r{number}a", direct=True)
            rows += fixture.operation(f"done{number}", fixture.acknowledgement(task, number, "done"), f"r{number}b", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        fixture.resolve()
        fixture.stage(1)
        fixture.replay()
        with fixture.repository.connection(write=True) as connection:
            self.assertIsNotNone(membership.apply_base_one(connection))
            for _ in range(100):
                if membership.apply_base_one(connection) is None:
                    break
            else:
                self.fail("base effects did not converge")
            for row in tuple(connection.execute("SELECT * FROM usage_reconcile_response_work")):
                membership.reconcile_response(connection, *row)
            for row in tuple(connection.execute("SELECT thread_id FROM usage_reconcile_diagnostic_work")):
                membership.reconcile_thread_diagnostics(connection, row[0])
            actual = [membership.component_geometry(connection, row[0]) for row in connection.execute("SELECT component_id FROM usage_reconcile_components")]
            self.assertIsNotNone(connection.execute("SELECT 1 FROM usage_reconcile_overlay_turn_work").fetchone())
        with fixture.reader() as core:
            expected = fixture.repository.attribution(core, geometry_only=True)["components"]
        self.assertEqual(sorted(actual, key=component_identity), sorted(expected, key=component_identity))


if __name__ == "__main__":
    unittest.main()
