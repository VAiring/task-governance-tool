"""Closed Review-result input and one caller-owned atomic registration."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from task_governance_tool.review_provenance import (
    ReviewProvenanceError,
    normalize_review_provenance_input,
)
from task_governance_tool.reviews import (
    FINDING_SEVERITIES,
    REVIEW_TARGET_KINDS,
    ReviewEvidenceError,
    add_review_finding,
    add_review_receipt,
    lock_and_reread_target_owner,
    normalize_receipt,
    reject_concurrent_review_basis_change,
    require_current_capture,
    review_error,
    validate_stored_review_target,
)
from task_governance_tool.storage import DatabaseTarget, ProjectIdentity
from task_governance_tool.tasks import (
    TaskRepositoryError,
    read_internal_task,
    reject_done_task_write,
)
from task_governance_tool.task_values import (
    SQLITE_INT64_MAX,
    TaskValidationError,
    reject_private_or_raw_content,
    validate_choice,
    validate_task_id,
    validate_text,
)


REVIEW_RESULTS_INPUT_LIMIT = 256 * 1024
REVIEW_RESULTS_RECEIPT_LIMIT = 8
REVIEW_RESULTS_FINDING_LIMIT = 64
_INPUT_KEYS = {"version", "task_id", "contract_revision", "review_target", "receipts"}
_TARGET_KEYS = {"kind", "value", "base_revision", "generation"}
_RECEIPT_KEYS = {"reviewer", "kind", "verdict", "summary", "provenance", "findings"}
_PROVENANCE_KEYS = {
    "reviewer_class", "model_state", "declared_model_id", "skill_state",
    "declared_skill_id", "declared_skill_version", "review_profiles",
    "review_lenses", "context_relation", "method_codes",
}
_PROVENANCE_IDENTIFIERS = {
    "declared_model_id", "declared_skill_id", "declared_skill_version",
}
_PROVENANCE_ARRAYS = {"review_profiles", "review_lenses", "method_codes"}


def _invalid_input() -> ReviewEvidenceError:
    return review_error("invalid_review_evidence", "review result input is invalid")


def _basis_mismatch() -> ReviewEvidenceError:
    return review_error(
        "review_target_mismatch",
        "review result Task, Contract, or target does not match the current task",
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid_input()
        result[key] = value
    return result


def _invalid_number(value: str) -> Any:
    raise _invalid_input()


def _exact_object(value: Any, keys: set[str]) -> None:
    if type(value) is not dict or set(value) != keys:
        raise _invalid_input()


def _string(value: Any) -> None:
    if type(value) is not str:
        raise _invalid_input()
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _invalid_input() from exc


def _integer(value: Any, minimum: int) -> None:
    if type(value) is not int or not minimum <= value <= SQLITE_INT64_MAX:
        raise _invalid_input()


def _validate_payload(payload: Any) -> None:
    """Validate only this closed input shape, before semantic normalization."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > REVIEW_RESULTS_INPUT_LIMIT:
            raise _invalid_input()
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid_input() from exc
    _exact_object(payload, _INPUT_KEYS)
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise _invalid_input()
    _string(payload["task_id"])
    _integer(payload["contract_revision"], 0)
    target = payload["review_target"]
    _exact_object(target, _TARGET_KEYS)
    for key in ("kind", "value", "base_revision"):
        _string(target[key])
    _integer(target["generation"], 1)
    receipts = payload["receipts"]
    if type(receipts) is not list or not 1 <= len(receipts) <= REVIEW_RESULTS_RECEIPT_LIMIT:
        raise _invalid_input()
    finding_count = 0
    for receipt in receipts:
        _exact_object(receipt, _RECEIPT_KEYS)
        for key in ("reviewer", "kind", "verdict", "summary"):
            _string(receipt[key])
        provenance = receipt["provenance"]
        if provenance is not None:
            _exact_object(provenance, _PROVENANCE_KEYS)
            for key, value in provenance.items():
                if key in _PROVENANCE_IDENTIFIERS and value is None:
                    continue
                if key in _PROVENANCE_ARRAYS:
                    if type(value) is not list:
                        raise _invalid_input()
                    for item in value:
                        _string(item)
                else:
                    _string(value)
        findings = receipt["findings"]
        if type(findings) is not list:
            raise _invalid_input()
        finding_count += len(findings)
        if finding_count > REVIEW_RESULTS_FINDING_LIMIT:
            raise _invalid_input()
        for finding in findings:
            _exact_object(finding, {"severity", "summary"})
            _string(finding["severity"])
            _string(finding["summary"])

    # Inspect the complete supplied declarations before enum, duplicate, text
    # capacity, or provenance-matrix checks; never echo rejected values.
    reject_private_or_raw_content("task_id", payload["task_id"])
    for key in ("kind", "value", "base_revision"):
        reject_private_or_raw_content(f"review_target_{key}", target[key])
    for receipt in receipts:
        for key, field in (
            ("reviewer", "reviewer_key"), ("kind", "receipt_kind"),
            ("verdict", "verdict"), ("summary", "review_receipt_summary"),
        ):
            reject_private_or_raw_content(field, receipt[key])
        if receipt["provenance"] is not None:
            for key, value in receipt["provenance"].items():
                for item in value if type(value) is list else [value]:
                    if item is not None:
                        reject_private_or_raw_content(key, item)
        for finding in receipt["findings"]:
            reject_private_or_raw_content("severity", finding["severity"])
            reject_private_or_raw_content("review_finding_summary", finding["summary"])


