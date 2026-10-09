"""One explicitly authorized fixed-target registration/commit/completion flow.

Public CLI handlers and their existing transactions remain the domain writers.
The helper and admitted wait worker provide genuine typed caller identity; no
environment replacement, permission inference, shell or coordinator model runs.
"""

import argparse
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from . import cli, review_handoff as files, review_finalization_git as git
from .project_scope import inspect_project_scope, STRUCTURAL_CODES
from .review_finalization_repository import FinalizationRecord, FinalizationRepository, registered_originals, registered_findings
from .review_finalization_basis import FinalizationBasis, require_no_findings
from .review_session_transport import decode_submission
from .review_wait_basis import wait_basis
from .session_identity import CallerIdentity
from .state_paths import create_physical_directory_exclusive, inspect_physical_directory, path_lexically_exists
from .state_resolver import resolve_project_state


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


def resolve(repo, *, writing):
    skill = Path(__file__).absolute().parents[2]
    inspection = inspect_project_scope(repo=repo, repo_explicit=True,
        script_path=skill / "scripts/review_handoff.py", include_runtime=False,
        include_package=False, include_ignore=writing)
    codes = STRUCTURAL_CODES | ({"state_ignore_required"} if writing else set())
    if inspection.first_issue(allowed_codes=codes) or inspection.scope is None:
        _fail("finalization_project_unavailable")
    resolution = resolve_project_state(skill_root=inspection.scope.skill_root, repo=inspection.scope.canonical_repo)
    if resolution.error_code or resolution.binding != "matching" or resolution.target is None:
        _fail("finalization_project_unavailable")
    return resolution


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
    packet_raw = files._read(files._path(repo, packet_path), files.PACKET_LIMIT)
    packet = files._packet(packet_raw)
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
    if wait_basis(repo, task_id=task_id) != observed or files._read(files._path(repo, packet_path), files.PACKET_LIMIT) != packet_raw:
        _fail("review_target_mismatch")
    paths = resolution.paths
    if not path_lexically_exists(paths.review_wait_root):
        create_physical_directory_exclusive(paths.review_wait_root, root=paths.fixed_root)
    inspect_physical_directory(paths.review_wait_root, root=paths.fixed_root)
    journal = FinalizationRepository(paths.review_finalization_store(actual, task_id, basis["target_generation"]))
    with journal.serial(initial=intent):
        pass
    return {"status": "prepared", "task_id": task_id, "target_generation": basis["target_generation"]}


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

    def notification_basis_valid(self):
        if self.journal is None:
            return False
        record = self.journal.read()
        self._match(record, self.core.read(self.task_id), allow_done=True)
        return True

    def observe_reviewers(self, observations=(), *, unavailable=False):
        """Retain admitted wait observations before another read or parent handoff.

        This records only structural facts in the existing intent; it never
        registers reviews, authorizes Git, or treats an unfinished child as failed.
        An unavailable initial read has no known turn to invent.
        """
        if self.journal is None:
            return
        with self.journal.serial() as lease:
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
        raw = files._read(files._path(self.repo, record.packet_path), files.PACKET_LIMIT)
        if sha256(raw).hexdigest() != record.packet_digest:
            _fail("review_packet_stale")
        available = []
        for index, path in enumerate(record.result_paths):
            try:
                _, framed = files.submission(self.repo, record.packet_path, [path])
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

    def execute(self, *, check=False, reviewer_observer=None, guard=lambda observe: None):
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
                if reviewer_observer is not None:
                    record = self.journal.observe_reviewers(lease, reviewer_observer())
                # Guard reads are fresh observations too. Retain each one before
                # a later child/parent read or cancellation can interrupt it.
                guard(retain_reviewer)
                if require_success and record.reviewer_blocker:
                    _fail(record.reviewer_blocker)
            if not check and record.completion != "succeeded":
                # Retain the reviewer failure even if originals or later host reads fail.
                guarded(require_success=False)
            available = self._originals(record)
            state = self.core.read(self.task_id)
            self._match(record, state, allow_done=True)
            if check:
                if record.reviewer_blocker:
                    _fail(record.reviewer_blocker)
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
                _, framed = files.submission(self.repo, record.packet_path, record.result_paths)
                submission = decode_submission(framed)
                if submission.bindings is None:
                    _fail("invalid_review_evidence")
                originals = tuple((b.binding.session_id, b.binding.original_result_digest) for b in submission.bindings)
                if not {item[0] for item in record.reviewer_observations} <= {item[0] for item in originals}:
                    record = self.journal.advance(lease, reviewer_blocker=record.reviewer_blocker or "finalization_reviewer_mismatch")
                    _fail("finalization_reviewer_mismatch")
                if record.originals and record.originals != originals:
                    _fail("handoff_file_changed")
                if record.registration == "not_started":
                    record = self.journal.advance(lease, registration="dispatching", originals=originals)
                ids = [item["review_receipt_id"] for item in state["review_evidence"]["current_receipts"]]
                found = registered_originals(self.resolution.target, record, ids)
                if not found:
                    guarded(require_success=False)
                    self._match(record, self.core.read(self.task_id))
                    self.core.register(self.task_id, submission, basis=FinalizationBasis.from_record(record))
                    state = self.core.read(self.task_id)
                    ids = [item["review_receipt_id"] for item in state["review_evidence"]["current_receipts"]]
                    found = registered_originals(self.resolution.target, record, ids)
                if len(found) != len(record.result_paths):
                    _fail("finalization_registration_unknown")
                record = self.journal.advance(lease, registration="succeeded", receipt_ids=found)
            state = self.core.read(self.task_id)
            self._match(record, state)
            guarded()
            evidence = state["review_evidence"]
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


def _code(exc):
    code = getattr(exc, "code", None)
    return code if type(code) is str and code and all(c.islower() or c.isdigit() or c == "_" for c in code) else "finalization_unavailable"


def report(record, state, available, error, gate):
    task = state["task"] if state else None
    completed = bool(task and task["status"] == "done" and record.completion == "succeeded")
    notes = [item for item in state["events"] if item["event_type"] == "note_added"] if state else []
    return {
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
        "registered_receipt_ids": list(record.receipt_ids), "completion_gate": gate,
        "commit_id": record.candidate if record.commit == "succeeded" else None,
        "candidate_commit_id": record.candidate, "task_status": task["status"] if task else None,
        "blocking_code": error, "unavailable": [] if state else ["current_task_and_evidence"],
        "next_action": "Report completion; do not repeat registration, commit or completion." if completed else
            "Review the retained failed, unavailable or changed reviewer turn; obtain fresh reviews with a new target generation and intent. Preserve registered results and successful stages." if record.reviewer_blocker else
            "Inspect the blocking code and available results; preserve successful stages. Use the retained finalization command for explicit recovery; do not reselect material or replay unknown Git/send effects.",
    }
