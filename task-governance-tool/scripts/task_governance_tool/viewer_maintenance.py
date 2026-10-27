"""Bounded same-process incremental maintenance of the canonical Viewer."""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .artifact_lock import ArtifactLockError, zero_wait_artifact_lock
from .storage import utc_now, validate_utc_timestamp
from .task_values import STATUSES
from .viewer import (
    ViewerSnapshotResult, render_viewer_html, resolve_canonical_viewer_output_target,
    validate_default_output_parent, write_viewer_html,
)
from .viewer_config import load_viewer_refresh_interval
from .viewer_display_validation import DisplayValidation, display_order
from .viewer_metadata_repository import (
    ViewerBasisChanged, accept_projection_cache, accept_projection_html,
    force_projection_rebuild, projection_replace_guard, projection_snapshot,
    read_projection_basis, record_projection_failure,
)
from .viewer_projection_cache import (
    CACHE_FORMAT_VERSION, PROJECTION_VERSION, artifact_present, cache_output,
    checked_artifact, encode_cache, load_candidate, matches, replace_artifact,
)
from .viewer_projection_repository import changed_tasks
from .viewer_task_projection_repository import read_task_projections

VIEWER_LOCK_FILENAME = "taskgov-viewer.lock"
MAX_RENDERS_PER_ATTEMPT = 2


@dataclass(frozen=True)
class ViewerRefreshResult:
    code: str
    renders: int


def _due(basis, output, cache):
    state = basis.state
    present = artifact_present(output) and artifact_present(cache)
    return (not present or basis.viewer.due
            or state.cache_format_version != CACHE_FORMAT_VERSION
            or state.projection_version != PROJECTION_VERSION
            or state.cache_generation != state.source_generation
            or state.html_generation != state.source_generation
            or basis.viewer.rendered_generation != state.html_generation
            or state.cache_generation < state.invalidated_generation)


def _capture(target, candidate, generated_at, *, validation=None):
    with projection_snapshot(target) as (connection, basis):
        if basis.enabled_at is None:
            return basis, None, None
        usable = matches(candidate, header=basis.header(), state=basis.state)
        task_ids = (tuple(task_id for task_id, _ in changed_tasks(
            connection, target.project.project_id, after=basis.state.cache_generation,
            through=basis.state.source_generation)) if usable else None)
        changed = read_task_projections(connection, project_id=target.project.project_id, task_ids=task_ids)
        tasks = {task["task_id"]: task for task in candidate.tasks} if usable else {}
        tasks.update((task["task_id"], task) for task in changed)
    ordered = sorted(tasks.values(), key=display_order)
    cache = encode_cache(basis.header(), ordered, validation=validation)
    counts = {"total": len(ordered), **{status: sum(task["status"] == status for task in ordered)
                                      for status in STATUSES}}
    snapshot = ViewerSnapshotResult({
        "snapshot_version": 4, "generated_at": generated_at,
        "project": {"project_id": basis.binding.project_id, "display_name": basis.binding.display_name},
        "source_schema_version": 34, "counts": counts, "tasks": ordered,
    }, len(ordered), sum(len(task["events"]) for task in ordered))
    return basis, cache, snapshot


