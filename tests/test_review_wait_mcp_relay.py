"""Offline protocol and lifecycle checks; never connect to Desktop."""

from __future__ import annotations

import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tools import review_wait_mcp_relay as relay


META = {"threadId": "fixture-child", "turnId": "fixture-turn", "nested": {"keep": [1, True]}}
RESERVATION = "fixture-paused-reservation"
SECRET = "PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE"

# A standalone fake MCP peer is necessary to exercise real pipes, EOF, deadlines
# and reaping. It has no access to Desktop and never launches further children.
PEER = r'''
import json, sys, time
mode = sys.argv[1]
def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["method"] == "initialize":
        if mode == "init_timeout":
            time.sleep(60)
        if mode == "init_error":
            emit({"jsonrpc":"2.0", "id":request["id"], "error":{"code":-1,"message":"PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE"}})
            continue
        emit({"jsonrpc":"2.0", "id":request["id"], "result":{
            "protocolVersion": "invalid" if mode == "bad_version" else request["params"]["protocolVersion"],
            "capabilities":{"tools":{}}, "serverInfo":{} if mode == "bad_server_info" else {"name":"fake","version":"1"}}})
    elif request["method"] == "notifications/initialized":
        continue
    elif request["method"] == "tools/call":
        expected = {"name":"automation_update", "arguments":{"mode":"view","id":"fixture-paused-reservation"},
                    "_meta":{"threadId":"fixture-child","turnId":"fixture-turn","nested":{"keep":[1,True]}}}
        if request["params"] != expected:
            emit({"jsonrpc":"2.0", "id":request["id"], "error":{"code":-1,"message":"unexpected forwarding"}})
            continue
        if mode == "timeout":
            time.sleep(60)
        if mode == "eof":
            sys.exit(0)
        if mode == "malformed":
            print("PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE", flush=True)
            continue
        if mode == "oversize":
            print("x" * 300000, flush=True)
            continue
        if mode == "server_request":
            emit({"jsonrpc":"2.0", "id":"challenge", "method":"elicitation/create", "params":{"message":"PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE"}})
            continue
        if mode == "stderr":
            sys.stderr.write("PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE\n" * 500)
            sys.stderr.flush()
        if mode == "rpc_error":
            emit({"jsonrpc":"2.0", "id":request["id"], "error":{"code":-1,"message":"PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE"}})
            continue
        result = {"content":[{"type":"text","text":"Rendered automation card in the app."}], "isError":False}
        if mode == "tool_error":
            result = {"content":[{"type":"text","text":"PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE"}], "isError":True}
        if mode == "missing_success":
            del result["isError"]
        if mode == "wrong_success_type":
            result["isError"] = 0
        if mode == "bad_content":
            result["content"] = [{"type":"text","text":42}]
        if mode == "not_result":
            result = "PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE"
        if mode == "nonfinite":
            print('{"jsonrpc":"2.0","id":' + json.dumps(request["id"]) + ',"result":{"content":[],"isError":false,"extra":NaN}}', flush=True)
            continue
        if mode == "duplicate":
            print('{"jsonrpc":"2.0","id":' + json.dumps(request["id"]) + ',"result":{"content":[],"isError":true,"isError":false}}', flush=True)
            continue
        emit({"jsonrpc":"2.0", "id": "wrong-id" if mode == "wrong_id" else request["id"], "result":result})
        if mode == "trailing_request":
            emit({"jsonrpc":"2.0", "id":"trailing", "method":"elicitation/create", "params":{}})
        if mode == "trailing_malformed":
            print("PRIVATE_PROVIDER_TEXT_MUST_NOT_ESCAPE", flush=True)
        if mode == "trailing_partial":
            sys.stdout.write('{"jsonrpc":')
            sys.stdout.flush()
if mode == "stay_after_eof":
    time.sleep(60)
'''


def params():
    return {"name": relay.TOOL_NAME, "arguments": {}, "_meta": json.loads(json.dumps(META))}


