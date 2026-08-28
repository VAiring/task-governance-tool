from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    json_payload,
    make_physical_install,
)
from tests.review_test_helpers import seed_review_evidence_connection
from tests.verification_receipt_test_support import (
    DEFAULT_VERIFICATION,
    FINGERPRINT_A,
    add_receipt,
    add_task,
    completion,
    initialize,
    initialize_v16_fixture,
    run_taskgov,
    seed_current_review_evidence,
    set_target,
    target_for,
)

from task_governance_tool import storage as storage_module
from task_governance_tool import tasks as tasks_module
from task_governance_tool import reviews as review_service
from task_governance_tool.reviews import read_review_evidence
from task_governance_tool.evidence_ledger import (
    EvidenceSource,
    TargetCaptureBinding,
    authority_snapshot_basis_digest as pure_authority_snapshot_basis_digest,
    build_evidence_reference,
    contract_criterion_digest as pure_contract_criterion_digest,
    verification_expectation_digest as pure_verification_expectation_digest,
)
from task_governance_tool.storage import (
    SCHEMA_VERSION,
    SQLITE_INT64_MAX,
    StorageError,
    apply_completion_evidence_bundle_migration,
    apply_evidence_ledger_capture_migration,
    apply_migrations,
    apply_verification_receipts_migration,
    authority_snapshot_basis_digest,
    capture_or_reuse_current_authority_snapshot_locked,
    connect,
    connect_initialized,
    contract_criterion_digest,
    current_schema_version,
    inspect_setup_state,
    read_verification_receipt_snapshot,
    read_review_receipt_with_provenance,
    stored_task_verification_limit,
    validate_evidence_ledger_storage,
    validate_evidence_ledger_storage_for_recovery,
    validate_selected_task_receipt_evidence,
    validate_stored_review_receipt_projection,
    verification_expectation_digest,
)
from task_governance_tool.tasks import TASK_VERIFICATION_INPUT_LIMIT


MIGRATION_TIME = "2026-08-04T01:02:03Z"
FAILURE_STAGES = (
    "after_tables",
    "after_columns",
    "after_objects",
    "after_snapshots",
    "after_marker",
    "before_commit",
)
NEW_TABLES = (
    "authority_snapshots",
    "contract_criteria",
    "authority_snapshot_criteria",
    "review_receipt_provenance",
    "review_receipt_provenance_codes",
    "artifact_manifests",
    "artifact_manifest_entries",
    "evidence_references",
)


def initialize_v17_fixture(root: Path):
    # The legacy fixture intentionally exercises pre-v18 schemas, where the
    # authority capture tables do not exist yet.
    with mock.patch(
        "task_governance_tool.tasks.capture_or_reuse_current_authority_snapshot_locked"
    ):
        target, task_id = initialize_v16_fixture(root)
    with closing(connect(target.db_path)) as connection:
        apply_verification_receipts_migration(connection)
        seed_review_evidence_connection(connection, task_id)
        connection.commit()
    return target, task_id


def initialize_v17_verification_fixture(root: Path):
    target, _done_task_id = initialize_v16_fixture(root)
    receipt_id = "tg_verification_receipt_00000000000000a1"
    with closing(connect(target.db_path)) as connection:
        apply_verification_receipts_migration(connection)
        task = connection.execute(
            "SELECT * FROM tasks WHERE status = 'ready'"
        ).fetchone()
        task_id = task["task_id"]
        connection.execute(
            """
            UPDATE tasks
               SET status = 'in_progress', verification = ?,
                   review_target_kind = 'diff_fingerprint',
                   review_target_value = ?,
                   review_target_base_revision = '',
                   review_target_generation = 1
             WHERE project_id = ? AND task_id = ?
            """,
            (
                DEFAULT_VERIFICATION,
                FINGERPRINT_A,
                task["project_id"],
                task_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO verification_receipts(
              verification_receipt_id, project_id, task_id,
              contract_revision, verification_expectation_digest,
              command_label, result, duration_ms, scope_coverage,
              target_kind, target_value, target_base_revision,
              target_generation, created_at
            ) VALUES (
              ?, ?, ?, 0, ?, 'legacy focused unittest',
              'pass', 7, 'full', 'diff_fingerprint', ?, '', 1,
              '2026-08-04T00:00:00Z'
            )
            """,
            (
                receipt_id,
                task["project_id"],
                task_id,
                verification_expectation_digest(DEFAULT_VERIFICATION),
                FINGERPRINT_A,
            ),
        )
        connection.commit()
    return target, task_id, receipt_id


def table_projection(connection: sqlite3.Connection, table_name: str):
    columns = tuple(
        str(row["name"])
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    )
    rows = tuple(
        tuple(row)
        for row in connection.execute(
            f'SELECT * FROM "{table_name}" ORDER BY rowid'
        ).fetchall()
    )
    return columns, rows


def database_projection(connection: sqlite3.Connection):
    schema = tuple(
        tuple(row)
        for row in connection.execute(
            """
            SELECT type, name, tbl_name, sql
              FROM sqlite_master
             WHERE name NOT LIKE 'sqlite_%'
             ORDER BY type, name
            """
        ).fetchall()
    )
    tables = tuple(
        str(row["name"])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
             WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
             ORDER BY name
            """
        ).fetchall()
    )
    return schema, tuple(
        (table_name, table_projection(connection, table_name))
        for table_name in tables
    )


def rewrite_table_schema_sql(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    old: str,
    new: str,
) -> None:
    table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if table_row is None or table_row[0] is None:
        raise AssertionError(f"table schema was not found: {table_name}")
    table_sql = str(table_row[0])
    if table_sql.count(old) != 1:
        raise AssertionError(
            f"table schema replacement was not unique: {table_name}"
        )
    altered_sql = table_sql.replace(old, new, 1)
    try:
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_master SET sql = ? "
            "WHERE type = 'table' AND name = ?",
            (altered_sql, table_name),
        )
    finally:
        connection.execute("PRAGMA writable_schema = OFF")
    schema_cookie = int(
        connection.execute("PRAGMA schema_version").fetchone()[0]
    )
    connection.execute(f"PRAGMA schema_version = {schema_cookie + 1}")
    connection.commit()


