"""Exact endpoint-derived interval lookup stays bounded as history grows."""

from dataclasses import replace
from pathlib import Path
import random
import tempfile
import unittest

from tests.test_usage_attribution import THREAD, turn, interval
from task_governance_tool.usage_attribution import _coverage
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_reconciliation_ranges import range_bins, replace_range, consumers, coverage, MAX_CLOCK
from task_governance_tool.session_identity import CallerIdentity


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repository = UsageReconciliationRepository(self.root / "usage.sqlite", "project", "a" * 64, 1)
        self.repository.initialize()
        self.repository.register_session(CallerIdentity(THREAD))

    def test_integer_partition_is_exact_at_extremes_and_random_ranges(self):
        randomizer = random.Random(417)
        cases = [(0, 0), (0, MAX_CLOCK), (1, MAX_CLOCK - 1), (MAX_CLOCK, MAX_CLOCK)]
        cases.extend(tuple(sorted((randomizer.randrange(MAX_CLOCK), randomizer.randrange(MAX_CLOCK)))) for _ in range(1000))
        for first, last in cases:
            bins = list(range_bins(first, last))
            self.assertLessEqual(len(bins), 126)
            expected = first
            for level, prefix in bins:
                self.assertEqual(prefix << level, expected)
                expected += 1 << level
            self.assertEqual(expected, last + 1)

    def test_reverse_lookup_avoids_unrelated_past_and_future_intervals(self):
        with self.repository.connection(write=True) as connection:
            value = {"thread_id": THREAD, "first": 100000, "last": 100010, "exact": None}
            replace_range(connection, "review", "selected", value)
            def count():
                steps = [0]
                def progress():
                    steps[0] += 1
                    return 0
                connection.set_progress_handler(progress, 1)
                try:
                    self.assertEqual(consumers(connection, THREAD, turn(1), 100005), {("review", "selected")})
                finally:
                    connection.set_progress_handler(None, 0)
                return steps[0]
            small = count()
            for number in range(3000):
                for prefix, start in (("past", number * 20), ("future", 200000 + number * 20)):
                    replace_range(connection, "review", f"{prefix}-{number}", {**value, "first": start, "last": start + 10})
            large = count()
            self.assertLessEqual(large, small + 100)

    def test_current_coverage_matches_pure_oracle_for_missing_reversed_and_identity_endpoints(self):
        starts = {(THREAD, turn(number)): number * 10 for number in range(1, 6)}
        with self.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_turns VALUES (?,?,?)", [(thread, identity, clock) for (thread, identity), clock in starts.items()])
            base = interval("task", "execution", 2, 4)
            cases = (base, replace(base, start_turn=None), replace(base, end_turn=None),
                     replace(base, start_turn=turn(4), end_turn=turn(2)), replace(base, closed=False),
                     replace(base, identity_only=True), replace(base, start_turn=turn(9), identity_only=True))
            for number, value in enumerate(cases):
                expected, gaps = _coverage(value, starts)
                selected = coverage(connection, value)
                self.assertEqual(set(selected["diagnostics"]), gaps)
                origin = str(number)
                replace_range(connection, "review", origin, selected)
                actual = {key for key in {*starts, (THREAD, turn(9))} if ("review", origin) in consumers(connection, *key, starts.get(key))}
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