class RelayProtocolTests(unittest.TestCase):
    def fake(self, mode="success", timeout=1.5):
        return relay.ViewRelay(
            automation_id=RESERVATION,
            command=[sys.executable, "-B", "-u", "-c", PEER, mode],
            timeout_seconds=timeout, cleanup_seconds=0.3,
        )

    def assert_failure(self, result):
        self.assertIs(result["isError"], True)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotEqual(result["content"], [{"type": "text", "text": "{}"}])

    def test_success_forwards_exact_context_and_fixed_view_then_returns_hook_json(self):
        request = params()
        before = json.dumps(request)
        result = self.fake().call(request)
        self.assertEqual(result, {"content": [{"type": "text", "text": "{}"}], "isError": False})
        self.assertEqual(json.dumps(request), before)
        self.assertEqual(json.loads(result["content"][0]["text"]), {})

    def test_rejected_calls_never_launch_a_peer(self):
        invalid = [None, [], {}, {"name": "automation_update", "arguments": {}, "_meta": META}]
        for field, value in [
            ("arguments", {"mode": "update"}), ("arguments", {"id": "another-reservation"}),
            ("arguments", {"_meta": META}), ("arguments", []),
            ("_meta", {}), ("_meta", {"threadId": ""}), ("_meta", {"threadId": 1}),
            ("_meta", {"threadId": "   "}), ("_meta", {"thread_id": "fixture-child"}),
        ]:
            request = params()
            request[field] = value
            invalid.append(request)
        request = params()
        del request["_meta"]
        invalid.append(request)
        with mock.patch.object(relay.subprocess, "Popen") as launch:
            for request in invalid:
                with self.subTest(request=request):
                    self.assert_failure(self.fake().call(request))
            launch.assert_not_called()

    def test_environment_is_not_used_as_caller_identity(self):
        request = params()
        del request["_meta"]
        with mock.patch.dict("os.environ", {"CODEX_THREAD_ID": "fixture-child"}):
            self.assert_failure(self.fake().call(request))

    def test_error_and_invalid_responses_are_sanitized_failures(self):
        for mode in ["init_error", "bad_version", "bad_server_info", "eof", "malformed", "oversize", "server_request",
                     "rpc_error", "tool_error", "missing_success", "wrong_success_type", "bad_content",
                     "not_result", "nonfinite", "duplicate", "wrong_id", "trailing_request",
                     "trailing_malformed", "trailing_partial"]:
            with self.subTest(mode=mode):
                self.assert_failure(self.fake(mode).call(params()))

    def test_stderr_is_drained_but_not_returned(self):
        result = self.fake("stderr").call(params())
        self.assertIs(result["isError"], False)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_deadline_and_cleanup_reap_only_the_launched_child(self):
        original = subprocess.Popen
        processes = []

        def launch(*args, **kwargs):
            self.assertFalse(kwargs.get("shell", False))
            self.assertNotIn("env", kwargs)
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        for mode in ["init_timeout", "timeout", "stay_after_eof"]:
            started = time.monotonic()
            with mock.patch.object(relay.subprocess, "Popen", side_effect=launch):
                result = self.fake(mode, timeout=0.6).call(params())
            if mode != "stay_after_eof":
                self.assert_failure(result)
            self.assertLess(time.monotonic() - started, 6)
            self.assertIsNotNone(processes[-1].poll())
            self.assertTrue(all(stream.closed for stream in [processes[-1].stdin, processes[-1].stdout, processes[-1].stderr]))

    def test_launch_error_is_not_echoed(self):
        with mock.patch.object(relay.subprocess, "Popen", side_effect=OSError(SECRET)):
            self.assert_failure(self.fake().call(params()))

    def test_cleanup_uncertainty_cannot_be_reported_as_success(self):
        original = relay._ChildSession.close

        def uncertain(session):
            original(session)
            return False

        with mock.patch.object(relay._ChildSession, "close", uncertain):
            self.assert_failure(self.fake().call(params()))


