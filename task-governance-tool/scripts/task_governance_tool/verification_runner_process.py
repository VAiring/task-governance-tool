"""Bounded trusted-local process execution for verification Runner requests.

The module accepts one already-authorized, closed request. It does not choose
project authority, inspect or write SQLite, retain child output, or claim
hostile-code isolation. Native process mechanics remain in the thin Windows
adapter; every variable request value is admitted before the first native
resource is created.
"""

from __future__ import annotations

import os
import re
import stat
import threading
import time
import unicodedata
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
MAX_ENVIRONMENT_BLOCK_UTF16_UNITS = 24_576
MAX_RESULT_INTEGER = 0x7FFFFFFFFFFFFFFF

_ATTEMPT_ID = re.compile(r"^tg_verification_runner_attempt_[0-9a-f]{16}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MODULE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,15}$"
)
_RELATIVE_COMPONENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

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

_NATIVE_REASON_MAP = {
    "sandbox_unavailable": "runtime_unavailable",
    "sandbox_setup_failed": "process_setup_failed",
    "sandbox_boundary_violation": "process_boundary_unproved",
    "process_create_failed": "process_create_failed",
    "process_resume_failed": "process_resume_failed",
    "process_wait_failed": "process_wait_failed",
    "pipe_drain_failed": "pipe_drain_failed",
    "job_state_unproved": "process_tree_unproved",
    "sandbox_cleanup_failed": "process_cleanup_failed",
}

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


def _utf_lengths(value: object) -> tuple[int, int] | None:
    if type(value) is not str:
        return None
    try:
        return len(value.encode("utf-8", errors="strict")), len(
            value.encode("utf-16-le", errors="strict")
        ) // 2
    except UnicodeError:
        return None


def _valid_unicode(
    value: object,
    *,
    minimum_utf8: int,
    maximum_utf8: int,
    maximum_utf16: int,
) -> bool:
    lengths = _utf_lengths(value)
    return bool(
        lengths is not None
        and minimum_utf8 <= lengths[0] <= maximum_utf8
        and lengths[1] <= maximum_utf16
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
    if not isinstance(path, Path):
        return False
    value = str(path)
    if not _valid_unicode(
        value,
        minimum_utf8=1,
        maximum_utf8=4096,
        maximum_utf16=4096,
    ):
        return False
    try:
        normalized = Path(os.path.normpath(value))
    except (OSError, ValueError):
        return False
    return bool(
        path.is_absolute()
        and normalized == path
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
    memory_mib: int
    process_limit: int
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
                    maximum_utf16=4096,
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
            or type(self.memory_mib) is not int
            or not 64 <= self.memory_mib <= 2048
            or type(self.process_limit) is not int
            or not 1 <= self.process_limit <= 32
            or type(self.output_byte_limit) is not int
            or self.output_byte_limit != MAX_OUTPUT_BYTES
        ):
            _fail()

    @property
    def limits(self) -> JobLimits:
        return JobLimits(
            self.timeout_seconds,
            self.cpu_seconds,
            self.memory_mib,
            self.process_limit,
        )


def _validate_clean_environment_shape(
    entries: object,
    scratch_root: Path,
) -> str:
    if type(entries) is not tuple or len(entries) != 11:
        _fail()
    if any(
        type(item) is not tuple
        or len(item) != 2
        or type(item[0]) is not str
        or type(item[1]) is not str
        for item in entries
    ):
        _fail()
    exact = entries
    if tuple(key for key, _value in exact) != _ENVIRONMENT_KEYS:
        _fail()
    if len({key.casefold() for key, _value in exact}) != 11:
        _fail()
    values = dict(exact)
    if any(
        not _valid_unicode(
            value,
            minimum_utf8=1,
            maximum_utf8=4096,
            maximum_utf16=4096,
        )
        for value in values.values()
    ):
        _fail()
    expected = {
        "APPDATA": scratch_root / "roaming",
        "HOME": scratch_root / "home",
        "LOCALAPPDATA": scratch_root / "local",
        "TEMP": scratch_root / "tmp",
        "TMP": scratch_root / "tmp",
        "USERPROFILE": scratch_root / "home",
    }
    if any(
        not _valid_absolute_path(Path(values[key]))
        or Path(values[key]) != path
        for key, path in expected.items()
    ):
        _fail()
    windows = Path(values["SystemRoot"])
    if (
        not _valid_absolute_path(windows)
        or Path(values["WINDIR"]) != windows
        or any(
            values[key] != "1"
            for key in (
                "PYTHONDONTWRITEBYTECODE",
                "PYTHONNOUSERSITE",
                "PYTHONUTF8",
            )
        )
    ):
        _fail()
    block = "".join(f"{key}={value}\0" for key, value in exact) + "\0"
    if (
        len(block.encode("utf-16-le", errors="strict")) // 2
        > MAX_ENVIRONMENT_BLOCK_UTF16_UNITS
    ):
        _fail()
    return block


@dataclass(frozen=True, slots=True)
class RunnerProcessRequestV1:
    version: int
    attempt_id: str
    executable: Path
    materialized_root: Path
    scratch_root: Path
    clean_environment: tuple[tuple[str, str], ...]
    steps: tuple[RunnerProcessStepV1, ...]
    cancel_signal: RunnerCancelSignal

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
        ):
            _fail()
        _validate_clean_environment_shape(
            self.clean_environment,
            self.scratch_root,
        )