def _refresh(target, *, force, observed_at, skill_root):
    from .debug_log import aggregate
    with aggregate(_refresh, unit="items") as measurement:
        measurement.count(0)
        observed_at = validate_utc_timestamp(observed_at, field="Viewer maintenance observation time")
        renders = 0
        observed = None
        failure_recorded = False
        try:
            observed = read_projection_basis(target)
            if observed.enabled_at is None:
                measurement.mark("skipped")
                return ViewerRefreshResult("not_opted_in", 0)
            output = resolve_canonical_viewer_output_target(target)
            cache_target = cache_output(target)
            if not force and not _due(observed, output, cache_target):
                measurement.mark("unchanged")
                return ViewerRefreshResult("current", 0)
            interval = load_viewer_refresh_interval(skill_root)
            validate_default_output_parent(output.path, output.state_root)
            output.path.parent.mkdir(parents=True, exist_ok=True)
            validate_default_output_parent(output.path, output.state_root)
            with zero_wait_artifact_lock(output.path.parent / VIEWER_LOCK_FILENAME):
                try:
                    observed = read_projection_basis(target)
                    if observed.enabled_at is None:
                        return ViewerRefreshResult("not_opted_in", 0)
                    if force:
                        force_projection_rebuild(target, observed)
                        observed = read_projection_basis(target)
                    elif not _due(observed, output, cache_target):
                        return ViewerRefreshResult("current", 0)
                    for attempt in range(MAX_RENDERS_PER_ATTEMPT):
                        validation = DisplayValidation()
                        candidate = load_candidate(cache_target, validation=validation)
                        basis, cache, snapshot = _capture(target, candidate, observed_at, validation=validation)
                        observed = basis
                        if snapshot is None:
                            return ViewerRefreshResult("not_opted_in", renders)
                        # Both complete byte capacities are checked before either
                        # artifact is replaced. No SQLite transaction is retained.
                        html = render_viewer_html(snapshot.snapshot, refresh_interval_seconds=interval)
                        try:
                            cache_file = replace_artifact(cache_target, cache.data,
                                guard=lambda: projection_replace_guard(target, basis), cache=True)
                        except ViewerBasisChanged:
                            # A pre-replacement guard rejection permits one new
                            # capture. Metadata writes below are never replayed.
                            if attempt + 1 == MAX_RENDERS_PER_ATTEMPT:
                                raise
                            continue
                        accepted = accept_projection_cache(target, basis, cache, cache_file)
                        try:
                            write_viewer_html(output, html, replace_guard=lambda:
                                projection_replace_guard(target, basis, accepted_cache=accepted))
                        except ViewerBasisChanged:
                            if attempt + 1 == MAX_RENDERS_PER_ATTEMPT:
                                raise
                            continue
                        html_file = checked_artifact(output, html.encode("utf-8"))
                        accept_projection_html(target, basis, accepted, output, html_file, published_at=utc_now())
                        renders += 1
                        measurement.count(1)
                        measurement.mark("published")
                        observed = read_projection_basis(target)
                        if not _due(observed, output, cache_target):
                            return ViewerRefreshResult("succeeded", renders)
                    # One recheck/capture budget, never a churn-driven retry loop.
                    raise RuntimeError("Viewer remains due after the bounded opportunity")
                except Exception:
                    # Observe own partial effects while the artifact lock remains
                    # held. Presence never certifies a successful publication.
                    with suppress(Exception):
                        observed = read_projection_basis(target)
                    if observed is not None:
                        failure_recorded = True
                        with suppress(Exception):
                            record_projection_failure(target, observed, code="failed", occurred_at=utc_now())
                    raise
        except ArtifactLockError as exc:
            code = "deferred" if exc.contended else "failed"
            measurement.mark("contended" if exc.contended else "failed")
            if observed is not None and not failure_recorded:
                with suppress(Exception):
                    record_projection_failure(target, observed, code=code, occurred_at=observed_at)
            return ViewerRefreshResult(code, renders)
        except Exception:
            measurement.mark("failed")
            if observed is not None and not failure_recorded:
                with suppress(Exception):
                    record_projection_failure(target, observed, code="failed", occurred_at=observed_at)
            return ViewerRefreshResult("failed", renders)


def run_routine_viewer_refresh(target, *, observed_at=None, skill_root: Path | None = None):
    return _refresh(target, force=False, observed_at=observed_at or utc_now(), skill_root=skill_root)


def publish_setup_viewer(target, *, observed_at=None, skill_root: Path | None = None):
    return _refresh(target, force=True, observed_at=observed_at or utc_now(), skill_root=skill_root)
