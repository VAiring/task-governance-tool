from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _verification_runner_win32 as win32  # noqa: E402
from task_governance_tool import verification_runner_process as process  # noqa: E402
from task_governance_tool import verification_runner_runtime as runtime  # noqa: E402
from task_governance_tool.verification_runner import (  # noqa: E402
    RUNNER_CONTRACT_VERSION,
)


ATTEMPT_ID = "tg_verification_runner_attempt_0123456789abcdef"


class RunnerWin32PureTests(unittest.TestCase):
    def test_limit_validation_is_closed(self):
        self.assertEqual(
            win32.JobLimits(900, 900, 2048, 32),
            win32.JobLimits(900, 900, 2048, 32),
        )
        for values in ((0, 1, 64, 1), (1, 901, 64, 1), (1, 1, 63, 1), (1, 1, 64, 33)):
            with self.subTest(values=values), self.assertRaises(win32.RunnerWin32Error):
                win32.JobLimits(*values)

    def test_close_failure_retains_handle_ownership_until_retry(self):
        kernel32 = Mock()
        kernel32.CloseHandle.side_effect = (False, True)
        provider = Mock(kernel32=kernel32)
        handle = win32.OwnedHandle(123)

        with patch.object(win32, "_apis", return_value=provider):
            with self.assertRaises(win32.RunnerWin32Error) as raised:
                handle.close()
            self.assertEqual(raised.exception.code, "sandbox_cleanup_failed")
            self.assertFalse(handle.closed)
            self.assertEqual(handle.value, 123)

            handle.close()
            self.assertTrue(handle.closed)
            handle.close()

        self.assertEqual(kernel32.CloseHandle.call_count, 2)

    def test_interrupted_close_becomes_uncertain_and_is_not_retried(self):
        kernel32 = Mock()
        kernel32.CloseHandle.side_effect = KeyboardInterrupt()
        provider = Mock(kernel32=kernel32)
        handle = win32.OwnedHandle(124)

        with patch.object(win32, "_apis", return_value=provider):
            with self.assertRaises(win32.RunnerWin32Error) as raised:
                handle.close()
            self.assertEqual(raised.exception.code, "sandbox_cleanup_failed")
            self.assertFalse(handle.closed)

            with self.assertRaises(win32.RunnerWin32Error) as repeated:
                handle.close()
            self.assertEqual(repeated.exception.code, "sandbox_cleanup_failed")

        kernel32.CloseHandle.assert_called_once()

    def test_create_job_interrupted_adoption_retires_raw_or_reports_cleanup(self):
        for close_result, expected in (
            (True, KeyboardInterrupt),
            (False, win32.RunnerWin32Error),
        ):
            with self.subTest(close_result=close_result):
                kernel32 = Mock()
                kernel32.CreateJobObjectW.return_value = 601
                kernel32.CloseHandle.return_value = close_result
                provider = Mock(kernel32=kernel32)
                with patch.object(
                    win32,
                    "_apis",
                    return_value=provider,
                ), patch.object(
                    win32.OwnedHandle,
                    "__init__",
                    side_effect=KeyboardInterrupt(),
                ):
                    with self.assertRaises(expected) as raised:
                        win32.create_job(win32.JobLimits(5, 5, 128, 2))

                if isinstance(raised.exception, win32.RunnerWin32Error):
                    self.assertEqual(
                        raised.exception.code,
                        "sandbox_cleanup_failed",
                    )
                self.assertEqual(
                    [
                        int(call.args[0].value or 0)
                        for call in kernel32.CloseHandle.call_args_list
                    ],
                    [601],
                )

    def test_return_handle_acquisition_without_raw_value_is_uncertain(self):
        kernel32 = Mock()
        acquired: list[int] = []

        def acquire_then_interrupt(*_args: object) -> int:
            acquired.append(801)
            raise KeyboardInterrupt()

        kernel32.CreateJobObjectW.side_effect = acquire_then_interrupt
        provider = Mock(kernel32=kernel32)
        with patch.object(win32, "_apis", return_value=provider):
            with self.assertRaises(win32.RunnerWin32Error) as raised:
                win32.create_job(win32.JobLimits(5, 5, 128, 2))

        self.assertEqual(acquired, [801])
        self.assertEqual(raised.exception.code, "sandbox_cleanup_failed")
        kernel32.CloseHandle.assert_not_called()

    def test_stdio_scope_retires_every_raw_at_each_interrupted_adoption(self):
        original_init = win32.OwnedHandle.__init__
        for interrupted_adoption in range(1, 9):
            with self.subTest(interrupted_adoption=interrupted_adoption):
                kernel32 = Mock()
                acquired: list[int] = []
                pipe_pairs = iter(((610, 611), (620, 621)))
                duplicate_values = iter((612, 622, 631))

                def create_pipe(read, write, *_args: object) -> bool:
                    read_value, write_value = next(pipe_pairs)
                    ctypes.cast(read, ctypes.POINTER(win32.HANDLE)).contents.value = (
                        read_value
                    )
                    ctypes.cast(write, ctypes.POINTER(win32.HANDLE)).contents.value = (
                        write_value
                    )
                    acquired.extend((read_value, write_value))
                    return True

                def duplicate_handle(*args: object) -> bool:
                    value = next(duplicate_values)
                    ctypes.cast(
                        args[3],
                        ctypes.POINTER(win32.HANDLE),
                    ).contents.value = value
                    acquired.append(value)
                    return True

                def create_file(*_args: object) -> int:
                    acquired.append(630)
                    return 630

                kernel32.CreatePipe.side_effect = create_pipe
                kernel32.DuplicateHandle.side_effect = duplicate_handle
                kernel32.CreateFileW.side_effect = create_file
                kernel32.GetCurrentProcess.return_value = 1
                kernel32.CloseHandle.return_value = True
                provider = Mock(kernel32=kernel32)
                adoption_count = 0

                def interrupt_selected_adoption(
                    handle: win32.OwnedHandle,
                    value: object,
                ) -> None:
                    nonlocal adoption_count
                    adoption_count += 1
                    if adoption_count == interrupted_adoption:
                        raise KeyboardInterrupt()
                    original_init(handle, value)

                with patch.object(
                    win32,
                    "_apis",
                    return_value=provider,
                ), patch.object(
                    win32,
                    "_make_noninheritable",
                ), patch.object(
                    win32,
                    "_prove_inheritability",
                ), patch.object(
                    win32.StdioPipes,
                    "prove_before_create",
                ), patch.object(
                    win32.OwnedHandle,
                    "__init__",
                    new=interrupt_selected_adoption,
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        win32.create_stdio_pipes()

                closed = [
                    int(call.args[0].value or 0)
                    for call in kernel32.CloseHandle.call_args_list
                ]
                self.assertCountEqual(closed, acquired)
                self.assertEqual(len(closed), len(set(closed)))

    def test_stdio_scope_attempts_every_sibling_after_close_failure(self):
        original_init = win32.OwnedHandle.__init__
        kernel32 = Mock()
        acquired: list[int] = []

        def create_pipe(read, write, *_args: object) -> bool:
            ctypes.cast(read, ctypes.POINTER(win32.HANDLE)).contents.value = 640
            ctypes.cast(write, ctypes.POINTER(win32.HANDLE)).contents.value = 641
            acquired.extend((640, 641))
            return True

        kernel32.CreatePipe.side_effect = create_pipe
        kernel32.CloseHandle.side_effect = (False, True)
        provider = Mock(kernel32=kernel32)
        adoption_count = 0

        def interrupt_second_adoption(
            handle: win32.OwnedHandle,
            value: object,
        ) -> None:
            nonlocal adoption_count
            adoption_count += 1
            if adoption_count == 2:
                raise KeyboardInterrupt()
            original_init(handle, value)

        with patch.object(
            win32,
            "_apis",
            return_value=provider,
        ), patch.object(
            win32,
            "_make_noninheritable",
        ), patch.object(
            win32.OwnedHandle,
            "__init__",
            new=interrupt_second_adoption,
        ):
            with self.assertRaises(win32.RunnerWin32Error) as raised:
                win32.create_stdio_pipes()

        self.assertEqual(raised.exception.code, "sandbox_cleanup_failed")
        closed = [
            int(call.args[0].value or 0)
            for call in kernel32.CloseHandle.call_args_list
        ]
        self.assertCountEqual(closed, acquired)
        self.assertEqual(len(closed), 2)

    def test_stdio_scope_interrupted_release_retires_every_handle_once(self):
        kernel32 = Mock()
        acquired: list[int] = []
        pipe_pairs = iter(((650, 651), (660, 661)))
        duplicate_values = iter((652, 662, 671))

        def create_pipe(read, write, *_args: object) -> bool:
            read_value, write_value = next(pipe_pairs)
            ctypes.cast(read, ctypes.POINTER(win32.HANDLE)).contents.value = (
                read_value
            )
            ctypes.cast(write, ctypes.POINTER(win32.HANDLE)).contents.value = (
                write_value
            )
            acquired.extend((read_value, write_value))
            return True

        def duplicate_handle(*args: object) -> bool:
            value = next(duplicate_values)
            ctypes.cast(
                args[3],
                ctypes.POINTER(win32.HANDLE),
            ).contents.value = value
            acquired.append(value)
            return True

        def create_file(*_args: object) -> int:
            acquired.append(670)
            return 670

        kernel32.CreatePipe.side_effect = create_pipe
        kernel32.DuplicateHandle.side_effect = duplicate_handle
        kernel32.CreateFileW.side_effect = create_file
        kernel32.GetCurrentProcess.return_value = 1
        kernel32.CloseHandle.return_value = True
        provider = Mock(kernel32=kernel32)

        with patch.object(
            win32,
            "_apis",
            return_value=provider,
        ), patch.object(
            win32,
            "_make_noninheritable",
        ), patch.object(
            win32,
            "_prove_inheritability",
        ), patch.object(
            win32.StdioPipes,
            "prove_before_create",
        ), patch.object(
            win32._HandleScope,
            "release",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                win32.create_stdio_pipes()

        closed = [
            int(call.args[0].value or 0)
            for call in kernel32.CloseHandle.call_args_list
        ]
        self.assertCountEqual(closed, acquired)
        self.assertEqual(len(closed), len(set(closed)))

    def test_poll_uses_wait_state_before_treating_259_as_terminal(self):
        kernel32 = Mock()
        provider = Mock(kernel32=kernel32)
        child = win32.SuspendedChild(
            win32.OwnedHandle(701),
            win32.OwnedHandle(702),
            17,
        )

        with patch.object(win32, "_apis", return_value=provider):
            kernel32.WaitForSingleObject.return_value = win32.WAIT_TIMEOUT
            self.assertIsNone(child.poll())
            kernel32.GetExitCodeProcess.assert_not_called()

            def terminal_259(_handle, code) -> bool:
                ctypes.cast(
                    code,
                    ctypes.POINTER(win32.DWORD),
                ).contents.value = win32.STILL_ACTIVE
                return True

            kernel32.WaitForSingleObject.return_value = win32.WAIT_OBJECT_0
            kernel32.GetExitCodeProcess.side_effect = terminal_259
            self.assertEqual(child.poll(), 259)

    def test_post_create_partial_wrap_retires_owned_and_raw_handles_once(self):
        class AttributeList:
            def __init__(self, _count: int) -> None:
                self.pointer = win32.LPVOID(1)

            def add(self, *_args: object) -> None:
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        original_init = win32.OwnedHandle.__init__

        for close_results, expected_code in (
            ((True, True), "sandbox_boundary_violation"),
            ((True, False), "sandbox_cleanup_failed"),
        ):
            with self.subTest(expected_code=expected_code):
                job = win32.NativeJob(
                    win32.OwnedHandle(401),
                    win32.JobLimits(5, 5, 128, 2),
                )
                stdio = win32.StdioPipes(
                    win32.OwnedHandle(501),
                    win32.OwnedHandle(502),
                    win32.OwnedHandle(503),
                    win32.OwnedHandle(504),
                    win32.OwnedHandle(505),
                )
                kernel32 = Mock()

                def create_process(*args: object) -> bool:
                    process_info = ctypes.cast(
                        args[-1],
                        ctypes.POINTER(win32._PROCESS_INFORMATION),
                    ).contents
                    process_info.hProcess = 701
                    process_info.hThread = 702
                    process_info.dwProcessId = 17
                    return True

                kernel32.CreateProcessW.side_effect = create_process
                kernel32.CloseHandle.side_effect = close_results
                provider = Mock(kernel32=kernel32)
                constructor_calls = 0

                def interrupt_second_handle(
                    handle: win32.OwnedHandle,
                    value: object,
                ) -> None:
                    nonlocal constructor_calls
                    constructor_calls += 1
                    if constructor_calls == 2:
                        raise KeyboardInterrupt()
                    original_init(handle, value)

                with tempfile.TemporaryDirectory() as temporary, patch.object(
                    win32,
                    "_AttributeList",
                    AttributeList,
                ), patch.object(
                    win32,
                    "_apis",
                    return_value=provider,
                ), patch.object(
                    win32.NativeJob,
                    "prove_configuration",
                ), patch.object(
                    win32.StdioPipes,
                    "prove_before_create",
                ), patch.object(
                    win32.NativeJob,
                    "terminate",
                ) as terminate, patch.object(
                    win32.OwnedHandle,
                    "__init__",
                    new=interrupt_second_handle,
                ):
                    with self.assertRaises(win32.RunnerWin32Error) as raised:
                        win32.create_suspended_child(
                            application=Path(sys.executable).resolve(),
                            command_line="python.exe",
                            environment_block="SystemRoot=C:\\Windows\0\0",
                            cwd=Path(temporary).resolve(),
                            job=job,
                            stdio=stdio,
                        )

                self.assertEqual(raised.exception.code, expected_code)
                self.assertTrue(raised.exception.after_create)
                terminate.assert_called_once_with()
                self.assertEqual(constructor_calls, 2)
                self.assertEqual(
                    [
                        int(call.args[0].value or 0)
                        for call in kernel32.CloseHandle.call_args_list
                    ],
                    [702, 701],
                )

    def test_attribute_list_exit_failure_retires_both_wrapped_handles(self):
        class FailingAttributeList:
            def __init__(self, _count: int) -> None:
                self.pointer = win32.LPVOID(1)

            def add(self, *_args: object) -> None:
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                raise KeyboardInterrupt()

        job = win32.NativeJob(
            win32.OwnedHandle(401),
            win32.JobLimits(5, 5, 128, 2),
        )
        stdio = win32.StdioPipes(
            win32.OwnedHandle(501),
            win32.OwnedHandle(502),
            win32.OwnedHandle(503),
            win32.OwnedHandle(504),
            win32.OwnedHandle(505),
        )
        kernel32 = Mock()

        def create_process(*args: object) -> bool:
            process_info = ctypes.cast(
                args[-1],
                ctypes.POINTER(win32._PROCESS_INFORMATION),
            ).contents
            process_info.hProcess = 701
            process_info.hThread = 702
            process_info.dwProcessId = 17
            return True

        kernel32.CreateProcessW.side_effect = create_process
        kernel32.CloseHandle.return_value = True
        provider = Mock(kernel32=kernel32)

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            win32,
            "_AttributeList",
            FailingAttributeList,
        ), patch.object(
            win32,
            "_apis",
            return_value=provider,
        ), patch.object(
            win32.NativeJob,
            "prove_configuration",
        ), patch.object(
            win32.StdioPipes,
            "prove_before_create",
        ), patch.object(
            win32.NativeJob,
            "contains",
            return_value=True,
        ) as contains, patch.object(
            win32.NativeJob,
            "terminate",
        ) as terminate:
            with self.assertRaises(win32.RunnerWin32Error) as raised:
                win32.create_suspended_child(
                    application=Path(sys.executable).resolve(),
                    command_line="python.exe",
                    environment_block="SystemRoot=C:\\Windows\0\0",
                    cwd=Path(temporary).resolve(),
                    job=job,
                    stdio=stdio,
                )

        self.assertEqual(raised.exception.code, "sandbox_boundary_violation")
        self.assertTrue(raised.exception.after_create)
        contains.assert_called_once()
        terminate.assert_called_once_with()
        self.assertEqual(
            [
                int(call.args[0].value or 0)
                for call in kernel32.CloseHandle.call_args_list
            ],
            [702, 701],
        )


@unittest.skipUnless(os.name == "nt", "native Windows Runner provider")
class RunnerWin32NativeTests(unittest.TestCase):
    def _write_request_layout(
        self,
        root: Path,
        source: str,
    ) -> tuple[Path, Path]:
        attempt = root / ATTEMPT_ID
        target = attempt / "target"
        scratch = attempt / "scratch"
        (target / "checks").mkdir(parents=True)
        scratch.mkdir()
        for name in ("tmp", "home", "local", "roaming"):
            (scratch / name).mkdir()
        (target / "checks" / "run.py").write_text(source, encoding="utf-8")
        return target, scratch

    def _request(
        self,
        *,
        executable: Path,
        target: Path,
        scratch: Path,
        limits: win32.JobLimits | None = None,
        cancel_signal: process.RunnerCancelSignal | None = None,
    ) -> process.RunnerProcessRequestV1:
        exact_limits = limits or win32.JobLimits(5, 5, 128, 4)
        return process.RunnerProcessRequestV1(
            version=RUNNER_CONTRACT_VERSION,
            attempt_id=ATTEMPT_ID,
            executable=executable,
            materialized_root=target,
            scratch_root=scratch,
            clean_environment=process.build_clean_environment(
                win32.verified_windows_directory(),
                scratch,
            ),
            steps=(
                process.RunnerProcessStepV1(
                    ordinal=1,
                    step_id="native",
                    mode="script",
                    entrypoint="checks/run.py",
                    argv=(),
                    cwd=".",
                    shell=False,
                    path_lookup=False,
                    timeout_seconds=exact_limits.timeout_seconds,
                    cpu_seconds=exact_limits.cpu_seconds,
                    memory_mib=exact_limits.memory_mib,
                    process_limit=exact_limits.process_limit,
                    output_byte_limit=process.MAX_OUTPUT_BYTES,
                ),
            ),
            cancel_signal=cancel_signal or process.RunnerCancelSignal(),
        )

    def _environment_block(self, scratch: Path) -> str:
        entries = process.build_clean_environment(
            win32.verified_windows_directory(),
            scratch,
        )
        return "".join(f"{key}={value}\0" for key, value in entries) + "\0"

    def _run_script(
        self,
        source: str,
        *,
        limits: win32.JobLimits | None = None,
        cancel_after_launch: bool = False,
    ):
        self.assertEqual(Path(sys.executable).name.casefold(), "python.exe")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            marker = root / ATTEMPT_ID / "scratch" / "cancel-started.txt"
            if cancel_after_launch:
                source = (
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('started', encoding='utf-8')\n"
                    + source
                )
            target, scratch = self._write_request_layout(root, source)
            signal = process.RunnerCancelSignal()
            stop = threading.Event()
            controller = None
            if cancel_after_launch:
                def request_after_marker() -> None:
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline and not stop.is_set():
                        if marker.exists():
                            signal.request()
                            return
                        stop.wait(0.01)

                controller = threading.Thread(target=request_after_marker, daemon=True)
                controller.start()
            try:
                result = process.run_process_request(
                    self._request(
                        executable=Path(sys.executable).resolve(),
                        target=target,
                        scratch=scratch,
                        limits=limits,
                        cancel_signal=signal,
                    )
                )
            finally:
                stop.set()
                if controller is not None:
                    controller.join(1.0)
            if cancel_after_launch:
                self.assertTrue(marker.exists())
                self.assertIsNotNone(controller)
                self.assertFalse(controller.is_alive())
            return result

    def assert_process_zero(self, result) -> None:
        self.assertTrue(result.process_zero)
        self.assertTrue(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)
        if result.launch_state == "launched":
            self.assertEqual(len(result.steps), 1)

    def test_fixed_runtime_lease_is_held_through_real_request_and_fixture_rename_after_close(self):
        self.assertEqual(Path(sys.executable).name.casefold(), "python.exe")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target, scratch = self._write_request_layout(
                root,
                "raise SystemExit(0)\n",
            )
            lease = runtime.RunnerFixedExecutableLease(target, scratch)
            with lease as executable:
                self.assertFalse(lease.closed)
                self.assertEqual(lease.executable, executable)
                result = process.run_process_request(
                    self._request(
                        executable=executable,
                        target=target,
                        scratch=scratch,
                    )
                )
                self.assertEqual((result.outcome, result.reason), ("pass", None))
                self.assert_process_zero(result)
                self.assertFalse(lease.closed)

            self.assertTrue(lease.closed)
            runtime_root = root / "runtime-fixture"
            runtime_root.mkdir()
            fixture_executable = runtime_root / "python.exe"
            renamed_executable = runtime_root / "python-renamed.exe"
            fixture_executable.write_bytes(b"fixture")

            real_kernel = runtime._kernel32()

            class FixtureKernel:
                def GetModuleFileNameW(self, _module, buffer, capacity):
                    observed = str(fixture_executable)
                    if len(observed) >= int(capacity):
                        return int(capacity)
                    buffer.value = observed
                    return len(observed)

                def __getattr__(self, name):
                    return getattr(real_kernel, name)

            fixture_lease = runtime.RunnerFixedExecutableLease(target, scratch)
            with patch.object(
                runtime,
                "_kernel32",
                return_value=FixtureKernel(),
            ), patch.object(runtime.sys, "executable", str(fixture_executable)):
                with fixture_lease as executable:
                    self.assertEqual(executable, fixture_executable)
                    self.assertFalse(fixture_lease.closed)
                    with self.assertRaises(OSError):
                        os.replace(fixture_executable, renamed_executable)

            self.assertTrue(fixture_lease.closed)
            self.assertTrue(fixture_executable.is_file())
            self.assertFalse(renamed_executable.exists())
            os.replace(fixture_executable, renamed_executable)
            self.assertTrue(renamed_executable.is_file())

    def test_exact_amd64_abi_job_and_stdio_handles(self):
        self.assertEqual(
            win32.abi_layout(),
            {
                "pointer_size": 8,
                "startupinfo_size": 104,
                "startupinfoex_size": 112,
                "process_information_size": 24,
                "job_basic_limit_size": 64,
                "job_extended_limit_size": 144,
                "job_accounting_size": 48,
            },
        )
        self.assertFalse(hasattr(win32, "EXACT_JOB_UI_FLAGS"))
        job = win32.create_job(win32.JobLimits(5, 5, 128, 2))
        try:
            job.prove_configuration()
            accounting = job.accounting()
            self.assertEqual(accounting.total_processes, 0)
            self.assertIsNone(job.limit_violation_reason(accounting))
            self.assertEqual(
                job.limit_violation_reason(win32.JobAccounting(50_000_000, 0, 1, 0)),
                "cpu_limit",
            )
            self.assertEqual(
                job.limit_violation_reason(
                    win32.JobAccounting(0, 128 * 1_048_576, 1, 0)
                ),
                "memory_limit",
            )
        finally:
            job.close()

        pipes = win32.create_stdio_pipes()
        try:
            pipes.prove_before_create()
            self.assertEqual(len(set(pipes.inherited_values)), 3)
        finally:
            pipes.close_child_ends()
            pipes.close_parent_ends()

    def test_real_pass_nonzero_and_output_limit_are_closed(self):
        secret = "TG_M242_RAW_OUTPUT_MUST_NOT_SURVIVE"
        passed = self._run_script(f"print({secret!r})\n")
        self.assertEqual((passed.outcome, passed.reason), ("pass", None))
        self.assertNotIn(secret, repr(passed))
        self.assert_process_zero(passed)

        nonzero = self._run_script("raise SystemExit(7)\n")
        self.assertEqual((nonzero.outcome, nonzero.reason), ("fail", "step_nonzero"))
        self.assert_process_zero(nonzero)

        terminal_259 = self._run_script("import os\nos._exit(259)\n")
        self.assertEqual(
            (
                terminal_259.outcome,
                terminal_259.reason,
                terminal_259.launch_state,
            ),
            ("fail", "step_nonzero", "launched"),
        )
        self.assert_process_zero(terminal_259)

        output = self._run_script(
            "import sys\n"
            f"sys.stdout.buffer.write(b'x' * {process.MAX_OUTPUT_BYTES + 65_536})\n"
            "sys.stdout.flush()\n"
        )
        self.assertEqual(
            (output.outcome, output.reason),
            ("output_rejected", "output_limit"),
        )
        self.assert_process_zero(output)

    def test_real_child_receives_exact_credential_clean_environment(self):
        exact_keys = (
            "APPDATA",
            "HOME",
            "LOCALAPPDATA",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONNOUSERSITE",
            "PYTHONUTF8",
            "SystemRoot",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "WINDIR",
        )
        source = (
            "import os\n"
            "from pathlib import Path\n"
            f"exact_keys = {exact_keys!r}\n"
            "values = {key.upper(): value for key, value in os.environ.items()}\n"
            "scratch = Path(values.get('TEMP', '')).parent\n"
            "expected_paths = {\n"
            "    'APPDATA': scratch / 'roaming',\n"
            "    'HOME': scratch / 'home',\n"
            "    'LOCALAPPDATA': scratch / 'local',\n"
            "    'TEMP': scratch / 'tmp',\n"
            "    'TMP': scratch / 'tmp',\n"
            "    'USERPROFILE': scratch / 'home',\n"
            "}\n"
            "valid = (\n"
            "    len(values) == len(exact_keys)\n"
            "    and set(values) == {key.upper() for key in exact_keys}\n"
            "    and all(values.get(key) == '1' for key in (\n"
            "        'PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE', 'PYTHONUTF8'\n"
            "    ))\n"
            "    and all(Path(values.get(key, '')) == expected for key, expected in expected_paths.items())\n"
            "    and values.get('SYSTEMROOT') == values.get('WINDIR')\n"
            "    and Path(values.get('SYSTEMROOT', '')).is_absolute()\n"
            ")\n"
            "raise SystemExit(0 if valid else 9)\n"
        )
        result = self._run_script(source)
        self.assertEqual((result.outcome, result.reason), ("pass", None))
        self.assert_process_zero(result)

    def test_real_output_limit_counts_stdout_and_stderr_together(self):
        per_stream = process.MAX_OUTPUT_BYTES // 2 + 32_768
        result = self._run_script(
            "import sys\n"
            f"payload = b'x' * {per_stream}\n"
            "sys.stdout.buffer.write(payload)\n"
            "sys.stdout.flush()\n"
            "sys.stderr.buffer.write(payload)\n"
            "sys.stderr.flush()\n"
        )
        self.assertEqual(
            (result.outcome, result.reason),
            ("output_rejected", "output_limit"),
        )
        self.assert_process_zero(result)

    def test_real_timeout_cancel_and_descendant_are_retired(self):
        timeout = self._run_script(
            "import time\ntime.sleep(30)\n",
            limits=win32.JobLimits(1, 5, 128, 4),
        )
        self.assertEqual((timeout.outcome, timeout.reason), ("timeout", "timeout"))
        self.assert_process_zero(timeout)

        cancelled = self._run_script(
            "import time\ntime.sleep(30)\n",
            cancel_after_launch=True,
        )
        self.assertEqual(
            (cancelled.outcome, cancelled.reason),
            ("cancelled", "cancelled"),
        )
        self.assert_process_zero(cancelled)

        descendant = self._run_script(
            "import subprocess,sys\n"
            "subprocess.Popen((sys.executable, '-I', '-c', "
            "'import time;time.sleep(30)'))\n",
            limits=win32.JobLimits(1, 5, 128, 4),
        )
        self.assertEqual(
            (descendant.outcome, descendant.reason),
            ("timeout", "timeout"),
        )
        self.assert_process_zero(descendant)

    def test_real_cpu_limit_returns_closed_resource_result(self):
        limited = self._run_script(
            "while True:\n    pass\n",
            limits=win32.JobLimits(30, 1, 128, 4),
        )
        self.assertEqual(
            (limited.outcome, limited.reason, limited.launch_state),
            ("resource_exceeded", "cpu_limit", "launched"),
        )
        self.assert_process_zero(limited)

    def test_real_job_precedes_code_and_process_limit_denies_n_plus_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            scratch = root / "scratch"
            scratch.mkdir()
            for name in ("tmp", "home", "local", "roaming"):
                (scratch / name).mkdir()
            marker = root / "started.txt"
            source = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('started', encoding='utf-8')\n"
                "import time\ntime.sleep(30)\n"
            )
            command_line = process.quote_windows_argv(
                (str(Path(sys.executable)), "-I", "-B", "-c", source)
            )
            environment_block = self._environment_block(scratch)
            job = win32.create_job(win32.JobLimits(10, 10, 128, 1))
            first_pipes = win32.create_stdio_pipes()
            second_pipes = None
            first_child = None
            second_child = None
            accounting = None
            try:
                first_child = win32.create_suspended_child(
                    application=Path(sys.executable),
                    command_line=command_line,
                    environment_block=environment_block,
                    cwd=root,
                    job=job,
                    stdio=first_pipes,
                )
                first_pipes.close_child_ends()
                self.assertTrue(job.contains(first_child.process))
                time.sleep(0.05)
                self.assertFalse(marker.exists())
                first_child.resume_once()
                deadline = time.monotonic() + 3.0
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                self.assertGreaterEqual(job.accounting().active_processes, 1)

                second_pipes = win32.create_stdio_pipes()
                try:
                    second_child = win32.create_suspended_child(
                        application=Path(sys.executable),
                        command_line=command_line,
                        environment_block=environment_block,
                        cwd=root,
                        job=job,
                        stdio=second_pipes,
                    )
                except win32.RunnerWin32Error as error:
                    self.assertEqual(error.code, "process_create_failed")
                    self.assertFalse(error.after_create)
                else:
                    self.fail("the active-process Job limit admitted N+1")

                job.terminate()
                accounting = job.wait_for_zero(deadline=time.monotonic() + 5.0)
                self.assertEqual(accounting.active_processes, 0)
                self.assertGreaterEqual(accounting.total_processes, 1)
            finally:
                if first_child is not None and accounting is None:
                    job.terminate()
                    job.wait_for_zero(deadline=time.monotonic() + 5.0)
                for child in (second_child, first_child):
                    if child is not None:
                        child.close()
                for pipes in (second_pipes, first_pipes):
                    if pipes is not None:
                        pipes.close_child_ends()
                        pipes.close_parent_ends()
                job.close()

    def test_real_non_executable_pe_create_failure_leaves_job_zero_and_closes_handles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            scratch = root / "scratch"
            scratch.mkdir()
            for name in ("tmp", "home", "local", "roaming"):
                (scratch / name).mkdir()
            invalid_executable = root / "python.exe"
            shutil.copy2(
                win32.verified_windows_directory() / "System32" / "kernel32.dll",
                invalid_executable,
            )
            job = win32.create_job(win32.JobLimits(5, 5, 128, 1))
            pipes = win32.create_stdio_pipes()
            handles = (
                job.handle,
                pipes.stdin_child,
                pipes.stdout_child,
                pipes.stderr_child,
                pipes.stdout_parent,
                pipes.stderr_parent,
            )
            try:
                with self.assertRaises(win32.RunnerWin32Error) as raised:
                    win32.create_suspended_child(
                        application=invalid_executable,
                        command_line=process.quote_windows_argv(
                            (str(invalid_executable),)
                        ),
                        environment_block=self._environment_block(scratch),
                        cwd=root,
                        job=job,
                        stdio=pipes,
                    )
                self.assertEqual(raised.exception.code, "process_create_failed")
                self.assertFalse(raised.exception.after_create)
                accounting = job.accounting()
                self.assertEqual(accounting.total_processes, 0)
                self.assertEqual(accounting.active_processes, 0)
            finally:
                pipes.close_child_ends()
                pipes.close_parent_ends()
                job.close()

            self.assertTrue(all(handle.closed for handle in handles))

    def test_synthetic_native_faults_fail_closed_after_create(self):
        with patch.object(
            win32,
            "create_suspended_child",
            side_effect=win32.RunnerWin32Error("process_create_failed"),
        ):
            create_failed = self._run_script("raise SystemExit(0)\n")
        self.assertEqual(
            (create_failed.outcome, create_failed.reason, create_failed.launch_state),
            ("blocked_prelaunch", "process_create_failed", "no_launch"),
        )
        self.assert_process_zero(create_failed)

        with patch.object(
            win32,
            "create_suspended_child",
            side_effect=win32.RunnerWin32Error(
                "sandbox_cleanup_failed",
                after_create=True,
            ),
        ):
            cleanup_failed = self._run_script("raise SystemExit(0)\n")
        self.assertEqual(
            (
                cleanup_failed.outcome,
                cleanup_failed.reason,
                cleanup_failed.launch_state,
            ),
            ("cleanup_failed", "process_cleanup_failed", "launched"),
        )
        self.assertFalse(cleanup_failed.process_zero)
        self.assertFalse(cleanup_failed.handles_closed)
        self.assertTrue(cleanup_failed.raw_output_discarded)

        with patch.object(
            win32.SuspendedChild,
            "resume_once",
            side_effect=win32.RunnerWin32Error("process_resume_failed"),
        ):
            resume_failed = self._run_script("raise SystemExit(0)\n")
        self.assertEqual(
            (resume_failed.outcome, resume_failed.reason),
            ("process_error", "process_resume_failed"),
        )
        self.assert_process_zero(resume_failed)

        with patch.object(
            win32,
            "read_pipe_chunk",
            side_effect=win32.RunnerWin32Error("pipe_drain_failed"),
        ):
            drain_failed = self._run_script("import time\ntime.sleep(30)\n")
        self.assertEqual(
            (drain_failed.outcome, drain_failed.reason),
            ("process_error", "pipe_drain_failed"),
        )
        self.assert_process_zero(drain_failed)
