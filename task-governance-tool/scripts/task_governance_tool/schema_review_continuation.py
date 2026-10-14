"""Closed schema-29 continuation relations, pending combined activation.

Storage owns connection admission and migration transactions. The SQL guards
protect immutable lineage and monotone publication; repository checks also
validate canonical/privacy content, source assertions and the complete basis.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

VERSION = 29
MIGRATION_NAME = "review_continuations"
OWNED_FINGERPRINT = "9eb7871f4d652c464e499e5ac7dcad15ef578c44ca272aab193185f3950f0f17"
REBUILT_TABLES = ("review_receipts", "task_completion_cycles", "completion_evidence_bundles")


def _id(column: str, prefix: str) -> str:
    return (f"typeof({column})='text' AND length({column})={len(prefix) + 16} "
            f"AND substr({column},1,{len(prefix)})='{prefix}' "
            f"AND substr({column},{len(prefix) + 1}) NOT GLOB '*[^0-9a-f]*'")


def _hex(column: str) -> str:
    return f"typeof({column})='text' AND length({column})=64 AND {column} NOT GLOB '*[^0-9a-f]*'"


def relation_statements() -> tuple[str, ...]:
    return (
        f"""CREATE TABLE review_continuations (
continuation_id TEXT PRIMARY KEY CHECK ({_id('continuation_id', 'tg_review_continuation_')}),
project_id TEXT NOT NULL, task_id TEXT NOT NULL,
target_generation INTEGER NOT NULL CHECK (typeof(target_generation)='integer' AND target_generation>0),
preparation_id TEXT NOT NULL CHECK ({_id('preparation_id', 'tg_review_preparation_')}),
sequence INTEGER NOT NULL CHECK (typeof(sequence)='integer' AND sequence BETWEEN 1 AND 64),
predecessor_id TEXT UNIQUE REFERENCES review_continuations(continuation_id),
basis_json TEXT NOT NULL CHECK (typeof(basis_json)='text' AND length(CAST(basis_json AS BLOB))<=16384 AND json_valid(basis_json)),
basis_digest TEXT NOT NULL CHECK ({_hex('basis_digest')}),
selection_digest TEXT NOT NULL CHECK ({_hex('selection_digest')}),
route TEXT NOT NULL CHECK (route IN ('manual','integrated')),
state TEXT NOT NULL CHECK (state IN ('preparing','prepared','registered','superseded')),
created_at TEXT NOT NULL, registered_at TEXT, retired_at TEXT,
retirement_digest TEXT CHECK (retirement_digest IS NULL OR ({_hex('retirement_digest')})),
UNIQUE (preparation_id,sequence),
FOREIGN KEY (project_id,task_id) REFERENCES tasks(project_id,task_id),
CHECK ((sequence=1 AND predecessor_id IS NULL) OR (sequence>1 AND predecessor_id IS NOT NULL)),
CHECK ((state='registered' AND registered_at IS NOT NULL) OR (state!='registered' AND registered_at IS NULL)),
CHECK ((state='superseded' AND retired_at IS NOT NULL AND retirement_digest IS NOT NULL) OR
       (state!='superseded' AND retired_at IS NULL AND retirement_digest IS NULL))
)""",
        """CREATE UNIQUE INDEX idx_review_continuations_pending
