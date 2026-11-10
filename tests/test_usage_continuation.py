"""One intake, durable restart, finite traversal and actual numerical publication."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
from time import monotonic, sleep
import unittest
from unittest import mock

from tests.test_usage_collection import THREAD, TURN, event, usage
from tests.test_viewer_maintenance import make_target
from task_governance_tool import usage_continuation as continuation
from task_governance_tool import usage_continuation_state as state
from task_governance_tool import usage_continuation_repository as progress
from task_governance_tool.usage_continuation_service import UsageServiceObserver
from task_governance_tool.usage_evidence_service import repository_for
from task_governance_tool.usage_sources import source_roots, locator_root_fingerprints
from task_governance_tool.state_resolver import observe_current_root
from task_governance_tool.usage_values import UsageError
from task_governance_tool.artifact_lock import ArtifactLockError
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_collection import collect_registered, register_source
from task_governance_tool.usage_repository import UsageRepository


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        target = make_target(self.root, enabled=False)
        self.target = replace(target, binding_path_hash=observe_current_root(target.project.canonical_repo).canonical_path_hash,
                              binding_generation=1)
        self.repo = repository_for(self.target)
        self.repo.initialize()
        self.host = self.root / "host"
        directory = self.host / "sessions/2026/11/09"
        directory.mkdir(parents=True)
        self.path = directory / f"rollout-fixture-{THREAD}.jsonl"
        self.environment = {"CODEX_HOME": str(self.host)}
        self.roots = source_roots(self.environment)
        self.write()
        self.payload = {"hook_event_name": "SessionStart", "cwd": str(self.target.project.canonical_repo),
                        "session_id": THREAD, "transcript_path": str(self.path)}

    def write(self, count=20, tail=b""):
        rows = [event("session_meta", id=THREAD, cwd=str(self.target.project.canonical_repo), model_provider="openai"),
                event("turn_context", turn_id=TURN, model="test-model")]
        rows.extend(usage(f"r{i}") for i in range(count))
        self.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows) + tail)

    def read(self):
        with state.lease(self.target, "state") as root:
            return state.read(root, self.target)

    def intake(self):
        # The real Hook publishes intent/captures the locator but cannot take
        # execution ownership. No synthetic later Hook is used to finish it.
        with state.lease(self.target, "execution", create=True):
            result = continuation.collect_hook(self.repo, self.target, self.payload, self.roots,
                self.target.project.canonical_repo, THREAD, monotonic() + 20)
        self.assertIn("usage_pending", result[1])
        return self.read()

    def run_slice(self, expected=None, seconds=20):
        return continuation.run_slice(self.target, self.repo, self.roots, expected or self.read(),
                                      deadline=monotonic() + seconds)

    def drain(self, limit=30):
        for _ in range(limit):
            result = self.run_slice()
            if result is not None and result["status"] == "caught_up":
                return result
        self.fail(f"did not catch up: {self.read()}")

    def test_one_intake_finishes_without_later_event_and_preserves_core(self):
        before = self.target.db_path.read_bytes()
        request = self.intake()
        self.assertEqual(request["status"], "requested")
        done = self.drain()
        self.assertEqual(done["request"], request["request"])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 2400)
        self.assertTrue(self.target.resolved_usage_index.exists())
        self.assertEqual(self.target.db_path.read_bytes(), before)

    def test_process_restart_retains_request_and_exact_numbers(self):
        request = self.intake()
        collect = self.repo.collect_source
        with mock.patch.object(self.repo, "collect_source", side_effect=lambda *args, **kwargs:
                collect(*args, **{**kwargs, "max_records": 3})):
            for _ in range(6):
                self.run_slice()
                if self.repo.summary()["models"]:
                    break
        partial = self.repo.summary()["models"][0]["response_count"]
        self.assertGreater(partial, 0)
        self.assertLess(partial, 20)
        self.repo = repository_for(self.target)
        self.drain()
        self.assertEqual(self.read()["request"], request["request"])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_partial_input_remains_unfinished_then_recovers_without_hook(self):
        self.write(tail=json.dumps(usage("later")).encode())
        self.intake()
        for _ in range(6):
            self.run_slice()
        self.assertNotEqual(self.read()["status"], "caught_up")
        with self.path.open("ab") as stream:
            stream.write(b"\n")
        self.drain()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 21)

    def test_superseding_intake_cannot_be_acknowledged_by_old_slice(self):
        before = self.intake()
        self.intake()
        self.assertIsNone(self.run_slice(before))
        self.assertNotEqual(self.read()["request"], before["request"])
        self.drain()

    def test_rotation_is_not_completion_evidence(self):
        self.intake()
        for item in self.repo.locator_page(locator_root_fingerprints(self.roots)):
            self.repo.begin_locator_attempt(item)
            self.repo.attempted(item.locator.source_id)
        self.assertEqual(self.repo.summary()["models"], [])
        self.drain()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def source_pass(self):
        saved = self.read()
        round_ = progress.source_round(self.repo, progress.begin(self.repo))
        return state.update(self.target, saved, round_=round_, status="pending", intakes=[])

    def publication_pass(self):
        self.intake()
        done = self.drain()
        return state.update(self.target, done, round_={**done["round"], "phase": "publish"}, status="pending")

    def confirmation_pass(self):
        self.publication_pass()
        with mock.patch.object(progress, "completion", side_effect=UsageError("usage_pending")):
            with self.assertRaises(UsageError):
                self.run_slice()
        self.assertEqual(self.read()["round"]["phase"], "confirm")
        self.assertNotEqual(self.read()["status"], "caught_up")

    def add_core_participant(self, *, transcript=True):
        from contextlib import closing
        from task_governance_tool import storage, tasks
        participant = "01234567-89ab-7cde-8fab-0123456789ac"
        with closing(storage.connect_initialized(self.target)) as core:
            tasks.add_task(core, self.target.project, database_target=self.target,
                           title="Continuation participant", status="in_progress", caller=CallerIdentity(participant))
            core.commit()
        if transcript:
            rows = [event("session_meta", id=participant, cwd=str(self.target.project.canonical_repo), model_provider="openai"),
                    event("turn_context", turn_id=TURN, model="test-model"), usage("new-participant", thread=participant)]
            self.path.with_name(f"rollout-added-{participant}.jsonl").write_bytes(
                b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        return participant

    def test_invalid_first_inventory_page_does_not_hide_later_valid_sources(self):
        self.path.write_bytes(b"invalid header\n")
        for number in range(18):
            rows = [event("session_meta", id=THREAD, cwd=str(self.target.project.canonical_repo), model_provider="openai"),
                    event("turn_context", turn_id=TURN, model="test-model"), usage(f"later-{number}")]
            self.path.with_name(f"rollout-later-{number:02d}-{THREAD}.jsonl").write_bytes(
                b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        self.intake()
        for _ in range(32):
            result = self.run_slice()
            self.assertNotEqual(result["status"], "caught_up")
            models = self.repo.summary()["models"]
            if models and models[0]["response_count"] == 18:
                break
        self.assertEqual(models[0]["response_count"], 18)

    def test_publication_admitted_participant_requires_new_inventory(self):
        self.publication_pass()
        self.add_core_participant()
        for _ in range(30):
            result = self.run_slice()
            if result["status"] == "caught_up":
                break
        self.assertEqual(result["status"], "caught_up")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 21)

    def test_post_discovery_diagnosis_interruption_cannot_hide_core_participant(self):
        self.intake()
        self.add_core_participant(transcript=False)
        interrupted = False
        for _ in range(12):
            with mock.patch.object(self.repo, "missing_sessions", side_effect=UsageError("usage_pending")):
                try:
                    self.run_slice()
                except UsageError as error:
                    self.assertEqual(error.code, "usage_pending")
                    interrupted = True
            if interrupted:
                break
        self.assertTrue(interrupted)
        results = [self.run_slice() for _ in range(6)]
        self.assertNotIn("caught_up", [item["status"] for item in results])
        self.assertIn("waiting_input", [item["status"] for item in results])

    def test_post_publication_confirmation_has_its_own_budget(self):
        from task_governance_tool import usage_evidence_service as service
        self.publication_pass()
        publish, waiting = service.refresh_usage, progress.waiting
        publications = []
        for _ in range(5):
            clock = [monotonic()]
            deadline = clock[0] + 20
            def charge():
                clock[0] += 12
                if clock[0] >= deadline:
                    raise UsageError("usage_pending")
            def publish_with_cost(*args, **kwargs):
                charge()
                result = publish(*args, **kwargs)
                publications.append(result)
                return result
            def waiting_with_cost(*args, **kwargs):
                charge()
                return waiting(*args, **kwargs)
            with mock.patch.object(service, "refresh_usage", side_effect=publish_with_cost), \
                    mock.patch.object(progress, "waiting", side_effect=waiting_with_cost), \
                    mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
                try:
                    result = continuation.run_slice(self.target, self.repo, self.roots, self.read(), deadline=deadline)
                except UsageError as error:
                    self.assertEqual(error.code, "usage_pending")
                    continue
            if result["status"] == "caught_up":
                break
        self.assertEqual(self.read()["status"], "caught_up")
        self.assertEqual(len(publications), 1)

    def test_publication_deadline_retains_confirmation_across_repository_reopen(self):
        from task_governance_tool import usage_evidence_service as service
        self.publication_pass()
        publish = service.refresh_usage
        clock = [monotonic()]
        deadline = clock[0] + 20
        def publish_at_deadline(*args, **kwargs):
            result = publish(*args, **kwargs)
            clock[0] = deadline
            return result
        with mock.patch.object(service, "refresh_usage", side_effect=publish_at_deadline), \
                mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
            saved = continuation.run_slice(self.target, self.repo, self.roots, self.read(), deadline=deadline)
        self.assertEqual(saved["round"]["phase"], "confirm")
        self.repo = repository_for(self.target)
        with mock.patch.object(service, "refresh_usage", side_effect=AssertionError("restarted completed audit")):
            self.assertEqual(self.run_slice()["status"], "caught_up")

    def test_confirmation_rechecks_core_and_numerical_changes(self):
        self.confirmation_pass()
        self.add_core_participant()
        stale = self.run_slice()
        self.assertEqual(stale["round"]["phase"], "publish")
        self.assertNotEqual(stale["status"], "caught_up")
        self.drain()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 21)
        done = self.read()
        state.update(self.target, done, round_={**done["round"], "phase": "confirm"}, status="pending")
        self.write(count=21)
        self.repo.collect_source(SourceInput(THREAD, self.path, self.roots[0], self.target.project.canonical_repo))
        stale = self.run_slice()
        self.assertEqual(stale["round"]["phase"], "publish")
        self.assertNotEqual(stale["status"], "caught_up")
        self.drain()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 22)

    def test_confirmation_rechecks_index_after_completion_reads(self):
        self.confirmation_pass()
        waiting = progress.waiting
        def change_file(*args, **kwargs):
            result = waiting(*args, **kwargs)
            self.target.resolved_usage_index.write_bytes(b"{}")
            return result
        with mock.patch.object(progress, "waiting", side_effect=change_file):
            result = self.run_slice()
        self.assertNotEqual(result["status"], "caught_up")
        self.assertEqual(result["round"]["phase"], "publish")
        self.drain()
        self.assertNotEqual(self.target.resolved_usage_index.read_bytes(), b"{}")

    def test_confirmation_missing_index_returns_to_publication(self):
        self.confirmation_pass()
        self.target.resolved_usage_index.rename(self.root / "retained-index.json")
        result = self.run_slice()
        self.assertEqual(result["round"]["phase"], "publish")
        self.assertNotEqual(result["status"], "caught_up")
        self.drain()
        self.assertEqual(self.target.resolved_usage_index.read_bytes(), (self.root / "retained-index.json").read_bytes())

    def test_confirmation_unavailable_preserves_finished_publication(self):
        from task_governance_tool import usage_evidence_service as service
        self.confirmation_pass()
        with mock.patch.object(service, "confirm_usage", side_effect=UsageError("usage_unavailable")):
            result = self.run_slice()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["round"]["phase"], "confirm")
        with mock.patch.object(service, "refresh_usage", side_effect=AssertionError("restarted completed audit")):
            self.assertEqual(self.run_slice()["status"], "caught_up")

    def test_deadline_after_committed_source_preserves_frontier_for_next_source(self):
        from task_governance_tool.usage_sources import locator_from_source
        self.intake()
        other = self.path.with_name(f"rollout-second-{THREAD}.jsonl")
        rows = [event("session_meta", id=THREAD, cwd=str(self.target.project.canonical_repo), model_provider="openai"),
                event("turn_context", turn_id=TURN, model="test-model"), usage("second-source")]
        other.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        self.repo.remember_locator(locator_from_source(
            SourceInput(THREAD, other, self.roots[0], self.target.project.canonical_repo), self.roots))
        self.source_pass()
        collect = self.repo.collect_source
        frontiers = []
        for _ in range(2):
            deadline = monotonic() + 20
            clock = [monotonic()]
            original = self.read()
            def collect_then_exhaust(*args, **kwargs):
                result = collect(*args, **kwargs)
                if kwargs.get("lane") != "audit":
                    clock[0] = deadline + 1
                    self.repo.deadline = 0
                return result
            with mock.patch.object(self.repo, "collect_source", side_effect=collect_then_exhaust), \
                    mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
                result = continuation.run_slice(self.target, self.repo, self.roots, original, deadline=deadline)
            self.assertEqual(result["status"], "budget_exhausted")
            frontiers.append(result["round"]["after_order"])
            # A service exception handler holding the old snapshot cannot undo
            # progress already saved by its slice.
            self.assertIsNone(state.update(self.target, original, round_=original["round"], status="unavailable"))
            self.repo.deadline = float("inf")
        self.assertGreater(frontiers[1], frontiers[0])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 21)
        self.drain()
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 2520)

    def test_stop_after_source_preserves_progress_without_fresh_database_read(self):
        self.intake()
        saved = self.source_pass()
        collect = self.repo.collect_source
        stopped = [False]
        def collect_then_stop(*args, **kwargs):
            result = collect(*args, **kwargs)
            if kwargs.get("lane") != "audit":
                stopped[0] = True
                self.repo.deadline = 0
            return result
        with mock.patch.object(self.repo, "collect_source", side_effect=collect_then_stop):
            result = continuation.run_slice(self.target, self.repo, self.roots, saved,
                deadline=monotonic() + 20, stopped=lambda: stopped[0])
        self.assertGreater(result["round"]["after_order"], 0)
        self.assertNotEqual(result["status"], "caught_up")
        self.repo.deadline = float("inf")
        self.drain()

    def second_source(self):
        from task_governance_tool.usage_sources import locator_from_source
        other = self.path.with_name(f"rollout-second-{THREAD}.jsonl")
        rows = [event("session_meta", id=THREAD, cwd=str(self.target.project.canonical_repo), model_provider="openai"),
                event("turn_context", turn_id=TURN, model="test-model"), usage("second-source")]
        other.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        source = SourceInput(THREAD, other, self.roots[0], self.target.project.canonical_repo)
        self.repo.remember_locator(locator_from_source(source, self.roots))
        return other

    def assert_interrupted_source_gets_fresh_slice(self, charged_lane):
        self.intake()
        other = self.second_source()
        self.source_pass()
        collect = self.repo.collect_source
        attempts = []
        for _ in range(2):
            clock = [monotonic()]
            deadline = clock[0] + 20
            def collect_with_cost(source, **kwargs):
                lane = kwargs.get("lane", "ingest")
                if lane == charged_lane:
                    attempts.append(source.path)
                    if clock[0] + 12 > deadline:
                        clock[0] = deadline
                        raise UsageError("usage_pending")
                result = collect(source, **kwargs)
                if lane == charged_lane:
                    clock[0] += 12
                return result
            with mock.patch.object(self.repo, "collect_source", side_effect=collect_with_cost), \
                    mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
                result = continuation.run_slice(self.target, self.repo, self.roots, self.read(), deadline=deadline)
        self.assertEqual(attempts, [self.path, other, other])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 21)
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 2520)
        self.assertEqual(self.drain()["status"], "caught_up")

    def test_deadline_during_later_ingest_retries_it_with_fresh_slice(self):
        self.assert_interrupted_source_gets_fresh_slice("ingest")

    def test_deadline_during_later_audit_retries_it_with_fresh_slice(self):
        self.assert_interrupted_source_gets_fresh_slice("audit")

    def test_pending_ingest_result_keeps_source_before_next_slice(self):
        self.intake()
        other = self.second_source()
        self.source_pass()
        collect = self.repo.collect_source
        attempts = []
        first_partial = [True]
        for _ in range(2):
            clock = [monotonic()]
            deadline = clock[0] + 20
            def collect_partial(source, **kwargs):
                if kwargs.get("lane") == "audit":
                    return collect(source, **kwargs)
                attempts.append(source.path)
                if source.path == other and first_partial[0]:
                    first_partial[0] = False
                    result = collect(source, **{**kwargs, "max_records": 1})
                    self.assertEqual(result["pending"], "more_records")
                    return result
                return collect(source, **kwargs)
            with mock.patch.object(self.repo, "collect_source", side_effect=collect_partial), \
                    mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
                result = continuation.run_slice(self.target, self.repo, self.roots, self.read(), deadline=deadline)
        self.assertEqual(attempts, [self.path, other, other])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 21)
        self.assertEqual(self.drain()["status"], "caught_up")

    def test_recurring_slow_review_admission_cannot_starve_registered_source(self):
        self.intake()
        collect = self.repo.collect_source
        attempts = []
        for _ in range(6):
            clock = [monotonic()]
            deadline = clock[0] + 20
            def pending(*args, **kwargs):
                clock[0] += 12
                args[5].add("usage_pending")
            def collect_with_cost(source, **kwargs):
                if kwargs.get("lane") != "audit":
                    attempts.append(source.path)
                    if clock[0] + 12 > deadline:
                        clock[0] = deadline
                        raise UsageError("usage_pending")
                    clock[0] += 12
                return collect(source, **kwargs)
            with mock.patch("task_governance_tool.usage_locator_collection._pending", side_effect=pending), \
                    mock.patch.object(self.repo, "collect_source", side_effect=collect_with_cost), \
                    mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
                continuation.run_slice(self.target, self.repo, self.roots, self.read(), deadline=deadline)
        self.assertTrue(attempts)
        self.assertTrue(self.repo.summary()["models"], "recurring metadata starved source ingestion")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 2400)

    def test_recurring_slow_failed_intake_leaves_source_and_publication_opportunities(self):
        self.intake()
        collect = self.repo.collect_source
        for _ in range(8):
            clock = [monotonic()]
            deadline = clock[0] + 20
            def capture(*args, **kwargs):
                clock[0] += 12
                raise UsageError("source_unreadable")
            def ingest(source, **kwargs):
                if kwargs.get("lane") != "audit":
                    if clock[0] + 12 > deadline:
                        clock[0] = deadline
                        raise UsageError("usage_pending")
                    clock[0] += 12
                return collect(source, **kwargs)
            with mock.patch("task_governance_tool.usage_locator_collection._capture", side_effect=capture), \
                    mock.patch.object(self.repo, "collect_source", side_effect=ingest), \
                    mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
                continuation.run_slice(self.target, self.repo, self.roots, self.read(), deadline=deadline)
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertTrue(self.target.resolved_usage_index.exists())
        self.assertTrue(self.read()["intakes"])
        self.assertNotEqual(self.read()["status"], "caught_up")
        self.drain()

    def test_metadata_inventory_does_not_spend_source_budget(self):
        from task_governance_tool import usage_locator_collection as located
        self.intake()
        inventory, collect = located._inventory, self.repo.collect_source
        for _ in range(10):
            clock = [monotonic()]
            deadline = clock[0] + 20
            def discover(*args, **kwargs):
                clock[0] += 12
                return inventory(*args, **kwargs)
            def ingest(source, **kwargs):
                if kwargs.get("lane") != "audit":
                    if clock[0] + 12 > deadline:
                        clock[0] = deadline
                        raise UsageError("usage_pending")
                    clock[0] += 12
                return collect(source, **kwargs)
            with mock.patch.object(located, "_inventory", side_effect=discover), \
                    mock.patch.object(self.repo, "collect_source", side_effect=ingest), \
                    mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
                result = continuation.run_slice(self.target, self.repo, self.roots, self.read(), deadline=deadline)
            if result["status"] == "caught_up":
                break
        self.assertEqual(result["status"], "caught_up")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_pending_admission_exception_preserves_next_stage_across_repository_reopen(self):
        self.intake()
        self.run_slice()
        self.assertEqual(self.read()["round"]["phase"], "review")
        with mock.patch("task_governance_tool.usage_locator_collection._pending", side_effect=UsageError("usage_pending")):
            with self.assertRaises(UsageError):
                self.run_slice()
        saved = self.read()
        self.assertEqual(saved["round"]["phase"], "inventory")
        self.repo = repository_for(self.target)
        with mock.patch("task_governance_tool.usage_locator_collection._pending") as pending:
            self.run_slice()
            self.run_slice()
        pending.assert_not_called()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.drain()

    def test_retained_explicit_hint_promotes_after_separate_participant_discovery(self):
        renamed = self.path.with_name("explicit-only.jsonl")
        self.path.rename(renamed)
        self.path = renamed
        self.payload.update(hook_event_name="Stop", transcript_path=str(renamed))
        self.intake()
        self.assertEqual(self.repo.registered_sessions((THREAD,)), set())
        with mock.patch("task_governance_tool.usage_locator_collection._participants", return_value={THREAD}):
            result = self.drain()
        self.assertEqual(result["status"], "caught_up")
        self.assertEqual(result["intakes"], [])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_missing_discovered_participant_is_not_acknowledged_after_good_sources_finish(self):
        self.intake()
        missing = "01234567-89ab-7cde-8fab-0123456789ac"
        results = []
        with mock.patch("task_governance_tool.usage_locator_collection._participants", return_value={THREAD, missing}):
            for _ in range(10):
                results.append(self.run_slice())
        self.assertNotIn("caught_up", [result["status"] for result in results])
        self.assertIn("waiting_input", [result["status"] for result in results])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_publication_receives_fresh_budget_after_source_consumed_previous_slice(self):
        from task_governance_tool import usage_evidence_service as service
        self.intake()
        self.source_pass()
        collect, refresh = self.repo.collect_source, service.refresh_usage
        rejected = []
        for _ in range(12):
            clock = [monotonic()]
            deadline = clock[0] + 20
            def ingest(source, **kwargs):
                if kwargs.get("lane") != "audit":
                    clock[0] += 12
                return collect(source, **kwargs)
            def publish(*args, **kwargs):
                if clock[0] + 12 > deadline:
                    clock[0] = deadline
                    rejected.append(True)
                    raise UsageError("usage_pending")
                clock[0] += 12
                return refresh(*args, **kwargs)
            with mock.patch.object(self.repo, "collect_source", side_effect=ingest), \
                    mock.patch.object(service, "refresh_usage", side_effect=publish), \
                    mock.patch.object(continuation, "monotonic", side_effect=lambda: clock[0]):
                try:
                    result = continuation.run_slice(self.target, self.repo, self.roots, self.read(), deadline=deadline)
                except UsageError as error:
                    self.assertEqual(error.code, "usage_pending")
                    self.assertEqual(self.read()["round"]["phase"], "publish")
                    continue
            if result["status"] == "caught_up":
                break
        self.assertTrue(rejected)
        self.assertEqual(result["status"], "caught_up")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertTrue(self.target.resolved_usage_index.exists())

    def test_source_loss_after_hook_preserves_numerical_gap_and_publication(self):
        self.intake()
        source = SourceInput(THREAD, self.path, self.roots[0], self.target.project.canonical_repo)
        self.repo.collect_source(source)
        self.source_pass()
        self.path.rename(self.path.with_suffix(".unavailable"))
        results = [self.run_slice() for _ in range(3)]
        report = self.repo.summary()
        self.assertIn("source_unreadable", report["diagnostics"])
        self.assertEqual(report["models"][0]["response_count"], 20)
        self.assertIn("waiting_input", [result["status"] for result in results])
        from task_governance_tool.usage_evidence_service import refresh_usage
        published = refresh_usage(self.target)
        self.assertEqual(published["publication"], "current")
        self.assertIn("source_unreadable", self.repo.summary()["diagnostics"])

    def test_unreadable_source_does_not_hold_later_source(self):
        self.intake()
        self.repo.collect_source(SourceInput(THREAD, self.path, self.roots[0], self.target.project.canonical_repo))
        self.second_source()
        self.source_pass()
        self.path.rename(self.path.with_suffix(".unavailable"))
        result = self.run_slice()
        self.assertEqual(result["status"], "waiting_input")
        report = self.repo.summary()
        self.assertEqual(report["models"][0]["response_count"], 21)
        self.assertIn("source_unreadable", report["diagnostics"])

    def test_transient_source_failure_never_becomes_permanent_gap(self):
        self.intake()
        source = SourceInput(THREAD, self.path, self.roots[0], self.target.project.canonical_repo)
        self.repo.collect_source(source)
        for code in ("usage_pending", "cursor_stale", "source_changed"):
            self.source_pass()
            with mock.patch.object(self.repo, "collect_source", side_effect=UsageError(code)), \
                    mock.patch.object(self.repo, "record_gap", wraps=self.repo.record_gap) as record:
                self.run_slice()
            record.assert_not_called()
            self.assertNotEqual(self.read()["status"], "caught_up")
        self.drain()

    def test_waiting_input_rediscovers_archive_without_another_hook(self):
        self.write(tail=json.dumps(usage("later")).encode())
        self.intake()
        for _ in range(4):
            self.run_slice()
        archived = self.host / "archived_sessions"
        archived.mkdir(exist_ok=True)
        moved = archived / self.path.name
        self.path.rename(moved)
        with moved.open("ab") as stream:
            stream.write(b"\n")
        for _ in range(8):
            self.run_slice()
            if self.repo.summary()["models"][0]["response_count"] == 21:
                break
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 21)
        # The retained original's missing-source gap is still real.
        self.assertNotEqual(self.read()["status"], "caught_up")

    def test_duplicate_owner_is_excluded_and_release_is_recoverable(self):
        self.intake()
        with state.lease(self.target, "execution"):
            with self.assertRaises(ArtifactLockError) as found:
                self.run_slice()
            self.assertTrue(found.exception.contended)
        self.drain()

    def test_failed_initial_capture_is_recovered_from_closed_intake(self):
        with mock.patch("task_governance_tool.usage_locator_collection._capture", side_effect=UsageError("usage_pending")):
            self.intake()
        self.assertEqual(self.repo.registered_sessions((THREAD,)), set())
        saved = self.read()
        self.assertEqual(len(saved["intakes"]), 1)
        self.assertNotIn("transcript_path", json.dumps(saved))
        self.repo = repository_for(self.target)
        self.drain()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertEqual(self.read()["intakes"], [])

    def test_duplicate_intakes_coalesce_without_losing_pending_metadata(self):
        self.intake()
        self.intake()
        self.assertEqual(len(self.read()["intakes"]), 1)
        self.drain()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_missing_intake_does_not_prevent_known_source_progress(self):
        self.intake()
        saved = self.read()
        missing = json.loads(json.dumps(saved["intakes"][0]))
        # A formerly admitted location can become unavailable after acceptance.
        from task_governance_tool.usage_sources import locator_from_source
        from dataclasses import asdict
        directory = self.host / "sessions/2026/11/10"
        directory.mkdir()
        missing["locator"] = asdict(locator_from_source(SourceInput(THREAD,
            directory / f"rollout-missing-{THREAD}.jsonl", self.roots[0], self.target.project.canonical_repo), self.roots))
        with state.lease(self.target, "state") as root:
            state.write(root, {**saved, "intakes": [missing, *saved["intakes"]]})
        for _ in range(5):
            self.run_slice()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertNotEqual(self.read()["status"], "caught_up")

    def test_continuation_matches_independent_full_replay(self):
        self.intake()
        self.drain()
        oracle = UsageRepository(self.root / "oracle.sqlite", self.target.project.project_id,
                                 self.target.binding_path_hash, self.target.binding_generation)
        oracle.initialize()
        source = SourceInput(THREAD, self.path, self.roots[0], self.target.project.canonical_repo)
        register_source(oracle, source, CallerIdentity(THREAD))
        expected = collect_registered(oracle, source)
        self.assertEqual(self.repo.summary()["models"], expected["models"])

    def test_publication_failure_preserves_request_and_is_retried(self):
        self.intake()
        with mock.patch("task_governance_tool.usage_evidence_service.refresh_usage",
                        return_value={"publication": "pending", "diagnostics": ["usage_unavailable"]}) as refresh:
            for _ in range(6):
                self.run_slice()
            self.assertGreater(refresh.call_count, 0)
        self.assertNotEqual(self.read()["status"], "caught_up")
        self.drain()

    def test_old_physical_store_checkpoint_is_not_reused(self):
        self.intake()
        self.run_slice()
        saved = self.read()
        with state.lease(self.target, "state") as root:
            state.write(root, {**saved, "status": "pending", "round": {**saved["round"], "identity": "0" * 64}})
        self.drain()
        self.assertNotEqual(self.read()["round"]["identity"], "0" * 64)

    def test_invalid_or_wrong_binding_record_is_not_repaired(self):
        before = self.intake()
        root = state.root_for(self.target)
        for data in (b'{"v":1,"v":1}', b'{}', json.dumps({**before, "binding_generation": 2}).encode()):
            (root / "continuation.json").write_bytes(data)
            with self.assertRaises(UsageError):
                self.read()
            self.assertEqual((root / "continuation.json").read_bytes(), data)

    def test_reserved_interrupted_temporary_is_recovered(self):
        self.intake()
        root = state.root_for(self.target)
        (root / ".continuation-next.json").write_bytes(b'{"v":')
        self.intake()
        self.assertFalse((root / ".continuation-next.json").exists())
        self.drain()

    def observer(self):
        item = UsageServiceObserver(skill_root=self.root, repo=self.target.project.canonical_repo,
            environment=self.environment, compatible=lambda: True, _poll_seconds=0.01)
        item._admit = lambda deadline: (self.target, repository_for(self.target))
        self.addCleanup(item.close)
        return item

    def test_service_runs_after_intake_and_stops_when_caught_up(self):
        self.intake()
        observer = self.observer()
        observer.start()
        until = monotonic() + 20
        observed = None
        while monotonic() < until:
            try:
                observed = self.read()
                if observed["status"] == "caught_up":
                    break
            except ArtifactLockError:
                pass
            sleep(0.02)
        self.assertIsNotNone(observed)
        self.assertEqual(observed["status"], "caught_up")
        self.assertTrue(observer.close())
        self.assertEqual(self.read()["status"], "caught_up")

    def test_service_without_actual_intake_neither_creates_state_nor_reads_sources(self):
        observer = self.observer()
        observer.start()
        sleep(0.1)
        self.assertFalse(self.target.resolved_usage_runtime.exists())
        self.assertEqual(self.repo.summary()["models"], [])
        self.assertTrue(observer.close())

    def test_shutdown_does_not_release_running_execution_lease(self):
        self.intake()
        entered, release = threading.Event(), threading.Event()
        original = continuation.run_slice
        def held(*args, **kwargs):
            with state.lease(self.target, "execution"):
                entered.set()
                release.wait(5)
            return original(*args, **kwargs)
        observer = self.observer()
        with mock.patch.object(continuation, "run_slice", side_effect=held):
            observer.start()
            self.assertTrue(entered.wait(5))
            self.assertFalse(observer.close())
            with self.assertRaises(ArtifactLockError):
                with state.lease(self.target, "service"):
                    pass
            release.set()
            observer._thread.join(5)
            self.assertFalse(observer._thread.is_alive())

    def test_unavailable_runtime_performs_no_collection(self):
        self.intake()
        observer = UsageServiceObserver(skill_root=self.root, repo=self.target.project.canonical_repo,
            environment=self.environment, compatible=lambda: False, _poll_seconds=0.01)
        self.addCleanup(observer.close)
        observer.start()
        observer._thread.join(5)
        self.assertEqual(self.repo.summary()["models"], [])
        self.assertEqual(self.read()["status"], "requested")


class ServiceBoundaryTests(unittest.TestCase):
    def test_continuation_source_loss_reaches_current_task_and_snapshot(self):
        from tests import test_usage_reconciliation_runtime as fixture
        from task_governance_tool import usage_evidence_service as service
        from task_governance_tool.usage_sources import locator_from_source
        helper = fixture.RuntimeTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        repository = helper.repository
        target = replace(helper.target, binding_path_hash=repository.basis[1], binding_generation=1)
        roots = (helper.root, helper.root / "archive")
        repository.remember_locator(locator_from_source(helper.source, roots))
        with state.lease(target, "state", create=True) as root:
            saved = state.request(root, target, locator_root_fingerprints(roots))
        saved = state.update(target, saved, round_=progress.source_round(repository, progress.begin(repository)),
                             status="pending")
        helper.source.path.rename(helper.source.path.with_suffix(".unavailable"))
        with mock.patch.object(service, "repository_for", return_value=repository):
            result = continuation.run_slice(target, repository, roots, saved, deadline=monotonic() + 20)
            self.assertEqual(result["status"], "waiting_input")
            self.assertEqual(service.refresh_usage(target)["publication"], "current")
        with helper.reader() as core:
            period = repository.read(core, task_id=helper.task_id)["periods"][0]
        self.assertIn("source_unreadable", period["gaps"])
        self.assertEqual(period["models"][0]["total_tokens"], 120)
        index = json.loads(target.resolved_usage_index.read_bytes())
        self.assertTrue(index["current_snapshot_ids"])
        for identity in index["current_snapshot_ids"]:
            snapshot = json.loads((target.resolved_usage_snapshots / (identity + ".json")).read_bytes())
            self.assertIn("source_unreadable", snapshot["gaps"])

    def test_disabled_policy_prevents_state_admission(self):
        observer = UsageServiceObserver(skill_root=Path.cwd(), repo=Path.cwd(), environment={}, compatible=lambda: True)
        with mock.patch("task_governance_tool.setup_feature_config.collection_allowed", return_value=False), \
                mock.patch("task_governance_tool.usage_lifecycle._target") as target:
            self.assertIsNone(observer._admit(monotonic() + 20))
        target.assert_not_called()

    def test_project_service_owns_once_and_reports_unfinished_close(self):
        from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
        root = Path.cwd()
        session = ProjectReviewWaitSession(ProjectConfig(root, root / "unused.mjs", root, "UTC"),
            runtime_identity=mock.Mock(inspect=mock.Mock(return_value={"comparison": "matching"})))
        with mock.patch("task_governance_tool.usage_continuation_service.UsageServiceObserver") as factory:
            factory.return_value.start.return_value = True
            factory.return_value.close.return_value = False
            self.assertTrue(session.start_usage_observer())
            self.assertFalse(session.start_usage_observer())
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(session.close(), {"ok": False, "error": "observer_cleanup_unknown"})
            self.assertFalse(session.start_usage_observer())


if __name__ == "__main__":
    unittest.main()
