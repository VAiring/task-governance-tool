"""Decode fixed external verification results into existing Receipt inputs."""

from __future__ import annotations

import json
from typing import Any

from task_governance_tool.task_values import (
    reject_private_or_raw_content,
    validate_task_id,
)
from task_governance_tool.verification_receipts import (
    VerificationReceiptError,
    normalize_verification_receipt_input,
)


VERIFICATION_RESULT_INPUT_LIMIT = 4096
_FIELDS = {
    "version", "task_id", "result", "duration_ms", "scope_coverage",
    "expected_target_generation",
}


def _invalid() -> VerificationReceiptError:
    return VerificationReceiptError(
        "invalid_verification_evidence",
        "structured verification result is invalid",
        "verification_result",
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _invalid_number(value: str) -> None:
    raise _invalid()


def decode_verification_result(raw: bytes, *, task_id: Any) -> dict[str, Any]:
    """Validate one aggregate declaration; never execute or infer coverage."""
    if type(raw) is not bytes or len(raw) > VERIFICATION_RESULT_INPUT_LIMIT:
        raise _invalid()
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_invalid_number,
            parse_constant=_invalid_number,
        )
        if type(payload) is not dict or set(payload) != _FIELDS:
            raise _invalid()
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise _invalid()
        for key in ("task_id", "result", "scope_coverage"):
            if type(payload[key]) is not str:
                raise _invalid()
            payload[key].encode("utf-8")
        for key in ("duration_ms", "expected_target_generation"):
            if type(payload[key]) is not int:
                raise _invalid()
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc

    for key in ("task_id", "result", "scope_coverage"):
        reject_private_or_raw_content(key, payload[key])
    command_task_id = validate_task_id(task_id)
    reported_task_id = validate_task_id(payload["task_id"])
    values = normalize_verification_receipt_input(
        **{key: payload[key] for key in _FIELDS - {"version", "task_id"}}
    )
    if command_task_id != reported_task_id:
        raise VerificationReceiptError(
            "verification_basis_stale",
            "verification result belongs to a different task",
            "task_id",
        )
    return {
        "result": values.result,
        "duration_ms": values.duration_ms,
        "scope_coverage": values.scope_coverage,
        "expected_target_generation": values.expected_target_generation,
    }
