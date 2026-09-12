"""Transient Packet freshness binding over already validated public Task data."""

import hashlib
import json
from typing import Any


def review_packet_binding(task: dict[str, Any], contract_revision: int) -> str:
    """Bind the full Task projection and immutable Contract revision, not authority."""
    payload = json.dumps(
        {"task": task, "contract_revision": contract_revision},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(b"taskgov-review-packet-binding-v1\0" + payload).hexdigest()
