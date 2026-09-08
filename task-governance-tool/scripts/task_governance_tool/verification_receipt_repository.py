"""Stored Verification Receipt rows, bounded snapshots, and locked append.

Callers retain transactions, gate evaluation, and Evidence Reference creation.
Shared schema/admission and selected validation remain storage-owned.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.sqlite_connection import StorageError


VERIFICATION_RECEIPT_ID_PATTERN = re.compile(
    r"^tg_verification_receipt_[0-9a-f]{16}$"
)

VERIFICATION_COMMAND_OPTION_PATTERN = re.compile(
    r"(?:^|\s)(?:--?[A-Za-z0-9][^\s]*|/(?:c|command)(?:\s|$))",
    re.IGNORECASE,
)

VERIFICATION_SHELL_CONTROL_PATTERN = re.compile(
    r"(?:[&|;<>`]|\$\(|^\.\s+)"
)

VERIFICATION_PATH_COMMAND_PATTERN = re.compile(
    r"^(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|/)"
)

VERIFICATION_SCRIPT_COMMAND_PATTERN = re.compile(
    r"^\S+\.(?:exe|cmd|bat|ps1|py|sh)(?:\s|$)",
    re.IGNORECASE,
)

VERIFICATION_RUNNER_COMMAND_PATTERN = re.compile(
    r"^(?:"
    r"(?:python(?:3(?:\.\d+)*)?|py)(?:\.exe)?(?:\s|$)|"
    r"(?:pytest|unittest|tox|nox|npx)(?:\.exe|\.cmd|\.bat)?(?:\s|$)|"
    r"uv(?:\.exe)?(?:\s|$)|"
    r"(?:npm|pnpm|yarn)(?:\.exe|\.cmd|\.bat)?(?:\s|$)|"
    r"pip(?:3(?:\.\d+)*)?(?:\.exe)?(?:\s|$)|"
    r"(?:node|deno|bun)(?:\.exe|\.cmd|\.bat)?(?:\s|$)|"
    r"(?:poetry|pipenv|pdm|hatch)(?:\.exe|\.cmd|\.bat)?(?:\s|$)|"
    r"cargo(?:\.exe)?(?:\s|$)|"
    r"go(?:\.exe)?(?:\s|$)|"
    r"dotnet(?:\.exe)?(?:\s|$)|"
    r"(?:mvn|gradle|gradlew|make)(?:\.exe|\.cmd|\.bat)?(?:\s|$)|"
    r"(?:msbuild|vstest\.console)(?:\.exe)?(?:\s|$)|"
    r"(?:ruff|mypy|flake8)(?:\.exe)?(?:\s|$)|"
    r"coverage(?:\.exe)?(?:\s|$)|"
    r"git(?:\.exe)?(?:\s|$)|"
    r"(?:powershell|pwsh|cmd|bash|sh)(?:\.exe|\.cmd|\.bat)?(?:\s|$)"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VerificationReceiptSnapshot:
    total: int
    same_generation: tuple[dict[str, Any], ...]
    exact_current: tuple[dict[str, Any], ...]
    recent: tuple[dict[str, Any], ...]


def verification_command_label_is_summary(value: object) -> bool:
    """Reject secrets and obvious executable command/argument syntax."""

    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or value != value.strip()
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
        or VERIFICATION_COMMAND_OPTION_PATTERN.search(value) is not None
        or VERIFICATION_SHELL_CONTROL_PATTERN.search(value) is not None
        or VERIFICATION_PATH_COMMAND_PATTERN.search(value) is not None
        or VERIFICATION_SCRIPT_COMMAND_PATTERN.search(value) is not None
        or VERIFICATION_RUNNER_COMMAND_PATTERN.search(value) is not None
    ):
        return False
    try:
        from task_governance_tool.task_values import TaskValidationError, validate_text

        return (
            validate_text(
                "command_label",
                value,
                required=True,
                limit=200,
            )
            == value
        )
    except (TaskValidationError, TypeError, ValueError):
        return False


def invalid_verification_evidence() -> StorageError:
    from task_governance_tool.storage import (
        StorageError,
    )

    return StorageError(
        "invalid_verification_evidence",
        "stored verification evidence is inconsistent",
    )


def _verification_receipt_int(
    value: object,
    *,
    minimum: int = 0,
) -> int:
    from task_governance_tool.storage import (
        SQLITE_INT64_MAX,
    )

    if type(value) is not int or not minimum <= value <= SQLITE_INT64_MAX:
        raise invalid_verification_evidence()
    return value


def _validate_verification_receipt_row(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Validate one complete stored Receipt and return its fixed storage shape."""
    from task_governance_tool.storage import (
        LOWER_HEX_64_PATTERN,
        StorageError,
        _validate_completion_target,
        validate_utc_timestamp,
    )


    required_fields = (
        "verification_receipt_id",
        "project_id",
        "task_id",
        "contract_revision",
        "verification_expectation_digest",
        "command_label",
        "result",
        "duration_ms",
        "scope_coverage",
        "target_kind",
        "target_value",
        "target_base_revision",
        "target_generation",
        "created_at",
    )
    if any(field_name not in row for field_name in required_fields):
        raise invalid_verification_evidence()
    has_subject = "verification_subject_basis_version" in row
    if has_subject != all(
        name in row
        for name in (
            "verification_subject_basis_version",
            "subject_authority_snapshot_id",
            "subject_verification_criterion_id",
        )
    ):
        raise invalid_verification_evidence()
    receipt_id = row["verification_receipt_id"]
    project_id = row["project_id"]
    task_id = row["task_id"]
    digest = row["verification_expectation_digest"]
    command_label = row["command_label"]
    result = row["result"]
    scope_coverage = row["scope_coverage"]
    target_kind = row["target_kind"]
    target_value = row["target_value"]
    target_base_revision = row["target_base_revision"]
    created_at = row["created_at"]
    if (
        not isinstance(receipt_id, str)
        or VERIFICATION_RECEIPT_ID_PATTERN.fullmatch(receipt_id) is None
        or not isinstance(project_id, str)
        or not project_id
        or not isinstance(task_id, str)
        or not task_id
        or not isinstance(digest, str)
        or LOWER_HEX_64_PATTERN.fullmatch(digest) is None
        or not isinstance(command_label, str)
        or not 1 <= len(command_label) <= 200
        or command_label != command_label.strip()
        or not verification_command_label_is_summary(command_label)
        or result not in {"pass", "fail", "timeout"}
        or scope_coverage not in {"full", "partial"}
        or not isinstance(target_kind, str)
        or not isinstance(target_value, str)
        or not isinstance(target_base_revision, str)
        or not isinstance(created_at, str)
    ):
        raise invalid_verification_evidence()
    contract_revision = _verification_receipt_int(row["contract_revision"])
    duration_ms = _verification_receipt_int(row["duration_ms"])
    target_generation = _verification_receipt_int(
        row["target_generation"],
        minimum=1,
    )
    try:
        _validate_completion_target(
            kind=target_kind,
            value=target_value,
            base_revision=target_base_revision,
            generation=target_generation,
        )
        validate_utc_timestamp(
            created_at,
            field="verification Receipt creation time",
        )
    except StorageError as exc:
        raise invalid_verification_evidence() from exc
    subject: dict[str, Any] = {}
    if has_subject:
        subject_basis = _verification_receipt_int(
            row["verification_subject_basis_version"]
        )
        subject_snapshot = row["subject_authority_snapshot_id"]
        subject_criterion = row["subject_verification_criterion_id"]
        if subject_basis not in {0, 1}:
            raise invalid_verification_evidence()
        if subject_basis == 0:
            if subject_snapshot is not None or subject_criterion is not None:
                raise invalid_verification_evidence()
        elif (
            not isinstance(subject_snapshot, str)
            or not subject_snapshot
            or not isinstance(subject_criterion, str)
            or not subject_criterion
            or command_label != "taskgov-owned-verification-subject-v1"
        ):
            raise invalid_verification_evidence()
        subject = {
            "verification_subject_basis_version": subject_basis,
            "subject_authority_snapshot_id": subject_snapshot,
            "subject_verification_criterion_id": subject_criterion,
        }
    return {
        "verification_receipt_id": receipt_id,
        "project_id": project_id,
        "task_id": task_id,
        "contract_revision": contract_revision,
        "verification_expectation_digest": digest,
        "command_label": command_label,
        "result": result,
        "duration_ms": duration_ms,
        "scope_coverage": scope_coverage,
        "target_kind": target_kind,
        "target_value": target_value,
        "target_base_revision": target_base_revision,
        "target_generation": target_generation,
        "created_at": created_at,
        **subject,
    }


