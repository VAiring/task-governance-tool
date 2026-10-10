"""Bounded verification context for reviewers; never a new completion gate."""

from task_governance_tool.completion_history_repository import read_completion_history
from task_governance_tool.storage import StorageError, utc_now, validate_utc_timestamp
from task_governance_tool.verification_receipts import (
    AUTHORITY_SNAPSHOT_ID_PATTERN, CONTRACT_CRITERION_ID_PATTERN,
    VERIFICATION_RECEIPT_ID_PATTERN, TaskShowVerificationDetails,
    read_verification_evidence,
)


SOURCES = {"caller_attestation", "runner_observation", "not_required", "unavailable", "legacy_exemption"}
BLOCKERS = {"review_target_required", "evidence_basis_stale", "verification_requirement_unspecified",
            "verification_receipt_required", "verification_receipt_blocking"}
COUNT_KEYS = {"receipts_exact_current", "qualifying_exact_current", "blocking_exact_current"}
KEYS = {"observed_at", "source_kind", "current_verification_subject", "gate", "counts", "current_receipt"}


def read_review_verification(connection, *, task, runner_selection=None):
    """Reuse the existing exact-current evaluator on the Packet's read snapshot."""
    cycle = None
    if task["status"] == "done":
        history = read_completion_history(connection, project_id=task["project_id"], task_id=task["task_id"])
        cycle = history.cycles[0] if history.cycles else None
    details = TaskShowVerificationDetails()
    evidence = read_verification_evidence(connection, task=task, completion_cycle=cycle,
        runner_selection=runner_selection, task_show_details=details)
    gate = evidence["gate"]
    source = "unavailable"
    if not gate["required"] and gate["satisfied"]:
        source = "not_required" if task.get("verification_not_required_reason", "").strip() else "legacy_exemption"
    elif gate["satisfied"] and gate["qualifying_receipt_id"] is None:
        source = "runner_observation"
    elif details.receipt is not None:
        source = "caller_attestation"
    return {"observed_at": utc_now(), "source_kind": source,
        "current_verification_subject": evidence["current_verification_subject"],
        "gate": gate, "counts": {key: evidence["counts"][key] for key in sorted(COUNT_KEYS)},
        "current_receipt": details.receipt}


def validate_review_verification(value):
    """Validate optional saved context without treating caller bytes as evidence."""
    def require(condition):
        if not condition:
            raise ValueError("invalid review verification context")

    def identifier(value, pattern):
        return type(value) is str and pattern.fullmatch(value) is not None

    def timestamp(value):
        require(type(value) is str)
        try:
            validate_utc_timestamp(value, field="verification time")
        except StorageError:
            raise ValueError("invalid review verification context") from None

    require(type(value) is dict and set(value) == KEYS)
    timestamp(value["observed_at"])
    require(type(value["source_kind"]) is str and value["source_kind"] in SOURCES)
    gate, counts, receipt, subject = (value[key] for key in ("gate", "counts", "current_receipt", "current_verification_subject"))
    require(type(gate) is dict and set(gate) == {"required", "satisfied", "blocking_code", "qualifying_receipt_id"})
    require(type(gate["required"]) is bool and type(gate["satisfied"]) is bool)
    require(gate["blocking_code"] is None or type(gate["blocking_code"]) is str and gate["blocking_code"] in BLOCKERS)
    require(gate["qualifying_receipt_id"] is None or identifier(gate["qualifying_receipt_id"], VERIFICATION_RECEIPT_ID_PATTERN))
    require(type(counts) is dict and set(counts) == COUNT_KEYS)
    require(all(type(n) is int and n in (0, 1) for n in counts.values()))
    require(counts["receipts_exact_current"] == counts["qualifying_exact_current"] + counts["blocking_exact_current"])
    if subject is not None:
        require(type(subject) is dict and set(subject) == {"basis_version", "kind", "authority_snapshot_id", "verification_criterion_id", "legacy_caller_label"})
        require(type(subject["basis_version"]) is int and subject["basis_version"] == 1)
        require(subject["kind"] == "task_verification_criterion" and subject["legacy_caller_label"] is None)
        require(identifier(subject["authority_snapshot_id"], AUTHORITY_SNAPSHOT_ID_PATTERN))
        require(identifier(subject["verification_criterion_id"], CONTRACT_CRITERION_ID_PATTERN))
    if receipt is not None:
        require(type(receipt) is dict and set(receipt) == {"verification_receipt_id", "result", "duration_ms", "scope_coverage", "created_at"})
        require(identifier(receipt["verification_receipt_id"], VERIFICATION_RECEIPT_ID_PATTERN))
        require(type(receipt["result"]) is str and receipt["result"] in {"pass", "fail", "timeout"})
        require(type(receipt["scope_coverage"]) is str and receipt["scope_coverage"] in {"full", "partial"})
        require(type(receipt["duration_ms"]) is int and 0 <= receipt["duration_ms"] < 2**63)
        timestamp(receipt["created_at"])
    require((receipt is not None) == (counts["receipts_exact_current"] == 1))
    if receipt is not None:
        require(counts["qualifying_exact_current"] == int(receipt["result"] == "pass" and receipt["scope_coverage"] == "full"))
    if gate["qualifying_receipt_id"] is not None:
        require(receipt is not None and receipt["verification_receipt_id"] == gate["qualifying_receipt_id"] and counts["qualifying_exact_current"] == 1)
    require(gate["satisfied"] == (gate["blocking_code"] is None))
    source = value["source_kind"]
    require((source == "caller_attestation") == (receipt is not None))
    if source == "caller_attestation":
        require(gate["required"])
        if gate["satisfied"]:
            require(counts["qualifying_exact_current"] == 1
                    and gate["qualifying_receipt_id"] == receipt["verification_receipt_id"])
        else:
            require(gate["qualifying_receipt_id"] is None)
            require(gate["blocking_code"] == "evidence_basis_stale"
                    or (gate["blocking_code"] == "verification_receipt_blocking"
                        and counts["blocking_exact_current"] == 1))
    elif source == "runner_observation":
        require(gate["required"] and gate["satisfied"] and receipt is None)
    elif source in {"not_required", "legacy_exemption"}:
        require(not gate["required"] and gate["satisfied"] and receipt is None)
    elif source == "unavailable":
        require(not gate["satisfied"] and receipt is None)


GUIDANCE = (
    "verification_evidence is a point-in-time summary bound to this Task, Contract and complete review_target; "
    "task.verification is the declared expectation, not an execution result. Caller-attested Receipts report the caller's run; "
    "runner_observation identifies the existing qualifying Runner observation. not_required is an explicit waiver, not a passed run; "
    "legacy_exemption is historical only. Missing, incomplete or stale evidence never implies PASS; use the gate's satisfaction and blocking code. "
    "This summary authenticates neither a caller's execution nor test sufficiency or review PASS. Independently judge coverage and quality. "
    "No routine parent transcription, Packet reread or Task-show lookup is needed for this summary; relevant investigation and questions remain permitted."
)
