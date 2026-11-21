"""Initial observation ends at the last page read after all child reads."""

from contextlib import nullcontext
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_host import PublicAnswerHost
from task_governance_tool.review_answer_item_pages import DispatchTurn, PaginationUnsupported
from task_governance_tool.review_answer_runtime import bind_initial
from task_governance_tool.review_answer_collection_repository import CollectionRepository, RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_host import PublicMcpHost
from task_governance_tool.review_wait_runtime.item_reader import _bundled_runtime, installed_runtime, open_reader
from tests import test_review_answer_runtime_setup as setup
from tests.test_review_continuation import A, B


PARENT = "30000000-0000-4000-8000-000000000001"
TURN = "40000000-0000-4000-8000-000000000001"


def activity(ident, child, kind="started"):
    return dict(type="subAgentActivity", id=ident, kind=kind, agentThreadId=child,
                agentPath="/root/" + ("a" if child == A else "b"))


def messages(n):
    return [dict(type="agentMessage", id=f"message-{i}") for i in range(n)]


def legacy_wait(**changes):
    item = dict(type="collabAgentToolCall", id="wait", tool="wait", status="completed",
                senderThreadId=PARENT, receiverThreadIds=[], prompt=None)
    return {**item, **changes}


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
    def observe_history(self, route, items, children, *, handles=False, after=None):
        from tests.test_review_answer_host import page, turn
        paths = {A: "/root/a", B: "/root/b"} if handles else {}
        if route == "whole":
            peer = Mock()
            peer._json_call.side_effect = [
                page(PARENT, 1, turn(TURN, items=items)),
                *(page(child, 1, turn(child)) for child in children),
                page(PARENT, 1, turn(TURN, items=items if after is None else after))]
            host = PublicAnswerHost(peer, PARENT, TURN, verify_whole_turn_tail=True, reviewer_paths=paths)
        else:
            rpc = PublicPages()
            rpc.rows = items.copy()
            def read(method, **params):
                if after is not None and rpc.child_read == set(children):
                    rpc.rows = after
                return rpc(method, **params)
            host = PublicAnswerHost(None, PARENT, TURN, item_reader=lambda: nullcontext(read), reviewer_paths=paths)
        return host.bind_reviewers(children, creation_context=True)

    def test_legacy_wait_precedes_every_selected_start_across_history_readers(self):
        unrelated = dict(activity("other", TURN), agentPath="/root/other")
        items = [unrelated, legacy_wait(), activity("a", A), activity("b", B)]
        for route in ("whole", "paged"):
            for children in ((A,), (B, A)):
                for handles in (False, True):
                    with self.subTest(route=route, children=children, handles=handles):
                        facts = self.observe_history(route, items, children, handles=handles,
                                                     after=items + messages(1))
                        self.assertEqual([f["initial_turn_id"] for f in facts], list(children))
                        self.assertTrue(all(f["initial_observation"]["item_count"] == 5 for f in facts))

    def test_missing_dispatch_preserves_other_reviewers_failure_across_readers(self):
        for route in ("whole", "paged"):
            for handles in (False, True):
                for children in ((A, B), (B, A)):
                    for extra, reason in (([], "initial_dispatch_unobserved"),
                                          ([activity("touch", B, "interacted")], "initial_binding_interacted"),
                                          ([activity("other-start", B)], "conflict")):
                        with self.subTest(route=route, handles=handles, children=children, reason=reason):
                            with self.assertRaises(AnswerError) as failure:
                                self.observe_history(route, [activity("start-b", B), *extra], children, handles=handles)
                            self.assertEqual(failure.exception.failure["reason"], reason)

    def test_legacy_wait_rejects_unproved_effect_order_and_changed_prefix(self):
        start, second = activity("a", A), activity("b", B)
        base = [legacy_wait(), start, second]
        invalid = [legacy_wait(**change) for change in (
            dict(senderThreadId=A), dict(receiverThreadIds=None), dict(receiverThreadIds=[A]),
            dict(status="inProgress"), dict(tool="sendInput"), dict(prompt="send"),
            dict(unknownEffect=True), dict(truncated=True), dict(id="a"))]
        invalid.append({k:v for k,v in legacy_wait().items() if k != "receiverThreadIds"})
        cases = [([item, start, second], None) for item in invalid]
        cases.extend([([start, legacy_wait(), second], None),
                      (base, base + [legacy_wait(id="later")]),
                      (base, base + [activity("contact", B, "interacted")]),
                      (base, [start, base[0], second]), (base, [start, second]),
                      (base, [dict(id="wait", type="reasoning"), start, second])])
        for route in ("whole", "paged"):
            for rows, after in cases:
                with self.subTest(route=route, rows=rows, after=after), self.assertRaises(AnswerError):
                    self.observe_history(route, rows, (A, B), after=after)

    def test_oversized_handle_read_resolves_mixed_references_with_prelaunch_wait(self):
        from task_governance_tool.review_wait_runtime.review_wait_host import HostAdapterError
        for valid in (True, False):
            with self.subTest(valid=valid):
                host = PublicMcpHost(metadata=dict(threadId=PARENT, turnId=TURN), automation_id="pending",
                    codex_home=Path.cwd(), confirmed_timezone="UTC", timezone_source="host_os",
                    reviewer_ids=["/root/b", A], command=["offline-peer"])
                oversized = HostAdapterError("host_call_failed", operation="read_thread", termination="confirmed")
                oversized.oversized_turn = True
                host._json_call = Mock(side_effect=oversized)
                rpc = PublicPages()
                rpc.rows.insert(0 if valid else 1, legacy_wait())
                host.item_reader = lambda: nullcontext(rpc)
                if valid:
                    self.assertEqual(host.resolve_reviewer_ids(), (B, A))
                else:
                    with self.assertRaises(HostAdapterError) as error:
                        host.resolve_reviewer_ids()
                    self.assertEqual(error.exception.code, "reviewer_identity_unavailable")

    def test_build_changes_admit_complete_small_and_large_dispatches(self):
        with TemporaryDirectory() as temp:
            home = Path(temp)
            for package_version in ("26.1111.2228.0", "27.2.3.4"):
                package = f"OpenAI.Codex_{package_version}_x64__2p2nqsd0c76g0"
                executable = home / "WindowsApps" / package / "app/resources/codex.exe"
                executable.parent.mkdir(parents=True)
                executable.touch()
                admitted = _bundled_runtime(executable, home, home, home)
                self.assertEqual(admitted, executable.resolve(strict=True))
                for server_version in ("0.169.0-alpha.11.1", "1.2.3"):
                    for large, handles in ((False, False), (False, True), (True, False), (True, True)):
                        with self.subTest(package=package_version, server=server_version, large=large, handles=handles):
                            rpc = PublicPages()
                            if large:
                                rpc.rows.extend(dict(v, text="x" * (512 * 1024)) for v in messages(34))
                            with patch("task_governance_tool.review_wait_runtime.item_reader._ItemSession") as launch:
                                child = launch.return_value
                                child.close.return_value = True
                                def reply(_limit):
                                    sent = json.loads(child.send.call_args.args[0])
                                    method = sent["method"]
                                    value = (dict(userAgent="Codex Desktop/" + server_version, codexHome=str(home))
                                             if method == "initialize" else rpc(method, **sent["params"]))
                                    return dict(id=sent["id"], result=value)
                                child._message.side_effect = reply
                                paths = {A: "/root/a", B: "/root/b"} if handles else {}
                                adapter = PublicAnswerHost(None, PARENT, TURN, reviewer_paths=paths,
                                    item_reader=lambda: open_reader(admitted, home))
                                rows = adapter.bind_reviewers((A, B), creation_context=True)
                                self.assertEqual([row["initial_turn_id"] for row in rows], [A, B])
                                self.assertTrue(all(row["initial_observation"]["item_count"] == len(rpc.rows)
                                                    for row in rows))
                                child.close.assert_called_once_with()

    def test_official_package_layout_and_home_are_required(self):
        with patch("task_governance_tool.review_wait_runtime.item_reader.os") as environment:
            environment.name = "nt"
            environment.environ = {}
            with self.assertRaises(AnswerError) as missing:
                installed_runtime(Path.cwd())
            self.assertEqual(missing.exception.failure["reason"], "identity")
        with TemporaryDirectory() as temp:
            root = Path(temp)
            other_home = root / "other"
            other_home.mkdir()
            for package, relative, home in (
                    ("OpenAI.Codex_27.2.3.4_x64__other", "app/resources/codex.exe", root),
                    ("Other.Codex_27.2.3.4_x64__2p2nqsd0c76g0", "app/resources/codex.exe", root),
                    ("OpenAI.Codex_27.2.3.4_x64__2p2nqsd0c76g0", "app/codex.exe", root),
                    ("OpenAI.Codex_27.2.3.4_x64__2p2nqsd0c76g0", "app/resources/codex.exe", other_home)):
                executable = root / "WindowsApps" / package / relative
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.touch()
                with self.subTest(package=package, relative=relative, home=home), self.assertRaises(AnswerError) as error:
                    _bundled_runtime(executable, home, root, root)
                self.assertEqual(error.exception.failure["reason"], "identity")

    def test_documented_windows_environment_binds_new_reviewers(self):
        guide = Path(__file__).resolve().parents[1] / "task-governance-tool/references/review_wait.md"
        setup_section = guide.read_text(encoding="utf-8").split("## Setup And Connection\n", 1)[1].split("\n## ", 1)[0]
        example = setup_section.split("```toml\n", 1)[1].split("\n```", 1)[0]
        forwarded_names = tomllib.loads(example)["env_vars"]
        self.assertIn("CODEX_THREAD_ID", forwarded_names)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            default_home = root / ".codex"
            custom_home = root / "custom-home"
            default_home.mkdir()
            custom_home.mkdir()
            executable = root / "WindowsApps/OpenAI.Codex_27.2.3.4_x64__2p2nqsd0c76g0/app/resources/codex.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            server = root / "server.mjs"
            server.touch()
            desktop = {"CODEX_APP_TOOLS_PIPE_PATH": "fixture", "CODEX_MCP_NODE_PATH": sys.executable,
                       "CODEX_THREAD_ID": PARENT, "CODEX_APP_RUNTIME_CLI_PATH": str(executable)}
            for home, omitted, accepted in (
                    (default_home, None, True), (custom_home, None, True),
                    (default_home, "CODEX_APP_RUNTIME_CLI_PATH", False), (custom_home, "CODEX_HOME", False)):
                source = desktop if home == default_home else {**desktop, "CODEX_HOME": str(custom_home)}
                inherited = {key: source[key] for key in forwarded_names if key in source and key != omitted}
                environment = SimpleNamespace(name="nt", environ={"ProgramFiles": str(root), **inherited})
                with (self.subTest(home=home.name, omitted=omitted),
                      patch.object(Path, "home", return_value=root),
                      patch("task_governance_tool.review_wait_runtime.review_wait_host.os", environment),
                      patch("task_governance_tool.review_wait_runtime.item_reader.os", environment),
                      patch("task_governance_tool.review_wait_runtime.item_reader.open_reader",
                            return_value=nullcontext(PublicPages())) as reader):
                    host = PublicMcpHost.from_environment(server_path=server, metadata=dict(threadId=PARENT, turnId=TURN),
                        automation_id="pending", codex_home=home, confirmed_timezone="UTC",
                        timezone_source="host_node_intl", reviewer_ids=[A, B])
                    answer = PublicAnswerHost(host, PARENT, TURN, **host.item_binding_options())
                    if accepted:
                        facts = answer.bind_reviewers((A, B), creation_context=True)
                        self.assertEqual([fact["initial_turn_id"] for fact in facts], [A, B])
                        reader.assert_called_once_with(executable.resolve(), home)
                    else:
                        with self.assertRaises(AnswerError) as failure:
                            answer.bind_reviewers((A, B), creation_context=True)
                        self.assertEqual(failure.exception.failure,
                                         {"class": "binding_mismatch", "field": None, "reason": "identity"})
                        reader.assert_not_called()

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

    def test_only_explicit_parent_unsupported_can_select_whole_history(self):
        from tests.test_review_answer_host import page, turn
        for mode, identity, clipped, success in (
                ("legacy", PARENT, False, True), ("legacy", A, False, False),
                ("legacy", PARENT, True, False), ("unknown", PARENT, False, False),
                (None, PARENT, False, False)):
            with self.subTest(mode=mode, identity=identity, clipped=clipped):
                meta = {"id": identity, "truncated": clipped}
                if mode is not None:
                    meta["historyMode"] = mode
                peer = Mock()
                parent = page(PARENT, 1, turn(TURN, items=[activity("a", A)]))
                peer._json_call.side_effect = [parent, page(A, 1, turn(A)), parent]
                host = PublicAnswerHost(peer, PARENT, TURN,
                    item_reader=lambda: nullcontext(Mock(return_value=dict(thread=meta))))
                if success:
                    row, = host.bind_reviewers((A,), creation_context=True)
                    self.assertEqual(row["initial_observation"]["item_count"], 1)
                    self.assertEqual(peer._json_call.call_count, 3)
                else:
                    with self.assertRaises(AnswerError):
                        host.bind_reviewers((A,), creation_context=True)
                    peer._json_call.assert_not_called()

    def test_legacy_child_requires_single_own_turn_and_parent_tail(self):
        from tests.test_review_answer_host import page, turn
        for fault in ("none", "reused", "contact"):
            rpc, peer = PublicPages(), Mock()
            def mixed(method, *, threadId, **params):
                if method == "thread/read" and threadId == B:
                    return dict(thread=dict(id=B, historyMode="legacy"))
                return rpc(method, threadId=threadId, **params)
            def child_read(_name, arguments):
                self.assertEqual(arguments["threadId"], B)
                own = page(B, 1, turn(B))
                if fault == "reused":
                    own["page"].update(hasMore=True, nextCursor="older")
                if fault == "contact":
                    rpc.rows.append(activity("extra", B, "interacted"))
                return own
            peer._json_call.side_effect = child_read
            host = PublicAnswerHost(peer, PARENT, TURN, item_reader=lambda: nullcontext(mixed))
            if fault == "none":
                self.assertEqual([f["initial_turn_id"] for f in host.bind_reviewers((A,B), creation_context=True)], [A,B])
            else:
                with self.assertRaises(AnswerError):
                    host.bind_reviewers((A,B), creation_context=True)
            peer._json_call.assert_called_once()

    def test_only_clean_correlated_method_absence_can_enable_fallback(self):
        home = Path.cwd()
        init = dict(id=1, result=dict(userAgent="codex/2.1", codexHome=str(home)))
        missing = dict(code=-32601, message="Method not found")
        for response, clean, prior, eligible in (
                (dict(id=2, error=missing), True, False, True),
                (dict(id=2, error=missing), False, False, False),
                (dict(id=3, error=missing), True, False, False),
                (dict(id=2, error=dict(code=-32603,message="unsupported")), True, False, False),
                (dict(id=2, error=dict(missing, data={"unknown":True})), True, False, False),
                (dict(id=3, error=missing), True, True, False)):
            with self.subTest(response=response, clean=clean, prior=prior), patch(
                    "task_governance_tool.review_wait_runtime.item_reader._ItemSession") as launch:
                child = launch.return_value
                child.close.return_value = clean
                child._message.side_effect = [init] + ([dict(id=2, result={})] if prior else []) + [response]
                with self.assertRaises(AnswerError) as caught, open_reader(home/"codex.exe",home) as read:
                    if prior:
                        read("thread/items/list", threadId=PARENT)
                    read("thread/items/list", threadId=PARENT)
                self.assertEqual(isinstance(caught.exception, PaginationUnsupported), eligible)
                child.close.assert_called_once_with()

    def test_oversized_handle_lookup_retains_the_observed_failure(self):
        from task_governance_tool.review_wait_runtime.review_wait_host import HostAdapterError
        for failure, expected in ((PaginationUnsupported(), "host_call_failed"),
                (AnswerError("binding_mismatch", reason="conflict"), "reviewer_identity_ambiguous"),
                (AnswerError("collection_unavailable", reason="transport_unavailable"), "host_transport_unavailable")):
            host = PublicMcpHost(metadata=dict(threadId=PARENT,turnId=TURN), automation_id="pending",
                codex_home=Path.cwd(), confirmed_timezone="UTC", timezone_source="host_os",
                reviewer_ids=["/root/a"], command=["offline-peer"])
            too_large = HostAdapterError("host_call_failed", operation="read_thread", termination="confirmed")
            too_large.oversized_turn = True
            host._json_call = Mock(side_effect=too_large)
            host.item_reader = Mock(side_effect=failure)
            with self.subTest(expected=expected), self.assertRaises(HostAdapterError) as result:
                host.resolve_reviewer_ids()
            self.assertEqual(result.exception.code, expected)
            self.assertEqual(result.exception.read_failure["conclusion"], "unknown")
            host.item_reader.assert_called_once()
            host._json_call.assert_called_once()

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

    def test_whole_fallback_is_atomic_reusable_and_reports_oversize(self):
        from tests.test_review_answer_host import page, turn
        from task_governance_tool.review_wait_runtime.review_wait_host import HostAdapterError
        for fault in ("none", "oversized", "unknown_item", "changed_tail", "second_start", "extra_contact"):
            with self.subTest(fault=fault):
                service, meta = self.service(True)
                parent = meta["binding"]["parent_session_id"]
                items = [activity("a", A), activity("b", B)]
                first = page(parent, 1, turn(TURN, items=items))
                final_items = items + {
                    "unknown_item": [dict(type="future", id="later")],
                    "second_start": [activity("another", B)],
                    "extra_contact": [activity("later", B, "interacted")],
                }.get(fault, [])
                last = page(parent, 1, turn(A if fault == "changed_tail" else TURN, items=final_items))
                peer = Mock()
                if fault == "oversized":
                    too_large = HostAdapterError("host_call_failed", operation="read_thread", termination="confirmed")
                    too_large.oversized_turn = True
                    peer._json_call.side_effect = too_large
                else:
                    peer._json_call.side_effect = [first, page(A, 1, turn(A)), page(B, 1, turn(B)), last]
                initial = Mock(return_value=dict(thread=dict(id=parent, historyMode="legacy")))
                host = PublicAnswerHost(peer, parent, TURN, item_reader=lambda: nullcontext(initial),
                                        reviewer_paths={A:"/root/a", B:"/root/b"})
                if fault == "none":
                    rows = bind_initial(service, host, (A, B), creation_context=True)
                    self.assertEqual(rows, bind_initial(service, host, (A, B)))
                    self.assertEqual(peer._json_call.call_count, 4)
                    initial.assert_called_once()
                else:
                    with self.assertRaises(AnswerError) as error:
                        bind_initial(service, host, (A, B), creation_context=True)
                    if fault == "oversized":
                        self.assertEqual(error.exception.failure["reason"], "bound")
                    self.assertTrue(all(row["binding"] is None for row in service.repository.read()))

    def test_invalid_initialized_home_never_uses_whole_turn(self):
        with TemporaryDirectory() as temp:
            home = Path(temp)
            other = home / "other"
            other.mkdir()
            with patch("task_governance_tool.review_wait_runtime.item_reader._ItemSession") as launch:
                launch.return_value._message.return_value = dict(id=1, result=dict(userAgent="official client", codexHome=str(other)))
                launch.return_value.close.return_value = True
                peer = Mock()
                host = PublicAnswerHost(peer, PARENT, TURN, item_reader=lambda: open_reader(home/"codex.exe", home))
                with self.assertRaises(AnswerError) as error:
                    host.bind_reviewers((A,B), creation_context=True)
                self.assertEqual(error.exception.failure["reason"], "identity")
                peer._json_call.assert_not_called()
                launch.return_value.close.assert_called_once()

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
                    rpc.rows.insert(0, legacy_wait(senderThreadId=fixture.parent))
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
                                      for r in fixture.selection.service().repository.read()], [37,37], body)
                    if state == "running":
                        self.assertEqual(body.get("status"), "waiting", body)
                        self.assertEqual(fixture.flow.effects, ["create","arm"])
                    else:
                        self.assertFalse(body["ok"], body)
                        self.assertEqual(fixture.flow.effects, [])
                finally:
                    fixture.doCleanups()
