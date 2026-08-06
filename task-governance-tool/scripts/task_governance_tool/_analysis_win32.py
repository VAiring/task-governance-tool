"""Narrow Windows handle primitives for the TG-M23 analysis boundary.

This private module owns only typed ``ctypes`` calls and explicit handle
lifetimes.  It has no Task, SQLite, adapter, retry, report, or network logic.
Failures are deliberately sanitized; callers must fail closed rather than
falling back to path-based copy, replace, or process APIs.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from ctypes import wintypes


# File access/share/disposition/flag values used verbatim by the delegated
# process-safety contract.
FILE_READ_DATA = 0x0001
FILE_LIST_DIRECTORY = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_ADD_FILE = 0x0002
FILE_APPEND_DATA = 0x0004
FILE_ADD_SUBDIRECTORY = 0x0004
FILE_READ_EA = 0x0008
FILE_WRITE_EA = 0x0010
FILE_TRAVERSE = 0x0020
FILE_WRITE_ATTRIBUTES = 0x0100
FILE_READ_ATTRIBUTES = 0x0080
DELETE = 0x00010000
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
SYNCHRONIZE = 0x00100000

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
CREATE_NEW = 1
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_TEMPORARY = 0x00000100
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_DELETE_ON_CLOSE = 0x04000000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000

HANDLE_FLAG_INHERIT = 0x00000001
LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
LOCKFILE_EXCLUSIVE_LOCK = 0x00000002

TOKEN_QUERY = 0x0008
TOKEN_INFORMATION_CLASS_USER = 1
TOKEN_INFORMATION_CLASS_PRIVILEGES = 3

FILE_STANDARD_INFO_CLASS = 1
FILE_NAME_INFO_CLASS = 2
FILE_DISPOSITION_INFO_CLASS = 4
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
FILE_ID_INFO_CLASS = 18
FILE_ID_EXTD_DIRECTORY_INFO_CLASS = 19
FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS = 20

ERROR_NO_MORE_FILES = 18
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_INSUFFICIENT_BUFFER = 122

STATUS_SUCCESS = 0x00000000
STATUS_PENDING = 0x00000103
STATUS_ACCESS_DENIED = 0xC0000022
STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
STATUS_OBJECT_NAME_COLLISION = 0xC0000035
STATUS_OBJECT_PATH_NOT_FOUND = 0xC000003A
STATUS_DELETE_PENDING = 0xC0000056
NATIVE_FILE_RENAME_INFORMATION_CLASS = 10

OBJ_CASE_INSENSITIVE = 0x00000040
NATIVE_FILE_OPEN = 1
NATIVE_FILE_CREATE = 2
NATIVE_FILE_OPEN_IF = 3
FILE_DIRECTORY_FILE = 0x00000001
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_NON_DIRECTORY_FILE = 0x00000040
NATIVE_FILE_DELETE_ON_CLOSE = 0x00001000
NATIVE_FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
NATIVE_FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_OPENED = 1
FILE_CREATED = 2

OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
SE_FILE_OBJECT = 1
SE_OWNER_DEFAULTED = 0x0001
SE_DACL_PRESENT = 0x0004
SE_DACL_DEFAULTED = 0x0008
SE_DACL_AUTO_INHERIT_REQ = 0x0100
SE_DACL_AUTO_INHERITED = 0x0400
SE_DACL_PROTECTED = 0x1000
SE_SELF_RELATIVE = 0x8000
ACL_REVISION_INFORMATION_CLASS = 1
ACL_SIZE_INFORMATION_CLASS = 2
ACL_REVISION = 2
ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1
GENERIC_ACCESS_BITS = 0xF0000000

_OWNER_RIGHTS_SID = "S-1-3-4"
_RESTRICTED_CODE_SID = "S-1-5-12"
_OWNER_CONTROL_DENY = WRITE_DAC | WRITE_OWNER

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_IO_CHUNK_BYTES = 64 * 1024
_MAX_TOKEN_PRIVILEGES = 256
_MAX_HELD_NAME_BYTES = 65_536
_MAX_DIRECTORY_ENTRIES = 100_000

FILE_TYPE_DISK = 0x0001
SDDL_REVISION_1 = 1


@dataclass(frozen=True)
class FileOpenAlias:
    access: int
    share: int
    disposition: int
    flags: int


OP = FileOpenAlias(
    DELETE | FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
    FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    CREATE_NEW,
    FILE_ATTRIBUTE_TEMPORARY
    | FILE_FLAG_DELETE_ON_CLOSE
    | FILE_FLAG_OPEN_REPARSE_POINT,
)
OC = FileOpenAlias(
    FILE_WRITE_DATA | SYNCHRONIZE,
    FILE_SHARE_READ | FILE_SHARE_DELETE,
    OPEN_EXISTING,
    FILE_FLAG_OPEN_REPARSE_POINT,
)
SP = FileOpenAlias(
    DELETE | FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
    FILE_SHARE_READ | FILE_SHARE_DELETE,
    CREATE_NEW,
    FILE_ATTRIBUTE_TEMPORARY
    | FILE_FLAG_DELETE_ON_CLOSE
    | FILE_FLAG_OPEN_REPARSE_POINT,
)
SC = FileOpenAlias(
    FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    OPEN_EXISTING,
    FILE_FLAG_OPEN_REPARSE_POINT,
)
TH = FileOpenAlias(
    READ_CONTROL
    | DELETE
    | FILE_READ_DATA
    | FILE_WRITE_DATA
    | FILE_READ_ATTRIBUTES
    | SYNCHRONIZE,
    0,
    CREATE_NEW,
    FILE_FLAG_OPEN_REPARSE_POINT,
)
RH = FileOpenAlias(
    READ_CONTROL | DELETE | FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
    0,
    OPEN_EXISTING,
    FILE_FLAG_OPEN_REPARSE_POINT,
)
DP = FileOpenAlias(
    FILE_LIST_DIRECTORY
    | FILE_ADD_FILE
    | FILE_ADD_SUBDIRECTORY
    | FILE_READ_ATTRIBUTES
    | SYNCHRONIZE,
    FILE_SHARE_WRITE,
    OPEN_EXISTING,
    FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
)
S0 = FileOpenAlias(
    READ_CONTROL
    | FILE_LIST_DIRECTORY
    | FILE_ADD_FILE
    | FILE_ADD_SUBDIRECTORY
    | FILE_READ_ATTRIBUTES
    | SYNCHRONIZE,
    FILE_SHARE_WRITE,
    NATIVE_FILE_OPEN_IF,
    FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
)
LOCK_FILE = FileOpenAlias(
    GENERIC_READ | GENERIC_WRITE,
    FILE_SHARE_READ | FILE_SHARE_WRITE,
    OPEN_ALWAYS,
    FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
)
R0 = FileOpenAlias(
    READ_CONTROL
    | DELETE
    | FILE_LIST_DIRECTORY
    | FILE_ADD_FILE
    | FILE_ADD_SUBDIRECTORY
    | FILE_READ_ATTRIBUTES
    | SYNCHRONIZE,
    0,
    OPEN_EXISTING,
    FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
)
ROOT = R0


@dataclass
class Win32BoundaryError(RuntimeError):
    """One sanitized failure from the private Windows boundary."""

    code: str = "analysis_process_unsafe"
    message: str = "analysis process boundary could not be proved safely"

    def __str__(self) -> str:
        return self.message


class Win32QuarantineRequired(BaseException):
    """A failed cleanup that must retain ownership until controller fail-fast."""

    def __init__(
        self,
        phase: str,
        *,
        handle: OwnedHandle | None,
        lock: ByteLock | None = None,
        resources: tuple[object, ...] = (),
    ) -> None:
        super().__init__("analysis Win32 quarantine requires fail-fast termination")
        self.phase = phase
        self.handle = handle
        self.lock = lock
        self._resources = resources


def _failure(
    code: str = "analysis_process_unsafe",
    message: str = "analysis process boundary could not be proved safely",
) -> NoReturn:
    raise Win32BoundaryError(code, message)


def _validated_basename(basename: object) -> tuple[str, bytes]:
    if (
        type(basename) is not str
        or not basename
        or len(basename) > _MAX_HELD_NAME_BYTES
        or basename in {".", ".."}
        or "\0" in basename
        or "/" in basename
        or "\\" in basename
        or ":" in basename
        or basename.endswith((" ", "."))
    ):
        _failure("analysis_argument_invalid")
    try:
        encoded = basename.encode("utf-16-le", errors="strict")
    except UnicodeEncodeError:
        _failure("analysis_argument_invalid")
    if not encoded or len(encoded) > _MAX_HELD_NAME_BYTES:
        _failure("analysis_argument_invalid")
    return basename, encoded


class _FILE_ID_128(ctypes.Structure):
    _fields_ = (("Identifier", wintypes.BYTE * 16),)


class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = (
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", _FILE_ID_128),
    )


class _FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = (
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    )


class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = (
        ("FileAttributes", wintypes.DWORD),
        ("ReparseTag", wintypes.DWORD),
    )


class _FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = (("DeleteFile", ctypes.c_ubyte),)


class _FILE_NAME_INFO(ctypes.Structure):
    _fields_ = (
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    )


class _FILE_ID_EXTD_DIR_INFO(ctypes.Structure):
    _fields_ = (
        ("NextEntryOffset", wintypes.DWORD),
        ("FileIndex", wintypes.DWORD),
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("AllocationSize", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
        ("FileNameLength", wintypes.DWORD),
        ("EaSize", wintypes.DWORD),
        ("ReparsePointTag", wintypes.DWORD),
        ("FileId", _FILE_ID_128),
        ("FileName", wintypes.WCHAR * 1),
    )


class _FILE_RENAME_INFO(ctypes.Structure):
    # The first field is the modern Flags/legacy ReplaceIfExists union.  Zero
    # represents ReplaceIfExists=FALSE for the exact no-replace operation.
    _fields_ = (
        ("Flags", wintypes.DWORD),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    )


class _FILE_RENAME_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("ReplaceIfExists", ctypes.c_ubyte),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    )


class _OVERLAPPED(ctypes.Structure):
    _fields_ = (
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    )


class _LUID(ctypes.Structure):
    _fields_ = (
        ("LowPart", wintypes.DWORD),
        ("HighPart", wintypes.LONG),
    )


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = (
        ("Luid", _LUID),
        ("Attributes", wintypes.DWORD),
    )


class _TOKEN_PRIVILEGES_ONE(ctypes.Structure):
    _fields_ = (
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", _LUID_AND_ATTRIBUTES * 1),
    )


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = (
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    )


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    )


class _OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    )


class _IO_STATUS_UNION(ctypes.Union):
    _fields_ = (
        ("Status", wintypes.LONG),
        ("Pointer", ctypes.c_void_p),
    )


class _IO_STATUS_BLOCK(ctypes.Structure):
    _anonymous_ = ("Value",)
    _fields_ = (
        ("Value", _IO_STATUS_UNION),
        ("Information", ctypes.c_size_t),
    )


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = (
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    )


class _TOKEN_USER(ctypes.Structure):
    _fields_ = (("User", _SID_AND_ATTRIBUTES),)


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    )


class _ACL_REVISION_INFORMATION(ctypes.Structure):
    _fields_ = (("AclRevision", wintypes.DWORD),)


class _ACE_HEADER(ctypes.Structure):
    _fields_ = (
        ("AceType", wintypes.BYTE),
        ("AceFlags", wintypes.BYTE),
        ("AceSize", wintypes.WORD),
    )


class _ACCESS_ACE_PREFIX(ctypes.Structure):
    _fields_ = (
        ("Header", _ACE_HEADER),
        ("Mask", wintypes.DWORD),
    )


@dataclass(frozen=True)
class HandleIdentity:
    volume_serial_number: int
    file_id: bytes
    size: int
    is_directory: bool
    is_reparse: bool
    delete_pending: bool
    link_count: int

    def same_object(self, other: object) -> bool:
        return (
            isinstance(other, HandleIdentity)
            and self.volume_serial_number == other.volume_serial_number
            and self.file_id == other.file_id
            and self.is_directory == other.is_directory
        )


def _valid_private_leaf_link_state(identity: HandleIdentity) -> bool:
    return (
        not identity.is_directory
        and not identity.is_reparse
        and (
            (not identity.delete_pending and identity.link_count == 1)
            or (identity.delete_pending and identity.link_count == 0)
        )
    )


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    file_id: bytes
    size: int
    is_directory: bool
    is_reparse: bool


def _expected_abi_layout() -> dict[str, int]:
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    if pointer_size not in {4, 8}:
        _failure("analysis_abi_unsupported")
    return {
        "byte_size": 1,
        "wchar_size": 2,
        "dword_size": 4,
        "long_size": 4,
        "handle_size": pointer_size,
        "pointer_size": pointer_size,
        "overlapped_size": 32 if pointer_size == 8 else 20,
        "overlapped_event_offset": 24 if pointer_size == 8 else 16,
        "file_id_128_size": 16,
        "file_id_info_file_id_offset": 8,
        "file_id_info_size": 24,
        "file_standard_allocation_offset": 0,
        "file_standard_eof_offset": 8,
        "file_standard_links_offset": 16,
        "file_standard_delete_offset": 20,
        "file_standard_directory_offset": 21,
        "file_standard_size": 24,
        "file_attribute_tag_size": 8,
        "file_disposition_size": 1,
        "file_name_name_offset": 4,
        "file_id_extd_name_offset": 88,
        "luid_size": 8,
        "luid_attributes_offset": 8,
        "luid_attributes_size": 12,
        "token_privileges_entries_offset": 4,
        "file_rename_root_offset": 8 if pointer_size == 8 else 4,
        "file_rename_length_offset": 16 if pointer_size == 8 else 8,
        "file_rename_name_offset": 20 if pointer_size == 8 else 12,
        "file_rename_size": 24 if pointer_size == 8 else 16,
        "native_file_rename_root_offset": 8 if pointer_size == 8 else 4,
        "native_file_rename_length_offset": 16 if pointer_size == 8 else 8,
        "native_file_rename_name_offset": 20 if pointer_size == 8 else 12,
        "native_file_rename_size": 24 if pointer_size == 8 else 16,
        "unicode_string_buffer_offset": 8 if pointer_size == 8 else 4,
        "unicode_string_size": 16 if pointer_size == 8 else 8,
        "object_attributes_root_offset": 8 if pointer_size == 8 else 4,
        "object_attributes_name_offset": 16 if pointer_size == 8 else 8,
        "object_attributes_attributes_offset": 24 if pointer_size == 8 else 12,
        "object_attributes_security_offset": 32 if pointer_size == 8 else 16,
        "object_attributes_qos_offset": 40 if pointer_size == 8 else 20,
        "object_attributes_size": 48 if pointer_size == 8 else 24,
        "io_status_information_offset": pointer_size,
        "io_status_size": pointer_size * 2,
        "sid_attributes_attributes_offset": 8 if pointer_size == 8 else 4,
        "sid_attributes_size": 16 if pointer_size == 8 else 8,
        "token_user_size": 16 if pointer_size == 8 else 8,
        "acl_size_information_size": 12,
        "acl_revision_information_size": 4,
        "ace_header_size": 4,
        "access_ace_mask_offset": 4,
        "access_ace_prefix_size": 8,
    }


def abi_layout() -> dict[str, int]:
    """Return every layout value relied upon by the typed Win32 boundary."""

    return {
        "byte_size": ctypes.sizeof(wintypes.BYTE),
        "wchar_size": ctypes.sizeof(wintypes.WCHAR),
        "dword_size": ctypes.sizeof(wintypes.DWORD),
        "long_size": ctypes.sizeof(wintypes.LONG),
        "handle_size": ctypes.sizeof(wintypes.HANDLE),
        "pointer_size": ctypes.sizeof(ctypes.c_void_p),
        "overlapped_size": ctypes.sizeof(_OVERLAPPED),
        "overlapped_event_offset": _OVERLAPPED.hEvent.offset,
        "file_id_128_size": ctypes.sizeof(_FILE_ID_128),
        "file_id_info_file_id_offset": _FILE_ID_INFO.FileId.offset,
        "file_id_info_size": ctypes.sizeof(_FILE_ID_INFO),
        "file_standard_allocation_offset": _FILE_STANDARD_INFO.AllocationSize.offset,
        "file_standard_eof_offset": _FILE_STANDARD_INFO.EndOfFile.offset,
        "file_standard_links_offset": _FILE_STANDARD_INFO.NumberOfLinks.offset,
        "file_standard_delete_offset": _FILE_STANDARD_INFO.DeletePending.offset,
        "file_standard_directory_offset": _FILE_STANDARD_INFO.Directory.offset,
        "file_standard_size": ctypes.sizeof(_FILE_STANDARD_INFO),
        "file_attribute_tag_size": ctypes.sizeof(_FILE_ATTRIBUTE_TAG_INFO),
        "file_disposition_size": ctypes.sizeof(_FILE_DISPOSITION_INFO),
        "file_name_name_offset": _FILE_NAME_INFO.FileName.offset,
        "file_id_extd_name_offset": _FILE_ID_EXTD_DIR_INFO.FileName.offset,
        "luid_size": ctypes.sizeof(_LUID),
        "luid_attributes_offset": _LUID_AND_ATTRIBUTES.Attributes.offset,
        "luid_attributes_size": ctypes.sizeof(_LUID_AND_ATTRIBUTES),
        "token_privileges_entries_offset": _TOKEN_PRIVILEGES_ONE.Privileges.offset,
        "file_rename_root_offset": _FILE_RENAME_INFO.RootDirectory.offset,
        "file_rename_length_offset": _FILE_RENAME_INFO.FileNameLength.offset,
        "file_rename_name_offset": _FILE_RENAME_INFO.FileName.offset,
        "file_rename_size": ctypes.sizeof(_FILE_RENAME_INFO),
        "native_file_rename_root_offset": _FILE_RENAME_INFORMATION.RootDirectory.offset,
        "native_file_rename_length_offset": _FILE_RENAME_INFORMATION.FileNameLength.offset,
        "native_file_rename_name_offset": _FILE_RENAME_INFORMATION.FileName.offset,
        "native_file_rename_size": ctypes.sizeof(_FILE_RENAME_INFORMATION),
        "unicode_string_buffer_offset": _UNICODE_STRING.Buffer.offset,
        "unicode_string_size": ctypes.sizeof(_UNICODE_STRING),
        "object_attributes_root_offset": _OBJECT_ATTRIBUTES.RootDirectory.offset,
        "object_attributes_name_offset": _OBJECT_ATTRIBUTES.ObjectName.offset,
        "object_attributes_attributes_offset": _OBJECT_ATTRIBUTES.Attributes.offset,
        "object_attributes_security_offset": _OBJECT_ATTRIBUTES.SecurityDescriptor.offset,
        "object_attributes_qos_offset": _OBJECT_ATTRIBUTES.SecurityQualityOfService.offset,
        "object_attributes_size": ctypes.sizeof(_OBJECT_ATTRIBUTES),
        "io_status_information_offset": _IO_STATUS_BLOCK.Information.offset,
        "io_status_size": ctypes.sizeof(_IO_STATUS_BLOCK),
        "sid_attributes_attributes_offset": _SID_AND_ATTRIBUTES.Attributes.offset,
        "sid_attributes_size": ctypes.sizeof(_SID_AND_ATTRIBUTES),
        "token_user_size": ctypes.sizeof(_TOKEN_USER),
        "acl_size_information_size": ctypes.sizeof(_ACL_SIZE_INFORMATION),
        "acl_revision_information_size": ctypes.sizeof(_ACL_REVISION_INFORMATION),
        "ace_header_size": ctypes.sizeof(_ACE_HEADER),
        "access_ace_mask_offset": _ACCESS_ACE_PREFIX.Mask.offset,
        "access_ace_prefix_size": ctypes.sizeof(_ACCESS_ACE_PREFIX),
    }


def _assert_abi() -> None:
    if abi_layout() != _expected_abi_layout():
        _failure("analysis_abi_unsupported")


class _Kernel32:
    def __init__(self) -> None:
        if os.name != "nt":
            _failure("analysis_unsupported", "analysis is unsupported on this platform")
        _assert_abi()
        library = ctypes.WinDLL("kernel32", use_last_error=True)

        self.CreateFileW = library.CreateFileW
        self.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        self.CreateFileW.restype = wintypes.HANDLE

        self.CloseHandle = library.CloseHandle
        self.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.CloseHandle.restype = wintypes.BOOL

        self.GetHandleInformation = library.GetHandleInformation
        self.GetHandleInformation.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        self.GetHandleInformation.restype = wintypes.BOOL

        self.SetHandleInformation = library.SetHandleInformation
        self.SetHandleInformation.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self.SetHandleInformation.restype = wintypes.BOOL

        self.GetFileInformationByHandleEx = library.GetFileInformationByHandleEx
        self.GetFileInformationByHandleEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        self.GetFileInformationByHandleEx.restype = wintypes.BOOL

        self.GetFileType = library.GetFileType
        self.GetFileType.argtypes = (wintypes.HANDLE,)
        self.GetFileType.restype = wintypes.DWORD

        self.SetFileInformationByHandle = library.SetFileInformationByHandle
        self.SetFileInformationByHandle.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        self.SetFileInformationByHandle.restype = wintypes.BOOL

        self.SetFilePointerEx = library.SetFilePointerEx
        self.SetFilePointerEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        )
        self.SetFilePointerEx.restype = wintypes.BOOL

        self.SetEndOfFile = library.SetEndOfFile
        self.SetEndOfFile.argtypes = (wintypes.HANDLE,)
        self.SetEndOfFile.restype = wintypes.BOOL

        self.ReadFile = library.ReadFile
        self.ReadFile.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        self.ReadFile.restype = wintypes.BOOL

        self.WriteFile = library.WriteFile
        self.WriteFile.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        self.WriteFile.restype = wintypes.BOOL

        self.FlushFileBuffers = library.FlushFileBuffers
        self.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
        self.FlushFileBuffers.restype = wintypes.BOOL

        self.LockFileEx = library.LockFileEx
        self.LockFileEx.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_OVERLAPPED),
        )
        self.LockFileEx.restype = wintypes.BOOL

        self.UnlockFileEx = library.UnlockFileEx
        self.UnlockFileEx.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_OVERLAPPED),
        )
        self.UnlockFileEx.restype = wintypes.BOOL

        self.GetCurrentProcess = library.GetCurrentProcess
        self.GetCurrentProcess.argtypes = ()
        self.GetCurrentProcess.restype = wintypes.HANDLE

        self.LocalFree = library.LocalFree
        self.LocalFree.argtypes = (ctypes.c_void_p,)
        self.LocalFree.restype = ctypes.c_void_p


class _Advapi32:
    def __init__(self) -> None:
        if os.name != "nt":
            _failure("analysis_unsupported", "analysis is unsupported on this platform")
        _assert_abi()
        library = ctypes.WinDLL("advapi32", use_last_error=True)

        self.OpenProcessToken = library.OpenProcessToken
        self.OpenProcessToken.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        )
        self.OpenProcessToken.restype = wintypes.BOOL

        self.GetTokenInformation = library.GetTokenInformation
        self.GetTokenInformation.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        self.GetTokenInformation.restype = wintypes.BOOL

        self.LookupPrivilegeNameW = library.LookupPrivilegeNameW
        self.LookupPrivilegeNameW.argtypes = (
            wintypes.LPCWSTR,
            ctypes.POINTER(_LUID),
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        self.LookupPrivilegeNameW.restype = wintypes.BOOL

        self.ConvertStringSecurityDescriptorToSecurityDescriptorW = (
            library.ConvertStringSecurityDescriptorToSecurityDescriptorW
        )
        self.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        )
        self.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
            wintypes.BOOL
        )

        self.ConvertSidToStringSidW = library.ConvertSidToStringSidW
        self.ConvertSidToStringSidW.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        )
        self.ConvertSidToStringSidW.restype = wintypes.BOOL

        self.ConvertStringSidToSidW = library.ConvertStringSidToSidW
        self.ConvertStringSidToSidW.argtypes = (
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self.ConvertStringSidToSidW.restype = wintypes.BOOL

        self.IsValidSid = library.IsValidSid
        self.IsValidSid.argtypes = (ctypes.c_void_p,)
        self.IsValidSid.restype = wintypes.BOOL

        self.GetLengthSid = library.GetLengthSid
        self.GetLengthSid.argtypes = (ctypes.c_void_p,)
        self.GetLengthSid.restype = wintypes.DWORD

        self.EqualSid = library.EqualSid
        self.EqualSid.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        self.EqualSid.restype = wintypes.BOOL

        self.GetSecurityInfo = library.GetSecurityInfo
        self.GetSecurityInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        )
        self.GetSecurityInfo.restype = wintypes.DWORD

        self.GetSecurityDescriptorControl = library.GetSecurityDescriptorControl
        self.GetSecurityDescriptorControl.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.WORD),
            ctypes.POINTER(wintypes.DWORD),
        )
        self.GetSecurityDescriptorControl.restype = wintypes.BOOL

        self.IsValidSecurityDescriptor = library.IsValidSecurityDescriptor
        self.IsValidSecurityDescriptor.argtypes = (ctypes.c_void_p,)
        self.IsValidSecurityDescriptor.restype = wintypes.BOOL

        self.GetSecurityDescriptorLength = library.GetSecurityDescriptorLength
        self.GetSecurityDescriptorLength.argtypes = (ctypes.c_void_p,)
        self.GetSecurityDescriptorLength.restype = wintypes.DWORD

        self.GetSecurityDescriptorDacl = library.GetSecurityDescriptorDacl
        self.GetSecurityDescriptorDacl.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        )
        self.GetSecurityDescriptorDacl.restype = wintypes.BOOL

        self.GetSecurityDescriptorOwner = library.GetSecurityDescriptorOwner
        self.GetSecurityDescriptorOwner.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        )
        self.GetSecurityDescriptorOwner.restype = wintypes.BOOL

        self.GetAclInformation = library.GetAclInformation
        self.GetAclInformation.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_int,
        )
        self.GetAclInformation.restype = wintypes.BOOL

        self.IsValidAcl = library.IsValidAcl
        self.IsValidAcl.argtypes = (ctypes.c_void_p,)
        self.IsValidAcl.restype = wintypes.BOOL

        self.GetAce = library.GetAce
        self.GetAce.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self.GetAce.restype = wintypes.BOOL


class _Ntdll:
    def __init__(self) -> None:
        if os.name != "nt":
            _failure("analysis_unsupported", "analysis is unsupported on this platform")
        _assert_abi()
        library = ctypes.WinDLL("ntdll", use_last_error=True)
        self.NtCreateFile = library.NtCreateFile
        self.NtCreateFile.argtypes = (
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_OBJECT_ATTRIBUTES),
            ctypes.POINTER(_IO_STATUS_BLOCK),
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
        )
        self.NtCreateFile.restype = wintypes.LONG

        self.NtSetInformationFile = library.NtSetInformationFile
        self.NtSetInformationFile.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_IO_STATUS_BLOCK),
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.c_int,
        )
        self.NtSetInformationFile.restype = wintypes.LONG


_kernel32_instance: _Kernel32 | None = None
_advapi32_instance: _Advapi32 | None = None
_ntdll_instance: _Ntdll | None = None


def _kernel32() -> _Kernel32:
    global _kernel32_instance
    if _kernel32_instance is None:
        _kernel32_instance = _Kernel32()
    return _kernel32_instance


def _advapi32() -> _Advapi32:
    global _advapi32_instance
    if _advapi32_instance is None:
        _advapi32_instance = _Advapi32()
    return _advapi32_instance


def _ntdll() -> _Ntdll:
    global _ntdll_instance
    if _ntdll_instance is None:
        _ntdll_instance = _Ntdll()
    return _ntdll_instance


def _raw_handle(value: object) -> int:
    if isinstance(value, int):
        return value
    converted = ctypes.cast(value, ctypes.c_void_p).value
    if converted is None:
        return 0
    return int(converted)


class OwnedHandle:
    """One explicitly closed, non-copyable Windows HANDLE.

    No finalizer is provided: quarantine/fail-fast correctness must never rely
    on Python garbage collection closing a live proof handle.
    """

    __slots__ = ("_value", "_closed", "_kind")

    def __init__(self, value: int, *, kind: str) -> None:
        if type(value) is not int or value in {0, INVALID_HANDLE_VALUE}:
            _failure()
        self._value = value
        self._closed = False
        self._kind = kind

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def kind(self) -> str:
        return self._kind

    def borrow(self) -> wintypes.HANDLE:
        if self._closed:
            _failure("analysis_handle_closed")
        return wintypes.HANDLE(self._value)

    def close(self) -> None:
        if self._closed:
            _failure("analysis_handle_closed")
        if not _kernel32().CloseHandle(self.borrow()):
            _failure()
        self._closed = True

    def detach(self) -> int:
        """Transfer this handle value exactly once without closing it."""

        if self._closed:
            _failure("analysis_handle_closed")
        value = self._value
        self._value = 0
        self._closed = True
        return value

    def __copy__(self) -> NoReturn:
        _failure("analysis_handle_copy_forbidden")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_handle_copy_forbidden")


@dataclass(frozen=True)
class _ExpectedAce:
    ace_type: int
    mask: int
    trustee: str


@dataclass(frozen=True)
class ExactSecurityProof:
    """Sanitized result of an effective handle-security query."""

    policy: str
    protected: bool
    ace_kinds: tuple[str, ...]
    ace_masks: tuple[int, ...]
    trustees: tuple[str, ...]


def _current_token_user_sddl_sid() -> str:
    """Return the current primary TokenUser SID only to the SD builder."""

    kernel32 = _kernel32()
    advapi32 = _advapi32()
    raw_token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(raw_token)
    ):
        _failure()
    token = OwnedHandle(_raw_handle(raw_token), kind="token-user-query")
    converted = wintypes.LPWSTR()
    try:
        required = wintypes.DWORD()
        ctypes.set_last_error(0)
        first = advapi32.GetTokenInformation(
            token.borrow(),
            TOKEN_INFORMATION_CLASS_USER,
            None,
            0,
            ctypes.byref(required),
        )
        if (
            first
            or ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER
            or required.value < ctypes.sizeof(_TOKEN_USER)
        ):
            _failure()
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token.borrow(),
            TOKEN_INFORMATION_CLASS_USER,
            buffer,
            required.value,
            ctypes.byref(required),
        ):
            _failure()
        token_user = _TOKEN_USER.from_buffer(buffer)
        sid = int(token_user.User.Sid or 0)
        if sid == 0 or not advapi32.IsValidSid(ctypes.c_void_p(sid)):
            _failure()
        sid_length = int(advapi32.GetLengthSid(ctypes.c_void_p(sid)))
        start = ctypes.addressof(buffer)
        if sid_length < 8 or sid < start or sid + sid_length > start + len(buffer):
            _failure()
        if not advapi32.ConvertSidToStringSidW(
            ctypes.c_void_p(sid), ctypes.byref(converted)
        ):
            _failure()
        value = converted.value
        if (
            type(value) is not str
            or not value.startswith("S-1-")
            or len(value) > 184
            or not value.isascii()
        ):
            _failure()
        return value
    finally:
        sid_free_failed = bool(
            converted
            and kernel32.LocalFree(ctypes.cast(converted, ctypes.c_void_p))
        )
        if not token.closed:
            try:
                token.close()
            except Win32BoundaryError as close_failure:
                raise Win32QuarantineRequired(
                    "token_user_cleanup_unproved",
                    handle=token,
                    resources=(converted,) if sid_free_failed else (),
                ) from close_failure
        if sid_free_failed:
            raise Win32QuarantineRequired(
                "token_user_sid_cleanup_unproved",
                handle=None,
                resources=(converted,),
            )


class ExplicitSecurityDescriptor:
    """Explicit LocalAlloc-backed descriptor with no default-ACL fallback."""

    __slots__ = (
        "_pointer",
        "_attributes",
        "_closed",
        "_policy",
        "_current_user_sid",
        "_expected_aces",
    )

    def __init__(
        self,
        pointer: int,
        *,
        policy: str = "untrusted",
        current_user_sid: str | None = None,
        expected_aces: tuple[_ExpectedAce, ...] = (),
    ) -> None:
        if type(pointer) is not int or pointer == 0:
            _failure("analysis_security_descriptor_invalid")
        if (
            type(policy) is not str
            or not policy
            or (policy == "untrusted") != (current_user_sid is None)
            or (policy == "untrusted") != (not expected_aces)
        ):
            _failure("analysis_security_descriptor_invalid")
        self._pointer = pointer
        self._attributes = _SECURITY_ATTRIBUTES(
            ctypes.sizeof(_SECURITY_ATTRIBUTES),
            ctypes.c_void_p(pointer),
            wintypes.BOOL(0),
        )
        self._closed = False
        self._policy = policy
        self._current_user_sid = current_user_sid
        self._expected_aces = expected_aces

    @classmethod
    def from_sddl(cls, sddl: str) -> ExplicitSecurityDescriptor:
        if (
            type(sddl) is not str
            or not sddl
            or len(sddl) > 4096
            or "\0" in sddl
        ):
            _failure("analysis_security_descriptor_invalid")
        pointer = ctypes.c_void_p()
        if not _advapi32().ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            SDDL_REVISION_1,
            ctypes.byref(pointer),
            None,
        ):
            _failure("analysis_security_descriptor_invalid")
        value = int(pointer.value or 0)
        if value == 0:
            _failure("analysis_security_descriptor_invalid")
        return cls(value)

    @classmethod
    def _exact(
        cls,
        *,
        policy: str,
        expected_aces: tuple[_ExpectedAce, ...],
    ) -> ExplicitSecurityDescriptor:
        current_user_sid = _current_token_user_sddl_sid()
        trustees = {
            "current_user": current_user_sid,
            "owner_rights": _OWNER_RIGHTS_SID,
            "restricted_code": _RESTRICTED_CODE_SID,
        }
        ace_text = "".join(
            "({kind};;0x{mask:08x};;;{sid})".format(
                kind="D" if ace.ace_type == ACCESS_DENIED_ACE_TYPE else "A",
                mask=ace.mask,
                sid=trustees[ace.trustee],
            )
            for ace in expected_aces
        )
        descriptor = cls.from_sddl(f"O:{current_user_sid}D:P{ace_text}")
        descriptor._policy = policy
        descriptor._current_user_sid = current_user_sid
        descriptor._expected_aces = expected_aces
        return descriptor

    @classmethod
    def root(cls) -> ExplicitSecurityDescriptor:
        return cls._exact(
            policy="root",
            expected_aces=(
                _ExpectedAce(
                    ACCESS_DENIED_ACE_TYPE,
                    _OWNER_CONTROL_DENY,
                    "owner_rights",
                ),
                _ExpectedAce(ACCESS_ALLOWED_ACE_TYPE, R0.access, "current_user"),
                _ExpectedAce(
                    ACCESS_ALLOWED_ACE_TYPE,
                    FILE_TRAVERSE | FILE_READ_ATTRIBUTES,
                    "restricted_code",
                ),
            ),
        )

    @classmethod
    def report_temp(cls) -> ExplicitSecurityDescriptor:
        return cls._exact(
            policy="report-temp",
            expected_aces=(
                _ExpectedAce(
                    ACCESS_DENIED_ACE_TYPE,
                    _OWNER_CONTROL_DENY,
                    "owner_rights",
                ),
                _ExpectedAce(ACCESS_ALLOWED_ACE_TYPE, TH.access, "current_user"),
            ),
        )

    @classmethod
    def private_leaf(cls, alias: FileOpenAlias) -> ExplicitSecurityDescriptor:
        if alias is OP:
            # A restricted-token access check must grant OC through both the
            # ordinary current-user SID pass and the restricting-SID pass.
            # Keep the restricting ACE exact while giving CU only the union
            # of the two closed O aliases.
            policy = "owner-output"
            current_user_access = OP.access | OC.access
            restricted_access = OC.access
        elif alias is SP:
            policy = "owner-status"
            current_user_access = SP.access
            restricted_access = SC.access
        else:
            _failure("analysis_argument_invalid")
        return cls._exact(
            policy=policy,
            expected_aces=(
                _ExpectedAce(
                    ACCESS_DENIED_ACE_TYPE,
                    _OWNER_CONTROL_DENY,
                    "owner_rights",
                ),
                _ExpectedAce(
                    ACCESS_ALLOWED_ACE_TYPE,
                    current_user_access,
                    "current_user",
                ),
                _ExpectedAce(
                    ACCESS_ALLOWED_ACE_TYPE,
                    restricted_access,
                    "restricted_code",
                ),
            ),
        )

    @classmethod
    def lease(cls) -> ExplicitSecurityDescriptor:
        # GENERIC_READ|GENERIC_WRITE are expanded in an ACL so that no generic
        # access bit survives in the effective descriptor.
        expanded_access = (
            DELETE
            | FILE_READ_DATA
            | FILE_WRITE_DATA
            | FILE_APPEND_DATA
            | FILE_READ_EA
            | FILE_WRITE_EA
            | FILE_READ_ATTRIBUTES
            | FILE_WRITE_ATTRIBUTES
            | READ_CONTROL
            | SYNCHRONIZE
        )
        return cls._exact(
            policy="lease",
            expected_aces=(
                _ExpectedAce(
                    ACCESS_DENIED_ACE_TYPE,
                    _OWNER_CONTROL_DENY,
                    "owner_rights",
                ),
                _ExpectedAce(
                    ACCESS_ALLOWED_ACE_TYPE,
                    expanded_access,
                    "current_user",
                ),
            ),
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def borrow_attributes(self) -> ctypes.POINTER(_SECURITY_ATTRIBUTES):
        if self._closed:
            _failure("analysis_security_descriptor_closed")
        return ctypes.pointer(self._attributes)

    def borrow_descriptor(self) -> ctypes.c_void_p:
        if self._closed:
            _failure("analysis_security_descriptor_closed")
        return ctypes.c_void_p(self._pointer)

    def close(self) -> None:
        if self._closed:
            _failure("analysis_security_descriptor_closed")
        if _kernel32().LocalFree(ctypes.c_void_p(self._pointer)):
            _failure()
        self._pointer = 0
        self._closed = True

    def __copy__(self) -> NoReturn:
        _failure("analysis_security_descriptor_copy_forbidden")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_security_descriptor_copy_forbidden")


def prove_exact_handle_security(
    handle: OwnedHandle,
    security: ExplicitSecurityDescriptor,
) -> ExactSecurityProof:
    """Query and prove the effective owner/DACL on an already-held handle."""

    if (
        not isinstance(handle, OwnedHandle)
        or not isinstance(security, ExplicitSecurityDescriptor)
        or security.closed
        or security._policy == "untrusted"
        or security._current_user_sid is None
        or not security._expected_aces
    ):
        _failure("analysis_argument_invalid")
    advapi32 = _advapi32()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    returned_descriptor = ctypes.c_void_p()
    expected_sids: dict[str, ctypes.c_void_p] = {}
    try:
        result = advapi32.GetSecurityInfo(
            handle.borrow(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(returned_descriptor),
        )
        if result != 0 or not returned_descriptor.value:
            _failure("analysis_security_query_unavailable")

        descriptor_length = int(
            advapi32.GetSecurityDescriptorLength(returned_descriptor)
        )
        if (
            not advapi32.IsValidSecurityDescriptor(returned_descriptor)
            or descriptor_length < 20
            or descriptor_length > 65_536
        ):
            _failure("analysis_security_descriptor_mismatch")

        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            returned_descriptor,
            ctypes.byref(control),
            ctypes.byref(revision),
        ):
            _failure()
        if (
            revision.value != SDDL_REVISION_1
            or control.value
            & (SE_OWNER_DEFAULTED | SE_DACL_DEFAULTED)
            or not control.value & SE_DACL_PRESENT
            or not control.value & SE_DACL_PROTECTED
            or not control.value & SE_SELF_RELATIVE
            or control.value
            & (SE_DACL_AUTO_INHERIT_REQ | SE_DACL_AUTO_INHERITED)
        ):
            _failure("analysis_security_descriptor_mismatch")

        owner_from_descriptor = ctypes.c_void_p()
        owner_defaulted = wintypes.BOOL()
        if not advapi32.GetSecurityDescriptorOwner(
            returned_descriptor,
            ctypes.byref(owner_from_descriptor),
            ctypes.byref(owner_defaulted),
        ):
            _failure()
        dacl_present = wintypes.BOOL()
        dacl_from_descriptor = ctypes.c_void_p()
        dacl_defaulted = wintypes.BOOL()
        if not advapi32.GetSecurityDescriptorDacl(
            returned_descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl_from_descriptor),
            ctypes.byref(dacl_defaulted),
        ):
            _failure()
        if (
            not owner.value
            or owner.value != owner_from_descriptor.value
            or owner_defaulted.value
            or not dacl_present.value
            or not dacl.value
            or dacl.value != dacl_from_descriptor.value
            or dacl_defaulted.value
            or not advapi32.IsValidAcl(dacl)
        ):
            _failure("analysis_security_descriptor_mismatch")

        trustee_sids = {
            "current_user": security._current_user_sid,
            "owner_rights": _OWNER_RIGHTS_SID,
            "restricted_code": _RESTRICTED_CODE_SID,
        }
        for trustee, sid_text in trustee_sids.items():
            allocated = ctypes.c_void_p()
            if not advapi32.ConvertStringSidToSidW(
                sid_text, ctypes.byref(allocated)
            ) or not allocated.value:
                _failure()
            expected_sids[trustee] = allocated
        if not advapi32.EqualSid(owner, expected_sids["current_user"]):
            _failure("analysis_security_descriptor_mismatch")

        acl_information = _ACL_SIZE_INFORMATION()
        acl_revision = _ACL_REVISION_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(acl_revision),
            ctypes.sizeof(acl_revision),
            ACL_REVISION_INFORMATION_CLASS,
        ) or int(acl_revision.AclRevision) != ACL_REVISION:
            _failure("analysis_security_descriptor_mismatch")
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(acl_information),
            ctypes.sizeof(acl_information),
            ACL_SIZE_INFORMATION_CLASS,
        ):
            _failure()
        if int(acl_information.AceCount) != len(security._expected_aces):
            _failure("analysis_security_descriptor_mismatch")
        if (
            int(acl_information.AclBytesInUse) < 8
            or int(acl_information.AclBytesInUse) > descriptor_length
        ):
            _failure("analysis_security_descriptor_mismatch")

        ace_kinds: list[str] = []
        ace_masks: list[int] = []
        trustees: list[str] = []
        observed_ace_bytes = 0
        for index, expected in enumerate(security._expected_aces):
            raw_ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(raw_ace)):
                _failure()
            if not raw_ace.value:
                _failure()
            prefix = _ACCESS_ACE_PREFIX.from_address(raw_ace.value)
            ace_type = int(prefix.Header.AceType)
            ace_flags = int(prefix.Header.AceFlags)
            ace_size = int(prefix.Header.AceSize)
            mask = int(prefix.Mask)
            sid_pointer = ctypes.c_void_p(
                raw_ace.value + ctypes.sizeof(_ACCESS_ACE_PREFIX)
            )
            if (
                ace_type != expected.ace_type
                or ace_flags != 0
                or mask != expected.mask
                or mask & GENERIC_ACCESS_BITS
                or ace_size < ctypes.sizeof(_ACCESS_ACE_PREFIX) + 8
                or not advapi32.IsValidSid(sid_pointer)
            ):
                _failure("analysis_security_descriptor_mismatch")
            sid_length = int(advapi32.GetLengthSid(sid_pointer))
            if (
                sid_length < 8
                or ace_size != ctypes.sizeof(_ACCESS_ACE_PREFIX) + sid_length
                or not advapi32.EqualSid(sid_pointer, expected_sids[expected.trustee])
            ):
                _failure("analysis_security_descriptor_mismatch")
            ace_kinds.append("deny" if ace_type == ACCESS_DENIED_ACE_TYPE else "allow")
            ace_masks.append(mask)
            trustees.append(expected.trustee)
            observed_ace_bytes += ace_size
        if int(acl_information.AclBytesInUse) != 8 + observed_ace_bytes:
            _failure("analysis_security_descriptor_mismatch")
        return ExactSecurityProof(
            policy=security._policy,
            protected=True,
            ace_kinds=tuple(ace_kinds),
            ace_masks=tuple(ace_masks),
            trustees=tuple(trustees),
        )
    finally:
        local_free_failed = False
        for allocated in expected_sids.values():
            if allocated.value and _kernel32().LocalFree(allocated):
                local_free_failed = True
        if returned_descriptor.value and _kernel32().LocalFree(returned_descriptor):
            local_free_failed = True
        if local_free_failed:
            raise Win32QuarantineRequired(
                "queried_security_cleanup_unproved",
                handle=handle,
                resources=tuple(expected_sids.values()) + (returned_descriptor,),
            )


@dataclass
class ByteLock:
    handle: OwnedHandle = field(repr=False)
    _overlapped: _OVERLAPPED = field(repr=False)
    _released: bool = field(default=False, repr=False)

    @property
    def released(self) -> bool:
        return self._released


def handle_is_inheritable(handle: OwnedHandle) -> bool:
    flags = wintypes.DWORD()
    if not _kernel32().GetHandleInformation(handle.borrow(), ctypes.byref(flags)):
        _failure()
    return bool(flags.value & HANDLE_FLAG_INHERIT)


def make_handle_noninheritable(handle: OwnedHandle) -> None:
    if not _kernel32().SetHandleInformation(
        handle.borrow(), HANDLE_FLAG_INHERIT, 0
    ) or handle_is_inheritable(handle):
        _failure()


def query_handle_identity(handle: OwnedHandle) -> HandleIdentity:
    kernel32 = _kernel32()
    ctypes.set_last_error(0)
    if kernel32.GetFileType(handle.borrow()) != FILE_TYPE_DISK:
        _failure()
    file_id = _FILE_ID_INFO()
    standard = _FILE_STANDARD_INFO()
    attributes = _FILE_ATTRIBUTE_TAG_INFO()
    for information_class, value in (
        (FILE_ID_INFO_CLASS, file_id),
        (FILE_STANDARD_INFO_CLASS, standard),
        (FILE_ATTRIBUTE_TAG_INFO_CLASS, attributes),
    ):
        if not kernel32.GetFileInformationByHandleEx(
            handle.borrow(),
            information_class,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            _failure()
    return HandleIdentity(
        volume_serial_number=int(file_id.VolumeSerialNumber),
        file_id=bytes(file_id.FileId.Identifier),
        size=int(standard.EndOfFile),
        is_directory=bool(standard.Directory),
        is_reparse=bool(attributes.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT),
        delete_pending=bool(standard.DeletePending),
        link_count=int(standard.NumberOfLinks),
    )


def query_handle_name(handle: OwnedHandle) -> str:
    """Read the volume-relative name from the already-held handle."""

    offset = _FILE_NAME_INFO.FileName.offset
    buffer = ctypes.create_string_buffer(offset + _MAX_HELD_NAME_BYTES)
    ctypes.set_last_error(0)
    if not _kernel32().GetFileInformationByHandleEx(
        handle.borrow(),
        FILE_NAME_INFO_CLASS,
        buffer,
        len(buffer),
    ):
        _failure()
    length = wintypes.DWORD.from_buffer_copy(buffer).value
    if (
        length == 0
        or length > _MAX_HELD_NAME_BYTES
        or length % ctypes.sizeof(wintypes.WCHAR) != 0
        or offset + length > len(buffer)
    ):
        _failure()
    raw = bytes(buffer[offset : offset + length])
    try:
        value = raw.decode("utf-16-le", errors="strict")
    except UnicodeDecodeError:
        _failure()
    if not value.startswith("\\") or "\0" in value:
        _failure()
    return value


def enumerate_held_directory(
    handle: OwnedHandle,
    *,
    maximum_entries: int,
) -> tuple[DirectoryEntry, ...]:
    """Enumerate one held directory without reopening any path."""

    if (
        type(maximum_entries) is not int
        or maximum_entries < 0
        or maximum_entries > _MAX_DIRECTORY_ENTRIES
    ):
        _failure("analysis_argument_invalid")
    before = query_handle_identity(handle)
    if not before.is_directory or before.is_reparse:
        _failure()
    entries: list[DirectoryEntry] = []
    names: set[str] = set()
    restart = True
    query_count = 0
    while True:
        query_count += 1
        if query_count > maximum_entries + 2:
            _failure("analysis_directory_enumeration_unbounded")
        buffer = ctypes.create_string_buffer(_IO_CHUNK_BYTES)
        ctypes.set_last_error(0)
        information_class = (
            FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS
            if restart
            else FILE_ID_EXTD_DIRECTORY_INFO_CLASS
        )
        restart = False
        if not _kernel32().GetFileInformationByHandleEx(
            handle.borrow(),
            information_class,
            buffer,
            len(buffer),
        ):
            if ctypes.get_last_error() == ERROR_NO_MORE_FILES:
                break
            _failure()
        cursor = 0
        while True:
            header_end = cursor + _FILE_ID_EXTD_DIR_INFO.FileName.offset
            if header_end > len(buffer):
                _failure()
            information = _FILE_ID_EXTD_DIR_INFO.from_buffer_copy(buffer, cursor)
            name_length = int(information.FileNameLength)
            name_end = header_end + name_length
            if (
                name_length == 0
                or name_length > _MAX_HELD_NAME_BYTES
                or name_length % ctypes.sizeof(wintypes.WCHAR) != 0
                or name_end > len(buffer)
            ):
                _failure()
            try:
                name = bytes(buffer[header_end:name_end]).decode(
                    "utf-16-le", errors="strict"
                )
            except UnicodeDecodeError:
                _failure()
            if "\0" in name or "\\" in name or "/" in name:
                _failure()
            if name not in {".", ".."}:
                if len(entries) >= maximum_entries:
                    _failure("analysis_directory_entry_limit")
                folded = name.casefold()
                if folded in names:
                    _failure()
                names.add(folded)
                entries.append(
                    DirectoryEntry(
                        name=name,
                        file_id=bytes(information.FileId.Identifier),
                        size=int(information.EndOfFile),
                        is_directory=bool(
                            information.FileAttributes & FILE_ATTRIBUTE_DIRECTORY
                        ),
                        is_reparse=bool(
                            information.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT
                        ),
                    )
                )
            next_offset = int(information.NextEntryOffset)
            if next_offset == 0:
                break
            if (
                next_offset < _FILE_ID_EXTD_DIR_INFO.FileName.offset
                or next_offset % 8 != 0
                or cursor + next_offset <= cursor
                or cursor + next_offset >= len(buffer)
            ):
                _failure()
            cursor += next_offset
    after = query_handle_identity(handle)
    if not before.same_object(after) or after.is_reparse:
        _failure()
    return tuple(sorted(entries, key=lambda item: (item.name.casefold(), item.name)))


def prove_held_directory_empty(handle: OwnedHandle) -> HandleIdentity:
    identity = query_handle_identity(handle)
    if enumerate_held_directory(handle, maximum_entries=0):
        _failure("analysis_directory_not_empty")
    observed = query_handle_identity(handle)
    if not identity.same_object(observed):
        _failure()
    return observed


def prove_held_membership(
    handle: OwnedHandle,
    parent: OwnedHandle,
    basename: str,
) -> HandleIdentity:
    """Prove exact held parent/name membership without a path reopen."""

    basename, _encoded = _validated_basename(basename)
    source = query_handle_identity(handle)
    destination = query_handle_identity(parent)
    if (
        not _valid_private_leaf_link_state(source)
        or not destination.is_directory
        or destination.is_reparse
        or source.volume_serial_number != destination.volume_serial_number
    ):
        _failure()
    parent_name = query_handle_name(parent)
    source_name = query_handle_name(handle)
    expected = parent_name.rstrip("\\") + "\\" + basename
    if source_name != expected:
        _failure("analysis_membership_unproved")
    return source


def prove_held_directory_membership(
    handle: OwnedHandle,
    parent: OwnedHandle,
    basename: str,
) -> HandleIdentity:
    """Prove exact held directory parent/name membership without reopening it."""

    return _prove_held_child(
        handle,
        parent,
        basename,
        expect_directory=True,
    )


def _prove_held_child(
    handle: OwnedHandle,
    parent: OwnedHandle,
    basename: str,
    *,
    expect_directory: bool,
) -> HandleIdentity:
    basename, _encoded = _validated_basename(basename)
    source = query_handle_identity(handle)
    destination = query_handle_identity(parent)
    valid_source = (
        source.is_directory
        and not source.is_reparse
        and not source.delete_pending
        and source.link_count == 1
        if expect_directory
        else _valid_private_leaf_link_state(source)
    )
    if (
        not valid_source
        or source.is_directory != expect_directory
        or not destination.is_directory
        or destination.is_reparse
        or source.volume_serial_number != destination.volume_serial_number
    ):
        _failure()
    expected = query_handle_name(parent).rstrip("\\") + "\\" + basename
    if query_handle_name(handle) != expected:
        _failure("analysis_membership_unproved")
    return source


@dataclass(frozen=True)
class RelativeOpenResult:
    handle: OwnedHandle = field(repr=False)
    created: bool


@dataclass(frozen=True)
class RelativeCleanupResult:
    """Sanitized result of a held-handle delete/close/absence proof."""

    outcome: str

    def __post_init__(self) -> None:
        if self.outcome not in {"original_absent", "foreign_replaced"}:
            _failure("analysis_cleanup_unproved")

    @property
    def foreign_replaced(self) -> bool:
        return self.outcome == "foreign_replaced"


def _nt_create_relative(
    parent: OwnedHandle,
    basename: str,
    *,
    desired_access: int,
    share_access: int,
    disposition: int,
    create_options: int,
    file_attributes: int,
    security: ExplicitSecurityDescriptor | None,
    kind: str,
    missing_is_absent: bool = False,
) -> tuple[OwnedHandle, int, HandleIdentity] | None:
    if (
        not isinstance(parent, OwnedHandle)
        or type(desired_access) is not int
        or type(share_access) is not int
        or disposition not in {NATIVE_FILE_OPEN, NATIVE_FILE_CREATE, NATIVE_FILE_OPEN_IF}
        or type(create_options) is not int
        or type(file_attributes) is not int
        or type(kind) is not str
        or not kind
        or not isinstance(missing_is_absent, bool)
        or (disposition != NATIVE_FILE_OPEN and security is None)
        or (security is not None and not isinstance(security, ExplicitSecurityDescriptor))
    ):
        _failure("analysis_argument_invalid")
    basename, encoded = _validated_basename(basename)
    if len(encoded) > 65_532:
        _failure("analysis_argument_invalid")
    parent_before = query_handle_identity(parent)
    if not parent_before.is_directory or parent_before.is_reparse:
        _failure()
    name_buffer = ctypes.create_unicode_buffer(basename)
    name = _UNICODE_STRING(
        len(encoded),
        len(encoded) + ctypes.sizeof(wintypes.WCHAR),
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    descriptor = security.borrow_descriptor() if security is not None else None
    attributes = _OBJECT_ATTRIBUTES(
        ctypes.sizeof(_OBJECT_ATTRIBUTES),
        parent.borrow(),
        ctypes.pointer(name),
        OBJ_CASE_INSENSITIVE,
        descriptor,
        None,
    )
    raw_handle = wintypes.HANDLE()
    io_status = _IO_STATUS_BLOCK()
    status = _ntdll().NtCreateFile(
        ctypes.byref(raw_handle),
        desired_access,
        ctypes.byref(attributes),
        ctypes.byref(io_status),
        None,
        file_attributes,
        share_access,
        disposition,
        create_options,
        None,
        0,
    )
    normalized_status = ctypes.c_ulong(status).value
    value = _raw_handle(raw_handle)
    if normalized_status != STATUS_SUCCESS:
        unexpected = (
            OwnedHandle(value, kind=f"{kind}-failed-native")
            if value not in {0, INVALID_HANDLE_VALUE}
            else None
        )
        retained = (
            parent,
            name_buffer,
            name,
            attributes,
            io_status,
            security,
        )
        if normalized_status == 0x00000103 or unexpected is not None:
            raise Win32QuarantineRequired(
                "native_completion_unproved",
                handle=unexpected,
                resources=retained,
            )
        if missing_is_absent and normalized_status == STATUS_OBJECT_NAME_NOT_FOUND:
            return None
        if normalized_status == STATUS_OBJECT_NAME_COLLISION or (
            disposition == NATIVE_FILE_CREATE
            and normalized_status == STATUS_DELETE_PENDING
        ):
            _failure("analysis_destination_exists")
        _failure()
    if value in {0, INVALID_HANDLE_VALUE}:
        raise Win32QuarantineRequired(
            "native_success_handle_unproved",
            handle=None,
            resources=(parent, name_buffer, name, attributes, io_status, security),
        )
    handle = OwnedHandle(value, kind=kind)
    information = int(io_status.Information)
    expected_information = {
        NATIVE_FILE_CREATE: {FILE_CREATED},
        NATIVE_FILE_OPEN: {FILE_OPENED},
        NATIVE_FILE_OPEN_IF: {FILE_OPENED, FILE_CREATED},
    }[disposition]
    if (
        ctypes.c_ulong(io_status.Status).value != STATUS_SUCCESS
        or information not in expected_information
    ):
        raise Win32QuarantineRequired(
            "native_information_unproved",
            handle=handle,
            resources=(parent, name_buffer, name, attributes, io_status, security),
        )
    return handle, information, parent_before


def _set_delete_disposition_unchecked(handle: OwnedHandle, *, delete: bool) -> None:
    information = _FILE_DISPOSITION_INFO(ctypes.c_ubyte(1 if delete else 0))
    if not _kernel32().SetFileInformationByHandle(
        handle.borrow(),
        FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        _failure()


def _classify_original_absence(
    parent: OwnedHandle,
    basename: str,
    original: HandleIdentity,
) -> RelativeCleanupResult:
    basename, _encoded = _validated_basename(basename)
    entries = enumerate_held_directory(
        parent,
        maximum_entries=_MAX_DIRECTORY_ENTRIES,
    )
    foreign_replaced = False
    for entry in entries:
        if entry.file_id == original.file_id:
            _failure("analysis_cleanup_unproved")
        if entry.name.casefold() == basename.casefold():
            # A foreign replacement is allowed only after the original ID is
            # absent; it is never opened, read, changed, or claimed.
            foreign_replaced = True
    return RelativeCleanupResult(
        "foreign_replaced" if foreign_replaced else "original_absent"
    )


def _cleanup_created_relative(
    handle: OwnedHandle,
    parent: OwnedHandle,
    basename: str,
    *,
    original: HandleIdentity,
) -> RelativeCleanupResult:
    try:
        parent_before = query_handle_identity(parent)
        if (
            not parent_before.is_directory
            or parent_before.is_reparse
            or parent_before.volume_serial_number
            != original.volume_serial_number
        ):
            _failure("analysis_cleanup_unproved")
        if original.is_directory:
            empty = prove_held_directory_empty(handle)
            if not original.same_object(empty):
                _failure("analysis_cleanup_unproved")
        if not original.delete_pending:
            _set_delete_disposition_unchecked(handle, delete=True)
        observed = query_handle_identity(handle)
        if (
            not original.same_object(observed)
            or not observed.delete_pending
            or (
                observed.link_count != 0
                and not (observed.is_directory and observed.link_count == 1)
            )
        ):
            _failure("analysis_cleanup_unproved")
        # Windows keeps a delete-pending link named until its last handle is
        # closed.  Checked last-close must therefore precede the held-parent
        # original-ID absence proof for both files and empty directories.
        handle.close()
        result = _classify_original_absence(parent, basename, observed)
        parent_after = query_handle_identity(parent)
        if not parent_before.same_object(parent_after):
            _failure("analysis_cleanup_unproved")
        return result
    except Win32BoundaryError as cleanup_failure:
        raise Win32QuarantineRequired(
            "created_object_cleanup_unproved",
            handle=handle,
            resources=(parent, original),
        ) from cleanup_failure


def _close_opened_after_failure(handle: OwnedHandle) -> None:
    try:
        handle.close()
    except Win32BoundaryError as close_failure:
        raise Win32QuarantineRequired(
            "opened_handle_close_unproved",
            handle=handle,
        ) from close_failure


def create_relative_directory(
    parent: OwnedHandle,
    basename: str,
    security: ExplicitSecurityDescriptor,
    *,
    kind: str = "attempt-root",
) -> OwnedHandle:
    """Atomically create one fresh directory under a held parent handle."""

    if (
        not isinstance(security, ExplicitSecurityDescriptor)
        or security._policy != "root"
    ):
        _failure("analysis_argument_invalid")
    handle, information, parent_before = _nt_create_relative(
        parent,
        basename,
        desired_access=R0.access,
        share_access=0,
        disposition=NATIVE_FILE_CREATE,
        create_options=FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT,
        file_attributes=FILE_ATTRIBUTE_NORMAL,
        security=security,
        kind=kind,
    )
    original: HandleIdentity | None = None
    try:
        make_handle_noninheritable(handle)
        candidate = query_handle_identity(handle)
        parent_candidate = query_handle_identity(parent)
        if (
            not candidate.is_directory
            or candidate.is_reparse
            or candidate.delete_pending
            or candidate.link_count != 1
            or not parent_candidate.is_directory
            or parent_candidate.is_reparse
            or candidate.volume_serial_number
            != parent_candidate.volume_serial_number
        ):
            _failure()
        original = candidate
        membership = _prove_held_child(
            handle,
            parent,
            basename,
            expect_directory=True,
        )
        if not original.same_object(membership):
            _failure()
        proof = prove_exact_handle_security(handle, security)
        if proof.policy != "root":
            _failure()
        parent_after = query_handle_identity(parent)
        if not parent_before.same_object(parent_after):
            _failure()
        return handle
    except BaseException as failure:
        if not handle.closed:
            if original is None:
                raise Win32QuarantineRequired(
                    "created_directory_identity_unproved",
                    handle=handle,
                    resources=(parent, security),
                ) from failure
            _cleanup_created_relative(
                handle,
                parent,
                basename,
                original=original,
            )
        raise


def open_or_create_status_directory(
    parent: OwnedHandle,
    basename: str,
    security: ExplicitSecurityDescriptor,
    *,
    kind: str = "analysis-status-directory",
) -> RelativeOpenResult:
    """Open or create one canonical status directory directly as S0."""

    if (
        not isinstance(parent, OwnedHandle)
        or not isinstance(security, ExplicitSecurityDescriptor)
        or security._policy != "root"
        or type(kind) is not str
        or not kind
    ):
        _failure("analysis_argument_invalid")
    handle, information, parent_before = _nt_create_relative(
        parent,
        basename,
        desired_access=S0.access,
        share_access=S0.share,
        disposition=S0.disposition,
        create_options=(
            FILE_DIRECTORY_FILE
            | FILE_SYNCHRONOUS_IO_NONALERT
            | NATIVE_FILE_OPEN_FOR_BACKUP_INTENT
            | NATIVE_FILE_OPEN_REPARSE_POINT
        ),
        file_attributes=FILE_ATTRIBUTE_NORMAL,
        security=security,
        kind=kind,
    )
    created = information == FILE_CREATED
    original: HandleIdentity | None = None
    try:
        make_handle_noninheritable(handle)
        candidate = query_handle_identity(handle)
        parent_candidate = query_handle_identity(parent)
        if (
            not candidate.is_directory
            or candidate.is_reparse
            or candidate.delete_pending
            or candidate.link_count != 1
            or not parent_candidate.is_directory
            or parent_candidate.is_reparse
            or candidate.volume_serial_number
            != parent_candidate.volume_serial_number
        ):
            _failure()
        original = candidate
        membership = _prove_held_child(
            handle,
            parent,
            basename,
            expect_directory=True,
        )
        proof = prove_exact_handle_security(handle, security)
        parent_after = query_handle_identity(parent)
        if (
            original != membership
            or proof.policy != "root"
            or not parent_before.same_object(parent_after)
        ):
            _failure()
        return RelativeOpenResult(handle=handle, created=created)
    except BaseException as failure:
        if created and not handle.closed:
            raise Win32QuarantineRequired(
                "status_directory_create_unproved",
                handle=handle,
                resources=(parent, security, original, failure),
            ) from failure
        if not handle.closed:
            _close_opened_after_failure(handle)
        raise


def create_relative_file(
    parent: OwnedHandle,
    basename: str,
    alias: FileOpenAlias,
    security: ExplicitSecurityDescriptor,
    *,
    kind: str = "private-leaf",
) -> OwnedHandle:
    """Create one held-parent-relative private file with immediate DF=true."""

    if alias is TH:
        policy = "report-temp"
    elif alias is OP:
        policy = "owner-output"
    elif alias is SP:
        policy = "owner-status"
    else:
        _failure("analysis_argument_invalid")
    if (
        not isinstance(security, ExplicitSecurityDescriptor)
        or security._policy != policy
    ):
        _failure("analysis_argument_invalid")
    handle, information, parent_before = _nt_create_relative(
        parent,
        basename,
        desired_access=alias.access,
        share_access=alias.share,
        disposition=NATIVE_FILE_CREATE,
        create_options=(
            FILE_NON_DIRECTORY_FILE
            | FILE_SYNCHRONOUS_IO_NONALERT
            | NATIVE_FILE_OPEN_REPARSE_POINT
            | (
                NATIVE_FILE_DELETE_ON_CLOSE
                if alias is OP or alias is SP
                else 0
            )
        ),
        file_attributes=(
            FILE_ATTRIBUTE_TEMPORARY
            if alias is OP or alias is SP
            else FILE_ATTRIBUTE_NORMAL
        ),
        security=security,
        kind=kind,
    )
    original: HandleIdentity | None = None
    delete_marked = alias is OP or alias is SP
    try:
        make_handle_noninheritable(handle)
        if not delete_marked:
            _set_delete_disposition_unchecked(handle, delete=True)
            delete_marked = True
        candidate = query_handle_identity(handle)
        parent_candidate = query_handle_identity(parent)
        expected_delete_state = (
            (not candidate.delete_pending and candidate.link_count == 1)
            if alias is OP or alias is SP
            else (candidate.delete_pending and candidate.link_count == 0)
        )
        if (
            candidate.is_directory
            or candidate.is_reparse
            or not expected_delete_state
            or not parent_candidate.is_directory
            or parent_candidate.is_reparse
            or candidate.volume_serial_number
            != parent_candidate.volume_serial_number
        ):
            _failure()
        original = candidate
        membership = _prove_held_child(
            handle,
            parent,
            basename,
            expect_directory=False,
        )
        if not original.same_object(membership):
            _failure()
        if alias is TH:
            prove_exact_handle_security(handle, security)
        parent_after = query_handle_identity(parent)
        if not parent_before.same_object(parent_after):
            _failure()
        return handle
    except BaseException as failure:
        if not handle.closed:
            if original is None:
                raise Win32QuarantineRequired(
                    "created_file_identity_unproved",
                    handle=handle,
                    resources=(parent, security),
                ) from failure
            _cleanup_created_relative(
                handle,
                parent,
                basename,
                original=original,
            )
        raise


def rollback_relative_handle(
    handle: OwnedHandle,
    parent: OwnedHandle,
    basename: str,
    *,
    original: HandleIdentity,
) -> RelativeCleanupResult:
    """Delete only the expected held object and classify its final name."""

    if (
        not isinstance(handle, OwnedHandle)
        or not isinstance(parent, OwnedHandle)
        or not isinstance(original, HandleIdentity)
    ):
        _failure("analysis_argument_invalid")
    try:
        current = _prove_held_child(
            handle,
            parent,
            basename,
            expect_directory=original.is_directory,
        )
        if not original.same_object(current):
            _failure("analysis_cleanup_unproved")
    except Win32BoundaryError as proof_failure:
        raise Win32QuarantineRequired(
            "rollback_identity_unproved",
            handle=handle,
            resources=(parent, original),
        ) from proof_failure
    return _cleanup_created_relative(
        handle,
        parent,
        basename,
        original=current,
    )


def remove_relative_directory(
    handle: OwnedHandle,
    parent: OwnedHandle,
    basename: str,
) -> None:
    """Remove one proved empty held directory and prove original-ID absence."""

    original = _prove_held_child(
        handle,
        parent,
        basename,
        expect_directory=True,
    )
    rollback_relative_handle(
        handle,
        parent,
        basename,
        original=original,
    )


def remove_relative_file(
    handle: OwnedHandle,
    parent: OwnedHandle,
    basename: str,
) -> None:
    """Delete one proved held regular leaf and prove original-ID absence."""

    original = _prove_held_child(
        handle,
        parent,
        basename,
        expect_directory=False,
    )
    rollback_relative_handle(
        handle,
        parent,
        basename,
        original=original,
    )


def open_relative_directory(
    parent: OwnedHandle,
    basename: str,
    alias: FileOpenAlias,
    *,
    kind: str = "relative-directory",
) -> OwnedHandle:
    """Open one held-parent-relative R0/DP directory without path fallback."""

    if alias is not R0 and alias is not DP:
        _failure("analysis_argument_invalid")
    opened = _nt_create_relative(
        parent,
        basename,
        desired_access=alias.access,
        share_access=alias.share,
        disposition=NATIVE_FILE_OPEN,
        create_options=(
            FILE_SYNCHRONOUS_IO_NONALERT
            | NATIVE_FILE_OPEN_FOR_BACKUP_INTENT
            | NATIVE_FILE_OPEN_REPARSE_POINT
        ),
        file_attributes=FILE_ATTRIBUTE_NORMAL,
        security=None,
        kind=kind,
    )
    if opened is None:
        _failure()
    handle, information, parent_before = opened
    try:
        if information != FILE_OPENED:
            _failure()
        make_handle_noninheritable(handle)
        identity = _prove_held_child(
            handle,
            parent,
            basename,
            expect_directory=True,
        )
        if (
            identity.delete_pending
            or identity.link_count != 1
            or not parent_before.same_object(query_handle_identity(parent))
        ):
            _failure()
        return handle
    except BaseException:
        if not handle.closed:
            _close_opened_after_failure(handle)
        raise


def open_relative_file_if_present(
    parent: OwnedHandle,
    basename: str,
    *,
    maximum: int,
    kind: str = "recovery-leaf",
) -> OwnedHandle | None:
    """Return RH only for a proved present capped file; exact missing is None."""

    if (
        not isinstance(parent, OwnedHandle)
        or type(maximum) is not int
        or maximum < 0
    ):
        _failure("analysis_argument_invalid")
    parent_before = query_handle_identity(parent)
    if not parent_before.is_directory or parent_before.is_reparse:
        _failure()
    opened = _nt_create_relative(
        parent,
        basename,
        desired_access=RH.access,
        share_access=RH.share,
        disposition=NATIVE_FILE_OPEN,
        create_options=(
            FILE_NON_DIRECTORY_FILE
            | FILE_SYNCHRONOUS_IO_NONALERT
            | NATIVE_FILE_OPEN_REPARSE_POINT
        ),
        file_attributes=FILE_ATTRIBUTE_NORMAL,
        security=None,
        kind=kind,
        missing_is_absent=True,
    )
    if opened is None:
        if not parent_before.same_object(query_handle_identity(parent)):
            _failure()
        return None
    handle, information, native_parent_before = opened
    try:
        if information != FILE_OPENED:
            _failure()
        make_handle_noninheritable(handle)
        identity = _prove_held_child(
            handle,
            parent,
            basename,
            expect_directory=False,
        )
        if identity.size > maximum:
            _failure("analysis_file_too_large")
        observed = query_handle_identity(handle)
        if (
            not identity.same_object(observed)
            or identity.size != observed.size
            or not native_parent_before.same_object(parent_before)
            or not parent_before.same_object(query_handle_identity(parent))
        ):
            _failure()
        return handle
    except BaseException:
        if not handle.closed:
            _close_opened_after_failure(handle)
        raise


def open_relative_file(
    parent: OwnedHandle,
    basename: str,
    alias: FileOpenAlias = RH,
    *,
    owner_handle: OwnedHandle | None = None,
    kind: str = "recovery-leaf",
) -> OwnedHandle:
    """Open an existing regular leaf relative to a held parent, no-follow."""

    if (
        (alias is not RH and alias is not OC and alias is not SC)
        or (alias is OC and not isinstance(owner_handle, OwnedHandle))
        or (alias is not OC and owner_handle is not None)
    ):
        _failure("analysis_argument_invalid")
    owner_before = None
    if alias is OC:
        owner_before = _prove_held_child(
            owner_handle,
            parent,
            basename,
            expect_directory=False,
        )
    handle, information, parent_before = _nt_create_relative(
        parent,
        basename,
        desired_access=alias.access,
        share_access=alias.share,
        disposition=NATIVE_FILE_OPEN,
        create_options=(
            FILE_NON_DIRECTORY_FILE
            | FILE_SYNCHRONOUS_IO_NONALERT
            | NATIVE_FILE_OPEN_REPARSE_POINT
        ),
        file_attributes=FILE_ATTRIBUTE_NORMAL,
        security=None,
        kind=kind,
    )
    try:
        if information != FILE_OPENED:
            _failure()
        make_handle_noninheritable(handle)
        if alias is OC:
            owner_after = _prove_held_child(
                owner_handle,
                parent,
                basename,
                expect_directory=False,
            )
            if not owner_before.same_object(owner_after):
                _failure()
        else:
            _prove_held_child(
                handle,
                parent,
                basename,
                expect_directory=False,
            )
        if not parent_before.same_object(query_handle_identity(parent)):
            _failure()
        return handle
    except BaseException:
        if not handle.closed:
            _close_opened_after_failure(handle)
        raise


def write_handle_flush(
    handle: OwnedHandle,
    data: bytes,
    *,
    maximum: int,
) -> None:
    """Bounded write/flush for an exact write-only private-leaf handle.

    ``OC`` deliberately has neither read-data nor read-attributes access.  The
    owner-side ``OP`` handle performs every identity, membership, and byte
    proof after this narrow target-side write completes.
    """

    if (
        not isinstance(handle, OwnedHandle)
        or type(data) is not bytes
        or type(maximum) is not int
        or maximum < 0
    ):
        _failure("analysis_argument_invalid")
    if len(data) > maximum:
        _failure("analysis_file_too_large")
    _seek_zero(handle)
    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + _IO_CHUNK_BYTES]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not _kernel32().WriteFile(
            handle.borrow(), buffer, len(chunk), ctypes.byref(written), None
        ) or written.value != len(chunk):
            _failure()
        offset += len(chunk)
    if not _kernel32().SetEndOfFile(handle.borrow()) or not _kernel32().FlushFileBuffers(
        handle.borrow()
    ):
        _failure()


def open_or_create_relative_lock(
    parent: OwnedHandle,
    basename: str,
    security: ExplicitSecurityDescriptor,
    *,
    kind: str = "analysis-lease",
) -> RelativeOpenResult:
    """Open or create the held-parent-relative lease and close its race."""

    if (
        not isinstance(security, ExplicitSecurityDescriptor)
        or security._policy != "lease"
    ):
        _failure("analysis_argument_invalid")
    handle, information, parent_before = _nt_create_relative(
        parent,
        basename,
        # NtCreateFile requires an explicit SYNCHRONIZE bit when a synchronous
        # CreateOption is requested; the Win32 GENERIC mapping is not enough
        # for that parameter check.
        desired_access=LOCK_FILE.access | DELETE | SYNCHRONIZE,
        share_access=LOCK_FILE.share,
        disposition=NATIVE_FILE_OPEN_IF,
        create_options=(
            FILE_NON_DIRECTORY_FILE
            | FILE_SYNCHRONOUS_IO_NONALERT
            | NATIVE_FILE_OPEN_REPARSE_POINT
        ),
        file_attributes=FILE_ATTRIBUTE_NORMAL,
        security=security,
        kind=kind,
    )
    created = information == FILE_CREATED
    original: HandleIdentity | None = None
    try:
        make_handle_noninheritable(handle)
        membership = _prove_held_child(
            handle,
            parent,
            basename,
            expect_directory=False,
        )
        original = membership
        if created:
            prove_exact_handle_security(handle, security)
        if not parent_before.same_object(query_handle_identity(parent)):
            _failure()
        return RelativeOpenResult(handle=handle, created=created)
    except BaseException as failure:
        if not handle.closed:
            if created:
                if original is None:
                    raise Win32QuarantineRequired(
                        "created_lock_identity_unproved",
                        handle=handle,
                        resources=(parent, security),
                    ) from failure
                _cleanup_created_relative(
                    handle,
                    parent,
                    basename,
                    original=original,
                )
            else:
                _close_opened_after_failure(handle)
        raise


def open_no_follow(
    path: str | Path,
    alias: FileOpenAlias,
    *,
    expect_directory: bool,
    kind: str,
    security: ExplicitSecurityDescriptor | None = None,
    parent: OwnedHandle | None = None,
    basename: str | None = None,
    lease_parent_busy_on_sharing_violation: bool = False,
) -> OwnedHandle:
    """Open one exact path without following a final-component reparse point."""

    if not isinstance(path, (str, Path)):
        _failure("analysis_argument_invalid")
    path_text = str(path)
    if (
        not path_text
        or "\0" in path_text
        or not isinstance(alias, FileOpenAlias)
        or alias.disposition not in {CREATE_NEW, OPEN_EXISTING, OPEN_ALWAYS}
        or not isinstance(expect_directory, bool)
        or type(kind) is not str
        or not kind
        or type(lease_parent_busy_on_sharing_violation) is not bool
        or not alias.flags & FILE_FLAG_OPEN_REPARSE_POINT
        or (security is not None and not isinstance(security, ExplicitSecurityDescriptor))
        or ((parent is None) != (basename is None))
    ):
        _failure("analysis_argument_invalid")
    if lease_parent_busy_on_sharing_violation and (
        alias is not R0
        or not expect_directory
        or not Path(path_text).is_absolute()
        or security is not None
        or parent is not None
        or basename is not None
    ):
        _failure("analysis_argument_invalid")
    validated_basename: str | None = None
    if basename is not None:
        validated_basename, _encoded = _validated_basename(basename)
    if alias.disposition in {CREATE_NEW, OPEN_ALWAYS}:
        if (
            not isinstance(security, ExplicitSecurityDescriptor)
            or not isinstance(parent, OwnedHandle)
            or validated_basename is None
            or Path(path_text).name != validated_basename
        ):
            _failure("analysis_argument_invalid")
        security.borrow_attributes()
        _failure(
            "analysis_atomic_leaf_creation_unavailable",
            "held-parent-relative atomic leaf creation is unavailable",
        )
    if security is not None:
        _failure("analysis_argument_invalid")
    parent_before = query_handle_identity(parent) if parent is not None else None
    if parent_before is not None and (
        not parent_before.is_directory or parent_before.is_reparse
    ):
        _failure()
    ctypes.set_last_error(0)
    raw = _kernel32().CreateFileW(
        path_text,
        alias.access,
        alias.share,
        None,
        alias.disposition,
        alias.flags,
        None,
    )
    value = _raw_handle(raw)
    if value in {0, INVALID_HANDLE_VALUE}:
        if (
            lease_parent_busy_on_sharing_violation
            and ctypes.get_last_error() == ERROR_SHARING_VIOLATION
        ):
            _failure("analysis_busy", "analysis outbox is busy")
        _failure()
    handle = OwnedHandle(value, kind=kind)
    try:
        make_handle_noninheritable(handle)
        identity = query_handle_identity(handle)
        if (
            identity.is_reparse
            or identity.is_directory != expect_directory
            or (not expect_directory and not _valid_private_leaf_link_state(identity))
        ):
            _failure()
        if parent is not None:
            membership = prove_held_membership(
                handle,
                parent,
                validated_basename,
            )
            parent_after = query_handle_identity(parent)
            if (
                parent_before is None
                or not parent_before.same_object(parent_after)
                or not identity.same_object(membership)
            ):
                _failure()
        return handle
    except BaseException:
        if not handle.closed:
            try:
                handle.close()
            except Win32BoundaryError as close_failure:
                raise Win32QuarantineRequired(
                    "handle_close_unproved",
                    handle=handle,
                ) from close_failure
        raise


def create_directory_explicit(
    path: str | Path,
    security: ExplicitSecurityDescriptor,
    *,
    kind: str,
) -> NoReturn:
    """Fail closed until held-parent-relative atomic directory create exists.

    ``CreateDirectoryW`` followed by a path reopen has an unprovable same-user
    swap window and is therefore not a supported freshness primitive.
    """

    if (
        not isinstance(path, (str, Path))
        or not isinstance(security, ExplicitSecurityDescriptor)
        or type(kind) is not str
        or not kind
    ):
        _failure("analysis_argument_invalid")
    security.borrow_attributes()
    _failure(
        "analysis_atomic_directory_creation_unavailable",
        "held-parent-relative atomic directory creation is unavailable",
    )


def lock_byte_zero(handle: OwnedHandle) -> ByteLock:
    before = query_handle_identity(handle)
    if before.is_directory or before.is_reparse or before.link_count != 1:
        _failure()
    overlap = _OVERLAPPED()
    ctypes.set_last_error(0)
    if not _kernel32().LockFileEx(
        handle.borrow(),
        LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
        0,
        1,
        0,
        ctypes.byref(overlap),
    ):
        error = ctypes.get_last_error()
        if error == ERROR_LOCK_VIOLATION:
            _failure("analysis_busy", "analysis outbox is busy")
        _failure()
    lock = ByteLock(handle=handle, _overlapped=overlap)
    try:
        after = query_handle_identity(handle)
        if (
            not before.same_object(after)
            or after.is_reparse
            or after.link_count != 1
        ):
            _failure()
    except BaseException:
        try:
            unlock_byte_zero(lock)
        except Win32BoundaryError as unlock_failure:
            raise Win32QuarantineRequired(
                "lock_release_unproved",
                handle=handle,
                lock=lock,
            ) from unlock_failure
        raise
    return lock


def unlock_byte_zero(lock: ByteLock) -> None:
    if not isinstance(lock, ByteLock) or lock._released:
        _failure("analysis_lock_state_invalid")
    if not _kernel32().UnlockFileEx(
        lock.handle.borrow(),
        0,
        1,
        0,
        ctypes.byref(lock._overlapped),
    ):
        _failure()
    lock._released = True


def _seek_zero(handle: OwnedHandle) -> None:
    if not _kernel32().SetFilePointerEx(handle.borrow(), 0, None, 0):
        _failure()


def read_handle_capped(handle: OwnedHandle, *, maximum: int) -> bytes:
    if type(maximum) is not int or maximum < 0:
        _failure("analysis_argument_invalid")
    before = query_handle_identity(handle)
    if not _valid_private_leaf_link_state(before) or before.size > maximum:
        _failure()
    _seek_zero(handle)
    chunks: list[bytes] = []
    observed = 0
    while observed < before.size:
        requested = min(_IO_CHUNK_BYTES, before.size - observed)
        buffer = ctypes.create_string_buffer(requested)
        read = wintypes.DWORD()
        if not _kernel32().ReadFile(
            handle.borrow(), buffer, requested, ctypes.byref(read), None
        ):
            _failure()
        if read.value == 0 or read.value > requested:
            _failure()
        observed += int(read.value)
        if observed > maximum:
            _failure()
        chunks.append(buffer.raw[: read.value])
    after = query_handle_identity(handle)
    if (
        not before.same_object(after)
        or after.size != before.size
        or observed != before.size
        or not _valid_private_leaf_link_state(after)
    ):
        _failure()
    return b"".join(chunks)


def write_flush_reread(
    handle: OwnedHandle,
    data: bytes,
    *,
    maximum: int,
) -> HandleIdentity:
    if type(data) is not bytes or type(maximum) is not int or maximum < 0:
        _failure("analysis_argument_invalid")
    if len(data) > maximum:
        _failure("analysis_file_too_large")
    before = query_handle_identity(handle)
    if not _valid_private_leaf_link_state(before):
        _failure()
    _seek_zero(handle)
    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + _IO_CHUNK_BYTES]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not _kernel32().WriteFile(
            handle.borrow(), buffer, len(chunk), ctypes.byref(written), None
        ) or written.value != len(chunk):
            _failure()
        offset += len(chunk)
    if not _kernel32().SetEndOfFile(handle.borrow()) or not _kernel32().FlushFileBuffers(
        handle.borrow()
    ):
        _failure()
    after_write = query_handle_identity(handle)
    if (
        not before.same_object(after_write)
        or after_write.size != len(data)
        or not _valid_private_leaf_link_state(after_write)
    ):
        _failure()
    if read_handle_capped(handle, maximum=maximum) != data:
        _failure()
    final = query_handle_identity(handle)
    if (
        not before.same_object(final)
        or final.size != len(data)
        or not _valid_private_leaf_link_state(final)
    ):
        _failure()
    return final


def set_delete_disposition(handle: OwnedHandle, *, delete: bool) -> HandleIdentity:
    if not isinstance(delete, bool):
        _failure("analysis_argument_invalid")
    before = query_handle_identity(handle)
    if (
        not _valid_private_leaf_link_state(before)
        or before.delete_pending == delete
    ):
        _failure()
    information = _FILE_DISPOSITION_INFO(ctypes.c_ubyte(1 if delete else 0))
    if not _kernel32().SetFileInformationByHandle(
        handle.borrow(),
        FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        _failure()
    observed = query_handle_identity(handle)
    if (
        not before.same_object(observed)
        or observed.delete_pending != delete
        or not _valid_private_leaf_link_state(observed)
    ):
        _failure()
    return observed


def rename_handle_no_replace(
    handle: OwnedHandle,
    destination_parent: OwnedHandle,
    basename: str,
) -> HandleIdentity:
    """Rename the held source through the held destination parent, no replace."""

    basename, encoded = _validated_basename(basename)
    source_before = query_handle_identity(handle)
    parent_before = query_handle_identity(destination_parent)
    if (
        source_before.is_directory
        or source_before.is_reparse
        or source_before.delete_pending
        or source_before.link_count != 1
        or not parent_before.is_directory
        or parent_before.is_reparse
        or source_before.volume_serial_number != parent_before.volume_serial_number
    ):
        _failure()

    offset = _FILE_RENAME_INFORMATION.FileName.offset
    size = offset + len(encoded)
    buffer = ctypes.create_string_buffer(size)
    information = ctypes.cast(
        buffer, ctypes.POINTER(_FILE_RENAME_INFORMATION)
    ).contents
    information.ReplaceIfExists = 0
    information.RootDirectory = destination_parent.borrow()
    information.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
    io_status = _IO_STATUS_BLOCK()
    status = _ntdll().NtSetInformationFile(
        handle.borrow(),
        ctypes.byref(io_status),
        buffer,
        size,
        NATIVE_FILE_RENAME_INFORMATION_CLASS,
    )
    normalized_status = ctypes.c_ulong(status).value
    if normalized_status != STATUS_SUCCESS:
        if normalized_status == STATUS_OBJECT_NAME_COLLISION:
            _failure("analysis_destination_exists")
        _failure()
    if ctypes.c_ulong(io_status.Status).value != STATUS_SUCCESS:
        _failure()
    source_after = query_handle_identity(handle)
    parent_after = query_handle_identity(destination_parent)
    if not source_before.same_object(source_after) or not parent_before.same_object(
        parent_after
    ) or source_after.link_count != 1:
        _failure()
    membership = prove_held_membership(handle, destination_parent, basename)
    if not source_after.same_object(membership):
        _failure()
    return membership


def _snapshot_closed_relative_file_identity(
    parent: OwnedHandle,
    basename: str,
    *,
    kind: str,
    retained: tuple[object, ...],
) -> HandleIdentity:
    """Prove one exact relative regular leaf and close its probe handle."""

    probe = open_relative_file(parent, basename, RH, kind=kind)
    try:
        identity = prove_held_membership(probe, parent, basename)
        if (
            identity.is_directory
            or identity.is_reparse
            or identity.delete_pending
            or identity.link_count != 1
        ):
            _failure()
    except BaseException as proof_failure:
        if not probe.closed:
            try:
                probe.close()
            except Win32BoundaryError as close_failure:
                raise Win32QuarantineRequired(
                    "relative_replace_probe_cleanup_unproved",
                    handle=probe,
                    resources=(parent, basename, retained, proof_failure),
                ) from close_failure
        raise
    try:
        probe.close()
    except Win32BoundaryError as close_failure:
        raise Win32QuarantineRequired(
            "relative_replace_probe_close_unproved",
            handle=probe,
            resources=(parent, basename, retained, identity),
        ) from close_failure
    return identity


def replace_relative_file(
    handle: OwnedHandle,
    parent: OwnedHandle,
    source_basename: str,
    destination_basename: str,
) -> HandleIdentity:
    """Atomically replace one exact relative leaf with one held source TH.

    ``handle`` and ``parent`` remain caller-owned.  Success moves the same
    source object to ``destination_basename``.  A proved native rejection
    raises ``analysis_replace_not_applied``; any uncertain completion retains
    the handles in ``Win32QuarantineRequired``.
    """

    if not isinstance(handle, OwnedHandle) or not isinstance(parent, OwnedHandle):
        _failure("analysis_argument_invalid")
    source_basename, _source_encoded = _validated_basename(source_basename)
    destination_basename, destination_encoded = _validated_basename(
        destination_basename
    )
    if source_basename.casefold() == destination_basename.casefold():
        _failure("analysis_argument_invalid")

    parent_before = query_handle_identity(parent)
    source_before = _prove_held_child(
        handle,
        parent,
        source_basename,
        expect_directory=False,
    )
    if (
        not parent_before.is_directory
        or parent_before.is_reparse
        or source_before.delete_pending
        or source_before.link_count != 1
        or source_before.volume_serial_number != parent_before.volume_serial_number
    ):
        _failure()
    destination_before = _snapshot_closed_relative_file_identity(
        parent,
        destination_basename,
        kind="relative-replace-destination-probe",
        retained=(handle, source_before),
    )
    if (
        source_before.same_object(destination_before)
        or destination_before.is_directory
        or destination_before.is_reparse
        or destination_before.delete_pending
        or destination_before.link_count != 1
        or destination_before.volume_serial_number
        != parent_before.volume_serial_number
    ):
        _failure()

    source_ready = _prove_held_child(
        handle,
        parent,
        source_basename,
        expect_directory=False,
    )
    parent_ready = query_handle_identity(parent)
    if source_ready != source_before or not parent_before.same_object(parent_ready):
        _failure()

    def _prove_not_applied() -> None:
        source_observed = _prove_held_child(
            handle,
            parent,
            source_basename,
            expect_directory=False,
        )
        destination_observed = _snapshot_closed_relative_file_identity(
            parent,
            destination_basename,
            kind="relative-replace-failure-probe",
            retained=(handle, source_before, destination_before),
        )
        parent_observed = query_handle_identity(parent)
        if (
            source_observed != source_before
            or destination_observed != destination_before
            or not parent_before.same_object(parent_observed)
        ):
            _failure()

    offset = _FILE_RENAME_INFORMATION.FileName.offset
    size = offset + len(destination_encoded)
    buffer = ctypes.create_string_buffer(size)
    information = ctypes.cast(
        buffer, ctypes.POINTER(_FILE_RENAME_INFORMATION)
    ).contents
    information.ReplaceIfExists = 1
    information.RootDirectory = parent.borrow()
    information.FileNameLength = len(destination_encoded)
    ctypes.memmove(
        ctypes.addressof(buffer) + offset,
        destination_encoded,
        len(destination_encoded),
    )
    io_status = _IO_STATUS_BLOCK()
    try:
        status = _ntdll().NtSetInformationFile(
            handle.borrow(),
            ctypes.byref(io_status),
            buffer,
            size,
            NATIVE_FILE_RENAME_INFORMATION_CLASS,
        )
    except BaseException as native_failure:
        raise Win32QuarantineRequired(
            "relative_replace_native_completion_unproved",
            handle=handle,
            resources=(
                parent,
                source_before,
                destination_before,
                native_failure,
            ),
        ) from native_failure
    normalized_status = ctypes.c_ulong(status).value
    if normalized_status != STATUS_SUCCESS:
        if normalized_status == STATUS_PENDING:
            raise Win32QuarantineRequired(
                "relative_replace_native_completion_unproved",
                handle=handle,
                resources=(parent, source_before, destination_before, io_status),
            )
        try:
            _prove_not_applied()
        except BaseException as proof_failure:
            raise Win32QuarantineRequired(
                "relative_replace_failure_state_unproved",
                handle=handle,
                resources=(
                    parent,
                    source_before,
                    destination_before,
                    io_status,
                    proof_failure,
                ),
            ) from proof_failure
        _failure(
            "analysis_replace_not_applied",
            "analysis relative replacement was not applied",
        )
    if ctypes.c_ulong(io_status.Status).value != STATUS_SUCCESS:
        raise Win32QuarantineRequired(
            "relative_replace_native_completion_unproved",
            handle=handle,
            resources=(parent, source_before, destination_before, io_status),
        )
    try:
        source_after = query_handle_identity(handle)
        parent_after = query_handle_identity(parent)
        membership = prove_held_membership(
            handle,
            parent,
            destination_basename,
        )
        if (
            source_before != source_after
            or source_after != membership
            or source_after.delete_pending
            or source_after.link_count != 1
            or not parent_before.same_object(parent_after)
        ):
            _failure()
    except BaseException as proof_failure:
        raise Win32QuarantineRequired(
            "relative_replace_postcondition_unproved",
            handle=handle,
            resources=(
                parent,
                source_before,
                destination_before,
                io_status,
                proof_failure,
            ),
        ) from proof_failure
    return membership


def current_token_privileges() -> frozenset[str]:
    """Read the current process token's privilege names without mutation."""

    kernel32 = _kernel32()
    advapi32 = _advapi32()
    raw = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(raw)
    ):
        _failure()
    token = OwnedHandle(_raw_handle(raw), kind="process-token")
    try:
        required = wintypes.DWORD()
        ctypes.set_last_error(0)
        first = advapi32.GetTokenInformation(
            token.borrow(),
            TOKEN_INFORMATION_CLASS_PRIVILEGES,
            None,
            0,
            ctypes.byref(required),
        )
        if (
            first
            or ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER
            or required.value < _TOKEN_PRIVILEGES_ONE.Privileges.offset
        ):
            _failure()
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token.borrow(),
            TOKEN_INFORMATION_CLASS_PRIVILEGES,
            buffer,
            required.value,
            ctypes.byref(required),
        ):
            _failure()
        count = wintypes.DWORD.from_buffer(buffer).value
        if count > _MAX_TOKEN_PRIVILEGES:
            _failure()
        offset = _TOKEN_PRIVILEGES_ONE.Privileges.offset
        item_size = ctypes.sizeof(_LUID_AND_ATTRIBUTES)
        if offset + count * item_size > len(buffer):
            _failure()
        names: set[str] = set()
        luids: set[tuple[int, int]] = set()
        for index in range(count):
            item = _LUID_AND_ATTRIBUTES.from_buffer_copy(
                buffer, offset + index * item_size
            )
            luid = (int(item.Luid.LowPart), int(item.Luid.HighPart))
            if luid in luids:
                _failure()
            luids.add(luid)
            length = wintypes.DWORD()
            ctypes.set_last_error(0)
            first_name = advapi32.LookupPrivilegeNameW(
                None, ctypes.byref(item.Luid), None, ctypes.byref(length)
            )
            if (
                first_name
                or ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER
                or length.value == 0
                or length.value > 256
            ):
                _failure()
            name = ctypes.create_unicode_buffer(length.value + 1)
            if not advapi32.LookupPrivilegeNameW(
                None, ctypes.byref(item.Luid), name, ctypes.byref(length)
            ):
                _failure()
            value = name.value
            if not value or "\0" in value or value in names:
                _failure()
            names.add(value)
        return frozenset(names)
    finally:
        if not token.closed:
            token.close()

