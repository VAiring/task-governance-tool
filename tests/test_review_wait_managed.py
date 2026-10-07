"""Single-call waiting and automatic receipts, with isolated state/fake hosts."""

from dataclasses import replace
from datetime import timedelta
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from tests.test_review_wait_service import BINDING, CHILD, TURN, PARENT, OTHER, NOW
from tests.test_review_wait_direct import DirectHost, ORIGINAL, NEW_TURN
from tests.test_review_wait_host import read_result, write_config, config_values, AUTOMATION, RULE
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_server import ReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn, HostAdapterError
from task_governance_tool.review_wait_runtime.request_repository import RequestRepository
from task_governance_tool.review_wait_runtime.managed_host import ManagedHost, fallback_prompt
from task_governance_tool.state_resolver import canonical_state_paths


TASK = "tg_task_1111111111111111"
META = {"threadId": PARENT, "turnId": ORIGINAL, "private": "DO_NOT_SAVE_THIS"}


class ManagedHostTests(unittest.TestCase):
    def host(self, home=None):
        return ManagedHost(metadata=META, task_id=TASK, automation_id=AUTOMATION,
            codex_home=home or Path.cwd(), confirmed_timezone="UTC", timezone_source="host_os",
            reviewer_ids=[CHILD], command=["offline-peer"])

    def test_creation_has_fixed_destination_prompt_and_paused_status(self):
        host = self.host()
        receipt = ("card", json.dumps({"automationId": AUTOMATION, "mode": "create", "status": "PAUSED"}))
        with mock.patch.object(host, "_call", return_value=receipt) as call:
            self.assertEqual(AUTOMATION, host.create_heartbeat(RULE))
        name, arguments = call.call_args.args
        self.assertEqual("automation_update", name)
        self.assertEqual({"mode", "kind", "destination", "targetThreadId", "name", "prompt", "rrule", "status"}, set(arguments))
        self.assertEqual(("create", "heartbeat", "thread", PARENT, "PAUSED", RULE),
            tuple(arguments[k] for k in ("mode", "kind", "destination", "targetThreadId", "status", "rrule")))
        self.assertEqual(fallback_prompt(TASK), arguments["prompt"])
        self.assertNotIn("probe_id", arguments["prompt"])

    def test_unknown_create_response_is_not_retried(self):
        for response in [("card",), ("card", "{}"), ("card", json.dumps({"automationId": "../bad", "mode": "create", "status": "PAUSED"}))]:
            with self.subTest(response=response):
                host = self.host()
                with mock.patch.object(host, "_call", return_value=response) as call:
                    with self.assertRaisesRegex(HostAdapterError, "^heartbeat_create_unknown$"):
                        host.create_heartbeat(RULE)
                self.assertEqual(1, call.call_count)

    def receipt(self, host):
        value = read_result()
        value["thread"].update(id=PARENT, status={"type": "active"})
        value["turns"][0].update(id=NEW_TURN, status="inProgress", items=[{
            "type": "functionCallOutput", "id": "actual-event", "name": "send_message_to_thread",
            "namespace": "codex_app", "output": {"truncated": False, "text":
                "<codex_delegation>\n  <source_thread_id>" + PARENT + "</source_thread_id>\n  <input>"
                + host._direct_prompt(OTHER) + "</input>\n</codex_delegation>"}}])
        return value

    def test_only_matching_structured_event_in_new_real_parent_turn_is_receipt(self):
        host = self.host()
        value = self.receipt(host)
        with mock.patch.object(host, "_json_call", return_value=value) as call:
            self.assertEqual(NEW_TURN, host.observe_receipt(OTHER, ORIGINAL))
        self.assertEqual(PARENT, call.call_args.args[1]["threadId"])
        for edit in (lambda v: v["turns"][0].update(id=ORIGINAL),
                     lambda v: v["turns"][0]["items"][0].update(type="agentMessage"),
                     lambda v: v["turns"][0]["items"][0].update(namespace="unrelated"),
                     lambda v: v["turns"][0]["items"][0]["output"].update(truncated=True),
                     lambda v: v["turns"][0]["items"][0]["output"].update(text="unrelated input")):
            altered = copy.deepcopy(value)
            edit(altered)
            with mock.patch.object(host, "_json_call", return_value=altered):
                self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL))
        value["thread"]["id"] = CHILD
        with mock.patch.object(host, "_json_call", return_value=value):
            with self.assertRaises(HostAdapterError):
                host.observe_receipt(OTHER, ORIGINAL)

    def test_created_config_must_bind_same_parent_and_fixed_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = self.host(root)
            values = {**config_values(), "status": "PAUSED", "prompt": fallback_prompt(TASK)}
            write_config(root, values)
            with mock.patch.object(host, "_call", return_value=("card",)):
                self.assertEqual("PAUSED", host.verify_created(RULE).status)
                write_config(root, {**values, "prompt": "unrelated"})
                with self.assertRaises(HostAdapterError):
                    host.verify_created(RULE)


class ManagedFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-managed-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = canonical_state_paths(self.root / "skill", repo=self.root)
        self.paths.fixed_root.mkdir(parents=True)
        self.basis = replace(BINDING, task_id=TASK)
        self.now = NOW
        self.enabled = True
        self.current_parent = ChildTurn(PARENT, ORIGINAL, "inProgress", "active")
        self.child = ChildTurn(CHILD, TURN, "inProgress", "active")
        self.hosts, self.effects = {}, []
        self.fail_create = self.fail_arm = self.fail_delete = self.fail_receipt = False
        self.auto_receive = True
        self.sessions = []
        self.session = self.make()
        self.addCleanup(self.close_all)

    def close_all(self):
        for session in self.sessions:
            session.close()

    def factory(self, **kwargs):
        automation = kwargs["automation_id"]
        if automation != "pending" and automation in self.hosts:
            return self.hosts[automation]
        fixture = self
        class Host(DirectHost):
            def read_child(self, child):
                return fixture.child

            def read_parent(self):
                return fixture.current_parent

            def resolve_reviewer_ids(self):
                return (CHILD,)

            def create_heartbeat(self, rule):
                fixture.effects.append("create")
                if fixture.fail_create:
                    raise HostAdapterError("heartbeat_create_unknown")
                self.automation_id = "managed-" + str(len(fixture.hosts) + 1)
                self.value = replace(self.value, id=self.automation_id, rule=rule, timezone_source="host_node_intl")
                fixture.hosts[self.automation_id] = self
                return self.automation_id

            def verify_created(self, rule):
                return self.value

            def update_heartbeat(self, before, action, **kwargs):
                fixture.effects.append(action)
                if action == "arm" and fixture.fail_arm:
                    raise HostAdapterError("heartbeat_update_unknown")
                return super().update_heartbeat(before, action, **kwargs)

            def delete_heartbeat(self, before, *, cleanup=False):
                fixture.effects.append("delete")
                self.fail_delete = fixture.fail_delete
                return super().delete_heartbeat(before)

            def send_direct_probe(self, probe):
                fixture.effects.append("send")
                self.sends.append(probe)
                if fixture.auto_receive:
                    fixture.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
                return self.outcome

            def observe_receipt(self, probe, original):
                if fixture.fail_receipt:
                    raise HostAdapterError("invalid_host_response")
                if fixture.auto_receive and probe in self.sends and fixture.current_parent.turn_id != original:
                    return fixture.current_parent.turn_id
                return None
        return Host()

    def make(self):
        def session_factory(*args, **kwargs):
            direct_type = kwargs.pop("direct_factory")
            return ReviewWaitSession(*args, **kwargs,
                direct_factory=lambda *a, **kw: direct_type(*a, **kw, cycle_seconds=0.01, join_seconds=5))
        session = ProjectReviewWaitSession(ProjectConfig(self.root, self.root / "server.mjs", self.root, "UTC"),
            managed_host_factory=self.factory, session_factory=session_factory, clock=lambda: self.now,
            basis_factory=lambda repo, task, parent, wait, **kwargs: lambda: replace(self.basis, wait_id=wait))
        session._enabled = lambda: self.enabled
        session._location = lambda **kwargs: self.paths
        self.sessions.append(session)
        return session

    def call(self, operation="wait", *, session=None, metadata=None, **args):
        if operation == "wait":
            args.setdefault("reviewer_ids", ["/root/actual_reviewer"])
        return (session or self.session).handle(operation, {"task_id": TASK, **args}, metadata or META)

    def inspect(self):
        return self.call("inspect")

    def until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(condition())

    def ended(self):
        self.child = ChildTurn(CHILD, TURN, "failed", "systemError")
        self.current_parent = ChildTurn(PARENT, ORIGINAL, "completed", "idle")

    def test_one_wait_call_covers_create_prepare_start_send_and_receipt(self):
        result = self.call()
        self.assertEqual({"ok": True, "status": "waiting", "task_id": TASK, "parent_may_end": True, "replayed": False}, result)
        self.assertEqual(["create", "arm"], self.effects)
        self.ended()
        self.until(lambda: self.inspect().get("delivery", {}).get("acknowledged_turn") == NEW_TURN)
        self.assertEqual(["create", "arm", "delete", "send"], self.effects)
        self.assertEqual("accepted", self.inspect()["delivery"]["status"])
        for path in self.paths.review_wait_root.glob("*.sqlite"):
            self.assertNotIn(b"DO_NOT_SAVE_THIS", path.read_bytes())
            self.assertNotIn(b"/root/actual_reviewer", path.read_bytes())

    def test_duplicate_request_returns_same_readiness_without_host_effect(self):
        self.assertTrue(self.call()["ok"])
        self.assertTrue(self.call()["replayed"])
        self.assertEqual(["create", "arm"], self.effects)

    def test_unknown_creation_blocks_duplicates_and_restart(self):
        self.fail_create = True
        self.assertEqual("heartbeat_create_unknown", self.call()["error"])
        self.assertFalse(self.call()["ok"])
        self.assertFalse(self.call(session=self.make())["ok"])
        self.assertEqual(["create"], self.effects)
        self.assertEqual("unknown", self.inspect()["phase"])

    def test_unknown_arm_blocks_competing_cleanup_or_new_wait(self):
        self.fail_arm = True
        self.assertFalse(self.call()["ok"])
        self.assertFalse(self.call("stop")["ok"])
        self.current_parent = replace(self.current_parent, turn_id=NEW_TURN)
        self.assertFalse(self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})["ok"])
        self.assertEqual(["create", "arm"], self.effects)

    def test_unknown_delete_never_sends_or_creates_replacement(self):
        self.fail_delete = True
        self.assertTrue(self.call()["ok"])
        self.ended()
        self.until(lambda: self.inspect()["delivery"]["timer_phase"] == "unknown")
        self.assertFalse(self.call("stop")["ok"])
        self.assertEqual(["create", "arm", "delete"], self.effects)

    def test_normal_ten_minute_check_rewait_cleans_and_creates_with_one_call(self):
        self.assertTrue(self.call()["ok"])
        self.now += timedelta(minutes=10)
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        result = self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})
        self.assertTrue(result["parent_may_end"], result)
        self.assertEqual(["create", "arm", "pause", "delete", "create", "arm"], self.effects)
        sequence, record = RequestRepository(self.paths.review_wait_request_store(PARENT, TASK)).read()
        self.assertEqual(2, sequence)
        self.assertEqual(NEW_TURN, record.parent_turn)

    def test_check_with_finished_reviews_cleans_without_new_reservation(self):
        self.call()
        self.child = ChildTurn(CHILD, TURN, "completed", "idle")
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        result = self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})
        self.assertEqual("reviews_ended", result["status"])
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)

    def test_restart_and_inspection_never_resume_worker(self):
        self.call()
        self.session.close()
        before = list(self.effects)
        restarted = self.make()
        self.assertTrue(self.call("inspect", session=restarted)["ok"])
        self.assertFalse(self.call(session=restarted)["ok"])
        self.assertEqual(before, self.effects)

    def test_disabled_wrong_parent_invalid_input_have_no_creation(self):
        self.enabled = False
        self.assertFalse(self.call()["ok"])
        self.enabled = True
        self.assertFalse(self.call(metadata={"threadId": OTHER, "turnId": ORIGINAL})["ok"])
        self.assertFalse(self.call(reviewer_ids=[])["ok"])
        self.assertFalse(self.call(reviewer_ids=[CHILD, CHILD])["ok"])
        self.assertEqual([], self.effects)

    def test_receipt_read_failure_preserves_accepted_but_not_received(self):
        self.fail_receipt = True
        self.call()
        self.ended()
        self.until(lambda: self.inspect()["delivery"]["status"] == "accepted")
        time.sleep(0.05)
        self.assertIsNone(self.inspect()["delivery"]["acknowledged_turn"])
        self.assertEqual(["create", "arm", "delete", "send"], self.effects)

    def test_explicit_stop_deletes_known_timer_and_stays_stopped(self):
        self.call()
        self.assertEqual("stopped", self.call("stop")["status"])
        self.assertEqual("stopped", self.call("stop")["status"])
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)
        self.assertFalse(self.call()["ok"])

    def test_off_while_running_preserves_bounded_cleanup_and_optional_stop(self):
        self.call()
        self.enabled = False
        self.until(lambda: self.inspect()["delivery"]["timer_phase"] == "paused")
        self.assertTrue(self.call("stop")["ok"])
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)

    def test_unknown_send_is_not_replaced_even_after_a_new_parent_turn(self):
        self.auto_receive = False
        self.call()
        self.hosts["managed-1"].outcome = "unknown"
        self.ended()
        self.until(lambda: self.inspect()["delivery"]["status"] == "unknown")
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertFalse(self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})["ok"])
        self.assertFalse(self.call("stop")["ok"])
        self.assertEqual(["create", "arm", "delete", "send"], self.effects)

    def test_send_becoming_unknown_during_stop_cannot_close_or_replace_wait(self):
        self.auto_receive = False
        self.assertTrue(self.call()["ok"])
        self.hosts["managed-1"].outcome = "unknown"
        original_session = self.session._session

        def finish_send_before_cancel(*args, **kwargs):
            # Cleanup has read waiting/active, but has not joined the worker.
            self.ended()
            self.until(lambda: self.inspect()["delivery"]["status"] == "unknown")
            return original_session(*args, **kwargs)

        with mock.patch.object(self.session, "_session", side_effect=finish_send_before_cancel):
            self.assertFalse(self.call("stop")["ok"])
        self.assertEqual("started", self.inspect()["phase"])
        self.assertEqual("unknown", self.inspect()["delivery"]["status"])
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertFalse(self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})["ok"])
        self.assertEqual(["create", "arm", "delete", "send"], self.effects)

    def test_basis_change_after_creation_cleans_paused_timer_without_start(self):
        factory = self.session.managed_host_factory
        def changed(**kwargs):
            host = factory(**kwargs)
            create = host.create_heartbeat
            def change(rule):
                result = create(rule)
                self.basis = replace(self.basis, target_generation=2)
                return result
            if kwargs["automation_id"] == "pending":
                host.create_heartbeat = change
            return host
        self.session.managed_host_factory = changed
        self.assertFalse(self.call()["ok"])
        self.assertEqual(["create", "delete"], self.effects)
        self.assertEqual("closed", self.inspect()["phase"])

    def test_missing_request_with_existing_lock_does_not_recreate(self):
        self.call()
        path = self.paths.review_wait_request_store(PARENT, TASK)
        path.unlink()  # Only this test's disposable store.
        self.assertFalse(self.call()["ok"])
        self.assertFalse(path.exists())
        self.assertEqual(["create", "arm"], self.effects)

    def test_inspection_is_read_only_and_competing_writer_does_not_create(self):
        self.assertEqual("absent", self.inspect()["status"])
        self.assertFalse(self.paths.review_wait_root.exists())
        self.call()
        path = self.paths.review_wait_request_store(PARENT, TASK)
        before = path.read_bytes()
        self.inspect()
        self.assertEqual(before, path.read_bytes())
        with RequestRepository(path).serial():
            self.assertFalse(self.call()["ok"])
        self.assertEqual(["create", "arm"], self.effects)
