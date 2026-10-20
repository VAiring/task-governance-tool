"""Freeze per-round answer requests before publishing a continuation.

The transition's explicit protocol owns the choice. Missing collected state is
an error, never permission to reinterpret an issued request as legacy.
"""
from contextlib import closing, contextmanager, ExitStack
from hashlib import sha256
from types import SimpleNamespace

from . import review_handoff_transport as files
from . import review_handoff_preparation as preparation
from .review_answer_collection_repository import CollectionRepository
from .review_answer_values import basis_digest, validate_basis
from .review_preparation_repository import PreparationRepository
from .review_handoff_scope import resolve
from .review_wait_runtime.continuation_transition_repository import TransitionRepository
from .state_paths import path_lexically_exists


def choose_protocol(scope,packet):
    """Use the existing admitted resolution; never resolve another workspace."""
    from .setup_feature_config import answer_collection_allowed
    if not answer_collection_allowed(getattr(scope.paths,"skill_root",None)):
        return "legacy"
    if packet["task"]["review_tier"]==0 or packet.get("review_session_context",{}).get("execution_id") is None:
        return "legacy"
    from .storage import connect_initialized_readonly,current_schema_version
    with closing(connect_initialized_readonly(scope.target, operation_local=True)) as connection:
        if current_schema_version(connection)<30:
            files.fail("setup_required")
    return "review-answer-v1"


def requests_for(repo, root, record, transition, *, records_only=False):
    directory=transition.packet_path.rsplit("/",1)[0]
    generated=preparation._requests(repo,SimpleNamespace(directory=directory,reviewers=len(record.selected),
        records_only=records_only,task_id=record.basis["task_id"]),transition.packet_path,
        collected=transition.answer_protocol=="review-answer-v1")
    for request,choice in zip(generated["review_requests"],record.selected):
        request["session_id"]=choice.session_id
        request["request"]+=("\n\nExplicit continuation: "+choice.action+". Reason: "+choice.reason
            +"\nKeep the same reviewed basis. This explanation changes no acceptance or permission. If material/conditions changed, stop and report that a fresh target is needed.")
    return generated


def collection_path(paths, root, continuation_id):
    basis=root.basis
    return paths.review_answer_collection_store(basis["parent_session_id"],basis["task_id"],basis["target_generation"],root.preparation_id,continuation_id)


def expected_bases(repo, root, record, transition, raw, *, records_only=False):
    requests=requests_for(repo,root,record,transition,records_only=records_only)["review_requests"]
    identity=dict(continuation_id=record.continuation_id,sequence=record.sequence,selection_digest=transition.selection_digest)
    return tuple(validate_basis(dict(version=2,preparation_id=root.preparation_id,packet_digest="sha256:"+sha256(raw).hexdigest(),
        request_digest="sha256:"+sha256(request["request"].encode("utf-8")).hexdigest(),protocol="review-answer-v1",
        attestation="assigned-independent-review-v1",route=root.route,continuation=identity,execution_basis=record.basis)) for request in requests)


def issued_bases(root, transition, records):
    """Compare every source slot to its separately frozen issue-time digest."""
    bases=tuple(row["basis"] for row in records)
    identity=dict(continuation_id=transition.continuation_id,sequence=transition.continuation_sequence,
        selection_digest=transition.selection_digest)
    if (not transition.collection_basis_digests or len(bases)!=len(transition.result_paths)
        or tuple(basis_digest(row) for row in bases)!=transition.collection_basis_digests
        or any(row["execution_basis"]!=root.basis or row["preparation_id"]!=root.preparation_id
            or row["packet_digest"]!="sha256:"+transition.packet_digest or row["route"]!=root.route
            or row["continuation"]!=identity for row in bases)):
        files.fail("review_continuation_conflict")
    return bases


def initialize(scope, record, transition, raw):
    if transition.answer_protocol != "review-answer-v1":
        return ()
    if transition.phase in ("prepared","published"):
        located=locate_from_preparation(scope.repo,SimpleNamespace(paths=scope.paths,target=scope.target),scope.root,
            transition.packet_path,raw,files.decode_packet(raw),current=True)
        return located[2]
    repository=CollectionRepository(collection_path(scope.paths,scope.root,record.continuation_id))
    if transition.collection_basis_digests:
        # A settled round can already have pinned its request before a crash.
        # Never recreate that issued intent from today's request generator.
        expected=issued_bases(scope.root,transition,repository.read())
    else:
        expected=expected_bases(scope.repo,scope.root,record,transition,raw,records_only=getattr(scope,"records_only",False))
    def admit():
        from . import review_continuation_handoff_repository as core
        state=core.read_state(scope.target,record.basis["task_id"],scope.caller)
        group=[row for row in state.records if row.preparation_id==scope.root.preparation_id]
        if state.basis!=record.basis or not group or group[-1].continuation_id!=record.continuation_id or group[-1].state not in ("preparing","prepared","registered"):
            files.fail("review_continuation_basis_changed")
        if files.read_bounded(files.checked_path(scope.repo,transition.packet_path),files.PACKET_LIMIT)!=raw:
            files.fail("handoff_file_changed")
    if transition.phase in ("prepared","published") and not path_lexically_exists(repository.path):
        files.fail("review_continuation_effect_unknown")
    repository.initialize(expected,admit=admit)
    return expected


