"""One explicitly authorized fixed-target registration/commit/completion flow.

Public CLI handlers and their existing transactions remain the domain writers.
The helper and admitted wait worker provide genuine typed caller identity; no
environment replacement, permission inference, shell or coordinator model runs.
"""

import argparse
from contextlib import closing
from dataclasses import replace
from hashlib import sha256

from . import review_handoff_originals as original_files
from . import cli, review_handoff_transport as files, review_finalization_git as git
from .review_handoff_scope import resolve, error_code as _code
from .review_finalization_repository import FinalizationRecord, FinalizationRepository, registered_originals, registered_findings
from .review_finalization_basis import FinalizationBasis, require_no_findings
from .review_session_transport import decode_submission, frame_submission
from .review_wait_basis import wait_basis
from .session_identity import CallerIdentity
from .state_paths import create_physical_directory_exclusive, inspect_physical_directory, path_lexically_exists


class FinalizationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise FinalizationError(code)


def _result(value):
    if not value.ok:
        _fail(value.errors[0]["code"] if value.errors else "finalization_unavailable")
    return value.data


class Core:
    def __init__(self, repo, target, caller):
        self.repo, self.target, self.caller = repo, target, caller
        self.maintenance_warnings = []

    def context(self, command, **arguments):
        return cli.CommandContext(command, str(self.repo), True, True, False,
                                  argparse.Namespace(**arguments), self.caller, self.target)

    def read(self, task_id):
        return _result(cli.handle_task_show(self.context("task.show", task_id=task_id)))

    def register(self, task_id, submission, *, basis):
        context = self.context("review.result.add", task_id=task_id, user_approved_reviewers=[])
        return self._maintain(context, cli.handle_review_command(context, submission=submission, finalization_basis=basis))

    def complete(self, task_id, candidate, *, basis, check=False):
        context = self.context("task.complete", task_id=task_id, check=check,
            verification_complete=True, review_complete=True, completion_evidence_kind="git_commit",
            completion_revision=candidate)
        result = cli.handle_task_complete(context, finalization_basis=basis)
        return _result(result) if check else self._maintain(context, result)

    def _maintain(self, context, result):
        _result(result)
        # Same bounded post-commit maintenance as the public CLI; no replay on
        # warning and no extra config or setup operation.
        maintained = cli.apply_post_commit_maintenance(context, replace(result, maintenance_target=self.target))
        self.maintenance_warnings.extend(maintained.warnings)
        return {**maintained.data, "maintenance_warnings": maintained.warnings}


def prepare_intent(repo, packet_path, result_paths, caller):
    """Called only by the explicit integrated preparation operation."""
    actual = caller.require()
    packet_raw = files.read_bounded(files.checked_path(repo, packet_path), files.PACKET_LIMIT)
    packet = files.decode_packet(packet_raw)
    task_id = packet["task"]["task_id"]
    observed = wait_basis(repo, task_id=task_id)
    if not observed["ok"]:
        _fail(observed["code"])
    basis = observed["basis"]
    if (basis["parent_thread_id"] != actual or basis["target_kind"] != "git_snapshot"
            or basis["contract_revision"] != packet["contract"]["revision"]
            or packet.get("review_session_context", {}).get("execution_id") != basis["execution_id"]
            or any(packet["review_target"][key] != basis["target_" + key]
                   for key in ("kind", "value", "base_revision", "generation"))):
        _fail("review_target_mismatch")
    expected_branch = git.branch(repo)
    git.matching_snapshot(repo, base=basis["target_base_revision"], fingerprint=basis["target_value"],
                          expected_branch=expected_branch)
    resolution = resolve(repo, writing=True)
    intent = FinalizationRecord(basis, packet_path, sha256(packet_raw).hexdigest(), tuple(result_paths), expected_branch)
    if wait_basis(repo, task_id=task_id) != observed or files.read_bounded(files.checked_path(repo, packet_path), files.PACKET_LIMIT) != packet_raw:
        _fail("review_target_mismatch")
    paths = resolution.paths
    if not path_lexically_exists(paths.review_wait_root):
        create_physical_directory_exclusive(paths.review_wait_root, root=paths.fixed_root)
    inspect_physical_directory(paths.review_wait_root, root=paths.fixed_root)
    journal = FinalizationRepository(paths.review_finalization_store(actual, task_id, basis["target_generation"]))
    with journal.serial(initial=intent):
        pass
    return {"status": "prepared", "task_id": task_id, "target_generation": basis["target_generation"]}