def read_verification_receipt_snapshot(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    contract_revision: int,
    verification_expectation_digest: str,
    target_kind: str,
    target_value: str,
    target_base_revision: str,
    target_generation: int,
    recent_limit: int = 10,
) -> VerificationReceiptSnapshot:
    """Read one Task's bounded audit rows and exact current Receipt basis."""
    from task_governance_tool.storage import (
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
        LOWER_HEX_64_PATTERN,
        StorageError,
        _validate_completion_target,
        current_schema_version,
        evidence_ledger_boundary_error,
        table_exists,
        validate_selected_task_receipt_evidence,
    )


    if (
        not isinstance(project_id, str)
        or not project_id
        or not isinstance(task_id, str)
        or not task_id
        or not isinstance(verification_expectation_digest, str)
        or LOWER_HEX_64_PATTERN.fullmatch(
            verification_expectation_digest
        )
        is None
        or type(recent_limit) is not int
        or not 0 <= recent_limit <= 10
    ):
        raise invalid_verification_evidence()
    contract_revision = _verification_receipt_int(contract_revision)
    if target_kind == "":
        if (
            target_value != ""
            or target_base_revision != ""
        ):
            raise invalid_verification_evidence()
        target_generation = _verification_receipt_int(target_generation)
    else:
        try:
            _validate_completion_target(
                kind=target_kind,
                value=target_value,
                base_revision=target_base_revision,
                generation=target_generation,
            )
        except StorageError as exc:
            raise invalid_verification_evidence() from exc
    source_schema_version = current_schema_version(connection)
    if not table_exists(connection, "verification_receipts"):
        if source_schema_version < 17:
            return VerificationReceiptSnapshot(
                total=0,
                same_generation=(),
                exact_current=(),
                recent=(),
            )
        raise invalid_verification_evidence()

    total = 0
    same_generation_rows: list[dict[str, Any]] = []
    exact_rows: list[dict[str, Any]] = []
    recent_candidates: list[
        tuple[str, str, dict[str, Any]]
    ] = []
    receipt_cursor: sqlite3.Cursor | None = None
    reference_cursor: sqlite3.Cursor | None = None
    try:
        receipt_cursor = connection.execute(
            """
            WITH selected_task_ids(value) AS (
                SELECT ?
                UNION ALL
                SELECT CAST(? AS BLOB)
            )
            SELECT *
              FROM verification_receipts
             WHERE task_id IN (SELECT value FROM selected_task_ids)
            """,
            (task_id, task_id),
        )
        if source_schema_version >= 18:
            reference_cursor = connection.execute(
                """
                WITH selected_task_ids(value) AS (
                    SELECT ?
                    UNION ALL
                    SELECT CAST(? AS BLOB)
                ),
                selected_source_kinds(value) AS (
                    SELECT ?
                    UNION ALL
                    SELECT CAST(? AS BLOB)
                )
                SELECT source_id, project_id, task_id, source_kind
                  FROM evidence_references
                 WHERE task_id IN (SELECT value FROM selected_task_ids)
                   AND source_kind IN (
                     SELECT value FROM selected_source_kinds
                   )
                """,
                (
                    task_id,
                    task_id,
                    "verification_receipt",
                    "verification_receipt",
                ),
            )
        while True:
            chunk = receipt_cursor.fetchmany(
                COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            )
            if not chunk:
                break
            validated_chunk: list[dict[str, Any]] = []
            selected_ids: set[str] = set()
            for row in chunk:
                receipt = _validate_verification_receipt_row(dict(row))
                receipt_id = receipt["verification_receipt_id"]
                if (
                    VERIFICATION_RECEIPT_ID_PATTERN.fullmatch(receipt_id)
                    is None
                    or receipt["project_id"] != project_id
                    or receipt["task_id"] != task_id
                ):
                    raise invalid_verification_evidence()
                validated_chunk.append(receipt)
                selected_ids.add(receipt_id)
            if source_schema_version >= 18:
                validate_selected_task_receipt_evidence(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    review_receipt_ids=set(),
                    review_finding_ids=set(),
                    verification_receipt_ids=selected_ids,
                )
            for receipt in validated_chunk:
                total += 1
                if receipt["target_generation"] == target_generation:
                    same_generation_rows.append(receipt)
                    if len(same_generation_rows) > 1:
                        raise invalid_verification_evidence()
                if (
                    receipt["contract_revision"] == contract_revision
                    and receipt["verification_expectation_digest"]
                    == verification_expectation_digest
                    and receipt["target_kind"] == target_kind
                    and receipt["target_value"] == target_value
                    and receipt["target_base_revision"]
                    == target_base_revision
                    and receipt["target_generation"] == target_generation
                ):
                    exact_rows.append(receipt)
                    if len(exact_rows) > 1:
                        raise invalid_verification_evidence()
                if recent_limit > 0:
                    recent_candidates.append(
                        (
                            receipt["created_at"],
                            receipt["verification_receipt_id"],
                            receipt,
                        )
                    )
                    recent_candidates.sort(reverse=True)
                    del recent_candidates[recent_limit:]
        if reference_cursor is not None:
            while True:
                chunk = reference_cursor.fetchmany(
                    COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
                )
                if not chunk:
                    break
                selected_ids = set()
                for row in chunk:
                    source_id = row["source_id"]
                    reference_project_id = row["project_id"]
                    reference_task_id = row["task_id"]
                    source_kind = row["source_kind"]
                    if (
                        type(source_id) is not str
                        or VERIFICATION_RECEIPT_ID_PATTERN.fullmatch(
                            source_id
                        )
                        is None
                        or type(reference_project_id) is not str
                        or reference_project_id != project_id
                        or type(reference_task_id) is not str
                        or reference_task_id != task_id
                        or type(source_kind) is not str
                        or source_kind != "verification_receipt"
                    ):
                        raise invalid_verification_evidence()
                    selected_ids.add(source_id)
                validate_selected_task_receipt_evidence(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    review_receipt_ids=set(),
                    review_finding_ids=set(),
                    verification_receipt_ids=selected_ids,
                )
    except (sqlite3.Error, StorageError) as exc:
        busy_error = evidence_ledger_boundary_error(exc)
        if busy_error.code == "database_busy":
            raise busy_error from exc
        raise invalid_verification_evidence() from exc
    finally:
        if receipt_cursor is not None:
            receipt_cursor.close()
        if reference_cursor is not None:
            reference_cursor.close()

    same_generation = tuple(same_generation_rows)
    exact_current = tuple(exact_rows)
    recent = tuple(item[2] for item in recent_candidates)
    return VerificationReceiptSnapshot(
        total=_verification_receipt_int(total),
        same_generation=same_generation,
        exact_current=exact_current,
        recent=recent,
    )


