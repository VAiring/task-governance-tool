"""Closed OS-neutral values and entry points for verification Runner requests.

Values carry bounded in-process input and sanitized results, not business
authority or persistence. OS-specific admission and execution are selected only
at the operation entry points; unsupported platforms cannot launch.
"""

from __future__ import annotations

import re
import sys
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import NoReturn

from task_governance_tool.verification_runner import (
    RUNNER_CONTRACT_VERSION,
    RUNNER_EXECUTABLE_ID,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_MAX_OUTPUT_BYTES,
    RUNNER_POLICY_DIGEST,
    RUNNER_POLICY_DIGESTS,
    RUNNER_POSIX_POLICY_DIGEST,
    runner_accounting_valid,
)


MAX_OUTPUT_BYTES = RUNNER_MAX_OUTPUT_BYTES
EXECUTABLE_ID = RUNNER_EXECUTABLE_ID
MAX_RESULT_INTEGER = 0x7FFFFFFFFFFFFFFF

_ATTEMPT_ID = re.compile(r"^tg_verification_runner_attempt_[0-9a-f]{16}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MODULE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,15}$"
)
_RELATIVE_COMPONENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_LOCAL_REASONS = frozenset(
    {
        "runtime_unavailable",
        "process_setup_failed",
        "process_boundary_unproved",
        "step_nonzero",
        "timeout",
        "cancelled",
        "cpu_limit",
        "memory_limit",
        "output_limit",
        "process_create_failed",
        "process_resume_failed",
        "process_wait_failed",
        "pipe_drain_failed",
        "process_tree_unproved",
        "controller_interrupted",
        "process_cleanup_failed",
    }
)

_STEP_PAIRINGS = frozenset(
    {
        ("blocked_prelaunch", "runtime_unavailable", "no_launch"),
        ("blocked_prelaunch", "process_setup_failed", "no_launch"),
        ("blocked_prelaunch", "process_boundary_unproved", "no_launch"),
        ("blocked_prelaunch", "process_create_failed", "no_launch"),
        ("pass", None, "launched"),
        ("fail", "step_nonzero", "launched"),
        ("timeout", "timeout", "launched"),
        ("cancelled", "cancelled", "launched"),
        ("resource_exceeded", "cpu_limit", "launched"),
        ("resource_exceeded", "memory_limit", "launched"),
        ("output_rejected", "output_limit", "launched"),
        ("process_error", "process_boundary_unproved", "launched"),
        ("process_error", "process_create_failed", "no_launch"),
        ("process_error", "process_resume_failed", "launched"),
        ("process_error", "process_wait_failed", "launched"),
        ("process_error", "pipe_drain_failed", "launched"),
        ("process_error", "process_tree_unproved", "launched"),
        ("controller_interrupted", "controller_interrupted", "no_launch"),
        ("controller_interrupted", "controller_interrupted", "launched"),
        ("cleanup_failed", "process_cleanup_failed", "no_launch"),
        ("cleanup_failed", "process_cleanup_failed", "launched"),
    }
)

_RESULT_PAIRINGS = frozenset(
    {
        ("blocked_prelaunch", "runtime_unavailable", "no_launch"),
        ("blocked_prelaunch", "process_setup_failed", "no_launch"),
        ("blocked_prelaunch", "process_boundary_unproved", "no_launch"),
        ("blocked_prelaunch", "process_create_failed", "no_launch"),
        ("blocked_prelaunch", "cancelled", "no_launch"),
        ("blocked_prelaunch", "controller_interrupted", "no_launch"),
        ("pass", None, "launched"),
        ("fail", "step_nonzero", "launched"),
        ("timeout", "timeout", "launched"),
        ("cancelled", "cancelled", "launched"),
        ("resource_exceeded", "cpu_limit", "launched"),
        ("resource_exceeded", "memory_limit", "launched"),
        ("output_rejected", "output_limit", "launched"),
        ("process_error", "runtime_unavailable", "launched"),
        ("process_error", "process_setup_failed", "launched"),
        ("process_error", "process_boundary_unproved", "launched"),
        ("process_error", "process_create_failed", "launched"),
        ("process_error", "process_resume_failed", "launched"),
        ("process_error", "process_wait_failed", "launched"),
        ("process_error", "pipe_drain_failed", "launched"),
        ("process_error", "process_tree_unproved", "launched"),
        ("controller_interrupted", "controller_interrupted", "no_launch"),
        ("controller_interrupted", "controller_interrupted", "launched"),
        ("cleanup_failed", "process_cleanup_failed", "no_launch"),
        ("cleanup_failed", "process_cleanup_failed", "launched"),
    }
)


