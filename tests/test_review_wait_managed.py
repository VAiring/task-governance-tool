"""Single-call waiting and automatic receipts, with isolated state/fake hosts."""

from dataclasses import asdict, replace
from datetime import timedelta
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from uuid import uuid4
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

    def history(self, host, newest):
        """The real cursor walk includes the send-time lower boundary."""
        latest = copy.deepcopy(newest)
        latest["page"].update(hasMore=True, nextCursor="before-send")
        fence = read_result()
        fence["thread"]["id"] = PARENT
        fence["turns"][0].update(id=ORIGINAL, items=[])
        return mock.patch.object(host, "_json_call", side_effect=[latest, fence])

    def test_only_matching_structured_event_in_new_real_parent_turn_is_receipt(self):
        host = self.host()
        value = self.receipt(host)
        with self.history(host, value) as call:
            self.assertEqual(NEW_TURN, host.observe_receipt(OTHER, ORIGINAL))
        self.assertEqual(PARENT, call.call_args.args[1]["threadId"])
        for edit in (lambda v: v["turns"][0].update(id=ORIGINAL),
                     lambda v: v["turns"][0]["items"][0].update(type="agentMessage"),
                     lambda v: v["turns"][0]["items"][0].update(namespace="unrelated"),
                     lambda v: v["turns"][0]["items"][0]["output"].update(truncated=True),
                     lambda v: v["turns"][0]["items"][0]["output"].update(text="unrelated input")):
            altered = copy.deepcopy(value)
            edit(altered)
            with self.history(host, altered):
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
        with self.history(host, value) as call:
            self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)
        self.assertEqual(call.call_args.args[1]["maxOutputCharsPerItem"], 20000)
        altered = copy.deepcopy(value)
        altered["turns"][0]["items"][0]["output"]["text"] = altered["turns"][0]["items"][0]["output"]["text"].replace("Repair finding", "Report completion")
        with self.history(host, altered):
            self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL))

    def test_report_can_refresh_before_send_but_attempted_body_is_immutable(self):
        for outcome in ("accepted", "rejected", "unknown"):
            with self.subTest(outcome=outcome):
                host = self.host()
                report = self.report("Original")
                host.set_finalization_result(OTHER, report)
                header = host._receipt_headers[OTHER]
                with self.assertRaises(HostAdapterError):
                    host.set_finalization_result(OTHER, {**report, "target": {**TARGET, "target_generation": 3}})
                host.set_finalization_result(OTHER, {**report, "blocking_code": "review_target_mismatch"})
                self.assertEqual(header, host._receipt_headers[OTHER])
                expected = host._direct_prompt(OTHER)
                wire = {"content": [], "isError": outcome == "rejected"} if outcome != "unknown" else {}
                with mock.patch.object(host, "_request", return_value=wire) as send:
                    self.assertEqual(host.send_direct_probe(OTHER), outcome)
                    with self.assertRaises(HostAdapterError):
                        host.set_finalization_result(OTHER, report)
                    with self.assertRaises(HostAdapterError):
                        host.send_direct_probe(OTHER)
                    self.assertEqual(send.call_count, 1)
                    self.assertEqual(send.call_args.args[1]["prompt"], expected)
                self.assertEqual(host._direct_prompt(OTHER), expected)

    def test_readonly_refresh_does_not_erase_the_known_attempt_failure(self):
        from types import SimpleNamespace
        from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe
        for reason in ("finalization_originals_unavailable", "verification_required",
                       "finalization_findings_require_judgment"):
            with self.subTest(reason=reason):
                host = self.host()
                previous = {**self.report("known blocked attempt"), "ok": False, "blocking_code": reason}
                host.set_finalization_result(OTHER, previous)
                worker = object.__new__(ManagedDirectProbe)
                worker._finalization_prepared = True
                worker._finalization_result = previous
                worker._receipt_finalizer = mock.Mock()
                worker._receipt_finalizer.execute.return_value = {**previous, "ok": True, "blocking_code": None}
                worker._before_send(host, None, SimpleNamespace(probe_id=OTHER), 0)
                actual = json.loads(host._direct_prompt(OTHER).splitlines()[2])
                self.assertFalse(actual["ok"])
                self.assertEqual(actual["blocking_code"], reason)
                self.assertEqual(actual["stages"], previous["stages"])
                worker._receipt_finalizer.execute.assert_called_once_with(check=True)

    def test_saved_judgment_drift_preserves_bound_report_but_unknown_and_fence_fail_closed(self):
        from types import SimpleNamespace
        from task_governance_tool.review_finalization import FinalizationError
        from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe
        worker = object.__new__(ManagedDirectProbe)
        finalizer = mock.Mock()
        worker._finalizer = mock.Mock(return_value=finalizer)
        controller = SimpleNamespace(reviewers=((CHILD, TURN),))
        for reason in ("review_target_mismatch", "task_ownership_changed"):
            with self.subTest(reason=reason):
                finalizer.saved_results_ready.side_effect = FinalizationError(reason)
                self.assertEqual(worker._reviewers_to_observe(controller), controller.reviewers)
                finalizer.execute.assert_not_called()
        for error in (FinalizationError("review_continuation_conflict"),
                      FinalizationError("finalization_reviewer_unknown"), OSError("unavailable")):
            with self.subTest(error=type(error), reason=str(error)):
                finalizer.saved_results_ready.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    worker._reviewers_to_observe(controller)
                self.assertIs(raised.exception, error)
                finalizer.execute.assert_not_called()

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

    def test_same_response_summary_reference_and_precompletion_observation_survive_delivery(self):
        result = self.report("保持するレビュー本文")
        result["reviews"] = {"current_receipts": [{"review_receipt_id": "receipt-one",
                             "summary": "保持するレビュー本文"}]}
        result["originals"][0] = {"path": "reviews/one.json", "status": "available",
            "registered_receipt_id": "receipt-one", "review": {"reviewer": "one", "verdict": "pass",
            "summary_ref": "/reviews/current_receipts/0/summary", "provenance": {"model_state": "unknown"},
            "findings": [{"severity": "low", "summary": "原本Finding"}]}}
        result["completion_gate"] = {"observation": "before_completion", "status": "in_progress", "ready": True}
        host = self.host()
        host.set_finalization_result(OTHER, result)
        prompt = host._direct_prompt(OTHER)
        delivered = json.loads(prompt.splitlines()[2])
        self.assertEqual({key: value for key, value in delivered.items() if key != "delivery"}, result)
        self.assertEqual(delivered["originals"][0]["review"]["summary_ref"], "/reviews/current_receipts/0/summary")
        self.assertIn("completion_gateは完了前の観測", prompt)
        self.assertIn("追加読取りは不要", prompt)
        with self.history(host, self.receipt(host)):
            self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)

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
                    with self.history(host, self.bounded_receipt(host)) as read:
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
            with self.subTest(edit=edit), self.history(host, changed):
                self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL))
        value["thread"]["id"] = CHILD
        with mock.patch.object(host, "_json_call", return_value=value), self.assertRaises(HostAdapterError):
            host.observe_receipt(OTHER, ORIGINAL)

    def test_report_guidance_preserves_completed_details_and_attention_facts(self):
        import re
        from read_reference import read_reference

        completed = {**self.report("独立レビュー合格"), "ok": True, "status": "completed",
            "task_status": "done", "completion_gate": {"ready": True},
            "recorded_work": {"status": "not_recorded", "checkpoint": None, "recent_notes": [],
                              "coverage": "latest_checkpoint_and_recent_task_events"},
            "stages": {"registration": "succeeded", "commit": "succeeded", "completion": "succeeded"},
            "commit_id": "c" * 40, "blocking_code": None,
            "maintenance_warnings": [{"code": "viewer_refresh_failed"}],
            "next_action": "Report completion; do not repeat registration, commit or completion."}
        completed["originals"][1] = {"reviewer": "two", "verdict": "pass", "summary": "合格",
            "findings": [{"severity": "low", "summary": "補足の指摘"}]}
        completed["registered_findings"] = [{"review_finding_id": "finding-one", "severity": "low",
            "status": "resolved", "summary": "補足の指摘", "resolution_summary": "説明に反映済み"}]
        attention = {**copy.deepcopy(completed), "ok": False, "status": "attention_required",
                     "blocking_code": "finalization_unavailable", "next_action": "Inspect the retained result."}
        conflicting = {**copy.deepcopy(completed), "task_status": "in_progress",
                       "stages": {"registration": "succeeded", "commit": "succeeded", "completion": "unknown"}}
        for report in (completed, attention, conflicting):
            with self.subTest(status=report["status"], completion=report["stages"]["completion"]):
                host = self.host()
                host.set_finalization_result(OTHER, report)
                prompt = host._direct_prompt(OTHER)
                payload = json.loads(prompt.splitlines()[2])
                # Guidance never filters evidence or classifies completion from
                # status alone; all observed facts reach the caller unchanged.
                self.assertEqual({k: v for k, v in payload.items() if k != "delivery"}, report)
                links = re.findall(r"references/[a-z_]+\.md#[a-z-]+", "\n".join(prompt.splitlines()[3:]))
                self.assertEqual(links, ["references/task_workflow.md#continue-after-reviews"])
                self.assertTrue(read_reference(links[0], Path(__file__).resolve().parents[1] / "task-governance-tool"))

    def test_truncated_correlation_does_not_claim_unseen_body_integrity(self):
        host = self.host()
        host.set_finalization_result(OTHER, self.report("x" * 25000))
        value = self.receipt(host)
        output = value["turns"][0]["items"][0]["output"]
        text = output["text"]
        output.update(text=text[:22000] + "y" + text[22001:])
        output.update(text=output["text"][:20000], truncated=True, originalChars=len(text))
        with self.history(host, value):
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

    def test_reported_review_details_keep_explicit_overflow_notification(self):
        host = self.host()
        report = self.report("")
        report["reported_receipts"] = [dict(review_receipt_id=f"tg_review_receipt_{index:016x}",
            roles=["retained"], successor_receipt_ids=[], review_details=dict(status="available",
            provenance_version=2, checks=["日" * 1000] * 32, limitations=["別" * 1000] * 32))
            for index in range(2)]
        host.set_finalization_result(OTHER, report)
        payload = json.loads(host._direct_prompt(OTHER).splitlines()[2])
        self.assertEqual(payload["delivery"]["source_body"], "incomplete")
        self.assertEqual(payload["delivery"]["limitation"], "local_relay_frame_limit")
        self.assertIn("reported_receipts", payload["delivery"]["omitted_fields"])
        self.assertNotIn("reported_receipts", payload)

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
            with self.history(host, value):
                self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)
        for padding in ("", "日"):
            host = self.host()
            host.set_finalization_result(OTHER, {**report, "detail": padding + "😀" * 14000})
            value = self.bounded_receipt(host)
            with self.history(host, value):
                self.assertEqual(host.observe_receipt(OTHER, ORIGINAL), NEW_TURN)

    def test_missing_or_invalid_source_identity_never_creates_a_receipt_header(self):
        for target in (None, {}, {**TARGET, "target_generation": True}, {**TARGET, "target_value": "unknown"}):
            host = self.host()
            with self.assertRaises(HostAdapterError):
                host.set_finalization_result(OTHER, {**self.report("test"), "target": target})
            self.assertNotIn(OTHER, host._receipt_headers)

    def pages(self, host, turns, matches=()):
        pages = []
        for index, identity in enumerate(turns):
            value = self.receipt(host)
            value["turns"][0]["id"] = identity
            if identity not in matches:
                value["turns"][0]["items"] = []
            more = index + 1 < len(turns)
            value["page"].update(hasMore=more, nextCursor=f"page-{index + 1}" if more else None)
            pages.append(value)
        return pages

    def test_older_first_receipt_found_after_other_task_turns_and_duplicate_events(self):
        host = self.host()
        later, duplicate, first, pre_send = [str(uuid4()) for _ in range(4)]
        pages = self.pages(host, [later, duplicate, first, pre_send, ORIGINAL], (first, duplicate))
        # The latest pre-send turn belongs to B; admission's ORIGINAL is older.
        for _ in range(2):
            with mock.patch.object(host, "_json_call", side_effect=pages) as call:
                self.assertEqual(first, host.observe_receipt(OTHER, pre_send))
                self.assertEqual(call.call_count, 4)
                self.assertEqual([c.args[1].get("cursor") for c in call.call_args_list],
                                 [None, "page-1", "page-2", "page-3"])
                self.assertTrue(all(c.kwargs["timeout"] > 0 for c in call.call_args_list))

    def test_pre_send_event_or_missing_fence_never_confirms_receipt(self):
        host = self.host()
        pre_send = str(uuid4())
        for turns, matches in (([NEW_TURN, pre_send, ORIGINAL], (pre_send, ORIGINAL)),
                               ([NEW_TURN], (NEW_TURN,))):
            with mock.patch.object(host, "_json_call", side_effect=self.pages(host, turns, matches)):
                self.assertIsNone(host.observe_receipt(OTHER, pre_send))

    def test_receipt_search_is_bounded_and_rejects_cycles(self):
        host = self.host()
        pages = self.pages(host, [str(uuid4()) for _ in range(18)] + [ORIGINAL])
        with mock.patch.object(host, "_json_call", side_effect=pages) as call:
            self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL))
            self.assertEqual(call.call_count, 16)
        pages = self.pages(host, [NEW_TURN, NEW_TURN, ORIGINAL], (NEW_TURN,))
        with mock.patch.object(host, "_json_call", side_effect=pages):
            self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL))
        pages = self.pages(host, [NEW_TURN, str(uuid4()), ORIGINAL], (NEW_TURN,))
        pages[1]["page"]["nextCursor"] = pages[0]["page"]["nextCursor"]
        with mock.patch.object(host, "_json_call", side_effect=pages):
            self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL))
        with mock.patch.object(host, "_json_call") as call:
            self.assertIsNone(host.observe_receipt(OTHER, ORIGINAL, deadline=time.monotonic() - 1))
            call.assert_not_called()