def decode_review_results(raw: bytes | str) -> dict[str, Any]:
    """Decode one bounded UTF-8 JSON object; no file or state access."""
    try:
        if type(raw) is bytes:
            if len(raw) > REVIEW_RESULTS_INPUT_LIMIT:
                raise _invalid_input()
            text = raw.decode("utf-8")
        elif type(raw) is str:
            if len(raw.encode("utf-8")) > REVIEW_RESULTS_INPUT_LIMIT:
                raise _invalid_input()
            text = raw
        else:
            raise _invalid_input()
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_invalid_number,
            parse_constant=_invalid_number,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid_input() from exc
    _validate_payload(payload)
    return payload


def normalize_review_results(
    payload: Any,
    *,
    review_tier: int,
    user_approved_reviewers: Sequence[str] = (),
) -> dict[str, Any]:
    """Normalize every new record before the caller acquires the writer."""
    _validate_payload(payload)
    if type(review_tier) is not int or review_tier not in {0, 1, 2}:
        raise _invalid_input()
    if type(user_approved_reviewers) not in {list, tuple}:
        raise _invalid_input()
    try:
        approved: set[str] = set()
        for reviewer in user_approved_reviewers:
            _string(reviewer)
            key = validate_text("reviewer_key", reviewer, required=True, limit=500)
            if key in approved:
                raise _invalid_input()
            approved.add(key)
        target = dict(payload["review_target"])
        if target["kind"] not in REVIEW_TARGET_KINDS:
            raise _invalid_input()
        # Expected identity bytes are checked, not rewritten or Git-resolved.
        validate_text("review_target_value", target["value"], required=True, limit=500)
        validate_text("review_target_base_revision", target["base_revision"], limit=500)
        task_id = validate_task_id(payload["task_id"])
        receipts = []
        reviewer_keys: set[str] = set()
        eligible_approvals: set[str] = set()
        for receipt in payload["receipts"]:
            reviewer = validate_text("reviewer_key", receipt["reviewer"], required=True, limit=500)
            normalized = normalize_receipt(
                review_tier=review_tier,
                reviewer=reviewer,
                kind=receipt["kind"],
                verdict=receipt["verdict"],
                summary=receipt["summary"],
                user_approved=reviewer in approved,
            )
            if reviewer in reviewer_keys:
                raise review_error(
                    "review_receipt_already_recorded",
                    "this reviewer already recorded a receipt for the current target generation",
                    "reviewer_key",
                )
            reviewer_keys.add(reviewer)
            if (
                review_tier == 2
                and normalized["receipt_kind"] == "self_review_fallback"
                and normalized["verdict"] == "pass"
            ):
                eligible_approvals.add(reviewer)
            provenance = receipt["provenance"]
            if (provenance is None) != (normalized["receipt_kind"] == "not_required"):
                raise _invalid_input()
            normalized_provenance = normalize_review_provenance_input(
                receipt_kind=normalized["receipt_kind"],
                **(provenance or {}),
            )
            findings = []
            finding_keys: set[tuple[str, str]] = set()
            for finding in receipt["findings"]:
                severity = validate_choice(
                    "severity", finding["severity"], FINDING_SEVERITIES,
                    "invalid_review_evidence",
                )
                summary = validate_text(
                    "review_finding_summary", finding["summary"], required=True, limit=1000,
                )
                if (severity, summary) in finding_keys:
                    raise _invalid_input()
                finding_keys.add((severity, summary))
                findings.append({"severity": severity, "summary": summary})
            receipts.append({**normalized, "provenance": normalized_provenance, "findings": findings})
        if approved != eligible_approvals:
            raise _invalid_input()
    except TaskValidationError as exc:
        if exc.code == "privacy_rejected":
            raise
        raise _invalid_input() from exc
    except ReviewProvenanceError as exc:
        raise _invalid_input() from exc
    return {
        "task_id": task_id,
        "contract_revision": payload["contract_revision"],
        "review_target": target,
        "receipts": receipts,
    }


