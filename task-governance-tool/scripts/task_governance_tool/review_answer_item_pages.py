"""Read one dispatch turn through the public paginated App Server interface."""

from .review_answer import AnswerError
from .review_answer_host import (
    _NON_ACTIVITY, _check_wait_order, _content_complete, _dispatch_activity, _fail,
    _host_id, _require_empty_wait, _uuid, initial_observation,
)
from .review_answer_collection import source


PAGE_SIZE = 4
MAX_PAGES = 768
MAX_ITEMS = 3072


class PaginationUnsupported(AnswerError):
    """An explicit capability result; unknown or malformed replies never qualify."""

    def __init__(self):
        super().__init__("collection_unavailable", reason="incomplete")


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
    if meta.get("id") != thread:
        _fail("identity", "binding_mismatch")
    if meta.get("historyMode") == "legacy":
        raise PaginationUnsupported()
    if meta.get("historyMode") != "paginated":
        _fail("shape")
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
        self.seen_ids = {}
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
                self.last = item_id
                kind = item.get("type")
                if type(kind) is not str:
                    _fail("shape")
                self.seen_ids[item_id] = kind
                if kind == "subAgentActivity":
                    if (item.get("kind") not in ("started", "completed", "interacted", "failed", "interrupted")
                            or type(item.get("agentPath")) is not str or len(item["agentPath"]) > 256):
                        _fail("shape")
                    self.activities.append(dict(type="subAgentActivity", id=item_id, kind=item["kind"],
                                               agentPath=item["agentPath"], agentThreadId=_uuid(item.get("agentThreadId"))))
                elif kind == "collabAgentToolCall":
                    _require_empty_wait(item, self.parent)
                    self.activities.append(dict(type=kind, id=item_id))
                elif kind not in _NON_ACTIVITY:
                    _fail("incomplete")
            if cursor is None:
                return
            if cursor in cursors:
                _fail("conflict", "binding_mismatch")
            cursors.add(cursor)
        _fail("bound")

    def resolve(self, references, *, expected=None):
        identities = []
        for index, ref in enumerate(references):
            if ref.startswith("/"):
                matches = {item["agentThreadId"] for item in self.activities
                           if item.get("agentPath") == ref and item["kind"] in ("started", "interacted")}
                if not matches:
                    continue
                if len(matches) > 1:
                    _fail("conflict", "binding_mismatch")
                identity = matches.pop()
            else:
                identity = _uuid(ref)
            if expected is not None and identity != expected[index]:
                _fail("conflict", "binding_mismatch")
            identities.append(identity)
        if self.parent in identities or len(set(identities)) != len(identities):
            _fail("conflict", "binding_mismatch")
        children = identities if expected is None else expected
        _check_wait_order(self.activities, children)
        if len(identities) != len(references):
            # An absent handle must not mask a known child's conflicting activity.
            self.dispatches(children)
            # If every expected child dispatched, the missing path disagrees with its UUID.
            _fail("initial_dispatch_unobserved" if expected is None else "conflict", "binding_mismatch")
        return tuple(identities)

    def dispatches(self, children):
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

    def bind(self, children, references, *, whole_child=None):
        if latest_turn_id(self.rpc, self.parent) != self.turn:
            _fail("identity", "binding_mismatch")
        self.read()
        self.resolve(references, expected=children)
        initial = self.dispatches(children)
        child_turns = []
        for child in children:
            try:
                child_turn = latest_turn_id(self.rpc, child, sole=True)
            except PaginationUnsupported:
                if whole_child is None:
                    raise
                child_turn = whole_child(child)
            child_turns.append(child_turn)
        if "collabAgentToolCall" in self.seen_ids.values():
            # A successor cursor cannot prove the wait is still before every
            # selected start. Recheck that ordered prefix on the same budget.
            first_items = tuple(self.seen_ids.items())
            first_activities = self.activities
            self.seen_ids = {}
            self.activities = []
            self.last = None
            self.read()
            if (tuple(self.seen_ids.items())[:len(first_items)] != first_items
                    or self.activities[:len(first_activities)] != first_activities):
                _fail("conflict", "binding_mismatch")
        self.read(tail=True)
        self.resolve(references, expected=children)
        if self.dispatches(children) != initial:
            _fail("conflict", "binding_mismatch")
        if latest_turn_id(self.rpc, self.parent) != self.turn:
            _fail("identity", "binding_mismatch")
        boundary = initial_observation(self.turn, len(self.seen_ids), self.last)
        return tuple(dict(dispatch_id=dispatch, parent_session_id=self.parent,
                          reviewer_session_id=child, initial_turn_id=turn,
                          source=source("dispatch_binding"), initial_observation=boundary.copy())
                     for child, turn, dispatch in zip(children, child_turns, initial))
