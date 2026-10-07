"""Isolated protocol/configuration fixtures; never invoke real host operations."""

from __future__ import annotations

from dataclasses import asdict, replace
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from tools import review_wait_host as host


PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
TURN = "33333333-3333-4333-8333-333333333333"
OTHER = "44444444-4444-4444-8444-444444444444"
META = {"threadId": PARENT, "turnId": TURN, "nested": {"preserved": [True, 7]}}
SECRET = "PRIVATE_BODY_MUST_NOT_ESCAPE"
AUTOMATION = "fixture-review-wait"
RULE = "FREQ=DAILY;BYHOUR=12;BYMINUTE=30;BYSECOND=0;COUNT=1"


def turn():
    return {"id": TURN, "status": "completed", "error": None,
            "startedAt": 1, "completedAt": 2, "durationMs": 1}


def read_result():
    return {"schemaVersion": 1, "thread": {"id": CHILD, "kind": "codex", "hostId": "local",
            "title": SECRET, "preview": SECRET, "cwd": SECRET, "createdAt": 1,
            "updatedAt": 2, "status": {"type": "notLoaded"}},
            "page": {"order": "newest_first", "limit": 1, "nextCursor": None, "hasMore": False},
            "turns": [{**turn(), "items": [{"type": "agentMessage", "text": SECRET}]}]}


def wait_result():
    return {"timedOut": False, "wake": {"reason": "completed", "threadId": CHILD, "hostId": "local"},
            "polls": [{"schemaVersion": 1, "cursor": "fixture-cursor", "revision": 1,
                       "changed": True, "thread": {"id": CHILD, "hostId": "local",
                       "status": {"type": "idle"}}, "latestTurn": turn(),
                       "latestAssistantMessageId": "message", "latestAssistantMessage": {"text": SECRET},
                       "latestToolMarkerId": "tool", "latestToolMarker": {"arguments": SECRET}}]}


def config_values():
    return {"version": 1, "id": AUTOMATION, "kind": "heartbeat", "name": "Review fixture",
            "prompt": SECRET, "status": "ACTIVE", "rrule": RULE, "target_thread_id": PARENT,
            "created_at": 1, "updated_at": 2, "notification_policy": "failed_runs_only"}


def write_config(home: Path, values: dict) -> Path:
    path = home / "automations" / AUTOMATION / "automation.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def adapter(home, **changes):
    args = dict(metadata=copy.deepcopy(META), automation_id=AUTOMATION, codex_home=home,
                confirmed_timezone="Asia/Tokyo", timezone_source="host_node_intl",
                reviewer_ids=[CHILD], command=["unused-offline-peer"])
    args.update(changes)
    return host.PublicMcpHost(**args)


class ParserTests(unittest.TestCase):
    def test_read_and_wait_retain_only_latest_identity_and_observed_state(self):
        observed = host.parse_read_thread(read_result(), CHILD)
        self.assertEqual(asdict(observed), {"child_id": CHILD, "turn_id": TURN,
                                          "status": "completed", "thread_status": "notLoaded"})
        waited = host.parse_wait_threads(wait_result(), [CHILD])
        self.assertEqual(waited.turns[0].status, "completed")
        self.assertEqual(waited.cursors, ((CHILD, "fixture-cursor"),))
        self.assertNotIn(SECRET, repr(observed) + repr(waited))
        self.assertFalse(hasattr(observed, "terminal"))

    def test_historical_completed_turn_is_not_promoted_over_active_state(self):
        value = read_result()
        value["thread"]["status"] = {"type": "active", "activeFlags": ["waitingForApproval"]}
        result = host.parse_read_thread(value, CHILD)
        self.assertEqual((result.status, result.thread_status), ("completed", "active"))

    def test_wait_accepts_optional_boolean_cursor_reset_and_returns_new_cursor(self):
        for reset in (False, True):
            with self.subTest(reset=reset):
                value = wait_result()
                value["polls"][0].update(cursorReset=reset, cursor="replacement-cursor")
                waited = host.parse_wait_threads(value, [CHILD])
                self.assertEqual(waited.cursors, ((CHILD, "replacement-cursor"),))
                self.assertEqual(waited.turns, host.parse_wait_threads(wait_result(), [CHILD]).turns)
                self.assertNotIn(SECRET, repr(waited))

    def test_wait_rejects_non_boolean_cursor_reset_and_unknown_poll_fields(self):
        for reset in (None, 0, 1, "true", [], {}):
            value = wait_result()
            value["polls"][0]["cursorReset"] = reset
            with self.subTest(reset=reset), self.assertRaises(host.HostAdapterError):
                host.parse_wait_threads(value, [CHILD])
        value = wait_result()
        value["polls"][0].update(cursorReset=True, unexpected=SECRET)
        with self.assertRaises(host.HostAdapterError):
            host.parse_wait_threads(value, [CHILD])

    def test_failed_interrupted_and_inprogress_are_distinct_observations(self):
        for state in ["failed", "interrupted", "inProgress"]:
            value = read_result()
            value["turns"][0]["status"] = state
            value["turns"][0]["error"] = {"message": SECRET}
            result = host.parse_read_thread(value, CHILD)
            self.assertEqual(result.status, state)
            self.assertNotIn(SECRET, repr(result))

    def test_closed_read_schema_rejects_unknown_or_misbound_results(self):
        mutations = [lambda x: x.update(schemaVersion=True), lambda x: x.update(schemaVersion=2),
                     lambda x: x.update(unknown=SECRET), lambda x: x["thread"].update(id=OTHER),
                     lambda x: x["thread"].update(hostId="remote"),
                     lambda x: x["thread"].update(status={"type": []}),
                     lambda x: x["page"].update(order="oldest_first"),
                     lambda x: x["turns"][0].update(status=[]), lambda x: x.update(turns=[])]
        for mutate in mutations:
            value = read_result()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(host.HostAdapterError) as caught:
                host.parse_read_thread(value, CHILD)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_wait_partial_errors_missing_polls_and_duplicate_children_are_unknown(self):
        mutations = [lambda x: x.update(errors=[{"message": SECRET}]), lambda x: x.update(polls=[]),
                     lambda x: x["polls"].append(copy.deepcopy(x["polls"][0])),
                     lambda x: x["polls"][0].update(latestTurn=None),
                     lambda x: x["polls"][0]["thread"].update(id=OTHER),
                     lambda x: x["polls"][0].update(schemaVersion=2)]
        for mutate in mutations:
            value = wait_result()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(host.HostAdapterError):
                host.parse_wait_threads(value, [CHILD])


class ConfigFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.path = write_config(self.home, config_values())

    def read(self):
        return host._read_config(self.home, AUTOMATION, PARENT, "Asia/Tokyo", "host_node_intl")


class ConfigTests(ConfigFixture, unittest.TestCase):
    def test_exact_file_has_no_prompt_or_inferred_due_in_public_snapshot(self):
        snapshot = self.read().snapshot
        self.assertEqual(snapshot.rule, RULE)
        self.assertEqual(snapshot.parent_thread_id, PARENT)
        self.assertEqual(len(snapshot.identity_digest), 64)
        self.assertNotIn(SECRET, json.dumps(asdict(snapshot)))
        self.assertFalse(any("due" in key or "next" in key for key in asdict(snapshot)))

    def test_unknown_fields_wrong_identity_or_status_fail_closed(self):
        for change in [{"id": "other"}, {"target_thread_id": OTHER}, {"kind": "cron"},
                       {"status": "DELETED"}, {"unexpected": SECRET}, {"model": "other-model"},
                       {"version": 2}, {"notification_policy": "unknown"}, {"updated_at": 0}]:
            write_config(self.home, {**config_values(), **change})
            with self.subTest(change=change), self.assertRaises(host.HostAdapterError):
                self.read()

    def test_prompt_and_policy_are_in_immutable_digest_but_rule_and_status_are_not(self):
        original = self.read().snapshot.identity_digest
        for change in [{"prompt": "changed"}, {"name": "changed"}]:
            write_config(self.home, {**config_values(), **change})
            self.assertNotEqual(self.read().snapshot.identity_digest, original)
        values = config_values()
        del values["notification_policy"]
        write_config(self.home, values)
        self.assertNotEqual(self.read().snapshot.identity_digest, original)
        write_config(self.home, {**config_values(), "status": "PAUSED", "rrule": host.SHORTEN_RULE})
        self.assertEqual(self.read().snapshot.identity_digest, original)

    def test_link_oversize_and_path_tamper_are_rejected(self):
        other = self.home / "other.toml"
        os.link(self.path, other)
        with self.assertRaises(host.HostAdapterError):
            self.read()
        other.unlink()
        self.path.write_bytes(b"x" * (host.MAX_CONFIG_BYTES + 1))
        with self.assertRaises(host.HostAdapterError):
            self.read()
        for bad in ["../fixture", "a/b", "a\\b", "a:stream", "CON"]:
            with self.subTest(bad=bad), self.assertRaises(host.HostAdapterError):
                host._read_config(self.home, bad, PARENT, "Asia/Tokyo", "host_node_intl")

    def test_file_change_between_admission_and_open_is_rejected(self):
        original = os.open

        def changed(path, flags):
            self.path.write_text("changed", encoding="utf-8")
            return original(path, flags)

        with mock.patch.object(host.os, "open", side_effect=changed), self.assertRaises(host.HostAdapterError):
            self.read()

    def test_symlink_is_rejected_where_creation_is_supported(self):
        actual = self.home / "actual.toml"
        self.path.rename(actual)
        try:
            self.path.symlink_to(actual)
        except OSError:
            actual.rename(self.path)
            self.skipTest("symlink creation unavailable to this test process")
        with self.assertRaises(host.HostAdapterError):
            self.read()


class DeleteHeartbeatTests(ConfigFixture, unittest.TestCase):
    def simulated(self, *, retain_config=False, receipt_change=None, failure=None, remove_directory=False):
        service = adapter(self.home)
        calls = []

        def call(name, args, **kwargs):
            self.assertEqual("automation_update", name)
            calls.append(args)
            if args["mode"] == "view":
                return ("Rendered card",)
            self.assertEqual({"mode": "delete", "id": AUTOMATION}, args)
            if not retain_config:
                self.path.unlink()
                if remove_directory:
                    self.path.parent.rmdir()
            if failure:
                raise host.HostAdapterError(failure)
            receipt = {"automationId": AUTOMATION, "mode": "delete", "deleteStatus": "deleted",
                       "snapshot": {"kind": "heartbeat", "name": config_values()["name"], "rrule": RULE}}
            if receipt_change:
                receipt_change(receipt)
            return ("Deleted automation in the app.", json.dumps(receipt))

        service._call = call
        return service, calls

    def test_public_delete_receipt_and_absence_confirm_once(self):
        service, calls = self.simulated()
        before = service.view_heartbeat()
        self.assertIsNone(service.delete_heartbeat(before))
        self.assertEqual(["view", "view", "delete"], [c["mode"] for c in calls])
        self.assertFalse(self.path.exists())

    def test_removed_automation_directory_is_valid_absence(self):
        service, _ = self.simulated(remove_directory=True)
        self.assertIsNone(service.delete_heartbeat(service.view_heartbeat()))

    def test_config_remaining_after_success_receipt_is_unknown(self):
        service, calls = self.simulated(retain_config=True)
        with self.assertRaisesRegex(host.HostAdapterError, "heartbeat_delete_unknown"):
            service.delete_heartbeat(service.view_heartbeat())
        self.assertEqual(1, sum(c["mode"] == "delete" for c in calls))

    def test_absence_without_exact_receipt_never_confirms(self):
        for mutate in (lambda r: r.update(deleteStatus="missing"), lambda r: r.update(automationId="wrong"),
                lambda r: r["snapshot"].update(rrule="wrong"), lambda r: r.update(extra=SECRET)):
            write_config(self.home, config_values())
            service, _ = self.simulated(receipt_change=mutate)
            with self.assertRaisesRegex(host.HostAdapterError, "heartbeat_delete_unknown"):
                service.delete_heartbeat(service.view_heartbeat())

    def test_response_loss_after_effect_is_unknown_without_second_call(self):
        service, calls = self.simulated(failure="host_cleanup_unknown")
        with self.assertRaisesRegex(host.HostAdapterError, "heartbeat_delete_unknown"):
            service.delete_heartbeat(service.view_heartbeat())
        self.assertFalse(self.path.exists())
        self.assertEqual(1, sum(c["mode"] == "delete" for c in calls))

    def test_changed_identity_or_cleanup_only_cannot_delete(self):
        service, calls = self.simulated()
        before = service.view_heartbeat()
        with self.assertRaises(host.HostAdapterError):
            service.delete_heartbeat(replace(before, cleanup_only=True))
        write_config(self.home, {**config_values(), "prompt": "changed"})
        with self.assertRaisesRegex(host.HostAdapterError, "heartbeat_changed"):
            service.delete_heartbeat(before)
        self.assertFalse(any(c["mode"] == "delete" for c in calls))

    def test_missing_enclosing_automations_root_is_not_confirmed_absence(self):
        service, _ = self.simulated(remove_directory=True)
        original = service._call

        def call(name, args, **kwargs):
            value = original(name, args, **kwargs)
            if args["mode"] == "delete":
                self.path.parent.parent.rmdir()
            return value

        service._call = call
        with self.assertRaisesRegex(host.HostAdapterError, "heartbeat_delete_unknown"):
            service.delete_heartbeat(service.view_heartbeat())


