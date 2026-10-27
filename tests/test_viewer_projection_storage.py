"""Viewer tracking: actual mutations, preservation, rollback and indexed deltas."""
from contextlib import closing
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from tests.test_viewer_maintenance import make_target, add_viewer_event
from tests.test_review_session_migration import candidate_runtime
from tests.test_m23s_schema22_migration import _logical_snapshot
from tests.verification_receipt_test_support import initialize, add_task, seed_current_review_evidence, add_receipt, completion, run_taskgov
from task_governance_tool import storage, schema_viewer_projection as schema
from task_governance_tool import viewer_projection_repository as projection
from task_governance_tool.tasks import TaskRepositoryError


class ViewerProjectionStorageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = make_target(self.root, enabled=False)
        self.project = self.target.project.project_id

    def snapshot(self):
        with closing(storage.connect_initialized_readonly(self.target)) as c:
            return projection.read_state(c, self.project), tuple(tuple(r) for r in c.execute(
                "SELECT * FROM viewer_task_revisions ORDER BY task_id"))

    def test_new_current_reentry_and_no_implicit_viewer_activation(self):
        state, revisions = self.snapshot()
        self.assertEqual((state.source_generation, state.invalidated_generation, revisions), (0, 0, ()))
        self.assertIsNone(state.cache_generation)
        self.assertFalse(self.target.resolved_viewer_path.exists())
        before = self.target.db_path.read_bytes()
        with closing(storage.connect(self.target.db_path)) as c:
            self.assertFalse(schema.migrate(c))
        self.assertEqual(before, self.target.db_path.read_bytes())

    def test_events_coalesce_and_delta_is_indexed_without_consuming_new_changes(self):
        add_viewer_event(self.target, "First display")
        add_viewer_event(self.target, "Second display")
        old, rows = self.snapshot()
        ids = tuple(row[1] for row in rows)
        with closing(storage.connect_initialized(self.target)) as c:
            storage.begin_initialized_write(c, self.target)
            projection.mark_tasks_locked(c, self.project, (ids[0], ids[0]))
            c.commit()
        with closing(storage.connect_initialized_readonly(self.target)) as c:
            first = projection.read_state(c, self.project)
            self.assertEqual(projection.changed_tasks(c, self.project, after=old.source_generation,
                through=first.source_generation), ((ids[0], first.source_generation),))
            plan = " ".join(str(tuple(row)) for row in c.execute("EXPLAIN QUERY PLAN SELECT task_id FROM viewer_task_revisions WHERE project_id=? AND source_generation>? AND source_generation<=? ORDER BY source_generation,task_id", (self.project, old.source_generation, first.source_generation)))
            self.assertIn("idx_viewer_task_revisions_generation", plan)
            self.assertNotIn("SCAN viewer_task_revisions", plan)
        with closing(storage.connect_initialized(self.target)) as c:
            storage.begin_initialized_write(c, self.target)
            projection.mark_tasks_locked(c, self.project, ids)
            c.commit()
        with closing(storage.connect_initialized_readonly(self.target)) as c:
            latest = projection.read_state(c, self.project)
            self.assertEqual({row[0] for row in projection.changed_tasks(c, self.project,
                after=first.source_generation, through=latest.source_generation)}, set(ids))
        self.assertEqual(len(self.snapshot()[1]), 2)

    def test_rollback_and_missing_parent_never_leave_a_notification(self):
        add_viewer_event(self.target, "Rollback subject")
        before = self.snapshot()
        with closing(storage.connect_initialized(self.target)) as c:
            storage.begin_initialized_write(c, self.target)
            projection.mark_tasks_locked(c, self.project, (before[1][0][1],))
            c.rollback()
            storage.begin_initialized_write(c, self.target)
            with self.assertRaises(storage.StorageError):
                projection.mark_tasks_locked(c, self.project, ("tg_task_ffffffffffffffff",))
            c.rollback()
        self.assertEqual(before, self.snapshot())

    def test_global_invalidation_rotates_restore_epoch_without_touching_revisions(self):
        add_viewer_event(self.target, "Retained display")
        original, rows = self.snapshot()
        first = projection.reset_restored_database(self.target)
        second = projection.reset_restored_database(self.target)
        self.assertEqual(len({original.database_epoch, first.database_epoch, second.database_epoch}), 3)
        self.assertEqual(second.invalidated_generation, original.source_generation + 2)
        self.assertEqual(self.snapshot()[1], rows)
        self.assertIsNone(second.cache_generation)
        self.assertIsNone(second.html_generation)

    def test_tuple_types_parent_and_future_revision_fail_closed(self):
        add_viewer_event(self.target, "Bound display")
        before = self.snapshot()
        for sql, args in (
            ("UPDATE viewer_projection_state SET cache_generation=0", ()),
            ("UPDATE viewer_projection_state SET invalidated_generation=1.5", ()),
            ("UPDATE viewer_projection_state SET database_epoch=?", ("private-value",)),
            ("UPDATE viewer_task_revisions SET source_generation=source_generation+100", ()),
            ("DELETE FROM viewer_task_revisions", ()),
        ):
            with self.subTest(sql=sql), closing(storage.connect(self.target.db_path)) as c:
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute(sql, args)
                c.rollback()
        self.assertEqual(before, self.snapshot())

    def test_event_overflow_aborts_business_mutation(self):
        with closing(storage.connect(self.target.db_path)) as c:
            c.execute("UPDATE viewer_maintenance_state SET source_generation=?", (projection.MAX_GENERATION,))
            c.commit()
        before = self.snapshot()
        with self.assertRaises(TaskRepositoryError):
            add_viewer_event(self.target, "Must roll back")
        self.assertEqual(before, self.snapshot())
        with closing(storage.connect(self.target.db_path)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_concurrent_writers_serialize_and_both_changes_remain(self):
        add_viewer_event(self.target, "Concurrent first")
        add_viewer_event(self.target, "Concurrent second")
        before, rows = self.snapshot()
        barrier = threading.Barrier(2)
        def write(task_id):
            with closing(storage.connect_initialized(self.target)) as c:
                barrier.wait(timeout=10)
                storage.begin_initialized_write(c, self.target, operation_local=True)
                projection.mark_tasks_locked(c, self.project, (task_id,))
                c.commit()
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(write, [row[1] for row in rows]))
        with closing(storage.connect_initialized_readonly(self.target)) as c:
            after = projection.read_state(c, self.project)
            changes = projection.changed_tasks(c, self.project, after=before.source_generation, through=after.source_generation)
        self.assertEqual(after.source_generation, before.source_generation + 2)
        self.assertEqual({row[0] for row in changes}, {row[1] for row in rows})
        self.assertEqual(len({row[1] for row in changes}), 2)

    def test_changed_id_query_does_not_grow_with_event_history(self):
        add_viewer_event(self.target, "History scale subject")
        task = self.snapshot()[1][0][1]
        costs = []
        for size in (100, 1000):
            with closing(storage.connect_initialized(self.target)) as c:
                storage.begin_initialized_write(c, self.target, operation_local=True)
                count = c.execute("SELECT count(*) FROM task_events").fetchone()[0]
                # Synthetic history volume only; each insertion exercises the
                # actual event trigger and stable Task parent.
                c.executemany("""INSERT INTO task_events(task_event_id,project_id,task_id,event_type,summary,created_at)
                    VALUES(?,?,?,'task_updated','History volume fixture','2026-10-27T00:00:00Z')""",
                    [(f"tg_event_{i:016x}", self.project, task) for i in range(count, size)])
                c.commit()
            with closing(storage.connect_initialized_readonly(self.target)) as c:
                state = projection.read_state(c, self.project)
                statements, steps = [], []
                c.set_trace_callback(statements.append)
                c.set_progress_handler(lambda: steps.append(1) or 0, 1)
                selected = projection.changed_tasks(c, self.project, after=0, through=state.source_generation)
                c.set_progress_handler(None, 0)
                c.set_trace_callback(None)
                self.assertEqual(selected, ((task, state.source_generation),))
                self.assertFalse(any("task_events" in sql for sql in statements))
                costs.append(len(steps))
        self.assertEqual(costs[0], costs[1])


