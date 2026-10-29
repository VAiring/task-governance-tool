"""Transactional dirty work and setup-only migration, with real numerical stores."""

import unittest
from contextlib import closing
import json
from pathlib import Path
import tempfile
from unittest import mock

from tests import test_usage_collection as support
from tests.test_usage_work_order import _snapshot
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository, _INPUTS
from task_governance_tool.usage_values import UsageError
from task_governance_tool import usage_reconciliation_core as core_inputs
from tests import test_usage_read_performance as physical_fixture
from tests.test_m17_recovery_hardening import _setup_current
from tests.test_usage_attribution import turn
from task_governance_tool import storage, usage_evidence_service as evidence_service
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_collection import register_source
from task_governance_tool.usage_evidence import encode
from task_governance_tool.usage_sources import locator_from_source, source_from_locator
from task_governance_tool.usage_work_order_repository import UsageWorkOrderRepository


class ReconciliationTrackingTests(unittest.TestCase):
    records = support.UsageCollectionTests.records
    write = support.UsageCollectionTests.write

    def setUp(self):
        support.UsageCollectionTests.setUp(self)
        old = self.repository
        self.repository = UsageReconciliationRepository(old.path, *old.basis)
        self.assertEqual(self.repository.initialize(), "migrated")

    def pending(self):
        with self.repository.connection() as connection:
            revision = self.repository.reconciliation_revision(connection)
            return revision, self.repository.pending_changes(connection, through=revision)

    def clear(self):
        with self.repository.connection(write=True) as connection:
            revision = self.repository.reconciliation_revision(connection)
            changes = self.repository.pending_changes(connection, through=revision)
            self.repository.acknowledge_changes(connection, changes, through=revision)

    def test_source_diagnostics_and_pending_are_tracked_without_new_numbers(self):
        self.clear()
        initial, _ = self.pending()
        self.repository.record_gap(self.source.source_id, support.THREAD, "source_unreadable")
        changed, rows = self.pending()
        self.assertGreater(changed.generation, initial.generation)
        self.assertIn(("source", self.source.source_id, ""), [row[:3] for row in rows])
        self.clear()
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_diagnostics WHERE source_id=?", (self.source.source_id,))
            connection.execute("UPDATE usage_sources SET pending='none' WHERE source_id=?", (self.source.source_id,))
        self.assertTrue(self.pending()[1])

    def test_collect_keeps_numbers_cursor_and_changes_atomic_and_noop_does_not_mark(self):
        self.write(self.records(support.usage()))
        initial = self.pending()
        with mock.patch.object(self.repository, "_before_cursor", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.repository.commit_batch(read_batch(self.source))
        self.assertEqual(self.pending(), initial)
        self.repository.commit_batch(read_batch(self.source))
        _, rows = self.pending()
        self.assertIn(("turn", support.THREAD, support.TURN), [row[:3] for row in rows])
        self.assertIn(("response", "openai", "resp_1"), [row[:3] for row in rows])
        self.clear()
        revision, _ = self.pending()
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_sources SET pending=pending")
            connection.execute("UPDATE usage_responses SET total_tokens=total_tokens")
        self.assertEqual(self.pending(), (revision, ()))

    def test_moved_identity_marks_old_and_new_and_stale_ack_preserves_new_work(self):
        self.write(self.records(support.usage()))
        self.repository.commit_batch(read_batch(self.source))
        self.clear()
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_responses SET turn_id=?", (support.TURN2,))
        revision, rows = self.pending()
        self.assertTrue({("turn", support.THREAD, support.TURN), ("turn", support.THREAD, support.TURN2)}
                        <= {row[:3] for row in rows})
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_responses SET turn_id=?", (support.TURN,))
        with self.assertRaises(UsageError):
            with self.repository.connection(write=True) as connection:
                self.repository.acknowledge_changes(connection, rows, through=revision)
        self.assertTrue(self.pending()[1])

    def test_dirty_page_is_carried_over_and_reopening_retains_committed_progress(self):
        with self.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_turns VALUES (?,?,?)", [
                (support.THREAD, f"{n:08x}-89ab-7cde-8fab-0123456789ab", n) for n in range(300)])
        revision, first = self.pending()
        self.assertEqual(len(first), 128)
        with self.repository.connection(write=True) as connection:
            self.repository.acknowledge_changes(connection, first, through=revision)
        self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
        _, second = self.pending()
        self.assertEqual(len(second), 128)
        self.assertFalse(set(first) & set(second))

    def test_missing_tracking_and_overflow_fail_without_committing_source_change(self):
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_reconcile_state SET generation=?", (2**63 - 1,))
        with self.assertRaises(UsageError):
            self.repository.record_gap(self.source.source_id, support.THREAD, "source_unreadable")
        with self.repository.connection() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_diagnostics").fetchone())
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_reconcile_state")
        for action in (self.repository.inspect, self.repository.initialize, self.pending):
            with self.assertRaises(UsageError):
                action()

    def test_every_registered_update_trigger_uses_real_columns_and_compiles(self):
        # SQLite allows creating a trigger containing a misspelled OLD/NEW
        # column. Preparing an UPDATE catches that even on an empty table.
        with self.repository.connection(write=True) as connection:
            for table, (columns, _) in _INPUTS.items():
                with self.subTest(table=table):
                    actual = {row[1] for row in connection.execute("PRAGMA table_info(" + table + ")")}
                    self.assertTrue(set(columns.split(",")) <= actual)
                    field = columns.split(",")[0]
                    connection.execute(f"UPDATE {table} SET {field}={field} WHERE 0")

    def test_all_predecessors_migrate_atomically_and_preserve_existing_tables(self):
        self.write(self.records(support.usage()))
        for previous in UsageReconciliationRepository._previous_repositories:
            with self.subTest(schema=previous.migrations[-1][0]):
                old = previous(self.root / f"old-{previous.migrations[-1][0]}.sqlite", *self.repository.basis)
                old.initialize()
                old.register_source(self.source.source_id, CallerIdentity(support.THREAD))
                old.commit_batch(read_batch(self.source))
                with old.connection() as connection:
                    before = _snapshot(connection)
                upgraded = UsageReconciliationRepository(old.path, *old.basis)
                self.assertEqual(upgraded.inspect(), "migration_required")
                with mock.patch.object(upgraded, "_before_reconciliation_marker", side_effect=UsageError()):
                    with self.assertRaises(UsageError):
                        upgraded.initialize()
                with old.connection() as connection:
                    self.assertEqual(_snapshot(connection), before)
                self.assertEqual(upgraded.initialize(), "migrated")
                self.assertEqual(upgraded.initialize(), "current")
                with upgraded.connection() as connection:
                    after = _snapshot(connection)
                    self.assertEqual({name: after[name] for name in before}, before)
                    self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                    self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
                with self.assertRaises(UsageError):
                    old.inspect()


