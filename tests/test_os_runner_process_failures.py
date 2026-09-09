"""Cross-host failure checks for the private POSIX process adapter."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from itertools import count
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import (  # noqa: E402
    _verification_runner_process_posix as posix,
    verification_runner_process as process,
)
from task_governance_tool.verification_runner import (  # noqa: E402
    RUNNER_CONTRACT_VERSION, RUNNER_POLICY_DIGEST, RUNNER_POSIX_POLICY_DIGEST,
)


ATTEMPT_ID = "tg_verification_runner_attempt_0123456789abcdef"


def _step(ordinal=1):
    return process.RunnerProcessStepV1(
        ordinal, f"check{ordinal}", "script", "checks/run.py", ("with space", ""),
        ".", False, False, 30, 2, None, None, process.MAX_OUTPUT_BYTES,
    )


def _request(*, count=1, root=None):
    root = PurePosixPath("/private") if root is None else root
    attempt = root / ATTEMPT_ID
    scratch = attempt / "scratch"
    return process.RunnerProcessRequestV1(
        RUNNER_CONTRACT_VERSION, ATTEMPT_ID, root / "python3", attempt / "target",
        scratch, (
            ("HOME", str(scratch / "home")), ("PYTHONDONTWRITEBYTECODE", "1"),
            ("PYTHONNOUSERSITE", "1"), ("PYTHONUTF8", "1"),
            ("TEMP", str(scratch / "tmp")), ("TMP", str(scratch / "tmp")),
            ("TMPDIR", str(scratch / "tmp")),
        ), tuple(_step(ordinal) for ordinal in range(1, count + 1)),
        process.RunnerCancelSignal(), RUNNER_POSIX_POLICY_DIGEST,
    )


def _execution(ordinal=1, outcome="pass", reason=None, launch="launched", cpu=7):
    return posix._StepExecution(
        process.RunnerProcessStepResultV1(
            ordinal, outcome, reason, launch, cpu, None, None,
            RUNNER_POSIX_POLICY_DIGEST,
        ), True, True, True,
    )


class _Pipe:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.closed = False
        self.fail_close = False

    def fileno(self):
        return self.descriptor

    def close(self):
        if self.fail_close:
            raise OSError("private close detail")
        self.closed = True


@contextmanager
def _step_resources(request, *, cpu=17, returncode=0):
    """Mock only OS resources; execute the adapter's actual terminal logic."""
    child = SimpleNamespace(pid=4123, returncode=None, stdout=_Pipe(41), stderr=_Pipe(42))
    drain = SimpleNamespace(failed=False, overflow=False, complete=True, poll=mock.Mock())

    def reap(state):
        state.child.returncode = returncode
        state.cpu_time_ms = cpu

    with ExitStack() as stack:
        prepared = stack.enter_context(mock.patch.object(
            posix, "_prepare_step", return_value=((str(request.executable), "fixed"), request.materialized_root),
        ))
        create = stack.enter_context(mock.patch.object(posix.subprocess, "Popen", return_value=child))
        stack.enter_context(mock.patch.object(posix, "_DiscardingPipes", return_value=drain))
        stack.enter_context(mock.patch.object(posix, "_poll_root", side_effect=reap))
        stack.enter_context(mock.patch.object(posix, "_group_absent", return_value=True))
        retire = stack.enter_context(mock.patch.object(posix, "_retire_group", return_value=True))
        stack.enter_context(mock.patch.object(posix.time, "monotonic", side_effect=count(step=1.0)))
        stack.enter_context(mock.patch.object(posix.time, "sleep"))
        yield SimpleNamespace(child=child, drain=drain, create=create, prepared=prepared, retire=retire)


