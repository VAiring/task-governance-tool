"""Read-only doctor orchestration and fixed component projection."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool.project_scope import (
    PREFLIGHT_MESSAGES,
    PROJECT_STATE_MESSAGES,
    STRUCTURAL_CODES,
    ProjectScopeInspection,
    inspect_project_scope,
)
from task_governance_tool.storage import (
    SCHEMA_VERSION,
    ProjectMaintenanceState,
    StorageError,
    connect_readonly,
    current_schema_version,
    default_viewer_output_path,
    is_sqlite_busy_or_locked,
    read_doctor_state,
)
from task_governance_tool.viewer import inspect_canonical_viewer_status


DOCTOR_MESSAGES = {
    **PREFLIGHT_MESSAGES,
    **PROJECT_STATE_MESSAGES,
}

PROJECT_COMPONENT_CODES = {
    "unsupported_python": "unsupported_runtime",
    "unsupported_install_layout": "invalid_layout",
    "project_scope_required": "invalid_project",
    "invalid_project_root": "invalid_project",
    "state_path_invalid": "invalid_state_path",
    "state_ignore_required": "ignore_required",
    "unsupported_journal_mode": "unsupported_journal",
    "database_busy": "busy",
    "project_state_unreadable": "unreadable",
    "project_mismatch": "foreign",
    "schema_too_new": "newer",
    "migration_required": "migration_required",
    "setup_required": "setup_required",
}

PACKAGE_WARNING_CODES = frozenset(
    {"package_core_modified", "package_status_unknown"}
)
SCOPE_FATAL_CODES = STRUCTURAL_CODES | {"state_ignore_required"}
READINESS_WARNING_CODES = frozenset({"migration_required", "setup_required"})


@dataclass(frozen=True)
class DoctorServiceResult:
    ok: bool
    project_id: str | None
    data: dict[str, Any]
    warnings: list[dict[str, str]]
    errors: list[dict[str, str]]
    text: str = ""


def _last_outcome(
    code: str | None,
    occurred_at: str | None,
) -> dict[str, str | None]:
    normalized = code if code in {"succeeded", "deferred", "failed"} else "none"
    return {
        "code": normalized,
        "occurred_at": occurred_at if normalized != "none" else None,
    }


def _maintenance_component(
    maintenance: ProjectMaintenanceState,
    *,
    viewer_current: bool,
) -> dict[str, Any]:
    enabled = maintenance.enabled
    backup_succeeded = maintenance.backup_last_outcome_code == "succeeded"
    backup_outcome_code = (
        maintenance.backup_last_outcome_code
        if enabled or backup_succeeded
        else None
    )
    backup_outcome_at = (
        maintenance.backup_last_outcome_at
        if enabled or backup_succeeded
        else None
    )
    return {
        "code": "enabled" if enabled else "not_opted_in",
        "opted_in": enabled,
        "backup": {
            "code": (
                "setup_copy_succeeded"
                if backup_succeeded
                else ("configured" if enabled else "not_opted_in")
            ),
            "due": None,
            "interval_minutes": (
                maintenance.backup_interval_minutes if enabled else None
            ),
            "generations": maintenance.backup_generations if enabled else None,
            "last_success_at": (
                maintenance.backup_last_success_at
                if enabled or backup_succeeded
                else None
            ),
            "last_outcome": _last_outcome(
                backup_outcome_code,
                backup_outcome_at,
            ),
        },
        "viewer": {
            "code": (
                "published"
                if (
                    enabled
                    and maintenance.viewer_last_outcome_code == "succeeded"
                    and viewer_current
                )
                else ("repair_required" if enabled else "not_opted_in")
            ),
            "due": None,
            "source_generation": None,
            "rendered_generation": None,
            "last_success_at": (
                maintenance.viewer_last_success_at if enabled else None
            ),
            "last_outcome": _last_outcome(
                maintenance.viewer_last_outcome_code if enabled else None,
                maintenance.viewer_last_outcome_at if enabled else None,
            ),
        },
    }


def _project_state_component(
    code: str,
    schema_version: int | None,
) -> dict[str, Any]:
    return {
        "code": PROJECT_COMPONENT_CODES.get(code, code),
        "schema_version": schema_version,
        "required_schema_version": SCHEMA_VERSION,
    }


def _doctor_data(
    *,
    package: dict[str, Any],
    setup_eligible: bool,
    project_state: dict[str, Any],
    task_summary: dict[str, Any],
    handoff_delivery: dict[str, Any],
    maintenance: dict[str, Any],
) -> dict[str, Any]:
    return {
        "suggested_action": "continue",
        "setup_eligible": setup_eligible,
        "components": {
            "package": package,
            "project_state": project_state,
            "task_summary": task_summary,
            "handoff_delivery": handoff_delivery,
            "maintenance": maintenance,
        },
    }


def _package_warning(
    inspection: ProjectScopeInspection,
) -> dict[str, str] | None:
    issue = inspection.first_issue(allowed_codes=PACKAGE_WARNING_CODES)
    if issue is None:
        return None
    return {"code": issue.code, "message": issue.message}


def _unavailable_result(
    inspection: ProjectScopeInspection,
    *,
    code: str,
    schema_version: int | None = None,
    package_warning: dict[str, str] | None,
    advisory: bool,
) -> DoctorServiceResult:
    package = (
        inspection.package_status.to_data()
        if inspection.package_status is not None
        else {}
    )
    unavailable = {"code": "unavailable"}
    issue = {"code": code, "message": DOCTOR_MESSAGES[code]}
    warnings = (
        [package_warning or issue]
        if advisory
        else ([package_warning] if package_warning is not None else [])
    )
    return DoctorServiceResult(
        ok=advisory,
        project_id=(
            inspection.scope.target.project.project_id
            if inspection.scope is not None
            else None
        ),
        data=_doctor_data(
            package=package,
            setup_eligible=advisory and package_warning is None,
            project_state=_project_state_component(code, schema_version),
            task_summary=dict(unavailable),
            handoff_delivery=dict(unavailable),
            maintenance=dict(unavailable),
        ),
        warnings=warnings,
        errors=[] if advisory else [issue],
        text=f"Doctor: {PROJECT_COMPONENT_CODES[code]}" if advisory else "",
    )


def _storage_error_code(exc: Exception) -> str:
    if isinstance(exc, StorageError):
        if exc.code == "db_not_initialized":
            return "setup_required"
        if exc.code in DOCTOR_MESSAGES:
            return exc.code
    if isinstance(exc, sqlite3.Error) and is_sqlite_busy_or_locked(exc):
        return "database_busy"
    return "project_state_unreadable"


def run_doctor(
    *,
    repo: str,
    repo_explicit: bool,
    script_path: Path,
) -> DoctorServiceResult:
    """Inspect package and project state without mutation or external commands."""

    inspection = inspect_project_scope(
        repo=repo,
        repo_explicit=repo_explicit,
        script_path=script_path,
    )
    package_warning = _package_warning(inspection)
    scope_issue = inspection.first_issue(allowed_codes=SCOPE_FATAL_CODES)
    if scope_issue is not None:
        return _unavailable_result(
            inspection,
            code=scope_issue.code,
            package_warning=package_warning,
            advisory=False,
        )
    if inspection.scope is None or inspection.package_status is None:
        return _unavailable_result(
            inspection,
            code="unsupported_install_layout",
            package_warning=package_warning,
            advisory=False,
        )

    scope = inspection.scope
    schema_version: int | None = None
    try:
        with closing(connect_readonly(scope.target.db_path)) as connection:
            schema_version = current_schema_version(connection)
            storage_state = read_doctor_state(connection, scope.target)
    except (StorageError, sqlite3.Error) as exc:
        code = _storage_error_code(exc)
        if code in READINESS_WARNING_CODES:
            return _unavailable_result(
                inspection,
                code=code,
                schema_version=schema_version,
                package_warning=package_warning,
                advisory=True,
            )
        return _unavailable_result(
            inspection,
            code=code,
            schema_version=schema_version,
            package_warning=package_warning,
            advisory=False,
        )

    viewer_status = inspect_canonical_viewer_status(
        path=default_viewer_output_path(
            scope.skill_root,
            scope.target.project.project_id,
        ),
        target=scope.target,
        current_snapshot=None,
        compare_snapshot=False,
        verify_template=False,
    )
    counts = storage_state.task_counts
    task_summary = {
        "code": "ready",
        "active": counts["active"],
        "blocked": counts["blocked"],
        "done": counts["done"],
        "next_actionable": counts["next_actionable"],
        "paused": counts["paused"],
        "review_pending": counts["review_pending"],
    }
    handoff_delivery = {
        "code": "ready",
        "handoff_pending": counts["handoff_pending"],
        "adapter_enabled": False,
        "delivery_due": False,
    }
    maintenance = _maintenance_component(
        storage_state.maintenance,
        viewer_current=viewer_status == "current",
    )
    project_code = storage_state.project_code
    warning = (
        None
        if project_code == "ready" or package_warning is not None
        else {
            "code": project_code,
            "message": DOCTOR_MESSAGES[project_code],
        }
    )
    warnings = [package_warning] if package_warning is not None else []
    if warning is not None:
        warnings.append(warning)
    setup_eligible = (
        package_warning is None
        and project_code in {"ready", "setup_required", "migration_required"}
    )
    data = _doctor_data(
        package=inspection.package_status.to_data(),
        setup_eligible=setup_eligible,
        project_state=_project_state_component(
            project_code,
            storage_state.schema_version,
        ),
        task_summary=task_summary,
        handoff_delivery=handoff_delivery,
        maintenance=maintenance,
    )
    return DoctorServiceResult(
        ok=True,
        project_id=scope.target.project.project_id,
        data=data,
        warnings=warnings,
        errors=[],
        text=f"Doctor: {project_code}",
    )
