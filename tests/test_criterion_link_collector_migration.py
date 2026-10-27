"""Exact schema-30 forward repair without rewriting sealed historical evidence."""
from __future__ import annotations

import sqlite3
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.review_answer_fixture_support import attach_existing, review_fixture
from tests.test_review_session_migration import candidate_runtime
from tests.test_m23s_schema22_migration import (
    _add_unrelated_objects, _logical_snapshot, _mutating_statements, _source_copy,
)
from tests.test_m23s_schema22_validation import _bundle_artifacts
from task_governance_tool import storage
from task_governance_tool import schema_review_answers as predecessor
from task_governance_tool import schema_criterion_link_collector as schema
from tests.m14_test_support import make_physical_install, refresh_test_manifest


def _row_identity_snapshot(connection, *, fixture_aliases=None):
    """Independent oracle: retain each row's identity with its entire value tuple."""
    aliases = fixture_aliases or {}
    rowid_tables = {row[1] for row in connection.execute("PRAGMA main.table_list")
        if row[0] == "main" and row[2] == "table" and not row[4]}
    result = {}
    for name, in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (lower(name) NOT GLOB 'sqlite_*' OR name='sqlite_sequence') "
        "AND name!='schema_migrations' ORDER BY name"
    ):
        if name not in rowid_tables:
            continue
        # Every stock table has an unshadowed rowid. Non-owned alias fixtures
        # provide their known identity column explicitly, not via product code.
        identity = aliases.get(name, "rowid")
        quoted_name = '"' + name.replace('"', '""') + '"'
        quoted_identity = '"' + identity.replace('"', '""') + '"'
        result[name] = tuple(tuple(row) for row in connection.execute(
            f"SELECT observed.{quoted_identity},observed.* FROM {quoted_name} AS observed "
            f"ORDER BY observed.{quoted_identity}"))
    return result


class CriterionLinkCollectorMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = cls.enterClassContext(review_fixture(schema_version=30, contract=None))
        cls.artifact = cls.fixture.collected_source_artifact()
        cls.db = cls.fixture.db
        cls.project_id = cls.fixture.basis["project_id"]
        with closing(storage.connect(cls.db)) as connection:
            _add_unrelated_objects(connection)

    def assert_idle(self, connection):
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def test_schema30_source_ddl_fingerprint_and_sealed_bundle_remain_admitted(self):
        with _source_copy(self.db) as connection, candidate_runtime(30):
            before = _logical_snapshot(connection)
            self.assertEqual(predecessor.OWNED_FINGERPRINT,
                "00cecd66821a5589ba99a359920393765c6c5e97fb421d4982091318a502952f")
            self.assertEqual(predecessor.owned_fingerprint(connection), predecessor.OWNED_FINGERPRINT)
            statements = []
            connection.execute("PRAGMA query_only=ON")
            connection.set_trace_callback(statements.append)
            predecessor.validate_storage(connection)
            self.assertFalse(predecessor.migrate(connection))
            _, bundles = _bundle_artifacts(connection, self.project_id)
            self.assertEqual(next(iter(bundles.values())), self.artifact)
            self.assertEqual(self.artifact.payload["source_schema_version"], 30)
            self.assertEqual(_mutating_statements(statements), ())
            self.assertEqual(_logical_snapshot(connection), before)
            self.assert_idle(connection)

    def test_migration_preserves_every_business_row_id_rowid_and_old_bundle_bytes(self):
        with _source_copy(self.db) as connection, candidate_runtime(31):
            tables = tuple(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND lower(name) NOT GLOB 'sqlite_*' AND name!='schema_migrations' ORDER BY name"))
            before = storage._selected_table_projection_snapshot(connection, tables)
            history = tuple(tuple(row) for row in connection.execute(
                "SELECT rowid,* FROM schema_migrations ORDER BY rowid"))
            rowids = _row_identity_snapshot(connection)
            self.assertIn("task_events", rowids)
            self.assertIn("unrelated_m23s_fixture", rowids)
            with candidate_runtime(30):
                _, old_bundles = _bundle_artifacts(connection, self.project_id)
            marker_time = "2026-10-18T00:00:00Z"
            with mock.patch.object(storage, "utc_now", return_value=marker_time):
                self.assertTrue(schema.migrate(connection))
            self.assertEqual(storage.current_schema_version(connection), 31)
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tables), before)
            self.assertEqual(_row_identity_snapshot(connection), rowids)
            self.assertEqual(tuple(tuple(row) for row in connection.execute(
                "SELECT rowid,* FROM schema_migrations ORDER BY rowid")),
                history + ((31, 31, "criterion_link_collector", marker_time),))
            _, new_bundles = _bundle_artifacts(connection, self.project_id)
            self.assertEqual(new_bundles, old_bundles)
            self.assertEqual(next(iter(new_bundles.values())).payload_bytes, self.artifact.payload_bytes)
            final = _logical_snapshot(connection)
            statements = []
            connection.execute("PRAGMA query_only=ON")
            connection.set_trace_callback(statements.append)
            schema.validate_storage(connection)
            self.assertFalse(schema.migrate(connection))
            self.assertEqual(_mutating_statements(statements), ())
            self.assertEqual(_logical_snapshot(connection), final)
            self.assert_idle(connection)

    def test_delta_and_marker_failure_restore_schema_rows_evidence_and_flags(self):
        for mode in ("after_delta", "marker"):
            with self.subTest(mode=mode), _source_copy(self.db) as connection, candidate_runtime(31):
                if mode == "marker":
                    connection.execute("CREATE TRIGGER unrelated_marker_change AFTER INSERT ON schema_migrations "
                        "WHEN NEW.version=31 BEGIN UPDATE tasks SET title='changed by fixture trigger'; END")
                    connection.commit()
                before = _logical_snapshot(connection)
                apply = schema._apply_delta_locked

                def interrupted(current):
                    apply(current)
                    raise sqlite3.OperationalError("isolated collector-link migration interruption")

                if mode == "after_delta":
                    with mock.patch.object(schema, "_apply_delta_locked", side_effect=interrupted):
                        with self.assertRaises(storage.StorageError):
                            schema.migrate(connection)
                else:
                    with self.assertRaises(storage.StorageError):
                        schema.migrate(connection)
                self.assertEqual(_logical_snapshot(connection), before)
                self.assertEqual(storage.current_schema_version(connection), 30)
                predecessor.validate_storage(connection)
                self.assert_idle(connection)

    def test_marker_triggered_history_changes_roll_back_schema_rows_and_flags(self):
        for version, column, value in (
            (30, "name", "changed historical migration"),
            (30, "applied_at", "2001-01-01T00:00:00Z"),
            (31, "applied_at", "2001-01-01T00:00:00Z"),
        ):
            with self.subTest(version=version, column=column), _source_copy(self.db) as connection, candidate_runtime(31):
                connection.execute(
                    "CREATE TRIGGER unrelated_migration_history_change AFTER INSERT ON schema_migrations "
                    "WHEN NEW.version=31 BEGIN UPDATE schema_migrations "
                    f"SET {column}='{value}' WHERE version={version}; END")
                connection.commit()
                predecessor.validate_storage(connection)
                before = _logical_snapshot(connection)
                with self.assertRaises(storage.StorageError):
                    schema.migrate(connection)
                self.assertEqual(_logical_snapshot(connection), before)
                self.assertEqual(storage.current_schema_version(connection), 30)
                predecessor.validate_storage(connection)
                self.assert_idle(connection)

    def test_task_event_rowid_change_rolls_back_and_preserves_same_timestamp_viewer_order(self):
        from task_governance_tool.tasks import create_task_event, list_tasks_for_viewer

        for phase in ("after_delta", "marker"):
            with self.subTest(phase=phase), _source_copy(self.db) as connection, candidate_runtime(31):
                task_id = self.fixture.basis["task_id"]
                expected = [create_task_event(connection, project_id=self.project_id,
                    task_id=task_id, event_type="task_updated", summary=f"Fixture event {number}",
                    created_at="2030-01-01T00:00:00Z")["task_event_id"] for number in range(3)]

                def displayed_events():
                    projected = list_tasks_for_viewer(connection, self.fixture.target.project,
                        source_schema_version=30)
                    task = next(item for item in projected.tasks if item["task_id"] == task_id)
                    return tuple(event["task_event_id"] for event in task["events"])

                if phase == "marker":
                    connection.execute("CREATE TRIGGER unrelated_event_identity_change "
                        "AFTER INSERT ON schema_migrations WHEN NEW.version=31 "
                        "BEGIN UPDATE task_events SET rowid=-rowid; END")
                connection.commit()
                predecessor.validate_storage(connection)
                before, identities = _logical_snapshot(connection), _row_identity_snapshot(connection)
                displayed = displayed_events()
                self.assertEqual(displayed[:3], tuple(reversed(expected)))
                # Prove this changes real user-facing tie ordering, while the
                # pre-existing named-column snapshot cannot detect the change.
                connection.execute("BEGIN")
                connection.execute("UPDATE task_events SET rowid=-rowid")
                self.assertEqual(displayed_events()[:3], tuple(expected))
                self.assertEqual(_logical_snapshot(connection), before)
                connection.rollback()
                apply = schema._apply_delta_locked

                def reordered(current):
                    apply(current)
                    current.execute("UPDATE task_events SET rowid=-rowid")

                with mock.patch.object(schema, "_apply_delta_locked",
                        side_effect=reordered if phase == "after_delta" else apply):
                    with self.assertRaises(storage.StorageError):
                        schema.migrate(connection)
                self.assertEqual(_logical_snapshot(connection), before)
                self.assertEqual(_row_identity_snapshot(connection), identities)
                self.assertEqual(displayed_events(), displayed)
                self.assertEqual(storage.current_schema_version(connection), 30)
                predecessor.validate_storage(connection)
                self.assert_idle(connection)

    def test_marker_reassociation_of_unrelated_rowids_rolls_back_even_with_same_id_set(self):
        with _source_copy(self.db) as connection, candidate_runtime(31):
            connection.execute("CREATE TABLE unrelated_identity_values (value BLOB)")
            connection.executemany("INSERT INTO unrelated_identity_values(rowid,value) VALUES (?,?)",
                ((1, b"first"), (2, b"second"), (3, b"third")))
            connection.execute("CREATE TRIGGER unrelated_identity_swap AFTER INSERT ON schema_migrations "
                "WHEN NEW.version=31 BEGIN UPDATE unrelated_identity_values SET rowid=-rowid; "
                "UPDATE unrelated_identity_values SET rowid=4+rowid; END")
            connection.commit()
            predecessor.validate_storage(connection)
            before, identities = _logical_snapshot(connection), _row_identity_snapshot(connection)
            # Identity sets and sorted named values both stay equal; their
            # association must be checked rather than comparing those separately.
            connection.execute("BEGIN")
            connection.execute("UPDATE unrelated_identity_values SET rowid=-rowid")
            connection.execute("UPDATE unrelated_identity_values SET rowid=4+rowid")
            changed = _row_identity_snapshot(connection)
            self.assertEqual(tuple(row[0] for row in identities["unrelated_identity_values"]),
                tuple(row[0] for row in changed["unrelated_identity_values"]))
            self.assertNotEqual(identities, changed)
            self.assertEqual(_logical_snapshot(connection), before)
            connection.rollback()
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
            self.assertEqual(_row_identity_snapshot(connection), identities)
            self.assertEqual(storage.current_schema_version(connection), 30)
            predecessor.validate_storage(connection)
            self.assert_idle(connection)

    def test_marker_mutation_of_legal_sqlitex_table_rolls_back(self):
        for mutation in ("rowid=-rowid", "payload=X'1122'"):
            with self.subTest(mutation=mutation), _source_copy(self.db) as connection, candidate_runtime(31):
                # Only sqlite_ is reserved; SQL LIKE's underscore wildcard must
                # not hide this legal predecessor table from preservation.
                connection.execute("CREATE TABLE sqliteXkeep (payload BLOB)")
                connection.execute("INSERT INTO sqliteXkeep(rowid,payload) VALUES (17,X'00FF')")
                connection.execute("CREATE TRIGGER unrelated_sqlitex_change AFTER INSERT ON schema_migrations "
                    f"WHEN NEW.version=31 BEGIN UPDATE sqliteXkeep SET {mutation}; END")
                connection.commit()
                predecessor.validate_storage(connection)
                before, identities = _logical_snapshot(connection), _row_identity_snapshot(connection)
                self.assertIn("sqliteXkeep", identities)
                with self.assertRaises(storage.StorageError):
                    schema.migrate(connection)
                self.assertEqual(_logical_snapshot(connection), before)
                self.assertEqual(_row_identity_snapshot(connection), identities)
                self.assertEqual(storage.current_schema_version(connection), 30)
                predecessor.validate_storage(connection)
                self.assert_idle(connection)

    def test_marker_change_to_autoincrement_sequence_rolls_back_next_identity(self):
        with _source_copy(self.db) as connection, candidate_runtime(31):
            connection.execute("CREATE TABLE unrelated_autoincrement "
                "(identity INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB)")
            connection.execute("INSERT INTO unrelated_autoincrement(identity,payload) VALUES (40,X'00FF')")
            connection.execute("CREATE TRIGGER unrelated_sequence_change AFTER INSERT ON schema_migrations "
                "WHEN NEW.version=31 BEGIN UPDATE sqlite_sequence SET seq=1000 "
                "WHERE name='unrelated_autoincrement'; END")
            connection.commit()
            predecessor.validate_storage(connection)
            before, identities = _logical_snapshot(connection), _row_identity_snapshot(connection)
            self.assertIn("sqlite_sequence", identities)
            with self.assertRaises(storage.StorageError):
                schema.migrate(connection)
            self.assertEqual(_logical_snapshot(connection), before)
            self.assertEqual(_row_identity_snapshot(connection), identities)
            self.assertEqual(storage.current_schema_version(connection), 30)
            predecessor.validate_storage(connection)
            self.assert_idle(connection)
            cursor = connection.execute("INSERT INTO unrelated_autoincrement(payload) VALUES (X'3344')")
            self.assertEqual(cursor.lastrowid, 41)
            connection.rollback()

    def test_nonowned_without_rowid_and_shadowed_alias_tables_remain_supported(self):
        with _source_copy(self.db) as connection, candidate_runtime(31):
            connection.execute("CREATE TABLE unrelated_without_rowid "
                '(key TEXT PRIMARY KEY, "rowid" TEXT, "_rowid_" TEXT, "oid" TEXT, '
                "payload BLOB) WITHOUT ROWID")
            connection.execute("INSERT INTO unrelated_without_rowid(key,payload) VALUES ('kept', X'00FF')")
            fixture_aliases = {}
            for name, definition, identity in (
                ("unrelated_shadow_one", '"RoWiD" TEXT, payload BLOB', "_rowid_"),
                ("unrelated_shadow_two", '"RoWiD" TEXT, "_RoWiD_" TEXT, payload BLOB', "oid"),
                ("unrelated_shadow_generated", 'payload BLOB, "rowid" INTEGER GENERATED ALWAYS AS '
                    '(length(payload)) STORED, "_rowid_" INTEGER GENERATED ALWAYS AS (42) VIRTUAL', "oid"),
                ("unrelated_shadow_all", '"rowid" TEXT, "_rowid_" TEXT, "oid" TEXT, '
                    'identity INTEGER PRIMARY KEY, payload BLOB', "identity"),
            ):
                connection.execute(f'CREATE TABLE "{name}" ({definition})')
                connection.executemany(f'INSERT INTO "{name}" ("{identity}",payload) VALUES (?,?)',
                    ((-17, b"before"), (41, b"after")))
                fixture_aliases[name] = identity
            connection.execute("CREATE INDEX unrelated_shadow_all_payload ON unrelated_shadow_all(payload)")
            connection.commit()
            predecessor.validate_storage(connection)
            tables = tuple(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND lower(name) NOT GLOB 'sqlite_*' AND name!='schema_migrations' ORDER BY name"))
            before = storage._selected_table_projection_snapshot(connection, tables)
            identities = _row_identity_snapshot(connection, fixture_aliases=fixture_aliases)
            self.assertNotIn("unrelated_without_rowid", identities)
            self.assertTrue(schema.migrate(connection))
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tables), before)
            self.assertEqual(_row_identity_snapshot(connection, fixture_aliases=fixture_aliases), identities)
            schema.validate_storage(connection)
            self.assert_idle(connection)

    def test_marker_mutation_uses_unshadowed_rowid_alias_for_identity_rollback(self):
        for definition, identity in (
            ('"RoWiD" TEXT, payload BLOB', "_rowid_"),
            ('"RoWiD" TEXT, "_RoWiD_" TEXT, payload BLOB', "oid"),
            ('payload BLOB, "rowid" INTEGER GENERATED ALWAYS AS (length(payload)) STORED, '
                '"_rowid_" INTEGER GENERATED ALWAYS AS (42) VIRTUAL', "oid"),
        ):
            with self.subTest(definition=definition), _source_copy(self.db) as connection, candidate_runtime(31):
                connection.execute(f'CREATE TABLE unrelated_shadowed_identity ({definition})')
                connection.execute(f'INSERT INTO unrelated_shadowed_identity("{identity}",payload) '
                    "VALUES (17, X'00FF')")
                connection.execute("CREATE TRIGGER unrelated_shadowed_identity_change "
                    "AFTER INSERT ON schema_migrations WHEN NEW.version=31 BEGIN "
                    f'UPDATE unrelated_shadowed_identity SET "{identity}"=-"{identity}"; END')
                connection.commit()
                predecessor.validate_storage(connection)
                aliases = {"unrelated_shadowed_identity": identity}
                before = _logical_snapshot(connection)
                identities = _row_identity_snapshot(connection, fixture_aliases=aliases)
                with self.assertRaises(storage.StorageError):
                    schema.migrate(connection)
                self.assertEqual(_logical_snapshot(connection), before)
                self.assertEqual(_row_identity_snapshot(connection, fixture_aliases=aliases), identities)
                self.assertEqual(storage.current_schema_version(connection), 30)
                predecessor.validate_storage(connection)
                self.assert_idle(connection)

    def test_inaccessible_hidden_identity_fails_before_delta_without_changing_admission(self):
        for primary_key in ("identity TEXT PRIMARY KEY", "identity INTEGER PRIMARY KEY DESC"):
            with self.subTest(primary_key=primary_key), _source_copy(self.db) as connection, candidate_runtime(31):
                connection.execute('CREATE TABLE unrelated_inaccessible_identity '
                    f'("RoWiD" TEXT, "_RoWiD_" TEXT, "OiD" TEXT, {primary_key}, payload BLOB)')
                connection.execute("INSERT INTO unrelated_inaccessible_identity(identity,payload) VALUES (17,X'00FF')")
                connection.commit()
                # Complete schema-30 admission allows this unrelated table. The
                # repair may not infer that a DESC or text PK aliases its rowid.
                predecessor.validate_storage(connection)
                before = _logical_snapshot(connection)
                statements = []
                connection.set_trace_callback(statements.append)
                try:
                    with mock.patch.object(schema, "_apply_delta_locked", wraps=schema._apply_delta_locked) as apply:
                        with self.assertRaises(storage.StorageError):
                            schema.migrate(connection)
                        apply.assert_not_called()
                finally:
                    connection.set_trace_callback(None)
                self.assertEqual(_mutating_statements(statements), ())
                self.assertEqual(_logical_snapshot(connection), before)
                self.assertEqual(storage.current_schema_version(connection), 30)
                predecessor.validate_storage(connection)
                self.assert_idle(connection)

    def test_unowned_rebuilt_table_attachment_is_rejected_without_mutation(self):
        for table in schema.REBUILT_TABLES:
            with self.subTest(table=table), _source_copy(self.db) as connection, candidate_runtime(31):
                connection.execute(f'CREATE INDEX unexpected_collector_attachment ON "{table}"(task_id)')
                connection.commit()
                before = _logical_snapshot(connection)
                with self.assertRaises(storage.StorageError):
                    schema.migrate(connection)
                self.assertEqual(_logical_snapshot(connection), before)
                self.assert_idle(connection)

    def test_unknown_producer_stays_rejected_by_sql_check_after_migration(self):
        with _source_copy(self.db) as connection, candidate_runtime(31):
            schema.migrate(connection)
            row = dict(connection.execute("SELECT * FROM criterion_evidence_links LIMIT 1").fetchone())
            row.update(criterion_evidence_link_id="tg_criterion_evidence_link_ffffffffffffffff",
                producer_class="unrecognized_collector")
            columns = tuple(row)
            before = _logical_snapshot(connection)
            statement = "INSERT INTO criterion_evidence_links (" + ",".join(columns) + ") VALUES (" + \
                ",".join("?" for _ in columns) + ")"
            parameters = tuple(row[column] for column in columns)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "invalid_criterion_evidence_link"):
                connection.execute(statement, parameters)
            connection.rollback()
            self.assertEqual(_logical_snapshot(connection), before)
            # The matrix trigger rejects first. In this disposable copy only,
            # remove it transactionally to exercise the independent DDL CHECK.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TRIGGER trg_criterion_evidence_links_matrix_insert")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "CHECK constraint failed.*producer_class"):
                connection.execute(statement, parameters)
            connection.rollback()
            self.assertEqual(_logical_snapshot(connection), before)
            schema.validate_storage(connection)


