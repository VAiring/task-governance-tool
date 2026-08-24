from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _verification_runner_win32 as win32  # noqa: E402
from task_governance_tool import verification_runner_process as process  # noqa: E402
from task_governance_tool.verification_runner import (  # noqa: E402
    RUNNER_CONTRACT_VERSION,
    RUNNER_EXECUTABLE_ID,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_MAX_OUTPUT_BYTES,
)


ATTEMPT_ID = "tg_verification_runner_attempt_0123456789abcdef"


@dataclass(frozen=True)
class _Layout:
    runtime: Path
    target: Path
    scratch: Path
    windows: Path


def _make_layout(root: Path) -> _Layout:
    attempt = root / ATTEMPT_ID
    target = attempt / "target"
    scratch = attempt / "scratch"
    runtime = root / "runtime" / "python.exe"
    windows = root / "Windows"
    target.mkdir(parents=True)
    scratch.mkdir()
    runtime.parent.mkdir()
    runtime.write_bytes(b"fixed-runtime")
    windows.mkdir()
    for child in ("tmp", "home", "local", "roaming"):
        (scratch / child).mkdir()
    (target / "checks").mkdir()
    (target / "checks" / "run.py").write_text(
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    return _Layout(runtime, target, scratch, windows)


def _step(
    ordinal: int = 1,
    step_id: str = "unit",
    **changes: object,
) -> process.RunnerProcessStepV1:
    values = {
        "ordinal": ordinal,
        "step_id": step_id,
        "mode": "script",
        "entrypoint": "checks/run.py",
        "argv": ("", "plain", "with space", 'quote"inside', "trail\\"),
        "cwd": ".",
        "shell": False,
        "path_lookup": False,
        "timeout_seconds": 30,
        "cpu_seconds": 20,
        "memory_mib": 128,
        "process_limit": 2,
        "output_byte_limit": RUNNER_MAX_OUTPUT_BYTES,
    }
    values.update(changes)
    return process.RunnerProcessStepV1(**values)


def _request(
    layout: _Layout,
    *,
    steps: tuple[process.RunnerProcessStepV1, ...] | None = None,
    signal: process.RunnerCancelSignal | None = None,
) -> process.RunnerProcessRequestV1:
    with patch.object(
        process._win32,
        "verified_windows_directory",
        return_value=layout.windows,
    ):
        clean_environment = process.build_clean_environment(
            layout.windows,
            layout.scratch,
        )
    return process.RunnerProcessRequestV1(
        RUNNER_CONTRACT_VERSION,
        ATTEMPT_ID,
        layout.runtime,
        layout.target,
        layout.scratch,
        clean_environment,
        (_step(),) if steps is None else steps,
        process.RunnerCancelSignal() if signal is None else signal,
    )


def _admit(
    request: process.RunnerProcessRequestV1,
    layout: _Layout,
) -> process._AdmittedRequest:
    with patch.object(
        process._win32,
        "verified_windows_directory",
        return_value=layout.windows,
    ):
        return process._admit_request(request)


def _run(
    request: process.RunnerProcessRequestV1,
    layout: _Layout,
) -> process.RunnerProcessResultV1:
    with patch.object(
        process._win32,
        "verified_windows_directory",
        return_value=layout.windows,
    ):
        return process.run_process_request(request)


def _step_result(
    ordinal: int,
    outcome: str = "pass",
    reason: str | None = None,
    launch_state: str = "launched",
    accounting: tuple[int | None, int | None, int | None] = (2, 128, 1),
) -> process.RunnerProcessStepResultV1:
    return process.RunnerProcessStepResultV1(
        ordinal,
        outcome,
        reason,
        launch_state,
        *accounting,
    )


def _execution(
    result: process.RunnerProcessStepResultV1,
    *,
    process_zero: bool = True,
    handles_closed: bool = True,
    raw_output_discarded: bool = True,
) -> process._StepExecution:
    return process._StepExecution(
        result,
        None,
        process_zero,
        handles_closed,
        raw_output_discarded,
    )


class RunnerProcessPureTests(unittest.TestCase):
    def test_exact_closed_record_fields_and_single_entry(self):
        expected = {
            process.RunnerProcessRequestV1: (
                "version",
                "attempt_id",
                "executable",
                "materialized_root",
                "scratch_root",
                "clean_environment",
                "steps",
                "cancel_signal",
            ),
            process.RunnerProcessStepV1: (
                "ordinal",
                "step_id",
                "mode",
                "entrypoint",
                "argv",
                "cwd",
                "shell",
                "path_lookup",
                "timeout_seconds",
                "cpu_seconds",
                "memory_mib",
                "process_limit",
                "output_byte_limit",
            ),
            process.RunnerProcessResultV1: (
                "version",
                "attempt_id",
                "outcome",
                "reason",
                "launch_state",
                "failed_step_ordinal",
                "duration_ms",
                "cpu_time_ms",
                "peak_job_memory_bytes",
                "total_process_count",
                "process_zero",
                "handles_closed",
                "raw_output_discarded",
                "steps",
            ),
            process.RunnerProcessStepResultV1: (
                "ordinal",
                "outcome",
                "reason",
                "launch_state",
                "cpu_time_ms",
                "peak_job_memory_bytes",
                "total_process_count",
            ),
        }
        for record, fields in expected.items():
            with self.subTest(record=record.__name__):
                self.assertEqual(
                    tuple(field.name for field in dataclasses.fields(record)),
                    fields,
                )
                self.assertTrue(record.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(inspect.signature(process.run_process_request).parameters),
            ("request",),
        )
        self.assertIs(
            process.ProcessRunResult,
            process.RunnerProcessResultV1,
        )
        self.assertFalse(hasattr(process, "ProcessStep"))
        self.assertFalse(hasattr(process, "StepProcessResult"))
        self.assertFalse(hasattr(process, "run_process_steps"))

    def test_central_identity_and_fixed_bootstrap(self):
        self.assertEqual(
            process.RUNNER_CONTRACT_VERSION,
            RUNNER_CONTRACT_VERSION,
        )
        self.assertEqual(
            process.RUNNER_IMPLEMENTATION_VERSION,
            RUNNER_IMPLEMENTATION_VERSION,
        )
        self.assertEqual(process.EXECUTABLE_ID, RUNNER_EXECUTABLE_ID)
        self.assertEqual(process.MAX_OUTPUT_BYTES, RUNNER_MAX_OUTPUT_BYTES)
        self.assertIn("runpy.run_path", process.FIXED_BOOTSTRAP)
        self.assertIn("runpy.run_module", process.FIXED_BOOTSTRAP)
        self.assertIn("find_spec", process.FIXED_BOOTSTRAP)
        self.assertIn("os.path.isabs(_spec.origin)", process.FIXED_BOOTSTRAP)
        self.assertIn("_under(_root,_spec.origin)", process.FIXED_BOOTSTRAP)
        self.assertLess(
            process.FIXED_BOOTSTRAP.index("not os.path.isabs(_spec.origin)"),
            process.FIXED_BOOTSTRAP.index("not _under(_root,_spec.origin)"),
        )
        self.assertNotIn("subprocess", process.FIXED_BOOTSTRAP)
        self.assertNotIn("shell", process.FIXED_BOOTSTRAP)

    def test_command_line_exact_utf16_bound_and_windows_roundtrip(self):
        exact = ("p.exe", "x" * 24_570)
        self.assertEqual(
            len(process.quote_windows_argv(exact).encode("utf-16-le")) // 2,
            24_576,
        )
        with self.assertRaises(process.RunnerProcessError):
            process.quote_windows_argv(("p.exe", "x" * 24_571))
        with self.assertRaises(process.RunnerProcessError):
            process.quote_windows_argv(("p.exe", "\ud800"))
        if os.name == "nt":
            fixtures = (
                ("program.exe",),
                (
                    "program.exe",
                    "",
                    "plain",
                    "with space",
                    'quote"inside',
                    "trail\\",
                ),
                (
                    r"C:\private runtime\python.exe",
                    "-c",
                    "line1\nline2",
                    "日本語",
                ),
            )
            for argv in fixtures:
                with self.subTest(argv=argv):
                    serialized = process.quote_windows_argv(argv)
                    self.assertEqual(
                        win32.command_line_to_argv(serialized),
                        argv,
                    )

    def test_step_validation_covers_exact_shape_and_unicode(self):
        self.assertEqual(_step().mode, "script")
        invalid = (
            {"ordinal": 0},
            {"step_id": "UPPER"},
            {"mode": "command"},
            {"entrypoint": "../run.py"},
            {"entrypoint": "checks/RUN.PY"},
            {"argv": ["not", "tuple"]},
            {"argv": ("bad\n",)},
            {"argv": ("bad\u0085",)},
            {"argv": ("\ud800",)},
            {"argv": ("x" * 4097,)},
            {"argv": tuple("x" for _ in range(65))},
            {"cwd": "../outside"},
            {"shell": True},
            {"path_lookup": True},
            {"timeout_seconds": 0},
            {"cpu_seconds": 901},
            {"memory_mib": 63},
            {"process_limit": 33},
            {"output_byte_limit": 1},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(
                process.RunnerProcessError
            ):
                _step(**changes)
        module = _step(
            mode="module",
            entrypoint="pkg.check",
        )
        self.assertEqual(module.entrypoint, "pkg.check")

    def test_request_and_environment_validation_is_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            keys = tuple(key for key, _value in request.clean_environment)
            self.assertEqual(keys, process._ENVIRONMENT_KEYS)
            self.assertTrue(
                set(keys).isdisjoint(
                    {"PATH", "HTTP_PROXY", "TOKEN", "PYTHONPATH"}
                )
            )
            invalid = (
                {"version": 2},
                {"attempt_id": ATTEMPT_ID[:-1] + "G"},
                {"executable": str(layout.runtime)},
                {"materialized_root": layout.target.parent / "wrong"},
                {"scratch_root": layout.scratch.parent / "wrong"},
                {"steps": (_step(2),)},
                {"steps": (_step(1, "same"), _step(2, "same"))},
                {"cancel_signal": object()},
            )
            for changes in invalid:
                with self.subTest(changes=changes), self.assertRaises(
                    process.RunnerProcessError
                ):
                    replace(request, **changes)
            wrong_environment = list(request.clean_environment)
            wrong_environment[0] = ("PATH", wrong_environment[0][1])
            with self.assertRaises(process.RunnerProcessError):
                replace(
                    request,
                    clean_environment=tuple(wrong_environment),
                )
            other_windows = Path(temporary).resolve() / "OtherWindows"
            other_windows.mkdir()
            with patch.object(
                process._win32,
                "verified_windows_directory",
                return_value=other_windows,
            ), self.assertRaises(process.RunnerProcessError):
                process.build_clean_environment(
                    layout.windows,
                    layout.scratch,
                )
            long_steps = (
                _step(1, "one", timeout_seconds=900),
                _step(2, "two", timeout_seconds=900),
                _step(3, "three", timeout_seconds=1),
            )
            with self.assertRaises(process.RunnerProcessError):
                replace(request, steps=long_steps)

    def test_all_admission_fails_before_first_native_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            missing = _request(
                layout,
                steps=(_step(entrypoint="checks/missing.py"),),
            )
            overlong = _request(
                layout,
                steps=(
                    _step(
                        argv=tuple("x" * 4096 for _ in range(7)),
                    ),
                ),
            )
            for request in (missing, overlong):
                with self.subTest(request=request), patch.object(
                    process._win32,
                    "create_job",
                ) as create_job, self.assertRaises(
                    process.RunnerProcessError
                ):
                    _run(request, layout)
                create_job.assert_not_called()

    def test_path_identity_change_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            admitted = _admit(request, layout)
            for observation in admitted.observations:
                self.assertEqual(
                    observation.chain[0][0],
                    os.path.normcase(str(Path(observation.path.anchor))),
                )
            script = layout.target / "checks" / "run.py"
            replacement = layout.target / "checks" / "replacement.py"
            replacement.write_text("raise SystemExit(9)\n", encoding="utf-8")
            os.replace(replacement, script)
            with self.assertRaises(process.RunnerProcessError):
                for observation in admitted.steps[0].observations:
                    process._ensure_same_observation(observation)

    def test_result_pairing_proofs_and_privacy_are_closed(self):
        passed = _step_result(1)
        result = process.RunnerProcessResultV1(
            1,
            ATTEMPT_ID,
            "pass",
            None,
            "launched",
            None,
            5,
            2,
            128,
            1,
            True,
            True,
            True,
            (passed,),
        )
        self.assertEqual(result.outcome, "pass")
        invalid_steps = (
            (1, "pass", "timeout", "launched", 1, 1, 1),
            (1, "pass", None, "no_launch", None, None, None),
            (1, "not_run", None, "no_launch", None, None, None),
            (1, "fail", None, "launched", 1, 1, 1),
        )
        for values in invalid_steps:
            with self.subTest(values=values), self.assertRaises(
                process.RunnerProcessError
            ):
                process.RunnerProcessStepResultV1(*values)
        with self.assertRaises(process.RunnerProcessError):
            replace(result, reason="timeout")
        with self.assertRaises(process.RunnerProcessError):
            replace(result, process_zero=False)
        cleanup = process.RunnerProcessResultV1(
            1,
            ATTEMPT_ID,
            "cleanup_failed",
            "process_cleanup_failed",
            "launched",
            1,
            5,
            None,
            None,
            None,
            False,
            True,
            True,
            (),
        )
        self.assertFalse(cleanup.process_zero)
        forbidden = {
            "raw_output",
            "argv",
            "environment",
            "credential",
            "path",
            "exit_code",
            "exception",
        }
        self.assertTrue(
            forbidden.isdisjoint(
                field.name
                for field in dataclasses.fields(
                    process.RunnerProcessResultV1
                )
            )
        )
        error = process.RunnerProcessError("secret-token")
        self.assertNotIn("secret-token", str(error))
        self.assertEqual(error.code, "process_setup_failed")

    def test_native_error_codes_map_to_local_non_sandbox_taxonomy(self):
        self.assertEqual(
            set(process._NATIVE_REASON_MAP),
            {
                "sandbox_unavailable",
                "sandbox_setup_failed",
                "sandbox_boundary_violation",
                "process_create_failed",
                "process_resume_failed",
                "process_wait_failed",
                "pipe_drain_failed",
                "job_state_unproved",
                "sandbox_cleanup_failed",
            },
        )
        self.assertTrue(
            all(
                "sandbox" not in reason
                for reason in process._NATIVE_REASON_MAP.values()
            )
        )
        self.assertNotIn("process_limit", process._LOCAL_REASONS)
        self.assertTrue(
            all(
                "sandbox" not in str(item)
                for item in process._STEP_PAIRINGS
                | process._RESULT_PAIRINGS
            )
        )

    def test_prelaunch_native_cleanup_failure_returns_closed_false_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            with patch.object(
                process._win32,
                "create_job",
                side_effect=win32.RunnerWin32Error("sandbox_cleanup_failed"),
            ):
                result = _run(request, layout)
        self.assertEqual(
            (result.outcome, result.reason, result.launch_state),
            ("cleanup_failed", "process_cleanup_failed", "no_launch"),
        )
        self.assertTrue(result.process_zero)
        self.assertFalse(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)
        self.assertEqual(result.steps, ())

    def test_prelaunch_job_state_failures_map_to_boundary_pairing(self):
        class Handle:
            def __init__(self) -> None:
                self.closed = False

        class Pipes:
            def __init__(self) -> None:
                self.stdin_child = Handle()
                self.stdout_child = Handle()
                self.stderr_child = Handle()
                self.stdout_parent = Handle()
                self.stderr_parent = Handle()

            def close_child_ends(self) -> None:
                self.stdin_child.closed = True
                self.stdout_child.closed = True
                self.stderr_child.closed = True

            def close_parent_ends(self) -> None:
                self.stdout_parent.closed = True
                self.stderr_parent.closed = True

        class Job:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            with patch.object(
                process._win32,
                "create_job",
                side_effect=win32.RunnerWin32Error("job_state_unproved"),
            ):
                initial = _run(request, layout)

            job = Job()
            pipes = Pipes()
            with patch.object(
                process._win32,
                "create_job",
                return_value=job,
            ), patch.object(
                process._win32,
                "create_stdio_pipes",
                return_value=pipes,
            ), patch.object(
                process._win32,
                "create_suspended_child",
                side_effect=win32.RunnerWin32Error("job_state_unproved"),
            ):
                revalidated = _run(request, layout)

        for result in (initial, revalidated):
            with self.subTest(result=result):
                self.assertEqual(
                    (result.outcome, result.reason, result.launch_state),
                    (
                        "blocked_prelaunch",
                        "process_boundary_unproved",
                        "no_launch",
                    ),
                )
                self.assertIsNone(result.failed_step_ordinal)
                self.assertTrue(result.process_zero)
                self.assertTrue(result.handles_closed)
                self.assertTrue(result.raw_output_discarded)
                self.assertEqual(result.steps, ())
        self.assertTrue(job.closed)

    def test_late_drain_failure_overrides_forced_cancel(self):
        class Handle:
            def __init__(self) -> None:
                self.closed = False

        class Pipes:
            def __init__(self) -> None:
                self.stdin_child = Handle()
                self.stdout_child = Handle()
                self.stderr_child = Handle()
                self.stdout_parent = Handle()
                self.stderr_parent = Handle()

            def close_child_ends(self) -> None:
                self.stdin_child.closed = True
                self.stdout_child.closed = True
                self.stderr_child.closed = True

            def close_parent_ends(self) -> None:
                self.stdout_parent.closed = True
                self.stderr_parent.closed = True

        class Job:
            def __init__(self) -> None:
                self.terminated = 0
                self.closed = False

            def contains(self, _process: object) -> bool:
                return True

            def terminate(self) -> None:
                self.terminated += 1

            def wait_for_zero(self, *, deadline: float) -> win32.JobAccounting:
                self.deadline = deadline
                return win32.JobAccounting(0, 0, 1, 0)

            def close(self) -> None:
                self.closed = True

        class Child:
            def __init__(self) -> None:
                self.process = object()
                self.resumed = False
                self.closed = False

            def resume_once(self) -> None:
                self.resumed = True

            def close(self) -> None:
                self.closed = True

        class Drain:
            def __init__(self, signal: process.RunnerCancelSignal) -> None:
                self.signal = signal
                self.failed = threading.Event()
                self.overflow = threading.Event()

            def start(self) -> None:
                self.signal.request()

            def join(self, _timeout: float) -> bool:
                self.failed.set()
                return True

        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            job = Job()
            pipes = Pipes()
            child = Child()
            drain = Drain(request.cancel_signal)
            with patch.object(
                process._win32,
                "create_job",
                return_value=job,
            ), patch.object(
                process._win32,
                "create_stdio_pipes",
                return_value=pipes,
            ), patch.object(
                process._win32,
                "create_suspended_child",
                return_value=child,
            ), patch.object(
                process,
                "_DiscardingDrain",
                return_value=drain,
            ):
                result = _run(request, layout)

        self.assertEqual(
            (result.outcome, result.reason, result.launch_state),
            ("process_error", "pipe_drain_failed", "launched"),
        )
        self.assertEqual(result.failed_step_ordinal, 1)
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(
            (
                result.steps[0].outcome,
                result.steps[0].reason,
                result.steps[0].launch_state,
            ),
            ("process_error", "pipe_drain_failed", "launched"),
        )
        self.assertIsNone(result.cpu_time_ms)
        self.assertIsNone(result.peak_job_memory_bytes)
        self.assertIsNone(result.total_process_count)
        self.assertTrue(result.process_zero)
        self.assertTrue(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)
        self.assertTrue(child.resumed)
        self.assertTrue(child.closed)
        self.assertTrue(job.closed)

    def test_exited_root_with_active_descendant_uses_bounded_poll_wait(self):
        class Handle:
            def __init__(self) -> None:
                self.closed = False

        class Pipes:
            def __init__(self) -> None:
                self.stdin_child = Handle()
                self.stdout_child = Handle()
                self.stderr_child = Handle()
                self.stdout_parent = Handle()
                self.stderr_parent = Handle()

            def close_child_ends(self) -> None:
                self.stdin_child.closed = True
                self.stdout_child.closed = True
                self.stderr_child.closed = True

            def close_parent_ends(self) -> None:
                self.stdout_parent.closed = True
                self.stderr_parent.closed = True

        class Job:
            def __init__(self) -> None:
                self.accounting_calls = 0
                self.terminated = 0
                self.closed = False

            def contains(self, _process: object) -> bool:
                return True

            def accounting(self) -> win32.JobAccounting:
                self.accounting_calls += 1
                return win32.JobAccounting(
                    0,
                    0,
                    1,
                    1 if self.accounting_calls == 1 else 0,
                )

            def wait_for_zero(self, *, deadline: float) -> win32.JobAccounting:
                self.deadline = deadline
                return win32.JobAccounting(0, 0, 1, 0)

            def limit_violation_reason(
                self,
                _accounting: win32.JobAccounting,
            ) -> None:
                return None

            def terminate(self) -> None:
                self.terminated += 1

            def close(self) -> None:
                self.closed = True

        class Child:
            def __init__(self) -> None:
                self.process = object()
                self.wait_calls = 0
                self.resumed = False
                self.closed = False

            def resume_once(self) -> None:
                self.resumed = True

            def wait(self, milliseconds: int) -> bool:
                self.assert_milliseconds = milliseconds
                self.wait_calls += 1
                return True

            def poll(self) -> int:
                return 0

            def close(self) -> None:
                self.closed = True

        class Drain:
            def __init__(self) -> None:
                self.failed = threading.Event()
                self.overflow = threading.Event()
                self.started = False

            def start(self) -> None:
                self.started = True

            def join(self, _timeout: float) -> bool:
                return True

        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            job = Job()
            pipes = Pipes()
            child = Child()
            drain = Drain()
            with patch.object(
                process._win32,
                "create_job",
                return_value=job,
            ), patch.object(
                process._win32,
                "create_stdio_pipes",
                return_value=pipes,
            ), patch.object(
                process._win32,
                "create_suspended_child",
                return_value=child,
            ), patch.object(
                process,
                "_DiscardingDrain",
                return_value=drain,
            ), patch.object(process.time, "sleep") as sleep:
                result = _run(request, layout)

        self.assertEqual(
            (result.outcome, result.reason, result.launch_state),
            ("pass", None, "launched"),
        )
        self.assertTrue(result.process_zero)
        self.assertTrue(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)
        self.assertTrue(child.resumed)
        self.assertEqual(child.wait_calls, 2)
        self.assertEqual(child.assert_milliseconds, 10)
        self.assertEqual(job.accounting_calls, 2)
        self.assertEqual(job.terminated, 0)
        sleep.assert_called_once()
        self.assertGreater(sleep.call_args.args[0], 0.0)
        self.assertLessEqual(sleep.call_args.args[0], 0.01)
        self.assertTrue(child.closed)
        self.assertTrue(job.closed)

    def test_parent_pipe_close_is_attempted_after_child_close_failure(self):
        class Handle:
            def __init__(self) -> None:
                self.closed = False

        class Pipes:
            def __init__(self) -> None:
                self.stdin_child = Handle()
                self.stdout_child = Handle()
                self.stderr_child = Handle()
                self.stdout_parent = Handle()
                self.stderr_parent = Handle()
                self.child_close_attempted = False
                self.parent_close_attempted = False

            def close_child_ends(self) -> None:
                self.child_close_attempted = True
                raise win32.RunnerWin32Error("sandbox_cleanup_failed")

            def close_parent_ends(self) -> None:
                self.parent_close_attempted = True
                self.stdout_parent.closed = True
                self.stderr_parent.closed = True

        class Job:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            job = Job()
            pipes = Pipes()
            with patch.object(
                process._win32,
                "create_job",
                return_value=job,
            ), patch.object(
                process._win32,
                "create_stdio_pipes",
                return_value=pipes,
            ), patch.object(
                process._win32,
                "create_suspended_child",
                side_effect=win32.RunnerWin32Error("process_create_failed"),
            ):
                result = _run(request, layout)

        self.assertTrue(pipes.child_close_attempted)
        self.assertTrue(pipes.parent_close_attempted)
        self.assertTrue(job.closed)
        self.assertEqual(
            (result.outcome, result.reason, result.launch_state),
            ("cleanup_failed", "process_cleanup_failed", "no_launch"),
        )
        self.assertTrue(result.process_zero)
        self.assertFalse(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)
        self.assertEqual(result.steps, ())

    def test_multi_step_aggregation_and_stop_on_nonpass(self):
        first = _step_result(1, accounting=(2, 100, 2))
        failure = _step_result(
            2,
            "fail",
            "step_nonzero",
            accounting=(3, 90, 3),
        )
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(
                layout,
                steps=(_step(1, "one"), _step(2, "two"), _step(3, "three")),
            )
            with patch.object(
                process,
                "_execute_step",
                side_effect=(_execution(first), _execution(failure)),
            ) as execute:
                result = _run(request, layout)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(result.outcome, "fail")
        self.assertEqual(result.failed_step_ordinal, 2)
        self.assertEqual(result.cpu_time_ms, 5)
        self.assertEqual(result.peak_job_memory_bytes, 100)
        self.assertEqual(result.total_process_count, 5)
        self.assertTrue(result.process_zero)
        self.assertTrue(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)

    def test_first_and_later_prelaunch_failures_are_exact(self):
        first_no_launch = _step_result(
            1,
            "blocked_prelaunch",
            "process_create_failed",
            "no_launch",
            (None, None, None),
        )
        prior_pass = _step_result(1)
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            first_request = _request(layout)
            later_request = _request(
                layout,
                steps=(_step(1, "one"), _step(2, "two")),
            )
            with patch.object(
                process,
                "_execute_step",
                return_value=_execution(first_no_launch),
            ):
                first = _run(first_request, layout)
            later_results = {}
            for reason in (
                "runtime_unavailable",
                "process_setup_failed",
                "process_boundary_unproved",
                "process_create_failed",
            ):
                later_no_launch = _step_result(
                    2,
                    "blocked_prelaunch",
                    reason,
                    "no_launch",
                    (None, None, None),
                )
                with patch.object(
                    process,
                    "_execute_step",
                    side_effect=(
                        _execution(prior_pass),
                        _execution(later_no_launch),
                    ),
                ):
                    later_results[reason] = _run(later_request, layout)
        self.assertEqual(first.launch_state, "no_launch")
        self.assertEqual(first.outcome, "blocked_prelaunch")
        self.assertIsNone(first.failed_step_ordinal)
        self.assertEqual(first.steps, ())
        for reason, later in later_results.items():
            with self.subTest(reason=reason):
                self.assertEqual(later.launch_state, "launched")
                self.assertEqual(later.outcome, "process_error")
                self.assertEqual(later.reason, reason)
                self.assertEqual(later.failed_step_ordinal, 2)
                self.assertEqual(len(later.steps), 2)
                self.assertEqual(
                    (
                        later.steps[-1].outcome,
                        later.steps[-1].reason,
                        later.steps[-1].launch_state,
                    ),
                    ("blocked_prelaunch", reason, "no_launch"),
                )

    def test_typed_cancel_before_and_between_steps_starts_no_next_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            first_signal = process.RunnerCancelSignal(True)
            first_request = _request(layout, signal=first_signal)
            with patch.object(process, "_execute_step") as execute:
                first = _run(first_request, layout)
            execute.assert_not_called()
            self.assertEqual(first.outcome, "blocked_prelaunch")
            self.assertEqual(first.reason, "cancelled")
            self.assertTrue(first.process_zero)
            self.assertTrue(first.handles_closed)
            self.assertTrue(first.raw_output_discarded)

            between_signal = process.RunnerCancelSignal()
            between_request = _request(
                layout,
                steps=(_step(1, "one"), _step(2, "two")),
                signal=between_signal,
            )

            def execute_once(**_kwargs: object) -> process._StepExecution:
                between_signal.request()
                return _execution(_step_result(1, accounting=(3, 256, 2)))

            with patch.object(
                process,
                "_execute_step",
                side_effect=execute_once,
            ) as execute:
                between = _run(between_request, layout)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(between.outcome, "cancelled")
            self.assertEqual(between.launch_state, "launched")
            self.assertIsNone(between.failed_step_ordinal)
            self.assertEqual(between.cpu_time_ms, 3)
            self.assertEqual(between.total_process_count, 2)

    def test_start_uncertainty_retires_job_and_proves_zero(self):
        class Handle:
            def __init__(self) -> None:
                self.closed = False

        class Pipes:
            def __init__(self) -> None:
                self.stdin_child = Handle()
                self.stdout_child = Handle()
                self.stderr_child = Handle()
                self.stdin_parent = Handle()
                self.stdout_parent = Handle()
                self.stderr_parent = Handle()

            def close_child_ends(self) -> None:
                self.stdin_child.closed = True
                self.stdout_child.closed = True
                self.stderr_child.closed = True

            def close_parent_ends(self) -> None:
                self.stdin_parent.closed = True
                self.stdout_parent.closed = True
                self.stderr_parent.closed = True

        class Job:
            def __init__(self) -> None:
                self.terminated = False
                self.zero_waited = False
                self.closed = False

            def terminate(self) -> None:
                self.terminated = True

            def wait_for_zero(self, *, deadline: float) -> win32.JobAccounting:
                self.assert_deadline = deadline
                self.zero_waited = True
                return win32.JobAccounting(0, 0, 1, 0)

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            job = Job()
            pipes = Pipes()
            with patch.object(
                process._win32,
                "verified_windows_directory",
                return_value=layout.windows,
            ), patch.object(
                process._win32,
                "create_job",
                return_value=job,
            ), patch.object(
                process._win32,
                "create_stdio_pipes",
                return_value=pipes,
            ), patch.object(
                process._win32,
                "create_suspended_child",
                side_effect=KeyboardInterrupt(),
            ):
                result = process.run_process_request(request)
        self.assertTrue(job.terminated)
        self.assertTrue(job.zero_waited)
        self.assertTrue(job.closed)
        self.assertEqual(result.outcome, "controller_interrupted")
        self.assertEqual(result.launch_state, "launched")
        self.assertTrue(result.process_zero)
        self.assertTrue(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)

    def test_post_create_create_failure_returns_request_only_closed_result(self):
        class Handle:
            def __init__(self) -> None:
                self.closed = False

        class Pipes:
            def __init__(self) -> None:
                self.stdin_child = Handle()
                self.stdout_child = Handle()
                self.stderr_child = Handle()
                self.stdout_parent = Handle()
                self.stderr_parent = Handle()

            def close_child_ends(self) -> None:
                self.stdin_child.closed = True
                self.stdout_child.closed = True
                self.stderr_child.closed = True

            def close_parent_ends(self) -> None:
                self.stdout_parent.closed = True
                self.stderr_parent.closed = True

        class Job:
            def __init__(self) -> None:
                self.terminated = False
                self.zero_waited = False
                self.closed = False

            def terminate(self) -> None:
                self.terminated = True

            def wait_for_zero(self, *, deadline: float) -> win32.JobAccounting:
                self.deadline = deadline
                self.zero_waited = True
                return win32.JobAccounting(0, 0, 1, 0)

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            job = Job()
            pipes = Pipes()
            with patch.object(
                process._win32,
                "create_job",
                return_value=job,
            ), patch.object(
                process._win32,
                "create_stdio_pipes",
                return_value=pipes,
            ), patch.object(
                process._win32,
                "create_suspended_child",
                side_effect=win32.RunnerWin32Error(
                    "process_create_failed",
                    after_create=True,
                ),
            ):
                result = _run(request, layout)

        self.assertEqual(
            (result.outcome, result.reason, result.launch_state),
            ("process_error", "process_create_failed", "launched"),
        )
        self.assertEqual(result.failed_step_ordinal, 1)
        self.assertEqual(result.steps, ())
        self.assertEqual(
            (
                result.cpu_time_ms,
                result.peak_job_memory_bytes,
                result.total_process_count,
            ),
            (None, None, None),
        )
        self.assertTrue(result.process_zero)
        self.assertTrue(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)
        self.assertTrue(job.terminated)
        self.assertTrue(job.zero_waited)
        self.assertTrue(job.closed)

    def test_later_post_create_failure_preserves_prefix_without_partial_accounting(self):
        prior = _step_result(1, accounting=(2, 100, 2))
        request_only = process._RequestOnlyCreateFailure(2, True, True, True)
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(
                layout,
                steps=(_step(1, "one"), _step(2, "two"), _step(3, "three")),
            )
            with patch.object(
                process,
                "_execute_step",
                side_effect=(_execution(prior), request_only),
            ) as execute:
                result = _run(request, layout)

        self.assertEqual(execute.call_count, 2)
        self.assertEqual(
            (result.outcome, result.reason, result.launch_state),
            ("process_error", "process_create_failed", "launched"),
        )
        self.assertEqual(result.failed_step_ordinal, 2)
        self.assertEqual(result.steps, (prior,))
        self.assertEqual(
            (
                result.cpu_time_ms,
                result.peak_job_memory_bytes,
                result.total_process_count,
            ),
            (None, None, None),
        )
        self.assertTrue(result.process_zero)
        self.assertTrue(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)

    def test_accounting_accepts_exact_request_limits_and_cumulative_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            layout = _make_layout(Path(temporary).resolve())
            request = _request(layout)
            exact_step = _step_result(
                1,
                accounting=(20_000, 128 * 1_048_576, 10_000),
            )
            exact = process.RunnerProcessResultV1(
                1,
                ATTEMPT_ID,
                "pass",
                None,
                "launched",
                None,
                1,
                20_000,
                128 * 1_048_576,
                10_000,
                True,
                True,
                True,
                (exact_step,),
            )
            process._validate_result_for_request(request, exact)
            self.assertGreater(
                exact.total_process_count,
                request.steps[0].process_limit,
            )
            for accounting in (
                (20_001, 128 * 1_048_576, 10_000),
                (20_000, 128 * 1_048_576 + 1, 10_000),
            ):
                over_step = _step_result(1, accounting=accounting)
                over = replace(
                    exact,
                    cpu_time_ms=accounting[0],
                    peak_job_memory_bytes=accounting[1],
                    total_process_count=accounting[2],
                    steps=(over_step,),
                )
                with self.subTest(accounting=accounting), self.assertRaises(
                    process.RunnerProcessError
                ):
                    process._validate_result_for_request(request, over)

    @unittest.skipUnless(
        Path(sys.executable).name.casefold() == "python.exe",
        "fixed Runner bootstrap requires the Windows CPython executable",
    )
    def test_module_parent_observes_exact_argv_before_find_spec(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            layout = replace(
                _make_layout(root),
                runtime=Path(sys.executable).resolve(strict=True),
            )
            package = layout.target / "pkg"
            package.mkdir()
            (package / "__init__.py").write_text(
                "import sys\n"
                "if sys.argv != ['pkg.check', 'expected']:\n"
                "    raise SystemExit(0)\n",
                encoding="utf-8",
            )
            (package / "check.py").write_text(
                "raise SystemExit(9)\n",
                encoding="utf-8",
            )
            request = _request(
                layout,
                steps=(
                    _step(
                        mode="module",
                        entrypoint="pkg.check",
                        argv=("expected",),
                    ),
                ),
            )
            admitted = _admit(request, layout).steps[0]
            completed = subprocess.run(
                admitted.raw_argv,
                cwd=admitted.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        self.assertEqual(completed.returncode, 9)

    @unittest.skipUnless(
        Path(sys.executable).name.casefold() == "python.exe",
        "fixed Runner bootstrap requires the Windows CPython executable",
    )
    def test_module_rejects_frozen_origin_sentinel(self):
        module_spec = importlib.util.find_spec("os")
        self.assertIsNotNone(module_spec)
        assert module_spec is not None
        self.assertIn(module_spec.origin, {"built-in", "frozen"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            layout = replace(
                _make_layout(root),
                runtime=Path(sys.executable).resolve(strict=True),
            )
            request = _request(
                layout,
                steps=(
                    _step(
                        mode="module",
                        entrypoint="os",
                        argv=(),
                    ),
                ),
            )
            admitted = _admit(request, layout).steps[0]
            completed = subprocess.run(
                admitted.raw_argv,
                cwd=admitted.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        self.assertEqual(completed.returncode, 126)


if __name__ == "__main__":
    unittest.main()
