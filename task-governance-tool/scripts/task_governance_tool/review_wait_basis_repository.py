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
