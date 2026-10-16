"""Observed public answer adapter; no transcripts, latest substitution or sends.

Only the known public Codex own-turn enumeration is admitted. Initial creation
context is supplied by the dispatching flow and cannot be reconstructed after
restart from a one-turn page. Saved bindings need no new initial selection.
"""

import json
import re
from uuid import UUID

from .review_answer import AnswerError
from .review_answer_collection import source
from .review_wait_runtime.review_wait_mcp_relay import MAX_READ_RESPONSE_BYTES


_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}\Z")
_TERMINALS = {"inProgress": "in_progress", "completed": "completed", "failed": "failed", "interrupted": "interrupted"}


def _fail(reason="incomplete", classification="collection_unavailable"):
    raise AnswerError(classification, reason=reason)


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        _fail("identity", "binding_mismatch")
    return value


def _host_id(value):
    if type(value) is not str or _HOST_ID.fullmatch(value) is None:
        _fail("identity", "binding_mismatch")
    return value


def _content_complete(value):
    # Recognize explicit public indications only. We make no claim to detect
    # undisclosed provider omissions or infer completeness from answer syntax.
    if type(value) is not dict:
        _fail()
    for key in ("truncated", "isTruncated", "textTruncated", "contentTruncated", "missingContent", "hasMoreContent"):
        if key in value and value[key] is not False:
            _fail()
    if "complete" in value and value["complete"] is not True:
        _fail()


def _page(value, expected_thread, limit):
    if (type(value) is not dict or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1
            or not {"thread", "page", "turns"} <= set(value)):
        _fail("shape")
    _content_complete(value)
    thread, page, turns = value["thread"], value["page"], value["turns"]
    if (type(thread) is not dict or thread.get("id") != expected_thread
            or thread.get("kind") != "codex" or thread.get("hostId") != "local"):
        _fail("identity", "binding_mismatch")
    _content_complete(thread)
    if (type(page) is not dict or page.get("order") != "newest_first" or type(page.get("limit")) is not int
            or page["limit"] != limit or type(page.get("hasMore")) is not bool or "nextCursor" not in page
            or page["nextCursor"] is not None and (type(page["nextCursor"]) is not str or not page["nextCursor"])
            or type(turns) is not list or not 1 <= len(turns) <= limit):
        _fail("shape")
    if page["hasMore"] != (page["nextCursor"] is not None):
        _fail("shape")
    seen = set()
    for turn in turns:
        if (type(turn) is not dict or not {"id", "status", "items", "error", "startedAt", "completedAt", "durationMs"} <= set(turn)
                or type(turn["status"]) is not str or turn["status"] not in _TERMINALS or type(turn["items"]) is not list):
            _fail("shape")
        identifier = _uuid(turn["id"])
        if identifier in seen:
            _fail("conflict", "binding_mismatch")
        seen.add(identifier)
        if turn["error"] is not None and type(turn["error"]) is not dict:
            _fail("shape")
        for key in ("startedAt", "completedAt", "durationMs"):
            if turn[key] is not None and (type(turn[key]) is not int or turn[key] < 0):
                _fail("shape")
    return page, turns


