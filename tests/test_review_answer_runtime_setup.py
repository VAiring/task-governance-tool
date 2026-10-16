"""Collection lifecycle glue and separate explicit Setup feature policy."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection import source
from task_governance_tool.review_answer_runtime import bind_initial, collect_bound, collect_hook_event
from task_governance_tool.review_answer_hook_setup import merge_definition, setup_answer_hook, prepare_tool_allowlist, MARKER, TOOL
from task_governance_tool import setup_feature_config as config
from task_governance_tool.setup_features import _answer_policy, setup_features, feature_notice
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_server import _call
from test_review_answer_pure_values import metadata_fixture, record_fixture


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.metadata, _ = metadata_fixture()
        self.child = self.metadata["binding"]["reviewer_session_id"]
        self.service, self.host = Mock(), Mock()
        self.record = record_fixture()
        self.service.repository.read.side_effect = lambda: (copy.deepcopy(self.record),)
        def bind(slot, binding):
            self.record["binding"] = copy.deepcopy(binding)
            self.record["attempts"][0]["turn_id"] = binding["initial_turn_id"]
        self.service.bind.side_effect = bind
        self.host.bind_reviewers.return_value = (dict(dispatch_id="dispatch-1", parent_session_id=self.metadata["binding"]["parent_session_id"],
            reviewer_session_id=self.child, initial_turn_id=self.metadata["binding"]["initial_turn_id"], source=source("dispatch_binding")),)

    def test_fresh_binding_defaults_are_assignment_only_and_restart_reuses(self):
        bind_initial(self.service, self.host, (self.child,), creation_context=True)
        binding = self.record["binding"]
        self.assertEqual(binding["reviewer_key"], self.child)
        self.assertEqual(binding["assignment"], dict(kind="independent", review_profiles=[], review_lenses=[],
            requested_model=None, requested_effort=None, requested_skill=None, context_relation=None))
        bind_initial(self.service, self.host, (self.child,), creation_context=False)
        self.host.bind_reviewers.assert_called_once()
        self.service.bind.assert_called_once()

    def test_lost_creation_context_and_wrong_saved_child_never_bind(self):
        with self.assertRaises(AnswerError):
            bind_initial(self.service, self.host, (self.child,))
        self.assertFalse(self.host.bind_reviewers.called)
        bind_initial(self.service, self.host, (self.child,), creation_context=True)
        with self.assertRaises(AnswerError):
            bind_initial(self.service, self.host, ("40000000-0000-4000-8000-000000000001",))
        self.assertEqual(self.service.bind.call_count, 1)

    def test_collection_selection_is_exact_and_failures_are_sanitized(self):
        bind_initial(self.service, self.host, (self.child,), creation_context=True)
        self.service.recover.side_effect = OSError("private value is discarded")
        result = collect_bound(self.service, self.host, (self.child,))
        self.assertEqual(result, dict(collected=[], unavailable=[dict(slot=1,
            failure={"class": "collection_unavailable", "field": None, "reason": "incomplete"})]))
        self.service.recover.assert_called_once_with(1, self.host, explicit_recovery=False)
        with self.assertRaises(AnswerError):
            collect_bound(self.service, self.host, ("40000000-0000-4000-8000-000000000001",))
        self.assertEqual(self.service.recover.call_count, 1)

    def test_hook_argument_is_closed_identity_only(self):
        bind_initial(self.service, self.host, (self.child,), creation_context=True)
        event = dict(hook_event_name="SubagentStop", session_id=self.metadata["binding"]["parent_session_id"],
            agent_id=self.child, turn_id="40000000-0000-4000-8000-000000000001", stop_hook_active=False)
        self.assertEqual(collect_hook_event(event, service=self.service, host=self.host), {})
        event["last_assistant_message"] = "not forwarded"
        with self.assertRaises(AnswerError):
            collect_hook_event(event, service=self.service, host=self.host)


class HookSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skill = self.root / "skill"
        self.skill.mkdir()

    def test_definition_forwards_only_documented_typed_hints(self):
        original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "unrelated"}]}]}}
        merged = merge_definition(original)
        self.assertEqual(merged["hooks"]["Stop"], original["hooks"]["Stop"])
        handler = merged["hooks"]["SubagentStop"][0]["hooks"][0]
        self.assertEqual(handler["input"], {"event": {key: "${" + key + "}" for key in
            ("hook_event_name", "session_id", "agent_id", "turn_id", "stop_hook_active")}})
        self.assertEqual(merge_definition(merged), merged)
        self.assertNotIn("SubagentStop", original["hooks"])
        handler["input"] = {}
        with self.assertRaises(ValueError):
            merge_definition(merged)

    def test_absent_policy_disabled_and_readonly_creates_nothing(self):
        self.assertFalse(config.answer_collection_allowed(self.skill))
        self.assertFalse(config.answer_correction_allowed(self.skill))
        self.assertEqual(setup_answer_hook(self.root, enabled=True, read_only=True), ("off", True))
        self.assertFalse((self.root / ".codex").exists())
        self.assertEqual(_answer_policy(self.root, self.skill, "review_answer_collection", True, True), (False, False, True))
        self.assertFalse((self.skill / "config").exists())

    def test_selection_is_independent_from_usage_and_correction(self):
        _answer_policy(self.root, self.skill, "review_answer_correction", True, False)
        self.assertFalse(config.answer_correction_allowed(self.skill))
        _answer_policy(self.root, self.skill, "review_answer_collection", True, False)
        self.assertTrue(config.answer_collection_allowed(self.skill))
        self.assertTrue(config.answer_correction_allowed(self.skill))
        hooks = (self.root / ".codex/hooks.json").read_bytes()
        document = json.loads(hooks)
        self.assertEqual(set(document["hooks"]), {"SubagentStop"})
        self.assertEqual(document["hooks"]["SubagentStop"][0]["hooks"][0]["statusMessage"], MARKER)
        _answer_policy(self.root, self.skill, "review_answer_collection", False, False)
        self.assertFalse(config.answer_collection_allowed(self.skill))
        self.assertFalse(config.answer_correction_allowed(self.skill))
        self.assertEqual((self.root / ".codex/hooks.json").read_bytes(), hooks)

    def test_malformed_saved_choice_and_owned_inline_hook_fail_closed(self):
        directory = self.skill / "config"
        directory.mkdir()
        (directory / "setup-features.json").write_text('{"schema_version":1,"choices":{"review_answer_collection":"true"}}')
        self.assertFalse(config.answer_collection_allowed(self.skill))
        codex = self.root / ".codex"
        codex.mkdir()
        (codex / "config.toml").write_text('[hooks]\nstatusMessage="' + MARKER + '"\n')
        with self.assertRaises(ValueError):
            setup_answer_hook(self.root, enabled=True, read_only=False)
        self.assertFalse((codex / "hooks.json").exists())

    def test_existing_allowlist_preview_and_apply_change_only_one_server_tool(self):
        codex = self.root / ".codex"
        codex.mkdir()
        original = (b'# preserve header\r\n[mcp_servers.other]\r\ncommand = "other"\r\n'
            b'[mcp_servers.taskgov_review_wait]\r\ncommand = "python"\r\n'
            b'args = ["unchanged.py"]\r\nenabled_tools = [\r\n "review_wait_wait", # keep operation\r\n]\r\n'
            b'default_tools_approval_mode = "prompt"\r\n[mcp_servers.taskgov_review_wait.env]\r\nTEST = "unchanged"\r\n')
        path = codex / "config.toml"
        path.write_bytes(original)
        diagnostic = {}
        self.assertEqual(setup_answer_hook(self.root, enabled=True, read_only=True, diagnostic=diagnostic), ("off", True))
        self.assertEqual(diagnostic, dict(status="preparation_required",
            planned_writes=["review_answer_hook_prepare", "review_answer_tool_allowlist_prepare"],
            completed_writes=[], connection="configured", trust="unknown", error=None))
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((codex / "hooks.json").exists())
        self.assertEqual(setup_answer_hook(self.root, enabled=True, read_only=False, diagnostic=diagnostic), ("on", True))
        expected = tomllib.loads(original.decode())
        expected["mcp_servers"]["taskgov_review_wait"]["enabled_tools"].append(TOOL)
        self.assertEqual(tomllib.loads(path.read_text()), expected)
        self.assertEqual(diagnostic["planned_writes"], diagnostic["completed_writes"])
        applied, hooks = path.read_bytes(), (codex / "hooks.json").read_bytes()
        self.assertTrue(applied.startswith(b'# preserve header\r\n[mcp_servers.other]'))
        self.assertTrue(applied.endswith(b'[mcp_servers.taskgov_review_wait.env]\r\nTEST = "unchanged"\r\n'))
        self.assertEqual(setup_answer_hook(self.root, enabled=True, read_only=False, diagnostic=diagnostic), ("on", False))
        self.assertEqual(path.read_bytes(), applied)
        self.assertEqual((codex / "hooks.json").read_bytes(), hooks)
        self.assertEqual(diagnostic["status"], "current")

    def test_fresh_setup_reports_missing_connection_without_creating_server(self):
        diagnostic = {}
        result = setup_answer_hook(self.root, enabled=True, read_only=False, diagnostic=diagnostic)
        self.assertEqual(result, ("off", True))
        self.assertEqual(diagnostic["status"], "connection_required")
        self.assertEqual(diagnostic["connection"], "missing")
        self.assertEqual(diagnostic["completed_writes"], ["review_answer_hook_prepare"])
        self.assertFalse((self.root / ".codex/config.toml").exists())

    def test_feature_output_exposes_preview_plan_and_missing_connection(self):
        inspection = SimpleNamespace(scope=SimpleNamespace(skill_root=self.skill, canonical_repo=self.root))
        core = SimpleNamespace(ok=True, data={"usage": {"status": "not_present"}})
        result, _ = setup_features(inspection, core,
            selections={"review_answer_collection": True, "usage_collection": False}, read_only=True)
        diagnostic = result["answer_collection_setup"]
        self.assertEqual(diagnostic, dict(status="connection_required", planned_writes=["review_answer_hook_prepare"],
            completed_writes=[], connection="missing", trust="unknown", error=None))
        self.assertIn("connection configuration", feature_notice(result))
        self.assertFalse((self.root / ".codex").exists())
        self.assertFalse((self.skill / "config").exists())

    def test_unrestricted_server_needs_no_allowlist_and_disabled_server_stays_disabled(self):
        raw = b'[mcp_servers.taskgov_review_wait]\ncommand="python"\n'
        self.assertEqual(prepare_tool_allowlist(raw), (None, "configured"))
        self.assertEqual(prepare_tool_allowlist(raw + b'enabled=false\n'), (None, "disabled"))
        self.assertEqual(prepare_tool_allowlist(b'[mcp_servers.unrelated]\ncommand="x"\n'), (None, "missing"))

    def test_conflicting_or_unrecognized_config_never_publishes_hook(self):
        prefix = b'[mcp_servers.taskgov_review_wait]\ncommand="python"\n'
        cases = [prefix + b'enabled_tools="not-list"\n',
                 prefix + b'disabled_tools=["review_answer_collect"]\n',
                 prefix + b'enabled_tools=["duplicate", "duplicate"]\n',
                 b'[mcp_servers."taskgov_review_wait"]\ncommand="python"\nenabled_tools=[]\n']
        codex = self.root / ".codex"
        codex.mkdir()
        path = codex / "config.toml"
        for raw in cases:
            path.write_bytes(raw)
            diagnostic = {}
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                setup_answer_hook(self.root, enabled=True, read_only=False, diagnostic=diagnostic)
            self.assertEqual(path.read_bytes(), raw)
            self.assertFalse((codex / "hooks.json").exists())
            self.assertEqual(diagnostic["status"], "unavailable")
            self.assertEqual(diagnostic["completed_writes"], [])

    def test_allowlist_scanner_handles_quoted_brackets_and_preserves_bom(self):
        raw = b'\xef\xbb\xbf[mcp_servers.taskgov_review_wait]\ncommand="python"\nenabled_tools=["a]b", \'c[d\'] # trailing\n'
        changed, connection = prepare_tool_allowlist(raw)
        self.assertEqual(connection, "configured")
        self.assertTrue(changed.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(changed.endswith(b' # trailing\n'))
        self.assertEqual(tomllib.loads(changed.decode("utf-8-sig"))["mcp_servers"]["taskgov_review_wait"]["enabled_tools"], ["a]b", "c[d", TOOL])


class HookWireTests(unittest.TestCase):
    def test_exact_hook_name_and_neutral_output_are_not_wait_alias(self):
        service = Mock()
        service.operations = ProjectReviewWaitSession.operations
        service.tool_operations = ProjectReviewWaitSession.tool_operations
        service.argument_shapes = ProjectReviewWaitSession.argument_shapes
        service.handle.return_value = {"ok": True, "status": "collected"}
        value = _call(dict(name="review_answer_collect", arguments={"event": {}}, _meta={}), service)
        self.assertEqual(value, {"content": [{"type": "text", "text": "{}"}], "isError": False})
        service.handle.assert_called_once_with("answer_collect", {"event": {}}, {})
        from task_governance_tool.review_wait_runtime.review_wait_mcp_relay import RelayError
        with self.assertRaises(RelayError):
            _call(dict(name="review_wait_answer_collect", arguments={"event": {}}, _meta={}), service)
        with self.assertRaises(RelayError):
            _call(dict(name="review_answer_collect", arguments={"event": {}, "task_id": "ignored"}, _meta={}), service)
        service.handle.return_value = {"ok": True, "collected": [1], "unavailable": []}
        value = _call(dict(name="review_answer_collect", arguments={"task_id": "task", "reviewer_ids": []}, _meta={}), service)
        self.assertEqual(json.loads(value["content"][0]["text"]), service.handle.return_value)

    def test_disabled_hook_is_inert_without_wait_policy_or_project_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = ProjectReviewWaitSession(ProjectConfig(root, root / "server.mjs", root, "UTC"))
            session.skill = root
            session._location = Mock(side_effect=AssertionError("must not probe"))
            session._enabled = Mock(side_effect=AssertionError("wait policy is separate"))
            metadata, _ = metadata_fixture()
            result = session.handle("answer_collect", {"event": {}}, {"threadId": metadata["binding"]["parent_session_id"]})
            self.assertEqual(result, {"ok": True, "status": "disabled"})
            self.assertFalse(session._location.called)
            self.assertFalse(session._enabled.called)


class HookCallerDiagnosticTests(unittest.TestCase):
    """Offline boundary checks; event hints never authorize a host call."""

    def setUp(self):
        metadata, _ = metadata_fixture()
        self.parent = metadata["binding"]["parent_session_id"]
        self.child = metadata["binding"]["reviewer_session_id"]
        self.turn = metadata["binding"]["initial_turn_id"]
        self.other = "90000000-0000-4000-8000-000000000001"
        self.event = dict(hook_event_name="SubagentStop", session_id=self.parent,
            agent_id=self.child, turn_id=self.turn, stop_hook_active=False)
        root = Path.cwd()
        self.host_factory = Mock()
        self.answer_read_host_factory = Mock()
        self.session = ProjectReviewWaitSession(ProjectConfig(root, root / "offline-server.mjs", root, "UTC"),
            managed_host_factory=self.host_factory, answer_read_host_factory=self.answer_read_host_factory)
        self.session._location = Mock()
        self.service_for_event = self.enterContext(patch(
            "task_governance_tool.review_answer_runtime.service_for_event"))
        record = record_fixture()
        record["binding"] = metadata["binding"]
        record["attempts"][0]["turn_id"] = self.turn
        self.service = Mock()
        self.service.repository.read.side_effect = lambda: (copy.deepcopy(record),)
        self.service_for_event.return_value = (self.service,
            SimpleNamespace(task_id="tg_task_0000000000000001", parent_thread_id=self.parent))
        self.collect_hook_event = self.enterContext(patch(
            "task_governance_tool.review_answer_runtime.collect_hook_event"))
        self.allowed = self.enterContext(patch.object(config, "answer_collection_allowed", return_value=True))

    def call(self, metadata, event=None):
        return _call(dict(name="review_answer_collect", arguments={"event": self.event if event is None else event},
            _meta=metadata), self.session)

    def test_wrong_reviewer_turn_and_other_stay_rejected_before_host_access(self):
        for caller, relation in ((self.child, "event_reviewer"), (self.other, "other")):
            with self.subTest(relation=relation):
                self.service_for_event.reset_mock()
                wire = self.call(dict(threadId=caller, turnId=self.other))
                self.assertTrue(wire["isError"])
                result = json.loads(wire["content"][0]["text"])
                self.assertEqual(result, {"ok": False, "error": "caller_mismatch", "diagnostic": {
                    "version": 1, "stage": "operation", "reason": "caller_mismatch",
                    "local_state": "not_attempted", "host_mutation": "not_dispatched",
                    "retained_effects": "not_inspected", "cleanup": "not_attempted",
                    "recovery": "report_and_resume", "turn_end": "report_limitation",
                    "hook_caller_relation": relation}})
                for identifier in (self.parent, self.child, self.turn, self.other):
                    self.assertNotIn(identifier, wire["content"][0]["text"])
                if caller == self.child:
                    self.service_for_event.assert_called_once_with(
                        self.session.config.repo, self.session.skill, self.parent, self.child)
                else:
                    self.service_for_event.assert_not_called()
        self.host_factory.assert_not_called()
        self.answer_read_host_factory.assert_not_called()
        self.collect_hook_event.assert_not_called()

    def test_missing_or_conflicting_executor_metadata_has_no_hint_comparison(self):
        for metadata in ({}, {"threadId": self.child, "openai/threadId": self.parent}):
            with self.subTest(metadata_keys=tuple(metadata)):
                result = json.loads(self.call(metadata)["content"][0]["text"])
                self.assertEqual(result["error"], "review_wait_unavailable")
                self.assertEqual(result["diagnostic"]["stage"], "executor_admission")
                self.assertNotIn("hook_caller_relation", result["diagnostic"])
        self.session._location.assert_not_called()
        self.service_for_event.assert_not_called()
        self.host_factory.assert_not_called()

    def test_invalid_event_has_no_comparison_or_collection(self):
        for change in ({"session_id": "invalid"}, {"agent_id": self.parent}, {"stop_hook_active": "false"}):
            with self.subTest(changed_fields=tuple(change)):
                result = json.loads(self.call({"threadId": self.child}, {**self.event, **change})["content"][0]["text"])
                self.assertNotIn("hook_caller_relation", result["diagnostic"])
                self.assertNotEqual(result["error"], "caller_mismatch")
        self.service_for_event.assert_not_called()
        self.host_factory.assert_not_called()

    def test_successful_parent_hook_keeps_neutral_wire_result_and_metadata(self):
        metadata = dict(threadId=self.parent, turnId=self.turn)
        binding = SimpleNamespace(task_id="tg_task_0000000000000001", parent_thread_id=self.parent)
        service = Mock()
        self.service_for_event.return_value = (service, binding)
        wire = self.call(metadata)
        self.assertEqual(wire, {"content": [{"type": "text", "text": "{}"}], "isError": False})
        self.assertEqual(self.host_factory.call_args.kwargs["metadata"], metadata)
        self.collect_hook_event.assert_called_once()
        self.assertIs(self.collect_hook_event.call_args.kwargs["service"], service)

    def test_comparison_is_not_reused_by_disabled_hook_or_parent_arm(self):
        first = json.loads(self.call({"threadId": self.child, "turnId": self.other})["content"][0]["text"])
        self.assertEqual(first["diagnostic"]["hook_caller_relation"], "event_reviewer")
        self.service_for_event.assert_called_once_with(
            self.session.config.repo, self.session.skill, self.parent, self.child)
        self.service_for_event.reset_mock()
        self.allowed.return_value = False
        self.assertEqual(self.call({"threadId": self.child}),
            {"content": [{"type": "text", "text": "{}"}], "isError": False})
        self.service_for_event.assert_not_called()
        with patch.object(self.session, "_collect_parent", return_value={"ok": False, "error": "caller_mismatch"}):
            result = self.session.handle("answer_collect", {"task_id": "task", "reviewer_ids": [self.child]},
                {"threadId": self.parent})
        self.assertNotIn("hook_caller_relation", result["diagnostic"])
        self.service_for_event.assert_not_called()
        self.host_factory.assert_not_called()
        self.answer_read_host_factory.assert_not_called()
        self.collect_hook_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
