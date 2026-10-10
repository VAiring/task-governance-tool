"""Canonical integration through physical installs and public Task helpers; fake host only."""

from dataclasses import replace
from io import BytesIO
import json
import os
import subprocess
import sys
import time
from unittest import mock

from tests.m14_test_support import file_snapshot
from tests.test_review_handoff_preparation import PreparationFixture
from tests.test_review_wait_direct import DirectHost, ORIGINAL, NEW_TURN
from tests.test_review_wait_service import CHILD, TURN, NOW, OTHER
from tests.test_review_wait_server import HELLO, READY, request
from tests.test_review_wait_host import dispatch_result
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_server import ReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_direct import DirectProbe
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn, PublicMcpHost
from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
from task_governance_tool.state_resolver import canonical_state_paths


class ProjectWaitTests(PreparationFixture):
    def setUp(self):
        identity = mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "11111111-1111-4111-8111-111111111111"})
        identity.start()
        self.addCleanup(identity.stop)
        super().setUp()
        self.cli("setup", "--review-wait", "on")
        self.task_id = self.task()
        completed, self.handoff = self.prepare(self.task_id)
        self.assertEqual(0, completed.returncode, completed.stdout)
        ownership = self.cli("task", "show", self.task_id)["task"]["ownership"]
        self.parent = ownership["owner_session_id"] or ownership["completion_session_id"]
        self.metadata = {"threadId": self.parent, "turnId": ORIGINAL, "private": "NEVER_SAVE_THIS"}
        self.paths = canonical_state_paths(self.install.skill_root, repo=self.root)
        self.hosts = {}
        self.instances = []
        self.session = self.make()
        self.addCleanup(self.close_all)

    def host(self, **kwargs):
        automation = kwargs["automation_id"]
        if automation not in self.hosts:
            host = DirectHost()
            host.automation_id = automation
            host.parent_thread_id = self.parent
            host.value = replace(host.value, id=automation, parent_thread_id=self.parent,
                                 timezone_source="host_node_intl")
            host.parent = ChildTurn(self.parent, ORIGINAL, "inProgress", "active")
            self.hosts[automation] = host
        return self.hosts[automation]

    def make(self):
        def session_factory(*args, **kwargs):
            direct_type = kwargs.pop("direct_factory", DirectProbe)
            return ReviewWaitSession(*args, **kwargs,
                direct_factory=lambda *a, **kw: direct_type(*a, **kw, cycle_seconds=0.01, join_seconds=5))
        session = ProjectReviewWaitSession(ProjectConfig(self.root, self.root / "server.mjs", self.root, "UTC"),
            host_factory=self.host, session_factory=session_factory, clock=lambda: NOW)
        session.skill = self.install.skill_root  # test-only physical install injection
        self.instances.append(session)
        return session

    def close_all(self):
        for session in self.instances:
            session.close()

    def call(self, operation, automation="timer-1", *, session=None, metadata=None, **args):
        return (session or self.session).handle(operation, {"automation_id": automation, **args},
                                               self.metadata if metadata is None else metadata)

    def prepare_wait(self, automation="timer-1"):
        result = self.call("prepare", automation, task_id=self.task_id, reviewer_ids=[CHILD])
        self.assertTrue(result["ok"], result)
        return result

    def until(self, predicate):
        deadline = time.monotonic() + 15
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(predicate())

    def test_physical_entry_discovery_is_inert_and_has_only_closed_controls(self):
        before = file_snapshot(self.root)
        entry = self.install.skill_root / "scripts/review_wait_server.py"
        result = subprocess.run([sys.executable, "-I", "-B", str(entry), "--repo", str(self.root),
            "--server", str(self.root / "server.mjs"), "--codex-home", str(self.root), "--timezone", "UTC"],
            input=HELLO + READY + request("tools/list"), capture_output=True, cwd=self.root, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        catalogue = json.loads(result.stdout.splitlines()[-1])["result"]["tools"]
        self.assertEqual(9, len(catalogue))
        self.assertNotIn("review_wait_direct_start", [tool["name"] for tool in catalogue])
        self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in catalogue))
        tools = {tool["name"]: tool for tool in catalogue}
        for operation in ("prepare", "view", "direct_delete_start", "direct_status", "direct_cancel", "direct_ack"):
            self.assertIn("Compatibility", tools["review_wait_" + operation]["description"])
        self.assertEqual({"task_id", "reviewer_ids"}, set(tools["review_wait_wait"]["inputSchema"]["properties"]))
        self.assertTrue(tools["review_wait_inspect"]["annotations"]["readOnlyHint"])
        self.assertEqual(before, file_snapshot(self.root))

    def test_prepare_canonical_isolated_associations_and_duplicate_exclusion(self):
        core = self.paths.database.read_bytes()
        self.prepare_wait()
        first = self.paths.review_wait_store("timer-1")
        self.assertTrue(first.is_file())
        self.assertEqual(self.paths.review_wait_root, first.parent)
        original = first.read_bytes()
        self.assertFalse(self.call("prepare", task_id=self.task_id, reviewer_ids=[CHILD])["ok"])
        self.prepare_wait("timer-2")
        self.assertEqual(original, first.read_bytes())
        self.assertEqual(core, self.paths.database.read_bytes())
        self.assertNotIn(b"NEVER_SAVE_THIS", original)
        self.assertEqual([], self.hosts["timer-1"].effects)
        self.assertFalse((self.root / "wait.sqlite").exists())

    def test_resident_preupgrade_ceiling_requires_reload_without_replaying_wait(self):
        from task_governance_tool import state_resolver
        self.prepare_wait()
        before = file_snapshot(self.root)
        # The running server imported this ceiling before the package/DB
        # upgrade. A successful fresh CLI does not replace that resident value.
        for old_ceiling in (25, 26):
            if old_ceiling >= state_resolver.SCHEMA_VERSION:
                continue
            with self.subTest(old_ceiling=old_ceiling), mock.patch.object(state_resolver, "SCHEMA_VERSION", old_ceiling):
                for result in (self.call("view"),
                               self.session.handle("inspect", {"task_id": self.task_id}, self.metadata)):
                    self.assertFalse(result["ok"])
                    self.assertEqual("review_wait_unavailable", result["error"])
                    self.assertEqual("project_admission", result["diagnostic"]["stage"])
                    self.assertEqual("schema_too_new", result["diagnostic"]["reason"])
                    self.assertEqual("not_dispatched", result["diagnostic"]["host_mutation"])
                    self.assertEqual("not_inspected", result["diagnostic"]["retained_effects"])
                self.assertEqual(before, file_snapshot(self.root))
        self.session.close()
        reloaded = self.make()
        self.assertTrue(self.call("view", session=reloaded)["ok"])
        self.assertFalse(self.call("direct_status", session=reloaded)["worker_alive"])
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual(self.hosts["timer-1"].effects, [])
        self.assertEqual(self.hosts["timer-1"].sends, [])

    def test_disabled_invalid_policy_and_identity_never_create_or_start(self):
        self.cli("setup", "--review-wait", "off")
        before = file_snapshot(self.root)
        result = self.call("prepare", task_id=self.task_id, reviewer_ids=[CHILD])
        self.assertEqual("review_wait_not_enabled", result["error"])
        self.assertFalse(self.call("direct_delete_start")["ok"])
        self.assertEqual(before, file_snapshot(self.root))
        self.cli("setup", "--review-wait", "on")
        before = file_snapshot(self.root)
        for metadata in ({}, {"threadId": OTHER, "turnId": ORIGINAL}):
            self.assertFalse(self.call("prepare", metadata=metadata,
                task_id=self.task_id, reviewer_ids=[CHILD])["ok"])
        self.assertEqual(before, file_snapshot(self.root))
        choices = self.install.skill_root / "config/setup-features.json"
        choices.write_bytes(b"invalid private config")
        self.assertEqual("review_wait_not_enabled", self.call("direct_delete_start")["error"])

    def test_other_parent_and_path_arguments_rejected_without_effect(self):
        self.prepare_wait()
        before = file_snapshot(self.root)
        for operation in ("view", "direct_status", "direct_delete_start"):
            self.assertFalse(self.call(operation, metadata={"threadId": OTHER, "turnId": ORIGINAL})["ok"])
        for automation in ("../elsewhere", "C:/other", "", "a/b", None):
            self.assertFalse(self.call("prepare", automation, task_id=self.task_id, reviewer_ids=[CHILD])["ok"])
        self.assertFalse(self.call("view", database="untrusted")["ok"])
        self.assertEqual(before, file_snapshot(self.root))

    def test_delete_send_ack_and_restart_never_replays(self):
        self.prepare_wait()
        started = self.call("direct_delete_start")
        self.assertTrue(started["ok"], started)
        probe = started["state"]["probe_id"]
        self.assertTrue(started["worker_alive"])
        host = self.hosts["timer-1"]
        host.turn = ChildTurn(CHILD, TURN, "completed", "idle")
        host.parent = ChildTurn(self.parent, ORIGINAL, "completed", "idle")
        self.until(lambda: self.call("direct_status")["state"]["status"] == "accepted")
        self.assertEqual(["arm", "delete"], host.effects)
        self.assertEqual([probe], host.sends)
        self.session.close()
        reopened = self.make()
        before = file_snapshot(self.root)
        self.assertFalse(self.call("direct_status", session=reopened)["worker_alive"])
        self.assertFalse(self.call("direct_delete_start", session=reopened)["ok"])
        self.assertEqual(before, file_snapshot(self.root))
        rejected = self.call("direct_ack", session=reopened, probe_id=probe,
            metadata={"threadId": self.parent, "turnId": NEW_TURN})
        self.assertFalse(rejected["ok"], rejected)
        host.parent = ChildTurn(self.parent, NEW_TURN, "inProgress", "active")
        result = self.call("direct_ack", session=reopened, probe_id=probe,
            metadata={"threadId": self.parent, "turnId": NEW_TURN})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["state"]["received"], result)
        self.assertEqual([probe], host.sends)

    def test_normal_dispatch_handle_prepare_start_delete_send_ack(self):
        returned_handle = "/root/normal_review"
        parent_activity = dispatch_result((returned_handle, CHILD))
        parent_activity["thread"].update(id=self.parent)
        parent_activity["turns"][0].update(id=ORIGINAL)
        real_host = PublicMcpHost(metadata=self.metadata, automation_id="timer-1",
            codex_home=self.root, confirmed_timezone="UTC", timezone_source="host_os",
            reviewer_ids=[returned_handle], command=["offline-public-host"])
        simulated = self.host(automation_id="timer-1")
        with mock.patch.object(real_host, "_json_call", return_value=parent_activity) as public_read, \
                mock.patch.object(simulated, "resolve_reviewer_ids", create=True,
                                  side_effect=real_host.resolve_reviewer_ids):
            prepared = self.call("prepare", task_id=self.task_id, reviewer_ids=[returned_handle])
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(2, public_read.call_count)
        controller = ReviewWaitRepository.open_existing(self.paths.review_wait_store("timer-1")).read().controller
        self.assertEqual([(CHILD, TURN)], [(r.reviewer_id, r.turn_id) for r in controller.reviewers])
        self.assertNotIn(returned_handle.encode(), self.paths.review_wait_store("timer-1").read_bytes())
        started = self.call("direct_delete_start")
        self.assertTrue(started["ok"], started)
        probe = started["state"]["probe_id"]
        simulated.turn = ChildTurn(CHILD, TURN, "completed", "idle")
        simulated.parent = ChildTurn(self.parent, ORIGINAL, "completed", "idle")
        self.until(lambda: self.call("direct_status")["state"]["status"] == "accepted")
        self.assertEqual(["arm", "delete"], simulated.effects)
        self.assertEqual([probe], simulated.sends)
        simulated.parent = ChildTurn(self.parent, NEW_TURN, "inProgress", "active")
        ack = self.call("direct_ack", probe_id=probe,
            metadata={"threadId": self.parent, "turnId": NEW_TURN})
        self.assertTrue(ack["ok"], ack)
        self.assertTrue(ack["state"]["received"])
        self.assertEqual([probe], simulated.sends)

    def test_off_stops_running_wait_but_preserves_inspection_and_cleanup(self):
        self.prepare_wait()
        started = self.call("direct_delete_start")
        self.assertTrue(started["ok"], started)
        self.cli("setup", "--review-wait", "off")
        self.until(lambda: not self.call("direct_status")["worker_alive"])
        host = self.hosts["timer-1"]
        self.assertEqual(["arm", "pause"], host.effects)
        self.assertEqual([], host.sends)
        self.assertTrue(self.call("view")["ok"])
        self.assertTrue(self.call("direct_cancel", probe_id=started["state"]["probe_id"])["ok"])
        self.assertEqual(["arm", "pause"], host.effects)

    def test_handoff_routes_policy_without_launch_or_supervisor_duty(self):
        route = self.handoff["handoff"]["review_wait"]
        self.assertEqual({"status": "enabled", "task_id": self.task_id,
            "wait_tool": "review_wait_wait", "guide": "references/review_wait.md#normal-wait"}, route)
        self.assertNotIn("wait_ended_command", self.handoff["handoff"])
        self.assertFalse(self.paths.review_wait_root.exists())
        for request_item in self.handoff["handoff"]["review_requests"]:
            self.assertNotIn("review_wait_prepare", request_item["request"])
            self.assertNotIn("wait-ended", request_item["request"])

    def test_single_wait_and_automatic_receipt_through_physical_install_basis(self):
        core = self.paths.database.read_bytes()
        def factory(**kwargs):
            host = self.host(**kwargs)
            if kwargs["automation_id"] == "pending":
                def create(rule):
                    host.automation_id = "managed-real-basis"
                    host.value = replace(host.value, id=host.automation_id, rule=rule)
                    self.hosts[host.automation_id] = host
                    host.effects.append("create")
                    return host.automation_id
                host.create_heartbeat = create
                host.verify_created = lambda rule: host.value
                host.observe_receipt = lambda probe, original, **kwargs: (
                    host.parent.turn_id if probe in host.sends and host.parent.turn_id == NEW_TURN else None)
                host.on_send = lambda probe: setattr(host, "parent", ChildTurn(self.parent, NEW_TURN, "inProgress", "active"))
            return host
        self.session.managed_host_factory = factory
        result = self.session.handle("wait", {"task_id": self.task_id, "reviewer_ids": [CHILD]}, self.metadata)
        self.assertTrue(result.get("parent_may_end"), result)
        host = self.hosts["managed-real-basis"]
        host.turn = ChildTurn(CHILD, TURN, "completed", "idle")
        host.parent = ChildTurn(self.parent, ORIGINAL, "completed", "idle")
        def received():
            state = self.session.handle("inspect", {"task_id": self.task_id}, self.metadata)
            return state.get("delivery", {}).get("acknowledged_turn") == NEW_TURN
        self.until(received)
        self.assertEqual(["create", "arm", "delete"], host.effects)
        self.assertEqual(1, len(host.sends))
        self.assertEqual(core, self.paths.database.read_bytes())