class ViewerProjectionMigrationTests(unittest.TestCase):
    def test_completed_native33_reviews_and_unrelated_rowids_remain_exact(self):
        from tests.review_answer_fixture_support import review_fixture
        from tests.test_m23s_schema22_migration import _source_copy, _add_unrelated_objects
        from task_governance_tool import schema_preservation
        with review_fixture(schema_version=33) as f:
            artifact = f.collected_source_artifact()
            with closing(storage.connect(f.db)) as c:
                _add_unrelated_objects(c)
            with _source_copy(f.db) as c, candidate_runtime(34):
                # Business rows, rowids and unrelated user objects are retained;
                # only the documented derived relations/DDL and marker are new.
                before = schema_preservation.capture(c, changed_object_names={
                    schema.BUNDLE_TABLE, schema.CYCLE_GUARD} | set(schema.TABLE_SQL)
                    | set(schema.INDEX_SQL) | set(schema.TRIGGER_SQL))
                schema.migrate(c)
                marker = c.execute("SELECT version,name,applied_at FROM schema_migrations WHERE version=34").fetchone()
                schema_preservation.assert_preserved(c, before, added_marker=tuple(marker))
                from tests.test_m23s_schema22_validation import _bundle_artifacts
                artifacts = _bundle_artifacts(c, f.basis["project_id"])[1]
                self.assertIn(artifact, artifacts.values())

    def test_populated33_migration_preserves_old_rows_and_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp, candidate_runtime(33):
            target = make_target(Path(tmp), enabled=False)
            add_viewer_event(target, "Source 33 display")
            with closing(storage.connect(target.db_path)) as c:
                before = _logical_snapshot(c)
                original = schema._apply_delta_locked
                def fail(connection):
                    original(connection)
                    raise sqlite3.OperationalError("isolated migration interruption")
                with candidate_runtime(34), mock.patch.object(schema, "_apply_delta_locked", side_effect=fail):
                    with self.assertRaises(storage.StorageError):
                        schema.migrate(c)
                self.assertEqual(_logical_snapshot(c), before)
                with candidate_runtime(34):
                    self.assertTrue(schema.migrate(c))
                    schema.validate_storage(c)
                    self.assertFalse(schema.migrate(c))
                    self.assertEqual(schema.owned_fingerprint(c), schema.OWNED_FINGERPRINT)
                    state = projection.read_state(c, target.project.project_id)
                    revision = c.execute("SELECT source_generation FROM viewer_task_revisions").fetchone()[0]
                    self.assertEqual(revision, state.source_generation)
                    self.assertEqual(state.invalidated_generation, state.source_generation)
                self.assertFalse(c.in_transaction)
                self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(c.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)


