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
from .failure_diagnostics import CallDiagnostics, DiagnosticError
from .worker_observation import observation, delivery_projection
from .review_wait_direct_repository import instant


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

    def observe(self, task_id, metadata, paths, *, expected=None):
        """Read only the same latest attempt; failure affects observation alone."""
        now = instant((self.owner.clock or (lambda: datetime.now(timezone.utc)))())
        arguments = dict(task_id=task_id, parent_id=metadata["threadId"], observed_at=now)
        try:
            path = paths.review_wait_request_store(metadata["threadId"], task_id)
            if not path_lexically_exists(path):
                return observation(**arguments)
            attempt, record = RequestRepository(path).read()
            if expected is not None:
                previous_attempt, previous = expected
                identity = lambda item: None if item is None else (
                    item.automation_id, item.basis_digest, item.parent_turn, item.reviewers)
                if previous_attempt != attempt or identity(previous) != identity(record):
                    return observation(**arguments, request=previous, attempt=previous_attempt,
                                       reason="attempt_changed")
            arguments.update(request=record, attempt=attempt)
            if record is not None and record.automation_id is not None:
                wait_path = paths.review_wait_store(record.automation_id)
                if marker_exists(wait_path):
                    controller = ReviewWaitRepository.open_existing(wait_path).read().controller
                    direct = DirectRepository.for_wait(wait_path).read()
                    session = self.owner.sessions.get(record.automation_id)
                    arguments.update(controller=controller, direct=direct,
                                     owner=None if session is None else session.direct)
            return observation(**arguments)
        except Exception:
            # Do not overwrite a successful stop or recommend another effect.
            arguments.pop("controller", None)
            arguments.pop("direct", None)
            arguments.pop("owner", None)
            return observation(**arguments, reason="observation_unavailable")

    def observe_safely(self, task_id, metadata, paths, *, expected=None):
        try:
            return self.observe(task_id, metadata, paths, expected=expected)
        except Exception:
            attempt, record = (None, None) if expected is None else expected
            return observation(task_id, metadata["threadId"], instant(datetime.now(timezone.utc)),
                               request=record, attempt=attempt, reason="observation_unavailable")

    def _read_diagnostic(self, observed, attempt, record, task_id, parent_id):
        """Reuse the admitted observation; unknown/mismatched ownership is no proof."""
        try:
            identity, facts = observed["identity"], observed["worker"]
            if (record is None or identity["task_id"] != task_id or identity["parent_thread_id"] != parent_id
                    or type(identity["attempt"]) is not int or identity["attempt"] != attempt
                    or identity["automation_id"] != record.automation_id
                    or identity["probe_id"] is None or facts["worker_id"] is None
                    or facts["status"] not in {"running", "stopped"}
                    or facts["source"] != "direct_thread" or facts["reason"] is not None):
                return None
            # direct_thread evidence already checked this same request/controller/
            # direct binding and actual started thread, including a joined stop.
            session = self.owner.sessions.get(record.automation_id)
            worker = None if session is None else session.direct
            getter = getattr(worker, "read_diagnostic_for", None)
            if not callable(getter):
                return None
            value = getter(identity["probe_id"], facts["worker_id"])
            if type(value) is not dict:
                return None
            safe = CallDiagnostics()
            safe.capture(result=value)
            if safe.read_failure is None:
                return None
            result = {"read_failure": safe.read_failure}
            if safe.host_failure is not None:
                result["host_failure"] = safe.host_failure
            return result
        except Exception:
            return None  # Optional diagnosis never changes a successful inspection.

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
            raise DiagnosticError("reviewer_turn_changed")
        if finalizer is not None and (_terminal(turn) or turn.status in {"failed", "interrupted"}):
            finalizer.observe_reviewers(((reviewer, turn.turn_id, turn.status),))
        return turn

    def _preflight(self, task_id, references, metadata, paths, diagnostic):
        diagnostic.stage = "executor_admission"
        actual_turn = executor_turn_id(metadata)
        diagnostic.stage = "basis_before"
        reader = self._current(task_id, metadata)
        binding = reader()
        if binding.parent_thread_id != metadata["threadId"] or binding.task_id != task_id:
            raise DiagnosticError("binding_mismatch")
        diagnostic.stage = "request"
        references = reviewer_references(references)
        diagnostic.stage = "host_context"
        host = self._host(metadata, task_id, "pending", references)
        diagnostic.stage = "reviewer_resolution"
        reviewers = host.resolve_reviewer_ids() if any(r.startswith("/") for r in references) else references
        if len(set(reviewers)) != len(reviewers) or metadata["threadId"] in reviewers:
            raise DiagnosticError("invalid_child_set")
        diagnostic.stage = "finalization_basis"
        finalizer = self.owner._finalizer(binding)
        if finalizer is not None and len(reviewers) != len(finalizer.journal.read().result_paths):
            raise DiagnosticError("invalid_child_set")
        expected = {}
        diagnostic.stage = "request_read"
        request_path = paths.review_wait_request_store(metadata["threadId"], task_id)
        if path_lexically_exists(request_path):
            _, previous = RequestRepository(request_path).read()
            diagnostic.observe_record(previous)
            if (previous is not None and previous.basis_digest == basis_digest(binding)
                    and (previous.phase != "closed" or finalizer is not None)):
                expected = dict(previous.reviewers)
        else:
            diagnostic.observe_record(None)
        pairs = []
        ended = True
        for reviewer in reviewers:
            diagnostic.stage = "child_read"
            if finalizer is not None:
                diagnostic.local_state = "may_have_changed"
            turn = self._read_reviewer(host, reviewer, expected, finalizer)
            pairs.append((reviewer, turn.turn_id))
            ended = _terminal(turn) and ended
        diagnostic.stage = "parent_read"
        parent = host.read_parent()
        if (parent.child_id != metadata["threadId"] or parent.turn_id != actual_turn
                or parent.status != "inProgress" or parent.thread_status != "active"):
            raise DiagnosticError("parent_turn_changed")
        diagnostic.stage = "basis_after"
        if reader() != binding:
            raise DiagnosticError("binding_changed")
        return reader, binding, tuple(sorted(pairs)), host, ended, finalizer

    def _ready(self, task_id, record, *, replayed=False, diagnostic=None):
        diagnostic = diagnostic or CallDiagnostics()
        diagnostic.stage = "readiness"
        diagnostic.observe_record(record)
        session = self.owner.sessions.get(record.automation_id)
        if session is None or session.direct is None:
            return self._failure("review_wait_restart_requires_recovery")
        with diagnostic.reading_retained():
            result = session.direct._summary()
        state = result.get("state") or {}
        ready = (record.phase == "started" and state.get("status") == "waiting"
                 and state.get("timer_phase") == "active" and result.get("worker_alive") is True)
        if not ready:
            return self._failure()
        return {"ok": True, "status": "waiting", "task_id": task_id,
                "parent_may_end": True, "replayed": replayed}

    def _cleanup(self, repository, lease, record, task_id, metadata, paths, diagnostic):
        diagnostic.stage = "cleanup"
        diagnostic.cleanup = "unresolved"
        diagnostic.observe_record(record)
        if record.phase == "closed":
            diagnostic.cleanup = "confirmed"
            return True
        if record.phase in {"creating", "unknown", "closing"} or record.automation_id is None:
            return False
        path = paths.review_wait_store(record.automation_id)
        if marker_exists(path):
            with diagnostic.reading_retained():
                direct = DirectRepository.for_wait(path).read()
            # Unknown sending is still a competing effect even if its timer is gone.
            if direct.status in {"dispatching", "unknown"} or direct.timer_phase in {
                    "arming", "deleting", "pausing", "unknown"}:
                return False
            session = self.owner._session(path, task_id, record.automation_id, managed=True)
            diagnostic.local_state = "may_have_changed"
            diagnostic.host_mutation = "may_have_occurred"
            diagnostic.invalidate_record()
            result = session.handle("direct_cancel", {"probe_id": direct.probe_id}, metadata)
            diagnostic.observe_operation(result, "present_unresolved")
            if result.get("ok") is not True:
                return False
            with diagnostic.reading_retained():
                direct = DirectRepository.for_wait(path).read()
            # The worker may dispatch between the first read and cancellation.
            # Joining settles it; a deleted timer alone cannot settle its send.
            if (direct.status in {"dispatching", "unknown"}
                    or direct.timer_phase not in {"paused", "deleted"}):
                return False
        else:
            direct = None
        diagnostic.local_state = "may_have_changed"
        repository.advance(lease, "closing")
        try:
            if direct is None or direct.timer_phase != "deleted":
                host = self._host(metadata, task_id, record.automation_id, [r[0] for r in record.reviewers])
                before = host.view_heartbeat(for_pause=True)
                if before.status != "PAUSED":
                    raise DiagnosticError("heartbeat_changed")
                if path_lexically_exists(path):
                    with diagnostic.reading_retained():
                        original = ReviewWaitRepository.open_existing(path).read().controller.reservation
                    if (before.id != original.timer_id or before.identity_digest != original.identity_digest
                            or before.parent_thread_id != original.parent_thread_id):
                        raise DiagnosticError("heartbeat_changed")
                else:
                    # Before preparation only the fixed creation policy may own cleanup.
                    host.verify_created(before.rule)
                diagnostic.host_mutation = "may_have_occurred"
                host.delete_heartbeat(before, cleanup=True)
            repository.advance(lease, "closed")
            diagnostic.cleanup = "confirmed"
            diagnostic.retained_effects = "settled"
            if diagnostic.host_mutation == "may_have_occurred":
                diagnostic.host_mutation = "confirmed"
            return True
        except Exception:
            repository.advance(lease, "unknown")
            return False

    def handle(self, operation, arguments, metadata, paths, diagnostic=None):
        diagnostic = diagnostic or CallDiagnostics()
        diagnostic.stage = "request_read"
        task_id = arguments["task_id"]
        path = paths.review_wait_request_store(metadata["threadId"], task_id)
        if operation != "wait":
            if not path_lexically_exists(path):
                diagnostic.observe_record(None)
                diagnostic.stage = "inspection"
                return {"ok": True, "status": "absent", "task_id": task_id}
            repository = RequestRepository(path)
            if operation == "inspect":
                attempt, record = repository.read()
                diagnostic.observe_record(record)
                diagnostic.stage = "inspection"
                result = {"ok": True, "task_id": task_id, "phase": None if record is None else record.phase}
                if record is not None:
                    result["automation_id"] = record.automation_id
                    if record.automation_id is not None:
                        timer_path = paths.review_wait_store(record.automation_id)
                        if marker_exists(timer_path):
                            with diagnostic.reading_retained():
                                state = delivery_projection(DirectRepository.for_wait(timer_path).read())
                            result["delivery"] = state
                result["observation"] = self.observe_safely(task_id, metadata, paths, expected=(attempt, record))
                read_diagnostic = self._read_diagnostic(result["observation"], attempt, record,
                                                        task_id, metadata["threadId"])
                if read_diagnostic is not None:
                    result["read_diagnostic"] = read_diagnostic
                return result
            with diagnostic.retained_context(repository.serial()) as lease:
                attempt, record = repository.read()
                diagnostic.observe_record(record)
                if record is not None and not self._cleanup(repository, lease, record, task_id, metadata, paths, diagnostic):
                    diagnostic.reason = "cleanup_unresolved"
                    return {**self._failure(), "observation": self.observe_safely(
                        task_id, metadata, paths, expected=(attempt, record))}
                observed = self.observe_safely(task_id, metadata, paths, expected=(attempt, record))
            return {"ok": True, "status": "stopped", "task_id": task_id, "observation": observed}

        reader, binding, pairs, host, ended, finalizer = self._preflight(
            task_id, arguments["reviewer_ids"], metadata, paths, diagnostic)
        diagnostic.stage = "state_create"
        diagnostic.local_state = "may_have_changed"
        diagnostic.invalidate_record()
        self.owner._ensure_root(paths)
        repository = RequestRepository(path)
        with diagnostic.retained_context(repository.serial(create=True)) as lease:
            diagnostic.stage = "request_read"
            diagnostic.invalidate_record()
            _, previous = repository.read()
            diagnostic.observe_record(previous)
            digest = basis_digest(binding)
            current_turn = executor_turn_id(metadata)
            if previous is not None:
                if previous.basis_digest != digest or previous.reviewers != pairs:
                    if previous.phase != "closed":
                        return self._failure("review_wait_binding_changed")
                elif previous.parent_turn == current_turn:
                    return self._ready(task_id, previous, replayed=True, diagnostic=diagnostic)
                if not self._cleanup(repository, lease, previous, task_id, metadata, paths, diagnostic):
                    diagnostic.reason = "cleanup_unresolved"
                    return self._failure()
                if ended:
                    return {"ok": True, "status": "reviews_ended", "task_id": task_id, "parent_may_end": False}
            if len(self.owner.sessions) >= 64:
                return self._failure("session_limit")
            diagnostic.stage = "host_context"
            appointment = ten_minute_appointment(self.owner.clock or (lambda: datetime.now(timezone.utc)),
                                               self.owner.config.timezone)
            diagnostic.stage = "basis_after"
            if reader() != binding:
                return self._failure("review_wait_binding_changed")
            diagnostic.stage = "request_write"
            diagnostic.invalidate_record()
            repository.append(lease, RequestRecord(digest, current_turn, pairs))
            diagnostic.retained_effects = "present_unresolved"
            try:
                diagnostic.stage = "create"
                diagnostic.host_mutation = "may_have_occurred"
                automation = host.create_heartbeat(appointment.rule)
                diagnostic.host_mutation = "confirmed"
            except Exception as exc:
                diagnostic.capture(error=exc)
                repository.advance(lease, "unknown")
                return self._failure("heartbeat_create_unknown")
            record = repository.advance(lease, "created", automation_id=automation)
            try:
                diagnostic.stage = "verify_created"
                host.verify_created(appointment.rule)
                def prepare_reviewer_reader(current_binding, current_host, reviewer):
                    if basis_digest(current_binding) != digest:
                        raise DiagnosticError("binding_changed")
                    # The preparation reread is part of this same admission;
                    # retain its outcome before startup or another host read.
                    return self._read_reviewer(current_host, reviewer, dict(pairs), finalizer)
                session = self.owner._session(paths.review_wait_store(automation), task_id, automation,
                    managed=True, prepare_reviewer_reader=prepare_reviewer_reader)
                diagnostic.stage = "prepare"
                prepared = session.handle("prepare", {"reviewer_ids": [p[0] for p in pairs]}, metadata)
                if prepared.get("ok") is not True:
                    diagnostic.capture(result=prepared)
                    raise DiagnosticError("preparation_failed")
                with diagnostic.reading_retained():
                    controller = ReviewWaitRepository.open_existing(paths.review_wait_store(automation)).read().controller
                if (basis_digest(controller.binding) != digest
                        or tuple(sorted((r.reviewer_id, r.turn_id) for r in controller.reviewers)) != pairs):
                    raise DiagnosticError("binding_changed")
                record = repository.advance(lease, "prepared")
                diagnostic.stage = "start"
                diagnostic.host_mutation = "may_have_occurred"
                started = session.handle("direct_delete_start", {}, metadata)
                if (started.get("ok") is not True or started.get("worker_alive") is not True
                        or started.get("state", {}).get("status") != "waiting"
                        or started.get("state", {}).get("timer_phase") != "active"):
                    diagnostic.capture_start(started)
                    raise DiagnosticError("start_unavailable")
                diagnostic.host_mutation = "confirmed"
                record = repository.advance(lease, "started")
                return self._ready(task_id, record, diagnostic=diagnostic)
            except Exception as exc:
                if diagnostic.reason is None:
                    diagnostic.capture(error=exc)
                origin = diagnostic.stage, diagnostic.reason
                record = repository.advance(lease, "failed")
                try:
                    self._cleanup(repository, lease, record, task_id, metadata, paths, diagnostic)
                finally:
                    diagnostic.stage, diagnostic.reason = origin
                return self._failure()