__all__ = (
    "CREATE_NEW",
    "DP",
    "DirectoryEntry",
    "ExactSecurityProof",
    "ExplicitSecurityDescriptor",
    "FileOpenAlias",
    "HandleIdentity",
    "LOCK_FILE",
    "OC",
    "OP",
    "OwnedHandle",
    "RH",
    "R0",
    "ROOT",
    "S0",
    "SC",
    "SP",
    "TH",
    "Win32BoundaryError",
    "Win32QuarantineRequired",
    "abi_layout",
    "current_token_privileges",
    "create_directory_explicit",
    "create_relative_directory",
    "create_relative_file",
    "enumerate_held_directory",
    "handle_is_inheritable",
    "lock_byte_zero",
    "make_handle_noninheritable",
    "open_no_follow",
    "open_or_create_relative_lock",
    "open_or_create_status_directory",
    "open_relative_directory",
    "open_relative_file",
    "open_relative_file_if_present",
    "prove_exact_handle_security",
    "prove_held_directory_empty",
    "prove_held_directory_membership",
    "prove_held_membership",
    "query_handle_identity",
    "query_handle_name",
    "read_handle_capped",
    "RelativeCleanupResult",
    "RelativeOpenResult",
    "remove_relative_directory",
    "remove_relative_file",
    "replace_relative_file",
    "rollback_relative_handle",
    "rename_handle_no_replace",
    "set_delete_disposition",
    "unlock_byte_zero",
    "write_handle_flush",
    "write_flush_reread",
)