class PublicAnswerHost:
    def __init__(self, host, parent_session_id, parent_turn_id=None):
        self.host = host
        self.parent_session_id = _uuid(parent_session_id)
        self.parent_turn_id = None if parent_turn_id is None else _uuid(parent_turn_id)

    def _read(self, thread_id, limit):
        arguments = dict(threadId=_uuid(thread_id), hostId="local", turnLimit=limit,
                         includeOutputs=False, maxOutputCharsPerItem=256)
        try:
            value = self.host._json_call("read_thread", arguments)
            # The relay independently enforces the exact outer read bound.
            # This detached parsed shape also stays within that same allowance.
            if len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_READ_RESPONSE_BYTES:
                _fail("bound")
            return value
        except AnswerError:
            raise
        except Exception:
            _fail("transport_unavailable")

    def bind_dispatch(self, dispatch_id, reviewer_session_id, *, creation_context=False, known_conflict=False):
        child = _uuid(reviewer_session_id)
        _host_id(dispatch_id)
        if (self.parent_turn_id is None or creation_context is not True
                or known_conflict is not False or child == self.parent_session_id):
            _fail("identity", "binding_mismatch")
        _page_info, turns = _page(self._read(self.parent_session_id, 1), self.parent_session_id, 1)
        if turns[0]["id"] != self.parent_turn_id:
            _fail("identity", "binding_mismatch")
        _content_complete(turns[0])
        if turns[0]["error"] is not None:
            _fail("incomplete")
        activities = [item for item in turns[0]["items"] if type(item) is dict and item.get("type") == "subAgentActivity"
                      and (item.get("agentThreadId") == child or item.get("id") == dispatch_id)]
        if (len(activities) != 1 or activities[0].get("kind") != "started"
                or activities[0].get("id") != dispatch_id or activities[0].get("agentThreadId") != child):
            _fail("conflict", "binding_mismatch")
        _content_complete(activities[0])
        child_page, child_turns = _page(self._read(child, 1), child, 1)
        if child_page["hasMore"] is not False or child_page["nextCursor"] is not None or len(child_turns) != 1:
            _fail()
        _content_complete(child_turns[0])
        if child_turns[0]["error"] is not None and child_turns[0]["status"] not in ("failed", "interrupted"):
            _fail("incomplete")
        return dict(parent_session_id=self.parent_session_id, reviewer_session_id=child,
                    initial_turn_id=child_turns[0]["id"], source=source("dispatch_binding"))

    def bind_reviewers(self, reviewer_ids, *, creation_context=False, known_conflict=False):
        """Resolve exact returned children from this dispatch turn only."""
        if (self.parent_turn_id is None or type(reviewer_ids) not in (tuple, list) or not 1 <= len(reviewer_ids) <= 8
                or creation_context is not True or known_conflict is not False):
            _fail("identity", "binding_mismatch")
        children = tuple(_uuid(child) for child in reviewer_ids)
        if len(set(children)) != len(children) or self.parent_session_id in children:
            _fail("identity", "binding_mismatch")
        _page_info, turns = _page(self._read(self.parent_session_id, 1), self.parent_session_id, 1)
        parent = turns[0]
        _content_complete(parent)
        if parent["id"] != self.parent_turn_id or parent["error"] is not None:
            _fail("identity", "binding_mismatch")
        dispatches = []
        for child in children:
            matches = [item for item in parent["items"] if type(item) is dict
                       and item.get("type") == "subAgentActivity" and item.get("agentThreadId") == child]
            if len(matches) != 1 or matches[0].get("kind") != "started":
                _fail("conflict", "binding_mismatch")
            _content_complete(matches[0])
            dispatch = _host_id(matches[0].get("id"))
            if sum(type(item) is dict and item.get("type") == "subAgentActivity" and item.get("id") == dispatch
                   for item in parent["items"]) != 1:
                _fail("conflict", "binding_mismatch")
            dispatches.append(dispatch)
        if len(set(dispatches)) != len(dispatches):
            _fail("conflict", "binding_mismatch")
        results = []
        for child, dispatch in zip(children, dispatches):
            page_info, child_turns = _page(self._read(child, 1), child, 1)
            if page_info["hasMore"] is not False or page_info["nextCursor"] is not None:
                _fail("incomplete")
            _content_complete(child_turns[0])
            if child_turns[0]["error"] is not None and child_turns[0]["status"] not in ("failed", "interrupted"):
                _fail("incomplete")
            results.append(dict(dispatch_id=dispatch, parent_session_id=self.parent_session_id,
                reviewer_session_id=child, initial_turn_id=child_turns[0]["id"], source=source("dispatch_binding")))
        return tuple(results)

    def _bound_turn(self, reviewer_session_id, turn_id):
        child, turn_id = _uuid(reviewer_session_id), _uuid(turn_id)
        _page_info, turns = _page(self._read(child, 2), child, 2)
        matches = [turn for turn in turns if turn["id"] == turn_id]
        if len(matches) != 1:
            _fail("missing")
        _content_complete(matches[0])
        if matches[0]["error"] is not None and matches[0]["status"] not in ("failed", "interrupted"):
            _fail("incomplete")
        return matches[0]

    def observe_answer(self, *, basis_digest, slot, attempt, reviewer_session_id, turn_id, expected_message_id):
        if expected_message_id is not None:
            _host_id(expected_message_id)
        turn = self._bound_turn(reviewer_session_id, turn_id)
        finals = [item for item in turn["items"] if type(item) is dict and item.get("type") == "agentMessage"
                  and item.get("phase") == "final_answer"]
        if len(finals) != 1:
            _fail("missing" if not finals else "conflict")
        item = finals[0]
        _content_complete(item)
        identifier = _host_id(item.get("id"))
        if expected_message_id is not None and identifier != expected_message_id:
            _fail("identity", "binding_mismatch")
        if type(item.get("text")) is not str:
            _fail("shape")
        try:
            raw = item["text"].encode("utf-8")
        except UnicodeError:
            _fail("unicode")
        if not raw or len(raw) > 262144:
            _fail("bound", "capacity_exceeded")
        terminal = _TERMINALS[turn["status"]]
        return dict(source_identity=dict(reviewer_session_id=reviewer_session_id, turn_id=turn_id, message_id=identifier),
            answer_bytes=raw, complete=True, terminal=terminal, answer_source=source("answer_observation"),
            terminal_source=source("terminal_observation"))

    def observe_terminal(self, *, basis_digest, slot, attempt, reviewer_session_id, turn_id):
        turn = self._bound_turn(reviewer_session_id, turn_id)
        return dict(reviewer_session_id=reviewer_session_id, turn_id=turn_id,
                    terminal=_TERMINALS[turn["status"]], terminal_source=source("terminal_observation"))

    def send_correction(self, **_arguments):
        # This adapter has not established native reviewer addressability and
        # exact correction-turn linkage. No message or hidden prompt is sent.
        return dict(effect="not_sent", correction_turn_id=None, source=None)
