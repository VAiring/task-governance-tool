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


def remove_v19_bundle_storage_for_test(
    connection: sqlite3.Connection,
) -> None:
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
