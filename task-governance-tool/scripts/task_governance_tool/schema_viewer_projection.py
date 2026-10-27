"""Setup-only schema 34: transactional Viewer revisions and derived metadata."""
from __future__ import annotations

import sqlite3

VERSION = 34
MIGRATION_NAME = "viewer_incremental_tracking"
OWNED_FINGERPRINT = "64e6f50aaef4bb9529f23bf319c276a9e54dfec7d87d98f78cd0c23f50f7302c"
BUNDLE_TABLE = "completion_evidence_bundles"
CYCLE_GUARD = "trg_task_completion_cycles_evidence_basis_insert"
EVENT_TRIGGER = "trg_task_events_viewer_generation"

TABLE_SQL = {
    "viewer_task_revisions": """CREATE TABLE viewer_task_revisions (
      project_id TEXT NOT NULL REFERENCES project_meta(project_id),
      task_id TEXT NOT NULL REFERENCES tasks(task_id),
      source_generation INTEGER NOT NULL CHECK(typeof(source_generation)='integer' AND source_generation>=0),
      PRIMARY KEY(project_id,task_id)
    )""",
    "viewer_projection_state": """CREATE TABLE viewer_projection_state (
      project_id TEXT PRIMARY KEY REFERENCES project_meta(project_id),
      database_epoch TEXT NOT NULL CHECK(typeof(database_epoch)='text' AND length(database_epoch)=32 AND database_epoch NOT GLOB '*[^0-9a-f]*'),
      invalidated_generation INTEGER NOT NULL CHECK(typeof(invalidated_generation)='integer' AND invalidated_generation>=0),
      cache_generation INTEGER CHECK(cache_generation IS NULL OR (typeof(cache_generation)='integer' AND cache_generation>=0)),
      cache_format_version INTEGER CHECK(cache_format_version IS NULL OR (typeof(cache_format_version)='integer' AND cache_format_version>=1)),
      projection_version INTEGER CHECK(projection_version IS NULL OR (typeof(projection_version)='integer' AND projection_version>=1)),
      cache_sha256 TEXT CHECK(cache_sha256 IS NULL OR (typeof(cache_sha256)='text' AND length(cache_sha256)=64 AND cache_sha256 NOT GLOB '*[^0-9a-f]*')),
      cache_bytes INTEGER CHECK(cache_bytes IS NULL OR (typeof(cache_bytes)='integer' AND cache_bytes BETWEEN 1 AND 67108864)),
      html_generation INTEGER CHECK(html_generation IS NULL OR (typeof(html_generation)='integer' AND html_generation>=0)),
      html_sha256 TEXT CHECK(html_sha256 IS NULL OR (typeof(html_sha256)='text' AND length(html_sha256)=64 AND html_sha256 NOT GLOB '*[^0-9a-f]*')),
      html_bytes INTEGER CHECK(html_bytes IS NULL OR (typeof(html_bytes)='integer' AND html_bytes BETWEEN 1 AND 67108864)),
      CHECK((cache_generation IS NULL AND cache_format_version IS NULL AND projection_version IS NULL AND cache_sha256 IS NULL AND cache_bytes IS NULL)
         OR (cache_generation IS NOT NULL AND cache_format_version IS NOT NULL AND projection_version IS NOT NULL AND cache_sha256 IS NOT NULL AND cache_bytes IS NOT NULL)),
      CHECK((html_generation IS NULL AND html_sha256 IS NULL AND html_bytes IS NULL)
         OR (html_generation IS NOT NULL AND html_sha256 IS NOT NULL AND html_bytes IS NOT NULL
             AND cache_generation IS NOT NULL AND html_generation<=cache_generation))
    )""",
}
INDEX_SQL = {"idx_viewer_task_revisions_generation":
    "CREATE INDEX idx_viewer_task_revisions_generation ON viewer_task_revisions(project_id,source_generation,task_id)"}

TRIGGER_SQL = {}
for operation in ("INSERT", "UPDATE"):
    name = "trg_viewer_task_revision_" + operation.lower()
    immutable = "NEW.project_id!=OLD.project_id OR NEW.task_id!=OLD.task_id OR NEW.source_generation<OLD.source_generation OR " if operation == "UPDATE" else ""
    TRIGGER_SQL[name] = f"""CREATE TRIGGER {name} BEFORE {operation} ON viewer_task_revisions
      BEGIN SELECT CASE WHEN {immutable}NOT EXISTS (
        SELECT 1 FROM tasks AS t JOIN viewer_maintenance_state AS m ON m.project_id=t.project_id
        JOIN viewer_projection_state AS p ON p.project_id=m.project_id
        WHERE t.task_id=NEW.task_id AND t.project_id=NEW.project_id
          AND typeof(m.source_generation)='integer' AND NEW.source_generation<=m.source_generation)
        THEN RAISE(ABORT,'Viewer revision parent is invalid') END; END"""
    name = "trg_viewer_projection_" + operation.lower()
    immutable = "NEW.project_id!=OLD.project_id OR NEW.invalidated_generation<OLD.invalidated_generation OR " if operation == "UPDATE" else ""
    TRIGGER_SQL[name] = f"""CREATE TRIGGER {name} BEFORE {operation} ON viewer_projection_state
      BEGIN SELECT CASE WHEN {immutable}NOT EXISTS (
        SELECT 1 FROM viewer_maintenance_state AS m WHERE m.project_id=NEW.project_id
          AND typeof(m.source_generation)='integer' AND NEW.invalidated_generation<=m.source_generation
          AND (NEW.cache_generation IS NULL OR NEW.cache_generation<=m.source_generation))
        THEN RAISE(ABORT,'Viewer projection parent is invalid') END; END"""
