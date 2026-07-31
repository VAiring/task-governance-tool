"""Pure reducers for the frozen M20.3 and M20.4 fresh-agent trials.

The module consumes only isolated trial material.  It emits schema-validated
observation records through :func:`tools.m20_observation.build_observation` and
never retains repository paths, command strings, or subprocess streams in a
reduced value.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from tools.m20_observation import (
    MAX_SIGNED_32,
    M20_4_EPISODE_PLAN_CANONICAL_SHA256,
    M20ObservationError,
    PROTOCOL_CANONICAL_SHA256,
    build_observation,
    canonical_json_bytes,
    reduce_task_show_state,
    validate_control_bundle,
)


MAX_GIT_OUTPUT_BYTES = 8_388_608
MAX_SOURCE_BYTES = 4_194_304
MAX_CHANGED_PATHS = 4_096
MAX_TASKGOV_OPERATIONS = 64
MAX_VERIFICATION_STEPS = 16
CHECKER_TIMEOUT_SECONDS = 300
PROCESS_TREE_TERMINATION_SECONDS = 10
WINDOWS_CREATE_SUSPENDED = 0x00000004

_SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]{1,240}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9._-]{1,64}\Z")
_TASK_ID = re.compile(r"tg_task_[0-9a-f]{16}\Z")
_HUNK = re.compile(
    rb"^@@ -(?P<old_start>[0-9]+)(?:,(?P<old_count>[0-9]+))? "
    rb"\+(?P<new_start>[0-9]+)(?:,(?P<new_count>[0-9]+))? @@"
)
_TEST_MODULE = re.compile(r"tests/test_[A-Za-z0-9_]+\.py\Z")


@dataclass(frozen=True)
class CheckerSpec:
    label: str
    arguments: tuple[str, ...]


FIXED_CHECKERS: dict[str, CheckerSpec] = {
    "vp_cli_contract": CheckerSpec(
        "cli_contract",
        ("-B", "-m", "unittest", "tests.test_cli_envelope"),
    ),
    "vp_state_transition": CheckerSpec(
        "state_transition",
        (
            "-B",
            "-m",
            "unittest",
            (
                "tests.test_task_edit.TaskEditTests."
                "test_task_edit_pauses_and_resumes_with_distinct_reason_and_history"
            ),
        ),
    ),
    "vp_release_contract": CheckerSpec(
        "release_contract",
        ("-B", "tools/release_contract.py", "--repo", "."),
    ),
}

TASKGOV_WRITE_LEAVES = frozenset(
    {
        "setup",
        "task.add",
        "task.checkpoint",
        "task.edit",
        "task.complete",
        "handoff.record",
        "handoff.withdraw",
        "review.target.set",
        "review.receipt.add",
        "review.finding.add",
        "review.finding.resolve",
    }
)
TASKGOV_READ_LEAVES = frozenset(
    {
        "doctor",
        "task.list",
        "task.next",
        "task.current",
        "task.effort",
        "task.show",
        "handoff.list",
        "handoff.show",
        "review.prepare",
    }
)
TASKGOV_LEAVES = TASKGOV_WRITE_LEAVES | TASKGOV_READ_LEAVES


@dataclass(frozen=True)
class SourceSide:
    data: bytes | None
    exists: bool
    non_text: bool
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class ChangeDetail:
    path: str
    baseline: SourceSide
    final: SourceSide
    old_lines: frozenset[int]
    new_lines: frozenset[int]
    added: int | None
    deleted: int | None
    hunk_unavailable_reason: str | None = None


@dataclass(frozen=True)
class BoundarySnapshot:
    """Safe in-memory M20.4 boundary projection.

    It intentionally contains no repository path, command, prose, or stream
    body.  ``metric_unknowns`` maps only ``files``, ``modules``, or ``lines``
    to a stable M20 reason.
    """

    scenario_id: str
    trial_id: str
    boundary_id: str
    files: int | None
    modules: int | None
    lines: int | None
    metric_unknowns: tuple[tuple[str, str], ...]
    task_states: tuple[tuple[str, str, int, int], ...]
    observer_log_position: int
    observer_verification_position: int = 0


class _MetricUnavailable(Exception):
    """Carry one field-local reason without invalidating sibling evidence."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _fail(code: str) -> None:
    raise M20ObservationError(code)


