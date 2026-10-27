"""Evidence/Viewer maintenance and one bounded asynchronous-backup request."""

from __future__ import annotations

from dataclasses import dataclass

from task_governance_tool.async_backup_opportunity import request_backup
from task_governance_tool.async_backup_repository import read_request_policy
from task_governance_tool.async_backup_state import lock_owned, observe_runtime, runtime_root
from task_governance_tool.backup import managed_backup_due

from task_governance_tool.evidence_publication import (
    run_routine_evidence_projection,
)
from task_governance_tool.storage import DatabaseTarget, utc_now
from task_governance_tool.viewer_maintenance import (
    run_routine_viewer_refresh,
)


VIEWER_WARNING_MESSAGES = {
    "deferred": "Viewer refresh was deferred; task result is unchanged",
    "failed": "Viewer refresh did not complete; task result is unchanged",
}
EVIDENCE_WARNING_MESSAGES = {
    "deferred": (
        "Evidence projection refresh was deferred; task result is unchanged"
    ),
    "failed": (
        "Evidence projection refresh did not complete; task result is unchanged"
    ),
}
BACKUP_REQUEST_WARNING_MESSAGES = {
    "deferred": "Backup request was deferred; task result is unchanged",
    "failed": "Backup request could not be published; task result is unchanged",
}


def _request_due_backup(target: DatabaseTarget, timestamp: str) -> str:
    if not target.canonical_fixed:
        return "not_opted_in"
    if lock_owned(runtime_root(target) / "worker.lock") is True:
        return "running"
    policy = read_request_policy(target)
    if not policy.enabled:
        return "not_opted_in"
    recovery = observe_runtime(target, observed_at=timestamp)["state"] in {
        "interrupted", "failed", "deferred"}
    if not recovery and not managed_backup_due(policy, observed_at=timestamp):
        return "current"
    return request_backup(target).code
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
    """Run projections, then publish one service-owned backup opportunity."""
    from task_governance_tool.debug_log import aggregate
    with aggregate(run_post_commit_maintenance, unit='items') as measurement:
        if not outcome.state_changed:
            measurement.mark('unchanged')
            return []
        timestamp = observed_at or utc_now()
        warnings: list[dict[str, str]] = []
        try:
            evidence = run_routine_evidence_projection(
                target,
                observed_at=timestamp,
            )
        except Exception:
            evidence_code = "failed"
        else:
            evidence_code = evidence.code
        if evidence_code in EVIDENCE_WARNING_MESSAGES:
            warnings.append(
                {
                    "code": f"evidence_projection_{evidence_code}",
                    "message": EVIDENCE_WARNING_MESSAGES[evidence_code],
                }
            )
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
            backup_code = _request_due_backup(target, timestamp)
        except Exception:
            backup_code = "failed"
        if backup_code == "unknown":
            backup_code = "deferred"
        if backup_code in BACKUP_REQUEST_WARNING_MESSAGES:
            warnings.append({"code": f"backup_request_{backup_code}",
                             "message": BACKUP_REQUEST_WARNING_MESSAGES[backup_code]})
        return warnings
