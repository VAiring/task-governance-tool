"""Single-call wait orchestration; result interpretation remains with the parent.

Only explicit wait/stop mutate. An unresolved intent, restart or inspection
never restarts a worker or creates a competing reservation.
"""

from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class _PreparedReviewerIdentity:
    """Stored occurrence identity, not an observation of current turn status."""

    child_id: str
    turn_id: str


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
        if finalizer is not None:
            intent = finalizer.journal.read()
            if (len(reviewers) != len(intent.result_paths) or intent.continuation_id is not None
                and set(reviewers) != set(intent.active_sessions)):
                raise DiagnosticError("invalid_child_set")
        expected = {}
        continued = None
        historical = None
        current_round = None
        previous = None
        diagnostic.stage = "request_read"
        request_path = paths.review_wait_request_store(metadata["threadId"], task_id)
        if path_lexically_exists(request_path):
            _, previous, rounds = RequestRepository(request_path).read_admission()
            if rounds:
                continued = rounds[-1]
                if continued.wait_basis_digest != basis_digest(binding):
                    self.owner._continuation(binding, continued, historical=True)
                    historical, continued = continued, None
                    if finalizer is not None and intent.continuation_id is not None:
                        raise DiagnosticError("review_continuation_conflict")
                else:
                    current_round = self.owner._continuation(binding, continued)
                    if set(reviewers) != {row.session_id for row in current_round.selected}:
                        raise DiagnosticError("invalid_child_set")
                    if (finalizer is not None and (intent.continuation_id != current_round.continuation_id
                        or intent.continuation_sequence != current_round.sequence)):
                        raise DiagnosticError("review_continuation_conflict")
            diagnostic.observe_record(previous)
            if (previous is not None and previous.basis_digest == basis_digest(binding)
                    and (continued is None or previous.continuation_id == continued.continuation_id)
                    and (previous.phase != "closed" or finalizer is not None)):
                expected = dict(previous.reviewers)
        else:
            diagnostic.observe_record(None)
        self.owner._wait_round_current(binding, continued)
        answer_binding = getattr(self.owner, "_bind_answers", None)
        initial_binding_failure = None
        if callable(answer_binding):
            try:
                # This explicit wait continues the current dispatch flow. A
                # prior occurrence with this same basis is recovery, not fresh
                # context; missing bindings then cannot be reconstructed.
                fresh = previous is None or previous.basis_digest != basis_digest(binding)
                admitted_pairs = answer_binding(binding, host, reviewers, fresh_context=fresh, diagnostic=diagnostic)
                if admitted_pairs is not None:
                    if expected and expected != admitted_pairs:
                        raise DiagnosticError("reviewer_turn_changed")
                    expected = admitted_pairs
            except DiagnosticError:
                raise
            except Exception as error:
                # Collection failure must not disable the existing fallback
                # timer. The exact limitation accompanies this wait response.
                from task_governance_tool.review_answer import AnswerError
                diagnostic.answer_collection = {"collected": [], "unavailable": [
                    {"failure": error.failure if isinstance(error, AnswerError) else
                     {"class": "collection_unavailable", "field": None, "reason": "incomplete"}}]}
                if isinstance(error, AnswerError) and error.failure["reason"] == "initial_binding_interacted":
                    initial_binding_failure = diagnostic.answer_collection
        pairs = []
        tracking = {}
        tracking_reader = getattr(self.owner, "_answer_tracking", None)
        if callable(tracking_reader):
            tracking = tracking_reader(binding, tuple(reviewers))
        reuse = (current_round is not None and current_round.state == "registered") or (finalizer is not None and finalizer.saved_results_ready())
        if reuse and expected:
            # Qualified judgments need no new child observation. Retain only
            # this exact occurrence's saved identity for duplicate admission
            # and cleanup; these pairs are not evidence of current activity.
            if set(expected) != set(reviewers):
                raise DiagnosticError("invalid_child_set")
            pairs.extend(expected.items())
        ended = True
        for reviewer in (() if reuse else reviewers):
            diagnostic.stage = "child_read"
            if finalizer is not None:
                diagnostic.local_state = "may_have_changed"
            if reviewer in tracking:
                tracked = tracking[reviewer]
                if reviewer not in expected or tracked["boundary_turn_id"] != expected[reviewer]:
                    raise DiagnosticError("reviewer_turn_changed")
                if not tracked["frozen"]:
                    collected = self.owner._collect_answers(binding, host, (reviewer,), diagnostic=diagnostic)
                    if collected is not None:
                        diagnostic.answer_collection = initial_binding_failure or collected
                    tracked = tracking_reader(binding, tuple(reviewers))[reviewer]
                pairs.append((reviewer, expected[reviewer]))
                ready = tracked["frozen"] and tracked["terminal"] == "completed"
                if ready and finalizer is not None:
                    finalizer.observe_reviewers(((reviewer, tracked["source_identity"]["turn_id"], "completed"),))
                ended = ready and ended
                continue
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
        if ended and callable(getattr(self.owner, "_collect_answers", None)):
            try:
                collected = self.owner._collect_answers(binding, host, tuple(reviewers), diagnostic=diagnostic)
                if collected is not None:
                    diagnostic.answer_collection = initial_binding_failure or collected
            except Exception as error:
                from task_governance_tool.review_answer import AnswerError
                # A derived unbound-source error must not erase the observed cause.
                diagnostic.answer_collection = initial_binding_failure or {"collected": [], "unavailable": [
                    {"failure": error.failure if isinstance(error, AnswerError) else
                     {"class": "collection_unavailable", "field": None, "reason": "incomplete"}}]}
        return reader, binding, tuple(sorted(pairs)), host, ended, finalizer, continued, historical, tuple(reviewers)

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

        reader, binding, pairs, host, ended, finalizer, continued, historical, collection_reviewers = self._preflight(
            task_id, arguments["reviewer_ids"], metadata, paths, diagnostic)
        diagnostic.stage = "state_create"
        diagnostic.local_state = "may_have_changed"
        diagnostic.invalidate_record()
        self.owner._ensure_root(paths)
        repository = RequestRepository(path)
        with diagnostic.retained_context(repository.serial(create=True)) as lease:
            diagnostic.stage = "request_read"
            diagnostic.invalidate_record()
            sequence, previous, rounds = repository.read_admission()
            if (rounds[-1] if rounds else None) != (continued or historical):
                return self._failure("review_wait_binding_changed")
            if continued is not None:
                self.owner._continuation(binding, continued)
            elif historical is not None:
                self.owner._continuation(binding, historical, historical=True)
            self.owner._wait_round_current(binding, continued)
            diagnostic.observe_record(previous)
            digest = basis_digest(binding)
            current_turn = executor_turn_id(metadata)
            if previous is not None:
                if previous.basis_digest != digest:
                    from .continuation_cleanup_repository import observe_generation_cleanup
                    session = self.owner.sessions.get(previous.automation_id)
                    with diagnostic.reading_retained():
                        proof = observe_generation_cleanup(paths, metadata["threadId"], task_id,
                            sequence, previous, binding, owner=None if session is None else session.direct)
                    if proof.state != "settled":
                        diagnostic.invalidate_record()
                        raise DiagnosticError(proof.reason)
                elif previous.reviewers != pairs:
                    if previous.phase != "closed":
                        return self._failure("review_wait_binding_changed")
                elif previous.parent_turn == current_turn and (continued is None or previous.continuation_id == continued.continuation_id):
                    return self._ready(task_id, previous, replayed=True, diagnostic=diagnostic)
                if not self._cleanup(repository, lease, previous, task_id, metadata, paths, diagnostic):
                    diagnostic.reason = "cleanup_unresolved"
                    return self._failure()
                if previous.basis_digest != digest:
                    diagnostic.stage = "basis_after"
                    if reader() != binding:
                        return self._failure("review_wait_binding_changed")
                    self.owner._wait_round_current(binding, continued)
            if historical is not None and (previous is None or previous.basis_digest != digest):
                from .continuation_cleanup_repository import observe_latest_cleanup
                with diagnostic.reading_retained():
                    _, cleaned = repository.read()
                    session = None if cleaned is None else self.owner.sessions.get(cleaned.automation_id)
                    proof = observe_latest_cleanup(paths, metadata["threadId"], task_id, sequence, cleaned,
                        owner=None if session is None else session.direct)
                if proof.state != "settled":
                    diagnostic.invalidate_record()
                    raise DiagnosticError(proof.reason)
                if historical.phase == "prepared":
                    repository.publish_without_wait(lease, historical)
            elif historical is not None and historical.phase != "published":
                raise DiagnosticError("review_continuation_conflict")
            if ended:
                if continued is not None and continued.phase == "prepared":
                    repository.publish_without_wait(lease, continued)
                return {"ok": True, "status": "reviews_ended", "task_id": task_id, "parent_may_end": False}
            if continued is not None and continued.phase == "published":
                # A review continuation is distinct from each ten-minute wait
                # occurrence. Renew only the exact latest settled occurrence;
                # publication never authorizes changed turns or a new selection.
                if (previous is None or previous.continuation_id != continued.continuation_id
                    or previous.continuation_sequence != continued.continuation_sequence
                    or previous.basis_digest != digest or previous.reviewers != pairs
                    or previous.parent_turn == current_turn):
                    return self._failure("review_continuation_conflict")
                from .continuation_cleanup_repository import observe_latest_cleanup
                with diagnostic.reading_retained():
                    _, cleaned = repository.read()
                    proof = observe_latest_cleanup(paths, metadata["threadId"], task_id, sequence, cleaned)
                if proof.state != "settled":
                    diagnostic.invalidate_record()
                    raise DiagnosticError(proof.reason)
            elif continued is not None and continued.phase != "prepared":
                return self._failure("review_continuation_conflict")
            if len(self.owner.sessions) >= 64:
                return self._failure("session_limit")
            diagnostic.stage = "host_context"
            appointment = ten_minute_appointment(self.owner.clock or (lambda: datetime.now(timezone.utc)),
                                               self.owner.config.timezone)
            diagnostic.stage = "basis_after"
            if reader() != binding:
                return self._failure("review_wait_binding_changed")
            self.owner._wait_round_current(binding, continued)
            diagnostic.stage = "request_write"
            diagnostic.invalidate_record()
            repository.append(lease, RequestRecord(digest, current_turn, pairs,
                **({"continuation_id": continued.continuation_id, "continuation_sequence": continued.continuation_sequence} if continued else {})))
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
                    tracking_reader = getattr(self.owner, "_answer_tracking", None)
                    tracked = (tracking_reader(current_binding, collection_reviewers).get(reviewer)
                               if callable(tracking_reader) else None)
                    if tracked is not None:
                        # The controller pins the admitted tracking boundary,
                        # not the latest own turn. Collection alone decides
                        # whether a later completed answer is ready.
                        if tracked["boundary_turn_id"] != dict(pairs).get(reviewer):
                            raise DiagnosticError("reviewer_turn_changed")
                        return _PreparedReviewerIdentity(reviewer, tracked["boundary_turn_id"])
                    # The preparation reread is part of this same admission;
                    # retain its outcome before startup or another host read.
                    return self._read_reviewer(current_host, reviewer, dict(pairs), finalizer)
                session = self.owner._session(paths.review_wait_store(automation), task_id, automation,
                    managed=True, prepare_reviewer_reader=prepare_reviewer_reader,
                    collection_reviewers=collection_reviewers)
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
