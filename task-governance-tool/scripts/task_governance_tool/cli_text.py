"""Human-readable Task, Handoff, and evidence output from supplied projections.

These formatters do not select tasks, access storage, or perform operations.
Review Packet rendering remains in review_packet.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from task_governance_tool.effort import METRIC_ORDER


def task_add_text(
    task: dict[str, Any],
    event: dict[str, Any],
    contract_write: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"Task added: {task['task_id']}",
        f"Title: {task['title']}",
        f"Kind: {task['kind']}  Status: {task['status']}  Priority: {task['priority']}",
        f"Review tier: {task['review_tier']}",
        f"Event: {event['event_type']}",
    ]
    if task["kind"] == "sequential":
        lines.insert(3, f"Lane: {task['lane']}  Order: {task['lane_order']}")
    if contract_write is not None:
        lines.append(
            "Contract: "
            f"revision {contract_write['revision']} "
            f"recorded={str(contract_write['recorded']).lower()}"
        )
    return "\n".join(lines)


def task_list_text(tasks: list[dict[str, Any]], count: int, limit: int) -> str:
    lines = [f"Tasks: {count} (limit {limit})"]
    for task in tasks:
        lane = ""
        if task["lane"]:
            lane = f" {task['lane']}"
            if task["lane_order"] is not None:
                lane += f"#{task['lane_order']}"
        lines.append(
            f"{task['task_id']} [{task['status']}] {task['priority']} {task['kind']}{lane} - {task['title']}"
        )
    return "\n".join(lines)


def task_next_text(
    tasks: list[dict[str, Any]],
    count: int,
    limit: int,
    warnings: Sequence[dict[str, str]] = (),
) -> str:
    lines = [f"Next tasks: {count} (limit {limit})"]
    for task in tasks:
        lane = ""
        if task["lane"]:
            lane = f" {task['lane']}"
            if task["lane_order"] is not None:
                lane += f"#{task['lane_order']}"
        lines.append(
            f"{task['task_id']} [{task['status']}] {task['priority']} {task['kind']}{lane} - {task['title']}"
        )
    lines.extend(f"Warning: {warning['message']}" for warning in warnings)
    return "\n".join(lines)


def task_current_text(tasks: list[dict[str, Any]], count: int, limit: int) -> str:
    lines = [f"Current tasks: {count} (limit {limit})"]
    for task in tasks:
        lines.append(
            f"{task['task_id']} [{task['status']}] {task['priority']} - "
            f"{task['title']} | {task['suggested_next_action']}"
        )
    return "\n".join(lines)


def task_context_text(
    selection: str,
    current: dict[str, Any],
    selected_text: str,
    warnings: Sequence[dict[str, str]] = (),
) -> str:
    lines = [f"Task selection: {selection}"]
    if selected_text:
        lines.append(selected_text)
    else:
        lines.append("No resumable or ready task.")
    held = [
        task for task in current["tasks"]
        if task["status"] in {"paused", "blocked"}
    ]
    if held:
        lines.append("Held work recalled:")
        for task in held:
            lines.append(
                f"{task['task_id']} [{task['status']}] {task['title']} | "
                f"{task['suggested_next_action']}"
            )
    if current["truncated"] or current["total_matching"] > current["returned_count"]:
        lines.append(
            "Current recall: "
            f"{current['returned_count']}/{current['total_matching']} returned "
            f"(limit {current['limit']})"
        )
    lines.extend(f"Warning: {warning['message']}" for warning in warnings)
    return "\n".join(lines)


def task_effort_text(data: dict[str, Any]) -> str:
    enabled = "enabled" if data["enabled"] else "disabled"
    exceeded = ", ".join(data["exceeded"]) or "none"
    reasons = ", ".join(data["unknown_reasons"]) or "none"
    measurements = " ".join(
        f"{metric}={data['measurements'][metric] if data['measurements'][metric] is not None else 'unknown'}"
        for metric in METRIC_ORDER
    )
    thresholds = " ".join(
        f"{metric}={data['thresholds'][metric]}"
        for metric in METRIC_ORDER
        if metric in data["thresholds"]
    ) or "none"
    return "\n".join(
        [
            f"Effort advisory: {enabled}",
            f"Task: {data['task_id']}",
            f"Measurements: {measurements}",
            f"Thresholds: {thresholds}",
            f"Attribution: {data['attribution']}",
            f"Exceeded: {exceeded}",
            f"Unknown reasons: {reasons}",
            f"Suggested action: {data['suggested_action']}",
        ]
    )


def task_show_text(
    task: dict[str, Any],
    events: list[dict[str, Any]],
    suggested_next_action: str,
    review_evidence: dict[str, Any],
    handoff_summary: dict[str, int],
    contract: dict[str, Any],
    completion_history: dict[str, Any],
    completion_history_latest_summary: dict[str, Any] | None,
) -> str:
    lines = [
        f"Task: {task['task_id']}",
        f"Title: {task['title']}",
        f"Status: {task['status']}  Priority: {task['priority']}  Kind: {task['kind']}",
    ]
    if task["lane"]:
        lane = f"Lane: {task['lane']}"
        if task["lane_order"] is not None:
            lane += f"  Order: {task['lane_order']}"
        lines.append(lane)
    lines.append(f"Review tier: {task['review_tier']}")
    lines.append(f"Contract revision: {contract['revision']}")
    review_target = review_evidence["target"]
    review_gate = review_evidence["gate"]
    lines.append(
        "Review evidence: "
        f"generation {review_target['generation']}, "
        f"passes {review_gate['qualifying_independent_passes']}/"
        f"{review_gate['required_independent_passes']}, "
        f"satisfied={str(review_gate['satisfied']).lower()}"
    )
    if task["verification"]:
        lines.append(f"Verification: {task['verification']}")
    if "completion_evidence_kind" in task:
        kind = task["completion_evidence_kind"]
        revision = task["completion_evidence_revision"]
        detail = f", {revision}" if revision else ""
        lines.append(f"Completion evidence: {kind}{detail}")
    elif "completion_commit_required" in task:
        if task["completion_commit_required"]:
            commit_hash = task["completion_commit_hash"] or "hash not set"
            lines.append(f"Completion commit: required, {commit_hash}")
        else:
            lines.append("Completion commit: not required")
    if task["blocked_reason"]:
        lines.append(f"Blocked: {task['blocked_reason']}")
    handoff_total = sum(handoff_summary.values())
    if handoff_total:
        lines.append(
            "Handoffs: "
            f"pending={handoff_summary['pending_handoff']} "
            f"handed_off={handoff_summary['handed_off']} "
            f"withdrawn={handoff_summary['handoff_withdrawn_by_user']}"
        )
    lines.append(
        "Completion history: "
        f"{completion_history['returned_count']}/{completion_history['total']} returned, "
        f"truncated={str(completion_history['truncated']).lower()}, "
        "legacy_history_incomplete="
        f"{str(completion_history['legacy_history_incomplete']).lower()}"
    )
    if completion_history_latest_summary is not None:
        latest_cycle = completion_history_latest_summary
        completed_at = latest_cycle["completed_at"] or "unknown"
        lines.append(
            "Latest completion cycle: "
            f"ordinal={latest_cycle['saved_cycle_ordinal']}, "
            f"{latest_cycle['origin']}/{latest_cycle['completeness']}, "
            f"completed_at={completed_at}, "
            f"evidence={latest_cycle['completion_evidence_kind']}, "
            f"target={latest_cycle['review_target_kind']}/generation "
            f"{latest_cycle['review_target_generation']}, "
            f"review_basis={latest_cycle['review_basis_kind']}"
        )
    lines.append(f"Suggested next action: {suggested_next_action}")
    if events:
        latest = events[0]
        lines.append(f"Latest event: {latest['event_type']} - {latest['summary']}")
    return "\n".join(lines)


def task_edit_text(
    task: dict[str, Any],
    changed_fields: list[str],
    event: dict[str, Any] | None,
    contract_write: dict[str, Any] | None = None,
    *,
    completed: bool = False,
) -> str:
    changed = ", ".join(changed_fields) if changed_fields else "none"
    lines = [
        (
            f"Task completed: {task['task_id']}"
            if completed
            else f"Task updated: {task['task_id']}"
        ),
        f"Title: {task['title']}",
        f"Status: {task['status']}  Priority: {task['priority']}  Kind: {task['kind']}",
        f"Changed: {changed}",
    ]
    if event is not None:
        lines.append(f"Event: {event['event_type']} - {event['summary']}")
    if contract_write is not None:
        lines.append(
            "Contract: "
            f"revision {contract_write['revision']} "
            f"recorded={str(contract_write['recorded']).lower()}"
        )
    return "\n".join(lines)


def task_completion_check_text(data: dict[str, Any]) -> str:
    blocking = ",".join(data["blocking_codes"]) or "none"
    readiness = "ready" if data["ready"] else "not ready"
    return "\n".join(
        [
            f"Task {data['task_id']}: {readiness}",
            f"Blocking: {blocking}",
            f"Suggested action: {data['suggested_action']}",
        ]
    )


def handoff_text(command: str, data: dict[str, Any]) -> str:
    if command == "handoff.record":
        handoff = data["handoff"]
        local = data["local_record"]
        action = "recorded" if local["created"] else "replayed"
        return "\n".join(
            [
                f"Handoff {action}: {handoff['handoff_id']}",
                f"Source task: {handoff['source_task_id']}",
                f"State: {handoff['state']}",
                f"Summary: {handoff['summary']}",
            ]
        )
    if command == "handoff.list":
        lines = [
            f"Handoffs: {data['count']} of {data['total_matching']} "
            f"(limit {data['limit']})"
        ]
        for handoff in data["handoffs"]:
            lines.append(
                f"{handoff['handoff_id']} [{handoff['state']}] "
                f"{handoff['source_task_id']} - {handoff['summary']}"
            )
        return "\n".join(lines)
    if command == "handoff.withdraw":
        handoff = data["handoff"]
        return "\n".join(
            [
                f"Handoff withdrawn: {handoff['handoff_id']}",
                f"State: {handoff['state']}",
                f"Reason: {handoff['withdraw_reason']}",
            ]
        )
    handoff = data["handoff"]
    return "\n".join(
        [
            f"Handoff: {handoff['handoff_id']}",
            f"Source task: {handoff['source_task_id']}",
            f"State: {handoff['state']}",
            f"Summary: {handoff['summary']}",
            f"Created: {handoff['created_at']}",
        ]
    )


def review_text(command: str, data: dict[str, Any]) -> str:
    event = data["event"]
    if command == "review.target.set":
        task = data["task"]
        base = (
            f"\nBase: {task['review_target_base_revision']}"
            if task["review_target_kind"] == "git_snapshot"
            else ""
        )
        return (
            f"Review target set: {task['task_id']}\n"
            f"Target: {task['review_target_kind']} generation "
            f"{task['review_target_generation']}{base}\n"
            f"Event: {event['event_type']} - {event['summary']}"
        )
    if command == "review.receipt.add":
        receipt = data["receipt"]
        return (
            f"Review receipt recorded: {receipt['review_receipt_id']}\n"
            f"Verdict: {receipt['verdict']}  Kind: {receipt['receipt_kind']}\n"
            f"Event: {event['event_type']} - {event['summary']}"
        )
    finding = data["finding"]
    verb = "resolved" if command.endswith("resolve") else "recorded"
    return (
        f"Review finding {verb}: {finding['review_finding_id']}\n"
        f"Severity: {finding['severity']}  Status: {finding['status']}\n"
        f"Event: {event['event_type']} - {event['summary']}"
    )


def verification_receipt_text(receipt: dict[str, Any]) -> str:
    source_revision = receipt["source_revision"]
    return (
        "Verification receipt recorded: "
        f"{receipt['verification_receipt_id']}\n"
        f"Result: {receipt['result']}  Coverage: {receipt['scope_coverage']}\n"
        f"Source: {source_revision['kind']}/generation "
        f"{source_revision['generation']}"
    )
