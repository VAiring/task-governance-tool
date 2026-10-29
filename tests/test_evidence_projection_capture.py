"""Whole-source publication capture: reuse, rejection and resource lifetimes."""
from __future__ import annotations

import gc
import json
import shutil
import sqlite3
import tempfile
import unittest
import weakref
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

from tests.verification_receipt_test_support import (
    add_task, completion, initialize, run_taskgov, seed_current_review_evidence,
    target_for,
)
from task_governance_tool import evidence_projection as codec
from task_governance_tool import evidence_publication as publication
from task_governance_tool import evidence_validation_repository as repository
from task_governance_tool import evidence_incremental_repository as incremental
from task_governance_tool import storage
from task_governance_tool import verification_runner_repository as runner_repository
from task_governance_tool.backup_metadata_repository import configure_project_maintenance


def make_fixture(root, *, count=3, large=False, old_first=False):
    """Native completions only; optionally preserve an actual source-29 Bundle."""
    from tests.test_review_session_migration import candidate_runtime
    from contextlib import nullcontext
    with candidate_runtime(29) if old_first else nullcontext():
        repo, db = initialize(root)
        _complete_fixture_task(repo, db, 0, large=large)
    if old_first:
        with closing(storage.connect(db)) as connection:
            storage.apply_migrations(connection)
    for index in range(1, count):
        _complete_fixture_task(repo, db, index, large=large)
    target = target_for(db, repo)
    configure_project_maintenance(
        target, requested_interval_minutes=None, requested_generations=None,
        enabled_at="2026-10-25T00:00:00Z",
    )
    return target


def _complete_fixture_task(repo, db, index, *, large):
    task = add_task(db, repo, title=f"Projection capture fixture {index}",
                    status="ready" if large else "in_progress",
                    verification="", verification_not_required_reason="No executable fixture change")
    if large:
        result = run_taskgov("task", "edit", task["task_id"], "--repo", str(repo),
            "--status", "in_progress",
            "--db", str(db), "--contract-scope", "Scope fixture. " * 60,
            "--contract-acceptance", "Acceptance fixture. " * 45,
            "--contract-constraints", "Constraint fixture. " * 45,
            "--contract-authority-ref", "test:projection-capacity", "--json")
        if result.returncode:
            raise AssertionError(result.stdout)
    seed_current_review_evidence(db, repo, task["task_id"])
    result = completion(db, repo, task["task_id"])
    if result.returncode:
        raise AssertionError(result.stdout)


class EvidenceProjectionCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.source = make_fixture(Path(cls.directory.name), count=3, old_first=True)

    def copy_target(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        db = directory / "state" / "taskgov.sqlite"
        db.parent.mkdir()
        shutil.copyfile(self.source.db_path, db)
        return target_for(db, self.source.project.canonical_repo)

    def assert_closed(self, connections):
        self.assertTrue(connections)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_capture_once_per_bundle_matches_standalone_and_all_consumers(self):
        target = self.copy_target()
        before = target.db_path.read_bytes()
        with closing(storage.connect_initialized_readonly(target)) as connection:
            basis = repository.capture_evidence_projection_basis(
                connection, project_id=target.project.project_id)
            expected = codec._render_projection(basis)
        self.assertEqual({r.bundle.source_schema_version for r in basis.native_bundles}, {29, storage.SCHEMA_VERSION})
        self.assertEqual({r.bundle.bundle_version for r in basis.native_bundles}, {2, 4})
        del basis
        builder = codec.build_projection_bundle_artifact
        for only_due in (True, False):
            with self.subTest(only_due=only_due), mock.patch.object(
                codec, "build_projection_bundle_artifact", wraps=builder,
            ) as observed:
                captured = publication._capture(target, include_basis=True, only_when_due=only_due)
                self.assertEqual(captured.rendered, expected)
                self.assertEqual(observed.call_count, 3)
                self.assertEqual(set(vars(captured.rendered.index)), {"document", "index_digest"})
                for _, item in captured.rendered.bundles:
                    self.assertEqual(set(vars(item)), {"document", "bundle_digest", "file_digest"})
        with mock.patch.object(codec, "build_projection_bundle_artifact", wraps=builder) as observed:
            self.assertEqual(publication.inspect_canonical_evidence_status(target), "not_present")
            self.assertEqual(observed.call_count, 3)
        self.assertEqual(target.db_path.read_bytes(), before)

    def test_publication_releases_raw_basis_artifacts_and_reader_before_file_io(self):
        target = self.copy_target()
        connections, bases, artifacts, runner_graphs = [], [], [], []
        open_read = storage.connect_readonly
        capture_rows = repository._capture_evidence_projection_basis_rows
        build = codec.build_projection_bundle_artifact
        admit_graph = storage.validate_current_schema21_admitted_rows
        physical = publication._temporary_file
        replace_file = publication._atomic_replace_temporary
        physical_calls, guarded = [], []

        def opened(*args, **kwargs):
            connection = open_read(*args, **kwargs)
            connections.append(connection)
            return connection

        def rows(*args, **kwargs):
            result = capture_rows(*args, **kwargs)
            bases.append(weakref.ref(result))
            return result

        def built(*args):
            self.assertTrue(all(ref() is None for ref in artifacts))
            result = build(*args)
            artifacts.append(weakref.ref(result))
            return result

        def admitted_graph(*args):
            result = admit_graph(*args)
            runner_graphs.append(weakref.ref(result))
            return result

        def write(*args, **kwargs):
            gc.collect()
            self.assertTrue(bases)
            self.assertTrue(runner_graphs)
            self.assertTrue(all(ref() is None for ref in bases + artifacts + runner_graphs))
            self.assert_closed(connections)
            physical_calls.append(kwargs["prefix"])
            return physical(*args, **kwargs)

        def replace(*args, **kwargs):
            if args[1] == target.resolved_evidence_index:
                self.assert_closed(connections[:-1])
                current = connections[-1]
                self.assertTrue(current.in_transaction)
                self.assertEqual(current.execute("PRAGMA query_only").fetchone()[0], 1)
                guarded.append(current)
            return replace_file(*args, **kwargs)

        with mock.patch.object(storage, "connect_readonly", side_effect=opened), \
             mock.patch.object(storage, "validate_current_schema21_admitted_rows", side_effect=admitted_graph), \
             mock.patch.object(repository, "_capture_evidence_projection_basis_rows", side_effect=rows), \
             mock.patch.object(codec, "build_projection_bundle_artifact", side_effect=built), \
             mock.patch.object(publication, "_temporary_file", side_effect=write), \
             mock.patch.object(publication, "_atomic_replace_temporary", side_effect=replace):
            result = publication.publish_setup_evidence_projection(target)
        self.assertEqual(result.code, "succeeded")
        self.assertEqual(len(physical_calls), 5)
        self.assertEqual(physical_calls[-2], publication._INDEX_TEMP_PREFIX)
        self.assertEqual(physical_calls[-1], publication.BASELINE_TEMP_PREFIX)
        self.assertEqual(len(guarded), 1)
        self.assert_closed(connections)
        self.assertEqual(list(target.resolved_evidence_root.rglob("*.tmp")), [])

    def test_tail_corruption_and_index_limits_reject_before_any_bundle_publication(self):
        for fault in ("digest", "size", "format", "index_bytes", "index_entries"):
            with self.subTest(fault=fault):
                target = self.copy_target()
                self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
                last_good = target.resolved_evidence_index.read_bytes()
                originals = {p.name: p.read_bytes() for p in target.resolved_evidence_bundles.iterdir()}
                if fault in {"digest", "size", "format"}:
                    with closing(storage.connect(target.db_path)) as connection:
                        last_id = connection.execute("SELECT completion_evidence_bundle_id FROM completion_evidence_bundles ORDER BY task_id COLLATE BINARY, cycle_ordinal, completion_cycle_id COLLATE BINARY").fetchall()[-1][0]
                        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_completion_evidence_bundles_no_update'").fetchone()[0]
                        connection.execute("DROP TRIGGER trg_completion_evidence_bundles_no_update")
                        field, value = {"digest": ("bundle_digest", "sha256:" + "0" * 64),
                            "size": ("payload_size_bytes", 1), "format": ("bundle_version", 1)}[fault]
                        connection.execute("PRAGMA ignore_check_constraints=ON")
                        connection.execute(f"UPDATE completion_evidence_bundles SET {field}=? WHERE completion_evidence_bundle_id=?", (value, last_id))
                        connection.execute("PRAGMA ignore_check_constraints=OFF")
                        connection.execute(trigger)
                        connection.commit()
                from contextlib import nullcontext
                limit = (mock.patch.object(codec, "INDEX_MAX_BYTES", len(last_good)-1)
                    if fault == "index_bytes" else mock.patch.object(codec, "INDEX_MAX_ENTRIES", 2)
                    if fault == "index_entries" else nullcontext())
                connections = []
                original = storage.connect_readonly
                def opened(*args, **kwargs):
                    conn = original(*args, **kwargs)
                    connections.append(conn)
                    return conn
                with limit, mock.patch.object(storage, "connect_readonly", side_effect=opened), \
                     mock.patch.object(publication, "_publish_immutable_bundle", wraps=publication._publish_immutable_bundle) as publish:
                    result = publication.publish_setup_evidence_projection(target)
                self.assertEqual(result.code, "failed")
                publish.assert_not_called()
                self.assert_closed(connections)
                self.assertEqual(target.resolved_evidence_index.read_bytes(), last_good)
                self.assertEqual({p.name: p.read_bytes() for p in target.resolved_evidence_bundles.iterdir()}, originals)
                self.assertEqual(list(target.resolved_evidence_root.rglob("*.tmp")), [])

    def test_generation_drift_rebuilds_independently_at_most_twice(self):
        for repeat in (False, True):
            with self.subTest(repeat=repeat):
                target = self.copy_target()
                self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
                last_good = target.resolved_evidence_index.read_bytes()
                _complete_fixture_task(target.project.canonical_repo, target.db_path, 3, large=False)
                capture = incremental.capture_incremental_evidence_projection
                replace_index = publication._replace_index
                retained, counts = [], []
                attempts = []

                def captured(*args, **kwargs):
                    gc.collect()
                    self.assertTrue(all(ref() is None for ref in retained))
                    result = capture(*args, **kwargs)
                    counts.append(len(result.bundles))
                    retained.append(weakref.ref(result))
                    retained.extend(weakref.ref(item) for _, item in result.bundles)
                    return result

                def replace(*args, **kwargs):
                    attempts.append(kwargs["captured_generation"])
                    if repeat or len(attempts) == 1:
                        # A real source commit after the initial capture reader
                        # closes. The fresh control guard must reject its index.
                        _complete_fixture_task(target.project.canonical_repo,
                            target.db_path, 3 + len(attempts), large=False)
                    return replace_index(*args, **kwargs)

                with mock.patch.object(incremental, "capture_incremental_evidence_projection", side_effect=captured), \
                     mock.patch.object(publication, "_replace_index", side_effect=replace):
                    result = publication.run_routine_evidence_projection(target)
                self.assertEqual(counts, [1, 2])
                self.assertEqual(len(attempts), 2)
                self.assertEqual(result.code, "failed" if repeat else "succeeded")
                self.assertEqual(result.publications, 0 if repeat else 1)
                if repeat:
                    self.assertEqual(target.resolved_evidence_index.read_bytes(), last_good)
                else:
                    from tests.evidence_reader_oracle import read_evidence_index, validate_evidence_source
                    index = read_evidence_index(target.resolved_evidence_root)
                    self.assertEqual(len(index.entries), 5)
                    for entry in index.entries:
                        validate_evidence_source(index, entry)
                self.assertEqual(list(target.resolved_evidence_root.rglob("*.tmp")), [])

    def test_current_and_disabled_skip_building_but_force_and_status_validate(self):
        from dataclasses import replace
        target = self.copy_target()
        self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
        build = codec.build_projection_bundle_artifact
        with mock.patch.object(codec, "build_projection_bundle_artifact", wraps=build) as observed:
            self.assertEqual(publication.run_routine_evidence_projection(target).code, "current")
            observed.assert_not_called()
            read = publication.read_project_maintenance
            with mock.patch.object(publication, "read_project_maintenance",
                side_effect=lambda *args: replace(read(*args), enabled_at=None)):
                self.assertEqual(publication.run_routine_evidence_projection(target).code, "not_opted_in")
                observed.assert_not_called()
            self.assertEqual(publication.inspect_canonical_evidence_status(target), "current")
            self.assertEqual(observed.call_count, 3)
            observed.reset_mock()
            self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
            self.assertEqual(observed.call_count, 3)


class EvidenceRunnerCaptureReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_m242_runner_service import RunnerServiceFixture
        from tests.test_m243c_runner_gate import (
            _add_runner_task, _complete_runner_pass, _launch, _persist_terminal,
            _seed_review_receipts, _complete_with_matching_commit, selection, service,
        )
        from task_governance_tool.session_identity import capture_caller_identity
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        fixture = RunnerServiceFixture(Path(cls.directory.name))
        # Retain a superseded generation as well as two native Runner Bundles.
        _, intent = _launch(fixture)
        _persist_terminal(fixture, intent, branch="pass")
        later = service._persist_launch_intent(fixture.target, fixture.prepared(),
            caller=capture_caller_identity())
        _persist_terminal(fixture, later, branch="pass")
        _seed_review_receipts(fixture)
        with mock.patch.object(selection, "_stored_runner_physical_basis_matches", return_value=True):
            completed = _complete_with_matching_commit(fixture)
        if completed.returncode:
            raise AssertionError(completed.stdout)
        fixture.task_id = _add_runner_task(fixture, title="Second capture Runner")
        _complete_runner_pass(fixture)
        cls.source = fixture.target
        configure_project_maintenance(cls.source, requested_interval_minutes=None,
            requested_generations=None, enabled_at="2026-10-28T00:00:00Z")

    def copy_target(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        db = directory / "state" / "taskgov.sqlite"
        db.parent.mkdir()
        shutil.copyfile(self.source.db_path, db)
        return target_for(db, self.source.project.canonical_repo)

    @contextmanager
    def observe_graph(self):
        original = runner_repository._validated_verification_runner_graph
        scans = []
        def traced(connection, **options):
            queries = []
            connection.set_trace_callback(queries.append)
            try:
                return original(connection, **options)
            finally:
                connection.set_trace_callback(None)
                scans.extend(sql for sql in queries if sql.startswith((
                    "SELECT * FROM verification_runner_resolutions ",
                    "SELECT * FROM verification_runner_attempts ",
                    "SELECT * FROM verification_runner_observations ",
                    "SELECT * FROM verification_runner_sandbox_events ",
                )))
        with mock.patch.object(runner_repository, "_validated_verification_runner_graph",
                side_effect=traced) as observed, \
             mock.patch.object(storage, "_validated_verification_runner_graph", new=observed):
            observed.graph_scans = scans
            yield observed

    def legacy_capture(self, target):
        with closing(storage.connect_initialized_readonly(target, operation_local=True)) as connection:
            storage.validate_current_database(connection, target)
            return repository.capture_evidence_projection(connection, project_id=target.project.project_id)

    def test_one_whole_graph_pass_matches_old_composition_with_multiple_generations(self):
        target = self.copy_target()
        before = target.db_path.read_bytes()
        with self.observe_graph() as observed:
            expected = self.legacy_capture(target)
            self.assertEqual(observed.call_count, 3)
            self.assertEqual(len(observed.graph_scans), 12)
            observed.reset_mock()
            observed.graph_scans.clear()
            for _ in range(2):
                result = publication._capture(target, include_basis=True).rendered
                self.assertEqual(result, expected)
                self.assertEqual(len(result.bundles), 2)
            self.assertEqual(observed.call_count, 2)
            self.assertEqual(len(observed.graph_scans), 8)
            self.assertIsNot(observed.call_args_list[0].args[0], observed.call_args_list[1].args[0])
        with closing(storage.connect_readonly(target.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM verification_runner_resolutions").fetchone()[0], 3)
        self.assertEqual(target.db_path.read_bytes(), before)

    def test_empty_graph_and_no_bundles_is_validated_once(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo, db = initialize(root)
        target = target_for(db, repo)
        configure_project_maintenance(target, requested_interval_minutes=None,
            requested_generations=None, enabled_at="2026-10-28T00:00:00Z")
        expected = self.legacy_capture(target)
        with self.observe_graph() as observed:
            result = publication._capture(target, include_basis=True).rendered
            self.assertEqual(observed.call_count, 1)
        self.assertEqual(result, expected)
        self.assertEqual(result.bundles, ())

    def test_standalone_entries_keep_their_own_full_validation(self):
        target = self.copy_target()
        operations = (
            (lambda c: repository.capture_evidence_projection(c, project_id=target.project.project_id), 2),
            (lambda c: repository.capture_evidence_projection_basis(c, project_id=target.project.project_id), 2),
            (repository.validate_completion_evidence_bundle_storage, 1),
            (lambda c: storage.validate_current_database(c, target), 1),
        )
        for operation, expected_count in operations:
            with closing(storage.connect_initialized_readonly(target, operation_local=True)) as connection:
                with self.observe_graph() as observed:
                    operation(connection)
                    self.assertEqual(observed.call_count, expected_count)
        with closing(storage.connect(target.db_path)) as connection:
            with self.assertRaises(storage.StorageError):
                storage.capture_current_evidence_projection(connection, target)
        with closing(storage.connect_readonly(target.db_path)) as connection:
            connection.rollback()
            with self.assertRaises(storage.StorageError):
                storage.capture_current_evidence_projection(connection, target)

    def corrupt(self, target, fault):
        table, statement = {
            "orphan": ("verification_runner_resolutions", "DELETE FROM verification_runner_resolutions WHERE rowid=(SELECT MIN(rowid) FROM verification_runner_resolutions)"),
            "foreign": ("verification_runner_observations", "UPDATE verification_runner_observations SET task_id=(SELECT task_id FROM tasks WHERE task_id<>verification_runner_observations.task_id LIMIT 1) WHERE rowid=(SELECT MIN(rowid) FROM verification_runner_observations)"),
            "generation": ("verification_runner_observations", "UPDATE verification_runner_observations SET target_generation=target_generation+100"),
            "reference": ("evidence_references", "UPDATE evidence_references SET digest='sha256:' || printf('%064d',0) WHERE source_kind='runner_observation'"),
            "bundle": ("completion_evidence_bundles", "UPDATE completion_evidence_bundles SET bundle_digest='sha256:' || printf('%064d',0) WHERE rowid=(SELECT MAX(rowid) FROM completion_evidence_bundles)"),
        }[fault]
        with closing(storage.connect(target.db_path)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
            for row in triggers:
                connection.execute('DROP TRIGGER "' + row["name"] + '"')
            self.assertGreater(connection.execute(statement).rowcount, 0)
            for row in triggers:
                connection.execute(row["sql"])
            connection.commit()

    def test_fresh_capture_rejects_all_relationship_faults_like_old_composition(self):
        for fault in ("orphan", "foreign", "generation", "reference", "bundle"):
            with self.subTest(fault=fault):
                target = self.copy_target()
                publication._capture(target, include_basis=True)
                self.corrupt(target, fault)
                before = target.db_path.read_bytes()
                with self.assertRaises(storage.StorageError) as old:
                    self.legacy_capture(target)
                with self.assertRaises(storage.StorageError) as new:
                    publication._capture(target, include_basis=True)
                self.assertEqual(new.exception.code, old.exception.code)
                self.assertEqual(target.db_path.read_bytes(), before)

    def test_publication_guard_revalidates_after_capture_and_reader_still_checks_bytes(self):
        from tests.evidence_reader_oracle import EvidenceConsumerError, read_evidence_index, validate_evidence_source
        target = self.copy_target()
        self.assertEqual(publication.publish_setup_evidence_projection(target).code, "succeeded")
        index = read_evidence_index(target.resolved_evidence_root)
        self.assertEqual(len(index.entries), 2)
        for entry in index.entries:
            validate_evidence_source(index, entry)
        before = target.resolved_evidence_index.read_bytes()
        original = publication._replace_index

        def corrupt_after_capture(*args, **kwargs):
            self.corrupt(target, "reference")
            return original(*args, **kwargs)

        with mock.patch.object(publication, "_replace_index", side_effect=corrupt_after_capture):
            self.assertEqual(publication.publish_setup_evidence_projection(target).code, "failed")
        self.assertEqual(target.resolved_evidence_index.read_bytes(), before)
        bundle_path = next(target.resolved_evidence_bundles.glob("*.json"))
        bundle_path.write_bytes(bundle_path.read_bytes().replace(b'"format_version":4', b'"format_version":9', 1))
        with self.assertRaises(EvidenceConsumerError):
            altered = read_evidence_index(target.resolved_evidence_root)
            for entry in altered.entries:
                validate_evidence_source(altered, entry)


if __name__ == "__main__":
    unittest.main()
