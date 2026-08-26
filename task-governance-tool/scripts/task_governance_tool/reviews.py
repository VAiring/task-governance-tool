"""Structured, sanitized review evidence and deterministic review gates."""

from __future__ import annotations

import re
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.artifact_manifest import (
    ArtifactManifestError,
    ArtifactObservation,
    build_artifact_manifest,
    observe_git_commit_manifest,
    observe_staged_git_manifest,
    opaque_artifact_observation,
)
from task_governance_tool.completion import (
    CompletionEvidenceError,
    FULL_GIT_OBJECT_ID,
    resolve_git_commit,
)
from task_governance_tool.evidence_ledger import (
    EvidenceLedgerError,
    EvidenceSource,
    TargetCaptureBinding,
    build_evidence_reference,
    verification_expectation_digest,
)
from task_governance_tool.git_snapshot import GitSnapshotError
from task_governance_tool.review_provenance import (
    ReviewProvenanceError,
    build_review_provenance_v1,
    normalize_review_provenance_input,
    project_review_provenance,
)
from task_governance_tool.storage import (
    COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
    REVIEW_FINDING_ID_PATTERN,
    REVIEW_RECEIPT_ID_PATTERN,
    SCHEMA_VERSION,
    DatabaseTarget,
    ProjectIdentity,
    StorageError,
    begin_initialized_write,
    evidence_ledger_sqlite_error,
    insert_review_receipt_with_provenance_locked,
    is_sqlite_busy_or_locked,
    operational_sqlite_error,
    persist_artifact_manifest_locked,
    persist_evidence_reference_locked,
    read_review_receipt_with_provenance,
    utc_now,
    validate_selected_task_receipt_evidence,
    validate_stored_review_finding_projection,
    validate_stored_review_receipt_projection,
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
    "review_provenance",
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
class ReviewTargetAuthorityBasis:
    """Validated pre-I/O authority used by the target-set parent service."""

    task: dict[str, Any]
    authority_snapshot_id: str
    acceptance_criterion_id: str | None
    verification_criterion_id: str | None
    verification_expectation_digest: str
    verification_criterion_digest: str | None


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


def require_current_capture(task: dict[str, Any]) -> None:
    if int(task.get("review_target_capture_version", 0)) != 1:
        raise review_error(
            "evidence_basis_stale",
            "current evidence basis must be captured again",
        )


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
    target_value = validate_revision_review_target_input(target_kind, revision)
    if target_kind == "git_commit":
        ensure_git_preflight_outside_transaction(connection)
        try:
            target_value = resolve_git_commit(project.canonical_repo, target_value)
        except CompletionEvidenceError as exc:
            raise review_error(exc.code, exc.message, "review_target_value") from exc
    return target_kind, target_value


def validate_revision_review_target_input(kind: Any, revision: Any) -> str:
    """Validate a non-snapshot request before any Runner state is touched."""

    target_kind = validate_choice(
        "review_target_kind",
        kind,
        REVISION_REVIEW_TARGET_KINDS,
        "invalid_review_evidence",
    )
    if revision is None:
        raise review_error(
            "invalid_review_evidence",
            "--revision is required unless review target kind is git_snapshot",
            "review_target_value",
        )
    target_value = validate_text(
        "review_target_value",
        revision,
        required=target_kind != "git_commit",
        limit=500,
    )
    if target_kind == "diff_fingerprint" and not DIFF_FINGERPRINT.fullmatch(
        target_value
    ):
        raise review_error(
            "invalid_review_evidence",
            "diff_fingerprint must use sha256 followed by 64 lowercase hexadecimal characters",
            "review_target_value",
        )
    return target_value


def normalize_revision_review_target(
    project: ProjectIdentity,
    kind: Any,
    revision: Any,
) -> tuple[str, str]:
    """Resolve one non-snapshot target outside a SQLite transaction."""

    target_kind = validate_choice(
        "review_target_kind",
        kind,
        REVISION_REVIEW_TARGET_KINDS,
        "invalid_review_evidence",
    )
    target_value = validate_revision_review_target_input(target_kind, revision)
    if target_kind == "git_commit":
        try:
            target_value = resolve_git_commit(project.canonical_repo, target_value)
        except CompletionEvidenceError as exc:
            raise review_error(exc.code, exc.message, "review_target_value") from exc
    return target_kind, target_value


def read_review_target_authority_basis(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
) -> ReviewTargetAuthorityBasis:
    """Read the exact current Task/Contract/criterion basis without mutation."""

    normalized_task_id = validate_task_id(task_id)
    task = read_internal_task(connection, project.project_id, normalized_task_id)
    if task is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(task)
    snapshot_id = task.get("current_authority_snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        )
    rows = connection.execute(
        """
        SELECT link.criterion_kind, link.criterion_id, criterion.digest
          FROM authority_snapshot_criteria AS link
          JOIN contract_criteria AS criterion
            ON criterion.project_id = link.project_id
           AND criterion.task_id = link.task_id
           AND criterion.criterion_id = link.criterion_id
         WHERE link.project_id = ?
           AND link.task_id = ?
           AND link.authority_snapshot_id = ?
         ORDER BY link.criterion_kind
        """,
        (project.project_id, normalized_task_id, snapshot_id),
    ).fetchall()
    criteria: dict[str, tuple[str, str]] = {}
    for row in rows:
        criterion_kind = row["criterion_kind"]
        criterion_id = row["criterion_id"]
        digest = row["digest"]
        if (
            criterion_kind not in {"acceptance", "verification"}
            or criterion_kind in criteria
            or not isinstance(criterion_id, str)
            or not isinstance(digest, str)
        ):
            raise StorageError(
                "evidence_ledger_inconsistent",
                "stored evidence ledger is inconsistent",
            )
        criteria[str(criterion_kind)] = (criterion_id, digest)
    acceptance = criteria.get("acceptance")
    verification = criteria.get("verification")
    exact_verification = str(task["verification"])
    if bool(exact_verification.strip()) != (verification is not None):
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        )
    return ReviewTargetAuthorityBasis(
        task=task,
        authority_snapshot_id=snapshot_id,
        acceptance_criterion_id=None if acceptance is None else acceptance[0],
        verification_criterion_id=None if verification is None else verification[0],
        verification_expectation_digest=verification_expectation_digest(
            exact_verification
        ),
        verification_criterion_digest=(
            None if verification is None else verification[1]
        ),
    )


def persist_prepared_review_target_capture(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    observed_task: dict[str, Any],
    *,
    observation: ArtifactObservation,
    database_target: DatabaseTarget | None = None,
    now: str | None = None,
) -> ReviewTargetResult:
    """Persist a caller-observed target after one locked freshness reread."""

    task_id = validate_task_id(observed_task.get("task_id"))
    task = lock_and_reread_target_owner(
        connection,
        project,
        task_id,
        database_target=database_target,
    )
    reject_concurrent_review_basis_change(observed_task, task)
    return _persist_review_target_capture(
        connection,
        project,
        task,
        observation=observation,
        generation=next_review_target_generation(task),
        now=now or utc_now(),
    )


def _persist_review_target_capture(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task: dict[str, Any],
    *,
    observation: ArtifactObservation,
    generation: int,
    now: str,
) -> ReviewTargetResult:
    """Persist one complete schema-v18 target capture in the active writer."""

    task_id = str(task["task_id"])
    try:
        binding = TargetCaptureBinding(
            target_kind=observation.target_kind,
            target_value=observation.target_value,
            target_base_revision=observation.target_base_revision,
            target_generation=generation,
            authority_snapshot_id=str(task["current_authority_snapshot_id"]),
            acceptance_criterion_id=(
                str(task["review_target_acceptance_criterion_id"])
                if task.get("review_target_acceptance_criterion_id") is not None
                else None
            ),
            verification_criterion_id=(
                str(task["review_target_verification_criterion_id"])
                if task.get("review_target_verification_criterion_id") is not None
                else None
            ),
        )
    except (EvidenceLedgerError, KeyError, TypeError, ValueError) as exc:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        ) from exc

    # The target binds the criteria belonging to the current authority
    # snapshot, not the nullable bindings of the previous target.
    criterion_rows = connection.execute(
        """
        SELECT criterion_kind, criterion_id
          FROM authority_snapshot_criteria
         WHERE project_id = ? AND task_id = ? AND authority_snapshot_id = ?
        """,
        (project.project_id, task_id, binding.authority_snapshot_id),
    ).fetchall()
    criteria = {str(row["criterion_kind"]): str(row["criterion_id"]) for row in criterion_rows}
    try:
        binding = TargetCaptureBinding(
            target_kind=observation.target_kind,
            target_value=observation.target_value,
            target_base_revision=observation.target_base_revision,
            target_generation=generation,
            authority_snapshot_id=binding.authority_snapshot_id,
            acceptance_criterion_id=criteria.get("acceptance"),
            verification_criterion_id=criteria.get("verification"),
        )
        manifest = build_artifact_manifest(observation, binding)
        manifest_id = generate_review_id("tg_artifact_manifest")
        manifest_row = {
            "artifact_manifest_id": manifest_id,
            "project_id": project.project_id,
            "task_id": task_id,
            "state": manifest.state,
            "object_format": manifest.object_format,
            "comparison_base": manifest.comparison_base,
            "target_kind": manifest.target_kind,
            "target_value": manifest.target_value,
            "target_base_revision": manifest.target_base_revision,
            "target_generation": manifest.target_generation,
            "authority_snapshot_id": manifest.authority_snapshot_id,
            "acceptance_criterion_id": manifest.acceptance_criterion_id,
            "verification_criterion_id": manifest.verification_criterion_id,
            "omission_code": manifest.omission_code,
            "entry_count": manifest.entry_count,
            "digest": manifest.digest,
            "created_at": now,
        }
        entry_rows = tuple(
            {
                "artifact_manifest_id": manifest_id,
                "project_id": project.project_id,
                "task_id": task_id,
                "ordinal": entry.ordinal,
                "entry_kind": entry.kind,
                "old_path": entry.old_path,
                "new_path": entry.new_path,
                "before_mode": entry.before_mode,
                "before_object_id": entry.before_object_id,
                "after_mode": entry.after_mode,
                "after_object_id": entry.after_object_id,
            }
            for entry in manifest.entries
        )
        persist_artifact_manifest_locked(
            connection,
            manifest=manifest_row,
            entries=entry_rows,
        )
        source_projection = (
            {
                "artifact_manifest_id": manifest_id,
                "state": manifest.state,
                "object_format": manifest.object_format,
                "comparison_base": manifest.comparison_base,
                "entry_count": manifest.entry_count,
                "digest": manifest.digest,
                "omission_code": manifest.omission_code,
            }
            if manifest.state == "complete_git"
            else {
                "artifact_manifest_id": manifest_id,
                "state": manifest.state,
                "target_kind": manifest.target_kind,
                "digest": manifest.digest,
                "omission_code": manifest.omission_code,
            }
        )
        source = EvidenceSource(
            source_kind="artifact_manifest",
            source_state=manifest.state,
            source_id=manifest_id,
            source_projection=source_projection,
        )
        reference = build_evidence_reference(
            source=source,
            project_id=project.project_id,
            task_id=task_id,
            contract_revision=int(task["current_contract_revision"]),
            binding=binding,
        )
        persist_evidence_reference_locked(
            connection,
            reference={
                "evidence_reference_id": generate_review_id(
                    "tg_evidence_reference"
                ),
                "project_id": project.project_id,
                "task_id": task_id,
                "source_kind": source.source_kind,
                "source_state": source.source_state,
                "source_id": source.source_id,
                "assurance_class": reference.attribution.assurance_class,
                "producer_class": reference.attribution.producer_class,
                "producer_version": reference.attribution.producer_version,
                "contract_revision": int(task["current_contract_revision"]),
                "authority_snapshot_id": binding.authority_snapshot_id,
                "acceptance_criterion_id": binding.acceptance_criterion_id,
                "verification_criterion_id": binding.verification_criterion_id,
                "target_kind": binding.target_kind,
                "target_value": binding.target_value,
                "target_base_revision": binding.target_base_revision,
                "target_generation": binding.target_generation,
                "completion_cycle_id": None,
                "digest": reference.digest,
                "created_at": now,
            },
        )
    except ArtifactManifestError as exc:
        raise review_error(exc.code, exc.message) from exc
    except EvidenceLedgerError as exc:
        raise StorageError(exc.code, exc.message) from exc

    connection.execute(
        """
        UPDATE tasks
           SET review_target_kind = ?,
               review_target_value = ?,
               review_target_base_revision = ?,
               review_target_generation = ?,
               review_target_capture_version = 1,
               review_target_authority_snapshot_id = ?,
               review_target_acceptance_criterion_id = ?,
               review_target_verification_criterion_id = ?,
               review_target_artifact_manifest_id = ?,
               updated_at = ?
         WHERE project_id = ? AND task_id = ?
        """,
        (
            binding.target_kind,
            binding.target_value,
            binding.target_base_revision,
            binding.target_generation,
            binding.authority_snapshot_id,
            binding.acceptance_criterion_id,
            binding.verification_criterion_id,
            manifest_id,
            now,
            project.project_id,
            task_id,
        ),
    )
    event = create_task_event(
        connection,
        project_id=project.project_id,
        task_id=task_id,
        event_type="review_target_set",
        summary=(
            f"Review target set: {binding.target_kind}, generation "
            f"{binding.target_generation}"
        ),
        created_at=now,
    )
    updated_row = read_internal_task(connection, project.project_id, task_id)
    if updated_row is None:
        raise TaskRepositoryError(
            "internal_error",
            "task was not readable after review target update",
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
    try:
        observation = (
            observe_git_commit_manifest(project.canonical_repo, target_value)
            if target_kind == "git_commit"
            else opaque_artifact_observation(
                target_kind=target_kind,
                target_value=target_value,
            )
        )
    except ArtifactManifestError as exc:
        raise review_error(exc.code, exc.message) from exc
    except GitSnapshotError as exc:
        raise review_error(exc.code, exc.message, exc.field) from exc
    except CompletionEvidenceError as exc:
        raise review_error(exc.code, exc.message, "review_target_value") from exc
    task = lock_and_reread_target_owner(
        connection,
        project,
        normalized_task_id,
        database_target=database_target,
    )
    reject_concurrent_review_basis_change(observed_task, task)

    generation = next_review_target_generation(task)
    now = utc_now()
    return _persist_review_target_capture(
        connection,
        project,
        task,
        observation=observation,
        generation=generation,
        now=now,
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
        observation = observe_staged_git_manifest(project.canonical_repo)
    except ArtifactManifestError as exc:
        raise review_error(exc.code, exc.message) from exc
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
    return _persist_review_target_capture(
        connection,
        project,
        task,
        observation=observation,
        generation=generation,
        now=now,
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
    reviewer_class: Any = None,
    model_state: Any = None,
    declared_model_id: Any = None,
    skill_state: Any = None,
    declared_skill_id: Any = None,
    declared_skill_version: Any = None,
    review_profiles: Any = None,
    review_lenses: Any = None,
    context_relation: Any = None,
    review_methods: Any = None,
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
    try:
        normalized_provenance = normalize_review_provenance_input(
            receipt_kind=normalized["receipt_kind"],
            reviewer_class=reviewer_class,
            model_state=model_state,
            declared_model_id=declared_model_id,
            skill_state=skill_state,
            declared_skill_id=declared_skill_id,
            declared_skill_version=declared_skill_version,
            review_profiles=review_profiles,
            review_lenses=review_lenses,
            context_relation=context_relation,
            method_codes=review_methods,
        )
    except ReviewProvenanceError as exc:
        raise review_error(exc.code, exc.message, exc.field) from exc
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
    require_current_capture(task)
    now = utc_now()
    receipt_id = generate_review_id("tg_review_receipt")
    provenance = None
    if normalized_provenance is not None:
        try:
            provenance = build_review_provenance_v1(
                project_id=project.project_id,
                task_id=normalized_task_id,
                review_receipt_id=receipt_id,
                receipt_kind=str(normalized["receipt_kind"]),
                target={
                    "kind": str(task["review_target_kind"]),
                    "value": str(task["review_target_value"]),
                    "base_revision": str(
                        task["review_target_base_revision"]
                    ),
                    "generation": int(task["review_target_generation"]),
                    "capture_version": 1,
                },
                normalized_input=normalized_provenance,
            )
        except ReviewProvenanceError as exc:
            raise review_error(exc.code, exc.message, exc.field) from exc
    receipt = {
        "review_receipt_id": receipt_id,
        "task_id": normalized_task_id,
        "project_id": project.project_id,
        **normalized,
        "target_kind": task["review_target_kind"],
        "target_value": task["review_target_value"],
        "target_base_revision": task["review_target_base_revision"],
        "target_generation": task["review_target_generation"],
        "created_at": now,
    }
    code_rows = tuple(
        {
            "project_id": project.project_id,
            "task_id": normalized_task_id,
            "review_provenance_id": provenance["review_provenance_id"],
            "code_kind": code_kind,
            "ordinal": ordinal,
            "code": code,
        }
        for code_kind, field in (
            ("profile", "review_profiles"),
            ("lens", "review_lenses"),
            ("method", "method_codes"),
        )
        for ordinal, code in enumerate(provenance[field])
    ) if provenance is not None else ()
    try:
        stored = insert_review_receipt_with_provenance_locked(
            connection,
            receipt,
            provenance,
            code_rows,
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
    try:
        projected_provenance = project_review_provenance(
            receipt_kind=receipt["receipt_kind"],
            basis_version=stored["receipt"][
                "review_provenance_basis_version"
            ],
            provenance=stored["provenance"],
            project_id=project.project_id,
            task_id=normalized_task_id,
            review_receipt_id=receipt_id,
            target={
                "kind": str(task["review_target_kind"]),
                "value": str(task["review_target_value"]),
                "base_revision": str(task["review_target_base_revision"]),
                "generation": int(task["review_target_generation"]),
                "capture_version": 1,
            },
        )
        public_stored = {
            **stored["receipt"],
            "review_provenance": projected_provenance,
        }
        binding = TargetCaptureBinding(
            target_kind=str(task["review_target_kind"]),
            target_value=str(task["review_target_value"]),
            target_base_revision=str(task["review_target_base_revision"]),
            target_generation=int(task["review_target_generation"]),
            authority_snapshot_id=str(
                task["review_target_authority_snapshot_id"]
            ),
            acceptance_criterion_id=(
                str(task["review_target_acceptance_criterion_id"])
                if task["review_target_acceptance_criterion_id"] is not None
                else None
            ),
            verification_criterion_id=(
                str(task["review_target_verification_criterion_id"])
                if task["review_target_verification_criterion_id"] is not None
                else None
            ),
        )
        source = EvidenceSource(
            source_kind="review_receipt",
            source_state="recorded",
            source_id=receipt_id,
            source_projection={
                "review_receipt_id": receipt_id,
                "reviewer_key": str(receipt["reviewer_key"]),
                "receipt_kind": str(receipt["receipt_kind"]),
                "verdict": str(receipt["verdict"]),
                "summary": str(receipt["summary"]),
                "user_approved": int(receipt["user_approved"]),
                "created_at": now,
                "review_provenance": projected_provenance,
            },
        )
        reference = build_evidence_reference(
            source=source,
            project_id=project.project_id,
            task_id=normalized_task_id,
            contract_revision=int(task["current_contract_revision"]),
            binding=binding,
        )
        persist_evidence_reference_locked(
            connection,
            reference={
                "evidence_reference_id": generate_review_id(
                    "tg_evidence_reference"
                ),
                "project_id": project.project_id,
                "task_id": normalized_task_id,
                "source_kind": source.source_kind,
                "source_state": source.source_state,
                "source_id": source.source_id,
                "assurance_class": reference.attribution.assurance_class,
                "producer_class": reference.attribution.producer_class,
                "producer_version": reference.attribution.producer_version,
                "contract_revision": int(task["current_contract_revision"]),
                "authority_snapshot_id": binding.authority_snapshot_id,
                "acceptance_criterion_id": binding.acceptance_criterion_id,
                "verification_criterion_id": binding.verification_criterion_id,
                "target_kind": binding.target_kind,
                "target_value": binding.target_value,
                "target_base_revision": binding.target_base_revision,
                "target_generation": binding.target_generation,
                "completion_cycle_id": None,
                "digest": reference.digest,
                "created_at": now,
            },
        )
    except (EvidenceLedgerError, ReviewProvenanceError) as exc:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        ) from exc
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
    return ReviewReceiptResult(receipt=public_receipt(public_stored), event=event)


def _read_unique_owned_review_receipt(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    receipt_id: str,
) -> sqlite3.Row | None:
    try:
        candidates = connection.execute(
            """
            SELECT *
              FROM review_receipts
             WHERE review_receipt_id IN (
                   ?, CAST(? AS TEXT), CAST(? AS BLOB)
             )
             LIMIT 2
            """,
            (receipt_id, receipt_id, receipt_id),
        ).fetchall()
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    if not candidates:
        return None
    if len(candidates) != 1:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        )
    receipt = candidates[0]
    if (
        type(receipt["review_receipt_id"]) is not str
        or receipt["review_receipt_id"] != receipt_id
        or type(receipt["project_id"]) is not str
        or type(receipt["task_id"]) is not str
    ):
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        )
    if receipt["project_id"] != project_id or receipt["task_id"] != task_id:
        return None
    return receipt


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
    observed_receipt = _read_unique_owned_review_receipt(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        receipt_id=normalized_receipt_id,
    )
    if observed_receipt is None:
        raise review_error(
            "review_receipt_mismatch",
            "receipt must belong to this task, project, and current review target",
            "review_receipt_id",
        )
    validate_selected_task_receipt_evidence(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        review_receipt_ids={normalized_receipt_id},
        review_finding_ids=set(),
        verification_receipt_ids=set(),
    )
    if (
        observed_receipt["target_generation"]
        != observed_task["review_target_generation"]
        or observed_receipt["target_kind"]
        != observed_task["review_target_kind"]
        or observed_receipt["target_value"]
        != observed_task["review_target_value"]
        or observed_receipt["target_base_revision"]
        != observed_task["review_target_base_revision"]
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
    require_current_capture(task)
    receipt = _read_unique_owned_review_receipt(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        receipt_id=normalized_receipt_id,
    )
    if receipt is None or dict(receipt) != dict(observed_receipt):
        raise review_error(
            "review_receipt_mismatch",
            "receipt changed before the finding could be recorded",
            "review_receipt_id",
        )
    validate_selected_task_receipt_evidence(
        connection,
        project_id=project.project_id,
        task_id=normalized_task_id,
        review_receipt_ids={normalized_receipt_id},
        review_finding_ids=set(),
        verification_receipt_ids=set(),
    )
    try:
        parent_reference_rows = connection.execute(
            """
            SELECT contract_revision, authority_snapshot_id,
                   acceptance_criterion_id, verification_criterion_id,
                   target_kind, target_value, target_base_revision,
                   target_generation
              FROM evidence_references
             WHERE project_id = ? AND task_id = ?
               AND source_kind = 'review_receipt'
               AND source_state = 'recorded'
               AND source_id = ?
             ORDER BY evidence_reference_id
             LIMIT 2
            """,
            (project.project_id, normalized_task_id, normalized_receipt_id),
        ).fetchall()
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    if len(parent_reference_rows) != 1:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        )
    try:
        parent_reference = parent_reference_rows[0]
        parent_contract_revision = parent_reference["contract_revision"]
        parent_binding_values = (
            parent_reference["authority_snapshot_id"],
            parent_reference["acceptance_criterion_id"],
            parent_reference["verification_criterion_id"],
            parent_reference["target_kind"],
            parent_reference["target_value"],
            parent_reference["target_base_revision"],
            parent_reference["target_generation"],
        )
    except (IndexError, KeyError, TypeError) as exc:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        ) from exc
    if (
        type(parent_contract_revision) is not int
        or parent_contract_revision < 0
        or type(parent_binding_values[0]) is not str
        or not parent_binding_values[0]
        or any(
            value is not None and (type(value) is not str or not value)
            for value in parent_binding_values[1:3]
        )
        or any(
            type(value) is not str
            for value in parent_binding_values[3:6]
        )
        or not parent_binding_values[3]
        or not parent_binding_values[4]
        or type(parent_binding_values[6]) is not int
        or parent_binding_values[6] <= 0
        or (
            parent_contract_revision,
            *parent_binding_values,
        )
        != (
            task["current_contract_revision"],
            task["review_target_authority_snapshot_id"],
            task["review_target_acceptance_criterion_id"],
            task["review_target_verification_criterion_id"],
            receipt["target_kind"],
            receipt["target_value"],
            receipt["target_base_revision"],
            receipt["target_generation"],
        )
    ):
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        )
    finding_binding = TargetCaptureBinding(
        target_kind=parent_binding_values[3],
        target_value=parent_binding_values[4],
        target_base_revision=parent_binding_values[5],
        target_generation=parent_binding_values[6],
        authority_snapshot_id=parent_binding_values[0],
        acceptance_criterion_id=parent_binding_values[1],
        verification_criterion_id=parent_binding_values[2],
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
    try:
        finding_source = EvidenceSource(
            source_kind="review_finding",
            source_state="recorded",
            source_id=str(finding["review_finding_id"]),
            source_projection={
                "review_finding_id": str(finding["review_finding_id"]),
                "review_receipt_id": normalized_receipt_id,
                "severity": finding_severity,
                "summary": finding_summary,
                "created_at": now,
            },
        )
        finding_reference = build_evidence_reference(
            source=finding_source,
            project_id=project.project_id,
            task_id=normalized_task_id,
            contract_revision=parent_contract_revision,
            binding=finding_binding,
        )
        persist_evidence_reference_locked(
            connection,
            reference={
                "evidence_reference_id": generate_review_id(
                    "tg_evidence_reference"
                ),
                "project_id": project.project_id,
                "task_id": normalized_task_id,
                "source_kind": finding_source.source_kind,
                "source_state": finding_source.source_state,
                "source_id": finding_source.source_id,
                "assurance_class": (
                    finding_reference.attribution.assurance_class
                ),
                "producer_class": finding_reference.attribution.producer_class,
                "producer_version": (
                    finding_reference.attribution.producer_version
                ),
                "contract_revision": parent_contract_revision,
                "authority_snapshot_id": finding_binding.authority_snapshot_id,
                "acceptance_criterion_id": (
                    finding_binding.acceptance_criterion_id
                ),
                "verification_criterion_id": (
                    finding_binding.verification_criterion_id
                ),
                "target_kind": finding_binding.target_kind,
                "target_value": finding_binding.target_value,
                "target_base_revision": finding_binding.target_base_revision,
                "target_generation": finding_binding.target_generation,
                "completion_cycle_id": None,
                "digest": finding_reference.digest,
                "created_at": now,
            },
        )
    except EvidenceLedgerError as exc:
        raise StorageError(exc.code, exc.message) from exc
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


def _read_owned_review_finding(
    connection: sqlite3.Connection,
    *,
    finding_id: str,
    project_id: str,
) -> sqlite3.Row | None:
    row = connection.execute(
        """
        SELECT finding.*, receipt.review_receipt_id AS parent_receipt_id,
               receipt.task_id, receipt.project_id,
               (
                 SELECT COUNT(*)
                   FROM review_receipts AS candidate
                  WHERE candidate.review_receipt_id IN (
                        finding.review_receipt_id,
                        CAST(finding.review_receipt_id AS TEXT),
                        CAST(finding.review_receipt_id AS BLOB)
                  )
               ) AS parent_candidate_count
          FROM review_findings AS finding
          LEFT JOIN review_receipts AS receipt
            ON typeof(finding.review_receipt_id) = 'text'
           AND typeof(receipt.review_receipt_id) = 'text'
           AND receipt.review_receipt_id = finding.review_receipt_id
         WHERE finding.review_finding_id = ?
        """,
        (finding_id,),
    ).fetchone()
    if row is None:
        return None
    if (
        type(row["review_receipt_id"]) is not str
        or not 1 <= len(row["review_receipt_id"]) <= 128
        or type(row["parent_receipt_id"]) is not str
        or row["parent_receipt_id"] != row["review_receipt_id"]
        or type(row["parent_candidate_count"]) is not int
        or row["parent_candidate_count"] != 1
        or type(row["task_id"]) is not str
        or type(row["project_id"]) is not str
    ):
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        )
    return row if row["project_id"] == project_id else None


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
    row = _read_owned_review_finding(
        connection,
        finding_id=normalized_finding_id,
        project_id=project.project_id,
    )
    if row is None:
        raise TaskRepositoryError("not_found", "review finding was not found")
    validate_selected_task_receipt_evidence(
        connection,
        project_id=project.project_id,
        task_id=row["task_id"],
        review_receipt_ids=set(),
        review_finding_ids={normalized_finding_id},
        verification_receipt_ids=set(),
    )
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
    locked_row = _read_owned_review_finding(
        connection,
        finding_id=normalized_finding_id,
        project_id=project.project_id,
    )
    if locked_row is None:
        raise TaskRepositoryError("not_found", "review finding was not found")
    if locked_row["status"] != "open":
        raise review_error(
            "invalid_review_evidence",
            "review finding is already resolved and its original resolution is preserved",
            "review_finding_id",
        )
    validate_selected_task_receipt_evidence(
        connection,
        project_id=project.project_id,
        task_id=locked_row["task_id"],
        review_receipt_ids=set(),
        review_finding_ids={normalized_finding_id},
        verification_receipt_ids=set(),
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


def _project_stored_review_receipt(
    connection: sqlite3.Connection,
    row: dict[str, Any] | sqlite3.Row,
    *,
    source_schema_version: int,
) -> dict[str, Any]:
    """Project the public v1/v0/null provenance union for one Receipt."""

    receipt = dict(row)
    provenance: dict[str, Any] | None = None
    basis_version = 0
    if source_schema_version >= 18:
        stored = read_review_receipt_with_provenance(
            connection,
            review_receipt_id=str(receipt["review_receipt_id"]),
        )
        if stored is None:
            raise StorageError(
                "evidence_ledger_inconsistent",
                "stored evidence ledger is inconsistent",
            )
        receipt = stored["receipt"]
        provenance = stored["provenance"]
        basis_version = int(receipt["review_provenance_basis_version"])
    try:
        projected = project_review_provenance(
            receipt_kind=receipt["receipt_kind"],
            basis_version=basis_version,
            provenance=provenance,
            project_id=receipt["project_id"],
            task_id=receipt["task_id"],
            review_receipt_id=receipt["review_receipt_id"],
            target=(
                {
                    "kind": receipt["target_kind"],
                    "value": receipt["target_value"],
                    "base_revision": receipt["target_base_revision"],
                    "generation": receipt["target_generation"],
                    "capture_version": 1,
                }
                if basis_version == 1
                else None
            ),
        )
    except ReviewProvenanceError as exc:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        ) from exc
    return public_receipt({**receipt, "review_provenance": projected})