ON review_continuations(project_id,task_id,target_generation) WHERE state IN ('preparing','prepared')""",
        f"""CREATE TABLE review_continuation_reviewers (
continuation_id TEXT NOT NULL REFERENCES review_continuations(continuation_id),
session_id TEXT NOT NULL,
action TEXT NOT NULL CHECK (action IN ('supplement','reassess')),
prior_receipt_id TEXT REFERENCES review_receipts(review_receipt_id),
reason TEXT NOT NULL CHECK (typeof(reason)='text' AND length(reason) BETWEEN 1 AND 1000),
result_receipt_id TEXT UNIQUE REFERENCES review_receipts(review_receipt_id),
original_result_digest TEXT CHECK (original_result_digest IS NULL OR ({_hex('original_result_digest')})),
PRIMARY KEY (continuation_id,session_id),
CHECK ((action='supplement' AND prior_receipt_id IS NULL) OR (action='reassess' AND prior_receipt_id IS NOT NULL)),
CHECK ((result_receipt_id IS NULL AND original_result_digest IS NULL) OR
       (result_receipt_id IS NOT NULL AND original_result_digest IS NOT NULL))
)""",
        """CREATE TABLE review_receipt_judgments (
review_receipt_id TEXT PRIMARY KEY REFERENCES review_receipts(review_receipt_id) DEFERRABLE INITIALLY DEFERRED,
judgment_revision INTEGER NOT NULL CHECK (typeof(judgment_revision)='integer' AND judgment_revision>0),
prior_receipt_id TEXT UNIQUE REFERENCES review_receipts(review_receipt_id),
continuation_id TEXT REFERENCES review_continuations(continuation_id),
UNIQUE (review_receipt_id,judgment_revision),
CHECK ((judgment_revision=1 AND prior_receipt_id IS NULL) OR
       (judgment_revision>1 AND prior_receipt_id IS NOT NULL AND continuation_id IS NOT NULL))
)""",
        f"""CREATE TABLE review_reassessment_resolutions (
review_finding_id TEXT NOT NULL REFERENCES review_findings(review_finding_id),
successor_receipt_id TEXT NOT NULL REFERENCES review_receipts(review_receipt_id),
continuation_id TEXT NOT NULL REFERENCES review_continuations(continuation_id),
resolution_summary TEXT NOT NULL CHECK (typeof(resolution_summary)='text' AND length(resolution_summary) BETWEEN 1 AND 1000),
resolution_digest TEXT NOT NULL CHECK ({_hex('resolution_digest')}),
created_at TEXT NOT NULL,
PRIMARY KEY (review_finding_id,successor_receipt_id)
)""",
        f"""CREATE TABLE review_continuation_retained_originals (
continuation_id TEXT NOT NULL REFERENCES review_continuations(continuation_id),
prior_continuation_id TEXT NOT NULL REFERENCES review_continuations(continuation_id),
session_id TEXT NOT NULL,
original_result_digest TEXT NOT NULL CHECK ({_hex('original_result_digest')}),
metadata_digest TEXT NOT NULL CHECK ({_hex('metadata_digest')}),
result_receipt_id TEXT REFERENCES review_receipts(review_receipt_id),
PRIMARY KEY (continuation_id,session_id),
FOREIGN KEY (prior_continuation_id,session_id) REFERENCES review_continuation_reviewers(continuation_id,session_id)
)""",
        """CREATE TRIGGER trg_review_continuations_predecessor
BEFORE INSERT ON review_continuations WHEN NEW.state!='preparing' OR
(NEW.predecessor_id IS NOT NULL AND NOT EXISTS (
SELECT 1 FROM review_continuations AS old WHERE old.continuation_id=NEW.predecessor_id
AND old.preparation_id=NEW.preparation_id AND old.sequence+1=NEW.sequence
AND old.project_id=NEW.project_id AND old.task_id=NEW.task_id
AND old.target_generation=NEW.target_generation AND old.basis_json=NEW.basis_json
AND old.basis_digest=NEW.basis_digest AND old.route=NEW.route AND old.state IN ('registered','superseded')))
BEGIN SELECT RAISE(ABORT,'review_continuation_predecessor_invalid'); END""",
        """CREATE TRIGGER trg_review_continuations_monotone
BEFORE UPDATE ON review_continuations WHEN
OLD.continuation_id IS NOT NEW.continuation_id OR OLD.project_id IS NOT NEW.project_id
OR OLD.task_id IS NOT NEW.task_id OR OLD.target_generation IS NOT NEW.target_generation
OR OLD.preparation_id IS NOT NEW.preparation_id OR OLD.sequence IS NOT NEW.sequence
OR OLD.predecessor_id IS NOT NEW.predecessor_id OR OLD.basis_json IS NOT NEW.basis_json
OR OLD.basis_digest IS NOT NEW.basis_digest OR OLD.selection_digest IS NOT NEW.selection_digest
OR OLD.route IS NOT NEW.route OR OLD.created_at IS NOT NEW.created_at
OR NOT ((OLD.state='preparing' AND NEW.state='prepared' AND NEW.registered_at IS NULL)
OR (OLD.state='prepared' AND NEW.state='registered' AND NEW.registered_at IS NOT NULL)
OR (OLD.state IN ('preparing','prepared') AND NEW.state='superseded' AND NEW.registered_at IS NULL))
OR (NEW.state!='superseded' AND (OLD.retired_at IS NOT NEW.retired_at OR OLD.retirement_digest IS NOT NEW.retirement_digest))
OR NOT ((SELECT COUNT(*) FROM review_continuation_reviewers WHERE continuation_id=OLD.continuation_id) BETWEEN 1 AND 8)
OR (NEW.state='registered' AND EXISTS (SELECT 1 FROM review_continuation_reviewers
WHERE continuation_id=OLD.continuation_id AND result_receipt_id IS NULL))
OR (NEW.state='registered' AND EXISTS (SELECT 1 FROM review_continuation_retained_originals
WHERE continuation_id=OLD.continuation_id AND result_receipt_id IS NULL))
BEGIN SELECT RAISE(ABORT,'review_continuation_transition_invalid'); END""",
        """CREATE TRIGGER trg_review_continuation_reviewers_admission
