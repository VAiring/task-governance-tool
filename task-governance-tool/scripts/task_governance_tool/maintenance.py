"""Bounded same-process maintenance after successful business mutations."""

from __future__ import annotations

from dataclasses import dataclass

from task_governance_tool.backup import run_routine_backup
from task_governance_tool.storage import DatabaseTarget, utc_now
from task_governance_tool.viewer_maintenance import (
    run_routine_viewer_refresh,
)


VIEWER_WARNING_MESSAGES = {
    "deferred": "Viewer refresh was deferred; task result is unchanged",
    "failed": "Viewer refresh did not complete; task result is unchanged",
}
BACKUP_WARNING_MESSAGES = {
    "deferred": "managed backup was deferred; task result is unchanged",
    "failed": "managed backup did not complete; task result is unchanged",
}


@dataclass(frozen=True)
class MutationOutcome:
    state_changed: bool
    viewer_relevant: bool


def run_post_commit_maintenance(
    target: DatabaseTarget,
    outcome: MutationOutcome,
    *,
    observed_at: str | None = None,
) -> list[dict[str, str]]:
    """Run bounded Viewer-then-backup work after the business connection closes."""
    if not outcome.state_changed:
        return []
    timestamp = observed_at or utc_now()
    warnings: list[dict[str, str]] = []
    if outcome.viewer_relevant:
        try:
            viewer = run_routine_viewer_refresh(
                target,
                observed_at=timestamp,
            )
        except Exception:
            viewer_code = "failed"
        else:
            viewer_code = viewer.code
        if viewer_code in VIEWER_WARNING_MESSAGES:
            warnings.append(
                {
                    "code": f"viewer_refresh_{viewer_code}",
                    "message": VIEWER_WARNING_MESSAGES[viewer_code],
                }
            )
    try:
        backup = run_routine_backup(
            target,
            observed_at=timestamp,
        )
    except Exception:
        backup_code = "failed"
    else:
        backup_code = backup.code
    if backup_code in BACKUP_WARNING_MESSAGES:
        warnings.append(
            {
                "code": f"backup_{backup_code}",
                "message": BACKUP_WARNING_MESSAGES[backup_code],
            }
        )
    return warnings
