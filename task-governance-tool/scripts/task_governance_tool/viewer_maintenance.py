"""Bounded same-process maintenance for the canonical static Viewer."""

from __future__ import annotations

from contextlib import closing, suppress
from dataclasses import dataclass

from task_governance_tool.artifact_lock import (
    ArtifactLockError,
    zero_wait_artifact_lock,
)
from task_governance_tool.storage import (
    DatabaseTarget,
    ProjectMaintenanceState,
    ViewerMaintenanceState,
    connect_initialized_readonly,
    read_project_maintenance,
    read_viewer_maintenance,
    record_viewer_attempt_outcome,
    record_viewer_publication,
    utc_now,
    validate_utc_timestamp,
)
from task_governance_tool.viewer import (
    ViewerOutputTarget,
    ViewerSnapshotResult,
    build_viewer_snapshot,
    render_viewer_html,
    resolve_canonical_viewer_output_target,
    validate_default_output_parent,
    write_viewer_html,
)


VIEWER_LOCK_FILENAME = "taskgov-viewer.lock"
MAX_RENDERS_PER_ATTEMPT = 2


@dataclass(frozen=True)
class ViewerRefreshResult:
    code: str
    renders: int


@dataclass(frozen=True)
class _ViewerCapture:
    maintenance: ProjectMaintenanceState
    viewer: ViewerMaintenanceState
    snapshot: ViewerSnapshotResult | None


def _capture(
    target: DatabaseTarget,
    *,
    force: bool,
    generated_at: str,
    include_snapshot: bool = True,
) -> _ViewerCapture:
    with closing(connect_initialized_readonly(target)) as connection:
        maintenance = read_project_maintenance(
            connection,
            target.project.project_id,
        )
        viewer = read_viewer_maintenance(
            connection,
            target.project.project_id,
        )
        if maintenance is None or viewer is None:
            raise RuntimeError("Viewer maintenance state is unavailable")
        snapshot = (
            build_viewer_snapshot(
                connection,
                target,
                generated_at=generated_at,
            )
            if include_snapshot
            and maintenance.enabled
            and (force or viewer.due)
            else None
        )
        return _ViewerCapture(
            maintenance=maintenance,
            viewer=viewer,
            snapshot=snapshot,
        )


def _record_failure(
    target: DatabaseTarget,
    *,
    code: str,
    occurred_at: str,
) -> None:
    with suppress(Exception):
        record_viewer_attempt_outcome(
            target,
            code=code,
            occurred_at=occurred_at,
        )


def _prepare_lock(output: ViewerOutputTarget) -> None:
    output.path.parent.mkdir(parents=True, exist_ok=True)
    validate_default_output_parent(output.path, output.state_root)


def _refresh(
    target: DatabaseTarget,
    *,
    force: bool,
    observed_at: str,
) -> ViewerRefreshResult:
    observed_at = validate_utc_timestamp(
        observed_at,
        field="Viewer maintenance observation time",
    )
    renders = 0
    try:
        first = _capture(
            target,
            force=force,
            generated_at=observed_at,
            include_snapshot=False,
        )
        if not first.maintenance.enabled:
            return ViewerRefreshResult(code="not_opted_in", renders=0)
        if not force and not first.viewer.due:
            return ViewerRefreshResult(code="current", renders=0)

        output = resolve_canonical_viewer_output_target(target)
        _prepare_lock(output)
        lock_path = output.path.parent / VIEWER_LOCK_FILENAME
        with zero_wait_artifact_lock(lock_path):
            capture = _capture(
                target,
                force=force,
                generated_at=observed_at,
            )
            for attempt in range(MAX_RENDERS_PER_ATTEMPT):
                if (
                    not capture.maintenance.enabled
                    or capture.snapshot is None
                ):
                    return ViewerRefreshResult(
                        code=(
                            "not_opted_in"
                            if not capture.maintenance.enabled
                            else "current"
                        ),
                        renders=renders,
                    )
                if attempt:
                    capture = _capture(
                        target,
                        force=False,
                        generated_at=observed_at,
                    )
                    if (
                        not capture.maintenance.enabled
                        or capture.snapshot is None
                    ):
                        break
                snapshot = capture.snapshot
                if snapshot is None:
                    break
                rendered = render_viewer_html(snapshot.snapshot)
                write_viewer_html(output, rendered)
                record_viewer_publication(
                    target,
                    source_generation=capture.viewer.source_generation,
                    published_at=utc_now(),
                )
                renders += 1
    except ArtifactLockError as exc:
        code = "deferred" if exc.contended else "failed"
        _record_failure(target, code=code, occurred_at=observed_at)
        return ViewerRefreshResult(code=code, renders=renders)
    except Exception:
        _record_failure(target, code="failed", occurred_at=observed_at)
        return ViewerRefreshResult(code="failed", renders=renders)
    return ViewerRefreshResult(code="succeeded", renders=renders)


def run_routine_viewer_refresh(
    target: DatabaseTarget,
    *,
    observed_at: str | None = None,
) -> ViewerRefreshResult:
    """Refresh when due, without waiting or changing the primary command."""

    return _refresh(
        target,
        force=False,
        observed_at=observed_at or utc_now(),
    )


def publish_setup_viewer(
    target: DatabaseTarget,
    *,
    observed_at: str | None = None,
) -> ViewerRefreshResult:
    """Force one bounded canonical publication for explicit setup repair."""

    return _refresh(
        target,
        force=True,
        observed_at=observed_at or utc_now(),
    )