class PopulatedSchema9MigrationTests(unittest.TestCase):
    """Preservation is separate from admission and actual continuation reuse."""

    cli = physical_fixture.UsageReadPerformanceTests.cli
    add = physical_fixture.UsageReadPerformanceTests.add
    complete = physical_fixture.UsageReadPerformanceTests.complete
    response_header = physical_fixture.UsageReadPerformanceTests.response_header
    response_rows = physical_fixture.UsageReadPerformanceTests.response_rows

    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.install, self.target, _ = _setup_current(directory)
        self.root = self.install.project_root.resolve()
        # Internal repository injection keeps the schema-9 fixture separate
        # from Setup's current empty numerical store and all real projects.
        self.repository = UsageWorkOrderRepository(directory / "schema9.sqlite",
                                                   *evidence_service.repository_for(self.target).basis)
        self.repository.initialize()
        self.source = SourceInput(support.THREAD, self.root / "migration.jsonl", self.root, self.root)
        register_source(self.repository, self.source, CallerIdentity(support.THREAD))
        started = self.add("Nonempty numerical migration")
        self.task_id = started["data"]["task"]["task_id"]
        self.source.path.write_bytes(b"".join(encode(row) + b"\n" for row in
            self.response_header(support.THREAD, 1) + self.response_rows("first", 1, started)))
        self.repository.collect_source(self.source, record_attempt=True)
        self.assertEqual(self.refresh()["publication"], "current")
        completed = self.complete(self.task_id)
        with self.source.path.open("ab") as stream:
            stream.write(b"".join(encode(row) + b"\n" for row in self.response_rows("completed", 1, completed)))
        self.repository.collect_source(self.source, record_attempt=True)
        self.assertEqual(self.refresh()["publication"], "current")
        # Both lanes have genuine unfinished scans, not invented cursor rows.
        with self.source.path.open("ab") as stream:
            stream.write(b"".join(encode(row) + b"\n" for row in [
                support.event("turn_context", turn_id=turn(1), model="fixture-model"),
                support.usage("later-1", turn=turn(1)), support.usage("later-2", turn=turn(1))]))
        self.repository.collect_source(self.source, max_records=1, record_attempt=True)
        self.repository.collect_source(self.source, lane="audit", max_records=4)
        self.roots = (self.root, directory / "archive")
        self.locator = locator_from_source(self.source, self.roots)
        self.assertIsNotNone(self.locator)
        self.fingerprints = {self.locator.root_kind: self.locator.root_fingerprint}
        self.repository.remember_locator(self.locator)
        self.repository.commit_discovery(0, {"root": 0, "stack": [], "cycle": 0}, [self.source.source_id], False)
        self.old_index, state = self.repository.prepare_publication(self.reader)
        self.assertGreater(len(self.old_index["snapshots"]), 1)
        first = self.old_index["snapshots"][0]
        # That immutable file was durably published by the earlier real service
        # call. Validate its exact bytes before checkpointing this audit step.
        self.assertEqual((self.target.resolved_usage_snapshots / (first + ".json")).read_bytes(),
                         encode(self.repository.publication_snapshot(first)))
        self.old_state = self.repository.advance_publication(state)
        self.assertTrue(0 < self.old_state["position"] < self.old_state["total"])
        self.old_cursor = self.repository.cursor(self.source.source_id, support.THREAD)
        self.old_locators = self.repository.locator_page(self.fingerprints)
        self.old_discovery = self.repository.discovery_position()
        self.old_files = {p.name: p.read_bytes() for p in self.target.resolved_usage_snapshots.glob("*.json")}
        self.last_good_index = self.target.resolved_usage_index.read_bytes()
        self.core_bytes = self.target.db_path.read_bytes()
        with self.repository.connection() as connection:
            self.before = _snapshot(connection)
            for table in ("usage_responses", "usage_observations", "usage_scan_state", "usage_scan_models",
                          "usage_response_context", "usage_locators", "usage_work_order", "usage_discovery",
                          "usage_publication", "usage_snapshots", "usage_snapshot_members", "usage_supersessions", "usage_cycle_links"):
                self.assertTrue(self.before[table], table)
            self.scans = {row["lane"]: json.loads(row["document"]) for row in connection.execute("SELECT * FROM usage_scan_state")}
            self.assertEqual(set(self.scans), {"ingest", "audit"})
            self.assertTrue(0 < self.scans["ingest"]["state"]["offset"] < self.source.path.stat().st_size)
            self.assertTrue(0 < self.scans["audit"]["state"]["offset"] < self.scans["audit"]["goal"])
            self.assertEqual(self.scans["audit"]["state"]["turn"], turn(1))

    def reader(self):
        return closing(storage.connect_initialized_readonly(self.target, operation_local=True))

    def refresh(self):
        with mock.patch.object(evidence_service, "repository_for", return_value=self.repository):
            return evidence_service.refresh_usage(self.target)

    def migrate(self):
        self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
        self.assertEqual(self.repository.initialize(), "migrated")

    def reopen(self):
        self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)

    def assert_preserved(self):
        with self.repository.connection() as connection:
            after = _snapshot(connection)
            self.assertEqual({table: after[table] for table in self.before}, self.before)
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(self.repository.cursor(self.source.source_id, support.THREAD), self.old_cursor)
        self.assertEqual(self.repository.locator_page(self.fingerprints), self.old_locators)
        self.assertEqual(self.repository.discovery_position(), self.old_discovery)
        self.assertEqual(self.target.db_path.read_bytes(), self.core_bytes)

    def test_nonempty_migration_rollback_reentry_and_exact_preservation(self):
        old = self.repository
        with old.connection() as connection:
            schema = tuple(map(tuple, connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")))
            markers = tuple(map(tuple, connection.execute("SELECT * FROM usage_migrations ORDER BY version")))
        upgraded = UsageReconciliationRepository(old.path, *old.basis)
        self.assertEqual(upgraded.inspect(), "migration_required")
        with mock.patch.object(upgraded, "_before_reconciliation_marker", side_effect=UsageError("usage_pending")):
            with self.assertRaises(UsageError):
                upgraded.initialize()
        with old.connection() as connection:
            self.assertEqual(_snapshot(connection), self.before)
            self.assertEqual(tuple(map(tuple, connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))), schema)
            self.assertEqual(tuple(map(tuple, connection.execute("SELECT * FROM usage_migrations ORDER BY version"))), markers)
        self.migrate()
        self.assert_preserved()
        with self.repository.connection() as connection:
            epoch = self.repository.reconciliation_revision(connection)
            self.assertEqual(epoch.generation, 0)
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_baseline").fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_snapshot_bootstrap").fetchone())
        self.reopen()
        self.assertEqual(self.repository.initialize(), "current")
        self.assert_preserved()
        with self.repository.connection() as connection:
            self.assertEqual(self.repository.reconciliation_revision(connection), epoch)

    def test_reestablishment_and_saved_scans_resume_before_current_publication(self):
        from task_governance_tool import usage_reconciliation_runtime as runtime
        from task_governance_tool import usage_reconciliation_baseline as baseline
        from task_governance_tool import usage_reconciliation_snapshots as snapshots
        from task_governance_tool.usage_reconciliation_publication import captured_basis
        self.migrate()
        self.assert_preserved()
        def unavailable():
            with self.repository.connection() as connection, self.reader() as core:
                with self.assertRaisesRegex(UsageError, "usage_pending"):
                    captured_basis(self.repository, core, connection)
            self.assertEqual(self.target.resolved_usage_index.read_bytes(), self.last_good_index)
        unavailable()
        self.repository.deadline = 0
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            self.repository.prepare_publication(self.reader)
        self.reopen()
        with mock.patch.object(runtime, "establish_page", side_effect=lambda repo, connection:
                               baseline.establish_page(repo, connection, limit=1)):
            for _ in range(20):
                if runtime.advance_initial(self.repository, self.reader)["phase"] == "baseline":
                    break
            else:
                self.fail("baseline was not reached")
            with self.repository.connection() as connection:
                saved = tuple(connection.execute("SELECT * FROM usage_reconcile_baseline").fetchone())
                self.assertGreater(saved[2], 0)
                self.assertEqual(saved[3], 0)
                before_interrupt = _snapshot(connection)
            def interrupted(repo, connection):
                baseline.establish_page(repo, connection, limit=1)
                raise UsageError("usage_pending")
            with mock.patch.object(runtime, "establish_page", side_effect=interrupted):
                with self.assertRaisesRegex(UsageError, "usage_pending"):
                    runtime.advance_initial(self.repository, self.reader)
            self.reopen()
            with self.repository.connection() as connection:
                self.assertEqual(_snapshot(connection), before_interrupt)
            unavailable()
            runtime.advance_initial(self.repository, self.reader)
            with self.repository.connection() as connection:
                self.assertNotEqual(tuple(connection.execute("SELECT * FROM usage_reconcile_baseline").fetchone()), saved)
        for advance, finished in ((runtime.advance_initial, "effects_pending"), (runtime.advance_effects, "classification_pending")):
            for _ in range(300):
                if advance(self.repository, self.reader)["phase"] == finished:
                    break
            else:
                self.fail("reconciliation did not finish")
        original_bootstrap = snapshots.bootstrap_page
        with mock.patch.object(snapshots, "bootstrap_page", side_effect=lambda repo, connection:
                               original_bootstrap(repo, connection, limit=1)):
            self.assertEqual(snapshots.advance(self.repository, self.reader)["phase"], "snapshot_baseline")
            with self.repository.connection() as connection:
                saved = tuple(connection.execute("SELECT * FROM usage_reconcile_snapshot_bootstrap").fetchone())
                self.assertEqual(saved[2], 0)
            self.reopen()
            unavailable()
            self.assertEqual(snapshots.advance(self.repository, self.reader)["phase"], "snapshot_baseline")
            with self.repository.connection() as connection:
                self.assertGreater(connection.execute("SELECT position FROM usage_reconcile_snapshot_bootstrap").fetchone()[0], saved[1])
        index, state = self.repository.prepare_publication(self.reader)
        self.assertNotEqual(state["basis"], self.old_state["basis"])
        self.assertEqual(state["position"], 0)
        with self.assertRaisesRegex(UsageError, "cursor_stale"):
            self.repository.advance_publication(self.old_state)
        self.assertEqual(self.refresh()["publication"], "current")
        self.assertTrue(set(self.old_index["snapshots"]) <= set(index["snapshots"]))
        # Resolve the preserved locator, then use the actual saved ingest and
        # audit states. No header replay, incarnation change or duplicate data.
        self.reopen()
        source = source_from_locator(self.locator, self.roots, self.root)
        self.assertEqual(self.repository.locator_page(self.fingerprints), self.old_locators)
        while self.repository.cursor(source.source_id, source.thread_id).offset < source.path.stat().st_size:
            self.repository.collect_source(source, max_records=1, record_attempt=True)
        for _ in range(50):
            self.repository.collect_source(source, lane="audit", max_records=1)
            with self.repository.connection() as connection:
                audit = json.loads(connection.execute("SELECT document FROM usage_scan_state WHERE lane='audit'").fetchone()[0])
            self.assertEqual(audit["goal"], self.scans["audit"]["goal"])
            if audit["state"]["offset"] == audit["goal"]:
                break
        else:
            self.fail("saved audit did not finish")
        self.assertEqual(self.repository.cursor(source.source_id, source.thread_id).incarnation, self.old_cursor.incarnation)
        self.assertEqual(self.refresh()["publication"], "current")
        with self.repository.connection() as connection:
            after = _snapshot(connection)
            self.assertEqual({row["response_id"] for row in connection.execute("SELECT response_id FROM usage_responses")},
                             {"first", "completed", "later-1", "later-2"})
            for table in ("usage_responses", "usage_observations", "usage_snapshots", "usage_snapshot_members", "usage_supersessions", "usage_cycle_links"):
                self.assertTrue(set(self.before[table]) <= set(after[table]), table)
            self.assertGreater(after["usage_work_order"][0][1], self.before["usage_work_order"][0][1])
            self.assertEqual(connection.execute("SELECT complete FROM usage_reconcile_baseline").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT complete FROM usage_reconcile_snapshot_bootstrap").fetchone()[0], 1)
        for name, content in self.old_files.items():
            self.assertEqual((self.target.resolved_usage_snapshots / name).read_bytes(), content)
        final = json.loads(self.target.resolved_usage_index.read_bytes())
        self.assertTrue(set(self.old_index["snapshots"]) <= set(final["snapshots"]))
        self.assertTrue({tuple(sorted(row.items())) for row in self.old_index["cycle_links"]} <=
                        {tuple(sorted(row.items())) for row in final["cycle_links"]})
        self.assertEqual(self.target.db_path.read_bytes(), self.core_bytes)


class CoreReconciliationPageTests(unittest.TestCase):
    def setUp(self):
        from tests.test_viewer_maintenance import make_target, add_viewer_event
        from task_governance_tool import storage
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = make_target(self.root, enabled=False)
        for number in range(3):
            add_viewer_event(self.target, f"Core input {number}")
        self.project = self.target.project.project_id
        self.repository = UsageReconciliationRepository(self.root / "usage.sqlite", self.project, "a" * 64, 1)
        self.repository.initialize()
        with self.reader() as core:
            self.tasks = tuple(row[0] for row in core.execute("SELECT task_id FROM tasks ORDER BY rowid"))

    def reader(self):
        from task_governance_tool import storage
        return closing(storage.connect_initialized_readonly(self.target, operation_local=True))

    def dirty_core(self, task):
        from task_governance_tool import storage, viewer_projection_repository as projection
        with closing(storage.connect_initialized(self.target)) as connection:
            storage.begin_initialized_write(connection, self.target, operation_local=True)
            projection.mark_tasks_locked(connection, self.project, (task,))
            connection.commit()

    def cached(self):
        with self.repository.connection() as connection:
            return {row["task_id"]: json.loads(row["document"]) for row in
                    connection.execute("SELECT task_id,document FROM usage_reconcile_core_inputs")}

    def test_page_restart_and_coalesced_change_above_fence_are_not_lost(self):
        with mock.patch.object(core_inputs, "PAGE", 2):
            first = self.repository.reconcile_core_page(self.reader)
            self.assertEqual((first["selected"], first["complete"]), (2, False))
            # Move the remaining mark beyond the first captured fence.
            self.dirty_core(self.tasks[2])
            self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
            second = self.repository.reconcile_core_page(self.reader)
            self.assertEqual((second["selected"], second["complete"]), (0, False))
            final = self.repository.reconcile_core_page(self.reader)
            self.assertEqual((final["selected"], final["complete"]), (1, True))
        with self.reader() as core:
            self.assertEqual(self.cached(), core_inputs.read_task_inputs(core, self.project, set(self.tasks)))

    def test_unchanged_core_never_reads_task_histories_and_nonsemantic_mark_changes_no_inputs(self):
        self.repository.reconcile_core_page(self.reader)
        with mock.patch.object(core_inputs, "read_task_inputs", side_effect=AssertionError("whole history reread")):
            result = self.repository.reconcile_core_page(self.reader)
        self.assertEqual((result["selected"], result["changed"], result["complete"]), (0, (), True))
        before = self.cached()
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_reconcile_core_work")
        self.dirty_core(self.tasks[1])
        with mock.patch.object(core_inputs, "read_task_inputs", wraps=core_inputs.read_task_inputs) as read:
            result = self.repository.reconcile_core_page(self.reader)
        self.assertEqual((result["selected"], result["changed"]), (1, ()))
        self.assertEqual(read.call_args.args[2], {self.tasks[1]})
        self.assertEqual(self.cached(), before)
        with self.repository.connection() as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT task_id FROM usage_reconcile_core_work")], [self.tasks[1]])

    def test_participant_discovery_reuses_exact_admitted_core_pages(self):
        from task_governance_tool.usage_attribution_repository import registered_participants
        from task_governance_tool import usage_locator_collection as collection
        with self.reader() as core:
            expected = registered_participants(core, self.project)
        actual = self.repository.discover_participants(self.reader)
        self.assertEqual(actual, expected)
        with mock.patch.object(core_inputs, "read_task_inputs", side_effect=AssertionError("unchanged histories reread")), \
                mock.patch.object(collection, "validate_usage_ownership", side_effect=AssertionError("whole state validation")):
            self.assertEqual(collection._participants(self.target, None, repository=self.repository), expected)

    def test_incomplete_discovery_baseline_commits_progress_before_reporting_pending(self):
        with mock.patch.object(core_inputs, "PAGE", 2):
            with self.assertRaisesRegex(UsageError, "usage_pending"):
                self.repository.discover_participants(self.reader)
        self.assertEqual(len(self.cached()), 2)
        self.repository.discover_participants(self.reader)
        self.assertEqual(len(self.cached()), 3)

    def test_rollback_keeps_earlier_page_and_retry_does_not_repeat_it(self):
        with mock.patch.object(core_inputs, "PAGE", 2):
            self.repository.reconcile_core_page(self.reader)
            before = self.cached()
            original = core_inputs._save_cursor
            def fail(connection, value):
                original(connection, value)
                raise UsageError("usage_pending")
            with mock.patch.object(core_inputs, "_save_cursor", side_effect=fail):
                with self.assertRaises(UsageError):
                    self.repository.reconcile_core_page(self.reader)
            self.assertEqual(self.cached(), before)
            result = self.repository.reconcile_core_page(self.reader)
            self.assertEqual((result["selected"], result["complete"]), (1, True))

    def test_restore_epoch_and_algorithm_version_reestablish_baseline(self):
        from task_governance_tool import viewer_projection_repository as projection
        self.repository.reconcile_core_page(self.reader)
        projection.reset_restored_database(self.target)
        result = self.repository.reconcile_core_page(self.reader)
        self.assertTrue(result["reset"])
        self.assertEqual(result["selected"], 3)
        with mock.patch.object(core_inputs, "CORE_INPUT_VERSION", core_inputs.CORE_INPUT_VERSION + 1):
            result = self.repository.reconcile_core_page(self.reader)
            self.assertTrue(result["reset"])
            self.assertEqual(result["selected"], 3)

    def test_tampered_progress_is_rejected_and_missing_progress_rebuilds(self):
        self.repository.reconcile_core_page(self.reader)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_reconcile_core_cursor SET document='{}'")
        with self.assertRaises(UsageError):
            self.repository.reconcile_core_page(self.reader)
        with self.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_reconcile_core_cursor")
        result = self.repository.reconcile_core_page(self.reader)
        self.assertTrue(result["reset"])
        self.assertEqual(result["selected"], 3)


class TurnReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repository = UsageReconciliationRepository(self.root / "usage.sqlite", "project", "a" * 64, 1)
        self.repository.initialize()

    def stage(self, number, *, owner=None, close=True, clock=None, gaps=()):
        from task_governance_tool.usage_attribution import Transition
        from task_governance_tool.usage_reconciliation_turns import stage_turn
        from tests.test_usage_attribution import turn
        owner = number if owner is None else owner
        execution, task = f"tg_execution_{owner:016x}", f"tg_task_{owner:016x}"
        first, last = ("openai", f"r{number}-a"), ("openai", f"r{number}-b")
        start = Transition(f"tg_owner_transition_{number * 2:016x}", task, execution, 1, "ready", "in_progress", support.THREAD, 1)
        finish = Transition(f"tg_owner_transition_{number * 2 + 1:016x}", task, execution, 1, "in_progress", "done", support.THREAD, 1)
        events = {first: [(number * 2, start)], last: [(number * 2 + 1, finish)] if close else []}
        with self.repository.connection(write=True) as connection:
            return stage_turn(connection, thread_id=support.THREAD, turn_id=turn(number),
                              started_at=number if clock is None else clock, keys=(first, last),
                              events=events, checks=(), thread_gaps=gaps)

    def replay(self):
        from task_governance_tool.usage_reconciliation_turns import replay_one
        results = []
        for _ in range(1000):
            with self.repository.connection(write=True) as connection:
                item = replay_one(connection, execution_task=lambda execution: execution.replace("tg_execution_", "tg_task_"))
            if item is None:
                return results
            results.append(item)
        self.fail("replay did not converge")

    def outputs(self):
        with self.repository.connection() as connection:
            return {row["turn_id"]: json.loads(row["document"]) for row in connection.execute("SELECT * FROM usage_reconcile_turn_allocations")}

    def test_fixed_changed_turn_does_not_replay_same_session_unrelated_history(self):
        from tests.test_usage_attribution import turn
        for number in range(1, 101):
            self.stage(number)
        self.assertEqual(len(self.replay()), 100)
        before = self.outputs()
        self.stage(50, owner=1001)
        result = self.replay()
        self.assertEqual([item["turn_id"] for item in result], [turn(50)])
        after = self.outputs()
        self.assertEqual({key: value for key, value in before.items() if key != turn(50)},
                         {key: value for key, value in after.items() if key != turn(50)})
        self.assertEqual(self.replay(), [])
        self.assertFalse(self.stage(50, owner=1001))
        self.assertEqual(self.replay(), [])

    def test_open_exit_propagates_until_next_turn_exit_converges(self):
        from tests.test_usage_attribution import turn
        for number in range(1, 5):
            self.stage(number)
        self.replay()
        self.stage(2, close=False)
        self.assertEqual([item["turn_id"] for item in self.replay()], [turn(2), turn(3)])
        self.assertEqual(self.outputs()[turn(3)]["active_before"], "tg_execution_0000000000000002")
        self.assertIsNone(self.outputs()[turn(3)]["active_after"])

    def test_clock_move_updates_both_old_and_new_successors(self):
        from tests.test_usage_attribution import turn
        for number in range(1, 5):
            self.stage(number, close=number != 2)
        self.replay()
        self.stage(2, close=False, clock=10)
        changed = {item["turn_id"] for item in self.replay()}
        self.assertEqual(changed, {turn(2), turn(3)})
        values = self.outputs()
        self.assertIsNone(values[turn(3)]["active_before"])
        self.assertEqual(values[turn(2)]["active_after"], "tg_execution_0000000000000002")

    def test_committed_prefix_survives_failure_in_later_replay(self):
        from task_governance_tool.usage_reconciliation_turns import replay_one
        for number in range(1, 4):
            self.stage(number)
        with self.repository.connection(write=True) as connection:
            replay_one(connection, execution_task=lambda _: None)
        before = self.outputs()
        with self.assertRaises(UsageError):
            with self.repository.connection(write=True) as connection:
                replay_one(connection, execution_task=lambda _: None)
                raise UsageError("usage_pending")
        self.assertEqual(self.outputs(), before)
        self.repository = UsageReconciliationRepository(self.repository.path, *self.repository.basis)
        self.assertEqual(len(self.replay()), 2)

    def test_gap_removal_and_altered_state_are_not_accepted_as_unchanged(self):
        from tests.test_usage_attribution import turn
        self.stage(1, gaps=("source_unreadable",))
        self.replay()
        self.assertTrue(self.outputs()[turn(1)]["gaps"])
        self.stage(1)
        self.assertEqual(len(self.replay()), 1)
        self.assertTrue(all(not codes for _, codes in self.outputs()[turn(1)]["gaps"]))
        self.stage(2)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_reconcile_turn_allocations SET active_after='unexpected'")
        with self.assertRaises(UsageError):
            self.replay()


if __name__ == "__main__":
    unittest.main()
