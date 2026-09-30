"""Bounded numerical collection and explicit setup integration.

No hooks, discovery, Task attribution or public usage command is activated.
The lifecycle caller supplies its actual identity and one approved exact path.
"""

from __future__ import annotations

from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.state_resolver import observe_current_root
from task_governance_tool.usage_adapter import SourceInput, read_batch
from task_governance_tool.usage_repository import UsageRepository
from task_governance_tool.usage_values import UsageError


def collect_registered(repository: UsageRepository, source: SourceInput) -> dict:
    """Read only an already registered source; no implicit session expansion."""
    try:
        if observe_current_root(source.project_root).canonical_path_hash != repository.basis[1]:
            raise UsageError("usage_binding_mismatch")
        expected = repository.cursor(source.source_id, source.thread_id)
        batch = read_batch(source, expected, include_attribution=repository.collect_attribution,
                           attribution_project_id=repository.basis[0])
        repository.commit_batch(batch)
        return repository.summary()
    except UsageError as exc:
        try:
            repository.record_gap(source.source_id, source.thread_id, exc.code)
            result = repository.summary()
            result.update(status="unknown", diagnostics=sorted(set(result["diagnostics"]) | {exc.code}))
            return result
        except UsageError:
            return {"status": "unknown", "models": [], "diagnostics": [exc.code]}


def register_source(repository: UsageRepository, source: SourceInput, caller: CallerIdentity) -> None:
    if caller.session_id is None or caller.session_id != source.thread_id:
        raise UsageError("source_not_registered")
    if observe_current_root(source.project_root).canonical_path_hash != repository.basis[1]:
        raise UsageError("usage_binding_mismatch")
    repository.register_source(source.source_id, caller)


def setup_usage(inspection, core_result, *, read_only: bool) -> dict:
    """Separate numerical outcome after core setup/preview, never a core gate."""
    from task_governance_tool.storage import SCHEMA_VERSION
    repository_type = UsageRepository
    if SCHEMA_VERSION >= 25:
        from task_governance_tool.usage_attribution_repository import UsageAttributionRepository
        repository_type = UsageAttributionRepository
    result = {"status": "not_attempted", "schema_to": repository_type.migrations[-1][0],
              "planned_writes": [], "completed_writes": [], "error": None}
    if not core_result.ok or inspection.scope is None:
        return result
    try:
        from task_governance_tool.state_resolver import resolve_project_state

        scope = inspection.scope
        resolution = resolve_project_state(skill_root=scope.skill_root, repo=scope.canonical_repo)
        if (resolution.error_code is not None or resolution.target is None
                or resolution.binding != "matching"):
            # Preview may precede fresh initialization/separation. No file access
            # or guessed durable identity is needed to report that later stage.
            if read_only and core_result.data.get("planned_writes"):
                result.update(status="pending_core_setup")
                return result
            raise UsageError()
        target = resolution.target
        repository = repository_type(resolution.paths.usage_database, target.project.project_id,
                                     target.binding_path_hash, target.binding_generation)
        status = repository.inspect()
        if status == "not_present":
            result["planned_writes"] = ["usage_initialize"]
        elif status == "migration_required":
            result["planned_writes"] = ["usage_migrate"]
        if not read_only:
            status = repository.initialize()
            if status == "initialized":
                result["completed_writes"] = ["usage_initialize"]
            elif status == "migrated":
                result["completed_writes"] = ["usage_migrate"]
        result["status"] = status
    except Exception:
        # No raw diagnostic crosses setup, and valid core state stays committed.
        result.update(status="unavailable", error="usage_unavailable")
    return result
