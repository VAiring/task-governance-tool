"""Publish a ready physical handoff's bounded locator; no core or host writes."""

from contextlib import closing, contextmanager, ExitStack
from hashlib import sha256
import secrets

from . import review_handoff_transport as files
from .review_handoff_scope import resolve
from .review_finalization_repository import FinalizationRepository
from .review_preparation_repository import PreparationRecord, PreparationRepository
from .review_wait_basis_repository import read_preparation_basis, read_review_basis
from .state_paths import create_physical_directory_exclusive, inspect_physical_directory, path_lexically_exists
from .storage import connect_initialized_readonly, current_schema_version, utc_now, validate_task_review_storage
from .verification_declaration import verification_requirement


def _basis(target, task_id, caller):
    with closing(connect_initialized_readonly(target, operation_local=True, review_task_id=task_id)) as connection:
        return read_preparation_basis(connection, project_id=target.project.project_id, task_id=task_id, caller=caller)


@contextmanager
def _replacement_collection_fence(paths, current):
    """Serialize replacement against the predecessor's source registration.

    Called under preparation admission, before collection leases. Finalization
    never acquires a preparation lease beneath its own lease; replacement waits
    on its existing source leases instead of reversing that lock order.
    """
    from .review_answer_collection_repository import CollectionRepository
    from .review_wait_runtime.continuation_transition_repository import TransitionRepository
    basis = current.basis
    stores = ([paths.review_answer_collection_store(basis["parent_session_id"], basis["task_id"],
        basis["target_generation"], current.preparation_id)] if current.version == 2 else [])
    transition_path = paths.review_continuation_store(basis["parent_session_id"], basis["task_id"],
        basis["target_generation"])
    if path_lexically_exists(transition_path):
        rounds = TransitionRepository(transition_path).read()
        # A currently selected batch can also consume an older unregistered
        # capture. Include every explicitly indexed collected source rather
        # than guessing only the latest round or scanning the filesystem.
        stores.extend(
            paths.review_answer_collection_store(basis["parent_session_id"], basis["task_id"],
                basis["target_generation"], current.preparation_id, row.continuation_id)
            for row in sorted(rounds, key=lambda row: row.continuation_id)
            if (row.preparation_id == current.preparation_id and row.answer_protocol == "review-answer-v1"
                and row.collection_basis_digests)
        )
    with ExitStack() as stack:
        if current.route == "integrated":
            path = paths.review_finalization_store(basis["parent_session_id"], basis["task_id"],
                basis["target_generation"])
            stack.enter_context(FinalizationRepository(path).serial())
        for path in stores:
            stack.enter_context(CollectionRepository(path).serial())
        yield


def publish_ready(repo, packet_path, result_paths, caller, *, route, collection_requests=()):
    """Called only after complete preparation, and after integrated intent creation.

    Schema 28 can publish the forward-compatible structural locator; it does
    not activate core continuation, migrate state, or alter the initial route.
    Failed publication retains residues and the previous pointer; it never
    claims readiness or retries a possibly created finalization intent.
    """
    resolution = resolve(repo, writing=True)
    with closing(connect_initialized_readonly(resolution.target, operation_local=True)) as connection:
        if current_schema_version(connection) < 28:
            return None
        raw = files.read_bounded(files.checked_path(repo, packet_path), files.PACKET_LIMIT)
        packet = files.decode_packet(raw)
        if (packet.get("review_session_context", {}).get("execution_id") is None
            or caller.session_id is None):
            return None  # Legacy absence remains absence, never inferred binding.
        validate_task_review_storage(connection, project_id=resolution.target.project.project_id,
            task_id=packet["task"]["task_id"], source_schema_version=current_schema_version(connection))
        basis = read_preparation_basis(connection, project_id=resolution.target.project.project_id,
            task_id=packet["task"]["task_id"], caller=caller)
    context = packet.get("review_session_context", {})
    if (packet["contract"]["revision"] != basis["contract_revision"]
        or context.get("version") != 1
        or packet["task"]["review_tier"] != basis["review_tier"]
        or verification_requirement(packet["task"]["verification"], packet["task"]["verification_not_required_reason"]) != basis["verification_requirement"]
        or packet["task"]["verification_not_required_reason"] != basis["verification_not_required_reason"]
        or any(packet["review_target"][key] != basis["target_" + key] for key in ("kind", "value", "base_revision", "generation"))
        or context.get("project_id") != basis["project_id"] or context.get("execution_id") != basis["execution_id"]):
        files.fail("review_continuation_basis_changed")
    for path in result_paths:
        if path_lexically_exists(files.checked_path(repo, path)):
            files.fail("handoff_destination_exists")
    paths = resolution.paths
    finalization_store = None
    if route == "integrated":
        finalization_path = paths.review_finalization_store(caller.require(), basis["task_id"], basis["target_generation"])
        intent = FinalizationRepository(finalization_path).read()
        expected = {key: value for key, value in basis.items() if key in intent.basis}
        expected["parent_thread_id"] = basis["parent_session_id"]
        if (any(intent.basis[key] != value for key, value in expected.items())
            or intent.packet_path != packet_path or intent.packet_digest != sha256(raw).hexdigest()
            or intent.result_paths != tuple(result_paths)
            or any(value != "not_started" for value in (intent.registration, intent.commit, intent.completion))
            or intent.candidate is not None):
            files.fail("review_continuation_conflict")
        finalization_store = finalization_path.name
    preparation_id = "tg_review_preparation_" + secrets.token_hex(8)
    collection_bases = ()
    if collection_requests:
        from .review_answer_preparation import PROTOCOL, ATTESTATION
        if (type(collection_requests) is not tuple or len(collection_requests) != len(result_paths)
            or any(type(request) is not str or not request for request in collection_requests)):
            files.fail("handoff_invalid_input")
        collection_bases = tuple(dict(version=2, preparation_id=preparation_id,
            packet_digest="sha256:" + sha256(raw).hexdigest(),
            request_digest="sha256:" + sha256(request.encode("utf-8")).hexdigest(),
            protocol=PROTOCOL, attestation=ATTESTATION, route=route,
            continuation=None, execution_basis=basis) for request in collection_requests)
    record = PreparationRecord(2 if collection_bases else 1, preparation_id, basis, route, packet_path,
        sha256(raw).hexdigest(), tuple(result_paths), finalization_store, utc_now(), collection_bases)
    if not path_lexically_exists(paths.review_wait_root):
        create_physical_directory_exclusive(paths.review_wait_root, root=paths.fixed_root)
    inspect_physical_directory(paths.review_wait_root, root=paths.fixed_root)
    journal = PreparationRepository(paths.review_preparation_store(caller.require(), basis["task_id"], basis["target_generation"]))
    fresh = not path_lexically_exists(journal.path)
    # Validate again under publication admission; another physical preparation
    # or core change cannot silently replace the admitted exact location.
    def admit():
        if _basis(resolution.target, basis["task_id"], caller) != basis or files.read_bounded(files.checked_path(repo, packet_path), files.PACKET_LIMIT) != raw:
            files.fail("review_continuation_basis_changed")
        for path in result_paths:
            if path_lexically_exists(files.checked_path(repo, path)):
                files.fail("handoff_destination_exists")
    if collection_bases:
        admit()
        from .review_answer_collection_repository import CollectionRepository
        CollectionRepository(paths.review_answer_collection_store(caller.require(), basis["task_id"],
            basis["target_generation"], preparation_id)).initialize(collection_bases)
    with journal.serial(initial=record if fresh else None, admit=admit) as lease:
        if not fresh:
            _, current = journal.read()
            with _replacement_collection_fence(paths, current):
                admit()
                journal.publish(lease, record)
    return {"preparation_id": record.preparation_id, "route": route}