class PosixRequestFailureTests(unittest.TestCase):
    def test_prelaunch_failure_is_empty_but_later_failure_keeps_exact_prefix(self):
        request = _request(count=3)
        blocked = _execution(1, "blocked_prelaunch", "process_create_failed", "no_launch", None)
        with mock.patch.object(posix, "_admit_request"), mock.patch.object(
            posix, "_execute_step", return_value=blocked,
        ) as execute:
            result = posix.run_process_request(request)
        self.assertEqual((result.outcome, result.reason, result.launch_state), (
            "blocked_prelaunch", "process_create_failed", "no_launch",
        ))
        self.assertEqual(result.steps, ())
        self.assertIsNone(result.failed_step_ordinal)
        execute.assert_called_once_with(request, request.steps[0])

        blocked = _execution(2, "blocked_prelaunch", "process_create_failed", "no_launch", None)
        with mock.patch.object(posix, "_admit_request"), mock.patch.object(
            posix, "_execute_step", side_effect=[_execution(), blocked],
        ) as execute:
            result = posix.run_process_request(request)
        self.assertEqual((result.outcome, result.reason, result.launch_state), (
            "process_error", "process_create_failed", "launched",
        ))
        self.assertEqual(tuple(step.ordinal for step in result.steps), (1, 2))
        self.assertEqual(result.failed_step_ordinal, 2)
        self.assertIsNone(result.cpu_time_ms)
        self.assertEqual(execute.call_count, 2)

    def test_each_launched_nonpass_stops_before_the_next_step(self):
        for outcome, reason in (
            ("fail", "step_nonzero"), ("timeout", "timeout"),
            ("resource_exceeded", "cpu_limit"), ("output_rejected", "output_limit"),
            ("process_error", "process_wait_failed"),
        ):
            with self.subTest(outcome=outcome):
                request = _request(count=3)
                with mock.patch.object(posix, "_admit_request"), mock.patch.object(
                    posix, "_execute_step", side_effect=[_execution(), _execution(2, outcome, reason)],
                ) as execute:
                    result = posix.run_process_request(request)
                self.assertEqual((result.outcome, result.reason), (outcome, reason))
                self.assertEqual(tuple(step.ordinal for step in result.steps), (1, 2))
                self.assertEqual(result.failed_step_ordinal, 2)
                self.assertEqual(result.cpu_time_ms, 14)
                self.assertIsNone(result.peak_job_memory_bytes)
                self.assertIsNone(result.total_process_count)
                self.assertEqual(result.runner_policy_digest, RUNNER_POSIX_POLICY_DIGEST)
                self.assertEqual(execute.call_count, 2)

    def test_cancellation_before_and_between_steps_never_launches_the_next_step(self):
        for before in (True, False):
            with self.subTest(before=before):
                request = _request(count=2)
                if before:
                    request.cancel_signal.request()

                def finish(_request, _step):
                    request.cancel_signal.request()
                    return _execution()

                with mock.patch.object(posix, "_admit_request"), mock.patch.object(
                    posix, "_execute_step", side_effect=finish,
                ) as execute:
                    result = posix.run_process_request(request)
                self.assertEqual(result.outcome, "blocked_prelaunch" if before else "cancelled")
                self.assertEqual(result.reason, "cancelled")
                self.assertEqual(result.launch_state, "no_launch" if before else "launched")
                self.assertEqual(len(result.steps), 0 if before else 1)
                self.assertIsNone(result.failed_step_ordinal)
                self.assertEqual(execute.call_count, 0 if before else 1)

    def test_cancel_observer_interruption_is_not_success(self):
        request = _request()
        with mock.patch.object(posix, "_admit_request"), mock.patch.object(
            process.RunnerCancelSignal, "requested", side_effect=KeyboardInterrupt,
        ), mock.patch.object(posix, "_execute_step") as execute:
            result = posix.run_process_request(request)
        self.assertEqual((result.outcome, result.reason, result.launch_state), (
            "controller_interrupted", "controller_interrupted", "no_launch",
        ))
        execute.assert_not_called()