class RunnerProcessError(RuntimeError):
    """One bounded, non-sensitive process-boundary error."""

    def __init__(self, code: str = "process_setup_failed") -> None:
        if code not in _LOCAL_REASONS:
            code = "process_setup_failed"
        super().__init__("verification Runner process boundary failed closed")
        self.code = code


def _fail(code: str = "process_setup_failed") -> NoReturn:
    raise RunnerProcessError(code)


def _valid_unicode(
    value: object,
    *,
    minimum_utf8: int,
    maximum_utf8: int,
) -> bool:
    if type(value) is not str:
        return False
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        return False
    return bool(
        minimum_utf8 <= size <= maximum_utf8
        and all(unicodedata.category(character) != "Cc" for character in value)
    )


def _valid_relative_path(value: object, *, script: bool) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeError:
        return False
    if value == ".":
        return not script
    components = value.split("/")
    return bool(
        1 <= len(components) <= 32
        and 1 <= len(encoded) <= 512
        and all(
            component not in {".", ".."}
            and _RELATIVE_COMPONENT.fullmatch(component) is not None
            for component in components
        )
        and (not script or value.endswith(".py"))
    )


def _valid_module(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeError:
        return False
    return len(encoded) <= 512 and _MODULE.fullmatch(value) is not None


def _valid_absolute_path(path: object) -> bool:
    return bool(
        isinstance(path, PurePath)
        and _valid_unicode(str(path), minimum_utf8=1, maximum_utf8=4096)
        and path.is_absolute()
        and all(part not in {".", ".."} for part in path.parts)
    )


def _valid_result_integer(value: object, *, nullable: bool) -> bool:
    return bool(
        (nullable and value is None)
        or (type(value) is int and 0 <= value <= MAX_RESULT_INTEGER)
    )


class RunnerCancelSignal:
    """A local signal whose only observation is one Boolean."""

    __slots__ = ("_event",)

    def __init__(self, requested: bool = False) -> None:
        if type(requested) is not bool:
            _fail()
        self._event = threading.Event()
        if requested:
            self._event.set()

    def request(self) -> None:
        self._event.set()

    def requested(self) -> bool:
        return self._event.is_set() is True


@dataclass(frozen=True, slots=True)
class RunnerProcessStepV1:
    ordinal: int
    step_id: str
    mode: str
    entrypoint: str
    argv: tuple[str, ...]
    cwd: str
    shell: bool
    path_lookup: bool
    timeout_seconds: int
    cpu_seconds: int
    memory_mib: int | None
    process_limit: int | None
    output_byte_limit: int

    def __post_init__(self) -> None:
        if (
            type(self.ordinal) is not int
            or not 1 <= self.ordinal <= 16
            or type(self.step_id) is not str
            or _IDENTIFIER.fullmatch(self.step_id) is None
            or type(self.mode) is not str
            or self.mode not in {"script", "module"}
            or not (
                _valid_relative_path(self.entrypoint, script=True)
                if self.mode == "script"
                else _valid_module(self.entrypoint)
            )
            or type(self.argv) is not tuple
            or len(self.argv) > 64
            or any(
                not _valid_unicode(
                    item,
                    minimum_utf8=0,
                    maximum_utf8=4096,
                )
                for item in self.argv
            )
            or not _valid_relative_path(self.cwd, script=False)
            or self.shell is not False
            or self.path_lookup is not False
            or type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 900
            or type(self.cpu_seconds) is not int
            or not 1 <= self.cpu_seconds <= 900
            or not (
                (self.memory_mib is None and self.process_limit is None)
                or (
                    type(self.memory_mib) is int
                    and 64 <= self.memory_mib <= 2048
                    and type(self.process_limit) is int
                    and 1 <= self.process_limit <= 32
                )
            )
            or type(self.output_byte_limit) is not int
            or self.output_byte_limit != MAX_OUTPUT_BYTES
        ):
            _fail()


def _validate_clean_environment_shape(entries: object) -> None:
    if type(entries) is not tuple or len(entries) > 11:
        _fail()
    if any(
        type(item) is not tuple
        or len(item) != 2
        or not _valid_unicode(item[0], minimum_utf8=1, maximum_utf8=4096)
        or "=" in item[0]
        or not _valid_unicode(item[1], minimum_utf8=1, maximum_utf8=4096)
        for item in entries
    ):
        _fail()
    if len({key for key, _value in entries}) != len(entries):
        _fail()


@dataclass(frozen=True, slots=True)
class RunnerProcessRequestV1:
    version: int
    attempt_id: str
    executable: PurePath
    materialized_root: PurePath
    scratch_root: PurePath
    clean_environment: tuple[tuple[str, str], ...]
    steps: tuple[RunnerProcessStepV1, ...]
    cancel_signal: RunnerCancelSignal
    runner_policy_digest: str = RUNNER_POLICY_DIGEST

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != RUNNER_CONTRACT_VERSION
            or type(self.attempt_id) is not str
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
            or not _valid_absolute_path(self.executable)
            or not _valid_absolute_path(self.materialized_root)
            or not _valid_absolute_path(self.scratch_root)
            or self.materialized_root == self.scratch_root
            or self.materialized_root.name.casefold() != "target"
            or self.scratch_root.name.casefold() != "scratch"
            or self.materialized_root.parent != self.scratch_root.parent
            or self.materialized_root.parent.name != self.attempt_id
            or type(self.steps) is not tuple
            or not 1 <= len(self.steps) <= 16
            or any(type(step) is not RunnerProcessStepV1 for step in self.steps)
            or tuple(step.ordinal for step in self.steps)
            != tuple(range(1, len(self.steps) + 1))
            or len({step.step_id for step in self.steps}) != len(self.steps)
            or sum(step.timeout_seconds for step in self.steps) > 1800
            or type(self.cancel_signal) is not RunnerCancelSignal
            or type(self.runner_policy_digest) is not str
            or self.runner_policy_digest not in RUNNER_POLICY_DIGESTS
        ):
            _fail()
        _validate_clean_environment_shape(self.clean_environment)


