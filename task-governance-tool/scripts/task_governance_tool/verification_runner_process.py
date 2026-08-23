"""Shell-free process orchestration for verification Runner plan steps.

The module accepts already-owned, already-materialized paths.  It does not
discover a project, read or write SQLite, choose a fallback, or retain child
output.  Native process mechanics are delegated exclusively to the thin
Windows adapter.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from task_governance_tool import _verification_runner_win32 as _win32
from task_governance_tool._verification_runner_win32 import (
    JobAccounting,
    JobLimits,
    RunnerWin32Error,
)
from task_governance_tool.verification_runner import (
    RUNNER_CONTRACT_VERSION,
    RUNNER_EXECUTABLE_ID,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_MAX_OUTPUT_BYTES,
)


MAX_OUTPUT_BYTES = RUNNER_MAX_OUTPUT_BYTES
EXECUTABLE_ID = RUNNER_EXECUTABLE_ID
MAX_COMMAND_LINE_UTF16_UNITS = 24_576

_MODULE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,15}$"
)
_SCRIPT_COMPONENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_CLOSED_OUTCOMES = frozenset(
    {
        "not_run",
        "blocked_prelaunch",
        "pass",
        "fail",
        "timeout",
        "cancelled",
        "resource_exceeded",
        "sandbox_violation",
        "output_rejected",
        "process_error",
        "controller_interrupted",
        "sandbox_cleanup_failed",
    }
)
_CLOSED_REASONS = frozenset(
    {
        "sandbox_unavailable",
        "sandbox_setup_failed",
        "sandbox_boundary_violation",
        "step_nonzero",
        "timeout",
        "cancelled",
        "cpu_limit",
        "memory_limit",
        "process_limit",
        "output_limit",
        "process_create_failed",
        "process_resume_failed",
        "process_wait_failed",
        "pipe_drain_failed",
        "job_state_unproved",
        "controller_interrupted",
        "sandbox_cleanup_failed",
    }
)


class RunnerProcessError(RuntimeError):
    def __init__(self, code: str = "sandbox_setup_failed") -> None:
        if code not in _CLOSED_REASONS:
            code = "sandbox_setup_failed"
        super().__init__("verification Runner process boundary failed closed")
        self.code = code


def _fail(code: str = "sandbox_setup_failed") -> NoReturn:
    raise RunnerProcessError(code)


def _valid_text(value: object, *, maximum_utf8: int = 4096) -> bool:
    return (
        type(value) is str
        and "\0" not in value
        and all(ord(character) >= 0x20 and ord(character) != 0x7F for character in value)
        and len(value.encode("utf-8")) <= maximum_utf8
    )


def _valid_relative_components(value: str, *, script: bool) -> bool:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    components = value.split("/")
    return (
        1 <= len(components) <= 32
        and 1 <= len(encoded) <= 512
        and all(
            component not in {".", ".."}
            and _SCRIPT_COMPONENT.fullmatch(component) is not None
            for component in components
        )
        and (not script or components[-1].endswith(".py"))
    )


@dataclass(frozen=True)
class ProcessStep:
    step_id: str
    mode: str
    entrypoint: str
    argv: tuple[str, ...]
    cwd: str
    limits: JobLimits

    def __post_init__(self) -> None:
        if (
            type(self.step_id) is not str
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", self.step_id) is None
            or self.mode not in {"script", "module"}
            or type(self.entrypoint) is not str
            or (
                self.mode == "script"
                and not _valid_relative_components(self.entrypoint, script=True)
            )
            or (
                self.mode == "module"
                and (
                    len(self.entrypoint.encode("ascii", "ignore")) != len(self.entrypoint)
                    or len(self.entrypoint) > 512
                    or _MODULE.fullmatch(self.entrypoint) is None
                )
            )
            or type(self.argv) is not tuple
            or len(self.argv) > 64
            or any(not _valid_text(argument) for argument in self.argv)
            or type(self.cwd) is not str
            or not (
                self.cwd == "."
                or _valid_relative_components(self.cwd, script=False)
            )
            or not isinstance(self.limits, JobLimits)
        ):
            _fail()


@dataclass(frozen=True)
class CleanEnvironment:
    entries: tuple[tuple[str, str], ...]
    block: str

    def __post_init__(self) -> None:
        if (
            len(self.entries) != 11
            or len({key.casefold() for key, _value in self.entries}) != 11
            or not self.block.endswith("\0\0")
            or self.block != "".join(f"{key}={value}\0" for key, value in self.entries) + "\0"
        ):
            _fail()


@dataclass(frozen=True)
class StepProcessResult:
    ordinal: int
    outcome: str
    reason: str | None
    launch_state: str
    accounting: JobAccounting | None

    def __post_init__(self) -> None:
        if (
            type(self.ordinal) is not int
            or self.ordinal <= 0
            or self.outcome not in _CLOSED_OUTCOMES
            or (self.reason is not None and self.reason not in _CLOSED_REASONS)
            or self.launch_state not in {"no_launch", "launched"}
            or (self.launch_state == "no_launch" and self.accounting is not None)
        ):
            _fail()


@dataclass(frozen=True)
class ProcessRunResult:
    outcome: str
    reason: str | None
    launch_state: str
    failed_step_ordinal: int | None
    duration_ms: int
    cpu_time_ms: int | None
    peak_job_memory_bytes: int | None
    total_process_count: int | None
    steps: tuple[StepProcessResult, ...]

    def __post_init__(self) -> None:
        if (
            self.outcome not in _CLOSED_OUTCOMES
            or (self.reason is not None and self.reason not in _CLOSED_REASONS)
            or self.launch_state not in {"no_launch", "launched"}
            or type(self.duration_ms) is not int
            or self.duration_ms < 0
            or (self.failed_step_ordinal is not None and self.failed_step_ordinal <= 0)
            or any(
                value is not None and (type(value) is not int or value < 0)
                for value in (
                    self.cpu_time_ms,
                    self.peak_job_memory_bytes,
                    self.total_process_count,
                )
            )
        ):
            _fail()


def quote_windows_argument(argument: str) -> str:
    """Quote one argument as the inverse consumed by CommandLineToArgvW."""

    if type(argument) is not str or "\0" in argument or len(argument.encode("utf-8")) > 65_536:
        _fail()
    if argument and not any(character in ' \t"' for character in argument):
        return argument
    quoted: list[str] = ['"']
    backslashes = 0
    for character in argument:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"':
            quoted.append("\\" * (backslashes * 2 + 1))
            quoted.append('"')
        else:
            quoted.append("\\" * backslashes)
            quoted.append(character)
        backslashes = 0
    quoted.append("\\" * (backslashes * 2))
    quoted.append('"')
    return "".join(quoted)


def quote_windows_argv(argv: tuple[str, ...]) -> str:
    if type(argv) is not tuple or not argv or len(argv) > 80:
        _fail()
    command_line = " ".join(quote_windows_argument(argument) for argument in argv)
    # CreateProcessW's contract is counted in UTF-16 code units, excluding the
    # terminating NUL.
    if len(command_line.encode("utf-16-le")) // 2 > MAX_COMMAND_LINE_UTF16_UNITS:
        _fail()
    return command_line


_ENVIRONMENT_KEYS = (
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


def build_clean_environment(
    windows_directory: str | os.PathLike[str],
    scratch_root: str | os.PathLike[str],
) -> CleanEnvironment:
    windows = Path(windows_directory)
    scratch = Path(scratch_root)
    if not windows.is_absolute() or not scratch.is_absolute() or not windows.is_dir() or not scratch.is_dir():
        _fail()
    try:
        windows_value = str(windows.resolve(strict=True))
        scratch_value = scratch.resolve(strict=True)
    except OSError:
        _fail()
    try:
        expected_windows = _win32.verified_windows_directory().resolve(strict=True)
    except (OSError, RunnerWin32Error):
        _fail()
    if os.path.normcase(windows_value) != os.path.normcase(str(expected_windows)):
        _fail()
    directories = {
        "TEMP": scratch_value / "tmp",
        "TMP": scratch_value / "tmp",
        "HOME": scratch_value / "home",
        "USERPROFILE": scratch_value / "home",
        "LOCALAPPDATA": scratch_value / "local",
        "APPDATA": scratch_value / "roaming",
    }
    for directory in set(directories.values()):
        try:
            attributes = int(getattr(directory.lstat(), "st_file_attributes", 0))
        except OSError:
            _fail()
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            _fail()
    values = {
        "SystemRoot": windows_value,
        "WINDIR": windows_value,
        **{key: str(value) for key, value in directories.items()},
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
    }
    if set(values) != set(_ENVIRONMENT_KEYS):
        _fail()
    entries = tuple(sorted(values.items(), key=lambda item: item[0].casefold()))
    block = "".join(f"{key}={value}\0" for key, value in entries) + "\0"
    return CleanEnvironment(entries, block)


FIXED_BOOTSTRAP = (
    "import importlib.util,os,runpy,sys\n"
    "_root,_cwd,_mode,_resolved,_display,*_args=sys.argv[1:]\n"
    "_root=os.path.abspath(_root);_cwd=os.path.abspath(_cwd)\n"
    "_base=os.path.abspath(sys.base_prefix)\n"
    "def _under(r,p):\n"
    " try:return os.path.normcase(os.path.commonpath((r,os.path.abspath(p))))==os.path.normcase(r)\n"
    " except ValueError:return False\n"
    "_stdlib=[p for p in sys.path if p and _under(_base,p)]\n"
    "sys.path[:]=[_root,*_stdlib]\n"
    "os.chdir(_cwd);sys.argv=[_display,*_args]\n"
    "if _mode=='module':\n"
    " _spec=importlib.util.find_spec(_resolved)\n"
    " if _spec is None or not _spec.origin or not _under(_root,_spec.origin):raise SystemExit(126)\n"
    "runpy.run_path(_resolved,run_name='__main__') if _mode=='script' else "
    "runpy.run_module(_resolved,run_name='__main__',alter_sys=False)\n"
)


def _contained_path(root: Path, relative: str, *, directory: bool) -> Path:
    candidate = root if relative == "." else root.joinpath(*relative.split("/"))
    current = root
    components = () if relative == "." else tuple(relative.split("/"))
    for component in (None, *components):
        if component is not None:
            current = current / component
        try:
            stat_result = current.lstat()
        except OSError:
            _fail()
        if current.is_symlink() or int(
            getattr(stat_result, "st_file_attributes", 0)
        ) & _FILE_ATTRIBUTE_REPARSE_POINT:
            _fail()
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError:
        _fail()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        _fail()
    if resolved.is_symlink() or (directory and not resolved.is_dir()) or (not directory and not resolved.is_file()):
        _fail()
    return resolved


def build_python_argv(
    runtime_executable: str | os.PathLike[str],
    target_root: str | os.PathLike[str],
    step: ProcessStep,
) -> tuple[tuple[str, ...], Path]:
    if not isinstance(step, ProcessStep):
        _fail()
    runtime = Path(runtime_executable)
    target = Path(target_root)
    if (
        not runtime.is_absolute()
        or not runtime.is_file()
        or runtime.name.casefold() != "python.exe"
        or not target.is_absolute()
        or not target.is_dir()
    ):
        _fail()
    cwd = _contained_path(target, step.cwd, directory=True)
    if step.mode == "script":
        resolved_entry = str(_contained_path(target, step.entrypoint, directory=False))
    else:
        resolved_entry = step.entrypoint
    raw = (
        str(runtime),
        "-I",
        "-B",
        "-X",
        "utf8",
        "-c",
        FIXED_BOOTSTRAP,
        str(target.resolve(strict=True)),
        str(cwd),
        step.mode,
        resolved_entry,
        step.entrypoint,
        *step.argv,
    )
    # Enforce the command-line contract here; callers receive raw argv only
    # after its unique serialization has proved bounded.
    quote_windows_argv(raw)
    return raw, cwd


class _DiscardingDrain:
    __slots__ = ("_handles", "_limit", "_lock", "_count", "overflow", "failed", "_threads")

    def __init__(self, handles: tuple[_win32.OwnedHandle, _win32.OwnedHandle], limit: int) -> None:
        self._handles = handles
        self._limit = limit
        self._lock = threading.Lock()
        self._count = 0
        self.overflow = threading.Event()
        self.failed = threading.Event()
        self._threads = tuple(
            threading.Thread(target=self._drain, args=(handle,), daemon=True)
            for handle in handles
        )

    def _drain(self, handle: _win32.OwnedHandle) -> None:
        try:
            while True:
                chunk = _win32.read_pipe_chunk(handle)
                if chunk is None:
                    return
                size = len(chunk)
                del chunk
                with self._lock:
                    self._count += size
                    if self._count > self._limit:
                        self.overflow.set()
        except BaseException:
            self.failed.set()

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    def join(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            try:
                thread.join(max(0.0, deadline - time.monotonic()))
            except RuntimeError:
                return False
        return all(not thread.is_alive() for thread in self._threads)


def _step_result(
    ordinal: int,
    outcome: str,
    reason: str | None,
    launch_state: str,
    accounting: JobAccounting | None,
) -> StepProcessResult:
    return StepProcessResult(ordinal, outcome, reason, launch_state, accounting)


def _execute_step(
    *,
    ordinal: int,
    step: ProcessStep,
    runtime_executable: Path,
    target_root: Path,
    environment: CleanEnvironment,
    cancel_requested: Callable[[], bool],
) -> StepProcessResult:
    job = None
    pipes = None
    child = None
    drain = None
    launched = False
    terminal: StepProcessResult | None = None
    cleanup_error = False
    try:
        job = _win32.create_job(step.limits)
        pipes = _win32.create_stdio_pipes()
        raw_argv, cwd = build_python_argv(runtime_executable, target_root, step)
        command_line = quote_windows_argv(raw_argv)
        child = _win32.create_suspended_child(
            application=runtime_executable,
            command_line=command_line,
            environment_block=environment.block,
            cwd=cwd,
            job=job,
            stdio=pipes,
        )
        launched = True
        pipes.close_child_ends()
        if not job.contains(child.process):
            _fail("job_state_unproved")
        drain = _DiscardingDrain((pipes.stdout_parent, pipes.stderr_parent), MAX_OUTPUT_BYTES)
        drain.start()
        child.resume_once()
        deadline = time.monotonic() + step.limits.timeout_seconds
        forced: tuple[str, str] | None = None
        while True:
            if drain.failed.is_set():
                forced = ("process_error", "pipe_drain_failed")
                break
            if drain.overflow.is_set():
                forced = ("output_rejected", "output_limit")
                break
            try:
                cancelled = cancel_requested()
            except BaseException:
                forced = ("controller_interrupted", "controller_interrupted")
                break
            if type(cancelled) is not bool:
                forced = ("controller_interrupted", "controller_interrupted")
                break
            if cancelled:
                forced = ("cancelled", "cancelled")
                break
            if time.monotonic() >= deadline:
                forced = ("timeout", "timeout")
                break
            if child.wait(10):
                accounting = job.accounting()
                if accounting.active_processes == 0:
                    break
            # A completed primary process may leave descendants.  The same
            # monotonic deadline remains authoritative until the whole Job is
            # empty and both output handles can reach EOF.
        if forced is not None:
            job.terminate()
            accounting = job.wait_for_zero(deadline=time.monotonic() + 5.0)
            if not drain.join(5.0):
                _fail("pipe_drain_failed")
            terminal = _step_result(
                ordinal, forced[0], forced[1], "launched", accounting
            )
        else:
            accounting = job.wait_for_zero(
                deadline=min(deadline, time.monotonic() + 5.0)
            )
            if not drain.join(5.0) or drain.failed.is_set():
                _fail("pipe_drain_failed")
            if drain.overflow.is_set():
                terminal = _step_result(
                    ordinal,
                    "output_rejected",
                    "output_limit",
                    "launched",
                    accounting,
                )
            else:
                violation = job.limit_violation_reason(accounting)
                if violation is not None:
                    terminal = _step_result(
                        ordinal,
                        "resource_exceeded",
                        violation,
                        "launched",
                        accounting,
                    )
                else:
                    exit_code = child.poll()
                    if exit_code is None:
                        _fail("process_wait_failed")
                    terminal = _step_result(
                        ordinal,
                        "pass" if exit_code == 0 else "fail",
                        None if exit_code == 0 else "step_nonzero",
                        "launched",
                        accounting,
                    )
    except (RunnerWin32Error, RunnerProcessError) as error:
        if isinstance(error, RunnerWin32Error) and error.after_create:
            launched = True
        if error.code == "sandbox_cleanup_failed":
            cleanup_error = True
        if launched and job is not None:
            terminal_reason = error.code
            boundary_failed = error.code == "sandbox_boundary_violation"
            try:
                job.terminate()
                accounting = job.wait_for_zero(deadline=time.monotonic() + 5.0)
            except RunnerWin32Error:
                accounting = None
                terminal_reason = "job_state_unproved"
                cleanup_error = True
            terminal = _step_result(
                ordinal,
                "sandbox_violation" if boundary_failed else "process_error",
                "sandbox_boundary_violation" if boundary_failed else terminal_reason,
                "launched",
                accounting,
            )
        else:
            terminal = _step_result(
                ordinal, "blocked_prelaunch", error.code, "no_launch", None
            )
    except BaseException:
        accounting = None
        if launched and job is not None:
            try:
                job.terminate()
                accounting = job.wait_for_zero(deadline=time.monotonic() + 5.0)
            except RunnerWin32Error:
                accounting = None
                cleanup_error = True
        terminal = _step_result(
            ordinal,
            "controller_interrupted",
            "controller_interrupted",
            "launched" if launched else "no_launch",
            accounting,
        )
    finally:
        if child is not None:
            try:
                child.close()
            except RunnerWin32Error:
                cleanup_error = True
        if pipes is not None:
            try:
                # A pre-create failure may leave call-only child ends open.
                if any(
                    not handle.closed
                    for handle in (
                        pipes.stdin_child,
                        pipes.stdout_child,
                        pipes.stderr_child,
                    )
                ):
                    pipes.close_child_ends()
                pipes.close_parent_ends()
            except RunnerWin32Error:
                cleanup_error = True
        if drain is not None and not drain.join(1.0):
            cleanup_error = True
        if job is not None:
            try:
                job.close()
            except RunnerWin32Error:
                cleanup_error = True
    if cleanup_error:
        return _step_result(
            ordinal,
            "sandbox_cleanup_failed",
            "sandbox_cleanup_failed",
            "launched" if launched else "no_launch",
            None,
        )
    if terminal is None:
        _fail("process_wait_failed")
    return terminal


def _aggregate(
    results: tuple[StepProcessResult, ...],
) -> tuple[int | None, int | None, int | None]:
    if any(
        result.launch_state == "launched" and result.accounting is None
        for result in results
    ):
        return None, None, None
    accounting = tuple(
        result.accounting for result in results if result.accounting is not None
    )
    if not accounting:
        return None, None, None
    total_user_time = 0
    total_processes = 0
    peak_memory = 0
    try:
        for item in accounting:
            assert item is not None
            if item.total_processes <= 0 or item.active_processes != 0:
                raise OverflowError
            total_user_time += item.total_user_time_100ns
            total_processes += item.total_processes
            peak_memory = max(peak_memory, item.peak_job_memory_bytes)
            if total_user_time > 0x7FFFFFFFFFFFFFFF or total_processes > 0x7FFFFFFF:
                raise OverflowError
    except (OverflowError, TypeError):
        return None, None, None
    return total_user_time // 10_000, peak_memory, total_processes


def run_process_steps(
    *,
    steps: tuple[ProcessStep, ...],
    runtime_executable: str | os.PathLike[str],
    target_root: str | os.PathLike[str],
    scratch_root: str | os.PathLike[str],
    windows_directory: str | os.PathLike[str],
    cancel_requested: Callable[[], bool] = lambda: False,
) -> ProcessRunResult:
    started = time.monotonic()
    if (
        type(steps) is not tuple
        or not 1 <= len(steps) <= 16
        or any(not isinstance(step, ProcessStep) for step in steps)
        or len({step.step_id for step in steps}) != len(steps)
        or sum(step.limits.timeout_seconds for step in steps) > 1800
        or not callable(cancel_requested)
    ):
        _fail()
    runtime = Path(runtime_executable)
    target = Path(target_root)
    environment = build_clean_environment(windows_directory, scratch_root)
    completed: list[StepProcessResult] = []
    launched_any = False
    for ordinal, step in enumerate(steps, 1):
        try:
            before_job_cancelled = cancel_requested()
        except BaseException:
            before_job_cancelled = None
        if before_job_cancelled is not False:
            duration = max(0, int((time.monotonic() - started) * 1000))
            cpu, memory, processes = _aggregate(tuple(completed))
            if launched_any:
                return ProcessRunResult(
                    "cancelled" if before_job_cancelled is True else "controller_interrupted",
                    "cancelled" if before_job_cancelled is True else "controller_interrupted",
                    "launched",
                    None,
                    duration,
                    cpu,
                    memory,
                    processes,
                    tuple(completed),
                )
            return ProcessRunResult(
                "blocked_prelaunch",
                "cancelled" if before_job_cancelled is True else "controller_interrupted",
                "no_launch",
                None,
                duration,
                None,
                None,
                None,
                (),
            )
        result = _execute_step(
            ordinal=ordinal,
            step=step,
            runtime_executable=runtime,
            target_root=target,
            environment=environment,
            cancel_requested=cancel_requested,
        )
        if result.launch_state == "no_launch" and not launched_any:
            duration = max(0, int((time.monotonic() - started) * 1000))
            return ProcessRunResult(
                result.outcome,
                result.reason,
                "no_launch",
                None,
                duration,
                None,
                None,
                None,
                tuple(completed),
            )
        if result.launch_state == "no_launch" and launched_any:
            if result.outcome in {
                "controller_interrupted",
                "sandbox_cleanup_failed",
            }:
                duration = max(0, int((time.monotonic() - started) * 1000))
                return ProcessRunResult(
                    result.outcome,
                    result.outcome,
                    "launched",
                    None,
                    duration,
                    None,
                    None,
                    None,
                    tuple(completed),
                )
            result = StepProcessResult(
                result.ordinal,
                "process_error",
                "process_create_failed",
                "no_launch",
                None,
            )
        completed.append(result)
        launched_any = launched_any or result.launch_state == "launched"
        if result.outcome != "pass":
            duration = max(0, int((time.monotonic() - started) * 1000))
            cpu, memory, processes = _aggregate(tuple(completed))
            if (cpu, memory, processes).count(None) not in {0, 3}:
                cpu = memory = processes = None
            return ProcessRunResult(
                result.outcome,
                result.reason,
                "launched" if launched_any else "no_launch",
                ordinal,
                duration,
                cpu,
                memory,
                processes,
                tuple(completed),
            )
    duration = max(0, int((time.monotonic() - started) * 1000))
    cpu, memory, processes = _aggregate(tuple(completed))
    if cpu is None or memory is None or processes is None:
        return ProcessRunResult(
            "process_error",
            "job_state_unproved",
            "launched",
            len(completed),
            duration,
            None,
            None,
            None,
            tuple(completed),
        )
    return ProcessRunResult(
        "pass",
        None,
        "launched",
        None,
        duration,
        cpu,
        memory,
        processes,
        tuple(completed),
    )
