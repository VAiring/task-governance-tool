"""Selected Viewer corruption rejection and work independent of unrelated rows."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from tests.test_viewer_maintenance import make_target, FIXED_TIME
from tests.test_viewer_review_inventory import seed_reviews
from task_governance_tool import storage, viewer_maintenance as service
from task_governance_tool import viewer_metadata_repository as metadata
from task_governance_tool import viewer_projection_cache as cache
from task_governance_tool.viewer_projection_repository import mark_tasks_locked


@contextmanager
def reject_whole_audits():
    """Observe actual SQL at each separately admitted read/write connection."""
    audits = []
    def authorizer(action, first, second, database, trigger):
        if action == sqlite3.SQLITE_PRAGMA and first.lower() in {
                "quick_check", "integrity_check", "foreign_key_check"}:
            audits.append(first)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    def wrap(original):
        def opened(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_authorizer(authorizer)
            return connection
        return opened
    with mock.patch.object(storage, "connect_readonly", side_effect=wrap(storage.connect_readonly)), \
         mock.patch.object(storage, "connect_existing", side_effect=wrap(storage.connect_existing)):
        yield audits


class ViewerTargetedAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = make_target(self.root, enabled=True)
        self.selected, self.other, _ = seed_reviews(self.target)

    def publish(self):
        return service.run_routine_viewer_refresh(self.target, observed_at=FIXED_TIME)

    def mark(self):
        with closing(storage.connect(self.target.db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            mark_tasks_locked(connection, self.target.project.project_id, (self.selected,))
            connection.commit()

    def test_rebuild_delta_current_disabled_and_legacy_metadata_writes_have_no_whole_audit(self):
        (self.root / "disabled").mkdir()
        disabled = make_target(self.root / "disabled", enabled=False)
        with reject_whole_audits() as audits:
            self.assertEqual(self.publish().code, "succeeded")
            self.assertEqual(self.publish().code, "current")
            self.mark()
            self.assertEqual(self.publish().code, "succeeded")
            basis = metadata.read_projection_basis(self.target)
            metadata.record_viewer_publication(self.target,
                source_generation=basis.state.source_generation, published_at=FIXED_TIME)
            metadata.record_viewer_attempt_outcome(self.target, code="deferred", occurred_at=FIXED_TIME)
            self.assertEqual(service.run_routine_viewer_refresh(disabled).code, "not_opted_in")
        self.assertEqual(audits, [])

    def test_invalid_selected_values_and_detached_parent_preserve_last_good(self):
        self.assertEqual(self.publish().code, "succeeded")
        old_html = self.target.resolved_viewer_path.read_bytes()
        # First-generation Findings are outside the recent-ten presentation.
        with closing(storage.connect(self.target.db_path)) as connection:
            original = dict(connection.execute("SELECT * FROM review_findings ORDER BY rowid LIMIT 1").fetchone())
        for field, value in (("status", "unknown"), ("severity", "unknown"),
                ("status", b"open"), ("summary", b"bad text"),
                ("review_receipt_id", "tg_review_receipt_" + "f" * 16),
                ("review_receipt_id", original["review_receipt_id"].encode())):
            with self.subTest(field=field, value=value):
                self.mark()
                with closing(storage.connect(self.target.db_path)) as connection:
                    connection.execute("PRAGMA foreign_keys=OFF")
                    connection.execute("PRAGMA ignore_check_constraints=ON")
                    connection.execute(f"UPDATE review_findings SET {field}=? WHERE review_finding_id=?",
                        (value, original["review_finding_id"]))
                    connection.commit()
                with reject_whole_audits() as audits:
                    self.assertEqual(self.publish().code, "failed")
                self.assertEqual(audits, [])
                self.assertEqual(self.target.resolved_viewer_path.read_bytes(), old_html)
                with closing(storage.connect(self.target.db_path)) as connection, connection:
                    connection.execute(f"UPDATE review_findings SET {field}=? WHERE review_finding_id=?",
                        (original[field], original["review_finding_id"]))

    def test_invalid_opt_in_never_authorizes_publication(self):
        self.assertEqual(self.publish().code, "succeeded")
        old_html = self.target.resolved_viewer_path.read_bytes()
        self.mark()
        with closing(storage.connect(self.target.db_path)) as connection:
            trigger = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='project_maintenance' AND sql LIKE '%enabled_at%'").fetchall()
            for row in trigger:
                connection.execute('DROP TRIGGER "' + row["name"] + '"')
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE project_maintenance SET enabled_at=CAST(? AS BLOB)", (FIXED_TIME,))
            for row in trigger:
                connection.execute(row["sql"])
            connection.commit()
        self.assertEqual(self.publish().code, "failed")
        self.assertEqual(self.target.resolved_viewer_path.read_bytes(), old_html)

    def test_pending_revision_values_cannot_hide_a_changed_task(self):
        self.assertEqual(self.publish().code, "succeeded")
        last_good = self.target.resolved_viewer_path.read_bytes()
        last_cache = cache.cache_output(self.target).path.read_bytes()
        accepted = metadata.read_projection_basis(self.target)
        with closing(storage.connect(self.target.db_path)) as connection, connection:
            connection.execute("UPDATE tasks SET title='Changed pending title' WHERE task_id=?",
                (self.selected,))
        self.mark()
        with closing(storage.connect(self.target.db_path)) as connection:
            original = dict(connection.execute("SELECT rowid,* FROM viewer_task_revisions WHERE task_id=?",
                (self.selected,)).fetchone())
            guards = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='viewer_task_revisions'").fetchall()
        generation = original["source_generation"]

        def replace(values):
            # Disposable fault injection; restore exact schema before admission.
            with closing(storage.connect(self.target.db_path)) as connection:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute("PRAGMA ignore_check_constraints=ON")
                for row in guards:
                    connection.execute('DROP TRIGGER "' + row["name"] + '"')
                connection.execute("UPDATE viewer_task_revisions SET project_id=?,task_id=?,source_generation=? "
                    "WHERE rowid=?", (values["project_id"], values["task_id"],
                                      values["source_generation"], original["rowid"]))
                for row in guards:
                    connection.execute(row["sql"])
                connection.commit()

        cases = [("source_generation", value) for value in
                 (str(generation).encode(), "invalid", generation - 0.5, generation + 1, -1)]
        cases += [("project_id", original["project_id"].encode()),
                  ("project_id", "0_foreign"),
                  ("project_id", "zz_foreign"),
                  ("task_id", self.selected.encode()),
                  ("task_id", "tg_task_" + "f" * 16)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                replace({**original, field: value})
                try:
                    with reject_whole_audits() as audits:
                        self.assertEqual(self.publish().code, "failed")
                    self.assertEqual(audits, [])
                    self.assertEqual(self.target.resolved_viewer_path.read_bytes(), last_good)
                    self.assertEqual(cache.cache_output(self.target).path.read_bytes(), last_cache)
                    basis = metadata.read_projection_basis(self.target)
                    self.assertEqual(basis.state.cache_generation, accepted.state.cache_generation)
                    self.assertEqual(basis.state.html_generation, accepted.state.html_generation)
                    self.assertEqual(basis.viewer.rendered_generation, accepted.viewer.rendered_generation)
                    self.assertLess(basis.viewer.rendered_generation, generation)
                finally:
                    replace(original)
        self.assertEqual(self.publish().code, "succeeded")
        current = cache.load_candidate(cache.cache_output(self.target))
        self.assertEqual(next(task["title"] for task in current.tasks
            if task["task_id"] == self.selected), "Changed pending title")
        self.assertNotEqual(self.target.resolved_viewer_path.read_bytes(), last_good)
        self.assertEqual(metadata.read_projection_basis(self.target).viewer.rendered_generation, generation)

    def test_revision_selection_work_does_not_grow_with_applied_revisions(self):
        from task_governance_tool.viewer_projection_repository import changed_tasks
        self.assertEqual(self.publish().code, "succeeded")
        applied = metadata.read_projection_basis(self.target).state.cache_generation
        self.mark()
        counts, timings, plans = [], [], []
        for size in (100, 10000):
            with closing(storage.connect(self.target.db_path)) as connection, connection:
                # Populate structurally valid unchanged identities/revisions.
                # No event/history is required by the revision-range query.
                template = dict(connection.execute("SELECT * FROM tasks WHERE task_id=?", (self.other,)).fetchone())
                columns = ",".join('"' + name + '"' for name in template)
                placeholders = ",".join("?" for _ in template)
                identifiers = [f"tg_task_{size:08x}{index:08x}" for index in range(size)]
                connection.executemany(f"INSERT INTO tasks ({columns}) VALUES ({placeholders})",
                    [tuple({**template, "task_id": identifier}.values()) for identifier in identifiers])
                connection.executemany("INSERT INTO viewer_task_revisions VALUES(?,?,?)",
                    [(self.target.project.project_id, identifier, applied) for identifier in identifiers])
            with metadata.projection_snapshot(self.target) as (connection, basis):
                steps, statements = [0], []
                def progress():
                    steps[0] += 1
                    return 0
                connection.set_progress_handler(progress, 1)
                connection.set_trace_callback(statements.append)
                started = time.perf_counter()
                result = changed_tasks(connection, self.target.project.project_id,
                    after=applied, through=basis.state.source_generation)
                timings.append(time.perf_counter() - started)
                counts.append(steps[0])
                connection.set_progress_handler(None, 0)
                connection.set_trace_callback(None)
                self.assertEqual(result, ((self.selected, basis.state.source_generation),))
                for sql in statements:
                    if "FROM viewer_task_revisions" in sql:
                        plans.extend(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql))
        self.assertLessEqual(counts[1], counts[0] * 1.05 + 100, counts)
        self.assertTrue(any("idx_viewer_task_revisions_generation" in row for row in plans), plans)
        self.assertFalse(any("SCAN r" in row or "SCAN viewer_task_revisions" in row for row in plans), plans)
        print(f"selected_revision_range vm_steps={counts} elapsed_seconds={timings}")

    def test_own_cycle_with_foreign_project_cannot_disappear_from_history(self):
        from tests.test_viewer_incremental_scale import extend_native_history
        from task_governance_tool import tasks
        from task_governance_tool.session_identity import CallerIdentity
        from task_governance_tool.viewer_task_projection_repository import _history
        caller = CallerIdentity("00000000-0000-4000-8000-000000000009")
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            task_id = tasks.add_task(connection, self.target.project, title="Own cycle",
                status="in_progress", review_tier=0, caller=caller,
                verification_not_required_reason="Isolated history fixture").task["task_id"]
        extend_native_history(self.target, task_id, caller, 1)
        with closing(storage.connect(self.target.db_path)) as connection:
            guards = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name IN ('task_completion_cycles','task_events')").fetchall()
            for guard in guards:
                connection.execute('DROP TRIGGER "' + guard["name"] + '"')
            connection.execute("PRAGMA foreign_keys=OFF")
            # Remove the event's forward pointer so the cycle's own identity
            # arm must detect the foreign project, without a global FK audit.
            connection.execute("UPDATE task_events SET completion_cycle_id=NULL WHERE task_id=?", (task_id,))
            connection.execute("UPDATE task_completion_cycles SET project_id='foreign' WHERE task_id=?", (task_id,))
            for guard in guards:
                connection.execute(guard["sql"])
            connection.commit()
        with metadata.projection_snapshot(self.target) as (connection, _):
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            with self.assertRaises(storage.StorageError):
                _history(connection, task)

    def test_selected_capture_work_does_not_grow_with_unrelated_finding_history(self):
        self.assertEqual(self.publish().code, "succeeded")
        candidate = cache.load_candidate(cache.cache_output(self.target))
        self.mark()
        counts = []
        plans = []
        original_open = storage.connect_readonly
        for size in (100, 10000):
            with closing(storage.connect(self.target.db_path)) as connection:
                foreign_receipt = connection.execute("SELECT review_receipt_id FROM review_receipts "
                    "WHERE task_id=? LIMIT 1", (self.other,)).fetchone()[0]
                connection.executemany("INSERT INTO review_findings VALUES(?,?,'low','open','Unrelated', '',?,NULL)",
                    [(f"tg_review_finding_{size:08x}{i:08x}", foreign_receipt, FIXED_TIME) for i in range(size)])
                connection.commit()
            steps, statements, readers = [0], [], []
            def progress():
                steps[0] += 1
                return 0
            def opened(*args, **kwargs):
                connection = original_open(*args, **kwargs)
                connection.set_progress_handler(progress, 1)
                connection.set_trace_callback(statements.append)
                readers.append(connection)
                return connection
            with mock.patch.object(storage, "connect_readonly", side_effect=opened):
                basis, result, snapshot = service._capture(self.target, candidate, FIXED_TIME)
            counts.append(steps[0])
            self.assertEqual(len(snapshot.snapshot["tasks"]), 3)
            self.assertEqual(basis.state.source_generation, result.header["applied_generation"])
            with closing(storage.connect_readonly(self.target.db_path)) as connection:
                for sql in statements:
                    if "FROM aliases CROSS JOIN review_receipts" in sql:
                        plans.extend(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql))
                    if "SELECT * FROM task_completion_cycles" in sql:
                        plans.extend(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql))
            for connection in readers:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
        self.assertLessEqual(counts[1], counts[0] * 1.05 + 100, counts)
        self.assertTrue(any("idx_review_findings_receipt" in step for step in plans), plans)
        self.assertTrue(any("idx_viewer_cycles_task_ordinal" in step for step in plans), plans)
        self.assertFalse(any("SCAN finding" in step for step in plans), plans)

    def _native_cycle_task(self):
        from tests.test_viewer_incremental_scale import extend_native_history
        from task_governance_tool import tasks
        from task_governance_tool.session_identity import CallerIdentity
        caller = CallerIdentity("00000000-0000-4000-8000-000000000009")
        with closing(storage.connect_initialized(self.target, operation_local=True)) as connection, connection:
            owner = tasks.add_task(connection, self.target.project, title="Selected cycle owner",
                status="in_progress", review_tier=0, caller=caller,
                verification_not_required_reason="Isolated history fixture").task["task_id"]
        extend_native_history(self.target, owner, caller, 1)
        return owner

    def _replace_event_cycle(self, event_id, cycle_id):
        # Fault injection is confined to this disposable fixture. Restore exact
        # trigger definitions before source admission, without dirtying another Task.
        with closing(storage.connect(self.target.db_path)) as connection:
            guards = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='task_events'").fetchall()
            names = connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='task_events'").fetchall()
            for row in names:
                connection.execute('DROP TRIGGER "' + row[0] + '"')
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE task_events SET completion_cycle_id=? WHERE task_event_id=?",
                (cycle_id, event_id))
            for row in guards:
                connection.execute(row[0])
            connection.commit()

    def test_cycle_reference_aliases_reject_incoming_foreign_and_outgoing_own_links(self):
        from task_governance_tool.viewer_task_projection_repository import _history
        self.selected = self._native_cycle_task()
        with closing(storage.connect(self.target.db_path)) as connection, connection:
            own = connection.execute("SELECT task_event_id,completion_cycle_id FROM task_events "
                "WHERE task_id=? AND completion_cycle_id IS NOT NULL", (self.selected,)).fetchone()
            foreign = connection.execute("SELECT task_event_id,completion_cycle_id FROM task_events "
                "WHERE task_id=? ORDER BY rowid LIMIT 1", (self.other,)).fetchone()
            connection.executemany("""INSERT INTO task_events
                (task_event_id,project_id,task_id,event_type,summary,created_at)
                VALUES(?,?,?,'task_updated','Newer display event','2099-01-01T00:00:00Z')""",
                [(f"tg_event_{0xeeeeeeeeeeeeee00 + index:016x}", self.target.project.project_id,
                  task_id) for index, task_id in enumerate([self.selected] * 11 + [self.other] * 11)])
        self.assertEqual(self.publish().code, "succeeded")
        last_good = self.target.resolved_viewer_path.read_bytes()
        cycle_id = own["completion_cycle_id"]
        cases = (("incoming_text", foreign, cycle_id),
                 ("incoming_blob", foreign, cycle_id.encode()),
                 ("own_blob", own, cycle_id.encode()))
        for label, event, invalid_link in cases:
            with self.subTest(label=label):
                self.mark()
                self._replace_event_cycle(event["task_event_id"], invalid_link)
                with metadata.projection_snapshot(self.target) as (connection, _):
                    task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (self.selected,)).fetchone()
                    with self.assertRaises(storage.StorageError) as rejected:
                        _history(connection, task)
                    self.assertEqual(rejected.exception.code, "completion_history_inconsistent")
                with reject_whole_audits() as audits, mock.patch.object(service,
                        "read_task_projections", wraps=service.read_task_projections) as reader:
                    self.assertEqual(self.publish().code, "failed")
                self.assertEqual(audits, [])
                self.assertEqual(reader.call_args.kwargs["task_ids"], (self.selected,))
                self.assertEqual(self.target.resolved_viewer_path.read_bytes(), last_good)
                self._replace_event_cycle(event["task_event_id"], event["completion_cycle_id"])

    def test_incoming_cycle_alias_query_remains_indexed_with_unrelated_events(self):
        from task_governance_tool.viewer_task_projection_repository import _history
        owner = self._native_cycle_task()
        counts, elapsed, plans = [], [], []
        for size in (100, 10000):
            with closing(storage.connect(self.target.db_path)) as connection, connection:
                connection.executemany("""INSERT INTO task_events
                    (task_event_id,project_id,task_id,event_type,summary,created_at)
                    VALUES(?,?,?,'task_updated','Unrelated history',?)""",
                    [(f"tg_event_{size:08x}{index:08x}", self.target.project.project_id,
                      self.other, FIXED_TIME) for index in range(size)])
            with metadata.projection_snapshot(self.target) as (connection, _):
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (owner,)).fetchone()
                steps, statements = [0], []
                def progress():
                    steps[0] += 1
                    return 0
                connection.set_progress_handler(progress, 1)
                connection.set_trace_callback(statements.append)
                started = time.perf_counter()
                _history(connection, task)
                elapsed.append(time.perf_counter() - started)
                counts.append(steps[0])
                connection.set_progress_handler(None, 0)
                connection.set_trace_callback(None)
                for sql in statements:
                    if "FROM task_events WHERE completion_cycle_id" in sql:
                        plans.extend(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql))
        self.assertLessEqual(counts[1], counts[0] * 1.05 + 100, counts)
        self.assertTrue(any("idx_task_events_completion_cycle" in row for row in plans), plans)
        self.assertFalse(any("SCAN task_events" in row for row in plans), plans)
        print(f"selected_cycle_history vm_steps={counts} elapsed_seconds={elapsed}")


if __name__ == "__main__":
    unittest.main()
