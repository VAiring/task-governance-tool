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
