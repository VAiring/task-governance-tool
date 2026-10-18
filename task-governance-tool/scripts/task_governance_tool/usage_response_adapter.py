"""Bounded response/call relations from admitted Codex records.

This is a transport projection, not a command interpreter. Only literal wait
handles are read from arguments. Task meaning comes from a whole structured
acknowledgement and must still be checked against committed core identities.
The continuation contains IDs and ordering metadata, never text or arguments.
"""

from dataclasses import dataclass
import json
import re

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.usage_turn_adapter import OperationObservation, project_record
from task_governance_tool.usage_values import RESPONSE_ID, UsageError, label
from task_governance_tool.task_values import validate_task_id, TaskValidationError
from task_governance_tool.usage_batch_adapter import AmbiguousBatch, parse_batch_input, parse_batch_output, is_batch_input


VERSION = "codex-response-relations-v7"
BRACKET_VERSIONS = frozenset({"0.162.0-alpha.17.2", "0.163.0-alpha.10.2"})
MAX_CONTEXT = 128
MAX_TEXT = 2_097_152
TOOLS = frozenset({"functions.exec", "functions.wait", "functions.exec_command",
                   "functions.write_stdin", "exec_command", "write_stdin", "exec", "wait"})
CHECKS = frozenset({"task.context", "task.current", "task.next", "task.show", "task.list"})
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_WARNING = re.compile(r"Failed to find real location of (?:[A-Za-z]:[\\/]|/)[^\r\n]+[\\/]python(?:[0-9.]*)?(?:\.exe)?\r?\n")
_HEADER_LINE = re.compile(r"(?:Chunk ID: [A-Za-z0-9_-]{1,128}|Wall time:? [0-9.]+(?: seconds)?|"
                          r"Process exited with code -?[0-9]+|Original token count: [0-9]+|"
                          r"Script completed|Script running with cell ID [A-Za-z0-9_-]{1,120})\Z")


@dataclass(frozen=True)
class ResponsePosition:
    thread_id: str
    turn_id: str
    provider: str
    response_id: str
    record_order: int

    def __post_init__(self):
        if (not is_session_id(self.thread_id) or not is_session_id(self.turn_id)
                or label(self.provider) != self.provider or not self.provider
                or not isinstance(self.response_id, str) or not RESPONSE_ID.fullmatch(self.response_id)
                or type(self.record_order) is not int or not 0 <= self.record_order < 2**63):
            raise UsageError("response_binding_unknown")


@dataclass(frozen=True)
class ResponseBoundary:
    thread_id: str
    turn_id: str
    project_id: str
    command: str
    call_id: str
    provider: str
    response_id: str | None
    record_order: int
    event_id: str | None = None
    task_id: str | None = None
    generation: int | None = None
    status: str | None = None
    method: str = "unavailable"

    def __post_init__(self):
        if (not is_session_id(self.thread_id) or not is_session_id(self.turn_id)
                or not isinstance(self.project_id, str) or not 0 < len(self.project_id) <= 200
                or self.command not in CHECKS | {"task.add", "task.edit", "task.complete"}
                or not isinstance(self.call_id, str) or not _ID.fullmatch(self.call_id)
                or label(self.provider) != self.provider or not self.provider
                or type(self.record_order) is not int or not 0 <= self.record_order < 2**63
                or self.method not in {"explicit", "single_bracket", "unavailable"}
                or (self.response_id is not None and (not isinstance(self.response_id, str)
                    or not RESPONSE_ID.fullmatch(self.response_id)))
                or (self.response_id is None) != (self.method == "unavailable")):
            raise UsageError("response_binding_unknown")
        if self.command in CHECKS:
            if any(value is not None for value in (self.event_id, self.task_id, self.generation, self.status)):
                raise UsageError("response_binding_unknown")
        else:
            OperationObservation(self.thread_id, self.turn_id, self.project_id, self.task_id,
                                 self.event_id, self.generation, self.status)


