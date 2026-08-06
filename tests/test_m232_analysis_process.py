from __future__ import annotations

import gc
import hashlib
import inspect
import os
import sys
import tempfile
import unittest
import weakref
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _analysis_windows_process as process_boundary  # noqa: E402
from task_governance_tool import _analysis_win32 as win32_boundary  # noqa: E402
from task_governance_tool._analysis_windows_process import (  # noqa: E402
    ATTEMPT_BUDGET_MS,
    BROKER_PRIVILEGES,
    MockBinding,
    MockScenario,
    NativeProcessBoundary,
    ProcessQuarantineRequired,
    ProcessSafetyError,
    abort_prepared_mock_attempt,
    discard_aborted_attempt_root,
    discard_attempt_tree,
    execute_prepared_mock_attempt,
    mark_prepared_mock_attempt_recorded,
    native_capability_preflight,
    prepare_closed_mock_attempt,
)


def _binding() -> MockBinding:
    return MockBinding(
        analysis_job_id="tg_analysis_job_0123456789abcdef",
        source_key="sha256:" + "1" * 64,
        recipe_digest="sha256:" + "2" * 64,
        packet_digest="sha256:" + "3" * 64,
    )


def _environment() -> tuple[tuple[str, str], ...]:
    return (
        ("CODEX_HOME", r"C:\private\codex"),
        ("PATH", r"C:\runtime"),
        ("PATHEXT", ".EXE"),
        ("SystemRoot", r"C:\Windows"),
        ("TEMP", r"C:\private\temp"),
        ("TMP", r"C:\private\temp"),
    )


def _prepare(
    scenario: MockScenario,
    *,
    attempt_number: int = 1,
    prior_discard=None,
):
    binding = _binding()
    root_capability = process_boundary._synthetic_attempt_root_capability_for_tests(
        binding=binding,
        attempt_number=attempt_number,
    )
    return prepare_closed_mock_attempt(
        attempt_number,
        scenario,
        binding=binding,
        stdin_bytes=b"fixed-input-frame",
        argv=(r"C:\runtime\adapter.exe", "--offline"),
        environment=_environment(),
        root_capability=root_capability,
        output_schema_bytes=b"{}",
        prior_discard=prior_discard,
    )


def _run(
    scenario: MockScenario,
    *,
    attempt_number: int = 1,
    prior_discard=None,
):
    prepared = _prepare(
        scenario,
        attempt_number=attempt_number,
        prior_discard=prior_discard,
    )
    mark_prepared_mock_attempt_recorded(prepared)
    return execute_prepared_mock_attempt(prepared)


def _make_physical_root(
    parent_path: Path,
    *,
    binding: MockBinding,
    attempt_number: int,
    owner_token: object,
    basename: str,
):
    parent = win32_boundary.open_no_follow(
        parent_path,
        win32_boundary.R0,
        expect_directory=True,
        kind="test-attempt-parent",
    )
    try:
        capability = process_boundary.create_physical_attempt_root_capability(
            root_parent=parent,
            root_basename=basename,
            analysis_job_id=binding.analysis_job_id,
            attempt_number=attempt_number,
            packet_digest=binding.packet_digest,
            owner_token=owner_token,
        )
    except BaseException:
        if not parent.closed:
            parent.close()
        raise
    root = capability._root_handle
    return capability, root, parent


def _prepare_physical(
    scenario: MockScenario,
    *,
    capability,
    binding: MockBinding,
    attempt_number: int,
    prior_discard=None,
):
    return prepare_closed_mock_attempt(
        attempt_number,
        scenario,
        binding=binding,
        stdin_bytes=b"fixed-input-frame",
        argv=(r"C:\runtime\adapter.exe", "--offline"),
        environment=_environment(),
        root_capability=capability,
        output_schema_bytes=b"{}",
        prior_discard=prior_discard,
    )


