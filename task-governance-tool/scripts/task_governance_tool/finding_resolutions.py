"""Decode explicit groups of already-confirmed Finding resolutions."""

from __future__ import annotations

import json
from typing import Any

from task_governance_tool.reviews import FINDING_RESOLUTION_BATCH_LIMIT, review_error
from task_governance_tool.task_values import validate_task_id, validate_text


FINDING_RESOLUTIONS_INPUT_LIMIT = 262144


def _invalid():
    return review_error("invalid_review_evidence", "structured Finding resolutions are invalid")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _invalid_number(value: str) -> None:
    raise _invalid()


def _string(value: Any) -> str:
    if type(value) is not str:
        raise _invalid()
    value.encode("utf-8")
    return value


def decode_finding_resolutions(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or len(raw) > FINDING_RESOLUTIONS_INPUT_LIMIT:
        raise _invalid()
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_float=_invalid_number, parse_constant=_invalid_number,
        )
        if type(payload) is not dict or set(payload) != {"version", "task_id", "resolutions"}:
            raise _invalid()
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise _invalid()
        task_id = validate_task_id(_string(payload["task_id"]))
        groups = payload["resolutions"]
        if type(groups) is not list or not 1 <= len(groups) <= FINDING_RESOLUTION_BATCH_LIMIT:
            raise _invalid()
        items = []
        seen = set()
        for group in groups:
            if type(group) is not dict or set(group) != {"finding_ids", "resolution"}:
                raise _invalid()
            ids = group["finding_ids"]
            if type(ids) is not list or not ids or len(items) + len(ids) > FINDING_RESOLUTION_BATCH_LIMIT:
                raise _invalid()
            resolution = validate_text(
                "review_finding_resolution", _string(group["resolution"]), required=True, limit=1000,
            )
            for value in ids:
                finding_id = validate_text("review_finding_id", _string(value), required=True, limit=128)
                if finding_id in seen:
                    raise _invalid()
                seen.add(finding_id)
                items.append({"finding_id": finding_id, "resolution": resolution})
        return {"task_id": task_id, "resolutions": items}
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
