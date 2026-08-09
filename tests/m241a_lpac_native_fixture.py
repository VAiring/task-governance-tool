"""Disposable Windows fixture for the TG-M24.1A private LPAC seam.

The fixture alone owns AppContainer profile, Job, pipe, and residue lifecycle.
No child is ever resumed, so the target command cannot execute project bytes.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import nullcontext
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from task_governance_tool import _verification_runner_lpac_win32 as lpac


DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
SIZE_T = ctypes.c_size_t
LONGLONG = ctypes.c_int64
ULONGLONG = ctypes.c_uint64
ULONG_PTR = ctypes.c_size_t
HANDLE = ctypes.c_void_p
LPVOID = ctypes.c_void_p
PSID = ctypes.c_void_p

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
HANDLE_FLAG_INHERIT = 1
DUPLICATE_SAME_ACCESS = 2
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3

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
JOB_OBJECT_BASIC_UI_RESTRICTIONS = 4
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
EXACT_JOB_UI_FLAGS = 0xFF

HRESULT_ALREADY_EXISTS = 0x800700B7
HRESULT_FILE_NOT_FOUND = 0x80070002
HRESULT_NOT_FOUND = 0x80070490


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = (
        ("nLength", DWORD),
        ("lpSecurityDescriptor", LPVOID),
        ("bInheritHandle", wintypes.BOOL),
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


class _JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):
    _fields_ = (("UIRestrictionsClass", DWORD),)


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
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


def _hresult(value: int) -> int:
    return int(value) & 0xFFFFFFFF


class _FixtureApis:
    def __init__(self) -> None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            raise lpac.LpacProofError("sandbox_unavailable")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.userenv = ctypes.WinDLL("userenv", use_last_error=True)
        self.ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        k = self.kernel32
        a = self.advapi32
        u = self.userenv
        self._prototype(k.GetCurrentProcess, [], HANDLE)
        self._prototype(k.GetWindowsDirectoryW, [wintypes.LPWSTR, DWORD], DWORD)
        self._prototype(k.CreateJobObjectW, [LPVOID, wintypes.LPCWSTR], HANDLE)
        self._prototype(k.SetInformationJobObject, [HANDLE, ctypes.c_int, LPVOID, DWORD], wintypes.BOOL)
        self._prototype(k.QueryInformationJobObject, [HANDLE, ctypes.c_int, LPVOID, DWORD, ctypes.POINTER(DWORD)], wintypes.BOOL)
        self._prototype(k.IsProcessInJob, [HANDLE, HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL)
        self._prototype(k.TerminateJobObject, [HANDLE, DWORD], wintypes.BOOL)
        self._prototype(k.CreatePipe, [ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE), ctypes.POINTER(_SECURITY_ATTRIBUTES), DWORD], wintypes.BOOL)
        self._prototype(k.SetHandleInformation, [HANDLE, DWORD, DWORD], wintypes.BOOL)
        self._prototype(k.GetHandleInformation, [HANDLE, ctypes.POINTER(DWORD)], wintypes.BOOL)
        self._prototype(k.DuplicateHandle, [HANDLE, HANDLE, HANDLE, ctypes.POINTER(HANDLE), DWORD, wintypes.BOOL, DWORD], wintypes.BOOL)
        self._prototype(k.CreateFileW, [wintypes.LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE], HANDLE)
        self._prototype(a.FreeSid, [PSID], LPVOID)
        self._prototype(u.CreateAppContainerProfile, [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(lpac._SID_AND_ATTRIBUTES), DWORD, ctypes.POINTER(PSID)], ctypes.c_long)
        self._prototype(u.DeleteAppContainerProfile, [wintypes.LPCWSTR], ctypes.c_long)
        self._prototype(u.GetAppContainerFolderPath, [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR)], ctypes.c_long)
        self._prototype(self.ole32.CoTaskMemFree, [LPVOID], None)

    @staticmethod
    def _prototype(function: object, arguments: list[object], result: object) -> None:
        function.argtypes = arguments
        function.restype = result


_API: _FixtureApis | None = None


def _apis() -> _FixtureApis:
    global _API
    if _API is None:
        _API = _FixtureApis()
    return _API


@dataclass(frozen=True)
class JobZeroProof:
    total_processes: int
    active_processes: int


@dataclass(frozen=True)
class NativeProbeResult:
    outcome: str
    reason: str | None
    route: str | None
    native_selector: bool
    controls: int
    grants: tuple[tuple[str, bool], ...]
    creations: tuple[lpac.CreationAttributeProof, ...]
    jobs: tuple[JobZeroProof, ...]
    resumes: tuple[int, ...]
    all_handles_closed: bool
    profile_absent: bool


class _FixtureProfile:
    def __init__(self) -> None:
        self.moniker = "OpenAI.TaskGov.Runner." + uuid.uuid4().hex[:16]
        self.sid = ""
        self.created = False

    def create(self) -> None:
        raw = PSID()
        result = _hresult(
            _apis().userenv.CreateAppContainerProfile(
                self.moniker,
                "TaskGov M24.1A fixture",
                "Disposable suspended LPAC portability fixture",
                None,
                0,
                ctypes.byref(raw),
            )
        )
        if result == HRESULT_ALREADY_EXISTS or result != 0 or not raw:
            raise lpac.LpacProofError("sandbox_setup_failed")
        self.created = True
        try:
            self.sid = lpac._sid_to_string(raw)
        finally:
            _apis().advapi32.FreeSid(raw)
        if not self.sid.startswith("S-1-15-2-"):
            raise lpac.LpacProofError("sandbox_boundary_violation")

    def delete(self) -> None:
        if not self.created:
            return
        result = _hresult(_apis().userenv.DeleteAppContainerProfile(self.moniker))
        if result not in {0, HRESULT_FILE_NOT_FOUND, HRESULT_NOT_FOUND}:
            raise lpac.LpacProofError("sandbox_cleanup_failed")
        self.created = False

    def is_absent(self) -> bool:
        raw = wintypes.LPWSTR()
        result = _hresult(
            _apis().userenv.GetAppContainerFolderPath(self.sid, ctypes.byref(raw))
        )
        if raw:
            _apis().ole32.CoTaskMemFree(raw)
        if result in {HRESULT_FILE_NOT_FOUND, HRESULT_NOT_FOUND}:
            return True
        if result == 0:
            return False
        raise lpac.LpacProofError("sandbox_cleanup_failed")


class _FixtureJob:
    CPU_SECONDS = 10
    MEMORY_MIB = 128
    PROCESS_LIMIT = 1

    def __init__(self, handle: lpac.OwnedHandle) -> None:
        self.handle = handle

    @classmethod
    def create(cls) -> "_FixtureJob":
        raw = _apis().kernel32.CreateJobObjectW(None, None)
        if int(raw or 0) in {0, INVALID_HANDLE_VALUE}:
            raise lpac.LpacProofError("sandbox_setup_failed")
        result = cls(lpac.OwnedHandle(raw))
        try:
            extended = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            extended.BasicLimitInformation.LimitFlags = EXACT_JOB_LIMIT_FLAGS
            extended.BasicLimitInformation.PerJobUserTimeLimit = (
                cls.CPU_SECONDS * 10_000_000
            )
            extended.BasicLimitInformation.ActiveProcessLimit = cls.PROCESS_LIMIT
            extended.ProcessMemoryLimit = cls.MEMORY_MIB * 1_048_576
            extended.JobMemoryLimit = cls.MEMORY_MIB * 1_048_576
            ui = _JOBOBJECT_BASIC_UI_RESTRICTIONS(EXACT_JOB_UI_FLAGS)
            if not _apis().kernel32.SetInformationJobObject(
                HANDLE(result.handle.value),
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(extended),
                ctypes.sizeof(extended),
            ) or not _apis().kernel32.SetInformationJobObject(
                HANDLE(result.handle.value),
                JOB_OBJECT_BASIC_UI_RESTRICTIONS,
                ctypes.byref(ui),
                ctypes.sizeof(ui),
            ):
                raise lpac.LpacProofError("sandbox_setup_failed")
            result.prove_configuration()
            return result
        except BaseException:
            result.close()
            raise

    def prove_configuration(self) -> None:
        returned = DWORD()
        extended = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        ui = _JOBOBJECT_BASIC_UI_RESTRICTIONS()
        if not _apis().kernel32.QueryInformationJobObject(
            HANDLE(self.handle.value),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
            ctypes.byref(returned),
        ) or returned.value != ctypes.sizeof(extended):
            raise lpac.LpacProofError("sandbox_boundary_violation")
        basic = extended.BasicLimitInformation
        expected_memory = self.MEMORY_MIB * 1_048_576
        if (
            basic.LimitFlags != EXACT_JOB_LIMIT_FLAGS
            or basic.PerJobUserTimeLimit != self.CPU_SECONDS * 10_000_000
            or basic.ActiveProcessLimit != self.PROCESS_LIMIT
            or extended.ProcessMemoryLimit != expected_memory
            or extended.JobMemoryLimit != expected_memory
            or not _apis().kernel32.QueryInformationJobObject(
                HANDLE(self.handle.value),
                JOB_OBJECT_BASIC_UI_RESTRICTIONS,
                ctypes.byref(ui),
                ctypes.sizeof(ui),
                ctypes.byref(returned),
            )
            or returned.value != ctypes.sizeof(ui)
            or ui.UIRestrictionsClass != EXACT_JOB_UI_FLAGS
        ):
            raise lpac.LpacProofError("sandbox_boundary_violation")

    def contains(self, process: lpac.OwnedHandle) -> bool:
        member = wintypes.BOOL()
        if not _apis().kernel32.IsProcessInJob(
            HANDLE(process.value), HANDLE(self.handle.value), ctypes.byref(member)
        ):
            raise lpac.LpacProofError("sandbox_boundary_violation")
        return bool(member.value)

    def accounting(self) -> JobZeroProof:
        information = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        returned = DWORD()
        if not _apis().kernel32.QueryInformationJobObject(
            HANDLE(self.handle.value),
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
            ctypes.byref(returned),
        ) or returned.value != ctypes.sizeof(information):
            raise lpac.LpacProofError("sandbox_cleanup_failed")
        return JobZeroProof(
            int(information.TotalProcesses), int(information.ActiveProcesses)
        )

    def terminate_and_prove_zero(self) -> JobZeroProof:
        if not _apis().kernel32.TerminateJobObject(
            HANDLE(self.handle.value), 0xC000013A
        ):
            raise lpac.LpacProofError("sandbox_cleanup_failed")
        deadline = time.monotonic() + 10.0
        while True:
            proof = self.accounting()
            if proof.active_processes == 0:
                return proof
            if time.monotonic() >= deadline:
                raise lpac.LpacProofError("sandbox_cleanup_failed")
            time.sleep(0.01)

    def close(self) -> None:
        if not self.handle.closed:
            self.handle.close()


class _FixtureStdio:
    def __init__(
        self,
        stdin_child: lpac.OwnedHandle,
        stdout_child: lpac.OwnedHandle,
        stderr_child: lpac.OwnedHandle,
        stdout_parent: lpac.OwnedHandle,
        stderr_parent: lpac.OwnedHandle,
    ) -> None:
        self.stdin_child = stdin_child
        self.stdout_child = stdout_child
        self.stderr_child = stderr_child
        self.stdout_parent = stdout_parent
        self.stderr_parent = stderr_parent

    @staticmethod
    def _make_noninheritable(handle: lpac.OwnedHandle) -> None:
        if not _apis().kernel32.SetHandleInformation(
            HANDLE(handle.value), HANDLE_FLAG_INHERIT, 0
        ):
            raise lpac.LpacProofError("sandbox_setup_failed")

    @staticmethod
    def _duplicate_inheritable(source: lpac.OwnedHandle) -> lpac.OwnedHandle:
        raw = HANDLE()
        process = _apis().kernel32.GetCurrentProcess()
        if not _apis().kernel32.DuplicateHandle(
            process,
            HANDLE(source.value),
            process,
            ctypes.byref(raw),
            0,
            True,
            DUPLICATE_SAME_ACCESS,
        ):
            raise lpac.LpacProofError("sandbox_setup_failed")
        duplicate = lpac.OwnedHandle(raw)
        flags = DWORD()
        if not _apis().kernel32.GetHandleInformation(
            HANDLE(duplicate.value), ctypes.byref(flags)
        ) or not (flags.value & HANDLE_FLAG_INHERIT):
            duplicate.close()
            raise lpac.LpacProofError("sandbox_boundary_violation")
        return duplicate

    @classmethod
    def _pipe(cls) -> tuple[lpac.OwnedHandle, lpac.OwnedHandle]:
        read = HANDLE()
        write = HANDLE()
        attributes = _SECURITY_ATTRIBUTES(
            ctypes.sizeof(_SECURITY_ATTRIBUTES), None, False
        )
        if not _apis().kernel32.CreatePipe(
            ctypes.byref(read), ctypes.byref(write), ctypes.byref(attributes), 0
        ):
            raise lpac.LpacProofError("sandbox_setup_failed")
        read_handle = lpac.OwnedHandle(read)
        write_handle = lpac.OwnedHandle(write)
        try:
            cls._make_noninheritable(read_handle)
            cls._make_noninheritable(write_handle)
            duplicate = cls._duplicate_inheritable(write_handle)
            write_handle.close()
            return read_handle, duplicate
        except BaseException:
            for handle in (read_handle, write_handle):
                if not handle.closed:
                    handle.close()
            raise

    @classmethod
    def create(cls) -> "_FixtureStdio":
        stdout_parent, stdout_child = cls._pipe()
        stderr_parent: lpac.OwnedHandle | None = None
        stderr_child: lpac.OwnedHandle | None = None
        nul: lpac.OwnedHandle | None = None
        stdin_child: lpac.OwnedHandle | None = None
        try:
            stderr_parent, stderr_child = cls._pipe()
            raw = _apis().kernel32.CreateFileW(
                "NUL",
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if int(raw or 0) in {0, INVALID_HANDLE_VALUE}:
                raise lpac.LpacProofError("sandbox_setup_failed")
            nul = lpac.OwnedHandle(raw)
            cls._make_noninheritable(nul)
            stdin_child = cls._duplicate_inheritable(nul)
            nul.close()
            return cls(
                stdin_child,
                stdout_child,
                stderr_child,
                stdout_parent,
                stderr_parent,
            )
        except BaseException:
            for handle in (
                stdin_child,
                nul,
                stdout_parent,
                stdout_child,
                stderr_parent,
                stderr_child,
            ):
                if isinstance(handle, lpac.OwnedHandle) and not handle.closed:
                    handle.close()
            raise

    def resources(self, job: _FixtureJob) -> lpac.SuspendedLaunchResources:
        return lpac.SuspendedLaunchResources(
            job.handle,
            self.stdin_child,
            self.stdout_child,
            self.stderr_child,
        )

    def close(self) -> None:
        failed = False
        for handle in (
            self.stdin_child,
            self.stdout_child,
            self.stderr_child,
            self.stdout_parent,
            self.stderr_parent,
        ):
            if not handle.closed:
                try:
                    handle.close()
                except lpac.LpacProofError:
                    failed = True
        if failed:
            raise lpac.LpacProofError("sandbox_cleanup_failed")

    @property
    def closed(self) -> bool:
        return all(
            handle.closed
            for handle in (
                self.stdin_child,
                self.stdout_child,
                self.stderr_child,
                self.stdout_parent,
                self.stderr_parent,
            )
        )


def _environment_block(scratch: str) -> str:
    """Build the closed no-credential environment for a never-resumed child."""

    scratch_path = Path(scratch)
    if not scratch_path.is_absolute() or not scratch_path.is_dir() or "\0" in scratch:
        raise lpac.LpacProofError("sandbox_setup_failed")
    buffer = ctypes.create_unicode_buffer(32_768)
    length = int(_apis().kernel32.GetWindowsDirectoryW(buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        raise lpac.LpacProofError("sandbox_unavailable")
    windows = str(Path(buffer.value))
    if not Path(windows).is_absolute() or "\0" in windows:
        raise lpac.LpacProofError("sandbox_unavailable")
    directories = {
        "APPDATA": scratch_path / "roaming",
        "HOME": scratch_path / "home",
        "LOCALAPPDATA": scratch_path / "local",
        "TEMP": scratch_path / "tmp",
        "TMP": scratch_path / "tmp",
        "USERPROFILE": scratch_path / "home",
    }
    for directory in set(directories.values()):
        directory.mkdir(parents=False, exist_ok=False)
    values = {
        **{key: str(value) for key, value in directories.items()},
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "SystemRoot": windows,
        "WINDIR": windows,
    }
    items = tuple(sorted(values.items(), key=lambda item: item[0].casefold()))
    return "\0".join(f"{key}={value}" for key, value in items) + "\0\0"


def _map_outcome(error: lpac.LpacProofError) -> tuple[str, str]:
    if error.code == "sandbox_cleanup_failed":
        return "sandbox_cleanup_failed", error.code
    return "sandbox_violation", error.code


def run_native_probe(
    *,
    selector_override: str | None = None,
    semantic_fault: bool = False,
    cleanup_uncertain: bool = False,
    profile_absence_uncertain: bool = False,
) -> NativeProbeResult:
    if selector_override not in {
        None,
        lpac.LPAC_PROOF_CLASS_46,
        lpac.LPAC_PROOF_ACCESS_CHECK,
        "unknown",
    }:
        raise ValueError("unsupported fixture selector")
    temporary = tempfile.TemporaryDirectory(prefix="taskgov-m241a-lpac-")
    profile = _FixtureProfile()
    jobs: list[_FixtureJob] = []
    stdios: list[_FixtureStdio] = []
    children: list[lpac._SuspendedProcess] = []
    creations: list[lpac.CreationAttributeProof] = []
    zero_proofs: list[JobZeroProof] = []
    grants: list[tuple[str, bool]] = []
    route: str | None = None
    controls = 0
    outcome = "pass"
    reason: str | None = None
    profile_absent = False
    cleanup_failed = False
    try:
        profile.create()
        command_line = subprocess.list2cmdline(
            [sys.executable, "-c", "raise SystemExit(0)"]
        )
        spec = lpac.SuspendedLaunchSpec(
            str(Path(sys.executable).resolve()),
            command_line,
            _environment_block(str(Path(temporary.name).resolve())),
            str(Path(temporary.name).resolve()),
            profile.sid,
        )
        lpac_job = _FixtureJob.create()
        lpac_stdio = _FixtureStdio.create()
        jobs.append(lpac_job)
        stdios.append(lpac_stdio)
        lpac_child = lpac.create_suspended_lpac_child(
            spec, lpac_stdio.resources(lpac_job)
        )
        children.append(lpac_child)
        creations.append(lpac_child.creation)
        if not lpac_job.contains(lpac_child.process):
            raise lpac.LpacProofError("sandbox_boundary_violation")

        if selector_override is None:
            selector_context = nullcontext()
        elif selector_override == "unknown":
            selector_context = patch.object(
                lpac,
                "_query_lpac_class46_dword",
                side_effect=lpac.LpacProofError("sandbox_boundary_violation"),
            )
        else:
            selector_context = patch.object(
                lpac,
                "_query_lpac_class46_dword",
                return_value=selector_override,
            )
        with selector_context:
            route_proof = lpac.prove_lpac_route(lpac_child.primary_token, profile.sid)
        route = route_proof.route
        if route == lpac.LPAC_PROOF_ACCESS_CHECK:
            control_job = _FixtureJob.create()
            control_stdio = _FixtureStdio.create()
            jobs.append(control_job)
            stdios.append(control_stdio)
            control = lpac.create_suspended_normal_control(
                spec, control_stdio.resources(control_job)
            )
            controls = 1
            children.append(control)
            creations.append(control.creation)
            if not control_job.contains(control.process):
                raise lpac.LpacProofError("sandbox_boundary_violation")
            original_grant = lpac._appcontainer_grant_allowed

            def observing_grant(token: lpac.OwnedHandle, sid: str) -> bool:
                value = original_grant(token, sid)
                grants.append((sid, value))
                if semantic_fault and len(grants) == 2:
                    return True
                return value

            with patch.object(
                lpac, "_appcontainer_grant_allowed", new=observing_grant
            ):
                lpac.prove_access_check_semantics(control, lpac_child, profile.sid)
        elif route != lpac.LPAC_PROOF_CLASS_46:
            raise lpac.LpacProofError("sandbox_boundary_violation")
    except lpac.LpacProofError as error:
        outcome, reason = _map_outcome(error)
    except BaseException:
        outcome, reason = "sandbox_violation", "sandbox_boundary_violation"
    finally:
        for job in reversed(jobs):
            try:
                zero_proofs.append(job.terminate_and_prove_zero())
            except lpac.LpacProofError:
                cleanup_failed = True
        for child in reversed(children):
            try:
                child.close()
            except lpac.LpacProofError:
                cleanup_failed = True
        for stdio in reversed(stdios):
            try:
                stdio.close()
            except lpac.LpacProofError:
                cleanup_failed = True
        for job in reversed(jobs):
            try:
                job.close()
            except lpac.LpacProofError:
                cleanup_failed = True
        try:
            profile.delete()
            profile_absent = profile.is_absent()
            if not profile_absent:
                cleanup_failed = True
        except lpac.LpacProofError:
            cleanup_failed = True
        temporary.cleanup()
    all_handles_closed = all(child.closed for child in children) and all(
        stdio.closed for stdio in stdios
    ) and all(job.handle.closed for job in jobs)
    if not all_handles_closed:
        cleanup_failed = True
    if cleanup_uncertain or profile_absence_uncertain:
        cleanup_failed = True
    if cleanup_failed:
        outcome, reason = "sandbox_cleanup_failed", "sandbox_cleanup_failed"
    return NativeProbeResult(
        outcome,
        reason,
        route,
        selector_override is None,
        controls,
        tuple(grants),
        tuple(creations),
        tuple(reversed(zero_proofs)),
        (),
        all_handles_closed,
        profile_absent,
    )