def require_preparation_intent(root, before, basis, caller):
    """Match the already admitted effect root; no route or permission inference."""
    if (before.packet_path != root.packet_path or before.packet_digest != root.packet_digest
        or before.result_paths != root.result_paths or any(before.basis[key] != basis[key] for key in
        ("project_id", "task_id", "execution_id", "ownership_generation", "contract_revision", "target_kind", "target_value",
         "target_base_revision", "target_generation", "artifact_manifest_id", "project_path_hash", "project_binding_generation"))
        or before.basis["parent_thread_id"] != caller.require()):
        _fail("review_continuation_basis_changed")


def continuation_candidate(root, record, transition, packet_raw, caller, journal):
    """Validate the existing effect root and derive this exact local round.

    Caller holds finalization admission; this does not acquire a lease or write.
    """
    if root.route != "integrated" or root.finalization_store != journal.path.name:
        _fail("review_continuation_unavailable")
    before = journal.read_rounds()[0]
    require_preparation_intent(root, before, record.basis, caller)
    current = journal.read()
    if (any(value == "dispatching" for value in (current.registration, current.commit, current.completion))
        or current.completion != "not_started"):
        _fail("review_continuation_effect_unknown")
    return FinalizationRecord(before.basis, transition.packet_path, sha256(packet_raw).hexdigest(),
        transition.result_paths, before.branch, continuation_id=record.continuation_id, continuation_sequence=record.sequence,
        preparation_id=record.preparation_id, active_sessions=tuple(row.session_id for row in record.selected))


def require_continuation_round(candidate, journal):
    """Current round identity, distinct from monotone registration/root effects."""
    current = journal.read()
    names = ("basis", "branch", "packet_path", "packet_digest", "result_paths", "continuation_id",
        "continuation_sequence", "preparation_id", "active_sessions")
    if any(getattr(current, name) != getattr(candidate, name) for name in names):
        _fail("review_continuation_conflict")
    return current


def prepare_continuation(repo, root, record, transition, packet_raw, caller, *, journal=None, lease=None):
    """Append only to the exact already authorized effect root after cleanup.

    The helper owns preparation/request admission and has confirmed settled
    worker/timer/send state. This performs no registration, Git or host effect.
    """
    if journal is None:
        resolution = resolve(repo, writing=True)
        path = resolution.paths.review_finalization_store(caller.require(), record.basis["task_id"], record.basis["target_generation"])
        if not path_lexically_exists(path):
            _fail("review_continuation_unavailable")
        journal = FinalizationRepository(path)
        with journal.serial() as acquired:
            return prepare_continuation(repo, root, record, transition, packet_raw, caller, journal=journal, lease=acquired)
    if lease is None:
        _fail("review_continuation_conflict")
    lease._check()
    candidate = continuation_candidate(root, record, transition, packet_raw, caller, journal)
    rounds = journal.read_rounds()
    existing = next((row for row in rounds if row.continuation_id == record.continuation_id), None)
    if existing is None:
        journal.append_round(lease, candidate)
    require_continuation_round(candidate, journal)