class ManagedFlowTests(unittest.TestCase):
    def setUp(self):
        # This wait-only fixture has no installed collection preparation or
        # native Task store. Do not inherit the checkout's optional policy.
        self.enterContext(mock.patch("task_governance_tool.setup_feature_config.answer_collection_allowed",
                                     return_value=False))
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-managed-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = canonical_state_paths(self.root / "skill", repo=self.root)
        self.paths.fixed_root.mkdir(parents=True)
        self.basis = replace(BINDING, task_id=TASK)
        self.bases, self.children, self.received = {}, {}, {}
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
        task = kwargs["task_id"]
        if automation != "pending" and automation in self.hosts:
            return self.hosts[automation]
        fixture = self
        class Host(DirectHost):
            def read_child(self, child):
                return fixture.children.get(task, fixture.child)

            def read_parent(self):
                return fixture.current_parent

            def resolve_reviewer_ids(self):
                return (fixture.children.get(task, fixture.child).child_id,)

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
                    turn = NEW_TURN if not fixture.received and fixture.current_parent.turn_id != NEW_TURN else str(uuid4())
                    fixture.received[probe] = turn
                    fixture.current_parent = ChildTurn(PARENT, turn, "inProgress", "active")
                return self.outcome

            def observe_receipt(self, probe, original, *, deadline=None):
                if fixture.fail_receipt:
                    raise HostAdapterError("invalid_host_response")
                if fixture.auto_receive and probe in self.sends:
                    return fixture.received.get(probe)
                return None
        return Host()

    def make(self):
        def session_factory(*args, **kwargs):
            direct_type = kwargs.pop("direct_factory")
            return ReviewWaitSession(*args, **kwargs,
                direct_factory=lambda *a, **kw: direct_type(*a, **kw, cycle_seconds=0.01, join_seconds=5))
        session = ProjectReviewWaitSession(ProjectConfig(self.root, self.root / "server.mjs", self.root, "UTC"),
            managed_host_factory=self.factory, session_factory=session_factory, clock=lambda: self.now,
            basis_factory=lambda repo, task, parent, wait, **kwargs: lambda:
                replace(self.bases.get(task, self.basis), task_id=task, wait_id=wait))
        session._enabled = lambda: self.enabled
        session._location = lambda **kwargs: self.paths
        # This fixture supplies its own live tracking source instead of a core
        # store. Production's reader is tested with issued collection sources.
        session._tracking_observer = lambda binding, reviewers: lambda: session._answer_tracking(binding, reviewers)
        def control_observer(paths, binding):
            previous = None
            def changed():
                nonlocal previous
                current = self.bases.get(binding.task_id, self.basis)
                result, previous = current != previous, current
                return result
            return changed
        session._control_observer = control_observer
        self.sessions.append(session)
        return session

    def call(self, operation="wait", *, session=None, metadata=None, task_id=TASK, **args):
        if operation == "wait":
            args.setdefault("reviewer_ids", ["/root/actual_reviewer"])
        return (session or self.session).handle(operation, {"task_id": task_id, **args}, metadata or META)

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

    def test_parent_end_during_public_reviewer_read_keeps_worker_and_timer(self):
        from tests.test_review_wait_host import adapter
        original_factory = self.factory
        calls, entered, release, block = [], threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def factory(**kwargs):
            fixture_host = original_factory(**kwargs)
            public = adapter(self.root, metadata=kwargs['metadata'], reviewer_ids=[CHILD])

            def snapshot(operation, arguments, **options):
                calls.append((operation, arguments))
                if block.is_set() and not entered.is_set():
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError('fixture synchronization timeout')
                    # The prior host route is cancelled by this exact event.
                    if operation == 'wait_threads' and self.current_parent.status == 'completed':
                        raise HostAdapterError('host_call_failed', boundary_reason='host_rpc_error',
                                               operation='wait_threads', termination='confirmed')
                value = read_result()
                value['thread'].update(id=CHILD, status={'type': self.child.thread_status})
                value['turns'][0].update(id=self.child.turn_id, status=self.child.status)
                return value

            public._json_call = snapshot
            fixture_host.read_child = public.read_child
            return fixture_host

        self.factory = factory
        self.session = self.make()
        self.assertTrue(self.call()['ok'])
        block.set()
        self.assertTrue(entered.wait(2))
        self.current_parent = ChildTurn(PARENT, ORIGINAL, 'completed', 'idle')
        release.set()
        count = len(calls)
        self.until(lambda: len(calls) > count)
        state = self.inspect()
        self.assertEqual((state['delivery']['status'], state['delivery']['timer_phase']), ('waiting', 'active'))
        self.assertEqual(state['observation']['worker']['status'], 'running')
        self.assertEqual(self.effects, ['create', 'arm'])
        self.child = ChildTurn(CHILD, TURN, 'completed', 'active')
        count = len(calls)
        self.until(lambda: len(calls) > count + 1)
        self.assertEqual(self.effects, ['create', 'arm'])
        self.child = ChildTurn(CHILD, TURN, 'completed', 'idle')
        self.until(lambda: self.inspect().get('delivery', {}).get('acknowledged_turn') == NEW_TURN)
        self.assertEqual(self.effects, ['create', 'arm', 'delete', 'send'])
        self.assertTrue(calls)
        for operation, arguments in calls:
            self.assertEqual((operation, arguments), ('read_thread', {'threadId': CHILD, 'hostId': 'local',
                'turnLimit': 1, 'includeOutputs': False, 'maxOutputCharsPerItem': 1}))

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
            def saved_results_ready(self):
                return False  # This fixture has no registered judgments.
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

    def test_registered_integrated_wait_reuses_saved_pairs_without_child_reads(self):
        journal = self.integrated_observer()
        self.assertEqual(self.call()["status"], "waiting")
        request_store = RequestRepository(self.paths.review_wait_request_store(PARENT, TASK))
        before = request_store.read()
        observer = self.session._finalizer(self.basis)
        # The native saved-result predicate is tested with real Receipts in
        # finalization tests. This fixture isolates wait/cleanup admission.
        observer.saved_results_ready = lambda: True
        original = self.factory
        admission_child_reads = []
        def no_fresh_child(**kwargs):
            host = original(**kwargs)
            if kwargs["automation_id"] == "pending":
                def unexpected_read(child):
                    admission_child_reads.append(child)
                    raise AssertionError("saved results need no fresh child activity")
                host.read_child = unexpected_read
            return host
        self.session.managed_host_factory = no_fresh_child
        wrong_actor = self.call(reviewer_ids=[OTHER])
        self.assertFalse(wrong_actor["ok"], wrong_actor)
        self.assertEqual(request_store.read(), before)
        self.assertEqual(self.effects, ["create", "arm"])
        duplicate = self.call()
        self.assertTrue(duplicate["replayed"], duplicate)
        self.assertEqual(request_store.read(), before)
        self.assertEqual(self.effects, ["create", "arm"])
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        result = self.call(metadata={"threadId": PARENT, "turnId": NEW_TURN})
        self.assertEqual(result["status"], "reviews_ended", result)
        self.assertFalse(result["parent_may_end"])
        attempt, request = request_store.read()
        self.assertEqual((attempt, request.reviewers), (before[0], before[1].reviewers))
        self.assertEqual(request.phase, "closed")
        self.assertEqual(self.effects, ["create", "arm", "pause", "delete"])
        self.assertEqual(journal.read().reviewer_observations, ())
        self.assertEqual(admission_child_reads, [])

    def test_preflight_observation_waits_for_short_worker_write_without_replaying(self):
        journal = self.integrated_observer()
        observer = self.session._finalizer(self.basis)
        entered, finished, errors = threading.Event(), threading.Event(), []
        def observe():
            entered.set()
            try:
                observer.observe_reviewers(((CHILD, TURN, "failed"),))
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()
        with journal.serial():
            worker = threading.Thread(target=observe)
            worker.start()
            self.assertTrue(entered.wait(2))
            self.assertFalse(finished.wait(0.05))
        worker.join(2)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(journal.read().reviewer_observations, ((CHILD, TURN, "failed"),))
        self.assertEqual(self.effects, [])

    def test_finalization_read_waits_for_short_observation_commit(self):
        journal = self.integrated_observer()
        entered, finished, outcomes = threading.Event(), threading.Event(), []
        def read():
            entered.set()
            try:
                outcomes.append(journal.read())
            except Exception as error:
                outcomes.append(error)
            finally:
                finished.set()
        with journal.serial(), journal._connection(write=True) as connection:
            # Hold the same SQLite exclusion window as an observation commit.
            connection.execute("COMMIT")
            connection.execute("BEGIN EXCLUSIVE")
            worker = threading.Thread(target=read)
            worker.start()
            self.assertTrue(entered.wait(2))
            self.assertFalse(finished.wait(0.05))
        worker.join(2)
        self.assertTrue(finished.is_set())
        self.assertEqual(outcomes, [journal.read()])

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

    def two_waits(self):
        second = "tg_task_2222222222222222"
        self.assertTrue(self.call()["parent_may_end"])
        # Work on B legitimately advances the same parent's actual turn.
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "inProgress", "active")
        self.children[second] = ChildTurn(OTHER, str(uuid4()), "inProgress", "active")
        self.assertTrue(self.call(task_id=second, metadata={"threadId": PARENT, "turnId": NEW_TURN})["parent_may_end"])
        self.assertEqual(self.inspect()["delivery"]["status"], "waiting")
        self.assertNotIn("pause", self.effects)
        return second

    def test_two_tasks_simultaneous_reverse_and_consecutive_completion_stay_bound(self):
        # Each iteration owns fresh disposable state and independent workers.
        for order in ("simultaneous", "a_first", "b_first"):
            with self.subTest(order=order):
                fixture = ManagedFlowTests()
                fixture.setUp()
                try:
                    second = fixture.two_waits()
                    if order != "b_first":
                        fixture.child = replace(fixture.child, status="completed", thread_status="idle")
                    if order != "a_first":
                        fixture.children[second] = replace(fixture.children[second], status="completed", thread_status="idle")
                    fixture.current_parent = replace(fixture.current_parent, status="completed", thread_status="idle")
                    fixture.until(lambda: len(fixture.received) == 1)
                    time.sleep(0.05)  # The other worker must defer while parent is busy.
                    self.assertEqual(fixture.effects.count("send"), 1)
                    fixture.child = replace(fixture.child, status="completed", thread_status="idle")
                    fixture.children[second] = replace(fixture.children[second], status="completed", thread_status="idle")
                    fixture.current_parent = replace(fixture.current_parent, status="completed", thread_status="idle")
                    fixture.until(lambda: len(fixture.received) == 2)
                    for task in (TASK, second):
                        fixture.until(lambda: fixture.call("inspect", task_id=task)["delivery"]["acknowledged_turn"] is not None)
                        record = fixture.call("inspect", task_id=task)["delivery"]
                        self.assertEqual(record["acknowledged_turn"], fixture.received[record["probe_id"]])
                    self.assertEqual(fixture.effects.count("delete"), 2)
                    self.assertEqual(fixture.effects.count("send"), 2)
                    self.assertEqual(fixture.effects.count("pause"), 0)
                finally:
                    fixture.doCleanups()

    def test_cancel_or_changed_target_of_one_task_preserves_other_wait(self):
        for change in ("cancel", "target"):
            with self.subTest(change=change):
                fixture = ManagedFlowTests()
                fixture.setUp()
                try:
                    second = fixture.two_waits()
                    if change == "cancel":
                        self.assertTrue(fixture.call("stop")["ok"])
                    else:
                        fixture.bases[TASK] = replace(fixture.basis, target_generation=2)
                        fixture.until(lambda: fixture.inspect()["delivery"]["status"] == "suppressed")
                    fixture.children[second] = replace(fixture.children[second], status="completed", thread_status="idle")
                    fixture.current_parent = replace(fixture.current_parent, status="completed", thread_status="idle")
                    fixture.until(lambda: fixture.call("inspect", task_id=second)["delivery"]["status"] == "accepted")
                    self.assertEqual(fixture.effects.count("send"), 1)
                    self.assertEqual(fixture.hosts["managed-1"].sends, [])
                finally:
                    fixture.doCleanups()

    def test_busy_after_deletion_defers_send_without_redeleting_or_suppressing(self):
        self.call()
        host = self.hosts["managed-1"]
        host.on_delete = lambda: setattr(self, "current_parent", ChildTurn(PARENT, NEW_TURN, "inProgress", "active"))
        self.ended()
        self.until(lambda: self.inspect()["delivery"]["timer_phase"] == "deleted")
        self.assertEqual(self.inspect()["delivery"]["status"], "waiting")
        self.assertNotIn("send", self.effects)
        # An already-started deletion can finish sending after the due cutoff.
        self.now += timedelta(minutes=11)
        self.current_parent = replace(self.current_parent, status="completed", thread_status="idle")
        self.until(lambda: self.inspect()["delivery"]["status"] == "accepted")
        self.assertEqual(self.effects, ["create", "arm", "delete", "send"])

    def test_unknown_send_for_a_does_not_replay_or_cancel_b_after_restart(self):
        second = self.two_waits()
        self.auto_receive = False
        self.hosts["managed-1"].outcome = "unknown"
        self.child = replace(self.child, status="completed", thread_status="idle")
        self.current_parent = replace(self.current_parent, status="completed", thread_status="idle")
        self.until(lambda: self.inspect()["delivery"]["status"] == "unknown")
        self.children[second] = replace(self.children[second], status="completed", thread_status="idle")
        self.current_parent = ChildTurn(PARENT, str(uuid4()), "completed", "idle")
        self.until(lambda: self.call("inspect", task_id=second)["delivery"]["status"] == "accepted")
        self.session.close()
        before = list(self.effects)
        restarted = self.make()
        self.assertEqual(self.call("inspect", session=restarted)["delivery"]["status"], "unknown")
        self.assertEqual(self.call("inspect", task_id=second, session=restarted)["delivery"]["status"], "accepted")
        self.assertEqual(before, self.effects)
        self.assertEqual(self.effects.count("send"), 2)
        self.assertEqual(len(self.hosts["managed-1"].sends), 1)
        self.assertEqual(len(self.hosts["managed-2"].sends), 1)

    def test_new_parent_turn_after_due_keeps_fallback_until_outer_expiry(self):
        self.call()
        self.now += timedelta(minutes=11)
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "completed", "idle")
        self.child = replace(self.child, status="completed", thread_status="idle")
        time.sleep(0.05)
        self.assertEqual(self.inspect()["delivery"]["status"], "waiting")
        self.assertEqual(self.effects, ["create", "arm"])
        self.now += timedelta(minutes=10)
        self.until(lambda: self.inspect()["delivery"]["timer_phase"] == "paused")
        self.assertEqual(self.inspect()["delivery"]["status"], "expired")

    def test_same_parent_os_dispatch_lease_defers_without_host_effect(self):
        from task_governance_tool.review_wait_runtime.request_repository import ParentDispatchRepository
        self.call()
        path = self.paths.review_wait_dispatch_store(PARENT)
        with ParentDispatchRepository(path).serial():
            self.ended()
            time.sleep(0.05)
            self.assertEqual(self.effects, ["create", "arm"])
            self.assertEqual(self.inspect()["delivery"]["status"], "waiting")
        self.until(lambda: self.inspect()["delivery"]["status"] == "accepted")
        self.assertFalse(path.exists())  # Only the bounded OS lock, no database.

    def test_first_dispatch_lock_publish_never_exposes_empty_file_to_competitor(self):
        from task_governance_tool import state_paths
        from task_governance_tool.review_wait_runtime.request_repository import ParentDispatchRepository
        self.paths.review_wait_root.mkdir()
        path = self.paths.review_wait_dispatch_store(PARENT)
        repository = ParentDispatchRepository(path)
        writing, release, outcomes = threading.Event(), threading.Event(), []
        write = state_paths._write_all
        def interrupted_write(*args):
            if threading.current_thread().name == "first-dispatch-creator":
                writing.set()
                if not release.wait(3):
                    raise AssertionError("test writer was not released")
            return write(*args)
        def first():
            try:
                with repository.serial():
                    outcomes.append("first_admitted")
            except Exception as error:
                outcomes.append(str(error))
        with mock.patch.object(state_paths, "_write_all", side_effect=interrupted_write):
            worker = threading.Thread(target=first, name="first-dispatch-creator")
            worker.start()
            try:
                self.assertTrue(writing.wait(3))
                self.assertFalse(repository.lock_path.exists())
                with ParentDispatchRepository(path).serial():
                    self.assertEqual(repository.lock_path.stat().st_size, 1)
                    outcomes.append("second_admitted")
            finally:
                release.set()
                worker.join(3)
        self.assertEqual(outcomes, ["second_admitted", "first_admitted"])
        self.assertEqual(repository.lock_path.read_bytes(), b"\x00")
        self.assertFalse(list(self.paths.review_wait_root.glob("*.tmp")))

    def test_existing_empty_dispatch_lock_is_not_repaired(self):
        from task_governance_tool.review_wait_runtime.request_repository import ParentDispatchRepository
        self.paths.review_wait_root.mkdir()
        repository = ParentDispatchRepository(self.paths.review_wait_dispatch_store(PARENT))
        repository.lock_path.write_bytes(b"")
        with self.assertRaisesRegex(Exception, "state_unreadable"):
            with repository.serial():
                self.fail("corrupt lock admitted")
        self.assertEqual(repository.lock_path.read_bytes(), b"")