class PosixStepFailureTests(unittest.TestCase):
    def test_setup_and_proven_creation_failure_are_no_launch(self):
        request = _request()
        with _step_resources(request) as resources:
            resources.prepared.side_effect = process.RunnerProcessError("process_boundary_unproved")
            execution = posix._execute_step(request, request.steps[0])
            resources.create.assert_not_called()
        self.assertEqual((execution.result.outcome, execution.result.reason, execution.result.launch_state), (
            "blocked_prelaunch", "process_boundary_unproved", "no_launch",
        ))
        with _step_resources(request) as resources:
            resources.create.side_effect = OSError("private creation detail")
            execution = posix._execute_step(request, request.steps[0])
            resources.retire.assert_not_called()
        self.assertEqual((execution.result.outcome, execution.result.reason, execution.result.launch_state), (
            "blocked_prelaunch", "process_create_failed", "no_launch",
        ))
        self.assertIsNone(execution.result.cpu_time_ms)

    def test_unproved_creation_interruption_is_launched_cleanup_failure(self):
        request = _request()
        with _step_resources(request) as resources:
            resources.create.side_effect = KeyboardInterrupt
            execution = posix._execute_step(request, request.steps[0])
        self.assertEqual((execution.result.outcome, execution.result.reason, execution.result.launch_state), (
            "cleanup_failed", "process_cleanup_failed", "launched",
        ))
        self.assertEqual((execution.process_zero, execution.handles_closed, execution.raw_output_discarded), (
            False, False, False,
        ))

    def test_process_zero_or_pipe_closure_uncertainty_cannot_pass(self):
        request = _request()
        for failure in ("process_zero", "retirement_interrupted", "stdout", "stderr"):
            with self.subTest(failure=failure), _step_resources(request) as resources:
                if failure == "process_zero":
                    resources.retire.return_value = False
                elif failure == "retirement_interrupted":
                    resources.retire.side_effect = KeyboardInterrupt
                else:
                    getattr(resources.child, failure).fail_close = True
                execution = posix._execute_step(request, request.steps[0])
            self.assertEqual((execution.result.outcome, execution.result.reason), (
                "cleanup_failed", "process_cleanup_failed",
            ))
            retirement_failed = failure in {"process_zero", "retirement_interrupted"}
            self.assertEqual(execution.process_zero, not retirement_failed)
            self.assertEqual(execution.handles_closed, retirement_failed)
            self.assertEqual(execution.raw_output_discarded, retirement_failed)
            # Failure of one close must not prevent closing the other pipe.
            if failure != "stderr":
                self.assertTrue(resources.child.stderr.closed)
            if failure != "stdout":
                self.assertTrue(resources.child.stdout.closed)

    def test_drain_failure_is_nonpass_even_when_closure_proves_discard(self):
        request = _request()
        with _step_resources(request) as resources:
            resources.drain.failed = True
            resources.drain.complete = False
            execution = posix._execute_step(request, request.steps[0])
        self.assertEqual((execution.result.outcome, execution.result.reason), (
            "process_error", "pipe_drain_failed",
        ))
        self.assertTrue(execution.process_zero)
        self.assertTrue(execution.handles_closed)
        self.assertTrue(execution.raw_output_discarded)

    def test_loop_interruption_and_unmeasured_cpu_never_become_pass(self):
        request = _request()
        with _step_resources(request) as resources:
            resources.drain.poll.side_effect = KeyboardInterrupt
            execution = posix._execute_step(request, request.steps[0])
        self.assertEqual((execution.result.outcome, execution.result.launch_state), (
            "controller_interrupted", "launched",
        ))
        self.assertTrue(execution.handles_closed)
        with _step_resources(request, cpu=None):
            execution = posix._execute_step(request, request.steps[0])
        self.assertEqual((execution.result.outcome, execution.result.reason), (
            "process_error", "process_wait_failed",
        ))


