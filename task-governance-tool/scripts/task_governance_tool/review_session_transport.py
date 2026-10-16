"""Machine-only handoff metadata over unchanged original review-result bytes.

No database, environment or filesystem access. Legacy version-1 result input
retains its existing shape; this envelope is produced by the handoff helper,
not added to the reviewer's judgment template.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

from task_governance_tool.review_session_repository import ReviewSessionBinding, ReviewSessionTarget
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.task_values import validate_text
from task_governance_tool.review_continuation import SelectedReviewer, RetainedOriginal, identifier


FORMAT = "taskgov-review-session-handoff-v1"
CONTINUATION_FORMAT = "taskgov-review-continuation-handoff-v1"
_KEYS = {"version", "session_id", "project_id", "task_id", "contract_revision", "review_target",
         "execution_id", "original_result_digest"}
_CONTINUATION_KEYS = {"preparation_id", "continuation_id", "continuation_sequence"}


@dataclass(frozen=True)
class ContinuationIdentity:
    preparation_id: str
    continuation_id: str
    continuation_sequence: int

    def __post_init__(self):
        from task_governance_tool.review_results import _invalid_input
        if (not identifier(self.preparation_id, "tg_review_preparation_")
            or not identifier(self.continuation_id, "tg_review_continuation_")
            or type(self.continuation_sequence) is not int or not 1 <= self.continuation_sequence <= 64):
            raise _invalid_input()


@dataclass(frozen=True)
class ContinuedResult:
    identity: ContinuationIdentity
    action: str
    prior_receipt_id: str | None

    def __post_init__(self):
        from task_governance_tool.review_results import _invalid_input
        if (type(self.identity) is not ContinuationIdentity or self.action not in ("supplement", "reassess")
            or self.action == "supplement" and self.prior_receipt_id is not None
            or self.action == "reassess" and not identifier(self.prior_receipt_id, "tg_review_receipt_")):
            raise _invalid_input()


@dataclass(frozen=True)
class BoundResult:
    target: ReviewSessionTarget
    binding: ReviewSessionBinding
    continuation: ContinuedResult | None = None


@dataclass(frozen=True)
class ReviewSubmission:
    document: dict
    bindings: tuple[BoundResult, ...] | None
    continuation: ContinuationIdentity | None = None
    retained_receipt_ids: tuple[str, ...] = ()
    retained_originals: tuple[RetainedOriginal, ...] = ()


def validate_context(context, *, allow_unknown_execution: bool = False) -> None:
    from task_governance_tool.review_results import _invalid_input
    if (type(context) is not dict or type(context.get("version")) is not int or context["version"] not in (1, 2)
        or type(context.get("project_id")) is not str):
        raise _invalid_input()
    expected = {"version", "project_id", "execution_id"}
    if context["version"] == 2:
        expected |= _CONTINUATION_KEYS | {"selected_reviewers"}
    if set(context) != expected:
        raise _invalid_input()
    if context["version"] == 2:
        ContinuationIdentity(**{key: context[key] for key in _CONTINUATION_KEYS})
        selected = context["selected_reviewers"]
        if type(selected) is not list or not 1 <= len(selected) <= 8:
            raise _invalid_input()
        for row in selected:
            if type(row) is not dict or set(row) != {"session_id", "action", "prior_receipt_id"}:
                raise _invalid_input()
            SelectedReviewer(**row, reason="Prepared review continuation")
        if len({row["session_id"] for row in selected}) != len(selected):
            raise _invalid_input()
    validate_text("project_id", context["project_id"], required=True, limit=500)
    execution = context["execution_id"]
    if execution is None and allow_unknown_execution and context["version"] == 1:
        return
    if (type(execution) is not str or not execution.startswith("tg_execution_") or len(execution) != 29
        or any(char not in "0123456789abcdef" for char in execution[13:])):
        raise _invalid_input()


def metadata_for(packet: dict, raw: bytes, caller: CallerIdentity) -> dict:
    context = packet["review_session_context"]
    validate_context(context)
    metadata = {"version": context["version"], "session_id": caller.require(), "project_id": context["project_id"],
            "execution_id": context["execution_id"], "task_id": packet["task"]["task_id"],
            "contract_revision": packet["contract"]["revision"], "review_target": dict(packet["review_target"]),
            "original_result_digest": hashlib.sha256(raw).hexdigest()}
    if context["version"] == 2:
        from task_governance_tool.review_results import _invalid_input
        selected = [row for row in context["selected_reviewers"] if row["session_id"] == caller.require()]
        if len(selected) != 1:
            raise _invalid_input()
        metadata.update({key: context[key] for key in _CONTINUATION_KEYS})
        metadata.update({key: selected[0][key] for key in ("action", "prior_receipt_id")})
    return metadata


def validate_packet_metadata(packet, metadata, raw):
    """Compare a machine sidecar to its exact Packet; do not construct a caller."""
    from task_governance_tool.review_results import _basis_mismatch
    bound = validate_metadata(metadata, raw)
    context = packet["review_session_context"]
    validate_context(context)
    if (metadata["version"] != context["version"] or metadata["project_id"] != context["project_id"]
        or metadata["execution_id"] != context["execution_id"]
        or metadata["task_id"] != packet["task"]["task_id"] or metadata["contract_revision"] != packet["contract"]["revision"]
        or metadata["review_target"] != packet["review_target"]):
        raise _basis_mismatch()
    if context["version"] == 2:
        selected = [row for row in context["selected_reviewers"] if row["session_id"] == metadata["session_id"]]
        if (len(selected) != 1 or any(metadata[key] != context[key] for key in _CONTINUATION_KEYS)
            or any(metadata[key] != selected[0][key] for key in ("action", "prior_receipt_id"))):
            raise _basis_mismatch()
    return bound


def metadata_digest(metadata):
    from .evidence_ledger import domain_digest
    return domain_digest(b"taskgov-review-continuation-retained-metadata-v1\0", metadata).removeprefix("sha256:")


def continued_metadata_digest(record, choice, original_digest):
    """Recompute a retained v2 sidecar's structural seal from native fields."""
    basis = record.basis
    return metadata_digest(dict(version=2, project_id=basis["project_id"], execution_id=basis["execution_id"],
        session_id=choice.session_id, task_id=basis["task_id"], contract_revision=basis["contract_revision"],
        review_target={key: basis["target_" + key] for key in ("kind", "value", "base_revision", "generation")},
        original_result_digest=original_digest, preparation_id=record.preparation_id,
        continuation_id=record.continuation_id, continuation_sequence=record.sequence,
        action=choice.action, prior_receipt_id=choice.prior_receipt_id))


