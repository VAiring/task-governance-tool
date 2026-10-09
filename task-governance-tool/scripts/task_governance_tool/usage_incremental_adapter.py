"""Resumable, metadata-only JSONL slices and deferred prefix verification.

No writer or raw-record retention. A scan may exceed its byte allowance by
one bounded record; oversized records drain in chunks without retaining bytes.
The complete cursor always stays behind an unfinished record. Hash chaining
allows continuation without serializing a hash implementation's private state.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from time import monotonic

from task_governance_tool.state_paths import inspect_physical_file, StatePathError
from task_governance_tool.usage_adapter import MAX_LINE, _header, _json, _projection
from task_governance_tool.usage_turn_adapter import project_record, ADAPTER_VERSION
from task_governance_tool.usage_values import UsageError


SLICE_BYTES = 256 * 1024
SLICE_RECORDS = 512


@dataclass(frozen=True)
class ScanState:
    offset: int = 0
    prefix: str = ""
    provider: str = ""
    owner: str = ""
    turn: str | None = None
    drain: int = 0


@dataclass(frozen=True)
class Scan:
    state: ScanState
    file_id: str
    responses: tuple
    attribution: tuple
    diagnostics: tuple
    pending: str
    models: dict
    modern: frozenset
    legacy: frozenset
    bytes_read: int
    records: int


class Models(dict):
    """Lazy lookup from the admitted numerical snapshot; only changes persist."""
    def __init__(self, lookup):
        super().__init__()
        self.lookup = lookup

    def get(self, key, default=None):
        if key in self:
            return self[key]
        return self.lookup(key) or default


def chain(prefix, value):
    safe = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(prefix.encode("ascii") + safe).hexdigest()


def scan(source, state, models, *, file_id="", goal=None, project_id=None,
         max_bytes=SLICE_BYTES, max_records=SLICE_RECORDS, deadline=float("inf")):
    """Read a stable append/audit slice. Replacement is a replay signal.

    The caller owns independent ingest/audit contexts. Audit has a fixed goal,
    so continuing append cannot keep moving its finish line. Private fields
    influence neither retained values nor digests (record boundaries do).
    """
    try:
        _, before = inspect_physical_file(source.path, root=source.allowed_root)
        identity = f"{before.device}:{before.inode}"
        if file_id and (file_id != identity or before.size < max(state.offset, state.drain, goal or 0)):
            raise UsageError("source_replaced")
        with source.path.open("rb") as stream:
            expected_stat = (before.device, before.inode, before.size, before.modified_ns)
            stat = lambda: tuple(getattr(os.fstat(stream.fileno()), field)
                                 for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns"))
            if stat() != expected_stat:
                raise UsageError("source_changed")
            first = stream.readline(MAX_LINE + 1)
            if len(first) > MAX_LINE or not first.endswith(b"\n"):
                raise UsageError("source_unreadable")
            provider = _header(_json(first), source)
            bytes_read = len(first)
            if state.offset:
                if provider != state.provider or len(first) > state.offset:
                    raise UsageError("source_replaced")
            else:
                state = ScanState(len(first), chain("", ["codex-incremental-v1", ADAPTER_VERSION,
                                                         source.thread_id, provider]), provider, source.thread_id)
            stream.seek(state.drain or state.offset)
            limit = before.size if goal is None else goal
            responses, attribution, gaps = [], [], set()
            modern, legacy = set(), set()
            count = 0
            pending = "none"
            while stream.tell() < limit:
                if count >= max_records or (bytes_read >= max_bytes and count) or monotonic() >= deadline:
                    pending = "more_records"
                    break
                projected = ()
                code = None
                if state.drain:
                    raw = stream.readline(min(65536, limit - stream.tell()))
                else:
                    raw = stream.readline(min(MAX_LINE + 1, limit - stream.tell()))
                bytes_read += len(raw)
                if state.drain or len(raw) > MAX_LINE:
                    if not raw.endswith(b"\n"):
                        state = replace(state, drain=stream.tell())
                        pending = "partial_tail" if stream.tell() == before.size else "more_records"
                        if bytes_read >= max_bytes:
                            break
                        continue
                    marker, response, code = ("record_too_large",), None, "record_too_large"
                elif not raw.endswith(b"\n"):
                    pending = "partial_tail"
                    break
                else:
                    try:
                        decoded = _json(raw)
                        marker, response = _projection(decoded, source.thread_id, provider, models)
                        if state.owner == source.thread_id:
                            projected = project_record(decoded, source.thread_id, project_id=project_id)
                    except (UsageError, TypeError):
                        marker, response, code = ("invalid_record",), None, "invalid_record"
                owner, turn = state.owner, state.turn
                if isinstance(marker, tuple) and marker[0] == "session":
                    owner, turn = marker[1], None
                elif isinstance(marker, tuple) and marker[0] == "turn":
                    turn = marker[1]
                elif marker == ("legacy",) and owner == source.thread_id:
                    legacy.add(turn or "")
                if response is not None:
                    responses.append(response)
                    modern.add(response.turn_id)
                    if response.model is None:
                        gaps.add("model_unknown")
                if code:
                    gaps.add(code)
                attribution.extend(projected)
                boundary = stream.tell()
                state = ScanState(boundary, chain(state.prefix, [boundary, marker,
                                  [asdict(item) for item in projected]]), provider, owner, turn)
                count += 1
            if stat() != expected_stat:
                raise UsageError("source_changed")
        _, after = inspect_physical_file(source.path, root=source.allowed_root)
        if after != before:
            raise UsageError("source_changed")
        return Scan(state, identity, tuple(responses), tuple(attribution), tuple(sorted(gaps)),
                    pending, dict(models), frozenset(modern), frozenset(legacy), bytes_read, count)
    except (OSError, StatePathError):
        raise UsageError("source_unreadable") from None
