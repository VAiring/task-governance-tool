"""Private POSIX request execution; public dispatch is enabled separately."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from task_governance_tool.state_paths import (
    StatePathError, inspect_physical_directory, inspect_physical_file,
)
from task_governance_tool.verification_runner import RUNNER_POSIX_POLICY_DIGEST
from task_governance_tool.verification_runner_process import (
    FIXED_BOOTSTRAP, MAX_RESULT_INTEGER, RunnerProcessError,
    RunnerProcessRequestV1, RunnerProcessResultV1, RunnerProcessStepV1,
    RunnerProcessStepResultV1, _result, _valid_absolute_path,
)


def prepare_clean_environment(scratch_root: Path) -> tuple[tuple[str, str], ...]:
    """Map only the existing private home/temp directories to a closed tuple."""

    if os.name != "posix" or sys.platform not in {"linux", "darwin"}:
        raise RunnerProcessError("runtime_unavailable")
    if (
        not isinstance(scratch_root, Path)
        or not _valid_absolute_path(scratch_root)
        or scratch_root.name != "scratch"
    ):
        raise RunnerProcessError()
    try:
        inspect_physical_directory(scratch_root, root=Path(scratch_root.anchor))
        for name in ("home", "tmp"):
            directory = scratch_root / name
            if not _valid_absolute_path(directory):
                raise RunnerProcessError()
            inspect_physical_directory(directory, root=scratch_root)
    except StatePathError as exc:
        raise RunnerProcessError("process_boundary_unproved") from exc
    return (
        ("HOME", str(scratch_root / "home")),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PYTHONNOUSERSITE", "1"),
        ("PYTHONUTF8", "1"),
        ("TEMP", str(scratch_root / "tmp")),
        ("TMP", str(scratch_root / "tmp")),
        ("TMPDIR", str(scratch_root / "tmp")),
    )


_POSIX_BOOTSTRAP = (
    "import resource,sys\n"
    "_cpu=int(sys.argv.pop(1))\n"
    "resource.setrlimit(resource.RLIMIT_CORE,(0,0))\n"
    "resource.setrlimit(resource.RLIMIT_CPU,(_cpu,_cpu+1))\n"
) + FIXED_BOOTSTRAP


def _prepare_step(
    request: RunnerProcessRequestV1, step: RunnerProcessStepV1,
) -> tuple[tuple[str, ...], Path]:
    root = request.materialized_root
    cwd = root if step.cwd == "." else root.joinpath(*step.cwd.split("/"))
    try:
        inspect_physical_directory(cwd, root=Path(root.anchor))
        if step.mode == "script":
            entry = root.joinpath(*step.entrypoint.split("/"))
            inspect_physical_file(entry, root=root)
            resolved = str(entry)
        else:
            resolved = step.entrypoint
    except StatePathError as exc:
        raise RunnerProcessError("process_boundary_unproved") from exc
    return (
        str(request.executable), "-I", "-S", "-B", "-X", "utf8", "-c",
        _POSIX_BOOTSTRAP, str(step.cpu_seconds), str(root), str(cwd),
        step.mode, resolved, step.entrypoint, *step.argv,
    ), cwd


def _admit_request(request: RunnerProcessRequestV1) -> None:
    if os.name != "posix" or sys.platform not in {"linux", "darwin"}:
        raise RunnerProcessError("runtime_unavailable")
    if (
        type(request) is not RunnerProcessRequestV1
        or request.runner_policy_digest != RUNNER_POSIX_POLICY_DIGEST
        or any(not isinstance(path, Path) for path in (
            request.executable, request.materialized_root, request.scratch_root,
        ))
        or request.executable.is_relative_to(request.materialized_root.parent)
    ):
        raise RunnerProcessError()
    try:
        inspect_physical_file(request.executable, root=Path(request.executable.anchor))
        inspect_physical_directory(
            request.materialized_root, root=Path(request.materialized_root.anchor),
        )
        if request.clean_environment != prepare_clean_environment(request.scratch_root):
            raise RunnerProcessError()
        for step in request.steps:
            _prepare_step(request, step)
    except StatePathError as exc:
        raise RunnerProcessError("process_boundary_unproved") from exc


@dataclass(frozen=True, slots=True)
class _StepExecution:
    result: RunnerProcessStepResultV1
    process_zero: bool
    handles_closed: bool
    raw_output_discarded: bool


@dataclass(slots=True)
class _ChildState:
    child: subprocess.Popen
    cpu_time_ms: int | None = None


def _poll_root(state: _ChildState) -> None:
    """Own the only root reaper, including its user-CPU observation."""
    if state.child.returncode is not None:
        return
    try:
        pid, status, usage = os.wait4(state.child.pid, os.WNOHANG)
    except InterruptedError:
        return
    except OSError as exc:
        raise RunnerProcessError("process_wait_failed") from exc
    if pid == 0:
        return
    if pid != state.child.pid:
        raise RunnerProcessError("process_wait_failed")
    # Set immediately so Popen destruction cannot consume a second wait.
    state.child.returncode = os.waitstatus_to_exitcode(status)
    try:
        cpu = usage.ru_utime * 1000
        if not math.isfinite(cpu) or not 0 <= cpu <= MAX_RESULT_INTEGER:
            raise ValueError
        state.cpu_time_ms = int(cpu)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise RunnerProcessError("process_wait_failed") from exc


def _group_absent(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except OSError as exc:
        raise RunnerProcessError("process_tree_unproved") from exc
    return False


def _signal_group(pid: int, *, force: bool) -> None:
    try:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pass


class _DiscardingPipes:
    """A bounded nonblocking drain; neither stream bytes nor text are retained."""

    def __init__(self, child: subprocess.Popen, limit: int) -> None:
        self.streams = (child.stdout, child.stderr)
        self.eof: set[int] = set()
        self.count = 0
        self.limit = limit
        self.failed = False
        for stream in self.streams:
            os.set_blocking(stream.fileno(), False)

    @property
    def overflow(self) -> bool:
        return self.count > self.limit

    @property
    def complete(self) -> bool:
        return len(self.eof) == len(self.streams)

    def poll(self) -> None:
        for ordinal, stream in enumerate(self.streams):
            if ordinal in self.eof:
                continue
            try:
                chunk = os.read(stream.fileno(), 65536)
                size = len(chunk)
                del chunk
                if size == 0:
                    self.eof.add(ordinal)
                self.count = min(self.limit + 1, self.count + size)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                self.failed = True


def _retire_group(state: _ChildState, drain: _DiscardingPipes | None) -> bool:
    """Stop only this launch's ordinary group; no daemon inventory or reaper."""
    try:
        _poll_root(state)
        if state.child.returncode is not None and _group_absent(state.child.pid):
            return True
    except BaseException:
        pass
    for force, seconds in ((False, 0.25), (True, 5.0)):
        try:
            _signal_group(state.child.pid, force=force)
        except BaseException:
            # Still attempt the next stop stage and close owned pipes below.
            continue
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if drain is not None:
                    drain.poll()
                _poll_root(state)
                if state.child.returncode is not None and _group_absent(state.child.pid):
                    return True
            except BaseException:
                break
            time.sleep(0.01)
    return False


