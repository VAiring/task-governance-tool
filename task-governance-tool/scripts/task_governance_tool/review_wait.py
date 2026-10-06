"""Bound one caller-declared all-ended decision to current immutable review basis.

This acknowledges the actual caller's declaration, not host terminal events,
timer success, review quality or permission. Only the optional numerical session
registry may be written; no core write, log read or scheduling operation occurs.
"""

from contextlib import closing
from pathlib import Path

from task_governance_tool import review_handoff as files
from task_governance_tool.session_identity import capture_caller_identity
from task_governance_tool.storage import connect_initialized_readonly
from task_governance_tool.usage_lifecycle import _target, register_wait_supervisor
from task_governance_tool.usage_values import UsageError
from task_governance_tool.usage_wait_repository import capture_wait_metadata


def wait_ended(repo, *, packet_path=None, raw=None):
    """No replay or state inference: the supervisor invokes this in its decision turn."""
    try:
        if (packet_path is None) == (raw is None):
            files._fail("handoff_invalid_arguments")
        path = None
        if packet_path is not None:
            path = files._path(repo, packet_path)
            raw = files._read(path, files.PACKET_LIMIT)
        packet = files._packet(raw)
        caller = capture_caller_identity()
        caller.require()
        target = _target(Path(__file__).absolute().parents[2], repo, repo_explicit=True)
        with closing(connect_initialized_readonly(target)) as core:
            metadata = capture_wait_metadata(core, project_id=target.project.project_id,
                                             packet=packet, caller=caller)
        if path is not None and files._read(path, files.PACKET_LIMIT) != raw:
            files._fail("handoff_file_changed")
        register_wait_supervisor(repo, caller)
        return {"ok": True, "status": "review_wait_ended", "review_wait": metadata}
    except (files.HandoffError, files.TaskValidationError, files.ReviewEvidenceError, UsageError) as exc:
        code = exc.code
    except Exception:
        # The declaration is optional numerical evidence, never a reason to
        # retry a review, timer action or Task operation. Keep private errors out.
        code = "usage_unavailable"
    return {"ok": False, "code": code,
            "message": "Review-wait usage is unavailable; continue the existing wait protocol without replaying its actions."}