@dataclass(frozen=True, slots=True)
class RunnerProcessStepResultV1:
    ordinal: int
    outcome: str
    reason: str | None
    launch_state: str
    cpu_time_ms: int | None
    peak_job_memory_bytes: int | None
    total_process_count: int | None
    runner_policy_digest: str = RUNNER_POLICY_DIGEST

    def __post_init__(self) -> None:
        accounting = (
            self.cpu_time_ms,
            self.peak_job_memory_bytes,
            self.total_process_count,
        )
        if (
            type(self.ordinal) is not int
            or not 1 <= self.ordinal <= 16
            or (self.outcome, self.reason, self.launch_state) not in _STEP_PAIRINGS
            or any(
                not _valid_result_integer(value, nullable=True)
                for value in accounting
            )
            or type(self.runner_policy_digest) is not str
            or self.runner_policy_digest not in RUNNER_POLICY_DIGESTS
            or not runner_accounting_valid(
                self.runner_policy_digest,
                outcome=self.outcome,
                launch_state=self.launch_state,
                cpu_time_ms=self.cpu_time_ms,
                peak_job_memory_bytes=self.peak_job_memory_bytes,
                total_process_count=self.total_process_count,
            )
        ):
            _fail()


@dataclass(frozen=True, slots=True)
class RunnerProcessResultV1:
    version: int
    attempt_id: str
    outcome: str
    reason: str | None
    launch_state: str
    failed_step_ordinal: int | None
    duration_ms: int
    cpu_time_ms: int | None
    peak_job_memory_bytes: int | None
    total_process_count: int | None
    process_zero: bool
    handles_closed: bool
    raw_output_discarded: bool
    steps: tuple[RunnerProcessStepResultV1, ...]
    runner_policy_digest: str = RUNNER_POLICY_DIGEST

    def __post_init__(self) -> None:
        accounting = (
            self.cpu_time_ms,
            self.peak_job_memory_bytes,
            self.total_process_count,
        )
        proof = (
            self.process_zero,
            self.handles_closed,
            self.raw_output_discarded,
        )
        if (
            type(self.version) is not int
            or self.version != RUNNER_CONTRACT_VERSION
            or type(self.attempt_id) is not str
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
            or (self.outcome, self.reason, self.launch_state)
            not in _RESULT_PAIRINGS
            or (
                self.failed_step_ordinal is not None
                and (
                    type(self.failed_step_ordinal) is not int
                    or not 1 <= self.failed_step_ordinal <= 16
                )
            )
            or not _valid_result_integer(self.duration_ms, nullable=False)
            or any(
                not _valid_result_integer(value, nullable=True)
                for value in accounting
            )
            or type(self.runner_policy_digest) is not str
            or self.runner_policy_digest not in RUNNER_POLICY_DIGESTS
            or not runner_accounting_valid(
                self.runner_policy_digest,
                outcome=self.outcome,
                launch_state=self.launch_state,
                cpu_time_ms=self.cpu_time_ms,
                peak_job_memory_bytes=self.peak_job_memory_bytes,
                total_process_count=self.total_process_count,
            )
            or any(type(value) is not bool for value in proof)
            or type(self.steps) is not tuple
            or len(self.steps) > 16
            or any(
                type(step) is not RunnerProcessStepResultV1
                or step.runner_policy_digest != self.runner_policy_digest
                for step in self.steps
            )
            or tuple(step.ordinal for step in self.steps)
            != tuple(sorted({step.ordinal for step in self.steps}))
            or (self.launch_state == "no_launch" and self.steps)
            or (
                self.outcome == "pass"
                and self.failed_step_ordinal is not None
            )
            or (
                self.outcome == "cleanup_failed"
                and self.process_zero
                and self.handles_closed
                and self.raw_output_discarded
            )
            or (
                self.outcome != "cleanup_failed"
                and not (
                    self.process_zero
                    and self.handles_closed
                    and self.raw_output_discarded
                )
            )
        ):
            _fail()


