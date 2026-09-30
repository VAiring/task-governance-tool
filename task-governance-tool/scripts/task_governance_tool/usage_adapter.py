"""Versioned Codex JSONL adapter. No discovery, SQLite, or private-body hashes.

The caller supplies exactly one registered source. Re-reading the sanitized
prefix validates append continuity; only new complete records enter the batch.
Unrelated JSON fields are discarded before any digest or durable value exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.state_paths import inspect_physical_file, StatePathError
from task_governance_tool.usage_values import ResponseUsage, UsageError, counters, label


ADAPTER_VERSION = "codex-response-usage-v1"
MAX_LINE = 8 * 1024 * 1024
MAX_BATCH_RECORDS = 4096


@dataclass(frozen=True)
class SourceInput:
    thread_id: str
    path: Path = field(repr=False)
    allowed_root: Path = field(repr=False)
    project_root: Path = field(repr=False)

    @property
    def source_id(self) -> str:
        # Path identity is not a body hash and is never displayed as a raw path.
        return hashlib.sha256(os.path.normcase(os.path.abspath(self.path)).encode()).hexdigest()


@dataclass(frozen=True)
class Cursor:
    incarnation: int = 0
    offset: int = 0
    prefix: str = ""
    file_id: str = ""


@dataclass(frozen=True)
class CollectionBatch:
    source_id: str
    thread_id: str
    expected: Cursor
    successor: Cursor
    responses: tuple[ResponseUsage, ...]
    diagnostics: tuple[str, ...]
    pending: str
    attribution: tuple = ()


def _json(raw: bytes) -> dict:
    try:
        result = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise UsageError("invalid_record") from None


def _path_key(value: object) -> str | None:
    if not isinstance(value, (str, Path)) or not os.path.isabs(value):
        return None
    return os.path.normcase(os.path.normpath(str(value)))


def _header(record: dict, source: SourceInput) -> str:
    payload = record.get("payload")
    if (record.get("type") != "session_meta" or not isinstance(payload, dict)
            or payload.get("id") != source.thread_id
            or not is_session_id(source.thread_id)
            or _path_key(payload.get("cwd")) != _path_key(source.project_root)):
        raise UsageError("source_unreadable")
    provider = label(payload.get("model_provider"))
    if provider is None:
        raise UsageError("source_unreadable")
    return provider


def _projection(record: dict, thread: str, provider: str, models: dict):
    """Return only a closed marker or validated numerical metadata."""
    kind, payload = record.get("type"), record.get("payload")
    if not isinstance(payload, dict):
        if kind in {"token_usage_record", "session_meta", "turn_context"}:
            raise UsageError("invalid_usage")
        return None, None
    if kind == "session_meta":
        if not is_session_id(payload.get("id")):
            raise UsageError("invalid_record")
        return ("session", payload["id"]), None
    if kind == "turn_context":
        turn = payload.get("turn_id")
        if not is_session_id(turn):
            raise UsageError("invalid_record")
        model, effort = label(payload.get("model")), label(payload.get("effort"))
        models[turn] = (model, effort)
        return ("turn", turn, model, effort), None
    if kind == "event_msg" and payload.get("type") == "token_count":
        return ("legacy",), None
    if kind != "token_usage_record":
        return None, None
    owner = payload.get("thread_id")
    if is_session_id(owner) and owner != thread:
        return ("inherited",), None
    model, effort = models.get(payload.get("turn_id"), (None, None))
    response = ResponseUsage(provider, payload.get("response_id"), owner,
                             payload.get("turn_id"), model, effort,
                             counters(payload.get("usage")))
    return asdict(response), response


def read_batch(source: SourceInput, expected: Cursor = Cursor(), *, include_attribution=False,
               attribution_project_id=None) -> CollectionBatch:
    """Read one stable physical segment without modifying it or any state.