def _exact_keys(value: Any, expected: Iterable[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        _fail("parse_failed")
    return value


def _integer(value: Any, *, maximum: int = MAX_SIGNED_32) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("parse_failed")
    if not 0 <= value <= maximum:
        _fail("parse_failed")
    return value


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _fail("parse_failed")
    return value


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_PATH.fullmatch(value) is None:
        _fail("source_drift")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or not path.name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail("source_drift")
    return value


def _safe_environment() -> dict[str, str]:
    allowed = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
    )
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return result


def _process_creation_options() -> dict[str, Any]:
    if os.name == "nt":
        process_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
        if type(process_group) is not int or process_group == 0:
            _fail("source_missing")
        return {"creationflags": process_group | WINDOWS_CREATE_SUSPENDED}
    return {"start_new_session": True}


class _ProcessTree:
    """Own one checker process group or Windows kill-on-close Job Object."""

    def __init__(self, process: subprocess.Popen[Any]):
        self.process = process
        self._kernel32: Any = None
        self._job: Any = None
        if os.name == "nt":
            self._attach_windows_job()

    def _attach_windows_job(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = wintypes.LONG

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            self._abort_unowned_process()
            _fail("source_missing")
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        configured = kernel32.SetInformationJobObject(
            job,
            9,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        assigned = configured and kernel32.AssignProcessToJobObject(
            job,
            wintypes.HANDLE(int(self.process._handle)),
        )
        resumed = assigned and ntdll.NtResumeProcess(
            wintypes.HANDLE(int(self.process._handle)),
        ) == 0
        if not resumed:
            kernel32.CloseHandle(job)
            self._abort_unowned_process()
            _fail("source_missing")
        self._kernel32 = kernel32
        self._job = job

    def _abort_unowned_process(self) -> None:
        try:
            self.process.kill()
        except OSError:
            pass
        try:
            self.process.wait(timeout=PROCESS_TREE_TERMINATION_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            _fail("source_missing")

    def terminate(self) -> None:
        tree_error = False
        if os.name == "nt":
            if self._job is None or not self._kernel32.TerminateJobObject(
                self._job,
                1,
            ):
                tree_error = True
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                tree_error = True
        if tree_error:
            try:
                self.process.kill()
            except OSError:
                pass
        try:
            self.process.wait(timeout=PROCESS_TREE_TERMINATION_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.process.kill()
                self.process.wait(timeout=PROCESS_TREE_TERMINATION_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                _fail("source_missing")
            tree_error = True
        if tree_error:
            _fail("source_missing")

    def close(self) -> None:
        if self._job is not None:
            job = self._job
            self._job = None
            if not self._kernel32.CloseHandle(job):
                _fail("source_missing")


def _new_process_tree(process: subprocess.Popen[Any]) -> _ProcessTree:
    """Internal injection seam for platform process-tree ownership tests."""

    return _ProcessTree(process)


def _git(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    accepted: frozenset[int] = frozenset({0}),
) -> tuple[int, bytes]:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo_root}",
                "-c",
                "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repo_root),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            shell=False,
            check=False,
            env=_safe_environment(),
        )
    except subprocess.TimeoutExpired:
        _fail("timeout")
    except OSError:
        _fail("source_missing")
    if result.returncode not in accepted:
        _fail("source_drift")
    if len(result.stdout) > MAX_GIT_OUTPUT_BYTES:
        _fail("cap_exceeded")
    return result.returncode, result.stdout


def _nul_paths(raw: bytes) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = raw.split(b"\0")
    if parts[-1] != b"":
        _fail("parse_failed")
    paths: list[str] = []
    for part in parts[:-1]:
        try:
            value = part.decode("utf-8", errors="strict")
        except UnicodeError:
            _fail("parse_failed")
        paths.append(_safe_relative(value))
    if len(paths) > MAX_CHANGED_PATHS or len(paths) != len(set(paths)):
        _fail("cap_exceeded" if len(paths) > MAX_CHANGED_PATHS else "source_drift")
    return tuple(paths)


def _resolve_root(repo_root: Path) -> Path:
    try:
        root = Path(repo_root).resolve(strict=True)
    except OSError:
        _fail("source_missing")
    if not root.is_dir() or (root / ".git").is_symlink():
        _fail("source_drift")
    return root


def _resolve_file(root: Path, relative: str) -> Path:
    lexical = root / Path(*PurePosixPath(_safe_relative(relative)).parts)
    try:
        resolved_parent = lexical.parent.resolve(strict=True)
        resolved_parent.relative_to(root)
    except (OSError, ValueError):
        _fail("source_missing")
    return resolved_parent / lexical.name


def _verify_baseline(root: Path, baseline_revision: str) -> str:
    if not isinstance(baseline_revision, str) or re.fullmatch(
        r"[0-9a-f]{40}", baseline_revision
    ) is None:
        _fail("source_drift")
    _, raw = _git(root, ("rev-parse", "--verify", f"{baseline_revision}^{{commit}}"))
    try:
        observed = raw.decode("ascii", errors="strict").strip()
    except UnicodeError:
        _fail("parse_failed")
    if observed != baseline_revision:
        _fail("source_drift")
    return observed


def _baseline_side(root: Path, baseline: str, path: str) -> SourceSide:
    code, entry = _git(
        root,
        ("ls-tree", "-z", baseline, "--", path),
        accepted=frozenset({0}),
    )
    del code
    if not entry:
        return SourceSide(None, False, False)
    if not entry.endswith(b"\0") or entry.count(b"\0") != 1:
        _fail("source_drift")
    header, observed_path = entry[:-1].split(b"\t", 1)
    try:
        mode, object_type, object_id = header.decode("ascii").split(" ")
        decoded_path = observed_path.decode("utf-8", errors="strict")
    except (UnicodeError, ValueError):
        _fail("parse_failed")
    if decoded_path != path or object_type != "blob":
        return SourceSide(None, True, True)
    if mode == "120000":
        return SourceSide(None, True, True)
    if re.fullmatch(r"[0-9a-f]{40,64}", object_id) is None:
        _fail("source_drift")
    _, size_raw = _git(root, ("cat-file", "-s", object_id))
    try:
        size = int(size_raw.decode("ascii", errors="strict").strip())
    except (UnicodeError, ValueError):
        _fail("parse_failed")
    if size < 0:
        _fail("source_drift")
    if size > MAX_SOURCE_BYTES:
        return SourceSide(None, True, False, "cap_exceeded")
    _, data = _git(root, ("show", f"{baseline}:{path}"))
    if len(data) > MAX_SOURCE_BYTES:
        return SourceSide(None, True, False, "cap_exceeded")
    return SourceSide(data, True, b"\0" in data)


def _final_side(root: Path, path: str) -> SourceSide:
    lexical = _resolve_file(root, path)
    try:
        info = lexical.lstat()
    except FileNotFoundError:
        return SourceSide(None, False, False)
    except OSError:
        _fail("source_missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return SourceSide(None, True, True)
    if info.st_size > MAX_SOURCE_BYTES:
        return SourceSide(None, True, False, "cap_exceeded")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
    except OSError:
        _fail("source_missing")
    data = bytearray()
    try:
        opened = os.fstat(descriptor)
        if _source_identity(opened) != _source_identity(info):
            _fail("source_drift")
        while len(data) <= MAX_SOURCE_BYTES:
            block = os.read(
                descriptor,
                min(65_536, MAX_SOURCE_BYTES + 1 - len(data)),
            )
            if not block:
                break
            data.extend(block)
        after_opened = os.fstat(descriptor)
        after_path = lexical.lstat()
        if (
            _source_identity(after_opened) != _source_identity(opened)
            or _source_identity(after_path) != _source_identity(info)
        ):
            _fail("source_drift")
    except OSError:
        _fail("source_missing")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            _fail("source_missing")
    if len(data) > MAX_SOURCE_BYTES:
        return SourceSide(None, True, False, "cap_exceeded")
    raw = bytes(data)
    return SourceSide(raw, True, b"\0" in raw)


def _source_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_size,
        info.st_mtime_ns,
    )


def _decode_text(side: SourceSide) -> str | None:
    if not side.exists:
        return ""
    if side.non_text or side.data is None:
        return None
    try:
        text = side.data.decode("utf-8", errors="strict")
    except UnicodeError:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _metric_text(side: SourceSide) -> str:
    if side.unavailable_reason is not None:
        raise _MetricUnavailable(side.unavailable_reason)
    text = _decode_text(side)
    if text is None:
        raise _MetricUnavailable("not_observable")
    return text


def _metric_value(factory: Any) -> tuple[int | None, str | None]:
    try:
        value = factory()
    except _MetricUnavailable as error:
        return (
            MAX_SIGNED_32 if error.reason == "cap_exceeded" else None,
            error.reason,
        )
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("parse_failed")
    if value > MAX_SIGNED_32:
        return MAX_SIGNED_32, "cap_exceeded"
    return value, None


def _numstat(root: Path, baseline: str) -> dict[str, tuple[int | None, int | None]]:
    _, raw = _git(
        root,
        (
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--numstat",
            "-z",
            baseline,
            "--",
        ),
    )
    result: dict[str, tuple[int | None, int | None]] = {}
    if not raw:
        return result
    fields = raw.split(b"\0")
    if fields[-1] != b"":
        _fail("parse_failed")
    for row in fields[:-1]:
        parts = row.split(b"\t", 2)
        if len(parts) != 3:
            _fail("parse_failed")
        try:
            path = _safe_relative(parts[2].decode("utf-8", errors="strict"))
            added = None if parts[0] == b"-" else int(parts[0])
            deleted = None if parts[1] == b"-" else int(parts[1])
        except (UnicodeError, ValueError):
            _fail("parse_failed")
        if path in result:
            _fail("source_drift")
        result[path] = (added, deleted)
    return result


def _hunk_lines(
    root: Path,
    baseline: str,
    path: str,
) -> tuple[frozenset[int], frozenset[int], str | None]:
    try:
        _, patch = _git(
            root,
            (
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--unified=0",
                "--no-color",
                baseline,
                "--",
                path,
            ),
        )
    except M20ObservationError as error:
        if error.code == "cap_exceeded":
            return frozenset(), frozenset(), "cap_exceeded"
        raise
    old_lines: set[int] = set()
    new_lines: set[int] = set()
    for line in patch.splitlines():
        match = _HUNK.match(line)
        if match is None:
            continue
        old_start = int(match.group("old_start"))
        new_start = int(match.group("new_start"))
        old_count = int(match.group("old_count") or b"1")
        new_count = int(match.group("new_count") or b"1")
        old_lines.update(range(old_start, old_start + old_count))
        new_lines.update(range(new_start, new_start + new_count))
    return frozenset(old_lines), frozenset(new_lines), None


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


class Materialization:
    """One bounded no-renames comparison against an exact baseline commit."""

    def __init__(self, repo_root: Path, baseline_revision: str) -> None:
        self.root = _resolve_root(repo_root)
        self.baseline = _verify_baseline(self.root, baseline_revision)
        _, tracked_raw = _git(
            self.root,
            (
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--name-only",
                "-z",
                self.baseline,
                "--",
            ),
        )
        _, untracked_raw = _git(
            self.root,
            ("ls-files", "--others", "--exclude-standard", "-z"),
        )
        paths = tuple(
            sorted(
                set(_nul_paths(tracked_raw)) | set(_nul_paths(untracked_raw)),
                key=lambda value: value.encode("ascii"),
            )
        )
        if len(paths) > MAX_CHANGED_PATHS:
            _fail("cap_exceeded")
        stats = _numstat(self.root, self.baseline)
        untracked = set(_nul_paths(untracked_raw))
        details: dict[str, ChangeDetail] = {}
        for path in paths:
            baseline_side = _baseline_side(self.root, self.baseline, path)
            final_side = _final_side(self.root, path)
            added, deleted = stats.get(path, (None, None))
            hunk_reason: str | None = None
            side_reason = next(
                (
                    side.unavailable_reason
                    for side in (baseline_side, final_side)
                    if side.unavailable_reason is not None
                ),
                None,
            )
            side_non_text = any(
                side.exists and _decode_text(side) is None
                for side in (baseline_side, final_side)
            )
            if path in untracked:
                final_text = _decode_text(final_side)
                if final_text is None:
                    added = deleted = None
                    new_lines = frozenset()
                    hunk_reason = side_reason or "not_observable"
                else:
                    added = _line_count(final_text)
                    deleted = 0
                    new_lines = frozenset(range(1, added + 1))
                old_lines = frozenset()
            elif side_reason is not None or side_non_text:
                old_lines = new_lines = frozenset()
                added = deleted = None
                hunk_reason = side_reason or "not_observable"
            else:
                old_lines, new_lines, hunk_reason = _hunk_lines(
                    self.root,
                    self.baseline,
                    path,
                )
                if hunk_reason is not None:
                    added = deleted = None
            details[path] = ChangeDetail(
                path=path,
                baseline=baseline_side,
                final=final_side,
                old_lines=old_lines,
                new_lines=new_lines,
                added=added,
                deleted=deleted,
                hunk_unavailable_reason=hunk_reason,
            )
        self.details = details

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(self.details)

    def side_text(self, path: str, *, final: bool) -> str:
        detail = self.details.get(path)
        if detail is not None:
            side = detail.final if final else detail.baseline
        else:
            side = (
                _final_side(self.root, path)
                if final
                else _baseline_side(self.root, self.baseline, path)
            )
        return _metric_text(side)

    def line_metric(self, paths: Iterable[str]) -> tuple[int | None, str | None]:
        def measure() -> int:
            total = 0
            for path in paths:
                detail = self.details[path]
                for side in (detail.baseline, detail.final):
                    if side.unavailable_reason is not None:
                        raise _MetricUnavailable(side.unavailable_reason)
                    if side.exists and _decode_text(side) is None:
                        raise _MetricUnavailable("not_observable")
                if detail.hunk_unavailable_reason is not None:
                    raise _MetricUnavailable(detail.hunk_unavailable_reason)
                if detail.added is None or detail.deleted is None:
                    raise _MetricUnavailable("not_observable")
                total += detail.added + detail.deleted
                if total > MAX_SIGNED_32:
                    raise _MetricUnavailable("cap_exceeded")
            return total

        return _metric_value(measure)

    def line_total(self, paths: Iterable[str]) -> int | None:
        value, reason = self.line_metric(paths)
        return None if reason is not None else value


def _occurrence_intersects(text: str, needle: str, changed: frozenset[int]) -> bool:
    start = 0
    while True:
        offset = text.find(needle, start)
        if offset < 0:
            return False
        first = text.count("\n", 0, offset) + 1
        last = first + needle.count("\n")
        if any(line in changed for line in range(first, last + 1)):
            return True
        start = offset + 1


def _probe_fanout(
    material: Materialization,
    probes: Sequence[Mapping[str, Any]],
) -> int:
    owners: set[str] = set()
    for probe in probes:
        item = _exact_keys(probe, {"probe_id", "owner_slot", "selector", "needle_lf"})
        selector = _safe_relative(item["selector"])
        needle = item["needle_lf"]
        if not isinstance(needle, str) or not needle:
            _fail("source_drift")
        detail = material.details.get(selector)
        if detail is None:
            continue
        if detail.hunk_unavailable_reason is not None:
            raise _MetricUnavailable(detail.hunk_unavailable_reason)
        baseline_text = _metric_text(detail.baseline)
        final_text = _metric_text(detail.final)
        baseline_found = needle in baseline_text
        final_found = needle in final_text
        if not baseline_found and not final_found:
            _fail("source_drift")
        if (
            baseline_found
            and _occurrence_intersects(baseline_text, needle, detail.old_lines)
            or final_found
            and _occurrence_intersects(final_text, needle, detail.new_lines)
        ):
            owners.add(_identifier(item["owner_slot"]))
    return len(owners)


def _maintenance_owners(
    changed_paths: Iterable[str],
    selectors: Sequence[Mapping[str, Any]],
) -> set[str]:
    owners: set[str] = set()
    changed = tuple(changed_paths)
    for raw in selectors:
        item = _exact_keys(raw, {"owner_slot", "selector"})
        owner = _identifier(item["owner_slot"])
        selector = _safe_relative(item["selector"])
        if any(path == selector or path.startswith(selector + "/") for path in changed):
            owners.add(owner)
    return owners


def _grep_paths(material: Materialization, needle: str, *, final: bool) -> set[str]:
    arguments = ["grep", "-l", "-z", "-F", "-e", needle]
    if not final:
        arguments.append(material.baseline)
    arguments.append("--")
    _code, raw = _git(material.root, tuple(arguments), accepted=frozenset({0, 1}))
    if raw and not final:
        prefix = material.baseline.encode("ascii") + b":"
        fields = raw.split(b"\0")
        if fields[-1] != b"" or any(not field.startswith(prefix) for field in fields[:-1]):
            _fail("parse_failed")
        raw = b"\0".join(field[len(prefix) :] for field in fields[:-1]) + b"\0"
    paths = set(_nul_paths(raw)) if raw else set()
    if final:
        _, untracked_raw = _git(
            material.root, ("ls-files", "--others", "--exclude-standard", "-z")
        )
        for path in _nul_paths(untracked_raw):
            text = _metric_text(_final_side(material.root, path))
            if needle in text:
                paths.add(path)
    result: set[str] = set()
    for path in paths:
        text = material.side_text(path, final=final)
        if needle in text:
            result.add(path)
    return result


def _duplicate_contract_locations(
    material: Materialization,
    probes: Sequence[Mapping[str, Any]],
) -> int:
    largest = 0
    for raw in probes:
        item = _exact_keys(raw, {"probe_id", "owner_slot", "selector", "needle_lf"})
        needle = item["needle_lf"]
        if not isinstance(needle, str) or not needle:
            _fail("source_drift")
        baseline = _grep_paths(material, needle, final=False)
        final = _grep_paths(material, needle, final=True)
        if len(final) >= 2 and len(final) > len(baseline):
            largest = max(largest, len(final))
    return largest


def _ast_nodes(source: str) -> tuple[tuple[str, ast.AST], ...]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        _fail("parse_failed")
    result: list[tuple[str, ast.AST]] = []

    def visit(body: Sequence[ast.stmt], parents: tuple[str, ...]) -> None:
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join((*parents, node.name))
                result.append((qualified, node))
                visit(node.body, (*parents, node.name))

    visit(tree.body, ())
    return tuple(result)


def _test_occurrences(source: str, path: str) -> tuple[tuple[str, ast.AST], ...]:
    grouped: dict[str, list[ast.AST]] = {}
    for qualified, node in _ast_nodes(source):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            grouped.setdefault(qualified, []).append(node)
    result: list[tuple[str, ast.AST]] = []
    for qualified in sorted(grouped, key=lambda value: value.encode("utf-8")):
        nodes = sorted(
            grouped[qualified],
            key=lambda node: (node.lineno, node.col_offset),
        )
        for ordinal, node in enumerate(nodes):
            result.append((f"{path}:{qualified}:{ordinal}", node))
    return tuple(result)


def _changed_test_cases(material: Materialization, test_paths: Sequence[str]) -> int:
    occurrences: set[str] = set()
    for path in test_paths:
        detail = material.details[path]
        if detail.hunk_unavailable_reason is not None:
            raise _MetricUnavailable(detail.hunk_unavailable_reason)
        for side, lines in (
            (detail.baseline, detail.old_lines),
            (detail.final, detail.new_lines),
        ):
            text = _metric_text(side)
            if not side.exists:
                continue
            for key, node in _test_occurrences(text, path):
                end = getattr(node, "end_lineno", None)
                if end is None:
                    _fail("parse_failed")
                if any(line in lines for line in range(node.lineno, end + 1)):
                    occurrences.add(key)
    return len(occurrences)


def _tree_test_paths(material: Materialization, *, final: bool) -> tuple[str, ...]:
    if final:
        _, raw = _git(
            material.root,
            ("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        )
        candidates = _nul_paths(raw)
        return tuple(
            path
            for path in candidates
            if _TEST_MODULE.fullmatch(path) is not None
            and _final_side(material.root, path).exists
        )
    _, raw = _git(
        material.root,
        ("ls-tree", "-r", "-z", "--name-only", material.baseline, "--", "tests"),
    )
    return tuple(path for path in _nul_paths(raw) if _TEST_MODULE.fullmatch(path) is not None)


def _fixture_copy_groups(
    material: Materialization,
    probes: Sequence[Mapping[str, Any]],
) -> int:
    if not probes:
        return 0
    baseline_paths = _tree_test_paths(material, final=False)
    final_paths = _tree_test_paths(material, final=True)
    baseline_fingerprints: dict[str, int] = {}
    final_fingerprints: dict[str, int] = {}

    for final, paths, counts in (
        (False, baseline_paths, baseline_fingerprints),
        (True, final_paths, final_fingerprints),
    ):
        for path in paths:
            text = material.side_text(path, final=final)
            for _key, node in _test_occurrences(text, path):
                fingerprint = hashlib.sha256(
                    ast.dump(
                        node,
                        annotate_fields=True,
                        include_attributes=False,
                    ).encode("utf-8")
                ).hexdigest()
                counts[fingerprint] = counts.get(fingerprint, 0) + 1

    selected: set[str] = set()
    for raw in probes:
        item = _exact_keys(raw, {"probe_id", "selector", "qualified_name"})
        selector = _safe_relative(item["selector"])
        qualified = item["qualified_name"]
        if not isinstance(qualified, str):
            _fail("parse_failed")
        text = material.side_text(selector, final=False)
        matches = [node for name, node in _ast_nodes(text) if name == qualified]
        if len(matches) != 1:
            _fail("source_drift")
        selected.add(
            hashlib.sha256(
                ast.dump(
                    matches[0],
                    annotate_fields=True,
                    include_attributes=False,
                ).encode("utf-8")
            ).hexdigest()
        )
    return sum(
        final_fingerprints.get(fingerprint, 0) >= 2
        and final_fingerprints.get(fingerprint, 0)
        > baseline_fingerprints.get(fingerprint, 0)
        for fingerprint in selected
    )


def reduce_verification_log(
    verification_log: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """Reduce the observer's safe ordinal/kind log to protocol steps."""

    if not isinstance(verification_log, Sequence) or isinstance(
        verification_log, (str, bytes)
    ) or len(verification_log) > 16:
        _fail("parse_failed")
    steps: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(verification_log, 1):
        item = _exact_keys(raw, {"ordinal", "kind", "duration_ms", "result"})
        if item["ordinal"] != ordinal:
            _fail("source_drift")
        kind = item["kind"]
        if kind not in {"focused", "lane", "all", "other"}:
            _fail("parse_failed")
        observed_result = item["result"]
        duration = _integer(item["duration_ms"], maximum=300_000)
        if observed_result == "timeout":
            if duration != 300_000:
                _fail("parse_failed")
            capped = True
            result = "timeout"
        elif observed_result in {"success", "failure"}:
            if duration > 299_999:
                _fail("parse_failed")
            capped = False
            result = observed_result
        else:
            _fail("parse_failed")
        steps.append(
            {
                "ordinal": ordinal,
                "kind": kind,
                "duration_ms": duration,
                "duration_capped": capped,
                "result": result,
            }
        )
    all_count = sum(step["kind"] == "all" for step in steps)
    unknowns: list[dict[str, Any]] = []
    if all_count >= 2:
        escalation = "repeated_all"
    elif steps and steps[0]["kind"] == "all":
        escalation = "all_first"
    elif steps:
        escalation = "proportional"
    else:
        escalation = "unknown"
        unknowns.append(
            {"field": "data.verification_escalation", "reasons": ["not_observable"]}
        )
    return steps, escalation, unknowns


def _checker_outcome(repo_root: Path, spec: CheckerSpec) -> str:
    try:
        process = subprocess.Popen(
            [sys.executable, *spec.arguments],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            env=_safe_environment(),
            **_process_creation_options(),
        )
    except OSError:
        return "unavailable"
    tree = _new_process_tree(process)
    try:
        try:
            return_code = process.wait(timeout=CHECKER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            tree.terminate()
            return "timeout"
        except OSError:
            tree.terminate()
            return "unavailable"
    finally:
        tree.close()
    return "pass" if return_code == 0 else "fail"


def target_change_sensitivity(
    repo_root: Path,
    *,
    scenario_id: str,
    manifest: Mapping[str, Any],
) -> tuple[str, str | None]:
    """Run the fixed accepted/mutated checker pair and always restore bytes."""

    spec = FIXED_CHECKERS.get(scenario_id)
    if spec is None:
        _fail("source_drift")
    target = _exact_keys(
        manifest["target_change"],
        {"selector", "before_lf", "after_lf", "verification_label"},
    )
    if target["verification_label"] != spec.label:
        _fail("source_drift")
    root = _resolve_root(repo_root)
    path = _resolve_file(root, target["selector"])
    before = target["before_lf"]
    after = target["after_lf"]
    if not isinstance(before, str) or not isinstance(after, str) or before == after:
        _fail("source_drift")
    side = _final_side(root, target["selector"])
    existed = side.exists
    if side.unavailable_reason is not None:
        return "unknown", side.unavailable_reason
    if existed:
        text = _decode_text(side)
        if text is None or side.data is None:
            return "unknown", "not_observable"
        original = side.data
    else:
        original = b""
        text = ""
    if "\r" in text:
        _fail("source_drift")
    if after:
        if text.count(after) != 1:
            _fail("source_drift")
        mutated = text.replace(after, before, 1).encode("utf-8")
    else:
        if text != "":
            _fail("source_drift")
        mutated = before.encode("utf-8")

    accepted = _checker_outcome(root, spec)
    if accepted == "timeout":
        return "unknown", "timeout"
    if accepted != "pass":
        return "unknown", "source_drift"
    try:
        path.write_bytes(mutated)
        changed = _checker_outcome(root, spec)
    except OSError:
        changed = "unavailable"
    finally:
        try:
            if existed:
                path.write_bytes(original)
            elif path.exists():
                path.unlink()
        except OSError:
            _fail("source_missing")
    if changed == "fail":
        return "detected", None
    if changed == "pass":
        return "not_detected", None
    if changed == "timeout":
        return "unknown", "timeout"
    return "unknown", "source_missing"


def _state_record(
    protocol: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    trial_id: str,
    expected_task_id: str,
    before_envelope: Mapping[str, Any],
    after_envelope: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(expected_task_id, str)
        or _TASK_ID.fullmatch(expected_task_id) is None
    ):
        _fail("source_drift")
    observed_task_ids: list[str] = []
    for envelope in (before_envelope, after_envelope):
        data = envelope.get("data") if isinstance(envelope, Mapping) else None
        task = data.get("task") if isinstance(data, Mapping) else None
        task_id = task.get("task_id") if isinstance(task, Mapping) else None
        if not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None:
            _fail("parse_failed")
        observed_task_ids.append(task_id)
    if observed_task_ids != [expected_task_id, expected_task_id]:
        _fail("source_drift")
    return build_observation(
        protocol,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        evidence_class="machine_observed",
        channel="state_projection",
        record_key="state_pair",
        payload={
            "before": reduce_task_show_state(before_envelope),
            "after": reduce_task_show_state(after_envelope),
        },
    )


def build_state_pair_observation(
    protocol: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    trial_id: str,
    expected_task_id: str,
    before_envelope: Mapping[str, Any],
    after_envelope: Mapping[str, Any],
) -> dict[str, Any]:
    """Public state-pair reducer shared by M20.3 and the M20.4 control."""

    return _state_record(
        protocol,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        expected_task_id=expected_task_id,
        before_envelope=before_envelope,
        after_envelope=after_envelope,
    )


def reduce_m20_3_trial(
    protocol: Mapping[str, Any],
    *,
    repo_root: Path,
    scenario_id: str,
    trial_id: str,
    expected_task_id: str,
    control_bundle: Mapping[str, Any],
    before_envelope: Mapping[str, Any],
    after_envelope: Mapping[str, Any],
    verification_log: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the M20.3 state and verification-proportionality observations."""

    if trial_id != f"{scenario_id}.baseline.01" or scenario_id not in FIXED_CHECKERS:
        _fail("source_drift")
    validate_control_bundle(
        protocol,
        control_bundle,
        unit="M20.3",
        scenario_id=scenario_id,
        arm="baseline",
        trial_id=trial_id,
    )
    manifest = control_bundle["reducer_manifest"]
    spec = FIXED_CHECKERS[scenario_id]
    if not any(
        label.get("label") == spec.label
        for label in manifest["verification_labels"]
        if isinstance(label, Mapping)
    ):
        _fail("source_drift")

    state = _state_record(
        protocol,
        unit="M20.3",
        scenario_id=scenario_id,
        trial_id=trial_id,
        expected_task_id=expected_task_id,
        before_envelope=before_envelope,
        after_envelope=after_envelope,
    )
    material = Materialization(repo_root, protocol["authority"]["baseline_revision"])
    test_paths = tuple(
        path for path in material.changed_paths if _TEST_MODULE.fullmatch(path) is not None
    )
    product_paths = tuple(path for path in material.changed_paths if path not in test_paths)
    product_lines, product_lines_reason = material.line_metric(product_paths)
    test_lines, test_lines_reason = material.line_metric(test_paths)
    test_cases, test_cases_reason = _metric_value(
        lambda: _changed_test_cases(material, test_paths)
    )
    contract_fanout, contract_fanout_reason = _metric_value(
        lambda: _probe_fanout(material, manifest["contract_probes"])
    )
    inventory_fanout, inventory_fanout_reason = _metric_value(
        lambda: _probe_fanout(material, manifest["inventory_probes"])
    )
    duplicate_locations, duplicate_locations_reason = _metric_value(
        lambda: _duplicate_contract_locations(
            material, manifest["contract_probes"]
        )
    )
    fixture_groups, fixture_groups_reason = _metric_value(
        lambda: _fixture_copy_groups(material, manifest["fixture_probes"])
    )
    steps, escalation, unknowns = reduce_verification_log(verification_log)
    target_result, target_reason = target_change_sensitivity(
        repo_root,
        scenario_id=scenario_id,
        manifest=manifest,
    )
    metric_reasons = {
        "product_lines": product_lines_reason,
        "test_lines": test_lines_reason,
        "test_cases": test_cases_reason,
        "contract_owner_fanout": contract_fanout_reason,
        "inventory_owner_fanout": inventory_fanout_reason,
        "duplicate_contract_locations": duplicate_locations_reason,
        "fixture_copy_groups": fixture_groups_reason,
    }
    for field, reason in metric_reasons.items():
        if reason is not None:
            unknowns.append({"field": f"data.{field}", "reasons": [reason]})
    if target_reason is not None:
        unknowns.append(
            {"field": "data.target_change_result", "reasons": [target_reason]}
        )

    data = {
        "product_files": len(product_paths),
        "test_files": len(test_paths),
        "product_lines": product_lines,
        "test_lines": test_lines,
        "test_cases": test_cases,
        "contract_owner_fanout": contract_fanout,
        "inventory_owner_fanout": inventory_fanout,
        "maintenance_fanout": len(
            _maintenance_owners(
                material.changed_paths, manifest["maintenance_selectors"]
            )
        ),
        "duplicate_contract_locations": duplicate_locations,
        "fixture_copy_groups": fixture_groups,
        "verification_escalation": escalation,
        "target_change_result": target_result,
        "verification_steps": steps,
    }
    measurement = build_observation(
        protocol,
        unit="M20.3",
        scenario_id=scenario_id,
        trial_id=trial_id,
        evidence_class="machine_observed",
        channel="trial_measurement",
        record_key="verification_measurement",
        payload={"measurement_kind": "verification_proportionality", "data": data},
        unknowns=unknowns,
    )
    return state, measurement


def _selected_plan(
    protocol: Mapping[str, Any],
    episode_plan: Mapping[str, Any],
    *,
    scenario_id: str,
    trial_id: str,
) -> Mapping[str, Any]:
    authority = protocol["authority"]
    root = _exact_keys(
        episode_plan,
        {
            "schema",
            "unit",
            "contract_id",
            "contract_revision",
            "baseline_revision",
            "authority_revision",
            "base_protocol_sha256",
            "plans",
        },
    )
    plan_digest = hashlib.sha256(canonical_json_bytes(root)).hexdigest()
    if (
        plan_digest != M20_4_EPISODE_PLAN_CANONICAL_SHA256
        or root["base_protocol_sha256"] != PROTOCOL_CANONICAL_SHA256
        or root["schema"] != "m20.4-episode-plan-v1"
        or root["unit"] != "M20.4"
        or root["contract_id"] != authority["contract_id"]
        or root["contract_revision"] != authority["contract_revision"]
        or root["baseline_revision"] != authority["baseline_revision"]
        or root["authority_revision"] != authority["authority_revision"]
    ):
        _fail("source_drift")
    matches = [
        item
        for item in root["plans"]
        if isinstance(item, Mapping)
        and item.get("scenario_id") == scenario_id
        and item.get("trial_id") == trial_id
    ]
    if len(matches) != 1:
        _fail("source_drift")
    return _exact_keys(
        matches[0],
        {"scenario_id", "arm", "trial_id", "task_slots", "boundaries", "episodes"},
    )


def capture_boundary_snapshot(
    protocol: Mapping[str, Any],
    episode_plan: Mapping[str, Any],
    *,
    repo_root: Path,
    scenario_id: str,
    trial_id: str,
    control_bundle: Mapping[str, Any],
    boundary_id: str,
    slot_envelopes: Mapping[str, Mapping[str, Any]],
    observer_log_position: int,
    observer_verification_position: int = 0,
) -> BoundarySnapshot:
    """Capture one safe M20.4 boundary from public state and Git material."""

    plan = _selected_plan(
        protocol, episode_plan, scenario_id=scenario_id, trial_id=trial_id
    )
    arm = _identifier(plan["arm"])
    validate_control_bundle(
        protocol,
        control_bundle,
        unit="M20.4",
        scenario_id=scenario_id,
        arm=arm,
        trial_id=trial_id,
    )
    boundary = _identifier(boundary_id)
    if boundary not in plan["boundaries"]:
        _fail("source_drift")
    slots = tuple(_identifier(slot) for slot in plan["task_slots"])
    if set(slot_envelopes) != set(slots):
        _fail("source_drift")
    states: list[tuple[str, str, int, int]] = []
    task_ids: set[str] = set()
    for slot in slots:
        envelope = slot_envelopes[slot]
        reduced = reduce_task_show_state(envelope)
        task = envelope.get("data", {}).get("task", {})
        task_id = task.get("task_id") if isinstance(task, Mapping) else None
        if not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None:
            _fail("parse_failed")
        if task_id in task_ids:
            _fail("source_drift")
        task_ids.add(task_id)
        states.append(
            (
                slot,
                task_id,
                _integer(reduced["contract_revision"], maximum=9_223_372_036_854_775_807),
                _integer(reduced["review_generation"], maximum=9_223_372_036_854_775_807),
            )
        )
    material = Materialization(repo_root, protocol["authority"]["baseline_revision"])
    line_total, line_reason = material.line_metric(material.changed_paths)
    unknowns: list[tuple[str, str]] = []
    if line_reason is not None:
        unknowns.append(("lines", line_reason))
    modules = len(
        _maintenance_owners(
            material.changed_paths,
            control_bundle["reducer_manifest"]["maintenance_selectors"],
        )
    )
    return BoundarySnapshot(
        scenario_id=scenario_id,
        trial_id=trial_id,
        boundary_id=boundary,
        files=len(material.changed_paths),
        modules=modules,
        lines=line_total,
        metric_unknowns=tuple(unknowns),
        task_states=tuple(states),
        observer_log_position=_integer(observer_log_position),
        observer_verification_position=_integer(
            observer_verification_position, maximum=MAX_VERIFICATION_STEPS
        ),
    )


def _operation_log(
    raw_log: Sequence[Mapping[str, Any]],
    slots: set[str],
) -> tuple[tuple[str, str | None, str], ...]:
    if not isinstance(raw_log, Sequence) or isinstance(raw_log, (str, bytes)):
        _fail("parse_failed")
    if len(raw_log) > MAX_TASKGOV_OPERATIONS:
        _fail("cap_exceeded")
    result: list[tuple[str, str | None, str]] = []
    for raw in raw_log:
        item = _exact_keys(
            raw, {"command_leaf", "task_slot", "duration_ms", "result"}
        )
        command_leaf = item["command_leaf"]
        if not isinstance(command_leaf, str) or command_leaf not in TASKGOV_LEAVES:
            _fail("source_drift")
        slot_value = item["task_slot"]
        slot = None if slot_value is None else _identifier(slot_value)
        if slot is not None and slot not in slots:
            _fail("source_drift")
        outcome = item["result"]
        duration = _integer(item["duration_ms"], maximum=300_000)
        if outcome == "timeout":
            if duration != 300_000:
                _fail("parse_failed")
        elif outcome in {"success", "input_error", "service_error"}:
            if duration > 299_999:
                _fail("parse_failed")
        else:
            _fail("parse_failed")
        result.append((command_leaf, slot, outcome))
    return tuple(result)


def _snapshot_states(snapshot: BoundarySnapshot) -> dict[str, tuple[str, int, int]]:
    return {
        slot: (task_id, contract, generation)
        for slot, task_id, contract, generation in snapshot.task_states
    }


def reduce_m20_4_measurement(
    protocol: Mapping[str, Any],
    episode_plan: Mapping[str, Any],
    *,
    scenario_id: str,
    trial_id: str,
    snapshots: Sequence[BoundarySnapshot],
    observer_log: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reduce frozen M20.4 boundary intervals to one split measurement."""

    plan = _selected_plan(
        protocol, episode_plan, scenario_id=scenario_id, trial_id=trial_id
    )
    boundaries = tuple(plan["boundaries"])
    if tuple(snapshot.boundary_id for snapshot in snapshots) != boundaries:
        _fail("source_drift")
    if any(
        snapshot.scenario_id != scenario_id or snapshot.trial_id != trial_id
        for snapshot in snapshots
    ):
        _fail("source_drift")
    positions = tuple(snapshot.observer_log_position for snapshot in snapshots)
    if positions != tuple(sorted(positions)):
        _fail("source_drift")
    slots = set(plan["task_slots"])
    operations = _operation_log(observer_log, slots)
    if positions[-1] > len(operations):
        _fail("source_drift")
    by_boundary = dict(zip(boundaries, snapshots, strict=True))
    first_states = _snapshot_states(snapshots[0])
    for snapshot in snapshots[1:]:
        states = _snapshot_states(snapshot)
        if set(states) != set(first_states) or any(
            states[slot][0] != first_states[slot][0] for slot in states
        ):
            _fail("source_drift")

    reduced_episodes: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    boundary_index = {name: index for index, name in enumerate(boundaries)}
    previous_end = -1
    for episode_index, raw_episode in enumerate(plan["episodes"]):
        episode = _exact_keys(
            raw_episode,
            {"episode_id", "task_slot", "start_boundary", "end_boundary"},
        )
        episode_id = _identifier(episode["episode_id"])
        task_slot = _identifier(episode["task_slot"])
        start_name = episode["start_boundary"]
        end_name = episode["end_boundary"]
        if (
            task_slot not in slots
            or start_name not in boundary_index
            or end_name not in boundary_index
            or boundary_index[start_name] >= boundary_index[end_name]
            or boundary_index[start_name] < previous_end
        ):
            _fail("source_drift")
        previous_end = boundary_index[end_name]
        start = by_boundary[start_name]
        end = by_boundary[end_name]
        start_states = _snapshot_states(start)
        end_states = _snapshot_states(end)
        start_task, contract_before, generation_before = start_states[task_slot]
        end_task, contract_after, generation_after = end_states[task_slot]
        if start_task != end_task or contract_after < contract_before or generation_after < generation_before:
            _fail("source_drift")
        interval = operations[
            start.observer_log_position : end.observer_log_position
        ]
        successful_writes = [
            row
            for row in interval
            if row[0] in TASKGOV_WRITE_LEAVES
            and row[1] is not None
            and row[2] == "success"
        ]
        if any(slot != task_slot for _leaf, slot, _result in successful_writes):
            _fail("source_drift")
        item = {
            "episode_id": episode_id,
            "files_before": start.files,
            "files_after": end.files,
            "modules_before": start.modules,
            "modules_after": end.modules,
            "lines_before": start.lines,
            "lines_after": end.lines,
            "contract_revision_before": contract_before,
            "contract_revision_after": contract_after,
            "review_generation_before": generation_before,
            "review_generation_after": generation_after,
            "governance_cycles": len(successful_writes),
            "review_cycles": generation_after - generation_before,
        }
        for suffix, snapshot, metric in (
            ("before", start, "files"),
            ("after", end, "files"),
            ("before", start, "modules"),
            ("after", end, "modules"),
            ("before", start, "lines"),
            ("after", end, "lines"),
        ):
            reasons = dict(snapshot.metric_unknowns)
            if metric in reasons:
                unknowns.append(
                    {
                        "field": f"data.episodes.{episode_index}.{metric}_{suffix}",
                        "reasons": [reasons[metric]],
                    }
                )
        reduced_episodes.append(item)

    return build_observation(
        protocol,
        unit="M20.4",
        scenario_id=scenario_id,
        trial_id=trial_id,
        evidence_class="machine_observed",
        channel="trial_measurement",
        record_key="split_measurement",
        payload={"measurement_kind": "split_pressure", "data": {"episodes": reduced_episodes}},
        unknowns=unknowns,
    )