class Finalizer:
    def __init__(self, repo, task_id, caller, *, generation=None):
        self.repo, self.task_id, self.caller = repo, task_id, caller
        self.resolution = resolve(repo, writing=True)
        self.core = Core(repo, self.resolution.target, caller)
        if generation is None:
            generation = self.core.read(task_id)["task"]["review_target_generation"]
        self.path = self.resolution.paths.review_finalization_store(caller.require(), task_id, generation)
        self.journal = FinalizationRepository(self.path) if path_lexically_exists(self.path) else None

    def _match(self, record, state, *, allow_done=False):
        basis, task = record.basis, state["task"]
        if (self.caller.require() != basis["parent_thread_id"]
                or task["task_id"] != basis["task_id"] or task["project_id"] != basis["project_id"]
                or state["contract"]["revision"] != basis["contract_revision"]
                or any(task["review_target_" + key] != basis["target_" + key]
                       for key in ("kind", "value", "base_revision", "generation"))):
            _fail("review_target_mismatch")
        if task["status"] == "done":
            owner = task["ownership"]
            if not (allow_done and record.completion != "not_started" and record.commit == "succeeded"
                    and task["completion_evidence_kind"] == "git_commit"
                    and task["completion_evidence_revision"] == record.candidate
                    and owner["state"] == "none" and owner["execution_id"] == basis["execution_id"]
                    and owner["generation"] == basis["ownership_generation"] + 1):
                _fail("task_ownership_changed")
            # Re-resolve binding even after legitimate completion releases ownership.
            fresh = resolve(self.repo, writing=False)
            if (fresh.target.project.project_id != basis["project_id"]
                    or fresh.stored_project.canonical_path_hash != basis["project_path_hash"]
                    or fresh.stored_project.binding_generation != basis["project_binding_generation"]):
                _fail("review_target_mismatch")
            return
        current = wait_basis(self.repo, task_id=self.task_id)
        if not current["ok"] or any(current["basis"][key] != value for key, value in basis.items()
                                    if key != "task_status"):
            _fail("task_ownership_changed")
        # A durable fence can precede native admission after an interruption.
        # It already closes the old occurrence even when core has no new row.
        from .review_wait_runtime.continuation_transition_repository import TransitionRepository
        transition_path = self.resolution.paths.review_continuation_store(self.caller.require(), self.task_id, basis["target_generation"])
        if path_lexically_exists(transition_path):
            transitions = TransitionRepository(transition_path).read()
            if transitions and (transitions[-1].continuation_id != record.continuation_id
                or transitions[-1].phase not in ("prepared", "published")):
                _fail("review_continuation_conflict")
        # A newer explicit round fences this worker even after its new judgments
        # qualify. Reusing quality evidence never grants the old occurrence a
        # second Git/completion/send decision.
        from .storage import connect_initialized_readonly, current_schema_version
        from .review_continuation_repository import read_continuations
        with closing(connect_initialized_readonly(self.resolution.target)) as connection:
            if current_schema_version(connection) >= 29:
                rounds = read_continuations(connection, project_id=basis["project_id"], task_id=self.task_id)
                applicable = [row for row in rounds if row.basis["target_generation"] == basis["target_generation"]]
                if applicable and applicable[-1].continuation_id != record.continuation_id:
                    _fail("review_continuation_conflict")

    def notification_basis_valid(self):
        if self.journal is None:
            return False
        record = self.journal.read()
        self._match(record, self.core.read(self.task_id), allow_done=True)
        return True

    def _require_reviewer_history(self, record):
        """An explicit successor judgment can retire only its actual reviewer.

        Keep all old observations and blockers unchanged. A new current pointer
        alone, or an unrelated later PASS, never erases an execution failure.
        """
        if record.continuation_id is None:
            return
        from .review_continuation_handoff_repository import read_state
        native = read_state(self.resolution.target, self.task_id, self.caller)
        heads = {row.session_id: row for row in native.heads}
        continuations = {row.continuation_id: row for row in native.records}
        for previous in self.journal.read_rounds():
            if previous.continuation_id == record.continuation_id:
                break
            if previous.reviewer_blocker is None:
                continue
            failed = [item for item in previous.reviewer_observations if item[2] != "completed"]
            if not failed:
                # A changed turn can retain an earlier completed observation.
                # Only that mismatch may use exact bound current judgments.
                if (previous.reviewer_blocker != "finalization_reviewer_mismatch"
                    or not previous.reviewer_observations
                    or any(actor not in heads for actor, _, _ in previous.reviewer_observations)):
                    _fail(previous.reviewer_blocker)
                continue  # Ordinary verdict/Finding gates still run afterwards.
            for actor, _, _ in failed:
                head = heads.get(actor)
                successor = continuations.get(head.continuation_id) if head is not None else None
                if (successor is None or successor.state != "registered"
                    or successor.preparation_id != record.preparation_id or successor.basis != native.basis
                    or successor.sequence <= previous.continuation_sequence
                    or not any(choice.session_id == actor for choice in successor.selected)):
                    _fail(previous.reviewer_blocker)

    def _incidental_mismatch_ready(self, record, state=None):
        """Qualify a retained mismatch without clearing its historical code."""
        if (record.reviewer_blocker != "finalization_reviewer_mismatch"
            or record.registration != "succeeded" or not record.receipt_ids or not record.originals
            or not record.reviewer_observations
            or any(item[2] != "completed" for item in record.reviewer_observations)):
            return False
        state = state or self.core.read(self.task_id)
        self._match(record, state, allow_done=True)
        if not state["review_evidence"]["gate"]["satisfied"]:
            return False
        if state["task"]["status"] == "done" and record.completion == "succeeded":
            return True  # The locked completion already qualified this exact basis.
        from .review_continuation_handoff_repository import read_state
        native = read_state(self.resolution.target, self.task_id, self.caller)
        actors = {row.session_id for row in native.heads}
        return (all(actor in actors for actor, _, _ in record.reviewer_observations)
                and set(registered_originals(self.resolution.target, record, record.receipt_ids)) == set(record.receipt_ids))

    def _require_current_reviewers(self, record, state=None):
        if record.reviewer_blocker and not self._incidental_mismatch_ready(record, state):
            _fail(record.reviewer_blocker)

    def saved_results_ready(self):
        """Consult qualified native judgments before fresh reviewer activity."""
        if self.journal is None:
            return False
        record = self.journal.read()
        if record.registration != "succeeded" or record.reviewer_blocker not in (None, "finalization_reviewer_mismatch"):
            return False
        state = self.core.read(self.task_id)
        self._match(record, state, allow_done=True)
        if record.reviewer_blocker and not self._incidental_mismatch_ready(record, state):
            return False
        if state["task"]["status"] != "done":
            self._require_reviewer_history(record)
        return bool(state["review_evidence"]["gate"]["satisfied"])

    def observe_reviewers(self, observations=(), *, unavailable=False):
        """Retain admitted wait observations before another read or parent handoff.

        This records only structural facts in the existing intent; it never
        registers reviews, authorizes Git, or treats an unfinished child as failed.
        An unavailable initial read has no known turn to invent.
        """
        if self.journal is None:
            return
        with self.journal.serial(wait_for_observation=True) as lease:
            record = self.journal.read()
            if observations:
                record = self.journal.observe_reviewers(lease, observations)
            if unavailable and record.reviewer_blocker is None:
                self.journal.advance(lease, reviewer_blocker="finalization_reviewer_unknown")

    def record_notification_receipt(self, turn):
        """Associate only the real receipt turn; optional numerical failures do not block."""
        from .setup_feature_config import collection_allowed
        from .storage import connect_initialized_readonly
        from .usage_wait_attribution import WaitBasis, WaitObservation
        from .usage_wait_repository import wait_anchor
        from .usage_evidence_service import repository_for
        try:
            if not collection_allowed(self.resolution.paths.skill_root):
                return
            record = self.journal.read()
            basis = record.basis
            observation = WaitObservation(**{key: basis[key] for key in WaitBasis.__dataclass_fields__},
                                          thread_id=self.caller.require(), turn_id=turn)
            with closing(connect_initialized_readonly(self.resolution.target)) as core:
                if wait_anchor(core, observation) is None:
                    return
            repository_for(self.resolution.target).record_host_receipt(observation)
        except Exception:
            return  # Numerical availability is never a Task or send gate.

    def _originals(self, record):
        if record.continuation_id is not None:
            from .review_handoff_originals import read_original
            available = []
            locations = record.original_locations or tuple((record.packet_path, path) for path in record.result_paths)
            for index, (packet, path) in enumerate(locations):
                try:
                    source = read_original(self.repo, packet, path)
                    saved = source.bound.binding
                    if record.originals and record.originals[index] != (saved.session_id, saved.original_result_digest):
                        _fail("handoff_file_changed")
                    decoded = decode_submission(frame_submission(
                        [source.raw], [source.metadata], continuation=source.bound.continuation.identity if source.bound.continuation else None))
                    available.append({"path": path, "status": "available", "review": decoded.document["receipts"][0]})
                except Exception as exc:
                    available.append({"path": path, "status": "unavailable", "code": _code(exc)})
            return available
        raw = files.read_bounded(files.checked_path(self.repo, record.packet_path), files.PACKET_LIMIT)
        if sha256(raw).hexdigest() != record.packet_digest:
            _fail("review_packet_stale")
        available = []
        for index, path in enumerate(record.result_paths):
            try:
                _, framed = original_files.submission(self.repo, record.packet_path, [path])
                decoded = decode_submission(framed)
                if decoded.bindings is None or len(decoded.bindings) != 1:
                    _fail("invalid_review_evidence")
                saved = decoded.bindings[0].binding
                if record.originals and record.originals[index] != (saved.session_id, saved.original_result_digest):
                    _fail("handoff_file_changed")
                available.append({"path": path, "status": "available", "review": decoded.document["receipts"][0]})
            except Exception as exc:
                available.append({"path": path, "status": "unavailable", "code": _code(exc)})
        return available

    def _continuation_submission(self, record):
        """Compose the shared original inventory under finalization admission.

        Read structural locators only: never acquire preparation/request leases
        beneath the finalization lease. Native registration independently checks
        the pending round and full basis in its writer transaction.
        """
        from . import review_continuation_handoff_repository as continuation_core
        from .review_handoff_originals import compose_inventory, read_sources
        from .review_preparation_repository import PreparationRepository
        from .review_wait_runtime.continuation_transition_repository import TransitionRepository
        state = continuation_core.read_state(self.resolution.target, self.task_id, self.caller)
        current = next((row for row in state.records if row.continuation_id == record.continuation_id), None)
        if (current is None or current.state not in ("prepared", "registered") or current.basis != state.basis
            or current.preparation_id != record.preparation_id or current.sequence != record.continuation_sequence
            or current.route != "integrated"):
            _fail("review_continuation_conflict")
        paths = self.resolution.paths
        _, root = PreparationRepository(paths.review_preparation_store(self.caller.require(), self.task_id,
            record.basis["target_generation"])).read()
        initial = self.journal.read_rounds()[0]
        if (root.route != "integrated" or root.finalization_store != self.path.name
            or root.preparation_id != current.preparation_id or root.basis != current.basis
            or (root.packet_path, root.packet_digest, root.result_paths) != (initial.packet_path, initial.packet_digest, initial.result_paths)):
            _fail("review_continuation_conflict")
        history = TransitionRepository(paths.review_continuation_store(self.caller.require(), self.task_id,
            record.basis["target_generation"])).read()
        transition = next((row for row in history if row.continuation_id == record.continuation_id), None)
        raw = files.read_bounded(files.checked_path(self.repo, record.packet_path), files.PACKET_LIMIT)
        if (transition is None or transition.phase not in ("prepared", "published")
            or (transition.packet_path, transition.packet_digest, transition.result_paths) !=
               (record.packet_path, record.packet_digest, record.result_paths)
            or sha256(raw).hexdigest() != record.packet_digest):
            _fail("review_packet_stale")
        framed, sources, retained = compose_inventory(self.repo, root, current, state, read_sources(self.repo, root, history),
            record.packet_path, record.result_paths)
        return decode_submission(framed), tuple((source.packet_path, source.result_path) for source in sources), retained

    def execute(self, *, check=False, guard=lambda observe: None):
        if self.journal is None:
            _fail("finalization_not_prepared")
        available, state, error, gate = [], None, None, None
        def run(lease):
            nonlocal available, state, gate
            record = self.journal.read()
            def retain_reviewer(observation):
                nonlocal record
                observations = {item[:2]: item for item in record.reviewer_observations}
                observations[observation[:2]] = observation
                record = self.journal.observe_reviewers(lease, tuple(observations.values()))
            def guarded(*, require_success=True):
                nonlocal record
                # One fresh guard read serves both readiness and retention at
                # this boundary. Save each child before another read or halt.
                guard(retain_reviewer)
                if require_success:
                    self._require_current_reviewers(record)
            if not check and record.completion != "succeeded":
                # Retain the reviewer failure even if originals or later host reads fail.
                guarded(require_success=False)
            available = self._originals(record)
            state = self.core.read(self.task_id)
            self._match(record, state, allow_done=True)
            if check:
                self._require_current_reviewers(record, state)
                return
            if record.completion == "succeeded":
                return
            if state["task"]["status"] == "done":
                self.journal.advance(lease, completion="succeeded")
                return
            guarded(require_success=False)
            if record.registration != "succeeded":
                if any(item["status"] != "available" for item in available):
                    _fail("finalization_originals_unavailable")
                if record.continuation_id is not None:
                    submission, locations, retained = self._continuation_submission(record)
                else:
                    _, framed = original_files.submission(self.repo, record.packet_path, record.result_paths)
                    submission = decode_submission(framed)
                    locations, retained = (), ()
                if submission.bindings is None:
                    _fail("invalid_review_evidence")
                originals = tuple((b.binding.session_id, b.binding.original_result_digest) for b in submission.bindings)
                if not {item[0] for item in record.reviewer_observations} <= {item[0] for item in originals}:
                    record = self.journal.advance(lease, reviewer_blocker=record.reviewer_blocker or "finalization_reviewer_mismatch")
                    _fail("finalization_reviewer_mismatch")
                if record.originals and record.originals != originals:
                    _fail("handoff_file_changed")
                if record.registration == "not_started":
                    record = self.journal.advance(lease, registration="dispatching", originals=originals,
                        **({"original_locations": locations, "retained_receipt_ids": retained} if record.continuation_id else {}))
                ids = [item["review_receipt_id"] for item in state["review_evidence"]["current_receipts"]]
                found = registered_originals(self.resolution.target, record, ids)
                if not found:
                    guarded(require_success=False)
                    self._match(record, self.core.read(self.task_id))
                    self.core.register(self.task_id, submission, basis=FinalizationBasis.from_record(record))
                    state = self.core.read(self.task_id)
                    ids = [item["review_receipt_id"] for item in state["review_evidence"]["current_receipts"]]
                    found = registered_originals(self.resolution.target, record, ids)
                if len(found) != len(record.originals):
                    _fail("finalization_registration_unknown")
                record = self.journal.advance(lease, registration="succeeded", receipt_ids=found)
                if record.continuation_id:
                    available = self._originals(record)
            state = self.core.read(self.task_id)
            self._match(record, state)
            guarded()
            evidence = state["review_evidence"]
            self._require_reviewer_history(record)
            require_no_findings(evidence)
            if not evidence["gate"]["satisfied"]:
                _fail("review_required")
            if not state["verification_evidence"]["gate"]["satisfied"]:
                _fail(state["verification_evidence"]["gate"].get("blocking_code") or "verification_required")
            binding = dict(base=record.basis["target_base_revision"], fingerprint=record.basis["target_value"],
                           expected_branch=record.branch)
            if record.candidate is None:
                guarded()
                candidate = git.create_candidate(self.repo, **binding, task_id=self.task_id)
                record = self.journal.advance(lease, candidate=candidate)
            def ready():
                nonlocal gate
                guarded()
                fresh = self.core.read(self.task_id)
                self._match(record, fresh)
                require_no_findings(fresh["review_evidence"])
                gate = self.core.complete(self.task_id, record.candidate, basis=FinalizationBasis.from_record(record), check=True)
                if gate["ready"] is not True:
                    _fail(gate["blocking_codes"][0])
            if record.commit != "succeeded":
                if git.published(self.repo, candidate=record.candidate, **binding):
                    if record.commit != "dispatching":
                        _fail("finalization_commit_unexpected")
                else:
                    ready()
                    git.matching_snapshot(self.repo, **binding)
                    # Recovery has now observed the original base and exact index.
                    # Reuse the immutable candidate and the same old-ID CAS; even
                    # an earlier delayed writer cannot publish a second commit.
                    if record.commit == "not_started":
                        record = self.journal.advance(lease, commit="dispatching")
                    git.publish_candidate(self.repo, candidate=record.candidate, **binding, revalidate=ready)
                record = self.journal.advance(lease, commit="succeeded")
            ready()
            if record.completion == "not_started":
                record = self.journal.advance(lease, completion="dispatching")
            guarded()
            self.core.complete(self.task_id, record.candidate, basis=FinalizationBasis.from_record(record))
            state = self.core.read(self.task_id)
            self._match(record, state, allow_done=True)
            self.journal.advance(lease, completion="succeeded")
        try:
            if check:
                run(None)
            else:
                with self.journal.serial() as lease:
                    run(lease)
        except Exception as exc:
            error = _code(exc)
        # Report actual observed state even after an acknowledgement was lost.
        # Reading here does not replay a mutation or silently settle journal intent.
        try:
            state = self.core.read(self.task_id)
            self._match(self.journal.read(), state, allow_done=True)
        except Exception:
            state = None
        record = self.journal.read()
        result = report(record, state, available, error, gate)
        result["maintenance_warnings"] = self.core.maintenance_warnings
        try:
            ids = [item["review_receipt_id"] for item in state["review_evidence"]["current_receipts"]] if state else []
            actual = registered_originals(self.resolution.target, record, ids) if record.originals and state else ()
            result["observed_registered_receipt_ids"] = list(actual)
            result["registered_findings"] = registered_findings(self.resolution.target, record, actual)
            result["originals"] = project_original_summaries(result, record, actual)
        except Exception:
            result["observed_registered_receipt_ids"] = None
            result["registered_findings"] = None
            result["unavailable"].append("registered_reviews_and_findings")
        result["commit_observation"] = "not_attempted" if record.candidate is None else "unavailable"
        if record.candidate:
            binding = dict(base=record.basis["target_base_revision"], fingerprint=record.basis["target_value"],
                           expected_branch=record.branch)
            try:
                if git.published(self.repo, candidate=record.candidate, **binding):
                    result["commit_observation"] = "published"
                    result["commit_id"] = record.candidate
                else:
                    git.matching_snapshot(self.repo, **binding)
                    result["commit_observation"] = "not_published"
            except Exception:
                result["unavailable"].append("current_git_publication")
        return result


