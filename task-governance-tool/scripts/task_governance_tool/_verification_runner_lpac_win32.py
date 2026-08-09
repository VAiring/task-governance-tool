"""Private inactive Win32 LPAC portability proof seam.

TG-M24.1A adds this module only as a directly testable security-boundary
primitive.  It is deliberately not imported by the public Runner or CLI.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import threading
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn


_SAFE_CODES = frozenset(
    {
        "sandbox_unavailable",
        "sandbox_setup_failed",
        "sandbox_boundary_violation",
        "process_create_failed",
        "sandbox_cleanup_failed",
    }
)


class LpacProofError(RuntimeError):
    """Sanitized fail-closed result from the private LPAC seam."""

    def __init__(self, code: str, *, after_create: bool = False) -> None:
        if code not in _SAFE_CODES:
            code = "sandbox_boundary_violation"
        super().__init__("Windows LPAC portability proof failed closed")
        self.code = code
        self.after_create = after_create is True


def _fail(code: str, *, after_create: bool = False) -> NoReturn:
    raise LpacProofError(code, after_create=after_create)


DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
SIZE_T = ctypes.c_size_t
HANDLE = ctypes.c_void_p
PSID = ctypes.c_void_p
LPVOID = ctypes.c_void_p

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_INVALID_PARAMETER = 87

TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_IMPERSONATE = 0x0004
TOKEN_USER = 1
TOKEN_TYPE = 8
TOKEN_IMPERSONATION_LEVEL = 9
TOKEN_TYPE_IMPERSONATION = 2
TOKEN_IS_APP_CONTAINER = 29
TOKEN_CAPABILITIES = 30
TOKEN_APP_CONTAINER_SID = 31
TOKEN_IS_LESS_PRIVILEGED_APP_CONTAINER = 46
SECURITY_IMPERSONATION = 2

ALL_APPLICATION_PACKAGES_SID = "S-1-15-2-1"
SYSTEM_SID = "S-1-5-18"
LPAC_PROOF_CLASS_46 = "class_46"
LPAC_PROOF_ACCESS_CHECK = "access_check"

FILE_READ_DATA = 0x00000001
READ_CONTROL = 0x00020000
SYNCHRONIZE = 0x00100000
FILE_READ_EA = 0x0008
FILE_EXECUTE = 0x0020
FILE_READ_ATTRIBUTES = 0x0080
FILE_GENERIC_READ = (
    READ_CONTROL
    | FILE_READ_DATA
    | FILE_READ_ATTRIBUTES
    | FILE_READ_EA
    | SYNCHRONIZE
)
FILE_GENERIC_WRITE = 0x00120116
FILE_GENERIC_EXECUTE = 0x001200A0
FILE_ALL_ACCESS = 0x001F01FF

SE_DACL_PRESENT = 0x0004
SE_DACL_PROTECTED = 0x1000
ACL_SIZE_INFORMATION = 2
ACCESS_ALLOWED_ACE_TYPE = 0

STARTF_USESTDHANDLES = 0x00000100
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXACT_CREATION_FLAGS = (
    EXTENDED_STARTUPINFO_PRESENT
    | CREATE_SUSPENDED
    | CREATE_NO_WINDOW
    | CREATE_UNICODE_ENVIRONMENT
)

PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY = 0x0002000F
PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT = 1
APPCONTAINER_CREATION_ATTRIBUTES = (
    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
    PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY,
    PROC_THREAD_ATTRIBUTE_JOB_LIST,
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
)

HANDLE_FLAG_INHERIT = 0x00000001


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = (("Sid", PSID), ("Attributes", DWORD))


class _TOKEN_APPCONTAINER_INFORMATION(ctypes.Structure):
    _fields_ = (("TokenAppContainer", PSID),)


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = (
        ("AppContainerSid", PSID),
        ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
        ("CapabilityCount", DWORD),
        ("Reserved", DWORD),
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


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("AceCount", DWORD),
        ("AclBytesInUse", DWORD),
        ("AclBytesFree", DWORD),
    )


class _ACL(ctypes.Structure):
    _fields_ = (
        ("AclRevision", ctypes.c_ubyte),
        ("Sbz1", ctypes.c_ubyte),
        ("AclSize", WORD),
        ("AceCount", WORD),
        ("Sbz2", WORD),
    )


class _ACE_HEADER(ctypes.Structure):
    _fields_ = (
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", WORD),
    )


class _LUID(ctypes.Structure):
    _fields_ = (("LowPart", DWORD), ("HighPart", wintypes.LONG))


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = (("Luid", _LUID), ("Attributes", DWORD))


class _PRIVILEGE_SET_ONE(ctypes.Structure):
    _fields_ = (
        ("PrivilegeCount", DWORD),
        ("Control", DWORD),
        ("Privilege", _LUID_AND_ATTRIBUTES * 1),
    )


class _GENERIC_MAPPING(ctypes.Structure):
    _fields_ = (
        ("GenericRead", DWORD),
        ("GenericWrite", DWORD),
        ("GenericExecute", DWORD),
        ("GenericAll", DWORD),
    )


class _SECURITY_DESCRIPTOR(ctypes.Structure):
    _fields_ = (
        ("Revision", ctypes.c_ubyte),
        ("Sbz1", ctypes.c_ubyte),
        ("Control", WORD),
        ("Owner", PSID),
        ("Group", PSID),
        ("Sacl", LPVOID),
        ("Dacl", LPVOID),
    )


@dataclass(frozen=True)
class TokenIdentityProof:
    appcontainer: bool
    appcontainer_sid: str
    capability_count: int


@dataclass(frozen=True)
class LpacRouteProof:
    route: str
    token: TokenIdentityProof


@dataclass(frozen=True)
class CreationAttributeProof:
    attribute_ids: tuple[int, ...]
    app_policy_dword: int
    job_handle: int
    inherited_handles: tuple[int, int, int]
    creation_flags: int
    launch_basis_digest: str


@dataclass(frozen=True)
class AccessCheckSemanticProof:
    normal_aap_allowed: bool
    lpac_aap_allowed: bool
    lpac_package_allowed: bool
    desired_access: int
    descriptor_owner_sid: str
    descriptor_group_sid: str
    descriptor_ace_count: int


@dataclass(frozen=True)
class _AppContainerGrantDescriptorState:
    revision: int
    control: int
    owner_sid: str
    owner_defaulted: bool
    group_sid: str
    group_defaulted: bool
    dacl_present: bool
    dacl_defaulted: bool
    dacl_matches: bool
    acl_revision: int
    acl_size: int
    acl_ace_count: int
    ace_count: int
    acl_bytes_in_use: int
    acl_bytes_free: int
    ace_types: tuple[int, ...]
    ace_flags: tuple[int, ...]
    ace_sizes: tuple[int, ...]
    ace_sid_lengths: tuple[int, ...]
    ace_masks: tuple[int, ...]
    trustees: tuple[str, ...]


@dataclass(frozen=True)
class _AccessCheckResult:
    privilege_length: int
    privilege_count: int
    privilege_control: int
    privilege_luid_low: int
    privilege_luid_high: int
    privilege_attributes: int
    granted_access: int
    access_status: int


class _Apis:
    def __init__(self) -> None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            _fail("sandbox_unavailable")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        k = self.kernel32
        a = self.advapi32
        self._prototype(k.CloseHandle, [HANDLE], wintypes.BOOL)
        self._prototype(k.LocalFree, [LPVOID], LPVOID)
        self._prototype(k.GetCurrentProcess, [], HANDLE)
        self._prototype(k.GetHandleInformation, [HANDLE, ctypes.POINTER(DWORD)], wintypes.BOOL)
        self._prototype(k.TerminateJobObject, [HANDLE, DWORD], wintypes.BOOL)
        self._prototype(k.InitializeProcThreadAttributeList, [LPVOID, DWORD, DWORD, ctypes.POINTER(SIZE_T)], wintypes.BOOL)
        self._prototype(k.UpdateProcThreadAttribute, [LPVOID, DWORD, SIZE_T, LPVOID, SIZE_T, LPVOID, ctypes.POINTER(SIZE_T)], wintypes.BOOL)
        self._prototype(k.DeleteProcThreadAttributeList, [LPVOID], None)
        self._prototype(k.CreateProcessW, [wintypes.LPCWSTR, wintypes.LPWSTR, LPVOID, LPVOID, wintypes.BOOL, DWORD, LPVOID, wintypes.LPCWSTR, ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)], wintypes.BOOL)
        self._prototype(a.OpenProcessToken, [HANDLE, DWORD, ctypes.POINTER(HANDLE)], wintypes.BOOL)
        self._prototype(a.GetTokenInformation, [HANDLE, ctypes.c_int, LPVOID, DWORD, ctypes.POINTER(DWORD)], wintypes.BOOL)
        self._prototype(a.DuplicateTokenEx, [HANDLE, DWORD, LPVOID, ctypes.c_int, ctypes.c_int, ctypes.POINTER(HANDLE)], wintypes.BOOL)
        self._prototype(a.ConvertStringSidToSidW, [wintypes.LPCWSTR, ctypes.POINTER(PSID)], wintypes.BOOL)
        self._prototype(a.ConvertSidToStringSidW, [PSID, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL)
        self._prototype(a.GetLengthSid, [PSID], DWORD)
        self._prototype(a.InitializeAcl, [LPVOID, DWORD, DWORD], wintypes.BOOL)
        self._prototype(a.AddAccessAllowedAceEx, [LPVOID, DWORD, DWORD, DWORD, PSID], wintypes.BOOL)
        self._prototype(a.InitializeSecurityDescriptor, [LPVOID, DWORD], wintypes.BOOL)
        self._prototype(a.SetSecurityDescriptorOwner, [LPVOID, PSID, wintypes.BOOL], wintypes.BOOL)
        self._prototype(a.SetSecurityDescriptorGroup, [LPVOID, PSID, wintypes.BOOL], wintypes.BOOL)
        self._prototype(a.SetSecurityDescriptorDacl, [LPVOID, wintypes.BOOL, LPVOID, wintypes.BOOL], wintypes.BOOL)
        self._prototype(a.SetSecurityDescriptorControl, [LPVOID, WORD, WORD], wintypes.BOOL)
        self._prototype(a.GetSecurityDescriptorControl, [LPVOID, ctypes.POINTER(WORD), ctypes.POINTER(DWORD)], wintypes.BOOL)
        self._prototype(a.GetSecurityDescriptorOwner, [LPVOID, ctypes.POINTER(PSID), ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL)
        self._prototype(a.GetSecurityDescriptorGroup, [LPVOID, ctypes.POINTER(PSID), ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL)
        self._prototype(a.GetSecurityDescriptorDacl, [LPVOID, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(LPVOID), ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL)
        self._prototype(a.GetAclInformation, [LPVOID, LPVOID, DWORD, ctypes.c_int], wintypes.BOOL)
        self._prototype(a.GetAce, [LPVOID, DWORD, ctypes.POINTER(LPVOID)], wintypes.BOOL)
        self._prototype(a.AccessCheck, [LPVOID, HANDLE, DWORD, ctypes.POINTER(_GENERIC_MAPPING), LPVOID, ctypes.POINTER(DWORD), ctypes.POINTER(DWORD), ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL)

    @staticmethod
    def _prototype(function: object, arguments: list[object], result: object) -> None:
        function.argtypes = arguments
        function.restype = result


_API: _Apis | None = None
_API_LOCK = threading.Lock()


def _apis() -> _Apis:
    global _API
    if _API is None:
        with _API_LOCK:
            if _API is None:
                _API = _Apis()
    return _API


def _set_last_error(value: int) -> None:
    setter = getattr(ctypes, "set_last_error", None)
    if setter is not None:
        setter(value)


def _get_last_error() -> int:
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


class OwnedHandle:
    __slots__ = ("_value",)

    def __init__(self, value: object) -> None:
        raw = int(value.value or 0) if isinstance(value, ctypes.c_void_p) else int(value or 0)
        if raw in {0, INVALID_HANDLE_VALUE}:
            _fail("sandbox_boundary_violation")
        self._value = raw

    @property
    def value(self) -> int:
        if self._value == 0:
            _fail("sandbox_boundary_violation")
        return self._value

    @property
    def closed(self) -> bool:
        return self._value == 0

    def close(self) -> None:
        raw, self._value = self._value, 0
        if raw and not _apis().kernel32.CloseHandle(HANDLE(raw)):
            _fail("sandbox_cleanup_failed")


@dataclass(frozen=True)
class SuspendedLaunchSpec:
    application: str
    command_line: str
    environment_block: str
    cwd: str
    appcontainer_sid: str

    def __post_init__(self) -> None:
        if (
            type(self.application) is not str
            or not self.application
            or "\0" in self.application
            or type(self.command_line) is not str
            or not self.command_line
            or "\0" in self.command_line
            or len(self.command_line.encode("utf-16-le")) // 2 > 24_576
            or type(self.environment_block) is not str
            or not self.environment_block.endswith("\0\0")
            or "\0\0" in self.environment_block[:-2]
            or type(self.cwd) is not str
            or not self.cwd
            or "\0" in self.cwd
            or type(self.appcontainer_sid) is not str
            or not self.appcontainer_sid.startswith("S-1-15-2-")
            or self.appcontainer_sid == ALL_APPLICATION_PACKAGES_SID
        ):
            _fail("sandbox_setup_failed")


@dataclass(frozen=True)
class SuspendedLaunchResources:
    job_handle: OwnedHandle
    stdin_handle: OwnedHandle
    stdout_handle: OwnedHandle
    stderr_handle: OwnedHandle

    def __post_init__(self) -> None:
        values: list[int] = []
        for handle in (
            self.job_handle,
            self.stdin_handle,
            self.stdout_handle,
            self.stderr_handle,
        ):
            if not isinstance(handle, OwnedHandle) or handle.closed:
                _fail("sandbox_setup_failed")
            values.append(handle.value)
        if len(set(values)) != len(values):
            _fail("sandbox_setup_failed")

    @property
    def inherited_handles(self) -> tuple[OwnedHandle, OwnedHandle, OwnedHandle]:
        return self.stdin_handle, self.stdout_handle, self.stderr_handle

    def close_inherited_handles(self) -> None:
        failed = False
        for handle in self.inherited_handles:
            if not handle.closed:
                try:
                    handle.close()
                except LpacProofError:
                    failed = True
        if failed:
            _fail("sandbox_cleanup_failed")


class _SuspendedProcess:
    __slots__ = (
        "process",
        "thread",
        "primary_token",
        "process_id",
        "creation",
    )

    def __init__(
        self,
        process: OwnedHandle,
        thread: OwnedHandle,
        primary_token: OwnedHandle,
        process_id: int,
        creation: CreationAttributeProof,
    ) -> None:
        self.process = process
        self.thread = thread
        self.primary_token = primary_token
        self.process_id = process_id
        self.creation = creation

    @property
    def closed(self) -> bool:
        return all(
            handle.closed for handle in (self.primary_token, self.thread, self.process)
        )

    def close(self) -> None:
        failed = False
        for handle in (self.primary_token, self.thread, self.process):
            if not handle.closed:
                try:
                    handle.close()
                except LpacProofError:
                    failed = True
        if failed:
            _fail("sandbox_cleanup_failed")


class SuspendedLpacChild(_SuspendedProcess):
    """Suspended LPAC child; this inactive seam exposes no resume operation."""


class SuspendedNormalControl(_SuspendedProcess):
    """Suspended normal-AppContainer control that can never be resumed."""


def _local_free(pointer: object) -> None:
    if pointer:
        _apis().kernel32.LocalFree(pointer)


def _sid_to_string(sid: PSID) -> str:
    text = wintypes.LPWSTR()
    if not sid or not _apis().advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        _fail("sandbox_boundary_violation")
    try:
        value = text.value
        if not value or not value.startswith("S-1-"):
            _fail("sandbox_boundary_violation")
        return value
    finally:
        _local_free(text)


class _OwnedLocalSid:
    __slots__ = ("pointer",)

    def __init__(self, sid_string: str) -> None:
        self.pointer = PSID()
        if (
            type(sid_string) is not str
            or not sid_string.startswith("S-1-")
            or not _apis().advapi32.ConvertStringSidToSidW(
                sid_string, ctypes.byref(self.pointer)
            )
        ):
            _fail("sandbox_boundary_violation")

    def close(self) -> None:
        pointer, self.pointer = self.pointer, PSID()
        if pointer:
            _local_free(pointer)

    def __enter__(self) -> "_OwnedLocalSid":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _token_buffer(token: OwnedHandle, information_class: int) -> ctypes.Array[ctypes.c_char]:
    if not isinstance(token, OwnedHandle) or token.closed:
        _fail("sandbox_boundary_violation")
    needed = DWORD()
    _set_last_error(0)
    first = _apis().advapi32.GetTokenInformation(
        HANDLE(token.value), information_class, None, 0, ctypes.byref(needed)
    )
    if (
        first
        or _get_last_error() != ERROR_INSUFFICIENT_BUFFER
        or needed.value <= 0
        or needed.value > 1_048_576
    ):
        _fail("sandbox_boundary_violation")
    allocated = int(needed.value)
    buffer = ctypes.create_string_buffer(allocated)
    returned = DWORD()
    if not _apis().advapi32.GetTokenInformation(
        HANDLE(token.value),
        information_class,
        buffer,
        allocated,
        ctypes.byref(returned),
    ) or returned.value != allocated:
        _fail("sandbox_boundary_violation")
    return buffer


def _query_exact_token_dword(
    token: OwnedHandle, information_class: int, expected: int
) -> None:
    if information_class not in {TOKEN_TYPE, TOKEN_IMPERSONATION_LEVEL} or expected != 2:
        _fail("sandbox_boundary_violation")
    value = DWORD()
    returned = DWORD()
    _set_last_error(0)
    if not _apis().advapi32.GetTokenInformation(
        HANDLE(token.value),
        information_class,
        ctypes.byref(value),
        ctypes.sizeof(value),
        ctypes.byref(returned),
    ):
        _fail("sandbox_boundary_violation")
    if returned.value != ctypes.sizeof(value) or value.value != expected:
        _fail("sandbox_boundary_violation")


def _query_lpac_class46_dword(token: OwnedHandle) -> str:
    """Use one fixed DWORD query; only exact Win32 error 87 selects fallback."""

    if not isinstance(token, OwnedHandle) or token.closed:
        _fail("sandbox_boundary_violation")
    value = DWORD()
    returned = DWORD()
    _set_last_error(0)
    succeeded = _apis().advapi32.GetTokenInformation(
        HANDLE(token.value),
        TOKEN_IS_LESS_PRIVILEGED_APP_CONTAINER,
        ctypes.byref(value),
        ctypes.sizeof(value),
        ctypes.byref(returned),
    )
    error = _get_last_error()
    if succeeded:
        if returned.value != ctypes.sizeof(value) or value.value != 1:
            _fail("sandbox_boundary_violation")
        return LPAC_PROOF_CLASS_46
    if error == ERROR_INVALID_PARAMETER:
        return LPAC_PROOF_ACCESS_CHECK
    _fail("sandbox_boundary_violation")


def _prove_token_identity(
    token: OwnedHandle, expected_appcontainer_sid: str
) -> TokenIdentityProof:
    if (
        type(expected_appcontainer_sid) is not str
        or not expected_appcontainer_sid.startswith("S-1-15-2-")
        or expected_appcontainer_sid == ALL_APPLICATION_PACKAGES_SID
    ):
        _fail("sandbox_boundary_violation")
    appcontainer = _token_buffer(token, TOKEN_IS_APP_CONTAINER)
    if (
        len(appcontainer) != ctypes.sizeof(DWORD)
        or DWORD.from_buffer(appcontainer).value != 1
    ):
        _fail("sandbox_boundary_violation")
    sid_buffer = _token_buffer(token, TOKEN_APP_CONTAINER_SID)
    sid_pointer = _TOKEN_APPCONTAINER_INFORMATION.from_buffer(
        sid_buffer
    ).TokenAppContainer
    actual_sid = _sid_to_string(sid_pointer) if sid_pointer else ""
    capabilities = _token_buffer(token, TOKEN_CAPABILITIES)
    capability_count = int(DWORD.from_buffer(capabilities).value)
    if actual_sid != expected_appcontainer_sid or capability_count != 0:
        _fail("sandbox_boundary_violation")
    return TokenIdentityProof(True, actual_sid, 0)


def prove_lpac_route(
    primary_token: OwnedHandle, expected_appcontainer_sid: str
) -> LpacRouteProof:
    identity = _prove_token_identity(primary_token, expected_appcontainer_sid)
    return LpacRouteProof(_query_lpac_class46_dword(primary_token), identity)


def _current_user_sid() -> str:
    raw = HANDLE()
    if not _apis().advapi32.OpenProcessToken(
        _apis().kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(raw)
    ):
        _fail("sandbox_unavailable")
    token = OwnedHandle(raw)
    try:
        buffer = _token_buffer(token, TOKEN_USER)
        sid = PSID.from_buffer(buffer).value
        return _sid_to_string(PSID(sid))
    finally:
        token.close()


def duplicate_impersonation_token(primary_token: OwnedHandle) -> OwnedHandle:
    if not isinstance(primary_token, OwnedHandle) or primary_token.closed:
        _fail("sandbox_boundary_violation")
    raw = HANDLE()
    if not _apis().advapi32.DuplicateTokenEx(
        HANDLE(primary_token.value),
        TOKEN_QUERY | TOKEN_IMPERSONATE,
        None,
        SECURITY_IMPERSONATION,
        TOKEN_TYPE_IMPERSONATION,
        ctypes.byref(raw),
    ):
        _fail("sandbox_boundary_violation")
    duplicate = OwnedHandle(raw)
    try:
        _query_exact_token_dword(duplicate, TOKEN_TYPE, TOKEN_TYPE_IMPERSONATION)
        _query_exact_token_dword(
            duplicate, TOKEN_IMPERSONATION_LEVEL, SECURITY_IMPERSONATION
        )
        return duplicate
    except BaseException:
        duplicate.close()
        raise


class _AppContainerGrantSecurityDescriptor:
    """Protected SYSTEM-owned/grouped DACL with exactly two bit-1 ACEs."""

    __slots__ = ("pointer", "_descriptor", "_acl", "_sids")

    def __init__(self, user_sid: str, application_sid: str) -> None:
        self.pointer = LPVOID()
        self._sids: list[_OwnedLocalSid] = []
        try:
            owner = _OwnedLocalSid(SYSTEM_SID)
            user = _OwnedLocalSid(user_sid)
            application = _OwnedLocalSid(application_sid)
            self._sids.extend((owner, user, application))
            acl_size = ctypes.sizeof(_ACL) + sum(
                8 + int(_apis().advapi32.GetLengthSid(item.pointer))
                for item in (user, application)
            )
            self._acl = ctypes.create_string_buffer(acl_size)
            dacl = ctypes.cast(self._acl, LPVOID)
            self._descriptor = ctypes.create_string_buffer(
                ctypes.sizeof(_SECURITY_DESCRIPTOR)
            )
            self.pointer = ctypes.cast(self._descriptor, LPVOID)
            if not _apis().advapi32.InitializeAcl(dacl, acl_size, 2):
                _fail("sandbox_boundary_violation")
            for sid in (user, application):
                if not _apis().advapi32.AddAccessAllowedAceEx(
                    dacl, 2, 0, FILE_READ_DATA, sid.pointer
                ):
                    _fail("sandbox_boundary_violation")
            if (
                not _apis().advapi32.InitializeSecurityDescriptor(self.pointer, 1)
                or not _apis().advapi32.SetSecurityDescriptorOwner(
                    self.pointer, owner.pointer, False
                )
                or not _apis().advapi32.SetSecurityDescriptorGroup(
                    self.pointer, owner.pointer, False
                )
                or not _apis().advapi32.SetSecurityDescriptorDacl(
                    self.pointer, True, dacl, False
                )
                or not _apis().advapi32.SetSecurityDescriptorControl(
                    self.pointer, SE_DACL_PROTECTED, SE_DACL_PROTECTED
                )
            ):
                _fail("sandbox_boundary_violation")
            _validate_appcontainer_grant_descriptor(
                _read_appcontainer_grant_descriptor(self.pointer, dacl),
                user_sid,
                application_sid,
            )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.pointer = LPVOID()
        for sid in reversed(getattr(self, "_sids", ())):
            sid.close()
        self._sids = []

    def __enter__(self) -> "_AppContainerGrantSecurityDescriptor":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _read_appcontainer_grant_descriptor(
    descriptor: LPVOID, expected_dacl: LPVOID
) -> _AppContainerGrantDescriptorState:
    control = WORD()
    revision = DWORD()
    owner = PSID()
    owner_defaulted = wintypes.BOOL()
    group = PSID()
    group_defaulted = wintypes.BOOL()
    dacl_present = wintypes.BOOL()
    dacl = LPVOID()
    dacl_defaulted = wintypes.BOOL()
    a = _apis().advapi32
    if (
        not descriptor
        or not expected_dacl
        or not a.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        )
        or not a.GetSecurityDescriptorOwner(
            descriptor, ctypes.byref(owner), ctypes.byref(owner_defaulted)
        )
        or not owner
        or not a.GetSecurityDescriptorGroup(
            descriptor, ctypes.byref(group), ctypes.byref(group_defaulted)
        )
        or not group
        or not a.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        )
        or not dacl
    ):
        _fail("sandbox_boundary_violation")
    info = _ACL_SIZE_INFORMATION()
    if not a.GetAclInformation(
        dacl, ctypes.byref(info), ctypes.sizeof(info), ACL_SIZE_INFORMATION
    ):
        _fail("sandbox_boundary_violation")
    acl = _ACL.from_address(dacl.value)
    ace_types: list[int] = []
    ace_flags: list[int] = []
    ace_sizes: list[int] = []
    ace_sid_lengths: list[int] = []
    ace_masks: list[int] = []
    trustees: list[str] = []
    if info.AceCount != 2 or acl.AceCount != 2:
        _fail("sandbox_boundary_violation")
    for index in range(2):
        raw_ace = LPVOID()
        if not a.GetAce(dacl, index, ctypes.byref(raw_ace)) or not raw_ace:
            _fail("sandbox_boundary_violation")
        header = _ACE_HEADER.from_address(raw_ace.value)
        sid = PSID(raw_ace.value + 8)
        sid_length = int(a.GetLengthSid(sid))
        if sid_length <= 0:
            _fail("sandbox_boundary_violation")
        ace_types.append(int(header.AceType))
        ace_flags.append(int(header.AceFlags))
        ace_sizes.append(int(header.AceSize))
        ace_sid_lengths.append(sid_length)
        ace_masks.append(int(DWORD.from_address(raw_ace.value + 4).value))
        trustees.append(_sid_to_string(sid))
    return _AppContainerGrantDescriptorState(
        int(revision.value),
        int(control.value),
        _sid_to_string(owner),
        bool(owner_defaulted.value),
        _sid_to_string(group),
        bool(group_defaulted.value),
        bool(dacl_present.value),
        bool(dacl_defaulted.value),
        dacl.value == expected_dacl.value,
        int(acl.AclRevision),
        int(acl.AclSize),
        int(acl.AceCount),
        int(info.AceCount),
        int(info.AclBytesInUse),
        int(info.AclBytesFree),
        tuple(ace_types),
        tuple(ace_flags),
        tuple(ace_sizes),
        tuple(ace_sid_lengths),
        tuple(ace_masks),
        tuple(trustees),
    )


def _validate_appcontainer_grant_descriptor(
    state: _AppContainerGrantDescriptorState,
    user_sid: str,
    application_sid: str,
) -> None:
    if not isinstance(state, _AppContainerGrantDescriptorState):
        _fail("sandbox_boundary_violation")
    expected_sizes = tuple(8 + length for length in state.ace_sid_lengths)
    expected_acl_size = ctypes.sizeof(_ACL) + sum(state.ace_sizes)
    if (
        state.revision != 1
        or state.control != SE_DACL_PRESENT | SE_DACL_PROTECTED
        or state.owner_sid != SYSTEM_SID
        or state.owner_defaulted is not False
        or state.group_sid != SYSTEM_SID
        or state.group_defaulted is not False
        or state.dacl_present is not True
        or state.dacl_defaulted is not False
        or state.dacl_matches is not True
        or state.acl_revision != 2
        or state.acl_size != expected_acl_size
        or state.acl_ace_count != 2
        or state.ace_count != 2
        or state.acl_bytes_in_use != expected_acl_size
        or state.acl_bytes_free != 0
        or state.ace_types != (ACCESS_ALLOWED_ACE_TYPE, ACCESS_ALLOWED_ACE_TYPE)
        or state.ace_flags != (0, 0)
        or state.ace_sizes != expected_sizes
        or len(state.ace_sid_lengths) != 2
        or any(length <= 0 for length in state.ace_sid_lengths)
        or state.ace_masks != (FILE_READ_DATA, FILE_READ_DATA)
        or state.trustees != (user_sid, application_sid)
    ):
        _fail("sandbox_boundary_violation")


def _validate_access_check_result(
    result: _AccessCheckResult, *, desired: int
) -> bool:
    if (
        not isinstance(result, _AccessCheckResult)
        or type(desired) is not int
        or not 0 < desired <= 0xFFFFFFFF
        or result.privilege_length != ctypes.sizeof(_PRIVILEGE_SET_ONE)
        or result.privilege_count != 0
        or result.privilege_control != 0
        or result.privilege_luid_low != 0
        or result.privilege_luid_high != 0
        or result.privilege_attributes != 0
        or result.access_status not in {0, 1}
        or (result.access_status == 1 and result.granted_access != desired)
        or (result.access_status == 0 and result.granted_access != 0)
    ):
        _fail("sandbox_boundary_violation")
    return result.access_status == 1


def _access_allowed(
    descriptor: LPVOID,
    token: OwnedHandle,
    desired: int,
    mapping: _GENERIC_MAPPING,
) -> bool:
    if (
        not descriptor
        or not isinstance(token, OwnedHandle)
        or token.closed
        or desired != FILE_READ_DATA
        or not isinstance(mapping, _GENERIC_MAPPING)
    ):
        _fail("sandbox_boundary_violation")
    _query_exact_token_dword(token, TOKEN_TYPE, TOKEN_TYPE_IMPERSONATION)
    _query_exact_token_dword(
        token, TOKEN_IMPERSONATION_LEVEL, SECURITY_IMPERSONATION
    )
    mapping_before = tuple(
        int(getattr(mapping, field))
        for field in ("GenericRead", "GenericWrite", "GenericExecute", "GenericAll")
    )
    if mapping_before != (
        FILE_GENERIC_READ,
        FILE_GENERIC_WRITE,
        FILE_GENERIC_EXECUTE,
        FILE_ALL_ACCESS,
    ):
        _fail("sandbox_boundary_violation")
    privilege_set = _PRIVILEGE_SET_ONE()
    privilege_length = DWORD(ctypes.sizeof(privilege_set))
    granted = DWORD()
    allowed = wintypes.BOOL()
    if not _apis().advapi32.AccessCheck(
        descriptor,
        HANDLE(token.value),
        desired,
        ctypes.byref(mapping),
        ctypes.byref(privilege_set),
        ctypes.byref(privilege_length),
        ctypes.byref(granted),
        ctypes.byref(allowed),
    ):
        _fail("sandbox_boundary_violation")
    mapping_after = tuple(
        int(getattr(mapping, field))
        for field in ("GenericRead", "GenericWrite", "GenericExecute", "GenericAll")
    )
    if mapping_after != mapping_before:
        _fail("sandbox_boundary_violation")
    return _validate_access_check_result(
        _AccessCheckResult(
            int(privilege_length.value),
            int(privilege_set.PrivilegeCount),
            int(privilege_set.Control),
            int(privilege_set.Privilege[0].Luid.LowPart),
            int(privilege_set.Privilege[0].Luid.HighPart),
            int(privilege_set.Privilege[0].Attributes),
            int(granted.value),
            int(allowed.value),
        ),
        desired=desired,
    )


def _appcontainer_grant_allowed(token: OwnedHandle, application_sid: str) -> bool:
    if (
        type(application_sid) is not str
        or not application_sid.startswith("S-1-15-2-")
    ):
        _fail("sandbox_boundary_violation")
    mapping = _GENERIC_MAPPING(
        FILE_GENERIC_READ,
        FILE_GENERIC_WRITE,
        FILE_GENERIC_EXECUTE,
        FILE_ALL_ACCESS,
    )
    with _AppContainerGrantSecurityDescriptor(
        _current_user_sid(), application_sid
    ) as descriptor:
        return _access_allowed(descriptor.pointer, token, FILE_READ_DATA, mapping)


def _close_duplicate(token: OwnedHandle | None) -> None:
    if isinstance(token, OwnedHandle) and not token.closed:
        token.close()


def prove_access_check_semantics(
    normal_control: SuspendedNormalControl,
    lpac_child: SuspendedLpacChild,
    expected_appcontainer_sid: str,
) -> AccessCheckSemanticProof:
    if (
        not isinstance(normal_control, SuspendedNormalControl)
        or normal_control.closed
        or not isinstance(lpac_child, SuspendedLpacChild)
        or lpac_child.closed
        or normal_control.creation.attribute_ids != APPCONTAINER_CREATION_ATTRIBUTES
        or lpac_child.creation.attribute_ids != APPCONTAINER_CREATION_ATTRIBUTES
        or normal_control.creation.app_policy_dword != 0
        or lpac_child.creation.app_policy_dword
        != PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT
        or normal_control.creation.creation_flags != EXACT_CREATION_FLAGS
        or lpac_child.creation.creation_flags != EXACT_CREATION_FLAGS
        or normal_control.creation.launch_basis_digest
        != lpac_child.creation.launch_basis_digest
        or normal_control.creation.job_handle == lpac_child.creation.job_handle
        or len(
            {
                normal_control.process.value,
                normal_control.thread.value,
                normal_control.primary_token.value,
                lpac_child.process.value,
                lpac_child.thread.value,
                lpac_child.primary_token.value,
            }
        )
        != 6
    ):
        _fail("sandbox_boundary_violation")
    _prove_token_identity(normal_control.primary_token, expected_appcontainer_sid)
    _prove_token_identity(lpac_child.primary_token, expected_appcontainer_sid)
    normal_duplicate: OwnedHandle | None = None
    lpac_duplicate: OwnedHandle | None = None
    try:
        normal_duplicate = duplicate_impersonation_token(
            normal_control.primary_token
        )
        lpac_duplicate = duplicate_impersonation_token(lpac_child.primary_token)
        normal_aap = _appcontainer_grant_allowed(
            normal_duplicate, ALL_APPLICATION_PACKAGES_SID
        )
        lpac_aap = _appcontainer_grant_allowed(
            lpac_duplicate, ALL_APPLICATION_PACKAGES_SID
        )
        lpac_package = _appcontainer_grant_allowed(
            lpac_duplicate, expected_appcontainer_sid
        )
        if normal_aap is not True or lpac_aap is not False or lpac_package is not True:
            _fail("sandbox_boundary_violation")
        return AccessCheckSemanticProof(
            True,
            False,
            True,
            FILE_READ_DATA,
            SYSTEM_SID,
            SYSTEM_SID,
            2,
        )
    finally:
        cleanup_failed = False
        for duplicate in (lpac_duplicate, normal_duplicate):
            try:
                _close_duplicate(duplicate)
            except LpacProofError:
                cleanup_failed = True
        if cleanup_failed:
            _fail("sandbox_cleanup_failed")


class _AttributeList:
    __slots__ = ("_buffer", "pointer", "_keepers", "attribute_ids")

    def __init__(self, count: int) -> None:
        if count != 4:
            _fail("sandbox_setup_failed")
        size = SIZE_T()
        _set_last_error(0)
        _apis().kernel32.InitializeProcThreadAttributeList(
            None, count, 0, ctypes.byref(size)
        )
        if (
            _get_last_error() != ERROR_INSUFFICIENT_BUFFER
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
        if attribute in self.attribute_ids or len(self.attribute_ids) >= 4:
            _fail("sandbox_setup_failed")
        self._keepers.append(value)
        if not _apis().kernel32.UpdateProcThreadAttribute(
            self.pointer,
            0,
            attribute,
            ctypes.cast(ctypes.byref(value), LPVOID),
            size,
            None,
            None,
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


def _launch_basis_digest(spec: SuspendedLaunchSpec) -> str:
    digest = hashlib.sha256(b"taskgov-m241a-lpac-launch-v1\0")
    for value in (
        spec.application,
        spec.command_line,
        spec.environment_block,
        spec.cwd,
        spec.appcontainer_sid,
        str(EXACT_CREATION_FLAGS),
    ):
        digest.update(value.encode("utf-8", "strict"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _creation_proof(
    spec: SuspendedLaunchSpec,
    resources: SuspendedLaunchResources,
    *,
    lpac: bool,
) -> CreationAttributeProof:
    return CreationAttributeProof(
        APPCONTAINER_CREATION_ATTRIBUTES,
        PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT if lpac else 0,
        resources.job_handle.value,
        tuple(handle.value for handle in resources.inherited_handles),
        EXACT_CREATION_FLAGS,
        _launch_basis_digest(spec),
    )


def _add_creation_attributes(
    attributes: _AttributeList,
    capabilities: _SECURITY_CAPABILITIES,
    policy: DWORD,
    job_handles: object,
    inherited_handles: object,
) -> None:
    attributes.add(
        PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
        capabilities,
        ctypes.sizeof(capabilities),
    )
    attributes.add(
        PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY,
        policy,
        ctypes.sizeof(policy),
    )
    attributes.add(
        PROC_THREAD_ATTRIBUTE_JOB_LIST,
        job_handles,
        ctypes.sizeof(job_handles),
    )
    attributes.add(
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
        inherited_handles,
        ctypes.sizeof(inherited_handles),
    )
    if tuple(attributes.attribute_ids) != APPCONTAINER_CREATION_ATTRIBUTES:
        _fail("sandbox_setup_failed")


def _prove_handle_inheritability(handle: OwnedHandle, expected: bool) -> None:
    flags = DWORD()
    if not _apis().kernel32.GetHandleInformation(
        HANDLE(handle.value), ctypes.byref(flags)
    ):
        _fail("sandbox_boundary_violation")
    if bool(flags.value & HANDLE_FLAG_INHERIT) is not expected:
        _fail("sandbox_boundary_violation")


def _open_child_primary_token(process: OwnedHandle) -> OwnedHandle:
    raw = HANDLE()
    if not _apis().advapi32.OpenProcessToken(
        HANDLE(process.value), TOKEN_QUERY | TOKEN_DUPLICATE, ctypes.byref(raw)
    ):
        _fail("sandbox_boundary_violation", after_create=True)
    return OwnedHandle(raw)


def _terminate_created_job(job: OwnedHandle) -> bool:
    try:
        return bool(
            _apis().kernel32.TerminateJobObject(HANDLE(job.value), 0xC000013A)
        )
    except (LpacProofError, OSError, ValueError):
        return False


def _create_suspended(
    spec: SuspendedLaunchSpec,
    resources: SuspendedLaunchResources,
    *,
    lpac: bool,
) -> SuspendedLpacChild | SuspendedNormalControl:
    if not isinstance(spec, SuspendedLaunchSpec) or not isinstance(
        resources, SuspendedLaunchResources
    ):
        _fail("sandbox_setup_failed")
    application = Path(spec.application)
    cwd = Path(spec.cwd)
    if not application.is_absolute() or not application.is_file():
        _fail("sandbox_setup_failed")
    if not cwd.is_absolute() or not cwd.is_dir():
        _fail("sandbox_setup_failed")
    _prove_handle_inheritability(resources.job_handle, False)
    for handle in resources.inherited_handles:
        _prove_handle_inheritability(handle, True)
    proof = _creation_proof(spec, resources, lpac=lpac)
    process_info = _PROCESS_INFORMATION()
    process: OwnedHandle | None = None
    thread: OwnedHandle | None = None
    primary_token: OwnedHandle | None = None
    created = False
    pending_error: BaseException | None = None
    try:
        with _OwnedLocalSid(spec.appcontainer_sid) as sid, _AttributeList(4) as attributes:
            capabilities = _SECURITY_CAPABILITIES(sid.pointer, None, 0, 0)
            policy = DWORD(proof.app_policy_dword)
            job_handles = (HANDLE * 1)(HANDLE(resources.job_handle.value))
            inherited = (HANDLE * 3)(
                *(HANDLE(handle.value) for handle in resources.inherited_handles)
            )
            _add_creation_attributes(
                attributes, capabilities, policy, job_handles, inherited
            )
            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = HANDLE(resources.stdin_handle.value)
            startup.StartupInfo.hStdOutput = HANDLE(resources.stdout_handle.value)
            startup.StartupInfo.hStdError = HANDLE(resources.stderr_handle.value)
            startup.lpAttributeList = attributes.pointer
            command_buffer = ctypes.create_unicode_buffer(spec.command_line)
            environment_buffer = ctypes.create_unicode_buffer(spec.environment_block)
            created = bool(
                _apis().kernel32.CreateProcessW(
                    spec.application,
                    command_buffer,
                    None,
                    None,
                    True,
                    EXACT_CREATION_FLAGS,
                    ctypes.cast(environment_buffer, LPVOID),
                    spec.cwd,
                    ctypes.byref(startup.StartupInfo),
                    ctypes.byref(process_info),
                )
            )
            if not created:
                _fail("process_create_failed")
            process = OwnedHandle(process_info.hProcess)
            thread = OwnedHandle(process_info.hThread)
            primary_token = _open_child_primary_token(process)
    except BaseException as error:
        pending_error = error
    try:
        resources.close_inherited_handles()
    except BaseException as error:
        pending_error = error
    if pending_error is not None:
        cleanup_failed = False
        if created and not _terminate_created_job(resources.job_handle):
            cleanup_failed = True
        for handle in (primary_token, thread, process):
            if isinstance(handle, OwnedHandle) and not handle.closed:
                try:
                    handle.close()
                except LpacProofError:
                    cleanup_failed = True
        if cleanup_failed:
            _fail("sandbox_cleanup_failed", after_create=created)
        if isinstance(pending_error, LpacProofError):
            raise pending_error
        _fail("sandbox_boundary_violation", after_create=created)
    if process is None or thread is None or primary_token is None:
        _fail("sandbox_boundary_violation", after_create=created)
    child_type = SuspendedLpacChild if lpac else SuspendedNormalControl
    return child_type(
        process,
        thread,
        primary_token,
        int(process_info.dwProcessId),
        proof,
    )


def create_suspended_lpac_child(
    spec: SuspendedLaunchSpec, resources: SuspendedLaunchResources
) -> SuspendedLpacChild:
    child = _create_suspended(spec, resources, lpac=True)
    if not isinstance(child, SuspendedLpacChild):
        _fail("sandbox_boundary_violation", after_create=True)
    return child


def create_suspended_normal_control(
    spec: SuspendedLaunchSpec, resources: SuspendedLaunchResources
) -> SuspendedNormalControl:
    control = _create_suspended(spec, resources, lpac=False)
    if not isinstance(control, SuspendedNormalControl):
        _fail("sandbox_boundary_violation", after_create=True)
    return control
