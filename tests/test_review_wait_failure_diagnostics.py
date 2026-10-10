"""Failure diagnostics through the normal entry; isolated journals and fake hosts."""

from dataclasses import replace
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_review_wait_managed as flow
from tests.m14_test_support import file_snapshot
from task_governance_tool.review_wait_runtime import managed_wait, project_server, review_wait_repository
from task_governance_tool.review_wait_runtime.failure_diagnostics import CallDiagnostics, DiagnosticError
from task_governance_tool.review_wait_runtime.review_wait_basis import BasisError
from task_governance_tool.review_wait_runtime.review_wait_host import HostAdapterError
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_server import ReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_service import ServiceError


FIELDS = {"version", "stage", "reason", "local_state", "host_mutation", "retained_effects",
          "cleanup", "recovery", "turn_end"}
PRIVATE = "PRIVATE_PROVIDER_BODY C:/private/token secret=value " * 200


class BoundaryDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        root = Path.cwd()
        self.session = project_server.ProjectReviewWaitSession(
            project_server.ProjectConfig(root, root / "offline-server.mjs", root, "UTC"),
            managed_host_factory=mock.Mock(side_effect=AssertionError("no host permitted")))
        self.session._enabled = mock.Mock(return_value=True)

    def call(self, metadata=flow.META):
        return self.session.handle("wait", {"task_id": flow.TASK, "reviewer_ids": [flow.CHILD]}, metadata)

    def assert_no_effects(self, result, stage, reason):
        self.assertFalse(result["ok"], result)
        diagnostic = result["diagnostic"]
        self.assertEqual(FIELDS, set(diagnostic))
        self.assertEqual((stage, reason), (diagnostic["stage"], diagnostic["reason"]))
        self.assertEqual("not_attempted", diagnostic["local_state"])
        self.assertEqual("not_dispatched", diagnostic["host_mutation"])
        self.assertEqual("not_inspected", diagnostic["retained_effects"])
        self.assertEqual("not_attempted", diagnostic["cleanup"])
        self.assertEqual("report_limitation", diagnostic["turn_end"])
        self.session.managed_host_factory.assert_not_called()

    def test_metadata_failure_is_not_project_or_connection_failure(self):
        with mock.patch.object(self.session, "_location") as location:
            result = self.call(metadata={})
        self.assertEqual("review_wait_unavailable", result["error"])
        self.assert_no_effects(result, "executor_admission", "executor_context_required")
        self.assertEqual("correct_request", result["diagnostic"]["recovery"])
        self.session._enabled.assert_not_called()
        location.assert_not_called()

    def test_off_and_unreadable_policy_keep_legacy_error_but_different_recovery(self):
        # Exercise the real policy read boundary, including its exception path.
        del self.session._enabled
        with mock.patch.object(self.session, "_location") as location:
            with mock.patch.object(project_server, "read_choices", return_value=(None, {"review_wait": False})):
                off = self.call()
            with mock.patch.object(project_server, "read_choices", side_effect=ValueError(PRIVATE)):
                broken = self.call()
        self.assertEqual("review_wait_not_enabled", off["error"])
        self.assertEqual(off["error"], broken["error"])
        self.assert_no_effects(off, "policy", "review_wait_not_enabled")
        self.assert_no_effects(broken, "policy", "policy_unreadable")
        self.assertEqual("respect_off", off["diagnostic"]["recovery"])
        self.assertEqual("check_configuration", broken["diagnostic"]["recovery"])
        self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(broken))
        location.assert_not_called()

    def test_structural_project_failure_retains_fixed_reason(self):
        for reason in ("project_root_uninspectable", "unsupported_install_layout", "state_ignore_required"):
            with self.subTest(reason=reason):
                inspection = mock.Mock(scope=None)
                inspection.first_issue.return_value = SimpleNamespace(code=reason)
                with mock.patch.object(project_server, "inspect_project_scope", return_value=inspection), \
                        mock.patch.object(project_server, "resolve_project_state") as resolve:
                    result = self.call()
                self.assertEqual("review_wait_unavailable", result["error"])
                self.assert_no_effects(result, "project_admission", reason)
                self.assertEqual("check_project", result["diagnostic"]["recovery"])
                resolve.assert_not_called()

    def test_project_binding_and_schema_failures_do_not_claim_stale_runtime(self):
        inspection = mock.Mock(scope=SimpleNamespace(skill_root=Path.cwd(), canonical_repo=Path.cwd()))
        inspection.first_issue.return_value = None
        for code, binding, expected in ((None, "mismatch", "project_relocation_required"),
                                        ("schema_too_new", "matching", "schema_too_new"),
                                        ("project_state_unreadable", "matching", "project_state_unreadable")):
            with self.subTest(code=code, binding=binding):
                resolution = SimpleNamespace(error_code=code, binding=binding,
                                             layout="fixed_current_v1", target=object())
                with mock.patch.object(project_server, "inspect_project_scope", return_value=inspection), \
                        mock.patch.object(project_server, "resolve_project_state", return_value=resolution):
                    result = self.call()
                self.assert_no_effects(result, "project_admission", expected)
                self.assertEqual("check_project", result["diagnostic"]["recovery"])
                self.assertNotIn("reload", json.dumps(result))

    def test_untyped_private_exception_cannot_impersonate_fixed_reason(self):
        for error in (ValueError("schema_too_new"), ValueError(PRIVATE), PermissionError(PRIVATE)):
            with self.subTest(error_type=type(error).__name__), \
                    mock.patch.object(self.session, "_location", side_effect=error):
                result = self.call()
            self.assert_no_effects(result, "project_admission", "unavailable")
            self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(result))
            self.assertNotIn("secret=value", json.dumps(result))

    def test_closed_vocabulary_is_bounded_even_for_rejected_large_values(self):
        diagnostic = CallDiagnostics(stage=PRIVATE, reason=PRIVATE, local_state=PRIVATE,
            host_mutation=PRIVATE, retained_effects=PRIVATE, cleanup=PRIVATE)
        result = diagnostic.failure({"ok": False, "error": "review_wait_unavailable"})
        value = result["diagnostic"]
        self.assertEqual(FIELDS, set(value))
        self.assertEqual(1, value["version"])
        self.assertEqual(("request", "unavailable"), (value["stage"], value["reason"]))
        self.assertLessEqual(len(json.dumps(value).encode("utf-8")), 2048)
        self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(result))
        self.assertNotIn("secret=value", json.dumps(result))

    def test_diagnostic_does_not_leak_from_one_call_to_the_next(self):
        with mock.patch.object(self.session, "_location", side_effect=DiagnosticError("schema_too_new")):
            first = self.call()
        self.session._enabled.return_value = False
        second = self.call()
        self.assertEqual("schema_too_new", first["diagnostic"]["reason"])
        self.assert_no_effects(second, "policy", "review_wait_not_enabled")
        self.assertEqual("respect_off", second["diagnostic"]["recovery"])