class AnalysisProcessTests(unittest.TestCase):
    def test_native_preflight_is_always_pre_count_policy_blocked(self):
        observed: list[str] = []

        def reader() -> frozenset[str]:
            observed.append("read")
            return BROKER_PRIVILEGES | frozenset({"SeDebugPrivilege"})

        preflight = native_capability_preflight(reader)
        self.assertFalse(preflight.ready)
        self.assertEqual(preflight.inference_state, "policy_blocked")
        self.assertEqual(preflight.adapter_attempt_count, 0)
        self.assertEqual(observed, ["read"])

        observed.clear()
        with self.assertRaises(ProcessSafetyError) as raised:
            NativeProcessBoundary(reader).execute_attempt(object())
        self.assertEqual(raised.exception.code, "policy_blocked")
        self.assertEqual(observed, ["read"])

    def test_two_phase_record_marker_is_required_once_and_execution_is_single_use(self):
        prepared = _prepare(MockScenario.SUCCESS)
        machine = prepared._machine
        attempt_ref = weakref.ref(machine.attempt)
        self.assertEqual(
            prepared.trace,
            (
                "n1:fresh_objects",
                "n1:ownership_proved",
                "n1:schema_flushed",
            ),
        )
        self.assertNotIn("fixed-input-frame", repr(prepared))
        self.assertNotIn("stdin_bytes", repr(prepared))

        with self.assertRaises(ProcessSafetyError) as unrecorded:
            execute_prepared_mock_attempt(prepared)
        self.assertEqual(unrecorded.exception.code, "analysis_mock_execute_invalid")
        self.assertNotIn("n1:broker_launch_proved", prepared.trace)

        mark_prepared_mock_attempt_recorded(prepared)
        self.assertEqual(prepared.trace[-1], "n1:attempt_recorded")
        with self.assertRaises(ProcessSafetyError) as duplicate_marker:
            mark_prepared_mock_attempt_recorded(prepared)
        self.assertEqual(
            duplicate_marker.exception.code,
            "analysis_mock_record_marker_invalid",
        )

        result = execute_prepared_mock_attempt(prepared)
        self.assertIsNone(prepared._machine)
        self.assertIsNone(prepared._scenario)
        self.assertIsNone(machine.attempt)
        gc.collect()
        self.assertIsNone(attempt_ref())
        self.assertLess(
            result.trace.index("n1:schema_flushed"),
            result.trace.index("n1:attempt_recorded"),
        )
        self.assertLess(
            result.trace.index("n1:attempt_recorded"),
            result.trace.index("n1:broker_launch_proved"),
        )
        with self.assertRaises(ProcessSafetyError) as duplicate_execute:
            execute_prepared_mock_attempt(prepared)
        self.assertEqual(
            duplicate_execute.exception.code,
            "analysis_mock_execute_invalid",
        )
        discard_attempt_tree(result.outcome.tree_proof)

    def test_prepared_abort_is_single_use_releases_input_and_never_launches(self):
        prepared = _prepare(MockScenario.SUCCESS)
        machine = prepared._machine
        attempt_ref = weakref.ref(machine.attempt)

        proof = abort_prepared_mock_attempt(prepared)

        self.assertIsNone(prepared._machine)
        self.assertIsNone(prepared._scenario)
        self.assertIsNone(machine.attempt)
        gc.collect()
        self.assertIsNone(attempt_ref())
        self.assertEqual(
            proof.trace[-3:],
            (
                "n1:prepared_abort_latched",
                "n1:s_o_absent",
                "n1:prepared_abort_complete",
            ),
        )
        self.assertNotIn("n1:attempt_recorded", proof.trace)
        self.assertNotIn("n1:broker_launch_proved", proof.trace)
        discard_aborted_attempt_root(proof)
        with self.assertRaises(ProcessSafetyError):
            discard_aborted_attempt_root(proof)
        with self.assertRaises(ProcessSafetyError):
            abort_prepared_mock_attempt(prepared)

    def test_abort_cleanup_quarantine_permanently_consumes_prepared_facade(self):
        prepared = _prepare(MockScenario.SUCCESS)
        machine = prepared._machine
        failure = ProcessQuarantineRequired((), frozenset({"n1:root"}))
        with (
            patch.object(machine, "abort", side_effect=failure),
            self.assertRaises(ProcessQuarantineRequired),
        ):
            abort_prepared_mock_attempt(prepared)

        self.assertEqual(prepared._state, "quarantine")
        self.assertIs(prepared._machine, machine)
        for operation in (
            lambda: mark_prepared_mock_attempt_recorded(prepared),
            lambda: abort_prepared_mock_attempt(prepared),
            lambda: execute_prepared_mock_attempt(prepared),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ProcessSafetyError):
                    operation()

    def test_normal_rejection_releases_attempt_input_and_keeps_safe_trace(self):
        prepared = _prepare(MockScenario.TIMEOUT)
        machine = prepared._machine
        mark_prepared_mock_attempt_recorded(prepared)
        result = execute_prepared_mock_attempt(prepared)
        self.assertEqual(result.outcome.inference_state, "timeout")
        self.assertIsNone(prepared._machine)
        self.assertIsNone(prepared._scenario)
        self.assertIsNone(machine.attempt)
        self.assertEqual(prepared.trace, result.trace)
        self.assertFalse(hasattr(result, "stdin_bytes"))
        self.assertFalse(hasattr(result.outcome.tree_proof, "stdin_bytes"))
        discard_attempt_tree(result.outcome.tree_proof)

    def test_success_has_exact_order_and_only_bound_empty_claims_output(self):
        result = _run(MockScenario.SUCCESS)
        expected = (
            "n1:fresh_objects",
            "n1:ownership_proved",
            "n1:schema_flushed",
            "n1:attempt_recorded",
            "n1:broker_launch_proved",
            "n1:broker_resumed_once",
            "n1:broker_primary_thread_closed",
            "n1:parent_duplicates_closed",
            "n1:broker_token_proved",
            "n1:private_desktop_proved",
            "n1:target_token_transferred_to_broker",
            "n1:controller_target_token_copy_closed",
            "n1:target_launch_proved",
            "n1:broker_target_creation_token_closed",
            "n1:target_resumed_once",
            "n1:target_signaled",
            "n1:stdin_source_closed",
            "n1:stdin_unused_pipe_end_closed",
            "n1:stdin_joined",
            "n1:stdout_unused_pipe_end_closed",
            "n1:stdout_eof",
            "n1:stdout_joined",
            "n1:stderr_unused_pipe_end_closed",
            "n1:stderr_eof",
            "n1:stderr_joined",
            "n1:worker_count_3",
            "n1:worker_join_duration_bounded",
            "n1:target_handles_closed",
            "n1:job_b_only_1",
            "n1:job_b_only_2",
            "n1:output_held_before",
            "n1:output_read_capped",
            "n1:output_held_after",
            "n1:q_sealed",
            "n1:s_o_absent",
            "n1:broker_terminal",
            "n1:broker_exited",
            "n1:job_zero_1",
            "n1:job_zero_2",
            "n1:q_reread",
            "n1:binding_valid",
            "n1:attempt_handles_closed",
            "n1:job_closed",
            "n1:root_retained",
            "n1:tree_quiescent",
        )
        self.assertEqual(result.trace, expected)
        outcome = result.outcome
        self.assertEqual(outcome.inference_state, "succeeded")
        self.assertEqual(outcome.worker_join_duration_ms, 17)
        self.assertIsNotNone(outcome.sealed_result)
        self.assertEqual(
            outcome.tree_proof.absent_private_leaves,
            frozenset({"n1:output_schema", "n1:output"}),
        )
        self.assertEqual(outcome.tree_proof.retained_objects, {"n1:root"})
        document = outcome.sealed_result.document
        self.assertEqual(
            document,
            (
                b'{"analysis_job_id":"tg_analysis_job_0123456789abcdef",'
                b'"claims":[],"output_schema_version":1,"recipe_digest":"sha256:'
                + b"2" * 64
                + b'","source_key":"sha256:'
                + b"1" * 64
                + b'"}'
            ),
        )
        self.assertNotIn("document=", repr(outcome.sealed_result))
        discarded = discard_attempt_tree(outcome.tree_proof)
        self.assertEqual(discarded.trace[-1], "n1:root_absent")
        self.assertIn("n1:root", discarded.absent_objects)
        with self.assertRaises(ProcessSafetyError) as reused:
            discard_attempt_tree(outcome.tree_proof)
        self.assertEqual(reused.exception.code, "analysis_mock_tree_proof_consumed")

    def test_abnormal_paths_latch_before_termination_and_never_read_q(self):
        cases = (
            (MockScenario.TIMEOUT, "timeout", "timeout", ATTEMPT_BUDGET_MS),
            (MockScenario.CANCEL, "cancel", "cancelled", 10),
            (MockScenario.BROKER_CRASH, "broker_crash", "failed", 20),
            (MockScenario.PARTIAL_RESULT, "partial_q", "failed", 20),
            (MockScenario.WORKER_HANG, "worker_hang", "failed", 5_000),
        )
        for scenario, reason, state, duration in cases:
            with self.subTest(scenario=scenario.value):
                result = _run(scenario)
                trace = result.trace
                latch = trace.index(f"n1:outcome_latched:{reason}")
                self.assertEqual(
                    trace[latch:],
                    (
                        f"n1:outcome_latched:{reason}",
                        "n1:terminate_job",
                        "n1:broker_signaled",
                        "n1:job_zero_1",
                        "n1:job_zero_2",
                        "n1:q_unread",
                        "n1:s_o_absent",
                        "n1:attempt_handles_closed",
                        "n1:job_closed",
                        "n1:root_retained",
                        "n1:tree_quiescent",
                    ),
                )
                self.assertNotIn("n1:q_reread", trace)
                self.assertIn("n1:q_unread", trace)
                outcome = result.outcome
                self.assertEqual(outcome.inference_state, state)
                self.assertEqual(outcome.duration_ms, duration)
                self.assertIsNone(outcome.sealed_result)
                self.assertEqual(
                    outcome.tree_proof.retained_objects,
                    frozenset({"n1:root"}),
                )
                discarded = discard_attempt_tree(outcome.tree_proof)
                self.assertEqual(discarded.trace[-1], "n1:root_absent")

    def test_wrong_binding_and_post_read_change_use_no_result_bytes(self):
        for scenario in (
            MockScenario.WRONG_BINDING,
            MockScenario.POST_READ_CHANGED,
        ):
            with self.subTest(scenario=scenario.value):
                result = _run(scenario)
                outcome = result.outcome
                self.assertEqual(outcome.inference_state, "invalid_output")
                self.assertIsNone(outcome.sealed_result)
                self.assertIn("n1:q_reread", result.trace)
                self.assertIn("n1:binding_invalid", result.trace)
                self.assertNotIn("n1:binding_valid", result.trace)
                discard_attempt_tree(outcome.tree_proof)

    def test_candidate_validator_rejects_each_binding_and_identity_fault(self):
        binding = _binding()
        attempt = process_boundary._attempt(
            binding=binding,
            number=1,
            stdin_bytes=b"fixed-input-frame",
            argv=(r"C:\runtime\adapter.exe", "--offline"),
            environment=_environment(),
            cancel_requested=False,
        )
        valid = process_boundary._candidate_for_attempt(
            attempt=attempt,
            binding=binding,
            fault=process_boundary._CandidateFault.NONE,
        )
        self.assertIsNotNone(
            process_boundary._validate_result_candidate(
                valid,
                attempt=attempt,
                binding=binding,
            )
        )
        invalid = (
            replace(valid, attempt_number=2),
            replace(valid, packet_digest="sha256:" + "4" * 64),
            replace(valid, length=valid.length + 1),
            replace(valid, digest="sha256:" + "5" * 64),
            replace(valid, post_length=valid.post_length + 1),
            replace(valid, post_digest="sha256:" + "6" * 64),
            replace(valid, identity_after=valid.identity_before + ":changed"),
        )
        changed_document = b"Y" + valid.post_document[1:]
        invalid += (
            replace(
                valid,
                post_document=changed_document,
                post_digest=(
                    "sha256:" + hashlib.sha256(changed_document).hexdigest()
                ),
            ),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                self.assertIsNone(
                    process_boundary._validate_result_candidate(
                        candidate,
                        attempt=attempt,
                        binding=binding,
                    )
                )
        different_binding = MockBinding(
            analysis_job_id="tg_analysis_job_fedcba9876543210",
            source_key=binding.source_key,
            recipe_digest=binding.recipe_digest,
            packet_digest="sha256:" + "7" * 64,
        )
        self.assertIsNone(
            process_boundary._validate_result_candidate(
                valid,
                attempt=attempt,
                binding=different_binding,
            )
        )

    def test_worker_can_retry_with_disjoint_attempt_objects_without_lease_action(self):
        for first_scenario, first_state in (
            (MockScenario.TIMEOUT, "timeout"),
            (MockScenario.WRONG_BINDING, "invalid_output"),
        ):
            with self.subTest(scenario=first_scenario.value):
                first = _run(first_scenario, attempt_number=1)
                discarded = discard_attempt_tree(first.outcome.tree_proof)
                second = _run(
                    MockScenario.SUCCESS,
                    attempt_number=2,
                    prior_discard=discarded,
                )
                self.assertEqual(
                    (first.outcome.inference_state, second.outcome.inference_state),
                    (first_state, "succeeded"),
                )
                self.assertFalse(first.attempt_identities & second.attempt_identities)
                combined = first.trace + second.trace
                self.assertTrue(all("lease" not in event for event in combined))
                self.assertEqual(first.trace[0], "n1:fresh_objects")
                self.assertEqual(second.trace[0], "n2:fresh_objects")
                self.assertEqual(discarded.trace[-1], "n1:root_absent")
                discard_attempt_tree(second.outcome.tree_proof)

    def test_retry_requires_one_matching_single_use_discard_proof(self):
        with self.assertRaises(ProcessSafetyError) as missing:
            _prepare(MockScenario.SUCCESS, attempt_number=2)
        self.assertEqual(missing.exception.code, "analysis_mock_retry_proof_invalid")

        first = _run(MockScenario.TIMEOUT)
        discarded = discard_attempt_tree(first.outcome.tree_proof)
        second = _prepare(
            MockScenario.SUCCESS,
            attempt_number=2,
            prior_discard=discarded,
        )
        with self.assertRaises(ProcessSafetyError) as reused:
            _prepare(
                MockScenario.SUCCESS,
                attempt_number=2,
                prior_discard=discarded,
            )
        self.assertEqual(reused.exception.code, "analysis_mock_retry_proof_invalid")
        mark_prepared_mock_attempt_recorded(second)
        result = execute_prepared_mock_attempt(second)
        discard_attempt_tree(result.outcome.tree_proof)

    def test_post_launch_ownership_is_exactly_nonoverlapping(self):
        result = _run(MockScenario.SUCCESS)
        snapshot = result.ownership
        self.assertEqual(snapshot.phase, "post_target_launch")
        self.assertEqual(
            snapshot.controller,
            frozenset(
                {
                    "n1:job",
                    "n1:broker_process",
                    "n1:input_mapping",
                    "n1:result_mapping",
                    "n1:broker_event",
                    "n1:controller_event",
                }
            ),
        )
        self.assertEqual(
            snapshot.broker,
            frozenset(
                {
                    "n1:root",
                    "n1:station",
                    "n1:desktop",
                    "n1:target_process",
                    "n1:target_thread",
                    "n1:stdin_worker_handle",
                    "n1:stdout_worker_handle",
                    "n1:stderr_worker_handle",
                    "n1:output_schema",
                    "n1:output",
                    "n1:result_mapping:write",
                }
            ),
        )
        self.assertEqual(
            snapshot.target,
            frozenset(
                {
                    "n1:stdin:read",
                    "n1:stdout:write",
                    "n1:stderr:write",
                }
            ),
        )
        self.assertEqual(
            snapshot.workers,
            (
                (
                    "stdin",
                    frozenset({"n1:stdin_pipe:write", "n1:stdin_worker"}),
                ),
                (
                    "stdout",
                    frozenset({"n1:stdout_pipe:read", "n1:stdout_worker"}),
                ),
                (
                    "stderr",
                    frozenset({"n1:stderr_pipe:read", "n1:stderr_worker"}),
                ),
            ),
        )
        owned = (
            snapshot.controller,
            snapshot.broker,
            snapshot.target,
            *(items for _, items in snapshot.workers),
        )
        for index, current in enumerate(owned):
            for other in owned[index + 1 :]:
                self.assertFalse(current & other)
        union = frozenset().union(*owned)
        self.assertEqual(union, result.attempt_identities & union)
        self.assertNotIn("n1:target_token_duplicate", union)
        self.assertIn("n1:target_token_transferred_to_broker", result.trace)
        self.assertIn("n1:broker_target_creation_token_closed", result.trace)
        discard_attempt_tree(result.outcome.tree_proof)

    def test_unproved_tree_zero_quarantines_without_cleanup_or_unlock(self):
        prepared = _prepare(MockScenario.TREE_ZERO_UNPROVED)
        machine = prepared._machine
        mark_prepared_mock_attempt_recorded(prepared)
        with self.assertRaises(ProcessQuarantineRequired) as raised:
            execute_prepared_mock_attempt(prepared)
        failure = raised.exception
        self.assertIs(prepared._machine, machine)
        self.assertIsNotNone(machine.attempt)
        self.assertEqual(prepared._scenario, MockScenario.TREE_ZERO_UNPROVED)
        self.assertEqual(
            failure.trace[-3:],
            (
                "n1:job_zero_1",
                "n1:job_zero_unproved",
                "n1:quarantine_fail_fast",
            ),
        )
        for forbidden in (
            "n1:q_unread",
            "n1:root_absent",
            "n1:attempt_handles_closed",
            "n1:job_closed",
            "n1:tree_quiescent",
            "lease_retained",
            "lease_unlock",
            "lease_close",
        ):
            self.assertNotIn(forbidden, failure.trace)
        self.assertIn("n1:job", failure.retained_objects)
        self.assertIn("n1:root", failure.retained_objects)

    def test_mock_entry_has_closed_scenarios_and_no_output_or_trace_input(self):
        self.assertEqual(
            {item.value for item in MockScenario},
            {
                "success",
                "timeout",
                "cancel",
                "broker_crash",
                "worker_hang",
                "partial_result",
                "wrong_binding",
                "post_read_changed",
                "tree_zero_unproved",
            },
        )
        self.assertEqual(
            tuple(inspect.signature(prepare_closed_mock_attempt).parameters),
            (
                "attempt_number",
                "scenario",
                "binding",
                "stdin_bytes",
                "argv",
                "environment",
                "root_capability",
                "output_schema_bytes",
                "prior_discard",
            ),
        )
        self.assertEqual(
            tuple(
                inspect.signature(mark_prepared_mock_attempt_recorded).parameters
            ),
            ("prepared",),
        )
        self.assertEqual(
            tuple(inspect.signature(execute_prepared_mock_attempt).parameters),
            ("prepared",),
        )
        self.assertEqual(
            tuple(inspect.signature(discard_attempt_tree).parameters),
            ("proof", "root_owner_token"),
        )
        self.assertFalse(hasattr(process_boundary, "run_closed_mock_attempt"))
        self.assertNotIn(
            "adapter_attempt_count",
            process_boundary.MockAttemptResult.__dataclass_fields__,
        )
        with self.assertRaises(ProcessSafetyError) as raised:
            binding = _binding()
            prepare_closed_mock_attempt(
                1,
                MockScenario.SUCCESS,
                binding=binding,
                stdin_bytes=b"fixed-input-frame",
                argv=(r"C:\runtime\adapter.exe",),
                environment=((),),
                root_capability=(
                    process_boundary._synthetic_attempt_root_capability_for_tests(
                        binding=binding,
                        attempt_number=1,
                    )
                ),
                output_schema_bytes=b"{}",
            )
        self.assertEqual(raised.exception.code, "analysis_attempt_input_invalid")


@unittest.skipUnless(os.name == "nt", "physical attempt roots are Windows-only")
class PhysicalAttemptRootTests(unittest.TestCase):
    def test_physical_factory_rejects_noncanonical_name_before_create(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = win32_boundary.open_no_follow(
                Path(temporary),
                win32_boundary.R0,
                expect_directory=True,
                kind="test-attempt-parent",
            )
            try:
                with (
                    patch.object(
                        process_boundary._win32,
                        "create_relative_directory",
                    ) as create,
                    self.assertRaises(ProcessSafetyError) as raised,
                ):
                    process_boundary.create_physical_attempt_root_capability(
                        root_parent=parent,
                        root_basename="arbitrary-root",
                        analysis_job_id=_binding().analysis_job_id,
                        attempt_number=1,
                        packet_digest=_binding().packet_digest,
                        owner_token=object(),
                    )
                self.assertEqual(
                    raised.exception.code,
                    "analysis_attempt_root_invalid",
                )
                create.assert_not_called()
                self.assertNotIn(
                    "root_handle",
                    inspect.signature(
                        process_boundary.create_physical_attempt_root_capability
                    ).parameters,
                )
            finally:
                parent.close()

    def test_physical_factory_collision_preserves_preexisting_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent_path = Path(temporary)
            basename = ".taskgov-analysis-0123abcd"
            existing = parent_path / basename
            existing.mkdir()
            sentinel = existing / "foreign.txt"
            sentinel.write_bytes(b"foreign")
            parent = win32_boundary.open_no_follow(
                parent_path,
                win32_boundary.R0,
                expect_directory=True,
                kind="test-attempt-parent",
            )
            try:
                with self.assertRaises(ProcessSafetyError) as raised:
                    process_boundary.create_physical_attempt_root_capability(
                        root_parent=parent,
                        root_basename=basename,
                        analysis_job_id=_binding().analysis_job_id,
                        attempt_number=1,
                        packet_digest=_binding().packet_digest,
                        owner_token=object(),
                    )
                self.assertEqual(
                    raised.exception.code,
                    "analysis_attempt_root_create_failed",
                )
            finally:
                parent.close()
            self.assertTrue(existing.is_dir())
            self.assertEqual(sentinel.read_bytes(), b"foreign")

    def test_physical_factory_post_create_uncertainty_retains_quarantine_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = win32_boundary.open_no_follow(
                Path(temporary),
                win32_boundary.R0,
                expect_directory=True,
                kind="test-attempt-parent",
            )
            basename = ".taskgov-analysis-1234abcd"
            capability = None
            try:
                with (
                    patch.object(
                        process_boundary._win32,
                        "enumerate_held_directory",
                        side_effect=win32_boundary.Win32BoundaryError(
                            "injected_inventory_failure"
                        ),
                    ),
                    self.assertRaises(ProcessQuarantineRequired) as raised,
                ):
                    process_boundary.create_physical_attempt_root_capability(
                        root_parent=parent,
                        root_basename=basename,
                        analysis_job_id=_binding().analysis_job_id,
                        attempt_number=1,
                        packet_digest=_binding().packet_digest,
                        owner_token=object(),
                    )
                capability = raised.exception.root_capability
                self.assertIsNotNone(capability)
                self.assertEqual(capability.state, "quarantine")
                self.assertFalse(capability._root_handle.closed)
                self.assertTrue((Path(temporary) / basename).is_dir())
                win32_boundary.remove_relative_directory(
                    capability._root_handle,
                    parent,
                    basename,
                )
                capability = None
            finally:
                if capability is not None and not capability._root_handle.closed:
                    win32_boundary.remove_relative_directory(
                        capability._root_handle,
                        parent,
                        basename,
                    )
                parent.close()

    def test_physical_s_o_order_same_root_and_single_publication_consume(self):
        with tempfile.TemporaryDirectory() as temporary:
            binding = _binding()
            owner = object()
            capability, root, parent = _make_physical_root(
                Path(temporary),
                binding=binding,
                attempt_number=1,
                owner_token=owner,
                basename=".taskgov-analysis-a1b2c3d4",
            )
            try:
                prepared = _prepare_physical(
                    MockScenario.SUCCESS,
                    capability=capability,
                    binding=binding,
                    attempt_number=1,
                )
                self.assertEqual(
                    [
                        item.name
                        for item in win32_boundary.enumerate_held_directory(
                            root,
                            maximum_entries=2,
                        )
                    ],
                    ["output-schema.json", "output.json"],
                )
                mark_prepared_mock_attempt_recorded(prepared)
                result = execute_prepared_mock_attempt(prepared)
                proof = result.outcome.tree_proof
                self.assertTrue(proof.is_physical)
                self.assertEqual(capability.state, "quiescent")
                self.assertEqual(
                    win32_boundary.enumerate_held_directory(
                        root,
                        maximum_entries=0,
                    ),
                    (),
                )
                with self.assertRaises(ProcessSafetyError):
                    process_boundary.consume_attempt_tree_for_publication(
                        proof,
                        root_capability=capability,
                        root_owner_token=object(),
                        binding=binding,
                    )
                ready = process_boundary.consume_attempt_tree_for_publication(
                    proof,
                    root_capability=capability,
                    root_owner_token=owner,
                    binding=binding,
                )
                self.assertEqual(ready.binding, binding)
                self.assertEqual(capability.state, "publication")
                with self.assertRaises(ProcessSafetyError):
                    process_boundary.borrow_publish_ready_attempt_root(
                        ready,
                        root_owner_token=object(),
                        binding=binding,
                        attempt_number=1,
                    )
                borrowed = process_boundary.borrow_publish_ready_attempt_root(
                    ready,
                    root_owner_token=owner,
                    binding=binding,
                    attempt_number=1,
                )
                self.assertIs(borrowed.root_handle, root)
                self.assertIs(borrowed.root_parent, parent)
                self.assertIs(borrowed.root_capability, capability)
                self.assertEqual(
                    borrowed.root_basename,
                    ".taskgov-analysis-a1b2c3d4",
                )
                with self.assertRaises(ProcessSafetyError):
                    process_boundary.borrow_publish_ready_attempt_root(
                        ready,
                        root_owner_token=owner,
                        binding=binding,
                        attempt_number=1,
                    )
                with self.assertRaises(ProcessSafetyError):
                    process_boundary.discard_attempt_tree(
                        proof,
                        root_owner_token=owner,
                    )
                with self.assertRaises(ProcessSafetyError):
                    process_boundary.remove_borrowed_publication_root(
                        borrowed,
                        root_owner_token=object(),
                        binding=binding,
                        attempt_number=1,
                    )
                self.assertEqual(capability.state, "publication_borrowed")
                self.assertFalse(root.closed)
                process_boundary.remove_borrowed_publication_root(
                    borrowed,
                    root_owner_token=owner,
                    binding=binding,
                    attempt_number=1,
                )
                self.assertEqual(capability.state, "removed")
                self.assertTrue(root.closed)
            finally:
                if not root.closed:
                    root.close()
                if not parent.closed:
                    parent.close()

    def test_physical_prepared_abort_is_no_launch_and_owner_cleaned_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            binding = _binding()
            owner = object()
            capability, root, parent = _make_physical_root(
                Path(temporary),
                binding=binding,
                attempt_number=1,
                owner_token=owner,
                basename=".taskgov-analysis-b1b2c3d4",
            )
            try:
                prepared = _prepare_physical(
                    MockScenario.SUCCESS,
                    capability=capability,
                    binding=binding,
                    attempt_number=1,
                )
                proof = abort_prepared_mock_attempt(prepared)
                self.assertTrue(proof.is_physical)
                self.assertEqual(capability.state, "aborted")
                self.assertNotIn("n1:attempt_recorded", proof.trace)
                self.assertNotIn("n1:broker_launch_proved", proof.trace)
                self.assertEqual(
                    win32_boundary.enumerate_held_directory(
                        root,
                        maximum_entries=0,
                    ),
                    (),
                )
                with self.assertRaises(ProcessSafetyError):
                    discard_aborted_attempt_root(
                        proof,
                        root_owner_token=object(),
                    )
                discard_aborted_attempt_root(
                    proof,
                    root_owner_token=owner,
                )
                self.assertTrue(root.closed)
                self.assertEqual(capability.state, "removed")
                with self.assertRaises(ProcessSafetyError):
                    discard_aborted_attempt_root(
                        proof,
                        root_owner_token=owner,
                    )
            finally:
                if not root.closed:
                    root.close()
                if not parent.closed:
                    parent.close()

    def test_physical_abort_leaf_close_uncertainty_is_irreversible_quarantine(self):
        with tempfile.TemporaryDirectory() as temporary:
            binding = _binding()
            owner = object()
            basename = ".taskgov-analysis-b2b2c3d4"
            capability, root, parent = _make_physical_root(
                Path(temporary),
                binding=binding,
                attempt_number=1,
                owner_token=owner,
                basename=basename,
            )
            try:
                prepared = _prepare_physical(
                    MockScenario.SUCCESS,
                    capability=capability,
                    binding=binding,
                    attempt_number=1,
                )
                original_close = win32_boundary.OwnedHandle.close
                schema = capability._schema_handle

                def fail_schema_close(candidate):
                    if candidate is schema:
                        raise win32_boundary.Win32BoundaryError(
                            "injected_schema_close_failure"
                        )
                    return original_close(candidate)

                with (
                    patch.object(
                        win32_boundary.OwnedHandle,
                        "close",
                        new=fail_schema_close,
                    ),
                    self.assertRaises(ProcessQuarantineRequired),
                ):
                    abort_prepared_mock_attempt(prepared)

                self.assertEqual(prepared._state, "quarantine")
                self.assertEqual(capability.state, "quarantine")
                self.assertFalse(schema.closed)
                for operation in (
                    lambda: mark_prepared_mock_attempt_recorded(prepared),
                    lambda: abort_prepared_mock_attempt(prepared),
                    lambda: execute_prepared_mock_attempt(prepared),
                ):
                    with self.subTest(operation=operation):
                        with self.assertRaises(ProcessSafetyError):
                            operation()

                for handle in (
                    capability._schema_handle,
                    capability._output_handle,
                ):
                    if handle is not None and not handle.closed:
                        handle.close()
                win32_boundary.remove_relative_directory(root, parent, basename)
            finally:
                for handle in (
                    capability._schema_handle,
                    capability._output_handle,
                ):
                    if handle is not None and not handle.closed:
                        handle.close()
                if not root.closed:
                    win32_boundary.remove_relative_directory(root, parent, basename)
                if not parent.closed:
                    parent.close()

    def test_physical_retry_requires_removed_n1_and_fresh_n2_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            binding = _binding()
            owner = object()
            parent_path = Path(temporary)
            first_cap, first_root, first_parent = _make_physical_root(
                parent_path,
                binding=binding,
                attempt_number=1,
                owner_token=owner,
                basename=".taskgov-analysis-c1b2c3d4",
            )
            rejected_root = rejected_parent = None
            second_root = second_parent = None
            try:
                first_prepared = _prepare_physical(
                    MockScenario.TIMEOUT,
                    capability=first_cap,
                    binding=binding,
                    attempt_number=1,
                )
                mark_prepared_mock_attempt_recorded(first_prepared)
                first = execute_prepared_mock_attempt(first_prepared)
                with self.assertRaises(ProcessSafetyError):
                    discard_attempt_tree(
                        first.outcome.tree_proof,
                        root_owner_token=object(),
                    )
                self.assertEqual(first_cap.state, "quiescent")
                self.assertFalse(first_root.closed)
                discarded = discard_attempt_tree(
                    first.outcome.tree_proof,
                    root_owner_token=owner,
                )
                self.assertTrue(first_root.closed)
                self.assertEqual(first_cap.state, "removed")
                first_parent.close()

                rejected_cap, rejected_root, rejected_parent = _make_physical_root(
                    parent_path,
                    binding=binding,
                    attempt_number=2,
                    owner_token=owner,
                    basename=".taskgov-analysis-c1b2c3d4",
                )
                with self.assertRaises(ProcessSafetyError):
                    _prepare_physical(
                        MockScenario.SUCCESS,
                        capability=rejected_cap,
                        binding=binding,
                        attempt_number=2,
                        prior_discard=discarded,
                    )
                self.assertEqual(rejected_cap.state, "bound")
                win32_boundary.remove_relative_directory(
                    rejected_root,
                    rejected_parent,
                    ".taskgov-analysis-c1b2c3d4",
                )
                rejected_parent.close()

                second_cap, second_root, second_parent = _make_physical_root(
                    parent_path,
                    binding=binding,
                    attempt_number=2,
                    owner_token=owner,
                    basename=".taskgov-analysis-d1b2c3d4",
                )
                second_prepared = _prepare_physical(
                    MockScenario.SUCCESS,
                    capability=second_cap,
                    binding=binding,
                    attempt_number=2,
                    prior_discard=discarded,
                )
                with self.assertRaises(ProcessSafetyError):
                    _prepare_physical(
                        MockScenario.SUCCESS,
                        capability=second_cap,
                        binding=binding,
                        attempt_number=2,
                        prior_discard=discarded,
                    )
                mark_prepared_mock_attempt_recorded(second_prepared)
                second = execute_prepared_mock_attempt(second_prepared)
                self.assertTrue(second.outcome.tree_proof.is_physical)
                discard_attempt_tree(
                    second.outcome.tree_proof,
                    root_owner_token=owner,
                )
                self.assertTrue(second_root.closed)
            finally:
                for handle in (
                    first_root,
                    first_parent,
                    rejected_root,
                    rejected_parent,
                    second_root,
                    second_parent,
                ):
                    if handle is not None and not handle.closed:
                        handle.close()

    def test_physical_wrong_session_and_binding_are_rejected_without_touching_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            binding = _binding()
            owner = object()
            capability, root, parent = _make_physical_root(
                Path(temporary),
                binding=binding,
                attempt_number=1,
                owner_token=owner,
                basename=".taskgov-analysis-e1b2c3d4",
            )
            try:
                with self.assertRaises(ProcessSafetyError):
                    process_boundary.prove_physical_attempt_root_capability(
                        capability,
                        owner_token=object(),
                        binding=binding,
                        attempt_number=1,
                    )
                wrong = MockBinding(
                    analysis_job_id="tg_analysis_job_fedcba9876543210",
                    source_key=binding.source_key,
                    recipe_digest=binding.recipe_digest,
                    packet_digest=binding.packet_digest,
                )
                with self.assertRaises(ProcessSafetyError):
                    _prepare_physical(
                        MockScenario.SUCCESS,
                        capability=capability,
                        binding=wrong,
                        attempt_number=1,
                    )
                self.assertEqual(capability.state, "bound")
                self.assertEqual(
                    win32_boundary.enumerate_held_directory(
                        root,
                        maximum_entries=0,
                    ),
                    (),
                )
                win32_boundary.remove_relative_directory(
                    root,
                    parent,
                    ".taskgov-analysis-e1b2c3d4",
                )
            finally:
                if not root.closed:
                    root.close()
                if not parent.closed:
                    parent.close()

    def test_physical_absence_uncertainty_quarantines_and_retains_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            binding = _binding()
            owner = object()
            capability, root, parent = _make_physical_root(
                Path(temporary),
                binding=binding,
                attempt_number=1,
                owner_token=owner,
                basename=".taskgov-analysis-f1b2c3d4",
            )
            try:
                prepared = _prepare_physical(
                    MockScenario.SUCCESS,
                    capability=capability,
                    binding=binding,
                    attempt_number=1,
                )
                mark_prepared_mock_attempt_recorded(prepared)
                original = win32_boundary.enumerate_held_directory

                def fail_empty(handle, *, maximum_entries):
                    result = original(handle, maximum_entries=maximum_entries)
                    if handle is root and result == ():
                        raise win32_boundary.Win32BoundaryError()
                    return result

                with patch.object(
                    process_boundary._win32,
                    "enumerate_held_directory",
                    side_effect=fail_empty,
                ):
                    with self.assertRaises(ProcessQuarantineRequired) as raised:
                        execute_prepared_mock_attempt(prepared)
                self.assertIs(raised.exception.root_capability, capability)
                self.assertEqual(capability.state, "quarantine")
                self.assertFalse(root.closed)
                self.assertEqual(
                    original(root, maximum_entries=0),
                    (),
                )
                win32_boundary.remove_relative_directory(
                    root,
                    parent,
                    ".taskgov-analysis-f1b2c3d4",
                )
            finally:
                if not root.closed:
                    root.close()
                if not parent.closed:
                    parent.close()


if __name__ == "__main__":
    unittest.main()
