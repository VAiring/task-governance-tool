"""Bounded public answer observations; no transcripts or automatic sends.

Only the known public Codex own-turn enumeration is admitted. Initial creation
context is supplied by the dispatching flow and cannot be reconstructed after
restart from a one-turn page. Legacy bindings keep exact-turn recovery. Explicit
version-two tracking follows its dedicated session until one answer is frozen.
"""

import json
import re
from hashlib import sha256
from uuid import UUID

from .review_answer import AnswerError
from .review_answer_collection import AnswerTurnInProgress, source
from .review_wait_runtime.review_wait_mcp_relay import MAX_READ_RESPONSE_BYTES
from .review_wait_runtime.review_wait_host import HostAdapterError, _oversized_read


_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}\Z")
_TERMINALS = {"inProgress": "in_progress", "completed": "completed", "failed": "failed", "interrupted": "interrupted"}
_NON_ACTIVITY = frozenset({
    "userMessage", "hookPrompt", "agentMessage", "functionCallOutput", "plan",
    "reasoning", "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
    "webSearch", "imageView", "sleep", "imageGeneration", "enteredReviewMode",
    "exitedReviewMode", "contextCompaction",
})


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


def initial_observation(parent_turn, count, last_id):
    return dict(parent_turn_id=parent_turn, item_count=count,
                last_item_digest="sha256:" + sha256(last_id.encode("ascii")).hexdigest())


def _require_empty_wait(item, parent):
    """Only the observed no-recipient, no-send legacy operation is admissible."""
    _content_complete(item)
    allowed = {"id", "type", "tool", "status", "senderThreadId", "receiverThreadIds",
               "prompt", "model", "reasoningEffort", "agentsStates", "complete",
               "truncated", "isTruncated", "textTruncated", "contentTruncated", "missingContent", "hasMoreContent"}
    if (not item.keys() <= allowed or item.get("tool") != "wait" or item.get("status") != "completed"
            or item.get("senderThreadId") != parent or item.get("receiverThreadIds") != []
            or item.get("agentsStates", {}) != {}
            or any(item.get(key) is not None for key in ("prompt", "model", "reasoningEffort"))):
        _fail("incomplete")


def _check_wait_order(items, children):
    started = False
    for item in items:
        if item["type"] == "subAgentActivity" and item.get("kind") == "started" and item.get("agentThreadId") in children:
            started = True
        elif started and item["type"] == "collabAgentToolCall":
            _fail("incomplete")


def _item_prefix(turn, parent, children):
    items = turn["items"]
    if not 1 <= len(items) <= 3072:
        _fail("bound")
    ids = []
    for item in items:
        _content_complete(item)
        kind = item.get("type")
        if kind == "collabAgentToolCall":
            _require_empty_wait(item, parent)
        elif kind == "subAgentActivity":
            _uuid(item.get("agentThreadId"))
            if item.get("kind") not in ("started", "completed", "interacted", "failed", "interrupted"):
                _fail("incomplete")
        elif type(kind) is not str or kind not in _NON_ACTIVITY:
            _fail("incomplete")
        ids.append((_host_id(item.get("id")), kind))
    if len(ids) != len({identifier for identifier, _ in ids}):
        _fail("conflict", "binding_mismatch")
    _check_wait_order(items, children)
    return ids


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
    _content_complete(page)
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


def _observed_reviewer_ids(items, references, *, expected=None):
    """Return observed IDs in slot order; reject known handle conflicts even if another handle is absent."""
    identities = []
    for index, ref in enumerate(references):
        if ref.startswith("/"):
            matches = {item["agentThreadId"] for item in items
                       if item.get("type") == "subAgentActivity" and item.get("agentPath") == ref
                       and item.get("kind") in ("started", "interacted")}
            if not matches:
                continue
            if len(matches) != 1:
                _fail("conflict", "binding_mismatch")
            identity = matches.pop()
        else:
            identity = _uuid(ref)
        if expected is not None and identity != expected[index]:
            _fail("conflict", "binding_mismatch")
        identities.append(identity)
    return tuple(identities)


