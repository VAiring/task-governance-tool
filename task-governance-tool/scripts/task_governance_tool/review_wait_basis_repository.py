"""Read one current wait basis from an admitted query-only core snapshot.

No caller identity, numerical state or scheduling is consumed here. Existing
Task, ownership, authority, manifest and Reference validators own their formats.
"""

from task_governance_tool.contracts import read_current_contract
from task_governance_tool.evidence_repository import (
    _validated_authority_context, _validate_artifact_manifest_storage,
    validate_manifest_evidence_references,
)
from task_governance_tool.project_binding_repository import read_project_binding_state
from task_governance_tool.task_ownership import read_basis
from task_governance_tool.tasks import read_internal_task


class WaitBasisError(ValueError):
    def __init__(self, code="wait_basis_unavailable"):
        self.code = code
        super().__init__(code)


def read_wait_basis(connection, *, project_id, task_id):
    """The caller retains the resolver's validated read transaction throughout."""
    if (not connection.in_transaction
            or connection.execute("PRAGMA query_only").fetchone()[0] != 1):
        raise WaitBasisError()
    project = read_project_binding_state(connection, expected_project_id=project_id)
    task = read_internal_task(connection, project_id, task_id)
    if task is None:
        raise WaitBasisError()
    owner = read_basis(connection, project_id=project_id, task_id=task_id)
    if owner.state not in ("owned", "completion_only") or owner.execution_id is None:
        raise WaitBasisError("wait_basis_inactive")
    contract = read_current_contract(connection, project_id=project_id, task_id=task_id,
                                     current_revision=task["current_contract_revision"])
    manifest_id = task["review_target_artifact_manifest_id"]
    snapshot_id = task["review_target_authority_snapshot_id"]
    if (task["review_target_capture_version"] != 1 or manifest_id is None
            or snapshot_id is None or task["review_target_generation"] <= 0):
        raise WaitBasisError("wait_basis_target_required")
    authority = _validated_authority_context(connection, snapshot_ids={snapshot_id})
    manifests, _ = _validate_artifact_manifest_storage(
        connection, snapshots=authority.snapshots, links=authority.links,
        manifest_ids={manifest_id})
    validate_manifest_evidence_references(connection, manifests=manifests,
                                          selected_project_id=project_id)
    record = manifests.get(manifest_id)
    snapshot = authority.snapshots.get(snapshot_id)
    if (record is None or snapshot is None or snapshot["contract_revision"] != contract["revision"]
            or snapshot["project_id"] != project_id or snapshot["task_id"] != task_id
            or record.row["project_id"] != project_id or record.row["task_id"] != task_id
            or record.row["authority_snapshot_id"] != snapshot_id
            or any(record.row[key] != task["review_target_" + key] for key in
                   ("acceptance_criterion_id", "verification_criterion_id"))
            or any(record.row["target_" + key] != task["review_target_" + key] for key in
                   ("kind", "value", "base_revision", "generation"))):
        raise WaitBasisError()
    return {
        "version": 1, "project_id": project.project_id,
        "project_path_hash": project.canonical_path_hash,
        "project_binding_generation": project.binding_generation,
        "task_id": task_id, "task_status": task["status"],
        "execution_id": owner.execution_id, "ownership_generation": owner.generation,
        "parent_thread_id": owner.owner_session_id or owner.completion_session_id,
        "contract_revision": contract["revision"],
        "target_kind": task["review_target_kind"], "target_value": task["review_target_value"],
        "target_base_revision": task["review_target_base_revision"],
        "target_generation": task["review_target_generation"], "artifact_manifest_id": manifest_id,
    }


def read_review_basis(connection, *, project_id, task_id):
    """Observe full native identity; this read grants no caller/worker authority."""
    from .review_continuation import validate_basis
    from .verification_declaration import verification_requirement
    wait = read_wait_basis(connection, project_id=project_id, task_id=task_id)
    if wait["task_status"] not in ("in_progress", "review_pending"):
        raise WaitBasisError("task_ownership_changed")
    task = read_internal_task(connection, project_id, task_id)
    return validate_basis({
        **{key: value for key, value in wait.items() if key not in ("task_status", "parent_thread_id")},
        "parent_session_id": wait["parent_thread_id"],
        "authority_snapshot_id": task["review_target_authority_snapshot_id"],
        "acceptance_criterion_id": task["review_target_acceptance_criterion_id"],
        "verification_criterion_id": task["review_target_verification_criterion_id"],
        "review_tier": task["review_tier"],
        "verification_requirement": verification_requirement(task["verification"], task["verification_not_required_reason"]),
        "verification_not_required_reason": task["verification_not_required_reason"],
        "completion_commit_required": task["completion_commit_required"],
    })