def _exit_outcome(returncode: int) -> tuple[str, str | None]:
    if returncode == 0:
        return "pass", None
    cpu_signal = getattr(signal, "SIGXCPU", None)
    if cpu_signal is not None and returncode == -cpu_signal:
        return "resource_exceeded", "cpu_limit"
    return "fail", "step_nonzero"


def _execute_step(
    request: RunnerProcessRequestV1, step: RunnerProcessStepV1,
) -> _StepExecution:
    state = None
    drain = None
    create_started = False
    launched = False
    process_zero = True
    handles_closed = True
    raw_output_discarded = True
    outcome, reason = "blocked_prelaunch", "process_setup_failed"
    deadline = time.monotonic() + step.timeout_seconds
    try:
        argv, cwd = _prepare_step(request, step)
        create_started = True
        try:
            child = subprocess.Popen(
                argv, executable=str(request.executable), cwd=cwd,
                env=dict(request.clean_environment), shell=False,
                start_new_session=True, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0,
            )
        except (OSError, ValueError):
            create_started = False  # Popen proved creation/setup did not succeed.
            raise RunnerProcessError("process_create_failed") from None
        state = _ChildState(child)
        launched = True
        process_zero = False
        drain = _DiscardingPipes(child, step.output_byte_limit)
        while True:
            drain.poll()
            if drain.failed:
                outcome, reason = "process_error", "pipe_drain_failed"
                break
            if drain.overflow:
                outcome, reason = "output_rejected", "output_limit"
                break
            if request.cancel_signal.requested():
                outcome, reason = "cancelled", "cancelled"
                break
            if time.monotonic() >= deadline:
                outcome, reason = "timeout", "timeout"
                break
            _poll_root(state)
            if child.returncode is not None:
                outcome, reason = _exit_outcome(child.returncode)
                if outcome != "pass":
                    break
                if _group_absent(child.pid):
                    process_zero = True
                    break
            time.sleep(0.01)
    except RunnerProcessError as exc:
        outcome = "process_error" if launched else "blocked_prelaunch"
        reason = exc.code
    except OSError:
        outcome, reason = (
            ("process_error", "pipe_drain_failed") if launched
            else ("blocked_prelaunch", "process_setup_failed")
        )
    except BaseException:
        launched = launched or create_started
        if create_started and state is None:
            process_zero = handles_closed = raw_output_discarded = False
        outcome, reason = "controller_interrupted", "controller_interrupted"
    finally:
        if state is not None:
            try:
                process_zero = _retire_group(state, drain)
            except BaseException:
                process_zero = False
            if drain is not None:
                finish = time.monotonic() + 5.0
                try:
                    while (
                        not drain.complete and not drain.failed
                        and time.monotonic() < finish
                    ):
                        drain.poll()
                        if not drain.complete:
                            time.sleep(0.01)
                    if drain.failed or not drain.complete:
                        if outcome == "pass":
                            outcome, reason = "process_error", "pipe_drain_failed"
                    elif drain.overflow and outcome == "pass":
                        outcome, reason = "output_rejected", "output_limit"
                except BaseException:
                    outcome, reason = "controller_interrupted", "controller_interrupted"
            for stream in (state.child.stdout, state.child.stderr):
                try:
                    stream.close()
                    if not stream.closed:
                        handles_closed = False
                except BaseException:
                    handles_closed = False
            # No drain worker or byte collection survives closure of these pipes.
            raw_output_discarded = handles_closed
    if not (process_zero and handles_closed and raw_output_discarded):
        outcome, reason = "cleanup_failed", "process_cleanup_failed"
    cpu = state.cpu_time_ms if state is not None else None
    if outcome == "pass" and cpu is None:
        outcome, reason = "process_error", "process_wait_failed"
    return _StepExecution(
        RunnerProcessStepResultV1(
            step.ordinal, outcome, reason, "launched" if launched else "no_launch",
            cpu, None, None, request.runner_policy_digest,
        ), process_zero, handles_closed, raw_output_discarded,
    )


