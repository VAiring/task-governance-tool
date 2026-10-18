"""Schema 31: preserve collected-review attribution in criterion links.

Only explicit Setup advances an admitted schema-30 database. The predecessor
DDL and sealed Evidence stay unchanged; new native Bundles retain format four.
"""

from __future__ import annotations

import json
import sqlite3


VERSION = 31
MIGRATION_NAME = "criterion_link_collector"
OWNED_FINGERPRINT = "fc5f71354956c14495847480c8f362efea6db3ac902e8a57e6f2869a70317bb5"
REBUILT_TABLES = ("criterion_evidence_links", "completion_evidence_bundles")
CYCLE_GUARD = "trg_task_completion_cycles_evidence_basis_insert"


def _owned_names():
    from . import storage as s

    return {
        name
        for inventory in (
            s._SCHEMA_TABLE_INTRODUCED_VERSION,
            s._SCHEMA_INDEX_INTRODUCED_VERSION,
            s._SCHEMA_TRIGGER_INTRODUCED_VERSION,
        )
        for name, version in inventory.items()
        if version <= VERSION
    }


def owned_fingerprint(connection):
    from .schema_review_continuation import _owned_fingerprint

    return _owned_fingerprint(connection, _owned_names())


def validate_owned_contract(connection):
    from . import storage as s
    from .schema_review_answers import REBUILT_TABLES as predecessor_tables

    names = _owned_names()
    if owned_fingerprint(connection) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    for tables, suffix in ((predecessor_tables, "_v29"), (REBUILT_TABLES, "_v30")):
        for table in tables:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE",
                (table + suffix,),
            ).fetchone():
                raise s._unreadable_project_state()
            if any(
                row["name"] not in names
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE tbl_name=? "
                    "AND type IN ('index','trigger') AND sql IS NOT NULL",
                    (table,),
                )
            ):
                raise s._unreadable_project_state()


def _replace_exact(sql, old, new, *, count=1):
    from . import storage as s

    if sql.count(old) != count:
        raise s._unreadable_project_state()
    return sql.replace(old, new)


def _apply_delta_locked(connection):
    from . import storage as s

    if (
        not connection.in_transaction
        or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0
        or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 1
    ):
        raise s.StorageError("internal_error", "collector-link migration requires its admitted writer")
    names = _owned_names()
    definitions = []
    for table in REBUILT_TABLES:
        attached = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE tbl_name=? "
            "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name",
            (table,),
        ).fetchall()
        if any(row["name"] not in names for row in attached) or connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE",
            (table + "_v30",),
        ).fetchone():
            raise s._unreadable_project_state()
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        if table == "criterion_evidence_links":
            sql = _replace_exact(sql, "'trusted_caller',", "'trusted_caller', 'taskgov_answer_collector',")
        else:
            sql = _replace_exact(sql, "28, 29, 30)", "28, 29, 30, 31)", count=3)
            sql = _replace_exact(
                sql,
                "(source_schema_version = 30 AND bundle_version = 4)",
                "(source_schema_version IN (30, 31) AND bundle_version = 4)",
                count=5,
            )
            sql = _replace_exact(
                sql,
                "CHECK (source_schema_version!=30 OR bundle_version=4)",
                "CHECK (source_schema_version NOT IN (30, 31) OR bundle_version=4)",
            )
        columns = tuple(
            row["name"] for row in connection.execute(f"PRAGMA table_xinfo({s._quoted_identifier(table)})")
        )
        definitions.append((table, sql, columns, attached))
    guard = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (CYCLE_GUARD,)).fetchone()[0]
    guard = _replace_exact(guard, "bundle.source_schema_version IN (29,30)", "bundle.source_schema_version IN (29,30,31)")
    guard = _replace_exact(guard, "bundle.source_schema_version=30", "bundle.source_schema_version IN (30,31)")

    connection.execute(f"DROP TRIGGER {CYCLE_GUARD}")
    for table, sql, columns, attached in definitions:
        projection = ",".join(s._quoted_identifier(column) for column in columns)
        old = s._quoted_identifier(table + "_v30")
        current = s._quoted_identifier(table)
        connection.execute(f"ALTER TABLE {current} RENAME TO {old}")
        connection.execute(sql)
        connection.execute(f"INSERT INTO {current} (rowid,{projection}) SELECT rowid,{projection} FROM {old}")
        connection.execute(f"DROP TABLE {old}")
        for row in attached:
            connection.execute(row["sql"])
    connection.execute(guard)


