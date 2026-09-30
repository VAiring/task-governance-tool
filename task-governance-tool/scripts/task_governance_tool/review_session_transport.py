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


FORMAT = "taskgov-review-session-handoff-v1"
_KEYS = {"version", "session_id", "project_id", "task_id", "contract_revision", "review_target",
         "execution_id", "original_result_digest"}


@dataclass(frozen=True)
class BoundResult:
    target: ReviewSessionTarget
    binding: ReviewSessionBinding


@dataclass(frozen=True)
class ReviewSubmission:
    document: dict
    bindings: tuple[BoundResult, ...] | None


def validate_context(context, *, allow_unknown_execution: bool = False) -> None:
    from task_governance_tool.review_results import _invalid_input
    if (type(context) is not dict or set(context) != {"version", "project_id", "execution_id"}
        or type(context["version"]) is not int or context["version"] != 1
        or type(context["project_id"]) is not str):
        raise _invalid_input()
    validate_text("project_id", context["project_id"], required=True, limit=500)
    execution = context["execution_id"]
    if execution is None and allow_unknown_execution:
        return
    if (type(execution) is not str or not execution.startswith("tg_execution_") or len(execution) != 29
        or any(char not in "0123456789abcdef" for char in execution[13:])):
        raise _invalid_input()


def metadata_for(packet: dict, raw: bytes, caller: CallerIdentity) -> dict:
    context = packet["review_session_context"]
    validate_context(context)
    return {"version": 1, "session_id": caller.require(), "project_id": context["project_id"],
            "execution_id": context["execution_id"], "task_id": packet["task"]["task_id"],
            "contract_revision": packet["contract"]["revision"], "review_target": dict(packet["review_target"]),
            "original_result_digest": hashlib.sha256(raw).hexdigest()}


def validate_metadata(metadata, raw: bytes) -> BoundResult:
    from task_governance_tool.review_results import decode_review_results, _invalid_input, _basis_mismatch
    if (type(metadata) is not dict or set(metadata) != _KEYS
        or type(metadata["version"]) is not int or metadata["version"] != 1):
        raise _invalid_input()
    validate_context({key: metadata[key] for key in ("version", "project_id", "execution_id")})
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
        document["contract_revision"], target["kind"], target["value"], target["base_revision"], target["generation"]), binding)


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
        if (set(value) != {"format", "items"} or value["format"] != FORMAT
            or type(value["items"]) is not list or not 1 <= len(value["items"]) <= REVIEW_RESULTS_RECEIPT_LIMIT):
            raise _invalid_input()
        originals, bindings = [], []
        for item in value["items"]:
            if type(item) is not dict or set(item) != {"original_base64", "binding"} or type(item["original_base64"]) is not str:
                raise _invalid_input()
            original = base64.b64decode(item["original_base64"], validate=True)
            if base64.b64encode(original).decode("ascii") != item["original_base64"]:
                raise _invalid_input()
            bindings.append(validate_metadata(item["binding"], original))
            originals.append(original)
        document = decode_review_results(b"[" + b",".join(originals) + b"]")
        return ReviewSubmission(document, tuple(bindings))
    except (ValueError, UnicodeError, RecursionError, binascii.Error) as exc:
        raise _invalid_input() from exc


def frame_submission(originals: list[bytes], metadata: list[dict]) -> bytes:
    from task_governance_tool.review_results import _invalid_input
    if len(originals) != len(metadata):
        raise _invalid_input()
    framed = json.dumps({"format": FORMAT, "items": [
        {"original_base64": base64.b64encode(original).decode("ascii"), "binding": binding}
        for original, binding in zip(originals, metadata)
    ]}, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    decode_submission(framed)
    return framed