The prefix digest covers only accepted metadata and fixed markers plus record
boundaries. It does not hash conversation bytes. A replacement or changed
numerical prefix replays idempotently in a new incarnation.
"""
    try:
        _, before = inspect_physical_file(source.path, root=source.allowed_root)
        file_id = f"{before.device}:{before.inode}"
        with source.path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                    before.device, before.inode, before.size, before.modified_ns):
                raise UsageError("source_changed")
            first = stream.readline(MAX_LINE + 1)
            if len(first) > MAX_LINE or not first.endswith(b"\n"):
                raise UsageError("source_unreadable")
            provider = _header(_json(first), source)
            # Bounded, sanitized prefix scan, including model context before cursor.
            digest = hashlib.sha256()
            digest.update(json.dumps([ADAPTER_VERSION, source.thread_id, provider]).encode())
            if include_attribution:
                from task_governance_tool.usage_turn_adapter import ADAPTER_VERSION as TURN_VERSION
                digest.update(TURN_VERSION.encode())
            models, rows, gaps = {}, [], set()
            attribution = []
            modern_turns, legacy_turns = set(), set()
            context_owner, context_turn = source.thread_id, None
            prior_matches = expected.offset == 0
            boundary, prefix = stream.tell(), digest.hexdigest()
            if boundary == expected.offset:
                prior_matches = prefix == expected.prefix
            replaced = (expected.incarnation > 0 and
                        (expected.file_id != file_id or expected.offset > before.size))
            pending = "none"
            # If replay becomes necessary retain no unbounded prefix response list:
            # restart once after the scan that disproves the previous prefix.
            while stream.tell() < before.size:
                start = stream.tell()
                projected = ()
                raw = stream.readline(min(MAX_LINE + 1, before.size - start))
                if not raw.endswith(b"\n"):
                    if len(raw) > MAX_LINE:
                        # Drain one oversized complete line in bounded chunks.
                        while stream.tell() < before.size and not raw.endswith(b"\n"):
                            raw = stream.readline(min(MAX_LINE + 1, before.size - stream.tell()))
                        if not raw.endswith(b"\n"):
                            gaps.add("partial_tail")
                            pending = "partial_tail"
                            break
                        marker, response = ("record_too_large",), None
                        code = "record_too_large"
                    else:
                        gaps.add("partial_tail")
                        pending = "partial_tail"
                        break
                else:
                    code = None
                    try:
                        if len(raw) > MAX_LINE:
                            raise UsageError("record_too_large")
                        decoded = _json(raw)
                        marker, response = _projection(decoded, source.thread_id, provider, models)
                        if include_attribution and context_owner == source.thread_id:
                            from task_governance_tool.usage_turn_adapter import project_record
                            projected = project_record(decoded, source.thread_id, project_id=attribution_project_id)
                    except (UsageError, TypeError):
                        marker, response, code = ("invalid_record",), None, "invalid_record"
                boundary = stream.tell()
                if isinstance(marker, tuple) and marker[0] == "session":
                    context_owner, context_turn = marker[1], None
                elif isinstance(marker, tuple) and marker[0] == "turn":
                    context_turn = marker[1]
                elif marker == ("legacy",) and context_owner == source.thread_id:
                    legacy_turns.add(context_turn)
                if response is not None:
                    modern_turns.add(response.turn_id)
                    # Usage may arrive after a later turn has begun. It is
                    # observation provenance, not an active-context transition.
                numerical_marker = [boundary, marker]
                if include_attribution:
                    numerical_marker.append([asdict(item) for item in projected])
                digest.update(json.dumps(numerical_marker, sort_keys=True,
                                         separators=(",", ":")).encode())
                prefix = digest.hexdigest()
                if boundary == expected.offset:
                    prior_matches = prefix == expected.prefix
                if expected.offset == 0 or start >= expected.offset:
                    if code:
                        gaps.add(code)
                    if response:
                        rows.append(response)
                        if response.model is None:
                            gaps.add("model_unknown")
                    attribution.extend(projected)
                    if len(rows) + len(attribution) >= MAX_BATCH_RECORDS:
                        pending = "more_records" if stream.tell() < before.size else "none"
                        break
            after = os.fstat(stream.fileno())
            if legacy_turns - modern_turns:
                gaps.add("legacy_usage")
        _, current = inspect_physical_file(source.path, root=source.allowed_root)
        if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) !=
                (before.device, before.inode, before.size, before.modified_ns)
                or current != before):
            raise UsageError("source_changed")
        if expected.incarnation and (replaced or not prior_matches):
            replay = read_batch(source, include_attribution=include_attribution,
                                attribution_project_id=attribution_project_id)
            return CollectionBatch(source.source_id, source.thread_id, expected,
                                   Cursor(expected.incarnation + 1, replay.successor.offset,
                                          replay.successor.prefix, replay.successor.file_id),
                                   replay.responses,
                                   tuple(sorted(set(replay.diagnostics) | {"source_replaced"})),
                                   replay.pending, replay.attribution)
        return CollectionBatch(source.source_id, source.thread_id, expected,
                               Cursor(expected.incarnation or 1, boundary, prefix, file_id),
                               tuple(rows), tuple(sorted(gaps)), pending, tuple(attribution))
    except (OSError, StatePathError):
        raise UsageError("source_unreadable") from None