class PosixSyscallFailureTests(unittest.TestCase):
    def test_root_wait_accounting_uses_user_cpu_not_system_cpu(self):
        child = SimpleNamespace(pid=4123, returncode=None)
        state = posix._ChildState(child)
        native = SimpleNamespace(
            WNOHANG=1,
            wait4=mock.Mock(return_value=(4123, 0, SimpleNamespace(ru_utime=0.125, ru_stime=500.0))),
            waitstatus_to_exitcode=mock.Mock(return_value=0),
        )
        with mock.patch.object(posix, "os", native):
            posix._poll_root(state)
            posix._poll_root(state)
        self.assertEqual(state.cpu_time_ms, 125)
        self.assertEqual(child.returncode, 0)
        native.wait4.assert_called_once_with(4123, 1)

    def test_wait_failure_and_invalid_cpu_are_sanitized_and_unmeasured(self):
        for observation in (OSError("private wait detail"), float("nan"), -0.1, float("inf")):
            with self.subTest(observation=type(observation).__name__):
                state = posix._ChildState(SimpleNamespace(pid=4123, returncode=None))
                native = SimpleNamespace(WNOHANG=1, wait4=mock.Mock(), waitstatus_to_exitcode=lambda _status: 0)
                if isinstance(observation, Exception):
                    native.wait4.side_effect = observation
                else:
                    native.wait4.return_value = (4123, 0, SimpleNamespace(ru_utime=observation))
                with mock.patch.object(posix, "os", native), self.assertRaises(process.RunnerProcessError) as caught:
                    posix._poll_root(state)
                self.assertEqual(caught.exception.code, "process_wait_failed")
                self.assertEqual(str(caught.exception), "verification Runner process boundary failed closed")
                self.assertIsNone(state.cpu_time_ms)

    def test_only_cpu_signal_is_a_cpu_limit_not_an_unknown_sigkill(self):
        with mock.patch.object(posix, "signal", SimpleNamespace(SIGXCPU=24)):
            self.assertEqual(posix._exit_outcome(-24), ("resource_exceeded", "cpu_limit"))
            self.assertEqual(posix._exit_outcome(-9), ("fail", "step_nonzero"))
            self.assertEqual(posix._exit_outcome(7), ("fail", "step_nonzero"))
            self.assertEqual(posix._exit_outcome(0), ("pass", None))

    def test_retirement_interruption_never_proves_process_zero(self):
        state = posix._ChildState(SimpleNamespace(pid=4123, returncode=None))
        with mock.patch.object(posix, "_poll_root", side_effect=KeyboardInterrupt), mock.patch.object(
            posix, "_signal_group",
        ) as stop, mock.patch.object(posix.time, "monotonic", side_effect=count(step=0.1)), mock.patch.object(
            posix.time, "sleep",
        ):
            self.assertFalse(posix._retire_group(state, None))
        self.assertEqual(stop.call_args_list, [mock.call(4123, force=False), mock.call(4123, force=True)])

    def test_pipe_drain_bounds_counts_discards_bytes_and_reports_read_failure(self):
        child = SimpleNamespace(stdout=_Pipe(41), stderr=_Pipe(42))
        native = SimpleNamespace(set_blocking=mock.Mock(), read=mock.Mock(side_effect=[b"private-output", b"", OSError("private read detail")]))
        with mock.patch.object(posix, "os", native):
            drain = posix._DiscardingPipes(child, 4)
            drain.poll()
            self.assertTrue(drain.overflow)
            self.assertEqual(drain.count, 5)
            drain.poll()
        self.assertTrue(drain.failed)
        self.assertFalse(drain.complete)
        self.assertFalse(any(isinstance(value, (str, bytes, bytearray)) for value in vars(drain).values()))
        self.assertEqual(native.set_blocking.call_args_list, [mock.call(41, False), mock.call(42, False)])


class PosixAdmissionFailureTests(unittest.TestCase):
    def test_wrong_native_host_policy_or_non_native_paths_are_rejected(self):
        request = _request()
        with mock.patch.object(posix, "os", SimpleNamespace(name="nt")):
            with self.assertRaises(process.RunnerProcessError) as caught:
                posix._admit_request(request)
        self.assertEqual(caught.exception.code, "runtime_unavailable")
        with mock.patch.object(posix, "os", SimpleNamespace(name="posix")), mock.patch.object(
            posix, "sys", SimpleNamespace(platform="linux"),
        ), mock.patch.object(posix.subprocess, "Popen") as create:
            for invalid in (request, replace(request, runner_policy_digest=RUNNER_POLICY_DIGEST)):
                with self.subTest(policy=invalid.runner_policy_digest), self.assertRaises(process.RunnerProcessError) as caught:
                    posix._admit_request(invalid)
                self.assertEqual(caught.exception.code, "process_setup_failed")
            create.assert_not_called()

    def test_physical_admission_rejects_owned_executable_changed_env_and_missing_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            request = _request(root=Path(temporary).resolve())
            request.executable.write_bytes(b"never executed fixture")
            request.materialized_root.mkdir(parents=True)
            request.scratch_root.mkdir()
            for child in ("home", "tmp", "local", "roaming"):
                (request.scratch_root / child).mkdir()
            entry = request.materialized_root / "checks" / "run.py"
            entry.parent.mkdir()
            entry.write_text("raise SystemExit(0)\n", encoding="utf-8")
            with mock.patch.object(posix, "os", SimpleNamespace(name="posix")), mock.patch.object(
                posix, "sys", SimpleNamespace(platform="linux"),
            ), mock.patch.object(posix.subprocess, "Popen") as create:
                posix._admit_request(request)
                for invalid in (
                    replace(request, executable=request.materialized_root / "python3"),
                    replace(request, clean_environment=request.clean_environment + (("PATH", "/ambient"),)),
                    replace(request, clean_environment=tuple(reversed(request.clean_environment))),
                ):
                    with self.assertRaises(process.RunnerProcessError) as caught:
                        posix._admit_request(invalid)
                    self.assertEqual(caught.exception.code, "process_setup_failed")
                missing = replace(request, steps=(replace(request.steps[0], entrypoint="checks/missing.py"),))
                with self.assertRaises(process.RunnerProcessError) as caught:
                    posix._admit_request(missing)
                self.assertEqual(caught.exception.code, "process_boundary_unproved")
                create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
