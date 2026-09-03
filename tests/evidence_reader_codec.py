"""Independent, test-only canonical JSON codec retained from M23."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NoReturn


@dataclass(frozen=True)
class EvidenceCodecError(ValueError):
    """One fixed rejection at a test-only Evidence-codec boundary."""

    code: str = "evidence_codec_invalid"
    message: str = "Evidence JSON is invalid"

    def __str__(self) -> str:
        return self.message


def _invalid(
    code: str = "evidence_codec_invalid",
    message: str = "Evidence JSON is invalid",
) -> NoReturn:
    raise EvidenceCodecError(code, message)


def _escape_string(value: str) -> bytes:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise EvidenceCodecError() from exc
    short = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    result = ['"']
    for character in value:
        replacement = short.get(character)
        if replacement is not None:
            result.append(replacement)
        elif ord(character) < 0x20:
            result.append(f"\\u00{ord(character):02x}")
        else:
            result.append(character)
    result.append('"')
    return "".join(result).encode("utf-8", errors="strict")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode the exact integer-only canonical JSON form without an LF."""

    active: set[int] = set()

    def encode(item: Any) -> bytes:
        if item is None:
            return b"null"
        if type(item) is bool:
            return b"true" if item else b"false"
        if type(item) is int:
            return str(item).encode("ascii")
        if type(item) is str:
            return _escape_string(item)
        if type(item) is list:
            identity = id(item)
            if identity in active:
                _invalid()
            active.add(identity)
            try:
                return b"[" + b",".join(encode(value) for value in item) + b"]"
            finally:
                active.remove(identity)
        if type(item) is dict:
            identity = id(item)
            if identity in active or any(type(key) is not str for key in item):
                _invalid()
            active.add(identity)
            try:
                return b"{" + b",".join(
                    _escape_string(key) + b":" + encode(item[key])
                    for key in sorted(item)
                ) + b"}"
            finally:
                active.remove(identity)
        _invalid()

    return encode(value)


def _pairs_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _reject_float(_value: str) -> NoReturn:
    _invalid()


def parse_canonical_json_document(
    document: bytes,
    *,
    maximum: int,
) -> Any:
    """Parse one duplicate-free canonical JSON document with exactly one LF."""

    if (
        type(document) is not bytes
        or type(maximum) is not int
        or maximum < 2
        or len(document) < 2
        or len(document) > maximum
        or not document.endswith(b"\n")
    ):
        _invalid()
    body = document[:-1]
    try:
        decoded = body.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_pairs_without_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except EvidenceCodecError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvidenceCodecError() from exc
    if canonical_json_bytes(value) != body:
        _invalid()
    return value
