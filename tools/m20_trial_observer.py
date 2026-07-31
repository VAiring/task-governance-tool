"""Trial-local, sanitized process observer for TG-M20 fresh-agent studies.

This is temporary repository-study tooling.  It is deliberately not part of
the installable package and never writes to the canonical taskgov state.
"""

from __future__ import annotations

import argparse
import errno
import io
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True

CONFIG_SCHEMA = "m20-trial-observer-config-v1"
LOG_SCHEMA = "m20-trial-observer-log-v1"
TIMEOUT_SECONDS = 300
MAX_CAPTURE_BYTES = 1_048_576
MAX_CONFIG_BYTES = 262_144
MAX_LOG_BYTES = 262_144
MAX_TASKGOV_OPERATIONS = 64
MAX_VERIFICATION_STEPS = 16
LOCK_FILENAME = ".m20-trial-observer.lock"
LOCK_POLL_SECONDS = 0.025
PROCESS_TREE_TERMINATION_SECONDS = 10
LOCK_WAIT_SECONDS = TIMEOUT_SECONDS + PROCESS_TREE_TERMINATION_SECONDS + 5
WINDOWS_CREATE_SUSPENDED = 0x00000004

PUBLIC_COMMAND_LEAVES = frozenset(
    {
        "setup",
        "doctor",
        "task.add",
        "task.list",
        "task.next",
        "task.current",
        "task.effort",
        "task.show",
        "task.edit",
        "task.complete",
        "task.checkpoint",
        "handoff.record",
        "handoff.list",
        "handoff.show",
        "handoff.withdraw",
        "review.prepare",
        "review.target.set",
        "review.receipt.add",
        "review.finding.add",
        "review.finding.resolve",
    }
)
VERIFICATION_KINDS = frozenset({"focused", "lane", "all", "other"})
LANES = frozenset({"fast", "integration", "release", "all"})
FRESH_TRIALS = frozenset(
    {
        ("M20.3", "vp_cli_contract", "baseline"),
        ("M20.3", "vp_state_transition", "baseline"),
        ("M20.3", "vp_release_contract", "baseline"),
        ("M20.4", "sp_multi_outcome_intake", "broad"),
        ("M20.4", "sp_multi_outcome_intake", "bounded"),
        ("M20.4", "sp_in_scope_discovery", "broad"),
        ("M20.4", "sp_in_scope_discovery", "bounded"),
        ("M20.4", "sp_user_expansion", "broad"),
        ("M20.4", "sp_user_expansion", "bounded"),
        ("M20.4", "sp_cross_module_failure", "broad"),
        ("M20.4", "sp_cross_module_failure", "bounded"),
        ("M20.4", "sp_handoff_control", "broad"),
    }
)