@contextmanager
def retained_admission(scope, continuation_id, entries):
    """Pin the exact source effects through the short native capture writer."""
    from .review_answer_handoff import service_for
    from .review_answer_values import metadata_digest, basis_digest
    groups={}
    resolution=SimpleNamespace(paths=scope.paths,target=scope.target)
    for consumer_slot,metadata in entries:
        service=service_for(scope.repo,resolution,scope.root,metadata["basis"],retained=True)
        groups.setdefault(str(service.repository.path),(service,[]))[1].append((consumer_slot,metadata))
    captures=[]
    with ExitStack() as stack:
        for key in sorted(groups):
            service,items=groups[key]
            items.sort(key=lambda item:item[1]["binding"]["slot"])
            slots=tuple(item[1]["binding"]["slot"] for item in items)
            _lease,sources=stack.enter_context(service.admission(slots))
            operational=service.repository.read()
            for (consumer_slot,metadata),(_raw,observed) in zip(items,sources):
                slot=metadata["binding"]["slot"]
                if observed!=metadata or operational[slot-1]["registration"]["state"]!="not_started":
                    files.fail("review_continuation_effect_unknown")
                source_cont=metadata["basis"]["continuation"]
                capture=dict(kind="collected",consumer_continuation_id=continuation_id,consumer_slot=consumer_slot,
                    source_preparation_id=metadata["basis"]["preparation_id"],source_continuation_id=None if source_cont is None else source_cont["continuation_id"],
                    source_slot=slot,session_id=metadata["binding"]["reviewer_session_id"],source_basis_digest=basis_digest(metadata["basis"]),
                    original_digest=metadata["original"]["digest"],metadata_digest=metadata_digest(metadata))
                captures.append((capture,metadata))
        yield tuple(sorted(captures,key=lambda item:item[0]["session_id"]))


def locate_continuation(repo, packet_path, packet_raw, packet, *, admitted=None, current=True):
    context=packet.get("review_session_context",{})
    if context.get("version")!=2:
        return None
    if admitted is not None:
        return locate_from_preparation(repo,*admitted,packet_path,packet_raw,packet,current=current)
    resolution=resolve(repo,writing=False)
    # The Packet identifies the owner only through its immutable preparation;
    # use the live basis reader, never infer it from the invoker's session.
    from .review_wait_basis_repository import read_review_basis
    from .storage import connect_initialized_readonly
    with closing(connect_initialized_readonly(resolution.target, operation_local=True, review_task_id=packet["task"]["task_id"])) as connection:
        basis=read_review_basis(connection,project_id=resolution.target.project.project_id,task_id=packet["task"]["task_id"])
    records,current_root=PreparationRepository(resolution.paths.review_preparation_store(basis["parent_session_id"],basis["task_id"],basis["target_generation"])).read()
    roots=[row for row in records if row.preparation_id==context["preparation_id"]]
    if len(roots)!=1 or current_root is None or current_root.preparation_id!=roots[0].preparation_id or roots[0].basis!=basis:
        files.fail("review_continuation_conflict")
    root=roots[0]
    return locate_from_preparation(repo,resolution,root,packet_path,packet_raw,packet,current=current)


def locate_from_preparation(repo,resolution,root,packet_path,packet_raw,packet,*,current=False):
    """Use an already admitted immutable root, including historical reporting."""
    context=packet.get("review_session_context",{})
    if context.get("version")!=2:
        return None
    basis=root.basis
    history=TransitionRepository(resolution.paths.review_continuation_store(basis["parent_session_id"],basis["task_id"],basis["target_generation"])).read()
    matches=[row for row in history if row.continuation_id==context["continuation_id"]]
    if len(matches)!=1:
        files.fail("review_continuation_conflict")
    transition=matches[0]
    if transition.answer_protocol=="legacy":
        return None
    from .review_continuation import basis_digest
    if (transition.phase not in ("prepared","published") or transition.packet_path!=packet_path or transition.packet_digest!=sha256(packet_raw).hexdigest()
        or transition.basis_digest!=basis_digest(basis) or context.get("project_id")!=basis["project_id"] or context.get("execution_id")!=basis["execution_id"]
        or transition.preparation_id!=root.preparation_id or transition.continuation_sequence!=context["continuation_sequence"]):
        files.fail("review_continuation_conflict")
    store=CollectionRepository(collection_path(resolution.paths,root,transition.continuation_id))
    collection=store.read()  # Absence/corruption stays closed; no initialization.
    bases=issued_bases(root,transition,collection)
    if current:
        from . import review_continuation_handoff_repository as core
        from .session_identity import CallerIdentity
        state=core.read_state(resolution.target,basis["task_id"],CallerIdentity(basis["parent_session_id"]))
        native=next((row for row in state.records if row.continuation_id==transition.continuation_id),None)
        if state.basis!=basis or native is None or native.basis!=basis:
            files.fail("review_continuation_conflict")
    return resolution,root,bases