class ManagedFailureDiagnosticsTests(unittest.TestCase):
    # Reuse only the existing fixture helpers, not its complete test suite.
    setUp = flow.ManagedFlowTests.setUp
    close_all = flow.ManagedFlowTests.close_all
    factory = flow.ManagedFlowTests.factory
    make = flow.ManagedFlowTests.make
    call = flow.ManagedFlowTests.call
    inspect = flow.ManagedFlowTests.inspect
    until = flow.ManagedFlowTests.until
    ended = flow.ManagedFlowTests.ended

    def assert_diagnostic(self, result, **expected):
        self.assertFalse(result["ok"], result)
        diagnostic = result["diagnostic"]
        self.assertEqual(FIELDS, set(diagnostic))
        self.assertEqual("report_limitation", diagnostic["turn_end"])
        for name, value in expected.items():
            self.assertEqual(value, diagnostic[name], (name, result))
        self.assertLessEqual(len(json.dumps(diagnostic).encode("utf-8")), 2048)
        self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(result))
        self.assertNotIn("DO_NOT_SAVE_THIS", json.dumps(result))
        return diagnostic

    def operational_snapshot(self):
        # Windows denies reading the byte held by an active OS writer lease.
        # Compare every durable payload byte and the complete file inventory;
        # lease siblings contribute their structural size, not locked content.
        return {path.relative_to(self.root).as_posix():
                ("lease", path.stat().st_size) if path.suffix == ".lock" else ("payload", path.read_bytes())
                for path in self.root.rglob("*") if path.is_file()}

    def test_typed_basis_failure_is_reported_before_any_local_or_host_write(self):
        with mock.patch.object(self.session, "basis_factory", side_effect=BasisError("wait_basis_response_invalid")):
            result = self.call()
        self.assertEqual("review_wait_unavailable", result["error"])
        self.assert_diagnostic(result, stage="basis_before", reason="wait_basis_response_invalid",
            local_state="not_attempted", host_mutation="not_dispatched", retained_effects="not_inspected")
        self.assertEqual([], self.effects)
        self.assertFalse(self.paths.review_wait_root.exists())

    def test_typed_host_failure_keeps_connection_reason_without_permission_inference(self):
        error = HostAdapterError("host_call_failed", boundary_reason="host_tool_error")
        with mock.patch.object(self.session, "managed_host_factory", side_effect=error):
            result = self.call()
        self.assert_diagnostic(result, stage="host_context", reason="host_tool_error",
            recovery="check_connection", local_state="not_attempted", host_mutation="not_dispatched",
            retained_effects="not_inspected")
        self.assertEqual([], self.effects)
        self.assertNotIn("permission", json.dumps(result))

    def test_child_read_failure_has_no_mutation_and_only_observed_absence(self):
        original = self.session.managed_host_factory
        def unavailable(**kwargs):
            host = original(**kwargs)
            host.read_child = mock.Mock(side_effect=HostAdapterError("host_call_failed",
                                               boundary_reason="host_response_unavailable"))
            return host
        self.session.managed_host_factory = unavailable
        result = self.call()
        self.assert_diagnostic(result, stage="child_read", reason="host_response_unavailable",
            local_state="not_attempted", host_mutation="not_dispatched", retained_effects="absent")
        self.assertEqual([], self.effects)
        self.assertFalse(self.paths.review_wait_root.exists())

    def test_basis_change_after_parent_read_keeps_basis_stage_without_writes(self):
        original = self.session.managed_host_factory
        parent_reads = []
        def changed(**kwargs):
            host = original(**kwargs)
            read_parent = host.read_parent
            def parent():
                parent_reads.append(True)
                self.basis = replace(self.basis, target_generation=self.basis.target_generation + 1)
                return read_parent()
            host.read_parent = parent
            return host
        self.session.managed_host_factory = changed
        result = self.call()
        self.assertEqual([True], parent_reads)
        self.assert_diagnostic(result, stage="basis_after", reason="binding_changed",
            local_state="not_attempted", host_mutation="not_dispatched", retained_effects="absent")
        self.assertEqual([], self.effects)
        self.assertFalse(self.paths.review_wait_root.exists())

    def test_basis_reread_error_is_not_misreported_as_parent_read_error(self):
        basis_reader = mock.Mock(side_effect=[replace(self.basis, wait_id="request"),
                                            BasisError("wait_basis_response_invalid")])
        with mock.patch.object(self.session, "basis_factory", return_value=basis_reader):
            result = self.call()
        self.assertEqual(2, basis_reader.call_count)
        self.assert_diagnostic(result, stage="basis_after", reason="wait_basis_response_invalid",
            local_state="not_attempted", host_mutation="not_dispatched", retained_effects="absent")
        self.assertEqual([], self.effects)
        self.assertFalse(self.paths.review_wait_root.exists())

    def failing_operation(self, operation, result):
        original = ReviewWaitSession.handle
        def handle(session, actual, arguments, metadata):
            if actual == operation:
                return result
            return original(session, actual, arguments, metadata)
        return mock.patch.object(ReviewWaitSession, "handle", new=handle)

    def test_prepare_reason_survives_confirmed_cleanup_at_normal_entry(self):
        with self.failing_operation("prepare", {"ok": False, "error": "candidate_unavailable",
                                               "stage": "child_read", "reason": "host_response_invalid"}):
            result = self.call()
        self.assertEqual("review_wait_unresolved", result["error"])
        self.assert_diagnostic(result, stage="child_read", reason="host_response_invalid",
            local_state="may_have_changed", host_mutation="confirmed", retained_effects="settled",
            cleanup="confirmed", recovery="check_connection")
        self.assertEqual(["create", "delete"], self.effects)
        self.assertEqual("closed", self.inspect()["phase"])

    def test_actual_prepare_policy_failure_survives_bootstrap_and_cleanup(self):
        original = self.session.managed_host_factory
        def policy_breaks_after_create(**kwargs):
            host = original(**kwargs)
            if kwargs["automation_id"] == "pending":
                create = host.create_heartbeat
                def created(rule):
                    automation = create(rule)
                    self.session._enabled = mock.Mock(side_effect=ValueError(PRIVATE))
                    return automation
                host.create_heartbeat = created
            return host
        self.session.managed_host_factory = policy_breaks_after_create
        result = self.call()
        # This traverses the actual ReviewWaitSession.prepare basis reader and
        # its bootstrap classifier, rather than supplying a synthetic failure.
        self.assertEqual("review_wait_unresolved", result["error"])
        self.assert_diagnostic(result, stage="basis_before", reason="policy_unreadable",
            local_state="may_have_changed", host_mutation="confirmed", retained_effects="settled",
            cleanup="confirmed", recovery="check_configuration")
        self.assertEqual(["create", "delete"], self.effects)
        self.assertEqual("closed", self.inspect()["phase"])

    def test_cleanup_unknown_takes_recovery_priority_but_preserves_prepare_reason(self):
        self.fail_delete = True
        with self.failing_operation("prepare", {"ok": False, "error": "candidate_unavailable",
                "stage": "host_context", "reason": "invalid_host_configuration"}):
            result = self.call()
        self.assert_diagnostic(result, stage="host_context", reason="invalid_host_configuration",
            local_state="may_have_changed", host_mutation="may_have_occurred",
            retained_effects="present_unresolved", cleanup="unresolved", recovery="inspect_existing")
        self.assertEqual(["create", "delete"], self.effects)
        self.assertEqual("unknown", self.inspect()["phase"])
        self.assertFalse(self.call("stop")["ok"])
        self.assertEqual(["create", "delete"], self.effects)

    def test_start_failure_keeps_origin_after_known_paused_timer_cleanup(self):
        with self.failing_operation("direct_delete_start", {"ok": False, "error": "direct_probe_unavailable"}):
            result = self.call()
        self.assertEqual("review_wait_unresolved", result["error"])
        self.assert_diagnostic(result, stage="start", reason="direct_probe_unavailable",
            local_state="may_have_changed", host_mutation="confirmed", retained_effects="settled",
            cleanup="confirmed")
        self.assertEqual(["create", "delete"], self.effects)
        self.assertEqual("closed", self.inspect()["phase"])

    def test_create_unknown_then_restart_does_not_replay_or_claim_absence(self):
        self.fail_create = True
        result = self.call()
        self.assertEqual("heartbeat_create_unknown", result["error"])
        self.assert_diagnostic(result, stage="create", reason="heartbeat_create_unknown", host_mutation="may_have_occurred",
            local_state="may_have_changed", retained_effects="present_unresolved", recovery="inspect_existing")
        restarted = self.make()
        repeated = self.call(session=restarted)
        self.assert_diagnostic(repeated, host_mutation="not_dispatched",
            retained_effects="present_unresolved", recovery="inspect_existing")
        stopped = self.call("stop", session=restarted)
        self.assert_diagnostic(stopped, host_mutation="not_dispatched", cleanup="unresolved",
            retained_effects="present_unresolved", recovery="inspect_existing")
        self.assertEqual(["create"], self.effects)

    def test_unknown_arm_blocks_stop_and_new_turn_without_competing_cleanup(self):
        self.fail_arm = True
        first = self.call()
        self.assert_diagnostic(first, stage="start", host_mutation="may_have_occurred",
            cleanup="unresolved", retained_effects="present_unresolved", recovery="inspect_existing")
        stop = self.call("stop")
        self.assert_diagnostic(stop, host_mutation="not_dispatched", cleanup="unresolved",
            retained_effects="present_unresolved")
        self.current_parent = replace(self.current_parent, turn_id=flow.NEW_TURN)
        next_turn = self.call(metadata={"threadId": flow.PARENT, "turnId": flow.NEW_TURN})
        self.assert_diagnostic(next_turn, host_mutation="not_dispatched", cleanup="unresolved",
            retained_effects="present_unresolved", recovery="inspect_existing")
        self.assertEqual(["create", "arm"], self.effects)

    def test_actual_arm_failure_retains_nested_host_reason_without_replay(self):
        original = self.session.managed_host_factory
        def arm_fails(**kwargs):
            host = original(**kwargs)
            if kwargs["automation_id"] == "pending":
                update = host.update_heartbeat
                def arm(before, action, **options):
                    if action == "arm":
                        self.effects.append(action)
                        raise HostAdapterError("host_call_failed", boundary_reason="host_tool_error")
                    return update(before, action, **options)
                host.update_heartbeat = arm
            return host
        self.session.managed_host_factory = arm_fails
        result = self.call()
        self.assert_diagnostic(result, stage="start", reason="host_tool_error",
            host_mutation="may_have_occurred", retained_effects="present_unresolved",
            cleanup="unresolved", recovery="inspect_existing")
        saved = self.inspect()["delivery"]
        self.assertEqual("host_tool_error", saved["reason"])
        self.assertEqual("unknown", saved["timer_phase"])
        stop = self.call("stop")
        self.assert_diagnostic(stop, host_mutation="not_dispatched", cleanup="unresolved")
        self.current_parent = replace(self.current_parent, turn_id=flow.NEW_TURN)
        self.assertFalse(self.call(metadata={"threadId": flow.PARENT, "turnId": flow.NEW_TURN})["ok"])
        self.assertEqual(["create", "arm"], self.effects)

    def test_unusable_nested_start_reason_has_fixed_fallback(self):
        states = [
            {"status": "suppressed", "timer_phase": "unknown"},
            *({"status": "suppressed", "timer_phase": "unknown", "reason": reason}
              for reason in (None, "unexpected_code", PRIVATE, [PRIVATE], {"private": PRIVATE}, True)),
            None, [PRIVATE], PRIVATE,
        ]
        for index, state in enumerate(states, start=2):
            task_id = "tg_task_" + format(index, "016x")
            with self.subTest(case=index), self.failing_operation("direct_delete_start",
                    {"ok": True, "worker_alive": False, "state": state}):
                result = self.call(task_id=task_id)
            self.assert_diagnostic(result, stage="start", reason="start_unavailable",
                local_state="may_have_changed", host_mutation="confirmed",
                retained_effects="settled", cleanup="confirmed")
            self.assertEqual("closed", self.call("inspect", task_id=task_id)["phase"])
        self.assertEqual(["create", "delete"] * len(states), self.effects)

    def test_unknown_delete_is_retained_without_send_or_replacement(self):
        self.fail_delete = True
        self.assertTrue(self.call()["ok"])
        self.ended()
        self.until(lambda: self.inspect()["delivery"]["timer_phase"] == "unknown")
        result = self.call("stop")
        self.assert_diagnostic(result, stage="cleanup", host_mutation="not_dispatched",
            local_state="not_attempted", retained_effects="present_unresolved", cleanup="unresolved",
            recovery="inspect_existing")
        self.current_parent = flow.ChildTurn(flow.PARENT, flow.NEW_TURN, "inProgress", "active")
        self.assertFalse(self.call(metadata={"threadId": flow.PARENT, "turnId": flow.NEW_TURN})["ok"])
        self.assertEqual(["create", "arm", "delete"], self.effects)

    def test_unknown_pause_blocks_repeated_cleanup_and_new_wait(self):
        self.assertTrue(self.call()["ok"])
        host = self.hosts["managed-1"]
        original = host.update_heartbeat
        def unknown_pause(before, action, **kwargs):
            if action == "pause":
                self.effects.append(action)
                raise HostAdapterError("heartbeat_update_unknown")
            return original(before, action, **kwargs)
        host.update_heartbeat = unknown_pause
        result = self.call("stop")
        self.assert_diagnostic(result, stage="cleanup", host_mutation="may_have_occurred",
            local_state="may_have_changed", retained_effects="present_unresolved",
            cleanup="unresolved", recovery="inspect_existing")
        self.assertEqual("unknown", self.inspect()["delivery"]["timer_phase"])
        repeated = self.call("stop")
        self.assert_diagnostic(repeated, host_mutation="not_dispatched", cleanup="unresolved",
            retained_effects="present_unresolved")
        self.current_parent = replace(self.current_parent, turn_id=flow.NEW_TURN)
        self.assertFalse(self.call(metadata={"threadId": flow.PARENT, "turnId": flow.NEW_TURN})["ok"])
        self.assertEqual(["create", "arm", "pause"], self.effects)

    def test_unknown_send_is_retained_separately_from_this_calls_no_dispatch(self):
        self.auto_receive = False
        self.assertTrue(self.call()["ok"])
        self.hosts["managed-1"].outcome = "unknown"
        self.ended()
        self.until(lambda: self.inspect()["delivery"]["status"] == "unknown")
        result = self.call("stop")
        self.assert_diagnostic(result, host_mutation="not_dispatched", local_state="not_attempted",
            retained_effects="present_unresolved", cleanup="unresolved", recovery="inspect_existing")
        self.current_parent = flow.ChildTurn(flow.PARENT, flow.NEW_TURN, "inProgress", "active")
        repeated = self.call(metadata={"threadId": flow.PARENT, "turnId": flow.NEW_TURN})
        self.assert_diagnostic(repeated, host_mutation="not_dispatched", retained_effects="present_unresolved")
        self.assertEqual(["create", "arm", "delete", "send"], self.effects)

    def test_inspection_failure_does_not_write_or_recover_saved_unknown_effect(self):
        self.fail_create = True
        self.assertFalse(self.call()["ok"])
        before = file_snapshot(self.root)
        with mock.patch.object(flow.RequestRepository, "read", side_effect=RepositoryError("state_unreadable")):
            failed = self.inspect()
        self.assert_diagnostic(failed, stage="request_read", reason="state_unreadable",
            local_state="not_attempted", host_mutation="not_dispatched", retained_effects="not_inspected")
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual("unknown", self.inspect()["phase"])
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual(["create"], self.effects)

    def test_closed_wait_inspect_direct_read_failure_invalidates_settled_observation(self):
        self.assertTrue(self.call()["ok"])
        self.assertEqual("stopped", self.call("stop")["status"])
        expected = self.inspect()
        self.assertEqual("closed", expected["phase"])
        self.assertIn("delivery", expected)
        before, effects = file_snapshot(self.root), list(self.effects)
        direct_repository = mock.Mock()
        direct_repository.for_wait.return_value.read.side_effect = RepositoryError("state_unreadable")
        # Patch this orchestration boundary only, not the worker's repository.
        with mock.patch.object(managed_wait, "DirectRepository", direct_repository):
            result = self.inspect()
        direct_repository.for_wait.return_value.read.assert_called_once()
        self.assert_diagnostic(result, stage="inspection", reason="state_unreadable",
            local_state="not_attempted", host_mutation="not_dispatched",
            retained_effects="not_inspected", cleanup="not_attempted")
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual(effects, self.effects)
        # A later successful complete read restores the ordinary projection.
        self.assertEqual(expected, self.inspect())
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual(effects, self.effects)

    def test_readiness_summary_failure_invalidates_previous_request_observation(self):
        expected = self.call()
        self.assertTrue(expected["ok"])
        before, effects = self.operational_snapshot(), list(self.effects)
        direct = self.session.sessions["managed-1"].direct
        with mock.patch.object(direct, "_summary", side_effect=RepositoryError("state_unreadable")) as read:
            result = self.call()
        read.assert_called_once()
        self.assert_diagnostic(result, stage="readiness", reason="state_unreadable",
            local_state="may_have_changed", host_mutation="not_dispatched",
            retained_effects="not_inspected", cleanup="not_attempted", recovery="inspect_existing")
        self.assertEqual(before, self.operational_snapshot())
        self.assertEqual(effects, self.effects)
        self.assertEqual({**expected, "replayed": True}, self.call())
        self.assertEqual(before, self.operational_snapshot())
        self.assertEqual(effects, self.effects)

    def test_cleanup_initial_direct_read_failure_never_dispatches_cleanup(self):
        self.assertTrue(self.call()["ok"])
        before, effects = self.operational_snapshot(), list(self.effects)
        direct_repository = mock.Mock()
        direct_repository.for_wait.return_value.read.side_effect = RepositoryError("state_unreadable")
        with mock.patch.object(managed_wait, "DirectRepository", direct_repository):
            result = self.call("stop")
        direct_repository.for_wait.return_value.read.assert_called_once()
        self.assert_diagnostic(result, stage="cleanup", reason="state_unreadable",
            local_state="not_attempted", host_mutation="not_dispatched",
            retained_effects="not_inspected", cleanup="unresolved", recovery="inspect_existing")
        self.assertEqual(before, self.operational_snapshot())
        self.assertEqual(effects, self.effects)
        self.assertEqual("active", self.inspect()["delivery"]["timer_phase"])
        self.assertEqual("stopped", self.call("stop")["status"])
        self.assertEqual("closed", self.inspect()["phase"])
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)

    def test_cleanup_post_cancel_read_failure_keeps_uncertainty_without_delete(self):
        self.assertTrue(self.call()["ok"])
        path = self.paths.review_wait_store("managed-1")
        repository = managed_wait.DirectRepository.for_wait(path)
        first = repository.read()
        reads, after_cancel = [], []
        def read():
            reads.append(True)
            if len(reads) == 1:
                return first
            after_cancel.append(self.operational_snapshot())
            raise RepositoryError("state_unreadable")
        direct_repository = mock.Mock()
        direct_repository.for_wait.return_value.read.side_effect = read
        with mock.patch.object(managed_wait, "DirectRepository", direct_repository):
            result = self.call("stop")
        self.assertEqual(2, len(reads))
        self.assert_diagnostic(result, stage="cleanup", reason="state_unreadable",
            local_state="may_have_changed", host_mutation="may_have_occurred",
            retained_effects="not_inspected", cleanup="unresolved", recovery="inspect_existing")
        self.assertEqual(after_cancel[0], self.operational_snapshot())
        self.assertEqual(["create", "arm", "pause"], self.effects)
        observed = self.inspect()
        self.assertEqual("started", observed["phase"])
        self.assertEqual("paused", observed["delivery"]["timer_phase"])
        # Confirmed readback permits the existing explicit cleanup, without
        # replaying activation or the already confirmed pause.
        self.assertEqual("stopped", self.call("stop")["status"])
        self.assertEqual("closed", self.inspect()["phase"])
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)

    def test_legacy_direct_status_internal_read_failure_invalidates_outer_observation(self):
        self.assertTrue(self.call()["ok"])
        self.assertEqual("stopped", self.call("stop")["status"])
        arguments = {"automation_id": "managed-1"}
        expected = self.session.handle("direct_status", arguments, flow.META)
        self.assertTrue(expected["ok"])
        before, effects = file_snapshot(self.root), list(self.effects)
        direct = self.session.sessions["managed-1"].direct
        # DirectProbe converts this internal exception to a failure response;
        # the outer project boundary must invalidate even without an exception.
        with mock.patch.object(direct, "_summary", side_effect=RepositoryError("state_unreadable")):
            result = self.session.handle("direct_status", arguments, flow.META)
        self.assertEqual("direct_probe_unavailable", result["error"])
        self.assert_diagnostic(result, stage="operation", reason="state_unreadable",
            local_state="not_attempted", host_mutation="not_dispatched",
            retained_effects="not_inspected", cleanup="not_attempted")
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual(effects, self.effects)
        self.assertEqual(expected, self.session.handle("direct_status", arguments, flow.META))
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual(effects, self.effects)

    def test_cleanup_success_then_lease_exit_failure_invalidates_settled_observation(self):
        self.assertTrue(self.call()["ok"])
        original = flow.RequestRepository.serial
        exit_checks, after_cleanup = [], []
        @contextmanager
        def fail_final_check(repository, **kwargs):
            with ExitStack() as patches:
                with original(repository, **kwargs) as lease:
                    yield lease
                    self.assertEqual("closed", repository.read()[1].phase)
                    after_cleanup.append(self.operational_snapshot())
                    # Inject only the real serial context's final check, after
                    # its complete cleanup body and confirmed host deletion.
                    exit_checks.append(patches.enter_context(mock.patch.object(
                        lease, "_check", side_effect=RepositoryError("state_unreadable"))))
        with mock.patch.object(flow.RequestRepository, "serial", new=fail_final_check):
            result = self.call("stop")
        self.assertEqual(1, len(exit_checks))
        exit_checks[0].assert_called_once()
        self.assert_diagnostic(result, stage="cleanup", reason="state_unreadable",
            local_state="may_have_changed", host_mutation="confirmed",
            retained_effects="not_inspected", cleanup="confirmed", recovery="inspect_existing")
        self.assertEqual(after_cleanup[0], self.operational_snapshot())
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)
        self.assertEqual("closed", self.inspect()["phase"])
        self.assertEqual("stopped", self.call("stop")["status"])
        self.assertEqual(["create", "arm", "pause", "delete"], self.effects)

    def closed_wait_in_new_turn(self):
        self.assertTrue(self.call()["ok"])
        self.assertEqual("stopped", self.call("stop")["status"])
        self.assertEqual("closed", self.inspect()["phase"])
        self.current_parent = replace(self.current_parent, turn_id=flow.NEW_TURN)
        return {"threadId": flow.PARENT, "turnId": flow.NEW_TURN}

    def test_closed_wait_body_failure_with_successful_release_preserves_observation(self):
        metadata = self.closed_wait_in_new_turn()
        before, effects = self.operational_snapshot(), list(self.effects)
        body_error = ServiceError("invalid_configuration")
        with mock.patch.object(managed_wait, "ten_minute_appointment", side_effect=body_error) as appointment:
            result = self.call(metadata=metadata)
        appointment.assert_called_once()
        self.assert_diagnostic(result, stage="host_context", reason="invalid_configuration",
            local_state="may_have_changed", host_mutation="not_dispatched",
            retained_effects="settled", cleanup="confirmed")
        self.assertEqual(before, self.operational_snapshot())
        self.assertEqual(effects, self.effects)
        self.assertEqual("closed", self.inspect()["phase"])

    def test_closed_wait_body_and_actual_lease_release_failures_invalidate_observation(self):
        metadata = self.closed_wait_in_new_turn()
        before, effects = self.operational_snapshot(), list(self.effects)
        body_error = ServiceError("invalid_configuration")
        original_lock = review_wait_repository._lock
        releases = []
        def failed_release(descriptor, *, release=False):
            # Exercise the actual repository's finally/error conversion. The
            # fixture unlocks first so the injected failure leaves no OS lock.
            original_lock(descriptor, release=release)
            if release:
                releases.append(descriptor)
                raise OSError(PRIVATE)
        with mock.patch.object(managed_wait, "ten_minute_appointment", side_effect=body_error) as appointment, \
                mock.patch.object(review_wait_repository, "_lock", new=failed_release):
            result = self.call(metadata=metadata)
        appointment.assert_called_once()
        self.assertEqual(1, len(releases))
        self.assert_diagnostic(result, stage="host_context", reason="state_unreadable",
            local_state="may_have_changed", host_mutation="not_dispatched",
            retained_effects="not_inspected", cleanup="confirmed", recovery="inspect_existing")
        self.assertEqual(before, self.operational_snapshot())
        self.assertEqual(effects, self.effects)
        self.assertEqual("closed", self.inspect()["phase"])
        self.assertEqual("stopped", self.call("stop")["status"])
        self.assertEqual(effects, self.effects)

    def test_closed_wait_lease_admission_failure_invalidates_preflight_observation(self):
        metadata = self.closed_wait_in_new_turn()
        before, effects = self.operational_snapshot(), list(self.effects)
        with mock.patch.object(review_wait_repository, "_lock", side_effect=OSError(PRIVATE)) as admission, \
                mock.patch.object(managed_wait, "ten_minute_appointment") as appointment:
            result = self.call(metadata=metadata)
        admission.assert_called_once()
        appointment.assert_not_called()
        self.assert_diagnostic(result, stage="state_create", reason="writer_busy",
            local_state="may_have_changed", host_mutation="not_dispatched",
            retained_effects="not_inspected", cleanup="not_attempted", recovery="inspect_existing")
        self.assertEqual(before, self.operational_snapshot())
        self.assertEqual(effects, self.effects)
        self.assertEqual("closed", self.inspect()["phase"])

    def test_authoritative_reread_failure_invalidates_preflight_absence(self):
        self.session._ensure_root(self.paths)
        path = self.paths.review_wait_request_store(flow.PARENT, flow.TASK)
        repository = flow.RequestRepository(path)
        with repository.serial(create=True):
            pass
        original = flow.RequestRepository.read
        reads = []
        def read(current):
            reads.append(current.path)
            if len(reads) == 2:
                raise RepositoryError("state_unreadable")
            value = original(current)
            self.assertEqual((0, None), value)
            return value
        with mock.patch.object(flow.RequestRepository, "read", new=read):
            result = self.call()
        self.assertEqual([path, path], reads)
        self.assert_diagnostic(result, stage="request_read", reason="state_unreadable",
            local_state="may_have_changed", host_mutation="not_dispatched", retained_effects="not_inspected")
        self.assertEqual((0, None), repository.read())
        self.assertEqual([], self.effects)

    def append_commits_then_fails(self, *, after_closed):
        if after_closed:
            self.assertTrue(self.call()["ok"])
            self.assertEqual("stopped", self.call("stop")["status"])
            self.assertEqual("closed", self.inspect()["phase"])
            self.current_parent = replace(self.current_parent, turn_id=flow.NEW_TURN)
        metadata = {"threadId": flow.PARENT, "turnId": self.current_parent.turn_id}
        effects = list(self.effects)
        original = flow.RequestRepository.append
        appended = []
        def append(repository, lease, record):
            original(repository, lease, record)
            appended.append(record)
            raise RepositoryError("state_unreadable")
        with mock.patch.object(flow.RequestRepository, "append", new=append):
            result = self.call(metadata=metadata)
        self.assertEqual(1, len(appended))
        diagnostic = self.assert_diagnostic(result, stage="request_write", reason="state_unreadable",
            local_state="may_have_changed", host_mutation="not_dispatched", recovery="inspect_existing")
        self.assertIn(diagnostic["retained_effects"], {"not_inspected", "present_unresolved"})
        path = self.paths.review_wait_request_store(flow.PARENT, flow.TASK)
        before = file_snapshot(self.root)
        sequence, record = flow.RequestRepository(path).read()
        self.assertEqual(2 if after_closed else 1, sequence)
        self.assertEqual("creating", record.phase)
        self.assertIsNone(record.automation_id)
        self.assertEqual("creating", self.inspect()["phase"])
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual(effects, self.effects)
        self.assertFalse(self.call("stop")["ok"])
        self.assertFalse(self.call(metadata=metadata)["ok"])
        self.assertEqual(effects, self.effects)

    def test_append_failure_after_commit_does_not_report_absence(self):
        self.append_commits_then_fails(after_closed=False)

    def test_append_failure_after_commit_does_not_reuse_previous_settled_evidence(self):
        self.append_commits_then_fails(after_closed=True)

    def test_runtime_diagnostic_failure_preserves_readable_inspection_and_saved_effects(self):
        self.fail_create = True
        self.assertFalse(self.call()["ok"])
        expected = self.inspect()
        self.assertEqual("unknown", expected["phase"])
        before = file_snapshot(self.root)
        identity = mock.Mock()
        identity.inspect.side_effect = PermissionError(PRIVATE)
        self.session.runtime_identity = identity
        result = self.inspect()
        self.assertEqual(expected, {key: value for key, value in result.items() if key != "runtime"})
        self.assertEqual({"version": 1, "code_id": None, "supported_schema": None,
            "deployed_supported_schema": None, "comparison": "unknown"}, result["runtime"])
        identity.inspect.assert_called_once()
        self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(result))
        self.assertNotIn("secret=value", json.dumps(result))
        self.assertNotIn("diagnostic", result)
        self.assertEqual(before, file_snapshot(self.root))
        self.assertEqual(["create"], self.effects)

    def test_early_failure_does_not_certify_preexisting_effect_absence(self):
        self.fail_create = True
        self.call()
        self.enabled = False
        result = self.call()
        self.assert_diagnostic(result, stage="policy", reason="review_wait_not_enabled",
            local_state="not_attempted", host_mutation="not_dispatched", retained_effects="not_inspected")
        self.assertEqual("unknown", self.inspect()["phase"])
        self.assertEqual(["create"], self.effects)

    def test_success_and_duplicate_readiness_keep_existing_shape(self):
        expected = {"ok": True, "status": "waiting", "task_id": flow.TASK,
                    "parent_may_end": True, "replayed": False}
        self.assertEqual(expected, self.call())
        self.assertEqual({**expected, "replayed": True}, self.call())
        self.assertEqual(["create", "arm"], self.effects)