# The service annotation remains an alias, not a second record shape.
ProcessRunResult = RunnerProcessResultV1


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
    " if _spec is None or not _spec.origin or not os.path.isabs(_spec.origin) or not _under(_root,_spec.origin):raise SystemExit(126)\n"
    "runpy.run_path(_resolved,run_name='__main__') if _mode=='script' else "
    "runpy.run_module(_resolved,run_name='__main__',alter_sys=False)\n"
)


def _aggregate(
    results: tuple[RunnerProcessStepResultV1, ...],
) -> tuple[int | None, int | None, int | None]:
    if len({result.runner_policy_digest for result in results}) > 1:
        _fail()
    if not results or any(result.cpu_time_ms is None for result in results):
        return None, None, None
    if results[0].runner_policy_digest == RUNNER_POSIX_POLICY_DIGEST:
        total_cpu = sum(result.cpu_time_ms for result in results)
        return (total_cpu if total_cpu <= MAX_RESULT_INTEGER else None), None, None
    total_cpu = 0
    total_processes = 0
    peak_memory = 0
    try:
        for result in results:
            assert result.cpu_time_ms is not None
            assert result.peak_job_memory_bytes is not None
            assert result.total_process_count is not None
            total_cpu += result.cpu_time_ms
            total_processes += result.total_process_count
            peak_memory = max(
                peak_memory,
                result.peak_job_memory_bytes,
            )
            if (
                total_cpu > MAX_RESULT_INTEGER
                or total_processes > MAX_RESULT_INTEGER
            ):
                raise OverflowError
    except (AssertionError, OverflowError, TypeError):
        return None, None, None
    return total_cpu, peak_memory, total_processes


