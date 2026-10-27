"""Current incremental admission and retained explicit whole-source diagnostics."""
from contextlib import closing, contextmanager
import json
import sqlite3
import sys
import unittest
from unittest import mock

from tests.review_answer_fixture_support import review_fixture
from tests.test_viewer_maintenance import add_viewer_event, viewer_state
from tests.verification_receipt_test_support import add_task
from task_governance_tool import storage, tasks, viewer, viewer_maintenance
from task_governance_tool import task_ownership, schema_task_event_notes
from task_governance_tool import review_session_repository, review_answer_repository
from task_governance_tool import review_continuation_repository
from task_governance_tool.backup_metadata_repository import configure_project_maintenance
from task_governance_tool import viewer_metadata_repository as metadata
from task_governance_tool import viewer_projection_cache as cache


TIME = "2026-10-25T00:00:00Z"
PROFILES = []


@contextmanager
def populated_source(task_count=1, schema_version=34):
    with review_fixture(schema_version=schema_version) as fixture:
        configure_project_maintenance(fixture.target, requested_interval_minutes=None,
            requested_generations=None, enabled_at=TIME)
        with closing(storage.connect_initialized(fixture.target)) as connection:
            fixture.add(connection)
            tasks.edit_task(connection, fixture.target.project, fixture.task_id,
                database_target=fixture.target, caller=fixture.caller, add_note="Retained note body")
            connection.commit()
        for index in range(task_count - 1):
            other = add_task(fixture.db, fixture.repo, title=f"Other source {index}", status="ready")
            with closing(storage.connect_initialized(fixture.target)) as connection:
                tasks.edit_task(connection, fixture.target.project, other["task_id"],
                    database_target=fixture.target, caller=fixture.caller, add_note="Other retained body")
                connection.commit()
        yield fixture


@contextmanager
def measured():
    functions = {
        task_ownership.validate_storage_rows.__code__: "ownership",
        schema_task_event_notes.validate_note_rows.__code__: "notes",
        review_session_repository.read_bindings.__code__: "bindings",
        review_answer_repository.validate_storage_rows.__code__: "answers",
        review_continuation_repository.validate_judgment_storage.__code__: "judgments",
    }
    counts = dict.fromkeys(functions.values(), 0)
    connections = set()
    def observe(frame, event, arg):
        if event == "call" and frame.f_code in functions:
            name = functions[frame.f_code]
            if name == "bindings" and frame.f_locals.get("receipt_ids") is not None:
                return
            if name in ("answers", "judgments") and frame.f_locals.get("review_task") is not None:
                return
            counts[name] += 1
            connection = frame.f_locals["connection"]
            if not connection.in_transaction:
                raise AssertionError("source validation escaped its transaction")
            if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise AssertionError("capture is not query-only")
            connections.add(connection)
    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        yield counts, connections
    finally:
        sys.setprofile(previous)


def comparable(snapshot):
    if snapshot is None:
        return None
    body = snapshot.snapshot
    return dict(version=body["snapshot_version"], schema=body["source_schema_version"],
        counts=body["counts"], tasks=[dict(title=row["title"], status=row["status"],
            review=row["review_evidence"]["gate"],
            events=[event["summary"] for event in row["events"]]) for row in body["tasks"]])


@contextmanager
def observed_readers():
    """Observe real readers, including admission failures before return."""
    readers = []
    original = storage.connect_readonly
    def open_reader(*args, **kwargs):
        connection = original(*args, **kwargs)
        readers.append(connection)
        return connection
    with mock.patch.object(storage, "connect_readonly", side_effect=open_reader):
        yield readers