BEFORE INSERT ON review_continuation_reviewers WHEN NEW.result_receipt_id IS NOT NULL
OR NOT EXISTS (SELECT 1 FROM review_continuations WHERE continuation_id=NEW.continuation_id AND state='preparing')
OR (SELECT COUNT(*) FROM review_continuation_reviewers WHERE continuation_id=NEW.continuation_id)>=8
BEGIN SELECT RAISE(ABORT,'review_continuation_selection_invalid'); END""",
        """CREATE TRIGGER trg_review_continuation_reviewers_publication
BEFORE UPDATE ON review_continuation_reviewers WHEN
OLD.continuation_id IS NOT NEW.continuation_id OR OLD.session_id IS NOT NEW.session_id
OR OLD.action IS NOT NEW.action OR OLD.prior_receipt_id IS NOT NEW.prior_receipt_id OR OLD.reason IS NOT NEW.reason
OR OLD.result_receipt_id IS NOT NULL OR NEW.result_receipt_id IS NULL OR NEW.original_result_digest IS NULL
OR NOT EXISTS (SELECT 1 FROM review_continuations AS continuation
JOIN review_receipt_judgments AS judgment ON judgment.continuation_id=continuation.continuation_id
JOIN review_receipt_sessions AS binding ON binding.review_receipt_id=judgment.review_receipt_id
WHERE continuation.continuation_id=NEW.continuation_id AND (continuation.state='prepared' OR
(continuation.state='superseded' AND EXISTS (SELECT 1 FROM review_continuation_retained_originals AS retained
JOIN review_continuations AS consumer ON consumer.continuation_id=retained.continuation_id
WHERE retained.prior_continuation_id=NEW.continuation_id AND retained.session_id=NEW.session_id
AND retained.original_result_digest=NEW.original_result_digest AND consumer.state='prepared' AND retained.result_receipt_id IS NULL)))
AND judgment.review_receipt_id=NEW.result_receipt_id AND judgment.prior_receipt_id IS NEW.prior_receipt_id
AND binding.session_id=NEW.session_id AND binding.binding_source='handoff'
AND binding.original_result_digest=NEW.original_result_digest)
BEGIN SELECT RAISE(ABORT,'review_continuation_publication_invalid'); END""",
        """CREATE TRIGGER trg_review_receipt_judgments_binding
