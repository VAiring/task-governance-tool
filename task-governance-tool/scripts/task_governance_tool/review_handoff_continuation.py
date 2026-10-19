"""One explicit exceptional continuation over an admitted physical preparation.

No host call, worker launch, core SQL, original rewrite or permission inference.
Known cleanup uses the existing stop service; unknown outcomes stay unavailable.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Iterator, Literal

from . import session_identity
from . import review_continuation as values, review_continuation_handoff_repository as core
from . import review_handoff_transport as files, review_handoff_preparation as preparation
from .review_handoff_scope import resolve, error_code as _code
from .review_preparation_repository import PreparationRecord, PreparationRepository
from .review_continuation_repository import Continuation
from .review_finalization_repository import FinalizationRepository
from .session_identity import CallerIdentity
from .state_resolver import CanonicalStatePaths
from .storage import DatabaseTarget
from .review_handoff_originals import compose_inventory, read_sources as _sources
from .review_session_transport import metadata_digest
from .review_packet import prepare_review_packet
from .state_paths import path_lexically_exists
from .review_wait_runtime.request_repository import RequestRecord, RequestRepository, ParentDispatchRepository, HistoricalRequestProof, request_identity_digest
from .review_wait_runtime.review_wait_repository import WriterLease
from .review_wait_runtime.continuation_transition_repository import TransitionRepository
from .review_wait_runtime.continuation_cleanup_repository import CleanupObservation, observe_cleanup, observe_current_occurrence, observe_latest_cleanup, read_bound_effects
from .review_wait_runtime.request_continuation import RequestRound
from .review_wait_runtime.review_wait_controller import WaitBinding
from .review_wait_runtime.managed_wait import basis_digest as wait_digest


@dataclass(frozen=True)
class ContinuationScope:
    repo: Path
    target: DatabaseTarget
    paths: CanonicalStatePaths
    caller: CallerIdentity
    root: PreparationRecord
    records_only: bool = False


@dataclass(frozen=True)
class IntegratedAdmission:
    """Borrowed only after the existing effect root has been admitted."""
    journal: FinalizationRepository
    lease: WriterLease


@dataclass(frozen=True)
class ContinuationLeases:
    """Borrowed from _admission_scope, never acquired or released by stages.

    That lexical owner also retains preparation and same-parent dispatch.
    Absence of integrated admission preserves the manual route.
    """
    transitions: TransitionRepository
    transition: WriterLease
    request: RequestRepository
    request_writer: WriterLease
    integrated: IntegratedAdmission | None


@dataclass(frozen=True)
class PreviousRequest:
    attempt: int
    record: RequestRecord | None


@dataclass(frozen=True)
class AdmissionSnapshot:
    state: core.HandoffState
    history: tuple[RequestRound, ...]
    previous: PreviousRequest


@dataclass(frozen=True)
class AdmittedContinuation:
    record: Continuation
    transition: RequestRound
    historical: HistoricalRequestProof | None
    replayed: bool


@dataclass(frozen=True)
class SettledContinuation:
    admitted: AdmittedContinuation
    transition: RequestRound


@dataclass(frozen=True)
class PacketPreparation:
    raw: bytes
    transition: RequestRound


@dataclass(frozen=True)
class ReadyContinuation:
    record: Continuation
    transition: RequestRound
    replayed: bool


@dataclass
class FailureProgress:
    """Only acknowledged local facts for the existing failure projection.

    Set at the original acknowledgement boundaries, including before a later
    check can fail. A lost write acknowledgement never proves absence and does
    not fabricate the unreturned record. This is not a readiness token.
    """
    stage: Literal["admission", "cleanup", "preparation"] = "admission"
    record: Continuation | None = None
    transition: RequestRound | None = None


def add_parser(commands):
    parser = commands.add_parser("continue", help="Continue only selected actual reviewers; preserve admitted results and known effects")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--target-generation", required=True, type=int)
    parser.add_argument("--directory", required=True, help="Unused ignored output directory; same request reconciles its saved location")


def _digest(basis):
    fields = {key: basis[key] for key in WaitBinding.__dataclass_fields__ if key not in ("wait_id", "parent_thread_id")}
    return wait_digest(WaitBinding(wait_id="continuation", parent_thread_id=basis["parent_session_id"], **fields))


def assemble_submission(repo, packet_path, originals, approvals=(), *, caller=None):
    """Read the complete selected/captured/retained inventory, without retyping."""
    caller = caller or session_identity.capture_caller_identity()
    resolution = resolve(repo, writing=False)
    raw = files.read_bounded(files.checked_path(repo, packet_path), files.PACKET_LIMIT)
    packet = files.decode_packet(raw)
    context = packet["review_session_context"]
    state = core.read_state(resolution.target, packet["task"]["task_id"], caller)
    record = next((row for row in state.records if row.continuation_id == context["continuation_id"]), None)
    if (record is None or record.state not in ("prepared", "registered") or record.basis != state.basis
        or record.preparation_id != context["preparation_id"] or record.sequence != context["continuation_sequence"]):
        files.fail("review_continuation_conflict")
    paths = resolution.paths
    locator = PreparationRepository(paths.review_preparation_store(caller.require(), state.basis["task_id"], state.basis["target_generation"]))
    with locator.serial():
        _, root = locator.read()
        if root.preparation_id != record.preparation_id or root.basis != record.basis:
            files.fail("review_continuation_conflict")
        history = TransitionRepository(paths.review_continuation_store(caller.require(), state.basis["task_id"], state.basis["target_generation"])).read()
        transition = next((row for row in history if row.continuation_id == record.continuation_id), None)
        if (transition is None or transition.phase not in ("prepared", "published") or transition.packet_path != packet_path
            or transition.packet_digest != sha256(raw).hexdigest() or tuple(originals) != transition.result_paths):
            files.fail("review_continuation_conflict")
        framed, _, _ = compose_inventory(repo, root, record, state, _sources(repo, root, history,resolution=resolution), packet_path, originals, approvals,resolution=resolution)
        if core.read_state(resolution.target, record.basis["task_id"], caller).basis != state.basis or files.read_bounded(files.checked_path(repo, packet_path), files.PACKET_LIMIT) != raw:
            files.fail("review_continuation_basis_changed")
        return record.basis["task_id"], framed


def _new_packet(repo, target, directory, count, raw):
    destination, missing, observed = preparation._directory(repo, directory)
    names = [directory + "/packet.json", *(directory + f"/review-{index}.json" for index in range(1, count + 1))]
    for path in names:
        files.require_ignored(repo, path)
    files.check_parents(observed)
    for path in missing:
        parents = files.observe_parents(path)
        files.require_ignored(repo, path.relative_to(repo).as_posix() + "/")
        path.mkdir() if os.name == "nt" else path.mkdir(mode=0o700)
        files.require_physical(path.lstat(), directory=True)
        files.check_parents(parents)
    packet_file = files.checked_path(repo, names[0])
    files.write_exclusive(packet_file, raw)
    if files.read_bounded(packet_file, files.PACKET_LIMIT) != raw:
        files.fail("handoff_file_changed")


def _response(repo, args, root, record, transition, *, replayed=False):
    from .review_answer_continuation_preparation import requests_for
    generated = requests_for(repo,root,record,transition,records_only=getattr(args,"records_only",False))
    tracking = False
    if transition.answer_protocol == "review-answer-v1":
        from .review_answer_values import basis_digest
        identity = dict(continuation_id=record.continuation_id,sequence=record.sequence,
            selection_digest=transition.selection_digest)
        def issued(version):
            return tuple(basis_digest(dict(version=version,preparation_id=root.preparation_id,
            packet_digest="sha256:"+transition.packet_digest,
            request_digest="sha256:"+sha256(request["request"].encode("utf-8")).hexdigest(),
            protocol="review-answer-v1",attestation="assigned-independent-review-v1",route=root.route,
            continuation=identity,execution_basis=record.basis)) for request in generated["review_requests"])
        tracking = issued(2) == transition.collection_basis_digests
        if not replayed and not tracking and issued(1) != transition.collection_basis_digests:
            files.fail("review_continuation_conflict")
    generated.update(continuation_id=record.continuation_id, continuation_sequence=record.sequence,
        preparation_id=root.preparation_id, route=root.route, basis=record.basis, replayed=replayed)
    if replayed:
        generated["review_requests"] = []  # Recovery is not redispatch authority.
    if tracking:
        generated["answer_collection_arm"] = dict(tool="review_answer_collect",
            arguments=dict(action="arm", task_id=record.basis["task_id"],
                reviewer_ids=[choice.session_id for choice in record.selected]),
            instruction="Before sending these continuation requests, call this arm operation once. It requires each dedicated reviewer to have ended its previous turn and excludes that turn. An existing arm is reused unchanged. After confirmed arming, send each request to its same reviewer and use the existing collect/wait route. Unknown send results do not authorize resending; collection proves only the observed post-boundary answer, not delivery of a particular message.")
    if root.route == "integrated":
        generated.pop("submit_command", None)
        generated["finalization_command"] = preparation._shell([sys.executable, "-B", str(preparation.Path(__file__).parent.parent / "review_handoff.py"),
            "finalize", "--repo=" + str(repo), "--task-id=" + args.task_id, "--target-generation=" + str(args.target_generation)])
    return {"ok": True, "command": "review.handoff.continue", "handoff": generated, "errors": []}


def _current_record(state, root, continuation_id, selected):
    """A historical successful operation is not a current preparation."""
    group = [row for row in state.records if row.preparation_id == root.preparation_id]
    record = next((row for row in group if row.continuation_id == continuation_id), None)
    if record is None:
        return None
    if (not group or group[-1] != record or record.state == "superseded"
        or record.basis != root.basis or state.basis != root.basis
        or record.route != root.route or record.selected != selected):
        files.fail("review_continuation_conflict")
    return record


def _require_transition(root, selected, transition, history):
    if (not history or history[-1] != transition
        or transition.preparation_id != root.preparation_id
        or transition.basis_digest != values.basis_digest(root.basis)
        or transition.wait_basis_digest != _digest(root.basis)
        or transition.selection_digest != values.selection_digest(selected)):
        files.fail("review_continuation_conflict")


def _read_packet(repo, root, record, transition):
    """Prove the physical original root and selected Packet; never recreate."""
    try:
        original = files.read_bounded(files.checked_path(repo, root.packet_path), files.PACKET_LIMIT)
        raw = files.read_bounded(files.checked_path(repo, transition.packet_path), files.PACKET_LIMIT)
    except FileNotFoundError:
        files.fail("handoff_file_changed")
    if sha256(original).hexdigest() != root.packet_digest:
        files.fail("handoff_file_changed")
    if transition.packet_digest is not None and sha256(raw).hexdigest() != transition.packet_digest:
        files.fail("handoff_file_changed")
    packet = files.decode_packet(raw, record.basis["task_id"])
    expected = dict(version=2, project_id=record.basis["project_id"], execution_id=record.basis["execution_id"],
        preparation_id=record.preparation_id, continuation_id=record.continuation_id, continuation_sequence=record.sequence,
        selected_reviewers=[dict(session_id=row.session_id, action=row.action, prior_receipt_id=row.prior_receipt_id)
            for row in record.selected])
    if (packet.get("review_session_context") != expected
        or packet["contract"]["revision"] != record.basis["contract_revision"]
        or any(packet["review_target"][key] != record.basis["target_" + key]
               for key in ("kind", "value", "base_revision", "generation"))):
        files.fail("review_continuation_conflict")
    return raw


def _request_round(request, transition, selected):
    """Read one coherent request snapshot and prove its current occurrence."""
    attempt, current, rounds = request.read_admission()
    if (not rounds or rounds[-1].continuation_id != transition.continuation_id
        or replace(rounds[-1], phase="prepared") != replace(transition, phase="prepared")):
        files.fail("review_continuation_conflict")
    round_record = rounds[-1]
    if attempt == transition.expected_attempt:
        if (request_identity_digest(current) if current else None) != transition.expected_request_digest:
            files.fail("review_continuation_conflict")
        if current is not None and current.phase != "closed":
            files.fail("review_continuation_effect_unknown")
    elif (attempt < transition.expected_attempt + 1 or current is None or round_record.phase != "published"
          or current.continuation_id != transition.continuation_id
          or current.continuation_sequence != transition.continuation_sequence
          or current.basis_digest != transition.wait_basis_digest
          or {pair[0] for pair in current.reviewers} != {row.session_id for row in selected}):
        files.fail("review_continuation_conflict")
    elif current.phase in ("creating", "closing", "unknown"):
        files.fail("review_continuation_effect_unknown")
    return round_record, current if attempt > transition.expected_attempt else None


def _ready(scope: ContinuationScope, leases: ContinuationLeases,
           selected: tuple[values.SelectedReviewer, ...], transition: RequestRound) -> Continuation:
    """One readiness predicate for first publication and every successful retry.

    Callers retain preparation/transition/request/dispatch/finalization leases.
    This renews native identity and physical bindings after the last write.
    """
    repo, target, paths, caller, root = scope.repo, scope.target, scope.paths, scope.caller, scope.root
    state = core.read_state(target, root.basis["task_id"], caller)
    record = _current_record(state, root, transition.continuation_id, selected)
    if record is None or record.state not in ("prepared", "registered") or transition.phase not in ("prepared", "published"):
        files.fail("review_continuation_pending")
    raw = _read_packet(repo, root, record, transition)
    if transition.answer_protocol == "review-answer-v1":
        from .review_answer_continuation_preparation import locate_from_preparation
        locate_from_preparation(repo,SimpleNamespace(target=target,paths=paths),root,transition.packet_path,raw,files.decode_packet(raw),current=True)
    _, occurrence = _request_round(leases.request, transition, selected)
    if occurrence is not None:
        observed = observe_current_occurrence(paths, caller.require(), root.basis["task_id"], occurrence)
        if observed.state != "known":
            files.fail(observed.reason)
    if leases.integrated is not None:
        from . import review_finalization
        journal = leases.integrated.journal
        expected = review_finalization.continuation_candidate(root, record, transition, raw, caller, journal)
        review_finalization.require_continuation_round(expected, journal)
    return record


def _settle_previous(scope: ContinuationScope, leases: ContinuationLeases,
                     previous: PreviousRequest, prior: RequestRound,
                     historical: HistoricalRequestProof | None) -> CleanupObservation:
    """Close only a proven old occurrence; no cancellation, join or host call.

    Pending retirement and registered workerless completion have distinct
    admission conditions in the caller. Both use this same cleanup/publication
    boundary, including a lost acknowledgement between its two local writes.
    """
    attempt, old_request = previous.attempt, previous.record
    observation = replace(prior, phase="fenced", packet_digest=None, collection_basis_digests=(), worker_stop_identity=None,
        expected_attempt=attempt, expected_request_digest=request_identity_digest(old_request) if old_request else None,
        old_automation_id=old_request.automation_id if old_request else None,
        old_request_phase=old_request.phase if old_request else None)
    cleanup = observe_cleanup(scope.paths, scope.caller.require(), scope.root.basis["task_id"], observation, historical=historical)
    if cleanup.state != "settled":
        files.fail(cleanup.reason)
    rounds = leases.request.read_rounds()
    if (not rounds or rounds[-1].continuation_id != prior.continuation_id
        or replace(rounds[-1], phase="prepared") != replace(prior, phase="prepared")):
        files.fail("review_continuation_conflict")
    if rounds[-1].phase == "prepared":
        leases.request.publish_without_wait(leases.request_writer, rounds[-1])
    if prior.phase == "prepared":
        leases.transitions.advance(leases.transition, prior, phase="published")
    return cleanup


def _retained_inventory(repo, root, history, state, selected, *, resolution=None):
    sources = _sources(repo, root, history,resolution=resolution)
    selected_actors = {choice.session_id for choice in selected}
    registered = {node.session_id for node in state.heads}
    if any(source.bound.binding.session_id in selected_actors - registered for source in sources):
        files.fail("review_continuation_original_registration_required")
    retained=[source for source in sources if source.bound.binding.session_id not in selected_actors | registered]
    actors=[source.bound.binding.session_id for source in retained]
    if len(set(actors))!=len(actors) or len(retained)+len(selected)>8:
        files.fail("review_continuation_conflict")
    retained.sort(key=lambda source:source.bound.binding.session_id)
    legacy=tuple(values.RetainedOriginal(source.bound.continuation.identity.continuation_id,
        source.bound.binding.session_id, source.bound.binding.original_result_digest, metadata_digest(source.metadata))
        for source in retained if getattr(source,"collected_metadata",None) is None and source.bound.continuation is not None)
    collected=tuple((len(selected)+index,source.collected_metadata) for index,source in enumerate(retained,1)
        if getattr(source,"collected_metadata",None) is not None)
    return legacy,collected


def _prior_admission(scope: ContinuationScope, leases: ContinuationLeases,
                     snapshot: AdmissionSnapshot,
                     historical: HistoricalRequestProof | None) -> values.RetirementProof | None:
    """Validate distinct pending-retirement / registered-round conditions."""
    root, state, history = scope.root, snapshot.state, snapshot.history
    old_request = snapshot.previous.record
    pending = [row for row in state.records if row.basis["target_generation"] == root.basis["target_generation"]
               and row.state in ("preparing", "prepared")]
    previous = history[-1] if history else None
    if pending:
        old_core = pending[0]
        prior = next((row for row in history if row.continuation_id == old_core.continuation_id), None)
        # Old binaries could admit core before saving a destination. Do not
        # invent its operation identity or escape into a different directory.
        if prior is None or prior.phase not in ("prepared", "published") or old_core.state != "prepared":
            files.fail("review_continuation_effect_unknown")
        _ready(scope, leases, old_core.selected, prior)
        cleanup = _settle_previous(scope, leases, snapshot.previous, prior, historical)
        return values.RetirementProof(old_core.continuation_id, values.basis_digest(root.basis),
            request_identity_digest(old_request) if old_request else None, cleanup.worker_stop_identity)
    if previous is not None and previous.phase != "published":
        old_core = _current_record(state, root, previous.continuation_id,
            next((row.selected for row in state.records if row.continuation_id == previous.continuation_id), ()))
        if old_core is None or old_core.state != "registered":
            files.fail("review_continuation_effect_unknown")
        _ready(scope, leases, old_core.selected, previous)
        _settle_previous(scope, leases, snapshot.previous, previous, historical)
    return None


def _historical_admission(scope: ContinuationScope, leases: ContinuationLeases,
                          observed: PreviousRequest,
                          transition: RequestRound | None) -> HistoricalRequestProof | None:
    """Prove older settled effects before first admission or exact recovery.

    A request journal spans target generations. Its old immutable basis is
    cleanup evidence only; the new round retains the current preparation basis.
    """
    target, paths, caller, root = scope.target, scope.paths, scope.caller, scope.root
    attempt, previous = observed.attempt, observed.record
    request, lease = leases.request, leases.request_writer
    rounds = request.read_rounds()
    if previous is None:
        if not rounds or rounds[-1].wait_basis_digest == _digest(root.basis):
            return None
        # An unused round can predate this target without ever admitting a
        # worker. Prove the actual empty journal, not a missing worker handle.
        if attempt != 0 or transition is not None and (
            transition.expected_attempt != 0 or transition.expected_request_digest is not None
            or transition.old_automation_id is not None):
            files.fail("review_continuation_conflict")
        cleanup = observe_latest_cleanup(paths, caller.require(), root.basis["task_id"], attempt, None)
        if cleanup.state != "settled":
            files.fail(cleanup.reason)
        from .review_wait_basis_repository import validate_historical_wait
        validate_historical_wait(target, root.basis, None, None, rounds)
        if rounds[-1].phase == "prepared":
            request.publish_without_wait(lease, rounds[-1])
        return None
    if previous.basis_digest == _digest(root.basis):
        return None
    if transition is not None:
        if any(row.continuation_id == transition.continuation_id for row in rounds):
            return None  # Already published; the ordinary readiness check owns it.
        if (attempt != transition.expected_attempt
            or request_identity_digest(previous) != transition.expected_request_digest):
            files.fail("review_continuation_conflict")
    cleanup = observe_latest_cleanup(paths, caller.require(), root.basis["task_id"], attempt, previous)
    if cleanup.state != "settled":
        files.fail(cleanup.reason)
    binding, direct = read_bound_effects(paths, caller.require(), root.basis["task_id"], previous)
    from .review_wait_basis_repository import validate_historical_wait
    validate_historical_wait(target, root.basis, binding, previous, rounds)
    if cleanup.worker_stop_identity is None or cleanup.worker_stop_identity != direct.worker_stop_identity:
        files.fail("review_continuation_worker_exit_unknown")
    proof = HistoricalRequestProof(attempt, request_identity_digest(previous), previous.automation_id,
        previous.basis_digest, _digest(root.basis), cleanup.worker_stop_identity)
    if rounds and rounds[-1].wait_basis_digest != proof.wait_basis_digest and rounds[-1].phase == "prepared":
        request.publish_without_wait(lease, rounds[-1])
    return proof


def _packet_for_preparation(scope: ContinuationScope, leases: ContinuationLeases,
                            settled: SettledContinuation) -> PacketPreparation:
    repo, target, root = scope.repo, scope.target, scope.root
    record, transition = settled.admitted.record, settled.transition
    transitions, lease = leases.transitions, leases.transition
    if transition.packet_digest is not None:
        return PacketPreparation(_read_packet(repo, root, record, transition), transition)
    packet = prepare_review_packet(target, record.basis["task_id"], _continuation_id=record.continuation_id)
    raw = json.dumps(packet, ensure_ascii=True, separators=(",", ":")).encode()
    files.decode_packet(raw, record.basis["task_id"])
    directory = transition.packet_path.rsplit("/", 1)[0]
    destination = files.relative_path(repo, directory)
    if path_lexically_exists(destination):
        # The persisted fence binds the only permitted path. Exact complete
        # bytes can reconnect a lost write acknowledgement; all other residue
        # remains in place and cannot be overwritten or replaced.
        if _read_packet(repo, root, record, transition) != raw:
            files.fail("handoff_file_changed")
        transition = transitions.bind_packet(lease, transition, sha256(raw).hexdigest())
    else:
        # Verification evidence includes its observation time. Bind the exact
        # bytes before saving instead of regenerating a different observation
        # after interruption. A retained file proves success; absent/unmatched
        # residue stays unknown, without recreating it from fresh observations.
        transition = transitions.bind_packet(lease, transition, sha256(raw).hexdigest())
        _new_packet(repo, target, directory, len(record.selected), raw)
    if _read_packet(repo, root, record, transition) != raw:
        files.fail("handoff_file_changed")
    return PacketPreparation(raw, transition)


def _complete_preparation(scope: ContinuationScope, leases: ContinuationLeases,
                          settled: SettledContinuation) -> ReadyContinuation:
    """Reconcile only missing local stages, then use the shared readiness test."""
    repo, target, caller, root = scope.repo, scope.target, scope.caller, scope.root
    record = settled.admitted.record
    rounds = leases.request.read_rounds()
    installed = next((row for row in rounds if row.continuation_id == record.continuation_id), None)
    packet = _packet_for_preparation(scope, leases, settled)
    raw, transition = packet.raw, packet.transition
    from .review_answer_continuation_preparation import initialize
    collection_bases = initialize(scope,record,transition,raw)
    if collection_bases:
        from .review_answer_values import basis_digest
        transition = leases.transitions.bind_collection_bases(leases.transition,transition,
            tuple(basis_digest(basis) for basis in collection_bases))
    fresh = core.read_state(target, root.basis["task_id"], caller)
    if _current_record(fresh, root, record.continuation_id, record.selected) is None:
        files.fail("review_continuation_conflict")
    if leases.integrated is not None:
        from . import review_finalization
        review_finalization.prepare_continuation(repo, root, record, transition, raw, caller,
            journal=leases.integrated.journal, lease=leases.integrated.lease)
    if transition.phase == "settled":
        transition = leases.transitions.advance(leases.transition, transition, phase="prepared",
            packet_path=transition.packet_path, packet_digest=sha256(raw).hexdigest(), result_paths=transition.result_paths)
    if record.state == "preparing":
        core.prepared(target, record, caller)
    if installed is None:
        leases.request.install_round(leases.request_writer, replace(transition, phase="prepared"),
            historical=settled.admitted.historical)
    record = _ready(scope, leases, record.selected, transition)
    return ReadyContinuation(record, transition, settled.admitted.replayed)


@contextmanager
def _admission_scope(repo: Path, args, caller: CallerIdentity
                     ) -> Iterator[tuple[ContinuationScope, ContinuationLeases, AdmissionSnapshot]]:
    """Own every lease from acquisition through response, including unwinding.

    Acquisition order: preparation -> transition -> request -> dispatch ->
    optional finalization. Stages borrow these permits; only repositories own
    short core writers. No stage acquires an earlier lease or performs host I/O.
    """
    resolution = resolve(repo, writing=True)
    initial = core.read_state(resolution.target, args.task_id, caller)
    if type(args.target_generation) is not int or args.target_generation != initial.basis["target_generation"]:
        files.fail("review_continuation_basis_changed")
    paths = resolution.paths
    locator = PreparationRepository(paths.review_preparation_store(caller.require(), args.task_id, args.target_generation))
    if not path_lexically_exists(locator.path):
        files.fail("review_continuation_unavailable")
    with ExitStack() as admissions:
        admissions.enter_context(locator.serial())
        _, root = locator.read()
        state = core.read_state(resolution.target, args.task_id, caller)
        if root.basis != initial.basis or state.basis != root.basis:
            files.fail("review_continuation_basis_changed")
        if root.route == "integrated" and getattr(args, "records_only", False):
            files.fail("review_continuation_unavailable")
        original = files.read_bounded(files.checked_path(repo, root.packet_path), files.PACKET_LIMIT)
        if sha256(original).hexdigest() != root.packet_digest:
            files.fail("handoff_file_changed")
        transitions = TransitionRepository(paths.review_continuation_store(caller.require(), args.task_id, args.target_generation))
        transition_lease = admissions.enter_context(transitions.serial(create=not path_lexically_exists(transitions.path)))
        history = transitions.read()
        request = RequestRepository(paths.review_wait_request_store(caller.require(), args.task_id))
        request_lease = admissions.enter_context(request.serial(create=not path_lexically_exists(request.path)))
        previous = PreviousRequest(*request.read())
        dispatch = ParentDispatchRepository(paths.review_wait_dispatch_store(caller.require()))
        admissions.enter_context(dispatch.serial())
        integrated = None
        if root.route == "integrated":
            from . import review_finalization
            journal = FinalizationRepository(paths.review_finalization_store(caller.require(), args.task_id, args.target_generation))
            if root.finalization_store != journal.path.name:
                files.fail("review_continuation_conflict")
            journal_lease = admissions.enter_context(journal.serial())
            review_finalization.require_preparation_intent(root, journal.read_rounds()[0], root.basis, caller)
            current = journal.read()
            if (any(value == "dispatching" for value in (current.registration, current.commit, current.completion))
                or current.completion != "not_started"):
                files.fail("review_continuation_effect_unknown")
            integrated = IntegratedAdmission(journal, journal_lease)
        yield (ContinuationScope(repo, resolution.target, paths, caller, root,getattr(args,"records_only",False)),
            ContinuationLeases(transitions, transition_lease, request, request_lease, integrated),
            AdmissionSnapshot(state, history, previous))


def _admit_operation(scope: ContinuationScope, leases: ContinuationLeases,
                     snapshot: AdmissionSnapshot, selected: tuple[values.SelectedReviewer, ...],
                     directory: str, progress: FailureProgress) -> AdmittedContinuation:
    """Reconcile the exact fence, predecessor and core admission, in that order."""
    root, state, history = scope.root, snapshot.state, snapshot.history
    attempt, old_request = snapshot.previous.attempt, snapshot.previous.record
    transition = next((row for row in history if row.preparation_id == root.preparation_id
                       and row.packet_path == directory + "/packet.json"), None)
    progress.transition = transition
    # A pre-Packet fence cannot have exposed executable requests. Once Packet
    # publication may have begun, retain no-redispatch even after a lost reply.
    replayed = transition is not None and not (
        transition.phase in ("fenced", "settled") and transition.packet_digest is None)
    record = None
    if transition is not None:
        _require_transition(root, selected, transition, history)
        record = _current_record(state, root, transition.continuation_id, selected)
        progress.record = record
        if record is None and transition.phase != "fenced":
            files.fail("review_continuation_effect_unknown")
    historical = _historical_admission(scope, leases, snapshot.previous, transition)
    if record is None:
        # A saved fence remains the operation identity even if native admission
        # did not commit or its acknowledgement was lost.
        predecessor_history = history[:-1] if transition is not None else history
        if transition is not None:
            if (attempt != transition.expected_attempt
                or (request_identity_digest(old_request) if old_request else None) != transition.expected_request_digest):
                files.fail("review_continuation_effect_unknown")
        else:
            if history and history[-1].phase in ("fenced", "settled"):
                files.fail("review_continuation_effect_unknown")
            preparation._directory(scope.repo, directory)
        captures,collected_sources = _retained_inventory(scope.repo, root, predecessor_history, state, selected,resolution=SimpleNamespace(paths=scope.paths,target=scope.target))
        retirement = _prior_admission(scope, leases, replace(snapshot, history=predecessor_history), historical)
        if transition is None:
            from .review_answer_continuation_preparation import choose_protocol
            original_packet=files.decode_packet(files.read_bounded(files.checked_path(scope.repo,root.packet_path),files.PACKET_LIMIT))
            answer_protocol=choose_protocol(scope,original_packet)
            if collected_sources and answer_protocol=="legacy":
                files.fail("review_continuation_unavailable")
            group = [row for row in state.records if row.preparation_id == root.preparation_id]
            sequence = group[-1].sequence + 1 if group else 1
            if sequence > values.CONTINUATION_LIMIT:
                files.fail("review_continuation_capacity")
            import secrets
            transition = leases.transitions.fence(leases.transition, RequestRound(root.preparation_id,
                "tg_review_continuation_" + secrets.token_hex(8), sequence, values.basis_digest(root.basis),
                _digest(root.basis), values.selection_digest(selected), attempt,
                request_identity_digest(old_request) if old_request else None,
                old_request.automation_id if old_request else None, old_request.phase if old_request else None,
                packet_path=directory + "/packet.json",
                result_paths=tuple(directory + f"/review-{index}.json" for index in range(1, len(selected) + 1)),answer_protocol=answer_protocol))
            progress.transition = transition
        if collected_sources and transition.answer_protocol=="legacy":
            files.fail("review_continuation_unavailable")
        from .review_answer_continuation_preparation import retained_admission
        with retained_admission(scope,transition.continuation_id,collected_sources) as collected:
            record = core.admit(scope.target, root.basis, root.preparation_id, root.route, selected, scope.caller,
                retirement=retirement, retained_originals=captures, retained_collected=collected,continuation_id=transition.continuation_id)
        progress.record = record
        if record.sequence != transition.continuation_sequence:
            files.fail("review_continuation_conflict")
    return AdmittedContinuation(record, transition, historical, replayed)


def _settle_cleanup(scope: ContinuationScope, leases: ContinuationLeases,
                    admitted: AdmittedContinuation) -> SettledContinuation:
    """Observe known cleanup only; stop/join remains outside these admissions."""
    transition = admitted.transition
    installed = next((row for row in leases.request.read_rounds()
                      if row.continuation_id == admitted.record.continuation_id), None)
    if installed is None:
        cleanup = observe_cleanup(scope.paths, scope.caller.require(), scope.root.basis["task_id"],
            transition, historical=admitted.historical)
        if cleanup.state != "settled":
            files.fail(cleanup.reason)
        if transition.phase == "fenced":
            transition = leases.transitions.advance(leases.transition, transition, phase="settled",
                worker_stop_identity=cleanup.worker_stop_identity)
    return SettledContinuation(admitted, transition)


def _failure_response(progress: FailureProgress, exc: Exception) -> dict:
    code = _code(exc)
    next_action = ("stop_known_wait_then_reconcile" if code == "review_continuation_cleanup_required"
        else "inspect_saved_packet" if code in ("handoff_file_changed", "handoff_path_unsafe")
        else "use_current_preparation" if code == "review_continuation_conflict"
        else "inspect_incomplete_admission" if progress.stage == "admission"
        else "inspect_reported_boundary")
    record, transition = progress.record, progress.transition
    return {"ok": False, "command": "review.handoff.continue",
        "handoff": {"status": "pending" if record or transition else "unavailable",
            "continuation_id": record.continuation_id if record else transition.continuation_id if transition else None,
            "preparation_stage": progress.stage, "next_action": next_action},
        "errors": [{"code": code, "message": "Continuation is not ready. Preserve saved records and reconcile only the reported boundary."}]}


def continue_reviews(repo, args, raw, *, caller=None):
    """Admit -> observe cleanup -> prepare -> shared readiness -> response.

    _admission_scope retains all leases through the ready response. Its unwind
    releases them before failure reporting. Stage values are invocation-local;
    retries derive them again from the original validated ledgers and files.
    """
    caller = caller or session_identity.capture_caller_identity()
    progress = FailureProgress()
    try:
        selected = values.decode_selection(raw)
        with _admission_scope(repo, args, caller) as (scope, leases, snapshot):
            admitted = _admit_operation(scope, leases, snapshot, selected, args.directory, progress)
            progress.stage = "cleanup"
            settled = _settle_cleanup(scope, leases, admitted)
            progress.transition = settled.transition
            progress.stage = "preparation"
            ready = _complete_preparation(scope, leases, settled)
            progress.record, progress.transition = ready.record, ready.transition
            return _response(repo, args, scope.root, ready.record, ready.transition, replayed=ready.replayed)
    except Exception as exc:
        return _failure_response(progress, exc)
