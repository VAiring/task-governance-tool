"""Source-only public host adapter; no scheduler, activation, or durable storage.

Caller metadata must come from the current genuine executor request. This module
does not establish that a retained request remains usable after its turn ends.
Child observations are snapshots, not terminal latches. Automation configuration
is read only from the documented exact local automation.toml path; it contains
no next-run instant. Actual host/timezone equivalence remains an integration gate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import re
import stat
import time
import tomllib
from typing import Callable, Mapping, Sequence
from uuid import UUID

from task_governance_tool.review_wait_runtime import review_wait_mcp_relay as relay


MAX_CONFIG_BYTES = 65_536
MAX_REVIEWER_LOOKUP_PAGES = 16
SHORTEN_RULE = "FREQ=MINUTELY;INTERVAL=1"
HOST_BOUNDARY_REASONS = frozenset({
    "host_launch_failed", "host_initialize_failed", "host_response_unavailable",
    "host_cleanup_unknown", "host_tool_error", "host_response_invalid",
}) | frozenset("host_" + reason for reason in relay.RESPONSE_FAILURE_REASONS)
HOST_OPERATIONS = frozenset({"read_thread", "wait_threads", "automation_update", "send_message_to_thread"})
HOST_TERMINATION = frozenset({"not_attempted", "confirmed", "unresolved"})
EXECUTOR_TURN_FAILURES = frozenset({"turn_missing", "turn_invalid", "turn_embedded_only"})
READ_PURPOSES = frozenset({"parent_state", "reviewer_identity", "notification_receipt"})
READ_NEXT_ACTIONS = {"parent_state": "restore_parent_read", "reviewer_identity": "restore_identity_read",
                     "notification_receipt": "retain_known_results"}
_TURN_STATES = {"completed", "failed", "interrupted", "inProgress"}
_THREAD_STATES = {"notLoaded", "idle", "active", "systemError"}
_CALL_ID_KEYS = ("openai/toolCallId", "openai/tool_call_id", "codexCallId",
                 "codex_call_id", "callId", "call_id")
_CONFIG_FIELDS = {
    "version", "id", "kind", "name", "prompt", "status", "rrule",
    "target_thread_id", "created_at", "updated_at", "model", "reasoning_effort",
    "notification_policy",
}


class HostAdapterError(Exception):
    """Only a fixed code crosses this boundary; never include rejected values."""

    def __init__(self, code: str, *, boundary_reason: str | None = None,
                 operation: str | None = None, termination: str | None = None,
                 executor_context: str | None = None):
        self.code = code
        self.boundary_reason = (boundary_reason if type(boundary_reason) is str
                                and boundary_reason in HOST_BOUNDARY_REASONS else None)
        self.operation = operation if type(operation) is str and operation in HOST_OPERATIONS else None
        self.termination = (termination if type(termination) is str
                            and termination in HOST_TERMINATION else None)
        self.read_failure = None
        self.executor_context = (executor_context if code == "executor_context_required"
            and type(executor_context) is str and executor_context in EXECUTOR_TURN_FAILURES else None)
        super().__init__(code)


def host_failure(error) -> dict | None:
    """Closed transient facts; no provider/metadata/exception text escapes."""
    if (type(error) is HostAdapterError and error.operation in HOST_OPERATIONS
            and error.termination in HOST_TERMINATION
            and error.boundary_reason in HOST_BOUNDARY_REASONS):
        return {"operation": error.operation, "reason": error.boundary_reason,
                "termination": error.termination}
    return None


def unknown_host_effect(code: str, error: Exception) -> HostAdapterError:
    failure = host_failure(error)
    return HostAdapterError(code, **({"operation": failure["operation"],
        "boundary_reason": failure["reason"], "termination": failure["termination"]}
        if failure is not None else {}))


def read_failure(error) -> dict | None:
    value = error.read_failure if type(error) is HostAdapterError else None
    return validated_read_failure(value)


def validated_read_failure(value) -> dict | None:
    if (type(value) is dict and set(value) == {"purpose", "retained_pages", "conclusion", "next_action"}
            and type(value["purpose"]) is str and value["purpose"] in READ_PURPOSES
            and type(value["retained_pages"]) is int and 0 <= value["retained_pages"] <= 17
            and value["conclusion"] == "unknown"
            and value["next_action"] == READ_NEXT_ACTIONS[value["purpose"]]):
        return dict(value)
    return None


def annotate_read_failure(error: HostAdapterError, purpose: str, retained_pages: int) -> HostAdapterError:
    error.read_failure = validated_read_failure({"purpose": purpose, "retained_pages": retained_pages,
        "conclusion": "unknown", "next_action": READ_NEXT_ACTIONS[purpose]})
    return error


@dataclass(frozen=True)
class ChildTurn:
    child_id: str
    turn_id: str
    status: str
    thread_status: str


@dataclass(frozen=True)
class ThreadPage:
    """Validated navigation and state only; never raw items or provider bodies."""

    snapshot: ChildTurn
    next_cursor: str | None
    has_more: bool


def thread_page(value, parent_id) -> ThreadPage:
    snapshot = parse_read_thread(value, parent_id)
    if value["page"]["nextCursor"] is not None:
        _text(value["page"]["nextCursor"], 4096, empty=True)
    return ThreadPage(snapshot, value["page"]["nextCursor"], value["page"]["hasMore"])


@dataclass(frozen=True)
class WaitSnapshot:
    turns: tuple[ChildTurn, ...]
    cursors: tuple[tuple[str, str], ...]
    timed_out: bool


@dataclass(frozen=True)
class HeartbeatSnapshot:
    id: str
    kind: str
    parent_thread_id: str
    rule: str
    timezone: str
    timezone_source: str
    status: str
    created_at: int
    updated_at: int
    identity_digest: str
    cleanup_only: bool = False


@dataclass(frozen=True, repr=False)
class _HeartbeatConfig:
    """Private transient payload. Never persist or log this object."""

    snapshot: HeartbeatSnapshot
    name: str
    prompt: str
    notification_policy: str | None


def _fail(code: str = "invalid_host_response") -> None:
    raise HostAdapterError(code)


def _uuid(value: object, code: str = "invalid_host_response") -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            _fail(code)
        return value
    except (ValueError, AttributeError):
        _fail(code)


def reviewer_references(values: object, *, invalid_id_code: str = "invalid_child_id") -> tuple[str, ...]:
    """Admit UUIDs or exact host-returned paths; paths are never filesystem input."""
    if type(values) not in (list, tuple) or not 1 <= len(values) <= 64:
        _fail("invalid_child_set")
    references = []
    for value in values:
        if isinstance(value, str) and value.startswith("/"):
            if len(value) > 1024 or not re.fullmatch(r"/root(?:/[a-z0-9_]+)+", value):
                _fail(invalid_id_code)
        else:
            _uuid(value, invalid_id_code)
        references.append(value)
    return tuple(references)


def _text(value: object, maximum: int = 4096, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > maximum
            or (not empty and not value.strip())):
        _fail()
    return value


def _object(value: object, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        _fail()
    return value


def _timestamp(value: object, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) is not int or value < 0:
        _fail()


def _thread_state(value: object) -> str:
    obj = _object(value, {"type", "activeFlags"}, {"type"})
    if not isinstance(obj["type"], str) or obj["type"] not in _THREAD_STATES:
        _fail()
    if "activeFlags" in obj and (not isinstance(obj["activeFlags"], list)
                                  or any(not isinstance(x, str) for x in obj["activeFlags"])):
        _fail()
    return obj["type"]


def _turn(value: object, child_id: str, thread_status: str, *, items: bool) -> ChildTurn:
    fields = {"id", "status", "error", "startedAt", "completedAt", "durationMs"}
    obj = _object(value, fields | ({"items"} if items else set()), fields)
    turn_id = _uuid(obj["id"])
    if not isinstance(obj["status"], str) or obj["status"] not in _TURN_STATES:
        _fail()
    if obj["error"] is not None and not isinstance(obj["error"], dict):
        _fail()
    for key in ("startedAt", "completedAt", "durationMs"):
        _timestamp(obj[key], nullable=True)
    if items and not isinstance(obj.get("items"), list):
        _fail()
    # Do not retain error bodies, messages, items, or outputs.
    return ChildTurn(child_id, turn_id, obj["status"], thread_status)


def parse_read_thread(value: object, expected_child: str) -> ChildTurn:
    child = _uuid(expected_child, "invalid_child_id")
    obj = _object(value, {"schemaVersion", "thread", "page", "turns"},
                  {"schemaVersion", "thread", "page", "turns"})
    if type(obj["schemaVersion"]) is not int or obj["schemaVersion"] != 1:
        _fail()
    thread = _object(obj["thread"], {"id", "kind", "hostId", "title", "preview", "status",
                                    "cwd", "createdAt", "updatedAt"},
                     {"id", "kind", "hostId", "status"})
    if thread["id"] != child or thread["kind"] != "codex" or thread["hostId"] != "local":
        _fail()
    page = _object(obj["page"], {"order", "limit", "nextCursor", "hasMore"},
                   {"order", "limit", "nextCursor", "hasMore"})
    if (page["order"] != "newest_first" or type(page["limit"]) is not int
            or page["limit"] != 1 or type(page["hasMore"]) is not bool
            or (page["nextCursor"] is not None and not isinstance(page["nextCursor"], str))):
        _fail()
    if not isinstance(obj["turns"], list) or len(obj["turns"]) != 1:
        _fail("child_status_unavailable")
    return _turn(obj["turns"][0], child, _thread_state(thread["status"]), items=True)


def parse_wait_threads(value: object, expected_children: Sequence[str]) -> WaitSnapshot:
    if not isinstance(expected_children, (tuple, list)):
        _fail("invalid_child_set")
    children = tuple(_uuid(x, "invalid_child_id") for x in expected_children)
    if not children or len(children) > 8 or len(set(children)) != len(children):
        _fail("invalid_child_set")
    obj = _object(value, {"timedOut", "wake", "polls", "errors"}, {"timedOut", "wake", "polls"})
    if type(obj["timedOut"]) is not bool or not isinstance(obj["polls"], list):
        _fail()
    if "errors" in obj:
        if not isinstance(obj["errors"], list):
            _fail()
        if obj["errors"]:
            _fail("child_status_unavailable")
    if obj["wake"] is not None:
        wake = _object(obj["wake"], {"reason", "threadId", "hostId", "turnId"}, {"reason"})
        _text(wake["reason"], 100)
        if "threadId" in wake and wake["threadId"] not in children:
            _fail()
        if "hostId" in wake and wake["hostId"] != "local":
            _fail()
        if "turnId" in wake:
            _uuid(wake["turnId"])
            if "threadId" not in wake:
                _fail()
    turns, cursors = [], []
    seen = set()
    fields = {"schemaVersion", "cursor", "cursorReset", "revision", "changed", "thread", "latestTurn",
              "latestAssistantMessageId", "latestAssistantMessage", "latestToolMarkerId", "latestToolMarker"}
    for value in obj["polls"]:
        poll = _object(value, fields, {"schemaVersion", "cursor", "revision", "changed", "thread", "latestTurn"})
        if (type(poll["schemaVersion"]) is not int or poll["schemaVersion"] != 1
                or type(poll["revision"]) is not int or poll["revision"] < 0
                or type(poll["changed"]) is not bool
                or ("cursorReset" in poll and type(poll["cursorReset"]) is not bool)):
            _fail()
        thread = _object(poll["thread"], {"id", "hostId", "status"}, {"id", "hostId", "status"})
        child = _uuid(thread["id"])
        if child not in children or child in seen or thread["hostId"] != "local":
            _fail()
        seen.add(child)
        cursors.append((child, _text(poll["cursor"])))
        if poll["latestTurn"] is None:
            _fail("child_status_unavailable")
        turns.append(_turn(poll["latestTurn"], child, _thread_state(thread["status"]), items=False))
    if seen != set(children):
        _fail("child_status_unavailable")
    if obj["wake"] is not None and "turnId" in obj["wake"]:
        if not any(turn.child_id == obj["wake"]["threadId"] and turn.turn_id == obj["wake"]["turnId"]
                   for turn in turns):
            _fail()
    return WaitSnapshot(tuple(turns), tuple(cursors), obj["timedOut"])


def _physical(path: Path, *, file: bool = False) -> os.stat_result:
    for parent in reversed((path, *path.parents)):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("automation_config_unsafe")
        if parent != path and not stat.S_ISDIR(info.st_mode):
            _fail("automation_config_unsafe")
    if file and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        _fail("automation_config_unsafe")
    return info


def _identity(info: os.stat_result) -> tuple:
    # Windows Python may expose birth time through lstat.st_ctime_ns while
    # fstat.st_ctime_ns reports change time. Birth time is consistent across
    # both APIs; inode and mtime still detect replacement and content changes.
    created_or_changed = getattr(info, "st_birthtime_ns", info.st_ctime_ns)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, created_or_changed


def _automation_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", value):
        _fail("invalid_automation_id")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                                       *(f"LPT{i}" for i in range(10))}:
        _fail("invalid_automation_id")
    return value


def _timezone(name: object, source: object) -> tuple[str, str]:
    if (not isinstance(name, str) or not name.strip() or len(name) > 128
            or name != name.strip() or any(ord(c) < 32 for c in name)
            or source not in {"host_node_intl", "host_os"}):
        _fail("timezone_not_admitted")
    return name, source


def admit_executor_metadata(metadata: object) -> dict:
    """Validate genuine request metadata without choosing an alternate identity.

    This does not authenticate an arbitrary stdio sender. The server owns that
    boundary. Conflicting wrapper aliases must not redirect an admitted parent.
    """
    try:
        if type(metadata) is not dict:
            _fail("executor_context_required")
        encoded = relay._encode(metadata)
        if len(encoded) > 16_384 or relay.strict_json_loads(encoded) != metadata:
            _fail("executor_context_required")
        parent = _uuid(metadata.get("threadId"), "executor_context_required")
        for key in ("openai/threadId", "openai/thread_id", "codexThreadId", "codex_thread_id", "thread_id"):
            if key in metadata and metadata[key] != parent:
                _fail("caller_mismatch")
        if "x-codex-turn-metadata" in metadata:
            embedded = metadata["x-codex-turn-metadata"]
            # The public bundled wrapper accepts either an object or JSON text.
            # Inspect both without rewriting the genuine executor payload or
            # using embedded identity as a fallback for required threadId.
            if type(embedded) is str:
                embedded = relay.strict_json_loads(embedded)
            if type(embedded) is not dict:
                _fail("executor_context_required")
            if "thread_id" in embedded and embedded["thread_id"] != parent:
                _fail("caller_mismatch")
            if "thread" in embedded:
                if type(embedded["thread"]) is not dict:
                    _fail("executor_context_required")
                if "id" in embedded["thread"] and embedded["thread"]["id"] != parent:
                    _fail("caller_mismatch")
        return metadata
    except HostAdapterError:
        raise
    except Exception:
        raise HostAdapterError("executor_context_required") from None


def executor_turn_id(metadata: object) -> str:
    """Read an actual executor turn, without the wrapper's generated fallback.

    All supplied wrapper aliases must agree. This checks an already trusted
    request; it does not authenticate arbitrary JSON or recover stored identity.
    """
    admitted = admit_executor_metadata(metadata)
    def invalid_turn():
        return HostAdapterError("executor_context_required", executor_context="turn_invalid")

    def turn_uuid(value):
        try:
            return _uuid(value, "executor_context_required")
        except HostAdapterError:
            raise invalid_turn() from None

    values = []
    primary = False
    for key in ("turnId", "openai/turnId", "openai/turn_id", "codexTurnId", "codex_turn_id", "turn_id"):
        if key in admitted:
            values.append(turn_uuid(admitted[key]))
            primary = True  # Every listed alias is selected by the public wrapper.
    if "turn" in admitted:
        nested = admitted["turn"]
        if type(nested) is not dict or "id" not in nested:
            raise invalid_turn()
        values.append(turn_uuid(nested["id"]))
        primary = True
    if "x-codex-turn-metadata" in admitted:
        embedded = admitted["x-codex-turn-metadata"]
        if type(embedded) is str:
            embedded = relay.strict_json_loads(embedded)  # already admitted above
        if "turn_id" in embedded:
            values.append(turn_uuid(embedded["turn_id"]))
            primary = True
        if "turn" in embedded:
            nested = embedded["turn"]
            if type(nested) is not dict or "id" not in nested:
                raise invalid_turn()
            # This is corroboration only: the bundled wrapper does not select
            # an embedded turn.id as its actual executor turn.
            values.append(turn_uuid(nested["id"]))
    if not primary or not values:
        raise HostAdapterError("executor_context_required", executor_context=(
            "turn_embedded_only" if values else "turn_missing"))
    if any(value != values[0] for value in values):
        _fail("caller_mismatch")
    return values[0]


def _downstream_metadata(metadata: dict) -> dict:
    """Keep executor context; let the public wrapper correlate each new call.

    The incoming call ID describes the outer MCP operation, which can perform
    several distinct host operations. Forwarding it for all of those makes
    the host deduplicate unrelated calls. Remove only the wrapper's known call
    aliases from a transient copy so its existing UUID fallback runs. This
    grants no retry: an unknown or rejected operation still cannot be replayed.
    """
    result = relay.strict_json_loads(relay._encode(metadata))
    for key in _CALL_ID_KEYS:
        result.pop(key, None)
    if type(result.get("call")) is dict:
        result["call"].pop("id", None)
    return result


def _node_timezone_reader(node: Path) -> Callable[[], str]:
    """Read Intl using the same inherited Node/environment, with bounded I/O."""
    script = ("process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:1,result:{timezone:"
              "Intl.DateTimeFormat().resolvedOptions().timeZone}})+'\\n')")

    def read() -> str:
        child, succeeded, name = None, False, None
        try:
            child = relay._ChildSession((str(node), "-e", script), None, time.monotonic() + 5, 1)
            result = child.result(1)
            if set(result) != {"timezone"}:
                _fail()
            name, _ = _timezone(result["timezone"], "host_node_intl")
            succeeded = True
        except Exception:
            succeeded = False
        finally:
            if child is not None:
                try:
                    succeeded = child.close() and succeeded
                except Exception:
                    succeeded = False
        if not succeeded:
            _fail("timezone_unavailable")
        return name

    return read


def _read_config(codex_home: Path, automation_id: str, parent_id: str,
                 timezone: str, timezone_source: str) -> _HeartbeatConfig:
    """Read one stable physical file; no directory scan or scheduler DB access."""
    try:
        if not codex_home.is_absolute():
            _fail("automation_config_unsafe")
        path = codex_home / "automations" / _automation_id(automation_id) / "automation.toml"
        before = _physical(path, file=True)
        if before.st_size > MAX_CONFIG_BYTES:
            _fail("automation_config_invalid")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _identity(opened) != _identity(before):
                _fail("automation_config_changed")
            raw = stream.read(MAX_CONFIG_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (len(raw) > MAX_CONFIG_BYTES or _identity(after) != _identity(before)
                or _identity(_physical(path, file=True)) != _identity(before)):
            _fail("automation_config_changed")
        obj = tomllib.loads(raw.decode("utf-8-sig"))
        required = {"version", "id", "kind", "name", "prompt", "status", "rrule",
                    "target_thread_id", "created_at", "updated_at"}
        if set(obj) - _CONFIG_FIELDS or not required <= set(obj):
            _fail("automation_config_invalid")
        if (type(obj["version"]) is not int or obj["version"] != 1
                or obj["id"] != automation_id or obj["kind"] != "heartbeat"
                or obj["status"] not in {"ACTIVE", "PAUSED"}
                or obj["target_thread_id"] != parent_id):
            _fail("automation_config_invalid")
        name, prompt, rule = _text(obj["name"], 1000), _text(obj["prompt"], 32_768), _text(obj["rrule"])
        for key in ("created_at", "updated_at"):
            _timestamp(obj[key])
        if obj["updated_at"] < obj["created_at"]:
            _fail("automation_config_invalid")
        for key in ("model", "reasoning_effort"):
            if key in obj and obj[key] not in (None, ""):
                _fail("automation_config_invalid")  # Heartbeats inherit their parent's model.
        policy = obj.get("notification_policy")
        if policy not in (None, "failed_runs_only"):
            _fail("automation_config_invalid")
        immutable = {"id": automation_id, "kind": "heartbeat", "name": name, "prompt": prompt,
                     "target_thread_id": parent_id, "notification_policy": policy}
        digest = hashlib.sha256(relay._encode(immutable)).hexdigest()
        snapshot = HeartbeatSnapshot(automation_id, "heartbeat", parent_id, rule, timezone,
                                     timezone_source, obj["status"], obj["created_at"],
                                     obj["updated_at"], digest)
        return _HeartbeatConfig(snapshot, name, prompt, policy)
    except HostAdapterError:
        raise
    except Exception:
        raise HostAdapterError("automation_config_unavailable") from None


class PublicMcpHost:
    """One admitted parent context, reservation, and actual reviewer set.

    ``command`` injection exists for offline fake peers. Production callers use
    ``from_environment``. This object never captures identity from environment,
    discovery metadata, stored state, or a Hook argument.
    """

    def __init__(self, *, metadata: Mapping, automation_id: str, codex_home: Path,
                 confirmed_timezone: str, timezone_source: str, reviewer_ids: Sequence[str],
                 command: Sequence[str], cwd: Path | None = None, timeout_seconds: float = 20,
                 timezone_reader: Callable[[], str] | None = None):
        try:
            self._metadata = relay.strict_json_loads(relay._encode(admit_executor_metadata(metadata)))
            self._parent_id = _uuid(self._metadata.get("threadId"), "executor_context_required")
            self._reviewers = reviewer_references(reviewer_ids)
            if (not self._reviewers or len(self._reviewers) > 64
                    or len(set(self._reviewers)) != len(self._reviewers)
                    or self._parent_id in self._reviewers):
                _fail("invalid_child_set")
            self._configure_transport(automation_id=automation_id, codex_home=codex_home,
                confirmed_timezone=confirmed_timezone, timezone_source=timezone_source,
                command=command, cwd=cwd, timeout_seconds=timeout_seconds, timezone_reader=timezone_reader)
        except HostAdapterError:
            raise
        except Exception:
            raise HostAdapterError("invalid_host_configuration") from None

    def _configure_transport(self, *, automation_id, codex_home, confirmed_timezone,
                             timezone_source, command, cwd, timeout_seconds, timezone_reader):
        """Shared bounded relay setup; caller admission stays with each host."""
        if (isinstance(command, (str, bytes)) or not command
                or any(not isinstance(x, str) or not x for x in command)
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 60):
            _fail("invalid_host_configuration")
        self._automation_id = _automation_id(automation_id)
        self._codex_home = Path(codex_home)
        self._timezone, self._timezone_source = _timezone(confirmed_timezone, timezone_source)
        if timezone_reader is not None and not callable(timezone_reader):
            _fail("timezone_not_admitted")
        self._timezone_reader = timezone_reader
        self._command, self._cwd = tuple(command), cwd
        self._timeout = timeout_seconds

    @property
    def parent_thread_id(self) -> str:
        return self._parent_id

    @property
    def automation_id(self) -> str:
        return self._automation_id

    @property
    def reviewer_ids(self) -> tuple[str, ...]:
        return self._reviewers

    @classmethod
    def from_environment(cls, *, server_path: Path, **kwargs) -> PublicMcpHost:
        try:
            server = Path(server_path)
            node = Path(os.environ.get("CODEX_MCP_NODE_PATH", ""))
            if (not os.environ.get("CODEX_APP_TOOLS_PIPE_PATH", "").strip()
                    or not node.is_absolute() or not node.is_file()
                    or not server.is_absolute() or server.name != "server.mjs" or not server.is_file()):
                _fail("host_transport_unavailable")
            if "timezone_reader" in kwargs or kwargs.get("timezone_source") != "host_node_intl":
                _fail("timezone_not_admitted")
            return cls(command=[str(node), str(server)], cwd=server.parent,
                       timezone_reader=_node_timezone_reader(node), **kwargs)
        except HostAdapterError:
            raise
        except Exception:
            raise HostAdapterError("host_transport_unavailable") from None

    def _request(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        """One bounded exchange; no result escapes before child cleanup."""
        child, result, succeeded = None, None, False
        boundary_reason = "host_launch_failed"
        termination = "not_attempted"
        try:
            if name not in HOST_OPERATIONS:
                _fail("unsupported_host_operation")
            # Construction may launch before failing; without a returned owned
            # session, do not assert that process/reader cleanup was unattempted.
            termination = "unresolved"
            read_only = name in {"read_thread", "wait_threads"}
            child = relay._ChildSession(self._command, self._cwd,
                                        time.monotonic() + (timeout or self._timeout), 1.0,
                                        **({"response_bytes": relay.MAX_READ_RESPONSE_BYTES,
                                            "stdout_bytes": relay.MAX_READ_DRAIN_BYTES} if read_only else {}))
            boundary_reason = "host_initialize_failed"
            child.send(relay._encode({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": relay.PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "taskgov-review-wait-host", "version": "0.1.0"}}}))
            initialized = child.result(1, max_bytes=relay.MAX_MESSAGE_BYTES)
            if (initialized.get("protocolVersion") not in relay.SUPPORTED_PROTOCOL_VERSIONS
                    or not isinstance(initialized.get("capabilities"), dict)
                    or not isinstance(initialized["capabilities"].get("tools"), dict)
                    or not relay._valid_implementation(initialized.get("serverInfo"))):
                _fail()
            child.send(relay._encode({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            boundary_reason = "host_response_unavailable"
            child.send(self._tool_call_bytes(name, arguments))
            result = child.result(2)
            if (type(result) is not dict or type(result.get("isError")) is not bool
                    or type(result.get("content")) is not list
                    or (name != "send_message_to_thread" and not result["content"])
                    or any(type(item) is not dict or set(item) != {"type", "text"}
                           or item["type"] != "text" or type(item["text"]) is not str
                           for item in result["content"])):
                boundary_reason = "host_response_invalid"
                _fail()
            if result["isError"]:
                boundary_reason = "host_tool_error"
            succeeded = True
        except Exception as error:
            succeeded = False
            if (type(error) is relay.RelayError
                    and type(error.response_reason) is str
                    and error.response_reason in relay.RESPONSE_FAILURE_REASONS
                    and (boundary_reason == "host_response_unavailable"
                         or boundary_reason == "host_initialize_failed" and error.response_reason in {
                             "response_frame_limit", "response_drain_limit", "response_queue_limit"})):
                boundary_reason = "host_" + error.response_reason
        finally:
            if child is not None:
                termination = "unresolved"
                try:
                    closed = child.close()
                    late_reason = getattr(child, "close_response_reason", None)
                    if closed is True or (late_reason in relay.RESPONSE_FAILURE_REASONS
                                          and child.termination_confirmed is True):
                        termination = "confirmed"
                    if closed is not True:
                        # Preserve the first communication failure even when
                        # termination also fails. Late replies still reject an
                        # otherwise successful exchange, separately from exit.
                        if succeeded and boundary_reason == "host_response_unavailable":
                            boundary_reason = ("host_" + late_reason
                                               if late_reason in relay.RESPONSE_FAILURE_REASONS
                                               else "host_cleanup_unknown")
                        succeeded = False
                except Exception:
                    if succeeded and boundary_reason == "host_response_unavailable":
                        boundary_reason = "host_cleanup_unknown"
                    succeeded = False
        if not succeeded:
            raise HostAdapterError("host_call_failed", boundary_reason=boundary_reason,
                                   operation=name, termination=termination) from None
        return result

    def _tool_call_bytes(self, name: str, arguments: dict) -> bytes:
        """The actual relay frame, also usable for effect-free size admission."""
        return relay._encode({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": name, "arguments": arguments, "_meta": _downstream_metadata(self._metadata)}})

    def _call(self, name: str, arguments: dict, *, timeout: float | None = None) -> tuple[str, ...]:
        try:
            if name not in {"read_thread", "wait_threads", "automation_update"}:
                _fail("unsupported_host_operation")
            result = self._request(name, arguments, timeout=timeout)
            if not isinstance(result, dict):
                raise HostAdapterError("host_call_failed", boundary_reason="host_response_invalid",
                                       operation=name, termination="confirmed")
            content = result.get("content")
            if (type(result.get("isError")) is not bool or not isinstance(content, list) or not content
                    or any(not isinstance(x, dict) or set(x) != {"type", "text"}
                           or x["type"] != "text" or not isinstance(x["text"], str) for x in content)):
                raise HostAdapterError("host_call_failed", boundary_reason="host_response_invalid",
                                       operation=name, termination="confirmed")
            if result["isError"]:
                # This is a tool-level failure, not proof of a permission denial.
                raise HostAdapterError("host_call_failed", boundary_reason="host_tool_error",
                                       operation=name, termination="confirmed")
            return tuple(x["text"] for x in content)
        except HostAdapterError as error:
            if error.code == "host_call_failed":
                raise
            raise HostAdapterError("host_call_failed", boundary_reason=error.boundary_reason,
                                   operation=error.operation, termination=error.termination) from None
        except Exception:
            raise HostAdapterError("host_call_failed", boundary_reason="host_response_invalid",
                                   operation=name, termination="confirmed") from None

    def _json_call(self, name: str, arguments: dict, *, timeout: float | None = None) -> object:
        deadline = time.monotonic() + (timeout or self._timeout)
        texts = self._call(name, arguments, timeout=timeout)
        try:
            if len(texts) != 1:
                _fail()
            value = relay.strict_json_loads(texts[0], max_bytes=relay.MAX_READ_RESPONSE_BYTES
                if name in {"read_thread", "wait_threads"} else relay.MAX_MESSAGE_BYTES)
        except Exception as error:
            reason = ("host_" + error.response_reason if type(error) is relay.RelayError
                      and error.response_reason in relay.RESPONSE_FAILURE_REASONS else "host_response_json_invalid")
            raise HostAdapterError("invalid_host_response", boundary_reason=reason,
                                   operation=name, termination="confirmed") from None
        if time.monotonic() >= deadline:
            raise HostAdapterError("host_call_failed", boundary_reason="host_response_timeout",
                                   operation=name, termination="confirmed")
        return value

    def _validated_read_thread(self, arguments, validate, *, timeout=None):
        """Retain settled exchange facts when its admitted JSON is rejected.

        Transport failures bypass this semantic boundary unchanged. A returned
        _json_call already requires confirmed helper/reader/pipe cleanup; merely
        invoking it, or an earlier successful page, supplies no such proof.
        """
        value = self._json_call("read_thread", arguments,
                                **({"timeout": timeout} if timeout is not None else {}))
        try:
            return validate(value)
        except HostAdapterError as error:
            if host_failure(error) is not None:
                raise
            raise HostAdapterError(error.code, boundary_reason="host_response_invalid",
                                   operation="read_thread", termination="confirmed") from None

    def resolve_reviewer_ids(self) -> tuple[str, ...]:
        """Resolve only this parent's structured dispatches, never message text.

        The newest parent turn dispatching a path owns its current meaning.
        Conflicting mappings within that turn are ambiguous, including a reused
        name in the same turn. Older turns cannot overwrite a newer dispatch.
        Resolution is transient; only UUID/actual-turn pairs reach the store.
        """
        unresolved = {value for value in self._reviewers if value.startswith("/")}
        if not unresolved:
            return self._reviewers
        self._validated_dispatch_pages = []
        try:
            return self._resolve_handles(unresolved)
        except HostAdapterError as error:
            raise annotate_read_failure(error, "reviewer_identity", len(self._validated_dispatch_pages))

    def _read_dispatch_page(self, paths, cursor, remaining):
        """Return validated selected dispatches, not the public history payload."""
        arguments = {"threadId": self._parent_id, "hostId": "local", "turnLimit": 1,
                     "includeOutputs": False, "maxOutputCharsPerItem": 1}
        if cursor is not None:
            arguments["cursor"] = cursor
        def validate(value):
            page = thread_page(value, self._parent_id)
            matches = {reference: set() for reference in paths}
            for item in value["turns"][0]["items"]:
                if (not isinstance(item, dict) or item.get("type") != "subAgentActivity"
                        or item.get("kind") not in ("started", "interacted")):
                    continue
                path = item.get("agentPath")
                if not isinstance(path, str) or path not in paths:
                    continue
                _object(item, {"type", "id", "kind", "agentPath", "agentThreadId"},
                        {"type", "id", "kind", "agentPath", "agentThreadId"})
                _text(item["id"], 256)
                matches[path].add(_uuid(item["agentThreadId"]))
                if len(matches[path]) > 1:
                    _fail("reviewer_identity_ambiguous")
            return page, tuple((path, tuple(sorted(ids))) for path, ids in matches.items())
        result = self._validated_read_thread(arguments, validate, timeout=remaining)
        self._validated_dispatch_pages.append(result)
        return result

    def _resolve_handles(self, unresolved):
        original_turn = executor_turn_id(self._metadata)
        resolved, seen_turns, seen_cursors = {}, set(), set()
        current_paths = set()
        cursor = None
        deadline = time.monotonic() + self._timeout

        def reject(code):
            # Called only for semantic guards on a fully returned fresh page,
            # never for an expired budget or an unissued next-page read.
            raise HostAdapterError(code, boundary_reason="host_response_invalid",
                                   operation="read_thread", termination="confirmed")

        def read(paths, cursor=None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("reviewer_identity_unavailable")
            page, matches = self._read_dispatch_page(paths, cursor, remaining)
            if time.monotonic() >= deadline:
                _fail("reviewer_identity_unavailable")
            return page, dict((path, set(ids)) for path, ids in matches)

        for page_number in range(MAX_REVIEWER_LOOKUP_PAGES):
            page, matches = read(unresolved, cursor)
            parent = page.snapshot
            if page_number == 0 and (parent.turn_id != original_turn
                    or parent.status != "inProgress" or parent.thread_status != "active"):
                reject("reviewer_identity_unavailable")
            if parent.turn_id in seen_turns:
                reject("reviewer_identity_unavailable")
            seen_turns.add(parent.turn_id)
            for path, identities in matches.items():
                if len(identities) > 1:
                    reject("reviewer_identity_ambiguous")
                if identities:
                    resolved[path] = identities.pop()
                    if page_number == 0:
                        current_paths.add(path)
                    unresolved.remove(path)
            if not unresolved:
                break
            cursor = page.next_cursor
            if (not page.has_more or not isinstance(cursor, str) or not cursor
                    or len(cursor) > 4096 or cursor in seen_cursors):
                _fail("reviewer_identity_unavailable")
            seen_cursors.add(cursor)
        if unresolved:
            _fail("reviewer_identity_unavailable")
        identities = tuple(resolved.get(value, value) for value in self._reviewers)
        if self._parent_id in identities or len(set(identities)) != len(identities):
            reject("invalid_child_set")
        latest, current_ids_by_path = read(resolved)
        current = latest.snapshot
        if (current.turn_id != original_turn or current.status != "inProgress"
                or current.thread_status != "active"):
            reject("reviewer_identity_unavailable")
        for path, current_ids in current_ids_by_path.items():
            if path in current_paths and not current_ids:
                reject("reviewer_identity_unavailable")
            if current_ids and current_ids != {resolved[path]}:
                reject("reviewer_identity_ambiguous")
        self._reviewers = identities
        return identities

    def read_child(self, child_id: str) -> ChildTurn:
        _uuid(child_id, "invalid_child_id")
        if child_id not in self._reviewers:
            _fail("unadmitted_child")
        return self._read_status(child_id)

    def read_parent(self) -> ChildTurn:
        """Read the admitted parent directly; the host rejects self-wait."""
        try:
            return self._validated_read_thread({"threadId": self._parent_id, "hostId": "local",
                "turnLimit": 1, "includeOutputs": False, "maxOutputCharsPerItem": 1},
                lambda value: parse_read_thread(value, self._parent_id))
        except HostAdapterError as error:
            raise annotate_read_failure(error, "parent_state", 0)

    def _read_status(self, thread_id: str) -> ChildTurn:
        # The host cancels wait_threads when its calling parent turn ends.
        # Read the admitted reviewer directly, without history traversal or a
        # stale terminal latch, at the same existing observation points.
        value = self._json_call("read_thread", {"threadId": thread_id, "hostId": "local",
                                               "turnLimit": 1, "includeOutputs": False,
                                               "maxOutputCharsPerItem": 1})
        try:
            return parse_read_thread(value, thread_id)
        except HostAdapterError as error:
            raise HostAdapterError(error.code, boundary_reason="host_response_invalid",
                                   operation="read_thread", termination="confirmed") from None

    def send_direct_probe(self, probe_id: str) -> str:
        """Send one fixed same-parent experiment, never retry or assert receipt.

        The experiment owner admits explicit permission, the stopped timer and
        terminal parent/reviewers, and persists its one-shot intent. This method
        supplies only a bounded transport result, not parent-resumption proof.
        """
        probe = _uuid(probe_id, "invalid_probe_id")
        executor_turn_id(self._metadata)
        prompt = self._direct_prompt(probe)
        try:
            result = self._request("send_message_to_thread", {"threadId": self._parent_id,
                "hostId": "local", "prompt": prompt})
            # The unchanged bundled wrapper emits exactly content/isError.
            # Never interpret or return provider text as a delivery receipt.
            if (type(result) is not dict or set(result) != {"content", "isError"}
                    or type(result["isError"]) is not bool or type(result["content"]) is not list
                    or any(type(item) is not dict or set(item) != {"type", "text"}
                           or item["type"] != "text" or type(item["text"]) is not str
                           for item in result["content"])):
                return "unknown"
            return "rejected" if result["isError"] else "accepted"
        except Exception:
            return "unknown"

    def _direct_prompt(self, probe: str) -> str:
        return (
            "これは同じ親チャットへの直接復帰を確認する実験通知です。"
            f"検証 ID は {probe} です。"
            f'review_wait_direct_ack(probe_id="{probe}") を呼び、受信を確認してください。'
            "確認結果を報告する以外に、通知・予約・再送を起動しないでください。"
        )

    def wait_children(self, child_ids: Sequence[str], *, timeout_ms: int = 0,
                      cursors: Mapping[str, str] | None = None) -> WaitSnapshot:
        if (not isinstance(child_ids, (list, tuple)) or not child_ids or len(child_ids) > 8
                or any(not isinstance(x, str) for x in child_ids)
                or len(set(child_ids)) != len(child_ids)
                or any(x not in self._reviewers for x in child_ids)
                or type(timeout_ms) is not int or not 0 <= timeout_ms <= 30_000):
            _fail("invalid_child_set")
        if cursors is not None and (not isinstance(cursors, dict) or set(cursors) - set(child_ids)):
            _fail("invalid_cursor")
        targets = []
        for child in child_ids:
            target = {"threadId": child, "hostId": "local"}
            if cursors and child in cursors:
                target["afterCursor"] = _text(cursors[child])
            targets.append(target)
        value = self._json_call("wait_threads", {"targets": targets, "timeoutMs": timeout_ms},
                                timeout=max(self._timeout, timeout_ms / 1000 + 5))
        return parse_wait_threads(value, child_ids)

    def _view_config(self, *, for_pause: bool = False) -> _HeartbeatConfig:
        # Logging uses only the fixed outcome of these existing reads. It never
        # changes a public error, performs another read or admits an operation.
        from . import worker_diagnostics as diagnostics
        from .worker_diagnostics import Stage, Outcome
        try:
            return self._view_config_observed(for_pause=for_pause)
        except Exception as error:
            diagnostics.event(Stage.TIMER_CONFIG, Outcome.FAILED,
                reason=error.code if type(error) is HostAdapterError else "automation_config_unavailable",
                error=error)
            raise

    def _view_config_observed(self, *, for_pause: bool = False) -> _HeartbeatConfig:
        if type(for_pause) is not bool:
            _fail("invalid_host_configuration")
        self._call("automation_update", {"mode": "view", "id": self._automation_id})
        if not for_pause and self._timezone_reader is not None:
            try:
                actual, _ = _timezone(self._timezone_reader(), "host_node_intl")
            except Exception:
                raise HostAdapterError("timezone_unavailable") from None
            if actual != self._timezone:
                _fail("timezone_changed")
        current = _read_config(self._codex_home, self._automation_id, self._parent_id,
                               self._timezone, self._timezone_source)
        # Cleanup retains the configured zone only to address the original
        # controller identity. It makes no assertion about the current zone and
        # can never supply permission for an ACTIVE schedule mutation.
        return replace(current, snapshot=replace(current.snapshot, cleanup_only=for_pause))

    def view_heartbeat(self, *, for_pause: bool = False) -> HeartbeatSnapshot:
        return self._view_config(for_pause=for_pause).snapshot

    def delete_heartbeat(self, before: HeartbeatSnapshot, *, cleanup: bool = False) -> None:
        """One admitted public deletion; receipt and exact-path absence required."""
        if (type(before) is not HeartbeatSnapshot or before.id != self._automation_id
                or type(cleanup) is not bool
                or (before.status != "PAUSED" if cleanup else before.status != "ACTIVE")
                or (not cleanup and before.cleanup_only)):
            _fail("unadmitted_heartbeat")
        current = self._view_config(for_pause=before.cleanup_only if cleanup else False)
        if current.snapshot != before:
            _fail("heartbeat_changed")
        try:
            texts = self._call("automation_update", {"mode": "delete", "id": before.id})
            expected = {"automationId": before.id, "mode": "delete", "deleteStatus": "deleted",
                        "snapshot": {"kind": "heartbeat", "name": current.name, "rrule": before.rule}}
            if len(texts) != 2 or relay.strict_json_loads(texts[1]) != expected:
                _fail()
            root = self._codex_home / "automations"
            if not stat.S_ISDIR(_physical(root).st_mode):
                _fail()
            directory = root / self._automation_id
            try:
                info = _physical(directory)
            except FileNotFoundError:
                # The admitted automation directory itself may be removed.
                # Revalidate the enclosing root, so losing it is not success.
                if not stat.S_ISDIR(_physical(root).st_mode):
                    _fail()
                return
            if not stat.S_ISDIR(info.st_mode):
                _fail()
            try:
                _physical(directory / "automation.toml", file=True)
            except FileNotFoundError:
                if not stat.S_ISDIR(_physical(directory).st_mode):
                    _fail()
                return
            _fail()  # A still-present config never confirms deletion.
        except Exception as error:
            raise unknown_host_effect("heartbeat_delete_unknown", error) from None

    def update_heartbeat(self, before: HeartbeatSnapshot, action: str, *, rrule: str | None = None) -> HeartbeatSnapshot:
        if not isinstance(before, HeartbeatSnapshot) or before.id != self._automation_id:
            _fail("unadmitted_heartbeat")
        if type(before.cleanup_only) is not bool or (before.cleanup_only and action != "pause"):
            _fail("unsupported_heartbeat_update")
        if action == "shorten" and before.status == "ACTIVE" and rrule is None:
            status, rule = "ACTIVE", SHORTEN_RULE
        elif action == "pause" and rrule is None:
            status, rule = "PAUSED", before.rule
        elif action == "arm" and before.status == "PAUSED" and isinstance(rrule, str):
            match = re.fullmatch(r"FREQ=DAILY;BYHOUR=(\d{1,2});BYMINUTE=(\d{1,2});BYSECOND=(\d{1,2});COUNT=1", rrule)
            if not match or any(int(x) > bound for x, bound in zip(match.groups(), (23, 59, 59))):
                _fail("invalid_arm_rule")
            status, rule = "ACTIVE", rrule
        else:
            _fail("unsupported_heartbeat_update")
        current = self._view_config(for_pause=before.cleanup_only)
        if current.snapshot != before:
            _fail("heartbeat_changed")
        arguments = {"mode": "update", "id": before.id, "kind": "heartbeat",
                     "name": current.name, "prompt": current.prompt, "targetThreadId": before.parent_thread_id,
                     "rrule": rule, "status": status}
        if current.notification_policy is not None:
            arguments["notificationPolicy"] = current.notification_policy
        try:
            # A transport/cleanup failure after dispatch cannot prove that the
            # host rejected the update. Keep the outcome explicitly unknown.
            texts = self._call("automation_update", arguments)
            if len(texts) != 2:
                _fail()
            receipt = relay.strict_json_loads(texts[1])
            if receipt != {"automationId": before.id, "mode": "update", "status": status}:
                _fail()
            after = self._view_config(for_pause=before.cleanup_only).snapshot
            if (after.id != before.id or after.identity_digest != before.identity_digest
                    or after.parent_thread_id != before.parent_thread_id or after.status != status
                    or after.rule != rule or after.timezone != before.timezone
                    or after.cleanup_only != before.cleanup_only
                    or after.created_at != before.created_at or after.updated_at < before.updated_at):
                _fail()
            return after
        except Exception as error:
            raise unknown_host_effect("heartbeat_update_unknown", error) from None
