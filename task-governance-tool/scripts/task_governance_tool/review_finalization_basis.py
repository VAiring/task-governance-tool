"""Retained integrated constraints, independent of transport and persistence."""

from dataclasses import dataclass

from .task_values import validation_error


@dataclass(frozen=True)
class FinalizationBasis:
    project_id: str
    project_path_hash: str
    project_binding_generation: int
    task_id: str
    execution_id: str
    ownership_generation: int
    parent_thread_id: str
    contract_revision: int
    target_kind: str
    target_value: str
    target_base_revision: str
    target_generation: int
    artifact_manifest_id: str

    @classmethod
    def from_record(cls, record):
        # The operational repository has already validated the closed wait basis.
        return cls(**{key: record.basis[key] for key in cls.__dataclass_fields__})

    def require_target(self, target):
        # Native admission rechecks this same DatabaseTarget under its writer lock.
        if (target.project.project_id != self.project_id
                or target.binding_path_hash != self.project_path_hash
                or target.binding_generation != self.project_binding_generation):
            raise validation_error("review_target_mismatch", "the retained project binding changed")

    def require_task(self, task, ownership):
        if (task["project_id"] != self.project_id or task["task_id"] != self.task_id
                or task["current_contract_revision"] != self.contract_revision
                or task["review_target_artifact_manifest_id"] != self.artifact_manifest_id
                or any(task["review_target_" + key] != getattr(self, "target_" + key)
                       for key in ("kind", "value", "base_revision", "generation"))):
            raise validation_error("review_target_mismatch", "the retained review target changed")
        if (ownership is None or ownership.state not in ("owned", "completion_only")
                or ownership.project_id != self.project_id or ownership.task_id != self.task_id
                or ownership.execution_id != self.execution_id
                or ownership.generation != self.ownership_generation
                or (ownership.owner_session_id or ownership.completion_session_id) != self.parent_thread_id):
            raise validation_error("task_ownership_changed", "the retained task ownership changed")


def require_no_findings(evidence):
    if evidence is None or any(evidence["counts"]["open_" + severity] for severity in ("high", "medium", "low")):
        raise validation_error("finalization_findings_require_judgment", "unresolved findings require judgment")
