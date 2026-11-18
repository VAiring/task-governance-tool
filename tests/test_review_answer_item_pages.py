"""Initial observation ends at the last page read after all child reads."""

from contextlib import nullcontext
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_host import PublicAnswerHost
from task_governance_tool.review_answer_item_pages import DispatchTurn
from task_governance_tool.review_answer_runtime import bind_initial
from task_governance_tool.review_answer_collection_repository import CollectionRepository, RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_host import PublicMcpHost
from task_governance_tool.review_wait_runtime.item_reader import open_reader
from tests import test_review_answer_runtime_setup as setup
from tests.test_review_continuation import A, B


PARENT = "30000000-0000-4000-8000-000000000001"
TURN = "40000000-0000-4000-8000-000000000001"


def activity(ident, child, kind="started"):
    return dict(type="subAgentActivity", id=ident, kind=kind, agentThreadId=child,
                agentPath="/root/" + ("a" if child == A else "b"))


def messages(n):
    return [dict(type="agentMessage", id=f"message-{i}") for i in range(n)]


class PublicPages:
    def __init__(self, parent=PARENT, turn=TURN):
        self.parent = parent
        self.rows = [activity("a", A), activity("b", B)]
        self.after_children = []
        self.calls = []
        self.child_read = set()
        self.turn = self.parent_turn = turn
        self.status, self.thread_state = "interrupted", "notLoaded"
        self.second_child_turn = False

    def __call__(self, method, *, threadId, **params):
        self.calls.append((method, threadId, params))
        if method == "thread/read":
            return dict(thread=dict(id=threadId, historyMode="paginated", status={"type": self.thread_state}))
        if method == "thread/turns/list":
            own = self.parent_turn if threadId == self.parent else threadId
            turn = dict(id=own, itemsView="notLoaded", items=[], status=self.status, error=None)
            if threadId != self.parent:
                self.child_read.add(threadId)
                if self.child_read == {A, B}:
                    self.rows.extend(self.after_children)
            return dict(data=[turn], nextCursor="more" if self.second_child_turn and threadId == B else None)
        assert method == "thread/items/list" and params["turnId"] == self.turn
        cursor = params["cursor"]
        start = 0
        if isinstance(cursor, dict):
            start = next(i for i, item in enumerate(self.rows) if item["id"] == cursor["itemId"]) + 1
        elif cursor is not None:
            start = int(cursor)
        end = start + params["limit"]
        return dict(data=[dict(turnId=self.turn, item=item) for item in self.rows[start:end]],
                    nextCursor=str(end) if end < len(self.rows) else None)

    def bind(self):
        return DispatchTurn(self, self.parent, TURN).bind((A, B), ("/root/a", "/root/b"))


