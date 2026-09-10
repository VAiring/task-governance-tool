"""Decode explicit Task sets; scope and permission decisions stay with callers."""

from __future__ import annotations

import json
from typing import Any

from task_governance_tool.contract_content import CONTRACT_LIMITS
from task_governance_tool.task_values import (
    KINDS, PRIORITIES, STATUSES, TASK_VERIFICATION_INPUT_LIMIT, TEXT_LIMITS,
    validate_choice, validate_lane, validate_lane_order, validate_review_tier,
    validate_text, validation_error,
)
from task_governance_tool.tasks import TASK_BATCH_LIMIT, validate_task_input


TASK_REGISTRATION_INPUT_LIMIT = 262144
TASK_REGISTRATION_FIELDS = frozenset({
    "title", "description", "kind", "lane", "lane_order", "priority", "status",
    "blocked_reason", "review_tier", "verification", "tags",
})


def _invalid():
    return validation_error("invalid_argument", "structured Task registration is invalid")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _invalid_number(value: str) -> None:
    raise _invalid()


def _task_fields(values: dict[str, Any]) -> None:
    """Validate even common values overridden by every item, without inference."""
    for key, value in values.items():
        if key == "contract":
            continue
        if key in {"lane_order", "review_tier"}:
            if not (key == "lane_order" and value is None) and type(value) is not int:
                raise _invalid()
            (validate_lane_order if key == "lane_order" else validate_review_tier)(value)
            continue
        if type(value) is not str:
            raise _invalid()
        value.encode("utf-8")
        if key in {"kind", "priority", "status"}:
            choices = {"kind": KINDS, "priority": PRIORITIES, "status": STATUSES}[key]
            validate_choice(key, value, choices, "invalid_" + key)
        elif key == "lane":
            validate_lane(value)
        else:
            limit = TASK_VERIFICATION_INPUT_LIMIT if key == "verification" else TEXT_LIMITS.get(key)
            validate_text(key, value, required=key == "title", limit=limit)


def _contract_fields(value: Any, *, common: bool) -> dict[str, str]:
    allowed = {"constraints", "authority_ref"} if common else {
        "scope", "acceptance", "constraints", "authority_ref",
    }
    if type(value) is not dict or set(value) - allowed:
        raise _invalid()
    if not common and not {"scope", "acceptance"} <= set(value):
        raise _invalid()
    for key, text in value.items():
        if type(text) is not str:
            raise _invalid()
        text.encode("utf-8")
        validate_text("contract_" + key, text, required=key in {"scope", "acceptance"},
                      limit=CONTRACT_LIMITS["contract_" + key])
        if key == "authority_ref" and ("\n" in text.strip() or "\r" in text.strip()):
            raise _invalid()
    return value


def decode_task_registration(raw: bytes) -> list[dict[str, Any]]:
    if type(raw) is not bytes or len(raw) > TASK_REGISTRATION_INPUT_LIMIT:
        raise _invalid()
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_float=_invalid_number, parse_constant=_invalid_number)
        if type(payload) is not dict or set(payload) != {"version", "common", "tasks"}:
            raise _invalid()
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise _invalid()
        common, items = payload["common"], payload["tasks"]
        if type(common) is not dict or set(common) - (TASK_REGISTRATION_FIELDS - {"title"} | {"contract"}):
            raise _invalid()
        if type(items) is not list or not 1 <= len(items) <= TASK_BATCH_LIMIT:
            raise _invalid()
        _task_fields(common)
        common_contract = _contract_fields(common.get("contract", {}), common=True)
        result = []
        for item in items:
            if (type(item) is not dict or set(item) - (TASK_REGISTRATION_FIELDS | {"contract"})
                    or not {"title", "contract"} <= set(item)):
                raise _invalid()
            _task_fields(item)
            merged = {**common, **item}
            if "review_tier" not in merged:
                raise _invalid()
            contract = merged.pop("contract")
            normalized = validate_task_input(**merged)
            if contract is not None:
                explicit = _contract_fields(contract, common=False)
                normalized.update({"contract_" + key: value
                                   for key, value in {**common_contract, **explicit}.items()})
            result.append(normalized)
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