def project_original_summaries(result, record, matched_receipt_ids):
    """Reference only exact summaries with already observed immutable bindings.

    No state read or original rewrite: provenance, Findings and unavailable or
    unequal originals remain visible. Pointers resolve within this response.
    """
    originals = result["originals"]
    paths = tuple(row[1] for row in record.original_locations) if getattr(record, "continuation_id", None) and record.original_locations else record.result_paths
    if (len(matched_receipt_ids) != len(paths)
            or len(originals) != len(paths)
            or len(set(matched_receipt_ids)) != len(matched_receipt_ids)
            or result["reviews"] is None):
        return originals
    receipts = result["reviews"]["current_receipts"]
    projected = []
    for path, original, receipt_id in zip(paths, originals, matched_receipt_ids):
        matches = [(index, receipt) for index, receipt in enumerate(receipts)
                   if receipt["review_receipt_id"] == receipt_id]
        review = original.get("review")
        if (original.get("path") == path and original.get("status") == "available"
                and review is not None and len(matches) == 1):
            index, receipt = matches[0]
            if all(review.get(source) == receipt[target] for source, target in (
                    ("reviewer", "reviewer_key"), ("kind", "receipt_kind"),
                    ("verdict", "verdict"), ("summary", "summary"))):
                review = {key: value for key, value in review.items() if key != "summary"}
                review["summary_ref"] = f"/reviews/current_receipts/{index}/summary"
                original = {**original, "registered_receipt_id": receipt_id, "review": review}
        projected.append(original)
    return projected