for table in TABLE_SQL:
    name = "trg_" + table + "_no_delete"
    TRIGGER_SQL[name] = f"CREATE TRIGGER {name} BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'Viewer tracking cannot be deleted'); END"
TRIGGER_SQL[EVENT_TRIGGER] = """CREATE TRIGGER trg_task_events_viewer_generation AFTER INSERT ON task_events
    BEGIN
      UPDATE viewer_maintenance_state SET source_generation=source_generation+1
        WHERE project_id=NEW.project_id AND typeof(source_generation)='integer'
          AND source_generation BETWEEN 0 AND 9223372036854775806
          AND EXISTS(SELECT 1 FROM viewer_projection_state WHERE project_id=NEW.project_id);
      SELECT CASE WHEN changes()!=1 THEN RAISE(ABORT,'Viewer generation state is unavailable') END;
      INSERT INTO viewer_task_revisions(project_id,task_id,source_generation)
        SELECT NEW.project_id,NEW.task_id,source_generation FROM viewer_maintenance_state WHERE project_id=NEW.project_id
        ON CONFLICT(project_id,task_id) DO UPDATE SET source_generation=excluded.source_generation;
    END"""


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
    if owned_fingerprint(connection) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    names = _owned_names()
    # Preserve every predecessor's attachment and interrupted-rebuild boundary.
    for tables, suffix in ((v30_tables, "_v29"), (v31_tables, "_v30"),
            ((BUNDLE_TABLE,), "_v31"), ((BUNDLE_TABLE,), "_v32"), ((BUNDLE_TABLE,), "_v33")):
        for table in tables:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE", (table + suffix,)).fetchone():
                raise s._unreadable_project_state()
    for table in set(TABLE_SQL) | set(v30_tables) | set(v31_tables) | {BUNDLE_TABLE}:
        if any(row[0] not in names for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,))):
            raise s._unreadable_project_state()


def _apply_delta_locked(connection):
    from . import storage as s
    from .schema_criterion_link_collector import _replace_exact
    from .viewer_projection_repository import initialize_locked
    if (not connection.in_transaction or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0
            or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 1):
        raise s.StorageError("internal_error", "Viewer tracking migration requires its admitted writer")
    attached = connection.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name", (BUNDLE_TABLE,)).fetchall()
    if any(row["name"] not in _owned_names() for row in attached):
        raise s._unreadable_project_state()
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (BUNDLE_TABLE,)).fetchone()[0]
    sql = _replace_exact(sql, "29, 30, 31, 32, 33)", "29, 30, 31, 32, 33, 34)", count=3)
    sql = _replace_exact(sql, "source_schema_version IN (30, 31, 32, 33)", "source_schema_version IN (30, 31, 32, 33, 34)", count=5)
    sql = _replace_exact(sql, "source_schema_version NOT IN (30, 31, 32, 33)", "source_schema_version NOT IN (30, 31, 32, 33, 34)")
    guard = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (CYCLE_GUARD,)).fetchone()[0]
    guard = _replace_exact(guard, "bundle.source_schema_version IN (29,30,31,32,33)", "bundle.source_schema_version IN (29,30,31,32,33,34)")
    guard = _replace_exact(guard, "bundle.source_schema_version IN (30,31,32,33)", "bundle.source_schema_version IN (30,31,32,33,34)")
    projection = ",".join(s._quoted_identifier(row["name"]) for row in connection.execute("PRAGMA table_xinfo(completion_evidence_bundles)"))
    connection.execute(f"DROP TRIGGER {CYCLE_GUARD}")
    connection.execute("ALTER TABLE completion_evidence_bundles RENAME TO completion_evidence_bundles_v33")
    connection.execute(sql)
    connection.execute(f"INSERT INTO completion_evidence_bundles (rowid,{projection}) SELECT rowid,{projection} FROM completion_evidence_bundles_v33")
    connection.execute("DROP TABLE completion_evidence_bundles_v33")
    for row in attached:
        connection.execute(row["sql"])
    connection.execute(guard)
    connection.execute(f"DROP TRIGGER {EVENT_TRIGGER}")
    for definition in (*TABLE_SQL.values(), *INDEX_SQL.values(), *TRIGGER_SQL.values()):
        connection.execute(definition)
    for row in connection.execute("SELECT project_id FROM project_meta ORDER BY project_id").fetchall():
        initialize_locked(connection, row[0])


def migrate(connection):
    from . import storage as s, schema_preservation
    from .schema_own_review_indexes import validate_storage as predecessor
    if connection.in_transaction:
        raise s.StorageError("internal_error", "Viewer tracking migration requires an idle connection")
    version = s.current_schema_version(connection)
    if version == VERSION:
        connection.execute("BEGIN")
        try:
            validate_storage(connection)
        finally:
            connection.rollback()
        return False
    if version != 33:
        raise s.StorageError("migration_required", "Viewer tracking migration requires schema version 33")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1 or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0:
        raise s._unreadable_project_state()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        predecessor(connection)
        new_names = set(TABLE_SQL) | set(INDEX_SQL) | (set(TRIGGER_SQL) - {EVENT_TRIGGER}) | {BUNDLE_TABLE + "_v33"}
        if s.schema_objects_inconsistent_with_version(connection, 33) or any(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE", (name,)).fetchone() for name in new_names):
            raise s._unreadable_project_state()
        changed = new_names | {CYCLE_GUARD, EVENT_TRIGGER} | {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL", (BUNDLE_TABLE,))}
        before = schema_preservation.capture(connection, changed_object_names=changed)
        _apply_delta_locked(connection)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)
        schema_preservation.assert_preserved(connection, before)
        marker_time = s.utc_now()
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (34,?,?)", (MIGRATION_NAME, marker_time))
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
