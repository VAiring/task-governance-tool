"""Schema-24 ownership objects; the setup migrator owns their publication."""

from __future__ import annotations

import sqlite3


VERSION = 24
MIGRATION_NAME = "task_session_ownership"
OWNED_FINGERPRINT = "30314790260d2cd602675cf116de60561310abb3e44e830c2461cdb88726147f"


def ownership_statements() -> tuple[str, ...]:
    immutable = ("task_executions", "task_owner_transitions", "task_execution_cycles")
    return (
        """
        CREATE TABLE task_executions (
          execution_id TEXT PRIMARY KEY NOT NULL,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          started_at TEXT NOT NULL,
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
        )
        """,
        "CREATE UNIQUE INDEX idx_task_executions_identity ON task_executions(project_id, task_id, execution_id)",
        """
        CREATE TABLE task_ownership (
          task_id TEXT PRIMARY KEY NOT NULL,
          project_id TEXT NOT NULL,
          execution_id TEXT,
          generation INTEGER NOT NULL CHECK (typeof(generation) = 'integer' AND generation >= 0),
          state TEXT NOT NULL CHECK (state IN ('none', 'owned', 'completion_only', 'unknown')),
          owner_session_id TEXT,
          completion_session_id TEXT,
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (project_id, task_id, execution_id)
            REFERENCES task_executions(project_id, task_id, execution_id),
          CHECK (
            (state = 'owned' AND execution_id IS NOT NULL AND generation > 0
              AND owner_session_id IS NOT NULL AND completion_session_id IS NULL)
            OR (state = 'completion_only' AND execution_id IS NOT NULL AND generation > 0
              AND owner_session_id IS NULL AND completion_session_id IS NOT NULL)
            OR (state = 'none' AND owner_session_id IS NULL AND completion_session_id IS NULL)
            OR (state = 'unknown' AND execution_id IS NULL AND generation = 0
              AND owner_session_id IS NULL AND completion_session_id IS NULL)
          )
        )
        """,
        """
        CREATE UNIQUE INDEX idx_task_ownership_active_session
          ON task_ownership(project_id, owner_session_id) WHERE state = 'owned'
        """,
        """
        CREATE TABLE task_owner_transitions (
          transition_id TEXT PRIMARY KEY NOT NULL,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          execution_id TEXT,
          generation INTEGER NOT NULL CHECK (typeof(generation) = 'integer' AND generation > 0),
          previous_status TEXT,
          current_status TEXT NOT NULL CHECK (current_status IN
            ('ready', 'in_progress', 'review_pending', 'paused', 'blocked', 'done', 'cancelled')),
          state TEXT NOT NULL CHECK (state IN ('none', 'owned', 'completion_only')),
          actor_session_id TEXT NOT NULL,
          recovery_reason TEXT NOT NULL CHECK (length(recovery_reason) <= 1000),
          created_at TEXT NOT NULL,
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (project_id, task_id, execution_id)
            REFERENCES task_executions(project_id, task_id, execution_id),
          CHECK (previous_status IS NULL OR previous_status IN
            ('ready', 'in_progress', 'review_pending', 'paused', 'blocked', 'done', 'cancelled')),
          CHECK ((state = 'owned' AND current_status = 'in_progress' AND execution_id IS NOT NULL)
            OR (state = 'completion_only' AND current_status = 'review_pending' AND execution_id IS NOT NULL)
            OR (state = 'none' AND current_status IN ('ready', 'paused', 'blocked', 'done', 'cancelled'))),
          CHECK (recovery_reason = '' OR (current_status = 'paused'
            AND previous_status IN ('in_progress', 'review_pending')))
        )
        """,
        "CREATE INDEX idx_task_owner_transitions_task ON task_owner_transitions(project_id, task_id)",
        """
        CREATE TABLE task_execution_cycles (
          completion_cycle_id TEXT PRIMARY KEY NOT NULL,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          execution_id TEXT NOT NULL UNIQUE,
          FOREIGN KEY (project_id, task_id, execution_id)
            REFERENCES task_executions(project_id, task_id, execution_id),
          FOREIGN KEY (completion_cycle_id) REFERENCES task_completion_cycles(completion_cycle_id)
        )
        """,
        """
        CREATE TRIGGER trg_task_execution_cycles_owner_insert
        BEFORE INSERT ON task_execution_cycles
        WHEN NOT EXISTS (
          SELECT 1 FROM task_completion_cycles AS cycle
          WHERE cycle.completion_cycle_id = NEW.completion_cycle_id
            AND cycle.project_id = NEW.project_id AND cycle.task_id = NEW.task_id
        )
        BEGIN SELECT RAISE(ABORT, 'invalid_execution_cycle_owner'); END
        """,
        *(
            f"CREATE TRIGGER trg_{table}_no_{operation.lower()} "
            f"BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'immutable_task_execution_history'); END"
            for table in immutable for operation in ("UPDATE", "DELETE")
        ),
    )


def replacement_statements() -> tuple[str, ...]:
    from task_governance_tool.schema_completion_evidence_bundles import (
        _completion_evidence_bundle_v20_table_sql,
        _task_completion_cycle_evidence_basis_v21_trigger_sql,
    )
    from task_governance_tool.storage import _bundle_v20_recreated_object_statements
    return (
        _completion_evidence_bundle_v20_table_sql(schema_version=21).replace(
            "source_schema_version = 21", "source_schema_version IN (21, 22, 23, 24)"),
        *_bundle_v20_recreated_object_statements(),
        _task_completion_cycle_evidence_basis_v21_trigger_sql().replace(
            "bundle.source_schema_version = 21", "bundle.source_schema_version = 24"),
    )


