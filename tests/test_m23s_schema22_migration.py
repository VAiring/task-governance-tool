from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing, contextmanager
from unittest import mock

from tests import test_m23s_schema22_definitions as definitions
from tests import test_m23s_schema22_validation as validation_fixture


storage = definitions.storage
REBUILT_TABLES = (
    "evidence_references",
    "criterion_evidence_links",
    "completion_evidence_bundles",
)
TEMPORARY_TABLES = tuple(f"{table}_v21" for table in REBUILT_TABLES)


def _rows(connection, *, include_markers=True):
    result = {}
    for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        table = str(row[0])
        if not include_markers and table == "schema_migrations":
            continue
        columns = tuple(str(item[1]) for item in connection.execute(f'PRAGMA table_info("{table}")'))
        projection = ", ".join(f'"{column}"' for column in columns)
        values = tuple(sorted(
            (tuple(item) for item in connection.execute(f'SELECT {projection} FROM "{table}"')),
            key=repr,
        ))
        result[table] = (columns, values)
    return result


def _logical_snapshot(connection):
    # Unlike a JSON-only snapshot, this retains unrelated BLOB storage values.
    objects = tuple(tuple(row) for row in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ))
    return objects, _rows(connection)


def _mutating_statements(statements):
    prefixes = ("CREATE ", "ALTER ", "DROP ", "INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
    return tuple(statement for statement in statements if statement.lstrip().upper().startswith(prefixes))


@contextmanager
def _source_copy(path, *, factory=sqlite3.Connection):
    with closing(storage.connect_readonly(path)) as source:
        with closing(storage.configure_connection(sqlite3.connect(":memory:", factory=factory))) as connection:
            source.backup(connection)
            yield connection


def _add_unrelated_objects(connection):
    connection.execute("CREATE TABLE unrelated_m23s_fixture (fixture_id TEXT PRIMARY KEY, content BLOB, note TEXT)")
    connection.execute("CREATE INDEX unrelated_m23s_lookup ON unrelated_m23s_fixture(note)")
    connection.execute(
        "CREATE TRIGGER unrelated_m23s_guard AFTER INSERT ON unrelated_m23s_fixture "
        "BEGIN SELECT NEW.fixture_id; END"
    )
    connection.execute(
        "INSERT INTO unrelated_m23s_fixture VALUES (?, ?, ?)",
        ("retained", sqlite3.Binary(b"\x00\xff\x01schema22"), "unchanged"),
    )
    connection.commit()


class _RebuildFailureConnection(sqlite3.Connection):
    fail_during_copy = False
    failed_with_temporary_tables = ()

    def execute(self, statement, parameters=()):
        normalized = " ".join(statement.replace('"', '').split()).upper()
        if self.fail_during_copy and normalized.startswith("INSERT INTO CRITERION_EVIDENCE_LINKS "):
            self.fail_during_copy = False
            self.failed_with_temporary_tables = tuple(
                str(row[0]) for row in super().execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN (?, ?, ?) ORDER BY name", TEMPORARY_TABLES
                )
            )
            raise sqlite3.OperationalError("test-only copy interruption")
        return super().execute(statement, parameters)