_IDENTIFIER = re.compile(r"[a-z0-9._-]{1,64}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_TASK_ID = re.compile(r"tg_task_[0-9a-f]{16}\Z")
_TASK_ID_ANYWHERE = re.compile(r"tg_task_[0-9a-f]{16}")
_UNITTEST_TARGET = re.compile(
    r"tests\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z"
)


class TrialObserverError(RuntimeError):
    """Stable, sanitized observer failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise TrialObserverError(code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return text.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("parse_failed")


def _load_canonical_object(raw: bytes, *, maximum: int) -> dict[str, Any]:
    if not raw or len(raw) > maximum:
        _fail("source_drift")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: _fail("parse_failed"),
        )
    except TrialObserverError:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        _fail("parse_failed")
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        _fail("source_drift")
    return value


def _load_process_object(raw: bytes) -> dict[str, Any]:
    """Parse one bounded process JSON object; a terminal newline is allowed."""

    if not raw or len(raw) > MAX_CAPTURE_BYTES:
        _fail("parse_failed")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: _fail("parse_failed"),
        )
    except TrialObserverError:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        _fail("parse_failed")
    if not isinstance(value, dict):
        _fail("parse_failed")
    return value


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail("source_drift")
    return value


def _string(value: Any, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > 8192:
        _fail("parse_failed")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        _fail("parse_failed")
    if "\0" in value or (pattern is not None and pattern.fullmatch(value) is None):
        _fail("parse_failed")
    return value


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        _fail("source_missing")
    return _stat_is_reparse(info)


def _stat_is_reparse(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & flag
    )


def _trial_path(
    trial_root: Path,
    relative: str | Path,
    *,
    must_exist: bool,
) -> Path:
    lexical = Path(relative)
    if (
        lexical.is_absolute()
        or lexical.anchor
        or lexical.drive
        or lexical.root
        or any(part in {"", ".", ".."} for part in lexical.parts)
    ):
        _fail("cross_path")
    candidate = trial_root.joinpath(lexical)
    current = candidate if candidate.exists() else candidate.parent
    while current != trial_root:
        if current.parent == current:
            _fail("cross_path")
        if not current.exists() or _is_reparse(current):
            _fail("cross_path")
        current = current.parent
    if must_exist:
        if not candidate.exists() or _is_reparse(candidate) or not candidate.is_file():
            _fail("source_missing")
    elif candidate.exists() and (_is_reparse(candidate) or not candidate.is_file()):
        _fail("cross_path")
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError:
        _fail("source_missing")
    if parent != trial_root and trial_root not in parent.parents:
        _fail("cross_path")
    return candidate


def _safe_environment() -> dict[str, str]:
    allowed = ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    return result


class _BoundedPipe:
    def __init__(self, stream: io.BufferedReader, cap: int = MAX_CAPTURE_BYTES):
        self.stream = stream
        self.cap = cap
        self.total = 0
        self.prefix = bytearray()
        self.non_whitespace_overflow = False
        self.error: BaseException | None = None

    def read(self) -> None:
        try:
            while True:
                block = self.stream.read(65_536)
                if not block:
                    break
                self.total += len(block)
                remaining = max(0, self.cap - len(self.prefix))
                self.prefix.extend(block[:remaining])
                if block[remaining:].strip(b" \t\r\n"):
                    self.non_whitespace_overflow = True
        except BaseException as error:  # pragma: no cover - defensive pipe path
            self.error = error
        finally:
            self.stream.close()


def _duration(start_ns: int, timed_out: bool) -> int:
    if timed_out:
        return 300_000
    return min(299_999, max(0, (time.monotonic_ns() - start_ns) // 1_000_000))


class _ExclusiveReservation:
    """One bounded, trial-local cross-process reservation.

    The empty reservation file contains no command material.  Atomic exclusive
    creation is the ownership operation; an existing regular file is treated
    as a live or crashed holder and is never broken automatically.
    """

    def __init__(self, path: Path):
        self.path = path
        self._descriptor: int | None = None
        self._identity: tuple[int, int] | None = None

    @staticmethod
    def _identity_for(info: os.stat_result) -> tuple[int, int]:
        return (info.st_dev, info.st_ino)

    def _existing_is_safe(self) -> bool:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            _fail("source_missing")
        if _stat_is_reparse(info) or not stat.S_ISREG(info.st_mode):
            _fail("cross_path")
        return True

    def __enter__(self) -> _ExclusiveReservation:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        while True:
            try:
                descriptor = os.open(self.path, flags, 0o600)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    _fail("source_missing")
                if not self._existing_is_safe():
                    continue
                if time.monotonic() >= deadline:
                    _fail("observer_busy")
                time.sleep(LOCK_POLL_SECONDS)
                continue
            try:
                opened = os.fstat(descriptor)
                current = self.path.lstat()
            except OSError:
                os.close(descriptor)
                _fail("source_drift")
            if (
                _stat_is_reparse(opened)
                or _stat_is_reparse(current)
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or self._identity_for(opened) != self._identity_for(current)
            ):
                os.close(descriptor)
                _fail("cross_path")
            self._descriptor = descriptor
            self._identity = self._identity_for(opened)
            return self

    def _validate_owned_path(self) -> None:
        try:
            current = self.path.lstat()
        except OSError:
            _fail("lock_cleanup_failed")
        if (
            _stat_is_reparse(current)
            or not stat.S_ISREG(current.st_mode)
            or self._identity_for(current) != self._identity
        ):
            _fail("lock_cleanup_failed")

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        descriptor = self._descriptor
        if descriptor is None:  # pragma: no cover - context manager invariant
            _fail("lock_cleanup_failed")
        try:
            self._validate_owned_path()
            if os.name == "nt":
                os.close(descriptor)
                self._descriptor = None
                self._validate_owned_path()
                self.path.unlink()
            else:
                self.path.unlink()
                os.close(descriptor)
                self._descriptor = None
        except TrialObserverError:
            if self._descriptor is not None:
                os.close(self._descriptor)
                self._descriptor = None
            raise
        except OSError:
            if self._descriptor is not None:
                os.close(self._descriptor)
                self._descriptor = None
            _fail("lock_cleanup_failed")


def _process_creation_options() -> dict[str, Any]:
    if os.name == "nt":
        process_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
        if type(process_group) is not int or process_group == 0:
            _fail("process_tree_unavailable")
        return {"creationflags": process_group | WINDOWS_CREATE_SUSPENDED}
    return {"start_new_session": True}


class _ProcessTree:
    """Own one child process group or Windows kill-on-close Job Object."""

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
            _fail("process_tree_unavailable")
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
            _fail("process_tree_unavailable")
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
            _fail("process_tree_kill_failed")

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
                _fail("process_tree_kill_failed")
            tree_error = True
        if tree_error:
            _fail("process_tree_kill_failed")

    def close(self) -> None:
        if self._job is not None:
            job = self._job
            self._job = None
            if not self._kernel32.CloseHandle(job):
                _fail("process_tree_cleanup_failed")


def _new_process_tree(process: subprocess.Popen[Any]) -> _ProcessTree:
    """Internal injection seam for platform process-tree ownership tests."""

    return _ProcessTree(process)


def _atomic_write(path: Path, payload: bytes) -> None:
    if len(payload) > MAX_LOG_BYTES:
        _fail("cap_exceeded")
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        finally:
            _fail("source_missing")


def _verification_argv(raw: Any) -> tuple[tuple[str, ...], str]:
    if not isinstance(raw, list) or not raw or len(raw) > 36:
        _fail("parse_failed")
    argv = tuple(_string(item) for item in raw)
    command = argv[1:] if argv[0] == "-B" else argv
    if len(command) >= 3 and command[:2] == ("-m", "unittest"):
        if not 1 <= len(command[2:]) <= 32 or any(
            _UNITTEST_TARGET.fullmatch(target) is None for target in command[2:]
        ):
            _fail("unsafe_verification")
        return argv, "focused"
    if command == ("tools/release_contract.py", "--repo", "."):
        return argv, "focused"
    prefix = ("tools/test_lanes.py", "--repo", ".")
    if command == (*prefix, "--check"):
        return argv, "other"
    if (
        len(command) == 5
        and command[:3] == prefix
        and command[3] == "--lane"
        and command[4] in LANES
    ):
        return argv, "all" if command[4] == "all" else "lane"
    _fail("unsafe_verification")


def _validate_config(value: Any) -> dict[str, Any]:
    root = _exact(
        value,
        {
            "schema",
            "unit",
            "scenario_id",
            "trial_id",
            "arm",
            "task_slots",
            "verification_labels",
        },
    )
    if root["schema"] != CONFIG_SCHEMA:
        _fail("source_drift")
    unit = _string(root["unit"])
    scenario_id = _string(root["scenario_id"], _IDENTIFIER)
    trial_id = _string(root["trial_id"], _IDENTIFIER)
    arm = _string(root["arm"], _SAFE_CODE)
    if (
        (unit, scenario_id, arm) not in FRESH_TRIALS
        or trial_id != f"{scenario_id}.{arm}.01"
    ):
        _fail("inventory_mismatch")

    if not isinstance(root["task_slots"], list) or len(root["task_slots"]) > 8:
        _fail("parse_failed")
    slots: set[str] = set()
    task_ids: set[str] = set()
    for raw in root["task_slots"]:
        item = _exact(raw, {"task_slot", "task_id"})
        slot = _string(item["task_slot"], _SAFE_CODE)
        task_id = _string(item["task_id"], _TASK_ID)
        if slot in slots or task_id in task_ids:
            _fail("duplicate_config")
        slots.add(slot)
        task_ids.add(task_id)

    labels = root["verification_labels"]
    if not isinstance(labels, list) or len(labels) > MAX_VERIFICATION_STEPS:
        _fail("parse_failed")
    seen: set[str] = set()
    for raw in labels:
        item = _exact(raw, {"label", "kind", "argv"})
        label = _string(item["label"], _SAFE_CODE)
        kind = _string(item["kind"], _SAFE_CODE)
        _argv, expected_kind = _verification_argv(item["argv"])
        if label in seen:
            _fail("duplicate_config")
        if kind not in VERIFICATION_KINDS or kind != expected_kind:
            _fail("unsafe_verification")
        seen.add(label)
    return root


def _empty_log(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": LOG_SCHEMA,
        "unit": config["unit"],
        "scenario_id": config["scenario_id"],
        "trial_id": config["trial_id"],
        "arm": config["arm"],
        "taskgov": [],
        "verifications": [],
    }


def _validate_log(value: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    root = _exact(
        value,
        {
            "schema",
            "unit",
            "scenario_id",
            "trial_id",
            "arm",
            "taskgov",
            "verifications",
        },
    )
    if (
        root["schema"] != LOG_SCHEMA
        or root["unit"] != config["unit"]
        or root["scenario_id"] != config["scenario_id"]
        or root["trial_id"] != config["trial_id"]
        or root["arm"] != config["arm"]
    ):
        _fail("source_drift")
    slots = {item["task_slot"] for item in config["task_slots"]}
    operations = root["taskgov"]
    if not isinstance(operations, list) or len(operations) > MAX_TASKGOV_OPERATIONS:
        _fail("source_drift")
    for raw in operations:
        item = _exact(raw, {"command_leaf", "task_slot", "duration_ms", "result"})
        if item["command_leaf"] not in PUBLIC_COMMAND_LEAVES:
            _fail("source_drift")
        if item["task_slot"] is not None and item["task_slot"] not in slots:
            _fail("source_drift")
        if type(item["duration_ms"]) is not int or not 0 <= item["duration_ms"] <= 300_000:
            _fail("source_drift")
        if item["result"] not in {"success", "input_error", "service_error", "timeout"}:
            _fail("source_drift")
        if (item["result"] == "timeout") != (item["duration_ms"] == 300_000):
            _fail("source_drift")

    steps = root["verifications"]
    if not isinstance(steps, list) or len(steps) > MAX_VERIFICATION_STEPS:
        _fail("source_drift")
    for ordinal, raw in enumerate(steps, start=1):
        item = _exact(raw, {"ordinal", "kind", "duration_ms", "result"})
        if item["ordinal"] != ordinal or item["kind"] not in VERIFICATION_KINDS:
            _fail("source_drift")
        if type(item["duration_ms"]) is not int or not 0 <= item["duration_ms"] <= 300_000:
            _fail("source_drift")
        if item["result"] not in {"success", "failure", "timeout"}:
            _fail("source_drift")
        if (item["result"] == "timeout") != (item["duration_ms"] == 300_000):
            _fail("source_drift")
    return root


class TrialObserver:
    """Proxy one isolated trial without retaining raw process material."""

    def __init__(self, trial_root: Path, config_path: str | Path, log_path: str | Path):
        lexical_root = Path(trial_root).absolute()
        if not lexical_root.exists() or _is_reparse(lexical_root):
            _fail("cross_path")
        try:
            self.trial_root = lexical_root.resolve(strict=True)
        except OSError:
            _fail("source_missing")
        if not self.trial_root.is_dir() or _is_reparse(self.trial_root):
            _fail("cross_path")
        self.config_path = _trial_path(self.trial_root, config_path, must_exist=True)
        self.log_path = _trial_path(self.trial_root, log_path, must_exist=False)
        if self.config_path == self.log_path:
            _fail("cross_path")
        lock_relative = self.config_path.parent.relative_to(self.trial_root) / LOCK_FILENAME
        self.lock_path = _trial_path(
            self.trial_root,
            lock_relative,
            must_exist=False,
        )
        if self.lock_path in {self.config_path, self.log_path}:
            _fail("cross_path")
        try:
            raw = self.config_path.read_bytes()
        except OSError:
            _fail("source_missing")
        self.config = _validate_config(_load_canonical_object(raw, maximum=MAX_CONFIG_BYTES))
        self.taskgov_path = _trial_path(
            self.trial_root,
            "task-governance-tool/scripts/taskgov.py",
            must_exist=True,
        )
        self._task_by_slot = {
            item["task_slot"]: item["task_id"] for item in self.config["task_slots"]
        }
        self._slot_by_task = {value: key for key, value in self._task_by_slot.items()}
        self._verification_by_label = {
            item["label"]: item for item in self.config["verification_labels"]
        }
        self._read_log()

    def _read_log(self) -> dict[str, Any]:
        if not self.log_path.exists():
            return _empty_log(self.config)
        if _is_reparse(self.log_path) or not self.log_path.is_file():
            _fail("cross_path")
        try:
            raw = self.log_path.read_bytes()
        except OSError:
            _fail("source_missing")
        return _validate_log(_load_canonical_object(raw, maximum=MAX_LOG_BYTES), self.config)

    def _append(self, channel: str, record: Mapping[str, Any]) -> dict[str, Any]:
        log = self._read_log()
        target = log[channel]
        limit = MAX_TASKGOV_OPERATIONS if channel == "taskgov" else MAX_VERIFICATION_STEPS
        if len(target) >= limit:
            _fail("cap_exceeded")
        target.append(dict(record))
        _validate_log(log, self.config)
        _atomic_write(self.log_path, canonical_json_bytes(log))
        return dict(record)

    def _preflight_capacity(self, channel: str) -> int:
        log = self._read_log()
        if channel == "taskgov":
            limit = MAX_TASKGOV_OPERATIONS
        elif channel == "verifications":
            limit = MAX_VERIFICATION_STEPS
        else:  # pragma: no cover - internal call invariant
            _fail("parse_failed")
        count = len(log[channel])
        if count >= limit:
            _fail("cap_exceeded")
        return count

    def _validate_arguments(self, arguments: Sequence[str], task_slot: str | None) -> str | None:
        if isinstance(arguments, (str, bytes)) or len(arguments) > 128:
            _fail("parse_failed")
        values = tuple(_string(argument) for argument in arguments)
        if any(
            value in {"--repo", "--json", "--db", "--"}
            or value.startswith(("--repo=", "--json=", "--db="))
            for value in values
        ):
            _fail("cross_path")
        seen_ids = {match.group(0) for value in values for match in _TASK_ID_ANYWHERE.finditer(value)}
        if any(task_id not in self._slot_by_task for task_id in seen_ids):
            _fail("unknown_task")
        if task_slot is not None:
            _string(task_slot, _SAFE_CODE)
            expected = self._task_by_slot.get(task_slot)
            if expected is None or seen_ids != {expected}:
                _fail("task_slot_mismatch")
            return task_slot
        if seen_ids:
            if len(seen_ids) != 1:
                _fail("task_slot_mismatch")
            return self._slot_by_task[next(iter(seen_ids))]
        return None

    def _invoke_taskgov(
        self,
        command_leaf: str,
        arguments: Sequence[str] = (),
        *,
        task_slot: str | None = None,
    ) -> tuple[Mapping[str, Any] | None, str, int, str | None]:
        if command_leaf not in PUBLIC_COMMAND_LEAVES:
            _fail("unknown_leaf")
        _trial_path(
            self.trial_root,
            "task-governance-tool/scripts/taskgov.py",
            must_exist=True,
        )
        safe_slot = self._validate_arguments(arguments, task_slot)
        argv = [
            sys.executable,
            "-I",
            "-S",
            str(self.taskgov_path),
            *command_leaf.split("."),
            *arguments,
            "--repo",
            str(self.trial_root),
            "--json",
        ]
        start = time.monotonic_ns()
        try:
            process = subprocess.Popen(
                argv,
                cwd=self.trial_root,
                env=_safe_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                **_process_creation_options(),
            )
        except OSError:
            _fail("source_missing")
        tree = _new_process_tree(process)
        assert process.stdout is not None and process.stderr is not None
        stdout = _BoundedPipe(process.stdout)
        stderr = _BoundedPipe(process.stderr)
        threads = (
            threading.Thread(target=stdout.read, daemon=True),
            threading.Thread(target=stderr.read, daemon=True),
        )
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            try:
                exit_code = process.wait(timeout=TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                tree.terminate()
                exit_code = None
        finally:
            tree.close()
        for thread in threads:
            thread.join()
        if stdout.error is not None or stderr.error is not None:
            _fail("source_missing")
        duration_ms = _duration(start, timed_out)
        if timed_out:
            result = "timeout"
            envelope = None
        else:
            if stdout.total > MAX_CAPTURE_BYTES and stdout.non_whitespace_overflow:
                _fail("cap_exceeded")
            envelope = _load_process_object(bytes(stdout.prefix))
            _exact(envelope, {"ok", "command", "project_id", "data", "warnings", "errors"})
            errors = envelope["errors"]
            if not isinstance(errors, list):
                _fail("parse_failed")
            if exit_code == 0 and envelope["ok"] is True and not errors:
                result = "success"
            elif exit_code == 1 and envelope["ok"] is False and errors:
                result = "input_error"
            elif exit_code == 2 and envelope["ok"] is False and errors:
                result = "service_error"
            else:
                _fail("parse_failed")
        return envelope, result, duration_ms, safe_slot

    def run_taskgov(
        self,
        command_leaf: str,
        arguments: Sequence[str] = (),
        *,
        task_slot: str | None = None,
    ) -> Mapping[str, Any] | None:
        """Run one fixed public leaf and return its bounded in-memory envelope."""

        if command_leaf not in PUBLIC_COMMAND_LEAVES:
            _fail("unknown_leaf")
        with _ExclusiveReservation(self.lock_path):
            self._preflight_capacity("taskgov")
            envelope, result, duration_ms, safe_slot = self._invoke_taskgov(
                command_leaf,
                arguments,
                task_slot=task_slot,
            )
            self._append(
                "taskgov",
                {
                    "command_leaf": command_leaf,
                    "task_slot": safe_slot,
                    "duration_ms": duration_ms,
                    "result": result,
                },
            )
        return envelope

    def read_task(self, task_slot: str) -> Mapping[str, Any]:
        """Read one configured Task through the public task-show boundary."""

        task_id = self._task_by_slot.get(task_slot)
        if task_id is None:
            _fail("unknown_task_slot")
        envelope = self.run_taskgov("task.show", (task_id,), task_slot=task_slot)
        if envelope is None or envelope["ok"] is not True:
            _fail("readback_failed")
        return envelope

    def snapshot_task(self, task_slot: str, output_path: str | Path) -> Mapping[str, Any]:
        """Parent-only unlogged raw snapshot through public ``task show``.

        The destination is temporary trial material.  It must be absent and
        is intentionally not exposed as a CLI mode or a normal subject action.
        """

        task_id = self._task_by_slot.get(task_slot)
        if task_id is None:
            _fail("unknown_task_slot")
        destination = _trial_path(self.trial_root, output_path, must_exist=False)
        if os.path.lexists(destination):
            if destination.is_symlink() or _is_reparse(destination):
                _fail("cross_path")
            _fail("artifact_exists")
        envelope, result, _duration_ms, safe_slot = self._invoke_taskgov(
            "task.show",
            (task_id,),
            task_slot=task_slot,
        )
        if (
            envelope is None
            or result != "success"
            or envelope["ok"] is not True
            or safe_slot != task_slot
        ):
            _fail("readback_failed")
        payload = canonical_json_bytes(envelope)
        if len(payload) > MAX_CAPTURE_BYTES:
            _fail("cap_exceeded")
        _atomic_write(destination, payload)
        try:
            retained = destination.read_bytes()
        except OSError:
            _fail("source_missing")
        readback = _load_canonical_object(retained, maximum=MAX_CAPTURE_BYTES)
        if readback != envelope:
            _fail("source_drift")
        return readback

    def run_verification(self, label: str) -> dict[str, Any]:
        """Run one pre-reviewed offline label without retaining its output."""

        item = self._verification_by_label.get(label)
        if item is None:
            _fail("unknown_label")
        with _ExclusiveReservation(self.lock_path):
            prior_steps = self._preflight_capacity("verifications")
            raw_argv, expected_kind = _verification_argv(item["argv"])
            if item["kind"] != expected_kind:
                _fail("unsafe_verification")
            script = raw_argv[1] if raw_argv[0] == "-B" else raw_argv[0]
            if script.startswith("tools/"):
                _trial_path(self.trial_root, script, must_exist=True)
            # Verification targets are repository modules/scripts.  Isolated mode
            # would remove the trial root from Python's import path, so safety is
            # provided by the exact argv allowlist and minimal environment instead.
            argv = [sys.executable, *raw_argv]
            start = time.monotonic_ns()
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=self.trial_root,
                    env=_safe_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    **_process_creation_options(),
                )
            except OSError:
                _fail("source_missing")
            tree = _new_process_tree(process)
            timed_out = False
            try:
                try:
                    exit_code = process.wait(timeout=TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    tree.terminate()
                    exit_code = None
            finally:
                tree.close()
            duration_ms = _duration(start, timed_out)
            result = "timeout" if timed_out else ("success" if exit_code == 0 else "failure")
            ordinal = prior_steps + 1
            return self._append(
                "verifications",
                {
                    "ordinal": ordinal,
                    "kind": item["kind"],
                    "duration_ms": duration_ms,
                    "result": result,
                },
            )

    def report(self) -> dict[str, Any]:
        """Return the exact sanitized canonical-log projection."""

        return self._read_log()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one fixed M20 trial observer.")
    parser.add_argument("--trial-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--log", required=True)
    subcommands = parser.add_subparsers(dest="mode", required=True)
    taskgov = subcommands.add_parser("taskgov")
    taskgov.add_argument("--leaf", required=True)
    taskgov.add_argument("--task-slot")
    taskgov.add_argument("arguments", nargs=argparse.REMAINDER)
    verify = subcommands.add_parser("verify")
    verify.add_argument("--label", required=True)
    subcommands.add_parser("report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        observer = TrialObserver(args.trial_root, args.config, args.log)
        if args.mode == "taskgov":
            result = observer.run_taskgov(args.leaf, args.arguments, task_slot=args.task_slot)
        elif args.mode == "verify":
            result = observer.run_verification(args.label)
        else:
            result = observer.report()
    except TrialObserverError as error:
        print(canonical_json_bytes({"ok": False, "error_code": error.code}).decode("utf-8"), file=sys.stderr)
        return 2
    print(canonical_json_bytes({"ok": True, "data": result}).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
