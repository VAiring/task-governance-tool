"""Fair finite slices, inventory continuation, gaps and neutral event budgets."""

from dataclasses import replace
import json
from time import monotonic
import unittest
from unittest import mock
from types import SimpleNamespace

from tests import test_usage_lifecycle as support
from tests.test_usage_collection import THREAD, PARENT, TURN, usage
from task_governance_tool import usage_lifecycle as lifecycle
from task_governance_tool.usage_incremental_repository import UsageIncrementalRepository
from task_governance_tool.usage_sources import locate_slice, source_roots
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_values import UsageError


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

    def test_current_large_backlog_and_331_historical_sources_both_advance(self):
        # One large current segment plus 331 registered past participants. The
        # assertion is bounded progress, not a wall-time performance threshold.
        participants = {f"{number:08x}-89ab-7cde-8fab-0123456789ab" for number in range(331)}
        lifecycle.registered_participants.return_value = frozenset(participants | {THREAD})
        for number, thread in enumerate(sorted(participants)):
            path = self.logs / f"rollout-{number:03d}-{thread}.jsonl"
            self.write(path, thread, usage(f"past-{number}", thread=thread))
        filler = {"type": "unrecognized", "payload": {"private": "x" * 260000}}
        self.write(self.path, THREAD, *([filler] * 34), usage("current-end"))
        segment = self.logs / f"rollout-second-{THREAD}_{PARENT}.jsonl"
        self.write(segment, THREAD, usage("segment-end"))
        self.assertGreater(self.path.stat().st_size, 8 * 1024 * 1024)
        source_id = lifecycle.source_hint(THREAD, str(self.path),
                      source_roots({"CODEX_HOME": str(self.host)}), self.project).source_id
        original = self.repo.collect_source
        for number in range(8):
            calls = []
            def tracked(source, *, lane="ingest", **limits):
                calls.append((source.source_id, lane))
                return original(source, lane=lane, **limits)
            with mock.patch.object(self.repo, "collect_source", side_effect=tracked):
                self.collect()
            self.assertLessEqual(sum(lane == "ingest" for _, lane in calls), lifecycle.MAX_SOURCES)
            audited = [identity for identity, lane in calls if lane == "audit"]
            self.assertEqual(len(audited), len(set(audited)))
            if self.repo.cursor(source_id, THREAD).offset == self.path.stat().st_size:
                break
        else:
            self.fail("current backlog did not use the bounded burst")
        with self.repo.connection() as connection:
            rows = {row[0] for row in connection.execute("SELECT response_id FROM usage_responses")}
        self.assertIn("current-end", rows)
        self.assertTrue(any(row.startswith("past-") for row in rows))
        # Later endpoint and duplicate observations preserve prior counters and
        # are caught without replaying an original Task or its completion.
        with self.path.open("ab") as stream:
            stream.write(json.dumps(usage("late-end")).encode() + b"\n")
            stream.write(json.dumps(usage("current-end")).encode() + b"\n")
        self.collect(self.payload("Stop"))
        with self.repo.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_responses WHERE response_id='current-end'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_responses WHERE response_id='late-end'").fetchone()[0], 1)
        # The same admitted source collected in one large slice has the same
        # original responses/counters as repeated events plus late append.
        baseline = UsageIncrementalRepository(self.project / "baseline.sqlite", *self.repo.basis)
        baseline.initialize()
        source = lifecycle.source_hint(THREAD, str(self.path), source_roots({"CODEX_HOME": str(self.host)}), self.project)
        baseline.collect_source(source, max_bytes=16 * 1024 * 1024, max_records=10000)
        statement = ("SELECT response.response_id,response.input_tokens,response.output_tokens,response.total_tokens "
                     "FROM usage_responses response JOIN usage_observations observation USING(provider,response_id) "
                     "WHERE observation.source_id=? ORDER BY response.response_id")
        with self.repo.connection() as actual, baseline.connection() as expected:
            self.assertEqual([tuple(row) for row in actual.execute(statement, (source_id,))],
                             [tuple(row) for row in expected.execute(statement, (source_id,))])

    def test_audit_deadline_does_not_discard_pending_page_candidate(self):
        self.collect()
        # Begin a fresh page containing the already known source, then expire
        # only after audit has returned. No ingest took place on that page.
        revision, position = self.repo.discovery_position()
        before = self.repo.unattempted_sources(self.repo.registered_sources()[1], position["attempt_floor"])
        self.assertTrue(before)
        original = self.repo.collect_source
        def expire(source, *, lane="ingest", **limits):
            if lane == "ingest":
                raise UsageError("usage_pending")
            return original(source, lane=lane, **limits)
        with mock.patch.object(self.repo, "collect_source", side_effect=expire):
            result = self.collect(self.payload("Stop"))
        self.assertIn("usage_pending", result["diagnostics"])
        self.assertEqual(self.repo.discovery_position(), (revision, position))
        self.assertEqual(self.repo.unattempted_sources(self.repo.registered_sources()[1], position["attempt_floor"]), before)
        self.collect(self.payload("Stop"))
        self.assertGreater(self.repo.discovery_position()[0], revision)

    def test_exact_current_hint_precedes_an_older_current_segment_after_oldest(self):
        sources = {key * 64: SimpleNamespace(thread_id=thread)
                   for key, thread in (("0", PARENT), ("1", THREAD), ("2", PARENT), ("f", THREAD))}
        for key in sources:
            self.repo.attempted(key)
        self.assertEqual(self.repo.work_order(sources, THREAD, hint_id="f" * 64),
                         ["0" * 64, "f" * 64, "1" * 64, "2" * 64])
        self.assertEqual(self.repo.work_order(sources, THREAD), list(sources))

    def test_conflicting_published_bytes_are_not_overwritten(self):
        from task_governance_tool.usage_evidence_service import _publish
        from task_governance_tool.usage_values import UsageError
        path = self.project / "snapshot.json"
        _publish(path, {"numerical": 1}, self.project, immutable=True)
        path.write_bytes(b'PRIVATE corrupted')
        with self.assertRaises(UsageError):
            _publish(path, {"numerical": 1}, self.project, immutable=True)
        self.assertEqual(path.read_bytes(), b'PRIVATE corrupted')