def migrate(connection):
    from . import storage as s
    from . import schema_preservation
    from .schema_review_answers import validate_storage as predecessor

    if connection.in_transaction:
        raise s.StorageError("internal_error", "collector-link migration requires an idle connection")
    version = s.current_schema_version(connection)
    if version == VERSION:
        connection.execute("BEGIN")
        try:
            validate_storage(connection)
        finally:
            connection.rollback()
        return False
    if version != 30:
        raise s.StorageError("migration_required", "collector-link migration requires schema version 30")
    if (
        connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
        or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0
    ):
        raise s._unreadable_project_state()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        predecessor(connection)
        if s.schema_objects_inconsistent_with_version(connection, 30):
            raise s._unreadable_project_state()
        changed_names = {CYCLE_GUARD} | {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE tbl_name IN "
                "(SELECT value FROM json_each(?)) AND sql IS NOT NULL",
                (json.dumps(REBUILT_TABLES),),
            )
        }

        before = schema_preservation.capture(connection, changed_object_names=changed_names)
        _apply_delta_locked(connection)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)
        schema_preservation.assert_preserved(connection, before)
        marker_time = s.utc_now()
        connection.execute(
            "INSERT INTO schema_migrations(version,name,applied_at) VALUES (31,?,?)",
            (MIGRATION_NAME, marker_time),
        )
        validate_storage(connection)
        schema_preservation.assert_preserved(connection, before, added_marker=(VERSION, MIGRATION_NAME, marker_time))
        connection.commit()
        return True
    except sqlite3.Error as exc:
        connection.rollback()
        if s.is_sqlite_busy_or_locked(exc):
            raise s.StorageError("database_busy", s.DATABASE_BUSY_MESSAGE) from exc
        raise s._unreadable_project_state() from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA legacy_alter_table=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        if (
            connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
            or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0
        ):
            raise s._unreadable_project_state()


def validate_storage(connection, *, recovery=False, _privacy_success_cache=None):
    from . import storage as s
    from .review_answer_repository import validate_storage_rows
    from .schema_task_event_notes import validate_note_rows
    from .task_ownership import validate_storage_rows as ownership
    from .review_session_repository import read_bindings
    from .review_continuation_repository import validate_judgment_storage

    marker = connection.execute("SELECT name FROM schema_migrations WHERE version=31").fetchone()
    if (
        s.current_schema_version(connection) != VERSION
        or s.missing_migration_versions(connection, VERSION)
        or marker is None or marker["name"] != MIGRATION_NAME
    ):
        raise s._unreadable_project_state()
    validate_owned_contract(connection)
    rejection = None
    if recovery:
        try:
            s.validate_evidence_ledger_storage_for_recovery(connection, _privacy_success_cache=_privacy_success_cache)
        except s.StoredTaskVerificationError as exc:
            rejection = exc
        s._validated_verification_runner_references(connection)
        s.validate_completion_cycle_storage(connection)
    else:
        s._validate_schema21_admitted_rows(connection, _privacy_success_cache=_privacy_success_cache)
    validate_note_rows(connection)
    ownership(connection)
    read_bindings(connection)
    validate_judgment_storage(connection)
    validate_storage_rows(connection)
    s._schema20_integrity_checks(connection)
    if rejection is not None:
        raise rejection