class Schema22MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Compose only fixture setup; do not inherit/discover another test suite.
        cls.source_fixture = validation_fixture.Schema22StoredValidationTests
        cls.source_fixture.setUpClass()
        cls.addClassCleanup(cls.source_fixture.doClassCleanups)
        cls.target = cls.source_fixture.target

    def assert_restored_connection(self, connection):
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def assert_preflight_rejection(self, connection):
        before = _logical_snapshot(connection)
        statements = []
        connection.set_trace_callback(statements.append)
        try:
            with self.assertRaises(storage.StorageError):
                storage._migrate_schema22_connection(connection)
        finally:
            connection.set_trace_callback(None)
        self.assertEqual(_mutating_statements(statements), ())
        self.assertEqual(_logical_snapshot(connection), before)
        self.assert_restored_connection(connection)

    def assert_migration_preserves_history(self, connection, project_id):
        storage.validate_schema21_storage(connection)
        rows = _rows(connection, include_markers=False)
        original_basis, original_artifacts = validation_fixture._bundle_artifacts(connection, project_id)
        original_histories = {
            cycle.task_id: storage.read_completion_history(
                connection, project_id=project_id, task_id=cycle.task_id
            )
            for cycle in original_basis.cycles
        }
        statements = []
        connection.set_trace_callback(statements.append)
        try:
            self.assertTrue(storage._migrate_schema22_connection(connection))
        finally:
            connection.set_trace_callback(None)
        writes = _mutating_statements(statements)
        self.assertTrue(writes)
        self.assertTrue(writes[-1].lstrip().upper().startswith("INSERT INTO SCHEMA_MIGRATIONS"))
        self.assertEqual(storage.current_schema_version(connection), 22)
        self.assertEqual(_rows(connection, include_markers=False), rows)
        self.assertEqual(tuple(connection.execute(
            "SELECT version, name FROM schema_migrations WHERE version=22"
        ).fetchone()), (22, "evidence_reservation_cleanup"))
        self.assertEqual([row[0] for row in connection.execute("PRAGMA quick_check")], ["ok"])
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN (?, ?, ?)", TEMPORARY_TABLES
        ).fetchall(), [])
        self.assert_restored_connection(connection)
        storage.validate_schema22_storage(connection)
        basis, artifacts = validation_fixture._bundle_artifacts(connection, project_id)
        self.assertEqual(basis.source_schema_version, 22)
        self.assertEqual(basis.cycles, original_basis.cycles)
        self.assertEqual(basis.bundles, original_basis.bundles)
        self.assertEqual(set(artifacts), set(original_artifacts))
        for bundle_id, artifact in artifacts.items():
            original = original_artifacts[bundle_id]
            self.assertEqual(artifact.payload_bytes, original.payload_bytes)
            self.assertEqual(artifact.document, original.document)
            self.assertEqual(artifact.bundle_digest, original.bundle_digest)
            self.assertEqual(artifact.payload, original.payload)
        for task_id, history in original_histories.items():
            self.assertEqual(storage.read_completion_history(
                connection, project_id=project_id, task_id=task_id
            ), history)
        return artifacts

    def test_populated_manual_runner_history_and_unrelated_objects_survive(self):
        with _source_copy(self.target.db_path) as connection:
            _add_unrelated_objects(connection)
            unrelated_before = tuple(tuple(row) for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name LIKE 'unrelated_m23s_%' ORDER BY type, name"
            ))
            artifacts = self.assert_migration_preserves_history(connection, self.target.project.project_id)
            self.assertEqual({artifact.payload["source_schema_version"] for artifact in artifacts.values()}, {21})
            self.assertEqual({artifact.payload["verification_basis"]["kind"] for artifact in artifacts.values()},
                             {"caller_attestation", "runner_observation"})
            self.assertEqual(tuple(tuple(row) for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name LIKE 'unrelated_m23s_%' ORDER BY type, name"
            )), unrelated_before)
            self.assertEqual(connection.execute(
                "SELECT typeof(content), content FROM unrelated_m23s_fixture"
            ).fetchone()[0], "blob")
            migrated_fingerprint = storage._owned_schema_sql_fingerprint(connection, schema_version=22)
            with definitions._empty_public_database() as (_initialized, fresh):
                definitions._construct_private_schema22(fresh)
                self.assertEqual(migrated_fingerprint, storage._owned_schema_sql_fingerprint(fresh, schema_version=22))
            before = _logical_snapshot(connection)
            connection.execute("PRAGMA query_only = ON")
            try:
                self.assertFalse(storage._migrate_schema22_connection(connection))
            finally:
                connection.execute("PRAGMA query_only = OFF")
            self.assertEqual(_logical_snapshot(connection), before)
            self.assert_restored_connection(connection)

    def test_retained_source19_and_source20_bundles_survive_migration(self):
        fixture = validation_fixture.schema19_fixture.R3ASchema20StorageTests(
            "test_private_migration_inventory_preservation_and_reentry"
        )
        fixture.root = self.source_fixture.root / "migration-source19"
        db19 = fixture._fresh_v19("retained")
        with closing(storage.connect(db19)) as connection:
            basis19, original19 = fixture._seed_nonempty_v19_closure(connection)
        storage.rehearse_schema20_storage(db19)
        storage.rehearse_schema21_storage(db19)
        with _source_copy(db19) as connection:
            artifacts = self.assert_migration_preserves_history(connection, str(basis19["project_id"]))
            artifact = next(iter(artifacts.values()))
            self.assertEqual(artifact.payload_bytes, original19)
            self.assertEqual((artifact.payload["source_schema_version"], artifact.payload["bundle_version"]), (19, 1))

        fixture20 = validation_fixture.schema20_fixture
        fixture20._start_schema20_runtime_oracle()
        try:
            _install, target = fixture20._fixed_current20(
                self.source_fixture.root / "migration-source20", identity_seed="migration-retained20"
            )
            task_id = fixture20._add_task(self, target, title="Preserve source20 Bundle", verification="")
            fixture20._seed_completion_gates(
                self, target, task_id, fingerprint="sha256:" + "a" * 64,
                verification_required=False,
            )
            fixture20._complete_task(self, target, task_id)
        finally:
            fixture20._stop_schema20_runtime_oracle()
        storage.rehearse_schema21_storage(target.db_path)
        with _source_copy(target.db_path) as connection:
            artifacts = self.assert_migration_preserves_history(connection, target.project.project_id)
            artifact = next(iter(artifacts.values()))
            self.assertEqual((artifact.payload["source_schema_version"], artifact.payload["bundle_version"]), (20, 2))
            self.assertEqual(artifact.payload["verification_basis"]["kind"], "not_required")

    def test_each_rebuilt_table_rejects_unowned_index_and_trigger_before_ddl(self):
        for table in REBUILT_TABLES:
            for kind in ("index", "trigger"):
                with self.subTest(table=table, kind=kind), _source_copy(self.target.db_path) as connection:
                    if kind == "index":
                        connection.execute(f"CREATE INDEX unowned_m23s_index ON {table}(task_id)")
                    else:
                        connection.execute(
                            f"CREATE TRIGGER unowned_m23s_trigger AFTER INSERT ON {table} BEGIN SELECT 1; END"
                        )
                    connection.commit()
                    self.assert_preflight_rejection(connection)
        with _source_copy(self.target.db_path) as connection:
            self.assertTrue(storage._migrate_schema22_connection(connection))
            connection.execute(
                "CREATE TRIGGER unowned_reentry_guard AFTER INSERT ON criterion_evidence_links "
                "BEGIN SELECT 1; END"
            )
            connection.commit()
            self.assert_preflight_rejection(connection)

    def test_reserved_rows_are_rejected_without_conversion_or_business_ddl(self):
        for table, column, value, selector in (
            ("evidence_references", "source_kind", "derived_analysis", "source_kind='verification_receipt'"),
            ("evidence_references", "assurance_class", "llm_derived", "source_kind='verification_receipt'"),
            ("evidence_references", "producer_class", "batch_analyzer", "source_kind='verification_receipt'"),
            ("criterion_evidence_links", "relation", "derived_analysis", "relation='verification_attestation'"),
        ):
            with self.subTest(table=table, column=column), _source_copy(self.target.db_path) as connection:
                trigger_name = f"trg_{table}_no_update"
                trigger = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (trigger_name,)
                ).fetchone()[0]
                connection.execute(f'DROP TRIGGER "{trigger_name}"')
                changed = connection.execute(
                    f"UPDATE {table} SET {column}=? WHERE task_id=? AND {selector}",
                    (value, self.source_fixture.manual_task),
                )
                self.assertEqual(changed.rowcount, 1)
                connection.execute(trigger)
                connection.commit()
                self.assert_preflight_rejection(connection)

    def test_temporary_name_collisions_and_other_source_versions_reject(self):
        for name in TEMPORARY_TABLES:
            with self.subTest(name=name), _source_copy(self.target.db_path) as connection:
                connection.execute(f'CREATE TABLE "{name}"(retained BLOB)')
                connection.commit()
                self.assert_preflight_rejection(connection)
        for version in (20, 23):
            with self.subTest(version=version), _source_copy(self.target.db_path) as connection:
                if version == 20:
                    connection.execute("DELETE FROM schema_migrations WHERE version=21")
                else:
                    connection.execute("INSERT INTO schema_migrations VALUES (23, 'future_fixture', '2026-09-04T00:00:00Z')")
                connection.commit()
                self.assert_preflight_rejection(connection)

    def test_rebuild_failure_rolls_back_rows_schema_and_connection_settings(self):
        with _source_copy(self.target.db_path, factory=_RebuildFailureConnection) as connection:
            _add_unrelated_objects(connection)
            before = _logical_snapshot(connection)
            connection.fail_during_copy = True
            with self.assertRaises(storage.StorageError):
                storage._migrate_schema22_connection(connection)
            self.assertEqual(connection.failed_with_temporary_tables, tuple(sorted(TEMPORARY_TABLES)))
            self.assertEqual(_logical_snapshot(connection), before)
            self.assertEqual(storage.current_schema_version(connection), 21)
            self.assert_restored_connection(connection)
            storage.validate_schema21_storage(connection)

    def test_post_marker_failure_rolls_back_before_commit(self):
        original_validate = storage.validate_schema22_storage
        observed_versions = []

        def reject_after_marker(connection, **kwargs):
            original_validate(connection, **kwargs)
            observed_versions.append(storage.current_schema_version(connection))
            self.assertTrue(connection.in_transaction)
            raise storage.StorageError("project_state_unreadable", "test-only precommit interruption")

        with _source_copy(self.target.db_path) as connection:
            _add_unrelated_objects(connection)
            before = _logical_snapshot(connection)
            with mock.patch.object(storage, "validate_schema22_storage", side_effect=reject_after_marker):
                with self.assertRaises(storage.StorageError):
                    storage._migrate_schema22_connection(connection)
            self.assertEqual(observed_versions, [22])
            self.assertEqual(_logical_snapshot(connection), before)
            self.assertEqual(storage.current_schema_version(connection), 21)
            self.assert_restored_connection(connection)
            storage.validate_schema21_storage(connection)

    def test_marker_trigger_business_mutation_rolls_back_with_source_objects(self):
        original_validate = storage.validate_schema22_storage
        observed_values = []

        def observe_post_marker(connection, **kwargs):
            original_validate(connection, **kwargs)
            observed_values.append(connection.execute(
                "SELECT note FROM unrelated_m23s_fixture WHERE fixture_id='retained'"
            ).fetchone()[0])

        with _source_copy(self.target.db_path) as connection:
            _add_unrelated_objects(connection)
            connection.execute(
                "CREATE TRIGGER unrelated_m23s_marker_write AFTER INSERT ON schema_migrations "
                "WHEN NEW.version=22 BEGIN UPDATE unrelated_m23s_fixture "
                "SET note='marker mutation' WHERE fixture_id='retained'; END"
            )
            connection.commit()
            storage.validate_schema21_storage(connection)
            before = _logical_snapshot(connection)
            with mock.patch.object(storage, "validate_schema22_storage", side_effect=observe_post_marker):
                with self.assertRaises(storage.StorageError):
                    storage._migrate_schema22_connection(connection)
            self.assertEqual(observed_values, ["marker mutation"])
            self.assertEqual(_logical_snapshot(connection), before)
            self.assertEqual(storage.current_schema_version(connection), 21)
            self.assert_restored_connection(connection)
            storage.validate_schema21_storage(connection)

    def test_public_initialization_and_migration_dispatch_remain_schema21(self):
        with definitions._empty_public_database() as (initialized, connection):
            self.assertEqual(storage.SCHEMA_VERSION, 21)
            self.assertEqual(initialized.schema_version, 21)
            before = _logical_snapshot(connection)
            self.assertEqual(storage.apply_migrations(connection), ([], []))
            self.assertEqual(_logical_snapshot(connection), before)


if __name__ == "__main__":
    unittest.main()