def _review_inventory_inconsistent() -> StorageError:
    return StorageError(
        "evidence_ledger_inconsistent",
        "stored evidence ledger is inconsistent",
    )


def _validate_review_receipt_inventory_owner(
    row: sqlite3.Row,
    *,
    project_id: str,
    task_id: str,
) -> str:
    receipt_id = row["review_receipt_id"]
    if (
        type(receipt_id) is not str
        or not 1 <= len(receipt_id) <= 128
        or type(row["project_id"]) is not str
        or row["project_id"] != project_id
        or type(row["task_id"]) is not str
        or row["task_id"] != task_id
        or type(row["inventory_rowid"]) is not int
    ):
        raise _review_inventory_inconsistent()
    return receipt_id


def _validate_review_finding_inventory_owner(
    row: sqlite3.Row,
    *,
    project_id: str,
    task_id: str,
) -> str:
    finding_id = row["review_finding_id"]
    if (
        type(finding_id) is not str
        or not 1 <= len(finding_id) <= 128
        or type(row["review_receipt_id"]) is not str
        or not 1 <= len(row["review_receipt_id"]) <= 128
        or type(row["parent_receipt_id"]) is not str
        or row["parent_receipt_id"] != row["review_receipt_id"]
        or type(row["parent_candidate_count"]) is not int
        or row["parent_candidate_count"] != 1
        or type(row["receipt_project_id"]) is not str
        or row["receipt_project_id"] != project_id
        or type(row["receipt_task_id"]) is not str
        or row["receipt_task_id"] != task_id
        or type(row["inventory_rowid"]) is not int
    ):
        raise _review_inventory_inconsistent()
    return finding_id


