"""Schema-33 own-review indexes: preservation, admission and public Setup."""
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from tests.review_answer_fixture_support import review_fixture, attach_existing
from tests.test_review_session_migration import candidate_runtime
from tests.test_criterion_link_collector_migration import _row_identity_snapshot
from tests.test_m23s_schema22_migration import _logical_snapshot, _source_copy, _mutating_statements, _add_unrelated_objects
from tests.test_m23s_schema22_validation import _bundle_artifacts
from task_governance_tool import storage
from task_governance_tool import schema_session_slot_index as predecessor
from task_governance_tool import schema_own_review_indexes as schema
from tests import test_criterion_link_collector_migration as public_fixture
from tests.m14_test_support import make_physical_install, refresh_test_manifest


class PublicOwnReviewIndexSetupTests(unittest.TestCase):
    cli = public_fixture.PublicCriterionLinkCollectorSetupTests.cli
    business = public_fixture.PublicCriterionLinkCollectorSetupTests.business

    def test_public_populated32_preview_migration_backup_and_current_reentry(self):
        from task_governance_tool.viewer import build_viewer_snapshot
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="taskgov-index-setup-"))
        self.install = make_physical_install(Path(temporary))
        self.enterContext(mock.patch.dict(os.environ,
            {"CODEX_THREAD_ID": "00000000-0000-4000-8000-000000000001"}))
        source = self.install.skill_root / "scripts/task_governance_tool/storage.py"
        # This fixture preserves the exact historical 32-to-33 public boundary.
        current = source.read_text(encoding="utf-8").replace("SCHEMA_VERSION = 34", "SCHEMA_VERSION = 33", 1)
        self.assertEqual(current.count("SCHEMA_VERSION = 33"), 1)
        source.write_text(current.replace("SCHEMA_VERSION = 33", "SCHEMA_VERSION = 32", 1),
            encoding="utf-8", newline="\n")
        refresh_test_manifest(self.install.skill_root)
        self.assertEqual(self.cli("setup")["schema_to"], 32)
        task = self.cli("task", "add", "--title", "Preserved source32 completion",
            "--status", "in_progress", "--review-tier", "0", "--verification-not-required",
            "Isolated source fixture")["task"]["task_id"]
        self.cli("review", "target", "set", task, "--kind", "diff_fingerprint",
            "--revision", "sha256:" + "a" * 64)
        self.cli("review", "receipt", "add", task, "--reviewer", "Mechanical fixture",
            "--kind", "not_required", "--verdict", "not_required", "--summary", "Fixture review")
        self.cli("task", "complete", task, "--verification-complete", "--review-complete", "--commit-not-required")
        with closing(storage.connect_readonly(self.install.db_path)) as connection, candidate_runtime(32):
            before = self.business(connection)
            project = connection.execute("SELECT project_id FROM tasks").fetchone()[0]
            _, artifacts = _bundle_artifacts(connection, project)
        source.write_text(current, encoding="utf-8", newline="\n")
        refresh_test_manifest(self.install.skill_root)
        snapshot = self.install.state_snapshot()
        preview = self.cli("setup", "--read-only")
        self.assertEqual((preview["schema_from"], preview["schema_to"]), (32, 33))
        self.assertEqual(preview["completed_writes"], [])
        self.assertEqual(self.install.state_snapshot(), snapshot)
        applied = self.cli("setup")
        self.assertEqual(applied["completed_writes"], ["migration_backup", "database_migrate",
            "evidence_projection_publish", "viewer_publish"])
        preserved = []
        for path in (self.install.fixed_root / "backups").glob("taskgov-backup-v1_*.sqlite"):
            with closing(storage.connect_readonly(path)) as connection:
                # Routine backups from earlier source32 states can coexist;
                # require the exact migration-basis backup, not every old copy.
                if storage.current_schema_version(connection) == 32 and self.business(connection) == before:
                    preserved.append(path)
        self.assertTrue(preserved)
        with candidate_runtime(33), closing(storage.connect_initialized_readonly(self.install.target)) as connection:
            self.assertEqual(self.business(connection), before)
            self.assertEqual(_bundle_artifacts(connection, project)[1], artifacts)
            self.assertEqual(build_viewer_snapshot(connection, self.install.target).snapshot["source_schema_version"], 33)
        index = json.loads(self.install.target.resolved_evidence_index.read_bytes())
        self.assertEqual(index["payload"]["source_schema_version"], 33)
        self.assertTrue(self.install.viewer_path.is_file())
        snapshot = self.install.state_snapshot()
        self.cli("doctor")
        repeated = self.cli("setup")
        self.assertEqual(repeated["completed_writes"], [])
        self.assertEqual(self.install.state_snapshot(), snapshot)

class OwnReviewIndexMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = cls.enterClassContext(review_fixture(schema_version=32, contract=None))
        cls.artifact = cls.fixture.collected_source_artifact()
        cls.db, cls.repo = cls.fixture.db, cls.fixture.repo
        cls.project = cls.fixture.basis["project_id"]
        with closing(storage.connect(cls.db)) as connection:
            _add_unrelated_objects(connection)

    def assert_idle(self, connection):
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def test_source32_fingerprint_and_bytes_remain_exact_and_read_only(self):
        with _source_copy(self.db) as connection:
            self.assertEqual(predecessor.owned_fingerprint(connection),
                "9104eec74408fdfc4a0e9def6c53c187a13ed98c3bd2b5756a15c353c3dd4a0c")
            before = _logical_snapshot(connection)
            trace = []
            connection.execute("PRAGMA query_only=ON")
            connection.set_trace_callback(trace.append)
            predecessor.validate_storage(connection)
            self.assertFalse(predecessor.migrate(connection))
            self.assertEqual(_mutating_statements(trace), ())
            self.assertEqual(before, _logical_snapshot(connection))
            self.assert_idle(connection)

    def test_migration_keeps_all_rows_ids_history_sealed_bytes_and_trigger_definitions(self):
        with _source_copy(self.db) as connection, candidate_runtime(33):
            before = _row_identity_snapshot(connection)
            history = tuple(tuple(row) for row in connection.execute("SELECT rowid,* FROM schema_migrations ORDER BY rowid"))
            triggers = tuple(tuple(row) for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE name IN ('trg_task_ownership_slot_insert','trg_task_ownership_slot_update') ORDER BY name"))
            with candidate_runtime(32):
                old = _bundle_artifacts(connection, self.project)[1]
            self.assertTrue(schema.migrate(connection))
            self.assertEqual(_row_identity_snapshot(connection), before)
            self.assertEqual(tuple(tuple(row) for row in connection.execute(
                "SELECT rowid,* FROM schema_migrations WHERE version<33 ORDER BY rowid")), history)
            self.assertEqual(tuple(tuple(row) for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE name IN ('trg_task_ownership_slot_insert','trg_task_ownership_slot_update') ORDER BY name")), triggers)
            self.assertEqual(_bundle_artifacts(connection, self.project)[1], old)
            self.assertEqual(next(iter(old.values())).payload_bytes, self.artifact.payload_bytes)
            self.assertEqual(schema.owned_fingerprint(connection), schema.OWNED_FINGERPRINT)
            self.assertEqual(dict((row[0], row[1]) for row in connection.execute(
                "SELECT type,COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'unrelated_%' GROUP BY type")),
                {"index": 51, "table": 50, "trigger": 101})
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            final = _logical_snapshot(connection)
            trace = []
            connection.execute("PRAGMA query_only=ON")
            connection.set_trace_callback(trace.append)
            self.assertFalse(schema.migrate(connection))
            self.assertEqual(_mutating_statements(trace), ())
            self.assertEqual(_logical_snapshot(connection), final)
            self.assert_idle(connection)

    def test_delta_and_marker_failures_roll_back_rows_history_ddl_and_flags(self):
        for stage in ("delta", "marker", "old_marker"):
            with self.subTest(stage=stage), _source_copy(self.db) as connection, candidate_runtime(33):
                if stage != "delta":
                    change = "UPDATE tasks SET title='isolated marker change'" if stage == "marker" else \
                        "UPDATE schema_migrations SET name='isolated history change' WHERE version=32"
                    connection.execute("CREATE TRIGGER unrelated_index_marker AFTER INSERT ON schema_migrations "
                        "WHEN NEW.version=33 BEGIN " + change + "; END")
                    connection.commit()
                before = _logical_snapshot(connection)
                apply = schema._apply_delta_locked
                def interrupted(current):
                    apply(current)
                    raise sqlite3.OperationalError("isolated index delta interruption")
                with mock.patch.object(schema, "_apply_delta_locked", side_effect=interrupted if stage == "delta" else apply):
                    with self.assertRaises(storage.StorageError):
                        schema.migrate(connection)
                self.assertEqual(before, _logical_snapshot(connection))
                predecessor.validate_storage(connection)
                self.assert_idle(connection)

    def test_missing_or_altered_index_never_passes_reentry(self):
        for name in schema.INDEX_SQL:
            for altered in (False, True):
                with self.subTest(index=name, altered=altered), _source_copy(self.db) as connection, candidate_runtime(33):
                    schema.migrate(connection)
                    connection.execute("DROP INDEX " + name)
                    if altered:
                        connection.execute("CREATE INDEX " + name + " ON tasks(task_id)")
                    connection.commit()
                    before = _logical_snapshot(connection)
                    with self.assertRaises(storage.StorageError):
                        schema.migrate(connection)
                    self.assertEqual(before, _logical_snapshot(connection))
                    self.assert_idle(connection)

    def test_conflicting_later_index_or_temporary_name_is_rejected_before_delta(self):
        for ddl in ("CREATE INDEX idx_review_continuations_task_history ON review_continuations(task_id)",
                "CREATE TABLE completion_evidence_bundles_v32(placeholder)",
                "CREATE TABLE COMPLETION_EVIDENCE_BUNDLES_V32(placeholder)"):
            with self.subTest(ddl=ddl), _source_copy(self.db) as connection, candidate_runtime(33):
                connection.execute(ddl)
                connection.commit()
                before = _logical_snapshot(connection)
                with mock.patch.object(schema, "_apply_delta_locked", wraps=schema._apply_delta_locked) as delta:
                    with self.assertRaises(storage.StorageError):
                        schema.migrate(connection)
                    self.assertEqual(delta.call_count, 0)
                self.assertEqual(before, _logical_snapshot(connection))
                self.assert_idle(connection)

    def test_old_runtime_refuses33_and_current_native_evidence_reports_actual_source(self):
        from tests.verification_receipt_test_support import run_taskgov, payload
        with _source_copy(self.db) as connection, candidate_runtime(33):
            schema.migrate(connection)
            before = _logical_snapshot(connection)
            with candidate_runtime(32), self.assertRaises(storage.StorageError):
                storage.validate_current_database(connection, self.fixture.target)
            self.assertEqual(_logical_snapshot(connection), before)
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        native_db = Path(temporary) / "native.sqlite"
        with closing(storage.connect(self.db)) as source, closing(storage.connect(native_db)) as connection:
            source.backup(connection)
        with closing(storage.connect(native_db)) as connection, candidate_runtime(33):
            schema.migrate(connection)
        with candidate_runtime(33):
            fixture = attach_existing(db=native_db, repo=self.repo, task_id=self.fixture.task_id, schema_version=33)
            result = payload(run_taskgov("task", "edit", fixture.task_id, "--status", "in_progress",
                "--reopen-reason", "Isolated current-source completion", "--repo", str(self.repo), "--db", str(native_db), "--json"))
            self.assertTrue(result["ok"], result)
            # The existing public completion fixture creates fresh review basis.
            from tests.verification_receipt_test_support import seed_current_review_evidence, completion, add_receipt
            # This helper itself captures the new target; retain its generation.
            target = seed_current_review_evidence(native_db, self.repo, fixture.task_id)
            verified = add_receipt(native_db, self.repo, fixture.task_id, target)
            self.assertEqual(verified.returncode, 0, verified.stdout)
            result = completion(native_db, self.repo, fixture.task_id)
            self.assertEqual(result.returncode, 0, result.stdout)
            with closing(storage.connect_initialized_readonly(fixture.target)) as connection:
                _, bundles = _bundle_artifacts(connection, self.project)
                native = [value for value in bundles.values() if value.payload["source_schema_version"] == 33]
                self.assertEqual(len(native), 1)
                self.assertEqual(native[0].payload["bundle_version"], 4)
                schema.validate_storage(connection)
