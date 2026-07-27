"""Direct, deterministic orchestration for the public setup command."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool.backup import (
    ManagedBackupRecoveryCandidate,
    discover_managed_backup_metadata,
    managed_backup_lock,
    publish_setup_backup,
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
    SCHEMA_VERSION,
    SetupStorageState,
    StorageError,
    connect_snapshot_readonly,
    configure_project_maintenance,
    initialize_database,
    inspect_setup_state,
    is_sqlite_busy_or_locked,
    read_viewer_maintenance,
    validate_backup_policy,
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


SETUP_WRITE_ORDER = (
    "database_restore",
    "database_initialize",
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "viewer_publish",
)

SETUP_ERROR_MESSAGES = {
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
    initialize: bool
    backup: bool
    migrate: bool
    configure: bool
    publish_viewer: bool
    interval_minutes: int
    generations: int
    publication_retention: int

    @property
    def planned_writes(self) -> list[str]:
        selected = {
            "database_restore": self.restore,
            "database_initialize": self.initialize,
            "migration_backup": self.backup,
            "database_migrate": self.migrate,
            "maintenance_configure": self.configure,
            "viewer_publish": self.publish_viewer,
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
) -> SetupServiceResult:
    return SetupServiceResult(
        ok=False,
        project_id=(
            inspection.scope.target.project.project_id
            if inspection.scope is not None
            else None
        ),
        data=_setup_data(),
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
    scope: ProjectScope,
    *,
    setup_state: SetupStorageState,
) -> str:
    current_snapshot: dict[str, Any] | None = None
    maintenance_viewer_succeeded = False
    try:
        with closing(connect_snapshot_readonly(scope.target.db_path)) as connection:
            current_snapshot = build_viewer_snapshot(
                connection,
                scope.target,
            ).snapshot
            if current_snapshot.get("source_schema_version") == SCHEMA_VERSION:
                viewer = read_viewer_maintenance(
                    connection,
                    scope.target.project.project_id,
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
        path=resolve_canonical_viewer_output_target(scope.target).path,
        target=scope.target,
        current_snapshot=current_snapshot,
        compare_snapshot=True,
        verify_template=True,
    )
    if artifact_status == "current" and not maintenance_viewer_succeeded:
        return "repair_required"
    return artifact_status


def _failure_viewer_status(
    scope: ProjectScope,
    setup_state: SetupStorageState,
) -> str:
    try:
        return _viewer_status(scope, setup_state=setup_state)
    except Exception:
        return "repair_required"


def _publish_viewer(scope: ProjectScope) -> None:
    result = publish_setup_viewer(scope.target)
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
        initialize=initialize,
        backup=migrate,
        migrate=migrate,
        configure=configure,
        publish_viewer=(
            restore or initialize or migrate or viewer_status != "current"
        ),
        interval_minutes=interval,
        generations=generations,
        publication_retention=(
            stored_generations if configured else generations
        ),
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

    try:
        state = inspect_setup_state(scope.target)
    except Exception as exc:
        code, message = _storage_preflight_code(exc)
        return _preflight_failure(
            inspection,
            code=code,
            message=message,
        )

    recovery_candidate: ManagedBackupRecoveryCandidate | None = None
    planning_state = state
    try:
        canonical_database_missing = (
            _canonical_database_is_lexically_absent(scope.target)
        )
    except Exception as exc:
        code, message = _storage_preflight_code(exc)
        return _preflight_failure(
            inspection,
            code=code,
            message=message,
        )
    if state.needs_initialize and canonical_database_missing:
        try:
            recovery_candidate = select_managed_backup_for_recovery(
                scope.target
            )
            if recovery_candidate is not None:
                planning_state = inspect_setup_state(
                    DatabaseTarget(
                        project=scope.target.project,
                        db_path=recovery_candidate.path,
                        explicit_db=scope.target.explicit_db,
                    )
                )
        except Exception as exc:
            code, message = _storage_preflight_code(exc)
            return _preflight_failure(
                inspection,
                code=code,
                message=message,
            )

    try:
        observed_viewer_status = _viewer_status(
            scope,
            setup_state=state,
        )
    except Exception as exc:
        code, message = _storage_preflight_code(exc)
        return _preflight_failure(
            inspection,
            code=code,
            message=message,
        )
    try:
        plan = _build_plan(
            planning_state,
            restore=recovery_candidate is not None,
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
    if read_only or not planned_writes:
        text = (
            "Setup preview complete"
            if read_only and planned_writes
            else "Project is already set up"
        )
        return SetupServiceResult(
            ok=True,
            project_id=scope.target.project.project_id,
            data=data,
            text=text,
        )

    completed: list[str] = []
    backup_metadata: MigrationBackupMetadata | None = None
    reported_interval = plan.interval_minutes
    reported_generations = plan.generations

    def failure_after_write(code: str) -> SetupServiceResult:
        return SetupServiceResult(
            ok=False,
            project_id=scope.target.project.project_id,
            data=_setup_data(
                planned_writes=planned_writes,
                completed_writes=completed,
                schema_from=schema_from,
                maintenance_enabled=current_maintenance,
                interval_minutes=reported_interval,
                generations=reported_generations,
                viewer_status=_failure_viewer_status(scope, state),
            ),
            error_code=code,
            error_message=SETUP_ERROR_MESSAGES[code],
        )

    if plan.restore:
        stage = "restore"
        try:
            scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            with managed_backup_lock(scope.target):
                if (
                    not _canonical_database_is_lexically_absent(scope.target)
                    or not inspect_setup_state(scope.target).needs_initialize
                ):
                    raise StorageError(
                        "setup_restore_failed",
                        SETUP_ERROR_MESSAGES["setup_restore_failed"],
                    )
                current_candidate = select_managed_backup_for_recovery(
                    scope.target
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
                    scope.target,
                    current_candidate,
                )
                if restored_version != schema_from:
                    raise StorageError(
                        "setup_restore_failed",
                        SETUP_ERROR_MESSAGES["setup_restore_failed"],
                    )
                restored_state = inspect_setup_state(scope.target)
                current_maintenance = bool(
                    restored_state.maintenance_enabled
                )
                completed.append("database_restore")
                if plan.backup:
                    stage = "backup"
                    backup_metadata = publish_setup_backup(
                        scope.target,
                        plan.publication_retention,
                    )
                    completed.append("migration_backup")
                    stage = "migrate"
                    scope = _revalidate_scope(
                        repo=repo,
                        repo_explicit=repo_explicit,
                        script_path=script_path,
                    )
                    initialize_database(
                        scope.target,
                        setup_backup=(
                            backup_metadata
                            if schema_from is not None and schema_from < 10
                            else None
                        ),
                        managed_backups=discover_managed_backup_metadata(
                            scope.target
                        ),
                    )
                    completed.append("database_migrate")
        except Exception:
            if stage == "restore":
                return failure_after_write("setup_restore_failed")
            if stage == "backup":
                return failure_after_write("setup_backup_failed")
            return failure_after_write("setup_migration_failed")

    if plan.initialize:
        try:
            scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            with managed_backup_lock(scope.target):
                current_database_missing = (
                    _canonical_database_is_lexically_absent(scope.target)
                )
                if (
                    not canonical_database_missing
                    or not current_database_missing
                    or not inspect_setup_state(scope.target).needs_initialize
                ):
                    raise StorageError(
                        "setup_initialization_failed",
                        SETUP_ERROR_MESSAGES["setup_initialization_failed"],
                    )
                if select_managed_backup_for_recovery(scope.target) is not None:
                    raise StorageError(
                        "setup_restore_failed",
                        SETUP_ERROR_MESSAGES["setup_restore_failed"],
                    )
                initialize_database(scope.target)
            completed.append("database_initialize")
        except StorageError as exc:
            if exc.code == "setup_restore_failed":
                return failure_after_write("setup_restore_failed")
            return failure_after_write("setup_initialization_failed")
        except Exception:
            return failure_after_write("setup_initialization_failed")

    if plan.backup and not plan.restore:
        stage_error: str | None = None
        try:
            scope = _revalidate_scope(
                repo=repo,
                repo_explicit=repo_explicit,
                script_path=script_path,
            )
            with managed_backup_lock(scope.target):
                backup_metadata = publish_setup_backup(
                    scope.target,
                    plan.publication_retention,
                )
                completed.append("migration_backup")
                scope = _revalidate_scope(
                    repo=repo,
                    repo_explicit=repo_explicit,
                    script_path=script_path,
                )
                initialize_database(
                    scope.target,
                    setup_backup=(
                        backup_metadata
                        if schema_from is not None and schema_from < 10
                        else None
                    ),
                    managed_backups=discover_managed_backup_metadata(
                        scope.target
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
            reported_interval, reported_generations = (
                configure_project_maintenance(
                    scope.target,
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
            _publish_viewer(scope)
            final_viewer_status = "published"
            completed.append("viewer_publish")
        except Exception:
            return failure_after_write("setup_incomplete")

    return SetupServiceResult(
        ok=True,
        project_id=scope.target.project.project_id,
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