def _result(
    request: RunnerProcessRequestV1,
    *,
    outcome: str,
    reason: str | None,
    launch_state: str,
    failed_step_ordinal: int | None,
    duration_ms: int,
    steps: tuple[RunnerProcessStepResultV1, ...],
    process_zero: bool,
    handles_closed: bool,
    raw_output_discarded: bool,
    accounting_complete: bool = True,
) -> RunnerProcessResultV1:
    if accounting_complete:
        cpu, memory, processes = _aggregate(steps)
    else:
        cpu, memory, processes = None, None, None
    result = RunnerProcessResultV1(
        RUNNER_CONTRACT_VERSION,
        request.attempt_id,
        outcome,
        reason,
        launch_state,
        failed_step_ordinal,
        duration_ms,
        cpu,
        memory,
        processes,
        process_zero,
        handles_closed,
        raw_output_discarded,
        steps,
        request.runner_policy_digest,
    )
    _validate_result_for_request(request, result)
    return result


def _validate_result_for_request(
    request: RunnerProcessRequestV1,
    result: RunnerProcessResultV1,
) -> None:
    expected_prefix = request.steps[: len(result.steps)]
    if (
        result.version != request.version
        or result.attempt_id != request.attempt_id
        or result.runner_policy_digest != request.runner_policy_digest
        or tuple(item.ordinal for item in result.steps)
        != tuple(step.ordinal for step in expected_prefix)
        or (
            result.failed_step_ordinal is not None
            and result.failed_step_ordinal
            not in {step.ordinal for step in request.steps}
        )
    ):
        _fail()
    for step, step_result in zip(
        expected_prefix,
        result.steps,
        strict=True,
    ):
        if (
            request.runner_policy_digest == RUNNER_POLICY_DIGEST
            and step_result.cpu_time_ms is not None
            and (
                step_result.cpu_time_ms > step.cpu_seconds * 1000
                or step_result.peak_job_memory_bytes is None
                or (
                    step.memory_mib is not None
                    and step_result.peak_job_memory_bytes > step.memory_mib * 1_048_576
                )
            )
        ):
            _fail("process_tree_unproved")


def build_clean_environment(scratch_root: Path) -> tuple[tuple[str, str], ...]:
    """Build the supported platform's credential-excluding environment."""

    if sys.platform in {"linux", "darwin"}:
        from task_governance_tool._verification_runner_process_posix import (
            prepare_clean_environment,
        )

        return prepare_clean_environment(scratch_root)
    if sys.platform != "win32":
        _fail("runtime_unavailable")
    from task_governance_tool._verification_runner_process_win32 import (
        prepare_clean_environment,
    )

    return prepare_clean_environment(scratch_root)


def run_process_request(
    request: RunnerProcessRequestV1,
) -> RunnerProcessResultV1:
    """Dispatch one closed request to the supported platform implementation."""

    if sys.platform == "linux":
        from task_governance_tool._verification_runner_process_posix import (
            run_process_request as run_posix_request,
        )

        return run_posix_request(request)
    if sys.platform == "darwin":
        if type(request) is not RunnerProcessRequestV1:
            _fail()
        # macOS preparation is connected, but public launch remains inactive.
        # The service still owns private-tree cleanup and manual fallback.
        return _result(
            request, outcome="blocked_prelaunch", reason="runtime_unavailable",
            launch_state="no_launch", failed_step_ordinal=None, duration_ms=0,
            steps=(), process_zero=True, handles_closed=True,
            raw_output_discarded=True,
        )
    if sys.platform != "win32":
        _fail("runtime_unavailable")
    from task_governance_tool._verification_runner_process_win32 import (
        run_process_request as run_windows_request,
    )

    return run_windows_request(request)


__all__ = [
    "EXECUTABLE_ID",
    "FIXED_BOOTSTRAP",
    "MAX_OUTPUT_BYTES",
    "ProcessRunResult",
    "RunnerCancelSignal",
    "RunnerProcessError",
    "RunnerProcessRequestV1",
    "RunnerProcessResultV1",
    "RunnerProcessStepResultV1",
    "RunnerProcessStepV1",
    "build_clean_environment",
    "run_process_request",
]
