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
from task_governance_tool.storage import (
    DEFAULT_BACKUP_GENERATIONS,
    DEFAULT_BACKUP_INTERVAL_MINUTES,
    DatabaseTarget,
    MigrationBackupMetadata,
    ProjectIdentity,
    SCHEMA_VERSION,
    SetupStorageState,
    StorageError,
    UnboundDatabaseTarget,
    clear_legacy_cleanup_pending,
    connect_snapshot_readonly,
    configure_project_maintenance,
    initialize_database,
    initialize_uuid_database,
    inspect_setup_state,
    is_sqlite_busy_or_locked,
    read_project_binding_state,
    read_viewer_maintenance,
    set_legacy_cleanup_pending,
    validate_backup_policy,
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
}

@dataclass(frozen=True)
class SetupPlan:
    restore: bool
    legacy_publish: bool
    initialize: bool
    backup: bool
    migrate: bool
    configure: bool
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
        publish_viewer=(
            restore
            or legacy_publish
            or initialize
            or migrate
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
    )


def _bound_target_at(
    resolution: ProjectStateResolution,
    database_path: Path,
) -> DatabaseTarget:
    stored = resolution.stored_project
    if stored is None:
        raise StorageError(
            "project_state_unreadable",
            PROJECT_STATE_MESSAGES["project_state_unreadable"],
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
    )


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
    directory = target.db_path.parent / "backups"
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
) -> bool:
    return bool(
        before.layout == after.layout == "legacy_projects_v1"
        and before.binding == after.binding == "matching"
        and before.project_id is not None
        and before.project_id == after.project_id
        and before.source_schema_version == after.source_schema_version
        and before.legacy_source is not None
        and after.legacy_source is not None
        and before.legacy_source.primary_present
        == after.legacy_source.primary_present
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


def _publish_same_binding_legacy(
    *,
    scope: ProjectScope,
    initial_resolution: ProjectStateResolution,
    plan: SetupPlan,
    repo: str,
    repo_explicit: bool,
    script_path: Path,
) -> tuple[DatabaseTarget, list[str]]:
    source = initial_resolution.legacy_source
    stored = initial_resolution.stored_project
    if (
        source is None
        or stored is None
        or initial_resolution.binding != "matching"
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
            if residue is not None:
                remove_stage_residue(
                    initial_resolution.paths.state_root,
                    residue,
                )
            refreshed_scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            refreshed = resolve_setup_project_state(
                skill_root=refreshed_scope.skill_root,
                repo=refreshed_scope.canonical_repo,
            )
            if not _same_legacy_observation(initial_resolution, refreshed):
                raise StateTransitionError()

            assert refreshed.legacy_source is not None
            with _legacy_managed_backup_lock(
                refreshed.legacy_source.lock_target
            ) as backup_lock_bytes:
                locked = resolve_setup_project_state(
                    skill_root=refreshed_scope.skill_root,
                    repo=refreshed_scope.canonical_repo,
                )
                if not _same_legacy_observation(refreshed, locked):
                    raise StateTransitionError()
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
                stage_target = _bound_target_at(
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
                    != expected.binding_generation
                    or observed.canonical_path_hash
                    != expected.canonical_path_hash
                    or observed.binding_lineage != expected.binding_lineage
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


def run_setup(
    *,
    repo: str,
    repo_explicit: bool,
    script_path: Path,
    read_only: bool,
    backup_interval_minutes: int | None,
    backup_generations: int | None,
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
    if resolution.binding == "relocation_required":
        return _preflight_failure(
            inspection,
            code="project_mismatch",
            message=PROJECT_STATE_MESSAGES["project_mismatch"],
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
        target = resolution.legacy_source.source_target
    elif resolution.layout == "fixed_current_v1":
        target = resolution.target
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
    if not read_only and residue is not None and not legacy_publish:
        try:
            _ensure_state_root(scope, resolution)
            with state_transition_lock(resolution.paths.state_root):
                current_residue = _inspect_setup_residue(resolution)
                if current_residue is not None:
                    remove_stage_residue(
                        resolution.paths.state_root,
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
                maintenance_enabled=current_maintenance,
                interval_minutes=reported_interval,
                generations=reported_generations,
                viewer_status=(
                    _failure_viewer_status(
                        scope.skill_root,
                        failure_target,
                        state,
                    )
                    if failure_target is not None
                    and failure_target.db_path.exists()
                    else observed_viewer_status
                ),
            ),
            error_code=code,
            error_message=SETUP_ERROR_MESSAGES[code],
        )

    if plan.legacy_publish:
        try:
            target, completed = _publish_same_binding_legacy(
                scope=scope,
                initial_resolution=resolution,
                plan=plan,
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
        except _LegacySetupFailure as exc:
            completed = list(exc.completed_writes)
            if exc.target is not None:
                target = exc.target
            code = (
                exc.code
                if exc.code in SETUP_ERROR_MESSAGES
                else "setup_incomplete"
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
            _ensure_state_root(
                scope,
                resolve_setup_project_state(
                    skill_root=scope.skill_root,
                    repo=scope.canonical_repo,
                ),
            )
            with state_transition_lock(target.db_path.parent.parent):
                current_resolution = resolve_setup_project_state(
                    skill_root=scope.skill_root,
                    repo=scope.canonical_repo,
                )
                if (
                    current_resolution.target is None
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
        ),
        text="Project setup complete",
    )