def run_process_request(request: RunnerProcessRequestV1) -> RunnerProcessResultV1:
    """Execute a private POSIX request without parent-state or cleanup decisions."""
    _admit_request(request)
    started = time.monotonic()
    completed: list[RunnerProcessStepResultV1] = []

    def result(
        outcome: str, reason: str | None, *, failed: int | None = None,
        proofs: tuple[bool, bool, bool] = (True, True, True),
    ) -> RunnerProcessResultV1:
        launched = any(s.launch_state == "launched" for s in completed)
        duration = min(
            MAX_RESULT_INTEGER, max(0, int((time.monotonic() - started) * 1000)),
        )
        return _result(
            request, outcome=outcome, reason=reason,
            launch_state="launched" if launched else "no_launch",
            failed_step_ordinal=failed,
            duration_ms=duration,
            steps=tuple(completed), process_zero=proofs[0],
            handles_closed=proofs[1], raw_output_discarded=proofs[2],
        )

    for step in request.steps:
        try:
            cancelled = request.cancel_signal.requested()
        except BaseException:
            return result("controller_interrupted", "controller_interrupted")
        if cancelled:
            return result("cancelled" if completed else "blocked_prelaunch", "cancelled")
        execution = _execute_step(request, step)
        current = execution.result
        proofs = (
            execution.process_zero, execution.handles_closed,
            execution.raw_output_discarded,
        )
        if current.launch_state == "no_launch" and not completed:
            return result(current.outcome, current.reason, proofs=proofs)
        if current.launch_state == "no_launch" and current.outcome in {
            "cleanup_failed", "controller_interrupted",
        }:
            return result(current.outcome, current.reason, proofs=proofs)
        completed.append(current)
        if current.outcome != "pass":
            return result(
                "process_error" if current.outcome == "blocked_prelaunch" else current.outcome,
                current.reason, failed=step.ordinal, proofs=proofs,
            )
    return result("pass", None)


__all__ = ["prepare_clean_environment", "run_process_request"]
