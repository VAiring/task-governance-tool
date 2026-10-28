"""Schema 30: immutable collected-answer evidence and registration inventory.

Only explicit Setup invokes migration. No answer bodies enter the operational
inventory; the bounded v2 evidence contains its sanitized, typed payload.
"""
from __future__ import annotations

import json
import sqlite3

VERSION = 30
MIGRATION_NAME = "collected_review_answers"
OWNED_FINGERPRINT = "00cecd66821a5589ba99a359920393765c6c5e97fb421d4982091318a502952f"
REBUILT_TABLES = ("review_receipts", "evidence_references", "completion_evidence_bundles")


def relation_statements():
    from .schema_review_continuation import _id
    statements = [
        f"""CREATE TABLE review_receipt_provenance_v2 (
review_receipt_id TEXT PRIMARY KEY REFERENCES review_receipts(review_receipt_id) DEFERRABLE INITIALLY DEFERRED,
review_provenance_id TEXT NOT NULL UNIQUE CHECK ({_id('review_provenance_id', 'tg_review_provenance_')}),
provenance_json TEXT NOT NULL CHECK (typeof(provenance_json)='text' AND json_valid(provenance_json) AND length(CAST(provenance_json AS BLOB)) BETWEEN 1 AND 262144),
digest TEXT NOT NULL CHECK (typeof(digest)='text' AND length(digest)=71 AND substr(digest,1,7)='sha256:' AND substr(digest,8) NOT GLOB '*[^0-9a-f]*')
)""",
        """CREATE TABLE review_collection_sources (
review_receipt_id TEXT PRIMARY KEY REFERENCES review_receipt_provenance_v2(review_receipt_id) DEFERRABLE INITIALLY DEFERRED,
preparation_id TEXT NOT NULL, slot INTEGER NOT NULL CHECK (typeof(slot)='integer' AND slot BETWEEN 1 AND 8),
attempt INTEGER NOT NULL CHECK (typeof(attempt)='integer' AND attempt IN (0,1)),
reviewer_session_id TEXT NOT NULL, turn_id TEXT NOT NULL, message_id TEXT NOT NULL,
basis_digest TEXT NOT NULL, basis_json TEXT NOT NULL CHECK (typeof(basis_json)='text' AND json_valid(basis_json) AND length(CAST(basis_json AS BLOB)) BETWEEN 1 AND 32768),
original_digest TEXT NOT NULL, payload_digest TEXT NOT NULL, submission_digest TEXT NOT NULL,
UNIQUE(preparation_id,slot,attempt,reviewer_session_id,turn_id,message_id),
UNIQUE(reviewer_session_id,turn_id,message_id),
FOREIGN KEY(preparation_id,submission_digest) REFERENCES review_collection_dispatches(preparation_id,submission_digest) DEFERRABLE INITIALLY DEFERRED
)""",
        """CREATE TABLE review_collection_dispatches (
preparation_id TEXT NOT NULL, submission_digest TEXT NOT NULL,
project_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(task_id),
inventory_json TEXT NOT NULL CHECK (typeof(inventory_json)='text' AND json_valid(inventory_json) AND length(CAST(inventory_json AS BLOB)) BETWEEN 1 AND 32768),
PRIMARY KEY(preparation_id,submission_digest)
)""",
        """CREATE TABLE review_collection_dispatch_entries (
preparation_id TEXT NOT NULL, submission_digest TEXT NOT NULL,
slot INTEGER NOT NULL CHECK (typeof(slot)='integer' AND slot BETWEEN 1 AND 8),
review_receipt_id TEXT NOT NULL REFERENCES review_receipts(review_receipt_id),
entry_json TEXT NOT NULL CHECK (typeof(entry_json)='text' AND json_valid(entry_json) AND length(CAST(entry_json AS BLOB)) BETWEEN 1 AND 32768),
PRIMARY KEY(preparation_id,submission_digest,slot),
UNIQUE(preparation_id,submission_digest,review_receipt_id),
FOREIGN KEY(preparation_id,submission_digest) REFERENCES review_collection_dispatches(preparation_id,submission_digest) DEFERRABLE INITIALLY DEFERRED
)""",
        """CREATE TABLE review_collected_captures (
continuation_id TEXT NOT NULL REFERENCES review_continuations(continuation_id),
session_id TEXT NOT NULL, source_continuation_id TEXT REFERENCES review_continuations(continuation_id),
metadata_json TEXT NOT NULL CHECK (typeof(metadata_json)='text' AND json_valid(metadata_json) AND length(CAST(metadata_json AS BLOB)) BETWEEN 1 AND 32768),
metadata_digest TEXT NOT NULL, projection_json TEXT NOT NULL CHECK (typeof(projection_json)='text' AND json_valid(projection_json) AND length(CAST(projection_json AS BLOB)) BETWEEN 1 AND 32768),
result_receipt_id TEXT UNIQUE REFERENCES review_receipts(review_receipt_id),
PRIMARY KEY(continuation_id,session_id)
)""",
    ]
    statements.extend(
        f"CREATE TRIGGER trg_{table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable_collected_review'); END"
        for table in ("review_receipt_provenance_v2", "review_collection_sources", "review_collection_dispatches", "review_collection_dispatch_entries")
        for operation in ("UPDATE", "DELETE")
    )
    statements.extend((
        """CREATE TRIGGER trg_review_collected_captures_no_delete BEFORE DELETE ON review_collected_captures BEGIN SELECT RAISE(ABORT,'immutable_collected_capture'); END""",
        """CREATE TRIGGER trg_review_collected_captures_publish BEFORE UPDATE ON review_collected_captures WHEN
OLD.continuation_id IS NOT NEW.continuation_id OR OLD.session_id IS NOT NEW.session_id OR
OLD.source_continuation_id IS NOT NEW.source_continuation_id OR OLD.metadata_json IS NOT NEW.metadata_json OR
OLD.metadata_digest IS NOT NEW.metadata_digest OR OLD.projection_json IS NOT NEW.projection_json OR
OLD.result_receipt_id IS NOT NULL OR NEW.result_receipt_id IS NULL
BEGIN SELECT RAISE(ABORT,'immutable_collected_capture'); END""",
    ))
    return tuple(statements)