@contextmanager
def save_admission(repo, packet_path, raw, packet, output, caller):
    """Serialize complete Save with continuation admission; no core write.

    Legacy absence remains the existing transport, with no inferred locator.
    A v2 original always requires the exact ready transition and pending actor.
    """
    context = packet.get("review_session_context")
    if context is None or context.get("execution_id") is None:
        yield
        return
    resolution = resolve(repo, writing=False)
    with closing(connect_initialized_readonly(resolution.target, operation_local=True, review_task_id=packet["task"]["task_id"])) as connection:
        if current_schema_version(connection) < 28:
            yield
            return
        basis = read_review_basis(connection, project_id=resolution.target.project.project_id, task_id=packet["task"]["task_id"])
    paths = resolution.paths
    path = paths.review_preparation_store(basis["parent_session_id"], basis["task_id"], packet["review_target"]["generation"])
    if not path_lexically_exists(path):
        if context["version"] == 2:
            files.fail("review_continuation_unavailable")
        yield
        return
    journal = PreparationRepository(path)
    with journal.serial():
        _, current = journal.read()
        if context["version"] == 1 and current.version == 2:
            files.fail("review_answer_save_not_allowed")
        with closing(connect_initialized_readonly(resolution.target, operation_local=True, review_task_id=packet["task"]["task_id"])) as connection:
            fresh = read_review_basis(connection, project_id=basis["project_id"], task_id=basis["task_id"])
            if fresh != basis or current.basis != basis:
                files.fail("review_continuation_basis_changed")
            if current_schema_version(connection) >= 29:
                from .review_continuation_repository import read_continuations
                history = read_continuations(connection, project_id=basis["project_id"], task_id=basis["task_id"])
            else:
                history = ()
        actor = caller.require()
        pending = [row for row in history if row.basis["target_generation"] == basis["target_generation"] and row.state in ("preparing", "prepared")]
        if context["version"] == 1:
            if (current.packet_path != packet_path or current.packet_digest != sha256(raw).hexdigest()
                or output not in current.result_paths or any(row.basis["target_generation"] == basis["target_generation"]
                    and actor in {choice.session_id for choice in row.selected} for row in history)):
                files.fail("review_packet_stale")
        else:
            matches = [row for row in pending if row.continuation_id == context["continuation_id"]]
            if (len(matches) != 1 or matches[0].state != "prepared" or current.preparation_id != context["preparation_id"]
                or matches[0].sequence != context["continuation_sequence"]):
                files.fail("review_packet_stale")
            record = matches[0]
            from .review_wait_runtime.continuation_transition_repository import TransitionRepository
            transitions = TransitionRepository(paths.review_continuation_store(basis["parent_session_id"], basis["task_id"], basis["target_generation"])).read()
            rounds = [row for row in transitions if row.continuation_id == record.continuation_id]
            actors = [choice.session_id for choice in record.selected]
            if (len(rounds) != 1 or rounds[0].phase not in ("prepared", "published") or actor not in actors
                or rounds[0].packet_path != packet_path or rounds[0].packet_digest != sha256(raw).hexdigest()
                or rounds[0].result_paths[actors.index(actor)] != output):
                files.fail("review_packet_stale")
            if rounds[0].answer_protocol == "review-answer-v1":
                files.fail("review_answer_save_not_allowed")
        yield
