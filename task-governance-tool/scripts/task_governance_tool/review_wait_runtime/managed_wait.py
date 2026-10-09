"""Single-call wait orchestration; result interpretation remains with the parent.

Only explicit wait/stop mutate. An unresolved intent, restart or inspection
never restarts a worker or creates a competing reservation.
"""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json

from .request_repository import RequestRecord, RequestRepository
from .review_wait_direct_repository import DirectRepository, marker_exists
from .review_wait_host import executor_turn_id, reviewer_references
from .review_wait_repository import ReviewWaitRepository
from .review_wait_direct import _terminal
from .review_wait_service import ten_minute_appointment
from task_governance_tool.state_paths import path_lexically_exists


def basis_digest(binding):
    value = asdict(binding)
    value.pop("wait_id")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ManagedWait:
    def __init__(self, owner):
        self.owner = owner

    @staticmethod
    def _failure(code="review_wait_unresolved"):
        return {"ok": False, "error": code, "parent_may_end": False}

    def _host(self, metadata, task_id, automation, reviewers):
        owner = self.owner
        return owner.managed_host_factory(metadata=metadata, task_id=task_id,
            server_path=owner.config.server_path, automation_id=automation,
            codex_home=owner.config.codex_home, confirmed_timezone=owner.config.timezone,
            timezone_source="host_node_intl", reviewer_ids=reviewers)

    def _current(self, task_id, metadata):
        return self.owner._reader(self.owner.config.repo, task_id, metadata["threadId"], "request",
            helper=self.owner.skill / "scripts/review_handoff.py")

    @staticmethod
    def _read_reviewer(host, reviewer, expected, finalizer):
        def unavailable():
            if finalizer is not None:
                if reviewer in expected:
                    finalizer.observe_reviewers(((reviewer, expected[reviewer], "unknown"),))
                else:
                    finalizer.observe_reviewers(unavailable=True)
        try:
            turn = host.read_child(reviewer)
        except Exception:
            unavailable()
            raise
        if turn.child_id != reviewer or reviewer in expected and turn.turn_id != expected[reviewer]:
            unavailable()
            raise ValueError("reviewer_turn_changed")
        if finalizer is not None and (_terminal(turn) or turn.status in {"failed", "interrupted"}):
            finalizer.observe_reviewers(((reviewer, turn.turn_id, turn.status),))
        return turn

    def _preflight(self, task_id, references, metadata, paths):
        actual_turn = executor_turn_id(metadata)
        reader = self._current(task_id, metadata)
        binding = reader()
        if binding.parent_thread_id != metadata["threadId"] or binding.task_id != task_id:
            raise ValueError("binding_mismatch")
        references = reviewer_references(references)
        host = self._host(metadata, task_id, "pending", references)
        reviewers = host.resolve_reviewer_ids() if any(r.startswith("/") for r in references) else references
        if len(set(reviewers)) != len(reviewers) or metadata["threadId"] in reviewers:
            raise ValueError("invalid_child_set")
        finalizer = self.owner._finalizer(binding)
        if finalizer is not None and len(reviewers) != len(finalizer.journal.read().result_paths):
            raise ValueError("invalid_child_set")
        expected = {}
        request_path = paths.review_wait_request_store(metadata["threadId"], task_id)
        if path_lexically_exists(request_path):
            _, previous = RequestRepository(request_path).read()
            if (previous is not None and previous.basis_digest == basis_digest(binding)
                    and (previous.phase != "closed" or finalizer is not None)):
                expected = dict(previous.reviewers)
        pairs = []
        ended = True
        for reviewer in reviewers:
            turn = self._read_reviewer(host, reviewer, expected, finalizer)
            pairs.append((reviewer, turn.turn_id))
            ended = _terminal(turn) and ended
        parent = host.read_parent()
        if (parent.child_id != metadata["threadId"] or parent.turn_id != actual_turn
                or parent.status != "inProgress" or parent.thread_status != "active"
                or reader() != binding):
            raise ValueError("parent_turn_changed")
        return reader, binding, tuple(sorted(pairs)), host, ended, finalizer

    def _ready(self, task_id, record, *, replayed=False):
        session = self.owner.sessions.get(record.automation_id)
        if session is None or session.direct is None:
            return self._failure("review_wait_restart_requires_recovery")
        result = session.direct._summary()
        state = result.get("state") or {}
        ready = (record.phase == "started" and state.get("status") == "waiting"
                 and state.get("timer_phase") == "active" and result.get("worker_alive") is True)
        if not ready:
            return self._failure()
        return {"ok": True, "status": "waiting", "task_id": task_id,
                "parent_may_end": True, "replayed": replayed}

    def _cleanup(self, repository, lease, record, task_id, metadata, paths):
        if record.phase == "closed":
            return True
        if record.phase in {"creating", "unknown", "closing"} or record.automation_id is None:
            return False
        path = paths.review_wait_store(record.automation_id)
        if marker_exists(path):
            direct = DirectRepository.for_wait(path).read()
            # Unknown sending is still a competing effect even if its timer is gone.
            if direct.status in {"dispatching", "unknown"} or direct.timer_phase in {
                    "arming", "deleting", "pausing", "unknown"}:
                return False
            session = self.owner._session(path, task_id, record.automation_id, managed=True)
            result = session.handle("direct_cancel", {"probe_id": direct.probe_id}, metadata)
            if result.get("ok") is not True:
                return False
            direct = DirectRepository.for_wait(path).read()
            # The worker may dispatch between the first read and cancellation.
            # Joining settles it; a deleted timer alone cannot settle its send.
            if (direct.status in {"dispatching", "unknown"}
                    or direct.timer_phase not in {"paused", "deleted"}):
                return False
        else:
            direct = None
        repository.advance(lease, "closing")
        try:
            if direct is None or direct.timer_phase != "deleted":
                host = self._host(metadata, task_id, record.automation_id, [r[0] for r in record.reviewers])
                before = host.view_heartbeat(for_pause=True)
                if before.status != "PAUSED":
                    raise ValueError("heartbeat_changed")
                if path_lexically_exists(path):
                    original = ReviewWaitRepository.open_existing(path).read().controller.reservation
                    if (before.id != original.timer_id or before.identity_digest != original.identity_digest
                            or before.parent_thread_id != original.parent_thread_id):
                        raise ValueError("heartbeat_changed")
                else:
                    # Before preparation only the fixed creation policy may own cleanup.
                    host.verify_created(before.rule)
                host.delete_heartbeat(before, cleanup=True)
            repository.advance(lease, "closed")
            return True
        except Exception:
            repository.advance(lease, "unknown")
            return False

    def handle(self, operation, arguments, metadata, paths):
        task_id = arguments["task_id"]
        path = paths.review_wait_request_store(metadata["threadId"], task_id)
        if operation != "wait":
            if not path_lexically_exists(path):
                return {"ok": True, "status": "absent", "task_id": task_id}
            repository = RequestRepository(path)
            if operation == "inspect":
                _, record = repository.read()
                result = {"ok": True, "task_id": task_id, "phase": None if record is None else record.phase}
                if record is not None:
                    result["automation_id"] = record.automation_id
                    if record.automation_id is not None:
                        timer_path = paths.review_wait_store(record.automation_id)
                        if marker_exists(timer_path):
                            state = asdict(DirectRepository.for_wait(timer_path).read())
                            state.pop("binding_digest")
                            result["delivery"] = state
                return result
            with repository.serial() as lease:
                _, record = repository.read()
                if record is not None and not self._cleanup(repository, lease, record, task_id, metadata, paths):
                    return self._failure()
            return {"ok": True, "status": "stopped", "task_id": task_id}

        reader, binding, pairs, host, ended, finalizer = self._preflight(
            task_id, arguments["reviewer_ids"], metadata, paths)
        self.owner._ensure_root(paths)
        repository = RequestRepository(path)
        with repository.serial(create=True) as lease:
            _, previous = repository.read()
            digest = basis_digest(binding)
            current_turn = executor_turn_id(metadata)
            if previous is not None:
                if previous.basis_digest != digest or previous.reviewers != pairs:
                    if previous.phase != "closed":
                        return self._failure("review_wait_binding_changed")
                elif previous.parent_turn == current_turn:
                    return self._ready(task_id, previous, replayed=True)
                if not self._cleanup(repository, lease, previous, task_id, metadata, paths):
                    return self._failure()
                if ended:
                    return {"ok": True, "status": "reviews_ended", "task_id": task_id, "parent_may_end": False}
            if len(self.owner.sessions) >= 64:
                return self._failure("session_limit")
            appointment = ten_minute_appointment(self.owner.clock or (lambda: datetime.now(timezone.utc)),
                                               self.owner.config.timezone)
            if reader() != binding:
                return self._failure("review_wait_binding_changed")
            repository.append(lease, RequestRecord(digest, current_turn, pairs))
            try:
                automation = host.create_heartbeat(appointment.rule)
            except Exception:
                repository.advance(lease, "unknown")
                return self._failure("heartbeat_create_unknown")
            record = repository.advance(lease, "created", automation_id=automation)
            try:
                host.verify_created(appointment.rule)
                def prepare_reviewer_reader(current_binding, current_host, reviewer):
                    if basis_digest(current_binding) != digest:
                        raise ValueError("binding_changed")
                    # The preparation reread is part of this same admission;
                    # retain its outcome before startup or another host read.
                    return self._read_reviewer(current_host, reviewer, dict(pairs), finalizer)
                session = self.owner._session(paths.review_wait_store(automation), task_id, automation,
                    managed=True, prepare_reviewer_reader=prepare_reviewer_reader)
                prepared = session.handle("prepare", {"reviewer_ids": [p[0] for p in pairs]}, metadata)
                if prepared.get("ok") is not True:
                    raise ValueError("preparation_failed")
                controller = ReviewWaitRepository.open_existing(paths.review_wait_store(automation)).read().controller
                if (basis_digest(controller.binding) != digest
                        or tuple(sorted((r.reviewer_id, r.turn_id) for r in controller.reviewers)) != pairs):
                    raise ValueError("binding_changed")
                record = repository.advance(lease, "prepared")
                started = session.handle("direct_delete_start", {}, metadata)
                if (started.get("ok") is not True or started.get("worker_alive") is not True
                        or started.get("state", {}).get("status") != "waiting"
                        or started.get("state", {}).get("timer_phase") != "active"):
                    raise ValueError("start_unavailable")
                record = repository.advance(lease, "started")
                return self._ready(task_id, record)
            except Exception:
                record = repository.advance(lease, "failed")
                self._cleanup(repository, lease, record, task_id, metadata, paths)
                return self._failure()