def _text_json(value, *, unique_keys=False):
    if len(value.encode("utf-8")) > MAX_TEXT:
        raise UsageError("response_binding_unknown")
    text = value.replace("\r\n", "\n").strip()
    if text.startswith(("Chunk ID:", "Script completed", "Script running with cell ID ")):
        original = text
        head, separator, text = text.partition("\nOutput:\n")
        if not separator and original.endswith("\nOutput:"):
            head, text, separator = original[:-len("\nOutput:")], "", True
        if not separator or any(not _HEADER_LINE.fullmatch(line) for line in head.splitlines()):
            raise UsageError("response_binding_unknown")
    for _ in range(4):
        warning = _WARNING.match(text)
        if warning is None:
            break
        text = text[warning.end():]
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise AmbiguousBatch("duplicate object key")
            result[key] = item
        return result
    decoder = json.JSONDecoder(object_pairs_hook=unique_object) if unique_keys else json.JSONDecoder()
    result = []
    while text.strip():
        value, end = decoder.raw_decode(text.lstrip())
        result.append(value)
        text = text.lstrip()[end:]
    return result


def objects(value, depth=0, *, unique_keys=False):
    """Parse the complete known container before yielding any success body."""
    if depth > 8:
        raise UsageError("response_binding_unknown")
    if isinstance(value, str):
        result = []
        for parsed in _text_json(value, unique_keys=unique_keys):
            result.extend(objects(parsed, depth + 1, unique_keys=unique_keys))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            if not isinstance(item, dict) or item.get("type") not in {"input_text", "text"}:
                result.append({"_transport_unreadable": True})
                continue
            try:
                result.extend(objects(item.get("text"), depth + 1, unique_keys=unique_keys))
            except AmbiguousBatch:
                raise
            except (UsageError, ValueError, TypeError, RecursionError, UnicodeError):
                # A separate text block is a separate closed container, not
                # a reason to discard another independently complete body.
                result.append({"_transport_unreadable": True})
        return result
    if isinstance(value, dict):
        if set(value) == {"i", "result"} and type(value["i"]) is int and 0 <= value["i"] < MAX_CONTEXT:
            settled = value["result"]
            if isinstance(settled, dict) and set(settled) == {"status", "value"} and settled["status"] == "fulfilled":
                return objects(settled["value"], depth + 1, unique_keys=unique_keys)
            return [{"_transport_unreadable": True}]
        body = ("command" in value or value.get("status") in {"review_wait_ended", "saved"}
                or value.get("context_check") == "matched_at_read")
        if body and not unique_keys:
            return [value]
        allowed = {"output", "content", "chunk_id", "wall_time_seconds", "session_id", "exit_code",
                   "original_token_count", "isError", "structuredContent", "_meta"}
        if not body and not set(value) <= allowed:
            return [{"_transport_unreadable": True}]
        # The turn projector also knows legacy transport traversal. Strict
        # results must contain only leaves already checked here, so it cannot
        # re-decode child text with a fresh depth or weaker ambiguity rules.
        result = [{key: item for key, item in value.items() if key not in {"output", "content"}}
                  if unique_keys else value]
        for field in ("output", "content"):
            if field in value and value[field] not in (None, "", []):
                try:
                    result.extend(objects(value[field], depth + 1, unique_keys=unique_keys))
                except AmbiguousBatch:
                    raise
                except (UsageError, ValueError, TypeError, RecursionError, UnicodeError):
                    result.append({"_transport_unreadable": True})
        return result
    raise UsageError("response_binding_unknown")


def _handle_input(name, arguments):
    """Admit direct JSON, or a sole literal exec wrapper; never evaluate JS."""
    name = {"exec": "functions.exec", "wait": "functions.wait"}.get(name, name)
    if name == "functions.exec" and isinstance(arguments, str):
        arguments = re.sub(r"\A\s*// @exec: \{[^\r\n]{0,200}\}\r?\n", "", arguments, count=1)
        match = re.fullmatch(r"\s*text\(await tools\.write_stdin\(\{\s*session_id\s*:\s*([0-9]+)"
                             r"(?:\s*,\s*(?:chars|yield_time_ms|max_output_tokens)\s*:\s*"
                             r"(?:\"(?:[^\"\\]|\\[\"\\nrt])*\"|[0-9]+))*\s*\}\)\);?\s*", arguments)
        if (match and len(match[1]) <= 19 and int(match[1]) < 2**63 and str(int(match[1])) == match[1]
                and int(float(int(match[1]))) == int(match[1])):
            return "session", match[1]
        # An unsupported composition might contain a poll. Its result cannot
        # be relabelled as an operation originating in the wrapper response.
        return ("unresolved", "") if "tools.write_stdin" in arguments else None
    try:
        value = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(value, dict):
            return None
        if name in {"functions.write_stdin", "write_stdin"}:
            handle = value.get("session_id")
            if type(handle) is int and 0 <= handle < 2**63:
                return "session", str(handle)
        if name == "functions.wait":
            handle = value.get("cell_id")
            if isinstance(handle, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,120}", handle):
                return "cell", handle
    except (ValueError, RecursionError):
        pass
    return None


