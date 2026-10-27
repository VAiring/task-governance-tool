"""Closed observation labels; never copy exception text or filesystem paths."""

from . import review_handoff_transport as files
from .state_paths import StatePathError


def diagnostic(stage, error):
    code = getattr(error, "code", None)
    # These wrappers preserve only the direct OS cause. Do not parse messages,
    # platform error text, arbitrary cause chains or provider output.
    cause = error.__cause__ if isinstance(error, (StatePathError, files.HandoffError)) else None
    observed = cause if isinstance(cause, OSError) else error
    reason = "unavailable"
    if stage in {"selection", "location"}:
        if isinstance(observed, FileNotFoundError):
            reason = "parent_missing"
        elif isinstance(observed, PermissionError):
            reason = "parent_inaccessible"
        elif code == "review_workspace_temp_changed":
            reason = "parent_changed"
        elif code in {"review_workspace_invalid_binding", "review_workspace_invalid_id"}:
            reason = "invalid_binding"
        else:
            reason = "parent_unavailable"
    elif stage in {"live_before", "live_after"}:
        if isinstance(error, (ValueError, UnicodeError)) or code == "handoff_response_invalid":
            reason = "public_response_incomplete"
        elif code in {"review_packet_stale", "handoff_file_changed"}:
            reason = "binding_changed"
    elif stage == "creation":
        if isinstance(observed, FileExistsError) or code == "review_workspace_exists":
            reason = "creation_conflict"
        elif isinstance(observed, PermissionError):
            reason = "creation_denied"
        elif isinstance(observed, FileNotFoundError):
            reason = "parent_missing"
    elif stage == "identity":
        reason = "identity_unconfirmed"
    return {"stage": stage, "reason": reason}
