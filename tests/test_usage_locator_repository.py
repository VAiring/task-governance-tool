"""Retained locations, bounded queues and explicit schema-8 setup migration."""

from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.test_usage_collection import THREAD, PARENT, TURN, event, usage
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.state_resolver import observe_current_root
from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_locator_repository import (
    UsageLocatorRepository, LOCATOR_PAGE_LIMIT, PENDING_LOCATOR_LIMIT,
)
from task_governance_tool.usage_publication_repository import UsagePublicationRepository
from task_governance_tool.usage_response_repository import UsageResponseRepository
from task_governance_tool.usage_sources import SourceLocator, locator_from_source, source_hint
from task_governance_tool.usage_values import UsageError


class UsageLocatorRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.roots = (self.root / "sessions", self.root / "archive")
        for path in self.roots:
            path.mkdir()
        self.repository = UsageLocatorRepository(self.root / "usage.sqlite", "fixture-project",
                                                observe_current_root(self.root).canonical_path_hash, 1)
        self.repository.initialize()
        self.repository.register_session(CallerIdentity(THREAD))
        self.source = source_hint(THREAD, str(self.roots[0] / f"rollout-fixture-{THREAD}.jsonl"),
                                  self.roots, self.root)
        self.locator = locator_from_source(self.source, self.roots)
        self.fingerprints = {self.locator.root_kind: self.locator.root_fingerprint}

    def restart(self):
        self.repository = UsageLocatorRepository(self.repository.path, *self.repository.basis)

    def binding(self, **changes):
        return {"version": 1, "project_id": self.repository.basis[0],
                "project_path_hash": self.repository.basis[1], "project_binding_generation": 1,
                "task_id": "tg_task_" + "1" * 16, "contract_revision": 1,
                "execution_id": "tg_execution_" + "2" * 16, "ownership_generation": 1,
                "target_kind": "git_snapshot", "target_value": "sha256:" + "3" * 64,
                "target_base_revision": "4" * 40, "target_generation": 1,
                "parent_session_id": PARENT, "reviewer_session_id": THREAD,
                "preparation_id": "tg_review_preparation_" + "5" * 16, "continuation_id": None,
                "slot": 1, "assignment_digest": "sha256:" + "6" * 64,
                "source_basis_digest": "sha256:" + "7" * 64, **changes}

    def synthetic(self, number, *, fingerprint=None):
        return replace(self.locator, source_id=hashlib.sha256(str(number).encode()).hexdigest(),
                       relative_path=f"segment-{number}.jsonl",
                       root_fingerprint=fingerprint or self.locator.root_fingerprint)

    def write_source(self):
        records = [event("session_meta", id=THREAD, cwd=str(self.root), model_provider="openai"),
                   event("turn_context", turn_id=TURN, model="fixture-model", effort="high"), usage()]
        self.source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in records))

    def test_candidate_survives_restart_without_registering_or_reading_missing_file(self):
        entry = self.repository.remember_locator(self.locator)
        self.assertFalse(self.source.path.exists())
        self.assertEqual(self.repository.known_sources([self.source.source_id]), {})
        self.restart()
        self.assertEqual(self.repository.locator_page(self.fingerprints), (entry,))
        self.assertNotIn(self.locator.relative_path, repr(entry))
        self.assertEqual(self.repository.remember_locator(self.locator), entry)
        self.assertEqual(self.repository.locator_page({"sessions": "f" * 64}), ())

    def test_unregistered_candidate_requires_exact_pending_binding_and_promotion(self):
        locator = replace(self.locator, thread_id=PARENT)
        with self.assertRaisesRegex(UsageError, "source_not_registered"):
            self.repository.remember_locator(locator)
        entry = self.repository.remember_locator(locator, pending_review=self.binding(
            reviewer_session_id=PARENT, parent_session_id=THREAD))
        self.assertEqual(self.repository.locator_page(self.fingerprints), ())
        self.assertEqual(self.repository.known_sources([locator.source_id]), {})
        self.assertNotIn(PARENT, self.repository.session_ids())
        with self.assertRaisesRegex(UsageError, "source_not_registered"):
            self.repository.promote_locator(entry)
        self.repository.register_session(CallerIdentity(PARENT))
        promoted = self.repository.promote_locator(entry)
        self.assertIsNone(promoted.pending_review)
        self.assertIsNone(self.repository.promote_locator(entry))
        self.assertEqual(self.repository.locator_page(self.fingerprints, pending=True), ())
        self.assertEqual(self.repository.known_sources([locator.source_id]), {})

    def test_pending_requires_matching_project_and_reviewer_and_closed_metadata(self):
        for changes in ({"reviewer_session_id": PARENT}, {"project_id": "other-project"},
                        {"project_path_hash": "f" * 64}, {"project_binding_generation": 2},
                        {"raw_body": "not allowed"}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(UsageError):
                self.repository.remember_locator(self.locator, pending_review=self.binding(**changes))
        self.assertEqual(self.repository.locator_page(self.fingerprints, pending=True), ())

    def test_attempt_and_reauthorization_cas_do_not_reuse_stale_entries(self):
        original = self.repository.remember_locator(self.locator, pending_review=self.binding())
        attempt = self.repository.begin_locator_attempt(original)
        self.assertGreater(attempt.queue_order, original.queue_order)
        self.assertIsNone(self.repository.begin_locator_attempt(original))
        self.assertFalse(self.repository.discard_pending_locator(original))
        newer = self.repository.remember_locator(self.locator, pending_review=self.binding(target_generation=2))
        self.assertIsNone(self.repository.promote_locator(attempt))
        self.assertFalse(self.repository.discard_pending_locator(attempt))
        self.assertTrue(self.repository.discard_pending_locator(newer))
        self.assertIsNone(self.repository.locator_by_id(self.locator.source_id, pending=True))

    def test_pending_bound_keeps_existing_and_never_caps_registered_candidates(self):
        first = self.repository.remember_locator(self.synthetic(0), pending_review=self.binding())
        with self.repository.connection(write=True) as connection:
            document = json.dumps(self.binding())
            connection.executemany("INSERT INTO usage_pending_locators VALUES (?,?,?,?,?,?,?,?)", [
                (item.source_id, THREAD, item.root_kind, item.root_fingerprint, item.relative_path, 1, n + 1, document)
                for n in range(1, PENDING_LOCATOR_LIMIT) for item in [self.synthetic(n)]])
            connection.execute("UPDATE usage_locator_sequence SET value=?", (PENDING_LOCATOR_LIMIT,))
        with self.assertRaisesRegex(UsageError, "usage_locator_capacity"):
            self.repository.remember_locator(self.synthetic(10000), pending_review=self.binding())
        self.assertEqual(self.repository.locator_by_id(first.locator.source_id, pending=True), first)
        known = self.repository.remember_locator(self.synthetic(10001))
        self.assertIsNone(known.pending_review)
        self.assertTrue(self.repository.discard_pending_locator(first))
        self.assertIsNotNone(self.repository.remember_locator(self.synthetic(10000), pending_review=self.binding()))

    def test_paged_queue_progress_survives_restart_and_new_arrivals(self):
        original = {self.repository.remember_locator(self.synthetic(n)).locator.source_id for n in range(35)}
        observed = set()
        for cycle in range(5):
            self.restart()
            self.repository.remember_locator(self.synthetic(100 + cycle))
            page = self.repository.locator_page(self.fingerprints, limit=8)
            self.assertEqual(len(page), 8)
            for entry in page:
                observed.add(entry.locator.source_id)
                self.assertIsNotNone(self.repository.begin_locator_attempt(entry))
        self.assertTrue(original <= observed)

    def test_selection_work_stays_bounded_with_unrelated_roots_and_many_candidates(self):
        self.repository.remember_locator(self.synthetic(0))
        with self.repository.connection(write=True) as connection:
            rows = []
            for n in range(1, 6001):
                item = self.synthetic(n, fingerprint="e" * 64 if n % 2 else None)
                rows.append((item.source_id, THREAD, item.root_kind, item.root_fingerprint, item.relative_path, 1, n + 1))
            connection.executemany("INSERT INTO usage_locators VALUES (?,?,?,?,?,?,?)", rows)
            connection.execute("UPDATE usage_locator_sequence SET value=6001")
        steps = [0]
        configure = self.repository._configure_work
        def counted(connection):
            configure(connection)
            def count():
                steps[0] += 1
                return 0
            connection.set_progress_handler(count, 100)
        with mock.patch.object(self.repository, "_configure_work", side_effect=counted):
            page = self.repository.locator_page(self.fingerprints, limit=8)
        self.assertEqual(len(page), 8)
        self.assertTrue(all(entry.locator.root_fingerprint == self.locator.root_fingerprint for entry in page))
        self.assertLess(steps[0] * 100, 5000)
        with self.assertRaises(UsageError):
            self.repository.locator_page(self.fingerprints, limit=LOCATOR_PAGE_LIMIT + 1)

    def test_failed_save_and_attempt_roll_back_only_their_own_transaction(self):
        with mock.patch.object(self.repository, "_before_locator_commit", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.repository.remember_locator(self.locator)
        self.assertIsNone(self.repository.locator_by_id(self.locator.source_id))
        entry = self.repository.remember_locator(self.locator)
        with mock.patch.object(self.repository, "_before_locator_commit", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.repository.begin_locator_attempt(entry)
        self.assertEqual(self.repository.locator_by_id(self.locator.source_id), entry)

    def test_source_failure_after_capture_keeps_candidate_and_cursor_replayable(self):
        entry = self.repository.remember_locator(self.locator)
        self.repository.begin_locator_attempt(entry)
        self.write_source()
        with mock.patch.object(self.repository, "_before_scan_commit", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.repository.collect_source(self.source)
        self.restart()
        self.assertIsNotNone(self.repository.locator_by_id(self.locator.source_id))
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).offset, 0)
        self.repository.collect_source(self.source)
        self.assertEqual(self.repository.summary()["models"][0]["total_tokens"], 120)

    def test_busy_or_expired_budget_preserves_candidate_and_attempt_order(self):
        entry = self.repository.remember_locator(self.locator)
        with closing(sqlite3.connect(self.repository.path)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with self.assertRaises(UsageError):
                self.repository.begin_locator_attempt(entry)
        self.repository.deadline = 0
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            self.repository.begin_locator_attempt(entry)
        self.restart()
        self.assertEqual(self.repository.locator_by_id(self.locator.source_id), entry)

    def test_selected_registry_queries_do_not_expand_to_all_sources(self):
        self.repository.register_source(self.source.source_id, CallerIdentity(THREAD))
        self.assertEqual(self.repository.known_sources([self.source.source_id]), {self.source.source_id: THREAD})
        self.assertEqual(self.repository.known_sources(["e" * 64]), {})
        self.assertEqual(self.repository.registered_sessions([THREAD, PARENT]), frozenset({THREAD}))
        self.assertEqual(self.repository.session_ids(), frozenset({THREAD}))

    def test_directory_omission_does_not_mark_direct_locator_missing_or_clear_a_gap(self):
        self.repository.remember_locator(self.locator)
        self.repository.register_source(self.source.source_id, CallerIdentity(THREAD))
        legacy = "e" * 64
        self.repository.register_source(legacy, CallerIdentity(PARENT))
        revision, position = self.repository.discovery_position()
        self.repository.commit_discovery(revision, position, [], True,
                                         locator_root_fingerprints=self.fingerprints)
        with self.repository.connection() as connection:
            diagnostics = set(map(tuple, connection.execute("SELECT * FROM usage_diagnostics")))
        self.assertNotIn((self.source.source_id, "source_unreadable"), diagnostics)
        self.assertIn((legacy, "source_unreadable"), diagnostics)
        self.repository.record_gap(self.source.source_id, THREAD, "source_unreadable")
        revision, position = self.repository.discovery_position()
        self.repository.commit_discovery(revision, position, [self.source.source_id], True,
                                         locator_root_fingerprints=self.fingerprints)
        self.assertIn("source_unreadable", self.repository.summary()["diagnostics"])
        self.assertFalse(self.repository.missing_sessions({THREAD, PARENT}, 99))

    def test_environment_root_change_excludes_old_locator_from_direct_loss_exemption(self):
        original = self.repository.remember_locator(self.locator)
        self.repository.register_source(self.source.source_id, CallerIdentity(THREAD))
        changed_roots = {"sessions": "f" * 64}
        self.assertEqual(self.repository.locator_page(changed_roots), ())
        revision, position = self.repository.discovery_position()
        self.repository.commit_discovery(revision, position, [], True,
                                         locator_root_fingerprints=changed_roots)
        self.assertEqual(self.repository.locator_by_id(self.source.source_id), original)
        self.assertIn("source_unreadable", self.repository.summary()["diagnostics"])

    def test_schema6_and_7_migration_preserves_populated_continuations_and_every_old_table(self):
        self.write_source()
        for previous in (UsageResponseRepository, UsagePublicationRepository):
            with self.subTest(schema=previous.migrations[-1][0]):
                old = previous(self.root / f"populated-{previous.migrations[-1][0]}.sqlite", *self.repository.basis)
                old.initialize()
                old.collect_source(self.source)
                with old.connection() as connection:
                    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='usage_migrations'")]
                    before = {name: list(map(tuple, connection.execute("SELECT * FROM " + name))) for name in tables}
                new = UsageLocatorRepository(old.path, *old.basis)
                self.assertEqual(new.inspect(), "migration_required")
                with self.assertRaises(UsageError):
                    new.remember_locator(self.locator)
                self.assertEqual(new.initialize(), "migrated")
                self.assertEqual(new.initialize(), "current")
                with new.connection() as connection:
                    after = {name: list(map(tuple, connection.execute("SELECT * FROM " + name))) for name in tables}
                self.assertEqual(after, before)
                self.assertEqual(new.locator_page(self.fingerprints), ())

    def test_every_prior_exact_schema_upgrades_atomically_and_preserves_observations(self):
        self.write_source()
        for previous in UsageLocatorRepository._previous_repositories:
            with self.subTest(schema=previous.migrations[-1][0]):
                old = previous(self.root / f"schema-{previous.migrations[-1][0]}.sqlite", *self.repository.basis)
                old.initialize()
                old.register_source(self.source.source_id, CallerIdentity(THREAD))
                batch = read_batch(self.source, old.cursor(self.source.source_id, THREAD),
                                   include_attribution=old.collect_attribution, attribution_project_id=old.basis[0])
                old.commit_batch(batch)
                before = old.summary()
                new = UsageLocatorRepository(old.path, *old.basis)
                with mock.patch.object(new, "_before_locator_marker", side_effect=UsageError()):
                    with self.assertRaises(UsageError):
                        new.initialize()
                self.assertEqual(old.inspect(), "current")
                self.assertEqual(old.summary(), before)
                self.assertEqual(new.initialize(), "migrated")
                self.assertEqual(new.summary(), before)

    def test_corrupt_sequence_or_stored_path_is_not_silently_repaired(self):
        entry = self.repository.remember_locator(self.locator)
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_locator_sequence")
        with self.assertRaisesRegex(UsageError, "usage_schema_invalid"):
            self.repository.begin_locator_attempt(entry)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_locators SET relative_path='../outside.jsonl'")
        with self.assertRaisesRegex(UsageError, "usage_schema_invalid"):
            self.repository.locator_page(self.fingerprints)


if __name__ == "__main__":
    unittest.main()