def bound_metadata_digest(bound):
    if type(bound) is not BoundResult or bound.continuation is None:
        from .review_results import _invalid_input
        raise _invalid_input()
    target, binding, continued = bound.target, bound.binding, bound.continuation
    return metadata_digest(dict(version=2, project_id=target.project_id, execution_id=binding.execution_id,
        session_id=binding.session_id, task_id=target.task_id, contract_revision=target.contract_revision,
        review_target={key: getattr(target, "target_" + key) for key in ("kind", "value", "base_revision", "generation")},
        original_result_digest=binding.original_result_digest, **continued.identity.__dict__,
        action=continued.action, prior_receipt_id=continued.prior_receipt_id))


def validate_metadata(metadata, raw: bytes) -> BoundResult:
    from task_governance_tool.review_results import decode_review_results, _invalid_input, _basis_mismatch
    if (type(metadata) is not dict or type(metadata.get("version")) is not int or metadata["version"] not in (1, 2)):
        raise _invalid_input()
    expected = _KEYS | (_CONTINUATION_KEYS | {"action", "prior_receipt_id"} if metadata["version"] == 2 else set())
    if set(metadata) != expected:
        raise _invalid_input()
    validate_context({"version": 1, **{key: metadata[key] for key in ("project_id", "execution_id")}})
    continued = None
    if metadata["version"] == 2:
        identity = ContinuationIdentity(**{key: metadata[key] for key in _CONTINUATION_KEYS})
        SelectedReviewer(metadata["session_id"], metadata["action"], metadata["prior_receipt_id"], "Prepared review continuation")
        continued = ContinuedResult(identity, metadata["action"], metadata["prior_receipt_id"])
    document = decode_review_results(raw)
    if not raw.lstrip().startswith(b"{") or len(document["receipts"]) != 1:
        raise _invalid_input()
    if (type(metadata["contract_revision"]) is not int
        or any(metadata[key] != document[key] for key in ("task_id", "contract_revision"))
        or json.dumps(metadata["review_target"], sort_keys=True) != json.dumps(document["review_target"], sort_keys=True)):
        raise _basis_mismatch()
    binding = ReviewSessionBinding(metadata["session_id"], metadata["execution_id"],
                                   "handoff", metadata["original_result_digest"])
    if binding.original_result_digest != hashlib.sha256(raw).hexdigest():
        raise _invalid_input()
    target = document["review_target"]
    return BoundResult(ReviewSessionTarget(metadata["project_id"], document["task_id"],
        document["contract_revision"], target["kind"], target["value"], target["base_revision"], target["generation"]), binding, continued)