def validate_owned_contract(connection: sqlite3.Connection) -> None:
    """Check exact owned SQL, preserving the predecessor's unchanged definitions."""
    from task_governance_tool import storage as s
    from task_governance_tool.schema_verification_declaration import replacement_statements as v23_statements
    if s._owned_schema_sql_fingerprint(connection, schema_version=VERSION) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    # Build from the existing closed schema inventory, replacing only this migration's objects.
    expected = dict(s._SCHEMA22_EXPECTED_OBJECTS)
    for statement in (*v23_statements(), *replacement_statements(), *ownership_statements()):
        kind, name, owner = s._schema20_statement_identity(statement)
        expected[name] = (kind, owner, s._normalized_schema_sql(statement))
    # The two v23 ADD COLUMN changes are retained and checked by the shared Evidence validator.
    for name, (kind, owner, sql) in expected.items():
        if name in {"tasks", "task_completion_cycles"}:
            continue
        row = connection.execute(
            "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ? COLLATE NOCASE", (name,),
        ).fetchone()
        if (row is None or row["type"] != kind or row["tbl_name"] != owner
            or row["sql"] is None or s._normalized_schema_sql(row["sql"]) != sql):
            raise s._unreadable_project_state()
    if (s._unowned_rebuilt_table_attachments(
        connection, table_names=tuple(dict.fromkeys((*s._SCHEMA21_REBUILT_TABLES, *s._SCHEMA22_REBUILT_TABLES))),
        expected_objects=expected.items(),
    ) or s._schema21_temporary_table_present(connection)
        or s._schema22_migration_temporary_name_collision(connection)
        or connection.execute("SELECT 1 FROM sqlite_master WHERE name IN ('completion_evidence_bundles_v22', 'completion_evidence_bundles_v23') COLLATE NOCASE").fetchone()):
        raise s._unreadable_project_state()


def validate_storage(connection: sqlite3.Connection, *, recovery: bool = False,
                     _privacy_success_cache=None) -> None:
    from task_governance_tool import storage as s
    from task_governance_tool.task_ownership import validate_storage_rows
    marker = connection.execute("SELECT name FROM schema_migrations WHERE version = 24").fetchone()
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
    s._schema20_integrity_checks(connection)
    if rejection is not None:
        raise rejection


def migrate(connection: sqlite3.Connection) -> bool:
    """Setup-only transaction, with unknown legacy owners and unchanged business bytes."""
    from task_governance_tool import storage as s
    from task_governance_tool.schema_verification_declaration import validate_storage as validate_v23
    if connection.in_transaction:
        raise s.StorageError("internal_error", "ownership migration requires an idle connection")
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
    if version != 23:
        raise s.StorageError("migration_required", "ownership migration requires schema version 23")
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        connection.execute("BEGIN IMMEDIATE")
        validate_v23(connection)
        if s.schema_objects_inconsistent_with_version(connection, 23) or connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'completion_evidence_bundles_v23' COLLATE NOCASE"
        ).fetchone():
            raise s._unreadable_project_state()
        tables = tuple(row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' ORDER BY name"))
        before = s._selected_table_projection_snapshot(connection, tables)
        columns = {name: value[0] for name, value in before.items()}

        def retained_objects():
            return tuple(tuple(row) for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                "AND name != 'trg_task_completion_cycles_evidence_basis_insert' "
                "AND tbl_name NOT IN ('completion_evidence_bundles', 'task_ownership', 'task_executions', "
                "'task_owner_transitions', 'task_execution_cycles') ORDER BY type, name"))

        objects_before = retained_objects()
        for statement in ownership_statements():
            connection.execute(statement)
        connection.execute(
            "INSERT INTO task_ownership(task_id, project_id, execution_id, generation, state, owner_session_id, completion_session_id) "
            "SELECT task_id, project_id, NULL, 0, CASE WHEN status IN ('in_progress', 'review_pending') "
            "THEN 'unknown' ELSE 'none' END, NULL, NULL FROM tasks"
        )
        connection.execute("DROP TRIGGER trg_task_completion_cycles_evidence_basis_insert")
        connection.execute("ALTER TABLE completion_evidence_bundles RENAME TO completion_evidence_bundles_v23")
        statements = replacement_statements()
        connection.execute(statements[0])
        projection = ", ".join(s._quoted_identifier(column) for column in columns["completion_evidence_bundles"])
        connection.execute(f"INSERT INTO completion_evidence_bundles ({projection}) SELECT {projection} FROM completion_evidence_bundles_v23")
        connection.execute("DROP TABLE completion_evidence_bundles_v23")
        for statement in statements[1:]:
            connection.execute(statement)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)
        if (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) != before
            or retained_objects() != objects_before):
            raise s._unreadable_project_state()
        connection.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (24, ?, ?)", (MIGRATION_NAME, s.utc_now()))
        validate_storage(connection)
        if (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) != before
            or retained_objects() != objects_before
            or any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                   for table in ("task_executions", "task_owner_transitions", "task_execution_cycles"))):
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