class AdmissionAndUpdateTests(ConfigFixture, unittest.TestCase):
    def simulated(self, *, stale_after_view=False, wrong_receipt=False):
        service = adapter(self.home)
        calls = []

        def call(name, args, **kwargs):
            calls.append((name, copy.deepcopy(args)))
            self.assertEqual(name, "automation_update")
            if args["mode"] == "view":
                if stale_after_view:
                    write_config(self.home, {**config_values(), "updated_at": 3})
                return ("Rendered automation card in the app.",)
            self.assertEqual(args["mode"], "update")
            self.assertEqual(args["id"], AUTOMATION)
            self.assertEqual(args["name"], config_values()["name"])
            self.assertEqual(args["prompt"], SECRET)
            self.assertEqual(args["targetThreadId"], PARENT)
            self.assertEqual(args["notificationPolicy"], "failed_runs_only")
            write_config(self.home, {**config_values(), "rrule": args["rrule"],
                                    "status": args["status"], "updated_at": 3})
            return ("Updated automation in the app.", json.dumps({"automationId": "wrong" if wrong_receipt else AUTOMATION,
                    "mode": "update", "status": args["status"]}))

        service._call = mock.Mock(side_effect=call)
        return service, calls

    def test_updates_preserve_identity_and_read_back_exact_status_rule(self):
        for action, rule, expected_status, expected_rule in [
                ("shorten", None, "ACTIVE", host.SHORTEN_RULE),
                ("pause", None, "PAUSED", RULE), ("arm", RULE, "ACTIVE", RULE)]:
            write_config(self.home, {**config_values(), "status": "PAUSED" if action == "arm" else "ACTIVE"})
            service, calls = self.simulated()
            before = service.view_heartbeat()
            after = service.update_heartbeat(before, action, rrule=rule)
            self.assertEqual((after.status, after.rule), (expected_status, expected_rule))
            self.assertEqual(after.identity_digest, before.identity_digest)
            self.assertEqual([args["mode"] for _, args in calls], ["view", "view", "update", "view"])

    def test_stale_snapshot_prevents_write_and_uncertain_receipt_is_explicit(self):
        before = self.read().snapshot
        service, calls = self.simulated(stale_after_view=True)
        with self.assertRaisesRegex(host.HostAdapterError, "heartbeat_changed"):
            service.update_heartbeat(before, "shorten")
        self.assertEqual([args["mode"] for _, args in calls], ["view"])
        write_config(self.home, config_values())
        service, _ = self.simulated(wrong_receipt=True)
        with self.assertRaisesRegex(host.HostAdapterError, "heartbeat_update_unknown"):
            service.update_heartbeat(before, "shorten")

    def test_dispatch_failure_is_unknown_without_retry_or_private_error(self):
        service = adapter(self.home)
        before = self.read().snapshot
        with mock.patch.object(service, "_view_config", return_value=self.read()), \
                mock.patch.object(service, "_call", side_effect=RuntimeError(SECRET)) as call:
            with self.assertRaisesRegex(host.HostAdapterError, "^heartbeat_update_unknown$") as caught:
                service.update_heartbeat(before, "shorten")
            self.assertEqual(call.call_count, 1)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_nonfixed_modes_rules_identity_and_missing_context_are_rejected(self):
        service = adapter(self.home)
        before = self.read().snapshot
        with mock.patch.object(service, "_call") as call:
            for action, rule in [("create", None), ("delete", None), ("shorten", RULE),
                                 ("arm", RULE), ("arm", "FREQ=MINUTELY;INTERVAL=1"),
                                 ("arm", "FREQ=DAILY;BYHOUR=30;BYMINUTE=0;BYSECOND=0;COUNT=1")]:
                with self.subTest(action=action), self.assertRaises(host.HostAdapterError):
                    service.update_heartbeat(before, action, rrule=rule)
            with self.assertRaises(host.HostAdapterError):
                service.update_heartbeat(replace(before, id="another"), "pause")
            with self.assertRaises(host.HostAdapterError):
                service.read_child(OTHER)
            call.assert_not_called()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": PARENT}):
            for metadata in [{}, {"thread_id": PARENT}, {"threadId": "invented"}]:
                with self.assertRaises(host.HostAdapterError):
                    adapter(self.home, metadata=metadata)
        with self.assertRaises(host.HostAdapterError):
            adapter(self.home, confirmed_timezone="", timezone_source="client_guess")

    def test_pins_are_read_only_and_wait_batches_are_limited_to_eight(self):
        from uuid import UUID
        children = [str(UUID(int=i)) for i in range(1, 65)]
        service = adapter(self.home, reviewer_ids=children)
        self.assertEqual(service.parent_thread_id, PARENT)
        self.assertEqual(service.automation_id, AUTOMATION)
        self.assertEqual(service.reviewer_ids, tuple(children))
        with self.assertRaises(AttributeError):
            service.parent_thread_id = OTHER
        with mock.patch.object(service, "_json_call") as call:
            with self.assertRaises(host.HostAdapterError):
                service.wait_children(children[:9])
            call.assert_not_called()

    def test_read_and_wait_requests_remain_bounded_and_do_not_reuse_discovery_identity(self):
        service = adapter(self.home)
        with mock.patch.object(service, "_json_call", return_value=read_result()) as call:
            service.read_child(CHILD)
            self.assertEqual(call.call_args.args[1], {"threadId": CHILD, "hostId": "local", "turnLimit": 1,
                                                     "includeOutputs": False, "maxOutputCharsPerItem": 1})
        with mock.patch.object(service, "_json_call", return_value=wait_result()) as call:
            service.wait_children([CHILD], cursors={CHILD: "prior"})
            self.assertEqual(call.call_args.args[1], {"targets": [{"threadId": CHILD, "hostId": "local",
                                                                 "afterCursor": "prior"}], "timeoutMs": 0})

    def test_conflicting_executor_aliases_are_rejected_without_rewriting(self):
        for key in ["openai/threadId", "openai/thread_id", "codexThreadId", "codex_thread_id", "thread_id"]:
            with self.subTest(key=key), self.assertRaisesRegex(host.HostAdapterError, "^caller_mismatch$"):
                adapter(self.home, metadata={**META, key: OTHER})
        for embedded in [{"thread_id": OTHER}, {"thread": {"id": OTHER}}]:
            for value in (embedded, json.dumps(embedded)):
                with self.subTest(embedded=value), self.assertRaisesRegex(host.HostAdapterError, "^caller_mismatch$"):
                    adapter(self.home, metadata={**META, "x-codex-turn-metadata": value})
        metadata = {**META, "openai/threadId": PARENT,
                    "x-codex-turn-metadata": json.dumps({"thread_id": PARENT, "thread": {"id": PARENT}})}
        self.assertIs(host.admit_executor_metadata(metadata), metadata)
        self.assertEqual(adapter(self.home, metadata=metadata)._metadata, metadata)
        metadata["x-codex-turn-metadata"] = {"thread_id": PARENT, "turn_id": CHILD}
        self.assertIs(host.admit_executor_metadata(metadata), metadata)
        self.assertEqual(adapter(self.home, metadata=metadata)._metadata, metadata)
        for metadata in [{**META, "x-codex-turn-metadata": "PRIVATE_BAD_JSON"},
                         {**META, "x-codex-turn-metadata": []},
                         {**META, "x-codex-turn-metadata": None},
                         {**META, "extra": "x" * 16384}, {"thread_id": PARENT},
                         {"x-codex-turn-metadata": {"thread_id": PARENT}}]:
            with self.assertRaisesRegex(host.HostAdapterError, "^executor_context_required$"):
                adapter(self.home, metadata=metadata)

    def test_fresh_timezone_drift_and_reader_failure_prevent_update(self):
        reader = mock.Mock(return_value="Asia/Tokyo")
        service = adapter(self.home, timezone_reader=reader)
        with mock.patch.object(service, "_call", return_value=("card",)) as call:
            before = service.view_heartbeat()
            reader.return_value = "UTC"
            with self.assertRaisesRegex(host.HostAdapterError, "^timezone_changed$"):
                service.update_heartbeat(before, "shorten")
            self.assertTrue(all(args.args[1]["mode"] == "view" for args in call.call_args_list))
            reader.side_effect = RuntimeError(SECRET)
            with self.assertRaisesRegex(host.HostAdapterError, "^timezone_unavailable$"):
                service.view_heartbeat()
        self.assertEqual(3, reader.call_count)

    def test_timezone_drift_after_dispatched_update_remains_unknown(self):
        service, calls = self.simulated()
        service._timezone_reader = mock.Mock(side_effect=["Asia/Tokyo", "Asia/Tokyo", "UTC"])
        before = service.view_heartbeat()
        with self.assertRaisesRegex(host.HostAdapterError, "^heartbeat_update_unknown$"):
            service.update_heartbeat(before, "shorten")
        self.assertEqual(1, sum(args["mode"] == "update" for _, args in calls))

    def test_cleanup_pauses_same_timer_when_timezone_changes_or_is_unavailable(self):
        for failure in ("UTC", RuntimeError(SECRET)):
            with self.subTest(kind=type(failure).__name__):
                write_config(self.home, config_values())
                service, calls = self.simulated()
                reader = mock.Mock()
                if isinstance(failure, Exception):
                    reader.side_effect = failure
                else:
                    reader.return_value = failure
                service._timezone_reader = reader
                before = service.view_heartbeat(for_pause=True)
                self.assertTrue(before.cleanup_only)
                self.assertEqual(before.timezone, "Asia/Tokyo")  # identity, not a current-zone observation
                after = service.update_heartbeat(before, "pause")
                self.assertEqual((after.status, after.id, after.rule), ("PAUSED", AUTOMATION, RULE))
                self.assertTrue(after.cleanup_only)
                self.assertEqual(after.identity_digest, before.identity_digest)
                reader.assert_not_called()
                self.assertEqual([args["mode"] for _, args in calls], ["view", "view", "update", "view"])

    def test_cleanup_snapshot_never_authorizes_active_write_or_stale_identity(self):
        service, calls = self.simulated()
        service._timezone_reader = mock.Mock(side_effect=RuntimeError(SECRET))
        before = service.view_heartbeat(for_pause=True)
        for action, snapshot, rule in [("shorten", before, None),
                                       ("arm", replace(before, status="PAUSED"), RULE)]:
            with self.subTest(action=action), self.assertRaisesRegex(host.HostAdapterError, "^unsupported_heartbeat_update$"):
                service.update_heartbeat(snapshot, action, rrule=rule)
        self.assertEqual([args["mode"] for _, args in calls], ["view"])
        write_config(self.home, {**config_values(), "prompt": "changed immutable fixture", "updated_at": 3})
        with self.assertRaisesRegex(host.HostAdapterError, "^heartbeat_changed$"):
            service.update_heartbeat(before, "pause")
        self.assertTrue(all(args["mode"] == "view" for _, args in calls))

    def test_cleanup_view_still_rejects_wrong_parent_and_nonboolean_mode(self):
        service = adapter(self.home, timezone_reader=lambda: "UTC")
        with mock.patch.object(service, "_call", return_value=("card",)):
            with self.assertRaises(host.HostAdapterError):
                service.view_heartbeat(for_pause=1)
            write_config(self.home, {**config_values(), "target_thread_id": OTHER})
            with self.assertRaises(host.HostAdapterError):
                service.view_heartbeat(for_pause=True)

    def test_production_factory_pins_same_node_timezone_reader(self):
        reader = mock.Mock(return_value="Asia/Tokyo")
        args = dict(metadata=META, automation_id=AUTOMATION, codex_home=self.home,
                    confirmed_timezone="Asia/Tokyo", timezone_source="host_node_intl", reviewer_ids=[CHILD])
        server = self.home / "server.mjs"
        server.write_text("offline fixture", encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_MCP_NODE_PATH": sys.executable,
                                         "CODEX_APP_TOOLS_PIPE_PATH": "offline-fixture"}), \
                mock.patch.object(host, "_node_timezone_reader", return_value=reader) as factory:
            service = host.PublicMcpHost.from_environment(server_path=server, **args)
            factory.assert_called_once_with(Path(sys.executable))
            with mock.patch.object(service, "_call", return_value=("card",)):
                service.view_heartbeat()
            reader.assert_called_once_with()
            with self.assertRaisesRegex(host.HostAdapterError, "^timezone_not_admitted$"):
                host.PublicMcpHost.from_environment(server_path=server, **args, timezone_reader=reader)