class ChildResponseDiagnosticTests(unittest.TestCase):
    def session(self, data):
        # Exercise the response boundary without a subprocess for malformed
        # envelopes and exhausted deadlines; real pipes are covered separately.
        child = object.__new__(relay._ChildSession)
        child.deadline = time.monotonic() + 1
        child.failed = threading.Event()
        child.messages = queue.Queue()
        child.messages.put(data)
        return child

    def assert_reason(self, child, reason):
        with self.assertRaises(relay.RelayError) as caught:
            child.result(2)
        error = caught.exception
        self.assertEqual(error.response_reason, reason)
        self.assertEqual(error.args, ())
        self.assertNotIn(SECRET, json.dumps(vars(error)))

    def test_standard_rpc_errors_are_classified_without_message_data_or_arbitrary_codes(self):
        cases = {-32700: "rpc_parse_error", -32600: "rpc_invalid_request",
                 -32601: "rpc_method_not_found", -32602: "rpc_invalid_params",
                 -32603: "rpc_internal_error", -1234567: "rpc_error", 1234567: "rpc_error"}
        for code, expected in cases.items():
            envelope = {"jsonrpc": "2.0", "id": 2,
                        "error": {"code": code, "message": SECRET, "data": {"secret": SECRET}}}
            with self.subTest(code=code):
                self.assert_reason(self.session(relay._encode(envelope)), expected)

    def test_invalid_rpc_envelopes_cannot_claim_a_standard_error(self):
        valid = {"jsonrpc": "2.0", "id": 2,
                 "error": {"code": -32602, "message": SECRET}}
        cases = [[], {**valid, "id": 3}, {**valid, "id": True}, {**valid, "id": "2"},
                 {**valid, "result": {}}, {**valid, "extra": SECRET},
                 {**valid, "jsonrpc": "1.0"}, {**valid, "error": SECRET}]
        cases += [{**valid, "error": error} for error in (
            {"code": True, "message": SECRET}, {"code": -32602.0, "message": SECRET},
            {"code": -32602}, {"code": -32602, "message": 1},
            {"code": -32602, "message": SECRET, "extra": SECRET},
        )]
        for value in cases:
            with self.subTest(value=value):
                self.assert_reason(self.session(relay._encode(value)), "response_envelope_invalid")

    def test_requests_notifications_bad_json_eof_limits_and_deadlines_stay_failures(self):
        for value, reason in [
            ({"jsonrpc": "2.0", "id": "challenge", "method": SECRET, "params": {}},
             "server_request_unsupported"),
            ({"jsonrpc": "2.0", "method": SECRET, "params": {}}, "notification_unsupported"),
        ]:
            self.assert_reason(self.session(relay._encode(value)), reason)
        for data in (SECRET.encode(), b'{"a":1,"a":2}', b'{"x":NaN}'):
            self.assert_reason(self.session(data), "response_json_invalid")
        self.assert_reason(self.session(None), "response_eof")
        child = self.session(None)
        child.deadline = time.monotonic() - 1
        self.assert_reason(child, "response_timeout")
        child.failed.set()
        self.assert_reason(child, "response_io_or_limit")

    def test_reason_is_closed_and_success_is_unchanged(self):
        for reason in (SECRET, [], {}, 1):
            self.assertIsNone(relay.RelayError(response_reason=reason).response_reason)
        value = {"jsonrpc": "2.0", "id": 2, "result": {"content": [], "isError": False}}
        self.assertEqual(self.session(relay._encode(value)).result(2), value["result"])