class ManagedTrackingAdmissionTests(unittest.TestCase):
    """Real issued collection/native admission through the public wait entry."""

    def setUp(self):
        self.flow = ManagedFlowTests(methodName="runTest")
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    def issued_collection(self, *, tracking=True):
        from tests import test_review_answer_runtime_selection as selections
        from tests.test_review_answer_tracking_collection import tracking_fixture
        from tests.test_review_continuation import A, B
        self.selection = selections.RuntimeSelectionTests(methodName="runTest")
        self.addCleanup(self.selection.doCleanups)
        # Issue tracking bases from the outset; no persisted request is upgraded.
        if tracking:
            self.enterContext(mock.patch.object(selections, "metadata_fixture", side_effect=tracking_fixture))
        native = self.selection.ready(version=2)
        self.native = native
        flow = self.flow
        flow.root, flow.paths, flow.basis = native.repo, native.resolution.paths, self.selection.binding
        flow.paths.review_wait_store = lambda automation: native.root / (automation + ".sqlite")
        self.parent, self.task = flow.basis.parent_thread_id, flow.basis.task_id
        self.metadata = dict(threadId=self.parent, turnId=ORIGINAL)
        flow.current_parent = ChildTurn(self.parent, ORIGINAL, "inProgress", "active")
        self.reviewers = (A, B)
        self.turns = {A: TURN, B: TURN}
        self.completed = False
        self.answer_text = None
        self.binding_unavailable = False
        self.binding_interacted = False
        self.answer_reads = []
        self.worker_cycle = threading.Event()
        original_factory = flow.factory

        def factory(**kwargs):
            host = original_factory(**kwargs)
            host._metadata = kwargs["metadata"]
            host.value = replace(host.value, parent_thread_id=self.parent)
            host.read_child = lambda child: ChildTurn(child, self.turns[child],
                "completed" if self.completed else "inProgress", "idle" if self.completed else "active")
            def read_parent():
                if threading.current_thread() is not threading.main_thread():
                    # The worker reads its parent only after all reviewer
                    # checks in this iteration have succeeded.
                    self.worker_cycle.set()
                return flow.current_parent
            host.read_parent = read_parent
            host._json_call = self.public_page
            return host

        flow.factory = factory
        flow.session = flow.make()
        # Workers must stop before native stores and resolver patches are removed.
        self.addCleanup(flow.close_all)

    def public_page(self, operation, arguments):
        self.assertEqual(operation, "read_thread")
        child = arguments["threadId"]
        self.answer_reads.append((child, arguments["turnLimit"]))
        if self.binding_unavailable and child == self.parent:
            raise HostAdapterError("host_call_failed")
        items = ([dict(type="subAgentActivity", id="dispatch-" + str(index),
                       kind="started", agentThreadId=reviewer)
                  for index, reviewer in enumerate(self.reviewers)] if child == self.parent else [])
        if child == self.parent and self.binding_interacted:
            items.append(dict(type="subAgentActivity", id="interaction", kind="interacted",
                              agentThreadId=self.reviewers[0], text="private contact"))
        if child != self.parent and self.completed and self.answer_text is not None:
            items = [dict(type="agentMessage", id="invalid-final", phase="final_answer", text=self.answer_text)]
        status = "completed" if child != self.parent and self.completed else "inProgress"
        return dict(schemaVersion=1, thread=dict(id=child, kind="codex", hostId="local"),
            page=dict(order="newest_first", limit=arguments["turnLimit"], hasMore=False, nextCursor=None),
            turns=[dict(id=ORIGINAL if child == self.parent else self.turns[child],
                status=status, items=items, error=None, startedAt=1,
                completedAt=2 if status == "completed" else None,
                durationMs=1 if status == "completed" else None)])

    def wait(self):
        from task_governance_tool.review_wait_runtime.review_wait_server import _call
        response = _call(dict(name="review_wait_wait", arguments=dict(task_id=self.task,
            reviewer_ids=list(self.reviewers)), _meta=self.metadata), self.flow.session)
        return json.loads(response["content"][0]["text"])

    def bind_public(self):
        from task_governance_tool.review_wait_runtime.review_wait_server import _call
        response = _call(dict(name="review_answer_collect", arguments=dict(task_id=self.task,
            reviewer_ids=list(self.reviewers)), _meta=self.metadata), self.flow.session)
        value = json.loads(response["content"][0]["text"])
        self.assertTrue(value["bound"], value)
        self.assertFalse(value["ok"], value)  # No valid completed answer is available.

    def assert_waiting(self, result):
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["parent_may_end"], result)
        self.assertEqual(self.flow.effects, ["create", "arm"])
        completed_iteration = self.worker_cycle.wait(10)
        observed = self.flow.session.handle("inspect", {"task_id": self.task}, self.metadata)
        self.assertTrue(completed_iteration, observed)
        self.assertEqual(observed["observation"]["worker"]["status"], "running", observed)
        self.assertEqual(observed["delivery"]["status"], "waiting", observed)

    def test_enabled_unbound_collection_keeps_real_tracking_resolver_and_wait_worker(self):
        self.issued_collection()
        self.binding_unavailable = True
        result = self.wait()
        self.assert_waiting(result)
        self.assertTrue(result["answer_collection"]["unavailable"], result)
        records = self.selection.service().repository.read()
        self.assertTrue(all(row["binding"] is None for row in records))
        # Repeated real tracking lookup remains optional for a validated unbound
        # initial source, but still traverses native and issued-basis admission.
        self.assertEqual(self.flow.session._answer_tracking(self.flow.basis, self.reviewers), {})

    def test_initial_interaction_reason_survives_wait_without_disabling_timer(self):
        self.issued_collection()
        self.binding_interacted = True
        result = self.wait()
        self.assert_waiting(result)
        failure = result["answer_collection"]["unavailable"][0]["failure"]
        self.assertEqual(failure, {"class": "binding_mismatch", "field": None,
                                  "reason": "initial_binding_interacted"})
        self.assertNotIn("private contact", json.dumps(result))
        self.assertTrue(all(row["binding"] is None for row in self.selection.service().repository.read()))
        self.assertEqual(self.answer_reads.count((self.parent, 1)), 1)

    def test_initial_interaction_reason_survives_already_ended_reviewers(self):
        self.issued_collection()
        self.binding_interacted = self.completed = True
        with mock.patch.object(self.flow.session, "_collect_answers",
                               wraps=self.flow.session._collect_answers) as collect:
            result = self.wait()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "reviews_ended")
        self.assertFalse(result["parent_may_end"])
        self.assertEqual(self.flow.effects, [])
        collect.assert_called_once()
        self.assertEqual(result["answer_collection"], {"collected": [], "unavailable": [
            {"failure": {"class": "binding_mismatch", "field": None,
                         "reason": "initial_binding_interacted"}}]})
        self.assertNotIn("private contact", json.dumps(result))
        self.assertTrue(all(row["binding"] is None for row in self.selection.service().repository.read()))
        self.assertEqual(self.answer_reads, [(self.parent, 1)])

    def test_workerless_initial_interaction_keeps_the_same_failure_without_timer(self):
        self.issued_collection()
        self.binding_interacted = True
        from task_governance_tool.review_wait_runtime.review_wait_server import _call
        response = _call(dict(name="review_answer_collect", arguments=dict(task_id=self.task,
            reviewer_ids=list(self.reviewers)), _meta=self.metadata), self.flow.session)
        result = json.loads(response["content"][0]["text"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"], {"class": "binding_mismatch", "field": None,
                                           "reason": "initial_binding_interacted"})
        self.assertEqual(self.flow.effects, [])
        self.assertEqual(self.answer_reads, [(self.parent, 1)])

    def test_normal_wait_binds_actual_handles_without_child_identity_query(self):
        self.issued_collection()
        original = self.flow.session.managed_host_factory
        def factory(**kwargs):
            host = original(**kwargs)
            host.resolve_reviewer_ids = lambda: self.reviewers
            return host
        self.flow.session.managed_host_factory = factory
        from task_governance_tool.review_wait_runtime.review_wait_server import _call
        response = _call(dict(name="review_wait_wait", arguments=dict(task_id=self.task,
            reviewer_ids=["/root/reviewer_a", "/root/reviewer_b"]), _meta=self.metadata), self.flow.session)
        result = json.loads(response["content"][0]["text"])
        self.assert_waiting(result)
        self.assertEqual([row["binding"]["reviewer_session_id"]
                          for row in self.selection.service().repository.read()], list(self.reviewers))
        self.assertEqual(self.answer_reads.count((self.parent, 1)), 1)

    def test_already_running_correction_turn_uses_saved_tracking_boundary(self):
        self.issued_collection()
        self.completed, self.answer_text = True, "not a structured review"
        self.bind_public()
        self.completed = False
        self.turns = {child: NEW_TURN for child in self.reviewers}
        result = self.wait()
        self.assert_waiting(result)
        from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
        automation = next(iter(self.flow.hosts))
        controller = ReviewWaitRepository.open_existing(self.flow.paths.review_wait_store(automation)).read().controller
        self.assertEqual({row.turn_id for row in controller.reviewers}, {TURN})
        self.assertEqual({row.turn_id for row in controller.reviewers},
                         {row["binding"]["tracking"]["boundary_turn_id"]
                          for row in self.selection.service().repository.read()})
        self.assertNotIn("delete", self.flow.effects)

    def test_invalidated_tracking_source_cannot_fall_back_to_ordinary_wait(self):
        self.issued_collection()
        self.bind_public()
        self.selection.service().invalidate(1)
        result = self.wait()
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.flow.effects, [])

    def test_worker_uses_issued_slot_order_when_uuid_sort_order_differs(self):
        self.issued_collection()
        self.reviewers = tuple(reversed(self.reviewers))
        self.bind_public()
        self.turns = {child: NEW_TURN for child in self.reviewers}
        result = self.wait()
        self.assert_waiting(result)
        _, request = RequestRepository(self.flow.paths.review_wait_request_store(self.parent, self.task)).read()
        self.assertEqual(tuple(row[0] for row in request.reviewers), tuple(sorted(self.reviewers)))

    def test_issued_version_one_still_rejects_changed_turn_before_create(self):
        self.issued_collection(tracking=False)
        self.bind_public()
        self.turns = {child: NEW_TURN for child in self.reviewers}
        result = self.wait()
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.flow.effects, [])

    def test_public_arm_then_running_continuation_starts_tracking_wait(self):
        from task_governance_tool import review_answer_continuation_preparation as continued
        from task_governance_tool.review_wait_runtime.review_wait_server import _call
        self.issued_collection()
        self.bind_public()
        # This fixture also serves compatibility tests; choose the new protocol
        # at issuance, never by changing a saved continuation record.
        legacy_issue = continued.expected_bases
        with mock.patch.object(continued, "expected_bases", side_effect=lambda *args, **kwargs:
                tuple({**basis, "version": 2} for basis in legacy_issue(*args, **kwargs))):
            prepared = self.native.call()
        self.assertTrue(prepared["ok"], prepared)
        self.reviewers = self.reviewers[:1]
        self.completed = True
        armed = _call(dict(name="review_answer_collect", arguments=dict(action="arm",
            task_id=self.task, reviewer_ids=list(self.reviewers)), _meta=self.metadata), self.flow.session)
        arm_result = json.loads(armed["content"][0]["text"])
        self.assertTrue(arm_result["ok"], arm_result)
        self.assertEqual(arm_result["status"], "armed")
        self.completed = False
        self.turns[self.reviewers[0]] = NEW_TURN
        self.assert_waiting(self.wait())


