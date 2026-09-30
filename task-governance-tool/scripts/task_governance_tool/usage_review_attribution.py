"""Numerical reviewer boundaries; core Receipt/session evidence stays authoritative."""

from dataclasses import dataclass

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.task_ownership import _identifier
from task_governance_tool.task_values import validate_task_id, TaskValidationError
from task_governance_tool.reviews import validate_stored_review_target, ReviewEvidenceError
from task_governance_tool.usage_values import UsageError
from task_governance_tool.usage_attribution import Interval


@dataclass(frozen=True)
class ReviewBoundary:
    thread_id: str
    turn_id: str
    project_id: str
    task_id: str
    execution_id: str
    contract_revision: int
    target_kind: str
    target_value: str
    target_base_revision: str
    target_generation: int
    phase: str
    original_result_digest: str

    def __post_init__(self):
        try:
            valid = validate_task_id(self.task_id) == self.task_id
            validate_stored_review_target({"review_target_kind": self.target_kind,
                "review_target_value": self.target_value, "review_target_base_revision": self.target_base_revision,
                "review_target_generation": self.target_generation})
        except (TaskValidationError, ReviewEvidenceError, ValueError, TypeError):
            valid = False
        digest = self.original_result_digest
        if (not valid or not is_session_id(self.thread_id) or not is_session_id(self.turn_id)
                or not isinstance(self.project_id, str) or not 0 < len(self.project_id) <= 200
                or not _identifier(self.execution_id, "tg_execution_")
                or type(self.contract_revision) is not int or not 0 <= self.contract_revision < 2**63
                or self.phase not in ("read", "save")
                or (self.phase == "read" and digest != "")
                or (self.phase == "save" and not (isinstance(digest, str) and len(digest) == 64
                                                  and all(char in "0123456789abcdef" for char in digest)))):
            raise UsageError("boundary_unknown")


@dataclass(frozen=True)
class ReviewReceiptTurn:
    thread_id: str
    turn_id: str
    receipt_id: str

    def __post_init__(self):
        if (not is_session_id(self.thread_id) or not is_session_id(self.turn_id)
                or not _identifier(self.receipt_id, "tg_review_receipt_")):
            raise UsageError("boundary_unknown")


def project_review(item, thread, turn, project_id):
    """Known helper read/save and public registration responses, no arguments."""
    metadata, phase = None, None
    if item.get("context_check") == "matched_at_read" and isinstance(item.get("review_session_context"), dict):
        context, task, contract, target = (item["review_session_context"], item.get("task"),
                                          item.get("contract"), item.get("review_target"))
        if isinstance(task, dict) and isinstance(contract, dict) and isinstance(target, dict):
            metadata = {**context, "task_id": task.get("task_id"), "contract_revision": contract.get("revision"),
                        "review_target": target, "original_result_digest": ""}
            phase = "read"
    elif item.get("ok") is True and item.get("status") == "saved":
        candidate = item.get("review_session")
        if isinstance(candidate, dict) and candidate.get("session_id") == thread:
            metadata, phase = candidate, "save"
    if metadata is not None:
        if (metadata.get("version") != 1 or (project_id is not None and metadata.get("project_id") != project_id)
                or metadata.get("execution_id") is None):
            return ()
        target = metadata.get("review_target")
        if not isinstance(target, dict):
            return ()
        return (ReviewBoundary(thread, turn, metadata.get("project_id"), metadata.get("task_id"),
            metadata.get("execution_id"), metadata.get("contract_revision"), target.get("kind"),
            target.get("value"), target.get("base_revision"), target.get("generation"),
            phase, metadata.get("original_result_digest")),)
    if (item.get("ok") is not True or item.get("command") not in ("review.receipt.add", "review.result.add")
            or (project_id is not None and item.get("project_id") != project_id)):
        return ()
    data = item.get("data")
    if not isinstance(data, dict):
        return ()
    entries = data.get("receipts", [data])
    if not isinstance(entries, list):
        return ()
    return tuple(ReviewReceiptTurn(thread, turn, entry["receipt"]["review_receipt_id"])
                 for entry in entries if isinstance(entry, dict) and isinstance(entry.get("receipt"), dict)
                 and "review_receipt_id" in entry["receipt"])


def reviewer_intervals(owners, turns, boundaries, receipt_turns, reviews):
    """Only committed exact core review bindings authorize numerical membership.

    `reviews` contains (Receipt ID, ReviewSessionTarget, ReviewSessionBinding).
    Missing read/save leaves explicit partial coverage, never parent attribution.
    """
    periods = {item.execution_id: item.preceding_completion for item in owners}
    starts = {(item.thread_id, item.turn_id): item.started_at for item in turns}
    result = []
    for receipt, target, binding in reviews:
        if binding.execution_id not in periods:
            continue
        matching = [item for item in boundaries if
                    (item.thread_id, item.project_id, item.task_id, item.execution_id, item.contract_revision,
                     item.target_kind, item.target_value, item.target_base_revision, item.target_generation)
                    == (binding.session_id, target.project_id, target.task_id, binding.execution_id,
                        target.contract_revision, target.target_kind, target.target_value,
                        target.target_base_revision, target.target_generation)]
        closes = ({item.turn_id for item in matching if item.phase == "save"
                   and item.original_result_digest == binding.original_result_digest}
                  if binding.binding_source == "handoff" else
                  {item.turn_id for item in receipt_turns if item.receipt_id == receipt
                   and item.thread_id == binding.session_id})
        end = next(iter(closes)) if len(closes) == 1 else None
        end_order = starts.get((binding.session_id, end))
        reads = [item.turn_id for item in matching if item.phase == "read"
                 and (binding.session_id, item.turn_id) in starts
                 and (end is None or (end_order is not None and starts[binding.session_id, item.turn_id] <= end_order))]
        start = min(reads, key=lambda turn: starts[binding.session_id, turn]) if reads else end
        gaps = () if reads and end is not None else ("review_boundary_unknown",)
        result.append(Interval(target.task_id, binding.execution_id, periods[binding.execution_id],
                               binding.session_id, start, end, True, gaps))
    return tuple(result)