@dataclass(frozen=True, slots=True)
class RunnerProcessStepResultV1:
    ordinal: int
    outcome: str
    reason: str | None
    launch_state: str
    cpu_time_ms: int | None
    peak_job_memory_bytes: int | None
    total_process_count: int | None

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
            or sum(value is None for value in accounting) not in {0, 3}
            or (
                self.launch_state == "no_launch"
                and any(value is not None for value in accounting)
            )
            or (
                self.outcome == "pass"
                and any(value is None for value in accounting)
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
            or sum(value is None for value in accounting) not in {0, 3}
            or any(type(value) is not bool for value in proof)
            or type(self.steps) is not tuple
            or len(self.steps) > 16
            or any(
                type(step) is not RunnerProcessStepResultV1
                for step in self.steps
            )
            or tuple(step.ordinal for step in self.steps)
            != tuple(sorted({step.ordinal for step in self.steps}))
            or (self.launch_state == "no_launch" and self.steps)
            or (
                self.launch_state == "no_launch"
                and any(value is not None for value in accounting)
            )
            or (
                self.outcome == "pass"
                and any(value is None for value in accounting)
            )
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


# The inactive service imports only this annotation name and reads fields shared
# with the exact V1 result. It remains a type alias, not a second record shape.
ProcessRunResult = RunnerProcessResultV1


def quote_windows_argument(argument: str) -> str:
    """Quote one argument as the inverse consumed by CommandLineToArgvW."""

    if _utf_lengths(argument) is None or "\0" in argument:
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
    if type(argv) is not tuple or not argv or len(argv) > 76:
        _fail()
    command_line = " ".join(quote_windows_argument(argument) for argument in argv)
    if (
        len(command_line.encode("utf-16-le", errors="strict")) // 2
        > MAX_COMMAND_LINE_UTF16_UNITS
    ):
        _fail()
    return command_line


def build_clean_environment(
    windows_directory: Path,
    scratch_root: Path,
) -> tuple[tuple[str, str], ...]:
    """Build the exact credential-clean tuple from parent-verified paths."""

    if (
        not _valid_absolute_path(windows_directory)
        or not _valid_absolute_path(scratch_root)
    ):
        _fail()
    try:
        verified_windows = _win32.verified_windows_directory().resolve(strict=True)
        supplied_windows = windows_directory.resolve(strict=True)
    except RunnerWin32Error as error:
        _fail(_native_reason(error))
    except (OSError, RuntimeError, ValueError):
        _fail("process_boundary_unproved")
    if os.path.normcase(str(verified_windows)) != os.path.normcase(
        str(supplied_windows)
    ):
        _fail("process_boundary_unproved")
    values = {
        "APPDATA": str(scratch_root / "roaming"),
        "HOME": str(scratch_root / "home"),
        "LOCALAPPDATA": str(scratch_root / "local"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "SystemRoot": str(windows_directory),
        "TEMP": str(scratch_root / "tmp"),
        "TMP": str(scratch_root / "tmp"),
        "USERPROFILE": str(scratch_root / "home"),
        "WINDIR": str(windows_directory),
    }
    entries = tuple((key, values[key]) for key in _ENVIRONMENT_KEYS)
    _validate_clean_environment_shape(entries, scratch_root)
    return entries


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


@dataclass(frozen=True, slots=True)
class _PathObservation:
    path: Path
    directory: bool
    chain: tuple[tuple[str, int, int, int, int], ...]


def _is_reparse(details: os.stat_result) -> bool:
    return stat.S_ISLNK(details.st_mode) or bool(
        int(getattr(details, "st_file_attributes", 0))
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _observe_physical_path(
    path: Path,
    *,
    directory: bool,
) -> _PathObservation:
    if not _valid_absolute_path(path):
        _fail("process_boundary_unproved")
    parts = path.parts
    if not parts:
        _fail("process_boundary_unproved")
    current = Path(parts[0])
    chain: list[tuple[str, int, int, int, int]] = []
    try:
        for part in (None, *parts[1:]):
            if part is not None:
                current = current / part
            details = current.lstat()
            if _is_reparse(details):
                _fail("process_boundary_unproved")
            chain.append(
                (
                    os.path.normcase(str(current)),
                    int(details.st_dev),
                    int(details.st_ino),
                    int(details.st_mode),
                    int(getattr(details, "st_file_attributes", 0)),
                )
            )
        final = path.lstat()
        resolved = path.resolve(strict=True)
    except RunnerProcessError:
        raise
    except (OSError, RuntimeError, ValueError):
        _fail("process_boundary_unproved")
    if (
        _is_reparse(final)
        or os.path.normcase(str(resolved)) != os.path.normcase(str(path))
        or (directory and not stat.S_ISDIR(final.st_mode))
        or (not directory and not stat.S_ISREG(final.st_mode))
    ):
        _fail("process_boundary_unproved")
    return _PathObservation(path, directory, tuple(chain))


def _ensure_same_observation(observation: _PathObservation) -> None:
    if (
        _observe_physical_path(
            observation.path,
            directory=observation.directory,
        ).chain
        != observation.chain
    ):
        _fail("process_boundary_unproved")


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        return os.path.normcase(
            os.path.commonpath((str(path), str(root)))
        ) == os.path.normcase(str(root))
    except ValueError:
        return False


def _contained_path(
    root: Path,
    relative: str,
    *,
    directory: bool,
) -> tuple[Path, _PathObservation]:
    candidate = (
        root if relative == "." else root.joinpath(*relative.split("/"))
    )
    if not _is_beneath(candidate, root):
        _fail("process_boundary_unproved")
    observation = _observe_physical_path(candidate, directory=directory)
    return candidate, observation


@dataclass(frozen=True, slots=True)
class _AdmittedStep:
    step: RunnerProcessStepV1
    raw_argv: tuple[str, ...]
    command_line: str
    cwd: Path
    observations: tuple[_PathObservation, ...]


@dataclass(frozen=True, slots=True)
class _AdmittedRequest:
    request: RunnerProcessRequestV1
    environment_block: str
    observations: tuple[_PathObservation, ...]
    steps: tuple[_AdmittedStep, ...]


def _prepare_step(
    request: RunnerProcessRequestV1,
    step: RunnerProcessStepV1,
) -> _AdmittedStep:
    cwd, cwd_observation = _contained_path(
        request.materialized_root,
        step.cwd,
        directory=True,
    )
    observations = [cwd_observation]
    if step.mode == "script":
        resolved, entry_observation = _contained_path(
            request.materialized_root,
            step.entrypoint,
            directory=False,
        )
        resolved_entry = str(resolved)
        observations.append(entry_observation)
    else:
        resolved_entry = step.entrypoint
    raw = (
        str(request.executable),
        "-I",
        "-B",
        "-X",
        "utf8",
        "-c",
        FIXED_BOOTSTRAP,
        str(request.materialized_root),
        str(cwd),
        step.mode,
        resolved_entry,
        step.entrypoint,
        *step.argv,
    )
    return _AdmittedStep(
        step,
        raw,
        quote_windows_argv(raw),
        cwd,
        tuple(observations),
    )


def _admit_request(request: RunnerProcessRequestV1) -> _AdmittedRequest:
    if type(request) is not RunnerProcessRequestV1:
        _fail()
    executable = _observe_physical_path(request.executable, directory=False)
    target = _observe_physical_path(request.materialized_root, directory=True)
    scratch = _observe_physical_path(request.scratch_root, directory=True)
    attempt_root = _observe_physical_path(
        request.materialized_root.parent,
        directory=True,
    )
    if (
        request.executable.name.casefold() != "python.exe"
        or _is_beneath(
            request.executable,
            request.materialized_root.parent,
        )
    ):
        _fail("process_boundary_unproved")
    environment_block = _validate_clean_environment_shape(
        request.clean_environment,
        request.scratch_root,
    )
    values = dict(request.clean_environment)
    try:
        verified_windows = _win32.verified_windows_directory().resolve(strict=True)
    except RunnerWin32Error as error:
        _fail(_native_reason(error))
    except (OSError, RuntimeError, ValueError):
        _fail("process_boundary_unproved")
    if os.path.normcase(str(verified_windows)) != os.path.normcase(
        str(Path(values["SystemRoot"]))
    ):
        _fail("process_boundary_unproved")
    environment_paths = (
        _observe_physical_path(Path(values["SystemRoot"]), directory=True),
        _observe_physical_path(request.scratch_root / "tmp", directory=True),
        _observe_physical_path(request.scratch_root / "home", directory=True),
        _observe_physical_path(request.scratch_root / "local", directory=True),
        _observe_physical_path(request.scratch_root / "roaming", directory=True),
    )
    steps = tuple(_prepare_step(request, step) for step in request.steps)
    return _AdmittedRequest(
        request,
        environment_block,
        (executable, target, scratch, attempt_root, *environment_paths),
        steps,
    )


class _DiscardingDrain:
    __slots__ = (
        "_handles",
        "_limit",
        "_lock",
        "_count",
        "overflow",
        "failed",
        "_threads",
    )

    def __init__(
        self,
        handles: tuple[_win32.OwnedHandle, _win32.OwnedHandle],
        limit: int,
    ) -> None:
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


@dataclass(frozen=True, slots=True)
class _StepExecution:
    result: RunnerProcessStepResultV1
    accounting: JobAccounting | None
    process_zero: bool
    handles_closed: bool
    raw_output_discarded: bool


@dataclass(frozen=True, slots=True)
class _RequestOnlyCreateFailure:
    ordinal: int
    process_zero: bool
    handles_closed: bool
    raw_output_discarded: bool


def _native_reason(error: RunnerWin32Error) -> str:
    return _NATIVE_REASON_MAP.get(error.code, "process_boundary_unproved")


def _flatten_accounting(
    ordinal: int,
    outcome: str,
    reason: str | None,
    launch_state: str,
    accounting: JobAccounting | None,
) -> RunnerProcessStepResultV1:
    if accounting is None:
        values = (None, None, None)
    else:
        try:
            if (
                type(accounting.total_user_time_100ns) is not int
                or accounting.total_user_time_100ns < 0
                or type(accounting.peak_job_memory_bytes) is not int
                or accounting.peak_job_memory_bytes < 0
                or type(accounting.total_processes) is not int
                or not 1 <= accounting.total_processes <= MAX_RESULT_INTEGER
                or accounting.active_processes != 0
            ):
                _fail("process_tree_unproved")
            values = (
                accounting.total_user_time_100ns // 10_000,
                accounting.peak_job_memory_bytes,
                accounting.total_processes,
            )
        except (AttributeError, TypeError, OverflowError):
            _fail("process_tree_unproved")
    return RunnerProcessStepResultV1(
        ordinal,
        outcome,
        reason,
        launch_state,
        values[0],
        values[1],
        values[2],
    )


def _execute_step(
    *,
    admitted: _AdmittedStep,
    admitted_request: _AdmittedRequest,
) -> _StepExecution | _RequestOnlyCreateFailure:
    step = admitted.step
    job = None
    pipes = None
    child = None
    drain = None
    create_started = False
    launched = False
    process_zero = True
    handles_closed = True
    raw_output_discarded = True
    accounting: JobAccounting | None = None
    terminal: tuple[str, str | None, str] | None = None
    cleanup_error = False
    try:
        for observation in (
            *admitted_request.observations,
            *admitted.observations,
        ):
            _ensure_same_observation(observation)
        job = _win32.create_job(step.limits)
        pipes = _win32.create_stdio_pipes()
        for observation in (
            *admitted_request.observations,
            *admitted.observations,
        ):
            _ensure_same_observation(observation)
        create_started = True
        child = _win32.create_suspended_child(
            application=admitted_request.request.executable,
            command_line=admitted.command_line,
            environment_block=admitted_request.environment_block,
            cwd=admitted.cwd,
            job=job,
            stdio=pipes,
        )
        launched = True
        process_zero = False
        pipes.close_child_ends()
        for observation in (
            *admitted_request.observations,
            *admitted.observations,
        ):
            _ensure_same_observation(observation)
        if not job.contains(child.process):
            _fail("process_tree_unproved")
        drain = _DiscardingDrain(
            (pipes.stdout_parent, pipes.stderr_parent),
            step.output_byte_limit,
        )
        drain.start()
        child.resume_once()
        deadline = time.monotonic() + step.timeout_seconds
        forced: tuple[str, str] | None = None
        while True:
            if drain.failed.is_set():
                forced = ("process_error", "pipe_drain_failed")
                break
            if drain.overflow.is_set():
                forced = ("output_rejected", "output_limit")
                break
            if admitted_request.request.cancel_signal.requested():
                forced = ("cancelled", "cancelled")
                break
            if time.monotonic() >= deadline:
                forced = ("timeout", "timeout")
                break
            if child.wait(10):
                observed = job.accounting()
                if observed.active_processes == 0:
                    break
                # A signaled root handle returns immediately even while Job
                # descendants remain. Preserve the 10 ms observation cadence.
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    forced = ("timeout", "timeout")
                    break
                time.sleep(min(0.01, remaining))
        if forced is not None:
            job.terminate()
            accounting = job.wait_for_zero(deadline=time.monotonic() + 5.0)
            process_zero = True
            if not drain.join(5.0):
                raw_output_discarded = False
                _fail("pipe_drain_failed")
            if drain.failed.is_set():
                _fail("pipe_drain_failed")
            terminal = (forced[0], forced[1], "launched")
        else:
            accounting = job.wait_for_zero(
                deadline=min(deadline, time.monotonic() + 5.0)
            )
            process_zero = True
            if not drain.join(5.0) or drain.failed.is_set():
                if not drain.join(0.0):
                    raw_output_discarded = False
                _fail("pipe_drain_failed")
            if drain.overflow.is_set():
                accounting = None
                terminal = ("output_rejected", "output_limit", "launched")
            else:
                violation = job.limit_violation_reason(accounting)
                # The Job ActiveProcessLimit prevents excess concurrent
                # creation, but supplies no deterministic, adapter-owned
                # terminal classification. The trusted program's eventual
                # exit status remains authoritative for that enforcement.
                if violation == "process_limit":
                    violation = None
                if violation is not None:
                    accounting = None
                    terminal = (
                        "resource_exceeded",
                        violation,
                        "launched",
                    )
                else:
                    exit_code = child.poll()
                    if exit_code is None:
                        _fail("process_wait_failed")
                    terminal = (
                        "pass" if exit_code == 0 else "fail",
                        None if exit_code == 0 else "step_nonzero",
                        "launched",
                    )
    except (RunnerWin32Error, RunnerProcessError) as error:
        if isinstance(error, RunnerWin32Error):
            reason = _native_reason(error)
            if error.after_create:
                launched = True
                process_zero = False
        else:
            reason = error.code
        if not launched and reason == "process_tree_unproved":
            reason = "process_boundary_unproved"
        if reason == "process_cleanup_failed":
            cleanup_error = True
            # A native cleanup failure is itself an unclosed-handle proof,
            # even if a later best-effort close happens to succeed.
            handles_closed = False
        if launched and job is not None:
            try:
                job.terminate()
                accounting = job.wait_for_zero(
                    deadline=time.monotonic() + 5.0
                )
                process_zero = True
            except BaseException:
                accounting = None
                process_zero = False
                cleanup_error = True
            terminal = ("process_error", reason, "launched")
        else:
            terminal = ("blocked_prelaunch", reason, "no_launch")
    except BaseException:
        if create_started:
            # An unknown interruption after CreateProcess entry cannot prove
            # that no process was created. Conservatively retire the Job and
            # require a fresh zero observation before returning.
            launched = True
            process_zero = False
        if launched and job is not None:
            try:
                job.terminate()
                accounting = job.wait_for_zero(
                    deadline=time.monotonic() + 5.0
                )
                process_zero = True
            except BaseException:
                accounting = None
                process_zero = False
                cleanup_error = True
        terminal = (
            "controller_interrupted",
            "controller_interrupted",
            "launched" if launched else "no_launch",
        )
    finally:
        if child is not None:
            try:
                child.close()
            except BaseException:
                handles_closed = False
                cleanup_error = True
        if pipes is not None:
            try:
                child_ends_open = any(
                    not handle.closed
                    for handle in (
                        pipes.stdin_child,
                        pipes.stdout_child,
                        pipes.stderr_child,
                    )
                )
            except BaseException:
                child_ends_open = True
                handles_closed = False
                cleanup_error = True
            if child_ends_open:
                try:
                    pipes.close_child_ends()
                except BaseException:
                    handles_closed = False
                    cleanup_error = True
            try:
                pipes.close_parent_ends()
            except BaseException:
                handles_closed = False
                cleanup_error = True
        if drain is not None:
            try:
                if not drain.join(1.0):
                    raw_output_discarded = False
                    cleanup_error = True
            except BaseException:
                raw_output_discarded = False
                cleanup_error = True
        if job is not None:
            try:
                job.close()
            except BaseException:
                handles_closed = False
                cleanup_error = True
    if not process_zero or not handles_closed or not raw_output_discarded:
        cleanup_error = True
    if cleanup_error:
        terminal = (
            "cleanup_failed",
            "process_cleanup_failed",
            "launched" if launched else "no_launch",
        )
        accounting = None
    if terminal is None:
        _fail("process_wait_failed")
    if terminal == ("process_error", "process_create_failed", "launched"):
        return _RequestOnlyCreateFailure(
            step.ordinal,
            process_zero,
            handles_closed,
            raw_output_discarded,
        )
    public_accounting = accounting if terminal[0] in {"pass", "fail"} else None
    result = _flatten_accounting(
        step.ordinal,
        terminal[0],
        terminal[1],
        terminal[2],
        public_accounting,
    )
    return _StepExecution(
        result,
        accounting,
        process_zero,
        handles_closed,
        raw_output_discarded,
    )


def _aggregate(
    results: tuple[RunnerProcessStepResultV1, ...],
) -> tuple[int | None, int | None, int | None]:
    if not results or any(result.cpu_time_ms is None for result in results):
        return None, None, None
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
        if step_result.cpu_time_ms is not None and (
            step_result.cpu_time_ms > step.cpu_seconds * 1000
            or step_result.peak_job_memory_bytes is None
            or step_result.peak_job_memory_bytes
            > step.memory_mib * 1_048_576
        ):
            _fail("process_tree_unproved")


def run_process_request(
    request: RunnerProcessRequestV1,
) -> RunnerProcessResultV1:
    """Execute one fully admitted request and return only its closed result."""

    admitted = _admit_request(request)
    started = time.monotonic()
    completed: list[RunnerProcessStepResultV1] = []
    launched_any = False
    process_zero = True
    handles_closed = True
    raw_output_discarded = True
    for admitted_step in admitted.steps:
        try:
            cancelled = request.cancel_signal.requested()
        except BaseException:
            cancelled = None
        if cancelled is not False:
            duration = min(
                MAX_RESULT_INTEGER,
                max(0, int((time.monotonic() - started) * 1000)),
            )
            if launched_any:
                return _result(
                    request,
                    outcome=(
                        "cancelled"
                        if cancelled is True
                        else "controller_interrupted"
                    ),
                    reason=(
                        "cancelled"
                        if cancelled is True
                        else "controller_interrupted"
                    ),
                    launch_state="launched",
                    failed_step_ordinal=None,
                    duration_ms=duration,
                    steps=tuple(completed),
                    process_zero=process_zero,
                    handles_closed=handles_closed,
                    raw_output_discarded=raw_output_discarded,
                )
            return _result(
                request,
                outcome="blocked_prelaunch",
                reason=(
                    "cancelled"
                    if cancelled is True
                    else "controller_interrupted"
                ),
                launch_state="no_launch",
                failed_step_ordinal=None,
                duration_ms=duration,
                steps=(),
                process_zero=True,
                handles_closed=True,
                raw_output_discarded=True,
            )
        execution = _execute_step(
            admitted=admitted_step,
            admitted_request=admitted,
        )
        if isinstance(execution, _RequestOnlyCreateFailure):
            process_zero = process_zero and execution.process_zero
            handles_closed = handles_closed and execution.handles_closed
            raw_output_discarded = (
                raw_output_discarded
                and execution.raw_output_discarded
            )
            duration = min(
                MAX_RESULT_INTEGER,
                max(0, int((time.monotonic() - started) * 1000)),
            )
            return _result(
                request,
                outcome="process_error",
                reason="process_create_failed",
                launch_state="launched",
                failed_step_ordinal=execution.ordinal,
                duration_ms=duration,
                steps=tuple(completed),
                process_zero=process_zero,
                handles_closed=handles_closed,
                raw_output_discarded=raw_output_discarded,
                accounting_complete=False,
            )
        step_result = execution.result
        process_zero = process_zero and execution.process_zero
        handles_closed = handles_closed and execution.handles_closed
        raw_output_discarded = (
            raw_output_discarded
            and execution.raw_output_discarded
        )
        if step_result.launch_state == "no_launch" and not launched_any:
            duration = min(
                MAX_RESULT_INTEGER,
                max(0, int((time.monotonic() - started) * 1000)),
            )
            return _result(
                request,
                outcome=step_result.outcome,
                reason=step_result.reason,
                launch_state="no_launch",
                failed_step_ordinal=None,
                duration_ms=duration,
                steps=(),
                process_zero=process_zero,
                handles_closed=handles_closed,
                raw_output_discarded=raw_output_discarded,
            )
        if step_result.launch_state == "no_launch" and launched_any:
            if step_result.outcome in {
                "controller_interrupted",
                "cleanup_failed",
            }:
                duration = min(
                    MAX_RESULT_INTEGER,
                    max(0, int((time.monotonic() - started) * 1000)),
                )
                return _result(
                    request,
                    outcome=step_result.outcome,
                    reason=step_result.reason,
                    launch_state="launched",
                    failed_step_ordinal=None,
                    duration_ms=duration,
                    steps=tuple(completed),
                    process_zero=process_zero,
                    handles_closed=handles_closed,
                    raw_output_discarded=raw_output_discarded,
                )
            completed.append(step_result)
            duration = min(
                MAX_RESULT_INTEGER,
                max(0, int((time.monotonic() - started) * 1000)),
            )
            return _result(
                request,
                outcome="process_error",
                reason=step_result.reason,
                launch_state="launched",
                failed_step_ordinal=step_result.ordinal,
                duration_ms=duration,
                steps=tuple(completed),
                process_zero=process_zero,
                handles_closed=handles_closed,
                raw_output_discarded=raw_output_discarded,
            )
        completed.append(step_result)
        launched_any = (
            launched_any or step_result.launch_state == "launched"
        )
        if step_result.outcome != "pass":
            duration = min(
                MAX_RESULT_INTEGER,
                max(0, int((time.monotonic() - started) * 1000)),
            )
            return _result(
                request,
                outcome=step_result.outcome,
                reason=step_result.reason,
                launch_state=(
                    "launched" if launched_any else "no_launch"
                ),
                failed_step_ordinal=step_result.ordinal,
                duration_ms=duration,
                steps=tuple(completed),
                process_zero=process_zero,
                handles_closed=handles_closed,
                raw_output_discarded=raw_output_discarded,
            )
    duration = min(
        MAX_RESULT_INTEGER,
        max(0, int((time.monotonic() - started) * 1000)),
    )
    return _result(
        request,
        outcome="pass",
        reason=None,
        launch_state="launched",
        failed_step_ordinal=None,
        duration_ms=duration,
        steps=tuple(completed),
        process_zero=process_zero,
        handles_closed=handles_closed,
        raw_output_discarded=raw_output_discarded,
    )


__all__ = [
    "EXECUTABLE_ID",
    "FIXED_BOOTSTRAP",
    "MAX_COMMAND_LINE_UTF16_UNITS",
    "MAX_OUTPUT_BYTES",
    "ProcessRunResult",
    "RunnerCancelSignal",
    "RunnerProcessError",
    "RunnerProcessRequestV1",
    "RunnerProcessResultV1",
    "RunnerProcessStepResultV1",
    "RunnerProcessStepV1",
    "build_clean_environment",
    "quote_windows_argument",
    "quote_windows_argv",
    "run_process_request",
]