class WorkerObservationTests(unittest.TestCase):
    # Reuse only the fixture, not its entire inherited regression test suite.
    setUp = ManagedFlowTests.setUp
    close_all = ManagedFlowTests.close_all
    factory = ManagedFlowTests.factory
    make = ManagedFlowTests.make
    call = ManagedFlowTests.call
    inspect = ManagedFlowTests.inspect
    until = ManagedFlowTests.until
    ended = ManagedFlowTests.ended

    def worker(self):
        return next(iter(self.session.sessions.values())).direct

    def settled(self):
        worker = self.worker()
        worker._worker.join(5)
        worker._termination_observer.join(5)
        self.assertFalse(worker._worker.is_alive())
        self.assertFalse(worker._termination_observer.is_alive())
        return worker

    def rewrite(self, record):
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import _encode
        with self.worker().repository._serial():
            with self.worker().repository._connection(write=True) as connection:
                connection.execute("UPDATE direct_probe SET payload=?", (_encode(record),))

    def test_absent_request_is_not_stopped_and_has_no_effects(self):
        before = list(self.paths.fixed_root.rglob("*"))
        for operation in ("inspect", "stop"):
            value = self.call(operation)
            self.assertEqual("absent", value["status"])
            self.assertEqual("absent", value["observation"]["request"]["status"])
            self.assertEqual("unknown", value["observation"]["worker"]["status"])
            self.assertEqual("unknown", value["observation"]["work"]["other_children"])
            self.assertNotIn("120秒", json.dumps(value, ensure_ascii=False))
        self.assertEqual(before, list(self.paths.fixed_root.rglob("*")))
        self.assertEqual([], self.effects)

    def test_running_is_exact_owned_thread_not_saved_started_phase(self):
        self.assertTrue(self.call()["ok"])
        observed = self.inspect()["observation"]
        self.assertEqual(("present", "started", "running", "direct_thread"),
            (observed["request"]["status"], observed["request"]["phase"],
             observed["worker"]["status"], observed["worker"]["source"]))
        self.assertEqual(TASK, observed["identity"]["task_id"])
        self.assertEqual(1, observed["identity"]["attempt"])
        self.assertEqual(self.worker()._owned_worker_id, observed["worker"]["worker_id"])
        restarted = self.make()
        value = self.call("inspect", session=restarted)
        self.assertEqual("unknown", value["observation"]["worker"]["status"])
        self.assertEqual({}, restarted.sessions)
        self.assertEqual(["create", "arm"], self.effects)

    def test_natural_join_is_persisted_and_read_only_after_restart(self):
        self.call()
        self.ended()
        worker = self.settled()
        record = worker.repository.read()
        self.assertEqual("thread_join", record.worker_stop_basis)
        original = self.inspect()["observation"]["worker"]
        self.assertEqual(("stopped", "direct_thread", "confirmed"),
                         (original["status"], original["source"], original["persistence"]))
        restarted = self.make()
        before = {path: path.read_bytes() for path in self.paths.review_wait_root.glob("*") if path.is_file()}
        effects = list(self.effects)
        self.now += timedelta(seconds=15)
        value = self.call("inspect", session=restarted)
        observed = value["observation"]
        self.assertEqual(("stopped", "saved_thread_join", record.worker_stopped_at),
                         (observed["worker"]["status"], observed["worker"]["source"], observed["worker"]["confirmed_at"]))
        self.assertNotEqual(observed["observed_at"], observed["worker"]["confirmed_at"])
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(effects, self.effects)
        self.assertEqual({}, restarted.sessions)

    def test_explicit_stop_reports_operation_and_exact_join_separately(self):
        self.call()
        value = self.call("stop")
        self.assertEqual({"status": "succeeded", "cleanup": "confirmed"}, value["operation"])
        self.assertEqual("stopped", value["observation"]["worker"]["status"])
        restarted = self.make()
        before = list(self.effects)
        self.assertEqual("saved_thread_join", self.call("inspect", session=restarted)["observation"]["worker"]["source"])
        self.assertEqual(before, self.effects)

    def test_successful_stop_survives_later_observation_failure(self):
        self.call()
        with mock.patch.object(self.session.managed, "observe", side_effect=OSError("PRIVATE")):
            value = self.call("stop")
        self.assertTrue(value["ok"])
        self.assertEqual("succeeded", value["operation"]["status"])
        self.assertEqual("unknown", value["observation"]["worker"]["status"])
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)

    def test_save_failure_keeps_direct_exit_but_restart_unknown(self):
        self.call()
        worker = self.worker()
        with mock.patch.object(worker.repository, "confirm_worker_stopped", side_effect=OSError("PRIVATE")):
            self.ended()
            self.settled()
        value = self.inspect()["observation"]["worker"]
        self.assertEqual(("stopped", "direct_thread", "failed"),
                         (value["status"], value["source"], value["persistence"]))
        restarted = self.make()
        self.assertEqual("unknown", self.call("inspect", session=restarted)["observation"]["worker"]["status"])
        self.assertNotIn("PRIVATE", json.dumps(value))

    def test_failure_after_saved_confirmation_does_not_erase_evidence(self):
        self.call()
        worker = self.worker()
        original = worker.repository.confirm_worker_stopped
        def after_commit(*args):
            original(*args)
            raise OSError("lost result")
        with mock.patch.object(worker.repository, "confirm_worker_stopped", side_effect=after_commit):
            self.ended()
            self.settled()
        self.assertEqual("confirmed", self.inspect()["observation"]["worker"]["persistence"])
        self.assertEqual("stopped", self.call("inspect", session=self.make())["observation"]["worker"]["status"])

    def test_unstarted_and_old_payload_never_supply_exit_evidence(self):
        self.call()
        self.call("stop")
        worker = self.settled()
        record = worker.repository.read()
        self.rewrite(replace(record, worker_stopped_at=None, worker_stop_basis=None, worker_stop_identity=None))
        worker._worker = threading.Thread(target=lambda: None)
        self.assertEqual("unknown", self.inspect()["observation"]["worker"]["status"])
        self.rewrite(replace(record, worker_id=None, worker_stopped_at=None, worker_stop_basis=None, worker_stop_identity=None))
        self.assertEqual("unknown", self.call("inspect", session=self.make())["observation"]["worker"]["status"])

    def test_saved_other_worker_and_controller_conflicts_are_unknown(self):
        self.call()
        self.call("stop")
        worker = self.settled()
        record = worker.repository.read()
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import worker_stop_identity
        other = str(uuid4())
        self.rewrite(replace(record, worker_id=other,
            worker_stop_identity=worker_stop_identity(record.probe_id, other, record.binding_digest)))
        self.assertEqual("ownership_mismatch", self.inspect()["observation"]["worker"]["reason"])
        self.rewrite(replace(record, binding_digest="f" * 64,
            worker_stop_identity=worker_stop_identity(record.probe_id, record.worker_id, "f" * 64)))
        self.assertEqual("binding_mismatch", self.call("inspect", session=self.make())["observation"]["worker"]["reason"])
        self.rewrite(record)

    def test_stopped_evidence_contradicting_live_thread_is_unknown(self):
        self.call()
        worker = self.worker()
        record = worker.repository.read()
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import worker_stop_identity
        # Deliberately corrupt fixture claim; never made by the production writer.
        self.rewrite(replace(record, worker_stopped_at=record.created_at, worker_stop_basis="thread_join",
            worker_stop_identity=worker_stop_identity(record.probe_id, record.worker_id, record.binding_digest)))
        value = self.inspect()["observation"]["worker"]
        self.assertEqual(("unknown", "conflicting_evidence"), (value["status"], value["reason"]))
        self.rewrite(record)

    def test_unreadable_direct_evidence_is_unknown_without_read_side_effects(self):
        self.call()
        self.call("stop")
        worker = self.settled()
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import DirectRepository
        before = worker.repository.path.read_bytes()
        effects = list(self.effects)
        with mock.patch.object(DirectRepository, "read", side_effect=OSError("PRIVATE")):
            value = self.call("inspect", session=self.make())
        self.assertFalse(value["ok"])
        self.assertEqual("unknown", value["observation"]["worker"]["status"])
        self.assertEqual(before, worker.repository.path.read_bytes())
        self.assertEqual(effects, self.effects)
        self.assertNotIn("PRIVATE", json.dumps(value))

    def test_join_confirmation_is_idempotent_bound_and_not_a_self_marker(self):
        self.call()
        self.call("stop")
        worker = self.settled()
        record = worker.repository.read()
        later = (self.now + timedelta(seconds=20)).isoformat()
        actual = worker.repository.confirm_worker_stopped(record.probe_id, record.worker_id, record.binding_digest, later)
        self.assertEqual(record.worker_stopped_at, actual.worker_stopped_at)
        from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
        for probe, identity, digest in ((str(uuid4()), record.worker_id, record.binding_digest),
                                       (record.probe_id, str(uuid4()), record.binding_digest),
                                       (record.probe_id, record.worker_id, "e" * 64)):
            with self.assertRaises(RepositoryError):
                worker.repository.confirm_worker_stopped(probe, identity, digest, later)
        for basis in ("finally", "self_exiting", "expired"):
            with self.assertRaises(RepositoryError):
                replace(record, worker_stop_basis=basis)

    def test_notification_observation_is_frozen_and_keeps_complete_results(self):
        self.call()
        worker = self.worker()
        observed = self.inspect()["observation"]
        host_tests = ManagedHostTests()
        host = host_tests.host()
        probe = worker.repository.read().probe_id
        host.set_finalization_result(probe, host_tests.report("all original fields"))
        worker._notification_observation(host, worker.wait.read().controller, worker.repository.read())
        prompt = host._direct_prompt(probe)
        value = json.loads(prompt.splitlines()[2])
        self.assertEqual("running", value["wait_observation"]["worker"]["status"])
        self.assertIsNone(value["wait_observation"]["delivery"]["acknowledged_turn"])
        self.assertEqual("waiting", value["wait_observation"]["delivery"]["status"])
        self.assertEqual("unknown", value["wait_observation"]["service"]["status"])
        self.assertEqual(host_tests.report("all original fields"),
                         {key: item for key, item in value.items() if key not in {"delivery", "wait_observation"}})
        self.assertIn("120秒", prompt)
        self.assertIn("終了確認ではありません", prompt)
        self.assertNotIn("120秒", json.dumps(observed, ensure_ascii=False))
        with mock.patch.object(host, "_request", return_value={}):
            self.assertEqual("unknown", host.send_direct_probe(probe))
        with self.assertRaises(HostAdapterError):
            host.set_wait_observation(probe, observed)
        self.call("stop")
        self.assertEqual(prompt, host._direct_prompt(probe))

    def test_review_only_notification_has_same_observation_and_no_extra_send(self):
        self.call()
        worker = self.worker()
        host = ManagedHostTests().host()
        record = worker.repository.read()
        worker._notification_observation(host, worker.wait.read().controller, record)
        prompt = host._direct_prompt(record.probe_id)
        observed = json.loads(prompt.split("\n待機観測: ", 1)[1].splitlines()[0])
        self.assertEqual(record.worker_id, observed["worker"]["worker_id"])
        self.assertEqual("running", observed["worker"]["status"])
        with mock.patch.object(host, "_request", return_value={"content": [], "isError": False}) as send:
            self.assertEqual("accepted", host.send_direct_probe(record.probe_id))
            with self.assertRaises(HostAdapterError):
                host.send_direct_probe(record.probe_id)
        self.assertEqual(1, send.call_count)

    def test_saved_evidence_from_other_worker_or_probe_is_rejected_after_restart(self):
        self.call()
        self.call("stop")
        worker = self.settled()
        record = worker.repository.read()
        from task_governance_tool.review_wait_runtime.review_wait_direct_repository import _encode
        for field in ("worker_id", "probe_id", "binding_digest", "worker_stop_basis", "worker_stop_identity"):
            value = json.loads(_encode(record))
            value[field] = "f" * 64 if field in {"binding_digest", "worker_stop_identity"} else "finally" if field == "worker_stop_basis" else str(uuid4())
            with worker.repository._serial():
                with worker.repository._connection(write=True) as connection:
                    connection.execute("UPDATE direct_probe SET payload=?", (json.dumps(value, sort_keys=True, separators=(",", ":")),))
            result = self.call("inspect", session=self.make())
            self.assertFalse(result["ok"])
            self.assertEqual("unknown", result["observation"]["worker"]["status"])
            self.rewrite(record)

    def test_restart_in_separate_process_reads_exit_without_start_or_write(self):
        import subprocess
        import sys
        self.call()
        self.ended()
        worker = self.settled()
        before = {path: path.read_bytes() for path in self.paths.review_wait_root.glob("*") if path.is_file()}
        script = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.state_resolver import canonical_state_paths
