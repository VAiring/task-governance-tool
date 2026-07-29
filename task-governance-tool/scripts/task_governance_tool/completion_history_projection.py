"""Bounded public projection for immutable completion-cycle history."""

from __future__ import annotations

import json
from typing import Any

from task_governance_tool.storage import (
    CompletionCycle,
    CompletionGateBasis,
    CompletionHistory,
    completion_history_inconsistent,
)


COMPLETION_HISTORY_MAX_CYCLES = 10
COMPLETION_HISTORY_MAX_CYCLE_BYTES = 8_192
COMPLETION_HISTORY_MAX_BYTES = 32_768

PUBLIC_COMPLETION_CYCLE_FIELDS = (
    "completion_cycle_id",
    "saved_cycle_ordinal",
    "origin",
    "completeness",
    "completed_at",
    "contract_revision",
    "review_tier",
    "verification_expectation",
    "verification_attestation",
    "completion_evidence",
    "review_target",
    "gate_basis",
)
PUBLIC_COMPLETION_EVIDENCE_FIELDS = (
    "kind",
    "revision",
    "reason",
    "external_revision_approved",
    "completion_commit_required",
    "completion_commit_hash",
)
PUBLIC_REVIEW_TARGET_FIELDS = (
    "kind",
    "value",
    "base_revision",
    "generation",
)
PUBLIC_GATE_BASIS_FIELDS = (
    "version",
    "kind",
    "required_independent_passes",
    "qualifying_independent_passes",
    "changes_requested",
    "open_high",
    "open_medium",
    "fresh_review_required",
    "qualifying_receipt_ids",
)
PUBLIC_COMPLETION_HISTORY_FIELDS = (
    "total",
    "returned_count",
    "truncated",
    "legacy_history_incomplete",
    "cycles",
)


