from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from tests.test_m243b_schema21_storage import (
    FIXED_TIME,
    ROOT,
    _identity,
    _new_target,
    storage,
)
from tests.m223_test_support import logical_database_digest


REPLACED_TABLES = (
    "evidence_references",
    "criterion_evidence_links",
    "completion_evidence_bundles",
)
EXPECTED_REPLACEMENTS = {
    "evidence_references": ("table", "evidence_references"),
    "criterion_evidence_links": ("table", "criterion_evidence_links"),
    "completion_evidence_bundles": ("table", "completion_evidence_bundles"),
    "idx_evidence_references_source": ("index", "evidence_references"),
    "idx_criterion_evidence_links_reference": ("index", "criterion_evidence_links"),
    "idx_completion_evidence_bundles_task_cycle": (
        "index", "completion_evidence_bundles"
    ),
    "trg_evidence_references_no_update": ("trigger", "evidence_references"),
    "trg_evidence_references_no_delete": ("trigger", "evidence_references"),
    "trg_criterion_evidence_links_no_update": ("trigger", "criterion_evidence_links"),
    "trg_criterion_evidence_links_no_delete": ("trigger", "criterion_evidence_links"),
    "trg_criterion_evidence_links_matrix_insert": (
        "trigger", "criterion_evidence_links"
    ),
    "trg_completion_evidence_bundles_no_update": (
        "trigger", "completion_evidence_bundles"
    ),
    "trg_completion_evidence_bundles_no_delete": (
        "trigger", "completion_evidence_bundles"
    ),
    "trg_task_completion_cycles_evidence_basis_insert": (
        "trigger", "task_completion_cycles"
    ),
}


def _replacement_sql() -> dict[str, str]:
    return {
        storage._schema20_statement_identity(statement)[1]: statement
        for statement in storage._schema22_replacement_statements()
    }


def _insert(connection: sqlite3.Connection, table: str, values: dict) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


def _reference_row() -> dict:
    return {
        "evidence_reference_id": "tg_evidence_reference_" + "1" * 16,
        "project_id": "project",
        "task_id": "task",
        "source_kind": "artifact_manifest",
        "source_state": "complete_git",
        "source_id": "tg_artifact_manifest_" + "2" * 16,
        "assurance_class": "machine_observed",
        "producer_class": "taskgov_git",
        "producer_version": 1,
        "contract_revision": 0,
        "authority_snapshot_id": "tg_authority_snapshot_" + "3" * 16,
        "acceptance_criterion_id": None,
        "verification_criterion_id": None,
        "target_kind": "diff_fingerprint",
        "target_value": "sha256:" + "4" * 64,
        "target_base_revision": "",
        "target_generation": 1,
        "completion_cycle_id": None,
        "digest": "sha256:" + "5" * 64,
        "created_at": FIXED_TIME,
    }


def _link_row() -> dict:
    return {
        "criterion_evidence_link_id": "tg_criterion_evidence_link_" + "6" * 16,
        "project_id": "project",
        "task_id": "task",
        "criterion_id": "tg_contract_criterion_" + "7" * 16,
        "evidence_reference_id": _reference_row()["evidence_reference_id"],
        "relation": "completion_basis",
        "assurance_class": "machine_observed",
        "producer_class": "taskgov_git",
        "producer_version": 1,
        "created_at": FIXED_TIME,
    }


def _bundle_row() -> dict:
    # The compact tagged-union fixture follows the existing schema20 DDL test.
    return {
        "completion_evidence_bundle_id": "tg_completion_evidence_bundle_" + "8" * 16,
        "project_id": "project",
        "task_id": "task",
        "completion_cycle_id": "tg_completion_cycle_" + "9" * 16,
        "cycle_ordinal": 1,
        "source_schema_version": 22,
        "bundle_version": 2,
        "contract_revision": 0,
        "authority_snapshot_id": "tg_authority_snapshot_" + "3" * 16,
        "acceptance_criterion_id": None,
        "verification_criterion_id": None,
        "target_kind": "diff_fingerprint",
        "target_value": "sha256:" + "4" * 64,
        "target_base_revision": "",
        "target_generation": 1,
        "target_capture_version": 1,
        "artifact_manifest_id": "tg_artifact_manifest_" + "2" * 16,
        "verification_receipt_id": None,
        "verification_basis_kind": "not_required",
        "verification_runner_observation_id": None,
        "omission_mask": 0,
        "sealed_at": FIXED_TIME,
        "bundle_digest": "sha256:" + "a" * 64,
        "payload_size_bytes": 1,
    }