def report(record, state, available, error, gate):
    task = state["task"] if state else None
    completed = bool(task and task["status"] == "done" and record.completion == "succeeded")
    notes = [item for item in state["events"] if item["event_type"] == "note_added"] if state else []
    return {
        **({"continuation_id": record.continuation_id, "continuation_sequence": record.continuation_sequence}
           if getattr(record, "continuation_id", None) else {}),
        "ok": error is None, "status": "completed" if completed else "attention_required",
        "task_id": record.basis["task_id"], "target": {key: record.basis[key] for key in
            ("contract_revision", "target_kind", "target_value", "target_base_revision", "target_generation")},
        "task_title": task["title"] if task else None,
        "recorded_work": ({"status": "recorded" if state["latest_checkpoint"] or notes else "not_recorded",
            "checkpoint": state["latest_checkpoint"], "recent_notes": notes,
            "coverage": "latest_checkpoint_and_recent_task_events"} if state else None),
        "verification": state["verification_evidence"] if state else None,
        "reviews": state["review_evidence"] if state else None,
        "originals": available,
        "reviewer_states": {item[0]: item[2] for item in record.reviewer_observations} or None,
        "reviewer_observations": [{"reviewer_id": item[0], "turn_id": item[1], "status": item[2]}
                                  for item in record.reviewer_observations],
        "reviewer_blocking_code": record.reviewer_blocker,
        "stages": {key: getattr(record, key) for key in ("registration", "commit", "completion")},
        "registered_receipt_ids": list(record.receipt_ids),
        "completion_gate": ({"observation": "before_completion", **{
            key: value for key, value in gate.items() if key != "suggested_action"}} if gate is not None else None),
        "commit_id": record.candidate if record.commit == "succeeded" else None,
        "candidate_commit_id": record.candidate, "task_status": task["status"] if task else None,
        "blocking_code": error, "unavailable": [] if state else ["current_task_and_evidence"],
        "next_action": "Report completion; do not repeat registration, commit or completion." if completed else
            "Inspect the retained reviewer observations and exact judgments. For unchanged material and conditions, use the admitted explicit same-basis continuation for only needed reviewers; preserve history and successful stages. Changed material or conditions require a fresh target and intent; diagnose unknown effects before replacement." if record.reviewer_blocker or error in ("finalization_reviewer_failed", "finalization_reviewer_unknown", "finalization_reviewer_mismatch") else
            "Inspect the blocking code and available results; preserve successful stages. Use the retained finalization command for explicit recovery; do not reselect material or replay unknown Git/send effects.",
    }