def _compact_json_bytes(value: Any) -> bytes:
    """Serialize with the exact completion-history measurement contract."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _is_json_integer(value: object) -> bool:
    return type(value) is int


def _format_gate_basis(
    basis: CompletionGateBasis,
    *,
    review_tier: int,
) -> dict[str, Any]:
    count_values = (
        basis.required_independent_passes,
        basis.qualifying_independent_passes,
        basis.changes_requested_count,
        basis.open_high_count,
        basis.open_medium_count,
        basis.fresh_review_required_count,
    )
    receipt_ids = list(basis.qualifying_receipt_ids)
    if basis.version == 0:
        if (
            basis.kind != "unknown"
            or any(value is not None for value in count_values)
            or receipt_ids
        ):
            raise completion_history_inconsistent()
    elif basis.version == 1:
        if (
            any(not _is_json_integer(value) for value in count_values)
            or any(
                not isinstance(receipt_id, str) or not receipt_id
                for receipt_id in receipt_ids
            )
        ):
            raise completion_history_inconsistent()
        expected_receipts = {
            "not_required": 1,
            "self_review_fallback": 1,
            "independent_passes": 2 if review_tier == 2 else 1,
        }.get(basis.kind)
        if expected_receipts is None or len(receipt_ids) != expected_receipts:
            raise completion_history_inconsistent()
    else:
        raise completion_history_inconsistent()
    return {
        "version": basis.version,
        "kind": basis.kind,
        "required_independent_passes": basis.required_independent_passes,
        "qualifying_independent_passes": basis.qualifying_independent_passes,
        "changes_requested": basis.changes_requested_count,
        "open_high": basis.open_high_count,
        "open_medium": basis.open_medium_count,
        "fresh_review_required": basis.fresh_review_required_count,
        "qualifying_receipt_ids": receipt_ids,
    }


def _format_cycle(cycle: CompletionCycle) -> dict[str, Any]:
    if (
        cycle.verification_attestation is not True
        and cycle.verification_attestation is not None
    ):
        raise completion_history_inconsistent()
    if cycle.origin == "native_done":
        if (
            cycle.completeness != "complete"
            or cycle.verification_attestation is not True
            or cycle.gate_basis.version != 1
        ):
            raise completion_history_inconsistent()
    elif cycle.origin == "legacy_current_done":
        if (
            cycle.completeness != "partial"
            or cycle.verification_attestation is not None
            or cycle.gate_basis.version != 0
        ):
            raise completion_history_inconsistent()
    else:
        raise completion_history_inconsistent()
    public_cycle = {
        "completion_cycle_id": cycle.completion_cycle_id,
        "saved_cycle_ordinal": cycle.saved_cycle_ordinal,
        "origin": cycle.origin,
        "completeness": cycle.completeness,
        "completed_at": cycle.completed_at,
        "contract_revision": cycle.contract_revision,
        "review_tier": cycle.review_tier,
        "verification_expectation": cycle.verification_expectation,
        "verification_attestation": cycle.verification_attestation,
        "completion_evidence": {
            "kind": cycle.completion_evidence_kind,
            "revision": cycle.completion_evidence_revision,
            "reason": cycle.completion_evidence_reason,
            "external_revision_approved": cycle.external_revision_approved,
            "completion_commit_required": cycle.completion_commit_required,
            "completion_commit_hash": cycle.completion_commit_hash,
        },
        "review_target": {
            "kind": cycle.review_target_kind,
            "value": cycle.review_target_value,
            "base_revision": cycle.review_target_base_revision,
            "generation": cycle.review_target_generation,
        },
        "gate_basis": _format_gate_basis(
            cycle.gate_basis,
            review_tier=cycle.review_tier,
        ),
    }
    if (
        tuple(public_cycle) != PUBLIC_COMPLETION_CYCLE_FIELDS
        or tuple(public_cycle["completion_evidence"])
        != PUBLIC_COMPLETION_EVIDENCE_FIELDS
        or tuple(public_cycle["review_target"]) != PUBLIC_REVIEW_TARGET_FIELDS
        or tuple(public_cycle["gate_basis"]) != PUBLIC_GATE_BASIS_FIELDS
    ):
        raise completion_history_inconsistent()
    return public_cycle


def _history_wrapper(
    *,
    total: int,
    legacy_history_incomplete: bool,
    cycles: list[dict[str, Any]],
) -> dict[str, Any]:
    returned_count = len(cycles)
    return {
        "total": total,
        "returned_count": returned_count,
        "truncated": returned_count < total,
        "legacy_history_incomplete": legacy_history_incomplete,
        "cycles": cycles,
    }


def format_completion_history(history: CompletionHistory) -> dict[str, Any]:
    """Return the exact newest-prefix public history within both byte caps."""

    if (
        not _is_json_integer(history.total)
        or history.total < 0
        or type(history.legacy_history_incomplete) is not bool
        or history.total < len(history.cycles)
    ):
        raise completion_history_inconsistent()

    accepted: list[dict[str, Any]] = []
    previous_ordinal: int | None = None
    for cycle in history.cycles:
        if len(accepted) >= COMPLETION_HISTORY_MAX_CYCLES:
            break
        if (
            not _is_json_integer(cycle.saved_cycle_ordinal)
            or cycle.saved_cycle_ordinal < 1
            or (
                previous_ordinal is not None
                and cycle.saved_cycle_ordinal >= previous_ordinal
            )
        ):
            raise completion_history_inconsistent()
        previous_ordinal = cycle.saved_cycle_ordinal
        public_cycle = _format_cycle(cycle)
        if len(_compact_json_bytes(public_cycle)) > COMPLETION_HISTORY_MAX_CYCLE_BYTES:
            break
        candidate = _history_wrapper(
            total=history.total,
            legacy_history_incomplete=history.legacy_history_incomplete,
            cycles=[*accepted, public_cycle],
        )
        if len(_compact_json_bytes(candidate)) > COMPLETION_HISTORY_MAX_BYTES:
            break
        accepted.append(public_cycle)

    result = _history_wrapper(
        total=history.total,
        legacy_history_incomplete=history.legacy_history_incomplete,
        cycles=accepted,
    )
    if tuple(result) != PUBLIC_COMPLETION_HISTORY_FIELDS:
        raise completion_history_inconsistent()
    return result
