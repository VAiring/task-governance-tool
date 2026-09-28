"""Schema 23: explicit waiver storage, with immutable legacy history retained."""

from __future__ import annotations

import sqlite3

from task_governance_tool.schema_completion_evidence_bundles import (
    _completion_evidence_bundle_v20_table_sql,
    _task_completion_cycle_evidence_basis_v21_trigger_sql,
)


VERSION = 23
MIGRATION_NAME = "verification_declaration"
OWNED_FINGERPRINT = "3e89de9f64246dcdc14fcb49742f34fffb78a5076aecd412e96922856d78997f"
TASK_COLUMN = """ALTER TABLE tasks ADD COLUMN verification_not_required_reason
TEXT NOT NULL DEFAULT '' CHECK (
  typeof(verification_not_required_reason) = 'text'
  AND length(verification_not_required_reason) <= 1000
)"""
CYCLE_COLUMN = """ALTER TABLE task_completion_cycles ADD COLUMN verification_not_required_reason
TEXT CHECK (verification_not_required_reason IS NULL OR (
  typeof(verification_not_required_reason) = 'text'
  AND length(verification_not_required_reason) <= 1000
))"""
DECLARATION_TRIGGER = """
CREATE TRIGGER trg_task_completion_cycles_verification_declaration_insert
BEFORE INSERT ON task_completion_cycles
WHEN NOT (
  (NEW.origin = 'legacy_current_done' AND NEW.verification_not_required_reason IS NULL)
  OR
  (NEW.origin = 'native_done' AND NEW.verification_not_required_reason IS NOT NULL
   AND EXISTS (
     SELECT 1 FROM tasks AS task
      WHERE task.project_id = NEW.project_id AND task.task_id = NEW.task_id
        AND task.verification_not_required_reason = NEW.verification_not_required_reason
        AND ((taskgov_verification_specified(task.verification) = 1
              AND NEW.verification_not_required_reason = '')
             OR (taskgov_verification_specified(task.verification) = 0
                 AND taskgov_verification_specified(NEW.verification_not_required_reason) = 1
                 AND NEW.verification_basis_kind = 'not_required'))
   ))
)
BEGIN SELECT RAISE(ABORT, 'invalid_completion_verification_declaration'); END
"""


def replacement_statements() -> tuple[str, ...]:
    from task_governance_tool.storage import _bundle_v20_recreated_object_statements
    return (
        _completion_evidence_bundle_v20_table_sql(schema_version=21).replace(
            "source_schema_version = 21", "source_schema_version IN (21, 22, 23)"),
        *_bundle_v20_recreated_object_statements(),
        _task_completion_cycle_evidence_basis_v21_trigger_sql().replace(
            "bundle.source_schema_version = 21", "bundle.source_schema_version = 23"),
        DECLARATION_TRIGGER,
    )


def validate_owned_contract(connection: sqlite3.Connection) -> None:
    from task_governance_tool import storage as s
    if s._owned_schema_sql_fingerprint(connection, schema_version=VERSION) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    expected = dict(s._SCHEMA22_EXPECTED_OBJECTS)
    for statement in replacement_statements():
        kind, name, owner = s._schema20_statement_identity(statement)
        expected[name] = (kind, owner, s._normalized_schema_sql(statement))
    if (s._unowned_rebuilt_table_attachments(
        connection, table_names=tuple(dict.fromkeys((*s._SCHEMA21_REBUILT_TABLES, *s._SCHEMA22_REBUILT_TABLES))),
        expected_objects=expected.items(),
    ) or s._schema21_temporary_table_present(connection)
        or s._schema22_migration_temporary_name_collision(connection)
        or connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'completion_evidence_bundles_v22' COLLATE NOCASE").fetchone()):
        raise s._unreadable_project_state()


def validate_storage(connection: sqlite3.Connection, *, recovery: bool = False,
                     _privacy_success_cache=None) -> None:
    from task_governance_tool import storage as s
    marker = connection.execute("SELECT name FROM schema_migrations WHERE version = 23").fetchone()
    if (s.current_schema_version(connection) != VERSION or s.missing_migration_versions(connection, VERSION)
        or marker is None or marker["name"] != MIGRATION_NAME):
        raise s._unreadable_project_state()
    validate_owned_contract(connection)
    if recovery:
        rejection = None
        try:
            s.validate_evidence_ledger_storage_for_recovery(connection, _privacy_success_cache=_privacy_success_cache)
        except s.StoredTaskVerificationError as exc:
            rejection = exc
        s._validated_verification_runner_references(connection)
        s.validate_completion_cycle_storage(connection)
        s._schema20_integrity_checks(connection)
        if rejection is not None:
            raise rejection
    else:
        s._validate_schema21_admitted_rows(connection, _privacy_success_cache=_privacy_success_cache)
        s._schema20_integrity_checks(connection)


def migrate(connection: sqlite3.Connection) -> bool:
    """Setup-owned transaction; no backfill of intent or historical evidence."""
    from task_governance_tool import storage as s
    if connection.in_transaction:
        raise s.StorageError("internal_error", "verification declaration migration requires an idle connection")
    if (connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
        or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0):
        raise s._unreadable_project_state()
    version = s.current_schema_version(connection)
    if version == VERSION:
        connection.execute("BEGIN")
        try:
            validate_storage(connection)
        finally:
            connection.rollback()
        return False
    if version != 22:
        raise s.StorageError("migration_required", "verification declaration migration requires schema version 22")
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        connection.execute("BEGIN IMMEDIATE")
        s.validate_schema22_storage(connection)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'completion_evidence_bundles_v22' COLLATE NOCASE").fetchone():
            raise s._unreadable_project_state()
        tables = tuple(row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' ORDER BY name"))
        before = s._selected_table_projection_snapshot(connection, tables)
        columns = {name: value[0] for name, value in before.items()}
        def retained_objects():
            return tuple(tuple(row) for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                "AND name NOT IN ('tasks', 'task_completion_cycles', "
                "'trg_task_completion_cycles_evidence_basis_insert', "
                "'trg_task_completion_cycles_verification_declaration_insert') "
                "AND tbl_name != 'completion_evidence_bundles' ORDER BY type, name"))
        objects_before = retained_objects()
        connection.execute(TASK_COLUMN)
        connection.execute(CYCLE_COLUMN)
        connection.execute("DROP TRIGGER trg_task_completion_cycles_evidence_basis_insert")
        connection.execute("ALTER TABLE completion_evidence_bundles RENAME TO completion_evidence_bundles_v22")
        statements = replacement_statements()
        connection.execute(statements[0])
        projection = ", ".join(s._quoted_identifier(column) for column in columns["completion_evidence_bundles"])
        connection.execute(f"INSERT INTO completion_evidence_bundles ({projection}) SELECT {projection} FROM completion_evidence_bundles_v22")
        connection.execute("DROP TABLE completion_evidence_bundles_v22")
        for statement in statements[1:]:
            connection.execute(statement)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)
        if (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) != before
            or retained_objects() != objects_before):
            raise s._unreadable_project_state()
        connection.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (23, ?, ?)", (MIGRATION_NAME, s.utc_now()))
        validate_storage(connection)
        if (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) != before
            or retained_objects() != objects_before
            or connection.execute("SELECT 1 FROM tasks WHERE verification_not_required_reason != '' LIMIT 1").fetchone()
            or connection.execute("SELECT 1 FROM task_completion_cycles WHERE verification_not_required_reason IS NOT NULL LIMIT 1").fetchone()):
            raise s._unreadable_project_state()
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
        connection.execute("PRAGMA legacy_alter_table = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        if (connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
            or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0):
            raise s._unreadable_project_state()
