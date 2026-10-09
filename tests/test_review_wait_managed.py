"""Single-call waiting and automatic receipts, with isolated state/fake hosts."""

from dataclasses import asdict, replace
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
TARGET = {"contract_revision": 1, "target_kind": "git_snapshot", "target_value": "sha256:" + "a" * 64,
          "target_base_revision": "b" * 40, "target_generation": 2}


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

    def test_untruncated_report_receipt_requires_exact_body(self):
        host = self.host()
        report = {"task_id": TASK, "target": TARGET, "status": "attention_required", "registered_findings": [{"summary": "確認"}],
                  "commit_id": None, "next_action": "Repair finding"}
        host.set_finalization_result(OTHER, report)
        value = self.receipt(host)
        with mock.patch.object(host, "_json_call", return_value=value) as call:
            self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)
        self.assertEqual(call.call_args.args[1]["maxOutputCharsPerItem"], 20000)
        altered = copy.deepcopy(value)
        altered["turns"][0]["items"][0]["output"]["text"] = altered["turns"][0]["items"][0]["output"]["text"].replace("Repair finding", "Report completion")
        with mock.patch.object(host, "_json_call", return_value=altered):
            self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL))

    def report(self, summary):
        return {"task_id": TASK, "target": TARGET, "status": "attention_required", "task_title": "結果通知",
            "recorded_work": {"checkpoint": {"summary": "記録済みの作業"}}, "verification": {"result": "pass"},
            "originals": [{"reviewer": "one", "verdict": "pass", "summary": summary, "findings": []},
                          {"reviewer": "two", "verdict": "changes_requested", "summary": "要修正",
                           "findings": [{"severity": "high", "summary": "高"}, {"severity": "low", "summary": "低"}]}],
            "registered_findings": [{"review_finding_id": "finding-one", "summary": summary}],
            "registered_receipt_ids": ["receipt-one", "receipt-two"], "completion_gate": {"ready": False},
            "stages": {"registration": "succeeded", "commit": "not_started", "completion": "not_started"},
            "commit_id": None, "unavailable": [], "next_action": "Fix all findings"}

    def bounded_receipt(self, host, maximum=20000):
        value = self.receipt(host)
        output = value["turns"][0]["items"][0]["output"]
        raw = output["text"].encode("utf-16-le")
        if len(raw) // 2 > maximum:
            # Measured public read_thread output, including a split surrogate.
            output.update(text=raw[:maximum * 2].decode("utf-16-le", errors="surrogatepass"),
                          truncated=True, originalChars=len(raw) // 2)
        return value

    def test_full_results_survive_read_boundaries_and_all_outcomes(self):
        for outcome in ("completed", "findings", "failed", "interrupted", "missing", "partial"):
            for size in (17999, 18000, 18001, 19999, 20000, 20001, 36000):
                with self.subTest(outcome=outcome, size=size):
                    report = self.report(("日本語😀" * (size // 5 + 1)))
                    report["status"] = outcome
                    if outcome == "missing":
                        report["originals"][1] = {"status": "unavailable", "reason": "missing_original"}
                        report["unavailable"] = ["original_two"]
                    if outcome == "partial":
                        report["commit_id"] = "c" * 40
                        report["stages"].update(commit="succeeded", completion="not_started")
                    host = self.host()
                    host.set_finalization_result(OTHER, report)
                    prompt = host._direct_prompt(OTHER)
                    payload = json.loads(prompt.splitlines()[2])
                    self.assertEqual({k: v for k, v in payload.items() if k != "delivery"}, report)
                    self.assertEqual(payload["delivery"], {"source_body": "complete", "omitted_fields": [],
                                                          "receipt_scope": "notification_correlation_only"})
                    self.assertNotIn("--check", prompt)
                    with mock.patch.object(host, "_request", return_value={"content": [], "isError": False}) as send:
                        self.assertEqual(host.send_direct_probe(OTHER), "accepted")
                    self.assertEqual(send.call_args.args[1]["prompt"], prompt)
                    with mock.patch.object(host, "_json_call", return_value=self.bounded_receipt(host)) as read:
                        self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)
                    self.assertEqual(read.call_args.args[1]["maxOutputCharsPerItem"], 20000)

    def test_truncated_event_requires_complete_identity_and_matching_host_structure(self):
        host = self.host()
        host.set_finalization_result(OTHER, self.report("😀" * 14000))
        value = self.bounded_receipt(host)
        output = value["turns"][0]["items"][0]["output"]
        self.assertTrue(output["truncated"])
        for edit in (
            lambda v: v["turns"][0].update(id=ORIGINAL),
            lambda v: v["turns"][0]["items"][0].update(type="agentMessage"),
            lambda v: v["turns"][0]["items"][0].update(namespace="other"),
            lambda v: v["turns"][0]["items"][0].update(name="echo"),
            lambda v: v["turns"][0]["items"][0]["output"].update(originalChars=True),
            lambda v: v["turns"][0]["items"][0]["output"].pop("originalChars"),
            lambda v: v["turns"][0]["items"][0]["output"].update(originalChars=output["originalChars"] + 1),
            lambda v: v["turns"][0]["items"][0]["output"].update(truncated=False),
            lambda v: v["turns"][0]["items"][0]["output"].update(text=output["text"][:250]),
            lambda v: v["turns"][0]["items"][0]["output"].update(text=output["text"].replace(PARENT, CHILD)),
            lambda v: v["turns"][0]["items"][0]["output"].update(text=output["text"].replace(OTHER, CHILD)),
            lambda v: v["turns"][0]["items"][0]["output"].update(text=output["text"].replace(TASK, "tg_task_2222222222222222")),
            lambda v: v["turns"][0]["items"][0]["output"].update(text=output["text"].replace('"target_generation":2', '"target_generation":3')),
            lambda v: v["turns"][0]["items"][0]["output"].update(text=output["text"].replace("記録済み", "未確認の")),
        ):
            changed = copy.deepcopy(value)
            edit(changed)
            with self.subTest(edit=edit), mock.patch.object(host, "_json_call", return_value=changed):
                self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL))
        value["thread"]["id"] = CHILD
        with mock.patch.object(host, "_json_call", return_value=value), self.assertRaises(HostAdapterError):
            host.observe_receipt(OTHER, ORIGINAL)

    def test_truncated_correlation_does_not_claim_unseen_body_integrity(self):
        host = self.host()
        host.set_finalization_result(OTHER, self.report("x" * 25000))
        value = self.receipt(host)
        output = value["turns"][0]["items"][0]["output"]
        text = output["text"]
        output.update(text=text[:22000] + "y" + text[22001:])
        output.update(text=output["text"][:20000], truncated=True, originalChars=len(text))
        with mock.patch.object(host, "_json_call", return_value=value):
            self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)
        self.assertEqual(json.loads(host._direct_prompt(OTHER).splitlines()[2])["delivery"]["receipt_scope"],
                         "notification_correlation_only")

    def test_only_actual_local_relay_limit_produces_explicit_delivery_failure(self):
        host = self.host()
        report = self.report("日" * 90000)
        host.set_finalization_result(OTHER, report)
        prompt = host._direct_prompt(OTHER)
        payload = json.loads(prompt.splitlines()[2])
        self.assertEqual(payload["delivery"]["source_body"], "incomplete")
        self.assertEqual(payload["delivery"]["limitation"], "local_relay_frame_limit")
        self.assertEqual(payload["delivery"]["max_frame_bytes"], 262144)
        self.assertEqual(set(payload["delivery"]["omitted_fields"]), set(report) - set(payload))
        self.assertIn("--check", payload["delivery"]["recovery"])
        self.assertEqual(payload["stages"], report["stages"])

    def test_exact_utf16_event_boundary_and_split_surrogate(self):
        report = self.report("")
        # A trailing field avoids changing the number of repeated summary fields.
        report["detail"] = ""
        empty = self.host()
        empty.set_finalization_result(OTHER, report)
        overhead = len(self.receipt(empty)["turns"][0]["items"][0]["output"]["text"].encode("utf-16-le")) // 2
        for size in (17999, 18000, 18001, 19999, 20000, 20001, 24000):
            host = self.host()
            host.set_finalization_result(OTHER, {**report, "detail": "日" * (size - overhead)})
            value = self.bounded_receipt(host)
            self.assertEqual(value["turns"][0]["items"][0]["output"]["truncated"], size > 20000)
            with mock.patch.object(host, "_json_call", return_value=value):
                self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)
        for padding in ("", "日"):
            host = self.host()
            host.set_finalization_result(OTHER, {**report, "detail": padding + "😀" * 14000})
            value = self.bounded_receipt(host)
            with mock.patch.object(host, "_json_call", return_value=value):
                self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)

    def test_missing_or_invalid_source_identity_never_creates_a_receipt_header(self):
        for target in (None, {}, {**TARGET, "target_generation": True}, {**TARGET, "target_value": "unknown"}):
            host = self.host()
            with self.assertRaises(HostAdapterError):
                host.set_finalization_result(OTHER, {**self.report("test"), "target": target})
            self.assertNotIn(OTHER, host._receipt_headers)


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

    def integrated_observer(self):
        from task_governance_tool.review_finalization import Finalizer
        from task_governance_tool.review_finalization_repository import FinalizationRecord, FinalizationRepository
        self.basis = replace(self.basis, execution_id="tg_execution_" + "2" * 16,
                             artifact_manifest_id="tg_artifact_manifest_" + "3" * 16,
                             target_kind="git_snapshot", target_base_revision="a" * 40)
        basis = asdict(self.basis)
        basis.pop("wait_id")
        basis.update(version=1, task_status="review_pending")
        record = FinalizationRecord(basis, "reviews/packet.json", "c" * 64, ("reviews/one.json",), "refs/heads/main")
        journal = FinalizationRepository(self.root / "finalization.sqlite")
        with journal.serial(initial=record):
            pass
        # Actual observation writer and journal; no fake PASS or Git effects.
        class ObservationOnly:
            observe_reviewers = Finalizer.observe_reviewers
        observer = ObservationOnly()
        observer.journal = journal
        self.session._finalizer = lambda binding: observer
        return journal

    def test_scheduled_failed_reviewer_is_retained_before_reviews_ended(self):
        journal = self.integrated_observer()
        self.assertTrue(self.call()["ok"])
        self.assertEqual(journal.read().reviewer_observations, ())
        self.child = ChildTurn(CHILD, TURN, "failed", "systemError")
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        result = self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})
        self.assertEqual(result["status"], "reviews_ended", result)
        self.assertEqual(journal.read().reviewer_observations, ((CHILD, TURN, "failed"),))
        self.assertEqual(journal.read().reviewer_blocker, "finalization_reviewer_failed")
        self.assertEqual(self.effects, ["create", "arm", "pause", "delete"])

    def test_failed_status_is_retained_while_host_thread_is_still_active(self):
        journal = self.integrated_observer()
        self.child = ChildTurn(CHILD, TURN, "failed", "active")
        self.assertTrue(self.call()["ok"])
        self.assertEqual(journal.read().reviewer_observations, ((CHILD, TURN, "failed"),))
        self.assertEqual(journal.read().reviewer_blocker, "finalization_reviewer_failed")
        self.assertNotIn("send", self.effects)

    def test_scheduled_changed_turn_remains_bound_to_original_pair(self):
        journal = self.integrated_observer()
        self.assertTrue(self.call()["ok"])
        finalizer_factory = self.session._finalizer
        self.session.close()
        self.session = self.make()
        self.session._finalizer = finalizer_factory
        self.child = ChildTurn(CHILD, OTHER, "completed", "idle")
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        result = self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})
        self.assertFalse(result["ok"], result)
        self.assertEqual(journal.read().reviewer_observations, ((CHILD, TURN, "unknown"),))
        self.assertEqual(journal.read().reviewer_blocker, "finalization_reviewer_unknown")
        self.assertNotIn("send", self.effects)

    def test_initial_unavailable_read_blocks_without_inventing_turn(self):
        journal = self.integrated_observer()
        factory = self.session.managed_host_factory
        def unavailable(**kwargs):
            host = factory(**kwargs)
            host.read_child = mock.Mock(side_effect=ValueError("private provider failure"))
            return host
        self.session.managed_host_factory = unavailable
        self.assertFalse(self.call()["ok"])
        self.assertEqual(journal.read().reviewer_observations, ())
        self.assertEqual(journal.read().reviewer_blocker, "finalization_reviewer_unknown")
        self.assertEqual(self.effects, [])
        self.assertNotIn(b"private provider failure", journal.path.read_bytes())

    def test_healthy_scheduled_review_has_no_invented_failure(self):
        journal = self.integrated_observer()
        self.assertTrue(self.call()["ok"])
        self.child = ChildTurn(CHILD, TURN, "completed", "idle")
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertEqual(self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})["status"], "reviews_ended")
        self.assertEqual(journal.read().reviewer_observations, ((CHILD, TURN, "completed"),))
        self.assertIsNone(journal.read().reviewer_blocker)

    def preparation_handoff(self, status):
        journal = self.integrated_observer()
        factory = self.session.managed_host_factory
        reads = []
        def during_prepare(**kwargs):
            host = factory(**kwargs)
            def child(reviewer):
                reads.append(reviewer)
                if len(reads) == 1:
                    return self.child
                self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
                if status == "unavailable":
                    raise ValueError("private preparation read failure")
                return ChildTurn(CHILD, OTHER if status == "changed" else TURN,
                                 "completed" if status == "changed" else status, "idle")
            host.read_child = child
            return host
        self.session.managed_host_factory = during_prepare
        self.assertFalse(self.call()["ok"])
        self.assertEqual(len(reads), 2)
        self.assertEqual(self.effects, ["create", "delete"])
        expected_status = "unknown" if status in {"unavailable", "changed"} else status
        self.assertEqual(journal.read().reviewer_observations, ((CHILD, TURN, expected_status),))
        self.assertEqual(journal.read().reviewer_blocker, None if status == "completed" else
                         "finalization_reviewer_" + ("unknown" if expected_status == "unknown" else "failed"))
        self.assertNotIn(b"private preparation read failure", journal.path.read_bytes())

    def test_preparation_failed_reviewer_survives_startup_failure(self):
        self.preparation_handoff("failed")

    def test_preparation_interrupted_reviewer_survives_startup_failure(self):
        self.preparation_handoff("interrupted")

    def test_preparation_unavailable_reviewer_survives_startup_failure(self):
        self.preparation_handoff("unavailable")

    def test_preparation_changed_turn_preserves_original_pair(self):
        self.preparation_handoff("changed")

    def test_preparation_healthy_parent_handoff_does_not_invent_failure(self):
        self.preparation_handoff("completed")

    def test_closed_manual_wait_accepts_new_reviewer_turn(self):
        self.assertTrue(self.call()["ok"])
        self.assertEqual(self.call("stop")["status"], "stopped")
        self.child = ChildTurn(CHILD, OTHER, "inProgress", "active")
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        result = self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})
        self.assertTrue(result["parent_may_end"], result)
        self.assertEqual(self.effects, ["create", "arm", "pause", "delete", "create", "arm"])

    def test_active_manual_wait_rejects_new_reviewer_turn_before_cleanup(self):
        self.assertTrue(self.call()["ok"])
        self.child = ChildTurn(CHILD, OTHER, "inProgress", "active")
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertFalse(self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})["ok"])
        self.assertNotIn("delete", self.effects)
        self.assertEqual(self.effects.count("create"), 1)

    def test_closed_integrated_wait_still_preserves_bound_turn(self):
        journal = self.integrated_observer()
        self.assertTrue(self.call()["ok"])
        self.assertEqual(self.call("stop")["status"], "stopped")
        self.child = ChildTurn(CHILD, OTHER, "inProgress", "active")
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.assertFalse(self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})["ok"])
        self.assertEqual(journal.read().reviewer_observations, ((CHILD, TURN, "unknown"),))
        self.assertEqual(journal.read().reviewer_blocker, "finalization_reviewer_unknown")
        self.assertEqual(self.effects.count("create"), 1)

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