class ViewerProjectionMutationTests(unittest.TestCase):
    def test_checkpoint_contract_and_exact_replay_cover_non_task_timestamps(self):
        from tests.test_checkpoint_cli import checkpoint_args
        with tempfile.TemporaryDirectory() as tmp:
            repo, db = initialize(Path(tmp))
            task = add_task(db, repo, status="ready")["task_id"]
            activated = run_taskgov("task", "edit", task, "--status", "in_progress", "--contract-scope",
                "Track display changes", "--contract-acceptance", "Initial committed revisions",
                "--contract-authority-ref", "user:isolated-tracking-test", "--repo", str(repo), "--db", str(db), "--json")
            self.assertEqual(activated.returncode, 0, activated.stdout)
            def state():
                with closing(storage.connect_readonly(db)) as c:
                    return tuple(c.execute("SELECT t.updated_at,r.source_generation FROM tasks t JOIN viewer_task_revisions r ON r.task_id=t.task_id WHERE t.task_id=?", (task,)).fetchone())
            before = state()
            args = (*checkpoint_args(db, repo, task), "--json")
            result = run_taskgov(*args)
            self.assertEqual(result.returncode, 0, result.stdout)
            checkpoint = state()
            self.assertEqual(checkpoint[0], before[0])
            self.assertGreater(checkpoint[1], before[1])
            replay = run_taskgov(*args)
            self.assertEqual(replay.returncode, 0, replay.stdout)
            self.assertEqual(state(), checkpoint)
            edited = run_taskgov("task", "edit", task, "--contract-scope", "Track display changes",
                "--contract-acceptance", "Committed revisions are complete", "--contract-authority-ref",
                "user:isolated-tracking-test", "--contract-change-reason", "Exercise isolated Contract revision",
                "--repo", str(repo), "--db", str(db), "--json")
            self.assertEqual(edited.returncode, 0, edited.stdout)
            self.assertGreater(state()[1], checkpoint[1])

    def test_real_continuation_findings_reassessment_and_collected_registration(self):
        from tests.review_answer_fixture_support import review_fixture, A, B
        from task_governance_tool import review_continuation_repository as continuations
        with review_fixture(schema_version=34) as f:
            def revision():
                with closing(storage.connect_readonly(f.db)) as c:
                    return projection.read_state(c, f.basis["project_id"]).source_generation
            start = revision()
            registered = f.register([f.original(A, verdict="changes_requested", findings=[
                dict(severity="medium", summary="Review one local boundary")]), f.original(B)])
            first = revision()
            self.assertGreater(first, start)
            old = registered["receipts"][0]["receipt"]["review_receipt_id"]
            finding = registered["receipts"][0]["findings"][0]["finding"]["review_finding_id"]
            retained = registered["receipts"][1]["receipt"]["review_receipt_id"]
            record = f.prepare(prior=old)
            prepared = revision()
            self.assertGreater(prepared, first)
            with closing(storage.connect_initialized(f.target)) as c, c:
                c.execute("BEGIN IMMEDIATE")
                continuations.advance_prepared_locked(c, record, f.caller)
            self.assertEqual(revision(), prepared)
            successor = f.register([f.original(A, continued=record)], continued=record, retained=(retained,))
            judged = revision()
            self.assertGreater(judged, prepared)
            f.resolve(finding, successor=successor["receipts"][0]["receipt"]["review_receipt_id"])
            self.assertGreater(revision(), judged)
        with review_fixture(schema_version=34) as f:
            with closing(storage.connect_readonly(f.db)) as c:
                initial = projection.read_state(c, f.basis["project_id"]).source_generation
            artifact = f.collected_source_artifact()
            self.assertEqual(artifact.payload["source_schema_version"], 34)
            with closing(storage.connect_initialized_readonly(f.target)) as c:
                self.assertGreater(projection.read_state(c, f.basis["project_id"]).source_generation, initial)

    def test_actual_rebind_invalidates_only_project_and_rolls_back_on_failure(self):
        from tests.test_project_identity_bindings import rebound_target, apply_rebind
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=False)
            add_viewer_event(target, "Binding subject")
            with closing(storage.connect_readonly(target.db_path)) as c:
                before = projection.read_state(c, target.project.project_id)
                revisions = tuple(tuple(row) for row in c.execute("SELECT * FROM viewer_task_revisions"))
            destination = rebound_target(target)
            apply_rebind(target, destination)
            with closing(storage.connect_readonly(target.db_path)) as c:
                after = projection.read_state(c, target.project.project_id)
                self.assertEqual(after.database_epoch, before.database_epoch)
                self.assertEqual(after.invalidated_generation, before.source_generation + 1)
                self.assertEqual(tuple(tuple(row) for row in c.execute("SELECT * FROM viewer_task_revisions")), revisions)
            with self.assertRaises(storage.StorageError):
                apply_rebind(target, destination)
            with closing(storage.connect_readonly(target.db_path)) as c:
                self.assertEqual(projection.read_state(c, target.project.project_id), after)

    def test_real_target_receipt_review_completion_and_reopen_mark_own_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, db = initialize(Path(tmp))
            task = add_task(db, repo)["task_id"]
            def revision():
                with closing(storage.connect_readonly(db)) as c:
                    return c.execute("SELECT source_generation FROM viewer_task_revisions WHERE task_id=?", (task,)).fetchone()[0]
            initial = revision()
            generation = seed_current_review_evidence(db, repo, task)
            reviewed = revision()
            self.assertGreater(reviewed, initial)
            with closing(storage.connect_readonly(db)) as c:
                before_failure = _logical_snapshot(c)
            with mock.patch.object(projection, "mark_tasks_locked",
                    side_effect=storage.StorageError("project_state_unreadable", "Isolated tracking failure")):
                failed = add_receipt(db, repo, task, generation)
            self.assertNotEqual(failed.returncode, 0)
            with closing(storage.connect_readonly(db)) as c:
                self.assertEqual(_logical_snapshot(c), before_failure)
            receipt = add_receipt(db, repo, task, generation)
            self.assertEqual(receipt.returncode, 0, receipt.stdout)
            verified = revision()
            self.assertGreater(verified, reviewed)
            done = completion(db, repo, task)
            self.assertEqual(done.returncode, 0, done.stdout)
            completed = revision()
            self.assertGreater(completed, verified)
            reopened = run_taskgov("task", "edit", task, "--status", "in_progress", "--reopen-reason",
                "Isolated revision coverage", "--repo", str(repo), "--db", str(db), "--json")
            self.assertEqual(reopened.returncode, 0, reopened.stdout)
            self.assertGreater(revision(), completed)


