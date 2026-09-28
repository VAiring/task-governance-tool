"""Structural verification declaration; never interpret Task prose."""

from __future__ import annotations

from typing import Any

from task_governance_tool.task_values import validate_text, validation_error


VERIFICATION_NOT_REQUIRED_REASON_LIMIT = 1_000


def verification_requirement(verification: str, reason: str = "") -> str:
    """Classify validated fields; a blank field is not a waiver."""
    if verification.strip():
        if reason.strip():
            raise validation_error(
                "invalid_argument",
                "verification and verification_not_required_reason conflict",
                "verification",
            )
        return "required"
    return "not_required" if reason.strip() else "unspecified"


def normalize_not_required_reason(value: Any) -> str:
    return validate_text(
        "verification_not_required_reason", value, required=True,
        limit=VERIFICATION_NOT_REQUIRED_REASON_LIMIT,
    )


def merge_declaration_fields(
    base: dict[str, Any], override: dict[str, Any],
) -> dict[str, Any]:
    """One explicit input pair replaces the inherited pair, including blanks."""
    result = dict(base)
    if "verification" in override or "verification_not_required_reason" in override:
        result["verification"] = ""
        result["verification_not_required_reason"] = ""
    result.update(override)
    return result