class CatchupSchedulingTests(unittest.TestCase):
    def fixture(self, *, pending="more_records", progressed=True, replay=False):
        sources = {key: SimpleNamespace(source_id=key, thread_id=thread)
                   for key, thread in (("old", PARENT), ("old-segment", THREAD), ("hint", THREAD), ("past", PARENT))}
        repository = mock.Mock()
        repository.work_order.return_value = ["old", "hint", "old-segment", "past"]
        repository.collect_source.return_value = {"pending": pending, "progressed": progressed, "replay": replay}
        return repository, sources

    def test_current_segments_round_robin_under_same_total_budget(self):
        repository, sources = self.fixture()
        count, gaps = lifecycle._collect_incremental(repository, sources, sources, THREAD, sources["hint"], float("inf"))
        calls = [(call.args[0].source_id, call.kwargs.get("lane", "ingest"))
                 for call in repository.collect_source.call_args_list]
        self.assertEqual([identity for identity, lane in calls if lane == "ingest"],
                         ["old", "hint", "old-segment", "hint", "old-segment", "past"])
        self.assertEqual([identity for identity, lane in calls if lane == "audit"],
                         ["old", "hint", "old-segment", "past"])
        self.assertEqual((count, gaps), (4, set()))
        repository.work_order.assert_called_once_with(sources, THREAD, hint_id="hint")

    def test_partial_tail_replay_no_progress_and_errors_never_repeat(self):
        for pending, progressed, replay in (("partial_tail", True, False), ("none", False, False),
                                            ("more_records", False, False), ("more_records", True, True)):
            with self.subTest(pending=pending, progressed=progressed, replay=replay):
                repository, sources = self.fixture(pending=pending, progressed=progressed, replay=replay)
                lifecycle._collect_incremental(repository, sources, sources, THREAD, sources["hint"], float("inf"))
                ingests = [call.args[0].source_id for call in repository.collect_source.call_args_list if "lane" not in call.kwargs]
                self.assertEqual(len(ingests), len(set(ingests)))
                if pending == "more_records" and not progressed:
                    repository.attempted.assert_not_called()
        for code in ("cursor_stale", "source_changed", "usage_pending"):
            with self.subTest(code=code):
                repository, sources = self.fixture()
                repository.collect_source.side_effect = UsageError(code)
                count, gaps = lifecycle._collect_incremental(repository, sources, sources, THREAD, sources["hint"], float("inf"))
                self.assertEqual((count, gaps), (0, {code}))
                self.assertEqual(repository.collect_source.call_count, len(sources))
                self.assertEqual(repository.attempted.call_count, 0 if code == "usage_pending" else len(sources))

    def test_total_budget_one_keeps_oldest_and_never_bursts(self):
        repository, sources = self.fixture()
        with mock.patch.object(lifecycle, "MAX_SOURCES", 1):
            count, gaps = lifecycle._collect_incremental(repository, sources, sources, THREAD, sources["hint"], float("inf"))
        self.assertEqual(count, 1)
        self.assertEqual(gaps, {"usage_pending"})
        self.assertEqual([call.args[0].source_id for call in repository.collect_source.call_args_list], ["old", "old"])

    def test_extra_current_slices_share_the_existing_sixteen_attempt_limit(self):
        repository, sources = self.fixture()
        for number in range(20):
            sources[str(number)] = SimpleNamespace(source_id=str(number), thread_id=PARENT)
        repository.work_order.return_value = list(sources)
        count, gaps = lifecycle._collect_incremental(repository, sources, sources, THREAD, sources["hint"], float("inf"))
        calls = repository.collect_source.call_args_list
        ingests = [call.args[0].source_id for call in calls if "lane" not in call.kwargs]
        audits = [call.args[0].source_id for call in calls if call.kwargs.get("lane") == "audit"]
        self.assertEqual(len(ingests), lifecycle.MAX_SOURCES)
        self.assertEqual(len(audits), len(set(audits)))
        self.assertEqual(count, len(set(ingests)))
        self.assertEqual(gaps, {"usage_pending"})


if __name__ == "__main__":
    unittest.main()
