"""Setup-only schema 28: retain validated Task note bodies separately from summaries."""

from __future__ import annotations

import sqlite3

VERSION = 28
MIGRATION_NAME = "task_event_note_bodies"
OWNED_FINGERPRINT = "15d8ab6cdebfa9a31f23bacc17c8a162cfc8ad84aefa06bd8676a261ee7e672f"

NOTE_COLUMN_SQL = """ALTER TABLE task_events ADD COLUMN note_body TEXT
CHECK (note_body IS NULL OR (typeof(note_body)='text' AND length(note_body)<=2000))"""


def note_statements() -> tuple[str, ...]:
    return (
        """CREATE TRIGGER trg_task_events_note_body_owner_insert
BEFORE INSERT ON task_events WHEN NEW.note_body IS NOT NULL AND (
NEW.event_type NOT IN ('note_added','task_updated','review_tier_changed')
OR NOT EXISTS (SELECT 1 FROM tasks WHERE task_id=NEW.task_id AND project_id=NEW.project_id))
BEGIN SELECT RAISE(ABORT, 'task_note_owner_invalid'); END""",
        """CREATE TRIGGER trg_task_events_note_body_immutable
BEFORE UPDATE ON task_events
WHEN OLD.note_body IS NOT NEW.note_body OR (OLD.note_body IS NOT NULL AND (
OLD.task_event_id IS NOT NEW.task_event_id OR OLD.task_id IS NOT NEW.task_id
OR OLD.project_id IS NOT NEW.project_id OR OLD.event_type IS NOT NEW.event_type))
BEGIN SELECT RAISE(ABORT, 'task_note_immutable'); END""",
        """CREATE TRIGGER trg_task_events_note_body_no_delete
BEFORE DELETE ON task_events WHEN OLD.note_body IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'task_note_immutable'); END""",
    )


def replacement_statements() -> tuple[str, ...]:
    from task_governance_tool.schema_session_slot_reacquisition import replacement_statements as predecessor
    return tuple(statement.replace("IN (21, 22, 23, 24, 25, 26, 27)",
                                   "IN (21, 22, 23, 24, 25, 26, 27, 28)")
                 .replace("bundle.source_schema_version = 27", "bundle.source_schema_version = 28")
                 for statement in predecessor())


def validate_owned_contract(connection: sqlite3.Connection) -> None:
    from task_governance_tool import storage as s
    from task_governance_tool.schema_session_slot_reacquisition import expected_objects
    if s._owned_schema_sql_fingerprint(connection, schema_version=VERSION) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    expected = expected_objects()
    for statement in (*replacement_statements(), *note_statements()):
        kind, name, owner = s._schema20_statement_identity(statement)
        expected[name] = (kind, owner, s._normalized_schema_sql(statement))
    for name, (kind, owner, sql) in expected.items():
        if name in {"tasks", "task_completion_cycles", "task_owner_transitions", "task_events"}:
            continue  # Exact ALTER COLUMN definitions are covered by the fingerprint.
        row = connection.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if (row is None or row["type"] != kind or row["tbl_name"] != owner
                or row["sql"] is None or s._normalized_schema_sql(row["sql"]) != sql):
            raise s._unreadable_project_state()
    if (s._unowned_rebuilt_table_attachments(connection,
            table_names=tuple(dict.fromkeys((*s._SCHEMA21_REBUILT_TABLES,
                *s._SCHEMA22_REBUILT_TABLES, "review_receipt_sessions"))),
            expected_objects=expected.items())
            or s._schema21_temporary_table_present(connection)
            or s._schema22_migration_temporary_name_collision(connection)
            or connection.execute("SELECT 1 FROM sqlite_master WHERE name IN "
                "('completion_evidence_bundles_v22','completion_evidence_bundles_v23',"
                "'completion_evidence_bundles_v24','completion_evidence_bundles_v25',"
                "'completion_evidence_bundles_v26','completion_evidence_bundles_v27') COLLATE NOCASE").fetchone()):
        raise s._unreadable_project_state()


def validate_note_rows(connection: sqlite3.Connection) -> None:
    from task_governance_tool import storage as s
    from task_governance_tool.tasks import validated_event_note
    from task_governance_tool.stored_task_validation import stored_task_sqlite_error
    try:
        rows = connection.execute("""SELECT event.*, task.project_id AS parent_project_id
FROM task_events AS event LEFT JOIN tasks AS task ON task.task_id=event.task_id
WHERE event.note_body IS NOT NULL ORDER BY event.rowid""").fetchall()
        for row in rows:
            if row["project_id"] != row["parent_project_id"]:
                raise s._unreadable_project_state()
            validated_event_note(row)
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc


def validate_storage(connection, *, recovery=False, _privacy_success_cache=None):
    _validate_storage_with_task_rows(connection, recovery=recovery,
        _privacy_success_cache=_privacy_success_cache)


def _validate_storage_with_task_rows(connection: sqlite3.Connection, *, recovery: bool = False,
                     _privacy_success_cache=None):
    from task_governance_tool import storage as s
    from task_governance_tool.task_ownership import validate_storage_rows
    from task_governance_tool.review_session_repository import read_bindings
    marker = connection.execute("SELECT name FROM schema_migrations WHERE version=28").fetchone()
    if (s.current_schema_version(connection) != VERSION or s.missing_migration_versions(connection, VERSION)
            or marker is None or marker["name"] != MIGRATION_NAME):
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
    validate_storage_rows(connection)
    read_bindings(connection)
    s._schema20_integrity_checks(connection)
    if rejection is not None:
        raise rejection
    return task_rows


def migrate(connection: sqlite3.Connection) -> bool:
    from task_governance_tool import storage as s
    from task_governance_tool.schema_session_slot_reacquisition import validate_storage as validate_v27
    if connection.in_transaction:
        raise s.StorageError("internal_error", "Task note migration requires an idle connection")
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
    if version != 27:
        raise s.StorageError("migration_required", "Task note migration requires schema version 27")
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        validate_v27(connection)
        if s.schema_objects_inconsistent_with_version(connection, 27):
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
                "'task_events','trg_task_completion_cycles_evidence_basis_insert',"
                "'trg_task_events_note_body_immutable','trg_task_events_note_body_no_delete',"
                "'trg_task_events_note_body_owner_insert') "
                "AND tbl_name!='completion_evidence_bundles' ORDER BY type,name"))

        objects_before = retained_objects()
        connection.execute(NOTE_COLUMN_SQL)
        for statement in note_statements():
            connection.execute(statement)
        connection.execute("DROP TRIGGER trg_task_completion_cycles_evidence_basis_insert")
        connection.execute("ALTER TABLE completion_evidence_bundles RENAME TO completion_evidence_bundles_v27")
        statements = replacement_statements()
        connection.execute(statements[0])
        projection = ", ".join(s._quoted_identifier(column) for column in columns["completion_evidence_bundles"])
        connection.execute(f"INSERT INTO completion_evidence_bundles ({projection}) SELECT {projection} FROM completion_evidence_bundles_v27")
        connection.execute("DROP TABLE completion_evidence_bundles_v27")
        for statement in statements[1:]:
            connection.execute(statement)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)

        def preserved():
            return (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) == before
                    and retained_objects() == objects_before
                    and connection.execute("SELECT 1 FROM task_events WHERE note_body IS NOT NULL LIMIT 1").fetchone() is None)

        if not preserved():
            raise s._unreadable_project_state()
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (28,?,?)", (MIGRATION_NAME, s.utc_now()))
        validate_storage(connection)
        if not preserved():
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