@contextmanager
def _empty_public_database():
    with tempfile.TemporaryDirectory(prefix=".tmp-m23s-ddl-", dir=ROOT) as tmp:
        initialized = storage.initialize_uuid_database(
            _new_target(Path(tmp), "schema22-ddl"),
            project_id_factory=lambda: _identity("schema22-ddl"),
            clock=lambda: FIXED_TIME,
        )
        with closing(storage.connect(initialized.target.db_path)) as connection:
            yield initialized, connection


def _construct_private_schema22(connection: sqlite3.Connection) -> None:
    """Replace empty fixture tables only; this is not a migration/rehearsal API."""
    for table in REPLACED_TABLES:
        if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
            raise AssertionError("private DDL fixture requires empty Evidence tables")
    statements = storage._schema22_replacement_statements()
    connection.commit()
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        for statement in statements:
            kind, name, _table = storage._schema20_statement_identity(statement)
            if kind != "table":
                connection.execute(f'DROP {kind.upper()} "{name}"')
        for table in reversed(REPLACED_TABLES):
            connection.execute(f'DROP TABLE "{table}"')
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.execute(f"PRAGMA foreign_keys = {int(foreign_keys)}")


class Schema22DefinitionTests(unittest.TestCase):
    def test_public_initialization_and_schema21_definition_remain_unchanged(self):
        self.assertEqual(storage.SCHEMA_VERSION, 21)
        self.assertEqual(storage.PRIVATE_SCHEMA22_VERSION, 22)
        self.assertEqual(
            storage.PRIVATE_SCHEMA22_MIGRATION_NAME,
            "evidence_reservation_cleanup",
        )
        with _empty_public_database() as (initialized, connection):
            self.assertEqual(initialized.schema_version, 21)
            self.assertEqual(initialized.migrations_applied[-1], 21)
            self.assertEqual(storage.current_schema_version(connection), 21)
            self.assertEqual(
                storage._owned_schema_sql_fingerprint(connection, schema_version=21),
                "8b7aa6d9619c2e98118c9c9c0ed8979a7e56c7ef9c5d3de51980f41a35f80558",
            )
            storage.validate_schema21_storage(connection)
            before = logical_database_digest(connection)
            self.assertEqual(storage.apply_migrations(connection), ([], []))
            self.assertEqual(logical_database_digest(connection), before)

    def test_private_schema_has_exact_objects_and_unchanged_columns_and_foreign_keys(self):
        statements = storage._schema22_replacement_statements()
        self.assertEqual(len(statements), 14)
        self.assertEqual(
            {
                name: (kind, table)
                for kind, name, table in map(storage._schema20_statement_identity, statements)
            },
            EXPECTED_REPLACEMENTS,
        )
        with _empty_public_database() as (_initialized, connection):
            before = {
                (table, pragma): tuple(map(tuple, connection.execute(f"PRAGMA {pragma}({table})")))
                for table in REPLACED_TABLES
                for pragma in ("table_xinfo", "foreign_key_list", "index_list")
            }
            _construct_private_schema22(connection)
            after = {
                (table, pragma): tuple(map(tuple, connection.execute(f"PRAGMA {pragma}({table})")))
                for table in REPLACED_TABLES
                for pragma in ("table_xinfo", "foreign_key_list", "index_list")
            }
            self.assertEqual(after, before)
            counts = dict(connection.execute(
                "SELECT type, COUNT(*) FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL GROUP BY type"
            ))
            self.assertEqual(counts, {"table": 35, "index": 42, "trigger": 59})
            storage._validate_schema22_owned_contract(connection)
            self.assertEqual(
                dict(storage._SCHEMA22_EXPECTED_OBJECTS),
                storage._schema22_expected_objects(),
            )
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
                _insert(connection, "evidence_references", _reference_row())
            with self.assertRaises(storage.StorageError):
                storage._validate_schema21_owned_contract(connection)

    def test_six_reservation_checks_reject_removed_values_without_matrix_guard(self):
        sql = _replacement_sql()
        for table, base, kind_column in (
            ("evidence_references", _reference_row(), "source_kind"),
            ("criterion_evidence_links", _link_row(), "relation"),
        ):
            with self.subTest(table=table), closing(sqlite3.connect(":memory:")) as connection:
                # No parent FK or matrix trigger can mask these six CHECK outcomes.
                connection.execute(sql[table])
                for control in (base, {**base, "assurance_class": "deterministically_derived"}):
                    _insert(connection, table, control)
                    connection.execute(f"DELETE FROM {table}")
                for column, removed in (
                    (kind_column, "derived_analysis"),
                    ("assurance_class", "llm_derived"),
                    ("producer_class", "batch_analyzer"),
                ):
                    with self.subTest(column=column), self.assertRaisesRegex(
                        sqlite3.IntegrityError, "CHECK constraint failed"
                    ):
                        _insert(connection, table, {**base, column: removed})

    def test_bundle_union_preserves_old_sources_and_admits_source22_v2(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute(_replacement_sql()["completion_evidence_bundles"])
            base = _bundle_row()
            accepted = [{**base, "source_schema_version": 19, "bundle_version": 1,
                         "verification_basis_kind": None}]
            for version in (20, 21, 22):
                accepted.extend((
                    {**base, "source_schema_version": version},
                    {**base, "source_schema_version": version,
                     "verification_basis_kind": "caller_attestation",
                     "verification_receipt_id": "tg_verification_receipt_" + "b" * 16},
                ))
                if version >= 21:
                    accepted.append({
                        **base, "source_schema_version": version,
                        "verification_basis_kind": "runner_observation",
                        "verification_runner_observation_id": "tg_verification_runner_observation_" + "c" * 16,
                    })
            for row in accepted:
                with self.subTest(source=row["source_schema_version"], basis=row["verification_basis_kind"]):
                    _insert(connection, "completion_evidence_bundles", row)
                    connection.execute("DELETE FROM completion_evidence_bundles")
            for delta in (
                {"source_schema_version": 19},
                {"source_schema_version": 23},
                {"bundle_version": 1},
                {"verification_basis_kind": "caller_attestation"},
                {"verification_basis_kind": "runner_observation"},
                {"verification_runner_observation_id": "unexpected"},
                {"verification_basis_kind": "runner_observation",
                 "verification_runner_observation_id": "observation",
                 "verification_receipt_id": "receipt"},
                {"source_schema_version": 20, "verification_basis_kind": "runner_observation",
                 "verification_runner_observation_id": "observation"},
            ):
                with self.subTest(delta=delta), self.assertRaises(sqlite3.IntegrityError):
                    _insert(connection, "completion_evidence_bundles", {**base, **delta})

    def test_replacement_table_immutability_triggers_remain_effective(self):
        sql = _replacement_sql()
        for table, row in zip(REPLACED_TABLES, (_reference_row(), _link_row(), _bundle_row()), strict=True):
            with self.subTest(table=table), closing(sqlite3.connect(":memory:")) as connection:
                connection.execute(sql[table])
                _insert(connection, table, row)
                for suffix in ("no_update", "no_delete"):
                    connection.execute(sql[f"trg_{table}_{suffix}"])
                for operation in (f"UPDATE {table} SET project_id = project_id", f"DELETE FROM {table}"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable_"):
                        connection.execute(operation)

    def test_matrix_guard_keeps_existing_manifest_and_runner_relations(self):
        sql = _replacement_sql()
        for runner in (False, True):
            with self.subTest(runner=runner), closing(sqlite3.connect(":memory:")) as connection:
                # Isolate the existing matrix from unrelated parent-row validation.
                connection.execute("CREATE TABLE contract_criteria (project_id, task_id, criterion_id, criterion_kind)")
                connection.execute(sql["evidence_references"])
                connection.execute(sql["criterion_evidence_links"])
                connection.execute(sql["trg_criterion_evidence_links_matrix_insert"])
                reference, link = _reference_row(), _link_row()
                if runner:
                    reference.update(source_kind="runner_observation", producer_class="verification_runner",
                                     verification_criterion_id=link["criterion_id"])
                    link.update(relation="runner_observation", producer_class="verification_runner")
                connection.execute("INSERT INTO contract_criteria VALUES (?, ?, ?, ?)",
                                   ("project", "task", link["criterion_id"], "verification" if runner else "acceptance"))
                _insert(connection, "evidence_references", reference)
                for delta in ({"producer_version": 2}, {"relation": "review_assessment"}):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "invalid_criterion_evidence_link"):
                        _insert(connection, "criterion_evidence_links", {**link, **delta})
                _insert(connection, "criterion_evidence_links", link)

    def test_cycle_guard_requires_same_source22_bundle_and_preserves_legacy_arm(self):
        sql = _replacement_sql()
        with closing(sqlite3.connect(":memory:")) as connection:
            # Only the coupled guard under test is installed on this small owner stub.
            connection.execute("CREATE TABLE task_completion_cycles (project_id, task_id, completion_cycle_id, "
                               "saved_cycle_ordinal, origin, evidence_basis_version, completion_evidence_bundle_id, "
                               "verification_receipt_id, verification_basis_kind, verification_runner_observation_id)")
            connection.execute(sql["completion_evidence_bundles"])
            connection.execute(sql["trg_task_completion_cycles_evidence_basis_insert"])
            bundle = _bundle_row()
            cycle = {key: bundle[key] for key in (
                "project_id", "task_id", "completion_cycle_id", "completion_evidence_bundle_id",
                "verification_receipt_id", "verification_basis_kind", "verification_runner_observation_id",
            )}
            cycle.update(saved_cycle_ordinal=1, origin="native_done", evidence_basis_version=1)
            for source in (21, 22):
                _insert(connection, "completion_evidence_bundles", {**bundle, "source_schema_version": source})
                if source == 21:
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "invalid_completion_evidence_basis"):
                        _insert(connection, "task_completion_cycles", cycle)
                else:
                    for delta in ({"saved_cycle_ordinal": 2}, {"verification_basis_kind": "caller_attestation"},
                                  {"verification_runner_observation_id": "unexpected"}):
                        with self.assertRaisesRegex(sqlite3.IntegrityError, "invalid_completion_evidence_basis"):
                            _insert(connection, "task_completion_cycles", {**cycle, **delta})
                    _insert(connection, "task_completion_cycles", cycle)
                connection.execute("DELETE FROM completion_evidence_bundles")
            _insert(connection, "task_completion_cycles", {
                **cycle, "origin": "legacy_current_done", "evidence_basis_version": 0,
                "completion_evidence_bundle_id": None, "verification_basis_kind": None,
            })

    def test_complete_owned_recognition_rejects_inherited_and_replacement_drift(self):
        for name, replacement in (
            ("idx_tasks_project_status", "CREATE INDEX idx_tasks_project_status ON tasks(project_id, title)"),
            ("idx_evidence_references_source", "CREATE INDEX idx_evidence_references_source ON evidence_references(source_id)"),
        ):
            with self.subTest(name=name), _empty_public_database() as (_initialized, connection):
                _construct_private_schema22(connection)
                storage._validate_schema22_owned_contract(connection)
                connection.execute(f'DROP INDEX "{name}"')
                connection.execute(replacement)
                connection.commit()
                before = logical_database_digest(connection)
                with self.assertRaises(storage.StorageError):
                    storage._validate_schema22_owned_contract(connection)
                self.assertEqual(logical_database_digest(connection), before)


if __name__ == "__main__":
    unittest.main()