BEFORE INSERT ON review_receipt_judgments WHEN
NOT EXISTS (SELECT 1 FROM review_receipts WHERE review_receipt_id=NEW.review_receipt_id AND judgment_revision=NEW.judgment_revision)
OR (NEW.continuation_id IS NOT NULL AND NOT EXISTS (
SELECT 1 FROM review_continuations AS continuation
JOIN review_continuation_reviewers AS selected ON selected.continuation_id=continuation.continuation_id
JOIN review_receipt_sessions AS binding ON binding.session_id=selected.session_id
JOIN review_receipts AS receipt ON receipt.review_receipt_id=binding.review_receipt_id
JOIN evidence_references AS reference ON reference.source_id=receipt.review_receipt_id
AND reference.source_kind='review_receipt' AND reference.source_state='recorded'
WHERE continuation.continuation_id=NEW.continuation_id AND (continuation.state='prepared' OR
(continuation.state='superseded' AND EXISTS (SELECT 1 FROM review_continuation_retained_originals AS retained
JOIN review_continuations AS consumer ON consumer.continuation_id=retained.continuation_id
WHERE retained.prior_continuation_id=NEW.continuation_id AND retained.session_id=binding.session_id
AND retained.original_result_digest=binding.original_result_digest AND consumer.state='prepared' AND retained.result_receipt_id IS NULL)))
AND receipt.review_receipt_id=NEW.review_receipt_id AND receipt.receipt_kind='independent'
AND receipt.project_id=continuation.project_id AND receipt.task_id=continuation.task_id
AND receipt.target_generation=continuation.target_generation
AND selected.prior_receipt_id IS NEW.prior_receipt_id AND selected.result_receipt_id IS NULL
AND binding.binding_source='handoff' AND binding.execution_id=json_extract(continuation.basis_json,'$.execution_id')
AND reference.contract_revision=json_extract(continuation.basis_json,'$.contract_revision')
AND reference.authority_snapshot_id=json_extract(continuation.basis_json,'$.authority_snapshot_id')
AND reference.acceptance_criterion_id IS json_extract(continuation.basis_json,'$.acceptance_criterion_id')
AND reference.verification_criterion_id IS json_extract(continuation.basis_json,'$.verification_criterion_id')
AND receipt.target_kind=json_extract(continuation.basis_json,'$.target_kind')
AND receipt.target_value=json_extract(continuation.basis_json,'$.target_value')
AND receipt.target_base_revision=json_extract(continuation.basis_json,'$.target_base_revision')))
OR (NEW.judgment_revision>1 AND NOT EXISTS (
SELECT 1 FROM review_receipts AS old JOIN review_receipts AS fresh
ON fresh.review_receipt_id=NEW.review_receipt_id
JOIN review_receipt_sessions AS prior_binding ON prior_binding.review_receipt_id=old.review_receipt_id
JOIN review_receipt_sessions AS fresh_binding ON fresh_binding.review_receipt_id=fresh.review_receipt_id
JOIN review_receipt_judgments AS prior_judgment ON prior_judgment.review_receipt_id=old.review_receipt_id
JOIN evidence_references AS old_ref ON old_ref.source_id=old.review_receipt_id AND old_ref.source_kind='review_receipt' AND old_ref.source_state='recorded'
JOIN evidence_references AS new_ref ON new_ref.source_id=fresh.review_receipt_id AND new_ref.source_kind='review_receipt' AND new_ref.source_state='recorded'
WHERE old.review_receipt_id=NEW.prior_receipt_id AND old.judgment_revision+1=NEW.judgment_revision
AND prior_judgment.judgment_revision=old.judgment_revision AND old.reviewer_key=fresh.reviewer_key
AND old.project_id=fresh.project_id AND old.task_id=fresh.task_id
AND old.target_kind=fresh.target_kind AND old.target_value=fresh.target_value
AND old.target_base_revision=fresh.target_base_revision AND old.target_generation=fresh.target_generation
AND old.receipt_kind='independent' AND prior_binding.session_id=fresh_binding.session_id
AND old_ref.contract_revision=new_ref.contract_revision AND old_ref.authority_snapshot_id=new_ref.authority_snapshot_id
AND old_ref.acceptance_criterion_id IS new_ref.acceptance_criterion_id
AND old_ref.verification_criterion_id IS new_ref.verification_criterion_id))
BEGIN SELECT RAISE(ABORT,'review_judgment_binding_invalid'); END""",
        """CREATE TRIGGER trg_review_reassessment_resolutions_binding
BEFORE INSERT ON review_reassessment_resolutions WHEN NOT EXISTS (
WITH RECURSIVE chain(id) AS (
SELECT prior_receipt_id FROM review_receipt_judgments WHERE review_receipt_id=NEW.successor_receipt_id
UNION SELECT judgment.prior_receipt_id FROM review_receipt_judgments AS judgment JOIN chain ON judgment.review_receipt_id=chain.id)
SELECT 1 FROM review_findings AS finding JOIN chain ON finding.review_receipt_id=chain.id
JOIN review_receipt_judgments AS successor ON successor.review_receipt_id=NEW.successor_receipt_id
JOIN review_continuations AS continuation ON continuation.continuation_id=successor.continuation_id
WHERE finding.review_finding_id=NEW.review_finding_id AND finding.status='resolved'
AND successor.continuation_id=NEW.continuation_id AND continuation.state IN ('registered','superseded'))
BEGIN SELECT RAISE(ABORT,'review_reassessment_resolution_invalid'); END""",
        """CREATE TRIGGER trg_review_retained_originals_admission
