"""Bounded five-field reviewer answers. Pure decoding; no host or storage I/O."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .task_values import TaskValidationError, reject_private_or_raw_content, validate_text

INPUT_LIMIT = 262_144
ANSWER_KEYS = frozenset(("verdict", "summary", "findings", "checks", "limitations"))


class AnswerError(Exception):
    """Only fixed diagnostics cross the boundary; rejected bodies are never held."""
    def __init__(self, classification="format_invalid", field=None, reason="shape", *,
                 privacy_admissible=False, observed_digest=None):
        self.failure = {"class": classification, "field": field, "reason": reason}
        self.privacy_admissible = privacy_admissible
        self.observed_digest = observed_digest if privacy_admissible else None
        super().__init__(classification)


@dataclass(frozen=True)
class DecodedAnswer:
    raw: bytes
    payload: dict
    payload_start: int
    payload_length: int
    original_digest: str
    payload_digest: str


def byte_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _number(_):
    raise ValueError


def payload_shape(value):
    if type(value) is not dict or set(value) != ANSWER_KEYS:
        raise AnswerError()
    if type(value["verdict"]) is not str or type(value["summary"]) is not str:
        raise AnswerError(reason="type")
    for key in ("findings", "checks", "limitations"):
        if type(value[key]) is not list:
            raise AnswerError(field=key, reason="type")
    for finding in value["findings"]:
        if type(finding) is not dict or set(finding) != {"severity", "summary"}:
            raise AnswerError(field="findings", reason="shape")
        if any(type(finding[k]) is not str for k in finding):
            raise AnswerError(field="findings", reason="type")
    for key in ("checks", "limitations"):
        if any(type(item) is not str for item in value[key]):
            raise AnswerError(field=key + "[]", reason="type")


def scan_strings(value, depth=0):
    """Unicode and structural bounds precede privacy, including for metadata."""
    if depth > 16:
        raise AnswerError(reason="bound")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -(1 << 63) <= value < (1 << 63):
            raise AnswerError(reason="bound")
        return
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise AnswerError(reason="unicode") from None
        return
    if type(value) is list:
        for item in value:
            scan_strings(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for key, item in value.items():
            scan_strings(key, depth + 1)
            scan_strings(item, depth + 1)
        return
    raise AnswerError(reason="type")


def privacy_scan(value):
    if type(value) is str:
        try:
            reject_private_or_raw_content("review_answer", value)
        except TaskValidationError:
            raise AnswerError("privacy_rejected", reason="privacy") from None
    elif type(value) is list:
        for item in value:
            privacy_scan(item)
    elif type(value) is dict:
        for item in value.values():
            privacy_scan(item)


def validate_payload(value, *, observed_digest=None):
    payload_shape(value)
    scan_strings(value)
    privacy_scan(value)
    try:
        storage_summaries(value)
    except AnswerError as error:
        if error.privacy_admissible:
            raise AnswerError(error.failure["class"], error.failure["field"], error.failure["reason"],
                              privacy_admissible=True, observed_digest=observed_digest) from None
        raise
    return _semantic_payload(value, observed_digest=observed_digest)


def storage_summaries(value):
    """The same field validators at admission and the public storage writer."""
    entries = [("summary", "review_receipt_summary", value["summary"])]
    entries.extend(("findings[].summary", "review_finding_summary", finding["summary"])
                   for finding in value["findings"])
    # Check privacy across every field before semantic bounds/missing values.
    for field, stored_field, text in entries:
        try:
            reject_private_or_raw_content(stored_field, text.strip())
        except TaskValidationError:
            raise AnswerError("privacy_rejected", field, "privacy") from None
    normalized = []
    for field, stored_field, text in entries:
        try:
            normalized.append(validate_text(stored_field, text, required=True, limit=1000))
        except TaskValidationError:
            raise AnswerError("capacity_exceeded" if len(text.strip()) > 1000 else "format_invalid",
                              field, "bound" if len(text.strip()) > 1000 else "missing",
                              privacy_admissible=True) from None
    return normalized[0], normalized[1:]


def _semantic_payload(value, *, observed_digest=None):
    def fail(field, reason="choice", classification="format_invalid"):
        raise AnswerError(classification, field, reason, privacy_admissible=True,
                          observed_digest=observed_digest)
    if value["verdict"] not in ("pass", "changes_requested", "unable_to_review"):
        fail("verdict")
    entries = [("summary", value["summary"])]
    if len(value["findings"]) > 64:
        fail("findings", "bound", "capacity_exceeded")
    normalized = set()
    for finding in value["findings"]:
        if finding["severity"] not in ("high", "medium", "low"):
            fail("findings[].severity")
        pair = (finding["severity"], finding["summary"].strip())
        if pair in normalized:
            fail("findings", "duplicate")
        normalized.add(pair)
        entries.append(("findings[].summary", finding["summary"]))
    for key in ("checks", "limitations"):
        if len(value[key]) > 32:
            fail(key, "bound", "capacity_exceeded")
        entries.extend((key + "[]", item) for item in value[key])
    for field, text in entries:
        if not text.strip():
            fail(field, "missing")
        if len(text) > 1000:
            fail(field, "bound", "capacity_exceeded")
    return json.loads(json.dumps(value, ensure_ascii=False))


def _parse_answer(raw):
    if type(raw) is not bytes:
        raise AnswerError(reason="type")
    if not raw or len(raw) > INPUT_LIMIT:
        raise AnswerError("capacity_exceeded", reason="bound")
    try:
        text = raw.decode("utf-8")
        candidate = text.strip(" \t\r\n")
        if candidate.startswith("```json"):
            leading = text[:text.index(candidate)]
            trailing = text[text.index(candidate) + len(candidate):]
            if ((leading and leading.rsplit("\n", 1)[-1]) or
                    (trailing and not trailing.startswith(("\n", "\r\n")))):
                raise ValueError
            match = re.fullmatch(r"```json\r?\n([\s\S]*)\r?\n```", candidate)
            if match is None:
                raise ValueError
            body = match.group(1).strip(" \t\r\n")
            start = len(text[:text.index(candidate) + match.start(1)].encode("utf-8"))
            start += len(match.group(1)[:len(match.group(1)) - len(match.group(1).lstrip(" \t\r\n"))].encode("utf-8"))
        else:
            body = candidate
            start = len(text[:len(text) - len(text.lstrip(" \t\r\n"))].encode("utf-8"))
        value = json.loads(body, object_pairs_hook=_unique, parse_float=_number, parse_constant=_number)
    except UnicodeError:
        raise AnswerError(reason="unicode") from None
    except (ValueError, RecursionError):
        raise AnswerError(reason="unicode" if raw.startswith(b"\xef\xbb\xbf") else "shape") from None
    length = len(body.encode("utf-8"))
    return value, start, length


def decode_answer(raw: bytes) -> DecodedAnswer:
    value, start, length = _parse_answer(raw)
    payload = validate_payload(value, observed_digest=byte_digest(raw))
    return DecodedAnswer(raw, payload, start, length, byte_digest(raw), byte_digest(raw[start:start + length]))


def _decode_matching_payload(raw, payload):
    """Fresh raw admission against a payload validated in this same call.

    Only the provenance builder uses this after validate_payload. Parsing is
    never cached: syntax, exact bytes and byte ranges are admitted anew. A
    different raw payload receives its own validation before binding fails.
    """
    value, start, length = _parse_answer(raw)
    if value != payload:
        validate_payload(value, observed_digest=byte_digest(raw))
        raise AnswerError(reason="identity")
    return DecodedAnswer(raw, payload, start, length, byte_digest(raw), byte_digest(raw[start:start + length]))


def inspect_retirement_rejection(raw, original):
    """Old saved bytes only: return closed facts, never an admissible answer.

    No caller-selectable relaxed decoder exists. The old generic admission must
    pass and the new storage-field policy must reject. Existing hashes are
    compared, never returned or added to the retirement record.
    """
    value, start, length = _parse_answer(raw)
    payload_shape(value)
    scan_strings(value)
    privacy_scan(value)
    _semantic_payload(value)
    if (original["byte_length"] != len(raw) or original["payload_start"] != start
            or original["payload_length"] != length or original["digest"] != byte_digest(raw)
            or original["payload_digest"] != byte_digest(raw[start:start + length])):
        raise AnswerError("binding_mismatch", reason="identity")
    try:
        storage_summaries(value)
    except AnswerError as error:
        if error.failure["class"] == "privacy_rejected":
            return dict(field=error.failure["field"], verdict=value["verdict"],
                        severities=[finding["severity"] for finding in value["findings"]])
        raise
    raise AnswerError("binding_mismatch", reason="identity")
