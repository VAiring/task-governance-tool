"""Storage path helpers for task-governance-tool."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from task_governance_tool.ordering import LANE_SQL_FUNCTION, canonical_lane


PROJECT_ID_HASH_LENGTH = 12
SCHEMA_VERSION = 17
VIEWER_MIN_SOURCE_SCHEMA_VERSION = 5
STORED_TASK_VERIFICATION_LIMIT_V17 = 500
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
VERIFICATION_EXPECTATION_DIGEST_DOMAIN = (
    b"taskgov-verification-expectation-v1\0"
)
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
MANAGED_BACKUP_FILENAME_PATTERN = re.compile(
    r"^backups/taskgov-backup-v1_\d{8}T\d{6}Z_"
    r"[0-9a-f]{32}_r(?:[1-9]|1[0-9]|20)\.sqlite$"
)
CLEANUP_TEMP_ENTRY_PATTERNS = (
    re.compile(r"^\.taskgov-restore-[a-z0-9_]{8}\.tmp$"),
    re.compile(r"^backups/\.taskgov-backup-[a-z0-9_]{8}\.tmp$"),
    re.compile(r"^viewer/\.task-viewer-[a-z0-9_]{8}\.tmp$"),
)
CLEANUP_FIXED_ENTRIES = {
    "taskgov.sqlite",
    "backups/taskgov-backup.lock",
    "viewer/task-viewer.html",
    "viewer/taskgov-viewer.lock",
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
    return STORED_TASK_VERIFICATION_LIMIT_V17


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
        fetch_stored_task_row,
        validate_stored_task_rows,
    )

    row = fetch_stored_task_row(
        connection,
        "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
        (project_id, task_id),
    )
    if row is not None:
        validate_stored_task_rows(
            [row],
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

    @property
    def resolved_backups_path(self) -> Path:
        return self.backups_path or (self.db_path.parent / "backups")

    @property
    def resolved_viewer_path(self) -> Path:
        return self.viewer_path or (
            self.db_path.parent / "viewer" / "task-viewer.html"
        )


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
    return DatabaseTarget(project=project, db_path=db_path, explicit_db=explicit_db)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _verification_expectation_digest(exact_text: str) -> str:
    if not isinstance(exact_text, str):
        raise TypeError("verification expectation must be text")
    return hashlib.sha256(
        VERIFICATION_EXPECTATION_DIGEST_DOMAIN + exact_text.encode("utf-8")
    ).hexdigest()


def verification_expectation_digest(exact_text: str) -> str:
    """Return the v1 domain-separated digest for exact stored verification text."""

    return _verification_expectation_digest(exact_text)


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
    temporary_counts = [0, 0, 0]
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
    if kind == "table":
        return table_exists(connection, name)
    if kind == "column":
        table_name, column_name = name.split(".", 1)
        return table_exists(connection, table_name) and column_exists(
            connection,
            table_name,
            column_name,
        )
    if kind not in {"index", "trigger"}:
        raise AssertionError(f"unknown schema requirement kind: {kind}")
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?",
            (kind, name),
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
    return [
        marker
        for marker, introduced_version in markers
        if introduced_version > schema_version
        if _schema_requirement_is_present(connection, marker)
    ]


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


def _normalized_schema_sql(statement: str) -> str:
    return " ".join(statement.strip().removesuffix(";").split())


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


def _verification_receipt_trigger_definitions() -> dict[str, str]:
    expected_names = {
        "trg_verification_receipts_no_update",
        "trg_verification_receipts_no_delete",
        "trg_verification_receipts_locked_basis_insert",
        "trg_task_completion_cycles_verification_basis_insert",
    }
    definitions: dict[str, str] = {}
    for statement in verification_receipt_schema_statements():
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
    if not table_exists(connection, "verification_receipts"):
        if current_schema_version(connection) < 17:
            return VerificationReceiptSnapshot(
                total=0,
                same_generation=(),
                exact_current=(),
                recent=(),
            )
        raise invalid_verification_evidence()

    total_row = connection.execute(
        """
        SELECT COUNT(*)
          FROM verification_receipts
         WHERE project_id = ? AND task_id = ?
        """,
        (project_id, task_id),
    ).fetchone()
    if total_row is None:
        raise invalid_verification_evidence()
    total = _verification_receipt_int(total_row[0])
    same_generation_rows = connection.execute(
        """
        SELECT *
          FROM verification_receipts
         WHERE project_id = ?
           AND task_id = ?
           AND target_generation = ?
         ORDER BY created_at DESC,
                  verification_receipt_id DESC
        """,
        (project_id, task_id, target_generation),
    ).fetchall()
    exact_rows = connection.execute(
        """
        SELECT *
          FROM verification_receipts
         WHERE project_id = ?
           AND task_id = ?
           AND contract_revision = ?
           AND verification_expectation_digest = ?
           AND target_kind = ?
           AND target_value = ?
           AND target_base_revision = ?
           AND target_generation = ?
         ORDER BY created_at DESC,
                  verification_receipt_id DESC
        """,
        (
            project_id,
            task_id,
            contract_revision,
            verification_expectation_digest,
            target_kind,
            target_value,
            target_base_revision,
            target_generation,
        ),
    ).fetchall()
    recent_rows = connection.execute(
        """
        SELECT *
          FROM verification_receipts
         WHERE project_id = ? AND task_id = ?
         ORDER BY created_at DESC,
                  verification_receipt_id DESC
         LIMIT ?
        """,
        (project_id, task_id, recent_limit),
    ).fetchall()
    same_generation = tuple(
        _validate_verification_receipt_row(dict(row))
        for row in same_generation_rows
    )
    exact_current = tuple(
        _validate_verification_receipt_row(dict(row))
        for row in exact_rows
    )
    recent = tuple(
        _validate_verification_receipt_row(dict(row))
        for row in recent_rows
    )
    if len(same_generation) > 1 or len(exact_current) > 1:
        raise invalid_verification_evidence()
    return VerificationReceiptSnapshot(
        total=total,
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
            "verification receipt recording requires schema version 17",
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
        }
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
          :verification_receipt_id, :project_id, :task_id,
          :contract_revision, :verification_expectation_digest,
          :command_label, :result, :duration_ms, :scope_coverage,
          :target_kind, :target_value, :target_base_revision,
          :target_generation, :created_at
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
            if (
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
        or _normalized_schema_sql(str(table_row["sql"]))
        != expected_table_sql
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

    expected_triggers = _verification_receipt_trigger_definitions()
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
              qualifying_receipt_id_2{verification_columns}
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
              :qualifying_receipt_id_2{verification_values}
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
            "completion capture requires schema version 17",
        )
    if required_schema_objects_missing(
        connection,
        schema_version=SCHEMA_VERSION,
    ):
        raise completion_history_inconsistent()
    _validate_completion_history_structure(connection)


def insert_native_completion_cycle_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    task_projection: dict[str, Any],
    recorded_at: str,
    verification_expectation_digest: str,
    verification_receipt_id: str | None,
) -> CompletionCycle:
    """Capture one service-validated proposed completion under the Task writer."""

    _require_completion_cycle_writer(connection)
    _require_completion_capture_activation_locked(connection)
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
    if (
        not isinstance(exact_verification, str)
        or not isinstance(verification_expectation_digest, str)
        or LOWER_HEX_64_PATTERN.fullmatch(
            verification_expectation_digest
        )
        is None
        or verification_expectation_digest
        != _verification_expectation_digest(exact_verification)
        or (
            bool(exact_verification.strip())
            and (
                verification_receipt_id is None
                or VERIFICATION_RECEIPT_ID_PATTERN.fullmatch(
                    verification_receipt_id
                )
                is None
            )
        )
        or (
            not exact_verification.strip()
            and verification_receipt_id is not None
        )
    ):
        raise completion_history_inconsistent()
    cycle = CompletionCycle(
        completion_cycle_id=(
            f"tg_completion_cycle_{secrets.token_hex(8)}"
        ),
        project_id=project_id,
        task_id=task_id,
        saved_cycle_ordinal=_next_completion_cycle_ordinal_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
        ),
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
    return _persist_completion_cycle_locked(connection, cycle)


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
            version not in {15, 16, 17}
            or missing_migration_versions(connection, version)
            or required_schema_objects_missing(
                connection,
                schema_version=version,
            )
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
    return CompletionHistory(
        total=total,
        legacy_history_incomplete=incomplete,
        cycles=cycles,
    )


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
                version not in {16, 17}
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
    else:
        apply_verification_receipts_migration(connection)
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


def _validate_current_schema_structure(
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

    if required_schema_objects_missing(connection):
        raise _unreadable_project_state()
    try:
        _validate_completion_history_structure(connection)
    except StorageError as exc:
        raise _unreadable_project_state() from exc
    return version


def _validate_current_project_rows(
    connection: sqlite3.Connection,
    project_id: str,
) -> None:
    if read_project_maintenance(connection, project_id) is None:
        raise _unreadable_project_state()
    if read_viewer_maintenance(connection, project_id) is None:
        raise _unreadable_project_state()


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


def connect_initialized_readonly(
    target: DatabaseTarget,
    *,
    managed_backup_source: bool = False,
) -> sqlite3.Connection:
    """Open one validated current-schema read transaction."""
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
        (
            validate_managed_backup_source_database
            if managed_backup_source
            else validate_current_database
        )(connection, target)
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


def validate_snapshot_database(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
) -> int:
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
    if version >= 15:
        if required_schema_objects_missing(
            connection,
            schema_version=version,
        ):
            raise StorageError(
                "migration_required",
                "database schema is incomplete for Viewer snapshot version 4",
            )
        try:
            _validate_completion_history_structure(connection)
        except StorageError as exc:
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
    return version


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
        from task_governance_tool.tasks import validate_stored_task_rows

        try:
            task_rows = connection.execute(
                "SELECT * FROM tasks ORDER BY task_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise stored_task_sqlite_error(exc) from exc
        validate_stored_task_rows(
            task_rows,
            source_schema_version=version,
            expected_project_id=target.project.project_id,
        )
        if version == SCHEMA_VERSION:
            try:
                validate_completion_cycle_storage(connection)
            except StorageError as exc:
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
    return DoctorStorageState(
        schema_version=SCHEMA_VERSION,
        project_code="ready" if maintenance.enabled else "setup_required",
        task_counts=count_tasks(connection, target.project.project_id),
        maintenance=maintenance,
        viewer=viewer,
    )


def _is_exact_empty_completion_history_database(db_path: Path) -> bool:
    """Recognize only the exact unbound schema-construction interval."""
    if db_path.is_symlink() or not db_path.is_file():
        return False
    try:
        with closing(connect_readonly(db_path)) as connection:
            version = current_schema_version(connection)
            if (
                version not in {15, 16, 17}
                or missing_migration_versions(connection, version)
                or required_schema_objects_missing(
                    connection,
                    schema_version=version,
                )
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