def decode_submission(raw: bytes) -> ReviewSubmission:
    from task_governance_tool.review_results import (
        REVIEW_RESULTS_INPUT_LIMIT, REVIEW_RESULTS_RECEIPT_LIMIT,
        decode_review_results, _unique_object, _invalid_number, _invalid_input,
    )
    if type(raw) is not bytes or len(raw) > REVIEW_RESULTS_INPUT_LIMIT:
        raise _invalid_input()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_float=_invalid_number, parse_constant=_invalid_number)
        if type(value) is not dict or "format" not in value:
            return ReviewSubmission(decode_review_results(raw), None)
        from .review_answer_transport import FORMAT as COLLECTED_FORMAT, decode_collected_submission
        if value["format"] == COLLECTED_FORMAT:
            return decode_collected_submission(raw)
        continued = None
        retained = ()
        captures = ()
        expected = {"format", "items"}
        if value["format"] == CONTINUATION_FORMAT:
            expected |= _CONTINUATION_KEYS | {"retained_receipt_ids"}
            if "retained_originals" in value:
                expected.add("retained_originals")
        if (set(value) != expected or value["format"] not in (FORMAT, CONTINUATION_FORMAT)
            or type(value["items"]) is not list or not 1 <= len(value["items"]) <= REVIEW_RESULTS_RECEIPT_LIMIT):
            raise _invalid_input()
        if value["format"] == CONTINUATION_FORMAT:
            continued = ContinuationIdentity(**{key: value[key] for key in _CONTINUATION_KEYS})
            ids = value["retained_receipt_ids"]
            if (type(ids) is not list or len(ids) + len(value["items"]) > REVIEW_RESULTS_RECEIPT_LIMIT
                or any(not identifier(item, "tg_review_receipt_") for item in ids) or len(set(ids)) != len(ids)):
                raise _invalid_input()
            retained = tuple(ids)
            if "retained_originals" in value:
                rows = value["retained_originals"]
                if (type(rows) is not list or not 1 <= len(rows) <= 8
                    or any(type(row) is not dict or set(row) != set(RetainedOriginal.__dataclass_fields__) for row in rows)):
                    raise _invalid_input()
                captures = tuple(RetainedOriginal(**row) for row in rows)
                if len({row.session_id for row in captures}) != len(captures):
                    raise _invalid_input()
        originals, bindings = [], []
        captures_by_session = {row.session_id: row for row in captures}
        consumed = set()
        for item in value["items"]:
            if type(item) is not dict or set(item) != {"original_base64", "binding"} or type(item["original_base64"]) is not str:
                raise _invalid_input()
            original = base64.b64decode(item["original_base64"], validate=True)
            if base64.b64encode(original).decode("ascii") != item["original_base64"]:
                raise _invalid_input()
            bindings.append(validate_metadata(item["binding"], original))
            binding_continuation = bindings[-1].continuation
            if binding_continuation is not None and binding_continuation.identity != continued:
                capture = captures_by_session.get(bindings[-1].binding.session_id)
                if (continued is None or capture is None
                    or binding_continuation.identity.continuation_id != capture.prior_continuation_id
                    or bindings[-1].binding.original_result_digest != capture.original_result_digest
                    or bound_metadata_digest(bindings[-1]) != capture.metadata_digest
                    or capture.session_id in consumed):
                    raise _invalid_input()
                consumed.add(capture.session_id)
            elif bindings[-1].binding.session_id in captures_by_session:
                raise _invalid_input()
            originals.append(original)
        if continued is not None and not any(item.continuation is not None and item.continuation.identity == continued for item in bindings):
            raise _invalid_input()
        if consumed != set(captures_by_session):
            raise _invalid_input()
        document = decode_review_results(b"[" + b",".join(originals) + b"]")
        return ReviewSubmission(document, tuple(bindings), continued, retained, captures)
    except (ValueError, UnicodeError, RecursionError, binascii.Error) as exc:
        raise _invalid_input() from exc


def frame_submission(originals: list[bytes], metadata: list[dict], *,
                     continuation: ContinuationIdentity | None = None,
                     retained_receipt_ids: tuple[str, ...] = (),
                     retained_originals: tuple[RetainedOriginal, ...] = ()) -> bytes:
    from task_governance_tool.review_results import _invalid_input
    if len(originals) != len(metadata) or (continuation is not None and type(continuation) is not ContinuationIdentity):
        raise _invalid_input()
    value = {"format": FORMAT if continuation is None else CONTINUATION_FORMAT, "items": [
        {"original_base64": base64.b64encode(original).decode("ascii"), "binding": binding}
        for original, binding in zip(originals, metadata)
    ]}
    if continuation is not None:
        value.update(continuation.__dict__)
        value["retained_receipt_ids"] = list(retained_receipt_ids)
        if retained_originals:
            value["retained_originals"] = [row.as_dict() for row in retained_originals]
    elif retained_receipt_ids or retained_originals:
        raise _invalid_input()
    framed = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    decode_submission(framed)
    return framed
