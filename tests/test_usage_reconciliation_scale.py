"""History growth must not expand a fixed changed-turn classification scope."""

from contextlib import contextmanager
import time
import unittest
from unittest import mock

from tests import test_usage_reconciliation_changes_runtime as changed_support
from tests.test_usage_collection import event, usage
from tests.test_usage_attribution import turn
from task_governance_tool import usage_reconciliation_core as core_inputs
from task_governance_tool import usage_reconciliation_inputs as inputs
from task_governance_tool import usage_reconciliation_membership as membership


class ScaleTests(unittest.TestCase):
    def test_actual_response_pages_reuse_component_without_changing_publication(self):
        from contextlib import nullcontext
        import sqlite3
        from task_governance_tool import usage_component_reuse as reuse, usage_reconciliation_runtime as runtime
        from tests.test_usage_reconciliation_membership import ComponentReuseTests
        fixture = changed_support.ChangedRuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        keys, expected = fixture.queued_responses(2 * runtime._RESPONSE_PAGE_SIZE + 1)
        native = sqlite3.connect
        measurements = []
        for enabled in (False, True):
            if measurements:
                with fixture.repository.connection(write=True) as connection:
                    connection.executemany("INSERT INTO usage_reconcile_response_work VALUES (?,?)", keys)
            sql = []
            def connect(*args, **kwargs):
                connection = native(*args, **kwargs)
                connection.set_trace_callback(sql.append)
                return connection
            start = time.monotonic()
            with mock.patch.object(sqlite3, "connect", connect), \
                    mock.patch.object(runtime, "monotonic", return_value=0), \
                    (nullcontext() if enabled else mock.patch.object(reuse, "confirmed_components", return_value=None)):
                index, state = fixture.repository.prepare_publication(fixture.reader)
            self.assertEqual(index, expected)
            measurements.append((*ComponentReuseTests.counts(self, sql), round(time.monotonic() - start, 6)))
            for identity in index["snapshots"][state["position"]:]:
                fixture.repository.publication_snapshot(identity)
                state = fixture.repository.advance_publication(state)
            with fixture.repository.publication_guard(fixture.reader, index, state):
                pass
        # Registrations become one per page (first key, then two full pages).
        # The independent geometry SELECT is still required in both runs.
        self.assertEqual(measurements[0][:2], (len(keys) + 1, 2 * len(keys)))
        self.assertEqual(measurements[1][:2], (4, 6))
        self.assertEqual(fixture.assert_same(), expected)
        print(f"component_response_pages_{len(keys)}=" + repr(measurements))

    def test_same_response_keys_use_fewer_core_admissions_and_sql(self):
        from tests.test_integrity_consumer_closure import observe_sql
        from task_governance_tool import storage, usage_reconciliation_runtime as runtime
        fixture = changed_support.ChangedRuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        keys, expected = fixture.queued_responses(2 * runtime._RESPONSE_PAGE_SIZE + 1)
        measurements = []
        for width in (1, runtime._RESPONSE_PAGE_SIZE):
            if measurements:
                with fixture.repository.connection(write=True) as db:
                    db.executemany("INSERT INTO usage_reconcile_response_work VALUES (?,?)", keys)
            with mock.patch.object(runtime, "_RESPONSE_PAGE_SIZE", width), \
                    mock.patch.object(runtime, "monotonic", return_value=0), \
                    mock.patch.object(storage, "_validate_current_schema_contract", wraps=storage._validate_current_schema_contract) as admit, \
                    mock.patch.object(membership, "reconcile_response", wraps=membership.reconcile_response) as classify, \
                    observe_sql() as cost:
                index, _ = fixture.repository.prepare_publication(fixture.reader)
            self.assertEqual(index, expected)
            self.assertEqual([call.args[1:] for call in classify.call_args_list], keys)
            measurements.append((admit.call_count, cost["connections"], cost["sql"]))
        before, after = measurements
        self.assertLess(after[0], before[0], measurements)
        self.assertLess(after[1], before[1], measurements)
        self.assertLess(after[2], before[2], measurements)
        self.assertEqual(fixture.assert_same(), expected)

    def measure(self, size):
        fixture = changed_support.ChangedRuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.transition("paused", "pause", pause_reason="Separate historical work")
        rows = []
        for number in range(2, size + 2):
            rows.extend((event("event_msg", type="task_started", turn_id=turn(number), started_at=number),
                event("turn_context", turn_id=turn(number), model="fixture-model"),
                usage(f"unrelated-{number}", turn=turn(number))))
        fixture.append(*rows)
        repository, reader = fixture.repository, fixture.reader
        repository.prepare_publication(reader)

        @contextmanager
        def counted():
            with mock.patch.object(core_inputs, "read_task_inputs", wraps=core_inputs.read_task_inputs) as core_read, \
                    mock.patch.object(inputs, "stage_input_turn", wraps=inputs.stage_input_turn) as turn_read, \
                    mock.patch.object(membership, "reconcile_response", wraps=membership.reconcile_response) as response, \
                    mock.patch.object(repository, "_responses", wraps=repository._responses) as originals:
                result = {}
                start = time.monotonic()
                yield result
                result.update(core_histories=core_read.call_count, turns=turn_read.call_count,
                              responses=response.call_count,
                              original_keys=sum(len(call.args[1]) for call in originals.call_args_list),
                              seconds=round(time.monotonic() - start, 3))
        with counted() as unchanged:
            repository.prepare_publication(reader)
        fixture.append(usage("late-before-pause", turn=turn(1)))
        with counted() as changed:
            repository.prepare_publication(reader)
        fixture.assert_same()
        return {"history_turns": size, "unchanged": unchanged, "changed": changed}

    def test_unchanged_and_fixed_changeset_counts_ignore_unrelated_same_thread_history(self):
        measured = [self.measure(size) for size in (8, 64)]
        print("reconciliation_scale=" + repr(measured))
        for value in measured:
            self.assertEqual({k: v for k, v in value["unchanged"].items() if k != "seconds"},
                             dict(core_histories=0, turns=0, responses=0, original_keys=0))
        small, large = (item["changed"] for item in measured)
        self.assertEqual({k: v for k, v in small.items() if k != "seconds"},
                         {k: v for k, v in large.items() if k != "seconds"})
        self.assertEqual(large["core_histories"], 0)
        self.assertLessEqual(large["turns"], 2)
        self.assertLessEqual(large["responses"], 4)


if __name__ == "__main__":
    unittest.main()
