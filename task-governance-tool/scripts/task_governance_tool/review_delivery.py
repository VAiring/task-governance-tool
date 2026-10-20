"""Transient bounded review output, measured after JSON transport escaping."""

from __future__ import annotations

import codecs
import hashlib
import json

from task_governance_tool import review_handoff_transport as files
from task_governance_tool.git_snapshot import GitSnapshotError, run_git_stream

TEXT_BUDGET = 5000
OUTPUT_BUDGET = 24000
WRAPPER_RESERVE = 1024


class _NonText(Exception):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def transport_size(value):
    # Include stdout LF, exec output JSON, and a second JSON string transport.
    # UTF-8 bytes are a conservative token allowance, not tokenizer measurements.
    output = encode(value).decode("utf-8") + "\n"
    return len(encode({"text": encode({"output": output}).decode("utf-8")})) + WRAPPER_RESERVE


def fits(value):
    return transport_size(value) <= OUTPUT_BUDGET


def require_fit(value):
    if not fits(value):
        files.fail("review_delivery_budget_exceeded")
    return value


GUIDANCE = (
    "One response per tool call: reserve at least 24000 output tokens in both the execution tool "
    "and outer return; do not batch outputs or duplicate them. Body and diff totals each have a "
    "separate 5000 allowance, conservatively charged in UTF-8 bytes, not exact model tokens; "
    "the full response is bounded to 24000 bytes. Lower host limits or extra wrappers can still "
    "truncate: recover only missing ranges. "
    "List/partial delivery is not full material, omitted entries remain in scope, and delivery is "
    "never review coverage or PASS. Reuse only complete matching immutable objects."
)


def range_result(raw, identity, command, *, start=0, expected=None, limit=TEXT_BUDGET, metadata=None):
    """Bound a UTF-8 document already held by its owner; never silently cut it."""
    digest = hashlib.sha256(raw).hexdigest()
    return _range(raw[start:start + limit + 4], len(raw), digest, identity, command,
                  start=start, expected=expected, limit=limit, metadata=metadata)


def _range(window, total, digest, identity, command, *, start, expected, limit, metadata=None):
    if start < 0 or start > total or (expected is not None and expected != digest):
        files.fail("review_material_range_mismatch")
    if window and window[0] & 0xC0 == 0x80:
        files.fail("handoff_invalid_arguments")
    end = min(limit, len(window))
    while end < len(window) and window[end] & 0xC0 == 0x80:
        end -= 1
    while True:
        last = start + end
        more = last < total
        result = {**(metadata or {}), "status": "partial" if start or more else "complete", "identity": identity,
                  "content_sha256": digest, "start": start, "end": last, "total_bytes": total,
                  "text": window[:end].decode("utf-8"), "next_start": last if more else None,
                  "next_command": command(last, digest) if more else None,
                  "instructions": "UTF-8 byte interval [start,end). Join contiguous ranges with matching identity/hash. Last range alone is not the full document. " + GUIDANCE}
        if fits(result):
            return result
        if end == 0:
            files.fail("review_delivery_budget_exceeded")
        end -= 1
        while end and window[end] & 0xC0 == 0x80:
            end -= 1


def git_range(repo, arguments, identity, command, *, start=0, expected=None):
    """Hash/validate a full immutable stream, retaining only the requested window."""
    if start < 0:
        files.fail("handoff_invalid_arguments")
    total = 0
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    window = bytearray()

    def consume(chunk):
        nonlocal total
        if b"\0" in chunk:
            raise _NonText
        try:
            decoder.decode(chunk)
        except UnicodeError as exc:
            raise _NonText from exc
        digest.update(chunk)
        low, high = max(start, total), min(start + TEXT_BUDGET + 4, total + len(chunk))
        if low < high:
            window.extend(chunk[low - total:high - total])
        total += len(chunk)

    try:
        run_git_stream(repo, arguments, consume)
        decoder.decode(b"", final=True)
    except GitSnapshotError:
        files.fail("review_material_unavailable")
    except (UnicodeError, _NonText):
        files.fail("review_material_non_text")
    return _range(window, total, digest.hexdigest(), identity, command,
                  start=start, expected=expected, limit=TEXT_BUDGET)