def _dispatch_activity(items, child, expected_dispatch=None):
    """A completion notice is not another dispatch or permission to reuse a child."""
    activities = [item for item in items if type(item) is dict
                  and item.get("type") == "subAgentActivity"
                  and (item.get("agentThreadId") == child
                       or expected_dispatch is not None and item.get("id") == expected_dispatch)]
    if not activities:
        # Absence is a fact about this complete current turn only. Malformed
        # or clipped activity cannot establish it, nor can this recover history.
        seen = set()
        for item in items:
            if type(item) is not dict or type(item.get("type")) is not str:
                _fail("conflict", "binding_mismatch")
            if item["type"] != "subAgentActivity":
                continue
            _content_complete(item)
            _uuid(item.get("agentThreadId"))
            identifier = _host_id(item.get("id"))
            if identifier in seen or item.get("kind") not in ("started", "completed", "interacted", "failed", "interrupted"):
                _fail("conflict", "binding_mismatch")
            seen.add(identifier)
        _fail("initial_dispatch_unobserved", "binding_mismatch")
    starts = [item for item in activities if item.get("kind") == "started"]
    completions = [item for item in activities if item.get("kind") == "completed"]
    interactions = [item for item in activities if item.get("kind") == "interacted"]
    if (len(starts) != 1 or len(completions) > 1
            or len(activities) != len(starts) + len(completions) + len(interactions)):
        _fail("conflict", "binding_mismatch")
    for item in activities:
        _content_complete(item)
        identifier = _host_id(item.get("id"))
        if (item.get("agentThreadId") != child
                or sum(type(other) is dict and other.get("type") == "subAgentActivity"
                       and other.get("id") == identifier for other in items) != 1):
            _fail("conflict", "binding_mismatch")
    dispatch = starts[0]["id"]
    if expected_dispatch is not None and dispatch != expected_dispatch:
        _fail("conflict", "binding_mismatch")
    if interactions:
        # Only validated same-child activity identifies this particular cause.
        # Unknown sends, ambiguous dispatches and identity faults stay generic.
        _fail("initial_binding_interacted", "binding_mismatch")
    return dispatch


