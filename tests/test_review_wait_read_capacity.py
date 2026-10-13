"""Finite read-response admission and purpose isolation; no real host access."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import queue
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
from uuid import uuid4

from tests import test_review_wait_host as fixtures
from tests import test_review_wait_managed as managed_fixtures
from task_governance_tool.review_wait_runtime import review_wait_host as host
from task_governance_tool.review_wait_runtime import review_wait_mcp_relay as relay
from task_governance_tool.review_wait_runtime.failure_diagnostics import CallDiagnostics
from task_governance_tool.review_wait_runtime.request_repository import RequestRepository
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository


# Every operation goes to an isolated stdin/stdout peer. No Desktop connection,
# executor injection, provider logs or runtime-state writes are involved.
PEER = r'''
import json, sys
size = int(sys.argv[1]); operation = sys.argv[2]
purpose = sys.argv[3] if len(sys.argv) > 3 else "parent"
def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
for line in sys.stdin:
    request = json.loads(line)
    if request["method"] == "initialize":
        result = {"protocolVersion":request["params"]["protocolVersion"],
                  "capabilities":{"tools":{}}, "serverInfo":{"name":"fake","version":"1"}}
    elif request["method"] == "notifications/initialized":
        continue
    else:
        assert request["params"]["name"] == operation
        parent = request["params"]["_meta"]["threadId"]
        child = "22222222-2222-4222-8222-222222222222"
        turn = {"id":"33333333-3333-4333-8333-333333333333","status":"completed",
                "error":None,"startedAt":1,"completedAt":2,"durationMs":1}
        if operation == "read_thread":
            value = {"schemaVersion":1,"thread":{"id":request["params"]["arguments"]["threadId"],"kind":"codex","hostId":"local",
                "status":{"type":"idle"}},"page":{"order":"newest_first","limit":1,
                "nextCursor":None,"hasMore":False},"turns":[dict(turn,items=[])]}
            if size == -1:
                value["turns"][0]["items"] = [{"type":"agentMessage","text":"日本語😀\\\"" * 40000}]
            if purpose == "dispatch":
                value["thread"]["status"] = {"type":"active"}
                value["turns"][0].update(id=request["params"]["_meta"]["turnId"], status="inProgress",
                    items=[{"type":"subAgentActivity","id":"dispatch","kind":"started",
                            "agentPath":"/root/one","agentThreadId":child}])
            if purpose == "receipt":
                fence = "cursor" in request["params"]["arguments"]
                value["turns"][0].update(id=sys.argv[5] if fence else sys.argv[6], items=[] if fence else [{
                    "type":"functionCallOutput","name":"send_message_to_thread","namespace":"codex_app",
                    "output":{"truncated":False,"text":sys.argv[4]}}])
                value["page"].update(hasMore=not fence, nextCursor=None if fence else "fence")
        elif operation == "wait_threads":
            value = {"timedOut":False,"wake":None,"polls":[{"schemaVersion":1,"cursor":"c",
                "revision":1,"changed":True,"thread":{"id":child,"hostId":"local",
                "status":{"type":"idle"}},"latestTurn":turn,"latestAssistantMessage":{}}]}
        else:
            value = {}
        result = {"content":[{"type":"text","text":json.dumps(value,ensure_ascii=False)}],"isError":False}
        response = {"jsonrpc":"2.0","id":request["id"],"result":result}
        # Padding sits in a parser-ignored presentation field, never a state fact.
        if size > 0:
            if operation == "read_thread":
                value["thread"]["preview"] = ""
            elif operation == "wait_threads":
                value["polls"][0]["latestAssistantMessage"] = {"text":""}
            else:
                value["padding"] = ""
            def rebuild():
                result["content"][0]["text"] = json.dumps(value,ensure_ascii=False,separators=(",", ":"))
            rebuild()
            padding = "x" * (size - len(wire(response)))
            if operation == "read_thread": value["thread"]["preview"] = padding
            elif operation == "wait_threads": value["polls"][0]["latestAssistantMessage"]["text"] = padding
            else: value["padding"] = padding
            rebuild()
            assert len(wire(response)) == size
        sys.stdout.buffer.write(wire(response)); sys.stdout.buffer.flush()
        continue
    sys.stdout.buffer.write(wire({"jsonrpc":"2.0","id":request["id"],"result":result})); sys.stdout.buffer.flush()
'''


def boundary_error():
    return host.HostAdapterError("host_call_failed", boundary_reason="host_response_frame_limit",
                                 operation="read_thread", termination="confirmed")


class ReadWireCapacityTests(unittest.TestCase):
    def peer(self, size, operation="read_thread"):
        return fixtures.adapter(Path.cwd(), command=[sys.executable, "-B", "-u", "-c", PEER,
                                                     str(size), operation])

    def test_real_outer_read_frames_at_old_and_adopted_boundaries(self):
        for size in (relay.MAX_MESSAGE_BYTES + 1, relay.MAX_READ_RESPONSE_BYTES - 1,
                     relay.MAX_READ_RESPONSE_BYTES):
            with self.subTest(size=size):
                value = self.peer(size).read_parent()
                self.assertEqual(value, host.ChildTurn(fixtures.PARENT, fixtures.TURN, "completed", "idle"))
                self.assertNotIn("preview", vars(value))
        with self.assertRaises(host.HostAdapterError) as caught:
            self.peer(relay.MAX_READ_RESPONSE_BYTES + 1).read_parent()
        self.assertEqual(caught.exception.boundary_reason, "host_response_frame_limit")
        self.assertEqual(host.read_failure(caught.exception)["purpose"], "parent_state")

    def test_child_status_read_gets_larger_outer_and_inner_admission(self):
        for size in (relay.MAX_MESSAGE_BYTES + 10000, relay.MAX_READ_RESPONSE_BYTES):
            value = self.peer(size).read_child(fixtures.CHILD)
            self.assertEqual((value.child_id, value.status, value.thread_status), (fixtures.CHILD, "completed", "idle"))
        with self.assertRaises(host.HostAdapterError) as caught:
            self.peer(relay.MAX_READ_RESPONSE_BYTES + 1).read_child(fixtures.CHILD)
        self.assertEqual(host.host_failure(caught.exception), {
            "operation": "read_thread", "reason": "host_response_frame_limit", "termination": "confirmed"})

    def test_dispatch_and_receipt_purposes_each_admit_real_large_outer_and_inner_frames(self):
        for size in (relay.MAX_MESSAGE_BYTES + 10000, relay.MAX_READ_RESPONSE_BYTES):
            with self.subTest(purpose="dispatch", size=size):
                service = fixtures.adapter(Path.cwd(), reviewer_ids=["/root/one"], command=[
                    sys.executable, "-B", "-u", "-c", PEER, str(size), "read_thread", "dispatch"])
                self.assertEqual(service.resolve_reviewer_ids(), (fixtures.CHILD,))
            with self.subTest(purpose="receipt", size=size):
                service = managed_fixtures.ManagedHostTests().host()
                probe, original, received = managed_fixtures.OTHER, managed_fixtures.ORIGINAL, managed_fixtures.NEW_TURN
                event = ("<codex_delegation>\n  <source_thread_id>" + service.parent_thread_id
                         + "</source_thread_id>\n  <input>" + service._direct_prompt(probe)
                         + "</input>\n</codex_delegation>")
                service._command = (sys.executable, "-B", "-u", "-c", PEER, str(size), "read_thread",
                                    "receipt", event, original, received)
                self.assertEqual(service.observe_receipt(probe, original), received)

    def test_unicode_and_escaped_nested_json_survive_old_limit(self):
        value = self.peer(-1).read_parent()
        self.assertEqual((value.status, value.thread_status), ("completed", "idle"))

    def test_mutation_response_request_and_notification_bounds_remain_small(self):
        with self.assertRaises(host.HostAdapterError) as caught:
            self.peer(relay.MAX_MESSAGE_BYTES + 1, "automation_update")._json_call("automation_update", {})
        self.assertEqual(caught.exception.boundary_reason, "host_response_frame_limit")
        with self.assertRaises(relay.RelayError):
            relay._encode({"prompt":"x" * relay.MAX_MESSAGE_BYTES})
        service = self.peer(1000)
        with self.assertRaises(relay.RelayError):
            service._tool_call_bytes("send_message_to_thread", {"prompt":"日" * relay.MAX_MESSAGE_BYTES})

    def test_inner_cap_includes_utf8_and_remains_finite(self):
        for size in (relay.MAX_READ_RESPONSE_BYTES - 1, relay.MAX_READ_RESPONSE_BYTES):
            text = '"' + "x" * (size - 2) + '"'
            self.assertEqual(len(relay.strict_json_loads(text, max_bytes=relay.MAX_READ_RESPONSE_BYTES)), size - 2)
            with self.assertRaises(relay.RelayError):
                relay.strict_json_loads(text)
        for text in ('"' + "x" * (relay.MAX_READ_RESPONSE_BYTES - 1) + '"',
                     '"' + "日" * (relay.MAX_READ_RESPONSE_BYTES // 3 + 1) + '"'):
            with self.assertRaises(relay.RelayError) as caught:
                relay.strict_json_loads(text, max_bytes=relay.MAX_READ_RESPONSE_BYTES)
            self.assertEqual(caught.exception.response_reason, "response_frame_limit")
        for invalid in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":', b'"\xff"'):
            with self.assertRaises(relay.RelayError):
                relay.strict_json_loads(invalid, max_bytes=relay.MAX_READ_RESPONSE_BYTES)

    def test_large_inner_json_has_no_hidden_old_limit_and_timeout_is_checked_after_parse(self):
        service = self.peer(1000)
        text = json.dumps({"value":"x" * (relay.MAX_MESSAGE_BYTES + 1)})
        with mock.patch.object(service, "_call", return_value=(text,)):
            self.assertGreater(len(service._json_call("read_thread", {})["value"]), relay.MAX_MESSAGE_BYTES)
            with mock.patch.object(host.time, "monotonic", side_effect=[0, 21]):
                with self.assertRaises(host.HostAdapterError) as caught:
                    service._json_call("read_thread", {})
            self.assertEqual(caught.exception.boundary_reason, "host_response_timeout")
        text = '"' + "x" * relay.MAX_READ_RESPONSE_BYTES + '"'
        with mock.patch.object(service, "_call", return_value=(text,)), self.assertRaises(host.HostAdapterError) as caught:
            service._json_call("read_thread", {})
        self.assertEqual(caught.exception.boundary_reason, "host_response_frame_limit")

    def test_actual_owned_pipes_and_process_are_closed_after_large_success(self):
        original = subprocess.Popen
        processes = []
        def launch(*args, **kwargs):
            value = original(*args, **kwargs)
            processes.append(value)
            return value
        with mock.patch.object(relay.subprocess, "Popen", side_effect=launch):
            self.peer(relay.MAX_READ_RESPONSE_BYTES).read_parent()
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0].poll(), 0)
        self.assertTrue(all(pipe.closed for pipe in (processes[0].stdin, processes[0].stdout, processes[0].stderr)))


class ReadDrainCapacityTests(unittest.TestCase):
    def child(self):
        child = object.__new__(relay._ChildSession)
        child.response_bytes, child.stdout_bytes = relay.MAX_READ_RESPONSE_BYTES, relay.MAX_READ_DRAIN_BYTES
        child.failed, child.stop = threading.Event(), threading.Event()
        child.failure_lock, child.failure_reason = threading.Lock(), None
        child.messages = queue.Queue(relay.MAX_QUEUED_MESSAGES)
        child.deadline = time.monotonic() + 10
        return child

    def drain(self, data, *, capture=True, chunk_size=8192):
        child = self.child()
        chunks = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)] + [b""]
        stream = mock.Mock()
        with mock.patch.object(relay.os, "read", side_effect=chunks):
            child._drain(stream, capture)
        return child

    def test_frame_limit_and_truncation_are_not_cleanup_unknown(self):
        for size in (relay.MAX_READ_RESPONSE_BYTES - 1, relay.MAX_READ_RESPONSE_BYTES):
            child = self.drain(b"x" * (size - 1) + b"\n")
            self.assertFalse(child.failed.is_set())
            self.assertEqual(len(child.messages.get()), size)
        child = self.drain(b"x" * relay.MAX_READ_RESPONSE_BYTES + b"\n")
        self.assertEqual(child.failure_reason, "response_frame_limit")
        child = self.drain(b'{"jsonrpc":')
        self.assertEqual(child.failure_reason, "response_truncated")
        with self.assertRaises(relay.RelayError) as caught:
            child.result(2)
        self.assertEqual(caught.exception.response_reason, "response_truncated")

    def test_total_stdout_and_separate_stderr_bounds(self):
        frame = b"x" * (relay.MAX_READ_RESPONSE_BYTES - 1) + b"\n"
        child = self.drain(frame * 2)
        self.assertFalse(child.failed.is_set())
        self.assertEqual(child.messages.qsize(), 3)  # Two frames and EOF.
        self.assertEqual(self.drain(frame * 2 + b"x").failure_reason, "response_drain_limit")
        self.assertFalse(self.drain(b"x" * relay.MAX_DRAIN_BYTES, capture=False).failed.is_set())
        self.assertEqual(self.drain(b"x" * (relay.MAX_DRAIN_BYTES + 1), capture=False).failure_reason,
                         "response_drain_limit")

    def test_queue_cap_and_first_failure_do_not_expand_with_read_frames(self):
        child = self.child()
        for _ in range(relay.MAX_QUEUED_MESSAGES):
            child._publish(b"{}\n")
        self.assertFalse(child.failed.is_set())
        child._publish(b"{}\n")
        child._fail_response("response_drain_limit")
        self.assertEqual(child.failure_reason, "response_queue_limit")
        self.assertEqual(child.messages.qsize(), 16)

    def test_large_envelope_still_rejects_duplicate_extra_wrong_id_and_incomplete_json(self):
        for raw, reason in ((b'{"jsonrpc":"2.0","id":2,"id":2,"result":{}}\n', "response_json_invalid"),
                (b'{"jsonrpc":"2.0","id":2,"result":{},"extra":0}\n', "response_envelope_invalid"),
                (b'{"jsonrpc":"2.0","id":3,"result":{}}\n', "response_envelope_invalid"),
                (b'{"jsonrpc":\n', "response_json_invalid")):
            child = self.child()
            child.messages.put(raw)
            with self.subTest(reason=reason), self.assertRaises(relay.RelayError) as caught:
                child.result(2)
            self.assertEqual(caught.exception.response_reason, reason)


class PurposeRetentionTests(unittest.TestCase):
    def test_three_purposes_reject_real_pipe_wrong_identity_with_exchange_facts(self):
        # Only the isolated peer's JSON is changed; the actual bounded transport
        # and helper/reader/pipe cleanup establish termination independently.
        peer = PEER.replace('value["thread"]["preview"] = ""',
            'value["thread"]["id"] = "99999999-9999-4999-8999-999999999999"; value["thread"]["preview"] = ""')
        for purpose, public_purpose in (("parent", "parent_state"),
                ("dispatch", "reviewer_identity"), ("receipt", "notification_receipt")):
            with self.subTest(purpose=purpose):
                if purpose == "receipt":
                    service = managed_fixtures.ManagedHostTests().host()
                    service._command = (sys.executable, "-B", "-u", "-c", peer,
                        "2000", "read_thread", purpose, "unused", managed_fixtures.ORIGINAL,
                        managed_fixtures.NEW_TURN)
                    call = lambda: service.observe_receipt(managed_fixtures.OTHER, managed_fixtures.ORIGINAL)
                else:
                    service = fixtures.adapter(Path.cwd(), reviewer_ids=["/root/one"] if purpose == "dispatch"
                        else [fixtures.CHILD], command=[sys.executable, "-B", "-u", "-c", peer,
                                                       "2000", "read_thread", purpose])
                    call = service.resolve_reviewer_ids if purpose == "dispatch" else service.read_parent
                with self.assertRaises(host.HostAdapterError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "invalid_host_response")
                self.assertEqual(host.host_failure(caught.exception), {"operation":"read_thread",
                    "reason":"host_response_invalid", "termination":"confirmed"})
                self.assertEqual(host.read_failure(caught.exception)["purpose"], public_purpose)
                self.assertEqual(host.read_failure(caught.exception)["retained_pages"], 0)

    def test_semantic_boundary_preserves_failed_exchange_and_does_not_invent_facts(self):
        service = fixtures.adapter(Path.cwd(), reviewer_ids=["/root/one"])
        for error in (host.HostAdapterError("host_call_failed", boundary_reason="host_response_frame_limit",
                        operation="read_thread", termination="unresolved"),
                      host.HostAdapterError("host_call_failed")):
            with mock.patch.object(service, "_json_call", side_effect=error):
                with self.assertRaises(host.HostAdapterError) as caught:
                    service.resolve_reviewer_ids()
            self.assertIs(caught.exception, error)
            self.assertEqual(host.host_failure(caught.exception), host.host_failure(error))
        with mock.patch.object(service, "_json_call", side_effect=RuntimeError(fixtures.SECRET)):
            with self.assertRaises(RuntimeError) as caught:
                service.resolve_reviewer_ids()
        self.assertIsNone(host.host_failure(caught.exception))

    def test_selected_dispatch_semantic_rejection_retains_exchange(self):
        service = fixtures.adapter(Path.cwd(), reviewer_ids=["/root/one"])
        page = fixtures.dispatch_result(("/root/one", fixtures.CHILD))
        page["turns"][0]["items"][0]["agentThreadId"] = "invalid"
        with mock.patch.object(service, "_json_call", return_value=page):
            with self.assertRaises(host.HostAdapterError) as caught:
                service.resolve_reviewer_ids()
        self.assertEqual(host.host_failure(caught.exception), {"operation":"read_thread",
            "reason":"host_response_invalid", "termination":"confirmed"})
        self.assertEqual(host.read_failure(caught.exception)["retained_pages"], 0)

    def test_identity_freshness_rejection_keeps_actual_last_exchange_not_prior_success(self):
        service = fixtures.adapter(Path.cwd(), reviewer_ids=["/root/one"])
        first = fixtures.dispatch_result(("/root/one", fixtures.CHILD))
        changed = fixtures.dispatch_result(("/root/one", fixtures.CHILD))
        changed["turns"][0]["id"] = str(uuid4())
        with mock.patch.object(service, "_json_call", side_effect=[first, changed]):
            with self.assertRaises(host.HostAdapterError) as caught:
                service.resolve_reviewer_ids()
        self.assertEqual(caught.exception.code, "reviewer_identity_unavailable")
        self.assertEqual(host.host_failure(caught.exception), {"operation":"read_thread",
            "reason":"host_response_invalid", "termination":"confirmed"})
        self.assertEqual(host.read_failure(caught.exception)["retained_pages"], 2)
        service = fixtures.adapter(Path.cwd(), reviewer_ids=["/root/one"])
        unobserved = host.HostAdapterError("host_call_failed")
        with mock.patch.object(service, "_json_call", side_effect=[first, unobserved]):
            with self.assertRaises(host.HostAdapterError) as caught:
                service.resolve_reviewer_ids()
        self.assertIsNone(host.host_failure(caught.exception))
        self.assertEqual(host.read_failure(caught.exception)["retained_pages"], 1)

    def test_parent_failure_survives_unresolved_timer_cleanup(self):
        fixture = managed_fixtures.ManagedFlowTests()
        fixture.setUp()
        try:
            self.assertTrue(fixture.call()["ok"])
            service = fixture.hosts["managed-1"]
            def unavailable():
                raise host.annotate_read_failure(boundary_error(), "parent_state", 0)
            def cleanup_failed(*args, **kwargs):
                fixture.effects.append("pause")
                raise host.HostAdapterError("heartbeat_update_unknown")
            service.read_parent = unavailable
            service.update_heartbeat = cleanup_failed
            fixture.ended()
            worker = fixture.session.sessions["managed-1"].direct
            worker._worker.join(5)
            self.assertFalse(worker._worker.is_alive())
            value = fixture.inspect()
            self.assertEqual(value["delivery"]["timer_phase"], "unknown")
            self.assertEqual(value["delivery"]["reason"], "host_response_frame_limit")
            self.assertEqual(value["read_diagnostic"]["host_failure"], host.host_failure(boundary_error()))
            self.assertEqual(fixture.effects, ["create", "arm", "pause"])
        finally:
            fixture.doCleanups()

    def test_running_parent_failure_survives_cleanup_and_worker_stop_without_send(self):
        for termination in ("confirmed", "unresolved"):
            with self.subTest(termination=termination):
                fixture = managed_fixtures.ManagedFlowTests()
                fixture.setUp()
                try:
                    self.assertTrue(fixture.call()["ok"])
                    def unavailable():
                        raise host.annotate_read_failure(host.HostAdapterError("host_call_failed",
                            boundary_reason="host_response_frame_limit", operation="read_thread",
                            termination=termination), "parent_state", 0)
                    fixture.hosts["managed-1"].read_parent = unavailable
                    fixture.ended()
                    fixture.until(lambda: "read_diagnostic" in fixture.inspect())
                    worker = fixture.session.sessions["managed-1"].direct
                    worker._worker.join(5)
                    self.assertFalse(worker._worker.is_alive())
                    value = fixture.inspect()
                    self.assertEqual(value["observation"]["worker"]["status"], "stopped")
                    self.assertEqual(value["delivery"]["status"], "suppressed")
                    self.assertEqual(value["delivery"]["reason"], "host_response_frame_limit")
                    diagnostic = value["read_diagnostic"]
                    self.assertEqual(diagnostic["host_failure"]["termination"], termination)
                    self.assertEqual(diagnostic["read_failure"], {"purpose":"parent_state", "retained_pages":0,
                        "conclusion":"unknown", "next_action":"restore_parent_read"})
                    self.assertEqual(fixture.effects, ["create", "arm", "pause"])
                    # Later cleanup/observation cannot replace the first cause.
                    worker._retain_read_failure(host.annotate_read_failure(boundary_error(),
                        "notification_receipt", 1), worker.repository.read())
                    self.assertEqual(fixture.inspect()["read_diagnostic"], diagnostic)
                    other = fixture.call("inspect", session=fixture.make())
                    self.assertNotIn("read_diagnostic", other)
                finally:
                    fixture.doCleanups()

    def test_dispatch_page_projection_contains_no_history_and_unread_conflicts_stay_unknown(self):
        first, second = "/root/one", "/root/two"
        service = fixtures.adapter(Path.cwd(), reviewer_ids=[first, second])
        page = fixtures.dispatch_result((first, fixtures.CHILD), cursor="older")
        with mock.patch.object(service, "_json_call", side_effect=[page, boundary_error()]) as read:
            with self.assertRaises(host.HostAdapterError) as caught:
                service.resolve_reviewer_ids()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(service.reviewer_ids, (first, second))
        self.assertEqual(len(service._validated_dispatch_pages), 1)
        self.assertNotIn(fixtures.SECRET, repr(service._validated_dispatch_pages))
        self.assertEqual(host.read_failure(caught.exception), {"purpose":"reviewer_identity", "retained_pages":1,
                         "conclusion":"unknown", "next_action":"restore_identity_read"})

    def test_complete_mapping_is_not_published_after_final_freshness_read_fails(self):
        service = fixtures.adapter(Path.cwd(), reviewer_ids=["/root/one"])
        page = fixtures.dispatch_result(("/root/one", fixtures.CHILD))
        with mock.patch.object(service, "_json_call", side_effect=[page, boundary_error()]):
            with self.assertRaises(host.HostAdapterError):
                service.resolve_reviewer_ids()
        self.assertEqual(service.reviewer_ids, ("/root/one",))

    def test_known_uuid_avoids_dispatch_history_and_failed_parent_does_not_reuse_snapshot(self):
        service = fixtures.adapter(Path.cwd())
        page = fixtures.dispatch_result()
        with mock.patch.object(service, "_json_call", side_effect=[page, boundary_error()]) as read:
            self.assertEqual(service.resolve_reviewer_ids(), (fixtures.CHILD,))
            self.assertEqual(service.read_parent().child_id, fixtures.PARENT)
            with self.assertRaises(host.HostAdapterError) as caught:
                service.read_parent()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(host.read_failure(caught.exception)["purpose"], "parent_state")

    def test_receipt_candidate_and_complete_page_survive_failure_but_missing_fence_cannot_confirm(self):
        fixture = managed_fixtures.ManagedHostTests()
        service = fixture.host()
        page = fixture.receipt(service)
        page["page"].update(hasMore=True, nextCursor="fence")
        with mock.patch.object(service, "_json_call", side_effect=[page, boundary_error()]):
            with self.assertRaises(host.HostAdapterError) as caught:
                service.observe_receipt(managed_fixtures.OTHER, "55555555-5555-4555-8555-555555555555")
        self.assertEqual(len(service._validated_receipt_pages), 1)
        self.assertTrue(service._validated_receipt_pages[0][1])
        self.assertNotIn("codex_delegation", repr(service._validated_receipt_pages))
        self.assertEqual(host.read_failure(caught.exception), {"purpose":"notification_receipt", "retained_pages":1,
                         "conclusion":"unknown", "next_action":"retain_known_results"})

    def test_diagnostic_purpose_is_closed_and_survives_later_cleanup(self):
        error = host.annotate_read_failure(boundary_error(), "reviewer_identity", 1)
        diagnostic = CallDiagnostics(stage="reviewer_resolution")
        diagnostic.capture(error=error)
        diagnostic.cleanup = "unresolved"
        value = diagnostic.failure({"ok":False, "error":"review_wait_unavailable"})["diagnostic"]
        self.assertEqual(value["host_failure"]["reason"], "host_response_frame_limit")
        self.assertEqual(value["host_failure"]["termination"], "confirmed")
        self.assertEqual(value["read_failure"]["retained_pages"], 1)
        self.assertEqual(value["recovery"], "inspect_existing")
        self.assertLessEqual(len(json.dumps(value).encode()), 2048)
        valid = value["read_failure"]
        for corrupt in ({**valid, "extra":fixtures.SECRET}, {**valid, "purpose":fixtures.SECRET},
                        {**valid, "retained_pages":True}, {**valid, "retained_pages":18},
                        {**valid, "next_action":"send_again"}, {**valid, "conclusion":[]}):
            self.assertIsNone(host.validated_read_failure(corrupt))

    def test_receipt_failure_inspection_is_transient_safe_and_has_no_extra_send(self):
        fixture = managed_fixtures.ManagedFlowTests()
        fixture.setUp()
        try:
            self.assertTrue(fixture.call()["ok"])
            observed_host = fixture.hosts["managed-1"]
            def unavailable(*args, **kwargs):
                raise host.annotate_read_failure(boundary_error(), "notification_receipt", 1)
            observed_host.observe_receipt = unavailable
            fixture.ended()
            fixture.until(lambda: "read_diagnostic" in fixture.inspect())
            value = fixture.inspect()
            self.assertEqual(value["delivery"]["status"], "accepted")
            self.assertIsNone(value["delivery"]["acknowledged_turn"])
            self.assertEqual(value["read_diagnostic"]["read_failure"]["next_action"], "retain_known_results")
            self.assertEqual(fixture.effects, ["create", "arm", "delete", "send"])
            worker = fixture.session.sessions["managed-1"].direct
            worker._worker.join(5)
            self.assertFalse(worker._worker.is_alive())
            stopped = fixture.inspect()
            self.assertEqual(stopped["observation"]["worker"]["status"], "stopped")
            self.assertIn("read_diagnostic", stopped)
            other = fixture.call("inspect", session=fixture.make())
            self.assertNotIn("read_diagnostic", other)  # No fabricated post-restart cause.
            self.assertEqual(fixture.effects, ["create", "arm", "delete", "send"])
        finally:
            fixture.doCleanups()

    def test_diagnostic_is_suppressed_for_unknown_changed_or_mismatched_observation(self):
        for purpose in ("parent_state", "notification_receipt"):
            with self.subTest(purpose=purpose):
                self._assert_diagnostic_binding_suppression(purpose)

    def _assert_diagnostic_binding_suppression(self, purpose):
        fixture = managed_fixtures.ManagedFlowTests()
        fixture.setUp()
        try:
            self.assertTrue(fixture.call()["ok"])
            def unavailable(*args, **kwargs):
                raise host.annotate_read_failure(boundary_error(), purpose, 0 if purpose == "parent_state" else 1)
            if purpose == "parent_state":
                fixture.hosts["managed-1"].read_parent = unavailable
            else:
                fixture.hosts["managed-1"].observe_receipt = unavailable
            fixture.ended()
            fixture.until(lambda: "read_diagnostic" in fixture.inspect())
            worker = fixture.session.sessions["managed-1"].direct
            worker._worker.join(5)
            self.assertFalse(worker._worker.is_alive())
            snapshot = fixture.inspect()
            identity = snapshot["observation"]["identity"]
            path = fixture.paths.review_wait_request_store(managed_fixtures.PARENT, managed_fixtures.TASK)
            attempt, record = RequestRepository(path).read()
            for changed in ((attempt + 1, record), (attempt, replace(record, basis_digest="f" * 64)),
                            (attempt, replace(record, parent_turn=str(uuid4()))),
                            (attempt, replace(record, reviewers=((managed_fixtures.CHILD, str(uuid4())),)))):
                with self.subTest(changed=changed[0]), mock.patch.object(RequestRepository, "read",
                        side_effect=[(attempt, record), changed]):
                    value = fixture.inspect()
                self.assertTrue(value["ok"])
                self.assertEqual(value["observation"]["worker"]["reason"], "attempt_changed")
                self.assertNotIn("read_diagnostic", value)
            with mock.patch.object(fixture.session.managed, "observe", side_effect=OSError(fixtures.SECRET)):
                value = fixture.inspect()
            self.assertEqual(value["observation"]["worker"]["reason"], "observation_unavailable")
            self.assertNotIn("read_diagnostic", value)
            self.assertNotIn(fixtures.SECRET, json.dumps(value))
            controller = worker.wait.read().controller
            conflicting = type(controller)(replace(controller.binding,
                target_generation=controller.binding.target_generation + 1), controller.reviewers, controller.reservation)
            reader = mock.Mock()
            reader.read.return_value = SimpleNamespace(controller=conflicting)
            with mock.patch.object(ReviewWaitRepository, "open_existing", return_value=reader):
                value = fixture.inspect()
            self.assertEqual(value["observation"]["worker"]["reason"], "binding_mismatch")
            self.assertNotIn("read_diagnostic", value)
            for attr in ("_owned_probe_id", "_owned_worker_id"):
                with self.subTest(attr=attr), mock.patch.object(worker, attr, str(uuid4())):
                    value = fixture.inspect()
                self.assertEqual(value["observation"]["worker"]["reason"], "ownership_mismatch")
                self.assertNotIn("read_diagnostic", value)
            expected = (identity["probe_id"], snapshot["observation"]["worker"]["worker_id"])
            for wrong in ((str(uuid4()), expected[1]), (expected[0], str(uuid4()))):
                with mock.patch.object(worker, "_read_diagnostic_identity", wrong):
                    value = fixture.inspect()
                self.assertEqual(value["observation"]["worker"]["status"], "stopped")
                self.assertNotIn("read_diagnostic", value)
            self.assertIn("read_diagnostic", fixture.inspect())
            with mock.patch.dict(fixture.session.sessions, {}, clear=True):
                self.assertNotIn("read_diagnostic", fixture.inspect())
            self.assertEqual(fixture.effects, ["create", "arm", "pause"] if purpose == "parent_state"
                             else ["create", "arm", "delete", "send"])
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
