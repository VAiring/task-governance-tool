from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from unittest import mock

from tests import test_m242_r3a_schema20_storage as schema19_fixture
from tests import test_m242_r3b_schema20_activation as schema20_fixture
from tests import test_m243b_schema21_compatibility as schema21_fixture
from tests.m223_test_support import logical_database_digest
from tests.test_m23s_schema22_definitions import ROOT, storage
from task_governance_tool.evidence_projection import build_projection_bundle_artifact
from task_governance_tool.verification_runner import verification_runner_attempt_digest


def _bundle_artifacts(connection, project_id: str):
    # Observe an explicit historical21 fixture through its old current-only
    # capture seam; actual22 and unsupported versions retain public admission.
    legacy_capture = (
        mock.patch.object(storage, "SCHEMA_VERSION", 21)
        if storage.current_schema_version(connection) == 21 else nullcontext()
    )
    with legacy_capture:
        basis = storage.capture_evidence_projection_basis(
            connection, project_id=project_id
        )
    return basis, {
        record.bundle.completion_evidence_bundle_id:
        build_projection_bundle_artifact(record)
        for record in basis.native_bundles
    }


@contextmanager
def _schema22_container(source):
    """Load existing fixture rows into new v22 DDL, not a migration algorithm."""
    storage.validate_schema21_storage(source)
    owned_names = {
        name
        for inventory in (
            storage._SCHEMA_TABLE_INTRODUCED_VERSION,
            storage._SCHEMA_INDEX_INTRODUCED_VERSION,
            storage._SCHEMA_TRIGGER_INTRODUCED_VERSION,
        )
        for name in inventory
    }
    objects = {
        str(row["name"]): (str(row["type"]), str(row["sql"]))
        for row in source.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
        if str(row["name"]) in owned_names
    }
    if set(objects) != owned_names:
        raise AssertionError("source21 fixture is missing an owned schema object")
    replacements = {
        storage._schema20_statement_identity(statement)[1]: statement
        for statement in storage._schema22_replacement_statements()
    }
    with closing(storage.configure_connection(sqlite3.connect(":memory:"))) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        tables = sorted(name for name, (kind, _sql) in objects.items() if kind == "table")
        for table in tables:
            connection.execute(replacements.get(table, objects[table][1]))
        # Historical rows precede insert guards: no current-cycle synthesis,
        # row conversion, source-version rewrite, or public schema switch.
        for table in tables:
            columns = tuple(
                str(row["name"])
                for row in source.execute(f'PRAGMA table_info("{table}")')
            )
            projection = ", ".join(f'"{column}"' for column in columns)
            rows = [tuple(row) for row in source.execute(f'SELECT {projection} FROM "{table}"')]
            connection.executemany(
                f'INSERT INTO "{table}" ({projection}) '
                f'VALUES ({", ".join("?" for _ in columns)})',
                rows,
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (22, storage.PRIVATE_SCHEMA22_MIGRATION_NAME, "2026-09-04T00:00:00Z"),
        )
        for name, (kind, statement) in objects.items():
            if kind != "table":
                connection.execute(replacements.get(name, statement))
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        storage._validate_schema22_owned_contract(connection)
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise AssertionError("schema22 historical fixture has broken foreign keys")
        yield connection


@contextmanager
def _changed_immutable_row(connection, table: str, statement: str, parameters):
    """Expose corrupt stored data with all original DDL restored during reads."""
    trigger_name = f"trg_{table}_no_update"
    trigger = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
        (trigger_name,),
    ).fetchone()
    if trigger is None:
        raise AssertionError("fixture immutability guard is missing")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(f'DROP TRIGGER "{trigger_name}"')
        connection.execute("PRAGMA ignore_check_constraints = ON")
        changed = connection.execute(statement, parameters)
        if changed.rowcount != 1:
            raise AssertionError("corruption fixture must change exactly one row")
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        connection.execute(str(trigger["sql"]))
        storage._validate_schema22_owned_contract(connection)
        yield
    finally:
        connection.rollback()
        connection.execute("PRAGMA ignore_check_constraints = OFF")


