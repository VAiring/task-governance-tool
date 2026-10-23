"""Read-only predecessor preservation for schema 29 through 33 migrations.

Callers own admission, intentional DDL changes, marker insertion and the complete
transaction. This module only freezes and compares the old state in that writer.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3


_USER_OBJECT = "(lower(name) NOT GLOB 'sqlite_*' OR name='sqlite_sequence')"


@dataclass(frozen=True)
class PreservedPredecessor:
    tables: tuple[str, ...]
    columns: dict[str, tuple[str, ...]]
    projection: dict[str, tuple[tuple[str, ...], int, str]]
    changed_object_names: frozenset[str]
    objects: tuple[tuple[str, str, str, str], ...]
    migrations: tuple[tuple[int, int, str, str], ...]


def _preservation_columns(connection, tables):
    from . import storage as s

    result = {}
    for table in tables:
        quoted = s._quoted_identifier(table)
        metadata = tuple(connection.execute(f"PRAGMA main.table_xinfo({quoted})"))
        columns = tuple(row["name"] for row in metadata if row["hidden"] != 1)
        if not columns:
            raise s._unreadable_project_state()
        # For a table name, index_info returns keys only for WITHOUT ROWID.
        if connection.execute(f"PRAGMA main.index_info({quoted})").fetchone() is not None:
            result[table] = columns
            continue
        names = {row["name"].lower() for row in metadata}
        identity = next((name for name in ("rowid", "_rowid_", "oid") if name not in names), None)
        if identity is None:
            primary = [row for row in metadata if row["pk"]]
            indexed_primary = any(
                row["origin"] == "pk"
                for row in connection.execute(f"PRAGMA main.index_list({quoted})")
            )
            if len(primary) != 1 or primary[0]["type"].strip().upper() != "INTEGER" or indexed_primary:
                raise s._unreadable_project_state()
            identity = primary[0]["name"]
        # Qualification prevents double-quoted-string fallback for a missing alias.
        connection.execute(f"SELECT preserved.{s._quoted_identifier(identity)} FROM {quoted} AS preserved LIMIT 0")
        result[table] = columns if identity in columns else (identity, *columns)
    return result


def _unaffected_objects(connection, changed_object_names):
    return tuple(tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
        f"AND {_USER_OBJECT} "
        "AND name NOT IN (SELECT value FROM json_each(?)) ORDER BY type,name",
        (json.dumps(sorted(changed_object_names)),),
    ))


def _migration_rows(connection):
    return tuple(tuple(row) for row in connection.execute(
        "SELECT rowid,version,name,applied_at FROM schema_migrations ORDER BY rowid"
    ))


def capture(
    connection: sqlite3.Connection, *, changed_object_names: set[str],
) -> PreservedPredecessor:
    from . import storage as s

    tables = tuple(row["name"] for row in connection.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND {_USER_OBJECT} "
        "AND name!='schema_migrations' ORDER BY name"
    ))
    columns = _preservation_columns(connection, tables)
    changed = frozenset(changed_object_names)
    return PreservedPredecessor(
        tables, columns,
        s._selected_table_projection_snapshot(connection, tables, column_basis=columns),
        changed, _unaffected_objects(connection, changed), _migration_rows(connection),
    )


def assert_preserved(
    connection: sqlite3.Connection,
    before: PreservedPredecessor,
    *,
    added_marker: tuple[int, str, str] | None = None,
) -> None:
    from . import storage as s

    expected_migrations = before.migrations
    if added_marker is not None:
        version, name, timestamp = added_marker
        expected_migrations += ((version, version, name, timestamp),)
    if (
        s._selected_table_projection_snapshot(connection, before.tables, column_basis=before.columns)
        != before.projection
        or _unaffected_objects(connection, before.changed_object_names) != before.objects
        or _migration_rows(connection) != expected_migrations
    ):
        raise s._unreadable_project_state()
