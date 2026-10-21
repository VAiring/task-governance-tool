"""Numerical reviewer boundaries; core Receipt/session evidence stays authoritative."""

from dataclasses import dataclass

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.task_ownership import _identifier
from task_governance_tool.task_values import validate_task_id, TaskValidationError
from task_governance_tool.reviews import validate_stored_review_target, ReviewEvidenceError
from task_governance_tool.usage_values import UsageError
from task_governance_tool.usage_attribution import Interval, safe_turn_starts
from task_governance_tool.usage_turn_adapter import BodyProjection, is_foreign_identity


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
    if item.get("context_check") == "matched_at_read":
        context, task, contract, target = (item.get("review_session_context"), item.get("task"),
                                          item.get("contract"), item.get("review_target"))
        if is_foreign_identity(context, project_id):
            return BodyProjection()
        if not all(isinstance(value, dict) for value in (context, task, contract, target)):
            raise UsageError("boundary_unknown")
        metadata = {**context, "task_id": task.get("task_id"), "contract_revision": contract.get("revision"),
                    "review_target": target, "original_result_digest": ""}
        phase = "read"
    elif item.get("status") == "saved":
        candidate = item.get("review_session")
        if is_foreign_identity(candidate, project_id, thread=thread):
            return BodyProjection()
        if (item.get("ok") is not True or not isinstance(candidate, dict)
                or not is_session_id(candidate.get("session_id"))):
            raise UsageError("boundary_unknown")
        metadata, phase = candidate, "save"
    if metadata is not None:
        project = metadata.get("project_id")
        if not isinstance(project, str) or not 0 < len(project) <= 200:
            raise UsageError("boundary_unknown")
        if type(metadata.get("version")) is not int or metadata["version"] != 1:
            raise UsageError("boundary_unknown")
        target = metadata.get("review_target")
        if not isinstance(target, dict):
            raise UsageError("boundary_unknown")
        return BodyProjection((ReviewBoundary(thread, turn, metadata.get("project_id"), metadata.get("task_id"),
            metadata.get("execution_id"), metadata.get("contract_revision"), target.get("kind"),
            target.get("value"), target.get("base_revision"), target.get("generation"),
            phase, metadata.get("original_result_digest")),), terminal=True)
    if (item.get("ok") is not True or item.get("command") not in ("review.receipt.add", "review.result.add")
            or (project_id is not None and item.get("project_id") != project_id)):
        return BodyProjection()
    data = item.get("data")
    if not isinstance(data, dict):
        return BodyProjection(unreadable=True)
    entries = data.get("receipts", [data])
    if not isinstance(entries, list) or not entries:
        return BodyProjection(unreadable=True)
    result, unreadable = [], False
    for entry in entries:
        receipt = entry.get("receipt") if isinstance(entry, dict) else None
        try:
            if not isinstance(receipt, dict):
                raise UsageError("boundary_unknown")
            result.append(ReviewReceiptTurn(thread, turn, receipt.get("review_receipt_id")))
        except (UsageError, ValueError, TypeError):
            unreadable = True
    return BodyProjection(tuple(result), unreadable)


def reviewer_intervals(owners, turns, boundaries, receipt_turns, reviews, *, collected_endpoints=(),
                       conflicting_turns=frozenset()):
    """Only committed exact core review bindings authorize numerical membership.

    `reviews` contains (Receipt ID, ReviewSessionTarget, ReviewSessionBinding).
    Registered collected answers supply their actual reviewer turn separately
    from legacy save observations. Missing boundaries retain partial coverage.
    """
    periods = {item.execution_id: item.preceding_completion for item in owners}
    # Preserve legacy selection while retaining every observation for native
    # order validation. Prefiltering a conflicting ID could hide another ID's
    # same-session order collision with it.
    legacy_starts = {(item.thread_id, item.turn_id): item.started_at for item in turns
                     if (item.thread_id, item.turn_id) not in conflicting_turns}
    native_starts = safe_turn_starts(turns, conflicting_turns)
    # Persistence retains the first start and a conflicting identity, not its
    # alternate starts. Any same-session disagreement can therefore conceal a
    # collision with either native boundary. Do not infer a range from it, even
    # when the retained conflicting turn appears outside that range. Apply the
    # same rule to complete in-memory observations before they are persisted.
    native_conflicts = set(conflicting_turns)
    observed_starts = {}
    for item in turns:
        key = (item.thread_id, item.turn_id)
        if key in observed_starts and observed_starts[key] != item.started_at:
            native_conflicts.add(key)
        observed_starts[key] = item.started_at
    conflicted_sessions = {thread for thread, _ in native_conflicts}
    # A matching read proves its identity even when an unrelated conflicting
    # turn prevents range inference. Its discarded alternate starts cannot
    # taint this identity through the arbitrary first value kept in storage.
    # The read's own disagreement and ties among nonconflicting IDs remain
    # ineligible. The selected key is later projected without consulting time.
    identity_starts = safe_turn_starts(tuple(item for item in turns
        if (item.thread_id, item.turn_id) not in native_conflicts))
    endpoints_by_receipt = {}
    for endpoint in collected_endpoints:
        endpoints_by_receipt.setdefault(endpoint.receipt_id, []).append(endpoint)
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
        native = endpoints_by_receipt.get(receipt, ())
        identity_only = bool(native) and binding.session_id in conflicted_sessions
        starts = identity_starts if identity_only else native_starts if native else legacy_starts
        if native:
            # A collected Receipt uses its exact registered source, not an old
            # save echo or the parent's later forwarding/registration turn.
            # Contradictory supplied identities never fall back to legacy proof.
            valid = [item for item in native if item.target == target and item.binding == binding
                     and binding.binding_source == "handoff" and is_session_id(item.turn_id)]
            closes = {item.turn_id for item in valid} if len(valid) == len(native) else set()
        else:
            closes = ({item.turn_id for item in matching if item.phase == "save"
                       and item.original_result_digest == binding.original_result_digest}
                      if binding.binding_source == "handoff" else
                      {item.turn_id for item in receipt_turns if item.receipt_id == receipt
                       and item.thread_id == binding.session_id})
        end = next(iter(closes)) if len(closes) == 1 else None
        end_order = starts.get((binding.session_id, end))
        unordered_native_end = bool(native) and (identity_only
                                                or (end is not None and end_order is None))
        reads = [item.turn_id for item in matching if item.phase == "read"
                 and (binding.session_id, item.turn_id) in starts
                 and (end is None or unordered_native_end
                      or (end_order is not None and starts[binding.session_id, item.turn_id] <= end_order))]
        start = min(reads, key=lambda turn: starts[binding.session_id, turn]) if reads else end
        if unordered_native_end:
            # The source identifies the answer, but cannot establish a range.
            # Keep a definite read and close with an unknown end so neither
            # projection can infer intermediate turns from conflicting order.
            end = None
            if not reads:
                start = None
        gaps = () if reads and end is not None else ("review_boundary_unknown",)
        result.append(Interval(target.task_id, binding.execution_id, periods[binding.execution_id],
                               binding.session_id, start, end, True, gaps, identity_only=identity_only))
    return tuple(result)