def _validate_review_reference_inventory_owner(
    row: sqlite3.Row,
    *,
    project_id: str,
    task_id: str,
    source_kind: str,
    source_id_pattern: re.Pattern[str],
) -> str:
    source_id = row["source_id"]
    if (
        type(source_id) is not str
        or source_id_pattern.fullmatch(source_id) is None
        or type(row["project_id"]) is not str
        or row["project_id"] != project_id
        or type(row["task_id"]) is not str
        or row["task_id"] != task_id
        or type(row["source_kind"]) is not str
        or row["source_kind"] != source_kind
    ):
        raise _review_inventory_inconsistent()
    return source_id


def _retain_recent_candidate(
    candidates: list[tuple[str, int, Any]],
    *,
    created_at: Any,
    rowid: Any,
    value: Any,
    limit: int,
) -> None:
    if type(created_at) is not str or type(rowid) is not int:
        raise _review_inventory_inconsistent()
    candidates.append((created_at, rowid, value))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    del candidates[limit:]


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
    review_privacy_successes: set[tuple[str, str, str]] = set()
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

    required = {0: 0, 1: 1, 2: 2}[tier]
    empty_evidence = {
        "target": {
            "kind": target_kind,
            "value": target_value,
            "generation": generation,
        },
        "gate": {
            "review_tier": tier,
            "required_independent_passes": required,
            "qualifying_independent_passes": 0,
            "fallback_kind": None,
            "satisfied": False,
        },
        "counts": {
            "receipts_total": 0,
            "receipts_current_generation": 0,
            "changes_requested_current_generation": 0,
            "open_high": 0,
            "open_medium": 0,
            "open_low": 0,
        },
        "blocking_findings": [],
        "recent_receipts": [],
        "recent_findings": [],
    }
    total_receipts = 0
    current_receipts = 0
    qualifying = 0
    changes_requested = 0
    fallback_kind: str | None = None
    open_counts = {"high": 0, "medium": 0, "low": 0}
    receipt_recent_candidates: list[tuple[str, int, sqlite3.Row]] = []
    finding_recent_candidates: list[tuple[str, int, dict[str, Any]]] = []
    blocking_recent_candidates: list[tuple[str, int, dict[str, Any]]] = []
    recent_receipts: list[dict[str, Any]] = []
    receipt_inventory_cursor: sqlite3.Cursor | None = None
    receipt_reference_cursor: sqlite3.Cursor | None = None
    finding_inventory_cursor: sqlite3.Cursor | None = None
    finding_reference_cursor: sqlite3.Cursor | None = None
    legacy_base_projection = (
        "receipt.target_base_revision"
        if receipt_has_base
        else "'' AS target_base_revision"
    )
    try:
        # Open every candidate stream before consuming one so direct readers
        # retain one SQLite snapshot for source/reference closure.
        receipt_inventory_cursor = connection.execute(
            f"""
            WITH selected_task_ids(value) AS (
                VALUES (?), (CAST(? AS BLOB))
            )
            SELECT receipt.review_receipt_id, receipt.task_id,
                   receipt.project_id, receipt.reviewer_key,
                   receipt.receipt_kind, receipt.verdict,
                   receipt.target_kind, receipt.target_value,
                   {legacy_base_projection}, receipt.target_generation,
                   receipt.summary, receipt.user_approved,
                   receipt.created_at, receipt.rowid AS inventory_rowid
              FROM review_receipts AS receipt
             WHERE receipt.task_id IN (
                   SELECT value FROM selected_task_ids
             )
            """,
            (task_id, task_id),
        )
        if source_schema_version >= 18:
            receipt_reference_cursor = connection.execute(
                """
                WITH selected_project_ids(value) AS (
                    VALUES (?), (CAST(? AS BLOB))
                ),
                selected_task_ids(value) AS (
                    VALUES (?), (CAST(? AS BLOB))
                ),
                selected_source_kinds(value) AS (
                    VALUES (?), (CAST(? AS BLOB))
                )
                SELECT source_id, project_id, task_id, source_kind
                  FROM evidence_references AS reference
                 WHERE project_id IN (
                       SELECT value FROM selected_project_ids
                 )
                   AND task_id IN (
                       SELECT value FROM selected_task_ids
                 )
                   AND source_kind IN (
                       SELECT value FROM selected_source_kinds
                 )
                """,
                (
                    project_id,
                    project_id,
                    task_id,
                    task_id,
                    "review_receipt",
                    "review_receipt",
                ),
            )
        finding_inventory_cursor = connection.execute(
            """
            WITH selected_task_ids(value) AS (
                VALUES (?), (CAST(? AS BLOB))
            )
            SELECT finding.*, finding.rowid AS inventory_rowid,
                   receipt.review_receipt_id AS parent_receipt_id,
                   receipt.project_id AS receipt_project_id,
                   receipt.task_id AS receipt_task_id,
                   receipt.target_generation,
                   receipt.reviewer_key,
                   (
                     SELECT COUNT(*)
                       FROM review_receipts AS candidate
                      WHERE candidate.review_receipt_id IN (
                            finding.review_receipt_id,
                            CAST(finding.review_receipt_id AS TEXT),
                            CAST(finding.review_receipt_id AS BLOB)
                      )
                   ) AS parent_candidate_count
              FROM review_findings AS finding
              LEFT JOIN review_receipts AS receipt
                ON typeof(finding.review_receipt_id) = 'text'
               AND typeof(receipt.review_receipt_id) = 'text'
               AND receipt.review_receipt_id = finding.review_receipt_id
             WHERE receipt.review_receipt_id IS NULL
                OR receipt.task_id IN (
                   SELECT value FROM selected_task_ids
               )
            """,
            (task_id, task_id),
        )
        if source_schema_version >= 18:
            finding_reference_cursor = connection.execute(
                """
                WITH selected_project_ids(value) AS (
                    VALUES (?), (CAST(? AS BLOB))
                ),
                selected_task_ids(value) AS (
                    VALUES (?), (CAST(? AS BLOB))
                ),
                selected_source_kinds(value) AS (
                    VALUES (?), (CAST(? AS BLOB))
                )
                SELECT source_id, project_id, task_id, source_kind
                  FROM evidence_references AS reference
                 WHERE project_id IN (
                       SELECT value FROM selected_project_ids
                 )
                   AND task_id IN (
                       SELECT value FROM selected_task_ids
                 )
                   AND source_kind IN (
                       SELECT value FROM selected_source_kinds
                 )
                """,
                (
                    project_id,
                    project_id,
                    task_id,
                    task_id,
                    "review_finding",
                    "review_finding",
                ),
            )

        while True:
            chunk = receipt_inventory_cursor.fetchmany(
                COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            )
            if not chunk:
                break
            selected_receipt_ids: set[str] = set()
            for row in chunk:
                receipt_id = _validate_review_receipt_inventory_owner(
                    row,
                    project_id=project_id,
                    task_id=task_id,
                )
                if source_schema_version >= 18:
                    selected_receipt_ids.add(receipt_id)
                else:
                    validate_stored_review_receipt_projection(
                        row,
                        source_schema_version=source_schema_version,
                        _privacy_success_cache=review_privacy_successes,
                    )
            if source_schema_version >= 18:
                validate_selected_task_receipt_evidence(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    review_receipt_ids=selected_receipt_ids,
                    review_finding_ids=set(),
                    verification_receipt_ids=set(),
                )
            for row in chunk:
                total_receipts += 1
                _retain_recent_candidate(
                    receipt_recent_candidates,
                    created_at=row["created_at"],
                    rowid=row["inventory_rowid"],
                    value=row,
                    limit=recent_limit,
                )
                is_current = (
                    generation > 0
                    and row["target_kind"] == target_kind
                    and row["target_value"] == target_value
                    and row["target_base_revision"] == target_base_revision
                    and row["target_generation"] == generation
                )
                if not is_current:
                    continue
                current_receipts += 1
                if (
                    row["receipt_kind"] == "independent"
                    and row["verdict"] == "pass"
                ):
                    qualifying += 1
                if row["verdict"] == "changes_requested":
                    changes_requested += 1
                if tier in {1, 2}:
                    expected_approval = 1 if tier == 2 else 0
                    if (
                        row["receipt_kind"] == "self_review_fallback"
                        and row["verdict"] == "pass"
                        and row["user_approved"] == expected_approval
                    ):
                        fallback_kind = "self_review_fallback"
                elif (
                    row["receipt_kind"] == "not_required"
                    and row["verdict"] == "not_required"
                    and row["user_approved"] == 0
                    and row["summary"] != ""
                ):
                    fallback_kind = "not_required"

        # The already-open Reference stream pins the same snapshot while the
        # bounded recent rows obtain their v18 provenance projection.
        receipt_rows = [item[2] for item in receipt_recent_candidates]
        recent_receipts = [
            _project_stored_review_receipt(
                connection,
                row,
                source_schema_version=source_schema_version,
            )
            for row in receipt_rows
        ]

        if receipt_reference_cursor is not None:
            while True:
                chunk = receipt_reference_cursor.fetchmany(
                    COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
                )
                if not chunk:
                    break
                selected_receipt_ids = set()
                for row in chunk:
                    selected_receipt_ids.add(
                        _validate_review_reference_inventory_owner(
                            row,
                            project_id=project_id,
                            task_id=task_id,
                            source_kind="review_receipt",
                            source_id_pattern=REVIEW_RECEIPT_ID_PATTERN,
                        )
                    )
                validate_selected_task_receipt_evidence(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    review_receipt_ids=selected_receipt_ids,
                    review_finding_ids=set(),
                    verification_receipt_ids=set(),
                )

        while True:
            chunk = finding_inventory_cursor.fetchmany(
                COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            )
            if not chunk:
                break
            selected_finding_ids: set[str] = set()
            for row in chunk:
                finding_id = _validate_review_finding_inventory_owner(
                    row,
                    project_id=project_id,
                    task_id=task_id,
                )
                if source_schema_version >= 18:
                    selected_finding_ids.add(finding_id)
                else:
                    validate_stored_review_finding_projection(
                        row,
                        _privacy_success_cache=review_privacy_successes,
                    )
            if source_schema_version >= 18:
                validate_selected_task_receipt_evidence(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    review_receipt_ids=set(),
                    review_finding_ids=selected_finding_ids,
                    verification_receipt_ids=set(),
                )
            for row in chunk:
                finding = dict(row)
                finding.pop("inventory_rowid", None)
                finding.pop("parent_receipt_id", None)
                finding.pop("parent_candidate_count", None)
                finding.pop("receipt_project_id", None)
                finding.pop("receipt_task_id", None)
                severity = finding["severity"]
                status = finding["status"]
                if status == "open":
                    open_counts[severity] += 1
                _retain_recent_candidate(
                    finding_recent_candidates,
                    created_at=finding["created_at"],
                    rowid=row["inventory_rowid"],
                    value=finding,
                    limit=recent_limit,
                )
                if severity in {"high", "medium"} and (
                    status == "open"
                    or (
                        status == "resolved"
                        and finding["target_generation"] >= generation
                    )
                ):
                    _retain_recent_candidate(
                        blocking_recent_candidates,
                        created_at=finding["created_at"],
                        rowid=row["inventory_rowid"],
                        value=finding,
                        limit=recent_limit,
                    )

        if finding_reference_cursor is not None:
            while True:
                chunk = finding_reference_cursor.fetchmany(
                    COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
                )
                if not chunk:
                    break
                selected_finding_ids = set()
                for row in chunk:
                    selected_finding_ids.add(
                        _validate_review_reference_inventory_owner(
                            row,
                            project_id=project_id,
                            task_id=task_id,
                            source_kind="review_finding",
                            source_id_pattern=REVIEW_FINDING_ID_PATTERN,
                        )
                    )
                validate_selected_task_receipt_evidence(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    review_receipt_ids=set(),
                    review_finding_ids=selected_finding_ids,
                    verification_receipt_ids=set(),
                )
    except sqlite3.Error as exc:
        if is_sqlite_busy_or_locked(exc):
            raise operational_sqlite_error(
                exc,
                fallback_message="could not read review evidence",
            ) from exc
        raise _review_inventory_inconsistent() from exc
    finally:
        for cursor in (
            receipt_inventory_cursor,
            receipt_reference_cursor,
            finding_inventory_cursor,
            finding_reference_cursor,
        ):
            if cursor is not None:
                cursor.close()

    if total_receipts == 0:
        return empty_evidence

    blocking_rows = [item[2] for item in blocking_recent_candidates]
    finding_rows = [item[2] for item in finding_recent_candidates]

    blocking_findings = []
    for row in blocking_rows:
        item = dict(row)
        item["blocking_reason"] = (
            "unresolved" if row["status"] == "open" else "fresh_review_required"
        )
        blocking_findings.append(item)

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
        "recent_receipts": recent_receipts,
        "recent_findings": finding_rows,
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
