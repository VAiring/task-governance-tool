"""Read settled old effects and actual joined-worker proof; never perform cleanup."""

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from task_governance_tool.state_paths import path_lexically_exists
from .request_repository import RequestRepository, HistoricalRequestProof, request_identity_digest
from .review_wait_repository import ReviewWaitRepository
from .review_wait_direct_repository import DirectRepository, marker_exists


@dataclass(frozen=True)
class CleanupObservation:
    state: str
    reason: str | None
    worker_stop_identity: str | None = None


def _read_bound_state(paths, parent_id, task_id, request):
    """Validate a direct marker against its actual immutable request identity."""
    from .managed_wait import basis_digest
    from .review_wait_direct import binding_digest
    from .review_wait_repository import RepositoryError
    path = paths.review_wait_store(request.automation_id)
    direct = DirectRepository.for_wait(path).read()
    controller = ReviewWaitRepository.open_existing(path).read().controller
    if (basis_digest(controller.binding) != request.basis_digest
        or tuple(sorted((row.reviewer_id, row.turn_id) for row in controller.reviewers)) != request.reviewers
        or binding_digest(controller) != direct.binding_digest
        or controller.binding.parent_thread_id != parent_id or controller.binding.task_id != task_id
        or controller.reservation.timer_id != request.automation_id
        or controller.reservation.parent_thread_id != parent_id
        or request.parent_turn != direct.original_parent_turn):
        raise RepositoryError("review_continuation_conflict")
    return controller, direct


def read_bound_effects(paths, parent_id, task_id, request):
    controller, direct = _read_bound_state(paths, parent_id, task_id, request)
    return controller.binding, direct


def _effect_unknown(direct):
    return direct.status in ("dispatching", "unknown") or direct.timer_phase in ("arming", "deleting", "pausing", "unknown")


def _settled_effects(controller, direct, *, owner=None):
    """Shared effect and actual-exit proof; receipt absence is not uncertainty."""
    from .worker_observation import observe_worker
    from .review_wait_direct_repository import instant
    if _effect_unknown(direct):
        return CleanupObservation("unknown", "review_continuation_effect_unknown")
    observed = observe_worker(direct, controller, owner, instant(datetime.now(timezone.utc)))
    if direct.version != 2 or direct.worker_id is None or observed["status"] != "stopped":
        return CleanupObservation("unknown", "review_continuation_worker_exit_unknown")
    if direct.timer_phase not in ("paused", "deleted"):
        return CleanupObservation("unknown", "review_continuation_effect_unknown")
    return CleanupObservation("settled", None, direct.worker_stop_identity)


def observe_generation_cleanup(paths, parent_id, task_id, sequence, request, binding, *, owner=None):
    """Admit only the exact settled older generation under the request lease.

    The caller retains that lease through ordinary cleanup and new admission.
    This read creates no session, stops no worker, and never settles unknown effects.
    """
    try:
        if RequestRepository(paths.review_wait_request_store(parent_id, task_id)).read() != (sequence, request):
            return CleanupObservation("unknown", "review_continuation_conflict")
        if request.phase not in ("prepared", "started", "failed", "closed") or request.automation_id is None:
            return CleanupObservation("unknown", "review_continuation_effect_unknown")
        controller, direct = _read_bound_state(paths, parent_id, task_id, request)
        previous = controller.binding
        identity = ("project_id", "project_path_hash", "project_binding_generation", "parent_thread_id", "task_id")
        if (previous.target_generation >= binding.target_generation
                or any(getattr(previous, field) != getattr(binding, field) for field in identity)):
            return CleanupObservation("unknown", "review_continuation_conflict")
        return _settled_effects(controller, direct, owner=owner)
    except Exception as exc:
        return CleanupObservation("unknown", "review_continuation_conflict" if getattr(exc, "code", None) == "review_continuation_conflict"
            else "review_continuation_effect_unknown")


