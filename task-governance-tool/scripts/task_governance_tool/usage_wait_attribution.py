"""Closed metadata for a supervisor's all-ended review-wait decision turn.

The caller identity and host turn are structural inputs. No notification text,
timer payload, verdict or elapsed-time heuristic contributes to attribution.
"""

from dataclasses import asdict, dataclass

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.task_ownership import _identifier
from task_governance_tool.task_values import validate_task_id, TaskValidationError
from task_governance_tool.reviews import validate_stored_review_target, ReviewEvidenceError
from task_governance_tool.usage_values import UsageError


@dataclass(frozen=True)
class WaitBasis:
    project_id: str
    task_id: str
    execution_id: str
    contract_revision: int
    target_kind: str
    target_value: str
    target_base_revision: str
    target_generation: int
    artifact_manifest_id: str

    def __post_init__(self):
        try:
            valid = validate_task_id(self.task_id) == self.task_id
            validate_stored_review_target({"review_target_kind": self.target_kind,
                "review_target_value": self.target_value, "review_target_base_revision": self.target_base_revision,
                "review_target_generation": self.target_generation})
        except (TaskValidationError, ReviewEvidenceError, ValueError, TypeError):
            valid = False
        if (not valid
                or not isinstance(self.project_id, str) or not 0 < len(self.project_id) <= 200
                or not _identifier(self.execution_id, "tg_execution_")
                or not _identifier(self.artifact_manifest_id, "tg_artifact_manifest_")
                or type(self.target_generation) is not int or not 0 < self.target_generation < 2**63
                or type(self.contract_revision) is not int or not 0 <= self.contract_revision < 2**63):
            raise UsageError("boundary_unknown")


@dataclass(frozen=True)
class WaitObservation(WaitBasis):
    thread_id: str
    turn_id: str

    def __post_init__(self):
        super().__post_init__()
        if not is_session_id(self.thread_id) or not is_session_id(self.turn_id):
            raise UsageError("boundary_unknown")


def binding_from_metadata(value):
    """Validate the complete closed helper metadata, without retaining its input."""
    if (not isinstance(value, dict) or set(value) != {"version", "session_id", "project_id", "task_id",
            "execution_id", "contract_revision", "review_target", "artifact_manifest_id"}
            or type(value["version"]) is not int or value["version"] != 1
            or not is_session_id(value["session_id"])):
        raise UsageError("boundary_unknown")
    target = value["review_target"]
    if not isinstance(target, dict) or set(target) != {"kind", "value", "base_revision", "generation"}:
        raise UsageError("boundary_unknown")
    return WaitBasis(value["project_id"], value["task_id"], value["execution_id"], value["contract_revision"],
                     target["kind"], target["value"], target["base_revision"], target["generation"],
                     value["artifact_manifest_id"])


def observation_from_metadata(value, turn):
    return WaitObservation(**asdict(binding_from_metadata(value)), thread_id=value["session_id"], turn_id=turn)


def project_wait(item, thread, turn, project_id):
    """Only the originating helper success in its sender's host turn qualifies."""
    if item.get("ok") is not True or item.get("status") != "review_wait_ended":
        return ()
    value = item.get("review_wait")
    if (not isinstance(value, dict) or value.get("session_id") != thread
            or (project_id is not None and value.get("project_id") != project_id)):
        return ()
    if set(item) != {"ok", "status", "review_wait"}:
        raise UsageError("boundary_unknown")
    return (observation_from_metadata(value, turn),)


def wait_intervals(owners, turns, observations, valid_observations, *, conflicting_turns=frozenset()):
    """One whole earliest observed decision turn per exact execution/manifest.

    Repeated facts never extend the wait into a later turn. Missing or conflicting
    turn order and different senders leave the decision unbound, rather than
    selecting whichever source happened to be collected first.
    """
    from task_governance_tool.usage_attribution import Interval
    executions = {item.execution_id: item for item in owners}
    starts, orders, invalid = {}, {}, set(conflicting_turns)
    for item in turns:
        key, order = (item.thread_id, item.turn_id), (item.thread_id, item.started_at)
        if key in starts and starts[key] != item.started_at:
            invalid.add(key)
        if order in orders and orders[order] != key:
            invalid.update((key, orders[order]))
        starts[key], orders[order] = item.started_at, key
    groups = {}
    for item in observations:
        owner = executions.get(item.execution_id)
        if owner is not None and owner.task_id == item.task_id:
            groups.setdefault((item.execution_id, item.artifact_manifest_id), set()).add(item)
    result = []
    for (execution, _), items in sorted(groups.items()):
        owner = executions[execution]
        senders = {item.thread_id for item in items}
        keys = {(item.thread_id, item.turn_id) for item in items}
        known = (items <= valid_observations and len(senders) == 1
                 and keys <= starts.keys() and not keys.intersection(invalid))
        thread, turn = min(keys, key=lambda key: (starts.get(key, 0), key)) if known else (min(senders), None)
        result.append(Interval(owner.task_id, execution, owner.preceding_completion, thread,
                               turn, turn, True, () if known else ("boundary_unknown",)))
    return tuple(result)
