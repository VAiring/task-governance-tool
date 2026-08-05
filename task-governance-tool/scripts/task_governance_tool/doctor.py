"""Read-only doctor orchestration and fixed component projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool.backup import managed_backup_due
from task_governance_tool.project_scope import (
    PREFLIGHT_MESSAGES,
    PROJECT_STATE_MESSAGES,
    STRUCTURAL_CODES,
    ProjectScopeInspection,
    inspect_project_scope,
)
from task_governance_tool.storage import (
    SCHEMA_VERSION,
    EvidenceProjectionState,
    ProjectMaintenanceState,
    ViewerMaintenanceState,
    utc_now,
)
from task_governance_tool.state_resolver import (
    consumer_error_code,
    resolve_project_state,
)


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
    "project_relocation_required": "relocation_required",
    "schema_too_new": "newer",
    "migration_required": "migration_required",
    "setup_required": "setup_required",
}

PACKAGE_WARNING_CODES = frozenset(
    {"package_core_modified", "package_status_unknown"}
)
SCOPE_FATAL_CODES = STRUCTURAL_CODES | {"state_ignore_required"}
READINESS_WARNING_CODES = frozenset(
    {
        "migration_required",
        "project_relocation_required",
        "setup_required",
    }
)


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
    viewer: ViewerMaintenanceState,
    evidence: EvidenceProjectionState,
    *,
    observed_at: str,
) -> dict[str, Any]:
    enabled = maintenance.enabled
    backup_succeeded = maintenance.backup_last_outcome_code == "succeeded"
    backup_due = (
        managed_backup_due(
            maintenance,
            observed_at=observed_at,
        )
        if enabled
        else None
    )
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
    viewer_due = viewer.due if enabled else None
    viewer_outcome_code = viewer.last_outcome_code if enabled else None
    viewer_outcome_at = viewer.last_outcome_at if enabled else None
    evidence_due = evidence.due if enabled else None
    evidence_outcome_code = evidence.last_outcome_code if enabled else None
    evidence_outcome_at = evidence.last_outcome_at if enabled else None
    return {
        "code": "enabled" if enabled else "not_opted_in",
        "opted_in": enabled,
        "backup": {
            "code": (
                (
                    maintenance.backup_last_outcome_code
                    if maintenance.backup_last_outcome_code
                    in {"deferred", "failed"}
                    else ("due" if backup_due else "current")
                )
                if enabled
                else "not_opted_in"
            ),
            "due": backup_due,
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
                (
                    viewer.last_outcome_code
                    if viewer.last_outcome_code in {"deferred", "failed"}
                    else ("due" if viewer_due else "current")
                )
                if enabled
                else "not_opted_in"
            ),
            "due": viewer_due,
            "source_generation": viewer.source_generation if enabled else None,
            "rendered_generation": (
                viewer.rendered_generation if enabled else None
            ),
            "last_success_at": viewer.last_success_at if enabled else None,
            "last_outcome": _last_outcome(
                viewer_outcome_code,
                viewer_outcome_at,
            ),
        },
        "evidence": {
            "code": (
                (
                    evidence.last_outcome_code
                    if evidence.last_outcome_code in {"deferred", "failed"}
                    else ("due" if evidence_due else "current")
                )
                if enabled
                else "not_opted_in"
            ),
            "due": evidence_due,
            "source_generation": (
                evidence.source_generation if enabled else None
            ),
            "published_generation": (
                evidence.published_generation if enabled else None
            ),
            "last_success_at": (
                evidence.last_success_at if enabled else None
            ),
            "last_outcome": _last_outcome(
                evidence_outcome_code,
                evidence_outcome_at,
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
    project_id: str | None = None,
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
        project_id=project_id,
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


def run_doctor(
    *,
    repo: str,
    repo_explicit: bool,
    script_path: Path,
) -> DoctorServiceResult:
    """Inspect package and project state without mutation."""

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
    resolution = resolve_project_state(
        skill_root=scope.skill_root,
        repo=scope.canonical_repo,
        include_doctor_state=True,
    )
    resolver_code = consumer_error_code(resolution)
    if resolver_code is not None:
        code = (
            "setup_required"
            if resolver_code == "db_not_initialized"
            else resolver_code
        )
        advisory = code in READINESS_WARNING_CODES
        return _unavailable_result(
            inspection,
            code=code,
            schema_version=resolution.source_schema_version,
            package_warning=package_warning,
            advisory=advisory,
            project_id=(
                resolution.project_id
                if code in READINESS_WARNING_CODES
                else None
            ),
        )
    if resolution.target is None:
        return _unavailable_result(
            inspection,
            code="project_state_unreadable",
            package_warning=package_warning,
            advisory=False,
        )
    storage_state = resolution.doctor_state
    if storage_state is None:
        return _unavailable_result(
            inspection,
            code="project_state_unreadable",
            schema_version=resolution.source_schema_version,
            package_warning=package_warning,
            advisory=False,
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
        storage_state.viewer,
        storage_state.evidence,
        observed_at=utc_now(),
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
        project_id=resolution.target.project.project_id,
        data=data,
        warnings=warnings,
        errors=[],
        text=f"Doctor: {project_code}",
    )