root = Path(sys.argv[2])
session = ProjectReviewWaitSession(ProjectConfig(root, root / "server.mjs", root, "UTC"))
session._location = lambda **kwargs: canonical_state_paths(root / "skill", repo=root)
result = session.handle("inspect", {"task_id": sys.argv[3]}, {"threadId": sys.argv[4]})
print(json.dumps({"result": result, "loaded_sessions": len(session.sessions)}))
'''
        scripts = Path(__file__).absolute().parents[1] / "task-governance-tool" / "scripts"
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script,
            str(scripts), str(self.root), TASK, PARENT], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value["result"]["ok"], value)
        self.assertEqual("saved_thread_join", value["result"]["observation"]["worker"]["source"])
        self.assertEqual("stopped", value["result"]["observation"]["worker"]["status"])
        self.assertEqual(0, value["loaded_sessions"])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_attempt_change_during_observation_cannot_rebind_old_stop(self):
        self.call()
        path = self.paths.review_wait_request_store(PARENT, TASK)
        sequence, record = RequestRepository(path).read()
        with mock.patch.object(RequestRepository, "read", return_value=(sequence + 1, record)):
            value = self.session.managed.observe(TASK, META, self.paths, expected=(sequence, record))
        self.assertEqual(sequence, value["identity"]["attempt"])
        self.assertEqual("attempt_changed", value["worker"]["reason"])
        self.assertEqual("unknown", value["worker"]["status"])

    def test_unknown_send_stop_preserves_effect_uncertainty_and_live_observation(self):
        self.auto_receive = False
        self.call()
        worker = self.worker()
        worker._host.outcome = "unknown"
        self.ended()
        self.until(lambda: self.inspect()["delivery"]["status"] == "unknown")
        before = list(self.effects)
        value = self.call("stop")
        self.assertFalse(value["ok"])
        self.assertEqual("unresolved", value["operation"]["status"])
        self.assertEqual("running", value["observation"]["worker"]["status"])
        self.assertEqual(before, self.effects)

    def test_partial_cleanup_reports_confirmed_exit_without_claiming_cleanup_success(self):
        self.call()
        self.fail_delete = True
        value = self.call("stop")
        self.assertFalse(value["ok"])
        self.assertEqual("unresolved", value["operation"]["cleanup"])
        self.assertEqual("stopped", value["observation"]["worker"]["status"])
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)

    def test_observation_failure_does_not_prevent_notification_or_create_effect(self):
        self.call()
        worker = self.worker()
        host = ManagedHostTests().host()
        worker.observation_factory = mock.Mock(side_effect=OSError("PRIVATE"))
        worker.runtime_observer = mock.Mock(side_effect=OSError("PRIVATE"))
        record = worker.repository.read()
        worker._notification_observation(host, worker.wait.read().controller, record)
        prompt = host._direct_prompt(record.probe_id)
        observed = json.loads(prompt.split("\n待機観測: ", 1)[1].splitlines()[0])
        self.assertEqual("unknown", observed["worker"]["status"])
        self.assertEqual(record.probe_id, observed["identity"]["probe_id"])
        self.assertNotIn("PRIVATE", prompt)
        with mock.patch.object(host, "_request", return_value={"content": [], "isError": False}):
            self.assertEqual("accepted", host.send_direct_probe(record.probe_id))

    def test_large_review_only_observation_uses_public_read_bound_and_exact_body(self):
        self.call()
        worker = self.worker()
        host_tests = ManagedHostTests()
        host = host_tests.host()
        record = worker.repository.read()
        observed = self.inspect()["observation"]
        observed["work"]["reviewers"] = [
            {"reviewer_id": str(uuid4()), "turn_id": str(uuid4()), "observed_at": observed["observed_at"],
             "status": "completed", "thread_status": "idle", "ended": True} for _ in range(64)]
        host.set_wait_observation(record.probe_id, observed)
        self.assertGreater(len(host._direct_prompt(record.probe_id)), 4096)
        value = host_tests.receipt(host)
        # The helper makes an event for OTHER; use this actual probe's body.
        output = value["turns"][0]["items"][0]["output"]
        output["text"] = output["text"].replace(host._direct_prompt(OTHER), host._direct_prompt(record.probe_id))
        with host_tests.history(host, value) as read:
            self.assertEqual(NEW_TURN, host.observe_receipt(record.probe_id, ORIGINAL))
        self.assertEqual(20000, read.call_args.args[1]["maxOutputCharsPerItem"])

    def test_production_caught_observation_read_failure_still_sends_unknown_snapshot(self):
        self.call()
        worker = self.worker()
        real_host = ManagedHostTests().host()
        worker._host.set_wait_observation = real_host.set_wait_observation
        original = worker.observation_factory
        from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository, RepositoryError
        def unavailable(controller):
            with mock.patch.object(ReviewWaitRepository, "open_existing",
                                   side_effect=RepositoryError("state_unreadable")) as read:
                result = original(controller)
            read.assert_called_once()
            return result
        worker.observation_factory = unavailable
        probe = worker.repository.read().probe_id
        self.ended()
        self.settled()
        self.assertEqual([probe], worker._host.sends)
        self.assertEqual("accepted", worker.repository.read().status)
        observed = json.loads(real_host._direct_prompt(probe).split("\n待機観測: ", 1)[1].splitlines()[0])
        self.assertEqual("unknown", observed["worker"]["status"])
        self.assertEqual(probe, observed["identity"]["probe_id"])
        self.assertIsNone(observed["delivery"])

    def test_notification_observation_cannot_send_after_original_deadline(self):
        self.call()
        worker = self.worker()
        worker._notification_observation = lambda *args: setattr(self, "now", self.now + timedelta(seconds=1201))
        self.ended()
        self.settled()
        self.assertEqual([], worker._host.sends)
        self.assertEqual("expired", worker.repository.read().status)
        self.assertEqual(["create", "arm", "delete"], self.effects)

    def test_notification_observation_cancellation_prevents_send(self):
        self.call()
        worker = self.worker()
        worker._notification_observation = lambda *args: worker._stop.set()
        self.ended()
        self.settled()
        self.assertEqual([], worker._host.sends)
        self.assertEqual(["create", "arm", "delete"], self.effects)

    def test_notification_observation_basis_drift_prevents_send(self):
        self.call()
        worker = self.worker()
        worker._notification_observation = lambda *args: setattr(self, "basis", replace(self.basis, ownership_generation=2))
        self.ended()
        self.settled()
        self.assertEqual([], worker._host.sends)
        self.assertEqual("suppressed", worker.repository.read().status)

    def test_notification_observation_reviewer_drift_prevents_send(self):
        self.call()
        worker = self.worker()
        worker._notification_observation = lambda *args: setattr(self, "child", ChildTurn(CHILD, OTHER, "completed", "idle"))
        self.ended()
        self.settled()
        self.assertEqual([], worker._host.sends)
        self.assertEqual("suppressed", worker.repository.read().status)

    def test_notification_observation_busy_parent_defers_without_second_delete(self):
        self.call()
        worker = self.worker()
        original = worker._notification_observation
        worker._notification_observation = lambda *args: setattr(self, "current_parent", ChildTurn(PARENT, NEW_TURN, "inProgress", "active"))
        self.ended()
        self.until(lambda: self.current_parent.turn_id == NEW_TURN)
        self.assertEqual([], worker._host.sends)
        self.assertEqual(["create", "arm", "delete"], self.effects)
        worker._notification_observation = original
        self.current_parent = ChildTurn(PARENT, NEW_TURN, "completed", "idle")
        self.settled()
        self.assertEqual(1, len(worker._host.sends))
        self.assertEqual(["create", "arm", "delete", "send"], self.effects)
