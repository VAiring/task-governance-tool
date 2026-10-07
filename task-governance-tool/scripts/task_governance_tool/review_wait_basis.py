"""Read-only public handoff operation for the original Task's current wait basis."""

from pathlib import Path

from task_governance_tool.project_scope import inspect_project_scope, STRUCTURAL_CODES
from task_governance_tool.review_wait_basis_repository import read_wait_basis, WaitBasisError
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.task_values import validate_task_id, TaskValidationError


def wait_basis(repo, *, task_id):
    """No setup, core/usage writes, Packet transport, host call or identity fallback."""
    resolution = None
    code = None
    try:
        try:
            task_id = validate_task_id(task_id)
        except TaskValidationError:
            raise WaitBasisError("wait_basis_invalid_arguments") from None
        skill_root = Path(__file__).absolute().parents[2]
        inspection = inspect_project_scope(
            repo=repo, repo_explicit=True, script_path=skill_root / "scripts/review_handoff.py",
            include_runtime=False, include_package=False, include_ignore=False)
        if inspection.first_issue(allowed_codes=STRUCTURAL_CODES) or inspection.scope is None:
            raise WaitBasisError()
        resolution = resolve_project_state(skill_root=inspection.scope.skill_root,
            repo=inspection.scope.canonical_repo, retain_read_connection=True)
        if (resolution.error_code or resolution.binding != "matching"
                or resolution.target is None or resolution.read_connection is None):
            raise WaitBasisError()
        basis = read_wait_basis(resolution.read_connection,
                               project_id=resolution.target.project.project_id, task_id=task_id)
    except WaitBasisError as exc:
        code = exc.code
    except Exception:
        code = "wait_basis_unavailable"
    finally:
        if resolution is not None and resolution.read_connection is not None:
            try:
                resolution.read_connection.close()
            except Exception:
                code = "wait_basis_unavailable"
    if code is None:
        return {"ok": True, "status": "review_wait_basis", "basis": basis}
    return {"ok": False, "code": code, "message": "Current review-wait basis is unavailable."}
