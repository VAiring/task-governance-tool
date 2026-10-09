"""Fair finite slices, inventory continuation, gaps and neutral event budgets."""

from dataclasses import replace
import json
from time import monotonic
import unittest
from unittest import mock

from tests import test_usage_lifecycle as support
from tests.test_usage_collection import THREAD, PARENT, TURN, usage
from task_governance_tool import usage_lifecycle as lifecycle
from task_governance_tool.usage_incremental_repository import UsageIncrementalRepository
from task_governance_tool.usage_sources import locate_slice, source_roots
from task_governance_tool.session_identity import CallerIdentity


class IncrementalLifecycleTests(unittest.TestCase):
    write = support.UsageLifecycleTests.write
    payload = support.UsageLifecycleTests.payload
    collect = support.UsageLifecycleTests.collect
    test_unlocatable_registered_session = support.UsageLifecycleTests.test_unlocatable_registered_session_reports_unavailable_not_measured_zero

    def setUp(self):
        support.UsageLifecycleTests.setUp(self)
        self.repo = UsageIncrementalRepository(self.repo.path, *self.repo.basis)
        self.repo.initialize()
        lifecycle.repository_for.return_value = self.repo

    def test_partial_inventory_eventually_visits_all_segments_without_unrelated_headers(self):
        roots = source_roots({"CODEX_HOME": str(self.host)})
        expected = {self.path}
        for n in range(8):
            path = self.logs / f"rollout-{n}-{THREAD}_{n:08x}-89ab-7cde-8fab-0123456789ab.jsonl"
            self.write(path, THREAD, usage(f"segment-{n}"))
            expected.add(path)
        unrelated = self.logs / f"rollout-secret-{PARENT}.jsonl"
        unrelated.write_bytes(b"PRIVATE not JSON")
        state = {"root": 0, "stack": [], "cycle": 0}
        observed = set()
        for attempt in range(50):
            candidates, state, complete = locate_slice({THREAD}, roots, self.project, state,
                                                        deadline=monotonic() + 2, max_entries=2, max_sources=1)
            observed.update(source.path for source in candidates.values())
            if complete:
                break
        else:
            self.fail("inventory did not resume")
        self.assertEqual(observed, expected)
        self.assertLess(attempt, 30)

    def test_current_append_does_not_starve_past_session_or_audit(self):
        other = self.logs / f"rollout-old-{PARENT}.jsonl"
        self.write(other, PARENT, usage("old", thread=PARENT))
        self.repo.register_session(CallerIdentity(PARENT))
        # First event admits both. Later deliberately allow only one attempt.
        self.collect()
        with other.open("ab") as stream:
            stream.write(json.dumps(usage("late-old", thread=PARENT)).encode() + b"\n")
        with mock.patch.object(lifecycle, "MAX_SOURCES", 1):
            for n in range(4):
                with self.path.open("ab") as stream:
                    stream.write(json.dumps(usage(f"new-{n}")).encode() + b"\n")
                self.collect(self.payload("Stop"))
        with self.repo.connection() as connection:
            keys = {row[0] for row in connection.execute("SELECT response_id FROM usage_responses")}
            audits = connection.execute("SELECT count(*) FROM usage_scan_state WHERE lane='audit'").fetchone()[0]
        self.assertIn("late-old", keys)
        self.assertEqual(audits, 2)

    def test_expired_event_budget_is_neutral_and_later_event_continues(self):
        before = self.repo.path.read_bytes()
        with mock.patch.object(lifecycle, "EVENT_SECONDS", -1):
            result = self.collect()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(before, self.repo.path.read_bytes())
        self.assertEqual(self.collect()["collected_sources"], 1)

    def test_partial_event_budgets_advance_later_inventory_pages_and_audits(self):
        from contextlib import ExitStack
        from task_governance_tool import usage_incremental_repository, usage_incremental_adapter, usage_sources
        # More than one 15-candidate page. Every busy event admits only three
        # completed source attempts, then its processing/DB deadline expires.
        for n in range(26):
            path = self.logs / f"rollout-{n}-{THREAD}_{n:08x}-89ab-7cde-8fab-0123456789ab.jsonl"
            self.write(path, THREAD, usage(f"segment-{n}"))
        clock = [monotonic()]
        attempts = [0]
        original = self.repo.collect_source
        def bounded(source, *, lane="ingest", **limits):
            result = original(source, lane=lane, **limits)
            if lane == "ingest":
                attempts[0] += 1
                if attempts[0] == 3:
                    clock[0] += lifecycle.EVENT_SECONDS + 1
            return result
        with ExitStack() as patches:
            for module in (lifecycle, usage_incremental_repository, usage_incremental_adapter, usage_sources):
                patches.enter_context(mock.patch.object(module, "monotonic", side_effect=lambda: clock[0]))
            patches.enter_context(mock.patch.object(self.repo, "collect_source", side_effect=bounded))
            for n in range(36):
                attempts[0] = 0
                with self.path.open("ab") as stream:
                    stream.write(json.dumps(usage(f"ongoing-{n}")).encode() + b"\n")
                self.collect()
        self.repo.deadline = float("inf")
        with self.repo.connection() as connection:
            responses = {row[0] for row in connection.execute("SELECT response_id FROM usage_responses")}
            audited = connection.execute("SELECT count(*) FROM usage_scan_state WHERE lane='audit'").fetchone()[0]
        self.assertTrue({f"segment-{n}" for n in range(26)} <= responses)
        self.assertEqual(len(self.repo.registered_sources()[1]), 27)
        self.assertEqual(audited, 27)
        self.assertGreaterEqual(self.repo.discovery_position()[1]["cycle"], 2)

    def test_missing_known_source_is_a_gap_only_after_inventory_cycle(self):
        self.collect()
        self.path.unlink()
        self.collect(self.payload("Stop", transcript_path=None))
        self.assertIn("source_unreadable", self.repo.summary()["diagnostics"])

    def test_conflicting_published_bytes_are_not_overwritten(self):
        from task_governance_tool.usage_evidence_service import _publish
        from task_governance_tool.usage_values import UsageError
        path = self.project / "snapshot.json"
        _publish(path, {"numerical": 1}, self.project, immutable=True)
        path.write_bytes(b'PRIVATE corrupted')
        with self.assertRaises(UsageError):
            _publish(path, {"numerical": 1}, self.project, immutable=True)
        self.assertEqual(path.read_bytes(), b'PRIVATE corrupted')


if __name__ == "__main__":
    unittest.main()
