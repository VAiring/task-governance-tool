"""Persisted location hints exercised through the admitted lifecycle path."""

from contextlib import closing
import json
from time import monotonic
import unittest
from unittest import mock

from tests import test_usage_lifecycle as support
from tests.test_usage_collection import THREAD, PARENT, usage
from tests.review_answer_fixture_support import review_fixture
from tests.usage_locator_fixture_support import prepare_pending_locator_assignment
from task_governance_tool import storage, usage_lifecycle as lifecycle
from task_governance_tool import usage_locator_collection as collection
from task_governance_tool import usage_locator_admission as admission
from task_governance_tool.usage_locator_repository import UsageLocatorRepository
from task_governance_tool.usage_sources import source_roots, locator_root_fingerprints
from task_governance_tool.usage_values import UsageError


class LocatorLifecycleTests(unittest.TestCase):
    write = support.UsageLifecycleTests.write
    payload = support.UsageLifecycleTests.payload
    collect = support.UsageLifecycleTests.collect

    def setUp(self):
        support.UsageLifecycleTests.setUp(self)
        self.repo = UsageLocatorRepository(self.repo.path, *self.repo.basis)
        self.repo.initialize()
        lifecycle.repository_for.return_value = self.repo
        self.enterContext(mock.patch.object(collection, "_participants", return_value=frozenset()))
        self.roots = source_roots({"CODEX_HOME": str(self.host)})

    def entries(self, pending=False):
        return self.repo.locator_page(locator_root_fingerprints(self.roots), pending=pending)

    def keys(self):
        with self.repo.connection() as connection:
            return {row[0] for row in connection.execute("SELECT response_id FROM usage_responses")}

    def no_inventory(self):
        return mock.patch.object(collection, "locate_slice", side_effect=UsageError("usage_pending"))

    def test_missing_file_then_restart_without_hint_or_discovery(self):
        self.path.unlink()
        with self.no_inventory():
            self.assertEqual(self.collect()["collected_sources"], 0)
            self.assertEqual(len(self.entries()), 1)
            self.repo = UsageLocatorRepository(self.repo.path, *self.repo.basis)
            lifecycle.repository_for.return_value = self.repo
            self.write(self.path, THREAD, usage())
            with mock.patch.object(self.repo, "registered_sources", side_effect=AssertionError("all sources")):
                result = self.collect(self.payload("Stop", transcript_path=""))
        self.assertEqual(result["collected_sources"], 1)
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 120)

    def test_partial_tail_later_parent_event_reads_saved_child_location(self):
        self.collect()
        tail = json.dumps(usage("late")).encode() + b"\n"
        with self.path.open("ab") as stream:
            stream.write(tail[:20])
        with self.no_inventory():
            self.collect(self.payload("Stop"))
            self.assertIn("partial_tail", self.repo.summary()["diagnostics"])
            with self.path.open("ab") as stream:
                stream.write(tail[20:])
            self.collect(self.payload(session_id=PARENT, transcript_path=""))
        self.assertIn("late", self.keys())
        self.assertNotIn("partial_tail", self.repo.summary()["diagnostics"])

    def test_exact_archive_copy_deduplicates_and_movement_keeps_old_gap(self):
        self.collect()
        archive = self.host / "archived_sessions"
        archive.mkdir()
        copied = archive / self.path.name
        copied.write_bytes(self.path.read_bytes())
        with copied.open("ab") as stream:
            stream.write(json.dumps(usage("archive-tail")).encode() + b"\n")
        with self.no_inventory():
            for _ in range(2):
                self.collect(self.payload("Stop", transcript_path=""))
            self.assertEqual(self.repo.summary()["models"][0]["response_count"], 2)
            self.path.unlink()
            for _ in range(2):
                self.collect(self.payload("Stop", transcript_path=""))
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 2)
        self.assertIn("source_unreadable", self.repo.summary()["diagnostics"])

    def test_current_hint_does_not_starve_old_segment_with_single_attempt(self):
        self.collect()
        old = self.logs / f"rollout-old-{PARENT}.jsonl"
        self.write(old, PARENT, usage("old", thread=PARENT))
        self.collect(self.payload(session_id=PARENT, transcript_path=str(old)))
        with old.open("ab") as stream:
            stream.write(json.dumps(usage("old-late", thread=PARENT)).encode() + b"\n")
        with self.no_inventory(), mock.patch.object(lifecycle, "MAX_SOURCES", 1):
            for n in range(4):
                new = self.logs / f"rollout-new-{n}-{THREAD}.jsonl"
                self.write(new, THREAD, usage(f"new-{n}"))
                self.collect(self.payload("Stop", transcript_path=str(new)))
        self.assertIn("old-late", self.keys())

    def test_wrong_header_is_never_admitted_and_can_later_recover(self):
        self.write(self.path, PARENT, usage(thread=PARENT))
        with self.no_inventory():
            self.assertEqual(self.collect()["collected_sources"], 0)
            self.assertEqual(self.keys(), set())
            self.assertEqual(len(self.entries()), 1)
            self.write(self.path, THREAD, usage())
            self.assertEqual(self.collect(self.payload("Stop", transcript_path=""))["collected_sources"], 1)

    def test_unknown_inventory_failure_keeps_direct_commit_and_neutral_protocol(self):
        with mock.patch.object(collection, "_participants", side_effect=UsageError("usage_pending")):
            result = self.collect()
        self.assertEqual(result["collected_sources"], 1)
        self.assertIn("usage_pending", result["diagnostics"])
        self.assertEqual(len(self.keys()), 1)

    def test_core_participant_snapshot_is_not_reloaded_for_hint_and_inventory(self):
        def participants(target, deadline):
            # A known core participant missed SessionStart. Its first lookup
            # must have the full residual budget, not capture's one-second cap.
            self.assertGreater(deadline - monotonic(), 5)
            return frozenset({THREAD})
        with mock.patch.object(collection, "_participants", side_effect=participants) as read:
            self.assertEqual(self.collect(self.payload("Stop"))["collected_sources"], 1)
        self.assertEqual(read.call_count, 1)

    def test_failed_participant_snapshot_is_not_retried_in_same_event(self):
        with mock.patch.object(collection, "_participants", side_effect=UsageError("usage_pending")) as read:
            self.assertEqual(self.collect(self.payload("Stop"))["collected_sources"], 0)
        self.assertEqual(read.call_count, 1)

    def test_early_metadata_wait_cannot_consume_direct_allowance(self):
        self.collect()
        with self.path.open("ab") as stream:
            stream.write(json.dumps(usage("later")).encode() + b"\n")
        seen = []
        def waiting(repository, target, roots, deadline, clock, gaps):
            seen.append(deadline)
            self.assertEqual(repository.deadline, deadline)
            self.assertLess(deadline - clock(), 1.01)
            raise UsageError("usage_pending")
        with mock.patch.object(collection, "_pending", side_effect=waiting), self.no_inventory():
            self.collect(self.payload("Stop"))
        self.assertIn("later", self.keys())
        self.assertLess(seen[0], self.repo.deadline)

    def test_root_change_does_not_reuse_saved_path_or_clear_prior_observations(self):
        self.collect()
        other = self.root / "other-home"
        other.mkdir()
        result = lifecycle.collect_event(self.payload("Stop", transcript_path=""), skill_root=self.project,
            repo=self.project, environment={"CODEX_HOME": str(other)})
        self.assertEqual(result["collected_sources"], 0)
        self.assertEqual(len(self.keys()), 1)
        self.assertIn("source_unreadable", self.repo.summary()["diagnostics"])

    def test_real_assignment_stop_then_receipt_then_parent_without_child_hint(self):
        with review_fixture(schema_version=33, contract=None) as fixture:
            observed = prepare_pending_locator_assignment(fixture)
            repository = UsageLocatorRepository(fixture.repo / "usage.sqlite",
                fixture.basis["project_id"], fixture.basis["project_path_hash"],
                fixture.basis["project_binding_generation"])
            repository.initialize()
            child = self.logs / f"rollout-child-{observed.reviewer}.jsonl"
            self.write(child, observed.reviewer, usage("review-usage", thread=observed.reviewer), project=fixture.repo)
            stop = {"hook_event_name": "SubagentStop", "session_id": observed.parent,
                "agent_id": observed.reviewer, "cwd": str(fixture.repo),
                "agent_transcript_path": str(child), "transcript_path": str(self.path)}

            def collect(payload):
                return lifecycle.collect_event(payload, skill_root=fixture.repo, repo=fixture.repo,
                    environment={"CODEX_HOME": str(self.host)})

            with mock.patch.object(lifecycle, "_target", return_value=fixture.target), \
                    mock.patch.object(lifecycle, "repository_for", return_value=repository), \
                    mock.patch.object(admission, "_paths", return_value=observed.paths), self.no_inventory():
                with mock.patch.object(repository, "collect_source", side_effect=AssertionError("unregistered body")):
                    self.assertEqual(collect(stop)["collected_sources"], 0)
                pending = repository.locator_page(locator_root_fingerprints(self.roots), pending=True)
                self.assertEqual(len(pending), 1)
                self.assertEqual(repository.registered_sessions((observed.reviewer,)), frozenset())
                # Another actual review Receipt cannot promote this candidate.
                with closing(storage.connect_initialized(fixture.target)) as core, core:
                    fixture.add(core, 2)
                parent = {"hook_event_name": "SessionStart", "session_id": observed.parent,
                    "cwd": str(fixture.repo), "transcript_path": ""}
                self.assertEqual(collect(parent)["collected_sources"], 0)
                with closing(storage.connect_initialized(fixture.target)) as core, core:
                    fixture.add(core, 1)
                # New process and no child notification/hint after registration.
                repository = UsageLocatorRepository(repository.path, *repository.basis)
                lifecycle.repository_for.return_value = repository
                self.assertEqual(collect(parent)["collected_sources"], 1)
                self.assertEqual(repository.locator_page(locator_root_fingerprints(self.roots), pending=True), ())
                self.assertEqual(repository.summary()["models"][0]["total_tokens"], 120)


if __name__ == "__main__":
    unittest.main()