def insert_verification_receipt_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    contract_revision: int,
    verification_expectation_digest: str,
    command_label: str,
    result: str,
    duration_ms: int,
    scope_coverage: str,
    target_kind: str,
    target_value: str,
    target_base_revision: str,
    target_generation: int,
    verification_subject_basis_version: int = 0,
    subject_authority_snapshot_id: str | None = None,
    subject_verification_criterion_id: str | None = None,
) -> dict[str, Any]:
    """Append one tool-owned Receipt against the exact locked Task basis."""
    from task_governance_tool.storage import (
        PRIVATE_SCHEMA22_VERSION,
        SCHEMA_VERSION,
        StorageError,
        _read_validated_current_task_row,
        _require_completion_cycle_writer,
        _verification_expectation_digest,
        current_schema_version,
        missing_migration_versions,
        required_schema_objects_missing,
        utc_now,
    )


    _require_completion_cycle_writer(connection)
    schema_version = current_schema_version(connection)
    if (
        schema_version not in {SCHEMA_VERSION, PRIVATE_SCHEMA22_VERSION}
        or missing_migration_versions(connection, schema_version)
        or required_schema_objects_missing(
            connection,
            schema_version=schema_version,
        )
    ):
        raise StorageError(
            "migration_required",
            "verification receipt recording requires schema version 18",
        )
    locked = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if locked is None:
        raise invalid_verification_evidence()
    expectation = locked["verification"]
    if not isinstance(expectation, str):
        raise invalid_verification_evidence()
    if (
        str(locked["status"]) not in {"in_progress", "review_pending"}
        or not expectation.strip()
        or locked["current_contract_revision"] != contract_revision
        or verification_expectation_digest
        != _verification_expectation_digest(expectation)
        or locked["review_target_kind"] != target_kind
        or locked["review_target_value"] != target_value
        or locked["review_target_base_revision"] != target_base_revision
        or locked["review_target_generation"] != target_generation
        or locked["review_target_capture_version"] != 1
        or locked["review_target_authority_snapshot_id"] is None
        or locked["review_target_verification_criterion_id"] is None
        or locked["review_target_artifact_manifest_id"] is None
        or verification_subject_basis_version != 1
        or subject_authority_snapshot_id
        != locked["review_target_authority_snapshot_id"]
        or subject_verification_criterion_id
        != locked["review_target_verification_criterion_id"]
        or command_label != "taskgov-owned-verification-subject-v1"
    ):
        raise invalid_verification_evidence()
    row = _validate_verification_receipt_row(
        {
            "verification_receipt_id": (
                f"tg_verification_receipt_{secrets.token_hex(8)}"
            ),
            "project_id": project_id,
            "task_id": task_id,
            "contract_revision": contract_revision,
            "verification_expectation_digest": (
                verification_expectation_digest
            ),
            "command_label": command_label,
            "result": result,
            "duration_ms": duration_ms,
            "scope_coverage": scope_coverage,
            "target_kind": target_kind,
            "target_value": target_value,
            "target_base_revision": target_base_revision,
            "target_generation": target_generation,
            "created_at": utc_now(),
            "verification_subject_basis_version": (
                verification_subject_basis_version
            ),
            "subject_authority_snapshot_id": subject_authority_snapshot_id,
            "subject_verification_criterion_id": (
                subject_verification_criterion_id
            ),
        }
    )
    connection.execute(
        """
        INSERT INTO verification_receipts(
          verification_receipt_id, project_id, task_id,
          contract_revision, verification_expectation_digest,
          command_label, result, duration_ms, scope_coverage,
          target_kind, target_value, target_base_revision,
          target_generation, created_at,
          verification_subject_basis_version,
          subject_authority_snapshot_id,
          subject_verification_criterion_id
        ) VALUES (
          :verification_receipt_id, :project_id, :task_id,
          :contract_revision, :verification_expectation_digest,
          :command_label, :result, :duration_ms, :scope_coverage,
          :target_kind, :target_value, :target_base_revision,
          :target_generation, :created_at,
          :verification_subject_basis_version,
          :subject_authority_snapshot_id,
          :subject_verification_criterion_id
        )
        """,
        row,
    )
    stored = connection.execute(
        """
        SELECT *
          FROM verification_receipts
         WHERE verification_receipt_id = ?
        """,
        (row["verification_receipt_id"],),
    ).fetchone()
    if stored is None:
        raise invalid_verification_evidence()
    persisted = _validate_verification_receipt_row(dict(stored))
    if persisted != row:
        raise invalid_verification_evidence()
    return persisted
