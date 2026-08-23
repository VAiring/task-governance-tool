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


def remove_v20_runner_shadow_for_test(
    connection: sqlite3.Connection,
    *,
    preserve_v19_bundle: bool = True,
) -> None:
    """Return a current test database to the exact complete schema-v19 surface."""

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
