"""Bounded same-process maintenance after successful business mutations."""

from __future__ import annotations

from dataclasses import dataclass

from task_governance_tool.backup import run_routine_backup
from task_governance_tool.storage import DatabaseTarget, utc_now


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
    """Run bounded backup-only M14.3 work after the business connection closes."""
    if not outcome.state_changed:
        return []
    try:
        backup = run_routine_backup(
            target,
            observed_at=observed_at or utc_now(),
        )
    except Exception:
        backup_code = "failed"
    else:
        backup_code = backup.code
    if backup_code not in BACKUP_WARNING_MESSAGES:
        return []
    return [
        {
            "code": f"backup_{backup_code}",
            "message": BACKUP_WARNING_MESSAGES[backup_code],
        }
    ]
