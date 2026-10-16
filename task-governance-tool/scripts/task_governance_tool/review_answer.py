"""Bounded five-field reviewer answers. Pure decoding; no host or storage I/O."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .task_values import TaskValidationError, reject_private_or_raw_content

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


def decode_answer(raw: bytes) -> DecodedAnswer:
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
    payload = validate_payload(value, observed_digest=byte_digest(raw))
    length = len(body.encode("utf-8"))
    return DecodedAnswer(raw, payload, start, length, byte_digest(raw), byte_digest(raw[start:start + length]))