def receipt_statement():
    from .schema_review_continuation import receipt_statement as predecessor
    sql = predecessor().replace("review_provenance_basis_version IN (0,1)", "review_provenance_basis_version IN (0,1,2)")
    sql = sql.replace("review_provenance_id TEXT REFERENCES review_receipt_provenance(review_provenance_id) DEFERRABLE INITIALLY DEFERRED", "review_provenance_id TEXT")
    sql = sql.replace("'pass', 'changes_requested', 'not_required'", "'pass', 'changes_requested', 'not_required', 'unable_to_review'")
    sql = sql.replace("verdict IN ('pass', 'changes_requested')", "verdict IN ('pass', 'changes_requested', 'unable_to_review')")
    # This additional matrix keeps the newly widened verdict out of legacy arms.
    return sql.rsplit(")", 1)[0] + ", CHECK (verdict!='unable_to_review' OR (receipt_kind='independent' AND review_provenance_basis_version=2))\n)"


def _owned_names():
    from . import storage as s
    return {name for inventory in (s._SCHEMA_TABLE_INTRODUCED_VERSION, s._SCHEMA_INDEX_INTRODUCED_VERSION, s._SCHEMA_TRIGGER_INTRODUCED_VERSION) for name, version in inventory.items() if version <= VERSION}


def owned_fingerprint(connection):
    from .schema_review_continuation import _owned_fingerprint
    return _owned_fingerprint(connection, _owned_names())