def read_preparation_basis(connection, *, project_id, task_id, caller):
    """Read the full retained basis and compare the actual preparation caller."""
    basis = read_review_basis(connection, project_id=project_id, task_id=task_id)
    if caller.require() != basis["parent_session_id"]:
        raise WaitBasisError("task_ownership_changed")
    return basis


def validate_historical_wait(target, current, binding, request, rounds):
    """Prove an old operational association without making it current evidence.

    Ordinary attempts have no native continuation row. Their immutable target
    manifest/authority and actual owner transition still establish the native
    association. Continued attempts additionally require their own native round.
    With no attempt, only an exact historical native round is consumed; no
    controller, worker or cleanup proof is invented for that empty journal.
    """
    from contextlib import closing
    from .storage import connect_initialized_readonly
    from .review_continuation import basis_digest, selection_digest
    from .review_continuation_repository import read_continuations
    from .review_wait_runtime.managed_wait import basis_digest as wait_digest
    from .review_wait_runtime.review_wait_controller import WaitBinding

    def reject():
        raise WaitBasisError("review_continuation_basis_changed")

    def historical_identity(old):
        return (old["target_generation"] < current["target_generation"]
            and all(old[key] == current[key] for key in (
                "project_id", "project_path_hash", "project_binding_generation", "task_id", "parent_session_id")))

    with closing(connect_initialized_readonly(target)) as connection:
        if read_review_basis(connection, project_id=current["project_id"], task_id=current["task_id"]) != current:
            reject()
        if request is None:
            if binding is not None or not rounds:
                reject()
        else:
            old = {key: getattr(binding, key) for key in WaitBinding.__dataclass_fields__ if key != "wait_id"}
            old["parent_session_id"] = old.pop("parent_thread_id")
            if (not historical_identity(old) or binding.ownership_generation > current["ownership_generation"]
                or wait_digest(binding) != request.basis_digest):
                reject()
            pointer = connection.execute("SELECT authority_snapshot_id FROM artifact_manifests WHERE artifact_manifest_id=?",
                (binding.artifact_manifest_id,)).fetchone()
            if pointer is None:
                reject()
            authority = _validated_authority_context(connection, snapshot_ids={pointer["authority_snapshot_id"]})
            manifests, _ = _validate_artifact_manifest_storage(connection, snapshots=authority.snapshots,
                links=authority.links, manifest_ids={binding.artifact_manifest_id})
            validate_manifest_evidence_references(connection, manifests=manifests, selected_project_id=current["project_id"])
            manifest = manifests[binding.artifact_manifest_id].row
            snapshot = authority.snapshots[pointer["authority_snapshot_id"]]
            if (any(manifest[key] != old[key] for key in ("project_id", "task_id", "target_kind", "target_value",
                    "target_base_revision", "target_generation"))
                or any(snapshot[key] != old[key] for key in ("project_id", "task_id", "contract_revision"))):
                reject()
            owner = connection.execute(
                "SELECT 1 FROM task_owner_transitions AS owner JOIN task_executions AS execution "
                "ON execution.execution_id=owner.execution_id AND execution.project_id=owner.project_id "
                "AND execution.task_id=owner.task_id WHERE owner.project_id=? AND owner.task_id=? "
                "AND owner.execution_id=? AND owner.generation=? AND owner.actor_session_id=? "
                "AND owner.state IN ('owned','completion_only') LIMIT 1",
                (binding.project_id, binding.task_id, binding.execution_id, binding.ownership_generation,
                 binding.parent_thread_id)).fetchone()
            if owner is None:
                reject()
        selected = {row.continuation_id: row for row in rounds if request is not None and row.continuation_id == request.continuation_id}
        if rounds:
            selected[rounds[-1].continuation_id] = rounds[-1]
        if request is not None and request.continuation_id is not None and request.continuation_id not in selected:
            reject()
        history = read_continuations(connection, project_id=current["project_id"], task_id=current["task_id"]) if selected else ()
        for operational in selected.values():
            native = next((row for row in history if row.continuation_id == operational.continuation_id), None)
            if (native is None or native.state not in ("prepared", "registered")
                or not (historical_identity(native.basis) or request is not None
                    and native.basis == current and native.continuation_id != request.continuation_id)
                or native.preparation_id != operational.preparation_id or native.sequence != operational.continuation_sequence
                or basis_digest(native.basis) != operational.basis_digest
                or selection_digest(native.selected) != operational.selection_digest):
                reject()
            native_binding = WaitBinding(wait_id="history", parent_thread_id=native.basis["parent_session_id"],
                **{key: native.basis[key] for key in WaitBinding.__dataclass_fields__ if key not in ("wait_id", "parent_thread_id")})
            if wait_digest(native_binding) != operational.wait_basis_digest:
                reject()
            if request is not None and native.continuation_id == request.continuation_id and (
                request.basis_digest != operational.wait_basis_digest or request.continuation_sequence != native.sequence
                or {actor for actor, _ in request.reviewers} != {row.session_id for row in native.selected}):
                reject()