def observe_current_occurrence(paths, parent_id, task_id, request):
    """Readiness may retain a healthy live wait, but never an unknown effect.

    Unlike replacement cleanup, this does not require stopping that same worker.
    The caller already holds dispatch admission and proves the request's round.
    """
    try:
        if request.phase in ("creating", "closing", "unknown") or request.automation_id is None:
            return CleanupObservation("unknown", "review_continuation_effect_unknown")
        if not marker_exists(paths.review_wait_store(request.automation_id)):
            return (CleanupObservation("known", None) if request.phase == "created"
                else CleanupObservation("unknown", "review_continuation_worker_exit_unknown"))
        _, direct = read_bound_effects(paths, parent_id, task_id, request)
        if _effect_unknown(direct):
            return CleanupObservation("unknown", "review_continuation_effect_unknown")
        return CleanupObservation("known", None)
    except Exception as exc:
        reason = getattr(exc, "code", None)
        return CleanupObservation("unknown", "review_continuation_conflict" if reason == "review_continuation_conflict"
            else "review_continuation_effect_unknown")


def observe_cleanup(paths, parent_id, task_id, record, *, historical=None, owner=None):
    """Called under the transition/request admission, outside any core writer.

    A closed request records known timer cleanup. Bound actual thread_join
    is independently required, from an exact owner or saved marker. Neither an
    absent in-memory reference nor a deleted timer establishes thread exit.
    """
    try:
        path = paths.review_wait_request_store(parent_id, task_id)
        if path_lexically_exists(path):
            sequence, previous = RequestRepository(path).read()
        else:
            sequence, previous = 0, None
        if sequence != record.expected_attempt:
            return CleanupObservation("unknown", "review_continuation_conflict")
        if previous is None:
            if record.expected_request_digest is None and record.old_automation_id is None:
                return CleanupObservation("settled", None)
            return CleanupObservation("unknown", "review_continuation_conflict")
        if (request_identity_digest(previous) != record.expected_request_digest
            or previous.automation_id != record.old_automation_id
            or previous.basis_digest != record.wait_basis_digest and not (
                type(historical) is HistoricalRequestProof and historical.matches(sequence, previous, record))):
            return CleanupObservation("unknown", "review_continuation_conflict")
        if previous.phase in ("creating", "closing", "unknown"):
            return CleanupObservation("unknown", "review_continuation_effect_unknown")
        if previous.phase != "closed":
            return CleanupObservation("pending", "review_continuation_cleanup_required")
        wait_path = paths.review_wait_store(previous.automation_id)
        if not marker_exists(wait_path):
            # Only admission before any possible start proves no worker. A
            # missing marker after started/failed/closed is not such proof.
            if record.old_request_phase == "created":
                return CleanupObservation("settled", None)
            return CleanupObservation("unknown", "review_continuation_worker_exit_unknown")
        controller, direct = _read_bound_state(paths, parent_id, task_id, previous)
        settled = _settled_effects(controller, direct, owner=owner)
        if settled.state != "settled":
            return settled
        if historical is not None and direct.worker_stop_identity != historical.worker_stop_identity:
            return CleanupObservation("unknown", "review_continuation_conflict")
        return settled
    except Exception as exc:
        return CleanupObservation("unknown", "review_continuation_conflict" if getattr(exc, "code", None) == "review_continuation_conflict"
            else "review_continuation_effect_unknown")


def observe_latest_cleanup(paths, parent_id, task_id, sequence, request, *, owner=None):
    """Prove exact latest effects before retiring an obsolete prepared round.

    This read token is not persisted and grants no host operation. A closed
    request still needs its matching join proof and settled timer/send.
    """
    token = SimpleNamespace(expected_attempt=sequence,
        expected_request_digest=request_identity_digest(request) if request else None,
        old_automation_id=request.automation_id if request else None,
        wait_basis_digest=request.basis_digest if request else None,
        old_request_phase=request.phase if request else None)
    return observe_cleanup(paths, parent_id, task_id, token, owner=owner)
