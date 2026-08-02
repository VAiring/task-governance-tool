"""Structured, sanitized review evidence and deterministic review gates."""

from __future__ import annotations

import re
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.completion import (
    CompletionEvidenceError,
    FULL_GIT_OBJECT_ID,
    resolve_git_commit,
)
from task_governance_tool.git_snapshot import GitSnapshotError, capture_git_snapshot
from task_governance_tool.storage import (
    SCHEMA_VERSION,
    DatabaseTarget,
    ProjectIdentity,
    begin_initialized_write,
    utc_now,
)
from task_governance_tool.tasks import (
    TaskRepositoryError,
    create_task_event,
    ensure_git_preflight_outside_transaction,
    read_internal_task,
    reject_done_task_write,
    row_to_show_task,
    validate_choice,
    validate_sqlite_int64,
    validate_task_id,
    validate_text,
)


REVISION_REVIEW_TARGET_KINDS = (
    "git_commit",
    "diff_fingerprint",
    "external_revision",
)
REVIEW_TARGET_KINDS = (*REVISION_REVIEW_TARGET_KINDS, "git_snapshot")
RECEIPT_KINDS = ("independent", "self_review_fallback", "not_required")
REVIEW_VERDICTS = ("pass", "changes_requested", "not_required")
FINDING_SEVERITIES = ("high", "medium", "low")
DIFF_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}\Z")
PUBLIC_RECEIPT_FIELDS = (
    "review_receipt_id",
    "task_id",
    "project_id",
    "reviewer_key",
    "receipt_kind",
    "verdict",
    "target_kind",
    "target_value",
    "target_generation",
    "summary",
    "user_approved",
    "created_at",
)


class ReviewEvidenceError(Exception):
    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


@dataclass(frozen=True)
class ReviewTargetResult:
    task: dict[str, Any]
    changed_fields: list[str]
    event: dict[str, Any]


@dataclass(frozen=True)
class ReviewReceiptResult:
    receipt: dict[str, Any]
    event: dict[str, Any]


@dataclass(frozen=True)
class ReviewFindingResult:
    finding: dict[str, Any]
    event: dict[str, Any]


def review_error(code: str, message: str, field: str | None = None) -> ReviewEvidenceError:
    return ReviewEvidenceError(code=code, message=message, field=field)