def _check_shape(command, data, project):
    """Essential public read shapes; no prose or candidate-ID inference."""
    def task(value):
        try:
            return (isinstance(value, dict) and validate_task_id(value.get("task_id")) == value["task_id"]
                    and value.get("project_id", project) == project)
        except (TaskValidationError, TypeError, KeyError):
            return False
    def listing(value, *, compact=False, selected=None):
        if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
            return False
        count = value.get("returned_count") if compact else value.get("count")
        if (type(count) is not int or not 0 <= count == len(value["tasks"])
                or type(value.get("limit")) is not int or value["limit"] < count
                or not all(task(row) for row in value["tasks"])):
            return False
        if compact and (type(value.get("total_matching")) is not int or value["total_matching"] < count
                        or type(value.get("truncated")) is not bool):
            return False
        for row in value["tasks"]:
            if "task_ref" in row and (selected is None or row["task_ref"] != "/data/selected/task"
                    or row["task_id"] != selected["task_id"]):
                return False
        return True
    if not isinstance(data, dict):
        return False
    if command == "task.show":
        return task(data.get("task")) and isinstance(data.get("events"), list)
    if command in {"task.list", "task.current", "task.next"}:
        return listing(data, compact=command != "task.list" and "returned_count" in data)
    if command == "task.context":
        selection = data.get("selection")
        selected = data.get("selected")
        selected_task = selected.get("task") if isinstance(selected, dict) else None
        return (set(data) >= {"selection", "current", "next", "selected"}
                and selection in {"none", "current", "next"}
                and ((selection == "none" and selected is None) or
                     (selection != "none" and isinstance(selected, dict) and task(selected.get("task"))))
                and all(value is None or listing(value, compact=True, selected=selected_task)
                        for value in (data["current"], data["next"])))
    return False


def _output_handles(value, parsed, *, depth=0, unique_keys=False):
    if depth > 8:
        return set()
    result = set()
    for item in parsed:
        handle = item.get("session_id")
        if type(handle) is int and 0 <= handle < 2**63:
            result.add(("session", str(handle)))
    texts = [value] if isinstance(value, str) else [item["text"] for item in value
                if isinstance(item, dict) and item.get("type") in {"text", "input_text"}
                and isinstance(item.get("text"), str)] if isinstance(value, list) else []
    text_depth = depth + int(isinstance(value, list))
    if text_depth > 8:
        return result
    for text in texts:
        match = re.match(r"Script running with cell ID ([A-Za-z0-9_-]{1,120})(?:\r?\n|\Z)", text)
        if match:
            try:
                if len(text.encode("utf-8")) > MAX_TEXT:
                    continue
                # A complete sole running header is its own bounded transport.
                # Otherwise validate the whole body with the same remaining
                # depth; a leading header cannot bypass unreadable trailing text.
                if text.rstrip("\r\n") != "Script running with cell ID " + match[1]:
                    if any(item.get("_transport_unreadable") for item in
                           objects(text, depth=text_depth, unique_keys=unique_keys)):
                        continue
                header = text.replace("\r\n", "\n").partition("\nOutput:")[0]
                for line in header.splitlines():
                    declared = re.fullmatch(r"Script running with cell ID ([A-Za-z0-9_-]{1,120})", line)
                    if declared:
                        result.add(("cell", declared[1]))
            except AmbiguousBatch:
                raise
            except (UsageError, ValueError, TypeError, RecursionError, UnicodeError):
                continue
    return result


def _valid_handle(value):
    if not isinstance(value, str):
        return False
    if re.fullmatch(r"cell:[A-Za-z0-9_-]{1,120}", value):
        return True
    if not re.fullmatch(r"session:[0-9]{1,19}", value):
        return False
    number = value.split(":", 1)[1]
    return int(number) < 2**63 and str(int(number)) == number


