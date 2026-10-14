"""Shared structural project admission for review handoff operations.

This resolves the existing canonical state binding only. Callers retain their
own operation, route, ownership, lease and effect permissions; resolution never
performs setup, migration, file publication or finalization admission.
"""

from pathlib import Path

from .project_scope import inspect_project_scope, STRUCTURAL_CODES
from .state_resolver import resolve_project_state


class ReviewHandoffScopeError(ValueError):
    def __init__(self, code="finalization_project_unavailable"):
        self.code = code
        super().__init__(code)


def resolve(repo, *, writing):
    """Resolve the existing project; writing additionally requires ignored state."""
    skill = Path(__file__).absolute().parents[2]
    inspection = inspect_project_scope(repo=repo, repo_explicit=True,
        script_path=skill / "scripts/review_handoff.py", include_runtime=False,
        include_package=False, include_ignore=writing)
    codes = STRUCTURAL_CODES | ({"state_ignore_required"} if writing else set())
    if inspection.first_issue(allowed_codes=codes) or inspection.scope is None:
        raise ReviewHandoffScopeError()
    resolution = resolve_project_state(skill_root=inspection.scope.skill_root, repo=inspection.scope.canonical_repo)
    if resolution.error_code or resolution.binding != "matching" or resolution.target is None:
        raise ReviewHandoffScopeError()
    return resolution


def error_code(exc, *, fallback="finalization_unavailable"):
    """Preserve the existing closed-code response without exposing exception text."""
    code = getattr(exc, "code", None)
    return code if type(code) is str and code and all(c.islower() or c.isdigit() or c == "_" for c in code) else fallback
