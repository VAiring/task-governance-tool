"""Delta parity and recoverable two-artifact publication on disposable state."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.test_viewer_maintenance import make_target, add_viewer_event, FIXED_TIME
from task_governance_tool import storage, viewer, viewer_maintenance as service
from task_governance_tool import viewer_projection_cache as cache
from task_governance_tool import viewer_metadata_repository as metadata
from task_governance_tool import viewer_task_projection_repository as projection
from task_governance_tool import tasks


class ViewerIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = make_target(self.root, enabled=True)

    def publish(self, *, force=False):
        function = service.publish_setup_viewer if force else service.run_routine_viewer_refresh
        return function(self.target, observed_at=FIXED_TIME)

    def candidate(self):
        return cache.load_candidate(cache.cache_output(self.target))

    def ids(self):
        with closing(storage.connect_initialized_readonly(self.target, operation_local=True)) as c:
            return tuple(row[0] for row in c.execute("SELECT task_id FROM tasks ORDER BY task_id"))

    def edit(self, task_id):
        with closing(storage.connect_initialized(self.target, operation_local=True)) as c:
            tasks.edit_task(c, self.target.project, task_id, title="Changed display",
                            add_note="Changed event", database_target=self.target)
            c.commit()

    def assert_full_parity(self):
        # The retained full-capture oracle intentionally performs global source
        # admission. The new path must give exactly the same public bytes.
        with closing(storage.connect_snapshot_readonly(self.target.db_path)) as c:
            full = viewer.build_viewer_snapshot(c, self.target, generated_at=FIXED_TIME)
        _, _, delta = service._capture(self.target, self.candidate(), FIXED_TIME)
        self.assertEqual(delta, full)
        self.assertEqual(viewer.render_viewer_html(delta.snapshot), viewer.render_viewer_html(full.snapshot))

    def test_first_empty_add_edit_and_reuse_match_full_snapshot(self):
        self.assertEqual(self.publish().code, "succeeded")
        self.assert_full_parity()
        for name in ("First task", "Second task"):
            add_viewer_event(self.target, name)
        self.assertEqual(self.publish().code, "succeeded")
        self.assert_full_parity()
        selected = []
        original = service.read_task_projections
        def read(*args, **kwargs):
            selected.append(kwargs["task_ids"])
            return original(*args, **kwargs)
        subject = self.ids()[0]
        self.edit(subject)
        with mock.patch.object(service, "read_task_projections", side_effect=read):
            self.assertEqual(self.publish().code, "succeeded")
        self.assertEqual(selected, [(subject,)])
        self.assert_full_parity()

    def test_current_reads_no_cache_body_or_task_history(self):
        add_viewer_event(self.target, "Current display")
        self.assertEqual(self.publish().code, "succeeded")
        with (mock.patch.object(service, "load_candidate", side_effect=AssertionError("cache body")),
              mock.patch.object(service, "read_task_projections", side_effect=AssertionError("history"))):
            self.assertEqual(self.publish().code, "current")

    def test_field_specific_titles_and_manual_diagnostics_match_full_capture(self):
        add_viewer_event(self.target, "Initial title")
        self.assertEqual(self.publish().code, "succeeded")
        subject = self.ids()[0]
        for title, description in (
            ("Implement Bearer abcdefghijklmnopqrst handling", "Ordinary description"),
            ("Investigate failure: stderr: permission denied",
             "Inspect result: stdout: no matching rows"),
        ):
            with self.subTest(title=title):
                with closing(storage.connect_initialized(self.target, operation_local=True)) as c, c:
                    tasks.edit_task(c, self.target.project, subject, title=title,
                                    description=description, database_target=self.target)
                self.assertEqual(self.publish().code, "succeeded")
                self.assert_full_parity()
                self.assertEqual(self.candidate().payload["tasks"][0]["title"], title)

    def test_display_privacy_keeps_each_source_field_policy(self):
        from task_governance_tool import viewer_display_validation as validation
        validation._bounded_text_predicate.cache_clear()
        self.addCleanup(validation._bounded_text_predicate.cache_clear)
        # Accepted title prose must never certify a stricter field, either in
        # this attempt or through the bounded memo in a later attempt.
        prose = "Command output: improve formatting"
        for same_attempt in (True, False):
            check = validation.DisplayValidation()
            validation._task_fields["title"](prose, check)
            if not same_attempt:
                check = validation.DisplayValidation()
            strict = (
                (validation._task_fields, ("description", "verification", "tags",
                    "blocked_reason", "pause_reason", "completion_commit_hash",
                    "completion_evidence_revision", "completion_evidence_reason", "review_target_value")),
                (validation._event_fields, ("summary",)),
                (validation._receipt_fields, ("summary", "reviewer_key", "target_value")),
                (validation._finding_fields, ("summary", "resolution_summary", "reviewer_key")),
            )
            for schema, fields in strict:
                for field in fields:
                    with self.subTest(same_attempt=same_attempt, field=field):
                        with self.assertRaises(viewer.ViewerError):
                            schema[field](prose, check)
        for field in ("title", "description"):
            with self.subTest(field=field), self.assertRaises(viewer.ViewerError):
                validation._task_fields[field]("token=synthetic-private-value", validation.DisplayValidation())

    def test_chunked_history_uses_actual_latest_verification_and_checks_old_links(self):
        from dataclasses import replace
        from task_governance_tool import reviews, verification_receipts
        from task_governance_tool.session_identity import CallerIdentity
        caller = CallerIdentity("00000000-0000-4000-8000-000000000001")
        for changing in (False, True):
            with self.subTest(changing=changing):
                with closing(storage.connect_initialized(self.target, operation_local=True)) as c:
                    subject = tasks.add_task(c, self.target.project, title="Chunk boundary history",
                        status="in_progress", review_tier=0, caller=caller,
                        verification="" if changing else "Verify current behavior",
                        verification_not_required_reason="Non-executable initial fixture" if changing else None).task["task_id"]
                    c.commit()
                    for ordinal in range(1, 13):
                        if ordinal > 1:
                            tasks.edit_task(c, self.target.project, subject, caller=caller,
                                status="in_progress", reopen_reason="Next isolated cycle", database_target=self.target)
                        if changing and ordinal == 11:
                            tasks.edit_task(c, self.target.project, subject, caller=caller,
                                verification="Verify changed behavior",
                                database_target=self.target)
                        reviews.set_review_target(c, self.target.project, subject, caller=caller,
                            kind="diff_fingerprint", revision="sha256:" + f"{ordinal:064x}")
                        c.commit()
                        if not changing or ordinal >= 11:
                            generation = c.execute("SELECT review_target_generation FROM tasks WHERE task_id=?", (subject,)).fetchone()[0]
                            verification_receipts.add_verification_receipt(c, self.target.project, subject,
                                caller=caller, result="pass", duration_ms=1, scope_coverage="full",
                                expected_target_generation=generation, database_target=self.target)
                        reviews.add_review_receipt(c, self.target.project, subject, caller=caller,
                            reviewer="Mechanical fixture", kind="not_required", verdict="not_required",
                            summary="Non-executable review fixture")
                        c.commit()
                        request = tasks.build_completion_request(subject, verification_complete=True,
                            review_complete=True, completion_evidence_kind="commit_not_required")
                        basis = tasks.capture_completion_basis(c, self.target.project, subject)
                        plan = tasks.prepare_completion_plan(basis, self.target.project, request)
                        tasks.complete_task(c, self.target.project, plan, caller=caller, database_target=self.target)
                        c.commit()
                        if ordinal >= 10:
                            self.assertEqual(self.publish().code, "succeeded")
                            self.assert_full_parity()

                    # A corrupted verification link in the oldest, now hidden
                    # cycle must still be rejected, not hidden by display LIMIT.
                    if not changing:
                        with closing(storage.connect_snapshot_readonly(self.target.db_path)) as snapshot:
                            row = dict(snapshot.execute("SELECT * FROM tasks WHERE task_id=?", (subject,)).fetchone())
                            first = snapshot.execute("SELECT * FROM task_completion_cycles WHERE task_id=? ORDER BY saved_cycle_ordinal LIMIT 1", (subject,)).fetchone()
                            wrong = replace(storage._cycle_from_row(first), verification_expectation_digest="sha256:" + "f" * 64)
                            with self.assertRaises(storage.StorageError) as rejected:
                                storage._validate_cycle_receipt_links_batch(snapshot,
                                    project_id=self.target.project.project_id, cycles=(wrong,))
                            self.assertEqual(rejected.exception.code, "completion_history_inconsistent")
                            row["verification"] = "Different current requirement"
                            with self.assertRaises(storage.StorageError) as rejected:
                                projection._history(snapshot, row)
                            self.assertEqual(rejected.exception.code, "completion_history_inconsistent")
                    tasks.edit_task(c, self.target.project, subject, caller=caller,
                        status="in_progress", reopen_reason="Reopened history boundary", database_target=self.target)
                    tasks.edit_task(c, self.target.project, subject, caller=caller,
                        verification="Different requirement after reopen",
                        database_target=self.target)
                    c.commit()
                    self.assertEqual(self.publish().code, "succeeded")
                    self.assert_full_parity()
                    # Release the slot for the next independent fixture Task.
                    tasks.edit_task(c, self.target.project, subject, caller=caller,
                        status="ready", database_target=self.target)
                    c.commit()

    def test_changed_task_rejects_outgoing_foreign_cycle_links_beyond_display_window(self):
        self._assert_hidden_event_rejected(own_cycle=False)

    def test_changed_task_with_own_cycle_rejects_foreign_cycle_link(self):
        self._assert_hidden_event_rejected(own_cycle=True)

    def test_foreign_event_project_is_rejected_before_publication(self):
        add_viewer_event(self.target, "Local event owner")
        self.assertEqual(self.publish().code, "succeeded")
        subject = self.ids()[0]
        with closing(storage.connect(self.target.db_path)) as c, c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("""INSERT INTO task_events
                    (task_event_id,project_id,task_id,event_type,summary,created_at)
                    VALUES('tg_event_ffffffffffffff00','foreign-project',?,
                           'task_updated','Foreign project fixture',?)""", (subject, FIXED_TIME))
        self.assertEqual(self.publish().code, "current")

    def _assert_hidden_event_rejected(self, *, own_cycle):
        from task_governance_tool.session_identity import CallerIdentity
        from tests.test_viewer_incremental_scale import extend_native_history
        caller = CallerIdentity("00000000-0000-4000-8000-000000000001")
        with closing(storage.connect_initialized(self.target, operation_local=True)) as c, c:
            owner = tasks.add_task(c, self.target.project, title="Unchanged cycle owner",
                status="in_progress", review_tier=0, caller=caller,
                verification_not_required_reason="Isolated history fixture").task["task_id"]
        extend_native_history(self.target, owner, caller, 1)
        with closing(storage.connect_initialized(self.target, operation_local=True)) as c, c:
            subject = tasks.add_task(c, self.target.project, title="Changed event owner",
                status="in_progress", review_tier=0, caller=caller,
                verification_not_required_reason="Isolated history fixture").task["task_id"]
        if own_cycle:
            extend_native_history(self.target, subject, caller, 1)
        self.assertEqual(self.publish().code, "succeeded")
        last_good = self.target.resolved_viewer_path.read_bytes()
        with closing(storage.connect(self.target.db_path)) as c, c:
            cycle = c.execute("SELECT completion_cycle_id FROM task_completion_cycles WHERE task_id=?",
                              (owner,)).fetchone()[0]
            c.execute("""INSERT INTO task_events
                (task_event_id,project_id,task_id,event_type,summary,created_at,completion_cycle_id)
                VALUES('tg_event_ffffffffffffff00',?,?,'task_updated','Foreign cycle fixture',?,?)""",
                (self.target.project.project_id, subject, FIXED_TIME, cycle))
            c.executemany("""INSERT INTO task_events
                (task_event_id,project_id,task_id,event_type,summary,created_at)
                VALUES(?,?,?,'task_updated','Newer unrelated event',?)""",
                [(f"tg_event_{0xffffffffffffff10 + index:016x}", self.target.project.project_id,
                  subject, "2099-01-01T00:00:00Z") for index in range(11)])
        # Only B changed; the cached A remains valid. The bad relation is older
        # than every displayed event, with or without B having its own cycle.
        with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
            self.assertEqual(self.publish().code, "failed")
        self.assertEqual(reader.call_args.kwargs["task_ids"], (subject,))
        self.assertEqual(self.target.resolved_viewer_path.read_bytes(), last_good)
        self.assertEqual(self.publish(force=True).code, "failed")
        self.assertEqual(self.target.resolved_viewer_path.read_bytes(), last_good)

    def test_checkpoint_review_completion_and_reopen_publish_only_the_changed_task(self):
        from task_governance_tool import reviews, checkpoints
        from task_governance_tool.session_identity import CallerIdentity
        caller = CallerIdentity("00000000-0000-4000-8000-000000000001")
        self.assertEqual(self.publish().code, "succeeded")
        with closing(storage.connect_initialized(self.target)) as c:
            subject = tasks.add_task(c, self.target.project, title="Lifecycle display",
                status="in_progress", review_tier=0, caller=caller,
                verification_not_required_reason="Isolated non-executable fixture").task["task_id"]
            c.commit()

        def check():
            with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
                self.assertEqual(self.publish().code, "succeeded")
            self.assertEqual(reader.call_args.kwargs["task_ids"], (subject,))
            self.assert_full_parity()

        check()
        with closing(storage.connect_initialized(self.target, operation_local=True)) as c:
            checkpoints.record_checkpoint(c, self.target.project, subject, caller=caller,
                summary="Isolated checkpoint", next_action="Continue verification", database_target=self.target)
            c.commit()
        check()
        with closing(storage.connect_initialized(self.target, operation_local=True)) as c:
            reviews.set_review_target(c, self.target.project, subject, caller=caller,
                kind="diff_fingerprint", revision="sha256:" + "a" * 64)
            c.commit()
        check()
        with closing(storage.connect_initialized(self.target, operation_local=True)) as c:
            reviews.add_review_receipt(c, self.target.project, subject, caller=caller,
                reviewer="Mechanical fixture", kind="not_required", verdict="not_required",
                summary="Non-executable fixture")
            c.commit()
        check()
        with closing(storage.connect_initialized(self.target, operation_local=True)) as c:
            request = tasks.build_completion_request(subject, verification_complete=True,
                review_complete=True, completion_evidence_kind="commit_not_required")
            basis = tasks.capture_completion_basis(c, self.target.project, subject)
            plan = tasks.prepare_completion_plan(basis, self.target.project, request)
            tasks.complete_task(c, self.target.project, plan, caller=caller, database_target=self.target)
            c.commit()
        check()
        with closing(storage.connect_initialized(self.target, operation_local=True)) as c:
            tasks.edit_task(c, self.target.project, subject, caller=caller, status="in_progress",
                reopen_reason="Isolated reopen", database_target=self.target)
            c.commit()
        check()

    def test_a_b_a_binding_rebuilds_despite_original_path_return(self):
        from tests.test_project_identity_bindings import rebound_target, apply_rebind
        from task_governance_tool.project_binding_repository import compare_and_swap_project_binding
        add_viewer_event(self.target, "Rebound display")
        self.assertEqual(self.publish().code, "succeeded")
        original = self.target
        first_cache = self.candidate()
        destination = rebound_target(original)
        apply_rebind(original, destination)
        for target in (destination, original):
            if target is original:
                compare_and_swap_project_binding(original, project_id=original.project.project_id,
                    identity_scheme="legacy_path_v1", expected_generation=2,
                    expected_old_hash=destination.project.canonical_path_hash,
                    new_hash=original.project.canonical_path_hash,
                    new_display_name=original.project.display_name, reason="confirmed_relocation",
                    confirmation_token_digest="e" * 64, bound_at="2026-10-28T00:00:00Z")
            self.target = target
            with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
                self.assertEqual(self.publish().code, "succeeded")
            self.assertIsNone(reader.call_args.kwargs["task_ids"])
            self.assert_full_parity()
        self.assertNotEqual(first_cache.payload["binding_generation"], self.candidate().payload["binding_generation"])

    def test_missing_html_reuses_cache_without_task_reads(self):
        add_viewer_event(self.target, "Retained display")
        self.assertEqual(self.publish().code, "succeeded")
        self.target.resolved_viewer_path.unlink()
        with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
            self.assertEqual(self.publish().code, "succeeded")
        self.assertEqual(reader.call_args.kwargs["task_ids"], ())
        self.assert_full_parity()

    def test_missing_or_corrupt_cache_requires_full_rebuild(self):
        add_viewer_event(self.target, "Rebuild display")
        self.assertEqual(self.publish().code, "succeeded")
        for bad in (None, b"{broken", b"{}", b'{"tasks":[],"tasks":[]}'):
            with self.subTest(bad=bad):
                path = self.target.resolved_viewer_projection_path
                if bad is None:
                    path.unlink()
                else:
                    path.write_bytes(bad)
                    self.edit(self.ids()[0])
                with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
                    self.assertEqual(self.publish().code, "succeeded")
                self.assertIsNone(reader.call_args.kwargs["task_ids"])
                self.assert_full_parity()

    def test_cache_shape_types_privacy_and_order_are_closed(self):
        add_viewer_event(self.target, "First cache row")
        add_viewer_event(self.target, "Second cache row")
        self.assertEqual(self.publish().code, "succeeded")
        original = self.candidate().payload
        mutations = [
            lambda p: p.update(extra="unowned"),
            lambda p: p.update(applied_generation=True),
            lambda p: p["database_file_identity"].update(inode="123"),
            lambda p: p["tasks"][0].update(extra="unowned"),
            lambda p: p["tasks"][0].update(title="token=synthetic-private-value"),
            lambda p: p["tasks"][0].update(review_tier=True),
            lambda p: p["tasks"][0]["completion_history"].update(returned_count=1),
            lambda p: p["tasks"].reverse(),
            lambda p: p["tasks"].append(p["tasks"][0]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                payload = deepcopy(original)
                mutate(payload)
                self.target.resolved_viewer_projection_path.write_bytes(cache.canonical_bytes(payload))
                self.assertIsNone(self.candidate())

    def test_attempt_local_predicates_never_waive_field_limits_or_changed_values(self):
        from task_governance_tool.viewer_display_validation import DisplayValidation
        add_viewer_event(self.target, "Predicate scope")
        self.assertEqual(self.publish().code, "succeeded")
        candidate = self.candidate()
        rows = deepcopy(candidate.tasks)
        rows[0]["description"] = "Bounded display text " * 20
        validation = DisplayValidation()
        cache.encode_cache(candidate.header, rows, validation=validation)
        for field, value in (("title", rows[0]["description"]),
                             ("description", "token=synthetic-private-value"),
                             ("updated_at", "invalid timestamp")):
            with self.subTest(field=field):
                changed = deepcopy(rows)
                changed[0][field] = value
                with self.assertRaises(viewer.ViewerError):
                    cache.encode_cache(candidate.header, changed, validation=validation)

    def test_successive_refreshes_do_not_share_display_validation_state(self):
        from task_governance_tool.viewer_display_validation import DisplayValidation
        seen = []
        def new_validation():
            value = DisplayValidation()
            seen.append(value)
            return value
        add_viewer_event(self.target, "First attempt")
        with mock.patch.object(service, "DisplayValidation", side_effect=new_validation):
            self.assertEqual(self.publish().code, "succeeded")
            self.edit(self.ids()[0])
            self.assertEqual(self.publish().code, "succeeded")
        self.assertEqual(len(seen), 2)
        self.assertIsNot(seen[0], seen[1])

    def test_text_memo_is_bounded_pure_and_never_retains_rejections(self):
        from task_governance_tool import viewer_display_validation as validation
        from task_governance_tool.task_values import TaskValidationError
        validation._bounded_text_predicate.cache_clear()
        with mock.patch.object(validation, "reject_private_or_raw_content",
                               wraps=validation.reject_private_or_raw_content) as predicate:
            validation.DisplayValidation().text("title", "Approved bounded display")
            validation.DisplayValidation().text("title", "Approved bounded display")
            self.assertEqual(predicate.call_count, 1)
            long_value = "Display text " * 100
            validation.DisplayValidation().text("description", long_value)
            validation.DisplayValidation().text("description", long_value)
            self.assertEqual(predicate.call_count, 3)
            for _ in range(2):
                with self.assertRaises(TaskValidationError):
                    validation.DisplayValidation().text("description", "token=synthetic-private-value")
            self.assertEqual(predicate.call_count, 5)
            info = validation._bounded_text_predicate.cache_info()
            self.assertEqual((info.maxsize, info.currsize), (16384, 1))
        with mock.patch.object(validation, "reject_private_or_raw_content",
                               side_effect=ValueError("changed predicate")) as changed:
            with self.assertRaises(ValueError):
                validation.DisplayValidation().text("title", "Approved bounded display")
            changed.assert_called_once()
        validation._bounded_text_predicate.cache_clear()

    def test_html_failure_retains_last_good_and_accepted_cache_remains_due(self):
        add_viewer_event(self.target, "Before failure")
        self.assertEqual(self.publish().code, "succeeded")
        old = self.target.resolved_viewer_path.read_bytes()
        self.edit(self.ids()[0])
        with mock.patch.object(service, "write_viewer_html", side_effect=OSError("isolated failure")):
            self.assertEqual(self.publish().code, "failed")
        state = metadata.read_projection_basis(self.target)
        self.assertEqual(state.state.cache_generation, state.state.source_generation)
        self.assertIsNone(state.state.html_generation)
        self.assertIsNone(state.viewer.rendered_generation)
        self.assertEqual(self.target.resolved_viewer_path.read_bytes(), old)
        with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
            self.assertEqual(self.publish().code, "succeeded")
        self.assertEqual(reader.call_args.kwargs["task_ids"], ())

    def test_orphan_cache_after_metadata_failure_is_not_adopted(self):
        add_viewer_event(self.target, "Before orphan")
        self.assertEqual(self.publish().code, "succeeded")
        old = self.target.resolved_viewer_path.read_bytes()
        self.edit(self.ids()[0])
        with mock.patch.object(service, "accept_projection_cache", side_effect=RuntimeError("unknown outcome")) as writer:
            self.assertEqual(self.publish().code, "failed")
        writer.assert_called_once()
        self.assertEqual(self.target.resolved_viewer_path.read_bytes(), old)
        with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
            self.assertEqual(self.publish().code, "succeeded")
        self.assertIsNone(reader.call_args.kwargs["task_ids"])

    def test_force_interruption_invalidates_tuples_before_any_artifact_write(self):
        add_viewer_event(self.target, "Before force")
        self.assertEqual(self.publish().code, "succeeded")
        old = self.target.resolved_viewer_path.read_bytes()
        with mock.patch.object(service, "load_candidate", side_effect=SystemExit("isolated exit")):
            with self.assertRaises(SystemExit):
                self.publish(force=True)
        state = metadata.read_projection_basis(self.target)
        self.assertIsNone(state.state.cache_generation)
        self.assertIsNone(state.state.html_generation)
        self.assertIsNone(state.viewer.rendered_generation)
        self.assertEqual(self.target.resolved_viewer_path.read_bytes(), old)
        self.assertEqual(self.publish().code, "succeeded")

    def test_new_revision_after_cache_acceptance_is_not_lost(self):
        add_viewer_event(self.target, "Concurrent display")
        self.assertEqual(self.publish().code, "succeeded")
        subject = self.ids()[0]
        self.edit(subject)
        original = service.accept_projection_cache
        invoked = 0
        def accept(*args):
            nonlocal invoked
            value = original(*args)
            invoked += 1
            if invoked == 1:
                self.edit(subject)
            return value
        with mock.patch.object(service, "accept_projection_cache", side_effect=accept):
            self.assertEqual(self.publish().code, "succeeded")
        self.assertEqual(invoked, 2)
        state = metadata.read_projection_basis(self.target)
        self.assertEqual(state.state.html_generation, state.state.source_generation)
        self.assert_full_parity()

    def test_cache_hard_link_is_rejected_without_deleting_either_file(self):
        add_viewer_event(self.target, "Safe display")
        self.assertEqual(self.publish().code, "succeeded")
        path = self.target.resolved_viewer_projection_path
        linked = self.root / "linked.json"
        os.link(path, linked)
        old = path.read_bytes()
        self.edit(self.ids()[0])
        self.assertEqual(self.publish().code, "failed")
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(linked.read_bytes(), old)

    def test_valid_cache_with_wrong_independent_binding_or_digest_rebuilds(self):
        add_viewer_event(self.target, "Bound cache")
        self.assertEqual(self.publish().code, "succeeded")
        mutations = [
            lambda p: p.update(database_epoch="f" * 32),
            lambda p: p.update(binding_generation=p["binding_generation"] + 1),
            lambda p: p["database_file_identity"].update(inode=p["database_file_identity"]["inode"] + 1),
            lambda p: p.update(applied_generation=p["applied_generation"] + 1),
            lambda p: p["tasks"][0].update(title="Unaccepted changed display"),
            lambda p: p.update(projection_version=2),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                payload = deepcopy(self.candidate().payload)
                mutate(payload)
                self.target.resolved_viewer_projection_path.write_bytes(cache.canonical_bytes(payload))
                self.edit(self.ids()[0])
                with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
                    self.assertEqual(self.publish().code, "succeeded")
                self.assertIsNone(reader.call_args.kwargs["task_ids"])
                self.assert_full_parity()

    def test_unknown_cache_or_html_commit_is_not_replayed_and_next_attempt_recovers(self):
        add_viewer_event(self.target, "Uncertain publication")
        self.assertEqual(self.publish().code, "succeeded")
        for name in ("accept_projection_cache", "accept_projection_html"):
            with self.subTest(stage=name):
                self.edit(self.ids()[0])
                original = getattr(service, name)
                def committed_but_unknown(*args, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("Injected lost commit response")
                with mock.patch.object(service, name, side_effect=committed_but_unknown) as writer:
                    self.assertEqual(self.publish().code, "failed")
                writer.assert_called_once()
                # The next opportunity reads actual committed metadata. An
                # already committed HTML success may still be current (the
                # same-second outcome rule cannot overwrite that success).
                basis = metadata.read_projection_basis(self.target)
                expected = "succeeded" if basis.viewer.due else "current"
                if expected == "current":
                    self.assertEqual(basis.state.cache_generation, basis.state.source_generation)
                    self.assertEqual(basis.state.html_generation, basis.state.source_generation)
                with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
                    self.assertEqual(self.publish().code, expected)
                if expected == "current":
                    reader.assert_not_called()
                else:
                    self.assertEqual(reader.call_args.kwargs["task_ids"], ())
                self.assert_full_parity()

    def test_post_replacement_identity_failure_never_replays_the_operation(self):
        from contextlib import contextmanager
        add_viewer_event(self.target, "Guard continuity")
        self.assertEqual(self.publish().code, "succeeded")
        self.edit(self.ids()[0])
        original = metadata.projection_snapshot
        @contextmanager
        def changed_after_yield(*args):
            with original(*args) as value:
                yield value
                raise metadata.ViewerBasisChanged()
        original_guard = service.projection_replace_guard
        def guard(*args, **kwargs):
            @contextmanager
            def injected():
                with mock.patch.object(metadata, "projection_snapshot", side_effect=changed_after_yield):
                    with original_guard(*args, **kwargs):
                        yield
            return injected()
        with mock.patch.object(service, "projection_replace_guard", side_effect=guard), \
             mock.patch.object(cache.os, "replace", wraps=cache.os.replace) as replace:
            self.assertEqual(self.publish().code, "failed")
        replace.assert_called_once()
        self.assertEqual(self.publish().code, "succeeded")

    def test_cache_capacity_and_partial_flush_failure_preserve_last_good(self):
        from task_governance_tool import state_paths
        add_viewer_event(self.target, "Durable artifact")
        self.assertEqual(self.publish().code, "succeeded")
        old = self.target.resolved_viewer_path.read_bytes()
        self.edit(self.ids()[0])
        with mock.patch.object(cache, "MAX_BYTES", 1):
            self.assertEqual(self.publish().code, "failed")
        self.assertEqual(self.target.resolved_viewer_path.read_bytes(), old)
        with mock.patch.object(state_paths.os, "fsync", side_effect=OSError("Injected flush failure")):
            self.assertEqual(self.publish().code, "failed")
        self.assertEqual(self.target.resolved_viewer_path.read_bytes(), old)
        self.assertEqual(list(self.target.resolved_viewer_path.parent.glob("*.tmp")), [])
        self.assertEqual(self.publish().code, "succeeded")

    def test_invalidation_during_publication_rejects_old_basis_then_rebuilds(self):
        from task_governance_tool.viewer_projection_repository import invalidate_locked
        add_viewer_event(self.target, "Incarnation reset")
        self.assertEqual(self.publish().code, "succeeded")
        before = metadata.read_projection_basis(self.target)
        self.edit(self.ids()[0])
        original = service.accept_projection_cache
        def reset_then_accept(*args):
            with closing(storage.connect_initialized(self.target, operation_local=True)) as c:
                storage.begin_initialized_write(c, self.target, operation_local=True)
                invalidate_locked(c, self.target.project.project_id, rotate_epoch=True)
                c.commit()
            return original(*args)
        with mock.patch.object(service, "accept_projection_cache", side_effect=reset_then_accept) as writer:
            self.assertEqual(self.publish().code, "failed")
        writer.assert_called_once()
        with mock.patch.object(service, "read_task_projections", wraps=service.read_task_projections) as reader:
            self.assertEqual(self.publish().code, "succeeded")
        self.assertIsNone(reader.call_args.kwargs["task_ids"])
        self.assertNotEqual(before.state.database_epoch, metadata.read_projection_basis(self.target).state.database_epoch)
        self.assert_full_parity()


class ViewerIncrementalReviewTests(unittest.TestCase):
    def test_public_setup_detects_and_repairs_missing_corrupt_or_orphan_cache(self):
        from tests.m14_test_support import make_physical_install
        from tests.test_criterion_link_collector_migration import PublicCriterionLinkCollectorSetupTests
        self.install = make_physical_install(Path(self.enterContext(tempfile.TemporaryDirectory())))
        cli = lambda *args: PublicCriterionLinkCollectorSetupTests.cli(self, *args)
        cli("setup")
        path = self.install.target.resolved_viewer_projection_path
        for mutation in ("missing", "malformed", "unmatched"):
            with self.subTest(mutation=mutation):
                last_good = self.install.viewer_path.read_bytes()
                if mutation == "missing":
                    path.unlink()
                elif mutation == "malformed":
                    path.write_bytes(b"{invalid")
                else:
                    value = json.loads(path.read_bytes())
                    value["database_epoch"] = "0" * 32
                    path.write_bytes(cache.canonical_bytes(value))
                before = self.install.state_snapshot()
                preview = cli("setup", "--read-only")
                self.assertEqual(preview["viewer_status"], "repair_required")
                self.assertIn("viewer_publish", preview["planned_writes"])
                self.assertEqual(self.install.state_snapshot(), before)
                self.assertEqual(self.install.viewer_path.read_bytes(), last_good)
                repaired = cli("setup")
                self.assertIn("viewer_publish", repaired["completed_writes"])
                self.assertEqual(cli("setup", "--read-only")["viewer_status"], "current")
                self.assertEqual(cli("setup")["completed_writes"], [])

    def test_finding_seek_domains_cannot_hide_corrupt_selected_rows(self):
        from tests.test_viewer_review_inventory import seed_reviews
        for column, value in (("status", "unknown"), ("severity", "unknown"),
                              ("summary", b"undecodable stored text")):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as tmp:
                target = make_target(Path(tmp), enabled=True)
                selected = seed_reviews(target)[0]
                self.assertEqual(service.publish_setup_viewer(target, observed_at=FIXED_TIME).code, "succeeded")
                old = target.resolved_viewer_path.read_bytes()
                add_viewer_event(target, "Eligible publication")
                with closing(storage.connect(target.db_path)) as c:
                    c.execute("PRAGMA ignore_check_constraints=ON")
                    c.execute(f"UPDATE review_findings SET {column}=? WHERE review_receipt_id IN "
                              "(SELECT review_receipt_id FROM review_receipts WHERE task_id=?)", (value, selected))
                    c.commit()
                self.assertEqual(service.publish_setup_viewer(target, observed_at=FIXED_TIME).code, "failed")
                self.assertEqual(target.resolved_viewer_path.read_bytes(), old)

    def test_collected_review_continuation_and_completion_match_full(self):
        from tests.review_answer_fixture_support import review_fixture
        from task_governance_tool.backup_metadata_repository import configure_project_maintenance
        with review_fixture(schema_version=34) as fixture:
            fixture.collected_source_artifact()
            configure_project_maintenance(fixture.target, requested_interval_minutes=None,
                                          requested_generations=None, enabled_at=FIXED_TIME)
            result = service.publish_setup_viewer(fixture.target, observed_at=FIXED_TIME)
            self.assertEqual(result.code, "succeeded")
            candidate = cache.load_candidate(cache.cache_output(fixture.target))
            with closing(storage.connect_snapshot_readonly(fixture.target.db_path)) as c:
                full = viewer.build_viewer_snapshot(c, fixture.target, generated_at=FIXED_TIME)
            _, _, delta = service._capture(fixture.target, candidate, FIXED_TIME)
            self.assertEqual(delta, full)


if __name__ == "__main__":
    unittest.main()
