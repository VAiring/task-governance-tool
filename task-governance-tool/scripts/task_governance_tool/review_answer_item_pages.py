"""Read one dispatch turn through the public paginated App Server interface."""

from .review_answer_host import _content_complete, _dispatch_activity, _fail, _host_id, _uuid, initial_observation
from .review_answer_collection import source


PAGE_SIZE = 4
MAX_PAGES = 768
MAX_ITEMS = 3072
_NON_ACTIVITY = frozenset({
    "userMessage", "hookPrompt", "agentMessage", "functionCallOutput", "plan",
    "reasoning", "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
    "webSearch", "imageView", "sleep", "imageGeneration", "enteredReviewMode",
    "exitedReviewMode", "contextCompaction",
})


def _cursor(page, limit):
    _content_complete(page)
    if ("nextCursor" not in page or type(page.get("data")) is not list
            or len(page["data"]) > limit):
        _fail("shape")
    cursor = page["nextCursor"]
    if cursor is not None and (type(cursor) is not str or not 1 <= len(cursor) <= 4096
                               or not page["data"]):
        _fail("shape")
    return cursor


def latest_turn_id(rpc, thread, *, sole=False):
    result = rpc("thread/read", threadId=thread, includeTurns=False)
    _content_complete(result)
    meta = result.get("thread")
    _content_complete(meta)
    if meta.get("id") != thread or meta.get("historyMode") != "paginated":
        _fail("identity", "binding_mismatch")
    limit = 2 if sole else 1
    page = rpc("thread/turns/list", threadId=thread, sortDirection="desc",
               itemsView="notLoaded", limit=limit)
    cursor = _cursor(page, limit)
    if not page["data"] or sole and (cursor is not None or len(page["data"]) != 1):
        _fail("incomplete")
    latest = page["data"][0]
    _content_complete(latest)
    if (latest.get("itemsView") != "notLoaded" or latest.get("items") != []
            or latest.get("status") not in ("completed", "inProgress", "interrupted", "failed")
            or "error" not in latest or latest["error"] is not None and
            (not sole or latest["status"] not in ("failed", "interrupted"))):
        _fail("incomplete")
    return _uuid(latest.get("id"))


class DispatchTurn:
    """Keep item identities and structured dispatch facts, never message bodies."""

    def __init__(self, rpc, parent, turn):
        self.rpc, self.parent, self.turn = rpc, _uuid(parent), _uuid(turn)
        self.seen_ids = set()
        self.activities = []
        self.last = None
        self.pages = 0

    def read(self, *, tail=False):
        if tail and self.last is None:
            _fail("incomplete")
        cursor = {"type": "item", "itemId": self.last} if tail else None
        cursors = set()
        while self.pages < MAX_PAGES:
            page = self.rpc("thread/items/list", threadId=self.parent, turnId=self.turn,
                            sortDirection="asc", limit=PAGE_SIZE, cursor=cursor)
            self.pages += 1
            cursor = _cursor(page, PAGE_SIZE)
            for row in page["data"]:
                _content_complete(row)
                if row.get("turnId") != self.turn:
                    _fail("identity", "binding_mismatch")
                item = row.get("item")
                _content_complete(item)
                item_id = _host_id(item.get("id"))
                if item_id in self.seen_ids:
                    _fail("conflict", "binding_mismatch")
                if len(self.seen_ids) >= MAX_ITEMS:
                    _fail("bound")
                self.seen_ids.add(item_id)
                self.last = item_id
                kind = item.get("type")
                if type(kind) is not str:
                    _fail("shape")
                if kind == "subAgentActivity":
                    if (item.get("kind") not in ("started", "completed", "interacted", "failed", "interrupted")
                            or type(item.get("agentPath")) is not str or len(item["agentPath"]) > 256):
                        _fail("shape")
                    self.activities.append(dict(type="subAgentActivity", id=item_id, kind=item["kind"],
                                               agentPath=item["agentPath"], agentThreadId=_uuid(item.get("agentThreadId"))))
                elif kind not in _NON_ACTIVITY:
                    # The older collaboration-call representation does not
                    # prove the same start/contact lifecycle as a native activity.
                    _fail("incomplete")
            if cursor is None:
                return
            if cursor in cursors:
                _fail("conflict", "binding_mismatch")
            cursors.add(cursor)
        _fail("bound")

    def resolve(self, references):
        identities = []
        for ref in references:
            if ref.startswith("/"):
                matches = {item["agentThreadId"] for item in self.activities
                           if item["agentPath"] == ref and item["kind"] in ("started", "interacted")}
                if len(matches) != 1:
                    _fail("conflict" if matches else "initial_dispatch_unobserved", "binding_mismatch")
                identities.append(matches.pop())
            else:
                identities.append(_uuid(ref))
        if self.parent in identities or len(set(identities)) != len(identities):
            _fail("conflict", "binding_mismatch")
        return tuple(identities)

    def dispatches(self, children):
        from .review_answer import AnswerError
        dispatches = []
        for child in children:
            try:
                dispatches.append(_dispatch_activity(self.activities, child))
            except AnswerError as error:
                if error.failure["reason"] != "initial_dispatch_unobserved":
                    raise
        if len(dispatches) != len(children):
            _fail("initial_dispatch_unobserved", "binding_mismatch")
        return dispatches

    def bind(self, children, references):
        if latest_turn_id(self.rpc, self.parent) != self.turn:
            _fail("identity", "binding_mismatch")
        self.read()
        if self.resolve(references) != children:
            _fail("conflict", "binding_mismatch")
        initial = self.dispatches(children)
        child_turns = [latest_turn_id(self.rpc, child, sole=True) for child in children]
        self.read(tail=True)
        if self.resolve(references) != children or self.dispatches(children) != initial:
            _fail("conflict", "binding_mismatch")
        if latest_turn_id(self.rpc, self.parent) != self.turn:
            _fail("identity", "binding_mismatch")
        boundary = initial_observation(self.turn, len(self.seen_ids), self.last)
        return tuple(dict(dispatch_id=dispatch, parent_session_id=self.parent,
                          reviewer_session_id=child, initial_turn_id=turn,
                          source=source("dispatch_binding"), initial_observation=boundary.copy())
                     for child, turn, dispatch in zip(children, child_turns, initial))