class PublicAnswerHost:
    def __init__(self, host, parent_session_id, parent_turn_id=None, *, item_reader=None, reviewer_paths=None,
                 verify_whole_turn_tail=False):
        self.host = host
        self.parent_session_id = _uuid(parent_session_id)
        self.parent_turn_id = None if parent_turn_id is None else _uuid(parent_turn_id)
        self.item_reader = item_reader
        self.reviewer_paths = reviewer_paths or {}
        self.verify_whole_turn_tail = verify_whole_turn_tail

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
        except HostAdapterError as error:
            _fail("bound" if _oversized_read(error) else "transport_unavailable")
        except Exception:
            _fail("transport_unavailable")

    def bind_dispatch(self, dispatch_id, reviewer_session_id, *, creation_context=False, known_conflict=False):
        child = _uuid(reviewer_session_id)
        _host_id(dispatch_id)
        if (self.parent_turn_id is None or creation_context is not True
                or known_conflict is not False or child == self.parent_session_id):
            _fail("identity", "binding_mismatch")
        if self.item_reader is not None or self.verify_whole_turn_tail:
            fact, = self.bind_reviewers((child,), creation_context=True, known_conflict=False)
            if fact.pop("dispatch_id") != dispatch_id:
                _fail("conflict", "binding_mismatch")
            return fact
        fact, = self._bind_whole((child,), expected_dispatch=dispatch_id)
        fact.pop("dispatch_id")
        return fact

    def _sole_child_turn(self, child):
        page, turns = _page(self._read(child, 1), child, 1)
        if page["hasMore"] or page["nextCursor"] is not None:
            _fail("incomplete")
        turn = turns[0]
        _content_complete(turn)
        if turn["error"] is not None and turn["status"] not in ("failed", "interrupted"):
            _fail("incomplete")
        return turn["id"]

    def bind_reviewers(self, reviewer_ids, *, creation_context=False, known_conflict=False):
        """Resolve exact returned children from this dispatch turn only."""
        if (self.parent_turn_id is None or type(reviewer_ids) not in (tuple, list) or not 1 <= len(reviewer_ids) <= 8
                or creation_context is not True or known_conflict is not False):
            _fail("identity", "binding_mismatch")
        children = tuple(_uuid(child) for child in reviewer_ids)
        if len(set(children)) != len(children) or self.parent_session_id in children:
            _fail("identity", "binding_mismatch")
        if self.item_reader is not None:
            from .review_answer_item_pages import DispatchTurn, PaginationUnsupported
            references = tuple(self.reviewer_paths.get(child, child) for child in children)
            observation = None
            try:
                with self.item_reader() as rpc:
                    observation = DispatchTurn(rpc, self.parent_session_id, self.parent_turn_id)
                    return observation.bind(children, references, whole_child=self._sole_child_turn)
            except PaginationUnsupported:
                # A missing first capability can select the complete Desktop
                # read. Never discard a page or restart after the tail changed.
                if observation is not None and observation.pages:
                    _fail("incomplete")
        return self._bind_whole(children)

    def _bind_whole(self, children, *, expected_dispatch=None):
        _page_info, turns = _page(self._read(self.parent_session_id, 1), self.parent_session_id, 1)
        parent = turns[0]
        _content_complete(parent)
        if parent["id"] != self.parent_turn_id:
            _fail("identity", "binding_mismatch")
        if parent["error"] is not None:
            if expected_dispatch is not None:
                _fail("incomplete")
            _fail("identity", "binding_mismatch")
        verify_tail = self.verify_whole_turn_tail or self.item_reader is not None or any(
            isinstance(item, dict) and item.get("type") == "collabAgentToolCall" for item in parent["items"])
        before = _item_prefix(parent, self.parent_session_id, children) if verify_tail and parent["items"] else []
        references = tuple(self.reviewer_paths.get(child, child) for child in children)
        observed = _observed_reviewer_ids(parent["items"], references, expected=children) if verify_tail else children
        dispatches = []
        missing_dispatch = False
        for child in children:
            try:
                dispatches.append(_dispatch_activity(parent["items"], child, expected_dispatch))
            except AnswerError as error:
                if error.failure["reason"] != "initial_dispatch_unobserved":
                    raise
                # Absence must not mask an observed failure for another
                # requested reviewer, regardless of prepared slot order.
                missing_dispatch = True
        if missing_dispatch:
            _fail("initial_dispatch_unobserved", "binding_mismatch")
        if observed != children or len(set(dispatches)) != len(dispatches):
            _fail("conflict", "binding_mismatch")
        results = []
        for child, dispatch in zip(children, dispatches):
            results.append(dict(dispatch_id=dispatch, parent_session_id=self.parent_session_id,
                reviewer_session_id=child, initial_turn_id=self._sole_child_turn(child), source=source("dispatch_binding")))
        if verify_tail:
            _, final_turns = _page(self._read(self.parent_session_id, 1), self.parent_session_id, 1)
            final = final_turns[0]
            _content_complete(final)
            if final["id"] != self.parent_turn_id or final["error"] is not None:
                _fail("identity", "binding_mismatch")
            after = _item_prefix(final, self.parent_session_id, children)
            if after[:len(before)] != before:
                _fail("conflict", "binding_mismatch")
            if _observed_reviewer_ids(final["items"], references, expected=children) != children:
                _fail("conflict", "binding_mismatch")
            for child, dispatch, result in zip(children, dispatches, results):
                _dispatch_activity(final["items"], child, dispatch)
                result["initial_observation"] = initial_observation(self.parent_turn_id, len(after), after[-1][0])
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

    def observe_tracking_baseline(self, reviewer_session_id):
        """Observe the closed prior turn before an explicit continuation send."""
        child = _uuid(reviewer_session_id)
        _page_info, turns = _page(self._read(child, 2), child, 2)
        turn = turns[0]
        _content_complete(turn)
        if turn["status"] not in ("completed", "failed", "interrupted"):
            _fail("incomplete")
        if turn["error"] is not None and turn["status"] == "completed":
            _fail("incomplete")
        return dict(turn_id=turn["id"], terminal=_TERMINALS[turn["status"]],
                    binding_source={**source("reviewer_tracking"), "version": 2})

    def observe_latest_answer(self, *, basis_digest, slot, reviewer_session_id,
                              boundary_turn_id, boundary):
        """Observe the latest own final under an explicitly armed dedicated session.

        This is session tracking, not proof that a particular send created a
        turn. Completed source identity is frozen by the collector, not here.
        """
        child, excluded = _uuid(reviewer_session_id), _uuid(boundary_turn_id)
        if boundary not in ("inclusive", "exclusive"):
            _fail("shape", "binding_mismatch")
        _page_info, turns = _page(self._read(child, 2), child, 2)
        turn = turns[0]
        _content_complete(turn)
        if boundary == "exclusive" and turn["id"] == excluded:
            _fail("missing")
        if turn["status"] == "inProgress" and turn["error"] is None:
            # Explicitly clipped final items are not healthy running observations.
            # This does not decode or certify a body that is still being produced.
            for item in turn["items"]:
                if type(item) is dict and item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                    _content_complete(item)
            raise AnswerTurnInProgress(child, turn["id"])
        if turn["status"] != "completed" or turn["error"] is not None:
            _fail("incomplete")
        finals = [item for item in turn["items"] if type(item) is dict
                  and item.get("type") == "agentMessage" and item.get("phase") == "final_answer"]
        if len(finals) != 1:
            _fail("missing" if not finals else "conflict")
        item = finals[0]
        _content_complete(item)
        identifier = _host_id(item.get("id"))
        if type(item.get("text")) is not str:
            _fail("shape")
        try:
            raw = item["text"].encode("utf-8")
        except UnicodeError:
            _fail("unicode")
        if not raw or len(raw) > 262144:
            _fail("bound", "capacity_exceeded")
        return dict(source_identity=dict(reviewer_session_id=child, turn_id=turn["id"], message_id=identifier),
            answer_bytes=raw, complete=True, terminal="completed",
            answer_source={**source("answer_observation"), "version": 2},
            terminal_source={**source("terminal_observation"), "version": 2})

    def send_correction(self, **_arguments):
        # This adapter has not established native reviewer addressability and
        # exact correction-turn linkage. No message or hidden prompt is sent.
        return dict(effect="not_sent", correction_turn_id=None, source=None)