def validate_owned_contract(connection):
    from . import storage as s
    names = _owned_names()
    if owned_fingerprint(connection) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    for table in REBUILT_TABLES:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE", (table + "_v29",)).fetchone():
            raise s._unreadable_project_state()
        if any(row["name"] not in names for row in connection.execute("SELECT name FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,))):
            raise s._unreadable_project_state()


def _apply_delta_locked(connection):
    from . import storage as s
    if not connection.in_transaction or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
        raise s.StorageError("internal_error", "answer migration requires its admitted writer")
    names = _owned_names()
    attached = []
    for table in REBUILT_TABLES:
        rows = connection.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name", (table,)).fetchall()
        if any(row["name"] not in names for row in rows):
            raise s._unreadable_project_state()
        attached.extend(rows)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE", (table + "_v29",)).fetchone():
            raise s._unreadable_project_state()
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        if table == "review_receipts":
            sql = receipt_statement()
        elif table == "evidence_references":
            sql = sql.replace("'trusted_caller',", "'trusted_caller', 'taskgov_answer_collector',")
        else:
            sql = sql.replace("28, 29)", "28, 29, 30)").replace("(source_schema_version = 29 AND bundle_version = 3)", "(source_schema_version = 29 AND bundle_version = 3) OR (source_schema_version = 30 AND bundle_version = 4)")
            sql = sql.replace("(bundle_version!=3 AND continuation_basis_json IS NULL)", "(bundle_version NOT IN (3,4) AND continuation_basis_json IS NULL) OR bundle_version=4")
            sql = sql.rsplit(")",1)[0] + ", CHECK (source_schema_version!=30 OR bundle_version=4)\n)"
        columns = [row["name"] for row in connection.execute(f"PRAGMA table_xinfo({s._quoted_identifier(table)})")]
        projection = ",".join(s._quoted_identifier(column) for column in columns)
        connection.execute(f"ALTER TABLE {s._quoted_identifier(table)} RENAME TO {s._quoted_identifier(table + '_v29')}")
        connection.execute(sql)
        connection.execute(f"INSERT INTO {s._quoted_identifier(table)} (rowid,{projection}) SELECT rowid,{projection} FROM {s._quoted_identifier(table + '_v29')}")
        connection.execute(f"DROP TABLE {s._quoted_identifier(table + '_v29')}")
    for statement in relation_statements():
        connection.execute(statement)
    for row in attached:
        sql = row["sql"]
        if row["name"] == "trg_review_receipts_provenance_basis_insert":
            sql = sql.replace("NEW.review_provenance_basis_version = 1", "NEW.review_provenance_basis_version IN (1,2)")
        connection.execute(sql)
    union = """(SELECT continuation_id,prior_continuation_id,session_id,original_result_digest,result_receipt_id FROM review_continuation_retained_originals UNION ALL SELECT continuation_id,source_continuation_id AS prior_continuation_id,session_id,substr(json_extract(metadata_json,'$.original.digest'),8) AS original_result_digest,result_receipt_id FROM review_collected_captures) AS retained"""
    for name in ("trg_review_continuation_reviewers_publication", "trg_review_receipt_judgments_binding"):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,)).fetchone()
        sql = row[0].replace("review_continuation_retained_originals AS retained",union)
        connection.execute(f"DROP TRIGGER {name}")
        connection.execute(sql)
    name = "trg_review_continuations_monotone"
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,)).fetchone()[0]
    sql = sql.replace("BEGIN SELECT RAISE", "OR (NEW.state='registered' AND EXISTS (SELECT 1 FROM review_collected_captures WHERE continuation_id=OLD.continuation_id AND result_receipt_id IS NULL))\nBEGIN SELECT RAISE")
    connection.execute(f"DROP TRIGGER {name}")
    connection.execute(sql)
    name = "trg_task_completion_cycles_evidence_basis_insert"
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,)).fetchone()[0]
    sql = sql.replace("bundle.source_schema_version = 29", "bundle.source_schema_version IN (29,30)")
    sql = sql.replace("((NEW.gate_basis_version = 1 AND bundle.bundle_version = 2) OR (NEW.gate_basis_version = 2 AND bundle.bundle_version = 3))", "((bundle.source_schema_version=29 AND ((NEW.gate_basis_version=1 AND bundle.bundle_version=2) OR (NEW.gate_basis_version=2 AND bundle.bundle_version=3))) OR (bundle.source_schema_version=30 AND bundle.bundle_version=4 AND ((NEW.gate_basis_version=1 AND bundle.continuation_basis_json IS NULL) OR (NEW.gate_basis_version=2 AND bundle.continuation_basis_json IS NOT NULL))))")
    connection.execute(f"DROP TRIGGER {name}")
    connection.execute(sql)


