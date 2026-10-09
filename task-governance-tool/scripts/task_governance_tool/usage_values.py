"""Closed, metadata-only values shared by the numerical adapter and repository."""

from __future__ import annotations

from dataclasses import dataclass
import re

from task_governance_tool.session_identity import is_session_id


METRICS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
           "reasoning_output_tokens", "cache_write_input_tokens")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}\Z")
RESPONSE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
GAP_CODES = frozenset({
    "invalid_record", "invalid_usage", "legacy_usage", "model_unknown",
    "partial_tail", "source_unreadable", "source_changed", "source_replaced",
    "response_conflict", "cursor_stale", "record_too_large", "prefix_verification_deferred",
})


class UsageError(Exception):
    """Fixed diagnostic only; never wrap source text or OS/SQLite messages."""

    def __init__(self, code: str = "usage_unavailable") -> None:
        self.code = code
        super().__init__(code)


def label(value: object) -> str | None:
    return value if isinstance(value, str) and LABEL.fullmatch(value) else None


def counters(value: object) -> tuple[int | None, ...]:
    if not isinstance(value, dict):
        raise UsageError("invalid_usage")
    result = []
    for index, name in enumerate(METRICS):
        item = value.get(name)
        if item is None and index >= 3:
            result.append(None)
        elif type(item) is not int or not 0 <= item <= 2**63 - 1:
            raise UsageError("invalid_usage")
        else:
            result.append(item)
    if result[2] != result[0] + result[1]:
        raise UsageError("invalid_usage")
    for part, whole in ((3, 0), (4, 1), (5, 0)):
        if result[part] is not None and result[part] > result[whole]:
            raise UsageError("invalid_usage")
    return tuple(result)


@dataclass(frozen=True)
class ResponseUsage:
    provider: str
    response_id: str
    thread_id: str
    turn_id: str
    model: str | None
    effort: str | None
    counts: tuple[int | None, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.provider, str) or label(self.provider) != self.provider
                or not isinstance(self.response_id, str)
                or not RESPONSE_ID.fullmatch(self.response_id)
                or not is_session_id(self.thread_id) or not is_session_id(self.turn_id)
                or (self.model is not None and label(self.model) != self.model)
                or (self.effort is not None and label(self.effort) != self.effort)
                or len(self.counts) != len(METRICS)
                or counters(dict(zip(METRICS, self.counts))) != self.counts):
            raise UsageError("invalid_usage")
