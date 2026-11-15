"""Permission-aware admission for Hook, accepted continuation and explicit Setup."""

from . import usage_continuation_state as state
from .artifact_lock import ArtifactLockError
from .debug_log import usage_diagnostic
from .usage_recovery_storage import failure
from .usage_values import UsageError, UsageRecoveryRequired


def admit(repository, target, *, source, roots=None, read_only=False):
    if source not in {"hook", "service", "setup"}:
        raise UsageError()
    try:
        if source == "setup":
            status = repository.inspect()
        else:
            with repository.connection():
                pass
            status = "current"
        usage_diagnostic("admission", "succeeded", "none")
        return status, False
    except UsageRecoveryRequired as needed:
        usage_diagnostic("admission", "unavailable", "readonly_rollback")
        if read_only:
            raise
        try:
            # A resolver-admitted target and exact numerical repository only.
            if (repository.path != target.resolved_usage_database or repository.basis !=
                    (target.project.project_id, target.binding_path_hash, target.binding_generation)):
                raise UsageError(reason="binding")
            if source == "service":
                roots = roots() if callable(roots) else roots
                # Configured service alone grants no recovery write or lock creation.
                with state.lease(target, "state") as root:
                    request = state.read(root, target)
                    if request is None or request["status"] == "caught_up" or request["roots"] != roots:
                        raise UsageError(reason="intent_required")
            with state.lease(target, "execution", create=source != "service"):
                if source == "service":
                    with state.lease(target, "state") as root:
                        if state.read(root, target) != request:
                            raise UsageError(reason="intent_required")
                status = repository.recover_interrupted_read(needed, setup=source == "setup")
            usage_diagnostic("recovery", "succeeded", "none")
            return status, True
        except ArtifactLockError as error:
            reason = failure(error)
            usage_diagnostic("recovery", "unavailable", reason)
            raise UsageError("usage_pending" if error.contended else "usage_unavailable", reason=reason) from None
        except Exception as error:
            usage_diagnostic("recovery", "unavailable", failure(error))
            raise
    except Exception as error:
        usage_diagnostic("admission", "unavailable", failure(error))
        raise