class Schema22StoredValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix=".tmp-m23s-history-", dir=ROOT)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        testcase = unittest.TestCase()
        cls.install, cls.target, cls.manual_task = schema21_fixture._seed_completed_m21_fixture(
            testcase, cls.root / "source21"
        )
        with closing(storage.connect(cls.target.db_path)) as connection:
            commit = connection.execute(
                "SELECT review_target_value FROM tasks WHERE task_id=?",
                (cls.manual_task,),
            ).fetchone()[0]
        cls.runner_task = schema21_fixture._add_completed_m21_task(
            testcase, cls.install, title="Retained Runner history", commit=str(commit)
        )
        with closing(storage.connect(cls.target.db_path)) as connection:
            basis, artifacts = _bundle_artifacts(connection, cls.target.project.project_id)
            runner_record = next(
                record for record in basis.native_bundles
                if record.bundle.task_id == cls.runner_task
            )
            schema21_fixture._persist_later_runner_history_fixture(
                connection,
                task_id=cls.runner_task,
                original_payload=artifacts[
                    runner_record.bundle.completion_evidence_bundle_id
                ].payload,
            )
            storage.validate_schema21_storage(connection)

    def _assert_preserved_projection(self, source, connection, project_id: str):
        before_basis, before = _bundle_artifacts(source, project_id)
        snapshot = logical_database_digest(connection)
        connection.execute("PRAGMA query_only = ON")
        try:
            storage.validate_schema22_storage(connection)
            after_basis, after = _bundle_artifacts(connection, project_id)
            self.assertEqual(before_basis.source_schema_version, 21)
            self.assertEqual(after_basis.source_schema_version, 22)
            self.assertEqual(after_basis.cycles, before_basis.cycles)
            self.assertEqual(after_basis.bundles, before_basis.bundles)
            self.assertEqual(set(after), set(before))
            for bundle_id, artifact in after.items():
                self.assertEqual(artifact.payload_bytes, before[bundle_id].payload_bytes)
                self.assertEqual(artifact.document, before[bundle_id].document)
                self.assertEqual(artifact.bundle_digest, before[bundle_id].bundle_digest)
                self.assertEqual(artifact.payload, before[bundle_id].payload)
            for task_id in {cycle.task_id for cycle in after_basis.cycles}:
                original = storage.read_completion_history(
                    source, project_id=project_id, task_id=task_id
                )
                retained = storage.read_completion_history(
                    connection, project_id=project_id, task_id=task_id
                )
                self.assertEqual(retained, original)
            self.assertEqual(logical_database_digest(connection), snapshot)
            self.assertEqual(storage.SCHEMA_VERSION, 22)
            return after
        finally:
            connection.execute("PRAGMA query_only = OFF")

    def test_source21_manual_and_runner_history_remain_sealed_and_read_only(self):
        with closing(storage.connect(self.target.db_path)) as source, _schema22_container(source) as connection:
            artifacts = self._assert_preserved_projection(
                source, connection, self.target.project.project_id
            )
            payloads = {
                artifact.payload["task"]["task_id"]: artifact.payload
                for artifact in artifacts.values()
            }
            manual = payloads[self.manual_task]
            runner = payloads[self.runner_task]
            self.assertEqual(manual["source_schema_version"], 21)
            self.assertEqual(manual["verification_basis"]["kind"], "caller_attestation")
            self.assertIsNone(manual["runner_observation"])
            self.assertNotIn("command_label", manual["verification_receipt"])
            self.assertEqual(runner["source_schema_version"], 21)
            self.assertEqual(runner["verification_basis"]["kind"], "runner_observation")
            self.assertIsNone(runner["verification_receipt"])
            for prohibited in ("stdout", "stderr", "argv", "environment", "credentials", "private_path"):
                self.assertNotIn(prohibited, runner["runner_observation"])

    def test_source19_and_source20_bundles_keep_their_original_versions(self):
        fixture = schema19_fixture.R3ASchema20StorageTests(
            "test_private_migration_inventory_preservation_and_reentry"
        )
        fixture.root = self.root / "source19"
        db19 = fixture._fresh_v19("retained")
        with closing(storage.connect(db19)) as source:
            basis19, original19_bytes = fixture._seed_nonempty_v19_closure(source)
        storage.rehearse_schema20_storage(db19)
        storage.rehearse_schema21_storage(db19)
        with closing(storage.connect(db19)) as source, _schema22_container(source) as connection:
            artifacts = self._assert_preserved_projection(source, connection, str(basis19["project_id"]))
            self.assertEqual(len(artifacts), 1)
            artifact = next(iter(artifacts.values()))
            self.assertEqual(artifact.payload_bytes, original19_bytes)
            self.assertEqual((artifact.payload["source_schema_version"], artifact.payload["bundle_version"]), (19, 1))

        schema20_fixture._start_schema20_runtime_oracle()
        try:
            _install20, target20 = schema20_fixture._fixed_current20(
                self.root / "source20", identity_seed="retained-source20"
            )
            task_id = schema20_fixture._add_task(
                self, target20, title="Retained not-required source20", verification=""
            )
            schema20_fixture._seed_completion_gates(
                self, target20, task_id,
                fingerprint="sha256:" + "a" * 64, verification_required=False,
            )
            schema20_fixture._complete_task(self, target20, task_id)
        finally:
            schema20_fixture._stop_schema20_runtime_oracle()
        storage.rehearse_schema21_storage(target20.db_path)
        with closing(storage.connect(target20.db_path)) as source, _schema22_container(source) as connection:
            artifacts = self._assert_preserved_projection(source, connection, target20.project.project_id)
            self.assertEqual(len(artifacts), 1)
            payload = next(iter(artifacts.values())).payload
            self.assertEqual((payload["source_schema_version"], payload["bundle_version"]), (20, 2))
            self.assertEqual(payload["verification_basis"]["kind"], "not_required")
            self.assertIsNone(payload["verification_receipt"])
            self.assertIsNone(payload["runner_observation"])

    def test_selected_history_rejects_bad_digest_and_source_version_in_schema22(self):
        with closing(storage.connect(self.target.db_path)) as source, _schema22_container(source) as connection:
            for task_id, column, value in (
                (self.manual_task, "bundle_digest", "sha256:" + "0" * 64),
                (self.runner_task, "bundle_digest", "sha256:" + "0" * 64),
                (self.manual_task, "source_schema_version", 23),
                (self.manual_task, "verification_basis_kind", None),
            ):
                with self.subTest(task=task_id, column=column), _changed_immutable_row(
                    connection, "completion_evidence_bundles",
                    f"UPDATE completion_evidence_bundles SET {column}=? WHERE task_id=?",
                    (value, task_id),
                ):
                    if column == "verification_basis_kind":
                        # A matching null cycle tag must not turn a source21
                        # Bundle into a preserved source19/v1 arm.
                        cycle_trigger = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE name="
                            "'trg_task_completion_cycles_no_update'"
                        ).fetchone()[0]
                        connection.execute("DROP TRIGGER trg_task_completion_cycles_no_update")
                        connection.execute(
                            "UPDATE task_completion_cycles SET verification_basis_kind=NULL WHERE task_id=?",
                            (task_id,),
                        )
                        connection.execute(cycle_trigger)
                        storage._validate_schema22_owned_contract(connection)
                    before = logical_database_digest(connection)
                    with self.assertRaises(storage.StorageError) as rejected:
                        storage.read_completion_history(
                            connection, project_id=self.target.project.project_id, task_id=task_id
                        )
                    self.assertEqual(rejected.exception.code, "completion_history_inconsistent")
                    self.assertEqual(logical_database_digest(connection), before)
            storage.validate_schema22_storage(connection)

    def test_mixed_historical_runner_eligibility_is_not_promoted(self):
        with closing(storage.connect(self.target.db_path)) as source, _schema22_container(source) as connection:
            attempt = dict(connection.execute(
                "SELECT * FROM verification_runner_attempts WHERE task_id=?", (self.runner_task,)
            ).fetchone())
            attempt["gate_eligibility_version"] = 0
            digest = verification_runner_attempt_digest(
                storage._verification_runner_attempt_digest_projection(attempt)
            )
            with _changed_immutable_row(
                connection, "verification_runner_attempts",
                "UPDATE verification_runner_attempts SET gate_eligibility_version=0, attempt_digest=? WHERE task_id=?",
                (digest, self.runner_task),
            ):
                before = logical_database_digest(connection)
                with self.assertRaises(storage.StorageError) as rejected:
                    storage.validate_schema22_storage(connection)
                self.assertEqual(rejected.exception.code, "project_state_unreadable")
                with self.assertRaises(storage.StorageError):
                    storage.read_completion_history(
                        connection, project_id=self.target.project.project_id, task_id=self.runner_task
                    )
                self.assertEqual(logical_database_digest(connection), before)

    def test_stored_retired_values_and_stronger_attribution_are_rejected(self):
        with closing(storage.connect(self.target.db_path)) as source, _schema22_container(source) as connection:
            for table, column, value, selector in (
                ("evidence_references", "source_kind", "derived_analysis", "source_kind='verification_receipt'"),
                ("evidence_references", "assurance_class", "llm_derived", "source_kind='verification_receipt'"),
                ("evidence_references", "producer_class", "batch_analyzer", "source_kind='verification_receipt'"),
                ("criterion_evidence_links", "relation", "derived_analysis", "relation='verification_attestation'"),
                ("evidence_references", "assurance_class", "machine_observed", "source_kind='verification_receipt'"),
            ):
                with self.subTest(table=table, column=column, value=value), _changed_immutable_row(
                    connection, table,
                    f"UPDATE {table} SET {column}=? WHERE task_id=? AND {selector}",
                    (value, self.manual_task),
                ):
                    before = logical_database_digest(connection)
                    with self.assertRaises(storage.StorageError) as rejected:
                        storage.validate_schema22_storage(connection)
                    self.assertEqual(rejected.exception.code, "project_state_unreadable")
                    self.assertEqual(logical_database_digest(connection), before)
            storage.validate_schema22_storage(connection)
            private_value = "Authorization: Bearer fixture-private-token"
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "UPDATE tasks SET description=? WHERE task_id=?",
                    (private_value, self.manual_task),
                )
                storage._validate_schema22_owned_contract(connection)
                before = logical_database_digest(connection)
                with self.assertRaises(storage.StorageError) as rejected:
                    storage.validate_schema22_storage(connection)
                self.assertEqual(rejected.exception.code, "project_state_unreadable")
                self.assertNotIn(private_value, str(rejected.exception))
                self.assertEqual(logical_database_digest(connection), before)
            finally:
                connection.rollback()


if __name__ == "__main__":
    unittest.main()