class DispatchPageTests(unittest.TestCase):
    def test_small_whole_turn_fallback_rechecks_after_children(self):
        from tests.test_review_answer_host import page, turn
        base = [activity("start-a", A), activity("start-b", B)]
        for appended, reason in (
                (messages(1), None),
                ([activity("later", B, "interacted")], "initial_binding_interacted"),
                ([dict(id="later", type="collabAgentToolCall")], "incomplete")):
            with self.subTest(appended=appended):
                peer = Mock()
                peer._json_call.side_effect = [
                    page(PARENT, 1, turn(TURN, items=base)), page(A, 1, turn(A)), page(B, 1, turn(B)),
                    page(PARENT, 1, turn(TURN, items=base + appended))]
                host = PublicAnswerHost(peer, PARENT, TURN, reviewer_paths={A:"/root/a", B:"/root/b"},
                                        verify_whole_turn_tail=True)
                if reason is None:
                    facts = host.bind_reviewers((A, B), creation_context=True)
                    self.assertEqual([v["initial_observation"]["item_count"] for v in facts], [3,3])
                else:
                    with self.assertRaises(AnswerError) as failure:
                        host.bind_reviewers((A, B), creation_context=True)
                    self.assertEqual(failure.exception.failure["reason"], reason)

    def test_reader_closes_on_invalid_frame_unknown_operation_and_cleanup_failure(self):
        home = Path.cwd()
        init = dict(id=1, result=dict(userAgent="Codex Desktop/0.169.0-alpha.11.1 (Windows)",
                                     codexHome=str(home)))
        for name, method, response, clean in (
                ("method", "turn/start", None, True),
                ("server_request", "thread/read", {"id": 2, "method":"request"}, True),
                ("wrong_id", "thread/read", {"id": 3, "result":{}}, True),
                ("unclean_exit", "thread/read", {"id": 2, "result":{}}, False)):
            with self.subTest(name=name), patch("task_governance_tool.review_wait_runtime.item_reader._ItemSession") as launch:
                child = launch.return_value
                child.close.return_value = clean
                child._message.side_effect = [init, response]
                with self.assertRaises(AnswerError), open_reader(home/"codex.exe",home) as read:
                    read(method, threadId=PARENT)
                sent = [json.loads(c.args[0])["method"] for c in child.send.call_args_list]
                self.assertEqual(sent, ["initialize","initialized"] + (["thread/read"] if method == "thread/read" else []))
                child.close.assert_called_once_with()

    def test_all_tail_pages_and_frozen_observation(self):
        rpc = PublicPages()
        rpc.after_children = messages(10)
        facts = rpc.bind()
        rpc.rows.append(activity("later", B, "interacted"))
        for child, fact in zip((A, B), facts):
            self.assertEqual((fact["reviewer_session_id"], fact["initial_turn_id"]), (child, child))
            self.assertEqual(fact["initial_observation"], dict(parent_turn_id=TURN, item_count=12,
                last_item_digest="sha256:" + sha256(b"message-9").hexdigest()))
        self.assertEqual([c[2]["cursor"] for c in rpc.calls if c[0] == "thread/items/list"],
                         [None, {"type": "item", "itemId": "b"}, "6", "10"])

    def test_last_tail_page_can_veto_both_children(self):
        for item in (activity("another", A), activity("contact", B, "interacted"),
                     activity("a", B), dict(type="collabAgentToolCall", id="unknown")):
            with self.subTest(item=item):
                rpc = PublicPages()
                rpc.after_children = messages(9) + [item]
                with self.assertRaises(AnswerError):
                    rpc.bind()

    def test_reused_child_changed_parent_and_exhausted_budget(self):
        rpc = PublicPages()
        rpc.second_child_turn = True
        with self.assertRaises(AnswerError):
            rpc.bind()
        rpc = PublicPages()
        def switched(method, **params):
            if rpc.child_read == {A, B}:
                rpc.parent_turn = A
            return rpc(method, **params)
        with self.assertRaises(AnswerError):
            DispatchTurn(switched, PARENT, TURN).bind((A, B), (A, B))
        with patch("task_governance_tool.review_answer_item_pages.MAX_PAGES", 1), self.assertRaises(AnswerError):
            PublicPages().bind()

    def test_incomplete_foreign_and_cyclic_pages(self):
        pages = (
            [dict(data=[])],
            [dict(data=[], nextCursor="more")],
            [dict(data=[dict(turnId=A, item=activity("a", A))], nextCursor=None)],
            [dict(data=[dict(turnId=TURN, item=activity(str(i), A))], nextCursor="same") for i in range(2)],
            [dict(data=[dict(turnId=TURN, item=activity("a", A))], nextCursor=None, truncated=True)],
        )
        for sequence in pages:
            with self.subTest(sequence=sequence), self.assertRaises(AnswerError):
                DispatchTurn(Mock(side_effect=sequence), PARENT, TURN).read()

    service = setup.InitialBindingStateTests.service

    def test_observation_survives_restart_and_cannot_be_retrofitted(self):
        for tracking in (False, True):
            with self.subTest(tracking=tracking):
                service, meta = self.service(tracking)
                rpc = PublicPages(meta["binding"]["parent_session_id"])
                host = PublicAnswerHost(None, rpc.parent, TURN, item_reader=lambda: nullcontext(rpc))
                rows = bind_initial(service, host, (A, B), creation_context=True)
                self.assertEqual(rows, CollectionRepository(service.repository.path).read())
                self.assertEqual(rows, bind_initial(service, Mock(), (A, B)))
                with self.assertRaises(RepositoryError), service.repository.serial() as lease:
                    service.repository.update(lease, 1, lambda row: {k:v for k,v in row.items() if k != "initial_observation"})

    def test_assignment_change_while_reading_is_never_overwritten(self):
        service, meta = self.service(True)
        parent = meta["binding"]["parent_session_id"]
        competing, _ = setup.InitialBindingStateTests.host(self, meta)
        rpc = PublicPages(parent)
        def observe(method, **params):
            if method == "thread/items/list" and type(params.get("cursor")) is dict:
                bind_initial(service, competing, (B, A), creation_context=True)
            return rpc(method, **params)
        host = PublicAnswerHost(None, parent, TURN, item_reader=lambda: nullcontext(observe))
        with self.assertRaises(AnswerError):
            bind_initial(service, host, (A, B), creation_context=True)
        saved = service.repository.read()
        self.assertEqual([r["binding"]["reviewer_session_id"] for r in saved], [B,A])
        self.assertTrue(all("initial_observation" not in r for r in saved))

    def test_wait_requires_fresh_active_parent_and_original_turn_after_binding(self):
        from tests.test_review_wait_managed import ManagedTrackingAdmissionTests, ORIGINAL, NEW_TURN
        from task_governance_tool.review_wait_runtime.review_wait_server import _call
        for state in ("running", "switched", "oversized"):
            with self.subTest(state=state):
                fixture = ManagedTrackingAdmissionTests(methodName="runTest")
                fixture.setUp()
                try:
                    fixture.issued_collection()
                    rpc = PublicPages(fixture.parent, ORIGINAL)
                    filler = "x" * (512 * 1024)
                    rpc.rows.extend(dict(item, text=filler) for item in messages(34))
                    fixture.turns = {A:A, B:B}
                    base_factory = fixture.flow.factory
                    class Bridge:
                        def __init__(self, **kwargs):
                            self.delegate = base_factory(**kwargs)
                            self.public = PublicMcpHost(metadata=kwargs["metadata"], automation_id="pending",
                                codex_home=Path.cwd(), confirmed_timezone="UTC", timezone_source="host_os",
                                reviewer_ids=kwargs["reviewer_ids"], command=["offline-peer"])
                            self.public.item_reader = lambda: nullcontext(rpc)
                            def request(*args, **kw):
                                return dict(isError=True, content=[dict(type="text", text=
                                    "Thread history exceeds the read size limit. Try fewer turns. "
                                    "A single oversized turn cannot be read by this tool.")])
                            self.public._request = request

                        def __getattr__(self, name):
                            return getattr(self.delegate, name)

                        def item_binding_options(self):
                            return self.public.item_binding_options()

                        def resolve_reviewer_ids(self):
                            return self.public.resolve_reviewer_ids()

                        def read_parent(self):
                            # Even an active-looking auxiliary header cannot authorize
                            # a wait if the owning Desktop cannot return the turn.
                            if state == "oversized":
                                rpc.status, rpc.thread_state = "inProgress", "active"
                                return self.public.read_parent()
                            parent = self.delegate.read_parent()
                            return replace(parent, turn_id=NEW_TURN) if state == "switched" else parent

                    fixture.flow.session.managed_host_factory = Bridge
                    result = _call(dict(name="review_wait_wait", arguments=dict(task_id=fixture.task,
                        reviewer_ids=["/root/a", "/root/b"]), _meta=fixture.metadata), fixture.flow.session)
                    body = json.loads(result["content"][0]["text"])
                    self.assertEqual([r.get("initial_observation",{}).get("item_count")
                                      for r in fixture.selection.service().repository.read()], [36,36], body)
                    if state == "running":
                        self.assertEqual(body.get("status"), "waiting", body)
                        self.assertEqual(fixture.flow.effects, ["create","arm"])
                    else:
                        self.assertFalse(body["ok"], body)
                        self.assertEqual(fixture.flow.effects, [])
                finally:
                    fixture.doCleanups()
