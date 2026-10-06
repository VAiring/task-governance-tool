"""Trusted project-local hook composition; numerical side effects only.

No core writer, implicit setup, host configuration, process launch or LLM
output. Invocation failures are neutral and remain recoverable by a later
project event. Unknown coverage is never promoted to complete.
"""

from contextlib import closing
import json
import os
from pathlib import Path

from task_governance_tool.session_identity import CallerIdentity, is_session_id
from task_governance_tool.project_scope import inspect_project_scope, STRUCTURAL_CODES
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.storage import connect_initialized_readonly
from task_governance_tool.usage_adapter import Cursor, read_batch
from task_governance_tool.usage_attribution_repository import registered_participants
from task_governance_tool.usage_evidence_service import repository_for, refresh_usage
from task_governance_tool.usage_sources import source_hint, source_roots, locate_sources
from task_governance_tool.usage_values import UsageError
from task_governance_tool.setup_feature_config import collection_allowed


EVENTS = frozenset({"SessionStart", "Stop", "SubagentStop", "SessionEnd"})
MAX_INPUT = 2 * 1024 * 1024


def _target(skill_root, repo, *, repo_explicit=False):
    inspection = inspect_project_scope(repo=repo, repo_explicit=repo_explicit,
        script_path=skill_root / "scripts/usage_hook.py", include_runtime=False,
        include_package=False, include_ignore=False)
    if inspection.first_issue(allowed_codes=STRUCTURAL_CODES) or inspection.scope is None:
        raise UsageError()
    resolution = resolve_project_state(skill_root=inspection.scope.skill_root,
                                       repo=inspection.scope.canonical_repo)
    if resolution.error_code or resolution.target is None or resolution.binding != "matching":
        raise UsageError()
    return resolution.target


def _register_caller(repo, caller):
    """Remember only an actual caller; never read logs or migrate numerical state."""
    try:
        target = _target(Path(__file__).absolute().parents[2], repo, repo_explicit=True)
        repository_for(target).register_session(caller)
    except Exception:
        pass


def register_reviewer(repo, caller):
    """Best effort after original save; later core Receipt binding is required."""
    _register_caller(repo, caller)


def register_wait_supervisor(repo, caller):
    """Best effort after a bound decision; registration alone attributes nothing."""
    _register_caller(repo, caller)


def collect_event(payload, *, skill_root, repo, environment, repo_explicit=False):
    """Internal report is for tests/diagnosis; never sent as model hook context."""
    unavailable = {"status": "unknown", "collected_sources": 0, "diagnostics": ["usage_unavailable"]}
    try:
        if not collection_allowed(skill_root):
            return unavailable
        if (not isinstance(payload, dict) or payload.get("hook_event_name") not in EVENTS
                or not isinstance(payload.get("cwd"), str) or not Path(payload["cwd"]).is_absolute()
                or os.path.normcase(os.path.abspath(payload["cwd"])) != os.path.normcase(os.path.abspath(repo))
                or not is_session_id(payload.get("session_id"))):
            return unavailable
        child = payload["hook_event_name"] == "SubagentStop"
        thread = payload.get("agent_id") if child else payload["session_id"]
        if not is_session_id(thread):
            return unavailable
        target = _target(skill_root, repo, repo_explicit=repo_explicit)
        repository = repository_for(target)
        sessions, known_sources = repository.registered_sources()
        with closing(connect_initialized_readonly(target)) as core:
            sessions = sessions | registered_participants(core, target.project.project_id)
        # SessionStart explicitly registers only its invoking session, not children.
        if payload["hook_event_name"] == "SessionStart":
            repository.register_session(CallerIdentity(thread))
            sessions = sessions | {thread}
        roots = source_roots(environment)
        sources = locate_sources(sessions, roots, Path(repo))
        if thread in sessions:
            hint = source_hint(thread, payload.get("agent_transcript_path" if child else "transcript_path"),
                               roots, Path(repo))
            if hint is not None:
                sources[hint.source_id] = hint
        collected = 0
        gaps = {"source_unreadable"} if sessions - {source.thread_id for source in sources.values()} else set()
        for identity, source in sources.items():
            try:
                # Validate the entire batch outside any writer, before admitting
                # a discovered path. A filename never registers another session.
                expected = repository.cursor(identity, source.thread_id) if identity in known_sources else Cursor()
                batch = read_batch(source, expected, include_attribution=True,
                                   attribution_project_id=target.project.project_id)
                repository.register_source(identity, CallerIdentity(source.thread_id))
                repository.commit_batch(batch)
                collected += 1
            except UsageError as error:
                gaps.add(error.code)
                if identity in known_sources:
                    repository.record_gap(identity, source.thread_id, error.code)
        for identity, owner in known_sources.items():
            if identity not in sources:
                repository.record_gap(identity, owner, "source_unreadable")
                gaps.add("source_unreadable")
        publication = refresh_usage(target)
        gaps.update(publication["diagnostics"])
        return {"status": "unknown" if gaps else "pending", "collected_sources": collected,
                "diagnostics": sorted(gaps)}
    except Exception:
        return unavailable


def main(*, stdin, stdout, skill_root, repo=None, environment=None, argv=()):
    """Fixed neutral hook protocol even for malformed/private/unknown input."""
    try:
        cwd = Path.cwd() if repo is None else Path(repo)
        if argv:
            if (len(argv) != 2 or argv[0] != "--repo" or not argv[1].strip()
                    or os.path.normcase(os.path.abspath(argv[1])) != os.path.normcase(os.path.abspath(cwd))):
                raise UsageError()
        # Explicit opt-in selects no alternate project: cwd and payload must
        # still match, and the existing physical-layout preflight stays intact.
        raw = stdin.read(MAX_INPUT + 1)
        if len(raw) <= MAX_INPUT:
            payload = json.loads(raw)
            collect_event(payload, skill_root=skill_root, repo=cwd,
                          environment=os.environ if environment is None else environment,
                          repo_explicit=bool(argv))
    except Exception:
        pass
    stdout.write("{}\n")
    return 0
