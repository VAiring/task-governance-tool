"""Schema-37 Evidence Reference source lookup index: preservation, admission and public Setup."""
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
from task_governance_tool import schema_viewer_finding_index as predecessor
from task_governance_tool import schema_evidence_reference_index as schema
from tests import test_criterion_link_collector_migration as public_fixture
from tests.m14_test_support import make_physical_install, refresh_test_manifest


class RebuildCatalogTests(unittest.TestCase):
    def test_each_supported_fingerprint_keeps_its_rebuild_boundaries(self):
        from task_governance_tool import (
            schema_review_continuation, schema_review_answers, schema_criterion_link_collector,
            schema_session_slot_index, schema_own_review_indexes, schema_viewer_projection,
            schema_runner_lookup_indexes,
        )
        versions = (schema_review_continuation, schema_review_answers, schema_criterion_link_collector,
                    schema_session_slot_index, schema_own_review_indexes, schema_viewer_projection,
                    schema_runner_lookup_indexes, predecessor, schema)
        with review_fixture(schema_version=29) as fixture, closing(storage.connect(fixture.db)) as connection:
            connection.execute("CREATE TABLE user_catalog_fixture(value TEXT UNIQUE)")
            connection.execute("CREATE INDEX user_visible_index ON user_catalog_fixture(value)")
            connection.execute("CREATE TRIGGER user_visible_trigger AFTER INSERT ON user_catalog_fixture BEGIN SELECT 1; END")
            connection.commit()
            for version in versions:
                with self.subTest(schema=version.VERSION), candidate_runtime(version.VERSION):
                    if version.VERSION > 29:
                        version.migrate(connection)
                    trace = []
                    def selected_metadata(cursor, values):
                        self.assertFalse({"user_catalog_fixture", "user_visible_index", "user_visible_trigger"}.intersection(values))
                        return sqlite3.Row(cursor, values)
                    connection.row_factory = selected_metadata
                    connection.set_trace_callback(trace.append)
                    try:
                        version.validate_owned_contract(connection)
                    finally:
                        connection.set_trace_callback(None)
                        connection.row_factory = sqlite3.Row
                    self.assertEqual(len(trace), 3, trace)
                    self.assertTrue(all("sqlite_master" in sql for sql in trace))
                    bundle = "completion_evidence_bundles"
                    if version.VERSION == 29:
                        rebuilt = [("review_receipts", 28), ("task_completion_cycles", 28), (bundle, 28)]
                    else:
                        rebuilt = [("review_receipts", 29), ("evidence_references", 29), (bundle, 29)]
                        if version.VERSION >= 31:
                            rebuilt += [("criterion_evidence_links", 30), (bundle, 30)]
                        rebuilt.extend((bundle, n) for n in range(31, version.VERSION))
                    protected = {table for table, _ in rebuilt}
                    if version.VERSION >= 34:
                        protected.update(("viewer_projection_state", "viewer_task_revisions"))
                    old = f"COMPLETION_EVIDENCE_BUNDLES_V{version.VERSION - 1}"
                    bad = [f"CREATE VIEW {old} AS SELECT 1",
                           f"CREATE INDEX {old} ON user_catalog_fixture(value)",
                           f"CREATE TRIGGER {old} AFTER INSERT ON user_catalog_fixture BEGIN SELECT 1; END",
                           "DROP INDEX idx_evidence_references_source"]
                    bad.extend(f"CREATE TABLE {table.upper()}_V{n}(v)" for table, n in rebuilt)
                    for table in sorted(protected):
                        bad.extend((f"CREATE INDEX extra_attachment ON {table}(1)",
                                    f"CREATE TRIGGER extra_attachment AFTER INSERT ON {table} BEGIN SELECT 1; END"))
                    for ddl in bad:
                        with self.subTest(ddl=ddl):
                            connection.execute("BEGIN")
                            try:
                                connection.execute(ddl)
                                with self.assertRaises(storage.StorageError) as rejected:
                                    version.validate_owned_contract(connection)
                                self.assertEqual(rejected.exception.code, "project_state_unreadable")
                            finally:
                                connection.rollback()
                    connection.execute("BEGIN")
                    try:
                        connection.execute("DROP INDEX idx_evidence_references_source")
                        connection.execute("CREATE INDEX idx_evidence_references_source ON evidence_references(task_id)")
                        with self.assertRaises(storage.StorageError):
                            version.validate_owned_contract(connection)
                    finally:
                        connection.rollback()
                    # Later catalogs do not inherit schema 29's v28 residue
                    # or its attachment guard on the completion-cycle table.
                    connection.execute("BEGIN")
                    try:
                        connection.execute("CREATE TABLE viewer_task_revisions_v33(v)")
                        if version.VERSION > 29:
                            connection.execute("CREATE TABLE review_receipts_v28(v)")
                            connection.execute("CREATE INDEX extra_cycle_attachment ON task_completion_cycles(completed_at)")
                        version.validate_owned_contract(connection)
                    finally:
                        connection.rollback()

    def test_older_name_checks_preserve_the_object_type_filters(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE user_catalog_fixture(x)")
            for names, collision in ((storage._SCHEMA21_TEMP_TABLES, storage._schema21_migration_temporary_name_collision),
                                     (storage._SCHEMA22_TEMP_TABLES, storage._schema22_migration_temporary_name_collision)):
                old = names[-1].upper()
                for kind, ddl in (("table", f"CREATE TABLE {old}(x)"),
                                  ("index", f"CREATE INDEX {old} ON user_catalog_fixture(x)"),
                                  ("view", f"CREATE VIEW {old} AS SELECT 1"),
                                  ("trigger", f"CREATE TRIGGER {old} AFTER INSERT ON user_catalog_fixture BEGIN SELECT 1; END")):
                    with self.subTest(old=old, kind=kind):
                        connection.execute("BEGIN")
                        try:
                            connection.execute(ddl)
                            self.assertEqual(collision(connection), kind != "trigger")
                            if names is storage._SCHEMA21_TEMP_TABLES:
                                self.assertEqual(storage._schema21_temporary_table_present(connection), kind == "table")
                        finally:
                            connection.rollback()


class PublicEvidenceSourceLookupSetupTests(unittest.TestCase):
    cli = public_fixture.PublicCriterionLinkCollectorSetupTests.cli
    business = public_fixture.PublicCriterionLinkCollectorSetupTests.business

    def test_public_populated36_preview_migration_backup_and_current_reentry(self):
        from task_governance_tool.viewer import build_viewer_snapshot
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="taskgov-index-setup-"))
        self.install = make_physical_install(Path(temporary))
        self.enterContext(mock.patch.dict(os.environ,
            {"CODEX_THREAD_ID": "00000000-0000-4000-8000-000000000001"}))
        source = self.install.skill_root / "scripts/task_governance_tool/storage.py"
        # This fixture preserves the exact historical 36-to-37 public boundary.
        current = source.read_text(encoding="utf-8")
        self.assertEqual(current.count("SCHEMA_VERSION = 37"), 1)
        source.write_text(current.replace("SCHEMA_VERSION = 37", "SCHEMA_VERSION = 36", 1),
            encoding="utf-8", newline="\n")
        refresh_test_manifest(self.install.skill_root)
        self.assertEqual(self.cli("setup")["schema_to"], 36)
        task = self.cli("task", "add", "--title", "Preserved source36 completion",
            "--status", "in_progress", "--review-tier", "0", "--verification-not-required",
            "Isolated source fixture")["task"]["task_id"]
        self.cli("review", "target", "set", task, "--kind", "diff_fingerprint",
            "--revision", "sha256:" + "a" * 64)
        self.cli("review", "receipt", "add", task, "--reviewer", "Mechanical fixture",
            "--kind", "not_required", "--verdict", "not_required", "--summary", "Fixture review")
        self.cli("task", "complete", task, "--verification-complete", "--review-complete", "--commit-not-required")
        with closing(storage.connect_readonly(self.install.db_path)) as connection, candidate_runtime(36):
            before = self.business(connection)
            project = connection.execute("SELECT project_id FROM tasks").fetchone()[0]
            _, artifacts = _bundle_artifacts(connection, project)
        source.write_text(current, encoding="utf-8", newline="\n")
        refresh_test_manifest(self.install.skill_root)
        snapshot = self.install.state_snapshot()
        preview = self.cli("setup", "--read-only")
        self.assertEqual((preview["schema_from"], preview["schema_to"]), (36, 37))
        self.assertEqual(preview["completed_writes"], [])
        self.assertEqual(self.install.state_snapshot(), snapshot)
        applied = self.cli("setup")
        self.assertEqual(applied["completed_writes"], ["migration_backup", "database_migrate",
            "evidence_projection_publish", "viewer_publish"])
        preserved = []
        for path in (self.install.fixed_root / "backups").glob("taskgov-backup-v1_*.sqlite"):
            with closing(storage.connect_readonly(path)) as connection:
                # Routine backups from earlier source36 states can coexist;
                # require the exact migration-basis backup, not every old copy.
                if storage.current_schema_version(connection) == 36 and self.business(connection) == before:
                    preserved.append(path)
        self.assertTrue(preserved)
        with candidate_runtime(37), closing(storage.connect_initialized_readonly(self.install.target)) as connection:
            self.assertEqual(self.business(connection), before)
            self.assertEqual(_bundle_artifacts(connection, project)[1], artifacts)
            self.assertEqual(build_viewer_snapshot(connection, self.install.target).snapshot["source_schema_version"], 37)
        index = json.loads(self.install.target.resolved_evidence_index.read_bytes())
        self.assertEqual(index["payload"]["source_schema_version"], 37)
        self.assertTrue(self.install.viewer_path.is_file())
        snapshot = self.install.state_snapshot()
        self.cli("doctor")
        repeated = self.cli("setup")
        self.assertEqual(repeated["completed_writes"], [])
        self.assertEqual(self.install.state_snapshot(), snapshot)

class EvidenceSourceLookupMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = cls.enterClassContext(review_fixture(schema_version=36, contract=None))
        cls.artifact = cls.fixture.collected_source_artifact()
        cls.db, cls.repo = cls.fixture.db, cls.fixture.repo
        cls.project = cls.fixture.basis["project_id"]
        with closing(storage.connect(cls.db)) as connection:
            _add_unrelated_objects(connection)

    def assert_idle(self, connection):
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def test_source36_fingerprint_and_bytes_remain_exact_and_read_only(self):
        with _source_copy(self.db) as connection:
            self.assertEqual(predecessor.owned_fingerprint(connection),
                predecessor.OWNED_FINGERPRINT)
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
        with _source_copy(self.db) as connection, candidate_runtime(37):
            # Additional indexes on non-rebuilt tables were admitted before
            # this migration and must remain supported and byte-exact.
            extra_indexes = (
                "CREATE INDEX unrelated_finding_lookup ON review_findings(summary)",
                "CREATE INDEX unrelated_cycle_lookup ON task_completion_cycles(completed_at)",
            )
            for statement in extra_indexes:
                connection.execute(statement)
            connection.commit()
            predecessor.validate_storage(connection)
            before = _row_identity_snapshot(connection)
            history = tuple(tuple(row) for row in connection.execute("SELECT rowid,* FROM schema_migrations ORDER BY rowid"))
            triggers = tuple(tuple(row) for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE name IN ('trg_task_ownership_slot_insert','trg_task_ownership_slot_update') ORDER BY name"))
            with candidate_runtime(36):
                old = _bundle_artifacts(connection, self.project)[1]
            self.assertTrue(schema.migrate(connection))
            self.assertEqual(_row_identity_snapshot(connection), before)
            self.assertEqual(tuple(tuple(row) for row in connection.execute(
                "SELECT rowid,* FROM schema_migrations WHERE version<37 ORDER BY rowid")), history)
            self.assertEqual(tuple(tuple(row) for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE name IN ('trg_task_ownership_slot_insert','trg_task_ownership_slot_update') ORDER BY name")), triggers)
            self.assertEqual(_bundle_artifacts(connection, self.project)[1], old)
            self.assertEqual(next(iter(old.values())).payload_bytes, self.artifact.payload_bytes)
            self.assertEqual(schema.owned_fingerprint(connection), schema.OWNED_FINGERPRINT)
            self.assertEqual(tuple(row[0] for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE name IN "
                "('unrelated_finding_lookup','unrelated_cycle_lookup') ORDER BY name DESC")), extra_indexes)
            self.assertEqual(dict((row[0], row[1]) for row in connection.execute(
                "SELECT type,COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'unrelated_%' GROUP BY type")),
                {"index": 64, "table": 52, "trigger": 107})
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
            with self.subTest(stage=stage), _source_copy(self.db) as connection, candidate_runtime(37):
                if stage != "delta":
                    change = "UPDATE tasks SET title='isolated marker change'" if stage == "marker" else \
                        "UPDATE schema_migrations SET name='isolated history change' WHERE version=36"
                    connection.execute("CREATE TRIGGER unrelated_index_marker AFTER INSERT ON schema_migrations "
                        "WHEN NEW.version=37 BEGIN " + change + "; END")
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
                with self.subTest(index=name, altered=altered), _source_copy(self.db) as connection, candidate_runtime(37):
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
        for ddl in ("CREATE INDEX idx_evidence_references_source_identity ON evidence_references(source_id)",
                    "CREATE INDEX idx_evidence_references_task_kind ON evidence_references(source_id)",
                "CREATE TABLE completion_evidence_bundles_v36(placeholder)",
                "CREATE TABLE COMPLETION_EVIDENCE_BUNDLES_V36(placeholder)"):
            with self.subTest(ddl=ddl), _source_copy(self.db) as connection, candidate_runtime(37):
                connection.execute(ddl)
                connection.commit()
                before = _logical_snapshot(connection)
                with mock.patch.object(schema, "_apply_delta_locked", wraps=schema._apply_delta_locked) as delta:
                    with self.assertRaises(storage.StorageError):
                        schema.migrate(connection)
                    self.assertEqual(delta.call_count, 0)
                self.assertEqual(before, _logical_snapshot(connection))
                self.assert_idle(connection)

    def test_old_runtime_refuses37_and_current_native_evidence_reports_actual_source(self):
        from tests.verification_receipt_test_support import run_taskgov, payload
        with _source_copy(self.db) as connection, candidate_runtime(37):
            schema.migrate(connection)
            before = _logical_snapshot(connection)
            with candidate_runtime(36), self.assertRaises(storage.StorageError):
                storage.validate_current_database(connection, self.fixture.target)
            self.assertEqual(_logical_snapshot(connection), before)
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        native_db = Path(temporary) / "native.sqlite"
        with closing(storage.connect(self.db)) as source, closing(storage.connect(native_db)) as connection:
            source.backup(connection)
        with closing(storage.connect(native_db)) as connection, candidate_runtime(37):
            schema.migrate(connection)
        with candidate_runtime(37):
            fixture = attach_existing(db=native_db, repo=self.repo, task_id=self.fixture.task_id, schema_version=37)
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
                native = [value for value in bundles.values() if value.payload["source_schema_version"] == 37]
                self.assertEqual(len(native), 1)
                self.assertEqual(native[0].payload["bundle_version"], 4)
                schema.validate_storage(connection)
