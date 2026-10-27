"""Pure validation of sanitized v4 display bytes, never source evidence.

The closed shapes below cover only fields emitted by the existing projections.
They are not substitutes for Task, Receipt, Bundle or completion admission.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .task_values import KINDS, PRIORITIES, STATUSES, reject_private_or_raw_content


def _bad():
    from .viewer import ViewerError
    return ViewerError("internal_error", "Viewer display cache is invalid")


@lru_cache(maxsize=16384)
def _bounded_text_predicate(predicate, field, value):
    """Memoize only successful pure checks of bounded display strings.

    The source field and callable are part of the key: different field policies
    or a replacement implementation cannot reuse another predicate's result.
    No DB identity, row, evidence,
    admission result or private/rejected value is retained here.
    """
    predicate(field, value)


class DisplayValidation:
    """Exact-value pure predicates, scoped to one publication attempt only.

    These sets retain references to already resident display strings. A bounded
    process memo also shares successful short-string privacy predicates. They
    certify neither source rows nor another transaction/attempt; shape, field
    limits, relationships and independent cache digests are always checked.
    """
    def __init__(self):
        self._text = set()
        self._time = set()

    def text(self, field, value):
        key = (reject_private_or_raw_content, field, value)
        if key not in self._text:
            if len(value) <= 1000:
                _bounded_text_predicate(*key)
            else:
                reject_private_or_raw_content(field, value)
            self._text.add(key)

    def timestamp(self, value):
        from .storage import validate_utc_timestamp
        if value not in self._time:
            validate_utc_timestamp(value, field="Viewer display time")
            self._time.add(value)


def _text(field, limit=None, *, required=False):
    def check(value, validation):
        if (type(value) is not str or required and not value.strip()
                or limit is not None and len(value) > limit):
            raise _bad()
        try:
            validation.text(field, value)
        except Exception as exc:
            raise _bad() from exc
    return check


def _integer(minimum=0, maximum=2**63 - 1):
    def check(value, validation):
        if type(value) is not int or not minimum <= value <= maximum:
            raise _bad()
    return check


def _choice(*values):
    def check(value, validation):
        if not any(type(value) is type(item) and value == item for item in values):
            raise _bad()
    return check


def _nullable(check):
    def wrapped(value, validation):
        if value is not None:
            check(value, validation)
    return wrapped


def _timestamp(value, validation):
    if type(value) is not str:
        raise _bad()
    try:
        validation.timestamp(value)
    except Exception as exc:
        raise _bad() from exc


def _shape(fields):
    def check(value, validation):
        if type(value) is not dict or set(value) != set(fields):
            raise _bad()
        for key, validate in fields.items():
            validate(value[key], validation)
    return check


def _array(check, maximum=None):
    def validate(value, validation):
        if type(value) is not list or maximum is not None and len(value) > maximum:
            raise _bad()
        for item in value:
            check(item, validation)
    return validate


_id = _text("identifier", 128, required=True)
_count = _integer()
_boolean = _choice(True, False)
_target_kind = _choice("", "git_commit", "diff_fingerprint", "external_revision", "git_snapshot")
_evidence_kind = _choice("none", "git_commit", "commit_not_required", "external_revision", "legacy_unverified")
_target = {"kind": _target_kind, "value": _text("review_target_value", 500), "generation": _count}
_task_fields = {
    "task_id": _id, "project_id": _text("project_id", required=True), "title": _text("title", 200, required=True),
    "description": _text("description", 4000), "kind": _choice(*KINDS), "lane": _text("lane"),
    "lane_order": _nullable(_integer(-(2**63))), "priority": _choice(*PRIORITIES),
    "status": _choice(*STATUSES), "blocked_reason": _text("blocked_reason"), "pause_reason": _text("pause_reason", 1000),
    "review_tier": _integer(0, 2), "verification": _text("verification", 1000), "tags": _text("tags", 500),
    "created_at": _timestamp, "updated_at": _timestamp, "completed_at": _nullable(_timestamp),
    "completion_commit_required": _integer(0, 1), "completion_commit_hash": _text("completion_revision", 500),
    "completion_evidence_kind": _evidence_kind, "completion_evidence_revision": _text("completion_revision", 500),
    "completion_evidence_reason": _text("completion_evidence_reason", 1000), "external_revision_approved": _integer(0, 1),
    "review_target_kind": _target_kind, "review_target_value": _text("review_target_value", 500),
    "review_target_generation": _count,
}
_event_fields = {
    "task_event_id": _id, "task_id": _id, "project_id": _text("project_id", required=True),
    "event_type": _text("event_type", required=True), "summary": _text("event_summary", 1000), "created_at": _timestamp,
}
_receipt_fields = {
    "review_receipt_id": _id, "task_id": _id, "project_id": _text("project_id", required=True),
    "reviewer_key": _text("reviewer_key", 500, required=True),
    "receipt_kind": _choice("independent", "self_review_fallback", "not_required"),
    "verdict": _choice("pass", "changes_requested", "unable_to_review", "not_required"),
    "target_kind": _target_kind, "target_value": _text("review_target_value", 500), "target_generation": _count,
    "summary": _text("review_receipt_summary", 1000), "user_approved": _integer(0, 1), "created_at": _timestamp,
    "judgment_revision": _integer(1),
}
_finding_fields = {
    "review_finding_id": _id, "review_receipt_id": _id,
    "severity": _choice("high", "medium", "low"), "status": _choice("open", "resolved"),
    "summary": _text("review_finding_summary", 1000, required=True), "resolution_summary": _text("review_finding_resolution", 1000),
    "created_at": _timestamp, "resolved_at": _nullable(_timestamp),
    "target_generation": _count, "reviewer_key": _text("reviewer_key", 500, required=True),
}
_review_fields = {
    "target": _shape(_target),
    "gate": _shape({
        "review_tier": _integer(0, 2), "required_independent_passes": _integer(0, 2),
        "qualifying_independent_passes": _count,
        "fallback_kind": _choice(None, "self_review_fallback", "not_required"), "satisfied": _boolean,
    }),
    "counts": _shape({key: _count for key in (
        "receipts_total", "receipts_current_generation", "changes_requested_current_generation",
        "open_high", "open_medium", "open_low", "unable_current_generation")}),
    "blocking_findings": _array(_shape({**_finding_fields,
        "blocking_reason": _choice("unresolved", "fresh_review_required")}), 10),
    "recent_receipts": _array(_shape(_receipt_fields), 10),
    "recent_findings": _array(_shape(_finding_fields), 10),
    "continuation": _nullable(_shape({
        "state": _choice("pending", "settled"), "pending_continuation_id": _nullable(_id),
        "pending_sessions": _array(_id), "effective_receipt_ids": _array(_id),
        "suspended_receipt_ids": _array(_id),
    })),
}
_cycle_fields = {
    "completion_cycle_id": _id, "saved_cycle_ordinal": _integer(1),
    "origin": _choice("native_done", "legacy_current_done"),
    "completeness": _choice("complete", "partial"), "completed_at": _nullable(_timestamp),
    "contract_revision": _count, "review_tier": _integer(0, 2),
    "verification_expectation": _choice("specified", "unspecified"),
    "verification_attestation": _choice(True, None),
    "completion_evidence": _shape({
        "kind": _evidence_kind, "revision": _text("completion_revision", 500), "reason": _text("completion_evidence_reason", 1000),
        "external_revision_approved": _boolean, "completion_commit_required": _boolean,
        "completion_commit_hash": _text("completion_commit_hash", 500),
    }),
    "review_target": _shape({**_target, "base_revision": _text("review_target_base_revision", 500)}),
    "gate_basis": _shape({
        "version": _integer(0, 2),
        "kind": _choice("unknown", "not_required", "independent_passes", "self_review_fallback"),
        **{key: _nullable(_count) for key in (
            "required_independent_passes", "qualifying_independent_passes", "changes_requested",
            "open_high", "open_medium", "fresh_review_required")},
        "qualifying_receipt_ids": _array(_id, 2),
    }),
}
_history_fields = {
    "total": _count, "returned_count": _integer(0, 10), "truncated": _boolean,
    "legacy_history_incomplete": _boolean, "cycles": _array(_shape(_cycle_fields), 10),
}


def display_order(task):
    """The existing SQL priority/lane/null-last/creation/ID order."""
    return ({"urgent": 0, "high": 1, "normal": 2, "low": 3}[task["priority"]],
            task["lane"], task["lane_order"] is None, task["lane_order"] or 0,
            task["created_at"], task["task_id"])


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


def validate_display_tasks(tasks, *, project_id, validation=None):
    from .tasks import VIEWER_TASK_FIELDS
    if set(VIEWER_TASK_FIELDS) != set(_task_fields):
        raise _bad()
    check = _shape({**_task_fields, "events": _array(_shape(_event_fields), 10),
                    "review_evidence": _shape(_review_fields),
                    "completion_history": _shape(_history_fields)})
    _array(check)(tasks, validation if validation is not None else DisplayValidation())
    identifiers = set()
    previous = None
    for task in tasks:
        key = display_order(task)
        if (task["project_id"] != project_id or task["task_id"] in identifiers
                or previous is not None and key <= previous):
            raise _bad()
        identifiers.add(task["task_id"])
        previous = key
        review = task["review_evidence"]
        if review["target"] != {"kind": task["review_target_kind"],
                                "value": task["review_target_value"],
                                "generation": task["review_target_generation"]}:
            raise _bad()
        if review["gate"]["review_tier"] != task["review_tier"]:
            raise _bad()
        for item in [*task["events"], *review["recent_receipts"]]:
            if item["project_id"] != project_id or item["task_id"] != task["task_id"]:
                raise _bad()
        for sequence in (task["events"], review["recent_receipts"], review["recent_findings"],
                         review["blocking_findings"]):
            times = [item["created_at"] for item in sequence]
            if times != sorted(times, reverse=True):
                raise _bad()
        history = task["completion_history"]
        returned = len(history["cycles"])
        if (history["returned_count"] != returned or history["total"] < returned
                or history["truncated"] != (returned < history["total"]) or _size(history) > 32768):
            raise _bad()
        ordinals = [cycle["saved_cycle_ordinal"] for cycle in history["cycles"]]
        if ordinals != sorted(set(ordinals), reverse=True):
            raise _bad()
        if any(_size(cycle) > 8192 for cycle in history["cycles"]):
            raise _bad()
