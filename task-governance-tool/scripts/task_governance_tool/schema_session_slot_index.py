"""Setup-only schema 32: indexed, non-unique combined session-slot lookup."""

from __future__ import annotations

import sqlite3

VERSION = 32
MIGRATION_NAME = "session_slot_lookup_index"
OWNED_FINGERPRINT = "9104eec74408fdfc4a0e9def6c53c187a13ed98c3bd2b5756a15c353c3dd4a0c"
INDEX_NAME = "idx_task_ownership_held_actor"
INDEX_SQL = ("CREATE INDEX idx_task_ownership_held_actor ON task_ownership "
    "(project_id, coalesce(owner_session_id, completion_session_id)) "
    "WHERE state IN ('owned','completion_only')")
BUNDLE_TABLE = "completion_evidence_bundles"
CYCLE_GUARD = "trg_task_completion_cycles_evidence_basis_insert"


def _owned_names():
    from . import storage as s
    return {name for inventory in (s._SCHEMA_TABLE_INTRODUCED_VERSION,
        s._SCHEMA_INDEX_INTRODUCED_VERSION, s._SCHEMA_TRIGGER_INTRODUCED_VERSION)
        for name, version in inventory.items() if version <= VERSION}


def owned_fingerprint(connection):
    from .schema_review_continuation import _owned_fingerprint
    return _owned_fingerprint(connection, _owned_names())


def validate_owned_contract(connection):
    from . import storage as s
    from .schema_review_answers import REBUILT_TABLES as v30_tables
    from .schema_criterion_link_collector import REBUILT_TABLES as v31_tables
    names = _owned_names()
    if owned_fingerprint(connection) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    for tables, suffix in ((v30_tables, "_v29"), (v31_tables, "_v30"), ((BUNDLE_TABLE,), "_v31")):
        for table in tables:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE",
                    (table + suffix,)).fetchone() or any(row["name"] not in names for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE tbl_name=? "
                        "AND type IN ('index','trigger') AND sql IS NOT NULL", (table,))):
                raise s._unreadable_project_state()


def _apply_delta_locked(connection):
    from . import storage as s
    from .schema_criterion_link_collector import _replace_exact
    if (not connection.in_transaction or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0
            or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 1):
        raise s.StorageError("internal_error", "session slot index migration requires its admitted writer")
    names = _owned_names()
    attached = connection.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name=? "
        "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name", (BUNDLE_TABLE,)).fetchall()
    if any(row["name"] not in names for row in attached):
        raise s._unreadable_project_state()
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (BUNDLE_TABLE,)).fetchone()[0]
    sql = _replace_exact(sql, "29, 30, 31)", "29, 30, 31, 32)", count=3)
    sql = _replace_exact(sql, "source_schema_version IN (30, 31)",
        "source_schema_version IN (30, 31, 32)", count=5)
    sql = _replace_exact(sql, "source_schema_version NOT IN (30, 31)",
        "source_schema_version NOT IN (30, 31, 32)")
    guard = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (CYCLE_GUARD,)).fetchone()[0]
    guard = _replace_exact(guard, "bundle.source_schema_version IN (29,30,31)",
        "bundle.source_schema_version IN (29,30,31,32)")
    guard = _replace_exact(guard, "bundle.source_schema_version IN (30,31)",
        "bundle.source_schema_version IN (30,31,32)")
    columns = tuple(row["name"] for row in connection.execute("PRAGMA table_xinfo(completion_evidence_bundles)"))
    projection = ",".join(s._quoted_identifier(column) for column in columns)
    connection.execute(f"DROP TRIGGER {CYCLE_GUARD}")
    connection.execute("ALTER TABLE completion_evidence_bundles RENAME TO completion_evidence_bundles_v31")
    connection.execute(sql)
    connection.execute(f"INSERT INTO completion_evidence_bundles (rowid,{projection}) "
        f"SELECT rowid,{projection} FROM completion_evidence_bundles_v31")
    connection.execute("DROP TABLE completion_evidence_bundles_v31")
    for row in attached:
        connection.execute(row["sql"])
    connection.execute(guard)
    connection.execute(INDEX_SQL)


def migrate(connection):
    from . import storage as s, schema_preservation
    from .schema_criterion_link_collector import validate_storage as predecessor
    if connection.in_transaction:
        raise s.StorageError("internal_error", "session slot index migration requires an idle connection")
    version = s.current_schema_version(connection)
    if version == VERSION:
        connection.execute("BEGIN")
        try:
            validate_storage(connection)
        finally:
            connection.rollback()
        return False
    if version != 31:
        raise s.StorageError("migration_required", "session slot index migration requires schema version 31")
    if (connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
            or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0):
        raise s._unreadable_project_state()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        predecessor(connection)
        if s.schema_objects_inconsistent_with_version(connection, 31) or connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name COLLATE NOCASE IN (?,?)",
                (INDEX_NAME, BUNDLE_TABLE + "_v31")).fetchone():
            raise s._unreadable_project_state()
        changed = {CYCLE_GUARD, INDEX_NAME} | {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL", (BUNDLE_TABLE,))}
        before = schema_preservation.capture(connection, changed_object_names=changed)
        _apply_delta_locked(connection)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)
        schema_preservation.assert_preserved(connection, before)
        marker_time = s.utc_now()
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (32,?,?)",
            (MIGRATION_NAME, marker_time))
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
        if (connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
                or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0):
            raise s._unreadable_project_state()


def validate_storage(connection, *, recovery=False, _privacy_success_cache=None):
    from . import storage as s
    from .schema_task_event_notes import validate_note_rows
    from .task_ownership import validate_storage_rows as ownership
    marker = connection.execute("SELECT name FROM schema_migrations WHERE version=32").fetchone()
    if (s.current_schema_version(connection) != VERSION or s.missing_migration_versions(connection, VERSION)
            or marker is None or marker["name"] != MIGRATION_NAME):
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
    s._validate_review_storage_rows(connection, VERSION)
    s._schema20_integrity_checks(connection)
    if rejection is not None:
        raise rejection