class PublicCriterionLinkCollectorSetupTests(unittest.TestCase):
    def cli(self, *arguments):
        result = self.install.run(*arguments, "--json")
        self.assertTrue(result.stdout, (result.returncode, result.stderr))
        value = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, value)
        return value["data"]

    def business(self, connection):
        # Setup owns maintenance/publication/backup metadata; these business
        # rows, including complete frozen Evidence, must remain byte-exact.
        names = ("tasks", "task_events", "task_contract_revisions", "contract_criteria",
            "authority_snapshots", "review_receipts", "review_receipt_provenance_v2",
            "review_collection_sources", "review_collection_dispatches", "review_collection_dispatch_entries",
            "criterion_evidence_links", "evidence_references", "task_completion_cycles",
            "completion_evidence_bundles")
        return storage._selected_table_projection_snapshot(connection, names)

    def test_public_setup_migrates_populated30_and_recovers30_and_current_backups(self):
        from task_governance_tool import backup
        from task_governance_tool.viewer import build_viewer_snapshot
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-public-collector-setup-")
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name))
        self.enterContext(mock.patch.dict(os.environ,
            {"CODEX_THREAD_ID": "00000000-0000-4000-8000-000000000001"}))
        storage_path = self.install.skill_root / "scripts/task_governance_tool/storage.py"
        current = storage_path.read_text(encoding="utf-8")
        self.assertEqual(current.count("SCHEMA_VERSION = 34"), 1)
        # Pin only the disposable predecessor binary. Actual upgrade/recovery
        # below use the restored, unpatched physical package and public CLI.
        storage_path.write_text(current.replace("SCHEMA_VERSION = 34", "SCHEMA_VERSION = 30", 1),
            encoding="utf-8", newline="\n")
        refresh_test_manifest(self.install.skill_root)
        self.assertEqual(self.cli("setup")["schema_to"], 30)
        historical = self.cli("task", "add", "--title", "Preserved schema30 completion",
            "--status", "in_progress", "--review-tier", "0", "--verification-not-required",
            "Historical fixture only")["task"]["task_id"]
        self.cli("review", "target", "set", historical, "--kind", "diff_fingerprint",
            "--revision", "sha256:" + "a" * 64)
        self.cli("review", "receipt", "add", historical, "--reviewer", "Mechanical fixture",
            "--kind", "not_required", "--verdict", "not_required", "--summary", "Historical fixture review")
        self.cli("task", "complete", historical, "--verification-complete", "--review-complete", "--commit-not-required")
        active = self.cli("task", "add", "--title", "Contract-bearing collected schema30 state",
            "--status", "ready", "--review-tier", "2", "--verification", "Run isolated checks")["task"]["task_id"]
        self.cli("task", "edit", active, "--contract-scope", "Preserve collected answers",
            "--contract-acceptance", "Two independent collected passes", "--contract-authority-ref",
            "user:isolated-public-setup", "--status", "in_progress")
        self.cli("review", "target", "set", active, "--kind", "diff_fingerprint",
            "--revision", "sha256:" + "b" * 64)
        with candidate_runtime(30):
            fixture = attach_existing(db=self.install.db_path, repo=self.install.project_root,
                task_id=active, schema_version=30, target=self.install.target)
        with candidate_runtime(30), closing(storage.connect_initialized(fixture.target)) as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (active,)).fetchone()
            for index in (1, 2):
                fixture.add(connection, index)
            connection.commit()
            before = self.business(connection)
            _, bundles = _bundle_artifacts(connection, task["project_id"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_receipt_provenance_v2").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT current_contract_revision FROM tasks WHERE task_id=?", (active,)).fetchone()[0], 1)
        storage_path.write_text(current, encoding="utf-8", newline="\n")
        refresh_test_manifest(self.install.skill_root)
        state = self.install.state_snapshot()
        preview = self.cli("setup", "--read-only")
        self.assertEqual((preview["schema_from"], preview["schema_to"]), (30, 34))
        self.assertEqual(preview["completed_writes"], [])
        self.assertEqual(self.install.state_snapshot(), state)
        migrated = self.cli("setup")
        self.assertEqual((migrated["schema_from"], migrated["schema_to"]), (30, 34))
        self.assertEqual(migrated["completed_writes"], ["migration_backup", "database_migrate",
            "evidence_projection_publish", "viewer_publish"])
        paths = tuple((self.install.fixed_root / "backups").glob("taskgov-backup-v1_*.sqlite"))
        matching = []
        for path in paths:
            with closing(storage.connect_readonly(path)) as connection:
                if storage.current_schema_version(connection) == 30 and self.business(connection) == before:
                    with candidate_runtime(30):
                        predecessor.validate_storage(connection)
                        _, preserved = _bundle_artifacts(connection, fixture.basis["project_id"])
                    self.assertEqual(preserved, bundles)
                    matching.append(path)
        self.assertTrue(matching, "public Setup must retain the exact populated schema30 migration backup")

        def assert_preserved_current():
            with closing(storage.connect_initialized_readonly(self.install.target)) as connection:
                self.assertEqual(storage.current_schema_version(connection), 34)
                self.assertEqual(self.business(connection), before)
                _, preserved = _bundle_artifacts(connection, fixture.basis["project_id"])
                self.assertEqual(preserved, bundles)
                snapshot = build_viewer_snapshot(connection, self.install.target).snapshot
                self.assertEqual(snapshot["source_schema_version"], 34)
                self.assertEqual(len(snapshot["tasks"]), 2)
            self.assertTrue(self.install.viewer_path.is_file())
            state = self.install.state_snapshot()
            self.cli("doctor")
            self.assertEqual(self.install.state_snapshot(), state)

        assert_preserved_current()
        # Missing-primary recovery is intentional only in this disposable tree.
        self.install.db_path.unlink()
        missing = self.install.state_snapshot()
        preview = self.cli("setup", "--read-only")
        self.assertEqual((preview["schema_from"], preview["schema_to"]), (30, 34))
        self.assertEqual(self.install.state_snapshot(), missing)
        recovered = self.cli("setup")
        self.assertEqual((recovered["schema_from"], recovered["schema_to"]), (30, 34))
        self.assertEqual(recovered["completed_writes"], ["database_restore", "migration_backup",
            "database_migrate", "evidence_projection_publish", "viewer_publish"])
        assert_preserved_current()
        with backup.managed_backup_lock(self.install.target):
            backup.publish_setup_backup(self.install.target, 3)
        self.install.db_path.unlink()
        recovered = self.cli("setup")
        self.assertEqual((recovered["schema_from"], recovered["schema_to"]), (34, 34))
        self.assertEqual(recovered["completed_writes"], ["database_restore", "evidence_projection_publish", "viewer_publish"])
        assert_preserved_current()


if __name__ == "__main__":
    unittest.main()
