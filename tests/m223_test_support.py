from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


V19_TABLES = (
    "criterion_evidence_links",
    "completion_evidence_bundles",
    "completion_bundle_members",
    "completion_bundle_finding_snapshots",
    "evidence_projection_state",
)

V19_INDEXES = (
    "idx_criterion_evidence_links_reference",
    "idx_completion_evidence_bundles_task_cycle",
    "idx_completion_bundle_members_reference",
    "idx_completion_bundle_finding_snapshots_order",
)

V19_TRIGGERS = (
    "trg_criterion_evidence_links_no_update",
    "trg_criterion_evidence_links_no_delete",
    "trg_completion_evidence_bundles_no_update",
    "trg_completion_evidence_bundles_no_delete",
    "trg_completion_bundle_members_no_update",
    "trg_completion_bundle_members_no_delete",
    "trg_completion_bundle_finding_snapshots_no_update",
    "trg_completion_bundle_finding_snapshots_no_delete",
    "trg_criterion_evidence_links_matrix_insert",
    "trg_completion_bundle_members_matrix_insert",
    "trg_completion_bundle_finding_snapshots_matrix_insert",
    "trg_task_completion_cycles_evidence_basis_insert",
)

V20_TABLES = (
    "verification_runner_observations",
    "verification_runner_sandbox_events",
    "verification_runner_attempts",
    "verification_runner_resolutions",
)

V20_INDEXES = (
    "idx_verification_runner_resolutions_parent",
    "idx_verification_runner_resolutions_task_generation",
    "idx_verification_runner_attempts_parent",
    "idx_verification_runner_attempts_task_generation",
    "idx_verification_runner_attempts_resolution",
    "idx_verification_runner_sandbox_events_attempt_kind",
    "idx_verification_runner_observations_parent",
    "idx_verification_runner_observations_task_generation",
    "idx_verification_runner_observations_resolution",
    "idx_verification_runner_observations_attempt",
)

V20_TRIGGERS = (
    "trg_verification_runner_resolutions_no_update",
    "trg_verification_runner_resolutions_no_delete",
    "trg_verification_runner_attempts_no_update",
    "trg_verification_runner_attempts_no_delete",
    "trg_verification_runner_sandbox_events_no_update",
    "trg_verification_runner_sandbox_events_no_delete",
    "trg_verification_runner_observations_no_update",
    "trg_verification_runner_observations_no_delete",
    "trg_verification_runner_resolutions_parent_insert",
    "trg_verification_runner_attempts_parent_insert",
    "trg_verification_runner_sandbox_events_parent_insert",
    "trg_verification_runner_observations_parent_insert",
)

_V19_BUNDLE_COLUMNS = (
    "completion_evidence_bundle_id",
    "project_id",
    "task_id",
    "completion_cycle_id",
    "cycle_ordinal",
    "source_schema_version",
    "bundle_version",
    "contract_revision",
    "authority_snapshot_id",
    "acceptance_criterion_id",
    "verification_criterion_id",
    "target_kind",
    "target_value",
    "target_base_revision",
    "target_generation",
    "target_capture_version",
    "artifact_manifest_id",
    "verification_receipt_id",
    "omission_mask",
    "sealed_at",
    "bundle_digest",
    "payload_size_bytes",
)