def migrate(connection):
    from . import storage as s
    from . import schema_preservation
    from .schema_review_continuation import validate_storage as predecessor
    if connection.in_transaction:
        raise s.StorageError("internal_error", "answer migration requires an idle connection")
    version = s.current_schema_version(connection)
    if version == VERSION:
        connection.execute("BEGIN")
        try:
            validate_storage(connection)
        finally:
            connection.rollback()
        return False
    if version != 29:
        raise s.StorageError("migration_required", "answer migration requires schema version 29")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1 or connection.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0:
        raise s._unreadable_project_state()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        predecessor(connection)
        if s.schema_objects_inconsistent_with_version(connection, 29):
            raise s._unreadable_project_state()
        new_names = {s._schema20_statement_identity(statement)[1] for statement in relation_statements()}
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name IN (SELECT value FROM json_each(?)) COLLATE NOCASE", (json.dumps(sorted(new_names)),)).fetchone():
            raise s._unreadable_project_state()
        changed_names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE tbl_name IN (SELECT value FROM json_each(?)) AND sql IS NOT NULL", (json.dumps(REBUILT_TABLES),))} | new_names | {"trg_review_continuation_reviewers_publication","trg_review_receipt_judgments_binding","trg_review_continuations_monotone","trg_task_completion_cycles_evidence_basis_insert"}
        before = schema_preservation.capture(connection, changed_object_names=changed_names)
        _apply_delta_locked(connection)
        validate_owned_contract(connection)
        schema_preservation.assert_preserved(connection, before)
        marker_time = s.utc_now()
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (30,?,?)", (MIGRATION_NAME, marker_time))
        validate_storage(connection)
        schema_preservation.assert_preserved(connection, before, added_marker=(VERSION, MIGRATION_NAME, marker_time))
        connection.commit()
        return True
    except sqlite3.Error as exc:
        connection.rollback()
        if s.is_sqlite_busy_or_locked(exc):
            raise s.StorageError("database_busy",s.DATABASE_BUSY_MESSAGE) from exc
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
    _validate_storage_with_task_rows(connection, recovery=recovery,
        _privacy_success_cache=_privacy_success_cache)


def _validate_storage_with_task_rows(connection, *, recovery=False, _privacy_success_cache=None):
    from . import storage as s
    from .review_answer_repository import validate_storage_rows
    marker = connection.execute("SELECT name FROM schema_migrations WHERE version=30").fetchone()
    if s.current_schema_version(connection) != VERSION or s.missing_migration_versions(connection, VERSION) or marker is None or marker["name"] != MIGRATION_NAME:
        raise s._unreadable_project_state()
    validate_owned_contract(connection)
    rejection=None
    task_rows = None
    if recovery:
        try:
            s.validate_evidence_ledger_storage_for_recovery(connection,_privacy_success_cache=_privacy_success_cache)
        except s.StoredTaskVerificationError as exc:
            rejection=exc
        s._validated_verification_runner_references(connection)
        s.validate_completion_cycle_storage(connection)
    else:
        task_rows = s._validate_schema21_admitted_rows(connection, _privacy_success_cache=_privacy_success_cache)
    from .schema_task_event_notes import validate_note_rows
    from .task_ownership import validate_storage_rows as ownership
    from .review_session_repository import read_bindings
    from .review_continuation_repository import validate_judgment_storage
    validate_note_rows(connection)
    ownership(connection)
    read_bindings(connection)
    validate_judgment_storage(connection)
    validate_storage_rows(connection)
    s._schema20_integrity_checks(connection)
    if rejection is not None:
        raise rejection
    return task_rows
