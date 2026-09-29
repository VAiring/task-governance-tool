"""Invocation-local caller identity; no log discovery or session fallback."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from task_governance_tool.task_values import validation_error


def is_session_id(value: object) -> bool:
    """Accept only the canonical UUID spelling emitted for a Codex thread."""
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


@dataclass(frozen=True)
class CallerIdentity:
    session_id: str | None

    def __post_init__(self) -> None:
        if self.session_id is not None and not is_session_id(self.session_id):
            raise ValueError("invalid caller identity")

    def require(self) -> str:
        if self.session_id is None:
            raise validation_error(
                "session_identity_required", "a caller session identity is required"
            )
        return self.session_id


def capture_caller_identity(
    environment: Mapping[str, str] | None = None,
) -> CallerIdentity:
    """Read the one allowed key once; callers pass this value through services.

    Missing or malformed input stays unknown so read-only commands can proceed.
    Discard the original value instead of retaining or displaying environment data.
    """
    source = os.environ if environment is None else environment
    value = source.get("CODEX_THREAD_ID")
    return CallerIdentity(value if is_session_id(value) else None)
