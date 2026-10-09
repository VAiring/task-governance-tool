"""Schema-4 migration and immutable decision-anchor replay without core writes."""

from contextlib import closing
from dataclasses import replace
import sqlite3
from unittest import mock

from tests import test_usage_evidence_repository as evidence_support
from tests.test_usage_evidence_repository import execution
from tests.test_usage_collection import THREAD, usage, event
from tests.test_usage_attribution import turn
from tests.test_usage_wait_attribution import TASK, marker, fact, MANIFEST
from tests.test_usage_turn_adapter import tool_record
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_repository import UsageRepository
from task_governance_tool.usage_attribution_repository import UsageAttributionRepository
from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository
from task_governance_tool.usage_wait_repository import UsageWaitRepository, wait_anchor
from task_governance_tool.usage_turn_adapter import TurnObservation
from task_governance_tool.usage_values import UsageError


class UsageWaitRepositoryTests(evidence_support.UsageEvidenceRepositoryTests):
    def setUp(self):
        super().setUp()
        self.repository = UsageWaitRepository(self.repository.path, *self.repository.basis)
        self.assertEqual(self.repository.initialize(), "migrated")
        with closing(sqlite3.connect(self.core_path)) as core:
            core.executescript("""
                CREATE TABLE artifact_manifests(artifact_manifest_id,project_id,task_id,state,target_kind,
                    target_value,target_base_revision,target_generation,authority_snapshot_id,
                    acceptance_criterion_id,verification_criterion_id,digest);
                CREATE TABLE authority_snapshots(authority_snapshot_id,project_id,task_id,contract_revision);
                CREATE TABLE evidence_references(evidence_reference_id,project_id,task_id,source_kind,source_state,
                    source_id,contract_revision,authority_snapshot_id,acceptance_criterion_id,verification_criterion_id,
                    target_kind,target_value,target_base_revision,target_generation,digest);
            """)

    def anchor(self, *, number=1, manifest=MANIFEST, generation=1):
        item = fact(5, execution=execution(number), manifest=manifest, generation=generation)
        p = item.project_id
        with closing(sqlite3.connect(self.core_path)) as core:
            snapshot = "snapshot-" + manifest
            core.execute("INSERT INTO authority_snapshots VALUES (?,?,?,?)", (snapshot, p, TASK, 1))
            core.execute("INSERT INTO artifact_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (manifest, p, TASK, "opaque_target", item.target_kind, item.target_value, "", generation,
                 snapshot, None, None, "sha256:" + "b" * 64))
            core.execute("INSERT INTO evidence_references VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("reference-" + manifest, p, TASK, "artifact_manifest", "opaque_target", manifest, 1,
                 snapshot, None, None, item.target_kind, item.target_value, "", generation, "sha256:" + "c" * 64))
            core.commit()
        return item

    def prepare_wait(self):
        self.observed()
        self.change(TASK, 1, "in_progress", 2)
        self.change(TASK, 1, "review_pending", 3)
        return self.anchor()

    def test_schema_four_migration_all_sources_preserves_rows_and_rollback(self):
        for previous in (UsageRepository, UsageAttributionRepository, UsageEvidenceRepository):
            with self.subTest(previous=previous.__name__):
                old = previous(self.root / ("wait-" + previous.__name__ + ".sqlite"), *self.repository.basis)
                old.initialize()
                old.register_source(self.source.source_id, CallerIdentity(THREAD))
                self.write(self.records(usage()))
                old.commit_batch(read_batch(self.source))
                summary = old.summary()
                before = old.path.read_bytes()
                new = UsageWaitRepository(old.path, *old.basis)
                self.assertEqual(new.inspect(), "migration_required")
                with self.assertRaises(UsageError):
                    new.summary()
                self.assertEqual(old.path.read_bytes(), before)
                with mock.patch.object(new, "_before_wait_marker", side_effect=UsageError()):
                    with self.assertRaises(UsageError):
                        new.initialize()
                self.assertEqual(old.summary(), summary)
                self.assertEqual(new.initialize(), "migrated")
                self.assertEqual(new.initialize(), "current")
                self.assertEqual(new.summary(), summary)
                with self.assertRaises(UsageError):
                    old.inspect()

    def test_wait_fact_cursor_response_atomic_and_private_body_not_hashed(self):
        item = marker(execution=execution(1))
        record = tool_record(item, 5)
        record["payload"]["private"] = "PRIVATE_WAIT_BODY_CANARY"
        self.write(self.records(event("event_msg", type="task_started", turn_id=turn(5), started_at=5),
                                record, usage(turn=turn(5))))
        batch = read_batch(self.source, include_attribution=True, attribution_project_id=self.repository.basis[0])
        with mock.patch.object(self.repository, "_before_cursor", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.repository.commit_batch(batch)
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_wait_turns").fetchone()[0], 0)
        self.repository.commit_batch(batch)
        self.assertNotIn(b"PRIVATE_WAIT_BODY_CANARY", self.repository.path.read_bytes())
        cursor = self.repository.cursor(self.source.source_id, THREAD)
        self.source.path.write_bytes(self.source.path.read_bytes().replace(b"PRIVATE_WAIT_BODY_CANARY", b"X" * 24))
        replay = read_batch(self.source, cursor, include_attribution=True, attribution_project_id=self.repository.basis[0])
        self.assertEqual(replay.successor.prefix, cursor.prefix)

    def test_late_response_renotification_completion_and_reopen_are_separate(self):
        item = self.prepare_wait()
        self.repository.record((item, replace(item, turn_id=turn(7)), item))
        self.change(TASK, 1, "done", 8, cycle="cycle-first")
        self.refresh()
        first = self.read(TASK)["periods"][0]
        self.assertEqual(first["response_count"], 3)
        self.append(usage("late-decision", turn=turn(5)))
        self.collect()
        self.change(TASK, 0, "ready", 9)
        self.change(TASK, 2, "in_progress", 10)
        self.change(TASK, 2, "done", 11, cycle="cycle-reopened")
        self.refresh()
        prior, current = self.read(TASK, audit=True)["periods"]
        self.assertEqual(prior["response_count"], 4)
        self.assertEqual(current["response_count"], 2)
        self.assertEqual(prior["completion_cycle_id"], "cycle-first")
        self.assertEqual(current["completion_cycle_id"], "cycle-reopened")
        self.assertNotEqual(prior["snapshot_ids"], first["snapshot_ids"])

    def test_actual_host_receipt_after_done_binds_original_execution_once(self):
        item = self.prepare_wait()
        self.change(TASK, 1, "done", 4, cycle="cycle-before-notification")
        before = self.core_path.read_bytes()
        self.repository.record_host_receipt(item)
        self.repository.record_host_receipt(item)
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_wait_turns").fetchone()[0], 1)
        self.refresh()
        period = self.read(TASK)["periods"][0]
        self.assertEqual(period["completion_cycle_id"], "cycle-before-notification")
        self.assertEqual(period["response_count"], 3)
        self.assertEqual(self.core_path.read_bytes(), before)

    def test_restore_lost_manifest_or_reference_invalidates_cached_read(self):
        item = self.prepare_wait()
        baseline = self.core_path.read_bytes()
        self.repository.record((item,))
        self.refresh()
        self.assertEqual(self.read(TASK)["periods"][0]["response_count"], 3)
        for table in ("artifact_manifests", "authority_snapshots", "evidence_references"):
            self.core_path.write_bytes(baseline)
            self.refresh()
            with closing(sqlite3.connect(self.core_path)) as core:
                core.execute("DELETE FROM " + table)
                core.commit()
            with self.assertRaises(UsageError):
                self.read(TASK)
            self.refresh()
            self.assertEqual(self.read(TASK)["periods"][0]["response_count"], 2)

    def test_restored_equal_target_is_not_rebound_to_a_replacement_manifest(self):
        item = self.prepare_wait()
        self.repository.record((item,))
        self.refresh()
        with closing(sqlite3.connect(self.core_path)) as core:
            core.execute("DELETE FROM artifact_manifests")
            core.execute("DELETE FROM evidence_references")
            core.commit()
        self.anchor(manifest="tg_artifact_manifest_1123456789abcdef")
        with self.core_reader() as core:
            self.assertIsNone(wait_anchor(core, item))
        self.refresh()
        self.assertEqual(self.read(TASK)["periods"][0]["response_count"], 2)

    def test_delayed_earlier_decision_recomputes_first_turn(self):
        item = self.prepare_wait()
        self.repository.record((replace(item, turn_id=turn(7)),))
        self.refresh()
        self.repository.record((item,))
        self.refresh()
        snapshot = self.read(TASK)["periods"][0]["snapshot_ids"][0]
        with self.repository.connection() as connection:
            keys = self.repository.members(connection, snapshot)
        self.assertIn(("openai", "r5"), keys)
        self.assertNotIn(("openai", "r7"), keys)