class TimezoneReaderTests(unittest.TestCase):
    def test_node_reader_is_bounded_sanitized_and_requires_cleanup(self):
        for response, clean, expected in [({"timezone": "Asia/Tokyo"}, True, True),
                                           ({"timezone": "Asia/Tokyo"}, False, False),
                                           ({"unknown": SECRET}, True, False),
                                           ({"timezone": None}, True, False)]:
            child = mock.Mock()
            child.result.return_value = response
            child.close.return_value = clean
            with mock.patch.object(host.relay, "_ChildSession", return_value=child) as launch:
                reader = host._node_timezone_reader(Path("fixture-node"))
                if expected:
                    self.assertEqual(reader(), "Asia/Tokyo")
                else:
                    with self.assertRaisesRegex(host.HostAdapterError, "^timezone_unavailable$"):
                        reader()
                child.close.assert_called_once_with()
                argv, cwd, deadline, cleanup = launch.call_args.args
                self.assertEqual(argv[:2], ("fixture-node", "-e"))
                self.assertIn("Intl.DateTimeFormat().resolvedOptions().timeZone", argv[2])
                self.assertIsNone(cwd)
                self.assertEqual(1, cleanup)
                child.send.assert_not_called()


class ExecutorTurnTests(unittest.TestCase):
    def test_actual_turn_forms_and_matching_aliases_preserve_metadata(self):
        for extra in ({"turnId": TURN}, {"openai/turnId": TURN}, {"openai/turn_id": TURN},
                      {"codexTurnId": TURN}, {"codex_turn_id": TURN}, {"turn_id": TURN},
                      {"turn": {"id": TURN}},
                      {"x-codex-turn-metadata": {"thread_id": PARENT, "turn_id": TURN}},
                      {"x-codex-turn-metadata": json.dumps({"thread_id": PARENT, "turn_id": TURN})}):
            metadata = {"threadId": PARENT, **extra}
            original = copy.deepcopy(metadata)
            self.assertEqual(host.executor_turn_id(metadata), TURN)
            self.assertEqual(metadata, original)
        metadata = {**META, "openai/turnId": TURN, "openai/turn_id": TURN,
                    "codexTurnId": TURN, "codex_turn_id": TURN, "turn_id": TURN,
                    "turn": {"id": TURN},
                    "x-codex-turn-metadata": {"turn_id": TURN, "turn": {"id": TURN}}}
        original = copy.deepcopy(metadata)
        self.assertEqual(host.executor_turn_id(metadata), TURN)
        self.assertEqual(metadata, original)

    def test_missing_malformed_and_conflicting_turns_have_no_identity_fallback(self):
        malformed = [{"threadId": PARENT},
                     {"threadId": PARENT, "x-codex-turn-metadata": {"turn": {"id": TURN}}},
                     {**META, "turnId": "mcp-turn-2"}, {**META, "turnId": None},
                     {**META, "turn": []}, {**META, "turn": {}},
                     {**META, "x-codex-turn-metadata": {"turn_id": None}},
                     {**META, "x-codex-turn-metadata": {"turn": []}},
                     {"turnId": TURN}, {**META, "openai/threadId": OTHER}]
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": PARENT, "CODEX_TURN_ID": TURN}):
            for metadata in malformed:
                with self.subTest(metadata=metadata), self.assertRaises(host.HostAdapterError):
                    host.executor_turn_id(metadata)
        conflicting = [{**META, key: OTHER} for key in
                       ("openai/turnId", "openai/turn_id", "codexTurnId", "codex_turn_id", "turn_id")]
        conflicting += [{**META, "turn": {"id": OTHER}}]
        for embedded in ({"turn_id": OTHER}, {"turn": {"id": OTHER}}):
            conflicting.extend({**META, "x-codex-turn-metadata": value}
                               for value in (embedded, json.dumps(embedded)))
        for metadata in conflicting:
            with self.subTest(metadata=metadata), self.assertRaisesRegex(host.HostAdapterError, "^caller_mismatch$"):
                host.executor_turn_id(metadata)