def logical_database_digest(connection: sqlite3.Connection) -> str:
    tables = tuple(
        str(row["name"])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
             WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
             ORDER BY name
            """
        ).fetchall()
    )
    payload: dict[str, Any] = {
        "schema": [
            list(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql FROM sqlite_master
                 WHERE name NOT LIKE 'sqlite_%'
                 ORDER BY type, name
                """
            ).fetchall()
        ],
        "tables": {},
    }
    for table_name in tables:
        columns = tuple(
            str(row["name"])
            for row in connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
        )
        projection = ", ".join(f'"{name}"' for name in columns)
        payload["tables"][table_name] = [
            list(row)
            for row in connection.execute(
                f'SELECT {projection} FROM "{table_name}" ORDER BY rowid'
            ).fetchall()
        ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def remove_v21_gate_basis_for_test(connection: sqlite3.Connection) -> None:
    """Reduce an admissible schema-v21 fixture to the exact schema-v20 surface.

    This is deliberately test-only fixture construction, not a supported product
    reverse migration.  Rows that use schema-v21-only arms fail closed while the
    surrounding transaction restores the original v21 fixture.
    """

    from task_governance_tool import storage

    marker = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 21"
    ).fetchone()
    if marker is None:
        return
    row_factory_before = connection.row_factory
    connection.row_factory = sqlite3.Row
    if connection.in_transaction:
        connection.commit()
    try:
        storage.validate_schema21_storage(connection)
    except Exception:
        connection.row_factory = row_factory_before
        raise
    foreign_keys_before = int(
        connection.execute("PRAGMA foreign_keys").fetchone()[0]
    )
    legacy_alter_before = int(
        connection.execute("PRAGMA legacy_alter_table").fetchone()[0]
    )
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA legacy_alter_table = ON")
    rebuilt_tables = (
        "completion_evidence_bundles",
        "verification_runner_resolutions",
        "verification_runner_attempts",
        "verification_runner_observations",
    )
    temporary_names = tuple(f"{name}_v21_test" for name in rebuilt_tables)
    try:
        connection.execute("BEGIN IMMEDIATE")
        storage.validate_schema21_storage(connection)
        if any(storage.table_exists(connection, name) for name in temporary_names):
            raise AssertionError("schema-v21 reducer temporary table already exists")
        for trigger_name in (
            "trg_task_completion_cycles_verification_basis_insert",
            "trg_task_completion_cycles_evidence_basis_insert",
        ):
            connection.execute(f'DROP TRIGGER "{trigger_name}"')
        for table_name, temporary_name in zip(
            rebuilt_tables,
            temporary_names,
            strict=True,
        ):
            connection.execute(
                f'ALTER TABLE "{table_name}" RENAME TO "{temporary_name}"'
            )

        runner_tables = storage._verification_runner_table_statements(
            schema_version=storage.PRIVATE_SCHEMA20_VERSION,
        )
        for statement in runner_tables[:3]:
            connection.execute(statement)
        connection.execute(
            storage._completion_evidence_bundle_v20_table_sql(
                schema_version=storage.PRIVATE_SCHEMA20_VERSION,
            )
        )
        copy_order = (
            "verification_runner_resolutions",
            "verification_runner_attempts",
            "verification_runner_observations",
            "completion_evidence_bundles",
        )
        for table_name in copy_order:
            temporary_name = f"{table_name}_v21_test"
            columns = tuple(
                str(row["name"])
                for row in connection.execute(
                    f'PRAGMA table_info("{temporary_name}")'
                ).fetchall()
            )
            projection = ", ".join(f'"{name}"' for name in columns)
            connection.execute(
                f'INSERT INTO "{table_name}" ({projection}) '
                f'SELECT {projection} FROM "{temporary_name}"'
            )
        for temporary_name in (
            "completion_evidence_bundles_v21_test",
            "verification_runner_observations_v21_test",
            "verification_runner_attempts_v21_test",
            "verification_runner_resolutions_v21_test",
        ):
            connection.execute(f'DROP TABLE "{temporary_name}"')

        for statement in storage._verification_runner_index_statements():
            _kind, _name, table_name = storage._schema20_statement_identity(statement)
            if table_name in rebuilt_tables:
                connection.execute(statement)
        for statement in storage._verification_runner_trigger_statements(
            schema_version=storage.PRIVATE_SCHEMA20_VERSION,
        ):
            _kind, _name, table_name = storage._schema20_statement_identity(statement)
            if table_name in rebuilt_tables:
                connection.execute(statement)
        for statement in storage._bundle_v20_recreated_object_statements():
            connection.execute(statement)
        v20_cycle_guards = (
            next(
                statement
                for statement in storage.verification_receipt_schema_statements()
                if "trg_task_completion_cycles_verification_basis_insert"
                in statement
            ),
            next(
                statement
                for statement in storage.completion_evidence_bundle_schema_statements()
                if "trg_task_completion_cycles_evidence_basis_insert" in statement
            ),
        )
        for statement in v20_cycle_guards:
            connection.execute(statement)

        connection.execute("DELETE FROM schema_migrations WHERE version = 21")
        storage.validate_schema20_storage(
            connection,
            allow_native_bundle_v2=True,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(
            f"PRAGMA legacy_alter_table = {legacy_alter_before}"
        )
        connection.execute(f"PRAGMA foreign_keys = {foreign_keys_before}")
        try:
            if (
                int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
                != foreign_keys_before
                or int(
                    connection.execute("PRAGMA legacy_alter_table").fetchone()[0]
                )
                != legacy_alter_before
            ):
                raise AssertionError("schema-v21 reducer could not restore pragmas")
        finally:
            connection.row_factory = row_factory_before


def remove_v20_runner_shadow_for_test(
    connection: sqlite3.Connection,
    *,
    preserve_v19_bundle: bool = True,
) -> None:
    """Return a current test database to the exact complete schema-v19 surface."""

    remove_v21_gate_basis_for_test(connection)
    marker = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 20"
    ).fetchone()
    if marker is None:
        return
    if connection.in_transaction:
        connection.commit()
    foreign_keys_enabled = bool(
        connection.execute("PRAGMA foreign_keys").fetchone()[0]
    )
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA legacy_alter_table = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        for trigger_name in V20_TRIGGERS:
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
        for index_name in V20_INDEXES:
            connection.execute(f'DROP INDEX IF EXISTS "{index_name}"')
        for table_name in V20_TABLES:
            connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')

        connection.execute(
            "ALTER TABLE task_completion_cycles "
            "DROP COLUMN verification_runner_observation_id"
        )
        connection.execute(
            "ALTER TABLE task_completion_cycles "
            "DROP COLUMN verification_basis_kind"
        )
        connection.execute(
            "ALTER TABLE tasks DROP COLUMN review_target_runner_basis_version"
        )

        if preserve_v19_bundle:
            from task_governance_tool.storage import (
                completion_evidence_bundle_schema_statements,
            )

            v19_statements = completion_evidence_bundle_schema_statements()
            connection.execute(
                "DROP TRIGGER trg_completion_evidence_bundles_no_update"
            )
            connection.execute(
                "DROP TRIGGER trg_completion_evidence_bundles_no_delete"
            )
            connection.execute(
                "DROP INDEX idx_completion_evidence_bundles_task_cycle"
            )
            connection.execute(
                "ALTER TABLE completion_evidence_bundles "
                "RENAME TO completion_evidence_bundles_v20"
            )
            v19_bundle_table = next(
                statement
                for statement in v19_statements
                if "CREATE TABLE completion_evidence_bundles" in statement
            )
            connection.execute(v19_bundle_table)
            columns = ",".join(_V19_BUNDLE_COLUMNS)
            connection.execute(
                f"INSERT INTO completion_evidence_bundles({columns}) "
                f"SELECT {columns} FROM completion_evidence_bundles_v20"
            )
            connection.execute("DROP TABLE completion_evidence_bundles_v20")
            for object_name in (
                "idx_completion_evidence_bundles_task_cycle",
                "trg_completion_evidence_bundles_no_update",
                "trg_completion_evidence_bundles_no_delete",
            ):
                connection.execute(
                    next(
                        statement
                        for statement in v19_statements
                        if object_name in statement
                    )
                )
            connection.execute(
                "DROP TRIGGER trg_criterion_evidence_links_matrix_insert"
            )
            connection.execute(
                next(
                    statement
                    for statement in v19_statements
                    if "trg_criterion_evidence_links_matrix_insert" in statement
                )
            )

        connection.execute("DELETE FROM schema_migrations WHERE version = 20")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA legacy_alter_table = OFF")
        if foreign_keys_enabled:
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise AssertionError(
                    "schema downgrade fixture could not restore foreign keys"
                )


def remove_v19_bundle_storage_for_test(
    connection: sqlite3.Connection,
) -> None:
    remove_v20_runner_shadow_for_test(
        connection,
        preserve_v19_bundle=False,
    )
    if connection.in_transaction:
        connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        for trigger_name in V19_TRIGGERS:
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
        for index_name in V19_INDEXES:
            connection.execute(f'DROP INDEX IF EXISTS "{index_name}"')
        for table_name in (
            "completion_bundle_finding_snapshots",
            "completion_bundle_members",
            "criterion_evidence_links",
            "completion_evidence_bundles",
            "evidence_projection_state",
        ):
            connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        connection.execute(
            "ALTER TABLE task_completion_cycles "
            "DROP COLUMN completion_evidence_bundle_id"
        )
        connection.execute(
            "ALTER TABLE task_completion_cycles "
            "DROP COLUMN evidence_basis_version"
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 19")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