class PublicViewerProjectionSetupTests(unittest.TestCase):
    from tests.test_criterion_link_collector_migration import PublicCriterionLinkCollectorSetupTests as _public
    cli = _public.cli
    business = _public.business

    def test_public33_upgrade_two_restores_and_readonly_reentry(self):
        from tests.m14_test_support import make_physical_install, refresh_test_manifest
        from task_governance_tool import backup
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.install = make_physical_install(root)
        self.enterContext(mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "00000000-0000-4000-8000-000000000001"}))
        source = self.install.skill_root / "scripts/task_governance_tool/storage.py"
        current = source.read_text(encoding="utf-8")
        self.assertEqual(current.count("SCHEMA_VERSION = 34"), 1)
        source.write_text(current.replace("SCHEMA_VERSION = 34", "SCHEMA_VERSION = 33", 1), encoding="utf-8")
        refresh_test_manifest(self.install.skill_root)
        self.assertEqual(self.cli("setup")["schema_to"], 33)
        self.cli("task", "add", "--title", "Preserve source33 display", "--status", "ready")
        with closing(storage.connect_readonly(self.install.db_path)) as c:
            business = self.business(c)
        source.write_text(current, encoding="utf-8")
        refresh_test_manifest(self.install.skill_root)
        before = self.install.state_snapshot()
        preview = self.cli("setup", "--read-only")
        self.assertEqual((preview["schema_from"], preview["schema_to"]), (33, 34))
        self.assertEqual(self.install.state_snapshot(), before)
        applied = self.cli("setup")
        self.assertEqual(applied["completed_writes"], ["migration_backup", "database_migrate", "evidence_projection_publish", "viewer_publish"])
        epochs = set()
        for iteration in range(3):
            with closing(storage.connect_initialized_readonly(self.install.target)) as c:
                self.assertEqual(self.business(c), business)
                state = projection.read_state(c, self.install.target.project.project_id)
                self.assertNotIn(state.database_epoch, epochs)
                epochs.add(state.database_epoch)
            before = self.install.state_snapshot()
            self.cli("doctor")
            self.assertEqual(self.cli("setup")["completed_writes"], [])
            self.assertEqual(self.install.state_snapshot(), before)
            if iteration < 2:
                with backup.managed_backup_lock(self.install.target):
                    backup.publish_setup_backup(self.install.target, 3)
                # Deliberate recovery of a disposable fixture, never live state.
                self.install.db_path.unlink()
                recovered = self.cli("setup")
                self.assertEqual((recovered["schema_from"], recovered["schema_to"]), (34, 34))
                self.assertEqual(recovered["completed_writes"], ["database_restore", "evidence_projection_publish", "viewer_publish"])