def recomputed_snapshot_basis_digest(
    connection: sqlite3.Connection,
    snapshot_id: str,
    *,
    legacy_coercion: bool = False,
) -> str:
    row = connection.execute(
        "SELECT * FROM authority_snapshots WHERE authority_snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    links = {
        link["criterion_kind"]: link["criterion_id"]
        for link in connection.execute(
            """
            SELECT criterion_kind, criterion_id
              FROM authority_snapshot_criteria
             WHERE authority_snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchall()
    }
    values = {
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "task_title": row["task_title"],
        "task_description": row["task_description"],
        "review_tier": row["review_tier"],
        "verification": row["verification"],
        "verification_digest": row["verification_digest"],
        "contract_revision": row["contract_revision"],
        "contract_state": row["contract_state"],
        "contract_scope": row["contract_scope"],
        "contract_acceptance": row["contract_acceptance"],
        "contract_constraints": row["contract_constraints"],
        "contract_authority_ref": row["contract_authority_ref"],
        "acceptance_criterion_id": links.get("acceptance"),
        "verification_criterion_id": links.get("verification"),
        "producer_class": row["producer_class"],
        "producer_version": row["producer_version"],
    }
    if legacy_coercion:
        for field in (
            "project_id",
            "task_id",
            "task_title",
            "task_description",
            "verification_digest",
            "contract_state",
            "contract_scope",
            "contract_acceptance",
            "contract_constraints",
            "contract_authority_ref",
            "producer_class",
        ):
            values[field] = str(values[field])
        for field in ("review_tier", "contract_revision", "producer_version"):
            values[field] = int(values[field])
    return authority_snapshot_basis_digest(values)


def rewrite_review_reference_digest(
    connection: sqlite3.Connection,
    *,
    source_kind: str,
    source_id: str,
    review_provenance: dict | None = None,
) -> None:
    reference = connection.execute(
        "SELECT * FROM evidence_references "
        "WHERE source_kind = ? AND source_id = ?",
        (source_kind, source_id),
    ).fetchone()
    if reference is None:
        raise AssertionError("native Review source Reference was not found")
    if source_kind == "review_receipt":
        row = connection.execute(
            "SELECT * FROM review_receipts WHERE review_receipt_id = ?",
            (source_id,),
        ).fetchone()
        source_projection = {
            "review_receipt_id": source_id,
            "reviewer_key": row["reviewer_key"],
            "receipt_kind": row["receipt_kind"],
            "verdict": row["verdict"],
            "summary": row["summary"],
            "user_approved": row["user_approved"],
            "created_at": row["created_at"],
            "review_provenance": review_provenance,
        }
    elif source_kind == "review_finding":
        row = connection.execute(
            "SELECT * FROM review_findings WHERE review_finding_id = ?",
            (source_id,),
        ).fetchone()
        source_projection = {
            "review_finding_id": source_id,
            "review_receipt_id": row["review_receipt_id"],
            "severity": row["severity"],
            "summary": row["summary"],
            "created_at": row["created_at"],
        }
    else:
        raise AssertionError(f"unsupported Review source kind: {source_kind}")
    source = EvidenceSource(
        source_kind=source_kind,
        source_state="recorded",
        source_id=source_id,
        source_projection=source_projection,
    )
    binding = TargetCaptureBinding(
        target_kind=reference["target_kind"],
        target_value=reference["target_value"],
        target_base_revision=reference["target_base_revision"],
        target_generation=reference["target_generation"],
        authority_snapshot_id=reference["authority_snapshot_id"],
        acceptance_criterion_id=reference["acceptance_criterion_id"],
        verification_criterion_id=reference["verification_criterion_id"],
    )
    rebuilt = build_evidence_reference(
        source=source,
        project_id=reference["project_id"],
        task_id=reference["task_id"],
        contract_revision=reference["contract_revision"],
        binding=binding,
    )
    trigger_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'trg_evidence_references_no_update'"
    ).fetchone()[0]
    connection.execute("DROP TRIGGER trg_evidence_references_no_update")
    changed = connection.execute(
        "UPDATE evidence_references SET digest = ? "
        "WHERE source_kind = ? AND source_id = ?",
        (rebuilt.digest, source_kind, source_id),
    )
    if changed.rowcount != 1:
        raise AssertionError("native Review source Reference was not updated")
    connection.execute(trigger_sql)


def add_contract_task(db: Path, repo: Path, *, scope: str, acceptance: str) -> dict:
    result = run_taskgov(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        "Contract evidence task",
        "--status",
        "in_progress",
        "--review-tier",
        "0",
        "--contract-scope",
        scope,
        "--contract-acceptance",
        acceptance,
        "--contract-authority-ref",
        "plan.md:TG-M22.2",
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json_payload(result)["data"]["task"]


def seed_v17_contract_constraints(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    constraints: str,
) -> None:
    connection.execute(
        """
        INSERT INTO task_contract_revisions(
          contract_revision_id, task_id, project_id, revision,
          scope, acceptance, constraints_text, authority_ref,
          change_reason, created_at
        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
        """,
        (
            "tg_contract_0000000000000001",
            task_id,
            project_id,
            "Legacy migration scope",
            "Legacy migration acceptance",
            constraints,
            "",
            "",
            "2026-08-04T00:00:00Z",
        ),
    )
    connection.execute(
        "UPDATE tasks SET current_contract_revision = 1 WHERE task_id = ?",
        (task_id,),
    )
    connection.commit()


def tamper_historical_authority_text(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    snapshot_field: str,
    forbidden_value: str,
) -> None:
    snapshot_columns = {
        "title": "task_title",
        "description": "task_description",
        "verification": "verification",
        "contract_scope": "contract_scope",
        "contract_acceptance": "contract_acceptance",
        "contract_constraints": "contract_constraints",
        "contract_authority_ref": "contract_authority_ref",
    }
    contract_columns = {
        "contract_scope": "scope",
        "contract_acceptance": "acceptance",
        "contract_constraints": "constraints_text",
        "contract_authority_ref": "authority_ref",
    }
    criterion_kinds = {
        "verification": "verification",
        "contract_acceptance": "acceptance",
    }
    snapshot_column = snapshot_columns[snapshot_field]
    criterion_kind = criterion_kinds.get(snapshot_field)
    affected_snapshot_ids = {snapshot_id}
    criterion_id = None
    if criterion_kind is not None:
        criterion_row = connection.execute(
            """
            SELECT criterion_id
              FROM authority_snapshot_criteria
             WHERE authority_snapshot_id = ? AND criterion_kind = ?
            """,
            (snapshot_id, criterion_kind),
        ).fetchone()
        if criterion_row is None:
            raise AssertionError("historical snapshot criterion is missing")
        criterion_id = str(criterion_row["criterion_id"])
        affected_snapshot_ids = {
            str(row["authority_snapshot_id"])
            for row in connection.execute(
                """
                SELECT authority_snapshot_id
                  FROM authority_snapshot_criteria
                 WHERE criterion_id = ?
                """,
                (criterion_id,),
            ).fetchall()
        }

    contract_keys: set[tuple[str, str, int]] = set()
    if snapshot_field in contract_columns:
        for affected_snapshot_id in affected_snapshot_ids:
            snapshot = connection.execute(
                """
                SELECT project_id, task_id, contract_revision
                  FROM authority_snapshots
                 WHERE authority_snapshot_id = ?
                """,
                (affected_snapshot_id,),
            ).fetchone()
            if snapshot is None or int(snapshot["contract_revision"]) <= 0:
                raise AssertionError("historical Contract revision is missing")
            contract_keys.add(
                (
                    str(snapshot["project_id"]),
                    str(snapshot["task_id"]),
                    int(snapshot["contract_revision"]),
                )
            )

    snapshot_trigger_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'trg_authority_snapshots_no_update'"
    ).fetchone()
    criterion_trigger_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'trg_contract_criteria_no_update'"
    ).fetchone()
    receipt_trigger_row = (
        connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'trg_verification_receipts_no_update'"
        ).fetchone()
        if snapshot_field == "verification"
        else None
    )
    if (
        snapshot_trigger_row is None
        or criterion_trigger_row is None
        or snapshot_field == "verification" and receipt_trigger_row is None
    ):
        raise AssertionError("evidence-ledger immutability trigger is missing")
    snapshot_trigger = str(snapshot_trigger_row["sql"])
    criterion_trigger = str(criterion_trigger_row["sql"])
    receipt_trigger = (
        str(receipt_trigger_row["sql"])
        if receipt_trigger_row is not None
        else None
    )

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DROP TRIGGER trg_authority_snapshots_no_update")
        if criterion_id is not None:
            connection.execute("DROP TRIGGER trg_contract_criteria_no_update")
        if receipt_trigger is not None:
            connection.execute("DROP TRIGGER trg_verification_receipts_no_update")
        for affected_snapshot_id in sorted(affected_snapshot_ids):
            if snapshot_field == "verification":
                connection.execute(
                    "UPDATE authority_snapshots "
                    "SET verification = ?, verification_digest = ? "
                    "WHERE authority_snapshot_id = ?",
                    (
                        forbidden_value,
                        pure_verification_expectation_digest(forbidden_value),
                        affected_snapshot_id,
                    ),
                )
            else:
                connection.execute(
                    f"UPDATE authority_snapshots SET {snapshot_column} = ? "
                    "WHERE authority_snapshot_id = ?",
                    (forbidden_value, affected_snapshot_id),
                )
        if criterion_id is not None:
            connection.execute(
                "UPDATE contract_criteria SET criterion_text = ?, digest = ? "
                "WHERE criterion_id = ?",
                (
                    forbidden_value,
                    pure_contract_criterion_digest(
                        criterion_kind,
                        forbidden_value,
                    ),
                    criterion_id,
                ),
            )
        if snapshot_field == "verification":
            connection.execute(
                """
                UPDATE verification_receipts
                   SET verification_expectation_digest = ?
                 WHERE verification_subject_basis_version = 1
                   AND subject_verification_criterion_id = ?
                """,
                (
                    pure_verification_expectation_digest(forbidden_value),
                    criterion_id,
                ),
            )
        contract_column = contract_columns.get(snapshot_field)
        if contract_column is not None:
            for project_id, task_id, revision in sorted(contract_keys):
                connection.execute(
                    f"UPDATE task_contract_revisions SET {contract_column} = ? "
                    "WHERE project_id = ? AND task_id = ? AND revision = ?",
                    (forbidden_value, project_id, task_id, revision),
                )
        for affected_snapshot_id in sorted(affected_snapshot_ids):
            connection.execute(
                "UPDATE authority_snapshots SET basis_digest = ? "
                "WHERE authority_snapshot_id = ?",
                (
                    recomputed_snapshot_basis_digest(
                        connection,
                        affected_snapshot_id,
                    ),
                    affected_snapshot_id,
                ),
            )
        if criterion_id is not None:
            connection.execute(criterion_trigger)
        if receipt_trigger is not None:
            connection.execute(receipt_trigger)
        connection.execute(snapshot_trigger)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def seed_historical_authority_receipt(root: Path):
    repo, db = initialize(root)
    added = run_taskgov(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        "Historical authority title",
        "--description",
        "Historical authority description",
        "--status",
        "in_progress",
        "--review-tier",
        "0",
        "--verification",
        "Historical verification expectation",
        "--contract-scope",
        "Historical scope",
        "--contract-acceptance",
        "Historical acceptance",
        "--contract-constraints",
        "Historical constraints",
        "--contract-authority-ref",
        "plan.md:TG-M22.2",
        "--json",
    )
    if added.returncode != 0:
        raise AssertionError(added.stderr or added.stdout)
    task = json_payload(added)["data"]["task"]
    generation = set_target(db, repo, task["task_id"])
    recorded = add_receipt(db, repo, task["task_id"], generation)
    if recorded.returncode != 0:
        raise AssertionError(recorded.stderr or recorded.stdout)

    revised = run_taskgov(
        "task",
        "edit",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task["task_id"],
        "--contract-scope",
        "Current scope",
        "--contract-acceptance",
        "Current acceptance",
        "--contract-constraints",
        "Current constraints",
        "--contract-authority-ref",
        "docs/specification.md:1245",
        "--contract-change-reason",
        "Advance the historical authority fixture",
        "--json",
    )
    if revised.returncode != 0:
        raise AssertionError(revised.stderr or revised.stdout)
    advanced = run_taskgov(
        "task",
        "edit",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task["task_id"],
        "--title",
        "Current authority title",
        "--description",
        "Current authority description",
        "--verification",
        "Current verification expectation",
        "--json",
    )
    if advanced.returncode != 0:
        raise AssertionError(advanced.stderr or advanced.stdout)

    with closing(connect(db)) as connection:
        receipt = dict(
            connection.execute(
                "SELECT * FROM verification_receipts WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()
        )
        snapshots = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM authority_snapshots
                 WHERE task_id = ?
                 ORDER BY generation
                """,
                (task["task_id"],),
            ).fetchall()
        ]
        current_snapshot_id = connection.execute(
            "SELECT current_authority_snapshot_id FROM tasks WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()[0]
        historical_snapshot = next(
            row
            for row in snapshots
            if row["authority_snapshot_id"]
            == receipt["subject_authority_snapshot_id"]
        )
        current_snapshot = next(
            row
            for row in snapshots
            if row["authority_snapshot_id"] == current_snapshot_id
        )
        current_links = {
            str(row["criterion_kind"]): str(row["criterion_id"])
            for row in connection.execute(
                """
                SELECT criterion_kind, criterion_id
                  FROM authority_snapshot_criteria
                 WHERE authority_snapshot_id = ?
                """,
                (current_snapshot_id,),
            ).fetchall()
        }
        historical_acceptance_id = str(
            connection.execute(
                """
                SELECT criterion_id
                  FROM authority_snapshot_criteria
                 WHERE authority_snapshot_id = ?
                   AND criterion_kind = 'acceptance'
                """,
                (receipt["subject_authority_snapshot_id"],),
            ).fetchone()[0]
        )
    if [row["generation"] for row in snapshots] != [1, 2, 3]:
        raise AssertionError("historical fixture did not create three generations")
    if historical_snapshot["generation"] != 1 or current_snapshot["generation"] != 3:
        raise AssertionError("verification receipt did not retain generation one")
    for column in (
        "task_title",
        "task_description",
        "verification",
        "contract_scope",
        "contract_acceptance",
        "contract_constraints",
        "contract_authority_ref",
    ):
        if historical_snapshot[column] == current_snapshot[column]:
            raise AssertionError(f"historical {column} did not advance")
    if (
        receipt["subject_verification_criterion_id"]
        == current_links.get("verification")
    ):
        raise AssertionError("historical verification criterion did not advance")
    if historical_acceptance_id == current_links.get("acceptance"):
        raise AssertionError("historical acceptance criterion did not advance")
    return db, task, receipt


class EvidenceLedgerStorageTests(unittest.TestCase):
    def _assert_selected_task_authority_operations_fail(
        self,
        *,
        db: Path,
        repo: Path,
        task_id: str,
        before: bytes,
        redacted_values: tuple[str, ...] = (),
    ) -> None:
        operations = (
            run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--read-only",
                "--json",
            ),
            run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--priority",
                "urgent",
                "--json",
            ),
        )
        for operation in operations:
            self.assertEqual(operation.returncode, 2, operation.stdout)
            self.assertEqual(
                json_payload(operation)["errors"][0],
                {
                    "code": "project_state_unreadable",
                    "message": "project state could not be read safely",
                },
            )
            for value in redacted_values:
                self.assertNotIn(value, operation.stdout)
            self.assertEqual(db.read_bytes(), before)

    def _assert_verification_inventory_operations_fail(
        self,
        *,
        db: Path,
        repo: Path,
        task_id: str,
        before: bytes,
        redacted_values: tuple[str, ...] = (),
        expected_error: dict[str, str] | None = None,
    ) -> None:
        expected = expected_error or {
            "code": "invalid_verification_evidence",
            "message": "stored verification evidence is inconsistent",
        }
        operations = (
            run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--read-only",
                "--json",
            ),
            completion(db, repo, task_id, check=True),
            completion(db, repo, task_id),
        )
        for operation in operations:
            self.assertNotEqual(operation.returncode, 0, operation.stdout)
            self.assertEqual(
                json_payload(operation)["errors"][0],
                expected,
            )
            for value in redacted_values:
                self.assertNotIn(value, operation.stdout)
            self.assertEqual(db.read_bytes(), before)

    def _assert_direct_verification_inventory_fails(
        self,
        *,
        connection: sqlite3.Connection,
        db: Path,
        project_id: str,
        task_id: str,
        redacted_values: tuple[str, ...] = (),
    ) -> bytes:
        task = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        before = db.read_bytes()
        with self.assertRaises(StorageError) as direct_failure:
            read_verification_receipt_snapshot(
                connection,
                project_id=project_id,
                task_id=task_id,
                contract_revision=task["current_contract_revision"],
                verification_expectation_digest=(
                    verification_expectation_digest(task["verification"])
                ),
                target_kind=task["review_target_kind"],
                target_value=task["review_target_value"],
                target_base_revision=task["review_target_base_revision"],
                target_generation=task["review_target_generation"],
            )
        self.assertEqual(
            (
                direct_failure.exception.code,
                direct_failure.exception.message,
            ),
            (
                "invalid_verification_evidence",
                "stored verification evidence is inconsistent",
            ),
        )
        for value in redacted_values:
            self.assertNotIn(value, str(direct_failure.exception))
        self.assertEqual(db.read_bytes(), before)
        return before

    def test_storage_digest_helpers_match_the_pure_canonical_owner(self):
        verification = "  python -m unittest\n"
        values = {
            "project_id": "tg_project_0123456789abcdef0123456789abcdef",
            "task_id": "tg_task_0123456789abcdef",
            "task_title": "Exact task title",
            "task_description": "Exact task description",
            "review_tier": 2,
            "verification": verification,
            "verification_digest": pure_verification_expectation_digest(
                verification
            ),
            "contract_revision": 3,
            "contract_state": "contract_specified",
            "contract_scope": "scope",
            "contract_acceptance": "acceptance",
            "contract_constraints": "constraints",
            "contract_authority_ref": "plan.md:TG-M22.2",
            "acceptance_criterion_id": (
                "tg_contract_criterion_1111111111111111"
            ),
            "verification_criterion_id": (
                "tg_contract_criterion_2222222222222222"
            ),
            "producer_class": "taskgov_core",
            "producer_version": 1,
        }

        self.assertEqual(
            storage_module.verification_expectation_digest(verification),
            pure_verification_expectation_digest(verification),
        )
        for kind, text in (
            ("acceptance", "acceptance"),
            ("verification", verification),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(
                    contract_criterion_digest(kind, text),
                    pure_contract_criterion_digest(kind, text),
                )
        self.assertEqual(
            authority_snapshot_basis_digest(values),
            pure_authority_snapshot_basis_digest(values),
        )

    def test_schema_version_and_verification_capacity_are_layered(self):
        self.assertEqual(SCHEMA_VERSION, 21)
        self.assertEqual(stored_task_verification_limit(17), 500)
        self.assertEqual(stored_task_verification_limit(18), 1_000)
        self.assertEqual(stored_task_verification_limit(19), 1_000)
        self.assertEqual(stored_task_verification_limit(20), 1_000)
        self.assertEqual(stored_task_verification_limit(21), 1_000)
        self.assertEqual(TASK_VERIFICATION_INPUT_LIMIT, 1_000)

    def test_migration_rejects_v17_overflow_and_v18_captures_one_thousand(self):
        with tempfile.TemporaryDirectory() as temp:
            invalid_target, invalid_task_id = initialize_v17_fixture(
                Path(temp) / "invalid-v17"
            )
            with closing(connect(invalid_target.db_path)) as connection:
                connection.execute(
                    "UPDATE tasks SET verification = ? WHERE task_id = ?",
                    ("x" * 501, invalid_task_id),
                )
                connection.commit()
                with self.assertRaises(StorageError) as failure:
                    apply_evidence_ledger_capture_migration(connection)
                self.assertEqual(failure.exception.code, "project_state_unreadable")
                self.assertEqual(current_schema_version(connection), 17)
                for table_name in NEW_TABLES:
                    self.assertIsNone(
                        connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE name = ?",
                            (table_name,),
                        ).fetchone()
                    )

            valid_target, valid_task_id = initialize_v17_fixture(
                Path(temp) / "valid-v18"
            )
            with closing(connect(valid_target.db_path)) as connection:
                apply_evidence_ledger_capture_migration(connection)
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE tasks SET verification = ? WHERE task_id = ?",
                    ("界" * 1_000, valid_task_id),
                )
                binding = capture_or_reuse_current_authority_snapshot_locked(
                    connection,
                    project_id=valid_target.project.project_id,
                    task_id=valid_task_id,
                    created_at="2026-08-04T02:03:00Z",
                )
                connection.commit()
                self.assertIsNotNone(binding.verification_criterion_id)
                self.assertEqual(
                    connection.execute(
                        "SELECT length(verification) FROM authority_snapshots "
                        "WHERE authority_snapshot_id = ?",
                        (binding.authority_snapshot_id,),
                    ).fetchone()[0],
                    1_000,
                )
                validate_evidence_ledger_storage(connection)

    def test_v17_to_v18_preserves_old_columns_and_creates_only_current_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            target, _ = initialize_v17_fixture(Path(temp))
            with closing(connect(target.db_path)) as connection:
                preserved_tables = (
                    "tasks",
                    "review_receipts",
                    "task_completion_cycles",
                    "verification_receipts",
                )
                before = {
                    table_name: table_projection(connection, table_name)
                    for table_name in preserved_tables
                }
                task_count = len(before["tasks"][1])

                with mock.patch(
                    "task_governance_tool.storage.utc_now",
                    return_value=MIGRATION_TIME,
                ):
                    apply_evidence_ledger_capture_migration(connection)

                self.assertEqual(current_schema_version(connection), 18)
                self.assertEqual(
                    connection.execute(
                        "SELECT name FROM schema_migrations WHERE version = 18"
                    ).fetchone()[0],
                    "evidence_ledger_capture",
                )
                for table_name, (old_columns, old_rows) in before.items():
                    projection = ", ".join(f'"{name}"' for name in old_columns)
                    actual = tuple(
                        tuple(row)
                        for row in connection.execute(
                            f'SELECT {projection} FROM "{table_name}" ORDER BY rowid'
                        ).fetchall()
                    )
                    self.assertEqual(actual, old_rows, table_name)

                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM authority_snapshots"
                    ).fetchone()[0],
                    task_count,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM authority_snapshots
                         WHERE generation = 1
                           AND producer_class = 'legacy_migration'
                           AND producer_version = 1
                           AND created_at = ?
                        """,
                        (MIGRATION_TIME,),
                    ).fetchone()[0],
                    task_count,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM tasks
                         WHERE current_authority_snapshot_id IS NULL
                            OR current_authority_snapshot_generation != 1
                        """
                    ).fetchone()[0],
                    0,
                )
                for table_name in (
                    "review_receipt_provenance",
                    "review_receipt_provenance_codes",
                    "artifact_manifests",
                    "artifact_manifest_entries",
                    "evidence_references",
                ):
                    self.assertEqual(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0],
                        0,
                        table_name,
                    )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM tasks
                         WHERE review_target_capture_version != 0
                            OR review_target_authority_snapshot_id IS NOT NULL
                            OR review_target_acceptance_criterion_id IS NOT NULL
                            OR review_target_verification_criterion_id IS NOT NULL
                            OR review_target_artifact_manifest_id IS NOT NULL
                        """
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM review_receipts
                         WHERE review_provenance_basis_version != 0
                            OR review_provenance_id IS NOT NULL
                        """
                    ).fetchone()[0],
                    0,
                )
                for table_name in (
                    "verification_receipts",
                    "task_completion_cycles",
                ):
                    self.assertEqual(
                        connection.execute(
                            f"""
                            SELECT COUNT(*) FROM {table_name}
                             WHERE verification_subject_basis_version != 0
                                OR subject_authority_snapshot_id IS NOT NULL
                                OR subject_verification_criterion_id IS NOT NULL
                            """
                        ).fetchone()[0],
                        0,
                        table_name,
                    )
                validate_evidence_ledger_storage(connection)

                after = database_projection(connection)
                changes_before = connection.total_changes
                apply_evidence_ledger_capture_migration(connection)
                self.assertEqual(connection.total_changes, changes_before)
                self.assertEqual(database_projection(connection), after)

    def test_v17_legacy_constraints_migrate_and_reenter_exactly(self):
        cases = (
            ("equality", "dispatch_authorization=7"),
            ("json", '{"dispatch_authorization":7}'),
        )
        with tempfile.TemporaryDirectory() as temp:
            for case, constraints in cases:
                with self.subTest(case=case):
                    target, task_id = initialize_v17_fixture(Path(temp) / case)
                    with closing(connect(target.db_path)) as connection:
                        seed_v17_contract_constraints(
                            connection,
                            project_id=target.project.project_id,
                            task_id=task_id,
                            constraints=constraints,
                        )

                        apply_evidence_ledger_capture_migration(connection)

                        self.assertEqual(current_schema_version(connection), 18)
                        self.assertEqual(
                            connection.execute(
                                "SELECT constraints_text "
                                "FROM task_contract_revisions "
                                "WHERE project_id = ? AND task_id = ? "
                                "AND revision = 1",
                                (target.project.project_id, task_id),
                            ).fetchone()[0],
                            constraints,
                        )
                        snapshot = connection.execute(
                            """
                            SELECT contract_constraints, producer_class
                              FROM authority_snapshots
                             WHERE project_id = ? AND task_id = ?
                            """,
                            (target.project.project_id, task_id),
                        ).fetchone()
                        self.assertEqual(snapshot["contract_constraints"], constraints)
                        self.assertEqual(snapshot["producer_class"], "legacy_migration")
                        validate_evidence_ledger_storage(connection)

                        applied, warnings = apply_migrations(connection)
                        self.assertEqual(applied, [19, 20, 21])
                        self.assertEqual(warnings, [])
                        self.assertEqual(current_schema_version(connection), 21)
                        self.assertEqual(
                            connection.execute(
                                "SELECT constraints_text "
                                "FROM task_contract_revisions "
                                "WHERE project_id = ? AND task_id = ? "
                                "AND revision = 1",
                                (target.project.project_id, task_id),
                            ).fetchone()[0],
                            constraints,
                        )
                        snapshot = connection.execute(
                            "SELECT contract_constraints, producer_class "
                            "FROM authority_snapshots "
                            "WHERE project_id = ? AND task_id = ?",
                            (target.project.project_id, task_id),
                        ).fetchone()
                        self.assertEqual(
                            tuple(snapshot),
                            (constraints, "legacy_migration"),
                        )

                        before_reentry = database_projection(connection)
                        changes_before_reentry = connection.total_changes
                        reapplied, reentry_warnings = apply_migrations(connection)
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(reapplied, [])
                        self.assertEqual(reentry_warnings, [])
                        self.assertEqual(
                            connection.total_changes,
                            changes_before_reentry,
                        )
                        self.assertEqual(
                            database_projection(connection),
                            before_reentry,
                        )

    def test_legacy_constraints_reject_compound_and_noncanonical_no_write(self):
        cases = (
            (
                "compound-token",
                "dispatch_authorization=7 token=opaque-value",
            ),
            ("noncanonical-equality", "dispatch_authorization=07"),
            ("noncanonical-json", '{"dispatch_authorization":"7"}'),
        )
        with tempfile.TemporaryDirectory() as temp:
            for case, rejected_constraints in cases:
                with self.subTest(case=case, stage="migration"):
                    target, task_id = initialize_v17_fixture(Path(temp) / case)
                    with closing(connect(target.db_path)) as connection:
                        seed_v17_contract_constraints(
                            connection,
                            project_id=target.project.project_id,
                            task_id=task_id,
                            constraints=rejected_constraints,
                        )
                        before_migration = database_projection(connection)
                        changes_before_migration = connection.total_changes

                        with self.assertRaises(StorageError) as failure:
                            apply_evidence_ledger_capture_migration(connection)

                        self.assertEqual(
                            failure.exception.code,
                            "project_state_unreadable",
                        )
                        self.assertNotIn(
                            rejected_constraints,
                            str(failure.exception),
                        )
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(current_schema_version(connection), 17)
                        self.assertEqual(
                            connection.total_changes,
                            changes_before_migration,
                        )
                        self.assertEqual(
                            database_projection(connection),
                            before_migration,
                        )

                        connection.execute(
                            "UPDATE task_contract_revisions "
                            "SET constraints_text = 'dispatch_authorization=7' "
                            "WHERE project_id = ? AND task_id = ? "
                            "AND revision = 1",
                            (target.project.project_id, task_id),
                        )
                        connection.commit()
                        apply_evidence_ledger_capture_migration(connection)
                        snapshot_id = connection.execute(
                            "SELECT current_authority_snapshot_id FROM tasks "
                            "WHERE project_id = ? AND task_id = ?",
                            (target.project.project_id, task_id),
                        ).fetchone()[0]
                        tamper_historical_authority_text(
                            connection,
                            snapshot_id=snapshot_id,
                            snapshot_field="contract_constraints",
                            forbidden_value=rejected_constraints,
                        )
                        before_reentry = database_projection(connection)
                        changes_before_reentry = connection.total_changes

                        with self.subTest(case=case, stage="recovery"):
                            with self.assertRaises(StorageError) as failure:
                                validate_evidence_ledger_storage_for_recovery(
                                    connection
                                )
                            self.assertEqual(
                                failure.exception.code,
                                "evidence_ledger_inconsistent",
                            )
                            self.assertNotIn(
                                rejected_constraints,
                                str(failure.exception),
                            )
                            self.assertFalse(connection.in_transaction)
                            self.assertEqual(
                                connection.total_changes,
                                changes_before_reentry,
                            )
                            self.assertEqual(
                                database_projection(connection),
                                before_reentry,
                            )

                        with self.subTest(case=case, stage="reentry"):
                            with self.assertRaises(StorageError) as failure:
                                apply_migrations(connection)
                            self.assertEqual(
                                failure.exception.code,
                                "project_state_unreadable",
                            )
                            self.assertNotIn(
                                rejected_constraints,
                                str(failure.exception),
                            )
                            self.assertFalse(connection.in_transaction)
                            self.assertEqual(
                                connection.total_changes,
                                changes_before_reentry,
                            )
                            self.assertEqual(
                                database_projection(connection),
                                before_reentry,
                            )

    def test_v18_migration_rolls_back_each_injected_stage_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            for stage in FAILURE_STAGES:
                with self.subTest(stage=stage):
                    target, _ = initialize_v17_fixture(Path(temp) / stage)
                    with closing(connect(target.db_path)) as connection:
                        before = database_projection(connection)
                        with self.assertRaises(StorageError) as failure:
                            apply_evidence_ledger_capture_migration(
                                connection,
                                fail_stage=stage,
                            )
                        self.assertEqual(failure.exception.code, "internal_error")
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(current_schema_version(connection), 17)
                        self.assertEqual(database_projection(connection), before)
                        for table_name in NEW_TABLES:
                            self.assertIsNone(
                                connection.execute(
                                    "SELECT 1 FROM sqlite_master WHERE name = ?",
                                    (table_name,),
                                ).fetchone()
                            )

                        apply_evidence_ledger_capture_migration(connection)
                        self.assertEqual(current_schema_version(connection), 18)
                        validate_evidence_ledger_storage(connection)

    def test_reentry_rejects_structural_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            for trigger in (
                "trg_review_receipts_provenance_basis_insert",
                "trg_evidence_references_no_update",
            ):
                with self.subTest(trigger=trigger):
                    target, _ = initialize_v17_fixture(Path(temp) / trigger)
                    with closing(connect(target.db_path)) as connection:
                        apply_evidence_ledger_capture_migration(connection)
                        connection.execute(f"DROP TRIGGER {trigger}")
                        connection.commit()
                        before = database_projection(connection)
                        changes_before = connection.total_changes

                        with self.assertRaises(StorageError) as failure:
                            apply_evidence_ledger_capture_migration(connection)
                        self.assertEqual(
                            failure.exception.code,
                            "project_state_unreadable",
                        )
                        self.assertEqual(connection.total_changes, changes_before)
                        self.assertEqual(database_projection(connection), before)

    def test_reentry_rejects_same_name_wrong_v18_index_definition(self):
        with tempfile.TemporaryDirectory() as temp:
            target, _ = initialize_v17_fixture(Path(temp))
            with closing(connect(target.db_path)) as connection:
                apply_evidence_ledger_capture_migration(connection)
                connection.execute("DROP INDEX idx_evidence_references_source")
                connection.execute(
                    "CREATE INDEX idx_evidence_references_source "
                    "ON evidence_references(source_id)"
                )
                connection.commit()
                before = database_projection(connection)
                changes_before = connection.total_changes

                with self.assertRaises(StorageError) as failure:
                    apply_evidence_ledger_capture_migration(connection)

                self.assertEqual(
                    (failure.exception.code, failure.exception.message),
                    (
                        "project_state_unreadable",
                        "project state could not be read safely",
                    ),
                )
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.total_changes, changes_before)
                self.assertEqual(database_projection(connection), before)

    def test_reentry_rejects_altered_v18_added_column_definitions(self):
        cases = (
            (
                "declared-type",
                "tasks",
                "current_authority_snapshot_generation INTEGER",
                "current_authority_snapshot_generation NUMERIC",
            ),
            (
                "default",
                "tasks",
                "review_target_capture_version INTEGER NOT NULL DEFAULT 0",
                "review_target_capture_version INTEGER NOT NULL DEFAULT 1",
            ),
            (
                "check",
                "review_receipts",
                "CHECK (review_provenance_basis_version IN (0, 1))",
                "CHECK (review_provenance_basis_version IN (0, 1, 2))",
            ),
            (
                "foreign-key",
                "review_receipts",
                "REFERENCES review_receipt_provenance(review_provenance_id)",
                "REFERENCES authority_snapshots(authority_snapshot_id)",
            ),
            (
                "extra-table-constraint",
                "tasks",
                "REFERENCES artifact_manifests(artifact_manifest_id)",
                "REFERENCES artifact_manifests(artifact_manifest_id), "
                "CHECK (current_authority_snapshot_generation <= 1)",
            ),
            (
                "unexpected-column-before-v18",
                "tasks",
                "current_authority_snapshot_id TEXT",
                "capture_guard INTEGER, current_authority_snapshot_id TEXT",
            ),
            (
                "generated-column-before-v18",
                "tasks",
                "current_authority_snapshot_id TEXT",
                "capture_guard INTEGER GENERATED ALWAYS AS (0) VIRTUAL, "
                "current_authority_snapshot_id TEXT",
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            for case, table_name, old, new in cases:
                with self.subTest(case=case):
                    target, _ = initialize_v17_fixture(Path(temp) / case)
                    with closing(connect(target.db_path)) as connection:
                        apply_evidence_ledger_capture_migration(connection)
                    with closing(sqlite3.connect(target.db_path)) as connection:
                        rewrite_table_schema_sql(
                            connection,
                            table_name=table_name,
                            old=old,
                            new=new,
                        )
                    with closing(connect(target.db_path)) as connection:
                        before = database_projection(connection)
                        changes_before = connection.total_changes

                        with self.assertRaises(StorageError) as failure:
                            apply_evidence_ledger_capture_migration(connection)

                        self.assertEqual(
                            (failure.exception.code, failure.exception.message),
                            (
                                "project_state_unreadable",
                                "project state could not be read safely",
                            ),
                        )
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(
                            connection.total_changes,
                            changes_before,
                        )
                        self.assertEqual(
                            database_projection(connection),
                            before,
                        )

    def test_authority_capture_reuses_exact_basis_and_advances_changed_basis(self):
        with tempfile.TemporaryDirectory() as temp:
            target, task_id = initialize_v17_fixture(Path(temp))
            with closing(connect(target.db_path)) as connection:
                apply_evidence_ledger_capture_migration(connection)
                project_id = target.project.project_id
                connection.execute("BEGIN IMMEDIATE")
                first = capture_or_reuse_current_authority_snapshot_locked(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    created_at="2026-08-04T02:00:00Z",
                )
                second = capture_or_reuse_current_authority_snapshot_locked(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    created_at="2026-08-04T02:01:00Z",
                )
                self.assertEqual(first, second)
                self.assertEqual(first.generation, 2)
                original_title = connection.execute(
                    "SELECT title FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()[0]

                connection.execute(
                    "UPDATE tasks SET title = ? WHERE project_id = ? AND task_id = ?",
                    ("Changed title", project_id, task_id),
                )
                third = capture_or_reuse_current_authority_snapshot_locked(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    created_at="2026-08-04T02:02:00Z",
                )
                self.assertEqual(third.generation, 3)
                self.assertNotEqual(third.authority_snapshot_id, first.authority_snapshot_id)

                connection.execute(
                    "UPDATE tasks SET title = ? WHERE project_id = ? AND task_id = ?",
                    (original_title, project_id, task_id),
                )
                fourth = capture_or_reuse_current_authority_snapshot_locked(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    created_at="2026-08-04T02:03:00Z",
                )
                replay = capture_or_reuse_current_authority_snapshot_locked(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    created_at="2026-08-04T02:04:00Z",
                )
                self.assertEqual(fourth.generation, 4)
                self.assertNotEqual(fourth.authority_snapshot_id, first.authority_snapshot_id)
                self.assertEqual(replay, fourth)
                connection.commit()
                validate_evidence_ledger_storage(connection)

    def test_migration_rejects_invalid_current_contract_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            target, task_id = initialize_v17_fixture(Path(temp))
            with closing(connect(target.db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO task_contract_revisions(
                      contract_revision_id, task_id, project_id, revision,
                      scope, acceptance, constraints_text, authority_ref,
                      change_reason, created_at
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tg_contract_0000000000000001",
                        task_id,
                        target.project.project_id,
                        "x" * 4_001,
                        "Required acceptance.",
                        "",
                        "",
                        "",
                        "2026-08-04T00:00:00Z",
                    ),
                )
                connection.execute(
                    "UPDATE tasks SET current_contract_revision = 1 WHERE task_id = ?",
                    (task_id,),
                )
                connection.commit()
                before = database_projection(connection)
                changes_before = connection.total_changes

                with self.assertRaises(StorageError) as failure:
                    apply_evidence_ledger_capture_migration(connection)

                self.assertEqual(failure.exception.code, "project_state_unreadable")
                self.assertEqual(current_schema_version(connection), 17)
                self.assertEqual(connection.total_changes, changes_before)
                self.assertEqual(database_projection(connection), before)
                for table_name in NEW_TABLES:
                    self.assertIsNone(
                        connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE name = ?",
                            (table_name,),
                        ).fetchone()
                    )

    def test_full_validation_rejects_blob_contract_and_snapshot_basis(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_contract_task(
                db,
                repo,
                scope="Original scope",
                acceptance="Original acceptance",
            )
            with closing(connect(db)) as connection:
                snapshot_id = connection.execute(
                    """
                    SELECT current_authority_snapshot_id
                      FROM tasks
                     WHERE task_id = ?
                    """,
                    (task["task_id"],),
                ).fetchone()[0]
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_authority_snapshots_no_update'
                    """
                ).fetchone()[0]
                connection.execute("DROP TRIGGER trg_authority_snapshots_no_update")
                connection.execute(
                    """
                    UPDATE task_contract_revisions
                       SET scope = ?
                     WHERE task_id = ? AND revision = 1
                    """,
                    (sqlite3.Binary(b"abc"), task["task_id"]),
                )
                connection.execute(
                    """
                    UPDATE authority_snapshots
                       SET contract_scope = ?
                     WHERE authority_snapshot_id = ?
                    """,
                    (sqlite3.Binary(b"abc"), snapshot_id),
                )
                connection.execute(
                    """
                    UPDATE authority_snapshots
                       SET basis_digest = ?
                     WHERE authority_snapshot_id = ?
                    """,
                    (
                        recomputed_snapshot_basis_digest(
                            connection,
                            snapshot_id,
                            legacy_coercion=True,
                        ),
                        snapshot_id,
                    ),
                )
                connection.execute(trigger_sql)
                connection.commit()
                storage_types = connection.execute(
                    """
                    SELECT typeof(contract.scope), typeof(snapshot.contract_scope)
                      FROM task_contract_revisions AS contract
                      JOIN authority_snapshots AS snapshot
                        ON snapshot.project_id = contract.project_id
                       AND snapshot.task_id = contract.task_id
                       AND snapshot.contract_revision = contract.revision
                     WHERE contract.task_id = ? AND contract.revision = 1
                    """,
                    (task["task_id"],),
                ).fetchone()
                self.assertEqual(tuple(storage_types), ("blob", "blob"))
                with self.assertRaises(StorageError) as failure:
                    validate_evidence_ledger_storage(connection)
                self.assertEqual(
                    failure.exception.code,
                    "evidence_ledger_inconsistent",
                )

    def test_historical_snapshot_must_match_its_exact_contract_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_contract_task(
                db,
                repo,
                scope="Scope 1",
                acceptance="Acceptance 1",
            )
            second = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--contract-scope",
                "Scope 2",
                "--contract-acceptance",
                "Acceptance 2",
                "--contract-authority-ref",
                "plan.md:TG-M22.2",
                "--contract-change-reason",
                "Exercise historical binding",
                "--json",
            )
            self.assertEqual(second.returncode, 0, second.stdout)
            with closing(connect(db)) as connection:
                snapshot_id = connection.execute(
                    """
                    SELECT authority_snapshot_id
                      FROM authority_snapshots
                     WHERE task_id = ? AND contract_revision = 1
                    """,
                    (task["task_id"],),
                ).fetchone()[0]
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_authority_snapshots_no_update'
                    """
                ).fetchone()[0]
                connection.execute("DROP TRIGGER trg_authority_snapshots_no_update")
                connection.execute(
                    """
                    UPDATE authority_snapshots
                       SET contract_scope = 'Tampered historical scope'
                     WHERE authority_snapshot_id = ?
                    """,
                    (snapshot_id,),
                )
                connection.execute(
                    """
                    UPDATE authority_snapshots
                       SET basis_digest = ?
                     WHERE authority_snapshot_id = ?
                    """,
                    (
                        recomputed_snapshot_basis_digest(connection, snapshot_id),
                        snapshot_id,
                    ),
                )
                connection.execute(trigger_sql)
                connection.commit()
                with self.assertRaises(StorageError) as failure:
                    validate_evidence_ledger_storage(connection)
                self.assertEqual(
                    failure.exception.code,
                    "evidence_ledger_inconsistent",
                )

    def test_historical_authority_privacy_is_structural_and_sanitized(self):
        cases = (
            (
                "authorization",
                "title",
                "Authorization: Basic dXNlcjpwYXNz",
            ),
            (
                "bearer",
                "description",
                "Bearer sk-historical-opaque-token",
            ),
            (
                "private-key",
                "verification",
                "-----BEGIN PRIVATE KEY-----",
            ),
            ("password", "contract_scope", "password=opaque-value"),
            ("token", "contract_acceptance", "token=opaque-value"),
            ("api-key", "contract_constraints", "api_key=opaque-value"),
            (
                "traceback",
                "contract_authority_ref",
                "Traceback (most recent call last)",
            ),
            (
                "raw-stream",
                "verification",
                "raw stdout\nopaque output",
            ),
            (
                "large-diff",
                "description",
                "diff --git a/file b/file\n-old line\n+new line",
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            for case, snapshot_field, forbidden_value in cases:
                with self.subTest(case=case, field=snapshot_field):
                    case_root = Path(temp) / case
                    case_root.mkdir()
                    db, task, receipt = seed_historical_authority_receipt(
                        case_root,
                    )
                    with closing(connect(db)) as connection:
                        tamper_historical_authority_text(
                            connection,
                            snapshot_id=receipt[
                                "subject_authority_snapshot_id"
                            ],
                            snapshot_field=snapshot_field,
                            forbidden_value=forbidden_value,
                        )
                        with mock.patch.object(
                            tasks_module,
                            "reject_private_or_raw_content",
                        ), mock.patch.object(
                            tasks_module,
                            "_reject_private_or_raw_content_value",
                        ):
                            validate_evidence_ledger_storage(connection)
                        before = database_projection(connection)
                        changes_before = connection.total_changes
                        validations = (
                            (
                                "selected",
                                lambda: validate_selected_task_receipt_evidence(
                                    connection,
                                    project_id=task["project_id"],
                                    task_id=task["task_id"],
                                    review_receipt_ids=set(),
                                    review_finding_ids=set(),
                                    verification_receipt_ids={
                                        receipt["verification_receipt_id"]
                                    },
                                ),
                                "evidence_ledger_inconsistent",
                            ),
                            (
                                "full",
                                lambda: validate_evidence_ledger_storage(connection),
                                "evidence_ledger_inconsistent",
                            ),
                            (
                                "recovery",
                                lambda: validate_evidence_ledger_storage_for_recovery(
                                    connection
                                ),
                                "evidence_ledger_inconsistent",
                            ),
                            (
                                "reentry",
                                lambda: apply_migrations(connection),
                                "project_state_unreadable",
                            ),
                        )
                        for surface, validation, expected_code in validations:
                            with self.subTest(surface=surface):
                                with self.assertRaises(StorageError) as failure:
                                    validation()
                                self.assertEqual(
                                    failure.exception.code,
                                    expected_code,
                                )
                                self.assertNotIn(
                                    forbidden_value,
                                    str(failure.exception),
                                )
                                self.assertFalse(connection.in_transaction)
                                self.assertEqual(
                                    connection.total_changes,
                                    changes_before,
                                )
                                self.assertEqual(
                                    database_projection(connection),
                                    before,
                                )

    def test_authority_privacy_scans_share_one_success_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            db, _, _ = seed_historical_authority_receipt(Path(temp))
            real_detector = tasks_module.reject_private_or_raw_content
            real_legacy_detector = tasks_module.validate_legacy_m19_7_stored_text
            with closing(connect(db)) as connection:
                with mock.patch.object(
                    tasks_module,
                    "reject_private_or_raw_content",
                    wraps=real_detector,
                ) as detector, mock.patch.object(
                    tasks_module,
                    "validate_legacy_m19_7_stored_text",
                    wraps=real_legacy_detector,
                ) as legacy_detector:
                    authority = storage_module._validated_authority_context(
                        connection
                    )
            observed = [call.args for call in detector.call_args_list]
            expected_fields = {
                "title",
                "description",
                "verification",
                "contract_scope",
                "contract_acceptance",
                "contract_authority_ref",
            }
            self.assertTrue(expected_fields.issubset({call[0] for call in observed}))
            self.assertNotIn(
                "contract_constraints",
                {call[0] for call in observed},
            )
            self.assertEqual(
                observed.count(
                    ("verification", "Historical verification expectation")
                ),
                1,
            )
            self.assertEqual(
                observed.count(
                    ("description", "Historical authority description")
                ),
                1,
            )
            legacy_observed = [call.args for call in legacy_detector.call_args_list]
            self.assertEqual(
                legacy_observed.count(
                    ("contract_constraints", "Historical constraints")
                ),
                1,
            )
            self.assertEqual(
                legacy_observed.count(
                    ("contract_constraints", "Current constraints")
                ),
                1,
            )
            self.assertIn(
                ("description", "Historical authority description"),
                authority.ordinary_privacy_successes,
            )
            self.assertNotIn(
                ("contract_constraints", "Historical constraints"),
                authority.ordinary_privacy_successes,
            )
            self.assertFalse(
                {
                    "contract_scope",
                    "contract_acceptance",
                    "contract_authority_ref",
                }
                & {
                    field_name
                    for field_name, _ in authority.ordinary_privacy_successes
                }
            )

    def test_full_validator_reuses_authority_privacy_only_within_each_call(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Same-call privacy reuse task",
                verification="Same-call verification expectation",
            )
            real_detector = tasks_module.reject_private_or_raw_content
            with closing(connect(db)) as connection:
                stored = connection.execute(
                    "SELECT title, description, verification FROM tasks "
                    "WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                with mock.patch.object(
                    tasks_module,
                    "reject_private_or_raw_content",
                    wraps=real_detector,
                ) as detector:
                    for expected_count in (1, 2):
                        validate_evidence_ledger_storage(connection)
                        observed = [
                            call.args for call in detector.call_args_list
                        ]
                        for field_name in (
                            "title",
                            "description",
                            "verification",
                        ):
                            self.assertEqual(
                                observed.count(
                                    (field_name, stored[field_name])
                                ),
                                expected_count,
                                field_name,
                            )

    def test_task_privacy_reuse_seed_shape_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            invalid_seeds = (
                set(),
                frozenset({("title",)}),
                frozenset({("contract_scope", "Safe scope")}),
                frozenset({("title", 1)}),
            )
            with closing(connect(db)) as connection:
                rows = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchall()
                for invalid_seed in invalid_seeds:
                    with self.subTest(seed=invalid_seed):
                        with self.assertRaises(StorageError) as failure:
                            tasks_module.validate_stored_task_rows(
                                rows,
                                connection=connection,
                                source_schema_version=18,
                                expected_project_id=task["project_id"],
                                _prevalidated_privacy_successes=invalid_seed,
                            )
                        self.assertEqual(
                            failure.exception.code,
                            "project_state_unreadable",
                        )

    def test_authority_privacy_cache_separates_ordinary_and_legacy_modes(self):
        privacy_success_cache: set[tuple[str, str, str]] = set()
        value = "Safe stored constraints"
        with mock.patch.object(
            tasks_module,
            "reject_private_or_raw_content",
            wraps=tasks_module.reject_private_or_raw_content,
        ) as detector, mock.patch.object(
            tasks_module,
            "validate_legacy_m19_7_stored_text",
            wraps=tasks_module.validate_legacy_m19_7_stored_text,
        ) as legacy_detector:
            for legacy_m19_7_stored in (False, True, False, True):
                storage_module._validate_evidence_ledger_stored_privacy(
                    "contract_constraints",
                    value,
                    privacy_success_cache=privacy_success_cache,
                    legacy_m19_7_stored=legacy_m19_7_stored,
                )

        detector.assert_called_once_with("contract_constraints", value)
        legacy_detector.assert_called_once_with("contract_constraints", value)
        self.assertEqual(
            privacy_success_cache,
            {
                ("ordinary", "contract_constraints", value),
                ("legacy_m19_7_stored", "contract_constraints", value),
            },
        )

    def test_authority_privacy_cache_never_records_a_rejection(self):
        privacy_success_cache: set[tuple[str, str, str]] = set()
        with self.assertRaises(StorageError):
            storage_module._validate_evidence_ledger_stored_privacy(
                "description",
                "Authorization: private-cache-value",
                privacy_success_cache=privacy_success_cache,
            )
        self.assertEqual(privacy_success_cache, set())

    def test_current_task_storage_class_must_match_snapshot_without_coercion(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            with closing(connect(db)) as connection:
                snapshot_id = connection.execute(
                    """
                    SELECT current_authority_snapshot_id
                      FROM tasks
                     WHERE task_id = ?
                    """,
                    (task["task_id"],),
                ).fetchone()[0]
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_authority_snapshots_no_update'
                    """
                ).fetchone()[0]
                connection.execute("DROP TRIGGER trg_authority_snapshots_no_update")
                connection.execute(
                    "UPDATE tasks SET title = ? WHERE task_id = ?",
                    (sqlite3.Binary(b"abc"), task["task_id"]),
                )
                connection.execute(
                    """
                    UPDATE authority_snapshots
                       SET task_title = ?
                     WHERE authority_snapshot_id = ?
                    """,
                    ("b'abc'", snapshot_id),
                )
                connection.execute(
                    """
                    UPDATE authority_snapshots
                       SET basis_digest = ?
                     WHERE authority_snapshot_id = ?
                    """,
                    (
                        recomputed_snapshot_basis_digest(connection, snapshot_id),
                        snapshot_id,
                    ),
                )
                connection.execute(trigger_sql)
                connection.commit()
                self.assertEqual(
                    connection.execute(
                        "SELECT typeof(title) FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    "blob",
                )
                with self.assertRaises(StorageError) as failure:
                    validate_evidence_ledger_storage(connection)
                self.assertEqual(
                    failure.exception.code,
                    "evidence_ledger_inconsistent",
                )

    def test_review_provenance_guard_and_reentry_reject_native_v0_disguise(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            set_target(db, repo, task_id)
            with closing(connect(db)) as connection:
                target = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                values = (
                    "tg_review_receipt_0000000000000001",
                    task_id,
                    target["project_id"],
                    "raw-reviewer",
                    "independent",
                    "pass",
                    target["review_target_kind"],
                    target["review_target_value"],
                    target["review_target_base_revision"],
                    target["review_target_generation"],
                    "",
                    0,
                    "2026-08-04T03:00:00Z",
                )
                sql = """
                    INSERT INTO review_receipts(
                      review_receipt_id, task_id, project_id, reviewer_key,
                      receipt_kind, verdict, target_kind, target_value,
                      target_base_revision, target_generation, summary,
                      user_approved, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "invalid_review_provenance_basis"
                ):
                    connection.execute(sql, values)
                connection.rollback()

                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_review_receipts_provenance_basis_insert'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_review_receipts_provenance_basis_insert"
                )
                connection.execute(sql, values)
                connection.execute(trigger_sql)
                connection.commit()

                with self.assertRaises(StorageError) as failure:
                    apply_migrations(connection)
                self.assertEqual(failure.exception.code, "project_state_unreadable")

    def test_reentry_rejects_manifest_and_closed_reference_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in ("manifest", "reserved-reference"):
                with self.subTest(case=case):
                    case_root = Path(temp) / case
                    case_root.mkdir()
                    repo, db = initialize(case_root)
                    task = add_task(db, repo)
                    set_target(db, repo, task["task_id"])
                    with closing(connect(db)) as connection:
                        if case == "manifest":
                            manifest = connection.execute(
                                "SELECT * FROM artifact_manifests"
                            ).fetchone()
                            connection.execute(
                                """
                                INSERT INTO artifact_manifest_entries(
                                  project_id, task_id, artifact_manifest_id,
                                  ordinal, entry_kind, old_path, new_path,
                                  before_mode, before_object_id,
                                  after_mode, after_object_id
                                ) VALUES (?, ?, ?, 0, 'add', NULL, 'new.txt',
                                          NULL, NULL, '100644', ?)
                                """,
                                (
                                    manifest["project_id"],
                                    manifest["task_id"],
                                    manifest["artifact_manifest_id"],
                                    "1" * 40,
                                ),
                            )
                        else:
                            reference = dict(
                                connection.execute(
                                    "SELECT * FROM evidence_references"
                                ).fetchone()
                            )
                            reference.update(
                                evidence_reference_id=(
                                    "tg_evidence_reference_0000000000000001"
                                ),
                                source_kind="derived_analysis",
                                source_state="recorded",
                                source_id="tg_derived_analysis_0000000000000001",
                            )
                            fields = tuple(reference)
                            connection.execute(
                                f"INSERT INTO evidence_references({', '.join(fields)}) "
                                f"VALUES ({', '.join('?' for _ in fields)})",
                                tuple(reference[field] for field in fields),
                            )
                        connection.commit()
                        with self.assertRaises(StorageError) as failure:
                            apply_migrations(connection)
                        self.assertEqual(
                            failure.exception.code, "project_state_unreadable"
                        )

    def test_historical_verification_subject_must_match_its_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            first_generation = set_target(db, repo, task_id)
            recorded = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            with closing(connect(db)) as connection:
                row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE tasks
                       SET title = 'Changed authority',
                           review_target_kind = '',
                           review_target_value = '',
                           review_target_base_revision = '',
                           review_target_generation = review_target_generation + 1,
                           review_target_capture_version = 0,
                           review_target_authority_snapshot_id = NULL,
                           review_target_acceptance_criterion_id = NULL,
                           review_target_verification_criterion_id = NULL,
                           review_target_artifact_manifest_id = NULL
                     WHERE project_id = ? AND task_id = ?
                    """,
                    (row["project_id"], task_id),
                )
                second_snapshot = capture_or_reuse_current_authority_snapshot_locked(
                    connection,
                    project_id=row["project_id"],
                    task_id=task_id,
                    created_at="2026-08-04T04:00:00Z",
                )
                connection.commit()
            set_target(db, repo, task_id)
            with closing(connect(db)) as connection:
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_verification_receipts_no_update'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_verification_receipts_no_update"
                )
                connection.execute(
                    """
                    UPDATE verification_receipts
                       SET subject_authority_snapshot_id = ?
                    """,
                    (second_snapshot.authority_snapshot_id,),
                )
                connection.execute(trigger_sql)
                connection.commit()
                shown = run_taskgov(
                    "task",
                    "show",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--json",
                )
                self.assertNotEqual(shown.returncode, 0, shown.stdout)
                self.assertEqual(
                    json_payload(shown)["errors"][0]["code"],
                    "invalid_verification_evidence",
                )
                with self.assertRaises(StorageError) as failure:
                    apply_migrations(connection)
                self.assertEqual(failure.exception.code, "project_state_unreadable")

    def test_historical_cycle_subject_must_match_its_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            first_generation = seed_current_review_evidence(db, repo, task_id)
            recorded = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            reopened = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--status",
                "in_progress",
                "--reopen-reason",
                "Exercise historical subject validation",
                "--json",
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout)
            changed = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--title",
                "Changed cycle authority",
                "--json",
            )
            self.assertEqual(changed.returncode, 0, changed.stdout)
            set_target(db, repo, task_id)
            with closing(connect(db)) as connection:
                second_snapshot_id = connection.execute(
                    """
                    SELECT review_target_authority_snapshot_id
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()[0]
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_task_completion_cycles_no_update'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_task_completion_cycles_no_update"
                )
                connection.execute(
                    """
                    UPDATE task_completion_cycles
                       SET subject_authority_snapshot_id = ?
                    """,
                    (second_snapshot_id,),
                )
                connection.execute(trigger_sql)
                connection.commit()
                with self.assertRaises(StorageError) as failure:
                    apply_migrations(connection)
                self.assertEqual(failure.exception.code, "project_state_unreadable")

    def test_historical_cycle_must_match_its_qualifying_verification_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in ("digest", "wrong-receipt"):
                with self.subTest(case=case):
                    case_root = Path(temp) / case
                    case_root.mkdir()
                    repo, db = initialize(case_root)
                    task = add_task(db, repo)
                    task_id = task["task_id"]
                    generation = seed_current_review_evidence(
                        db,
                        repo,
                        task_id,
                    )
                    recorded = add_receipt(db, repo, task_id, generation)
                    self.assertEqual(recorded.returncode, 0, recorded.stdout)
                    completed = completion(db, repo, task_id)
                    self.assertEqual(completed.returncode, 0, completed.stdout)

                    wrong_receipt_id = None
                    if case == "wrong-receipt":
                        other = add_task(
                            db,
                            repo,
                            title="Other verification subject",
                        )
                        other_generation = seed_current_review_evidence(
                            db,
                            repo,
                            other["task_id"],
                        )
                        other_recorded = add_receipt(
                            db,
                            repo,
                            other["task_id"],
                            other_generation,
                        )
                        self.assertEqual(
                            other_recorded.returncode,
                            0,
                            other_recorded.stdout,
                        )
                        with closing(connect(db)) as connection:
                            wrong_receipt_id = connection.execute(
                                """
                                SELECT verification_receipt_id
                                  FROM verification_receipts
                                 WHERE task_id = ?
                                """,
                                (other["task_id"],),
                            ).fetchone()[0]

                    with closing(connect(db)) as connection:
                        trigger_sql = connection.execute(
                            """
                            SELECT sql FROM sqlite_master
                             WHERE type = 'trigger'
                               AND name = 'trg_task_completion_cycles_no_update'
                            """
                        ).fetchone()[0]
                        connection.execute(
                            "DROP TRIGGER trg_task_completion_cycles_no_update"
                        )
                        if case == "digest":
                            connection.execute(
                                """
                                UPDATE task_completion_cycles
                                   SET verification_expectation_digest = ?
                                 WHERE task_id = ?
                                """,
                                ("0" * 64, task_id),
                            )
                        else:
                            connection.execute(
                                """
                                UPDATE task_completion_cycles
                                   SET verification_receipt_id = ?
                                 WHERE task_id = ?
                                """,
                                (wrong_receipt_id, task_id),
                            )
                        connection.execute(trigger_sql)
                        connection.commit()
                        with self.assertRaises(StorageError) as validation_failure:
                            validate_evidence_ledger_storage(connection)
                        self.assertEqual(
                            validation_failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )
                        with self.assertRaises(StorageError) as reentry_failure:
                            apply_migrations(connection)
                        self.assertEqual(
                            reentry_failure.exception.code,
                            "project_state_unreadable",
                        )

    def test_public_reads_and_setup_reject_missing_initial_v18_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            install = make_physical_install(Path(temp))
            initialized = install.run("setup", "--json")
            self.assertEqual(initialized.returncode, 0, initialized.stdout)
            created = install.run(
                "task",
                "add",
                "--title",
                "Representative pre-guard Task",
                "--status",
                "in_progress",
                "--review-tier",
                "2",
                "--verification",
                "python -m unittest tests.test_m22_evidence_ledger_storage",
                "--json",
            )
            self.assertEqual(created.returncode, 0, created.stdout)
            task_id = json_payload(created)["data"]["task"]["task_id"]
            targeted = install.run(
                "review",
                "target",
                "set",
                task_id,
                "--kind",
                "diff_fingerprint",
                "--revision",
                "sha256:" + "a" * 64,
                "--json",
            )
            self.assertEqual(targeted.returncode, 0, targeted.stdout)
            generation = json_payload(targeted)["data"]["task"][
                "review_target_generation"
            ]
            verified = install.run(
                "verification",
                "receipt",
                "add",
                task_id,
                "--result",
                "pass",
                "--duration-ms",
                "25",
                "--scope-coverage",
                "full",
                "--expected-target-generation",
                str(generation),
                "--json",
            )
            self.assertEqual(verified.returncode, 0, verified.stdout)
            for reviewer in ("reviewer-one", "reviewer-two"):
                reviewed = install.run(
                    "review",
                    "receipt",
                    "add",
                    task_id,
                    "--reviewer",
                    reviewer,
                    "--kind",
                    "independent",
                    "--verdict",
                    "changes_requested",
                    "--summary",
                    "Focused changes requested",
                    "--reviewer-class",
                    "human",
                    "--model-state",
                    "not_applicable",
                    "--skill-state",
                    "not_applicable",
                    "--context-relation",
                    "external_context",
                    "--json",
                )
                self.assertEqual(reviewed.returncode, 0, reviewed.stdout)

            with closing(connect(install.db_path)) as connection:
                connection.execute(
                    "DROP TRIGGER trg_review_receipts_provenance_basis_insert"
                )
                connection.commit()
            before_preview = install.db_path.read_bytes()
            rejected = install.run(
                "task", "show", task_id, "--read-only", "--json"
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(
                json_payload(rejected)["errors"][0]["code"],
                "project_state_unreadable",
            )
            preview = install.run("setup", "--read-only", "--json")
            self.assertNotEqual(preview.returncode, 0)
            self.assertEqual(
                json_payload(preview)["errors"][0]["code"],
                "project_state_unreadable",
            )
            self.assertEqual(install.db_path.read_bytes(), before_preview)

    def test_strict_setup_reentry_rejects_multiple_missing_v18_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            with closing(connect(db)) as connection:
                connection.execute(
                    "DROP TRIGGER trg_review_receipts_provenance_basis_insert"
                )
                connection.execute(
                    "DROP TRIGGER trg_evidence_references_no_update"
                )
                connection.commit()
                before = database_projection(connection)
            with self.assertRaises(StorageError) as preview_failure:
                inspect_setup_state(target_for(db, repo))
            self.assertEqual(
                preview_failure.exception.code,
                "project_state_unreadable",
            )
            with closing(connect(db)) as connection:
                with self.assertRaises(StorageError) as repair_failure:
                    apply_migrations(connection)
                self.assertEqual(
                    repair_failure.exception.code,
                    "migration_required",
                )
                self.assertEqual(database_projection(connection), before)

    def test_huge_snapshot_generation_is_rejected_without_proportional_allocation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            set_target(db, repo, task["task_id"])
            with closing(connect(db)) as connection:
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_authority_snapshots_no_update'
                    """
                ).fetchone()[0]
                connection.execute("DROP TRIGGER trg_authority_snapshots_no_update")
                connection.execute(
                    "UPDATE authority_snapshots SET generation = ?",
                    (SQLITE_INT64_MAX,),
                )
                connection.execute(
                    """
                    UPDATE tasks
                       SET current_authority_snapshot_generation = ?
                     WHERE task_id = ?
                    """,
                    (SQLITE_INT64_MAX, task["task_id"]),
                )
                connection.execute(trigger_sql)
                connection.commit()
                with self.assertRaises(StorageError) as failure:
                    validate_evidence_ledger_storage(connection)
                self.assertEqual(
                    failure.exception.code,
                    "evidence_ledger_inconsistent",
                )

    def test_provenance_created_at_must_be_canonical_and_match_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            for case, created_at in (
                ("invalid", "not-a-timestamp"),
                ("mismatch", "2026-08-04T23:59:59Z"),
            ):
                with self.subTest(case=case):
                    case_root = Path(temp) / case
                    case_root.mkdir()
                    repo, db = initialize(case_root)
                    task = add_task(db, repo, review_tier=1)
                    seed_current_review_evidence(db, repo, task["task_id"])
                    with closing(connect(db)) as connection:
                        trigger_sql = connection.execute(
                            """
                            SELECT sql FROM sqlite_master
                             WHERE type = 'trigger'
                               AND name = 'trg_review_receipt_provenance_no_update'
                            """
                        ).fetchone()[0]
                        connection.execute(
                            "DROP TRIGGER trg_review_receipt_provenance_no_update"
                        )
                        connection.execute(
                            "UPDATE review_receipt_provenance SET created_at = ?",
                            (created_at,),
                        )
                        connection.execute(trigger_sql)
                        connection.commit()
                        receipt_id = connection.execute(
                            "SELECT review_receipt_id FROM review_receipts"
                        ).fetchone()[0]
                        with self.assertRaises(StorageError) as read_failure:
                            read_review_receipt_with_provenance(
                                connection,
                                review_receipt_id=receipt_id,
                            )
                        self.assertEqual(
                            read_failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )
                        with self.assertRaises(StorageError) as failure:
                            apply_migrations(connection)
                        self.assertEqual(
                            failure.exception.code,
                            "project_state_unreadable",
                        )

    def test_contract_criteria_require_canonical_time_and_a_snapshot_link(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in ("timestamp", "orphan"):
                with self.subTest(case=case):
                    case_root = Path(temp) / case
                    case_root.mkdir()
                    repo, db = initialize(case_root)
                    task = add_task(db, repo)
                    set_target(db, repo, task["task_id"])
                    with closing(connect(db)) as connection:
                        if case == "timestamp":
                            trigger_sql = connection.execute(
                                """
                                SELECT sql FROM sqlite_master
                                 WHERE type = 'trigger'
                                   AND name = 'trg_contract_criteria_no_update'
                                """
                            ).fetchone()[0]
                            connection.execute(
                                "DROP TRIGGER trg_contract_criteria_no_update"
                            )
                            connection.execute(
                                "UPDATE contract_criteria SET created_at = 'not-a-timestamp'"
                            )
                            connection.execute(trigger_sql)
                        else:
                            project_id = connection.execute(
                                "SELECT project_id FROM tasks WHERE task_id = ?",
                                (task["task_id"],),
                            ).fetchone()[0]
                            text = "Orphan acceptance criterion"
                            connection.execute(
                                """
                                INSERT INTO contract_criteria(
                                  criterion_id, project_id, task_id,
                                  criterion_kind, criterion_text, digest,
                                  created_at
                                ) VALUES (?, ?, ?, 'acceptance', ?, ?, ?)
                                """,
                                (
                                    "tg_contract_criterion_0000000000000001",
                                    project_id,
                                    task["task_id"],
                                    text,
                                    contract_criterion_digest("acceptance", text),
                                    "2026-08-04T00:00:00Z",
                                ),
                            )
                        connection.commit()
                        with self.assertRaises(StorageError) as failure:
                            apply_migrations(connection)
                        self.assertEqual(
                            failure.exception.code,
                            "project_state_unreadable",
                        )

    def test_single_review_reader_rejects_code_relation_corruption_and_overflow(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in ("ordinal", "owner", "overflow"):
                with self.subTest(case=case):
                    case_root = Path(temp) / case
                    case_root.mkdir()
                    repo, db = initialize(case_root)
                    task = add_task(db, repo, review_tier=1)
                    set_target(db, repo, task["task_id"])
                    reviewed = run_taskgov(
                        "review",
                        "receipt",
                        "add",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task["task_id"],
                        "--reviewer",
                        "bounded-reader",
                        "--kind",
                        "independent",
                        "--verdict",
                        "pass",
                        "--summary",
                        "Bounded reader evidence",
                        "--reviewer-class",
                        "human",
                        "--model-state",
                        "not_applicable",
                        "--skill-state",
                        "not_applicable",
                        "--context-relation",
                        "external_context",
                        "--review-profile",
                        "general",
                        "--review-lens",
                        "correctness",
                        "--review-method",
                        "review_packet_inspection",
                        "--json",
                    )
                    self.assertEqual(reviewed.returncode, 0, reviewed.stdout)
                    with closing(connect(db)) as connection:
                        receipt_id = connection.execute(
                            "SELECT review_receipt_id FROM review_receipts"
                        ).fetchone()[0]
                        provenance = connection.execute(
                            "SELECT * FROM review_receipt_provenance"
                        ).fetchone()
                        if case in {"ordinal", "owner"}:
                            trigger_sql = connection.execute(
                                """
                                SELECT sql FROM sqlite_master
                                 WHERE type = 'trigger'
                                   AND name = 'trg_review_receipt_provenance_codes_no_update'
                                """
                            ).fetchone()[0]
                            if case == "owner":
                                connection.commit()
                                connection.execute("PRAGMA foreign_keys = OFF")
                            connection.execute(
                                "DROP TRIGGER trg_review_receipt_provenance_codes_no_update"
                            )
                            if case == "ordinal":
                                connection.execute(
                                    """
                                    UPDATE review_receipt_provenance_codes
                                       SET ordinal = 5
                                     WHERE code_kind = 'profile'
                                    """
                                )
                            else:
                                connection.execute(
                                    """
                                    UPDATE review_receipt_provenance_codes
                                       SET task_id = 'tg_task_wrong_owner'
                                     WHERE code_kind = 'profile'
                                    """
                                )
                            connection.execute(trigger_sql)
                        else:
                            for ordinal in range(1, 19):
                                connection.execute(
                                    """
                                    INSERT INTO review_receipt_provenance_codes(
                                      project_id, task_id, review_provenance_id,
                                      code_kind, ordinal, code
                                    ) VALUES (?, ?, ?, 'profile', ?, ?)
                                    """,
                                    (
                                        provenance["project_id"],
                                        provenance["task_id"],
                                        provenance["review_provenance_id"],
                                        ordinal,
                                        f"invalid-code-{ordinal}",
                                    ),
                                )
                        connection.commit()
                        with self.assertRaises(StorageError) as failure:
                            read_review_receipt_with_provenance(
                                connection,
                                review_receipt_id=receipt_id,
                            )
                        self.assertEqual(
                            failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )

    def test_single_review_reader_rejects_basis_zero_reverse_row_and_bad_time(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in ("reverse", "timestamp"):
                with self.subTest(case=case):
                    case_root = Path(temp) / case
                    case_root.mkdir()
                    repo, db = initialize(case_root)
                    task = add_task(db, repo, review_tier=0)
                    seed_current_review_evidence(db, repo, task["task_id"])
                    with closing(connect(db)) as connection:
                        receipt = connection.execute(
                            "SELECT * FROM review_receipts"
                        ).fetchone()
                        if case == "reverse":
                            connection.execute(
                                """
                                INSERT INTO review_receipt_provenance(
                                  review_provenance_id, review_receipt_id,
                                  project_id, task_id, provenance_version,
                                  reviewer_class, model_state,
                                  declared_model_id, skill_state,
                                  declared_skill_id, declared_skill_version,
                                  context_relation, assurance_class,
                                  producer_class, producer_version, digest,
                                  created_at
                                ) VALUES (?, ?, ?, ?, 1, 'human',
                                          'not_applicable', NULL,
                                          'not_applicable', NULL, NULL,
                                          'external_context',
                                          'bound_attestation', 'trusted_caller',
                                          1, ?, ?)
                                """,
                                (
                                    "tg_review_provenance_0000000000000001",
                                    receipt["review_receipt_id"],
                                    receipt["project_id"],
                                    receipt["task_id"],
                                    "sha256:" + "0" * 64,
                                    receipt["created_at"],
                                ),
                            )
                        else:
                            connection.execute(
                                "UPDATE review_receipts SET created_at = 'not-a-timestamp'"
                            )
                        connection.commit()
                        with self.assertRaises(StorageError) as failure:
                            read_review_receipt_with_provenance(
                                connection,
                                review_receipt_id=receipt["review_receipt_id"],
                            )
                        self.assertEqual(
                            failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )

    def test_stored_review_receipt_tamper_fails_selected_and_full_validation(self):
        cases = (
            (
                "private-reviewer",
                "reviewer_key = ?",
                ("Authorization: Bearer stored-review-secret",),
            ),
            ("long-reviewer", "reviewer_key = ?", ("r" * 501,)),
            (
                "private-summary",
                "summary = ?",
                ("api_key=stored-review-secret",),
            ),
            ("long-summary", "summary = ?", ("s" * 1_001,)),
            (
                "semantic-matrix",
                "verdict = 'changes_requested', summary = ''",
                (),
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            for case, assignment, values in cases:
                with self.subTest(case=case):
                    root = Path(temp) / case
                    root.mkdir()
                    repo, db = initialize(root)
                    task = add_task(db, repo, review_tier=1)
                    seed_current_review_evidence(db, repo, task["task_id"])
                    database_target = target_for(db, repo)
                    with closing(connect_initialized(database_target)) as connection:
                        receipt_id = connection.execute(
                            "SELECT review_receipt_id FROM review_receipts"
                        ).fetchone()[0]
                        stored = read_review_receipt_with_provenance(
                            connection,
                            review_receipt_id=receipt_id,
                        )
                        connection.execute(
                            f"UPDATE review_receipts SET {assignment} "
                            "WHERE review_receipt_id = ?",
                            (*values, receipt_id),
                        )
                        rewrite_review_reference_digest(
                            connection,
                            source_kind="review_receipt",
                            source_id=receipt_id,
                            review_provenance=stored["provenance"],
                        )
                        connection.commit()
                        if case == "semantic-matrix":
                            direct_before = db.read_bytes()
                            changes_before = connection.total_changes
                            counts_before = tuple(
                                connection.execute(
                                    f"SELECT COUNT(*) FROM {table_name}"
                                ).fetchone()[0]
                                for table_name in (
                                    "review_findings",
                                    "evidence_references",
                                    "task_events",
                                )
                            )
                            with self.assertRaises(StorageError) as direct_failure:
                                with connection:
                                    review_service.add_review_finding(
                                        connection,
                                        database_target.project,
                                        task["task_id"],
                                        receipt_id=receipt_id,
                                        severity="medium",
                                        summary="Direct parent validation",
                                        database_target=database_target,
                                    )
                            self.assertEqual(
                                (direct_failure.exception.code,
                                 direct_failure.exception.message),
                                (
                                    "evidence_ledger_inconsistent",
                                    "stored evidence ledger is inconsistent",
                                ),
                            )
                            self.assertEqual(connection.total_changes, changes_before)
                            self.assertEqual(
                                tuple(
                                    connection.execute(
                                        f"SELECT COUNT(*) FROM {table_name}"
                                    ).fetchone()[0]
                                    for table_name in (
                                        "review_findings",
                                        "evidence_references",
                                        "task_events",
                                    )
                                ),
                                counts_before,
                            )
                            self.assertEqual(db.read_bytes(), direct_before)

                    before = db.read_bytes()
                    add_finding = run_taskgov(
                        "review",
                        "finding",
                        "add",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task["task_id"],
                        "--receipt-id",
                        receipt_id,
                        "--severity",
                        "medium",
                        "--summary",
                        "Stored parent must validate",
                        "--json",
                    )
                    self.assertEqual(add_finding.returncode, 2, add_finding.stdout)
                    self.assertEqual(
                        json_payload(add_finding)["errors"][0],
                        {
                            "code": "evidence_ledger_inconsistent",
                            "message": "stored evidence ledger is inconsistent",
                        },
                    )
                    self.assertEqual(db.read_bytes(), before)

                    shown = run_taskgov(
                        "task",
                        "show",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task["task_id"],
                        "--read-only",
                        "--json",
                    )
                    self.assertEqual(shown.returncode, 2, shown.stdout)
                    self.assertEqual(
                        json_payload(shown)["errors"][0]["code"],
                        "evidence_ledger_inconsistent",
                    )
                    self.assertEqual(db.read_bytes(), before)
                    with closing(connect(db)) as connection:
                        with self.assertRaises(StorageError) as read_failure:
                            read_review_receipt_with_provenance(
                                connection,
                                review_receipt_id=receipt_id,
                            )
                        self.assertEqual(
                            (read_failure.exception.code, read_failure.exception.message),
                            (
                                "evidence_ledger_inconsistent",
                                "stored evidence ledger is inconsistent",
                            ),
                        )
                        for validator in (
                            validate_evidence_ledger_storage,
                            validate_evidence_ledger_storage_for_recovery,
                        ):
                            with self.assertRaises(StorageError) as failure:
                                validator(connection)
                            self.assertEqual(
                                failure.exception.code,
                                "evidence_ledger_inconsistent",
                            )
                    self.assertEqual(db.read_bytes(), before)
                    if case == "semantic-matrix":
                        with closing(connect(db)) as connection:
                            with self.assertRaises(StorageError) as reentry_failure:
                                apply_migrations(connection)
                            self.assertEqual(
                                reentry_failure.exception.code,
                                "project_state_unreadable",
                            )
                        self.assertEqual(db.read_bytes(), before)

    def test_stored_review_finding_text_tamper_fails_public_and_full_validation(self):
        cases = (
            (
                "private-summary",
                "summary",
                "Authorization: Bearer stored-finding-secret",
                False,
            ),
            ("long-summary", "summary", "f" * 1_001, False),
            (
                "private-resolution",
                "resolution_summary",
                "api_key=stored-resolution-secret",
                True,
            ),
            (
                "long-resolution",
                "resolution_summary",
                "x" * 1_001,
                True,
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            for case, field, value, resolve_first in cases:
                with self.subTest(case=case):
                    root = Path(temp) / case
                    root.mkdir()
                    repo, db = initialize(root)
                    task = add_task(db, repo, review_tier=1)
                    seed_current_review_evidence(db, repo, task["task_id"])
                    with closing(connect(db)) as connection:
                        receipt_id = connection.execute(
                            "SELECT review_receipt_id FROM review_receipts"
                        ).fetchone()[0]
                    added = run_taskgov(
                        "review",
                        "finding",
                        "add",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task["task_id"],
                        "--receipt-id",
                        receipt_id,
                        "--severity",
                        "medium",
                        "--summary",
                        "Stored Finding must validate",
                        "--json",
                    )
                    self.assertEqual(added.returncode, 0, added.stdout)
                    finding_id = json_payload(added)["data"]["finding"][
                        "review_finding_id"
                    ]
                    if resolve_first:
                        resolved = run_taskgov(
                            "review",
                            "finding",
                            "resolve",
                            "--repo",
                            str(repo),
                            "--db",
                            str(db),
                            finding_id,
                            "--resolution",
                            "Stored resolution initially validates",
                            "--json",
                        )
                        self.assertEqual(resolved.returncode, 0, resolved.stdout)

                    database_target = target_for(db, repo)
                    with closing(connect_initialized(database_target)) as connection:
                        connection.execute(
                            f"UPDATE review_findings SET {field} = ? "
                            "WHERE review_finding_id = ?",
                            (value, finding_id),
                        )
                        if field == "summary":
                            rewrite_review_reference_digest(
                                connection,
                                source_kind="review_finding",
                                source_id=finding_id,
                            )
                        connection.commit()
                        direct_before = db.read_bytes()
                        stored_task = connection.execute(
                            "SELECT * FROM tasks WHERE task_id = ?",
                            (task["task_id"],),
                        ).fetchone()
                        with self.assertRaises(StorageError) as public_failure:
                            read_review_evidence(
                                connection,
                                database_target.project.project_id,
                                task["task_id"],
                                validated_task=stored_task,
                            )
                        self.assertEqual(
                            (
                                public_failure.exception.code,
                                public_failure.exception.message,
                            ),
                            (
                                "evidence_ledger_inconsistent",
                                "stored evidence ledger is inconsistent",
                            ),
                        )
                        self.assertEqual(db.read_bytes(), direct_before)
                        changes_before = connection.total_changes
                        counts_before = tuple(
                            connection.execute(
                                f"SELECT COUNT(*) FROM {table_name}"
                            ).fetchone()[0]
                            for table_name in (
                                "review_findings",
                                "task_events",
                            )
                        )
                        with self.assertRaises(StorageError) as direct_failure:
                            with connection:
                                review_service.resolve_review_finding(
                                    connection,
                                    database_target.project,
                                    finding_id,
                                    resolution="Direct resolution validation",
                                    database_target=database_target,
                                )
                        self.assertEqual(
                            (
                                direct_failure.exception.code,
                                direct_failure.exception.message,
                            ),
                            (
                                "evidence_ledger_inconsistent",
                                "stored evidence ledger is inconsistent",
                            ),
                        )
                        self.assertEqual(connection.total_changes, changes_before)
                        self.assertEqual(
                            tuple(
                                connection.execute(
                                    f"SELECT COUNT(*) FROM {table_name}"
                                ).fetchone()[0]
                                for table_name in (
                                    "review_findings",
                                    "task_events",
                                )
                            ),
                            counts_before,
                        )
                        self.assertEqual(db.read_bytes(), direct_before)

                    before = db.read_bytes()
                    shown = run_taskgov(
                        "task",
                        "show",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task["task_id"],
                        "--read-only",
                        "--json",
                    )
                    self.assertEqual(shown.returncode, 2, shown.stdout)
                    self.assertEqual(
                        json_payload(shown)["errors"][0],
                        {
                            "code": "evidence_ledger_inconsistent",
                            "message": "stored evidence ledger is inconsistent",
                        },
                    )
                    self.assertNotIn(value, shown.stdout)
                    self.assertEqual(db.read_bytes(), before)

                    if not resolve_first:
                        resolution = run_taskgov(
                            "review",
                            "finding",
                            "resolve",
                            "--repo",
                            str(repo),
                            "--db",
                            str(db),
                            finding_id,
                            "--resolution",
                            "Corrupt Finding must not resolve",
                            "--json",
                        )
                        self.assertEqual(resolution.returncode, 2, resolution.stdout)
                        self.assertEqual(
                            json_payload(resolution)["errors"][0]["code"],
                            "evidence_ledger_inconsistent",
                        )
                        self.assertEqual(db.read_bytes(), before)

                    with closing(connect(db)) as connection:
                        with self.assertRaises(StorageError) as failure:
                            validate_evidence_ledger_storage(connection)
                        self.assertEqual(
                            failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )
                    self.assertEqual(db.read_bytes(), before)

    def test_verification_inventory_rejects_legacy_receipt_ownership_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            for source_schema_version in (17, 18):
                with self.subTest(source_schema_version=source_schema_version):
                    root = Path(temp) / f"verification-schema-{source_schema_version}"
                    target, task_id, receipt_id = (
                        initialize_v17_verification_fixture(root)
                    )
                    repo = target.project.canonical_repo
                    db = target.db_path
                    if source_schema_version == 18:
                        with closing(connect(db)) as connection:
                            apply_evidence_ledger_capture_migration(connection)
                            apply_completion_evidence_bundle_migration(connection)
                            self.assertEqual(
                                apply_migrations(connection),
                                ([20, 21], []),
                            )

                    corrupt_owner = "corrupt-verification-project-owner"
                    with closing(connect(db)) as connection:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        trigger_sql = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                            "AND name = 'trg_verification_receipts_no_update'"
                        ).fetchone()[0]
                        connection.execute(
                            "DROP TRIGGER trg_verification_receipts_no_update"
                        )
                        connection.execute(
                            "UPDATE verification_receipts SET project_id = ? "
                            "WHERE verification_receipt_id = ?",
                            (corrupt_owner, receipt_id),
                        )
                        connection.execute(trigger_sql)
                        connection.commit()
                        before = self._assert_direct_verification_inventory_fails(
                            connection=connection,
                            db=db,
                            project_id=target.project.project_id,
                            task_id=task_id,
                            redacted_values=(corrupt_owner,),
                        )

                    if source_schema_version == 18:
                        self._assert_verification_inventory_operations_fail(
                            db=db,
                            repo=repo,
                            task_id=task_id,
                            before=before,
                            redacted_values=(corrupt_owner,),
                        )

    def test_verification_inventory_rejects_blob_task_id_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            for source_schema_version in (17, 18):
                with self.subTest(source_schema_version=source_schema_version):
                    target, task_id, receipt_id = (
                        initialize_v17_verification_fixture(
                            Path(temp) / f"blob-task-{source_schema_version}"
                        )
                    )
                    db = target.db_path
                    repo = target.project.canonical_repo
                    if source_schema_version == 18:
                        with closing(connect(db)) as connection:
                            apply_evidence_ledger_capture_migration(connection)
                            apply_completion_evidence_bundle_migration(connection)
                            self.assertEqual(
                                apply_migrations(connection),
                                ([20, 21], []),
                            )

                    with closing(connect(db)) as connection:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        trigger_sql = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                            "AND name = 'trg_verification_receipts_no_update'"
                        ).fetchone()[0]
                        connection.execute(
                            "DROP TRIGGER trg_verification_receipts_no_update"
                        )
                        connection.execute(
                            "UPDATE verification_receipts "
                            "SET task_id = CAST(? AS BLOB) "
                            "WHERE verification_receipt_id = ?",
                            (task_id, receipt_id),
                        )
                        connection.execute(trigger_sql)
                        connection.commit()
                        before = self._assert_direct_verification_inventory_fails(
                            connection=connection,
                            db=db,
                            project_id=target.project.project_id,
                            task_id=task_id,
                        )

                    if source_schema_version == 18:
                        self._assert_verification_inventory_operations_fail(
                            db=db,
                            repo=repo,
                            task_id=task_id,
                            before=before,
                        )

    def test_verification_inventory_rejects_old_native_reference_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in ("missing", "cross-owned"):
                with self.subTest(case=case):
                    root = Path(temp) / case
                    root.mkdir()
                    repo, db = initialize(root)
                    task = add_task(db, repo)
                    task_id = task["task_id"]
                    for generation_seed in range(1, 13):
                        generation = set_target(
                            db,
                            repo,
                            task_id,
                            fingerprint=(
                                "sha256:" + f"{generation_seed:064x}"
                            ),
                        )
                        recorded = add_receipt(
                            db,
                            repo,
                            task_id,
                            generation,
                        )
                        self.assertEqual(
                            recorded.returncode,
                            0,
                            recorded.stdout,
                        )

                    shown_before = run_taskgov(
                        "task",
                        "show",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task_id,
                        "--read-only",
                        "--json",
                    )
                    self.assertEqual(
                        shown_before.returncode,
                        0,
                        shown_before.stdout,
                    )
                    evidence_before = json_payload(shown_before)["data"][
                        "verification_evidence"
                    ]
                    self.assertEqual(
                        evidence_before["counts"]["receipts_total"],
                        12,
                    )
                    self.assertEqual(
                        len(evidence_before["recent_receipts"]),
                        10,
                    )
                    if case == "missing":
                        with closing(connect(db)) as connection:
                            task_row = connection.execute(
                                "SELECT * FROM tasks WHERE task_id = ?",
                                (task_id,),
                            ).fetchone()
                            real_validator = (
                                storage_module.validate_selected_task_receipt_evidence
                            )
                            with (
                                mock.patch.object(
                                    storage_module,
                                    "COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE",
                                    5,
                                ),
                                mock.patch.object(
                                    storage_module,
                                    "validate_selected_task_receipt_evidence",
                                    wraps=real_validator,
                                ) as selected_validator,
                            ):
                                snapshot = read_verification_receipt_snapshot(
                                    connection,
                                    project_id=task_row["project_id"],
                                    task_id=task_id,
                                    contract_revision=task_row[
                                        "current_contract_revision"
                                    ],
                                    verification_expectation_digest=(
                                        verification_expectation_digest(
                                            task_row["verification"]
                                        )
                                    ),
                                    target_kind=task_row[
                                        "review_target_kind"
                                    ],
                                    target_value=task_row[
                                        "review_target_value"
                                    ],
                                    target_base_revision=task_row[
                                        "review_target_base_revision"
                                    ],
                                    target_generation=task_row[
                                        "review_target_generation"
                                    ],
                                )
                            self.assertEqual(snapshot.total, 12)
                            self.assertEqual(len(snapshot.recent), 10)
                            self.assertEqual(
                                [
                                    len(
                                        call.kwargs[
                                            "verification_receipt_ids"
                                        ]
                                    )
                                    for call in selected_validator.call_args_list
                                ],
                                [5, 5, 2, 5, 5, 2],
                            )

                    other_task = None
                    if case == "cross-owned":
                        other_task = add_task(
                            db,
                            repo,
                            title="Other Verification Reference owner",
                        )
                        set_target(
                            db,
                            repo,
                            other_task["task_id"],
                            fingerprint="sha256:" + "f" * 64,
                        )

                    with closing(connect(db)) as connection:
                        old_receipt = connection.execute(
                            """
                            SELECT * FROM verification_receipts
                             WHERE task_id = ?
                               AND verification_receipt_id NOT IN (
                                 SELECT verification_receipt_id
                                   FROM verification_receipts
                                  WHERE task_id = ?
                                  ORDER BY created_at DESC,
                                           verification_receipt_id DESC
                                  LIMIT 10
                               )
                             ORDER BY target_generation
                             LIMIT 1
                            """,
                            (task_id, task_id),
                        ).fetchone()
                        self.assertIsNotNone(old_receipt)
                        reference = dict(
                            connection.execute(
                                "SELECT * FROM evidence_references "
                                "WHERE source_kind = 'verification_receipt' "
                                "AND source_id = ?",
                                (old_receipt["verification_receipt_id"],),
                            ).fetchone()
                        )
                        if case == "missing":
                            trigger_sql = connection.execute(
                                "SELECT sql FROM sqlite_master "
                                "WHERE type = 'trigger' AND name = "
                                "'trg_evidence_references_no_delete'"
                            ).fetchone()[0]
                            connection.execute(
                                "DROP TRIGGER trg_evidence_references_no_delete"
                            )
                            connection.execute(
                                "DELETE FROM evidence_references "
                                "WHERE evidence_reference_id = ?",
                                (reference["evidence_reference_id"],),
                            )
                            connection.execute(trigger_sql)
                            sensitive_value = reference[
                                "evidence_reference_id"
                            ]
                        else:
                            assert other_task is not None
                            other = connection.execute(
                                "SELECT * FROM tasks WHERE task_id = ?",
                                (other_task["task_id"],),
                            ).fetchone()
                            binding = TargetCaptureBinding(
                                target_kind=other["review_target_kind"],
                                target_value=other["review_target_value"],
                                target_base_revision=other[
                                    "review_target_base_revision"
                                ],
                                target_generation=other[
                                    "review_target_generation"
                                ],
                                authority_snapshot_id=other[
                                    "review_target_authority_snapshot_id"
                                ],
                                acceptance_criterion_id=other[
                                    "review_target_acceptance_criterion_id"
                                ],
                                verification_criterion_id=other[
                                    "review_target_verification_criterion_id"
                                ],
                            )
                            source = EvidenceSource(
                                source_kind="verification_receipt",
                                source_state="recorded",
                                source_id=old_receipt[
                                    "verification_receipt_id"
                                ],
                                source_projection={
                                    "verification_receipt_id": old_receipt[
                                        "verification_receipt_id"
                                    ],
                                    "subject_basis_version": old_receipt[
                                        "verification_subject_basis_version"
                                    ],
                                    "authority_snapshot_id": (
                                        binding.authority_snapshot_id
                                    ),
                                    "verification_criterion_id": (
                                        binding.verification_criterion_id
                                    ),
                                    "result": old_receipt["result"],
                                    "duration_ms": old_receipt["duration_ms"],
                                    "scope_coverage": old_receipt[
                                        "scope_coverage"
                                    ],
                                    "created_at": old_receipt["created_at"],
                                },
                            )
                            rebuilt = build_evidence_reference(
                                source=source,
                                project_id=other["project_id"],
                                task_id=other["task_id"],
                                contract_revision=other[
                                    "current_contract_revision"
                                ],
                                binding=binding,
                            )
                            duplicate = dict(reference)
                            duplicate.update(
                                evidence_reference_id=(
                                    "tg_evidence_reference_00000000000000cc"
                                ),
                                project_id=other["project_id"],
                                task_id=other["task_id"],
                                contract_revision=other[
                                    "current_contract_revision"
                                ],
                                authority_snapshot_id=(
                                    binding.authority_snapshot_id
                                ),
                                acceptance_criterion_id=(
                                    binding.acceptance_criterion_id
                                ),
                                verification_criterion_id=(
                                    binding.verification_criterion_id
                                ),
                                target_kind=binding.target_kind,
                                target_value=binding.target_value,
                                target_base_revision=(
                                    binding.target_base_revision
                                ),
                                target_generation=binding.target_generation,
                                digest=rebuilt.digest,
                            )
                            fields = tuple(duplicate)
                            connection.execute(
                                f"INSERT INTO evidence_references"
                                f"({', '.join(fields)}) VALUES "
                                f"({', '.join('?' for _ in fields)})",
                                tuple(duplicate[field] for field in fields),
                            )
                            sensitive_value = duplicate[
                                "evidence_reference_id"
                            ]
                        connection.commit()

                    before = db.read_bytes()
                    self._assert_verification_inventory_operations_fail(
                        db=db,
                        repo=repo,
                        task_id=task_id,
                        before=before,
                        redacted_values=(sensitive_value,),
                        expected_error=(
                            {
                                "code": "project_state_unreadable",
                                "message": "project state could not be read safely",
                            }
                            if case == "cross-owned"
                            else None
                        ),
                    )

    def test_verification_inventory_rejects_malformed_reference_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in (
                "oversized-source-id",
                "blob-task-id",
                "blob-source-kind",
            ):
                with self.subTest(case=case):
                    root = Path(temp) / case
                    root.mkdir()
                    repo, db = initialize(root)
                    task = add_task(db, repo)
                    task_id = task["task_id"]
                    generation = set_target(db, repo, task_id)
                    recorded = add_receipt(db, repo, task_id, generation)
                    self.assertEqual(recorded.returncode, 0, recorded.stdout)

                    marker = "malformed-verification-reference-source"
                    with closing(connect(db)) as connection:
                        reference = dict(
                            connection.execute(
                                "SELECT * FROM evidence_references "
                                "WHERE source_kind = 'verification_receipt' "
                                "AND task_id = ?",
                                (task_id,),
                            ).fetchone()
                        )
                        reference["evidence_reference_id"] = (
                            "tg_evidence_reference_00000000000000dd"
                        )
                        if case == "oversized-source-id":
                            reference["source_id"] = marker + "x" * 10_000
                        elif case == "blob-task-id":
                            connection.execute("PRAGMA foreign_keys = OFF")
                            reference["task_id"] = sqlite3.Binary(
                                task_id.encode("utf-8")
                            )
                        else:
                            connection.execute(
                                "PRAGMA ignore_check_constraints = ON"
                            )
                            reference["source_kind"] = sqlite3.Binary(
                                b"verification_receipt"
                            )
                        fields = tuple(reference)
                        connection.execute(
                            f"INSERT INTO evidence_references"
                            f"({', '.join(fields)}) VALUES "
                            f"({', '.join('?' for _ in fields)})",
                            tuple(reference[field] for field in fields),
                        )
                        connection.commit()

                    before = db.read_bytes()
                    self._assert_verification_inventory_operations_fail(
                        db=db,
                        repo=repo,
                        task_id=task_id,
                        before=before,
                        redacted_values=(
                            marker,
                            "tg_evidence_reference_00000000000000dd",
                        ),
                        expected_error={
                            "code": "project_state_unreadable",
                            "message": "project state could not be read safely",
                        },
                    )

    def test_task_review_inventory_rejects_historical_receipt_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo, review_tier=1)
            for index in range(12):
                seed_current_review_evidence(
                    db,
                    repo,
                    task["task_id"],
                    fingerprint="sha256:" + f"{index:064x}",
                )

            database_target = target_for(db, repo)
            private_value = "Authorization: Bearer historical-review-secret"
            with closing(connect_initialized(database_target)) as connection:
                receipt = connection.execute(
                    "SELECT * FROM review_receipts WHERE task_id = ? "
                    "ORDER BY rowid LIMIT 1",
                    (task["task_id"],),
                ).fetchone()
                stored = read_review_receipt_with_provenance(
                    connection,
                    review_receipt_id=receipt["review_receipt_id"],
                )
                connection.execute(
                    "UPDATE review_receipts SET summary = ? "
                    "WHERE review_receipt_id = ?",
                    (private_value, receipt["review_receipt_id"]),
                )
                rewrite_review_reference_digest(
                    connection,
                    source_kind="review_receipt",
                    source_id=receipt["review_receipt_id"],
                    review_provenance=stored["provenance"],
                )
                connection.commit()
                task_row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                before = db.read_bytes()
                with self.assertRaises(StorageError) as direct_failure:
                    read_review_evidence(
                        connection,
                        database_target.project.project_id,
                        task["task_id"],
                        validated_task=task_row,
                    )
                self.assertEqual(
                    (
                        direct_failure.exception.code,
                        direct_failure.exception.message,
                    ),
                    (
                        "evidence_ledger_inconsistent",
                        "stored evidence ledger is inconsistent",
                    ),
                )
                self.assertEqual(db.read_bytes(), before)

            shown = run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--read-only",
                "--json",
            )
            self.assertEqual(shown.returncode, 2, shown.stdout)
            self.assertEqual(
                json_payload(shown)["errors"][0],
                {
                    "code": "evidence_ledger_inconsistent",
                    "message": "stored evidence ledger is inconsistent",
                },
            )
            self.assertNotIn(private_value, shown.stdout)
            self.assertEqual(db.read_bytes(), before)

    def test_legacy_review_inventory_rejects_historical_count_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in ("receipt", "finding"):
                with self.subTest(case=case):
                    root = Path(temp) / case
                    target, task_id = initialize_v17_fixture(root)
                    with closing(connect(target.db_path)) as connection:
                        task = connection.execute(
                            "SELECT project_id FROM tasks WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()
                        for index in range(12):
                            if index > 0:
                                seed_review_evidence_connection(
                                    connection,
                                    task_id,
                                    target_value=(
                                        "sha256:" + f"{index:064x}"
                                    ),
                                )
                            receipt_id = connection.execute(
                                "SELECT review_receipt_id FROM review_receipts "
                                "WHERE task_id = ? ORDER BY rowid DESC LIMIT 1",
                                (task_id,),
                            ).fetchone()[0]
                            connection.execute(
                                """
                                INSERT INTO review_findings(
                                  review_finding_id, review_receipt_id,
                                  severity, status, summary,
                                  resolution_summary, created_at, resolved_at
                                ) VALUES (?, ?, 'low', 'open', ?, '', ?, NULL)
                                """,
                                (
                                    f"tg_review_finding_{index + 1:016x}",
                                    receipt_id,
                                    f"Historical low finding {index}",
                                    f"2026-08-04T00:{index:02}:00Z",
                                ),
                            )
                        if case == "receipt":
                            private_value = "token=legacy-receipt-secret"
                            connection.execute(
                                "UPDATE review_receipts SET summary = ? "
                                "WHERE rowid = (SELECT MIN(rowid) "
                                "FROM review_receipts WHERE task_id = ?)",
                                (private_value, task_id),
                            )
                        else:
                            private_value = (
                                "Authorization: Bearer legacy-finding-secret"
                            )
                            connection.execute(
                                "UPDATE review_findings SET summary = ? "
                                "WHERE rowid = (SELECT MIN(rowid) "
                                "FROM review_findings)",
                                (private_value,),
                            )
                        connection.commit()
                        before = target.db_path.read_bytes()
                        with self.assertRaises(StorageError) as failure:
                            read_review_evidence(
                                connection,
                                task["project_id"],
                                task_id,
                                source_schema_version=17,
                            )
                        self.assertEqual(
                            (failure.exception.code, failure.exception.message),
                            (
                                "evidence_ledger_inconsistent",
                                "stored evidence ledger is inconsistent",
                            ),
                        )
                        self.assertNotIn(private_value, str(failure.exception))
                        self.assertEqual(target.db_path.read_bytes(), before)

    def test_review_inventory_rejects_legacy_receipt_ownership_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            for source_schema_version in (17, 18):
                with self.subTest(source_schema_version=source_schema_version):
                    target, task_id = initialize_v17_fixture(
                        Path(temp) / f"schema-{source_schema_version}"
                    )
                    repo = target.project.canonical_repo
                    with closing(connect(target.db_path)) as connection:
                        for index in range(1, 12):
                            seed_review_evidence_connection(
                                connection,
                                task_id,
                                target_value="sha256:" + f"{index:064x}",
                            )
                        legacy_receipt_id = connection.execute(
                            "SELECT review_receipt_id FROM review_receipts "
                            "WHERE task_id = ? ORDER BY rowid LIMIT 1",
                            (task_id,),
                        ).fetchone()[0]
                        connection.execute(
                            """
                            INSERT INTO review_findings(
                              review_finding_id, review_receipt_id,
                              severity, status, summary,
                              resolution_summary, created_at, resolved_at
                            ) VALUES (?, ?, 'medium', 'open', ?, '', ?, NULL)
                            """,
                            (
                                "tg_review_finding_00000000000000aa",
                                legacy_receipt_id,
                                "Historical ownership blocker",
                                "2026-08-04T00:00:00Z",
                            ),
                        )
                        connection.commit()
                        if source_schema_version == 18:
                            apply_evidence_ledger_capture_migration(connection)
                            apply_completion_evidence_bundle_migration(connection)
                            self.assertEqual(
                                apply_migrations(connection),
                                ([20, 21], []),
                            )

                    with closing(connect(target.db_path)) as connection:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        connection.execute(
                            "UPDATE review_receipts SET project_id = ? "
                            "WHERE review_receipt_id = ?",
                            ("corrupt-project-owner", legacy_receipt_id),
                        )
                        connection.commit()
                        task = connection.execute(
                            "SELECT * FROM tasks WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()
                        before = target.db_path.read_bytes()
                        with self.assertRaises(StorageError) as direct_failure:
                            read_review_evidence(
                                connection,
                                target.project.project_id,
                                task_id,
                                validated_task=task,
                                source_schema_version=source_schema_version,
                            )
                        self.assertEqual(
                            (
                                direct_failure.exception.code,
                                direct_failure.exception.message,
                            ),
                            (
                                "evidence_ledger_inconsistent",
                                "stored evidence ledger is inconsistent",
                            ),
                        )
                        self.assertNotIn(
                            "corrupt-project-owner",
                            str(direct_failure.exception),
                        )
                        self.assertEqual(target.db_path.read_bytes(), before)

                    if source_schema_version == 18:
                        shown = run_taskgov(
                            "task",
                            "show",
                            "--repo",
                            str(repo),
                            "--db",
                            str(target.db_path),
                            task_id,
                            "--read-only",
                            "--json",
                        )
                        self.assertEqual(shown.returncode, 2, shown.stdout)
                        self.assertEqual(
                            json_payload(shown)["errors"][0]["code"],
                            "evidence_ledger_inconsistent",
                        )
                        self.assertNotIn(
                            "corrupt-project-owner",
                            shown.stdout,
                        )
                        self.assertEqual(target.db_path.read_bytes(), before)

    def test_selected_task_rejects_current_manifest_corruption_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in (
                "digest",
                "extra-entry",
                "blob-entry-manifest-id",
                "missing-reference",
            ):
                with self.subTest(case=case):
                    root = Path(temp) / case
                    root.mkdir()
                    repo, db = initialize(root)
                    task = add_task(db, repo, verification="")
                    set_target(db, repo, task["task_id"])
                    with closing(connect(db)) as connection:
                        manifest = connection.execute(
                            "SELECT * FROM artifact_manifests WHERE task_id = ?",
                            (task["task_id"],),
                        ).fetchone()
                        marker = manifest["artifact_manifest_id"]
                        if case == "digest":
                            trigger_sql = connection.execute(
                                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                                "AND name = 'trg_artifact_manifests_no_update'"
                            ).fetchone()[0]
                            connection.execute(
                                "DROP TRIGGER trg_artifact_manifests_no_update"
                            )
                            marker = "sha256:" + "f" * 64
                            connection.execute(
                                "UPDATE artifact_manifests SET digest = ? "
                                "WHERE artifact_manifest_id = ?",
                                (marker, manifest["artifact_manifest_id"]),
                            )
                            connection.execute(trigger_sql)
                        elif case in {"extra-entry", "blob-entry-manifest-id"}:
                            artifact_manifest_id = manifest["artifact_manifest_id"]
                            if case == "blob-entry-manifest-id":
                                connection.execute("PRAGMA foreign_keys = OFF")
                                artifact_manifest_id = sqlite3.Binary(
                                    artifact_manifest_id.encode("utf-8")
                                )
                            connection.execute(
                                """
                                INSERT INTO artifact_manifest_entries(
                                  project_id, task_id, artifact_manifest_id,
                                  ordinal, entry_kind, old_path, new_path,
                                  before_mode, before_object_id,
                                  after_mode, after_object_id
                                ) VALUES (?, ?, ?, 0, 'add', NULL, ?, NULL, NULL,
                                          '100644', ?)
                                """,
                                (
                                    manifest["project_id"],
                                    manifest["task_id"],
                                    artifact_manifest_id,
                                    "manifest-entry-marker.txt",
                                    "0" * 40,
                                ),
                            )
                            marker = "manifest-entry-marker.txt"
                        else:
                            reference = connection.execute(
                                "SELECT * FROM evidence_references "
                                "WHERE source_kind = 'artifact_manifest' "
                                "AND source_id = ?",
                                (manifest["artifact_manifest_id"],),
                            ).fetchone()
                            trigger_sql = connection.execute(
                                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                                "AND name = 'trg_evidence_references_no_delete'"
                            ).fetchone()[0]
                            connection.execute(
                                "DROP TRIGGER trg_evidence_references_no_delete"
                            )
                            connection.execute(
                                "DELETE FROM evidence_references "
                                "WHERE evidence_reference_id = ?",
                                (reference["evidence_reference_id"],),
                            )
                            connection.execute(trigger_sql)
                            marker = reference["evidence_reference_id"]
                        connection.commit()

                    before = db.read_bytes()
                    self._assert_selected_task_authority_operations_fail(
                        db=db,
                        repo=repo,
                        task_id=task["task_id"],
                        before=before,
                        redacted_values=(marker,),
                    )

    def test_selected_task_rejects_all_reference_inventory_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            cases = (
                "reserved-kind",
                "wrong-kind-id",
                "orphan-active-source",
                "blob-task-owner",
                "blob-project-owner",
            )
            for case_index, case in enumerate(cases, start=1):
                with self.subTest(case=case):
                    root = Path(temp) / case
                    root.mkdir()
                    repo, db = initialize(root)
                    task = add_task(db, repo, verification="")
                    set_target(db, repo, task["task_id"])
                    with closing(connect(db)) as connection:
                        reference = dict(
                            connection.execute(
                                "SELECT * FROM evidence_references "
                                "WHERE source_kind = 'artifact_manifest' "
                                "AND task_id = ?",
                                (task["task_id"],),
                            ).fetchone()
                        )
                        reference["evidence_reference_id"] = (
                            "tg_evidence_reference_"
                            + f"{case_index + 0xE0:016x}"
                        )
                        marker = reference["evidence_reference_id"]
                        if case == "reserved-kind":
                            reference["source_kind"] = "derived_analysis"
                            reference["source_state"] = "recorded"
                            reference["source_id"] = "reserved-source-marker"
                            marker = reference["source_id"]
                        elif case == "wrong-kind-id":
                            reference["source_kind"] = "review_receipt"
                            reference["source_state"] = "recorded"
                        elif case == "orphan-active-source":
                            reference["source_kind"] = "review_receipt"
                            reference["source_state"] = "recorded"
                            reference["source_id"] = (
                                "tg_review_receipt_ffffffffffffffff"
                            )
                            marker = reference["source_id"]
                        elif case == "blob-task-owner":
                            connection.execute("PRAGMA foreign_keys = OFF")
                            reference["task_id"] = sqlite3.Binary(
                                task["task_id"].encode("utf-8")
                            )
                        else:
                            connection.execute("PRAGMA foreign_keys = OFF")
                            reference["project_id"] = sqlite3.Binary(
                                reference["project_id"].encode("utf-8")
                            )
                        fields = tuple(reference)
                        connection.execute(
                            f"INSERT INTO evidence_references({', '.join(fields)}) "
                            f"VALUES ({', '.join('?' for _ in fields)})",
                            tuple(reference[field] for field in fields),
                        )
                        connection.commit()

                    before = db.read_bytes()
                    self._assert_selected_task_authority_operations_fail(
                        db=db,
                        repo=repo,
                        task_id=task["task_id"],
                        before=before,
                        redacted_values=(marker,),
                    )

    def test_selected_task_rejects_single_wrong_owner_source(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            selected_task = add_task(
                db,
                repo,
                title="Selected Reference owner",
                verification="",
            )
            source_task = add_task(
                db,
                repo,
                title="Stored source owner",
                verification="",
            )
            set_target(db, repo, selected_task["task_id"])
            set_target(db, repo, source_task["task_id"])
            with closing(connect(db)) as connection:
                source_reference = dict(
                    connection.execute(
                        "SELECT * FROM evidence_references "
                        "WHERE source_kind = 'artifact_manifest' "
                        "AND task_id = ?",
                        (source_task["task_id"],),
                    ).fetchone()
                )
                trigger_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = 'trg_evidence_references_no_delete'"
                ).fetchone()[0]
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TRIGGER trg_evidence_references_no_delete")
                connection.execute(
                    "DELETE FROM evidence_references "
                    "WHERE evidence_reference_id = ?",
                    (source_reference["evidence_reference_id"],),
                )
                connection.execute(trigger_sql)
                source_reference["evidence_reference_id"] = (
                    "tg_evidence_reference_00000000000000ef"
                )
                source_reference["task_id"] = selected_task["task_id"]
                fields = tuple(source_reference)
                connection.execute(
                    f"INSERT INTO evidence_references({', '.join(fields)}) "
                    f"VALUES ({', '.join('?' for _ in fields)})",
                    tuple(source_reference[field] for field in fields),
                )
                connection.commit()

            before = db.read_bytes()
            self._assert_selected_task_authority_operations_fail(
                db=db,
                repo=repo,
                task_id=selected_task["task_id"],
                before=before,
                redacted_values=(source_reference["evidence_reference_id"],),
            )

    def test_selected_reference_inventory_is_one_owner_indexed_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            tasks = [
                add_task(db, repo, title=f"Reference batch {index}", verification="")
                for index in range(2)
            ]
            for task in tasks:
                set_target(db, repo, task["task_id"])
            with closing(connect(db)) as connection:
                task_rows = connection.execute(
                    "SELECT * FROM tasks ORDER BY task_id"
                ).fetchall()
                statements: list[str] = []
                connection.set_trace_callback(statements.append)
                try:
                    tasks_module.validate_current_stored_task_rows(
                        connection,
                        task_rows,
                        expected_project_id=tasks[0]["project_id"],
                    )
                finally:
                    connection.set_trace_callback(None)
                inventory_reads = [
                    statement
                    for statement in statements
                    if (
                        "selected_task_aliases" in statement
                        and "CROSS JOIN evidence_references AS reference" in statement
                    )
                ]
                self.assertEqual(len(inventory_reads), 1)

                selected_json = "[" + ",".join(
                    f'"{task["task_id"]}"' for task in tasks
                ) + "]"
                plan_rows = connection.execute(
                    "EXPLAIN QUERY PLAN "
                    + storage_module._SELECTED_TASK_EVIDENCE_REFERENCE_INVENTORY_SQL,
                    (
                        selected_json,
                        tasks[0]["project_id"],
                        tasks[0]["project_id"],
                    ),
                ).fetchall()
                plan = "\n".join(str(row[3]) for row in plan_rows)
                self.assertIn("idx_evidence_references_source", plan)
                self.assertIn("SEARCH reference", plan)
                self.assertNotIn("USE TEMP B-TREE", plan)

    def test_selected_reader_rejects_cross_owned_duplicate_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            source_task = add_task(
                db,
                repo,
                review_tier=1,
                verification="",
            )
            other_task = add_task(
                db,
                repo,
                review_tier=1,
                verification="",
            )
            seed_current_review_evidence(db, repo, source_task["task_id"])
            seed_current_review_evidence(db, repo, other_task["task_id"])
            with closing(connect(db)) as connection:
                receipt = connection.execute(
                    "SELECT * FROM review_receipts WHERE task_id = ?",
                    (source_task["task_id"],),
                ).fetchone()
                stored = read_review_receipt_with_provenance(
                    connection,
                    review_receipt_id=receipt["review_receipt_id"],
                )
                reference = dict(
                    connection.execute(
                        "SELECT * FROM evidence_references "
                        "WHERE source_kind = 'review_receipt' "
                        "AND source_id = ?",
                        (receipt["review_receipt_id"],),
                    ).fetchone()
                )
                other = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (other_task["task_id"],),
                ).fetchone()
                source = EvidenceSource(
                    source_kind="review_receipt",
                    source_state="recorded",
                    source_id=receipt["review_receipt_id"],
                    source_projection={
                        "review_receipt_id": receipt["review_receipt_id"],
                        "reviewer_key": receipt["reviewer_key"],
                        "receipt_kind": receipt["receipt_kind"],
                        "verdict": receipt["verdict"],
                        "summary": receipt["summary"],
                        "user_approved": receipt["user_approved"],
                        "created_at": receipt["created_at"],
                        "review_provenance": stored["provenance"],
                    },
                )
                binding = TargetCaptureBinding(
                    target_kind=other["review_target_kind"],
                    target_value=other["review_target_value"],
                    target_base_revision=other[
                        "review_target_base_revision"
                    ],
                    target_generation=other["review_target_generation"],
                    authority_snapshot_id=other[
                        "review_target_authority_snapshot_id"
                    ],
                    acceptance_criterion_id=other[
                        "review_target_acceptance_criterion_id"
                    ],
                    verification_criterion_id=other[
                        "review_target_verification_criterion_id"
                    ],
                )
                rebuilt = build_evidence_reference(
                    source=source,
                    project_id=reference["project_id"],
                    task_id=other_task["task_id"],
                    contract_revision=other["current_contract_revision"],
                    binding=binding,
                )
                reference.update(
                    evidence_reference_id=(
                        "tg_evidence_reference_00000000000000bb"
                    ),
                    task_id=other_task["task_id"],
                    contract_revision=other["current_contract_revision"],
                    authority_snapshot_id=binding.authority_snapshot_id,
                    acceptance_criterion_id=binding.acceptance_criterion_id,
                    verification_criterion_id=(
                        binding.verification_criterion_id
                    ),
                    target_kind=binding.target_kind,
                    target_value=binding.target_value,
                    target_base_revision=binding.target_base_revision,
                    target_generation=binding.target_generation,
                    digest=rebuilt.digest,
                )
                fields = tuple(reference)
                connection.execute(
                    f"INSERT INTO evidence_references({', '.join(fields)}) "
                    f"VALUES ({', '.join('?' for _ in fields)})",
                    tuple(reference[field] for field in fields),
                )
                connection.commit()

            before = db.read_bytes()
            operations = (
                run_taskgov(
                    "task",
                    "show",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    source_task["task_id"],
                    "--read-only",
                    "--json",
                ),
                completion(db, repo, source_task["task_id"], check=True),
                completion(db, repo, source_task["task_id"]),
            )
            for operation in operations:
                self.assertEqual(operation.returncode, 2, operation.stdout)
                self.assertEqual(
                    json_payload(operation)["errors"][0]["code"],
                    "project_state_unreadable",
                )
                self.assertEqual(db.read_bytes(), before)

    def test_historical_fallback_uses_manifest_snapshot_tier_not_current_tier(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo, review_tier=1)
            generation = set_target(db, repo, task["task_id"])
            reviewed = run_taskgov(
                "review",
                "receipt",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--reviewer",
                "historical-tier-one-fallback",
                "--kind",
                "self_review_fallback",
                "--verdict",
                "pass",
                "--summary",
                "Tier one fallback remains valid history",
                "--reviewer-class",
                "human",
                "--model-state",
                "not_applicable",
                "--skill-state",
                "not_applicable",
                "--context-relation",
                "same_context",
                "--json",
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stdout)
            receipt_id = json_payload(reviewed)["data"]["receipt"][
                "review_receipt_id"
            ]
            raised = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--review-tier",
                "2",
                "--review-tier-change-reason",
                "Raise the current review gate",
                "--json",
            )
            self.assertEqual(raised.returncode, 0, raised.stdout)

            shown = run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--read-only",
                "--json",
            )
            self.assertEqual(shown.returncode, 0, shown.stdout)
            with closing(connect(db)) as connection:
                project_id = connection.execute(
                    "SELECT project_id FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]
                validate_selected_task_receipt_evidence(
                    connection,
                    project_id=project_id,
                    task_id=task["task_id"],
                    review_receipt_ids={receipt_id},
                    review_finding_ids=set(),
                    verification_receipt_ids=set(),
                )
                validate_evidence_ledger_storage(connection)
                old_snapshot_tier = connection.execute(
                    "SELECT snapshot.review_tier "
                    "FROM artifact_manifests AS manifest "
                    "JOIN authority_snapshots AS snapshot "
                    "ON snapshot.authority_snapshot_id = manifest.authority_snapshot_id "
                    "WHERE manifest.task_id = ? AND manifest.target_generation = ?",
                    (task["task_id"], generation),
                ).fetchone()[0]
                self.assertEqual(old_snapshot_tier, 1)

    def test_native_fallback_approval_uses_manifest_snapshot_tier(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo, review_tier=1)
            set_target(db, repo, task["task_id"])
            reviewed = run_taskgov(
                "review",
                "receipt",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--reviewer",
                "tier-one-fallback",
                "--kind",
                "self_review_fallback",
                "--verdict",
                "pass",
                "--summary",
                "Tier one fallback approval is false",
                "--reviewer-class",
                "human",
                "--model-state",
                "not_applicable",
                "--skill-state",
                "not_applicable",
                "--context-relation",
                "same_context",
                "--json",
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stdout)
            receipt_id = json_payload(reviewed)["data"]["receipt"][
                "review_receipt_id"
            ]
            with closing(connect(db)) as connection:
                stored = read_review_receipt_with_provenance(
                    connection,
                    review_receipt_id=receipt_id,
                )
                connection.execute(
                    "UPDATE review_receipts SET user_approved = 1 "
                    "WHERE review_receipt_id = ?",
                    (receipt_id,),
                )
                rewrite_review_reference_digest(
                    connection,
                    source_kind="review_receipt",
                    source_id=receipt_id,
                    review_provenance=stored["provenance"],
                )
                connection.commit()
                project_id = connection.execute(
                    "SELECT project_id FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]
                with self.assertRaises(StorageError) as selected_failure:
                    validate_selected_task_receipt_evidence(
                        connection,
                        project_id=project_id,
                        task_id=task["task_id"],
                        review_receipt_ids={receipt_id},
                        review_finding_ids=set(),
                        verification_receipt_ids=set(),
                    )
                self.assertEqual(
                    selected_failure.exception.code,
                    "evidence_ledger_inconsistent",
                )
                with self.assertRaises(StorageError) as full_failure:
                    validate_evidence_ledger_storage(connection)
                self.assertEqual(
                    full_failure.exception.code,
                    "evidence_ledger_inconsistent",
                )

    def test_legacy_review_projection_revalidates_text_and_v5_base_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            for case in ("receipt-private", "receipt-long", "finding-private"):
                with self.subTest(case=case):
                    root = Path(temp) / case
                    root.mkdir()
                    target, task_id = initialize_v17_fixture(root)
                    with closing(connect(target.db_path)) as connection:
                        task = connection.execute(
                            "SELECT project_id FROM tasks WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()
                        receipt = connection.execute(
                            "SELECT * FROM review_receipts WHERE task_id = ? "
                            "ORDER BY review_receipt_id LIMIT 1",
                            (task_id,),
                        ).fetchone()
                        if case == "receipt-private":
                            connection.execute(
                                "UPDATE review_receipts SET summary = ? "
                                "WHERE review_receipt_id = ?",
                                ("token=legacy-review-secret", receipt["review_receipt_id"]),
                            )
                        elif case == "receipt-long":
                            connection.execute(
                                "UPDATE review_receipts SET reviewer_key = ? "
                                "WHERE review_receipt_id = ?",
                                ("l" * 501, receipt["review_receipt_id"]),
                            )
                        else:
                            connection.execute(
                                """
                                INSERT INTO review_findings(
                                  review_finding_id, review_receipt_id,
                                  severity, status, summary,
                                  resolution_summary, created_at, resolved_at
                                ) VALUES (?, ?, 'low', 'open', ?, '', ?, NULL)
                                """,
                                (
                                    "tg_review_finding_0000000000000001",
                                    receipt["review_receipt_id"],
                                    "Authorization: Bearer legacy-finding-secret",
                                    "2026-08-04T00:00:00Z",
                                ),
                            )
                        connection.commit()
                        with self.assertRaises(StorageError) as failure:
                            read_review_evidence(
                                connection,
                                task["project_id"],
                                task_id,
                                source_schema_version=17,
                            )
                        self.assertEqual(
                            (failure.exception.code, failure.exception.message),
                            (
                                "evidence_ledger_inconsistent",
                                "stored evidence ledger is inconsistent",
                            ),
                        )

            root = Path(temp) / "v5-base"
            root.mkdir()
            target, task_id = initialize_v17_fixture(root)
            with closing(connect(target.db_path)) as connection:
                receipt = dict(connection.execute(
                    "SELECT * FROM review_receipts WHERE task_id = ? "
                    "ORDER BY review_receipt_id LIMIT 1",
                    (task_id,),
                ).fetchone())
                receipt.pop("target_base_revision")
                validate_stored_review_receipt_projection(
                    receipt,
                    source_schema_version=5,
                )

    def test_full_validator_query_count_is_independent_of_manifest_and_receipt_count(self):
        with tempfile.TemporaryDirectory() as temp:
            query_counts = []
            for case, task_count in (("small", 1), ("larger", 4)):
                root = Path(temp) / case
                root.mkdir()
                repo, db = initialize(root)
                for index in range(task_count):
                    task = add_task(
                        db,
                        repo,
                        title=f"Query-bound Task {index}",
                        review_tier=1,
                    )
                    seed_current_review_evidence(db, repo, task["task_id"])
                with closing(connect(db)) as connection:
                    statements: list[str] = []
                    connection.set_trace_callback(statements.append)
                    try:
                        validate_evidence_ledger_storage(connection)
                    finally:
                        connection.set_trace_callback(None)
                reads = [
                    statement
                    for statement in statements
                    if statement.lstrip().upper().startswith(("SELECT", "PRAGMA"))
                ]
                query_counts.append(len(reads))
                self.assertEqual(
                    sum(
                        "FROM artifact_manifest_entries" in statement
                        for statement in reads
                    ),
                    1,
                )
                self.assertEqual(
                    sum(
                        "FROM review_receipt_provenance\n" in statement
                        for statement in reads
                    ),
                    1,
                )
                self.assertEqual(
                    sum(
                        "FROM review_receipt_provenance_codes" in statement
                        for statement in reads
                    ),
                    1,
                )
            self.assertEqual(query_counts[0], query_counts[1])

    def test_v17_validation_is_locked_and_uses_one_exact_contract_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            target, _ = initialize_v17_fixture(Path(temp))
            with closing(connect(target.db_path)) as connection:
                statements: list[str] = []
                connection.set_trace_callback(statements.append)
                try:
                    apply_evidence_ledger_capture_migration(connection)
                finally:
                    connection.set_trace_callback(None)
            begin_index = next(
                index
                for index, statement in enumerate(statements)
                if statement == "BEGIN IMMEDIATE"
            )
            task_read_index = next(
                index
                for index, statement in enumerate(statements)
                if "SELECT * FROM tasks ORDER BY task_id" in statement
            )
            ddl_index = next(
                index
                for index, statement in enumerate(statements)
                if statement.lstrip().upper().startswith("CREATE TABLE AUTHORITY_SNAPSHOTS")
            )
            self.assertLess(begin_index, task_read_index)
            self.assertLess(task_read_index, ddl_index)
            contract_batches = [
                statement
                for statement in statements[:ddl_index]
                if (
                    "FROM task_contract_revisions" in statement
                    and "selected_storage_keys" in statement
                )
            ]
            self.assertEqual(len(contract_batches), 1)
            self.assertNotIn("MAX(revision)", contract_batches[0])

    def test_v17_source_cannot_change_after_validation_before_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            target, task_id = initialize_v17_fixture(Path(temp))
            real_projection = storage_module._selected_table_projection_snapshot
            attempted = False

            def projection_with_competing_write(*args, **kwargs):
                nonlocal attempted
                if not attempted:
                    attempted = True
                    competing = sqlite3.connect(target.db_path, timeout=0.0)
                    try:
                        with self.assertRaises(sqlite3.OperationalError):
                            competing.execute(
                                "UPDATE tasks SET verification = ? WHERE task_id = ?",
                                ("x" * 600, task_id),
                            )
                            competing.commit()
                    finally:
                        competing.close()
                return real_projection(*args, **kwargs)

            with closing(connect(target.db_path)) as connection:
                with mock.patch.object(
                    storage_module,
                    "_selected_table_projection_snapshot",
                    side_effect=projection_with_competing_write,
                ):
                    apply_evidence_ledger_capture_migration(connection)
                self.assertTrue(attempted)
                self.assertLessEqual(
                    len(
                        connection.execute(
                            "SELECT verification FROM tasks WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0]
                    ),
                    500,
                )

    def test_v17_preflight_rejects_non_integer_higher_contract_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            target, task_id = initialize_v17_fixture(Path(temp))
            with closing(connect(target.db_path)) as connection:
                project_id = connection.execute(
                    "SELECT project_id FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO task_contract_revisions(
                      contract_revision_id, task_id, project_id, revision,
                      scope, acceptance, constraints_text, authority_ref,
                      change_reason, created_at
                    ) VALUES (?, ?, ?, 1, 'Scope', 'Acceptance', '', '', '', ?)
                    """,
                    (
                        "tg_contract_0000000000000001",
                        task_id,
                        project_id,
                        "2026-08-04T00:00:00Z",
                    ),
                )
                connection.execute(
                    "UPDATE tasks SET current_contract_revision = 1 WHERE task_id = ?",
                    (task_id,),
                )
                connection.execute(
                    """
                    INSERT INTO task_contract_revisions(
                      contract_revision_id, task_id, project_id, revision,
                      scope, acceptance, constraints_text, authority_ref,
                      change_reason, created_at
                    ) VALUES (?, ?, ?, 'not-an-integer', 'Scope 2',
                              'Acceptance 2', '', '', '', ?)
                    """,
                    (
                        "tg_contract_0000000000000002",
                        task_id,
                        project_id,
                        "2026-08-04T00:01:00Z",
                    ),
                )
                connection.commit()
                before = database_projection(connection)
                with self.assertRaises(StorageError) as failure:
                    apply_evidence_ledger_capture_migration(connection)
                self.assertEqual(failure.exception.code, "project_state_unreadable")
                self.assertEqual(current_schema_version(connection), 17)
                self.assertEqual(database_projection(connection), before)


if __name__ == "__main__":
    unittest.main()