class RelayServerTests(unittest.TestCase):
    def run_frames(self, frames, service=None):
        service = service or mock.Mock()
        service.call.return_value = {"content": [{"type": "text", "text": "{}"}], "isError": False}
        source = io.BytesIO(b"".join(json.dumps(frame).encode() + b"\n" for frame in frames))
        output = io.BytesIO()
        relay.serve(source, output, service)
        return [json.loads(line) for line in output.getvalue().splitlines()], service

    def initialize(self):
        return [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]

    def test_initialize_and_discover_are_host_free_and_expose_only_one_empty_input_tool(self):
        responses, service = self.run_frames(self.initialize() + [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ])
        service.call.assert_not_called()
        self.assertEqual(responses[0]["result"]["capabilities"], {"tools": {}})
        tools = responses[1]["result"]["tools"]
        self.assertEqual([item["name"] for item in tools], [relay.TOOL_NAME])
        self.assertEqual(tools[0]["inputSchema"]["properties"], {})
        self.assertIs(tools[0]["inputSchema"]["additionalProperties"], False)
        self.assertEqual(responses[2]["result"], {})

    def test_call_after_initialize_preserves_request_metadata(self):
        responses, service = self.run_frames(self.initialize() + [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params()},
        ])
        service.call.assert_called_once_with(params())
        self.assertEqual(responses[-1]["result"]["content"][0]["text"], "{}")

    def test_standard_discovery_metadata_is_accepted_without_host_access_or_disclosure(self):
        frames = self.initialize()
        frames[-1]["params"] = {"_meta": {"traceparent": SECRET}}
        responses, service = self.run_frames(frames + [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {
                "_meta": {"traceparent": SECRET}, "cursor": None}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {"_meta": {"traceparent": SECRET}}},
        ])
        self.assertTrue(all("result" in response for response in responses))
        self.assertEqual(responses[1]["result"]["tools"][0]["name"], relay.TOOL_NAME)
        self.assertNotIn(SECRET, json.dumps(responses))
        service.call.assert_not_called()

    def test_control_parameters_are_validated_and_never_supply_missing_call_identity(self):
        for method, invalid in [("tools/list", {"cursor": "unknown"}), ("tools/list", {"mode": "view"}),
                                ("tools/list", {"_meta": []}), ("ping", {"cursor": None})]:
            with self.subTest(method=method, invalid=invalid):
                responses, service = self.run_frames(self.initialize() + [
                    {"jsonrpc": "2.0", "id": 2, "method": method, "params": invalid},
                ])
                self.assertEqual(responses[-1]["error"]["code"], -32602)
                service.call.assert_not_called()
        call_params = params()
        del call_params["_meta"]
        responses, service = self.run_frames(self.initialize() + [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": META}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": call_params},
        ])
        self.assertIn("result", responses[-2])
        self.assertEqual(responses[-1]["error"]["code"], -32602)
        service.call.assert_not_called()

    def test_call_before_initialize_and_call_notifications_do_not_dispatch(self):
        frames = [{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params()}]
        responses, service = self.run_frames(frames)
        self.assertIn("error", responses[0])
        service.call.assert_not_called()
        responses, service = self.run_frames(self.initialize() + [
            {"jsonrpc": "2.0", "method": "tools/call", "params": params()},
        ])
        service.call.assert_not_called()

    def test_unsupported_and_invalid_calls_are_rejected_before_dispatch(self):
        bad_params = params()
        bad_params["arguments"] = {"mode": "update"}
        responses, service = self.run_frames(self.initialize() + [
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": bad_params},
        ])
        self.assertTrue(all("error" in response for response in responses[1:]))
        service.call.assert_not_called()

    def test_nonstandard_json_is_rejected(self):
        for source in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}']:
            with self.subTest(source=source), self.assertRaises((relay.RelayError, ValueError)):
                relay.strict_json_loads(source)

    def test_array_json_rpc_envelopes_are_rejected_without_dispatch(self):
        responses, service = self.run_frames([[1, 2]])
        self.assertEqual(responses[0]["error"]["code"], -32600)
        service.call.assert_not_called()


class RelayEntryTests(unittest.TestCase):
    def run_cli(self, *args):
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"CODEX_MCP_NODE_PATH", "CODEX_APP_TOOLS_PIPE_PATH"}}
        return subprocess.run([sys.executable, "-B", relay.__file__, *args],
                              input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=environment, timeout=5)

    def test_help_has_no_runtime_dependency(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"--automation-id", result.stdout)
        self.assertEqual(result.stderr, b"")

    def test_missing_environment_and_bad_arguments_are_sanitized(self):
        for args in [
            ["--automation-id", "fixture", "--server", SECRET],
            ["--unknown-argument", SECRET],
        ]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(SECRET.encode(), result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)

    def test_examples_are_inert_and_pinned_to_the_fixed_tool(self):
        directory = Path(__file__).resolve().parents[1] / "tools"
        config = tomllib.loads((directory / "review_wait_mcp_relay.example.toml").read_text(encoding="utf-8"))
        server = config["mcp_servers"]["taskgov_hook_relay"]
        self.assertIs(server["enabled"], False)
        self.assertEqual(server["enabled_tools"], [relay.TOOL_NAME])
        self.assertNotIn("env", server)
        self.assertNotIn("CODEX_THREAD_ID", server["env_vars"])
        hook = json.loads((directory / "review_wait_mcp_relay.hooks.example.json").read_text(encoding="utf-8"))
        handler = hook["hooks"]["SubagentStop"][0]["hooks"][0]
        self.assertEqual(handler["input"], {})
        self.assertEqual(handler["tool"], relay.TOOL_NAME)
        self.assertEqual(handler["server"], "taskgov_hook_relay")


if __name__ == "__main__":
    unittest.main()
