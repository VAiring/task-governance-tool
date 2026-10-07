"""Isolated protocol/lifetime checks; no genuine executor or host operations."""

from io import BytesIO
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.review_wait_mcp_relay import MAX_MESSAGE_BYTES, _encode
from tools.review_wait_server import ReviewWaitSession, SessionConfig, _bootstrap_failure, serve
from tools.review_wait_basis import BasisError
from tools.review_wait_host import HostAdapterError
from tools.review_wait_service import ReviewWaitService, ServiceConfig
from tools.review_wait_repository import RepositoryError, ReviewWaitRepository
from tests.test_review_wait_service import BINDING, CHILD, META, NOW, OTHER, FakeHost, FakeObserver


def request(method, params=None, request_id=1):
    value = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        value["id"] = request_id
    return _encode(value)


HELLO = request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "fixture", "version": "1"}})
READY = request("notifications/initialized", request_id=None)


class Service:
    def __init__(self):
        self.calls, self.closed, self.cleanup, self.failure = [], 0, {"ok": True}, None

    def handle(self, operation, arguments, metadata):
        self.calls.append((operation, arguments, metadata))
        if self.failure:
            raise ValueError(self.failure)
        return {"ok": True, "state": {"arm": 1}}

    def close(self):
        self.closed += 1
        return self.cleanup


class ReviewWaitServerTests(unittest.TestCase):
    def run_server(self, data, service=None):
        service = service or Service()
        output = BytesIO()
        code = serve(BytesIO(data), output, service)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(1, service.closed)
        return code, lines, service

    def test_discovery_and_ping_do_not_call_host_or_create_state(self):
        code, lines, service = self.run_server(HELLO + READY + request("tools/list") + request("ping"))
        self.assertEqual(0, code)
        self.assertEqual([], service.calls)
        tools = lines[1]["result"]["tools"]
        self.assertEqual(["review_wait_" + op for op in ("prepare", "view", "arm", "check", "rearm", "cancel",
                         "direct_start", "direct_delete_start", "direct_status", "direct_cancel", "direct_ack")],
                         [tool["name"] for tool in tools])
        self.assertEqual({}, lines[-1]["result"])

    def test_only_exact_tool_metadata_and_arguments_are_forwarded(self):
        metadata = {"threadId": "actual-parent", "opaque": {"token": "private-do-not-echo"}}
        params = {"name": "review_wait_arm", "arguments": {}, "_meta": metadata}
        code, lines, service = self.run_server(HELLO + READY + request("tools/call", params))
        self.assertEqual(0, code)
        self.assertEqual([("arm", {}, metadata)], service.calls)
        self.assertFalse(lines[-1]["result"]["isError"])
        self.assertNotIn("private-do-not-echo", json.dumps(lines))

    def test_notifications_and_uninitialized_calls_cannot_mutate(self):
        params = {"name": "review_wait_arm", "arguments": {}, "_meta": {"threadId": "parent"}}
        code, lines, service = self.run_server(request("tools/call", params) + HELLO + READY
            + request("tools/call", params, request_id=None))
        self.assertEqual([], service.calls)
        self.assertEqual(-32600, lines[0]["error"]["code"])

    def test_invalid_input_does_not_reach_service(self):
        for params in ({"name": "review_wait_arm", "arguments": {}},
                       {"name": "automation_update", "arguments": {}, "_meta": {}},
                       {"name": "review_wait_arm", "arguments": {"rrule": "secret"}, "_meta": {}},
                       {"name": "review_wait_cancel", "arguments": {}, "_meta": {}}):
            with self.subTest(params=params):
                _, lines, service = self.run_server(HELLO + READY + request("tools/call", params))
                self.assertEqual([], service.calls)
                self.assertEqual(-32602, lines[-1]["error"]["code"])

    def test_malformed_and_oversized_records_fail_closed_and_cleanup(self):
        for record in (b'{"jsonrpc":"2.0","jsonrpc":"2.0"}\n', b'{}', b'x' * (MAX_MESSAGE_BYTES + 1)):
            code, lines, service = self.run_server(HELLO + READY + record)
            self.assertEqual(1, code)
            self.assertEqual(-32700, lines[-1]["error"]["code"])
            self.assertEqual([], service.calls)

    def test_unknown_cleanup_is_not_successful_server_exit(self):
        service = Service()
        service.cleanup = {"ok": False, "error": "cleanup_unknown"}
        self.assertEqual(1, self.run_server(HELLO + READY, service)[0])

    def test_exception_does_not_echo_content_and_service_still_closes(self):
        service = Service()
        service.failure = "private-provider-content"
        _, lines, _ = self.run_server(HELLO + READY + request("tools/call",
            {"name": "review_wait_view", "arguments": {}, "_meta": {}}), service)
        self.assertEqual(-32603, lines[-1]["error"]["code"])
        self.assertNotIn(service.failure, json.dumps(lines))


class ReviewWaitSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-review-wait-session-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "candidate.sqlite"
        service = ServiceConfig(self.path, self.directory / "server.mjs", self.directory, "UTC", "host_os")
        self.config = SessionConfig(service, self.directory, BINDING.task_id, "timer-1")
        self.host, self.binding, self.factory_calls = FakeHost(), BINDING, []
        self.session = self.make_session()
        self.addCleanup(self.session.close)

    def make_session(self):
        def basis(repo, task, parent, wait_id):
            self.factory_calls.append((repo, task, parent, wait_id))
            return lambda: replace(self.binding, wait_id=wait_id)
        return ReviewWaitSession(self.config, host_factory=lambda **kwargs: self.host,
            basis_factory=basis, clock=lambda: NOW,
            service_factory=lambda *args, **kwargs: ReviewWaitService(*args, **kwargs, observer_factory=FakeObserver))

    def prepare(self, metadata=META):
        return self.session.handle("prepare", {"reviewer_ids": [CHILD]}, metadata)

    def test_prepare_captures_current_turn_and_basis_without_timer_effect(self):
        response = self.prepare()
        self.assertTrue(response["ok"], response)
        state = ReviewWaitRepository.open_existing(self.path).read().controller
        self.assertEqual(self.host.turn.turn_id, state.reviewers[0].turn_id)
        self.assertEqual(self.binding, replace(state.binding, wait_id=self.binding.wait_id))
        self.assertEqual([], self.host.effects)
        self.assertEqual("preparing", response["state"]["phase"])
        self.assertFalse(response["observer_running"])
        self.assertNotIn(b"PRIVATE_EXECUTOR", self.path.read_bytes())

    def test_unresolved_handle_reports_fixed_stage_without_creating_association(self):
        with mock.patch.object(self.host, "resolve_reviewer_ids", create=True,
                side_effect=HostAdapterError("reviewer_identity_unavailable")) as resolve:
            response = self.session.handle("prepare", {"reviewer_ids": ["/root/reviewer"]}, META)
        self.assertEqual({"ok": False, "error": "candidate_unavailable",
            "stage": "reviewer_resolution", "reason": "reviewer_identity_unavailable"}, response)
        resolve.assert_called_once_with()
        self.assertFalse(self.path.exists())
        self.assertEqual([], self.host.effects)
        self.assertEqual([], self.host.calls)

    def test_view_or_arm_missing_store_never_initializes(self):
        for operation in ("view", "arm"):
            self.assertFalse(self.session.handle(operation, {}, META)["ok"])
            self.assertFalse(self.path.exists())
        self.assertEqual([], self.host.calls)

    def test_prepare_never_overwrites_and_restart_view_never_starts_worker(self):
        self.assertTrue(self.prepare()["ok"])
        before = self.path.read_bytes()
        self.assertEqual("state_already_exists", self.prepare()["error"])
        self.session.close()
        reopened = self.make_session()
        self.addCleanup(reopened.close)
        response = reopened.handle("view", {}, META)
        self.assertTrue(response["ok"])
        self.assertFalse(response["observer_running"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], self.host.effects)

    def test_missing_identity_or_different_owner_cannot_prepare(self):
        for metadata in ({}, {"threadId": OTHER}):
            self.assertFalse(self.prepare(metadata)["ok"])
            self.assertFalse(self.path.exists())

    def test_bootstrap_diagnostics_report_fixed_stage_without_exception_text(self):
        secret = "PRIVATE_EXCEPTION_METADATA_AND_PROVIDER_BODY"
        cases = [
            ("host_context", mock.patch.object(self.session, "host_factory",
                side_effect=HostAdapterError("host_transport_unavailable")), "host_transport_unavailable"),
            ("basis_before", mock.patch.object(self.session, "basis_factory",
                side_effect=BasisError()), "wait_basis_unavailable"),
            ("child_read", mock.patch.object(self.host, "read_child",
                side_effect=HostAdapterError("host_call_failed")), "host_call_failed"),
            ("heartbeat_read", mock.patch.object(self.host, "view_heartbeat",
                side_effect=HostAdapterError("timezone_changed")), "timezone_changed"),
            ("state_create", mock.patch.object(ReviewWaitRepository, "create",
                side_effect=RepositoryError("state_path_invalid")), "state_path_invalid"),
        ]
        for stage, patched, reason in cases:
            with self.subTest(stage=stage), patched as target:
                target.side_effect.args = (secret,)
                response = self.prepare()
                self.assertEqual({"ok": False, "error": "candidate_unavailable",
                                  "stage": stage, "reason": reason}, response)
                self.assertNotIn(secret, json.dumps(response))
                self.assertFalse(self.path.exists())
                self.assertEqual([], self.host.effects)
        response = self.prepare({})
        self.assertEqual(("executor_admission", "executor_context_required"),
                         (response["stage"], response["reason"]))

    def test_second_basis_read_and_post_create_load_failures_are_distinguished(self):
        reads = mock.Mock(side_effect=[self.binding, BasisError()])
        with mock.patch.object(self.session, "_reader", return_value=reads):
            response = self.prepare()
        self.assertEqual(("basis_after", "wait_basis_unavailable"),
                         (response["stage"], response["reason"]))
        self.assertFalse(self.path.exists())
        with mock.patch.object(self.session, "_load", side_effect=RepositoryError("state_unreadable")):
            response = self.prepare()
        self.assertEqual(("service_load", "state_unreadable"),
                         (response["stage"], response["reason"]))
        self.assertTrue(self.path.exists())  # Report partial publication; never overwrite it.
        self.assertEqual("state_already_exists", self.prepare()["error"])
        self.assertEqual([], self.host.effects)

    def test_unknown_exceptions_and_unlisted_codes_never_escape(self):
        for error in (ValueError("PRIVATE_EXCEPTION"), HostAdapterError("PRIVATE_CODE")):
            error.code = "host_call_failed" if type(error) is ValueError else "PRIVATE_CODE"
            response = _bootstrap_failure("child_read", error)
            self.assertEqual("candidate_unavailable", response["reason"])
            self.assertNotIn("PRIVATE", json.dumps(response))
        with mock.patch.object(self.host, "read_child", side_effect=ValueError("PRIVATE_EXCEPTION")):
            self.assertEqual({"ok": False, "error": "candidate_unavailable", "stage": "child_read",
                              "reason": "candidate_unavailable"}, self.prepare())

    def test_host_boundary_diagnostics_preserve_outer_stage_and_do_not_echo_details(self):
        from tools.review_wait_host import HOST_BOUNDARY_REASONS
        for reason in HOST_BOUNDARY_REASONS:
            error = HostAdapterError("host_call_failed")
            error.boundary_reason = reason
            error.args = ("PRIVATE_PROVIDER_BODY",)
            with self.subTest(reason=reason), mock.patch.object(self.host, "view_heartbeat", side_effect=error):
                self.assertEqual({"ok": False, "error": "candidate_unavailable",
                    "stage": "heartbeat_read", "reason": reason}, self.prepare())
                self.assertFalse(self.path.exists())
        for error in (HostAdapterError("host_call_failed"), ValueError("PRIVATE_EXCEPTION")):
            error.code = "host_call_failed"
            error.boundary_reason = "PRIVATE_DIAGNOSTIC"
            response = _bootstrap_failure("heartbeat_read", error)
            self.assertNotIn("PRIVATE", json.dumps(response))
            self.assertEqual("host_call_failed" if type(error) is HostAdapterError
                             else "candidate_unavailable", response["reason"])
        spoofed = ValueError("PRIVATE_EXCEPTION")
        spoofed.code, spoofed.boundary_reason = "host_call_failed", "host_tool_error"
        self.assertEqual("candidate_unavailable", _bootstrap_failure("heartbeat_read", spoofed)["reason"])

    def test_active_or_different_timer_refuses_store_creation(self):
        original = self.host.value
        for changed in (replace(original, status="ACTIVE"), replace(original, id="other-timer"),
                        replace(original, timezone="Asia/Tokyo")):
            self.host.value = changed
            self.assertFalse(self.prepare()["ok"])
            self.assertFalse(self.path.exists())
        self.assertEqual([], self.host.effects)

    def test_basis_change_during_preparation_refuses_store_creation(self):
        view = self.host.view_heartbeat
        def changed():
            self.binding = replace(self.binding, ownership_generation=2)
            return view()
        self.host.view_heartbeat = changed
        self.assertEqual("task_binding_changed", self.prepare()["error"])
        self.assertFalse(self.path.exists())

    def test_only_explicit_arm_mutates_timer_and_close_never_pauses_it(self):
        self.assertTrue(self.prepare()["ok"])
        response = self.session.handle("arm", {}, META)
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["observer_running"])
        self.assertEqual(["arm"], self.host.effects)
        self.assertEqual({"ok": True}, self.session.close())
        self.assertEqual(["arm"], self.host.effects)
        self.assertEqual("service_closed", self.session.handle("arm", {}, META)["error"])

    def test_direct_controls_route_metadata_and_exclude_timer(self):
        self.assertTrue(self.prepare()["ok"])
        probe = mock.Mock()
        probe.handle.return_value = {"ok": True}
        probe.blocks_timer.return_value = True
        probe.close.return_value = {"ok": True}
        self.session.direct_factory = mock.Mock(return_value=probe)
        self.assertTrue(self.session.handle("direct_start", {}, META)["ok"])
        probe.handle.assert_called_once_with("direct_start", {}, META)
        self.assertEqual("direct_probe_selected", self.session.handle("arm", {}, META)["error"])
        self.assertEqual("direct_probe_selected", self.session.handle("rearm",
                         {"expected_arm": 0, "healthy": True}, META)["error"])
        self.assertEqual([], self.host.effects)
        self.assertTrue(self.session.handle("view", {}, META)["ok"])
        self.assertEqual({"ok": True}, self.session.close())
        probe.close.assert_called_once()

    def test_direct_missing_preparation_cannot_initialize_state(self):
        for operation in ("direct_start", "direct_status", "direct_ack"):
            self.assertFalse(self.session.handle(operation, {}, META)["ok"])
            self.assertFalse(self.path.exists())
        self.assertEqual([], self.host.effects)

    def test_direct_unknown_cleanup_is_reported_and_regular_service_also_closes(self):
        self.assertTrue(self.prepare()["ok"])
        probe = mock.Mock()
        probe.close.return_value = {"ok": False}
        self.session.direct = probe
        result = self.session.close()
        self.assertFalse(result["ok"])
        self.assertEqual("service_closed", self.session.service.handle("view", {}, META)["error"])


if __name__ == "__main__":
    unittest.main()