class ViewerCaptureAdmissionTests(unittest.TestCase):
    def assert_readers_closed(self, readers):
        self.assertTrue(readers)
        for connection in readers:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_capture_counts_and_outputs_for_due_force_current_disabled(self):
        # Approved incremental contract: no unrelated hidden-state audit.
        for task_count in (1, 4):
            with populated_source(task_count) as fixture:
                for force in (False, True):
                    with measured() as (counts, _):
                        refresh = (viewer_maintenance.publish_setup_viewer if force
                                   else viewer_maintenance.run_routine_viewer_refresh)
                        result = refresh(fixture.target, observed_at=TIME)
                    self.assertEqual(result.code, "succeeded")
                    self.assertEqual(set(counts.values()), {0})
                    saved = cache.load_candidate(cache.cache_output(fixture.target))
                    self.assertEqual(len(saved.tasks), task_count)
                with measured() as (counts, _):
                    self.assertEqual(viewer_maintenance.run_routine_viewer_refresh(
                        fixture.target, observed_at=TIME).code, "current")
                self.assertEqual(set(counts.values()), {0})
                from dataclasses import replace
                original = metadata._basis
                with mock.patch.object(metadata, "_basis",
                        side_effect=lambda *args: replace(original(*args), enabled_at=None)), \
                     measured() as (counts, _):
                    self.assertEqual(viewer_maintenance.run_routine_viewer_refresh(
                        fixture.target, observed_at=TIME).code, "not_opted_in")
                self.assertEqual(set(counts.values()), {0})

    def test_direct_snapshot_consumers_keep_complete_current_and_legacy_admission(self):
        for schema in (33, 34):
            with self.subTest(schema=schema), populated_source(4, schema) as fixture:
                full_note = "n" * 1200 + " undisplayed tail"
                with closing(storage.connect_initialized(fixture.target)) as connection:
                    tasks.edit_task(connection, fixture.target.project, fixture.task_id,
                        database_target=fixture.target, caller=fixture.caller, add_note=full_note)
                    connection.commit()
                with closing(storage.connect_snapshot_readonly(fixture.db)) as connection:
                    with measured() as (counts, connections):
                        snapshot = viewer.build_viewer_snapshot(connection, fixture.target, generated_at=TIME)
                    self.assertEqual(counts, dict.fromkeys(("ownership", "notes", "bindings", "answers", "judgments"), 1))
                    self.assertEqual(connections, {connection})
                    self.assertEqual(snapshot.task_count, 4)
                    encoded = json.dumps(snapshot.snapshot)
                    self.assertNotIn('"note_body"', encoded)
                    self.assertNotIn("undisplayed tail", encoded)

    def test_explicit_full_capture_still_rejects_every_corrupt_source(self):
        faults = (
            ("ownership", "task_ownership", "UPDATE task_ownership SET owner_session_id='20000000-0000-4000-8000-000000000001' WHERE state='owned'"),
            ("note_privacy", "task_events", "UPDATE task_events SET note_body='token=synthetic-private-value' WHERE note_body IS NOT NULL AND task_id<>?"),
            ("binding", "review_receipt_sessions", "UPDATE review_receipt_sessions SET session_id='invalid-session'"),
            ("collected_source", "review_collection_sources", "DELETE FROM review_collection_sources"),
            ("judgment", "review_receipt_judgments", "DELETE FROM review_receipt_judgments"),
            ("other_task", "tasks", "UPDATE tasks SET title=CAST(X'7469746c65' AS BLOB) WHERE task_id<>?"),
            ("reference", "evidence_references", "UPDATE evidence_references SET target_generation=target_generation+1 WHERE source_kind='review_receipt'"),
            ("migration", "schema_migrations", "UPDATE schema_migrations SET name='unexpected_migration' WHERE version=29"),
            ("project", "project_meta", "UPDATE project_meta SET canonical_path_hash='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'"),
        )
        for name, table, sql in faults:
            with self.subTest(source=name), populated_source(2) as fixture:
                self.assertEqual(viewer_maintenance.publish_setup_viewer(fixture.target, observed_at=TIME).code, "succeeded")
                output = fixture.target.resolved_viewer_path
                original_html = output.read_bytes()
                with closing(sqlite3.connect(fixture.db)) as connection:
                    triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
                    for trigger, _ in triggers:
                        connection.execute('DROP TRIGGER "' + trigger.replace('"', '""') + '"')
                    connection.execute("PRAGMA ignore_check_constraints=ON")
                    changed = connection.execute(sql, (fixture.task_id,) if "?" in sql else ())
                    self.assertGreater(changed.rowcount, 0)
                    for _, definition in triggers:
                        connection.execute(definition)
                    connection.commit()
                original_db = fixture.db.read_bytes()
                with closing(storage.connect_snapshot_readonly(fixture.db)) as connection:
                    with self.assertRaises(storage.StorageError):
                        viewer.build_viewer_snapshot(connection, fixture.target, generated_at=TIME)
                self.assertEqual(fixture.db.read_bytes(), original_db)
                self.assertEqual(output.read_bytes(), original_html)
                # A full display rebuild excludes hidden ownership/raw notes;
                # every other corruption remains in the consumed source boundary.
                if name not in {"ownership", "note_privacy"}:
                    self.assertEqual(viewer_maintenance.publish_setup_viewer(
                        fixture.target, observed_at=TIME).code, "failed")
                    self.assertEqual(output.read_bytes(), original_html)

    def test_pre_replacement_changes_use_fresh_structural_guards_and_bounded_attempts(self):
        from dataclasses import replace
        for force in (False, True):
            for change in ("generation_once", "generation_always", "opt_out", "hidden_note_corruption"):
                with self.subTest(force=force, change=change), populated_source(2) as fixture:
                    self.assertEqual(viewer_maintenance.publish_setup_viewer(fixture.target, observed_at=TIME).code, "succeeded")
                    output = fixture.target.resolved_viewer_path
                    last_good = output.read_bytes()
                    before = viewer_state(fixture.target)
                    add_viewer_event(fixture.target, "Pending capture")
                    original_write = viewer_maintenance.write_viewer_html
                    original_replace = viewer.os.replace
                    original_basis = metadata._basis
                    attempts, guards = [], []

                    def atomic_replace(*args, **kwargs):
                        self.assert_readers_closed(readers[:-1])
                        guard = readers[-1]
                        self.assertTrue(guard.in_transaction)
                        self.assertEqual(guard.execute("PRAGMA query_only").fetchone()[0], 1)
                        self.assertTrue(any(call.args[0] is guard and call.kwargs.get("operation_local", False)
                            for call in admission.call_args_list))
                        guards.append(guard)
                        return original_replace(*args, **kwargs)

                    def change_before_guard(target, html, *, replace_guard=None):
                        self.assert_readers_closed(readers)
                        attempts.append(len(readers))
                        if change == "generation_always" or (change == "generation_once" and len(attempts) == 1):
                            # This deliberately interleaved business writer is
                            # not Viewer capture. Its own full admission may
                            # run outside a query-only transaction.
                            saved_profile = sys.getprofile()
                            sys.setprofile(None)
                            try:
                                add_viewer_event(fixture.target, f"Pre-replacement change {len(attempts)}")
                            finally:
                                sys.setprofile(saved_profile)
                        elif change == "hidden_note_corruption":
                            with closing(sqlite3.connect(fixture.db)) as connection:
                                ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_events_note_body_immutable'").fetchone()[0]
                                connection.execute("DROP TRIGGER trg_task_events_note_body_immutable")
                                changed = connection.execute("UPDATE task_events SET note_body='token=synthetic-private-value' WHERE note_body IS NOT NULL AND task_id<>?", (fixture.task_id,))
                                self.assertGreater(changed.rowcount, 0)
                                connection.execute(ddl)
                                connection.commit()
                        if change == "opt_out":
                            with mock.patch.object(metadata, "_basis",
                                    side_effect=lambda *args: replace(original_basis(*args), enabled_at=None)):
                                return original_write(target, html, replace_guard=replace_guard)
                        return original_write(target, html, replace_guard=replace_guard)

                    with observed_readers() as readers, \
                         mock.patch.object(storage, "validate_current_database", wraps=storage.validate_current_database) as admission, \
                         mock.patch.object(viewer_maintenance, "write_viewer_html", side_effect=change_before_guard), \
                         mock.patch.object(viewer.os, "replace", side_effect=atomic_replace), \
                         measured() as (counts, _):
                        refresh = viewer_maintenance.publish_setup_viewer if force else viewer_maintenance.run_routine_viewer_refresh
                        result = refresh(fixture.target, observed_at=TIME)

                    self.assertEqual(len(attempts), 1 if change == "hidden_note_corruption" else 2)
                    self.assertEqual(len(guards), len(set(guards)))
                    self.assert_readers_closed(readers)
                    self.assertEqual(list(output.parent.glob(".task-viewer-*.tmp")), [])
                    recovered = change in {"generation_once", "hidden_note_corruption"}
                    self.assertEqual((result.code, result.renders),
                        ("succeeded", 1) if recovered else ("failed", 0))
                    if recovered:
                        self.assertNotEqual(output.read_bytes(), last_good)
                    else:
                        self.assertEqual(output.read_bytes(), last_good)
                    with closing(storage.connect_initialized_readonly(fixture.target, operation_local=True)) as connection:
                        final = metadata.read_viewer_maintenance(connection, fixture.target.project.project_id)
                    self.assertEqual(final.due, not recovered)
                    if not recovered:
                        self.assertIsNone(final.rendered_generation)
                        self.assertEqual(final.last_success_at, before.last_success_at)
                    if change == "hidden_note_corruption":
                        self.assertEqual(set(counts.values()), {0})
                        with closing(storage.connect_snapshot_readonly(fixture.db)) as connection:
                            with self.assertRaises(storage.StorageError):
                                viewer.build_viewer_snapshot(connection, fixture.target, generated_at=TIME)


if __name__ == "__main__":
    unittest.main()