BEFORE INSERT ON review_continuation_retained_originals WHEN NEW.result_receipt_id IS NOT NULL
OR NOT EXISTS (
WITH RECURSIVE chain(id) AS (
SELECT predecessor_id FROM review_continuations WHERE continuation_id=NEW.continuation_id
UNION SELECT row.predecessor_id FROM review_continuations AS row JOIN chain ON row.continuation_id=chain.id)
SELECT 1 FROM review_continuations AS consumer JOIN review_continuations AS old
ON old.continuation_id=NEW.prior_continuation_id JOIN chain ON chain.id=old.continuation_id
JOIN review_continuation_reviewers AS selected ON selected.continuation_id=old.continuation_id
WHERE consumer.continuation_id=NEW.continuation_id AND consumer.state='preparing' AND old.state='superseded'
AND consumer.basis_json=old.basis_json AND selected.session_id=NEW.session_id AND selected.result_receipt_id IS NULL)
OR EXISTS (SELECT 1 FROM review_continuation_reviewers WHERE continuation_id=NEW.continuation_id AND session_id=NEW.session_id)
OR ((SELECT COUNT(*) FROM review_continuation_reviewers WHERE continuation_id=NEW.continuation_id)
+(SELECT COUNT(*) FROM review_continuation_retained_originals WHERE continuation_id=NEW.continuation_id))>=8
BEGIN SELECT RAISE(ABORT,'review_retained_original_invalid'); END""",
        """CREATE TRIGGER trg_review_retained_originals_publication
