"""Exact-version selection and transport gates, without real host effects."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from tools import review_wait_host as host
from task_governance_tool.review_wait_runtime import host_server_selection as selection
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_service import ServiceConfig
from tests.test_review_wait_host import META, CHILD, AUTOMATION
from task_governance_tool.review_answer_read_host import AnswerReadHost
from task_governance_tool.review_wait_runtime.managed_host import ManagedHost


class HostSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.repo = self.root / "project"
        self.repo.mkdir()
        self.mode = selection.AutoServer(self.repo)
        # A synthetic Codex home must never consult the developer's real
        # ancestor configuration. Exercise a completely owned two-layer tree.
        layers = mock.patch.object(selection, "_configuration_paths", return_value=(
            self.repo / ".codex/config.toml", self.root / ".codex/config.toml"))
        layers.start()
        self.addCleanup(layers.stop)
        self.source_root = self.home / ".tmp/bundled-marketplaces/openai-bundled"
        self.source = self.source_root / "plugins/codex-app-tools"
        self.cache = self.home / "plugins/cache/openai-bundled/codex-app-tools"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(
            '[plugins."codex-app-tools@openai-bundled"]\nenabled=true\n'
            '[marketplaces.openai-bundled]\nsource_type="local"\n'
            f'source={json.dumps(str(self.source_root))}\n', encoding="utf-8")
        self.catalog = self.source_root / ".agents/plugins/marketplace.json"
        self.write(self.catalog, {"name": "openai-bundled", "plugins": [{"name": "codex-app-tools",
            "source": {"source": "local", "path": "./plugins/codex-app-tools"}}]})
        self.install("1.0.0")

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def install(self, version, body="// fixture"):
        package = self.cache / version
        for root in (self.source, package):
            self.write(root / ".codex-plugin/plugin.json", {"name": "codex-app-tools",
                "version": version, "mcpServers": "./.mcp.json"})
            self.write(root / ".mcp.json", {"mcpServers": {"codex_app": {}}})
            (root / "server.mjs").write_text(body, encoding="utf-8")
        return package / "server.mjs"

    def selected(self):
        return selection.select_server(self.mode, self.home)

    def test_exact_version_changes_without_selecting_newest_cache(self):
        initial = self.selected()
        self.install("999.0.0")
        self.install("1.0.0")
        self.assertEqual(initial, self.selected())
        self.install("2.0.0")
        self.assertEqual(self.cache / "2.0.0/server.mjs", self.selected().path)
        with self.assertRaises(selection.SelectionError):
            selection.validate_selection(self.mode, self.home, initial)

    def test_missing_exact_version_does_not_fall_back(self):
        self.install("2.0.0").rename(self.cache / "2.0.0/retired.mjs")
        with self.assertRaises(selection.SelectionError):
            self.selected()

    def test_source_and_cache_must_match(self):
        for relative in (".codex-plugin/plugin.json", ".mcp.json", "server.mjs"):
            with self.subTest(relative=relative):
                path = self.cache / "1.0.0" / relative
                old = path.read_bytes()
                path.write_bytes(b'{}')
                with self.assertRaises(selection.SelectionError):
                    self.selected()
                path.write_bytes(old)

    def test_malformed_mcp_definition_shapes_are_rejected(self):
        path = self.source / ".mcp.json"
        for value in (None, [], {"mcpServers": "codex_app"}, {"mcpServers": ["codex_app"]},
                      {"mcpServers": {"codex_app": None}}, {"mcpServers": {"codex_app": []}}):
            with self.subTest(value=value):
                self.write(path, value)
                with self.assertRaises(selection.SelectionError):
                    self.selected()

    def test_catalog_identity_duplicate_and_escape_rejected(self):
        original = json.loads(self.catalog.read_text())
        values = [dict(original, name="other"), dict(original, plugins=original["plugins"] * 2),
                  {"name": "openai-bundled", "plugins": [{"name": "codex-app-tools", "source": {
                      "source": "local", "path": "../other"}}]}]
        for value in values:
            with self.subTest(value=value):
                self.write(self.catalog, value)
                with self.assertRaises(selection.SelectionError):
                    self.selected()

    def test_invalid_metadata_and_size_rejected_without_payload_disclosure(self):
        path = self.source / ".codex-plugin/plugin.json"
        for body in ('{"name":"codex-app-tools","name":"PRIVATE_SENTINEL"}', '{broken',
                     'x' * (selection.MAX_METADATA_BYTES + 1)):
            path.write_text(body, encoding="utf-8")
            with self.assertRaises(selection.SelectionError) as caught:
                self.selected()
            self.assertEqual("host_server_selection_unavailable", str(caught.exception))

    def test_nonfinite_metadata_is_rejected_even_in_unused_fields(self):
        path = self.source / ".codex-plugin/plugin.json"
        original = path.read_text()
        for token in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(token=token):
                path.write_text(original[:-1] + ',"unused":' + token + '}', encoding="utf-8")
                with self.assertRaises(selection.SelectionError):
                    self.selected()

    def test_disabled_plugin_profile_and_project_overrides_rejected(self):
        original = self.config.read_text()
        for text in (original.replace("enabled=true", "enabled=false"), original +
                     '\n[profiles.example.plugins."codex-app-tools@openai-bundled"]\nenabled=true\n'):
            self.config.write_text(text, encoding="utf-8")
            with self.assertRaises(selection.SelectionError):
                self.selected()
        self.config.write_text(original, encoding="utf-8")
        local = self.repo / ".codex/config.toml"
        local.parent.mkdir()
        local.write_text('[plugins."codex-app-tools@openai-bundled"]\nenabled=false\n', encoding="utf-8")
        with self.assertRaises(selection.SelectionError):
            self.selected()
        local.write_text('[mcp_servers.taskgov_review_wait]\nenabled=true\n', encoding="utf-8")
        self.assertEqual("1.0.0", self.selected().version)

    def test_added_project_config_invalidates_prior_selection(self):
        prior = self.selected()
        local = self.repo / ".codex/config.toml"
        local.parent.mkdir()
        local.write_text("# concurrent edit", encoding="utf-8")
        with self.assertRaises(selection.SelectionError):
            selection.validate_selection(self.mode, self.home, prior)

    def test_enable_only_project_layer_allowed_but_transport_override_rejected(self):
        local = self.repo / ".codex/config.toml"
        local.parent.mkdir()
        prefix = '[plugins."codex-app-tools@openai-bundled".mcp_servers.codex_app]\n'
        local.write_text(prefix + 'enabled=true\n', encoding="utf-8")
        self.assertEqual("1.0.0", self.selected().version)
        for tail in ('enabled=false\n', 'enabled=1\n', 'enabled=true\ncommand="PRIVATE_SENTINEL"\n',
                     'enabled=true\n[plugins."codex-app-tools@openai-bundled".mcp_servers.codex_app.env]\nX="value"\n'):
            with self.subTest(tail=tail):
                local.write_text(prefix + tail, encoding="utf-8")
                with self.assertRaises(selection.SelectionError):
                    self.selected()

    def test_user_server_disable_and_transport_override_are_rejected(self):
        original = self.config.read_text()
        prefix = '\n[plugins."codex-app-tools@openai-bundled".mcp_servers.codex_app]\n'
        for text in ('enabled=false\n', 'enabled=true\ncommand="PRIVATE_SENTINEL"\n'):
            self.config.write_text(original + prefix + text, encoding="utf-8")
            with self.assertRaises(selection.SelectionError):
                self.selected()
        self.config.write_text(original + prefix + 'enabled=true\n', encoding="utf-8")
        self.assertEqual("1.0.0", self.selected().version)

    def test_unknown_source_is_not_inspected(self):
        self.config.write_text(self.config.read_text().replace(str(self.source_root).replace("\\", "\\\\"),
                               "//untrusted/share"), encoding="utf-8")
        with mock.patch.object(selection, "_physical", wraps=selection._physical) as physical:
            with self.assertRaises(selection.SelectionError):
                self.selected()
        self.assertFalse(any("untrusted" in str(call.args) for call in physical.call_args_list))

    def test_link_or_reparse_point_is_rejected(self):
        actual = Path.lstat
        def lstat(path):
            if path == self.source:
                result = mock.Mock(st_mode=0o040755, st_file_attributes=0x400)
                return result
            return actual(path)
        with mock.patch.object(Path, "lstat", lstat):
            with self.assertRaises(selection.SelectionError):
                self.selected()

    def test_project_and_service_config_accept_auto_but_not_relative_path(self):
        config = ProjectConfig(self.repo, self.mode, self.home, "UTC")
        session = ProjectReviewWaitSession(config)
        self.assertIs(session.config.server_path, self.mode)
        session.close()
        self.assertIs(ServiceConfig(self.repo / "wait.sqlite", self.mode, self.home, "UTC", "host_node_intl").server_path,
                      self.mode)
        with self.assertRaises(ValueError):
            ProjectReviewWaitSession(replace(config, server_path=Path("relative.mjs")))

    def adapter(self):
        with mock.patch.dict(os.environ, {"CODEX_MCP_NODE_PATH": sys.executable,
                                          "CODEX_APP_TOOLS_PIPE_PATH": "fixture"}):
            return host.PublicMcpHost.from_environment(server_path=self.mode, metadata=META,
                automation_id=AUTOMATION, codex_home=self.home, confirmed_timezone="UTC",
                timezone_source="host_node_intl", reviewer_ids=[CHILD])

    def child(self, *, change=None, compatible=True, name="automation_update"):
        child = mock.Mock()
        child.close.return_value = True
        child.close_response_reason = None
        def result(request_id, **_kwargs):
            if request_id == 1:
                return {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                        "serverInfo": {"name": "codex-app-tools", "version": "0.1.0"}}
            if request_id == 3:
                if change:
                    change()
                return {"tools": [{"name": name, "inputSchema": {"type": "object"}}] if compatible else []}
            return {"content": [{"type": "text", "text": "{}"}], "isError": False}
        child.result.side_effect = result
        return child

    def test_existing_host_reselects_for_each_exchange_and_does_not_replay(self):
        adapter = self.adapter()
        child = self.child()
        with mock.patch.object(host.relay, "_ChildSession", return_value=child) as launch:
            adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})
            self.assertEqual(str(self.cache / "1.0.0/server.mjs"), launch.call_args.args[0][1])
            self.install("2.0.0")
            adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})
            self.assertEqual(str(self.cache / "2.0.0/server.mjs"), launch.call_args.args[0][1])
        requests = [json.loads(c.args[0]) for c in child.send.call_args_list]
        self.assertEqual(2, sum(r["method"] == "tools/call" for r in requests))
        self.assertEqual(2, child.close.call_count)
        child.result.assert_any_call(2, max_bytes=host.relay.MAX_MESSAGE_BYTES)

    def test_update_during_handshake_prevents_tool_dispatch(self):
        adapter = self.adapter()
        child = self.child(change=lambda: self.install("2.0.0"))
        with mock.patch.object(host.relay, "_ChildSession", return_value=child):
            with self.assertRaises(host.HostAdapterError) as error:
                adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})
        self.assertEqual("host_server_selection_changed", error.exception.boundary_reason)
        self.assertEqual("confirmed", error.exception.termination)
        self.assertFalse(any(json.loads(c.args[0])["method"] == "tools/call" for c in child.send.call_args_list))

    def test_missing_tool_prevents_dispatch_and_cleans_up(self):
        adapter = self.adapter()
        child = self.child(compatible=False)
        with mock.patch.object(host.relay, "_ChildSession", return_value=child):
            with self.assertRaises(host.HostAdapterError) as error:
                adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})
        self.assertEqual("host_server_incompatible", error.exception.boundary_reason)
        self.assertEqual(1, child.close.call_count)
        self.assertFalse(any(json.loads(c.args[0])["method"] == "tools/call" for c in child.send.call_args_list))

    def test_missing_selection_does_not_launch_child(self):
        adapter = self.adapter()
        self.config.write_text("", encoding="utf-8")
        with mock.patch.object(host.relay, "_ChildSession") as launch:
            with self.assertRaises(host.HostAdapterError) as error:
                adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})
        self.assertEqual("host_server_selection_unavailable", error.exception.boundary_reason)
        self.assertEqual("not_attempted", error.exception.termination)
        launch.assert_not_called()

    def test_answer_self_reader_and_managed_host_share_auto_selection(self):
        admitted = mock.Mock()
        common = dict(server_path=self.mode, metadata=META, automation_id=AUTOMATION,
            codex_home=self.home, confirmed_timezone="UTC", timezone_source="host_node_intl")
        with mock.patch.dict(os.environ, {"CODEX_MCP_NODE_PATH": sys.executable,
                                          "CODEX_APP_TOOLS_PIPE_PATH": "fixture"}):
            reader = AnswerReadHost.from_environment(**common, reviewer_id=META["threadId"],
                reviewer_turn_id=META["turnId"], admit=admitted)
            managed = ManagedHost.from_environment(**common, task_id="tg_task_1234567890abcdef", reviewer_ids=[CHILD])
        self.assertEqual(self.mode, managed._auto_server)
        child = self.child(name="read_thread")
        with mock.patch.object(host.relay, "_ChildSession", return_value=child):
            reader._request("read_thread", dict(reader._read_arguments))
        self.assertEqual(2, admitted.call_count)
        requests = [json.loads(c.args[0]) for c in child.send.call_args_list]
        calls = [r for r in requests if r["method"] == "tools/call"]
        self.assertEqual(1, len(calls))
        self.assertEqual(META["threadId"], calls[0]["params"]["_meta"]["threadId"])

    def test_failed_dispatched_exchange_never_retries_or_falls_back(self):
        adapter = self.adapter()
        child = self.child()
        ordinary = child.result.side_effect
        def fail_after_dispatch(request_id, **kwargs):
            if request_id == 2:
                raise host.relay.RelayError(response_reason="response_eof")
            return ordinary(request_id, **kwargs)
        child.result.side_effect = fail_after_dispatch
        with mock.patch.object(host.relay, "_ChildSession", return_value=child) as launch:
            with self.assertRaises(host.HostAdapterError) as error:
                adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})
        self.assertEqual("host_response_eof", error.exception.boundary_reason)
        self.assertEqual(1, launch.call_count)
        self.assertEqual(1, sum(json.loads(c.args[0])["method"] == "tools/call" for c in child.send.call_args_list))

    def test_discovery_transport_failure_keeps_original_fixed_reason(self):
        adapter = self.adapter()
        for reason in ("response_frame_limit", "response_drain_limit", "response_queue_limit",
                       "response_eof", "response_timeout", "response_json_invalid"):
            with self.subTest(reason=reason):
                child = self.child()
                ordinary = child.result.side_effect
                def fail_discovery(request_id, **kwargs):
                    if request_id == 3:
                        raise host.relay.RelayError(response_reason=reason)
                    return ordinary(request_id, **kwargs)
                child.result.side_effect = fail_discovery
                with mock.patch.object(host.relay, "_ChildSession", return_value=child) as launch:
                    with self.assertRaises(host.HostAdapterError) as error:
                        adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})
                self.assertEqual("host_" + reason, error.exception.boundary_reason)
                self.assertEqual("confirmed", error.exception.termination)
                self.assertEqual(1, launch.call_count)
                self.assertFalse(any(json.loads(c.args[0])["method"] == "tools/call" for c in child.send.call_args_list))

    def test_real_auto_transport_allows_large_discovery_but_bounds_mutation_reply(self):
        script = '''import json,sys
for line in sys.stdin:
 r=json.loads(line)
 if r['method']=='initialize':
  result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'codex-app-tools','version':'0.1.0'}}
 elif r['method']=='tools/list':
  result={'tools':[{'name':'automation_update','inputSchema':{'type':'object'},'description':'x'*300000}]}
 elif r['method']=='tools/call':
  result={'isError':False,'content':[{'type':'text','text':'x'*REPLY_SIZE}]}
 else: continue
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
'''
        for size in (2, 300000):
            with self.subTest(size=size):
                self.install("1.0.0", script.replace("REPLY_SIZE", str(size)))
                adapter = self.adapter()
                if size == 2:
                    self.assertFalse(adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})["isError"])
                else:
                    with self.assertRaises(host.HostAdapterError) as error:
                        adapter._request("automation_update", {"mode": "view", "id": AUTOMATION})
                    self.assertEqual("host_response_frame_limit", error.exception.boundary_reason)