class ResponseTracker:
    """One physical source incarnation's bounded, serializable continuation."""
    def __init__(self, thread, provider, state=None, *, allow_bracket=True):
        if not is_session_id(thread) or label(provider) != provider or not provider or type(allow_bracket) is not bool:
            raise UsageError("usage_schema_invalid")
        self.thread, self.provider = thread, provider
        self.allow_bracket = allow_bracket
        self.state = self.empty_state() if state is None else state
        self._validate()

    @staticmethod
    def empty_state():
        return {"version": 3, "calls": {}, "batches": {}, "handles": {}, "retired": [], "limited": False, "overflow": None}

    def _validate(self):
        value = self.state
        version = value.get("version") if isinstance(value, dict) else None
        expected = {"version", "calls", "handles", "retired", "limited", "overflow"}
        if version != 1:
            expected.add("batches")
        if (not isinstance(value, dict) or set(value) != expected
                or type(version) is not int or version not in {1, 2, 3}
                or type(value["limited"]) is not bool
                or not isinstance(value["calls"], dict) or not isinstance(value["handles"], dict)
                or not isinstance(value["retired"], list)
                or any(len(value[field]) > MAX_CONTEXT for field in ("calls", "handles", "retired"))):
            raise UsageError("usage_schema_invalid")
        if len(json.dumps(value, separators=(",", ":")).encode("utf-8")) > 262144:
            raise UsageError("usage_schema_invalid")
        for call, frame in value["calls"].items():
            if not isinstance(call, str) or not _ID.fullmatch(call):
                raise UsageError("usage_schema_invalid")
            self._frame(frame, version=version)
        for handle, frame in value["handles"].items():
            if not _valid_handle(handle):
                raise UsageError("usage_schema_invalid")
            self._frame(frame, version=version)
            if version == 3 and not frame["bracket_closed"]:
                raise UsageError("usage_schema_invalid")
        if any(not _valid_handle(key) for key in value["retired"]):
            raise UsageError("usage_schema_invalid")
        if value["overflow"] is not None:
            self._frame(value["overflow"], version=version)
            if not value["limited"] or value["overflow"]["handle"] is not None:
                raise UsageError("usage_schema_invalid")
            if version == 3 and not value["overflow"]["bracket_closed"]:
                raise UsageError("usage_schema_invalid")
        batches = {} if version == 1 else value["batches"]
        if not isinstance(batches, dict) or len(batches) > MAX_CONTEXT:
            raise UsageError("usage_schema_invalid")
        for call, slots in batches.items():
            if (not isinstance(call, str) or not _ID.fullmatch(call) or call in value["calls"]
                    or not isinstance(slots, list) or not 1 <= len(slots) <= MAX_CONTEXT
                    or not any(frame is not None for frame in slots)):
                raise UsageError("usage_schema_invalid")
            for frame in slots:
                if frame is not None:
                    self._frame(frame, version=version)
        if len(value["calls"]) + sum(len(slots) for slots in batches.values()) > MAX_CONTEXT:
            raise UsageError("usage_schema_invalid")
        if version < 3:
            # Old codecs do not prove when their first result arrived. Even a
            # live handle may inherit an already late bracket candidate. Keep
            # identities and explicit origins, but recover bracket evidence
            # only through the ordinary bounded prefix replay.
            def normalize(frame):
                if frame is None:
                    return None
                return {**frame, "slot": frame.get("slot"), "bracket_closed": True,
                        "response": frame["response"] if frame["method"] == "explicit" else None,
                        "method": "explicit" if frame["method"] == "explicit" else "unavailable"}
            self.state = {**value, "version": 3,
                          "batches": {key: [normalize(frame) for frame in slots] for key, slots in batches.items()},
                          "calls": {key: normalize(frame) for key, frame in value["calls"].items()},
                          "handles": {key: normalize(frame) for key, frame in value["handles"].items()},
                          "retired": list(value["retired"]), "overflow": normalize(value["overflow"])}
            self._validate()

    def pending_frames(self):
        """All unresolved metadata, including independently pending batch slots."""
        yield from self.state["calls"].values()
        for slots in self.state["batches"].values():
            yield from (frame for frame in slots if frame is not None)
        yield from self.state["handles"].values()
        if self.state["overflow"] is not None:
            yield self.state["overflow"]

    def _pending_count(self):
        # Resolved placeholders still reserve their index until the batch drains;
        # otherwise many sparse batches could evade the continuation bound.
        return len(self.state["calls"]) + sum(len(slots) for slots in self.state["batches"].values())

    @staticmethod
    def _origin(frame):
        return frame["call"], frame["slot"]

    def _lost_origin(self, frame):
        """One earliest lost origin fences capacity loss without storing bodies."""
        self.state["limited"] = True
        previous = self.state["overflow"]
        if previous is None or frame["order"] < previous["order"]:
            self.state["overflow"] = {**frame, "handle": None, "bracket_closed": True}

    def _invalidate_handle(self, key):
        known = self.state["handles"].get(key)
        if known is not None:
            known["ambiguous"] = True
        for alias in self.pending_frames():
            if alias["handle"] == key:
                alias["ambiguous"] = True

    def interrupt_relation(self):
        """A missing/foreign record breaks pending proofs, not numerical data."""
        for frame in self.pending_frames():
            frame["ambiguous"] = True

    @staticmethod
    def _frame(frame, *, version=3):
        expected = {"call", "turn", "order", "response", "method", "ambiguous", "handle"}
        if version >= 2:
            expected.add("slot")
        if version >= 3:
            expected.add("bracket_closed")
        if (not isinstance(frame, dict) or set(frame) != expected
                or not isinstance(frame["call"], str) or not _ID.fullmatch(frame["call"])
                or not is_session_id(frame["turn"]) or type(frame["order"]) is not int or not 0 <= frame["order"] < 2**63
                or type(frame["ambiguous"]) is not bool or frame["method"] not in {"explicit", "single_bracket", "unavailable"}
                or (frame["response"] is not None and (not isinstance(frame["response"], str)
                    or not RESPONSE_ID.fullmatch(frame["response"])))
                or (frame["response"] is None) != (frame["method"] == "unavailable")
                or (frame["handle"] is not None and not _valid_handle(frame["handle"]))
                or (version >= 2 and frame["slot"] is not None
                    and (type(frame["slot"]) is not int or not 0 <= frame["slot"] < MAX_CONTEXT))
                or (version >= 3 and (type(frame["bracket_closed"]) is not bool
                    or (frame["handle"] is not None and not frame["bracket_closed"])))):
            raise UsageError("usage_schema_invalid")

    def _close_brackets(self, call, *, invalid_turn=False):
        """First matching host output ends the bracket, not the operation."""
        frames = [self.state["calls"][call]] if call in self.state["calls"] else self.state["batches"].get(call, ())
        for frame in frames:
            if frame is not None:
                frame["bracket_closed"] = True
                if invalid_turn:
                    frame["ambiguous"] = True

    def _poll_frame(self, frame, handle):
        if handle:
            key = ":".join(handle)
            origin = self.state["handles"].get(key)
            if origin and key not in self.state["retired"]:
                return {**origin, "handle": key}
            frame["ambiguous"] = True
        return frame

    def consume(self, record, order, response, project):
        result = []
        if response is not None:
            result.append(ResponsePosition(self.thread, response.turn_id, self.provider, response.response_id, order))
            pending_calls = list(self.state["calls"].values())
            pending_calls.extend(frame for slots in self.state["batches"].values() for frame in slots if frame is not None)
            for frame in pending_calls:
                if frame["bracket_closed"] or frame["turn"] != response.turn_id or frame["handle"] is not None:
                    continue
                if frame["method"] == "explicit" or not self.allow_bracket:
                    continue
                if frame["response"] is not None and frame["response"] != response.response_id:
                    frame["ambiguous"] = True
                else:
                    frame["response"], frame["method"] = response.response_id, "single_bracket"
        payload = record.get("payload")
        if record.get("type") != "response_item" or not isinstance(payload, dict):
            return tuple(result)
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        turn = metadata.get("turn_id") if isinstance(metadata, dict) else None
        call = payload.get("call_id")
        kind = payload.get("type")
        if kind in {"function_call_output", "custom_tool_call_output"} and isinstance(call, str) and _ID.fullmatch(call):
            # Closure precedes all body parsing and turn admission. Missing or
            # malformed metadata cannot leave a bracket open for a later reply.
            self._close_brackets(call, invalid_turn=not is_session_id(turn))
        if not is_session_id(turn):
            return tuple(result)
        if kind in {"function_call", "custom_tool_call"} and payload.get("name") in TOOLS:
            if not isinstance(call, str) or not _ID.fullmatch(call):
                return tuple(result)
            if call in self.state["calls"] or call in self.state["batches"]:
                if call in self.state["calls"]:
                    self.state["calls"][call]["ambiguous"] = True
                for frame in self.state["batches"].get(call, ()):
                    if frame is not None:
                        frame["ambiguous"] = True
                return tuple(result)
            direct = metadata.get("response_id")
            explicit = isinstance(direct, str) and RESPONSE_ID.fullmatch(direct)
            frame = {"call": call, "turn": turn, "order": order, "response": direct if explicit else None,
                     "method": "explicit" if explicit else "unavailable", "ambiguous": False, "handle": None,
                     "slot": None, "bracket_closed": bool(explicit)}
            arguments = payload.get("arguments", payload.get("input"))
            batch = parse_batch_input(payload["name"], arguments)
            if batch is not None:
                slots = [self._poll_frame({**frame, "slot": index}, item.handle)
                         for index, item in enumerate(batch)]
                if self._pending_count() + len(slots) > MAX_CONTEXT:
                    for slot in slots:
                        self._lost_origin(slot)
                else:
                    self.state["batches"][call] = slots
                return tuple(result)
            handle = _handle_input(payload["name"], arguments)
            if handle:
                frame = self._poll_frame(frame, handle)
            elif payload["name"] in {"functions.wait", "functions.write_stdin", "write_stdin", "wait"}:
                frame["ambiguous"] = True
            if not handle and is_batch_input(payload["name"], arguments):
                frame["ambiguous"] = True
            if self._pending_count() >= MAX_CONTEXT:
                self._lost_origin(frame)
                return tuple(result)
            self.state["calls"][call] = frame
            return tuple(result)
        if kind not in {"function_call_output", "custom_tool_call_output"}:
            return tuple(result)
        output = payload.get("output")
        if isinstance(call, str) and call in self.state["batches"]:
            slots = self.state["batches"][call]
            values = parse_batch_output(output, len(slots), text_parser=_text_json)
            if values is None:
                return tuple(result)
            selected = []
            try:
                for index, routed in values.items():
                    value = routed.value
                    parsed = self._parsed(value, depth=routed.depth, unique_keys=True)
                    handles = _output_handles(value, parsed, depth=routed.depth, unique_keys=True)
                    # A repeated resolved slot is still part of this container's
                    # ambiguity check; only state updates skip resolved slots.
                    if slots[index] is not None:
                        selected.append((index, slots[index], value, parsed, handles))
            except AmbiguousBatch:
                # No handle registration, observation or slot change is allowed
                # until every admitted fulfilled value passes this preflight.
                return tuple(result)
            # Validate every lifetime before emitting any result from this output.
            # Two fresh slots have the same host call, but different origins.
            for _, frame, _, _, handles in selected:
                self._register_handles(frame, handles)
            live_handles = {":".join(handle) for _, _, _, _, handles in selected for handle in handles}
            for _, frame, _, _, handles in selected:
                if frame["handle"] in live_handles and not handles:
                    # Simultaneous polls cannot establish terminal/running order.
                    # Retain the live lifetime and make its competing proof unknown.
                    self._invalidate_handle(frame["handle"])
            for index, frame, value, parsed, handles in selected:
                found, pending = self._result(frame, value, parsed, handles, turn, project, live_handles=live_handles)
                result.extend(found)
                if not pending:
                    slots[index] = None
            if not any(frame is not None for frame in slots):
                self.state["batches"].pop(call, None)
            return tuple(result)
        frame = self.state["calls"].get(call) if isinstance(call, str) and _ID.fullmatch(call) else None
        # A sole poll still belongs to its original batch slot's lifetime.
        # Validate before removing its pending frame or updating its handles.
        unique_keys = frame is not None and frame["slot"] is not None
        try:
            parsed = self._parsed(output, unique_keys=unique_keys)
            handles = _output_handles(output, parsed, unique_keys=unique_keys) if frame is not None else set()
        except AmbiguousBatch:
            return tuple(result)
        if frame is None:
            # Closed historical results can prove a turn and independent
            # reviewer/receipt relation, but cannot invent a call response.
            for item in parsed:
                actual = {"type": "response_item", "payload": {"type": "function_call_output", "output": item,
                          "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}
                result.extend(project_record(actual, self.thread, project_id=project))
            return tuple(result)
        self.state["calls"].pop(call)
        unproven = parse_batch_output(output, MAX_CONTEXT,
                                     text_parser=lambda value, **_: _text_json(value))
        if unproven is None or unproven.indexed:
            # An indexed result cannot prove which inner call ran from this
            # ordinary frame. This also closes unsupported JS spellings without
            # interpreting their syntax or relabelling a poll as a fresh exec.
            frame["ambiguous"] = True
        self._register_handles(frame, handles)
        found, pending = self._result(frame, output, parsed, handles, turn, project)
        result.extend(found)
        if pending:
            self.state["calls"][call] = frame
        return tuple(result)

    @staticmethod
    def _parsed(output, *, depth=0, unique_keys=False):
        try:
            return objects(output, depth=depth, unique_keys=unique_keys)
        except AmbiguousBatch:
            raise
        except (UsageError, ValueError, TypeError, RecursionError, UnicodeError):
            return []

    def _register_handles(self, frame, handles):
        if len(handles) > 1:
            frame["ambiguous"] = True
        for handle in handles:
            key = ":".join(handle)
            known = self.state["handles"].get(key)
            collision = known is not None and self._origin(known) != self._origin(frame)
            if (key in self.state["retired"] or collision
                    or (known is None and len(self.state["handles"]) >= MAX_CONTEXT) or self.state["limited"]):
                frame["ambiguous"] = True
                if collision:
                    self._invalidate_handle(key)
                    # Fresh batch origins have no input-handle alias yet.
                    for alias in self.pending_frames():
                        if self._origin(alias) == self._origin(known):
                            alias["ambiguous"] = True
                elif known is None:
                    self._lost_origin(frame)
            else:
                self.state["handles"][key] = {**frame, "handle": None}

    def _result(self, frame, output, parsed, handles, turn, project, *, live_handles=frozenset()):
        result = []
        method = frame["method"] if (frame["response"] is not None and not frame["ambiguous"]
                    and (frame["method"] != "single_bracket" or self.allow_bracket)) else "unavailable"
        response_id = frame["response"] if method != "unavailable" else None
        validated_success = False
        for item in parsed:
            actual = {"type": "response_item", "payload": {"type": "function_call_output", "output": item,
                      "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}
            result.extend(observation for observation in project_record(actual, self.thread, project_id=project)
                          if not isinstance(observation, OperationObservation))
            if item.get("ok") is not True or item.get("project_id") != project:
                continue
            command, data = item.get("command"), item.get("data")
            if command in CHECKS and _check_shape(command, data, project):
                validated_success = True
                result.append(ResponseBoundary(self.thread, frame["turn"], project, command, frame["call"],
                                               self.provider, response_id, frame["order"], method=method))
            single = {"type": "response_item", "payload": {"type": "function_call_output", "output": item,
                      "internal_chat_message_metadata_passthrough": {"turn_id": frame["turn"]}}}
            for observation in project_record(single, self.thread, project_id=project):
                if isinstance(observation, OperationObservation):
                    validated_success = True
                    result.append(observation)
                    result.append(ResponseBoundary(self.thread, frame["turn"], project, command, frame["call"],
                        self.provider, response_id, frame["order"], observation.event_id, observation.task_id,
                        observation.generation, observation.status, method))
        # Arrival is not terminal proof. An opaque block can hide a running
        # nested operation even when another independently valid block is read.
        terminal = bool(parsed) and not any(item.get("_transport_unreadable") for item in parsed) and any(
            (type(item.get("exit_code")) is int and "session_id" not in item)
            or (item.get("project_id") == project and item.get("ok") is False
                and isinstance(item.get("data"), dict)
                and item.get("command") in CHECKS | {"task.add", "task.edit", "task.complete"})
            or item.get("status") in {"review_wait_ended", "saved"}
            or item.get("context_check") == "matched_at_read" for item in parsed)
        terminal = terminal or (validated_success and not any(item.get("_transport_unreadable") for item in parsed))
        if frame["handle"] is not None and frame["handle"] not in live_handles and not handles and terminal:
            key = frame["handle"]
            self.state["handles"].pop(key, None)
            if key in self.state["retired"]:
                pass
            elif len(self.state["retired"]) < MAX_CONTEXT:
                self.state["retired"].append(key)
            else:
                self.state["limited"] = True
        return tuple(result), not handles and not terminal
