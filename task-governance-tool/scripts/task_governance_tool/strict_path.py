"""Strict physical-path observation without changing identity or OS permissions.

Only Windows access denial from the standard strict resolver permits the bounded
native alternative. Callers still own directory, ancestor/reparse and containment
checks. This module neither changes global path functions nor stores native data.
"""

from __future__ import annotations

import ntpath
import os
import re
from pathlib import Path


_VOLUME_ROOT = re.compile(r"\\Device\\HarddiskVolume[0-9]+\\?", re.IGNORECASE)
_RESERVED = re.compile(
    r"(?:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\.|$)",
    re.IGNORECASE,
)


def _unavailable() -> OSError:
    return OSError("physical path equivalence was not established")


def _ordinary_dos_path(value: str) -> str:
    if not isinstance(value, str) or not re.match(r"^[A-Za-z]:\\", value):
        raise _unavailable()
    if any(ord(char) < 32 or char in ':*?"<>|/' for char in value[2:]):
        raise _unavailable()
    parts = value[3:].split("\\") if len(value) > 3 else []
    for part in parts:
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or _RESERVED.match(part)):
            raise _unavailable()
    return value


def _volume_root(value: str) -> str:
    if not isinstance(value, str) or not _VOLUME_ROOT.fullmatch(value):
        raise _unavailable()
    return value.rstrip("\\")


def _rebuild_dos_path(original: str, root: str, target: str) -> str:
    """The input supplies the drive only; normalized native data supplies suffix."""
    _ordinary_dos_path(original)
    volume = _volume_root(root)
    if not isinstance(target, str):
        raise _unavailable()
    if target.casefold() == volume.casefold():
        suffix = "\\"
    elif (target[:len(volume)].casefold() == volume.casefold()
          and target[len(volume):len(volume) + 1] == "\\"):
        suffix = target[len(volume):]
    else:
        raise _unavailable()
    return _ordinary_dos_path(original[0].upper() + ":" + suffix)


class _WindowsMetadata:
    """Access-zero, bounded metadata observations; each call owns its handle."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ]
        self.kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL

    def final_nt(self, path: str) -> str:
        _ordinary_dos_path(path)
        # OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS; no read/write access or share.
        handle = self.kernel.CreateFileW(path, 0, 0, None, 3, 0x02000000, None)
        if handle == self.ctypes.c_void_p(-1).value or handle is None:
            raise _unavailable()
        try:
            buffer = self.ctypes.create_unicode_buffer(32768)
            size = self.kernel.GetFinalPathNameByHandleW(
                handle, buffer, len(buffer), 2  # FILE_NAME_NORMALIZED | VOLUME_NAME_NT
            )
            if not 0 < size < len(buffer):
                raise _unavailable()
            return buffer.value
        finally:
            if not self.kernel.CloseHandle(handle):
                raise _unavailable()


def _resolve_windows(path: Path) -> Path:
    # Callers supply absolute paths. normpath retains ambiguous trailing dots
    # and spaces for rejection, unlike Windows GetFullPathName-based abspath.
    original = _ordinary_dos_path(ntpath.normpath(str(path)))
    metadata = _WindowsMetadata()
    drive = original[0].upper() + ":\\"
    before = _volume_root(metadata.final_nt(drive))
    target = metadata.final_nt(original)
    candidate = _rebuild_dos_path(original, before, target)
    reopened = metadata.final_nt(candidate)
    after = _volume_root(metadata.final_nt(drive))
    if before.casefold() != after.casefold() or target != reopened:
        raise _unavailable()
    # Do not introduce a different spelling into canonical_path_v1. This is an
    # additional consistency check, never the source of the physical candidate.
    # It neither proves drive-letter uniqueness nor changes nonstrict resolution.
    if ntpath.normcase(str(path.resolve(strict=False))) != ntpath.normcase(candidate):
        raise _unavailable()
    return Path(candidate)


def resolve_strict_path(path: Path) -> Path:
    """Prefer standard resolution; preserve its exact failure on uncertainty."""
    try:
        return path.resolve(strict=True)
    except OSError as original:
        if os.name != "nt" or getattr(original, "winerror", None) != 5:
            raise
        try:
            return _resolve_windows(path)
        except (OSError, RuntimeError, ValueError, TypeError):
            pass
        raise
