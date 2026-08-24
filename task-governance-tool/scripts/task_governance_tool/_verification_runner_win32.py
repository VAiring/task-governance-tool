"""Thin native Windows primitives for the bounded verification Runner.

This module owns only Job, process, stdio, accounting, termination, wait, and
handle mechanics.  It has no storage, project-discovery, shell, or parent-policy
behavior.
"""

from __future__ import annotations

import ctypes
import os
import platform
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn


_SAFE_CODES = frozenset(
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
    }
)


class RunnerWin32Error(RuntimeError):
    """Sanitized fail-closed native-boundary failure."""

    def __init__(self, code: str, *, after_create: bool = False) -> None:
        if code not in _SAFE_CODES:
            code = "sandbox_boundary_violation"
        super().__init__("verification Runner Windows boundary failed closed")
        self.code = code
        self.after_create = after_create is True


def _fail(code: str, *, after_create: bool = False) -> NoReturn:
    raise RunnerWin32Error(code, after_create=after_create)


@dataclass(frozen=True)
class JobLimits:
    timeout_seconds: int
    cpu_seconds: int
    memory_mib: int
    process_limit: int

    def __post_init__(self) -> None:
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 900
            or type(self.cpu_seconds) is not int
            or not 1 <= self.cpu_seconds <= 900
            or type(self.memory_mib) is not int
            or not 64 <= self.memory_mib <= 2048
            or type(self.process_limit) is not int
            or not 1 <= self.process_limit <= 32
        ):
            _fail("sandbox_setup_failed")


@dataclass(frozen=True)
class JobAccounting:
    total_user_time_100ns: int
    peak_job_memory_bytes: int
    total_processes: int
    active_processes: int


DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t
SIZE_T = ctypes.c_size_t
LONGLONG = ctypes.c_int64
ULONGLONG = ctypes.c_uint64
HANDLE = ctypes.c_void_p
LPVOID = ctypes.c_void_p

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
STILL_ACTIVE = 259
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
ERROR_INSUFFICIENT_BUFFER = 122

FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3

STARTF_USESTDHANDLES = 0x00000100
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400

PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D

JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
EXACT_JOB_LIMIT_FLAGS = (
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    | JOB_OBJECT_LIMIT_JOB_MEMORY
    | JOB_OBJECT_LIMIT_PROCESS_MEMORY
    | JOB_OBJECT_LIMIT_JOB_TIME
)
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = (
        ("nLength", DWORD),
        ("lpSecurityDescriptor", LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    )


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = (
        ("cb", DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCountChars", DWORD),
        ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", WORD),
        ("cbReserved2", WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    )


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = (("StartupInfo", _STARTUPINFOW), ("lpAttributeList", LPVOID))


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
    )


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = tuple(
        (name, ULONGLONG)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    )


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", LONGLONG),
        ("PerJobUserTimeLimit", LONGLONG),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", ULONG_PTR),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    )


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    )


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION_STRUCT(ctypes.Structure):
    _fields_ = (
        ("TotalUserTime", LONGLONG),
        ("TotalKernelTime", LONGLONG),
        ("ThisPeriodTotalUserTime", LONGLONG),
        ("ThisPeriodTotalKernelTime", LONGLONG),
        ("TotalPageFaultCount", DWORD),
        ("TotalProcesses", DWORD),
        ("ActiveProcesses", DWORD),
        ("TotalTerminatedProcesses", DWORD),
    )


def abi_layout() -> dict[str, int]:
    return {
        "pointer_size": ctypes.sizeof(ctypes.c_void_p),
        "startupinfo_size": ctypes.sizeof(_STARTUPINFOW),
        "startupinfoex_size": ctypes.sizeof(_STARTUPINFOEXW),
        "process_information_size": ctypes.sizeof(_PROCESS_INFORMATION),
        "job_basic_limit_size": ctypes.sizeof(_JOBOBJECT_BASIC_LIMIT_INFORMATION),
        "job_extended_limit_size": ctypes.sizeof(_JOBOBJECT_EXTENDED_LIMIT_INFORMATION),
        "job_accounting_size": ctypes.sizeof(
            _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION_STRUCT
        ),
    }


def _assert_supported_abi() -> None:
    if (
        os.name != "nt"
        or platform.python_implementation() != "CPython"
        or platform.machine().upper() not in {"AMD64", "X86_64"}
        or ctypes.sizeof(ctypes.c_void_p) != 8
        or ctypes.sizeof(ctypes.c_wchar) != 2
        or ctypes.sizeof(_STARTUPINFOW) != 104
        or ctypes.sizeof(_STARTUPINFOEXW) != 112
        or ctypes.sizeof(_PROCESS_INFORMATION) != 24
        or ctypes.sizeof(_JOBOBJECT_BASIC_LIMIT_INFORMATION) != 64
        or ctypes.sizeof(_JOBOBJECT_EXTENDED_LIMIT_INFORMATION) != 144
        or ctypes.sizeof(_JOBOBJECT_BASIC_ACCOUNTING_INFORMATION_STRUCT) != 48
    ):
        _fail("sandbox_unavailable")


class _Apis:
    def __init__(self) -> None:
        _assert_supported_abi()
        try:
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        except (AttributeError, OSError):
            _fail("sandbox_unavailable")
        try:
            self._bind()
        except (AttributeError, OSError):
            _fail("sandbox_unavailable")

    @staticmethod
    def _prototype(function: object, argtypes: list[object], restype: object) -> None:
        function.argtypes = argtypes
        function.restype = restype

    def _bind(self) -> None:
        kernel32 = self.kernel32
        shell32 = self.shell32
        self._prototype(kernel32.CloseHandle, [HANDLE], wintypes.BOOL)
        self._prototype(kernel32.LocalFree, [LPVOID], LPVOID)
        self._prototype(kernel32.GetCurrentProcess, [], HANDLE)
        self._prototype(
            kernel32.GetWindowsDirectoryW, [wintypes.LPWSTR, DWORD], DWORD
        )
        self._prototype(
            kernel32.CreateFileW,
            [
                wintypes.LPCWSTR,
                DWORD,
                DWORD,
                LPVOID,
                DWORD,
                DWORD,
                HANDLE,
            ],
            HANDLE,
        )
        self._prototype(
            kernel32.GetHandleInformation,
            [HANDLE, ctypes.POINTER(DWORD)],
            wintypes.BOOL,
        )
        self._prototype(
            kernel32.SetHandleInformation,
            [HANDLE, DWORD, DWORD],
            wintypes.BOOL,
        )
        self._prototype(
            kernel32.CreatePipe,
            [
                ctypes.POINTER(HANDLE),
                ctypes.POINTER(HANDLE),
                ctypes.POINTER(_SECURITY_ATTRIBUTES),
                DWORD,
            ],
            wintypes.BOOL,
        )
        self._prototype(
            kernel32.DuplicateHandle,
            [
                HANDLE,
                HANDLE,
                HANDLE,
                ctypes.POINTER(HANDLE),
                DWORD,
                wintypes.BOOL,
                DWORD,
            ],
            wintypes.BOOL,
        )
        self._prototype(
            kernel32.ReadFile,
            [HANDLE, LPVOID, DWORD, ctypes.POINTER(DWORD), LPVOID],
            wintypes.BOOL,
        )
        self._prototype(kernel32.CreateJobObjectW, [LPVOID, wintypes.LPCWSTR], HANDLE)
        self._prototype(
            kernel32.SetInformationJobObject,
            [HANDLE, ctypes.c_int, LPVOID, DWORD],
            wintypes.BOOL,
        )
        self._prototype(
            kernel32.QueryInformationJobObject,
            [HANDLE, ctypes.c_int, LPVOID, DWORD, ctypes.POINTER(DWORD)],
            wintypes.BOOL,
        )
        self._prototype(
            kernel32.IsProcessInJob,
            [HANDLE, HANDLE, ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
        )
        self._prototype(
            kernel32.TerminateJobObject, [HANDLE, DWORD], wintypes.BOOL
        )
        self._prototype(
            kernel32.InitializeProcThreadAttributeList,
            [LPVOID, DWORD, DWORD, ctypes.POINTER(SIZE_T)],
            wintypes.BOOL,
        )
        self._prototype(
            kernel32.UpdateProcThreadAttribute,
            [LPVOID, DWORD, SIZE_T, LPVOID, SIZE_T, LPVOID, LPVOID],
            wintypes.BOOL,
        )
        self._prototype(kernel32.DeleteProcThreadAttributeList, [LPVOID], None)
        self._prototype(
            kernel32.CreateProcessW,
            [
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                LPVOID,
                LPVOID,
                wintypes.BOOL,
                DWORD,
                LPVOID,
                wintypes.LPCWSTR,
                ctypes.POINTER(_STARTUPINFOW),
                ctypes.POINTER(_PROCESS_INFORMATION),
            ],
            wintypes.BOOL,
        )
        self._prototype(kernel32.ResumeThread, [HANDLE], DWORD)
        self._prototype(
            kernel32.WaitForSingleObject, [HANDLE, DWORD], DWORD
        )
        self._prototype(
            kernel32.GetExitCodeProcess,
            [HANDLE, ctypes.POINTER(DWORD)],
            wintypes.BOOL,
        )
        self._prototype(
            shell32.CommandLineToArgvW,
            [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)],
            ctypes.POINTER(wintypes.LPWSTR),
        )


_API: _Apis | None = None
_API_LOCK = threading.Lock()


def _apis() -> _Apis:
    global _API
    if _API is None:
        with _API_LOCK:
            if _API is None:
                _API = _Apis()
    return _API


class OwnedHandle:
    __slots__ = ("_state", "_value")

    def __init__(self, value: object) -> None:
        raw = (
            int(value.value or 0)
            if isinstance(value, ctypes.c_void_p)
            else int(value or 0)
        )
        if raw in {0, INVALID_HANDLE_VALUE}:
            _fail("sandbox_boundary_violation")
        self._value = raw
        self._state = "open"

    @property
    def value(self) -> int:
        if self._state != "open" or self._value == 0:
            _fail("sandbox_boundary_violation")
        return self._value

    @property
    def closed(self) -> bool:
        return self._state == "closed"

    def close(self) -> None:
        if self._state == "closed":
            return
        if self._state != "open" or self._value == 0:
            _fail("sandbox_cleanup_failed")
        raw = self._value
        try:
            if not _apis().kernel32.CloseHandle(HANDLE(raw)):
                _fail("sandbox_cleanup_failed")
            self._value = 0
            self._state = "closed"
        except RunnerWin32Error:
            raise
        except BaseException:
            # The native call may already have closed and recycled the value.
            # Never retry an ownership-uncertain handle.
            self._value = 0
            self._state = "uncertain"
            _fail("sandbox_cleanup_failed")

    def __enter__(self) -> "OwnedHandle":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _HandleSlot:
    __slots__ = ("owned", "raw", "uncertain")

    def __init__(self, raw: object = 0) -> None:
        self.raw = raw
        self.owned: OwnedHandle | None = None
        self.uncertain = False


class _HandleScope:
    """Own native acquisitions until their final aggregate is committed."""

    __slots__ = ("_slots",)

    def __init__(self) -> None:
        self._slots: list[_HandleSlot] = []

    def output_slot(self) -> _HandleSlot:
        slot = _HandleSlot(HANDLE())
        self._slots.append(slot)
        return slot

    def acquire_return(
        self,
        invoke: Callable[[], object],
        *,
        failure_code: str,
    ) -> _HandleSlot:
        slot = _HandleSlot()
        self._slots.append(slot)
        try:
            slot.raw = invoke()
        except BaseException:
            # A return-handle API may have acquired a native handle before a
            # Python-level interruption prevents the value from being stored.
            slot.uncertain = True
            raise
        if not _raw_handle_value(slot.raw):
            _fail(failure_code)
        return slot

    def adopt(self, slot: _HandleSlot) -> OwnedHandle:
        if slot not in self._slots or slot.owned is not None:
            _fail("sandbox_boundary_violation")
        owned = OwnedHandle(slot.raw)
        slot.owned = owned
        return owned

    def release(self, *handles: OwnedHandle) -> None:
        expected = {id(handle) for handle in handles}
        observed: set[int] = set()
        for slot in self._slots:
            owned = slot.owned
            if isinstance(owned, OwnedHandle):
                if not owned.closed:
                    observed.add(id(owned))
            elif _raw_handle_value(slot.raw):
                _fail("sandbox_boundary_violation")
        if len(expected) != len(handles) or observed != expected:
            _fail("sandbox_boundary_violation")

    def close_all(self) -> bool:
        cleanup_succeeded = True
        seen: set[int] = set()
        for slot in reversed(self._slots):
            try:
                raw = _raw_handle_value(slot.raw)
            except BaseException:
                cleanup_succeeded = False
                continue
            if slot.uncertain and not raw:
                cleanup_succeeded = False
            if not raw or raw in seen:
                continue
            seen.add(raw)
            owned = slot.owned
            try:
                if isinstance(owned, OwnedHandle):
                    if not owned.closed:
                        owned.close()
                elif not _apis().kernel32.CloseHandle(HANDLE(raw)):
                    cleanup_succeeded = False
            except BaseException:
                cleanup_succeeded = False
        return cleanup_succeeded


def _windows_directory() -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(_apis().kernel32.GetWindowsDirectoryW(buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        _fail("sandbox_unavailable")
    path = Path(buffer.value)
    if not path.is_absolute() or not path.is_dir():
        _fail("sandbox_unavailable")
    return path


def verified_windows_directory() -> Path:
    """Return the OS-reported absolute Windows directory."""

    return _windows_directory()


class NativeJob:
    __slots__ = ("_handle", "limits", "_terminated")

    def __init__(self, handle: OwnedHandle, limits: JobLimits) -> None:
        self._handle = handle
        self.limits = limits
        self._terminated = False

    @property
    def handle(self) -> OwnedHandle:
        return self._handle

    @property
    def closed(self) -> bool:
        return self._handle.closed

    def prove_configuration(self) -> None:
        extended = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        returned = DWORD()
        if not _apis().kernel32.QueryInformationJobObject(
            HANDLE(self._handle.value),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
            ctypes.byref(returned),
        ) or returned.value != ctypes.sizeof(extended):
            _fail("job_state_unproved")
        basic = extended.BasicLimitInformation
        expected_memory = self.limits.memory_mib * 1_048_576
        if (
            basic.LimitFlags != EXACT_JOB_LIMIT_FLAGS
            or basic.PerJobUserTimeLimit != self.limits.cpu_seconds * 10_000_000
            or basic.ActiveProcessLimit != self.limits.process_limit
            or extended.ProcessMemoryLimit != expected_memory
            or extended.JobMemoryLimit != expected_memory
        ):
            _fail("job_state_unproved")
    def contains(self, process: OwnedHandle) -> bool:
        member = wintypes.BOOL()
        if not _apis().kernel32.IsProcessInJob(
            HANDLE(process.value), HANDLE(self._handle.value), ctypes.byref(member)
        ):
            _fail("job_state_unproved")
        return bool(member.value)

    def accounting(self) -> JobAccounting:
        basic = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION_STRUCT()
        returned = DWORD()
        if not _apis().kernel32.QueryInformationJobObject(
            HANDLE(self._handle.value),
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(basic),
            ctypes.sizeof(basic),
            ctypes.byref(returned),
        ) or returned.value != ctypes.sizeof(basic):
            _fail("job_state_unproved")
        extended = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        if not _apis().kernel32.QueryInformationJobObject(
            HANDLE(self._handle.value),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
            ctypes.byref(returned),
        ) or returned.value != ctypes.sizeof(extended):
            _fail("job_state_unproved")
        if basic.TotalUserTime < 0:
            _fail("job_state_unproved")
        return JobAccounting(
            int(basic.TotalUserTime),
            int(extended.PeakJobMemoryUsed),
            int(basic.TotalProcesses),
            int(basic.ActiveProcesses),
        )

    def limit_violation_reason(self, accounting: JobAccounting) -> str | None:
        if not isinstance(accounting, JobAccounting):
            _fail("job_state_unproved")
        if accounting.total_user_time_100ns >= self.limits.cpu_seconds * 10_000_000:
            return "cpu_limit"
        if accounting.peak_job_memory_bytes >= self.limits.memory_mib * 1_048_576:
            return "memory_limit"
        return None

    def wait_for_zero(self, *, deadline: float) -> JobAccounting:
        while True:
            accounting = self.accounting()
            if accounting.active_processes == 0:
                if accounting.total_processes <= 0:
                    _fail("job_state_unproved")
                return accounting
            if time.monotonic() >= deadline:
                _fail("job_state_unproved")
            time.sleep(0.01)

    def terminate(self) -> None:
        if self._handle.closed:
            _fail("job_state_unproved")
        if not self._terminated:
            if not _apis().kernel32.TerminateJobObject(
                HANDLE(self._handle.value), 0xC000013A
            ):
                _fail("job_state_unproved")
            self._terminated = True

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "NativeJob":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def create_job(limits: JobLimits) -> NativeJob:
    if not isinstance(limits, JobLimits):
        _fail("sandbox_setup_failed")
    scope = _HandleScope()
    try:
        slot = scope.acquire_return(
            lambda: _apis().kernel32.CreateJobObjectW(None, None),
            failure_code="sandbox_setup_failed",
        )
        job = NativeJob(scope.adopt(slot), limits)
        extended = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        extended.BasicLimitInformation.LimitFlags = EXACT_JOB_LIMIT_FLAGS
        extended.BasicLimitInformation.PerJobUserTimeLimit = (
            limits.cpu_seconds * 10_000_000
        )
        extended.BasicLimitInformation.ActiveProcessLimit = limits.process_limit
        extended.ProcessMemoryLimit = limits.memory_mib * 1_048_576
        extended.JobMemoryLimit = limits.memory_mib * 1_048_576
        if not _apis().kernel32.SetInformationJobObject(
            HANDLE(job.handle.value),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
        ):
            _fail("sandbox_setup_failed")
        job.prove_configuration()
        scope.release(job.handle)
        return job
    except BaseException:
        if not scope.close_all():
            _fail("sandbox_cleanup_failed")
        raise


HANDLE_FLAG_INHERIT = 1
DUPLICATE_SAME_ACCESS = 2
GENERIC_READ = 0x80000000


def _make_noninheritable(handle: OwnedHandle) -> None:
    if not _apis().kernel32.SetHandleInformation(
        HANDLE(handle.value), HANDLE_FLAG_INHERIT, 0
    ):
        _fail("sandbox_boundary_violation")


def _prove_inheritability(handle: OwnedHandle, expected: bool) -> None:
    flags = DWORD()
    if not _apis().kernel32.GetHandleInformation(
        HANDLE(handle.value), ctypes.byref(flags)
    ):
        _fail("sandbox_boundary_violation")
    if bool(flags.value & HANDLE_FLAG_INHERIT) is not expected:
        _fail("sandbox_boundary_violation")


def _duplicate_inheritable(
    source: OwnedHandle,
    scope: _HandleScope,
) -> OwnedHandle:
    slot = scope.output_slot()
    process = _apis().kernel32.GetCurrentProcess()
    if not _apis().kernel32.DuplicateHandle(
        process,
        HANDLE(source.value),
        process,
        ctypes.byref(slot.raw),
        0,
        True,
        DUPLICATE_SAME_ACCESS,
    ):
        _fail("sandbox_setup_failed")
    duplicate = scope.adopt(slot)
    _prove_inheritability(duplicate, True)
    return duplicate


def _pipe(scope: _HandleScope) -> tuple[OwnedHandle, OwnedHandle]:
    read_slot = scope.output_slot()
    write_slot = scope.output_slot()
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), None, False
    )
    if not _apis().kernel32.CreatePipe(
        ctypes.byref(read_slot.raw),
        ctypes.byref(write_slot.raw),
        ctypes.byref(attributes),
        0,
    ):
        _fail("sandbox_setup_failed")
    read_handle = scope.adopt(read_slot)
    write_handle = scope.adopt(write_slot)
    _make_noninheritable(read_handle)
    _make_noninheritable(write_handle)
    duplicate = _duplicate_inheritable(write_handle, scope)
    write_handle.close()
    return read_handle, duplicate


class StdioPipes:
    __slots__ = (
        "stdin_child",
        "stdout_child",
        "stderr_child",
        "stdout_parent",
        "stderr_parent",
    )

    def __init__(
        self,
        stdin_child: OwnedHandle,
        stdout_child: OwnedHandle,
        stderr_child: OwnedHandle,
        stdout_parent: OwnedHandle,
        stderr_parent: OwnedHandle,
    ) -> None:
        self.stdin_child = stdin_child
        self.stdout_child = stdout_child
        self.stderr_child = stderr_child
        self.stdout_parent = stdout_parent
        self.stderr_parent = stderr_parent

    @property
    def inherited_values(self) -> tuple[int, int, int]:
        return (
            self.stdin_child.value,
            self.stdout_child.value,
            self.stderr_child.value,
        )

    def prove_before_create(self) -> None:
        for handle in (self.stdin_child, self.stdout_child, self.stderr_child):
            _prove_inheritability(handle, True)
        for handle in (self.stdout_parent, self.stderr_parent):
            _prove_inheritability(handle, False)
        if len(set(self.inherited_values)) != 3:
            _fail("sandbox_boundary_violation")

    def close_child_ends(self) -> None:
        errors = False
        for handle in (self.stdin_child, self.stdout_child, self.stderr_child):
            if not handle.closed:
                try:
                    handle.close()
                except BaseException:
                    errors = True
        if errors:
            _fail("sandbox_cleanup_failed")

    def close_parent_ends(self) -> None:
        errors = False
        for handle in (self.stdout_parent, self.stderr_parent):
            if not handle.closed:
                try:
                    handle.close()
                except BaseException:
                    errors = True
        if errors:
            _fail("sandbox_cleanup_failed")


def create_stdio_pipes() -> StdioPipes:
    scope = _HandleScope()
    try:
        stdout_parent, stdout_child = _pipe(scope)
        stderr_parent, stderr_child = _pipe(scope)
        nul_slot = scope.acquire_return(
            lambda: _apis().kernel32.CreateFileW(
                "NUL",
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None,
            ),
            failure_code="sandbox_setup_failed",
        )
        nul = scope.adopt(nul_slot)
        _make_noninheritable(nul)
        stdin_child = _duplicate_inheritable(nul, scope)
        nul.close()
        pipes = StdioPipes(
            stdin_child,
            stdout_child,
            stderr_child,
            stdout_parent,
            stderr_parent,
        )
        pipes.prove_before_create()
        scope.release(
            stdin_child,
            stdout_child,
            stderr_child,
            stdout_parent,
            stderr_parent,
        )
        return pipes
    except BaseException:
        if not scope.close_all():
            _fail("sandbox_cleanup_failed")
        raise


class _AttributeList:
    __slots__ = ("_buffer", "pointer", "_keepers", "attribute_ids")

    def __init__(self, count: int) -> None:
        size = SIZE_T()
        _apis().kernel32.InitializeProcThreadAttributeList(
            None, count, 0, ctypes.byref(size)
        )
        if (
            ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER
            or size.value <= 0
            or size.value > 1_048_576
        ):
            _fail("sandbox_setup_failed")
        self._buffer = ctypes.create_string_buffer(size.value)
        self.pointer = ctypes.cast(self._buffer, LPVOID)
        self._keepers: list[object] = []
        self.attribute_ids: list[int] = []
        if not _apis().kernel32.InitializeProcThreadAttributeList(
            self.pointer, count, 0, ctypes.byref(size)
        ):
            _fail("sandbox_setup_failed")

    def add(self, attribute: int, value: object, size: int) -> None:
        self._keepers.append(value)
        pointer = ctypes.cast(ctypes.byref(value), LPVOID)
        if not _apis().kernel32.UpdateProcThreadAttribute(
            self.pointer, 0, attribute, pointer, size, None, None
        ):
            _fail("sandbox_setup_failed")
        self.attribute_ids.append(attribute)

    def close(self) -> None:
        if self.pointer:
            _apis().kernel32.DeleteProcThreadAttributeList(self.pointer)
            self.pointer = LPVOID()
        self._keepers.clear()

    def __enter__(self) -> "_AttributeList":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class SuspendedChild:
    __slots__ = ("process", "thread", "process_id", "_resumed")

    def __init__(
        self,
        process: OwnedHandle,
        thread: OwnedHandle,
        process_id: int,
    ) -> None:
        self.process = process
        self.thread = thread
        self.process_id = process_id
        self._resumed = False

    @property
    def resumed(self) -> bool:
        return self._resumed

    def resume_once(self) -> None:
        if self._resumed or self.thread.closed:
            _fail("process_resume_failed")
        previous = int(_apis().kernel32.ResumeThread(HANDLE(self.thread.value)))
        if previous != 1:
            _fail("process_resume_failed")
        self._resumed = True
        self.thread.close()

    def poll(self) -> int | None:
        if not self.wait(0):
            return None
        code = DWORD()
        if not _apis().kernel32.GetExitCodeProcess(
            HANDLE(self.process.value), ctypes.byref(code)
        ):
            _fail("process_wait_failed")
        return int(code.value)

    def wait(self, milliseconds: int) -> bool:
        if milliseconds < 0 or milliseconds > 60_000:
            _fail("process_wait_failed")
        result = int(
            _apis().kernel32.WaitForSingleObject(
                HANDLE(self.process.value), milliseconds
            )
        )
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        _fail("process_wait_failed")

    def close(self) -> None:
        errors = False
        for handle in (self.thread, self.process):
            if not handle.closed:
                try:
                    handle.close()
                except BaseException:
                    errors = True
        if errors:
            _fail("sandbox_cleanup_failed")


def command_line_to_argv(command_line: str) -> tuple[str, ...]:
    count = ctypes.c_int()
    pointer = _apis().shell32.CommandLineToArgvW(
        command_line, ctypes.byref(count)
    )
    if not pointer or count.value <= 0 or count.value > 4096:
        _fail("sandbox_setup_failed")
    try:
        return tuple(pointer[index] for index in range(count.value))
    finally:
        _apis().kernel32.LocalFree(pointer)


def _raw_handle_value(value: object) -> int:
    raw = (
        int(value.value or 0)
        if isinstance(value, ctypes.c_void_p)
        else int(value or 0)
    )
    return 0 if raw in {0, INVALID_HANDLE_VALUE} else raw


def _process_information_has_handle(process_info: _PROCESS_INFORMATION) -> bool:
    try:
        return bool(
            _raw_handle_value(process_info.hProcess)
            or _raw_handle_value(process_info.hThread)
        )
    except BaseException:
        return True


def _retire_created_process_handles(
    *,
    job: NativeJob,
    process_info: _PROCESS_INFORMATION,
    process_handle: OwnedHandle | None,
    thread_handle: OwnedHandle | None,
) -> bool:
    """Best-effort each post-CreateProcess owner exactly once."""

    cleanup_failed = False
    try:
        job.terminate()
    except BaseException:
        cleanup_failed = True
    for owned, raw in (
        (thread_handle, process_info.hThread),
        (process_handle, process_info.hProcess),
    ):
        try:
            if isinstance(owned, OwnedHandle):
                if not owned.closed:
                    owned.close()
                continue
            raw_value = _raw_handle_value(raw)
            if not raw_value or not _apis().kernel32.CloseHandle(
                HANDLE(raw_value)
            ):
                cleanup_failed = True
        except BaseException:
            cleanup_failed = True
    return not cleanup_failed


def create_suspended_child(
    *,
    application: str | os.PathLike[str],
    command_line: str,
    environment_block: str,
    cwd: str | os.PathLike[str],
    job: NativeJob,
    stdio: StdioPipes,
) -> SuspendedChild:
    """Create an ordinary child already assigned to the Job, still suspended."""

    if (
        not isinstance(job, NativeJob)
        or not isinstance(stdio, StdioPipes)
        or type(command_line) is not str
        or not command_line
        or "\0" in command_line
        or len(command_line.encode("utf-16-le")) // 2 > 24_576
        or type(environment_block) is not str
        or not environment_block.endswith("\0\0")
    ):
        _fail("sandbox_setup_failed")
    application_path = Path(application)
    cwd_path = Path(cwd)
    if (
        not application_path.is_absolute()
        or not application_path.is_file()
        or not cwd_path.is_absolute()
        or not cwd_path.is_dir()
    ):
        _fail("sandbox_setup_failed")
    stdio.prove_before_create()
    job.prove_configuration()
    process_info = _PROCESS_INFORMATION()
    process_handle = None
    thread_handle = None
    child = None
    create_entered = False
    created = False
    try:
        with _AttributeList(2) as attributes:
            job_handles = (HANDLE * 1)(HANDLE(job.handle.value))
            inherited = (HANDLE * 3)(
                *(HANDLE(value) for value in stdio.inherited_values)
            )
            attributes.add(
                PROC_THREAD_ATTRIBUTE_JOB_LIST,
                job_handles,
                ctypes.sizeof(job_handles),
            )
            attributes.add(
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                inherited,
                ctypes.sizeof(inherited),
            )
            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = HANDLE(stdio.stdin_child.value)
            startup.StartupInfo.hStdOutput = HANDLE(stdio.stdout_child.value)
            startup.StartupInfo.hStdError = HANDLE(stdio.stderr_child.value)
            startup.lpAttributeList = attributes.pointer
            command_buffer = ctypes.create_unicode_buffer(command_line)
            environment_buffer = ctypes.create_unicode_buffer(environment_block)
            create_entered = True
            created = bool(
                _apis().kernel32.CreateProcessW(
                    str(application_path),
                    command_buffer,
                    None,
                    None,
                    True,
                    EXTENDED_STARTUPINFO_PRESENT
                    | CREATE_SUSPENDED
                    | CREATE_NO_WINDOW
                    | CREATE_UNICODE_ENVIRONMENT,
                    ctypes.cast(environment_buffer, LPVOID),
                    str(cwd_path),
                    ctypes.byref(startup.StartupInfo),
                    ctypes.byref(process_info),
                )
            )
            if not created:
                _fail("process_create_failed")
            process_handle = OwnedHandle(process_info.hProcess)
            thread_handle = OwnedHandle(process_info.hThread)
            child = SuspendedChild(
                process_handle,
                thread_handle,
                int(process_info.dwProcessId),
            )
            if not job.contains(child.process):
                _fail("job_state_unproved", after_create=True)
        if child is None:
            _fail("sandbox_boundary_violation", after_create=True)
        return child
    except RunnerWin32Error as error:
        after_create = bool(
            error.after_create
            or created
            or _process_information_has_handle(process_info)
        )
        if not after_create:
            raise
        cleanup_succeeded = _retire_created_process_handles(
            job=job,
            process_info=process_info,
            process_handle=process_handle,
            thread_handle=thread_handle,
        )
        _fail(
            error.code if cleanup_succeeded else "sandbox_cleanup_failed",
            after_create=True,
        )
    except BaseException:
        after_create = bool(
            create_entered
            or created
            or _process_information_has_handle(process_info)
        )
        if after_create:
            cleanup_succeeded = _retire_created_process_handles(
                job=job,
                process_info=process_info,
                process_handle=process_handle,
                thread_handle=thread_handle,
            )
            _fail(
                (
                    "sandbox_boundary_violation"
                    if cleanup_succeeded
                    else "sandbox_cleanup_failed"
                ),
                after_create=True,
            )
        raise


def read_pipe_chunk(handle: OwnedHandle, maximum: int = 65_536) -> bytes | None:
    if type(maximum) is not int or not 1 <= maximum <= 65_536:
        _fail("pipe_drain_failed")
    buffer = ctypes.create_string_buffer(maximum)
    read = DWORD()
    if _apis().kernel32.ReadFile(
        HANDLE(handle.value), buffer, maximum, ctypes.byref(read), None
    ):
        if read.value > maximum:
            _fail("pipe_drain_failed")
        return bytes(buffer.raw[: read.value])
    if ctypes.get_last_error() in {ERROR_BROKEN_PIPE, ERROR_NO_DATA}:
        return None
    _fail("pipe_drain_failed")
