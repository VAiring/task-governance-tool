"""Setup-only schema 35: bounded Runner closure and reverse Evidence lookup."""
from __future__ import annotations

import sqlite3

VERSION = 35
MIGRATION_NAME = "runner_closure_lookup_indexes"
OWNED_FINGERPRINT = "9b92e7c64a604adeedfe9de1b1de123cd3f06afe7f67f467e08cd837105f15d5"
BUNDLE_TABLE = "completion_evidence_bundles"
CYCLE_GUARD = "trg_task_completion_cycles_evidence_basis_insert"
INDEX_SQL = {
    "idx_runner_attempt_resolution_reverse": "CREATE INDEX idx_runner_attempt_resolution_reverse ON verification_runner_attempts(verification_runner_resolution_id)",
    "idx_runner_observation_resolution_reverse": "CREATE INDEX idx_runner_observation_resolution_reverse ON verification_runner_observations(verification_runner_resolution_id)",
    "idx_runner_observation_attempt_reverse": "CREATE INDEX idx_runner_observation_attempt_reverse ON verification_runner_observations(verification_runner_attempt_id)",
    "idx_runner_cleanup_attempt_reverse": "CREATE INDEX idx_runner_cleanup_attempt_reverse ON verification_runner_sandbox_events(verification_runner_attempt_id)",
    "idx_runner_cleanup_observation_reverse": "CREATE INDEX idx_runner_cleanup_observation_reverse ON verification_runner_sandbox_events(terminal_observation_id)",
    "idx_evidence_reference_source_reverse": "CREATE INDEX idx_evidence_reference_source_reverse ON evidence_references(source_id)",
    "idx_evidence_reference_runner_generation": "CREATE INDEX idx_evidence_reference_runner_generation ON evidence_references(project_id,task_id,target_generation,source_kind)",
    "idx_criterion_link_reference_reverse": "CREATE INDEX idx_criterion_link_reference_reverse ON criterion_evidence_links(evidence_reference_id)",
}


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
    from .schema_viewer_projection import TABLE_SQL as viewer_tables
    if owned_fingerprint(connection) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    names = _owned_names()
    s._validate_rebuild_catalog(connection, names, (
        (v30_tables, "_v29"), (v31_tables, "_v30"),
        ((BUNDLE_TABLE,), "_v31"), ((BUNDLE_TABLE,), "_v32"),
        ((BUNDLE_TABLE,), "_v33"), ((BUNDLE_TABLE,), "_v34"),
    ), additional_tables=viewer_tables)


def _apply_delta_locked(connection):
    from . import storage as s
    from .schema_criterion_link_collector import _replace_exact
    if (not connection.in_transaction or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0
            or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 1):
        raise s.StorageError("internal_error", "Runner index migration requires its admitted writer")
    attached = connection.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name", (BUNDLE_TABLE,)).fetchall()
    if any(row["name"] not in _owned_names() for row in attached):
        raise s._unreadable_project_state()
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (BUNDLE_TABLE,)).fetchone()[0]
    sql = _replace_exact(sql, "29, 30, 31, 32, 33, 34)", "29, 30, 31, 32, 33, 34, 35)", count=3)
    sql = _replace_exact(sql, "source_schema_version IN (30, 31, 32, 33, 34)", "source_schema_version IN (30, 31, 32, 33, 34, 35)", count=5)
    sql = _replace_exact(sql, "source_schema_version NOT IN (30, 31, 32, 33, 34)", "source_schema_version NOT IN (30, 31, 32, 33, 34, 35)")
    guard = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (CYCLE_GUARD,)).fetchone()[0]
    guard = _replace_exact(guard, "bundle.source_schema_version IN (29,30,31,32,33,34)", "bundle.source_schema_version IN (29,30,31,32,33,34,35)")
    guard = _replace_exact(guard, "bundle.source_schema_version IN (30,31,32,33,34)", "bundle.source_schema_version IN (30,31,32,33,34,35)")
    projection = ",".join(s._quoted_identifier(row["name"]) for row in connection.execute("PRAGMA table_xinfo(completion_evidence_bundles)"))
    connection.execute(f"DROP TRIGGER {CYCLE_GUARD}")
    connection.execute("ALTER TABLE completion_evidence_bundles RENAME TO completion_evidence_bundles_v34")
    connection.execute(sql)
    connection.execute(f"INSERT INTO completion_evidence_bundles (rowid,{projection}) SELECT rowid,{projection} FROM completion_evidence_bundles_v34")
    connection.execute("DROP TABLE completion_evidence_bundles_v34")
    for row in attached:
        connection.execute(row["sql"])
    connection.execute(guard)
    for definition in INDEX_SQL.values():
        connection.execute(definition)


def migrate(connection):
    from . import storage as s, schema_preservation
    from .schema_viewer_projection import validate_storage as predecessor
    if connection.in_transaction:
        raise s.StorageError("internal_error", "Runner index migration requires an idle connection")
    version = s.current_schema_version(connection)
    if version == VERSION:
        connection.execute("BEGIN")
        try:
            validate_storage(connection)
        finally:
            connection.rollback()
        return False
    if version != 34:
        raise s.StorageError("migration_required", "Runner index migration requires schema version 34")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1 or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0:
        raise s._unreadable_project_state()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        predecessor(connection)
        new_names = set(INDEX_SQL) | {BUNDLE_TABLE + "_v34"}
        if s.schema_objects_inconsistent_with_version(connection, 34) or any(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE", (name,)).fetchone() for name in new_names):
            raise s._unreadable_project_state()
        changed = new_names | {CYCLE_GUARD} | {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL", (BUNDLE_TABLE,))}
        before = schema_preservation.capture(connection, changed_object_names=changed)
        _apply_delta_locked(connection)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)
        schema_preservation.assert_preserved(connection, before)
        marker_time = s.utc_now()
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (35,?,?)", (MIGRATION_NAME, marker_time))
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
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1 or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0:
            raise s._unreadable_project_state()


def validate_storage(connection, *, recovery=False, _privacy_success_cache=None):
    _validate_storage_with_task_rows(connection, recovery=recovery, _privacy_success_cache=_privacy_success_cache)


def _validate_storage_with_task_rows(connection, *, recovery=False, _privacy_success_cache=None):
    from . import storage as s
    from .schema_task_event_notes import validate_note_rows
    from .task_ownership import validate_storage_rows as ownership
    from .viewer_projection_repository import validate_all_rows
    if s.current_schema_version(connection) != VERSION or s.missing_migration_versions(connection, VERSION):
        raise s._unreadable_project_state()
    validate_owned_contract(connection)
    rejection = None
    task_rows = None
    if recovery:
        try:
            s.validate_evidence_ledger_storage_for_recovery(connection, _privacy_success_cache=_privacy_success_cache)
        except s.StoredTaskVerificationError as exc:
            rejection = exc
        s._validated_verification_runner_references(connection)
        s.validate_completion_cycle_storage(connection)
    else:
        task_rows = s._validate_schema21_admitted_rows(connection, _privacy_success_cache=_privacy_success_cache)
    validate_note_rows(connection)
    ownership(connection)
    s._validate_review_storage_rows(connection, VERSION)
    validate_all_rows(connection)
    s._schema20_integrity_checks(connection)
    if rejection is not None:
        raise rejection
    return task_rows
