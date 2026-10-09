"""Setup-only schema 26: combined session slots and versioned usage intervals."""

from __future__ import annotations

import sqlite3

VERSION = 26
MIGRATION_NAME = "review_pending_session_slot"
OWNED_FINGERPRINT = "7ccddfc94dddc45863efece04b688ad73141d2f0bb770b2632d12d2ba10b3e04"


def policy_statements() -> tuple[str, ...]:
    """Existing holdings survive migration; only new acquisitions need a free slot."""
    competing = """EXISTS (SELECT 1 FROM task_ownership held
        WHERE held.project_id = NEW.project_id AND held.task_id != NEW.task_id
          AND held.state IN ('owned','completion_only')
          AND coalesce(held.owner_session_id, held.completion_session_id)
            = coalesce(NEW.owner_session_id, NEW.completion_session_id))"""
    return (
        "ALTER TABLE task_owner_transitions ADD COLUMN policy_version INTEGER NOT NULL DEFAULT 0 "
        "CHECK(typeof(policy_version)='integer' AND policy_version IN (0,1))",
        "CREATE TRIGGER trg_task_owner_transitions_policy_insert BEFORE INSERT ON task_owner_transitions "
        "WHEN NEW.policy_version != 1 BEGIN SELECT RAISE(ABORT,'invalid_ownership_policy'); END",
        "CREATE TRIGGER trg_task_ownership_slot_insert BEFORE INSERT ON task_ownership "
        "WHEN NEW.state IN ('owned','completion_only') AND " + competing +
        " BEGIN SELECT RAISE(ABORT,'session_task_in_progress'); END",
        "CREATE TRIGGER trg_task_ownership_slot_update BEFORE UPDATE ON task_ownership "
        "WHEN NEW.state IN ('owned','completion_only') AND "
        "(OLD.state NOT IN ('owned','completion_only') OR OLD.project_id != NEW.project_id "
        "OR coalesce(OLD.owner_session_id,OLD.completion_session_id) "
        "IS NOT coalesce(NEW.owner_session_id,NEW.completion_session_id)) AND " + competing +
        " BEGIN SELECT RAISE(ABORT,'session_task_in_progress'); END",
    )


def replacement_statements() -> tuple[str, ...]:
    from task_governance_tool.schema_review_sessions import replacement_statements as predecessor
    return tuple(statement.replace("IN (21, 22, 23, 24, 25)", "IN (21, 22, 23, 24, 25, 26)")
                 .replace("bundle.source_schema_version = 25", "bundle.source_schema_version = 26")
                 for statement in predecessor())


def validate_owned_contract(connection: sqlite3.Connection) -> None:
    from task_governance_tool import storage as s
    from task_governance_tool.schema_verification_declaration import replacement_statements as v23
    from task_governance_tool.schema_task_ownership import ownership_statements
    from task_governance_tool.schema_review_sessions import binding_statements
    if s._owned_schema_sql_fingerprint(connection, schema_version=VERSION) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    expected = dict(s._SCHEMA22_EXPECTED_OBJECTS)
    for statement in (*v23(), *ownership_statements(), *replacement_statements(),
                      *binding_statements(), *policy_statements()[1:]):
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
            "'completion_evidence_bundles_v24','completion_evidence_bundles_v25') COLLATE NOCASE").fetchone()):
        raise s._unreadable_project_state()


def validate_storage(connection: sqlite3.Connection, *, recovery: bool = False,
                     _privacy_success_cache=None) -> None:
    from task_governance_tool import storage as s
    from task_governance_tool.task_ownership import validate_storage_rows
    from task_governance_tool.review_session_repository import read_bindings
    marker = connection.execute("SELECT name FROM schema_migrations WHERE version=26").fetchone()
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
    from task_governance_tool.schema_review_sessions import validate_storage as validate_v25
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
    if version != 25:
        raise s.StorageError("migration_required", "session slot migration requires schema version 25")
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        validate_v25(connection)
        if s.schema_objects_inconsistent_with_version(connection, 25) or connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='completion_evidence_bundles_v25' COLLATE NOCASE").fetchone():
            raise s._unreadable_project_state()
        tables = tuple(row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name!='schema_migrations' ORDER BY name"))
        before = s._selected_table_projection_snapshot(connection, tables)
        columns = {name: value[0] for name, value in before.items()}

        def retained_objects():
            return tuple(tuple(row) for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' AND name NOT IN ('task_owner_transitions',"
                "'trg_task_owner_transitions_policy_insert','trg_task_ownership_slot_insert',"
                "'trg_task_ownership_slot_update','trg_task_completion_cycles_evidence_basis_insert') "
                "AND tbl_name!='completion_evidence_bundles' ORDER BY type,name"))

        objects_before = retained_objects()
        for statement in policy_statements():
            connection.execute(statement)
        connection.execute("DROP TRIGGER trg_task_completion_cycles_evidence_basis_insert")
        connection.execute("ALTER TABLE completion_evidence_bundles RENAME TO completion_evidence_bundles_v25")
        statements = replacement_statements()
        connection.execute(statements[0])
        projection = ", ".join(s._quoted_identifier(column) for column in columns["completion_evidence_bundles"])
        connection.execute(f"INSERT INTO completion_evidence_bundles ({projection}) SELECT {projection} FROM completion_evidence_bundles_v25")
        connection.execute("DROP TABLE completion_evidence_bundles_v25")
        for statement in statements[1:]:
            connection.execute(statement)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)
        if (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) != before
                or retained_objects() != objects_before):
            raise s._unreadable_project_state()
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (26,?,?)", (MIGRATION_NAME, s.utc_now()))
        validate_storage(connection)
        if (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) != before
                or retained_objects() != objects_before
                or connection.execute("SELECT 1 FROM task_owner_transitions WHERE policy_version!=0 LIMIT 1").fetchone()):
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
