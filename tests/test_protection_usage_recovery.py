"""Recovery admits originals; bounded later events establish derived usage."""

from contextlib import closing, contextmanager
from dataclasses import replace
import json
import os
import sqlite3
import time
import unittest
from unittest import mock

from tests import test_usage_reconciliation_changes_runtime as support
from tests.test_usage_reconciliation_oracle import previous_classifier
from tests.test_usage_collection import event, usage
from tests.test_usage_attribution import turn
from task_governance_tool import backup, protection_repository as protection, storage
from task_governance_tool.usage_evidence import snapshot
from task_governance_tool.usage_evidence_service import repository_for, read_task_usage, refresh_usage
from task_governance_tool.usage_response_repository import component_identity
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_reconciliation_runtime import advance_initial, advance_effects
from task_governance_tool.usage_reconciliation_snapshots import advance
from task_governance_tool.usage_values import UsageError


class RecoveryUsageTests(unittest.TestCase):
    def test_sql_valid_original_and_restart_corruption_prevents_protection(self):
        from task_governance_tool.protection import protect
        corruptions = {
            "unsupported_diagnostic": "INSERT INTO usage_diagnostics SELECT source_id,'unsupported' FROM usage_sources",
            "nontext_diagnostic": "INSERT INTO usage_diagnostics SELECT source_id,x'00' FROM usage_sources",
            "discovery_document": "UPDATE usage_discovery SET document='{}'",
            "discovery_revision": "UPDATE usage_discovery SET revision=revision+0.5",
            "discovery_floor": "UPDATE usage_discovery SET document=json_set(document,'$.attempt_floor',0.5)",
            "discovery_stack": "UPDATE usage_discovery SET document=json_set(document,'$.stack',json('[\"invalid\"]'))",
            "work_order": "UPDATE usage_work_order SET last_attempt=last_attempt+0.5",
            "discovered_cycle": "UPDATE usage_discovered SET cycle=cycle+0.5",
            "scan_model": "UPDATE usage_scan_models SET model='invalid label'",
            "scan_effort": "UPDATE usage_scan_models SET effort=x'00'",
            "scan_coverage_turn": "UPDATE usage_scan_coverage SET turn_id='invalid'",
            "locator_path": "UPDATE usage_locators SET relative_path='../escape.jsonl'",
            "locator_revision": "UPDATE usage_locators SET revision=revision+0.5",
            "locator_sequence": "UPDATE usage_locator_sequence SET value=value+0.5",
            "missing_locator_sequence": "DELETE FROM usage_locator_sequence",
            "pending_document": "UPDATE usage_pending_locators SET pending_review='{}'",
            "pending_binding": "UPDATE usage_pending_locators SET pending_review=json_set(pending_review,'$.project_id','another-project')",
            "publication_cursor": "UPDATE usage_publication SET checksum='invalid'",
            "blob_change_first_key": "INSERT INTO usage_reconcile_changes SELECT 'turn',x'01',turn_id,generation FROM usage_turns,usage_reconcile_state LIMIT 1",
            "blob_change_second_key": "INSERT INTO usage_reconcile_changes SELECT 'turn',thread_id,x'01',generation FROM usage_turns,usage_reconcile_state LIMIT 1",
            "unknown_turn_modern": "INSERT INTO usage_scan_coverage SELECT source_id,'',1,0 FROM usage_sources",
            "fractional_tokens": "UPDATE usage_responses SET input_tokens=input_tokens+0.5,total_tokens=total_tokens+0.5",
            "fractional_optional_tokens": "UPDATE usage_responses SET cached_input_tokens=0.5",
            "fractional_turn": "UPDATE usage_turns SET started_at=started_at+0.5",
            "fractional_operation": "UPDATE usage_operation_turns SET generation=generation+0.5",
            "fractional_source_cursor": "UPDATE usage_sources SET offset=offset+0.5",
            "fractional_old_cursor": "UPDATE usage_incarnations SET offset=offset+0.5",
            "identity_digest": "UPDATE usage_reconcile_identity SET digest='invalid'",
            "identity_generation": "UPDATE usage_reconcile_identity SET observed_generation=observed_generation+0.5",
            "missing_evidence_revision": "DELETE FROM usage_reconcile_evidence_revision",
            "future_change": "INSERT INTO usage_reconcile_changes SELECT 'clock',thread_id,'0',generation+1 FROM usage_turns,usage_reconcile_state LIMIT 1",
            "invalid_clock": "INSERT INTO usage_reconcile_changes SELECT 'clock',thread_id,'00',generation FROM usage_turns,usage_reconcile_state LIMIT 1",
            "scan_document": "UPDATE usage_scan_state SET document='{}'",
            "response_context": "UPDATE usage_response_context SET document='{}'",
            "missing_scan_pair": "DELETE FROM usage_scan_state",
            "missing_context_pair": "DELETE FROM usage_response_context",
            "snapshot_document": "INSERT INTO usage_snapshots VALUES ('malformed','{}')",
        }
        for name, sql in corruptions.items():
            with self.subTest(name=name):
                fixture, target = self.fixture()
                self.collection_controls(fixture)
                with fixture.repository.connection(write=True) as connection:
                    changed = connection.execute(sql).rowcount
                    self.assertGreater(changed, 0)
                    self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                    self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
                before = target.resolved_usage_database.read_bytes()
                root = fixture.fixture.root / "protected"
                with mock.patch.object(UsageReconciliationRepository, "refresh", side_effect=AssertionError("must not drain")):
                    with self.assertRaises(UsageError):
                        protect(target, root, package_basis="a" * 64)
                self.assertEqual(target.resolved_usage_database.read_bytes(), before)
                self.assertFalse(list(root.glob("tg_protection_*")))
                self.assertFalse(list(root.rglob("manifest.json")))

    def test_unknown_turn_legacy_coverage_from_normal_collection_remains_recoverable(self):
        from task_governance_tool.usage_collection import register_source
        from task_governance_tool.session_identity import CallerIdentity
        fixture, target = self.fixture()
        original = fixture.fixture.source
        source = replace(original, path=original.path.with_name("legacy.jsonl"))
        register_source(fixture.repository, source, CallerIdentity(source.thread_id))
        fixture.fixture.write([event("session_meta", id=source.thread_id, cwd=str(source.project_root), model_provider="openai"),
                               event("event_msg", type="token_count")], path=source.path)
        fixture.repository.collect_source(source)
        with fixture.repository.connection() as connection:
            self.assertEqual(tuple(connection.execute("SELECT turn_id,modern,legacy FROM usage_scan_coverage WHERE source_id=?",
                                                      (source.source_id,)).fetchone()), ("", 0, 1))
        restored = self.recover(fixture, target)
        self.assert_unknown(restored, fixture.fixture.task_id)
        self.assert_oracle(restored)
        self.assertIn("legacy_usage", repository_for(restored).summary()["diagnostics"])

    def change_keys(self, fixture):
        from tests.test_usage_collection import THREAD
        return [("thread", THREAD, ""), ("turn", THREAD, turn(99)),
                ("task", fixture.repository.basis[0], "legacy task identity"),
                ("source", "f" * 64, ""), ("response", "openai", "unobserved"),
                ("boundary", "f" * 64, "e" * 64), ("context", "f" * 64, ""),
                ("origin_event", "tg_event_" + "f" * 16, ""), ("origin_call", THREAD, "unobserved_call"),
                ("clock", THREAD, str(2**63 - 1)), ("receipt", "tg_review_receipt_" + "f" * 16, "")]

    def test_retained_change_key_types_and_shapes_are_checked_for_every_kind(self):
        fixture, target = self.fixture()
        restored = self.recover(fixture, target)
        repository = repository_for(restored)
        for kind, first, second in self.change_keys(fixture):
            for index in (1, 2):
                for invalid in (b"invalid", "x" * 201):
                    values = [kind, first, second]
                    values[index] = invalid
                    with self.subTest(kind=kind, key=index, invalid=invalid):
                        with repository.connection(write=True) as connection:
                            connection.execute("INSERT OR REPLACE INTO usage_reconcile_changes SELECT ?,?,?,generation FROM usage_reconcile_state",
                                               values)
                            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
                        try:
                            with self.assertRaises(UsageError):
                                protection.validate_restored_usage(restored)
                        finally:
                            with repository.connection(write=True) as connection:
                                connection.execute("DELETE FROM usage_reconcile_changes WHERE kind=? AND key1=? AND key2=?", values)

    def test_valid_changes_without_restored_anchors_can_resume(self):
        fixture, target = self.fixture()
        with fixture.repository.connection(write=True) as connection:
            connection.executemany("INSERT OR REPLACE INTO usage_reconcile_changes SELECT ?,?,?,generation FROM usage_reconcile_state",
                                   self.change_keys(fixture))
        restored = self.recover(fixture, target)
        self.assert_oracle(restored)

    def collection_controls(self, fixture):
        from tests.test_usage_locator_repository import UsageLocatorRepositoryTests
        from task_governance_tool.usage_sources import locator_from_source
        source, repository = fixture.fixture.source, fixture.repository
        repository.attempted(source.source_id)
        repository.commit_discovery(0, {"root": 0, "stack": [[[], 2], [["2026"], 1]], "cycle": 0},
                                    [source.source_id], False)
        roots = (source.allowed_root, source.allowed_root / "archive")
        locator = locator_from_source(source, roots)
        self.assertIsNotNone(locator)
        entry = repository.remember_locator(locator)
        # A valid pending assignment can outlive the restored Core snapshot.
        # Recovery checks its closed shape/binding, not live Task attribution.
        repository.remember_locator(replace(locator, source_id="f" * 64, relative_path="missing.jsonl"),
                                    pending_review=UsageLocatorRepositoryTests.binding(fixture))
        return entry

    def test_saved_collection_controls_resume_without_discovery_during_recovery(self):
        fixture, target = self.fixture()
        entry = self.collection_controls(fixture)
        before_position = fixture.repository.discovery_position()
        with mock.patch("task_governance_tool.usage_incremental_repository.scan", side_effect=AssertionError("scan")), \
                mock.patch("task_governance_tool.usage_locator_collection.locate_slice", side_effect=AssertionError("inventory")), \
                mock.patch.object(UsageReconciliationRepository, "refresh", side_effect=AssertionError("drain")):
            restored = self.recover(fixture, target)
        repository = repository_for(restored)
        self.assertEqual(repository.discovery_position(), before_position)
        self.assertIsNotNone(repository.locator_by_id("f" * 64, pending=True))
        rotated = repository.begin_locator_attempt(entry)
        self.assertGreater(rotated.queue_order, entry.queue_order)
        with fixture.fixture.source.path.open("ab") as stream:
            stream.write(json.dumps(usage("restored-later", turn=turn(1))).encode() + b"\n")
        repository.collect_source(fixture.fixture.source, record_attempt=True)
        self.assertEqual(repository.unattempted_sources([entry.locator.source_id], before_position[1]["attempt_floor"]), set())
        repository.commit_discovery(*before_position, [entry.locator.source_id], True)
        self.assertEqual(repository.discovery_position()[1]["cycle"], before_position[1]["cycle"] + 1)
        self.assert_oracle(restored)

    def test_migration_reset_without_response_context_remains_recoverable(self):
        fixture, target = self.fixture()
        with fixture.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_scan_state SET document=json_set(document,'$.reset',json('true'))")
            connection.execute("DELETE FROM usage_response_context")
        restored = self.recover(fixture, target)
        self.assert_oracle(restored)

    def test_current_lifecycle_keeps_entry_deadline_for_deferred_reconciliation(self):
        from tests.test_usage_locator_lifecycle import LocatorLifecycleTests
        from task_governance_tool import usage_lifecycle
        fixture = LocatorLifecycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.repo = UsageReconciliationRepository(fixture.repo.path, *fixture.repo.basis)
        fixture.repo.initialize()
        usage_lifecycle.repository_for.return_value = fixture.repo
        started = time.monotonic()
        fixture.collect()
        usage_lifecycle.refresh_usage.assert_called_once()
        deadline = usage_lifecycle.refresh_usage.call_args.kwargs["deadline"]
        self.assertEqual(deadline, fixture.repo.deadline)
        self.assertGreater(deadline, started)
        self.assertLessEqual(deadline, time.monotonic() + usage_lifecycle.EVENT_SECONDS)

    def fixture(self, history=0):
        fixture = support.ChangedRuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.transition("paused", "pause", pause_reason="Unrelated later history")
        if history:
            rows = []
            for number in range(2, history + 2):
                rows.extend((event("event_msg", type="task_started", turn_id=turn(number), started_at=number),
                    event("turn_context", turn_id=turn(number), model="fixture-model"),
                    usage(f"unrelated-{number}", turn=turn(number))))
            fixture.append(*rows)
        fixture.repository.prepare_publication(fixture.reader)
        target = replace(fixture.fixture.target, usage_database=fixture.repository.path,
                         binding_path_hash=fixture.repository.basis[1], binding_generation=fixture.repository.basis[2])
        return fixture, target

    def recover(self, fixture, target, *, after_core=lambda: None):
        stage = fixture.fixture.root / "private-recovery"
        stage.mkdir()
        inbox = stage / "recovery"
        (inbox / "backups").mkdir(parents=True)
        core = stage / "core.sqlite"
        metadata, *_ = protection.copy_core(target, core)
        core.rename(inbox / "backups" / backup._filename(metadata))
        after_core()
        numerical = stage / "usage.sqlite"
        protection.copy_usage(target, numerical)
        proof = protection.rehearse(target, inbox, numerical)
        restored = replace(target, db_path=inbox / "taskgov.sqlite", explicit_db=True,
                           canonical_fixed=False, backups_path=inbox / "backups",
                           usage_database=inbox / "taskgov-usage.sqlite")
        self.assertEqual(proof["usage_aggregation"], "pending")
        self.assertEqual(numerical.read_bytes(), restored.resolved_usage_database.read_bytes())
        return restored

    def reader(self, target):
        return lambda: closing(storage.connect_initialized_readonly(target, operation_local=True))

    def originals(self, repository):
        with repository.connection() as connection:
            return {table: set(map(tuple, connection.execute("SELECT * FROM " + table))) for table in
                    ("usage_responses", "usage_observations", "usage_turns", "usage_operation_turns", "usage_snapshots")}

    def assert_unknown(self, target, task):
        with self.reader(target)() as core:
            result = read_task_usage(target, core, task)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["periods"], [])
        self.assertNotIn("total", result)

    def assert_oracle(self, target):
        repository = repository_for(target)
        index, _ = repository.prepare_publication(self.reader(target))
        with repository.connection() as connection, self.reader(target)() as core, mock.patch(
                "task_governance_tool.usage_response_repository.project_responses", previous_classifier()):
            full = repository.attribution(core)
            expected = [snapshot(repository.basis[0], item) for item in full["components"]]
            actual = [repository._snapshot(connection, connection.execute(
                "SELECT * FROM usage_snapshots WHERE snapshot_id=?", (identity,)).fetchone())
                for identity in index["current_snapshot_ids"]]
            def comparable(items):
                return [{k: v for k, v in item.items() if k not in {"snapshot_id", "predecessors"}}
                        for item in sorted(items, key=component_identity)]
            self.assertEqual(comparable(actual), comparable(expected))
            self.assertEqual(index["unattributed"], {kind: repository._totals(connection, full[field])
                for kind, field in (("unknown", "unknown_response_keys"), ("unassigned", "unassigned_response_keys"))})

    def test_restore_preserves_originals_and_resumes_after_deadline_and_restart(self):
        fixture, target = self.fixture()
        before = self.originals(fixture.repository)
        restored = self.recover(fixture, target)
        self.assert_unknown(restored, fixture.fixture.task_id)
        self.assertEqual(before, self.originals(repository_for(restored)))
        self.assertEqual(refresh_usage(restored, deadline=time.monotonic() - 1)["publication"], "pending")
        self.assert_unknown(restored, fixture.fixture.task_id)
        # Every committed work unit can be resumed by a new repository instance.
        for function, final in ((advance_initial, "effects_pending"),
                                (advance_effects, "classification_pending"), (advance, "captured")):
            for _ in range(400):
                repository = repository_for(restored)
                result = function(repository, self.reader(restored))
                if result["phase"] == final:
                    break
                self.assert_unknown(restored, fixture.fixture.task_id)
            else:
                self.fail("bounded work failed to converge")
        self.assert_oracle(restored)
        after = self.originals(repository_for(restored))
        for table, rows in before.items():
            self.assertTrue(rows <= after[table], table)
        with self.reader(restored)() as core:
            self.assertEqual(read_task_usage(restored, core, fixture.fixture.task_id)["status"], "pending")
        self.assertEqual(refresh_usage(restored, deadline=time.monotonic() + 20)["publication"], "current")

    def test_independent_capture_keeps_later_task_observation_unbound(self):
        from task_governance_tool import tasks
        from task_governance_tool.usage_turn_adapter import OperationObservation
        from task_governance_tool.session_identity import CallerIdentity
        from tests.test_usage_collection import THREAD
        fixture, target = self.fixture()
        later = []
        def create_later():
            with closing(storage.connect_initialized(target)) as core:
                result = tasks.add_task(core, target.project, database_target=target,
                    title="After Core snapshot", status="in_progress", caller=CallerIdentity(THREAD))
                core.commit()
            later.append(result.task["task_id"])
            fixture.repository.record((OperationObservation(THREAD, turn(3), target.project.project_id,
                result.task["task_id"], result.event["task_event_id"], 1, "in_progress"),))
        restored = self.recover(fixture, target, after_core=create_later)
        self.assert_unknown(restored, later[0])
        self.assert_oracle(restored)
        with repository_for(restored).connection() as numerical:
            self.assertIsNotNone(numerical.execute("SELECT 1 FROM usage_operation_turns WHERE task_id=?", later).fetchone())
            self.assertIsNone(numerical.execute("SELECT 1 FROM usage_reconcile_periods WHERE task_id=?", later).fetchone())

    def test_reestablished_display_rejects_changed_identity_epoch_schema_and_binding(self):
        for changed in ("file", "core_epoch", "usage_epoch", "schema", "binding"):
            with self.subTest(changed=changed):
                fixture, target = self.fixture()
                restored = self.recover(fixture, target)
                self.assert_oracle(restored)
                repository = repository_for(restored)
                if changed == "file":
                    replacement = repository.path.with_suffix(".replacement")
                    replacement.write_bytes(repository.path.read_bytes())
                    os.replace(replacement, repository.path)
                elif changed == "core_epoch":
                    from task_governance_tool.viewer_projection_repository import reset_restored_database
                    reset_restored_database(restored)
                else:
                    with repository.connection(write=True) as connection:
                        connection.execute({"usage_epoch": "UPDATE usage_reconcile_state SET epoch=lower(hex(randomblob(16)))",
                            "schema": "DROP INDEX usage_reconcile_turn_order",
                            "binding": "UPDATE usage_meta SET binding_generation=binding_generation+1"}[changed])
                self.assert_unknown(restored, fixture.fixture.task_id)
                if changed in {"schema", "binding"}:
                    with self.assertRaises(UsageError):
                        protection.validate_restored_usage(restored)
                else:
                    protection.validate_restored_usage(restored)

    def test_restore_fence_cost_does_not_replay_growing_unrelated_history(self):
        measured = []
        for history in (4, 24):
            fixture, target = self.fixture(history)
            start = time.monotonic()
            restored = self.recover(fixture, target)
            necessary = time.monotonic() - start
            @contextmanager
            def measure():
                counts = dict(sql=0, vm_100=0, connections=0, recalculations=0)
                def sql(_):
                    counts["sql"] += 1
                def progress():
                    counts["vm_100"] += 1
                    return 0
                native_connect = sqlite3.connect
                native_configure = UsageReconciliationRepository._configure_work
                def connect(*args, **kwargs):
                    counts["connections"] += 1
                    value = native_connect(*args, **kwargs)
                    value.set_trace_callback(sql)
                    value.set_progress_handler(progress, 100)
                    return value
                def configure(repository, connection):
                    native_configure(repository, connection)
                    connection.set_progress_handler(progress, 100)
                from task_governance_tool import usage_reconciliation_membership as membership
                with mock.patch.object(sqlite3, "connect", new=connect), mock.patch.object(
                        UsageReconciliationRepository, "_configure_work", new=configure), mock.patch.object(
                        membership, "reconcile_response", wraps=membership.reconcile_response) as work:
                    started = time.monotonic()
                    yield counts
                    counts["seconds"] = round(time.monotonic() - started, 3)
                    counts["recalculations"] = work.call_count
            with measure() as current:
                protection.validate_restored_usage(restored)
            # Exact removed algorithm on that same unchanged private copy.
            with measure() as previous, closing(storage.connect_initialized_readonly(restored)) as core:
                @contextmanager
                def reader():
                    yield core
                repository_for(restored).refresh(reader)
            measured.append(dict(history=history, copy_restore_validation_seconds=round(necessary, 3),
                usage_bytes=restored.resolved_usage_database.stat().st_size, current=current, previous=previous))
            self.assertEqual(current["recalculations"], 0)
            self.assertEqual(current["connections"], 2)
            self.assertGreater(previous["recalculations"], history)
        self.assertEqual(measured[0]["current"]["sql"], measured[1]["current"]["sql"])
        self.assertGreater(measured[1]["previous"]["sql"], measured[0]["previous"]["sql"])
        print("protection_usage_cost=" + json.dumps(measured, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
