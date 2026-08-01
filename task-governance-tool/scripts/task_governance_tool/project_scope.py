"""Bounded, read-only project and package-scope inspection."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from task_governance_tool import __version__
from task_governance_tool.completion import safe_git_command, safe_git_environment
from task_governance_tool.self_status import PackageSelfStatus, inspect_local_package
from task_governance_tool.state_resolver import canonical_state_paths
from task_governance_tool.storage import (
    DATABASE_BUSY_MESSAGE,
    lexical_skill_root_from_script,
    skill_root_from_script,
    uses_unsupported_linked_install,
)


PREFLIGHT_PRECEDENCE = (
    "unsupported_python",
    "unsupported_install_layout",
    "project_scope_required",
    "invalid_project_root",
    "state_path_invalid",
    "package_core_modified",
    "package_status_unknown",
    "state_ignore_required",
)

PREFLIGHT_MESSAGES = {
    "unsupported_python": "Python 3.12 or newer is required",
    "unsupported_install_layout": (
        "stateful use requires one supported physical project-scoped package layout"
    ),
    "project_scope_required": "explicit --repo is required from the package directory",
    "invalid_project_root": "project root must be an existing directory",
    "state_path_invalid": "project state path is not valid for this package layout",
    "package_core_modified": "packaged core files differ from the release manifest",
    "package_status_unknown": "package integrity could not be verified",
    "state_ignore_required": "project-local state must be ignored before setup",
}

PROJECT_STATE_MESSAGES = {
    "unsupported_journal_mode": "task database uses unsupported WAL journal mode",
    "database_busy": DATABASE_BUSY_MESSAGE,
    "project_state_unreadable": "project state could not be read safely",
    "project_mismatch": "task database belongs to a different project",
    "project_relocation_required": (
        "project state is bound to a different project location; "
        "run setup --read-only"
    ),
    "schema_too_new": "task database schema is newer than this taskgov version",
    "migration_required": "task database requires setup migration",
    "setup_required": "project state is not set up",
}

STRUCTURAL_CODES = frozenset(
    {
        "unsupported_python",
        "unsupported_install_layout",
        "project_scope_required",
        "invalid_project_root",
        "state_path_invalid",
    }
)

SOURCE_REQUIRED_FILES = (
    Path("AGENTS.md"),
    Path("docs/specification.md"),
    Path("docs/design.md"),
    Path("plan.md"),
)
PACKAGE_REQUIRED_FILES = (
    Path("SKILL.md"),
    Path("release-manifest.json"),
    Path("scripts/taskgov.py"),
)
STATE_IGNORE_OPERANDS = {
    "ordinary": ".agents/skills/task-governance-tool/state/",
    "source": "task-governance-tool/state/",
}


@dataclass(frozen=True)
class ProjectScopeIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ProjectScope:
    skill_root: Path
    canonical_repo: Path
    layout: str


@dataclass(frozen=True)
class ProjectScopeInspection:
    scope: ProjectScope | None
    package_status: PackageSelfStatus | None
    issues: tuple[ProjectScopeIssue, ...]

    def first_issue(
        self,
        *,
        allowed_codes: Iterable[str] | None = None,
    ) -> ProjectScopeIssue | None:
        allowed = None if allowed_codes is None else frozenset(allowed_codes)
        for code in PREFLIGHT_PRECEDENCE:
            if allowed is not None and code not in allowed:
                continue
            for issue in self.issues:
                if issue.code == code:
                    return issue
        return None


def _absolute_lexical_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute()


def _path_key(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = path.absolute()
    return os.path.normcase(os.path.normpath(str(resolved)))


def _same_location(left: Path, right: Path) -> bool:
    return _path_key(left) == _path_key(right)


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        normalized_path = Path(_path_key(path))
        normalized_parent = Path(_path_key(parent))
        normalized_path.relative_to(normalized_parent)
    except ValueError:
        return False
    return True


def _is_linklike(path: Path) -> bool:
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & reparse_attribute
    )


def _regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not _is_linklike(path)
    except OSError:
        return False


def _physical_directory(path: Path) -> bool:
    try:
        return path.is_dir() and not _is_linklike(path)
    except OSError:
        return False


def _has_linklike_component(path: Path) -> bool:
    candidate = path
    while True:
        if _is_linklike(candidate):
            return True
        if candidate.parent == candidate:
            return False
        candidate = candidate.parent


def _exists_or_linklike(path: Path) -> bool:
    try:
        return path.exists() or _is_linklike(path)
    except OSError:
        return True


def _required_files_are_regular(root: Path, relative_paths: Iterable[Path]) -> bool:
    for relative_path in relative_paths:
        candidate = root
        for component in relative_path.parts:
            candidate = candidate / component
            if _is_linklike(candidate):
                return False
        if not _regular_file(candidate):
            return False
    return True


def _state_path_is_valid(skill_root: Path) -> bool:
    paths = canonical_state_paths(skill_root)
    state_root = paths.state_root
    current_root = paths.fixed_root
    database_path = paths.database
    try:
        resolved_state = state_root.resolve(strict=False)
        resolved_current = current_root.resolve(strict=False)
        resolved_current.relative_to(resolved_state)
    except (OSError, RuntimeError, ValueError):
        return False

    directory_candidates = (
        state_root,
        current_root,
        paths.viewer.parent,
        paths.backups,
    )
    for candidate in directory_candidates:
        try:
            if _is_linklike(candidate) or (
                candidate.exists() and not candidate.is_dir()
            ):
                return False
        except OSError:
            return False
    try:
        if _is_linklike(database_path) or (
            database_path.exists() and not database_path.is_file()
        ):
            return False
        transition_lock = paths.transition_lock
        if _is_linklike(transition_lock) or (
            transition_lock.exists() and not transition_lock.is_file()
        ):
            return False
    except OSError:
        return False
    return True


def _has_enclosing_git_marker(repo: Path) -> bool | None:
    candidate = repo
    while True:
        try:
            os.lstat(candidate / ".git")
        except FileNotFoundError:
            pass
        except OSError:
            return None
        else:
            return True
        parent = candidate.parent
        if parent == candidate:
            return False
        candidate = parent


def _state_is_ignored(repo: Path, layout: str) -> bool:
    git_candidate = _has_enclosing_git_marker(repo)
    if git_candidate is None:
        return False
    if not git_candidate:
        return True

    operand = STATE_IGNORE_OPERANDS.get(layout)
    if operand is None:
        return False
    try:
        result = subprocess.run(
            [
                *safe_git_command(repo),
                "-c",
                "core.fsmonitor=false",
                "check-ignore",
                "--quiet",
                "--no-index",
                "--",
                operand,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
            env=safe_git_environment(),
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def inspect_project_scope(
    *,
    repo: str | os.PathLike[str],
    repo_explicit: bool,
    script_path: str | os.PathLike[str],
    include_runtime: bool = True,
    include_package: bool = True,
    include_ignore: bool = True,
) -> ProjectScopeInspection:
    """Observe the fixed project/package preflight without writing state."""

    lexical_skill_root = lexical_skill_root_from_script(script_path)
    physical_skill_root = skill_root_from_script(script_path)
    linked_layout = uses_unsupported_linked_install(script_path)
    package_status = (
        inspect_local_package(
            lexical_skill_root,
            installed_version=__version__,
            unsupported_install_layout=linked_layout,
        )
        if include_package
        else None
    )
    issues: list[ProjectScopeIssue] = []

    def add_issue(code: str) -> None:
        issues.append(ProjectScopeIssue(code, PREFLIGHT_MESSAGES[code]))

    if include_runtime and sys.version_info < (3, 12):
        add_issue("unsupported_python")

    lexical_repo = _absolute_lexical_path(repo)
    lexical_cwd = _absolute_lexical_path(Path.cwd())
    package_cwd_without_repo = (
        not repo_explicit
        and _path_is_within(lexical_cwd, lexical_skill_root)
    )
    if linked_layout:
        add_issue("unsupported_install_layout")

    repo_valid = _physical_directory(lexical_repo)
    canonical_repo: Path | None = None
    if repo_valid:
        try:
            canonical_repo = lexical_repo.resolve(strict=True)
        except (OSError, RuntimeError):
            repo_valid = False
    if repo_valid and _has_linklike_component(lexical_repo):
        if not linked_layout:
            add_issue("unsupported_install_layout")
        linked_layout = True

    layout: str | None = None
    if canonical_repo is not None and not linked_layout:
        ordinary_root = canonical_repo / ".agents" / "skills" / "task-governance-tool"
        source_root = canonical_repo / "task-governance-tool"
        if _same_location(lexical_skill_root, ordinary_root):
            layout = "ordinary"
        elif _same_location(lexical_skill_root, source_root):
            layout = "source"
            competing = ordinary_root
            source_valid = (
                _required_files_are_regular(canonical_repo, SOURCE_REQUIRED_FILES)
                and _required_files_are_regular(source_root, PACKAGE_REQUIRED_FILES)
                and not _exists_or_linklike(competing)
            )
            if not source_valid:
                add_issue("unsupported_install_layout")
        elif not package_cwd_without_repo:
            add_issue("unsupported_install_layout")

    source_requires_repo = layout == "source" and not repo_explicit
    if package_cwd_without_repo or source_requires_repo:
        add_issue("project_scope_required")

    if not repo_valid:
        add_issue("invalid_project_root")

    scope: ProjectScope | None = None
    if canonical_repo is not None and layout is not None:
        if not _state_path_is_valid(physical_skill_root):
            add_issue("state_path_invalid")
        scope = ProjectScope(
            skill_root=physical_skill_root,
            canonical_repo=canonical_repo,
            layout=layout,
        )
        if include_ignore and not _state_is_ignored(canonical_repo, layout):
            add_issue("state_ignore_required")

    if package_status is not None and package_status.status == "modified":
        add_issue("package_core_modified")
    elif package_status is not None and package_status.status == "unknown":
        add_issue("package_status_unknown")

    return ProjectScopeInspection(
        scope=scope,
        package_status=package_status,
        issues=tuple(issues),
    )
