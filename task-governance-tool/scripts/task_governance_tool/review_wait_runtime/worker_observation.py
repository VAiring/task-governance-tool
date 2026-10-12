"""Bounded same-attempt observations; no effects or general process monitoring."""

from dataclasses import asdict
import hashlib
import json
from .review_wait_direct import binding_digest


WORKER_FIELDS = ("worker_id", "worker_stopped_at", "worker_stop_basis", "worker_stop_identity")


def delivery_projection(record):
    value = asdict(record)
    value.pop("binding_digest")
    for field in WORKER_FIELDS:
        value.pop(field)
    return value


def unknown_worker(reason="not_observed", worker_id=None):
    return {"status": "unknown", "worker_id": worker_id, "source": "unavailable",
            "confirmed_at": None, "persistence": "unknown", "reason": reason}


def observe_worker(record, controller, owner, observed_at):
    result = unknown_worker("no_live_reference_or_stop_evidence", record.worker_id)
    if record.binding_digest != binding_digest(controller):
        return unknown_worker("binding_mismatch", record.worker_id)
    worker = None if owner is None else owner._worker
    if owner is not None and worker is not None:
        if (owner._owned_probe_id != record.probe_id
                or owner._owned_worker_id != record.worker_id or record.worker_id is None):
            return unknown_worker("ownership_mismatch", record.worker_id)
        if worker.ident is not None:
            alive = worker.is_alive()
            if alive and record.worker_stopped_at is not None:
                return unknown_worker("conflicting_evidence", record.worker_id)
            if alive:
                return {**result, "status": "running", "source": "direct_thread",
                        "persistence": "not_recorded", "reason": None}
            worker.join(0)  # Actual started thread termination, never a finally marker.
            if not worker.is_alive():
                saved = record.worker_stopped_at is not None
                return {**result, "status": "stopped", "source": "direct_thread",
                        "confirmed_at": observed_at,
                        "persistence": "confirmed" if saved else owner._worker_stop_persistence,
                        "reason": None}
    if record.worker_stopped_at is not None:
        return {**result, "status": "stopped", "source": "saved_thread_join",
                "confirmed_at": record.worker_stopped_at, "persistence": "confirmed", "reason": None}
    return result


def observation(task_id, parent_id, observed_at, *, request=None, attempt=None,
                controller=None, direct=None, owner=None, runtime=None, reason=None):
    worker = unknown_worker(reason or "not_observed")
    if direct is not None and controller is not None:
        basis = asdict(controller.binding)
        basis.pop("wait_id")
        digest = hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        matched = (controller.binding.task_id == task_id and controller.binding.parent_thread_id == parent_id
                   and request is not None and request.basis_digest == digest
                   and request.automation_id == controller.reservation.timer_id
                   and request.parent_turn == direct.original_parent_turn
                   and request.reviewers == tuple(sorted((r.reviewer_id, r.turn_id) for r in controller.reviewers)))
        worker = observe_worker(direct, controller, owner, observed_at) if matched else unknown_worker("binding_mismatch", direct.worker_id)
    reviewers = []
    if owner is not None:
        with owner._observations_lock:
            reviewers = list(owner._reviewer_observations.values())
    return {
        "version": 1, "observed_at": observed_at,
        "identity": {"task_id": task_id, "parent_thread_id": parent_id,
                     "attempt": attempt, "automation_id": None if request is None else request.automation_id,
                     "probe_id": None if direct is None else direct.probe_id},
        "request": {"status": "absent" if request is None and reason is None else
                    "unknown" if request is None else "present",
                    "phase": None if request is None else request.phase},
        "worker": worker,
        "delivery": None if direct is None else delivery_projection(direct),
        "service": {"status": "responding", "scope": "this_observation_only"},
        "runtime": runtime or {"comparison": "unknown"},
        "work": {"scope": "bound_reviewers_only",
                 "reviewers": reviewers,
                 "other_children": "unknown", "verification_process": "unknown",
                 "postprocessing": "returned" if owner is not None and
                    getattr(owner, "_finalization_prepared", False) else "unknown"},
    }