def generate_review_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def public_receipt(receipt: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
    return {field: receipt[field] for field in PUBLIC_RECEIPT_FIELDS}


def next_review_target_generation(task: dict[str, Any]) -> int:
    return validate_sqlite_int64(
        int(task["review_target_generation"]) + 1,
        field="review_target_generation",
    )


def lock_and_reread_target_owner(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: str,
    *,
    database_target: DatabaseTarget | None = None,
) -> dict[str, Any]:
    if not connection.in_transaction:
        if database_target is not None:
            begin_initialized_write(connection, database_target)
        else:
            connection.execute("BEGIN IMMEDIATE")
    task = read_internal_task(connection, project.project_id, task_id)
    if task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(task)
    return task


def reject_concurrent_review_basis_change(
    observed: dict[str, Any],
    locked: dict[str, Any],
    *,
    code: str = "invalid_argument",
    message: str = "task changed concurrently before the review target was recorded",
) -> None:
    if any(
        locked[field] != observed[field]
        for field in observed
        if field != "updated_at"
    ):
        raise review_error(code, message)


def validate_stored_review_target(task: dict[str, Any]) -> None:
    target_kind = str(task["review_target_kind"])
    target_value = str(task["review_target_value"])
    base_revision = str(task["review_target_base_revision"])
    if target_kind == "git_snapshot":
        if (
            not DIFF_FINGERPRINT.fullmatch(target_value)
            or not FULL_GIT_OBJECT_ID.fullmatch(base_revision)
            or set(base_revision) == {"0"}
        ):
            raise review_error(
                "invalid_review_evidence",
                "stored Git snapshot review target is invalid",
                "review_target_value",
            )
    elif base_revision:
        raise review_error(
            "invalid_review_evidence",
            "non-snapshot review targets cannot retain a Git base revision",
            "review_target_base_revision",
        )


def normalize_review_target(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    kind: Any,
    revision: Any,
) -> tuple[str, str]:
    target_kind = validate_choice(
        "review_target_kind",
        kind,
        REVISION_REVIEW_TARGET_KINDS,
        "invalid_review_evidence",
    )
    target_value = validate_text(
        "review_target_value",
        revision,
        required=target_kind != "git_commit",
        limit=500,
    )
    if target_kind == "git_commit":
        ensure_git_preflight_outside_transaction(connection)
        try:
            target_value = resolve_git_commit(project.canonical_repo, target_value)
        except CompletionEvidenceError as exc:
            raise review_error(exc.code, exc.message, "review_target_value") from exc
    elif target_kind == "diff_fingerprint" and not DIFF_FINGERPRINT.fullmatch(target_value):
        raise review_error(
            "invalid_review_evidence",
            "diff_fingerprint must use sha256 followed by 64 lowercase hexadecimal characters",
            "review_target_value",
        )
    return target_kind, target_value


def set_review_target(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    kind: Any,
    revision: Any,
    database_target: DatabaseTarget | None = None,
) -> ReviewTargetResult:
    normalized_task_id = validate_task_id(task_id)
    observed_task = read_internal_task(connection, project.project_id, normalized_task_id)
    if observed_task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(observed_task)
    target_kind, target_value = normalize_review_target(
        connection,
        project,
        kind,
        revision,
    )
    task = lock_and_reread_target_owner(
        connection,
        project,
        normalized_task_id,
        database_target=database_target,
    )
    reject_concurrent_review_basis_change(observed_task, task)

    generation = next_review_target_generation(task)
    now = utc_now()
    connection.execute(
        """
        UPDATE tasks
           SET review_target_kind = ?,
               review_target_value = ?,
               review_target_base_revision = '',
               review_target_generation = ?,
               updated_at = ?
         WHERE project_id = ? AND task_id = ?
        """,
        (target_kind, target_value, generation, now, project.project_id, normalized_task_id),
    )
    event = create_task_event(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        event_type="review_target_set",
        summary=f"Review target set: {target_kind}, generation {generation}",
        created_at=now,
    )
    updated_row = read_internal_task(
        connection,
        project.project_id,
        normalized_task_id,
    )
    if updated_row is None:
        raise TaskRepositoryError("internal_error", "task was not readable after review target update")
    return ReviewTargetResult(
        task=row_to_show_task(updated_row),
        changed_fields=[
            "review_target_kind",
            "review_target_value",
            "review_target_base_revision",
            "review_target_generation",
        ],
        event=event,
    )


def set_git_snapshot_target(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    database_target: DatabaseTarget | None = None,
) -> ReviewTargetResult:
    normalized_task_id = validate_task_id(task_id)
    observed_task = read_internal_task(connection, project.project_id, normalized_task_id)
    if observed_task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(observed_task)
    ensure_git_preflight_outside_transaction(connection)
    try:
        snapshot = capture_git_snapshot(project.canonical_repo)
    except GitSnapshotError as exc:
        raise review_error(exc.code, exc.message, exc.field) from exc
    task = lock_and_reread_target_owner(
        connection,
        project,
        normalized_task_id,
        database_target=database_target,
    )
    reject_concurrent_review_basis_change(observed_task, task)
    generation = next_review_target_generation(task)
    now = utc_now()
    connection.execute(
        """
        UPDATE tasks
           SET review_target_kind = 'git_snapshot',
               review_target_value = ?,
               review_target_base_revision = ?,
               review_target_generation = ?,
               updated_at = ?
         WHERE project_id = ? AND task_id = ?
        """,
        (
            snapshot.fingerprint,
            snapshot.base_revision,
            generation,
            now,
            project.project_id,
            normalized_task_id,
        ),
    )
    event = create_task_event(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        event_type="review_target_set",
        summary=f"Review target set: git_snapshot, generation {generation}",
        created_at=now,
    )
    updated_row = read_internal_task(
        connection,
        project.project_id,
        normalized_task_id,
    )
    if updated_row is None:
        raise TaskRepositoryError(
            "internal_error",
            "task was not readable after Git snapshot target update",
        )
    return ReviewTargetResult(
        task=row_to_show_task(updated_row),
        changed_fields=[
            "review_target_kind",
            "review_target_value",
            "review_target_base_revision",
            "review_target_generation",
        ],
        event=event,
    )


def set_requested_review_target(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    kind: Any,
    revision: Any = None,
    database_target: DatabaseTarget | None = None,
) -> ReviewTargetResult:
    """Dispatch the public target request without accepting a snapshot revision."""
    normalized_task_id = validate_task_id(task_id)
    task = read_internal_task(connection, project.project_id, normalized_task_id)
    if task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(task)
    target_kind = validate_choice(
        "review_target_kind",
        kind,
        REVIEW_TARGET_KINDS,
        "invalid_review_evidence",
    )
    if target_kind == "git_snapshot":
        if revision is not None:
            raise review_error(
                "invalid_review_evidence",
                "git_snapshot captures the current staged index and does not accept --revision",
                "review_target_value",
            )
        return set_git_snapshot_target(
            connection,
            project,
            normalized_task_id,
            database_target=database_target,
        )
    if revision is None:
        raise review_error(
            "invalid_review_evidence",
            "--revision is required unless review target kind is git_snapshot",
            "review_target_value",
        )
    return set_review_target(
        connection,
        project,
        normalized_task_id,
        kind=target_kind,
        revision=revision,
        database_target=database_target,
    )


def normalize_receipt(
    *,
    review_tier: int,
    reviewer: Any,
    kind: Any,
    verdict: Any,
    summary: Any = "",
    user_approved: bool = False,
) -> dict[str, Any]:
    reviewer_key = validate_text("reviewer_key", reviewer, required=True, limit=500)
    receipt_kind = validate_choice(
        "receipt_kind", kind, RECEIPT_KINDS, "invalid_review_evidence"
    )
    receipt_verdict = validate_choice(
        "verdict", verdict, REVIEW_VERDICTS, "invalid_review_evidence"
    )
    receipt_summary = validate_text("review_receipt_summary", summary, limit=1000)
    approval = bool(user_approved)

    if receipt_kind == "independent":
        if receipt_verdict not in {"pass", "changes_requested"} or approval:
            raise review_error(
                "invalid_review_evidence",
                "independent receipts require pass or changes_requested and cannot use user approval",
                "receipt_kind",
            )
    elif receipt_kind == "self_review_fallback":
        if review_tier not in {1, 2} or receipt_verdict not in {"pass", "changes_requested"}:
            raise review_error(
                "invalid_review_evidence",
                "self_review_fallback is valid only for Tier 1 or Tier 2 pass/changes_requested receipts",
                "receipt_kind",
            )
        if not receipt_summary.strip():
            raise review_error(
                "invalid_review_evidence",
                "self_review_fallback requires a concise summary",
                "review_receipt_summary",
            )
        expected_approval = review_tier == 2 and receipt_verdict == "pass"
        if approval != expected_approval:
            message = (
                "Tier 2 self-review PASS requires explicit user approval"
                if expected_approval
                else "user approval is allowed only for a Tier 2 self-review PASS"
            )
            raise review_error("invalid_review_evidence", message, "user_approved")
    else:
        if (
            review_tier != 0
            or receipt_verdict != "not_required"
            or approval
            or not receipt_summary.strip()
        ):
            raise review_error(
                "invalid_review_evidence",
                "not_required is Tier 0 only and requires verdict not_required plus a rationale",
                "receipt_kind",
            )

    if receipt_verdict == "changes_requested" and not receipt_summary.strip():
        raise review_error(
            "invalid_review_evidence",
            "changes_requested requires a concise summary",
            "review_receipt_summary",
        )
    return {
        "reviewer_key": reviewer_key,
        "receipt_kind": receipt_kind,
        "verdict": receipt_verdict,
        "summary": receipt_summary,
        "user_approved": int(approval),
    }


def add_review_receipt(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    reviewer: Any,
    kind: Any,
    verdict: Any,
    summary: Any = "",
    user_approved: bool = False,
    database_target: DatabaseTarget | None = None,
) -> ReviewReceiptResult:
    normalized_task_id = validate_task_id(task_id)
    observed_task = read_internal_task(connection, project.project_id, normalized_task_id)
    if observed_task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(observed_task)
    if (
        int(observed_task["review_target_generation"]) <= 0
        or not str(observed_task["review_target_kind"])
        or not str(observed_task["review_target_value"])
    ):
        raise review_error(
            "review_target_required",
            "set a current review target before recording a receipt",
            "review_target_kind",
        )
    validate_stored_review_target(observed_task)
    normalized = normalize_receipt(
        review_tier=int(observed_task["review_tier"]),
        reviewer=reviewer,
        kind=kind,
        verdict=verdict,
        summary=summary,
        user_approved=user_approved,
    )
    task = lock_and_reread_target_owner(
        connection,
        project,
        normalized_task_id,
        database_target=database_target,
    )
    reject_concurrent_review_basis_change(
        observed_task,
        task,
        code="review_target_mismatch",
        message="review target changed before the receipt could be recorded",
    )
    validate_stored_review_target(task)
    now = utc_now()
    receipt = {
        "review_receipt_id": generate_review_id("tg_review_receipt"),
        "task_id": normalized_task_id,
        "project_id": project.project_id,
        **normalized,
        "target_kind": task["review_target_kind"],
        "target_value": task["review_target_value"],
        "target_base_revision": task["review_target_base_revision"],
        "target_generation": task["review_target_generation"],
        "created_at": now,
    }
    try:
        connection.execute(
            """
            INSERT INTO review_receipts(
              review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
              verdict, target_kind, target_value, target_base_revision,
              target_generation, summary, user_approved, created_at
            ) VALUES (
              :review_receipt_id, :task_id, :project_id, :reviewer_key, :receipt_kind,
              :verdict, :target_kind, :target_value, :target_base_revision,
              :target_generation, :summary, :user_approved, :created_at
            )
            """,
            receipt,
        )
    except sqlite3.IntegrityError as exc:
        duplicate = connection.execute(
            """
            SELECT 1 FROM review_receipts
             WHERE task_id = ? AND target_generation = ? AND reviewer_key = ?
            """,
            (normalized_task_id, task["review_target_generation"], normalized["reviewer_key"]),
        ).fetchone()
        if duplicate is not None:
            raise review_error(
                "review_receipt_already_recorded",
                "this reviewer already recorded a receipt for the current target generation",
                "reviewer_key",
            ) from exc
        raise
    connection.execute(
        "UPDATE tasks SET updated_at = ? WHERE project_id = ? AND task_id = ?",
        (now, project.project_id, normalized_task_id),
    )
    event = create_task_event(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        event_type="review_receipt_added",
        summary=(
            f"Review receipt recorded: {receipt['receipt_kind']} "
            f"{receipt['verdict']}, generation {receipt['target_generation']}"
        ),
        created_at=now,
    )
    return ReviewReceiptResult(receipt=public_receipt(receipt), event=event)


def add_review_finding(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    receipt_id: Any,
    severity: Any,
    summary: Any,
    database_target: DatabaseTarget | None = None,
) -> ReviewFindingResult:
    normalized_task_id = validate_task_id(task_id)
    observed_task = read_internal_task(connection, project.project_id, normalized_task_id)
    if observed_task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(observed_task)
    normalized_receipt_id = validate_text("review_receipt_id", receipt_id, required=True, limit=128)
    finding_severity = validate_choice(
        "severity", severity, FINDING_SEVERITIES, "invalid_review_evidence"
    )
    finding_summary = validate_text(
        "review_finding_summary", summary, required=True, limit=1000
    )
    observed_receipt = connection.execute(
        """
        SELECT * FROM review_receipts
         WHERE project_id = ? AND task_id = ? AND review_receipt_id = ?
        """,
        (project.project_id, normalized_task_id, normalized_receipt_id),
    ).fetchone()
    if observed_receipt is None or (
        int(observed_receipt["target_generation"])
        != int(observed_task["review_target_generation"])
        or str(observed_receipt["target_kind"])
        != str(observed_task["review_target_kind"])
        or str(observed_receipt["target_value"])
        != str(observed_task["review_target_value"])
        or str(observed_receipt["target_base_revision"])
        != str(observed_task["review_target_base_revision"])
    ):
        raise review_error(
            "review_receipt_mismatch",
            "receipt must belong to this task, project, and current review target",
            "review_receipt_id",
        )
    task = lock_and_reread_target_owner(
        connection,
        project,
        normalized_task_id,
        database_target=database_target,
    )
    reject_concurrent_review_basis_change(
        observed_task,
        task,
        code="review_receipt_mismatch",
        message="review target changed before the finding could be recorded",
    )
    receipt = connection.execute(
        """
        SELECT * FROM review_receipts
         WHERE project_id = ? AND task_id = ? AND review_receipt_id = ?
        """,
        (project.project_id, normalized_task_id, normalized_receipt_id),
    ).fetchone()
    if receipt is None or dict(receipt) != dict(observed_receipt):
        raise review_error(
            "review_receipt_mismatch",
            "receipt changed before the finding could be recorded",
            "review_receipt_id",
        )
    now = utc_now()
    finding = {
        "review_finding_id": generate_review_id("tg_review_finding"),
        "review_receipt_id": normalized_receipt_id,
        "severity": finding_severity,
        "status": "open",
        "summary": finding_summary,
        "resolution_summary": "",
        "created_at": now,
        "resolved_at": None,
    }
    connection.execute(
        """
        INSERT INTO review_findings(
          review_finding_id, review_receipt_id, severity, status, summary,
          resolution_summary, created_at, resolved_at
        ) VALUES (
          :review_finding_id, :review_receipt_id, :severity, :status, :summary,
          :resolution_summary, :created_at, :resolved_at
        )
        """,
        finding,
    )
    connection.execute(
        "UPDATE tasks SET updated_at = ? WHERE project_id = ? AND task_id = ?",
        (now, project.project_id, normalized_task_id),
    )
    event = create_task_event(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        event_type="review_finding_added",
        summary=f"Review finding recorded: {finding_severity}",
        created_at=now,
    )
    return ReviewFindingResult(finding=finding, event=event)


def resolve_review_finding(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    finding_id: Any,
    *,
    resolution: Any,
    database_target: DatabaseTarget | None = None,
) -> ReviewFindingResult:
    normalized_finding_id = validate_text(
        "review_finding_id", finding_id, required=True, limit=128
    )
    row = connection.execute(
        """
        SELECT finding.*, receipt.task_id, receipt.project_id
          FROM review_findings AS finding
          JOIN review_receipts AS receipt
            ON receipt.review_receipt_id = finding.review_receipt_id
         WHERE finding.review_finding_id = ? AND receipt.project_id = ?
        """,
        (normalized_finding_id, project.project_id),
    ).fetchone()
    if row is None:
        raise TaskRepositoryError("not_found", "review finding was not found")
    observed_task = read_internal_task(connection, project.project_id, str(row["task_id"]))
    if observed_task is None:
        raise TaskRepositoryError(
            "internal_error",
            "review finding owner was not readable",
        )
    reject_done_task_write(observed_task)
    resolution_summary = validate_text(
        "review_finding_resolution", resolution, required=True, limit=1000
    )
    if row["status"] != "open":
        raise review_error(
            "invalid_review_evidence",
            "review finding is already resolved and its original resolution is preserved",
            "review_finding_id",
        )
    task = lock_and_reread_target_owner(
        connection,
        project,
        str(row["task_id"]),
        database_target=database_target,
    )
    reject_concurrent_review_basis_change(
        observed_task,
        task,
        code="invalid_review_evidence",
        message="review task changed before the finding could be resolved",
    )
    locked_row = connection.execute(
        """
        SELECT finding.*, receipt.task_id, receipt.project_id
          FROM review_findings AS finding
          JOIN review_receipts AS receipt
            ON receipt.review_receipt_id = finding.review_receipt_id
         WHERE finding.review_finding_id = ? AND receipt.project_id = ?
        """,
        (normalized_finding_id, project.project_id),
    ).fetchone()
    if locked_row is None:
        raise TaskRepositoryError("not_found", "review finding was not found")
    if locked_row["status"] != "open":
        raise review_error(
            "invalid_review_evidence",
            "review finding is already resolved and its original resolution is preserved",
            "review_finding_id",
        )
    row = locked_row
    now = utc_now()
    connection.execute(
        """
        UPDATE review_findings
           SET status = 'resolved', resolution_summary = ?, resolved_at = ?
         WHERE review_finding_id = ?
        """,
        (resolution_summary, now, normalized_finding_id),
    )
    connection.execute(
        "UPDATE tasks SET updated_at = ? WHERE project_id = ? AND task_id = ?",
        (now, project.project_id, row["task_id"]),
    )
    event = create_task_event(
        connection,
        project_id=project.project_id,
        task_id=str(row["task_id"]),
        event_type="review_finding_resolved",
        summary=f"Review finding resolved: {row['severity']}",
        created_at=now,
    )
    finding_row = connection.execute(
        "SELECT * FROM review_findings WHERE review_finding_id = ?",
        (normalized_finding_id,),
    ).fetchone()
    if finding_row is None:
        raise TaskRepositoryError("internal_error", "review finding was not readable after update")
    return ReviewFindingResult(finding=dict(finding_row), event=event)


def read_review_evidence(
    connection: sqlite3.Connection,
    project_id: str,
    task_id: str,
    *,
    review_tier: int | None = None,
    recent_limit: int = 10,
    validated_task: dict[str, Any] | sqlite3.Row | None = None,
    source_schema_version: int = SCHEMA_VERSION,
) -> dict[str, Any]:
    if not 1 <= recent_limit <= 10:
        raise review_error("invalid_review_evidence", "recent review evidence limit must be 1 to 10")
    task_has_base = source_schema_version >= 6
    receipt_has_base = task_has_base
    task = validated_task
    if task is None:
        task = read_internal_task(connection, project_id, task_id)
    if task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    tier = int(task["review_tier"] if review_tier is None else review_tier)
    target_kind = str(task["review_target_kind"])
    target_value = str(task["review_target_value"])
    target_base_revision = (
        str(task["review_target_base_revision"])
        if task_has_base
        else ""
    )
    generation = int(task["review_target_generation"])
    validate_stored_review_target(
        {
            "review_target_kind": target_kind,
            "review_target_value": target_value,
            "review_target_base_revision": target_base_revision,
        }
    )

    total_receipts = int(
        connection.execute(
            "SELECT COUNT(*) FROM review_receipts WHERE project_id = ? AND task_id = ?",
            (project_id, task_id),
        ).fetchone()[0]
    )
    target_predicate = """
           project_id = ? AND task_id = ?
           AND target_kind = ? AND target_value = ?
    """
    target_parameters: list[Any] = [
        project_id,
        task_id,
        target_kind,
        target_value,
    ]
    if receipt_has_base:
        target_predicate += " AND target_base_revision = ?"
        target_parameters.append(target_base_revision)
    target_predicate += " AND target_generation = ?"
    target_parameters.append(generation)
    current_receipts = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM review_receipts
             WHERE {target_predicate}
            """,
            target_parameters,
        ).fetchone()[0]
    ) if generation > 0 else 0
    qualifying = int(
        connection.execute(
            f"""
            SELECT COUNT(DISTINCT reviewer_key) FROM review_receipts
             WHERE {target_predicate}
               AND receipt_kind = 'independent' AND verdict = 'pass'
            """,
            target_parameters,
        ).fetchone()[0]
    ) if generation > 0 else 0
    changes_requested = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM review_receipts
             WHERE {target_predicate}
               AND verdict = 'changes_requested'
            """,
            target_parameters,
        ).fetchone()[0]
    ) if generation > 0 else 0

    fallback_kind: str | None = None
    if generation > 0 and tier in {1, 2}:
        expected_approval = 1 if tier == 2 else 0
        fallback = connection.execute(
            f"""
            SELECT 1 FROM review_receipts
             WHERE {target_predicate}
               AND receipt_kind = 'self_review_fallback' AND verdict = 'pass'
               AND user_approved = ? LIMIT 1
            """,
            [*target_parameters, expected_approval],
        ).fetchone()
        if fallback is not None:
            fallback_kind = "self_review_fallback"
    elif generation > 0 and tier == 0:
        fallback = connection.execute(
            f"""
            SELECT 1 FROM review_receipts
             WHERE {target_predicate}
               AND receipt_kind = 'not_required' AND verdict = 'not_required'
               AND user_approved = 0 AND summary != '' LIMIT 1
            """,
            target_parameters,
        ).fetchone()
        if fallback is not None:
            fallback_kind = "not_required"

    severity_rows = connection.execute(
        """
        SELECT finding.severity, COUNT(*) AS count
          FROM review_findings AS finding
          JOIN review_receipts AS receipt
            ON receipt.review_receipt_id = finding.review_receipt_id
         WHERE receipt.project_id = ? AND receipt.task_id = ?
           AND finding.status = 'open'
         GROUP BY finding.severity
        """,
        (project_id, task_id),
    ).fetchall()
    open_counts = {"high": 0, "medium": 0, "low": 0}
    for row in severity_rows:
        open_counts[str(row["severity"])] = int(row["count"])

    blocking_rows = connection.execute(
        """
        SELECT finding.*, receipt.target_generation, receipt.reviewer_key
          FROM review_findings AS finding
          JOIN review_receipts AS receipt
            ON receipt.review_receipt_id = finding.review_receipt_id
         WHERE receipt.project_id = ? AND receipt.task_id = ?
           AND finding.severity IN ('high', 'medium')
           AND (
             finding.status = 'open' OR
             (finding.status = 'resolved' AND receipt.target_generation >= ?)
           )
         ORDER BY finding.created_at DESC, finding.rowid DESC
         LIMIT ?
        """,
        (project_id, task_id, generation, recent_limit),
    ).fetchall()
    blocking_findings = []
    for row in blocking_rows:
        item = dict(row)
        item["blocking_reason"] = (
            "unresolved" if row["status"] == "open" else "fresh_review_required"
        )
        blocking_findings.append(item)

    receipt_rows = connection.execute(
        """
        SELECT review_receipt_id, task_id, project_id, reviewer_key,
               receipt_kind, verdict, target_kind, target_value,
               target_generation, summary, user_approved, created_at
          FROM review_receipts
         WHERE project_id = ? AND task_id = ?
         ORDER BY created_at DESC, rowid DESC LIMIT ?
        """,
        (project_id, task_id, recent_limit),
    ).fetchall()
    finding_rows = connection.execute(
        """
        SELECT finding.*, receipt.target_generation, receipt.reviewer_key
          FROM review_findings AS finding
          JOIN review_receipts AS receipt
            ON receipt.review_receipt_id = finding.review_receipt_id
         WHERE receipt.project_id = ? AND receipt.task_id = ?
         ORDER BY finding.created_at DESC, finding.rowid DESC LIMIT ?
        """,
        (project_id, task_id, recent_limit),
    ).fetchall()

    required = {0: 0, 1: 1, 2: 2}[tier]
    target_set = generation > 0 and bool(target_kind) and bool(target_value)
    tier_satisfied = (
        fallback_kind == "not_required"
        if tier == 0
        else qualifying >= required or fallback_kind == "self_review_fallback"
    )
    satisfied = (
        target_set
        and tier_satisfied
        and changes_requested == 0
        and not blocking_rows
    )
    return {
        "target": {"kind": target_kind, "value": target_value, "generation": generation},
        "gate": {
            "review_tier": tier,
            "required_independent_passes": required,
            "qualifying_independent_passes": qualifying,
            "fallback_kind": fallback_kind,
            "satisfied": satisfied,
        },
        "counts": {
            "receipts_total": total_receipts,
            "receipts_current_generation": current_receipts,
            "changes_requested_current_generation": changes_requested,
            "open_high": open_counts["high"],
            "open_medium": open_counts["medium"],
            "open_low": open_counts["low"],
        },
        "blocking_findings": blocking_findings,
        "recent_receipts": [dict(row) for row in receipt_rows],
        "recent_findings": [dict(row) for row in finding_rows],
    }


def enforce_review_gate(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    review_tier: int,
) -> dict[str, Any]:
    evidence = read_review_evidence(
        connection,
        project_id,
        task_id,
        review_tier=review_tier,
    )
    error = first_review_gate_error(evidence)
    if error is not None:
        raise error
    return evidence


def first_review_gate_error(
    evidence: dict[str, Any],
) -> ReviewEvidenceError | None:
    """Return the existing review gate's first deterministic failure."""
    target = evidence["target"]
    if target["generation"] <= 0 or not target["kind"] or not target["value"]:
        return review_error(
            "review_target_required",
            "task completion requires a current structured review target",
            "review_target_kind",
        )
    if evidence["blocking_findings"]:
        return review_error(
            "review_finding_unresolved",
            "a high or medium finding is unresolved or still requires a newer target and fresh review",
            "review_finding",
        )
    if evidence["counts"]["changes_requested_current_generation"]:
        return review_error(
            "review_changes_requested",
            "a current-generation changes_requested receipt requires a newer target and fresh review",
            "review_receipt",
        )
    if not evidence["gate"]["satisfied"]:
        return review_error(
            "review_receipts_insufficient",
            "structured review receipts do not satisfy this task's review tier",
            "review_receipt",
        )
    return None