BEFORE UPDATE ON review_continuation_retained_originals WHEN
OLD.continuation_id IS NOT NEW.continuation_id OR OLD.prior_continuation_id IS NOT NEW.prior_continuation_id
OR OLD.session_id IS NOT NEW.session_id OR OLD.original_result_digest IS NOT NEW.original_result_digest
OR OLD.metadata_digest IS NOT NEW.metadata_digest OR OLD.result_receipt_id IS NOT NULL OR NEW.result_receipt_id IS NULL
OR NOT EXISTS (SELECT 1 FROM review_continuations AS consumer
JOIN review_continuation_reviewers AS prior ON prior.continuation_id=NEW.prior_continuation_id
JOIN review_receipt_sessions AS binding ON binding.review_receipt_id=prior.result_receipt_id
WHERE consumer.continuation_id=NEW.continuation_id AND consumer.state='prepared'
AND prior.session_id=NEW.session_id AND prior.result_receipt_id=NEW.result_receipt_id
AND binding.session_id=NEW.session_id AND binding.original_result_digest=NEW.original_result_digest)
BEGIN SELECT RAISE(ABORT,'review_retained_original_publication_invalid'); END""",
        *_immutable_statements(),
    )


def _immutable_statements() -> tuple[str, ...]:
    result = []
    for table in ("review_continuations", "review_continuation_reviewers",
                  "review_receipt_judgments", "review_reassessment_resolutions", "review_continuation_retained_originals"):
        result.append(f"""CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT,'review_continuation_immutable'); END""")
        if table in ("review_receipt_judgments", "review_reassessment_resolutions"):
            result.append(f"""CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT,'review_continuation_immutable'); END""")
    return tuple(result)


def receipt_statement() -> str:
    from .storage import git_snapshot_review_receipts_schema_sql
    # Existing assertion/provenance columns keep their order and meaning.
    statement = git_snapshot_review_receipts_schema_sql().replace("review_receipts_v6", "review_receipts")
    return statement.replace("  UNIQUE (task_id, target_generation, reviewer_key),", """
  review_provenance_basis_version INTEGER NOT NULL DEFAULT 0 CHECK (review_provenance_basis_version IN (0,1)),
  review_provenance_id TEXT REFERENCES review_receipt_provenance(review_provenance_id) DEFERRABLE INITIALLY DEFERRED,
  judgment_revision INTEGER NOT NULL DEFAULT 1 CHECK (typeof(judgment_revision)='integer' AND judgment_revision>0),
  UNIQUE (task_id,target_generation,reviewer_key,judgment_revision),
  UNIQUE (review_receipt_id,judgment_revision),
  FOREIGN KEY (review_receipt_id,judgment_revision) REFERENCES review_receipt_judgments(review_receipt_id,judgment_revision) DEFERRABLE INITIALLY DEFERRED,""")


def cycle_statement(predecessor_sql: str) -> str:
    """Widen only the immutable gate tag on the admitted predecessor table."""
    return predecessor_sql.replace("gate_basis_version IN (0, 1)", "gate_basis_version IN (0, 1, 2)").replace(
        "gate_basis_version = 1", "gate_basis_version IN (1, 2)")


def bundle_statement() -> str:
    from .schema_task_event_notes import replacement_statements
    statement = replacement_statements()[0].replace(
        "IN (21, 22, 23, 24, 25, 26, 27, 28)", "IN (21, 22, 23, 24, 25, 26, 27, 28, 29)"
    ).replace("bundle_version = 2", "(bundle_version = 2 OR (source_schema_version = 29 AND bundle_version = 3))")
    return statement.replace("      UNIQUE (project_id, task_id, completion_evidence_bundle_id),", """
      continuation_basis_json TEXT CHECK (continuation_basis_json IS NULL OR
        (typeof(continuation_basis_json)='text' AND length(CAST(continuation_basis_json AS BLOB))<=16777216 AND json_valid(continuation_basis_json))),
      CHECK ((bundle_version=3 AND continuation_basis_json IS NOT NULL) OR
             (bundle_version!=3 AND continuation_basis_json IS NULL)),
      UNIQUE (project_id, task_id, completion_evidence_bundle_id),""")


def _owned_names() -> set[str]:
    from . import storage as s
    names = {name for inventory in (s._SCHEMA_TABLE_INTRODUCED_VERSION,
        s._SCHEMA_INDEX_INTRODUCED_VERSION, s._SCHEMA_TRIGGER_INTRODUCED_VERSION)
        for name, version in inventory.items() if version <= VERSION}
    names.update(s._schema20_statement_identity(statement)[1] for statement in relation_statements())
    return names


def owned_fingerprint(connection: sqlite3.Connection) -> str:
    from . import storage as s
    names = sorted(_owned_names())
    rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name IN "
        "(SELECT value FROM json_each(?)) AND sql IS NOT NULL ORDER BY type,name", (json.dumps(names),)).fetchall()
    if len(rows) != len(names):
        raise s._unreadable_project_state()
    payload = [(row["type"], row["name"], row["tbl_name"], s._normalized_schema_sql(row["sql"])) for row in rows]
    return hashlib.sha256(b"taskgov-owned-schema-v1\0" + json.dumps(payload,
        ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_owned_contract(connection: sqlite3.Connection) -> None:
    from . import storage as s
    if owned_fingerprint(connection) != OWNED_FINGERPRINT:
        raise s._unreadable_project_state()
    for table in REBUILT_TABLES:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE", (table + "_v28",)).fetchone():
            raise s._unreadable_project_state()
        for row in connection.execute("SELECT name FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,)):
            if row["name"] not in _owned_names():
                raise s._unreadable_project_state()


def _apply_delta_locked(connection: sqlite3.Connection) -> None:
    """Only an admitted disposable/canonical predecessor writer may call this.

    Migration owns predecessor validation, FK toggling, row/object preservation,
    final validation and commit. This helper does not provide a public bypass.
    """
    from . import storage as s
    if not connection.in_transaction or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
        raise s.StorageError("internal_error", "review continuation migration requires its admitted writer")
    attachments = []
    for table in REBUILT_TABLES:
        attached = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name", (table,)).fetchall()
        if any(row["name"] not in _owned_names() for row in attached):
            raise s._unreadable_project_state()
        attachments.extend(attached)
    replacements = {"review_receipts": receipt_statement(), "completion_evidence_bundles": bundle_statement(),
        "task_completion_cycles": cycle_statement(connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='task_completion_cycles'").fetchone()[0])}
    for table in REBUILT_TABLES:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE", (table + "_v28",)).fetchone():
            raise s._unreadable_project_state()
        columns = [row["name"] for row in connection.execute(f"PRAGMA table_xinfo({s._quoted_identifier(table)})")]
        projection = ", ".join(s._quoted_identifier(column) for column in columns)
        connection.execute(f"ALTER TABLE {s._quoted_identifier(table)} RENAME TO {s._quoted_identifier(table + '_v28')}")
        connection.execute(replacements[table])
        connection.execute(f"INSERT INTO {s._quoted_identifier(table)} (rowid,{projection}) SELECT rowid,{projection} FROM {s._quoted_identifier(table + '_v28')}")
        connection.execute(f"DROP TABLE {s._quoted_identifier(table + '_v28')}")
    for statement in relation_statements():
        connection.execute(statement)
    connection.execute("INSERT INTO review_receipt_judgments (review_receipt_id,judgment_revision) SELECT review_receipt_id,1 FROM review_receipts ORDER BY rowid")
    for row in attachments:
        statement = row["sql"]
        if row["name"] == "trg_task_completion_cycles_evidence_basis_insert":
            statement = statement.replace("bundle.source_schema_version = 28", "bundle.source_schema_version = 29").replace(
                "bundle.bundle_version = 2", "((NEW.gate_basis_version = 1 AND bundle.bundle_version = 2) OR (NEW.gate_basis_version = 2 AND bundle.bundle_version = 3))")
        connection.execute(statement)


def migrate(connection: sqlite3.Connection) -> bool:
    """Setup-owned schema/row transaction; ordinary readers never invoke this."""
    from . import storage as s
    from .schema_task_event_notes import validate_storage as validate_v28
    if connection.in_transaction:
        raise s.StorageError("internal_error", "review continuation migration requires an idle connection")
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
    if version != 28:
        raise s.StorageError("migration_required", "review continuation migration requires schema version 28")
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        validate_v28(connection)
        if s.schema_objects_inconsistent_with_version(connection, 28):
            raise s._unreadable_project_state()
        new_names = {s._schema20_statement_identity(statement)[1] for statement in relation_statements()}
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name IN (SELECT value FROM json_each(?)) COLLATE NOCASE LIMIT 1", (json.dumps(sorted(new_names)),)).fetchone():
            raise s._unreadable_project_state()
        tables = tuple(row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations' ORDER BY name"))
        before = s._selected_table_projection_snapshot(connection, tables)
        columns = {name: value[0] for name, value in before.items()}
        replaced_objects = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE tbl_name IN (SELECT value FROM json_each(?)) AND sql IS NOT NULL", (json.dumps(REBUILT_TABLES),))}

        def unaffected_objects():
            return tuple(tuple(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT IN (SELECT value FROM json_each(?)) AND name NOT LIKE 'sqlite_%' ORDER BY type,name", (json.dumps(sorted(replaced_objects | new_names)),)))

        objects = unaffected_objects()
        receipt_rowids = tuple(tuple(row) for row in connection.execute("SELECT rowid,review_receipt_id FROM review_receipts ORDER BY rowid"))
        _apply_delta_locked(connection)
        validate_owned_contract(connection)
        s._schema20_integrity_checks(connection)

        def preserved():
            return (s._selected_table_projection_snapshot(connection, tables, column_basis=columns) == before
                and unaffected_objects() == objects
                and tuple(tuple(row) for row in connection.execute("SELECT rowid,review_receipt_id FROM review_receipts ORDER BY rowid")) == receipt_rowids
                and connection.execute("SELECT 1 FROM review_receipt_judgments WHERE judgment_revision!=1 OR prior_receipt_id IS NOT NULL OR continuation_id IS NOT NULL LIMIT 1").fetchone() is None)

        if not preserved():
            raise s._unreadable_project_state()
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (29,?,?)", (MIGRATION_NAME, s.utc_now()))
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


def validate_storage(connection: sqlite3.Connection, *, recovery: bool = False,
                     _privacy_success_cache=None) -> None:
    from . import storage as s
    from .schema_task_event_notes import validate_note_rows
    from .task_ownership import validate_storage_rows
    from .review_session_repository import read_bindings
    from .review_continuation_repository import validate_judgment_storage
    from .review_continuation_evidence_repository import read_resolution_links
    marker = connection.execute("SELECT name FROM schema_migrations WHERE version=29").fetchone()
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
    validate_storage_rows(connection)
    read_bindings(connection)
    validate_judgment_storage(connection)
    for row in connection.execute("SELECT project_id,task_id FROM tasks ORDER BY task_id"):
        read_resolution_links(connection, project_id=row["project_id"], task_id=row["task_id"])
    s._schema20_integrity_checks(connection)
    if rejection is not None:
        raise rejection