class DirectProbeTests(unittest.TestCase):
    @staticmethod
    def arguments(probe_id=OTHER):
        return {"threadId": PARENT, "hostId": "local", "prompt": (
            "これは同じ親チャットへの直接復帰を確認する実験通知です。"
            f"検証 ID は {probe_id} です。"
            f'review_wait_direct_ack(probe_id="{probe_id}") を呼び、受信を確認してください。'
            "確認結果を報告する以外に、通知・予約・再送を起動しないでください。")}

    @staticmethod
    def child(response, *, clean=True):
        child = mock.Mock()
        child.result.side_effect = [
            {"protocolVersion": host.relay.PROTOCOL_VERSION, "capabilities": {"tools": {}},
             "serverInfo": {"name": "offline", "version": "1"}}, response]
        child.close.return_value = clean
        return child

    def test_parent_read_has_no_destination_input_and_discards_private_fields(self):
        value = read_result()
        value["thread"]["id"] = PARENT
        service = adapter(Path.cwd())
        with mock.patch.object(service, "_json_call", return_value=value) as call:
            observed = service.read_parent()
            call.assert_called_once_with("read_thread", {"threadId": PARENT, "hostId": "local",
                "turnLimit": 1, "includeOutputs": False, "maxOutputCharsPerItem": 1})
            self.assertEqual(observed, host.ChildTurn(PARENT, TURN, "completed", "notLoaded"))
            self.assertNotIn(SECRET, repr(observed))
        with mock.patch.object(service, "_json_call", return_value=read_result()):
            with self.assertRaises(host.HostAdapterError):
                service.read_parent()
        self.assertEqual(service.reviewer_ids, (CHILD,))

    def test_same_parent_send_preserves_context_and_only_reports_transport_acceptance(self):
        metadata = {**META, "x-codex-turn-metadata": json.dumps({"thread_id": PARENT, "turn_id": TURN})}
        for is_error, expected in [(False, "accepted"), (True, "rejected")]:
            child = self.child({"content": [{"type": "text", "text": SECRET}], "isError": is_error})
            with mock.patch.object(host.relay, "_ChildSession", return_value=child) as launch:
                service = adapter(Path.cwd(), metadata=metadata)
                result = service.send_direct_probe(OTHER)
            self.assertEqual(result, expected)
            self.assertNotIn(SECRET, result)
            launch.assert_called_once()
            child.close.assert_called_once_with()
            frames = [host.relay.strict_json_loads(call.args[0]) for call in child.send.call_args_list]
            self.assertEqual([frame["method"] for frame in frames],
                             ["initialize", "notifications/initialized", "tools/call"])
            self.assertEqual(frames[-1]["params"], {"name": "send_message_to_thread",
                "arguments": self.arguments(), "_meta": metadata})
            self.assertEqual(service._metadata, metadata)

    def test_malformed_transport_and_cleanup_outcomes_are_unknown_without_retry(self):
        good = {"content": [{"type": "text", "text": SECRET}], "isError": False}
        malformed = [{"content": []}, {**good, "isError": 0}, {**good, "content": None},
                     {**good, "content": [{"type": "text", "text": 1}]},
                     {**good, "content": [{"type": "other", "text": SECRET}]},
                     {**good, "unexpected": SECRET}]
        for response, clean in [(value, True) for value in malformed] + [(good, False),
                ({**good, "isError": True}, False)]:
            child = self.child(response, clean=clean)
            with self.subTest(response=response, clean=clean), \
                    mock.patch.object(host.relay, "_ChildSession", return_value=child) as launch:
                self.assertEqual(adapter(Path.cwd()).send_direct_probe(OTHER), "unknown")
            launch.assert_called_once()
            child.close.assert_called_once_with()
            self.assertEqual(child.send.call_count, 3)
        for boundary in ("launch", "response", "cleanup"):
            child = self.child(good)
            if boundary == "response":
                child.result.side_effect = RuntimeError(SECRET)
            if boundary == "cleanup":
                child.close.side_effect = RuntimeError(SECRET)
            with mock.patch.object(host.relay, "_ChildSession",
                    side_effect=RuntimeError(SECRET) if boundary == "launch" else None,
                    return_value=child) as launch:
                self.assertEqual(adapter(Path.cwd()).send_direct_probe(OTHER), "unknown")
            launch.assert_called_once()
        with mock.patch.object(host.relay, "_ChildSession", return_value=self.child(
                {"content": [], "isError": False})):
            self.assertEqual(adapter(Path.cwd()).send_direct_probe(OTHER), "accepted")

    def test_bad_probe_or_executor_turn_cannot_dispatch_and_timer_call_stays_closed(self):
        with mock.patch.object(host.relay, "_ChildSession") as launch:
            service = adapter(Path.cwd())
            for probe in ("invented", None, {}, "urn:uuid:" + OTHER):
                with self.assertRaisesRegex(host.HostAdapterError, "^invalid_probe_id$"):
                    service.send_direct_probe(probe)
            for metadata in ({"threadId": PARENT}, {**META, "openai/turnId": OTHER}):
                with self.assertRaises(host.HostAdapterError):
                    adapter(Path.cwd(), metadata=metadata).send_direct_probe(OTHER)
            with self.assertRaisesRegex(host.HostAdapterError, "^host_call_failed$"):
                service._call("send_message_to_thread", self.arguments())
            launch.assert_not_called()


