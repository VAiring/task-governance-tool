"""Setup-only schema 27: guard legacy pending-to-active reacquisition."""

from __future__ import annotations

import sqlite3

VERSION = 27
MIGRATION_NAME = "review_pending_reacquisition_guard"
OWNED_FINGERPRINT = "78a21ad1539f09546b7d4e987be87f40c0b99cdfc99827272507300a98d118e9"


def slot_update_statement() -> str:
    from task_governance_tool.schema_session_slot import policy_statements
    return policy_statements()[-1].replace(
        "(OLD.state NOT IN ('owned','completion_only')",
        "((OLD.state='completion_only' AND NEW.state='owned') "
        "OR OLD.state NOT IN ('owned','completion_only')",
    )


def replacement_statements() -> tuple[str, ...]:
    from task_governance_tool.schema_session_slot import replacement_statements as predecessor
    return tuple(statement.replace("IN (21, 22, 23, 24, 25, 26)", "IN (21, 22, 23, 24, 25, 26, 27)")
                 .replace("bundle.source_schema_version = 26", "bundle.source_schema_version = 27")
                 for statement in predecessor())


def validate_owned_contract(connection: sqlite3.Connection) -> None:
    from task_governance_tool import storage as s
    from task_governance_tool.schema_verification_declaration import replacement_statements as v23
    from task_governance_tool.schema_task_ownership import ownership_statements
    from task_governance_tool.schema_review_sessions import binding_statements
    from task_governance_tool.schema_session_slot import policy_statements
    if s._owned_schema_sql_fingerprint(connection, schema_version=VERSION) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    expected = dict(s._SCHEMA22_EXPECTED_OBJECTS)
    for statement in (*v23(), *ownership_statements(), *replacement_statements(),
                      *binding_statements(), *policy_statements()[1:], slot_update_statement()):
        kind, name, owner = s._schema20_statement_identity(statement)
        expected[name] = (kind, owner, s._normalized_schema_sql(statement))
    for name, (kind, owner, sql) in expected.items():
        # ADD COLUMN SQL is checked by the exact fingerprint above.
        if name in {"tasks", "task_completion_cycles", "task_owner_transitions"}:
            continue
        row = connection.execute(
            "SELECT type, tbl_name, sql FROM sqlite_master WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if (row is None or row["type"] != kind or row["tbl_name"] != owner
                or row["sql"] is None or s._normalized_schema_sql(row["sql"]) != sql):
            raise s._unreadable_project_state()
    if (s._unowned_rebuilt_table_attachments(
        connection, table_names=tuple(dict.fromkeys((*s._SCHEMA21_REBUILT_TABLES,
            *s._SCHEMA22_REBUILT_TABLES, "review_receipt_sessions"))), expected_objects=expected.items(),
    ) or s._schema21_temporary_table_present(connection)
        or s._schema22_migration_temporary_name_collision(connection)
        or connection.execute("SELECT 1 FROM sqlite_master WHERE name IN "
            "('completion_evidence_bundles_v22','completion_evidence_bundles_v23',"
            "'completion_evidence_bundles_v24','completion_evidence_bundles_v25','completion_evidence_bundles_v26') COLLATE NOCASE").fetchone()):
        raise s._unreadable_project_state()


def validate_storage(connection: sqlite3.Connection, *, recovery: bool = False,
                     _privacy_success_cache=None) -> None:
    from task_governance_tool import storage as s
    from task_governance_tool.task_ownership import validate_storage_rows
    from task_governance_tool.review_session_repository import read_bindings
    marker = connection.execute("SELECT name FROM schema_migrations WHERE version=27").fetchone()
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
    validate_storage_rows(connection)
    read_bindings(connection)
    s._schema20_integrity_checks(connection)
    if rejection is not None:
        raise rejection


def migrate(connection: sqlite3.Connection) -> bool:
    """Retain every old holding and transition; never choose between legacy owners."""
    from task_governance_tool import storage as s
    from task_governance_tool.schema_session_slot import validate_storage as validate_v26
    if connection.in_transaction:
        raise s.StorageError("internal_error", "session slot migration requires an idle connection")
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
    if version != 26:
        raise s.StorageError("migration_required", "session slot migration requires schema version 26")
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        validate_v26(connection)
        if s.schema_objects_inconsistent_with_version(connection, 26) or connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='completion_evidence_bundles_v26' COLLATE NOCASE").fetchone():
            raise s._unreadable_project_state()
        tables = tuple(row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name!='schema_migrations' ORDER BY name"))
        before = s._selected_table_projection_snapshot(connection, tables)
        columns = {name: value[0] for name, value in before.items()}

        def retained_objects():
            return tuple(tuple(row) for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' AND name NOT IN ("
                "'trg_task_ownership_slot_update','trg_task_completion_cycles_evidence_basis_insert') "
                "AND tbl_name!='completion_evidence_bundles' ORDER BY type,name"))

        objects_before = retained_objects()
        connection.execute("DROP TRIGGER trg_task_ownership_slot_update")
        connection.execute(slot_update_statement())
        connection.execute("DROP TRIGGER trg_task_completion_cycles_evidence_basis_insert")
        connection.execute("ALTER TABLE completion_evidence_bundles RENAME TO completion_evidence_bundles_v26")
        statements = replacement_statements()
        connection.execute(statements[0])
        projection = ", ".join(s._quoted_identifier(column) for column in columns["completion_evidence_bundles"])
        connection.execute(f"INSERT INTO completion_evidence_bundles ({projection}) SELECT {projection} FROM completion_evidence_bundles_v26")
        connection.execute("DROP TABLE completion_evidence_bundles_v26")
        for statement in statements[1:]:
            connection.execute(statement)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)
        if (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) != before
                or retained_objects() != objects_before):
            raise s._unreadable_project_state()
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (27,?,?)", (MIGRATION_NAME, s.utc_now()))
        validate_storage(connection)
        if (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) != before
                or retained_objects() != objects_before):
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
        connection.execute("PRAGMA legacy_alter_table=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        if (connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
                or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0):
            raise s._unreadable_project_state()
