"""Whole-source publication capture: reuse, rejection and resource lifetimes."""
from __future__ import annotations

import gc
import json
import shutil
import sqlite3
import tempfile
import unittest
import weakref
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.verification_receipt_test_support import (
    add_task, completion, initialize, run_taskgov, seed_current_review_evidence,
    target_for,
)
from task_governance_tool import evidence_projection as codec
from task_governance_tool import evidence_publication as publication
from task_governance_tool import evidence_validation_repository as repository
from task_governance_tool import storage
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
        self.assertEqual({r.bundle.source_schema_version for r in basis.native_bundles}, {29, 33})
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
        connections, bases, artifacts = [], [], []
        open_read = storage.connect_readonly
        capture_rows = repository._capture_evidence_projection_basis_rows
        build = codec.build_projection_bundle_artifact
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

        def write(*args, **kwargs):
            gc.collect()
            self.assertTrue(bases)
            self.assertTrue(all(ref() is None for ref in bases + artifacts))
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
             mock.patch.object(repository, "_capture_evidence_projection_basis_rows", side_effect=rows), \
             mock.patch.object(codec, "build_projection_bundle_artifact", side_effect=built), \
             mock.patch.object(publication, "_temporary_file", side_effect=write), \
             mock.patch.object(publication, "_atomic_replace_temporary", side_effect=replace):
            result = publication.run_routine_evidence_projection(target)
        self.assertEqual(result.code, "succeeded")
        self.assertEqual(len(physical_calls), 4)
        self.assertEqual(physical_calls[-1], publication._INDEX_TEMP_PREFIX)
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
                capture = publication.capture_evidence_projection
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
                        # closes. The fresh global guard must reject its index.
                        _complete_fixture_task(target.project.canonical_repo,
                            target.db_path, 3 + len(attempts), large=False)
                    return replace_index(*args, **kwargs)

                with mock.patch.object(publication, "capture_evidence_projection", side_effect=captured), \
                     mock.patch.object(publication, "_replace_index", side_effect=replace):
                    result = publication.run_routine_evidence_projection(target)
                self.assertEqual(counts, [4, 5])
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


if __name__ == "__main__":
    unittest.main()