class HostBoundaryTests(unittest.TestCase):
    GOOD = {"content": [{"type": "text", "text": SECRET}], "isError": False}

    def assert_boundary(self, service, reason):
        with self.assertRaises(host.HostAdapterError) as caught:
            service._call("automation_update", {"mode": "view", "id": AUTOMATION})
        error = caught.exception
        self.assertEqual(error.code, "host_call_failed")
        self.assertEqual(str(error), "host_call_failed")
        self.assertEqual(error.args, ("host_call_failed",))
        self.assertEqual(error.boundary_reason, reason)
        self.assertNotIn(SECRET, json.dumps(vars(error)))

    def test_exception_keeps_legacy_code_and_only_fixed_optional_diagnostics(self):
        expected = {"host_launch_failed", "host_initialize_failed", "host_response_unavailable",
                    "host_cleanup_unknown", "host_tool_error", "host_response_invalid"}
        expected |= {"host_" + reason for reason in host.relay.RESPONSE_FAILURE_REASONS}
        self.assertEqual(host.HOST_BOUNDARY_REASONS, expected)
        self.assertIsInstance(host.HOST_BOUNDARY_REASONS, frozenset)
        for reason in expected:
            error = host.HostAdapterError("host_call_failed", boundary_reason=reason)
            self.assertEqual(error.boundary_reason, reason)
            self.assertEqual(str(error), "host_call_failed")
        for reason in (None, SECRET, {}, [], 1):
            self.assertIsNone(host.HostAdapterError("host_call_failed", boundary_reason=reason).boundary_reason)

    def test_launch_initialize_and_response_failures_remain_distinguishable(self):
        for boundary in ("launch", "initialize_response", "initialize_shape", "initialize_send",
                         "tool_response", "tool_send"):
            child = DirectProbeTests.child(self.GOOD)
            if boundary == "initialize_response":
                child.result.side_effect = RuntimeError(SECRET)
            elif boundary == "initialize_shape":
                child.result.side_effect = [{"private": SECRET}]
            elif boundary == "initialize_send":
                child.send.side_effect = RuntimeError(SECRET)
            elif boundary == "tool_response":
                initialized = child.result.side_effect
                child.result.side_effect = [next(initialized), host.relay.RelayError(SECRET)]
            elif boundary == "tool_send":
                child.send.side_effect = [None, None, OSError(SECRET)]
            reason = ("host_launch_failed" if boundary == "launch" else
                      "host_initialize_failed" if boundary.startswith("initialize") else
                      "host_response_unavailable")
            with self.subTest(boundary=boundary), mock.patch.object(host.relay, "_ChildSession",
                    side_effect=OSError(SECRET) if boundary == "launch" else None,
                    return_value=child) as launch:
                self.assert_boundary(adapter(Path.cwd()), reason)
            launch.assert_called_once()
            if boundary != "launch":
                child.close.assert_called_once_with()

    def test_valid_tool_failure_is_not_reported_as_permission_denial(self):
        child = DirectProbeTests.child({**self.GOOD, "isError": True})
        with mock.patch.object(host.relay, "_ChildSession", return_value=child):
            self.assert_boundary(adapter(Path.cwd()), "host_tool_error")
        child.close.assert_called_once_with()

    def test_closed_protocol_reason_refines_only_response_and_cleanup_still_wins(self):
        for detail in host.relay.RESPONSE_FAILURE_REASONS:
            for phase, clean, expected in [
                ("response", True, "host_" + detail),
                ("initialize", True, "host_initialize_failed"),
                ("response", False, "host_cleanup_unknown"),
            ]:
                child = DirectProbeTests.child(self.GOOD, clean=clean)
                error = host.relay.RelayError(response_reason=detail)
                initialized = next(child.result.side_effect)
                child.result.side_effect = ([initialized, error] if phase == "response" else error)
                with self.subTest(detail=detail, phase=phase, clean=clean), \
                        mock.patch.object(host.relay, "_ChildSession", return_value=child):
                    self.assert_boundary(adapter(Path.cwd()), expected)
                child.close.assert_called_once_with()

    def test_spoofed_or_unknown_protocol_reason_does_not_refine_host_failure(self):
        for error in (ValueError(SECRET), host.relay.RelayError(SECRET)):
            error.response_reason = ("rpc_invalid_params" if type(error) is ValueError else SECRET)
            child = DirectProbeTests.child(self.GOOD)
            child.result.side_effect = [next(child.result.side_effect), error]
            with mock.patch.object(host.relay, "_ChildSession", return_value=child):
                self.assert_boundary(adapter(Path.cwd()), "host_response_unavailable")

    def test_invalid_result_or_content_has_only_fixed_shape_reason(self):
        for response in (None, [], {"content": []}, {**self.GOOD, "isError": 1},
                         {**self.GOOD, "content": []}, {**self.GOOD, "content": None},
                         {**self.GOOD, "content": [{"type": "text", "text": SECRET, "private": SECRET}]},
                         {**self.GOOD, "content": [{"type": "text", "text": 1}]},
                         {"isError": True, "content": [{"type": "other", "text": SECRET}]}):
            child = DirectProbeTests.child(response)
            with self.subTest(response=response), \
                    mock.patch.object(host.relay, "_ChildSession", return_value=child):
                self.assert_boundary(adapter(Path.cwd()), "host_response_invalid")
            child.close.assert_called_once_with()

    def test_cleanup_failure_takes_priority_over_every_earlier_boundary(self):
        for boundary in ("initialize", "response", "tool_error", "invalid_content", "success"):
            for close_raises in (False, True):
                response = ({**self.GOOD, "isError": True} if boundary == "tool_error" else
                            {**self.GOOD, "content": None} if boundary == "invalid_content" else self.GOOD)
                child = DirectProbeTests.child(response, clean=False)
                if boundary == "initialize":
                    child.result.side_effect = RuntimeError(SECRET)
                elif boundary == "response":
                    initialized = child.result.side_effect
                    child.result.side_effect = [next(initialized), RuntimeError(SECRET)]
                if close_raises:
                    child.close.side_effect = RuntimeError(SECRET)
                with self.subTest(boundary=boundary, close_raises=close_raises), \
                        mock.patch.object(host.relay, "_ChildSession", return_value=child) as launch:
                    self.assert_boundary(adapter(Path.cwd()), "host_cleanup_unknown")
                child.close.assert_called_once_with()
                launch.assert_called_once()