def add_review_results(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    payload: Any,
    *,
    user_approved_reviewers: Sequence[str] = (),
    database_target: DatabaseTarget | None = None,
) -> dict[str, Any]:
    """Append the whole batch; caller must commit or roll back on exception.

    The service opens no connection and owns no commit, formatting, or
    maintenance. Existing single-record services retain all evidence writes.
    """
    _validate_payload(payload)
    normalized_task_id = validate_task_id(task_id)
    try:
        expected_task_id = validate_task_id(payload["task_id"])
    except TaskValidationError as exc:
        if exc.code == "privacy_rejected":
            raise
        raise _invalid_input() from exc
    if expected_task_id != normalized_task_id:
        raise _basis_mismatch()
    observed = read_internal_task(connection, project.project_id, normalized_task_id)
    if observed is None:
        raise TaskRepositoryError("not_found", "task was not found")
    reject_done_task_write(observed)
    if (
        int(observed["review_target_generation"]) <= 0
        or not str(observed["review_target_kind"])
        or not str(observed["review_target_value"])
    ):
        raise review_error(
            "review_target_required",
            "set a current review target before recording a receipt",
            "review_target_kind",
        )
    validate_stored_review_target(observed)
    normalized = normalize_review_results(
        payload,
        review_tier=observed["review_tier"],
        user_approved_reviewers=user_approved_reviewers,
    )
    locked = lock_and_reread_target_owner(
        connection, project, normalized_task_id, database_target=database_target,
    )
    reject_concurrent_review_basis_change(
        observed, locked,
        code="review_target_mismatch",
        message="review result Task, Contract, or target does not match the current task",
    )
    expected = normalized["review_target"]
    if normalized["contract_revision"] != locked["current_contract_revision"] or any(
        expected[field] != locked[f"review_target_{field}"]
        for field in ("kind", "value", "base_revision", "generation")
    ):
        raise _basis_mismatch()
    validate_stored_review_target(locked)
    require_current_capture(locked)
    receipts = []
    for entry in normalized["receipts"]:
        provenance = dict(entry["provenance"] or {})
        methods = provenance.pop("method_codes", None)
        receipt = add_review_receipt(
            connection, project, normalized_task_id,
            reviewer=entry["reviewer_key"], kind=entry["receipt_kind"],
            verdict=entry["verdict"], summary=entry["summary"],
            user_approved=bool(entry["user_approved"]),
            review_methods=methods, **provenance,
            database_target=database_target,
        )
        findings = []
        for entry_finding in entry["findings"]:
            finding = add_review_finding(
                connection, project, normalized_task_id,
                receipt_id=receipt.receipt["review_receipt_id"],
                severity=entry_finding["severity"], summary=entry_finding["summary"],
                database_target=database_target,
            )
            findings.append({"finding": finding.finding, "event": finding.event})
        receipts.append({"receipt": receipt.receipt, "event": receipt.event, "findings": findings})
    return {"receipts": receipts}
