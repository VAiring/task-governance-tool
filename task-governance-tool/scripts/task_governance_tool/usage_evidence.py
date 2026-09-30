"""Closed, metadata-only immutable usage snapshot format. No I/O or gates."""

from __future__ import annotations

import hashlib
import json
import re

from task_governance_tool.usage_values import GAP_CODES, METRICS, UsageError, label


FORMAT = "taskgov-usage-snapshot-v1"
ALGORITHM = "inclusive-turn-components-v1"
MAX_DOCUMENT_BYTES = 16_777_216
GAPS = GAP_CODES | {"boundary_unknown", "review_boundary_unknown", "interval_open",
                    "collection_pending", "usage_pending", "operation_unbound"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_EXECUTION = re.compile(r"tg_execution_[0-9a-f]{16}\Z")


def encode(value) -> bytes:
    document = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    if len(document) > MAX_DOCUMENT_BYTES:
        raise UsageError()
    return document


def digest(value) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def snapshot(project_id, component, predecessors=()):
    gaps = sorted(set(component["diagnostics"]))
    quality = "conflicting" if "response_conflict" in gaps else "incomplete" if gaps else "pending"
    body = {"format": FORMAT, "algorithm": ALGORITHM, "project_id": project_id,
            "executions": sorted(component["executions"]),
            "response_set_digest": digest(sorted(component["response_keys"])),
            "response_count": len(component["response_keys"]), "models": component["models"],
            "coverage": "registered_only", "quality": quality, "gaps": gaps,
            "predecessors": sorted(predecessors)}
    result = {**body, "snapshot_id": digest(body)}
    validate(result, project_id)
    return result


def validate(value, project_id):
    """Stored JSON is untrusted: admit only this numerical format before display."""
    expected = {"format", "algorithm", "project_id", "executions", "response_set_digest",
                "response_count", "models", "coverage", "quality", "gaps", "predecessors", "snapshot_id"}
    if (not isinstance(value, dict) or set(value) != expected
            or value["format"] != FORMAT or value["algorithm"] != ALGORITHM
            or value["project_id"] != project_id or value["coverage"] != "registered_only"
            or value["quality"] not in {"pending", "incomplete", "conflicting"}
            or type(value["response_count"]) is not int or value["response_count"] < 0):
        raise UsageError()
    for field, pattern in (("executions", _EXECUTION), ("predecessors", _HEX)):
        items = value[field]
        if (not isinstance(items, list) or any(not isinstance(item, str) or not pattern.fullmatch(item) for item in items)
                or items != sorted(set(items))):
            raise UsageError()
    if not value["executions"]:
        raise UsageError()
    gaps = value["gaps"]
    if (not isinstance(gaps, list) or any(not isinstance(gap, str) or gap not in GAPS for gap in gaps)
            or gaps != sorted(set(gaps))):
        raise UsageError()
    quality = "conflicting" if "response_conflict" in gaps else "incomplete" if gaps else "pending"
    if value["quality"] != quality:
        raise UsageError()
    for field in ("response_set_digest", "snapshot_id"):
        if not isinstance(value[field], str) or not _HEX.fullmatch(value[field]):
            raise UsageError()
    models = value["models"]
    if not isinstance(models, list):
        raise UsageError()
    seen, count = set(), 0
    for model in models:
        if (not isinstance(model, dict) or set(model) != {"provider", "model", "response_count", *METRICS}
                or not isinstance(model["provider"], str) or label(model["provider"]) != model["provider"]
                or (model["model"] is not None and label(model["model"]) != model["model"])
                or type(model["response_count"]) is not int or model["response_count"] <= 0):
            raise UsageError()
        key = (model["provider"], model["model"])
        if key in seen:
            raise UsageError()
        seen.add(key)
        count += model["response_count"]
        for index, name in enumerate(METRICS):
            item = model[name]
            if not (index >= 3 and item is None) and (type(item) is not int or item < 0):
                raise UsageError()
        if model["total_tokens"] != model["input_tokens"] + model["output_tokens"]:
            raise UsageError()
        for part, whole in (("cached_input_tokens", "input_tokens"),
                            ("reasoning_output_tokens", "output_tokens"),
                            ("cache_write_input_tokens", "input_tokens")):
            if model[part] is not None and model[part] > model[whole]:
                raise UsageError()
    if count != value["response_count"] or value["snapshot_id"] != digest({k: v for k, v in value.items() if k != "snapshot_id"}):
        raise UsageError()
    return value
