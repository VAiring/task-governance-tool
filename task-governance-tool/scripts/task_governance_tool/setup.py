"""Direct, deterministic orchestration for the public setup command."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import stat
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from task_governance_tool.backup import (
    ManagedBackupRecoveryCandidate,
    copy_database_snapshot,
    discover_managed_backup_metadata,
    managed_backup_lock,
    publish_setup_backup,
    reconcile_private_migration_repository,
    restore_managed_backup,
    select_managed_backup_for_recovery,
)
from task_governance_tool.project_scope import (
    PREFLIGHT_MESSAGES,
    PROJECT_STATE_MESSAGES,
    ProjectScope,
    ProjectScopeInspection,
    inspect_project_scope,
)
from task_governance_tool.relocation import (
    RelocationContext,
    RelocationTokenClaims,
    RelocationTokenError,
    context_matches,
    decode_relocation_token,
    encode_relocation_token,
    relocation_token_digest,
    relocation_token_expiry,
    require_unexpired,
)
from task_governance_tool.storage import (
    DEFAULT_BACKUP_GENERATIONS,
    DEFAULT_BACKUP_INTERVAL_MINUTES,
    DatabaseTarget,
    MigrationBackupMetadata,
    ProjectPathBinding,
    ProjectIdentity,
    SCHEMA_VERSION,
    SetupStorageState,
    StorageError,
    UnboundDatabaseTarget,
    clear_legacy_cleanup_pending,
    compare_and_swap_project_binding,
    connect_snapshot_readonly,
    configure_project_maintenance,
    initialize_database,
    initialize_uuid_database,
    inspect_setup_state,
    is_sqlite_busy_or_locked,
    read_project_binding_history,
    read_project_binding_state,
    read_viewer_maintenance,
    set_legacy_cleanup_pending,
    utc_now,
    validate_backup_policy,
    validate_completion_cycle_storage,
)
from task_governance_tool.state_paths import (
    StatePathError,
    copy_physical_file_exclusive,
    create_physical_directory_exclusive,
    hash_physical_file,
    inspect_physical_directory,
    path_lexically_exists,
    rename_no_replace,
    unlink_validated_file,
)
from task_governance_tool.state_resolver import (
    ProjectStateResolution,
    resolve_setup_project_state,
    resolve_staged_project_state,
)
from task_governance_tool.state_transition import (
    CleanupInventory,
    CleanupInventoryEntry,
    StateTransitionError,
    STAGE_FILE_MAX_OVERHEAD,
    build_cleanup_inventory,
    create_owned_stage,
    inspect_legacy_cleanup,
    inspect_state_transition_lock,
    inspect_stage_residue,
    remove_stage_residue,
    retire_legacy_inventory,
    state_transition_lock,
    validate_publishable_stage,
)
from task_governance_tool.viewer import (
    ViewerError,
    build_viewer_snapshot,
    inspect_canonical_viewer_status,
    resolve_canonical_viewer_output_target,
)
from task_governance_tool.viewer_maintenance import (
    publish_setup_viewer,
)
from task_governance_tool.viewer_config import (
    ViewerConfigError,
    load_viewer_refresh_interval,
)


SETUP_WRITE_ORDER = (
    "database_restore",
    "legacy_state_publish",
    "database_initialize",
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "project_binding_update",
    "viewer_publish",
    "legacy_state_cleanup",
)

SETUP_ERROR_MESSAGES = {
    "database_busy": PROJECT_STATE_MESSAGES["database_busy"],
    "invalid_backup_policy": "backup policy is outside the supported range",
    "setup_restore_failed": "managed backup could not be restored",
    "setup_backup_failed": "setup backup could not be completed",
    "setup_initialization_failed": "project state could not be initialized",
    "setup_migration_failed": "project state could not be migrated",
    "setup_incomplete": "setup completed only partially; rerun setup",
    "project_relocation_required": PROJECT_STATE_MESSAGES[
        "project_relocation_required"
    ],
    "relocation_token_invalid": "relocation confirmation is invalid",
    "relocation_token_expired": (
        "relocation confirmation has expired; run setup --read-only again"
    ),
    "relocation_token_stale": (
        "project relocation state changed; run setup --read-only again"
    ),
    "relocation_token_used": (
        "relocation confirmation has already been used"
    ),
    "relocation_not_required": "project relocation is not required",
}

@dataclass(frozen=True)
class SetupPlan:
    restore: bool
    legacy_publish: bool
    initialize: bool
    backup: bool
    migrate: bool
    configure: bool
    rebind: bool
    publish_viewer: bool
    legacy_cleanup: bool
    interval_minutes: int
    generations: int
    publication_retention: int

    @property
    def planned_writes(self) -> list[str]:
        selected = {
            "database_restore": self.restore,
            "legacy_state_publish": self.legacy_publish,
            "database_initialize": self.initialize,
            "migration_backup": self.backup,
            "database_migrate": self.migrate,
            "maintenance_configure": self.configure,
            "project_binding_update": self.rebind,
            "viewer_publish": self.publish_viewer,
            "legacy_state_cleanup": self.legacy_cleanup,
        }
        return [name for name in SETUP_WRITE_ORDER if selected[name]]


@dataclass(frozen=True)
class SetupServiceResult:
    ok: bool
    project_id: str | None
    data: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None
    text: str = ""


@dataclass
class _LegacySetupFailure(Exception):
    code: str
    completed_writes: tuple[str, ...] = ()
    target: DatabaseTarget | None = None
    resolution: ProjectStateResolution | None = None


@dataclass
class _RelocationConfirmationFailure(Exception):
    code: str


@dataclass
class _FixedRelocationFailure(Exception):
    code: str
    completed_writes: tuple[str, ...] = ()
    target: DatabaseTarget | None = None
    resolution: ProjectStateResolution | None = None


@dataclass(frozen=True)
class _AcceptedRelocation:
    claims: RelocationTokenClaims
    digest: str
    checked_at: str


def _relocation_data(
    *,
    required: bool = False,
    source_layout: str | None = None,
    identity_scheme: str | None = None,
    binding_generation: int | None = None,
    confirmation_token: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    return {
        "required": bool(required),
        "source_layout": source_layout,
        "identity_scheme": identity_scheme,
        "binding_generation": binding_generation,
        "confirmation_token": confirmation_token,
        "expires_at": expires_at,
    }


def _setup_data(
    *,
    status: str | None = None,
    planned_writes: list[str] | None = None,
    completed_writes: list[str] | None = None,
    schema_from: int | None = None,
    maintenance_enabled: bool | None = None,
    interval_minutes: int | None = None,
    generations: int | None = None,
    viewer_status: str | None = None,
    relocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "planned_writes": list(planned_writes or ()),
        "completed_writes": list(completed_writes or ()),
        "schema_from": schema_from,
        "schema_to": SCHEMA_VERSION,
        "maintenance_enabled": maintenance_enabled,
        "backup_interval_minutes": interval_minutes,
        "backup_generations": generations,
        "viewer_status": viewer_status,
        "relocation": (
            _relocation_data()
            if relocation is None
            else dict(relocation)
        ),
    }


def _preflight_failure(
    inspection: ProjectScopeInspection,
    *,
    code: str,
    message: str,
    project_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> SetupServiceResult:
    return SetupServiceResult(
        ok=False,
        project_id=project_id,
        data=_setup_data() if data is None else data,
        error_code=code,
        error_message=message,
    )


def _canonical_database_is_lexically_absent(target: DatabaseTarget) -> bool:
    """Return true only when no filesystem entry occupies the canonical path."""

    try:
        target.db_path.lstat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise StorageError(
            "project_state_unreadable",
            PROJECT_STATE_MESSAGES["project_state_unreadable"],
        ) from exc
    return False


def _viewer_status(
    skill_root: Path,
    target: DatabaseTarget,
    *,
    setup_state: SetupStorageState,
) -> str:
    try:
        refresh_interval_seconds = load_viewer_refresh_interval(
            skill_root
        )
    except ViewerConfigError:
        return "repair_required"
    current_snapshot: dict[str, Any] | None = None
    maintenance_viewer_succeeded = False
    try:
        with closing(connect_snapshot_readonly(target.db_path)) as connection:
            current_snapshot = build_viewer_snapshot(
                connection,
                target,
            ).snapshot
            if current_snapshot.get("source_schema_version") == SCHEMA_VERSION:
                viewer = read_viewer_maintenance(
                    connection,
                    target.project.project_id,
                )
                maintenance_viewer_succeeded = bool(
                    viewer is not None
                    and viewer.last_outcome_code == "succeeded"
                    and viewer.rendered_generation == viewer.source_generation
                )
    except StorageError as exc:
        expected_incompatibility = (
            exc.code == "db_not_initialized"
            and setup_state.needs_initialize
        ) or (
            exc.code == "migration_required"
            and setup_state.needs_migration
        )
        if not expected_incompatibility:
            raise
        current_snapshot = None
    except ViewerError as exc:
        if exc.code != "migration_required" or not setup_state.needs_migration:
            raise
        current_snapshot = None
    artifact_status = inspect_canonical_viewer_status(
        path=resolve_canonical_viewer_output_target(target).path,
        target=target,
        current_snapshot=current_snapshot,
        compare_snapshot=True,
        verify_template=True,
        refresh_interval_seconds=refresh_interval_seconds,
        skill_root=skill_root,
    )
    if artifact_status == "current" and not maintenance_viewer_succeeded:
        return "repair_required"
    return artifact_status


def _failure_viewer_status(
    skill_root: Path,
    target: DatabaseTarget,
    setup_state: SetupStorageState,
) -> str:
    try:
        return _viewer_status(
            skill_root,
            target,
            setup_state=setup_state,
        )
    except Exception:
        return "repair_required"


def _publish_viewer(skill_root: Path, target: DatabaseTarget) -> None:
    result = publish_setup_viewer(
        target,
        skill_root=skill_root,
    )
    if result.code != "succeeded":
        raise ViewerError(
            "internal_error",
            "canonical Viewer could not be published",
        )


def _revalidate_scope(
    *,
    repo: str,
    repo_explicit: bool,
    script_path: Path,
) -> ProjectScope:
    inspection = inspect_project_scope(
        repo=repo,
        repo_explicit=repo_explicit,
        script_path=script_path,
        include_ignore=False,
    )
    issue = inspection.first_issue()
    if issue is not None or inspection.scope is None:
        if issue is None:
            raise StorageError(
                "unsupported_install_layout",
                PREFLIGHT_MESSAGES["unsupported_install_layout"],
            )
        raise StorageError(issue.code, issue.message)
    return inspection.scope


def _storage_preflight_code(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, StorageError):
        if exc.code in SETUP_ERROR_MESSAGES:
            return exc.code, SETUP_ERROR_MESSAGES[exc.code]
        if exc.code in PROJECT_STATE_MESSAGES:
            return exc.code, PROJECT_STATE_MESSAGES[exc.code]
        if exc.code == "db_not_initialized":
            return "setup_required", PROJECT_STATE_MESSAGES["setup_required"]
    if isinstance(exc, sqlite3.Error) and is_sqlite_busy_or_locked(exc):
        return "database_busy", PROJECT_STATE_MESSAGES["database_busy"]
    return (
        "project_state_unreadable",
        PROJECT_STATE_MESSAGES["project_state_unreadable"],
    )


def _build_plan(
    state: SetupStorageState,
    *,
    restore: bool,
    legacy_publish: bool,
    legacy_cleanup: bool,
    rebind: bool,
    requested_interval: int | None,
    requested_generations: int | None,
    viewer_status: str,
) -> SetupPlan:
    configured = bool(state.maintenance_enabled)
    stored_interval = state.backup_interval_minutes
    stored_generations = state.backup_generations
    if configured and (
        not isinstance(stored_interval, int)
        or not isinstance(stored_generations, int)
    ):
        raise StorageError(
            "project_state_unreadable",
            PROJECT_STATE_MESSAGES["project_state_unreadable"],
        )

    interval = (
        requested_interval
        if requested_interval is not None
        else (
            stored_interval
            if configured
            else DEFAULT_BACKUP_INTERVAL_MINUTES
        )
    )
    generations = (
        requested_generations
        if requested_generations is not None
        else (
            stored_generations
            if configured
            else DEFAULT_BACKUP_GENERATIONS
        )
    )
    configure = not configured or (
        requested_interval is not None and requested_interval != stored_interval
    ) or (
        requested_generations is not None
        and requested_generations != stored_generations
    )
    initialize = bool(state.needs_initialize)
    migrate = bool(state.needs_migration)
    return SetupPlan(
        restore=restore,
        legacy_publish=legacy_publish,
        initialize=initialize,
        backup=migrate,
        migrate=migrate,
        configure=configure,
        rebind=rebind,
        publish_viewer=(
            restore
            or legacy_publish
            or initialize
            or migrate
            or rebind
            or viewer_status != "current"
        ),
        legacy_cleanup=legacy_cleanup or legacy_publish,
        interval_minutes=interval,
        generations=generations,
        publication_retention=(
            stored_generations if configured else generations
        ),
    )


def _empty_setup_state() -> SetupStorageState:
    return SetupStorageState(
        schema_version=None,
        needs_initialize=True,
        needs_migration=False,
        maintenance_enabled=False,
        backup_interval_minutes=None,
        backup_generations=None,
    )


def _unbound_target(resolution: ProjectStateResolution) -> UnboundDatabaseTarget:
    return UnboundDatabaseTarget(
        canonical_repo=resolution.current_root.canonical_repo,
        canonical_path_hash=resolution.current_root.canonical_path_hash,
        display_name=resolution.current_root.display_name,
        db_path=resolution.paths.database,
        explicit_db=True,
        skill_root=resolution.paths.skill_root,
        backups_path=resolution.paths.backups,
        viewer_path=resolution.paths.viewer,
        canonical_fixed=True,
    )


def _artifact_paths_at(
    resolution: ProjectStateResolution,
    database_path: Path,
) -> tuple[Path, Path, bool]:
    canonical_fixed = database_path == resolution.paths.database
    if canonical_fixed:
        return resolution.paths.backups, resolution.paths.viewer, True
    return (
        database_path.parent / "backups",
        database_path.parent / "viewer" / "task-viewer.html",
        False,
    )


def _bound_target_at(
    resolution: ProjectStateResolution,
    database_path: Path,
    *,
    binding_path_hash: str,
    binding_generation: int,
) -> DatabaseTarget:
    stored = resolution.stored_project
    if stored is None:
        raise StorageError(
            "project_state_unreadable",
            PROJECT_STATE_MESSAGES["project_state_unreadable"],
        )
    backups_path, viewer_path, canonical_fixed = _artifact_paths_at(
        resolution,
        database_path,
    )
    return DatabaseTarget(
        project=ProjectIdentity(
            project_id=stored.project_id,
            canonical_repo=resolution.current_root.canonical_repo,
            canonical_path_hash=resolution.current_root.canonical_path_hash,
            display_name=resolution.current_root.display_name,
        ),
        db_path=database_path,
        explicit_db=True,
        binding_path_hash=binding_path_hash,
        binding_generation=binding_generation,
        skill_root=resolution.paths.skill_root,
        backups_path=backups_path,
        viewer_path=viewer_path,
        canonical_fixed=canonical_fixed,
    )


def _stored_target_at(
    resolution: ProjectStateResolution,
    database_path: Path,
) -> DatabaseTarget:
    stored = resolution.stored_project
    if stored is None:
        raise StorageError(
            "project_state_unreadable",
            PROJECT_STATE_MESSAGES["project_state_unreadable"],
        )
    backups_path, viewer_path, canonical_fixed = _artifact_paths_at(
        resolution,
        database_path,
    )
    return DatabaseTarget(
        project=ProjectIdentity(
            project_id=stored.project_id,
            canonical_repo=resolution.current_root.canonical_repo,
            canonical_path_hash=stored.canonical_path_hash,
            display_name=stored.display_name,
        ),
        db_path=database_path,
        explicit_db=True,
        binding_path_hash=stored.canonical_path_hash,
        binding_generation=stored.binding_generation,
        skill_root=resolution.paths.skill_root,
        backups_path=backups_path,
        viewer_path=viewer_path,
        canonical_fixed=canonical_fixed,
    )


def _relocation_projection(
    resolution: ProjectStateResolution | None,
    *,
    confirmation_token: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    stored = resolution.stored_project if resolution is not None else None
    source_layout = (
        resolution.layout
        if resolution is not None
        and resolution.layout in {"fixed_current_v1", "legacy_projects_v1"}
        and stored is not None
        else None
    )
    return _relocation_data(
        required=bool(
            resolution is not None
            and resolution.binding == "relocation_required"
        ),
        source_layout=source_layout,
        identity_scheme=(
            stored.identity_scheme if source_layout is not None else None
        ),
        binding_generation=(
            stored.binding_generation if source_layout is not None else None
        ),
        confirmation_token=confirmation_token,
        expires_at=expires_at,
    )


def _relocation_context(
    resolution: ProjectStateResolution,
) -> RelocationContext:
    stored = resolution.stored_project
    if (
        stored is None
        or resolution.layout
        not in {"fixed_current_v1", "legacy_projects_v1"}
        or resolution.binding != "relocation_required"
    ):
        raise _RelocationConfirmationFailure("relocation_token_stale")
    try:
        return RelocationContext(
            project_id=stored.project_id,
            identity_scheme=stored.identity_scheme,
            binding_generation=stored.binding_generation,
            old_path_hash=stored.canonical_path_hash,
            new_path_hash=resolution.current_root.canonical_path_hash,
            source_layout=resolution.layout,
            source_schema_version=stored.source_schema_version,
        )
    except RelocationTokenError as exc:
        raise _RelocationConfirmationFailure(
            "relocation_token_stale"
        ) from exc


def _binding_history(
    resolution: ProjectStateResolution,
    target: DatabaseTarget | None,
) -> tuple[ProjectPathBinding, ...]:
    if (
        target is None
        or resolution.source_schema_version != SCHEMA_VERSION
        or resolution.project_id is None
    ):
        return ()
    with closing(connect_snapshot_readonly(target.db_path)) as connection:
        return read_project_binding_history(
            connection,
            expected_project_id=resolution.project_id,
        )


def _confirmation_target(
    resolution: ProjectStateResolution,
) -> DatabaseTarget | None:
    if resolution.layout == "fixed_current_v1":
        return resolution.target
    if (
        resolution.layout == "legacy_projects_v1"
        and resolution.legacy_source is not None
    ):
        return resolution.legacy_source.source_target
    return None


def _already_applied_relocation(
    claims: RelocationTokenClaims,
    resolution: ProjectStateResolution,
    history: tuple[ProjectPathBinding, ...],
) -> bool:
    stored = resolution.stored_project
    context = claims.context
    if (
        stored is None
        or resolution.binding != "matching"
        or resolution.layout != "fixed_current_v1"
        or stored.source_schema_version != SCHEMA_VERSION
        or stored.project_id != context.project_id
        or stored.identity_scheme != context.identity_scheme
        or stored.binding_generation != context.binding_generation + 1
        or stored.canonical_path_hash != context.new_path_hash
        or resolution.current_root.canonical_path_hash
        != context.new_path_hash
        or len(history) != stored.binding_generation
        or context.binding_generation < 1
    ):
        return False
    previous = history[context.binding_generation - 1]
    current = history[context.binding_generation]
    return bool(
        previous.binding_generation == context.binding_generation
        and previous.canonical_path_hash == context.old_path_hash
        and current.binding_generation == context.binding_generation + 1
        and current.previous_path_hash == context.old_path_hash
        and current.canonical_path_hash == context.new_path_hash
        and current.reason == "confirmed_relocation"
    )


def _validate_confirmation(
    token: str,
    *,
    resolution: ProjectStateResolution,
    target: DatabaseTarget | None,
    checked_at: str,
) -> _AcceptedRelocation:
    try:
        claims = decode_relocation_token(token, now=checked_at)
        digest = relocation_token_digest(token)
    except RelocationTokenError as exc:
        raise _RelocationConfirmationFailure(exc.code) from exc

    history = _binding_history(resolution, target)
    if any(
        row.confirmation_token_digest == digest
        for row in history
    ):
        raise _RelocationConfirmationFailure("relocation_token_used")

    try:
        require_unexpired(claims, now=checked_at)
    except RelocationTokenError as exc:
        raise _RelocationConfirmationFailure(exc.code) from exc

    if resolution.binding == "relocation_required":
        expected = _relocation_context(resolution)
        if context_matches(claims, expected):
            return _AcceptedRelocation(
                claims=claims,
                digest=digest,
                checked_at=checked_at,
            )
        raise _RelocationConfirmationFailure("relocation_token_stale")
    if _already_applied_relocation(claims, resolution, history):
        raise _RelocationConfirmationFailure("relocation_not_required")
    raise _RelocationConfirmationFailure("relocation_token_stale")


def _raise_if_used_confirmation(
    token: str,
    *,
    resolution: ProjectStateResolution,
    target: DatabaseTarget | None,
    checked_at: str,
) -> None:
    """Expose a successful replay before setup-owned cleanup validation."""

    try:
        decode_relocation_token(token, now=checked_at)
        digest = relocation_token_digest(token)
    except RelocationTokenError:
        return
    history = _binding_history(resolution, target)
    if any(row.confirmation_token_digest == digest for row in history):
        raise _RelocationConfirmationFailure("relocation_token_used")


def _ensure_state_root(scope: ProjectScope, resolution: ProjectStateResolution) -> None:
    state_root = resolution.paths.state_root
    if path_lexically_exists(state_root):
        inspect_physical_directory(state_root, root=scope.skill_root)
        return
    create_physical_directory_exclusive(
        state_root,
        root=scope.skill_root,
    )


def _residue_file_limit(resolution: ProjectStateResolution) -> int:
    candidates: list[Path] = []
    if resolution.legacy_source is not None:
        candidates.append(resolution.legacy_source.source_database)
    if resolution.target is not None:
        candidates.append(resolution.target.db_path)
    for candidate in candidates:
        try:
            details = candidate.lstat()
        except OSError:
            continue
        if stat.S_ISREG(details.st_mode):
            return int(details.st_size) + STAGE_FILE_MAX_OVERHEAD
    return STAGE_FILE_MAX_OVERHEAD


def _inspect_setup_residue(
    resolution: ProjectStateResolution,
):
    if not path_lexically_exists(resolution.paths.state_root):
        return None
    return inspect_stage_residue(
        resolution.paths.state_root,
        max_file_bytes=_residue_file_limit(resolution),
    )


def _legacy_entry_path(
    resolution: ProjectStateResolution,
    relative_name: str,
) -> Path:
    source = resolution.legacy_source
    if source is None:
        raise StateTransitionError()
    return source.root.joinpath(*relative_name.split("/"))


def _build_legacy_cleanup_inventory(
    resolution: ProjectStateResolution,
    *,
    backup_lock_bytes: bytes,
) -> CleanupInventory:
    source = resolution.legacy_source
    if source is None:
        raise StateTransitionError()
    maximum = _residue_file_limit(resolution)
    entries: list[CleanupInventoryEntry] = []
    for relative_name in source.recognized_entries:
        entry_path = _legacy_entry_path(resolution, relative_name)
        if relative_name == "backups/taskgov-backup.lock":
            details = entry_path.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_ISLNK(details.st_mode)
                or getattr(details, "st_file_attributes", 0) & reparse_flag
                or details.st_size not in {0, 1}
            ):
                raise StateTransitionError()
            entry_size = int(details.st_size)
            if len(backup_lock_bytes) != entry_size:
                raise StateTransitionError()
            entry_hash = hashlib.sha256(backup_lock_bytes).hexdigest()
        else:
            observed = hash_physical_file(
                entry_path,
                root=resolution.paths.state_root,
                max_bytes=maximum,
            )
            entry_size = observed.identity.size
            entry_hash = observed.sha256
        entries.append(
            CleanupInventoryEntry(
                name=relative_name,
                size=entry_size,
                sha256=entry_hash,
            )
        )
    return build_cleanup_inventory(entries)


def _copy_legacy_artifacts(
    resolution: ProjectStateResolution,
    *,
    stage_root: Path,
) -> None:
    source = resolution.legacy_source
    if source is None:
        raise StateTransitionError()
    maximum = _residue_file_limit(resolution)
    backup_destination = stage_root / "backups"
    if source.managed_backups:
        create_physical_directory_exclusive(
            backup_destination,
            root=stage_root,
        )
    for backup in source.managed_backups:
        observed = hash_physical_file(
            backup.path,
            root=resolution.paths.state_root,
            max_bytes=maximum,
        )
        copy_physical_file_exclusive(
            observed,
            backup_destination / backup.path.name,
            source_root=resolution.paths.state_root,
            destination_root=stage_root,
            max_bytes=maximum,
        )

    viewer_relative = "viewer/task-viewer.html"
    if viewer_relative in source.recognized_entries:
        viewer_destination = stage_root / "viewer"
        create_physical_directory_exclusive(
            viewer_destination,
            root=stage_root,
        )
        observed = hash_physical_file(
            _legacy_entry_path(resolution, viewer_relative),
            root=resolution.paths.state_root,
            max_bytes=maximum,
        )
        copy_physical_file_exclusive(
            observed,
            viewer_destination / "task-viewer.html",
            source_root=resolution.paths.state_root,
            destination_root=stage_root,
            max_bytes=maximum,
        )


@contextmanager
def _legacy_managed_backup_lock(
    target: DatabaseTarget,
) -> Iterator[bytes]:
    directory = target.resolved_backups_path
    lock_path = directory / "taskgov-backup.lock"
    directory_existed = os.path.lexists(directory)
    lock_existed = os.path.lexists(lock_path)
    try:
        with managed_backup_lock(target) as lock_bytes:
            yield lock_bytes
    finally:
        if not lock_existed:
            try:
                details = lock_path.lstat()
                reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if (
                    stat.S_ISREG(details.st_mode)
                    and not stat.S_ISLNK(details.st_mode)
                    and not (
                        getattr(details, "st_file_attributes", 0)
                        & reparse_flag
                    )
                    and details.st_size in {0, 1}
                ):
                    lock_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        if not directory_existed:
            with suppress(OSError):
                directory.rmdir()


def _pending_cleanup_binding(
    target: DatabaseTarget,
):
    with closing(connect_snapshot_readonly(target.db_path)) as connection:
        return read_project_binding_state(
            connection,
            expected_project_id=target.project.project_id,
        )


def _complete_pending_cleanup(
    resolution: ProjectStateResolution,
    target: DatabaseTarget,
) -> bool:
    binding = _pending_cleanup_binding(target)
    if not binding.legacy_cleanup_pending:
        return False
    if (
        binding.identity_scheme != "legacy_path_v1"
        or binding.legacy_cleanup_inventory is None
        or binding.legacy_cleanup_fingerprint is None
    ):
        raise StateTransitionError()
    result = retire_legacy_inventory(
        resolution.paths.state_root,
        project_id=binding.project_id,
        inventory_text=binding.legacy_cleanup_inventory,
        inventory_fingerprint=binding.legacy_cleanup_fingerprint,
    )
    if not result.filesystem_complete:
        raise StateTransitionError()
    clear_legacy_cleanup_pending(
        target,
        project_id=binding.project_id,
        expected_identity_scheme=binding.identity_scheme,
        expected_generation=binding.binding_generation,
        expected_path_hash=binding.canonical_path_hash,
        expected_inventory_fingerprint=binding.legacy_cleanup_fingerprint,
    )
    return True


def _validate_pending_cleanup_readonly(
    resolution: ProjectStateResolution,
    target: DatabaseTarget,
) -> None:
    stored = resolution.stored_project
    if stored is None or not stored.legacy_cleanup_pending:
        return
    binding = _pending_cleanup_binding(target)
    if (
        binding.project_id != stored.project_id
        or binding.identity_scheme != stored.identity_scheme
        or binding.binding_generation != stored.binding_generation
        or binding.canonical_path_hash != stored.canonical_path_hash
        or binding.legacy_cleanup_inventory is None
        or binding.legacy_cleanup_fingerprint is None
    ):
        raise StateTransitionError()
    inspect_legacy_cleanup(
        resolution.paths.state_root,
        project_id=binding.project_id,
        inventory_text=binding.legacy_cleanup_inventory,
        inventory_fingerprint=binding.legacy_cleanup_fingerprint,
    )


def _same_legacy_observation(
    before: ProjectStateResolution,
    after: ProjectStateResolution,
    *,
    expected_binding: str,
) -> bool:
    before_stored = before.stored_project
    after_stored = after.stored_project
    return bool(
        before.layout == after.layout == "legacy_projects_v1"
        and before.binding == after.binding == expected_binding
        and before.project_id is not None
        and before.project_id == after.project_id
        and before.source_schema_version == after.source_schema_version
        and before_stored is not None
        and after_stored is not None
        and before_stored.identity_scheme == after_stored.identity_scheme
        and before_stored.binding_generation
        == after_stored.binding_generation
        and before_stored.canonical_path_hash
        == after_stored.canonical_path_hash
        and before_stored.binding_lineage == after_stored.binding_lineage
        and before.current_root.canonical_path_hash
        == after.current_root.canonical_path_hash
        and before.legacy_source is not None
        and after.legacy_source is not None
        and before.legacy_source.primary_present
        == after.legacy_source.primary_present
    )


def _same_fixed_relocation_observation(
    before: ProjectStateResolution,
    after: ProjectStateResolution,
) -> bool:
    before_stored = before.stored_project
    after_stored = after.stored_project
    return bool(
        before.layout == after.layout == "fixed_current_v1"
        and before.binding == after.binding == "relocation_required"
        and before.fixed_recovery is None
        and after.fixed_recovery is None
        and before.target is not None
        and after.target is not None
        and before.target.db_path == after.target.db_path
        and before_stored is not None
        and after_stored is not None
        and before_stored.project_id == after_stored.project_id
        and before_stored.identity_scheme == after_stored.identity_scheme
        and before_stored.binding_generation
        == after_stored.binding_generation
        and before_stored.canonical_path_hash
        == after_stored.canonical_path_hash
        and before_stored.source_schema_version
        == after_stored.source_schema_version
        and before_stored.binding_lineage == after_stored.binding_lineage
        and before.current_root.canonical_path_hash
        == after.current_root.canonical_path_hash
    )


def _same_residue_cleanup_authority(
    before: ProjectStateResolution,
    after: ProjectStateResolution,
) -> bool:
    if (
        before.error_code is not None
        or after.error_code is not None
        or before.paths != after.paths
        or before.current_root != after.current_root
        or before.layout != after.layout
        or before.binding != after.binding
    ):
        return False
    if before.layout == "missing":
        return bool(
            before.binding == "unbound"
            and before.stored_project is None
            and after.stored_project is None
            and before.target is None
            and after.target is None
        )
    if before.layout != "fixed_current_v1" or before.binding != "matching":
        return False
    return bool(
        before.stored_project is not None
        and before.stored_project == after.stored_project
        and before.target is not None
        and after.target is not None
        and before.target == after.target
        and before.fixed_recovery == after.fixed_recovery
    )


def _matching_fixed_target(
    scope: ProjectScope,
    *,
    expected_project_id: str,
) -> DatabaseTarget:
    resolution = resolve_setup_project_state(
        skill_root=scope.skill_root,
        repo=scope.canonical_repo,
    )
    if (
        resolution.error_code is not None
        or resolution.layout != "fixed_current_v1"
        or resolution.binding != "matching"
        or resolution.target is None
        or resolution.project_id != expected_project_id
    ):
        raise StorageError(
            "project_state_unreadable",
            PROJECT_STATE_MESSAGES["project_state_unreadable"],
        )
    return resolution.target


def _publish_legacy(
    *,
    scope: ProjectScope,
    initial_resolution: ProjectStateResolution,
    plan: SetupPlan,
    repo: str,
    repo_explicit: bool,
    script_path: Path,
    confirmation_token: str | None,
) -> tuple[DatabaseTarget, list[str]]:
    source = initial_resolution.legacy_source
    stored = initial_resolution.stored_project
    expected_binding = (
        "relocation_required"
        if confirmation_token is not None
        else "matching"
    )
    if (
        source is None
        or stored is None
        or initial_resolution.binding != expected_binding
    ):
        raise _LegacySetupFailure("setup_incomplete")

    completed: list[str] = []
    published_target: DatabaseTarget | None = None
    stage_published = False
    owned_stage_id: str | None = None
    owned_project_id: str | None = None
    owned_inventory_fingerprint: str | None = None
    _ensure_state_root(scope, initial_resolution)
    try:
        with state_transition_lock(initial_resolution.paths.state_root):
            residue = _inspect_setup_residue(initial_resolution)
            refreshed_scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            refreshed = resolve_setup_project_state(
                skill_root=refreshed_scope.skill_root,
                repo=refreshed_scope.canonical_repo,
            )
            if refreshed.error_code is not None:
                raise _LegacySetupFailure(
                    refreshed.error_code,
                    resolution=refreshed,
                )
            accepted_relocation: _AcceptedRelocation | None = None
            if confirmation_token is not None:
                try:
                    accepted_relocation = _validate_confirmation(
                        confirmation_token,
                        resolution=refreshed,
                        target=_confirmation_target(refreshed),
                        checked_at=utc_now(),
                    )
                except _RelocationConfirmationFailure as exc:
                    raise _LegacySetupFailure(
                        exc.code,
                        resolution=refreshed,
                    ) from exc
            if not _same_legacy_observation(
                initial_resolution,
                refreshed,
                expected_binding=expected_binding,
            ):
                raise _LegacySetupFailure(
                    "relocation_token_stale"
                    if confirmation_token is not None
                    else "setup_incomplete",
                    resolution=refreshed,
                )
            if residue is not None:
                remove_stage_residue(
                    initial_resolution.paths.state_root,
                    residue,
                )

            assert refreshed.legacy_source is not None
            with _legacy_managed_backup_lock(
                refreshed.legacy_source.lock_target
            ) as backup_lock_bytes:
                locked = resolve_setup_project_state(
                    skill_root=refreshed_scope.skill_root,
                    repo=refreshed_scope.canonical_repo,
                )
                if locked.error_code is not None:
                    raise _LegacySetupFailure(
                        locked.error_code,
                        resolution=locked,
                    )
                if not _same_legacy_observation(
                    refreshed,
                    locked,
                    expected_binding=expected_binding,
                ):
                    raise _LegacySetupFailure(
                        "relocation_token_stale"
                        if confirmation_token is not None
                        else "setup_incomplete"
                    )
                assert locked.legacy_source is not None
                assert locked.stored_project is not None
                inventory = _build_legacy_cleanup_inventory(
                    locked,
                    backup_lock_bytes=backup_lock_bytes,
                )
                owned_stage_id = secrets.token_hex(16)
                owned_project_id = locked.stored_project.project_id
                owned_inventory_fingerprint = inventory.fingerprint
                owned = create_owned_stage(
                    locked.paths.state_root,
                    project_id=owned_project_id,
                    inventory_fingerprint=inventory.fingerprint,
                    stage_id=owned_stage_id,
                )
                if owned.stage_directory is None:
                    raise StateTransitionError()
                stage_root = owned.stage_directory.path
                stage_target = _stored_target_at(
                    locked,
                    stage_root / "taskgov.sqlite",
                )
                source_target = locked.legacy_source.source_target
                copied_version = copy_database_snapshot(
                    source_path=locked.legacy_source.source_database,
                    source_target=source_target,
                    destination_target=stage_target,
                )
                if copied_version != locked.source_schema_version:
                    raise StateTransitionError()
                _copy_legacy_artifacts(locked, stage_root=stage_root)
                reconcile_private_migration_repository(stage_target)

                backup_metadata: MigrationBackupMetadata | None = None
                if plan.backup:
                    try:
                        backup_metadata = publish_setup_backup(
                            stage_target,
                            plan.publication_retention,
                        )
                    except Exception as exc:
                        raise _LegacySetupFailure("setup_backup_failed") from exc
                if plan.migrate:
                    try:
                        initialize_database(
                            stage_target,
                            setup_backup=(
                                backup_metadata
                                if copied_version < 10
                                else None
                            ),
                            managed_backups=discover_managed_backup_metadata(
                                stage_target
                            ),
                        )
                    except Exception as exc:
                        raise _LegacySetupFailure("setup_migration_failed") from exc
                if plan.configure:
                    try:
                        configure_project_maintenance(
                            stage_target,
                            requested_interval_minutes=plan.interval_minutes,
                            requested_generations=plan.generations,
                        )
                    except Exception as exc:
                        raise _LegacySetupFailure(
                            "setup_incomplete"
                        ) from exc
                if accepted_relocation is not None:
                    try:
                        stage_target = _bound_target_at(
                            locked,
                            stage_root / "taskgov.sqlite",
                            binding_path_hash=(
                                locked.stored_project.canonical_path_hash
                            ),
                            binding_generation=(
                                locked.stored_project.binding_generation
                            ),
                        )
                        compare_and_swap_project_binding(
                            stage_target,
                            project_id=accepted_relocation.claims.context.project_id,
                            identity_scheme=(
                                accepted_relocation.claims.context.identity_scheme
                            ),
                            expected_generation=(
                                accepted_relocation.claims.context.binding_generation
                            ),
                            expected_old_hash=(
                                accepted_relocation.claims.context.old_path_hash
                            ),
                            new_hash=(
                                accepted_relocation.claims.context.new_path_hash
                            ),
                            new_display_name=(
                                locked.current_root.display_name
                            ),
                            reason="confirmed_relocation",
                            confirmation_token_digest=(
                                accepted_relocation.digest
                            ),
                            bound_at=accepted_relocation.checked_at,
                        )
                        stage_target = _bound_target_at(
                            locked,
                            stage_root / "taskgov.sqlite",
                            binding_path_hash=(
                                locked.current_root.canonical_path_hash
                            ),
                            binding_generation=(
                                locked.stored_project.binding_generation + 1
                            ),
                        )
                    except StorageError as exc:
                        raise _LegacySetupFailure(
                            (
                                "relocation_token_stale"
                                if exc.code == "project_binding_stale"
                                else "setup_incomplete"
                            )
                        ) from exc
                binding = _pending_cleanup_binding(stage_target)
                set_legacy_cleanup_pending(
                    stage_target,
                    project_id=binding.project_id,
                    expected_identity_scheme=binding.identity_scheme,
                    expected_generation=binding.binding_generation,
                    expected_path_hash=binding.canonical_path_hash,
                    inventory=inventory.text,
                    fingerprint=inventory.fingerprint,
                )
                try:
                    _publish_viewer(
                        refreshed_scope.skill_root,
                        stage_target,
                    )
                except Exception as exc:
                    raise _LegacySetupFailure("setup_incomplete") from exc
                staged_state = inspect_setup_state(stage_target)
                staged_binding = _pending_cleanup_binding(stage_target)
                if (
                    staged_state.needs_initialize
                    or staged_state.needs_migration
                    or not staged_binding.legacy_cleanup_pending
                    or staged_binding.legacy_cleanup_fingerprint
                    != inventory.fingerprint
                ):
                    raise StateTransitionError()
                residue = inspect_stage_residue(
                    locked.paths.state_root,
                    max_file_bytes=_residue_file_limit(locked),
                    expected_project_id=staged_binding.project_id,
                    expected_inventory_fingerprint=inventory.fingerprint,
                )
                if residue is None or residue.stage_directory is None:
                    raise StateTransitionError()
                validate_publishable_stage(residue)
                staged_resolution = resolve_staged_project_state(
                    stage_root=residue.stage_directory.path,
                    repo=refreshed_scope.canonical_repo,
                )
                expected = locked.stored_project
                observed = staged_resolution.stored_project
                expected_generation = (
                    expected.binding_generation
                    + (1 if accepted_relocation is not None else 0)
                    if expected is not None
                    else None
                )
                expected_hash = (
                    locked.current_root.canonical_path_hash
                    if accepted_relocation is not None
                    else (
                        expected.canonical_path_hash
                        if expected is not None
                        else None
                    )
                )
                expected_lineage = (
                    expected.binding_lineage
                    + (locked.current_root.canonical_path_hash,)
                    if accepted_relocation is not None
                    and expected is not None
                    else (
                        expected.binding_lineage
                        if expected is not None
                        else ()
                    )
                )
                if (
                    staged_resolution.error_code is not None
                    or staged_resolution.layout != "fixed_current_v1"
                    or staged_resolution.binding != "matching"
                    or staged_resolution.fixed_recovery is not None
                    or staged_resolution.target is None
                    or observed is None
                    or expected is None
                    or observed.project_id != expected.project_id
                    or observed.identity_scheme != expected.identity_scheme
                    or observed.binding_generation
                    != expected_generation
                    or observed.canonical_path_hash
                    != expected_hash
                    or observed.binding_lineage != expected_lineage
                    or not observed.legacy_cleanup_pending
                    or _viewer_status(
                        refreshed_scope.skill_root,
                        staged_resolution.target,
                        setup_state=staged_state,
                    )
                    != "current"
                ):
                    raise StateTransitionError()
                rename_no_replace(
                    residue.stage_directory,
                    locked.paths.fixed_root,
                    root=locked.paths.state_root,
                )
                stage_published = True
                published_target = _bound_target_at(
                    locked,
                    locked.paths.database,
                    binding_path_hash=(
                        locked.current_root.canonical_path_hash
                        if accepted_relocation is not None
                        else locked.stored_project.canonical_path_hash
                    ),
                    binding_generation=(
                        locked.stored_project.binding_generation
                        + (1 if accepted_relocation is not None else 0)
                    ),
                )
                source_prefix = (
                    ["database_restore", "legacy_state_publish"]
                    if not locked.legacy_source.primary_present
                    else ["legacy_state_publish"]
                )
                completed.extend(source_prefix)
                if plan.backup:
                    completed.append("migration_backup")
                if plan.migrate:
                    completed.append("database_migrate")
                if plan.configure:
                    completed.append("maintenance_configure")
                if accepted_relocation is not None:
                    completed.append("project_binding_update")
                completed.append("viewer_publish")
                unlink_validated_file(
                    residue.owner_file,
                    root=locked.paths.state_root,
                )

            if published_target is None:
                raise StateTransitionError()
            fixed_resolution = resolve_setup_project_state(
                skill_root=refreshed_scope.skill_root,
                repo=refreshed_scope.canonical_repo,
            )
            if (
                fixed_resolution.layout != "fixed_current_v1"
                or fixed_resolution.binding != "matching"
                or fixed_resolution.target is None
                or fixed_resolution.project_id != initial_resolution.project_id
            ):
                raise StateTransitionError()
            if _complete_pending_cleanup(
                fixed_resolution,
                fixed_resolution.target,
            ):
                completed.append("legacy_state_cleanup")
            return fixed_resolution.target, completed
    except _LegacySetupFailure:
        raise
    except StateTransitionError as exc:
        raise _LegacySetupFailure(
            exc.code,
            tuple(completed),
            published_target,
        ) from exc
    except (StatePathError, StorageError, OSError, sqlite3.Error) as exc:
        raise _LegacySetupFailure(
            "setup_incomplete",
            tuple(completed),
            published_target,
        ) from exc
    finally:
        if (
            not stage_published
            and owned_stage_id is not None
            and owned_project_id is not None
            and owned_inventory_fingerprint is not None
            and path_lexically_exists(initial_resolution.paths.state_root)
        ):
            with suppress(Exception):
                with state_transition_lock(initial_resolution.paths.state_root):
                    residue = inspect_stage_residue(
                        initial_resolution.paths.state_root,
                        max_file_bytes=_residue_file_limit(initial_resolution),
                        expected_project_id=owned_project_id,
                        expected_inventory_fingerprint=(
                            owned_inventory_fingerprint
                        ),
                    )
                    if (
                        residue is not None
                        and residue.owner.stage_id == owned_stage_id
                    ):
                        remove_stage_residue(
                            initial_resolution.paths.state_root,
                            residue,
                        )


def _execute_fixed_relocation(
    *,
    scope: ProjectScope,
    initial_resolution: ProjectStateResolution,
    plan: SetupPlan,
    repo: str,
    repo_explicit: bool,
    script_path: Path,
    confirmation_token: str,
) -> tuple[DatabaseTarget, list[str], str]:
    if (
        initial_resolution.layout != "fixed_current_v1"
        or initial_resolution.binding != "relocation_required"
        or initial_resolution.target is None
        or initial_resolution.stored_project is None
    ):
        raise _FixedRelocationFailure("setup_incomplete")

    completed: list[str] = []
    current_target: DatabaseTarget | None = None
    stage = "preflight"
    _ensure_state_root(scope, initial_resolution)
    try:
        with state_transition_lock(initial_resolution.paths.state_root):
            refreshed_scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            refreshed = resolve_setup_project_state(
                skill_root=refreshed_scope.skill_root,
                repo=refreshed_scope.canonical_repo,
            )
            if refreshed.error_code is not None:
                raise _FixedRelocationFailure(
                    refreshed.error_code,
                    resolution=refreshed,
                )
            try:
                accepted = _validate_confirmation(
                    confirmation_token,
                    resolution=refreshed,
                    target=_confirmation_target(refreshed),
                    checked_at=utc_now(),
                )
            except _RelocationConfirmationFailure as exc:
                raise _FixedRelocationFailure(
                    exc.code,
                    resolution=refreshed,
                ) from exc
            if not _same_fixed_relocation_observation(
                initial_resolution,
                refreshed,
            ):
                raise _FixedRelocationFailure(
                    "relocation_token_stale",
                    resolution=refreshed,
                )
            old_target = _stored_target_at(
                refreshed,
                refreshed.paths.database,
            )

            residue = _inspect_setup_residue(refreshed)
            if residue is not None:
                remove_stage_residue(
                    refreshed.paths.state_root,
                    residue,
                )

            backup_metadata: MigrationBackupMetadata | None = None
            if plan.backup:
                stage = "backup"
                with managed_backup_lock(old_target):
                    backup_metadata = publish_setup_backup(
                        old_target,
                        plan.publication_retention,
                    )
                    completed.append("migration_backup")
                    stage = "migrate"
                    initialize_database(
                        old_target,
                        setup_backup=(
                            backup_metadata
                            if refreshed.source_schema_version is not None
                            and refreshed.source_schema_version < 10
                            else None
                        ),
                        managed_backups=discover_managed_backup_metadata(
                            old_target
                        ),
                    )
                    completed.append("database_migrate")

            if plan.configure:
                stage = "configure"
                configure_project_maintenance(
                    old_target,
                    requested_interval_minutes=plan.interval_minutes,
                    requested_generations=plan.generations,
                )
                completed.append("maintenance_configure")

            stage = "binding"
            current_target = _bound_target_at(
                refreshed,
                refreshed.paths.database,
                binding_path_hash=(
                    refreshed.stored_project.canonical_path_hash
                ),
                binding_generation=(
                    refreshed.stored_project.binding_generation
                ),
            )
            compare_and_swap_project_binding(
                current_target,
                project_id=accepted.claims.context.project_id,
                identity_scheme=accepted.claims.context.identity_scheme,
                expected_generation=(
                    accepted.claims.context.binding_generation
                ),
                expected_old_hash=accepted.claims.context.old_path_hash,
                new_hash=accepted.claims.context.new_path_hash,
                new_display_name=refreshed.current_root.display_name,
                reason="confirmed_relocation",
                confirmation_token_digest=accepted.digest,
                bound_at=accepted.checked_at,
            )
            current_target = _bound_target_at(
                refreshed,
                refreshed.paths.database,
                binding_path_hash=refreshed.current_root.canonical_path_hash,
                binding_generation=(
                    refreshed.stored_project.binding_generation + 1
                ),
            )
            completed.append("project_binding_update")

            stage = "viewer"
            _publish_viewer(refreshed_scope.skill_root, current_target)
            completed.append("viewer_publish")

            if plan.legacy_cleanup:
                stage = "cleanup"
                if _complete_pending_cleanup(refreshed, current_target):
                    completed.append("legacy_state_cleanup")
            return current_target, completed, "published"
    except _FixedRelocationFailure:
        raise
    except StateTransitionError as exc:
        raise _FixedRelocationFailure(
            (
                "database_busy"
                if exc.code == "database_busy"
                else "setup_incomplete"
            ),
            tuple(completed),
            current_target,
        ) from exc
    except StorageError as exc:
        if stage == "binding" and exc.code == "project_binding_stale":
            code = "relocation_token_stale"
        elif exc.code == "database_busy":
            code = "database_busy"
        elif stage == "backup":
            code = "setup_backup_failed"
        elif stage == "migrate":
            code = "setup_migration_failed"
        elif stage == "binding":
            code = "project_state_unreadable"
        else:
            code = "setup_incomplete"
        raise _FixedRelocationFailure(
            code,
            tuple(completed),
            current_target,
        ) from exc
    except Exception as exc:
        code = (
            "setup_backup_failed"
            if stage == "backup"
            else (
                "setup_migration_failed"
                if stage == "migrate"
                else "setup_incomplete"
            )
        )
        raise _FixedRelocationFailure(
            code,
            tuple(completed),
            current_target,
        ) from exc


def run_setup(
    *,
    repo: str,
    repo_explicit: bool,
    script_path: Path,
    read_only: bool,
    backup_interval_minutes: int | None,
    backup_generations: int | None,
    confirmation_token: str | None = None,
) -> SetupServiceResult:
    """Plan or execute setup using only fixed, local stages."""

    inspection = inspect_project_scope(
        repo=repo,
        repo_explicit=repo_explicit,
        script_path=script_path,
    )
    issue = inspection.first_issue()
    if issue is not None:
        return _preflight_failure(
            inspection,
            code=issue.code,
            message=issue.message,
        )
    if inspection.scope is None:
        return _preflight_failure(
            inspection,
            code="unsupported_install_layout",
            message=PREFLIGHT_MESSAGES["unsupported_install_layout"],
        )
    scope = inspection.scope

    try:
        validate_backup_policy(
            interval_minutes=(
                backup_interval_minutes
                if backup_interval_minutes is not None
                else DEFAULT_BACKUP_INTERVAL_MINUTES
            ),
            generations=(
                backup_generations
                if backup_generations is not None
                else DEFAULT_BACKUP_GENERATIONS
            ),
        )
    except StorageError:
        return _preflight_failure(
            inspection,
            code="invalid_backup_policy",
            message=SETUP_ERROR_MESSAGES["invalid_backup_policy"],
        )

    resolution = resolve_setup_project_state(
        skill_root=scope.skill_root,
        repo=scope.canonical_repo,
    )
    if resolution.error_code is not None:
        return _preflight_failure(
            inspection,
            code=resolution.error_code,
            message=PROJECT_STATE_MESSAGES[resolution.error_code],
        )
    try:
        inspect_state_transition_lock(resolution.paths.state_root)
    except StateTransitionError:
        return _preflight_failure(
            inspection,
            code="setup_incomplete",
            message=SETUP_ERROR_MESSAGES["setup_incomplete"],
            project_id=resolution.project_id,
        )
    try:
        residue = _inspect_setup_residue(resolution)
    except StateTransitionError:
        return _preflight_failure(
            inspection,
            code="setup_incomplete",
            message=SETUP_ERROR_MESSAGES["setup_incomplete"],
        )
    target: DatabaseTarget | None = None
    legacy_publish = resolution.layout == "legacy_projects_v1"
    if legacy_publish:
        if resolution.legacy_source is None:
            return _preflight_failure(
                inspection,
                code="project_state_unreadable",
                message=PROJECT_STATE_MESSAGES["project_state_unreadable"],
            )
        target = (
            _stored_target_at(
                resolution,
                resolution.legacy_source.source_database,
            )
            if resolution.binding == "relocation_required"
            else resolution.legacy_source.source_target
        )
    elif resolution.layout == "fixed_current_v1":
        target = (
            _stored_target_at(resolution, resolution.paths.database)
            if resolution.binding == "relocation_required"
            else resolution.target
        )
        if target is None:
            return _preflight_failure(
                inspection,
                code="project_state_unreadable",
                message=PROJECT_STATE_MESSAGES["project_state_unreadable"],
            )

    try:
        state = (
            inspect_setup_state(target)
            if target is not None
            else _empty_setup_state()
        )
        if (
            target is not None
            and state.schema_version == SCHEMA_VERSION
        ):
            with closing(
                connect_snapshot_readonly(target.db_path)
            ) as connection:
                validate_completion_cycle_storage(connection)
    except Exception as exc:
        code, message = _storage_preflight_code(exc)
        return _preflight_failure(
            inspection,
            code=code,
            message=message,
            project_id=resolution.project_id,
        )
    recovery_candidate: ManagedBackupRecoveryCandidate | None = None
    planning_state = state
    if (
        resolution.fixed_recovery is not None
        and target is not None
        and state.needs_initialize
    ):
        try:
            recovery_candidate = select_managed_backup_for_recovery(
                target
            )
            if recovery_candidate is not None:
                planning_state = inspect_setup_state(
                    DatabaseTarget(
                        project=target.project,
                        db_path=recovery_candidate.path,
                        explicit_db=target.explicit_db,
                        binding_path_hash=target.binding_path_hash,
                        binding_generation=target.binding_generation,
                        skill_root=target.skill_root,
                        backups_path=target.backups_path,
                        viewer_path=target.viewer_path,
                        canonical_fixed=False,
                    )
                )
        except Exception as exc:
            code, message = _storage_preflight_code(exc)
            return _preflight_failure(
                inspection,
                code=code,
                message=message,
            )

    if (
        target is not None
        and resolution.layout == "fixed_current_v1"
        and resolution.fixed_recovery is None
    ):
        try:
            observed_viewer_status = _viewer_status(
                scope.skill_root,
                target,
                setup_state=state,
            )
        except Exception as exc:
            code, message = _storage_preflight_code(exc)
            return _preflight_failure(
                inspection,
                code=code,
                message=message,
                project_id=resolution.project_id,
            )
    else:
        observed_viewer_status = "not_present"
    try:
        plan = _build_plan(
            planning_state,
            restore=(
                recovery_candidate is not None
                or (
                    legacy_publish
                    and resolution.legacy_source is not None
                    and not resolution.legacy_source.primary_present
                )
            ),
            legacy_publish=legacy_publish,
            legacy_cleanup=bool(
                resolution.stored_project is not None
                and resolution.stored_project.legacy_cleanup_pending
            ),
            rebind=resolution.binding == "relocation_required",
            requested_interval=backup_interval_minutes,
            requested_generations=backup_generations,
            viewer_status=observed_viewer_status,
        )
    except StorageError as exc:
        return _preflight_failure(
            inspection,
            code=exc.code,
            message=exc.message,
        )

    planned_writes = plan.planned_writes
    schema_from = (
        recovery_candidate.schema_version
        if recovery_candidate is not None
        else state.schema_version
    )
    current_maintenance = bool(state.maintenance_enabled)
    relocation_projection = _relocation_projection(resolution)
    data = _setup_data(
        status=(
            "setup_preview"
            if read_only and planned_writes
            else "already_setup"
        ),
        planned_writes=planned_writes,
        schema_from=schema_from,
        maintenance_enabled=current_maintenance,
        interval_minutes=plan.interval_minutes,
        generations=plan.generations,
        viewer_status=observed_viewer_status,
        relocation=relocation_projection,
    )

    def current_relocation_resolution() -> ProjectStateResolution:
        try:
            refreshed = resolve_setup_project_state(
                skill_root=scope.skill_root,
                repo=scope.canonical_repo,
            )
        except Exception:
            return resolution
        return refreshed if refreshed.error_code is None else resolution

    def relocation_failure(
        code: str,
        *,
        retain_plan: bool = False,
        observed_resolution: ProjectStateResolution | None = None,
    ) -> SetupServiceResult:
        effective_resolution = observed_resolution or resolution
        effective_maintenance = current_maintenance
        effective_viewer_status = observed_viewer_status
        initial_stored = resolution.stored_project
        observed_stored = (
            observed_resolution.stored_project
            if observed_resolution is not None
            else None
        )
        binding_changed = bool(
            observed_resolution is not None
            and (
                observed_resolution.layout != resolution.layout
                or observed_resolution.binding != resolution.binding
                or initial_stored is None
                or observed_stored is None
                or observed_stored.project_id != initial_stored.project_id
                or observed_stored.identity_scheme
                != initial_stored.identity_scheme
                or observed_stored.binding_generation
                != initial_stored.binding_generation
                or observed_stored.canonical_path_hash
                != initial_stored.canonical_path_hash
            )
        )
        if (
            binding_changed
            and observed_resolution is not None
            and observed_resolution.layout == "fixed_current_v1"
            and observed_resolution.target is not None
        ):
            try:
                observed_state = inspect_setup_state(
                    observed_resolution.target
                )
                effective_maintenance = bool(
                    observed_state.maintenance_enabled
                )
                effective_viewer_status = _viewer_status(
                    scope.skill_root,
                    observed_resolution.target,
                    setup_state=observed_state,
                )
            except Exception:
                pass
        return SetupServiceResult(
            ok=False,
            project_id=effective_resolution.project_id,
            data=_setup_data(
                planned_writes=(
                    planned_writes if retain_plan else []
                ),
                schema_from=schema_from,
                maintenance_enabled=effective_maintenance,
                interval_minutes=plan.interval_minutes,
                generations=plan.generations,
                viewer_status=effective_viewer_status,
                relocation=_relocation_projection(effective_resolution),
            ),
            error_code=code,
            error_message=SETUP_ERROR_MESSAGES[code],
        )

    confirmation_resolution = resolution
    confirmation_target = target
    if confirmation_token is not None:
        early_checked_at = utc_now()
        try:
            _raise_if_used_confirmation(
                confirmation_token,
                resolution=confirmation_resolution,
                target=confirmation_target,
                checked_at=early_checked_at,
            )
        except _RelocationConfirmationFailure:
            return relocation_failure(
                "relocation_token_used",
                observed_resolution=current_relocation_resolution(),
            )
        except Exception as exc:
            confirmation_resolution = current_relocation_resolution()
            confirmation_target = _confirmation_target(
                confirmation_resolution
            )
            try:
                _raise_if_used_confirmation(
                    confirmation_token,
                    resolution=confirmation_resolution,
                    target=confirmation_target,
                    checked_at=early_checked_at,
                )
            except _RelocationConfirmationFailure:
                return relocation_failure(
                    "relocation_token_used",
                    observed_resolution=confirmation_resolution,
                )
            except Exception:
                code, message = _storage_preflight_code(exc)
                return _preflight_failure(
                    inspection,
                    code=code,
                    message=message,
                    project_id=resolution.project_id,
                    data=_setup_data(
                        schema_from=schema_from,
                        maintenance_enabled=current_maintenance,
                        interval_minutes=plan.interval_minutes,
                        generations=plan.generations,
                        viewer_status=observed_viewer_status,
                        relocation=relocation_projection,
                    ),
                )

    if target is not None and resolution.layout == "fixed_current_v1":
        try:
            _validate_pending_cleanup_readonly(resolution, target)
        except StateTransitionError:
            return _preflight_failure(
                inspection,
                code="setup_incomplete",
                message=SETUP_ERROR_MESSAGES["setup_incomplete"],
                project_id=resolution.project_id,
                data=_setup_data(
                    planned_writes=planned_writes,
                    schema_from=schema_from,
                    maintenance_enabled=current_maintenance,
                    interval_minutes=plan.interval_minutes,
                    generations=plan.generations,
                    viewer_status=observed_viewer_status,
                    relocation=relocation_projection,
                ),
            )
        except Exception as exc:
            code, message = _storage_preflight_code(exc)
            return _preflight_failure(
                inspection,
                code=code,
                message=message,
                project_id=resolution.project_id,
            )

    accepted_relocation: _AcceptedRelocation | None = None
    if confirmation_token is not None:
        confirmation_resolution = current_relocation_resolution()
        confirmation_target = _confirmation_target(
            confirmation_resolution
        )
        checked_at = utc_now()
        first_observation_error: Exception | None = None
        for attempt in range(2):
            try:
                accepted_relocation = _validate_confirmation(
                    confirmation_token,
                    resolution=confirmation_resolution,
                    target=confirmation_target,
                    checked_at=checked_at,
                )
                break
            except _RelocationConfirmationFailure as exc:
                return relocation_failure(
                    exc.code,
                    observed_resolution=current_relocation_resolution(),
                )
            except Exception as exc:
                if attempt == 0:
                    first_observation_error = exc
                    confirmation_resolution = (
                        current_relocation_resolution()
                    )
                    confirmation_target = _confirmation_target(
                        confirmation_resolution
                    )
                    continue
                observed_error = first_observation_error or exc
                code, message = _storage_preflight_code(observed_error)
                return _preflight_failure(
                    inspection,
                    code=code,
                    message=message,
                    project_id=resolution.project_id,
                    data=_setup_data(
                        schema_from=schema_from,
                        maintenance_enabled=current_maintenance,
                        interval_minutes=plan.interval_minutes,
                        generations=plan.generations,
                        viewer_status=observed_viewer_status,
                        relocation=relocation_projection,
                    ),
                )
    elif resolution.binding == "relocation_required":
        if not read_only:
            return relocation_failure(
                "project_relocation_required",
                retain_plan=True,
            )
        try:
            issued_at = utc_now()
            preview_token = encode_relocation_token(
                _relocation_context(resolution),
                issued_at=issued_at,
            )
            preview_expires_at = relocation_token_expiry(issued_at)
        except (RelocationTokenError, _RelocationConfirmationFailure):
            return _preflight_failure(
                inspection,
                code="project_state_unreadable",
                message=PROJECT_STATE_MESSAGES["project_state_unreadable"],
                project_id=resolution.project_id,
            )
        preview_relocation = _relocation_projection(
            resolution,
            confirmation_token=preview_token,
            expires_at=preview_expires_at,
        )
        return SetupServiceResult(
            ok=True,
            project_id=resolution.project_id,
            data=_setup_data(
                status="relocation_preview",
                planned_writes=planned_writes,
                schema_from=schema_from,
                maintenance_enabled=current_maintenance,
                interval_minutes=plan.interval_minutes,
                generations=plan.generations,
                viewer_status=observed_viewer_status,
                relocation=preview_relocation,
            ),
            text="Relocation preview complete",
        )

    if (
        not read_only
        and confirmation_token is None
        and residue is not None
        and not legacy_publish
    ):
        try:
            _ensure_state_root(scope, resolution)
            with state_transition_lock(resolution.paths.state_root):
                current_resolution = resolve_setup_project_state(
                    skill_root=scope.skill_root,
                    repo=scope.canonical_repo,
                )
                if not _same_residue_cleanup_authority(
                    resolution,
                    current_resolution,
                ):
                    raise StateTransitionError()
                current_residue = _inspect_setup_residue(
                    current_resolution
                )
                if current_residue is not None:
                    remove_stage_residue(
                        current_resolution.paths.state_root,
                        current_residue,
                    )
        except StateTransitionError as exc:
            return _preflight_failure(
                inspection,
                code=(
                    "database_busy"
                    if exc.code == "database_busy"
                    else "setup_incomplete"
                ),
                message=(
                    SETUP_ERROR_MESSAGES["database_busy"]
                    if exc.code == "database_busy"
                    else SETUP_ERROR_MESSAGES["setup_incomplete"]
                ),
                project_id=resolution.project_id,
            )
        except Exception:
            return _preflight_failure(
                inspection,
                code="setup_incomplete",
                message=SETUP_ERROR_MESSAGES["setup_incomplete"],
                project_id=resolution.project_id,
            )
    if read_only or not planned_writes:
        text = (
            "Setup preview complete"
            if read_only and planned_writes
            else "Project is already set up"
        )
        return SetupServiceResult(
            ok=True,
            project_id=resolution.project_id,
            data=data,
            text=text,
        )

    completed: list[str] = []
    backup_metadata: MigrationBackupMetadata | None = None
    reported_interval = plan.interval_minutes
    reported_generations = plan.generations

    def failure_after_write(code: str) -> SetupServiceResult:
        failure_target = target
        failure_relocation = relocation_projection
        failure_maintenance = (
            current_maintenance
            or "maintenance_configure" in completed
        )
        failure_viewer_status = (
            "published"
            if "viewer_publish" in completed
            else (
                _failure_viewer_status(
                    scope.skill_root,
                    failure_target,
                    state,
                )
                if failure_target is not None
                and failure_target.db_path.exists()
                else observed_viewer_status
            )
        )
        if (
            resolution.stored_project is not None
            and (
                "legacy_state_publish" in completed
                or "project_binding_update" in completed
            )
        ):
            failure_relocation = _relocation_data(
                required=False,
                source_layout="fixed_current_v1",
                identity_scheme=(
                    resolution.stored_project.identity_scheme
                ),
                binding_generation=(
                    resolution.stored_project.binding_generation
                    + (
                        1
                        if "project_binding_update" in completed
                        else 0
                    )
                ),
            )
        return SetupServiceResult(
            ok=False,
            project_id=(
                failure_target.project.project_id
                if failure_target is not None
                else resolution.project_id
            ),
            data=_setup_data(
                planned_writes=planned_writes,
                completed_writes=completed,
                schema_from=schema_from,
                maintenance_enabled=failure_maintenance,
                interval_minutes=reported_interval,
                generations=reported_generations,
                viewer_status=failure_viewer_status,
                relocation=failure_relocation,
            ),
            error_code=code,
            error_message=(
                SETUP_ERROR_MESSAGES.get(code)
                or PROJECT_STATE_MESSAGES.get(
                    code,
                    SETUP_ERROR_MESSAGES["setup_incomplete"],
                )
            ),
        )

    if (
        accepted_relocation is not None
        and resolution.layout == "fixed_current_v1"
    ):
        try:
            target, completed, final_viewer_status = (
                _execute_fixed_relocation(
                    scope=scope,
                    initial_resolution=resolution,
                    plan=plan,
                    repo=repo,
                    repo_explicit=repo_explicit,
                    script_path=script_path,
                    confirmation_token=confirmation_token or "",
                )
            )
        except _FixedRelocationFailure as exc:
            completed = list(exc.completed_writes)
            if exc.target is not None:
                target = exc.target
            if not completed and exc.code in PROJECT_STATE_MESSAGES:
                return _preflight_failure(
                    inspection,
                    code=exc.code,
                    message=PROJECT_STATE_MESSAGES[exc.code],
                    project_id=resolution.project_id,
                )
            if exc.code.startswith("relocation_") and not completed:
                return relocation_failure(
                    exc.code,
                    observed_resolution=exc.resolution,
                )
            return failure_after_write(
                (
                    "setup_incomplete"
                    if exc.code.startswith("relocation_")
                    else exc.code
                )
            )
        final_state = inspect_setup_state(target)
        assert resolution.stored_project is not None
        return SetupServiceResult(
            ok=True,
            project_id=target.project.project_id,
            data=_setup_data(
                status="setup_complete",
                planned_writes=planned_writes,
                completed_writes=completed,
                schema_from=schema_from,
                maintenance_enabled=final_state.maintenance_enabled,
                interval_minutes=plan.interval_minutes,
                generations=plan.generations,
                viewer_status=final_viewer_status,
                relocation=_relocation_data(
                    required=False,
                    source_layout="fixed_current_v1",
                    identity_scheme=(
                        resolution.stored_project.identity_scheme
                    ),
                    binding_generation=(
                        resolution.stored_project.binding_generation + 1
                    ),
                ),
            ),
            text="Project setup complete",
        )

    if plan.legacy_publish:
        try:
            target, completed = _publish_legacy(
                scope=scope,
                initial_resolution=resolution,
                plan=plan,
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
                confirmation_token=(
                    confirmation_token
                    if accepted_relocation is not None
                    else None
                ),
            )
        except _LegacySetupFailure as exc:
            completed = list(exc.completed_writes)
            if exc.target is not None:
                target = exc.target
            if not completed and exc.code in PROJECT_STATE_MESSAGES:
                return _preflight_failure(
                    inspection,
                    code=exc.code,
                    message=PROJECT_STATE_MESSAGES[exc.code],
                    project_id=resolution.project_id,
                )
            if exc.code.startswith("relocation_") and not completed:
                return relocation_failure(
                    exc.code,
                    observed_resolution=exc.resolution,
                )
            code = (
                "setup_incomplete"
                if exc.code.startswith("relocation_")
                else (
                    exc.code
                    if exc.code in SETUP_ERROR_MESSAGES
                    else "setup_incomplete"
                )
            )
            return failure_after_write(code)
        final_state = inspect_setup_state(target)
        return SetupServiceResult(
            ok=True,
            project_id=target.project.project_id,
            data=_setup_data(
                status="setup_complete",
                planned_writes=planned_writes,
                completed_writes=completed,
                schema_from=schema_from,
                maintenance_enabled=final_state.maintenance_enabled,
                interval_minutes=plan.interval_minutes,
                generations=plan.generations,
                viewer_status="published",
                relocation=(
                    _relocation_data(
                        required=False,
                        source_layout="fixed_current_v1",
                        identity_scheme=(
                            resolution.stored_project.identity_scheme
                            if resolution.stored_project is not None
                            else None
                        ),
                        binding_generation=(
                            resolution.stored_project.binding_generation + 1
                            if accepted_relocation is not None
                            and resolution.stored_project is not None
                            else (
                                resolution.stored_project.binding_generation
                                if resolution.stored_project is not None
                                else None
                            )
                        ),
                    )
                    if resolution.stored_project is not None
                    else _relocation_data()
                ),
            ),
            text="Project setup complete",
        )

    if plan.initialize:
        current_created = False
        fresh_stage = "initialize"
        try:
            _ensure_state_root(scope, resolution)
            with state_transition_lock(resolution.paths.state_root):
                refreshed_scope = _revalidate_scope(
                    repo=repo,
                    repo_explicit=repo_explicit,
                    script_path=script_path,
                )
                refreshed = resolve_setup_project_state(
                    skill_root=refreshed_scope.skill_root,
                    repo=refreshed_scope.canonical_repo,
                )
                if refreshed.fixed_recovery is not None:
                    fresh_stage = "restore"
                    raise StorageError(
                        "setup_restore_failed",
                        SETUP_ERROR_MESSAGES["setup_restore_failed"],
                    )
                if (
                    refreshed.layout != "missing"
                    or refreshed.error_code is not None
                    or refreshed.binding != "unbound"
                ):
                    raise StorageError(
                        "setup_initialization_failed",
                        SETUP_ERROR_MESSAGES["setup_initialization_failed"],
                    )
                current = create_physical_directory_exclusive(
                    refreshed.paths.fixed_root,
                    root=refreshed.paths.state_root,
                )
                current_created = True
                initialized = initialize_uuid_database(
                    _unbound_target(refreshed)
                )
                target = initialized.target
                completed.append("database_initialize")
                if plan.configure:
                    fresh_stage = "configure"
                    reported_interval, reported_generations = (
                        configure_project_maintenance(
                            target,
                            requested_interval_minutes=backup_interval_minutes,
                            requested_generations=backup_generations,
                        )
                    )
                    current_maintenance = True
                    completed.append("maintenance_configure")
                if plan.publish_viewer:
                    fresh_stage = "viewer"
                    _publish_viewer(refreshed_scope.skill_root, target)
                    completed.append("viewer_publish")
                inspect_physical_directory(
                    current.path,
                    root=refreshed.paths.state_root,
                )
        except StateTransitionError as exc:
            if exc.code == "database_busy":
                return failure_after_write("database_busy")
            if (
                current_created
                and target is None
                and not path_lexically_exists(resolution.paths.database)
            ):
                with suppress(OSError):
                    resolution.paths.fixed_root.rmdir()
            return failure_after_write(
                (
                    "setup_initialization_failed"
                    if fresh_stage == "initialize"
                    else (
                        "setup_restore_failed"
                        if fresh_stage == "restore"
                        else "setup_incomplete"
                    )
                )
            )
        except Exception:
            if (
                current_created
                and target is None
                and not path_lexically_exists(resolution.paths.database)
            ):
                with suppress(OSError):
                    resolution.paths.fixed_root.rmdir()
            return failure_after_write(
                (
                    "setup_initialization_failed"
                    if fresh_stage == "initialize"
                    else (
                        "setup_restore_failed"
                        if fresh_stage == "restore"
                        else "setup_incomplete"
                    )
                )
            )
        return SetupServiceResult(
            ok=True,
            project_id=target.project.project_id if target is not None else None,
            data=_setup_data(
                status="setup_complete",
                planned_writes=planned_writes,
                completed_writes=completed,
                schema_from=schema_from,
                maintenance_enabled=current_maintenance,
                interval_minutes=reported_interval,
                generations=reported_generations,
                viewer_status="published",
                relocation=relocation_projection,
            ),
            text="Project setup complete",
        )

    if plan.restore:
        stage = "restore"
        try:
            scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            if resolution.project_id is None:
                raise StorageError(
                    "setup_restore_failed",
                    SETUP_ERROR_MESSAGES["setup_restore_failed"],
                )
            target = _matching_fixed_target(
                scope,
                expected_project_id=resolution.project_id,
            )
            with managed_backup_lock(target):
                locked_resolution = resolve_setup_project_state(
                    skill_root=scope.skill_root,
                    repo=scope.canonical_repo,
                )
                if (
                    locked_resolution.error_code is not None
                    or locked_resolution.layout != "fixed_current_v1"
                    or locked_resolution.binding != "matching"
                    or locked_resolution.target is None
                    or locked_resolution.project_id != resolution.project_id
                    or resolution.fixed_recovery is None
                    or locked_resolution.fixed_recovery
                    != resolution.fixed_recovery
                ):
                    raise StorageError(
                        "setup_restore_failed",
                        SETUP_ERROR_MESSAGES["setup_restore_failed"],
                    )
                target = locked_resolution.target
                if (
                    not _canonical_database_is_lexically_absent(target)
                    or not inspect_setup_state(target).needs_initialize
                ):
                    raise StorageError(
                        "setup_restore_failed",
                        SETUP_ERROR_MESSAGES["setup_restore_failed"],
                    )
                current_candidate = select_managed_backup_for_recovery(
                    target
                )
                if (
                    current_candidate is None
                    or recovery_candidate is None
                    or current_candidate != recovery_candidate
                ):
                    raise StorageError(
                        "setup_restore_failed",
                        SETUP_ERROR_MESSAGES["setup_restore_failed"],
                    )
                restored_version = restore_managed_backup(
                    target,
                    current_candidate,
                )
                if restored_version != schema_from:
                    raise StorageError(
                        "setup_restore_failed",
                        SETUP_ERROR_MESSAGES["setup_restore_failed"],
                    )
                restored_state = inspect_setup_state(target)
                current_maintenance = bool(
                    restored_state.maintenance_enabled
                )
                completed.append("database_restore")
                if plan.backup:
                    stage = "backup"
                    backup_metadata = publish_setup_backup(
                        target,
                        plan.publication_retention,
                    )
                    completed.append("migration_backup")
                    stage = "migrate"
                    scope = _revalidate_scope(
                        repo=repo,
                        repo_explicit=repo_explicit,
                        script_path=script_path,
                    )
                    target = _matching_fixed_target(
                        scope,
                        expected_project_id=resolution.project_id,
                    )
                    initialize_database(
                        target,
                        setup_backup=(
                            backup_metadata
                            if schema_from is not None and schema_from < 10
                            else None
                        ),
                        managed_backups=discover_managed_backup_metadata(
                            target
                        ),
                    )
                    completed.append("database_migrate")
        except Exception:
            if stage == "restore":
                return failure_after_write("setup_restore_failed")
            if stage == "backup":
                return failure_after_write("setup_backup_failed")
            return failure_after_write("setup_migration_failed")

    if plan.backup and not plan.restore:
        stage_error: str | None = None
        try:
            scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            if resolution.project_id is None:
                raise StorageError(
                    "setup_migration_failed",
                    SETUP_ERROR_MESSAGES["setup_migration_failed"],
                )
            target = _matching_fixed_target(
                scope,
                expected_project_id=resolution.project_id,
            )
            with managed_backup_lock(target):
                backup_metadata = publish_setup_backup(
                    target,
                    plan.publication_retention,
                )
                completed.append("migration_backup")
                scope = _revalidate_scope(
                    repo=repo,
                    repo_explicit=repo_explicit,
                    script_path=script_path,
                )
                target = _matching_fixed_target(
                    scope,
                    expected_project_id=resolution.project_id,
                )
                initialize_database(
                    target,
                    setup_backup=(
                        backup_metadata
                        if schema_from is not None and schema_from < 10
                        else None
                    ),
                    managed_backups=discover_managed_backup_metadata(
                        target
                    ),
                )
                completed.append("database_migrate")
        except Exception:
            stage_error = (
                "setup_backup_failed"
                if backup_metadata is None
                else "setup_migration_failed"
            )
        if stage_error is not None:
            return failure_after_write(stage_error)

    if plan.configure:
        try:
            scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            if resolution.project_id is None:
                raise StorageError(
                    "setup_incomplete",
                    SETUP_ERROR_MESSAGES["setup_incomplete"],
                )
            target = _matching_fixed_target(
                scope,
                expected_project_id=resolution.project_id,
            )
            reported_interval, reported_generations = (
                configure_project_maintenance(
                    target,
                    requested_interval_minutes=backup_interval_minutes,
                    requested_generations=backup_generations,
                )
            )
            current_maintenance = True
            completed.append("maintenance_configure")
        except Exception:
            return failure_after_write("setup_incomplete")

    final_viewer_status = observed_viewer_status
    if plan.publish_viewer:
        try:
            scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            if resolution.project_id is None:
                raise StorageError(
                    "setup_incomplete",
                    SETUP_ERROR_MESSAGES["setup_incomplete"],
                )
            target = _matching_fixed_target(
                scope,
                expected_project_id=resolution.project_id,
            )
            _publish_viewer(scope.skill_root, target)
            final_viewer_status = "published"
            completed.append("viewer_publish")
        except Exception:
            return failure_after_write("setup_incomplete")

    if plan.legacy_cleanup:
        try:
            scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            if resolution.project_id is None:
                raise StateTransitionError()
            target = _matching_fixed_target(
                scope,
                expected_project_id=resolution.project_id,
            )
            cleanup_resolution = resolve_setup_project_state(
                skill_root=scope.skill_root,
                repo=scope.canonical_repo,
            )
            _ensure_state_root(scope, cleanup_resolution)
            with state_transition_lock(cleanup_resolution.paths.state_root):
                current_resolution = resolve_setup_project_state(
                    skill_root=scope.skill_root,
                    repo=scope.canonical_repo,
                )
                if (
                    current_resolution.error_code is not None
                    or current_resolution.layout != "fixed_current_v1"
                    or current_resolution.binding != "matching"
                    or current_resolution.fixed_recovery is not None
                    or current_resolution.target is None
                    or current_resolution.project_id != resolution.project_id
                ):
                    raise StateTransitionError()
                if _complete_pending_cleanup(
                    current_resolution,
                    current_resolution.target,
                ):
                    completed.append("legacy_state_cleanup")
                target = current_resolution.target
        except StateTransitionError as exc:
            return failure_after_write(
                "database_busy"
                if exc.code == "database_busy"
                else "setup_incomplete"
            )
        except Exception:
            return failure_after_write("setup_incomplete")

    if target is None:
        return failure_after_write("setup_incomplete")
    return SetupServiceResult(
        ok=True,
        project_id=target.project.project_id,
        data=_setup_data(
            status="setup_complete",
            planned_writes=planned_writes,
            completed_writes=completed,
            schema_from=schema_from,
            maintenance_enabled=current_maintenance,
            interval_minutes=reported_interval,
            generations=reported_generations,
            viewer_status=final_viewer_status,
            relocation=relocation_projection,
        ),
        text="Project setup complete",
    )