class DownstreamCorrelationTests(unittest.TestCase):
    ALIASES = ("openai/toolCallId", "openai/tool_call_id", "codexCallId",
               "codex_call_id", "callId", "call_id")
    GOOD = {"content": [{"type": "text", "text": "ok"}], "isError": False}

    def test_only_outer_call_aliases_are_removed_from_each_transient_copy(self):
        common = {**META, "openai/threadId": PARENT, "openai/turnId": TURN,
                  "x-codex-turn-metadata": json.dumps({"thread_id": PARENT, "turn_id": TURN}),
                  "opaque": {"private": SECRET}}
        variants = [{}, *[{key: "outer-call"} for key in self.ALIASES],
                    {"call": {"id": "outer-call", "keep": SECRET}},
                    {"call": "opaque"},
                    {**{key: "outer-call" for key in self.ALIASES}, "call": {"id": "outer-call"}}]
        for extra in variants:
            metadata = copy.deepcopy({**common, **extra})
            original = copy.deepcopy(metadata)
            child = DirectProbeTests.child(self.GOOD)
            with self.subTest(extra=extra), mock.patch.object(host.relay, "_ChildSession", return_value=child):
                service = adapter(Path.cwd(), metadata=metadata)
                service._call("automation_update", {"mode": "view", "id": AUTOMATION})
            forwarded = json.loads(child.send.call_args_list[-1].args[0])["params"]["_meta"]
            expected = copy.deepcopy(metadata)
            for key in self.ALIASES:
                expected.pop(key, None)
            if isinstance(expected.get("call"), dict):
                expected["call"].pop("id", None)
            self.assertEqual(forwarded, expected)
            self.assertEqual(metadata, original)
            self.assertEqual(service._metadata, original)

    def test_two_child_reads_and_reservation_view_do_not_share_the_outer_claim(self):
        # Model the unmodified public wrapper's selection/fallback and the app's
        # duplicate guard, which does not include the downstream tool name.
        claimed, calls = set(), []
        aliases = self.ALIASES

        class Peer:
            def send(self, data):
                self.frame = json.loads(data)

            def result(self, request_id):
                if request_id == 1:
                    return {"protocolVersion": host.relay.PROTOCOL_VERSION, "capabilities": {"tools": {}},
                            "serverInfo": {"name": "offline", "version": "1"}}
                call = self.frame["params"]
                metadata = call["_meta"]
                call_id = next((metadata[k] for k in aliases if isinstance(metadata.get(k), str)
                                and metadata[k].strip()), None)
                if call_id is None and isinstance(metadata.get("call"), dict):
                    call_id = metadata["call"].get("id")
                call_id = call_id or "mcp-call-" + str(uuid4())
                key = ("local", metadata["threadId"], metadata["turnId"], call_id)
                if key in claimed:
                    raise host.relay.RelayError(response_reason="rpc_error")
                claimed.add(key)
                calls.append(call)
                if call["name"] == "read_thread":
                    value = read_result()
                    value["thread"]["id"] = call["arguments"]["threadId"]
                    return {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
                if call != {"name": "automation_update", "arguments": {"mode": "view", "id": AUTOMATION},
                            "_meta": metadata}:
                    raise AssertionError("unexpected operation")
                return DownstreamCorrelationTests.GOOD

            def close(self):
                return True

        metadata = {**META, **{key: "outer-call" for key in aliases}, "call": {"id": "outer-call"}}
        original = copy.deepcopy(metadata)
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_config(home, {**config_values(), "status": "PAUSED"})
            service = adapter(home, metadata=metadata, reviewer_ids=[CHILD, OTHER])
            with mock.patch.object(host.relay, "_ChildSession", side_effect=lambda *a, **kw: Peer()) as launch:
                self.assertEqual(service.read_child(CHILD).child_id, CHILD)
                self.assertEqual(service.read_child(OTHER).child_id, OTHER)
                self.assertEqual(service.view_heartbeat().status, "PAUSED")
            self.assertEqual(launch.call_count, 3)
        self.assertEqual([c["name"] for c in calls], ["read_thread", "read_thread", "automation_update"])
        self.assertEqual(len(claimed), 3)
        self.assertTrue(all(key[1:3] == (PARENT, TURN) and key[3] != "outer-call" for key in claimed))
        self.assertEqual(metadata, original)

    def test_direct_error_or_unknown_does_not_get_a_second_correlation_attempt(self):
        metadata = {**META, **{key: "outer-call" for key in self.ALIASES}, "call": {"id": "outer-call"}}
        for failure, expected in [("rejected", "rejected"), ("rpc", "unknown"), ("cleanup", "unknown")]:
            child = DirectProbeTests.child({**self.GOOD, "isError": failure == "rejected"},
                                          clean=failure != "cleanup")
            if failure == "rpc":
                child.result.side_effect = [next(child.result.side_effect),
                    host.relay.RelayError(response_reason="rpc_error")]
            with self.subTest(failure=failure), mock.patch.object(host.relay, "_ChildSession", return_value=child) as launch:
                self.assertEqual(adapter(Path.cwd(), metadata=metadata).send_direct_probe(OTHER), expected)
            launch.assert_called_once()
            self.assertEqual(child.send.call_count, 3)  # handshake + one call
            child.close.assert_called_once_with()


PEER = r'''
import json,sys
expected=json.loads(sys.argv[1]); response=json.loads(sys.argv[2]); mode=sys.argv[3]
for line in sys.stdin.buffer:
 request=json.loads(line)
 if request['method']=='initialize':
  value={'protocolVersion':request['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'offline','version':'1'}}
 elif request['method']=='notifications/initialized':continue
 else:
  if request['params']!=expected:sys.exit(7)
  if mode=='error':response={'content':[{'type':'text','text':'PRIVATE_BODY_MUST_NOT_ESCAPE'}],'isError':True}
  value=response
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':value}),flush=True)
'''


class TransportTests(unittest.TestCase):
    def test_direct_probe_real_offline_pipes_preserve_exact_arguments_and_reap(self):
        expected = {"name": "send_message_to_thread", "arguments": DirectProbeTests.arguments(), "_meta": META}
        response = {"content": [{"type": "text", "text": SECRET}], "isError": False}
        original = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            return process

        for mode, outcome in [("success", "accepted"), ("error", "rejected")]:
            service = adapter(Path.cwd(), command=[sys.executable, "-B", "-u", "-c", PEER,
                              json.dumps(expected), json.dumps(response), mode], timeout_seconds=2)
            with mock.patch.object(host.relay.subprocess, "Popen", side_effect=launch):
                self.assertEqual(service.send_direct_probe(OTHER), outcome)
            self.assertIsNotNone(children[-1].poll())
            self.assertTrue(all(stream.closed for stream in [children[-1].stdin, children[-1].stdout, children[-1].stderr]))
        self.assertEqual(len(children), 2)

    def test_real_pipes_forward_unchanged_context_and_sanitize_errors_then_reap(self):
        arguments = {"threadId": CHILD, "hostId": "local", "turnLimit": 1,
                     "includeOutputs": False, "maxOutputCharsPerItem": 1}
        expected = {"name": "read_thread", "arguments": arguments, "_meta": META}
        response = {"content": [{"type": "text", "text": json.dumps(read_result())}], "isError": False}
        original = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            return process

        for mode in ["success", "error"]:
            service = adapter(Path.cwd(), command=[sys.executable, "-B", "-u", "-c", PEER,
                              json.dumps(expected), json.dumps(response), mode], timeout_seconds=2)
            with mock.patch.object(host.relay.subprocess, "Popen", side_effect=launch):
                if mode == "success":
                    self.assertEqual(service.read_child(CHILD).status, "completed")
                else:
                    with self.assertRaisesRegex(host.HostAdapterError, "^host_call_failed$"):
                        service.read_child(CHILD)
            self.assertIsNotNone(children[-1].poll())
            self.assertTrue(all(stream.closed for stream in [children[-1].stdin, children[-1].stdout, children[-1].stderr]))


if __name__ == "__main__":
    unittest.main()
