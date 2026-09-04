"""Storage path helpers for task-governance-tool."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import uuid
import weakref
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from task_governance_tool.evidence_ledger import (
    EvidenceLedgerError,
    authority_snapshot_basis_digest as canonical_authority_snapshot_basis_digest,
    contract_criterion_digest as canonical_contract_criterion_digest,
    verification_expectation_digest as canonical_verification_expectation_digest,
)
from task_governance_tool.ordering import LANE_SQL_FUNCTION, canonical_lane


PROJECT_ID_HASH_LENGTH = 12
SCHEMA_VERSION = 21
PRIVATE_SCHEMA20_VERSION = 20
PRIVATE_SCHEMA20_MIGRATION_NAME = "verification_runner_shadow"
PRIVATE_SCHEMA21_VERSION = 21
PRIVATE_SCHEMA21_MIGRATION_NAME = "verification_runner_gate_basis"
PRIVATE_SCHEMA22_VERSION = 22
PRIVATE_SCHEMA22_MIGRATION_NAME = "evidence_reservation_cleanup"
VIEWER_MIN_SOURCE_SCHEMA_VERSION = 5
STORED_TASK_VERIFICATION_LIMIT_V17 = 500
STORED_TASK_VERIFICATION_LIMIT_V18 = 1_000
SQLITE_INT64_MAX = (1 << 63) - 1
IDENTITY_SCHEMES = {"legacy_path_v1", "uuid_v1"}
BINDING_REASONS = {
    "legacy_migration",
    "fresh_setup",
    "confirmed_relocation",
}
LOWER_HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LEGACY_PROJECT_ID_PATTERN = re.compile(
    rf"^[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{{{PROJECT_ID_HASH_LENGTH}}}$"
)
UUID_PROJECT_ID_PATTERN = re.compile(r"^tg_project_([0-9a-f]{32})$")
COMPLETION_CYCLE_ID_PATTERN = re.compile(
    r"^tg_completion_cycle_[0-9a-f]{16}$"
)
VERIFICATION_RECEIPT_ID_PATTERN = re.compile(
    r"^tg_verification_receipt_[0-9a-f]{16}$"
)
ARTIFACT_MANIFEST_ID_PATTERN = re.compile(
    r"^tg_artifact_manifest_[0-9a-f]{16}$"
)
REVIEW_RECEIPT_ID_PATTERN = re.compile(
    r"^tg_review_receipt_[0-9a-f]{16}$"
)
REVIEW_FINDING_ID_PATTERN = re.compile(
    r"^tg_review_finding_[0-9a-f]{16}$"
)
EVIDENCE_REFERENCE_ID_PATTERN = re.compile(
    r"^tg_evidence_reference_[0-9a-f]{16}$"
)
VERIFICATION_RUNNER_RESOLUTION_ID_PATTERN = re.compile(
    r"^tg_verification_runner_resolution_[0-9a-f]{16}$"
)
VERIFICATION_RUNNER_ATTEMPT_ID_PATTERN = re.compile(
    r"^tg_verification_runner_attempt_[0-9a-f]{16}$"
)
VERIFICATION_RUNNER_SANDBOX_EVENT_ID_PATTERN = re.compile(
    r"^tg_verification_runner_sandbox_event_[0-9a-f]{16}$"
)
VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN = re.compile(
    r"^tg_verification_runner_observation_[0-9a-f]{16}$"
)
VERIFICATION_RUNNER_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CRITERION_EVIDENCE_LINK_ID_PATTERN = re.compile(
    r"^tg_criterion_evidence_link_[0-9a-f]{16}$"
)
COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN = re.compile(
    r"^tg_completion_evidence_bundle_[0-9a-f]{16}$"
)
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMPLETION_EVIDENCE_BUNDLE_MAX_BYTES = 16_777_216
CRITERION_EVIDENCE_RELATIONS = {
    "verification_attestation",
    "review_assessment",
    "review_finding",
    "completion_basis",
    "derived_analysis",
    "runner_observation",
}
COMPLETION_BUNDLE_MEMBER_KINDS = {
    "criterion_link",
    "evidence_reference",
}
EVIDENCE_PROJECTION_BATCH_SIZE = 400
EVIDENCE_PROJECTION_INDEX_ENTRY_LIMIT = 100_000
COMPLETION_BUNDLE_OMISSION_BITS = {
    "acceptance_criterion_absent": 1,
    "verification_criterion_absent": 2,
    "artifact_content_not_observed": 4,
    "historical_finding_reference_absent": 8,
}
EVIDENCE_PROJECTION_OUTCOMES = {"succeeded", "deferred", "failed"}
VERIFICATION_SPECIFIED_SQL_FUNCTION = "taskgov_verification_specified"
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
COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE = 400
SELECTED_TASK_AUTHORITY_VALIDATION_CHUNK_SIZE = 100
MANAGED_BACKUP_FILENAME_PATTERN = re.compile(
    r"^backups/taskgov-backup-v1_\d{8}T\d{6}Z_"
    r"[0-9a-f]{32}_r(?:[1-9]|1[0-9]|20)\.sqlite$"
)
CLEANUP_TEMP_ENTRY_PATTERNS = (
    re.compile(r"^\.taskgov-restore-[a-z0-9_]{8}\.tmp$"),
    re.compile(r"^backups/\.taskgov-backup-[a-z0-9_]{8}\.tmp$"),
    re.compile(r"^viewer/\.task-viewer-[a-z0-9_]{8}\.tmp$"),
    re.compile(r"^evidence/\.taskgov-evidence-index-[a-z0-9_]{8}\.tmp$"),
    re.compile(
        r"^evidence/bundles/\.taskgov-evidence-bundle-[a-z0-9_]{8}\.tmp$"
    ),
)
CLEANUP_EVIDENCE_BUNDLE_PATTERN = re.compile(
    r"^evidence/bundles/tg_completion_evidence_bundle_[0-9a-f]{16}\.json$"
)
CLEANUP_FIXED_ENTRIES = {
    "taskgov.sqlite",
    "backups/taskgov-backup.lock",
    "viewer/task-viewer.html",
    "viewer/taskgov-viewer.lock",
    "evidence/index.json",
    "evidence/taskgov-evidence.lock",
}
CLEANUP_INVENTORY_MAX_ENTRIES = 32
CLEANUP_INVENTORY_MAX_BYTES = 16_384
MIN_BACKUP_INTERVAL_MINUTES = 1
MAX_BACKUP_INTERVAL_MINUTES = 1_440
MIN_BACKUP_GENERATIONS = 1
MAX_BACKUP_GENERATIONS = 20
DEFAULT_BACKUP_INTERVAL_MINUTES = 30
DEFAULT_BACKUP_GENERATIONS = 3
MANAGED_BACKUP_GENERATION_PATTERN = re.compile(r"^tg_backup_[0-9a-f]{32}$")
UNSUPPORTED_JOURNAL_MODE_MESSAGE = (
    "task database uses unsupported WAL journal mode"
)
DATABASE_BUSY_MESSAGE = "task database is busy; run the command again later"
_VIEWER_TASK_BATCH_SAVEPOINT_PATTERN = re.compile(
    r"^taskgov_viewer_batch_[0-9a-f]{32}$"
)


class StorageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StoredTaskVerificationError(Exception):
    """One sanitized stored-verification rejection usable by recovery callers."""

    def __init__(self, reason: str) -> None:
        super().__init__("stored task verification is not recovery-eligible")
        self.reason = reason


def stored_task_verification_limit(source_schema_version: int) -> int:
    """Return the durable Task verification limit owned by a source schema."""

    if (
        isinstance(source_schema_version, bool)
        or not isinstance(source_schema_version, int)
        or source_schema_version < 1
        or source_schema_version > SCHEMA_VERSION
    ):
        raise StorageError(
            "project_state_unreadable",
            "task database schema version is invalid",
        )
    return (
        STORED_TASK_VERIFICATION_LIMIT_V17
        if source_schema_version <= 17
        else STORED_TASK_VERIFICATION_LIMIT_V18
    )


def validate_stored_task_verification(
    connection: sqlite3.Connection,
    source_schema_version: int,
    expected_project_id: str,
) -> None:
    """Validate stored Tasks for recovery without exposing stored bytes.

    Privacy is intentionally checked before the source-schema capacity.  Only
    those two failures for ``verification`` use
    ``StoredTaskVerificationError``; every other Task-row or query fault remains
    a structural storage failure.
    """

    from task_governance_tool.tasks import (  # Avoid the storage/tasks cycle.
        validate_stored_task_rows,
    )

    try:
        rows = connection.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc
    result = validate_stored_task_rows(
        rows,
        connection=connection,
        source_schema_version=source_schema_version,
        expected_project_id=expected_project_id,
        verification_rejection_is_local=True,
    )
    if result.verification_rejection is not None:
        raise StoredTaskVerificationError(result.verification_rejection)


def _read_validated_current_task_row(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> sqlite3.Row | None:
    """Read and validate one locked/current Task without an import cycle."""

    from task_governance_tool.tasks import (
        fetch_validated_current_task_row,
    )

    return fetch_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )


def _read_task_for_authority_capture(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> sqlite3.Row | None:
    """Validate one locked Task immediately before its authority row exists."""

    from task_governance_tool.tasks import validate_stored_task_rows

    try:
        row = connection.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
            (project_id, task_id),
        ).fetchone()
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc
    if row is not None:
        validate_stored_task_rows(
            [row],
            connection=connection,
            source_schema_version=SCHEMA_VERSION,
            expected_project_id=project_id,
        )
    return row


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: str
    canonical_repo: Path
    canonical_path_hash: str
    display_name: str


@dataclass(frozen=True)
class DatabaseTarget:
    project: ProjectIdentity
    db_path: Path
    explicit_db: bool
    binding_path_hash: str | None = None
    binding_generation: int | None = None
    skill_root: Path | None = field(default=None, repr=False, compare=False)
    backups_path: Path | None = field(default=None, repr=False, compare=False)
    viewer_path: Path | None = field(default=None, repr=False, compare=False)
    canonical_fixed: bool = False
    evidence_root: Path | None = field(default=None, repr=False, compare=False)
    evidence_index: Path | None = field(default=None, repr=False, compare=False)
    evidence_bundles: Path | None = field(default=None, repr=False, compare=False)
    evidence_lock: Path | None = field(default=None, repr=False, compare=False)
    verification_runner_root: Path | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def resolved_backups_path(self) -> Path:
        return self.backups_path or (self.db_path.parent / "backups")

    @property
    def resolved_viewer_path(self) -> Path:
        return self.viewer_path or (
            self.db_path.parent / "viewer" / "task-viewer.html"
        )

    @property
    def resolved_evidence_root(self) -> Path:
        return self.evidence_root or (self.db_path.parent / "evidence")

    @property
    def resolved_evidence_index(self) -> Path:
        return self.evidence_index or (
            self.resolved_evidence_root / "index.json"
        )

    @property
    def resolved_evidence_bundles(self) -> Path:
        return self.evidence_bundles or (
            self.resolved_evidence_root / "bundles"
        )

    @property
    def resolved_evidence_lock(self) -> Path:
        return self.evidence_lock or (
            self.resolved_evidence_root / "taskgov-evidence.lock"
        )

    @property
    def resolved_verification_runner_root(self) -> Path:
        return self.verification_runner_root or (
            self.db_path.parent / "verification-runner"
        )


class _ValidatedViewerTaskBatch:
    """One-shot proof that current Evidence storage validated every Task."""

    __slots__ = ("__weakref__",)


@dataclass(frozen=True)
class _ViewerTaskBatchIssuance:
    connection: sqlite3.Connection = field(repr=False, compare=False)
    project_id: str
    source_schema_version: int
    task_ids: tuple[str, ...]
    task_count: int
    data_version: int
    savepoint_name: str = field(repr=False, compare=False)


_VIEWER_TASK_BATCH_ISSUANCES: dict[
    int,
    tuple[
        weakref.ReferenceType[_ValidatedViewerTaskBatch],
        _ViewerTaskBatchIssuance,
    ],
] = {}
_VIEWER_TASK_BATCH_ISSUANCE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ViewerSnapshotDatabaseValidation:
    source_schema_version: int
    validated_task_batch: _ValidatedViewerTaskBatch | None = field(
        default=None,
        repr=False,
        compare=False,
    )


def _register_validated_viewer_task_batch(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    source_schema_version: int,
    task_ids: tuple[str, ...],
    task_count: int,
    data_version: int,
    savepoint_name: str,
) -> _ValidatedViewerTaskBatch:
    batch = _ValidatedViewerTaskBatch()
    batch_id = id(batch)
    issuance = _ViewerTaskBatchIssuance(
        connection=connection,
        project_id=project_id,
        source_schema_version=source_schema_version,
        task_ids=task_ids,
        task_count=task_count,
        data_version=data_version,
        savepoint_name=savepoint_name,
    )

    def discard(reference: weakref.ReferenceType[_ValidatedViewerTaskBatch]) -> None:
        with _VIEWER_TASK_BATCH_ISSUANCE_LOCK:
            current = _VIEWER_TASK_BATCH_ISSUANCES.get(batch_id)
            if current is not None and current[0] is reference:
                _VIEWER_TASK_BATCH_ISSUANCES.pop(batch_id, None)

    reference = weakref.ref(batch, discard)
    with _VIEWER_TASK_BATCH_ISSUANCE_LOCK:
        _VIEWER_TASK_BATCH_ISSUANCES[batch_id] = (reference, issuance)
    return batch


@dataclass(frozen=True)
class UnboundDatabaseTarget:
    canonical_repo: Path
    canonical_path_hash: str
    display_name: str
    db_path: Path
    explicit_db: bool = True
    skill_root: Path | None = field(default=None, repr=False, compare=False)
    backups_path: Path | None = field(default=None, repr=False, compare=False)
    viewer_path: Path | None = field(default=None, repr=False, compare=False)
    canonical_fixed: bool = False
    evidence_root: Path | None = field(default=None, repr=False, compare=False)
    evidence_index: Path | None = field(default=None, repr=False, compare=False)
    evidence_bundles: Path | None = field(default=None, repr=False, compare=False)
    evidence_lock: Path | None = field(default=None, repr=False, compare=False)
    verification_runner_root: Path | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def resolved_evidence_root(self) -> Path:
        return self.evidence_root or (self.db_path.parent / "evidence")

    @property
    def resolved_evidence_index(self) -> Path:
        return self.evidence_index or (
            self.resolved_evidence_root / "index.json"
        )

    @property
    def resolved_evidence_bundles(self) -> Path:
        return self.evidence_bundles or (
            self.resolved_evidence_root / "bundles"
        )

    @property
    def resolved_evidence_lock(self) -> Path:
        return self.evidence_lock or (
            self.resolved_evidence_root / "taskgov-evidence.lock"
        )

    @property
    def resolved_verification_runner_root(self) -> Path:
        return self.verification_runner_root or (
            self.db_path.parent / "verification-runner"
        )


@dataclass(frozen=True)
class InitResult:
    target: DatabaseTarget
    created: bool
    migrations_applied: list[int]
    schema_version: int
    warnings: list[dict[str, str]]


@dataclass(frozen=True)
class MigrationBackupMetadata:
    generation_id: str
    published_at: str
    publication_retention: int


@dataclass(frozen=True)
class ManagedBackupRepositoryState:
    maintenance: ProjectMaintenanceState
    generations: tuple[MigrationBackupMetadata, ...]


@dataclass(frozen=True)
class SetupStorageState:
    schema_version: int | None
    needs_initialize: bool
    needs_migration: bool
    maintenance_enabled: bool
    backup_interval_minutes: int | None
    backup_generations: int | None


@dataclass(frozen=True)
class ProjectMaintenanceState:
    project_id: str
    enabled_at: str | None
    backup_interval_minutes: int | None
    backup_generations: int | None
    applied_backup_generations: int | None
    backup_last_success_at: str | None
    backup_last_outcome_code: str | None
    backup_last_outcome_at: str | None
    latest_backup_generation_id: str | None
    viewer_last_success_at: str | None
    viewer_last_outcome_code: str | None
    viewer_last_outcome_at: str | None

    @property
    def enabled(self) -> bool:
        return self.enabled_at is not None


@dataclass(frozen=True)
class ViewerMaintenanceState:
    project_id: str
    source_generation: int
    rendered_generation: int | None
    last_success_at: str | None
    last_outcome_code: str | None
    last_outcome_at: str | None

    @property
    def due(self) -> bool:
        return (
            self.rendered_generation is None
            or self.rendered_generation < self.source_generation
            or self.last_outcome_code in {"deferred", "failed"}
        )


@dataclass(frozen=True)
class DoctorStorageState:
    schema_version: int
    project_code: str
    task_counts: dict[str, int]
    maintenance: ProjectMaintenanceState
    viewer: ViewerMaintenanceState
    evidence: EvidenceProjectionState


@dataclass(frozen=True)
class ProjectBindingState:
    project_id: str
    identity_scheme: str
    binding_generation: int
    canonical_path_hash: str
    display_name: str
    binding_reason: str
    binding_updated_at: str
    legacy_cleanup_pending: bool
    legacy_cleanup_inventory: str | None
    legacy_cleanup_fingerprint: str | None


@dataclass(frozen=True)
class ProjectPathBinding:
    project_id: str
    binding_generation: int
    previous_path_hash: str | None
    canonical_path_hash: str
    display_name: str
    reason: str
    confirmation_token_digest: str | None
    bound_at: str


@dataclass(frozen=True)
class CompletionGateBasis:
    version: int
    kind: str
    required_independent_passes: int | None
    qualifying_independent_passes: int | None
    changes_requested_count: int | None
    open_high_count: int | None
    open_medium_count: int | None
    fresh_review_required_count: int | None
    qualifying_receipt_ids: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class CompletionCycle:
    completion_cycle_id: str
    project_id: str
    task_id: str
    saved_cycle_ordinal: int
    origin: str
    completeness: str
    completed_at: str | None
    recorded_at: str
    contract_revision: int
    review_tier: int
    verification_expectation: str
    verification_attestation: bool | None
    completion_evidence_kind: str
    completion_evidence_revision: str = field(repr=False)
    completion_evidence_reason: str = field(repr=False)
    external_revision_approved: bool
    completion_commit_required: bool
    completion_commit_hash: str = field(repr=False)
    review_target_kind: str
    review_target_value: str = field(repr=False)
    review_target_base_revision: str = field(repr=False)
    review_target_generation: int
    gate_basis: CompletionGateBasis
    verification_basis_version: int = 0
    verification_expectation_digest: str | None = field(
        default=None,
        repr=False,
    )
    verification_receipt_id: str | None = None
    verification_subject_basis_version: int = 0
    subject_authority_snapshot_id: str | None = None
    subject_verification_criterion_id: str | None = None
    evidence_basis_version: int = 0
    completion_evidence_bundle_id: str | None = None
    verification_basis_kind: str | None = None
    verification_runner_observation_id: str | None = None


@dataclass(frozen=True)
class NativeCompletionIdentity:
    completion_cycle_id: str
    saved_cycle_ordinal: int
    completion_evidence_bundle_id: str


@dataclass(frozen=True)
class PreparedCriterionEvidenceLink:
    criterion_evidence_link_id: str
    project_id: str
    task_id: str
    criterion_id: str
    evidence_reference_id: str
    relation: str
    assurance_class: str
    producer_class: str
    producer_version: int
    created_at: str


@dataclass(frozen=True)
class VerificationRunnerResolution:
    verification_runner_resolution_id: str
    project_id: str
    task_id: str
    contract_revision: int
    authority_snapshot_id: str
    verification_criterion_id: str
    verification_expectation_digest: str
    verification_criterion_digest: str
    target_kind: str
    target_value: str
    target_base_revision: str | None
    target_generation: int
    target_capture_version: int
    artifact_manifest_id: str
    target_material_digest: str | None
    plan_state: str
    plan_blob_object_id: str | None
    plan_raw_digest: str | None
    plan_id: str | None
    plan_version: int | None
    plan_semantic_digest: str | None
    selected_entry_digest: str | None
    coverage: str
    step_count: int
    runner_contract_version: int
    runner_implementation_version: str
    runner_implementation_digest: str
    runner_policy_digest: str
    runtime_digest: str | None
    gate_eligibility_version: int
    trigger: str
    route: str
    reason: str | None
    idempotency_digest: str
    created_at: str


@dataclass(frozen=True)
class VerificationRunnerAttempt:
    verification_runner_attempt_id: str
    project_id: str
    task_id: str
    target_generation: int
    gate_eligibility_version: int
    verification_runner_resolution_id: str
    target_material_digest: str
    runner_implementation_digest: str
    attempt_digest: str
    intent_recorded_at: str


@dataclass(frozen=True)
class VerificationRunnerObservation:
    verification_runner_observation_id: str
    project_id: str
    task_id: str
    target_generation: int
    gate_eligibility_version: int
    verification_runner_resolution_id: str
    verification_runner_attempt_id: str | None
    runner_implementation_digest: str
    route: str
    launch_state: str
    outcome: str
    reason: str | None
    complete_plan: int
    total_step_count: int
    completed_step_count: int
    failed_step_ordinal: int | None
    started_at: str
    finished_at: str
    duration_ms: int
    cpu_time_ms: int | None
    peak_job_memory_bytes: int | None
    total_process_count: int | None
    sanitized_result_digest: str
    created_at: str


@dataclass(frozen=True)
class VerificationRunnerSandboxEvent:
    verification_runner_sandbox_event_id: str
    project_id: str
    task_id: str
    target_generation: int
    verification_runner_attempt_id: str
    event_kind: str
    event_digest: str
    terminal_observation_id: str | None
    created_at: str


@dataclass(frozen=True)
class PreparedCompletionBundleMember:
    project_id: str
    task_id: str
    completion_evidence_bundle_id: str
    member_kind: str
    ordinal: int
    criterion_evidence_link_id: str | None
    evidence_reference_id: str | None


@dataclass(frozen=True)
class PreparedCompletionFindingSnapshot:
    project_id: str
    task_id: str
    completion_evidence_bundle_id: str
    ordinal: int
    review_finding_id: str
    review_receipt_id: str
    target_generation: int
    severity: str
    summary: str
    status: str
    resolution_summary: str
    created_at: str
    resolved_at: str | None
    evidence_reference_id: str | None
    assurance_class: str
    producer_class: str
    producer_version: int
    digest: str


@dataclass(frozen=True)
class PreparedCompletionEvidenceBundle:
    completion_evidence_bundle_id: str
    project_id: str
    task_id: str
    completion_cycle_id: str
    cycle_ordinal: int
    source_schema_version: int
    bundle_version: int
    contract_revision: int
    authority_snapshot_id: str
    acceptance_criterion_id: str | None
    verification_criterion_id: str | None
    target_kind: str
    target_value: str
    target_base_revision: str
    target_generation: int
    target_capture_version: int
    artifact_manifest_id: str
    verification_receipt_id: str | None
    verification_basis_kind: str | None
    verification_runner_observation_id: str | None
    omission_mask: int
    sealed_at: str
    bundle_digest: str
    payload_size_bytes: int
    criterion_links: tuple[PreparedCriterionEvidenceLink, ...]
    members: tuple[PreparedCompletionBundleMember, ...]
    finding_snapshots: tuple[PreparedCompletionFindingSnapshot, ...]


@dataclass(frozen=True)
class EvidenceProjectionState:
    project_id: str
    source_generation: int
    published_generation: int | None
    index_digest: str | None
    last_success_at: str | None
    last_outcome_code: str | None
    last_outcome_at: str | None

    @property
    def due(self) -> bool:
        return (
            self.published_generation is None
            or self.published_generation < self.source_generation
            or self.last_outcome_code in {"deferred", "failed"}
        )


@dataclass(frozen=True)
class NativeCompletionBundleBasis:
    task: dict[str, Any]
    authority_snapshot: dict[str, Any]
    criteria: tuple[dict[str, Any], ...]
    artifact_manifest: dict[str, Any]
    artifact_entries: tuple[dict[str, Any], ...]
    artifact_reference: dict[str, Any]
    verification_receipt: dict[str, Any] | None
    verification_reference: dict[str, Any] | None
    runner_observation: dict[str, Any] | None
    runner_reference: dict[str, Any] | None
    runner_criterion_link: dict[str, Any] | None
    review_receipts: tuple[dict[str, Any], ...]
    review_references: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]
    finding_references: tuple[dict[str, Any] | None, ...]
    completion_reference: dict[str, Any]


@dataclass(frozen=True)
class EvidenceProjectionBasis:
    source_schema_version: int
    project_id: str
    source_generation: int
    cycles: tuple[CompletionCycle, ...]
    bundles: tuple[PreparedCompletionEvidenceBundle, ...]
    native_bundles: tuple[ProjectionBundleRecord, ...]


@dataclass(frozen=True)
class ProjectionBundleRecord:
    bundle: PreparedCompletionEvidenceBundle
    cycle: CompletionCycle
    task: dict[str, Any]
    authority_snapshot: dict[str, Any]
    criteria: tuple[dict[str, Any], ...]
    artifact_manifest: dict[str, Any]
    artifact_entries: tuple[dict[str, Any], ...]
    evidence_references: tuple[dict[str, Any], ...]
    verification_receipt: dict[str, Any] | None
    runner_observation: dict[str, Any] | None
    review_receipts: tuple[dict[str, Any], ...]
    finding_snapshots: tuple[PreparedCompletionFindingSnapshot, ...]


@dataclass(frozen=True)
class CompletionHistory:
    total: int
    legacy_history_incomplete: bool
    cycles: tuple[CompletionCycle, ...]

    @property
    def returned_count(self) -> int:
        return len(self.cycles)

    @property
    def truncated(self) -> bool:
        return self.returned_count < self.total


@dataclass(frozen=True)
class VerificationReceiptSnapshot:
    total: int
    same_generation: tuple[dict[str, Any], ...]
    exact_current: tuple[dict[str, Any], ...]
    recent: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AuthoritySnapshotBinding:
    authority_snapshot_id: str
    generation: int
    acceptance_criterion_id: str | None
    verification_criterion_id: str | None


def skill_root_from_script(script_path: str | os.PathLike[str]) -> Path:
    script = Path(script_path).resolve()
    return script.parent.parent


def lexical_skill_root_from_script(script_path: str | os.PathLike[str]) -> Path:
    script = Path(script_path).expanduser()
    if not script.is_absolute():
        script = Path.cwd() / script
    return script.absolute().parent.parent


def _linked_install_path_candidates(script_path: Path) -> tuple[Path, ...]:
    skill_root = script_path.parent.parent
    candidates = [script_path, script_path.parent, skill_root]
    if (
        skill_root.name.casefold() == "task-governance-tool"
        and skill_root.parent.name.casefold() == "skills"
        and skill_root.parent.parent.name.casefold() == ".agents"
    ):
        candidates.extend((skill_root.parent, skill_root.parent.parent))
    return tuple(candidates)


def uses_unsupported_linked_install(script_path: str | os.PathLike[str]) -> bool:
    script = Path(script_path).expanduser()
    if not script.is_absolute():
        script = Path.cwd() / script
    script = script.absolute()
    try:
        for candidate in _linked_install_path_candidates(script):
            if candidate.is_symlink():
                return True
            is_junction = getattr(candidate, "is_junction", None)
            if is_junction is not None and is_junction():
                return True
        return False
    except (OSError, RuntimeError):
        return True


def canonicalize_repo(repo: str | os.PathLike[str]) -> Path:
    return Path(repo).expanduser().resolve(strict=False)


def normalized_path_for_hash(path: Path) -> str:
    normalized = str(path)
    if os.name == "nt":
        normalized = normalized.replace("/", "\\")
        normalized = os.path.normcase(normalized)
    return normalized


def sanitize_project_basename(name: str) -> str:
    sanitized = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    return sanitized or "project"


def sanitize_project_display_name(name: str) -> str:
    """Return the bounded, non-authoritative display form used by schema v14."""
    if not isinstance(name, str):
        name = ""
    sanitized = "".join(
        "\ufffd"
        if (
            ord(character) < 0x20
            or 0x7F <= ord(character) <= 0x9F
            or character in {"\u2028", "\u2029"}
        )
        else character
        for character in name
    )
    return (sanitized or "project")[:200]


def project_identity(repo: str | os.PathLike[str]) -> ProjectIdentity:
    canonical = canonicalize_repo(repo)
    hash_input = normalized_path_for_hash(canonical)
    digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
    raw_display_name = canonical.name or "project"
    display_name = sanitize_project_display_name(raw_display_name)
    prefix = sanitize_project_basename(raw_display_name)
    project_id = f"{prefix}-{digest[:PROJECT_ID_HASH_LENGTH]}"
    return ProjectIdentity(
        project_id=project_id,
        canonical_repo=canonical,
        canonical_path_hash=digest,
        display_name=display_name,
    )


def default_db_path(skill_root: str | os.PathLike[str], project_id: str) -> Path:
    return Path(skill_root).resolve() / "state" / "projects" / project_id / "taskgov.sqlite"


def default_viewer_output_path(
    skill_root: str | os.PathLike[str],
    project_id: str,
) -> Path:
    return (
        Path(skill_root).resolve()
        / "state"
        / "projects"
        / project_id
        / "viewer"
        / "task-viewer.html"
    )


def resolve_database_target(
    *,
    repo: str | os.PathLike[str],
    db: str | os.PathLike[str] | None,
    script_path: str | os.PathLike[str],
) -> DatabaseTarget:
    project = project_identity(repo)
    if db:
        db_path = Path(db).expanduser().resolve(strict=False)
        explicit_db = True
    else:
        db_path = default_db_path(skill_root_from_script(script_path), project.project_id)
        explicit_db = False
    evidence_root = db_path.parent / "evidence"
    return DatabaseTarget(
        project=project,
        db_path=db_path,
        evidence_root=evidence_root,
        evidence_index=evidence_root / "index.json",
        evidence_bundles=evidence_root / "bundles",
        evidence_lock=evidence_root / "taskgov-evidence.lock",
        verification_runner_root=db_path.parent / "verification-runner",
        explicit_db=explicit_db,
    )


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _verification_expectation_digest(exact_text: str) -> str:
    if not isinstance(exact_text, str):
        raise TypeError("verification expectation must be text")
    try:
        return canonical_verification_expectation_digest(exact_text)
    except EvidenceLedgerError as exc:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        ) from exc


def verification_expectation_digest(exact_text: str) -> str:
    """Return the v1 domain-separated digest for exact stored verification text."""

    return _verification_expectation_digest(exact_text)


def contract_criterion_digest(kind: str, exact_text: str) -> str:
    try:
        return canonical_contract_criterion_digest(kind, exact_text)
    except EvidenceLedgerError as exc:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        ) from exc


def authority_snapshot_basis_digest(values: dict[str, Any]) -> str:
    try:
        return canonical_authority_snapshot_basis_digest(values)
    except EvidenceLedgerError as exc:
        raise StorageError(
            "evidence_ledger_inconsistent",
            "stored evidence ledger is inconsistent",
        ) from exc


def verification_expectation_is_specified(exact_text: object) -> int:
    """Mirror the public Python whitespace classification inside SQLite."""

    return int(isinstance(exact_text, str) and bool(exact_text.strip()))


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
        from task_governance_tool.tasks import TaskValidationError, validate_text

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


def _completion_cycle_matches_exact_verification(
    cycle: CompletionCycle,
    exact_text: object,
) -> bool:
    if not isinstance(exact_text, str):
        return False
    return (
        cycle.verification_expectation_digest
        == _verification_expectation_digest(exact_text)
        and cycle.verification_expectation
        == ("specified" if exact_text.strip() else "unspecified")
    )


def validate_utc_timestamp(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        value,
    ):
        raise StorageError("internal_error", f"{field} is not a canonical UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise StorageError(
            "internal_error",
            f"{field} is not a canonical UTC timestamp",
        ) from exc
    return value


def _unreadable_project_state() -> StorageError:
    return StorageError(
        "project_state_unreadable",
        "project state could not be read safely",
    )


def validate_sqlite_integer_storage_class(value: object) -> int:
    """Require an exact SQLite INTEGER value without coercing its storage class."""

    if type(value) is not int:
        raise _unreadable_project_state()
    return value


def validate_lower_hex_64(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not LOWER_HEX_64_PATTERN.fullmatch(value):
        raise StorageError("internal_error", f"{field} is invalid")
    return value


def validate_project_display_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or sanitize_project_display_name(value) != value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        raise StorageError("internal_error", "project display name is invalid")
    return value


def validate_identity_project_id(project_id: str, identity_scheme: str) -> str:
    if identity_scheme not in IDENTITY_SCHEMES:
        raise StorageError("internal_error", "project identity scheme is invalid")
    if identity_scheme == "legacy_path_v1":
        if not isinstance(project_id, str) or not LEGACY_PROJECT_ID_PATTERN.fullmatch(
            project_id
        ):
            raise StorageError("internal_error", "legacy project identity is invalid")
        return project_id
    if not isinstance(project_id, str):
        raise StorageError("internal_error", "UUID project identity is invalid")
    match = UUID_PROJECT_ID_PATTERN.fullmatch(project_id)
    if match is None:
        raise StorageError("internal_error", "UUID project identity is invalid")
    raw_uuid = match.group(1)
    if raw_uuid[12] != "4" or raw_uuid[16] not in {"8", "9", "a", "b"}:
        raise StorageError("internal_error", "UUID project identity is invalid")
    return project_id


def validate_binding_generation(value: object) -> int:
    if (
        type(value) is not int
        or not 1 <= value <= SQLITE_INT64_MAX
    ):
        raise StorageError("internal_error", "project binding generation is invalid")
    return value


def validate_viewer_generation(value: object, *, field: str) -> int:
    if (
        type(value) is not int
        or not 0 <= value <= SQLITE_INT64_MAX
    ):
        raise StorageError("internal_error", f"{field} is invalid")
    return value


def _recognized_cleanup_entry(value: str) -> bool:
    return (
        value in CLEANUP_FIXED_ENTRIES
        or MANAGED_BACKUP_FILENAME_PATTERN.fullmatch(value) is not None
        or CLEANUP_EVIDENCE_BUNDLE_PATTERN.fullmatch(value) is not None
        or any(pattern.fullmatch(value) is not None for pattern in CLEANUP_TEMP_ENTRY_PATTERNS)
    )


def validate_cleanup_inventory(
    inventory: str,
    fingerprint: str,
) -> tuple[str, str]:
    if (
        not isinstance(inventory, str)
        or not 1 <= len(inventory.encode("utf-8")) <= CLEANUP_INVENTORY_MAX_BYTES
        or not inventory.isascii()
    ):
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            inventory,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeError, ValueError) as exc:
        raise StorageError(
            "internal_error",
            "legacy cleanup inventory is invalid",
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"entries", "v"}
        or type(payload.get("v")) is not int
        or payload.get("v") != 1
    ):
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    entries = payload.get("entries")
    if (
        not isinstance(entries, list)
        or not 1 <= len(entries) <= CLEANUP_INVENTORY_MAX_ENTRIES
    ):
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    names: list[str] = []
    backup_count = 0
    temporary_counts = [0] * len(CLEANUP_TEMP_ENTRY_PATTERNS)
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "kind",
            "name",
            "sha256",
            "size",
        }:
            raise StorageError(
                "internal_error",
                "legacy cleanup inventory is invalid",
            )
        name = entry.get("name")
        size = entry.get("size")
        if (
            entry.get("kind") != "file"
            or not isinstance(name, str)
            or not _recognized_cleanup_entry(name)
            or not isinstance(entry.get("sha256"), str)
            or LOWER_HEX_64_PATTERN.fullmatch(entry["sha256"]) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise StorageError(
                "internal_error",
                "legacy cleanup inventory is invalid",
            )
        names.append(name)
        if MANAGED_BACKUP_FILENAME_PATTERN.fullmatch(name) is not None:
            backup_count += 1
        for index, pattern in enumerate(CLEANUP_TEMP_ENTRY_PATTERNS):
            if pattern.fullmatch(name) is not None:
                temporary_counts[index] += 1
    if (
        len(set(names)) != len(names)
        or names != sorted(names, key=lambda value: value.encode("utf-8"))
        or backup_count > 21
        or any(count > 1 for count in temporary_counts)
    ):
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if inventory != canonical:
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    validate_lower_hex_64(fingerprint, field="legacy cleanup fingerprint")
    if hashlib.sha256(inventory.encode("ascii")).hexdigest() != fingerprint:
        raise StorageError("internal_error", "legacy cleanup fingerprint is invalid")
    return inventory, fingerprint


def validate_migration_backup_metadata(
    metadata: MigrationBackupMetadata,
) -> MigrationBackupMetadata:
    if not isinstance(metadata, MigrationBackupMetadata):
        raise StorageError("internal_error", "setup backup metadata is invalid")
    if not MANAGED_BACKUP_GENERATION_PATTERN.fullmatch(metadata.generation_id):
        raise StorageError("internal_error", "setup backup generation identity is invalid")
    validate_utc_timestamp(metadata.published_at, field="setup backup publication time")
    if (
        isinstance(metadata.publication_retention, bool)
        or not isinstance(metadata.publication_retention, int)
        or not MIN_BACKUP_GENERATIONS
        <= metadata.publication_retention
        <= MAX_BACKUP_GENERATIONS
    ):
        raise StorageError("internal_error", "setup backup retention is invalid")
    return metadata


def validate_managed_backup_metadata_set(
    metadata_items: tuple[MigrationBackupMetadata, ...],
) -> tuple[MigrationBackupMetadata, ...]:
    validated = tuple(
        sorted(
            (
                validate_migration_backup_metadata(metadata)
                for metadata in metadata_items
            ),
            key=lambda metadata: (
                metadata.published_at,
                metadata.generation_id,
            ),
        )
    )
    if len({metadata.generation_id for metadata in validated}) != len(validated):
        raise StorageError(
            "internal_error",
            "managed backup metadata contains duplicate identities",
        )
    return validated


def configure_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.row_factory = sqlite3.Row
    connection.create_function(
        LANE_SQL_FUNCTION,
        1,
        canonical_lane,
        deterministic=True,
    )
    connection.create_function(
        VERIFICATION_SPECIFIED_SQL_FUNCTION,
        1,
        verification_expectation_is_specified,
        deterministic=True,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def is_sqlite_busy_or_locked(exc: sqlite3.Error) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int):
        return (error_code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def operational_sqlite_error(
    exc: sqlite3.Error,
    *,
    fallback_message: str,
) -> StorageError:
    if is_sqlite_busy_or_locked(exc):
        return StorageError("database_busy", DATABASE_BUSY_MESSAGE)
    return StorageError("internal_error", fallback_message)


def stored_task_sqlite_error(exc: sqlite3.Error) -> StorageError:
    """Map Task-row fetch/decode faults without weakening busy semantics."""

    if is_sqlite_busy_or_locked(exc):
        return StorageError("database_busy", DATABASE_BUSY_MESSAGE)
    return StorageError(
        "project_state_unreadable",
        "project state could not be read safely",
    )


def connect(db_path: Path) -> sqlite3.Connection:
    return configure_connection(sqlite3.connect(db_path))


def connect_existing(db_path: Path) -> sqlite3.Connection:
    """Open an existing database read/write without allowing SQLite to create it."""
    uri = db_path.resolve(strict=False).as_uri() + "?mode=rw"
    return configure_connection(sqlite3.connect(uri, uri=True))


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open one lock-respecting query-only read transaction."""
    validate_operational_journal_state(db_path)
    if not db_path.exists():
        raise StorageError(
            "db_not_initialized",
            "project state is not set up; run setup first",
        )
    uri = db_path.resolve(strict=False).as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open database",
        ) from exc
    try:
        configure_connection(connection)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
    except sqlite3.Error as exc:
        connection.close()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open database",
        ) from exc
    except Exception:
        connection.close()
        raise
    return connection


def connect_snapshot_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a Viewer-compatible point-in-time read transaction."""
    return connect_readonly(db_path)


def sqlite_sidecar_paths(db_path: Path) -> list[Path]:
    return [Path(str(db_path) + suffix) for suffix in ("-wal", "-shm")]


def existing_sqlite_sidecars(db_path: Path) -> list[Path]:
    return [
        path
        for path in sqlite_sidecar_paths(db_path)
        if os.path.lexists(path)
    ]


def sqlite_header_uses_wal(db_path: Path) -> bool:
    """Inspect SQLite's stable file-header journal bytes without opening SQLite."""
    try:
        with db_path.open("rb") as stream:
            header = stream.read(20)
    except OSError as exc:
        raise StorageError("internal_error", "could not inspect database journal mode") from exc
    if len(header) < 20 or header[:16] != b"SQLite format 3\x00":
        return False
    journal_versions = (header[18], header[19])
    if any(version not in {1, 2} for version in journal_versions):
        return False
    return 2 in journal_versions


def validate_operational_journal_state(db_path: Path) -> None:
    """Reject persistent WAL state before any operational SQLite access."""
    if existing_sqlite_sidecars(db_path):
        raise StorageError(
            "unsupported_journal_mode",
            UNSUPPORTED_JOURNAL_MODE_MESSAGE,
        )
    if not db_path.exists():
        return
    if sqlite_header_uses_wal(db_path):
        raise StorageError(
            "unsupported_journal_mode",
            UNSUPPORTED_JOURNAL_MODE_MESSAGE,
        )


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return column_name in {str(row["name"]) for row in rows}


def current_schema_version(connection: sqlite3.Connection) -> int:
    if not table_exists(connection, "schema_migrations"):
        return 0
    row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    if row is None or row["version"] is None:
        return 0
    return int(row["version"])


def missing_migration_versions(
    connection: sqlite3.Connection,
    version: int,
) -> list[int]:
    if version <= 0 or not table_exists(connection, "schema_migrations"):
        return list(range(1, max(version, 0) + 1))
    rows = connection.execute(
        "SELECT version FROM schema_migrations WHERE version BETWEEN 1 AND ?",
        (version,),
    ).fetchall()
    present = {int(row["version"]) for row in rows}
    return [candidate for candidate in range(1, version + 1) if candidate not in present]


def initial_schema_sql() -> str:
    return """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_meta (
  project_id TEXT PRIMARY KEY,
  canonical_path_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('sequential', 'optional')),
  lane TEXT NOT NULL DEFAULT '',
  lane_order INTEGER,
  priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  status TEXT NOT NULL CHECK (status IN (
    'ready',
    'in_progress',
    'blocked',
    'review_pending',
    'done',
    'cancelled'
  )),
  blocked_reason TEXT NOT NULL DEFAULT '',
  review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
  verification TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  CHECK (status != 'blocked' OR blocked_reason != ''),
  CHECK (kind != 'sequential' OR lane != ''),
  CHECK (kind != 'sequential' OR lane_order IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS task_events (
  task_event_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS tool_events (
  tool_event_id TEXT PRIMARY KEY,
  project_id TEXT,
  command TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_project_kind ON tasks(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_tasks_project_lane_order ON tasks(project_id, lane, lane_order);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_project_lane_order_unique
  ON tasks(project_id, lane, lane_order)
  WHERE kind = 'sequential';
CREATE INDEX IF NOT EXISTS idx_task_events_task_created ON task_events(task_id, created_at);
"""


def completion_commit_schema_sql() -> str:
    return """
CREATE INDEX IF NOT EXISTS idx_tasks_project_completion_commit
  ON tasks(project_id, completion_commit_hash)
  WHERE completion_commit_hash != '';
"""


def paused_tasks_schema_sql() -> str:
    return """
CREATE TABLE tasks_v3 (
  task_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('sequential', 'optional')),
  lane TEXT NOT NULL DEFAULT '',
  lane_order INTEGER,
  priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  status TEXT NOT NULL CHECK (status IN (
    'ready',
    'in_progress',
    'paused',
    'blocked',
    'review_pending',
    'done',
    'cancelled'
  )),
  blocked_reason TEXT NOT NULL DEFAULT '',
  pause_reason TEXT NOT NULL DEFAULT '',
  review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
  verification TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  completion_commit_required INTEGER NOT NULL DEFAULT 1
    CHECK (completion_commit_required IN (0, 1)),
  completion_commit_hash TEXT NOT NULL DEFAULT '',
  CHECK (status != 'blocked' OR blocked_reason != ''),
  CHECK (
    (status = 'paused' AND pause_reason != '') OR
    (status != 'paused' AND pause_reason = '')
  ),
  CHECK (kind != 'sequential' OR lane != ''),
  CHECK (kind != 'sequential' OR lane_order IS NOT NULL)
);
"""


def create_task_indexes(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE INDEX idx_tasks_project_status ON tasks(project_id, status)")
    connection.execute("CREATE INDEX idx_tasks_project_kind ON tasks(project_id, kind)")
    connection.execute(
        "CREATE INDEX idx_tasks_project_lane_order ON tasks(project_id, lane, lane_order)"
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX idx_tasks_project_lane_order_unique
          ON tasks(project_id, lane, lane_order)
          WHERE kind = 'sequential'
        """
    )
    connection.execute(
        """
        CREATE INDEX idx_tasks_project_completion_commit
          ON tasks(project_id, completion_commit_hash)
          WHERE completion_commit_hash != ''
        """
    )


def empty_counts() -> dict[str, int]:
    return {
        "active": 0,
        "paused": 0,
        "blocked": 0,
        "review_pending": 0,
        "done": 0,
        "next_actionable": 0,
        "handoff_pending": 0,
    }


_SCHEMA_TABLE_INTRODUCED_VERSION = {
    "schema_migrations": 1,
    "project_meta": 1,
    "tasks": 1,
    "task_events": 1,
    "tool_events": 1,
    "review_receipts": 5,
    "review_findings": 5,
    "handoff_records": 7,
    "task_contract_revisions": 8,
    "task_effort_activity": 9,
    "task_effort_bases": 9,
    "project_maintenance": 10,
    "managed_backup_generations": 11,
    "task_checkpoints": 12,
    "viewer_maintenance_state": 13,
    "project_path_binding_history": 14,
    "task_completion_cycles": 15,
    "verification_receipts": 17,
    "authority_snapshots": 18,
    "contract_criteria": 18,
    "authority_snapshot_criteria": 18,
    "review_receipt_provenance": 18,
    "review_receipt_provenance_codes": 18,
    "artifact_manifests": 18,
    "artifact_manifest_entries": 18,
    "evidence_references": 18,
    "criterion_evidence_links": 19,
    "completion_evidence_bundles": 19,
    "completion_bundle_members": 19,
    "completion_bundle_finding_snapshots": 19,
    "evidence_projection_state": 19,
    "verification_runner_resolutions": 20,
    "verification_runner_attempts": 20,
    "verification_runner_sandbox_events": 20,
    "verification_runner_observations": 20,
}

_SCHEMA_INDEX_INTRODUCED_VERSION = {
    "idx_tasks_project_status": 1,
    "idx_tasks_project_kind": 1,
    "idx_tasks_project_lane_order": 1,
    "idx_tasks_project_lane_order_unique": 1,
    "idx_task_events_task_created": 1,
    "idx_tasks_project_completion_commit": 2,
    "idx_review_receipts_task_target_verdict": 5,
    "idx_review_receipts_task_reviewer_generation": 5,
    "idx_review_findings_status_severity_receipt": 5,
    "idx_handoff_project_state_created": 7,
    "idx_handoff_project_source": 7,
    "idx_handoff_due_claim": 7,
    "idx_contract_project_task_revision": 8,
    "idx_effort_bases_project": 9,
    "idx_managed_backup_project_published": 11,
    "idx_checkpoints_project_task_created": 12,
    "idx_tasks_project_task_identity": 15,
    "idx_review_receipts_completion_cycle_reference": 15,
    "idx_task_completion_cycles_task_ordinal": 15,
    "idx_task_events_completion_cycle": 15,
    "idx_verification_receipts_task_generation": 17,
    "idx_verification_receipts_exact_basis": 17,
    "idx_verification_receipts_recent": 17,
    "idx_authority_snapshots_task_generation": 18,
    "idx_contract_criteria_task_kind_digest": 18,
    "idx_review_provenance_receipt": 18,
    "idx_artifact_manifests_target": 18,
    "idx_evidence_references_source": 18,
    "idx_criterion_evidence_links_reference": 19,
    "idx_completion_evidence_bundles_task_cycle": 19,
    "idx_completion_bundle_members_reference": 19,
    "idx_completion_bundle_finding_snapshots_order": 19,
    "idx_verification_runner_resolutions_parent": 20,
    "idx_verification_runner_resolutions_task_generation": 20,
    "idx_verification_runner_attempts_parent": 20,
    "idx_verification_runner_attempts_task_generation": 20,
    "idx_verification_runner_attempts_resolution": 20,
    "idx_verification_runner_sandbox_events_attempt_kind": 20,
    "idx_verification_runner_observations_parent": 20,
    "idx_verification_runner_observations_task_generation": 20,
    "idx_verification_runner_observations_resolution": 20,
    "idx_verification_runner_observations_attempt": 20,
}

_SCHEMA_TRIGGER_INTRODUCED_VERSION = {
    "trg_project_maintenance_enabled_at_immutable": 10,
    "trg_task_events_viewer_generation": 13,
    "trg_project_meta_identity_immutable": 14,
    "trg_project_meta_no_delete": 14,
    "trg_project_meta_cleanup_insert_valid": 14,
    "trg_project_meta_cleanup_update_valid": 14,
    "trg_project_path_binding_history_no_update": 14,
    "trg_project_path_binding_history_no_delete": 14,
    "trg_task_completion_cycles_no_update": 15,
    "trg_task_completion_cycles_no_delete": 15,
    "trg_tasks_completion_history_coverage_immutable": 15,
    "trg_task_events_completion_cycle_link_immutable": 15,
    "trg_verification_receipts_no_update": 17,
    "trg_verification_receipts_no_delete": 17,
    "trg_verification_receipts_locked_basis_insert": 17,
    "trg_task_completion_cycles_verification_basis_insert": 17,
    "trg_authority_snapshots_no_update": 18,
    "trg_authority_snapshots_no_delete": 18,
    "trg_contract_criteria_no_update": 18,
    "trg_contract_criteria_no_delete": 18,
    "trg_authority_snapshot_criteria_no_update": 18,
    "trg_authority_snapshot_criteria_no_delete": 18,
    "trg_review_receipt_provenance_no_update": 18,
    "trg_review_receipt_provenance_no_delete": 18,
    "trg_review_receipt_provenance_codes_no_update": 18,
    "trg_review_receipt_provenance_codes_no_delete": 18,
    "trg_artifact_manifests_no_update": 18,
    "trg_artifact_manifests_no_delete": 18,
    "trg_artifact_manifest_entries_no_update": 18,
    "trg_artifact_manifest_entries_no_delete": 18,
    "trg_evidence_references_no_update": 18,
    "trg_evidence_references_no_delete": 18,
    "trg_review_receipts_provenance_basis_insert": 18,
    "trg_verification_receipts_subject_basis_insert": 18,
    "trg_task_completion_cycles_subject_basis_insert": 18,
    "trg_criterion_evidence_links_no_update": 19,
    "trg_criterion_evidence_links_no_delete": 19,
    "trg_completion_evidence_bundles_no_update": 19,
    "trg_completion_evidence_bundles_no_delete": 19,
    "trg_completion_bundle_members_no_update": 19,
    "trg_completion_bundle_members_no_delete": 19,
    "trg_completion_bundle_finding_snapshots_no_update": 19,
    "trg_completion_bundle_finding_snapshots_no_delete": 19,
    "trg_criterion_evidence_links_matrix_insert": 19,
    "trg_completion_bundle_members_matrix_insert": 19,
    "trg_completion_bundle_finding_snapshots_matrix_insert": 19,
    "trg_task_completion_cycles_evidence_basis_insert": 19,
    "trg_verification_runner_resolutions_no_update": 20,
    "trg_verification_runner_resolutions_no_delete": 20,
    "trg_verification_runner_attempts_no_update": 20,
    "trg_verification_runner_attempts_no_delete": 20,
    "trg_verification_runner_sandbox_events_no_update": 20,
    "trg_verification_runner_sandbox_events_no_delete": 20,
    "trg_verification_runner_observations_no_update": 20,
    "trg_verification_runner_observations_no_delete": 20,
    "trg_verification_runner_resolutions_parent_insert": 20,
    "trg_verification_runner_attempts_parent_insert": 20,
    "trg_verification_runner_sandbox_events_parent_insert": 20,
    "trg_verification_runner_observations_parent_insert": 20,
}

_SCHEMA_COLUMN_INTRODUCED_VERSION = {
    "column:tasks.completion_commit_required": 2,
    "column:tasks.completion_commit_hash": 2,
    "column:tasks.pause_reason": 3,
    "column:tasks.completion_evidence_kind": 4,
    "column:tasks.completion_evidence_revision": 4,
    "column:tasks.completion_evidence_reason": 4,
    "column:tasks.external_revision_approved": 4,
    "column:tasks.review_target_kind": 5,
    "column:tasks.review_target_value": 5,
    "column:tasks.review_target_generation": 5,
    "column:tasks.review_target_base_revision": 6,
    "column:review_receipts.target_base_revision": 6,
    "column:tasks.current_contract_revision": 8,
    "column:project_meta.effort_activity_generation": 9,
    "column:project_meta.identity_scheme": 14,
    "column:project_meta.binding_generation": 14,
    "column:project_meta.binding_reason": 14,
    "column:project_meta.binding_updated_at": 14,
    "column:project_meta.legacy_cleanup_pending": 14,
    "column:project_meta.legacy_cleanup_inventory": 14,
    "column:project_meta.legacy_cleanup_fingerprint": 14,
    "column:tasks.completion_history_coverage": 15,
    "column:task_events.completion_cycle_id": 15,
    "column:task_completion_cycles.verification_basis_version": 17,
    "column:task_completion_cycles.verification_expectation_digest": 17,
    "column:task_completion_cycles.verification_receipt_id": 17,
    "column:tasks.current_authority_snapshot_id": 18,
    "column:tasks.current_authority_snapshot_generation": 18,
    "column:tasks.review_target_capture_version": 18,
    "column:tasks.review_target_authority_snapshot_id": 18,
    "column:tasks.review_target_acceptance_criterion_id": 18,
    "column:tasks.review_target_verification_criterion_id": 18,
    "column:tasks.review_target_artifact_manifest_id": 18,
    "column:review_receipts.review_provenance_basis_version": 18,
    "column:review_receipts.review_provenance_id": 18,
    "column:verification_receipts.verification_subject_basis_version": 18,
    "column:verification_receipts.subject_authority_snapshot_id": 18,
    "column:verification_receipts.subject_verification_criterion_id": 18,
    "column:task_completion_cycles.verification_subject_basis_version": 18,
    "column:task_completion_cycles.subject_authority_snapshot_id": 18,
    "column:task_completion_cycles.subject_verification_criterion_id": 18,
    "column:task_completion_cycles.evidence_basis_version": 19,
    "column:task_completion_cycles.completion_evidence_bundle_id": 19,
    "column:tasks.review_target_runner_basis_version": 20,
    "column:task_completion_cycles.verification_basis_kind": 20,
    "column:task_completion_cycles.verification_runner_observation_id": 20,
    "column:completion_evidence_bundles.verification_basis_kind": 20,
    "column:completion_evidence_bundles.verification_runner_observation_id": 20,
}

_EVIDENCE_LEDGER_REQUIRED_COLUMNS = {
    "authority_snapshots": {
        "authority_snapshot_id", "project_id", "task_id", "generation",
        "task_title", "task_description", "review_tier", "verification",
        "verification_digest", "contract_revision", "contract_state",
        "contract_scope", "contract_acceptance", "contract_constraints",
        "contract_authority_ref", "basis_digest", "producer_class",
        "producer_version", "created_at",
    },
    "contract_criteria": {
        "criterion_id", "project_id", "task_id", "criterion_kind",
        "criterion_text", "digest", "created_at",
    },
    "authority_snapshot_criteria": {
        "project_id", "task_id", "authority_snapshot_id", "criterion_kind",
        "criterion_id",
    },
    "review_receipt_provenance": {
        "review_provenance_id", "review_receipt_id", "project_id", "task_id",
        "provenance_version", "reviewer_class", "model_state",
        "declared_model_id", "skill_state", "declared_skill_id",
        "declared_skill_version", "context_relation", "assurance_class",
        "producer_class", "producer_version", "digest", "created_at",
    },
    "review_receipt_provenance_codes": {
        "project_id", "task_id", "review_provenance_id", "code_kind",
        "ordinal", "code",
    },
    "artifact_manifests": {
        "artifact_manifest_id", "project_id", "task_id", "state",
        "object_format", "comparison_base", "target_kind", "target_value",
        "target_base_revision", "target_generation", "authority_snapshot_id",
        "acceptance_criterion_id", "verification_criterion_id",
        "omission_code", "entry_count", "digest", "created_at",
    },
    "artifact_manifest_entries": {
        "project_id", "task_id", "artifact_manifest_id", "ordinal",
        "entry_kind", "old_path", "new_path", "before_mode",
        "before_object_id", "after_mode", "after_object_id",
    },
    "evidence_references": {
        "evidence_reference_id", "project_id", "task_id", "source_kind",
        "source_state", "source_id", "assurance_class", "producer_class",
        "producer_version", "contract_revision", "authority_snapshot_id",
        "acceptance_criterion_id", "verification_criterion_id", "target_kind",
        "target_value", "target_base_revision", "target_generation",
        "completion_cycle_id", "digest", "created_at",
    },
    "criterion_evidence_links": {
        "criterion_evidence_link_id", "project_id", "task_id",
        "criterion_id", "evidence_reference_id", "relation",
        "assurance_class", "producer_class", "producer_version",
        "created_at",
    },
    "completion_evidence_bundles": {
        "completion_evidence_bundle_id", "project_id", "task_id",
        "completion_cycle_id", "cycle_ordinal", "source_schema_version",
        "bundle_version", "contract_revision", "authority_snapshot_id",
        "acceptance_criterion_id", "verification_criterion_id",
        "target_kind", "target_value", "target_base_revision",
        "target_generation", "target_capture_version",
        "artifact_manifest_id", "verification_receipt_id",
        "verification_basis_kind", "verification_runner_observation_id",
        "omission_mask", "sealed_at", "bundle_digest",
        "payload_size_bytes",
    },
    "completion_bundle_members": {
        "project_id", "task_id", "completion_evidence_bundle_id",
        "member_kind", "ordinal", "criterion_evidence_link_id",
        "evidence_reference_id",
    },
    "completion_bundle_finding_snapshots": {
        "project_id", "task_id", "completion_evidence_bundle_id",
        "ordinal", "review_finding_id", "review_receipt_id",
        "target_generation", "severity", "summary", "status",
        "resolution_summary", "created_at", "resolved_at",
        "evidence_reference_id", "assurance_class", "producer_class",
        "producer_version", "digest",
    },
    "evidence_projection_state": {
        "project_id", "source_generation", "published_generation",
        "index_digest", "last_success_at", "last_outcome_code",
        "last_outcome_at",
    },
}

def _schema_requirement_introduced_version(requirement: str) -> int:
    kind, name = requirement.split(":", 1)
    if kind == "table":
        return _SCHEMA_TABLE_INTRODUCED_VERSION[name]
    if kind == "index":
        return _SCHEMA_INDEX_INTRODUCED_VERSION[name]
    if kind == "trigger":
        return _SCHEMA_TRIGGER_INTRODUCED_VERSION[name]
    if kind == "column":
        table_name = name.split(".", 1)[0]
        return _SCHEMA_COLUMN_INTRODUCED_VERSION.get(
            requirement,
            _SCHEMA_TABLE_INTRODUCED_VERSION[table_name],
        )
    raise AssertionError(f"unknown schema requirement kind: {kind}")


def _schema_requirement_is_present(
    connection: sqlite3.Connection,
    requirement: str,
) -> bool:
    kind, name = requirement.split(":", 1)
    if kind == "column":
        table_name, column_name = name.split(".", 1)
        table_row = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = ? COLLATE NOCASE",
            (table_name,),
        ).fetchone()
        if table_row is None:
            return False
        return (
            connection.execute(
                "SELECT 1 FROM pragma_table_xinfo(?, 'main') "
                "WHERE name = ? COLLATE NOCASE",
                (str(table_row[0]), column_name),
            ).fetchone()
            is not None
        )
    if kind not in {"table", "index", "trigger"}:
        raise AssertionError(f"unknown schema requirement kind: {kind}")
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        is not None
    )


def newer_schema_markers_present(
    connection: sqlite3.Connection,
    schema_version: int,
) -> list[str]:
    """Return known later-migration markers inconsistent with a declared version."""
    markers = (
        *(
            (f"table:{name}", introduced_version)
            for name, introduced_version in _SCHEMA_TABLE_INTRODUCED_VERSION.items()
        ),
        *(
            (f"index:{name}", introduced_version)
            for name, introduced_version in _SCHEMA_INDEX_INTRODUCED_VERSION.items()
        ),
        *(
            (f"trigger:{name}", introduced_version)
            for name, introduced_version in _SCHEMA_TRIGGER_INTRODUCED_VERSION.items()
        ),
        *(
            (name, introduced_version)
            for name, introduced_version in _SCHEMA_COLUMN_INTRODUCED_VERSION.items()
        ),
    )
    detected = [
        marker
        for marker, introduced_version in markers
        if introduced_version > schema_version
        if _schema_requirement_is_present(connection, marker)
    ]
    if schema_version < PRIVATE_SCHEMA20_VERSION:
        matrix_trigger = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' "
            "AND name = 'trg_criterion_evidence_links_matrix_insert'"
        ).fetchone()
        if (
            matrix_trigger is not None
            and matrix_trigger["sql"] is not None
            and _normalized_schema_sql(str(matrix_trigger["sql"]))
            == _normalized_schema_sql(
                _criterion_evidence_links_v20_matrix_trigger_sql()
            )
        ):
            detected.append(
                "trigger:trg_criterion_evidence_links_matrix_insert"
            )
    return detected


def required_schema_objects_missing(
    connection: sqlite3.Connection,
    *,
    schema_version: int = SCHEMA_VERSION,
) -> list[str]:
    required_tables = set(_SCHEMA_TABLE_INTRODUCED_VERSION)
    required_indexes = set(_SCHEMA_INDEX_INTRODUCED_VERSION)
    required_triggers = set(_SCHEMA_TRIGGER_INTRODUCED_VERSION)
    table_rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    index_rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    trigger_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    ).fetchall()
    tables = {str(row["name"]) for row in table_rows}
    indexes = {str(row["name"]) for row in index_rows}
    triggers = {str(row["name"]) for row in trigger_rows}
    missing = [f"table:{name}" for name in sorted(required_tables - tables)]
    missing.extend(f"index:{name}" for name in sorted(required_indexes - indexes))
    missing.extend(f"trigger:{name}" for name in sorted(required_triggers - triggers))
    if "tasks" in tables:
        task_column_rows = connection.execute("PRAGMA table_info(tasks)").fetchall()
        task_columns = {str(row["name"]) for row in task_column_rows}
        required_task_columns = {
            "task_id",
            "project_id",
            "title",
            "description",
            "kind",
            "lane",
            "lane_order",
            "priority",
            "status",
            "blocked_reason",
            "pause_reason",
            "review_tier",
            "verification",
            "tags",
            "created_at",
            "updated_at",
            "completed_at",
            "completion_commit_required",
            "completion_commit_hash",
            "completion_evidence_kind",
            "completion_evidence_revision",
            "completion_evidence_reason",
            "external_revision_approved",
            "review_target_kind",
            "review_target_value",
            "review_target_generation",
            "review_target_base_revision",
            "current_contract_revision",
            "completion_history_coverage",
            "current_authority_snapshot_id",
            "current_authority_snapshot_generation",
            "review_target_capture_version",
            "review_target_authority_snapshot_id",
            "review_target_acceptance_criterion_id",
            "review_target_verification_criterion_id",
            "review_target_artifact_manifest_id",
        }
        missing.extend(
            f"column:tasks.{name}" for name in sorted(required_task_columns - task_columns)
        )
    if "task_events" in tables:
        event_column_rows = connection.execute("PRAGMA table_info(task_events)").fetchall()
        event_columns = {str(row["name"]) for row in event_column_rows}
        required_event_columns = {
            "task_event_id",
            "task_id",
            "project_id",
            "event_type",
            "summary",
            "created_at",
            "completion_cycle_id",
        }
        missing.extend(
            f"column:task_events.{name}"
            for name in sorted(required_event_columns - event_columns)
        )
    if "task_completion_cycles" in tables:
        cycle_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(task_completion_cycles)"
            ).fetchall()
        }
        required_cycle_columns = {
            "completion_cycle_id",
            "project_id",
            "task_id",
            "saved_cycle_ordinal",
            "origin",
            "completeness",
            "completed_at",
            "recorded_at",
            "contract_revision",
            "review_tier",
            "verification_expectation",
            "verification_attestation",
            "verification_basis_version",
            "verification_expectation_digest",
            "verification_receipt_id",
            "verification_subject_basis_version",
            "subject_authority_snapshot_id",
            "subject_verification_criterion_id",
            "evidence_basis_version",
            "completion_evidence_bundle_id",
            "completion_evidence_kind",
            "completion_evidence_revision",
            "completion_evidence_reason",
            "external_revision_approved",
            "completion_commit_required",
            "completion_commit_hash",
            "review_target_kind",
            "review_target_value",
            "review_target_base_revision",
            "review_target_generation",
            "gate_basis_version",
            "review_basis_kind",
            "required_independent_passes",
            "qualifying_independent_passes",
            "changes_requested_count",
            "open_high_count",
            "open_medium_count",
            "fresh_review_required_count",
            "qualifying_receipt_id_1",
            "qualifying_receipt_id_2",
        }
        missing.extend(
            f"column:task_completion_cycles.{name}"
            for name in sorted(required_cycle_columns - cycle_columns)
        )
    if "verification_receipts" in tables:
        receipt_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(verification_receipts)"
            ).fetchall()
        }
        required_receipt_columns = {
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
            "verification_subject_basis_version",
            "subject_authority_snapshot_id",
            "subject_verification_criterion_id",
        }
        missing.extend(
            f"column:verification_receipts.{name}"
            for name in sorted(required_receipt_columns - receipt_columns)
        )
    if "project_meta" in tables:
        project_column_rows = connection.execute("PRAGMA table_info(project_meta)").fetchall()
        project_columns = {str(row["name"]) for row in project_column_rows}
        required_project_columns = {
            "project_id",
            "canonical_path_hash",
            "display_name",
            "created_at",
            "updated_at",
            "effort_activity_generation",
            "identity_scheme",
            "binding_generation",
            "binding_reason",
            "binding_updated_at",
            "legacy_cleanup_pending",
            "legacy_cleanup_inventory",
            "legacy_cleanup_fingerprint",
        }
        missing.extend(
            f"column:project_meta.{name}"
            for name in sorted(required_project_columns - project_columns)
        )
    if "project_path_binding_history" in tables:
        history_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(project_path_binding_history)"
            ).fetchall()
        }
        required_history_columns = {
            "project_id",
            "binding_generation",
            "previous_path_hash",
            "canonical_path_hash",
            "display_name",
            "reason",
            "confirmation_token_digest",
            "bound_at",
        }
        missing.extend(
            f"column:project_path_binding_history.{name}"
            for name in sorted(required_history_columns - history_columns)
        )
    required_review_columns = {
        "review_receipts": {
            "review_receipt_id",
            "task_id",
            "project_id",
            "reviewer_key",
            "receipt_kind",
            "verdict",
            "target_kind",
            "target_value",
            "target_base_revision",
            "target_generation",
            "summary",
            "user_approved",
            "created_at",
            "review_provenance_basis_version",
            "review_provenance_id",
        },
        "review_findings": {
            "review_finding_id",
            "review_receipt_id",
            "severity",
            "status",
            "summary",
            "resolution_summary",
            "created_at",
            "resolved_at",
        },
    }
    for table_name, required_columns in required_review_columns.items():
        if table_name not in tables:
            continue
        column_rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        columns = {str(row["name"]) for row in column_rows}
        missing.extend(
            f"column:{table_name}.{name}"
            for name in sorted(required_columns - columns)
        )
    if "handoff_records" in tables:
        handoff_column_rows = connection.execute(
            "PRAGMA table_info(handoff_records)"
        ).fetchall()
        handoff_columns = {str(row["name"]) for row in handoff_column_rows}
        required_handoff_columns = {
            "handoff_id",
            "project_id",
            "source_task_id",
            "source_contract_revision",
            "idempotency_key",
            "occurrence_id",
            "summary",
            "rationale",
            "state",
            "adapter_key",
            "adapter_version",
            "delivery_attempts",
            "last_delivery_code",
            "next_attempt_at",
            "claim_token",
            "claim_expires_at",
            "receiver_receipt",
            "withdraw_reason",
            "created_at",
            "updated_at",
            "handed_off_at",
            "withdrawn_at",
        }
        missing.extend(
            f"column:handoff_records.{name}"
            for name in sorted(required_handoff_columns - handoff_columns)
        )
    if "task_contract_revisions" in tables:
        contract_column_rows = connection.execute(
            "PRAGMA table_info(task_contract_revisions)"
        ).fetchall()
        contract_columns = {str(row["name"]) for row in contract_column_rows}
        required_contract_columns = {
            "contract_revision_id",
            "task_id",
            "project_id",
            "revision",
            "scope",
            "acceptance",
            "constraints_text",
            "authority_ref",
            "change_reason",
            "created_at",
        }
        missing.extend(
            f"column:task_contract_revisions.{name}"
            for name in sorted(required_contract_columns - contract_columns)
        )
    required_effort_columns = {
        "task_effort_activity": {
            "task_id",
            "project_id",
            "generation",
        },
        "task_effort_bases": {
            "task_id",
            "project_id",
            "basis_head",
            "basis_clean",
            "captured_at",
            "project_generation",
            "subject_generation",
            "other_active_at_capture",
        },
    }
    for table_name, required_columns in required_effort_columns.items():
        if table_name not in tables:
            continue
        column_rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        columns = {str(row["name"]) for row in column_rows}
        missing.extend(
            f"column:{table_name}.{name}"
            for name in sorted(required_columns - columns)
        )
    if "project_maintenance" in tables:
        maintenance_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(project_maintenance)"
            ).fetchall()
        }
        required_maintenance_columns = {
            "project_id",
            "enabled_at",
            "backup_interval_minutes",
            "backup_generations",
            "applied_backup_generations",
            "backup_last_success_at",
            "backup_last_outcome_code",
            "backup_last_outcome_at",
            "latest_backup_generation_id",
            "viewer_last_success_at",
            "viewer_last_outcome_code",
            "viewer_last_outcome_at",
        }
        missing.extend(
            f"column:project_maintenance.{name}"
            for name in sorted(required_maintenance_columns - maintenance_columns)
        )
    if "managed_backup_generations" in tables:
        generation_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(managed_backup_generations)"
            ).fetchall()
        }
        required_generation_columns = {
            "generation_id",
            "project_id",
            "published_at",
            "publication_retention",
        }
        missing.extend(
            f"column:managed_backup_generations.{name}"
            for name in sorted(required_generation_columns - generation_columns)
        )
    if "task_checkpoints" in tables:
        checkpoint_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(task_checkpoints)"
            ).fetchall()
        }
        required_checkpoint_columns = {
            "checkpoint_id",
            "task_id",
            "project_id",
            "contract_revision",
            "summary",
            "next_action",
            "unresolved_risks_json",
            "created_at",
        }
        missing.extend(
            f"column:task_checkpoints.{name}"
            for name in sorted(required_checkpoint_columns - checkpoint_columns)
        )
    if "viewer_maintenance_state" in tables:
        viewer_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(viewer_maintenance_state)"
            ).fetchall()
        }
        required_viewer_columns = {
            "project_id",
            "source_generation",
            "rendered_generation",
            "last_success_at",
            "last_outcome_code",
            "last_outcome_at",
        }
        missing.extend(
            f"column:viewer_maintenance_state.{name}"
            for name in sorted(required_viewer_columns - viewer_columns)
        )
    for table_name, required_columns in _EVIDENCE_LEDGER_REQUIRED_COLUMNS.items():
        if table_name not in tables:
            continue
        columns = {
            str(row["name"])
            for row in connection.execute(
                f"PRAGMA table_info({_quoted_identifier(table_name)})"
            ).fetchall()
        }
        missing.extend(
            f"column:{table_name}.{name}"
            for name in sorted(required_columns - columns)
        )
    return [
        requirement
        for requirement in missing
        if _schema_requirement_introduced_version(requirement) <= schema_version
    ]


def schema_objects_inconsistent_with_version(
    connection: sqlite3.Connection,
    schema_version: int,
) -> list[str]:
    """Reject missing own-version structure and markers from later migrations."""
    return [
        *required_schema_objects_missing(
            connection,
            schema_version=schema_version,
        ),
        *newer_schema_markers_present(connection, schema_version),
    ]


def apply_initial_schema_migration(connection: sqlite3.Connection) -> None:
    applied_at = utc_now()
    connection.executescript(initial_schema_sql())
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (1, "initial_schema", applied_at),
    )


def apply_completion_commit_migration(connection: sqlite3.Connection) -> None:
    applied_at = utc_now()
    if not column_exists(connection, "tasks", "completion_commit_required"):
        connection.execute(
            """
            ALTER TABLE tasks
              ADD COLUMN completion_commit_required INTEGER NOT NULL DEFAULT 1
              CHECK (completion_commit_required IN (0, 1))
            """
        )
    if not column_exists(connection, "tasks", "completion_commit_hash"):
        connection.execute(
            """
            ALTER TABLE tasks
              ADD COLUMN completion_commit_hash TEXT NOT NULL DEFAULT ''
            """
        )
    connection.executescript(completion_commit_schema_sql())
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (2, "completion_commit_columns", applied_at),
    )


def apply_paused_state_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Rebuild the task parent table safely while preserving task-event links."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "paused-state migration requires no active transaction",
        )
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
            raise StorageError("internal_error", "could not disable foreign key enforcement")
        connection.execute("BEGIN IMMEDIATE")
        task_ids_before = [
            str(row["task_id"])
            for row in connection.execute("SELECT task_id FROM tasks ORDER BY task_id").fetchall()
        ]
        event_links_before = [
            (str(row["task_event_id"]), str(row["task_id"]))
            for row in connection.execute(
                "SELECT task_event_id, task_id FROM task_events ORDER BY task_event_id"
            ).fetchall()
        ]
        connection.execute("DROP TABLE IF EXISTS tasks_v3")
        connection.execute(paused_tasks_schema_sql())
        connection.execute(
            """
            INSERT INTO tasks_v3(
              task_id, project_id, title, description, kind, lane, lane_order,
              priority, status, blocked_reason, pause_reason, review_tier,
              verification, tags, created_at, updated_at, completed_at,
              completion_commit_required, completion_commit_hash
            )
            SELECT
              task_id, project_id, title, description, kind, lane, lane_order,
              priority, status, blocked_reason, '', review_tier,
              verification, tags, created_at, updated_at, completed_at,
              completion_commit_required, completion_commit_hash
              FROM tasks
            """
        )
        if fail_stage == "after_copy":
            raise StorageError("internal_error", "injected paused-state migration failure")
        connection.execute("DROP TABLE tasks")
        connection.execute("ALTER TABLE tasks_v3 RENAME TO tasks")
        create_task_indexes(connection)
        task_ids_after = [
            str(row["task_id"])
            for row in connection.execute("SELECT task_id FROM tasks ORDER BY task_id").fetchall()
        ]
        event_links_after = [
            (str(row["task_event_id"]), str(row["task_id"]))
            for row in connection.execute(
                "SELECT task_event_id, task_id FROM task_events ORDER BY task_event_id"
            ).fetchall()
        ]
        if task_ids_after != task_ids_before or event_links_after != event_links_before:
            raise StorageError("internal_error", "paused-state migration changed task or event identities")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise StorageError("internal_error", "paused-state migration produced foreign key violations")
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (3, "paused_state", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected paused-state migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise StorageError("internal_error", "could not restore foreign key enforcement")


def apply_completion_evidence_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> int:
    """Add typed completion evidence while preserving every legacy projection."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "completion-evidence migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        if not column_exists(connection, "tasks", "completion_evidence_kind"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN completion_evidence_kind TEXT NOT NULL DEFAULT 'none'
                  CHECK (completion_evidence_kind IN (
                    'none', 'git_commit', 'external_revision',
                    'commit_not_required', 'legacy_unverified'
                  ))
                """
            )
        if not column_exists(connection, "tasks", "completion_evidence_revision"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN completion_evidence_revision TEXT NOT NULL DEFAULT ''
                """
            )
        if not column_exists(connection, "tasks", "completion_evidence_reason"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN completion_evidence_reason TEXT NOT NULL DEFAULT ''
                """
            )
        if not column_exists(connection, "tasks", "external_revision_approved"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN external_revision_approved INTEGER NOT NULL DEFAULT 0
                  CHECK (external_revision_approved IN (0, 1))
                """
            )

        inconsistent_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM tasks
                 WHERE completion_commit_required = 0
                   AND completion_commit_hash != ''
                """
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE tasks
               SET completion_evidence_kind = CASE
                     WHEN completion_commit_required = 1 AND completion_commit_hash = ''
                       THEN 'none'
                     WHEN completion_commit_required = 0 AND completion_commit_hash = ''
                       THEN 'commit_not_required'
                     ELSE 'legacy_unverified'
                   END,
                   completion_evidence_revision = CASE
                     WHEN completion_commit_hash != '' THEN completion_commit_hash
                     ELSE ''
                   END,
                   completion_evidence_reason = '',
                   external_revision_approved = 0
            """
        )
        if fail_stage == "after_mapping":
            raise StorageError("internal_error", "injected completion-evidence migration failure")
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (4, "typed_completion_evidence", utc_now()),
        )
        connection.commit()
        return inconsistent_count
    except Exception:
        connection.rollback()
        raise


def apply_review_evidence_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add append-only structured review evidence without synthesizing history."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "review-evidence migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        if not column_exists(connection, "tasks", "review_target_kind"):
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN review_target_kind TEXT NOT NULL DEFAULT ''"
            )
        if not column_exists(connection, "tasks", "review_target_value"):
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN review_target_value TEXT NOT NULL DEFAULT ''"
            )
        if not column_exists(connection, "tasks", "review_target_generation"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN review_target_generation INTEGER NOT NULL DEFAULT 0
                  CHECK (review_target_generation >= 0)
                """
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_receipts (
              review_receipt_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              reviewer_key TEXT NOT NULL,
              receipt_kind TEXT NOT NULL CHECK (receipt_kind IN (
                'independent', 'self_review_fallback', 'not_required'
              )),
              verdict TEXT NOT NULL CHECK (verdict IN (
                'pass', 'changes_requested', 'not_required'
              )),
              target_kind TEXT NOT NULL CHECK (target_kind IN (
                'git_commit', 'diff_fingerprint', 'external_revision'
              )),
              target_value TEXT NOT NULL,
              target_generation INTEGER NOT NULL CHECK (target_generation > 0),
              summary TEXT NOT NULL DEFAULT '',
              user_approved INTEGER NOT NULL DEFAULT 0 CHECK (user_approved IN (0, 1)),
              created_at TEXT NOT NULL,
              UNIQUE (task_id, target_generation, reviewer_key),
              CHECK (reviewer_key != ''),
              CHECK (target_value != ''),
              CHECK (
                (receipt_kind = 'independent'
                  AND verdict IN ('pass', 'changes_requested')
                  AND user_approved = 0) OR
                (receipt_kind = 'self_review_fallback'
                  AND verdict IN ('pass', 'changes_requested')) OR
                (receipt_kind = 'not_required'
                  AND verdict = 'not_required'
                  AND user_approved = 0
                  AND summary != '')
              ),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_findings (
              review_finding_id TEXT PRIMARY KEY,
              review_receipt_id TEXT NOT NULL,
              severity TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
              status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
              summary TEXT NOT NULL,
              resolution_summary TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              resolved_at TEXT,
              FOREIGN KEY (review_receipt_id) REFERENCES review_receipts(review_receipt_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_receipts_task_target_verdict
              ON review_receipts(
                task_id, target_generation, target_kind, target_value, verdict
              )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_receipts_task_reviewer_generation
              ON review_receipts(task_id, target_generation, reviewer_key)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_findings_status_severity_receipt
              ON review_findings(status, severity, review_receipt_id)
            """
        )
        if fail_stage == "after_schema":
            raise StorageError("internal_error", "injected review-evidence migration failure")
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (5, "structured_review_evidence", utc_now()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def git_snapshot_review_receipts_schema_sql() -> str:
    return """
CREATE TABLE review_receipts_v6 (
  review_receipt_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  reviewer_key TEXT NOT NULL,
  receipt_kind TEXT NOT NULL CHECK (receipt_kind IN (
    'independent', 'self_review_fallback', 'not_required'
  )),
  verdict TEXT NOT NULL CHECK (verdict IN (
    'pass', 'changes_requested', 'not_required'
  )),
  target_kind TEXT NOT NULL CHECK (target_kind IN (
    'git_commit', 'diff_fingerprint', 'external_revision', 'git_snapshot'
  )),
  target_value TEXT NOT NULL,
  target_base_revision TEXT NOT NULL DEFAULT '',
  target_generation INTEGER NOT NULL CHECK (target_generation > 0),
  summary TEXT NOT NULL DEFAULT '',
  user_approved INTEGER NOT NULL DEFAULT 0 CHECK (user_approved IN (0, 1)),
  created_at TEXT NOT NULL,
  UNIQUE (task_id, target_generation, reviewer_key),
  CHECK (reviewer_key != ''),
  CHECK (target_value != ''),
  CHECK (
    (target_kind = 'git_snapshot' AND target_base_revision != '') OR
    (target_kind != 'git_snapshot' AND target_base_revision = '')
  ),
  CHECK (
    (receipt_kind = 'independent'
      AND verdict IN ('pass', 'changes_requested')
      AND user_approved = 0) OR
    (receipt_kind = 'self_review_fallback'
      AND verdict IN ('pass', 'changes_requested')) OR
    (receipt_kind = 'not_required'
      AND verdict = 'not_required'
      AND user_approved = 0
      AND summary != '')
  ),
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""


def create_review_receipt_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX idx_review_receipts_task_target_verdict
          ON review_receipts(
            task_id, target_generation, target_kind, target_value,
            target_base_revision, verdict
          )
        """
    )
    connection.execute(
        """
        CREATE INDEX idx_review_receipts_task_reviewer_generation
          ON review_receipts(task_id, target_generation, reviewer_key)
        """
    )


def apply_git_snapshot_schema_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add target-base storage while preserving receipt and finding identities."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "git-snapshot migration requires no active transaction",
        )
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
            raise StorageError("internal_error", "could not disable foreign key enforcement")
        connection.execute("BEGIN IMMEDIATE")
        if not column_exists(connection, "tasks", "review_target_base_revision"):
            connection.execute(
                """
                ALTER TABLE tasks
                  ADD COLUMN review_target_base_revision TEXT NOT NULL DEFAULT ''
                """
            )

        receipt_rows_before = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT review_receipt_id, task_id, project_id, reviewer_key,
                       receipt_kind, verdict, target_kind, target_value,
                       target_generation, summary, user_approved, created_at
                  FROM review_receipts
                 ORDER BY review_receipt_id
                """
            ).fetchall()
        ]
        finding_links_before = [
            (str(row["review_finding_id"]), str(row["review_receipt_id"]))
            for row in connection.execute(
                """
                SELECT review_finding_id, review_receipt_id
                  FROM review_findings
                 ORDER BY review_finding_id
                """
            ).fetchall()
        ]
        connection.execute("DROP TABLE IF EXISTS review_receipts_v6")
        connection.execute(git_snapshot_review_receipts_schema_sql())
        connection.execute(
            """
            INSERT INTO review_receipts_v6(
              review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
              verdict, target_kind, target_value, target_base_revision,
              target_generation, summary, user_approved, created_at
            )
            SELECT
              review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
              verdict, target_kind, target_value, '', target_generation,
              summary, user_approved, created_at
              FROM review_receipts
            """
        )
        if fail_stage == "after_copy":
            raise StorageError("internal_error", "injected git-snapshot migration failure")
        connection.execute("DROP TABLE review_receipts")
        connection.execute("ALTER TABLE review_receipts_v6 RENAME TO review_receipts")
        create_review_receipt_indexes(connection)

        receipt_rows_after = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT review_receipt_id, task_id, project_id, reviewer_key,
                       receipt_kind, verdict, target_kind, target_value,
                       target_generation, summary, user_approved, created_at
                  FROM review_receipts
                 ORDER BY review_receipt_id
                """
            ).fetchall()
        ]
        finding_links_after = [
            (str(row["review_finding_id"]), str(row["review_receipt_id"]))
            for row in connection.execute(
                """
                SELECT review_finding_id, review_receipt_id
                  FROM review_findings
                 ORDER BY review_finding_id
                """
            ).fetchall()
        ]
        if (
            receipt_rows_after != receipt_rows_before
            or finding_links_after != finding_links_before
        ):
            raise StorageError(
                "internal_error",
                "git-snapshot migration changed review evidence identities",
            )
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise StorageError(
                "internal_error",
                "git-snapshot migration produced foreign key violations",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (6, "git_snapshot_target_base", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected git-snapshot migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise StorageError("internal_error", "could not restore foreign key enforcement")


def apply_handoff_outbox_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add the local handoff outbox without changing existing task data."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "handoff-outbox migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE handoff_records (
              handoff_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              source_task_id TEXT NOT NULL,
              source_contract_revision INTEGER NOT NULL DEFAULT 0
                CHECK (source_contract_revision >= 0),
              idempotency_key TEXT NOT NULL,
              occurrence_id TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL,
              rationale TEXT NOT NULL DEFAULT '',
              state TEXT NOT NULL CHECK (state IN (
                'pending_handoff',
                'handed_off',
                'handoff_withdrawn_by_user'
              )),
              adapter_key TEXT NOT NULL DEFAULT '',
              adapter_version TEXT NOT NULL DEFAULT '',
              delivery_attempts INTEGER NOT NULL DEFAULT 0
                CHECK (delivery_attempts >= 0),
              last_delivery_code TEXT NOT NULL DEFAULT '',
              next_attempt_at TEXT,
              claim_token TEXT NOT NULL DEFAULT '',
              claim_expires_at TEXT,
              receiver_receipt TEXT NOT NULL DEFAULT '',
              withdraw_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              handed_off_at TEXT,
              withdrawn_at TEXT,
              UNIQUE (project_id, idempotency_key),
              FOREIGN KEY (source_task_id) REFERENCES tasks(task_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_handoff_project_state_created
              ON handoff_records(project_id, state, created_at, handoff_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_handoff_project_source
              ON handoff_records(project_id, source_task_id, created_at, handoff_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_handoff_due_claim
              ON handoff_records(project_id, state, claim_expires_at)
            """
        )
        if fail_stage == "after_schema":
            raise StorageError("internal_error", "injected handoff-outbox migration failure")
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (7, "local_handoff_outbox", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected handoff-outbox migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_task_contract_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add immutable Task Contract revisions without rewriting legacy task rows."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "task-contract migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            ALTER TABLE tasks
              ADD COLUMN current_contract_revision INTEGER NOT NULL DEFAULT 0
              CHECK (current_contract_revision >= 0)
            """
        )
        if fail_stage == "after_task_column":
            raise StorageError("internal_error", "injected task-contract migration failure")
        connection.execute(
            """
            CREATE TABLE task_contract_revisions (
              contract_revision_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK (revision > 0),
              scope TEXT NOT NULL,
              acceptance TEXT NOT NULL,
              constraints_text TEXT NOT NULL DEFAULT '',
              authority_ref TEXT NOT NULL DEFAULT '',
              change_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE (task_id, revision),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_contract_project_task_revision
              ON task_contract_revisions(project_id, task_id, revision)
            """
        )
        if fail_stage == "after_schema":
            raise StorageError("internal_error", "injected task-contract migration failure")
        nonzero_pointer_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE current_contract_revision != 0"
            ).fetchone()[0]
        )
        if nonzero_pointer_count:
            raise StorageError(
                "internal_error",
                "task-contract migration did not preserve revision-zero compatibility",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (8, "task_contract_revisions", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected task-contract migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_effort_advisory_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add the minimal basis and activity data required by Effort Advisory."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "effort-advisory migration requires no active transaction",
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            ALTER TABLE project_meta
              ADD COLUMN effort_activity_generation INTEGER NOT NULL DEFAULT 0
              CHECK (effort_activity_generation >= 0)
            """
        )
        if fail_stage == "after_project_column":
            raise StorageError("internal_error", "injected effort-advisory migration failure")
        connection.execute(
            """
            CREATE TABLE task_effort_activity (
              task_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE task_effort_bases (
              task_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              basis_head TEXT NOT NULL,
              basis_clean INTEGER NOT NULL CHECK (basis_clean IN (0, 1)),
              captured_at TEXT NOT NULL,
              project_generation INTEGER NOT NULL CHECK (project_generation >= 0),
              subject_generation INTEGER NOT NULL CHECK (subject_generation >= 0),
              other_active_at_capture INTEGER NOT NULL
                CHECK (other_active_at_capture IN (0, 1)),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_effort_bases_project
              ON task_effort_bases(project_id, captured_at, task_id)
            """
        )
        if fail_stage == "after_schema":
            raise StorageError("internal_error", "injected effort-advisory migration failure")
        legacy_counts = (
            int(connection.execute("SELECT COUNT(*) FROM task_effort_activity").fetchone()[0]),
            int(connection.execute("SELECT COUNT(*) FROM task_effort_bases").fetchone()[0]),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM project_meta WHERE effort_activity_generation != 0"
                ).fetchone()[0]
            ),
        )
        if legacy_counts != (0, 0, 0):
            raise StorageError(
                "internal_error",
                "effort-advisory migration did not preserve a strict disabled baseline",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (9, "informational_effort_advisory", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected effort-advisory migration failure")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_project_maintenance_migration(
    connection: sqlite3.Connection,
    *,
    setup_backup: MigrationBackupMetadata | None = None,
    fail_stage: str | None = None,
) -> None:
    """Add the partial one-row maintenance state used by setup and doctor."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "project-maintenance migration requires no active transaction",
        )
    existing_version = current_schema_version(connection)
    if existing_version >= 10:
        maintenance_missing = {
            item
            for item in required_schema_objects_missing(connection)
            if item == "table:project_maintenance"
            or item == "trigger:trg_project_maintenance_enabled_at_immutable"
            or item.startswith("column:project_maintenance.")
        }
        if (
            missing_migration_versions(connection, existing_version)
            or maintenance_missing
        ):
            raise StorageError(
                "migration_required",
                "project-maintenance migration is incomplete",
            )
        return
    if setup_backup is not None:
        validate_migration_backup_metadata(setup_backup)

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE project_maintenance (
              project_id TEXT PRIMARY KEY,
              enabled_at TEXT,
              backup_interval_minutes INTEGER
                CHECK (
                  backup_interval_minutes IS NULL
                  OR backup_interval_minutes BETWEEN 1 AND 1440
                ),
              backup_generations INTEGER
                CHECK (
                  backup_generations IS NULL
                  OR backup_generations BETWEEN 1 AND 20
                ),
              applied_backup_generations INTEGER
                CHECK (
                  applied_backup_generations IS NULL
                  OR applied_backup_generations BETWEEN 1 AND 20
                ),
              backup_last_success_at TEXT,
              backup_last_outcome_code TEXT
                CHECK (
                  backup_last_outcome_code IS NULL
                  OR backup_last_outcome_code IN ('succeeded', 'deferred', 'failed')
                ),
              backup_last_outcome_at TEXT,
              latest_backup_generation_id TEXT
                CHECK (
                  latest_backup_generation_id IS NULL
                  OR (
                    length(latest_backup_generation_id) = 42
                    AND latest_backup_generation_id GLOB
                      'tg_backup_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                  )
                ),
              viewer_last_success_at TEXT,
              viewer_last_outcome_code TEXT
                CHECK (
                  viewer_last_outcome_code IS NULL
                  OR viewer_last_outcome_code IN ('succeeded', 'deferred', 'failed')
                ),
              viewer_last_outcome_at TEXT,
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id),
              CHECK (
                (enabled_at IS NULL
                  AND backup_interval_minutes IS NULL
                  AND backup_generations IS NULL)
                OR
                (enabled_at IS NOT NULL
                  AND backup_interval_minutes IS NOT NULL
                  AND backup_generations IS NOT NULL)
              ),
              CHECK (
                (backup_last_outcome_code IS NULL
                  AND backup_last_outcome_at IS NULL)
                OR
                (backup_last_outcome_code IS NOT NULL
                  AND backup_last_outcome_at IS NOT NULL)
              ),
              CHECK (
                (viewer_last_outcome_code IS NULL
                  AND viewer_last_outcome_at IS NULL)
                OR
                (viewer_last_outcome_code IS NOT NULL
                  AND viewer_last_outcome_at IS NOT NULL)
              ),
              CHECK (
                (backup_last_success_at IS NULL
                  AND latest_backup_generation_id IS NULL
                  AND applied_backup_generations IS NULL)
                OR
                (backup_last_success_at IS NOT NULL
                  AND latest_backup_generation_id IS NOT NULL
                  AND applied_backup_generations IS NOT NULL)
              )
            )
            """
        )
        connection.execute(
            """
            CREATE TRIGGER trg_project_maintenance_enabled_at_immutable
            BEFORE UPDATE OF enabled_at ON project_maintenance
            WHEN OLD.enabled_at IS NOT NULL AND NEW.enabled_at IS NOT OLD.enabled_at
            BEGIN
              SELECT RAISE(ABORT, 'project maintenance enabled_at is immutable');
            END
            """
        )
        if fail_stage == "after_schema":
            raise StorageError(
                "internal_error",
                "injected project-maintenance migration failure",
            )

        project_rows = connection.execute(
            "SELECT project_id FROM project_meta ORDER BY project_id"
        ).fetchall()
        if len(project_rows) > 1:
            raise StorageError(
                "internal_error",
                "project-maintenance migration found multiple project identities",
            )
        if setup_backup is not None and not project_rows:
            raise StorageError(
                "internal_error",
                "setup backup metadata requires existing project identity",
            )
        if project_rows:
            project_id = str(project_rows[0]["project_id"])
            if setup_backup is None:
                connection.execute(
                    "INSERT INTO project_maintenance(project_id) VALUES (?)",
                    (project_id,),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO project_maintenance(
                      project_id, applied_backup_generations,
                      backup_last_success_at, backup_last_outcome_code,
                      backup_last_outcome_at, latest_backup_generation_id
                    ) VALUES (?, ?, ?, 'succeeded', ?, ?)
                    """,
                    (
                        project_id,
                        setup_backup.publication_retention,
                        setup_backup.published_at,
                        setup_backup.published_at,
                        setup_backup.generation_id,
                    ),
                )
        if fail_stage == "after_row":
            raise StorageError(
                "internal_error",
                "injected project-maintenance migration failure",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (10, "project_maintenance", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected project-maintenance migration failure",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_managed_backup_generations_migration(
    connection: sqlite3.Connection,
    *,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
    fail_stage: str | None = None,
) -> None:
    """Add the bounded managed-generation set and seed validated setup copies."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "managed-backup migration requires no active transaction",
        )
    existing_version = current_schema_version(connection)
    if existing_version >= 11:
        generation_missing = {
            item
            for item in required_schema_objects_missing(connection)
            if item == "table:managed_backup_generations"
            or item == "index:idx_managed_backup_project_published"
            or item.startswith("column:managed_backup_generations.")
        }
        if (
            missing_migration_versions(connection, existing_version)
            or generation_missing
        ):
            raise StorageError(
                "migration_required",
                "managed-backup migration is incomplete",
            )
        return

    validated = validate_managed_backup_metadata_set(managed_backups)

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE managed_backup_generations (
              generation_id TEXT PRIMARY KEY
                CHECK (
                  length(generation_id) = 42
                  AND generation_id GLOB
                    'tg_backup_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                ),
              project_id TEXT NOT NULL,
              published_at TEXT NOT NULL
                CHECK (
                  length(published_at) = 20
                  AND published_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                ),
              publication_retention INTEGER NOT NULL
                CHECK (publication_retention BETWEEN 1 AND 20),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_managed_backup_project_published
              ON managed_backup_generations(
                project_id, published_at, generation_id
              )
            """
        )
        if fail_stage == "after_schema":
            raise StorageError(
                "internal_error",
                "injected managed-backup migration failure",
            )

        project_rows = connection.execute(
            "SELECT project_id FROM project_meta ORDER BY project_id"
        ).fetchall()
        if len(project_rows) > 1:
            raise StorageError(
                "internal_error",
                "managed-backup migration found multiple project identities",
            )
        if validated and not project_rows:
            raise StorageError(
                "internal_error",
                "managed-backup seed requires existing project identity",
            )
        if project_rows:
            project_id = str(project_rows[0]["project_id"])
            maintenance = connection.execute(
                """
                SELECT applied_backup_generations, backup_last_success_at,
                       latest_backup_generation_id
                  FROM project_maintenance
                 WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if maintenance is None:
                raise StorageError(
                    "internal_error",
                    "managed-backup migration requires maintenance state",
                )
            latest_id = (
                str(maintenance["latest_backup_generation_id"])
                if maintenance["latest_backup_generation_id"] is not None
                else None
            )
            if latest_id is None:
                if validated:
                    raise StorageError(
                        "internal_error",
                        "managed-backup seed has no matching latest generation",
                    )
            else:
                latest = next(
                    (
                        metadata
                        for metadata in validated
                        if metadata.generation_id == latest_id
                    ),
                    None,
                )
                applied_backup_generations = validate_sqlite_integer_storage_class(
                    maintenance["applied_backup_generations"]
                )
                if (
                    latest is None
                    or latest.published_at
                    != str(maintenance["backup_last_success_at"])
                    or latest.publication_retention
                    != applied_backup_generations
                ):
                    raise StorageError(
                        "internal_error",
                        "managed-backup seed does not match latest generation",
                    )
            for metadata in validated:
                _insert_managed_backup_generation(
                    connection,
                    project_id,
                    metadata,
                    allow_existing=False,
                )
        if fail_stage == "after_rows":
            raise StorageError(
                "internal_error",
                "injected managed-backup migration failure",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (11, "managed_backup_generations", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected managed-backup migration failure",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_task_checkpoints_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add the append-only typed continuation checkpoint store."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "task-checkpoint migration requires no active transaction",
        )
    existing_version = current_schema_version(connection)
    if existing_version >= 12:
        checkpoint_missing = {
            item
            for item in required_schema_objects_missing(connection)
            if item == "table:task_checkpoints"
            or item == "index:idx_checkpoints_project_task_created"
            or item.startswith("column:task_checkpoints.")
        }
        if (
            missing_migration_versions(connection, existing_version)
            or checkpoint_missing
        ):
            raise StorageError(
                "migration_required",
                "task-checkpoint migration is incomplete",
            )
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE task_checkpoints (
              checkpoint_id TEXT PRIMARY KEY
                CHECK (
                  length(CAST(checkpoint_id AS BLOB)) BETWEEN 1 AND 128
                ),
              task_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              contract_revision INTEGER NOT NULL
                CHECK (contract_revision >= 0),
              summary TEXT NOT NULL
                CHECK (
                  length(CAST(summary AS BLOB)) BETWEEN 1 AND 1024
                ),
              next_action TEXT NOT NULL
                CHECK (
                  length(CAST(next_action AS BLOB)) BETWEEN 1 AND 1024
                ),
              unresolved_risks_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                  length(CAST(unresolved_risks_json AS BLOB))
                    BETWEEN 2 AND 24601
                ),
              created_at TEXT NOT NULL
                CHECK (
                  length(created_at) = 20
                  AND created_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                ),
              FOREIGN KEY (task_id) REFERENCES tasks(task_id),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_checkpoints_project_task_created
              ON task_checkpoints(
                project_id, task_id, created_at, checkpoint_id
              )
            """
        )
        if fail_stage == "after_schema":
            raise StorageError(
                "internal_error",
                "injected task-checkpoint migration failure",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (12, "task_checkpoints", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected task-checkpoint migration failure",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_viewer_maintenance_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add bounded source/render generations for the canonical Viewer."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "Viewer-maintenance migration requires no active transaction",
        )
    existing_version = current_schema_version(connection)
    if existing_version >= 13:
        viewer_missing = {
            item
            for item in required_schema_objects_missing(connection)
            if item == "table:viewer_maintenance_state"
            or item == "trigger:trg_task_events_viewer_generation"
            or item.startswith("column:viewer_maintenance_state.")
        }
        if (
            missing_migration_versions(connection, existing_version)
            or viewer_missing
        ):
            raise StorageError(
                "migration_required",
                "Viewer-maintenance migration is incomplete",
            )
        return
    if existing_version != 12:
        raise StorageError(
            "migration_required",
            "Viewer-maintenance migration requires schema version 12",
        )
    if missing_migration_versions(connection, existing_version):
        raise StorageError(
            "migration_required",
            "Viewer-maintenance migration requires complete schema version 12 history",
        )

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE viewer_maintenance_state (
              project_id TEXT PRIMARY KEY,
              source_generation INTEGER NOT NULL
                CHECK (source_generation >= 0),
              rendered_generation INTEGER
                CHECK (
                  rendered_generation IS NULL
                  OR (
                    rendered_generation >= 0
                    AND rendered_generation <= source_generation
                  )
                ),
              last_success_at TEXT,
              last_outcome_code TEXT
                CHECK (
                  last_outcome_code IS NULL
                  OR last_outcome_code IN ('succeeded', 'deferred', 'failed')
                ),
              last_outcome_at TEXT,
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id),
              CHECK (
                last_success_at IS NULL
                OR (
                  length(last_success_at) = 20
                  AND last_success_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                )
              ),
              CHECK (
                last_outcome_at IS NULL
                OR (
                  length(last_outcome_at) = 20
                  AND last_outcome_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                )
              ),
              CHECK (
                (last_outcome_code IS NULL AND last_outcome_at IS NULL)
                OR
                (last_outcome_code IS NOT NULL AND last_outcome_at IS NOT NULL)
              )
            )
            """
        )
        connection.execute(
            """
            CREATE TRIGGER trg_task_events_viewer_generation
            AFTER INSERT ON task_events
            BEGIN
              UPDATE viewer_maintenance_state
                 SET source_generation = source_generation + 1
               WHERE project_id = NEW.project_id
                 AND source_generation < 9223372036854775807;
              SELECT CASE
                WHEN changes() != 1
                THEN RAISE(ABORT, 'Viewer generation state is unavailable')
              END;
            END
            """
        )
        if fail_stage == "after_schema":
            raise StorageError(
                "internal_error",
                "injected Viewer-maintenance migration failure",
            )

        project_rows = connection.execute(
            "SELECT project_id FROM project_meta ORDER BY project_id"
        ).fetchall()
        if len(project_rows) > 1:
            raise StorageError(
                "internal_error",
                "Viewer-maintenance migration found multiple project identities",
            )
        if project_rows:
            project_id = str(project_rows[0]["project_id"])
            legacy = connection.execute(
                """
                SELECT viewer_last_success_at, viewer_last_outcome_code,
                       viewer_last_outcome_at
                  FROM project_maintenance
                 WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if legacy is None:
                raise StorageError(
                    "migration_required",
                    "Viewer-maintenance migration requires maintenance state",
                )
            last_success_at = (
                validate_utc_timestamp(
                    str(legacy["viewer_last_success_at"]),
                    field="Viewer success time",
                )
                if legacy["viewer_last_success_at"] is not None
                else None
            )
            last_outcome_code = (
                str(legacy["viewer_last_outcome_code"])
                if legacy["viewer_last_outcome_code"] is not None
                else None
            )
            if last_outcome_code not in {
                None,
                "succeeded",
                "deferred",
                "failed",
            }:
                raise StorageError(
                    "migration_required",
                    "Viewer-maintenance outcome is invalid",
                )
            last_outcome_at = (
                validate_utc_timestamp(
                    str(legacy["viewer_last_outcome_at"]),
                    field="Viewer outcome time",
                )
                if legacy["viewer_last_outcome_at"] is not None
                else None
            )
            if (last_outcome_code is None) != (last_outcome_at is None):
                raise StorageError(
                    "migration_required",
                    "Viewer-maintenance outcome is incomplete",
                )
            connection.execute(
                """
                INSERT INTO viewer_maintenance_state(
                  project_id, source_generation, rendered_generation,
                  last_success_at, last_outcome_code, last_outcome_at
                ) VALUES (?, 0, NULL, ?, ?, ?)
                """,
                (
                    project_id,
                    last_success_at,
                    last_outcome_code,
                    last_outcome_at,
                ),
            )
        if fail_stage == "after_row":
            raise StorageError(
                "internal_error",
                "injected Viewer-maintenance migration failure",
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (13, "viewer_maintenance_state", utc_now()),
        )
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected Viewer-maintenance migration failure",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_project_identity_bindings_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add stable identity and append-only path-binding metadata."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "project-identity migration requires no active transaction",
        )
    existing_version = current_schema_version(connection)
    if existing_version >= 14:
        if missing_migration_versions(connection, existing_version) or (
            required_schema_objects_missing(
                connection,
                schema_version=existing_version,
            )
        ):
            raise StorageError(
                "migration_required",
                "project-identity migration is incomplete",
            )
        return
    if existing_version != 13:
        raise StorageError(
            "migration_required",
            "project-identity migration requires schema version 13",
        )
    if missing_migration_versions(connection, existing_version):
        raise StorageError(
            "migration_required",
            "project-identity migration requires complete schema version 13 history",
        )

    migration_time = utc_now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        project_rows = connection.execute(
            """
            SELECT project_id, canonical_path_hash, display_name, created_at,
                   updated_at
              FROM project_meta
             ORDER BY project_id
            """
        ).fetchall()
        if len(project_rows) > 1:
            raise _unreadable_project_state()

        new_columns = (
            (
                "identity_scheme",
                """
                ALTER TABLE project_meta
                  ADD COLUMN identity_scheme TEXT NOT NULL DEFAULT 'legacy_path_v1'
                  CHECK (identity_scheme IN ('legacy_path_v1', 'uuid_v1'))
                """,
            ),
            (
                "binding_generation",
                """
                ALTER TABLE project_meta
                  ADD COLUMN binding_generation INTEGER NOT NULL DEFAULT 1
                  CHECK (binding_generation >= 1)
                """,
            ),
            (
                "binding_reason",
                """
                ALTER TABLE project_meta
                  ADD COLUMN binding_reason TEXT NOT NULL DEFAULT 'legacy_migration'
                  CHECK (
                    binding_reason IN (
                      'legacy_migration', 'fresh_setup', 'confirmed_relocation'
                    )
                  )
                """,
            ),
            (
                "binding_updated_at",
                """
                ALTER TABLE project_meta
                  ADD COLUMN binding_updated_at TEXT NOT NULL
                  DEFAULT '1970-01-01T00:00:00Z'
                """,
            ),
            (
                "legacy_cleanup_pending",
                """
                ALTER TABLE project_meta
                  ADD COLUMN legacy_cleanup_pending INTEGER NOT NULL DEFAULT 0
                  CHECK (legacy_cleanup_pending IN (0, 1))
                """,
            ),
            (
                "legacy_cleanup_inventory",
                """
                ALTER TABLE project_meta
                  ADD COLUMN legacy_cleanup_inventory TEXT
                  CHECK (
                    legacy_cleanup_inventory IS NULL
                    OR length(legacy_cleanup_inventory) BETWEEN 1 AND 16384
                  )
                """,
            ),
            (
                "legacy_cleanup_fingerprint",
                """
                ALTER TABLE project_meta
                  ADD COLUMN legacy_cleanup_fingerprint TEXT
                  CHECK (
                    legacy_cleanup_fingerprint IS NULL
                    OR (
                      length(legacy_cleanup_fingerprint) = 64
                      AND legacy_cleanup_fingerprint NOT GLOB '*[^0-9a-f]*'
                    )
                  )
                """,
            ),
        )
        for column_name, statement in new_columns:
            if column_exists(connection, "project_meta", column_name):
                raise StorageError(
                    "migration_required",
                    "project-identity migration is incomplete",
                )
            connection.execute(statement)

        if fail_stage == "after_columns":
            raise StorageError(
                "internal_error",
                "injected project-identity migration failure",
            )

        connection.execute(
            """
            CREATE TABLE project_path_binding_history (
              project_id TEXT NOT NULL,
              binding_generation INTEGER NOT NULL
                CHECK (binding_generation >= 1),
              previous_path_hash TEXT,
              canonical_path_hash TEXT NOT NULL,
              display_name TEXT NOT NULL
                CHECK (length(display_name) BETWEEN 1 AND 200),
              reason TEXT NOT NULL
                CHECK (
                  reason IN (
                    'legacy_migration', 'fresh_setup', 'confirmed_relocation'
                  )
                ),
              confirmation_token_digest TEXT,
              bound_at TEXT NOT NULL,
              PRIMARY KEY (project_id, binding_generation),
              FOREIGN KEY (project_id) REFERENCES project_meta(project_id),
              CHECK (
                length(canonical_path_hash) = 64
                AND canonical_path_hash NOT GLOB '*[^0-9a-f]*'
              ),
              CHECK (
                previous_path_hash IS NULL
                OR (
                  length(previous_path_hash) = 64
                  AND previous_path_hash NOT GLOB '*[^0-9a-f]*'
                )
              ),
              CHECK (
                confirmation_token_digest IS NULL
                OR (
                  length(confirmation_token_digest) = 64
                  AND confirmation_token_digest NOT GLOB '*[^0-9a-f]*'
                )
              ),
              CHECK (
                (binding_generation = 1 AND previous_path_hash IS NULL)
                OR
                (binding_generation > 1 AND previous_path_hash IS NOT NULL)
              ),
              CHECK (
                (
                  reason = 'confirmed_relocation'
                  AND binding_generation > 1
                  AND confirmation_token_digest IS NOT NULL
                )
                OR
                (
                  reason != 'confirmed_relocation'
                  AND confirmation_token_digest IS NULL
                )
              )
            )
            """
        )

        trigger_statements = (
            """
            CREATE TRIGGER trg_project_meta_identity_immutable
            BEFORE UPDATE ON project_meta
            WHEN NEW.project_id != OLD.project_id
              OR NEW.identity_scheme != OLD.identity_scheme
              OR NEW.created_at != OLD.created_at
            BEGIN
              SELECT RAISE(ABORT, 'project identity metadata is immutable');
            END
            """,
            """
            CREATE TRIGGER trg_project_meta_no_delete
            BEFORE DELETE ON project_meta
            BEGIN
              SELECT RAISE(ABORT, 'project metadata cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER trg_project_meta_cleanup_insert_valid
            BEFORE INSERT ON project_meta
            WHEN NOT (
              (
                NEW.legacy_cleanup_pending = 0
                AND NEW.legacy_cleanup_inventory IS NULL
                AND NEW.legacy_cleanup_fingerprint IS NULL
              )
              OR
              (
                NEW.legacy_cleanup_pending = 1
                AND NEW.legacy_cleanup_inventory IS NOT NULL
                AND NEW.legacy_cleanup_fingerprint IS NOT NULL
                AND length(NEW.legacy_cleanup_fingerprint) = 64
                AND NEW.legacy_cleanup_fingerprint NOT GLOB '*[^0-9a-f]*'
              )
            )
            BEGIN
              SELECT RAISE(ABORT, 'legacy cleanup metadata is invalid');
            END
            """,
            """
            CREATE TRIGGER trg_project_meta_cleanup_update_valid
            BEFORE UPDATE ON project_meta
            WHEN NOT (
              (
                NEW.legacy_cleanup_pending = 0
                AND NEW.legacy_cleanup_inventory IS NULL
                AND NEW.legacy_cleanup_fingerprint IS NULL
              )
              OR
              (
                NEW.legacy_cleanup_pending = 1
                AND NEW.legacy_cleanup_inventory IS NOT NULL
                AND NEW.legacy_cleanup_fingerprint IS NOT NULL
                AND length(NEW.legacy_cleanup_fingerprint) = 64
                AND NEW.legacy_cleanup_fingerprint NOT GLOB '*[^0-9a-f]*'
              )
            )
            BEGIN
              SELECT RAISE(ABORT, 'legacy cleanup metadata is invalid');
            END
            """,
            """
            CREATE TRIGGER trg_project_path_binding_history_no_update
            BEFORE UPDATE ON project_path_binding_history
            BEGIN
              SELECT RAISE(ABORT, 'project binding history is append-only');
            END
            """,
            """
            CREATE TRIGGER trg_project_path_binding_history_no_delete
            BEFORE DELETE ON project_path_binding_history
            BEGIN
              SELECT RAISE(ABORT, 'project binding history is append-only');
            END
            """,
        )
        for statement in trigger_statements:
            connection.execute(statement)

        for row in project_rows:
            project_id = str(row["project_id"])
            canonical_hash = str(row["canonical_path_hash"])
            display_name = sanitize_project_display_name(str(row["display_name"]))
            validate_identity_project_id(project_id, "legacy_path_v1")
            validate_lower_hex_64(canonical_hash, field="canonical path hash")
            validate_project_display_name(display_name)
            validate_utc_timestamp(str(row["created_at"]), field="project creation time")
            validate_utc_timestamp(str(row["updated_at"]), field="project update time")
            connection.execute(
                """
                UPDATE project_meta
                   SET display_name = ?,
                       binding_updated_at = ?
                 WHERE project_id = ?
                """,
                (display_name, migration_time, project_id),
            )
            connection.execute(
                """
                INSERT INTO project_path_binding_history(
                  project_id, binding_generation, previous_path_hash,
                  canonical_path_hash, display_name, reason,
                  confirmation_token_digest, bound_at
                ) VALUES (?, 1, NULL, ?, ?, 'legacy_migration', NULL, ?)
                """,
                (project_id, canonical_hash, display_name, migration_time),
            )

        if fail_stage == "after_history":
            raise StorageError(
                "internal_error",
                "injected project-identity migration failure",
            )

        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (14, "project_identity_bindings", migration_time),
        )
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected project-identity migration failure",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def completion_cycle_history_schema_statements() -> tuple[str, ...]:
    """Return the exact schema-v15 objects in their migration order."""

    return (
        """
        ALTER TABLE tasks
          ADD COLUMN completion_history_coverage TEXT NOT NULL
            DEFAULT 'legacy_unknown'
            CHECK (completion_history_coverage IN ('legacy_unknown', 'complete'))
        """,
        """
        CREATE UNIQUE INDEX idx_tasks_project_task_identity
          ON tasks(project_id, task_id)
        """,
        """
        CREATE UNIQUE INDEX idx_review_receipts_completion_cycle_reference
          ON review_receipts(
            project_id, task_id, target_kind, target_value,
            target_base_revision, target_generation, review_receipt_id
          )
        """,
        """
        CREATE TABLE task_completion_cycles (
          completion_cycle_id TEXT PRIMARY KEY
            CHECK (
              length(completion_cycle_id) = 36
              AND substr(completion_cycle_id, 1, 20) = 'tg_completion_cycle_'
              AND substr(completion_cycle_id, 21) NOT GLOB '*[^0-9a-f]*'
            ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          saved_cycle_ordinal INTEGER NOT NULL
            CHECK (saved_cycle_ordinal >= 1),

          origin TEXT NOT NULL
            CHECK (origin IN ('native_done', 'legacy_current_done')),
          completeness TEXT NOT NULL
            CHECK (completeness IN ('complete', 'partial')),
          completed_at TEXT,
          recorded_at TEXT NOT NULL,

          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
          verification_expectation TEXT NOT NULL
            CHECK (verification_expectation IN ('specified', 'unspecified')),
          verification_attestation INTEGER
            CHECK (
              verification_attestation IS NULL
              OR verification_attestation IN (0, 1)
            ),

          completion_evidence_kind TEXT NOT NULL
            CHECK (completion_evidence_kind IN (
              'none', 'git_commit', 'external_revision',
              'commit_not_required', 'legacy_unverified'
            )),
          completion_evidence_revision TEXT NOT NULL
            CHECK (length(completion_evidence_revision) <= 500),
          completion_evidence_reason TEXT NOT NULL
            CHECK (length(completion_evidence_reason) <= 1000),
          external_revision_approved INTEGER NOT NULL
            CHECK (external_revision_approved IN (0, 1)),
          completion_commit_required INTEGER NOT NULL
            CHECK (completion_commit_required IN (0, 1)),
          completion_commit_hash TEXT NOT NULL
            CHECK (length(completion_commit_hash) <= 500),

          review_target_kind TEXT NOT NULL
            CHECK (review_target_kind IN (
              '', 'git_commit', 'diff_fingerprint',
              'external_revision', 'git_snapshot'
            )),
          review_target_value TEXT NOT NULL
            CHECK (length(review_target_value) <= 500),
          review_target_base_revision TEXT NOT NULL
            CHECK (length(review_target_base_revision) <= 500),
          review_target_generation INTEGER NOT NULL
            CHECK (review_target_generation >= 0),

          gate_basis_version INTEGER NOT NULL
            CHECK (gate_basis_version IN (0, 1)),
          review_basis_kind TEXT NOT NULL
            CHECK (review_basis_kind IN (
              'unknown', 'independent_passes',
              'self_review_fallback', 'not_required'
            )),
          required_independent_passes INTEGER
            CHECK (
              required_independent_passes IS NULL
              OR required_independent_passes BETWEEN 0 AND 2
            ),
          qualifying_independent_passes INTEGER
            CHECK (
              qualifying_independent_passes IS NULL
              OR qualifying_independent_passes >= 0
            ),
          changes_requested_count INTEGER
            CHECK (changes_requested_count IS NULL OR changes_requested_count >= 0),
          open_high_count INTEGER
            CHECK (open_high_count IS NULL OR open_high_count >= 0),
          open_medium_count INTEGER
            CHECK (open_medium_count IS NULL OR open_medium_count >= 0),
          fresh_review_required_count INTEGER
            CHECK (
              fresh_review_required_count IS NULL
              OR fresh_review_required_count >= 0
            ),
          qualifying_receipt_id_1 TEXT,
          qualifying_receipt_id_2 TEXT,

          FOREIGN KEY (project_id, task_id)
            REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (
            project_id, task_id, review_target_kind, review_target_value,
            review_target_base_revision, review_target_generation,
            qualifying_receipt_id_1
          ) REFERENCES review_receipts(
            project_id, task_id, target_kind, target_value,
            target_base_revision, target_generation, review_receipt_id
          ),
          FOREIGN KEY (
            project_id, task_id, review_target_kind, review_target_value,
            review_target_base_revision, review_target_generation,
            qualifying_receipt_id_2
          ) REFERENCES review_receipts(
            project_id, task_id, target_kind, target_value,
            target_base_revision, target_generation, review_receipt_id
          ),

          CHECK (
            (review_target_kind = ''
              AND review_target_value = ''
              AND review_target_base_revision = ''
              AND review_target_generation = 0)
            OR
            (review_target_kind = 'git_snapshot'
              AND review_target_value != ''
              AND review_target_base_revision != ''
              AND review_target_generation > 0)
            OR
            (review_target_kind IN (
                'git_commit', 'diff_fingerprint', 'external_revision'
              )
              AND review_target_value != ''
              AND review_target_base_revision = ''
              AND review_target_generation > 0)
          ),
          CHECK (
            (completion_evidence_kind = 'none'
              AND completeness = 'partial'
              AND completion_evidence_revision = ''
              AND completion_evidence_reason = ''
              AND external_revision_approved = 0
              AND completion_commit_required = 1
              AND completion_commit_hash = '')
            OR
            (completion_evidence_kind = 'git_commit'
              AND completion_evidence_revision != ''
              AND completion_evidence_reason = ''
              AND external_revision_approved = 0
              AND completion_commit_required = 1
              AND completion_commit_hash = completion_evidence_revision)
            OR
            (completion_evidence_kind = 'external_revision'
              AND completion_evidence_revision != ''
              AND completion_evidence_reason != ''
              AND external_revision_approved = 1
              AND completion_commit_required = 1
              AND completion_commit_hash = completion_evidence_revision)
            OR
            (completion_evidence_kind = 'commit_not_required'
              AND completion_evidence_revision = ''
              AND completion_evidence_reason = ''
              AND external_revision_approved = 0
              AND completion_commit_required = 0
              AND completion_commit_hash = '')
            OR
            (completion_evidence_kind = 'legacy_unverified'
              AND completeness = 'partial'
              AND completion_evidence_revision != ''
              AND completion_evidence_reason = ''
              AND external_revision_approved = 0
              AND completion_commit_hash = completion_evidence_revision)
          ),
          CHECK (
            (origin = 'native_done'
              AND completeness = 'complete'
              AND completed_at IS NOT NULL
              AND verification_attestation = 1
              AND review_target_kind != ''
              AND gate_basis_version = 1)
            OR
            (origin = 'legacy_current_done'
              AND completeness = 'partial'
              AND verification_attestation IS NULL
              AND gate_basis_version = 0)
          ),
          CHECK (
            (gate_basis_version = 0
              AND review_basis_kind = 'unknown'
              AND required_independent_passes IS NULL
              AND qualifying_independent_passes IS NULL
              AND changes_requested_count IS NULL
              AND open_high_count IS NULL
              AND open_medium_count IS NULL
              AND fresh_review_required_count IS NULL
              AND qualifying_receipt_id_1 IS NULL
              AND qualifying_receipt_id_2 IS NULL)
            OR
            (gate_basis_version = 1
              AND required_independent_passes =
                CASE review_tier WHEN 0 THEN 0 WHEN 1 THEN 1 ELSE 2 END
              AND qualifying_independent_passes IS NOT NULL
              AND changes_requested_count = 0
              AND open_high_count = 0
              AND open_medium_count = 0
              AND fresh_review_required_count = 0
              AND (
                (review_basis_kind = 'independent_passes'
                  AND review_tier IN (1, 2)
                  AND qualifying_independent_passes >= required_independent_passes
                  AND qualifying_receipt_id_1 IS NOT NULL
                  AND (
                    (review_tier = 1 AND qualifying_receipt_id_2 IS NULL)
                    OR
                    (review_tier = 2 AND qualifying_receipt_id_2 IS NOT NULL)
                  ))
                OR
                (review_basis_kind = 'self_review_fallback'
                  AND review_tier IN (1, 2)
                  AND qualifying_independent_passes < required_independent_passes
                  AND qualifying_receipt_id_1 IS NOT NULL
                  AND qualifying_receipt_id_2 IS NULL)
                OR
                (review_basis_kind = 'not_required'
                  AND review_tier = 0
                  AND qualifying_receipt_id_1 IS NOT NULL
                  AND qualifying_receipt_id_2 IS NULL)
              ))
          )
        )
        """,
        """
        CREATE UNIQUE INDEX idx_task_completion_cycles_task_ordinal
          ON task_completion_cycles(project_id, task_id, saved_cycle_ordinal)
        """,
        """
        ALTER TABLE task_events
          ADD COLUMN completion_cycle_id TEXT
            REFERENCES task_completion_cycles(completion_cycle_id)
        """,
        """
        CREATE INDEX idx_task_events_completion_cycle
          ON task_events(completion_cycle_id)
          WHERE completion_cycle_id IS NOT NULL
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_no_update
        BEFORE UPDATE ON task_completion_cycles
        BEGIN
          SELECT RAISE(ABORT, 'immutable_completion_cycle');
        END
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_no_delete
        BEFORE DELETE ON task_completion_cycles
        BEGIN
          SELECT RAISE(ABORT, 'immutable_completion_cycle');
        END
        """,
        """
        CREATE TRIGGER trg_tasks_completion_history_coverage_immutable
        BEFORE UPDATE OF completion_history_coverage ON tasks
        WHEN NEW.completion_history_coverage IS NOT OLD.completion_history_coverage
        BEGIN
          SELECT RAISE(ABORT, 'immutable_completion_history_coverage');
        END
        """,
        """
        CREATE TRIGGER trg_task_events_completion_cycle_link_immutable
        BEFORE UPDATE OF completion_cycle_id ON task_events
        WHEN NEW.completion_cycle_id IS NOT OLD.completion_cycle_id
        BEGIN
          SELECT RAISE(ABORT, 'immutable_completion_cycle_link');
        END
        """,
    )


def verification_receipt_schema_statements() -> tuple[str, ...]:
    """Return the exact schema-v17 Receipt objects in migration order."""

    return (
        """
        CREATE TABLE verification_receipts (
          verification_receipt_id TEXT PRIMARY KEY
            CHECK (
              length(verification_receipt_id) = 40
              AND substr(verification_receipt_id, 1, 24) =
                    'tg_verification_receipt_'
              AND substr(verification_receipt_id, 25)
                    NOT GLOB '*[^0-9a-f]*'
            ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          contract_revision INTEGER NOT NULL
            CHECK (contract_revision >= 0),
          verification_expectation_digest TEXT NOT NULL
            CHECK (
              length(verification_expectation_digest) = 64
              AND verification_expectation_digest
                    NOT GLOB '*[^0-9a-f]*'
            ),
          command_label TEXT NOT NULL
            CHECK (
              length(command_label) BETWEEN 1 AND 200
              AND command_label = trim(command_label)
            ),
          result TEXT NOT NULL
            CHECK (result IN ('pass', 'fail', 'timeout')),
          duration_ms INTEGER NOT NULL
            CHECK (duration_ms >= 0),
          scope_coverage TEXT NOT NULL
            CHECK (scope_coverage IN ('full', 'partial')),
          target_kind TEXT NOT NULL
            CHECK (target_kind IN (
              'git_commit', 'diff_fingerprint',
              'external_revision', 'git_snapshot'
            )),
          target_value TEXT NOT NULL
            CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT NOT NULL
            CHECK (length(target_base_revision) <= 500),
          target_generation INTEGER NOT NULL
            CHECK (target_generation >= 1),
          created_at TEXT NOT NULL,

          FOREIGN KEY (project_id, task_id)
            REFERENCES tasks(project_id, task_id),

          CHECK (
            (target_kind = 'git_snapshot'
              AND target_base_revision != '')
            OR
            (target_kind IN (
                'git_commit', 'diff_fingerprint', 'external_revision'
              )
              AND target_base_revision = '')
          )
        )
        """,
        """
        CREATE UNIQUE INDEX idx_verification_receipts_task_generation
          ON verification_receipts(project_id, task_id, target_generation)
        """,
        """
        CREATE INDEX idx_verification_receipts_exact_basis
          ON verification_receipts(
            project_id, task_id, contract_revision,
            verification_expectation_digest,
            target_kind, target_value, target_base_revision,
            target_generation
          )
        """,
        """
        CREATE INDEX idx_verification_receipts_recent
          ON verification_receipts(
            project_id, task_id, created_at DESC,
            verification_receipt_id DESC
          )
        """,
        """
        ALTER TABLE task_completion_cycles
          ADD COLUMN verification_basis_version INTEGER NOT NULL DEFAULT 0
            CHECK (verification_basis_version IN (0, 1))
        """,
        """
        ALTER TABLE task_completion_cycles
          ADD COLUMN verification_expectation_digest TEXT
            CHECK (
              verification_expectation_digest IS NULL
              OR (
                length(verification_expectation_digest) = 64
                AND verification_expectation_digest
                      NOT GLOB '*[^0-9a-f]*'
              )
            )
        """,
        """
        ALTER TABLE task_completion_cycles
          ADD COLUMN verification_receipt_id TEXT
            REFERENCES verification_receipts(verification_receipt_id)
        """,
        """
        CREATE TRIGGER trg_verification_receipts_no_update
        BEFORE UPDATE ON verification_receipts
        BEGIN
          SELECT RAISE(ABORT, 'immutable_verification_receipt');
        END
        """,
        """
        CREATE TRIGGER trg_verification_receipts_no_delete
        BEFORE DELETE ON verification_receipts
        BEGIN
          SELECT RAISE(ABORT, 'immutable_verification_receipt');
        END
        """,
        """
        CREATE TRIGGER trg_verification_receipts_locked_basis_insert
        BEFORE INSERT ON verification_receipts
        WHEN NOT EXISTS (
          SELECT 1
            FROM tasks AS task
           WHERE task.project_id = NEW.project_id
             AND task.task_id = NEW.task_id
             AND task.status IN ('in_progress', 'review_pending')
             AND taskgov_verification_specified(task.verification) = 1
             AND task.current_contract_revision = NEW.contract_revision
             AND task.review_target_kind = NEW.target_kind
             AND task.review_target_value = NEW.target_value
             AND task.review_target_base_revision = NEW.target_base_revision
             AND task.review_target_generation = NEW.target_generation
        )
        BEGIN
          SELECT RAISE(ABORT, 'verification_receipt_basis_mismatch');
        END
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_verification_basis_insert
        BEFORE INSERT ON task_completion_cycles
        WHEN NOT (
          (
            NEW.verification_basis_version = 0
            AND NEW.verification_expectation_digest IS NULL
            AND NEW.verification_receipt_id IS NULL
            AND NEW.origin = 'legacy_current_done'
            AND NEW.completeness = 'partial'
            AND EXISTS (
              SELECT 1
                FROM tasks AS task
               WHERE task.project_id = NEW.project_id
                 AND task.task_id = NEW.task_id
                 AND task.status = 'done'
                 AND task.completion_history_coverage = 'legacy_unknown'
            )
            AND NOT EXISTS (
              SELECT 1
                FROM task_completion_cycles AS earlier
               WHERE earlier.project_id = NEW.project_id
                 AND earlier.task_id = NEW.task_id
            )
          )
          OR
          (
            NEW.verification_basis_version = 1
            AND NEW.verification_expectation_digest IS NOT NULL
            AND NEW.origin = 'native_done'
            AND EXISTS (
              SELECT 1
                FROM tasks AS task
               WHERE task.project_id = NEW.project_id
                 AND task.task_id = NEW.task_id
                 AND task.current_contract_revision = NEW.contract_revision
                 AND task.review_target_kind = NEW.review_target_kind
                 AND task.review_target_value = NEW.review_target_value
                 AND task.review_target_base_revision =
                       NEW.review_target_base_revision
                 AND task.review_target_generation =
                       NEW.review_target_generation
                 AND (
                   (
                     taskgov_verification_specified(task.verification) = 0
                     AND NEW.verification_expectation = 'unspecified'
                     AND NEW.verification_receipt_id IS NULL
                   )
                   OR
                   (
                     taskgov_verification_specified(task.verification) = 1
                     AND NEW.verification_expectation = 'specified'
                     AND NEW.verification_receipt_id IS NOT NULL
                     AND EXISTS (
                       SELECT 1
                         FROM verification_receipts AS receipt
                        WHERE receipt.verification_receipt_id =
                              NEW.verification_receipt_id
                          AND receipt.project_id = NEW.project_id
                          AND receipt.task_id = NEW.task_id
                          AND receipt.contract_revision =
                                NEW.contract_revision
                          AND receipt.verification_expectation_digest =
                                NEW.verification_expectation_digest
                          AND receipt.target_kind = NEW.review_target_kind
                          AND receipt.target_value = NEW.review_target_value
                          AND receipt.target_base_revision =
                                NEW.review_target_base_revision
                          AND receipt.target_generation =
                                NEW.review_target_generation
                          AND receipt.result = 'pass'
                          AND receipt.scope_coverage = 'full'
                     )
                   )
                 )
            )
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_verification_basis');
        END
        """,
    )


_REVIEW_PROVENANCE_GUARD_TRIGGER_SQL = """
CREATE TRIGGER trg_review_receipts_provenance_basis_insert
BEFORE INSERT ON review_receipts
WHEN NOT (
  (
    NEW.receipt_kind = 'not_required'
    AND NEW.review_provenance_basis_version = 0
    AND NEW.review_provenance_id IS NULL
  )
  OR
  (
    NEW.receipt_kind IN ('independent', 'self_review_fallback')
    AND NEW.review_provenance_basis_version = 1
    AND NEW.review_provenance_id IS NOT NULL
  )
)
BEGIN
  SELECT RAISE(ABORT, 'invalid_review_provenance_basis');
END
"""


def evidence_ledger_capture_schema_statements() -> tuple[str, ...]:
    """Return the additive schema-v18 capture foundation in migration order."""

    statements = (
        """
        CREATE TABLE authority_snapshots (
          authority_snapshot_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          generation INTEGER NOT NULL CHECK (generation > 0),
          task_title TEXT NOT NULL CHECK (length(task_title) BETWEEN 1 AND 200),
          task_description TEXT NOT NULL CHECK (length(task_description) <= 4000),
          review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
          verification TEXT NOT NULL CHECK (length(verification) <= 1000),
          verification_digest TEXT NOT NULL CHECK (
            length(verification_digest) = 64
            AND verification_digest NOT GLOB '*[^0-9a-f]*'
          ),
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          contract_state TEXT NOT NULL CHECK (
            contract_state IN ('contract_specified', 'contract_unspecified')
          ),
          contract_scope TEXT NOT NULL,
          contract_acceptance TEXT NOT NULL,
          contract_constraints TEXT NOT NULL,
          contract_authority_ref TEXT NOT NULL,
          basis_digest TEXT NOT NULL CHECK (
            length(basis_digest) = 71
            AND substr(basis_digest, 1, 7) = 'sha256:'
            AND substr(basis_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          producer_class TEXT NOT NULL CHECK (
            producer_class IN ('taskgov_core', 'legacy_migration')
          ),
          producer_version INTEGER NOT NULL CHECK (producer_version = 1),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, generation),
          UNIQUE (project_id, task_id, authority_snapshot_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
        )
        """,
        """
        CREATE TABLE contract_criteria (
          criterion_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          criterion_kind TEXT NOT NULL CHECK (
            criterion_kind IN ('acceptance', 'verification')
          ),
          criterion_text TEXT NOT NULL,
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, criterion_kind, digest),
          UNIQUE (project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
        )
        """,
        """
        CREATE TABLE authority_snapshot_criteria (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          authority_snapshot_id TEXT NOT NULL,
          criterion_kind TEXT NOT NULL CHECK (
            criterion_kind IN ('acceptance', 'verification')
          ),
          criterion_id TEXT NOT NULL,
          PRIMARY KEY (authority_snapshot_id, criterion_kind),
          UNIQUE (authority_snapshot_id, criterion_id),
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id),
          FOREIGN KEY (project_id, task_id, criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id)
        )
        """,
        """
        CREATE TABLE review_receipt_provenance (
          review_provenance_id TEXT PRIMARY KEY,
          review_receipt_id TEXT NOT NULL UNIQUE,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          provenance_version INTEGER NOT NULL CHECK (provenance_version = 1),
          reviewer_class TEXT NOT NULL CHECK (reviewer_class IN (
            'human', 'llm', 'deterministic_tool', 'hybrid', 'unknown'
          )),
          model_state TEXT NOT NULL CHECK (model_state IN (
            'declared', 'not_applicable', 'unknown'
          )),
          declared_model_id TEXT,
          skill_state TEXT NOT NULL CHECK (skill_state IN (
            'declared', 'not_applicable', 'not_used', 'unknown'
          )),
          declared_skill_id TEXT,
          declared_skill_version TEXT,
          context_relation TEXT NOT NULL CHECK (context_relation IN (
            'same_context', 'forked_context', 'fresh_context',
            'external_context', 'not_applicable', 'unknown'
          )),
          assurance_class TEXT NOT NULL CHECK (assurance_class = 'bound_attestation'),
          producer_class TEXT NOT NULL CHECK (producer_class = 'trusted_caller'),
          producer_version INTEGER NOT NULL CHECK (producer_version = 1),
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, review_provenance_id),
          FOREIGN KEY (review_receipt_id) REFERENCES review_receipts(review_receipt_id)
            DEFERRABLE INITIALLY DEFERRED,
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
        )
        """,
        """
        CREATE TABLE review_receipt_provenance_codes (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          review_provenance_id TEXT NOT NULL,
          code_kind TEXT NOT NULL CHECK (code_kind IN ('profile', 'lens', 'method')),
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          code TEXT NOT NULL,
          PRIMARY KEY (review_provenance_id, code_kind, ordinal),
          UNIQUE (review_provenance_id, code_kind, code),
          FOREIGN KEY (project_id, task_id, review_provenance_id)
            REFERENCES review_receipt_provenance(
              project_id, task_id, review_provenance_id
            )
        )
        """,
        """
        CREATE TABLE artifact_manifests (
          artifact_manifest_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          state TEXT NOT NULL CHECK (state IN ('complete_git', 'opaque_target')),
          object_format TEXT CHECK (object_format IS NULL OR object_format IN ('sha1', 'sha256')),
          comparison_base TEXT,
          target_kind TEXT NOT NULL CHECK (target_kind IN (
            'git_commit', 'diff_fingerprint', 'external_revision', 'git_snapshot'
          )),
          target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT NOT NULL CHECK (length(target_base_revision) <= 500),
          target_generation INTEGER NOT NULL CHECK (target_generation > 0),
          authority_snapshot_id TEXT NOT NULL,
          acceptance_criterion_id TEXT,
          verification_criterion_id TEXT,
          omission_code TEXT CHECK (
            omission_code IS NULL OR omission_code = 'artifact_content_not_observed'
          ),
          entry_count INTEGER NOT NULL CHECK (entry_count BETWEEN 0 AND 10000),
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, target_generation),
          UNIQUE (project_id, task_id, artifact_manifest_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id),
          FOREIGN KEY (project_id, task_id, acceptance_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, verification_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id)
        )
        """,
        """
        CREATE TABLE artifact_manifest_entries (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          artifact_manifest_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          entry_kind TEXT NOT NULL CHECK (entry_kind IN ('add', 'modify', 'delete', 'rename')),
          old_path TEXT,
          new_path TEXT,
          before_mode TEXT,
          before_object_id TEXT,
          after_mode TEXT,
          after_object_id TEXT,
          PRIMARY KEY (artifact_manifest_id, ordinal),
          FOREIGN KEY (project_id, task_id, artifact_manifest_id)
            REFERENCES artifact_manifests(
              project_id, task_id, artifact_manifest_id
            )
        )
        """,
        """
        CREATE TABLE evidence_references (
          evidence_reference_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          source_kind TEXT NOT NULL CHECK (source_kind IN (
            'artifact_manifest', 'verification_receipt', 'review_receipt',
            'review_finding', 'completion_evidence', 'derived_analysis',
            'runner_observation'
          )),
          source_state TEXT NOT NULL,
          source_id TEXT NOT NULL,
          assurance_class TEXT NOT NULL CHECK (assurance_class IN (
            'machine_observed', 'bound_attestation', 'deterministically_derived',
            'external_reference', 'legacy_unknown', 'llm_derived'
          )),
          producer_class TEXT NOT NULL CHECK (producer_class IN (
            'taskgov_core', 'taskgov_git', 'trusted_caller', 'legacy_migration',
            'external_system', 'batch_analyzer', 'verification_runner'
          )),
          producer_version INTEGER NOT NULL CHECK (producer_version > 0),
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          authority_snapshot_id TEXT NOT NULL,
          acceptance_criterion_id TEXT,
          verification_criterion_id TEXT,
          target_kind TEXT NOT NULL CHECK (target_kind IN (
            'git_commit', 'diff_fingerprint', 'external_revision', 'git_snapshot'
          )),
          target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT NOT NULL CHECK (length(target_base_revision) <= 500),
          target_generation INTEGER NOT NULL CHECK (target_generation > 0),
          completion_cycle_id TEXT,
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, source_kind, source_id),
          UNIQUE (project_id, task_id, evidence_reference_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id),
          FOREIGN KEY (project_id, task_id, acceptance_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, verification_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (completion_cycle_id) REFERENCES task_completion_cycles(completion_cycle_id)
            DEFERRABLE INITIALLY DEFERRED
        )
        """,
        """ALTER TABLE tasks ADD COLUMN current_authority_snapshot_id TEXT
             REFERENCES authority_snapshots(authority_snapshot_id)""",
        """ALTER TABLE tasks ADD COLUMN current_authority_snapshot_generation INTEGER NOT NULL DEFAULT 0
             CHECK (current_authority_snapshot_generation >= 0)""",
        """ALTER TABLE tasks ADD COLUMN review_target_capture_version INTEGER NOT NULL DEFAULT 0
             CHECK (review_target_capture_version IN (0, 1))""",
        """ALTER TABLE tasks ADD COLUMN review_target_authority_snapshot_id TEXT
             REFERENCES authority_snapshots(authority_snapshot_id)""",
        """ALTER TABLE tasks ADD COLUMN review_target_acceptance_criterion_id TEXT
             REFERENCES contract_criteria(criterion_id)""",
        """ALTER TABLE tasks ADD COLUMN review_target_verification_criterion_id TEXT
             REFERENCES contract_criteria(criterion_id)""",
        """ALTER TABLE tasks ADD COLUMN review_target_artifact_manifest_id TEXT
             REFERENCES artifact_manifests(artifact_manifest_id)""",
        """ALTER TABLE review_receipts ADD COLUMN review_provenance_basis_version INTEGER NOT NULL DEFAULT 0
             CHECK (review_provenance_basis_version IN (0, 1))""",
        """ALTER TABLE review_receipts ADD COLUMN review_provenance_id TEXT
             REFERENCES review_receipt_provenance(review_provenance_id)
             DEFERRABLE INITIALLY DEFERRED""",
        """ALTER TABLE verification_receipts ADD COLUMN verification_subject_basis_version INTEGER NOT NULL DEFAULT 0
             CHECK (verification_subject_basis_version IN (0, 1))""",
        """ALTER TABLE verification_receipts ADD COLUMN subject_authority_snapshot_id TEXT
             REFERENCES authority_snapshots(authority_snapshot_id)""",
        """ALTER TABLE verification_receipts ADD COLUMN subject_verification_criterion_id TEXT
             REFERENCES contract_criteria(criterion_id)""",
        """ALTER TABLE task_completion_cycles ADD COLUMN verification_subject_basis_version INTEGER NOT NULL DEFAULT 0
             CHECK (verification_subject_basis_version IN (0, 1))""",
        """ALTER TABLE task_completion_cycles ADD COLUMN subject_authority_snapshot_id TEXT
             REFERENCES authority_snapshots(authority_snapshot_id)""",
        """ALTER TABLE task_completion_cycles ADD COLUMN subject_verification_criterion_id TEXT
             REFERENCES contract_criteria(criterion_id)""",
        """CREATE UNIQUE INDEX idx_authority_snapshots_task_generation
             ON authority_snapshots(project_id, task_id, generation)""",
        """CREATE UNIQUE INDEX idx_contract_criteria_task_kind_digest
             ON contract_criteria(project_id, task_id, criterion_kind, digest)""",
        """CREATE UNIQUE INDEX idx_review_provenance_receipt
             ON review_receipt_provenance(review_receipt_id)""",
        """CREATE UNIQUE INDEX idx_artifact_manifests_target
             ON artifact_manifests(project_id, task_id, target_generation)""",
        """CREATE UNIQUE INDEX idx_evidence_references_source
             ON evidence_references(project_id, task_id, source_kind, source_id)""",
    )
    immutable_tables = (
        "authority_snapshots",
        "contract_criteria",
        "authority_snapshot_criteria",
        "review_receipt_provenance",
        "review_receipt_provenance_codes",
        "artifact_manifests",
        "artifact_manifest_entries",
        "evidence_references",
    )
    immutable_triggers = tuple(
        statement
        for table_name in immutable_tables
        for statement in (
            f"""CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name}
                 BEGIN SELECT RAISE(ABORT, 'immutable_evidence_ledger'); END""",
            f"""CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name}
                 BEGIN SELECT RAISE(ABORT, 'immutable_evidence_ledger'); END""",
        )
    )
    subject_triggers = (
        _REVIEW_PROVENANCE_GUARD_TRIGGER_SQL,
        """
        CREATE TRIGGER trg_verification_receipts_subject_basis_insert
        BEFORE INSERT ON verification_receipts
        WHEN NOT (
          NEW.verification_subject_basis_version = 1
          AND NEW.command_label = 'taskgov-owned-verification-subject-v1'
          AND NEW.subject_authority_snapshot_id IS NOT NULL
          AND NEW.subject_verification_criterion_id IS NOT NULL
          AND EXISTS (
            SELECT 1
              FROM tasks AS task
             WHERE task.project_id = NEW.project_id
               AND task.task_id = NEW.task_id
               AND task.review_target_kind = NEW.target_kind
               AND task.review_target_value = NEW.target_value
               AND task.review_target_base_revision = NEW.target_base_revision
               AND task.review_target_generation = NEW.target_generation
               AND task.review_target_capture_version = 1
               AND task.review_target_authority_snapshot_id =
                     NEW.subject_authority_snapshot_id
               AND task.review_target_verification_criterion_id =
                     NEW.subject_verification_criterion_id
               AND task.review_target_artifact_manifest_id IS NOT NULL
          )
          AND EXISTS (
            SELECT 1
              FROM authority_snapshot_criteria AS link
             WHERE link.project_id = NEW.project_id
               AND link.task_id = NEW.task_id
               AND link.authority_snapshot_id =
                     NEW.subject_authority_snapshot_id
               AND link.criterion_kind = 'verification'
               AND link.criterion_id = NEW.subject_verification_criterion_id
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_verification_subject_basis');
        END
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_subject_basis_insert
        BEFORE INSERT ON task_completion_cycles
        WHEN NOT (
          (
            NEW.origin = 'legacy_current_done'
            AND NEW.completeness = 'partial'
            AND NEW.verification_subject_basis_version = 0
            AND NEW.subject_authority_snapshot_id IS NULL
            AND NEW.subject_verification_criterion_id IS NULL
          )
          OR
          (
            NEW.origin = 'native_done'
            AND NEW.completeness = 'complete'
            AND NEW.verification_subject_basis_version = 1
            AND EXISTS (
              SELECT 1
                FROM tasks AS task
               WHERE task.project_id = NEW.project_id
                 AND task.task_id = NEW.task_id
                 AND task.review_target_kind = NEW.review_target_kind
                 AND task.review_target_value = NEW.review_target_value
                 AND task.review_target_base_revision =
                       NEW.review_target_base_revision
                 AND task.review_target_generation = NEW.review_target_generation
                 AND task.review_target_capture_version = 1
                 AND task.review_target_artifact_manifest_id IS NOT NULL
                 AND (
                   (
                     NEW.verification_expectation = 'specified'
                     AND NEW.subject_authority_snapshot_id =
                           task.review_target_authority_snapshot_id
                     AND NEW.subject_verification_criterion_id =
                           task.review_target_verification_criterion_id
                     AND EXISTS (
                       SELECT 1
                         FROM authority_snapshot_criteria AS link
                        WHERE link.project_id = NEW.project_id
                          AND link.task_id = NEW.task_id
                          AND link.authority_snapshot_id =
                                NEW.subject_authority_snapshot_id
                          AND link.criterion_kind = 'verification'
                          AND link.criterion_id =
                                NEW.subject_verification_criterion_id
                     )
                   )
                   OR
                   (
                     NEW.verification_expectation = 'unspecified'
                     AND NEW.subject_authority_snapshot_id IS NULL
                     AND NEW.subject_verification_criterion_id IS NULL
                     AND task.review_target_verification_criterion_id IS NULL
                   )
                 )
            )
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_subject_basis');
        END
        """,
    )
    return (*statements, *immutable_triggers, *subject_triggers)


def completion_evidence_bundle_schema_statements() -> tuple[str, ...]:
    """Return the schema-v19 Bundle foundation in migration order."""

    statements = (
        """
        CREATE TABLE criterion_evidence_links (
          criterion_evidence_link_id TEXT PRIMARY KEY CHECK (
            length(criterion_evidence_link_id) = 43
            AND substr(criterion_evidence_link_id, 1, 27) =
                  'tg_criterion_evidence_link_'
            AND substr(criterion_evidence_link_id, 28)
                  NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          criterion_id TEXT NOT NULL,
          evidence_reference_id TEXT NOT NULL,
          relation TEXT NOT NULL CHECK (relation IN (
            'verification_attestation', 'review_assessment',
            'review_finding', 'completion_basis', 'derived_analysis',
            'runner_observation'
          )),
          assurance_class TEXT NOT NULL CHECK (assurance_class IN (
            'machine_observed', 'bound_attestation',
            'deterministically_derived', 'external_reference',
            'legacy_unknown', 'llm_derived'
          )),
          producer_class TEXT NOT NULL CHECK (producer_class IN (
            'taskgov_core', 'taskgov_git', 'trusted_caller',
            'legacy_migration', 'external_system', 'batch_analyzer',
            'verification_runner'
          )),
          producer_version INTEGER NOT NULL CHECK (producer_version > 0),
          created_at TEXT NOT NULL,
          UNIQUE (
            project_id, task_id, criterion_id,
            evidence_reference_id, relation
          ),
          UNIQUE (project_id, task_id, criterion_evidence_link_id),
          FOREIGN KEY (project_id, task_id, criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, evidence_reference_id)
            REFERENCES evidence_references(
              project_id, task_id, evidence_reference_id
            )
        )
        """,
        """
        CREATE TABLE completion_evidence_bundles (
          completion_evidence_bundle_id TEXT PRIMARY KEY CHECK (
            length(completion_evidence_bundle_id) = 46
            AND substr(completion_evidence_bundle_id, 1, 30) =
                  'tg_completion_evidence_bundle_'
            AND substr(completion_evidence_bundle_id, 31)
                  NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          completion_cycle_id TEXT NOT NULL,
          cycle_ordinal INTEGER NOT NULL CHECK (cycle_ordinal > 0),
          source_schema_version INTEGER NOT NULL
            CHECK (source_schema_version = 19),
          bundle_version INTEGER NOT NULL CHECK (bundle_version = 1),
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          authority_snapshot_id TEXT NOT NULL,
          acceptance_criterion_id TEXT,
          verification_criterion_id TEXT,
          target_kind TEXT NOT NULL CHECK (target_kind IN (
            'git_commit', 'diff_fingerprint', 'external_revision',
            'git_snapshot'
          )),
          target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT NOT NULL
            CHECK (length(target_base_revision) <= 500),
          target_generation INTEGER NOT NULL CHECK (target_generation > 0),
          target_capture_version INTEGER NOT NULL
            CHECK (target_capture_version = 1),
          artifact_manifest_id TEXT NOT NULL,
          verification_receipt_id TEXT,
          omission_mask INTEGER NOT NULL CHECK (omission_mask BETWEEN 0 AND 15),
          sealed_at TEXT NOT NULL,
          bundle_digest TEXT NOT NULL CHECK (
            length(bundle_digest) = 71
            AND substr(bundle_digest, 1, 7) = 'sha256:'
            AND substr(bundle_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          payload_size_bytes INTEGER NOT NULL CHECK (
            payload_size_bytes BETWEEN 1 AND 16777216
          ),
          UNIQUE (project_id, task_id, completion_evidence_bundle_id),
          UNIQUE (project_id, task_id, completion_cycle_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(
              project_id, task_id, authority_snapshot_id
            ),
          FOREIGN KEY (project_id, task_id, acceptance_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, verification_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, artifact_manifest_id)
            REFERENCES artifact_manifests(
              project_id, task_id, artifact_manifest_id
            ),
          FOREIGN KEY (verification_receipt_id)
            REFERENCES verification_receipts(verification_receipt_id),
          FOREIGN KEY (completion_cycle_id)
            REFERENCES task_completion_cycles(completion_cycle_id)
            DEFERRABLE INITIALLY DEFERRED
        )
        """,
        """
        CREATE TABLE completion_bundle_members (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          completion_evidence_bundle_id TEXT NOT NULL,
          member_kind TEXT NOT NULL CHECK (
            member_kind IN ('criterion_link', 'evidence_reference')
          ),
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          criterion_evidence_link_id TEXT,
          evidence_reference_id TEXT,
          PRIMARY KEY (completion_evidence_bundle_id, member_kind, ordinal),
          UNIQUE (completion_evidence_bundle_id, criterion_evidence_link_id),
          UNIQUE (completion_evidence_bundle_id, evidence_reference_id),
          CHECK (
            (member_kind = 'criterion_link'
              AND criterion_evidence_link_id IS NOT NULL
              AND evidence_reference_id IS NULL)
            OR
            (member_kind = 'evidence_reference'
              AND criterion_evidence_link_id IS NULL
              AND evidence_reference_id IS NOT NULL)
          ),
          FOREIGN KEY (project_id, task_id, completion_evidence_bundle_id)
            REFERENCES completion_evidence_bundles(
              project_id, task_id, completion_evidence_bundle_id
            ),
          FOREIGN KEY (project_id, task_id, criterion_evidence_link_id)
            REFERENCES criterion_evidence_links(
              project_id, task_id, criterion_evidence_link_id
            ),
          FOREIGN KEY (project_id, task_id, evidence_reference_id)
            REFERENCES evidence_references(
              project_id, task_id, evidence_reference_id
            )
        )
        """,
        """
        CREATE TABLE completion_bundle_finding_snapshots (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          completion_evidence_bundle_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          review_finding_id TEXT NOT NULL,
          review_receipt_id TEXT NOT NULL,
          target_generation INTEGER NOT NULL CHECK (target_generation > 0),
          severity TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
          summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 1000),
          status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
          resolution_summary TEXT NOT NULL CHECK (length(resolution_summary) <= 1000),
          created_at TEXT NOT NULL,
          resolved_at TEXT,
          evidence_reference_id TEXT,
          assurance_class TEXT NOT NULL CHECK (
            assurance_class IN ('bound_attestation', 'legacy_unknown')
          ),
          producer_class TEXT NOT NULL CHECK (
            producer_class IN ('trusted_caller', 'legacy_migration')
          ),
          producer_version INTEGER NOT NULL CHECK (producer_version = 1),
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          PRIMARY KEY (completion_evidence_bundle_id, ordinal),
          UNIQUE (completion_evidence_bundle_id, review_finding_id),
          CHECK (
            (status = 'open'
              AND resolution_summary = '' AND resolved_at IS NULL)
            OR
            (status = 'resolved'
              AND resolution_summary != '' AND resolved_at IS NOT NULL)
          ),
          CHECK (
            (evidence_reference_id IS NOT NULL
              AND assurance_class = 'bound_attestation'
              AND producer_class = 'trusted_caller')
            OR
            (evidence_reference_id IS NULL
              AND assurance_class = 'legacy_unknown'
              AND producer_class = 'legacy_migration')
          ),
          FOREIGN KEY (project_id, task_id, completion_evidence_bundle_id)
            REFERENCES completion_evidence_bundles(
              project_id, task_id, completion_evidence_bundle_id
            ),
          FOREIGN KEY (review_finding_id)
            REFERENCES review_findings(review_finding_id),
          FOREIGN KEY (review_receipt_id)
            REFERENCES review_receipts(review_receipt_id),
          FOREIGN KEY (project_id, task_id, evidence_reference_id)
            REFERENCES evidence_references(
              project_id, task_id, evidence_reference_id
            )
        )
        """,
        """
        CREATE TABLE evidence_projection_state (
          project_id TEXT PRIMARY KEY,
          source_generation INTEGER NOT NULL CHECK (source_generation >= 0),
          published_generation INTEGER CHECK (
            published_generation IS NULL
            OR (published_generation >= 0
              AND published_generation <= source_generation)
          ),
          index_digest TEXT CHECK (
            index_digest IS NULL
            OR (
              length(index_digest) = 71
              AND substr(index_digest, 1, 7) = 'sha256:'
              AND substr(index_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          last_success_at TEXT,
          last_outcome_code TEXT CHECK (
            last_outcome_code IS NULL
            OR last_outcome_code IN ('succeeded', 'deferred', 'failed')
          ),
          last_outcome_at TEXT,
          CHECK (
            (published_generation IS NULL AND index_digest IS NULL)
            OR
            (published_generation IS NOT NULL AND index_digest IS NOT NULL)
          ),
          CHECK (
            (last_outcome_code IS NULL AND last_outcome_at IS NULL)
            OR
            (last_outcome_code IS NOT NULL AND last_outcome_at IS NOT NULL)
          ),
          FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
        )
        """,
        """ALTER TABLE task_completion_cycles
             ADD COLUMN evidence_basis_version INTEGER NOT NULL DEFAULT 0
             CHECK (evidence_basis_version IN (0, 1))""",
        """ALTER TABLE task_completion_cycles
             ADD COLUMN completion_evidence_bundle_id TEXT
             REFERENCES completion_evidence_bundles(completion_evidence_bundle_id)
             DEFERRABLE INITIALLY DEFERRED""",
        """CREATE INDEX idx_criterion_evidence_links_reference
             ON criterion_evidence_links(project_id, task_id, evidence_reference_id)""",
        """CREATE UNIQUE INDEX idx_completion_evidence_bundles_task_cycle
             ON completion_evidence_bundles(project_id, task_id, completion_cycle_id)""",
        """CREATE INDEX idx_completion_bundle_members_reference
             ON completion_bundle_members(completion_evidence_bundle_id, member_kind, ordinal)""",
        """CREATE INDEX idx_completion_bundle_finding_snapshots_order
             ON completion_bundle_finding_snapshots(
               completion_evidence_bundle_id, target_generation, created_at,
               review_finding_id
             )""",
    )
    immutable_tables = (
        "criterion_evidence_links",
        "completion_evidence_bundles",
        "completion_bundle_members",
        "completion_bundle_finding_snapshots",
    )
    immutable_triggers = tuple(
        statement
        for table_name in immutable_tables
        for statement in (
            f"""CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name}
                 BEGIN SELECT RAISE(ABORT, 'immutable_completion_evidence'); END""",
            f"""CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name}
                 BEGIN SELECT RAISE(ABORT, 'immutable_completion_evidence'); END""",
        )
    )
    guard_triggers = (
        """
        CREATE TRIGGER trg_criterion_evidence_links_matrix_insert
        BEFORE INSERT ON criterion_evidence_links
        WHEN NOT EXISTS (
          SELECT 1
            FROM contract_criteria AS criterion
            JOIN evidence_references AS reference
              ON reference.project_id = criterion.project_id
             AND reference.task_id = criterion.task_id
           WHERE criterion.project_id = NEW.project_id
             AND criterion.task_id = NEW.task_id
             AND criterion.criterion_id = NEW.criterion_id
             AND reference.evidence_reference_id = NEW.evidence_reference_id
             AND reference.assurance_class = NEW.assurance_class
             AND reference.producer_class = NEW.producer_class
             AND reference.producer_version = NEW.producer_version
             AND (
               (NEW.relation = 'verification_attestation'
                 AND criterion.criterion_kind = 'verification'
                 AND reference.source_kind = 'verification_receipt')
               OR
               (NEW.relation = 'review_assessment'
                 AND criterion.criterion_kind = 'acceptance'
                 AND reference.source_kind = 'review_receipt')
               OR
               (NEW.relation = 'review_finding'
                 AND criterion.criterion_kind = 'acceptance'
                 AND reference.source_kind = 'review_finding')
               OR
               (NEW.relation = 'completion_basis'
                 AND criterion.criterion_kind = 'acceptance'
                 AND reference.source_kind IN (
                   'artifact_manifest', 'completion_evidence'
                 ))
             )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_criterion_evidence_link');
        END
        """,
        """
        CREATE TRIGGER trg_completion_bundle_members_matrix_insert
        BEFORE INSERT ON completion_bundle_members
        WHEN NOT (
          (
            NEW.member_kind = 'criterion_link'
            AND EXISTS (
              SELECT 1 FROM criterion_evidence_links AS link
               WHERE link.project_id = NEW.project_id
                 AND link.task_id = NEW.task_id
                 AND link.criterion_evidence_link_id =
                       NEW.criterion_evidence_link_id
            )
          )
          OR
          (
            NEW.member_kind = 'evidence_reference'
            AND EXISTS (
              SELECT 1 FROM evidence_references AS reference
               WHERE reference.project_id = NEW.project_id
                 AND reference.task_id = NEW.task_id
                 AND reference.evidence_reference_id =
                       NEW.evidence_reference_id
            )
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_bundle_member');
        END
        """,
        """
        CREATE TRIGGER trg_completion_bundle_finding_snapshots_matrix_insert
        BEFORE INSERT ON completion_bundle_finding_snapshots
        WHEN NOT EXISTS (
          SELECT 1
            FROM review_findings AS finding
            JOIN review_receipts AS receipt
              ON receipt.review_receipt_id = finding.review_receipt_id
           WHERE finding.review_finding_id = NEW.review_finding_id
             AND finding.review_receipt_id = NEW.review_receipt_id
             AND receipt.project_id = NEW.project_id
             AND receipt.task_id = NEW.task_id
             AND receipt.target_generation = NEW.target_generation
             AND finding.severity = NEW.severity
             AND finding.summary = NEW.summary
             AND finding.status = NEW.status
             AND finding.resolution_summary = NEW.resolution_summary
             AND finding.created_at = NEW.created_at
             AND finding.resolved_at IS NEW.resolved_at
             AND (
               (
                 NEW.evidence_reference_id IS NULL
                 AND NEW.assurance_class = 'legacy_unknown'
                 AND NEW.producer_class = 'legacy_migration'
                 AND NEW.producer_version = 1
               )
               OR
               (
                 NEW.evidence_reference_id IS NOT NULL
                 AND NEW.assurance_class = 'bound_attestation'
                 AND NEW.producer_class = 'trusted_caller'
                 AND NEW.producer_version = 1
                 AND EXISTS (
                   SELECT 1 FROM evidence_references AS reference
                    WHERE reference.project_id = NEW.project_id
                      AND reference.task_id = NEW.task_id
                      AND reference.evidence_reference_id =
                            NEW.evidence_reference_id
                      AND reference.source_kind = 'review_finding'
                      AND reference.source_id = NEW.review_finding_id
                 )
               )
             )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_finding_snapshot');
        END
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_evidence_basis_insert
        BEFORE INSERT ON task_completion_cycles
        WHEN NOT (
          (
            NEW.origin = 'legacy_current_done'
            AND NEW.evidence_basis_version = 0
            AND NEW.completion_evidence_bundle_id IS NULL
          )
          OR
          (
            NEW.origin = 'native_done'
            AND NEW.evidence_basis_version = 1
            AND NEW.completion_evidence_bundle_id IS NOT NULL
            AND EXISTS (
              SELECT 1 FROM completion_evidence_bundles AS bundle
               WHERE bundle.project_id = NEW.project_id
                 AND bundle.task_id = NEW.task_id
                 AND bundle.completion_cycle_id = NEW.completion_cycle_id
                 AND bundle.cycle_ordinal = NEW.saved_cycle_ordinal
                 AND bundle.completion_evidence_bundle_id =
                       NEW.completion_evidence_bundle_id
            )
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_evidence_basis');
        END
        """,
    )
    return (*statements, *immutable_triggers, *guard_triggers)


_R3A_SCHEMA20_RUNNER_TABLES = (
    "verification_runner_resolutions",
    "verification_runner_attempts",
    "verification_runner_sandbox_events",
    "verification_runner_observations",
)

_R3A_SCHEMA20_COLUMN_ALTERS = (
    """ALTER TABLE tasks
         ADD COLUMN review_target_runner_basis_version INTEGER NOT NULL DEFAULT 0
         CHECK (review_target_runner_basis_version IN (0, 2))""",
    """ALTER TABLE task_completion_cycles
         ADD COLUMN verification_basis_kind TEXT""",
    """ALTER TABLE task_completion_cycles
         ADD COLUMN verification_runner_observation_id TEXT""",
)


def _completion_evidence_bundle_v20_table_sql(
    table_name: str = "completion_evidence_bundles",
    *,
    schema_version: int = PRIVATE_SCHEMA20_VERSION,
) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]*", table_name) is None:
        raise AssertionError("invalid private Bundle table name")
    if schema_version not in {PRIVATE_SCHEMA20_VERSION, PRIVATE_SCHEMA21_VERSION}:
        raise AssertionError("invalid private Bundle schema version")
    basis_kinds = (
        "'caller_attestation', 'not_required', 'runner_observation'"
        if schema_version == PRIVATE_SCHEMA21_VERSION
        else "'caller_attestation', 'not_required'"
    )
    source21_arms = (
        """
        OR
        (source_schema_version = 21 AND bundle_version = 2
          AND verification_basis_kind = 'caller_attestation'
          AND verification_receipt_id IS NOT NULL
          AND verification_runner_observation_id IS NULL)
        OR
        (source_schema_version = 21 AND bundle_version = 2
          AND verification_basis_kind = 'not_required'
          AND verification_receipt_id IS NULL
          AND verification_runner_observation_id IS NULL)
        OR
        (source_schema_version = 21 AND bundle_version = 2
          AND verification_basis_kind = 'runner_observation'
          AND verification_receipt_id IS NULL
          AND verification_runner_observation_id IS NOT NULL)
        """
        if schema_version == PRIVATE_SCHEMA21_VERSION
        else ""
    )
    return f"""
    CREATE TABLE {table_name} (
      completion_evidence_bundle_id TEXT PRIMARY KEY CHECK (
        length(completion_evidence_bundle_id) = 46
        AND substr(completion_evidence_bundle_id, 1, 30) =
              'tg_completion_evidence_bundle_'
        AND substr(completion_evidence_bundle_id, 31)
              NOT GLOB '*[^0-9a-f]*'
      ),
      project_id TEXT NOT NULL,
      task_id TEXT NOT NULL,
      completion_cycle_id TEXT NOT NULL,
      cycle_ordinal INTEGER NOT NULL CHECK (cycle_ordinal > 0),
      source_schema_version INTEGER NOT NULL,
      bundle_version INTEGER NOT NULL,
      contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
      authority_snapshot_id TEXT NOT NULL,
      acceptance_criterion_id TEXT,
      verification_criterion_id TEXT,
      target_kind TEXT NOT NULL CHECK (target_kind IN (
        'git_commit', 'diff_fingerprint', 'external_revision', 'git_snapshot'
      )),
      target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
      target_base_revision TEXT NOT NULL CHECK (length(target_base_revision) <= 500),
      target_generation INTEGER NOT NULL CHECK (target_generation > 0),
      target_capture_version INTEGER NOT NULL CHECK (target_capture_version = 1),
      artifact_manifest_id TEXT NOT NULL,
      verification_receipt_id TEXT,
      verification_basis_kind TEXT CHECK (
        verification_basis_kind IS NULL
        OR verification_basis_kind IN ({basis_kinds})
      ),
      verification_runner_observation_id TEXT,
      omission_mask INTEGER NOT NULL CHECK (omission_mask BETWEEN 0 AND 15),
      sealed_at TEXT NOT NULL,
      bundle_digest TEXT NOT NULL CHECK (
        length(bundle_digest) = 71
        AND substr(bundle_digest, 1, 7) = 'sha256:'
        AND substr(bundle_digest, 8) NOT GLOB '*[^0-9a-f]*'
      ),
      payload_size_bytes INTEGER NOT NULL CHECK (
        payload_size_bytes BETWEEN 1 AND 16777216
      ),
      UNIQUE (project_id, task_id, completion_evidence_bundle_id),
      UNIQUE (project_id, task_id, completion_cycle_id),
      CHECK (
        (source_schema_version = 19 AND bundle_version = 1
          AND verification_basis_kind IS NULL
          AND verification_runner_observation_id IS NULL)
        OR
        (source_schema_version = 20 AND bundle_version = 2
          AND verification_basis_kind = 'caller_attestation'
          AND verification_receipt_id IS NOT NULL
          AND verification_runner_observation_id IS NULL)
        OR
        (source_schema_version = 20 AND bundle_version = 2
          AND verification_basis_kind = 'not_required'
          AND verification_receipt_id IS NULL
          AND verification_runner_observation_id IS NULL)
        {source21_arms}
      ),
      FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
      FOREIGN KEY (project_id, task_id, authority_snapshot_id)
        REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id),
      FOREIGN KEY (project_id, task_id, acceptance_criterion_id)
        REFERENCES contract_criteria(project_id, task_id, criterion_id),
      FOREIGN KEY (project_id, task_id, verification_criterion_id)
        REFERENCES contract_criteria(project_id, task_id, criterion_id),
      FOREIGN KEY (project_id, task_id, artifact_manifest_id)
        REFERENCES artifact_manifests(project_id, task_id, artifact_manifest_id),
      FOREIGN KEY (verification_receipt_id)
        REFERENCES verification_receipts(verification_receipt_id),
      FOREIGN KEY (
        project_id, task_id, target_generation,
        verification_runner_observation_id
      ) REFERENCES verification_runner_observations(
        project_id, task_id, target_generation,
        verification_runner_observation_id
      ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
      FOREIGN KEY (completion_cycle_id)
        REFERENCES task_completion_cycles(completion_cycle_id)
        DEFERRABLE INITIALLY DEFERRED
    )
    """


def _verification_runner_table_statements(
    *,
    schema_version: int = PRIVATE_SCHEMA20_VERSION,
) -> tuple[str, ...]:
    if schema_version not in {PRIVATE_SCHEMA20_VERSION, PRIVATE_SCHEMA21_VERSION}:
        raise AssertionError("invalid private Runner schema version")
    statements = (
        """
        CREATE TABLE verification_runner_resolutions (
          verification_runner_resolution_id TEXT PRIMARY KEY CHECK (
            length(verification_runner_resolution_id) =
              length('tg_verification_runner_resolution_') + 16
            AND substr(verification_runner_resolution_id, 1,
              length('tg_verification_runner_resolution_')) =
              'tg_verification_runner_resolution_'
            AND substr(verification_runner_resolution_id,
              length('tg_verification_runner_resolution_') + 1)
              NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 1),
          authority_snapshot_id TEXT NOT NULL,
          verification_criterion_id TEXT NOT NULL,
          verification_expectation_digest TEXT NOT NULL CHECK (
            length(verification_expectation_digest) = 64
            AND verification_expectation_digest NOT GLOB '*[^0-9a-f]*'
          ),
          verification_criterion_digest TEXT NOT NULL CHECK (
            length(verification_criterion_digest) = 71
            AND substr(verification_criterion_digest, 1, 7) = 'sha256:'
            AND substr(verification_criterion_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          target_kind TEXT NOT NULL CHECK (
            length(target_kind) BETWEEN 1 AND 64
            AND substr(target_kind, 1, 1) GLOB '[a-z]'
            AND substr(target_kind, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT CHECK (
            target_base_revision IS NULL
            OR length(target_base_revision) BETWEEN 1 AND 128
          ),
          target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
          target_capture_version INTEGER NOT NULL CHECK (target_capture_version = 1),
          artifact_manifest_id TEXT NOT NULL,
          target_material_digest TEXT CHECK (
            target_material_digest IS NULL OR (
              length(target_material_digest) = 71
              AND substr(target_material_digest, 1, 7) = 'sha256:'
              AND substr(target_material_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          plan_state TEXT NOT NULL CHECK (
            length(plan_state) BETWEEN 1 AND 64
            AND substr(plan_state, 1, 1) GLOB '[a-z]'
            AND substr(plan_state, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          plan_blob_object_id TEXT CHECK (
            plan_blob_object_id IS NULL
            OR length(plan_blob_object_id) BETWEEN 1 AND 500
          ),
          plan_raw_digest TEXT CHECK (
            plan_raw_digest IS NULL OR (
              length(plan_raw_digest) = 71
              AND substr(plan_raw_digest, 1, 7) = 'sha256:'
              AND substr(plan_raw_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          plan_id TEXT CHECK (plan_id IS NULL OR length(plan_id) BETWEEN 1 AND 200),
          plan_version INTEGER CHECK (plan_version >= 1),
          plan_semantic_digest TEXT CHECK (
            plan_semantic_digest IS NULL OR (
              length(plan_semantic_digest) = 71
              AND substr(plan_semantic_digest, 1, 7) = 'sha256:'
              AND substr(plan_semantic_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          selected_entry_digest TEXT CHECK (
            selected_entry_digest IS NULL OR (
              length(selected_entry_digest) = 71
              AND substr(selected_entry_digest, 1, 7) = 'sha256:'
              AND substr(selected_entry_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          coverage TEXT NOT NULL CHECK (
            length(coverage) BETWEEN 1 AND 64
            AND substr(coverage, 1, 1) GLOB '[a-z]'
            AND substr(coverage, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          step_count INTEGER NOT NULL CHECK (step_count BETWEEN 0 AND 16),
          runner_contract_version INTEGER NOT NULL CHECK (runner_contract_version = 1),
          runner_implementation_version TEXT NOT NULL CHECK (
            runner_implementation_version = 'taskgov-verification-runner/1'
          ),
          runner_implementation_digest TEXT NOT NULL CHECK (
            length(runner_implementation_digest) = 71
            AND substr(runner_implementation_digest, 1, 7) = 'sha256:'
            AND substr(runner_implementation_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          runner_policy_digest TEXT NOT NULL CHECK (
            length(runner_policy_digest) = 71
            AND substr(runner_policy_digest, 1, 7) = 'sha256:'
            AND substr(runner_policy_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          runtime_digest TEXT CHECK (
            runtime_digest IS NULL OR (
              length(runtime_digest) = 71
              AND substr(runtime_digest, 1, 7) = 'sha256:'
              AND substr(runtime_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          gate_eligibility_version INTEGER NOT NULL CHECK (gate_eligibility_version = 0),
          trigger TEXT NOT NULL CHECK (trigger = 'review_target_set_v1'),
          route TEXT NOT NULL CHECK (
            length(route) BETWEEN 1 AND 64
            AND substr(route, 1, 1) GLOB '[a-z]'
            AND substr(route, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          reason TEXT CHECK (
            reason IS NULL OR (
              length(reason) BETWEEN 1 AND 64
              AND substr(reason, 1, 1) GLOB '[a-z]'
              AND substr(reason, 2) NOT GLOB '*[^a-z0-9_]*'
            )
          ),
          idempotency_digest TEXT NOT NULL CHECK (
            length(idempotency_digest) = 71
            AND substr(idempotency_digest, 1, 7) = 'sha256:'
            AND substr(idempotency_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          FOREIGN KEY (project_id, task_id)
            REFERENCES tasks(project_id, task_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (project_id, task_id, verification_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (project_id, task_id, artifact_manifest_id)
            REFERENCES artifact_manifests(project_id, task_id, artifact_manifest_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE
        )
        """,
        """
        CREATE TABLE verification_runner_attempts (
          verification_runner_attempt_id TEXT PRIMARY KEY CHECK (
            length(verification_runner_attempt_id) =
              length('tg_verification_runner_attempt_') + 16
            AND substr(verification_runner_attempt_id, 1,
              length('tg_verification_runner_attempt_')) =
              'tg_verification_runner_attempt_'
            AND substr(verification_runner_attempt_id,
              length('tg_verification_runner_attempt_') + 1)
              NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
          gate_eligibility_version INTEGER NOT NULL CHECK (gate_eligibility_version = 0),
          verification_runner_resolution_id TEXT NOT NULL,
          target_material_digest TEXT NOT NULL CHECK (
            length(target_material_digest) = 71
            AND substr(target_material_digest, 1, 7) = 'sha256:'
            AND substr(target_material_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          runner_implementation_digest TEXT NOT NULL CHECK (
            length(runner_implementation_digest) = 71
            AND substr(runner_implementation_digest, 1, 7) = 'sha256:'
            AND substr(runner_implementation_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          attempt_digest TEXT NOT NULL CHECK (
            length(attempt_digest) = 71
            AND substr(attempt_digest, 1, 7) = 'sha256:'
            AND substr(attempt_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          intent_recorded_at TEXT NOT NULL,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            verification_runner_resolution_id
          ) REFERENCES verification_runner_resolutions(
            project_id, task_id, target_generation,
            verification_runner_resolution_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE
        )
        """,
        """
        CREATE TABLE verification_runner_observations (
          verification_runner_observation_id TEXT PRIMARY KEY CHECK (
            length(verification_runner_observation_id) =
              length('tg_verification_runner_observation_') + 16
            AND substr(verification_runner_observation_id, 1,
              length('tg_verification_runner_observation_')) =
              'tg_verification_runner_observation_'
            AND substr(verification_runner_observation_id,
              length('tg_verification_runner_observation_') + 1)
              NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
          gate_eligibility_version INTEGER NOT NULL CHECK (gate_eligibility_version = 0),
          verification_runner_resolution_id TEXT NOT NULL,
          verification_runner_attempt_id TEXT,
          runner_implementation_digest TEXT NOT NULL CHECK (
            length(runner_implementation_digest) = 71
            AND substr(runner_implementation_digest, 1, 7) = 'sha256:'
            AND substr(runner_implementation_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          route TEXT NOT NULL CHECK (
            length(route) BETWEEN 1 AND 64
            AND substr(route, 1, 1) GLOB '[a-z]'
            AND substr(route, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          launch_state TEXT NOT NULL CHECK (
            length(launch_state) BETWEEN 1 AND 64
            AND substr(launch_state, 1, 1) GLOB '[a-z]'
            AND substr(launch_state, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          outcome TEXT NOT NULL CHECK (
            length(outcome) BETWEEN 1 AND 64
            AND substr(outcome, 1, 1) GLOB '[a-z]'
            AND substr(outcome, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          reason TEXT CHECK (
            reason IS NULL OR (
              length(reason) BETWEEN 1 AND 64
              AND substr(reason, 1, 1) GLOB '[a-z]'
              AND substr(reason, 2) NOT GLOB '*[^a-z0-9_]*'
            )
          ),
          complete_plan INTEGER NOT NULL CHECK (complete_plan IN (0, 1)),
          total_step_count INTEGER NOT NULL CHECK (total_step_count BETWEEN 0 AND 16),
          completed_step_count INTEGER NOT NULL CHECK (
            completed_step_count BETWEEN 0 AND total_step_count
          ),
          failed_step_ordinal INTEGER CHECK (
            failed_step_ordinal BETWEEN 1 AND total_step_count
          ),
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL,
          duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
          cpu_time_ms INTEGER CHECK (cpu_time_ms >= 0),
          peak_job_memory_bytes INTEGER CHECK (
            peak_job_memory_bytes >= 0
          ),
          total_process_count INTEGER CHECK (
            total_process_count >= 0
          ),
          sanitized_result_digest TEXT NOT NULL CHECK (
            length(sanitized_result_digest) = 71
            AND substr(sanitized_result_digest, 1, 7) = 'sha256:'
            AND substr(sanitized_result_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            verification_runner_resolution_id
          ) REFERENCES verification_runner_resolutions(
            project_id, task_id, target_generation,
            verification_runner_resolution_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            verification_runner_attempt_id
          ) REFERENCES verification_runner_attempts(
            project_id, task_id, target_generation,
            verification_runner_attempt_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE
        )
        """,
        """
        CREATE TABLE verification_runner_sandbox_events (
          verification_runner_sandbox_event_id TEXT PRIMARY KEY CHECK (
            length(verification_runner_sandbox_event_id) =
              length('tg_verification_runner_sandbox_event_') + 16
            AND substr(verification_runner_sandbox_event_id, 1,
              length('tg_verification_runner_sandbox_event_')) =
              'tg_verification_runner_sandbox_event_'
            AND substr(verification_runner_sandbox_event_id,
              length('tg_verification_runner_sandbox_event_') + 1)
              NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
          verification_runner_attempt_id TEXT NOT NULL,
          event_kind TEXT NOT NULL CHECK (event_kind = 'attempt_cleanup_succeeded'),
          event_digest TEXT NOT NULL CHECK (
            length(event_digest) = 71
            AND substr(event_digest, 1, 7) = 'sha256:'
            AND substr(event_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          terminal_observation_id TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            verification_runner_attempt_id
          ) REFERENCES verification_runner_attempts(
            project_id, task_id, target_generation,
            verification_runner_attempt_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            terminal_observation_id
          ) REFERENCES verification_runner_observations(
            project_id, task_id, target_generation,
            verification_runner_observation_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT
            DEFERRABLE INITIALLY DEFERRED
        )
        """,
    )
    if schema_version == PRIVATE_SCHEMA20_VERSION:
        return statements
    return tuple(
        statement.replace(
            "CHECK (gate_eligibility_version = 0)",
            "CHECK (gate_eligibility_version IN (0, 1))",
        )
        for statement in statements
    )


def _verification_runner_index_statements() -> tuple[str, ...]:
    return (
        """CREATE UNIQUE INDEX idx_verification_runner_resolutions_parent
             ON verification_runner_resolutions(
               project_id, task_id, target_generation,
               verification_runner_resolution_id
             )""",
        """CREATE INDEX idx_verification_runner_resolutions_task_generation
             ON verification_runner_resolutions(
               project_id, task_id, target_generation
             )""",
        """CREATE UNIQUE INDEX idx_verification_runner_attempts_parent
             ON verification_runner_attempts(
               project_id, task_id, target_generation,
               verification_runner_attempt_id
             )""",
        """CREATE INDEX idx_verification_runner_attempts_task_generation
             ON verification_runner_attempts(
               project_id, task_id, target_generation
             )""",
        """CREATE INDEX idx_verification_runner_attempts_resolution
             ON verification_runner_attempts(
               project_id, task_id, target_generation,
               verification_runner_resolution_id
             )""",
        """CREATE INDEX idx_verification_runner_sandbox_events_attempt_kind
             ON verification_runner_sandbox_events(
               project_id, task_id, target_generation,
               verification_runner_attempt_id, event_kind
             )""",
        """CREATE UNIQUE INDEX idx_verification_runner_observations_parent
             ON verification_runner_observations(
               project_id, task_id, target_generation,
               verification_runner_observation_id
             )""",
        """CREATE INDEX idx_verification_runner_observations_task_generation
             ON verification_runner_observations(
               project_id, task_id, target_generation
             )""",
        """CREATE INDEX idx_verification_runner_observations_resolution
             ON verification_runner_observations(
               project_id, task_id, target_generation,
               verification_runner_resolution_id
             )""",
        """CREATE INDEX idx_verification_runner_observations_attempt
             ON verification_runner_observations(
               project_id, task_id, target_generation,
               verification_runner_attempt_id
             ) WHERE verification_runner_attempt_id IS NOT NULL""",
    )


def _verification_runner_trigger_statements(
    *,
    schema_version: int = PRIVATE_SCHEMA20_VERSION,
) -> tuple[str, ...]:
    if schema_version not in {PRIVATE_SCHEMA20_VERSION, PRIVATE_SCHEMA21_VERSION}:
        raise AssertionError("invalid private Runner schema version")
    immutable = tuple(
        f"""CREATE TRIGGER trg_{table_name}_{suffix}
              BEFORE {verb} ON {table_name} FOR EACH ROW
              BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END"""
        for table_name in _R3A_SCHEMA20_RUNNER_TABLES
        for suffix, verb in (("no_update", "UPDATE"), ("no_delete", "DELETE"))
    )
    parent = (
        """
        CREATE TRIGGER trg_verification_runner_resolutions_parent_insert
        BEFORE INSERT ON verification_runner_resolutions FOR EACH ROW
        WHEN NOT EXISTS (
          SELECT 1
            FROM tasks AS t
            JOIN authority_snapshots AS s
              ON s.project_id = t.project_id
             AND s.task_id = t.task_id
             AND s.authority_snapshot_id = NEW.authority_snapshot_id
            JOIN artifact_manifests AS m
              ON m.project_id = t.project_id
             AND m.task_id = t.task_id
             AND m.artifact_manifest_id = NEW.artifact_manifest_id
            JOIN contract_criteria AS vc
              ON vc.project_id = t.project_id
             AND vc.task_id = t.task_id
             AND vc.criterion_id = NEW.verification_criterion_id
            JOIN authority_snapshot_criteria AS vcm
              ON vcm.project_id = t.project_id
             AND vcm.task_id = t.task_id
             AND vcm.authority_snapshot_id = NEW.authority_snapshot_id
             AND vcm.criterion_kind = 'verification'
             AND vcm.criterion_id = NEW.verification_criterion_id
           WHERE t.project_id = NEW.project_id
             AND t.task_id = NEW.task_id
             AND t.current_contract_revision = NEW.contract_revision
             AND t.review_target_authority_snapshot_id = NEW.authority_snapshot_id
             AND t.review_target_acceptance_criterion_id IS m.acceptance_criterion_id
             AND t.review_target_verification_criterion_id =
                   NEW.verification_criterion_id
             AND t.review_target_kind = NEW.target_kind
             AND t.review_target_value = NEW.target_value
             AND t.review_target_base_revision =
                   COALESCE(NEW.target_base_revision, '')
             AND t.review_target_generation = NEW.target_generation
             AND t.review_target_capture_version = NEW.target_capture_version
             AND t.review_target_artifact_manifest_id = NEW.artifact_manifest_id
             AND t.review_target_runner_basis_version = 0
             AND s.contract_revision = NEW.contract_revision
             AND s.verification_digest = NEW.verification_expectation_digest
             AND vc.criterion_kind = 'verification'
             AND vc.digest = NEW.verification_criterion_digest
             AND m.authority_snapshot_id = NEW.authority_snapshot_id
             AND m.acceptance_criterion_id IS
                   t.review_target_acceptance_criterion_id
             AND m.verification_criterion_id = NEW.verification_criterion_id
             AND m.target_kind = NEW.target_kind
             AND m.target_value = NEW.target_value
             AND m.target_base_revision = COALESCE(NEW.target_base_revision, '')
             AND m.target_generation = NEW.target_generation
             AND (
               t.review_target_acceptance_criterion_id IS NULL
               OR EXISTS (
                 SELECT 1
                   FROM contract_criteria AS ac
                   JOIN authority_snapshot_criteria AS acm
                     ON acm.project_id = ac.project_id
                    AND acm.task_id = ac.task_id
                    AND acm.authority_snapshot_id = NEW.authority_snapshot_id
                    AND acm.criterion_kind = 'acceptance'
                    AND acm.criterion_id = ac.criterion_id
                  WHERE ac.project_id = t.project_id
                    AND ac.task_id = t.task_id
                    AND ac.criterion_id =
                          t.review_target_acceptance_criterion_id
                    AND ac.criterion_kind = 'acceptance'
               )
             )
        )
        BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END
        """,
        """
        CREATE TRIGGER trg_verification_runner_attempts_parent_insert
        BEFORE INSERT ON verification_runner_attempts FOR EACH ROW
        WHEN NOT EXISTS (
          SELECT 1 FROM verification_runner_resolutions AS r
           WHERE r.project_id = NEW.project_id
             AND r.task_id = NEW.task_id
             AND r.target_generation = NEW.target_generation
             AND r.verification_runner_resolution_id =
                   NEW.verification_runner_resolution_id
             AND r.target_material_digest = NEW.target_material_digest
             AND r.runner_implementation_digest =
                   NEW.runner_implementation_digest
        )
        BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END
        """,
        """
        CREATE TRIGGER trg_verification_runner_observations_parent_insert
        BEFORE INSERT ON verification_runner_observations FOR EACH ROW
        WHEN NOT EXISTS (
          SELECT 1 FROM verification_runner_resolutions AS r
           WHERE r.project_id = NEW.project_id
             AND r.task_id = NEW.task_id
             AND r.target_generation = NEW.target_generation
             AND r.verification_runner_resolution_id =
                   NEW.verification_runner_resolution_id
             AND r.runner_implementation_digest =
                   NEW.runner_implementation_digest
             AND (
               NEW.verification_runner_attempt_id IS NULL
               OR EXISTS (
                 SELECT 1 FROM verification_runner_attempts AS a
                  WHERE a.project_id = NEW.project_id
                    AND a.task_id = NEW.task_id
                    AND a.target_generation = NEW.target_generation
                    AND a.verification_runner_attempt_id =
                          NEW.verification_runner_attempt_id
                    AND a.verification_runner_resolution_id =
                          NEW.verification_runner_resolution_id
                    AND a.runner_implementation_digest =
                          NEW.runner_implementation_digest
               )
             )
        )
        BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END
        """,
        """
        CREATE TRIGGER trg_verification_runner_sandbox_events_parent_insert
        BEFORE INSERT ON verification_runner_sandbox_events FOR EACH ROW
        WHEN NOT EXISTS (
          SELECT 1 FROM verification_runner_attempts AS a
           WHERE a.project_id = NEW.project_id
             AND a.task_id = NEW.task_id
             AND a.target_generation = NEW.target_generation
             AND a.verification_runner_attempt_id =
                   NEW.verification_runner_attempt_id
             AND (
               NEW.terminal_observation_id IS NULL
               OR EXISTS (
                 SELECT 1 FROM verification_runner_observations AS o
                  WHERE o.project_id = NEW.project_id
                    AND o.task_id = NEW.task_id
                    AND o.target_generation = NEW.target_generation
                    AND o.verification_runner_observation_id =
                          NEW.terminal_observation_id
                    AND o.verification_runner_attempt_id =
                          NEW.verification_runner_attempt_id
               )
             )
        )
        BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END
        """,
    )
    statements = (*immutable, *parent)
    if schema_version == PRIVATE_SCHEMA20_VERSION:
        return statements
    result: list[str] = []
    for statement in statements:
        normalized = _normalized_schema_sql(statement)
        if "trg_verification_runner_resolutions_parent_insert" in normalized:
            statement = statement.replace(
                "AND t.review_target_runner_basis_version = 0",
                """AND (
               (NEW.gate_eligibility_version = 0
                 AND t.review_target_runner_basis_version = 0)
               OR
               (NEW.gate_eligibility_version = 1
                 AND t.review_target_runner_basis_version = 2)
             )""",
            )
        elif "trg_verification_runner_attempts_parent_insert" in normalized:
            statement = statement.replace(
                "AND r.runner_implementation_digest =\n                   NEW.runner_implementation_digest",
                """AND r.runner_implementation_digest =
                   NEW.runner_implementation_digest
             AND r.gate_eligibility_version = NEW.gate_eligibility_version""",
            )
        elif "trg_verification_runner_observations_parent_insert" in normalized:
            statement = statement.replace(
                "AND r.runner_implementation_digest =\n                   NEW.runner_implementation_digest",
                """AND r.runner_implementation_digest =
                   NEW.runner_implementation_digest
             AND r.gate_eligibility_version = NEW.gate_eligibility_version""",
            ).replace(
                "AND a.runner_implementation_digest =\n                          NEW.runner_implementation_digest",
                """AND a.runner_implementation_digest =
                          NEW.runner_implementation_digest
                    AND a.gate_eligibility_version =
                          NEW.gate_eligibility_version""",
            )
        result.append(statement)
    return tuple(result)


def _criterion_evidence_links_v20_matrix_trigger_sql() -> str:
    return """
    CREATE TRIGGER trg_criterion_evidence_links_matrix_insert
    BEFORE INSERT ON criterion_evidence_links
    WHEN NOT EXISTS (
      SELECT 1
        FROM contract_criteria AS criterion
        JOIN evidence_references AS reference
          ON reference.project_id = criterion.project_id
         AND reference.task_id = criterion.task_id
       WHERE criterion.project_id = NEW.project_id
         AND criterion.task_id = NEW.task_id
         AND criterion.criterion_id = NEW.criterion_id
         AND reference.evidence_reference_id = NEW.evidence_reference_id
         AND reference.assurance_class = NEW.assurance_class
         AND reference.producer_class = NEW.producer_class
         AND reference.producer_version = NEW.producer_version
         AND (
           (NEW.relation = 'verification_attestation'
             AND criterion.criterion_kind = 'verification'
             AND reference.source_kind = 'verification_receipt')
           OR
           (NEW.relation = 'review_assessment'
             AND criterion.criterion_kind = 'acceptance'
             AND reference.source_kind = 'review_receipt')
           OR
           (NEW.relation = 'review_finding'
             AND criterion.criterion_kind = 'acceptance'
             AND reference.source_kind = 'review_finding')
           OR
           (NEW.relation = 'completion_basis'
             AND criterion.criterion_kind = 'acceptance'
             AND reference.source_kind IN (
               'artifact_manifest', 'completion_evidence'
             ))
           OR
           (NEW.relation = 'runner_observation'
             AND criterion.criterion_kind = 'verification'
             AND reference.source_kind = 'runner_observation'
             AND reference.verification_criterion_id = NEW.criterion_id
             AND NEW.assurance_class = 'machine_observed'
             AND NEW.producer_class = 'verification_runner'
             AND NEW.producer_version = 1)
         )
    )
    BEGIN
      SELECT RAISE(ABORT, 'invalid_criterion_evidence_link');
    END
    """


def _verification_runner_shadow_schema_statements() -> tuple[str, ...]:
    return (
        *_R3A_SCHEMA20_COLUMN_ALTERS,
        _completion_evidence_bundle_v20_table_sql(),
        *_verification_runner_table_statements(),
        *_verification_runner_index_statements(),
        *_verification_runner_trigger_statements(),
        _criterion_evidence_links_v20_matrix_trigger_sql(),
    )


def _verification_runner_resolution_digest_projection(row: Any) -> dict[str, Any]:
    fields = (
        "project_id", "task_id", "contract_revision",
        "authority_snapshot_id", "verification_criterion_id",
        "verification_expectation_digest", "verification_criterion_digest",
        "target_kind", "target_value", "target_base_revision",
        "target_generation", "target_capture_version", "artifact_manifest_id",
        "target_material_digest", "plan_state", "plan_blob_object_id",
        "plan_raw_digest", "plan_id", "plan_version",
        "plan_semantic_digest", "selected_entry_digest", "coverage",
        "step_count", "runner_contract_version",
        "runner_implementation_version", "runner_implementation_digest",
        "runner_policy_digest", "runtime_digest", "gate_eligibility_version",
        "trigger", "route", "reason",
    )
    try:
        projection = {field: row[field] for field in fields}
    except (KeyError, IndexError, TypeError) as exc:
        raise _unreadable_project_state() from exc
    projection["sandbox_provider"] = None
    projection["sandbox_policy_digest"] = None
    return projection


def _verification_runner_attempt_digest_projection(row: Any) -> dict[str, Any]:
    try:
        return {
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "target_generation": row["target_generation"],
            "gate_eligibility_version": row["gate_eligibility_version"],
            "target_material_digest": row["target_material_digest"],
            "resolution_id": row["verification_runner_resolution_id"],
            "runner_implementation_digest": row[
                "runner_implementation_digest"
            ],
            "sandbox_instance_digest": None,
        }
    except (KeyError, IndexError, TypeError) as exc:
        raise _unreadable_project_state() from exc


def _bundle_v20_recreated_object_statements() -> tuple[str, ...]:
    return (
        """CREATE UNIQUE INDEX idx_completion_evidence_bundles_task_cycle
             ON completion_evidence_bundles(
               project_id, task_id, completion_cycle_id
             )""",
        """CREATE TRIGGER trg_completion_evidence_bundles_no_update
             BEFORE UPDATE ON completion_evidence_bundles
             BEGIN SELECT RAISE(ABORT, 'immutable_completion_evidence'); END""",
        """CREATE TRIGGER trg_completion_evidence_bundles_no_delete
             BEFORE DELETE ON completion_evidence_bundles
             BEGIN SELECT RAISE(ABORT, 'immutable_completion_evidence'); END""",
    )


def _task_completion_cycle_verification_basis_v21_trigger_sql() -> str:
    """Return the closed schema-v21 completion-basis insert guard."""

    return """
    CREATE TRIGGER trg_task_completion_cycles_verification_basis_insert
    BEFORE INSERT ON task_completion_cycles
    WHEN NOT (
      (
        NEW.verification_basis_version = 0
        AND NEW.verification_expectation_digest IS NULL
        AND NEW.verification_receipt_id IS NULL
        AND NEW.verification_basis_kind IS NULL
        AND NEW.verification_runner_observation_id IS NULL
        AND NEW.origin = 'legacy_current_done'
        AND NEW.completeness = 'partial'
        AND EXISTS (
          SELECT 1 FROM tasks AS task
           WHERE task.project_id = NEW.project_id
             AND task.task_id = NEW.task_id
             AND task.status = 'done'
             AND task.completion_history_coverage = 'legacy_unknown'
        )
        AND NOT EXISTS (
          SELECT 1 FROM task_completion_cycles AS earlier
           WHERE earlier.project_id = NEW.project_id
             AND earlier.task_id = NEW.task_id
        )
      )
      OR
      (
        NEW.verification_basis_version = 1
        AND NEW.verification_expectation_digest IS NOT NULL
        AND NEW.origin = 'native_done'
        AND EXISTS (
          SELECT 1 FROM tasks AS task
           WHERE task.project_id = NEW.project_id
             AND task.task_id = NEW.task_id
             AND task.current_contract_revision = NEW.contract_revision
             AND task.review_target_kind = NEW.review_target_kind
             AND task.review_target_value = NEW.review_target_value
             AND task.review_target_base_revision = NEW.review_target_base_revision
             AND task.review_target_generation = NEW.review_target_generation
             AND (
               (
                 task.review_target_runner_basis_version = 0
                 AND NEW.verification_runner_observation_id IS NULL
                 AND (
                   (
                     taskgov_verification_specified(task.verification) = 0
                     AND NEW.verification_expectation = 'unspecified'
                     AND NEW.verification_basis_kind = 'not_required'
                     AND NEW.verification_receipt_id IS NULL
                   )
                   OR
                   (
                     taskgov_verification_specified(task.verification) = 1
                     AND NEW.verification_expectation = 'specified'
                     AND NEW.verification_basis_kind = 'caller_attestation'
                     AND EXISTS (
                       SELECT 1 FROM verification_receipts AS receipt
                        WHERE receipt.verification_receipt_id =
                              NEW.verification_receipt_id
                          AND receipt.project_id = NEW.project_id
                          AND receipt.task_id = NEW.task_id
                          AND receipt.contract_revision = NEW.contract_revision
                          AND receipt.verification_expectation_digest =
                                NEW.verification_expectation_digest
                          AND receipt.target_kind = NEW.review_target_kind
                          AND receipt.target_value = NEW.review_target_value
                          AND receipt.target_base_revision =
                                NEW.review_target_base_revision
                          AND receipt.target_generation =
                                NEW.review_target_generation
                          AND receipt.result = 'pass'
                          AND receipt.scope_coverage = 'full'
                     )
                   )
                 )
               )
               OR
               (
                 task.review_target_runner_basis_version = 2
                 AND taskgov_verification_specified(task.verification) = 1
                 AND NEW.verification_expectation = 'specified'
                 AND EXISTS (
                   SELECT 1
                     FROM verification_runner_resolutions AS resolution
                     JOIN verification_runner_attempts AS attempt
                       ON attempt.project_id = resolution.project_id
                      AND attempt.task_id = resolution.task_id
                      AND attempt.target_generation = resolution.target_generation
                      AND attempt.verification_runner_resolution_id =
                            resolution.verification_runner_resolution_id
                      AND attempt.gate_eligibility_version = 1
                     JOIN verification_runner_observations AS observation
                       ON observation.project_id = resolution.project_id
                      AND observation.task_id = resolution.task_id
                      AND observation.target_generation =
                            resolution.target_generation
                      AND observation.verification_runner_resolution_id =
                            resolution.verification_runner_resolution_id
                      AND observation.verification_runner_attempt_id =
                            attempt.verification_runner_attempt_id
                      AND observation.gate_eligibility_version = 1
                     JOIN verification_runner_sandbox_events AS cleanup
                       ON cleanup.project_id = observation.project_id
                      AND cleanup.task_id = observation.task_id
                      AND cleanup.target_generation = observation.target_generation
                      AND cleanup.verification_runner_attempt_id =
                            attempt.verification_runner_attempt_id
                      AND cleanup.terminal_observation_id =
                            observation.verification_runner_observation_id
                     JOIN evidence_references AS reference
                       ON reference.project_id = observation.project_id
                      AND reference.task_id = observation.task_id
                      AND reference.source_kind = 'runner_observation'
                      AND reference.source_id =
                            observation.verification_runner_observation_id
                     JOIN criterion_evidence_links AS link
                       ON link.project_id = reference.project_id
                      AND link.task_id = reference.task_id
                      AND link.evidence_reference_id =
                            reference.evidence_reference_id
                      AND link.criterion_id =
                            resolution.verification_criterion_id
                      AND link.relation = 'runner_observation'
                    WHERE resolution.project_id = NEW.project_id
                      AND resolution.task_id = NEW.task_id
                      AND resolution.contract_revision = NEW.contract_revision
                      AND resolution.verification_expectation_digest =
                            NEW.verification_expectation_digest
                      AND resolution.authority_snapshot_id =
                            task.review_target_authority_snapshot_id
                      AND resolution.verification_criterion_id =
                            task.review_target_verification_criterion_id
                      AND resolution.target_kind = NEW.review_target_kind
                      AND resolution.target_value = NEW.review_target_value
                      AND COALESCE(resolution.target_base_revision, '') =
                            NEW.review_target_base_revision
                      AND resolution.target_generation =
                            NEW.review_target_generation
                      AND resolution.artifact_manifest_id =
                            task.review_target_artifact_manifest_id
                      AND resolution.gate_eligibility_version = 1
                      AND (
                        (
                          NEW.verification_basis_kind = 'caller_attestation'
                          AND NEW.verification_runner_observation_id IS NULL
                          AND observation.route = 'm21_fallback'
                          AND observation.launch_state = 'no_launch'
                          AND observation.outcome = 'blocked_prelaunch'
                          AND observation.reason IN (
                            'runtime_unavailable', 'process_setup_failed'
                          )
                          AND observation.complete_plan = 0
                          AND NEW.verification_receipt_id IS NOT NULL
                          AND EXISTS (
                            SELECT 1 FROM verification_receipts AS receipt
                             WHERE receipt.verification_receipt_id =
                                   NEW.verification_receipt_id
                               AND receipt.project_id = NEW.project_id
                               AND receipt.task_id = NEW.task_id
                               AND receipt.contract_revision =
                                     NEW.contract_revision
                               AND receipt.verification_expectation_digest =
                                     NEW.verification_expectation_digest
                               AND receipt.target_kind = NEW.review_target_kind
                               AND receipt.target_value = NEW.review_target_value
                               AND receipt.target_base_revision =
                                     NEW.review_target_base_revision
                               AND receipt.target_generation =
                                     NEW.review_target_generation
                               AND receipt.result = 'pass'
                               AND receipt.scope_coverage = 'full'
                          )
                        )
                        OR
                        (
                          NEW.verification_basis_kind = 'runner_observation'
                          AND NEW.verification_receipt_id IS NULL
                          AND NEW.verification_runner_observation_id =
                                observation.verification_runner_observation_id
                          AND observation.route = 'runner'
                          AND observation.launch_state = 'launched'
                          AND observation.outcome = 'pass'
                          AND observation.reason IS NULL
                          AND observation.complete_plan = 1
                          AND observation.total_step_count =
                                resolution.step_count
                          AND observation.completed_step_count =
                                resolution.step_count
                          AND observation.failed_step_ordinal IS NULL
                        )
                      )
                 )
               )
             )
        )
      )
    )
    BEGIN
      SELECT RAISE(ABORT, 'invalid_completion_verification_basis');
    END
    """


def _task_completion_cycle_evidence_basis_v21_trigger_sql() -> str:
    """Return the schema-v21 same-cycle Bundle/tag relation guard."""

    return """
    CREATE TRIGGER trg_task_completion_cycles_evidence_basis_insert
    BEFORE INSERT ON task_completion_cycles
    WHEN NOT (
      (
        NEW.origin = 'legacy_current_done'
        AND NEW.evidence_basis_version = 0
        AND NEW.completion_evidence_bundle_id IS NULL
      )
      OR
      (
        NEW.origin = 'native_done'
        AND NEW.evidence_basis_version = 1
        AND NEW.completion_evidence_bundle_id IS NOT NULL
        AND EXISTS (
          SELECT 1 FROM completion_evidence_bundles AS bundle
           WHERE bundle.project_id = NEW.project_id
             AND bundle.task_id = NEW.task_id
             AND bundle.completion_cycle_id = NEW.completion_cycle_id
             AND bundle.cycle_ordinal = NEW.saved_cycle_ordinal
             AND bundle.completion_evidence_bundle_id =
                   NEW.completion_evidence_bundle_id
             AND bundle.source_schema_version = 21
             AND bundle.bundle_version = 2
             AND bundle.verification_receipt_id IS
                   NEW.verification_receipt_id
             AND bundle.verification_basis_kind =
                   NEW.verification_basis_kind
             AND bundle.verification_runner_observation_id IS
                   NEW.verification_runner_observation_id
        )
      )
    )
    BEGIN
      SELECT RAISE(ABORT, 'invalid_completion_evidence_basis');
    END
    """


def _rebuild_completion_evidence_bundle_v20(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("DROP TRIGGER trg_completion_evidence_bundles_no_update")
    connection.execute("DROP TRIGGER trg_completion_evidence_bundles_no_delete")
    connection.execute("DROP INDEX idx_completion_evidence_bundles_task_cycle")
    connection.execute(
        "ALTER TABLE completion_evidence_bundles "
        "RENAME TO completion_evidence_bundles_v19"
    )
    connection.execute(_completion_evidence_bundle_v20_table_sql())
    prefix = (
        "completion_evidence_bundle_id",
        "project_id",
        "task_id",
        "completion_cycle_id",
        "cycle_ordinal",
        "source_schema_version",
        "bundle_version",
        "contract_revision",
        "authority_snapshot_id",
        "acceptance_criterion_id",
        "verification_criterion_id",
        "target_kind",
        "target_value",
        "target_base_revision",
        "target_generation",
        "target_capture_version",
        "artifact_manifest_id",
        "verification_receipt_id",
    )
    trailing = (
        "omission_mask",
        "sealed_at",
        "bundle_digest",
        "payload_size_bytes",
    )
    destination = (
        *prefix,
        "verification_basis_kind",
        "verification_runner_observation_id",
        *trailing,
    )
    connection.execute(
        "INSERT INTO completion_evidence_bundles("
        + ",".join(destination)
        + ") SELECT "
        + ",".join(prefix)
        + ",NULL,NULL,"
        + ",".join(trailing)
        + " FROM completion_evidence_bundles_v19"
    )
    connection.execute("DROP TABLE completion_evidence_bundles_v19")
    for statement in _bundle_v20_recreated_object_statements():
        connection.execute(statement)


def _schema20_statement_identity(statement: str) -> tuple[str, str, str]:
    normalized = _normalized_schema_sql(statement)
    match = re.match(
        r"CREATE\s+(TABLE|(?:UNIQUE\s+)?INDEX|TRIGGER)\s+([a-z0-9_]+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        raise AssertionError("private schema-v20 object inventory is incomplete")
    raw_kind, name = match.groups()
    kind = "index" if "INDEX" in raw_kind.upper() else raw_kind.lower()
    table_match = re.search(
        r"\bON\s+([a-z0-9_]+)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    table_name = name if kind == "table" else (
        table_match.group(1) if table_match is not None else ""
    )
    return kind, name, table_name


def _schema20_expected_objects() -> dict[str, tuple[str, str, str]]:
    inherited_bundle_objects = tuple(
        statement
        for statement in completion_evidence_bundle_schema_statements()
        if _normalized_schema_sql(statement).startswith("CREATE ")
    )
    statements = (
        *inherited_bundle_objects,
        _completion_evidence_bundle_v20_table_sql(),
        *_verification_runner_table_statements(),
        *_verification_runner_index_statements(),
        *_verification_runner_trigger_statements(),
        *_bundle_v20_recreated_object_statements(),
        _criterion_evidence_links_v20_matrix_trigger_sql(),
    )
    result: dict[str, tuple[str, str, str]] = {}
    for statement in statements:
        kind, name, table_name = _schema20_statement_identity(statement)
        result[name] = (kind, table_name, _normalized_schema_sql(statement))
    return result


def _schema21_expected_objects() -> dict[str, tuple[str, str, str]]:
    """Return every owned object whose exact SQL is fixed by migration 21."""

    result = _schema20_expected_objects()
    statements = (
        _completion_evidence_bundle_v20_table_sql(
            schema_version=PRIVATE_SCHEMA21_VERSION,
        ),
        *_verification_runner_table_statements(
            schema_version=PRIVATE_SCHEMA21_VERSION,
        ),
        *_verification_runner_index_statements(),
        *_verification_runner_trigger_statements(
            schema_version=PRIVATE_SCHEMA21_VERSION,
        ),
        *_bundle_v20_recreated_object_statements(),
        _task_completion_cycle_verification_basis_v21_trigger_sql(),
        _task_completion_cycle_evidence_basis_v21_trigger_sql(),
        _criterion_evidence_links_v20_matrix_trigger_sql(),
    )
    for statement in statements:
        kind, name, table_name = _schema20_statement_identity(statement)
        result[name] = (kind, table_name, _normalized_schema_sql(statement))
    return result


_SCHEMA22_REBUILT_TABLES = (
    "evidence_references",
    "criterion_evidence_links",
    "completion_evidence_bundles",
)
_SCHEMA22_TEMP_TABLES = tuple(
    f"{table_name}_v21" for table_name in _SCHEMA22_REBUILT_TABLES
)


def _schema22_replacement_statements() -> tuple[str, ...]:
    """Return the private v22 Evidence replacements, not a migration path."""

    statements: list[str] = []
    for legacy_statements, table_name in (
        (evidence_ledger_capture_schema_statements(), "evidence_references"),
        (completion_evidence_bundle_schema_statements(), "criterion_evidence_links"),
    ):
        for statement in legacy_statements:
            if not _normalized_schema_sql(statement).startswith("CREATE "):
                continue
            kind, name, owner = _schema20_statement_identity(statement)
            if owner != table_name or name == "trg_criterion_evidence_links_matrix_insert":
                continue
            if kind == "table":
                # Derive only the six closed SQL allow-lists; legacy DDL stays intact.
                statement = (
                    statement.replace(", 'derived_analysis'", "")
                    .replace(", 'llm_derived'", "")
                    .replace(", 'batch_analyzer'", "")
                )
            statements.append(statement)
    return (
        *statements,
        _completion_evidence_bundle_v20_table_sql(
            schema_version=PRIVATE_SCHEMA21_VERSION,
        ).replace("source_schema_version = 21", "source_schema_version IN (21, 22)"),
        *_bundle_v20_recreated_object_statements(),
        _criterion_evidence_links_v20_matrix_trigger_sql(),
        _task_completion_cycle_evidence_basis_v21_trigger_sql().replace(
            "bundle.source_schema_version = 21", "bundle.source_schema_version = 22",
        ),
    )


def _schema22_expected_objects() -> dict[str, tuple[str, str, str]]:
    result = _schema21_expected_objects()
    for statement in _schema22_replacement_statements():
        kind, name, table_name = _schema20_statement_identity(statement)
        result[name] = (kind, table_name, _normalized_schema_sql(statement))
    return result


def _validate_schema22_owned_contract(connection: sqlite3.Connection) -> None:
    """Recognize exact private v22 owned DDL without admitting stored rows."""

    if _owned_schema_sql_fingerprint(
        connection,
        schema_version=PRIVATE_SCHEMA22_VERSION,
    ) != _SCHEMA22_OWNED_SCHEMA_FINGERPRINT:
        raise _unreadable_project_state()
    if _unowned_rebuilt_table_attachments(
        connection,
        table_names=tuple(dict.fromkeys(
            (*_SCHEMA21_REBUILT_TABLES, *_SCHEMA22_REBUILT_TABLES)
        )),
        expected_objects=_SCHEMA22_EXPECTED_OBJECTS,
    ) or _schema21_temporary_table_present(connection) or (
        _schema22_migration_temporary_name_collision(connection)
    ):
        raise _unreadable_project_state()


def _schema22_migration_temporary_name_collision(
    connection: sqlite3.Connection,
) -> bool:
    return any(
        connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'view') "
            "AND name = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()
        is not None
        for name in _SCHEMA22_TEMP_TABLES
    )


def _unowned_rebuilt_table_attachments(
    connection: sqlite3.Connection,
    *,
    table_names: tuple[str, ...],
    expected_objects: Iterable[tuple[str, tuple[str, str, str]]],
) -> tuple[str, ...]:
    expected_names = {
        name
        for name, (_kind, table_name, _sql) in expected_objects
        if table_name in table_names
    }
    placeholders = ", ".join("?" for _ in table_names)
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('index', 'trigger') AND sql IS NOT NULL "
        f"AND tbl_name IN ({placeholders}) ORDER BY name",
        table_names,
    ).fetchall()
    return tuple(
        str(row["name"])
        for row in rows
        if str(row["name"]) not in expected_names
    )


def _validate_schema21_owned_contract(connection: sqlite3.Connection) -> None:
    if required_schema_objects_missing(
        connection,
        schema_version=PRIVATE_SCHEMA21_VERSION,
    ):
        raise _unreadable_project_state()
    expected_objects = _SCHEMA21_EXPECTED_OBJECTS
    if _owned_schema_sql_fingerprint(
        connection,
        schema_version=PRIVATE_SCHEMA21_VERSION,
    ) != _SCHEMA21_OWNED_SCHEMA_FINGERPRINT:
        raise _unreadable_project_state()

    task_columns = _validate_schema20_column(
        connection,
        table_name="tasks",
        column_name="review_target_runner_basis_version",
        declared_type="INTEGER",
        notnull=1,
        default="0",
    )
    cycle_columns = _validate_schema20_column(
        connection,
        table_name="task_completion_cycles",
        column_name="verification_basis_kind",
        declared_type="TEXT",
        notnull=0,
        default=None,
    )
    _validate_schema20_column(
        connection,
        table_name="task_completion_cycles",
        column_name="verification_runner_observation_id",
        declared_type="TEXT",
        notnull=0,
        default=None,
    )
    expected_bundle_columns = tuple(
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS["completion_evidence_bundles"]
    )
    actual_bundle_columns = tuple(
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_xinfo(completion_evidence_bundles)"
        ).fetchall()
    )
    if (
        task_columns[-1:] != ("review_target_runner_basis_version",)
        or cycle_columns[-2:] != (
            "verification_basis_kind",
            "verification_runner_observation_id",
        )
        or set(actual_bundle_columns) != set(expected_bundle_columns)
        or len(actual_bundle_columns) != len(expected_bundle_columns)
    ):
        raise _unreadable_project_state()

    rebuilt_tables = (
        "completion_evidence_bundles",
        "verification_runner_resolutions",
        "verification_runner_attempts",
        "verification_runner_observations",
    )
    if _unowned_rebuilt_table_attachments(
        connection,
        table_names=rebuilt_tables,
        expected_objects=expected_objects,
    ):
        raise _unreadable_project_state()
    if _schema21_temporary_table_present(connection):
        raise _unreadable_project_state()
    if (
        len(_SCHEMA_TABLE_INTRODUCED_VERSION) != 35
        or len(_SCHEMA_INDEX_INTRODUCED_VERSION) != 42
        or len(_SCHEMA_TRIGGER_INTRODUCED_VERSION) != 59
    ):
        raise AssertionError("schema-v21 owned object inventory is incomplete")


def _validate_schema20_column(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    declared_type: str,
    notnull: int,
    default: str | None,
) -> tuple[str, ...]:
    rows = connection.execute(
        f"PRAGMA table_xinfo({_quoted_identifier(table_name)})"
    ).fetchall()
    row = next(
        (candidate for candidate in rows if str(candidate["name"]) == column_name),
        None,
    )
    if (
        row is None
        or str(row["type"]).upper() != declared_type
        or int(row["notnull"]) != notnull
        or row["dflt_value"] != default
        or int(row["pk"]) != 0
        or int(row["hidden"]) != 0
    ):
        raise _unreadable_project_state()
    return tuple(str(candidate["name"]) for candidate in rows)


def _validate_schema20_owned_contract(connection: sqlite3.Connection) -> None:
    if required_schema_objects_missing(
        connection,
        schema_version=PRIVATE_SCHEMA20_VERSION,
    ):
        raise _unreadable_project_state()
    if _owned_schema_sql_fingerprint(
        connection,
        schema_version=PRIVATE_SCHEMA20_VERSION,
    ) != _SCHEMA20_OWNED_SCHEMA_FINGERPRINT:
        raise _unreadable_project_state()

    task_columns = _validate_schema20_column(
        connection,
        table_name="tasks",
        column_name="review_target_runner_basis_version",
        declared_type="INTEGER",
        notnull=1,
        default="0",
    )
    cycle_columns = _validate_schema20_column(
        connection,
        table_name="task_completion_cycles",
        column_name="verification_basis_kind",
        declared_type="TEXT",
        notnull=0,
        default=None,
    )
    _validate_schema20_column(
        connection,
        table_name="task_completion_cycles",
        column_name="verification_runner_observation_id",
        declared_type="TEXT",
        notnull=0,
        default=None,
    )
    bundle_columns = tuple(
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_xinfo(completion_evidence_bundles)"
        ).fetchall()
    )
    expected_bundle_columns = (
        "completion_evidence_bundle_id",
        "project_id",
        "task_id",
        "completion_cycle_id",
        "cycle_ordinal",
        "source_schema_version",
        "bundle_version",
        "contract_revision",
        "authority_snapshot_id",
        "acceptance_criterion_id",
        "verification_criterion_id",
        "target_kind",
        "target_value",
        "target_base_revision",
        "target_generation",
        "target_capture_version",
        "artifact_manifest_id",
        "verification_receipt_id",
        "verification_basis_kind",
        "verification_runner_observation_id",
        "omission_mask",
        "sealed_at",
        "bundle_digest",
        "payload_size_bytes",
    )
    if (
        task_columns[-1:] != ("review_target_runner_basis_version",)
        or cycle_columns[-2:] != (
            "verification_basis_kind",
            "verification_runner_observation_id",
        )
        or bundle_columns != expected_bundle_columns
    ):
        raise _unreadable_project_state()
    if _unowned_rebuilt_table_attachments(
        connection,
        table_names=("completion_evidence_bundles",),
        expected_objects=_SCHEMA20_EXPECTED_OBJECTS,
    ) or _schema21_migration_temporary_name_collision(connection):
        raise _unreadable_project_state()


def _schema20_integrity_checks(connection: sqlite3.Connection) -> None:
    if [
        str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()
    ] != ["ok"]:
        raise _unreadable_project_state()
    integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
    if not integrity_rows or any(str(row[0]) != "ok" for row in integrity_rows):
        raise _unreadable_project_state()
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise _unreadable_project_state()


_RUNNER_RESULT_PAIRINGS = frozenset(
    {
        ("blocked_prelaunch", "runtime_unavailable", "no_launch"),
        ("blocked_prelaunch", "process_setup_failed", "no_launch"),
        ("blocked_prelaunch", "process_boundary_unproved", "no_launch"),
        ("blocked_prelaunch", "process_create_failed", "no_launch"),
        ("blocked_prelaunch", "cancelled", "no_launch"),
        ("blocked_prelaunch", "controller_interrupted", "no_launch"),
        ("pass", None, "launched"),
        ("fail", "step_nonzero", "launched"),
        ("timeout", "timeout", "launched"),
        ("cancelled", "cancelled", "launched"),
        ("resource_exceeded", "cpu_limit", "launched"),
        ("resource_exceeded", "memory_limit", "launched"),
        ("output_rejected", "output_limit", "launched"),
        ("process_error", "runtime_unavailable", "launched"),
        ("process_error", "process_setup_failed", "launched"),
        ("process_error", "process_boundary_unproved", "launched"),
        ("process_error", "process_create_failed", "launched"),
        ("process_error", "process_resume_failed", "launched"),
        ("process_error", "process_wait_failed", "launched"),
        ("process_error", "pipe_drain_failed", "launched"),
        ("process_error", "process_tree_unproved", "launched"),
        ("controller_interrupted", "controller_interrupted", "no_launch"),
        ("controller_interrupted", "controller_interrupted", "launched"),
    }
)


def _runner_row_value(row: Any, value_type: type[Any]) -> Any:
    fields = tuple(value_type.__dataclass_fields__)
    try:
        if set(row.keys()) != set(fields):
            raise evidence_ledger_inconsistent()
        return value_type(**{field: row[field] for field in fields})
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise evidence_ledger_inconsistent() from exc


def _runner_resolution_value(row: Any) -> VerificationRunnerResolution:
    return _runner_row_value(row, VerificationRunnerResolution)


def _runner_attempt_value(row: Any) -> VerificationRunnerAttempt:
    return _runner_row_value(row, VerificationRunnerAttempt)


def _runner_observation_value(row: Any) -> VerificationRunnerObservation:
    return _runner_row_value(row, VerificationRunnerObservation)


def _runner_sandbox_event_value(row: Any) -> VerificationRunnerSandboxEvent:
    return _runner_row_value(row, VerificationRunnerSandboxEvent)


def _validate_runner_timestamp(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise evidence_ledger_inconsistent()
    try:
        return validate_utc_timestamp(value, field=field_name)
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc


def _validated_verification_runner_graph(
    connection: sqlite3.Connection,
    *,
    selected_generation: tuple[str, str, int] | None = None,
    selected_task: sqlite3.Row | None = None,
    selected_history_cycle: CompletionCycle | None = None,
) -> tuple[
    tuple[_ExpectedEvidenceReference, ...],
    dict[tuple[str, str, int], dict[str, Any]],
]:
    """Validate all Runner rows or one exact selected generation."""

    from task_governance_tool.evidence_ledger import (
        EvidenceLedgerError,
        EvidenceSource,
        TargetCaptureBinding,
    )
    from task_governance_tool.verification_runner import (
        RUNNER_CONTRACT_VERSION,
        RUNNER_IMPLEMENTATION_VERSION,
        RUNNER_POLICY_DIGEST,
        RUNNER_TRIGGER,
        VerificationRunnerModelError,
        resolution_idempotency_digest,
        runner_observation_source_projection,
        verification_runner_attempt_digest,
        verification_runner_observation_digest,
        verification_runner_sandbox_event_digest,
    )

    physical_schema_version = current_schema_version(connection)
    if physical_schema_version not in {
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
    }:
        return (), {}
    allowed_eligibility_versions = (
        {0, 1}
        if physical_schema_version in {
            PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
        }
        else {0}
    )
    if selected_generation is not None:
        project_id, task_id, target_generation = selected_generation
        if (
            physical_schema_version not in {
                PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
            }
            or type(project_id) is not str
            or not project_id
            or type(task_id) is not str
            or not task_id
            or type(target_generation) is not int
            or target_generation < 1
            or (selected_task is None) == (selected_history_cycle is None)
        ):
            raise evidence_ledger_inconsistent()
        if selected_task is not None:
            if (
                selected_task["project_id"] != project_id
                or selected_task["task_id"] != task_id
                or selected_task["review_target_generation"] != target_generation
            ):
                raise evidence_ledger_inconsistent()
        else:
            assert selected_history_cycle is not None
            if (
                selected_history_cycle.project_id != project_id
                or selected_history_cycle.task_id != task_id
                or selected_history_cycle.review_target_generation
                != target_generation
                or selected_history_cycle.origin != "native_done"
                or selected_history_cycle.evidence_basis_version != 1
                or selected_history_cycle.verification_basis_kind
                not in {"caller_attestation", "runner_observation"}
            ):
                raise evidence_ledger_inconsistent()
        generation_predicate = (
            " WHERE project_id = ? AND task_id = ? AND target_generation = ? "
        )
        generation_parameters: tuple[object, ...] = selected_generation
        generation_limit = " LIMIT 2"
    else:
        if selected_task is not None or selected_history_cycle is not None:
            raise evidence_ledger_inconsistent()
        generation_predicate = " "
        generation_parameters = ()
        generation_limit = ""
    try:
        resolution_rows = connection.execute(
            "SELECT * FROM verification_runner_resolutions "
            + generation_predicate
            + "ORDER BY project_id, task_id, target_generation, "
            "verification_runner_resolution_id"
            + generation_limit,
            generation_parameters,
        ).fetchall()
        attempt_rows = connection.execute(
            "SELECT * FROM verification_runner_attempts "
            + generation_predicate
            + "ORDER BY project_id, task_id, target_generation, "
            "verification_runner_attempt_id"
            + generation_limit,
            generation_parameters,
        ).fetchall()
        observation_rows = connection.execute(
            "SELECT * FROM verification_runner_observations "
            + generation_predicate
            + "ORDER BY project_id, task_id, target_generation, "
            "verification_runner_observation_id"
            + generation_limit,
            generation_parameters,
        ).fetchall()
        event_rows = connection.execute(
            "SELECT * FROM verification_runner_sandbox_events "
            + generation_predicate
            + "ORDER BY project_id, task_id, target_generation, "
            "verification_runner_sandbox_event_id"
            + generation_limit,
            generation_parameters,
        ).fetchall()
        if selected_generation is None:
            snapshots = {
                str(row["authority_snapshot_id"]): row
                for row in connection.execute(
                    "SELECT * FROM authority_snapshots "
                    "ORDER BY authority_snapshot_id"
                ).fetchall()
            }
            criteria = {
                str(row["criterion_id"]): row
                for row in connection.execute(
                    "SELECT * FROM contract_criteria ORDER BY criterion_id"
                ).fetchall()
            }
            manifests = {
                str(row["artifact_manifest_id"]): row
                for row in connection.execute(
                    "SELECT * FROM artifact_manifests "
                    "ORDER BY artifact_manifest_id"
                ).fetchall()
            }
            tasks = {
                (str(row["project_id"]), str(row["task_id"])): row
                for row in connection.execute(
                    "SELECT project_id, task_id, review_target_generation, "
                    "review_target_runner_basis_version FROM tasks "
                    "ORDER BY project_id, task_id"
                ).fetchall()
            }
        else:
            snapshot_ids: set[str] = set()
            manifest_ids: set[str] = set()
            for row in resolution_rows:
                snapshot_id = row["authority_snapshot_id"]
                manifest_id = row["artifact_manifest_id"]
                if (
                    type(snapshot_id) is not str
                    or not snapshot_id
                    or type(manifest_id) is not str
                    or not manifest_id
                ):
                    raise evidence_ledger_inconsistent()
                snapshot_ids.add(snapshot_id)
                manifest_ids.add(manifest_id)
            authority = _validated_authority_context(
                connection,
                snapshot_ids=snapshot_ids,
            )
            manifest_records, manifests_by_target = (
                _validate_artifact_manifest_storage(
                    connection,
                    snapshots=authority.snapshots,
                    links=authority.links,
                    manifest_ids=manifest_ids,
                )
            )
            _validate_evidence_reference_storage(
                connection,
                manifests=manifest_records,
                manifests_by_target=manifests_by_target,
                snapshots=authority.snapshots,
                verification_receipt_ids=set(),
                review_receipt_ids=set(),
                review_finding_ids=set(),
                completion_cycle_ids=set(),
                selected_project_id=selected_generation[0],
            )
            snapshots = authority.snapshots
            criteria = authority.criteria
            manifests = {
                manifest_id: record.row
                for manifest_id, record in manifest_records.items()
            }
            if selected_task is not None:
                selected_task_basis = selected_task
                tasks = {
                    (selected_generation[0], selected_generation[1]): (
                        selected_task_basis
                    )
                }
            else:
                assert selected_history_cycle is not None
                tasks = {}

        resolutions: dict[
            tuple[str, str, int], tuple[VerificationRunnerResolution, sqlite3.Row]
        ] = {}
        for row in resolution_rows:
            value = _runner_resolution_value(row)
            key = (value.project_id, value.task_id, value.target_generation)
            snapshot = snapshots.get(value.authority_snapshot_id)
            criterion = criteria.get(value.verification_criterion_id)
            manifest = manifests.get(value.artifact_manifest_id)
            target_base = value.target_base_revision or ""
            if (
                key in resolutions
                or VERIFICATION_RUNNER_RESOLUTION_ID_PATTERN.fullmatch(
                    value.verification_runner_resolution_id
                )
                is None
                or type(value.contract_revision) is not int
                or value.contract_revision < 1
                or type(value.target_generation) is not int
                or value.target_generation < 1
                or value.target_capture_version != 1
                or value.target_kind not in {"git_snapshot", "git_commit"}
                or type(value.target_value) is not str
                or not value.target_value
                or (
                    value.target_base_revision is not None
                    and (
                        type(value.target_base_revision) is not str
                        or not value.target_base_revision
                    )
                )
                or value.target_material_digest is None
                or SHA256_DIGEST_PATTERN.fullmatch(value.target_material_digest)
                is None
                or value.plan_state != "runner"
                or value.plan_blob_object_id is not None
                or type(value.plan_raw_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(value.plan_raw_digest) is None
                or type(value.plan_id) is not str
                or VERIFICATION_RUNNER_IDENTIFIER_PATTERN.fullmatch(value.plan_id)
                is None
                or value.plan_version != 1
                or type(value.plan_semantic_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(value.plan_semantic_digest)
                is None
                or type(value.selected_entry_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(value.selected_entry_digest)
                is None
                or value.coverage != "full"
                or type(value.step_count) is not int
                or not 1 <= value.step_count <= 16
                or value.runner_contract_version != RUNNER_CONTRACT_VERSION
                or value.runner_implementation_version
                != RUNNER_IMPLEMENTATION_VERSION
                or type(value.runner_implementation_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(
                    value.runner_implementation_digest
                )
                is None
                or value.runner_policy_digest != RUNNER_POLICY_DIGEST
                or value.runtime_digest is not None
                or value.gate_eligibility_version
                not in allowed_eligibility_versions
                or value.trigger != RUNNER_TRIGGER
                or value.route != "runner"
                or value.reason is not None
                or type(value.idempotency_digest) is not str
                or value.idempotency_digest
                != resolution_idempotency_digest(
                    _verification_runner_resolution_digest_projection(row)
                )
                or snapshot is None
                or snapshot["project_id"] != value.project_id
                or snapshot["task_id"] != value.task_id
                or snapshot["contract_revision"] != value.contract_revision
                or snapshot["verification_digest"]
                != value.verification_expectation_digest
                or criterion is None
                or criterion["project_id"] != value.project_id
                or criterion["task_id"] != value.task_id
                or criterion["criterion_kind"] != "verification"
                or criterion["digest"] != value.verification_criterion_digest
                or manifest is None
                or manifest["project_id"] != value.project_id
                or manifest["task_id"] != value.task_id
                or manifest["state"] != "complete_git"
                or manifest["authority_snapshot_id"] != value.authority_snapshot_id
                or manifest["verification_criterion_id"]
                != value.verification_criterion_id
                or manifest["target_kind"] != value.target_kind
                or manifest["target_value"] != value.target_value
                or manifest["target_base_revision"] != target_base
                or manifest["target_generation"] != value.target_generation
            ):
                raise evidence_ledger_inconsistent()
            _validate_runner_timestamp(
                value.created_at,
                field_name="verification Runner resolution creation time",
            )
            resolutions[key] = (value, row)

        attempts: dict[
            tuple[str, str, int], tuple[VerificationRunnerAttempt, sqlite3.Row]
        ] = {}
        for row in attempt_rows:
            value = _runner_attempt_value(row)
            key = (value.project_id, value.task_id, value.target_generation)
            parent = resolutions.get(key)
            resolution = parent[0] if parent is not None else None
            if (
                key in attempts
                or VERIFICATION_RUNNER_ATTEMPT_ID_PATTERN.fullmatch(
                    value.verification_runner_attempt_id
                )
                is None
                or resolution is None
                or value.gate_eligibility_version
                != resolution.gate_eligibility_version
                or value.verification_runner_resolution_id
                != resolution.verification_runner_resolution_id
                or value.target_material_digest
                != resolution.target_material_digest
                or value.runner_implementation_digest
                != resolution.runner_implementation_digest
                or value.attempt_digest
                != verification_runner_attempt_digest(
                    _verification_runner_attempt_digest_projection(row)
                )
            ):
                raise evidence_ledger_inconsistent()
            recorded_at = _validate_runner_timestamp(
                value.intent_recorded_at,
                field_name="verification Runner attempt intent time",
            )
            if recorded_at < resolution.created_at:
                raise evidence_ledger_inconsistent()
            attempts[key] = (value, row)
        if set(attempts) != set(resolutions):
            raise evidence_ledger_inconsistent()

        observations: dict[
            tuple[str, str, int], tuple[VerificationRunnerObservation, sqlite3.Row]
        ] = {}
        for row in observation_rows:
            value = _runner_observation_value(row)
            key = (value.project_id, value.task_id, value.target_generation)
            parent_resolution = resolutions.get(key)
            parent_attempt = attempts.get(key)
            resolution = (
                parent_resolution[0] if parent_resolution is not None else None
            )
            attempt = parent_attempt[0] if parent_attempt is not None else None
            pairing = (value.outcome, value.reason, value.launch_state)
            expected_route = (
                "runner" if value.launch_state == "launched" else "m21_fallback"
            )
            accounting = (
                value.cpu_time_ms,
                value.peak_job_memory_bytes,
                value.total_process_count,
            )
            expected_complete = int(
                value.outcome == "pass"
                and value.reason is None
                and value.launch_state == "launched"
                and resolution is not None
                and value.completed_step_count
                == value.total_step_count
                == resolution.step_count
            )
            if (
                key in observations
                or VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN.fullmatch(
                    value.verification_runner_observation_id
                )
                is None
                or resolution is None
                or attempt is None
                or value.gate_eligibility_version
                != resolution.gate_eligibility_version
                or value.verification_runner_resolution_id
                != resolution.verification_runner_resolution_id
                or value.verification_runner_attempt_id
                != attempt.verification_runner_attempt_id
                or value.runner_implementation_digest
                != resolution.runner_implementation_digest
                or pairing not in _RUNNER_RESULT_PAIRINGS
                or value.route != expected_route
                or value.complete_plan != expected_complete
                or value.total_step_count != resolution.step_count
                or type(value.completed_step_count) is not int
                or not 0 <= value.completed_step_count <= value.total_step_count
                or (
                    value.failed_step_ordinal is not None
                    and (
                        type(value.failed_step_ordinal) is not int
                        or not 1
                        <= value.failed_step_ordinal
                        <= value.total_step_count
                    )
                )
                or type(value.duration_ms) is not int
                or value.duration_ms < 0
                or any(
                    item is not None and (type(item) is not int or item < 0)
                    for item in accounting
                )
                or sum(item is None for item in accounting) not in {0, 3}
                or (
                    value.launch_state == "no_launch"
                    and (
                        value.completed_step_count != 0
                        or value.failed_step_ordinal is not None
                        or any(item is not None for item in accounting)
                    )
                )
                or (
                    value.outcome == "pass"
                    and (
                        value.failed_step_ordinal is not None
                        or any(item is None for item in accounting)
                    )
                )
                or value.sanitized_result_digest
                != verification_runner_observation_digest(
                    {
                        "attempt_id": value.verification_runner_attempt_id,
                        "completed_step_count": value.completed_step_count,
                        "complete_plan": value.complete_plan,
                        "cpu_time_ms": value.cpu_time_ms,
                        "duration_ms": value.duration_ms,
                        "failed_step_ordinal": value.failed_step_ordinal,
                        "finished_at": value.finished_at,
                        "gate_eligibility_version": value.gate_eligibility_version,
                        "launch_state": value.launch_state,
                        "outcome": value.outcome,
                        "peak_job_memory_bytes": value.peak_job_memory_bytes,
                        "project_id": value.project_id,
                        "reason": value.reason,
                        "resolution_id": value.verification_runner_resolution_id,
                        "runner_implementation_digest": (
                            value.runner_implementation_digest
                        ),
                        "started_at": value.started_at,
                        "target_generation": value.target_generation,
                        "task_id": value.task_id,
                        "route": value.route,
                        "total_process_count": value.total_process_count,
                        "total_step_count": value.total_step_count,
                    }
                )
            ):
                raise evidence_ledger_inconsistent()
            started = _validate_runner_timestamp(
                value.started_at,
                field_name="verification Runner observation start time",
            )
            finished = _validate_runner_timestamp(
                value.finished_at,
                field_name="verification Runner observation finish time",
            )
            created = _validate_runner_timestamp(
                value.created_at,
                field_name="verification Runner observation creation time",
            )
            if (
                started < attempt.intent_recorded_at
                or started > finished
                or created != finished
            ):
                raise evidence_ledger_inconsistent()
            observations[key] = (value, row)

        events: dict[
            tuple[str, str, int], tuple[VerificationRunnerSandboxEvent, sqlite3.Row]
        ] = {}
        for row in event_rows:
            value = _runner_sandbox_event_value(row)
            key = (value.project_id, value.task_id, value.target_generation)
            attempt_record = attempts.get(key)
            observation_record = observations.get(key)
            attempt = attempt_record[0] if attempt_record is not None else None
            observation = (
                observation_record[0] if observation_record is not None else None
            )
            expected_terminal = (
                observation.verification_runner_observation_id
                if observation is not None
                else None
            )
            if (
                key in events
                or VERIFICATION_RUNNER_SANDBOX_EVENT_ID_PATTERN.fullmatch(
                    value.verification_runner_sandbox_event_id
                )
                is None
                or attempt is None
                or value.verification_runner_attempt_id
                != attempt.verification_runner_attempt_id
                or value.event_kind != "attempt_cleanup_succeeded"
                or value.terminal_observation_id != expected_terminal
                or value.event_digest
                != verification_runner_sandbox_event_digest(
                    {
                        "attempt_id": value.verification_runner_attempt_id,
                        "event_kind": value.event_kind,
                        "project_id": value.project_id,
                        "target_generation": value.target_generation,
                        "task_id": value.task_id,
                        "terminal_observation_id": value.terminal_observation_id,
                    }
                )
            ):
                raise evidence_ledger_inconsistent()
            event_time = _validate_runner_timestamp(
                value.created_at,
                field_name="verification Runner cleanup event time",
            )
            event_floor = (
                observation.finished_at
                if observation is not None
                else attempt.intent_recorded_at
            )
            if event_time < event_floor:
                raise evidence_ledger_inconsistent()
            events[key] = (value, row)
        if not set(observations).issubset(events):
            raise evidence_ledger_inconsistent()

        pending_by_project: dict[str, int] = {}
        for key, (attempt, _) in attempts.items():
            if key not in events:
                pending_by_project[attempt.project_id] = (
                    pending_by_project.get(attempt.project_id, 0) + 1
                )
        if any(count > 1 for count in pending_by_project.values()):
            raise evidence_ledger_inconsistent()

        for (project_id, task_id), task in tasks.items():
            generation = task["review_target_generation"]
            marker = task["review_target_runner_basis_version"]
            if (
                type(generation) is not int
                or generation < 0
                or type(marker) is not int
                or marker not in {0, 2}
            ):
                raise evidence_ledger_inconsistent()
            current = resolutions.get((project_id, task_id, generation))
            if marker == 2:
                if (
                    physical_schema_version not in {
                        PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
                    }
                    or current is None
                    or current[0].gate_eligibility_version != 1
                ):
                    raise evidence_ledger_inconsistent()
            elif current is not None and current[0].gate_eligibility_version != 0:
                raise evidence_ledger_inconsistent()

            if marker == 2 and current is not None:
                resolution = current[0]
                exact_receipt = connection.execute(
                    "SELECT 1 FROM verification_receipts "
                    "WHERE project_id = ? AND task_id = ? "
                    "AND contract_revision = ? "
                    "AND verification_expectation_digest = ? "
                    "AND verification_subject_basis_version = 1 "
                    "AND subject_authority_snapshot_id = ? "
                    "AND subject_verification_criterion_id = ? "
                    "AND target_kind = ? AND target_value = ? "
                    "AND target_base_revision = ? AND target_generation = ? "
                    "LIMIT 1",
                    (
                        project_id,
                        task_id,
                        resolution.contract_revision,
                        resolution.verification_expectation_digest,
                        resolution.authority_snapshot_id,
                        resolution.verification_criterion_id,
                        resolution.target_kind,
                        resolution.target_value,
                        resolution.target_base_revision or "",
                        resolution.target_generation,
                    ),
                ).fetchone()
                if exact_receipt is not None:
                    attempt = attempts.get((project_id, task_id, generation))
                    observation = observations.get((project_id, task_id, generation))
                    cleanup = events.get((project_id, task_id, generation))
                    if (
                        attempt is None
                        or observation is None
                        or cleanup is None
                        or cleanup[0].terminal_observation_id
                        != observation[0].verification_runner_observation_id
                        or observation[0].route != "m21_fallback"
                        or observation[0].launch_state != "no_launch"
                        or observation[0].outcome != "blocked_prelaunch"
                        or observation[0].reason
                        not in {"runtime_unavailable", "process_setup_failed"}
                        or observation[0].complete_plan != 0
                    ):
                        raise evidence_ledger_inconsistent()

        if selected_generation is None:
            runner_references = connection.execute(
                "SELECT * FROM evidence_references "
                "WHERE source_kind = 'runner_observation' "
                "ORDER BY source_id, evidence_reference_id"
            ).fetchall()
        else:
            runner_references = connection.execute(
                "SELECT * FROM evidence_references "
                "WHERE project_id = ? AND task_id = ? "
                "AND target_generation = ? "
                "AND source_kind = 'runner_observation' "
                "ORDER BY source_id, evidence_reference_id LIMIT 2",
                selected_generation,
            ).fetchall()
        references_by_source: dict[str, sqlite3.Row] = {}
        runner_reference_keys: list[list[str]] = []
        for row in runner_references:
            source_id = row["source_id"]
            reference_id = row["evidence_reference_id"]
            project_id = row["project_id"]
            task_id = row["task_id"]
            if (
                type(source_id) is not str
                or source_id in references_by_source
                or type(reference_id) is not str
                or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(reference_id) is None
                or type(project_id) is not str
                or not project_id
                or type(task_id) is not str
                or not task_id
            ):
                raise evidence_ledger_inconsistent()
            references_by_source[source_id] = row
            runner_reference_keys.append([project_id, task_id, reference_id])

        runner_links: tuple[sqlite3.Row, ...] | list[sqlite3.Row]
        if runner_reference_keys:
            selected_references_json = json.dumps(
                runner_reference_keys,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            runner_links = connection.execute(
                """
                WITH selected_references(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT link.*
                  FROM selected_references AS selected
                 CROSS JOIN criterion_evidence_links AS link
                       INDEXED BY idx_criterion_evidence_links_reference
                 WHERE link.project_id = json_extract(selected.value, '$[0]')
                   AND link.task_id = json_extract(selected.value, '$[1]')
                   AND link.evidence_reference_id =
                         json_extract(selected.value, '$[2]')
                 ORDER BY link.evidence_reference_id,
                          link.criterion_evidence_link_id
                 LIMIT ?
                """,
                (selected_references_json, len(runner_reference_keys) + 1),
            ).fetchall()
        else:
            runner_links = ()
        links_by_reference: dict[str, list[sqlite3.Row]] = {}
        for row in runner_links:
            reference_id = row["evidence_reference_id"]
            if type(reference_id) is not str:
                raise evidence_ledger_inconsistent()
            links_by_reference.setdefault(reference_id, []).append(row)

        expected_references: list[_ExpectedEvidenceReference] = []
        observed_reference_ids: set[str] = set()
        observed_link_ids: set[str] = set()
        for key, (observation, observation_row) in observations.items():
            resolution, resolution_row = resolutions[key]
            manifest = manifests[resolution.artifact_manifest_id]
            reference = references_by_source.get(
                observation.verification_runner_observation_id
            )
            if reference is None:
                raise evidence_ledger_inconsistent()
            reference_id = reference["evidence_reference_id"]
            links = links_by_reference.get(str(reference_id), [])
            link = links[0] if len(links) == 1 else None
            target_base = resolution.target_base_revision or ""
            source = EvidenceSource(
                source_kind="runner_observation",
                source_state="recorded",
                source_id=observation.verification_runner_observation_id,
                source_projection=runner_observation_source_projection(
                    observation=dict(observation_row),
                    resolution=dict(resolution_row),
                ),
                _validated_runner_eligibility_version=(
                    observation.gate_eligibility_version
                    if physical_schema_version in {
                        PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
                    }
                    else 0
                ),
            )
            binding = TargetCaptureBinding(
                target_kind=resolution.target_kind,
                target_value=resolution.target_value,
                target_base_revision=target_base,
                target_generation=resolution.target_generation,
                authority_snapshot_id=resolution.authority_snapshot_id,
                acceptance_criterion_id=manifest["acceptance_criterion_id"],
                verification_criterion_id=resolution.verification_criterion_id,
            )
            expected_reference = _ExpectedEvidenceReference(
                source=source,
                project_id=resolution.project_id,
                task_id=resolution.task_id,
                contract_revision=resolution.contract_revision,
                binding=binding,
            )
            reference_seen: set[tuple[str, str]] = set()
            _validate_stored_evidence_reference_row(
                reference,
                expected={
                    (source.source_kind, source.source_id): expected_reference
                },
                seen=reference_seen,
            )
            link_id = (
                link["criterion_evidence_link_id"] if link is not None else None
            )
            if (
                type(reference_id) is not str
                or reference_id in observed_reference_ids
                or reference["created_at"] != observation.created_at
                or link is None
                or type(link_id) is not str
                or CRITERION_EVIDENCE_LINK_ID_PATTERN.fullmatch(link_id) is None
                or link_id in observed_link_ids
                or link["project_id"] != resolution.project_id
                or link["task_id"] != resolution.task_id
                or link["criterion_id"] != resolution.verification_criterion_id
                or link["evidence_reference_id"] != reference_id
                or link["relation"] != "runner_observation"
                or link["assurance_class"] != "machine_observed"
                or link["producer_class"] != "verification_runner"
                or link["producer_version"] != 1
                or link["created_at"] != observation.created_at
            ):
                raise evidence_ledger_inconsistent()
            expected_references.append(expected_reference)
            observed_reference_ids.add(reference_id)
            observed_link_ids.add(link_id)
        if (
            set(references_by_source)
            != {
                item.source.source_id for item in expected_references
            }
            or set(links_by_reference) != observed_reference_ids
        ):
            raise evidence_ledger_inconsistent()
        if (
            observed_reference_ids
            and physical_schema_version == PRIVATE_SCHEMA20_VERSION
        ):
            placeholders = ", ".join("?" for _ in observed_reference_ids)
            ordered_reference_ids = tuple(sorted(observed_reference_ids))
            if connection.execute(
                "SELECT 1 FROM completion_bundle_members "
                f"WHERE evidence_reference_id IN ({placeholders}) LIMIT 1",
                ordered_reference_ids,
            ).fetchone() is not None:
                raise evidence_ledger_inconsistent()
            ordered_link_ids = tuple(
                sorted(observed_link_ids)
            )
            link_placeholders = ", ".join("?" for _ in ordered_link_ids)
            if connection.execute(
                "SELECT 1 FROM completion_bundle_members "
                f"WHERE criterion_evidence_link_id IN ({link_placeholders}) "
                "LIMIT 1",
                ordered_link_ids,
            ).fetchone() is not None:
                raise evidence_ledger_inconsistent()
        generations: dict[tuple[str, str, int], dict[str, Any]] = {}
        for key, (resolution, _) in resolutions.items():
            attempt = attempts[key][0]
            observation_record = observations.get(key)
            cleanup_record = events.get(key)
            observation = (
                observation_record[0] if observation_record is not None else None
            )
            cleanup = cleanup_record[0] if cleanup_record is not None else None
            state = (
                "terminal"
                if observation is not None
                else "restart_cleaned"
                if cleanup is not None
                else "pending"
            )
            generations[key] = {
                "state": state,
                "resolution": resolution,
                "attempt": attempt,
                "observation": observation,
                "cleanup_event": cleanup,
            }
        return tuple(expected_references), generations
    except (
        EvidenceLedgerError,
        VerificationRunnerModelError,
        StorageError,
        sqlite3.Error,
    ) as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def _validated_verification_runner_references(
    connection: sqlite3.Connection,
) -> tuple[_ExpectedEvidenceReference, ...]:
    references, _ = _validated_verification_runner_graph(connection)
    return references


def _validate_schema20_admitted_rows(
    connection: sqlite3.Connection,
    *,
    allow_native_bundle_v2: bool,
) -> None:
    try:
        _validated_verification_runner_references(connection)
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc
    cycle_basis_predicate = "verification_runner_observation_id IS NOT NULL"
    bundle_basis_predicate = "verification_runner_observation_id IS NOT NULL"
    if not allow_native_bundle_v2:
        cycle_basis_predicate += " OR verification_basis_kind IS NOT NULL"
        bundle_basis_predicate += " OR verification_basis_kind IS NOT NULL"
    forbidden_runner_basis = sum(
        int(row[0])
        for row in (
            connection.execute(
                "SELECT COUNT(*) FROM tasks "
                "WHERE review_target_runner_basis_version != 0"
            ).fetchone(),
            connection.execute(
                "SELECT COUNT(*) FROM task_completion_cycles WHERE "
                + cycle_basis_predicate
            ).fetchone(),
            connection.execute(
                "SELECT COUNT(*) FROM completion_evidence_bundles WHERE "
                + bundle_basis_predicate
            ).fetchone(),
        )
        if row is not None
    )
    if forbidden_runner_basis:
        raise _unreadable_project_state()


def validate_current_schema20_admitted_rows(
    connection: sqlite3.Connection,
) -> None:
    """Admit only the closed audit-only Runner graph and null gate basis."""

    _validate_schema20_admitted_rows(
        connection,
        allow_native_bundle_v2=True,
    )


def validate_schema20_storage(
    connection: sqlite3.Connection,
    *,
    allow_native_bundle_v2: bool = False,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate the exact schema-v20 storage foundation and its admitted rows."""

    marker = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 20"
    ).fetchone()
    if (
        current_schema_version(connection) != PRIVATE_SCHEMA20_VERSION
        or missing_migration_versions(connection, PRIVATE_SCHEMA20_VERSION)
        or marker is None
        or str(marker["name"]) != PRIVATE_SCHEMA20_MIGRATION_NAME
        or connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version > 20 LIMIT 1"
        ).fetchone()
        is not None
    ):
        raise _unreadable_project_state()
    _validate_schema20_owned_contract(connection)
    try:
        validate_completion_cycle_storage(connection)
        validate_evidence_ledger_storage(
            connection,
            _privacy_success_cache=_privacy_success_cache,
        )
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc

    _validate_schema20_admitted_rows(
        connection,
        allow_native_bundle_v2=allow_native_bundle_v2,
    )
    _schema20_integrity_checks(connection)


def validate_schema18_19_storage(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate the complete schema-v18 or schema-v19 publication source."""

    version = current_schema_version(connection)
    if (
        version not in {18, 19}
        or missing_migration_versions(connection, version)
        or schema_objects_inconsistent_with_version(connection, version)
    ):
        raise _unreadable_project_state()
    try:
        validate_completion_cycle_storage(connection)
        validate_evidence_ledger_storage(
            connection,
            _privacy_success_cache=_privacy_success_cache,
        )
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc


def validate_schema18_19_storage_for_recovery(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate v18/v19 state while preserving one Task-local rejection."""

    version = current_schema_version(connection)
    if (
        version not in {18, 19}
        or missing_migration_versions(connection, version)
        or schema_objects_inconsistent_with_version(connection, version)
    ):
        raise _unreadable_project_state()
    task_rejection: StoredTaskVerificationError | None = None
    try:
        validate_evidence_ledger_storage_for_recovery(
            connection,
            _privacy_success_cache=_privacy_success_cache,
        )
    except StoredTaskVerificationError as exc:
        task_rejection = exc
    validate_completion_cycle_storage(connection)
    if task_rejection is not None:
        raise task_rejection


def validate_schema20_storage_for_recovery(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate complete v20 state while preserving one Task-local rejection."""

    marker = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 20"
    ).fetchone()
    if (
        current_schema_version(connection) != PRIVATE_SCHEMA20_VERSION
        or missing_migration_versions(connection, PRIVATE_SCHEMA20_VERSION)
        or marker is None
        or str(marker["name"]) != PRIVATE_SCHEMA20_MIGRATION_NAME
        or connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version > 20 LIMIT 1"
        ).fetchone()
        is not None
    ):
        raise _unreadable_project_state()
    _validate_schema20_owned_contract(connection)
    task_rejection: StoredTaskVerificationError | None = None
    try:
        validate_evidence_ledger_storage_for_recovery(
            connection,
            _privacy_success_cache=_privacy_success_cache,
        )
    except StoredTaskVerificationError as exc:
        task_rejection = exc
    _validate_schema20_admitted_rows(
        connection,
        allow_native_bundle_v2=True,
    )
    try:
        validate_completion_cycle_storage(connection)
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc
    _schema20_integrity_checks(connection)
    if task_rejection is not None:
        raise task_rejection


def _private_schema20_failure(fail_stage: str | None, stage: str) -> None:
    if fail_stage == stage:
        raise StorageError(
            "internal_error",
            "injected private schema-v20 rehearsal failure",
        )


def _migrate_schema20_connection(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
    allow_native_bundle_v2_reentry: bool = False,
) -> bool:
    """Apply or validate migration 20 on one caller-owned connection."""

    source_version = PRIVATE_SCHEMA20_VERSION - 1
    foreign_keys_disabled = False
    try:
        version = current_schema_version(connection)
        if version == PRIVATE_SCHEMA20_VERSION:
            connection.execute("BEGIN")
            try:
                if current_schema_version(connection) != PRIVATE_SCHEMA20_VERSION:
                    raise _unreadable_project_state()
                validate_schema20_storage(
                    connection,
                    allow_native_bundle_v2=allow_native_bundle_v2_reentry,
                )
            finally:
                connection.rollback()
            return False
        if version != source_version:
            raise StorageError(
                "migration_required",
                "schema-v20 migration requires complete schema version 19",
            )

        connection.execute("PRAGMA foreign_keys = OFF")
        foreign_keys_disabled = True
        connection.execute("PRAGMA legacy_alter_table = ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
            raise _unreadable_project_state()
        connection.execute("BEGIN IMMEDIATE")
        try:
            if (
                current_schema_version(connection) != source_version
                or missing_migration_versions(connection, source_version)
                or schema_objects_inconsistent_with_version(
                    connection,
                    source_version,
                )
            ):
                raise StorageError(
                    "migration_required",
                    "schema-v20 migration requires complete schema version 19",
                )
            validate_completion_cycle_storage(connection)
            validate_evidence_ledger_storage(connection)
            _schema20_integrity_checks(connection)
            preserved_tables = tuple(
                table_name
                for table_name, introduced_version in (
                    _SCHEMA_TABLE_INTRODUCED_VERSION.items()
                )
                if (
                    table_name != "schema_migrations"
                    and introduced_version <= source_version
                )
            )
            before = _selected_table_projection_snapshot(
                connection,
                preserved_tables,
            )
            column_basis = {name: value[0] for name, value in before.items()}

            for statement in _R3A_SCHEMA20_COLUMN_ALTERS:
                connection.execute(statement)
            _private_schema20_failure(fail_stage, "after_columns")
            _rebuild_completion_evidence_bundle_v20(connection)
            _private_schema20_failure(fail_stage, "after_bundle")
            for statement in _verification_runner_table_statements():
                connection.execute(statement)
            _private_schema20_failure(fail_stage, "after_runner_tables")
            for statement in (
                *_verification_runner_index_statements(),
                *_verification_runner_trigger_statements(),
            ):
                connection.execute(statement)
            connection.execute(
                "DROP TRIGGER trg_criterion_evidence_links_matrix_insert"
            )
            connection.execute(_criterion_evidence_links_v20_matrix_trigger_sql())
            _private_schema20_failure(fail_stage, "after_objects")

            after = _selected_table_projection_snapshot(
                connection,
                preserved_tables,
                column_basis=column_basis,
            )
            if after != before:
                raise _unreadable_project_state()
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (20, ?, ?)",
                (PRIVATE_SCHEMA20_MIGRATION_NAME, utc_now()),
            )
            _private_schema20_failure(fail_stage, "after_marker")
            validate_schema20_storage(connection)
            _private_schema20_failure(fail_stage, "before_commit")
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
    except sqlite3.Error as exc:
        if is_sqlite_busy_or_locked(exc):
            raise StorageError("database_busy", DATABASE_BUSY_MESSAGE) from exc
        raise _unreadable_project_state() from exc
    finally:
        if foreign_keys_disabled:
            connection.execute("PRAGMA legacy_alter_table = OFF")
            connection.execute("PRAGMA foreign_keys = ON")
            if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
                raise _unreadable_project_state()


def rehearse_schema20_storage(
    db_path: Path,
    *,
    fail_stage: str | None = None,
) -> None:
    """Migrate one caller-owned disposable v19 database in place, privately."""

    allowed_stages = {
        None,
        "after_columns",
        "after_bundle",
        "after_runner_tables",
        "after_objects",
        "after_marker",
        "before_commit",
    }
    path = Path(db_path)
    if (
        fail_stage not in allowed_stages
        or not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
    ):
        raise StorageError(
            "internal_error",
            "private schema-v20 rehearsal target is invalid",
        )
    validate_operational_journal_state(path)
    try:
        connection = connect_existing(path)
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open private rehearsal database",
        ) from exc

    try:
        _migrate_schema20_connection(connection, fail_stage=fail_stage)
    finally:
        connection.close()


_SCHEMA21_REBUILT_TABLES = (
    "completion_evidence_bundles",
    "verification_runner_resolutions",
    "verification_runner_attempts",
    "verification_runner_observations",
)
_SCHEMA21_TEMP_TABLES = tuple(
    f"{table_name}_v20" for table_name in _SCHEMA21_REBUILT_TABLES
)


def _schema21_migration_temporary_name_collision(
    connection: sqlite3.Connection,
) -> bool:
    return any(
        connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'view') "
            "AND name = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()
        is not None
        for name in _SCHEMA21_TEMP_TABLES
    )


def _schema21_temporary_table_present(connection: sqlite3.Connection) -> bool:
    return any(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()
        is not None
        for name in _SCHEMA21_TEMP_TABLES
    )


def _schema21_preserved_object_snapshot(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    changed_names = {
        "trg_task_completion_cycles_verification_basis_insert",
        "trg_task_completion_cycles_evidence_basis_insert",
    }
    return tuple(
        (str(row["type"]), str(row["name"]), str(row["tbl_name"]), str(row["sql"]))
        for row in rows
        if str(row["name"]) not in changed_names
        if str(row["tbl_name"]) not in _SCHEMA21_REBUILT_TABLES
    )


def _schema21_failure(fail_stage: str | None, stage: str) -> None:
    if fail_stage == stage:
        raise StorageError(
            "internal_error",
            "injected private schema-v21 rehearsal failure",
        )


def _validate_schema21_admitted_rows(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    try:
        _validated_verification_runner_references(connection)
        validate_completion_cycle_storage(connection)
        validate_evidence_ledger_storage(
            connection,
            _privacy_success_cache=_privacy_success_cache,
        )
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc


def validate_current_schema21_admitted_rows(
    connection: sqlite3.Connection,
) -> None:
    """Validate the schema-v21 Runner graph at the normal current boundary."""

    try:
        _validated_verification_runner_references(connection)
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc


def validate_schema21_storage(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate the exact schema-v21 storage and closed tagged graph."""

    marker = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 21"
    ).fetchone()
    if (
        current_schema_version(connection) != PRIVATE_SCHEMA21_VERSION
        or missing_migration_versions(connection, PRIVATE_SCHEMA21_VERSION)
        or marker is None
        or str(marker["name"]) != PRIVATE_SCHEMA21_MIGRATION_NAME
        or connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version > 21 LIMIT 1"
        ).fetchone()
        is not None
    ):
        raise _unreadable_project_state()
    _validate_schema21_owned_contract(connection)
    _validate_schema21_admitted_rows(
        connection,
        _privacy_success_cache=_privacy_success_cache,
    )
    _schema20_integrity_checks(connection)


def validate_schema22_storage(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate explicit v22 storage with unchanged retained Evidence semantics."""

    marker = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 22"
    ).fetchone()
    if (
        current_schema_version(connection) != PRIVATE_SCHEMA22_VERSION
        or missing_migration_versions(connection, PRIVATE_SCHEMA22_VERSION)
        or marker is None
        or str(marker["name"]) != PRIVATE_SCHEMA22_MIGRATION_NAME
        or connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version > 22 LIMIT 1"
        ).fetchone()
        is not None
    ):
        raise _unreadable_project_state()
    _validate_schema22_owned_contract(connection)
    _validate_schema21_admitted_rows(
        connection,
        _privacy_success_cache=_privacy_success_cache,
    )
    _schema20_integrity_checks(connection)


def validate_schema21_storage_for_recovery(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate v21 while preserving the bounded Task-local recovery exception."""

    marker = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 21"
    ).fetchone()
    if (
        current_schema_version(connection) != PRIVATE_SCHEMA21_VERSION
        or missing_migration_versions(connection, PRIVATE_SCHEMA21_VERSION)
        or marker is None
        or str(marker["name"]) != PRIVATE_SCHEMA21_MIGRATION_NAME
        or connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version > 21 LIMIT 1"
        ).fetchone()
        is not None
    ):
        raise _unreadable_project_state()
    _validate_schema21_owned_contract(connection)
    task_rejection: StoredTaskVerificationError | None = None
    try:
        validate_evidence_ledger_storage_for_recovery(
            connection,
            _privacy_success_cache=_privacy_success_cache,
        )
    except StoredTaskVerificationError as exc:
        task_rejection = exc
    _validated_verification_runner_references(connection)
    validate_completion_cycle_storage(connection)
    _schema20_integrity_checks(connection)
    if task_rejection is not None:
        raise task_rejection


def _schema22_preserved_object_snapshot(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    return tuple(
        (str(row["type"]), str(row["name"]), str(row["tbl_name"]), str(row["sql"]))
        for row in rows
        if str(row["name"]) != "trg_task_completion_cycles_evidence_basis_insert"
        if str(row["tbl_name"]) not in _SCHEMA22_REBUILT_TABLES
    )


def _migrate_schema22_connection(connection: sqlite3.Connection) -> bool:
    """Migrate complete v21, or validation-only reenter explicit v22 storage."""

    if connection.in_transaction:
        raise StorageError(
            "internal_error", "schema-v22 migration requires no active transaction"
        )
    foreign_keys_disabled = False
    legacy_alter_enabled = False
    try:
        if (
            int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1
            or int(connection.execute("PRAGMA legacy_alter_table").fetchone()[0])
            != 0
        ):
            raise _unreadable_project_state()
        version = current_schema_version(connection)
        if version == PRIVATE_SCHEMA22_VERSION:
            connection.execute("BEGIN")
            try:
                validate_schema22_storage(connection)
            finally:
                connection.rollback()
            return False
        if version != PRIVATE_SCHEMA21_VERSION:
            raise StorageError(
                "migration_required",
                "schema-v22 migration requires complete schema version 21",
            )

        connection.execute("PRAGMA foreign_keys = OFF")
        foreign_keys_disabled = True
        connection.execute("PRAGMA legacy_alter_table = ON")
        legacy_alter_enabled = True
        if (
            int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0
            or int(connection.execute("PRAGMA legacy_alter_table").fetchone()[0])
            != 1
        ):
            raise _unreadable_project_state()
        connection.execute("BEGIN IMMEDIATE")
        try:
            validate_schema21_storage(connection)
            if _unowned_rebuilt_table_attachments(
                connection,
                table_names=_SCHEMA22_REBUILT_TABLES,
                expected_objects=_SCHEMA22_EXPECTED_OBJECTS,
            ) or _schema22_migration_temporary_name_collision(connection):
                raise _unreadable_project_state()
            preserved_tables = tuple(
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' "
                    "AND name != 'schema_migrations' ORDER BY name"
                ).fetchall()
            )
            before = _selected_table_projection_snapshot(connection, preserved_tables)
            column_basis = {name: value[0] for name, value in before.items()}
            preserved_objects = _schema22_preserved_object_snapshot(connection)
            connection.execute(
                "DROP TRIGGER trg_task_completion_cycles_evidence_basis_insert"
            )
            for table_name, temporary_name in zip(
                _SCHEMA22_REBUILT_TABLES, _SCHEMA22_TEMP_TABLES, strict=True
            ):
                connection.execute(
                    f"ALTER TABLE {_quoted_identifier(table_name)} "
                    f"RENAME TO {_quoted_identifier(temporary_name)}"
                )
            replacements = _schema22_replacement_statements()
            for statement in replacements:
                kind, _name, _table_name = _schema20_statement_identity(statement)
                if kind == "table":
                    connection.execute(statement)
            for table_name, temporary_name in zip(
                _SCHEMA22_REBUILT_TABLES, _SCHEMA22_TEMP_TABLES, strict=True
            ):
                projection = ", ".join(
                    _quoted_identifier(column) for column in column_basis[table_name]
                )
                connection.execute(
                    f"INSERT INTO {_quoted_identifier(table_name)} ({projection}) "
                    f"SELECT {projection} FROM {_quoted_identifier(temporary_name)}"
                )
            for temporary_name in reversed(_SCHEMA22_TEMP_TABLES):
                connection.execute(f"DROP TABLE {_quoted_identifier(temporary_name)}")
            for statement in replacements:
                kind, _name, _table_name = _schema20_statement_identity(statement)
                if kind != "table":
                    connection.execute(statement)
            if _selected_table_projection_snapshot(
                connection, preserved_tables, column_basis=column_basis
            ) != before or _schema22_preserved_object_snapshot(
                connection
            ) != preserved_objects:
                raise _unreadable_project_state()
            _validate_schema22_owned_contract(connection)
            _schema20_integrity_checks(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (22, ?, ?)",
                (PRIVATE_SCHEMA22_MIGRATION_NAME, utc_now()),
            )
            validate_schema22_storage(connection)
            # Admitted external marker triggers must not change business rows.
            if _selected_table_projection_snapshot(
                connection, preserved_tables, column_basis=column_basis
            ) != before:
                raise _unreadable_project_state()
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
    except sqlite3.Error as exc:
        if is_sqlite_busy_or_locked(exc):
            raise StorageError("database_busy", DATABASE_BUSY_MESSAGE) from exc
        raise _unreadable_project_state() from exc
    finally:
        if legacy_alter_enabled:
            connection.execute("PRAGMA legacy_alter_table = OFF")
        if foreign_keys_disabled:
            connection.execute("PRAGMA foreign_keys = ON")
        if (
            int(connection.execute("PRAGMA legacy_alter_table").fetchone()[0]) != 0
            or int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1
        ):
            raise _unreadable_project_state()


def _migrate_schema21_connection(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> bool:
    """Apply or validation-only reenter migration 21 on a caller connection."""

    foreign_keys_disabled = False
    legacy_alter_enabled = False
    try:
        if (
            int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1
            or int(connection.execute("PRAGMA legacy_alter_table").fetchone()[0])
            != 0
        ):
            raise _unreadable_project_state()
        version = current_schema_version(connection)
        if version == PRIVATE_SCHEMA21_VERSION:
            connection.execute("BEGIN")
            try:
                validate_schema21_storage(connection)
            finally:
                connection.rollback()
            return False
        if version != PRIVATE_SCHEMA20_VERSION:
            raise StorageError(
                "migration_required",
                "schema-v21 migration requires complete schema version 20",
            )

        connection.execute("PRAGMA foreign_keys = OFF")
        foreign_keys_disabled = True
        connection.execute("PRAGMA legacy_alter_table = ON")
        legacy_alter_enabled = True
        if (
            int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0
            or int(connection.execute("PRAGMA legacy_alter_table").fetchone()[0])
            != 1
        ):
            raise _unreadable_project_state()
        connection.execute("BEGIN IMMEDIATE")
        try:
            if (
                current_schema_version(connection) != PRIVATE_SCHEMA20_VERSION
                or missing_migration_versions(connection, PRIVATE_SCHEMA20_VERSION)
            ):
                raise _unreadable_project_state()
            validate_schema20_storage(
                connection,
                allow_native_bundle_v2=True,
            )

            preserved_tables = tuple(
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' "
                    "AND name != 'schema_migrations' ORDER BY name"
                ).fetchall()
            )
            before = _selected_table_projection_snapshot(
                connection,
                preserved_tables,
            )
            column_basis = {name: value[0] for name, value in before.items()}
            preserved_objects = _schema21_preserved_object_snapshot(connection)
            connection.execute(
                "DROP TRIGGER trg_task_completion_cycles_verification_basis_insert"
            )
            connection.execute(
                "DROP TRIGGER trg_task_completion_cycles_evidence_basis_insert"
            )
            _schema21_failure(fail_stage, "after_cycle_guards")

            for table_name, temporary_name in zip(
                _SCHEMA21_REBUILT_TABLES,
                _SCHEMA21_TEMP_TABLES,
                strict=True,
            ):
                connection.execute(
                    f"ALTER TABLE {_quoted_identifier(table_name)} "
                    f"RENAME TO {_quoted_identifier(temporary_name)}"
                )
            _schema21_failure(fail_stage, "after_renames")

            runner_tables = _verification_runner_table_statements(
                schema_version=PRIVATE_SCHEMA21_VERSION,
            )
            for statement in runner_tables[:3]:
                connection.execute(statement)
            connection.execute(
                _completion_evidence_bundle_v20_table_sql(
                    schema_version=PRIVATE_SCHEMA21_VERSION,
                )
            )
            _schema21_failure(fail_stage, "after_tables")

            copy_order = (
                "verification_runner_resolutions",
                "verification_runner_attempts",
                "verification_runner_observations",
                "completion_evidence_bundles",
            )
            for table_name in copy_order:
                columns = column_basis[table_name]
                projection = ", ".join(
                    _quoted_identifier(column) for column in columns
                )
                connection.execute(
                    f"INSERT INTO {_quoted_identifier(table_name)} ({projection}) "
                    f"SELECT {projection} FROM "
                    f"{_quoted_identifier(table_name + '_v20')}"
                )
            _schema21_failure(fail_stage, "after_copy")

            for temporary_name in (
                "completion_evidence_bundles_v20",
                "verification_runner_observations_v20",
                "verification_runner_attempts_v20",
                "verification_runner_resolutions_v20",
            ):
                connection.execute(
                    f"DROP TABLE {_quoted_identifier(temporary_name)}"
                )
            _schema21_failure(fail_stage, "after_drop_old")

            for statement in _verification_runner_index_statements():
                _kind, _name, table_name = _schema20_statement_identity(statement)
                if table_name in _SCHEMA21_REBUILT_TABLES:
                    connection.execute(statement)
            for statement in _verification_runner_trigger_statements(
                schema_version=PRIVATE_SCHEMA21_VERSION,
            ):
                _kind, _name, table_name = _schema20_statement_identity(statement)
                if table_name in _SCHEMA21_REBUILT_TABLES:
                    connection.execute(statement)
            for statement in _bundle_v20_recreated_object_statements():
                connection.execute(statement)
            connection.execute(
                _task_completion_cycle_verification_basis_v21_trigger_sql()
            )
            connection.execute(
                _task_completion_cycle_evidence_basis_v21_trigger_sql()
            )
            _schema21_failure(fail_stage, "after_objects")

            after = _selected_table_projection_snapshot(
                connection,
                preserved_tables,
                column_basis=column_basis,
            )
            if after != before or _schema21_preserved_object_snapshot(
                connection
            ) != preserved_objects:
                raise _unreadable_project_state()
            _validate_schema21_owned_contract(connection)
            _schema20_integrity_checks(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (21, ?, ?)",
                (PRIVATE_SCHEMA21_MIGRATION_NAME, utc_now()),
            )
            _schema21_failure(fail_stage, "after_marker")
            validate_schema21_storage(connection)
            _schema21_failure(fail_stage, "before_commit")
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
    except sqlite3.Error as exc:
        if is_sqlite_busy_or_locked(exc):
            raise StorageError("database_busy", DATABASE_BUSY_MESSAGE) from exc
        raise _unreadable_project_state() from exc
    finally:
        if legacy_alter_enabled:
            connection.execute("PRAGMA legacy_alter_table = OFF")
        if foreign_keys_disabled:
            connection.execute("PRAGMA foreign_keys = ON")
        if (
            int(connection.execute("PRAGMA legacy_alter_table").fetchone()[0])
            != 0
            or int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1
        ):
            raise _unreadable_project_state()


def rehearse_schema21_storage(
    db_path: Path,
    *,
    fail_stage: str | None = None,
) -> None:
    """Migrate one caller-owned disposable v20 database in place."""

    allowed_stages = {
        None,
        "after_cycle_guards",
        "after_renames",
        "after_tables",
        "after_copy",
        "after_drop_old",
        "after_objects",
        "after_marker",
        "before_commit",
    }
    path = Path(db_path)
    if (
        fail_stage not in allowed_stages
        or not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
    ):
        raise StorageError(
            "internal_error",
            "private schema-v21 rehearsal target is invalid",
        )
    validate_operational_journal_state(path)
    try:
        connection = connect_existing(path)
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open private schema-v21 rehearsal database",
        ) from exc
    try:
        _migrate_schema21_connection(connection, fail_stage=fail_stage)
    finally:
        connection.close()


def _normalized_schema_sql(statement: str) -> str:
    return " ".join(statement.strip().removesuffix(";").split())


def _owned_schema_sql_fingerprint(
    connection: sqlite3.Connection,
    *,
    schema_version: int,
) -> str:
    if schema_version not in {
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
    }:
        raise AssertionError("owned schema fingerprint version is unsupported")
    owned_names = tuple(
        sorted(
            {
                name
                for inventory in (
                    _SCHEMA_TABLE_INTRODUCED_VERSION,
                    _SCHEMA_INDEX_INTRODUCED_VERSION,
                    _SCHEMA_TRIGGER_INTRODUCED_VERSION,
                )
                for name, introduced_version in inventory.items()
                if introduced_version <= schema_version
            }
        )
    )
    expected_count = sum(
        1
        for inventory in (
            _SCHEMA_TABLE_INTRODUCED_VERSION,
            _SCHEMA_INDEX_INTRODUCED_VERSION,
            _SCHEMA_TRIGGER_INTRODUCED_VERSION,
        )
        for introduced_version in inventory.values()
        if introduced_version <= schema_version
    )
    if len(owned_names) != expected_count:
        raise AssertionError("owned schema object inventory contains a duplicate name")
    placeholders = ", ".join("?" for _ in owned_names)
    rows = tuple(
        (
            str(row["type"]),
            str(row["name"]),
            str(row["tbl_name"]),
            _normalized_schema_sql(str(row["sql"])),
        )
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL "
            f"AND name IN ({placeholders}) ORDER BY type, name",
            owned_names,
        ).fetchall()
    )
    if len(rows) != expected_count:
        raise _unreadable_project_state()
    payload = json.dumps(
        rows,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"taskgov-owned-schema-v1\0" + payload).hexdigest()


_SCHEMA20_OWNED_SCHEMA_FINGERPRINT = (
    "7e138accb34abb126f55767193bcfb2466b9c920d14b4699e150eeae7baaec63"
)
_SCHEMA21_OWNED_SCHEMA_FINGERPRINT = (
    "8b7aa6d9619c2e98118c9c9c0ed8979a7e56c7ef9c5d3de51980f41a35f80558"
)
_SCHEMA22_OWNED_SCHEMA_FINGERPRINT = (
    "711309d9dcf8dbf9513ef5c1dadeef9faf96c11436b4ef21c06388838f8f9b71"
)
_SCHEMA20_EXPECTED_OBJECTS = tuple(_schema20_expected_objects().items())
_SCHEMA21_EXPECTED_OBJECTS = tuple(_schema21_expected_objects().items())
_SCHEMA22_EXPECTED_OBJECTS = tuple(_schema22_expected_objects().items())


def _validate_completion_evidence_bundle_schema_contract(
    connection: sqlite3.Connection,
) -> None:
    version = current_schema_version(connection)
    if version == PRIVATE_SCHEMA20_VERSION:
        _validate_schema20_owned_contract(connection)
        return
    if version == PRIVATE_SCHEMA21_VERSION:
        _validate_schema21_owned_contract(connection)
        return
    if version == PRIVATE_SCHEMA22_VERSION:
        _validate_schema22_owned_contract(connection)
        return
    marker = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 19"
    ).fetchone()
    if marker is None or str(marker["name"]) != "completion_evidence_bundles":
        raise evidence_ledger_inconsistent()
    if required_schema_objects_missing(connection, schema_version=19):
        raise evidence_ledger_inconsistent()

    statements = completion_evidence_bundle_schema_statements()
    for statement in statements[:5]:
        match = re.match(
            r"\s*CREATE\s+TABLE\s+([a-z0-9_]+)\b",
            statement,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise AssertionError("completion Bundle table inventory is incomplete")
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (match.group(1),),
        ).fetchone()
        if (
            row is None
            or row["sql"] is None
            or _normalized_schema_sql(str(row["sql"]))
            != _normalized_schema_sql(statement)
        ):
            raise evidence_ledger_inconsistent()

    cycle_columns = {
        str(row["name"]): row
        for row in connection.execute(
            "PRAGMA table_xinfo(task_completion_cycles)"
        ).fetchall()
    }
    evidence_basis = cycle_columns.get("evidence_basis_version")
    bundle_id = cycle_columns.get("completion_evidence_bundle_id")
    if (
        evidence_basis is None
        or str(evidence_basis["type"]).upper() != "INTEGER"
        or int(evidence_basis["notnull"]) != 1
        or str(evidence_basis["dflt_value"]) != "0"
        or int(evidence_basis["pk"]) != 0
        or int(evidence_basis["hidden"]) != 0
        or bundle_id is None
        or str(bundle_id["type"]).upper() != "TEXT"
        or int(bundle_id["notnull"]) != 0
        or bundle_id["dflt_value"] is not None
        or int(bundle_id["pk"]) != 0
        or int(bundle_id["hidden"]) != 0
    ):
        raise evidence_ledger_inconsistent()
    cycle_table = connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'task_completion_cycles'"
    ).fetchone()
    if cycle_table is None or cycle_table["sql"] is None:
        raise evidence_ledger_inconsistent()
    normalized_cycle_sql = _normalized_schema_sql(str(cycle_table["sql"]))
    required_cycle_fragments = (
        "evidence_basis_version INTEGER NOT NULL DEFAULT 0 "
        "CHECK (evidence_basis_version IN (0, 1))",
        "completion_evidence_bundle_id TEXT REFERENCES "
        "completion_evidence_bundles(completion_evidence_bundle_id) "
        "DEFERRABLE INITIALLY DEFERRED",
    )
    if any(
        normalized_cycle_sql.count(fragment) != 1
        for fragment in required_cycle_fragments
    ):
        raise evidence_ledger_inconsistent()

    expected_indexes: dict[str, str] = {}
    expected_triggers: dict[str, str] = {}
    for statement in statements:
        normalized = _normalized_schema_sql(statement)
        index_match = re.match(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+([a-z0-9_]+)\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if index_match is not None:
            expected_indexes[index_match.group(1)] = normalized
        trigger_match = re.match(
            r"CREATE\s+TRIGGER\s+([a-z0-9_]+)\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if trigger_match is not None:
            expected_triggers[trigger_match.group(1)] = normalized

    actual_indexes = {
        str(row["name"]): _normalized_schema_sql(str(row["sql"]))
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
        if str(row["name"]) in expected_indexes and row["sql"] is not None
    }
    actual_triggers = {
        str(row["name"]): _normalized_schema_sql(str(row["sql"]))
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        if str(row["name"]) in expected_triggers and row["sql"] is not None
    }
    if actual_indexes != expected_indexes or actual_triggers != expected_triggers:
        raise evidence_ledger_inconsistent()

    bundle_foreign_key = (
        "completion_evidence_bundles",
        (("completion_evidence_bundle_id", "completion_evidence_bundle_id"),),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    )
    if bundle_foreign_key not in _foreign_key_signatures(
        connection,
        "task_completion_cycles",
    ):
        raise evidence_ledger_inconsistent()


def _completion_history_trigger_definitions() -> dict[str, str]:
    expected_names = {
        "trg_task_completion_cycles_no_update",
        "trg_task_completion_cycles_no_delete",
        "trg_tasks_completion_history_coverage_immutable",
        "trg_task_events_completion_cycle_link_immutable",
    }
    definitions: dict[str, str] = {}
    for statement in completion_cycle_history_schema_statements():
        match = re.match(
            r"\s*CREATE\s+TRIGGER\s+([a-z0-9_]+)\b",
            statement,
            flags=re.IGNORECASE,
        )
        if match is not None and match.group(1) in expected_names:
            definitions[match.group(1)] = _normalized_schema_sql(statement)
    if set(definitions) != expected_names:
        raise AssertionError("completion history trigger inventory is incomplete")
    return definitions


def _verification_receipt_trigger_definitions(
    *,
    schema_version: int | None = None,
) -> dict[str, str]:
    expected_names = {
        "trg_verification_receipts_no_update",
        "trg_verification_receipts_no_delete",
        "trg_verification_receipts_locked_basis_insert",
        "trg_task_completion_cycles_verification_basis_insert",
    }
    definitions: dict[str, str] = {}
    statements = list(verification_receipt_schema_statements())
    if schema_version in {PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION}:
        statements = [
            (
                _task_completion_cycle_verification_basis_v21_trigger_sql()
                if "trg_task_completion_cycles_verification_basis_insert"
                in _normalized_schema_sql(statement)
                else statement
            )
            for statement in statements
        ]
    for statement in statements:
        match = re.match(
            r"\s*CREATE\s+TRIGGER\s+([a-z0-9_]+)\b",
            statement,
            flags=re.IGNORECASE,
        )
        if match is not None and match.group(1) in expected_names:
            definitions[match.group(1)] = _normalized_schema_sql(statement)
    if set(definitions) != expected_names:
        raise AssertionError("verification Receipt trigger inventory is incomplete")
    return definitions


_MIGRATION_PRESERVATION_TABLES = (
    "project_meta",
    "tasks",
    "task_events",
    "tool_events",
    "review_receipts",
    "review_findings",
    "handoff_records",
    "task_contract_revisions",
    "task_effort_activity",
    "task_effort_bases",
    "project_maintenance",
    "managed_backup_generations",
    "task_checkpoints",
    "viewer_maintenance_state",
    "project_path_binding_history",
)


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _migration_preservation_snapshot(
    connection: sqlite3.Connection,
    *,
    column_basis: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, tuple[tuple[str, ...], int, str]]:
    result: dict[str, tuple[tuple[str, ...], int, str]] = {}
    for table_name in _MIGRATION_PRESERVATION_TABLES:
        columns = (
            column_basis[table_name]
            if column_basis is not None
            else tuple(
                str(row["name"])
                for row in connection.execute(
                    f"PRAGMA table_info({_quoted_identifier(table_name)})"
                ).fetchall()
            )
        )
        if not columns:
            raise StorageError(
                "migration_required",
                "completion-history migration requires complete schema version 14",
            )
        projection = ", ".join(_quoted_identifier(column) for column in columns)
        rows = [
            list(row)
            for row in connection.execute(
                f"SELECT {projection} FROM {_quoted_identifier(table_name)} "
                "ORDER BY rowid"
            ).fetchall()
        ]
        payload = json.dumps(
            rows,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        result[table_name] = (
            columns,
            len(rows),
            hashlib.sha256(payload).hexdigest(),
        )
    return result


def completion_history_inconsistent() -> StorageError:
    return StorageError(
        "completion_history_inconsistent",
        "stored completion history is inconsistent",
    )


def evidence_ledger_inconsistent() -> StorageError:
    return StorageError(
        "evidence_ledger_inconsistent",
        "stored evidence ledger is inconsistent",
    )


def verification_runner_state_invalid() -> StorageError:
    return StorageError(
        "runner_state_invalid",
        "verification runner state could not be changed safely",
    )


def evidence_ledger_sqlite_error(exc: sqlite3.Error) -> StorageError:
    if is_sqlite_busy_or_locked(exc):
        return operational_sqlite_error(
            exc,
            fallback_message="could not read evidence ledger",
        )
    return evidence_ledger_inconsistent()


def evidence_ledger_boundary_error(exc: BaseException) -> StorageError:
    if isinstance(exc, sqlite3.Error):
        return evidence_ledger_sqlite_error(exc)
    if isinstance(exc, StorageError) and exc.code == "database_busy":
        return exc
    return evidence_ledger_inconsistent()


def _require_evidence_writer(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise StorageError(
            "internal_error",
            "evidence ledger writes require an active transaction",
        )


def _criterion_id_for_text_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    kind: str,
    exact_text: str,
    created_at: str,
) -> str:
    digest = contract_criterion_digest(kind, exact_text)
    row = connection.execute(
        """
        SELECT criterion_id, criterion_text
          FROM contract_criteria
         WHERE project_id = ? AND task_id = ?
           AND criterion_kind = ? AND digest = ?
        """,
        (project_id, task_id, kind, digest),
    ).fetchone()
    if row is not None:
        if type(row["criterion_text"]) is not str or row["criterion_text"] != exact_text:
            raise evidence_ledger_inconsistent()
        return str(row["criterion_id"])
    criterion_id = f"tg_contract_criterion_{secrets.token_hex(8)}"
    connection.execute(
        """
        INSERT INTO contract_criteria(
          criterion_id, project_id, task_id, criterion_kind,
          criterion_text, digest, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            criterion_id,
            project_id,
            task_id,
            kind,
            exact_text,
            digest,
            created_at,
        ),
    )
    return criterion_id


def capture_or_reuse_current_authority_snapshot_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    producer_class: str = "taskgov_core",
    created_at: str | None = None,
) -> AuthoritySnapshotBinding:
    """Capture the exact locked Task/Contract basis without target mutation."""

    _require_evidence_writer(connection)
    if producer_class not in {"taskgov_core", "legacy_migration"}:
        raise evidence_ledger_inconsistent()
    timestamp = validate_utc_timestamp(
        created_at or utc_now(),
        field="authority snapshot creation time",
    )
    task = _read_task_for_authority_capture(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    revision = task["current_contract_revision"]
    if type(revision) is not int or revision < 0:
        raise evidence_ledger_inconsistent()
    if revision == 0:
        contract_state = "contract_unspecified"
        scope = acceptance = constraints = authority_ref = ""
    else:
        contract = connection.execute(
            """
            SELECT scope, acceptance, constraints_text, authority_ref
              FROM task_contract_revisions
             WHERE project_id = ? AND task_id = ? AND revision = ?
            """,
            (project_id, task_id, revision),
        ).fetchone()
        if contract is None or any(
            type(contract[name]) is not str
            for name in ("scope", "acceptance", "constraints_text", "authority_ref")
        ):
            raise evidence_ledger_inconsistent()
        contract_state = "contract_specified"
        scope = str(contract["scope"])
        acceptance = str(contract["acceptance"])
        constraints = str(contract["constraints_text"])
        authority_ref = str(contract["authority_ref"])
    verification = task["verification"]
    if type(verification) is not str or len(verification) > 1_000:
        raise evidence_ledger_inconsistent()
    acceptance_criterion_id = (
        _criterion_id_for_text_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
            kind="acceptance",
            exact_text=acceptance,
            created_at=timestamp,
        )
        if revision > 0 and acceptance
        else None
    )
    verification_criterion_id = (
        _criterion_id_for_text_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
            kind="verification",
            exact_text=verification,
            created_at=timestamp,
        )
        if verification.strip()
        else None
    )
    digest_values = {
        "project_id": project_id,
        "task_id": task_id,
        "task_title": str(task["title"]),
        "task_description": str(task["description"]),
        "review_tier": int(task["review_tier"]),
        "verification": verification,
        "verification_digest": _verification_expectation_digest(verification),
        "contract_revision": revision,
        "contract_state": contract_state,
        "contract_scope": scope,
        "contract_acceptance": acceptance,
        "contract_constraints": constraints,
        "contract_authority_ref": authority_ref,
        "acceptance_criterion_id": acceptance_criterion_id,
        "verification_criterion_id": verification_criterion_id,
        "producer_class": producer_class,
        "producer_version": 1,
    }
    basis_digest = authority_snapshot_basis_digest(digest_values)
    current = connection.execute(
        """
        SELECT authority_snapshot_id, generation, basis_digest
          FROM authority_snapshots
         WHERE project_id = ? AND task_id = ?
         ORDER BY generation DESC LIMIT 1
        """,
        (project_id, task_id),
    ).fetchone()
    current_pointer_id = task["current_authority_snapshot_id"]
    current_pointer_generation = task["current_authority_snapshot_generation"]
    if current is None:
        if current_pointer_id is not None or current_pointer_generation != 0:
            raise evidence_ledger_inconsistent()
        current_generation = 0
    else:
        current_generation = current["generation"]
        if (
            type(current_generation) is not int
            or current_generation <= 0
            or current_pointer_id != current["authority_snapshot_id"]
            or current_pointer_generation != current_generation
        ):
            raise evidence_ledger_inconsistent()
    if current is not None and current["basis_digest"] == basis_digest:
        snapshot_id = current["authority_snapshot_id"]
        generation = current_generation
    else:
        if current_generation >= SQLITE_INT64_MAX:
            raise evidence_ledger_inconsistent()
        generation = current_generation + 1
        if not 1 <= generation <= SQLITE_INT64_MAX:
            raise evidence_ledger_inconsistent()
        snapshot_id = f"tg_authority_snapshot_{secrets.token_hex(8)}"
        connection.execute(
            """
            INSERT INTO authority_snapshots(
              authority_snapshot_id, project_id, task_id, generation,
              task_title, task_description, review_tier, verification,
              verification_digest, contract_revision, contract_state,
              contract_scope, contract_acceptance, contract_constraints,
              contract_authority_ref, basis_digest, producer_class,
              producer_version, created_at
            ) VALUES (
              :authority_snapshot_id, :project_id, :task_id, :generation,
              :task_title, :task_description, :review_tier, :verification,
              :verification_digest, :contract_revision, :contract_state,
              :contract_scope, :contract_acceptance, :contract_constraints,
              :contract_authority_ref, :basis_digest, :producer_class,
              :producer_version, :created_at
            )
            """,
            {
                "authority_snapshot_id": snapshot_id,
                "generation": generation,
                "basis_digest": basis_digest,
                "created_at": timestamp,
                **digest_values,
            },
        )
        for kind, criterion_id in (
            ("acceptance", acceptance_criterion_id),
            ("verification", verification_criterion_id),
        ):
            if criterion_id is not None:
                connection.execute(
                    """
                    INSERT INTO authority_snapshot_criteria(
                      project_id, task_id, authority_snapshot_id,
                      criterion_kind, criterion_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, task_id, snapshot_id, kind, criterion_id),
                )
    connection.execute(
        """
        UPDATE tasks
           SET current_authority_snapshot_id = ?,
               current_authority_snapshot_generation = ?
         WHERE project_id = ? AND task_id = ?
        """,
        (snapshot_id, generation, project_id, task_id),
    )
    return AuthoritySnapshotBinding(
        authority_snapshot_id=snapshot_id,
        generation=generation,
        acceptance_criterion_id=acceptance_criterion_id,
        verification_criterion_id=verification_criterion_id,
    )


def read_current_target_ledger_binding_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    _require_evidence_writer(connection)
    task = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    fields = (
        "review_target_kind",
        "review_target_value",
        "review_target_base_revision",
        "review_target_generation",
        "review_target_capture_version",
        "review_target_authority_snapshot_id",
        "review_target_acceptance_criterion_id",
        "review_target_verification_criterion_id",
        "review_target_artifact_manifest_id",
    )
    return {field: task[field] for field in fields}


def invalid_verification_evidence() -> StorageError:
    return StorageError(
        "invalid_verification_evidence",
        "stored verification evidence is inconsistent",
    )


def _verification_receipt_int(
    value: object,
    *,
    minimum: int = 0,
) -> int:
    if type(value) is not int or not minimum <= value <= SQLITE_INT64_MAX:
        raise invalid_verification_evidence()
    return value


def _validate_verification_receipt_row(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Validate one complete stored Receipt and return its fixed storage shape."""

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


def _completion_int(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = SQLITE_INT64_MAX,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise completion_history_inconsistent()
    return value


def _completion_bool(value: object) -> bool:
    numeric = _completion_int(value, maximum=1)
    return bool(numeric)


def _validate_completion_target(
    *,
    kind: str,
    value: str,
    base_revision: str,
    generation: int,
) -> None:
    from task_governance_tool.completion import FULL_GIT_OBJECT_ID
    from task_governance_tool.reviews import DIFF_FINGERPRINT

    if len(value) > 500 or len(base_revision) > 500:
        raise completion_history_inconsistent()
    _completion_int(generation)
    if kind == "":
        if value or base_revision or generation != 0:
            raise completion_history_inconsistent()
        return
    if generation <= 0 or not value or value != value.strip():
        raise completion_history_inconsistent()
    if kind == "git_snapshot":
        if (
            DIFF_FINGERPRINT.fullmatch(value) is None
            or FULL_GIT_OBJECT_ID.fullmatch(base_revision) is None
            or set(base_revision) == {"0"}
        ):
            raise completion_history_inconsistent()
        return
    if base_revision:
        raise completion_history_inconsistent()
    if kind == "git_commit":
        if FULL_GIT_OBJECT_ID.fullmatch(value) is None or set(value) == {"0"}:
            raise completion_history_inconsistent()
    elif kind == "diff_fingerprint":
        if DIFF_FINGERPRINT.fullmatch(value) is None:
            raise completion_history_inconsistent()
    elif kind != "external_revision":
        raise completion_history_inconsistent()


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

    _require_completion_cycle_writer(connection)
    if (
        current_schema_version(connection) != SCHEMA_VERSION
        or missing_migration_versions(connection, SCHEMA_VERSION)
        or required_schema_objects_missing(
            connection,
            schema_version=SCHEMA_VERSION,
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


def _validate_completion_evidence(
    cycle: CompletionCycle,
) -> None:
    from task_governance_tool.completion import (
        CompletionEvidenceError,
        WRITABLE_EVIDENCE_KINDS,
        validate_evidence_matrix,
    )

    if (
        len(cycle.completion_evidence_revision) > 500
        or len(cycle.completion_evidence_reason) > 1000
        or len(cycle.completion_commit_hash) > 500
    ):
        raise completion_history_inconsistent()
    evidence = {
        "completion_evidence_kind": cycle.completion_evidence_kind,
        "completion_evidence_revision": cycle.completion_evidence_revision,
        "completion_evidence_reason": cycle.completion_evidence_reason,
        "external_revision_approved": int(cycle.external_revision_approved),
        "completion_commit_required": int(cycle.completion_commit_required),
        "completion_commit_hash": cycle.completion_commit_hash,
    }
    try:
        validate_evidence_matrix(
            evidence,
            allow_legacy=cycle.completeness == "partial",
        )
    except (CompletionEvidenceError, TypeError, ValueError) as exc:
        raise completion_history_inconsistent() from exc
    if cycle.origin == "native_done" and (
        cycle.completion_evidence_kind not in WRITABLE_EVIDENCE_KINDS
    ):
        raise completion_history_inconsistent()
    if cycle.completion_evidence_kind in {
        "external_revision",
        "legacy_unverified",
    } and (
        not cycle.completion_evidence_revision
        or cycle.completion_evidence_revision
        != cycle.completion_evidence_revision.strip()
    ):
        raise completion_history_inconsistent()


def _validate_completion_gate_basis(
    basis: CompletionGateBasis,
    *,
    review_tier: int,
) -> None:
    _completion_int(basis.version, maximum=1)
    if basis.version == 0:
        if (
            basis.kind != "unknown"
            or basis.required_independent_passes is not None
            or basis.qualifying_independent_passes is not None
            or basis.changes_requested_count is not None
            or basis.open_high_count is not None
            or basis.open_medium_count is not None
            or basis.fresh_review_required_count is not None
            or basis.qualifying_receipt_ids
        ):
            raise completion_history_inconsistent()
        return

    required = {0: 0, 1: 1, 2: 2}[review_tier]
    if basis.required_independent_passes != required:
        raise completion_history_inconsistent()
    counts = (
        basis.qualifying_independent_passes,
        basis.changes_requested_count,
        basis.open_high_count,
        basis.open_medium_count,
        basis.fresh_review_required_count,
    )
    if any(value is None for value in counts):
        raise completion_history_inconsistent()
    for value in counts:
        _completion_int(value)
    if (
        basis.changes_requested_count != 0
        or basis.open_high_count != 0
        or basis.open_medium_count != 0
        or basis.fresh_review_required_count != 0
    ):
        raise completion_history_inconsistent()
    if any(
        not isinstance(receipt_id, str) or not receipt_id
        for receipt_id in basis.qualifying_receipt_ids
    ):
        raise completion_history_inconsistent()
    if basis.kind == "independent_passes":
        if (
            review_tier not in {1, 2}
            or basis.qualifying_independent_passes < required
            or len(basis.qualifying_receipt_ids) != required
        ):
            raise completion_history_inconsistent()
    elif basis.kind == "self_review_fallback":
        if (
            review_tier not in {1, 2}
            or basis.qualifying_independent_passes >= required
            or len(basis.qualifying_receipt_ids) != 1
        ):
            raise completion_history_inconsistent()
    elif basis.kind == "not_required":
        if review_tier != 0 or len(basis.qualifying_receipt_ids) != 1:
            raise completion_history_inconsistent()
    else:
        raise completion_history_inconsistent()


def _cycle_from_row(row: sqlite3.Row) -> CompletionCycle:
    row_fields = set(row.keys())
    receipt_ids = tuple(
        str(row[field_name])
        for field_name in (
            "qualifying_receipt_id_1",
            "qualifying_receipt_id_2",
        )
        if row[field_name] is not None
    )
    attestation_value = row["verification_attestation"]
    if attestation_value is None:
        attestation: bool | None = None
    else:
        attestation = _completion_bool(attestation_value)
    basis = CompletionGateBasis(
        version=_completion_int(row["gate_basis_version"], maximum=1),
        kind=str(row["review_basis_kind"]),
        required_independent_passes=(
            _completion_int(row["required_independent_passes"], maximum=2)
            if row["required_independent_passes"] is not None
            else None
        ),
        qualifying_independent_passes=(
            _completion_int(row["qualifying_independent_passes"])
            if row["qualifying_independent_passes"] is not None
            else None
        ),
        changes_requested_count=(
            _completion_int(row["changes_requested_count"])
            if row["changes_requested_count"] is not None
            else None
        ),
        open_high_count=(
            _completion_int(row["open_high_count"])
            if row["open_high_count"] is not None
            else None
        ),
        open_medium_count=(
            _completion_int(row["open_medium_count"])
            if row["open_medium_count"] is not None
            else None
        ),
        fresh_review_required_count=(
            _completion_int(row["fresh_review_required_count"])
            if row["fresh_review_required_count"] is not None
            else None
        ),
        qualifying_receipt_ids=receipt_ids,
    )
    cycle = CompletionCycle(
        completion_cycle_id=str(row["completion_cycle_id"]),
        project_id=str(row["project_id"]),
        task_id=str(row["task_id"]),
        saved_cycle_ordinal=_completion_int(
            row["saved_cycle_ordinal"],
            minimum=1,
        ),
        origin=str(row["origin"]),
        completeness=str(row["completeness"]),
        completed_at=(
            str(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
        recorded_at=str(row["recorded_at"]),
        contract_revision=_completion_int(row["contract_revision"]),
        review_tier=_completion_int(row["review_tier"], maximum=2),
        verification_expectation=str(row["verification_expectation"]),
        verification_attestation=attestation,
        verification_basis_version=(
            _completion_int(
                row["verification_basis_version"],
                maximum=1,
            )
            if "verification_basis_version" in row_fields
            else 0
        ),
        verification_expectation_digest=(
            str(row["verification_expectation_digest"])
            if (
                "verification_expectation_digest" in row_fields
                and row["verification_expectation_digest"] is not None
            )
            else None
        ),
        verification_receipt_id=(
            str(row["verification_receipt_id"])
            if (
                "verification_receipt_id" in row_fields
                and row["verification_receipt_id"] is not None
            )
            else None
        ),
        verification_subject_basis_version=(
            _completion_int(
                row["verification_subject_basis_version"],
                maximum=1,
            )
            if "verification_subject_basis_version" in row_fields
            else 0
        ),
        subject_authority_snapshot_id=(
            str(row["subject_authority_snapshot_id"])
            if (
                "subject_authority_snapshot_id" in row_fields
                and row["subject_authority_snapshot_id"] is not None
            )
            else None
        ),
        subject_verification_criterion_id=(
            str(row["subject_verification_criterion_id"])
            if (
                "subject_verification_criterion_id" in row_fields
                and row["subject_verification_criterion_id"] is not None
            )
            else None
        ),
        evidence_basis_version=(
            _completion_int(
                row["evidence_basis_version"],
                maximum=1,
            )
            if "evidence_basis_version" in row_fields
            else 0
        ),
        completion_evidence_bundle_id=(
            str(row["completion_evidence_bundle_id"])
            if (
                "completion_evidence_bundle_id" in row_fields
                and row["completion_evidence_bundle_id"] is not None
            )
            else None
        ),
        verification_basis_kind=(
            str(row["verification_basis_kind"])
            if (
                "verification_basis_kind" in row_fields
                and row["verification_basis_kind"] is not None
            )
            else None
        ),
        verification_runner_observation_id=(
            str(row["verification_runner_observation_id"])
            if (
                "verification_runner_observation_id" in row_fields
                and row["verification_runner_observation_id"] is not None
            )
            else None
        ),
        completion_evidence_kind=str(row["completion_evidence_kind"]),
        completion_evidence_revision=str(row["completion_evidence_revision"]),
        completion_evidence_reason=str(row["completion_evidence_reason"]),
        external_revision_approved=_completion_bool(
            row["external_revision_approved"]
        ),
        completion_commit_required=_completion_bool(
            row["completion_commit_required"]
        ),
        completion_commit_hash=str(row["completion_commit_hash"]),
        review_target_kind=str(row["review_target_kind"]),
        review_target_value=str(row["review_target_value"]),
        review_target_base_revision=str(row["review_target_base_revision"]),
        review_target_generation=_completion_int(
            row["review_target_generation"]
        ),
        gate_basis=basis,
    )
    _validate_completion_cycle(cycle)
    return cycle


def _validate_completion_cycle(cycle: CompletionCycle) -> None:
    if COMPLETION_CYCLE_ID_PATTERN.fullmatch(cycle.completion_cycle_id) is None:
        raise completion_history_inconsistent()
    if not cycle.project_id or not cycle.task_id:
        raise completion_history_inconsistent()
    _completion_int(cycle.saved_cycle_ordinal, minimum=1)
    _completion_int(cycle.contract_revision)
    _completion_int(cycle.review_tier, maximum=2)
    try:
        validate_utc_timestamp(
            cycle.recorded_at,
            field="completion cycle record time",
        )
        if cycle.completed_at is not None:
            validate_utc_timestamp(
                cycle.completed_at,
                field="completion cycle completion time",
            )
    except StorageError as exc:
        raise completion_history_inconsistent() from exc
    if cycle.verification_expectation not in {"specified", "unspecified"}:
        raise completion_history_inconsistent()
    _completion_int(cycle.verification_basis_version, maximum=1)
    _completion_int(cycle.verification_subject_basis_version, maximum=1)
    _completion_int(cycle.evidence_basis_version, maximum=1)
    if cycle.verification_basis_kind is None:
        if cycle.verification_runner_observation_id is not None:
            raise completion_history_inconsistent()
    elif cycle.verification_basis_kind == "caller_attestation":
        if (
            cycle.origin != "native_done"
            or cycle.verification_expectation != "specified"
            or cycle.verification_receipt_id is None
            or cycle.verification_runner_observation_id is not None
        ):
            raise completion_history_inconsistent()
    elif cycle.verification_basis_kind == "not_required":
        if (
            cycle.origin != "native_done"
            or cycle.verification_expectation != "unspecified"
            or cycle.verification_receipt_id is not None
            or cycle.verification_runner_observation_id is not None
        ):
            raise completion_history_inconsistent()
    elif cycle.verification_basis_kind == "runner_observation":
        if (
            cycle.origin != "native_done"
            or cycle.verification_expectation != "specified"
            or cycle.verification_receipt_id is not None
            or cycle.verification_runner_observation_id is None
            or VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN.fullmatch(
                cycle.verification_runner_observation_id
            )
            is None
        ):
            raise completion_history_inconsistent()
    else:
        raise completion_history_inconsistent()
    if cycle.evidence_basis_version == 0:
        if cycle.completion_evidence_bundle_id is not None:
            raise completion_history_inconsistent()
    elif (
        cycle.origin != "native_done"
        or cycle.completion_evidence_bundle_id is None
        or COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(
            cycle.completion_evidence_bundle_id
        )
        is None
    ):
        raise completion_history_inconsistent()
    if cycle.verification_subject_basis_version == 0:
        if (
            cycle.subject_authority_snapshot_id is not None
            or cycle.subject_verification_criterion_id is not None
        ):
            raise completion_history_inconsistent()
    elif cycle.verification_expectation == "specified":
        if (
            cycle.subject_authority_snapshot_id is None
            or cycle.subject_verification_criterion_id is None
        ):
            raise completion_history_inconsistent()
    elif (
        cycle.subject_authority_snapshot_id is not None
        or cycle.subject_verification_criterion_id is not None
    ):
        raise completion_history_inconsistent()
    if cycle.verification_basis_version == 0:
        if (
            cycle.verification_expectation_digest is not None
            or cycle.verification_receipt_id is not None
        ):
            raise completion_history_inconsistent()
    else:
        if (
            cycle.origin != "native_done"
            or cycle.verification_expectation_digest is None
            or LOWER_HEX_64_PATTERN.fullmatch(
                cycle.verification_expectation_digest
            )
            is None
        ):
            raise completion_history_inconsistent()
        if cycle.verification_expectation == "specified":
            if cycle.verification_basis_kind == "runner_observation":
                if cycle.verification_receipt_id is not None:
                    raise completion_history_inconsistent()
            elif (
                cycle.verification_receipt_id is None
                or VERIFICATION_RECEIPT_ID_PATTERN.fullmatch(
                    cycle.verification_receipt_id
                )
                is None
            ):
                raise completion_history_inconsistent()
        elif cycle.verification_receipt_id is not None:
            raise completion_history_inconsistent()
    _validate_completion_evidence(cycle)
    _validate_completion_target(
        kind=cycle.review_target_kind,
        value=cycle.review_target_value,
        base_revision=cycle.review_target_base_revision,
        generation=cycle.review_target_generation,
    )
    _validate_completion_gate_basis(
        cycle.gate_basis,
        review_tier=cycle.review_tier,
    )
    if cycle.origin == "native_done":
        _validate_completion_projection_relationship(cycle)
        if (
            cycle.completeness != "complete"
            or cycle.completed_at is None
            or cycle.verification_attestation is not True
            or not cycle.review_target_kind
            or cycle.gate_basis.version != 1
        ):
            raise completion_history_inconsistent()
    elif cycle.origin == "legacy_current_done":
        if (
            cycle.completeness != "partial"
            or cycle.verification_attestation is not None
            or cycle.gate_basis.version != 0
        ):
            raise completion_history_inconsistent()
    else:
        raise completion_history_inconsistent()


def _validate_cycle_verification_receipt_projection(
    cycle: CompletionCycle,
    receipt: dict[str, Any] | None,
    *,
    validate_complete_receipt: bool = True,
) -> None:
    if cycle.verification_basis_version == 0:
        if receipt is not None:
            raise completion_history_inconsistent()
        return
    if cycle.verification_basis_kind == "runner_observation":
        if receipt is not None or cycle.verification_receipt_id is not None:
            raise completion_history_inconsistent()
        return
    if cycle.verification_expectation == "unspecified":
        if receipt is not None or cycle.verification_receipt_id is not None:
            raise completion_history_inconsistent()
        return
    if receipt is None:
        raise completion_history_inconsistent()
    if validate_complete_receipt:
        try:
            validated = _validate_verification_receipt_row(receipt)
        except StorageError as exc:
            raise completion_history_inconsistent() from exc
    else:
        validated = receipt
    required_link_fields = {
        "verification_receipt_id",
        "project_id",
        "task_id",
        "contract_revision",
        "verification_expectation_digest",
        "result",
        "scope_coverage",
        "target_kind",
        "target_value",
        "target_base_revision",
        "target_generation",
    }
    if not required_link_fields <= set(validated):
        raise completion_history_inconsistent()
    if (
        validated["verification_receipt_id"]
        != cycle.verification_receipt_id
        or validated["project_id"] != cycle.project_id
        or validated["task_id"] != cycle.task_id
        or validated["contract_revision"] != cycle.contract_revision
        or validated["verification_expectation_digest"]
        != cycle.verification_expectation_digest
        or validated["target_kind"] != cycle.review_target_kind
        or validated["target_value"] != cycle.review_target_value
        or validated["target_base_revision"]
        != cycle.review_target_base_revision
        or validated["target_generation"]
        != cycle.review_target_generation
        or validated["result"] != "pass"
        or validated["scope_coverage"] != "full"
    ):
        raise completion_history_inconsistent()
    if cycle.verification_subject_basis_version == 1 and (
        not {
            "verification_subject_basis_version",
            "subject_authority_snapshot_id",
            "subject_verification_criterion_id",
        }
        <= set(validated)
        or validated["verification_subject_basis_version"] != 1
        or validated["subject_authority_snapshot_id"]
        != cycle.subject_authority_snapshot_id
        or validated["subject_verification_criterion_id"]
        != cycle.subject_verification_criterion_id
    ):
        raise completion_history_inconsistent()


def _validate_cycle_verification_receipt(
    connection: sqlite3.Connection,
    cycle: CompletionCycle,
) -> None:
    receipt: dict[str, Any] | None = None
    if cycle.verification_receipt_id is not None:
        row = connection.execute(
            """
            SELECT *
              FROM verification_receipts
             WHERE verification_receipt_id = ?
            """,
            (cycle.verification_receipt_id,),
        ).fetchone()
        if row is not None:
            receipt = dict(row)
    _validate_cycle_verification_receipt_projection(cycle, receipt)


def _validate_cycle_receipts(
    connection: sqlite3.Connection,
    cycle: CompletionCycle,
) -> None:
    if cycle.verification_basis_version == 1:
        _validate_cycle_verification_receipt(connection, cycle)
    basis = cycle.gate_basis
    if basis.version == 0:
        return
    target_parameters = (
        cycle.project_id,
        cycle.task_id,
        cycle.review_target_kind,
        cycle.review_target_value,
        cycle.review_target_base_revision,
        cycle.review_target_generation,
    )
    independent_rows = connection.execute(
        """
        SELECT review_receipt_id, reviewer_key
          FROM review_receipts
         WHERE project_id = ?
           AND task_id = ?
           AND target_kind = ?
           AND target_value = ?
           AND target_base_revision = ?
           AND target_generation = ?
           AND receipt_kind = 'independent'
           AND verdict = 'pass'
           AND user_approved = 0
         ORDER BY reviewer_key COLLATE BINARY ASC,
                  review_receipt_id COLLATE BINARY ASC
        """,
        target_parameters,
    ).fetchall()
    reviewer_keys = [str(row["reviewer_key"]) for row in independent_rows]
    changes_requested = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM review_receipts
             WHERE project_id = ?
               AND task_id = ?
               AND target_kind = ?
               AND target_value = ?
               AND target_base_revision = ?
               AND target_generation = ?
               AND verdict = 'changes_requested'
            """,
            target_parameters,
        ).fetchone()[0]
    )

    fallback_receipt_id: str | None = None
    not_required_receipt_id: str | None = None
    if basis.kind == "self_review_fallback":
        expected_approval = 1 if cycle.review_tier == 2 else 0
        fallback = connection.execute(
            """
            SELECT review_receipt_id
              FROM review_receipts
             WHERE project_id = ?
               AND task_id = ?
               AND target_kind = ?
               AND target_value = ?
               AND target_base_revision = ?
               AND target_generation = ?
               AND receipt_kind = 'self_review_fallback'
               AND verdict = 'pass'
               AND user_approved = ?
             ORDER BY review_receipt_id COLLATE BINARY ASC
             LIMIT 1
            """,
            (*target_parameters, expected_approval),
        ).fetchone()
        fallback_receipt_id = (
            str(fallback["review_receipt_id"]) if fallback is not None else None
        )
    elif basis.kind == "not_required":
        not_required = connection.execute(
            """
            SELECT review_receipt_id
              FROM review_receipts
             WHERE project_id = ?
               AND task_id = ?
               AND target_kind = ?
               AND target_value = ?
               AND target_base_revision = ?
               AND target_generation = ?
               AND receipt_kind = 'not_required'
               AND verdict = 'not_required'
               AND user_approved = 0
               AND summary != ''
             ORDER BY review_receipt_id COLLATE BINARY ASC
             LIMIT 1
            """,
            target_parameters,
        ).fetchone()
        not_required_receipt_id = (
            str(not_required["review_receipt_id"])
            if not_required is not None
            else None
        )
    _validate_cycle_receipt_projection(
        cycle,
        independent_count=len(independent_rows),
        distinct_independent_reviewers=len(set(reviewer_keys)),
        independent_receipt_ids=tuple(
            str(row["review_receipt_id"]) for row in independent_rows[:2]
        ),
        changes_requested_count=changes_requested,
        fallback_receipt_id=fallback_receipt_id,
        not_required_receipt_id=not_required_receipt_id,
    )


def _validate_cycle_receipt_projection(
    cycle: CompletionCycle,
    *,
    independent_count: int,
    distinct_independent_reviewers: int,
    independent_receipt_ids: tuple[str, ...],
    changes_requested_count: int,
    fallback_receipt_id: str | None,
    not_required_receipt_id: str | None,
) -> None:
    basis = cycle.gate_basis
    if (
        basis.version != 1
        or independent_count != distinct_independent_reviewers
        or independent_count != basis.qualifying_independent_passes
        or changes_requested_count != basis.changes_requested_count
    ):
        raise completion_history_inconsistent()

    if basis.kind == "independent_passes":
        expected = independent_receipt_ids[
            : basis.required_independent_passes
        ]
    elif basis.kind == "self_review_fallback":
        expected = (
            (fallback_receipt_id,)
            if fallback_receipt_id is not None
            else ()
        )
    else:
        expected = (
            (not_required_receipt_id,)
            if not_required_receipt_id is not None
            else ()
        )
    if expected != basis.qualifying_receipt_ids:
        raise completion_history_inconsistent()


def _validate_cycle_receipts_batch(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    cycles: tuple[CompletionCycle, ...],
) -> None:
    receipt_subject_projection = (
        """
                   , receipt.verification_subject_basis_version AS receipt_subject_basis_version
                   , receipt.subject_authority_snapshot_id AS receipt_subject_authority_snapshot_id
                   , receipt.subject_verification_criterion_id AS receipt_subject_verification_criterion_id
        """
        if current_schema_version(connection) >= 18
        else """
                   , 0 AS receipt_subject_basis_version
                   , NULL AS receipt_subject_authority_snapshot_id
                   , NULL AS receipt_subject_verification_criterion_id
        """
    )
    verification_cycles = tuple(
        cycle
        for cycle in cycles
        if cycle.verification_basis_version == 1
    )
    for offset in range(
        0,
        len(verification_cycles),
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
    ):
        chunk = verification_cycles[
            offset : offset + COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        ]
        placeholders = ", ".join("?" for _ in chunk)
        cycle_ids = tuple(cycle.completion_cycle_id for cycle in chunk)
        rows = connection.execute(
            f"""
            SELECT cycle.completion_cycle_id,
                   receipt.verification_receipt_id AS receipt_id,
                   receipt.project_id AS receipt_project_id,
                   receipt.task_id AS receipt_task_id,
                   receipt.contract_revision AS receipt_contract_revision,
                   receipt.verification_expectation_digest AS receipt_digest,
                   receipt.result AS receipt_result,
                   receipt.scope_coverage AS receipt_scope_coverage,
                   receipt.target_kind AS receipt_target_kind,
                   receipt.target_value AS receipt_target_value,
                   receipt.target_base_revision AS receipt_target_base_revision,
                   receipt.target_generation AS receipt_target_generation
                   {receipt_subject_projection}
              FROM task_completion_cycles AS cycle
              LEFT JOIN verification_receipts AS receipt
                ON receipt.verification_receipt_id =
                   cycle.verification_receipt_id
             WHERE cycle.project_id = ?
               AND cycle.completion_cycle_id IN ({placeholders})
             ORDER BY cycle.completion_cycle_id COLLATE BINARY
            """,
            (project_id, *cycle_ids),
        ).fetchall()
        rows_by_cycle = {
            str(row["completion_cycle_id"]): row for row in rows
        }
        if len(rows_by_cycle) != len(chunk):
            raise completion_history_inconsistent()
        for cycle in chunk:
            row = rows_by_cycle.get(cycle.completion_cycle_id)
            if row is None or cycle.project_id != project_id:
                raise completion_history_inconsistent()
            receipt = None
            if row["receipt_id"] is not None:
                receipt = {
                    "verification_receipt_id": row["receipt_id"],
                    "project_id": row["receipt_project_id"],
                    "task_id": row["receipt_task_id"],
                    "contract_revision": row[
                        "receipt_contract_revision"
                    ],
                    "verification_expectation_digest": row[
                        "receipt_digest"
                    ],
                    "result": row["receipt_result"],
                    "scope_coverage": row["receipt_scope_coverage"],
                    "target_kind": row["receipt_target_kind"],
                    "target_value": row["receipt_target_value"],
                    "target_base_revision": row[
                        "receipt_target_base_revision"
                    ],
                    "target_generation": row[
                        "receipt_target_generation"
                    ],
                    "verification_subject_basis_version": row[
                        "receipt_subject_basis_version"
                    ],
                    "subject_authority_snapshot_id": row[
                        "receipt_subject_authority_snapshot_id"
                    ],
                    "subject_verification_criterion_id": row[
                        "receipt_subject_verification_criterion_id"
                    ],
                }
            _validate_cycle_verification_receipt_projection(
                cycle,
                receipt,
                validate_complete_receipt=False,
            )

    latest_by_task: dict[str, CompletionCycle] = {}
    for cycle in cycles:
        latest = latest_by_task.get(cycle.task_id)
        if (
            latest is None
            or cycle.saved_cycle_ordinal > latest.saved_cycle_ordinal
        ):
            latest_by_task[cycle.task_id] = cycle
    exact_basis = {
        task_id: cycle
        for task_id, cycle in latest_by_task.items()
        if cycle.verification_basis_version == 1
    }
    rows_by_task: dict[str, sqlite3.Row] = {}
    task_ids = tuple(sorted(exact_basis))
    for offset in range(
        0,
        len(task_ids),
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
    ):
        chunk = task_ids[
            offset : offset + COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        ]
        placeholders = ", ".join("?" for _ in chunk)
        task_rows = connection.execute(
            f"""
            SELECT task_id, status, verification
              FROM tasks
             WHERE project_id = ?
               AND task_id IN ({placeholders})
             ORDER BY task_id COLLATE BINARY
            """,
            (project_id, *chunk),
        ).fetchall()
        rows_by_task.update(
            {str(row["task_id"]): row for row in task_rows}
        )
    if len(rows_by_task) != len(exact_basis):
        raise completion_history_inconsistent()
    for task_id, cycle in exact_basis.items():
        row = rows_by_task.get(task_id)
        if row is None:
            raise completion_history_inconsistent()
        if str(row["status"]) == "done" and not (
            _completion_cycle_matches_exact_verification(
                cycle,
                row["verification"],
            )
        ):
            raise completion_history_inconsistent()

    native_cycles = tuple(
        cycle for cycle in cycles if cycle.gate_basis.version == 1
    )
    for offset in range(
        0,
        len(native_cycles),
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
    ):
        chunk = native_cycles[
            offset : offset + COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        ]
        placeholders = ", ".join("?" for _ in chunk)
        cycle_ids = tuple(cycle.completion_cycle_id for cycle in chunk)
        aggregate_rows = connection.execute(
            f"""
            SELECT cycle.completion_cycle_id,
                   COUNT(
                     CASE
                       WHEN receipt.receipt_kind = 'independent'
                        AND receipt.verdict = 'pass'
                        AND receipt.user_approved = 0
                       THEN 1
                     END
                   ) AS independent_count,
                   COUNT(
                     DISTINCT CASE
                       WHEN receipt.receipt_kind = 'independent'
                        AND receipt.verdict = 'pass'
                        AND receipt.user_approved = 0
                       THEN receipt.reviewer_key
                     END
                   ) AS distinct_independent_reviewers,
                   COUNT(
                     CASE
                       WHEN receipt.verdict = 'changes_requested' THEN 1
                     END
                   ) AS changes_requested_count
              FROM task_completion_cycles AS cycle
              LEFT JOIN review_receipts AS receipt
                ON receipt.project_id = cycle.project_id
               AND receipt.task_id = cycle.task_id
               AND receipt.target_kind = cycle.review_target_kind
               AND receipt.target_value = cycle.review_target_value
               AND receipt.target_base_revision =
                     cycle.review_target_base_revision
               AND receipt.target_generation =
                     cycle.review_target_generation
             WHERE cycle.project_id = ?
               AND cycle.completion_cycle_id IN ({placeholders})
             GROUP BY cycle.completion_cycle_id
            """,
            (project_id, *cycle_ids),
        ).fetchall()
        aggregate_by_cycle = {
            str(row["completion_cycle_id"]): row for row in aggregate_rows
        }

        ranked_rows = connection.execute(
            f"""
            WITH qualifying_receipts AS (
              SELECT
                cycle.completion_cycle_id,
                receipt.review_receipt_id,
                CASE
                  WHEN receipt.receipt_kind = 'independent'
                  THEN 'independent'
                  WHEN receipt.receipt_kind = 'self_review_fallback'
                  THEN 'self_review_fallback'
                  ELSE 'not_required'
                END AS receipt_class,
                CASE
                  WHEN receipt.receipt_kind = 'independent'
                  THEN receipt.reviewer_key
                  ELSE ''
                END AS reviewer_sort_key
                FROM task_completion_cycles AS cycle
                JOIN review_receipts AS receipt
                  ON receipt.project_id = cycle.project_id
                 AND receipt.task_id = cycle.task_id
                 AND receipt.target_kind = cycle.review_target_kind
                 AND receipt.target_value = cycle.review_target_value
                 AND receipt.target_base_revision =
                       cycle.review_target_base_revision
                 AND receipt.target_generation =
                       cycle.review_target_generation
               WHERE cycle.project_id = ?
                 AND cycle.completion_cycle_id IN ({placeholders})
                 AND (
                   (
                     receipt.receipt_kind = 'independent'
                     AND receipt.verdict = 'pass'
                     AND receipt.user_approved = 0
                   )
                   OR (
                     receipt.receipt_kind = 'self_review_fallback'
                     AND receipt.verdict = 'pass'
                     AND receipt.user_approved = CASE
                       WHEN cycle.review_tier = 2 THEN 1 ELSE 0
                     END
                   )
                   OR (
                     receipt.receipt_kind = 'not_required'
                     AND receipt.verdict = 'not_required'
                     AND receipt.user_approved = 0
                     AND receipt.summary != ''
                   )
                 )
            ),
            ranked_receipts AS (
              SELECT
                completion_cycle_id,
                receipt_class,
                review_receipt_id,
                ROW_NUMBER() OVER (
                  PARTITION BY completion_cycle_id, receipt_class
                  ORDER BY reviewer_sort_key COLLATE BINARY ASC,
                           review_receipt_id COLLATE BINARY ASC
                ) AS receipt_rank
                FROM qualifying_receipts
            )
            SELECT
              completion_cycle_id,
              receipt_class,
              review_receipt_id
              FROM ranked_receipts
             WHERE (
               receipt_class = 'independent' AND receipt_rank <= 2
             ) OR (
               receipt_class != 'independent' AND receipt_rank = 1
             )
             ORDER BY completion_cycle_id COLLATE BINARY,
                      receipt_class COLLATE BINARY,
                      receipt_rank
            """,
            (project_id, *cycle_ids),
        ).fetchall()
        selections_by_cycle: dict[str, dict[str, list[str]]] = {}
        for row in ranked_rows:
            cycle_selections = selections_by_cycle.setdefault(
                str(row["completion_cycle_id"]),
                {},
            )
            cycle_selections.setdefault(str(row["receipt_class"]), []).append(
                str(row["review_receipt_id"])
            )

        for cycle in chunk:
            aggregate = aggregate_by_cycle.get(cycle.completion_cycle_id)
            if aggregate is None or cycle.project_id != project_id:
                raise completion_history_inconsistent()
            selections = selections_by_cycle.get(
                cycle.completion_cycle_id,
                {},
            )
            fallback_ids = selections.get("self_review_fallback", ())
            not_required_ids = selections.get("not_required", ())
            _validate_cycle_receipt_projection(
                cycle,
                independent_count=_completion_int(
                    aggregate["independent_count"]
                ),
                distinct_independent_reviewers=_completion_int(
                    aggregate["distinct_independent_reviewers"]
                ),
                independent_receipt_ids=tuple(
                    selections.get("independent", ())
                ),
                changes_requested_count=_completion_int(
                    aggregate["changes_requested_count"]
                ),
                fallback_receipt_id=(
                    fallback_ids[0] if fallback_ids else None
                ),
                not_required_receipt_id=(
                    not_required_ids[0] if not_required_ids else None
                ),
            )


def _validate_completion_history_marker(
    connection: sqlite3.Connection,
) -> None:
    row = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 15"
    ).fetchone()
    if row is None or str(row["name"]) != "completion_cycle_history":
        raise completion_history_inconsistent()


def _validate_completion_capture_activation_marker(
    connection: sqlite3.Connection,
) -> None:
    row = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 16"
    ).fetchone()
    if (
        row is None
        or str(row["name"])
        != "completion_cycle_capture_activation"
    ):
        raise completion_history_inconsistent()


def _validate_verification_receipt_marker(
    connection: sqlite3.Connection,
) -> None:
    row = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 17"
    ).fetchone()
    if row is None or str(row["name"]) != "verification_receipts":
        raise invalid_verification_evidence()


def _foreign_key_signatures(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[tuple[str, tuple[tuple[str, str], ...], str, str, str]]:
    groups: dict[int, list[sqlite3.Row]] = {}
    for row in connection.execute(
        f"PRAGMA foreign_key_list({_quoted_identifier(table_name)})"
    ).fetchall():
        groups.setdefault(int(row["id"]), []).append(row)
    signatures = []
    for rows in groups.values():
        ordered = sorted(rows, key=lambda row: int(row["seq"]))
        first = ordered[0]
        signatures.append(
            (
                str(first["table"]),
                tuple(
                    (str(row["from"]), str(row["to"]))
                    for row in ordered
                ),
                str(first["on_update"]),
                str(first["on_delete"]),
                str(first["match"]),
            )
        )
    return sorted(signatures, key=repr)


def _validate_completion_history_schema_contract(
    connection: sqlite3.Connection,
) -> None:
    receipt_prefix = (
        ("project_id", "project_id"),
        ("task_id", "task_id"),
        ("review_target_kind", "target_kind"),
        ("review_target_value", "target_value"),
        ("review_target_base_revision", "target_base_revision"),
        ("review_target_generation", "target_generation"),
    )
    expected_cycle_foreign_keys = [
            (
                "tasks",
                (
                    ("project_id", "project_id"),
                    ("task_id", "task_id"),
                ),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
            (
                "review_receipts",
                (*receipt_prefix, ("qualifying_receipt_id_1", "review_receipt_id")),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
            (
                "review_receipts",
                (*receipt_prefix, ("qualifying_receipt_id_2", "review_receipt_id")),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
        ]
    if current_schema_version(connection) >= 17:
        expected_cycle_foreign_keys.append(
            (
                "verification_receipts",
                (("verification_receipt_id", "verification_receipt_id"),),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            )
        )
    if current_schema_version(connection) >= 18:
        expected_cycle_foreign_keys.extend(
            [
                (
                    "authority_snapshots",
                    (("subject_authority_snapshot_id", "authority_snapshot_id"),),
                    "NO ACTION", "NO ACTION", "NONE",
                ),
                (
                    "contract_criteria",
                    (("subject_verification_criterion_id", "criterion_id"),),
                    "NO ACTION", "NO ACTION", "NONE",
                ),
            ]
        )
    if current_schema_version(connection) >= 19:
        expected_cycle_foreign_keys.append(
            (
                "completion_evidence_bundles",
                (
                    (
                        "completion_evidence_bundle_id",
                        "completion_evidence_bundle_id",
                    ),
                ),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            )
        )
    expected_cycle_foreign_keys = sorted(
        expected_cycle_foreign_keys,
        key=repr,
    )
    expected_event_foreign_keys = sorted(
        [
            (
                "tasks",
                (("task_id", "task_id"),),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
            (
                "task_completion_cycles",
                (("completion_cycle_id", "completion_cycle_id"),),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
        ],
        key=repr,
    )
    if (
        _foreign_key_signatures(connection, "task_completion_cycles")
        != expected_cycle_foreign_keys
        or _foreign_key_signatures(connection, "task_events")
        != expected_event_foreign_keys
    ):
        raise completion_history_inconsistent()

    expected_indexes = {
        "idx_tasks_project_task_identity": (
            "tasks",
            1,
            0,
            ("project_id", "task_id"),
        ),
        "idx_review_receipts_completion_cycle_reference": (
            "review_receipts",
            1,
            0,
            (
                "project_id",
                "task_id",
                "target_kind",
                "target_value",
                "target_base_revision",
                "target_generation",
                "review_receipt_id",
            ),
        ),
        "idx_task_completion_cycles_task_ordinal": (
            "task_completion_cycles",
            1,
            0,
            ("project_id", "task_id", "saved_cycle_ordinal"),
        ),
        "idx_task_events_completion_cycle": (
            "task_events",
            0,
            1,
            ("completion_cycle_id",),
        ),
    }
    for index_name, (
        table_name,
        expected_unique,
        expected_partial,
        expected_columns,
    ) in expected_indexes.items():
        index_row = next(
            (
                row
                for row in connection.execute(
                    f"PRAGMA index_list({_quoted_identifier(table_name)})"
                ).fetchall()
                if str(row["name"]) == index_name
            ),
            None,
        )
        columns = tuple(
            str(row["name"])
            for row in connection.execute(
                f"PRAGMA index_info({_quoted_identifier(index_name)})"
            ).fetchall()
        )
        if (
            index_row is None
            or int(index_row["unique"]) != expected_unique
            or int(index_row["partial"]) != expected_partial
            or columns != expected_columns
        ):
            raise completion_history_inconsistent()

    expected_triggers = _completion_history_trigger_definitions()
    actual_triggers = {
        str(row["name"]): _normalized_schema_sql(str(row["sql"]))
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        if str(row["name"]) in expected_triggers and row["sql"] is not None
    }
    if actual_triggers != expected_triggers:
        raise completion_history_inconsistent()


def _validate_verification_receipt_schema_contract(
    connection: sqlite3.Connection,
) -> None:
    table_row = connection.execute(
        """
        SELECT sql
          FROM sqlite_master
         WHERE type = 'table' AND name = 'verification_receipts'
        """
    ).fetchone()
    expected_table_sql = _normalized_schema_sql(
        verification_receipt_schema_statements()[0]
    )
    if (
        table_row is None
        or table_row["sql"] is None
        or (
            current_schema_version(connection) == 17
            and _normalized_schema_sql(str(table_row["sql"]))
            != expected_table_sql
        )
    ):
        raise invalid_verification_evidence()

    expected_receipt_foreign_keys = [
        (
            "tasks",
            (
                ("project_id", "project_id"),
                ("task_id", "task_id"),
            ),
            "NO ACTION",
            "NO ACTION",
            "NONE",
        )
    ]
    if current_schema_version(connection) >= 18:
        expected_receipt_foreign_keys.extend(
            [
                (
                    "authority_snapshots",
                    (("subject_authority_snapshot_id", "authority_snapshot_id"),),
                    "NO ACTION", "NO ACTION", "NONE",
                ),
                (
                    "contract_criteria",
                    (("subject_verification_criterion_id", "criterion_id"),),
                    "NO ACTION", "NO ACTION", "NONE",
                ),
            ]
        )
        expected_receipt_foreign_keys = sorted(
            expected_receipt_foreign_keys,
            key=repr,
        )
    if (
        _foreign_key_signatures(connection, "verification_receipts")
        != expected_receipt_foreign_keys
    ):
        raise invalid_verification_evidence()

    expected_indexes = {
        "idx_verification_receipts_task_generation": (
            1,
            0,
            ("project_id", "task_id", "target_generation"),
        ),
        "idx_verification_receipts_exact_basis": (
            0,
            0,
            (
                "project_id",
                "task_id",
                "contract_revision",
                "verification_expectation_digest",
                "target_kind",
                "target_value",
                "target_base_revision",
                "target_generation",
            ),
        ),
        "idx_verification_receipts_recent": (
            0,
            0,
            (
                "project_id",
                "task_id",
                "created_at",
                "verification_receipt_id",
            ),
        ),
    }
    receipt_indexes = connection.execute(
        "PRAGMA index_list(verification_receipts)"
    ).fetchall()
    for index_name, (
        expected_unique,
        expected_partial,
        expected_columns,
    ) in expected_indexes.items():
        index_row = next(
            (
                row
                for row in receipt_indexes
                if str(row["name"]) == index_name
            ),
            None,
        )
        columns = tuple(
            str(row["name"])
            for row in connection.execute(
                f"PRAGMA index_info({_quoted_identifier(index_name)})"
            ).fetchall()
        )
        if (
            index_row is None
            or int(index_row["unique"]) != expected_unique
            or int(index_row["partial"]) != expected_partial
            or columns != expected_columns
        ):
            raise invalid_verification_evidence()

    cycle_columns = {
        str(row["name"]): row
        for row in connection.execute(
            "PRAGMA table_info(task_completion_cycles)"
        ).fetchall()
    }
    basis_column = cycle_columns.get("verification_basis_version")
    digest_column = cycle_columns.get("verification_expectation_digest")
    receipt_column = cycle_columns.get("verification_receipt_id")
    if (
        basis_column is None
        or str(basis_column["type"]).upper() != "INTEGER"
        or int(basis_column["notnull"]) != 1
        or str(basis_column["dflt_value"]) not in {"0", "'0'"}
        or digest_column is None
        or str(digest_column["type"]).upper() != "TEXT"
        or int(digest_column["notnull"]) != 0
        or digest_column["dflt_value"] is not None
        or receipt_column is None
        or str(receipt_column["type"]).upper() != "TEXT"
        or int(receipt_column["notnull"]) != 0
        or receipt_column["dflt_value"] is not None
    ):
        raise invalid_verification_evidence()

    cycle_table_row = connection.execute(
        """
        SELECT sql
          FROM sqlite_master
         WHERE type = 'table' AND name = 'task_completion_cycles'
        """
    ).fetchone()
    if cycle_table_row is None or cycle_table_row["sql"] is None:
        raise invalid_verification_evidence()
    normalized_cycle_table_sql = _normalized_schema_sql(
        str(cycle_table_row["sql"])
    )
    for statement in verification_receipt_schema_statements()[4:7]:
        normalized_statement = _normalized_schema_sql(statement)
        _prefix, separator, column_definition = normalized_statement.partition(
            " ADD COLUMN "
        )
        if not separator or column_definition not in normalized_cycle_table_sql:
            raise invalid_verification_evidence()

    expected_triggers = _verification_receipt_trigger_definitions(
        schema_version=current_schema_version(connection),
    )
    actual_triggers = {
        str(row["name"]): _normalized_schema_sql(str(row["sql"]))
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        if str(row["name"]) in expected_triggers and row["sql"] is not None
    }
    if actual_triggers != expected_triggers:
        raise invalid_verification_evidence()


def _validate_verification_receipt_rows(
    connection: sqlite3.Connection,
) -> None:
    task_bases = {
        (str(row["project_id"]), str(row["task_id"])): row
        for row in connection.execute(
            """
            SELECT project_id, task_id, verification,
                   current_contract_revision,
                   review_target_kind, review_target_value,
                   review_target_base_revision, review_target_generation
              FROM tasks
             ORDER BY project_id COLLATE BINARY, task_id COLLATE BINARY
            """
        ).fetchall()
    }
    seen_generations: set[tuple[str, str, int]] = set()
    for row in connection.execute(
        """
        SELECT *
          FROM verification_receipts
         ORDER BY project_id COLLATE BINARY,
                  task_id COLLATE BINARY,
                  target_generation,
                  verification_receipt_id COLLATE BINARY
        """
    ).fetchall():
        receipt = _validate_verification_receipt_row(dict(row))
        generation_key = (
            receipt["project_id"],
            receipt["task_id"],
            receipt["target_generation"],
        )
        if generation_key in seen_generations:
            raise invalid_verification_evidence()
        seen_generations.add(generation_key)
        task_basis = task_bases.get(
            (receipt["project_id"], receipt["task_id"])
        )
        if task_basis is None:
            raise invalid_verification_evidence()
        try:
            current_generation = _verification_receipt_int(
                task_basis["review_target_generation"]
            )
        except StorageError as exc:
            raise invalid_verification_evidence() from exc
        if receipt["target_generation"] == current_generation:
            exact_verification = task_basis["verification"]
            if (
                not isinstance(exact_verification, str)
                or not exact_verification.strip()
                or receipt["contract_revision"]
                != task_basis["current_contract_revision"]
                or receipt["verification_expectation_digest"]
                != _verification_expectation_digest(exact_verification)
                or receipt["target_kind"] != task_basis["review_target_kind"]
                or receipt["target_value"] != task_basis["review_target_value"]
                or receipt["target_base_revision"]
                != task_basis["review_target_base_revision"]
            ):
                raise invalid_verification_evidence()
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise invalid_verification_evidence()


def validate_verification_receipt_storage(
    connection: sqlite3.Connection,
) -> None:
    """Validate schema-v17 Receipt structure and all immutable rows."""

    _validate_verification_receipt_marker(connection)
    _validate_verification_receipt_schema_contract(connection)
    _validate_verification_receipt_rows(connection)


def _validate_completion_history_structure(
    connection: sqlite3.Connection,
) -> None:
    """Validate completion-history markers, foreign keys, and indexes."""

    _validate_completion_history_marker(connection)
    if current_schema_version(connection) >= 16:
        _validate_completion_capture_activation_marker(connection)
    _validate_completion_history_schema_contract(connection)
    if current_schema_version(connection) >= 17:
        _validate_verification_receipt_marker(connection)
        _validate_verification_receipt_schema_contract(connection)


def validate_completion_cycle_storage(
    connection: sqlite3.Connection,
) -> None:
    """Validate immutable-cycle matrices without replaying migration assertions."""

    _validate_completion_history_structure(connection)
    if current_schema_version(connection) >= 17:
        _validate_verification_receipt_rows(connection)
    task_rows = connection.execute(
        """
        SELECT project_id, task_id, status, verification,
               completion_history_coverage
          FROM tasks
         ORDER BY project_id COLLATE BINARY, task_id COLLATE BINARY
        """
    ).fetchall()
    task_owners: dict[str, str] = {}
    for row in task_rows:
        project_id = str(row["project_id"])
        task_id = str(row["task_id"])
        if (
            not project_id
            or not task_id
            or str(row["completion_history_coverage"])
            not in {"legacy_unknown", "complete"}
            or task_id in task_owners
        ):
            raise completion_history_inconsistent()
        task_owners[task_id] = project_id

    rows = connection.execute(
        """
        SELECT *
          FROM task_completion_cycles
         ORDER BY project_id COLLATE BINARY,
                  task_id COLLATE BINARY,
                  saved_cycle_ordinal
        """
    ).fetchall()
    expected_ordinals: dict[tuple[str, str], int] = {}
    cycle_owners: dict[str, tuple[str, str, str]] = {}
    cycles_by_project: dict[str, list[CompletionCycle]] = {}
    latest_cycles: dict[tuple[str, str], CompletionCycle] = {}
    for row in rows:
        cycle = _cycle_from_row(row)
        if task_owners.get(cycle.task_id) != cycle.project_id:
            raise completion_history_inconsistent()
        owner = (cycle.project_id, cycle.task_id)
        expected = expected_ordinals.get(owner, 1)
        if cycle.saved_cycle_ordinal != expected:
            raise completion_history_inconsistent()
        expected_ordinals[owner] = expected + 1
        latest_cycles[owner] = cycle
        cycles_by_project.setdefault(cycle.project_id, []).append(cycle)
        cycle_owners[cycle.completion_cycle_id] = (
            cycle.project_id,
            cycle.task_id,
            cycle.origin,
        )
    for project_id, cycles in cycles_by_project.items():
        _validate_cycle_receipts_batch(
            connection,
            project_id=project_id,
            cycles=tuple(cycles),
        )
    for row in task_rows:
        owner = (str(row["project_id"]), str(row["task_id"]))
        latest = latest_cycles.get(owner)
        if (
            str(row["status"]) == "done"
            and latest is not None
            and latest.verification_basis_version == 1
        ):
            if not _completion_cycle_matches_exact_verification(
                latest,
                row["verification"],
            ):
                raise completion_history_inconsistent()

    event_rows = connection.execute(
        """
        SELECT task_event_id, project_id, task_id, event_type,
               completion_cycle_id
          FROM task_events
         WHERE completion_cycle_id IS NOT NULL
         ORDER BY completion_cycle_id COLLATE BINARY,
                  event_type COLLATE BINARY,
                  task_event_id COLLATE BINARY
        """
    ).fetchall()
    linked_counts: dict[tuple[str, str], int] = {}
    completion_event_types = {"task_updated", "review_tier_changed"}
    for row in event_rows:
        cycle_id = str(row["completion_cycle_id"])
        owner = cycle_owners.get(cycle_id)
        event_type = str(row["event_type"])
        if (
            owner is None
            or owner[:2] != (
                str(row["project_id"]),
                str(row["task_id"]),
            )
            or event_type
            not in {*completion_event_types, "task_reopened"}
            or (
                event_type in completion_event_types
                and owner[2] != "native_done"
            )
        ):
            raise completion_history_inconsistent()
        link_kind = (
            "completion"
            if event_type in completion_event_types
            else "task_reopened"
        )
        key = (cycle_id, link_kind)
        linked_counts[key] = linked_counts.get(key, 0) + 1
        if linked_counts[key] > 1:
            raise completion_history_inconsistent()
    for cycle_id, (_, _, origin) in cycle_owners.items():
        completion_links = linked_counts.get((cycle_id, "completion"), 0)
        if (
            origin == "native_done"
            and completion_links != 1
            or origin == "legacy_current_done"
            and completion_links != 0
        ):
            raise completion_history_inconsistent()

    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise completion_history_inconsistent()


def _select_completion_gate_basis_for_projection_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    task_projection: dict[str, Any],
) -> CompletionGateBasis:
    """Select one deterministic review basis for a validated Task projection."""

    if not connection.in_transaction or int(
        connection.execute("PRAGMA query_only").fetchone()[0]
    ):
        raise StorageError(
            "internal_error",
            "completion gate basis requires an active writer transaction",
        )
    task = task_projection
    tier = _completion_int(task["review_tier"], maximum=2)
    target_kind = str(task["review_target_kind"])
    target_value = str(task["review_target_value"])
    target_base = str(task["review_target_base_revision"])
    target_generation = _completion_int(task["review_target_generation"])
    _validate_completion_target(
        kind=target_kind,
        value=target_value,
        base_revision=target_base,
        generation=target_generation,
    )
    if not target_kind:
        raise completion_history_inconsistent()
    parameters = (
        project_id,
        task_id,
        target_kind,
        target_value,
        target_base,
        target_generation,
    )
    independent_rows = connection.execute(
        """
        SELECT review_receipt_id, reviewer_key
          FROM review_receipts
         WHERE project_id = ?
           AND task_id = ?
           AND target_kind = ?
           AND target_value = ?
           AND target_base_revision = ?
           AND target_generation = ?
           AND receipt_kind = 'independent'
           AND verdict = 'pass'
           AND user_approved = 0
         ORDER BY reviewer_key COLLATE BINARY ASC,
                  review_receipt_id COLLATE BINARY ASC
        """,
        parameters,
    ).fetchall()
    reviewers = [str(row["reviewer_key"]) for row in independent_rows]
    if len(reviewers) != len(set(reviewers)):
        raise completion_history_inconsistent()
    qualifying = len(independent_rows)
    changes_requested = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM review_receipts
             WHERE project_id = ?
               AND task_id = ?
               AND target_kind = ?
               AND target_value = ?
               AND target_base_revision = ?
               AND target_generation = ?
               AND verdict = 'changes_requested'
            """,
            parameters,
        ).fetchone()[0]
    )
    finding_counts = {
        str(row["kind"]): int(row["count"])
        for row in connection.execute(
            """
            SELECT
              CASE
                WHEN finding.status = 'open' THEN finding.severity
                ELSE 'fresh_review_required'
              END AS kind,
              COUNT(*) AS count
              FROM review_findings AS finding
              JOIN review_receipts AS receipt
                ON receipt.review_receipt_id = finding.review_receipt_id
             WHERE receipt.project_id = ?
               AND receipt.task_id = ?
               AND finding.severity IN ('high', 'medium')
               AND (
                 finding.status = 'open'
                 OR (
                   finding.status = 'resolved'
                   AND receipt.target_generation >= ?
                 )
               )
             GROUP BY kind
            """,
            (project_id, task_id, target_generation),
        ).fetchall()
    }
    open_high = finding_counts.get("high", 0)
    open_medium = finding_counts.get("medium", 0)
    fresh_review_required = finding_counts.get("fresh_review_required", 0)
    if (
        changes_requested
        or open_high
        or open_medium
        or fresh_review_required
    ):
        raise completion_history_inconsistent()

    required = {0: 0, 1: 1, 2: 2}[tier]
    if tier in {1, 2} and qualifying >= required:
        kind = "independent_passes"
        receipt_ids = tuple(
            str(row["review_receipt_id"])
            for row in independent_rows[:required]
        )
    elif tier in {1, 2}:
        expected_approval = 1 if tier == 2 else 0
        fallback = connection.execute(
            """
            SELECT review_receipt_id
              FROM review_receipts
             WHERE project_id = ?
               AND task_id = ?
               AND target_kind = ?
               AND target_value = ?
               AND target_base_revision = ?
               AND target_generation = ?
               AND receipt_kind = 'self_review_fallback'
               AND verdict = 'pass'
               AND user_approved = ?
             ORDER BY review_receipt_id COLLATE BINARY ASC
             LIMIT 1
            """,
            (*parameters, expected_approval),
        ).fetchone()
        if fallback is None:
            raise completion_history_inconsistent()
        kind = "self_review_fallback"
        receipt_ids = (str(fallback["review_receipt_id"]),)
    else:
        not_required = connection.execute(
            """
            SELECT review_receipt_id
              FROM review_receipts
             WHERE project_id = ?
               AND task_id = ?
               AND target_kind = ?
               AND target_value = ?
               AND target_base_revision = ?
               AND target_generation = ?
               AND receipt_kind = 'not_required'
               AND verdict = 'not_required'
               AND user_approved = 0
               AND summary != ''
             ORDER BY review_receipt_id COLLATE BINARY ASC
             LIMIT 1
            """,
            parameters,
        ).fetchone()
        if not_required is None:
            raise completion_history_inconsistent()
        kind = "not_required"
        receipt_ids = (str(not_required["review_receipt_id"]),)

    basis = CompletionGateBasis(
        version=1,
        kind=kind,
        required_independent_passes=required,
        qualifying_independent_passes=qualifying,
        changes_requested_count=changes_requested,
        open_high_count=open_high,
        open_medium_count=open_medium,
        fresh_review_required_count=fresh_review_required,
        qualifying_receipt_ids=receipt_ids,
    )
    _validate_completion_gate_basis(basis, review_tier=tier)
    return basis


def select_completion_gate_basis_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> CompletionGateBasis:
    """Select one deterministic current review basis inside a writer."""

    task = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if task is None:
        raise completion_history_inconsistent()
    return _select_completion_gate_basis_for_projection_locked(
        connection,
        project_id=project_id,
        task_id=task_id,
        task_projection=dict(task),
    )


def _validate_completion_projection_relationship(cycle: CompletionCycle) -> None:
    if not cycle.review_target_kind:
        return
    kind = cycle.completion_evidence_kind
    revision = cycle.completion_evidence_revision
    if cycle.review_target_kind == "git_commit":
        valid = kind == "git_commit" and revision == cycle.review_target_value
    elif cycle.review_target_kind == "git_snapshot":
        valid = kind == "git_commit"
    elif cycle.review_target_kind == "external_revision":
        valid = (
            kind == "external_revision"
            and revision == cycle.review_target_value
        )
    else:
        valid = kind == "commit_not_required"
    if not valid:
        raise completion_history_inconsistent()


def _require_completion_cycle_writer(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction or int(
        connection.execute("PRAGMA query_only").fetchone()[0]
    ):
        raise StorageError(
            "internal_error",
            "completion cycle insertion requires an active writer transaction",
        )


def _next_completion_cycle_ordinal_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> int:
    ordinal_row = connection.execute(
        """
        SELECT MAX(saved_cycle_ordinal)
          FROM task_completion_cycles
         WHERE project_id = ? AND task_id = ?
        """,
        (project_id, task_id),
    ).fetchone()
    previous_ordinal = (
        _completion_int(ordinal_row[0], minimum=1)
        if ordinal_row is not None and ordinal_row[0] is not None
        else 0
    )
    if previous_ordinal == SQLITE_INT64_MAX:
        raise completion_history_inconsistent()
    return previous_ordinal + 1


def _legacy_completion_cycle_from_task_projection(
    task_projection: dict[str, Any],
    *,
    project_id: str,
    task_id: str,
    ordinal: int,
    recorded_at: str,
    completion_cycle_id: str | None = None,
) -> CompletionCycle:
    if str(task_projection.get("status", "")) != "done":
        raise completion_history_inconsistent()
    completed_at = task_projection.get("completed_at")
    if completed_at is None:
        raise completion_history_inconsistent()
    basis = CompletionGateBasis(
        version=0,
        kind="unknown",
        required_independent_passes=None,
        qualifying_independent_passes=None,
        changes_requested_count=None,
        open_high_count=None,
        open_medium_count=None,
        fresh_review_required_count=None,
        qualifying_receipt_ids=(),
    )
    cycle = CompletionCycle(
        completion_cycle_id=(
            completion_cycle_id
            or f"tg_completion_cycle_{secrets.token_hex(8)}"
        ),
        project_id=project_id,
        task_id=task_id,
        saved_cycle_ordinal=ordinal,
        origin="legacy_current_done",
        completeness="partial",
        completed_at=str(completed_at),
        recorded_at=recorded_at,
        contract_revision=_completion_int(
            task_projection.get("current_contract_revision")
        ),
        review_tier=_completion_int(
            task_projection.get("review_tier"),
            maximum=2,
        ),
        verification_expectation=(
            "specified"
            if str(task_projection.get("verification", "")).strip()
            else "unspecified"
        ),
        verification_attestation=None,
        verification_basis_version=0,
        verification_expectation_digest=None,
        verification_receipt_id=None,
        completion_evidence_kind=str(
            task_projection.get("completion_evidence_kind", "")
        ),
        completion_evidence_revision=str(
            task_projection.get("completion_evidence_revision", "")
        ),
        completion_evidence_reason=str(
            task_projection.get("completion_evidence_reason", "")
        ),
        external_revision_approved=_completion_bool(
            task_projection.get("external_revision_approved")
        ),
        completion_commit_required=_completion_bool(
            task_projection.get("completion_commit_required")
        ),
        completion_commit_hash=str(
            task_projection.get("completion_commit_hash", "")
        ),
        review_target_kind=str(
            task_projection.get("review_target_kind", "")
        ),
        review_target_value=str(
            task_projection.get("review_target_value", "")
        ),
        review_target_base_revision=str(
            task_projection.get("review_target_base_revision", "")
        ),
        review_target_generation=_completion_int(
            task_projection.get("review_target_generation")
        ),
        gate_basis=basis,
    )
    _validate_completion_cycle(cycle)
    return cycle


def _persist_completion_cycle_locked(
    connection: sqlite3.Connection,
    cycle: CompletionCycle,
) -> CompletionCycle:
    _require_completion_cycle_writer(connection)
    _validate_completion_cycle(cycle)
    _validate_cycle_receipts(connection, cycle)
    basis = cycle.gate_basis
    receipt_ids = (*basis.qualifying_receipt_ids, None, None)
    parameters = {
        "completion_cycle_id": cycle.completion_cycle_id,
        "project_id": cycle.project_id,
        "task_id": cycle.task_id,
        "saved_cycle_ordinal": cycle.saved_cycle_ordinal,
        "origin": cycle.origin,
        "completeness": cycle.completeness,
        "completed_at": cycle.completed_at,
        "recorded_at": cycle.recorded_at,
        "contract_revision": cycle.contract_revision,
        "review_tier": cycle.review_tier,
        "verification_expectation": cycle.verification_expectation,
        "verification_attestation": (
            int(cycle.verification_attestation)
            if cycle.verification_attestation is not None
            else None
        ),
        "verification_basis_version": cycle.verification_basis_version,
        "verification_expectation_digest": (
            cycle.verification_expectation_digest
        ),
        "verification_receipt_id": cycle.verification_receipt_id,
        "verification_subject_basis_version": (
            cycle.verification_subject_basis_version
        ),
        "subject_authority_snapshot_id": cycle.subject_authority_snapshot_id,
        "subject_verification_criterion_id": (
            cycle.subject_verification_criterion_id
        ),
        "evidence_basis_version": cycle.evidence_basis_version,
        "completion_evidence_bundle_id": (
            cycle.completion_evidence_bundle_id
        ),
        "verification_basis_kind": cycle.verification_basis_kind,
        "verification_runner_observation_id": (
            cycle.verification_runner_observation_id
        ),
        "completion_evidence_kind": cycle.completion_evidence_kind,
        "completion_evidence_revision": cycle.completion_evidence_revision,
        "completion_evidence_reason": cycle.completion_evidence_reason,
        "external_revision_approved": int(
            cycle.external_revision_approved
        ),
        "completion_commit_required": int(
            cycle.completion_commit_required
        ),
        "completion_commit_hash": cycle.completion_commit_hash,
        "review_target_kind": cycle.review_target_kind,
        "review_target_value": cycle.review_target_value,
        "review_target_base_revision": cycle.review_target_base_revision,
        "review_target_generation": cycle.review_target_generation,
        "gate_basis_version": basis.version,
        "review_basis_kind": basis.kind,
        "required_independent_passes": (
            basis.required_independent_passes
        ),
        "qualifying_independent_passes": (
            basis.qualifying_independent_passes
        ),
        "changes_requested_count": basis.changes_requested_count,
        "open_high_count": basis.open_high_count,
        "open_medium_count": basis.open_medium_count,
        "fresh_review_required_count": (
            basis.fresh_review_required_count
        ),
        "qualifying_receipt_id_1": receipt_ids[0],
        "qualifying_receipt_id_2": receipt_ids[1],
    }
    has_verification_basis = column_exists(
        connection,
        "task_completion_cycles",
        "verification_basis_version",
    )
    if not has_verification_basis and (
        cycle.verification_basis_version != 0
        or cycle.verification_expectation_digest is not None
        or cycle.verification_receipt_id is not None
    ):
        raise completion_history_inconsistent()
    verification_columns = (
        ", verification_basis_version, verification_expectation_digest, "
        "verification_receipt_id"
        if has_verification_basis
        else ""
    )
    verification_values = (
        ", :verification_basis_version, :verification_expectation_digest, "
        ":verification_receipt_id"
        if has_verification_basis
        else ""
    )
    has_subject_basis = column_exists(
        connection,
        "task_completion_cycles",
        "verification_subject_basis_version",
    )
    if not has_subject_basis and (
        cycle.verification_subject_basis_version != 0
        or cycle.subject_authority_snapshot_id is not None
        or cycle.subject_verification_criterion_id is not None
    ):
        raise completion_history_inconsistent()
    subject_columns = (
        ", verification_subject_basis_version, subject_authority_snapshot_id, "
        "subject_verification_criterion_id"
        if has_subject_basis
        else ""
    )
    subject_values = (
        ", :verification_subject_basis_version, :subject_authority_snapshot_id, "
        ":subject_verification_criterion_id"
        if has_subject_basis
        else ""
    )
    has_evidence_basis = column_exists(
        connection,
        "task_completion_cycles",
        "evidence_basis_version",
    )
    if not has_evidence_basis and (
        cycle.evidence_basis_version != 0
        or cycle.completion_evidence_bundle_id is not None
    ):
        raise completion_history_inconsistent()
    evidence_columns = (
        ", evidence_basis_version, completion_evidence_bundle_id"
        if has_evidence_basis
        else ""
    )
    evidence_values = (
        ", :evidence_basis_version, :completion_evidence_bundle_id"
        if has_evidence_basis
        else ""
    )
    has_runner_basis = column_exists(
        connection,
        "task_completion_cycles",
        "verification_basis_kind",
    )
    if not has_runner_basis and (
        cycle.verification_basis_kind is not None
        or cycle.verification_runner_observation_id is not None
    ):
        raise completion_history_inconsistent()
    runner_columns = (
        ", verification_basis_kind, verification_runner_observation_id"
        if has_runner_basis
        else ""
    )
    runner_values = (
        ", :verification_basis_kind, :verification_runner_observation_id"
        if has_runner_basis
        else ""
    )
    try:
        connection.execute(
            f"""
            INSERT INTO task_completion_cycles(
              completion_cycle_id, project_id, task_id, saved_cycle_ordinal,
              origin, completeness, completed_at, recorded_at,
              contract_revision, review_tier, verification_expectation,
              verification_attestation, completion_evidence_kind,
              completion_evidence_revision, completion_evidence_reason,
              external_revision_approved, completion_commit_required,
              completion_commit_hash, review_target_kind, review_target_value,
              review_target_base_revision, review_target_generation,
              gate_basis_version, review_basis_kind,
              required_independent_passes, qualifying_independent_passes,
              changes_requested_count, open_high_count, open_medium_count,
              fresh_review_required_count, qualifying_receipt_id_1,
              qualifying_receipt_id_2{verification_columns}{subject_columns}
              {evidence_columns}{runner_columns}
            ) VALUES (
              :completion_cycle_id, :project_id, :task_id,
              :saved_cycle_ordinal, :origin, :completeness, :completed_at,
              :recorded_at, :contract_revision, :review_tier,
              :verification_expectation, :verification_attestation,
              :completion_evidence_kind, :completion_evidence_revision,
              :completion_evidence_reason, :external_revision_approved,
              :completion_commit_required, :completion_commit_hash,
              :review_target_kind, :review_target_value,
              :review_target_base_revision, :review_target_generation,
              :gate_basis_version, :review_basis_kind,
              :required_independent_passes, :qualifying_independent_passes,
              :changes_requested_count, :open_high_count, :open_medium_count,
              :fresh_review_required_count, :qualifying_receipt_id_1,
              :qualifying_receipt_id_2{verification_values}{subject_values}
              {evidence_values}{runner_values}
            )
            """,
            parameters,
        )
    except sqlite3.IntegrityError as exc:
        raise completion_history_inconsistent() from exc
    stored = connection.execute(
        "SELECT * FROM task_completion_cycles WHERE completion_cycle_id = ?",
        (cycle.completion_cycle_id,),
    ).fetchone()
    if stored is None:
        raise completion_history_inconsistent()
    persisted = _cycle_from_row(stored)
    _validate_cycle_receipts(connection, persisted)
    if persisted != cycle:
        raise completion_history_inconsistent()
    if has_evidence_basis:
        _advance_evidence_source_generation_locked(
            connection,
            project_id=cycle.project_id,
        )
        _validate_completion_evidence_bundle_rows(connection)
    return persisted


def insert_completion_cycle_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    recorded_at: str,
) -> CompletionCycle:
    """Archive the locked current-done projection without caller-owned content."""

    _require_completion_cycle_writer(connection)
    current = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if current is None:
        raise completion_history_inconsistent()
    cycle = _legacy_completion_cycle_from_task_projection(
        dict(current),
        project_id=project_id,
        task_id=task_id,
        ordinal=_next_completion_cycle_ordinal_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
        ),
        recorded_at=recorded_at,
    )
    return _persist_completion_cycle_locked(connection, cycle)


def _require_completion_capture_activation_locked(
    connection: sqlite3.Connection,
) -> None:
    if (
        current_schema_version(connection) != SCHEMA_VERSION
        or missing_migration_versions(connection, SCHEMA_VERSION)
    ):
        raise StorageError(
            "migration_required",
            f"completion capture requires schema version {SCHEMA_VERSION}",
        )
    if required_schema_objects_missing(
        connection,
        schema_version=SCHEMA_VERSION,
    ):
        raise completion_history_inconsistent()
    _validate_completion_history_structure(connection)


def allocate_native_completion_identity_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> NativeCompletionIdentity:
    """Allocate one unpersisted native cycle/Bundle identity under a writer."""

    _require_completion_cycle_writer(connection)
    _require_completion_capture_activation_locked(connection)
    return NativeCompletionIdentity(
        completion_cycle_id=f"tg_completion_cycle_{secrets.token_hex(8)}",
        saved_cycle_ordinal=_next_completion_cycle_ordinal_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
        ),
        completion_evidence_bundle_id=(
            f"tg_completion_evidence_bundle_{secrets.token_hex(8)}"
        ),
    )


def prepare_native_completion_cycle_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    task_projection: dict[str, Any],
    recorded_at: str,
    verification_expectation_digest: str,
    verification_receipt_id: str | None,
    verification_subject_basis_version: int = 0,
    subject_authority_snapshot_id: str | None = None,
    subject_verification_criterion_id: str | None = None,
    verification_basis_kind: str | None = None,
    verification_runner_observation_id: str | None = None,
    completion_identity: NativeCompletionIdentity | None = None,
) -> CompletionCycle:
    """Prepare but do not persist one exact native completion cycle."""

    _require_completion_cycle_writer(connection)
    _require_completion_capture_activation_locked(connection)
    if completion_identity is None:
        raise completion_history_inconsistent()
    if (
        COMPLETION_CYCLE_ID_PATTERN.fullmatch(
            completion_identity.completion_cycle_id
        )
        is None
        or type(completion_identity.saved_cycle_ordinal) is not int
        or completion_identity.saved_cycle_ordinal < 1
        or COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(
            completion_identity.completion_evidence_bundle_id
        )
        is None
    ):
        raise completion_history_inconsistent()
    locked = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if locked is None:
        raise completion_history_inconsistent()
    locked_task = dict(locked)
    proposed = dict(task_projection)
    if (
        str(locked_task.get("status", "")) == "done"
        or str(proposed.get("status", "")) != "done"
        or str(proposed.get("project_id", "")) != project_id
        or str(proposed.get("task_id", "")) != task_id
    ):
        raise completion_history_inconsistent()
    protected_fields = (
        "current_contract_revision",
        "review_target_kind",
        "review_target_value",
        "review_target_base_revision",
        "review_target_generation",
        "review_target_capture_version",
        "review_target_authority_snapshot_id",
        "review_target_acceptance_criterion_id",
        "review_target_verification_criterion_id",
        "review_target_artifact_manifest_id",
        "review_target_runner_basis_version",
        "verification",
    )
    if any(
        proposed.get(field_name) != locked_task.get(field_name)
        for field_name in protected_fields
    ):
        raise completion_history_inconsistent()

    basis = _select_completion_gate_basis_for_projection_locked(
        connection,
        project_id=project_id,
        task_id=task_id,
        task_projection=proposed,
    )
    completed_at = proposed.get("completed_at")
    if completed_at is None:
        raise completion_history_inconsistent()
    exact_verification = proposed.get("verification")
    marker = locked_task.get("review_target_runner_basis_version", 0)
    has_runner_basis = column_exists(
        connection,
        "task_completion_cycles",
        "verification_basis_kind",
    )
    selected_basis_kind = (
        verification_basis_kind
        if verification_basis_kind is not None
        else (
            "caller_attestation"
            if str(exact_verification or "").strip()
            else "not_required"
        )
        if has_runner_basis
        else None
    )
    expected_marker_zero_kind = (
        (
            "caller_attestation"
            if exact_verification.strip()
            else "not_required"
        )
        if has_runner_basis
        else None
    )
    exact_subject = (
        1,
        (
            locked_task.get("review_target_authority_snapshot_id")
            if str(exact_verification or "").strip()
            else None
        ),
        (
            locked_task.get("review_target_verification_criterion_id")
            if str(exact_verification or "").strip()
            else None
        ),
    )
    if (
        not isinstance(exact_verification, str)
        or not isinstance(verification_expectation_digest, str)
        or LOWER_HEX_64_PATTERN.fullmatch(
            verification_expectation_digest
        )
        is None
        or verification_expectation_digest
        != _verification_expectation_digest(exact_verification)
        or marker not in {0, 2}
        or (
            marker == 0
            and (
                selected_basis_kind != expected_marker_zero_kind
                or verification_runner_observation_id is not None
            )
        )
        or (
            marker == 2
            and (
                not has_runner_basis
                or not exact_verification.strip()
                or selected_basis_kind
                not in {"caller_attestation", "runner_observation"}
            )
        )
        or (
            not has_runner_basis
            and exact_verification.strip()
            and (
                verification_receipt_id is None
                or VERIFICATION_RECEIPT_ID_PATTERN.fullmatch(
                    verification_receipt_id
                )
                is None
            )
        )
        or (
            not has_runner_basis
            and not exact_verification.strip()
            and verification_receipt_id is not None
        )
        or (
            selected_basis_kind == "caller_attestation"
            and (
                verification_receipt_id is None
                or VERIFICATION_RECEIPT_ID_PATTERN.fullmatch(
                    verification_receipt_id
                )
                is None
                or verification_runner_observation_id is not None
            )
        )
        or (
            selected_basis_kind == "not_required"
            and (
                verification_receipt_id is not None
                or verification_runner_observation_id is not None
            )
        )
        or (
            selected_basis_kind == "runner_observation"
            and (
                verification_receipt_id is not None
                or type(verification_runner_observation_id) is not str
                or VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN.fullmatch(
                    verification_runner_observation_id
                )
                is None
            )
        )
        or locked_task.get("review_target_capture_version") != 1
        or locked_task.get("review_target_authority_snapshot_id") is None
        or locked_task.get("review_target_artifact_manifest_id") is None
        or (
            bool(exact_verification.strip())
            != (
                locked_task.get("review_target_verification_criterion_id")
                is not None
            )
        )
        or (
            verification_subject_basis_version,
            subject_authority_snapshot_id,
            subject_verification_criterion_id,
        )
        != exact_subject
    ):
        raise completion_history_inconsistent()
    cycle = CompletionCycle(
        completion_cycle_id=completion_identity.completion_cycle_id,
        project_id=project_id,
        task_id=task_id,
        saved_cycle_ordinal=completion_identity.saved_cycle_ordinal,
        origin="native_done",
        completeness="complete",
        completed_at=str(completed_at),
        recorded_at=recorded_at,
        contract_revision=_completion_int(
            proposed.get("current_contract_revision")
        ),
        review_tier=_completion_int(
            proposed.get("review_tier"),
            maximum=2,
        ),
        verification_expectation=(
            "specified"
            if str(proposed.get("verification", "")).strip()
            else "unspecified"
        ),
        verification_attestation=True,
        verification_basis_version=1,
        verification_expectation_digest=(
            verification_expectation_digest
        ),
        verification_receipt_id=verification_receipt_id,
        verification_subject_basis_version=(
            verification_subject_basis_version
        ),
        subject_authority_snapshot_id=subject_authority_snapshot_id,
        subject_verification_criterion_id=(
            subject_verification_criterion_id
        ),
        evidence_basis_version=1,
        completion_evidence_bundle_id=(
            completion_identity.completion_evidence_bundle_id
        ),
        verification_basis_kind=selected_basis_kind,
        verification_runner_observation_id=(
            verification_runner_observation_id
        ),
        completion_evidence_kind=str(
            proposed.get("completion_evidence_kind", "")
        ),
        completion_evidence_revision=str(
            proposed.get("completion_evidence_revision", "")
        ),
        completion_evidence_reason=str(
            proposed.get("completion_evidence_reason", "")
        ),
        external_revision_approved=_completion_bool(
            proposed.get("external_revision_approved")
        ),
        completion_commit_required=_completion_bool(
            proposed.get("completion_commit_required")
        ),
        completion_commit_hash=str(
            proposed.get("completion_commit_hash", "")
        ),
        review_target_kind=str(
            proposed.get("review_target_kind", "")
        ),
        review_target_value=str(
            proposed.get("review_target_value", "")
        ),
        review_target_base_revision=str(
            proposed.get("review_target_base_revision", "")
        ),
        review_target_generation=_completion_int(
            proposed.get("review_target_generation")
        ),
        gate_basis=basis,
    )
    if cycle.saved_cycle_ordinal != _next_completion_cycle_ordinal_locked(
        connection,
        project_id=project_id,
        task_id=task_id,
    ):
        raise completion_history_inconsistent()
    return cycle


def insert_native_completion_cycle_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    task_projection: dict[str, Any],
    recorded_at: str,
    verification_expectation_digest: str,
    verification_receipt_id: str | None,
    verification_subject_basis_version: int = 0,
    subject_authority_snapshot_id: str | None = None,
    subject_verification_criterion_id: str | None = None,
    verification_basis_kind: str | None = None,
    verification_runner_observation_id: str | None = None,
    completion_identity: NativeCompletionIdentity | None = None,
    completion_bundle: PreparedCompletionEvidenceBundle | None = None,
    prepared_cycle: CompletionCycle | None = None,
) -> CompletionCycle:
    """Validate and atomically persist one prepared Bundle/native cycle."""

    if completion_identity is None or completion_bundle is None:
        raise completion_history_inconsistent()
    if prepared_cycle is None:
        cycle = prepare_native_completion_cycle_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
            task_projection=task_projection,
            recorded_at=recorded_at,
            verification_expectation_digest=verification_expectation_digest,
            verification_receipt_id=verification_receipt_id,
            verification_subject_basis_version=(
                verification_subject_basis_version
            ),
            subject_authority_snapshot_id=subject_authority_snapshot_id,
            subject_verification_criterion_id=(
                subject_verification_criterion_id
            ),
            verification_basis_kind=verification_basis_kind,
            verification_runner_observation_id=(
                verification_runner_observation_id
            ),
            completion_identity=completion_identity,
        )
    else:
        _require_completion_cycle_writer(connection)
        _require_completion_capture_activation_locked(connection)
        cycle = prepared_cycle
        _validate_completion_cycle(cycle)
        expected_gate_basis = _select_completion_gate_basis_for_projection_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
            task_projection=task_projection,
        )
        expected_completed_at = task_projection.get("completed_at")
        expected_verification = task_projection.get("verification")
        if (
            type(expected_completed_at) is not str
            or type(expected_verification) is not str
            or cycle.project_id != project_id
            or cycle.task_id != task_id
            or cycle.completion_cycle_id
            != completion_identity.completion_cycle_id
            or cycle.saved_cycle_ordinal
            != completion_identity.saved_cycle_ordinal
            or cycle.completion_evidence_bundle_id
            != completion_identity.completion_evidence_bundle_id
            or cycle.origin != "native_done"
            or cycle.completeness != "complete"
            or cycle.completed_at != expected_completed_at
            or cycle.recorded_at != recorded_at
            or cycle.contract_revision
            != _completion_int(task_projection.get("current_contract_revision"))
            or cycle.review_tier
            != _completion_int(task_projection.get("review_tier"), maximum=2)
            or cycle.verification_expectation
            != ("specified" if expected_verification.strip() else "unspecified")
            or cycle.verification_attestation is not True
            or cycle.verification_basis_version != 1
            or cycle.verification_expectation_digest
            != verification_expectation_digest
            or cycle.verification_receipt_id != verification_receipt_id
            or cycle.verification_subject_basis_version
            != verification_subject_basis_version
            or cycle.subject_authority_snapshot_id
            != subject_authority_snapshot_id
            or cycle.subject_verification_criterion_id
            != subject_verification_criterion_id
            or cycle.verification_basis_kind != verification_basis_kind
            or cycle.verification_runner_observation_id
            != verification_runner_observation_id
            or cycle.evidence_basis_version != 1
            or cycle.completion_evidence_kind
            != str(task_projection.get("completion_evidence_kind", ""))
            or cycle.completion_evidence_revision
            != str(task_projection.get("completion_evidence_revision", ""))
            or cycle.completion_evidence_reason
            != str(task_projection.get("completion_evidence_reason", ""))
            or cycle.external_revision_approved
            != _completion_bool(
                task_projection.get("external_revision_approved")
            )
            or cycle.completion_commit_required
            != _completion_bool(
                task_projection.get("completion_commit_required")
            )
            or cycle.completion_commit_hash
            != str(task_projection.get("completion_commit_hash", ""))
            or cycle.review_target_kind
            != str(task_projection.get("review_target_kind", ""))
            or cycle.review_target_value
            != str(task_projection.get("review_target_value", ""))
            or cycle.review_target_base_revision
            != str(task_projection.get("review_target_base_revision", ""))
            or cycle.review_target_generation
            != _completion_int(task_projection.get("review_target_generation"))
            or cycle.gate_basis != expected_gate_basis
            or cycle.saved_cycle_ordinal
            != _next_completion_cycle_ordinal_locked(
                connection,
                project_id=project_id,
                task_id=task_id,
            )
        ):
            raise completion_history_inconsistent()
    persist_completion_evidence_bundle_locked(
        connection,
        bundle=completion_bundle,
        expected_cycle=cycle,
    )
    persisted_cycle = _persist_completion_cycle_locked(connection, cycle)
    _validate_projection_bundle_record(
        _prepared_projection_bundle_record_locked(
            connection,
            bundle=completion_bundle,
            expected_cycle=persisted_cycle,
        )
    )
    return persisted_cycle


def _completion_projection_signature(
    cycle: CompletionCycle,
) -> tuple[object, ...]:
    return (
        cycle.completed_at,
        cycle.contract_revision,
        cycle.review_tier,
        cycle.verification_expectation,
        cycle.completion_evidence_kind,
        cycle.completion_evidence_revision,
        cycle.completion_evidence_reason,
        cycle.external_revision_approved,
        cycle.completion_commit_required,
        cycle.completion_commit_hash,
        cycle.review_target_kind,
        cycle.review_target_value,
        cycle.review_target_base_revision,
        cycle.review_target_generation,
    )


def _match_current_done_completion_cycle_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    validate_structure: bool,
) -> tuple[str, CompletionCycle | None, bool, bool]:
    _require_completion_cycle_writer(connection)
    if validate_structure:
        version = current_schema_version(connection)
        if (
            version not in {15, 16, 17, 18, 19, 20, 21}
            or missing_migration_versions(connection, version)
            or schema_objects_inconsistent_with_version(connection, version)
        ):
            raise completion_history_inconsistent()
        _validate_completion_history_structure(connection)
    current = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if current is None:
        raise completion_history_inconsistent()
    task_projection = dict(current)
    coverage = str(
        task_projection.get("completion_history_coverage", "")
    )
    if coverage not in {"legacy_unknown", "complete"}:
        raise completion_history_inconsistent()

    latest = read_latest_completion_cycle(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    completed_at = task_projection.get("completed_at")
    projection = _legacy_completion_cycle_from_task_projection(
        task_projection,
        project_id=project_id,
        task_id=task_id,
        ordinal=(latest.saved_cycle_ordinal if latest is not None else 1),
        recorded_at=(
            latest.recorded_at
            if latest is not None
            else str(completed_at)
        ),
        completion_cycle_id=(
            latest.completion_cycle_id
            if latest is not None
            else "tg_completion_cycle_0000000000000000"
        ),
    )
    projection_matches = (
        latest is not None
        and _completion_projection_signature(projection)
        == _completion_projection_signature(latest)
    )
    if latest is not None and latest.verification_basis_version == 1:
        if not _completion_cycle_matches_exact_verification(
            latest,
            task_projection.get("verification"),
        ):
            projection_matches = False
    reopen_linked = False
    if latest is not None:
        completion_event_types = {"task_updated", "review_tier_changed"}
        completion_links = 0
        reopen_links = 0
        for row in connection.execute(
            """
            SELECT project_id, task_id, event_type
              FROM task_events
             WHERE completion_cycle_id = ?
            """,
            (latest.completion_cycle_id,),
        ).fetchall():
            event_type = str(row["event_type"])
            if (
                str(row["project_id"]) != project_id
                or str(row["task_id"]) != task_id
            ):
                raise completion_history_inconsistent()
            if event_type in completion_event_types:
                if latest.origin != "native_done":
                    raise completion_history_inconsistent()
                completion_links += 1
            elif event_type == "task_reopened":
                reopen_links += 1
            else:
                raise completion_history_inconsistent()
        if (
            completion_links > 1
            or reopen_links > 1
            or (
                latest.origin == "native_done"
                and completion_links != 1
            )
            or (
                latest.origin == "legacy_current_done"
                and completion_links != 0
            )
        ):
            raise completion_history_inconsistent()
        reopen_linked = reopen_links == 1
    return coverage, latest, projection_matches, reopen_linked


def match_current_done_completion_cycle_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> tuple[str, CompletionCycle | None, bool, bool]:
    """Compare the locked done projection with its latest saved cycle."""

    return _match_current_done_completion_cycle_locked(
        connection,
        project_id=project_id,
        task_id=task_id,
        validate_structure=True,
    )


def read_latest_completion_cycle(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> CompletionCycle | None:
    row = connection.execute(
        """
        SELECT *
          FROM task_completion_cycles
         WHERE project_id = ? AND task_id = ?
         ORDER BY saved_cycle_ordinal DESC
         LIMIT 1
        """,
        (project_id, task_id),
    ).fetchone()
    if row is None:
        return None
    cycle = _cycle_from_row(row)
    _validate_cycle_receipts(connection, cycle)
    _validate_selected_completion_cycle_evidence(
        connection,
        project_id=project_id,
        cycles=(cycle,),
    )
    return cycle


def _completion_history_metadata(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_ids: tuple[str, ...],
) -> dict[str, tuple[int, bool]]:
    if not task_ids:
        return {}
    placeholders = ", ".join("?" for _ in task_ids)
    task_rows = connection.execute(
        f"""
        SELECT task_id, completion_history_coverage
          FROM tasks
         WHERE project_id = ?
           AND task_id IN ({placeholders})
        """,
        (project_id, *task_ids),
    ).fetchall()
    if len(task_rows) != len(task_ids):
        raise completion_history_inconsistent()
    metadata = {
        str(row["task_id"]): [
            0,
            str(row["completion_history_coverage"]) != "complete",
        ]
        for row in task_rows
    }
    cycle_rows = connection.execute(
        f"""
        SELECT task_id, COUNT(*) AS total,
               MAX(CASE WHEN completeness = 'partial' THEN 1 ELSE 0 END)
                 AS has_partial
          FROM task_completion_cycles
         WHERE project_id = ?
           AND task_id IN ({placeholders})
         GROUP BY task_id
        """,
        (project_id, *task_ids),
    ).fetchall()
    for row in cycle_rows:
        item = metadata[str(row["task_id"])]
        item[0] = _completion_int(row["total"])
        item[1] = bool(item[1]) or bool(
            _completion_int(row["has_partial"], maximum=1)
        )
    reopen_rows = connection.execute(
        f"""
        SELECT task_id, COUNT(*) AS total
          FROM task_events
         WHERE project_id = ?
           AND task_id IN ({placeholders})
           AND event_type = 'task_reopened'
           AND completion_cycle_id IS NULL
         GROUP BY task_id
        """,
        (project_id, *task_ids),
    ).fetchall()
    for row in reopen_rows:
        if _completion_int(row["total"]):
            metadata[str(row["task_id"])][1] = True
    return {
        task_id: (int(values[0]), bool(values[1]))
        for task_id, values in metadata.items()
    }


def read_completion_history(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    limit: int = 10,
) -> CompletionHistory:
    if type(limit) is not int or not 1 <= limit <= 10:
        raise StorageError(
            "internal_error",
            "completion history limit must be between 1 and 10",
        )
    metadata = _completion_history_metadata(
        connection,
        project_id=project_id,
        task_ids=(task_id,),
    )
    total, incomplete = metadata[task_id]
    rows = connection.execute(
        """
        SELECT *
          FROM task_completion_cycles
         WHERE project_id = ? AND task_id = ?
         ORDER BY saved_cycle_ordinal DESC
         LIMIT ?
        """,
        (project_id, task_id, limit),
    ).fetchall()
    cycles = tuple(_cycle_from_row(row) for row in rows)
    for cycle in cycles:
        _validate_cycle_receipts(connection, cycle)
    _validate_selected_completion_cycle_evidence(
        connection,
        project_id=project_id,
        cycles=cycles,
    )
    _validate_selected_schema21_completion_bundle_history(connection, cycles=cycles)
    return CompletionHistory(
        total=total,
        legacy_history_incomplete=incomplete,
        cycles=cycles,
    )


def _selected_history_runner_generations(
    connection: sqlite3.Connection,
    *,
    cycles: tuple[CompletionCycle, ...],
) -> dict[tuple[str, str, int], dict[str, Any]]:
    """Validate only historical Runner generations that a returned cycle uses."""

    generations: dict[tuple[str, str, int], dict[str, Any]] = {}
    for cycle in cycles:
        if cycle.evidence_basis_version != 1:
            continue
        key = (
            cycle.project_id,
            cycle.task_id,
            cycle.review_target_generation,
        )
        resolution_rows = connection.execute(
            "SELECT gate_eligibility_version "
            "FROM verification_runner_resolutions "
            "WHERE project_id = ? AND task_id = ? AND target_generation = ? "
            "ORDER BY verification_runner_resolution_id LIMIT 2",
            key,
        ).fetchall()
        if len(resolution_rows) > 1:
            raise evidence_ledger_inconsistent()
        eligibility = None
        if resolution_rows:
            eligibility = resolution_rows[0]["gate_eligibility_version"]
            if type(eligibility) is not int or eligibility not in {0, 1}:
                raise evidence_ledger_inconsistent()
        basis_kind = cycle.verification_basis_kind
        runner_backed = basis_kind == "runner_observation" or (
            basis_kind == "caller_attestation" and eligibility == 1
        )
        # Preserved source19/v1 has no Runner tag. The subsequent Bundle
        # validator still checks its original source/version and full basis.
        if basis_kind in {None, "not_required"}:
            if eligibility == 1:
                raise evidence_ledger_inconsistent()
            continue
        if basis_kind not in {"caller_attestation", "runner_observation"}:
            raise evidence_ledger_inconsistent()
        if not runner_backed:
            continue
        _, selected = _validated_verification_runner_graph(
            connection,
            selected_generation=key,
            selected_history_cycle=cycle,
        )
        if set(selected) != {key}:
            raise evidence_ledger_inconsistent()
        existing = generations.get(key)
        if existing is not None and existing != selected[key]:
            raise evidence_ledger_inconsistent()
        generations[key] = selected[key]
    return generations


def _validate_selected_completion_bundle_history(
    connection: sqlite3.Connection,
    *,
    cycles: tuple[CompletionCycle, ...],
    runner_generations: dict[tuple[str, str, int], dict[str, Any]],
    container_schema_version: int,
) -> None:
    """Validate and replay only Bundles owned by the returned history cycles."""

    native_cycles = tuple(
        cycle for cycle in cycles if cycle.evidence_basis_version == 1
    )
    if not native_cycles:
        return
    if (
        len(cycles) > 10
        or len({cycle.project_id for cycle in native_cycles}) != 1
        or len({cycle.task_id for cycle in native_cycles}) != 1
        or any(
            cycle.origin != "native_done"
            or type(cycle.completion_evidence_bundle_id) is not str
            or COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(
                cycle.completion_evidence_bundle_id
            )
            is None
            for cycle in native_cycles
        )
    ):
        raise evidence_ledger_inconsistent()
    cycle_ids = {cycle.completion_cycle_id for cycle in native_cycles}
    bundle_ids = {
        cycle.completion_evidence_bundle_id for cycle in native_cycles
    }
    if len(cycle_ids) != len(native_cycles) or len(bundle_ids) != len(native_cycles):
        raise evidence_ledger_inconsistent()

    bundle_rows = _selected_storage_rows_by_ids(
        connection,
        table_name="completion_evidence_bundles",
        id_field="completion_evidence_bundle_id",
        selected_ids=bundle_ids,
    )
    bundles_by_id = {
        str(row["completion_evidence_bundle_id"]): row for row in bundle_rows
    }
    if len(bundles_by_id) != len(bundle_rows):
        raise evidence_ledger_inconsistent()
    cycles_by_bundle = {
        str(cycle.completion_evidence_bundle_id): cycle for cycle in native_cycles
    }
    prepared_by_id: dict[str, PreparedCompletionEvidenceBundle] = {}
    member_groups_by_bundle: dict[
        str,
        dict[str, list[sqlite3.Row | dict[str, Any]]],
    ] = {}
    findings_by_bundle: dict[
        str,
        list[sqlite3.Row | dict[str, Any]],
    ] = {}
    link_rows_by_id: dict[str, sqlite3.Row | dict[str, Any]] = {}
    reference_ids: set[str] = set()
    review_receipt_ids: set[str] = set()
    review_finding_ids: set[str] = set()
    verification_receipt_ids: set[str] = set()
    snapshot_ids: set[str] = set()
    manifest_ids: set[str] = set()
    for bundle_id, cycle in cycles_by_bundle.items():
        row = bundles_by_id.get(bundle_id)
        if row is None or row["completion_cycle_id"] != cycle.completion_cycle_id:
            raise evidence_ledger_inconsistent()
        bundle = read_completion_evidence_bundle(
            connection,
            completion_evidence_bundle_id=bundle_id,
        )
        _validate_prepared_completion_bundle(bundle, expected_cycle=cycle)
        prepared_by_id[bundle_id] = bundle
        grouped: dict[str, list[sqlite3.Row | dict[str, Any]]] = {}
        for member in bundle.members:
            member_row = dict(vars(member))
            grouped.setdefault(member.member_kind, []).append(member_row)
            if member.evidence_reference_id is not None:
                reference_ids.add(member.evidence_reference_id)
        member_groups_by_bundle[bundle_id] = grouped
        findings_by_bundle[bundle_id] = [
            dict(vars(finding)) for finding in bundle.finding_snapshots
        ]
        for link in bundle.criterion_links:
            link_row = dict(vars(link))
            existing = link_rows_by_id.get(link.criterion_evidence_link_id)
            if existing is not None and dict(existing) != link_row:
                raise evidence_ledger_inconsistent()
            link_rows_by_id[link.criterion_evidence_link_id] = link_row
        review_receipt_ids.update(cycle.gate_basis.qualifying_receipt_ids)
        review_finding_ids.update(
            finding.review_finding_id for finding in bundle.finding_snapshots
        )
        if bundle.verification_receipt_id is not None:
            verification_receipt_ids.add(bundle.verification_receipt_id)
        snapshot_ids.add(bundle.authority_snapshot_id)
        manifest_ids.add(bundle.artifact_manifest_id)

    project_id = native_cycles[0].project_id
    task_id = native_cycles[0].task_id
    validate_selected_task_receipt_evidence(
        connection,
        project_id=project_id,
        task_id=task_id,
        review_receipt_ids=review_receipt_ids,
        review_finding_ids=review_finding_ids,
        verification_receipt_ids=verification_receipt_ids,
    )
    authority = _validated_authority_context(
        connection,
        snapshot_ids=snapshot_ids,
    )
    manifests, _ = _validate_artifact_manifest_storage(
        connection,
        snapshots=authority.snapshots,
        links=authority.links,
        manifest_ids=manifest_ids,
    )
    manifest_rows = {
        manifest_id: record.row for manifest_id, record in manifests.items()
    }
    reference_rows = _selected_storage_rows_by_ids(
        connection,
        table_name="evidence_references",
        id_field="evidence_reference_id",
        selected_ids=reference_ids,
    )
    references = {
        str(row["evidence_reference_id"]): row for row in reference_rows
    }
    if len(references) != len(reference_rows):
        raise evidence_ledger_inconsistent()

    for link_id, link in link_rows_by_id.items():
        criterion_id = link["criterion_id"]
        reference_id = link["evidence_reference_id"]
        criterion = authority.criteria.get(str(criterion_id))
        reference = references.get(str(reference_id))
        if (
            CRITERION_EVIDENCE_LINK_ID_PATTERN.fullmatch(link_id) is None
            or type(criterion_id) is not str
            or type(reference_id) is not str
            or type(link["relation"]) is not str
            or link["relation"] not in CRITERION_EVIDENCE_RELATIONS
            or link["relation"] == "derived_analysis"
            or type(link["producer_version"]) is not int
            or link["producer_version"] <= 0
            or type(link["created_at"]) is not str
            or criterion is None
            or reference is None
            or criterion["project_id"] != link["project_id"]
            or criterion["task_id"] != link["task_id"]
            or reference["project_id"] != link["project_id"]
            or reference["task_id"] != link["task_id"]
            or reference["assurance_class"] != link["assurance_class"]
            or reference["producer_class"] != link["producer_class"]
            or reference["producer_version"] != link["producer_version"]
            or not _criterion_link_relation_valid(
                criterion_kind=criterion["criterion_kind"],
                source_kind=reference["source_kind"],
                relation=link["relation"],
            )
        ):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                link["created_at"],
                field="criterion Evidence Link creation time",
            )
        except StorageError as exc:
            raise evidence_ledger_inconsistent() from exc

    verification_rows = _selected_storage_rows_by_ids(
        connection,
        table_name="verification_receipts",
        id_field="verification_receipt_id",
        selected_ids=verification_receipt_ids,
    )
    verification_receipts = {
        str(row["verification_receipt_id"]): row for row in verification_rows
    }
    review_receipts_by_id: dict[str, dict[str, Any]] = {}
    if review_receipt_ids:
        for receipt, provenance in _iter_validated_review_receipts_with_provenance(
            connection,
            review_receipt_ids,
        ):
            review_receipts_by_id[str(receipt["review_receipt_id"])] = {
                "receipt": dict(receipt),
                "provenance": provenance,
            }

    for bundle_id, bundle in prepared_by_id.items():
        row = bundles_by_id[bundle_id]
        cycle = cycles_by_bundle[bundle_id]
        snapshot = authority.snapshots.get(bundle.authority_snapshot_id)
        manifest = manifest_rows.get(bundle.artifact_manifest_id)
        if snapshot is None or manifest is None:
            raise evidence_ledger_inconsistent()
        _validate_one_completion_evidence_bundle_row(
            bundle_id=bundle_id,
            row=row,
            container_schema_version=container_schema_version,
            cycle=cycle,
            snapshot=snapshot,
            owner_links=authority.links.get(bundle.authority_snapshot_id, {}),
            manifest=manifest,
            verification_receipts=verification_receipts,
            member_groups=member_groups_by_bundle[bundle_id],
            links=link_rows_by_id,
            references=references,
            finding_rows=findings_by_bundle[bundle_id],
            runner_generations=runner_generations,
        )
        criteria = tuple(
            dict(authority.criteria[criterion_id])
            for criterion_id in (
                authority.links.get(bundle.authority_snapshot_id, {}).get(
                    "acceptance"
                ),
                authority.links.get(bundle.authority_snapshot_id, {}).get(
                    "verification"
                ),
            )
            if criterion_id is not None
        )
        entry_rows = connection.execute(
            "SELECT * FROM artifact_manifest_entries "
            "WHERE artifact_manifest_id = ? ORDER BY ordinal",
            (bundle.artifact_manifest_id,),
        ).fetchall()
        ordered_references = tuple(
            dict(references[member.evidence_reference_id])
            for member in bundle.members
            if member.member_kind == "evidence_reference"
            and member.evidence_reference_id is not None
        )
        record = _projection_bundle_record_from_validated_rows(
            bundle=bundle,
            cycle=cycle,
            snapshot=snapshot,
            criteria=criteria,
            manifest=manifest,
            entries=tuple(dict(entry) for entry in entry_rows),
            references=ordered_references,
            verification_receipt=(
                _validate_verification_receipt_row(
                    dict(verification_receipts[bundle.verification_receipt_id])
                )
                if bundle.verification_receipt_id is not None
                else None
            ),
            review_receipts_by_id=review_receipts_by_id,
            runner_generation=runner_generations.get(
                (bundle.project_id, bundle.task_id, bundle.target_generation)
            ),
        )
        _validate_projection_bundle_record(record)


def _validate_selected_schema21_completion_bundle_history(
    connection: sqlite3.Connection,
    *,
    cycles: tuple[CompletionCycle, ...],
) -> None:
    """Revalidate selected v21/v22 native Bundle history before replay."""

    container_schema_version = current_schema_version(connection)
    if container_schema_version not in {
        PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
    } or not any(
        cycle.evidence_basis_version == 1 for cycle in cycles
    ):
        return
    try:
        runner_generations = _selected_history_runner_generations(
            connection,
            cycles=cycles,
        )
    except sqlite3.Error as exc:
        error = evidence_ledger_sqlite_error(exc)
        if error.code == "database_busy":
            raise error from exc
        raise _unreadable_project_state() from exc
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc
    try:
        _validate_selected_completion_bundle_history(
            connection,
            cycles=cycles,
            runner_generations=runner_generations,
            container_schema_version=container_schema_version,
        )
    except sqlite3.Error as exc:
        error = evidence_ledger_sqlite_error(exc)
        if error.code == "database_busy":
            raise error from exc
        raise completion_history_inconsistent() from exc
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise completion_history_inconsistent() from exc


def read_completion_histories_for_tasks(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_ids: tuple[str, ...],
    limit: int = 10,
) -> dict[str, CompletionHistory]:
    """Read bounded histories for at most the Viewer's existing 500 Tasks."""

    if (
        type(limit) is not int
        or not 1 <= limit <= 10
        or len(task_ids) > 500
        or len(task_ids) != len(set(task_ids))
        or any(not isinstance(task_id, str) or not task_id for task_id in task_ids)
    ):
        raise StorageError(
            "internal_error",
            "completion history batch request is invalid",
        )
    if not task_ids:
        return {}
    metadata = _completion_history_metadata(
        connection,
        project_id=project_id,
        task_ids=task_ids,
    )
    placeholders = ", ".join("?" for _ in task_ids)
    rows = connection.execute(
        f"""
        SELECT *
          FROM (
            SELECT cycle.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY task_id
                     ORDER BY saved_cycle_ordinal DESC
                   ) AS bounded_row_number
              FROM task_completion_cycles AS cycle
             WHERE project_id = ?
               AND task_id IN ({placeholders})
          )
         WHERE bounded_row_number <= ?
         ORDER BY task_id COLLATE BINARY, saved_cycle_ordinal DESC
        """,
        (project_id, *task_ids, limit),
    ).fetchall()
    grouped: dict[str, list[CompletionCycle]] = {
        task_id: [] for task_id in task_ids
    }
    cycles = tuple(_cycle_from_row(row) for row in rows)
    _validate_cycle_receipts_batch(
        connection,
        project_id=project_id,
        cycles=cycles,
    )
    _validate_selected_completion_cycle_evidence(
        connection,
        project_id=project_id,
        cycles=cycles,
    )
    for cycle in cycles:
        grouped[cycle.task_id].append(cycle)
    return {
        task_id: CompletionHistory(
            total=metadata[task_id][0],
            legacy_history_incomplete=metadata[task_id][1],
            cycles=tuple(grouped[task_id]),
        )
        for task_id in task_ids
    }


def apply_completion_cycle_history_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Add schema-v15 immutable completion history and current-done backfill."""

    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "completion-history migration requires no active transaction",
        )
    version = current_schema_version(connection)
    if version >= 15:
        connection.execute("BEGIN")
        try:
            if (
                version != 15
                or missing_migration_versions(connection, version)
                or required_schema_objects_missing(
                    connection,
                    schema_version=15,
                )
            ):
                raise StorageError(
                    "migration_required",
                    "completion-history migration is incomplete",
                )
            validate_completion_cycle_storage(connection)
            connection.commit()
        except StorageError as exc:
            connection.rollback()
            if exc.code == "completion_history_inconsistent":
                raise _unreadable_project_state() from exc
            raise
        except Exception:
            connection.rollback()
            raise
        return
    if (
        version != 14
        or missing_migration_versions(connection, 14)
        or schema_objects_inconsistent_with_version(connection, 14)
    ):
        raise StorageError(
            "migration_required",
            "completion-history migration requires complete schema version 14",
        )
    connection.execute("PRAGMA foreign_keys = ON")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise StorageError(
            "internal_error",
            "completion-history migration requires foreign key enforcement",
        )

    before = _migration_preservation_snapshot(connection)
    column_basis = {
        table_name: snapshot[0]
        for table_name, snapshot in before.items()
    }
    migration_time = utc_now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        statements = completion_cycle_history_schema_statements()
        connection.execute(statements[0])
        if fail_stage == "after_columns":
            raise StorageError(
                "internal_error",
                "injected completion-history migration failure",
            )
        for statement in statements[1:5]:
            connection.execute(statement)
        if fail_stage == "after_cycle_schema":
            raise StorageError(
                "internal_error",
                "injected completion-history migration failure",
            )
        for statement in statements[5:7]:
            connection.execute(statement)
        if fail_stage == "after_event_link":
            raise StorageError(
                "internal_error",
                "injected completion-history migration failure",
            )
        for statement in statements[7:]:
            connection.execute(statement)
        if fail_stage == "after_schema":
            raise StorageError(
                "internal_error",
                "injected completion-history migration failure",
            )

        done_rows = connection.execute(
            """
            SELECT *
              FROM tasks
             WHERE status = 'done'
             ORDER BY task_id COLLATE BINARY ASC
            """
        ).fetchall()
        for row in done_rows:
            task = dict(row)
            completed_at = task.get("completed_at")
            if completed_at is None:
                raise completion_history_inconsistent()
            try:
                validate_utc_timestamp(
                    str(completed_at),
                    field="legacy completion time",
                )
            except StorageError as exc:
                raise completion_history_inconsistent() from exc
            insert_completion_cycle_locked(
                connection,
                project_id=str(task["project_id"]),
                task_id=str(task["task_id"]),
                recorded_at=migration_time,
            )
        if fail_stage == "after_backfill":
            raise StorageError(
                "internal_error",
                "injected completion-history migration failure",
            )

        task_count = int(
            connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        )
        unknown_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM tasks
                 WHERE completion_history_coverage = 'legacy_unknown'
                """
            ).fetchone()[0]
        )
        done_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'done'"
            ).fetchone()[0]
        )
        cycle_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM task_completion_cycles"
            ).fetchone()[0]
        )
        invalid_cycle_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM task_completion_cycles AS cycle
                  JOIN tasks AS task
                    ON task.project_id = cycle.project_id
                   AND task.task_id = cycle.task_id
                 WHERE task.status != 'done'
                    OR cycle.saved_cycle_ordinal != 1
                    OR cycle.origin != 'legacy_current_done'
                    OR cycle.completeness != 'partial'
                """
            ).fetchone()[0]
        )
        linked_event_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM task_events
                 WHERE completion_cycle_id IS NOT NULL
                """
            ).fetchone()[0]
        )
        after = _migration_preservation_snapshot(
            connection,
            column_basis=column_basis,
        )
        if (
            task_count != unknown_count
            or done_count != cycle_count
            or invalid_cycle_count
            or linked_event_count
            or after != before
        ):
            raise completion_history_inconsistent()

        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (15, 'completion_cycle_history', ?)
            """,
            (migration_time,),
        )
        validate_completion_cycle_storage(connection)
        quick_rows = [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
        if quick_rows != ["ok"]:
            raise completion_history_inconsistent()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise completion_history_inconsistent()
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected completion-history migration failure",
            )
        connection.commit()
    except StorageError as exc:
        connection.rollback()
        if exc.code == "completion_history_inconsistent":
            raise _unreadable_project_state() from exc
        raise
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise _unreadable_project_state() from exc
    except Exception:
        connection.rollback()
        raise


def apply_completion_cycle_capture_activation_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Atomically reconcile v15 current completions and record marker 16."""

    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "completion-capture activation requires no active transaction",
        )
    version = current_schema_version(connection)
    if version >= 16:
        connection.execute("BEGIN")
        try:
            if (
                version not in {16, 17, 18, 19}
                or missing_migration_versions(connection, version)
                or required_schema_objects_missing(
                    connection,
                    schema_version=version,
                )
            ):
                raise StorageError(
                    "migration_required",
                    "completion-capture activation is incomplete",
                )
            validate_completion_cycle_storage(connection)
            quick_rows = [
                str(row[0])
                for row in connection.execute("PRAGMA quick_check").fetchall()
            ]
            if quick_rows != ["ok"]:
                raise completion_history_inconsistent()
            connection.commit()
        except StorageError as exc:
            connection.rollback()
            if exc.code == "completion_history_inconsistent":
                raise _unreadable_project_state() from exc
            raise
        except Exception:
            connection.rollback()
            raise
        return
    if (
        version != 15
        or missing_migration_versions(connection, 15)
        or schema_objects_inconsistent_with_version(connection, 15)
    ):
        raise StorageError(
            "migration_required",
            "completion-capture activation requires complete schema version 15",
        )
    connection.execute("PRAGMA foreign_keys = ON")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise StorageError(
            "internal_error",
            "completion-capture activation requires foreign key enforcement",
        )

    activation_time = utc_now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        validate_completion_cycle_storage(connection)
        nonlegacy_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM tasks
                 WHERE completion_history_coverage != 'legacy_unknown'
                """
            ).fetchone()[0]
        )
        if nonlegacy_count:
            raise completion_history_inconsistent()

        before = _migration_preservation_snapshot(connection)
        cycle_count_before = int(
            connection.execute(
                "SELECT COUNT(*) FROM task_completion_cycles"
            ).fetchone()[0]
        )
        inserted_count = 0
        done_rows = connection.execute(
            """
            SELECT project_id, task_id
              FROM tasks
             WHERE status = 'done'
             ORDER BY task_id COLLATE BINARY ASC
            """
        ).fetchall()
        for row in done_rows:
            project_id = str(row["project_id"])
            task_id = str(row["task_id"])
            (
                coverage,
                latest,
                projection_matches,
                reopen_linked,
            ) = _match_current_done_completion_cycle_locked(
                connection,
                project_id=project_id,
                task_id=task_id,
                validate_structure=False,
            )
            if coverage != "legacy_unknown":
                raise completion_history_inconsistent()
            if (
                latest is None
                or not projection_matches
                or reopen_linked
            ):
                insert_completion_cycle_locked(
                    connection,
                    project_id=project_id,
                    task_id=task_id,
                    recorded_at=activation_time,
                )
                inserted_count += 1
                if fail_stage == "after_first_reconciliation":
                    raise StorageError(
                        "internal_error",
                        "injected completion-capture activation failure",
                    )
        if fail_stage == "after_reconciliation":
            raise StorageError(
                "internal_error",
                "injected completion-capture activation failure",
            )

        after = _migration_preservation_snapshot(connection)
        cycle_count_after = int(
            connection.execute(
                "SELECT COUNT(*) FROM task_completion_cycles"
            ).fetchone()[0]
        )
        if (
            after != before
            or cycle_count_after - cycle_count_before != inserted_count
        ):
            raise completion_history_inconsistent()

        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (16, 'completion_cycle_capture_activation', ?)
            """,
            (activation_time,),
        )
        if fail_stage == "after_marker":
            raise StorageError(
                "internal_error",
                "injected completion-capture activation failure",
            )
        validate_completion_cycle_storage(connection)
        quick_rows = [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
        if quick_rows != ["ok"]:
            raise completion_history_inconsistent()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise completion_history_inconsistent()
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected completion-capture activation failure",
            )
        connection.commit()
    except StorageError as exc:
        connection.rollback()
        if exc.code == "completion_history_inconsistent":
            raise _unreadable_project_state() from exc
        raise
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise _unreadable_project_state() from exc
    except Exception:
        connection.rollback()
        raise


def _completion_cycle_projection_snapshot(
    connection: sqlite3.Connection,
    *,
    columns: tuple[str, ...] | None = None,
) -> tuple[tuple[str, ...], int, str]:
    selected_columns = columns or tuple(
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(task_completion_cycles)"
        ).fetchall()
    )
    if not selected_columns:
        raise completion_history_inconsistent()
    projection = ", ".join(
        _quoted_identifier(column) for column in selected_columns
    )
    rows = [
        list(row)
        for row in connection.execute(
            f"SELECT {projection} FROM task_completion_cycles ORDER BY rowid"
        ).fetchall()
    ]
    payload = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        selected_columns,
        len(rows),
        hashlib.sha256(payload).hexdigest(),
    )


def apply_verification_receipts_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Atomically add schema-v17 immutable Verification Receipts."""

    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "verification-Receipt migration requires no active transaction",
        )
    version = current_schema_version(connection)
    if version >= 17:
        connection.execute("BEGIN")
        try:
            if (
                version != 17
                or missing_migration_versions(connection, version)
                or required_schema_objects_missing(
                    connection,
                    schema_version=17,
                )
            ):
                raise StorageError(
                    "migration_required",
                    "verification-Receipt migration is incomplete",
                )
            validate_completion_cycle_storage(connection)
            quick_rows = [
                str(row[0])
                for row in connection.execute("PRAGMA quick_check").fetchall()
            ]
            if quick_rows != ["ok"]:
                raise invalid_verification_evidence()
            connection.commit()
        except StorageError as exc:
            connection.rollback()
            if exc.code in {
                "completion_history_inconsistent",
                "invalid_verification_evidence",
            }:
                raise _unreadable_project_state() from exc
            raise
        except Exception:
            connection.rollback()
            raise
        return
    if (
        version != 16
        or missing_migration_versions(connection, 16)
        or schema_objects_inconsistent_with_version(connection, 16)
    ):
        raise StorageError(
            "migration_required",
            "verification-Receipt migration requires complete schema version 16",
        )
    connection.execute("PRAGMA foreign_keys = ON")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise StorageError(
            "internal_error",
            "verification-Receipt migration requires foreign key enforcement",
        )

    before = _migration_preservation_snapshot(connection)
    column_basis = {
        table_name: snapshot[0]
        for table_name, snapshot in before.items()
    }
    cycle_before = _completion_cycle_projection_snapshot(connection)
    migration_time = utc_now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        statements = verification_receipt_schema_statements()
        for statement in statements[:4]:
            connection.execute(statement)
        if fail_stage == "after_receipt_schema":
            raise StorageError(
                "internal_error",
                "injected verification-Receipt migration failure",
            )
        for statement in statements[4:7]:
            connection.execute(statement)
        if fail_stage == "after_cycle_columns":
            raise StorageError(
                "internal_error",
                "injected verification-Receipt migration failure",
            )
        for statement in statements[7:]:
            connection.execute(statement)
        if fail_stage == "after_triggers":
            raise StorageError(
                "internal_error",
                "injected verification-Receipt migration failure",
            )

        receipt_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM verification_receipts"
            ).fetchone()[0]
        )
        nonlegacy_cycle_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM task_completion_cycles
                 WHERE verification_basis_version != 0
                    OR verification_expectation_digest IS NOT NULL
                    OR verification_receipt_id IS NOT NULL
                """
            ).fetchone()[0]
        )
        after = _migration_preservation_snapshot(
            connection,
            column_basis=column_basis,
        )
        cycle_after = _completion_cycle_projection_snapshot(
            connection,
            columns=cycle_before[0],
        )
        if (
            receipt_count
            or nonlegacy_cycle_count
            or after != before
            or cycle_after != cycle_before
        ):
            raise invalid_verification_evidence()

        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (17, 'verification_receipts', ?)
            """,
            (migration_time,),
        )
        if fail_stage == "after_marker":
            raise StorageError(
                "internal_error",
                "injected verification-Receipt migration failure",
            )
        validate_completion_cycle_storage(connection)
        quick_rows = [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
        if quick_rows != ["ok"]:
            raise invalid_verification_evidence()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise invalid_verification_evidence()
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected verification-Receipt migration failure",
            )
        connection.commit()
    except StorageError as exc:
        connection.rollback()
        if exc.code in {
            "completion_history_inconsistent",
            "invalid_verification_evidence",
        }:
            raise _unreadable_project_state() from exc
        raise
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise _unreadable_project_state() from exc
    except Exception:
        connection.rollback()
        raise


def _sqlite_projection_value_token(value: object) -> tuple[str, str]:
    if value is None:
        return ("null", "")
    if type(value) is int:
        return ("integer", str(value))
    if type(value) is float:
        return ("real", value.hex())
    if type(value) is str:
        return ("text", value)
    if type(value) is bytes:
        return ("blob", value.hex())
    raise evidence_ledger_inconsistent()


def _selected_table_projection_snapshot(
    connection: sqlite3.Connection,
    tables: tuple[str, ...],
    *,
    column_basis: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, tuple[tuple[str, ...], int, str]]:
    result: dict[str, tuple[tuple[str, ...], int, str]] = {}
    for table_name in tables:
        columns = (
            column_basis[table_name]
            if column_basis is not None
            else tuple(
                str(row["name"])
                for row in connection.execute(
                    f"PRAGMA table_info({_quoted_identifier(table_name)})"
                ).fetchall()
            )
        )
        if not columns:
            raise evidence_ledger_inconsistent()
        projection = ", ".join(_quoted_identifier(name) for name in columns)
        rows = sorted(
            tuple(_sqlite_projection_value_token(value) for value in row)
            for row in connection.execute(
                f"SELECT {projection} FROM {_quoted_identifier(table_name)}"
            ).fetchall()
        )
        payload = json.dumps(
            rows,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        result[table_name] = (
            columns,
            len(rows),
            hashlib.sha256(payload).hexdigest(),
        )
    return result


def apply_evidence_ledger_capture_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Atomically add schema-v18 capture storage and current legacy snapshots."""

    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "evidence-ledger migration requires no active transaction",
        )
    version = current_schema_version(connection)
    if version >= 18:
        connection.execute("BEGIN")
        try:
            if version not in {18, 19} or missing_migration_versions(
                connection,
                18,
            ):
                raise StorageError(
                    "migration_required",
                    "evidence-ledger migration is incomplete",
                )
            validate_evidence_ledger_storage(connection)
            validate_completion_cycle_storage(connection)
            if [
                str(row[0])
                for row in connection.execute("PRAGMA quick_check").fetchall()
            ] != ["ok"]:
                raise evidence_ledger_inconsistent()
            connection.commit()
        except StorageError as exc:
            connection.rollback()
            if exc.code in {
                "evidence_ledger_inconsistent",
                "completion_history_inconsistent",
                "invalid_verification_evidence",
            }:
                raise _unreadable_project_state() from exc
            raise
        except Exception:
            connection.rollback()
            raise
        return
    if (
        version != 17
        or missing_migration_versions(connection, 17)
        or schema_objects_inconsistent_with_version(connection, 17)
    ):
        raise StorageError(
            "migration_required",
            "evidence-ledger migration requires complete schema version 17",
        )

    from task_governance_tool.tasks import TaskRepositoryError, validate_stored_task_rows

    connection.execute("BEGIN IMMEDIATE")
    try:
        task_rows = connection.execute(
            "SELECT * FROM tasks ORDER BY task_id"
        ).fetchall()
        task_validation = validate_stored_task_rows(
            task_rows,
            connection=connection,
            source_schema_version=17,
            expected_project_id=(
                str(task_rows[0]["project_id"])
                if task_rows
                else "__empty_project__"
            ),
        )
        expected_contracts: set[tuple[str, str, int]] = set()
        selected_contract_rows: list[sqlite3.Row] = []
        for task_row in task_rows:
            project_id = task_row["project_id"]
            task_id = task_row["task_id"]
            revision = task_row["current_contract_revision"]
            if (
                type(project_id) is not str
                or not project_id
                or type(task_id) is not str
                or not task_id
                or type(revision) is not int
            ):
                raise _unreadable_project_state()
            if revision > 0:
                expected_contracts.add((project_id, task_id, revision))
                contract_row = task_validation.current_contract_rows.get(task_id)
                if contract_row is None:
                    raise _unreadable_project_state()
                selected_contract_rows.append(contract_row)
        _validated_contract_revision_rows(
            selected_contract_rows,
            expected_keys=expected_contracts,
        )
        validate_completion_cycle_storage(connection)
        preserved_tables = (
            *_MIGRATION_PRESERVATION_TABLES,
            "task_completion_cycles",
            "verification_receipts",
        )
        before = _selected_table_projection_snapshot(
            connection,
            preserved_tables,
        )
        old_columns = {name: value[0] for name, value in before.items()}
        migration_time = utc_now()
    except TaskRepositoryError as exc:
        connection.rollback()
        raise _unreadable_project_state() from exc
    except StorageError as exc:
        connection.rollback()
        if exc.code in {
            "evidence_ledger_inconsistent",
            "completion_history_inconsistent",
            "invalid_verification_evidence",
        }:
            raise _unreadable_project_state() from exc
        raise
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise _unreadable_project_state() from exc
    except Exception:
        connection.rollback()
        raise

    try:
        statements = evidence_ledger_capture_schema_statements()
        for statement in statements[:8]:
            connection.execute(statement)
        if fail_stage == "after_tables":
            raise StorageError("internal_error", "injected evidence-ledger migration failure")
        for statement in statements[8:23]:
            connection.execute(statement)
        if fail_stage == "after_columns":
            raise StorageError("internal_error", "injected evidence-ledger migration failure")
        for statement in statements[23:]:
            connection.execute(statement)
        if fail_stage == "after_objects":
            raise StorageError("internal_error", "injected evidence-ledger migration failure")

        for row in connection.execute(
            "SELECT project_id, task_id FROM tasks ORDER BY task_id"
        ).fetchall():
            capture_or_reuse_current_authority_snapshot_locked(
                connection,
                project_id=str(row["project_id"]),
                task_id=str(row["task_id"]),
                producer_class="legacy_migration",
                created_at=migration_time,
            )
        if fail_stage == "after_snapshots":
            raise StorageError("internal_error", "injected evidence-ledger migration failure")

        after = _selected_table_projection_snapshot(
            connection,
            preserved_tables,
            column_basis=old_columns,
        )
        if after != before:
            raise evidence_ledger_inconsistent()
        invented_count = sum(
            int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
            for table_name in (
                "review_receipt_provenance",
                "review_receipt_provenance_codes",
                "artifact_manifests",
                "artifact_manifest_entries",
                "evidence_references",
            )
        )
        nonlegacy_review = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM review_receipts
                 WHERE review_provenance_basis_version != 0
                    OR review_provenance_id IS NOT NULL
                """
            ).fetchone()[0]
        )
        nonlegacy_subject = sum(
            int(connection.execute(
                f"""
                SELECT COUNT(*) FROM {table_name}
                 WHERE verification_subject_basis_version != 0
                    OR subject_authority_snapshot_id IS NOT NULL
                    OR subject_verification_criterion_id IS NOT NULL
                """
            ).fetchone()[0])
            for table_name in ("verification_receipts", "task_completion_cycles")
        )
        capture_bindings = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM tasks
                 WHERE review_target_capture_version != 0
                    OR review_target_authority_snapshot_id IS NOT NULL
                    OR review_target_acceptance_criterion_id IS NOT NULL
                    OR review_target_verification_criterion_id IS NOT NULL
                    OR review_target_artifact_manifest_id IS NOT NULL
                """
            ).fetchone()[0]
        )
        if invented_count or nonlegacy_review or nonlegacy_subject or capture_bindings:
            raise evidence_ledger_inconsistent()

        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (18, 'evidence_ledger_capture', ?)
            """,
            (migration_time,),
        )
        if fail_stage == "after_marker":
            raise StorageError("internal_error", "injected evidence-ledger migration failure")
        validate_evidence_ledger_storage(connection)
        validate_completion_cycle_storage(connection)
        if [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ] != ["ok"]:
            raise evidence_ledger_inconsistent()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise evidence_ledger_inconsistent()
        if fail_stage == "before_commit":
            raise StorageError("internal_error", "injected evidence-ledger migration failure")
        connection.commit()
    except StorageError as exc:
        connection.rollback()
        if exc.code in {
            "evidence_ledger_inconsistent",
            "completion_history_inconsistent",
            "invalid_verification_evidence",
        }:
            raise _unreadable_project_state() from exc
        raise
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise _unreadable_project_state() from exc
    except Exception:
        connection.rollback()
        raise


def apply_completion_evidence_bundle_migration(
    connection: sqlite3.Connection,
    *,
    fail_stage: str | None = None,
) -> None:
    """Atomically add schema-v19 Bundle storage without inventing history."""

    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "completion Bundle migration requires no active transaction",
        )
    version = current_schema_version(connection)
    if version >= 19:
        connection.execute("BEGIN")
        try:
            if version != 19 or missing_migration_versions(connection, 19):
                raise StorageError(
                    "migration_required",
                    "completion Bundle migration is incomplete",
                )
            validate_evidence_ledger_storage(connection)
            validate_completion_cycle_storage(connection)
            if [
                str(row[0])
                for row in connection.execute("PRAGMA quick_check").fetchall()
            ] != ["ok"]:
                raise evidence_ledger_inconsistent()
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise evidence_ledger_inconsistent()
            connection.commit()
        except StorageError as exc:
            connection.rollback()
            if exc.code in {
                "evidence_ledger_inconsistent",
                "completion_history_inconsistent",
                "invalid_verification_evidence",
            }:
                raise _unreadable_project_state() from exc
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise _unreadable_project_state() from exc
        except Exception:
            connection.rollback()
            raise
        return
    if (
        version != 18
        or missing_migration_versions(connection, 18)
        or schema_objects_inconsistent_with_version(connection, 18)
    ):
        raise StorageError(
            "migration_required",
            "completion Bundle migration requires complete schema version 18",
        )

    connection.execute("BEGIN IMMEDIATE")
    try:
        validate_evidence_ledger_storage(connection)
        validate_completion_cycle_storage(connection)
        preserved_tables = tuple(
            table_name
            for table_name, introduced_version in (
                _SCHEMA_TABLE_INTRODUCED_VERSION.items()
            )
            if table_name != "schema_migrations" and introduced_version <= 18
        )
        before = _selected_table_projection_snapshot(
            connection,
            preserved_tables,
        )
        old_columns = {name: value[0] for name, value in before.items()}
        project_rows = connection.execute(
            "SELECT project_id FROM project_meta ORDER BY project_id"
        ).fetchall()
        initial_generations: dict[str, int] = {}
        for project_row in project_rows:
            project_id = project_row["project_id"]
            if type(project_id) is not str or not project_id:
                raise evidence_ledger_inconsistent()
            count_row = connection.execute(
                """
                SELECT COUNT(*) AS cycle_count
                  FROM task_completion_cycles
                 WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            cycle_count = None if count_row is None else count_row["cycle_count"]
            if type(cycle_count) is not int or not 0 <= cycle_count <= SQLITE_INT64_MAX:
                raise evidence_ledger_inconsistent()
            initial_generations[project_id] = cycle_count
        migration_time = utc_now()

        statements = completion_evidence_bundle_schema_statements()
        for statement in statements[:5]:
            connection.execute(statement)
        if fail_stage == "after_tables":
            raise StorageError(
                "internal_error",
                "injected completion Bundle migration failure",
            )
        for statement in statements[5:7]:
            connection.execute(statement)
        if fail_stage == "after_columns":
            raise StorageError(
                "internal_error",
                "injected completion Bundle migration failure",
            )
        for statement in statements[7:]:
            connection.execute(statement)
        if fail_stage == "after_objects":
            raise StorageError(
                "internal_error",
                "injected completion Bundle migration failure",
            )

        for project_id, source_generation in initial_generations.items():
            connection.execute(
                """
                INSERT INTO evidence_projection_state(
                  project_id, source_generation, published_generation,
                  index_digest, last_success_at, last_outcome_code,
                  last_outcome_at
                ) VALUES (?, ?, NULL, NULL, NULL, NULL, NULL)
                """,
                (project_id, source_generation),
            )
        if fail_stage == "after_state":
            raise StorageError(
                "internal_error",
                "injected completion Bundle migration failure",
            )

        after = _selected_table_projection_snapshot(
            connection,
            preserved_tables,
            column_basis=old_columns,
        )
        if after != before:
            raise evidence_ledger_inconsistent()
        invented_count = sum(
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quoted_identifier(table_name)}"
                ).fetchone()[0]
            )
            for table_name in (
                "criterion_evidence_links",
                "completion_evidence_bundles",
                "completion_bundle_members",
                "completion_bundle_finding_snapshots",
            )
        )
        nonlegacy_cycles = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM task_completion_cycles
                 WHERE evidence_basis_version != 0
                    OR completion_evidence_bundle_id IS NOT NULL
                """
            ).fetchone()[0]
        )
        if invented_count or nonlegacy_cycles:
            raise evidence_ledger_inconsistent()

        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (19, 'completion_evidence_bundles', ?)
            """,
            (migration_time,),
        )
        if fail_stage == "after_marker":
            raise StorageError(
                "internal_error",
                "injected completion Bundle migration failure",
            )
        validate_evidence_ledger_storage(connection)
        validate_completion_cycle_storage(connection)
        if [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ] != ["ok"]:
            raise evidence_ledger_inconsistent()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise evidence_ledger_inconsistent()
        if fail_stage == "before_commit":
            raise StorageError(
                "internal_error",
                "injected completion Bundle migration failure",
            )
        connection.commit()
    except StorageError as exc:
        connection.rollback()
        if exc.code in {
            "evidence_ledger_inconsistent",
            "completion_history_inconsistent",
            "invalid_verification_evidence",
        }:
            raise _unreadable_project_state() from exc
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise _unreadable_project_state() from exc
    except Exception:
        connection.rollback()
        raise


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
) -> tuple[list[int], list[dict[str, str]]]:
    version = current_schema_version(connection)
    if version > SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            f"database schema version {version} is newer than supported version {SCHEMA_VERSION}",
        )
    if version > 0 and missing_migration_versions(connection, version):
        raise StorageError(
            "migration_required",
            (
                "database migration history is incomplete; restore a valid "
                "database backup or inspect the migration history"
            ),
        )
    if version > 0 and schema_objects_inconsistent_with_version(
        connection,
        version,
    ):
        raise StorageError(
            "migration_required",
            "database schema is inconsistent with its declared version",
        )
    if version == PRIVATE_SCHEMA21_VERSION:
        _migrate_schema21_connection(connection)
        return [], []
    if version == PRIVATE_SCHEMA20_VERSION:
        if SCHEMA_VERSION >= PRIVATE_SCHEMA21_VERSION:
            migrated = _migrate_schema21_connection(connection)
            return ([PRIVATE_SCHEMA21_VERSION] if migrated else []), []
        _migrate_schema20_connection(
            connection,
            allow_native_bundle_v2_reentry=True,
        )
        return [], []

    applied: list[int] = []
    warnings: list[dict[str, str]] = []
    if version < 1:
        apply_initial_schema_migration(connection)
        applied.append(1)
        version = 1
    if version < 2:
        apply_completion_commit_migration(connection)
        applied.append(2)
        version = 2
    if version < 3:
        if connection.in_transaction:
            connection.commit()
        apply_paused_state_migration(connection)
        applied.append(3)
        version = 3
    if version < 4:
        inconsistent_count = apply_completion_evidence_migration(connection)
        applied.append(4)
        if inconsistent_count:
            warnings.append(
                {
                    "code": "legacy_completion_evidence_preserved",
                    "message": (
                        f"preserved {inconsistent_count} inconsistent legacy completion "
                        "record(s) as legacy_unverified"
                    ),
                }
            )
        version = 4
    if version < 5:
        apply_review_evidence_migration(connection)
        applied.append(5)
        version = 5
    if version < 6:
        apply_git_snapshot_schema_migration(connection)
        applied.append(6)
        version = 6
    if version < 7:
        apply_handoff_outbox_migration(connection)
        applied.append(7)
        version = 7
    if version < 8:
        apply_task_contract_migration(connection)
        applied.append(8)
        version = 8
    if version < 9:
        apply_effort_advisory_migration(connection)
        applied.append(9)
        version = 9
    if version < 10:
        apply_project_maintenance_migration(
            connection,
            setup_backup=setup_backup,
        )
        applied.append(10)
        version = 10
    if version < 11:
        apply_managed_backup_generations_migration(
            connection,
            managed_backups=managed_backups,
        )
        applied.append(11)
        version = 11
    if version < 12:
        apply_task_checkpoints_migration(connection)
        applied.append(12)
        version = 12
    if version < 13:
        apply_viewer_maintenance_migration(connection)
        applied.append(13)
        version = 13
    if version < 14:
        apply_project_identity_bindings_migration(connection)
        applied.append(14)
        version = 14
    if version < 15:
        apply_completion_cycle_history_migration(connection)
        applied.append(15)
        version = 15
    if version < 16:
        apply_completion_cycle_capture_activation_migration(connection)
        applied.append(16)
        version = 16
    else:
        apply_completion_cycle_capture_activation_migration(connection)
    if version < 17:
        apply_verification_receipts_migration(connection)
        applied.append(17)
        version = 17
    elif version == 17:
        apply_verification_receipts_migration(connection)
    if version < 18:
        apply_evidence_ledger_capture_migration(connection)
        applied.append(18)
        version = 18
    else:
        apply_evidence_ledger_capture_migration(connection)
    if connection.in_transaction:
        connection.commit()
    if version < 19:
        apply_completion_evidence_bundle_migration(connection)
        applied.append(19)
        version = 19
    elif version == 19:
        apply_completion_evidence_bundle_migration(connection)
    if connection.in_transaction:
        connection.commit()
    if version < PRIVATE_SCHEMA20_VERSION:
        if _migrate_schema20_connection(connection):
            applied.append(PRIVATE_SCHEMA20_VERSION)
        version = PRIVATE_SCHEMA20_VERSION
    if SCHEMA_VERSION >= PRIVATE_SCHEMA21_VERSION and version < PRIVATE_SCHEMA21_VERSION:
        if _migrate_schema21_connection(connection):
            applied.append(PRIVATE_SCHEMA21_VERSION)
        version = PRIVATE_SCHEMA21_VERSION
    return applied, warnings


def _read_project_binding_snapshot(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str | None = None,
) -> tuple[ProjectBindingState, tuple[ProjectPathBinding, ...]]:
    """Validate and return the one schema-v14 current binding and its lineage."""
    try:
        rows = connection.execute(
            """
            SELECT project_id, canonical_path_hash, display_name, created_at,
                   updated_at, identity_scheme, binding_generation,
                   binding_reason, binding_updated_at, legacy_cleanup_pending,
                   legacy_cleanup_inventory, legacy_cleanup_fingerprint
              FROM project_meta
             ORDER BY project_id
            """
        ).fetchall()
        if len(rows) != 1:
            raise _unreadable_project_state()
        row = rows[0]
        project_id = str(row["project_id"])
        identity_scheme = str(row["identity_scheme"])
        validate_identity_project_id(project_id, identity_scheme)
        if expected_project_id is not None and project_id != expected_project_id:
            raise StorageError(
                "project_mismatch",
                "task database belongs to a different project",
            )
        canonical_hash = validate_lower_hex_64(
            str(row["canonical_path_hash"]),
            field="canonical path hash",
        )
        display_name = validate_project_display_name(str(row["display_name"]))
        validate_utc_timestamp(str(row["created_at"]), field="project creation time")
        validate_utc_timestamp(str(row["updated_at"]), field="project update time")
        binding_generation = validate_binding_generation(row["binding_generation"])
        binding_reason = str(row["binding_reason"])
        if binding_reason not in BINDING_REASONS:
            raise StorageError("internal_error", "project binding reason is invalid")
        binding_updated_at = validate_utc_timestamp(
            str(row["binding_updated_at"]),
            field="project binding time",
        )

        cleanup_pending = int(row["legacy_cleanup_pending"])
        cleanup_inventory = (
            str(row["legacy_cleanup_inventory"])
            if row["legacy_cleanup_inventory"] is not None
            else None
        )
        cleanup_fingerprint = (
            str(row["legacy_cleanup_fingerprint"])
            if row["legacy_cleanup_fingerprint"] is not None
            else None
        )
        if cleanup_pending == 0:
            if cleanup_inventory is not None or cleanup_fingerprint is not None:
                raise StorageError(
                    "internal_error",
                    "legacy cleanup metadata is invalid",
                )
        elif cleanup_pending == 1:
            if cleanup_inventory is None or cleanup_fingerprint is None:
                raise StorageError(
                    "internal_error",
                    "legacy cleanup metadata is invalid",
                )
            validate_cleanup_inventory(cleanup_inventory, cleanup_fingerprint)
        else:
            raise StorageError(
                "internal_error",
                "legacy cleanup metadata is invalid",
            )

        history_rows = connection.execute(
            """
            SELECT project_id, binding_generation, previous_path_hash,
                   canonical_path_hash, display_name, reason,
                   confirmation_token_digest, bound_at
              FROM project_path_binding_history
             WHERE project_id = ?
             ORDER BY binding_generation
            """,
            (project_id,),
        ).fetchall()
        history_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM project_path_binding_history"
            ).fetchone()[0]
        )
        if (
            len(history_rows) != binding_generation
            or history_count != len(history_rows)
        ):
            raise StorageError("internal_error", "project binding history is invalid")

        previous_hash: str | None = None
        history: list[ProjectPathBinding] = []
        for expected_generation, history_row in enumerate(history_rows, start=1):
            history_project_id = str(history_row["project_id"])
            generation = validate_binding_generation(
                history_row["binding_generation"]
            )
            history_previous_hash = (
                validate_lower_hex_64(
                    str(history_row["previous_path_hash"]),
                    field="previous canonical path hash",
                )
                if history_row["previous_path_hash"] is not None
                else None
            )
            history_hash = validate_lower_hex_64(
                str(history_row["canonical_path_hash"]),
                field="canonical path hash",
            )
            history_display = validate_project_display_name(
                str(history_row["display_name"])
            )
            reason = str(history_row["reason"])
            token_digest = (
                validate_lower_hex_64(
                    str(history_row["confirmation_token_digest"]),
                    field="confirmation token digest",
                )
                if history_row["confirmation_token_digest"] is not None
                else None
            )
            bound_at = validate_utc_timestamp(
                str(history_row["bound_at"]),
                field="project binding time",
            )
            if (
                history_project_id != project_id
                or generation != expected_generation
                or history_previous_hash != previous_hash
            ):
                raise StorageError(
                    "internal_error",
                    "project binding history is invalid",
                )
            expected_reason = (
                "legacy_migration"
                if identity_scheme == "legacy_path_v1"
                else "fresh_setup"
            )
            if generation == 1:
                if reason != expected_reason or token_digest is not None:
                    raise StorageError(
                        "internal_error",
                        "project binding history is invalid",
                    )
            elif reason != "confirmed_relocation" or token_digest is None:
                raise StorageError(
                    "internal_error",
                    "project binding history is invalid",
                )
            history.append(ProjectPathBinding(
                project_id=history_project_id,
                binding_generation=generation,
                previous_path_hash=history_previous_hash,
                canonical_path_hash=history_hash,
                display_name=history_display,
                reason=reason,
                confirmation_token_digest=token_digest,
                bound_at=bound_at,
            ))
            previous_hash = history_hash

        history_head = history[-1] if history else None
        if history_head is None or (
            history_head.binding_generation != binding_generation
            or history_head.canonical_path_hash != canonical_hash
            or history_head.display_name != display_name
            or history_head.reason != binding_reason
            or history_head.bound_at != binding_updated_at
        ):
            raise StorageError("internal_error", "project binding history is invalid")
        state = ProjectBindingState(
            project_id=project_id,
            identity_scheme=identity_scheme,
            binding_generation=binding_generation,
            canonical_path_hash=canonical_hash,
            display_name=display_name,
            binding_reason=binding_reason,
            binding_updated_at=binding_updated_at,
            legacy_cleanup_pending=bool(cleanup_pending),
            legacy_cleanup_inventory=cleanup_inventory,
            legacy_cleanup_fingerprint=cleanup_fingerprint,
        )
        return state, tuple(history)
    except StorageError as exc:
        if exc.code == "project_mismatch":
            raise
        raise _unreadable_project_state() from exc
    except (TypeError, ValueError, sqlite3.Error) as exc:
        raise _unreadable_project_state() from exc


def read_project_binding_state(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str | None = None,
) -> ProjectBindingState:
    """Validate and return the one schema-v14 current binding."""

    return _read_project_binding_snapshot(
        connection,
        expected_project_id=expected_project_id,
    )[0]


def read_project_binding_history(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str | None = None,
) -> tuple[ProjectPathBinding, ...]:
    """Validate and return the complete schema-v14 binding lineage."""

    return _read_project_binding_snapshot(
        connection,
        expected_project_id=expected_project_id,
    )[1]


def ensure_project_meta(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    identity_scheme: str = "legacy_path_v1",
    binding_reason: str = "legacy_migration",
    timestamp: str | None = None,
    fail_stage: str | None = None,
) -> None:
    now = validate_utc_timestamp(
        timestamp or utc_now(),
        field="project binding time",
    )
    display_name = validate_project_display_name(
        sanitize_project_display_name(project.display_name)
    )
    validate_lower_hex_64(project.canonical_path_hash, field="canonical path hash")
    validate_identity_project_id(project.project_id, identity_scheme)
    expected_reason = (
        "legacy_migration" if identity_scheme == "legacy_path_v1" else "fresh_setup"
    )
    if binding_reason != expected_reason:
        raise StorageError("internal_error", "initial project binding reason is invalid")

    version = current_schema_version(connection)
    rows = connection.execute(
        "SELECT project_id FROM project_meta ORDER BY project_id"
    ).fetchall()
    if version < 14:
        if identity_scheme != "legacy_path_v1":
            raise StorageError(
                "internal_error",
                "UUID project identity requires schema version 14",
            )
        if rows:
            existing_project_id = str(rows[0]["project_id"])
            if len(rows) != 1:
                raise _unreadable_project_state()
            try:
                validate_identity_project_id(
                    existing_project_id,
                    "legacy_path_v1",
                )
            except StorageError as exc:
                raise _unreadable_project_state() from exc
            if existing_project_id != project.project_id:
                raise StorageError(
                    "project_mismatch",
                    "task database belongs to a different project",
                )
            connection.execute(
                """
                UPDATE project_meta
                   SET canonical_path_hash = ?, display_name = ?, updated_at = ?
                 WHERE project_id = ?
                """,
                (
                    project.canonical_path_hash,
                    display_name,
                    now,
                    project.project_id,
                ),
            )
            return
        connection.execute(
            """
            INSERT INTO project_meta(
              project_id, canonical_path_hash, display_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                project.project_id,
                project.canonical_path_hash,
                display_name,
                now,
                now,
            ),
        )
        return

    if rows:
        binding = read_project_binding_state(connection)
        if binding.project_id != project.project_id:
            raise StorageError(
                "project_mismatch",
                "task database belongs to a different project",
            )
        if (
            binding.identity_scheme != identity_scheme
            or binding.canonical_path_hash != project.canonical_path_hash
        ):
            raise _unreadable_project_state()
        return

    connection.execute(
        """
        INSERT INTO project_meta(
          project_id, canonical_path_hash, display_name, created_at, updated_at,
          identity_scheme, binding_generation, binding_reason,
          binding_updated_at, legacy_cleanup_pending,
          legacy_cleanup_inventory, legacy_cleanup_fingerprint
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 0, NULL, NULL)
        """,
        (
            project.project_id,
            project.canonical_path_hash,
            display_name,
            now,
            now,
            identity_scheme,
            binding_reason,
            now,
        ),
    )
    if fail_stage == "after_project_row":
        raise StorageError(
            "internal_error",
            "injected project metadata initialization failure",
        )
    connection.execute(
        """
        INSERT INTO project_path_binding_history(
          project_id, binding_generation, previous_path_hash,
          canonical_path_hash, display_name, reason,
          confirmation_token_digest, bound_at
        ) VALUES (?, 1, NULL, ?, ?, ?, NULL, ?)
        """,
        (
            project.project_id,
            project.canonical_path_hash,
            display_name,
            binding_reason,
            now,
        ),
    )
    if fail_stage == "after_history_row":
        raise StorageError(
            "internal_error",
            "injected project metadata initialization failure",
        )


def read_project_meta_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute("SELECT project_id FROM project_meta ORDER BY project_id LIMIT 1").fetchone()
    if row is None:
        return None
    return str(row["project_id"])


def read_project_maintenance(
    connection: sqlite3.Connection,
    project_id: str,
) -> ProjectMaintenanceState | None:
    row = connection.execute(
        """
        SELECT project_id, enabled_at, backup_interval_minutes,
               backup_generations, applied_backup_generations,
               backup_last_success_at, backup_last_outcome_code,
               backup_last_outcome_at, latest_backup_generation_id,
               viewer_last_success_at, viewer_last_outcome_code,
               viewer_last_outcome_at
          FROM project_maintenance
         WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return ProjectMaintenanceState(
        project_id=str(row["project_id"]),
        enabled_at=(
            str(row["enabled_at"]) if row["enabled_at"] is not None else None
        ),
        backup_interval_minutes=(
            validate_sqlite_integer_storage_class(
                row["backup_interval_minutes"]
            )
            if row["backup_interval_minutes"] is not None
            else None
        ),
        backup_generations=(
            validate_sqlite_integer_storage_class(row["backup_generations"])
            if row["backup_generations"] is not None
            else None
        ),
        applied_backup_generations=(
            validate_sqlite_integer_storage_class(
                row["applied_backup_generations"]
            )
            if row["applied_backup_generations"] is not None
            else None
        ),
        backup_last_success_at=(
            str(row["backup_last_success_at"])
            if row["backup_last_success_at"] is not None
            else None
        ),
        backup_last_outcome_code=(
            str(row["backup_last_outcome_code"])
            if row["backup_last_outcome_code"] is not None
            else None
        ),
        backup_last_outcome_at=(
            str(row["backup_last_outcome_at"])
            if row["backup_last_outcome_at"] is not None
            else None
        ),
        latest_backup_generation_id=(
            str(row["latest_backup_generation_id"])
            if row["latest_backup_generation_id"] is not None
            else None
        ),
        viewer_last_success_at=(
            str(row["viewer_last_success_at"])
            if row["viewer_last_success_at"] is not None
            else None
        ),
        viewer_last_outcome_code=(
            str(row["viewer_last_outcome_code"])
            if row["viewer_last_outcome_code"] is not None
            else None
        ),
        viewer_last_outcome_at=(
            str(row["viewer_last_outcome_at"])
            if row["viewer_last_outcome_at"] is not None
            else None
        ),
    )


def ensure_project_maintenance_row(
    connection: sqlite3.Connection,
    project_id: str,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO project_maintenance(project_id) VALUES (?)",
        (project_id,),
    )


def read_viewer_maintenance(
    connection: sqlite3.Connection,
    project_id: str,
) -> ViewerMaintenanceState | None:
    row = connection.execute(
        """
        SELECT project_id, source_generation, rendered_generation,
               last_success_at, last_outcome_code, last_outcome_at
          FROM viewer_maintenance_state
         WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    source_generation = validate_viewer_generation(
        row["source_generation"],
        field="Viewer source generation",
    )
    rendered_generation = (
        validate_viewer_generation(
            row["rendered_generation"],
            field="Viewer rendered generation",
        )
        if row["rendered_generation"] is not None
        else None
    )
    if (
        source_generation < 0
        or (
            rendered_generation is not None
            and (
                rendered_generation < 0
                or rendered_generation > source_generation
            )
        )
    ):
        raise StorageError(
            "internal_error",
            "Viewer generation state is invalid",
        )
    last_success_at = (
        validate_utc_timestamp(
            str(row["last_success_at"]),
            field="Viewer success time",
        )
        if row["last_success_at"] is not None
        else None
    )
    last_outcome_code = (
        str(row["last_outcome_code"])
        if row["last_outcome_code"] is not None
        else None
    )
    if last_outcome_code not in {
        None,
        "succeeded",
        "deferred",
        "failed",
    }:
        raise StorageError("internal_error", "Viewer outcome is invalid")
    last_outcome_at = (
        validate_utc_timestamp(
            str(row["last_outcome_at"]),
            field="Viewer outcome time",
        )
        if row["last_outcome_at"] is not None
        else None
    )
    if (last_outcome_code is None) != (last_outcome_at is None):
        raise StorageError("internal_error", "Viewer outcome is incomplete")
    return ViewerMaintenanceState(
        project_id=str(row["project_id"]),
        source_generation=source_generation,
        rendered_generation=rendered_generation,
        last_success_at=last_success_at,
        last_outcome_code=last_outcome_code,
        last_outcome_at=last_outcome_at,
    )


def ensure_viewer_maintenance_row(
    connection: sqlite3.Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO viewer_maintenance_state(
          project_id, source_generation, rendered_generation
        ) VALUES (?, 0, NULL)
        """,
        (project_id,),
    )


def _validate_target_binding(
    binding: ProjectBindingState,
    target: DatabaseTarget,
) -> None:
    existing_project_id = binding.project_id
    if existing_project_id != target.project.project_id:
        raise StorageError(
            "project_mismatch",
            "task database belongs to a different project",
        )
    if (
        (target.binding_path_hash is None)
        != (target.binding_generation is None)
    ):
        raise StorageError(
            "internal_error",
            "database target binding basis is incomplete",
        )
    if target.binding_path_hash is not None:
        try:
            expected_hash = validate_lower_hex_64(
                target.binding_path_hash,
                field="database target binding hash",
            )
            expected_generation = validate_binding_generation(
                target.binding_generation
            )
        except StorageError as exc:
            raise StorageError(
                "internal_error",
                "database target binding basis is invalid",
            ) from exc
        if (
            binding.canonical_path_hash != expected_hash
            or binding.binding_generation != expected_generation
        ):
            raise _unreadable_project_state()


def _validate_evidence_ledger_schema_contract(
    connection: sqlite3.Connection,
) -> None:
    marker = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 18"
    ).fetchone()
    if marker is None or str(marker["name"]) != "evidence_ledger_capture":
        raise evidence_ledger_inconsistent()
    missing = required_schema_objects_missing(connection, schema_version=18)
    if missing:
        raise evidence_ledger_inconsistent()
    statements = evidence_ledger_capture_schema_statements()
    for statement in statements[:8]:
        match = re.match(
            r"\s*CREATE\s+TABLE\s+([a-z0-9_]+)\b",
            statement,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise AssertionError("evidence-ledger table inventory is incomplete")
        expected_sql = _normalized_schema_sql(statement)
        if (
            match.group(1) == "evidence_references"
            and current_schema_version(connection) == PRIVATE_SCHEMA22_VERSION
        ):
            expected_sql = dict(_SCHEMA22_EXPECTED_OBJECTS)["evidence_references"][2]
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (match.group(1),),
        ).fetchone()
        if (
            row is None
            or row["sql"] is None
            or _normalized_schema_sql(str(row["sql"]))
            != expected_sql
        ):
            raise evidence_ledger_inconsistent()

    alter_statements: list[
        tuple[str, str, str, str, int, str | None, int]
    ] = []
    index_statements: list[
        tuple[str, str, tuple[str, ...], int, int, str]
    ] = []
    for statement in statements:
        normalized_statement = _normalized_schema_sql(statement)
        if re.match(r"^ALTER\s+TABLE\b", normalized_statement, re.IGNORECASE):
            match = re.fullmatch(
                r"ALTER\s+TABLE\s+([a-z0-9_]+)\s+ADD\s+COLUMN\s+"
                r"([a-z0-9_]+)\s+([a-z0-9_]+)(?:\s+(.*))?",
                normalized_statement,
                flags=re.IGNORECASE,
            )
            if match is None:
                raise AssertionError(
                    "evidence-ledger ALTER COLUMN inventory is incomplete"
                )
            table_name, column_name, declared_type, remainder = match.groups()
            remainder = remainder or ""
            expected_definition = _normalized_schema_sql(
                " ".join(
                    value
                    for value in (column_name, declared_type, remainder)
                    if value
                )
            )
            default_match = re.search(
                r"\bDEFAULT\s+(.+?)(?=\s+(?:CONSTRAINT|PRIMARY|NOT|UNIQUE|"
                r"CHECK|REFERENCES|COLLATE|GENERATED)\b|$)",
                remainder,
                flags=re.IGNORECASE,
            )
            alter_statements.append(
                (
                    table_name,
                    column_name,
                    declared_type.upper(),
                    expected_definition,
                    int(
                        re.search(
                            r"\bNOT\s+NULL\b",
                            remainder,
                            re.IGNORECASE,
                        )
                        is not None
                    ),
                    (
                        _normalized_schema_sql(default_match.group(1))
                        if default_match is not None
                        else None
                    ),
                    int(
                        re.search(
                            r"\bPRIMARY\s+KEY\b",
                            remainder,
                            re.IGNORECASE,
                        )
                        is not None
                    ),
                )
            )
            continue
        if re.match(
            r"^CREATE\s+(?:UNIQUE\s+)?INDEX\b",
            normalized_statement,
            re.IGNORECASE,
        ):
            match = re.fullmatch(
                r"CREATE\s+(UNIQUE\s+)?INDEX\s+([a-z0-9_]+)\s+ON\s+"
                r"([a-z0-9_]+)\s*\(([^()]*)\)(?:\s+(WHERE)\s+.+)?",
                normalized_statement,
                flags=re.IGNORECASE,
            )
            if match is None:
                raise AssertionError(
                    "evidence-ledger index inventory is incomplete"
                )
            unique, index_name, table_name, raw_columns, where = match.groups()
            columns = tuple(
                value.strip() for value in raw_columns.split(",")
            )
            if not columns or any(
                re.fullmatch(r"[a-z0-9_]+", value, re.IGNORECASE) is None
                for value in columns
            ):
                raise AssertionError(
                    "evidence-ledger index column inventory is incomplete"
                )
            index_statements.append(
                (
                    index_name,
                    table_name,
                    columns,
                    int(unique is not None),
                    int(where is not None),
                    normalized_statement,
                )
            )

    base_constraint_suffixes: dict[str, str] = {}
    base_columns: dict[str, tuple[int, tuple[str, ...]]] = {}
    for base_version, base_statement in (
        (3, paused_tasks_schema_sql()),
        (6, git_snapshot_review_receipts_schema_sql()),
        (17, verification_receipt_schema_statements()[0]),
        (15, completion_cycle_history_schema_statements()[3]),
    ):
        normalized_base = _normalized_schema_sql(base_statement)
        table_match = re.match(
            r"CREATE\s+TABLE\s+([a-z0-9_]+)\s*\(",
            normalized_base,
            flags=re.IGNORECASE,
        )
        constraint_match = re.search(
            r",\s+(?=(?:UNIQUE|CHECK|FOREIGN\s+KEY)\s*\()",
            normalized_base,
            flags=re.IGNORECASE,
        )
        if table_match is None or constraint_match is None:
            raise AssertionError(
                "evidence-ledger base table inventory is incomplete"
            )
        table_name = re.sub(r"_v[0-9]+$", "", table_match.group(1))
        base_constraint_suffixes[table_name] = normalized_base[
            constraint_match.start() :
        ]
        column_names = tuple(
            match.group(1)
            for line in base_statement.splitlines()
            if (
                match := re.match(
                    r"^\s+([a-z0-9_]+)\s+(?:TEXT|INTEGER)\b",
                    line,
                    flags=re.IGNORECASE,
                )
            )
        )
        if not column_names:
            raise AssertionError(
                "evidence-ledger base column inventory is incomplete"
            )
        base_columns[table_name] = (base_version, column_names)
    if set(base_constraint_suffixes) != {
        contract[0] for contract in alter_statements
    } or set(base_columns) != set(base_constraint_suffixes):
        raise AssertionError(
            "evidence-ledger ALTER table inventory is incomplete"
        )

    table_sql: dict[str, str] = {}
    table_columns: dict[str, dict[str, sqlite3.Row]] = {}
    table_foreign_keys: dict[
        str,
        list[tuple[str, tuple[tuple[str, str], ...], str, str, str]],
    ] = {}
    for (
        table_name,
        column_name,
        expected_type,
        expected_definition,
        expected_notnull,
        expected_default,
        expected_pk,
    ) in alter_statements:
        if table_name not in table_sql:
            table_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            if table_row is None or table_row["sql"] is None:
                raise evidence_ledger_inconsistent()
            table_sql[table_name] = _normalized_schema_sql(
                str(table_row["sql"])
            )
            expected_definitions = tuple(
                contract[3]
                for contract in alter_statements
                if contract[0] == table_name
            )
            later_definitions: tuple[str, ...] = ()
            later_columns: tuple[str, ...] = ()
            if (
                current_schema_version(connection) >= 19
                and table_name == "task_completion_cycles"
            ):
                later_definitions = (
                    _normalized_schema_sql(
                        "evidence_basis_version INTEGER NOT NULL DEFAULT 0 "
                        "CHECK (evidence_basis_version IN (0, 1))"
                    ),
                    _normalized_schema_sql(
                        "completion_evidence_bundle_id TEXT REFERENCES "
                        "completion_evidence_bundles("
                        "completion_evidence_bundle_id) DEFERRABLE "
                        "INITIALLY DEFERRED"
                    ),
                )
                later_columns = (
                    "evidence_basis_version",
                    "completion_evidence_bundle_id",
                )
            if current_schema_version(connection) >= PRIVATE_SCHEMA20_VERSION:
                if table_name == "tasks":
                    later_definitions = (
                        _normalized_schema_sql(
                            "review_target_runner_basis_version INTEGER NOT NULL "
                            "DEFAULT 0 CHECK "
                            "(review_target_runner_basis_version IN (0, 2))"
                        ),
                    )
                    later_columns = ("review_target_runner_basis_version",)
                elif table_name == "task_completion_cycles":
                    later_definitions = (
                        *later_definitions,
                        _normalized_schema_sql("verification_basis_kind TEXT"),
                        _normalized_schema_sql(
                            "verification_runner_observation_id TEXT"
                        ),
                    )
                    later_columns = (
                        *later_columns,
                        "verification_basis_kind",
                        "verification_runner_observation_id",
                    )
            expected_suffix = (
                ", "
                + ", ".join((*expected_definitions, *later_definitions))
                + base_constraint_suffixes[table_name]
            )
            if not table_sql[table_name].endswith(expected_suffix):
                raise evidence_ledger_inconsistent()
            base_version, expected_columns = base_columns[table_name]
            expected_columns += tuple(
                marker.split(".", 1)[1]
                for marker, introduced_version in (
                    _SCHEMA_COLUMN_INTRODUCED_VERSION.items()
                )
                if marker.startswith(f"column:{table_name}.")
                if base_version < introduced_version < 18
            )
            expected_columns += tuple(
                contract[1]
                for contract in alter_statements
                if contract[0] == table_name
            )
            expected_columns += later_columns
            column_rows = connection.execute(
                f"PRAGMA table_xinfo({_quoted_identifier(table_name)})"
            ).fetchall()
            if (
                tuple(str(row["name"]) for row in column_rows)
                != expected_columns
                or any(
                    int(row["cid"]) != ordinal or int(row["hidden"]) != 0
                    for ordinal, row in enumerate(column_rows)
                )
            ):
                raise evidence_ledger_inconsistent()
            table_columns[table_name] = {
                str(row["name"]): row for row in column_rows
            }
            table_foreign_keys[table_name] = _foreign_key_signatures(
                connection,
                table_name,
            )
        column_row = table_columns[table_name].get(column_name)
        if (
            column_row is None
            or str(column_row["type"]).upper() != expected_type
            or int(column_row["notnull"]) != expected_notnull
            or (
                None
                if column_row["dflt_value"] is None
                else _normalized_schema_sql(str(column_row["dflt_value"]))
            )
            != expected_default
            or int(column_row["pk"]) != expected_pk
        ):
            raise evidence_ledger_inconsistent()

        reference_match = re.search(
            r"\bREFERENCES\s+([a-z0-9_]+)\s*\(\s*([a-z0-9_]+)\s*\)",
            expected_definition,
            flags=re.IGNORECASE,
        )
        expected_foreign_keys = []
        if reference_match is not None:
            expected_foreign_keys.append(
                (
                    reference_match.group(1),
                    ((column_name, reference_match.group(2)),),
                    "NO ACTION",
                    "NO ACTION",
                    "NONE",
                )
            )
        actual_foreign_keys = [
            signature
            for signature in table_foreign_keys[table_name]
            if any(
                source_column == column_name
                for source_column, _target_column in signature[1]
            )
        ]
        if actual_foreign_keys != expected_foreign_keys:
            raise evidence_ledger_inconsistent()

    for (
        index_name,
        table_name,
        expected_columns,
        expected_unique,
        expected_partial,
        expected_sql,
    ) in index_statements:
        index_sql_row = connection.execute(
            "SELECT tbl_name, sql FROM sqlite_master "
            "WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        index_row = next(
            (
                row
                for row in connection.execute(
                    f"PRAGMA index_list({_quoted_identifier(table_name)})"
                ).fetchall()
                if str(row["name"]) == index_name
            ),
            None,
        )
        actual_columns = tuple(
            str(row["name"])
            for row in connection.execute(
                f"PRAGMA index_info({_quoted_identifier(index_name)})"
            ).fetchall()
        )
        if (
            index_sql_row is None
            or str(index_sql_row["tbl_name"]) != table_name
            or index_sql_row["sql"] is None
            or _normalized_schema_sql(str(index_sql_row["sql"])) != expected_sql
            or index_row is None
            or int(index_row["unique"]) != expected_unique
            or int(index_row["partial"]) != expected_partial
            or str(index_row["origin"]) != "c"
            or actual_columns != expected_columns
        ):
            raise evidence_ledger_inconsistent()

    expected_triggers = {
        match.group(1): _normalized_schema_sql(statement)
        for statement in statements
        if (
            match := re.match(
                r"\s*CREATE\s+TRIGGER\s+([a-z0-9_]+)\b",
                statement,
                flags=re.IGNORECASE,
            )
        )
    }
    actual_triggers = {
        str(row["name"]): _normalized_schema_sql(str(row["sql"]))
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        if str(row["name"]) in expected_triggers and row["sql"] is not None
    }
    if actual_triggers != expected_triggers:
        raise evidence_ledger_inconsistent()


def _snapshot_criterion_links(
    connection: sqlite3.Connection,
    snapshot_ids: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if snapshot_ids is None:
        rows = connection.execute(
            """
            SELECT project_id, task_id, authority_snapshot_id,
                   criterion_kind, criterion_id
              FROM authority_snapshot_criteria
             ORDER BY authority_snapshot_id, criterion_kind
            """
        ).fetchall()
    else:
        selected_json = json.dumps(
            sorted(snapshot_ids),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        rows = connection.execute(
            """
            WITH selected_snapshot_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT link.project_id, link.task_id,
                   link.authority_snapshot_id,
                   link.criterion_kind, link.criterion_id
              FROM authority_snapshot_criteria AS link
              JOIN selected_snapshot_ids AS selected
                ON selected.value = link.authority_snapshot_id
             ORDER BY link.authority_snapshot_id, link.criterion_kind
            """,
            (selected_json,),
        ).fetchall()
    for row in rows:
        project_id = row["project_id"]
        task_id = row["task_id"]
        snapshot_id = row["authority_snapshot_id"]
        kind = row["criterion_kind"]
        criterion_id = row["criterion_id"]
        if (
            type(project_id) is not str
            or not project_id
            or type(task_id) is not str
            or not task_id
            or type(snapshot_id) is not str
            or re.fullmatch(
                r"tg_authority_snapshot_[0-9a-f]{16}", snapshot_id
            )
            is None
            or type(kind) is not str
            or kind not in {"acceptance", "verification"}
            or type(criterion_id) is not str
            or re.fullmatch(
                r"tg_contract_criterion_[0-9a-f]{16}", criterion_id
            )
            is None
        ):
            raise evidence_ledger_inconsistent()
        links = result.setdefault(snapshot_id, {})
        if kind in links:
            raise evidence_ledger_inconsistent()
        links[kind] = criterion_id
    return result


def _validated_contract_revision_rows(
    rows: Any,
    *,
    expected_keys: set[tuple[str, str, int]],
) -> dict[tuple[str, str, int], sqlite3.Row]:
    """Validate the exact Contract revisions selected by current authority."""

    from task_governance_tool.contracts import _validate_stored_contract
    from task_governance_tool.tasks import TaskRepositoryError

    contracts: dict[tuple[str, str, int], sqlite3.Row] = {}
    try:
        for row in rows:
            contract_revision_id = row["contract_revision_id"]
            project_id = row["project_id"]
            task_id = row["task_id"]
            revision = row["revision"]
            created_at = row["created_at"]
            if (
                type(contract_revision_id) is not str
                or not contract_revision_id
                or type(project_id) is not str
                or not project_id
                or type(task_id) is not str
                or not task_id
                or type(revision) is not int
                or revision <= 0
                or revision > SQLITE_INT64_MAX
                or any(
                    type(row[field]) is not str
                    for field in (
                        "scope",
                        "acceptance",
                        "constraints_text",
                        "authority_ref",
                        "change_reason",
                    )
                )
                or type(created_at) is not str
            ):
                raise evidence_ledger_inconsistent()
            validate_utc_timestamp(
                created_at,
                field="Task Contract creation time",
            )
            _validate_stored_contract(
                row,
                project_id=project_id,
                task_id=task_id,
                revision=revision,
            )
            key = (project_id, task_id, revision)
            if key not in expected_keys or key in contracts:
                raise evidence_ledger_inconsistent()
            contracts[key] = row
    except (sqlite3.Error, StorageError, TaskRepositoryError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    if set(contracts) != expected_keys:
        raise evidence_ledger_inconsistent()
    return contracts


def validate_selected_task_authority_storage(
    connection: sqlite3.Connection,
    task_rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    *,
    expected_project_id: str,
    current_contract_rows: dict[str, sqlite3.Row | None],
) -> None:
    """Validate only the current authority basis for one selected Task batch."""

    if not task_rows:
        return
    if len(task_rows) > SELECTED_TASK_AUTHORITY_VALIDATION_CHUNK_SIZE:
        try:
            task_ids = [row["task_id"] for row in task_rows]
        except (IndexError, KeyError) as exc:
            raise evidence_ledger_boundary_error(exc) from exc
        if (
            any(type(task_id) is not str or not task_id for task_id in task_ids)
            or len(task_ids) != len(set(task_ids))
            or set(current_contract_rows) != set(task_ids)
        ):
            raise evidence_ledger_inconsistent()
        for offset in range(
            0,
            len(task_rows),
            SELECTED_TASK_AUTHORITY_VALIDATION_CHUNK_SIZE,
        ):
            chunk = task_rows[
                offset : offset + SELECTED_TASK_AUTHORITY_VALIDATION_CHUNK_SIZE
            ]
            chunk_task_ids = {row["task_id"] for row in chunk}
            validate_selected_task_authority_storage(
                connection,
                chunk,
                expected_project_id=expected_project_id,
                current_contract_rows={
                    task_id: current_contract_rows[task_id]
                    for task_id in chunk_task_ids
                },
            )
        return
    selected: dict[str, sqlite3.Row] = {}
    selected_task_ids: set[str] = set()
    selected_manifest_ids: set[str] = set()
    for task in task_rows:
        try:
            project_id = task["project_id"]
            task_id = task["task_id"]
            snapshot_id = task["current_authority_snapshot_id"]
            generation = task["current_authority_snapshot_generation"]
            capture_version = task["review_target_capture_version"]
            target_kind = task["review_target_kind"]
            target_value = task["review_target_value"]
            target_base_revision = task["review_target_base_revision"]
            target_generation = task["review_target_generation"]
            target_snapshot_id = task["review_target_authority_snapshot_id"]
            target_acceptance_id = task[
                "review_target_acceptance_criterion_id"
            ]
            target_verification_id = task[
                "review_target_verification_criterion_id"
            ]
            target_manifest_id = task["review_target_artifact_manifest_id"]
        except (IndexError, KeyError) as exc:
            raise evidence_ledger_boundary_error(exc) from exc
        if (
            type(project_id) is not str
            or project_id != expected_project_id
            or type(task_id) is not str
            or not task_id
            or task_id in selected_task_ids
            or type(snapshot_id) is not str
            or re.fullmatch(r"tg_authority_snapshot_[0-9a-f]{16}", snapshot_id)
            is None
            or type(generation) is not int
            or generation <= 0
            or snapshot_id in selected
            or type(capture_version) is not int
            or capture_version not in {0, 1}
        ):
            raise evidence_ledger_inconsistent()
        target_bindings = (
            target_snapshot_id,
            target_acceptance_id,
            target_verification_id,
            target_manifest_id,
        )
        if capture_version == 0:
            if any(value is not None for value in target_bindings):
                raise evidence_ledger_inconsistent()
        elif (
            type(target_kind) is not str
            or not target_kind
            or type(target_value) is not str
            or not target_value
            or type(target_base_revision) is not str
            or type(target_generation) is not int
            or target_generation <= 0
            or type(target_snapshot_id) is not str
            or re.fullmatch(
                r"tg_authority_snapshot_[0-9a-f]{16}", target_snapshot_id
            )
            is None
            or (
                target_acceptance_id is not None
                and (
                    type(target_acceptance_id) is not str
                    or re.fullmatch(
                        r"tg_contract_criterion_[0-9a-f]{16}",
                        target_acceptance_id,
                    )
                    is None
                )
            )
            or (
                target_verification_id is not None
                and (
                    type(target_verification_id) is not str
                    or re.fullmatch(
                        r"tg_contract_criterion_[0-9a-f]{16}",
                        target_verification_id,
                    )
                    is None
                )
            )
            or type(target_manifest_id) is not str
            or re.fullmatch(
                r"tg_artifact_manifest_[0-9a-f]{16}", target_manifest_id
            )
            is None
        ):
            raise evidence_ledger_inconsistent()
        else:
            selected_manifest_ids.add(target_manifest_id)
        selected[snapshot_id] = task
        selected_task_ids.add(task_id)

    if set(current_contract_rows) != selected_task_ids:
        raise evidence_ledger_inconsistent()

    selected_json = json.dumps(
        sorted(selected),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    try:
        snapshot_rows = connection.execute(
            """
            WITH selected_snapshot_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT * FROM authority_snapshots
             WHERE authority_snapshot_id IN (
                   SELECT value FROM selected_snapshot_ids
             )
             ORDER BY authority_snapshot_id
            """,
            (selected_json,),
        ).fetchall()
        link_rows = connection.execute(
            """
            WITH selected_snapshot_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT link.project_id AS link_project_id,
                   link.task_id AS link_task_id,
                   link.authority_snapshot_id,
                   link.criterion_kind AS link_kind,
                   link.criterion_id AS link_criterion_id,
                   criterion.project_id AS criterion_project_id,
                   criterion.task_id AS criterion_task_id,
                   criterion.criterion_kind,
                   criterion.criterion_text,
                   criterion.digest
              FROM authority_snapshot_criteria AS link
              LEFT JOIN contract_criteria AS criterion
                ON criterion.criterion_id = link.criterion_id
             WHERE link.authority_snapshot_id IN (
                   SELECT value FROM selected_snapshot_ids
             )
             ORDER BY link.authority_snapshot_id, link.criterion_kind
            """,
            (selected_json,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc

    snapshots: dict[str, sqlite3.Row] = {}
    for snapshot in snapshot_rows:
        snapshot_id = snapshot["authority_snapshot_id"]
        if type(snapshot_id) is not str or snapshot_id in snapshots:
            raise evidence_ledger_inconsistent()
        snapshots[snapshot_id] = snapshot
    links: dict[str, dict[str, sqlite3.Row]] = {
        snapshot_id: {} for snapshot_id in selected
    }
    for link in link_rows:
        snapshot_id = link["authority_snapshot_id"]
        kind = link["link_kind"]
        if (
            type(snapshot_id) is not str
            or snapshot_id not in links
            or kind not in {"acceptance", "verification"}
            or kind in links[snapshot_id]
        ):
            raise evidence_ledger_inconsistent()
        links[snapshot_id][kind] = link
    for snapshot_id, task in selected.items():
        snapshot = snapshots.get(snapshot_id)
        if snapshot is None:
            raise evidence_ledger_inconsistent()
        snapshot_links = links[snapshot_id]
        acceptance = snapshot_links.get("acceptance")
        verification = snapshot_links.get("verification")
        for kind, link in snapshot_links.items():
            criterion_id = link["link_criterion_id"]
            criterion_text = link["criterion_text"]
            if (
                type(criterion_id) is not str
                or re.fullmatch(
                    r"tg_contract_criterion_[0-9a-f]{16}",
                    criterion_id,
                )
                is None
                or link["link_project_id"] != expected_project_id
                or link["criterion_project_id"] != expected_project_id
                or link["link_task_id"] != task["task_id"]
                or link["criterion_task_id"] != task["task_id"]
                or link["criterion_kind"] != kind
                or type(criterion_text) is not str
                or link["digest"]
                != contract_criterion_digest(kind, criterion_text)
            ):
                raise evidence_ledger_inconsistent()

        task_verification = task["verification"]
        contract_revision = task["current_contract_revision"]
        contract_state = snapshot["contract_state"]
        contract = current_contract_rows[task["task_id"]]
        acceptance_id = (
            acceptance["link_criterion_id"] if acceptance is not None else None
        )
        verification_id = (
            verification["link_criterion_id"]
            if verification is not None
            else None
        )
        if contract is None:
            live_contract = ("contract_unspecified", "", "", "", "")
        else:
            try:
                live_contract = (
                    "contract_specified",
                    contract["scope"],
                    contract["acceptance"],
                    contract["constraints_text"],
                    contract["authority_ref"],
                )
                contract_identity = (
                    contract["project_id"],
                    contract["task_id"],
                    contract["revision"],
                )
            except (IndexError, KeyError) as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            if (
                contract_identity
                != (expected_project_id, task["task_id"], contract_revision)
                or type(contract_identity[0]) is not str
                or type(contract_identity[1]) is not str
                or type(contract_identity[2]) is not int
                or any(type(value) is not str for value in live_contract[1:])
            ):
                raise evidence_ledger_inconsistent()
        if (
            type(task_verification) is not str
            or type(contract_revision) is not int
            or contract_revision < 0
            or type(snapshot["generation"]) is not int
            or snapshot["generation"]
            != task["current_authority_snapshot_generation"]
            or snapshot["project_id"] != expected_project_id
            or snapshot["task_id"] != task["task_id"]
            or snapshot["task_title"] != task["title"]
            or snapshot["task_description"] != task["description"]
            or snapshot["review_tier"] != task["review_tier"]
            or snapshot["verification"] != task_verification
            or snapshot["verification_digest"]
            != _verification_expectation_digest(task_verification)
            or snapshot["contract_revision"] != contract_revision
            or (
                snapshot["contract_state"],
                snapshot["contract_scope"],
                snapshot["contract_acceptance"],
                snapshot["contract_constraints"],
                snapshot["contract_authority_ref"],
            )
            != live_contract
            or (bool(task_verification.strip()) != (verification is not None))
            or (
                verification is not None
                and verification["criterion_text"] != task_verification
            )
            or (
                acceptance is not None
                and acceptance["criterion_text"]
                != snapshot["contract_acceptance"]
            )
            or (
                contract_revision == 0
                and (
                    contract_state != "contract_unspecified"
                    or any(
                        snapshot[name]
                        for name in (
                            "contract_scope",
                            "contract_acceptance",
                            "contract_constraints",
                            "contract_authority_ref",
                        )
                    )
                    or acceptance is not None
                )
            )
            or (
                contract_revision > 0
                and (
                    contract_state != "contract_specified"
                    or type(snapshot["contract_acceptance"]) is not str
                    or not snapshot["contract_acceptance"]
                    or acceptance is None
                )
            )
        ):
            raise evidence_ledger_inconsistent()
        digest_values = {
            "project_id": expected_project_id,
            "task_id": task["task_id"],
            "task_title": snapshot["task_title"],
            "task_description": snapshot["task_description"],
            "review_tier": snapshot["review_tier"],
            "verification": snapshot["verification"],
            "verification_digest": snapshot["verification_digest"],
            "contract_revision": snapshot["contract_revision"],
            "contract_state": contract_state,
            "contract_scope": snapshot["contract_scope"],
            "contract_acceptance": snapshot["contract_acceptance"],
            "contract_constraints": snapshot["contract_constraints"],
            "contract_authority_ref": snapshot["contract_authority_ref"],
            "acceptance_criterion_id": acceptance_id,
            "verification_criterion_id": verification_id,
            "producer_class": snapshot["producer_class"],
            "producer_version": snapshot["producer_version"],
        }
        if snapshot["basis_digest"] != authority_snapshot_basis_digest(
            digest_values
        ):
            raise evidence_ledger_inconsistent()

    manifest_links = {
        snapshot_id: {
            kind: row["link_criterion_id"]
            for kind, row in snapshot_links.items()
        }
        for snapshot_id, snapshot_links in links.items()
    }
    manifests, manifests_by_target = _validate_artifact_manifest_storage(
        connection,
        snapshots=snapshots,
        links=manifest_links,
        manifest_ids=selected_manifest_ids,
    )
    for snapshot_id, task in selected.items():
        if task["review_target_capture_version"] != 1:
            continue
        manifest_id = task["review_target_artifact_manifest_id"]
        manifest_record = manifests.get(manifest_id)
        manifest = manifest_record.row if manifest_record is not None else None
        snapshot_links = manifest_links[snapshot_id]
        acceptance_id = snapshot_links.get("acceptance")
        verification_id = snapshot_links.get("verification")
        if (
            manifest is None
            or task["review_target_authority_snapshot_id"] != snapshot_id
            or task["review_target_acceptance_criterion_id"] != acceptance_id
            or task["review_target_verification_criterion_id"] != verification_id
            or manifest["project_id"] != expected_project_id
            or manifest["task_id"] != task["task_id"]
            or manifest["target_kind"] != task["review_target_kind"]
            or manifest["target_value"] != task["review_target_value"]
            or manifest["target_base_revision"]
            != task["review_target_base_revision"]
            or manifest["target_generation"] != task["review_target_generation"]
            or manifest["authority_snapshot_id"] != snapshot_id
            or manifest["acceptance_criterion_id"] != acceptance_id
            or manifest["verification_criterion_id"] != verification_id
        ):
            raise evidence_ledger_inconsistent()

    _validate_evidence_reference_storage(
        connection,
        manifests=manifests,
        manifests_by_target=manifests_by_target,
        snapshots=snapshots,
        verification_receipt_ids=set(),
        review_receipt_ids=set(),
        review_finding_ids=set(),
        completion_cycle_ids=set(),
        selected_project_id=expected_project_id,
    )
    _validate_selected_task_evidence_reference_inventory(
        connection,
        expected_project_id=expected_project_id,
        selected_task_ids=selected_task_ids,
    )


@dataclass(frozen=True)
class _ValidatedManifestRecord:
    row: dict[str, Any]
    binding: Any
    source: Any
    contract_revision: int


@dataclass(frozen=True)
class _ExpectedEvidenceReference:
    source: Any
    project_id: str
    task_id: str
    contract_revision: int
    binding: Any
    completion_cycle_id: str | None = None


def _artifact_manifest_target_key(
    *,
    project_id: object,
    task_id: object,
    target_kind: object,
    target_value: object,
    target_base_revision: object,
    target_generation: object,
) -> tuple[str, str, str, str, str, int]:
    if (
        type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(target_kind) is not str
        or type(target_value) is not str
        or type(target_base_revision) is not str
        or type(target_generation) is not int
        or target_generation <= 0
    ):
        raise evidence_ledger_inconsistent()
    return (
        project_id,
        task_id,
        target_kind,
        target_value,
        target_base_revision,
        target_generation,
    )


def _validate_artifact_manifest_storage(
    connection: sqlite3.Connection,
    *,
    snapshots: dict[str, sqlite3.Row],
    links: dict[str, dict[str, str]],
    manifest_ids: set[str] | None = None,
) -> tuple[
    dict[str, _ValidatedManifestRecord],
    dict[tuple[str, str, str, str, str, int], _ValidatedManifestRecord],
]:
    """Validate every bounded manifest and entry row from stored bytes."""

    from task_governance_tool.artifact_manifest import (
        ARTIFACT_ENTRY_LIMIT,
        ArtifactManifestEntry,
        ArtifactManifestError,
        ArtifactManifestSpec,
    )
    from task_governance_tool.evidence_ledger import (
        EvidenceLedgerError,
        EvidenceSource,
        TargetCaptureBinding,
        canonical_json_bytes,
    )

    by_id: dict[str, _ValidatedManifestRecord] = {}
    by_target: dict[
        tuple[str, str, str, str, str, int], _ValidatedManifestRecord
    ] = {}
    try:
        if manifest_ids is not None and (
            type(manifest_ids) is not set
            or len(manifest_ids) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            or any(
                type(manifest_id) is not str
                or len(manifest_id) > 128
                or ARTIFACT_MANIFEST_ID_PATTERN.fullmatch(manifest_id) is None
                for manifest_id in manifest_ids
            )
        ):
            raise evidence_ledger_inconsistent()
        manifest_rows: list[dict[str, Any]] = []
        manifest_headers: dict[str, dict[str, Any]] = {}
        declared_entry_total = 0
        if manifest_ids is None:
            stored_manifests = connection.execute(
                "SELECT * FROM artifact_manifests ORDER BY artifact_manifest_id"
            ).fetchall()
        else:
            selected_json = json.dumps(
                sorted(manifest_ids),
                ensure_ascii=True,
                separators=(",", ":"),
            )
            stored_manifests = connection.execute(
                """
                WITH selected_manifest_ids(value) AS (
                    SELECT value FROM json_each(?)
                ), selected_manifest_aliases(value) AS (
                    SELECT value FROM selected_manifest_ids
                    UNION ALL
                    SELECT CAST(value AS BLOB) FROM selected_manifest_ids
                )
                SELECT manifest.*
                  FROM artifact_manifests AS manifest
                  JOIN selected_manifest_aliases AS selected
                    ON selected.value = manifest.artifact_manifest_id
                 ORDER BY manifest.artifact_manifest_id
                 LIMIT ?
                """,
                (selected_json, len(manifest_ids) + 1),
            ).fetchall()
        for stored in stored_manifests:
            row = dict(stored)
            manifest_id = row["artifact_manifest_id"]
            if (
                type(manifest_id) is not str
                or re.fullmatch(
                    r"tg_artifact_manifest_[0-9a-f]{16}", manifest_id
                )
                is None
                or manifest_id in manifest_headers
                or type(row["entry_count"]) is not int
                or not 0 <= row["entry_count"] <= ARTIFACT_ENTRY_LIMIT
                or type(row["created_at"]) is not str
                or declared_entry_total
                > SQLITE_INT64_MAX - 1 - row["entry_count"]
            ):
                raise evidence_ledger_inconsistent()
            validate_utc_timestamp(
                row["created_at"],
                field="artifact manifest creation time",
            )
            manifest_rows.append(row)
            manifest_headers[manifest_id] = row
            declared_entry_total += row["entry_count"]

        if manifest_ids is None:
            entry_cursor = connection.execute(
                """
                SELECT * FROM artifact_manifest_entries
                 ORDER BY artifact_manifest_id, ordinal
                 LIMIT ?
                """,
                (declared_entry_total + 1,),
            )
        else:
            entry_cursor = connection.execute(
                """
                WITH selected_manifest_ids(value) AS (
                    SELECT value FROM json_each(?)
                ), selected_manifest_aliases(value) AS (
                    SELECT value FROM selected_manifest_ids
                    UNION ALL
                    SELECT CAST(value AS BLOB) FROM selected_manifest_ids
                )
                SELECT entry.*
                  FROM artifact_manifest_entries AS entry
                  JOIN selected_manifest_aliases AS selected
                    ON selected.value = entry.artifact_manifest_id
                 ORDER BY entry.artifact_manifest_id, entry.ordinal
                 LIMIT ?
                """,
                (selected_json, declared_entry_total + 1),
            )
        entry_iterator = iter(entry_cursor.fetchone, None)
        next_entry = next(entry_iterator, None)
        observed_entry_total = 0

        for row in manifest_rows:
            manifest_id = row["artifact_manifest_id"]
            entry_rows_buffer: list[sqlite3.Row] = []
            while next_entry is not None:
                entry_manifest_id = next_entry["artifact_manifest_id"]
                if type(entry_manifest_id) is not str:
                    raise evidence_ledger_inconsistent()
                if entry_manifest_id < manifest_id:
                    raise evidence_ledger_inconsistent()
                if entry_manifest_id != manifest_id:
                    break
                if (
                    next_entry["project_id"] != row["project_id"]
                    or next_entry["task_id"] != row["task_id"]
                    or type(next_entry["ordinal"]) is not int
                    or next_entry["ordinal"] != len(entry_rows_buffer)
                    or len(entry_rows_buffer) >= row["entry_count"]
                    or len(entry_rows_buffer) >= ARTIFACT_ENTRY_LIMIT
                ):
                    raise evidence_ledger_inconsistent()
                entry_rows_buffer.append(next_entry)
                observed_entry_total += 1
                next_entry = next(entry_iterator, None)
            entry_rows = tuple(entry_rows_buffer)
            if len(entry_rows) != row["entry_count"]:
                raise evidence_ledger_inconsistent()
            entries = tuple(
                ArtifactManifestEntry(
                    ordinal=entry["ordinal"],
                    kind=entry["entry_kind"],
                    old_path=entry["old_path"],
                    new_path=entry["new_path"],
                    before_mode=entry["before_mode"],
                    before_object_id=entry["before_object_id"],
                    after_mode=entry["after_mode"],
                    after_object_id=entry["after_object_id"],
                )
                for entry in entry_rows
            )
            canonical_value = {
                "acceptance_criterion_id": row["acceptance_criterion_id"],
                "authority_snapshot_id": row["authority_snapshot_id"],
                "comparison_base": row["comparison_base"],
                "entries": [entry.canonical_value() for entry in entries],
                "object_format": row["object_format"],
                "omission_code": row["omission_code"],
                "state": row["state"],
                "target_base_revision": row["target_base_revision"],
                "target_generation": row["target_generation"],
                "target_kind": row["target_kind"],
                "target_value": row["target_value"],
                "verification_criterion_id": row[
                    "verification_criterion_id"
                ],
            }
            spec = ArtifactManifestSpec(
                state=row["state"],
                object_format=row["object_format"],
                comparison_base=row["comparison_base"],
                target_kind=row["target_kind"],
                target_value=row["target_value"],
                target_base_revision=row["target_base_revision"],
                target_generation=row["target_generation"],
                authority_snapshot_id=row["authority_snapshot_id"],
                acceptance_criterion_id=row["acceptance_criterion_id"],
                verification_criterion_id=row[
                    "verification_criterion_id"
                ],
                omission_code=row["omission_code"],
                entries=entries,
                digest=row["digest"],
                canonical_size=len(canonical_json_bytes(canonical_value)),
            )
            binding = TargetCaptureBinding(
                target_kind=spec.target_kind,
                target_value=spec.target_value,
                target_base_revision=spec.target_base_revision,
                target_generation=spec.target_generation,
                authority_snapshot_id=spec.authority_snapshot_id,
                acceptance_criterion_id=spec.acceptance_criterion_id,
                verification_criterion_id=spec.verification_criterion_id,
            )
            snapshot = snapshots.get(spec.authority_snapshot_id)
            snapshot_links = links.get(spec.authority_snapshot_id, {})
            if (
                snapshot is None
                or snapshot["project_id"] != row["project_id"]
                or snapshot["task_id"] != row["task_id"]
                or snapshot_links.get("acceptance")
                != spec.acceptance_criterion_id
                or snapshot_links.get("verification")
                != spec.verification_criterion_id
                or type(snapshot["contract_revision"]) is not int
            ):
                raise evidence_ledger_inconsistent()
            source_projection = (
                {
                    "artifact_manifest_id": manifest_id,
                    "state": spec.state,
                    "object_format": spec.object_format,
                    "comparison_base": spec.comparison_base,
                    "entry_count": spec.entry_count,
                    "digest": spec.digest,
                    "omission_code": spec.omission_code,
                }
                if spec.state == "complete_git"
                else {
                    "artifact_manifest_id": manifest_id,
                    "state": spec.state,
                    "target_kind": spec.target_kind,
                    "digest": spec.digest,
                    "omission_code": spec.omission_code,
                }
            )
            source = EvidenceSource(
                source_kind="artifact_manifest",
                source_state=spec.state,
                source_id=manifest_id,
                source_projection=source_projection,
            )
            record = _ValidatedManifestRecord(
                row=row,
                binding=binding,
                source=source,
                contract_revision=snapshot["contract_revision"],
            )
            target_key = _artifact_manifest_target_key(
                project_id=row["project_id"],
                task_id=row["task_id"],
                target_kind=spec.target_kind,
                target_value=spec.target_value,
                target_base_revision=spec.target_base_revision,
                target_generation=spec.target_generation,
            )
            if target_key in by_target:
                raise evidence_ledger_inconsistent()
            by_id[manifest_id] = record
            by_target[target_key] = record
        if (
            next_entry is not None
            or observed_entry_total != declared_entry_total
            or (manifest_ids is not None and set(by_id) != manifest_ids)
        ):
            raise evidence_ledger_inconsistent()
    except (ArtifactManifestError, EvidenceLedgerError, StorageError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    return by_id, by_target


def _register_expected_evidence_reference(
    expected: dict[tuple[str, str], _ExpectedEvidenceReference],
    value: _ExpectedEvidenceReference,
) -> None:
    key = (value.source.source_kind, value.source.source_id)
    if key in expected:
        raise evidence_ledger_inconsistent()
    expected[key] = value


def _validate_stored_evidence_reference_row(
    row: sqlite3.Row,
    *,
    expected: dict[tuple[str, str], _ExpectedEvidenceReference],
    seen: set[tuple[str, str]],
) -> None:
    from task_governance_tool.evidence_ledger import build_evidence_reference

    reference_id = row["evidence_reference_id"]
    if (
        type(reference_id) is not str
        or re.fullmatch(
            r"tg_evidence_reference_[0-9a-f]{16}", reference_id
        )
        is None
        or type(row["source_kind"]) is not str
        or type(row["source_id"]) is not str
        or type(row["created_at"]) is not str
    ):
        raise evidence_ledger_inconsistent()
    validate_utc_timestamp(
        row["created_at"], field="Evidence Reference creation time"
    )
    key = (row["source_kind"], row["source_id"])
    value = expected.get(key)
    if value is None or key in seen:
        raise evidence_ledger_inconsistent()
    binding = value.binding
    if (
        row["project_id"] != value.project_id
        or row["task_id"] != value.task_id
        or row["source_state"] != value.source.source_state
        or row["contract_revision"] != value.contract_revision
        or row["authority_snapshot_id"] != binding.authority_snapshot_id
        or row["acceptance_criterion_id"] != binding.acceptance_criterion_id
        or row["verification_criterion_id"]
        != binding.verification_criterion_id
        or row["target_kind"] != binding.target_kind
        or row["target_value"] != binding.target_value
        or row["target_base_revision"] != binding.target_base_revision
        or row["target_generation"] != binding.target_generation
        or row["completion_cycle_id"] != value.completion_cycle_id
    ):
        raise evidence_ledger_inconsistent()
    rebuilt = build_evidence_reference(
        source=value.source,
        project_id=value.project_id,
        task_id=value.task_id,
        contract_revision=value.contract_revision,
        binding=binding,
        completion_cycle_id=value.completion_cycle_id,
    )
    if (
        row["assurance_class"] != rebuilt.attribution.assurance_class
        or row["producer_class"] != rebuilt.attribution.producer_class
        or row["producer_version"] != rebuilt.attribution.producer_version
        or row["digest"] != rebuilt.digest
    ):
        raise evidence_ledger_inconsistent()
    seen.add(key)


def _iter_validated_review_receipts_with_provenance(
    connection: sqlite3.Connection,
    receipt_ids: set[str] | None = None,
    *,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
):
    """Stream the closed Receipt/provenance/code relation in fixed queries."""

    review_privacy_successes = (
        privacy_success_cache if privacy_success_cache is not None else set()
    )
    if type(review_privacy_successes) is not set:
        raise evidence_ledger_inconsistent()
    if receipt_ids is None:
        count_row = connection.execute(
            "SELECT COUNT(*) AS count FROM review_receipts"
        ).fetchone()
        receipt_count = None if count_row is None else count_row["count"]
        selected_json = None
    else:
        receipt_count = len(receipt_ids)
        selected_json = json.dumps(
            sorted(receipt_ids),
            ensure_ascii=True,
            separators=(",", ":"),
        )
    if (
        type(receipt_count) is not int
        or receipt_count < 0
        or receipt_count >= SQLITE_INT64_MAX
    ):
        raise evidence_ledger_inconsistent()
    code_caps = _review_provenance_code_caps()
    per_receipt_code_cap = sum(code_caps.values())
    if receipt_count > (SQLITE_INT64_MAX - 1) // per_receipt_code_cap:
        raise evidence_ledger_inconsistent()
    global_code_cap = receipt_count * per_receipt_code_cap

    if selected_json is None:
        receipt_cursor = connection.execute(
            """
            SELECT * FROM review_receipts
             ORDER BY (review_provenance_id IS NOT NULL),
                      review_provenance_id,
                      review_receipt_id
             LIMIT ?
            """,
            (receipt_count + 1,),
        )
        provenance_cursor = connection.execute(
            """
            SELECT * FROM review_receipt_provenance
             ORDER BY review_provenance_id
             LIMIT ?
            """,
            (receipt_count + 1,),
        )
        provenance_iterator = iter(provenance_cursor.fetchone, None)
        code_cursor = connection.execute(
            """
            SELECT * FROM review_receipt_provenance_codes
             ORDER BY review_provenance_id,
                      CASE code_kind
                        WHEN 'profile' THEN 0
                        WHEN 'lens' THEN 1
                        WHEN 'method' THEN 2
                        ELSE 3
                      END,
                      ordinal
             LIMIT ?
            """,
            (global_code_cap + 1,),
        )
        code_iterator = iter(code_cursor.fetchone, None)
    else:
        receipt_cursor = connection.execute(
            """
            WITH selected_receipt_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT receipt.*
              FROM review_receipts AS receipt
              JOIN selected_receipt_ids AS selected
                ON selected.value = receipt.review_receipt_id
             ORDER BY (receipt.review_provenance_id IS NOT NULL),
                      receipt.review_provenance_id,
                      receipt.review_receipt_id
             LIMIT ?
            """,
            (selected_json, receipt_count + 1),
        )
        provenance_cursor = connection.execute(
            """
            WITH selected_receipt_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT provenance.*
              FROM review_receipt_provenance AS provenance
              JOIN selected_receipt_ids AS selected
                ON selected.value = provenance.review_receipt_id
             ORDER BY provenance.review_provenance_id
             LIMIT ?
            """,
            (selected_json, receipt_count + 1),
        )
        provenance_iterator = iter(provenance_cursor.fetchone, None)
        code_cursor = connection.execute(
            """
            WITH selected_receipt_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT code.*
              FROM review_receipt_provenance_codes AS code
              JOIN review_receipt_provenance AS provenance
                ON provenance.review_provenance_id = code.review_provenance_id
              JOIN selected_receipt_ids AS selected
                ON selected.value = provenance.review_receipt_id
             ORDER BY code.review_provenance_id,
                      CASE code.code_kind
                        WHEN 'profile' THEN 0
                        WHEN 'lens' THEN 1
                        WHEN 'method' THEN 2
                        ELSE 3
                      END,
                      code.ordinal
             LIMIT ?
            """,
            (selected_json, global_code_cap + 1),
        )
        code_iterator = iter(code_cursor.fetchone, None)
    next_provenance = next(provenance_iterator, None)
    next_code = next(code_iterator, None)
    observed_receipts = 0
    for receipt in iter(receipt_cursor.fetchone, None):
        observed_receipts += 1
        _validate_review_receipt_base_row(
            receipt,
            privacy_success_cache=review_privacy_successes,
        )
        receipt_id = receipt["review_receipt_id"]
        basis = receipt["review_provenance_basis_version"]
        provenance_id = receipt["review_provenance_id"]
        if (
            observed_receipts > receipt_count
            or type(receipt_id) is not str
            or not receipt_id
            or type(basis) is not int
            or basis not in {0, 1}
        ):
            raise evidence_ledger_inconsistent()
        if basis == 0:
            if provenance_id is not None:
                raise evidence_ledger_inconsistent()
            yield receipt, None
            continue
        if (
            receipt["receipt_kind"] == "not_required"
            or type(provenance_id) is not str
            or not provenance_id
            or next_provenance is None
        ):
            raise evidence_ledger_inconsistent()
        stored_provenance_id = next_provenance["review_provenance_id"]
        if (
            type(stored_provenance_id) is not str
            or stored_provenance_id != provenance_id
        ):
            raise evidence_ledger_inconsistent()

        code_rows: list[sqlite3.Row] = []
        while next_code is not None:
            code_provenance_id = next_code["review_provenance_id"]
            if type(code_provenance_id) is not str:
                raise evidence_ledger_inconsistent()
            if code_provenance_id < provenance_id:
                raise evidence_ledger_inconsistent()
            if code_provenance_id != provenance_id:
                break
            if len(code_rows) >= per_receipt_code_cap:
                raise evidence_ledger_inconsistent()
            code_rows.append(next_code)
            next_code = next(code_iterator, None)

        validated = _validate_review_provenance_relation(
            receipt,
            next_provenance,
            tuple(code_rows),
        )
        next_provenance = next(provenance_iterator, None)
        yield receipt, validated
    if (
        observed_receipts != receipt_count
        or next_provenance is not None
        or next_code is not None
    ):
        raise evidence_ledger_inconsistent()


def _selected_storage_rows_by_ids(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    id_field: str,
    selected_ids: set[str] | None,
) -> list[sqlite3.Row]:
    if selected_ids is None:
        return connection.execute(
            f"SELECT * FROM {_quoted_identifier(table_name)} "
            f"ORDER BY {_quoted_identifier(id_field)}"
        ).fetchall()
    selected_json = json.dumps(
        sorted(selected_ids),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    rows = connection.execute(
        f"""
        WITH selected_ids(value) AS (
            SELECT value FROM json_each(?)
        )
        SELECT stored.*
          FROM {_quoted_identifier(table_name)} AS stored
          JOIN selected_ids AS selected
            ON selected.value = stored.{_quoted_identifier(id_field)}
         ORDER BY stored.{_quoted_identifier(id_field)}
        """,
        (selected_json,),
    ).fetchall()
    if {
        row[id_field] for row in rows if type(row[id_field]) is str
    } != selected_ids:
        raise evidence_ledger_inconsistent()
    return rows


def _validate_review_finding_base_row(
    row: sqlite3.Row,
    *,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    finding_id = row["review_finding_id"]
    receipt_id = row["review_receipt_id"]
    severity = row["severity"]
    status = row["status"]
    summary = row["summary"]
    resolution_summary = row["resolution_summary"]
    created_at = row["created_at"]
    resolved_at = row["resolved_at"]
    if (
        type(finding_id) is not str
        or not 1 <= len(finding_id) <= 128
        or type(receipt_id) is not str
        or not 1 <= len(receipt_id) <= 128
        or type(severity) is not str
        or severity not in {"high", "medium", "low"}
        or type(status) is not str
        or status not in {"open", "resolved"}
        or type(summary) is not str
        or type(resolution_summary) is not str
        or type(created_at) is not str
        or (
            status == "open"
            and (resolution_summary != "" or resolved_at is not None)
        )
        or (
            status == "resolved"
            and (
                not resolution_summary
                or type(resolved_at) is not str
            )
        )
    ):
        raise evidence_ledger_inconsistent()
    cache = privacy_success_cache if privacy_success_cache is not None else set()
    if type(cache) is not set:
        raise evidence_ledger_inconsistent()
    _validate_evidence_ledger_stored_privacy(
        "review_finding_summary",
        summary,
        privacy_success_cache=cache,
    )
    _validate_evidence_ledger_stored_privacy(
        "review_finding_resolution",
        resolution_summary,
        privacy_success_cache=cache,
    )
    if (
        not 1 <= len(summary) <= 1_000
        or summary != summary.strip()
        or len(resolution_summary) > 1_000
        or (
            status == "resolved"
            and resolution_summary != resolution_summary.strip()
        )
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            created_at,
            field="Review Finding creation time",
        )
        if status == "resolved":
            validate_utc_timestamp(
                resolved_at,
                field="Review Finding resolution time",
            )
    except StorageError as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def _validate_evidence_reference_storage(
    connection: sqlite3.Connection,
    *,
    manifests: dict[str, _ValidatedManifestRecord],
    manifests_by_target: dict[
        tuple[str, str, str, str, str, int], _ValidatedManifestRecord
    ],
    snapshots: dict[str, sqlite3.Row],
    verification_receipt_ids: set[str] | None = None,
    review_receipt_ids: set[str] | None = None,
    review_finding_ids: set[str] | None = None,
    completion_cycle_ids: set[str] | None = None,
    selected_project_id: str | None = None,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Rebuild every native source and require its one exact Reference."""

    from task_governance_tool.evidence_ledger import (
        EvidenceLedgerError,
        EvidenceSource,
    )

    expected: dict[tuple[str, str], _ExpectedEvidenceReference] = {}
    review_privacy_successes = (
        privacy_success_cache if privacy_success_cache is not None else set()
    )
    if type(review_privacy_successes) is not set:
        raise evidence_ledger_inconsistent()
    selected_source_owners: set[tuple[str, str, str, str]] = set()
    verification_receipts_by_id: dict[str, dict[str, Any]] = {}
    review_sources: dict[
        str, tuple[dict[str, Any], _ValidatedManifestRecord | None, bool]
    ] = {}
    selection_mode = (
        verification_receipt_ids is not None
        or review_receipt_ids is not None
        or review_finding_ids is not None
        or completion_cycle_ids is not None
    )
    if selection_mode and (
        verification_receipt_ids is None
        or review_receipt_ids is None
        or review_finding_ids is None
        or completion_cycle_ids is None
        or type(selected_project_id) is not str
        or not selected_project_id
    ):
        raise evidence_ledger_inconsistent()
    if not selection_mode and selected_project_id is not None:
        raise evidence_ledger_inconsistent()
    try:
        for manifest in manifests.values():
            if selection_mode:
                selected_source_owners.add(
                    (
                        manifest.row["project_id"],
                        manifest.row["task_id"],
                        "artifact_manifest",
                        manifest.source.source_id,
                    )
                )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=manifest.source,
                    project_id=manifest.row["project_id"],
                    task_id=manifest.row["task_id"],
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                ),
            )

        for stored in _selected_storage_rows_by_ids(
            connection,
            table_name="verification_receipts",
            id_field="verification_receipt_id",
            selected_ids=verification_receipt_ids,
        ):
            try:
                receipt = _validate_verification_receipt_row(dict(stored))
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            receipt_id = receipt["verification_receipt_id"]
            if receipt_id in verification_receipts_by_id:
                raise evidence_ledger_inconsistent()
            verification_receipts_by_id[receipt_id] = receipt
            if selection_mode:
                selected_source_owners.add(
                    (
                        receipt["project_id"],
                        receipt["task_id"],
                        "verification_receipt",
                        receipt_id,
                    )
                )
            basis = receipt["verification_subject_basis_version"]
            manifest = manifests_by_target.get(
                _artifact_manifest_target_key(
                    project_id=receipt["project_id"],
                    task_id=receipt["task_id"],
                    target_kind=receipt["target_kind"],
                    target_value=receipt["target_value"],
                    target_base_revision=receipt["target_base_revision"],
                    target_generation=receipt["target_generation"],
                )
            )
            if basis == 0:
                if manifest is not None:
                    raise evidence_ledger_inconsistent()
                continue
            if manifest is None:
                raise evidence_ledger_inconsistent()
            snapshot = snapshots.get(manifest.binding.authority_snapshot_id)
            if (
                receipt["subject_authority_snapshot_id"]
                != manifest.binding.authority_snapshot_id
                or receipt["subject_verification_criterion_id"]
                != manifest.binding.verification_criterion_id
                or receipt["contract_revision"] != manifest.contract_revision
                or snapshot is None
                or receipt["verification_expectation_digest"]
                != snapshot["verification_digest"]
            ):
                raise evidence_ledger_inconsistent()
            source = EvidenceSource(
                source_kind="verification_receipt",
                source_state="recorded",
                source_id=receipt["verification_receipt_id"],
                source_projection={
                    "verification_receipt_id": receipt[
                        "verification_receipt_id"
                    ],
                    "subject_basis_version": basis,
                    "authority_snapshot_id": receipt[
                        "subject_authority_snapshot_id"
                    ],
                    "verification_criterion_id": receipt[
                        "subject_verification_criterion_id"
                    ],
                    "result": receipt["result"],
                    "duration_ms": receipt["duration_ms"],
                    "scope_coverage": receipt["scope_coverage"],
                    "created_at": receipt["created_at"],
                },
            )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=source,
                    project_id=receipt["project_id"],
                    task_id=receipt["task_id"],
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                ),
            )

        for stored, provenance in (
            _iter_validated_review_receipts_with_provenance(
                connection,
                review_receipt_ids,
                privacy_success_cache=review_privacy_successes,
            )
        ):
            receipt_id = stored["review_receipt_id"]
            if (
                type(receipt_id) is not str
                or not receipt_id
                or type(stored["project_id"]) is not str
                or not stored["project_id"]
                or type(stored["task_id"]) is not str
                or not stored["task_id"]
                or type(stored["reviewer_key"]) is not str
                or not stored["reviewer_key"]
                or type(stored["summary"]) is not str
                or type(stored["user_approved"]) is not int
                or stored["user_approved"] not in {0, 1}
                or type(stored["created_at"]) is not str
            ):
                raise evidence_ledger_inconsistent()
            try:
                validate_utc_timestamp(
                    stored["created_at"], field="Review Receipt creation time"
                )
                _validate_completion_target(
                    kind=stored["target_kind"],
                    value=stored["target_value"],
                    base_revision=stored["target_base_revision"],
                    generation=stored["target_generation"],
                )
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            manifest = manifests_by_target.get(
                _artifact_manifest_target_key(
                    project_id=stored["project_id"],
                    task_id=stored["task_id"],
                    target_kind=stored["target_kind"],
                    target_value=stored["target_value"],
                    target_base_revision=stored["target_base_revision"],
                    target_generation=stored["target_generation"],
                )
            )
            basis = stored["review_provenance_basis_version"]
            kind = stored["receipt_kind"]
            native = basis == 1 or (kind == "not_required" and manifest is not None)
            if (
                type(basis) is not int
                or basis not in {0, 1}
                or (basis == 1 and (kind == "not_required" or manifest is None))
                or (
                    basis == 0
                    and kind in {"independent", "self_review_fallback"}
                    and manifest is not None
                )
            ):
                raise evidence_ledger_inconsistent()
            if native:
                assert manifest is not None
                snapshot = snapshots.get(manifest.binding.authority_snapshot_id)
                if snapshot is None:
                    raise evidence_ledger_inconsistent()
                _validate_native_review_receipt_tier(
                    stored,
                    review_tier=snapshot["review_tier"],
                )
            receipt = dict(stored)
            review_sources[receipt_id] = (receipt, manifest, native)
            if selection_mode:
                selected_source_owners.add(
                    (
                        receipt["project_id"],
                        receipt["task_id"],
                        "review_receipt",
                        receipt_id,
                    )
                )
            if not native:
                continue
            assert manifest is not None
            source = EvidenceSource(
                source_kind="review_receipt",
                source_state="recorded",
                source_id=receipt_id,
                source_projection={
                    "review_receipt_id": receipt_id,
                    "reviewer_key": receipt["reviewer_key"],
                    "receipt_kind": kind,
                    "verdict": receipt["verdict"],
                    "summary": receipt["summary"],
                    "user_approved": receipt["user_approved"],
                    "created_at": receipt["created_at"],
                    "review_provenance": provenance,
                },
            )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=source,
                    project_id=receipt["project_id"],
                    task_id=receipt["task_id"],
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                ),
            )

        finding_rows = (
            _selected_storage_rows_by_ids(
                connection,
                table_name="review_findings",
                id_field="review_finding_id",
                selected_ids=review_finding_ids,
            )
            if selection_mode
            else iter(
                connection.execute(
                    "SELECT * FROM review_findings ORDER BY review_finding_id"
                ).fetchone,
                None,
            )
        )
        for stored in finding_rows:
            _validate_review_finding_base_row(
                stored,
                privacy_success_cache=review_privacy_successes,
            )
            finding_id = stored["review_finding_id"]
            receipt_id = stored["review_receipt_id"]
            receipt_record = review_sources.get(receipt_id)
            if receipt_record is None:
                raise evidence_ledger_inconsistent()
            receipt, manifest, native = receipt_record
            if selection_mode:
                selected_source_owners.add(
                    (
                        receipt["project_id"],
                        receipt["task_id"],
                        "review_finding",
                        finding_id,
                    )
                )
            if not native:
                continue
            assert manifest is not None
            source = EvidenceSource(
                source_kind="review_finding",
                source_state="recorded",
                source_id=finding_id,
                source_projection={
                    "review_finding_id": finding_id,
                    "review_receipt_id": receipt_id,
                    "severity": stored["severity"],
                    "summary": stored["summary"],
                    "created_at": stored["created_at"],
                },
            )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=source,
                    project_id=receipt["project_id"],
                    task_id=receipt["task_id"],
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                ),
            )

        cycle_rows = (
            _selected_storage_rows_by_ids(
                connection,
                table_name="task_completion_cycles",
                id_field="completion_cycle_id",
                selected_ids=completion_cycle_ids,
            )
            if selection_mode
            else connection.execute(
                "SELECT * FROM task_completion_cycles ORDER BY completion_cycle_id"
            )
        )
        for stored in cycle_rows:
            try:
                cycle = _cycle_from_row(stored)
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            if selection_mode:
                selected_source_owners.add(
                    (
                        cycle.project_id,
                        cycle.task_id,
                        "completion_evidence",
                        cycle.completion_cycle_id,
                    )
                )
            linked_verification_receipt = (
                verification_receipts_by_id.get(
                    cycle.verification_receipt_id
                )
                if cycle.verification_receipt_id is not None
                else None
            )
            try:
                _validate_cycle_verification_receipt_projection(
                    cycle,
                    linked_verification_receipt,
                    validate_complete_receipt=False,
                )
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            manifest = None
            if cycle.review_target_generation > 0:
                manifest = manifests_by_target.get(
                    _artifact_manifest_target_key(
                        project_id=cycle.project_id,
                        task_id=cycle.task_id,
                        target_kind=cycle.review_target_kind,
                        target_value=cycle.review_target_value,
                        target_base_revision=cycle.review_target_base_revision,
                        target_generation=cycle.review_target_generation,
                    )
                )
            if cycle.verification_subject_basis_version == 0:
                if manifest is not None:
                    raise evidence_ledger_inconsistent()
                continue
            if (
                manifest is None
                or cycle.contract_revision != manifest.contract_revision
                or (
                    cycle.verification_expectation == "specified"
                    and (
                        cycle.subject_authority_snapshot_id
                        != manifest.binding.authority_snapshot_id
                        or cycle.subject_verification_criterion_id
                        != manifest.binding.verification_criterion_id
                    )
                )
                or (
                    cycle.verification_expectation == "unspecified"
                    and manifest.binding.verification_criterion_id is not None
                )
            ):
                raise evidence_ledger_inconsistent()
            source = EvidenceSource(
                source_kind="completion_evidence",
                source_state=cycle.completion_evidence_kind,
                source_id=cycle.completion_cycle_id,
                source_projection={
                    "completion_cycle_id": cycle.completion_cycle_id,
                    "completed_at": cycle.completed_at,
                    "completion_evidence_kind": cycle.completion_evidence_kind,
                    "completion_evidence_revision": cycle.completion_evidence_revision,
                    "completion_evidence_reason": cycle.completion_evidence_reason,
                    "external_revision_approved": int(
                        cycle.external_revision_approved
                    ),
                    "completion_commit_required": int(
                        cycle.completion_commit_required
                    ),
                    "completion_commit_hash": cycle.completion_commit_hash,
                },
            )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=source,
                    project_id=cycle.project_id,
                    task_id=cycle.task_id,
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                    completion_cycle_id=cycle.completion_cycle_id,
                ),
            )

        if not selection_mode:
            for runner_reference in _validated_verification_runner_references(
                connection
            ):
                _register_expected_evidence_reference(
                    expected,
                    runner_reference,
                )

        reference_cursor: sqlite3.Cursor | None = None
        if selection_mode:
            assert verification_receipt_ids is not None
            assert review_receipt_ids is not None
            assert review_finding_ids is not None
            assert completion_cycle_ids is not None
            selected_source_keys = {
                (source_kind, source_id)
                for source_project_id, _, source_kind, source_id
                in selected_source_owners
                if source_project_id == selected_project_id
            }
            if len(selected_source_keys) != len(selected_source_owners):
                raise evidence_ledger_inconsistent()
            selected_sources_json = json.dumps(
                [list(key) for key in sorted(selected_source_keys)],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            # A same-source Reference owned by another valid Task is corruption.
            # Enumerating this project's Task owners keeps the owner-leading
            # Reference index seekable; one row beyond the expected set is
            # sufficient to prove inconsistency and bounds materialization.
            reference_rows = connection.execute(
                """
                WITH selected_sources(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT reference.*
                  FROM selected_sources AS selected
                 CROSS JOIN tasks AS owner
                       INDEXED BY idx_tasks_project_task_identity
                 CROSS JOIN evidence_references AS reference
                       INDEXED BY idx_evidence_references_source
                 WHERE owner.project_id = ?
                   AND reference.project_id = owner.project_id
                   AND reference.task_id = owner.task_id
                   AND reference.source_kind =
                         json_extract(selected.value, '$[0]')
                   AND reference.source_id =
                         json_extract(selected.value, '$[1]')
                 LIMIT ?
                """,
                (selected_sources_json, selected_project_id, len(expected) + 1),
            ).fetchall()
        else:
            reference_cursor = connection.execute(
                "SELECT * FROM evidence_references "
                "ORDER BY evidence_reference_id"
            )
            reference_rows = iter(reference_cursor.fetchone, None)
        seen: set[tuple[str, str]] = set()
        try:
            for row in reference_rows:
                _validate_stored_evidence_reference_row(
                    row,
                    expected=expected,
                    seen=seen,
                )
        finally:
            if reference_cursor is not None:
                reference_cursor.close()

        if seen != set(expected):
            raise evidence_ledger_inconsistent()
    except (EvidenceLedgerError, StorageError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def _validate_selected_reference_source_chunk(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str,
    selected_task_ids: set[str],
    source_owners: dict[tuple[str, str], str],
) -> None:
    """Validate one bounded all-kind Reference chunk structurally."""

    if (
        not source_owners
        or len(source_owners) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
    ):
        if source_owners:
            raise evidence_ledger_inconsistent()
        return

    artifact_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "artifact_manifest"
    }
    verification_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "verification_receipt"
    }
    review_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "review_receipt"
    }
    finding_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "review_finding"
    }
    cycle_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "completion_evidence"
    }
    runner_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "runner_observation"
    }
    source_schema_version = current_schema_version(connection)
    if source_schema_version < PRIVATE_SCHEMA20_VERSION and runner_owners:
        raise evidence_ledger_inconsistent()

    finding_rows = _selected_storage_rows_by_ids(
        connection,
        table_name="review_findings",
        id_field="review_finding_id",
        selected_ids=set(finding_owners),
    )
    for row in finding_rows:
        finding_id = row["review_finding_id"]
        expected_task_id = finding_owners.get(finding_id)
        parent_receipt_id = row["review_receipt_id"]
        if (
            type(finding_id) is not str
            or len(finding_id) > 128
            or REVIEW_FINDING_ID_PATTERN.fullmatch(finding_id) is None
            or expected_task_id not in selected_task_ids
            or type(parent_receipt_id) is not str
            or len(parent_receipt_id) > 128
            or REVIEW_RECEIPT_ID_PATTERN.fullmatch(parent_receipt_id) is None
            or (
                parent_receipt_id in review_owners
                and review_owners[parent_receipt_id] != expected_task_id
            )
        ):
            raise evidence_ledger_inconsistent()
        review_owners[parent_receipt_id] = expected_task_id
    if len(review_owners) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE:
        raise evidence_ledger_inconsistent()

    source_specs = (
        (
            "artifact_manifest",
            "artifact_manifests",
            "artifact_manifest_id",
            ARTIFACT_MANIFEST_ID_PATTERN,
            artifact_owners,
        ),
        (
            "verification_receipt",
            "verification_receipts",
            "verification_receipt_id",
            VERIFICATION_RECEIPT_ID_PATTERN,
            verification_owners,
        ),
        (
            "review_receipt",
            "review_receipts",
            "review_receipt_id",
            REVIEW_RECEIPT_ID_PATTERN,
            review_owners,
        ),
        (
            "completion_evidence",
            "task_completion_cycles",
            "completion_cycle_id",
            COMPLETION_CYCLE_ID_PATTERN,
            cycle_owners,
        ),
        *(
            (
                (
                    "runner_observation",
                    "verification_runner_observations",
                    "verification_runner_observation_id",
                    VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN,
                    runner_owners,
                ),
            )
            if source_schema_version >= PRIVATE_SCHEMA20_VERSION
            else ()
        ),
    )
    not_required_target_keys: set[tuple[str, str, str, str, str, int]] = set()
    for source_kind, table_name, id_field, pattern, owners in source_specs:
        rows = _selected_storage_rows_by_ids(
            connection,
            table_name=table_name,
            id_field=id_field,
            selected_ids=set(owners),
        )
        for row in rows:
            source_id = row[id_field]
            expected_task_id = owners.get(source_id)
            if (
                type(source_id) is not str
                or len(source_id) > 128
                or pattern.fullmatch(source_id) is None
                or expected_task_id not in selected_task_ids
                or row["project_id"] != expected_project_id
                or row["task_id"] != expected_task_id
            ):
                raise evidence_ledger_inconsistent()
            if source_kind == "review_receipt":
                basis = row["review_provenance_basis_version"]
                receipt_kind = row["receipt_kind"]
                if (
                    type(basis) is not int
                    or basis not in {0, 1}
                    or type(receipt_kind) is not str
                    or (
                        basis == 1
                        and receipt_kind
                        not in {"independent", "self_review_fallback"}
                    )
                    or (basis == 0 and receipt_kind != "not_required")
                ):
                    raise evidence_ledger_inconsistent()
                if basis == 0:
                    try:
                        _validate_completion_target(
                            kind=row["target_kind"],
                            value=row["target_value"],
                            base_revision=row["target_base_revision"],
                            generation=row["target_generation"],
                        )
                    except StorageError as exc:
                        raise evidence_ledger_boundary_error(exc) from exc
                    not_required_target_keys.add(
                        _artifact_manifest_target_key(
                            project_id=row["project_id"],
                            task_id=row["task_id"],
                            target_kind=row["target_kind"],
                            target_value=row["target_value"],
                            target_base_revision=row["target_base_revision"],
                            target_generation=row["target_generation"],
                        )
                    )
            elif source_kind == "verification_receipt":
                if (
                    type(row["verification_subject_basis_version"]) is not int
                    or row["verification_subject_basis_version"] != 1
                ):
                    raise evidence_ledger_inconsistent()
            elif source_kind == "completion_evidence":
                if (
                    type(row["verification_subject_basis_version"]) is not int
                    or row["verification_subject_basis_version"] != 1
                ):
                    raise evidence_ledger_inconsistent()
            elif source_kind == "runner_observation":
                admitted_eligibility_versions = (
                    {0, 1}
                    if source_schema_version in {
                        PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
                    }
                    else {0}
                )
                if (
                    type(row["gate_eligibility_version"]) is not int
                    or row["gate_eligibility_version"]
                    not in admitted_eligibility_versions
                    or row["route"] not in {"runner", "m21_fallback"}
                    or row["launch_state"] not in {"launched", "no_launch"}
                ):
                    raise evidence_ledger_inconsistent()

    if not_required_target_keys:
        targets_json = json.dumps(
            [list(key) for key in sorted(not_required_target_keys)],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        target_manifest_rows = connection.execute(
            """
            WITH selected_targets(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT manifest.artifact_manifest_id, manifest.project_id,
                   manifest.task_id, manifest.target_kind,
                   manifest.target_value, manifest.target_base_revision,
                   manifest.target_generation
              FROM artifact_manifests AS manifest
              JOIN selected_targets AS selected
                ON manifest.project_id = json_extract(selected.value, '$[0]')
               AND manifest.task_id = json_extract(selected.value, '$[1]')
               AND manifest.target_kind = json_extract(selected.value, '$[2]')
               AND manifest.target_value = json_extract(selected.value, '$[3]')
               AND manifest.target_base_revision = json_extract(selected.value, '$[4]')
               AND manifest.target_generation = json_extract(selected.value, '$[5]')
             ORDER BY manifest.artifact_manifest_id
             LIMIT ?
            """,
            (targets_json, len(not_required_target_keys) + 1),
        ).fetchall()
        observed_target_keys: set[
            tuple[str, str, str, str, str, int]
        ] = set()
        for row in target_manifest_rows:
            target_key = _artifact_manifest_target_key(
                project_id=row["project_id"],
                task_id=row["task_id"],
                target_kind=row["target_kind"],
                target_value=row["target_value"],
                target_base_revision=row["target_base_revision"],
                target_generation=row["target_generation"],
            )
            if (
                type(row["artifact_manifest_id"]) is not str
                or ARTIFACT_MANIFEST_ID_PATTERN.fullmatch(
                    row["artifact_manifest_id"]
                )
                is None
                or target_key not in not_required_target_keys
                or target_key in observed_target_keys
            ):
                raise evidence_ledger_inconsistent()
            observed_target_keys.add(target_key)
        if observed_target_keys != not_required_target_keys:
            raise evidence_ledger_inconsistent()

    selected_sources_json = json.dumps(
        [list(key) for key in sorted(source_owners)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    reference_rows = connection.execute(
        """
        WITH selected_sources(value) AS (
            SELECT value FROM json_each(?)
        )
        SELECT reference.evidence_reference_id, reference.project_id,
               reference.task_id, reference.source_kind, reference.source_id
          FROM selected_sources AS selected
         CROSS JOIN tasks AS owner
               INDEXED BY idx_tasks_project_task_identity
         CROSS JOIN evidence_references AS reference
               INDEXED BY idx_evidence_references_source
         WHERE owner.project_id = ?
           AND reference.project_id = owner.project_id
           AND reference.task_id = owner.task_id
           AND reference.source_kind = json_extract(selected.value, '$[0]')
           AND reference.source_id = json_extract(selected.value, '$[1]')
         LIMIT ?
        """,
        (selected_sources_json, expected_project_id, len(source_owners) + 1),
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for row in reference_rows:
        reference_id = row["evidence_reference_id"]
        source_kind = row["source_kind"]
        source_id = row["source_id"]
        key = (source_kind, source_id)
        expected_task_id = source_owners.get(key)
        if (
            type(reference_id) is not str
            or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(reference_id) is None
            or type(source_kind) is not str
            or type(source_id) is not str
            or expected_task_id is None
            or key in seen
            or row["project_id"] != expected_project_id
            or row["task_id"] != expected_task_id
        ):
            raise evidence_ledger_inconsistent()
        seen.add(key)
    if seen != set(source_owners):
        raise evidence_ledger_inconsistent()


_SELECTED_TASK_EVIDENCE_REFERENCE_INVENTORY_SQL = """
    WITH selected_task_ids(value) AS (
        SELECT value FROM json_each(?)
    ), selected_task_aliases(value) AS (
        SELECT value FROM selected_task_ids
        UNION ALL
        SELECT CAST(value AS BLOB) FROM selected_task_ids
    ), selected_project_aliases(value) AS (
        SELECT ?
        UNION ALL
        SELECT CAST(? AS BLOB)
    )
    SELECT reference.*
      FROM selected_project_aliases AS selected_project
     CROSS JOIN selected_task_aliases AS selected_task
     CROSS JOIN evidence_references AS reference
           INDEXED BY idx_evidence_references_source
     WHERE reference.project_id = selected_project.value
       AND reference.task_id = selected_task.value
"""


def _validate_selected_task_evidence_reference_inventory(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str,
    selected_task_ids: set[str],
) -> None:
    """Stream every Reference owned by one bounded selected Task batch."""

    if (
        type(expected_project_id) is not str
        or not expected_project_id
        or type(selected_task_ids) is not set
        or not selected_task_ids
        or len(selected_task_ids) > SELECTED_TASK_AUTHORITY_VALIDATION_CHUNK_SIZE
        or any(type(task_id) is not str or not task_id for task_id in selected_task_ids)
    ):
        raise evidence_ledger_inconsistent()
    selected_json = json.dumps(
        sorted(selected_task_ids),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    source_id_patterns = {
        "artifact_manifest": ARTIFACT_MANIFEST_ID_PATTERN,
        "verification_receipt": VERIFICATION_RECEIPT_ID_PATTERN,
        "review_receipt": REVIEW_RECEIPT_ID_PATTERN,
        "review_finding": REVIEW_FINDING_ID_PATTERN,
        "completion_evidence": COMPLETION_CYCLE_ID_PATTERN,
        "runner_observation": VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN,
    }
    reference_cursor: sqlite3.Cursor | None = None
    try:
        reference_cursor = connection.execute(
            _SELECTED_TASK_EVIDENCE_REFERENCE_INVENTORY_SQL,
            (selected_json, expected_project_id, expected_project_id),
        )
        while True:
            rows = reference_cursor.fetchmany(
                COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            )
            if not rows:
                break
            source_owners: dict[tuple[str, str], str] = {}
            for row in rows:
                reference_id = row["evidence_reference_id"]
                project_id = row["project_id"]
                task_id = row["task_id"]
                source_kind = row["source_kind"]
                source_id = row["source_id"]
                pattern = (
                    source_id_patterns.get(source_kind)
                    if type(source_kind) is str
                    else None
                )
                if (
                    type(reference_id) is not str
                    or len(reference_id) > 128
                    or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(reference_id)
                    is None
                    or type(project_id) is not str
                    or project_id != expected_project_id
                    or type(task_id) is not str
                    or task_id not in selected_task_ids
                    or type(source_kind) is not str
                    or pattern is None
                    or type(source_id) is not str
                    or len(source_id) > 128
                    or pattern.fullmatch(source_id) is None
                    or (source_kind, source_id) in source_owners
                ):
                    raise evidence_ledger_inconsistent()
                key = (source_kind, source_id)
                source_owners[key] = task_id
            _validate_selected_reference_source_chunk(
                connection,
                expected_project_id=expected_project_id,
                selected_task_ids=selected_task_ids,
                source_owners=source_owners,
            )
    except (sqlite3.Error, StorageError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    finally:
        if reference_cursor is not None:
            reference_cursor.close()


def _validate_selected_completion_cycle_evidence(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    cycles: tuple[CompletionCycle, ...],
) -> None:
    """Validate only requested cycles against schema-v18 ledger relations."""

    if current_schema_version(connection) < 18:
        return
    if (
        type(project_id) is not str
        or not project_id
        or type(cycles) is not tuple
    ):
        raise evidence_ledger_inconsistent()

    observed_cycle_ids: set[str] = set()
    for cycle in cycles:
        if not isinstance(cycle, CompletionCycle):
            raise evidence_ledger_inconsistent()
        cycle_id = cycle.completion_cycle_id
        if (
            cycle.project_id != project_id
            or type(cycle_id) is not str
            or not cycle_id
            or cycle_id in observed_cycle_ids
        ):
            raise evidence_ledger_inconsistent()
        observed_cycle_ids.add(cycle_id)

    try:
        for offset in range(
            0,
            len(cycles),
            COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
        ):
            chunk = cycles[
                offset : offset + COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            ]
            cycle_ids = {cycle.completion_cycle_id for cycle in chunk}
            verification_receipt_ids = {
                cycle.verification_receipt_id
                for cycle in chunk
                if cycle.verification_receipt_id is not None
            }
            target_keys = {
                _artifact_manifest_target_key(
                    project_id=cycle.project_id,
                    task_id=cycle.task_id,
                    target_kind=cycle.review_target_kind,
                    target_value=cycle.review_target_value,
                    target_base_revision=cycle.review_target_base_revision,
                    target_generation=cycle.review_target_generation,
                )
                for cycle in chunk
                if cycle.review_target_generation > 0
            }
            targets_json = json.dumps(
                [list(key) for key in sorted(target_keys)],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            manifest_rows = connection.execute(
                """
                WITH selected_targets(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT manifest.artifact_manifest_id,
                       manifest.authority_snapshot_id
                  FROM artifact_manifests AS manifest
                  JOIN selected_targets AS selected
                    ON manifest.project_id = json_extract(selected.value, '$[0]')
                   AND manifest.task_id = json_extract(selected.value, '$[1]')
                   AND manifest.target_kind = json_extract(selected.value, '$[2]')
                   AND manifest.target_value = json_extract(selected.value, '$[3]')
                   AND manifest.target_base_revision =
                         json_extract(selected.value, '$[4]')
                   AND manifest.target_generation =
                         json_extract(selected.value, '$[5]')
                 ORDER BY manifest.artifact_manifest_id
                """,
                (targets_json,),
            ).fetchall()
            manifest_ids: set[str] = set()
            snapshot_ids: set[str] = set()
            for row in manifest_rows:
                manifest_id = row["artifact_manifest_id"]
                snapshot_id = row["authority_snapshot_id"]
                if (
                    type(manifest_id) is not str
                    or not manifest_id
                    or manifest_id in manifest_ids
                    or type(snapshot_id) is not str
                    or not snapshot_id
                ):
                    raise evidence_ledger_inconsistent()
                manifest_ids.add(manifest_id)
                snapshot_ids.add(snapshot_id)

            authority = _validated_authority_context(
                connection,
                snapshot_ids=snapshot_ids,
            )
            manifests, manifests_by_target = _validate_artifact_manifest_storage(
                connection,
                snapshots=authority.snapshots,
                links=authority.links,
                manifest_ids=manifest_ids,
            )
            cycle_rows = _selected_storage_rows_by_ids(
                connection,
                table_name="task_completion_cycles",
                id_field="completion_cycle_id",
                selected_ids=cycle_ids,
            )
            _validate_stored_verification_subject_rows(
                cycle_rows,
                table_name="task_completion_cycles",
                snapshots=authority.snapshots,
                criteria=authority.criteria,
                links=authority.links,
            )
            _validate_evidence_reference_storage(
                connection,
                manifests=manifests,
                manifests_by_target=manifests_by_target,
                snapshots=authority.snapshots,
                verification_receipt_ids=verification_receipt_ids,
                review_receipt_ids=set(),
                review_finding_ids=set(),
                completion_cycle_ids=cycle_ids,
                selected_project_id=project_id,
            )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc


_AUTHORITY_TASK_PRIVACY_REUSE_FIELDS = frozenset(
    {"title", "description", "verification"}
)


def _validate_evidence_ledger_stored_privacy(
    field: str,
    value: str,
    *,
    privacy_success_cache: set[tuple[str, str, str]],
    legacy_m19_7_stored: bool = False,
) -> None:
    privacy_mode = "legacy_m19_7_stored" if legacy_m19_7_stored else "ordinary"
    privacy_key = (privacy_mode, field, value)
    if privacy_key in privacy_success_cache:
        return
    from task_governance_tool.tasks import (
        TaskValidationError,
        reject_private_or_raw_content,
        validate_legacy_m19_7_stored_text,
    )

    try:
        if legacy_m19_7_stored:
            validate_legacy_m19_7_stored_text(field, value)
        else:
            reject_private_or_raw_content(field, value)
    except TaskValidationError as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    privacy_success_cache.add(privacy_key)


def _validate_authority_snapshot_storage_classes(
    row: sqlite3.Row,
    *,
    privacy_success_cache: set[tuple[str, str, str]],
) -> None:
    snapshot_id = row["authority_snapshot_id"]
    project_id = row["project_id"]
    task_id = row["task_id"]
    generation = row["generation"]
    task_title = row["task_title"]
    task_description = row["task_description"]
    review_tier = row["review_tier"]
    verification = row["verification"]
    verification_digest = row["verification_digest"]
    contract_revision = row["contract_revision"]
    contract_state = row["contract_state"]
    contract_scope = row["contract_scope"]
    contract_acceptance = row["contract_acceptance"]
    contract_constraints = row["contract_constraints"]
    contract_authority_ref = row["contract_authority_ref"]
    basis_digest = row["basis_digest"]
    producer_class = row["producer_class"]
    producer_version = row["producer_version"]
    created_at = row["created_at"]
    if (
        type(snapshot_id) is not str
        or re.fullmatch(r"tg_authority_snapshot_[0-9a-f]{16}", snapshot_id)
        is None
        or type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(generation) is not int
        or not 1 <= generation <= SQLITE_INT64_MAX
        or type(task_title) is not str
        or not 1 <= len(task_title) <= 200
        or type(task_description) is not str
        or len(task_description) > 4_000
        or type(review_tier) is not int
        or review_tier not in {0, 1, 2}
        or type(verification) is not str
        or len(verification) > 1_000
        or type(verification_digest) is not str
        or LOWER_HEX_64_PATTERN.fullmatch(verification_digest) is None
        or type(contract_revision) is not int
        or not 0 <= contract_revision <= SQLITE_INT64_MAX
        or type(contract_state) is not str
        or contract_state not in {"contract_specified", "contract_unspecified"}
        or type(contract_scope) is not str
        or len(contract_scope) > 4_000
        or type(contract_acceptance) is not str
        or len(contract_acceptance) > 4_000
        or type(contract_constraints) is not str
        or len(contract_constraints) > 2_000
        or type(contract_authority_ref) is not str
        or len(contract_authority_ref) > 500
        or type(basis_digest) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", basis_digest) is None
        or type(producer_class) is not str
        or producer_class not in {"taskgov_core", "legacy_migration"}
        or type(producer_version) is not int
        or producer_version != 1
        or type(created_at) is not str
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            created_at,
            field="authority snapshot creation time",
        )
    except StorageError as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    for field, value in (
        ("title", task_title),
        ("description", task_description),
        ("verification", verification),
        ("contract_scope", contract_scope),
        ("contract_acceptance", contract_acceptance),
        ("contract_authority_ref", contract_authority_ref),
    ):
        _validate_evidence_ledger_stored_privacy(
            field,
            value,
            privacy_success_cache=privacy_success_cache,
        )
    _validate_evidence_ledger_stored_privacy(
        "contract_constraints",
        contract_constraints,
        privacy_success_cache=privacy_success_cache,
        legacy_m19_7_stored=True,
    )


def _selected_contract_revision_rows(
    connection: sqlite3.Connection,
    required_keys: set[tuple[str, str, int]],
) -> list[sqlite3.Row]:
    if not required_keys:
        return []
    selected_json = json.dumps(
        [list(key) for key in sorted(required_keys)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    try:
        return connection.execute(
            """
            WITH required_contracts(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT contract.*
              FROM task_contract_revisions AS contract
              JOIN required_contracts AS required
                ON contract.project_id = json_extract(required.value, '$[0]')
               AND contract.task_id = json_extract(required.value, '$[1]')
               AND contract.revision = json_extract(required.value, '$[2]')
             ORDER BY contract.project_id, contract.task_id, contract.revision
            """,
            (selected_json,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc


@dataclass(frozen=True)
class _ValidatedAuthorityContext:
    snapshots: dict[str, sqlite3.Row]
    criteria: dict[str, sqlite3.Row]
    links: dict[str, dict[str, str]]
    generation_state: dict[tuple[str, str], tuple[int, int]]
    contracts_by_revision: dict[tuple[str, str, int], sqlite3.Row]
    ordinary_privacy_successes: frozenset[tuple[str, str]] = field(
        repr=False,
        compare=False,
    )


def _validated_contract_criteria_rows(
    rows: list[sqlite3.Row],
    *,
    expected_ids: set[str],
    privacy_success_cache: set[tuple[str, str, str]],
) -> dict[str, sqlite3.Row]:
    criteria: dict[str, sqlite3.Row] = {}
    for row in rows:
        criterion_id = row["criterion_id"]
        project_id = row["project_id"]
        task_id = row["task_id"]
        kind = row["criterion_kind"]
        text = row["criterion_text"]
        if (
            type(criterion_id) is not str
            or not re.fullmatch(
                r"tg_contract_criterion_[0-9a-f]{16}", criterion_id
            )
            or criterion_id in criteria
            or criterion_id not in expected_ids
            or type(project_id) is not str
            or not project_id
            or type(task_id) is not str
            or not task_id
            or type(kind) is not str
            or kind not in {"acceptance", "verification"}
            or type(text) is not str
            or type(row["created_at"]) is not str
            or type(row["digest"]) is not str
            or (kind == "verification" and (not text.strip() or len(text) > 1_000))
            or (kind == "acceptance" and (not text or len(text) > 4_000))
        ):
            raise evidence_ledger_inconsistent()
        _validate_evidence_ledger_stored_privacy(
            "verification" if kind == "verification" else "contract_acceptance",
            text,
            privacy_success_cache=privacy_success_cache,
        )
        if row["digest"] != contract_criterion_digest(kind, text):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                row["created_at"],
                field="Contract criterion creation time",
            )
        except StorageError as exc:
            raise evidence_ledger_boundary_error(exc) from exc
        criteria[criterion_id] = row
    if set(criteria) != expected_ids:
        raise evidence_ledger_inconsistent()
    return criteria


def _validated_authority_context(
    connection: sqlite3.Connection,
    *,
    snapshot_ids: set[str] | None = None,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> _ValidatedAuthorityContext:
    """Validate all or one selected historical authority subgraph."""

    selected_json = (
        json.dumps(
            sorted(snapshot_ids),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if snapshot_ids is not None
        else None
    )
    try:
        if selected_json is None:
            snapshot_rows = connection.execute(
                """
                SELECT * FROM authority_snapshots
                 ORDER BY project_id, task_id, generation
                """
            ).fetchall()
        else:
            snapshot_rows = connection.execute(
                """
                WITH selected_snapshot_ids(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT snapshot.*
                  FROM authority_snapshots AS snapshot
                  JOIN selected_snapshot_ids AS selected
                    ON selected.value = snapshot.authority_snapshot_id
                 ORDER BY snapshot.project_id, snapshot.task_id,
                          snapshot.generation
                """,
                (selected_json,),
            ).fetchall()
        links = _snapshot_criterion_links(connection, snapshot_ids)
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc

    if privacy_success_cache is None:
        privacy_success_cache = set()
    observed_ordinary_privacy_successes: set[tuple[str, str]] = set()
    required_contract_keys: set[tuple[str, str, int]] = set()
    observed_snapshot_ids: set[str] = set()
    for row in snapshot_rows:
        _validate_authority_snapshot_storage_classes(
            row,
            privacy_success_cache=privacy_success_cache,
        )
        observed_ordinary_privacy_successes.update(
            (field_name, row[column_name])
            for field_name, column_name in (
                ("title", "task_title"),
                ("description", "task_description"),
                ("verification", "verification"),
            )
        )
        observed_snapshot_ids.add(row["authority_snapshot_id"])
        if row["contract_revision"] > 0:
            required_contract_keys.add(
                (row["project_id"], row["task_id"], row["contract_revision"])
            )
    if snapshot_ids is not None and observed_snapshot_ids != snapshot_ids:
        raise evidence_ledger_inconsistent()
    if not set(links).issubset(observed_snapshot_ids):
        raise evidence_ledger_inconsistent()

    linked_criterion_ids = {
        criterion_id
        for snapshot_links in links.values()
        for criterion_id in snapshot_links.values()
    }
    try:
        if snapshot_ids is None:
            criterion_rows = connection.execute(
                "SELECT * FROM contract_criteria ORDER BY criterion_id"
            ).fetchall()
        else:
            criteria_json = json.dumps(
                sorted(linked_criterion_ids),
                ensure_ascii=True,
                separators=(",", ":"),
            )
            criterion_rows = connection.execute(
                """
                WITH selected_criterion_ids(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT criterion.*
                  FROM contract_criteria AS criterion
                  JOIN selected_criterion_ids AS selected
                    ON selected.value = criterion.criterion_id
                 ORDER BY criterion.criterion_id
                """,
                (criteria_json,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    criteria = _validated_contract_criteria_rows(
        criterion_rows,
        expected_ids=linked_criterion_ids,
        privacy_success_cache=privacy_success_cache,
    )
    contracts_by_revision = _validated_contract_revision_rows(
        _selected_contract_revision_rows(connection, required_contract_keys),
        expected_keys=required_contract_keys,
    )

    snapshots: dict[str, sqlite3.Row] = {}
    generation_state: dict[tuple[str, str], tuple[int, int]] = {}
    for row in snapshot_rows:
        snapshot_id = row["authority_snapshot_id"]
        snapshot_links = links.get(snapshot_id, {})
        acceptance_id = snapshot_links.get("acceptance")
        verification_id = snapshot_links.get("verification")
        verification = row["verification"]
        contract_revision = row["contract_revision"]
        contract = (
            contracts_by_revision.get(
                (row["project_id"], row["task_id"], contract_revision)
            )
            if contract_revision > 0
            else None
        )
        expected_contract = (
            (
                "contract_specified",
                contract["scope"],
                contract["acceptance"],
                contract["constraints_text"],
                contract["authority_ref"],
            )
            if contract is not None
            else ("contract_unspecified", "", "", "", "")
        )
        if (
            snapshot_id in snapshots
            or row["verification_digest"]
            != _verification_expectation_digest(verification)
            or (bool(verification.strip()) != (verification_id is not None))
            or contract_revision > 0 and contract is None
            or (
                row["contract_state"],
                row["contract_scope"],
                row["contract_acceptance"],
                row["contract_constraints"],
                row["contract_authority_ref"],
            )
            != expected_contract
            or (contract_revision == 0 and acceptance_id is not None)
            or (contract_revision > 0 and acceptance_id is None)
        ):
            raise evidence_ledger_inconsistent()
        for kind, criterion_id in snapshot_links.items():
            criterion = criteria.get(criterion_id)
            if (
                criterion is None
                or criterion["project_id"] != row["project_id"]
                or criterion["task_id"] != row["task_id"]
                or criterion["criterion_kind"] != kind
                or criterion["criterion_text"]
                != (
                    row["contract_acceptance"]
                    if kind == "acceptance"
                    else row["verification"]
                )
            ):
                raise evidence_ledger_inconsistent()
        digest_values = {
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "task_title": row["task_title"],
            "task_description": row["task_description"],
            "review_tier": row["review_tier"],
            "verification": verification,
            "verification_digest": row["verification_digest"],
            "contract_revision": contract_revision,
            "contract_state": row["contract_state"],
            "contract_scope": row["contract_scope"],
            "contract_acceptance": row["contract_acceptance"],
            "contract_constraints": row["contract_constraints"],
            "contract_authority_ref": row["contract_authority_ref"],
            "acceptance_criterion_id": acceptance_id,
            "verification_criterion_id": verification_id,
            "producer_class": row["producer_class"],
            "producer_version": row["producer_version"],
        }
        if row["basis_digest"] != authority_snapshot_basis_digest(digest_values):
            raise evidence_ledger_inconsistent()
        generation_key = (row["project_id"], row["task_id"])
        generation_count, previous_generation = generation_state.get(
            generation_key,
            (0, 0),
        )
        if snapshot_ids is None and row["generation"] != previous_generation + 1:
            raise evidence_ledger_inconsistent()
        snapshots[snapshot_id] = row
        generation_state[generation_key] = (
            generation_count + 1,
            row["generation"],
        )
    return _ValidatedAuthorityContext(
        snapshots=snapshots,
        criteria=criteria,
        links=links,
        generation_state=generation_state,
        contracts_by_revision=contracts_by_revision,
        ordinary_privacy_successes=frozenset(
            observed_ordinary_privacy_successes
        ),
    )


def _validate_stored_verification_subject_rows(
    rows: list[sqlite3.Row],
    *,
    table_name: str,
    snapshots: dict[str, sqlite3.Row],
    criteria: dict[str, sqlite3.Row],
    links: dict[str, dict[str, str]],
) -> None:
    for row in rows:
        basis = row["verification_subject_basis_version"]
        snapshot_id = row["subject_authority_snapshot_id"]
        criterion_id = row["subject_verification_criterion_id"]
        if type(basis) is not int or basis not in {0, 1}:
            raise evidence_ledger_inconsistent()
        if basis == 0:
            if snapshot_id is not None or criterion_id is not None:
                raise evidence_ledger_inconsistent()
        elif table_name == "verification_receipts":
            if (
                snapshot_id is None
                or criterion_id is None
                or str(row["command_label"])
                != "taskgov-owned-verification-subject-v1"
            ):
                raise evidence_ledger_inconsistent()
        elif str(row["verification_expectation"]) == "specified":
            if snapshot_id is None or criterion_id is None:
                raise evidence_ledger_inconsistent()
        elif snapshot_id is not None or criterion_id is not None:
            raise evidence_ledger_inconsistent()
        if basis == 1 and snapshot_id is not None:
            snapshot = snapshots.get(str(snapshot_id))
            criterion = criteria.get(str(criterion_id))
            if (
                snapshot is None
                or criterion is None
                or str(snapshot["project_id"]) != str(row["project_id"])
                or str(snapshot["task_id"]) != str(row["task_id"])
                or str(criterion["project_id"]) != str(row["project_id"])
                or str(criterion["task_id"]) != str(row["task_id"])
                or str(criterion["criterion_kind"]) != "verification"
                or links.get(str(snapshot_id), {}).get("verification")
                != str(criterion_id)
            ):
                raise evidence_ledger_inconsistent()


def _validate_evidence_ledger_rows(
    connection: sqlite3.Connection,
    *,
    verification_rejection_is_local: bool = False,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> tuple[sqlite3.Row, ...]:
    authority = _validated_authority_context(
        connection,
        privacy_success_cache=privacy_success_cache,
    )
    snapshots = authority.snapshots
    criteria = authority.criteria
    links = authority.links
    snapshot_generation_state = authority.generation_state
    contracts_by_revision = authority.contracts_by_revision
    manifests, manifests_by_target = _validate_artifact_manifest_storage(
        connection,
        snapshots=snapshots,
        links=links,
    )

    from task_governance_tool.tasks import TaskRepositoryError, validate_stored_task_rows

    task_rows = connection.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
    expected_project_id = (
        task_rows[0]["project_id"] if task_rows else "__empty_project__"
    )
    if type(expected_project_id) is not str or not expected_project_id:
        raise evidence_ledger_inconsistent()
    physical_schema_version = current_schema_version(connection)
    if physical_schema_version not in {
        18,
        19,
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
    }:
        raise evidence_ledger_inconsistent()
    source_schema_version = (
        19
        if physical_schema_version == PRIVATE_SCHEMA20_VERSION
        else physical_schema_version
    )
    if physical_schema_version == PRIVATE_SCHEMA22_VERSION:
        # v22 changes Evidence DDL only; stored Task fields retain v21 rules.
        source_schema_version = PRIVATE_SCHEMA21_VERSION
    try:
        task_validation = validate_stored_task_rows(
            task_rows,
            connection=connection,
            source_schema_version=source_schema_version,
            expected_project_id=expected_project_id,
            verification_rejection_is_local=verification_rejection_is_local,
            _prevalidated_privacy_successes=authority.ordinary_privacy_successes,
        )
    except (StorageError, TaskRepositoryError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc

    for row in task_rows:
        snapshot_id = row["current_authority_snapshot_id"]
        generation = row["current_authority_snapshot_generation"]
        project_id = row["project_id"]
        task_id = row["task_id"]
        title = row["title"]
        description = row["description"]
        review_tier = row["review_tier"]
        verification = row["verification"]
        contract_revision = row["current_contract_revision"]
        if (
            type(snapshot_id) is not str
            or re.fullmatch(
                r"tg_authority_snapshot_[0-9a-f]{16}", snapshot_id
            )
            is None
            or type(generation) is not int
            or not 1 <= generation <= SQLITE_INT64_MAX
        ):
            raise evidence_ledger_inconsistent()
        snapshot = snapshots.get(snapshot_id)
        contract = None
        if contract_revision > 0:
            contract = contracts_by_revision.get(
                (project_id, task_id, contract_revision)
            )
        expected_contract = (
            (
                "contract_specified",
                contract["scope"],
                contract["acceptance"],
                contract["constraints_text"],
                contract["authority_ref"],
            )
            if contract is not None
            else ("contract_unspecified", "", "", "", "")
        )
        if (
            snapshot is None
            or (contract_revision > 0 and contract is None)
            or snapshot_generation_state.get(
                (project_id, task_id)
            )
            != (generation, generation)
            or snapshot["generation"] != generation
            or snapshot["project_id"] != project_id
            or snapshot["task_id"] != task_id
            or snapshot["task_title"] != title
            or snapshot["task_description"] != description
            or snapshot["review_tier"] != review_tier
            or (
                snapshot["verification"] != verification
                and task_id
                not in task_validation.verification_rejected_task_ids
            )
            or snapshot["contract_revision"] != contract_revision
            or (
                snapshot["contract_state"],
                snapshot["contract_scope"],
                snapshot["contract_acceptance"],
                snapshot["contract_constraints"],
                snapshot["contract_authority_ref"],
            )
            != expected_contract
        ):
            raise evidence_ledger_inconsistent()
        capture_version = row["review_target_capture_version"]
        target_bindings = (
            row["review_target_authority_snapshot_id"],
            row["review_target_acceptance_criterion_id"],
            row["review_target_verification_criterion_id"],
            row["review_target_artifact_manifest_id"],
        )
        if type(capture_version) is not int or capture_version not in {0, 1}:
            raise evidence_ledger_inconsistent()
        if capture_version == 0:
            if any(value is not None for value in target_bindings):
                raise evidence_ledger_inconsistent()
        elif (
            type(row["review_target_kind"]) is not str
            or not row["review_target_kind"]
            or type(row["review_target_value"]) is not str
            or not row["review_target_value"]
            or type(row["review_target_base_revision"]) is not str
            or type(row["review_target_generation"]) is not int
            or row["review_target_generation"] <= 0
            or type(target_bindings[0]) is not str
            or type(target_bindings[3]) is not str
        ):
            raise evidence_ledger_inconsistent()
        elif capture_version == 1:
            manifest_record = manifests.get(target_bindings[3])
            manifest = manifest_record.row if manifest_record is not None else None
            snapshot_links = links.get(target_bindings[0], {})
            if (
                manifest is None
                or manifest["project_id"] != project_id
                or manifest["task_id"] != task_id
                or manifest["target_kind"] != row["review_target_kind"]
                or manifest["target_value"] != row["review_target_value"]
                or manifest["target_base_revision"]
                != row["review_target_base_revision"]
                or manifest["target_generation"]
                != row["review_target_generation"]
                or manifest["authority_snapshot_id"] != target_bindings[0]
                or manifest["acceptance_criterion_id"] != target_bindings[1]
                or manifest["verification_criterion_id"] != target_bindings[2]
                or snapshot_links.get("acceptance") != target_bindings[1]
                or snapshot_links.get("verification") != target_bindings[2]
            ):
                raise evidence_ledger_inconsistent()

    for table_name in ("verification_receipts", "task_completion_cycles"):
        rows = connection.execute(
            f"SELECT * FROM {_quoted_identifier(table_name)} ORDER BY rowid"
        ).fetchall()
        _validate_stored_verification_subject_rows(
            rows,
            table_name=table_name,
            snapshots=snapshots,
            criteria=criteria,
            links=links,
        )


    _validate_evidence_reference_storage(
        connection,
        manifests=manifests,
        manifests_by_target=manifests_by_target,
        snapshots=snapshots,
    )
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise evidence_ledger_inconsistent()
    if task_validation.verification_rejection is not None:
        raise StoredTaskVerificationError(
            task_validation.verification_rejection
        )
    return tuple(task_rows)


def _projection_state_from_row(row: sqlite3.Row) -> EvidenceProjectionState:
    project_id = row["project_id"]
    source_generation = row["source_generation"]
    published_generation = row["published_generation"]
    index_digest = row["index_digest"]
    last_success_at = row["last_success_at"]
    last_outcome_code = row["last_outcome_code"]
    last_outcome_at = row["last_outcome_at"]
    if (
        type(project_id) is not str
        or not project_id
        or type(source_generation) is not int
        or source_generation < 0
        or source_generation > SQLITE_INT64_MAX
        or (
            published_generation is not None
            and (
                type(published_generation) is not int
                or published_generation < 0
                or published_generation > source_generation
            )
        )
        or (
            (published_generation is None) != (index_digest is None)
        )
        or (
            index_digest is not None
            and (
                type(index_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(index_digest) is None
            )
        )
        or (
            last_outcome_code is not None
            and (
                type(last_outcome_code) is not str
                or last_outcome_code not in EVIDENCE_PROJECTION_OUTCOMES
            )
        )
        or ((last_outcome_code is None) != (last_outcome_at is None))
        or (last_success_at is not None and type(last_success_at) is not str)
        or (last_outcome_at is not None and type(last_outcome_at) is not str)
    ):
        raise evidence_ledger_inconsistent()
    try:
        if last_success_at is not None:
            validate_utc_timestamp(
                last_success_at,
                field="Evidence projection success time",
            )
        if last_outcome_at is not None:
            validate_utc_timestamp(
                last_outcome_at,
                field="Evidence projection outcome time",
            )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    return EvidenceProjectionState(
        project_id=project_id,
        source_generation=source_generation,
        published_generation=published_generation,
        index_digest=index_digest,
        last_success_at=last_success_at,
        last_outcome_code=last_outcome_code,
        last_outcome_at=last_outcome_at,
    )


def _criterion_link_relation_valid(
    *,
    criterion_kind: object,
    source_kind: object,
    relation: object,
) -> bool:
    return (
        relation == "verification_attestation"
        and criterion_kind == "verification"
        and source_kind == "verification_receipt"
    ) or (
        relation == "review_assessment"
        and criterion_kind == "acceptance"
        and source_kind == "review_receipt"
    ) or (
        relation == "review_finding"
        and criterion_kind == "acceptance"
        and source_kind == "review_finding"
    ) or (
        relation == "completion_basis"
        and criterion_kind == "acceptance"
        and source_kind in {"artifact_manifest", "completion_evidence"}
    ) or (
        relation == "runner_observation"
        and criterion_kind == "verification"
        and source_kind == "runner_observation"
    )


def _completion_bundle_version_basis_valid(
    *,
    source_schema_version: object,
    bundle_version: object,
    verification_basis_kind: object,
    verification_runner_observation_id: object,
    verification_receipt_id: object,
    cycle: CompletionCycle,
) -> bool:
    if type(source_schema_version) is not int or type(bundle_version) is not int:
        return False
    if (source_schema_version, bundle_version) == (19, 1):
        return (
            verification_basis_kind is None
            and verification_runner_observation_id is None
            and cycle.verification_basis_kind is None
            and cycle.verification_runner_observation_id is None
        )
    if (source_schema_version, bundle_version) not in {(20, 2), (21, 2), (22, 2)}:
        return False
    if verification_basis_kind != cycle.verification_basis_kind:
        return False
    if verification_basis_kind == "runner_observation":
        return (
            source_schema_version in {
                PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
            }
            and verification_receipt_id is None
            and verification_runner_observation_id is not None
            and verification_runner_observation_id
            == cycle.verification_runner_observation_id
        )
    if (
        verification_runner_observation_id is not None
        or cycle.verification_runner_observation_id is not None
    ):
        return False
    return (
        verification_basis_kind == "caller_attestation"
        and verification_receipt_id is not None
    ) or (
        verification_basis_kind == "not_required"
        and verification_receipt_id is None
    )


def _validate_one_completion_evidence_bundle_row(
    *,
    bundle_id: str,
    row: sqlite3.Row | dict[str, Any],
    container_schema_version: int,
    cycle: CompletionCycle | None,
    snapshot: sqlite3.Row | dict[str, Any] | None,
    owner_links: dict[str, str],
    manifest: sqlite3.Row | dict[str, Any] | None,
    verification_receipts: dict[str, sqlite3.Row | dict[str, Any]],
    member_groups: dict[str, list[sqlite3.Row | dict[str, Any]]],
    links: dict[str, sqlite3.Row | dict[str, Any]],
    references: dict[str, sqlite3.Row | dict[str, Any]],
    finding_rows: list[sqlite3.Row | dict[str, Any]],
    runner_generations: dict[tuple[str, str, int], dict[str, Any]] | None,
) -> None:
    """Validate the semantic closure of one already selected Bundle row."""

    verification_receipt_id = row["verification_receipt_id"]
    row_fields = set(row.keys())
    verification_basis_kind = (
        row["verification_basis_kind"]
        if "verification_basis_kind" in row_fields
        else None
    )
    verification_runner_observation_id = (
        row["verification_runner_observation_id"]
        if "verification_runner_observation_id" in row_fields
        else None
    )
    omission_mask = row["omission_mask"]
    payload_size = row["payload_size_bytes"]
    if (
        COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(bundle_id) is None
        or (
            row["source_schema_version"] == PRIVATE_SCHEMA22_VERSION
            and container_schema_version != PRIVATE_SCHEMA22_VERSION
        )
        or cycle is None
        or cycle.evidence_basis_version != 1
        or cycle.completion_evidence_bundle_id != bundle_id
        or cycle.project_id != row["project_id"]
        or cycle.task_id != row["task_id"]
        or cycle.saved_cycle_ordinal != row["cycle_ordinal"]
        or cycle.contract_revision != row["contract_revision"]
        or not _completion_bundle_version_basis_valid(
            source_schema_version=row["source_schema_version"],
            bundle_version=row["bundle_version"],
            verification_basis_kind=verification_basis_kind,
            verification_runner_observation_id=(
                verification_runner_observation_id
            ),
            verification_receipt_id=verification_receipt_id,
            cycle=cycle,
        )
        or snapshot is None
        or snapshot["project_id"] != row["project_id"]
        or snapshot["task_id"] != row["task_id"]
        or snapshot["contract_revision"] != row["contract_revision"]
        or owner_links.get("acceptance") != row["acceptance_criterion_id"]
        or owner_links.get("verification") != row["verification_criterion_id"]
        or manifest is None
        or manifest["project_id"] != row["project_id"]
        or manifest["task_id"] != row["task_id"]
        or manifest["authority_snapshot_id"] != row["authority_snapshot_id"]
        or manifest["acceptance_criterion_id"]
        != row["acceptance_criterion_id"]
        or manifest["verification_criterion_id"]
        != row["verification_criterion_id"]
        or (
            manifest["target_kind"], manifest["target_value"],
            manifest["target_base_revision"], manifest["target_generation"],
        )
        != (
            row["target_kind"], row["target_value"],
            row["target_base_revision"], row["target_generation"],
        )
        or (
            cycle.review_target_kind, cycle.review_target_value,
            cycle.review_target_base_revision,
            cycle.review_target_generation,
        )
        != (
            row["target_kind"], row["target_value"],
            row["target_base_revision"], row["target_generation"],
        )
        or row["target_capture_version"] != 1
        or type(row["target_capture_version"]) is not int
        or type(omission_mask) is not int
        or not 0 <= omission_mask <= 15
        or type(row["sealed_at"]) is not str
        or type(row["bundle_digest"]) is not str
        or SHA256_DIGEST_PATTERN.fullmatch(row["bundle_digest"]) is None
        or type(payload_size) is not int
        or not 1 <= payload_size <= COMPLETION_EVIDENCE_BUNDLE_MAX_BYTES
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            row["sealed_at"],
            field="completion Evidence Bundle seal time",
        )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    runner_generation = (
        runner_generations.get(
            (
                str(row["project_id"]),
                str(row["task_id"]),
                int(row["target_generation"]),
            )
        )
        if runner_generations is not None
        else None
    )
    resolution = (
        runner_generation["resolution"] if runner_generation is not None else None
    )
    attempt = runner_generation["attempt"] if runner_generation is not None else None
    observation = (
        runner_generation["observation"] if runner_generation is not None else None
    )
    cleanup = (
        runner_generation["cleanup_event"] if runner_generation is not None else None
    )
    runner_eligibility_one = (
        resolution is not None and resolution.gate_eligibility_version == 1
    )
    if (
        runner_eligibility_one
        and row["source_schema_version"] not in {
            PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
        }
    ):
        raise evidence_ledger_inconsistent()
    runner_identity_matches = (
        runner_eligibility_one
        and runner_generation is not None
        and runner_generation["state"] == "terminal"
        and attempt is not None
        and attempt.gate_eligibility_version == 1
        and observation is not None
        and observation.gate_eligibility_version == 1
        and cleanup is not None
        and cleanup.terminal_observation_id
        == observation.verification_runner_observation_id
        and resolution.contract_revision == row["contract_revision"]
        and resolution.verification_expectation_digest
        == cycle.verification_expectation_digest
        and resolution.authority_snapshot_id == row["authority_snapshot_id"]
        and resolution.verification_criterion_id
        == row["verification_criterion_id"]
        and resolution.target_kind == row["target_kind"]
        and resolution.target_value == row["target_value"]
        and (resolution.target_base_revision or "") == row["target_base_revision"]
        and resolution.target_generation == row["target_generation"]
        and resolution.artifact_manifest_id == row["artifact_manifest_id"]
    )
    if verification_basis_kind == "runner_observation":
        if (
            verification_receipt_id is not None
            or type(verification_runner_observation_id) is not str
            or row["verification_criterion_id"] is None
        ):
            raise evidence_ledger_inconsistent()
        if (
            not runner_identity_matches
            or observation.verification_runner_observation_id
            != verification_runner_observation_id
            or observation.route != "runner"
            or observation.launch_state != "launched"
            or observation.outcome != "pass"
            or observation.reason is not None
            or observation.complete_plan != 1
            or observation.total_step_count != resolution.step_count
            or observation.completed_step_count != resolution.step_count
            or observation.failed_step_ordinal is not None
        ):
            raise evidence_ledger_inconsistent()
    elif row["verification_criterion_id"] is None:
        if runner_eligibility_one or verification_receipt_id is not None:
            raise evidence_ledger_inconsistent()
    else:
        if runner_eligibility_one and (
            not runner_identity_matches
            or observation.route != "m21_fallback"
            or observation.launch_state != "no_launch"
            or observation.outcome != "blocked_prelaunch"
            or observation.reason not in {"runtime_unavailable", "process_setup_failed"}
            or observation.complete_plan != 0
        ):
            raise evidence_ledger_inconsistent()
        receipt = verification_receipts.get(str(verification_receipt_id))
        if (
            receipt is None
            or verification_receipt_id != cycle.verification_receipt_id
            or receipt["project_id"] != row["project_id"]
            or receipt["task_id"] != row["task_id"]
            or receipt["verification_subject_basis_version"] != 1
            or receipt["subject_authority_snapshot_id"]
            != row["authority_snapshot_id"]
            or receipt["subject_verification_criterion_id"]
            != row["verification_criterion_id"]
        ):
            raise evidence_ledger_inconsistent()

    reference_members = member_groups.get("evidence_reference", [])
    link_members = member_groups.get("criterion_link", [])
    member_reference_ids = {
        str(member["evidence_reference_id"]) for member in reference_members
    }
    member_link_ids = {
        str(member["criterion_evidence_link_id"]) for member in link_members
    }
    if any(
        str(links[link_id]["evidence_reference_id"]) not in member_reference_ids
        for link_id in member_link_ids
    ):
        raise evidence_ledger_inconsistent()
    source_keys = {
        (
            str(references[reference_id]["source_kind"]),
            str(references[reference_id]["source_id"]),
        ): reference_id
        for reference_id in member_reference_ids
    }
    required_source_keys = {
        ("artifact_manifest", str(row["artifact_manifest_id"])),
        ("completion_evidence", cycle.completion_cycle_id),
        *(
            ("review_receipt", receipt_id)
            for receipt_id in cycle.gate_basis.qualifying_receipt_ids
        ),
    }
    if verification_receipt_id is not None:
        required_source_keys.add(
            ("verification_receipt", str(verification_receipt_id))
        )
    if verification_runner_observation_id is not None:
        required_source_keys.add(
            ("runner_observation", str(verification_runner_observation_id))
        )
    for finding in finding_rows:
        if finding["evidence_reference_id"] is not None:
            required_source_keys.add(
                ("review_finding", str(finding["review_finding_id"]))
            )
    if set(source_keys) != required_source_keys:
        raise evidence_ledger_inconsistent()

    expected_links: set[tuple[str, str, str]] = set()
    acceptance_id = row["acceptance_criterion_id"]
    verification_id = row["verification_criterion_id"]
    current_finding_source_ids = {
        str(finding["review_finding_id"])
        for finding in finding_rows
        if (
            finding["evidence_reference_id"] is not None
            and finding["target_generation"] == cycle.review_target_generation
        )
    }
    if acceptance_id is not None:
        for source_kind, source_id in required_source_keys:
            if (
                source_kind == "review_finding"
                and source_id not in current_finding_source_ids
            ):
                continue
            relation = {
                "artifact_manifest": "completion_basis",
                "completion_evidence": "completion_basis",
                "review_receipt": "review_assessment",
                "review_finding": "review_finding",
            }.get(source_kind)
            if relation is not None:
                expected_links.add(
                    (
                        str(acceptance_id),
                        source_keys[(source_kind, source_id)],
                        relation,
                    )
                )
    if verification_id is not None and verification_receipt_id is not None:
        expected_links.add(
            (
                str(verification_id),
                source_keys[("verification_receipt", str(verification_receipt_id))],
                "verification_attestation",
            )
        )
    if verification_id is not None and verification_runner_observation_id is not None:
        expected_links.add(
            (
                str(verification_id),
                source_keys[
                    ("runner_observation", str(verification_runner_observation_id))
                ],
                "runner_observation",
            )
        )
    actual_links = {
        (
            str(links[link_id]["criterion_id"]),
            str(links[link_id]["evidence_reference_id"]),
            str(links[link_id]["relation"]),
        )
        for link_id in member_link_ids
    }
    if actual_links != expected_links:
        raise evidence_ledger_inconsistent()

    expected_omission_mask = 0
    if acceptance_id is None:
        expected_omission_mask |= COMPLETION_BUNDLE_OMISSION_BITS[
            "acceptance_criterion_absent"
        ]
    if verification_id is None:
        expected_omission_mask |= COMPLETION_BUNDLE_OMISSION_BITS[
            "verification_criterion_absent"
        ]
    if manifest["omission_code"] == "artifact_content_not_observed":
        expected_omission_mask |= COMPLETION_BUNDLE_OMISSION_BITS[
            "artifact_content_not_observed"
        ]
    if any(finding["evidence_reference_id"] is None for finding in finding_rows):
        expected_omission_mask |= COMPLETION_BUNDLE_OMISSION_BITS[
            "historical_finding_reference_absent"
        ]
    if omission_mask != expected_omission_mask:
        raise evidence_ledger_inconsistent()


def _validate_completion_evidence_bundle_rows(
    connection: sqlite3.Connection,
    *,
    _validated_runner_generations: (
        dict[tuple[str, str, int], dict[str, Any]] | None
    ) = None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    criteria = {
        str(row["criterion_id"]): row
        for row in connection.execute(
            "SELECT * FROM contract_criteria ORDER BY criterion_id"
        ).fetchall()
    }
    references = {
        str(row["evidence_reference_id"]): row
        for row in connection.execute(
            "SELECT * FROM evidence_references ORDER BY evidence_reference_id"
        ).fetchall()
    }
    links: dict[str, sqlite3.Row] = {}
    for row in connection.execute(
        "SELECT * FROM criterion_evidence_links ORDER BY criterion_evidence_link_id"
    ).fetchall():
        link_id = row["criterion_evidence_link_id"]
        criterion_id = row["criterion_id"]
        reference_id = row["evidence_reference_id"]
        relation = row["relation"]
        producer_version = row["producer_version"]
        created_at = row["created_at"]
        criterion = criteria.get(str(criterion_id))
        reference = references.get(str(reference_id))
        if (
            type(link_id) is not str
            or CRITERION_EVIDENCE_LINK_ID_PATTERN.fullmatch(link_id) is None
            or link_id in links
            or type(criterion_id) is not str
            or type(reference_id) is not str
            or type(relation) is not str
            or relation not in CRITERION_EVIDENCE_RELATIONS
            or relation == "derived_analysis"
            or type(producer_version) is not int
            or producer_version <= 0
            or type(created_at) is not str
            or criterion is None
            or reference is None
            or criterion["project_id"] != row["project_id"]
            or criterion["task_id"] != row["task_id"]
            or reference["project_id"] != row["project_id"]
            or reference["task_id"] != row["task_id"]
            or reference["assurance_class"] != row["assurance_class"]
            or reference["producer_class"] != row["producer_class"]
            or reference["producer_version"] != producer_version
            or not _criterion_link_relation_valid(
                criterion_kind=criterion["criterion_kind"],
                source_kind=reference["source_kind"],
                relation=relation,
            )
        ):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                created_at,
                field="criterion Evidence Link creation time",
            )
        except StorageError as exc:
            raise evidence_ledger_inconsistent() from exc
        links[link_id] = row

    bundle_rows = connection.execute(
        "SELECT * FROM completion_evidence_bundles "
        "ORDER BY completion_evidence_bundle_id"
    ).fetchall()
    container_schema_version = current_schema_version(connection)
    if (
        _validated_runner_generations is None
        and bundle_rows
        and container_schema_version in {
            PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
        }
    ):
        _, _validated_runner_generations = _validated_verification_runner_graph(
            connection
        )
    bundles = {
        str(row["completion_evidence_bundle_id"]): row for row in bundle_rows
    }
    if len(bundles) != len(bundle_rows):
        raise evidence_ledger_inconsistent()

    member_rows = connection.execute(
        "SELECT * FROM completion_bundle_members "
        "ORDER BY completion_evidence_bundle_id, member_kind, ordinal"
    ).fetchall()
    members_by_bundle: dict[str, dict[str, list[sqlite3.Row]]] = {}
    used_link_ids: set[str] = set()
    for row in member_rows:
        bundle_id = row["completion_evidence_bundle_id"]
        member_kind = row["member_kind"]
        ordinal = row["ordinal"]
        if (
            type(bundle_id) is not str
            or bundle_id not in bundles
            or type(member_kind) is not str
            or member_kind not in COMPLETION_BUNDLE_MEMBER_KINDS
            or type(ordinal) is not int
            or ordinal < 0
            or bundles[bundle_id]["project_id"] != row["project_id"]
            or bundles[bundle_id]["task_id"] != row["task_id"]
        ):
            raise evidence_ledger_inconsistent()
        grouped = members_by_bundle.setdefault(bundle_id, {}).setdefault(
            member_kind,
            [],
        )
        if ordinal != len(grouped):
            raise evidence_ledger_inconsistent()
        if member_kind == "criterion_link":
            link_id = row["criterion_evidence_link_id"]
            if (
                type(link_id) is not str
                or row["evidence_reference_id"] is not None
                or link_id not in links
                or links[link_id]["project_id"] != row["project_id"]
                or links[link_id]["task_id"] != row["task_id"]
            ):
                raise evidence_ledger_inconsistent()
            used_link_ids.add(link_id)
        else:
            reference_id = row["evidence_reference_id"]
            if (
                type(reference_id) is not str
                or row["criterion_evidence_link_id"] is not None
                or reference_id not in references
                or references[reference_id]["project_id"] != row["project_id"]
                or references[reference_id]["task_id"] != row["task_id"]
            ):
                raise evidence_ledger_inconsistent()
        grouped.append(row)
    bundle_link_ids = {
        link_id
        for link_id, link in links.items()
        if link["relation"] != "runner_observation"
    }
    if not bundle_link_ids.issubset(used_link_ids):
        raise evidence_ledger_inconsistent()

    snapshots_by_bundle: dict[str, list[sqlite3.Row]] = {}
    for row in connection.execute(
        "SELECT * FROM completion_bundle_finding_snapshots "
        "ORDER BY completion_evidence_bundle_id, ordinal"
    ).fetchall():
        bundle_id = row["completion_evidence_bundle_id"]
        ordinal = row["ordinal"]
        producer_version = row["producer_version"]
        reference_id = row["evidence_reference_id"]
        if (
            type(bundle_id) is not str
            or bundle_id not in bundles
            or type(ordinal) is not int
            or ordinal < 0
            or type(row["review_finding_id"]) is not str
            or type(row["review_receipt_id"]) is not str
            or type(row["target_generation"]) is not int
            or row["target_generation"] <= 0
            or type(row["summary"]) is not str
            or type(row["resolution_summary"]) is not str
            or type(row["created_at"]) is not str
            or type(producer_version) is not int
            or producer_version != 1
            or type(row["digest"]) is not str
            or SHA256_DIGEST_PATTERN.fullmatch(row["digest"]) is None
            or bundles[bundle_id]["project_id"] != row["project_id"]
            or bundles[bundle_id]["task_id"] != row["task_id"]
            or (
                reference_id is None
                and (
                    row["assurance_class"] != "legacy_unknown"
                    or row["producer_class"] != "legacy_migration"
                )
            )
            or (
                reference_id is not None
                and (
                    type(reference_id) is not str
                    or reference_id not in references
                    or row["assurance_class"] != "bound_attestation"
                    or row["producer_class"] != "trusted_caller"
                    or references[reference_id]["source_kind"]
                    != "review_finding"
                    or references[reference_id]["source_id"]
                    != row["review_finding_id"]
                )
            )
        ):
            raise evidence_ledger_inconsistent()
        grouped = snapshots_by_bundle.setdefault(bundle_id, [])
        if ordinal != len(grouped):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                row["created_at"],
                field="completion Finding snapshot creation time",
            )
            if row["resolved_at"] is not None:
                if type(row["resolved_at"]) is not str:
                    raise evidence_ledger_inconsistent()
                validate_utc_timestamp(
                    row["resolved_at"],
                    field="completion Finding snapshot resolution time",
                )
        except StorageError as exc:
            raise evidence_ledger_inconsistent() from exc
        grouped.append(row)

    snapshots = {
        str(row["authority_snapshot_id"]): row
        for row in connection.execute(
            "SELECT * FROM authority_snapshots ORDER BY authority_snapshot_id"
        ).fetchall()
    }
    snapshot_links = _snapshot_criterion_links(connection)
    manifests = {
        str(row["artifact_manifest_id"]): row
        for row in connection.execute(
            "SELECT * FROM artifact_manifests ORDER BY artifact_manifest_id"
        ).fetchall()
    }
    verification_receipts = {
        str(row["verification_receipt_id"]): row
        for row in connection.execute(
            "SELECT * FROM verification_receipts ORDER BY verification_receipt_id"
        ).fetchall()
    }
    cycle_rows = connection.execute(
        "SELECT * FROM task_completion_cycles "
        "ORDER BY project_id, task_id, saved_cycle_ordinal"
    ).fetchall()
    cycles = {
        cycle.completion_cycle_id: cycle
        for cycle in (_cycle_from_row(row) for row in cycle_rows)
    }
    if len(cycles) != len(cycle_rows):
        raise evidence_ledger_inconsistent()

    for bundle_id, row in bundles.items():
        _validate_one_completion_evidence_bundle_row(
            bundle_id=bundle_id,
            row=row,
            container_schema_version=container_schema_version,
            cycle=cycles.get(str(row["completion_cycle_id"])),
            snapshot=snapshots.get(str(row["authority_snapshot_id"])),
            owner_links=snapshot_links.get(
                str(row["authority_snapshot_id"]),
                {},
            ),
            manifest=manifests.get(str(row["artifact_manifest_id"])),
            verification_receipts=verification_receipts,
            member_groups=members_by_bundle.get(bundle_id, {}),
            links=links,
            references=references,
            finding_rows=snapshots_by_bundle.get(bundle_id, []),
            runner_generations=_validated_runner_generations,
        )

    for cycle in cycles.values():
        if cycle.evidence_basis_version == 1:
            if cycle.completion_evidence_bundle_id not in bundles:
                raise evidence_ledger_inconsistent()
        elif cycle.completion_evidence_bundle_id is not None:
            raise evidence_ledger_inconsistent()
    if {
        str(row["completion_cycle_id"]) for row in bundle_rows
    } != {
        cycle.completion_cycle_id
        for cycle in cycles.values()
        if cycle.evidence_basis_version == 1
    }:
        raise evidence_ledger_inconsistent()

    project_rows = connection.execute(
        "SELECT project_id FROM project_meta ORDER BY project_id"
    ).fetchall()
    projection_rows = connection.execute(
        "SELECT * FROM evidence_projection_state ORDER BY project_id"
    ).fetchall()
    projection_states = {
        state.project_id: state
        for state in (_projection_state_from_row(row) for row in projection_rows)
    }
    project_ids = {
        str(row["project_id"])
        for row in project_rows
        if type(row["project_id"]) is str and row["project_id"]
    }
    if len(project_ids) != len(project_rows) or set(projection_states) != project_ids:
        raise evidence_ledger_inconsistent()
    cycle_counts: dict[str, int] = {project_id: 0 for project_id in project_ids}
    for cycle in cycles.values():
        if cycle.project_id not in cycle_counts:
            raise evidence_ledger_inconsistent()
        cycle_counts[cycle.project_id] += 1
    if any(
        projection_states[project_id].source_generation != cycle_count
        for project_id, cycle_count in cycle_counts.items()
    ):
        raise evidence_ledger_inconsistent()
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise evidence_ledger_inconsistent()
    return (
        _validated_runner_generations
        if _validated_runner_generations is not None
        else {}
    )


def _validated_completion_evidence_projection_bases(
    connection: sqlite3.Connection,
    *,
    _validated_runner_generations: (
        dict[tuple[str, str, int], dict[str, Any]] | None
    ) = None,
) -> dict[str, EvidenceProjectionBasis]:
    _validate_completion_evidence_bundle_schema_contract(connection)
    runner_generations = _validate_completion_evidence_bundle_rows(
        connection,
        _validated_runner_generations=_validated_runner_generations,
    )
    project_rows = connection.execute(
        "SELECT project_id FROM project_meta ORDER BY project_id"
    ).fetchall()
    project_ids = tuple(row["project_id"] for row in project_rows)
    if (
        any(
            type(project_id) is not str or not project_id
            for project_id in project_ids
        )
        or len(set(project_ids)) != len(project_ids)
    ):
        raise evidence_ledger_inconsistent()
    result: dict[str, EvidenceProjectionBasis] = {}
    for project_id in project_ids:
        basis = _capture_evidence_projection_basis_rows(
            connection,
            project_id=project_id,
            _validated_runner_generations=runner_generations,
        )
        for record in basis.native_bundles:
            _validate_projection_bundle_record(record)
        result[project_id] = basis
    return result


def validate_completion_evidence_bundle_storage(
    connection: sqlite3.Connection,
) -> None:
    _validated_completion_evidence_projection_bases(connection)


def _validate_evidence_ledger_storage_with_task_rows(
    connection: sqlite3.Connection,
    *,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> tuple[sqlite3.Row, ...]:
    _validate_evidence_ledger_schema_contract(connection)
    task_rows = _validate_evidence_ledger_rows(
        connection,
        privacy_success_cache=privacy_success_cache,
    )
    if current_schema_version(connection) >= 19:
        validate_completion_evidence_bundle_storage(connection)
    return task_rows


def validate_evidence_ledger_storage(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate the complete current Evidence storage and row relations."""

    if _privacy_success_cache is not None and (
        type(_privacy_success_cache) is not set
        or any(
            type(item) is not tuple
            or len(item) != 3
            or any(type(value) is not str for value in item)
            or item[0] not in {"ordinary", "legacy_m19_7_stored"}
            for item in _privacy_success_cache
        )
    ):
        raise evidence_ledger_inconsistent()
    _validate_evidence_ledger_storage_with_task_rows(
        connection,
        privacy_success_cache=_privacy_success_cache,
    )


def validate_evidence_ledger_storage_for_recovery(
    connection: sqlite3.Connection,
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate current Evidence storage with the Task-local recovery exception."""

    if _privacy_success_cache is not None and (
        type(_privacy_success_cache) is not set
        or any(
            type(item) is not tuple
            or len(item) != 3
            or any(type(value) is not str for value in item)
            or item[0] not in {"ordinary", "legacy_m19_7_stored"}
            for item in _privacy_success_cache
        )
    ):
        raise evidence_ledger_inconsistent()
    _validate_evidence_ledger_schema_contract(connection)
    task_rejection: StoredTaskVerificationError | None = None
    try:
        _validate_evidence_ledger_rows(
            connection,
            verification_rejection_is_local=True,
            privacy_success_cache=_privacy_success_cache,
        )
    except StoredTaskVerificationError as exc:
        task_rejection = exc
    if current_schema_version(connection) >= 19:
        validate_completion_evidence_bundle_storage(connection)
    if task_rejection is not None:
        raise task_rejection


def validate_selected_task_receipt_evidence(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    review_receipt_ids: set[str],
    review_finding_ids: set[str],
    verification_receipt_ids: set[str],
) -> None:
    """Validate one bounded Receipt/Finding source set and its bindings."""

    if (
        type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(review_receipt_ids) is not set
        or type(review_finding_ids) is not set
        or type(verification_receipt_ids) is not set
        or len(review_receipt_ids) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        or len(review_finding_ids) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        or len(verification_receipt_ids)
        > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        or any(
            type(value) is not str or not value
            for value in (
                *review_receipt_ids,
                *review_finding_ids,
                *verification_receipt_ids,
            )
        )
    ):
        raise evidence_ledger_inconsistent()
    try:
        review_privacy_successes: set[tuple[str, str, str]] = set()
        finding_rows = _selected_storage_rows_by_ids(
            connection,
            table_name="review_findings",
            id_field="review_finding_id",
            selected_ids=review_finding_ids,
        )
        parent_receipt_ids: set[str] = set()
        for row in finding_rows:
            _validate_review_finding_base_row(
                row,
                privacy_success_cache=review_privacy_successes,
            )
            receipt_id = row["review_receipt_id"]
            if type(receipt_id) is not str or not receipt_id:
                raise evidence_ledger_inconsistent()
            parent_receipt_ids.add(receipt_id)
        selected_review_receipt_ids = review_receipt_ids | parent_receipt_ids
        if (
            len(selected_review_receipt_ids)
            > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        ):
            raise evidence_ledger_inconsistent()
        review_rows = _selected_storage_rows_by_ids(
            connection,
            table_name="review_receipts",
            id_field="review_receipt_id",
            selected_ids=selected_review_receipt_ids,
        )
        verification_rows = _selected_storage_rows_by_ids(
            connection,
            table_name="verification_receipts",
            id_field="verification_receipt_id",
            selected_ids=verification_receipt_ids,
        )
        for row in review_rows:
            _validate_review_receipt_base_row(
                row,
                privacy_success_cache=review_privacy_successes,
            )
            if row["project_id"] != project_id or row["task_id"] != task_id:
                raise evidence_ledger_inconsistent()
        for row in verification_rows:
            try:
                receipt = _validate_verification_receipt_row(dict(row))
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            if receipt["project_id"] != project_id or receipt["task_id"] != task_id:
                raise evidence_ledger_inconsistent()

        target_keys = {
            _artifact_manifest_target_key(
                project_id=row["project_id"],
                task_id=row["task_id"],
                target_kind=row["target_kind"],
                target_value=row["target_value"],
                target_base_revision=row["target_base_revision"],
                target_generation=row["target_generation"],
            )
            for row in (*review_rows, *verification_rows)
        }
        targets_json = json.dumps(
            [list(key) for key in sorted(target_keys)],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        manifest_rows = connection.execute(
            """
            WITH selected_targets(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT manifest.artifact_manifest_id,
                   manifest.authority_snapshot_id
              FROM artifact_manifests AS manifest
              JOIN selected_targets AS selected
                ON manifest.project_id = json_extract(selected.value, '$[0]')
               AND manifest.task_id = json_extract(selected.value, '$[1]')
               AND manifest.target_kind = json_extract(selected.value, '$[2]')
               AND manifest.target_value = json_extract(selected.value, '$[3]')
               AND manifest.target_base_revision = json_extract(selected.value, '$[4]')
               AND manifest.target_generation = json_extract(selected.value, '$[5]')
             ORDER BY manifest.artifact_manifest_id
            """,
            (targets_json,),
        ).fetchall()
        manifest_ids: set[str] = set()
        snapshot_ids: set[str] = set()
        for row in manifest_rows:
            manifest_id = row["artifact_manifest_id"]
            snapshot_id = row["authority_snapshot_id"]
            if (
                type(manifest_id) is not str
                or manifest_id in manifest_ids
                or type(snapshot_id) is not str
            ):
                raise evidence_ledger_inconsistent()
            manifest_ids.add(manifest_id)
            snapshot_ids.add(snapshot_id)

        authority = _validated_authority_context(
            connection,
            snapshot_ids=snapshot_ids,
        )
        manifests, manifests_by_target = _validate_artifact_manifest_storage(
            connection,
            snapshots=authority.snapshots,
            links=authority.links,
            manifest_ids=manifest_ids,
        )
        _validate_stored_verification_subject_rows(
            verification_rows,
            table_name="verification_receipts",
            snapshots=authority.snapshots,
            criteria=authority.criteria,
            links=authority.links,
        )
        _validate_evidence_reference_storage(
            connection,
            manifests=manifests,
            manifests_by_target=manifests_by_target,
            snapshots=authority.snapshots,
            verification_receipt_ids=verification_receipt_ids,
            review_receipt_ids=selected_review_receipt_ids,
            review_finding_ids=review_finding_ids,
            completion_cycle_ids=set(),
            selected_project_id=project_id,
            privacy_success_cache=review_privacy_successes,
        )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc


_REVIEW_RECEIPT_INSERT_FIELDS = (
    "review_receipt_id",
    "task_id",
    "project_id",
    "reviewer_key",
    "receipt_kind",
    "verdict",
    "target_kind",
    "target_value",
    "target_base_revision",
    "target_generation",
    "summary",
    "user_approved",
    "created_at",
)


def _review_provenance_from_storage(
    provenance_row: sqlite3.Row,
    code_rows: tuple[sqlite3.Row, ...],
) -> dict[str, Any]:
    codes = {"profile": [], "lens": [], "method": []}
    for row in code_rows:
        kind = str(row["code_kind"])
        if kind not in codes:
            raise evidence_ledger_inconsistent()
        codes[kind].append(str(row["code"]))
    return {
        "review_provenance_id": str(provenance_row["review_provenance_id"]),
        "provenance_version": provenance_row["provenance_version"],
        "reviewer_class": provenance_row["reviewer_class"],
        "model_state": provenance_row["model_state"],
        "declared_model_id": provenance_row["declared_model_id"],
        "skill_state": provenance_row["skill_state"],
        "declared_skill_id": provenance_row["declared_skill_id"],
        "declared_skill_version": provenance_row["declared_skill_version"],
        "review_profiles": codes["profile"],
        "review_lenses": codes["lens"],
        "context_relation": provenance_row["context_relation"],
        "method_codes": codes["method"],
        "assurance_class": provenance_row["assurance_class"],
        "producer_class": provenance_row["producer_class"],
        "producer_version": provenance_row["producer_version"],
        "digest": provenance_row["digest"],
    }


def _review_provenance_code_caps() -> dict[str, int]:
    from task_governance_tool.review_provenance import (
        REVIEW_LENSES,
        REVIEW_METHODS,
        REVIEW_PROFILES,
    )

    return {
        "profile": min(4, len(REVIEW_PROFILES)),
        "lens": min(8, len(REVIEW_LENSES)),
        "method": min(8, len(REVIEW_METHODS)),
    }


def _validate_review_receipt_base_row(
    receipt: sqlite3.Row | dict[str, Any],
    *,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    receipt_id = receipt["review_receipt_id"]
    project_id = receipt["project_id"]
    task_id = receipt["task_id"]
    reviewer_key = receipt["reviewer_key"]
    receipt_kind = receipt["receipt_kind"]
    verdict = receipt["verdict"]
    target_kind = receipt["target_kind"]
    target_value = receipt["target_value"]
    target_base_revision = receipt["target_base_revision"]
    target_generation = receipt["target_generation"]
    summary = receipt["summary"]
    user_approved = receipt["user_approved"]
    created_at = receipt["created_at"]
    provenance_basis = receipt["review_provenance_basis_version"]
    if (
        type(receipt_id) is not str
        or not 1 <= len(receipt_id) <= 128
        or type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(reviewer_key) is not str
        or type(receipt_kind) is not str
        or receipt_kind
        not in {"independent", "self_review_fallback", "not_required"}
        or type(verdict) is not str
        or verdict not in {"pass", "changes_requested", "not_required"}
        or type(target_kind) is not str
        or type(target_value) is not str
        or type(target_base_revision) is not str
        or type(target_generation) is not int
        or type(summary) is not str
        or type(user_approved) is not int
        or user_approved not in {0, 1}
        or type(created_at) is not str
        or type(provenance_basis) is not int
        or provenance_basis not in {0, 1}
    ):
        raise evidence_ledger_inconsistent()
    cache = privacy_success_cache if privacy_success_cache is not None else set()
    if type(cache) is not set:
        raise evidence_ledger_inconsistent()
    _validate_evidence_ledger_stored_privacy(
        "reviewer_key",
        reviewer_key,
        privacy_success_cache=cache,
    )
    _validate_evidence_ledger_stored_privacy(
        "review_receipt_summary",
        summary,
        privacy_success_cache=cache,
    )
    if (
        not reviewer_key
        or reviewer_key != reviewer_key.strip()
        or len(reviewer_key) > 500
        or len(summary) > 1_000
        or (
            receipt_kind == "independent"
            and (verdict not in {"pass", "changes_requested"} or user_approved != 0)
        )
        or (
            receipt_kind == "self_review_fallback"
            and (
                verdict not in {"pass", "changes_requested"}
                or not summary.strip()
                or (verdict == "changes_requested" and user_approved != 0)
            )
        )
        or (
            receipt_kind == "not_required"
            and (
                verdict != "not_required"
                or user_approved != 0
                or not summary.strip()
            )
        )
        or (verdict == "changes_requested" and not summary.strip())
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            created_at,
            field="Review Receipt creation time",
        )
        _validate_completion_target(
            kind=target_kind,
            value=target_value,
            base_revision=target_base_revision,
            generation=target_generation,
        )
    except StorageError as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def _validate_native_review_receipt_tier(
    receipt: sqlite3.Row | dict[str, Any],
    *,
    review_tier: object,
) -> None:
    """Enforce creation-time semantics from the manifest-bound snapshot."""

    if type(review_tier) is not int or review_tier not in {0, 1, 2}:
        raise evidence_ledger_inconsistent()
    receipt_kind = receipt["receipt_kind"]
    verdict = receipt["verdict"]
    user_approved = receipt["user_approved"]
    if receipt_kind == "self_review_fallback":
        if (
            review_tier not in {1, 2}
            or user_approved != int(review_tier == 2 and verdict == "pass")
        ):
            raise evidence_ledger_inconsistent()
    elif receipt_kind == "not_required" and review_tier != 0:
        raise evidence_ledger_inconsistent()


def validate_stored_review_receipt_projection(
    receipt: sqlite3.Row | dict[str, Any],
    *,
    source_schema_version: object,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate one source-schema Review Receipt before public projection."""

    if (
        type(source_schema_version) is not int
        or not 5 <= source_schema_version <= SCHEMA_VERSION
    ):
        raise evidence_ledger_inconsistent()
    try:
        stored = dict(receipt)
    except (TypeError, ValueError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    if source_schema_version < 6:
        stored["target_base_revision"] = ""
    if source_schema_version < 18:
        stored["review_provenance_basis_version"] = 0
        stored["review_provenance_id"] = None
    _validate_review_receipt_base_row(
        stored,
        privacy_success_cache=_privacy_success_cache,
    )


def validate_stored_review_finding_projection(
    finding: sqlite3.Row | dict[str, Any],
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate one stored Review Finding before public projection."""

    _validate_review_finding_base_row(
        finding,
        privacy_success_cache=_privacy_success_cache,
    )


def _validate_review_provenance_relation(
    receipt: sqlite3.Row,
    provenance_row: sqlite3.Row,
    code_rows: tuple[sqlite3.Row, ...],
) -> dict[str, Any]:
    """Validate one exact stored Receipt/provenance/code relation."""

    provenance_id = provenance_row["review_provenance_id"]
    if (
        type(provenance_id) is not str
        or provenance_id != receipt["review_provenance_id"]
        or provenance_row["review_receipt_id"] != receipt["review_receipt_id"]
        or provenance_row["project_id"] != receipt["project_id"]
        or provenance_row["task_id"] != receipt["task_id"]
        or type(receipt["created_at"]) is not str
        or type(provenance_row["created_at"]) is not str
        or provenance_row["created_at"] != receipt["created_at"]
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            receipt["created_at"],
            field="Review Receipt creation time",
        )
        validate_utc_timestamp(
            provenance_row["created_at"],
            field="Review provenance creation time",
        )
    except StorageError as exc:
        raise evidence_ledger_boundary_error(exc) from exc

    code_caps = _review_provenance_code_caps()
    group_ordinals = {kind: 0 for kind in code_caps}
    if len(code_rows) > sum(code_caps.values()):
        raise evidence_ledger_inconsistent()
    for code_row in code_rows:
        code_kind = code_row["code_kind"]
        if code_kind not in code_caps:
            raise evidence_ledger_inconsistent()
        ordinal = group_ordinals[code_kind]
        if (
            code_row["review_provenance_id"] != provenance_id
            or code_row["project_id"] != receipt["project_id"]
            or code_row["task_id"] != receipt["task_id"]
            or type(code_row["ordinal"]) is not int
            or code_row["ordinal"] != ordinal
            or ordinal >= code_caps[code_kind]
        ):
            raise evidence_ledger_inconsistent()
        group_ordinals[code_kind] = ordinal + 1

    provenance = _review_provenance_from_storage(provenance_row, code_rows)
    try:
        from task_governance_tool.review_provenance import (
            ReviewProvenanceError,
            validate_stored_review_provenance_v1,
        )

        return validate_stored_review_provenance_v1(
            provenance,
            project_id=receipt["project_id"],
            task_id=receipt["task_id"],
            review_receipt_id=receipt["review_receipt_id"],
            receipt_kind=receipt["receipt_kind"],
            target={
                "kind": receipt["target_kind"],
                "value": receipt["target_value"],
                "base_revision": receipt["target_base_revision"],
                "generation": receipt["target_generation"],
                "capture_version": 1,
            },
        )
    except ReviewProvenanceError as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def read_review_receipt_with_provenance(
    connection: sqlite3.Connection,
    *,
    review_receipt_id: str,
) -> dict[str, Any] | None:
    """Read and validate one exact Receipt plus its versioned provenance."""

    receipt = connection.execute(
        "SELECT * FROM review_receipts WHERE review_receipt_id = ?",
        (review_receipt_id,),
    ).fetchone()
    if receipt is None:
        return None
    _validate_review_receipt_base_row(
        receipt,
        privacy_success_cache=set(),
    )
    basis = receipt["review_provenance_basis_version"]
    provenance_id = receipt["review_provenance_id"]
    if type(basis) is not int or basis not in {0, 1}:
        raise evidence_ledger_inconsistent()
    if basis == 0:
        if provenance_id is not None:
            raise evidence_ledger_inconsistent()
        if connection.execute(
            """
            SELECT 1 FROM review_receipt_provenance
             WHERE review_receipt_id = ?
             LIMIT 1
            """,
            (review_receipt_id,),
        ).fetchone() is not None:
            raise evidence_ledger_inconsistent()
        return {"receipt": dict(receipt), "provenance": None}
    if provenance_id is None:
        raise evidence_ledger_inconsistent()
    provenance_row = connection.execute(
        """
        SELECT * FROM review_receipt_provenance
         WHERE review_provenance_id = ?
        """,
        (provenance_id,),
    ).fetchone()
    code_cap = sum(_review_provenance_code_caps().values())
    code_rows = tuple(connection.execute(
        """
        SELECT * FROM review_receipt_provenance_codes
         WHERE review_provenance_id = ?
         ORDER BY CASE code_kind
                    WHEN 'profile' THEN 0
                    WHEN 'lens' THEN 1
                    WHEN 'method' THEN 2
                    ELSE 3
                  END,
                  ordinal
         LIMIT ?
        """,
        (provenance_id, code_cap + 1),
    ).fetchall())
    if provenance_row is None or len(code_rows) > code_cap:
        raise evidence_ledger_inconsistent()
    validated = _validate_review_provenance_relation(
        receipt,
        provenance_row,
        code_rows,
    )
    return {"receipt": dict(receipt), "provenance": validated}


def insert_review_receipt_with_provenance_locked(
    connection: sqlite3.Connection,
    receipt: dict[str, Any],
    provenance: dict[str, Any] | None,
    code_rows: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Atomically append one current Receipt and its exact provenance union."""

    _require_evidence_writer(connection)
    if set(receipt) != set(_REVIEW_RECEIPT_INSERT_FIELDS):
        raise evidence_ledger_inconsistent()
    task = _read_validated_current_task_row(
        connection,
        project_id=str(receipt.get("project_id", "")),
        task_id=str(receipt.get("task_id", "")),
    )
    if (
        task is None
        or task["review_target_capture_version"] != 1
        or task["review_target_artifact_manifest_id"] is None
        or any(
            receipt[field] != task[f"review_target_{field.removeprefix('target_')}"]
            for field in (
                "target_kind",
                "target_value",
                "target_base_revision",
                "target_generation",
            )
        )
    ):
        raise evidence_ledger_inconsistent()
    kind = receipt["receipt_kind"]
    if kind == "not_required":
        if provenance is not None or code_rows:
            raise evidence_ledger_inconsistent()
        basis = 0
        provenance_id = None
    else:
        if kind not in {"independent", "self_review_fallback"} or provenance is None:
            raise evidence_ledger_inconsistent()
        try:
            from task_governance_tool.review_provenance import (
                ReviewProvenanceError,
                validate_stored_review_provenance_v1,
            )

            validated = validate_stored_review_provenance_v1(
                provenance,
                project_id=receipt["project_id"],
                task_id=receipt["task_id"],
                review_receipt_id=receipt["review_receipt_id"],
                receipt_kind=kind,
                target={
                    "kind": receipt["target_kind"],
                    "value": receipt["target_value"],
                    "base_revision": receipt["target_base_revision"],
                    "generation": receipt["target_generation"],
                    "capture_version": 1,
                },
            )
        except ReviewProvenanceError as exc:
            raise evidence_ledger_boundary_error(exc) from exc
        provenance_id = str(validated["review_provenance_id"])
        expected_codes = tuple(
            {
                "project_id": receipt["project_id"],
                "task_id": receipt["task_id"],
                "review_provenance_id": provenance_id,
                "code_kind": code_kind,
                "ordinal": ordinal,
                "code": code,
            }
            for code_kind, field in (
                ("profile", "review_profiles"),
                ("lens", "review_lenses"),
                ("method", "method_codes"),
            )
            for ordinal, code in enumerate(validated[field])
        )
        if code_rows != expected_codes:
            raise evidence_ledger_inconsistent()
        basis = 1

    stored_receipt = {
        **receipt,
        "review_provenance_basis_version": basis,
        "review_provenance_id": provenance_id,
    }
    _validate_review_receipt_base_row(
        stored_receipt,
        privacy_success_cache=set(),
    )
    connection.execute(
        """
        INSERT INTO review_receipts(
          review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
          verdict, target_kind, target_value, target_base_revision,
          target_generation, summary, user_approved, created_at,
          review_provenance_basis_version, review_provenance_id
        ) VALUES (
          :review_receipt_id, :task_id, :project_id, :reviewer_key, :receipt_kind,
          :verdict, :target_kind, :target_value, :target_base_revision,
          :target_generation, :summary, :user_approved, :created_at,
          :review_provenance_basis_version, :review_provenance_id
        )
        """,
        stored_receipt,
    )
    if provenance_id is not None:
        connection.execute(
            """
            INSERT INTO review_receipt_provenance(
              review_provenance_id, review_receipt_id, project_id, task_id,
              provenance_version, reviewer_class, model_state,
              declared_model_id, skill_state, declared_skill_id,
              declared_skill_version, context_relation, assurance_class,
              producer_class, producer_version, digest, created_at
            ) VALUES (
              :review_provenance_id, :review_receipt_id, :project_id, :task_id,
              :provenance_version, :reviewer_class, :model_state,
              :declared_model_id, :skill_state, :declared_skill_id,
              :declared_skill_version, :context_relation, :assurance_class,
              :producer_class, :producer_version, :digest, :created_at
            )
            """,
            {
                **provenance,
                "review_receipt_id": receipt["review_receipt_id"],
                "project_id": receipt["project_id"],
                "task_id": receipt["task_id"],
                "created_at": receipt["created_at"],
            },
        )
        for row in code_rows:
            connection.execute(
                """
                INSERT INTO review_receipt_provenance_codes(
                  project_id, task_id, review_provenance_id,
                  code_kind, ordinal, code
                ) VALUES (
                  :project_id, :task_id, :review_provenance_id,
                  :code_kind, :ordinal, :code
                )
                """,
                row,
            )
    result = read_review_receipt_with_provenance(
        connection,
        review_receipt_id=str(receipt["review_receipt_id"]),
    )
    if result is None:
        raise evidence_ledger_inconsistent()
    return result


def persist_artifact_manifest_locked(
    connection: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    entries: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Persist one already-normalized manifest; derivation remains service-owned."""

    _require_evidence_writer(connection)
    manifest_fields = tuple(sorted(
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS["artifact_manifests"]
    ))
    if set(manifest) != set(manifest_fields):
        raise evidence_ledger_inconsistent()
    entry_fields = tuple(sorted(
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS["artifact_manifest_entries"]
    ))
    if any(set(entry) != set(entry_fields) for entry in entries):
        raise evidence_ledger_inconsistent()
    columns = ", ".join(manifest_fields)
    values = ", ".join(f":{name}" for name in manifest_fields)
    connection.execute(
        f"INSERT INTO artifact_manifests({columns}) VALUES ({values})",
        manifest,
    )
    for entry in entries:
        entry_columns = ", ".join(entry_fields)
        entry_values = ", ".join(f":{name}" for name in entry_fields)
        connection.execute(
            f"INSERT INTO artifact_manifest_entries({entry_columns}) VALUES ({entry_values})",
            entry,
        )
    row = connection.execute(
        "SELECT * FROM artifact_manifests WHERE artifact_manifest_id = ?",
        (manifest["artifact_manifest_id"],),
    ).fetchone()
    if row is None:
        raise evidence_ledger_inconsistent()
    return dict(row)


def persist_evidence_reference_locked(
    connection: sqlite3.Connection,
    *,
    reference: dict[str, Any],
) -> dict[str, Any]:
    """Persist one already-derived closed reference in the caller transaction."""

    _require_evidence_writer(connection)
    fields = tuple(sorted(_EVIDENCE_LEDGER_REQUIRED_COLUMNS["evidence_references"]))
    if set(reference) != set(fields):
        raise evidence_ledger_inconsistent()
    columns = ", ".join(fields)
    values = ", ".join(f":{name}" for name in fields)
    connection.execute(
        f"INSERT INTO evidence_references({columns}) VALUES ({values})",
        reference,
    )
    return read_evidence_reference(
        connection,
        evidence_reference_id=str(reference["evidence_reference_id"]),
    )


def read_evidence_reference(
    connection: sqlite3.Connection,
    *,
    evidence_reference_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM evidence_references WHERE evidence_reference_id = ?",
        (evidence_reference_id,),
    ).fetchone()
    if row is None:
        raise evidence_ledger_inconsistent()
    return dict(row)


def _prepared_row(value: object, fields: set[str]) -> dict[str, Any]:
    try:
        result = {name: getattr(value, name) for name in fields}
    except (AttributeError, TypeError) as exc:
        raise evidence_ledger_inconsistent() from exc
    if set(result) != fields:
        raise evidence_ledger_inconsistent()
    return result


def _validate_prepared_criterion_evidence_link(
    connection: sqlite3.Connection,
    link: PreparedCriterionEvidenceLink,
) -> None:
    if (
        not isinstance(link, PreparedCriterionEvidenceLink)
        or CRITERION_EVIDENCE_LINK_ID_PATTERN.fullmatch(
            link.criterion_evidence_link_id
        )
        is None
        or type(link.project_id) is not str
        or not link.project_id
        or type(link.task_id) is not str
        or not link.task_id
        or type(link.criterion_id) is not str
        or not link.criterion_id
        or type(link.evidence_reference_id) is not str
        or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(
            link.evidence_reference_id
        )
        is None
        or type(link.relation) is not str
        or link.relation not in CRITERION_EVIDENCE_RELATIONS
        or link.relation == "derived_analysis"
        or type(link.assurance_class) is not str
        or type(link.producer_class) is not str
        or type(link.producer_version) is not int
        or link.producer_version <= 0
        or type(link.created_at) is not str
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            link.created_at,
            field="criterion Evidence Link creation time",
        )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    relation = connection.execute(
        """
        SELECT criterion.criterion_kind, reference.source_kind,
               reference.assurance_class, reference.producer_class,
               reference.producer_version
          FROM contract_criteria AS criterion
          JOIN evidence_references AS reference
            ON reference.project_id = criterion.project_id
           AND reference.task_id = criterion.task_id
         WHERE criterion.project_id = ?
           AND criterion.task_id = ?
           AND criterion.criterion_id = ?
           AND reference.evidence_reference_id = ?
        """,
        (
            link.project_id,
            link.task_id,
            link.criterion_id,
            link.evidence_reference_id,
        ),
    ).fetchone()
    if (
        relation is None
        or relation["assurance_class"] != link.assurance_class
        or relation["producer_class"] != link.producer_class
        or relation["producer_version"] != link.producer_version
        or not _criterion_link_relation_valid(
            criterion_kind=relation["criterion_kind"],
            source_kind=relation["source_kind"],
            relation=link.relation,
        )
    ):
        raise evidence_ledger_inconsistent()


def persist_criterion_evidence_link_locked(
    connection: sqlite3.Connection,
    *,
    link: PreparedCriterionEvidenceLink,
) -> PreparedCriterionEvidenceLink:
    """Persist or exactly reuse one append-only criterion Evidence Link."""

    _require_evidence_writer(connection)
    _validate_prepared_criterion_evidence_link(connection, link)
    fields = _EVIDENCE_LEDGER_REQUIRED_COLUMNS["criterion_evidence_links"]
    values = _prepared_row(link, fields)
    existing = connection.execute(
        """
        SELECT * FROM criterion_evidence_links
         WHERE criterion_evidence_link_id = ?
        """,
        (link.criterion_evidence_link_id,),
    ).fetchone()
    if existing is None:
        ordered = tuple(sorted(fields))
        try:
            connection.execute(
                f"INSERT INTO criterion_evidence_links("
                f"{', '.join(ordered)}) VALUES ("
                f"{', '.join(':' + name for name in ordered)})",
                values,
            )
        except sqlite3.IntegrityError as exc:
            raise evidence_ledger_inconsistent() from exc
        existing = connection.execute(
            "SELECT * FROM criterion_evidence_links "
            "WHERE criterion_evidence_link_id = ?",
            (link.criterion_evidence_link_id,),
        ).fetchone()
    if existing is None or dict(existing) != values:
        raise evidence_ledger_inconsistent()
    return link


def _verification_runner_value_dict(
    value: object,
    value_type: type[Any],
) -> dict[str, Any]:
    if not isinstance(value, value_type):
        raise evidence_ledger_inconsistent()
    fields = set(value_type.__dataclass_fields__)
    return _prepared_row(value, fields)


def read_current_verification_runner_target_basis(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Read the exact current target/authority basis used by Runner T1."""

    if (
        current_schema_version(connection)
        not in {PRIVATE_SCHEMA20_VERSION, PRIVATE_SCHEMA21_VERSION}
        or type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
    ):
        raise evidence_ledger_inconsistent()
    task = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    snapshot_id = task["review_target_authority_snapshot_id"]
    acceptance_id = task["review_target_acceptance_criterion_id"]
    verification_id = task["review_target_verification_criterion_id"]
    manifest_id = task["review_target_artifact_manifest_id"]
    snapshot = connection.execute(
        "SELECT * FROM authority_snapshots "
        "WHERE project_id = ? AND task_id = ? AND authority_snapshot_id = ?",
        (project_id, task_id, snapshot_id),
    ).fetchone()
    criterion = connection.execute(
        "SELECT * FROM contract_criteria "
        "WHERE project_id = ? AND task_id = ? AND criterion_id = ?",
        (project_id, task_id, verification_id),
    ).fetchone()
    manifest = connection.execute(
        "SELECT * FROM artifact_manifests "
        "WHERE project_id = ? AND task_id = ? AND artifact_manifest_id = ?",
        (project_id, task_id, manifest_id),
    ).fetchone()
    contract_revision = task["current_contract_revision"]
    target_kind = task["review_target_kind"]
    target_value = task["review_target_value"]
    target_base_revision = task["review_target_base_revision"]
    target_generation = task["review_target_generation"]
    target_capture_version = task["review_target_capture_version"]
    if (
        type(contract_revision) is not int
        or contract_revision < 1
        or type(snapshot_id) is not str
        or snapshot is None
        or snapshot["contract_revision"] != contract_revision
        or snapshot["verification_digest"]
        != _verification_expectation_digest(str(task["verification"]))
        or (
            acceptance_id is not None
            and type(acceptance_id) is not str
        )
        or type(verification_id) is not str
        or criterion is None
        or criterion["criterion_kind"] != "verification"
        or type(criterion["digest"]) is not str
        or SHA256_DIGEST_PATTERN.fullmatch(criterion["digest"]) is None
        or type(manifest_id) is not str
        or manifest is None
        or manifest["state"] != "complete_git"
        or manifest["authority_snapshot_id"] != snapshot_id
        or manifest["acceptance_criterion_id"] != acceptance_id
        or manifest["verification_criterion_id"] != verification_id
        or target_kind not in {"git_snapshot", "git_commit"}
        or type(target_value) is not str
        or not target_value
        or type(target_base_revision) is not str
        or type(target_generation) is not int
        or target_generation < 1
        or target_capture_version != 1
        or type(task["review_target_runner_basis_version"]) is not int
        or task["review_target_runner_basis_version"] not in {0, 2}
        or (
            task["review_target_runner_basis_version"] == 2
            and current_schema_version(connection) != PRIVATE_SCHEMA21_VERSION
        )
        or manifest["target_kind"] != target_kind
        or manifest["target_value"] != target_value
        or manifest["target_base_revision"] != target_base_revision
        or manifest["target_generation"] != target_generation
    ):
        raise evidence_ledger_inconsistent()
    return {
        "contract_revision": contract_revision,
        "authority_snapshot_id": snapshot_id,
        "acceptance_criterion_id": acceptance_id,
        "verification_criterion_id": verification_id,
        "verification_expectation_digest": snapshot["verification_digest"],
        "verification_criterion_digest": criterion["digest"],
        "target_kind": target_kind,
        "target_value": target_value,
        "target_base_revision": target_base_revision,
        "target_generation": target_generation,
        "target_capture_version": target_capture_version,
        "artifact_manifest_id": manifest_id,
        "gate_eligibility_version": (
            1 if task["review_target_runner_basis_version"] == 2 else 0
        ),
    }


def _read_verification_runner_generation_unvalidated(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    target_generation: int,
) -> dict[str, Any] | None:
    specs = (
        (
            "resolution",
            "verification_runner_resolutions",
            VerificationRunnerResolution,
        ),
        (
            "attempt",
            "verification_runner_attempts",
            VerificationRunnerAttempt,
        ),
        (
            "observation",
            "verification_runner_observations",
            VerificationRunnerObservation,
        ),
        (
            "cleanup_event",
            "verification_runner_sandbox_events",
            VerificationRunnerSandboxEvent,
        ),
    )
    values: dict[str, Any] = {}
    for name, table_name, value_type in specs:
        rows = connection.execute(
            f"SELECT * FROM {table_name} "
            "WHERE project_id = ? AND task_id = ? AND target_generation = ? "
            "ORDER BY rowid LIMIT 2",
            (project_id, task_id, target_generation),
        ).fetchall()
        if len(rows) > 1:
            raise evidence_ledger_inconsistent()
        values[name] = (
            _runner_row_value(rows[0], value_type) if rows else None
        )
    if all(value is None for value in values.values()):
        return None
    if values["resolution"] is None or values["attempt"] is None:
        raise evidence_ledger_inconsistent()
    if values["observation"] is not None:
        state = "terminal"
        if values["cleanup_event"] is None:
            raise evidence_ledger_inconsistent()
    elif values["cleanup_event"] is not None:
        state = "restart_cleaned"
    else:
        state = "pending"
    return {"state": state, **values}


def read_verification_runner_generation_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    target_generation: int,
) -> dict[str, Any] | None:
    """Read one fully validated generation; the name denotes DB serialization."""

    if (
        type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(target_generation) is not int
        or target_generation < 1
    ):
        raise evidence_ledger_inconsistent()
    _validated_verification_runner_references(connection)
    return _read_verification_runner_generation_unvalidated(
        connection,
        project_id=project_id,
        task_id=task_id,
        target_generation=target_generation,
    )


def _verification_runner_gate_storage_token(
    *,
    marker: int,
    basis_matches: bool,
    generation: dict[str, Any],
    reference: dict[str, Any] | None,
    criterion_link: dict[str, Any] | None,
) -> tuple[object, ...]:
    resolution = generation["resolution"]
    attempt = generation["attempt"]
    observation = generation["observation"]
    cleanup = generation["cleanup_event"]
    return (
        marker,
        generation["state"],
        basis_matches,
        resolution.verification_runner_resolution_id,
        resolution.idempotency_digest,
        attempt.verification_runner_attempt_id,
        attempt.attempt_digest,
        (
            observation.verification_runner_observation_id
            if observation is not None
            else None
        ),
        observation.sanitized_result_digest if observation is not None else None,
        (
            cleanup.verification_runner_sandbox_event_id
            if cleanup is not None
            else None
        ),
        cleanup.event_digest if cleanup is not None else None,
        reference["evidence_reference_id"] if reference is not None else None,
        reference["digest"] if reference is not None else None,
        (
            criterion_link["criterion_evidence_link_id"]
            if criterion_link is not None
            else None
        ),
        criterion_link["relation"] if criterion_link is not None else None,
    )


def _read_current_verification_runner_gate_snapshot(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Return validated current Runner graph facts without selecting an outcome."""

    if (
        current_schema_version(connection) != PRIVATE_SCHEMA21_VERSION
        or type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
    ):
        raise evidence_ledger_inconsistent()
    task = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    marker = task["review_target_runner_basis_version"]
    target_generation = task["review_target_generation"]
    if marker != 2 or type(target_generation) is not int or target_generation < 1:
        raise evidence_ledger_inconsistent()
    generation_key = (project_id, task_id, target_generation)
    _references, generations = _validated_verification_runner_graph(
        connection,
        selected_generation=generation_key,
        selected_task=task,
    )
    generation = generations.get(generation_key)
    if generation is None:
        raise evidence_ledger_inconsistent()
    resolution = generation["resolution"]
    manifest = connection.execute(
        "SELECT acceptance_criterion_id FROM artifact_manifests "
        "WHERE project_id = ? AND task_id = ? AND artifact_manifest_id = ?",
        (project_id, task_id, resolution.artifact_manifest_id),
    ).fetchone()
    if manifest is None:
        raise evidence_ledger_inconsistent()
    current_basis = read_current_verification_runner_target_basis(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    expected_basis = _verification_runner_current_basis_for_resolution(
        resolution,
        acceptance_criterion_id=manifest["acceptance_criterion_id"],
    )
    basis_matches = current_basis == expected_basis

    observation = generation["observation"]
    reference: dict[str, Any] | None = None
    criterion_link: dict[str, Any] | None = None
    if observation is not None:
        reference_rows = connection.execute(
            "SELECT * FROM evidence_references "
            "WHERE project_id = ? AND task_id = ? "
            "AND source_kind = 'runner_observation' AND source_id = ? "
            "ORDER BY evidence_reference_id LIMIT 2",
            (
                project_id,
                task_id,
                observation.verification_runner_observation_id,
            ),
        ).fetchall()
        if len(reference_rows) != 1:
            raise evidence_ledger_inconsistent()
        reference = dict(reference_rows[0])
        link_rows = connection.execute(
            "SELECT * FROM criterion_evidence_links "
            "WHERE project_id = ? AND task_id = ? "
            "AND relation = 'runner_observation' "
            "AND evidence_reference_id = ? "
            "ORDER BY criterion_evidence_link_id LIMIT 2",
            (project_id, task_id, reference["evidence_reference_id"]),
        ).fetchall()
        if len(link_rows) != 1:
            raise evidence_ledger_inconsistent()
        criterion_link = dict(link_rows[0])

    storage_token = _verification_runner_gate_storage_token(
        marker=marker,
        basis_matches=basis_matches,
        generation=generation,
        reference=reference,
        criterion_link=criterion_link,
    )
    return {
        "marker": marker,
        "target_generation": target_generation,
        "basis_matches": basis_matches,
        "state": generation["state"],
        "resolution": resolution,
        "attempt": generation["attempt"],
        "observation": observation,
        "cleanup_event": generation["cleanup_event"],
        "reference": reference,
        "criterion_link": criterion_link,
        "storage_token": storage_token,
    }


def read_current_verification_runner_gate_snapshot(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Return one exact graph or the established unreadable-state failure."""

    try:
        return _read_current_verification_runner_gate_snapshot(
            connection,
            project_id=project_id,
            task_id=task_id,
        )
    except StorageError as exc:
        if exc.code in {"database_busy", "project_state_unreadable"}:
            raise
        raise _unreadable_project_state() from exc


def require_current_verification_runner_selection(
    connection: sqlite3.Connection,
    *,
    selection: Any,
) -> dict[str, Any]:
    """Revalidate the DB half of one service-owned selection."""

    from task_governance_tool.verification_runner import (
        VerificationRunnerGateSelection,
    )

    if not isinstance(selection, VerificationRunnerGateSelection):
        raise evidence_ledger_inconsistent()
    snapshot = read_current_verification_runner_gate_snapshot(
        connection,
        project_id=selection.project_id,
        task_id=selection.task_id,
    )
    observation = snapshot["observation"]
    observation_id = (
        observation.verification_runner_observation_id
        if observation is not None
        else None
    )
    if (
        not snapshot["basis_matches"]
        or snapshot["target_generation"] != selection.target_generation
        or snapshot["storage_token"] != selection.storage_token
        or observation_id != selection.verification_runner_observation_id
    ):
        raise verification_runner_state_invalid()
    return snapshot


def read_pending_verification_runner_cleanup(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> tuple[dict[str, Any], ...]:
    """Return only unresolved attempt intents requiring proved cleanup."""

    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    _validated_verification_runner_references(connection)
    rows = connection.execute(
        "SELECT resolution.task_id, resolution.target_generation "
        "FROM verification_runner_resolutions AS resolution "
        "LEFT JOIN verification_runner_observations AS observation "
        "ON observation.project_id = resolution.project_id "
        "AND observation.task_id = resolution.task_id "
        "AND observation.target_generation = resolution.target_generation "
        "LEFT JOIN verification_runner_sandbox_events AS cleanup "
        "ON cleanup.project_id = resolution.project_id "
        "AND cleanup.task_id = resolution.task_id "
        "AND cleanup.target_generation = resolution.target_generation "
        "WHERE resolution.project_id = ? "
        "AND resolution.gate_eligibility_version IN (0, 1) "
        "AND observation.verification_runner_observation_id IS NULL "
        "AND cleanup.verification_runner_sandbox_event_id IS NULL "
        "ORDER BY resolution.task_id, resolution.target_generation",
        (project_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = _read_verification_runner_generation_unvalidated(
            connection,
            project_id=project_id,
            task_id=str(row["task_id"]),
            target_generation=int(row["target_generation"]),
        )
        if item is None or item["state"] != "pending":
            raise evidence_ledger_inconsistent()
        result.append(item)
    return tuple(result)


def has_pending_verification_runner_cleanup(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> bool:
    return bool(
        read_pending_verification_runner_cleanup(
            connection,
            project_id=project_id,
        )
    )


def _verification_runner_current_basis_for_resolution(
    resolution: VerificationRunnerResolution,
    *,
    acceptance_criterion_id: str | None,
) -> dict[str, Any]:
    return {
        "contract_revision": resolution.contract_revision,
        "authority_snapshot_id": resolution.authority_snapshot_id,
        "acceptance_criterion_id": acceptance_criterion_id,
        "verification_criterion_id": resolution.verification_criterion_id,
        "verification_expectation_digest": (
            resolution.verification_expectation_digest
        ),
        "verification_criterion_digest": resolution.verification_criterion_digest,
        "target_kind": resolution.target_kind,
        "target_value": resolution.target_value,
        "target_base_revision": resolution.target_base_revision or "",
        "target_generation": resolution.target_generation,
        "target_capture_version": resolution.target_capture_version,
        "artifact_manifest_id": resolution.artifact_manifest_id,
        "gate_eligibility_version": resolution.gate_eligibility_version,
    }


def _verification_runner_current_basis_matches(
    connection: sqlite3.Connection,
    *,
    resolution: VerificationRunnerResolution,
    acceptance_criterion_id: str | None,
) -> bool:
    """Return false for a valid reset/drift and validate a matching basis fully."""

    task = _read_validated_current_task_row(
        connection,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    expected = _verification_runner_current_basis_for_resolution(
        resolution,
        acceptance_criterion_id=acceptance_criterion_id,
    )
    task_projection = {
        "contract_revision": task["current_contract_revision"],
        "authority_snapshot_id": task[
            "review_target_authority_snapshot_id"
        ],
        "acceptance_criterion_id": task[
            "review_target_acceptance_criterion_id"
        ],
        "verification_criterion_id": task[
            "review_target_verification_criterion_id"
        ],
        "target_kind": task["review_target_kind"],
        "target_value": task["review_target_value"],
        "target_base_revision": task["review_target_base_revision"],
        "target_generation": task["review_target_generation"],
        "target_capture_version": task["review_target_capture_version"],
        "artifact_manifest_id": task["review_target_artifact_manifest_id"],
    }
    if any(task_projection[key] != expected[key] for key in task_projection):
        return False
    return read_current_verification_runner_target_basis(
        connection,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
    ) == expected


def insert_verification_runner_resolution_locked(
    connection: sqlite3.Connection,
    *,
    resolution: VerificationRunnerResolution,
    attempt: VerificationRunnerAttempt,
) -> bool:
    """Atomically append the resolution and launch intent inside caller T1."""

    _require_evidence_writer(connection)
    resolution_values = _verification_runner_value_dict(
        resolution,
        VerificationRunnerResolution,
    )
    attempt_values = _verification_runner_value_dict(
        attempt,
        VerificationRunnerAttempt,
    )
    if (
        resolution.project_id != attempt.project_id
        or resolution.task_id != attempt.task_id
        or resolution.target_generation != attempt.target_generation
        or resolution.verification_runner_resolution_id
        != attempt.verification_runner_resolution_id
        or resolution.gate_eligibility_version not in {0, 1}
        or attempt.gate_eligibility_version
        != resolution.gate_eligibility_version
    ):
        raise evidence_ledger_inconsistent()
    existing = _read_verification_runner_generation_unvalidated(
        connection,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
        target_generation=resolution.target_generation,
    )
    if existing is not None:
        if (
            existing["state"] != "pending"
            or _verification_runner_value_dict(
                existing["resolution"], VerificationRunnerResolution
            )
            != resolution_values
            or _verification_runner_value_dict(
                existing["attempt"], VerificationRunnerAttempt
            )
            != attempt_values
        ):
            raise evidence_ledger_inconsistent()
        _validated_verification_runner_references(connection)
        return False
    pending = connection.execute(
        "SELECT 1 FROM verification_runner_resolutions AS existing "
        "LEFT JOIN verification_runner_observations AS observation "
        "ON observation.project_id = existing.project_id "
        "AND observation.task_id = existing.task_id "
        "AND observation.target_generation = existing.target_generation "
        "LEFT JOIN verification_runner_sandbox_events AS cleanup "
        "ON cleanup.project_id = existing.project_id "
        "AND cleanup.task_id = existing.task_id "
        "AND cleanup.target_generation = existing.target_generation "
        "WHERE existing.project_id = ? "
        "AND observation.verification_runner_observation_id IS NULL "
        "AND cleanup.verification_runner_sandbox_event_id IS NULL LIMIT 1",
        (resolution.project_id,),
    ).fetchone()
    if pending is not None:
        raise evidence_ledger_inconsistent()
    current = read_current_verification_runner_target_basis(
        connection,
        project_id=resolution.project_id,
        task_id=resolution.task_id,
    )
    expected_current = _verification_runner_current_basis_for_resolution(
        resolution,
        acceptance_criterion_id=current["acceptance_criterion_id"],
    )
    if current != expected_current:
        raise evidence_ledger_inconsistent()
    try:
        resolution_fields = tuple(sorted(resolution_values))
        connection.execute(
            "INSERT INTO verification_runner_resolutions("
            + ", ".join(resolution_fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in resolution_fields)
            + ")",
            resolution_values,
        )
        attempt_fields = tuple(sorted(attempt_values))
        connection.execute(
            "INSERT INTO verification_runner_attempts("
            + ", ".join(attempt_fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in attempt_fields)
            + ")",
            attempt_values,
        )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    _validated_verification_runner_references(connection)
    return True


def _exact_runner_terminal_replay(
    connection: sqlite3.Connection,
    *,
    generation: dict[str, Any],
    observation_values: dict[str, Any],
    reference: dict[str, Any],
    criterion_link: PreparedCriterionEvidenceLink,
    cleanup_values: dict[str, Any],
) -> bool:
    stored_observation = generation["observation"]
    stored_cleanup = generation["cleanup_event"]
    if stored_observation is None or stored_cleanup is None:
        return False
    stored_reference = connection.execute(
        "SELECT * FROM evidence_references "
        "WHERE source_kind = 'runner_observation' AND source_id = ?",
        (stored_observation.verification_runner_observation_id,),
    ).fetchone()
    stored_link = connection.execute(
        "SELECT * FROM criterion_evidence_links "
        "WHERE relation = 'runner_observation' AND evidence_reference_id = ?",
        (reference.get("evidence_reference_id"),),
    ).fetchone()
    return (
        _verification_runner_value_dict(
            stored_observation, VerificationRunnerObservation
        )
        == observation_values
        and _verification_runner_value_dict(
            stored_cleanup, VerificationRunnerSandboxEvent
        )
        == cleanup_values
        and stored_reference is not None
        and dict(stored_reference) == reference
        and stored_link is not None
        and dict(stored_link)
        == _verification_runner_value_dict(
            criterion_link,
            PreparedCriterionEvidenceLink,
        )
    )


def persist_verification_runner_terminal_locked(
    connection: sqlite3.Connection,
    *,
    observation: VerificationRunnerObservation,
    evidence_reference: dict[str, Any],
    criterion_link: PreparedCriterionEvidenceLink,
    cleanup_event: VerificationRunnerSandboxEvent,
) -> bool:
    """Atomically append one closed terminal graph and standalone Evidence."""

    _require_evidence_writer(connection)
    observation_values = _verification_runner_value_dict(
        observation,
        VerificationRunnerObservation,
    )
    cleanup_values = _verification_runner_value_dict(
        cleanup_event,
        VerificationRunnerSandboxEvent,
    )
    if type(evidence_reference) is not dict:
        raise evidence_ledger_inconsistent()
    reference = dict(evidence_reference)
    reference_fields = _EVIDENCE_LEDGER_REQUIRED_COLUMNS["evidence_references"]
    if set(reference) != reference_fields:
        raise evidence_ledger_inconsistent()
    _validated_verification_runner_references(connection)
    generation = _read_verification_runner_generation_unvalidated(
        connection,
        project_id=observation.project_id,
        task_id=observation.task_id,
        target_generation=observation.target_generation,
    )
    if generation is None:
        raise evidence_ledger_inconsistent()
    if generation["state"] == "terminal":
        if _exact_runner_terminal_replay(
            connection,
            generation=generation,
            observation_values=observation_values,
            reference=reference,
            criterion_link=criterion_link,
            cleanup_values=cleanup_values,
        ):
            return False
        raise evidence_ledger_inconsistent()
    if generation["state"] != "pending":
        raise evidence_ledger_inconsistent()
    attempt = generation["attempt"]
    resolution = generation["resolution"]
    if (
        cleanup_event.project_id != observation.project_id
        or cleanup_event.task_id != observation.task_id
        or cleanup_event.target_generation != observation.target_generation
        or cleanup_event.verification_runner_attempt_id
        != observation.verification_runner_attempt_id
        or cleanup_event.terminal_observation_id
        != observation.verification_runner_observation_id
        or attempt.verification_runner_attempt_id
        != observation.verification_runner_attempt_id
        or observation.gate_eligibility_version
        != resolution.gate_eligibility_version
    ):
        raise evidence_ledger_inconsistent()
    if not _verification_runner_current_basis_matches(
        connection,
        resolution=resolution,
        acceptance_criterion_id=reference["acceptance_criterion_id"],
    ):
        raise verification_runner_state_invalid()
    observation_fields = tuple(sorted(observation_values))
    cleanup_fields = tuple(sorted(cleanup_values))
    reference_order = tuple(sorted(reference))
    try:
        connection.execute(
            "INSERT INTO verification_runner_observations("
            + ", ".join(observation_fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in observation_fields)
            + ")",
            observation_values,
        )
        connection.execute(
            "INSERT INTO evidence_references("
            + ", ".join(reference_order)
            + ") VALUES ("
            + ", ".join(":" + field for field in reference_order)
            + ")",
            reference,
        )
        persist_criterion_evidence_link_locked(
            connection,
            link=criterion_link,
        )
        connection.execute(
            "INSERT INTO verification_runner_sandbox_events("
            + ", ".join(cleanup_fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in cleanup_fields)
            + ")",
            cleanup_values,
        )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    _validated_verification_runner_references(connection)
    return True


def persist_verification_runner_restart_cleanup_locked(
    connection: sqlite3.Connection,
    *,
    cleanup_event: VerificationRunnerSandboxEvent,
) -> bool:
    """Append the one cleanup-only event; this state remains fail closed."""

    _require_evidence_writer(connection)
    cleanup_values = _verification_runner_value_dict(
        cleanup_event,
        VerificationRunnerSandboxEvent,
    )
    _validated_verification_runner_references(connection)
    generation = _read_verification_runner_generation_unvalidated(
        connection,
        project_id=cleanup_event.project_id,
        task_id=cleanup_event.task_id,
        target_generation=cleanup_event.target_generation,
    )
    if generation is None:
        raise evidence_ledger_inconsistent()
    if (
        generation["resolution"].gate_eligibility_version not in {0, 1}
        or generation["attempt"] is None
        or generation["attempt"].gate_eligibility_version
        != generation["resolution"].gate_eligibility_version
    ):
        raise evidence_ledger_inconsistent()
    if generation["state"] == "restart_cleaned":
        if (
            _verification_runner_value_dict(
                generation["cleanup_event"], VerificationRunnerSandboxEvent
            )
            == cleanup_values
        ):
            return False
        raise evidence_ledger_inconsistent()
    if (
        generation["state"] != "pending"
        or cleanup_event.terminal_observation_id is not None
        or cleanup_event.verification_runner_attempt_id
        != generation["attempt"].verification_runner_attempt_id
    ):
        raise evidence_ledger_inconsistent()
    fields = tuple(sorted(cleanup_values))
    try:
        connection.execute(
            "INSERT INTO verification_runner_sandbox_events("
            + ", ".join(fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in fields)
            + ")",
            cleanup_values,
        )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    _validated_verification_runner_references(connection)
    return True


def _validate_prepared_completion_bundle(
    bundle: PreparedCompletionEvidenceBundle,
    *,
    expected_cycle: CompletionCycle,
) -> None:
    if (
        not isinstance(bundle, PreparedCompletionEvidenceBundle)
        or COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(
            bundle.completion_evidence_bundle_id
        )
        is None
        or bundle.completion_evidence_bundle_id
        != expected_cycle.completion_evidence_bundle_id
        or bundle.project_id != expected_cycle.project_id
        or bundle.task_id != expected_cycle.task_id
        or bundle.completion_cycle_id != expected_cycle.completion_cycle_id
        or bundle.cycle_ordinal != expected_cycle.saved_cycle_ordinal
        or not _completion_bundle_version_basis_valid(
            source_schema_version=bundle.source_schema_version,
            bundle_version=bundle.bundle_version,
            verification_basis_kind=bundle.verification_basis_kind,
            verification_runner_observation_id=(
                bundle.verification_runner_observation_id
            ),
            verification_receipt_id=bundle.verification_receipt_id,
            cycle=expected_cycle,
        )
        or bundle.contract_revision != expected_cycle.contract_revision
        or type(bundle.contract_revision) is not int
        or bundle.contract_revision < 0
        or type(bundle.authority_snapshot_id) is not str
        or not bundle.authority_snapshot_id
        or (
            bundle.acceptance_criterion_id is not None
            and type(bundle.acceptance_criterion_id) is not str
        )
        or (
            bundle.verification_criterion_id is not None
            and type(bundle.verification_criterion_id) is not str
        )
        or (
            bundle.target_kind,
            bundle.target_value,
            bundle.target_base_revision,
            bundle.target_generation,
        )
        != (
            expected_cycle.review_target_kind,
            expected_cycle.review_target_value,
            expected_cycle.review_target_base_revision,
            expected_cycle.review_target_generation,
        )
        or bundle.target_capture_version != 1
        or type(bundle.target_capture_version) is not int
        or type(bundle.artifact_manifest_id) is not str
        or ARTIFACT_MANIFEST_ID_PATTERN.fullmatch(bundle.artifact_manifest_id)
        is None
        or bundle.verification_receipt_id
        != expected_cycle.verification_receipt_id
        or type(bundle.omission_mask) is not int
        or not 0 <= bundle.omission_mask <= 15
        or type(bundle.sealed_at) is not str
        or bundle.sealed_at != expected_cycle.recorded_at
        or type(bundle.bundle_digest) is not str
        or SHA256_DIGEST_PATTERN.fullmatch(bundle.bundle_digest) is None
        or type(bundle.payload_size_bytes) is not int
        or not 1
        <= bundle.payload_size_bytes
        <= COMPLETION_EVIDENCE_BUNDLE_MAX_BYTES
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            bundle.sealed_at,
            field="completion Evidence Bundle seal time",
        )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    link_ids: set[str] = set()
    for link in bundle.criterion_links:
        if (
            not isinstance(link, PreparedCriterionEvidenceLink)
            or link.project_id != bundle.project_id
            or link.task_id != bundle.task_id
            or link.criterion_evidence_link_id in link_ids
        ):
            raise evidence_ledger_inconsistent()
        link_ids.add(link.criterion_evidence_link_id)
    member_keys: set[tuple[str, int]] = set()
    member_link_ids: set[str] = set()
    member_reference_ids: set[str] = set()
    next_ordinals = {kind: 0 for kind in COMPLETION_BUNDLE_MEMBER_KINDS}
    for member in bundle.members:
        if (
            not isinstance(member, PreparedCompletionBundleMember)
            or member.project_id != bundle.project_id
            or member.task_id != bundle.task_id
            or member.completion_evidence_bundle_id
            != bundle.completion_evidence_bundle_id
            or member.member_kind not in COMPLETION_BUNDLE_MEMBER_KINDS
            or type(member.ordinal) is not int
            or member.ordinal != next_ordinals[member.member_kind]
            or (member.member_kind, member.ordinal) in member_keys
        ):
            raise evidence_ledger_inconsistent()
        next_ordinals[member.member_kind] += 1
        member_keys.add((member.member_kind, member.ordinal))
        if member.member_kind == "criterion_link":
            if (
                type(member.criterion_evidence_link_id) is not str
                or member.criterion_evidence_link_id not in link_ids
                or member.evidence_reference_id is not None
                or member.criterion_evidence_link_id in member_link_ids
            ):
                raise evidence_ledger_inconsistent()
            member_link_ids.add(member.criterion_evidence_link_id)
        elif (
            member.criterion_evidence_link_id is not None
            or type(member.evidence_reference_id) is not str
            or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(
                member.evidence_reference_id
            )
            is None
            or member.evidence_reference_id in member_reference_ids
        ):
            raise evidence_ledger_inconsistent()
        else:
            member_reference_ids.add(member.evidence_reference_id)
    if member_link_ids != link_ids or any(
        link.evidence_reference_id not in member_reference_ids
        for link in bundle.criterion_links
    ):
        raise evidence_ledger_inconsistent()
    finding_ids: set[str] = set()
    for ordinal, finding in enumerate(bundle.finding_snapshots):
        if (
            not isinstance(finding, PreparedCompletionFindingSnapshot)
            or finding.project_id != bundle.project_id
            or finding.task_id != bundle.task_id
            or finding.completion_evidence_bundle_id
            != bundle.completion_evidence_bundle_id
            or type(finding.ordinal) is not int
            or finding.ordinal != ordinal
            or type(finding.review_finding_id) is not str
            or REVIEW_FINDING_ID_PATTERN.fullmatch(finding.review_finding_id)
            is None
            or type(finding.review_receipt_id) is not str
            or REVIEW_RECEIPT_ID_PATTERN.fullmatch(finding.review_receipt_id)
            is None
            or finding.review_finding_id in finding_ids
            or type(finding.target_generation) is not int
            or finding.target_generation <= 0
            or finding.severity not in {"high", "medium", "low"}
            or type(finding.summary) is not str
            or not 1 <= len(finding.summary) <= 1_000
            or finding.status not in {"open", "resolved"}
            or type(finding.resolution_summary) is not str
            or len(finding.resolution_summary) > 1_000
            or type(finding.created_at) is not str
            or (
                finding.status == "open"
                and (
                    finding.resolution_summary != ""
                    or finding.resolved_at is not None
                )
            )
            or (
                finding.status == "resolved"
                and (
                    not finding.resolution_summary
                    or type(finding.resolved_at) is not str
                )
            )
            or type(finding.producer_version) is not int
            or finding.producer_version != 1
            or type(finding.digest) is not str
            or SHA256_DIGEST_PATTERN.fullmatch(finding.digest) is None
            or (
                finding.evidence_reference_id is None
                and (
                    finding.assurance_class != "legacy_unknown"
                    or finding.producer_class != "legacy_migration"
                )
            )
            or (
                finding.evidence_reference_id is not None
                and (
                    type(finding.evidence_reference_id) is not str
                    or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(
                        finding.evidence_reference_id
                    )
                    is None
                    or finding.evidence_reference_id not in member_reference_ids
                    or finding.assurance_class != "bound_attestation"
                    or finding.producer_class != "trusted_caller"
                )
            )
        ):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                finding.created_at,
                field="completion Finding snapshot creation time",
            )
            if finding.resolved_at is not None:
                validate_utc_timestamp(
                    finding.resolved_at,
                    field="completion Finding snapshot resolution time",
                )
        except StorageError as exc:
            raise evidence_ledger_inconsistent() from exc
        finding_ids.add(finding.review_finding_id)


def _validate_projection_bundle_record(
    record: ProjectionBundleRecord,
) -> None:
    from task_governance_tool.evidence_projection import (
        EvidenceProjectionError,
        build_projection_bundle_artifact,
    )

    try:
        build_projection_bundle_artifact(record)
    except EvidenceProjectionError as exc:
        raise evidence_ledger_inconsistent() from exc


def _prepared_projection_bundle_record_locked(
    connection: sqlite3.Connection,
    *,
    bundle: PreparedCompletionEvidenceBundle,
    expected_cycle: CompletionCycle,
) -> ProjectionBundleRecord:
    completion_reference = _completion_basis_reference(
        connection,
        project_id=bundle.project_id,
        task_id=bundle.task_id,
        source_kind="completion_evidence",
        source_id=expected_cycle.completion_cycle_id,
    )
    if completion_reference is None:
        raise evidence_ledger_inconsistent()
    basis = read_native_completion_bundle_basis_locked(
        connection,
        project_id=bundle.project_id,
        task_id=bundle.task_id,
        cycle=expected_cycle,
        completion_reference=completion_reference,
    )
    references = (
        dict(basis.artifact_reference),
        *(
            (dict(basis.verification_reference),)
            if basis.verification_reference is not None
            else ()
        ),
        *(
            (dict(basis.runner_reference),)
            if basis.runner_reference is not None
            else ()
        ),
        *(dict(value) for value in basis.review_references),
        *(
            dict(value)
            for value in basis.finding_references
            if value is not None
        ),
        dict(basis.completion_reference),
    )
    return ProjectionBundleRecord(
        bundle=bundle,
        cycle=expected_cycle,
        task=dict(basis.task),
        authority_snapshot=dict(basis.authority_snapshot),
        criteria=tuple(dict(value) for value in basis.criteria),
        artifact_manifest=dict(basis.artifact_manifest),
        artifact_entries=tuple(
            dict(value) for value in basis.artifact_entries
        ),
        evidence_references=references,
        verification_receipt=(
            dict(basis.verification_receipt)
            if basis.verification_receipt is not None
            else None
        ),
        runner_observation=(
            dict(basis.runner_observation)
            if basis.runner_observation is not None
            else None
        ),
        review_receipts=tuple(
            {
                "receipt": dict(value["receipt"]),
                "provenance": value["provenance"],
            }
            for value in basis.review_receipts
        ),
        finding_snapshots=bundle.finding_snapshots,
    )


def persist_completion_evidence_bundle_locked(
    connection: sqlite3.Connection,
    *,
    bundle: PreparedCompletionEvidenceBundle,
    expected_cycle: CompletionCycle,
) -> PreparedCompletionEvidenceBundle:
    """Persist caller-prepared relational Bundle rows before its deferred cycle."""

    _require_evidence_writer(connection)
    if bundle.source_schema_version != current_schema_version(connection):
        raise evidence_ledger_inconsistent()
    _validate_prepared_completion_bundle(bundle, expected_cycle=expected_cycle)
    try:
        for link in bundle.criterion_links:
            persist_criterion_evidence_link_locked(connection, link=link)
        available_bundle_fields = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(completion_evidence_bundles)"
            ).fetchall()
        }
        bundle_fields = {
            field
            for field in _EVIDENCE_LEDGER_REQUIRED_COLUMNS[
                "completion_evidence_bundles"
            ]
            if field in available_bundle_fields
        }
        bundle_values = _prepared_row(bundle, bundle_fields)
        ordered_bundle_fields = tuple(sorted(bundle_fields))
        connection.execute(
            f"INSERT INTO completion_evidence_bundles("
            f"{', '.join(ordered_bundle_fields)}) VALUES ("
            f"{', '.join(':' + name for name in ordered_bundle_fields)})",
            bundle_values,
        )
        member_fields = _EVIDENCE_LEDGER_REQUIRED_COLUMNS[
            "completion_bundle_members"
        ]
        ordered_member_fields = tuple(sorted(member_fields))
        for member in bundle.members:
            connection.execute(
                f"INSERT INTO completion_bundle_members("
                f"{', '.join(ordered_member_fields)}) VALUES ("
                f"{', '.join(':' + name for name in ordered_member_fields)})",
                _prepared_row(member, member_fields),
            )
        finding_fields = _EVIDENCE_LEDGER_REQUIRED_COLUMNS[
            "completion_bundle_finding_snapshots"
        ]
        ordered_finding_fields = tuple(sorted(finding_fields))
        for finding in bundle.finding_snapshots:
            connection.execute(
                f"INSERT INTO completion_bundle_finding_snapshots("
                f"{', '.join(ordered_finding_fields)}) VALUES ("
                f"{', '.join(':' + name for name in ordered_finding_fields)})",
                _prepared_row(finding, finding_fields),
            )
    except sqlite3.IntegrityError as exc:
        raise evidence_ledger_inconsistent() from exc
    stored = read_completion_evidence_bundle(
        connection,
        completion_evidence_bundle_id=bundle.completion_evidence_bundle_id,
    )
    if stored != bundle:
        raise evidence_ledger_inconsistent()
    return stored


def read_completion_evidence_bundle(
    connection: sqlite3.Connection,
    *,
    completion_evidence_bundle_id: str,
) -> PreparedCompletionEvidenceBundle:
    if (
        type(completion_evidence_bundle_id) is not str
        or COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(
            completion_evidence_bundle_id
        )
        is None
    ):
        raise evidence_ledger_inconsistent()
    row = connection.execute(
        "SELECT * FROM completion_evidence_bundles "
        "WHERE completion_evidence_bundle_id = ?",
        (completion_evidence_bundle_id,),
    ).fetchone()
    if row is None:
        raise evidence_ledger_inconsistent()
    member_rows = connection.execute(
        "SELECT * FROM completion_bundle_members "
        "WHERE completion_evidence_bundle_id = ? "
        "ORDER BY member_kind, ordinal",
        (completion_evidence_bundle_id,),
    ).fetchall()
    link_rows = connection.execute(
        """
        SELECT link.*
          FROM completion_bundle_members AS member
          JOIN criterion_evidence_links AS link
            ON link.criterion_evidence_link_id =
               member.criterion_evidence_link_id
         WHERE member.completion_evidence_bundle_id = ?
           AND member.member_kind = 'criterion_link'
         ORDER BY member.ordinal
        """,
        (completion_evidence_bundle_id,),
    ).fetchall()
    finding_rows = connection.execute(
        "SELECT * FROM completion_bundle_finding_snapshots "
        "WHERE completion_evidence_bundle_id = ? ORDER BY ordinal",
        (completion_evidence_bundle_id,),
    ).fetchall()
    return PreparedCompletionEvidenceBundle(
        completion_evidence_bundle_id=str(row["completion_evidence_bundle_id"]),
        project_id=str(row["project_id"]),
        task_id=str(row["task_id"]),
        completion_cycle_id=str(row["completion_cycle_id"]),
        cycle_ordinal=row["cycle_ordinal"],
        source_schema_version=row["source_schema_version"],
        bundle_version=row["bundle_version"],
        contract_revision=row["contract_revision"],
        authority_snapshot_id=str(row["authority_snapshot_id"]),
        acceptance_criterion_id=row["acceptance_criterion_id"],
        verification_criterion_id=row["verification_criterion_id"],
        target_kind=str(row["target_kind"]),
        target_value=str(row["target_value"]),
        target_base_revision=str(row["target_base_revision"]),
        target_generation=row["target_generation"],
        target_capture_version=row["target_capture_version"],
        artifact_manifest_id=str(row["artifact_manifest_id"]),
        verification_receipt_id=row["verification_receipt_id"],
        verification_basis_kind=(
            row["verification_basis_kind"]
            if "verification_basis_kind" in row.keys()
            else None
        ),
        verification_runner_observation_id=(
            row["verification_runner_observation_id"]
            if "verification_runner_observation_id" in row.keys()
            else None
        ),
        omission_mask=row["omission_mask"],
        sealed_at=str(row["sealed_at"]),
        bundle_digest=str(row["bundle_digest"]),
        payload_size_bytes=row["payload_size_bytes"],
        criterion_links=tuple(
            PreparedCriterionEvidenceLink(**dict(link_row))
            for link_row in link_rows
        ),
        members=tuple(
            PreparedCompletionBundleMember(**dict(member_row))
            for member_row in member_rows
        ),
        finding_snapshots=tuple(
            PreparedCompletionFindingSnapshot(**dict(finding_row))
            for finding_row in finding_rows
        ),
    )


def read_evidence_projection_state(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionState:
    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    row = connection.execute(
        "SELECT * FROM evidence_projection_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise evidence_ledger_inconsistent()
    return _projection_state_from_row(row)


def ensure_evidence_projection_state_row(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionState:
    """Seed the one project projection row after project identity exists."""

    if current_schema_version(connection) != SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            f"Evidence projection state requires schema version {SCHEMA_VERSION}",
        )
    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    project_count = connection.execute(
        "SELECT COUNT(*) FROM project_meta WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]
    source_generation = connection.execute(
        "SELECT COUNT(*) FROM task_completion_cycles WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]
    if type(project_count) is not int or project_count != 1:
        raise evidence_ledger_inconsistent()
    if (
        type(source_generation) is not int
        or source_generation < 0
        or source_generation > SQLITE_INT64_MAX
    ):
        raise evidence_ledger_inconsistent()
    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO evidence_projection_state(
              project_id, source_generation, published_generation,
              index_digest, last_success_at, last_outcome_code,
              last_outcome_at
            ) VALUES (?, ?, NULL, NULL, NULL, NULL, NULL)
            """,
            (project_id, source_generation),
        )
    except sqlite3.IntegrityError as exc:
        raise evidence_ledger_inconsistent() from exc
    state = read_evidence_projection_state(
        connection,
        project_id=project_id,
    )
    if state.source_generation != source_generation:
        raise evidence_ledger_inconsistent()
    return state


def advance_evidence_source_generation_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionState:
    """Advance the DB-authoritative projection generation exactly once."""

    _require_evidence_writer(connection)
    current = read_evidence_projection_state(
        connection,
        project_id=project_id,
    )
    if current.source_generation == SQLITE_INT64_MAX:
        raise evidence_ledger_inconsistent()
    cursor = connection.execute(
        """
        UPDATE evidence_projection_state
           SET source_generation = source_generation + 1
         WHERE project_id = ? AND source_generation = ?
        """,
        (project_id, current.source_generation),
    )
    if cursor.rowcount != 1:
        raise evidence_ledger_inconsistent()
    return read_evidence_projection_state(
        connection,
        project_id=project_id,
    )


def _advance_evidence_source_generation_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionState:
    return advance_evidence_source_generation_locked(
        connection,
        project_id=project_id,
    )


def record_evidence_projection_outcome_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    captured_generation: int,
    outcome_code: str,
    recorded_at: str,
    index_digest: str | None = None,
) -> EvidenceProjectionState:
    """Conditionally record one bounded projector outcome without file data."""

    _require_evidence_writer(connection)
    current = read_evidence_projection_state(
        connection,
        project_id=project_id,
    )
    if (
        type(captured_generation) is not int
        or not 0 <= captured_generation <= current.source_generation
        or type(outcome_code) is not str
        or outcome_code not in EVIDENCE_PROJECTION_OUTCOMES
        or type(recorded_at) is not str
        or (
            outcome_code == "succeeded"
            and (
                type(index_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(index_digest) is None
                or (
                    current.published_generation is not None
                    and captured_generation < current.published_generation
                )
            )
        )
        or (outcome_code != "succeeded" and index_digest is not None)
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            recorded_at,
            field="Evidence projection outcome time",
        )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    if outcome_code == "succeeded":
        cursor = connection.execute(
            """
            UPDATE evidence_projection_state
               SET published_generation = ?, index_digest = ?,
                   last_success_at = ?, last_outcome_code = ?,
                   last_outcome_at = ?
             WHERE project_id = ? AND source_generation >= ?
            """,
            (
                captured_generation,
                index_digest,
                recorded_at,
                outcome_code,
                recorded_at,
                project_id,
                captured_generation,
            ),
        )
    else:
        cursor = connection.execute(
            """
            UPDATE evidence_projection_state
               SET last_outcome_code = ?, last_outcome_at = ?
             WHERE project_id = ? AND source_generation >= ?
            """,
            (outcome_code, recorded_at, project_id, captured_generation),
        )
    if cursor.rowcount != 1:
        raise evidence_ledger_inconsistent()
    return read_evidence_projection_state(
        connection,
        project_id=project_id,
    )


def _completion_basis_reference(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    source_kind: str,
    source_id: str,
    required: bool = True,
) -> dict[str, Any] | None:
    rows = connection.execute(
        """
        SELECT * FROM evidence_references
         WHERE project_id = ? AND task_id = ?
           AND source_kind = ? AND source_id = ?
         ORDER BY evidence_reference_id
         LIMIT 2
        """,
        (project_id, task_id, source_kind, source_id),
    ).fetchall()
    if len(rows) > 1 or (required and not rows):
        raise evidence_ledger_inconsistent()
    return dict(rows[0]) if rows else None


def read_native_completion_bundle_basis_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    cycle: CompletionCycle,
    completion_reference: dict[str, Any],
) -> NativeCompletionBundleBasis:
    """Read one validated, bounded seal-time basis under the Task writer.

    Review Receipt elements are ``{"receipt": ..., "provenance": ...}`` in
    ``cycle.gate_basis.qualifying_receipt_ids`` order. Findings are ordered by
    numeric target generation, creation time, then Finding ID; the positional
    ``finding_references`` tuple contains the matching Reference or ``None``.
    """

    _require_evidence_writer(connection)
    _require_completion_capture_activation_locked(connection)
    _validate_completion_cycle(cycle)
    if (
        cycle.project_id != project_id
        or cycle.task_id != task_id
        or cycle.origin != "native_done"
        or cycle.evidence_basis_version != 1
        or cycle.completion_evidence_bundle_id is None
        or not isinstance(completion_reference, dict)
        or set(completion_reference)
        != _EVIDENCE_LEDGER_REQUIRED_COLUMNS["evidence_references"]
    ):
        raise evidence_ledger_inconsistent()

    locked = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if locked is None:
        raise evidence_ledger_inconsistent()
    task = dict(locked)
    if (
        task.get("status") == "done"
        or task.get("current_contract_revision") != cycle.contract_revision
        or task.get("review_tier") != cycle.review_tier
        or (
            task.get("review_target_kind"),
            task.get("review_target_value"),
            task.get("review_target_base_revision"),
            task.get("review_target_generation"),
        )
        != (
            cycle.review_target_kind,
            cycle.review_target_value,
            cycle.review_target_base_revision,
            cycle.review_target_generation,
        )
        or task.get("review_target_capture_version") != 1
        or type(task.get("review_target_authority_snapshot_id")) is not str
        or type(task.get("review_target_artifact_manifest_id")) is not str
    ):
        raise evidence_ledger_inconsistent()

    authority = _validated_authority_context(connection)
    snapshot_id = str(task["review_target_authority_snapshot_id"])
    snapshot = authority.snapshots.get(snapshot_id)
    criterion_bindings = authority.links.get(snapshot_id, {})
    if (
        snapshot is None
        or snapshot["project_id"] != project_id
        or snapshot["task_id"] != task_id
        or snapshot["contract_revision"] != cycle.contract_revision
        or snapshot["task_title"] != task.get("title")
        or snapshot["task_description"] != task.get("description")
        or snapshot["review_tier"] != task.get("review_tier")
        or snapshot["verification"] != task.get("verification")
        or criterion_bindings.get("acceptance")
        != task.get("review_target_acceptance_criterion_id")
        or criterion_bindings.get("verification")
        != task.get("review_target_verification_criterion_id")
    ):
        raise evidence_ledger_inconsistent()
    criteria: list[dict[str, Any]] = []
    for kind in ("acceptance", "verification"):
        criterion_id = criterion_bindings.get(kind)
        if criterion_id is None:
            continue
        criterion = authority.criteria.get(criterion_id)
        if (
            criterion is None
            or criterion["project_id"] != project_id
            or criterion["task_id"] != task_id
            or criterion["criterion_kind"] != kind
        ):
            raise evidence_ledger_inconsistent()
        criteria.append(dict(criterion))

    manifest_id = str(task["review_target_artifact_manifest_id"])
    manifests, _by_target = _validate_artifact_manifest_storage(
        connection,
        snapshots=authority.snapshots,
        links=authority.links,
        manifest_ids={manifest_id},
    )
    manifest_record = manifests.get(manifest_id)
    if manifest_record is None:
        raise evidence_ledger_inconsistent()
    manifest = dict(manifest_record.row)
    artifact_entries = tuple(
        dict(row)
        for row in connection.execute(
            "SELECT * FROM artifact_manifest_entries "
            "WHERE artifact_manifest_id = ? ORDER BY ordinal",
            (manifest_id,),
        ).fetchall()
    )
    if len(artifact_entries) != manifest["entry_count"]:
        raise evidence_ledger_inconsistent()
    artifact_reference = _completion_basis_reference(
        connection,
        project_id=project_id,
        task_id=task_id,
        source_kind="artifact_manifest",
        source_id=manifest_id,
    )
    if artifact_reference is None:
        raise evidence_ledger_inconsistent()

    verification_receipt: dict[str, Any] | None = None
    verification_reference: dict[str, Any] | None = None
    runner_observation: dict[str, Any] | None = None
    runner_reference: dict[str, Any] | None = None
    runner_criterion_link: dict[str, Any] | None = None
    if cycle.verification_basis_kind == "runner_observation":
        from task_governance_tool.verification_runner import (
            runner_observation_source_projection,
        )

        observation_id = cycle.verification_runner_observation_id
        runner_generation_key = (
            project_id,
            task_id,
            cycle.review_target_generation,
        )
        _runner_references, runner_generations = (
            _validated_verification_runner_graph(
                connection,
                selected_generation=runner_generation_key,
                selected_task=locked,
            )
        )
        runner_generation = runner_generations.get(runner_generation_key)
        resolution = (
            runner_generation["resolution"]
            if runner_generation is not None
            else None
        )
        observation = (
            runner_generation["observation"]
            if runner_generation is not None
            else None
        )
        if (
            type(observation_id) is not str
            or runner_generation is None
            or runner_generation["state"] != "terminal"
            or resolution is None
            or observation is None
            or resolution.gate_eligibility_version != 1
            or observation.gate_eligibility_version != 1
            or observation.verification_runner_observation_id
            != observation_id
            or resolution.contract_revision != cycle.contract_revision
            or resolution.authority_snapshot_id != snapshot_id
            or resolution.verification_criterion_id
            != criterion_bindings.get("verification")
            or resolution.artifact_manifest_id != manifest_id
            or (
                resolution.target_kind,
                resolution.target_value,
                resolution.target_base_revision or "",
                resolution.target_generation,
            )
            != (
                cycle.review_target_kind,
                cycle.review_target_value,
                cycle.review_target_base_revision,
                cycle.review_target_generation,
            )
        ):
            raise evidence_ledger_inconsistent()
        resolution_row = connection.execute(
            "SELECT * FROM verification_runner_resolutions "
            "WHERE verification_runner_resolution_id = ?",
            (resolution.verification_runner_resolution_id,),
        ).fetchone()
        observation_row = connection.execute(
            "SELECT * FROM verification_runner_observations "
            "WHERE verification_runner_observation_id = ?",
            (observation_id,),
        ).fetchone()
        if resolution_row is None or observation_row is None:
            raise evidence_ledger_inconsistent()
        runner_observation = runner_observation_source_projection(
            observation=dict(observation_row),
            resolution=dict(resolution_row),
        )
        runner_reference = _completion_basis_reference(
            connection,
            project_id=project_id,
            task_id=task_id,
            source_kind="runner_observation",
            source_id=observation_id,
        )
        if runner_reference is None:
            raise evidence_ledger_inconsistent()
        link_rows = connection.execute(
            "SELECT * FROM criterion_evidence_links "
            "WHERE project_id = ? AND task_id = ? "
            "AND criterion_id = ? AND evidence_reference_id = ? "
            "AND relation = 'runner_observation' "
            "ORDER BY criterion_evidence_link_id LIMIT 2",
            (
                project_id,
                task_id,
                resolution.verification_criterion_id,
                runner_reference["evidence_reference_id"],
            ),
        ).fetchall()
        if len(link_rows) != 1:
            raise evidence_ledger_inconsistent()
        runner_criterion_link = dict(link_rows[0])
    elif cycle.verification_receipt_id is not None:
        stored_verification = connection.execute(
            "SELECT * FROM verification_receipts "
            "WHERE verification_receipt_id = ?",
            (cycle.verification_receipt_id,),
        ).fetchone()
        if stored_verification is None:
            raise evidence_ledger_inconsistent()
        validated_verification = _validate_verification_receipt_row(
            dict(stored_verification)
        )
        if (
            validated_verification["project_id"] != project_id
            or validated_verification["task_id"] != task_id
            or validated_verification["verification_subject_basis_version"]
            != 1
            or validated_verification["subject_authority_snapshot_id"]
            != snapshot_id
            or validated_verification["subject_verification_criterion_id"]
            != criterion_bindings.get("verification")
        ):
            raise evidence_ledger_inconsistent()
        verification_receipt = {
            "verification_receipt_id": cycle.verification_receipt_id,
            "verification_subject": {
                "basis_version": 1,
                "kind": "task_verification_criterion",
                "authority_snapshot_id": snapshot_id,
                "verification_criterion_id": criterion_bindings.get(
                    "verification"
                ),
            },
            "result": validated_verification["result"],
            "duration_ms": validated_verification["duration_ms"],
            "scope_coverage": validated_verification["scope_coverage"],
            "created_at": validated_verification["created_at"],
        }
        verification_reference = _completion_basis_reference(
            connection,
            project_id=project_id,
            task_id=task_id,
            source_kind="verification_receipt",
            source_id=cycle.verification_receipt_id,
        )
        if verification_reference is None:
            raise evidence_ledger_inconsistent()
    elif criterion_bindings.get("verification") is not None:
        raise evidence_ledger_inconsistent()

    review_receipts: list[dict[str, Any]] = []
    review_references: list[dict[str, Any]] = []
    for receipt_id in cycle.gate_basis.qualifying_receipt_ids:
        value = read_review_receipt_with_provenance(
            connection,
            review_receipt_id=receipt_id,
        )
        reference = _completion_basis_reference(
            connection,
            project_id=project_id,
            task_id=task_id,
            source_kind="review_receipt",
            source_id=receipt_id,
        )
        if value is None or reference is None:
            raise evidence_ledger_inconsistent()
        receipt = value["receipt"]
        provenance = value["provenance"]
        if (
            receipt["project_id"] != project_id
            or receipt["task_id"] != task_id
            or (
                receipt["target_kind"], receipt["target_value"],
                receipt["target_base_revision"],
                receipt["target_generation"],
            )
            != (
                cycle.review_target_kind, cycle.review_target_value,
                cycle.review_target_base_revision,
                cycle.review_target_generation,
            )
            or (
                receipt["receipt_kind"] != "not_required"
                and provenance is None
            )
            or (
                receipt["receipt_kind"] == "not_required"
                and provenance is not None
            )
        ):
            raise evidence_ledger_inconsistent()
        review_receipts.append(value)
        review_references.append(reference)

    finding_rows = connection.execute(
        """
        SELECT finding.*, receipt.target_generation AS target_generation
          FROM review_findings AS finding
          JOIN review_receipts AS receipt
            ON receipt.review_receipt_id = finding.review_receipt_id
         WHERE receipt.project_id = ? AND receipt.task_id = ?
           AND (
             receipt.target_generation = ?
             OR (
               receipt.target_generation < ?
               AND finding.severity IN ('high', 'medium')
             )
           )
         ORDER BY receipt.target_generation,
                  finding.created_at COLLATE BINARY,
                  finding.review_finding_id COLLATE BINARY
        """,
        (
            project_id,
            task_id,
            cycle.review_target_generation,
            cycle.review_target_generation,
        ),
    ).fetchall()
    findings: list[dict[str, Any]] = []
    finding_references: list[dict[str, Any] | None] = []
    for stored_finding in finding_rows:
        finding = dict(stored_finding)
        _validate_review_finding_base_row(finding)
        reference = _completion_basis_reference(
            connection,
            project_id=project_id,
            task_id=task_id,
            source_kind="review_finding",
            source_id=str(finding["review_finding_id"]),
            required=False,
        )
        if (
            finding["target_generation"] == cycle.review_target_generation
            and reference is None
        ):
            raise evidence_ledger_inconsistent()
        findings.append(finding)
        finding_references.append(reference)

    completion_dispatch = {
        "git_commit": ("machine_observed", "taskgov_git"),
        "external_revision": ("external_reference", "external_system"),
        "commit_not_required": ("bound_attestation", "trusted_caller"),
    }
    expected_attribution = completion_dispatch.get(
        cycle.completion_evidence_kind
    )
    stored_completion_reference = _completion_basis_reference(
        connection,
        project_id=project_id,
        task_id=task_id,
        source_kind="completion_evidence",
        source_id=cycle.completion_cycle_id,
        required=False,
    )
    if stored_completion_reference is not None:
        if stored_completion_reference != completion_reference:
            raise evidence_ledger_inconsistent()
    if (
        expected_attribution is None
        or completion_reference["project_id"] != project_id
        or completion_reference["task_id"] != task_id
        or completion_reference["source_kind"] != "completion_evidence"
        or completion_reference["source_state"]
        != cycle.completion_evidence_kind
        or completion_reference["source_id"] != cycle.completion_cycle_id
        or completion_reference["completion_cycle_id"]
        != cycle.completion_cycle_id
        or completion_reference["contract_revision"]
        != cycle.contract_revision
        or completion_reference["authority_snapshot_id"] != snapshot_id
        or completion_reference["acceptance_criterion_id"]
        != criterion_bindings.get("acceptance")
        or completion_reference["verification_criterion_id"]
        != criterion_bindings.get("verification")
        or (
            completion_reference["target_kind"],
            completion_reference["target_value"],
            completion_reference["target_base_revision"],
            completion_reference["target_generation"],
        )
        != (
            cycle.review_target_kind,
            cycle.review_target_value,
            cycle.review_target_base_revision,
            cycle.review_target_generation,
        )
        or (
            completion_reference["assurance_class"],
            completion_reference["producer_class"],
        )
        != expected_attribution
        or completion_reference["producer_version"] != 1
        or type(completion_reference["digest"]) is not str
        or SHA256_DIGEST_PATTERN.fullmatch(completion_reference["digest"])
        is None
    ):
        raise evidence_ledger_inconsistent()

    return NativeCompletionBundleBasis(
        task={
            "task_id": task_id,
            "title": snapshot["task_title"],
            "description": snapshot["task_description"],
            "review_tier": snapshot["review_tier"],
            "verification": snapshot["verification"],
        },
        authority_snapshot=dict(snapshot),
        criteria=tuple(criteria),
        artifact_manifest=manifest,
        artifact_entries=artifact_entries,
        artifact_reference=artifact_reference,
        verification_receipt=verification_receipt,
        verification_reference=verification_reference,
        runner_observation=runner_observation,
        runner_reference=runner_reference,
        runner_criterion_link=runner_criterion_link,
        review_receipts=tuple(review_receipts),
        review_references=tuple(review_references),
        findings=tuple(findings),
        finding_references=tuple(finding_references),
        completion_reference=dict(completion_reference),
    )


def _fetch_bounded_projection_rows(
    cursor: sqlite3.Cursor,
    *,
    maximum: int | None = None,
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    while True:
        batch = cursor.fetchmany(EVIDENCE_PROJECTION_BATCH_SIZE)
        if not batch:
            break
        rows.extend(batch)
        if maximum is not None and len(rows) > maximum:
            raise evidence_ledger_inconsistent()
    return rows


def _projection_bundle_record_from_validated_rows(
    *,
    bundle: PreparedCompletionEvidenceBundle,
    cycle: CompletionCycle,
    snapshot: sqlite3.Row | dict[str, Any],
    criteria: tuple[dict[str, Any], ...],
    manifest: sqlite3.Row | dict[str, Any],
    entries: tuple[dict[str, Any], ...],
    references: tuple[dict[str, Any], ...],
    verification_receipt: dict[str, Any] | None,
    review_receipts_by_id: dict[str, dict[str, Any]],
    runner_generation: dict[str, Any] | None,
) -> ProjectionBundleRecord:
    """Assemble one projection record from an already validated Bundle graph."""

    verification_projection: dict[str, Any] | None = None
    if bundle.verification_receipt_id is not None:
        if verification_receipt is None:
            raise evidence_ledger_inconsistent()
        verification_projection = {
            "verification_receipt_id": bundle.verification_receipt_id,
            "verification_subject": {
                "basis_version": 1,
                "kind": "task_verification_criterion",
                "authority_snapshot_id": bundle.authority_snapshot_id,
                "verification_criterion_id": bundle.verification_criterion_id,
            },
            "result": verification_receipt["result"],
            "duration_ms": verification_receipt["duration_ms"],
            "scope_coverage": verification_receipt["scope_coverage"],
            "created_at": verification_receipt["created_at"],
        }
    elif verification_receipt is not None:
        raise evidence_ledger_inconsistent()

    ordered_reviews = tuple(
        review_receipts_by_id[receipt_id]
        for receipt_id in cycle.gate_basis.qualifying_receipt_ids
    )
    runner_observation: dict[str, Any] | None = None
    if bundle.verification_runner_observation_id is not None:
        from task_governance_tool.verification_runner import (
            runner_observation_source_projection,
        )

        if (
            runner_generation is None
            or runner_generation["state"] != "terminal"
            or runner_generation["observation"] is None
            or runner_generation[
                "observation"
            ].verification_runner_observation_id
            != bundle.verification_runner_observation_id
        ):
            raise evidence_ledger_inconsistent()
        runner_observation = runner_observation_source_projection(
            observation=_verification_runner_value_dict(
                runner_generation["observation"],
                VerificationRunnerObservation,
            ),
            resolution=_verification_runner_value_dict(
                runner_generation["resolution"],
                VerificationRunnerResolution,
            ),
        )

    return ProjectionBundleRecord(
        bundle=bundle,
        cycle=cycle,
        task={
            "task_id": bundle.task_id,
            "title": snapshot["task_title"],
            "description": snapshot["task_description"],
            "review_tier": snapshot["review_tier"],
            "verification": snapshot["verification"],
        },
        authority_snapshot=dict(snapshot),
        criteria=criteria,
        artifact_manifest=dict(manifest),
        artifact_entries=entries,
        evidence_references=references,
        verification_receipt=verification_projection,
        runner_observation=runner_observation,
        review_receipts=ordered_reviews,
        finding_snapshots=bundle.finding_snapshots,
    )


def _capture_evidence_projection_basis_rows(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    _validated_runner_generations: (
        dict[tuple[str, str, int], dict[str, Any]] | None
    ) = None,
) -> EvidenceProjectionBasis:
    """Capture raw cycle/Bundle rows after repository validation."""

    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    source_schema_version = current_schema_version(connection)
    if source_schema_version not in {
        19,
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
    }:
        raise evidence_ledger_inconsistent()
    state = read_evidence_projection_state(
        connection,
        project_id=project_id,
    )
    cycle_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT * FROM task_completion_cycles
             WHERE project_id = ?
             ORDER BY task_id COLLATE BINARY, saved_cycle_ordinal,
                      completion_cycle_id COLLATE BINARY
            """,
            (project_id,),
        ),
        maximum=EVIDENCE_PROJECTION_INDEX_ENTRY_LIMIT,
    )
    cycles = tuple(_cycle_from_row(row) for row in cycle_rows)
    bundle_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT * FROM completion_evidence_bundles
             WHERE project_id = ?
             ORDER BY task_id COLLATE BINARY, cycle_ordinal,
                      completion_cycle_id COLLATE BINARY
            """,
            (project_id,),
        ),
        maximum=EVIDENCE_PROJECTION_INDEX_ENTRY_LIMIT,
    )
    member_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT member.*
              FROM completion_bundle_members AS member
              JOIN completion_evidence_bundles AS bundle
                ON bundle.completion_evidence_bundle_id =
                   member.completion_evidence_bundle_id
             WHERE bundle.project_id = ?
             ORDER BY member.completion_evidence_bundle_id COLLATE BINARY,
                      member.member_kind COLLATE BINARY, member.ordinal
            """,
            (project_id,),
        )
    )
    link_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT member.completion_evidence_bundle_id AS member_bundle_id,
                   link.*
              FROM completion_bundle_members AS member
              JOIN completion_evidence_bundles AS bundle
                ON bundle.completion_evidence_bundle_id =
                   member.completion_evidence_bundle_id
              JOIN criterion_evidence_links AS link
                ON link.criterion_evidence_link_id =
                   member.criterion_evidence_link_id
             WHERE bundle.project_id = ?
               AND member.member_kind = 'criterion_link'
             ORDER BY member.completion_evidence_bundle_id COLLATE BINARY,
                      member.ordinal
            """,
            (project_id,),
        )
    )
    finding_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT finding.*
              FROM completion_bundle_finding_snapshots AS finding
              JOIN completion_evidence_bundles AS bundle
                ON bundle.completion_evidence_bundle_id =
                   finding.completion_evidence_bundle_id
             WHERE bundle.project_id = ?
             ORDER BY finding.completion_evidence_bundle_id COLLATE BINARY,
                      finding.ordinal
            """,
            (project_id,),
        )
    )
    snapshot_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   snapshot.*
              FROM completion_evidence_bundles AS bundle
              JOIN authority_snapshots AS snapshot
                ON snapshot.authority_snapshot_id =
                   bundle.authority_snapshot_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY
            """,
            (project_id,),
        )
    )
    criterion_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   criterion.*
              FROM completion_evidence_bundles AS bundle
              JOIN authority_snapshot_criteria AS binding
                ON binding.authority_snapshot_id =
                   bundle.authority_snapshot_id
              JOIN contract_criteria AS criterion
                ON criterion.criterion_id = binding.criterion_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY,
                      CASE criterion.criterion_kind
                        WHEN 'acceptance' THEN 0 ELSE 1 END,
                      criterion.criterion_id COLLATE BINARY
            """,
            (project_id,),
        )
    )
    manifest_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   manifest.*
              FROM completion_evidence_bundles AS bundle
              JOIN artifact_manifests AS manifest
                ON manifest.artifact_manifest_id =
                   bundle.artifact_manifest_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY
            """,
            (project_id,),
        )
    )
    entry_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   entry.*
              FROM completion_evidence_bundles AS bundle
              JOIN artifact_manifest_entries AS entry
                ON entry.artifact_manifest_id = bundle.artifact_manifest_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY,
                      entry.ordinal
            """,
            (project_id,),
        )
    )
    reference_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT member.completion_evidence_bundle_id AS member_bundle_id,
                   member.ordinal AS member_ordinal, reference.*
              FROM completion_bundle_members AS member
              JOIN completion_evidence_bundles AS bundle
                ON bundle.completion_evidence_bundle_id =
                   member.completion_evidence_bundle_id
              JOIN evidence_references AS reference
                ON reference.evidence_reference_id =
                   member.evidence_reference_id
             WHERE bundle.project_id = ?
               AND member.member_kind = 'evidence_reference'
             ORDER BY member.completion_evidence_bundle_id COLLATE BINARY,
                      member.ordinal
            """,
            (project_id,),
        )
    )
    verification_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   receipt.*
              FROM completion_evidence_bundles AS bundle
              JOIN verification_receipts AS receipt
                ON receipt.verification_receipt_id =
                   bundle.verification_receipt_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY
            """,
            (project_id,),
        )
    )
    members_by_bundle: dict[str, list[PreparedCompletionBundleMember]] = {}
    for row in member_rows:
        members_by_bundle.setdefault(
            str(row["completion_evidence_bundle_id"]),
            [],
        ).append(PreparedCompletionBundleMember(**dict(row)))
    links_by_bundle: dict[str, list[PreparedCriterionEvidenceLink]] = {}
    for row in link_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        links_by_bundle.setdefault(bundle_id, []).append(
            PreparedCriterionEvidenceLink(**values)
        )
    findings_by_bundle: dict[
        str,
        list[PreparedCompletionFindingSnapshot],
    ] = {}
    for row in finding_rows:
        findings_by_bundle.setdefault(
            str(row["completion_evidence_bundle_id"]),
            [],
        ).append(PreparedCompletionFindingSnapshot(**dict(row)))
    snapshots_by_bundle_id: dict[str, dict[str, Any]] = {}
    for row in snapshot_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        if bundle_id in snapshots_by_bundle_id:
            raise evidence_ledger_inconsistent()
        snapshots_by_bundle_id[bundle_id] = values
    criteria_by_bundle: dict[str, list[dict[str, Any]]] = {}
    for row in criterion_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        criteria_by_bundle.setdefault(bundle_id, []).append(values)
    manifests_by_bundle: dict[str, dict[str, Any]] = {}
    for row in manifest_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        if bundle_id in manifests_by_bundle:
            raise evidence_ledger_inconsistent()
        manifests_by_bundle[bundle_id] = values
    entries_by_bundle: dict[str, list[dict[str, Any]]] = {}
    for row in entry_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        entries_by_bundle.setdefault(bundle_id, []).append(values)
    references_by_bundle: dict[str, list[dict[str, Any]]] = {}
    for row in reference_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        values.pop("member_ordinal")
        references_by_bundle.setdefault(bundle_id, []).append(values)
    verification_by_bundle: dict[str, dict[str, Any]] = {}
    for row in verification_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        if bundle_id in verification_by_bundle:
            raise evidence_ledger_inconsistent()
        verification_by_bundle[bundle_id] = _validate_verification_receipt_row(
            values
        )
    bundles = tuple(
        PreparedCompletionEvidenceBundle(
            completion_evidence_bundle_id=str(
                row["completion_evidence_bundle_id"]
            ),
            project_id=str(row["project_id"]),
            task_id=str(row["task_id"]),
            completion_cycle_id=str(row["completion_cycle_id"]),
            cycle_ordinal=row["cycle_ordinal"],
            source_schema_version=row["source_schema_version"],
            bundle_version=row["bundle_version"],
            contract_revision=row["contract_revision"],
            authority_snapshot_id=str(row["authority_snapshot_id"]),
            acceptance_criterion_id=row["acceptance_criterion_id"],
            verification_criterion_id=row["verification_criterion_id"],
            target_kind=str(row["target_kind"]),
            target_value=str(row["target_value"]),
            target_base_revision=str(row["target_base_revision"]),
            target_generation=row["target_generation"],
            target_capture_version=row["target_capture_version"],
            artifact_manifest_id=str(row["artifact_manifest_id"]),
            verification_receipt_id=row["verification_receipt_id"],
            verification_basis_kind=(
                row["verification_basis_kind"]
                if "verification_basis_kind" in row.keys()
                else None
            ),
            verification_runner_observation_id=(
                row["verification_runner_observation_id"]
                if "verification_runner_observation_id" in row.keys()
                else None
            ),
            omission_mask=row["omission_mask"],
            sealed_at=str(row["sealed_at"]),
            bundle_digest=str(row["bundle_digest"]),
            payload_size_bytes=row["payload_size_bytes"],
            criterion_links=tuple(
                links_by_bundle.get(
                    str(row["completion_evidence_bundle_id"]),
                    [],
                )
            ),
            members=tuple(
                members_by_bundle.get(
                    str(row["completion_evidence_bundle_id"]),
                    [],
                )
            ),
            finding_snapshots=tuple(
                findings_by_bundle.get(
                    str(row["completion_evidence_bundle_id"]),
                    [],
                )
            ),
        )
        for row in bundle_rows
    )
    cycles_by_id = {cycle.completion_cycle_id: cycle for cycle in cycles}
    bundle_by_id = {
        bundle.completion_evidence_bundle_id: bundle for bundle in bundles
    }
    selected_review_ids = {
        receipt_id
        for bundle in bundles
        for receipt_id in cycles_by_id[
            bundle.completion_cycle_id
        ].gate_basis.qualifying_receipt_ids
    }
    review_receipts_by_id: dict[str, dict[str, Any]] = {}
    if selected_review_ids:
        for receipt, provenance in _iter_validated_review_receipts_with_provenance(
            connection,
            selected_review_ids,
        ):
            receipt_id = str(receipt["review_receipt_id"])
            review_receipts_by_id[receipt_id] = {
                "receipt": dict(receipt),
                "provenance": provenance,
            }
    native_records: list[ProjectionBundleRecord] = []
    for row in bundle_rows:
        bundle_id = str(row["completion_evidence_bundle_id"])
        bundle = bundle_by_id.get(bundle_id)
        cycle = cycles_by_id.get(str(row["completion_cycle_id"]))
        snapshot = snapshots_by_bundle_id.get(bundle_id)
        manifest = manifests_by_bundle.get(bundle_id)
        references = tuple(references_by_bundle.get(bundle_id, []))
        if bundle is None or cycle is None or snapshot is None or manifest is None:
            raise evidence_ledger_inconsistent()
        native_records.append(
            _projection_bundle_record_from_validated_rows(
                bundle=bundle,
                cycle=cycle,
                snapshot=snapshot,
                criteria=tuple(criteria_by_bundle.get(bundle_id, [])),
                manifest=manifest,
                entries=tuple(entries_by_bundle.get(bundle_id, [])),
                references=references,
                verification_receipt=verification_by_bundle.get(bundle_id),
                review_receipts_by_id=review_receipts_by_id,
                runner_generation=(
                    _validated_runner_generations.get(
                        (
                            bundle.project_id,
                            bundle.task_id,
                            bundle.target_generation,
                        )
                    )
                    if _validated_runner_generations is not None
                    else None
                ),
            )
        )
    return EvidenceProjectionBasis(
        source_schema_version=source_schema_version,
        project_id=project_id,
        source_generation=state.source_generation,
        cycles=cycles,
        bundles=bundles,
        native_bundles=tuple(native_records),
    )


def capture_evidence_projection_basis(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionBasis:
    """Validate and capture one coherent Evidence projection basis once."""

    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    if current_schema_version(connection) not in {
        SCHEMA_VERSION, PRIVATE_SCHEMA22_VERSION,
    }:
        raise evidence_ledger_inconsistent()
    _validate_evidence_ledger_schema_contract(connection)
    _validate_evidence_ledger_rows(connection)
    validate_completion_cycle_storage(connection)
    bases = _validated_completion_evidence_projection_bases(connection)
    basis = bases.get(project_id)
    if basis is None:
        raise evidence_ledger_inconsistent()
    return basis


def record_evidence_projection_outcome(
    target: DatabaseTarget,
    *,
    captured_generation: int,
    outcome_code: str,
    recorded_at: str,
    index_digest: str | None = None,
) -> EvidenceProjectionState:
    """Record one projector outcome through the initialized writer boundary."""

    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            state = record_evidence_projection_outcome_locked(
                connection,
                project_id=target.project.project_id,
                captured_generation=captured_generation,
                outcome_code=outcome_code,
                recorded_at=recorded_at,
                index_digest=index_digest,
            )
            connection.commit()
            return state
        except Exception:
            connection.rollback()
            raise


def _validate_current_schema_contract(
    connection: sqlite3.Connection,
) -> int:
    version = current_schema_version(connection)
    if version != SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            (
                f"database schema version {version} does not match supported "
                f"version {SCHEMA_VERSION}; run setup to migrate"
            ),
        )

    if missing_migration_versions(connection, version):
        raise _unreadable_project_state()

    if version == PRIVATE_SCHEMA21_VERSION:
        marker = connection.execute(
            "SELECT name FROM schema_migrations WHERE version = 21"
        ).fetchone()
        if (
            marker is None
            or str(marker["name"]) != PRIVATE_SCHEMA21_MIGRATION_NAME
            or connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version > 21 LIMIT 1"
            ).fetchone()
            is not None
        ):
            raise _unreadable_project_state()
        _validate_schema21_owned_contract(connection)
    elif schema_objects_inconsistent_with_version(connection, version):
        raise _unreadable_project_state()
    try:
        _validate_completion_history_structure(connection)
        _validate_evidence_ledger_schema_contract(connection)
        _validate_completion_evidence_bundle_schema_contract(connection)
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc
    return version


def _validate_current_schema_structure(
    connection: sqlite3.Connection,
) -> int:
    """Validate the current schema contract and its global admitted rows."""

    version = _validate_current_schema_contract(connection)
    if version == PRIVATE_SCHEMA20_VERSION:
        validate_current_schema20_admitted_rows(connection)
    elif version == PRIVATE_SCHEMA21_VERSION:
        validate_current_schema21_admitted_rows(connection)
    return version


def _validate_current_project_rows(
    connection: sqlite3.Connection,
    project_id: str,
) -> None:
    if read_project_maintenance(connection, project_id) is None:
        raise _unreadable_project_state()
    if read_viewer_maintenance(connection, project_id) is None:
        raise _unreadable_project_state()
    try:
        evidence = read_evidence_projection_state(
            connection,
            project_id=project_id,
        )
        cycle_count = connection.execute(
            "SELECT COUNT(*) FROM task_completion_cycles WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        if type(cycle_count) is not int or evidence.source_generation != cycle_count:
            raise evidence_ledger_inconsistent()
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc


def validate_current_database_structure(
    connection: sqlite3.Connection,
    project_id: str,
) -> int:
    """Require complete current-schema rows without checking a caller basis."""
    version = _validate_current_schema_structure(connection)
    _validate_current_project_rows(connection, project_id)
    return version


def validate_current_database_binding(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> None:
    """Require one current binding to match the resolver-owned caller basis."""
    binding = read_project_binding_state(connection)
    _validate_target_binding(binding, target)


def validate_current_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> int:
    """Require the supported schema and matching project without migrating."""
    version = _validate_current_schema_structure(connection)
    binding = read_project_binding_state(connection)
    _validate_target_binding(binding, target)
    _validate_current_project_rows(connection, binding.project_id)
    return version


def _validate_current_task_read_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> int:
    """Validate schema, binding, and project rows for one Task-local read."""

    version = _validate_current_schema_contract(connection)
    binding = read_project_binding_state(connection)
    _validate_target_binding(binding, target)
    _validate_current_project_rows(connection, binding.project_id)
    return version


def validate_managed_backup_source_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> int:
    """Validate the stable v11 backup repository on a migration source."""
    version = current_schema_version(connection)
    if version == SCHEMA_VERSION:
        return validate_current_database(connection, target)
    if version < 11 or version > SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            "managed backup state requires schema version 11 or newer",
        )
    if missing_migration_versions(connection, version):
        raise StorageError(
            "migration_required",
            "managed backup migration history is incomplete",
        )
    if schema_objects_inconsistent_with_version(connection, version):
        raise StorageError(
            "migration_required",
            "managed backup source schema is inconsistent with its declared version",
        )
    existing_project_id = read_project_meta_id(connection)
    if existing_project_id is None:
        raise StorageError(
            "migration_required",
            "database project metadata is missing; run setup to repair",
        )
    if existing_project_id != target.project.project_id:
        raise StorageError(
            "project_mismatch",
            "task database belongs to a different project",
        )
    if read_project_maintenance(connection, existing_project_id) is None:
        raise StorageError(
            "migration_required",
            "database project maintenance state is missing; run setup to repair",
        )
    return version


def connect_initialized(
    target: DatabaseTarget,
    *,
    managed_backup_source: bool = False,
) -> sqlite3.Connection:
    """Open a current, project-matching database without taking a write lock."""
    validate_operational_journal_state(target.db_path)
    if not target.db_path.exists():
        raise StorageError(
            "db_not_initialized",
            "project state is not set up; run setup first",
        )
    try:
        connection = connect_existing(target.db_path)
    except sqlite3.Error as exc:
        if not target.db_path.exists():
            raise StorageError(
                "db_not_initialized",
                "project state is not set up; run setup first",
            ) from exc
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open database",
        ) from exc
    try:
        (
            validate_managed_backup_source_database
            if managed_backup_source
            else validate_current_database
        )(connection, target)
    except sqlite3.Error as exc:
        connection.close()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not inspect database",
        ) from exc
    except Exception:
        connection.close()
        raise
    return connection


def begin_initialized_write(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    *,
    managed_backup_source: bool = False,
) -> None:
    """Acquire the short writer transaction and revalidate its database owner."""
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "write transaction was acquired before preflight completed",
        )
    try:
        connection.execute("BEGIN IMMEDIATE")
        (
            validate_managed_backup_source_database
            if managed_backup_source
            else validate_current_database
        )(connection, target)
    except sqlite3.Error as exc:
        connection.rollback()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not start database write",
        ) from exc
    except Exception:
        connection.rollback()
        raise


def _connect_validated_readonly(
    target: DatabaseTarget,
    *,
    validator: Callable[[sqlite3.Connection, DatabaseTarget], object],
) -> sqlite3.Connection:
    """Open one read-only transaction under the supplied storage boundary."""

    try:
        connection = connect_readonly(target.db_path)
    except StorageError:
        raise
    except sqlite3.Error as exc:
        if not target.db_path.exists():
            raise StorageError(
                "db_not_initialized",
                "project state is not set up; run setup first",
            ) from exc
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open database",
        ) from exc
    try:
        validator(connection, target)
    except sqlite3.Error as exc:
        connection.rollback()
        connection.close()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not inspect database",
        ) from exc
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection


def connect_initialized_readonly(
    target: DatabaseTarget,
    *,
    managed_backup_source: bool = False,
) -> sqlite3.Connection:
    """Open one globally validated current-schema read transaction."""

    return _connect_validated_readonly(
        target,
        validator=(
            validate_managed_backup_source_database
            if managed_backup_source
            else validate_current_database
        ),
    )


def connect_initialized_task_readonly(
    target: DatabaseTarget,
) -> sqlite3.Connection:
    """Open one schema-valid read transaction for selected-Task validation."""

    return _connect_validated_readonly(
        target,
        validator=_validate_current_task_read_database,
    )


def validate_backup_policy(
    *,
    interval_minutes: int,
    generations: int,
) -> tuple[int, int]:
    if (
        isinstance(interval_minutes, bool)
        or not isinstance(interval_minutes, int)
        or not MIN_BACKUP_INTERVAL_MINUTES
        <= interval_minutes
        <= MAX_BACKUP_INTERVAL_MINUTES
        or isinstance(generations, bool)
        or not isinstance(generations, int)
        or not MIN_BACKUP_GENERATIONS <= generations <= MAX_BACKUP_GENERATIONS
    ):
        raise StorageError(
            "invalid_backup_policy",
            "backup policy is outside the supported range",
        )
    return interval_minutes, generations


def configure_project_maintenance(
    target: DatabaseTarget,
    *,
    requested_interval_minutes: int | None,
    requested_generations: int | None,
    enabled_at: str | None = None,
) -> tuple[int, int]:
    activation_time = validate_utc_timestamp(
        enabled_at or utc_now(),
        field="maintenance enablement time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            current = read_project_maintenance(
                connection,
                target.project.project_id,
            )
            if current is None:
                raise StorageError(
                    "migration_required",
                    "database project maintenance state is missing; run setup to repair",
                )
            interval_minutes = (
                requested_interval_minutes
                if requested_interval_minutes is not None
                else (
                    current.backup_interval_minutes
                    if current.enabled
                    else DEFAULT_BACKUP_INTERVAL_MINUTES
                )
            )
            generations = (
                requested_generations
                if requested_generations is not None
                else (
                    current.backup_generations
                    if current.enabled
                    else DEFAULT_BACKUP_GENERATIONS
                )
            )
            interval_minutes, generations = validate_backup_policy(
                interval_minutes=interval_minutes,
                generations=generations,
            )
            if (
                current.enabled
                and current.backup_interval_minutes == interval_minutes
                and current.backup_generations == generations
            ):
                connection.commit()
                return interval_minutes, generations
            connection.execute(
                """
                UPDATE project_maintenance
                   SET enabled_at = COALESCE(enabled_at, ?),
                       backup_interval_minutes = ?,
                       backup_generations = ?
                 WHERE project_id = ?
                """,
                (
                    activation_time,
                    interval_minutes,
                    generations,
                    target.project.project_id,
                ),
            )
            connection.commit()
            return interval_minutes, generations
        except Exception:
            connection.rollback()
            raise


def _begin_backup_metadata_write(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    *,
    allowed_versions: set[int],
) -> int:
    if connection.in_transaction:
        raise StorageError(
            "internal_error",
            "backup metadata write requires no active transaction",
        )
    try:
        connection.execute("BEGIN IMMEDIATE")
        version = current_schema_version(connection)
        if (
            version not in allowed_versions
            or missing_migration_versions(connection, version)
            or read_project_meta_id(connection) != target.project.project_id
            or read_project_maintenance(
                connection,
                target.project.project_id,
            )
            is None
        ):
            raise StorageError(
                "migration_required",
                "backup metadata requires valid project maintenance state",
            )
        return version
    except sqlite3.Error as exc:
        connection.rollback()
        raise operational_sqlite_error(
            exc,
            fallback_message="could not start backup metadata write",
        ) from exc
    except Exception:
        connection.rollback()
        raise


def _metadata_from_generation_row(
    row: sqlite3.Row,
) -> MigrationBackupMetadata:
    return validate_migration_backup_metadata(
        MigrationBackupMetadata(
            generation_id=str(row["generation_id"]),
            published_at=str(row["published_at"]),
            publication_retention=validate_sqlite_integer_storage_class(
                row["publication_retention"]
            ),
        )
    )


def _write_backup_success(
    connection: sqlite3.Connection,
    project_id: str,
    metadata: MigrationBackupMetadata,
) -> None:
    validated = validate_migration_backup_metadata(metadata)
    connection.execute(
        """
        UPDATE project_maintenance
           SET applied_backup_generations = ?,
               backup_last_success_at = ?,
               backup_last_outcome_code = 'succeeded',
               backup_last_outcome_at = ?,
               latest_backup_generation_id = ?
         WHERE project_id = ?
        """,
        (
            validated.publication_retention,
            validated.published_at,
            validated.published_at,
            validated.generation_id,
            project_id,
        ),
    )


def _insert_managed_backup_generation(
    connection: sqlite3.Connection,
    project_id: str,
    metadata: MigrationBackupMetadata,
    *,
    allow_existing: bool,
) -> None:
    validated = validate_migration_backup_metadata(metadata)
    existing = connection.execute(
        """
        SELECT generation_id, published_at, publication_retention
          FROM managed_backup_generations
         WHERE generation_id = ? AND project_id = ?
        """,
        (validated.generation_id, project_id),
    ).fetchone()
    if existing is not None:
        if (
            not allow_existing
            or _metadata_from_generation_row(existing) != validated
        ):
            raise StorageError(
                "internal_error",
                "managed backup generation metadata changed",
            )
        return
    connection.execute(
        """
        INSERT INTO managed_backup_generations(
          generation_id, project_id, published_at, publication_retention
        ) VALUES (?, ?, ?, ?)
        """,
        (
            validated.generation_id,
            project_id,
            validated.published_at,
            validated.publication_retention,
        ),
    )


def record_setup_backup(
    target: DatabaseTarget,
    metadata: MigrationBackupMetadata,
) -> None:
    validated = validate_migration_backup_metadata(metadata)
    validate_operational_journal_state(target.db_path)
    with closing(connect_existing(target.db_path)) as connection:
        try:
            _begin_backup_metadata_write(
                connection,
                target,
                allowed_versions={10},
            )
            _write_backup_success(
                connection,
                target.project.project_id,
                validated,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def list_managed_backup_generations(
    connection: sqlite3.Connection,
    project_id: str,
) -> tuple[MigrationBackupMetadata, ...]:
    rows = connection.execute(
        """
        SELECT generation_id, published_at, publication_retention
          FROM managed_backup_generations
         WHERE project_id = ?
         ORDER BY published_at, generation_id
        """,
        (project_id,),
    ).fetchall()
    return tuple(
        _metadata_from_generation_row(row)
        for row in rows
    )


def _connect_managed_backup_repository(
    target: DatabaseTarget,
    *,
    migration_source: bool,
    read_only: bool,
) -> sqlite3.Connection:
    return (
        connect_initialized_readonly(
            target,
            managed_backup_source=migration_source,
        )
        if read_only
        else connect_initialized(
            target,
            managed_backup_source=migration_source,
        )
    )


def _begin_managed_backup_repository_write(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    *,
    migration_source: bool,
) -> None:
    begin_initialized_write(
        connection,
        target,
        managed_backup_source=migration_source,
    )


def read_managed_backup_repository(
    target: DatabaseTarget,
    *,
    migration_source: bool = False,
) -> ManagedBackupRepositoryState:
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=True,
        )
    ) as connection:
        maintenance = read_project_maintenance(
            connection,
            target.project.project_id,
        )
        if maintenance is None:
            raise StorageError(
                "migration_required",
                "database project maintenance state is missing; run setup to repair",
            )
        generations = list_managed_backup_generations(
            connection,
            target.project.project_id,
        )
    return ManagedBackupRepositoryState(
        maintenance=maintenance,
        generations=generations,
    )


def _latest_managed_backup_row(
    connection: sqlite3.Connection,
    project_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT generation_id, published_at, publication_retention
          FROM managed_backup_generations
         WHERE project_id = ?
         ORDER BY published_at DESC, generation_id DESC
         LIMIT 1
        """,
        (project_id,),
    ).fetchone()


def _update_backup_pointer_from_row(
    connection: sqlite3.Connection,
    project_id: str,
    row: sqlite3.Row | None,
    *,
    outcome_code: str | None = None,
    outcome_at: str | None = None,
) -> None:
    if outcome_code is not None:
        if outcome_code not in {"deferred", "failed"} or outcome_at is None:
            raise StorageError("internal_error", "backup outcome is invalid")
        validate_utc_timestamp(outcome_at, field="backup outcome time")
    if row is None:
        metadata = None
    else:
        metadata = _metadata_from_generation_row(row)
    connection.execute(
        """
        UPDATE project_maintenance
           SET applied_backup_generations = ?,
               backup_last_success_at = ?,
               backup_last_outcome_code =
                 CASE WHEN ? IS NULL THEN backup_last_outcome_code ELSE ? END,
               backup_last_outcome_at =
                 CASE WHEN ? IS NULL THEN backup_last_outcome_at ELSE ? END,
               latest_backup_generation_id = ?
         WHERE project_id = ?
        """,
        (
            metadata.publication_retention if metadata is not None else None,
            metadata.published_at if metadata is not None else None,
            outcome_code,
            outcome_code,
            outcome_code,
            outcome_at,
            metadata.generation_id if metadata is not None else None,
            project_id,
        ),
    )


def record_managed_backup(
    target: DatabaseTarget,
    metadata: MigrationBackupMetadata,
    *,
    migration_source: bool = False,
) -> None:
    validated = validate_migration_backup_metadata(metadata)
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=False,
        )
    ) as connection:
        try:
            _begin_managed_backup_repository_write(
                connection,
                target,
                migration_source=migration_source,
            )
            _insert_managed_backup_generation(
                connection,
                target.project.project_id,
                validated,
                allow_existing=False,
            )
            _write_backup_success(
                connection,
                target.project.project_id,
                validated,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def import_managed_backup_generations(
    target: DatabaseTarget,
    metadata_items: tuple[MigrationBackupMetadata, ...],
    *,
    migration_source: bool = False,
) -> None:
    validated = validate_managed_backup_metadata_set(metadata_items)
    if not validated:
        return
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=False,
        )
    ) as connection:
        try:
            _begin_managed_backup_repository_write(
                connection,
                target,
                migration_source=migration_source,
            )
            for metadata in validated:
                _insert_managed_backup_generation(
                    connection,
                    target.project.project_id,
                    metadata,
                    allow_existing=True,
                )
            latest = _latest_managed_backup_row(
                connection,
                target.project.project_id,
            )
            if latest is None:
                raise StorageError(
                    "internal_error",
                    "managed backup import did not persist a generation",
                )
            _write_backup_success(
                connection,
                target.project.project_id,
                _metadata_from_generation_row(latest),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def delete_managed_backup_generation(
    target: DatabaseTarget,
    generation_id: str,
    *,
    failure_at: str | None = None,
    migration_source: bool = False,
) -> None:
    if not MANAGED_BACKUP_GENERATION_PATTERN.fullmatch(generation_id):
        raise StorageError("internal_error", "managed backup identity is invalid")
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=False,
        )
    ) as connection:
        try:
            _begin_managed_backup_repository_write(
                connection,
                target,
                migration_source=migration_source,
            )
            connection.execute(
                """
                DELETE FROM managed_backup_generations
                 WHERE generation_id = ? AND project_id = ?
                """,
                (generation_id, target.project.project_id),
            )
            latest = _latest_managed_backup_row(
                connection,
                target.project.project_id,
            )
            _update_backup_pointer_from_row(
                connection,
                target.project.project_id,
                latest,
                outcome_code="failed" if failure_at is not None else None,
                outcome_at=failure_at,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def normalize_managed_backup_pointer(
    target: DatabaseTarget,
    *,
    migration_source: bool = False,
) -> None:
    with closing(
        _connect_managed_backup_repository(
            target,
            migration_source=migration_source,
            read_only=False,
        )
    ) as connection:
        try:
            _begin_managed_backup_repository_write(
                connection,
                target,
                migration_source=migration_source,
            )
            latest = _latest_managed_backup_row(
                connection,
                target.project.project_id,
            )
            _update_backup_pointer_from_row(
                connection,
                target.project.project_id,
                latest,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def record_backup_attempt_outcome(
    target: DatabaseTarget,
    *,
    code: str,
    occurred_at: str,
) -> None:
    if code not in {"deferred", "failed"}:
        raise StorageError("internal_error", "backup outcome is invalid")
    timestamp = validate_utc_timestamp(
        occurred_at,
        field="backup outcome time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            connection.execute(
                """
                UPDATE project_maintenance
                   SET backup_last_outcome_code = ?,
                       backup_last_outcome_at = ?
                 WHERE project_id = ?
                """,
                (code, timestamp, target.project.project_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def record_viewer_publication(
    target: DatabaseTarget,
    *,
    source_generation: int,
    published_at: str,
) -> None:
    if (
        isinstance(source_generation, bool)
        or not isinstance(source_generation, int)
        or source_generation < 0
    ):
        raise StorageError(
            "internal_error",
            "Viewer publication generation is invalid",
        )
    timestamp = validate_utc_timestamp(
        published_at,
        field="Viewer publication time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            cursor = connection.execute(
                """
                UPDATE viewer_maintenance_state
                   SET rendered_generation = ?,
                       last_success_at = CASE
                         WHEN last_success_at IS NULL OR last_success_at <= ?
                         THEN ?
                         ELSE last_success_at
                       END,
                       last_outcome_code = CASE
                         WHEN last_outcome_at IS NULL OR last_outcome_at <= ?
                         THEN 'succeeded'
                         ELSE last_outcome_code
                       END,
                       last_outcome_at = CASE
                         WHEN last_outcome_at IS NULL OR last_outcome_at <= ?
                         THEN ?
                         ELSE last_outcome_at
                       END
                 WHERE project_id = ?
                   AND (
                     rendered_generation IS NULL
                     OR rendered_generation <= ?
                   )
                   AND source_generation >= ?
                """,
                (
                    source_generation,
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                    target.project.project_id,
                    source_generation,
                    source_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "internal_error",
                    "Viewer publication generation changed unexpectedly",
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def record_viewer_attempt_outcome(
    target: DatabaseTarget,
    *,
    code: str,
    occurred_at: str,
) -> None:
    if code not in {"deferred", "failed"}:
        raise StorageError("internal_error", "Viewer outcome is invalid")
    timestamp = validate_utc_timestamp(
        occurred_at,
        field="Viewer outcome time",
    )
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            cursor = connection.execute(
                """
                UPDATE viewer_maintenance_state
                   SET last_outcome_code = CASE
                         WHEN last_outcome_at IS NULL
                           OR last_outcome_at < ?
                           OR (
                             last_outcome_at = ?
                             AND source_generation
                               > COALESCE(rendered_generation, -1)
                           )
                         THEN ?
                         ELSE last_outcome_code
                       END,
                       last_outcome_at = CASE
                         WHEN last_outcome_at IS NULL
                           OR last_outcome_at < ?
                           OR (
                             last_outcome_at = ?
                             AND source_generation
                               > COALESCE(rendered_generation, -1)
                           )
                         THEN ?
                         ELSE last_outcome_at
                       END
                 WHERE project_id = ?
                """,
                (
                    timestamp,
                    timestamp,
                    code,
                    timestamp,
                    timestamp,
                    timestamp,
                    target.project.project_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "internal_error",
                    "Viewer maintenance state is missing",
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def compare_and_swap_project_binding(
    target: DatabaseTarget,
    *,
    project_id: str,
    identity_scheme: str,
    expected_generation: int,
    expected_old_hash: str,
    new_hash: str,
    new_display_name: str,
    reason: str,
    confirmation_token_digest: str,
    bound_at: str,
    fail_stage: str | None = None,
) -> ProjectBindingState:
    """Append one confirmed binding and advance Viewer source state atomically."""
    validate_identity_project_id(project_id, identity_scheme)
    validate_binding_generation(expected_generation)
    validate_lower_hex_64(expected_old_hash, field="previous canonical path hash")
    validate_lower_hex_64(new_hash, field="canonical path hash")
    display_name = validate_project_display_name(new_display_name)
    if expected_old_hash == new_hash:
        raise StorageError("internal_error", "project binding did not change")
    if reason != "confirmed_relocation":
        raise StorageError("internal_error", "project binding reason is invalid")
    token_digest = validate_lower_hex_64(
        confirmation_token_digest,
        field="confirmation token digest",
    )
    timestamp = validate_utc_timestamp(bound_at, field="project binding time")
    if (
        target.project.project_id != project_id
        or target.project.canonical_path_hash != new_hash
        or sanitize_project_display_name(target.project.display_name) != display_name
    ):
        raise StorageError("internal_error", "project binding target is invalid")

    try:
        opened_connection = connect_initialized(target)
    except StorageError as exc:
        if exc.code in {"internal_error", "migration_required"}:
            raise _unreadable_project_state() from exc
        raise
    with closing(opened_connection) as connection:
        try:
            begin_initialized_write(connection, target)
            current = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            if (
                current.identity_scheme != identity_scheme
                or current.binding_generation != expected_generation
                or current.canonical_path_hash != expected_old_hash
            ):
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            if current.binding_generation >= SQLITE_INT64_MAX:
                raise _unreadable_project_state()
            viewer_row = connection.execute(
                """
                SELECT source_generation
                  FROM viewer_maintenance_state
                 WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if viewer_row is None:
                raise _unreadable_project_state()
            try:
                viewer_generation = validate_viewer_generation(
                    viewer_row["source_generation"],
                    field="Viewer source generation",
                )
            except StorageError as exc:
                raise _unreadable_project_state() from exc
            if viewer_generation >= SQLITE_INT64_MAX:
                raise _unreadable_project_state()

            next_generation = current.binding_generation + 1
            connection.execute(
                """
                INSERT INTO project_path_binding_history(
                  project_id, binding_generation, previous_path_hash,
                  canonical_path_hash, display_name, reason,
                  confirmation_token_digest, bound_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    next_generation,
                    expected_old_hash,
                    new_hash,
                    display_name,
                    reason,
                    token_digest,
                    timestamp,
                ),
            )
            if fail_stage == "after_history":
                raise StorageError(
                    "internal_error",
                    "injected project binding failure",
                )

            cursor = connection.execute(
                """
                UPDATE project_meta
                   SET canonical_path_hash = ?,
                       display_name = ?,
                       binding_generation = ?,
                       binding_reason = ?,
                       binding_updated_at = ?,
                       updated_at = ?
                 WHERE project_id = ?
                   AND identity_scheme = ?
                   AND binding_generation = ?
                   AND canonical_path_hash = ?
                """,
                (
                    new_hash,
                    display_name,
                    next_generation,
                    reason,
                    timestamp,
                    timestamp,
                    project_id,
                    identity_scheme,
                    expected_generation,
                    expected_old_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            if fail_stage == "after_current":
                raise StorageError(
                    "internal_error",
                    "injected project binding failure",
                )

            cursor = connection.execute(
                """
                UPDATE viewer_maintenance_state
                   SET source_generation = source_generation + 1
                 WHERE project_id = ?
                   AND source_generation < ?
                """,
                (project_id, SQLITE_INT64_MAX),
            )
            if cursor.rowcount != 1:
                raise _unreadable_project_state()
            if fail_stage == "after_viewer":
                raise StorageError(
                    "internal_error",
                    "injected project binding failure",
                )

            updated = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            if fail_stage == "before_commit":
                raise StorageError(
                    "internal_error",
                    "injected project binding failure",
                )
            connection.commit()
            return updated
        except StorageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise operational_sqlite_error(
                exc,
                fallback_message="project state could not be updated safely",
            ) from exc
        except Exception:
            connection.rollback()
            raise


def set_legacy_cleanup_pending(
    target: DatabaseTarget,
    *,
    project_id: str,
    expected_identity_scheme: str,
    expected_generation: int,
    expected_path_hash: str,
    inventory: str,
    fingerprint: str,
) -> ProjectBindingState:
    """Persist one canonical cleanup plan against an exact binding basis."""

    validate_identity_project_id(project_id, expected_identity_scheme)
    validate_binding_generation(expected_generation)
    validate_lower_hex_64(expected_path_hash, field="canonical path hash")
    validate_cleanup_inventory(inventory, fingerprint)
    if target.project.project_id != project_id:
        raise StorageError("internal_error", "project binding target is invalid")
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            current = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            if (
                current.identity_scheme != expected_identity_scheme
                or current.binding_generation != expected_generation
                or current.canonical_path_hash != expected_path_hash
            ):
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            if current.legacy_cleanup_pending:
                if (
                    current.legacy_cleanup_inventory != inventory
                    or current.legacy_cleanup_fingerprint != fingerprint
                ):
                    raise StorageError(
                        "project_binding_stale",
                        "project binding state changed",
                    )
                connection.rollback()
                return current
            cursor = connection.execute(
                """
                UPDATE project_meta
                   SET legacy_cleanup_pending = 1,
                       legacy_cleanup_inventory = ?,
                       legacy_cleanup_fingerprint = ?
                 WHERE project_id = ?
                   AND identity_scheme = ?
                   AND binding_generation = ?
                   AND canonical_path_hash = ?
                   AND legacy_cleanup_pending = 0
                """,
                (
                    inventory,
                    fingerprint,
                    project_id,
                    expected_identity_scheme,
                    expected_generation,
                    expected_path_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            updated = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            connection.commit()
            return updated
        except StorageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise operational_sqlite_error(
                exc,
                fallback_message="project state could not be updated safely",
            ) from exc


def clear_legacy_cleanup_pending(
    target: DatabaseTarget,
    *,
    project_id: str,
    expected_identity_scheme: str,
    expected_generation: int,
    expected_path_hash: str,
    expected_inventory_fingerprint: str,
) -> ProjectBindingState:
    """Clear only the persisted cleanup plan proven complete by setup."""

    validate_identity_project_id(project_id, expected_identity_scheme)
    validate_binding_generation(expected_generation)
    validate_lower_hex_64(expected_path_hash, field="canonical path hash")
    validate_lower_hex_64(
        expected_inventory_fingerprint,
        field="legacy cleanup fingerprint",
    )
    if target.project.project_id != project_id:
        raise StorageError("internal_error", "project binding target is invalid")
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            current = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            if (
                current.identity_scheme != expected_identity_scheme
                or current.binding_generation != expected_generation
                or current.canonical_path_hash != expected_path_hash
                or not current.legacy_cleanup_pending
                or current.legacy_cleanup_inventory is None
                or current.legacy_cleanup_fingerprint
                != expected_inventory_fingerprint
            ):
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            validate_cleanup_inventory(
                current.legacy_cleanup_inventory,
                expected_inventory_fingerprint,
            )
            cursor = connection.execute(
                """
                UPDATE project_meta
                   SET legacy_cleanup_pending = 0,
                       legacy_cleanup_inventory = NULL,
                       legacy_cleanup_fingerprint = NULL
                 WHERE project_id = ?
                   AND identity_scheme = ?
                   AND binding_generation = ?
                   AND canonical_path_hash = ?
                   AND legacy_cleanup_pending = 1
                   AND legacy_cleanup_fingerprint = ?
                """,
                (
                    project_id,
                    expected_identity_scheme,
                    expected_generation,
                    expected_path_hash,
                    expected_inventory_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            updated = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            connection.commit()
            return updated
        except StorageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise operational_sqlite_error(
                exc,
                fallback_message="project state could not be updated safely",
            ) from exc


def _validate_snapshot_database_state(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> tuple[int, tuple[sqlite3.Row, ...] | None]:
    """Revalidate schema and project identity inside a viewer read transaction."""
    query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
    if not connection.in_transaction or query_only != 1:
        raise StorageError(
            "internal_error",
            "viewer snapshot requires an active query-only transaction",
        )

    version = current_schema_version(connection)
    if version < VIEWER_MIN_SOURCE_SCHEMA_VERSION or version > SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            (
                f"database schema version {version} is not supported by "
                "Viewer snapshot version 4"
            ),
        )
    if missing_migration_versions(connection, version):
        raise StorageError(
            "migration_required",
            "database migration history is incomplete",
        )
    if schema_objects_inconsistent_with_version(connection, version):
        raise StorageError(
            "migration_required",
            "database schema is inconsistent with its declared version",
        )
    if version == PRIVATE_SCHEMA20_VERSION:
        validate_current_schema20_admitted_rows(connection)
    validated_task_rows: tuple[sqlite3.Row, ...] | None = None
    if version >= 15:
        try:
            _validate_completion_history_structure(connection)
            if version >= 18:
                validated_task_rows = (
                    _validate_evidence_ledger_storage_with_task_rows(connection)
                )
        except StorageError as exc:
            if exc.code == "database_busy":
                raise
            raise _unreadable_project_state() from exc

    required_tables = {
        "schema_migrations",
        "project_meta",
        "tasks",
        "task_events",
        "review_receipts",
        "review_findings",
    }
    table_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    tables = {str(row["name"]) for row in table_rows}
    if required_tables - tables:
        raise StorageError(
            "migration_required",
            "database schema is incomplete for Viewer snapshot version 4",
        )

    from task_governance_tool.tasks import VIEWER_TASK_FIELDS

    task_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if set(VIEWER_TASK_FIELDS) - task_columns:
        raise StorageError(
            "migration_required",
            "database task schema is incomplete for Viewer snapshot version 4",
        )
    receipt_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(review_receipts)").fetchall()
    }
    required_receipt_columns = {
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
    }
    if version >= 6:
        required_receipt_columns.add("target_base_revision")
    if required_receipt_columns - receipt_columns:
        raise StorageError(
            "migration_required",
            "database review schema is incomplete for Viewer snapshot version 4",
        )

    if version >= 14:
        binding = read_project_binding_state(connection)
        _validate_target_binding(binding, target)
        existing_project_id = binding.project_id
    else:
        existing_project_id = read_project_meta_id(connection)
        if existing_project_id is None:
            raise StorageError(
                "migration_required",
                "database project metadata is missing",
            )
    if existing_project_id != target.project.project_id:
        raise StorageError(
            "project_mismatch",
            "task database belongs to a different project",
        )
    return version, validated_task_rows


def validate_snapshot_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> int:
    """Revalidate one Viewer source while preserving the legacy int result."""

    version, _ = _validate_snapshot_database_state(connection, target)
    return version


def validate_snapshot_database_for_viewer(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> ViewerSnapshotDatabaseValidation:
    """Validate a Viewer source and issue one current Evidence Task proof."""

    version, task_rows = _validate_snapshot_database_state(connection, target)
    if version not in {18, 19, 20, 21}:
        return ViewerSnapshotDatabaseValidation(source_schema_version=version)
    if task_rows is None:
        raise _unreadable_project_state()

    savepoint_name = f"taskgov_viewer_batch_{secrets.token_hex(16)}"
    if _VIEWER_TASK_BATCH_SAVEPOINT_PATTERN.fullmatch(savepoint_name) is None:
        raise _unreadable_project_state()
    try:
        data_version = connection.execute("PRAGMA data_version").fetchone()[0]
        if type(data_version) is not int or data_version < 0:
            raise _unreadable_project_state()
        connection.execute(f"SAVEPOINT {savepoint_name}")
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc
    task_ids = tuple(row["task_id"] for row in task_rows)
    task_count = len(task_rows)
    return ViewerSnapshotDatabaseValidation(
        source_schema_version=version,
        validated_task_batch=_register_validated_viewer_task_batch(
            connection,
            project_id=target.project.project_id,
            source_schema_version=version,
            task_ids=task_ids,
            task_count=task_count,
            data_version=data_version,
            savepoint_name=savepoint_name,
        ),
    )


def _consume_validated_viewer_task_batch(
    connection: sqlite3.Connection,
    batch: _ValidatedViewerTaskBatch,
    *,
    project_id: str,
    source_schema_version: int,
    task_rows: list[sqlite3.Row],
) -> None:
    """Consume one exact current Evidence proof after the Viewer Task query."""

    if type(batch) is not _ValidatedViewerTaskBatch:
        raise _unreadable_project_state()
    with _VIEWER_TASK_BATCH_ISSUANCE_LOCK:
        registered = _VIEWER_TASK_BATCH_ISSUANCES.pop(id(batch), None)
    if registered is None or registered[0]() is not batch:
        raise _unreadable_project_state()
    issuance = registered[1]
    try:
        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
        data_version = connection.execute("PRAGMA data_version").fetchone()[0]
        task_ids = tuple(row["task_id"] for row in task_rows)
        task_project_ids = tuple(row["project_id"] for row in task_rows)
    except (IndexError, KeyError, TypeError, sqlite3.Error) as exc:
        if isinstance(exc, sqlite3.Error):
            raise stored_task_sqlite_error(exc) from exc
        raise _unreadable_project_state() from exc
    if (
        type(issuance.project_id) is not str
        or not issuance.project_id
        or type(issuance.source_schema_version) is not int
        or type(issuance.task_ids) is not tuple
        or any(type(task_id) is not str for task_id in issuance.task_ids)
        or type(issuance.task_count) is not int
        or issuance.task_count < 0
        or type(issuance.data_version) is not int
        or issuance.data_version < 0
        or type(issuance.savepoint_name) is not str
        or _VIEWER_TASK_BATCH_SAVEPOINT_PATTERN.fullmatch(issuance.savepoint_name)
        is None
        or issuance.connection is not connection
        or not connection.in_transaction
        or query_only != 1
        or type(data_version) is not int
        or data_version != issuance.data_version
        or source_schema_version not in {18, 19, 20, 21}
        or issuance.source_schema_version != source_schema_version
        or type(project_id) is not str
        or not project_id
        or issuance.project_id != project_id
        or len(task_rows) != issuance.task_count
        or any(type(task_id) is not str for task_id in task_ids)
        or any(value != project_id for value in task_project_ids)
        or tuple(sorted(task_ids)) != issuance.task_ids
    ):
        raise _unreadable_project_state()
    try:
        connection.execute(f"RELEASE SAVEPOINT {issuance.savepoint_name}")
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc


def count_tasks(connection: sqlite3.Connection, project_id: str) -> dict[str, int]:
    from task_governance_tool.selection import count_next_tasks

    counts = empty_counts()
    counts["active"] = int(
        connection.execute(
            """
            SELECT COUNT(*) AS count
              FROM tasks
             WHERE project_id = ?
               AND status IN ('ready', 'in_progress', 'paused', 'blocked', 'review_pending')
            """,
            (project_id,),
        ).fetchone()["count"]
    )
    for status, key in (
        ("paused", "paused"),
        ("blocked", "blocked"),
        ("review_pending", "review_pending"),
        ("done", "done"),
    ):
        counts[key] = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE project_id = ? AND status = ?",
                (project_id, status),
            ).fetchone()["count"]
        )
    counts["next_actionable"] = count_next_tasks(connection, project_id)
    counts["handoff_pending"] = int(
        connection.execute(
            """
            SELECT COUNT(*) AS count
              FROM handoff_records
             WHERE project_id = ?
               AND state = 'pending_handoff'
            """,
            (project_id,),
        ).fetchone()["count"]
    )
    return counts


def read_setup_state(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> SetupStorageState:
    try:
        version = current_schema_version(connection)
        if version == 0:
            return SetupStorageState(
                schema_version=None,
                needs_initialize=True,
                needs_migration=False,
                maintenance_enabled=False,
                backup_interval_minutes=None,
                backup_generations=None,
            )
        if version > SCHEMA_VERSION:
            raise StorageError(
                "schema_too_new",
                "task database schema is newer than this taskgov version",
            )
        if missing_migration_versions(connection, version):
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        if schema_objects_inconsistent_with_version(connection, version):
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        if not table_exists(connection, "project_meta"):
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        if version >= 14:
            binding = read_project_binding_state(connection)
            _validate_target_binding(binding, target)
            existing_project_id = binding.project_id
        else:
            existing_project_id = read_project_meta_id(connection)
            if existing_project_id is None:
                raise StorageError(
                    "project_state_unreadable",
                    "project state could not be read safely",
                )
        if version < 14 and existing_project_id != target.project.project_id:
            raise StorageError(
                "project_mismatch",
                "task database belongs to a different project",
            )
        if version == PRIVATE_SCHEMA20_VERSION:
            validate_current_schema20_admitted_rows(connection)
        elif version == PRIVATE_SCHEMA21_VERSION:
            validate_schema21_storage(connection)
        from task_governance_tool.tasks import validate_stored_task_rows

        try:
            task_rows = connection.execute(
                "SELECT * FROM tasks ORDER BY task_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise stored_task_sqlite_error(exc) from exc
        validate_stored_task_rows(
            task_rows,
            connection=connection,
            source_schema_version=version,
            expected_project_id=target.project.project_id,
        )
        if version == SCHEMA_VERSION:
            try:
                validate_completion_cycle_storage(connection)
                validate_evidence_ledger_storage(connection)
            except StorageError as exc:
                if exc.code == "database_busy":
                    raise
                raise _unreadable_project_state() from exc
        maintenance = None
        if version >= 10:
            maintenance_missing = {
                item
                for item in required_schema_objects_missing(connection)
                if item == "table:project_maintenance"
                or item
                == "trigger:trg_project_maintenance_enabled_at_immutable"
                or item.startswith("column:project_maintenance.")
            }
            if maintenance_missing:
                raise StorageError(
                    "project_state_unreadable",
                    "project state could not be read safely",
                )
            maintenance = read_project_maintenance(
                connection,
                target.project.project_id,
            )
            if maintenance is None:
                raise StorageError(
                    "project_state_unreadable",
                    "project state could not be read safely",
                )
        if version < SCHEMA_VERSION:
            return SetupStorageState(
                schema_version=version,
                needs_initialize=False,
                needs_migration=True,
                maintenance_enabled=bool(
                    maintenance is not None and maintenance.enabled
                ),
                backup_interval_minutes=(
                    maintenance.backup_interval_minutes
                    if maintenance is not None
                    else None
                ),
                backup_generations=(
                    maintenance.backup_generations
                    if maintenance is not None
                    else None
                ),
            )
        if required_schema_objects_missing(connection):
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        if maintenance is None:
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        if (
            read_viewer_maintenance(
                connection,
                target.project.project_id,
            )
            is None
        ):
            raise StorageError(
                "project_state_unreadable",
                "project state could not be read safely",
            )
        return SetupStorageState(
            schema_version=version,
            needs_initialize=False,
            needs_migration=False,
            maintenance_enabled=maintenance.enabled,
            backup_interval_minutes=maintenance.backup_interval_minutes,
            backup_generations=maintenance.backup_generations,
        )
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="project state could not be read safely",
        ) from exc


def inspect_setup_state(target: DatabaseTarget) -> SetupStorageState:
    validate_operational_journal_state(target.db_path)
    if not target.db_path.exists():
        return SetupStorageState(
            schema_version=None,
            needs_initialize=True,
            needs_migration=False,
            maintenance_enabled=False,
            backup_interval_minutes=None,
            backup_generations=None,
        )
    try:
        with closing(connect_readonly(target.db_path)) as connection:
            return read_setup_state(connection, target)
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="project state could not be read safely",
        ) from exc


def read_doctor_state(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> DoctorStorageState:
    setup_state = read_setup_state(connection, target)
    if setup_state.needs_initialize:
        raise StorageError("setup_required", "project state is not set up")
    if setup_state.needs_migration:
        raise StorageError(
            "migration_required",
            "task database requires setup migration",
        )
    maintenance = read_project_maintenance(
        connection,
        target.project.project_id,
    )
    if maintenance is None:
        raise StorageError(
            "project_state_unreadable",
            "project state could not be read safely",
        )
    viewer = read_viewer_maintenance(
        connection,
        target.project.project_id,
    )
    if viewer is None:
        raise StorageError(
            "project_state_unreadable",
            "project state could not be read safely",
        )
    evidence = read_evidence_projection_state(
        connection,
        project_id=target.project.project_id,
    )
    return DoctorStorageState(
        schema_version=SCHEMA_VERSION,
        project_code="ready" if maintenance.enabled else "setup_required",
        task_counts=count_tasks(connection, target.project.project_id),
        maintenance=maintenance,
        viewer=viewer,
        evidence=evidence,
    )


def _is_exact_empty_completion_history_database(db_path: Path) -> bool:
    """Recognize only the exact unbound schema-construction interval."""
    if db_path.is_symlink() or not db_path.is_file():
        return False
    try:
        with closing(connect_readonly(db_path)) as connection:
            version = current_schema_version(connection)
            if (
                version not in {15, 16, 17, 18, 19, 20, 21}
                or missing_migration_versions(connection, version)
                or schema_objects_inconsistent_with_version(connection, version)
            ):
                return False
            _validate_completion_history_structure(connection)
            object_counts = {
                str(row["type"]): int(row["count"])
                for row in connection.execute(
                    """
                    SELECT type, COUNT(*) AS count
                      FROM sqlite_master
                     WHERE name NOT LIKE 'sqlite_%'
                     GROUP BY type
                    """
                ).fetchall()
            }
            expected_object_counts = (
                {
                    "index": 42,
                    "table": 35,
                    "trigger": 59,
                }
                if version in {20, 21}
                else
                {
                    "index": 32,
                    "table": 31,
                    "trigger": 47,
                }
                if version == 19
                else
                {
                    "index": 28,
                    "table": 26,
                    "trigger": 35,
                }
                if version == 18
                else {
                    "index": 23,
                    "table": 18,
                    "trigger": 16,
                }
                if version == 17
                else {
                    "index": 20,
                    "table": 17,
                    "trigger": 12,
                }
            )
            if object_counts != expected_object_counts:
                return False
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return False
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                return False
            tables = [
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name
                      FROM sqlite_master
                     WHERE type = 'table'
                       AND name NOT LIKE 'sqlite_%'
                     ORDER BY name
                    """
                ).fetchall()
            ]
            for table_name in tables:
                if table_name == "schema_migrations":
                    continue
                if int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table_name}"'
                    ).fetchone()[0]
                ):
                    return False
            return (
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations"
                    ).fetchone()[0]
                )
                == version
            )
    except (OSError, sqlite3.Error, StorageError):
        return False


def _initialize_database_with_identity(
    target: DatabaseTarget,
    *,
    identity_scheme: str,
    binding_reason: str,
    binding_timestamp: str | None = None,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
    fail_stage: str | None = None,
) -> InitResult:
    created = not target.db_path.exists()
    project_identity_preexisting = False
    try:
        validate_operational_journal_state(target.db_path)
        target.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(target.db_path)) as connection:
            with connection:
                if table_exists(connection, "project_meta"):
                    project_rows = connection.execute(
                        "SELECT project_id FROM project_meta ORDER BY project_id"
                    ).fetchall()
                    if len(project_rows) > 1:
                        raise _unreadable_project_state()
                    existing_project_id = (
                        str(project_rows[0]["project_id"])
                        if project_rows
                        else None
                    )
                    project_identity_preexisting = existing_project_id is not None
                    if existing_project_id is not None:
                        if current_schema_version(connection) >= 14:
                            existing_project_id = read_project_binding_state(
                                connection
                            ).project_id
                        else:
                            try:
                                validate_identity_project_id(
                                    existing_project_id,
                                    "legacy_path_v1",
                                )
                            except StorageError as exc:
                                raise _unreadable_project_state() from exc
                    if existing_project_id is not None and (
                        existing_project_id != target.project.project_id
                    ):
                        raise StorageError(
                            "project_mismatch",
                            "task database belongs to a different project",
                        )
                migrations_applied, warnings = apply_migrations(
                    connection,
                    setup_backup=setup_backup,
                    managed_backups=managed_backups,
                )
                ensure_project_meta(
                    connection,
                    target.project,
                    identity_scheme=identity_scheme,
                    binding_reason=binding_reason,
                    timestamp=binding_timestamp,
                    fail_stage=fail_stage,
                )
                ensure_evidence_projection_state_row(
                    connection,
                    project_id=target.project.project_id,
                )
                ensure_project_maintenance_row(
                    connection,
                    target.project.project_id,
                )
                if not project_identity_preexisting:
                    ensure_viewer_maintenance_row(
                        connection,
                        target.project.project_id,
                    )
                version = validate_current_database(connection, target)
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError("internal_error", "could not prepare database path") from exc
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="could not open or initialize database",
        ) from exc
    return InitResult(
        target=target,
        created=created,
        migrations_applied=migrations_applied,
        schema_version=version,
        warnings=warnings,
    )


def initialize_database(
    target: DatabaseTarget,
    *,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
) -> InitResult:
    """Initialize the still-active M17.1 legacy production target."""
    return _initialize_database_with_identity(
        target,
        identity_scheme="legacy_path_v1",
        binding_reason="legacy_migration",
        setup_backup=setup_backup,
        managed_backups=managed_backups,
    )


def migrate_bound_database(
    target: DatabaseTarget,
    *,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
) -> InitResult:
    """Migrate an existing bound database without changing its identity scheme."""

    try:
        with closing(connect(target.db_path)) as connection:
            version = current_schema_version(connection)
            if version >= 14:
                identity_scheme = read_project_binding_state(
                    connection,
                    expected_project_id=target.project.project_id,
                ).identity_scheme
            else:
                identity_scheme = "legacy_path_v1"
    except StorageError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise _unreadable_project_state() from exc
    if identity_scheme == "legacy_path_v1":
        binding_reason = "legacy_migration"
    elif identity_scheme == "uuid_v1":
        binding_reason = "fresh_setup"
    else:
        raise _unreadable_project_state()
    return _initialize_database_with_identity(
        target,
        identity_scheme=identity_scheme,
        binding_reason=binding_reason,
        setup_backup=setup_backup,
        managed_backups=managed_backups,
    )


def initialize_uuid_database(
    target: DatabaseTarget | UnboundDatabaseTarget,
    *,
    project_id_factory: Callable[[], str] | None = None,
    clock: Callable[[], str] | None = None,
    fail_stage: str | None = None,
) -> InitResult:
    """Bind and initialize one explicitly selected, previously unbound fixed DB."""
    if isinstance(target, UnboundDatabaseTarget):
        canonical_repo = target.canonical_repo
        canonical_path_hash = target.canonical_path_hash
        display_name = target.display_name
    elif isinstance(target, DatabaseTarget):
        # Retain the M17.1 explicitly injected repository/test seam.
        canonical_repo = target.project.canonical_repo
        canonical_path_hash = target.project.canonical_path_hash
        display_name = target.project.display_name
    else:
        raise StorageError("internal_error", "fixed database target is invalid")

    db_path = Path(target.db_path)
    sidecar_paths = tuple(
        Path(f"{db_path}{suffix}") for suffix in ("-journal", "-wal", "-shm")
    )
    current_directory_existed = db_path.parent.exists()
    state_directory_existed = db_path.parent.parent.exists()
    if (
        target.explicit_db is not True
        or not db_path.is_absolute()
        or db_path.name.casefold() != "taskgov.sqlite"
        or db_path.parent.name.casefold() != "current"
        or db_path.parent.parent.name.casefold() != "state"
    ):
        raise StorageError("internal_error", "fixed database target is invalid")
    try:
        db_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StorageError(
            "internal_error",
            "fixed database target could not be inspected",
        ) from exc
    else:
        raise StorageError(
            "internal_error",
            "fixed database target is already initialized",
        )
    if any(os.path.lexists(path) for path in sidecar_paths):
        raise StorageError(
            "internal_error",
            "fixed database target is already initialized",
        )

    factory = project_id_factory or (lambda: uuid.uuid4().hex)
    raw_project_id = factory()
    if not isinstance(raw_project_id, str) or not re.fullmatch(
        r"[0-9a-f]{32}",
        raw_project_id,
    ):
        raise StorageError("internal_error", "UUID project identity is invalid")
    project_id = f"tg_project_{raw_project_id}"
    validate_identity_project_id(project_id, "uuid_v1")
    binding_time = validate_utc_timestamp(
        (clock or utc_now)(),
        field="project binding time",
    )
    validate_lower_hex_64(
        canonical_path_hash,
        field="canonical path hash",
    )
    display_name = validate_project_display_name(
        sanitize_project_display_name(display_name)
    )
    uuid_project = ProjectIdentity(
        project_id=project_id,
        canonical_repo=canonical_repo,
        canonical_path_hash=canonical_path_hash,
        display_name=display_name,
    )
    uuid_target = DatabaseTarget(
        project=uuid_project,
        db_path=db_path,
        evidence_root=target.resolved_evidence_root,
        evidence_index=target.resolved_evidence_index,
        evidence_bundles=target.resolved_evidence_bundles,
        evidence_lock=target.resolved_evidence_lock,
        verification_runner_root=target.resolved_verification_runner_root,
        explicit_db=target.explicit_db,
        binding_path_hash=canonical_path_hash,
        binding_generation=1,
        skill_root=target.skill_root,
        backups_path=target.backups_path,
        viewer_path=target.viewer_path,
        canonical_fixed=target.canonical_fixed,
    )
    try:
        return _initialize_database_with_identity(
            uuid_target,
            identity_scheme="uuid_v1",
            binding_reason="fresh_setup",
            binding_timestamp=binding_time,
            fail_stage=fail_stage,
        )
    except Exception:
        if (
            not any(os.path.lexists(path) for path in sidecar_paths)
            and _is_exact_empty_completion_history_database(db_path)
        ):
            try:
                db_path.unlink()
            except OSError as exc:
                raise _unreadable_project_state() from exc
            for directory, existed_before in (
                (db_path.parent, current_directory_existed),
                (db_path.parent.parent, state_directory_existed),
            ):
                if not existed_before:
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
        raise


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)
