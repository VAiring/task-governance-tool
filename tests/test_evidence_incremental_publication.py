"""Routine publication consumes new Bundle closure and acknowledged membership."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path
from unittest import mock
from time import perf_counter

from tests.test_evidence_projection_capture import make_fixture, _complete_fixture_task
from tests.verification_receipt_test_support import (
    target_for, run_taskgov, seed_current_review_evidence, completion, add_task, add_receipt,
)
from task_governance_tool import storage, evidence_projection as codec
from task_governance_tool import evidence_publication as publication
from task_governance_tool.evidence_publication_baseline import BASELINE_FILENAME


class IncrementalEvidencePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.source_directory.cleanup)
        cls.source = make_fixture(Path(cls.source_directory.name), count=2)

    def target(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        db = directory / "state" / "taskgov.sqlite"
        db.parent.mkdir()
        shutil.copyfile(self.source.db_path, db)
        target = target_for(db, self.source.project.canonical_repo)
        self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
        return target

    def add(self, target, ordinal=99):
        _complete_fixture_task(target.project.canonical_repo, target.db_path, ordinal, large=False)

    def no_global_audit(self):
        return mock.patch.object(storage, "validate_current_schema21_admitted_rows",
                                 side_effect=AssertionError("routine global audit"))

    def test_only_new_bundle_is_built_and_matches_explicit_audit(self):
        target = self.target()
        originals = {p.name: p.read_bytes() for p in target.resolved_evidence_bundles.iterdir()}
        self.add(target)
        with self.no_global_audit(), mock.patch.object(codec, "build_projection_bundle_artifact",
                wraps=codec.build_projection_bundle_artifact) as build:
            result = publication.run_routine_evidence_projection(target)
            self.assertEqual(result.code, "succeeded")
            self.assertEqual(build.call_count, 1)
        for name, raw in originals.items():
            self.assertEqual((target.resolved_evidence_bundles / name).read_bytes(), raw)
        with closing(storage.connect_initialized_readonly(target)) as connection:
            expected = storage.capture_current_evidence_projection(connection, target)
        self.assertEqual(target.resolved_evidence_index.read_bytes(), expected.index.document)
        with self.no_global_audit(), mock.patch.object(codec, "build_projection_bundle_artifact",
                side_effect=AssertionError("unchanged rebuild")):
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "current")

    def test_crash_after_index_before_outcome_revalidates_only_unacknowledged(self):
        target = self.target()
        self.add(target)
        record = publication.record_evidence_projection_outcome
        def fail_success(*args, **kwargs):
            if kwargs["outcome_code"] == "succeeded":
                raise RuntimeError("interruption")
            return record(*args, **kwargs)
        with mock.patch.object(publication, "record_evidence_projection_outcome", side_effect=fail_success):
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
        published = target.resolved_evidence_index.read_bytes()
        with self.no_global_audit(), mock.patch.object(codec, "build_projection_bundle_artifact",
                wraps=codec.build_projection_bundle_artifact) as build:
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "succeeded")
            self.assertEqual(build.call_count, 1)
        self.assertEqual(target.resolved_evidence_index.read_bytes(), published)

    def test_crash_after_outcome_before_baseline_adopts_only_db_acknowledged_index(self):
        target = self.target()
        self.add(target)
        with mock.patch.object(publication, "_save_baseline", side_effect=RuntimeError("interruption")):
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
        with self.no_global_audit(), mock.patch.object(codec, "build_projection_bundle_artifact",
                side_effect=AssertionError("acknowledged rebuild")):
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "succeeded")
        self.assertEqual(publication.run_routine_evidence_projection(target).code, "current")

    def test_replaced_database_requires_explicit_setup_even_for_identical_bytes(self):
        target = self.target()
        original = target.resolved_evidence_index.read_bytes()
        replacement = target.db_path.with_name("replacement.sqlite")
        shutil.copyfile(target.db_path, replacement)
        replacement.replace(target.db_path)
        with self.no_global_audit():
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
        self.assertEqual(target.resolved_evidence_index.read_bytes(), original)
        self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
        self.assertEqual(publication.run_routine_evidence_projection(target).code, "current")

    def test_missing_baseline_never_falls_back_to_whole_history(self):
        target = self.target()
        baseline = target.resolved_evidence_root / BASELINE_FILENAME
        baseline.unlink()
        with self.no_global_audit():
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
        self.assertFalse(baseline.exists())
        self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")

    def test_crash_after_bundle_before_index_keeps_last_good_and_resumes(self):
        target = self.target()
        before = target.resolved_evidence_index.read_bytes()
        self.add(target)
        with mock.patch.object(publication, "_replace_index", side_effect=RuntimeError("interruption")):
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
        self.assertEqual(target.resolved_evidence_index.read_bytes(), before)
        artifacts = {p.name: p.read_bytes() for p in target.resolved_evidence_bundles.iterdir()}
        with self.no_global_audit():
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "succeeded")
        self.assertEqual({p.name: p.read_bytes() for p in target.resolved_evidence_bundles.iterdir()}, artifacts)

    def test_recompletion_selects_new_cycle_and_retains_prior_bytes(self):
        target = self.target()
        old = json.loads(target.resolved_evidence_index.read_bytes())["payload"]["entries"]
        task_id = old[0]["task_id"]
        previous = {p.name: p.read_bytes() for p in target.resolved_evidence_bundles.iterdir()}
        result = run_taskgov("task", "edit", task_id, "--repo", str(target.project.canonical_repo),
            "--db", str(target.db_path), "--status", "in_progress", "--reopen-reason",
            "Exercise incremental recompletion", "--json")
        self.assertEqual(result.returncode, 0, result.stdout)
        seed_current_review_evidence(target.db_path, target.project.canonical_repo, task_id,
                                     fingerprint="sha256:" + "c" * 64)
        result = completion(target.db_path, target.project.canonical_repo, task_id)
        self.assertEqual(result.returncode, 0, result.stdout)
        with self.no_global_audit(), mock.patch.object(codec, "build_projection_bundle_artifact",
                wraps=codec.build_projection_bundle_artifact) as build:
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "succeeded")
            self.assertEqual(build.call_count, 1)
        current = json.loads(target.resolved_evidence_index.read_bytes())["payload"]["entries"]
        self.assertEqual([entry["cycle_ordinal"] for entry in current if entry["task_id"] == task_id], [1, 2])
        for name, raw in previous.items():
            self.assertEqual((target.resolved_evidence_bundles / name).read_bytes(), raw)

    def test_stale_outcome_cannot_overwrite_newer_acknowledgement(self):
        target = self.target()
        before = publication._capture(target, include_basis=False)
        self.add(target)
        self.assertEqual(publication.run_routine_evidence_projection(target).code, "succeeded")
        current = publication._capture(target, include_basis=False)
        raw = target.db_path.read_bytes()
        with self.assertRaises(storage.StorageError):
            publication.record_evidence_projection_outcome(target,
                captured_generation=current.state.source_generation, outcome_code="succeeded",
                recorded_at=storage.utc_now(), index_digest=current.state.index_digest,
                expected_identity=before.identity, expected_state=before.state)
        self.assertEqual(target.db_path.read_bytes(), raw)

    def test_next_generation_after_index_acknowledges_both_without_regression(self):
        target = self.target()
        self.add(target)
        original = publication.record_evidence_projection_outcome
        captured = []
        def record(*args, **kwargs):
            if kwargs["outcome_code"] == "succeeded":
                captured.append(kwargs["captured_generation"])
                if len(captured) == 1:
                    self.add(target, 100)
            return original(*args, **kwargs)
        with mock.patch.object(publication, "record_evidence_projection_outcome", side_effect=record):
            result = publication.run_routine_evidence_projection(target)
        self.assertEqual((result.code, result.publications), ("succeeded", 2))
        self.assertEqual(captured, [3, 4])
        self.assertEqual(publication.inspect_canonical_evidence_status(target), "current")

    def corrupt_bundle(self, target, bundle_id, field, value):
        with closing(storage.connect(target.db_path)) as connection:
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_completion_evidence_bundles_no_update'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_completion_evidence_bundles_no_update")
            connection.execute(f"UPDATE completion_evidence_bundles SET {field}=? WHERE completion_evidence_bundle_id=?", (value, bundle_id))
            connection.execute(sql)
            connection.commit()

    def test_selected_payload_fault_fails_but_published_body_audit_is_explicit(self):
        for selected in (True, False):
            with self.subTest(selected=selected):
                target = self.target()
                old_index = json.loads(target.resolved_evidence_index.read_bytes())["payload"]
                old_id = old_index["entries"][0]["bundle_id"]
                self.add(target)
                with closing(storage.connect(target.db_path)) as connection:
                    ids = {row[0] for row in connection.execute("SELECT completion_evidence_bundle_id FROM completion_evidence_bundles")}
                new_id, = ids - {e["bundle_id"] for e in old_index["entries"]}
                before = target.resolved_evidence_index.read_bytes()
                self.corrupt_bundle(target, new_id if selected else old_id, "payload_size_bytes", 1)
                with self.no_global_audit():
                    result = publication.run_routine_evidence_projection(target)
                self.assertEqual(result.code, "failed" if selected else "succeeded")
                if selected:
                    self.assertEqual(target.resolved_evidence_index.read_bytes(), before)
                self.assertEqual(publication.publish_setup_evidence_projection(target).code, "failed")

    def test_changed_published_digest_rejects_thin_membership_without_body_build(self):
        target = self.target()
        entry = json.loads(target.resolved_evidence_index.read_bytes())["payload"]["entries"][0]
        self.add(target)
        self.corrupt_bundle(target, entry["bundle_id"], "bundle_digest", "sha256:" + "0" * 64)
        before = target.resolved_evidence_index.read_bytes()
        with self.no_global_audit(), mock.patch.object(codec, "build_projection_bundle_artifact",
                side_effect=AssertionError("membership must fail first")):
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
        self.assertEqual(target.resolved_evidence_index.read_bytes(), before)

    def test_invalid_baseline_format_requires_repair_and_preserves_index(self):
        target = self.target()
        path = target.resolved_evidence_root / BASELINE_FILENAME
        original = path.read_bytes()
        before = target.resolved_evidence_index.read_bytes()
        for raw in (original.replace(b'"format_version":1', b'"format_version":true', 1),
                    original.replace(b'"schema_version":34', b'"schema_version":33', 1),
                    b'{"format_version":1,"format_version":1}\n'):
            with self.subTest(raw_length=len(raw)):
                path.write_bytes(raw)
                with self.no_global_audit():
                    self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(target.resolved_evidence_index.read_bytes(), before)
        self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")

    def test_history_growth_does_not_add_sql_or_old_bundle_construction(self):
        measurements = []
        for historical_count in (2, 16):
            target = self.target()
            for ordinal in range(2, historical_count):
                self.add(target, ordinal)
            if historical_count > 2:
                self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
            self.add(target, 100)
            counts = Counter()
            def trace(statement):
                counts["sql"] += 1
                if "FROM task_completion_cycles AS cycle" in statement and "bundle.bundle_digest" in statement:
                    counts["thin_inventory"] += 1
            def observe(opener):
                def opened(*args, **kwargs):
                    connection = opener(*args, **kwargs)
                    connection.set_trace_callback(trace)
                    return connection
                return opened
            with self.no_global_audit(), \
                    mock.patch.object(storage, "connect_readonly", side_effect=observe(storage.connect_readonly)), \
                    mock.patch.object(storage, "connect_existing", side_effect=observe(storage.connect_existing)), \
                    mock.patch.object(codec, "build_projection_bundle_artifact", wraps=codec.build_projection_bundle_artifact) as build:
                started = perf_counter()
                result = publication.run_routine_evidence_projection(target)
                elapsed = perf_counter() - started
                self.assertEqual(result.code, "succeeded")
                self.assertEqual(build.call_count, 1)
            self.assertEqual(counts["thin_inventory"], 1)
            measurements.append(dict(history=historical_count, **counts, new_bundle_builds=1,
                                     old_bundle_builds=0, global_audits=0, seconds=elapsed))
        self.assertEqual(measurements[0]["sql"], measurements[1]["sql"])
        print("Evidence routine measurement: " + json.dumps(measurements, sort_keys=True))

    def test_valid_large_finding_backlog_and_history_keep_per_bundle_limits(self):
        from task_governance_tool import reviews, task_ownership
        from task_governance_tool.session_identity import CallerIdentity
        from task_governance_tool.evidence_validation_repository import _validate_selected_completion_bundle_history
        target = self.target()
        repo, db = target.project.canonical_repo, target.db_path
        task = add_task(db, repo, review_tier=1, verification="",
                        verification_not_required_reason="Fixture-only change")
        task_id = task["task_id"]
        for cycle_number in range(2):
            if cycle_number:
                reopened = run_taskgov("task", "edit", task_id, "--repo", str(repo), "--db", str(db),
                    "--status", "in_progress", "--reopen-reason", "Exercise unpublished backlog", "--json")
                self.assertEqual(reopened.returncode, 0, reopened.stdout)
            generation = seed_current_review_evidence(db, repo, task_id,
                fingerprint="sha256:" + ("c" if cycle_number else "b") * 64)
            with closing(storage.connect_initialized(target, operation_local=True)) as connection:
                receipt_id = connection.execute("SELECT review_receipt_id FROM review_receipts WHERE task_id=? AND target_generation=?",
                    (task_id, generation)).fetchone()[0]
                owner = task_ownership.read_basis(connection, project_id=target.project.project_id, task_id=task_id)
                for ordinal in range(201):
                    reviews.add_review_finding(connection, target.project, task_id,
                        receipt_id=receipt_id, severity="low", summary=f"Fixture observation {cycle_number} {ordinal}",
                        database_target=target, caller=CallerIdentity(owner.owner_session_id))
                    connection.commit()
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)
        with self.no_global_audit(), mock.patch.object(codec, "build_projection_bundle_artifact",
                wraps=codec.build_projection_bundle_artifact) as build:
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "succeeded")
            self.assertEqual(build.call_count, 2)
        # The shared history consumer must accept the same valid combined window.
        with closing(storage.connect_initialized_readonly(target, operation_local=True)) as connection:
            cycles = tuple(storage._cycle_from_row(row) for row in connection.execute(
                "SELECT * FROM task_completion_cycles WHERE task_id=? ORDER BY saved_cycle_ordinal", (task_id,)))
            self.assertEqual(len(cycles), 2)
            _validate_selected_completion_bundle_history(connection, cycles=cycles,
                runner_generations=storage._selected_history_runner_generations(connection, cycles=cycles),
                container_schema_version=storage.SCHEMA_VERSION)
        self.assertEqual(publication.inspect_canonical_evidence_status(target), "current")

    def test_delayed_failed_and_deferred_outcomes_do_not_replace_newer_success(self):
        for code in ("failed", "deferred"):
            for same_generation in (False, True):
                with self.subTest(code=code, same_generation=same_generation):
                    target = self.target()
                    if same_generation:
                        observed = publication._capture(target, include_basis=False)
                        publication._record_failure(target, generation=observed.state.source_generation,
                            code="failed", occurred_at=storage.utc_now(),
                            expected_identity=observed.identity, expected_state=observed.state)
                    else:
                        self.add(target)
                    old = publication._capture(target, include_basis=False)
                    self.assertEqual(publication.run_routine_evidence_projection(target).code, "succeeded")
                    before = target.db_path.read_bytes()
                    publication._record_failure(target, generation=old.state.source_generation,
                        code=code, occurred_at=storage.utc_now(),
                        expected_identity=old.identity, expected_state=old.state)
                    self.assertEqual(target.db_path.read_bytes(), before)
                    self.assertEqual(publication.run_routine_evidence_projection(target).code, "current")

    def test_failed_publication_does_not_record_outcome_into_replacement_database(self):
        target = self.target()
        self.add(target)
        record = publication.record_evidence_projection_outcome
        replacements = []
        def replaced(*args, **kwargs):
            if kwargs["outcome_code"] == "succeeded":
                path = target.db_path.with_name("replacement.sqlite")
                shutil.copyfile(target.db_path, path)
                path.replace(target.db_path)
                replacements.append(target.db_path.read_bytes())
            return record(*args, **kwargs)
        with mock.patch.object(publication, "record_evidence_projection_outcome", side_effect=replaced):
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
        self.assertEqual(len(replacements), 1)
        self.assertEqual(target.db_path.read_bytes(), replacements[0])

    def test_selected_continuation_native_relations_and_capture_inventory_are_required(self):
        from tests.review_answer_fixture_support import review_fixture
        from task_governance_tool.backup_metadata_repository import configure_project_maintenance
        # Missing or changed native relations previously escaped the routine path's
        # pure frozen-graph check. Use real native completions, not mocked gates.
        for table in (None, "collected_valid", "review_reassessment_resolutions", "review_receipt_judgments",
                      "review_receipt_sessions", "review_collected_captures"):
            with self.subTest(table=table), review_fixture(schema_version=34) as fixture:
                target = fixture.target
                configure_project_maintenance(target, requested_interval_minutes=None,
                    requested_generations=None, enabled_at="2026-10-29T00:00:00Z")
                self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
                if table in ("collected_valid", "review_collected_captures"):
                    fixture.register_collected_source()
                else:
                    _, finding, successor = fixture.reassess_with_finding()
                    fixture.resolve(finding, successor=successor)
                verified = add_receipt(fixture.db, fixture.repo, fixture.task_id, fixture.basis["target_generation"])
                self.assertEqual(verified.returncode, 0, verified.stdout)
                completed = completion(fixture.db, fixture.repo, fixture.task_id)
                self.assertEqual(completed.returncode, 0, completed.stdout)
                if table in (None, "collected_valid"):
                    with self.no_global_audit():
                        self.assertEqual(publication.run_routine_evidence_projection(target).code, "succeeded")
                    self.assertEqual(publication.inspect_canonical_evidence_status(target), "current")
                    continue
                before = target.resolved_evidence_index.read_bytes()
                with closing(storage.connect(target.db_path)) as connection:
                    triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
                    for name, _ in triggers:
                        connection.execute(f'DROP TRIGGER "{name}"')
                    if table == "review_receipt_judgments":
                        # Keep its reciprocal foreign key valid: change a frozen
                        # assertion instead of relying on a structural FK error.
                        changed = connection.execute("UPDATE review_receipt_judgments SET continuation_id="
                            "(SELECT continuation_id FROM review_continuations LIMIT 1) WHERE judgment_revision=1")
                    else:
                        changed = connection.execute(f"DELETE FROM {table}")
                    self.assertGreater(changed.rowcount, 0)
                    for _, definition in triggers:
                        connection.execute(definition)
                    connection.commit()
                with self.no_global_audit():
                    self.assertEqual(publication.run_routine_evidence_projection(target).code, "failed")
                self.assertEqual(target.resolved_evidence_index.read_bytes(), before)
                self.assertEqual(publication.publish_setup_evidence_projection(target).code, "failed")


if __name__ == "__main__":
    unittest.main()
